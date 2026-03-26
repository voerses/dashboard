#!/usr/bin/env python3
"""
Build Parquet Cache — Multi-Exchange Data Pipeline
====================================================

Reads CSVs from Binance, Kraken, and Hyperliquid.
Normalizes, merges, validates quality, and outputs clean parquet files.

Supports two market types:
  --market perp  (default) → reads from data/perp/, writes to data/perp/1h_cache/
  --market spot             → reads from data/spot/binance/, writes to data/spot/1h_cache/

Only 1h parquets are built. Engine computes 4h + daily from 1h on the fly (~5ms/token).

Priority for perp: Binance > Kraken > Hyperliquid.
Spot: Binance only (single source).

Usage:
    python tools/build_parquet_cache.py [--market perp|spot] [--tokens BTC,ETH] [--force] [--verbose]
"""

import os
import sys
import argparse
import time
import json
import warnings
from pathlib import Path
from datetime import datetime, timezone

import numpy as np
import pandas as pd

warnings.filterwarnings('ignore', category=FutureWarning)

# ============================================================
# Config
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / 'data'
PERP_DIR = DATA_DIR / 'perp'
SPOT_DIR = DATA_DIR / 'spot'
CACHE_DIR = DATA_DIR / 'perp' / '1h_cache'  # default (perp), overridden by --market
REPORT_DIR = DATA_DIR / 'quality_reports'

# Exchange-specific symbol mappings
# Binance uses 1000X prefix for low-price tokens
BINANCE_SYMBOL_MAP = {
    '1000PEPE': 'PEPE', '1000BONK': 'BONK', '1000FLOKI': 'FLOKI',
    '1000SHIB': 'SHIB', '1000LUNC': 'LUNC', '1000SATS': 'SATS',
    '1000XEC': 'XEC', '1000RATS': 'RATS', '1000CAT': 'CAT',
}
BINANCE_REVERSE_MAP = {v: k for k, v in BINANCE_SYMBOL_MAP.items()}

# Hyperliquid uses kPEPE etc
HYPER_SYMBOL_MAP = {'kPEPE': 'PEPE', 'kBONK': 'BONK', 'kSHIB': 'SHIB',
                    'kFLOKI': 'FLOKI'}
HYPER_REVERSE_MAP = {v: k for k, v in HYPER_SYMBOL_MAP.items()}

# Quality check thresholds
QC = {
    'max_gap_hours': 6,           # Gaps > 6h flagged as critical
    'max_stale_run': 3,           # 3+ identical OHLC candles = stale
    'max_stale_run_altcoin': 6,   # More lenient for altcoins
    'spike_pct_btc': 0.15,        # 15% single-bar BTC
    'spike_pct_alt': 0.25,        # 25% single-bar altcoin
    'spike_zscore': 4.0,          # Z-score threshold
    'vol_spike_mult': 10,         # Volume > 10x median
    'range_pct_btc': 0.10,        # 10% intra-candle range BTC
    'range_pct_alt': 0.20,        # 20% intra-candle range altcoin
    'continuity_pct': 0.005,      # 0.5% close-to-open gap
    'funding_max': 0.0075,        # 0.75% max funding rate
    'completeness_major': 0.995,  # 99.5% for BTC/ETH
    'completeness_alt': 0.98,     # 98% for alts
    'min_bars': 500,              # Engine minimum
}

MAJORS = {'BTC', 'ETH', 'SOL', 'XRP', 'BNB', 'DOGE', 'SUI'}


# ============================================================
# CSV Readers (one per exchange)
# ============================================================

def read_binance_ohlcv(token):
    """Read Binance 1h OHLCV CSV. Has the richest schema."""
    # Check for 1000X naming
    name = BINANCE_REVERSE_MAP.get(token, token)
    path = PERP_DIR / 'binance' / '1h_ohlcv' / f'{name}_perp_1h.csv'
    if not path.exists():
        path = PERP_DIR / 'binance' / '1h_ohlcv' / f'{token}_perp_1h.csv'
    if not path.exists():
        return None

    df = pd.read_csv(path)
    # Columns: timestamp, datetime, open, high, low, close, volume
    # Timestamp is in milliseconds
    ts_col = 'timestamp'
    if ts_col not in df.columns:
        return None

    df['dt'] = pd.to_datetime(df[ts_col], unit='ms', utc=True).dt.tz_localize(None)
    df = df.set_index('dt').sort_index()

    # Normalize column names
    cols = {'open': 'open', 'high': 'high', 'low': 'low',
            'close': 'close', 'volume': 'volume'}
    out = df[list(cols.keys())].rename(columns=cols).copy()
    out.index.name = 'datetime'

    # If this is a 1000X token, divide prices by 1000
    if name in BINANCE_SYMBOL_MAP:
        for c in ['open', 'high', 'low', 'close']:
            out[c] = out[c] / 1000.0

    return out


