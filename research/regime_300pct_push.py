#!/usr/bin/env python3
"""Push toward 300%+ post-ETF returns: multi-angle attack.

Approaches:
1. Forward-looking token selection (rolling momentum, no survivorship bias)
2. Intra-regime dip-buying (buy pullbacks within bullish regime)
3. Funding income from shorts (perp funding adds ~12% annualized on short side)
4. Multi-timeframe regime (faster regime detection with 4h confirmation)
5. Strategy stacking (regime alt basket + BTC trend following)
6. Sector rotation (1 L1, 1 DeFi, 1 meme — dynamic)
"""
import numpy as np
import pandas as pd
import warnings
from pathlib import Path
from itertools import product

warnings.filterwarnings('ignore')

DATA_DIR = Path('/workspace/crypto_backtest/data/spot/1h_cache')
PERP_DIR = Path('/workspace/crypto_backtest/data/perp/1h_cache')
CAPITAL = 200_000
COST_BPS = 20  # 0.2% round trip (taker fees)
FUNDING_RATE_ANNUAL = 0.12  # ~12% annualized funding on perps (paid by longs, received by shorts)

# Sector classifications
SECTORS = {
    'L1': ['ETH', 'SOL', 'BNB', 'AVAX', 'NEAR', 'ADA', 'DOT', 'ATOM', 'XLM', 'TRX', 'HBAR'],
    'DeFi': ['UNI', 'AAVE', 'INJ', 'LINK'],
    'Meme': ['DOGE'],
    'Storage': ['FIL'],
    'Legacy': ['LTC', 'BCH', 'XRP'],
}

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


def compute_sma(prices, window):
    p = np.array(prices, dtype=np.float64)
    cs = np.cumsum(np.nan_to_num(p, 0.0))
    cs = np.insert(cs, 0, 0.0)
    out = np.full(len(p), np.nan)
    out[window - 1:] = (cs[window:] - cs[:-window]) / window
    return out


def resample_daily(df):
    return df.resample('D').agg({
        'open': 'first', 'high': 'max', 'low': 'min',
        'close': 'last', 'volume': 'sum'
    }).dropna()


def btc_regime(btc_close_daily, fast=8, slow=54):
    """BTC regime: 1=bullish (fast EMA > slow EMA), 0=bearish."""
    prices = np.array(btc_close_daily.values, dtype=np.float64)
    fast_ema = compute_ema(prices, fast)
    slow_ema = compute_ema(prices, slow)
    signal = (fast_ema > slow_ema).astype(float)
    signal[np.isnan(fast_ema) | np.isnan(slow_ema)] = 0
    return pd.Series(signal, index=btc_close_daily.index)


def compute_metrics(equity_curve, index, capital=CAPITAL):
    """Compute strategy metrics from equity curve."""
    eq = pd.Series(equity_curve, index=index)
    daily_eq = eq.resample('D').last().dropna()
    if len(daily_eq) < 30:
        return None
    daily_rets = daily_eq.pct_change().dropna()
    years = len(daily_rets) / 365.25
    total_ret = (equity_curve[-1] / capital) - 1
    ann_ret = (1 + total_ret) ** (1 / max(years, 0.1)) - 1
    sharpe = daily_rets.mean() / max(daily_rets.std(), 1e-10) * np.sqrt(365.25)
    peak = np.maximum.accumulate(daily_eq.values)
    dd = (daily_eq.values - peak) / peak
    max_dd = np.min(dd)
    calmar = ann_ret / max(abs(max_dd), 0.01)
    neg = daily_rets[daily_rets < 0]
    sortino = daily_rets.mean() / max(neg.std() if len(neg) > 0 else 1e-10, 1e-10) * np.sqrt(365.25)
    return {
        'ann_ret': ann_ret, 'total_ret': total_ret, 'sharpe': sharpe,
        'sortino': sortino, 'max_dd': max_dd, 'calmar': calmar,
        'years': years, 'win_rate': (daily_rets > 0).mean(),
    }


# ============================================================================
# APPROACH 1: Forward-Looking Token Selection (No Survivorship Bias)
# ============================================================================

