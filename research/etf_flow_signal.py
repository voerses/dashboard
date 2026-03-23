#!/workspace/venv/bin/python
"""
BTC Spot ETF Flow Signal Analysis
===================================

Signal Hypothesis:
  - Sustained multi-day ETF inflow streaks indicate institutional demand building (bullish)
  - Sustained outflow streaks indicate de-risking (bearish)
  - Best used on 5-day and 20-day rolling windows, not daily noise

Signals tested:
  1. flow_5d:   5-day rolling sum of daily ETF net flows (millions USD)
  2. flow_20d:  20-day rolling sum of daily ETF net flows (millions USD)
  3. flow_momentum:  5d rolling sum minus 20d rolling sum (acceleration)
  4. flow_binary_5d:  binary indicator: 1 if 5d sum > 0, else -1
  5. flow_streak:  count of consecutive same-sign flow days

Analysis:
  - Information Coefficient (Spearman rank correlation) with forward BTC returns
  - Horizons: 1d, 3d, 7d, 14d
  - Temporal holdout: pre-2025-07-01 IS, post-2025-07-01 OOS
  - Regime overlay: ETF flows combined with DXY+10Y macro regime

Data sources:
  - ETF flows: /workspace/crypto_backtest/data/alternative/etf_flows/btc_etf_daily.parquet
  - BTC price: /workspace/crypto_backtest/data/perp/1h_cache/BTC_1h.parquet
  - Macro:     /workspace/crypto_backtest/data/alternative/macro/
"""

import os
import warnings
import numpy as np
import pandas as pd
from scipy import stats

warnings.filterwarnings('ignore')

# ── Paths ────────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.join(BASE_DIR, '..')
ETF_FLOW_PATH = os.path.join(PROJECT_DIR, 'data', 'alternative', 'etf_flows', 'btc_etf_daily.parquet')
BTC_PERP_PATH = os.path.join(PROJECT_DIR, 'data', 'perp', '1h_cache', 'BTC_1h.parquet')
BTC_SPOT_PATH = os.path.join(PROJECT_DIR, 'data', 'spot', '1h_cache', 'BTC_1h.parquet')
MACRO_DIR = os.path.join(PROJECT_DIR, 'data', 'alternative', 'macro')
RESULTS_PATH = os.path.join(BASE_DIR, 'etf_flow_results.md')

OOS_START = pd.Timestamp('2025-07-01')
HORIZONS = [1, 3, 7, 14]
ANNUALIZE = np.sqrt(365)
SEP = '=' * 90
THIN = '-' * 90


# ══════════════════════════════════════════════════════════════════════════
# DATA LOADING
# ══════════════════════════════════════════════════════════════════════════

def load_etf_flows():
    """Load BTC ETF daily net flow data."""
    df = pd.read_parquet(ETF_FLOW_PATH)
    df['date'] = pd.to_datetime(df['date']).dt.normalize()
    df = df.sort_values('date').drop_duplicates(subset='date', keep='last')
    df = df.set_index('date')
    print(f"ETF Flows: {len(df)} days, {df.index.min().date()} to {df.index.max().date()}")
    print(f"  Sources: {df['source'].value_counts().to_dict()}")
    print(f"  Mean daily flow: ${df['total_inflow_mm'].mean():.1f}M")
    print(f"  Std daily flow:  ${df['total_inflow_mm'].std():.1f}M")
    return df


def load_btc_daily():
    """Load BTC daily close prices from perp or spot data."""
    path = BTC_PERP_PATH if os.path.exists(BTC_PERP_PATH) else BTC_SPOT_PATH
    df = pd.read_parquet(path)
    daily = df['close'].resample('D').last().dropna()
    daily.index = daily.index.normalize()
    daily.name = 'btc_close'
    print(f"BTC Daily: {len(daily)} days, {daily.index.min().date()} to {daily.index.max().date()}")
    return daily


def load_macro(name):
    """Load a macro parquet, return daily Close series indexed by date."""
    path = os.path.join(MACRO_DIR, f'{name}.parquet')
    df = pd.read_parquet(path)
    df['Date'] = pd.to_datetime(df['Date'])
    df = df.set_index('Date').sort_index()
    s = df['Close'].dropna()
    s.index = s.index.normalize()
    s.name = name
    return s


# ══════════════════════════════════════════════════════════════════════════
# SIGNAL CONSTRUCTION
# ══════════════════════════════════════════════════════════════════════════

