#!/usr/bin/env python3
"""
Mission H Phase 2 — OFI-M Sanity Check
================================================

Repeats the Phase 1 21-event lead/lag analysis but with the new Maker
Order Flow Imbalance (OFI-M) signals added in Phase 2 of Mission H.

Key question: does OFI-M show displacement at T-2h (earlier than DtM's
T-1h)? That would confirm the spec's thesis that maker withdrawal
precedes visible book thinning.

Run:
    /workspace/venv/bin/python research/mission_h_sanity_phase2.py
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parent.parent
MSIG = BASE / 'data' / 'perp' / 'binance' / 'BTCUSDT' / 'microstructure_1min.parquet'

# Phase 1 reference signals + Phase 2 OFI-M signals
SIGNALS = [
    # Phase 1
    'dtm_bid_5pct_z168h',
    'ofi_t_5min',
    'vdv_5min',
    # Phase 2 (new)
    'ofi_m_1pct_5min',
    'ofi_m_total_5min',
    'ofi_m_total_30min',
    'ofi_m_total_z168h',
]

OFFSETS_MIN = {
    't-4h': -240, 't-2h': -120, 't-1h': -60,
    't': 0, 't+1h': 60, 't+6h': 360,
}


def main() -> int:
    if not MSIG.exists():
        print(f"ERROR: {MSIG} does not exist", file=sys.stderr)
        return 1

    print(f"loading {MSIG} ...")
    df = pd.read_parquet(MSIG)
    df['ts'] = pd.to_datetime(df['ts'])
    df = df.set_index('ts').sort_index()
    print(f"  rows={len(df):,} cols={len(df.columns)}")
    print(f"  range={df.index.min()} .. {df.index.max()}")
    have_cols = [s for s in SIGNALS if s in df.columns]
    missing = [s for s in SIGNALS if s not in df.columns]
    if missing:
        print(f"  MISSING signals: {missing}", file=sys.stderr)
    print(f"  signals available: {have_cols}")

    # 1h bars: close-to-close return + hourly volume (matches Phase 1 sanity)
    hourly_close = df['mid_price'].resample('1h').last().dropna()
    hourly_ret = hourly_close.pct_change()

    cache = BASE / 'data' / 'perp' / '1h_cache' / 'BTC_1h.parquet'
    vol_series = None
    if cache.exists():
        try:
            c = pd.read_parquet(cache)
            if 'ts' in c.columns:
                c['ts'] = pd.to_datetime(c['ts'])
                c = c.set_index('ts').sort_index()
            else:
                c.index = pd.to_datetime(c.index)
                c = c.sort_index()
            if 'volume' in c.columns:
                vol_series = c['volume'].astype('float64')
                print(f"  loaded hourly volume from {cache}")
        except Exception as e:
            print(f"  WARN: could not read hourly cache: {e}")

    events = pd.DataFrame({'ret': hourly_ret})
    if vol_series is not None:
        events['volume'] = vol_series.reindex(events.index)
        events['vol_ma24'] = events['volume'].rolling(24, min_periods=6).mean().shift(1)
        events['vol_ratio'] = events['volume'] / events['vol_ma24']
        mask = (events['ret'] < -0.02) & (events['vol_ratio'] > 2.0)
        trigger_label = "BTC 1h ret < -2% AND vol > 2x trailing 24h avg"
    else:
        mask = events['ret'] < -0.02
        trigger_label = "BTC 1h ret < -2% (volume filter unavailable)"

    events = events.dropna(subset=['ret'])
    mask = mask.reindex(events.index).fillna(False)
    trigger_times = events.index[mask]
    print(f"\nTrigger: {trigger_label}")
    print(f"Cascade events found: {len(trigger_times)}")
    if len(trigger_times) == 0:
        return 0

    # Sample signals at offsets
    rows = []
    for t in trigger_times:
        for label, off in OFFSETS_MIN.items():
            stamp = t + pd.Timedelta(minutes=off)
            idx = df.index.searchsorted(stamp, side='right') - 1
            if idx < 0 or idx >= len(df):
                continue
            r = {'event_ts': t, 'offset': label}
            for s in have_cols:
                r[s] = float(df[s].iloc[idx])
            rows.append(r)
    samples = pd.DataFrame(rows)

    print("\n=== mean signal values by offset (cascade events) ===")
    pivot = samples.groupby('offset')[have_cols].mean().reindex(list(OFFSETS_MIN.keys()))
    print(pivot.round(4).to_string())

    print("\n=== median signal values by offset (cascade events) ===")
    pivot_med = samples.groupby('offset')[have_cols].median().reindex(list(OFFSETS_MIN.keys()))
    print(pivot_med.round(4).to_string())

    # Per-signal lead/lag z-table
    baseline_mu = df[have_cols].mean()
    baseline_sd = df[have_cols].std()

    print("\n=== lead/lag z-table (vs all-time baseline) ===")
    header = (f"{'signal':<26}{'all_time_sigma':>16}"
              f"{'t-2h z':>10}{'t-1h z':>10}{'t z':>10}{'t+1h z':>10}{'verdict':>14}")
    print(header)
    print('-' * len(header))
    verdict_table = {}
    for s in have_cols:
        mu = baseline_mu.loc[s]
        sd = baseline_sd.loc[s]
        if sd == 0 or not np.isfinite(sd):
            continue

        def z(off_label):
            if off_label not in pivot.index:
                return np.nan
            v = pivot.loc[off_label, s]
            return (v - mu) / sd if np.isfinite(v) else np.nan

        z_m2 = z('t-2h')
        z_m1 = z('t-1h')
        z_0 = z('t')
        z_p1 = z('t+1h')

        # Verdict: if |z| at T-2h or T-1h is >= 50% of |z(t)|, signal LEADS.
        # Otherwise if |z(t+1h)| > |z(t)|, signal LAGS. Else COINCIDENT.
        def absf(x):
            return abs(x) if np.isfinite(x) else 0.0
        a0 = absf(z_0)
        verdict = 'COINCIDENT'
        if a0 > 0 and (absf(z_m2) >= 0.5 * a0 or absf(z_m1) >= 0.5 * a0):
            verdict = 'LEADS'
        elif a0 > 0 and absf(z_p1) > a0:
            verdict = 'LAGS'
        elif a0 == 0:
            verdict = 'NONE'
        verdict_table[s] = verdict

        print(f"{s:<26}{sd:>16.4f}{z_m2:>10.2f}{z_m1:>10.2f}{z_0:>10.2f}{z_p1:>10.2f}{verdict:>14}")

    # Comparison: which signals show the EARLIEST displacement?
    print("\n=== earliest displacement check (does OFI-M lead DtM?) ===")
    print("for each signal, the earliest offset at which |z| >= 0.5 (half a sigma):")
    for s in have_cols:
        mu = baseline_mu.loc[s]; sd = baseline_sd.loc[s]
        if sd == 0:
            continue
        earliest = None
        for label in ['t-4h', 't-2h', 't-1h', 't', 't+1h']:
            if label not in pivot.index:
                continue
            zv = (pivot.loc[label, s] - mu) / sd
            if np.isfinite(zv) and abs(zv) >= 0.5:
                earliest = (label, zv)
                break
        if earliest:
            print(f"  {s:<26} first |z|>=0.5 at {earliest[0]:<6} (z={earliest[1]:+.2f})")
        else:
            print(f"  {s:<26} no offset reaches |z|>=0.5")

    print("\n=== KEY QUESTION ===")
    print("Phase 1 (DtM) was COINCIDENT — peaked at T=0, no displacement at T-1h.")
    print("Did OFI-M_total_5min show displacement at T-2h or T-1h?")
    if 'ofi_m_total_5min' in verdict_table:
        v = verdict_table['ofi_m_total_5min']
        print(f"  -> ofi_m_total_5min verdict: {v}")
    if 'ofi_m_total_z168h' in verdict_table:
        v = verdict_table['ofi_m_total_z168h']
        print(f"  -> ofi_m_total_z168h verdict: {v}")

    return 0


if __name__ == '__main__':
    sys.exit(main())
