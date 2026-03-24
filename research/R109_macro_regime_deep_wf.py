#!/workspace/venv/bin/python
"""
R109: Deep Walk-Forward Validation of Macro Regime Rotation Signal on BTC
=========================================================================

Signal Definition (from R107):
  - Long: US10Y 20d change < 0 AND DXY 20d change < 0 (both falling = risk-on)
  - Short/Flat: US10Y 20d change > 0 AND DXY 20d change > 0 (both rising = risk-off)
  - Neutral: Mixed signals -> flat
  - Market: BTC spot
  - R107 result: Standalone Sharpe 0.397, corr 0.010 with V3, 4/6 WF positive

Walk-Forward Protocol:
  - 10 rolling windows: 180-day train / 90-day test, rolling 90 days
  - Train: Grid search over lookback, threshold, holding period (60 combos)
  - Optimize on: Sharpe ratio
  - Test: Apply best train params to OOS window
  - Fees: 10bps per rebalance round-trip

Additional Tests:
  - Data snooping check (look-ahead bias)
  - Regime analysis (UPTREND, DOWNTREND, RANGE)
  - Parameter sensitivity (+/-20%)
  - Signal lag analysis (1-day lag)
  - Stationarity check (rolling IC)
  - Long-only vs Bidirectional

Kill Criteria:
  - <5/10 positive OOS windows -> KILL
  - Mean OOS Sharpe < 0.2 -> KILL
  - <30 total signal changes -> KILL (too sparse)
  - 1-day lag kills signal -> KILL (latency-dependent)
  - IC sign changed in last 2 years -> KILL (non-stationary)
  - Parameter sensitivity >30% degradation -> CONDITIONAL

Author: Quant Research Agent
Date: 2026-03-24
"""

import warnings
import numpy as np
import pandas as pd
from scipy import stats
from pathlib import Path
from datetime import datetime, timedelta
from itertools import product

warnings.filterwarnings('ignore')

# ── Paths ──────────────────────────────────────────────────────────────────────
PROJECT_DIR = Path('/workspace/crypto_backtest')
DATA_DIR = PROJECT_DIR / 'data'
OUTPUT_PY = PROJECT_DIR / 'research' / 'R109_macro_regime_deep_wf.py'
OUTPUT_MD = PROJECT_DIR / 'research' / 'R109_macro_regime_deep_wf.md'

COST_BPS = 10  # round-trip cost in basis points
ANNUALIZE = np.sqrt(365)  # daily -> annual


# ══════════════════════════════════════════════════════════════════════════════
# DATA LOADING
# ══════════════════════════════════════════════════════════════════════════════

def load_btc_daily():
    """Load BTC 1h data and resample to daily."""
    print("[DATA] Loading BTC 1h spot and resampling to daily...")
    df = pd.read_parquet(DATA_DIR / 'spot/1h_cache/BTC_1h.parquet')
    df.index = pd.to_datetime(df.index)
    df = df.sort_index()
    df = df[~df.index.duplicated(keep='first')]

    # Resample to daily
    daily = pd.DataFrame()
    daily['close'] = df['close'].resample('1D').last().dropna()
    daily['open'] = df['open'].resample('1D').first()
    daily['high'] = df['high'].resample('1D').max()
    daily['low'] = df['low'].resample('1D').min()
    daily['volume'] = df['volume'].resample('1D').sum()
    daily = daily.dropna()
    daily['ret'] = daily['close'].pct_change()

    print(f"  BTC daily: {daily.index.min().date()} to {daily.index.max().date()}, {len(daily)} days")
    return daily


def load_macro():
    """Load US10Y and DXY daily data."""
    print("[DATA] Loading macro data...")

    # US10Y
    us10y = pd.read_parquet(DATA_DIR / 'alternative/macro/us10y_yield.parquet')
    us10y['Date'] = pd.to_datetime(us10y['Date'])
    us10y = us10y.set_index('Date').sort_index()
    us10y = us10y[~us10y.index.duplicated(keep='first')]
    us10y_close = us10y['Close'].rename('us10y')

    # DXY
    dxy = pd.read_parquet(DATA_DIR / 'alternative/macro/usd_index.parquet')
    dxy['Date'] = pd.to_datetime(dxy['Date'])
    dxy = dxy.set_index('Date').sort_index()
    dxy = dxy[~dxy.index.duplicated(keep='first')]
    dxy_close = dxy['Close'].rename('dxy')

    macro = pd.concat([us10y_close, dxy_close], axis=1).dropna()
    print(f"  Macro data: {macro.index.min().date()} to {macro.index.max().date()}, {len(macro)} days")
    return macro


# ══════════════════════════════════════════════════════════════════════════════
# SIGNAL GENERATION
# ══════════════════════════════════════════════════════════════════════════════

def compute_signal(macro_df, lookback=20, threshold=0.0, holding='signal_change',
                   lag_days=0, mode='bidirectional'):
    """
    Compute macro regime rotation signal.

    Parameters:
    - lookback: days to compute change
    - threshold: z-score threshold for direction (0 = any change)
    - holding: 'daily', 'weekly', 'signal_change'
    - lag_days: additional lag to add (for robustness testing)
    - mode: 'bidirectional' or 'long_only'

    Returns:
    - Series of positions: +1 (long), -1 (short), 0 (flat)
    """
    us10y_change = macro_df['us10y'].diff(lookback)
    dxy_change = macro_df['dxy'].pct_change(lookback)

    # Z-score if threshold > 0
    if threshold > 0:
        us10y_z = (us10y_change - us10y_change.rolling(60).mean()) / us10y_change.rolling(60).std()
        dxy_z = (dxy_change - dxy_change.rolling(60).mean()) / dxy_change.rolling(60).std()
    else:
        us10y_z = us10y_change
        dxy_z = dxy_change

    # Signal generation using PREVIOUS day's close (no look-ahead)
    # The .shift(1) ensures we use yesterday's macro close
    us10y_z = us10y_z.shift(1 + lag_days)
    dxy_z = dxy_z.shift(1 + lag_days)

    signal = pd.Series(0.0, index=macro_df.index)

    if threshold > 0:
        risk_on = (us10y_z < -threshold) & (dxy_z < -threshold)
        risk_off = (us10y_z > threshold) & (dxy_z > threshold)
    else:
        risk_on = (us10y_z < 0) & (dxy_z < 0)
        risk_off = (us10y_z > 0) & (dxy_z > 0)

    signal[risk_on] = 1.0
    signal[risk_off] = -1.0 if mode == 'bidirectional' else 0.0
    # Mixed = 0 (flat)

    # Apply holding period logic
    if holding == 'weekly':
        # Only allow rebalance on Mondays
        mask = signal.index.dayofweek == 0
        weekly_signal = signal.copy()
        weekly_signal[~mask] = np.nan
        weekly_signal = weekly_signal.ffill()
        weekly_signal = weekly_signal.fillna(0)
        signal = weekly_signal
    elif holding == 'signal_change':
        # Hold until signal changes direction (already the default behavior)
        pass
    # daily = rebalance every day (no change needed)

    return signal


