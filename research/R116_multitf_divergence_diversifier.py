#!/workspace/venv/bin/python
"""
R116: Multi-Timeframe Divergence as BTC Portfolio Diversifier
==============================================================

Objective:
  Test multi-timeframe divergence signals — situations where short and long
  timeframe indicators disagree — as a portfolio diversifier for V3 (EMA
  trend-following). V3 uses daily 20/50 EMA. Signals that fire when DIFFERENT
  timeframes disagree should be uncorrelated with V3.

Hypothesis:
  When shorter-term and longer-term momentum disagree, it predicts:
    1. Trend exhaustion (long bullish but short turning -> reversal coming)
    2. Trend initiation (short bullish but long still bearish -> new trend starting)
    3. Whipsaw risk (multiple timeframes in conflict -> reduce exposure)

Signal Candidates:
  S1. 4h/Daily RSI divergence — 4h RSI vs daily RSI disagreement
  S2. EMA stack alignment — count of [8h, 1d, 3d, 7d] EMAs agreeing on direction
  S3. Short/Long momentum divergence — sign(24h ret) != sign(168h ret)
  S4. Acceleration divergence — 1d ROC positive but 7d ROC decelerating
  S5. Volatility-adjusted divergence — distance from EMA20/EMA50 normalized by ATR
  S6. Mean-reversion timing — daily trend bullish but 4h RSI < 30 (pullback in uptrend)

Methodology:
  1. Compute all signals from BTC 1h/4h/daily data
  2. IC scan at 1d, 3d, 7d, 14d horizons
  3. For top signals: deep walk-forward (10 windows, 180d train / 90d test)
  4. Check correlation with V3 returns (must be decorrelated)
  5. Regime analysis: which regimes do divergence signals work best in?

Kill Criteria:
  - IC < 0.02 across all horizons -> KILL
  - Correlation with V3 > 0.5 -> KILL
  - WF mean Sharpe < 0.3 -> KILL
  - < 5/10 WF windows positive -> KILL

Key Context:
  - V3 uses 20/50 EMA daily crossover with weekly rebalance
  - V3's weakness is RANGE markets (Sharpe -1.01 in range)
  - A good diversifier would specifically work in range/transition regimes
  - V3 already uses 4h RSI for ENTRY TIMING (threshold 35, within rebalance window).
    Any RSI-based signal here must be fundamentally different.

Author: Quant Research Agent
Date: 2026-03-24
"""

import warnings
import numpy as np
import pandas as pd
from scipy import stats
from pathlib import Path
from datetime import timedelta
from itertools import product

warnings.filterwarnings('ignore')

# ── Paths ──────────────────────────────────────────────────────────────────────
PROJECT_DIR = Path('/workspace/crypto_backtest')
DATA_DIR = PROJECT_DIR / 'data'
OUTPUT_PY = PROJECT_DIR / 'research' / 'R116_multitf_divergence_diversifier.py'
OUTPUT_MD = PROJECT_DIR / 'research' / 'R116_multitf_divergence_diversifier.md'

COST_BPS = 10  # round-trip cost in basis points
ANNUALIZE = np.sqrt(365)  # daily -> annual

# ── Walk-Forward Config ────────────────────────────────────────────────────────
TRAIN_DAYS = 180
TEST_DAYS = 90
ROLL_DAYS = 90
N_WINDOWS = 10


# ══════════════════════════════════════════════════════════════════════════════
# DATA LOADING
# ══════════════════════════════════════════════════════════════════════════════

def load_btc_1h():
    """Load BTC 1h spot data."""
    print("[DATA] Loading BTC 1h spot...")
    df = pd.read_parquet(DATA_DIR / 'spot/1h_cache/BTC_1h.parquet')
    df.index = pd.to_datetime(df.index)
    df = df.sort_index()
    df = df[~df.index.duplicated(keep='first')]
    print(f"  BTC 1h: {df.index.min().date()} to {df.index.max().date()}, {len(df)} bars")
    return df


def resample_timeframes(btc_1h):
    """Create 4h and daily OHLCV from 1h data."""
    print("[DATA] Resampling to 4h and daily...")

    # 4h bars
    btc_4h = pd.DataFrame()
    btc_4h['open'] = btc_1h['open'].resample('4h').first()
    btc_4h['high'] = btc_1h['high'].resample('4h').max()
    btc_4h['low'] = btc_1h['low'].resample('4h').min()
    btc_4h['close'] = btc_1h['close'].resample('4h').last()
    btc_4h['volume'] = btc_1h['volume'].resample('4h').sum()
    btc_4h = btc_4h.dropna()
    print(f"  BTC 4h: {btc_4h.index.min().date()} to {btc_4h.index.max().date()}, {len(btc_4h)} bars")

    # Daily bars
    btc_daily = pd.DataFrame()
    btc_daily['open'] = btc_1h['open'].resample('1D').first()
    btc_daily['high'] = btc_1h['high'].resample('1D').max()
    btc_daily['low'] = btc_1h['low'].resample('1D').min()
    btc_daily['close'] = btc_1h['close'].resample('1D').last()
    btc_daily['volume'] = btc_1h['volume'].resample('1D').sum()
    btc_daily = btc_daily.dropna()
    btc_daily['ret'] = btc_daily['close'].pct_change()
    print(f"  BTC daily: {btc_daily.index.min().date()} to {btc_daily.index.max().date()}, {len(btc_daily)} days")

    return btc_4h, btc_daily


# ══════════════════════════════════════════════════════════════════════════════
# RSI HELPER
# ══════════════════════════════════════════════════════════════════════════════

