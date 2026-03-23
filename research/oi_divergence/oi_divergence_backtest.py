#!/workspace/venv/bin/python
"""
OI Divergence Signal Backtest
=============================

Hypothesis: When OI rises while price drops (or stays flat), new positions
are being built. If those are mostly longs accumulating at lower prices,
the eventual unwind creates upside.

Signals:
  A) OI Up / Price Down divergence (>5%)
  B) OI Acceleration with price confirmation
  C) OI Z-Score extreme with price confirmation

Train: before 2025-07-01
Test:  after  2025-07-01
Kill metric: avg forward 24h return > +0.5% with >50 occurrences OOS

Data: Bybit OI (hourly) + Binance perp price (hourly, from 1h_cache)
"""

import os
import sys
import warnings
import numpy as np
import pandas as pd
from datetime import datetime, timezone

warnings.filterwarnings('ignore')

# ── Paths ───────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.join(BASE_DIR, '..', '..')
OI_DIR = os.path.join(PROJECT_DIR, 'data', 'perp', 'bybit_oi')
PRICE_DIR = os.path.join(PROJECT_DIR, 'data', 'perp', '1h_cache')

TOKENS = [
    'BTC', 'ETH', 'SOL', 'XRP', 'DOGE',
    'ADA', 'AVAX', 'LINK', 'DOT',
    'ARB', 'OP', 'SUI', 'APT', 'NEAR',
]

TRAIN_END = pd.Timestamp('2025-07-01', tz='UTC')
FWD_HORIZONS = [4, 8, 24]  # hours


# ── Data Loading ────────────────────────────────────────────────────────
def load_token_data(token: str) -> pd.DataFrame | None:
    """Load and merge OI + price data for a single token."""
    oi_path = os.path.join(OI_DIR, f'{token}_oi_1h.csv')
    price_path = os.path.join(PRICE_DIR, f'{token}_1h.parquet')

    if not os.path.exists(oi_path):
        print(f'  [{token}] OI file not found, skipping')
        return None
    if not os.path.exists(price_path):
        print(f'  [{token}] Price file not found, skipping')
        return None

    # Load OI
    oi_df = pd.read_csv(oi_path)
    oi_df['datetime'] = pd.to_datetime(oi_df['datetime'], utc=True)
    oi_df = oi_df.set_index('datetime')[['open_interest']].sort_index()
    oi_df = oi_df[~oi_df.index.duplicated(keep='first')]

    # Load price
    price_df = pd.read_parquet(price_path)
    if price_df.index.tz is None:
        price_df.index = price_df.index.tz_localize('UTC')
    price_df = price_df[['close', 'volume']].sort_index()
    price_df = price_df[~price_df.index.duplicated(keep='first')]

    # Merge on hourly timestamps (inner join)
    merged = oi_df.join(price_df, how='inner')
    merged = merged.dropna()

    if len(merged) < 500:
        print(f'  [{token}] only {len(merged)} merged rows, skipping')
        return None

    merged['token'] = token
    return merged


def load_all_data() -> pd.DataFrame:
    """Load all token data and concatenate."""
    frames = []
    for token in TOKENS:
        df = load_token_data(token)
        if df is not None:
            frames.append(df)
            first, last = df.index.min(), df.index.max()
            print(f'  [{token}] {len(df):,} rows | {first.date()} to {last.date()}')

    if not frames:
        print('ERROR: No data loaded!')
        sys.exit(1)

    all_data = pd.concat(frames)
    print(f'\nTotal: {len(all_data):,} rows across {len(frames)} tokens')
    return all_data