def backtest_forward_selection(btc_regime_daily, alt_closes_hourly, alt_returns_hourly,
                                n_alts=5, momentum_lookback=60, reselect_freq_days=30,
                                leverage=1.0, short_ratio=0.0, dip_threshold=0.0):
    """
    At each regime flip (and every reselect_freq_days), pick top N alts by
    trailing momentum using ONLY PAST DATA.

    When regime=1: long top N alts (equal weight)
    When regime=0: short top N alts at short_ratio (0=flat, 0.7=70% short)

    dip_threshold: if > 0, only enter long when alt RSI < threshold (buy dips)
    """
    regime_h = btc_regime_daily.reindex(alt_returns_hourly.index, method='ffill').fillna(0)
    regime = regime_h.values

    n_bars = len(alt_returns_hourly)
    tokens = list(alt_returns_hourly.columns)
    n_tokens = len(tokens)

    # Pre-compute all close prices aligned
    closes = alt_closes_hourly.values  # (n_bars, n_tokens)
    returns = alt_returns_hourly.values  # (n_bars, n_tokens)

    # Rolling momentum: trailing N-day return for each token
    lookback_bars = momentum_lookback * 24

    equity = CAPITAL
    equity_curve = np.ones(n_bars) * CAPITAL

    current_selection = list(range(min(n_alts, n_tokens)))  # initial: first N tokens
    prev_regime = 0
    last_reselect = 0
    trades = 0
    cost_per_trade = (COST_BPS / 10000) * leverage

    # Funding income per hour when short
    funding_per_hour = FUNDING_RATE_ANNUAL / (365.25 * 24)

    for i in range(1, n_bars):
        r = regime[i]

        # Check if we need to reselect tokens
        hours_since = i - last_reselect
        regime_changed = (r != prev_regime)
        need_reselect = regime_changed or (hours_since >= reselect_freq_days * 24)

        if need_reselect and i > lookback_bars:
            # Compute trailing momentum for all tokens using ONLY past data
            trail_ret = np.zeros(n_tokens)
            for j in range(n_tokens):
                start_price = closes[i - lookback_bars, j]
                end_price = closes[i, j]
                if start_price > 0 and not np.isnan(start_price) and not np.isnan(end_price):
                    trail_ret[j] = (end_price / start_price) - 1
                else:
                    trail_ret[j] = -999  # exclude

            # Select top N by trailing momentum
            valid = trail_ret > -998
            if valid.sum() >= n_alts:
                ranked = np.argsort(-trail_ret)
                current_selection = [idx for idx in ranked[:n_alts] if valid[idx]]

            last_reselect = i

            # Trade cost on reselection
            if regime_changed:
                trades += 2  # entry + exit
                equity *= (1 - cost_per_trade)

        # Portfolio return this bar
        if r == 1 and len(current_selection) > 0:
            # Long: equal weight across selected alts
            port_ret = np.mean([returns[i, j] for j in current_selection])
            equity *= (1 + port_ret * leverage)
        elif r == 0 and short_ratio > 0 and len(current_selection) > 0:
            # Short: inverse return + funding income
            port_ret = np.mean([returns[i, j] for j in current_selection])
            equity *= (1 - port_ret * short_ratio * leverage)
            # Add funding income (shorts receive funding)
            equity *= (1 + funding_per_hour * short_ratio * leverage)

        equity_curve[i] = equity
        prev_regime = r

    m = compute_metrics(equity_curve, alt_returns_hourly.index)
    if m:
        m['trades'] = trades
    return m, equity_curve


# ============================================================================
# APPROACH 2: Dip-Buying Within Regime
# ============================================================================

def backtest_regime_dipbuy(btc_regime_daily, alt_closes_hourly, alt_returns_hourly,
                            n_alts=3, rsi_period=14, rsi_entry=40, rsi_exit=70,
                            leverage=1.0, short_ratio=0.0):
    """
    Within bullish regime: wait for RSI dip below entry threshold, then go long.
    Exit on RSI > exit threshold or regime flip.
    Within bearish regime: short at short_ratio.
    """
    regime_h = btc_regime_daily.reindex(alt_returns_hourly.index, method='ffill').fillna(0)
    regime = regime_h.values

    tokens = list(alt_returns_hourly.columns)[:n_alts]
    n_bars = len(alt_returns_hourly)
    returns = alt_returns_hourly[tokens].values
    closes = alt_closes_hourly[tokens].values

    # Compute RSI for basket (average close across tokens)
    basket_close = np.nanmean(closes, axis=1)
    basket_close = np.array(basket_close, dtype=np.float64)

    # RSI on 4h bars (resample for less noise)
    # Simple: use hourly RSI with longer period
    delta = np.diff(basket_close, prepend=basket_close[0])
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)

    # EMA-smoothed RSI
    rsi_span = rsi_period * 24  # daily periods in hourly bars
    avg_gain = np.zeros(n_bars)
    avg_loss = np.zeros(n_bars)

    # Initialize with SMA
    init_period = min(rsi_span, n_bars - 1)
    avg_gain[init_period] = np.mean(gain[1:init_period + 1])
    avg_loss[init_period] = np.mean(loss[1:init_period + 1])

    alpha_rsi = 1.0 / rsi_span
    for i in range(init_period + 1, n_bars):
        avg_gain[i] = alpha_rsi * gain[i] + (1 - alpha_rsi) * avg_gain[i - 1]
        avg_loss[i] = alpha_rsi * loss[i] + (1 - alpha_rsi) * avg_loss[i - 1]

    rs = avg_gain / np.where(avg_loss == 0, 1e-10, avg_loss)
    rsi = 100.0 - 100.0 / (1.0 + rs)

    equity = CAPITAL
    equity_curve = np.ones(n_bars) * CAPITAL
    in_position = False
    trades = 0
    cost = COST_BPS / 10000 * leverage
    funding_per_hour = FUNDING_RATE_ANNUAL / (365.25 * 24)

    for i in range(1, n_bars):
        r = regime[i]

        if r == 1:  # Bullish regime
            if not in_position and rsi[i] < rsi_entry:
                # Dip detected — enter long
                in_position = True
                trades += 1
                equity *= (1 - cost)

            if in_position:
                port_ret = np.nanmean(returns[i])
                equity *= (1 + port_ret * leverage)

                if rsi[i] > rsi_exit:
                    in_position = False
                    trades += 1
                    equity *= (1 - cost)
        else:  # Bearish regime
            if in_position:
                in_position = False
                trades += 1
                equity *= (1 - cost)

            if short_ratio > 0:
                port_ret = np.nanmean(returns[i])
                equity *= (1 - port_ret * short_ratio * leverage)
                equity *= (1 + funding_per_hour * short_ratio * leverage)

        equity_curve[i] = equity

    m = compute_metrics(equity_curve, alt_returns_hourly.index)
    if m:
        m['trades'] = trades
    return m, equity_curve


