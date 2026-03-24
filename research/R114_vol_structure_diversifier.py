#!/workspace/venv/bin/python
"""
R114: Realized Volatility Structure as BTC Diversifier Signal
==============================================================

Objective:
  Test realized vol STRUCTURE signals as portfolio diversifier for V3 (EMA trend).
  V3 is trend-following -- we need signals firing in DIFFERENT conditions
  (especially range markets where V3 loses money).

Hypothesis:
  1. Vol compression (short vol < long vol) precedes breakouts
  2. Vol expansion (short vol >> long vol) precedes mean-reversion
  3. Vol term structure slope predicts regime changes
  4. Realized skewness predicts tail risk

Signal Candidates (all use ONLY realized vol structure -- NOT VRP):
  S1: Vol ratio -- realized_vol(5d) / realized_vol(30d)
  S2: Vol term structure slope -- realized_vol(5d) - realized_vol(60d)
  S3: Realized skewness -- 30d rolling skew of 1h returns
  S4: Vol-of-vol -- rolling std of realized vol
  S5: Parkinson vol ratio -- high-low range estimator vs close-to-close vol
  S6: Vol regime binary -- above/below 60d median vol + direction of vol change

Key Constraint:
  V3 uses VRP (DVOL vs realized vol) as an OVERLAY already.
  These signals use ONLY realized vol STRUCTURE (term structure, skew, volvol).
  Must be DIFFERENT from VRP.

Kill Criteria:
  - IC < 0.02 across all horizons -> KILL
  - Correlation with V3 > 0.5 -> KILL
  - WF mean Sharpe < 0.3 -> KILL
  - Overlap with VRP overlay > 0.7 -> KILL (redundant)

Methodology:
  1. Compute all 6 signal variants from BTC 1h data
  2. IC scan at 1d, 3d, 7d, 14d horizons
  3. For top signals: deep walk-forward (10 windows, 180d train / 90d test)
  4. Check correlation with V3 daily returns
  5. Test combination: adding vol signal to V3 portfolio

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

# -- Paths -------------------------------------------------------------------
PROJECT_DIR = Path('/workspace/crypto_backtest')
DATA_DIR = PROJECT_DIR / 'data'
OUTPUT_MD = PROJECT_DIR / 'research' / 'R114_vol_structure_diversifier.md'

COST_BPS = 10        # round-trip cost in basis points
ANNUALIZE = np.sqrt(365)  # daily -> annual

# Walk-forward configuration
TRAIN_DAYS = 180
TEST_DAYS = 90
ROLL_DAYS = 90
N_WINDOWS = 10

# IC horizons (forward return days)
IC_HORIZONS = [1, 3, 7, 14]


# ==============================================================================
# DATA LOADING
# ==============================================================================

def load_btc_1h():
    """Load BTC 1h spot data."""
    print("[DATA] Loading BTC 1h spot...")
    df = pd.read_parquet(DATA_DIR / 'spot/1h_cache/BTC_1h.parquet')
    df.index = pd.to_datetime(df.index)
    df = df.sort_index()
    df = df[~df.index.duplicated(keep='first')]
    print(f"  {df.index.min().date()} to {df.index.max().date()}, {len(df)} bars")
    return df


def load_btc_daily(btc_1h):
    """Resample 1h to daily OHLCV."""
    daily = pd.DataFrame()
    daily['close'] = btc_1h['close'].resample('1D').last().dropna()
    daily['open'] = btc_1h['open'].resample('1D').first()
    daily['high'] = btc_1h['high'].resample('1D').max()
    daily['low'] = btc_1h['low'].resample('1D').min()
    daily['volume'] = btc_1h['volume'].resample('1D').sum()
    daily = daily.dropna()
    daily['ret'] = daily['close'].pct_change()
    daily['log_ret'] = np.log(daily['close'] / daily['close'].shift(1))
    print(f"  Daily: {daily.index.min().date()} to {daily.index.max().date()}, {len(daily)} days")
    return daily


# ==============================================================================
# V3 MOMENTUM BASELINE
# ==============================================================================

def compute_v3_momentum(daily):
    """
    V3 Momentum: Long when daily EMA(20) > EMA(50), flat otherwise.
    Weekly rebalance (Monday). Returns daily return series.
    """
    print("[V3] Computing V3 Momentum baseline...")
    ema20 = daily['close'].ewm(span=20, adjust=False).mean()
    ema50 = daily['close'].ewm(span=50, adjust=False).mean()
    uptrend = (ema20 > ema50).astype(int)

    # Use prior day's signal (no lookahead)
    pos = uptrend.shift(1).fillna(0).astype(int)

    # Weekly rebalance: lock position on Monday
    weekly_signal = pos.resample('W-MON').last()
    pos_weekly = weekly_signal.reindex(pos.index, method='ffill').fillna(0).astype(int)

    # Returns with costs
    daily_ret = daily['ret'].fillna(0)
    position_changes = pos_weekly.diff().abs().fillna(0)
    cost = position_changes * (COST_BPS / 10000)
    strat_ret = pos_weekly * daily_ret - cost

    days_long = (pos_weekly == 1).sum()
    total_days = len(pos_weekly)
    ann_ret = strat_ret.mean() * 365
    ann_vol = strat_ret.std() * ANNUALIZE
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0
    print(f"  V3: {days_long}/{total_days} days long ({100*days_long/total_days:.1f}%)")
    print(f"  Ann return: {ann_ret:.2%}, Sharpe: {sharpe:.3f}")

    return strat_ret, pos_weekly


# ==============================================================================
# SIGNAL COMPUTATION
# ==============================================================================

def compute_all_signals(btc_1h, daily):
    """Compute all 6 vol structure signals. Returns daily DataFrame of signals."""
    print("\n[SIGNALS] Computing all 6 volatility structure signals...")
    signals = pd.DataFrame(index=daily.index)

    # -- Common ingredients --
    log_ret = daily['log_ret'].fillna(0)

    # Realized vols at different horizons (annualized, in %)
    rv_5d  = log_ret.rolling(5).std() * np.sqrt(365) * 100
    rv_10d = log_ret.rolling(10).std() * np.sqrt(365) * 100
    rv_20d = log_ret.rolling(20).std() * np.sqrt(365) * 100
    rv_30d = log_ret.rolling(30).std() * np.sqrt(365) * 100
    rv_60d = log_ret.rolling(60).std() * np.sqrt(365) * 100

    # -- S1: Vol Ratio (short / long) --
    # Low ratio = compression (short vol < long vol) -> breakout coming
    # High ratio = expansion (short vol >> long vol) -> mean-reversion
    signals['S1_vol_ratio'] = rv_5d / rv_30d.replace(0, np.nan)
    print(f"  S1 vol_ratio: mean={signals['S1_vol_ratio'].mean():.3f}, "
          f"std={signals['S1_vol_ratio'].std():.3f}")

    # -- S2: Vol Term Structure Slope --
    # Positive = short-term vol elevated above long-term (contango)
    # Negative = backwardation (quiet short-term)
    signals['S2_vol_slope'] = rv_5d - rv_60d
    print(f"  S2 vol_slope: mean={signals['S2_vol_slope'].mean():.2f}, "
          f"std={signals['S2_vol_slope'].std():.2f}")

    # -- S3: Realized Skewness --
    # 30d rolling skew of 1h returns (use 1h for better precision)
    ret_1h = btc_1h['close'].pct_change()
    # Resample: compute daily skew from 24 hourly returns
    skew_daily = ret_1h.rolling(24 * 30, min_periods=24 * 15).skew()
    skew_daily = skew_daily.resample('1D').last()
    skew_daily = skew_daily.reindex(daily.index, method='ffill')
    signals['S3_skewness'] = skew_daily
    print(f"  S3 skewness: mean={signals['S3_skewness'].mean():.3f}, "
          f"std={signals['S3_skewness'].std():.3f}")

    # -- S4: Vol-of-Vol --
    # Rolling 30d std of 5d realized vol itself
    signals['S4_volvol'] = rv_5d.rolling(30, min_periods=15).std()
    print(f"  S4 volvol: mean={signals['S4_volvol'].mean():.2f}, "
          f"std={signals['S4_volvol'].std():.2f}")

    # -- S5: Parkinson Vol Ratio --
    # Parkinson estimator uses high-low range (captures intrabar vol)
    # Ratio = Parkinson / close-to-close vol -> high = intrabar anomaly
    log_hl = np.log(daily['high'] / daily['low'].replace(0, np.nan))
    parkinson_daily = log_hl ** 2 / (4 * np.log(2))
    parkinson_20d = np.sqrt(parkinson_daily.rolling(20).mean()) * np.sqrt(365) * 100
    cc_20d = rv_20d  # close-to-close 20d vol
    signals['S5_parkinson_ratio'] = parkinson_20d / cc_20d.replace(0, np.nan)
    print(f"  S5 parkinson_ratio: mean={signals['S5_parkinson_ratio'].mean():.3f}, "
          f"std={signals['S5_parkinson_ratio'].std():.3f}")

    # -- S6: Vol Regime Binary --
    # Combine: above/below 60d median vol AND direction of vol change
    vol_median_60d = rv_20d.rolling(60, min_periods=30).median()
    vol_above = (rv_20d > vol_median_60d).astype(int)
    vol_rising = (rv_20d > rv_20d.shift(5)).astype(int)
    # Encode: 0=low+falling, 1=low+rising, 2=high+falling, 3=high+rising
    signals['S6_vol_regime'] = vol_above * 2 + vol_rising
    # For IC, use continuous version: z-score of vol relative to median * direction
    vol_z = (rv_20d - vol_median_60d) / rv_20d.rolling(60, min_periods=30).std().replace(0, np.nan)
    vol_direction = rv_20d.diff(5)
    signals['S6_vol_regime_cont'] = vol_z * np.sign(vol_direction)
    print(f"  S6 vol_regime: regime counts = {signals['S6_vol_regime'].value_counts().to_dict()}")

    return signals


# ==============================================================================
# INFORMATION COEFFICIENT SCAN
# ==============================================================================

def compute_ic_scan(signals, daily):
    """
    Compute rank IC (Spearman) between each signal and forward returns.
    Returns DataFrame: rows = signals, cols = horizons.
    """
    print("\n[IC SCAN] Computing Information Coefficients...")
    signal_cols = [c for c in signals.columns if c != 'S6_vol_regime']  # skip categorical
    fwd_returns = {}
    for h in IC_HORIZONS:
        fwd_returns[h] = daily['ret'].rolling(h).sum().shift(-h)

    results = {}
    for col in signal_cols:
        sig = signals[col]
        row = {}
        for h in IC_HORIZONS:
            fwd = fwd_returns[h]
            mask = sig.notna() & fwd.notna()
            if mask.sum() < 100:
                row[f'{h}d_ic'] = np.nan
                row[f'{h}d_pval'] = np.nan
                continue
            ic, pval = stats.spearmanr(sig[mask], fwd[mask])
            row[f'{h}d_ic'] = ic
            row[f'{h}d_pval'] = pval
        results[col] = row

    ic_df = pd.DataFrame(results).T
    for h in IC_HORIZONS:
        ic_col = f'{h}d_ic'
        pval_col = f'{h}d_pval'
        if ic_col in ic_df.columns:
            for sig_name in ic_df.index:
                ic_val = ic_df.loc[sig_name, ic_col]
                pval = ic_df.loc[sig_name, pval_col]
                star = "***" if pval < 0.001 else "**" if pval < 0.01 else "*" if pval < 0.05 else ""
                print(f"  {sig_name:25s} {h:2d}d IC = {ic_val:+.4f} {star}")

    return ic_df


def compute_rolling_ic(signals, daily, window=180, horizon=7):
    """Compute rolling IC to check stationarity."""
    print(f"\n[ROLLING IC] {window}d rolling IC at {horizon}d horizon...")
    signal_cols = [c for c in signals.columns if c != 'S6_vol_regime']
    fwd = daily['ret'].rolling(horizon).sum().shift(-horizon)
    rolling_ics = {}

    for col in signal_cols:
        sig = signals[col]
        ic_series = pd.Series(np.nan, index=signals.index)
        for i in range(window, len(signals) - horizon):
            start = i - window
            end = i
            s = sig.iloc[start:end]
            f = fwd.iloc[start:end]
            mask = s.notna() & f.notna()
            if mask.sum() > 50:
                ic, _ = stats.spearmanr(s[mask], f[mask])
                ic_series.iloc[i] = ic
        rolling_ics[col] = ic_series

    rolling_df = pd.DataFrame(rolling_ics)

    # Check stationarity: IC sign stability in last 2 years
    cutoff = signals.index.max() - pd.Timedelta(days=730)
    for col in signal_cols:
        recent = rolling_df[col].dropna()
        recent = recent[recent.index >= cutoff]
        if len(recent) > 30:
            pct_positive = (recent > 0).mean()
            mean_ic = recent.mean()
            print(f"  {col:25s} last 2y: mean IC = {mean_ic:+.4f}, "
                  f"pct positive = {pct_positive:.1%}")

    return rolling_df


# ==============================================================================
# SIGNAL -> POSITION MAPPING
# ==============================================================================

def signal_to_position(signal_series, signal_name, daily, params=None):
    """
    Convert continuous signal to discrete position {-1, 0, +1}.
    Each signal has its own interpretation logic.

    Parameters:
    - params: dict of tunable parameters for WF optimization
    """
    if params is None:
        params = {}

    pos = pd.Series(0.0, index=daily.index)
    sig = signal_series.reindex(daily.index)

    if signal_name.startswith('S1_vol_ratio'):
        # Low ratio = compression -> go long (breakout expected)
        # High ratio = expansion -> go short (mean-reversion expected)
        low_thresh = params.get('low_thresh', 0.7)
        high_thresh = params.get('high_thresh', 1.3)
        pos[sig < low_thresh] = 1.0   # compression -> long
        pos[sig > high_thresh] = -1.0  # expansion -> short

    elif signal_name.startswith('S2_vol_slope'):
        # Negative slope (short vol < long vol) = compression -> long
        # Positive slope (short vol > long vol) = expansion -> short
        neg_thresh = params.get('neg_thresh', -5.0)
        pos_thresh = params.get('pos_thresh', 5.0)
        pos[sig < neg_thresh] = 1.0    # backwardation -> long
        pos[sig > pos_thresh] = -1.0   # contango -> short

    elif signal_name.startswith('S3_skewness'):
        # Negative skew = crash risk -> go flat/short
        # Positive skew = upside momentum -> go long
        neg_thresh = params.get('neg_thresh', -0.3)
        pos_thresh = params.get('pos_thresh', 0.3)
        pos[sig < neg_thresh] = -1.0   # left tail risk -> short
        pos[sig > pos_thresh] = 1.0    # right tail -> long

    elif signal_name.startswith('S4_volvol'):
        # High volvol = regime uncertainty -> go flat (reduce exposure)
        # Low volvol = stable regime -> go long (trend continuation)
        z = (sig - sig.rolling(60, min_periods=30).mean()) / sig.rolling(60, min_periods=30).std().replace(0, np.nan)
        low_thresh = params.get('low_thresh', -0.5)
        high_thresh = params.get('high_thresh', 0.5)
        pos[z < low_thresh] = 1.0    # stable vol -> long
        pos[z > high_thresh] = -1.0  # uncertain vol -> short/flat

    elif signal_name.startswith('S5_parkinson'):
        # High Parkinson/CC ratio = intrabar vol anomaly -> potential reversal
        # Low ratio = normal -> trend continuation
        z = (sig - sig.rolling(60, min_periods=30).mean()) / sig.rolling(60, min_periods=30).std().replace(0, np.nan)
        low_thresh = params.get('low_thresh', -0.5)
        high_thresh = params.get('high_thresh', 0.5)
        pos[z < low_thresh] = 1.0    # normal range -> continue trend
        pos[z > high_thresh] = -1.0  # anomalous range -> reversal

    elif signal_name.startswith('S6_vol_regime'):
        # Use continuous version for trading
        z_thresh = params.get('z_thresh', 0.5)
        pos[sig < -z_thresh] = 1.0    # low vol + falling = compression -> long
        pos[sig > z_thresh] = -1.0    # high vol + rising = expansion -> short

    # Apply 1-day lag to avoid lookahead
    pos = pos.shift(1).fillna(0)

    return pos


# ==============================================================================
# BACKTEST ENGINE
# ==============================================================================

def backtest_signal(position, daily, cost_bps=COST_BPS):
    """
    Backtest a position series against BTC daily returns.
    Returns dict with performance metrics.
    """
    common = position.index.intersection(daily.index)
    if len(common) < 30:
        return None

    pos = position.loc[common]
    ret = daily.loc[common, 'ret'].fillna(0)

    # Position changes for cost
    trades = pos.diff().abs().fillna(0)
    trade_cost = trades * (cost_bps / 10000)

    strat_ret = pos * ret - trade_cost
    strat_ret_clean = strat_ret.dropna()
    n_days = len(strat_ret_clean)
    if n_days < 20:
        return None

    total_ret = (1 + strat_ret_clean).prod() - 1
    ann_ret = (1 + total_ret) ** (365 / n_days) - 1
    ann_vol = strat_ret_clean.std() * ANNUALIZE
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0

    # Drawdown
    cum = (1 + strat_ret_clean).cumprod()
    peak = cum.cummax()
    dd = cum / peak - 1
    max_dd = dd.min()

    # Signal stats
    n_trades = int(trades.sum() / 2)
    pct_long = (pos == 1).mean()
    pct_short = (pos == -1).mean()
    pct_flat = (pos == 0).mean()
    signal_changes = int((pos.diff() != 0).sum())

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
        'strat_ret': strat_ret_clean,
    }


# ==============================================================================
# WALK-FORWARD ENGINE
# ==============================================================================

def get_param_grid(signal_name):
    """Return parameter grid for each signal."""
    if signal_name == 'S1_vol_ratio':
        return list(product(
            [0.5, 0.6, 0.7, 0.8],       # low_thresh
            [1.2, 1.3, 1.4, 1.5, 1.6],  # high_thresh
        ))
    elif signal_name == 'S2_vol_slope':
        return list(product(
            [-3, -5, -7, -10],   # neg_thresh
            [3, 5, 7, 10],      # pos_thresh
        ))
    elif signal_name == 'S3_skewness':
        return list(product(
            [-0.2, -0.3, -0.5, -0.7],  # neg_thresh
            [0.2, 0.3, 0.5, 0.7],      # pos_thresh
        ))
    elif signal_name == 'S4_volvol':
        return list(product(
            [-1.0, -0.5, -0.3, 0.0],    # low_thresh
            [0.3, 0.5, 1.0, 1.5],       # high_thresh
        ))
    elif signal_name == 'S5_parkinson_ratio':
        return list(product(
            [-1.0, -0.5, -0.3, 0.0],    # low_thresh
            [0.3, 0.5, 1.0, 1.5],       # high_thresh
        ))
    elif signal_name == 'S6_vol_regime_cont':
        return list(product(
            [0.3, 0.5, 0.7, 1.0, 1.5],  # z_thresh
        ))
    return []


def params_to_dict(signal_name, param_tuple):
    """Convert grid tuple to named params dict."""
    if signal_name == 'S1_vol_ratio':
        return {'low_thresh': param_tuple[0], 'high_thresh': param_tuple[1]}
    elif signal_name == 'S2_vol_slope':
        return {'neg_thresh': param_tuple[0], 'pos_thresh': param_tuple[1]}
    elif signal_name == 'S3_skewness':
        return {'neg_thresh': param_tuple[0], 'pos_thresh': param_tuple[1]}
    elif signal_name in ('S4_volvol', 'S5_parkinson_ratio'):
        return {'low_thresh': param_tuple[0], 'high_thresh': param_tuple[1]}
    elif signal_name == 'S6_vol_regime_cont':
        return {'z_thresh': param_tuple[0]}
    return {}


def walk_forward_signal(signal_name, signals, daily, n_windows=N_WINDOWS,
                        train_days=TRAIN_DAYS, test_days=TEST_DAYS):
    """
    Walk-forward optimization for a single signal.
    Returns list of window results + aggregated metrics.
    """
    print(f"\n[WF] Walk-forward for {signal_name}: {n_windows} windows, "
          f"{train_days}d train / {test_days}d test")

    param_grid = get_param_grid(signal_name)
    if not param_grid:
        print(f"  No param grid for {signal_name}, skipping WF")
        return None, None

    print(f"  Parameter grid: {len(param_grid)} combinations")

    sig_data = signals[signal_name]
    all_dates = daily.index.sort_values()
    total_days = len(all_dates)
    first_valid = sig_data.first_valid_index()
    if first_valid is None:
        return None, None

    # Need enough warmup
    # Find the index position of the first valid signal date
    if first_valid in all_dates:
        warmup_idx = all_dates.searchsorted(first_valid)
    else:
        warmup_idx = all_dates.searchsorted(first_valid, side='left')
    warmup_idx = min(warmup_idx, total_days - 1)
    start_idx = max(warmup_idx + 60, 0)  # at least 60 days warmup after signal starts

    window_results = []
    oos_returns = []

    for w in range(n_windows):
        train_start_idx = start_idx + w * ROLL_DAYS
        train_end_idx = train_start_idx + train_days
        test_start_idx = train_end_idx
        test_end_idx = test_start_idx + test_days

        if test_end_idx >= total_days:
            print(f"  Window {w+1}: insufficient data, stopping")
            break

        train_start = all_dates[train_start_idx]
        train_end = all_dates[train_end_idx - 1]
        test_start = all_dates[test_start_idx]
        test_end = all_dates[min(test_end_idx - 1, total_days - 1)]

        # Train: grid search
        best_sharpe = -999
        best_params = None

        for pt in param_grid:
            p = params_to_dict(signal_name, pt)
            train_daily = daily.loc[train_start:train_end]
            train_sig = sig_data.loc[train_start:train_end]
            pos = signal_to_position(train_sig, signal_name, train_daily, p)
            result = backtest_signal(pos, train_daily)
            if result and result['n_days'] > 30:
                if result['sharpe'] > best_sharpe:
                    best_sharpe = result['sharpe']
                    best_params = p

        if best_params is None:
            print(f"  Window {w+1}: no valid train result")
            continue

        # Test: apply best params OOS
        test_daily = daily.loc[test_start:test_end]
        test_sig = sig_data.loc[test_start:test_end]
        pos_oos = signal_to_position(test_sig, signal_name, test_daily, best_params)
        oos_result = backtest_signal(pos_oos, test_daily)

        if oos_result is None:
            print(f"  Window {w+1}: no valid OOS result")
            continue

        window_results.append({
            'window': w + 1,
            'train_start': str(train_start.date()),
            'train_end': str(train_end.date()),
            'test_start': str(test_start.date()),
            'test_end': str(test_end.date()),
            'train_sharpe': best_sharpe,
            'oos_sharpe': oos_result['sharpe'],
            'oos_ann_ret': oos_result['ann_ret'],
            'oos_max_dd': oos_result['max_dd'],
            'oos_n_trades': oos_result['n_trades'],
            'best_params': best_params,
        })
        if 'strat_ret' in oos_result:
            oos_returns.append(oos_result['strat_ret'])

        print(f"  W{w+1}: train Sharpe={best_sharpe:.3f}, "
              f"OOS Sharpe={oos_result['sharpe']:.3f}, "
              f"OOS ann_ret={oos_result['ann_ret']:.2%}, "
              f"OOS maxDD={oos_result['max_dd']:.2%}, "
              f"params={best_params}")

    if not window_results:
        return None, None

    # Aggregate
    oos_sharpes = [w['oos_sharpe'] for w in window_results]
    agg = {
        'n_windows': len(window_results),
        'mean_oos_sharpe': np.mean(oos_sharpes),
        'median_oos_sharpe': np.median(oos_sharpes),
        'std_oos_sharpe': np.std(oos_sharpes),
        'pct_positive': np.mean([s > 0 for s in oos_sharpes]),
        'min_oos_sharpe': np.min(oos_sharpes),
        'max_oos_sharpe': np.max(oos_sharpes),
        'mean_oos_ret': np.mean([w['oos_ann_ret'] for w in window_results]),
        'mean_oos_dd': np.mean([w['oos_max_dd'] for w in window_results]),
    }

    # Concatenate OOS returns for full equity curve
    if oos_returns:
        full_oos = pd.concat(oos_returns).sort_index()
        full_oos = full_oos[~full_oos.index.duplicated(keep='first')]
        agg['full_oos_ret'] = full_oos

    print(f"\n  [AGG] {signal_name}: mean OOS Sharpe = {agg['mean_oos_sharpe']:.3f}, "
          f"{agg['pct_positive']:.0%} positive windows")

    return window_results, agg


# ==============================================================================
# CORRELATION & DIVERSIFICATION ANALYSIS
# ==============================================================================

def compute_correlation_with_v3(signal_ret, v3_ret, signal_name):
    """
    Compute daily return correlation between signal strategy and V3.
    Also compute crash-day correlation and regime-conditional correlation.
    """
    print(f"\n[CORR] {signal_name} vs V3 Momentum...")

    common = signal_ret.index.intersection(v3_ret.index)
    s = signal_ret.reindex(common).fillna(0)
    v = v3_ret.reindex(common).fillna(0)

    mask = s.notna() & v.notna()
    s, v = s[mask], v[mask]

    if len(s) < 30:
        return None

    # Full period correlation
    full_corr, full_pval = stats.pearsonr(s, v)

    # Crash-day correlation (V3 worst 10% days)
    v3_threshold = v.quantile(0.10)
    crash_mask = v <= v3_threshold
    crash_corr = np.nan
    if crash_mask.sum() > 20:
        crash_corr, _ = stats.pearsonr(s[crash_mask], v[crash_mask])

    # V3 losing-day correlation
    v3_losing = v < 0
    losing_corr = np.nan
    if v3_losing.sum() > 20:
        losing_corr, _ = stats.pearsonr(s[v3_losing], v[v3_losing])

    # Rolling 60d correlation
    rolling_corr = s.rolling(60, min_periods=30).corr(v)
    mean_rolling = rolling_corr.mean()
    std_rolling = rolling_corr.std()

    result = {
        'full_corr': full_corr,
        'full_pval': full_pval,
        'crash_corr': crash_corr,
        'losing_corr': losing_corr,
        'rolling_corr_mean': mean_rolling,
        'rolling_corr_std': std_rolling,
    }

    print(f"  Full correlation: {full_corr:.3f} (p={full_pval:.4f})")
    print(f"  Crash-day corr:   {crash_corr:.3f}")
    print(f"  V3-losing corr:   {losing_corr:.3f}")
    print(f"  Rolling 60d corr: {mean_rolling:.3f} +/- {std_rolling:.3f}")

    return result


def test_portfolio_combination(signal_ret, v3_ret, signal_name, weights=None):
    """
    Test combined portfolio of V3 + vol signal.
    Default weights: 70/30 (V3 anchor).
    Also test 50/50, 60/40, and risk parity.
    """
    print(f"\n[PORTFOLIO] {signal_name} + V3 combination...")

    common = signal_ret.index.intersection(v3_ret.index)
    s = signal_ret.reindex(common).fillna(0)
    v = v3_ret.reindex(common).fillna(0)

    if len(s) < 60:
        return None

    results = {}

    # Compute V3-only metrics for comparison
    v3_ann_ret = v.mean() * 365
    v3_ann_vol = v.std() * ANNUALIZE
    v3_sharpe = v3_ann_ret / v3_ann_vol if v3_ann_vol > 0 else 0
    v3_cum = (1 + v).cumprod()
    v3_dd = (v3_cum / v3_cum.cummax() - 1).min()
    results['V3_only'] = {
        'sharpe': v3_sharpe, 'ann_ret': v3_ann_ret,
        'ann_vol': v3_ann_vol, 'max_dd': v3_dd
    }

    # Signal-only metrics
    s_ann_ret = s.mean() * 365
    s_ann_vol = s.std() * ANNUALIZE
    s_sharpe = s_ann_ret / s_ann_vol if s_ann_vol > 0 else 0
    s_cum = (1 + s).cumprod()
    s_dd = (s_cum / s_cum.cummax() - 1).min()
    results['signal_only'] = {
        'sharpe': s_sharpe, 'ann_ret': s_ann_ret,
        'ann_vol': s_ann_vol, 'max_dd': s_dd
    }

    # Portfolio combinations
    for name, wv, ws in [('50/50', 0.5, 0.5), ('60/40', 0.6, 0.4),
                          ('70/30', 0.7, 0.3)]:
        port_ret = wv * v + ws * s
        p_ann_ret = port_ret.mean() * 365
        p_ann_vol = port_ret.std() * ANNUALIZE
        p_sharpe = p_ann_ret / p_ann_vol if p_ann_vol > 0 else 0
        p_cum = (1 + port_ret).cumprod()
        p_dd = (p_cum / p_cum.cummax() - 1).min()
        results[name] = {
            'sharpe': p_sharpe, 'ann_ret': p_ann_ret,
            'ann_vol': p_ann_vol, 'max_dd': p_dd
        }
        print(f"  {name}: Sharpe={p_sharpe:.3f}, AnnRet={p_ann_ret:.2%}, MaxDD={p_dd:.2%}")

    # Risk parity (inverse vol weighted)
    v3_vol = v.rolling(60, min_periods=30).std()
    sig_vol = s.rolling(60, min_periods=30).std()
    total_inv_vol = (1 / v3_vol) + (1 / sig_vol)
    w_v3 = (1 / v3_vol) / total_inv_vol
    w_sig = (1 / sig_vol) / total_inv_vol
    rp_ret = w_v3 * v + w_sig * s
    rp_ret = rp_ret.dropna()
    if len(rp_ret) > 30:
        rp_ann_ret = rp_ret.mean() * 365
        rp_ann_vol = rp_ret.std() * ANNUALIZE
        rp_sharpe = rp_ann_ret / rp_ann_vol if rp_ann_vol > 0 else 0
        rp_cum = (1 + rp_ret).cumprod()
        rp_dd = (rp_cum / rp_cum.cummax() - 1).min()
        results['risk_parity'] = {
            'sharpe': rp_sharpe, 'ann_ret': rp_ann_ret,
            'ann_vol': rp_ann_vol, 'max_dd': rp_dd
        }
        print(f"  Risk parity: Sharpe={rp_sharpe:.3f}, AnnRet={rp_ann_ret:.2%}, MaxDD={rp_dd:.2%}")

    print(f"  V3 alone:     Sharpe={v3_sharpe:.3f}, AnnRet={v3_ann_ret:.2%}, MaxDD={v3_dd:.2%}")

    return results


# ==============================================================================
# REGIME ANALYSIS
# ==============================================================================

def regime_analysis(signal_ret, v3_ret, daily, signal_name):
    """
    Break down performance by BTC market regime:
    UPTREND (price > SMA50), DOWNTREND (price < SMA50), RANGE (flat SMA50).
    """
    print(f"\n[REGIME] {signal_name} performance by regime...")

    sma50 = daily['close'].rolling(50).mean()
    sma50_slope = sma50.pct_change(10)

    regime = pd.Series('RANGE', index=daily.index)
    regime[(daily['close'] > sma50) & (sma50_slope > 0.01)] = 'UPTREND'
    regime[(daily['close'] < sma50) & (sma50_slope < -0.01)] = 'DOWNTREND'

    common = signal_ret.index.intersection(v3_ret.index).intersection(regime.index)
    s = signal_ret.reindex(common).fillna(0)
    v = v3_ret.reindex(common).fillna(0)
    r = regime.reindex(common)

    results = {}
    for reg in ['UPTREND', 'DOWNTREND', 'RANGE']:
        mask = r == reg
        if mask.sum() < 20:
            continue
        s_reg = s[mask]
        v_reg = v[mask]

        s_sharpe = s_reg.mean() / s_reg.std() * ANNUALIZE if s_reg.std() > 0 else 0
        v_sharpe = v_reg.mean() / v_reg.std() * ANNUALIZE if v_reg.std() > 0 else 0

        corr = np.nan
        if mask.sum() > 20:
            corr, _ = stats.pearsonr(s_reg, v_reg)

        results[reg] = {
            'n_days': mask.sum(),
            'signal_sharpe': s_sharpe,
            'v3_sharpe': v_sharpe,
            'correlation': corr,
            'signal_mean_ret': s_reg.mean() * 365,
            'v3_mean_ret': v_reg.mean() * 365,
        }
        print(f"  {reg:10s}: {mask.sum():4d} days | "
              f"Signal Sharpe={s_sharpe:.3f}, V3 Sharpe={v_sharpe:.3f}, "
              f"Corr={corr:.3f}")

    return results


# ==============================================================================
# KILL CRITERIA EVALUATION
# ==============================================================================

def evaluate_kill_criteria(ic_df, wf_results, corr_results, signal_name, port_results=None):
    """
    Check all kill criteria for a signal. Returns (alive, reasons) tuple.

    STRICT evaluation: mean Sharpe is skewed by outlier windows.
    We use MEDIAN Sharpe and require >= 50% positive windows.
    """
    reasons = []
    killed = False

    # Kill 1: IC < 0.02 across all horizons
    ic_cols = [c for c in ic_df.columns if c.endswith('_ic')]
    max_abs_ic = 0
    for col in ic_cols:
        if signal_name in ic_df.index:
            ic_val = abs(ic_df.loc[signal_name, col])
            if not np.isnan(ic_val):
                max_abs_ic = max(max_abs_ic, ic_val)
    if max_abs_ic < 0.02:
        reasons.append(f"KILL: max |IC| = {max_abs_ic:.4f} < 0.02")
        killed = True
    else:
        reasons.append(f"PASS: max |IC| = {max_abs_ic:.4f} >= 0.02")

    # Kill 2: Correlation with V3 > 0.5
    if corr_results:
        corr_v3 = abs(corr_results.get('full_corr', 0))
        if corr_v3 > 0.5:
            reasons.append(f"KILL: |corr V3| = {corr_v3:.3f} > 0.5")
            killed = True
        else:
            reasons.append(f"PASS: |corr V3| = {corr_v3:.3f} <= 0.5")

    # Kill 3: WF MEDIAN Sharpe < 0.3 (use median to avoid outlier inflation)
    if wf_results:
        median_sharpe = wf_results.get('median_oos_sharpe', 0)
        mean_sharpe = wf_results.get('mean_oos_sharpe', 0)
        pct_pos = wf_results.get('pct_positive', 0)
        reasons.append(f"INFO: WF mean OOS Sharpe = {mean_sharpe:.3f} (outlier-sensitive)")
        if median_sharpe < 0.3:
            reasons.append(f"KILL: WF median OOS Sharpe = {median_sharpe:.3f} < 0.3")
            killed = True
        else:
            reasons.append(f"PASS: WF median OOS Sharpe = {median_sharpe:.3f} >= 0.3")
        # Additional: require >= 50% positive windows
        if pct_pos < 0.5:
            reasons.append(f"KILL: Only {pct_pos:.0%} positive OOS windows (need >= 50%)")
            killed = True
        else:
            reasons.append(f"PASS: {pct_pos:.0%} positive OOS windows >= 50%")
    else:
        reasons.append("KILL: No valid WF results")
        killed = True

    # Kill 4: Portfolio combination must beat V3 (at any weight) to have diversification value
    if port_results:
        v3_sharpe = port_results.get('V3_only', {}).get('sharpe', 0)
        any_improvement = False
        for alloc in ['50/50', '60/40', '70/30', 'risk_parity']:
            if alloc in port_results:
                p_sharpe = port_results[alloc]['sharpe']
                p_dd = port_results[alloc]['max_dd']
                v3_dd = port_results.get('V3_only', {}).get('max_dd', -1)
                # Improvement = better Sharpe OR same Sharpe + better DD
                if p_sharpe > v3_sharpe or (p_sharpe > v3_sharpe * 0.95 and p_dd > v3_dd * 0.8):
                    any_improvement = True
                    break
        if any_improvement:
            reasons.append(f"PASS: Portfolio improves over V3-only at some allocation")
        else:
            reasons.append(f"WARN: No portfolio allocation improves over V3-only Sharpe ({v3_sharpe:.3f})")

    return not killed, reasons


# ==============================================================================
# REPORT GENERATION
# ==============================================================================

def generate_report(ic_df, rolling_ic_df, signal_results, v3_sharpe_full):
    """Generate markdown report."""
    print("\n[REPORT] Generating R114 report...")

    lines = []
    lines.append("# R114: Realized Volatility Structure as BTC Diversifier Signal")
    lines.append(f"\nDate: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"\nV3 Baseline Sharpe (full period): {v3_sharpe_full:.3f}")

    # === IC Scan ===
    lines.append("\n## 1. Information Coefficient Scan")
    lines.append("\n| Signal | 1d IC | 3d IC | 7d IC | 14d IC | Best Horizon |")
    lines.append("|--------|-------|-------|-------|--------|-------------|")
    for sig_name in ic_df.index:
        row = []
        best_h = None
        best_ic = 0
        for h in IC_HORIZONS:
            ic_col = f'{h}d_ic'
            pval_col = f'{h}d_pval'
            ic_val = ic_df.loc[sig_name, ic_col] if ic_col in ic_df.columns else np.nan
            pval = ic_df.loc[sig_name, pval_col] if pval_col in ic_df.columns else np.nan
            star = ""
            if not np.isnan(pval):
                if pval < 0.001: star = "***"
                elif pval < 0.01: star = "**"
                elif pval < 0.05: star = "*"
            row.append(f"{ic_val:+.4f}{star}" if not np.isnan(ic_val) else "N/A")
            if not np.isnan(ic_val) and abs(ic_val) > abs(best_ic):
                best_ic = ic_val
                best_h = f"{h}d"
        lines.append(f"| {sig_name} | {' | '.join(row)} | {best_h or 'N/A'} ({best_ic:+.4f}) |")

    # === Rolling IC Stationarity ===
    lines.append("\n## 2. Rolling IC Stationarity (last 2 years)")
    lines.append("\n| Signal | Mean IC (2y) | Pct Positive | Stable? |")
    lines.append("|--------|-------------|-------------|---------|")
    cutoff = rolling_ic_df.index.max() - pd.Timedelta(days=730)
    for col in rolling_ic_df.columns:
        recent = rolling_ic_df[col].dropna()
        recent = recent[recent.index >= cutoff]
        if len(recent) > 30:
            mean_ic = recent.mean()
            pct_pos = (recent > 0).mean()
            stable = "Yes" if abs(mean_ic) > 0.01 and pct_pos > 0.55 else "No"
            lines.append(f"| {col} | {mean_ic:+.4f} | {pct_pos:.1%} | {stable} |")

    # === Per-Signal Deep Results ===
    lines.append("\n## 3. Signal-by-Signal Results")
    for sig_name, res in signal_results.items():
        lines.append(f"\n### {sig_name}")

        # Kill criteria
        alive = res.get('alive', False)
        kill_reasons = res.get('kill_reasons', [])
        verdict = "ALIVE" if alive else "KILLED"
        lines.append(f"\n**Verdict: {verdict}**")
        for r in kill_reasons:
            lines.append(f"- {r}")

        # WF results
        wf_windows = res.get('wf_windows')
        wf_agg = res.get('wf_agg')
        if wf_windows:
            lines.append(f"\n#### Walk-Forward Results ({wf_agg['n_windows']} windows)")
            lines.append(f"- Mean OOS Sharpe: {wf_agg['mean_oos_sharpe']:.3f}")
            lines.append(f"- Median OOS Sharpe: {wf_agg['median_oos_sharpe']:.3f}")
            lines.append(f"- Positive windows: {wf_agg['pct_positive']:.0%}")
            lines.append(f"- Mean OOS return: {wf_agg['mean_oos_ret']:.2%}")
            lines.append(f"- Mean OOS MaxDD: {wf_agg['mean_oos_dd']:.2%}")

            lines.append("\n| Window | Train Period | Test Period | Train Sharpe | OOS Sharpe | OOS Return | OOS MaxDD | Params |")
            lines.append("|--------|-------------|-------------|-------------|-----------|-----------|----------|--------|")
            for w in wf_windows:
                lines.append(
                    f"| {w['window']} | {w['train_start']}..{w['train_end']} | "
                    f"{w['test_start']}..{w['test_end']} | "
                    f"{w['train_sharpe']:.3f} | {w['oos_sharpe']:.3f} | "
                    f"{w['oos_ann_ret']:.2%} | {w['oos_max_dd']:.2%} | "
                    f"{w['best_params']} |"
                )

        # Correlation with V3
        corr = res.get('correlation')
        if corr:
            lines.append(f"\n#### Correlation with V3")
            lines.append(f"- Full period: {corr['full_corr']:.3f}")
            lines.append(f"- Crash days: {corr['crash_corr']:.3f}")
            lines.append(f"- V3 losing days: {corr['losing_corr']:.3f}")
            lines.append(f"- Rolling 60d: {corr['rolling_corr_mean']:.3f} +/- {corr['rolling_corr_std']:.3f}")

        # Portfolio
        port = res.get('portfolio')
        if port:
            lines.append(f"\n#### Portfolio Combination")
            lines.append("| Allocation | Sharpe | Ann Return | Max DD |")
            lines.append("|-----------|--------|-----------|--------|")
            for alloc, metrics in port.items():
                lines.append(
                    f"| {alloc} | {metrics['sharpe']:.3f} | "
                    f"{metrics['ann_ret']:.2%} | {metrics['max_dd']:.2%} |"
                )

        # Regime
        regime = res.get('regime')
        if regime:
            lines.append(f"\n#### Regime Analysis")
            lines.append("| Regime | Days | Signal Sharpe | V3 Sharpe | Correlation |")
            lines.append("|--------|------|--------------|----------|-------------|")
            for reg, m in regime.items():
                lines.append(
                    f"| {reg} | {m['n_days']} | {m['signal_sharpe']:.3f} | "
                    f"{m['v3_sharpe']:.3f} | {m['correlation']:.3f} |"
                )

    # === Summary & Recommendation ===
    lines.append("\n## 4. Summary")
    alive_signals = [k for k, v in signal_results.items() if v.get('alive')]
    killed_signals = [k for k, v in signal_results.items() if not v.get('alive')]

    lines.append(f"\n**Alive signals ({len(alive_signals)}):** {', '.join(alive_signals) if alive_signals else 'None'}")
    lines.append(f"**Killed signals ({len(killed_signals)}):** {', '.join(killed_signals)}")

    if alive_signals:
        lines.append("\n### Recommendation")
        # Find best alive signal by WF Sharpe
        best_sig = None
        best_sharpe = -999
        for sig in alive_signals:
            wf = signal_results[sig].get('wf_agg')
            if wf and wf['mean_oos_sharpe'] > best_sharpe:
                best_sharpe = wf['mean_oos_sharpe']
                best_sig = sig
        if best_sig:
            corr_v3 = signal_results[best_sig].get('correlation', {}).get('full_corr', np.nan)
            lines.append(f"- Best diversifier: **{best_sig}** (WF Sharpe={best_sharpe:.3f}, V3 corr={corr_v3:.3f})")
            port = signal_results[best_sig].get('portfolio', {})
            best_port = None
            best_port_sharpe = -999
            for alloc, m in port.items():
                if m['sharpe'] > best_port_sharpe:
                    best_port_sharpe = m['sharpe']
                    best_port = alloc
            if best_port:
                lines.append(f"- Best allocation: {best_port} (Sharpe={best_port_sharpe:.3f})")
            regime_info = signal_results[best_sig].get('regime', {})
            range_regime = regime_info.get('RANGE')
            if range_regime:
                lines.append(f"- RANGE regime Sharpe: {range_regime['signal_sharpe']:.3f} "
                             f"(V3 in RANGE: {range_regime['v3_sharpe']:.3f})")
                if range_regime['signal_sharpe'] > 0 and range_regime['v3_sharpe'] <= 0:
                    lines.append("- **KEY FINDING**: Signal profitable in RANGE regime where V3 loses money")
    else:
        lines.append("\n### Recommendation")
        lines.append("- All signals killed. Realized vol structure does not provide diversification value over V3.")
        lines.append("- Consider: (a) different signal construction, (b) non-BTC vol structure, or (c) cross-asset vol signals.")

    # Kill criteria summary table
    lines.append("\n## 5. Kill Criteria Summary")
    lines.append("\n| Signal | IC >= 0.02 | V3 Corr <= 0.5 | WF Sharpe >= 0.3 | Overall |")
    lines.append("|--------|-----------|---------------|-----------------|---------|")
    for sig_name, res in signal_results.items():
        ic_pass = "PASS" if any("PASS" in r and "IC" in r for r in res.get('kill_reasons', [])) else "FAIL"
        corr_pass = "PASS" if any("PASS" in r and "corr" in r for r in res.get('kill_reasons', [])) else "FAIL"
        wf_pass = "PASS" if any("PASS" in r and "Sharpe" in r for r in res.get('kill_reasons', [])) else "FAIL"
        overall = "ALIVE" if res.get('alive') else "KILLED"
        lines.append(f"| {sig_name} | {ic_pass} | {corr_pass} | {wf_pass} | **{overall}** |")

    report = "\n".join(lines)
    return report


# ==============================================================================
# MAIN EXECUTION
# ==============================================================================

def main():
    print("=" * 80)
    print("R114: Realized Volatility Structure as BTC Diversifier Signal")
    print("=" * 80)

    # --- Load Data ---
    btc_1h = load_btc_1h()
    daily = load_btc_daily(btc_1h)

    # --- Compute V3 Baseline ---
    v3_ret, v3_pos = compute_v3_momentum(daily)
    v3_ann_ret = v3_ret.mean() * 365
    v3_ann_vol = v3_ret.std() * ANNUALIZE
    v3_sharpe = v3_ann_ret / v3_ann_vol if v3_ann_vol > 0 else 0

    # --- Compute All Signals ---
    signals = compute_all_signals(btc_1h, daily)

    # --- IC Scan ---
    ic_df = compute_ic_scan(signals, daily)

    # --- Rolling IC Stationarity ---
    rolling_ic_df = compute_rolling_ic(signals, daily)

    # --- Identify top signals for deep analysis ---
    # Take all signals that have |IC| > 0.01 at any horizon
    ic_cols = [c for c in ic_df.columns if c.endswith('_ic')]
    top_signals = []
    for sig_name in ic_df.index:
        max_abs_ic = 0
        for col in ic_cols:
            ic_val = abs(ic_df.loc[sig_name, col])
            if not np.isnan(ic_val):
                max_abs_ic = max(max_abs_ic, ic_val)
        if max_abs_ic >= 0.01:
            top_signals.append(sig_name)
        else:
            print(f"\n[SKIP] {sig_name}: max |IC| = {max_abs_ic:.4f} < 0.01, skipping WF")

    # If too few signals pass, include all for completeness
    if len(top_signals) < 3:
        print("\n[NOTE] Few signals pass IC filter, running WF on all signals for completeness")
        top_signals = [c for c in signals.columns if c != 'S6_vol_regime']

    print(f"\n[TOP] Signals for deep analysis: {top_signals}")

    # --- Deep Analysis per Signal ---
    signal_results = {}
    for sig_name in top_signals:
        print(f"\n{'='*70}")
        print(f"DEEP ANALYSIS: {sig_name}")
        print(f"{'='*70}")

        # Walk-forward
        wf_windows, wf_agg = walk_forward_signal(sig_name, signals, daily)

        # Get full-period returns for correlation/portfolio analysis
        # Use default params for full-period
        full_pos = signal_to_position(signals[sig_name], sig_name, daily)
        full_result = backtest_signal(full_pos, daily)

        if full_result is None:
            signal_results[sig_name] = {
                'alive': False,
                'kill_reasons': ['KILL: No valid backtest result'],
                'wf_windows': None, 'wf_agg': None,
                'correlation': None, 'portfolio': None, 'regime': None,
            }
            continue

        full_strat_ret = full_result.get('strat_ret', pd.Series(dtype=float))

        # Correlation with V3
        corr = compute_correlation_with_v3(full_strat_ret, v3_ret, sig_name)

        # Portfolio combination
        port = test_portfolio_combination(full_strat_ret, v3_ret, sig_name)

        # Regime analysis
        regime = regime_analysis(full_strat_ret, v3_ret, daily, sig_name)

        # Kill criteria
        alive, kill_reasons = evaluate_kill_criteria(ic_df, wf_agg, corr, sig_name, port)

        signal_results[sig_name] = {
            'alive': alive,
            'kill_reasons': kill_reasons,
            'wf_windows': wf_windows,
            'wf_agg': wf_agg,
            'correlation': corr,
            'portfolio': port,
            'regime': regime,
            'full_metrics': full_result,
        }

    # --- Generate Report ---
    report = generate_report(ic_df, rolling_ic_df, signal_results, v3_sharpe)

    # Write report
    with open(OUTPUT_MD, 'w') as f:
        f.write(report)
    print(f"\n[OUTPUT] Report written to {OUTPUT_MD}")

    # --- Final Summary ---
    print("\n" + "=" * 80)
    print("FINAL SUMMARY")
    print("=" * 80)
    alive = [k for k, v in signal_results.items() if v.get('alive')]
    killed = [k for k, v in signal_results.items() if not v.get('alive')]
    print(f"  Alive: {alive if alive else 'None'}")
    print(f"  Killed: {killed}")
    if alive:
        best = max(alive, key=lambda s: signal_results[s].get('wf_agg', {}).get('mean_oos_sharpe', -999))
        wf = signal_results[best].get('wf_agg', {})
        corr = signal_results[best].get('correlation', {})
        print(f"  Best: {best}")
        print(f"    WF Sharpe: {wf.get('mean_oos_sharpe', 0):.3f}")
        print(f"    V3 Corr: {corr.get('full_corr', 0):.3f}")
    else:
        print("  ALL SIGNALS KILLED -- vol structure provides no diversification edge")

    return signal_results


if __name__ == '__main__':
    results = main()