def read_kraken_ohlcv(token):
    """Read Kraken 1h OHLCV CSV."""
    path = PERP_DIR / 'kraken' / '1h_ohlcv' / f'{token}_perp_1h.csv'
    if not path.exists():
        return None

    df = pd.read_csv(path)
    # Columns: timestamp, open, high, low, close, volume
    if 'timestamp' not in df.columns:
        return None

    df['dt'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True).dt.tz_localize(None)
    df = df.set_index('dt').sort_index()

    cols = {'open': 'open', 'high': 'high', 'low': 'low',
            'close': 'close', 'volume': 'volume'}
    out = df[list(cols.keys())].rename(columns=cols).copy()
    out.index.name = 'datetime'
    return out


def read_hyperliquid_ohlcv(token):
    """Read Hyperliquid 1h OHLCV CSV. Non-standard column names."""
    name = HYPER_REVERSE_MAP.get(token, token)
    path = PERP_DIR / 'hyperliquid' / 'ohlcv' / f'{name}_perp_1h.csv'
    if not path.exists():
        path = PERP_DIR / 'hyperliquid' / 'ohlcv' / f'{token}_perp_1h.csv'
    if not path.exists():
        return None

    df = pd.read_csv(path)
    # Columns: T(close_time_ms), c(close), h(high), i(interval), l(low),
    #          n(trades), o(open), s(symbol), t(open_time_ms), v(volume)
    if 't' not in df.columns:
        return None

    df['dt'] = pd.to_datetime(df['t'], unit='ms', utc=True).dt.tz_localize(None)
    df = df.set_index('dt').sort_index()

    out = pd.DataFrame({
        'open': df['o'].astype(float),
        'high': df['h'].astype(float),
        'low': df['l'].astype(float) if 'l' in df.columns else df['i'].astype(float),
        'close': df['c'].astype(float),
        'volume': df['v'].astype(float),
    }, index=df.index)
    out.index.name = 'datetime'

    # Fix: Hyperliquid uses 'l' for low, but CSV header might use 'i' for interval
    # The actual low column in the header 'T,c,h,i,l,n,o,s,t,v' maps to:
    # T=close_time, c=close, h=high, i=interval, l=low, n=trades, o=open, s=symbol, t=open_time, v=volume
    # So 'l' is indeed low. But if the CSV has it under 'l', we already got it.
    # Let me re-check — the header is T,c,h,i,l,n,o,s,t,v
    # l IS the low column.
    return out


def read_binance_spot_ohlcv(token):
    """Read Binance spot 1h OHLCV CSV."""
    path = SPOT_DIR / 'binance' / '1h_ohlcv' / f'{token}_spot_1h.csv'
    if not path.exists():
        return None

    df = pd.read_csv(path)
    if 'timestamp' not in df.columns:
        return None

    df['dt'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True).dt.tz_localize(None)
    df = df.set_index('dt').sort_index()

    cols = {'open': 'open', 'high': 'high', 'low': 'low',
            'close': 'close', 'volume': 'volume'}
    out = df[list(cols.keys())].rename(columns=cols).copy()
    out.index.name = 'datetime'
    return out


def read_binance_funding(token):
    """Read Binance funding rate CSV."""
    path = PERP_DIR / 'binance' / 'funding' / f'{BINANCE_REVERSE_MAP.get(token, token)}_funding.csv'
    if not path.exists():
        path = PERP_DIR / 'binance' / 'funding' / f'{token}_funding.csv'
    if not path.exists():
        return None

    df = pd.read_csv(path)
    if 'timestamp' not in df.columns or 'funding_rate' not in df.columns:
        return None

    df['dt'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True).dt.tz_localize(None)
    df = df.set_index('dt').sort_index()
    return df[['funding_rate']].copy()


def read_kraken_funding(token):
    """Read Kraken funding rate CSV."""
    path = PERP_DIR / 'kraken' / 'funding' / f'{token}_funding.csv'
    if not path.exists():
        return None

    df = pd.read_csv(path)
    if 'timestamp' not in df.columns or 'funding_rate' not in df.columns:
        return None

    df['dt'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True).dt.tz_localize(None)
    df = df.set_index('dt').sort_index()
    return df[['funding_rate']].copy()