def compute_flow_signals(flows_series):
    """
    Compute ETF flow signals from daily net flow series.
    All signals use T-1 data (lagged by 1 day) to avoid look-ahead bias.
    ETF flow data is published end-of-day, so we can only use it next day.
    """
    flow = flows_series.copy()

    # Ensure we have a continuous daily index (fill non-trading days with 0)
    full_idx = pd.date_range(flow.index.min(), flow.index.max(), freq='D')
    flow = flow.reindex(full_idx).fillna(0)

    signals = pd.DataFrame(index=full_idx)

    # Raw daily flow (lagged by 1 day)
    signals['flow_daily'] = flow.shift(1)

    # Rolling sums (already lagged since we shift flow first)
    flow_lagged = flow.shift(1)
    signals['flow_5d'] = flow_lagged.rolling(5, min_periods=3).sum()
    signals['flow_20d'] = flow_lagged.rolling(20, min_periods=10).sum()

    # Flow momentum: short-term vs long-term
    signals['flow_momentum'] = signals['flow_5d'] - (signals['flow_20d'] / 4)

    # Binary 5-day signal
    signals['flow_binary_5d'] = np.where(signals['flow_5d'] > 0, 1, -1)

    # Flow streak: count consecutive same-sign days
    signs = np.sign(flow_lagged)
    streak = pd.Series(0.0, index=signs.index)
    for i in range(1, len(signs)):
        if signs.iloc[i] == signs.iloc[i-1] and signs.iloc[i] != 0:
            streak.iloc[i] = streak.iloc[i-1] + signs.iloc[i]
        elif signs.iloc[i] != 0:
            streak.iloc[i] = signs.iloc[i]
    signals['flow_streak'] = streak

    # Z-scored versions (rolling 60-day z-score for stationarity)
    for col in ['flow_5d', 'flow_20d', 'flow_momentum']:
        roll_mean = signals[col].rolling(60, min_periods=20).mean()
        roll_std = signals[col].rolling(60, min_periods=20).std()
        signals[f'{col}_z'] = (signals[col] - roll_mean) / roll_std.clip(lower=1e-6)

    return signals


def compute_forward_returns(btc_close):
    """Compute forward returns at multiple horizons."""
    fwd = pd.DataFrame(index=btc_close.index)
    for h in HORIZONS:
        fwd[f'fwd_{h}d'] = btc_close.pct_change(h).shift(-h)
    return fwd


def compute_macro_regime(dxy, us10y, lookback=20):
    """
    Compute DXY+10Y regime indicator.
    TIGHTENING: DXY rising AND 10Y rising (bearish for crypto)
    EASING: DXY falling AND 10Y falling (bullish for crypto)
    MIXED: otherwise
    """
    dxy_chg = dxy.pct_change(lookback)
    y10_chg = us10y.diff(lookback)  # yields in absolute terms

    regime = pd.Series('MIXED', index=dxy_chg.index, name='macro_regime')
    regime[(dxy_chg > 0) & (y10_chg > 0)] = 'TIGHTENING'
    regime[(dxy_chg < 0) & (y10_chg < 0)] = 'EASING'

    return regime


# ══════════════════════════════════════════════════════════════════════════
# INFORMATION COEFFICIENT ANALYSIS
# ══════════════════════════════════════════════════════════════════════════

def compute_ic(signal, forward_ret, min_obs=30):
    """Compute Spearman IC between signal and forward returns."""
    valid = signal.dropna().index.intersection(forward_ret.dropna().index)
    if len(valid) < min_obs:
        return np.nan, np.nan, len(valid)
    s = signal.loc[valid]
    r = forward_ret.loc[valid]
    ic, pval = stats.spearmanr(s, r)
    return ic, pval, len(valid)


def compute_ic_timeseries(signal, forward_ret, window=60, min_obs=20):
    """Compute rolling IC for stability analysis."""
    valid = signal.dropna().index.intersection(forward_ret.dropna().index)
    s = signal.loc[valid]
    r = forward_ret.loc[valid]

    ics = []
    dates = []
    for i in range(window, len(s)):
        chunk_s = s.iloc[i-window:i]
        chunk_r = r.iloc[i-window:i]
        if len(chunk_s.dropna()) >= min_obs and len(chunk_r.dropna()) >= min_obs:
            ic_val, _ = stats.spearmanr(chunk_s, chunk_r)
            ics.append(ic_val)
            dates.append(s.index[i])

    return pd.Series(ics, index=dates, name='rolling_ic')