# ── Signal Construction ─────────────────────────────────────────────────
def compute_signals(df: pd.DataFrame) -> pd.DataFrame:
    """Compute all OI divergence signals per token group."""
    results = []

    for token, gdf in df.groupby('token'):
        gdf = gdf.sort_index().copy()

        # ── Base changes ──
        gdf['oi_change_24h'] = gdf['open_interest'].pct_change(24)
        gdf['oi_change_4h'] = gdf['open_interest'].pct_change(4)
        gdf['price_change_24h'] = gdf['close'].pct_change(24)
        gdf['price_change_4h'] = gdf['close'].pct_change(4)

        # ── Forward returns (what we're predicting) ──
        for h in FWD_HORIZONS:
            gdf[f'fwd_ret_{h}h'] = gdf['close'].shift(-h) / gdf['close'] - 1

        # ── Signal A: OI Up / Price Down divergence ──
        gdf['divergence'] = gdf['oi_change_24h'] - gdf['price_change_24h']
        gdf['signal_A'] = (
            (gdf['divergence'] > 0.05) &        # OI growing 5%+ faster than price
            (gdf['oi_change_24h'] > 0) &         # OI actually rising
            (gdf['price_change_24h'] < 0.01)     # price flat or down
        )

        # ── Signal B: OI Acceleration ──
        oi_accel = gdf['oi_change_4h'] - (gdf['oi_change_24h'] / 6)
        gdf['oi_accel'] = oi_accel
        gdf['signal_B'] = (
            (oi_accel > 0) &                     # OI accelerating
            (gdf['price_change_4h'] > 0) &       # price confirming
            (gdf['oi_change_4h'] > 0.01)         # meaningful OI move
        )

        # ── Signal C: OI Z-Score extreme ──
        oi_change_1h = gdf['open_interest'].pct_change(1)
        oi_mean = oi_change_1h.rolling(168, min_periods=48).mean()
        oi_std = oi_change_1h.rolling(168, min_periods=48).std()
        gdf['oi_zscore'] = (oi_change_1h - oi_mean) / oi_std.replace(0, np.nan)
        gdf['signal_C'] = (
            (gdf['oi_zscore'] > 2.0) &           # OI spike
            (gdf['price_change_4h'] > 0)          # price confirming
        )

        results.append(gdf)

    return pd.concat(results)


# ── Evaluation ──────────────────────────────────────────────────────────
def evaluate_signal(df: pd.DataFrame, signal_col: str, label: str, split: str) -> dict:
    """Evaluate a single signal: avg forward return, count, t-stat."""
    mask = df[signal_col]
    sig_df = df[mask].copy()
    n = len(sig_df)

    result = {
        'signal': label,
        'split': split,
        'count': n,
    }

    for h in FWD_HORIZONS:
        col = f'fwd_ret_{h}h'
        if n > 0:
            rets = sig_df[col].dropna()
            result[f'avg_ret_{h}h'] = rets.mean() * 100  # percentage
            result[f'med_ret_{h}h'] = rets.median() * 100
            result[f'win_rate_{h}h'] = (rets > 0).mean() * 100
            if len(rets) > 1 and rets.std() > 0:
                result[f'tstat_{h}h'] = (rets.mean() / rets.std()) * np.sqrt(len(rets))
            else:
                result[f'tstat_{h}h'] = 0.0
        else:
            result[f'avg_ret_{h}h'] = np.nan
            result[f'med_ret_{h}h'] = np.nan
            result[f'win_rate_{h}h'] = np.nan
            result[f'tstat_{h}h'] = np.nan

    # Base rate for comparison
    for h in FWD_HORIZONS:
        col = f'fwd_ret_{h}h'
        base_rets = df[col].dropna()
        result[f'base_rate_{h}h'] = base_rets.mean() * 100

    return result


def monthly_breakdown(df: pd.DataFrame, signal_col: str, label: str) -> pd.DataFrame:
    """Per-month breakdown of signal performance in OOS period."""
    oos = df[df.index >= TRAIN_END].copy()
    oos['month'] = oos.index.to_period('M')

    rows = []
    for month, mdf in oos.groupby('month'):
        mask = mdf[signal_col]
        sig = mdf[mask]
        n = len(sig)

        row = {'month': str(month), 'count': n}
        for h in FWD_HORIZONS:
            col = f'fwd_ret_{h}h'
            if n > 0:
                rets = sig[col].dropna()
                row[f'avg_ret_{h}h'] = rets.mean() * 100 if len(rets) > 0 else np.nan
                row[f'win_rate_{h}h'] = (rets > 0).mean() * 100 if len(rets) > 0 else np.nan
            else:
                row[f'avg_ret_{h}h'] = np.nan
                row[f'win_rate_{h}h'] = np.nan
        rows.append(row)

    return pd.DataFrame(rows)