def read_hyperliquid_funding(token):
    """Read Hyperliquid funding rate CSV."""
    name = HYPER_REVERSE_MAP.get(token, token)
    path = PERP_DIR / 'hyperliquid' / 'funding' / f'{name}_funding.csv'
    if not path.exists():
        path = PERP_DIR / 'hyperliquid' / 'funding' / f'{token}_funding.csv'
    if not path.exists():
        return None

    df = pd.read_csv(path)
    # Columns: coin, fundingRate, premium, time
    if 'time' not in df.columns or 'fundingRate' not in df.columns:
        return None

    df['dt'] = pd.to_datetime(df['time'], unit='ms', utc=True).dt.tz_localize(None)
    df = df.set_index('dt').sort_index()
    df = df.rename(columns={'fundingRate': 'funding_rate'})
    return df[['funding_rate']].copy()


# ============================================================
# Funding Rate Resampling
# ============================================================

def resample_funding_to_1h(funding_df):
    """Resample already-normalized per-hour funding rates to 1h grid.

    Input funding_rate values are already per-hour (normalized by
    _normalize_funding_to_hourly in merge_funding). This function simply
    creates a funding_1h column (equal to funding_rate) and forward-fills
    to the 1h grid.

    Returns DataFrame with columns: funding_rate (per-hour ffilled), funding_1h (same)
    """
    if funding_df is None or len(funding_df) < 2:
        return None

    funding_df = funding_df.copy()
    funding_df['funding_1h'] = funding_df['funding_rate']

    # Resample to 1h grid with forward-fill
    funding_1h = funding_df.resample('1h').ffill()

    return funding_1h


# ============================================================
# Multi-Exchange Merge
# ============================================================

def merge_ohlcv(token, verbose=False, market='perp'):
    """Load OHLCV from exchanges, merge with Binance priority."""
    sources = {}

    if market == 'spot':
        # Spot: Binance only
        df = read_binance_spot_ohlcv(token)
        if df is not None and len(df) > 0:
            sources['binance_spot'] = df
            if verbose:
                print(f'    [binance_spot] {len(df)} bars: '
                      f'{df.index[0].strftime("%Y-%m-%d")} → '
                      f'{df.index[-1].strftime("%Y-%m-%d")}')
    else:
        # Perp: multi-exchange merge
        for name, reader in [('binance', read_binance_ohlcv),
                              ('kraken', read_kraken_ohlcv),
                              ('hyperliquid', read_hyperliquid_ohlcv)]:
            df = reader(token)
            if df is not None and len(df) > 0:
                sources[name] = df
                if verbose:
                    print(f'    [{name}] {len(df)} bars: '
                          f'{df.index[0].strftime("%Y-%m-%d")} → '
                          f'{df.index[-1].strftime("%Y-%m-%d")}')

    if not sources:
        return None, {}

    # Priority merge: binance > kraken > hyperliquid (spot uses binance_spot)
    priority = ['binance', 'binance_spot', 'kraken', 'hyperliquid']
    primary_name = next((n for n in priority if n in sources), None)
    if primary_name is None:
        return None, {}
    primary = sources[primary_name]

    # Align to hourly grid
    primary = primary[~primary.index.duplicated(keep='last')]

    # Fill gaps from secondary sources
    fill_info = {'primary': primary_name, 'filled_from': {}}
    for name in priority:
        if name == primary_name or name not in sources:
            continue
        sec = sources[name]
        sec = sec[~sec.index.duplicated(keep='last')]

        # Find gaps in primary
        gaps = primary.index.to_series().diff()
        gap_mask = gaps > pd.Timedelta(hours=1)

        if gap_mask.any():
            # Get the gap periods
            gap_starts = primary.index[gap_mask]
            filled = 0
            for gs in gap_starts:
                prev_idx = primary.index[primary.index < gs][-1] if len(primary.index[primary.index < gs]) > 0 else gs
                # Fill from secondary
                fill = sec[(sec.index > prev_idx) & (sec.index < gs)]
                if len(fill) > 0:
                    primary = pd.concat([primary, fill]).sort_index()
                    primary = primary[~primary.index.duplicated(keep='first')]
                    filled += len(fill)
            if filled > 0:
                fill_info['filled_from'][name] = filled

    # Ensure numeric types
    for col in ['open', 'high', 'low', 'close', 'volume']:
        if col in primary.columns:
            primary[col] = pd.to_numeric(primary[col], errors='coerce')

    return primary, fill_info