def compute_rsi(close, period=14):
    """Compute RSI using exponential moving average method."""
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(span=period, adjust=False).mean()
    avg_loss = loss.ewm(span=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi


# ══════════════════════════════════════════════════════════════════════════════
# V3 MOMENTUM BASELINE
# ══════════════════════════════════════════════════════════════════════════════

def compute_v3_returns(btc_daily):
    """
    Compute V3 base signal (20/50 EMA crossover) daily returns.
    V3 base = long when EMA20 > EMA50, flat otherwise. Weekly rebalance.
    """
    print("[V3] Computing V3 baseline returns...")
    ema20 = btc_daily['close'].ewm(span=20, adjust=False).mean()
    ema50 = btc_daily['close'].ewm(span=50, adjust=False).mean()

    # Signal: 1 when EMA20 > EMA50, else 0
    raw_signal = (ema20 > ema50).astype(float)

    # Weekly rebalance (Mondays only)
    signal = raw_signal.copy()
    is_monday = btc_daily.index.dayofweek == 0
    signal[~is_monday] = np.nan
    signal = signal.ffill().fillna(0)

    # Apply signal to next day's return (no look-ahead)
    signal_shifted = signal.shift(1)
    trades = signal_shifted.diff().abs()
    trade_cost = trades * (COST_BPS / 10000)
    v3_ret = signal_shifted * btc_daily['ret'] - trade_cost

    print(f"  V3 Sharpe: {v3_ret.dropna().mean() / v3_ret.dropna().std() * ANNUALIZE:.3f}")
    return v3_ret.dropna()


# ══════════════════════════════════════════════════════════════════════════════
# REGIME CLASSIFICATION
# ══════════════════════════════════════════════════════════════════════════════

def classify_regimes(btc_daily):
    """
    Classify market regimes.
    UPTREND:   50d SMA > 200d SMA AND close > 50d SMA
    DOWNTREND: 50d SMA < 200d SMA AND close < 50d SMA
    RANGE:     otherwise
    """
    sma50 = btc_daily['close'].rolling(50).mean()
    sma200 = btc_daily['close'].rolling(200).mean()

    regime = pd.Series('RANGE', index=btc_daily.index)
    uptrend = (sma50 > sma200) & (btc_daily['close'] > sma50)
    downtrend = (sma50 < sma200) & (btc_daily['close'] < sma50)
    regime[uptrend] = 'UPTREND'
    regime[downtrend] = 'DOWNTREND'

    return regime


# ══════════════════════════════════════════════════════════════════════════════
# SIGNAL GENERATION
# ══════════════════════════════════════════════════════════════════════════════

def signal_s1_rsi_divergence(btc_4h, btc_daily, rsi_period_4h=14, rsi_period_d=14):
    """
    S1: 4h/Daily RSI Divergence
    Signal = daily_rsi - 4h_rsi (resampled to daily).
    When 4h bearish (low RSI) but daily bullish (high RSI) -> negative divergence.
    Use as a continuous signal for IC analysis.
    """
    rsi_4h = compute_rsi(btc_4h['close'], period=rsi_period_4h)
    rsi_daily = compute_rsi(btc_daily['close'], period=rsi_period_d)

    # Resample 4h RSI to daily (end-of-day value)
    rsi_4h_daily = rsi_4h.resample('1D').last().dropna()

    # Align indices
    common = rsi_4h_daily.index.intersection(rsi_daily.index)
    divergence = rsi_daily.loc[common] - rsi_4h_daily.loc[common]

    return divergence.dropna()


def signal_s2_ema_stack(btc_1h, btc_daily):
    """
    S2: EMA Stack Alignment Score
    Count how many of [8h, 1d, 3d, 7d] EMAs have close > EMA.
    Score: -1 (all bearish) to +1 (all bullish), computed daily.
    Full alignment = strong trend. Disagreement = divergence.
    We use the DISAGREEMENT as the signal (middle scores).
    """
    close_1h = btc_1h['close']

    # Compute EMAs in 1h-bar terms
    ema_8h = close_1h.ewm(span=8, adjust=False).mean()      # 8 hours
    ema_1d = close_1h.ewm(span=24, adjust=False).mean()     # 24 hours
    ema_3d = close_1h.ewm(span=72, adjust=False).mean()     # 72 hours
    ema_7d = close_1h.ewm(span=168, adjust=False).mean()    # 168 hours

    # Score: how many EMAs is close above?
    above_count = (
        (close_1h > ema_8h).astype(float) +
        (close_1h > ema_1d).astype(float) +
        (close_1h > ema_3d).astype(float) +
        (close_1h > ema_7d).astype(float)
    )
    # Normalize to [-1, +1] range: 0 EMAs above -> -1, 4 above -> +1
    alignment_score = (above_count / 2.0) - 1.0

    # Resample to daily (end of day)
    alignment_daily = alignment_score.resample('1D').last().dropna()

    # The divergence signal: how MISALIGNED are timeframes?
    # |alignment| close to 0 means divergence, close to 1 means alignment
    # We want: divergence_score = 1 - |alignment|
    divergence_score = 1.0 - alignment_daily.abs()

    # Also return directional alignment for use in strategies
    return alignment_daily, divergence_score


def signal_s3_momentum_divergence(btc_daily):
    """
    S3: Short/Long Momentum Divergence
    sign(24h return) != sign(168h return).
    Signal = 24h_ret - scaled_168h_ret (captures direction disagreement).
    Continuous version: short momentum minus long momentum, normalized.
    """
    ret_1d = btc_daily['close'].pct_change(1)
    ret_7d = btc_daily['close'].pct_change(7)

    # Continuous divergence: z-score of (1d_ret - 7d_ret_daily_avg)
    ret_7d_daily = ret_7d / 7.0  # daily equivalent
    divergence = ret_1d - ret_7d_daily

    # Rolling z-score for stationarity
    div_mean = divergence.rolling(30).mean()
    div_std = divergence.rolling(30).std()
    div_z = (divergence - div_mean) / div_std.replace(0, np.nan)

    # Also compute binary divergence indicator
    sign_disagree = (np.sign(ret_1d) != np.sign(ret_7d)).astype(float)

    return div_z.dropna(), sign_disagree.dropna()


def signal_s4_acceleration_divergence(btc_daily):
    """
    S4: Acceleration Divergence
    1d ROC positive but 7d ROC decelerating (second derivative turning).
    Signal: 1d_ROC * (-1 * d(7d_ROC)) — positive when short momentum up but
    long momentum decelerating.
    """
    roc_1d = btc_daily['close'].pct_change(1)
    roc_7d = btc_daily['close'].pct_change(7)

    # Acceleration = change in 7d ROC
    accel_7d = roc_7d.diff(1)

    # Divergence: short momentum up + long momentum decelerating
    # Continuous signal: roc_1d * sign(-accel_7d) captures the divergence
    divergence = roc_1d * np.sign(-accel_7d)

    # Z-score for stationarity
    div_mean = divergence.rolling(30).mean()
    div_std = divergence.rolling(30).std()
    div_z = (divergence - div_mean) / div_std.replace(0, np.nan)

    return div_z.dropna()


def signal_s5_vol_adjusted_divergence(btc_daily):
    """
    S5: Volatility-Adjusted Divergence
    (close - EMA20) / ATR_14  vs  (close - EMA50) / ATR_14
    When short-term says "extended" but long-term says "not", there's divergence.
    Signal = short_distance - long_distance (normalized by vol).
    """
    ema20 = btc_daily['close'].ewm(span=20, adjust=False).mean()
    ema50 = btc_daily['close'].ewm(span=50, adjust=False).mean()

    # ATR14
    high = btc_daily['high']
    low = btc_daily['low']
    prev_close = btc_daily['close'].shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs()
    ], axis=1).max(axis=1)
    atr14 = tr.rolling(14).mean()

    short_dist = (btc_daily['close'] - ema20) / atr14.replace(0, np.nan)
    long_dist = (btc_daily['close'] - ema50) / atr14.replace(0, np.nan)

    # Divergence: difference in normalized distances
    divergence = short_dist - long_dist

    return divergence.dropna()


