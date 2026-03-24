#!/workspace/venv/bin/python
"""
R110: Three-Strategy Portfolio -- V3 Momentum + Intraday Breakout + Macro Regime
================================================================================

Goal: Test whether a 3-strategy portfolio combining V3 Momentum, Intraday Momentum
Breakout, and Macro Regime Rotation on BTC improves risk-adjusted returns vs V3 alone.

Strategy Definitions:
  1. V3 Momentum (s320): Long when EMA(20) > EMA(50), weekly rebalance
  2. Intraday Momentum Breakout: 1h return > 2% AND vol > 2x avg, trailing stop
  3. Macro Regime Rotation: Long when US10Y & DXY both falling (risk-on)

Portfolio Construction:
  - Equal Weight (33/33/33)
  - Risk Parity (inversely proportional to 60-day rolling vol)
  - V3 Anchor (50/25/25)

Kill Criteria:
  - Portfolio Sharpe < V3 alone -> KILL
  - MaxDD > V3 alone -> KILL
  - Walk-forward < 3/6 windows where portfolio > V3 -> KILL
  - Correlation spikes > 0.5 during drawdowns -> WARNING

Data:
  - BTC spot 1h: data/spot/1h_cache/BTC_1h.parquet
  - Macro: data/alternative/macro/*.parquet

Author: Quant Research Agent
Date: 2026-03-24
"""

import os
import warnings
import numpy as np
import pandas as pd
from scipy import stats
from pathlib import Path
from datetime import datetime

warnings.filterwarnings('ignore')

# -- Paths -------------------------------------------------------------------
PROJECT_DIR = Path('/workspace/crypto_backtest')
DATA_DIR = PROJECT_DIR / 'data'
OUTPUT_MD = PROJECT_DIR / 'research' / 'R110_three_strategy_portfolio.md'

COST_BPS = 10  # round-trip cost in basis points
PERIOD_START = '2021-01-01'
PERIOD_END = '2026-03-31'

# Walk-forward configuration
WF_TRAIN_MONTHS = 12
WF_TEST_MONTHS = 6
WF_N_WINDOWS = 6


# ============================================================================
# DATA LOADING
# ============================================================================

def load_btc_1h():
    """Load BTC 1h spot data."""
    print("[DATA] Loading BTC 1h spot...")
    df = pd.read_parquet(DATA_DIR / 'spot/1h_cache/BTC_1h.parquet')
    df.index = pd.to_datetime(df.index)
    df = df.sort_index()
    df = df[~df.index.duplicated(keep='first')]
    df = df.loc[PERIOD_START:PERIOD_END]
    print(f"  {df.index.min().date()} to {df.index.max().date()}, {len(df)} bars")
    return df


def load_btc_daily(btc_1h):
    """Resample 1h to daily."""
    daily = btc_1h['close'].resample('1D').last().dropna().to_frame('close')
    daily['open'] = btc_1h['open'].resample('1D').first()
    daily['high'] = btc_1h['high'].resample('1D').max()
    daily['low'] = btc_1h['low'].resample('1D').min()
    daily['volume'] = btc_1h['volume'].resample('1D').sum()
    daily['daily_return'] = daily['close'].pct_change()
    daily['log_return'] = np.log(daily['close'] / daily['close'].shift(1))
    print(f"  Daily: {daily.index.min().date()} to {daily.index.max().date()}, {len(daily)} rows")
    return daily


def load_macro():
    """Load macro data: DXY, US10Y."""
    print("[DATA] Loading macro data...")
    macro = {}
    for name, fname in [('dxy', 'usd_index'), ('us10y', 'us10y_yield')]:
        df = pd.read_parquet(DATA_DIR / f'alternative/macro/{fname}.parquet')
        df['Date'] = pd.to_datetime(df['Date'])
        df = df.set_index('Date').sort_index()
        df = df[~df.index.duplicated(keep='last')]
        macro[name] = df['Close']
        print(f"  {name}: {df.index.min().date()} to {df.index.max().date()}, {len(df)} rows")
    return macro


# ============================================================================
# STRATEGY 1: V3 MOMENTUM
# ============================================================================

def compute_v3_momentum(daily):
    """
    V3 Momentum: Long when daily EMA(20) > EMA(50), flat otherwise.
    Weekly rebalance (Monday). Returns daily return series.
    """
    print("\n[STRATEGY 1] V3 Momentum")
    ema20 = daily['close'].ewm(span=20, adjust=False).mean()
    ema50 = daily['close'].ewm(span=50, adjust=False).mean()
    uptrend = (ema20 > ema50).astype(int)

    # Use prior day's signal (no lookahead)
    pos = uptrend.shift(1).fillna(0).astype(int)

    # Weekly rebalance: lock position on Monday
    weekly_signal = pos.resample('W-MON').last()
    pos_weekly = weekly_signal.reindex(pos.index, method='ffill').fillna(0).astype(int)

    # Compute returns with costs
    daily_ret = daily['daily_return'].fillna(0)
    position_changes = pos_weekly.diff().abs().fillna(0)
    cost = position_changes * (COST_BPS / 10000)
    strat_ret = pos_weekly * daily_ret - cost

    days_long = (pos_weekly == 1).sum()
    total_days = len(pos_weekly)
    ann_ret = strat_ret.mean() * 365
    ann_vol = strat_ret.std() * np.sqrt(365)
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0
    print(f"  V3 Momentum: {days_long}/{total_days} days long ({100*days_long/total_days:.1f}%)")
    print(f"  Ann return: {ann_ret:.2%}, Sharpe: {sharpe:.3f}")

    return strat_ret, pos_weekly


# ============================================================================
# STRATEGY 2: INTRADAY MOMENTUM BREAKOUT
# ============================================================================

