#!/usr/bin/env python3
"""
Build Microstructure Signals — Mission H Phase 1 + Phase 2
====================================================

Computes microstructure signals from Binance Vision bookdepth + trades_1s data
at 1-minute cadence, for use in Mission A (Liquidation Cascade Recovery) and
downstream strategies.

Signals produced per (symbol, 1min bar):
    DtM  (Depth-to-Move):  dtm_bid_1pct, dtm_bid_5pct, dtm_ask_1pct,
                           dtm_ask_5pct, dtm_asymmetry_5pct, dtm_total_5pct,
                           dtm_bid_5pct_z168h, dtm_total_5pct_z168h,
                           dtm_bid_velocity_1h
    OFI-T (Taker imbalance): ofi_t_1min, ofi_t_5min, ofi_t_30min, ofi_t_4h,
                             ofi_t_notional_5min
    VDV  (VWAP dislocation): vwap_5min, vwap_30min, vwap_4h,
                             dev_5min, dev_30min, dev_4h,
                             vdv_5min, vdv_30min, vdv_4h
    OFI-M (Maker order flow imbalance, Phase 2):
                             ofi_m_1pct, ofi_m_5pct, ofi_m_total,
                             ofi_m_1pct_5min, ofi_m_total_5min,
                             ofi_m_total_30min, ofi_m_total_z168h

Output:
    data/perp/binance/{SYMBOL}/microstructure_1min.parquet  (float32 + zstd)

Usage:
    /workspace/venv/bin/python tools/build_microstructure_signals.py \
        --symbols BTCUSDT,ETHUSDT,SOLUSDT \
        --start 2025-04-10 --end 2026-04-05 \
        [--resolution 1min] [--force]

Notes:
  - Binance bookdepth `notional` is CUMULATIVE across bands. dtm_bid_5pct is
    simply the notional at the -5% row (the full ±5% band is the outermost).
  - trades_1s is sparse (gaps when no trades). We reindex to a full 1-second
    grid before rolling-sum to guarantee time-aligned windows.
  - Processing is per-symbol, monthly chunks with a trailing buffer to keep
    long-window rolling aggregates correct across chunk boundaries.
"""

import argparse
import gc
import sys
import time
import warnings
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings('ignore', category=FutureWarning)
warnings.filterwarnings('ignore', category=pd.errors.PerformanceWarning)

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / 'data' / 'perp' / 'binance'

DEFAULT_SYMBOLS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT']

# Rolling-window widths (in 1-second rows) for OFI-T / VDV
OFI_WINDOWS = {
    'ofi_t_1min':  60,
    'ofi_t_5min':  300,
    'ofi_t_30min': 1800,
    'ofi_t_4h':    14400,
}
VWAP_WINDOWS = {
    '5min':  300,
    '30min': 1800,
    '4h':    14400,
}

# Look-back (in 1-min bars) for VDV velocities
VDV_LOOKBACKS = {
    'vdv_5min':  5,
    'vdv_30min': 30,
    'vdv_4h':    240,
}

# 1min bars in 168h (7d) window for z-scores
Z_WINDOW_1MIN = 168 * 60  # 10_080
# Buffer bars (1-min) to carry across monthly chunks so rolling windows remain valid
BUFFER_1MIN = Z_WINDOW_1MIN + 10  # ~7d + slack

# ============================================================
# IO helpers
# ============================================================