# ============================================================================
# APPROACH 3: Multi-Timeframe Regime (Daily + 4H confirmation)
# ============================================================================

def btc_regime_multi_tf(btc_close_hourly, fast_d=8, slow_d=54, fast_4h=4, slow_4h=20):
    """
    Multi-timeframe regime:
    - Daily: fast/slow EMA cross (trend direction)
    - 4H: faster EMA cross (timing confirmation)

    Enter only when BOTH timeframes agree = bullish.
    """
    # Daily regime
    daily = btc_close_hourly.resample('D').last().dropna()
    d_prices = np.array(daily.values, dtype=np.float64)
    d_fast = compute_ema(d_prices, fast_d)
    d_slow = compute_ema(d_prices, slow_d)
    daily_bull = pd.Series((d_fast > d_slow).astype(float), index=daily.index)
    daily_bull[np.isnan(d_fast) | np.isnan(d_slow)] = 0

    # 4H regime
    h4 = btc_close_hourly.resample('4h').last().dropna()
    h4_prices = np.array(h4.values, dtype=np.float64)
    h4_fast = compute_ema(h4_prices, fast_4h)
    h4_slow = compute_ema(h4_prices, slow_4h)
    h4_bull = pd.Series((h4_fast > h4_slow).astype(float), index=h4.index)
    h4_bull[np.isnan(h4_fast) | np.isnan(h4_slow)] = 0

    # Combine: both must agree
    hourly_idx = btc_close_hourly.index
    d_hourly = daily_bull.reindex(hourly_idx, method='ffill').fillna(0)
    h4_hourly = h4_bull.reindex(hourly_idx, method='ffill').fillna(0)

    combined = (d_hourly.values * h4_hourly.values)
    return pd.Series(combined, index=hourly_idx)


def backtest_simple_regime(regime_hourly, alt_returns_hourly, tokens, leverage=1.0,
                            short_ratio=0.0):
    """Simple regime backtest: long when regime=1, short when regime=0."""
    regime = regime_hourly.reindex(alt_returns_hourly.index, method='ffill').fillna(0).values
    returns = alt_returns_hourly[tokens].values
    n_bars = len(returns)

    equity = CAPITAL
    equity_curve = np.ones(n_bars) * CAPITAL
    prev_r = 0
    trades = 0
    cost = COST_BPS / 10000 * leverage
    funding_per_hour = FUNDING_RATE_ANNUAL / (365.25 * 24)

    for i in range(1, n_bars):
        r = regime[i]
        if r != prev_r:
            trades += 1
            equity *= (1 - cost)

        port_ret = np.nanmean(returns[i])

        if r == 1:
            equity *= (1 + port_ret * leverage)
        elif short_ratio > 0:
            equity *= (1 - port_ret * short_ratio * leverage)
            equity *= (1 + funding_per_hour * short_ratio * leverage)

        equity_curve[i] = equity
        prev_r = r

    m = compute_metrics(equity_curve, alt_returns_hourly.index)
    if m:
        m['trades'] = trades
    return m, equity_curve


# ============================================================================
# APPROACH 4: Sector Rotation (best from each sector)
# ============================================================================

def select_sector_tokens(alt_closes, sector_map, momentum_days=60, n_per_sector=1):
    """Select top token from each sector by trailing momentum."""
    lookback = momentum_days * 24
    selections = {}

    for sector, tokens in sector_map.items():
        available = [t for t in tokens if t in alt_closes.columns]
        if not available:
            continue

        # Get last available data
        last_idx = len(alt_closes) - 1
        start_idx = max(0, last_idx - lookback)

        mom = {}
        for t in available:
            start_p = alt_closes[t].iloc[start_idx]
            end_p = alt_closes[t].iloc[last_idx]
            if start_p > 0 and not np.isnan(start_p) and not np.isnan(end_p):
                mom[t] = (end_p / start_p) - 1

        if mom:
            sorted_tokens = sorted(mom, key=lambda x: mom[x], reverse=True)
            selections[sector] = sorted_tokens[:n_per_sector]

    return selections


