"""
Real Historical Data Fetcher

Pulls daily OHLCV data from Binance public API (no auth required).
Supports BTC, ETH, SOL and any other ticker available on Binance.
"""

import urllib.request
import json
import time
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import os


def fetch_binance_klines(symbol, interval='1d', limit=1000, end_time=None):
    """
    Fetch klines (candlestick) data from Binance public API.

    Args:
        symbol: Trading pair (e.g., 'BTCUSDT')
        interval: Candle interval ('1d', '4h', '1h', etc.)
        limit: Max 1000 per request
        end_time: End timestamp in ms (for pagination)

    Returns:
        List of kline data
    """
    url = f'https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}'
    if end_time:
        url += f'&endTime={end_time}'

    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36'
    })

    resp = urllib.request.urlopen(req, timeout=30)
    data = json.loads(resp.read().decode())
    return data


def fetch_full_history(symbol, interval='1d', days=2000):
    """
    Fetch full history by paginating backwards.

    Binance returns max 1000 candles per request, so we paginate.
    """
    all_klines = []
    end_time = None
    target_start = int((datetime.now() - timedelta(days=days)).timestamp() * 1000)

    print(f"  Fetching {symbol} ({days} days)...", end='', flush=True)

    while True:
        klines = fetch_binance_klines(symbol, interval, limit=1000, end_time=end_time)

        if not klines:
            break

        all_klines = klines + all_klines

        # Check if we've gone back far enough
        earliest = klines[0][0]  # Open time of first candle
        if earliest <= target_start:
            break

        # Paginate backwards
        end_time = klines[0][0] - 1
        print('.', end='', flush=True)
        time.sleep(0.2)  # Rate limit courtesy

    print(f" {len(all_klines)} candles")
    return all_klines


def klines_to_dataframe(klines):
    """Convert Binance kline data to pandas DataFrame."""
    df = pd.DataFrame(klines, columns=[
        'open_time', 'open', 'high', 'low', 'close', 'volume',
        'close_time', 'quote_volume', 'trades', 'taker_buy_base',
        'taker_buy_quote', 'ignore'
    ])

    # Convert types
    df['open'] = df['open'].astype(float)
    df['high'] = df['high'].astype(float)
    df['low'] = df['low'].astype(float)
    df['close'] = df['close'].astype(float)
    df['volume'] = df['volume'].astype(float)
    df['quote_volume'] = df['quote_volume'].astype(float)
    df['trades'] = df['trades'].astype(int)

    # Set datetime index
    df['date'] = pd.to_datetime(df['open_time'], unit='ms')
    df.set_index('date', inplace=True)

    # Keep only OHLCV columns
    df = df[['open', 'high', 'low', 'close', 'volume']].copy()

    # Remove duplicates
    df = df[~df.index.duplicated(keep='last')]
    df.sort_index(inplace=True)

    return df


def add_synthetic_derivatives(df, ticker):
    """
    Add synthetic funding rate and open interest columns.

    Real funding/OI data requires authenticated APIs or specialized sources.
    We synthesize plausible values from price/volume patterns to keep
    Strategy 4 (Funding+OI Divergence) functional.
    """
    n = len(df)

    # Synthetic funding rate: correlated with momentum but mean-reverting
    returns = df['close'].pct_change().fillna(0)
    momentum_20 = returns.rolling(20).mean().fillna(0)

    # Funding tends to be positive in uptrends, negative in downtrends
    # with noise and mean-reversion
    np.random.seed(42)
    noise = np.random.normal(0, 0.0003, n)
    funding = momentum_20 * 5 + noise  # Scale momentum to funding-rate-like values
    funding = np.clip(funding, -0.003, 0.003)  # Typical range: -0.3% to 0.3%
    df['funding_rate'] = funding

    # Synthetic open interest: correlated with volume and price trends
    vol_ma = df['volume'].rolling(30).mean().fillna(df['volume'].iloc[0])
    vol_ratio = df['volume'] / vol_ma

    # OI grows in trends, shrinks in mean-reversion/chaos
    base_oi = df['close'] * df['volume'] * 0.1  # Rough notional
    oi_trend = base_oi.rolling(20).mean().fillna(base_oi.iloc[0])
    np.random.seed(43)
    oi_noise = np.random.normal(1.0, 0.05, n)
    df['open_interest'] = oi_trend * oi_noise

    return df


