#!/usr/bin/env python3
"""
Mission H Phase 1 — Sanity Check
================================================

Answers: do the microstructure signals LEAD the original Mission A cascade
definition ("BTC 1h close-to-close < -2% AND 1h volume > 2x trailing 24h
average hourly volume"), coincide with it, or lag it?

For each cascade event found in BTCUSDT, we sample three signals at
T-1h, T, T+1h, T+6h, T+12h:
    * dtm_bid_5pct_z168h  (book thinness)
    * ofi_t_5min          (taker sell pressure)
    * vdv_5min            (VWAP dislocation velocity)

And compare (a) global means, (b) cascade-time means, (c) t-1h means to
detect leading behavior.

Run:
    /workspace/venv/bin/python research/mission_h_sanity.py
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parent.parent
MSIG = BASE / 'data' / 'perp' / 'binance' / 'BTCUSDT' / 'microstructure_1min.parquet'

SIGNALS = ['dtm_bid_5pct_z168h', 'ofi_t_5min', 'vdv_5min']
OFFSETS_MIN = {'t-1h': -60, 't': 0, 't+1h': 60, 't+6h': 360, 't+12h': 720}


def main() -> int:
    if not MSIG.exists():
        print(f"ERROR: {MSIG} does not exist — run build_microstructure_signals.py first",
              file=sys.stderr)
        return 1

    print(f"loading {MSIG} ...")
    df = pd.read_parquet(MSIG)
    df['ts'] = pd.to_datetime(df['ts'])
    df = df.set_index('ts').sort_index()
    print(f"  rows={len(df):,} cols={len(df.columns)}")
    print(f"  range={df.index.min()} .. {df.index.max()}")

    # 1h bars: close-to-close return + hourly volume.
    # We don't have buy+sell volume in the microstructure parquet, so proxy
    # "1h volume" with mean |vdv_5min| is WRONG. Instead we reload trades_1s
    # hourly sum via resampled mid_price returns — and for volume we load the
    # raw BTC 1h cache if present. Fall back to |mid return| proxy otherwise.

    # Resample mid price to hourly close
    hourly_close = df['mid_price'].resample('1h').last().dropna()
    hourly_ret = hourly_close.pct_change()

    # Try to use the project 1h cache for volume; fall back to nan filter
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

    # Align
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
        print("No events — nothing to analyse.")
        return 0

    # For each event, sample signals at offsets
    rows = []
    for t in trigger_times:
        for label, off in OFFSETS_MIN.items():
            stamp = t + pd.Timedelta(minutes=off)
            # nearest 1min row at or before stamp
            idx = df.index.searchsorted(stamp, side='right') - 1
            if idx < 0 or idx >= len(df):
                continue
            r = {'event_ts': t, 'offset': label}
            for s in SIGNALS:
                r[s] = float(df[s].iloc[idx]) if s in df.columns else np.nan
            rows.append(r)
    samples = pd.DataFrame(rows)

    print("\n=== mean signal values by offset (cascade events) ===")
    pivot = samples.groupby('offset')[SIGNALS].mean().reindex(list(OFFSETS_MIN.keys()))
    print(pivot.round(4).to_string())

    print("\n=== median signal values by offset (cascade events) ===")
    pivot_med = samples.groupby('offset')[SIGNALS].median().reindex(list(OFFSETS_MIN.keys()))
    print(pivot_med.round(4).to_string())

    # Global baseline
    baseline = df[SIGNALS].mean().rename('all_time_mean')
    baseline_std = df[SIGNALS].std().rename('all_time_std')

    summary = pd.DataFrame({
        'all_time_mean':    baseline,
        'all_time_std':     baseline_std,
        'event_t-1h_mean':  pivot.loc['t-1h'] if 't-1h' in pivot.index else np.nan,
        'event_t_mean':     pivot.loc['t'] if 't' in pivot.index else np.nan,
        'event_t+1h_mean':  pivot.loc['t+1h'] if 't+1h' in pivot.index else np.nan,
        'event_t+6h_mean':  pivot.loc['t+6h'] if 't+6h' in pivot.index else np.nan,
        'event_t+12h_mean': pivot.loc['t+12h'] if 't+12h' in pivot.index else np.nan,
    })
    print("\n=== summary table ===")
    print(summary.round(4).to_string())

    # Verdict: is signal value at t-1h already materially displaced vs baseline?
    print("\n=== lead/lag verdict ===")
    for s in SIGNALS:
        mu = baseline.loc[s]
        sd = baseline_std.loc[s]
        if sd == 0 or not np.isfinite(sd):
            continue
        z_t_minus_1 = (pivot.loc['t-1h', s] - mu) / sd if 't-1h' in pivot.index else np.nan
        z_t = (pivot.loc['t', s] - mu) / sd if 't' in pivot.index else np.nan
        z_t_plus_1 = (pivot.loc['t+1h', s] - mu) / sd if 't+1h' in pivot.index else np.nan
        verdict = 'COINCIDENT'
        if np.isfinite(z_t_minus_1) and abs(z_t_minus_1) > 0.5 * abs(z_t):
            verdict = 'LEADS'
        elif np.isfinite(z_t_plus_1) and abs(z_t_plus_1) > abs(z_t):
            verdict = 'LAGS'
        print(f"  {s:24s} zt-1h={z_t_minus_1:+.2f}  zt={z_t:+.2f}  "
              f"zt+1h={z_t_plus_1:+.2f}  -> {verdict}")

    return 0


if __name__ == '__main__':
    sys.exit(main())