def per_token_breakdown(df: pd.DataFrame, signal_col: str) -> pd.DataFrame:
    """Per-token breakdown in OOS period."""
    oos = df[df.index >= TRAIN_END].copy()

    rows = []
    for token, tdf in oos.groupby('token'):
        mask = tdf[signal_col]
        sig = tdf[mask]
        n = len(sig)

        row = {'token': token, 'count': n}
        for h in FWD_HORIZONS:
            col = f'fwd_ret_{h}h'
            if n > 0:
                rets = sig[col].dropna()
                row[f'avg_ret_{h}h'] = rets.mean() * 100 if len(rets) > 0 else np.nan
            else:
                row[f'avg_ret_{h}h'] = np.nan
        rows.append(row)

    return pd.DataFrame(rows)


# ── Main ────────────────────────────────────────────────────────────────
def main():
    print('='*70)
    print('OI DIVERGENCE SIGNAL BACKTEST')
    print('='*70)

    # ── Step 1: Load data ──
    print('\n── Loading data ──')
    df = load_all_data()

    # ── Step 2: Compute signals ──
    print('\n── Computing signals ──')
    df = compute_signals(df)

    # ── Split ──
    train = df[df.index < TRAIN_END]
    test = df[df.index >= TRAIN_END]
    print(f'\nTrain: {len(train):,} rows | {train.index.min().date()} to {train.index.max().date()}')
    print(f'Test:  {len(test):,} rows | {test.index.min().date()} to {test.index.max().date()}')

    # ── Signal counts ──
    signals = [
        ('signal_A', 'A: OI Up/Price Down'),
        ('signal_B', 'B: OI Acceleration'),
        ('signal_C', 'C: OI Z-Score Extreme'),
    ]

    for sig_col, sig_label in signals:
        train_n = train[sig_col].sum()
        test_n = test[sig_col].sum()
        print(f'  {sig_label}: Train={train_n}, Test={test_n}')

    # ── Step 3: Evaluate each signal ──
    print('\n' + '='*70)
    print('SIGNAL EVALUATION — IN-SAMPLE (Train)')
    print('='*70)

    all_results = []
    for sig_col, sig_label in signals:
        res_train = evaluate_signal(train, sig_col, sig_label, 'train')
        all_results.append(res_train)

    res_df = pd.DataFrame(all_results)
    for _, row in res_df.iterrows():
        print(f'\n  {row["signal"]} ({row["split"]}, n={row["count"]})')
        for h in FWD_HORIZONS:
            avg = row.get(f'avg_ret_{h}h', np.nan)
            med = row.get(f'med_ret_{h}h', np.nan)
            wr = row.get(f'win_rate_{h}h', np.nan)
            ts = row.get(f'tstat_{h}h', np.nan)
            base = row.get(f'base_rate_{h}h', np.nan)
            print(f'    {h:2d}h: avg={avg:+.3f}% | med={med:+.3f}% | '
                  f'win={wr:.1f}% | t={ts:.2f} | base={base:+.3f}%')

    print('\n' + '='*70)
    print('SIGNAL EVALUATION — OUT-OF-SAMPLE (Test)')
    print('='*70)

    oos_results = []
    for sig_col, sig_label in signals:
        res_test = evaluate_signal(test, sig_col, sig_label, 'test')
        oos_results.append(res_test)

    oos_df = pd.DataFrame(oos_results)
    for _, row in oos_df.iterrows():
        print(f'\n  {row["signal"]} ({row["split"]}, n={row["count"]})')
        for h in FWD_HORIZONS:
            avg = row.get(f'avg_ret_{h}h', np.nan)
            med = row.get(f'med_ret_{h}h', np.nan)
            wr = row.get(f'win_rate_{h}h', np.nan)
            ts = row.get(f'tstat_{h}h', np.nan)
            base = row.get(f'base_rate_{h}h', np.nan)
            print(f'    {h:2d}h: avg={avg:+.3f}% | med={med:+.3f}% | '
                  f'win={wr:.1f}% | t={ts:.2f} | base={base:+.3f}%')

    # ── Step 4: Per-month breakdown (OOS) ──
    print('\n' + '='*70)
    print('PER-MONTH BREAKDOWN — OOS')
    print('='*70)

    for sig_col, sig_label in signals:
        print(f'\n  {sig_label}')
        monthly = monthly_breakdown(df, sig_col, sig_label)
        if len(monthly) == 0:
            print('    No data')
            continue
        print(f'    {"Month":>10s}  {"N":>5s}  ', end='')
        for h in FWD_HORIZONS:
            print(f'{"avg_"+str(h)+"h":>10s}  {"wr_"+str(h)+"h":>8s}  ', end='')
        print()
        for _, row in monthly.iterrows():
            print(f'    {row["month"]:>10s}  {row["count"]:5d}  ', end='')
            for h in FWD_HORIZONS:
                avg = row.get(f'avg_ret_{h}h', np.nan)
                wr = row.get(f'win_rate_{h}h', np.nan)
                avg_s = f'{avg:+.3f}%' if not np.isnan(avg) else '    N/A'
                wr_s = f'{wr:.1f}%' if not np.isnan(wr) else '  N/A'
                print(f'{avg_s:>10s}  {wr_s:>8s}  ', end='')
            print()

    # ── Step 5: Per-token breakdown (OOS) ──
    print('\n' + '='*70)
    print('PER-TOKEN BREAKDOWN — OOS (24h forward return)')
    print('='*70)

    for sig_col, sig_label in signals:
        print(f'\n  {sig_label}')
        token_df = per_token_breakdown(df, sig_col)
        print(f'    {"Token":>8s}  {"N":>5s}  ', end='')
        for h in FWD_HORIZONS:
            print(f'{"avg_"+str(h)+"h":>10s}  ', end='')
        print()
        for _, row in token_df.sort_values('count', ascending=False).iterrows():
            print(f'    {row["token"]:>8s}  {row["count"]:5d}  ', end='')
            for h in FWD_HORIZONS:
                avg = row.get(f'avg_ret_{h}h', np.nan)
                avg_s = f'{avg:+.3f}%' if not np.isnan(avg) else '    N/A'
                print(f'{avg_s:>10s}  ', end='')
            print()

    # ── Step 6: Verdicts ──
    print('\n' + '='*70)
    print('VERDICTS')
    print('='*70)
    print(f'Kill metric: avg forward 24h return > +0.50% with > 50 occurrences OOS')
    print()

    for _, row in oos_df.iterrows():
        avg_24 = row.get('avg_ret_24h', np.nan)
        n = row['count']
        sig = row['signal']

        if np.isnan(avg_24):
            verdict = 'NO EDGE (insufficient data)'
        elif n < 50:
            verdict = f'NO EDGE (only {n} occurrences, need 50+)'
        elif avg_24 > 0.50:
            verdict = f'EDGE (avg 24h = {avg_24:+.3f}%, n={n})'
        elif avg_24 > 0.20:
            verdict = f'MARGINAL (avg 24h = {avg_24:+.3f}%, n={n})'
        else:
            verdict = f'NO EDGE (avg 24h = {avg_24:+.3f}%, n={n})'

        print(f'  {sig}: {verdict}')

    # ── Excess over base rate ──
    print('\n  Excess returns over base rate (OOS):')
    for _, row in oos_df.iterrows():
        for h in FWD_HORIZONS:
            avg = row.get(f'avg_ret_{h}h', np.nan)
            base = row.get(f'base_rate_{h}h', np.nan)
            if not np.isnan(avg) and not np.isnan(base):
                excess = avg - base
                print(f'    {row["signal"]} {h}h: {excess:+.3f}% excess')

    print('\n' + '='*70)


if __name__ == '__main__':
    main()