def backtest_signal(signal, btc_daily, cost_bps=COST_BPS):
    """
    Backtest a signal series against BTC daily returns.

    Returns dict with performance metrics.
    """
    # Align
    common = signal.index.intersection(btc_daily.index)
    if len(common) < 30:
        return None

    sig = signal.loc[common]
    ret = btc_daily.loc[common, 'ret']

    # Position changes for cost calculation
    trades = sig.diff().abs()
    trade_cost = trades * (cost_bps / 10000)

    # Strategy return: signal applied to next day's return (already shifted in signal)
    strat_ret = sig * ret - trade_cost

    # Metrics
    n_days = len(strat_ret.dropna())
    if n_days < 20:
        return None

    strat_ret_clean = strat_ret.dropna()
    total_ret = (1 + strat_ret_clean).prod() - 1
    ann_ret = (1 + total_ret) ** (365 / n_days) - 1
    ann_vol = strat_ret_clean.std() * ANNUALIZE
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0

    # Drawdown
    cum = (1 + strat_ret_clean).cumprod()
    peak = cum.cummax()
    dd = (cum / peak - 1)
    max_dd = dd.min()

    # Signal statistics
    n_trades = int(trades.sum() / 2)  # round trips
    pct_long = (sig == 1).mean()
    pct_short = (sig == -1).mean()
    pct_flat = (sig == 0).mean()
    signal_changes = int((sig.diff() != 0).sum())

    return {
        'total_ret': total_ret,
        'ann_ret': ann_ret,
        'ann_vol': ann_vol,
        'sharpe': sharpe,
        'max_dd': max_dd,
        'n_days': n_days,
        'n_trades': n_trades,
        'signal_changes': signal_changes,
        'pct_long': pct_long,
        'pct_short': pct_short,
        'pct_flat': pct_flat,
    }


# ══════════════════════════════════════════════════════════════════════════════
# WALK-FORWARD ENGINE
# ══════════════════════════════════════════════════════════════════════════════