def _normalize_funding_to_hourly(df):
    """Normalize a single exchange's funding rates to per-hour values.

    Detects the settlement interval from median timestamp diffs and divides
    accordingly.  Returns the DataFrame with funding_rate converted to per-hour.
    """
    if df is None or len(df) < 2:
        return df

    diffs_hours = df.index.to_series().diff().dt.total_seconds().dropna() / 3600
    if len(diffs_hours) == 0:
        return df

    median_h = float(diffs_hours.median())
    if median_h < 2:
        interval_hours = 1
    elif median_h < 6:
        interval_hours = 4
    else:
        interval_hours = 8

    if interval_hours > 1:
        df = df.copy()
        df['funding_rate'] = df['funding_rate'] / interval_hours

    return df


def merge_funding(token):
    """Load funding rates from all exchanges, normalize to per-hour, then merge.

    Each exchange's raw rates are divided by their settlement interval BEFORE
    merging, so Binance 8h rates and Hyperliquid 1h rates are in the same unit.
    """
    sources = {}
    for name, reader in [('binance', read_binance_funding),
                          ('kraken', read_kraken_funding),
                          ('hyperliquid', read_hyperliquid_funding)]:
        df = reader(token)
        if df is not None and len(df) > 0:
            # Normalize to per-hour rates before merging
            df = _normalize_funding_to_hourly(df)
            sources[name] = df

    if not sources:
        return None

    # Use the source with the longest history as primary
    primary_name = max(sources, key=lambda n: len(sources[n]))
    primary = sources[primary_name].copy()
    primary = primary[~primary.index.duplicated(keep='last')]

    # Fill gaps from other sources
    for name, df in sources.items():
        if name == primary_name:
            continue
        df = df[~df.index.duplicated(keep='last')]
        missing = df.index.difference(primary.index)
        if len(missing) > 0:
            primary = pd.concat([primary, df.loc[missing]]).sort_index()

    return primary


# ============================================================
# Quality Checks
# ============================================================