def compute_intraday_momentum(btc_1h, daily):
    """
    Intraday momentum breakout on 1h bars (long-only version):
    - Entry: 1h return > 2% AND volume > 2x 20-bar average
    - Exit: 1.5% trailing stop OR 8h max hold
    - Long-only for BTC spot
    """
    print("\n[STRATEGY 2] Intraday Momentum Breakout")

    ret_1h = btc_1h['close'].pct_change()
    avg_vol = btc_1h['volume'].rolling(20).mean()

    n = len(btc_1h)
    position = np.zeros(n)
    hold_count = 0
    max_hold = 8
    trailing_stop = 0.015
    in_trade = False
    peak_price = 0.0

    close_vals = btc_1h['close'].values
    ret_vals = ret_1h.values
    vol_vals = btc_1h['volume'].values
    avg_vol_vals = avg_vol.values

    for i in range(1, n):
        if np.isnan(ret_vals[i]) or np.isnan(avg_vol_vals[i]):
            continue

        if in_trade:
            hold_count += 1
            if hold_count >= max_hold:
                in_trade = False
                position[i] = 0
                continue

            # Trailing stop
            if close_vals[i] > peak_price:
                peak_price = close_vals[i]
            if (peak_price - close_vals[i]) / peak_price > trailing_stop:
                in_trade = False
                position[i] = 0
                continue

            position[i] = 1
        else:
            # Entry: positive breakout + high volume (long-only)
            if ret_vals[i] > 0.02 and vol_vals[i] > 2 * avg_vol_vals[i]:
                in_trade = True
                peak_price = close_vals[i]
                hold_count = 0
                position[i] = 1

    pos_1h = pd.Series(position, index=btc_1h.index)

    # Compute 1h strategy returns
    ret_1h_clean = btc_1h['close'].pct_change().fillna(0)
    strat_ret_1h = pos_1h.shift(1).fillna(0) * ret_1h_clean

    # Position changes for cost
    pos_changes_1h = pos_1h.diff().abs().fillna(0)
    cost_1h = pos_changes_1h * (COST_BPS / 10000 / 2)
    strat_ret_1h = strat_ret_1h - cost_1h

    # Aggregate to daily
    strat_ret_daily = strat_ret_1h.resample('1D').sum()
    strat_ret_daily = strat_ret_daily.reindex(daily.index, fill_value=0).fillna(0)

    # Daily position (average of 1h positions for exposure tracking)
    daily_pos = pos_1h.resample('1D').mean().fillna(0)
    daily_pos = daily_pos.reindex(daily.index, fill_value=0).fillna(0).clip(0, 1)

    n_entries = ((pos_1h.diff() > 0)).sum()
    pct_time = (daily_pos > 0).sum() / len(daily_pos) * 100
    ann_ret = strat_ret_daily.mean() * 365
    ann_vol = strat_ret_daily.std() * np.sqrt(365)
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0
    print(f"  Entries: {n_entries}, Time in market: {pct_time:.1f}%")
    print(f"  Ann return: {ann_ret:.2%}, Sharpe: {sharpe:.3f}")

    return strat_ret_daily, daily_pos


# ============================================================================
# STRATEGY 3: MACRO REGIME ROTATION
# ============================================================================

def compute_macro_regime(daily, macro):
    """
    Macro regime rotation (long-only, safer version):
    - Long when US10Y 20d change < 0 AND DXY 20d change < 0 (risk-on)
    - Flat when mixed or both rising (risk-off)
    - Daily rebalance based on macro signals
    """
    print("\n[STRATEGY 3] Macro Regime Rotation")

    dxy = macro['dxy'].reindex(daily.index, method='ffill')
    us10y = macro['us10y'].reindex(daily.index, method='ffill')

    # 20-day changes
    dxy_chg_20d = dxy.diff(20)
    us10y_chg_20d = us10y.diff(20)

    # Risk-on: both falling
    risk_on = ((us10y_chg_20d < 0) & (dxy_chg_20d < 0)).astype(int)

    # Use prior day's signal (no lookahead)
    pos_shifted = risk_on.shift(1).fillna(0).astype(int)

    # Compute returns
    daily_ret = daily['daily_return'].fillna(0)
    pos_changes = pos_shifted.diff().abs().fillna(0)
    cost = pos_changes * (COST_BPS / 10000)
    strat_ret = pos_shifted * daily_ret - cost

    n_long = (pos_shifted == 1).sum()
    n_flat = (pos_shifted == 0).sum()
    total = len(pos_shifted)
    ann_ret = strat_ret.mean() * 365
    ann_vol = strat_ret.std() * np.sqrt(365)
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0
    print(f"  Days: {n_long} long ({100*n_long/total:.1f}%), {n_flat} flat")
    print(f"  Ann return: {ann_ret:.2%}, Sharpe: {sharpe:.3f}")

    return strat_ret, pos_shifted


# ============================================================================
# BUY & HOLD BASELINE
# ============================================================================

def compute_buy_hold(daily):
    """Simple buy & hold BTC."""
    print("\n[BASELINE] Buy & Hold BTC")
    daily_ret = daily['daily_return'].fillna(0)
    ann_ret = daily_ret.mean() * 365
    ann_vol = daily_ret.std() * np.sqrt(365)
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0
    print(f"  Ann return: {ann_ret:.2%}, Sharpe: {sharpe:.3f}")
    return daily_ret


# ============================================================================
# METRICS
# ============================================================================

def compute_metrics(returns, name="Strategy"):
    """Compute standard performance metrics from a daily return series."""
    returns = returns.dropna()
    if len(returns) == 0 or returns.std() == 0:
        return {
            'name': name, 'ann_return': 0, 'ann_vol': 0, 'sharpe': 0,
            'sortino': 0, 'max_dd': 0, 'calmar': 0, 'total_return': 0,
            'worst_month': 0, 'pct_time_in_market': 0, 'n_days': 0
        }

    ann_ret = returns.mean() * 365
    ann_vol = returns.std() * np.sqrt(365)
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0

    # Sortino
    downside = returns[returns < 0]
    downside_vol = downside.std() * np.sqrt(365) if len(downside) > 0 else 0
    sortino = ann_ret / downside_vol if downside_vol > 0 else 0

    # Drawdown
    cum = (1 + returns).cumprod()
    peak = cum.cummax()
    dd = (cum - peak) / peak
    max_dd = dd.min()

    calmar = ann_ret / abs(max_dd) if max_dd != 0 else 0
    total_ret = cum.iloc[-1] - 1 if len(cum) > 0 else 0

    # Worst monthly return
    monthly_ret = (1 + returns).resample('ME').prod() - 1
    worst_month = monthly_ret.min() if len(monthly_ret) > 0 else 0

    return {
        'name': name,
        'ann_return': ann_ret,
        'ann_vol': ann_vol,
        'sharpe': sharpe,
        'sortino': sortino,
        'max_dd': max_dd,
        'calmar': calmar,
        'total_return': total_ret,
        'worst_month': worst_month,
        'n_days': len(returns)
    }


def compute_dd_recovery(returns, name="Strategy"):
    """Compute max drawdown and recovery time in days."""
    cum = (1 + returns.dropna()).cumprod()
    peak = cum.cummax()
    dd = (cum - peak) / peak

    max_dd = dd.min()
    max_dd_date = dd.idxmin()

    # Find when drawdown started
    peak_date = cum.loc[:max_dd_date].idxmax()

    # Find recovery date (when cum exceeds the peak again)
    post_dd = cum.loc[max_dd_date:]
    peak_val = cum.loc[peak_date]
    recovered = post_dd[post_dd >= peak_val]

    if len(recovered) > 0:
        recovery_date = recovered.index[0]
        recovery_days = (recovery_date - peak_date).days
    else:
        recovery_date = None
        recovery_days = (cum.index[-1] - peak_date).days  # still in drawdown

    return {
        'name': name,
        'max_dd': max_dd,
        'peak_date': peak_date,
        'trough_date': max_dd_date,
        'recovery_date': recovery_date,
        'recovery_days': recovery_days,
        'recovered': recovery_date is not None
    }


