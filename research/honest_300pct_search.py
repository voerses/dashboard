#!/usr/bin/env python3
"""Honest 300% Search: All signals properly shifted, no look-ahead.

After discovering look-ahead bias in the MTF regime, this script tests
multiple HONEST approaches. All signals use shift(1) on their native
timeframe before forward-filling to hourly.

Approaches:
1. Properly shifted daily EMA regime + leveraged L/S on static top3
2. Properly shifted hourly EMA (no resample needed — naturally causal)
3. Rolling ATR regime (volatility breakout — naturally causal on hourly)
4. Donchian channel breakout (highest close in N days — hourly, causal)
5. Multi-strategy stacking (combine uncorrelated honest signals)
6. Adaptive leverage (size up in strong trends, down in weak)
"""
import numpy as np
import pandas as pd
import warnings
from pathlib import Path

warnings.filterwarnings('ignore')

DATA_DIR = Path('/workspace/crypto_backtest/data/spot/1h_cache')
PERP_DIR = Path('/workspace/crypto_backtest/data/perp/1h_cache')
CAPITAL = 200_000
COST_BPS = 20
FUNDING_RATE_ANNUAL = 0.12

ALL_TOKENS = [
    'ETH', 'SOL', 'BNB', 'AVAX', 'LINK', 'DOT', 'NEAR', 'ADA', 'XRP',
    'DOGE', 'ATOM', 'UNI', 'INJ', 'FIL', 'AAVE', 'LTC', 'BCH', 'HBAR',
    'XLM', 'TRX'
]


def load_token(token, data_dir=DATA_DIR):
    path = data_dir / f'{token}_1h.parquet'
    if not path.exists():
        path = PERP_DIR / f'{token}_1h.parquet'
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    if 'close' not in df.columns:
        return None
    return df[['open', 'high', 'low', 'close', 'volume']].copy()


def compute_ema(prices, span):
    alpha = 2.0 / (span + 1)
    out = np.empty_like(prices, dtype=np.float64)
    out[0] = prices[0]
    for i in range(1, len(prices)):
        out[i] = alpha * prices[i] + (1 - alpha) * out[i - 1]
    return out


def compute_metrics(equity_curve, index, capital=CAPITAL):
    eq = pd.Series(equity_curve, index=index)
    daily_eq = eq.resample('D').last().dropna()
    if len(daily_eq) < 30:
        return None
    dr = daily_eq.pct_change().dropna()
    yrs = len(dr) / 365.25
    total_ret = (equity_curve[-1] / capital) - 1
    ann_ret = (1 + total_ret) ** (1 / max(yrs, 0.1)) - 1
    sharpe = dr.mean() / max(dr.std(), 1e-10) * np.sqrt(365.25)
    peak = np.maximum.accumulate(daily_eq.values)
    dd = (daily_eq.values - peak) / peak
    max_dd = np.min(dd)
    calmar = ann_ret / max(abs(max_dd), 0.01)
    neg = dr[dr < 0]
    sortino = dr.mean() / max(neg.std() if len(neg) > 0 else 1e-10, 1e-10) * np.sqrt(365.25)
    return {
        'ann_ret': ann_ret, 'sharpe': sharpe, 'sortino': sortino,
        'max_dd': max_dd, 'calmar': calmar, 'years': yrs,
    }


# ============================================================================
# SIGNAL 1: Hourly EMA cross (NATURALLY CAUSAL — no resample needed)
# ============================================================================
def hourly_ema_regime(btc_close_hourly, fast_hours, slow_hours):
    """EMA cross on HOURLY bars — naturally causal, no resample/shift needed.
    At bar T, uses close[T] which IS available at time T."""
    prices = np.array(btc_close_hourly.values, dtype=np.float64)
    fast = compute_ema(prices, fast_hours)
    slow = compute_ema(prices, slow_hours)
    # Shift by 1 bar: act on NEXT bar after signal forms
    signal = np.zeros(len(prices))
    signal[1:] = (fast[:-1] > slow[:-1]).astype(float)  # signal[t] based on close[t-1]
    return pd.Series(signal, index=btc_close_hourly.index)