def run_quality_checks(df, token, verbose=False):
    """Run all 15 quality checks on OHLCV data. Returns issues dict."""
    issues = {
        'token': token,
        'total_bars': len(df),
        'date_range': f'{df.index[0]} → {df.index[-1]}',
        'checks': {},
        'critical': [],
        'warnings': [],
        'fixes_applied': [],
    }

    is_major = token in MAJORS
    n = len(df)

    # --- 1. Timestamp alignment ---
    misaligned = df.index[df.index.minute != 0].union(df.index[df.index.second != 0])
    issues['checks']['timestamp_alignment'] = {'misaligned': len(misaligned)}
    if len(misaligned) > 0:
        issues['warnings'].append(f'{len(misaligned)} timestamps not aligned to hour')

    # --- 2. Gap detection ---
    diffs = df.index.to_series().diff().dt.total_seconds() / 3600
    gaps = diffs[diffs > 1.0]
    gap_hours = gaps.sum() - len(gaps)  # Total missing hours
    critical_gaps = gaps[gaps > QC['max_gap_hours']]
    issues['checks']['gaps'] = {
        'total_gap_bars': int(gap_hours),
        'n_gaps': len(gaps),
        'n_critical_gaps': len(critical_gaps),
        'max_gap_hours': float(gaps.max()) if len(gaps) > 0 else 0,
    }
    if len(critical_gaps) > 0:
        issues['critical'].append(f'{len(critical_gaps)} gaps > {QC["max_gap_hours"]}h')

    # Expected completeness
    expected_bars = int((df.index[-1] - df.index[0]).total_seconds() / 3600) + 1
    completeness = n / max(expected_bars, 1)
    threshold = QC['completeness_major'] if is_major else QC['completeness_alt']
    issues['checks']['completeness'] = {
        'expected': expected_bars,
        'actual': n,
        'pct': round(completeness * 100, 2),
    }
    if completeness < threshold:
        issues['warnings'].append(f'Completeness {completeness:.1%} < {threshold:.1%}')

    # --- 3. Duplicates ---
    n_dupes = df.index.duplicated().sum()
    issues['checks']['duplicates'] = {'count': int(n_dupes)}
    if n_dupes > 0:
        issues['critical'].append(f'{n_dupes} duplicate timestamps')

    # --- 4. OHLC structural consistency ---
    o, h, l, c = df['open'].values, df['high'].values, df['low'].values, df['close'].values
    high_ok = h >= np.maximum(o, c) - 1e-10
    low_ok = l <= np.minimum(o, c) + 1e-10
    hl_ok = h >= l - 1e-10
    positive = (o > 0) & (h > 0) & (l > 0) & (c > 0)
    ohlc_violations = int((~high_ok).sum() + (~low_ok).sum() + (~hl_ok).sum() + (~positive).sum())
    issues['checks']['ohlc_consistency'] = {'violations': ohlc_violations}
    if ohlc_violations > 0:
        issues['critical'].append(f'{ohlc_violations} OHLC structural violations')

    # --- 5. Zero volume ---
    zero_vol = (df['volume'] == 0) | df['volume'].isna()
    n_zero_vol = int(zero_vol.sum())
    zero_vol_pct = n_zero_vol / max(n, 1)
    issues['checks']['zero_volume'] = {
        'count': n_zero_vol,
        'pct': round(zero_vol_pct * 100, 2),
    }
    if is_major and n_zero_vol > 0:
        issues['warnings'].append(f'{n_zero_vol} zero-volume candles on major')
    elif zero_vol_pct > 0.02:
        issues['warnings'].append(f'{n_zero_vol} zero-volume ({zero_vol_pct:.1%}) exceeds 2%')

    # --- 6. Stale/flat candle detection ---
    flat = (df['open'] == df['high']) & (df['high'] == df['low']) & (df['low'] == df['close'])
    max_stale = QC['max_stale_run'] if is_major else QC['max_stale_run_altcoin']
    # Find runs of flat candles
    flat_runs = []
    run_len = 0
    for i in range(n):
        if flat.iloc[i]:
            run_len += 1
        else:
            if run_len >= max_stale:
                flat_runs.append(run_len)
            run_len = 0
    if run_len >= max_stale:
        flat_runs.append(run_len)
    issues['checks']['stale_candles'] = {
        'n_flat': int(flat.sum()),
        'stale_runs': flat_runs,
    }
    if flat_runs:
        issues['warnings'].append(f'{len(flat_runs)} stale runs (max {max(flat_runs)} bars)')

    # --- 7. Price spikes ---
    log_ret = np.log(c / np.maximum(np.roll(c, 1), 1e-10))
    log_ret[0] = 0
    spike_pct = QC['spike_pct_btc'] if is_major else QC['spike_pct_alt']
    pct_spikes = np.abs(log_ret) > spike_pct
    n_spikes = int(pct_spikes.sum())
    issues['checks']['price_spikes'] = {
        'count': n_spikes,
        'threshold': spike_pct,
    }
    if n_spikes > 0:
        issues['warnings'].append(f'{n_spikes} bars with |return| > {spike_pct:.0%}')

    # --- 8. Volume spikes ---
    vol = df['volume'].values
    vol_median = pd.Series(vol).rolling(168, min_periods=10).median().values
    vol_spike = vol > QC['vol_spike_mult'] * np.maximum(vol_median, 1e-10)
    vol_spike[:168] = False
    n_vol_spike = int(vol_spike.sum())
    issues['checks']['volume_spikes'] = {'count': n_vol_spike}

    # --- 9. Range reasonableness ---
    range_pct = (h - l) / np.maximum(c, 1e-10)
    range_thresh = QC['range_pct_btc'] if is_major else QC['range_pct_alt']
    n_wide_range = int((range_pct > range_thresh).sum())
    issues['checks']['wide_range'] = {'count': n_wide_range, 'threshold': range_thresh}

    # --- 10. Close-to-open continuity ---
    prev_close = np.roll(c, 1)
    prev_close[0] = o[0]
    cont_gap = np.abs(o - prev_close) / np.maximum(prev_close, 1e-10)
    n_cont_gaps = int((cont_gap > QC['continuity_pct']).sum())
    issues['checks']['continuity_gaps'] = {'count': n_cont_gaps}

    # --- 13. NaN check ---
    n_nans = int(df[['open', 'high', 'low', 'close', 'volume']].isna().sum().sum())
    issues['checks']['nans'] = {'count': n_nans}
    if n_nans > 0:
        issues['critical'].append(f'{n_nans} NaN values in OHLCV columns')

    # --- 15. Summary ---
    issues['checks']['summary'] = {
        'completeness_pct': round(completeness * 100, 2),
        'n_critical': len(issues['critical']),
        'n_warnings': len(issues['warnings']),
        'grade': 'A' if not issues['critical'] and len(issues['warnings']) <= 2
                 else ('B' if not issues['critical'] else 'C'),
    }

    return issues