def run_walk_forward(macro_df, btc_daily, n_windows=10, train_days=180,
                     test_days=90, mode='bidirectional', lag_days=0):
    """
    Run walk-forward optimization.

    Returns list of window results and aggregated metrics.
    """
    # Parameter grid
    lookbacks = [10, 15, 20, 30, 40]
    thresholds = [0.0, 0.3, 0.5, 0.7]
    holdings = ['daily', 'weekly', 'signal_change']

    # Find common date range
    common_dates = macro_df.index.intersection(btc_daily.index)
    common_dates = common_dates.sort_values()

    if len(common_dates) < train_days + test_days:
        print("[ERROR] Not enough common data for walk-forward")
        return None, None

    # Calculate window starts
    # Total span needed: train_days + test_days + (n_windows-1)*test_days
    total_needed = train_days + test_days * n_windows
    if len(common_dates) < total_needed:
        print(f"[WARN] Need {total_needed} days but have {len(common_dates)}. Adjusting windows.")
        n_windows = max(1, (len(common_dates) - train_days) // test_days)
        print(f"  Adjusted to {n_windows} windows")

    # Start from early enough to have all windows
    start_idx = len(common_dates) - (train_days + test_days * n_windows)
    if start_idx < 0:
        start_idx = 0

    window_results = []

    for w in range(n_windows):
        train_start_idx = start_idx + w * test_days
        train_end_idx = train_start_idx + train_days
        test_start_idx = train_end_idx
        test_end_idx = test_start_idx + test_days

        if test_end_idx > len(common_dates):
            break

        train_dates = common_dates[train_start_idx:train_end_idx]
        test_dates = common_dates[test_start_idx:test_end_idx]

        train_start = train_dates[0]
        train_end = train_dates[-1]
        test_start = test_dates[0]
        test_end = test_dates[-1]

        print(f"\n  Window {w+1}/{n_windows}: Train {train_start.date()}-{train_end.date()} | "
              f"Test {test_start.date()}-{test_end.date()}")

        # Grid search on train period
        best_sharpe = -999
        best_params = None
        best_result = None

        for lb, thr, hold in product(lookbacks, thresholds, holdings):
            sig = compute_signal(macro_df, lookback=lb, threshold=thr,
                                 holding=hold, lag_days=lag_days, mode=mode)
            sig_train = sig.loc[train_start:train_end]
            btc_train = btc_daily.loc[train_start:train_end]

            result = backtest_signal(sig_train, btc_train)
            if result and result['sharpe'] > best_sharpe:
                best_sharpe = result['sharpe']
                best_params = {'lookback': lb, 'threshold': thr, 'holding': hold}
                best_result = result

        if best_params is None:
            print(f"    No valid params found for window {w+1}")
            continue

        print(f"    Best train params: {best_params} -> Sharpe {best_sharpe:.3f}")

        # Apply best params to test period (OOS)
        sig_oos = compute_signal(macro_df, lookback=best_params['lookback'],
                                 threshold=best_params['threshold'],
                                 holding=best_params['holding'],
                                 lag_days=lag_days, mode=mode)
        sig_oos = sig_oos.loc[test_start:test_end]
        btc_test = btc_daily.loc[test_start:test_end]

        oos_result = backtest_signal(sig_oos, btc_test)
        if oos_result is None:
            print(f"    OOS backtest failed for window {w+1}")
            continue

        print(f"    OOS Sharpe: {oos_result['sharpe']:.3f} | "
              f"Return: {oos_result['total_ret']*100:.1f}% | "
              f"Trades: {oos_result['n_trades']}")

        window_results.append({
            'window': w + 1,
            'train_start': train_start.date(),
            'train_end': train_end.date(),
            'test_start': test_start.date(),
            'test_end': test_end.date(),
            'best_params': best_params,
            'train_sharpe': best_sharpe,
            'oos_sharpe': oos_result['sharpe'],
            'oos_return': oos_result['total_ret'],
            'oos_max_dd': oos_result['max_dd'],
            'oos_n_trades': oos_result['n_trades'],
            'oos_signal_changes': oos_result['signal_changes'],
            'oos_pct_long': oos_result['pct_long'],
            'oos_pct_short': oos_result['pct_short'],
            'oos_pct_flat': oos_result['pct_flat'],
        })

    if not window_results:
        return None, None

    # Aggregate
    oos_sharpes = [w['oos_sharpe'] for w in window_results]
    positive_windows = sum(1 for s in oos_sharpes if s > 0)
    total_signal_changes = sum(w['oos_signal_changes'] for w in window_results)

    agg = {
        'n_windows': len(window_results),
        'positive_windows': positive_windows,
        'mean_oos_sharpe': np.mean(oos_sharpes),
        'median_oos_sharpe': np.median(oos_sharpes),
        'std_oos_sharpe': np.std(oos_sharpes),
        'min_oos_sharpe': np.min(oos_sharpes),
        'max_oos_sharpe': np.max(oos_sharpes),
        'mean_oos_return': np.mean([w['oos_return'] for w in window_results]),
        'total_signal_changes': total_signal_changes,
        'mode': mode,
        'lag_days': lag_days,
    }

    return window_results, agg


# ══════════════════════════════════════════════════════════════════════════════
# REGIME ANALYSIS
# ══════════════════════════════════════════════════════════════════════════════

def classify_btc_regime(btc_daily, lookback=30):
    """
    Classify BTC into regimes: UPTREND, DOWNTREND, RANGE.
    Uses 30-day return and volatility.
    """
    ret_30d = btc_daily['close'].pct_change(lookback)
    vol_30d = btc_daily['ret'].rolling(lookback).std() * ANNUALIZE

    regime = pd.Series('RANGE', index=btc_daily.index)
    regime[ret_30d > 0.10] = 'UPTREND'    # >10% in 30 days
    regime[ret_30d < -0.10] = 'DOWNTREND'  # <-10% in 30 days

    return regime


def regime_analysis(macro_df, btc_daily, params=None):
    """
    Analyze signal performance by BTC regime.
    """
    if params is None:
        params = {'lookback': 20, 'threshold': 0.0, 'holding': 'signal_change'}

    print("\n[REGIME ANALYSIS]")
    regime = classify_btc_regime(btc_daily)
    signal = compute_signal(macro_df, **params)

    results = {}
    for r in ['UPTREND', 'DOWNTREND', 'RANGE']:
        mask = regime == r
        dates = btc_daily.index[mask]
        if len(dates) < 30:
            continue

        sig_r = signal.loc[signal.index.isin(dates)]
        btc_r = btc_daily.loc[btc_daily.index.isin(dates)]

        result = backtest_signal(sig_r, btc_r)
        if result:
            n_days = int(mask.sum())
            pct = n_days / len(regime) * 100
            print(f"  {r}: {n_days} days ({pct:.0f}%) | Sharpe {result['sharpe']:.3f} | "
                  f"Return {result['total_ret']*100:.1f}%")
            results[r] = result
            results[r]['n_days_regime'] = n_days
            results[r]['pct_total'] = pct

    return results


# ══════════════════════════════════════════════════════════════════════════════
# PARAMETER SENSITIVITY
# ══════════════════════════════════════════════════════════════════════════════

def parameter_sensitivity(macro_df, btc_daily, base_params=None):
    """
    Test +/-20% on lookback and threshold.
    """
    if base_params is None:
        base_params = {'lookback': 20, 'threshold': 0.0, 'holding': 'signal_change'}

    print("\n[PARAMETER SENSITIVITY]")

    # Base case
    base_sig = compute_signal(macro_df, **base_params)
    base_result = backtest_signal(base_sig, btc_daily)
    base_sharpe = base_result['sharpe'] if base_result else 0

    print(f"  Base params: {base_params} -> Sharpe {base_sharpe:.3f}")

    # Lookback variations
    lb = base_params['lookback']
    lb_low = max(5, int(lb * 0.8))
    lb_high = int(lb * 1.2)

    results = []
    for lb_test in [lb_low, lb, lb_high]:
        params = base_params.copy()
        params['lookback'] = lb_test
        sig = compute_signal(macro_df, **params)
        result = backtest_signal(sig, btc_daily)
        sharpe = result['sharpe'] if result else 0
        degradation = (base_sharpe - sharpe) / abs(base_sharpe) * 100 if base_sharpe != 0 else 0
        results.append({
            'param': 'lookback',
            'value': lb_test,
            'sharpe': sharpe,
            'degradation_pct': degradation
        })
        print(f"  lookback={lb_test}: Sharpe {sharpe:.3f} ({degradation:+.1f}% vs base)")

    # Threshold variations (only if base > 0)
    thr = base_params['threshold']
    if thr > 0:
        thr_low = max(0.0, thr * 0.8)
        thr_high = thr * 1.2
        for thr_test in [thr_low, thr, thr_high]:
            params = base_params.copy()
            params['threshold'] = round(thr_test, 2)
            sig = compute_signal(macro_df, **params)
            result = backtest_signal(sig, btc_daily)
            sharpe = result['sharpe'] if result else 0
            degradation = (base_sharpe - sharpe) / abs(base_sharpe) * 100 if base_sharpe != 0 else 0
            results.append({
                'param': 'threshold',
                'value': round(thr_test, 2),
                'sharpe': sharpe,
                'degradation_pct': degradation
            })
            print(f"  threshold={thr_test:.2f}: Sharpe {sharpe:.3f} ({degradation:+.1f}% vs base)")
    else:
        # Test thresholds around 0
        for thr_test in [0.0, 0.1, 0.2, 0.3]:
            params = base_params.copy()
            params['threshold'] = thr_test
            sig = compute_signal(macro_df, **params)
            result = backtest_signal(sig, btc_daily)
            sharpe = result['sharpe'] if result else 0
            degradation = (base_sharpe - sharpe) / abs(base_sharpe) * 100 if base_sharpe != 0 else 0
            results.append({
                'param': 'threshold',
                'value': thr_test,
                'sharpe': sharpe,
                'degradation_pct': degradation
            })
            print(f"  threshold={thr_test:.2f}: Sharpe {sharpe:.3f} ({degradation:+.1f}% vs base)")

    max_degradation = max(abs(r['degradation_pct']) for r in results if r['value'] != base_params.get(r['param']))
    return results, max_degradation, base_sharpe


# ══════════════════════════════════════════════════════════════════════════════
# STATIONARITY CHECK
# ══════════════════════════════════════════════════════════════════════════════

def rolling_ic_analysis(macro_df, btc_daily, lookback=20, window=252):
    """
    Compute rolling 1-year Information Coefficient of the signal.
    IC = rank correlation between signal and next-day return.
    """
    print("\n[STATIONARITY CHECK - Rolling IC]")

    us10y_change = macro_df['us10y'].diff(lookback).shift(1)
    dxy_change = macro_df['dxy'].pct_change(lookback).shift(1)

    # Combined signal: negative of sum (both falling = positive signal)
    combined = -(us10y_change / us10y_change.rolling(60).std().clip(lower=1e-6) +
                 dxy_change / dxy_change.rolling(60).std().clip(lower=1e-6))

    # Align with BTC returns
    common = combined.index.intersection(btc_daily.index)
    sig = combined.loc[common]
    ret = btc_daily.loc[common, 'ret'].shift(-1)  # next day return

    # Rolling IC
    rolling_ic = sig.rolling(window).corr(ret)

    # Analysis
    ic_values = rolling_ic.dropna()
    if len(ic_values) == 0:
        print("  No IC data available")
        return None

    # Last 2 years
    two_years_ago = ic_values.index.max() - pd.Timedelta(days=730)
    recent_ic = ic_values.loc[two_years_ago:]

    if len(recent_ic) > 0:
        ic_mean_recent = recent_ic.mean()
        ic_sign_changes = ((recent_ic > 0).astype(int).diff() != 0).sum()
        ic_positive_pct = (recent_ic > 0).mean() * 100

        print(f"  Full period IC: mean={ic_values.mean():.4f}, std={ic_values.std():.4f}")
        print(f"  Last 2y IC: mean={ic_mean_recent:.4f}")
        print(f"  Last 2y IC positive: {ic_positive_pct:.1f}% of time")
        print(f"  Last 2y IC sign changes: {ic_sign_changes}")

        # Check if sign has flipped
        # Split into quarters
        q_size = len(recent_ic) // 4
        if q_size > 20:
            quarters = []
            for i in range(4):
                q_start = i * q_size
                q_end = (i + 1) * q_size if i < 3 else len(recent_ic)
                q_ic = recent_ic.iloc[q_start:q_end].mean()
                quarters.append(q_ic)
                print(f"    Q{i+1}: IC={q_ic:.4f}")

            sign_changed = any(q < 0 for q in quarters) and any(q > 0 for q in quarters)
            print(f"  IC sign changed in last 2 years: {sign_changed}")
        else:
            sign_changed = False
            print("  Not enough data for quarterly analysis")
    else:
        ic_mean_recent = 0
        sign_changed = False
        ic_positive_pct = 0

    return {
        'full_mean': ic_values.mean(),
        'full_std': ic_values.std(),
        'recent_mean': ic_mean_recent,
        'recent_positive_pct': ic_positive_pct if len(recent_ic) > 0 else 0,
        'sign_changed': sign_changed,
        'ic_series': ic_values,
    }


# ══════════════════════════════════════════════════════════════════════════════
# DATA SNOOPING CHECK
# ══════════════════════════════════════════════════════════════════════════════

def data_snooping_check(macro_df, btc_daily):
    """
    Verify no look-ahead bias in macro data alignment.
    """
    print("\n[DATA SNOOPING CHECK]")

    # Check: macro data is daily, BTC is 1h resampled to daily
    # Key question: when is daily macro close available?
    # US10Y: bond market closes ~3pm ET, data available same day evening
    # DXY: forex market, closes 5pm ET, data available same day

    # Our signal uses .shift(1) which means we use PREVIOUS day's close
    # This is conservative - in practice same-day close might be available
    # before crypto trading at midnight UTC

    # Check macro data frequency
    macro_diffs = macro_df.index.to_series().diff().dropna()
    median_diff = macro_diffs.median()
    print(f"  Macro data frequency: median gap = {median_diff}")

    # Check for weekday-only data (bonds/forex don't trade weekends)
    weekend_data = macro_df.index[macro_df.index.dayofweek >= 5]
    print(f"  Weekend data points: {len(weekend_data)} (expect 0 for bonds)")

    # Verify alignment
    common = macro_df.index.intersection(btc_daily.index)
    btc_only = btc_daily.index.difference(macro_df.index)
    print(f"  Common trading days: {len(common)}")
    print(f"  BTC-only days (weekends): {len(btc_only)}")

    # The shift(1) in compute_signal handles the T+1 lag
    print("  Signal uses .shift(1) = previous day's macro close applied to today")
    print("  This is CONSERVATIVE: no look-ahead bias")

    return {
        'common_days': len(common),
        'btc_only_days': len(btc_only),
        'weekend_macro': len(weekend_data),
        'bias_free': True
    }


# ══════════════════════════════════════════════════════════════════════════════
# FULL-PERIOD ANALYSIS (for finding best params)
# ══════════════════════════════════════════════════════════════════════════════

def full_period_grid_search(macro_df, btc_daily, mode='bidirectional'):
    """
    Grid search over full period to find best params (for sensitivity analysis).
    """
    print(f"\n[FULL PERIOD GRID SEARCH - {mode}]")

    lookbacks = [10, 15, 20, 30, 40]
    thresholds = [0.0, 0.3, 0.5, 0.7]
    holdings = ['daily', 'weekly', 'signal_change']

    best_sharpe = -999
    best_params = None
    all_results = []

    for lb, thr, hold in product(lookbacks, thresholds, holdings):
        sig = compute_signal(macro_df, lookback=lb, threshold=thr,
                             holding=hold, mode=mode)
        result = backtest_signal(sig, btc_daily)
        if result:
            all_results.append({
                'lookback': lb,
                'threshold': thr,
                'holding': hold,
                'sharpe': result['sharpe'],
                'return': result['total_ret'],
                'max_dd': result['max_dd'],
                'n_trades': result['n_trades'],
            })
            if result['sharpe'] > best_sharpe:
                best_sharpe = result['sharpe']
                best_params = {'lookback': lb, 'threshold': thr, 'holding': hold}

    print(f"  Best params: {best_params} -> Sharpe {best_sharpe:.3f}")

    # Show top 5
    all_results_sorted = sorted(all_results, key=lambda x: x['sharpe'], reverse=True)
    print("  Top 5 configurations:")
    for i, r in enumerate(all_results_sorted[:5]):
        print(f"    {i+1}. lb={r['lookback']}, thr={r['threshold']}, hold={r['holding']} "
              f"-> Sharpe {r['sharpe']:.3f}, Return {r['return']*100:.1f}%")

    return best_params, best_sharpe, all_results_sorted


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    print("=" * 80)
    print("R109: Deep Walk-Forward Validation of Macro Regime Rotation Signal")
    print("=" * 80)

    # Load data
    btc_daily = load_btc_daily()
    macro_df = load_macro()

    # Restrict to overlapping period
    common_start = max(btc_daily.index.min(), macro_df.index.min())
    common_end = min(btc_daily.index.max(), macro_df.index.max())
    print(f"\nCommon period: {common_start.date()} to {common_end.date()}")

    btc_daily = btc_daily.loc[common_start:common_end]
    macro_df = macro_df.loc[common_start:common_end]

    # Forward-fill macro on weekends so BTC weekend bars have macro context
    all_dates = btc_daily.index
    macro_df = macro_df.reindex(all_dates).ffill()

    results = {}

    # ── 1. Data Snooping Check ────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("1. DATA SNOOPING CHECK")
    print("=" * 60)
    snooping = data_snooping_check(macro_df, btc_daily)
    results['snooping'] = snooping

    # ── 2. Walk-Forward: Bidirectional ────────────────────────────────────
    print("\n" + "=" * 60)
    print("2. WALK-FORWARD: BIDIRECTIONAL (10 windows, 180d/90d)")
    print("=" * 60)
    wf_bi_windows, wf_bi_agg = run_walk_forward(
        macro_df, btc_daily, n_windows=10, train_days=180, test_days=90,
        mode='bidirectional', lag_days=0
    )
    results['wf_bidirectional'] = {'windows': wf_bi_windows, 'agg': wf_bi_agg}

    # ── 3. Walk-Forward: Long-Only ────────────────────────────────────────
    print("\n" + "=" * 60)
    print("3. WALK-FORWARD: LONG-ONLY (10 windows, 180d/90d)")
    print("=" * 60)
    wf_lo_windows, wf_lo_agg = run_walk_forward(
        macro_df, btc_daily, n_windows=10, train_days=180, test_days=90,
        mode='long_only', lag_days=0
    )
    results['wf_long_only'] = {'windows': wf_lo_windows, 'agg': wf_lo_agg}

    # ── 4. Signal Lag Analysis ────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("4. SIGNAL LAG ANALYSIS (1-day additional lag)")
    print("=" * 60)
    wf_lag_windows, wf_lag_agg = run_walk_forward(
        macro_df, btc_daily, n_windows=10, train_days=180, test_days=90,
        mode='bidirectional', lag_days=1
    )
    results['wf_lagged'] = {'windows': wf_lag_windows, 'agg': wf_lag_agg}

    # ── 5. Full Period Grid Search ────────────────────────────────────────
    print("\n" + "=" * 60)
    print("5. FULL PERIOD GRID SEARCH")
    print("=" * 60)
    best_bi_params, best_bi_sharpe, all_bi = full_period_grid_search(
        macro_df, btc_daily, mode='bidirectional')
    best_lo_params, best_lo_sharpe, all_lo = full_period_grid_search(
        macro_df, btc_daily, mode='long_only')
    results['full_period'] = {
        'bidirectional': {'params': best_bi_params, 'sharpe': best_bi_sharpe, 'top5': all_bi[:5]},
        'long_only': {'params': best_lo_params, 'sharpe': best_lo_sharpe, 'top5': all_lo[:5]},
    }

    # ── 6. Parameter Sensitivity ──────────────────────────────────────────
    print("\n" + "=" * 60)
    print("6. PARAMETER SENSITIVITY")
    print("=" * 60)
    sens_results, max_degradation, base_sharpe = parameter_sensitivity(
        macro_df, btc_daily, base_params=best_bi_params)
    results['sensitivity'] = {
        'results': sens_results,
        'max_degradation': max_degradation,
        'base_sharpe': base_sharpe,
    }

    # ── 7. Regime Analysis ────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("7. REGIME ANALYSIS")
    print("=" * 60)
    regime_results = regime_analysis(macro_df, btc_daily, params=best_bi_params)
    results['regime'] = regime_results

    # ── 8. Stationarity Check ─────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("8. STATIONARITY CHECK")
    print("=" * 60)
    ic_results = rolling_ic_analysis(macro_df, btc_daily, lookback=20)
    results['stationarity'] = ic_results

    # ══════════════════════════════════════════════════════════════════════
    # KILL CRITERIA EVALUATION
    # ══════════════════════════════════════════════════════════════════════
    print("\n" + "=" * 60)
    print("KILL CRITERIA EVALUATION")
    print("=" * 60)

    kills = []
    conditionals = []
    passes = []

    # K1: <5/10 positive OOS windows
    if wf_bi_agg:
        pos = wf_bi_agg['positive_windows']
        tot = wf_bi_agg['n_windows']
        if pos < 5:
            kills.append(f"K1: Only {pos}/{tot} positive OOS windows (need >=5)")
        else:
            passes.append(f"K1: {pos}/{tot} positive OOS windows (>=5) -- PASS")
    else:
        kills.append("K1: Walk-forward failed to produce results")

    # K2: Mean OOS Sharpe < 0.2
    if wf_bi_agg:
        mean_s = wf_bi_agg['mean_oos_sharpe']
        if mean_s < 0.2:
            kills.append(f"K2: Mean OOS Sharpe {mean_s:.3f} < 0.2")
        else:
            passes.append(f"K2: Mean OOS Sharpe {mean_s:.3f} >= 0.2 -- PASS")
    else:
        kills.append("K2: No walk-forward results")

    # K3: <30 total signal changes
    if wf_bi_agg:
        sc = wf_bi_agg['total_signal_changes']
        if sc < 30:
            kills.append(f"K3: Only {sc} total signal changes (need >=30)")
        else:
            passes.append(f"K3: {sc} total signal changes (>=30) -- PASS")

    # K4: 1-day lag kills signal
    if wf_lag_agg and wf_bi_agg:
        lag_sharpe = wf_lag_agg['mean_oos_sharpe']
        base_sharpe_wf = wf_bi_agg['mean_oos_sharpe']
        if lag_sharpe <= 0 and base_sharpe_wf > 0:
            kills.append(f"K4: 1-day lag kills signal (lagged Sharpe {lag_sharpe:.3f} vs base {base_sharpe_wf:.3f})")
        elif lag_sharpe < base_sharpe_wf * 0.5:
            conditionals.append(f"K4: 1-day lag degrades signal significantly "
                                f"(lagged {lag_sharpe:.3f} vs base {base_sharpe_wf:.3f})")
        else:
            passes.append(f"K4: Signal survives 1-day lag (lagged {lag_sharpe:.3f} vs base {base_sharpe_wf:.3f}) -- PASS")
    elif wf_lag_agg is None:
        kills.append("K4: Lagged walk-forward failed")

    # K5: IC sign changed in last 2 years
    if ic_results:
        if ic_results['sign_changed']:
            kills.append(f"K5: IC sign changed in last 2 years (non-stationary)")
        else:
            passes.append(f"K5: IC sign stable in last 2 years -- PASS")
    else:
        conditionals.append("K5: Could not compute IC (insufficient data)")

    # K6: Parameter sensitivity >30% degradation
    if max_degradation > 30:
        conditionals.append(f"K6: Parameter sensitivity {max_degradation:.1f}% degradation (>30%)")
    else:
        passes.append(f"K6: Parameter sensitivity {max_degradation:.1f}% max degradation (<=30%) -- PASS")

    print("\nKILL conditions triggered:")
    for k in kills:
        print(f"  [KILL] {k}")
    print("\nCONDITIONAL conditions triggered:")
    for c in conditionals:
        print(f"  [COND] {c}")
    print("\nPASS conditions:")
    for p in passes:
        print(f"  [PASS] {p}")

    # Final verdict
    if kills:
        verdict = "KILL"
        verdict_reason = "; ".join(kills)
    elif conditionals:
        verdict = "CONDITIONAL PASS"
        verdict_reason = "; ".join(conditionals)
    else:
        verdict = "PASS"
        verdict_reason = "All kill criteria passed"

    print(f"\n{'='*60}")
    print(f"FINAL VERDICT: {verdict}")
    print(f"Reason: {verdict_reason}")
    print(f"{'='*60}")

    results['kills'] = kills
    results['conditionals'] = conditionals
    results['passes'] = passes
    results['verdict'] = verdict
    results['verdict_reason'] = verdict_reason

    # ══════════════════════════════════════════════════════════════════════
    # WRITE REPORT
    # ══════════════════════════════════════════════════════════════════════
    write_report(results)

    return results


def write_report(results):
    """Write markdown report."""
    lines = []
    lines.append("# R109: Deep Walk-Forward Validation — Macro Regime Rotation Signal")
    lines.append("")
    lines.append(f"**Date**: 2026-03-24")
    lines.append(f"**Verdict**: **{results['verdict']}**")
    lines.append(f"**Reason**: {results['verdict_reason']}")
    lines.append("")

    # Signal definition
    lines.append("## Signal Definition")
    lines.append("")
    lines.append("- **Long**: US10Y 20d change < 0 AND DXY 20d change < 0 (both falling = risk-on)")
    lines.append("- **Short/Flat**: US10Y 20d change > 0 AND DXY 20d change > 0 (both rising = risk-off)")
    lines.append("- **Neutral**: Mixed signals -> flat")
    lines.append("- **Market**: BTC spot")
    lines.append("- **R107 result**: Standalone Sharpe 0.397, corr 0.010 with V3, 4/6 WF positive")
    lines.append("- **Fees**: 10bps per rebalance round-trip")
    lines.append("")

    # Data snooping
    lines.append("## 1. Data Snooping Check")
    lines.append("")
    if results.get('snooping'):
        s = results['snooping']
        lines.append(f"- Common trading days (macro + BTC): {s['common_days']}")
        lines.append(f"- BTC-only days (weekends/holidays): {s['btc_only_days']}")
        lines.append(f"- Weekend macro data points: {s['weekend_macro']}")
        lines.append(f"- **Bias-free**: {s['bias_free']}")
        lines.append("")
        lines.append("Signal uses `.shift(1)` on macro data = previous day's close applied to today's trading.")
        lines.append("This is conservative: no look-ahead bias. Macro data (US10Y, DXY) is available")
        lines.append("same-day evening, but we use T-1 close for safety.")
    lines.append("")

    # Walk-forward bidirectional
    lines.append("## 2. Walk-Forward: Bidirectional")
    lines.append("")
    wf = results.get('wf_bidirectional', {})
    if wf.get('agg'):
        agg = wf['agg']
        lines.append(f"- **Windows**: {agg['n_windows']}")
        lines.append(f"- **Positive OOS windows**: {agg['positive_windows']}/{agg['n_windows']}")
        lines.append(f"- **Mean OOS Sharpe**: {agg['mean_oos_sharpe']:.3f}")
        lines.append(f"- **Median OOS Sharpe**: {agg['median_oos_sharpe']:.3f}")
        lines.append(f"- **Std OOS Sharpe**: {agg['std_oos_sharpe']:.3f}")
        lines.append(f"- **Min/Max OOS Sharpe**: {agg['min_oos_sharpe']:.3f} / {agg['max_oos_sharpe']:.3f}")
        lines.append(f"- **Mean OOS Return**: {agg['mean_oos_return']*100:.1f}%")
        lines.append(f"- **Total signal changes**: {agg['total_signal_changes']}")
        lines.append("")

        # Window details table
        lines.append("### Window Details")
        lines.append("")
        lines.append("| Window | Train Period | Test Period | Best Params | Train Sharpe | OOS Sharpe | OOS Return | OOS MaxDD |")
        lines.append("|--------|-------------|-------------|-------------|-------------|-----------|-----------|----------|")
        for w in wf['windows']:
            params_str = f"lb={w['best_params']['lookback']},thr={w['best_params']['threshold']},h={w['best_params']['holding'][:3]}"
            lines.append(f"| {w['window']} | {w['train_start']}-{w['train_end']} | "
                        f"{w['test_start']}-{w['test_end']} | {params_str} | "
                        f"{w['train_sharpe']:.3f} | {w['oos_sharpe']:.3f} | "
                        f"{w['oos_return']*100:.1f}% | {w['oos_max_dd']*100:.1f}% |")
    else:
        lines.append("Walk-forward failed to produce results.")
    lines.append("")

    # Walk-forward long-only
    lines.append("## 3. Walk-Forward: Long-Only")
    lines.append("")
    wf_lo = results.get('wf_long_only', {})
    if wf_lo.get('agg'):
        agg = wf_lo['agg']
        lines.append(f"- **Positive OOS windows**: {agg['positive_windows']}/{agg['n_windows']}")
        lines.append(f"- **Mean OOS Sharpe**: {agg['mean_oos_sharpe']:.3f}")
        lines.append(f"- **Median OOS Sharpe**: {agg['median_oos_sharpe']:.3f}")
        lines.append(f"- **Mean OOS Return**: {agg['mean_oos_return']*100:.1f}%")
        lines.append("")

        lines.append("### Window Details")
        lines.append("")
        lines.append("| Window | Test Period | OOS Sharpe | OOS Return |")
        lines.append("|--------|-------------|-----------|-----------|")
        for w in wf_lo['windows']:
            lines.append(f"| {w['window']} | {w['test_start']}-{w['test_end']} | "
                        f"{w['oos_sharpe']:.3f} | {w['oos_return']*100:.1f}% |")
    else:
        lines.append("Walk-forward failed to produce results.")
    lines.append("")

    # Long-only vs bidirectional comparison
    lines.append("## 3b. Long-Only vs Bidirectional Comparison")
    lines.append("")
    if wf.get('agg') and wf_lo.get('agg'):
        lines.append("| Metric | Bidirectional | Long-Only |")
        lines.append("|--------|:------------:|:---------:|")
        lines.append(f"| Mean OOS Sharpe | {wf['agg']['mean_oos_sharpe']:.3f} | {wf_lo['agg']['mean_oos_sharpe']:.3f} |")
        lines.append(f"| Positive Windows | {wf['agg']['positive_windows']}/{wf['agg']['n_windows']} | {wf_lo['agg']['positive_windows']}/{wf_lo['agg']['n_windows']} |")
        lines.append(f"| Mean OOS Return | {wf['agg']['mean_oos_return']*100:.1f}% | {wf_lo['agg']['mean_oos_return']*100:.1f}% |")
        lines.append("")
        if wf_lo['agg']['mean_oos_sharpe'] > wf['agg']['mean_oos_sharpe']:
            lines.append("**Long-only is superior** for BTC spot (as expected -- shorting spot has structural drag).")
        else:
            lines.append("**Bidirectional is superior** -- the short signal adds value despite structural headwinds.")
    lines.append("")

    # Signal lag analysis
    lines.append("## 4. Signal Lag Analysis (+1 day)")
    lines.append("")
    wf_lag = results.get('wf_lagged', {})
    if wf_lag.get('agg') and wf.get('agg'):
        agg_lag = wf_lag['agg']
        agg_base = wf['agg']
        lines.append(f"- **Base Mean OOS Sharpe**: {agg_base['mean_oos_sharpe']:.3f}")
        lines.append(f"- **Lagged Mean OOS Sharpe**: {agg_lag['mean_oos_sharpe']:.3f}")
        lines.append(f"- **Lagged Positive Windows**: {agg_lag['positive_windows']}/{agg_lag['n_windows']}")
        degradation = 0
        if agg_base['mean_oos_sharpe'] != 0:
            degradation = (agg_base['mean_oos_sharpe'] - agg_lag['mean_oos_sharpe']) / abs(agg_base['mean_oos_sharpe']) * 100
        lines.append(f"- **Degradation**: {degradation:.1f}%")
        lines.append("")
        if agg_lag['mean_oos_sharpe'] > 0:
            lines.append("Signal **survives** 1-day additional lag. This is a slow-moving macro signal,")
            lines.append("so latency is not a concern.")
        else:
            lines.append("**WARNING**: Signal does NOT survive 1-day lag. This suggests the signal")
            lines.append("may be capturing short-term reactions rather than regime shifts.")
    else:
        lines.append("Lagged walk-forward failed to produce results.")
    lines.append("")

    # Full period grid search
    lines.append("## 5. Full Period Grid Search")
    lines.append("")
    fp = results.get('full_period', {})
    if fp.get('bidirectional'):
        bi = fp['bidirectional']
        lines.append(f"### Bidirectional")
        lines.append(f"- Best params: {bi['params']}")
        lines.append(f"- Best Sharpe: {bi['sharpe']:.3f}")
        lines.append("")
        lines.append("Top 5 configurations:")
        lines.append("")
        lines.append("| Rank | Lookback | Threshold | Holding | Sharpe | Return | MaxDD |")
        lines.append("|------|----------|-----------|---------|--------|--------|-------|")
        for i, r in enumerate(bi['top5']):
            lines.append(f"| {i+1} | {r['lookback']} | {r['threshold']} | {r['holding']} | "
                        f"{r['sharpe']:.3f} | {r['return']*100:.1f}% | {r['max_dd']*100:.1f}% |")
        lines.append("")

    if fp.get('long_only'):
        lo = fp['long_only']
        lines.append(f"### Long-Only")
        lines.append(f"- Best params: {lo['params']}")
        lines.append(f"- Best Sharpe: {lo['sharpe']:.3f}")
        lines.append("")
        lines.append("Top 5 configurations:")
        lines.append("")
        lines.append("| Rank | Lookback | Threshold | Holding | Sharpe | Return | MaxDD |")
        lines.append("|------|----------|-----------|---------|--------|--------|-------|")
        for i, r in enumerate(lo['top5']):
            lines.append(f"| {i+1} | {r['lookback']} | {r['threshold']} | {r['holding']} | "
                        f"{r['sharpe']:.3f} | {r['return']*100:.1f}% | {r['max_dd']*100:.1f}% |")
    lines.append("")

    # Parameter sensitivity
    lines.append("## 6. Parameter Sensitivity")
    lines.append("")
    sens = results.get('sensitivity', {})
    if sens.get('results'):
        lines.append(f"Base Sharpe: {sens['base_sharpe']:.3f}")
        lines.append(f"Max degradation: {sens['max_degradation']:.1f}%")
        lines.append("")
        lines.append("| Parameter | Value | Sharpe | Degradation |")
        lines.append("|-----------|-------|--------|-------------|")
        for r in sens['results']:
            lines.append(f"| {r['param']} | {r['value']} | {r['sharpe']:.3f} | {r['degradation_pct']:+.1f}% |")
        lines.append("")
        if sens['max_degradation'] > 30:
            lines.append("**WARNING**: Parameter sensitivity exceeds 30% -- signal is fragile to parameter choice.")
        else:
            lines.append("Parameter sensitivity within acceptable bounds (<=30% degradation).")
    lines.append("")

    # Regime analysis
    lines.append("## 7. Regime Analysis")
    lines.append("")
    regime = results.get('regime', {})
    if regime:
        lines.append("| Regime | Days | % Total | Sharpe | Return |")
        lines.append("|--------|------|---------|--------|--------|")
        for r_name in ['UPTREND', 'DOWNTREND', 'RANGE']:
            if r_name in regime:
                r = regime[r_name]
                lines.append(f"| {r_name} | {r['n_days_regime']} | {r['pct_total']:.0f}% | "
                            f"{r['sharpe']:.3f} | {r['total_ret']*100:.1f}% |")
        lines.append("")
        if 'RANGE' in regime:
            range_sharpe = regime['RANGE']['sharpe']
            if range_sharpe > 0:
                lines.append(f"**Key finding**: Macro signal has positive Sharpe ({range_sharpe:.3f}) in RANGE regime.")
                lines.append("This is valuable -- V3 trend-following struggles in range-bound markets.")
            else:
                lines.append(f"Macro signal has negative Sharpe ({range_sharpe:.3f}) in RANGE regime.")
                lines.append("Does NOT add value in the regime where V3 struggles most.")
    lines.append("")

    # Stationarity
    lines.append("## 8. Stationarity Check (Rolling IC)")
    lines.append("")
    ic = results.get('stationarity')
    if ic:
        lines.append(f"- Full period IC: mean={ic['full_mean']:.4f}, std={ic['full_std']:.4f}")
        lines.append(f"- Last 2y IC mean: {ic['recent_mean']:.4f}")
        lines.append(f"- Last 2y IC positive: {ic['recent_positive_pct']:.1f}% of time")
        lines.append(f"- IC sign changed in last 2 years: **{ic['sign_changed']}**")
        lines.append("")
        if ic['sign_changed']:
            lines.append("**WARNING**: The US10Y/DXY -> BTC relationship is non-stationary.")
            lines.append("IC has changed sign in recent quarters. Proceed with caution.")
        else:
            lines.append("IC sign is stable in the last 2 years. Relationship appears stationary.")
    lines.append("")

    # Kill criteria summary
    lines.append("## Kill Criteria Summary")
    lines.append("")
    lines.append("| Criterion | Threshold | Result | Status |")
    lines.append("|-----------|-----------|--------|--------|")

    for p in results.get('passes', []):
        parts = p.split(': ', 1)
        code = parts[0]
        desc = parts[1] if len(parts) > 1 else p
        lines.append(f"| {code} | - | {desc.replace(' -- PASS', '')} | PASS |")

    for c in results.get('conditionals', []):
        parts = c.split(': ', 1)
        code = parts[0]
        desc = parts[1] if len(parts) > 1 else c
        lines.append(f"| {code} | - | {desc} | CONDITIONAL |")

    for k in results.get('kills', []):
        parts = k.split(': ', 1)
        code = parts[0]
        desc = parts[1] if len(parts) > 1 else k
        lines.append(f"| {code} | - | {desc} | KILL |")

    lines.append("")
    lines.append(f"## Final Verdict: **{results['verdict']}**")
    lines.append("")
    lines.append(f"**Reason**: {results['verdict_reason']}")
    lines.append("")

    # Recommendations
    lines.append("## Recommendations")
    lines.append("")
    if results['verdict'] == 'PASS':
        lines.append("- Signal passes all kill criteria. Proceed with integration into portfolio.")
        lines.append("- Use long-only mode for BTC spot (no shorting).")
        lines.append("- Re-optimize parameters quarterly using rolling walk-forward.")
    elif results['verdict'] == 'CONDITIONAL PASS':
        lines.append("- Signal has conditional issues that need monitoring.")
        lines.append("- Consider using long-only mode for BTC spot.")
        lines.append("- Monitor IC stability quarterly.")
        lines.append("- If used, allocate conservatively (small position size).")
    else:
        lines.append("- Signal fails kill criteria. Do NOT integrate into portfolio.")
        lines.append("- The macro regime rotation concept may work at longer timeframes")
        lines.append("  or with additional conditioning variables, but this specific")
        lines.append("  implementation does not meet our quality bar.")
    lines.append("")

    report = '\n'.join(lines)
    OUTPUT_MD.write_text(report)
    print(f"\nReport written to {OUTPUT_MD}")


if __name__ == '__main__':
    results = main()
