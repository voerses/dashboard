"""
Binance Vision 1-Minute Data Downloader + Daily Feature Aggregator

Downloads 1-min klines from data.binance.vision (free, no API key, no rate limits),
aggregates into enriched daily features, and merges into Parquet store.

Pipeline:
  1. Download monthly ZIP files (1-min OHLCV) per token
  2. Process each month: compute intraday features → aggregate to daily
  3. Merge enriched daily features with existing daily OHLCV
  4. Save to single Parquet file for the fast WFO engine

Features extracted from 1-min data (not possible with daily bars):
  - realized_var: sum of squared 1-min log returns (true intraday volatility)
  - vpin: Volume-Synchronized Probability of Informed Trading (real, not approximated)
  - taker_buy_ratio: fraction of volume from taker buys (order flow)
  - trade_count: number of trades per day (activity intensity)
  - volume_concentration: Herfindahl index of hourly volume (distribution shape)
  - close_to_close_vol vs realized_vol: vol ratio (overnight vs intraday gap)
  - parkinson_vol: (log(H/L))^2 / (4*ln2) from 1-min bars (better than daily)
  - amihud_1m: intraday Amihud illiquidity from 1-min bars
  - max_drawdown_intraday: worst intraday peak-to-trough (crash detection)
  - vwap_deviation: close vs VWAP (institutional flow signal)
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import numpy as np
import pandas as pd
import polars as pl
import urllib.request
import zipfile
import io
import time
from pathlib import Path
from datetime import datetime, timedelta
from joblib import Parallel, delayed

from liquid_universe import LIQUID_TOKENS


# Tokens confirmed available on Binance Vision (tested)
BINANCE_VISION_TOKENS = [
    'BTC', 'ETH', 'SOL', 'XRP', 'PAXG', 'DOGE', 'BNB', 'SUI',
    'ADA', 'ZEC', 'PEPE', 'TRX', 'LINK', 'AVAX', 'DOT', 'LTC',
    'NEAR', 'TAO', 'BCH', 'UNI', 'ALICE', 'APT', 'ICP', 'ARB',
    'FIL', 'ENA', 'ZRO', 'AAVE', 'HBAR', 'PENGU', 'DASH', 'WLD',
    'SHIB', 'WIF', 'XLM', 'OM', 'TRUMP', 'TON', 'PENDLE', 'SEI',
    'BONK', 'FET', 'DENT', 'OP', 'CHZ', 'FLOKI', 'INJ', 'CAKE', 'POL',
]

# Tokens too new for Binance Vision — keep daily-only data
NO_1M_DATA = [t for t in LIQUID_TOKENS if t not in BINANCE_VISION_TOKENS]

BASE_URL = "https://data.binance.vision/data/spot/monthly/klines"


def _download_month(symbol, year, month):
    """Download one month of 1-min data. Returns DataFrame or None."""
    url = f"{BASE_URL}/{symbol}USDT/1m/{symbol}USDT-1m-{year}-{month:02d}.zip"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        data = resp.read()
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            csv_name = zf.namelist()[0]
            with zf.open(csv_name) as f:
                df = pd.read_csv(f, header=None, names=[
                    'open_time', 'open', 'high', 'low', 'close', 'volume',
                    'close_time', 'quote_volume', 'trades', 'taker_buy_base',
                    'taker_buy_quote', 'ignore'
                ])
                for col in ['open', 'high', 'low', 'close', 'volume',
                           'quote_volume', 'taker_buy_base', 'taker_buy_quote']:
                    df[col] = df[col].astype(float)
                df['trades'] = df['trades'].astype(int)

                # Handle timestamp format (microseconds since Jan 2025)
                if df['open_time'].iloc[0] > 1e15:
                    df['datetime'] = pd.to_datetime(df['open_time'], unit='us')
                else:
                    df['datetime'] = pd.to_datetime(df['open_time'], unit='ms')

                df.set_index('datetime', inplace=True)
                return df
    except Exception:
        return None


def aggregate_1m_to_daily(df_1m):
    """
    Aggregate 1-minute bars into enriched daily features.
    This is where the magic happens — extracting signals impossible from daily data.
    """
    df_1m = df_1m.copy()
    df_1m['date'] = df_1m.index.date
    df_1m['hour'] = df_1m.index.hour
    df_1m['log_ret'] = np.log(df_1m['close'] / df_1m['close'].shift(1))

    daily_groups = df_1m.groupby('date')
    records = []

    for date, g in daily_groups:
        if len(g) < 60:  # need at least 1 hour of data
            continue

        close = g['close'].values
        high = g['high'].values
        low = g['low'].values
        volume = g['volume'].values
        quote_vol = g['quote_volume'].values
        log_rets = g['log_ret'].dropna().values
        trades = g['trades'].values
        taker_buy = g['taker_buy_base'].values

        rec = {'date': pd.Timestamp(date)}

        # --- Standard OHLCV (from 1-min bars) ---
        rec['open_1m'] = g['open'].iloc[0]
        rec['high_1m'] = high.max()
        rec['low_1m'] = low.min()
        rec['close_1m'] = close[-1]
        rec['volume_1m'] = volume.sum()
        rec['quote_volume'] = quote_vol.sum()

        # --- Realized Variance (sum of squared 1-min returns) ---
        if len(log_rets) > 10:
            rec['realized_var'] = np.sum(log_rets ** 2)
            rec['realized_vol'] = np.sqrt(rec['realized_var'] * 252)  # annualized
        else:
            rec['realized_var'] = 0
            rec['realized_vol'] = 0

        # --- VPIN (Volume-Synchronized Probability of Informed Trading) ---
        # Real VPIN from 1-min data using Bulk Volume Classification
        if len(log_rets) > 50:
            sigma = np.std(log_rets) + 1e-10
            z = log_rets / sigma
            from scipy.stats import norm
            buy_frac = norm.cdf(z)
            buy_vol = volume[1:len(log_rets)+1] * buy_frac if len(volume) > len(log_rets) else volume[:len(log_rets)] * buy_frac
            sell_vol = volume[1:len(log_rets)+1] * (1 - buy_frac) if len(volume) > len(log_rets) else volume[:len(log_rets)] * (1 - buy_frac)

            # Bucket into ~50 volume buckets
            total_vol = volume.sum()
            bucket_size = max(total_vol / 50, 1)

            cum_buy = np.cumsum(buy_vol)
            cum_sell = np.cumsum(sell_vol)
            cum_total = cum_buy + cum_sell

            n_buckets = max(int(cum_total[-1] / bucket_size), 1) if len(cum_total) > 0 else 1
            bucket_imbalances = []

            for b in range(min(n_buckets, 50)):
                start_vol = b * bucket_size
                end_vol = (b + 1) * bucket_size
                mask = (cum_total >= start_vol) & (cum_total < end_vol)
                if mask.sum() > 0:
                    b_buy = buy_vol[mask].sum()
                    b_sell = sell_vol[mask].sum()
                    bucket_imbalances.append(abs(b_buy - b_sell))

            if bucket_imbalances and total_vol > 0:
                rec['vpin'] = np.sum(bucket_imbalances) / total_vol
            else:
                rec['vpin'] = 0.5
        else:
            rec['vpin'] = 0.5

        # --- Taker Buy Ratio (order flow) ---
        total_vol = volume.sum()
        if total_vol > 0:
            rec['taker_buy_ratio'] = taker_buy.sum() / total_vol
        else:
            rec['taker_buy_ratio'] = 0.5

        # --- Trade Count ---
        rec['trade_count'] = trades.sum()

        # --- Volume Concentration (Herfindahl of hourly volume) ---
        hourly_vol = g.groupby('hour')['volume'].sum()
        if hourly_vol.sum() > 0:
            shares = hourly_vol / hourly_vol.sum()
            rec['volume_herfindahl'] = (shares ** 2).sum()
            # Normalized: 1/24 = perfectly even, 1.0 = all in one hour
        else:
            rec['volume_herfindahl'] = 1.0 / 24

        # --- Parkinson Volatility (from 1-min H/L) ---
        log_hl = np.log(high / (low + 1e-10) + 1e-10)
        valid_hl = log_hl[np.isfinite(log_hl) & (log_hl > 0)]
        if len(valid_hl) > 10:
            rec['parkinson_vol'] = np.sqrt(np.mean(valid_hl ** 2) / (4 * np.log(2)) * 252)
        else:
            rec['parkinson_vol'] = 0

        # --- Amihud Illiquidity (1-min) ---
        abs_rets = np.abs(log_rets)
        dv = quote_vol[1:len(log_rets)+1] if len(quote_vol) > len(log_rets) else quote_vol[:len(log_rets)]
        valid_dv = dv > 0
        if valid_dv.sum() > 10:
            rec['amihud_1m'] = np.mean(abs_rets[valid_dv] / dv[valid_dv])
        else:
            rec['amihud_1m'] = 0

        # --- Max Intraday Drawdown ---
        if len(close) > 1:
            cum_max = np.maximum.accumulate(close)
            drawdowns = (close - cum_max) / (cum_max + 1e-10)
            rec['max_intraday_dd'] = drawdowns.min()
        else:
            rec['max_intraday_dd'] = 0

        # --- VWAP Deviation ---
        if total_vol > 0:
            vwap = (close * volume).sum() / total_vol
            rec['vwap_deviation'] = (close[-1] - vwap) / (vwap + 1e-10)
        else:
            rec['vwap_deviation'] = 0

        # --- Return Skewness (1-min) ---
        if len(log_rets) > 30:
            mu = np.mean(log_rets)
            sigma = np.std(log_rets) + 1e-10
            rec['intraday_skew'] = np.mean(((log_rets - mu) / sigma) ** 3)
        else:
            rec['intraday_skew'] = 0

        # --- Return Kurtosis (1-min, excess) ---
        if len(log_rets) > 30:
            rec['intraday_kurtosis'] = np.mean(((log_rets - mu) / sigma) ** 4) - 3
        else:
            rec['intraday_kurtosis'] = 0

        records.append(rec)

    if not records:
        return pd.DataFrame()

    result = pd.DataFrame(records)
    result.set_index('date', inplace=True)
    result.index = pd.DatetimeIndex(result.index)
    return result


def download_and_aggregate_token(ticker, start_year=2020, end_year=2026,
                                  cache_dir='real_data/1m_cache', verbose=True):
    """
    Download all available 1-min data for a token and aggregate to daily features.
    Returns enriched daily DataFrame.
    """
    os.makedirs(cache_dir, exist_ok=True)
    cache_path = os.path.join(cache_dir, f'{ticker}_1m_features.parquet')

    # Check cache
    if os.path.exists(cache_path):
        df = pd.read_parquet(cache_path)
        if verbose:
            print(f"  ✓ {ticker:8s} cached ({len(df)} days)", flush=True)
        return df

    all_daily = []
    months_downloaded = 0
    months_tried = 0

    # Count total months
    total_months = 0
    for year in range(start_year, end_year + 1):
        for month in range(1, 13):
            if year == end_year and month > datetime.now().month:
                break
            total_months += 1

    for year in range(start_year, end_year + 1):
        for month in range(1, 13):
            # Skip future months
            if year == end_year and month > datetime.now().month:
                break

            months_tried += 1
            df_1m = _download_month(ticker, year, month)
            if df_1m is not None and len(df_1m) > 100:
                daily = aggregate_1m_to_daily(df_1m)
                if len(daily) > 0:
                    all_daily.append(daily)
                    months_downloaded += 1

            if verbose and months_tried % 6 == 0:
                print(f"    {ticker:8s} {months_tried}/{total_months} months "
                      f"({months_downloaded} ok)", flush=True)

            # Small delay between downloads (be nice to Binance)
            time.sleep(0.1)

    if not all_daily:
        if verbose:
            print(f"  ✗ {ticker:8s} no data found", flush=True)
        return pd.DataFrame()

    result = pd.concat(all_daily)
    result = result[~result.index.duplicated(keep='last')]
    result.sort_index(inplace=True)

    # Cache as parquet
    result.to_parquet(cache_path)

    if verbose:
        print(f"  ✓ {ticker:8s} {months_downloaded} months → {len(result)} days", flush=True)

    return result


def build_enriched_parquet(n_jobs=4, start_year=2024):
    """
    Main pipeline: download 1-min data, aggregate, merge with daily OHLCV,
    save as single enriched Parquet file.
    """
    print("=" * 80)
    print("ENRICHED DATA PIPELINE — 1-min → Daily Features")
    print("=" * 80)
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Tokens: {len(BINANCE_VISION_TOKENS)} with 1-min data + {len(NO_1M_DATA)} daily-only")
    print(f"Period: {start_year} → present")
    print(f"Source: data.binance.vision (free, no API key)")
    print()

    t0 = time.time()

    # Step 1: Download and aggregate 1-min data in parallel
    print(f"[1/3] Downloading 1-min data for {len(BINANCE_VISION_TOKENS)} tokens...")
    print(f"  Using {n_jobs} parallel workers")
    print(f"  Estimated download: ~{len(BINANCE_VISION_TOKENS) * 26 * 1.7 / 1024:.1f} GB compressed")
    print()

    enriched = {}
    errors = []
    for i, ticker in enumerate(BINANCE_VISION_TOKENS, 1):
        print(f"  [{i}/{len(BINANCE_VISION_TOKENS)}] {ticker}...", flush=True)
        try:
            features = download_and_aggregate_token(
                ticker, start_year=start_year,
                cache_dir='real_data/1m_cache'
            )
            if len(features) > 0:
                enriched[ticker] = features
            else:
                errors.append((ticker, "no data"))
        except Exception as e:
            errors.append((ticker, str(e)))

    if errors:
        print(f"\n  Errors ({len(errors)}):")
        for t, e in errors:
            print(f"    {t}: {e}")

    dl_time = time.time() - t0
    print(f"\n  Download + aggregate: {dl_time:.0f}s")

    # Step 2: Merge with existing daily OHLCV
    print(f"\n[2/3] Merging with existing daily OHLCV data...")

    all_frames = []

    for ticker in LIQUID_TOKENS:
        daily_path = f'real_data/{ticker}_daily.csv'
        if not os.path.exists(daily_path):
            continue

        df_daily = pd.read_csv(daily_path, index_col=0, parse_dates=True)
        df_daily['ticker'] = ticker

        # Merge enriched features if available
        if ticker in enriched:
            feat = enriched[ticker]
            # Align on date index
            feat.index = pd.DatetimeIndex(feat.index)
            df_daily.index = pd.DatetimeIndex(df_daily.index)

            # Only take the new feature columns (not the duplicate OHLCV)
            feature_cols = [c for c in feat.columns if c not in
                          ['open_1m', 'high_1m', 'low_1m', 'close_1m', 'volume_1m']]
            if feature_cols:
                df_daily = df_daily.join(feat[feature_cols], how='left')

        # Fill NaN features with defaults for days without 1-min data
        feature_defaults = {
            'realized_var': 0, 'realized_vol': 0, 'vpin': 0.5,
            'taker_buy_ratio': 0.5, 'trade_count': 0,
            'volume_herfindahl': 1/24, 'parkinson_vol': 0,
            'amihud_1m': 0, 'max_intraday_dd': 0, 'vwap_deviation': 0,
            'intraday_skew': 0, 'intraday_kurtosis': 0, 'quote_volume': 0,
        }
        for col, default in feature_defaults.items():
            if col not in df_daily.columns:
                df_daily[col] = default
            else:
                df_daily[col] = df_daily[col].fillna(default)

        all_frames.append(df_daily.reset_index())

    if not all_frames:
        print("  No data to merge!")
        return None

    combined = pd.concat(all_frames, ignore_index=True)

    # Step 3: Save as enriched Parquet
    print(f"\n[3/3] Saving enriched Parquet store...")

    output_path = 'real_data/all_tokens_enriched.parquet'
    combined.to_parquet(output_path, engine='pyarrow', index=False)

    # Also update the basic parquet
    basic_cols = ['date', 'open', 'high', 'low', 'close', 'volume', 'ticker']
    basic = combined[[c for c in basic_cols if c in combined.columns]]
    basic.to_parquet('real_data/all_tokens.parquet', engine='pyarrow', index=False)

    total_time = time.time() - t0

    # Summary
    n_enriched = sum(1 for t in LIQUID_TOKENS if t in enriched)
    feature_cols = [c for c in combined.columns if c not in
                   ['date', 'open', 'high', 'low', 'close', 'volume', 'ticker']]

    print(f"\n{'='*80}")
    print("ENRICHED DATA SUMMARY")
    print(f"{'='*80}")
    print(f"  Tokens: {len(all_frames)} total ({n_enriched} with 1-min features)")
    print(f"  Rows: {len(combined):,}")
    print(f"  Features: {len(feature_cols)} enriched columns")
    print(f"  Feature list: {feature_cols}")
    print(f"  Output: {output_path} ({os.path.getsize(output_path)/1e6:.1f} MB)")
    print(f"  Total time: {total_time:.0f}s")

    return combined


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--jobs', type=int, default=4, help='Parallel downloads')
    parser.add_argument('--start-year', type=int, default=2024, help='Start year for 1-min data')
    parser.add_argument('--tokens', nargs='+', help='Specific tokens (default: all liquid)')
    parser.add_argument('--test', action='store_true', help='Test with 1 token, 1 month')
    args = parser.parse_args()

    if args.test:
        print("TEST MODE: 1 token (BTC), 1 month")
        df = _download_month('BTC', 2025, 1)
        if df is not None:
            print(f"Downloaded: {len(df)} 1-min bars")
            daily = aggregate_1m_to_daily(df)
            print(f"Aggregated: {len(daily)} daily bars")
            print(f"Columns: {list(daily.columns)}")
            print(daily.tail())
        else:
            print("Download failed!")
    else:
        build_enriched_parquet(n_jobs=args.jobs, start_year=args.start_year)