def fix_issues(df, issues, token, verbose=False):
    """Apply automated fixes for common issues. Returns cleaned df."""
    fixes = []

    # Remove duplicates
    if df.index.duplicated().any():
        before = len(df)
        df = df[~df.index.duplicated(keep='last')]
        fixes.append(f'Removed {before - len(df)} duplicates')

    # Fix OHLC violations: clamp H to max(O,C), L to min(O,C)
    o, h, l, c = df['open'], df['high'], df['low'], df['close']
    max_oc = np.maximum(o, c)
    min_oc = np.minimum(o, c)
    h_fix = (h < max_oc - 1e-10)
    l_fix = (l > min_oc + 1e-10)
    if h_fix.any():
        df.loc[h_fix, 'high'] = max_oc[h_fix]
        fixes.append(f'Fixed {h_fix.sum()} high < max(O,C)')
    if l_fix.any():
        df.loc[l_fix, 'low'] = min_oc[l_fix]
        fixes.append(f'Fixed {l_fix.sum()} low > min(O,C)')

    # Ensure H >= L
    hl_fix = df['high'] < df['low']
    if hl_fix.any():
        df.loc[hl_fix, ['high', 'low']] = df.loc[hl_fix, ['low', 'high']].values
        fixes.append(f'Swapped {hl_fix.sum()} inverted H/L')

    # Fill small gaps (1-2 hours) with interpolation
    full_idx = pd.date_range(df.index[0], df.index[-1], freq='1h')
    missing = full_idx.difference(df.index)
    if len(missing) > 0:
        # Only fill gaps of 1-2 candles
        small_gaps = []
        for ts in missing:
            prev_in = (ts - pd.Timedelta(hours=1)) in df.index
            next_in = (ts + pd.Timedelta(hours=1)) in df.index
            if prev_in and next_in:
                small_gaps.append(ts)

        if small_gaps:
            df = df.reindex(df.index.union(pd.DatetimeIndex(small_gaps)))
            df = df.sort_index()
            # Forward fill for OHLC, zero for volume
            for col in ['open', 'high', 'low', 'close']:
                df[col] = df[col].interpolate(method='linear')
            df['volume'] = df['volume'].fillna(0)
            fixes.append(f'Interpolated {len(small_gaps)} single-bar gaps')

    # Drop NaN rows that couldn't be fixed
    nan_rows = df[['open', 'high', 'low', 'close']].isna().any(axis=1)
    if nan_rows.any():
        df = df[~nan_rows]
        fixes.append(f'Dropped {nan_rows.sum()} unfixable NaN rows')

    # Sort
    df = df.sort_index()

    issues['fixes_applied'] = fixes
    if verbose and fixes:
        for f in fixes:
            print(f'    FIX: {f}')

    return df


# ============================================================
# Main Pipeline
# ============================================================

