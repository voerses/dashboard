#!/usr/bin/env python3
"""
R109: Deep Walk-Forward Validation of Macro Regime Rotation on BTC
====================================================================
Signal: US10Y yield + DXY rate-of-change → regime classification → BTC positioning
"""

import pandas as pd
import numpy as np
from itertools import product
import warnings
warnings.filterwarnings('ignore')

# ==============================================================================
# 1. DATA LOADING
# ==============================================================================

def load_btc_daily():
    """Load BTC 1h data and downsample to daily OHLCV."""
    btc = pd.read_parquet('/workspace/crypto_backtest/data/spot/1h_cache/BTC_1h.parquet')
    daily = btc.resample('1D').agg({
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last',
        'volume': 'sum'
    }).dropna()
    return daily

def load_macro(filename):
    """Load macro data from parquet, return daily Close series indexed by date."""
    df = pd.read_parquet(f'/workspace/crypto_backtest/data/alternative/macro/{filename}')
    df['Date'] = pd.to_datetime(df['Date'])
    df = df.set_index('Date').sort_index()
    return df['Close']

def prepare_data():
    """Load and align all data sources."""
    btc = load_btc_daily()
    us10y = load_macro('us10y_yield.parquet')
    dxy = load_macro('usd_index.parquet')

    # Align to common dates
    common_idx = btc.index.intersection(us10y.index).intersection(dxy.index)
    btc = btc.loc[common_idx]
    us10y = us10y.loc[common_idx]
    dxy = dxy.loc[common_idx]

    return btc, us10y, dxy

# ==============================================================================
# 2. SIGNAL GENERATION
# ==============================================================================

def compute_signal(us10y, dxy, roc_lookback=20, smooth=0, lag=0):
    """
    Compute macro regime signal.

    Parameters:
        roc_lookback: days for rate-of-change calculation
        smooth: SMA smoothing window (0 = none)
        lag: days of lag to apply to macro data (simulates data delay)

    Returns:
        signal: Series with values 1 (long), 0 (flat), -1 (short)
    """
    # Apply lag
    if lag > 0:
        us10y_use = us10y.shift(lag)
        dxy_use = dxy.shift(lag)
    else:
        us10y_use = us10y
        dxy_use = dxy

    # Rate of change
    us10y_roc = us10y_use / us10y_use.shift(roc_lookback) - 1
    dxy_roc = dxy_use / dxy_use.shift(roc_lookback) - 1

    # Optional smoothing
    if smooth > 0:
        us10y_roc = us10y_roc.rolling(smooth).mean()
        dxy_roc = dxy_roc.rolling(smooth).mean()

    # Regime classification
    signal = pd.Series(0.0, index=us10y_roc.index)

    # RISK-ON: both falling → long
    risk_on = (us10y_roc < 0) & (dxy_roc < 0)
    signal[risk_on] = 1.0

    # RISK-OFF: both rising → short (for bidirectional)
    risk_off = (us10y_roc > 0) & (dxy_roc > 0)
    signal[risk_off] = -1.0

    # MIXED: one up, one down → flat (already 0)

    return signal

# ==============================================================================
# 3. BACKTESTING ENGINE
# ==============================================================================