# ============================================================================
# REGIME CLASSIFICATION
# ============================================================================

def classify_regimes(daily):
    """
    Classify BTC into regimes using 50-day SMA and volatility:
    - UPTREND: price > SMA50 AND 20d vol < 80th percentile
    - DOWNTREND: price < SMA50 AND 20d vol < 80th percentile
    - RANGE: price near SMA50 (within 5%) AND low vol
    - CRISIS: 20d vol > 80th percentile
    """
    print("\n[REGIMES] Classifying BTC market regimes...")

    sma50 = daily['close'].rolling(50).mean()
    daily_logret = daily['log_return'].fillna(0)
    vol_20d = daily_logret.rolling(20).std() * np.sqrt(365) * 100
    vol_80pct = vol_20d.expanding(min_periods=60).quantile(0.80)

    pct_from_sma = (daily['close'] - sma50) / sma50

    regime = pd.Series('RANGE', index=daily.index)
    regime[pct_from_sma > 0.05] = 'UPTREND'
    regime[pct_from_sma < -0.05] = 'DOWNTREND'
    regime[vol_20d > vol_80pct] = 'CRISIS'

    for r in ['UPTREND', 'DOWNTREND', 'RANGE', 'CRISIS']:
        n = (regime == r).sum()
        print(f"  {r}: {n} days ({100*n/len(regime):.1f}%)")

    return regime


# ============================================================================
# PORTFOLIO CONSTRUCTION
# ============================================================================

def build_equal_weight_portfolio(ret1, ret2, ret3):
    """Equal weight 33/33/33 portfolio."""
    aligned = pd.DataFrame({
        'v3': ret1, 'intraday': ret2, 'macro': ret3
    }).fillna(0)
    portfolio_ret = (1/3) * aligned['v3'] + (1/3) * aligned['intraday'] + (1/3) * aligned['macro']
    return portfolio_ret


def build_risk_parity_portfolio(ret1, ret2, ret3, lookback=60):
    """Risk parity: weight inversely proportional to 60-day rolling vol."""
    aligned = pd.DataFrame({
        'v3': ret1, 'intraday': ret2, 'macro': ret3
    }).fillna(0)

    vol1 = aligned['v3'].rolling(lookback, min_periods=20).std()
    vol2 = aligned['intraday'].rolling(lookback, min_periods=20).std()
    vol3 = aligned['macro'].rolling(lookback, min_periods=20).std()

    # Inverse vol weights
    inv_vol1 = 1.0 / vol1.replace(0, np.nan)
    inv_vol2 = 1.0 / vol2.replace(0, np.nan)
    inv_vol3 = 1.0 / vol3.replace(0, np.nan)

    total_inv = inv_vol1 + inv_vol2 + inv_vol3
    w1 = (inv_vol1 / total_inv).fillna(1/3)
    w2 = (inv_vol2 / total_inv).fillna(1/3)
    w3 = (inv_vol3 / total_inv).fillna(1/3)

    portfolio_ret = w1 * aligned['v3'] + w2 * aligned['intraday'] + w3 * aligned['macro']
    return portfolio_ret, w1, w2, w3


def build_v3_anchor_portfolio(ret1, ret2, ret3):
    """V3 Anchor: 50% V3, 25% Intraday, 25% Macro."""
    aligned = pd.DataFrame({
        'v3': ret1, 'intraday': ret2, 'macro': ret3
    }).fillna(0)
    portfolio_ret = 0.50 * aligned['v3'] + 0.25 * aligned['intraday'] + 0.25 * aligned['macro']
    return portfolio_ret


def build_2strat_portfolio(ret1, ret2, w1=0.5, w2=0.5):
    """2-strategy portfolio."""
    aligned = pd.DataFrame({'s1': ret1, 's2': ret2}).fillna(0)
    return w1 * aligned['s1'] + w2 * aligned['s2']


# ============================================================================
# WALK-FORWARD PORTFOLIO TEST
# ============================================================================

def walk_forward_portfolio_test(daily, ret_v3, ret_intra, ret_macro, ret_v3_only):
    """
    6 windows: 12-month train / 6-month test.
    In each window: compute optimal allocation on train, apply to test.
    Report: portfolio Sharpe per window, and dSharpe vs V3-only.
    """
    print("\n[WALK-FORWARD] Portfolio Walk-Forward Test")

    end_date = daily.index.max()
    results = []
    window_start = pd.Timestamp(PERIOD_START)

    for w in range(WF_N_WINDOWS):
        train_end = window_start + pd.DateOffset(months=WF_TRAIN_MONTHS)
        test_start = train_end
        test_end = test_start + pd.DateOffset(months=WF_TEST_MONTHS)

        if test_end > end_date:
            break

        # Train period: find best allocation
        train_v3 = ret_v3.loc[window_start:train_end].fillna(0)
        train_intra = ret_intra.loc[window_start:train_end].fillna(0)
        train_macro = ret_macro.loc[window_start:train_end].fillna(0)

        # Test period
        test_v3 = ret_v3.loc[test_start:test_end].fillna(0)
        test_intra = ret_intra.loc[test_start:test_end].fillna(0)
        test_macro = ret_macro.loc[test_start:test_end].fillna(0)
        test_v3only = ret_v3_only.loc[test_start:test_end].fillna(0)

        if len(test_v3) < 30:
            window_start += pd.DateOffset(months=WF_TEST_MONTHS)
            continue

        # Train: optimize allocation by grid search over weights
        best_sharpe = -999
        best_weights = (1/3, 1/3, 1/3)
        for w1 in np.arange(0.1, 0.8, 0.1):
            for w2 in np.arange(0.1, 0.8 - w1, 0.1):
                w3 = 1.0 - w1 - w2
                if w3 < 0.05:
                    continue
                port = w1 * train_v3 + w2 * train_intra + w3 * train_macro
                if port.std() > 0:
                    s = (port.mean() * 365) / (port.std() * np.sqrt(365))
                    if s > best_sharpe:
                        best_sharpe = s
                        best_weights = (w1, w2, w3)

        # Apply optimized weights to test period
        w1_opt, w2_opt, w3_opt = best_weights
        test_port = w1_opt * test_v3 + w2_opt * test_intra + w3_opt * test_macro

        port_metrics = compute_metrics(test_port, f"Portfolio W{w+1}")
        v3only_metrics = compute_metrics(test_v3only, f"V3-only W{w+1}")

        # Also compute fixed allocation portfolios for comparison
        test_eq = (1/3) * test_v3 + (1/3) * test_intra + (1/3) * test_macro
        eq_metrics = compute_metrics(test_eq, f"EqW W{w+1}")

        test_anchor = 0.50 * test_v3 + 0.25 * test_intra + 0.25 * test_macro
        anchor_metrics = compute_metrics(test_anchor, f"Anchor W{w+1}")

        d_sharpe = port_metrics['sharpe'] - v3only_metrics['sharpe']

        results.append({
            'window': w + 1,
            'train': f"{window_start.date()} to {train_end.date()}",
            'test': f"{test_start.date()} to {test_end.date()}",
            'opt_weights': f"{w1_opt:.1f}/{w2_opt:.1f}/{w3_opt:.1f}",
            'port_sharpe': port_metrics['sharpe'],
            'v3_sharpe': v3only_metrics['sharpe'],
            'd_sharpe': d_sharpe,
            'port_better': port_metrics['sharpe'] > v3only_metrics['sharpe'],
            'eq_sharpe': eq_metrics['sharpe'],
            'anchor_sharpe': anchor_metrics['sharpe'],
            'port_max_dd': port_metrics['max_dd'],
            'v3_max_dd': v3only_metrics['max_dd']
        })

        print(f"  W{w+1}: Opt weights={w1_opt:.1f}/{w2_opt:.1f}/{w3_opt:.1f}, "
              f"Port Sharpe={port_metrics['sharpe']:.3f}, V3 Sharpe={v3only_metrics['sharpe']:.3f}, "
              f"dSharpe={d_sharpe:+.3f}")

        window_start += pd.DateOffset(months=WF_TEST_MONTHS)

    return results