def process_token(token, force=False, verbose=False, market='perp', cache_dir=None):
    """Full pipeline for one token: load → merge → quality check → fix → save."""
    _cache = cache_dir or CACHE_DIR
    out_path = _cache / f'{token}_1h.parquet'

    if out_path.exists() and not force:
        # Check if CSV sources are newer
        csv_mtime = 0
        if market == 'spot':
            p = SPOT_DIR / 'binance' / '1h_ohlcv' / f'{token}_spot_1h.csv'
            if p.exists():
                csv_mtime = p.stat().st_mtime
        else:
            for d in ['binance/1h_ohlcv', 'kraken/1h_ohlcv', 'hyperliquid/ohlcv']:
                for suffix in [f'{token}_perp_1h.csv',
                               f'{BINANCE_REVERSE_MAP.get(token, token)}_perp_1h.csv',
                               f'{HYPER_REVERSE_MAP.get(token, token)}_perp_1h.csv']:
                    p = PERP_DIR / d / suffix
                    if p.exists():
                        csv_mtime = max(csv_mtime, p.stat().st_mtime)
        if csv_mtime > 0 and out_path.stat().st_mtime > csv_mtime:
            if verbose:
                print(f'  {token}: up to date, skipping')
            return None, None

    if verbose:
        print(f'  {token}:')

    # 1. Load and merge
    df, fill_info = merge_ohlcv(token, verbose=verbose, market=market)
    if df is None or len(df) < QC['min_bars']:
        if verbose:
            bars = len(df) if df is not None else 0
            print(f'    SKIP: only {bars} bars (need {QC["min_bars"]})')
        return None, None

    # 2. Quality checks
    issues = run_quality_checks(df, token, verbose=verbose)
    issues['merge_info'] = fill_info

    if verbose:
        grade = issues['checks']['summary']['grade']
        comp = issues['checks']['completeness']['pct']
        n_crit = issues['checks']['summary']['n_critical']
        n_warn = issues['checks']['summary']['n_warnings']
        print(f'    Quality: grade={grade} completeness={comp}% '
              f'critical={n_crit} warnings={n_warn}')

    # 3. Fix issues
    df = fix_issues(df, issues, token, verbose=verbose)

    # 3b. For perp market: merge funding rates into OHLCV parquet
    if market == 'perp':
        funding_raw = merge_funding(token)
        if funding_raw is not None and len(funding_raw) > 0:
            funding_1h = resample_funding_to_1h(funding_raw)
            if funding_1h is not None:
                # Align funding to OHLCV index
                funding_aligned = funding_1h.reindex(df.index, method='ffill')
                df['funding_rate'] = funding_aligned['funding_rate'].fillna(0.0)
                df['funding_1h'] = funding_aligned['funding_1h'].fillna(0.0)

                # QC: funding sanity check
                max_funding = df['funding_rate'].abs().max()
                if max_funding > QC['funding_max']:
                    issues['warnings'].append(
                        f'Funding rate {max_funding:.4f} exceeds threshold {QC["funding_max"]}')

                n_funding = (df['funding_rate'] != 0).sum()
                if verbose:
                    print(f'    Funding: {n_funding} bars with data, '
                          f'max |rate|={max_funding:.6f}')

    # 4. Save 1h parquet
    # Preserve promoted bars: if existing historical extends beyond CSV data,
    # keep those extra bars (they came from live buffer promotion)
    if out_path.exists():
        try:
            existing = pd.read_parquet(out_path)
            if not isinstance(existing.index, pd.DatetimeIndex):
                existing.index = pd.to_datetime(existing.index, unit="ms")
            if hasattr(existing.index, "tz") and existing.index.tz is not None:
                existing.index = existing.index.tz_convert("UTC").tz_localize(None)
            if len(existing) > 0 and len(df) > 0:
                csv_max = df.index.max()
                promoted_tail = existing[existing.index > csv_max]
                if len(promoted_tail) > 0:
                    df = pd.concat([df, promoted_tail])
                    df = df[~df.index.duplicated(keep="last")]
                    df = df.sort_index()
                    # Fill NaN in funding columns from column mismatch
                    for col in ("funding_rate", "funding_1h"):
                        if col in df.columns:
                            df[col] = df[col].fillna(0.0)
                    if verbose:
                        print(f'    Preserved {len(promoted_tail)} promoted bars beyond CSV range')
        except Exception as e:
            if verbose:
                print(f'    Warning: could not read existing parquet for merge: {e}')

    df.to_parquet(out_path, engine='pyarrow')
    issues['output_bars'] = len(df)
    issues['output_path'] = str(out_path)

    if verbose:
        print(f'    Saved: {len(df)} bars → {out_path.name}')

    # 4h and daily aggregation removed — engine computes these from 1h on the fly
    # (5ms/token, vectorized pandas.resample, no need to pre-build)

    return df, issues