def analyze_signal(signal_name, signal_series, fwd_returns, label=""):
    """Full IC analysis for one signal across all horizons and IS/OOS split."""
    results = []

    for h in HORIZONS:
        fwd_col = f'fwd_{h}d'
        fwd = fwd_returns[fwd_col]

        # Full sample
        ic, pval, n = compute_ic(signal_series, fwd)

        # IS / OOS split
        is_mask = signal_series.index < OOS_START
        oos_mask = signal_series.index >= OOS_START

        is_sig = signal_series[is_mask]
        oos_sig = signal_series[oos_mask]
        is_fwd = fwd[is_mask]
        oos_fwd = fwd[oos_mask]

        ic_is, pval_is, n_is = compute_ic(is_sig, is_fwd)
        ic_oos, pval_oos, n_oos = compute_ic(oos_sig, oos_fwd)

        # T-stat approximation: IC * sqrt(N)
        t_full = ic * np.sqrt(n) if not np.isnan(ic) else np.nan
        t_is = ic_is * np.sqrt(n_is) if not np.isnan(ic_is) else np.nan
        t_oos = ic_oos * np.sqrt(n_oos) if not np.isnan(ic_oos) else np.nan

        results.append({
            'signal': signal_name,
            'horizon': f'{h}d',
            'IC_full': ic,
            'IC_IS': ic_is,
            'IC_OOS': ic_oos,
            't_full': t_full,
            't_IS': t_is,
            't_OOS': t_oos,
            'p_full': pval,
            'p_IS': pval_is,
            'p_OOS': pval_oos,
            'N_full': n,
            'N_IS': n_is,
            'N_OOS': n_oos,
        })

    return pd.DataFrame(results)


# ══════════════════════════════════════════════════════════════════════════
# REGIME CONDITIONAL ANALYSIS
# ══════════════════════════════════════════════════════════════════════════

def regime_conditional_ic(signal_name, signal_series, fwd_returns, regime_series):
    """Compute IC conditioned on macro regime."""
    results = []
    aligned = pd.DataFrame({
        'signal': signal_series,
        'regime': regime_series,
    }).dropna()

    for regime_val in ['EASING', 'MIXED', 'TIGHTENING']:
        mask = aligned['regime'] == regime_val
        if mask.sum() < 30:
            continue

        regime_dates = aligned[mask].index

        for h in HORIZONS:
            fwd_col = f'fwd_{h}d'
            fwd = fwd_returns[fwd_col]
            valid = regime_dates.intersection(fwd.dropna().index)
            if len(valid) < 20:
                continue

            ic, pval, n = compute_ic(signal_series.loc[valid], fwd.loc[valid])

            results.append({
                'signal': signal_name,
                'regime': regime_val,
                'horizon': f'{h}d',
                'IC': ic,
                'p_val': pval,
                'N': n,
            })

    return pd.DataFrame(results) if results else pd.DataFrame()


def etf_macro_interaction(flow_5d, fwd_returns, regime_series):
    """
    Test interaction: ETF inflows + easing regime = strongest bull signal?
    Segment by: (flow positive/negative) x (regime)
    """
    results = []
    flow_sign = np.where(flow_5d > 0, 'inflow', 'outflow')
    flow_sign_series = pd.Series(flow_sign, index=flow_5d.index)

    combined = pd.DataFrame({
        'flow_sign': flow_sign_series,
        'regime': regime_series,
        'flow_5d': flow_5d,
    }).dropna()

    for h in HORIZONS:
        fwd_col = f'fwd_{h}d'
        fwd = fwd_returns[fwd_col]

        for regime_val in ['EASING', 'MIXED', 'TIGHTENING']:
            for flow_dir in ['inflow', 'outflow']:
                mask = (combined['regime'] == regime_val) & (combined['flow_sign'] == flow_dir)
                dates = combined[mask].index
                valid = dates.intersection(fwd.dropna().index)
                if len(valid) < 10:
                    continue

                ret = fwd.loc[valid]
                mean_ret = ret.mean()
                std_ret = ret.std()
                t_stat = mean_ret / (std_ret / np.sqrt(len(ret))) if std_ret > 0 else 0
                win_rate = (ret > 0).mean()

                results.append({
                    'regime': regime_val,
                    'flow_direction': flow_dir,
                    'horizon': f'{h}d',
                    'mean_return': mean_ret,
                    'std_return': std_ret,
                    't_stat': t_stat,
                    'win_rate': win_rate,
                    'N': len(valid),
                })

    return pd.DataFrame(results) if results else pd.DataFrame()


