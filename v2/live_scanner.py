"""
Live Token Scanner — Which tokens to trade RIGHT NOW?

Pulls fresh data from Binance API, runs signal lab on all liquid tokens,
and ranks them by current signal strength.

Only scans the liquid universe (>$5M ADV). Runs in ~30-60 seconds.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import numpy as np
import pandas as pd
import urllib.request
import json
import time
from datetime import datetime, timedelta
from liquid_universe import LIQUID_TOKENS, get_tier, max_position_usd, MIN_ADV


def fetch_recent_klines(symbol, days=100):
    """Fetch recent daily klines from Binance (no auth needed)."""
    url = f'https://api.binance.com/api/v3/klines?symbol={symbol}&interval=1d&limit={days}'
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36'
    })
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read().decode())
        if not data:
            return None
        df = pd.DataFrame(data, columns=[
            'open_time', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'quote_volume', 'trades', 'taker_buy_base',
            'taker_buy_quote', 'ignore'
        ])
        for col in ['open', 'high', 'low', 'close', 'volume', 'quote_volume']:
            df[col] = df[col].astype(float)
        df['date'] = pd.to_datetime(df['open_time'], unit='ms')
        df.set_index('date', inplace=True)
        df = df[['open', 'high', 'low', 'close', 'volume']].copy()
        df = df[~df.index.duplicated(keep='last')]
        df.sort_index(inplace=True)
        return df
    except Exception:
        return None


def get_top_symbols(n=200):
    """Get top N USDT pairs by 24h volume from Binance."""
    url = 'https://api.binance.com/api/v3/ticker/24hr'
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    resp = urllib.request.urlopen(req, timeout=30)
    all_tickers = json.loads(resp.read().decode())

    exclude = ['DOWN', 'UP', 'BULL', 'BEAR']
    stables = {'USDC', 'FDUSD', 'RLUSD', 'BFUSD', 'XUSD', 'BUSD', 'USD1',
               'WBTC', 'WBETH', 'BNSOL', 'EUR', 'USTC', 'LUNC', 'TUSD', 'DAI'}

    pairs = []
    for t in all_tickers:
        s = t['symbol']
        if not s.endswith('USDT'):
            continue
        if any(kw in s for kw in exclude):
            continue
        ticker = s.replace('USDT', '')
        if ticker in stables or not ticker.isascii() or len(ticker) < 2:
            continue
        vol_24h = float(t['quoteVolume'])
        price = float(t['lastPrice'])
        change = float(t['priceChangePercent'])
        pairs.append({'ticker': ticker, 'symbol': s, 'volume_24h': vol_24h,
                      'price': price, 'change_24h': change})

    pairs.sort(key=lambda x: x['volume_24h'], reverse=True)
    return pairs[:n]


# ============================================================================
# Signal computations (vectorized, fast)
# ============================================================================

def compute_signals(df):
    """Compute all trading signals for a single token. Returns dict of signal values."""
    close = df['close'].values.astype(float)
    high = df['high'].values.astype(float)
    low = df['low'].values.astype(float)
    volume = df['volume'].values.astype(float)
    n = len(close)

    if n < 80:
        return None

    signals = {}

    # Returns
    for period in [5, 10, 20, 60]:
        if n > period:
            signals[f'ret_{period}d'] = close[-1] / close[-1-period] - 1
        else:
            signals[f'ret_{period}d'] = 0

    # RSI
    deltas = np.diff(close[-15:])
    gains = np.mean(np.where(deltas > 0, deltas, 0))
    losses = np.mean(np.where(deltas < 0, -deltas, 0))
    if losses > 0:
        rs = gains / losses
        signals['rsi_14'] = 100 - (100 / (1 + rs))
    else:
        signals['rsi_14'] = 100

    # Z-scores
    for period in [20, 50]:
        if n > period:
            window = close[-period:]
            mu, sigma = np.mean(window), np.std(window)
            signals[f'zscore_{period}'] = (close[-1] - mu) / sigma if sigma > 0 else 0
        else:
            signals[f'zscore_{period}'] = 0

    # Volatility
    rets = np.diff(np.log(np.maximum(close, 1e-10)))
    if len(rets) > 60:
        vol_5 = np.std(rets[-5:]) * np.sqrt(365)
        vol_20 = np.std(rets[-20:]) * np.sqrt(365)
        vol_60 = np.std(rets[-60:]) * np.sqrt(365)
        signals['vol_5d'] = vol_5
        signals['vol_20d'] = vol_20
        signals['vol_60d'] = vol_60
        signals['rvol_ratio'] = vol_5 / vol_60 if vol_60 > 0 else 1
    else:
        signals['vol_5d'] = signals['vol_20d'] = signals['vol_60d'] = 0
        signals['rvol_ratio'] = 1

    # Vol-of-vol
    if n > 40:
        rolling_vol = np.array([np.std(rets[max(0,i-20):i]) for i in range(20, len(rets))])
        if len(rolling_vol) > 5 and np.mean(rolling_vol[-20:]) > 0:
            signals['vol_of_vol'] = np.std(rolling_vol[-20:]) / np.mean(rolling_vol[-20:])
        else:
            signals['vol_of_vol'] = 0
    else:
        signals['vol_of_vol'] = 0

    # Garman-Klass volatility
    if n > 20:
        h = np.log(high[-20:])
        l = np.log(low[-20:])
        c = np.log(close[-20:])
        o = np.log(df['open'].values[-20:].astype(float))
        gk = 0.5 * (h - l)**2 - (2 * np.log(2) - 1) * (c - o)**2
        signals['garman_klass'] = np.sqrt(np.mean(gk) * 252)
    else:
        signals['garman_klass'] = 0

    # Volume momentum
    if n > 20:
        vol_sma = np.mean(volume[-20:])
        signals['volume_momentum'] = volume[-1] / vol_sma if vol_sma > 0 else 1
    else:
        signals['volume_momentum'] = 1

    # Dollar volume (liquidity)
    signals['dollar_volume_20d'] = np.mean(close[-20:] * volume[-20:]) if n > 20 else 0

    # Close location (where in day's range)
    rng = high[-1] - low[-1]
    signals['close_location'] = (close[-1] - low[-1]) / rng if rng > 0 else 0.5

    # OBV slope
    if n > 10:
        obv = np.zeros(min(n, 20))
        for i in range(1, len(obv)):
            idx = n - len(obv) + i
            if close[idx] > close[idx-1]:
                obv[i] = obv[i-1] + volume[idx]
            elif close[idx] < close[idx-1]:
                obv[i] = obv[i-1] - volume[idx]
            else:
                obv[i] = obv[i-1]
        x = np.arange(len(obv[-10:]))
        y = obv[-10:]
        if np.std(y) > 0:
            signals['obv_slope'] = np.polyfit(x, y, 1)[0]
        else:
            signals['obv_slope'] = 0
    else:
        signals['obv_slope'] = 0

    # Amihud illiquidity
    if n > 11:
        dv = close[-10:] * volume[-10:]
        abs_rets = np.abs(np.diff(close[-11:]) / close[-11:-1])
        valid = dv > 0
        if valid.sum() > 3:
            signals['amihud'] = np.mean(abs_rets[valid] / dv[valid])
        else:
            signals['amihud'] = 0
    else:
        signals['amihud'] = 0

    # EMA cross
    if n > 26:
        ema12 = close[-1]  # approximate
        ema26 = close[-1]
        alpha12 = 2/13
        alpha26 = 2/27
        for i in range(min(n, 60)):
            idx = n - 1 - i
            if i == 0:
                ema12 = close[idx]
                ema26 = close[idx]
            else:
                ema12 = alpha12 * close[idx] + (1 - alpha12) * ema12
                ema26 = alpha26 * close[idx] + (1 - alpha26) * ema26
        # Recompute forward
        ema12 = np.mean(close[-12:])
        ema26 = np.mean(close[-26:])
        for i in range(-12, 0):
            ema12 = alpha12 * close[i] + (1 - alpha12) * ema12
        for i in range(-26, 0):
            ema26 = alpha26 * close[i] + (1 - alpha26) * ema26
        signals['ema_cross'] = ema12 / ema26 - 1 if ema26 > 0 else 0
    else:
        signals['ema_cross'] = 0

    # MACD histogram
    if n > 35:
        ema_fast = np.mean(close[-12:])
        ema_slow = np.mean(close[-26:])
        af = 2/13
        a_s = 2/27
        for p in close[-35:]:
            ema_fast = af * p + (1-af) * ema_fast
            ema_slow = a_s * p + (1-a_s) * ema_slow
        signals['macd_hist'] = ema_fast - ema_slow
    else:
        signals['macd_hist'] = 0

    # Range compression (ATR5 / ATR20)
    if n > 20:
        tr = np.maximum(high[1:] - low[1:],
                       np.maximum(np.abs(high[1:] - close[:-1]),
                                  np.abs(low[1:] - close[:-1])))
        atr5 = np.mean(tr[-5:])
        atr20 = np.mean(tr[-20:])
        signals['range_compression'] = atr5 / atr20 if atr20 > 0 else 1
    else:
        signals['range_compression'] = 1

    # Mean reversion 5d (contrarian)
    signals['mean_reversion_5d'] = -signals['ret_5d']

    return signals


def compute_composite_score(signals, btc_signals=None):
    """
    Compute composite trading score using IC-weighted signals.

    Weights based on post-ETF IC from signal_lab results:
    - vol_of_vol: IC=-0.098 (CONTRARIAN: high vov = sell)
    - garman_klass: IC=-0.065 (CONTRARIAN: high GK vol = sell)
    - volume_momentum: IC=+0.040 (high vol = buy)
    - mean_reversion_5d: IC=+0.033 (recent losers = buy)
    - rvol_ratio: IC=+0.029 (vol expansion = buy)
    """
    score = 0
    n_signals = 0

    # Contrarian vol signals (NEGATIVE IC = flip)
    if 'vol_of_vol' in signals:
        # High vol_of_vol predicts negative returns → SHORT signal
        # Low vol_of_vol → LONG signal
        vov = signals['vol_of_vol']
        if vov < 0.2:
            score += 0.098 * 2  # Low vov = strong buy
        elif vov > 0.5:
            score -= 0.098 * 2  # High vov = sell
        n_signals += 1

    if 'garman_klass' in signals:
        gk = signals['garman_klass']
        if gk < 0.3:
            score += 0.065  # Low vol = accumulation
        elif gk > 1.0:
            score -= 0.065  # High vol = danger
        n_signals += 1

    # Positive IC signals
    if 'volume_momentum' in signals:
        vm = signals['volume_momentum']
        if vm > 1.5:
            score += 0.040 * (vm - 1)  # Volume surge = interest
        elif vm < 0.5:
            score -= 0.040  # No volume = no interest
        n_signals += 1

    if 'mean_reversion_5d' in signals:
        mr = signals['mean_reversion_5d']
        score += 0.033 * mr * 10  # Scale up: 5% drop → +0.017 score
        n_signals += 1

    if 'rvol_ratio' in signals:
        rv = signals['rvol_ratio']
        if rv < 0.7:
            score += 0.029  # Vol compressed = breakout coming
        elif rv > 2.0:
            score -= 0.029  # Vol spike = danger
        n_signals += 1

    # RSI contrarian
    rsi = signals.get('rsi_14', 50)
    if rsi < 30:
        score += 0.05  # Oversold = buy
    elif rsi > 70:
        score -= 0.05  # Overbought = sell

    # Z-score contrarian (post-ETF: z-score flipped to contrarian)
    z20 = signals.get('zscore_20', 0)
    score -= 0.023 * z20 * 0.5  # Negative z = buy, positive z = sell

    # Trend (weaker post-ETF but still has some signal)
    ret20 = signals.get('ret_20d', 0)
    if abs(ret20) > 0.1:
        # Strong moves: contrarian post-ETF
        score -= 0.034 * ret20
    else:
        # Small moves: slight momentum
        score += 0.010 * ret20

    # OBV divergence
    obv = signals.get('obv_slope', 0)
    if obv > 0 and ret20 < 0:
        score += 0.03  # Accumulation: volume up, price down
    elif obv < 0 and ret20 > 0:
        score -= 0.03  # Distribution: volume down, price up

    # BTC-relative signal
    if btc_signals is not None:
        btc_ret5 = btc_signals.get('ret_5d', 0)
        # Post-ETF: btc_lead_5d has IC=-0.072 (contrarian!)
        score -= 0.072 * btc_ret5

    return score


def run_live_scan():
    print("=" * 80)
    print("LIVE TOKEN SCANNER — What to Trade RIGHT NOW")
    print("=" * 80)
    print(f"Scan time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"Data: Binance API (live, last 100 daily candles)")
    print()

    # Step 1: Get top tokens by volume
    print("[1/4] Fetching top 200 tokens by 24h volume...")
    top_pairs = get_top_symbols(200)
    print(f"  Found {len(top_pairs)} tradeable USDT pairs")

    # Step 2: Fetch recent data for each
    print(f"\n[2/4] Fetching last 100 daily candles for each token...")
    token_data = {}
    token_meta = {}
    failed = 0

    for idx, pair in enumerate(top_pairs):
        ticker = pair['ticker']
        symbol = pair['symbol']

        df = fetch_recent_klines(symbol, days=100)
        if df is not None and len(df) >= 60:
            token_data[ticker] = df
            token_meta[ticker] = pair
        else:
            failed += 1

        if (idx + 1) % 50 == 0:
            print(f"  [{idx+1}/{len(top_pairs)}] {len(token_data)} loaded, {failed} failed")
            time.sleep(0.5)  # Rate limit

        # Small delay to avoid rate limits
        if (idx + 1) % 10 == 0:
            time.sleep(0.2)

    print(f"  Done: {len(token_data)} tokens loaded, {failed} failed")

    # Step 3: Compute signals
    print(f"\n[3/4] Computing signals for {len(token_data)} tokens...")
    t0 = time.time()

    all_signals = {}
    btc_signals = None

    for ticker, df in token_data.items():
        sigs = compute_signals(df)
        if sigs is not None:
            all_signals[ticker] = sigs
            if ticker == 'BTC':
                btc_signals = sigs

    # Compute composite scores
    scores = {}
    for ticker, sigs in all_signals.items():
        score = compute_composite_score(sigs, btc_signals)
        scores[ticker] = score

    elapsed = time.time() - t0
    print(f"  Computed {len(all_signals)} token signals in {elapsed:.1f}s")

    # Step 4: Rank and display
    print(f"\n[4/4] Ranking tokens...\n")

    # Build results DataFrame
    rows = []
    for ticker, sigs in all_signals.items():
        meta = token_meta.get(ticker, {})
        rows.append({
            'ticker': ticker,
            'price': meta.get('price', 0),
            'change_24h': meta.get('change_24h', 0),
            'volume_24h_usd': meta.get('volume_24h', 0),
            'score': scores.get(ticker, 0),
            'rsi': sigs.get('rsi_14', 50),
            'zscore_20': sigs.get('zscore_20', 0),
            'ret_5d': sigs.get('ret_5d', 0) * 100,
            'ret_20d': sigs.get('ret_20d', 0) * 100,
            'vol_20d': sigs.get('vol_20d', 0) * 100,
            'rvol_ratio': sigs.get('rvol_ratio', 1),
            'vol_of_vol': sigs.get('vol_of_vol', 0),
            'volume_momentum': sigs.get('volume_momentum', 1),
            'dollar_volume_20d': sigs.get('dollar_volume_20d', 0),
        })

    results = pd.DataFrame(rows)
    results = results.sort_values('score', ascending=False)

    # Filter to liquid universe only
    liquid = results[results['ticker'].isin(LIQUID_TOKENS)]
    illiquid = results[~results['ticker'].isin(LIQUID_TOKENS)]

    # TOP BUYS
    print("=" * 100)
    print("TOP 20 TOKENS TO BUY NOW (by composite signal score, liquid only)")
    print("=" * 100)
    print(f"{'Rank':>4} {'Token':>8} {'Price':>12} {'24h%':>7} {'Score':>7} "
          f"{'RSI':>5} {'Z20':>6} {'5d%':>7} {'20d%':>7} "
          f"{'Vol20d':>7} {'VRatio':>6} {'VolMom':>6} {'ADV($M)':>9}")
    print("-" * 100)

    for rank, (_, row) in enumerate(liquid.head(20).iterrows(), 1):
        # Signal interpretation
        if row['score'] > 0.05:
            icon = 'BUY'
        elif row['score'] > 0.02:
            icon = 'buy'
        elif row['score'] > 0:
            icon = 'lean'
        else:
            continue

        print(f"{rank:>4} {row['ticker']:>8} ${row['price']:>10.4f} {row['change_24h']:>+6.1f}% "
              f"{row['score']:>+6.3f} "
              f"{row['rsi']:>5.0f} {row['zscore_20']:>+5.2f} "
              f"{row['ret_5d']:>+6.1f}% {row['ret_20d']:>+6.1f}% "
              f"{row['vol_20d']:>6.0f}% {row['rvol_ratio']:>5.2f} "
              f"{row['volume_momentum']:>5.2f} {row['dollar_volume_20d']/1e6:>8.1f}")

    # TOP SELLS / AVOIDS
    print(f"\n{'='*100}")
    print("TOP 20 TOKENS TO AVOID / SELL (strongest sell signals)")
    print("=" * 100)
    print(f"{'Rank':>4} {'Token':>8} {'Price':>12} {'24h%':>7} {'Score':>7} "
          f"{'RSI':>5} {'Z20':>6} {'5d%':>7} {'20d%':>7} "
          f"{'Vol20d':>7} {'VRatio':>6} {'VolMom':>6} {'ADV($M)':>9}")
    print("-" * 100)

    sells = liquid.sort_values('score').head(20)
    for rank, (_, row) in enumerate(sells.iterrows(), 1):
        if row['score'] >= 0:
            continue
        print(f"{rank:>4} {row['ticker']:>8} ${row['price']:>10.4f} {row['change_24h']:>+6.1f}% "
              f"{row['score']:>+6.3f} "
              f"{row['rsi']:>5.0f} {row['zscore_20']:>+5.2f} "
              f"{row['ret_5d']:>+6.1f}% {row['ret_20d']:>+6.1f}% "
              f"{row['vol_20d']:>6.0f}% {row['rvol_ratio']:>5.2f} "
              f"{row['volume_momentum']:>5.2f} {row['dollar_volume_20d']/1e6:>8.1f}")

    # SUMMARY STATS
    print(f"\n{'='*80}")
    print("MARKET OVERVIEW")
    print("=" * 80)

    n_buy = (liquid['score'] > 0.02).sum()
    n_sell = (liquid['score'] < -0.02).sum()
    n_neutral = len(liquid) - n_buy - n_sell

    avg_rsi = liquid['rsi'].mean()
    avg_vol = liquid['vol_20d'].mean()
    avg_ret5 = liquid['ret_5d'].mean()
    avg_ret20 = liquid['ret_20d'].mean()

    print(f"\n  Liquid universe: {len(liquid)} tokens scanned")
    print(f"  + {len(illiquid)} tokens outside liquid universe (excluded)")
    print(f"\n  Signal distribution:")
    print(f"    BUY signals:     {n_buy:3d} tokens ({n_buy/len(liquid)*100:.0f}%)")
    print(f"    NEUTRAL:         {n_neutral:3d} tokens ({n_neutral/len(liquid)*100:.0f}%)")
    print(f"    SELL signals:    {n_sell:3d} tokens ({n_sell/len(liquid)*100:.0f}%)")
    print(f"\n  Market conditions:")
    print(f"    Avg RSI:         {avg_rsi:.0f} ({'overbought' if avg_rsi > 60 else 'oversold' if avg_rsi < 40 else 'neutral'})")
    print(f"    Avg 20d Vol:     {avg_vol:.0f}%")
    print(f"    Avg 5d Return:   {avg_ret5:+.1f}%")
    print(f"    Avg 20d Return:  {avg_ret20:+.1f}%")

    if btc_signals:
        btc_rsi = btc_signals.get('rsi_14', 50)
        btc_z = btc_signals.get('zscore_20', 0)
        btc_vov = btc_signals.get('vol_of_vol', 0)
        print(f"\n  BTC status:")
        print(f"    RSI: {btc_rsi:.0f}  Z-score: {btc_z:+.2f}  Vol-of-Vol: {btc_vov:.3f}")
        if btc_rsi > 70:
            print(f"    ⚠ BTC OVERBOUGHT — contrarian signal says REDUCE exposure")
        elif btc_rsi < 30:
            print(f"    BTC OVERSOLD — contrarian signal says ADD exposure")
        if btc_vov > 0.4:
            print(f"    ⚠ BTC vol-of-vol HIGH — regime change possible, SIZE DOWN")

    # Save results
    os.makedirs('outputs_v2', exist_ok=True)
    results.to_csv('outputs_v2/live_scan.csv', index=False)
    print(f"\n  Saved live_scan.csv ({len(results)} tokens)")

    # Top picks summary with position sizing
    print(f"\n{'='*80}")
    print("ACTIONABLE PICKS (Score > 0.03, liquid universe)")
    print("=" * 80)

    picks = liquid[liquid['score'] > 0.03].sort_values('score', ascending=False)

    if len(picks) == 0:
        print("\n  No strong picks right now. Market may be in transition.")
        print("  Consider waiting or using smaller position sizes.")
    else:
        print(f"\n  {'Token':>8} {'Price':>10} {'Score':>7} {'Tier':>4} {'MaxPos':>8} {'ADV($M)':>8}  Why")
        print("  " + "-" * 85)
        for _, row in picks.head(15).iterrows():
            reasons = []
            if row['rsi'] < 35:
                reasons.append(f"RSI={row['rsi']:.0f}")
            if row['zscore_20'] < -1:
                reasons.append(f"Z={row['zscore_20']:.1f}")
            if row['ret_5d'] < -5:
                reasons.append(f"5d={row['ret_5d']:.0f}%")
            if row['volume_momentum'] > 1.5:
                reasons.append(f"vol={row['volume_momentum']:.1f}x")
            if row['rvol_ratio'] < 0.7:
                reasons.append("compressed")
            if row['vol_of_vol'] < 0.15:
                reasons.append("stable")

            reason_str = ", ".join(reasons) if reasons else "composite"
            adv = row['dollar_volume_20d']
            tier, _ = get_tier(row['ticker'])
            max_pos = max_position_usd(row['ticker'], capital=200_000, adv=adv)

            print(f"  {row['ticker']:>8} ${row['price']:>9.4f} {row['score']:>+6.3f}  T{tier}  "
                  f"${max_pos:>6,.0f} ${adv/1e6:>7.1f}  {reason_str}")

    total_time = time.time() - t0
    print(f"\n\nTotal scan time: {total_time:.1f}s")
    return results


if __name__ == '__main__':
    run_live_scan()