# Ticker mapping: friendly name -> Binance symbol
TICKER_MAP = {
    'BTC': 'BTCUSDT',
    'ETH': 'ETHUSDT',
    'SOL': 'SOLUSDT',
    'BNB': 'BNBUSDT',
    'XRP': 'XRPUSDT',
    'ADA': 'ADAUSDT',
    'AVAX': 'AVAXUSDT',
    'DOGE': 'DOGEUSDT',
    'DOT': 'DOTUSDT',
    'LINK': 'LINKUSDT',
    'MATIC': 'MATICUSDT',
    'UNI': 'UNIUSDT',
    'ATOM': 'ATOMUSDT',
    'LTC': 'LTCUSDT',
    'NEAR': 'NEARUSDT',
    'ARB': 'ARBUSDT',
    'OP': 'OPUSDT',
    'APT': 'APTUSDT',
    'SUI': 'SUIUSDT',
    'FIL': 'FILUSDT',
}


def fetch_all_tickers(tickers=None, days=2000, cache_dir='real_data'):
    """
    Fetch real OHLCV data for multiple tickers.

    Args:
        tickers: List of ticker names (default: BTC, ETH, SOL)
        days: Number of days of history
        cache_dir: Directory to cache downloaded data

    Returns:
        Dict of {ticker: DataFrame}
    """
    if tickers is None:
        tickers = ['BTC', 'ETH', 'SOL']

    os.makedirs(cache_dir, exist_ok=True)
    datasets = {}

    for ticker in tickers:
        symbol = TICKER_MAP.get(ticker, f'{ticker}USDT')
        cache_file = os.path.join(cache_dir, f'{ticker}_daily.csv')

        # Check cache (re-download if older than 1 day)
        use_cache = False
        if os.path.exists(cache_file):
            mod_time = datetime.fromtimestamp(os.path.getmtime(cache_file))
            if datetime.now() - mod_time < timedelta(hours=24):
                use_cache = True

        if use_cache:
            print(f"  {ticker}: Loading from cache")
            df = pd.read_csv(cache_file, index_col=0, parse_dates=True)
        else:
            try:
                klines = fetch_full_history(symbol, days=days)
                df = klines_to_dataframe(klines)
                df.to_csv(cache_file)
            except Exception as e:
                print(f"  {ticker}: FAILED - {e}")
                continue

        # Add synthetic derivatives data
        df = add_synthetic_derivatives(df, ticker)

        datasets[ticker] = df
        print(f"  {ticker}: {len(df)} days, "
              f"${df['close'].iloc[0]:.2f} -> ${df['close'].iloc[-1]:.2f} "
              f"({df.index[0].strftime('%Y-%m-%d')} to {df.index[-1].strftime('%Y-%m-%d')})")

    return datasets