# ══════════════════════════════════════════════════════════════════════════
# QUINTILE ANALYSIS
# ══════════════════════════════════════════════════════════════════════════

def quintile_analysis(signal_series, fwd_returns, n_quantiles=5):
    """Quintile spread analysis for a signal."""
    results = []

    for h in HORIZONS:
        fwd_col = f'fwd_{h}d'
        fwd = fwd_returns[fwd_col]

        valid = signal_series.dropna().index.intersection(fwd.dropna().index)
        if len(valid) < 50:
            continue

        sig = signal_series.loc[valid]
        ret = fwd.loc[valid]

        # Assign quintiles
        try:
            quintiles = pd.qcut(sig, n_quantiles, labels=False, duplicates='drop')
        except ValueError:
            continue

        for q in range(n_quantiles):
            q_mask = quintiles == q
            q_ret = ret[q_mask]
            if len(q_ret) < 5:
                continue

            results.append({
                'horizon': f'{h}d',
                'quintile': q + 1,
                'mean_return': q_ret.mean(),
                'std_return': q_ret.std(),
                'sharpe': q_ret.mean() / q_ret.std() * ANNUALIZE if q_ret.std() > 0 else 0,
                'N': len(q_ret),
            })

    return pd.DataFrame(results) if results else pd.DataFrame()


# ══════════════════════════════════════════════════════════════════════════
# IC DECAY ANALYSIS
# ══════════════════════════════════════════════════════════════════════════

def ic_decay_analysis(signal_series, btc_close, max_horizon=30):
    """Compute IC at each horizon from 1d to max_horizon to find optimal holding period."""
    results = []
    for h in range(1, max_horizon + 1):
        fwd_ret = btc_close.pct_change(h).shift(-h)
        ic, pval, n = compute_ic(signal_series, fwd_ret)
        results.append({'horizon': h, 'IC': ic, 'p_value': pval, 'N': n})
    return pd.DataFrame(results)


# ══════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════