# ============================================================================
# CORRELATION ANALYSIS
# ============================================================================

def compute_correlation_matrix(ret_dict):
    """Compute correlation matrix of daily returns."""
    df = pd.DataFrame(ret_dict).dropna()
    return df.corr()


def compute_rolling_correlations(ret1, ret2, name1, name2, window=90):
    """Rolling 90-day correlation between two return series."""
    aligned = pd.DataFrame({name1: ret1, name2: ret2}).dropna()
    rolling_corr = aligned[name1].rolling(window, min_periods=30).corr(aligned[name2])
    return rolling_corr


def compute_crash_correlations(ret_dict, daily, threshold_pctile=5):
    """
    Check if correlations spike during crashes.
    Crash days = bottom 5th percentile of BTC daily returns.
    """
    btc_ret = daily['daily_return'].dropna()
    crash_threshold = btc_ret.quantile(threshold_pctile / 100)
    crash_days = btc_ret[btc_ret <= crash_threshold].index

    df_all = pd.DataFrame(ret_dict).dropna()
    df_crash = df_all.loc[df_all.index.isin(crash_days)]
    df_normal = df_all.loc[~df_all.index.isin(crash_days)]

    corr_normal = df_normal.corr() if len(df_normal) > 30 else None
    corr_crash = df_crash.corr() if len(df_crash) > 10 else None

    return corr_normal, corr_crash, len(crash_days)


# ============================================================================
# REGIME ANALYSIS
# ============================================================================

def regime_analysis(ret_dict, regime_series):
    """Compute per-regime metrics for each strategy/portfolio."""
    results = {}
    for regime in ['UPTREND', 'DOWNTREND', 'RANGE', 'CRISIS']:
        regime_mask = regime_series == regime
        regime_days = regime_mask[regime_mask].index
        if len(regime_days) < 30:
            continue

        regime_results = {}
        for name, rets in ret_dict.items():
            rets_regime = rets.reindex(regime_days).dropna()
            if len(rets_regime) > 10:
                m = compute_metrics(rets_regime, name)
                regime_results[name] = m
            else:
                regime_results[name] = {'name': name, 'ann_return': 0, 'sharpe': 0, 'max_dd': 0}

        results[regime] = regime_results

    return results


# ============================================================================
# KILL CRITERIA EVALUATION
# ============================================================================

def evaluate_portfolio_kill_criteria(v3_metrics, port_metrics_dict, wf_results, corr_crash):
    """Apply kill criteria to the portfolio."""
    verdicts = {}

    for alloc_name, port_m in port_metrics_dict.items():
        v = {
            'name': alloc_name,
            'killed': False,
            'kill_reason': None,
            'warnings': []
        }

        # Kill 1: Portfolio Sharpe < V3 alone
        if port_m['sharpe'] < v3_metrics['sharpe']:
            v['killed'] = True
            v['kill_reason'] = (f"Portfolio Sharpe ({port_m['sharpe']:.3f}) < "
                               f"V3 alone ({v3_metrics['sharpe']:.3f})")
            verdicts[alloc_name] = v
            continue

        # Kill 2: MaxDD > V3 alone (more negative = worse)
        if port_m['max_dd'] < v3_metrics['max_dd']:
            v['killed'] = True
            v['kill_reason'] = (f"Portfolio MaxDD ({port_m['max_dd']:.2%}) > "
                               f"V3 alone ({v3_metrics['max_dd']:.2%})")
            verdicts[alloc_name] = v
            continue

        # Kill 3: Walk-forward < 3/6 windows where portfolio > V3
        if wf_results:
            n_better = sum(1 for r in wf_results if r['port_better'])
            n_total = len(wf_results)
            if n_better < 3:
                v['killed'] = True
                v['kill_reason'] = (f"Walk-forward: portfolio beats V3 in only "
                                   f"{n_better}/{n_total} windows (< 3/6)")
                verdicts[alloc_name] = v
                continue

        # Warning: correlation spikes during crashes
        if corr_crash is not None:
            max_crash_corr = corr_crash.values[np.triu_indices_from(corr_crash.values, k=1)].max()
            if max_crash_corr > 0.5:
                v['warnings'].append(
                    f"Correlation spikes to {max_crash_corr:.3f} during crashes "
                    f"(> 0.5 threshold) -- fair-weather diversification warning")

        verdicts[alloc_name] = v

    return verdicts


# ============================================================================
# REPORT GENERATION
# ============================================================================