def fetch_top_tokens(n=200, min_days=365, days=2000, cache_dir='real_data'):
    """
    Fetch top N tokens by 24h volume from Binance, download OHLCV history.

    Args:
        n: Number of top tokens to fetch
        min_days: Minimum history required (skip tokens with less)
        days: Max days of history to fetch
        cache_dir: Cache directory

    Returns:
        Dict of {ticker: DataFrame} for tokens with enough history
    """
    import urllib.request as req2

    # Step 1: Get top tokens by volume
    print(f"Fetching top {n} tokens by volume from Binance...")
    url = 'https://api.binance.com/api/v3/ticker/24hr'
    request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    resp = urllib.request.urlopen(request, timeout=30)
    all_tickers = json.loads(resp.read().decode())

    # Filter and sort
    exclude_keywords = ['DOWN', 'UP', 'BULL', 'BEAR']
    stablecoins = {'USDC', 'FDUSD', 'RLUSD', 'BFUSD', 'XUSD', 'BUSD', 'USD1',
                   'BARD', 'WBTC', 'WBETH', 'BNSOL', 'EUR', 'USTC', 'LUNC'}

    usdt_pairs = []
    for t in all_tickers:
        s = t['symbol']
        if not s.endswith('USDT'):
            continue
        if any(kw in s for kw in exclude_keywords):
            continue
        ticker = s.replace('USDT', '')
        if ticker in stablecoins or ticker == 'U':
            continue
        # Skip non-ASCII tickers
        if not ticker.isascii():
            continue
        usdt_pairs.append((ticker, float(t['quoteVolume']), float(t['lastPrice'])))

    usdt_pairs.sort(key=lambda x: x[1], reverse=True)
    top_tickers = [t[0] for t in usdt_pairs[:n]]
    print(f"  Found {len(top_tickers)} tradeable tokens")

    # Step 2: Download history for each
    os.makedirs(cache_dir, exist_ok=True)
    datasets = {}
    failed = []
    skipped_short = []

    for idx, ticker in enumerate(top_tickers):
        symbol = f'{ticker}USDT'
        cache_file = os.path.join(cache_dir, f'{ticker}_daily.csv')

        # Check cache
        use_cache = False
        if os.path.exists(cache_file):
            mod_time = datetime.fromtimestamp(os.path.getmtime(cache_file))
            if datetime.now() - mod_time < timedelta(hours=24):
                use_cache = True

        if use_cache:
            df = pd.read_csv(cache_file, index_col=0, parse_dates=True)
        else:
            try:
                klines = fetch_full_history(symbol, days=days)
                if not klines:
                    failed.append(ticker)
                    continue
                df = klines_to_dataframe(klines)
                df.to_csv(cache_file)
            except Exception as e:
                failed.append(ticker)
                if (idx + 1) % 50 == 0:
                    print(f"  [{idx+1}/{len(top_tickers)}] {ticker}: FAILED - {e}")
                continue

        if len(df) < min_days:
            skipped_short.append((ticker, len(df)))
            continue

        df = add_synthetic_derivatives(df, ticker)
        datasets[ticker] = df

        if (idx + 1) % 25 == 0 or idx == 0:
            print(f"  [{idx+1}/{len(top_tickers)}] {len(datasets)} loaded, "
                  f"{len(failed)} failed, {len(skipped_short)} too short")

    print(f"\nDone: {len(datasets)} tokens with {min_days}+ days of history")
    print(f"  Failed: {len(failed)} ({', '.join(failed[:10])}{'...' if len(failed) > 10 else ''})")
    print(f"  Too short (<{min_days}d): {len(skipped_short)}")

    return datasets


if __name__ == '__main__':
    import sys

    if '--top200' in sys.argv or '--all' in sys.argv:
        print("Fetching top 200 tokens from Binance...")
        datasets = fetch_top_tokens(n=200, min_days=365, days=2000)

        print(f"\n{'='*70}")
        print(f"TOP TOKENS SUMMARY ({len(datasets)} tokens)")
        print(f"{'='*70}")

        summaries = []
        for ticker, df in datasets.items():
            returns = df['close'].pct_change().dropna()
            total_ret = (df['close'].iloc[-1] / df['close'].iloc[0] - 1) * 100
            ann_vol = returns.std() * np.sqrt(365) * 100
            max_dd = ((df['close'] / df['close'].cummax()) - 1).min() * 100
            summaries.append({
                'ticker': ticker, 'days': len(df),
                'return_pct': total_ret, 'vol_pct': ann_vol, 'max_dd': max_dd,
                'start': df.index[0].strftime('%Y-%m-%d'),
                'end': df.index[-1].strftime('%Y-%m-%d'),
            })

        summaries.sort(key=lambda x: x['return_pct'], reverse=True)
        for s in summaries[:20]:
            print(f"  {s['ticker']:8s} {s['days']:5d}d  "
                  f"Ret={s['return_pct']:>8.1f}%  Vol={s['vol_pct']:>6.1f}%  "
                  f"DD={s['max_dd']:>7.1f}%  ({s['start']} to {s['end']})")
        print(f"  ... and {len(summaries) - 20} more")

    else:
        print("Fetching BTC/ETH/SOL from Binance...")
        datasets = fetch_all_tickers(['BTC', 'ETH', 'SOL'], days=2000)

        for ticker, df in datasets.items():
            returns = df['close'].pct_change().dropna()
            print(f"\n{ticker} Summary:")
            print(f"  Period: {df.index[0].strftime('%Y-%m-%d')} to {df.index[-1].strftime('%Y-%m-%d')}")
            print(f"  Days: {len(df)}")
            print(f"  Total Return: {(df['close'].iloc[-1]/df['close'].iloc[0] - 1)*100:.1f}%")
            print(f"  Annual Vol: {returns.std() * np.sqrt(365) * 100:.1f}%")
            print(f"  Max Drawdown: {((df['close'] / df['close'].cummax()) - 1).min() * 100:.1f}%")