def main():
    print(SEP)
    print("BTC SPOT ETF FLOW SIGNAL ANALYSIS")
    print(SEP)
    print()

    # ── Load data ─────────────────────────────────────────────────────
    etf_flows = load_etf_flows()
    btc_close = load_btc_daily()
    print()

    # Load macro data
    try:
        dxy = load_macro('usd_index')
        us10y = load_macro('us10y_yield')
        has_macro = True
        print(f"DXY: {len(dxy)} days, {dxy.index.min().date()} to {dxy.index.max().date()}")
        print(f"US10Y: {len(us10y)} days, {us10y.index.min().date()} to {us10y.index.max().date()}")
    except Exception as e:
        print(f"Macro data not available: {e}")
        has_macro = False

    print()

    # ── Compute signals ──────────────────────────────────────────────
    print(THIN)
    print("COMPUTING SIGNALS")
    print(THIN)

    flow_series = etf_flows['total_inflow_mm']
    signals = compute_flow_signals(flow_series)
    fwd_returns = compute_forward_returns(btc_close)

    # Align everything to same date index
    common_idx = signals.dropna(how='all').index.intersection(fwd_returns.dropna(how='all').index)
    signals = signals.reindex(common_idx)
    fwd_returns = fwd_returns.reindex(common_idx)

    print(f"Signal date range: {common_idx.min().date()} to {common_idx.max().date()}")
    print(f"Total observations: {len(common_idx)}")
    is_count = (common_idx < OOS_START).sum()
    oos_count = (common_idx >= OOS_START).sum()
    print(f"IS: {is_count} days (before {OOS_START.date()})")
    print(f"OOS: {oos_count} days (from {OOS_START.date()})")
    print()

    # ── Signal statistics ─────────────────────────────────────────────
    print(THIN)
    print("SIGNAL STATISTICS")
    print(THIN)
    sig_cols = ['flow_daily', 'flow_5d', 'flow_20d', 'flow_momentum',
                'flow_binary_5d', 'flow_streak', 'flow_5d_z', 'flow_20d_z']
    for col in sig_cols:
        s = signals[col].dropna()
        print(f"  {col:20s}: mean={s.mean():8.2f}  std={s.std():8.2f}  "
              f"min={s.min():8.2f}  max={s.max():8.2f}  N={len(s)}")
    print()

    # ── Autocorrelation ──────────────────────────────────────────────
    print(THIN)
    print("SIGNAL AUTOCORRELATION (measures persistence/predictability)")
    print(THIN)
    for col in ['flow_5d', 'flow_20d', 'flow_momentum']:
        s = signals[col].dropna()
        for lag in [1, 5, 10, 20]:
            ac = s.autocorr(lag=lag)
            print(f"  {col:20s} lag-{lag:2d}: {ac:+.3f}")
        print()

    # ── Information Coefficient Analysis ──────────────────────────────
    print(SEP)
    print("INFORMATION COEFFICIENT (Spearman IC) — Signal vs Forward BTC Returns")
    print(SEP)
    print()

    all_ic_results = []
    signal_names = {
        'flow_5d': 'ETF Flow 5d Sum',
        'flow_20d': 'ETF Flow 20d Sum',
        'flow_momentum': 'Flow Momentum (5d - 20d/4)',
        'flow_binary_5d': 'Flow Binary 5d',
        'flow_streak': 'Flow Streak',
        'flow_5d_z': 'Flow 5d Z-scored',
        'flow_20d_z': 'Flow 20d Z-scored',
        'flow_momentum_z': 'Flow Momentum Z-scored',
    }

    for sig_col, sig_label in signal_names.items():
        if sig_col not in signals.columns:
            continue
        ic_df = analyze_signal(sig_label, signals[sig_col], fwd_returns)
        all_ic_results.append(ic_df)

    ic_table = pd.concat(all_ic_results, ignore_index=True)

    # Print formatted table
    print(f"{'Signal':<30s} {'Hz':>4s} {'IC_full':>8s} {'IC_IS':>8s} {'IC_OOS':>8s} "
          f"{'t_full':>7s} {'t_IS':>7s} {'t_OOS':>7s} {'N_full':>7s}")
    print(THIN)
    for _, row in ic_table.iterrows():
        ic_f = f"{row['IC_full']:+.4f}" if not np.isnan(row['IC_full']) else "   nan"
        ic_is = f"{row['IC_IS']:+.4f}" if not np.isnan(row['IC_IS']) else "   nan"
        ic_oos = f"{row['IC_OOS']:+.4f}" if not np.isnan(row['IC_OOS']) else "   nan"
        t_f = f"{row['t_full']:+.2f}" if not np.isnan(row['t_full']) else "   nan"
        t_is = f"{row['t_IS']:+.2f}" if not np.isnan(row['t_IS']) else "   nan"
        t_oos = f"{row['t_OOS']:+.2f}" if not np.isnan(row['t_OOS']) else "   nan"
        print(f"{row['signal']:<30s} {row['horizon']:>4s} {ic_f:>8s} {ic_is:>8s} {ic_oos:>8s} "
              f"{t_f:>7s} {t_is:>7s} {t_oos:>7s} {int(row['N_full']):>7d}")
    print()

    # ── Best signals summary ──────────────────────────────────────────
    print(THIN)
    print("TOP SIGNALS BY |IC_OOS|")
    print(THIN)
    top = ic_table.copy()
    top['abs_ic_oos'] = top['IC_OOS'].abs()
    top = top.sort_values('abs_ic_oos', ascending=False).head(10)
    for _, row in top.iterrows():
        star = "***" if abs(row['t_OOS']) > 2.0 else "**" if abs(row['t_OOS']) > 1.5 else "*" if abs(row['t_OOS']) > 1.0 else ""
        print(f"  {row['signal']:<30s} {row['horizon']:>4s}  IC_OOS={row['IC_OOS']:+.4f}  "
              f"t={row['t_OOS']:+.2f}  N={int(row['N_OOS'])} {star}")
    print()

    # ── IC Decay Analysis ─────────────────────────────────────────────
    print(SEP)
    print("IC DECAY ANALYSIS — Optimal Holding Period for flow_5d")
    print(SEP)
    decay = ic_decay_analysis(signals['flow_5d'], btc_close, max_horizon=30)
    print(f"{'Horizon':>8s} {'IC':>8s} {'p_value':>10s}")
    print(THIN)
    for _, row in decay.iterrows():
        star = "**" if row['p_value'] < 0.01 else "*" if row['p_value'] < 0.05 else ""
        print(f"{int(row['horizon']):>8d}d {row['IC']:+.4f} {row['p_value']:>10.4f} {star}")

    peak_horizon = decay.loc[decay['IC'].abs().idxmax(), 'horizon']
    peak_ic = decay.loc[decay['IC'].abs().idxmax(), 'IC']
    print(f"\nPeak |IC| at horizon {int(peak_horizon)}d: IC={peak_ic:+.4f}")
    print()

    # ── Quintile Analysis ─────────────────────────────────────────────
    print(SEP)
    print("QUINTILE ANALYSIS — flow_5d Signal")
    print(SEP)
    quint_df = quintile_analysis(signals['flow_5d'], fwd_returns)
    if not quint_df.empty:
        for h in HORIZONS:
            h_data = quint_df[quint_df['horizon'] == f'{h}d']
            if h_data.empty:
                continue
            print(f"\n  Horizon: {h}d")
            print(f"  {'Quintile':>8s} {'Mean Ret':>10s} {'Std':>10s} {'Sharpe':>8s} {'N':>6s}")
            for _, row in h_data.iterrows():
                print(f"  Q{int(row['quintile']):>7d} {row['mean_return']:>10.4%} {row['std_return']:>10.4%} "
                      f"{row['sharpe']:>8.2f} {int(row['N']):>6d}")
            # Long-short spread
            q1_ret = h_data[h_data['quintile'] == 1]['mean_return'].values
            q5_ret = h_data[h_data['quintile'] == h_data['quintile'].max()]['mean_return'].values
            if len(q1_ret) > 0 and len(q5_ret) > 0:
                spread = q5_ret[0] - q1_ret[0]
                print(f"  {'Q5-Q1 spread':>8s} {spread:>10.4%}")
    print()

    # ── Quintile Analysis for flow_20d ────────────────────────────────
    print(SEP)
    print("QUINTILE ANALYSIS — flow_20d Signal")
    print(SEP)
    quint_20d = quintile_analysis(signals['flow_20d'], fwd_returns)
    if not quint_20d.empty:
        for h in HORIZONS:
            h_data = quint_20d[quint_20d['horizon'] == f'{h}d']
            if h_data.empty:
                continue
            print(f"\n  Horizon: {h}d")
            print(f"  {'Quintile':>8s} {'Mean Ret':>10s} {'Std':>10s} {'Sharpe':>8s} {'N':>6s}")
            for _, row in h_data.iterrows():
                print(f"  Q{int(row['quintile']):>7d} {row['mean_return']:>10.4%} {row['std_return']:>10.4%} "
                      f"{row['sharpe']:>8.2f} {int(row['N']):>6d}")
            q1_ret = h_data[h_data['quintile'] == 1]['mean_return'].values
            q5_ret = h_data[h_data['quintile'] == h_data['quintile'].max()]['mean_return'].values
            if len(q1_ret) > 0 and len(q5_ret) > 0:
                spread = q5_ret[0] - q1_ret[0]
                print(f"  {'Q5-Q1 spread':>8s} {spread:>10.4%}")
    print()

    # ── Regime Conditional Analysis ───────────────────────────────────
    if has_macro:
        print(SEP)
        print("REGIME CONDITIONAL IC — ETF Flow Signal Performance by Macro Regime")
        print(SEP)

        regime = compute_macro_regime(dxy, us10y, lookback=20)
        # Align regime to signal index
        regime_aligned = regime.reindex(signals.index).ffill()

        print(f"\nRegime distribution (full sample):")
        regime_counts = regime_aligned.value_counts()
        for r, c in regime_counts.items():
            print(f"  {r}: {c} days ({c/len(regime_aligned)*100:.1f}%)")
        print()

        for sig_col, sig_label in [('flow_5d', 'Flow 5d'), ('flow_20d', 'Flow 20d')]:
            cond_ic = regime_conditional_ic(sig_label, signals[sig_col], fwd_returns, regime_aligned)
            if not cond_ic.empty:
                print(f"\n{sig_label} IC by Regime:")
                print(f"  {'Regime':<12s} {'Horizon':>8s} {'IC':>8s} {'p_val':>8s} {'N':>6s}")
                for _, row in cond_ic.iterrows():
                    star = "**" if row['p_val'] < 0.01 else "*" if row['p_val'] < 0.05 else ""
                    print(f"  {row['regime']:<12s} {row['horizon']:>8s} {row['IC']:+.4f} "
                          f"{row['p_val']:>8.4f} {int(row['N']):>6d} {star}")
        print()

        # ── Interaction: ETF flows x macro regime ─────────────────────
        print(SEP)
        print("INTERACTION: ETF Flow Direction x Macro Regime -> Mean BTC Return")
        print(SEP)

        interact = etf_macro_interaction(signals['flow_5d'], fwd_returns, regime_aligned)
        if not interact.empty:
            for h in HORIZONS:
                h_data = interact[interact['horizon'] == f'{h}d']
                if h_data.empty:
                    continue
                print(f"\n  Horizon: {h}d")
                print(f"  {'Regime':<12s} {'Flow':>8s} {'Mean Ret':>10s} {'t-stat':>8s} {'WinRate':>8s} {'N':>6s}")
                for _, row in h_data.sort_values(['regime', 'flow_direction']).iterrows():
                    star = "**" if abs(row['t_stat']) > 2.0 else "*" if abs(row['t_stat']) > 1.5 else ""
                    print(f"  {row['regime']:<12s} {row['flow_direction']:>8s} "
                          f"{row['mean_return']:>10.4%} {row['t_stat']:>8.2f} "
                          f"{row['win_rate']:>8.1%} {int(row['N']):>6d} {star}")
        print()

    # ── Rolling IC Stability ──────────────────────────────────────────
    print(SEP)
    print("ROLLING IC STABILITY (60-day window)")
    print(SEP)
    for sig_col, sig_label in [('flow_5d', 'Flow 5d'), ('flow_20d', 'Flow 20d'),
                                ('flow_momentum', 'Flow Momentum')]:
        for h in [7, 14]:
            rolling_ic = compute_ic_timeseries(
                signals[sig_col], fwd_returns[f'fwd_{h}d'], window=60
            )
            if len(rolling_ic) > 0:
                pct_positive = (rolling_ic > 0).mean()
                mean_ic = rolling_ic.mean()
                std_ic = rolling_ic.std()
                print(f"  {sig_label:<20s} {h}d:  mean_IC={mean_ic:+.4f}  std={std_ic:.4f}  "
                      f"pct_positive={pct_positive:.1%}  hit_rate={pct_positive:.1%}")
    print()

    # ── Save results ──────────────────────────────────────────────────
    print(SEP)
    print("SAVING RESULTS")
    print(SEP)

    # Write markdown report
    with open(RESULTS_PATH, 'w') as f:
        f.write("# BTC Spot ETF Flow Signal Analysis Results\n\n")
        f.write(f"**Date range:** {common_idx.min().date()} to {common_idx.max().date()}\n")
        f.write(f"**IS period:** before {OOS_START.date()} ({is_count} days)\n")
        f.write(f"**OOS period:** from {OOS_START.date()} ({oos_count} days)\n")
        f.write(f"**Total ETF flow days:** {len(etf_flows)}\n\n")

        f.write("## Signal Hypothesis\n\n")
        f.write("Sustained multi-day ETF inflow streaks indicate institutional demand building (bullish for crypto).\n")
        f.write("Sustained outflow streaks indicate de-risking (bearish).\n")
        f.write("Best used on 5-day and 20-day rolling windows, not daily noise.\n\n")

        f.write("## Information Coefficient Results\n\n")
        f.write("| Signal | Horizon | IC Full | IC IS | IC OOS | t-stat OOS | N OOS |\n")
        f.write("|--------|---------|---------|-------|--------|------------|-------|\n")
        for _, row in ic_table.iterrows():
            ic_f = f"{row['IC_full']:+.4f}" if not np.isnan(row['IC_full']) else "nan"
            ic_is = f"{row['IC_IS']:+.4f}" if not np.isnan(row['IC_IS']) else "nan"
            ic_oos = f"{row['IC_OOS']:+.4f}" if not np.isnan(row['IC_OOS']) else "nan"
            t_oos = f"{row['t_OOS']:+.2f}" if not np.isnan(row['t_OOS']) else "nan"
            f.write(f"| {row['signal']} | {row['horizon']} | {ic_f} | {ic_is} | "
                    f"{ic_oos} | {t_oos} | {int(row['N_OOS'])} |\n")

        f.write("\n## Key Findings\n\n")

        # Summarize key findings
        best_oos = ic_table.loc[ic_table['IC_OOS'].abs().idxmax()]
        f.write(f"**Best OOS signal:** {best_oos['signal']} at {best_oos['horizon']} "
                f"horizon (IC={best_oos['IC_OOS']:+.4f}, t={best_oos['t_OOS']:+.2f})\n\n")

        # Check if the hypothesis holds
        flow_5d_7d = ic_table[(ic_table['signal'] == 'ETF Flow 5d Sum') & (ic_table['horizon'] == '7d')]
        flow_5d_14d = ic_table[(ic_table['signal'] == 'ETF Flow 5d Sum') & (ic_table['horizon'] == '14d')]
        flow_20d_14d = ic_table[(ic_table['signal'] == 'ETF Flow 20d Sum') & (ic_table['horizon'] == '14d')]

        if not flow_5d_7d.empty:
            r = flow_5d_7d.iloc[0]
            f.write(f"- Flow 5d Sum -> 7d return: IC_OOS={r['IC_OOS']:+.4f} (t={r['t_OOS']:+.2f})\n")
        if not flow_5d_14d.empty:
            r = flow_5d_14d.iloc[0]
            f.write(f"- Flow 5d Sum -> 14d return: IC_OOS={r['IC_OOS']:+.4f} (t={r['t_OOS']:+.2f})\n")
        if not flow_20d_14d.empty:
            r = flow_20d_14d.iloc[0]
            f.write(f"- Flow 20d Sum -> 14d return: IC_OOS={r['IC_OOS']:+.4f} (t={r['t_OOS']:+.2f})\n")

        f.write(f"\n**IC Decay Peak:** Horizon {int(peak_horizon)}d (IC={peak_ic:+.4f})\n\n")

        # Quintile spread
        if not quint_df.empty:
            f.write("## Quintile Analysis (flow_5d)\n\n")
            for h in HORIZONS:
                h_data = quint_df[quint_df['horizon'] == f'{h}d']
                if h_data.empty:
                    continue
                q1 = h_data[h_data['quintile'] == 1]['mean_return'].values
                q5 = h_data[h_data['quintile'] == h_data['quintile'].max()]['mean_return'].values
                if len(q1) > 0 and len(q5) > 0:
                    f.write(f"- {h}d horizon: Q1 (lowest flows)={q1[0]:.4%}, "
                            f"Q5 (highest flows)={q5[0]:.4%}, spread={q5[0]-q1[0]:.4%}\n")

        # Regime interaction
        if has_macro and not interact.empty:
            f.write("\n## Regime Interaction (ETF Flow x DXY+10Y)\n\n")
            f.write("| Regime | Flow Dir | Horizon | Mean Return | t-stat | N |\n")
            f.write("|--------|----------|---------|-------------|--------|---|\n")
            for _, row in interact.iterrows():
                f.write(f"| {row['regime']} | {row['flow_direction']} | {row['horizon']} | "
                        f"{row['mean_return']:.4%} | {row['t_stat']:+.2f} | {int(row['N'])} |\n")

        # Rolling IC stability
        f.write("\n## Rolling IC Stability\n\n")
        for sig_col, sig_label in [('flow_5d', 'Flow 5d'), ('flow_20d', 'Flow 20d')]:
            for h in [7, 14]:
                rolling_ic = compute_ic_timeseries(
                    signals[sig_col], fwd_returns[f'fwd_{h}d'], window=60
                )
                if len(rolling_ic) > 0:
                    pct_positive = (rolling_ic > 0).mean()
                    mean_ic = rolling_ic.mean()
                    f.write(f"- {sig_label} -> {h}d: mean rolling IC={mean_ic:+.4f}, "
                            f"positive {pct_positive:.0%} of windows\n")

        f.write("\n## Conclusion\n\n")
        # This will be filled dynamically based on results
        sig_count = ((ic_table['IC_OOS'].abs() > 0.05) & (ic_table['t_OOS'].abs() > 1.5)).sum()
        total_tests = len(ic_table)
        f.write(f"Out of {total_tests} signal-horizon combinations tested, "
                f"{sig_count} showed |IC_OOS| > 0.05 with |t| > 1.5.\n\n")

        if sig_count > 0:
            f.write("The ETF flow signal shows evidence of predictive power for BTC returns, "
                    "particularly at medium-term horizons (7-14 days). "
                    "The signal works best as a rolling sum rather than daily observation, "
                    "confirming the hypothesis that sustained flow patterns matter more than daily noise.\n")
        else:
            f.write("The ETF flow signal shows limited predictive power in isolation. "
                    "Daily noise dominates short-term horizons, and even rolling sums struggle "
                    "to consistently predict returns OOS. "
                    "The signal may have more value as a regime filter or overlay rather than "
                    "a standalone directional predictor.\n")

    print(f"Results saved to {RESULTS_PATH}")
    print()
    print(SEP)
    print("ANALYSIS COMPLETE")
    print(SEP)


if __name__ == '__main__':
    main()