def signal_s6_meanrev_timing(btc_4h, btc_daily, rsi_period=14):
    """
    S6: Mean-Reversion Timing (Pullback in Uptrend)
    Daily trend bullish (EMA20 > EMA50) but 4h RSI < 30 -> buy the dip.
    This is a STANDALONE signal different from V3's RSI entry timing:
      - V3 uses RSI<35 as a TIMING filter within its rebalance window
      - This signal fires independently, with different RSI threshold (30)
        and uses 4h timeframe (not daily)
    Continuous version: (50 - 4h_RSI) * trend_direction
    """
    ema20 = btc_daily['close'].ewm(span=20, adjust=False).mean()
    ema50 = btc_daily['close'].ewm(span=50, adjust=False).mean()
    trend = ((ema20 > ema50).astype(float) * 2 - 1)  # +1 bullish, -1 bearish

    rsi_4h = compute_rsi(btc_4h['close'], period=rsi_period)
    rsi_4h_daily = rsi_4h.resample('1D').last().dropna()

    common = rsi_4h_daily.index.intersection(trend.index)
    trend_c = trend.loc[common]
    rsi_c = rsi_4h_daily.loc[common]

    # Pullback strength: how far RSI is from 50, in the OPPOSITE direction of trend
    # Bullish trend + low RSI = strong pullback signal
    # Bearish trend + high RSI = strong pullback signal (short side)
    pullback_signal = (50.0 - rsi_c) * trend_c / 50.0  # normalized to [-1, +1]

    return pullback_signal.dropna()


# ══════════════════════════════════════════════════════════════════════════════
# IC ANALYSIS
# ══════════════════════════════════════════════════════════════════════════════

def compute_ic(signal, btc_daily, horizons=[1, 3, 7, 14]):
    """
    Compute Information Coefficient (rank correlation) between signal and
    forward returns at multiple horizons.
    """
    results = {}
    common = signal.index.intersection(btc_daily.index)
    sig = signal.loc[common]

    for h in horizons:
        fwd_ret = btc_daily['close'].pct_change(h).shift(-h)
        fwd_common = sig.index.intersection(fwd_ret.dropna().index)
        if len(fwd_common) < 50:
            results[h] = {'ic': 0, 'p_value': 1.0, 'n': 0}
            continue

        s = sig.loc[fwd_common]
        f = fwd_ret.loc[fwd_common]

        # Drop NaN
        mask = ~(s.isna() | f.isna())
        s = s[mask]
        f = f[mask]

        if len(s) < 50:
            results[h] = {'ic': 0, 'p_value': 1.0, 'n': 0}
            continue

        ic, p = stats.spearmanr(s, f)
        results[h] = {'ic': ic, 'p_value': p, 'n': len(s)}

    return results


# ══════════════════════════════════════════════════════════════════════════════
# SIGNAL-TO-STRATEGY BACKTEST
# ══════════════════════════════════════════════════════════════════════════════

def backtest_signal_strategy(signal, btc_daily, long_threshold=0.0, short_threshold=0.0,
                             rebalance='daily', cost_bps=COST_BPS, mode='long_short'):
    """
    Convert a continuous signal to a trading strategy and backtest.

    Parameters:
    - signal: continuous signal series
    - long_threshold: go long when signal > threshold
    - short_threshold: go short when signal < -threshold (if mode allows)
    - rebalance: 'daily' or 'weekly'
    - mode: 'long_short', 'long_only'
    """
    common = signal.index.intersection(btc_daily.index)
    if len(common) < 60:
        return None

    sig = signal.loc[common]
    ret = btc_daily.loc[common, 'ret']

    # Generate positions
    pos = pd.Series(0.0, index=common)
    pos[sig > long_threshold] = 1.0
    if mode == 'long_short':
        pos[sig < -short_threshold] = -1.0

    # Weekly rebalance
    if rebalance == 'weekly':
        is_monday = common.dayofweek == 0
        pos_weekly = pos.copy()
        pos_weekly[~is_monday] = np.nan
        pos_weekly = pos_weekly.ffill().fillna(0)
        pos = pos_weekly

    # Apply signal to next day's return
    pos_shifted = pos.shift(1)
    trades = pos_shifted.diff().abs()
    trade_cost = trades * (cost_bps / 10000)
    strat_ret = pos_shifted * ret - trade_cost

    strat_ret = strat_ret.dropna()
    if len(strat_ret) < 30:
        return None

    # Metrics
    ann_ret = strat_ret.mean() * 365
    ann_vol = strat_ret.std() * ANNUALIZE
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0
    cum_ret = (1 + strat_ret).cumprod()
    max_dd = (cum_ret / cum_ret.cummax() - 1).min()
    win_rate = (strat_ret[strat_ret != 0] > 0).mean() if (strat_ret != 0).sum() > 0 else 0

    n_trades = int(trades.sum() / 2)  # round-trips

    return {
        'daily_returns': strat_ret,
        'ann_return': ann_ret,
        'ann_vol': ann_vol,
        'sharpe': sharpe,
        'max_dd': max_dd,
        'win_rate': win_rate,
        'total_return': cum_ret.iloc[-1] - 1 if len(cum_ret) > 0 else 0,
        'n_trades': n_trades,
        'n_days': len(strat_ret),
    }


# ══════════════════════════════════════════════════════════════════════════════
# WALK-FORWARD ENGINE
# ══════════════════════════════════════════════════════════════════════════════

def walk_forward_signal(signal, btc_daily, param_grid, signal_to_strategy_fn,
                        cost_bps=COST_BPS):
    """
    Walk-forward validation for a signal.

    param_grid: list of dicts, each with strategy parameters
    signal_to_strategy_fn(signal_slice, btc_slice, params, cost_bps) -> dict with 'sharpe', 'daily_returns'

    Returns: list of window results
    """
    data_start = signal.index.min()
    data_end = signal.index.max()

    last_train_start = data_end - timedelta(days=TEST_DAYS + TRAIN_DAYS)
    first_train_start = last_train_start - timedelta(days=(N_WINDOWS - 1) * ROLL_DAYS)

    if first_train_start < data_start:
        first_train_start = data_start + timedelta(days=1)

    all_results = []
    all_oos_returns = []

    for w in range(N_WINDOWS):
        train_start = first_train_start + timedelta(days=w * ROLL_DAYS)
        train_end = train_start + timedelta(days=TRAIN_DAYS)
        test_start = train_end
        test_end = test_start + timedelta(days=TEST_DAYS)

        sig_train = signal.loc[train_start:train_end]
        sig_test = signal.loc[test_start:test_end]
        btc_train = btc_daily.loc[train_start:train_end]
        btc_test = btc_daily.loc[test_start:test_end]

        if len(sig_train) < 30 or len(sig_test) < 15:
            continue

        # Grid search on train
        best_sharpe = -999
        best_params = None
        for params in param_grid:
            result = signal_to_strategy_fn(sig_train, btc_train, params, cost_bps)
            if result is not None and result['sharpe'] > best_sharpe:
                best_sharpe = result['sharpe']
                best_params = params

        if best_params is None:
            continue

        # Apply best params to test
        oos_result = signal_to_strategy_fn(sig_test, btc_test, best_params, cost_bps)
        if oos_result is None:
            continue

        all_results.append({
            'window': w + 1,
            'train_start': train_start.date(),
            'train_end': train_end.date(),
            'test_start': test_start.date(),
            'test_end': test_end.date(),
            'best_params': best_params,
            'train_sharpe': best_sharpe,
            'oos_sharpe': oos_result['sharpe'],
            'oos_return': oos_result['total_return'],
            'oos_max_dd': oos_result['max_dd'],
            'oos_n_trades': oos_result['n_trades'],
            'oos_win_rate': oos_result['win_rate'],
            'oos_daily_returns': oos_result['daily_returns'],
        })

        all_oos_returns.append(oos_result['daily_returns'])

    return all_results, all_oos_returns