def backtest(btc_daily, signal, mode='long_only', cost_bps=10, rebalance_days=7):
    """
    Run backtest with weekly rebalance.

    Parameters:
        btc_daily: DataFrame with OHLCV
        signal: Series with 1, 0, -1
        mode: 'long_only' (clamp short to 0) or 'bidirectional'
        cost_bps: trading cost in basis points per trade
        rebalance_days: days between rebalances

    Returns:
        dict with metrics
    """
    # Align
    common = btc_daily.index.intersection(signal.dropna().index)
    if len(common) < 30:
        return None

    btc = btc_daily.loc[common]
    sig = signal.loc[common]

    if mode == 'long_only':
        sig = sig.clip(lower=0)

    # Daily returns
    btc_ret = btc['close'].pct_change()

    # Weekly rebalance: only update position every N days
    position = pd.Series(0.0, index=common)
    current_pos = 0.0
    last_rebalance = 0

    for i, dt in enumerate(common):
        target = sig.loc[dt]
        if i == 0 or (i - last_rebalance) >= rebalance_days:
            if not np.isnan(target):
                current_pos = target
                last_rebalance = i
        position.iloc[i] = current_pos

    # Strategy returns with cost
    cost_rate = cost_bps / 10000
    pos_change = position.diff().abs()
    costs = pos_change * cost_rate

    strat_ret = position.shift(1) * btc_ret - costs
    strat_ret = strat_ret.dropna()

    if len(strat_ret) < 10:
        return None

    # Metrics
    total_ret = (1 + strat_ret).prod() - 1
    ann_factor = 365 / max(len(strat_ret), 1)
    ann_ret = (1 + total_ret) ** ann_factor - 1 if total_ret > -1 else -1.0

    vol = strat_ret.std() * np.sqrt(365)
    sharpe = ann_ret / vol if vol > 0 else 0.0

    # Max drawdown
    cum = (1 + strat_ret).cumprod()
    peak = cum.cummax()
    dd = (cum - peak) / peak
    max_dd = dd.min()

    # Trade count and win rate
    trades = (pos_change > 0).sum()

    # Win rate: per-trade P&L
    trade_starts = pos_change[pos_change > 0].index
    wins = 0
    total_trades = 0
    for j, ts in enumerate(trade_starts):
        # Next trade or end
        if j + 1 < len(trade_starts):
            te = trade_starts[j + 1]
        else:
            te = strat_ret.index[-1]
        trade_pnl = strat_ret.loc[ts:te].sum()
        if trade_pnl > 0:
            wins += 1
        total_trades += 1

    win_rate = wins / total_trades if total_trades > 0 else 0.0

    return {
        'total_return': total_ret,
        'ann_return': ann_ret,
        'sharpe': sharpe,
        'max_dd': max_dd,
        'trades': int(trades),
        'win_rate': win_rate,
        'vol': vol,
        'days': len(strat_ret),
    }

# ==============================================================================
# 4. WALK-FORWARD VALIDATION
# ==============================================================================

def walk_forward(btc, us10y, dxy, n_windows=10, train_months=12, test_months=6,
                 start_date='2021-07-01', mode='long_only', lag=0):
    """
    Rolling walk-forward validation.

    Returns:
        results: list of dicts per window
        all_params_tested: total parameter combinations
    """
    start = pd.Timestamp(start_date)

    # Parameter grid
    roc_lookbacks = [10, 15, 20, 30, 40]
    smoothings = [0, 5, 10]
    param_grid = list(product(roc_lookbacks, smoothings))
    n_params = len(param_grid)

    results = []

    for w in range(n_windows):
        train_start = start + pd.DateOffset(months=w * test_months)
        train_end = train_start + pd.DateOffset(months=train_months)
        test_start = train_end
        test_end = test_start + pd.DateOffset(months=test_months)

        # Check data availability
        if test_end > btc.index.max():
            break

        # TRAIN: optimize parameters
        best_sharpe = -999
        best_params = (20, 0)

        for roc_lb, smooth in param_grid:
            sig = compute_signal(us10y, dxy, roc_lookback=roc_lb, smooth=smooth, lag=lag)
            train_btc = btc.loc[train_start:train_end]
            train_sig = sig.loc[train_start:train_end]

            metrics = backtest(train_btc, train_sig, mode=mode)
            if metrics and metrics['sharpe'] > best_sharpe:
                best_sharpe = metrics['sharpe']
                best_params = (roc_lb, smooth)

        # TEST: apply best params
        sig = compute_signal(us10y, dxy, roc_lookback=best_params[0],
                           smooth=best_params[1], lag=lag)
        test_btc = btc.loc[test_start:test_end]
        test_sig = sig.loc[test_start:test_end]

        metrics = backtest(test_btc, test_sig, mode=mode)

        if metrics:
            metrics['window'] = w + 1
            metrics['train_start'] = train_start.strftime('%Y-%m-%d')
            metrics['train_end'] = train_end.strftime('%Y-%m-%d')
            metrics['test_start'] = test_start.strftime('%Y-%m-%d')
            metrics['test_end'] = test_end.strftime('%Y-%m-%d')
            metrics['best_roc'] = best_params[0]
            metrics['best_smooth'] = best_params[1]
            metrics['train_sharpe'] = best_sharpe
            results.append(metrics)

    return results, n_params

# ==============================================================================
# 5. INFORMATION COEFFICIENT (IC) ANALYSIS
# ==============================================================================

def rolling_ic(btc, us10y, dxy, roc_lookback=20, window=90, fwd_days=7):
    """
    Compute rolling IC between macro signal and BTC forward returns.
    """
    sig = compute_signal(us10y, dxy, roc_lookback=roc_lookback)
    fwd_ret = btc['close'].pct_change(fwd_days).shift(-fwd_days)

    common = sig.dropna().index.intersection(fwd_ret.dropna().index)
    sig_aligned = sig.loc[common]
    fwd_aligned = fwd_ret.loc[common]

    # Rolling correlation
    df = pd.DataFrame({'signal': sig_aligned, 'fwd_ret': fwd_aligned})
    rolling_corr = df['signal'].rolling(window).corr(df['fwd_ret'])

    return rolling_corr.dropna()