# ============================================================================
# APPROACH 5: Strategy Stacking (Regime + BTC Trend + Mean Reversion)
# ============================================================================

def btc_trend_following(btc_close_hourly, fast=20, slow=50, leverage=1.0):
    """Simple BTC trend following on hourly data. Returns equity curve."""
    prices = np.array(btc_close_hourly.values, dtype=np.float64)
    returns = np.diff(prices, prepend=prices[0]) / np.where(prices == 0, 1, prices)
    returns[0] = 0

    fast_ema = compute_ema(prices, fast * 24)
    slow_ema = compute_ema(prices, slow * 24)

    signal = (fast_ema > slow_ema).astype(float)
    signal[np.isnan(fast_ema) | np.isnan(slow_ema)] = 0

    equity = CAPITAL * 0.3  # 30% allocation to BTC trend
    equity_curve = np.ones(len(prices)) * equity
    cost = COST_BPS / 10000
    prev_s = 0

    for i in range(1, len(prices)):
        s = signal[i]
        if s != prev_s:
            equity *= (1 - cost)
        if s == 1:
            equity *= (1 + returns[i] * leverage)
        equity_curve[i] = equity
        prev_s = s

    return equity_curve


# ============================================================================
# MAIN: RUN ALL APPROACHES
# ============================================================================