def generate_report(
    v3_metrics, intraday_metrics, macro_metrics, bh_metrics,
    port_eq_metrics, port_rp_metrics, port_anchor_metrics,
    port_v3_intra_metrics, port_v3_macro_metrics,
    corr_matrix, corr_normal, corr_crash, n_crash_days,
    rolling_corrs, regime_results, regimes,
    wf_results, dd_results,
    kill_verdicts,
    rp_weights
):
    """Generate the full markdown report."""
    lines = []
    lines.append("# R110 -- Three-Strategy Portfolio: V3 Momentum + Intraday Breakout + Macro Regime")
    lines.append("")
    lines.append(f"**Date**: {datetime.now().strftime('%Y-%m-%d')}")
    lines.append(f"**Period**: {PERIOD_START} to {PERIOD_END}")
    lines.append(f"**Asset**: BTC spot")
    lines.append(f"**Cost assumption**: {COST_BPS} bps round-trip")
    lines.append("")

    # ---- Section 1: Strategy Definitions ----
    lines.append("## Strategy Definitions")
    lines.append("")
    lines.append("| # | Strategy | Signal | Rebalance | Position |")
    lines.append("|---|----------|--------|-----------|----------|")
    lines.append("| 1 | V3 Momentum | EMA(20) > EMA(50) | Weekly (Mon) | Long/Flat |")
    lines.append("| 2 | Intraday Breakout | 1h ret > 2% + vol > 2x avg | Per-bar | Long/Flat (8h max) |")
    lines.append("| 3 | Macro Regime | US10Y chg < 0 + DXY chg < 0 | Daily | Long/Flat |")
    lines.append("")

    # ---- Section 2: Individual Strategy Metrics ----
    lines.append("## Individual Strategy Metrics")
    lines.append("")
    lines.append("| Metric | Buy&Hold | V3 Momentum | Intraday Breakout | Macro Regime |")
    lines.append("|--------|----------|-------------|-------------------|--------------|")
    for metric, label, fmt in [
        ('ann_return', 'Ann. Return', '.2%'),
        ('ann_vol', 'Ann. Volatility', '.2%'),
        ('sharpe', 'Sharpe', '.3f'),
        ('sortino', 'Sortino', '.3f'),
        ('calmar', 'Calmar', '.3f'),
        ('max_dd', 'Max Drawdown', '.2%'),
        ('worst_month', 'Worst Month', '.2%'),
        ('total_return', 'Total Return', '.2%'),
    ]:
        bh_v = format(bh_metrics.get(metric, 0), fmt)
        v3_v = format(v3_metrics.get(metric, 0), fmt)
        intra_v = format(intraday_metrics.get(metric, 0), fmt)
        macro_v = format(macro_metrics.get(metric, 0), fmt)
        lines.append(f"| {label} | {bh_v} | {v3_v} | {intra_v} | {macro_v} |")
    lines.append("")

    # ---- Section 3: Correlation Structure ----
    lines.append("## Correlation Structure")
    lines.append("")
    lines.append("### 3x3 Correlation Matrix (Daily Returns)")
    lines.append("")
    if corr_matrix is not None:
        cols = corr_matrix.columns.tolist()
        lines.append("| | " + " | ".join(cols) + " |")
        lines.append("|" + "|".join(["---"] * (len(cols) + 1)) + "|")
        for idx, row in corr_matrix.iterrows():
            vals = " | ".join([f"{v:.4f}" for v in row.values])
            lines.append(f"| **{idx}** | {vals} |")
        lines.append("")

    lines.append("### Crash vs Normal Correlation")
    lines.append("")
    lines.append(f"Crash days (bottom 5th pctile BTC returns): **{n_crash_days}** days")
    lines.append("")
    if corr_normal is not None and corr_crash is not None:
        lines.append("**Normal-day correlations:**")
        lines.append("")
        cols = corr_normal.columns.tolist()
        lines.append("| | " + " | ".join(cols) + " |")
        lines.append("|" + "|".join(["---"] * (len(cols) + 1)) + "|")
        for idx, row in corr_normal.iterrows():
            vals = " | ".join([f"{v:.4f}" for v in row.values])
            lines.append(f"| **{idx}** | {vals} |")
        lines.append("")

        lines.append("**Crash-day correlations:**")
        lines.append("")
        cols = corr_crash.columns.tolist()
        lines.append("| | " + " | ".join(cols) + " |")
        lines.append("|" + "|".join(["---"] * (len(cols) + 1)) + "|")
        for idx, row in corr_crash.iterrows():
            vals = " | ".join([f"{v:.4f}" for v in row.values])
            lines.append(f"| **{idx}** | {vals} |")
        lines.append("")

    # Rolling correlation summary
    lines.append("### Rolling 90-Day Correlations (Summary)")
    lines.append("")
    lines.append("| Pair | Mean | Std | Min | Max |")
    lines.append("|------|------|-----|-----|-----|")
    for (pair_name, rc) in rolling_corrs:
        rc_clean = rc.dropna()
        if len(rc_clean) > 0:
            lines.append(f"| {pair_name} | {rc_clean.mean():.4f} | {rc_clean.std():.4f} | "
                        f"{rc_clean.min():.4f} | {rc_clean.max():.4f} |")
    lines.append("")

    # ---- Section 4: Portfolio Allocation Comparison ----
    lines.append("## Portfolio Allocation Comparison")
    lines.append("")
    all_port_metrics = {
        'Buy & Hold': bh_metrics,
        'V3 Only': v3_metrics,
        'V3 + Intraday (50/50)': port_v3_intra_metrics,
        'V3 + Macro (50/50)': port_v3_macro_metrics,
        'Equal Weight (33/33/33)': port_eq_metrics,
        'Risk Parity': port_rp_metrics,
        'V3 Anchor (50/25/25)': port_anchor_metrics,
    }

    lines.append("| Portfolio | Ann. Return | Sharpe | Sortino | Calmar | Max DD | Worst Month |")
    lines.append("|-----------|-------------|--------|---------|--------|--------|-------------|")
    for name, m in all_port_metrics.items():
        lines.append(f"| {name} | {m['ann_return']:.2%} | **{m['sharpe']:.3f}** | "
                    f"{m['sortino']:.3f} | {m['calmar']:.3f} | {m['max_dd']:.2%} | "
                    f"{m['worst_month']:.2%} |")
    lines.append("")

    # Risk parity average weights
    lines.append("### Risk Parity Average Weights")
    lines.append("")
    if rp_weights is not None:
        w1, w2, w3 = rp_weights
        lines.append(f"- V3 Momentum: **{w1.mean():.1%}** (range: {w1.min():.1%} to {w1.max():.1%})")
        lines.append(f"- Intraday Breakout: **{w2.mean():.1%}** (range: {w2.min():.1%} to {w2.max():.1%})")
        lines.append(f"- Macro Regime: **{w3.mean():.1%}** (range: {w3.min():.1%} to {w3.max():.1%})")
        lines.append("")

    # ---- Section 5: Regime Analysis ----
    lines.append("## Regime Analysis")
    lines.append("")

    # Per-regime Sharpe heatmap
    strategy_names = ['V3 Momentum', 'Intraday Breakout', 'Macro Regime',
                      'Equal Weight', 'V3 Anchor', 'Buy & Hold']
    regime_order = ['UPTREND', 'DOWNTREND', 'RANGE', 'CRISIS']

    lines.append("### Sharpe Ratio by Regime")
    lines.append("")
    header = "| Regime | " + " | ".join(strategy_names) + " |"
    lines.append(header)
    lines.append("|" + "|".join(["---"] * (len(strategy_names) + 1)) + "|")

    for regime in regime_order:
        if regime in regime_results:
            vals = []
            for sn in strategy_names:
                r = regime_results[regime].get(sn, {})
                s = r.get('sharpe', 0)
                vals.append(f"**{s:.3f}**" if s > 0.5 else f"{s:.3f}")
            lines.append(f"| {regime} | " + " | ".join(vals) + " |")
    lines.append("")

    lines.append("### Ann. Return by Regime")
    lines.append("")
    header = "| Regime | " + " | ".join(strategy_names) + " |"
    lines.append(header)
    lines.append("|" + "|".join(["---"] * (len(strategy_names) + 1)) + "|")

    for regime in regime_order:
        if regime in regime_results:
            vals = []
            for sn in strategy_names:
                r = regime_results[regime].get(sn, {})
                ar = r.get('ann_return', 0)
                vals.append(f"{ar:.2%}")
            lines.append(f"| {regime} | " + " | ".join(vals) + " |")
    lines.append("")

    # Best contributor per regime
    lines.append("### Best Strategy Contributor per Regime")
    lines.append("")
    indiv_names = ['V3 Momentum', 'Intraday Breakout', 'Macro Regime']
    for regime in regime_order:
        if regime in regime_results:
            best_name = None
            best_sharpe = -999
            for sn in indiv_names:
                r = regime_results[regime].get(sn, {})
                s = r.get('sharpe', -999)
                if s > best_sharpe:
                    best_sharpe = s
                    best_name = sn
            lines.append(f"- **{regime}**: {best_name} (Sharpe: {best_sharpe:.3f})")
    lines.append("")

    # Does portfolio fix V3 RANGE weakness?
    lines.append("### Does Portfolio Fix V3's RANGE Weakness?")
    lines.append("")
    if 'RANGE' in regime_results:
        v3_range = regime_results['RANGE'].get('V3 Momentum', {}).get('sharpe', 0)
        eq_range = regime_results['RANGE'].get('Equal Weight', {}).get('sharpe', 0)
        anchor_range = regime_results['RANGE'].get('V3 Anchor', {}).get('sharpe', 0)
        lines.append(f"- V3 alone in RANGE: Sharpe = {v3_range:.3f}")
        lines.append(f"- Equal Weight in RANGE: Sharpe = {eq_range:.3f}")
        lines.append(f"- V3 Anchor in RANGE: Sharpe = {anchor_range:.3f}")
        if eq_range > v3_range or anchor_range > v3_range:
            lines.append(f"- **YES**: Portfolio improves RANGE regime performance")
        else:
            lines.append(f"- **NO**: Portfolio does not meaningfully improve RANGE regime")
    lines.append("")

    # ---- Section 6: Walk-Forward ----
    lines.append("## Walk-Forward Portfolio Test")
    lines.append("")
    lines.append(f"Configuration: {WF_TRAIN_MONTHS}mo train / {WF_TEST_MONTHS}mo test, {WF_N_WINDOWS} windows")
    lines.append("")

    if wf_results:
        lines.append("| Window | Test Period | Opt Weights | Port Sharpe | V3 Sharpe | dSharpe | EqW Sharpe | Anchor Sharpe | Port > V3? |")
        lines.append("|--------|-------------|-------------|-------------|-----------|---------|------------|---------------|------------|")
        for r in wf_results:
            better = "YES" if r['port_better'] else "NO"
            lines.append(f"| W{r['window']} | {r['test']} | {r['opt_weights']} | "
                        f"{r['port_sharpe']:.3f} | {r['v3_sharpe']:.3f} | {r['d_sharpe']:+.3f} | "
                        f"{r['eq_sharpe']:.3f} | {r['anchor_sharpe']:.3f} | {better} |")

        n_better = sum(1 for r in wf_results if r['port_better'])
        n_total = len(wf_results)
        avg_d_sharpe = np.mean([r['d_sharpe'] for r in wf_results])
        lines.append("")
        lines.append(f"**Summary**: Portfolio beats V3 in **{n_better}/{n_total}** windows, "
                    f"avg dSharpe = **{avg_d_sharpe:+.3f}**")
    lines.append("")

    # ---- Section 7: Drawdown Recovery ----
    lines.append("## Drawdown Recovery Analysis")
    lines.append("")
    lines.append("| Strategy | Max DD | Peak Date | Trough Date | Recovery Date | Recovery Days | Recovered? |")
    lines.append("|----------|--------|-----------|-------------|---------------|---------------|------------|")
    for dd in dd_results:
        rec_date = dd['recovery_date'].date() if dd['recovery_date'] is not None else "N/A"
        recovered = "YES" if dd['recovered'] else "NO (still in DD)"
        lines.append(f"| {dd['name']} | {dd['max_dd']:.2%} | {dd['peak_date'].date()} | "
                    f"{dd['trough_date'].date()} | {rec_date} | {dd['recovery_days']} | {recovered} |")
    lines.append("")

    # ---- Section 8: Kill Criteria ----
    lines.append("## Kill Criteria Evaluation")
    lines.append("")
    lines.append("| Criterion | Threshold | Result |")
    lines.append("|-----------|-----------|--------|")

    # Use best portfolio for kill evaluation
    best_port_name = None
    best_port_sharpe = -999
    for name, m in [('Equal Weight', port_eq_metrics), ('Risk Parity', port_rp_metrics),
                     ('V3 Anchor', port_anchor_metrics)]:
        if m['sharpe'] > best_port_sharpe:
            best_port_sharpe = m['sharpe']
            best_port_name = name
    best_port_m = {'Equal Weight': port_eq_metrics, 'Risk Parity': port_rp_metrics,
                   'V3 Anchor': port_anchor_metrics}[best_port_name]

    # K1: Sharpe
    k1_pass = best_port_m['sharpe'] >= v3_metrics['sharpe']
    k1_str = f"PASS ({best_port_name} Sharpe {best_port_m['sharpe']:.3f} >= V3 {v3_metrics['sharpe']:.3f})" if k1_pass else \
             f"**KILL** ({best_port_name} Sharpe {best_port_m['sharpe']:.3f} < V3 {v3_metrics['sharpe']:.3f})"
    lines.append(f"| Portfolio Sharpe >= V3 | {v3_metrics['sharpe']:.3f} | {k1_str} |")

    # K2: MaxDD
    k2_pass = best_port_m['max_dd'] >= v3_metrics['max_dd']  # less negative = better
    k2_str = f"PASS ({best_port_m['max_dd']:.2%} vs V3 {v3_metrics['max_dd']:.2%})" if k2_pass else \
             f"**KILL** ({best_port_m['max_dd']:.2%} worse than V3 {v3_metrics['max_dd']:.2%})"
    lines.append(f"| Portfolio MaxDD <= V3 | {v3_metrics['max_dd']:.2%} | {k2_str} |")

    # K3: Walk-forward
    if wf_results:
        n_better = sum(1 for r in wf_results if r['port_better'])
        n_total = len(wf_results)
        k3_pass = n_better >= 3
        k3_str = f"PASS ({n_better}/{n_total} windows)" if k3_pass else \
                 f"**KILL** ({n_better}/{n_total} windows < 3/6)"
    else:
        k3_str = "N/A (no walk-forward data)"
        k3_pass = False
    lines.append(f"| Walk-forward >= 3/6 | 3/6 | {k3_str} |")

    # K4: Crash correlation
    if corr_crash is not None:
        max_crash_corr = corr_crash.values[np.triu_indices_from(corr_crash.values, k=1)].max()
        k4_warn = max_crash_corr > 0.5
        k4_str = f"**WARNING** (max crash corr = {max_crash_corr:.3f} > 0.5)" if k4_warn else \
                 f"OK (max crash corr = {max_crash_corr:.3f} <= 0.5)"
    else:
        k4_str = "N/A (insufficient crash data)"
        k4_warn = False
    lines.append(f"| Crash corr <= 0.5 | 0.5 | {k4_str} |")
    lines.append("")

    all_kill_pass = k1_pass and k2_pass and k3_pass
    overall = "PASS" if all_kill_pass else "KILL"
    lines.append(f"**Overall Verdict**: **{overall}**")
    if k4_warn:
        lines.append(f"  - WARNING: fair-weather diversification detected")
    lines.append("")

    # ---- Per-allocation verdicts ----
    lines.append("### Per-Allocation Kill Verdicts")
    lines.append("")
    lines.append("| Allocation | Sharpe | MaxDD | Verdict | Reason |")
    lines.append("|------------|--------|-------|---------|--------|")
    for name, v in kill_verdicts.items():
        status = "KILLED" if v['killed'] else "PASSED"
        reason = v['kill_reason'] if v['killed'] else "All criteria passed"
        m = {'Equal Weight': port_eq_metrics, 'Risk Parity': port_rp_metrics,
             'V3 Anchor': port_anchor_metrics}.get(name, {})
        lines.append(f"| {name} | {m.get('sharpe', 0):.3f} | {m.get('max_dd', 0):.2%} | "
                    f"**{status}** | {reason} |")
    lines.append("")

    # ---- Section 9: Conclusion ----
    lines.append("## Conclusion")
    lines.append("")

    if all_kill_pass:
        lines.append(f"The three-strategy portfolio **PASSES** all kill criteria.")
        lines.append(f"**Best allocation method**: {best_port_name} (Sharpe: {best_port_m['sharpe']:.3f})")
        lines.append("")
        lines.append("### Recommendation")
        lines.append("")
        lines.append(f"- Use **{best_port_name}** allocation")
        lines.append(f"- Portfolio Sharpe: {best_port_m['sharpe']:.3f} vs V3-only: {v3_metrics['sharpe']:.3f} "
                    f"(+{best_port_m['sharpe'] - v3_metrics['sharpe']:.3f})")
        lines.append(f"- Portfolio MaxDD: {best_port_m['max_dd']:.2%} vs V3-only: {v3_metrics['max_dd']:.2%}")
    else:
        lines.append(f"The three-strategy portfolio **FAILS** kill criteria.")
        lines.append("")
        # Identify which allocations passed
        passed = [n for n, v in kill_verdicts.items() if not v['killed']]
        if passed:
            lines.append(f"Allocations that passed: {', '.join(passed)}")
        else:
            lines.append("No allocation method passed all kill criteria.")
        lines.append("")
        lines.append("### What Failed")
        for name, v in kill_verdicts.items():
            if v['killed']:
                lines.append(f"- **{name}**: {v['kill_reason']}")

    lines.append("")
    return "\n".join(lines)