# ══════════════════════════════════════════════════════════════════════════════
# CORRELATION WITH V3
# ══════════════════════════════════════════════════════════════════════════════

def compute_v3_correlation(signal_returns, v3_returns):
    """
    Compute correlation between signal strategy returns and V3 returns.
    """
    common = signal_returns.index.intersection(v3_returns.index)
    if len(common) < 30:
        return {'pearson_r': np.nan, 'pearson_p': np.nan, 'spearman_r': np.nan, 'n': 0}

    s = signal_returns.loc[common]
    v = v3_returns.loc[common]

    mask = ~(s.isna() | v.isna())
    s = s[mask]
    v = v[mask]

    if len(s) < 30:
        return {'pearson_r': np.nan, 'pearson_p': np.nan, 'spearman_r': np.nan, 'n': 0}

    pr, pp = stats.pearsonr(s, v)
    sr, _ = stats.spearmanr(s, v)

    return {'pearson_r': pr, 'pearson_p': pp, 'spearman_r': sr, 'n': len(s)}


# ══════════════════════════════════════════════════════════════════════════════
# REGIME ANALYSIS
# ══════════════════════════════════════════════════════════════════════════════

def regime_analysis(signal_returns, btc_daily, regimes):
    """Compute signal performance by regime."""
    common = signal_returns.index.intersection(regimes.index)
    sig = signal_returns.loc[common]
    reg = regimes.loc[common]

    results = {}
    for regime in ['UPTREND', 'DOWNTREND', 'RANGE']:
        mask = reg == regime
        r = sig[mask].dropna()
        if len(r) < 10:
            results[regime] = {'sharpe': np.nan, 'ann_ret': np.nan, 'n_days': 0}
            continue

        ann_ret = r.mean() * 365
        ann_vol = r.std() * ANNUALIZE
        sharpe = ann_ret / ann_vol if ann_vol > 0 else 0
        results[regime] = {
            'sharpe': sharpe,
            'ann_ret': ann_ret,
            'n_days': len(r),
        }

    return results


# ══════════════════════════════════════════════════════════════════════════════
# STRATEGY WRAPPER FUNCTIONS (for walk-forward grid search)
# ══════════════════════════════════════════════════════════════════════════════

def wf_rsi_divergence_strategy(signal, btc_daily, params, cost_bps):
    """Walk-forward strategy function for RSI divergence."""
    return backtest_signal_strategy(
        signal, btc_daily,
        long_threshold=params['long_thresh'],
        short_threshold=params['short_thresh'],
        rebalance=params.get('rebalance', 'daily'),
        cost_bps=cost_bps,
        mode=params.get('mode', 'long_short'),
    )


def wf_ema_alignment_strategy(signal, btc_daily, params, cost_bps):
    """Walk-forward strategy for EMA alignment. Signal is directional alignment."""
    return backtest_signal_strategy(
        signal, btc_daily,
        long_threshold=params['long_thresh'],
        short_threshold=params['short_thresh'],
        rebalance=params.get('rebalance', 'daily'),
        cost_bps=cost_bps,
        mode=params.get('mode', 'long_short'),
    )


def wf_momentum_div_strategy(signal, btc_daily, params, cost_bps):
    """Walk-forward strategy for momentum divergence z-score."""
    return backtest_signal_strategy(
        signal, btc_daily,
        long_threshold=params['long_thresh'],
        short_threshold=params['short_thresh'],
        rebalance=params.get('rebalance', 'daily'),
        cost_bps=cost_bps,
        mode=params.get('mode', 'long_short'),
    )


def wf_generic_strategy(signal, btc_daily, params, cost_bps):
    """Generic walk-forward strategy wrapper."""
    return backtest_signal_strategy(
        signal, btc_daily,
        long_threshold=params['long_thresh'],
        short_threshold=params['short_thresh'],
        rebalance=params.get('rebalance', 'daily'),
        cost_bps=cost_bps,
        mode=params.get('mode', 'long_short'),
    )


# ══════════════════════════════════════════════════════════════════════════════
# PARAMETER GRIDS
# ══════════════════════════════════════════════════════════════════════════════

def make_param_grid_s1():
    """RSI divergence parameter grid."""
    grid = []
    for lt in [5, 10, 15, 20]:
        for st in [5, 10, 15, 20]:
            for reb in ['daily', 'weekly']:
                for mode in ['long_short', 'long_only']:
                    grid.append({
                        'long_thresh': lt, 'short_thresh': st,
                        'rebalance': reb, 'mode': mode,
                    })
    return grid


def make_param_grid_s2():
    """EMA alignment parameter grid."""
    grid = []
    for lt in [0.0, 0.25, 0.5, 0.75]:
        for st in [0.0, 0.25, 0.5, 0.75]:
            for reb in ['daily', 'weekly']:
                for mode in ['long_short', 'long_only']:
                    grid.append({
                        'long_thresh': lt, 'short_thresh': st,
                        'rebalance': reb, 'mode': mode,
                    })
    return grid


def make_param_grid_continuous():
    """Generic parameter grid for z-scored continuous signals."""
    grid = []
    for lt in [0.0, 0.5, 1.0, 1.5, 2.0]:
        for st in [0.0, 0.5, 1.0, 1.5, 2.0]:
            for reb in ['daily', 'weekly']:
                for mode in ['long_short', 'long_only']:
                    grid.append({
                        'long_thresh': lt, 'short_thresh': st,
                        'rebalance': reb, 'mode': mode,
                    })
    return grid


def make_param_grid_s6():
    """Mean-reversion timing parameter grid."""
    grid = []
    for lt in [0.0, 0.1, 0.2, 0.3, 0.4, 0.5]:
        for st in [0.0, 0.1, 0.2, 0.3]:
            for reb in ['daily', 'weekly']:
                for mode in ['long_short', 'long_only']:
                    grid.append({
                        'long_thresh': lt, 'short_thresh': st,
                        'rebalance': reb, 'mode': mode,
                    })
    return grid


# ══════════════════════════════════════════════════════════════════════════════
# PORTFOLIO ANALYSIS
# ══════════════════════════════════════════════════════════════════════════════

def portfolio_analysis(signal_returns, v3_returns, weight=0.5):
    """Compute 50/50 portfolio metrics."""
    common = signal_returns.index.intersection(v3_returns.index)
    if len(common) < 30:
        return None

    s = signal_returns.loc[common].fillna(0)
    v = v3_returns.loc[common].fillna(0)

    port = weight * s + (1 - weight) * v

    ann_ret = port.mean() * 365
    ann_vol = port.std() * ANNUALIZE
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0
    cum_ret = (1 + port).cumprod()
    max_dd = (cum_ret / cum_ret.cummax() - 1).min()

    return {
        'ann_return': ann_ret,
        'ann_vol': ann_vol,
        'sharpe': sharpe,
        'max_dd': max_dd,
        'total_return': cum_ret.iloc[-1] - 1 if len(cum_ret) > 0 else 0,
    }


# ══════════════════════════════════════════════════════════════════════════════
# MAIN EXECUTION
# ══════════════════════════════════════════════════════════════════════════════