def main():
    print("=" * 120)
    print("300%+ POST-ETF PUSH: MULTI-ANGLE ATTACK")
    print("=" * 120)

    # ── LOAD DATA ──
    print("\n[LOADING DATA]")
    btc = load_token('BTC')
    if btc is None:
        print("ERROR: No BTC data"); return

    btc_daily = resample_daily(btc)
    print(f"  BTC: {btc.index[0]} to {btc.index[-1]} ({len(btc)} bars)")

    alt_data = {}
    for tok in ALL_TOKENS:
        df = load_token(tok)
        if df is not None and len(df) > 8760:
            alt_data[tok] = df

    print(f"  Loaded {len(alt_data)} alt tokens")

    # Common index
    common_idx = btc.index
    for tok in alt_data:
        common_idx = common_idx.intersection(alt_data[tok].index)

    alt_closes = pd.DataFrame(index=common_idx)
    alt_returns = pd.DataFrame(index=common_idx)
    for tok, df in alt_data.items():
        aligned = df.reindex(common_idx)
        alt_closes[tok] = aligned['close']
        alt_returns[tok] = aligned['close'].pct_change().fillna(0)

    available = list(alt_data.keys())
    print(f"  Common: {common_idx[0]} to {common_idx[-1]} ({len(common_idx)} bars)")

    # BTC regime (best params from prior research)
    regime_daily = btc_regime(btc_daily['close'], fast=8, slow=54)
    regime_hourly = regime_daily.reindex(common_idx, method='ffill').fillna(0)

    # Post-ETF cutoff
    post_etf = '2024-01-01'
    post_etf_mask = common_idx >= post_etf
    post_idx = common_idx[post_etf_mask]

    print(f"  Post-ETF: {post_idx[0]} to {post_idx[-1]} ({len(post_idx)} bars, {len(post_idx)/24/365.25:.2f} yrs)")

    # Static top 3 (known best from prior research — for comparison baseline)
    static_top3 = ['ETH', 'SOL', 'BNB']

    # ================================================================
    print("\n" + "=" * 120)
    print("APPROACH 1: FORWARD-LOOKING TOKEN SELECTION (No Survivorship Bias)")
    print("=" * 120)

    print("\n  Testing rolling momentum selection vs static top 3...")
    print(f"\n  {'Config':<55}  {'AnnRet':>8}  {'Sharpe':>7}  {'MaxDD':>8}  {'Calmar':>7}  {'Sortino':>8}  {'Trades':>6}")
    print("  " + "-" * 110)

    configs = [
        # (n_alts, mom_lookback, reselect_days, leverage, short_ratio, label)
        (3, 30, 14, 1.0, 0.0, "Mom30d N=3 Resel14d 1x LO"),
        (3, 60, 14, 1.0, 0.0, "Mom60d N=3 Resel14d 1x LO"),
        (3, 90, 14, 1.0, 0.0, "Mom90d N=3 Resel14d 1x LO"),
        (5, 30, 14, 1.0, 0.0, "Mom30d N=5 Resel14d 1x LO"),
        (5, 60, 14, 1.0, 0.0, "Mom60d N=5 Resel14d 1x LO"),
        (5, 60, 30, 1.0, 0.0, "Mom60d N=5 Resel30d 1x LO"),
        (3, 60, 14, 1.0, 0.7, "Mom60d N=3 Resel14d 1x LS70"),
        (5, 60, 14, 1.0, 0.7, "Mom60d N=5 Resel14d 1x LS70"),
        (3, 60, 14, 2.0, 0.0, "Mom60d N=3 Resel14d 2x LO"),
        (3, 60, 14, 2.0, 0.7, "Mom60d N=3 Resel14d 2x LS70"),
        (3, 60, 14, 3.0, 0.7, "Mom60d N=3 Resel14d 3x LS70"),
        (5, 60, 14, 2.0, 0.7, "Mom60d N=5 Resel14d 2x LS70"),
        (5, 60, 14, 3.0, 0.7, "Mom60d N=5 Resel14d 3x LS70"),
    ]

    results_1 = []
    for n, mom, resel, lev, sr, label in configs:
        # Full period
        m_full, eq_full = backtest_forward_selection(
            regime_daily, alt_closes, alt_returns,
            n_alts=n, momentum_lookback=mom, reselect_freq_days=resel,
            leverage=lev, short_ratio=sr)

        # Post-ETF
        post_alt_closes = alt_closes.loc[post_etf:]
        post_alt_returns = alt_returns.loc[post_etf:]
        m_post, eq_post = backtest_forward_selection(
            regime_daily, post_alt_closes, post_alt_returns,
            n_alts=n, momentum_lookback=mom, reselect_freq_days=resel,
            leverage=lev, short_ratio=sr)

        if m_post:
            print(f"  {label:<55}  {m_post['ann_ret']:>+7.1%}  {m_post['sharpe']:>+6.2f}  {m_post['max_dd']:>+7.1%}  {m_post['calmar']:>+6.2f}  {m_post['sortino']:>+7.2f}  {m_post.get('trades',0):>5}")
            results_1.append({'label': label, 'period': 'post_etf', **m_post})

        if m_full:
            results_1.append({'label': label, 'period': 'full', **m_full})

    # Comparison: static top 3
    print("\n  --- Baseline: Static Top 3 (ETH/SOL/BNB) ---")
    for lev, sr, label in [(1.0, 0.0, "Static3 1x LO"), (1.0, 0.7, "Static3 1x LS70"),
                            (2.0, 0.7, "Static3 2x LS70"), (3.0, 0.7, "Static3 3x LS70")]:
        post_alt_returns_3 = alt_returns.loc[post_etf:, static_top3]
        m, _ = backtest_simple_regime(regime_hourly, alt_returns.loc[post_etf:], static_top3,
                                       leverage=lev, short_ratio=sr)
        if m:
            print(f"  {label:<55}  {m['ann_ret']:>+7.1%}  {m['sharpe']:>+6.2f}  {m['max_dd']:>+7.1%}  {m['calmar']:>+6.2f}  {m['sortino']:>+7.2f}  {m.get('trades',0):>5}")

    # ================================================================
    print("\n" + "=" * 120)
    print("APPROACH 2: INTRA-REGIME DIP-BUYING (RSI pullbacks within bullish regime)")
    print("=" * 120)

    print(f"\n  {'Config':<55}  {'AnnRet':>8}  {'Sharpe':>7}  {'MaxDD':>8}  {'Calmar':>7}  {'Sortino':>8}  {'Trades':>6}")
    print("  " + "-" * 110)

    dip_configs = [
        # (n_alts, rsi_period, rsi_entry, rsi_exit, leverage, short_ratio, label)
        (3, 14, 40, 70, 1.0, 0.0, "Top3 RSI14 entry40/exit70 1x LO"),
        (3, 14, 35, 65, 1.0, 0.0, "Top3 RSI14 entry35/exit65 1x LO"),
        (3, 14, 45, 75, 1.0, 0.0, "Top3 RSI14 entry45/exit75 1x LO"),
        (3, 7, 40, 70, 1.0, 0.0, "Top3 RSI7 entry40/exit70 1x LO"),
        (3, 14, 40, 70, 1.0, 0.7, "Top3 RSI14 entry40/exit70 1x LS70"),
        (3, 14, 40, 70, 2.0, 0.7, "Top3 RSI14 entry40/exit70 2x LS70"),
        (3, 14, 40, 70, 3.0, 0.7, "Top3 RSI14 entry40/exit70 3x LS70"),
        (5, 14, 40, 70, 2.0, 0.7, "Top5 RSI14 entry40/exit70 2x LS70"),
    ]

    for n, rsi_p, rsi_e, rsi_x, lev, sr, label in dip_configs:
        tokens = static_top3[:n] if n <= 3 else available[:n]
        post_closes = alt_closes.loc[post_etf:, tokens]
        post_returns = alt_returns.loc[post_etf:, tokens]
        m, _ = backtest_regime_dipbuy(
            regime_daily, post_closes, post_returns,
            n_alts=n, rsi_period=rsi_p, rsi_entry=rsi_e, rsi_exit=rsi_x,
            leverage=lev, short_ratio=sr)
        if m:
            print(f"  {label:<55}  {m['ann_ret']:>+7.1%}  {m['sharpe']:>+6.2f}  {m['max_dd']:>+7.1%}  {m['calmar']:>+6.2f}  {m['sortino']:>+7.2f}  {m.get('trades',0):>5}")

    # ================================================================
    print("\n" + "=" * 120)
    print("APPROACH 3: MULTI-TIMEFRAME REGIME (Daily + 4H confirmation)")
    print("=" * 120)

    print(f"\n  {'Config':<55}  {'AnnRet':>8}  {'Sharpe':>7}  {'MaxDD':>8}  {'Calmar':>7}  {'Sortino':>8}  {'Trades':>6}")
    print("  " + "-" * 110)

    mtf_configs = [
        # (fast_d, slow_d, fast_4h, slow_4h, n_alts, leverage, short_ratio, label)
        (8, 54, 4, 20, 3, 1.0, 0.0, "D8/54 4H4/20 Top3 1x LO"),
        (8, 54, 6, 24, 3, 1.0, 0.0, "D8/54 4H6/24 Top3 1x LO"),
        (8, 54, 4, 20, 3, 1.0, 0.7, "D8/54 4H4/20 Top3 1x LS70"),
        (8, 54, 4, 20, 3, 2.0, 0.7, "D8/54 4H4/20 Top3 2x LS70"),
        (8, 54, 4, 20, 3, 3.0, 0.7, "D8/54 4H4/20 Top3 3x LS70"),
        (8, 54, 4, 20, 5, 2.0, 0.7, "D8/54 4H4/20 Top5 2x LS70"),
        (10, 40, 4, 20, 3, 2.0, 0.7, "D10/40 4H4/20 Top3 2x LS70"),
        (8, 54, 8, 32, 3, 2.0, 0.7, "D8/54 4H8/32 Top3 2x LS70"),
    ]

    for fd, sd, f4, s4, n, lev, sr, label in mtf_configs:
        # Multi-TF regime
        mtf_regime = btc_regime_multi_tf(btc['close'], fast_d=fd, slow_d=sd,
                                          fast_4h=f4, slow_4h=s4)

        tokens = static_top3[:n] if n <= 3 else available[:n]
        m, _ = backtest_simple_regime(mtf_regime.loc[post_etf:], alt_returns.loc[post_etf:],
                                       tokens, leverage=lev, short_ratio=sr)
        if m:
            print(f"  {label:<55}  {m['ann_ret']:>+7.1%}  {m['sharpe']:>+6.2f}  {m['max_dd']:>+7.1%}  {m['calmar']:>+6.2f}  {m['sortino']:>+7.2f}  {m.get('trades',0):>5}")

    # ================================================================
    print("\n" + "=" * 120)
    print("APPROACH 4: SECTOR ROTATION (Best from each sector)")
    print("=" * 120)

    print(f"\n  {'Config':<55}  {'AnnRet':>8}  {'Sharpe':>7}  {'MaxDD':>8}  {'Calmar':>7}  {'Sortino':>8}  {'Trades':>6}")
    print("  " + "-" * 110)

    # Different sector combos
    sector_combos = [
        (['ETH', 'DOGE', 'AAVE'], "ETH+DOGE+AAVE (L1+Meme+DeFi)"),
        (['SOL', 'DOGE', 'LINK'], "SOL+DOGE+LINK (L1+Meme+DeFi)"),
        (['ETH', 'SOL', 'DOGE'], "ETH+SOL+DOGE (L1+L1+Meme)"),
        (['BNB', 'DOGE', 'AAVE'], "BNB+DOGE+AAVE (L1+Meme+DeFi)"),
        (['ETH', 'SOL', 'LINK'], "ETH+SOL+LINK (2×L1+DeFi)"),
        (['ETH', 'SOL', 'BNB', 'DOGE', 'LINK'], "5-sector (L1×3+Meme+DeFi)"),
    ]

    for tokens, label in sector_combos:
        valid = [t for t in tokens if t in alt_returns.columns]
        if not valid:
            continue

        for lev, sr, lev_label in [(1.0, 0.0, "1x LO"), (2.0, 0.7, "2x LS70"), (3.0, 0.7, "3x LS70")]:
            full_label = f"{label} {lev_label}"
            m, _ = backtest_simple_regime(regime_hourly.loc[post_etf:], alt_returns.loc[post_etf:],
                                           valid, leverage=lev, short_ratio=sr)
            if m:
                print(f"  {full_label:<55}  {m['ann_ret']:>+7.1%}  {m['sharpe']:>+6.2f}  {m['max_dd']:>+7.1%}  {m['calmar']:>+6.2f}  {m['sortino']:>+7.2f}  {m.get('trades',0):>5}")

    # ================================================================
    print("\n" + "=" * 120)
    print("APPROACH 5: STRATEGY STACKING (Regime Alt Basket + BTC Trend)")
    print("=" * 120)

    print("\n  Combining 70% regime alt basket + 30% BTC trend following...")
    print(f"\n  {'Config':<55}  {'AnnRet':>8}  {'Sharpe':>7}  {'MaxDD':>8}  {'Calmar':>7}  {'Sortino':>8}")
    print("  " + "-" * 100)

    # BTC trend curve (30% allocation)
    btc_trend_eq = btc_trend_following(btc['close'].loc[post_etf:], fast=20, slow=50, leverage=1.0)

    for lev, sr, label in [(1.0, 0.0, "70% Alt3 LO + 30% BTC trend"),
                            (1.5, 0.0, "70% Alt3 1.5x LO + 30% BTC 1x"),
                            (2.0, 0.7, "70% Alt3 2x LS70 + 30% BTC 1.5x"),
                            (2.5, 0.7, "70% Alt3 2.5x LS70 + 30% BTC 2x")]:
        # Alt basket part (70% allocation)
        alt_capital = CAPITAL * 0.7
        m_alt, eq_alt = backtest_simple_regime(
            regime_hourly.loc[post_etf:], alt_returns.loc[post_etf:],
            static_top3, leverage=lev, short_ratio=sr)

        if m_alt is None:
            continue

        # Scale alt equity to 70% allocation
        eq_alt_scaled = eq_alt * (alt_capital / CAPITAL)

        # BTC trend (30%)
        btc_lev = 1.0 if lev <= 1.5 else (1.5 if lev <= 2.0 else 2.0)
        btc_eq = btc_trend_following(btc['close'].loc[post_etf:], fast=20, slow=50, leverage=btc_lev)

        # Combined equity
        min_len = min(len(eq_alt_scaled), len(btc_eq))
        combined_eq = eq_alt_scaled[:min_len] + btc_eq[:min_len]

        idx = alt_returns.loc[post_etf:].index[:min_len]
        m_comb = compute_metrics(combined_eq, idx, capital=CAPITAL)
        if m_comb:
            print(f"  {label:<55}  {m_comb['ann_ret']:>+7.1%}  {m_comb['sharpe']:>+6.2f}  {m_comb['max_dd']:>+7.1%}  {m_comb['calmar']:>+6.2f}  {m_comb['sortino']:>+7.2f}")

    # ================================================================
    print("\n" + "=" * 120)
    print("APPROACH 6: AGGRESSIVE MOMENTUM ROTATOR (Weekly rebalance)")
    print("=" * 120)

    print("\n  Weekly rebalance into top N by momentum, regime-gated...")
    print(f"\n  {'Config':<55}  {'AnnRet':>8}  {'Sharpe':>7}  {'MaxDD':>8}  {'Calmar':>7}  {'Sortino':>8}  {'Trades':>6}")
    print("  " + "-" * 110)

    for n, mom, resel, lev, sr, label in [
        (3, 14, 7, 2.0, 0.7, "Mom14d N=3 Weekly 2x LS70"),
        (3, 21, 7, 2.0, 0.7, "Mom21d N=3 Weekly 2x LS70"),
        (3, 7, 7, 2.0, 0.7, "Mom7d N=3 Weekly 2x LS70"),
        (3, 14, 7, 3.0, 0.7, "Mom14d N=3 Weekly 3x LS70"),
        (5, 14, 7, 2.0, 0.7, "Mom14d N=5 Weekly 2x LS70"),
        (3, 14, 7, 2.0, 1.0, "Mom14d N=3 Weekly 2x LS100"),
        (3, 14, 7, 3.0, 1.0, "Mom14d N=3 Weekly 3x LS100"),
        (2, 14, 7, 3.0, 0.7, "Mom14d N=2 Weekly 3x LS70"),
        (2, 7, 7, 3.0, 1.0, "Mom7d N=2 Weekly 3x LS100"),
    ]:
        post_closes = alt_closes.loc[post_etf:]
        post_returns = alt_returns.loc[post_etf:]
        m, _ = backtest_forward_selection(
            regime_daily, post_closes, post_returns,
            n_alts=n, momentum_lookback=mom, reselect_freq_days=resel,
            leverage=lev, short_ratio=sr)
        if m:
            print(f"  {label:<55}  {m['ann_ret']:>+7.1%}  {m['sharpe']:>+6.2f}  {m['max_dd']:>+7.1%}  {m['calmar']:>+6.2f}  {m['sortino']:>+7.2f}  {m.get('trades',0):>5}")

    # ================================================================
    print("\n" + "=" * 120)
    print("APPROACH 7: LEVERAGED L/S WITH CONCENTRATED BETS")
    print("=" * 120)

    print("\n  High conviction: fewer tokens, more leverage, full short...")
    print(f"\n  {'Config':<55}  {'AnnRet':>8}  {'Sharpe':>7}  {'MaxDD':>8}  {'Calmar':>7}  {'Sortino':>8}  {'Trades':>6}")
    print("  " + "-" * 110)

    for tokens, lev, sr, label in [
        (['SOL'], 3.0, 1.0, "SOL only 3x LS100"),
        (['SOL'], 4.0, 1.0, "SOL only 4x LS100"),
        (['SOL'], 5.0, 1.0, "SOL only 5x LS100"),
        (['ETH'], 3.0, 1.0, "ETH only 3x LS100"),
        (['ETH'], 5.0, 1.0, "ETH only 5x LS100"),
        (['SOL', 'ETH'], 3.0, 1.0, "SOL+ETH 3x LS100"),
        (['SOL', 'ETH'], 4.0, 1.0, "SOL+ETH 4x LS100"),
        (['SOL', 'ETH'], 5.0, 1.0, "SOL+ETH 5x LS100"),
        (['BNB'], 3.0, 1.0, "BNB only 3x LS100"),
        (['BNB'], 5.0, 1.0, "BNB only 5x LS100"),
        (['SOL', 'BNB'], 4.0, 1.0, "SOL+BNB 4x LS100"),
        (['ETH', 'SOL', 'BNB'], 4.0, 1.0, "Top3 4x LS100"),
        (['ETH', 'SOL', 'BNB'], 5.0, 1.0, "Top3 5x LS100"),
    ]:
        valid = [t for t in tokens if t in alt_returns.columns]
        if not valid:
            continue
        m, _ = backtest_simple_regime(regime_hourly.loc[post_etf:], alt_returns.loc[post_etf:],
                                       valid, leverage=lev, short_ratio=sr)
        if m:
            print(f"  {label:<55}  {m['ann_ret']:>+7.1%}  {m['sharpe']:>+6.2f}  {m['max_dd']:>+7.1%}  {m['calmar']:>+6.2f}  {m['sortino']:>+7.2f}  {m.get('trades',0):>5}")

    # ================================================================
    print("\n" + "=" * 120)
    print("APPROACH 8: COMBINED BEST — STACKED PORTFOLIO")
    print("=" * 120)

    print("\n  Combining best approaches into one portfolio...")

    # Best approach: likely some variant of leveraged L/S regime on concentrated basket
    # Let's test different allocation splits

    allocations = [
        # (alt_pct, btc_pct, alt_tokens, alt_lev, alt_sr, btc_lev, label)
        (0.6, 0.4, static_top3, 3.0, 0.7, 2.0, "60% Alt3 3x LS70 + 40% BTC 2x"),
        (0.5, 0.5, static_top3, 3.0, 1.0, 2.0, "50% Alt3 3x LS100 + 50% BTC 2x"),
        (0.7, 0.3, static_top3, 3.0, 1.0, 1.5, "70% Alt3 3x LS100 + 30% BTC 1.5x"),
        (0.5, 0.5, ['SOL', 'ETH'], 4.0, 1.0, 2.0, "50% SOL+ETH 4x LS100 + 50% BTC 2x"),
        (0.6, 0.4, ['SOL', 'ETH'], 5.0, 1.0, 2.0, "60% SOL+ETH 5x LS100 + 40% BTC 2x"),
    ]

    print(f"\n  {'Config':<55}  {'AnnRet':>8}  {'Sharpe':>7}  {'MaxDD':>8}  {'Calmar':>7}  {'Sortino':>8}")
    print("  " + "-" * 100)

    for alt_pct, btc_pct, alt_toks, alt_lev, alt_sr, btc_lev, label in allocations:
        valid_toks = [t for t in alt_toks if t in alt_returns.columns]
        if not valid_toks:
            continue

        # Alt basket
        _, eq_alt = backtest_simple_regime(
            regime_hourly.loc[post_etf:], alt_returns.loc[post_etf:],
            valid_toks, leverage=alt_lev, short_ratio=alt_sr)

        # BTC trend
        eq_btc = btc_trend_following(btc['close'].loc[post_etf:], fast=20, slow=50, leverage=btc_lev)

        min_len = min(len(eq_alt), len(eq_btc))

        # Scale to allocation
        eq_alt_s = eq_alt[:min_len] * (alt_pct * CAPITAL / CAPITAL)
        eq_btc_s = eq_btc[:min_len] * (btc_pct * CAPITAL / (CAPITAL * 0.3))  # btc_trend uses 30% base

        combined = eq_alt_s + eq_btc_s
        idx = alt_returns.loc[post_etf:].index[:min_len]
        m = compute_metrics(combined, idx, capital=CAPITAL)
        if m:
            print(f"  {label:<55}  {m['ann_ret']:>+7.1%}  {m['sharpe']:>+6.2f}  {m['max_dd']:>+7.1%}  {m['calmar']:>+6.2f}  {m['sortino']:>+7.2f}")

    # ================================================================
    print("\n" + "=" * 120)
    print("EXECUTIVE SUMMARY")
    print("=" * 120)
    print("""
    Target: 300%+ annual return post-ETF (Jan 2024+)
    Constraint: Max drawdown < 50% preferred, < 75% acceptable

    Results above show the full matrix. Key question: can we get to 300% post-ETF
    without survivorship bias and with acceptable drawdown?
    """)


if __name__ == '__main__':
    main()
