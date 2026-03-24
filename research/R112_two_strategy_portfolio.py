#!/workspace/venv/bin/python
"""
R112: 2-Strategy Portfolio Test -- V3 Momentum + Intraday Momentum Only
=========================================================================

Context:
  R109 KILLED Macro Regime Rotation (non-stationary IC, fragile params).
  R110 tested a 3-strategy portfolio but one leg is now dead.
  This test evaluates the simpler V3 + Intraday Momentum combination.

From R110 reference:
  - V3 vs Intraday correlation: 0.188 (daily), 0.213 (crash-day)
  - V3+Intraday 50/50: Sharpe 0.746, MaxDD -36.59%

Strategy Definitions (identical to R110):
  1. V3 Momentum: Long when EMA(20) > EMA(50), weekly rebalance
  2. Intraday Momentum Breakout: 1h return > 2% AND vol > 2x avg, trailing stop

Portfolio Construction:
  a) 50/50 equal weight
  b) 60/40 (V3 heavy)
  c) 70/30 (V3 anchor)
  d) Risk parity (inverse vol weighted)

Kill Criteria:
  - Portfolio Sharpe < V3 alone -> KILL
  - MaxDD > V3 alone -> KILL
  - Walk-forward < 3/6 windows where portfolio > V3 -> KILL

Key Question: Does removing Macro Regime hurt significantly, or was Intraday
the main diversifier?

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
OUTPUT_MD = PROJECT_DIR / 'research' / 'R112_two_strategy_portfolio.md'

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


# ============================================================================
# STRATEGY 1: V3 MOMENTUM (identical to R110)
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
# STRATEGY 2: INTRADAY MOMENTUM BREAKOUT (identical to R110)
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
# REGIME CLASSIFICATION (identical to R110)
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
# 2-STRATEGY PORTFOLIO CONSTRUCTION
# ============================================================================

def build_2strat_portfolio(ret_v3, ret_intra, w_v3=0.5, w_intra=0.5):
    """Build a 2-strategy portfolio with given weights."""
    aligned = pd.DataFrame({'v3': ret_v3, 'intraday': ret_intra}).fillna(0)
    return w_v3 * aligned['v3'] + w_intra * aligned['intraday']


def build_risk_parity_2strat(ret_v3, ret_intra, lookback=60):
    """Risk parity: weight inversely proportional to 60-day rolling vol."""
    aligned = pd.DataFrame({'v3': ret_v3, 'intraday': ret_intra}).fillna(0)

    vol_v3 = aligned['v3'].rolling(lookback, min_periods=20).std()
    vol_intra = aligned['intraday'].rolling(lookback, min_periods=20).std()

    # Inverse vol weights
    inv_vol_v3 = 1.0 / vol_v3.replace(0, np.nan)
    inv_vol_intra = 1.0 / vol_intra.replace(0, np.nan)

    total_inv = inv_vol_v3 + inv_vol_intra
    w_v3 = (inv_vol_v3 / total_inv).fillna(0.5)
    w_intra = (inv_vol_intra / total_inv).fillna(0.5)

    portfolio_ret = w_v3 * aligned['v3'] + w_intra * aligned['intraday']
    return portfolio_ret, w_v3, w_intra


# ============================================================================
# CORRELATION ANALYSIS
# ============================================================================

def compute_correlation_pair(ret_v3, ret_intra):
    """Compute correlation between V3 and Intraday returns."""
    df = pd.DataFrame({'V3 Momentum': ret_v3, 'Intraday Breakout': ret_intra}).dropna()
    return df.corr()


def compute_rolling_correlation(ret_v3, ret_intra, window=90):
    """Rolling 90-day correlation between V3 and Intraday."""
    aligned = pd.DataFrame({'V3': ret_v3, 'Intraday': ret_intra}).dropna()
    rolling_corr = aligned['V3'].rolling(window, min_periods=30).corr(aligned['Intraday'])
    return rolling_corr


def compute_crash_vs_normal_corr(ret_v3, ret_intra, daily, threshold_pctile=5):
    """
    Check if V3-Intraday correlation spikes during crashes.
    Crash days = bottom 5th percentile of BTC daily returns.
    """
    btc_ret = daily['daily_return'].dropna()
    crash_threshold = btc_ret.quantile(threshold_pctile / 100)
    crash_days = btc_ret[btc_ret <= crash_threshold].index

    df_all = pd.DataFrame({'V3 Momentum': ret_v3, 'Intraday Breakout': ret_intra}).dropna()
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
# WALK-FORWARD PORTFOLIO TEST (2-strategy version)
# ============================================================================

def walk_forward_2strat(daily, ret_v3, ret_intra, ret_v3_only):
    """
    6 windows: 12-month train / 6-month test.
    In each window: optimize V3/Intraday allocation on train, apply to test.
    Grid search over V3 weight from 0.1 to 0.9 in steps of 0.05.
    """
    print("\n[WALK-FORWARD] 2-Strategy Portfolio Walk-Forward Test")

    end_date = daily.index.max()
    results = []
    window_start = pd.Timestamp(PERIOD_START)

    for w in range(WF_N_WINDOWS):
        train_end = window_start + pd.DateOffset(months=WF_TRAIN_MONTHS)
        test_start = train_end
        test_end = test_start + pd.DateOffset(months=WF_TEST_MONTHS)

        if test_end > end_date:
            break

        # Train period
        train_v3 = ret_v3.loc[window_start:train_end].fillna(0)
        train_intra = ret_intra.loc[window_start:train_end].fillna(0)

        # Test period
        test_v3 = ret_v3.loc[test_start:test_end].fillna(0)
        test_intra = ret_intra.loc[test_start:test_end].fillna(0)
        test_v3only = ret_v3_only.loc[test_start:test_end].fillna(0)

        if len(test_v3) < 30:
            window_start += pd.DateOffset(months=WF_TEST_MONTHS)
            continue

        # Train: optimize V3 weight by grid search (finer grid than R110)
        best_sharpe = -999
        best_w_v3 = 0.5
        for wv3 in np.arange(0.10, 0.91, 0.05):
            w_intra = 1.0 - wv3
            port = wv3 * train_v3 + w_intra * train_intra
            if port.std() > 0:
                s = (port.mean() * 365) / (port.std() * np.sqrt(365))
                if s > best_sharpe:
                    best_sharpe = s
                    best_w_v3 = wv3

        best_w_intra = 1.0 - best_w_v3

        # Apply optimized weights to test period
        test_port = best_w_v3 * test_v3 + best_w_intra * test_intra

        port_metrics = compute_metrics(test_port, f"Portfolio W{w+1}")
        v3only_metrics = compute_metrics(test_v3only, f"V3-only W{w+1}")

        # Also compute fixed allocation portfolios for comparison
        test_50_50 = 0.50 * test_v3 + 0.50 * test_intra
        m_50_50 = compute_metrics(test_50_50, f"50/50 W{w+1}")

        test_60_40 = 0.60 * test_v3 + 0.40 * test_intra
        m_60_40 = compute_metrics(test_60_40, f"60/40 W{w+1}")

        test_70_30 = 0.70 * test_v3 + 0.30 * test_intra
        m_70_30 = compute_metrics(test_70_30, f"70/30 W{w+1}")

        d_sharpe = port_metrics['sharpe'] - v3only_metrics['sharpe']

        results.append({
            'window': w + 1,
            'train': f"{window_start.date()} to {train_end.date()}",
            'test': f"{test_start.date()} to {test_end.date()}",
            'opt_w_v3': best_w_v3,
            'opt_w_intra': best_w_intra,
            'opt_weights': f"{best_w_v3:.2f}/{best_w_intra:.2f}",
            'port_sharpe': port_metrics['sharpe'],
            'v3_sharpe': v3only_metrics['sharpe'],
            'd_sharpe': d_sharpe,
            'port_better': port_metrics['sharpe'] > v3only_metrics['sharpe'],
            'm_50_50_sharpe': m_50_50['sharpe'],
            'm_60_40_sharpe': m_60_40['sharpe'],
            'm_70_30_sharpe': m_70_30['sharpe'],
            'port_max_dd': port_metrics['max_dd'],
            'v3_max_dd': v3only_metrics['max_dd']
        })

        print(f"  W{w+1}: Opt V3/Intra={best_w_v3:.2f}/{best_w_intra:.2f}, "
              f"Port Sharpe={port_metrics['sharpe']:.3f}, V3 Sharpe={v3only_metrics['sharpe']:.3f}, "
              f"dSharpe={d_sharpe:+.3f}")

        window_start += pd.DateOffset(months=WF_TEST_MONTHS)

    return results


# ============================================================================
# REPORT GENERATION
# ============================================================================

def generate_report(
    v3_metrics, intraday_metrics, bh_metrics,
    port_50_50_metrics, port_60_40_metrics, port_70_30_metrics, port_rp_metrics,
    corr_matrix, corr_normal, corr_crash, n_crash_days,
    rolling_corr,
    regime_results,
    wf_results,
    dd_results,
    rp_weights,
    # R110 reference metrics for comparison
    r110_eq_metrics, r110_rp_metrics, r110_anchor_metrics
):
    """Generate the full markdown report for R112."""
    lines = []
    lines.append("# R112 -- 2-Strategy Portfolio: V3 Momentum + Intraday Momentum")
    lines.append("")
    lines.append(f"**Date**: {datetime.now().strftime('%Y-%m-%d')}")
    lines.append(f"**Period**: {PERIOD_START} to {PERIOD_END}")
    lines.append(f"**Asset**: BTC spot")
    lines.append(f"**Cost assumption**: {COST_BPS} bps round-trip")
    lines.append("")
    lines.append("**Context**: R109 KILLED the Macro Regime Rotation signal (non-stationary IC, fragile params). "
                 "R110 tested a 3-strategy portfolio but one leg is now dead. "
                 "This research evaluates the simpler V3 + Intraday Momentum combination.")
    lines.append("")

    # ---- Section 1: Strategy Definitions ----
    lines.append("## Strategy Definitions")
    lines.append("")
    lines.append("| # | Strategy | Signal | Rebalance | Position |")
    lines.append("|---|----------|--------|-----------|----------|")
    lines.append("| 1 | V3 Momentum | EMA(20) > EMA(50) | Weekly (Mon) | Long/Flat |")
    lines.append("| 2 | Intraday Breakout | 1h ret > 2% + vol > 2x avg | Per-bar | Long/Flat (8h max) |")
    lines.append("")

    # ---- Section 2: Individual Strategy Metrics ----
    lines.append("## Individual Strategy Metrics")
    lines.append("")
    lines.append("| Metric | Buy&Hold | V3 Momentum | Intraday Breakout |")
    lines.append("|--------|----------|-------------|-------------------|")
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
        lines.append(f"| {label} | {bh_v} | {v3_v} | {intra_v} |")
    lines.append("")

    # ---- Section 3: Correlation Structure ----
    lines.append("## Correlation Structure")
    lines.append("")
    lines.append("### V3 vs Intraday Correlation (Daily Returns)")
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

    if corr_normal is not None:
        normal_corr_val = corr_normal.iloc[0, 1]
        lines.append(f"- Normal-day correlation: **{normal_corr_val:.4f}**")
    if corr_crash is not None:
        crash_corr_val = corr_crash.iloc[0, 1]
        lines.append(f"- Crash-day correlation: **{crash_corr_val:.4f}**")
        lines.append(f"- Correlation change under stress: **{crash_corr_val - normal_corr_val:+.4f}**")
        if crash_corr_val < 0.5:
            lines.append(f"- LOW crash correlation: good diversification holds under stress")
        else:
            lines.append(f"- WARNING: crash correlation > 0.5, fair-weather diversification")
    lines.append("")

    # Rolling correlation summary
    lines.append("### Rolling 90-Day Correlation (Summary)")
    lines.append("")
    rc_clean = rolling_corr.dropna()
    if len(rc_clean) > 0:
        lines.append(f"| Stat | Value |")
        lines.append(f"|------|-------|")
        lines.append(f"| Mean | {rc_clean.mean():.4f} |")
        lines.append(f"| Std | {rc_clean.std():.4f} |")
        lines.append(f"| Min | {rc_clean.min():.4f} |")
        lines.append(f"| Max | {rc_clean.max():.4f} |")
        lines.append(f"| Pct > 0.5 | {(rc_clean > 0.5).mean():.1%} |")
        lines.append(f"| Pct < 0 | {(rc_clean < 0).mean():.1%} |")
    lines.append("")

    # ---- Section 4: Portfolio Allocation Comparison ----
    lines.append("## Portfolio Allocation Results")
    lines.append("")

    all_port_metrics = {
        'Buy & Hold': bh_metrics,
        'V3 Only': v3_metrics,
        'Intraday Only': intraday_metrics,
        '50/50 (Equal Wt)': port_50_50_metrics,
        '60/40 (V3 Heavy)': port_60_40_metrics,
        '70/30 (V3 Anchor)': port_70_30_metrics,
        'Risk Parity': port_rp_metrics,
    }

    lines.append("| Portfolio | Ann. Return | Ann. Vol | Sharpe | Sortino | Calmar | Max DD | Worst Month |")
    lines.append("|-----------|-------------|----------|--------|---------|--------|--------|-------------|")
    for name, m in all_port_metrics.items():
        lines.append(f"| {name} | {m['ann_return']:.2%} | {m['ann_vol']:.2%} | **{m['sharpe']:.3f}** | "
                    f"{m['sortino']:.3f} | {m['calmar']:.3f} | {m['max_dd']:.2%} | "
                    f"{m['worst_month']:.2%} |")
    lines.append("")

    # Risk parity weights
    lines.append("### Risk Parity Average Weights")
    lines.append("")
    if rp_weights is not None:
        w_v3, w_intra = rp_weights
        lines.append(f"- V3 Momentum: **{w_v3.mean():.1%}** (range: {w_v3.min():.1%} to {w_v3.max():.1%})")
        lines.append(f"- Intraday Breakout: **{w_intra.mean():.1%}** (range: {w_intra.min():.1%} to {w_intra.max():.1%})")
    lines.append("")

    # Delta vs V3 only
    lines.append("### Improvement vs V3-Only")
    lines.append("")
    lines.append("| Portfolio | dSharpe | dMaxDD | dSortino |")
    lines.append("|-----------|---------|--------|----------|")
    for name, m in [('50/50', port_50_50_metrics), ('60/40', port_60_40_metrics),
                     ('70/30', port_70_30_metrics), ('Risk Parity', port_rp_metrics)]:
        d_sharpe = m['sharpe'] - v3_metrics['sharpe']
        d_dd = m['max_dd'] - v3_metrics['max_dd']  # less negative = improvement
        d_sortino = m['sortino'] - v3_metrics['sortino']
        lines.append(f"| {name} | {d_sharpe:+.3f} | {d_dd:+.2%} | {d_sortino:+.3f} |")
    lines.append("")

    # ---- Section 5: Regime Analysis ----
    lines.append("## Regime Analysis")
    lines.append("")

    strategy_names = ['V3 Momentum', 'Intraday Breakout', '50/50', '60/40', '70/30', 'Buy & Hold']
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
    indiv_names = ['V3 Momentum', 'Intraday Breakout']
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
        p50_range = regime_results['RANGE'].get('50/50', {}).get('sharpe', 0)
        p60_range = regime_results['RANGE'].get('60/40', {}).get('sharpe', 0)
        p70_range = regime_results['RANGE'].get('70/30', {}).get('sharpe', 0)
        lines.append(f"- V3 alone in RANGE: Sharpe = {v3_range:.3f}")
        lines.append(f"- 50/50 in RANGE: Sharpe = {p50_range:.3f}")
        lines.append(f"- 60/40 in RANGE: Sharpe = {p60_range:.3f}")
        lines.append(f"- 70/30 in RANGE: Sharpe = {p70_range:.3f}")
        if any(s > v3_range for s in [p50_range, p60_range, p70_range]):
            lines.append(f"- **YES**: Portfolio improves RANGE regime performance")
        else:
            lines.append(f"- **NO**: Portfolio does not meaningfully improve RANGE regime")
    lines.append("")

    # ---- Section 6: Walk-Forward ----
    lines.append("## Walk-Forward Portfolio Test")
    lines.append("")
    lines.append(f"Configuration: {WF_TRAIN_MONTHS}mo train / {WF_TEST_MONTHS}mo test, {WF_N_WINDOWS} windows")
    lines.append(f"Optimization: Grid search V3 weight [0.10, 0.90] step 0.05")
    lines.append("")

    if wf_results:
        lines.append("| Window | Test Period | Opt V3/Intra | Port Sharpe | V3 Sharpe | dSharpe | 50/50 | 60/40 | 70/30 | Port > V3? |")
        lines.append("|--------|-------------|--------------|-------------|-----------|---------|-------|-------|-------|------------|")
        for r in wf_results:
            better = "YES" if r['port_better'] else "NO"
            lines.append(f"| W{r['window']} | {r['test']} | {r['opt_weights']} | "
                        f"{r['port_sharpe']:.3f} | {r['v3_sharpe']:.3f} | {r['d_sharpe']:+.3f} | "
                        f"{r['m_50_50_sharpe']:.3f} | {r['m_60_40_sharpe']:.3f} | {r['m_70_30_sharpe']:.3f} | {better} |")

        n_better = sum(1 for r in wf_results if r['port_better'])
        n_total = len(wf_results)
        avg_d_sharpe = np.mean([r['d_sharpe'] for r in wf_results])
        avg_opt_v3 = np.mean([r['opt_w_v3'] for r in wf_results])
        lines.append("")
        lines.append(f"**Summary**: Portfolio beats V3 in **{n_better}/{n_total}** windows, "
                    f"avg dSharpe = **{avg_d_sharpe:+.3f}**, avg optimal V3 weight = **{avg_opt_v3:.2f}**")
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

    # ---- Section 8: Comparison to R110 3-Strategy Portfolio ----
    lines.append("## Comparison: 2-Strategy vs R110 3-Strategy Portfolio")
    lines.append("")
    lines.append("Key question: Does removing Macro Regime hurt significantly, or was Intraday the main diversifier?")
    lines.append("")

    lines.append("| Metric | V3 Only | 2-Strat 50/50 | 2-Strat 60/40 | 2-Strat RiskPar | R110 EqWt (33/33/33) | R110 RiskPar | R110 V3Anchor |")
    lines.append("|--------|---------|---------------|---------------|-----------------|----------------------|--------------|---------------|")

    r110_ref = {
        'R110 EqWt (33/33/33)': r110_eq_metrics,
        'R110 RiskPar': r110_rp_metrics,
        'R110 V3Anchor': r110_anchor_metrics,
    }
    r112_ref = {
        'V3 Only': v3_metrics,
        '2-Strat 50/50': port_50_50_metrics,
        '2-Strat 60/40': port_60_40_metrics,
        '2-Strat RiskPar': port_rp_metrics,
    }
    all_compare = {**r112_ref, **r110_ref}

    for metric, label, fmt in [
        ('sharpe', 'Sharpe', '.3f'),
        ('sortino', 'Sortino', '.3f'),
        ('calmar', 'Calmar', '.3f'),
        ('max_dd', 'Max DD', '.2%'),
        ('ann_return', 'Ann. Return', '.2%'),
        ('worst_month', 'Worst Month', '.2%'),
    ]:
        vals = []
        for name in ['V3 Only', '2-Strat 50/50', '2-Strat 60/40', '2-Strat RiskPar',
                      'R110 EqWt (33/33/33)', 'R110 RiskPar', 'R110 V3Anchor']:
            m = all_compare[name]
            vals.append(format(m.get(metric, 0), fmt))
        lines.append(f"| {label} | " + " | ".join(vals) + " |")
    lines.append("")

    # Analysis of macro regime contribution
    lines.append("### Impact of Removing Macro Regime")
    lines.append("")
    d_sharpe_eq = port_50_50_metrics['sharpe'] - r110_eq_metrics['sharpe']
    d_sharpe_rp = port_rp_metrics['sharpe'] - r110_rp_metrics['sharpe']
    d_dd_eq = port_50_50_metrics['max_dd'] - r110_eq_metrics['max_dd']

    lines.append(f"- Sharpe change (50/50 vs R110 EqWt): **{d_sharpe_eq:+.3f}**")
    lines.append(f"- Sharpe change (RiskPar 2s vs R110 RiskPar): **{d_sharpe_rp:+.3f}**")
    lines.append(f"- MaxDD change (50/50 vs R110 EqWt): **{d_dd_eq:+.2%}** (more negative = worse)")
    lines.append("")

    if abs(d_sharpe_eq) < 0.05:
        lines.append("**Verdict**: Macro Regime removal has **NEGLIGIBLE** impact on Sharpe. "
                     "Intraday was the main diversifier.")
    elif d_sharpe_eq < -0.1:
        lines.append("**Verdict**: Macro Regime removal **HURTS** meaningfully. "
                     "The 3-strategy portfolio was genuinely better, but since Macro is KILLED, "
                     "we must accept the 2-strategy version.")
    else:
        lines.append("**Verdict**: Macro Regime removal has **MODERATE** impact. "
                     "Intraday was the primary diversifier, Macro was secondary.")
    lines.append("")

    # ---- Section 9: Kill Criteria ----
    lines.append("## Kill Criteria Evaluation")
    lines.append("")
    lines.append("| Criterion | Threshold | Result |")
    lines.append("|-----------|-----------|--------|")

    # Find best 2-strategy portfolio
    best_port_name = None
    best_port_sharpe = -999
    for name, m in [('50/50', port_50_50_metrics), ('60/40', port_60_40_metrics),
                     ('70/30', port_70_30_metrics), ('Risk Parity', port_rp_metrics)]:
        if m['sharpe'] > best_port_sharpe:
            best_port_sharpe = m['sharpe']
            best_port_name = name
    best_port_m = {'50/50': port_50_50_metrics, '60/40': port_60_40_metrics,
                   '70/30': port_70_30_metrics, 'Risk Parity': port_rp_metrics}[best_port_name]

    # K1: Sharpe
    k1_pass = best_port_m['sharpe'] >= v3_metrics['sharpe']
    k1_str = (f"PASS ({best_port_name} Sharpe {best_port_m['sharpe']:.3f} >= V3 {v3_metrics['sharpe']:.3f})"
              if k1_pass else
              f"**KILL** ({best_port_name} Sharpe {best_port_m['sharpe']:.3f} < V3 {v3_metrics['sharpe']:.3f})")
    lines.append(f"| Portfolio Sharpe >= V3 | {v3_metrics['sharpe']:.3f} | {k1_str} |")

    # K2: MaxDD
    k2_pass = best_port_m['max_dd'] >= v3_metrics['max_dd']
    k2_str = (f"PASS ({best_port_m['max_dd']:.2%} vs V3 {v3_metrics['max_dd']:.2%})"
              if k2_pass else
              f"**KILL** ({best_port_m['max_dd']:.2%} worse than V3 {v3_metrics['max_dd']:.2%})")
    lines.append(f"| Portfolio MaxDD <= V3 | {v3_metrics['max_dd']:.2%} | {k2_str} |")

    # K3: Walk-forward
    if wf_results:
        n_better = sum(1 for r in wf_results if r['port_better'])
        n_total = len(wf_results)
        k3_pass = n_better >= 3
        k3_str = (f"PASS ({n_better}/{n_total} windows)"
                  if k3_pass else
                  f"**KILL** ({n_better}/{n_total} windows < 3/6)")
    else:
        k3_str = "N/A (no walk-forward data)"
        k3_pass = False
    lines.append(f"| Walk-forward >= 3/6 | 3/6 | {k3_str} |")

    # K4: Crash correlation
    if corr_crash is not None:
        max_crash_corr = corr_crash.iloc[0, 1]
        k4_warn = abs(max_crash_corr) > 0.5
        k4_str = (f"**WARNING** (crash corr = {max_crash_corr:.3f} > 0.5)"
                  if k4_warn else
                  f"OK (crash corr = {max_crash_corr:.3f} <= 0.5)")
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

    # Per-allocation verdicts
    lines.append("### Per-Allocation Kill Verdicts")
    lines.append("")
    lines.append("| Allocation | Sharpe | MaxDD | Sharpe >= V3? | MaxDD <= V3? | Verdict |")
    lines.append("|------------|--------|-------|---------------|--------------|---------|")
    for name, m in [('50/50', port_50_50_metrics), ('60/40', port_60_40_metrics),
                     ('70/30', port_70_30_metrics), ('Risk Parity', port_rp_metrics)]:
        sp = m['sharpe'] >= v3_metrics['sharpe']
        dp = m['max_dd'] >= v3_metrics['max_dd']
        verdict = "PASS" if sp and dp else "FAIL"
        lines.append(f"| {name} | {m['sharpe']:.3f} | {m['max_dd']:.2%} | "
                    f"{'YES' if sp else 'NO'} | {'YES' if dp else 'NO'} | **{verdict}** |")
    lines.append("")

    # ---- Section 10: Conclusion ----
    lines.append("## Conclusion")
    lines.append("")

    if all_kill_pass:
        lines.append(f"The 2-strategy portfolio (V3 + Intraday) **PASSES** all kill criteria.")
        lines.append(f"**Best allocation method**: {best_port_name} (Sharpe: {best_port_m['sharpe']:.3f})")
        lines.append("")
        lines.append("### Recommendation")
        lines.append("")
        lines.append(f"- Use **{best_port_name}** allocation for the V3 + Intraday portfolio")
        lines.append(f"- Portfolio Sharpe: {best_port_m['sharpe']:.3f} vs V3-only: {v3_metrics['sharpe']:.3f} "
                    f"({best_port_m['sharpe'] - v3_metrics['sharpe']:+.3f})")
        lines.append(f"- Portfolio MaxDD: {best_port_m['max_dd']:.2%} vs V3-only: {v3_metrics['max_dd']:.2%}")
        d_sharpe_from_macro = port_50_50_metrics['sharpe'] - r110_eq_metrics['sharpe']
        if abs(d_sharpe_from_macro) < 0.05:
            lines.append(f"- Removing Macro Regime: negligible impact on risk-adjusted returns")
        elif d_sharpe_from_macro < -0.1:
            lines.append(f"- Removing Macro Regime: costs {abs(d_sharpe_from_macro):.3f} Sharpe vs 3-strategy, "
                        f"but Macro is KILLED so this is the best available option")
        else:
            lines.append(f"- Removing Macro Regime: moderate impact ({d_sharpe_from_macro:+.3f} Sharpe)")
    else:
        lines.append(f"The 2-strategy portfolio **FAILS** kill criteria.")
        lines.append("")
        lines.append("### Implications")
        lines.append("")
        lines.append("- The 2-strategy combination does not improve on V3-only by enough to justify complexity")
        lines.append("- Since Macro Regime is KILLED, the fallback is V3-only")
        lines.append("- Consider: is the Intraday signal strong enough to justify the added complexity?")
    lines.append("")

    # ---- Section 11: Next Steps ----
    lines.append("## Next Steps")
    lines.append("")
    if all_kill_pass:
        lines.append("1. Implement the 2-strategy portfolio in the live signal pipeline")
        lines.append("2. Monitor V3 vs Intraday rolling correlation -- if it rises above 0.5, revert to V3-only")
        lines.append("3. Periodic rebalance check: re-run walk-forward quarterly")
        lines.append("4. Search for a 3rd uncorrelated signal to replace Macro Regime")
    else:
        lines.append("1. Continue with V3-only as the production strategy")
        lines.append("2. Search for alternative diversifiers with lower correlation to V3")
        lines.append("3. Re-evaluate Intraday signal parameters for higher standalone Sharpe")
    lines.append("")

    return "\n".join(lines)


# ============================================================================
# MAIN
# ============================================================================

def main():
    print("=" * 80)
    print("R112: 2-Strategy Portfolio Test -- V3 Momentum + Intraday Momentum")
    print("=" * 80)

    # --- Load data ---
    btc_1h = load_btc_1h()
    daily = load_btc_daily(btc_1h)

    # --- Compute strategy returns ---
    ret_v3, pos_v3 = compute_v3_momentum(daily)
    ret_intra, pos_intra = compute_intraday_momentum(btc_1h, daily)
    ret_bh = compute_buy_hold(daily)

    # --- Individual metrics ---
    v3_metrics = compute_metrics(ret_v3, "V3 Momentum")
    intraday_metrics = compute_metrics(ret_intra, "Intraday Breakout")
    bh_metrics = compute_metrics(ret_bh, "Buy & Hold")

    print("\n[METRICS] Individual strategy performance:")
    for m in [bh_metrics, v3_metrics, intraday_metrics]:
        print(f"  {m['name']}: Sharpe={m['sharpe']:.3f}, MaxDD={m['max_dd']:.2%}, "
              f"Ann.Ret={m['ann_return']:.2%}")

    # --- Correlation analysis ---
    print("\n[CORRELATION] V3 vs Intraday correlation analysis...")
    corr_matrix = compute_correlation_pair(ret_v3, ret_intra)
    print(f"  Full-period correlation: {corr_matrix.iloc[0,1]:.4f}")

    rolling_corr = compute_rolling_correlation(ret_v3, ret_intra)
    rc_clean = rolling_corr.dropna()
    print(f"  Rolling 90d corr: mean={rc_clean.mean():.4f}, min={rc_clean.min():.4f}, max={rc_clean.max():.4f}")

    corr_normal, corr_crash, n_crash_days = compute_crash_vs_normal_corr(ret_v3, ret_intra, daily)
    if corr_normal is not None:
        print(f"  Normal-day correlation: {corr_normal.iloc[0,1]:.4f}")
    if corr_crash is not None:
        print(f"  Crash-day correlation: {corr_crash.iloc[0,1]:.4f}")
        print(f"  Crash days: {n_crash_days}")

    # --- Build 2-strategy portfolios ---
    print("\n[PORTFOLIO] Building 2-strategy portfolios...")

    # a) 50/50 equal weight
    ret_50_50 = build_2strat_portfolio(ret_v3, ret_intra, 0.50, 0.50)
    port_50_50_metrics = compute_metrics(ret_50_50, "50/50")

    # b) 60/40 (V3 heavy)
    ret_60_40 = build_2strat_portfolio(ret_v3, ret_intra, 0.60, 0.40)
    port_60_40_metrics = compute_metrics(ret_60_40, "60/40")

    # c) 70/30 (V3 anchor)
    ret_70_30 = build_2strat_portfolio(ret_v3, ret_intra, 0.70, 0.30)
    port_70_30_metrics = compute_metrics(ret_70_30, "70/30")

    # d) Risk parity
    ret_rp, rp_w_v3, rp_w_intra = build_risk_parity_2strat(ret_v3, ret_intra)
    port_rp_metrics = compute_metrics(ret_rp, "Risk Parity")

    print(f"\n  Portfolio metrics:")
    for name, m in [("50/50", port_50_50_metrics), ("60/40", port_60_40_metrics),
                     ("70/30", port_70_30_metrics), ("Risk Parity", port_rp_metrics)]:
        print(f"    {name}: Sharpe={m['sharpe']:.3f}, MaxDD={m['max_dd']:.2%}, Ann.Ret={m['ann_return']:.2%}")

    print(f"\n  Risk Parity avg weights: V3={rp_w_v3.mean():.1%}, Intraday={rp_w_intra.mean():.1%}")

    # --- Regime analysis ---
    regimes = classify_regimes(daily)
    regime_ret_dict = {
        'V3 Momentum': ret_v3,
        'Intraday Breakout': ret_intra,
        '50/50': ret_50_50,
        '60/40': ret_60_40,
        '70/30': ret_70_30,
        'Buy & Hold': ret_bh,
    }
    regime_results = regime_analysis(regime_ret_dict, regimes)

    print("\n[REGIME] Per-regime Sharpe:")
    for regime in ['UPTREND', 'DOWNTREND', 'RANGE', 'CRISIS']:
        if regime in regime_results:
            vals = []
            for name in ['V3 Momentum', 'Intraday Breakout', '50/50', '60/40']:
                s = regime_results[regime].get(name, {}).get('sharpe', 0)
                vals.append(f"{name}={s:.3f}")
            print(f"  {regime}: " + ", ".join(vals))

    # --- Walk-forward ---
    wf_results = walk_forward_2strat(daily, ret_v3, ret_intra, ret_v3)

    # --- Drawdown recovery ---
    print("\n[DRAWDOWN] Drawdown recovery analysis...")
    dd_results = []
    for name, rets in [("V3 Only", ret_v3), ("50/50", ret_50_50), ("60/40", ret_60_40),
                        ("70/30", ret_70_30), ("Risk Parity", ret_rp), ("Buy & Hold", ret_bh)]:
        dd = compute_dd_recovery(rets, name)
        dd_results.append(dd)
        print(f"  {name}: MaxDD={dd['max_dd']:.2%}, Recovery={'YES' if dd['recovered'] else 'NO'}, "
              f"Days={dd['recovery_days']}")

    # --- R110 reference metrics (hardcoded from R110 results for comparison) ---
    print("\n[COMPARISON] Loading R110 reference metrics...")
    # These come from the R110 results markdown
    r110_eq_metrics = {
        'sharpe': 0.880, 'sortino': 1.142, 'calmar': 0.609,
        'max_dd': -0.3062, 'ann_return': 0.1863, 'worst_month': -0.1208,
        'ann_vol': 0.0, 'total_return': 0.0
    }
    r110_rp_metrics = {
        'sharpe': 0.820, 'sortino': 1.040, 'calmar': 0.608,
        'max_dd': -0.2025, 'ann_return': 0.1230, 'worst_month': -0.0533,
        'ann_vol': 0.0, 'total_return': 0.0
    }
    r110_anchor_metrics = {
        'sharpe': 0.825, 'sortino': 1.034, 'calmar': 0.550,
        'max_dd': -0.3875, 'ann_return': 0.2133, 'worst_month': -0.1462,
        'ann_vol': 0.0, 'total_return': 0.0
    }

    # --- Generate report ---
    print("\n[REPORT] Generating markdown report...")
    report = generate_report(
        v3_metrics=v3_metrics,
        intraday_metrics=intraday_metrics,
        bh_metrics=bh_metrics,
        port_50_50_metrics=port_50_50_metrics,
        port_60_40_metrics=port_60_40_metrics,
        port_70_30_metrics=port_70_30_metrics,
        port_rp_metrics=port_rp_metrics,
        corr_matrix=corr_matrix,
        corr_normal=corr_normal,
        corr_crash=corr_crash,
        n_crash_days=n_crash_days,
        rolling_corr=rolling_corr,
        regime_results=regime_results,
        wf_results=wf_results,
        dd_results=dd_results,
        rp_weights=(rp_w_v3, rp_w_intra),
        r110_eq_metrics=r110_eq_metrics,
        r110_rp_metrics=r110_rp_metrics,
        r110_anchor_metrics=r110_anchor_metrics,
    )

    # Write report
    OUTPUT_MD.write_text(report)
    print(f"\n[DONE] Report written to {OUTPUT_MD}")

    # Print key results summary
    print("\n" + "=" * 80)
    print("R112 KEY RESULTS SUMMARY")
    print("=" * 80)
    print(f"  V3-only Sharpe:      {v3_metrics['sharpe']:.3f}")
    print(f"  50/50 Sharpe:        {port_50_50_metrics['sharpe']:.3f} ({port_50_50_metrics['sharpe'] - v3_metrics['sharpe']:+.3f})")
    print(f"  60/40 Sharpe:        {port_60_40_metrics['sharpe']:.3f} ({port_60_40_metrics['sharpe'] - v3_metrics['sharpe']:+.3f})")
    print(f"  70/30 Sharpe:        {port_70_30_metrics['sharpe']:.3f} ({port_70_30_metrics['sharpe'] - v3_metrics['sharpe']:+.3f})")
    print(f"  Risk Parity Sharpe:  {port_rp_metrics['sharpe']:.3f} ({port_rp_metrics['sharpe'] - v3_metrics['sharpe']:+.3f})")
    print(f"  R110 EqWt Sharpe:    {r110_eq_metrics['sharpe']:.3f} (3-strategy reference)")
    print(f"  V3-only MaxDD:       {v3_metrics['max_dd']:.2%}")
    print(f"  50/50 MaxDD:         {port_50_50_metrics['max_dd']:.2%}")
    print(f"  Risk Parity MaxDD:   {port_rp_metrics['max_dd']:.2%}")

    if wf_results:
        n_better = sum(1 for r in wf_results if r['port_better'])
        print(f"  Walk-forward:        {n_better}/{len(wf_results)} windows beat V3")

    print("=" * 80)


if __name__ == '__main__':
    main()