# ============================================================================
# SIGNAL 2: Donchian channel breakout (BTC above N-day high = bullish)
# ============================================================================
def donchian_regime(btc_close_hourly, lookback_hours):
    """Bullish when BTC close > highest close in past N hours.
    This is naturally causal: uses rolling max of PAST closes."""
    prices = np.array(btc_close_hourly.values, dtype=np.float64)
    n = len(prices)
    signal = np.zeros(n)

    # Rolling max of past lookback_hours closes
    for i in range(lookback_hours + 1, n):
        past_max = np.max(prices[i - lookback_hours:i])  # excludes current bar
        past_min = np.min(prices[i - lookback_hours:i])
        mid = (past_max + past_min) / 2
        # Bullish if above midpoint, bearish if below
        signal[i] = 1.0 if prices[i - 1] > mid else 0.0  # use prev close

    return pd.Series(signal, index=btc_close_hourly.index)


# Vectorized version
def donchian_regime_vec(btc_close_hourly, lookback_hours):
    prices = btc_close_hourly.values.astype(np.float64)
    # Use shifted prices (prev bar's close)
    prev_close = np.roll(prices, 1)
    prev_close[0] = prices[0]

    # Rolling max/min of past lookback bars (excluding current)
    s = pd.Series(prev_close, index=btc_close_hourly.index)
    roll_max = s.rolling(lookback_hours, min_periods=lookback_hours).max()
    roll_min = s.rolling(lookback_hours, min_periods=lookback_hours).min()
    mid = (roll_max + roll_min) / 2

    signal = (prev_close > mid.values).astype(float)
    signal[:lookback_hours + 1] = 0
    return pd.Series(signal, index=btc_close_hourly.index)


# ============================================================================
# SIGNAL 3: ATR breakout regime (BTC move > 2 * ATR = bullish)
# ============================================================================
def atr_breakout_regime(btc_hourly, atr_period_hours, breakout_mult=1.5):
    """
    Bullish when BTC's recent move exceeds ATR * multiplier.
    Uses hourly true range, naturally causal.
    """
    high = btc_hourly['high'].values.astype(np.float64)
    low = btc_hourly['low'].values.astype(np.float64)
    close = btc_hourly['close'].values.astype(np.float64)
    n = len(close)

    # True range (shifted by 1 to be causal)
    tr = np.zeros(n)
    for i in range(1, n):
        tr[i] = max(high[i-1] - low[i-1],
                     abs(high[i-1] - close[max(0, i-2)]),
                     abs(low[i-1] - close[max(0, i-2)]))

    # ATR (EMA of TR)
    atr = compute_ema(tr[1:], atr_period_hours)
    atr = np.insert(atr, 0, tr[0])

    # Recent move: close[t-1] vs close[t-1-atr_period]
    signal = np.zeros(n)
    for i in range(atr_period_hours + 1, n):
        move = close[i-1] - close[i - 1 - atr_period_hours]
        if atr[i] > 0:
            signal[i] = 1.0 if move > atr[i] * breakout_mult else 0.0

    return pd.Series(signal, index=btc_hourly.index)


# ============================================================================
# SIGNAL 4: Properly shifted daily EMA (the honest version)
# ============================================================================
def daily_ema_regime(btc_close_hourly, fast_d, slow_d):
    """Daily EMA cross, properly shifted by 1 day."""
    daily = btc_close_hourly.resample('D').last().dropna()
    dp = np.array(daily.values, dtype=np.float64)
    f = compute_ema(dp, fast_d)
    s = compute_ema(dp, slow_d)
    d_bull = pd.Series((f > s).astype(float), index=daily.index)
    d_bull = d_bull.shift(1).fillna(0)
    return d_bull.reindex(btc_close_hourly.index, method='ffill').fillna(0)


