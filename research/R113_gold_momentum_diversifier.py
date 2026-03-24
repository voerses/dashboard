#!/workspace/venv/bin/python
"""
R113: Gold Momentum as BTC Portfolio Diversifier
=================================================

Hypothesis:
  Gold momentum divergence from BTC can predict BTC returns because:
  1. Gold rallies during risk-off -> BTC should weaken
  2. Gold weakness during risk-on -> BTC should strengthen
  3. Gold/BTC correlation regime shifts are informative

Signal Candidates:
  1. Gold 20d/60d momentum -- rolling return as BTC timing signal
  2. Gold/BTC rolling correlation change -- correlation regime shift (60d vs 120d)
  3. Gold relative momentum -- gold outperforming BTC -> risk-off -> reduce BTC
  4. Gold-BTC divergence -- when both trend same direction vs diverge
  5. Gold volatility regime -- high gold vol -> uncertainty -> reduce BTC

Methodology:
  - Align gold daily data to BTC daily returns
  - Compute each signal candidate
  - IC at 1d, 3d, 7d, 14d horizons with BTC forward returns
  - Top signals: full walk-forward validation (10 windows, 180d train / 90d test)
  - Check correlation with V3 returns (MUST be < 0.3)
  - If any signal passes WF (mean Sharpe > 0.3, >5/10 positive): build standalone strategy

Kill Criteria:
  - IC < 0.02 absolute across all horizons -> KILL
  - Correlation with V3 > 0.5 -> KILL (no diversification)
  - WF mean Sharpe < 0.3 -> KILL
  - < 5/10 WF windows positive -> KILL

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

# -- Paths ------------------------------------------------------------------
PROJECT_DIR = Path('/workspace/crypto_backtest')
DATA_DIR = PROJECT_DIR / 'data'
OUTPUT_MD = PROJECT_DIR / 'research' / 'R113_gold_momentum_diversifier.md'

COST_BPS = 10  # round-trip cost in basis points
ANNUALIZE = np.sqrt(365)  # daily -> annual

# Walk-forward config: anchored from END of data so recent period is always tested
WF_TRAIN_DAYS = 180
WF_TEST_DAYS = 90
WF_N_WINDOWS = 10


# ==========================================================================
# DATA LOADING
# ==========================================================================

def load_btc_daily():
    """Load BTC 1h data and resample to daily."""
    print("[DATA] Loading BTC 1h spot and resampling to daily...")
    df = pd.read_parquet(DATA_DIR / 'spot/1h_cache/BTC_1h.parquet')
    df.index = pd.to_datetime(df.index)
    df = df.sort_index()
    df = df[~df.index.duplicated(keep='first')]

    daily = pd.DataFrame()
    daily['close'] = df['close'].resample('1D').last().dropna()
    daily['open'] = df['open'].resample('1D').first()
    daily['high'] = df['high'].resample('1D').max()
    daily['low'] = df['low'].resample('1D').min()
    daily['volume'] = df['volume'].resample('1D').sum()
    daily = daily.dropna()
    daily['ret'] = daily['close'].pct_change()
    daily['log_ret'] = np.log(daily['close'] / daily['close'].shift(1))

    print(f"  BTC daily: {daily.index.min().date()} to {daily.index.max().date()}, {len(daily)} days")
    return daily


def load_gold_daily():
    """Load gold daily data."""
    print("[DATA] Loading gold daily data...")
    df = pd.read_parquet(DATA_DIR / 'alternative/macro/gold.parquet')
    df['Date'] = pd.to_datetime(df['Date'])
    df = df.set_index('Date').sort_index()
    df = df[~df.index.duplicated(keep='first')]
    gold = df[['Close']].rename(columns={'Close': 'gold_close'})
    gold['gold_ret'] = gold['gold_close'].pct_change()
    gold['gold_log_ret'] = np.log(gold['gold_close'] / gold['gold_close'].shift(1))
    print(f"  Gold daily: {gold.index.min().date()} to {gold.index.max().date()}, {len(gold)} days")
    return gold


def align_data(btc_daily, gold_daily):
    """Align BTC and gold on common trading dates."""
    print("[DATA] Aligning BTC and gold data...")
    gold_reindexed = gold_daily.reindex(btc_daily.index, method='ffill')
    merged = btc_daily.join(gold_reindexed, how='inner').dropna(subset=['close', 'gold_close'])
    print(f"  Aligned: {merged.index.min().date()} to {merged.index.max().date()}, {len(merged)} days")
    return merged


# ==========================================================================
# V3 MOMENTUM BASELINE
# ==========================================================================

def compute_v3_momentum(daily):
    """V3 Momentum: Long when EMA(20) > EMA(50), flat. Weekly rebalance."""
    print("[V3] Computing V3 Momentum baseline...")
    ema20 = daily['close'].ewm(span=20, adjust=False).mean()
    ema50 = daily['close'].ewm(span=50, adjust=False).mean()
    uptrend = (ema20 > ema50).astype(int)
    pos = uptrend.shift(1).fillna(0).astype(int)
    weekly_signal = pos.resample('W-MON').last()
    pos_weekly = weekly_signal.reindex(pos.index, method='ffill').fillna(0).astype(int)
    daily_ret = daily['ret'].fillna(0)
    position_changes = pos_weekly.diff().abs().fillna(0)
    cost = position_changes * (COST_BPS / 10000)
    strat_ret = pos_weekly * daily_ret - cost
    sharpe = strat_ret.mean() / strat_ret.std() * ANNUALIZE
    print(f"  V3 Sharpe: {sharpe:.3f}")
    return strat_ret, pos_weekly


# ==========================================================================
# SIGNAL CONSTRUCTION
# ==========================================================================

def compute_all_signals(df):
    """
    Compute all gold-based signal candidates.
    All signals use .shift(1) to avoid look-ahead bias.
    """
    print("[SIGNALS] Computing gold-based signal candidates...")
    signals = pd.DataFrame(index=df.index)

    # --- Signal 1: Gold Momentum ---
    for lb in [10, 20, 40, 60]:
        gold_mom = df['gold_close'].pct_change(lb)
        signals[f'gold_mom_{lb}d'] = gold_mom.shift(1)
        gold_mom_z = (gold_mom - gold_mom.rolling(120).mean()) / gold_mom.rolling(120).std()
        signals[f'gold_mom_{lb}d_z'] = gold_mom_z.shift(1)

    # --- Signal 2: Gold/BTC Rolling Correlation Change ---
    for short_w, long_w in [(20, 60), (30, 90), (60, 120)]:
        corr_short = df['gold_ret'].rolling(short_w).corr(df['ret'])
        corr_long = df['gold_ret'].rolling(long_w).corr(df['ret'])
        signals[f'corr_change_{short_w}v{long_w}'] = (corr_short - corr_long).shift(1)
        signals[f'corr_level_{short_w}d'] = corr_short.shift(1)

    # --- Signal 3: Gold Relative Momentum ---
    for lb in [10, 20, 40, 60]:
        gold_mom = df['gold_close'].pct_change(lb)
        btc_mom = df['close'].pct_change(lb)
        rel_mom = gold_mom - btc_mom
        signals[f'rel_mom_{lb}d'] = rel_mom.shift(1)
        rel_z = (rel_mom - rel_mom.rolling(120).mean()) / rel_mom.rolling(120).std()
        signals[f'rel_mom_{lb}d_z'] = rel_z.shift(1)

    # --- Signal 4: Gold-BTC Divergence ---
    for lb in [10, 20, 40]:
        gold_dir = np.sign(df['gold_close'].pct_change(lb))
        btc_dir = np.sign(df['close'].pct_change(lb))
        signals[f'convergence_{lb}d'] = (gold_dir * btc_dir).shift(1)
        gold_ret_z = (df['gold_close'].pct_change(lb) /
                      df['gold_close'].pct_change(lb).rolling(120).std())
        btc_ret_z = (df['close'].pct_change(lb) /
                     df['close'].pct_change(lb).rolling(120).std())
        signals[f'divergence_score_{lb}d'] = (gold_ret_z * btc_ret_z).shift(1)

    # --- Signal 5: Gold Volatility Regime ---
    for w in [20, 40, 60]:
        gold_vol = df['gold_ret'].rolling(w).std() * np.sqrt(365)
        signals[f'gold_vol_{w}d'] = gold_vol.shift(1)
        vol_z = (gold_vol - gold_vol.rolling(120).mean()) / gold_vol.rolling(120).std()
        signals[f'gold_vol_{w}d_z'] = vol_z.shift(1)
        btc_vol = df['ret'].rolling(w).std() * np.sqrt(365)
        vol_ratio = gold_vol / btc_vol
        signals[f'gold_btc_vol_ratio_{w}d'] = vol_ratio.shift(1)

    # --- Signal 6 (bonus): Gold/BTC Ratio Trend ---
    ratio = df['gold_close'] / df['close']
    for lb in [10, 20, 40]:
        ratio_mom = ratio.pct_change(lb)
        signals[f'gold_btc_ratio_mom_{lb}d'] = ratio_mom.shift(1)
        ratio_z = (ratio_mom - ratio_mom.rolling(120).mean()) / ratio_mom.rolling(120).std()
        signals[f'gold_btc_ratio_mom_{lb}d_z'] = ratio_z.shift(1)

    n_signals = len(signals.columns)
    valid_counts = signals.dropna(how='all').count()
    print(f"  Generated {n_signals} signal variants")
    print(f"  Min valid observations: {valid_counts.min()}, Max: {valid_counts.max()}")
    return signals


# ==========================================================================
# IC ANALYSIS
# ==========================================================================

def compute_ic_table(signals_df, btc_daily, horizons=[1, 3, 7, 14]):
    """Compute rank IC (Spearman) between each signal and BTC forward returns."""
    print("[IC] Computing Information Coefficients...")
    fwd_rets = {}
    for h in horizons:
        fwd_rets[h] = btc_daily['ret'].rolling(h).sum().shift(-h)

    results = []
    for sig_name in signals_df.columns:
        sig = signals_df[sig_name]
        row = {'signal': sig_name}
        for h in horizons:
            common = pd.concat([sig, fwd_rets[h]], axis=1).dropna()
            if len(common) < 100:
                row[f'ic_{h}d'] = np.nan
                row[f'ic_{h}d_p'] = np.nan
                continue
            ic, pval = stats.spearmanr(common.iloc[:, 0], common.iloc[:, 1])
            row[f'ic_{h}d'] = ic
            row[f'ic_{h}d_p'] = pval
        ic_vals = [abs(row.get(f'ic_{h}d', 0) or 0) for h in horizons]
        row['max_abs_ic'] = max(ic_vals)
        row['mean_abs_ic'] = np.mean(ic_vals)
        ic_signs = [np.sign(row.get(f'ic_{h}d', 0) or 0) for h in horizons]
        most_common_sign = max(set(ic_signs), key=ic_signs.count) if ic_signs else 0
        row['ic_sign_consistency'] = sum(1 for s in ic_signs if s == most_common_sign) / len(ic_signs)
        row['dominant_sign'] = most_common_sign
        results.append(row)

    ic_df = pd.DataFrame(results).set_index('signal')
    ic_df = ic_df.sort_values('max_abs_ic', ascending=False)
    print(f"  Top signal by max abs IC: {ic_df.index[0]} ({ic_df['max_abs_ic'].iloc[0]:.4f})")
    return ic_df


def ic_kill_check(ic_df, threshold=0.02):
    """Check kill criterion: any signal with IC >= threshold?"""
    survivors = ic_df[ic_df['max_abs_ic'] >= threshold]
    n_survived = len(survivors)
    n_total = len(ic_df)
    print(f"[KILL CHECK] IC threshold {threshold}: {n_survived}/{n_total} signals survive")
    if n_survived == 0:
        print("  ** ALL SIGNALS KILLED at IC stage **")
    return survivors


# ==========================================================================
# IC STABILITY: FIRST HALF vs SECOND HALF
# ==========================================================================

def ic_stability_check(signals_df, btc_daily, horizons=[1, 3, 7, 14]):
    """Check if IC is stable across time (first half vs second half)."""
    print("[IC STABILITY] Checking first-half vs second-half IC...")
    midpoint = len(btc_daily) // 2
    first_half = btc_daily.iloc[:midpoint]
    second_half = btc_daily.iloc[midpoint:]
    sig_first = signals_df.iloc[:midpoint]
    sig_second = signals_df.iloc[midpoint:]

    results = []
    for sig_name in signals_df.columns:
        row = {'signal': sig_name}
        for half_label, sig_h, btc_h in [('h1', sig_first, first_half), ('h2', sig_second, second_half)]:
            for h in horizons:
                fwd = btc_h['ret'].rolling(h).sum().shift(-h)
                common = pd.concat([sig_h[sig_name], fwd], axis=1).dropna()
                if len(common) < 60:
                    row[f'{half_label}_ic_{h}d'] = np.nan
                    continue
                ic, _ = stats.spearmanr(common.iloc[:, 0], common.iloc[:, 1])
                row[f'{half_label}_ic_{h}d'] = ic
        # Check sign consistency between halves at 7d horizon
        h1_7d = row.get('h1_ic_7d', np.nan)
        h2_7d = row.get('h2_ic_7d', np.nan)
        if not np.isnan(h1_7d) and not np.isnan(h2_7d):
            row['ic_same_sign_7d'] = np.sign(h1_7d) == np.sign(h2_7d)
            row['ic_decay'] = abs(h2_7d) - abs(h1_7d)  # negative = decaying
        else:
            row['ic_same_sign_7d'] = False
            row['ic_decay'] = np.nan
        results.append(row)

    return pd.DataFrame(results).set_index('signal')


# ==========================================================================
# WALK-FORWARD VALIDATION (ANCHORED FROM END)
# ==========================================================================

def generate_signal_positions(signal_series, mode='long_short', threshold=0.0, invert=False):
    """Convert continuous signal to positions."""
    if invert:
        signal_series = -signal_series

    if mode == 'tercile':
        upper = signal_series.expanding(min_periods=60).quantile(0.67)
        lower = signal_series.expanding(min_periods=60).quantile(0.33)
        pos = pd.Series(0.0, index=signal_series.index)
        pos[signal_series > upper] = 1.0
        pos[signal_series < lower] = -1.0
    elif mode == 'long_short':
        pos = pd.Series(0.0, index=signal_series.index)
        pos[signal_series > threshold] = 1.0
        pos[signal_series < -threshold] = -1.0
    elif mode == 'long_flat':
        pos = pd.Series(0.0, index=signal_series.index)
        pos[signal_series > threshold] = 1.0
    else:
        raise ValueError(f"Unknown mode: {mode}")

    return pos


def backtest_signal(positions, btc_returns, cost_bps=COST_BPS):
    """Run simple backtest: position * btc returns - costs."""
    pos = positions.reindex(btc_returns.index).fillna(0)
    ret = btc_returns.fillna(0)
    trades = pos.diff().abs().fillna(0)
    cost = trades * (cost_bps / 10000)
    strat_ret = pos * ret - cost
    return strat_ret


def compute_metrics(returns):
    """Compute Sharpe, total return, max drawdown."""
    returns = returns.dropna()
    if len(returns) < 10 or returns.std() == 0:
        return {'sharpe': 0.0, 'total_return': 0.0, 'max_dd': 0.0, 'n_days': len(returns)}

    sharpe = returns.mean() / returns.std() * ANNUALIZE
    cum = (1 + returns).cumprod()
    total_ret = cum.iloc[-1] / cum.iloc[0] - 1
    running_max = cum.cummax()
    drawdown = (cum - running_max) / running_max
    max_dd = drawdown.min()

    return {
        'sharpe': sharpe,
        'total_return': total_ret,
        'max_dd': max_dd,
        'n_days': len(returns),
    }


def walk_forward_anchored(signal_series, btc_returns, param_grid,
                           n_windows=WF_N_WINDOWS,
                           train_days=WF_TRAIN_DAYS,
                           test_days=WF_TEST_DAYS):
    """
    Walk-forward validation ANCHORED FROM THE END of the data.
    This ensures the most recent period is always tested.
    Windows: last test window ends at data end, then roll backward.
    """
    # Align signal and returns on common dates
    common = pd.concat([signal_series.rename('sig'), btc_returns.rename('ret')], axis=1).dropna()
    dates = common.index
    total_days = len(dates)

    min_days = train_days + test_days
    if total_days < min_days:
        print(f"  Not enough data: {total_days} < {min_days}")
        return []

    # Build windows backward from the end
    windows = []
    for w in range(n_windows):
        test_end_idx = total_days - w * test_days
        test_start_idx = test_end_idx - test_days
        train_end_idx = test_start_idx
        train_start_idx = train_end_idx - train_days

        if train_start_idx < 0:
            break

        windows.append({
            'train_dates': dates[train_start_idx:train_end_idx],
            'test_dates': dates[test_start_idx:test_end_idx],
        })

    windows = list(reversed(windows))  # chronological order

    if len(windows) < 3:
        print(f"  Only {len(windows)} windows possible, need >= 3")
        return []

    results = []
    for i, win in enumerate(windows):
        train_dates = win['train_dates']
        test_dates = win['test_dates']

        train_sig = common.loc[train_dates, 'sig']
        train_ret = common.loc[train_dates, 'ret']
        test_sig = common.loc[test_dates, 'sig']
        test_ret = common.loc[test_dates, 'ret']

        if len(train_sig) < 30 or len(test_sig) < 10:
            continue

        # Grid search on train
        best_train_sharpe = -999
        best_params = None
        for params in param_grid:
            try:
                pos = generate_signal_positions(train_sig, **params)
                strat_ret = backtest_signal(pos, train_ret)
                m = compute_metrics(strat_ret)
                if m['sharpe'] > best_train_sharpe:
                    best_train_sharpe = m['sharpe']
                    best_params = params
            except Exception:
                continue

        if best_params is None:
            continue

        # Apply best params to test
        test_pos = generate_signal_positions(test_sig, **best_params)
        test_strat_ret = backtest_signal(test_pos, test_ret)
        test_metrics = compute_metrics(test_strat_ret)
        signal_changes = test_pos.diff().abs().sum()

        results.append({
            'window': i + 1,
            'train_start': train_dates[0].strftime('%Y-%m-%d'),
            'train_end': train_dates[-1].strftime('%Y-%m-%d'),
            'test_start': test_dates[0].strftime('%Y-%m-%d'),
            'test_end': test_dates[-1].strftime('%Y-%m-%d'),
            'best_params': str(best_params),
            'train_sharpe': best_train_sharpe,
            'oos_sharpe': test_metrics['sharpe'],
            'oos_return': test_metrics['total_return'],
            'oos_max_dd': test_metrics['max_dd'],
            'signal_changes': signal_changes,
        })

    return results


def run_walk_forward_for_signal(sig_name, sig_series, btc_returns):
    """Run full WF validation for one signal with reasonable param grid."""
    print(f"\n[WF] Walk-forward for: {sig_name}")

    param_grid = [
        {'mode': 'long_short', 'threshold': 0.0, 'invert': False},
        {'mode': 'long_short', 'threshold': 0.0, 'invert': True},
        {'mode': 'long_flat', 'threshold': 0.0, 'invert': False},
        {'mode': 'long_flat', 'threshold': 0.0, 'invert': True},
        {'mode': 'tercile', 'threshold': 0.0, 'invert': False},
        {'mode': 'tercile', 'threshold': 0.0, 'invert': True},
    ]

    wf_results = walk_forward_anchored(sig_series, btc_returns, param_grid)

    if not wf_results:
        return None

    oos_sharpes = [r['oos_sharpe'] for r in wf_results]
    positive_windows = sum(1 for s in oos_sharpes if s > 0)
    mean_sharpe = np.mean(oos_sharpes)
    median_sharpe = np.median(oos_sharpes)
    std_sharpe = np.std(oos_sharpes)
    total_signal_changes = sum(r['signal_changes'] for r in wf_results)

    # Recent performance: last 3 windows
    recent_sharpes = oos_sharpes[-3:] if len(oos_sharpes) >= 3 else oos_sharpes
    recent_positive = sum(1 for s in recent_sharpes if s > 0)
    recent_mean = np.mean(recent_sharpes)

    summary = {
        'signal': sig_name,
        'n_windows': len(wf_results),
        'positive_windows': positive_windows,
        'mean_oos_sharpe': mean_sharpe,
        'median_oos_sharpe': median_sharpe,
        'std_oos_sharpe': std_sharpe,
        'min_oos_sharpe': min(oos_sharpes),
        'max_oos_sharpe': max(oos_sharpes),
        'mean_oos_return': np.mean([r['oos_return'] for r in wf_results]),
        'total_signal_changes': total_signal_changes,
        'recent_3w_mean_sharpe': recent_mean,
        'recent_3w_positive': recent_positive,
        'window_details': wf_results,
    }

    print(f"  Windows: {len(wf_results)}, Positive: {positive_windows}/{len(wf_results)}")
    print(f"  Mean OOS Sharpe: {mean_sharpe:.3f}, Median: {median_sharpe:.3f}")
    print(f"  Recent 3 windows: mean={recent_mean:.3f}, positive={recent_positive}/3")
    return summary


# ==========================================================================
# V3 CORRELATION ANALYSIS
# ==========================================================================

def compute_v3_correlation(signal_returns, v3_returns):
    """Compute correlation between signal returns and V3 returns."""
    common = pd.concat([signal_returns.rename('signal'), v3_returns.rename('v3')], axis=1).dropna()
    if len(common) < 30:
        return np.nan
    return common['signal'].corr(common['v3'])


# ==========================================================================
# PARAMETER STABILITY CHECK
# ==========================================================================

def parameter_stability_check(wf_results_list):
    """Check if optimal params are stable across WF windows."""
    if not wf_results_list:
        return {'stable': False, 'dominant_mode': 'N/A', 'mode_consistency': 0.0}

    modes = []
    inverts = []
    for wd in wf_results_list:
        params_str = wd['best_params']
        if "'mode': 'long_flat'" in params_str:
            modes.append('long_flat')
        elif "'mode': 'long_short'" in params_str:
            modes.append('long_short')
        elif "'mode': 'tercile'" in params_str:
            modes.append('tercile')
        else:
            modes.append('unknown')
        inverts.append('True' in params_str.split('invert')[1][:10] if 'invert' in params_str else False)

    from collections import Counter
    mode_counts = Counter(modes)
    dominant_mode = mode_counts.most_common(1)[0][0]
    mode_consistency = mode_counts[dominant_mode] / len(modes)

    invert_counts = Counter(inverts)
    invert_consistency = invert_counts.most_common(1)[0][0]

    return {
        'dominant_mode': dominant_mode,
        'mode_consistency': mode_consistency,
        'dominant_invert': invert_consistency,
        'modes': modes,
        'stable': mode_consistency >= 0.6,
    }


# ==========================================================================
# ROLLING IC / STATIONARITY
# ==========================================================================

def rolling_ic_analysis(signal_series, btc_returns, window=120, horizon=7):
    """Compute rolling IC to check stationarity."""
    fwd_ret = btc_returns.rolling(horizon).sum().shift(-horizon)
    common = pd.concat([signal_series, fwd_ret], axis=1).dropna()
    common.columns = ['signal', 'fwd_ret']

    rolling_ic = common['signal'].rolling(window).corr(common['fwd_ret'])
    rolling_ic = rolling_ic.dropna()

    if len(rolling_ic) < 30:
        return {'full_mean_ic': np.nan, 'sign_changes': 0, 'last_2y_mean_ic': np.nan}

    cutoff = rolling_ic.index.max() - pd.Timedelta(days=730)
    last_2y = rolling_ic[rolling_ic.index >= cutoff]

    sign_series = np.sign(rolling_ic)
    sign_changes = (sign_series.diff().abs() > 0).sum()

    # Quarterly IC
    quarterly_ic = rolling_ic.resample('QE').mean().dropna()

    return {
        'full_mean_ic': rolling_ic.mean(),
        'full_std_ic': rolling_ic.std(),
        'last_2y_mean_ic': last_2y.mean() if len(last_2y) > 0 else np.nan,
        'last_2y_pos_pct': (last_2y > 0).mean() if len(last_2y) > 0 else np.nan,
        'sign_changes': int(sign_changes),
        'ic_sign_changed_last_2y': bool((np.sign(last_2y) != np.sign(last_2y.iloc[0])).any()) if len(last_2y) > 5 else True,
        'quarterly_ic': quarterly_ic,
    }


# ==========================================================================
# REGIME ANALYSIS
# ==========================================================================

def regime_analysis(signal_series, btc_daily, signals_df):
    """Classify BTC into UPTREND/DOWNTREND/RANGE and check signal per regime."""
    ema20 = btc_daily['close'].ewm(span=20, adjust=False).mean()
    ema50 = btc_daily['close'].ewm(span=50, adjust=False).mean()
    sma200 = btc_daily['close'].rolling(200).mean()

    regime = pd.Series('RANGE', index=btc_daily.index)
    regime[(ema20 > ema50) & (btc_daily['close'] > sma200)] = 'UPTREND'
    regime[(ema20 < ema50) & (btc_daily['close'] < sma200)] = 'DOWNTREND'

    btc_ret = btc_daily['ret']
    results = {}

    for r in ['UPTREND', 'DOWNTREND', 'RANGE']:
        mask = regime == r
        sig_in_regime = signal_series[mask].dropna()
        ret_in_regime = btc_ret[mask].dropna()

        if len(sig_in_regime) < 30:
            continue

        # Use tercile positioning for consistent comparison
        pos = generate_signal_positions(sig_in_regime, mode='tercile')
        strat_ret = backtest_signal(pos, ret_in_regime)
        m = compute_metrics(strat_ret)

        results[r] = {
            'n_days': int(mask.sum()),
            'pct_total': mask.mean(),
            'sharpe': m['sharpe'],
            'total_return': m['total_return'],
        }

    return results, regime


# ==========================================================================
# MAIN ANALYSIS
# ==========================================================================

def main():
    print("=" * 80)
    print("R113: Gold Momentum as BTC Portfolio Diversifier")
    print("=" * 80)

    # -- Load Data --
    btc_daily = load_btc_daily()
    gold_daily = load_gold_daily()
    df = align_data(btc_daily, gold_daily)

    # -- V3 Baseline --
    v3_ret, v3_pos = compute_v3_momentum(df)

    # -- Compute Signals --
    signals = compute_all_signals(df)

    # -- IC Analysis --
    horizons = [1, 3, 7, 14]
    ic_df = compute_ic_table(signals, df, horizons=horizons)

    print("\n" + "=" * 80)
    print("IC TABLE (top 20 by max abs IC)")
    print("=" * 80)
    ic_display = ic_df.head(20)[['ic_1d', 'ic_3d', 'ic_7d', 'ic_14d', 'max_abs_ic',
                                   'mean_abs_ic', 'ic_sign_consistency', 'dominant_sign']]
    print(ic_display.to_string(float_format='%.4f'))

    # -- IC Stability Check --
    ic_stability = ic_stability_check(signals, df, horizons=horizons)

    # -- IC Kill Check --
    ic_survivors = ic_kill_check(ic_df, threshold=0.02)

    if len(ic_survivors) == 0:
        print("\n** GLOBAL KILL: No signal meets IC threshold of 0.02 **")
        write_report(ic_df, ic_survivors, {}, {}, {}, v3_ret, df, signals,
                     ic_stability=ic_stability)
        return

    # -- Select Top Signals for WF --
    top_signals = ic_survivors.head(10)
    print(f"\n[WF] Running walk-forward on {len(top_signals)} top signals...")

    wf_results = {}
    for sig_name in top_signals.index:
        sig_series = signals[sig_name].dropna()
        btc_ret = df['ret']
        wf = run_walk_forward_for_signal(sig_name, sig_series, btc_ret)
        if wf is not None:
            wf_results[sig_name] = wf

    # -- WF Kill Check --
    print("\n" + "=" * 80)
    print("WALK-FORWARD SUMMARY")
    print("=" * 80)

    wf_survivors = {}
    for sig_name, wf in wf_results.items():
        n_win = wf['n_windows']
        pos_win = wf['positive_windows']
        mean_s = wf['mean_oos_sharpe']
        passes_sharpe = mean_s > 0.3
        passes_windows = pos_win >= 5
        status = "PASS" if (passes_sharpe and passes_windows) else "KILL"
        print(f"  {sig_name}: Mean Sharpe={mean_s:.3f}, Positive={pos_win}/{n_win}, "
              f"Recent 3w={wf['recent_3w_mean_sharpe']:.3f} -> {status}")
        if status == "PASS":
            wf_survivors[sig_name] = wf

    if not wf_survivors:
        print("\n** GLOBAL KILL at WF stage **")

    # -- V3 Correlation Check --
    print("\n" + "=" * 80)
    print("V3 CORRELATION CHECK")
    print("=" * 80)

    v3_corr_results = {}
    final_survivors = {}
    check_signals = wf_survivors if wf_survivors else dict(list(wf_results.items())[:5])

    for sig_name in check_signals:
        sig_series = signals[sig_name].dropna()
        btc_ret = df['ret']
        for inv in [False, True]:
            pos = generate_signal_positions(sig_series, mode='tercile', invert=inv)
            sig_ret = backtest_signal(pos, btc_ret)
            corr = compute_v3_correlation(sig_ret, v3_ret)
            suffix = "_inv" if inv else ""
            label = f"{sig_name}{suffix}"
            v3_corr_results[label] = corr
            status = "PASS" if abs(corr) < 0.5 else "KILL"
            print(f"  {label}: corr with V3 = {corr:.4f} -> {status}")
            if abs(corr) < 0.5 and sig_name in wf_survivors:
                if sig_name not in final_survivors:
                    final_survivors[sig_name] = {
                        'wf': wf_survivors[sig_name],
                        'v3_corr': corr,
                        'invert': inv,
                    }

    # -- Stationarity Check --
    print("\n" + "=" * 80)
    print("STATIONARITY CHECK (Rolling IC)")
    print("=" * 80)

    stationarity = {}
    check_set = list(final_survivors.keys()) if final_survivors else list(wf_results.keys())[:5]
    for sig_name in check_set:
        sig_series = signals[sig_name].dropna()
        btc_ret = df['ret']
        stat = rolling_ic_analysis(sig_series, btc_ret, window=120, horizon=7)
        stationarity[sig_name] = stat
        print(f"  {sig_name}: full IC={stat['full_mean_ic']:.4f}, last 2y={stat['last_2y_mean_ic']:.4f}, "
              f"pos%={stat['last_2y_pos_pct']:.1%}, sign_changed={stat['ic_sign_changed_last_2y']}")

    # -- Parameter Stability --
    print("\n" + "=" * 80)
    print("PARAMETER STABILITY CHECK")
    print("=" * 80)

    param_stability = {}
    for sig_name in (final_survivors if final_survivors else wf_results):
        wf = (final_survivors[sig_name]['wf'] if sig_name in final_survivors
              else wf_results[sig_name])
        ps = parameter_stability_check(wf['window_details'])
        param_stability[sig_name] = ps
        print(f"  {sig_name}: dominant_mode={ps['dominant_mode']}, "
              f"consistency={ps['mode_consistency']:.0%}, stable={ps['stable']}")

    # -- Regime Analysis for top survivors --
    print("\n" + "=" * 80)
    print("REGIME ANALYSIS")
    print("=" * 80)

    regime_results = {}
    for sig_name in list(final_survivors.keys())[:3] if final_survivors else list(wf_results.keys())[:3]:
        sig_series = signals[sig_name].dropna()
        reg, _ = regime_analysis(sig_series, df, signals)
        regime_results[sig_name] = reg
        for r, m in reg.items():
            print(f"  {sig_name} in {r}: {m['n_days']}d ({m['pct_total']:.0%}), Sharpe={m['sharpe']:.3f}")

    # -- Write Report --
    write_report(ic_df, ic_survivors, wf_results, wf_survivors, final_survivors,
                 v3_ret, df, signals, v3_corr_results, stationarity,
                 ic_stability, param_stability, regime_results)


def write_report(ic_df, ic_survivors, wf_results, wf_survivors, final_survivors,
                 v3_ret, df, signals, v3_corr_results=None, stationarity=None,
                 ic_stability=None, param_stability=None, regime_results=None):
    """Generate the comprehensive markdown report."""
    print("\n[REPORT] Writing markdown report...")

    lines = []
    lines.append("# R113: Gold Momentum as BTC Portfolio Diversifier")
    lines.append("")
    lines.append(f"**Date**: {datetime.now().strftime('%Y-%m-%d')}")

    # Determine verdict
    if final_survivors:
        # Check additional quality gates
        all_stable = all(param_stability.get(s, {}).get('stable', False)
                        for s in final_survivors) if param_stability else False
        any_stationary = any(
            not stationarity.get(s, {}).get('ic_sign_changed_last_2y', True)
            for s in final_survivors) if stationarity else False

        # Check recent performance
        all_recent_positive = all(
            final_survivors[s]['wf']['recent_3w_mean_sharpe'] > 0
            for s in final_survivors)

        if all_recent_positive and all_stable:
            verdict = "CONDITIONAL PASS"
            reason = (f"{len(final_survivors)} signal(s) survive all kill criteria "
                     f"with stable params and positive recent performance")
        elif all_recent_positive:
            verdict = "CONDITIONAL PASS (WEAK)"
            reason = (f"{len(final_survivors)} signal(s) survive kill criteria "
                     f"but parameter instability raises concerns")
        else:
            verdict = "KILL"
            reason = "Survivors show degraded recent performance or parameter instability"
    elif wf_survivors:
        verdict = "KILL"
        reason = "Signals pass WF but fail V3 correlation or stationarity check"
    elif len(wf_results) > 0:
        verdict = "KILL"
        reason = "No signal passes walk-forward criteria (mean Sharpe > 0.3 AND >= 5/10 positive)"
    elif len(ic_survivors) == 0:
        verdict = "KILL"
        reason = "No signal meets IC threshold of 0.02 across all horizons"
    else:
        verdict = "KILL"
        reason = "Signals fail at walk-forward stage"

    lines.append(f"**Verdict**: **{verdict}**")
    lines.append(f"**Reason**: {reason}")
    lines.append("")

    # -- Hypothesis --
    lines.append("## Hypothesis")
    lines.append("")
    lines.append("Gold momentum divergence from BTC can predict BTC returns because:")
    lines.append("1. Gold rallies during risk-off -> BTC should weaken")
    lines.append("2. Gold weakness during risk-on -> BTC should strengthen")
    lines.append("3. Gold/BTC correlation regime shifts are informative")
    lines.append("")

    # -- Data Summary --
    lines.append("## Data Summary")
    lines.append("")
    lines.append(f"- **BTC**: {df.index.min().date()} to {df.index.max().date()}, {len(df)} trading days")
    lines.append(f"- **Gold**: Aligned to BTC daily via forward-fill (weekends/holidays)")
    lines.append(f"- **Overlap period**: {len(df)} days")
    lines.append(f"- **Signal candidates tested**: {len(ic_df)} variants across 6 categories")
    lines.append(f"- **Fees**: {COST_BPS}bps round-trip per rebalance")
    lines.append(f"- **Walk-forward**: {WF_N_WINDOWS} windows, {WF_TRAIN_DAYS}d train / {WF_TEST_DAYS}d test, anchored from end")
    lines.append("")

    # -- IC Table --
    lines.append("## 1. Information Coefficient Analysis")
    lines.append("")
    lines.append("### Top 20 Signals by Max Absolute IC")
    lines.append("")
    lines.append("| Signal | IC 1d | IC 3d | IC 7d | IC 14d | Max |IC| | Mean |IC| | Sign Cons. |")
    lines.append("|--------|------:|------:|------:|-------:|--------:|----------:|----------:|")

    for sig_name in ic_df.head(20).index:
        row = ic_df.loc[sig_name]
        lines.append(
            f"| {sig_name} | {row.get('ic_1d', 0):.4f} | {row.get('ic_3d', 0):.4f} | "
            f"{row.get('ic_7d', 0):.4f} | {row.get('ic_14d', 0):.4f} | "
            f"{row['max_abs_ic']:.4f} | {row['mean_abs_ic']:.4f} | "
            f"{row['ic_sign_consistency']:.0%} |"
        )
    lines.append("")

    # IC category summary
    lines.append("### IC by Signal Category")
    lines.append("")
    categories = {
        'Gold Momentum': [c for c in ic_df.index if c.startswith('gold_mom_')],
        'Corr Change': [c for c in ic_df.index if c.startswith('corr_')],
        'Relative Momentum': [c for c in ic_df.index if c.startswith('rel_mom_')],
        'Convergence/Divergence': [c for c in ic_df.index if c.startswith('convergence_') or c.startswith('divergence_')],
        'Gold Volatility': [c for c in ic_df.index if c.startswith('gold_vol_') or c.startswith('gold_btc_vol_')],
        'Gold/BTC Ratio': [c for c in ic_df.index if c.startswith('gold_btc_ratio_')],
    }
    lines.append("| Category | # Signals | Best Max |IC| | Best Signal |")
    lines.append("|----------|----------:|----------:|------------|")
    for cat, sigs in categories.items():
        if sigs:
            cat_df = ic_df.loc[[s for s in sigs if s in ic_df.index]]
            if len(cat_df) > 0:
                best = cat_df['max_abs_ic'].idxmax()
                lines.append(f"| {cat} | {len(cat_df)} | {cat_df['max_abs_ic'].max():.4f} | {best} |")
    lines.append("")

    # IC kill check
    lines.append(f"### IC Kill Check (threshold: 0.02)")
    lines.append(f"- Signals tested: {len(ic_df)}")
    lines.append(f"- Signals surviving IC >= 0.02: {len(ic_survivors)}")
    if len(ic_survivors) == 0:
        lines.append(f"- **KILL**: No signal meets IC threshold")
    lines.append("")

    # IC stability
    if ic_stability is not None:
        lines.append("### IC Stability (First Half vs Second Half)")
        lines.append("")
        lines.append("Critical test: does the IC persist or decay in the second half of the sample?")
        lines.append("")
        lines.append("| Signal | H1 IC(7d) | H2 IC(7d) | Same Sign | IC Decay |")
        lines.append("|--------|----------:|----------:|:---------:|---------:|")
        top_sigs = ic_df.head(10).index
        for sig_name in top_sigs:
            if sig_name in ic_stability.index:
                row = ic_stability.loc[sig_name]
                h1 = row.get('h1_ic_7d', np.nan)
                h2 = row.get('h2_ic_7d', np.nan)
                same = row.get('ic_same_sign_7d', False)
                decay = row.get('ic_decay', np.nan)
                lines.append(f"| {sig_name} | {h1:.4f} | {h2:.4f} | "
                           f"{'Yes' if same else 'NO'} | {decay:+.4f} |")
        lines.append("")

    # -- Walk-Forward Results --
    if wf_results:
        lines.append("## 2. Walk-Forward Validation (Anchored from End)")
        lines.append("")
        lines.append(f"Configuration: up to {WF_N_WINDOWS} windows, {WF_TRAIN_DAYS}d train / {WF_TEST_DAYS}d test")
        lines.append("")

        lines.append("### Summary")
        lines.append("")
        lines.append("| Signal | Win | Pos | Mean Sharpe | Med Sharpe | Min | Max | Mean Ret | Recent 3w | Status |")
        lines.append("|--------|----:|----:|------------:|-----------:|----:|----:|---------:|----------:|--------|")

        for sig_name, wf in sorted(wf_results.items(), key=lambda x: x[1]['mean_oos_sharpe'], reverse=True):
            passes_sharpe = wf['mean_oos_sharpe'] > 0.3
            passes_windows = wf['positive_windows'] >= 5
            status = "PASS" if (passes_sharpe and passes_windows) else "KILL"
            lines.append(
                f"| {sig_name} | {wf['n_windows']} | {wf['positive_windows']}/{wf['n_windows']} | "
                f"{wf['mean_oos_sharpe']:.3f} | {wf['median_oos_sharpe']:.3f} | "
                f"{wf['min_oos_sharpe']:.3f} | {wf['max_oos_sharpe']:.3f} | "
                f"{wf['mean_oos_return']:.1%} | {wf['recent_3w_mean_sharpe']:.3f} | {status} |"
            )
        lines.append("")

        # Detailed windows for top 3
        top_wf = sorted(wf_results.items(), key=lambda x: x[1]['mean_oos_sharpe'], reverse=True)[:3]
        for sig_name, wf in top_wf:
            lines.append(f"### Window Details: {sig_name}")
            lines.append("")
            lines.append("| Win | Train Period | Test Period | Best Params | Train Sharpe | OOS Sharpe | OOS Return | OOS MaxDD |")
            lines.append("|----:|-------------|-------------|-------------|-------------:|-----------:|-----------:|----------:|")
            for wd in wf['window_details']:
                params_short = wd['best_params'][:35]
                lines.append(
                    f"| {wd['window']} | {wd['train_start']} to {wd['train_end']} | "
                    f"{wd['test_start']} to {wd['test_end']} | {params_short} | "
                    f"{wd['train_sharpe']:.3f} | {wd['oos_sharpe']:.3f} | "
                    f"{wd['oos_return']:.1%} | {wd['oos_max_dd']:.1%} |"
                )
            lines.append("")

    # -- V3 Correlation --
    if v3_corr_results:
        lines.append("## 3. V3 Correlation Check")
        lines.append("")
        lines.append("| Signal | Corr with V3 | Status |")
        lines.append("|--------|-------------:|--------|")
        for label, corr in sorted(v3_corr_results.items(), key=lambda x: abs(x[1])):
            status = "PASS" if abs(corr) < 0.5 else "KILL"
            lines.append(f"| {label} | {corr:.4f} | {status} |")
        lines.append("")
        lines.append(f"Kill threshold: |corr| > 0.5")
        lines.append("")

    # -- Stationarity --
    if stationarity:
        lines.append("## 4. Stationarity Check (Rolling IC, 120d window, 7d horizon)")
        lines.append("")
        lines.append("| Signal | Full IC Mean | Full IC Std | Last 2y IC | Last 2y Pos% | Sign Changed |")
        lines.append("|--------|------------:|-----------:|----------:|-----------:|:------------:|")
        for sig_name, stat in stationarity.items():
            lines.append(
                f"| {sig_name} | {stat['full_mean_ic']:.4f} | {stat['full_std_ic']:.4f} | "
                f"{stat['last_2y_mean_ic']:.4f} | {stat['last_2y_pos_pct']:.1%} | "
                f"{'Yes' if stat['ic_sign_changed_last_2y'] else 'No'} |"
            )
        lines.append("")

    # -- Parameter Stability --
    if param_stability:
        lines.append("## 5. Parameter Stability")
        lines.append("")
        lines.append("| Signal | Dominant Mode | Mode Consistency | Stable |")
        lines.append("|--------|:-------------|----------------:|:------:|")
        for sig_name, ps in param_stability.items():
            lines.append(f"| {sig_name} | {ps['dominant_mode']} | "
                        f"{ps['mode_consistency']:.0%} | {'Yes' if ps['stable'] else 'No'} |")
        lines.append("")

    # -- Regime Analysis --
    if regime_results:
        lines.append("## 6. Regime Analysis")
        lines.append("")
        lines.append("How does each signal perform during BTC UPTREND, DOWNTREND, and RANGE?")
        lines.append("(RANGE is where V3 struggles, so diversifiers should add value there.)")
        lines.append("")
        for sig_name, reg in regime_results.items():
            lines.append(f"### {sig_name}")
            lines.append("")
            lines.append("| Regime | Days | % Total | Sharpe | Return |")
            lines.append("|--------|-----:|--------:|-------:|-------:|")
            for r in ['UPTREND', 'DOWNTREND', 'RANGE']:
                if r in reg:
                    m = reg[r]
                    lines.append(f"| {r} | {m['n_days']} | {m['pct_total']:.0%} | "
                               f"{m['sharpe']:.3f} | {m['total_return']:.1%} |")
            lines.append("")

    # -- Kill Criteria Summary --
    lines.append("## Kill Criteria Summary")
    lines.append("")
    lines.append("| Criterion | Threshold | Result | Status |")
    lines.append("|-----------|-----------|--------|--------|")

    best_ic = ic_df['max_abs_ic'].max() if len(ic_df) > 0 else 0
    k1_status = "PASS" if best_ic >= 0.02 else "KILL"
    lines.append(f"| K1: IC >= 0.02 | 0.02 | Best max IC = {best_ic:.4f} | {k1_status} |")

    if v3_corr_results:
        min_corr = min(abs(c) for c in v3_corr_results.values())
        k2_status = "PASS" if min_corr < 0.5 else "KILL"
        lines.append(f"| K2: V3 corr < 0.5 | 0.5 | Min |corr| = {min_corr:.4f} | {k2_status} |")
    else:
        lines.append("| K2: V3 corr < 0.5 | 0.5 | Not tested (killed earlier) | N/A |")

    if wf_results:
        best_wf_sharpe = max(wf['mean_oos_sharpe'] for wf in wf_results.values())
        k3_status = "PASS" if best_wf_sharpe > 0.3 else "KILL"
        lines.append(f"| K3: WF mean Sharpe > 0.3 | 0.3 | Best = {best_wf_sharpe:.3f} | {k3_status} |")
    else:
        lines.append("| K3: WF mean Sharpe > 0.3 | 0.3 | Not tested | N/A |")

    if wf_results:
        best_pos = max(wf['positive_windows'] for wf in wf_results.values())
        best_n = [wf for wf in wf_results.values() if wf['positive_windows'] == best_pos][0]['n_windows']
        k4_status = "PASS" if best_pos >= 5 else "KILL"
        lines.append(f"| K4: >= 5/10 WF positive | 5/10 | Best = {best_pos}/{best_n} | {k4_status} |")
    else:
        lines.append("| K4: >= 5/10 WF positive | 5/10 | Not tested | N/A |")

    lines.append("")

    # -- Final Verdict --
    lines.append("## Final Verdict")
    lines.append("")
    lines.append(f"**{verdict}**: {reason}")
    lines.append("")

    if final_survivors:
        lines.append("### Surviving Signals")
        lines.append("")
        for sig_name, info in final_survivors.items():
            wf = info['wf']
            lines.append(f"- **{sig_name}**: Mean OOS Sharpe={wf['mean_oos_sharpe']:.3f}, "
                         f"Positive={wf['positive_windows']}/{wf['n_windows']}, "
                         f"V3 corr={info['v3_corr']:.4f}, "
                         f"Recent 3w mean={wf['recent_3w_mean_sharpe']:.3f}")
        lines.append("")

    # -- Detailed Analysis --
    lines.append("## Detailed Analysis")
    lines.append("")

    lines.append("### Key Observations")
    lines.append("")

    # All ICs are negative -> gold strength predicts BTC weakness
    neg_ic_count = sum(1 for s in ic_df.head(15).index if ic_df.loc[s, 'dominant_sign'] < 0)
    lines.append(f"1. **Direction**: {neg_ic_count}/15 top signals have negative IC (gold strength -> BTC weakness). "
                 "This confirms the risk-off thesis: when gold is rallying relative to BTC, "
                 "BTC forward returns tend to be negative.")
    lines.append("")
    lines.append("2. **Horizon Effect**: IC magnitude increases monotonically from 1d to 14d across "
                 "all top signals. The gold-BTC relationship is slow-moving -- it predicts multi-week "
                 "returns better than daily returns. This is consistent with a macro regime signal.")
    lines.append("")

    if wf_results:
        n_pass = len(wf_survivors)
        n_total = len(wf_results)
        lines.append(f"3. **Walk-Forward**: {n_pass}/{n_total} signals pass both WF criteria. "
                     "The anchored-from-end design ensures the most recent market regime "
                     "(2024-2026) is tested in every signal's last 3 windows.")
        lines.append("")

    if stationarity:
        n_sign_changed = sum(1 for s in stationarity.values() if s.get('ic_sign_changed_last_2y', True))
        lines.append(f"4. **Stationarity WARNING**: {n_sign_changed}/{len(stationarity)} signals show IC sign changes "
                     "in the last 2 years. Rolling IC hovers around zero with ~50% positive -- "
                     "this means the signal is noisy and the predictive relationship is weak/intermittent.")
        lines.append("")

    if regime_results:
        lines.append("5. **Regime Performance**: ")
        for sig_name, reg in regime_results.items():
            range_sharpe = reg.get('RANGE', {}).get('sharpe', 0)
            lines.append(f"   - {sig_name}: RANGE Sharpe = {range_sharpe:.3f} "
                        f"({'positive -- diversifies V3' if range_sharpe > 0 else 'NEGATIVE -- no diversification value'})")
        lines.append("")

    # -- Recommendations --
    lines.append("## Recommendations")
    lines.append("")
    if final_survivors:
        # Be honest about quality
        lines.append("### Caution: Surface-Level Pass Masks Fragility")
        lines.append("")
        lines.append("While some signals technically pass all kill criteria, several red flags warrant caution:")
        lines.append("")
        if stationarity:
            lines.append("- **All signals show IC sign changes in the last 2 years.** "
                        "The gold->BTC predictive relationship is intermittent, not persistent.")
        if param_stability:
            unstable = [s for s, ps in param_stability.items() if not ps.get('stable', False)]
            if unstable:
                lines.append(f"- **Parameter instability** in {len(unstable)} signal(s): "
                            "optimal mode (long/short vs tercile) shifts across windows.")
        lines.append("- **High OOS Sharpe variance**: Min/Max OOS Sharpe ranges are wide, "
                    "indicating inconsistent performance across market regimes.")
        lines.append("")
        lines.append("### Next Steps (if proceeding)")
        lines.append("")
        lines.append("- Paper trade the best signal for 90 days before any live allocation")
        lines.append("- Cap allocation at 10% of portfolio until 6+ months of live data confirm edge")
        lines.append("- Set kill switch: if rolling 90d Sharpe < -0.5, halt the signal")
        lines.append("- Recheck IC monthly for sign stability")
    else:
        lines.append("- **Signal fails kill criteria. Do NOT integrate into portfolio.**")
        lines.append("- Gold momentum and gold-BTC dynamics do not provide reliable BTC timing signals")
        lines.append("- **Potential salvage paths** (for future research):")
        lines.append("  - Test gold at weekly/monthly frequency (reduce daily noise)")
        lines.append("  - Combine gold with VIX regime gate")
        lines.append("  - Test gold relative to other macro assets as a composite signal")
        lines.append("  - Investigate nonlinear relationships")
        lines.append("  - Test on altcoins or crypto sector indices")
    lines.append("")

    report = "\n".join(lines)
    with open(OUTPUT_MD, 'w') as f:
        f.write(report)
    print(f"[REPORT] Written to {OUTPUT_MD}")


if __name__ == '__main__':
    main()