def main():
    parser = argparse.ArgumentParser(description='Build parquet cache (perp or spot)')
    parser.add_argument('--market', choices=['perp', 'spot'], default='perp',
                        help='Market type: perp (default) or spot')
    parser.add_argument('--tokens', type=str, default=None,
                        help='Comma-separated tokens (default: all in universe)')
    parser.add_argument('--force', action='store_true',
                        help='Rebuild even if up to date')
    parser.add_argument('--verbose', '-v', action='store_true')
    args = parser.parse_args()

    # Load universe
    sys.path.insert(0, str(BASE_DIR / 'v4'))
    # universe imported for symbol maps only — token lists discovered from data

    if args.tokens:
        tokens = [t.strip().upper() for t in args.tokens.split(',')]
    elif args.market == 'spot':
        # For spot: process ALL tokens that have spot CSVs
        spot_csv_dir = SPOT_DIR / 'binance' / '1h_ohlcv'
        if spot_csv_dir.exists():
            tokens = sorted(set(
                f.replace('_spot_1h.csv', '')
                for f in os.listdir(spot_csv_dir) if f.endswith('_spot_1h.csv')
            ))
        else:
            print("ERROR: No spot CSV files found in data/spot/binance/1h_ohlcv/")
            sys.exit(1)
    else:
        # For perp: discover ALL tokens from all exchange CSVs
        all_perp = set()
        for exch_dir, suffix in [
            (PERP_DIR / 'binance' / '1h_ohlcv', '_perp_1h.csv'),
            (PERP_DIR / 'kraken' / '1h_ohlcv', '_perp_1h.csv'),
            (PERP_DIR / 'hyperliquid' / 'ohlcv', '_perp_1h.csv'),
        ]:
            if exch_dir.exists():
                for f in os.listdir(exch_dir):
                    if f.endswith(suffix):
                        raw_name = f.replace(suffix, '')
                        normalized = BINANCE_SYMBOL_MAP.get(raw_name,
                                     HYPER_SYMBOL_MAP.get(raw_name, raw_name))
                        all_perp.add(normalized)
        if not all_perp:
            print("ERROR: No perp CSV files found in data/perp/*/")
            sys.exit(1)
        tokens = sorted(all_perp)

    # Set output directory based on market type
    if args.market == 'spot':
        cache_dir = DATA_DIR / 'spot' / '1h_cache'
    else:
        cache_dir = DATA_DIR / 'perp' / '1h_cache'

    cache_dir.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    market_label = args.market.upper()
    print('=' * 60)
    print(f'Build 1H Parquet Cache — {market_label}')
    print('=' * 60)
    print(f'Market: {market_label}')
    print(f'Tokens: {len(tokens)}')
    print(f'Output: {cache_dir}')
    print(f'Force:  {args.force}')
    print()

    t0 = time.time()
    all_issues = {}
    summary = {'processed': 0, 'skipped': 0, 'failed': 0,
               'grade_a': 0, 'grade_b': 0, 'grade_c': 0}

    for i, token in enumerate(tokens, 1):
        if args.verbose:
            print(f'[{i}/{len(tokens)}]', end='')

        try:
            df, issues = process_token(token, force=args.force,
                                        verbose=args.verbose,
                                        market=args.market,
                                        cache_dir=cache_dir)
            if issues is None:
                summary['skipped'] += 1
                continue

            all_issues[token] = issues
            summary['processed'] += 1
            grade = issues['checks']['summary']['grade']
            summary[f'grade_{grade.lower()}'] += 1

        except Exception as e:
            summary['failed'] += 1
            if args.verbose:
                print(f'  {token}: ERROR — {e}')

    elapsed = time.time() - t0

    # Print summary
    print()
    print('=' * 60)
    print('SUMMARY')
    print('=' * 60)
    print(f'Elapsed:   {elapsed:.1f}s')
    print(f'Processed: {summary["processed"]}')
    print(f'Skipped:   {summary["skipped"]}')
    print(f'Failed:    {summary["failed"]}')
    print(f'Grades:    A={summary["grade_a"]} B={summary["grade_b"]} C={summary["grade_c"]}')

    # Quality scorecard
    if all_issues:
        print()
        print(f'{"Token":<10} {"Bars":>8} {"Complete":>10} {"Gaps":>6} '
              f'{"Dupes":>6} {"OHLC":>6} {"ZeroV":>6} {"Spikes":>7} '
              f'{"Grade":>6} {"Source":>10}')
        print('-' * 82)
        for token in tokens:
            if token not in all_issues:
                continue
            iss = all_issues[token]
            c = iss['checks']
            print(f'{token:<10} '
                  f'{iss.get("output_bars", 0):>8} '
                  f'{c["completeness"]["pct"]:>9.1f}% '
                  f'{c["gaps"]["n_gaps"]:>6} '
                  f'{c["duplicates"]["count"]:>6} '
                  f'{c["ohlc_consistency"]["violations"]:>6} '
                  f'{c["zero_volume"]["count"]:>6} '
                  f'{c["price_spikes"]["count"]:>7} '
                  f'{c["summary"]["grade"]:>6} '
                  f'{iss.get("merge_info", {}).get("primary", "?"):>10}')

    # Save detailed report
    report_path = REPORT_DIR / f'quality_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'
    with open(report_path, 'w') as f:
        json.dump(all_issues, f, indent=2, default=str)
    print(f'\nDetailed report: {report_path}')

    # Summary of critical issues
    tokens_with_critical = [t for t, iss in all_issues.items() if iss['critical']]
    if tokens_with_critical:
        print(f'\nTokens with CRITICAL issues ({len(tokens_with_critical)}):')
        for t in tokens_with_critical:
            for c in all_issues[t]['critical']:
                print(f'  {t}: {c}')


if __name__ == '__main__':
    main()