def daterange(start: datetime, end: datetime):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def load_bookdepth(symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
    root = DATA_DIR / symbol / 'bookdepth'
    frames = []
    for d in daterange(start, end):
        p = root / f"{d.strftime('%Y-%m-%d')}.parquet"
        if p.exists():
            try:
                frames.append(pd.read_parquet(p))
            except Exception as e:
                print(f"  WARN: failed to read {p}: {e}", file=sys.stderr)
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df['ts'] = pd.to_datetime(df['ts'])
    return df.sort_values('ts', kind='stable')


def load_trades_1s(symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
    root = DATA_DIR / symbol / 'trades_1s'
    frames = []
    for d in daterange(start, end):
        p = root / f"{d.strftime('%Y-%m-%d')}.parquet"
        if p.exists():
            try:
                frames.append(pd.read_parquet(p))
            except Exception as e:
                print(f"  WARN: failed to read {p}: {e}", file=sys.stderr)
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df['ts'] = pd.to_datetime(df['ts'])
    return df.sort_values('ts', kind='stable')


# ============================================================
# Signal computation
# ============================================================

def compute_dtm_1min(book: pd.DataFrame) -> pd.DataFrame:
    """Pivot bookdepth long->wide, keep LAST snapshot per 1min, derive DtM features.

    Returns a frame indexed by 1min ts with DtM columns (pre-rolling features).
    """
    if book.empty:
        return pd.DataFrame()
    # Pivot: one row per ts, columns per percentage
    wide = book.pivot_table(index='ts', columns='percentage', values='notional',
                            aggfunc='last')
    # Ensure all expected columns exist
    for p in (-5, -4, -3, -2, -1, 1, 2, 3, 4, 5):
        if p not in wide.columns:
            wide[p] = np.nan
    wide = wide.sort_index()

    # Binance bookdepth notional is CUMULATIVE; -5 row = full -5% band
    out = pd.DataFrame(index=wide.index)
    out['dtm_bid_1pct'] = wide[-1].astype('float64')
    out['dtm_bid_5pct'] = wide[-5].astype('float64')
    out['dtm_ask_1pct'] = wide[1].astype('float64')
    out['dtm_ask_5pct'] = wide[5].astype('float64')

    tot5 = out['dtm_ask_5pct'] + out['dtm_bid_5pct']
    out['dtm_asymmetry_5pct'] = np.where(
        tot5 > 0,
        (out['dtm_ask_5pct'] - out['dtm_bid_5pct']) / tot5,
        np.nan,
    )
    out['dtm_total_5pct'] = tot5

    # Resample to 1min, take last snapshot in each minute
    out_1m = out.resample('1min').last()
    return out_1m


def compute_rolling_1s_features(trades: pd.DataFrame) -> pd.DataFrame:
    """Reindex trades_1s to full 1Hz grid, compute rolling sums, return 1min frame.

    Produces per-minute: mid_price, ofi_t_*, ofi_t_notional_5min, vwap_*, dev_*.
    """
    if trades.empty:
        return pd.DataFrame()

    # Deduplicate any repeat ts
    trades = trades.drop_duplicates(subset='ts', keep='last').set_index('ts').sort_index()

    # Full 1-second grid (missing seconds -> 0 volume / NaN close)
    full_idx = pd.date_range(trades.index.min().floor('s'),
                             trades.index.max().ceil('s'),
                             freq='1s')
    cols = ['close', 'buy_volume', 'sell_volume', 'buy_notional', 'sell_notional']
    tr = trades.reindex(full_idx)[cols]
    # Forward-fill close (last trade price), zero-fill volumes
    tr['close'] = tr['close'].astype('float64').ffill()
    for c in ('buy_volume', 'sell_volume', 'buy_notional', 'sell_notional'):
        tr[c] = tr[c].astype('float64').fillna(0.0)

    # Rolling sums at each window width (row-count = seconds since grid is 1Hz)
    roll_frames = {}
    for name, w in OFI_WINDOWS.items():
        bv = tr['buy_volume'].rolling(w, min_periods=1).sum()
        sv = tr['sell_volume'].rolling(w, min_periods=1).sum()
        tot = bv + sv
        roll_frames[name] = np.where(tot > 0, (bv - sv) / tot, np.nan)

    # Notional-weighted 5min variant
    bn5 = tr['buy_notional'].rolling(300, min_periods=1).sum()
    sn5 = tr['sell_notional'].rolling(300, min_periods=1).sum()
    tn5 = bn5 + sn5
    roll_frames['ofi_t_notional_5min'] = np.where(
        tn5 > 0, (bn5 - sn5) / tn5, np.nan
    )

    # VWAPs — Σ(price*notional) / Σ(notional) over last N seconds.
    # Approximate price*notional as close_sec * total_notional (notional-weighted).
    tot_notional = tr['buy_notional'] + tr['sell_notional']
    px_notional = tr['close'] * tot_notional
    for label, w in VWAP_WINDOWS.items():
        num = px_notional.rolling(w, min_periods=1).sum()
        den = tot_notional.rolling(w, min_periods=1).sum()
        vwap = np.where(den > 0, num / den, np.nan)
        roll_frames[f'vwap_{label}'] = vwap
        roll_frames[f'dev_{label}'] = np.where(
            (vwap == vwap) & (vwap > 0),
            (tr['close'].values - vwap) / vwap,
            np.nan,
        )

    df = pd.DataFrame(roll_frames, index=tr.index)
    df['mid_price'] = tr['close']

    # Resample to 1min — take value at the last second of the minute
    df_1m = df.resample('1min').last()
    return df_1m


def add_rolling_1min_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add z-scores, velocity, and VDV features computed at 1-min cadence."""
    out = df.copy()
    # 168h z-scores on 1-min bars
    for col in ('dtm_bid_5pct', 'dtm_total_5pct'):
        if col not in out.columns:
            out[f'{col}_z168h'] = np.nan
            continue
        roll = out[col].rolling(Z_WINDOW_1MIN, min_periods=Z_WINDOW_1MIN // 4)
        mu = roll.mean()
        sd = roll.std(ddof=0)
        out[f'{col}_z168h'] = np.where(sd > 0, (out[col] - mu) / sd, np.nan)

    # dtm_bid_velocity_1h: change over 60 1-min bars
    if 'dtm_bid_5pct' in out.columns:
        out['dtm_bid_velocity_1h'] = out['dtm_bid_5pct'].diff(60)
    else:
        out['dtm_bid_velocity_1h'] = np.nan

    # VDV: velocity of dev_N
    for label, k in VDV_LOOKBACKS.items():
        src = 'dev_' + label.split('_', 1)[1]  # vdv_5min -> dev_5min
        if src in out.columns:
            out[label] = out[src] - out[src].shift(k)
        else:
            out[label] = np.nan
    return out


# ============================================================
# Phase 2: Maker OFI (OFI-M)
# ============================================================
# Spec: research/mission_a_orderbook_signals.md "Maker OFI"
#   ΔBid_i = notional_bid[t,-i%] - notional_bid[t-1,-i%]
#   ΔAsk_i = notional_ask[t,+i%] - notional_ask[t-1,+i%]
#   OFI-M[i] = ΔBid_i - ΔAsk_i; OFI-M_total = Σ w_i * OFI-M[i]
# Skip Δ if |Δmid|/mid > 0.2% or gap > 300s (handles day boundaries).

OFI_M_BAND_WEIGHTS = {1: 0.50, 2: 0.25, 3: 0.15, 4: 0.05, 5: 0.05}
OFI_M_MID_SHIFT_THRESHOLD = 0.002
OFI_M_MAX_GAP_SECONDS = 300

OFI_M_COLUMNS = [
    'ofi_m_1pct', 'ofi_m_5pct', 'ofi_m_total',
    'ofi_m_1pct_5min', 'ofi_m_total_5min', 'ofi_m_total_30min',
    'ofi_m_total_z168h',
]


def _add_ofi_m_rollings(df: pd.DataFrame) -> pd.DataFrame:
    """Add 5min/30min rolling means and 168h z-score to a frame with raw OFI-M cols."""
    df['ofi_m_1pct_5min'] = df['ofi_m_1pct'].rolling(5, min_periods=2).mean()
    df['ofi_m_total_5min'] = df['ofi_m_total'].rolling(5, min_periods=2).mean()
    df['ofi_m_total_30min'] = df['ofi_m_total'].rolling(30, min_periods=6).mean()
    roll = df['ofi_m_total'].rolling(Z_WINDOW_1MIN, min_periods=Z_WINDOW_1MIN // 4)
    mu, sd = roll.mean(), roll.std(ddof=0)
    df['ofi_m_total_z168h'] = np.where(sd > 0, (df['ofi_m_total'] - mu) / sd, np.nan)
    return df


def compute_ofi_m(book: pd.DataFrame, trades_1s: pd.DataFrame) -> pd.DataFrame:
    """Maker OFI from consecutive bookdepth snapshots, returned at 1-min cadence."""
    if book.empty:
        return pd.DataFrame()

    wide = book.pivot_table(index='ts', columns='percentage', values='notional',
                            aggfunc='last')
    for p in (-5, -4, -3, -2, -1, 1, 2, 3, 4, 5):
        if p not in wide.columns:
            wide[p] = np.nan
    wide = wide.sort_index().astype('float64')
    wide.index = pd.to_datetime(wide.index).astype('datetime64[ns]')

    # Mid price at each snapshot — asof to trades_1s.close (no native mid in bookdepth)
    if trades_1s is not None and not trades_1s.empty:
        tr = trades_1s[['ts', 'close']].copy()
        tr['ts'] = pd.to_datetime(tr['ts']).astype('datetime64[ns]')
        tr = tr.drop_duplicates(subset='ts', keep='last').sort_values('ts')
        wr = wide.reset_index().rename(columns={'index': 'ts'}).sort_values('ts')
        m = pd.merge_asof(wr, tr, on='ts', direction='nearest',
                          tolerance=pd.Timedelta('5s'))
        mid = m.set_index('ts')['close'].astype('float64')
    else:
        mid = pd.Series(np.nan, index=wide.index, dtype='float64')

    # Validity: positive intra-day gap AND mid moved <0.2%
    gap = wide.index.to_series().diff().dt.total_seconds()
    mid_shift = (mid - mid.shift(1)).abs() / mid.shift(1)
    valid = ((gap > 0) & (gap <= OFI_M_MAX_GAP_SECONDS)
             & (mid_shift <= OFI_M_MID_SHIFT_THRESHOLD) & mid_shift.notna())

    ofim = {}
    total = pd.Series(0.0, index=wide.index)
    band1_valid = None
    for i in range(1, 6):
        prev_bid, prev_ask = wide[-i].shift(1), wide[i].shift(1)
        band_valid = (valid & prev_bid.notna() & (prev_bid > 0)
                      & prev_ask.notna() & (prev_ask > 0))
        band = (wide[-i].diff() - wide[i].diff()).where(band_valid, np.nan)
        ofim[i] = band
        if i == 1:
            band1_valid = band_valid
        total = total + band.fillna(0.0) * OFI_M_BAND_WEIGHTS[i]
    total = total.where(band1_valid, np.nan)

    snap = pd.DataFrame({
        'ofi_m_1pct': ofim[1], 'ofi_m_5pct': ofim[5], 'ofi_m_total': total,
    }, index=wide.index)
    snap_1m = snap.resample('1min').last()
    return _add_ofi_m_rollings(snap_1m)


def process_symbol_phase2(symbol: str, start: datetime, end: datetime) -> dict:
    """Compute OFI-M and merge into existing microstructure_1min.parquet (atomic)."""
    out_path = DATA_DIR / symbol / 'microstructure_1min.parquet'
    print(f"[{symbol}] PHASE 2 (OFI-M) {start.date()} -> {end.date()}")

    month_starts = pd.date_range(start, end, freq='MS').to_pydatetime().tolist()
    if not month_starts or month_starts[0] > start:
        month_starts.insert(0, start)
    month_starts = sorted({datetime(d.year, d.month, 1) if d.day == 1 else d
                           for d in month_starts})

    chunks = []
    for i, ms in enumerate(month_starts):
        cs = max(ms, start)
        ce = min(month_starts[i + 1] - timedelta(days=1) if i + 1 < len(month_starts)
                 else end, end)
        if cs > ce:
            continue
        t0 = time.time()
        print(f"  chunk {cs.date()} .. {ce.date()}")
        book = load_bookdepth(symbol, cs, ce)
        trades = load_trades_1s(symbol, cs, ce)
        if book.empty:
            print("    (no bookdepth)"); continue
        ofi_m = compute_ofi_m(book, trades)
        del book, trades; gc.collect()
        if ofi_m.empty:
            continue
        chunks.append(ofi_m[['ofi_m_1pct', 'ofi_m_5pct', 'ofi_m_total']])
        print(f"    chunk rows={len(ofi_m)} in {time.time()-t0:.1f}s")

    if not chunks:
        return {'symbol': symbol, 'rows': 0}

    raw = pd.concat(chunks).sort_index()
    raw = raw[~raw.index.duplicated(keep='last')]
    del chunks; gc.collect()

    idx = pd.date_range(raw.index.min().floor('min'),
                        raw.index.max().ceil('min'), freq='1min')
    raw = raw.reindex(idx)
    raw.index.name = 'ts'
    raw = _add_ofi_m_rollings(raw)[OFI_M_COLUMNS].astype('float32')

    if out_path.exists():
        existing = pd.read_parquet(out_path)
        existing['ts'] = pd.to_datetime(existing['ts'])
        existing = existing.drop(columns=[c for c in OFI_M_COLUMNS
                                          if c in existing.columns], errors='ignore')
        merged = existing.merge(raw.reset_index().rename(columns={'index': 'ts'}),
                                on='ts', how='left')
    else:
        merged = raw.reset_index().rename(columns={'index': 'ts'})
    for c in OFI_M_COLUMNS:
        merged[c] = merged[c].astype('float32')

    tmp = out_path.with_suffix('.parquet.tmp')
    out_path.parent.mkdir(parents=True, exist_ok=True)
    merged.to_parquet(tmp, compression='zstd', index=False)
    tmp.replace(out_path)
    size_mb = out_path.stat().st_size / (1024 * 1024)
    nan_rate = float(merged['ofi_m_total'].isna().mean())
    print(f"[{symbol}] PHASE 2 wrote {len(merged):,} rows -> {out_path} "
          f"({size_mb:.1f} MB, ofi_m_total NaN rate={nan_rate:.3f})")
    return {'symbol': symbol, 'path': str(out_path), 'rows': len(merged),
            'size_mb': size_mb, 'ofi_m_nan_rate': nan_rate}


FINAL_COLUMNS = [
    'ts', 'mid_price',
    # DtM (9)
    'dtm_bid_1pct', 'dtm_bid_5pct', 'dtm_ask_1pct', 'dtm_ask_5pct',
    'dtm_asymmetry_5pct', 'dtm_total_5pct',
    'dtm_bid_5pct_z168h', 'dtm_total_5pct_z168h', 'dtm_bid_velocity_1h',
    # OFI-T (5)
    'ofi_t_1min', 'ofi_t_5min', 'ofi_t_30min', 'ofi_t_4h',
    'ofi_t_notional_5min',
    # VDV block (9: 3 vwap + 3 dev + 3 vdv)
    'vwap_5min', 'vwap_30min', 'vwap_4h',
    'dev_5min', 'dev_30min', 'dev_4h',
    'vdv_5min', 'vdv_30min', 'vdv_4h',
]


def process_symbol(symbol: str, start: datetime, end: datetime,
                   force: bool) -> dict:
    out_path = DATA_DIR / symbol / 'microstructure_1min.parquet'
    if out_path.exists() and not force:
        print(f"[{symbol}] output exists, skip (use --force): {out_path}")
        existing = pd.read_parquet(out_path)
        return {'symbol': symbol, 'path': str(out_path),
                'rows': len(existing), 'skipped': True}

    print(f"[{symbol}] processing {start.date()} -> {end.date()}")

    # Chunk by month to bound memory; keep a trailing buffer for rolling windows.
    month_starts = pd.date_range(start, end, freq='MS').to_pydatetime().tolist()
    if not month_starts or month_starts[0] > start:
        month_starts.insert(0, start)
    month_starts = sorted({datetime(d.year, d.month, 1) if d.day == 1 else d
                           for d in month_starts})

    chunks = []
    for i, ms in enumerate(month_starts):
        chunk_start = max(ms, start)
        if i + 1 < len(month_starts):
            chunk_end = month_starts[i + 1] - timedelta(days=1)
        else:
            chunk_end = end
        chunk_end = min(chunk_end, end)
        if chunk_start > chunk_end:
            continue

        t0 = time.time()
        print(f"  chunk {chunk_start.date()} .. {chunk_end.date()}")
        book = load_bookdepth(symbol, chunk_start, chunk_end)
        trades = load_trades_1s(symbol, chunk_start, chunk_end)
        if book.empty and trades.empty:
            print(f"    (no data)")
            continue

        dtm = compute_dtm_1min(book) if not book.empty else pd.DataFrame()
        tr1m = compute_rolling_1s_features(trades) if not trades.empty else pd.DataFrame()
        del book, trades
        gc.collect()

        # Outer-join on 1min grid
        if tr1m.empty:
            merged = dtm
        elif dtm.empty:
            merged = tr1m
        else:
            merged = tr1m.join(dtm, how='outer')
        merged = merged.sort_index()
        chunks.append(merged)
        print(f"    chunk rows={len(merged)} in {time.time()-t0:.1f}s")

    if not chunks:
        print(f"[{symbol}] no chunks produced")
        return {'symbol': symbol, 'path': None, 'rows': 0}

    full = pd.concat(chunks).sort_index()
    full = full[~full.index.duplicated(keep='last')]
    del chunks
    gc.collect()

    # Reindex to a dense 1-min grid so diffs / shifts use calendar time consistently
    idx = pd.date_range(full.index.min().floor('min'),
                        full.index.max().ceil('min'), freq='1min')
    full = full.reindex(idx)
    full.index.name = 'ts'

    # Forward-fill DtM bands up to 5 minutes (book is ~33s, so 2-3 rows tops)
    dtm_cols = [c for c in full.columns if c.startswith('dtm_')
                and not c.endswith('_z168h')
                and not c.endswith('_velocity_1h')
                and c != 'dtm_asymmetry_5pct']
    for c in dtm_cols:
        full[c] = full[c].ffill(limit=5)
    if 'dtm_asymmetry_5pct' in full.columns:
        tot = full.get('dtm_ask_5pct', 0) + full.get('dtm_bid_5pct', 0)
        full['dtm_asymmetry_5pct'] = np.where(
            tot > 0,
            (full['dtm_ask_5pct'] - full['dtm_bid_5pct']) / tot,
            np.nan,
        )

    # Derived 1-min features (z-scores, velocity, VDV)
    full = add_rolling_1min_features(full)

    # Assemble in canonical order, cast to float32
    full = full.reset_index().rename(columns={'index': 'ts'})
    for c in FINAL_COLUMNS:
        if c not in full.columns:
            full[c] = np.nan
    full = full[FINAL_COLUMNS]
    for c in full.columns:
        if c == 'ts':
            continue
        full[c] = full[c].astype('float32')

    out_path.parent.mkdir(parents=True, exist_ok=True)
    full.to_parquet(out_path, compression='zstd', index=False)
    size_mb = out_path.stat().st_size / (1024 * 1024)
    print(f"[{symbol}] wrote {len(full):,} rows -> {out_path} ({size_mb:.1f} MB)")

    # Quick NaN audit
    nan_rates = {c: float(full[c].isna().mean()) for c in FINAL_COLUMNS if c != 'ts'}
    return {'symbol': symbol, 'path': str(out_path), 'rows': len(full),
            'size_mb': size_mb, 'nan_rates': nan_rates}


def parse_args():
    p = argparse.ArgumentParser(description='Build microstructure signal parquets')
    p.add_argument('--symbols', default=','.join(DEFAULT_SYMBOLS))
    p.add_argument('--start', default='2025-04-10')
    p.add_argument('--end', default='2026-04-05')
    p.add_argument('--resolution', default='1min',
                   help='Output cadence (only 1min supported in Phase 1)')
    p.add_argument('--force', action='store_true')
    p.add_argument('--phase', default='1', choices=['1', '2', 'all'],
                   help='1 = DtM/OFI-T/VDV only, 2 = OFI-M only (merge), all = both')
    return p.parse_args()


def main():
    args = parse_args()
    if args.resolution != '1min':
        raise ValueError(f"Only --resolution 1min is supported, got {args.resolution}")
    symbols = [s.strip().upper() for s in args.symbols.split(',') if s.strip()]
    start = datetime.strptime(args.start, '%Y-%m-%d')
    end = datetime.strptime(args.end, '%Y-%m-%d')
    if end < start:
        raise ValueError("--end must be >= --start")

    results = []
    for sym in symbols:
        try:
            if args.phase in ('1', 'all'):
                res = process_symbol(sym, start, end, args.force)
                results.append(res)
            if args.phase in ('2', 'all'):
                res2 = process_symbol_phase2(sym, start, end)
                results.append(res2)
        except Exception as e:
            import traceback
            print(f"[{sym}] FAILED: {e}", file=sys.stderr)
            traceback.print_exc()

    print("\n=== SUMMARY ===")
    for r in results:
        print(f"  {r['symbol']}: rows={r.get('rows', 0):,} path={r.get('path')}")


if __name__ == '__main__':
    main()