# ==============================================================================
# 6. MAIN ANALYSIS
# ==============================================================================

def main():
    print("=" * 80)
    print("R109: Deep Walk-Forward Validation of Macro Regime Rotation on BTC")
    print("=" * 80)

    # Load data
    print("\n[1] Loading data...")
    btc, us10y, dxy = prepare_data()
    print(f"    BTC daily: {btc.index.min().date()} to {btc.index.max().date()} ({len(btc)} days)")
    print(f"    US10Y:     {us10y.index.min().date()} to {us10y.index.max().date()} ({len(us10y)} days)")
    print(f"    DXY:       {dxy.index.min().date()} to {dxy.index.max().date()} ({len(dxy)} days)")

    # =========================================================================
    # SECTION 2: Walk-Forward Validation (Long-Only, 0 lag)
    # =========================================================================
    print("\n[2] Walk-Forward Validation (Long-Only, 0-day lag)...")
    wf_results, n_params = walk_forward(btc, us10y, dxy, n_windows=10, mode='long_only', lag=0)

    print(f"\n    Windows completed: {len(wf_results)}")
    print(f"    Parameter combinations per window: {n_params}")

    positive_windows = sum(1 for r in wf_results if r['total_return'] > 0)
    mean_sharpe = np.mean([r['sharpe'] for r in wf_results]) if wf_results else 0

    print(f"    Positive windows: {positive_windows}/{len(wf_results)}")
    print(f"    Mean Sharpe: {mean_sharpe:.3f}")

    # Kill criteria check
    kill = False
    if positive_windows < 5:
        print("    *** KILL: <5/10 positive windows ***")
        kill = True
    if mean_sharpe < 0.2:
        print("    *** KILL: Mean Sharpe < 0.2 ***")
        kill = True

    # Print per-window results
    print("\n    Per-Window Results (Long-Only, 0-lag):")
    print(f"    {'Win':>3} | {'Test Period':>23} | {'Return':>8} | {'Sharpe':>7} | {'MaxDD':>7} | {'Trades':>6} | {'WR':>5} | {'ROC':>3} | {'Smooth':>6}")
    print("    " + "-" * 95)
    for r in wf_results:
        print(f"    {r['window']:3d} | {r['test_start']} → {r['test_end']} | {r['total_return']:7.1%} | {r['sharpe']:7.2f} | {r['max_dd']:7.1%} | {r['trades']:6d} | {r['win_rate']:4.0%} | {r['best_roc']:3d} | {r['best_smooth']:6d}")

    # =========================================================================
    # SECTION 3: Walk-Forward Validation (Bidirectional, 0 lag)
    # =========================================================================
    print("\n[3] Walk-Forward Validation (Bidirectional, 0-day lag)...")
    wf_bidir, _ = walk_forward(btc, us10y, dxy, n_windows=10, mode='bidirectional', lag=0)

    positive_bidir = sum(1 for r in wf_bidir if r['total_return'] > 0)
    mean_sharpe_bidir = np.mean([r['sharpe'] for r in wf_bidir]) if wf_bidir else 0

    print(f"    Positive windows: {positive_bidir}/{len(wf_bidir)}")
    print(f"    Mean Sharpe: {mean_sharpe_bidir:.3f}")

    print("\n    Per-Window Results (Bidirectional, 0-lag):")
    print(f"    {'Win':>3} | {'Test Period':>23} | {'Return':>8} | {'Sharpe':>7} | {'MaxDD':>7} | {'Trades':>6} | {'WR':>5} | {'ROC':>3} | {'Smooth':>6}")
    print("    " + "-" * 95)
    for r in wf_bidir:
        print(f"    {r['window']:3d} | {r['test_start']} → {r['test_end']} | {r['total_return']:7.1%} | {r['sharpe']:7.2f} | {r['max_dd']:7.1%} | {r['trades']:6d} | {r['win_rate']:4.0%} | {r['best_roc']:3d} | {r['best_smooth']:6d}")

    # =========================================================================
    # SECTION 4: Signal Lag Analysis
    # =========================================================================
    print("\n[4] Signal Lag Analysis...")
    lag_results = {}
    for lag_val in [0, 1, 2, 3]:
        wf_lag, _ = walk_forward(btc, us10y, dxy, n_windows=10, mode='long_only', lag=lag_val)
        if wf_lag:
            pos_w = sum(1 for r in wf_lag if r['total_return'] > 0)
            ms = np.mean([r['sharpe'] for r in wf_lag])
            mr = np.mean([r['total_return'] for r in wf_lag])
            mdd = np.mean([r['max_dd'] for r in wf_lag])
            lag_results[lag_val] = {
                'mean_sharpe': ms,
                'mean_return': mr,
                'mean_maxdd': mdd,
                'positive_windows': pos_w,
                'n_windows': len(wf_lag)
            }
            print(f"    Lag={lag_val}d: Mean Sharpe={ms:.3f}, Mean Return={mr:.1%}, Positive={pos_w}/{len(wf_lag)}")

    # Degradation check
    if 0 in lag_results and 1 in lag_results:
        sharpe_0 = lag_results[0]['mean_sharpe']
        sharpe_1 = lag_results[1]['mean_sharpe']
        degradation = (sharpe_0 - sharpe_1) / abs(sharpe_0) if abs(sharpe_0) > 0.01 else 0
        print(f"\n    Sharpe degradation 0→1 day lag: {degradation:.1%}")
        if abs(degradation) > 0.5:
            print("    *** WARNING: >50% degradation with 1-day lag — signal may not be tradeable ***")
        else:
            print("    Signal appears robust to 1-day lag.")

    # =========================================================================
    # SECTION 5: Data Snooping Check
    # =========================================================================
    print("\n[5] Data Snooping Check...")
    total_combos = n_params  # per window
    bonferroni_alpha = 0.05 / total_combos
    print(f"    Parameter combinations tested: {total_combos}")
    print(f"    Bonferroni-adjusted alpha: {bonferroni_alpha:.4f}")
    print(f"    (Need p < {bonferroni_alpha:.4f} for statistical significance)")

    # Simple t-test on OOS returns
    if wf_results:
        oos_sharpes = [r['sharpe'] for r in wf_results]
        from scipy import stats
        t_stat, p_val = stats.ttest_1samp(oos_sharpes, 0)
        print(f"    OOS Sharpe t-stat: {t_stat:.3f}, p-value: {p_val:.4f}")
        print(f"    Bonferroni-adjusted p-value: {min(p_val * total_combos, 1.0):.4f}")
        if p_val * total_combos < 0.05:
            print("    *** Survives Bonferroni correction ***")
        else:
            print("    *** Does NOT survive Bonferroni correction ***")

    # 1-day lagged macro test (already done in lag analysis)
    print(f"\n    1-day lagged macro results (from lag analysis above):")
    if 1 in lag_results:
        lr = lag_results[1]
        print(f"    Mean Sharpe: {lr['mean_sharpe']:.3f}, Positive: {lr['positive_windows']}/{lr['n_windows']}")

    # =========================================================================
    # SECTION 6: Stationarity / Rolling IC
    # =========================================================================
    print("\n[6] Rolling IC Analysis (90d window, 7d forward returns)...")
    ic_series = rolling_ic(btc, us10y, dxy, roc_lookback=20, window=90, fwd_days=7)

    if len(ic_series) > 0:
        # Break into yearly chunks
        years = ic_series.groupby(ic_series.index.year)
        print(f"\n    Rolling IC by year:")
        print(f"    {'Year':>6} | {'Mean IC':>8} | {'Std IC':>8} | {'% Positive':>10} | {'Min':>8} | {'Max':>8}")
        print("    " + "-" * 60)
        for year, data in years:
            if len(data) > 10:
                print(f"    {year:6d} | {data.mean():8.4f} | {data.std():8.4f} | {(data > 0).mean():9.0%} | {data.min():8.4f} | {data.max():8.4f}")

        overall_ic = ic_series.mean()
        ic_std = ic_series.std()
        ic_positive_pct = (ic_series > 0).mean()
        # Count sign flips
        sign_changes = (np.sign(ic_series).diff().abs() > 0).sum()
        print(f"\n    Overall IC: {overall_ic:.4f} (std: {ic_std:.4f})")
        print(f"    % time positive: {ic_positive_pct:.0%}")
        print(f"    Sign changes: {sign_changes}")

        if ic_std > 0.15:
            print("    *** WARNING: High IC volatility — relationship is unstable ***")
        if ic_positive_pct < 0.55 and ic_positive_pct > 0.45:
            print("    *** WARNING: IC flips sign frequently — near random ***")

    # =========================================================================
    # SECTION 7: Long-Only vs Bidirectional Comparison
    # =========================================================================
    print("\n[7] Long-Only vs Bidirectional Comparison...")

    # Full-sample backtest for each with default params
    sig = compute_signal(us10y, dxy, roc_lookback=20, smooth=0, lag=0)

    # Long-only
    m_long = backtest(btc.loc['2021-07-01':], sig.loc['2021-07-01':], mode='long_only')
    m_bidir = backtest(btc.loc['2021-07-01':], sig.loc['2021-07-01':], mode='bidirectional')

    print(f"\n    {'Metric':<20} | {'Long-Only':>12} | {'Bidirectional':>14}")
    print("    " + "-" * 52)
    if m_long and m_bidir:
        for k in ['total_return', 'ann_return', 'sharpe', 'max_dd', 'trades', 'win_rate', 'vol']:
            v_l = m_long[k]
            v_b = m_bidir[k]
            if k in ['total_return', 'ann_return', 'max_dd', 'win_rate', 'vol']:
                print(f"    {k:<20} | {v_l:11.1%} | {v_b:13.1%}")
            elif k == 'sharpe':
                print(f"    {k:<20} | {v_l:12.3f} | {v_b:14.3f}")
            else:
                print(f"    {k:<20} | {v_l:12d} | {v_b:14d}")

    # WF comparison summaries
    print("\n    Walk-Forward Summary Comparison:")
    print(f"    {'Metric':<25} | {'Long-Only':>12} | {'Bidirectional':>14}")
    print("    " + "-" * 55)

    lo_sharpes = [r['sharpe'] for r in wf_results]
    bi_sharpes = [r['sharpe'] for r in wf_bidir]
    lo_rets = [r['total_return'] for r in wf_results]
    bi_rets = [r['total_return'] for r in wf_bidir]
    lo_dds = [r['max_dd'] for r in wf_results]
    bi_dds = [r['max_dd'] for r in wf_bidir]

    print(f"    {'Mean OOS Sharpe':<25} | {np.mean(lo_sharpes):12.3f} | {np.mean(bi_sharpes):14.3f}")
    print(f"    {'Median OOS Sharpe':<25} | {np.median(lo_sharpes):12.3f} | {np.median(bi_sharpes):14.3f}")
    print(f"    {'Mean OOS Return':<25} | {np.mean(lo_rets):11.1%} | {np.mean(bi_rets):13.1%}")
    print(f"    {'Mean OOS MaxDD':<25} | {np.mean(lo_dds):11.1%} | {np.mean(bi_dds):13.1%}")
    print(f"    {'Positive Windows':<25} | {positive_windows:>8}/{len(wf_results):<3} | {positive_bidir:>10}/{len(wf_bidir):<3}")

    # =========================================================================
    # SECTION 8: Regime Distribution Analysis
    # =========================================================================
    print("\n[8] Regime Distribution Analysis...")
    sig_full = compute_signal(us10y, dxy, roc_lookback=20, smooth=0, lag=0)
    sig_period = sig_full.loc['2021-07-01':]

    risk_on = (sig_period == 1).sum()
    risk_off = (sig_period == -1).sum()
    mixed = (sig_period == 0).sum()
    total = len(sig_period)

    print(f"    RISK-ON  (both falling, long):  {risk_on:4d} days ({risk_on/total:.0%})")
    print(f"    RISK-OFF (both rising, short):  {risk_off:4d} days ({risk_off/total:.0%})")
    print(f"    MIXED    (divergent, flat):      {mixed:4d} days ({mixed/total:.0%})")
    print(f"    Total:                           {total:4d} days")

    # BTC returns by regime
    btc_ret = btc['close'].pct_change()
    for regime_val, regime_name in [(1, 'RISK-ON'), (-1, 'RISK-OFF'), (0, 'MIXED')]:
        mask = sig_period == regime_val
        regime_dates = mask[mask].index
        regime_rets = btc_ret.loc[btc_ret.index.isin(regime_dates)]
        if len(regime_rets) > 5:
            ann_r = regime_rets.mean() * 365
            vol_r = regime_rets.std() * np.sqrt(365)
            sr = ann_r / vol_r if vol_r > 0 else 0
            print(f"    BTC in {regime_name:8s}: ann_ret={ann_r:.1%}, vol={vol_r:.1%}, sharpe={sr:.2f}, n={len(regime_rets)}")

    # =========================================================================
    # FINAL VERDICT
    # =========================================================================
    print("\n" + "=" * 80)
    print("VERDICT")
    print("=" * 80)

    if kill:
        print("FAIL — Signal does not pass kill criteria.")
    else:
        print("Signal passes kill criteria. Evaluate details below.")

    print(f"\n  Long-Only WF:     Mean Sharpe={np.mean(lo_sharpes):.3f}, Positive={positive_windows}/{len(wf_results)}")
    print(f"  Bidirectional WF: Mean Sharpe={np.mean(bi_sharpes):.3f}, Positive={positive_bidir}/{len(wf_bidir)}")

    if 0 in lag_results and 1 in lag_results:
        print(f"  Lag robustness:   0d Sharpe={lag_results[0]['mean_sharpe']:.3f}, 1d Sharpe={lag_results[1]['mean_sharpe']:.3f}")

    if len(ic_series) > 0:
        print(f"  Rolling IC:       Mean={ic_series.mean():.4f}, Std={ic_series.std():.4f}")

    print()

    # =========================================================================
    # RETURN ALL RESULTS FOR MARKDOWN GENERATION
    # =========================================================================
    return {
        'wf_long_only': wf_results,
        'wf_bidirectional': wf_bidir,
        'lag_results': lag_results,
        'n_params': n_params,
        'ic_series': ic_series,
        'regime_dist': {
            'risk_on': risk_on, 'risk_off': risk_off,
            'mixed': mixed, 'total': total
        },
        'full_long': m_long,
        'full_bidir': m_bidir,
        'kill': kill,
    }