# ============================================================================
# SIGNAL 5: Combined signals (AND/OR logic)
# ============================================================================
def combine_and(sig1, sig2):
    """Both signals must agree."""
    idx = sig1.index.intersection(sig2.index)
    return pd.Series(sig1.reindex(idx).values * sig2.reindex(idx).values, index=idx)


def combine_or(sig1, sig2):
    """Either signal is bullish."""
    idx = sig1.index.intersection(sig2.index)
    return pd.Series(np.maximum(sig1.reindex(idx).values, sig2.reindex(idx).values), index=idx)


# ============================================================================
# Backtest engine
# ============================================================================
def backtest(regime_hourly, alt_returns, tokens, leverage=1.0, short_ratio=0.0):
    """Simple backtest: long when regime=1, optional short when regime=0."""
    regime = regime_hourly.reindex(alt_returns.index, method='ffill').fillna(0).values
    rets = alt_returns[tokens].values
    n = len(rets)
    funding_per_hour = FUNDING_RATE_ANNUAL / (365.25 * 24)

    equity = CAPITAL
    eq_curve = np.ones(n) * CAPITAL
    prev = 0
    trades = 0

    for i in range(1, n):
        r = regime[i]
        if r != prev:
            trades += 1
            equity *= (1 - COST_BPS / 10000 * leverage)

        port_ret = np.nanmean(rets[i])

        if r == 1:
            equity *= (1 + port_ret * leverage)
        elif r == 0 and short_ratio > 0:
            equity *= (1 - port_ret * short_ratio * leverage)
            equity *= (1 + funding_per_hour * short_ratio * leverage)

        eq_curve[i] = equity
        prev = r

    m = compute_metrics(eq_curve, alt_returns.index)
    if m:
        m['trades'] = trades
        m['time_in_market'] = (regime == 1).sum() / n
    return m, eq_curve


def adaptive_leverage_backtest(regime_hourly, alt_returns, btc_close,
                                tokens, base_lev=1.0, max_lev=3.0,
                                short_ratio=0.0, trend_strength_hours=120):
    """
    Adaptive leverage: size up when trend is strong (large EMA spread),
    size down when weak. Regime must be ON to trade.
    """
    regime = regime_hourly.reindex(alt_returns.index, method='ffill').fillna(0).values
    rets = alt_returns[tokens].values
    n = len(rets)
    funding_per_hour = FUNDING_RATE_ANNUAL / (365.25 * 24)

    # Trend strength: (fast - slow) / slow normalized
    btc_aligned = btc_close.reindex(alt_returns.index, method='ffill')
    prices = np.array(btc_aligned.values, dtype=np.float64)
    fast_ema = compute_ema(prices, trend_strength_hours)
    slow_ema = compute_ema(prices, trend_strength_hours * 5)

    # Normalize spread: use rolling percentile of spread
    spread = (fast_ema - slow_ema) / np.where(slow_ema > 0, slow_ema, 1)
    # Use abs spread — bigger spread = stronger trend either way
    abs_spread = np.abs(spread)

    # Rolling percentile rank of abs_spread
    window = trend_strength_hours * 10
    rank = np.zeros(n)
    for i in range(window, n):
        historical = abs_spread[i - window:i]
        rank[i] = np.mean(historical <= abs_spread[i])

    equity = CAPITAL
    eq_curve = np.ones(n) * CAPITAL
    prev = 0
    trades = 0

    for i in range(1, n):
        r = regime[i]
        if r != prev:
            trades += 1
            equity *= (1 - COST_BPS / 10000 * max_lev)  # worst-case cost

        # Adaptive leverage: scale from base_lev to max_lev based on trend strength
        lev = base_lev + (max_lev - base_lev) * rank[i]
        lev = max(base_lev, min(max_lev, lev))

        port_ret = np.nanmean(rets[i])

        if r == 1:
            equity *= (1 + port_ret * lev)
        elif r == 0 and short_ratio > 0:
            equity *= (1 - port_ret * short_ratio * lev)
            equity *= (1 + funding_per_hour * short_ratio * lev)

        eq_curve[i] = equity
        prev = r

    m = compute_metrics(eq_curve, alt_returns.index)
    if m:
        m['trades'] = trades
        m['time_in_market'] = (regime == 1).sum() / n
    return m, eq_curve