def main():
    print("=" * 70)
    print("R116: Multi-Timeframe Divergence as BTC Portfolio Diversifier")
    print("=" * 70)

    # ── Load Data ──────────────────────────────────────────────────────────
    btc_1h = load_btc_1h()
    btc_4h, btc_daily = resample_timeframes(btc_1h)
    v3_returns = compute_v3_returns(btc_daily)
    regimes = classify_regimes(btc_daily)

    # Print regime distribution
    print(f"\n[REGIMES] Distribution:")
    for r in ['UPTREND', 'DOWNTREND', 'RANGE']:
        n = (regimes == r).sum()
        print(f"  {r}: {n} days ({n/len(regimes)*100:.1f}%)")

    # ── Phase 1: Compute All Signals ──────────────────────────────────────
    print("\n" + "=" * 70)
    print("PHASE 1: Signal Generation")
    print("=" * 70)

    signals = {}

    print("\n[S1] 4h/Daily RSI Divergence...")
    signals['S1_rsi_div'] = signal_s1_rsi_divergence(btc_4h, btc_daily)
    print(f"  Range: [{signals['S1_rsi_div'].min():.2f}, {signals['S1_rsi_div'].max():.2f}], "
          f"Mean: {signals['S1_rsi_div'].mean():.2f}, Std: {signals['S1_rsi_div'].std():.2f}")

    print("\n[S2] EMA Stack Alignment...")
    s2_alignment, s2_divergence = signal_s2_ema_stack(btc_1h, btc_daily)
    signals['S2_ema_align'] = s2_alignment  # directional: use for trading
    signals['S2_ema_div'] = s2_divergence    # divergence score: use for IC
    print(f"  Alignment range: [{s2_alignment.min():.2f}, {s2_alignment.max():.2f}]")
    print(f"  Divergence range: [{s2_divergence.min():.2f}, {s2_divergence.max():.2f}]")

    print("\n[S3] Short/Long Momentum Divergence...")
    s3_z, s3_binary = signal_s3_momentum_divergence(btc_daily)
    signals['S3_mom_div_z'] = s3_z
    signals['S3_mom_div_binary'] = s3_binary
    print(f"  Z-score range: [{s3_z.min():.2f}, {s3_z.max():.2f}]")
    print(f"  Binary divergence rate: {s3_binary.mean():.1%}")

    print("\n[S4] Acceleration Divergence...")
    signals['S4_accel_div'] = signal_s4_acceleration_divergence(btc_daily)
    print(f"  Z-score range: [{signals['S4_accel_div'].min():.2f}, {signals['S4_accel_div'].max():.2f}]")

    print("\n[S5] Volatility-Adjusted Divergence...")
    signals['S5_vol_div'] = signal_s5_vol_adjusted_divergence(btc_daily)
    print(f"  Range: [{signals['S5_vol_div'].min():.2f}, {signals['S5_vol_div'].max():.2f}]")

    print("\n[S6] Mean-Reversion Timing...")
    signals['S6_meanrev'] = signal_s6_meanrev_timing(btc_4h, btc_daily)
    print(f"  Range: [{signals['S6_meanrev'].min():.2f}, {signals['S6_meanrev'].max():.2f}]")

    # ── Phase 2: IC Scan ─────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("PHASE 2: Information Coefficient Scan")
    print("=" * 70)

    horizons = [1, 3, 7, 14]
    ic_results = {}

    # IC signals to test (use directional signals where possible)
    ic_signals = {
        'S1: RSI Divergence': signals['S1_rsi_div'],
        'S2: EMA Alignment': signals['S2_ema_align'],
        'S2: EMA Divergence': signals['S2_ema_div'],
        'S3: Momentum Div Z': signals['S3_mom_div_z'],
        'S4: Acceleration Div': signals['S4_accel_div'],
        'S5: Vol-Adj Divergence': signals['S5_vol_div'],
        'S6: MeanRev Timing': signals['S6_meanrev'],
    }

    print(f"\n{'Signal':<30} {'1d IC':>8} {'3d IC':>8} {'7d IC':>8} {'14d IC':>8} {'Max |IC|':>8} {'Pass?':>6}")
    print("-" * 86)

    passed_ic = []
    all_ic_data = []

    for name, sig in ic_signals.items():
        ic = compute_ic(sig, btc_daily, horizons)
        ic_results[name] = ic

        max_abs_ic = max(abs(ic[h]['ic']) for h in horizons if ic[h]['n'] > 0)
        passed = max_abs_ic >= 0.02

        row = {
            'signal': name,
            '1d_ic': ic[1]['ic'],
            '3d_ic': ic[3]['ic'],
            '7d_ic': ic[7]['ic'],
            '14d_ic': ic[14]['ic'],
            'max_abs_ic': max_abs_ic,
            'passed': passed,
        }
        all_ic_data.append(row)

        status = "PASS" if passed else "KILL"
        print(f"{name:<30} {ic[1]['ic']:>8.4f} {ic[3]['ic']:>8.4f} "
              f"{ic[7]['ic']:>8.4f} {ic[14]['ic']:>8.4f} {max_abs_ic:>8.4f} {status:>6}")

        if passed:
            passed_ic.append(name)

    print(f"\n{len(passed_ic)}/{len(ic_signals)} signals passed IC threshold (|IC| >= 0.02)")
    if not passed_ic:
        print("\n*** ALL SIGNALS KILLED AT IC SCAN ***")
        print("No signals have predictive power above noise floor.")

    # ── Phase 3: Full-Sample Backtest for IC-passing signals ──────────────
    print("\n" + "=" * 70)
    print("PHASE 3: Full-Sample Backtest & V3 Correlation")
    print("=" * 70)

    # Map signal names to trading signals and parameter grids
    signal_config = {
        'S1: RSI Divergence': {
            'signal': signals['S1_rsi_div'],
            'grid': make_param_grid_s1(),
            'wf_fn': wf_rsi_divergence_strategy,
        },
        'S2: EMA Alignment': {
            'signal': signals['S2_ema_align'],
            'grid': make_param_grid_s2(),
            'wf_fn': wf_ema_alignment_strategy,
        },
        'S2: EMA Divergence': {
            'signal': signals['S2_ema_div'],
            'grid': make_param_grid_s2(),
            'wf_fn': wf_generic_strategy,
        },
        'S3: Momentum Div Z': {
            'signal': signals['S3_mom_div_z'],
            'grid': make_param_grid_continuous(),
            'wf_fn': wf_momentum_div_strategy,
        },
        'S4: Acceleration Div': {
            'signal': signals['S4_accel_div'],
            'grid': make_param_grid_continuous(),
            'wf_fn': wf_generic_strategy,
        },
        'S5: Vol-Adj Divergence': {
            'signal': signals['S5_vol_div'],
            'grid': make_param_grid_continuous(),
            'wf_fn': wf_generic_strategy,
        },
        'S6: MeanRev Timing': {
            'signal': signals['S6_meanrev'],
            'grid': make_param_grid_s6(),
            'wf_fn': wf_generic_strategy,
        },
    }

    backtest_results = {}
    wf_candidates = []

    for name in passed_ic:
        cfg = signal_config[name]
        sig = cfg['signal']

        print(f"\n--- {name} ---")

        # Find best full-sample parameters
        best_sharpe = -999
        best_params = None
        best_result = None

        for params in cfg['grid']:
            result = cfg['wf_fn'](sig, btc_daily, params, COST_BPS)
            if result is not None and result['sharpe'] > best_sharpe:
                best_sharpe = result['sharpe']
                best_params = params
                best_result = result

        if best_result is None:
            print(f"  No valid backtest — skipping")
            continue

        # V3 correlation
        corr = compute_v3_correlation(best_result['daily_returns'], v3_returns)

        # Regime analysis
        regime_perf = regime_analysis(best_result['daily_returns'], btc_daily, regimes)

        # Portfolio analysis
        port = portfolio_analysis(best_result['daily_returns'], v3_returns)

        backtest_results[name] = {
            'best_params': best_params,
            'sharpe': best_result['sharpe'],
            'ann_return': best_result['ann_return'],
            'ann_vol': best_result['ann_vol'],
            'max_dd': best_result['max_dd'],
            'total_return': best_result['total_return'],
            'win_rate': best_result['win_rate'],
            'n_trades': best_result['n_trades'],
            'corr_pearson': corr['pearson_r'],
            'corr_spearman': corr['spearman_r'],
            'corr_p': corr['pearson_p'],
            'regime_perf': regime_perf,
            'portfolio': port,
            'daily_returns': best_result['daily_returns'],
        }

        print(f"  Best params: {best_params}")
        print(f"  Sharpe: {best_result['sharpe']:.3f}, Ann Ret: {best_result['ann_return']:.1%}, "
              f"Max DD: {best_result['max_dd']:.1%}")
        print(f"  V3 Correlation: r={corr['pearson_r']:.4f} (p={corr['pearson_p']:.4f})")
        print(f"  Regime Sharpe — UP: {regime_perf['UPTREND']['sharpe']:.2f}, "
              f"DOWN: {regime_perf['DOWNTREND']['sharpe']:.2f}, "
              f"RANGE: {regime_perf['RANGE']['sharpe']:.2f}")
        if port:
            print(f"  50/50 Portfolio Sharpe: {port['sharpe']:.3f} vs V3: "
                  f"{v3_returns.mean()/v3_returns.std()*ANNUALIZE:.3f}")

        # Check kill criteria
        killed = False
        kill_reasons = []
        if abs(corr['pearson_r']) > 0.5:
            killed = True
            kill_reasons.append(f"Correlation with V3 = {corr['pearson_r']:.3f} > 0.5")

        if killed:
            print(f"  *** KILLED: {'; '.join(kill_reasons)} ***")
        else:
            wf_candidates.append(name)

    print(f"\n{len(wf_candidates)} signals passed to walk-forward phase")

    # ── Phase 4: Walk-Forward Validation ─────────────────────────────────
    print("\n" + "=" * 70)
    print("PHASE 4: Walk-Forward Validation (10 windows, 180d/90d)")
    print("=" * 70)

    wf_results = {}
    final_candidates = []

    for name in wf_candidates:
        cfg = signal_config[name]
        sig = cfg['signal']

        print(f"\n{'='*60}")
        print(f"Walk-Forward: {name}")
        print(f"  Grid size: {len(cfg['grid'])} parameter combinations")
        print(f"{'='*60}")

        windows, oos_returns = walk_forward_signal(
            sig, btc_daily, cfg['grid'], cfg['wf_fn'], COST_BPS
        )

        if len(windows) < 5:
            print(f"  Only {len(windows)} valid windows — SKIP")
            continue

        # Summarize
        oos_sharpes = [w['oos_sharpe'] for w in windows]
        n_positive = sum(1 for s in oos_sharpes if s > 0)
        mean_sharpe = np.mean(oos_sharpes)
        median_sharpe = np.median(oos_sharpes)

        print(f"\n  Window Results:")
        print(f"  {'W#':<4} {'Test Period':<25} {'OOS Sharpe':>12} {'OOS Return':>12} {'Train Sharpe':>12} {'Positive?':>10}")
        print(f"  {'-'*75}")
        for w in windows:
            pos_str = "YES" if w['oos_sharpe'] > 0 else "NO"
            print(f"  W{w['window']:<3} {str(w['test_start'])+' to '+str(w['test_end']):<25} "
                  f"{w['oos_sharpe']:>12.3f} {w['oos_return']:>11.1%} "
                  f"{w['train_sharpe']:>12.3f} {pos_str:>10}")

        print(f"\n  Summary:")
        print(f"    Mean OOS Sharpe: {mean_sharpe:.3f}")
        print(f"    Median OOS Sharpe: {median_sharpe:.3f}")
        print(f"    Positive windows: {n_positive}/{len(windows)}")

        # Compute OOS correlation with V3
        if oos_returns:
            all_oos = pd.concat(oos_returns)
            oos_corr = compute_v3_correlation(all_oos, v3_returns)
            print(f"    OOS Correlation with V3: {oos_corr['pearson_r']:.4f}")
        else:
            oos_corr = {'pearson_r': np.nan}

        # Check kill criteria
        killed = False
        kill_reasons = []

        if mean_sharpe < 0.3:
            killed = True
            kill_reasons.append(f"Mean OOS Sharpe {mean_sharpe:.3f} < 0.3")

        if n_positive < 5:
            killed = True
            kill_reasons.append(f"Positive windows {n_positive}/{len(windows)} < 5/10")

        if not np.isnan(oos_corr['pearson_r']) and abs(oos_corr['pearson_r']) > 0.5:
            killed = True
            kill_reasons.append(f"OOS Corr with V3 = {oos_corr['pearson_r']:.3f} > 0.5")

        wf_results[name] = {
            'windows': windows,
            'oos_returns': oos_returns,
            'mean_sharpe': mean_sharpe,
            'median_sharpe': median_sharpe,
            'n_positive': n_positive,
            'n_windows': len(windows),
            'oos_corr': oos_corr,
            'killed': killed,
            'kill_reasons': kill_reasons,
        }

        if killed:
            print(f"\n  *** KILLED: {'; '.join(kill_reasons)} ***")
        else:
            final_candidates.append(name)
            print(f"\n  >>> PASSED all kill criteria <<<")

    # ── Phase 5: Regime-Specific Deep Dive (surviving signals) ───────────
    print("\n" + "=" * 70)
    print("PHASE 5: Regime Deep Dive & Portfolio Construction")
    print("=" * 70)

    final_results = {}
    v3_sharpe = v3_returns.mean() / v3_returns.std() * ANNUALIZE

    for name in final_candidates:
        bt = backtest_results[name]
        wf = wf_results[name]
        regime_perf = bt['regime_perf']

        print(f"\n--- {name} ---")
        print(f"  Regime Performance:")
        for r in ['UPTREND', 'DOWNTREND', 'RANGE']:
            rp = regime_perf[r]
            print(f"    {r}: Sharpe={rp['sharpe']:.2f}, "
                  f"Ann Ret={rp['ann_ret']:.1%}, Days={rp['n_days']}")

        # Range regime analysis (key for V3 diversification)
        range_sharpe = regime_perf['RANGE']['sharpe']
        print(f"\n  V3 RANGE Sharpe: -1.01 (known weakness)")
        print(f"  Signal RANGE Sharpe: {range_sharpe:.2f}")
        if not np.isnan(range_sharpe) and range_sharpe > 0:
            print(f"  >>> POSITIVE in RANGE — strong diversifier potential <<<")

        # Portfolio construction test
        port = bt['portfolio']
        if port:
            print(f"\n  50/50 Portfolio vs V3-only:")
            print(f"    V3 Sharpe:       {v3_sharpe:.3f}")
            print(f"    Portfolio Sharpe: {port['sharpe']:.3f}")
            print(f"    V3 MaxDD:        {(1+v3_returns).cumprod().div((1+v3_returns).cumprod().cummax()).sub(1).min():.1%}")
            print(f"    Portfolio MaxDD:  {port['max_dd']:.1%}")

        final_results[name] = {
            'backtest': bt,
            'walk_forward': wf,
        }

    # Also analyze ALL signals that passed IC (even if killed at WF) for the
    # regime analysis, since some might still be useful as overlay filters
    print("\n\n" + "=" * 70)
    print("SUPPLEMENTARY: Regime Analysis for All IC-Passing Signals")
    print("=" * 70)

    for name in passed_ic:
        if name in backtest_results:
            bt = backtest_results[name]
            rp = bt['regime_perf']
            print(f"\n  {name}:")
            for r in ['UPTREND', 'DOWNTREND', 'RANGE']:
                if r in rp:
                    print(f"    {r}: Sharpe={rp[r]['sharpe']:.2f}, "
                          f"Ann Ret={rp[r]['ann_ret']:.1%}, Days={rp[r]['n_days']}")

    # ══════════════════════════════════════════════════════════════════════
    # GENERATE REPORT
    # ══════════════════════════════════════════════════════════════════════
    print("\n\n" + "=" * 70)
    print("GENERATING REPORT...")
    print("=" * 70)

    report = []
    report.append("# R116: Multi-Timeframe Divergence as BTC Portfolio Diversifier\n")
    report.append(f"**Date**: 2026-03-24")
    report.append(f"**Period**: {btc_daily.index.min().date()} to {btc_daily.index.max().date()}")
    report.append(f"**Asset**: BTC spot (derived from 1h data)")
    report.append(f"**Cost assumption**: {COST_BPS} bps round-trip")
    report.append(f"**Walk-forward**: {N_WINDOWS} windows, {TRAIN_DAYS}d train / {TEST_DAYS}d test, rolling {ROLL_DAYS}d\n")

    # V3 Baseline
    report.append("## V3 Momentum Baseline\n")
    v3_ann_ret = v3_returns.mean() * 365
    v3_ann_vol = v3_returns.std() * ANNUALIZE
    v3_cum = (1 + v3_returns).cumprod()
    v3_max_dd = (v3_cum / v3_cum.cummax() - 1).min()
    report.append("| Metric | Value |")
    report.append("|--------|-------|")
    report.append(f"| Ann. Return | {v3_ann_ret:.1%} |")
    report.append(f"| Ann. Volatility | {v3_ann_vol:.1%} |")
    report.append(f"| Sharpe Ratio | **{v3_sharpe:.3f}** |")
    report.append(f"| Max Drawdown | {v3_max_dd:.1%} |")
    report.append(f"| Total Return | {v3_cum.iloc[-1]-1:.1%} |")
    report.append(f"| Regime Sharpe (RANGE) | **-1.01** |")
    report.append("")

    # IC Results
    report.append("## Phase 1: IC Scan Results\n")
    report.append(f"| Signal | 1d IC | 3d IC | 7d IC | 14d IC | Max |IC| | Verdict |")
    report.append("|--------|-------|-------|-------|--------|---------|---------|")
    for row in all_ic_data:
        verdict = "PASS" if row['passed'] else "KILL"
        report.append(f"| {row['signal']} | {row['1d_ic']:.4f} | {row['3d_ic']:.4f} | "
                      f"{row['7d_ic']:.4f} | {row['14d_ic']:.4f} | {row['max_abs_ic']:.4f} | {verdict} |")
    report.append("")
    report.append(f"**{len(passed_ic)}/{len(ic_signals)} signals passed IC >= 0.02 threshold**\n")

    # Full-Sample Backtest Results
    if backtest_results:
        report.append("## Phase 2: Full-Sample Backtest & V3 Correlation\n")
        for name, bt in backtest_results.items():
            report.append(f"### {name}\n")
            report.append("| Metric | Value |")
            report.append("|--------|-------|")
            report.append(f"| Ann. Return | {bt['ann_return']:.1%} |")
            report.append(f"| Ann. Volatility | {bt['ann_vol']:.1%} |")
            report.append(f"| Sharpe Ratio | **{bt['sharpe']:.3f}** |")
            report.append(f"| Max Drawdown | {bt['max_dd']:.1%} |")
            report.append(f"| Total Return | {bt['total_return']:.1%} |")
            report.append(f"| Win Rate | {bt['win_rate']:.1%} |")
            report.append(f"| N Trades | {bt['n_trades']} |")
            report.append(f"| V3 Corr (Pearson) | **{bt['corr_pearson']:.4f}** |")
            report.append(f"| V3 Corr (Spearman) | {bt['corr_spearman']:.4f} |")
            report.append(f"| Best Params | {bt['best_params']} |")
            report.append("")

            # Regime performance
            report.append("**Regime Performance:**\n")
            report.append("| Regime | Sharpe | Ann. Return | Days |")
            report.append("|--------|--------|-------------|------|")
            for r in ['UPTREND', 'DOWNTREND', 'RANGE']:
                rp = bt['regime_perf'][r]
                report.append(f"| {r} | {rp['sharpe']:.2f} | {rp['ann_ret']:.1%} | {rp['n_days']} |")
            report.append("")

            # Portfolio
            if bt['portfolio']:
                port = bt['portfolio']
                report.append("**50/50 Portfolio with V3:**\n")
                report.append("| Metric | V3 Only | 50/50 Portfolio | Delta |")
                report.append("|--------|---------|----------------|-------|")
                report.append(f"| Sharpe | {v3_sharpe:.3f} | {port['sharpe']:.3f} | "
                              f"{port['sharpe']-v3_sharpe:+.3f} |")
                report.append(f"| Ann. Return | {v3_ann_ret:.1%} | {port['ann_return']:.1%} | |")
                report.append(f"| Max DD | {v3_max_dd:.1%} | {port['max_dd']:.1%} | |")
                report.append("")

            # Kill verdict
            if name not in wf_candidates:
                report.append(f"**Verdict**: **KILLED** (V3 correlation > 0.5)\n")
            report.append("---\n")

    # Walk-Forward Results
    if wf_results:
        report.append("## Phase 3: Walk-Forward Validation\n")
        for name, wf in wf_results.items():
            report.append(f"### {name}\n")
            report.append(f"| Window | Test Period | OOS Sharpe | OOS Return | Train Sharpe | Positive? |")
            report.append(f"|--------|-------------|-----------|------------|-------------|-----------|")
            for w in wf['windows']:
                pos_str = "YES" if w['oos_sharpe'] > 0 else "NO"
                report.append(f"| W{w['window']} | {w['test_start']} to {w['test_end']} | "
                              f"{w['oos_sharpe']:.3f} | {w['oos_return']:.1%} | "
                              f"{w['train_sharpe']:.3f} | {pos_str} |")
            report.append("")
            report.append(f"**Summary:**")
            report.append(f"- Mean OOS Sharpe: **{wf['mean_sharpe']:.3f}**")
            report.append(f"- Median OOS Sharpe: {wf['median_sharpe']:.3f}")
            report.append(f"- Positive windows: **{wf['n_positive']}/{wf['n_windows']}**")
            report.append(f"- OOS V3 Correlation: {wf['oos_corr']['pearson_r']:.4f}")
            report.append("")

            if wf['killed']:
                report.append(f"**Verdict**: **KILLED**")
                for reason in wf['kill_reasons']:
                    report.append(f"- {reason}")
            else:
                report.append(f"**Verdict**: **PASSED**")
            report.append("\n---\n")

    # Final Summary
    report.append("## Summary\n")
    report.append(f"| Signal | IC (max) | Sharpe | V3 Corr | WF Mean Sharpe | WF +ive | Verdict |")
    report.append(f"|--------|----------|--------|---------|----------------|---------|---------|")

    for row in all_ic_data:
        name = row['signal']
        ic_val = row['max_abs_ic']

        if name in backtest_results:
            bt = backtest_results[name]
            sharpe = bt['sharpe']
            corr = bt['corr_pearson']
        else:
            sharpe = np.nan
            corr = np.nan

        if name in wf_results:
            wf = wf_results[name]
            wf_sharpe = wf['mean_sharpe']
            wf_pos = f"{wf['n_positive']}/{wf['n_windows']}"
        else:
            wf_sharpe = np.nan
            wf_pos = "N/A"

        if name in final_candidates:
            verdict = "**PASSED**"
        elif not row['passed']:
            verdict = "KILLED (IC)"
        elif name not in wf_candidates:
            verdict = "KILLED (Corr)"
        elif name in wf_results and wf_results[name]['killed']:
            reasons = wf_results[name]['kill_reasons']
            short_reason = reasons[0].split(' ')[0] if reasons else "WF"
            verdict = f"KILLED (WF)"
        else:
            verdict = "KILLED"

        sharpe_str = f"{sharpe:.3f}" if not np.isnan(sharpe) else "N/A"
        corr_str = f"{corr:.4f}" if not np.isnan(corr) else "N/A"
        wf_str = f"{wf_sharpe:.3f}" if not np.isnan(wf_sharpe) else "N/A"

        report.append(f"| {name} | {ic_val:.4f} | {sharpe_str} | {corr_str} | "
                      f"{wf_str} | {wf_pos} | {verdict} |")

    report.append("")

    # Conclusions
    report.append("## Conclusions\n")

    if final_candidates:
        report.append(f"**{len(final_candidates)} signal(s) passed all kill criteria:**\n")
        for name in final_candidates:
            bt = backtest_results[name]
            wf = wf_results[name]
            report.append(f"- **{name}**: Sharpe={bt['sharpe']:.3f}, "
                          f"V3 Corr={bt['corr_pearson']:.4f}, "
                          f"WF Sharpe={wf['mean_sharpe']:.3f}, "
                          f"WF +ive={wf['n_positive']}/{wf['n_windows']}")

            # Range regime note
            range_sharpe = bt['regime_perf']['RANGE']['sharpe']
            if not np.isnan(range_sharpe) and range_sharpe > 0:
                report.append(f"  - RANGE regime Sharpe: {range_sharpe:.2f} "
                              f"(V3 RANGE Sharpe: -1.01 — complementary)")
        report.append("")
        report.append("### Portfolio Diversification Value\n")
        for name in final_candidates:
            bt = backtest_results[name]
            port = bt['portfolio']
            if port:
                report.append(f"**{name}** 50/50 portfolio with V3:")
                report.append(f"- Portfolio Sharpe: {port['sharpe']:.3f} vs V3 alone: {v3_sharpe:.3f} "
                              f"({port['sharpe']-v3_sharpe:+.3f})")
                report.append(f"- Portfolio MaxDD: {port['max_dd']:.1%} vs V3 alone: {v3_max_dd:.1%}")
                report.append("")
    else:
        report.append("**No signals passed all kill criteria.**\n")
        report.append("Multi-timeframe divergence signals did not produce robust, "
                      "decorrelated alpha suitable for V3 diversification.\n")

        # Still report best-performing signals
        if backtest_results:
            report.append("### Best Performers (before kill criteria):\n")
            sorted_bt = sorted(backtest_results.items(), key=lambda x: x[1]['sharpe'], reverse=True)
            for name, bt in sorted_bt[:3]:
                report.append(f"- **{name}**: Sharpe={bt['sharpe']:.3f}, "
                              f"V3 Corr={bt['corr_pearson']:.4f}")
                range_s = bt['regime_perf']['RANGE']['sharpe']
                if not np.isnan(range_s):
                    report.append(f"  - RANGE Sharpe: {range_s:.2f}")
            report.append("")

    # Kill reasons summary
    report.append("### Kill Reasons\n")
    for row in all_ic_data:
        name = row['signal']
        if not row['passed']:
            report.append(f"- **{name}**: IC < 0.02 across all horizons")
        elif name in wf_results and wf_results[name]['killed']:
            for reason in wf_results[name]['kill_reasons']:
                report.append(f"- **{name}**: {reason}")
        elif name not in wf_candidates and name in backtest_results:
            report.append(f"- **{name}**: V3 Correlation > 0.5")

    report.append("")

    # Methodology note
    report.append("## Methodology Notes\n")
    report.append("- All signals computed from BTC 1h spot data (resampled to 4h/daily as needed)")
    report.append("- No external data used — purely price-derived signals")
    report.append("- V3 baseline uses simplified EMA20/50 crossover with weekly rebalance "
                  "(no positioning/VRP overlays)")
    report.append("- Walk-forward: 10 windows x 180d train / 90d test, rolling 90d")
    report.append("- Fees: 10 bps round-trip per position change")
    report.append("- IC computed as Spearman rank correlation with forward returns")
    report.append("- Regime classification: UPTREND (SMA50>SMA200 & close>SMA50), "
                  "DOWNTREND (SMA50<SMA200 & close<SMA50), RANGE (other)")
    report.append("")

    # Write report
    report_text = "\n".join(report)
    OUTPUT_MD.write_text(report_text)
    print(f"\n[DONE] Report written to {OUTPUT_MD}")

    # Final verdict
    print("\n" + "=" * 70)
    if final_candidates:
        print(f"RESULT: {len(final_candidates)} SIGNAL(S) PASSED ALL CRITERIA")
        for name in final_candidates:
            wf = wf_results[name]
            bt = backtest_results[name]
            print(f"  {name}: WF Sharpe={wf['mean_sharpe']:.3f}, "
                  f"V3 Corr={bt['corr_pearson']:.4f}, "
                  f"WF +ive={wf['n_positive']}/{wf['n_windows']}")
    else:
        print("RESULT: ALL SIGNALS KILLED")
        print("Multi-timeframe divergence does not provide robust diversification for V3.")
    print("=" * 70)


if __name__ == '__main__':
    main()