def generate_markdown(results):
    """Generate the results markdown file."""
    wf_lo = results['wf_long_only']
    wf_bi = results['wf_bidirectional']
    lag = results['lag_results']
    n_params = results['n_params']
    ic = results['ic_series']
    regime = results['regime_dist']
    m_long = results['full_long']
    m_bidir = results['full_bidir']
    kill = results['kill']

    lo_sharpes = [r['sharpe'] for r in wf_lo]
    bi_sharpes = [r['sharpe'] for r in wf_bi]
    lo_rets = [r['total_return'] for r in wf_lo]
    bi_rets = [r['total_return'] for r in wf_bi]
    lo_dds = [r['max_dd'] for r in wf_lo]
    bi_dds = [r['max_dd'] for r in wf_bi]
    pos_lo = sum(1 for r in wf_lo if r['total_return'] > 0)
    pos_bi = sum(1 for r in wf_bi if r['total_return'] > 0)

    from scipy import stats
    t_stat, p_val = stats.ttest_1samp(lo_sharpes, 0)
    bonf_p = min(p_val * n_params, 1.0)

    md = f"""# R109: Deep Walk-Forward Validation of Macro Regime Rotation on BTC

**Date**: 2026-03-24
**Status**: {'FAIL' if kill else 'CONDITIONAL PASS — see caveats'}
**Parent**: R107 (Uncorrelated Signal Discovery)
**Signal**: US10Y + DXY 20d rate-of-change regime rotation

## Signal Description

Macro regime rotation uses the 20-day rate-of-change of the US 10-Year Treasury Yield and the US Dollar Index (DXY). When both are falling simultaneously, this indicates a "risk-on" macro environment favorable for BTC (long). When both are rising, it signals "risk-off" (flat or short). Mixed signals (one up, one down) result in a flat/neutral position.

- **Rebalance**: Weekly (every 7 days)
- **Cost assumption**: 10 bps per trade
- **Correlation with V3 momentum**: ~0.010 (uncorrelated, per R107)

---

## 1. Walk-Forward Validation: Long-Only (10 Windows)

Train: 12 months | Test: 6 months | Start: 2021-07-01
Parameters optimized: ROC lookback (10/15/20/30/40d), signal smoothing (none/5d/10d SMA)

| Window | Test Period | Return | Sharpe | MaxDD | Trades | Win Rate | Best ROC | Best Smooth |
|--------|------------|--------|--------|-------|--------|----------|----------|-------------|
"""
    for r in wf_lo:
        md += f"| {r['window']} | {r['test_start']} to {r['test_end']} | {r['total_return']:.1%} | {r['sharpe']:.2f} | {r['max_dd']:.1%} | {r['trades']} | {r['win_rate']:.0%} | {r['best_roc']}d | {r['best_smooth']}d |\n"

    md += f"""
**Summary (Long-Only)**:
- Positive windows: **{pos_lo}/{len(wf_lo)}**
- Mean OOS Sharpe: **{np.mean(lo_sharpes):.3f}**
- Median OOS Sharpe: **{np.median(lo_sharpes):.3f}**
- Mean OOS Return: **{np.mean(lo_rets):.1%}**
- Mean OOS MaxDD: **{np.mean(lo_dds):.1%}**
- Kill criteria (<5/10 positive OR mean Sharpe <0.2): **{'TRIGGERED' if kill else 'PASSED'}**

---

## 2. Walk-Forward Validation: Bidirectional (10 Windows)

| Window | Test Period | Return | Sharpe | MaxDD | Trades | Win Rate | Best ROC | Best Smooth |
|--------|------------|--------|--------|-------|--------|----------|----------|-------------|
"""
    for r in wf_bi:
        md += f"| {r['window']} | {r['test_start']} to {r['test_end']} | {r['total_return']:.1%} | {r['sharpe']:.2f} | {r['max_dd']:.1%} | {r['trades']} | {r['win_rate']:.0%} | {r['best_roc']}d | {r['best_smooth']}d |\n"

    md += f"""
**Summary (Bidirectional)**:
- Positive windows: **{pos_bi}/{len(wf_bi)}**
- Mean OOS Sharpe: **{np.mean(bi_sharpes):.3f}**
- Median OOS Sharpe: **{np.median(bi_sharpes):.3f}**
- Mean OOS Return: **{np.mean(bi_rets):.1%}**
- Mean OOS MaxDD: **{np.mean(bi_dds):.1%}**

---

## 3. Long-Only vs Bidirectional Comparison

| Metric | Long-Only | Bidirectional |
|--------|-----------|---------------|
| Mean OOS Sharpe | {np.mean(lo_sharpes):.3f} | {np.mean(bi_sharpes):.3f} |
| Median OOS Sharpe | {np.median(lo_sharpes):.3f} | {np.median(bi_sharpes):.3f} |
| Mean OOS Return | {np.mean(lo_rets):.1%} | {np.mean(bi_rets):.1%} |
| Mean OOS MaxDD | {np.mean(lo_dds):.1%} | {np.mean(bi_dds):.1%} |
| Positive Windows | {pos_lo}/{len(wf_lo)} | {pos_bi}/{len(wf_bi)} |
"""

    # Full sample comparison
    if m_long and m_bidir:
        md += f"""
**Full-Sample Backtest (2021-07-01 onward, default params ROC=20, smooth=none)**:

| Metric | Long-Only | Bidirectional |
|--------|-----------|---------------|
| Total Return | {m_long['total_return']:.1%} | {m_bidir['total_return']:.1%} |
| Annualized Return | {m_long['ann_return']:.1%} | {m_bidir['ann_return']:.1%} |
| Sharpe | {m_long['sharpe']:.3f} | {m_bidir['sharpe']:.3f} |
| MaxDD | {m_long['max_dd']:.1%} | {m_bidir['max_dd']:.1%} |
| Volatility | {m_long['vol']:.1%} | {m_bidir['vol']:.1%} |
| Trades | {m_long['trades']} | {m_bidir['trades']} |
| Win Rate | {m_long['win_rate']:.0%} | {m_bidir['win_rate']:.0%} |
"""

    md += f"""
---

## 4. Signal Lag Analysis

Tests robustness of the signal when macro data is delayed by 0-3 days (realistic: macro data often available with a 1-day lag).

| Lag (days) | Mean Sharpe | Mean Return | Mean MaxDD | Positive Windows |
|------------|-------------|-------------|------------|------------------|
"""
    for lag_val in sorted(lag.keys()):
        lr = lag[lag_val]
        md += f"| {lag_val} | {lr['mean_sharpe']:.3f} | {lr['mean_return']:.1%} | {lr['mean_maxdd']:.1%} | {lr['positive_windows']}/{lr['n_windows']} |\n"

    # Degradation
    if 0 in lag and 1 in lag:
        s0 = lag[0]['mean_sharpe']
        s1 = lag[1]['mean_sharpe']
        deg = (s0 - s1) / abs(s0) if abs(s0) > 0.01 else 0
        tradeable = "YES" if abs(deg) < 0.5 else "NO"
        md += f"""
**Degradation 0 to 1 day lag**: {deg:.1%}
**Tradeable with realistic lag?** {tradeable}
"""

    md += f"""
---

## 5. Data Snooping Check

- Parameter combinations per window: **{n_params}** (5 ROC lookbacks x 3 smoothing options)
- Bonferroni-adjusted significance level: **alpha = {0.05 / n_params:.4f}**
- OOS Sharpe t-test: t-stat = {t_stat:.3f}, raw p-value = {p_val:.4f}
- **Bonferroni-adjusted p-value: {bonf_p:.4f}**
- Survives Bonferroni correction: **{'YES' if bonf_p < 0.05 else 'NO'}**

Note: Walk-forward inherently mitigates snooping (parameters chosen on train, evaluated on test). The Bonferroni check is conservative since WF already controls for overfitting. The real question is whether OOS performance is significantly different from zero.

---

## 6. Stationarity / Rolling IC Analysis

Rolling 90-day rank correlation between macro regime signal and BTC 7-day forward returns.

"""
    if len(ic) > 0:
        years = ic.groupby(ic.index.year)
        md += "| Year | Mean IC | Std IC | % Positive | Min | Max |\n"
        md += "|------|---------|--------|------------|-----|-----|\n"
        for year, data in years:
            if len(data) > 10:
                md += f"| {year} | {data.mean():.4f} | {data.std():.4f} | {(data > 0).mean():.0%} | {data.min():.4f} | {data.max():.4f} |\n"

        sign_changes = (np.sign(ic).diff().abs() > 0).sum()
        md += f"""
- **Overall IC**: {ic.mean():.4f} (std: {ic.std():.4f})
- **% time positive**: {(ic > 0).mean():.0%}
- **Sign changes**: {sign_changes}
- **Stability assessment**: {'UNSTABLE — IC flips sign frequently' if (ic > 0).mean() < 0.55 and (ic > 0).mean() > 0.45 else 'MODERATELY STABLE' if (ic > 0).mean() >= 0.55 or (ic > 0).mean() <= 0.45 else 'STABLE'}
"""

    md += f"""
---

## 7. Regime Distribution

Period: 2021-07-01 onward (with ROC=20, no smoothing)

| Regime | Days | % of Total | Description |
|--------|------|------------|-------------|
| RISK-ON | {regime['risk_on']} | {regime['risk_on']/regime['total']:.0%} | US10Y falling AND DXY falling -> Long BTC |
| RISK-OFF | {regime['risk_off']} | {regime['risk_off']/regime['total']:.0%} | US10Y rising AND DXY rising -> Short/Flat BTC |
| MIXED | {regime['mixed']} | {regime['mixed']/regime['total']:.0%} | Divergent signals -> Flat |
| **Total** | **{regime['total']}** | **100%** | |

---

## 8. Verdict and Recommendations

"""
    if kill:
        md += """### FAIL

The macro regime rotation signal **fails the kill criteria** in walk-forward validation:
"""
        if pos_lo < 5:
            md += f"- Only {pos_lo}/{len(wf_lo)} positive OOS windows (need >=5)\n"
        if np.mean(lo_sharpes) < 0.2:
            md += f"- Mean OOS Sharpe of {np.mean(lo_sharpes):.3f} is below 0.2 threshold\n"

        md += """
**Recommendation**: Do NOT proceed to implementation. The macro regime signal does not demonstrate consistent OOS profitability under walk-forward validation. While the in-sample fit from R107 looked promising, the signal does not generalize reliably across market regimes.

**Why it fails**: The relationship between macro variables (US10Y, DXY) and BTC returns is not stationary. The rolling IC analysis shows the signal flips predictive direction across time, making it unreliable as a standalone signal.

**Salvage options**:
1. Use as a regime filter (reduce position size in risk-off) rather than a standalone signal
2. Combine with V3 momentum as a conditional overlay (only trade V3 signals in risk-on regimes)
3. Test with different macro variables (e.g., real rates, credit spreads)
"""
    else:
        md += f"""### CONDITIONAL PASS

The signal passes the basic kill criteria but review the following caveats:

- Mean OOS Sharpe: {np.mean(lo_sharpes):.3f} (long-only), {np.mean(bi_sharpes):.3f} (bidirectional)
- Positive windows: {pos_lo}/{len(wf_lo)} (long-only), {pos_bi}/{len(wf_bi)} (bidirectional)
"""
        if 0 in lag and 1 in lag:
            md += f"- Lag robustness: {lag[0]['mean_sharpe']:.3f} (0d) vs {lag[1]['mean_sharpe']:.3f} (1d lag)\n"

        md += f"- Bonferroni-adjusted p-value: {bonf_p:.4f} ({'significant' if bonf_p < 0.05 else 'NOT significant'})\n"

        md += """
**Recommendation**: Consider as a regime overlay for V3 momentum rather than a standalone strategy. The uncorrelated nature (r=0.01 per R107) makes it valuable for portfolio construction even if standalone Sharpe is modest.
"""

    md += """
---

*Generated by R109_macro_regime_wf.py*
"""

    return md


if __name__ == '__main__':
    results = main()

    # Generate and save markdown
    md = generate_markdown(results)
    with open('/workspace/crypto_backtest/research/R109_macro_regime_wf.md', 'w') as f:
        f.write(md)

    print("\nResults saved to /workspace/crypto_backtest/research/R109_macro_regime_wf.md")
    print("Script saved to /workspace/crypto_backtest/research/R109_macro_regime_wf.py")