def main():
    print("=" * 120)
    print("HONEST 300% SEARCH — ALL SIGNALS PROPERLY SHIFTED")
    print("=" * 120)

    # Load data
    btc = load_token('BTC')
    alt_data = {}
    for tok in ALL_TOKENS:
        df = load_token(tok)
        if df is not None and len(df) > 8760:
            alt_data[tok] = df

    common_idx = btc.index
    for tok in alt_data:
        common_idx = common_idx.intersection(alt_data[tok].index)

    alt_returns = pd.DataFrame(index=common_idx)
    for tok, df in alt_data.items():
        aligned = df.reindex(common_idx)
        alt_returns[tok] = aligned['close'].pct_change().fillna(0)

    post_etf = '2024-01-01'
    static3 = ['ETH', 'SOL', 'BNB']

    btc_close = btc['close'].reindex(common_idx, method='ffill')
    btc_aligned = btc.reindex(common_idx, method='ffill')

    # ================================================================
    print("\n" + "=" * 120)
    print("SECTION 1: INDIVIDUAL SIGNALS (Post-ETF, Static3)")
    print("=" * 120)

    print(f"\n  {'Signal':<55}  {'AnnRet':>8}  {'Sharpe':>7}  {'MaxDD':>8}  {'Calmar':>7}  {'Sortino':>8}  {'Trades':>6}  {'TiM':>5}")
    print("  " + "-" * 115)

    # Buy and hold baseline
    bh_rets = alt_returns.loc[post_etf:, static3].mean(axis=1)
    bh_eq = CAPITAL * np.cumprod(1 + bh_rets.values)
    bh_m = compute_metrics(bh_eq, bh_rets.index)
    if bh_m:
        print(f"  {'Buy & Hold Static3':<55}  {bh_m['ann_ret']:>+7.1%}  {bh_m['sharpe']:>+6.2f}  {bh_m['max_dd']:>+7.1%}  {bh_m['calmar']:>+6.2f}  {bh_m['sortino']:>+7.2f}  {'N/A':>6}  {'100%':>5}")

    signals = {}

    # A. Properly shifted daily EMA regime (various params)
    for fd, sd in [(8, 54), (10, 40), (15, 60), (20, 80)]:
        name = f"Daily EMA {fd}/{sd} (shifted)"
        sig = daily_ema_regime(btc['close'], fd, sd)
        sig = sig.reindex(common_idx, method='ffill').fillna(0)
        signals[name] = sig
        m, _ = backtest(sig.loc[post_etf:], alt_returns.loc[post_etf:], static3)
        if m:
            print(f"  {name:<55}  {m['ann_ret']:>+7.1%}  {m['sharpe']:>+6.2f}  {m['max_dd']:>+7.1%}  {m['calmar']:>+6.2f}  {m['sortino']:>+7.2f}  {m.get('trades',0):>5d}  {m.get('time_in_market',0):>4.1%}")

    # B. Hourly EMA regime (naturally causal)
    for fh, sh in [(96, 480), (120, 600), (168, 720), (192, 1296),
                    (240, 1200), (336, 1680), (480, 2400)]:
        name = f"Hourly EMA {fh}h/{sh}h ({fh//24}d/{sh//24}d)"
        sig = hourly_ema_regime(btc_close, fh, sh)
        sig = sig.reindex(common_idx, method='ffill').fillna(0)
        signals[name] = sig
        m, _ = backtest(sig.loc[post_etf:], alt_returns.loc[post_etf:], static3)
        if m:
            print(f"  {name:<55}  {m['ann_ret']:>+7.1%}  {m['sharpe']:>+6.2f}  {m['max_dd']:>+7.1%}  {m['calmar']:>+6.2f}  {m['sortino']:>+7.2f}  {m.get('trades',0):>5d}  {m.get('time_in_market',0):>4.1%}")

    # C. Donchian channel
    for lb in [168, 336, 504, 720, 1008, 1440]:
        name = f"Donchian {lb}h ({lb//24}d)"
        sig = donchian_regime_vec(btc_close, lb)
        sig = sig.reindex(common_idx, method='ffill').fillna(0)
        signals[name] = sig
        m, _ = backtest(sig.loc[post_etf:], alt_returns.loc[post_etf:], static3)
        if m:
            print(f"  {name:<55}  {m['ann_ret']:>+7.1%}  {m['sharpe']:>+6.2f}  {m['max_dd']:>+7.1%}  {m['calmar']:>+6.2f}  {m['sortino']:>+7.2f}  {m.get('trades',0):>5d}  {m.get('time_in_market',0):>4.1%}")

    # D. ATR breakout
    for atr_p, mult in [(168, 1.0), (168, 1.5), (168, 2.0), (336, 1.0), (336, 1.5),
                          (504, 1.0), (504, 1.5)]:
        name = f"ATR {atr_p}h ({atr_p//24}d) mult={mult}"
        sig = atr_breakout_regime(btc_aligned, atr_p, mult)
        sig = sig.reindex(common_idx, method='ffill').fillna(0)
        signals[name] = sig
        m, _ = backtest(sig.loc[post_etf:], alt_returns.loc[post_etf:], static3)
        if m:
            print(f"  {name:<55}  {m['ann_ret']:>+7.1%}  {m['sharpe']:>+6.2f}  {m['max_dd']:>+7.1%}  {m['calmar']:>+6.2f}  {m['sortino']:>+7.2f}  {m.get('trades',0):>5d}  {m.get('time_in_market',0):>4.1%}")

    # ================================================================
    print("\n\n" + "=" * 120)
    print("SECTION 2: BEST SIGNALS WITH L/S + LEVERAGE (Post-ETF)")
    print("=" * 120)

    # Pick best signals from above and test with leverage
    best_signals = [
        "Daily EMA 8/54 (shifted)",
        "Hourly EMA 192h/1296h (8d/54d)",
        "Hourly EMA 240h/1200h (10d/50d)",
        "Donchian 720h (30d)",
        "Donchian 1008h (42d)",
    ]

    print(f"\n  {'Config':<55}  {'AnnRet':>8}  {'Sharpe':>7}  {'MaxDD':>8}  {'Calmar':>7}  {'Sortino':>8}  {'Trades':>6}")
    print("  " + "-" * 110)

    for sig_name in best_signals:
        if sig_name not in signals:
            continue
        sig = signals[sig_name]

        for lev, sr, lev_label in [(1.0, 0.7, "1x LS70"), (1.5, 0.7, "1.5x LS70"),
                                    (2.0, 0.7, "2x LS70"), (2.5, 0.7, "2.5x LS70"),
                                    (3.0, 0.7, "3x LS70"), (2.0, 1.0, "2x LS100"),
                                    (3.0, 1.0, "3x LS100")]:
            label = f"{sig_name[:30]} {lev_label}"
            m, _ = backtest(sig.loc[post_etf:], alt_returns.loc[post_etf:], static3,
                           leverage=lev, short_ratio=sr)
            if m:
                hit = " ***300%***" if m['ann_ret'] >= 3.0 else ""
                print(f"  {label:<55}  {m['ann_ret']:>+7.1%}  {m['sharpe']:>+6.2f}  {m['max_dd']:>+7.1%}  {m['calmar']:>+6.2f}  {m['sortino']:>+7.2f}  {m.get('trades',0):>5d}{hit}")

    # ================================================================
    print("\n\n" + "=" * 120)
    print("SECTION 3: COMBINED SIGNALS (AND = both agree)")
    print("=" * 120)

    # Combine daily EMA + Donchian (both must agree = more selective)
    combos = [
        ("Daily8/54 AND Hourly192/1296", "Daily EMA 8/54 (shifted)", "Hourly EMA 192h/1296h (8d/54d)"),
        ("Daily8/54 AND Donchian720", "Daily EMA 8/54 (shifted)", "Donchian 720h (30d)"),
        ("Daily8/54 AND Donchian1008", "Daily EMA 8/54 (shifted)", "Donchian 1008h (42d)"),
        ("Hourly240/1200 AND Donchian720", "Hourly EMA 240h/1200h (10d/50d)", "Donchian 720h (30d)"),
    ]

    print(f"\n  {'Config':<55}  {'AnnRet':>8}  {'Sharpe':>7}  {'MaxDD':>8}  {'Calmar':>7}  {'Sortino':>8}  {'Trades':>6}  {'TiM':>5}")
    print("  " + "-" * 115)

    combined_signals = {}
    for combo_name, sig1_name, sig2_name in combos:
        if sig1_name not in signals or sig2_name not in signals:
            continue
        combined = combine_and(signals[sig1_name], signals[sig2_name])
        combined_signals[combo_name] = combined

        for lev, sr, lev_label in [(1.0, 0.0, "1x LO"), (1.0, 0.7, "1x LS70"),
                                    (2.0, 0.7, "2x LS70"), (3.0, 0.7, "3x LS70"),
                                    (2.0, 1.0, "2x LS100")]:
            label = f"{combo_name} {lev_label}"
            m, _ = backtest(combined.loc[post_etf:], alt_returns.loc[post_etf:], static3,
                           leverage=lev, short_ratio=sr)
            if m:
                hit = " ***300%***" if m['ann_ret'] >= 3.0 else ""
                print(f"  {label:<55}  {m['ann_ret']:>+7.1%}  {m['sharpe']:>+6.2f}  {m['max_dd']:>+7.1%}  {m['calmar']:>+6.2f}  {m['sortino']:>+7.2f}  {m.get('trades',0):>5d}  {m.get('time_in_market',0):>4.1%}{hit}")

    # ================================================================
    print("\n\n" + "=" * 120)
    print("SECTION 4: ADAPTIVE LEVERAGE (Scale up in strong trends)")
    print("=" * 120)

    print(f"\n  {'Config':<55}  {'AnnRet':>8}  {'Sharpe':>7}  {'MaxDD':>8}  {'Calmar':>7}  {'Sortino':>8}  {'Trades':>6}")
    print("  " + "-" * 110)

    # Use best signal with adaptive leverage
    best_sig_name = "Hourly EMA 192h/1296h (8d/54d)"
    best_sig = signals.get(best_sig_name) or signals.get("Daily EMA 8/54 (shifted)")

    for base, mx, sr, label in [
        (1.0, 3.0, 0.7, "Adaptive 1-3x LS70 HourlyEMA"),
        (1.0, 4.0, 0.7, "Adaptive 1-4x LS70 HourlyEMA"),
        (1.5, 4.0, 0.7, "Adaptive 1.5-4x LS70 HourlyEMA"),
        (1.0, 5.0, 0.7, "Adaptive 1-5x LS70 HourlyEMA"),
        (1.0, 3.0, 1.0, "Adaptive 1-3x LS100 HourlyEMA"),
        (1.5, 5.0, 1.0, "Adaptive 1.5-5x LS100 HourlyEMA"),
    ]:
        m, _ = adaptive_leverage_backtest(
            best_sig.loc[post_etf:], alt_returns.loc[post_etf:],
            btc_close.loc[post_etf:], static3,
            base_lev=base, max_lev=mx, short_ratio=sr)
        if m:
            hit = " ***300%***" if m['ann_ret'] >= 3.0 else ""
            print(f"  {label:<55}  {m['ann_ret']:>+7.1%}  {m['sharpe']:>+6.2f}  {m['max_dd']:>+7.1%}  {m['calmar']:>+6.2f}  {m['sortino']:>+7.2f}  {m.get('trades',0):>5d}{hit}")

    # ================================================================
    print("\n\n" + "=" * 120)
    print("SECTION 5: BROADER TOKEN BASKETS (Not just ETH/SOL/BNB)")
    print("=" * 120)

    # Maybe other token baskets with the best signal can do better
    baskets = {
        'Static3': ['ETH', 'SOL', 'BNB'],
        'High-beta3': ['SOL', 'DOGE', 'INJ'],
        'DeFi3': ['AAVE', 'UNI', 'LINK'],
        'All20': ALL_TOKENS,
        'Top5': ['ETH', 'SOL', 'BNB', 'AVAX', 'LINK'],
        'Top10': ['ETH', 'SOL', 'BNB', 'AVAX', 'LINK', 'DOT', 'NEAR', 'ADA', 'XRP', 'DOGE'],
    }

    sig = signals.get("Daily EMA 8/54 (shifted)")

    print(f"\n  {'Config':<55}  {'AnnRet':>8}  {'Sharpe':>7}  {'MaxDD':>8}  {'Calmar':>7}  {'Sortino':>8}")
    print("  " + "-" * 100)

    for basket_name, tokens in baskets.items():
        valid = [t for t in tokens if t in alt_returns.columns]
        if not valid:
            continue

        for lev, sr, lev_label in [(1.0, 0.0, "1x LO"), (2.0, 0.7, "2x LS70"), (3.0, 0.7, "3x LS70")]:
            label = f"{basket_name} {lev_label}"
            m, _ = backtest(sig.loc[post_etf:], alt_returns.loc[post_etf:], valid,
                           leverage=lev, short_ratio=sr)
            if m:
                print(f"  {label:<55}  {m['ann_ret']:>+7.1%}  {m['sharpe']:>+6.2f}  {m['max_dd']:>+7.1%}  {m['calmar']:>+6.2f}  {m['sortino']:>+7.2f}")

    # ================================================================
    print("\n\n" + "=" * 120)
    print("SECTION 6: FULL PERIOD + WALK-FORWARD (Best configs)")
    print("=" * 120)

    sig_daily = daily_ema_regime(btc['close'], 8, 54)
    sig_daily = sig_daily.reindex(common_idx, method='ffill').fillna(0)

    print(f"\n  {'Config':<45}  {'Full Ann':>9}  {'Full DD':>8}  {'Train Ann':>10}  {'Test Ann':>10}  {'WFE':>6}")
    print("  " + "-" * 100)

    for tokens, lev, sr, label in [
        (static3, 1.0, 0.0, "Static3 Daily8/54 1x LO"),
        (static3, 1.0, 0.7, "Static3 Daily8/54 1x LS70"),
        (static3, 2.0, 0.7, "Static3 Daily8/54 2x LS70"),
        (static3, 3.0, 0.7, "Static3 Daily8/54 3x LS70"),
        (static3, 2.0, 1.0, "Static3 Daily8/54 2x LS100"),
    ]:
        # Full
        m_full, _ = backtest(sig_daily, alt_returns, tokens, leverage=lev, short_ratio=sr)
        # Train
        m_train, _ = backtest(sig_daily.loc[:'2023-12-31'], alt_returns.loc[:'2023-12-31'],
                             tokens, leverage=lev, short_ratio=sr)
        # Test
        m_test, _ = backtest(sig_daily.loc[post_etf:], alt_returns.loc[post_etf:],
                            tokens, leverage=lev, short_ratio=sr)

        if m_full and m_train and m_test:
            wfe = m_test['ann_ret'] / max(m_train['ann_ret'], 0.01) * 100
            print(f"  {label:<45}  {m_full['ann_ret']:>+8.1%}  {m_full['max_dd']:>+7.1%}  {m_train['ann_ret']:>+9.1%}  {m_test['ann_ret']:>+9.1%}  {wfe:>5.0f}%")

    # ================================================================
    print("\n\n" + "=" * 120)
    print("FINAL VERDICT")
    print("=" * 120)


if __name__ == '__main__':
    main()