# ============================================================================
# MAIN
# ============================================================================

def main():
    print("=" * 80)
    print("R110: THREE-STRATEGY PORTFOLIO TEST")
    print("=" * 80)

    # ── Load Data ────────────────────────────────────────────────────────────
    btc_1h = load_btc_1h()
    daily = load_btc_daily(btc_1h)
    macro = load_macro()

    # ── Compute Individual Strategies ────────────────────────────────────────
    v3_ret, v3_pos = compute_v3_momentum(daily)
    v3_metrics = compute_metrics(v3_ret, "V3 Momentum")

    intraday_ret, intraday_pos = compute_intraday_momentum(btc_1h, daily)
    intraday_metrics = compute_metrics(intraday_ret, "Intraday Breakout")

    macro_ret, macro_pos = compute_macro_regime(daily, macro)
    macro_metrics = compute_metrics(macro_ret, "Macro Regime")

    bh_ret = compute_buy_hold(daily)
    bh_metrics = compute_metrics(bh_ret, "Buy & Hold")

    print("\n" + "=" * 60)
    print("INDIVIDUAL STRATEGY SUMMARY")
    print("=" * 60)
    for m in [bh_metrics, v3_metrics, intraday_metrics, macro_metrics]:
        print(f"  {m['name']:25s}: Sharpe={m['sharpe']:.3f}, Ann={m['ann_return']:.2%}, MaxDD={m['max_dd']:.2%}")

    # ── Portfolio Construction ───────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("PORTFOLIO CONSTRUCTION")
    print("=" * 60)

    # 3-strategy portfolios
    port_eq_ret = build_equal_weight_portfolio(v3_ret, intraday_ret, macro_ret)
    port_eq_metrics = compute_metrics(port_eq_ret, "Equal Weight")
    print(f"\n  Equal Weight (33/33/33): Sharpe={port_eq_metrics['sharpe']:.3f}")

    port_rp_ret, rp_w1, rp_w2, rp_w3 = build_risk_parity_portfolio(v3_ret, intraday_ret, macro_ret)
    port_rp_metrics = compute_metrics(port_rp_ret, "Risk Parity")
    print(f"  Risk Parity: Sharpe={port_rp_metrics['sharpe']:.3f}")
    print(f"    Avg weights: V3={rp_w1.mean():.1%}, Intraday={rp_w2.mean():.1%}, Macro={rp_w3.mean():.1%}")

    port_anchor_ret = build_v3_anchor_portfolio(v3_ret, intraday_ret, macro_ret)
    port_anchor_metrics = compute_metrics(port_anchor_ret, "V3 Anchor")
    print(f"  V3 Anchor (50/25/25): Sharpe={port_anchor_metrics['sharpe']:.3f}")

    # 2-strategy portfolios
    port_v3_intra_ret = build_2strat_portfolio(v3_ret, intraday_ret)
    port_v3_intra_metrics = compute_metrics(port_v3_intra_ret, "V3 + Intraday")
    print(f"\n  V3 + Intraday (50/50): Sharpe={port_v3_intra_metrics['sharpe']:.3f}")

    port_v3_macro_ret = build_2strat_portfolio(v3_ret, macro_ret)
    port_v3_macro_metrics = compute_metrics(port_v3_macro_ret, "V3 + Macro")
    print(f"  V3 + Macro (50/50): Sharpe={port_v3_macro_metrics['sharpe']:.3f}")

    # ── Correlation Structure ────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("CORRELATION STRUCTURE")
    print("=" * 60)

    ret_dict = {
        'V3 Momentum': v3_ret,
        'Intraday Breakout': intraday_ret,
        'Macro Regime': macro_ret
    }

    corr_matrix = compute_correlation_matrix(ret_dict)
    print("\n3x3 Correlation Matrix:")
    print(corr_matrix.to_string())

    # Rolling correlations
    rc_v3_intra = compute_rolling_correlations(v3_ret, intraday_ret, 'V3', 'Intraday')
    rc_v3_macro = compute_rolling_correlations(v3_ret, macro_ret, 'V3', 'Macro')
    rc_intra_macro = compute_rolling_correlations(intraday_ret, macro_ret, 'Intraday', 'Macro')
    rolling_corrs = [
        ('V3 vs Intraday', rc_v3_intra),
        ('V3 vs Macro', rc_v3_macro),
        ('Intraday vs Macro', rc_intra_macro)
    ]

    # Crash correlations
    corr_normal, corr_crash, n_crash_days = compute_crash_correlations(ret_dict, daily)
    if corr_crash is not None:
        print(f"\nCrash-day correlations ({n_crash_days} crash days):")
        print(corr_crash.to_string())
    else:
        print(f"\nInsufficient crash data for crash correlation analysis.")

    # ── Regime Analysis ──────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("REGIME ANALYSIS")
    print("=" * 60)

    regimes = classify_regimes(daily)

    # Build full return dict for regime analysis including portfolios
    full_ret_dict = {
        'V3 Momentum': v3_ret,
        'Intraday Breakout': intraday_ret,
        'Macro Regime': macro_ret,
        'Equal Weight': port_eq_ret,
        'V3 Anchor': port_anchor_ret,
        'Buy & Hold': bh_ret
    }

    regime_results = regime_analysis(full_ret_dict, regimes)

    for regime in ['UPTREND', 'DOWNTREND', 'RANGE', 'CRISIS']:
        if regime in regime_results:
            print(f"\n  {regime}:")
            for sn, r in regime_results[regime].items():
                print(f"    {sn:25s}: Sharpe={r.get('sharpe', 0):.3f}, Ann={r.get('ann_return', 0):.2%}")

    # ── Walk-Forward ─────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("WALK-FORWARD TEST")
    print("=" * 60)

    wf_results = walk_forward_portfolio_test(daily, v3_ret, intraday_ret, macro_ret, v3_ret)

    # ── Drawdown Recovery ────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("DRAWDOWN RECOVERY")
    print("=" * 60)

    dd_results = []
    for name, rets in [('V3 Only', v3_ret), ('Equal Weight', port_eq_ret),
                        ('Risk Parity', port_rp_ret), ('V3 Anchor', port_anchor_ret),
                        ('Buy & Hold', bh_ret)]:
        dd = compute_dd_recovery(rets, name)
        dd_results.append(dd)
        rec_str = f"{dd['recovery_days']} days" if dd['recovered'] else f"{dd['recovery_days']} days (not recovered)"
        print(f"  {name:20s}: MaxDD={dd['max_dd']:.2%}, Recovery={rec_str}")

    # ── Kill Criteria ────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("KILL CRITERIA EVALUATION")
    print("=" * 60)

    port_metrics_dict = {
        'Equal Weight': port_eq_metrics,
        'Risk Parity': port_rp_metrics,
        'V3 Anchor': port_anchor_metrics
    }

    kill_verdicts = evaluate_portfolio_kill_criteria(
        v3_metrics, port_metrics_dict, wf_results, corr_crash
    )

    for name, v in kill_verdicts.items():
        status = "KILLED" if v['killed'] else "PASSED"
        print(f"  {name:20s}: {status}")
        if v['killed']:
            print(f"    Reason: {v['kill_reason']}")
        for w in v.get('warnings', []):
            print(f"    WARNING: {w}")

    # ── Generate Report ──────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("GENERATING REPORT")
    print("=" * 60)

    report = generate_report(
        v3_metrics=v3_metrics,
        intraday_metrics=intraday_metrics,
        macro_metrics=macro_metrics,
        bh_metrics=bh_metrics,
        port_eq_metrics=port_eq_metrics,
        port_rp_metrics=port_rp_metrics,
        port_anchor_metrics=port_anchor_metrics,
        port_v3_intra_metrics=port_v3_intra_metrics,
        port_v3_macro_metrics=port_v3_macro_metrics,
        corr_matrix=corr_matrix,
        corr_normal=corr_normal,
        corr_crash=corr_crash,
        n_crash_days=n_crash_days,
        rolling_corrs=rolling_corrs,
        regime_results=regime_results,
        regimes=regimes,
        wf_results=wf_results,
        dd_results=dd_results,
        kill_verdicts=kill_verdicts,
        rp_weights=(rp_w1, rp_w2, rp_w3)
    )

    with open(OUTPUT_MD, 'w') as f:
        f.write(report)
    print(f"\nReport written to: {OUTPUT_MD}")

    # Final summary
    print("\n" + "=" * 80)
    print("FINAL SUMMARY")
    print("=" * 80)
    best_name = max(port_metrics_dict.keys(), key=lambda k: port_metrics_dict[k]['sharpe'])
    best_m = port_metrics_dict[best_name]
    print(f"  Best allocation: {best_name}")
    print(f"  Portfolio Sharpe: {best_m['sharpe']:.3f} vs V3-only: {v3_metrics['sharpe']:.3f} "
          f"(delta: {best_m['sharpe'] - v3_metrics['sharpe']:+.3f})")
    print(f"  Portfolio MaxDD: {best_m['max_dd']:.2%} vs V3-only: {v3_metrics['max_dd']:.2%}")

    any_passed = any(not v['killed'] for v in kill_verdicts.values())
    if any_passed:
        print(f"\n  VERDICT: PORTFOLIO ADDS VALUE -- proceed to implementation consideration")
    else:
        print(f"\n  VERDICT: PORTFOLIO KILLED -- diversification not working, V3 alone is superior")

    print("=" * 80)


if __name__ == '__main__':
    main()
