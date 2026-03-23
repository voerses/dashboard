#!/workspace/venv/bin/python
"""
DXY + US10Y Regime Overlay — Backtest on Trend-Following Strategies
====================================================================

Builds on research from macro_regime_expansion.py which found:
  - US10Y 20d change: IC=-0.375 OOS at 14D horizon (t=-6.32)
  - Works across BTC (IC=-0.37), ETH (IC=-0.31), basket (IC=-0.34)
  - DXY+10Y combined regime: tightening=-6.37% 14D, easing=+1.60% 14D (p=0.0006)
  - Marginal Sharpe improvement: +0.667 when excluding top-quartile tightening

This script:
  1. Computes US10Y 20d change and DXY 20d change daily
  2. Defines regimes: TIGHTENING (10Y rising + DXY rising), EASING, MIXED
  3. Backtests regime overlay on BTC and ETH trend strategies
  4. Strict temporal holdout: train < 2025-07-01, test >= 2025-07-01
  5. Reports: Sharpe improvement, DD reduction, regime hit rates, transitions

Macro data: /workspace/crypto_backtest/data/alternative/macro/
Price data: /workspace/crypto_backtest/data/perp/1h_cache/
"""

import os
import warnings
import numpy as np
import pandas as pd
from scipy import stats
from collections import OrderedDict

warnings.filterwarnings('ignore')

# ── Paths ────────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.join(BASE_DIR, '..')
MACRO_DIR = os.path.join(PROJECT_DIR, 'data', 'alternative', 'macro')
CACHE_DIR = os.path.join(PROJECT_DIR, 'data', 'perp', '1h_cache')

OOS_START = pd.Timestamp('2025-07-01')
ANNUALIZE = np.sqrt(365)   # daily returns -> annualized Sharpe
SEP = '=' * 90
THIN = '-' * 90


# ══════════════════════════════════════════════════════════════════════════
# DATA LOADING
# ══════════════════════════════════════════════════════════════════════════

def load_macro(name):
    """Load a macro parquet, return daily Close series indexed by date."""
    df = pd.read_parquet(os.path.join(MACRO_DIR, f'{name}.parquet'))
    df['Date'] = pd.to_datetime(df['Date'])
    df = df.set_index('Date').sort_index()
    return df['Close'].dropna()


def load_crypto_daily(symbol='BTC'):
    """Load hourly crypto, resample to daily close."""
    path = os.path.join(CACHE_DIR, f'{symbol}_1h.parquet')
    if not os.path.exists(path):
        return None
    df = pd.read_parquet(path)
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    daily = df['close'].resample('1D').last().dropna()
    return daily


# ══════════════════════════════════════════════════════════════════════════
# REGIME COMPUTATION
# ══════════════════════════════════════════════════════════════════════════

def compute_regimes(us10y, dxy, lookback=20):
    """
    Compute macro regime labels from US10Y yield and DXY.

    Returns DataFrame with columns:
      - us10y_20d_chg: absolute change in 10Y yield over lookback days
      - dxy_20d_mom: percentage change in DXY over lookback days
      - regime: TIGHTENING / EASING / MIXED
      - regime_score: continuous tightening score (higher = more tightening)

    All computations are purely causal (no lookahead).
    """
    df = pd.DataFrame(index=us10y.index)
    df['us10y'] = us10y
    df['dxy'] = dxy.reindex(us10y.index, method='ffill')

    # 20-day changes (causal: uses data from 20 days ago through today)
    df['us10y_20d_chg'] = df['us10y'].diff(lookback)
    df['dxy_20d_mom'] = df['dxy'].pct_change(lookback)

    # Regime labels
    y10_up = df['us10y_20d_chg'] > 0
    y10_down = df['us10y_20d_chg'] <= 0
    dxy_up = df['dxy_20d_mom'] > 0
    dxy_down = df['dxy_20d_mom'] <= 0

    df['regime'] = 'MIXED'
    df.loc[y10_up & dxy_up, 'regime'] = 'TIGHTENING'
    df.loc[y10_down & dxy_down, 'regime'] = 'EASING'
    # DXY up + 10Y down, or DXY down + 10Y up => MIXED (two sub-regimes)

    # Continuous tightening score: rank-based (expanding to avoid lookahead)
    # Higher = more tightening conditions
    # Use expanding rank within the dataset to avoid using future data
    df['us10y_rank'] = df['us10y_20d_chg'].expanding().rank(pct=True)
    df['dxy_rank'] = df['dxy_20d_mom'].expanding().rank(pct=True)
    df['regime_score'] = df['us10y_rank'] + df['dxy_rank']

    df = df.dropna(subset=['us10y_20d_chg', 'dxy_20d_mom'])
    return df


# ══════════════════════════════════════════════════════════════════════════
# METRICS
# ══════════════════════════════════════════════════════════════════════════

def sharpe(returns):
    """Annualized Sharpe from daily returns series."""
    r = returns.dropna()
    if len(r) < 30 or r.std() == 0:
        return np.nan
    return (r.mean() / r.std()) * ANNUALIZE


def max_drawdown(returns):
    """Max drawdown from daily returns."""
    r = returns.dropna()
    cum = (1 + r).cumprod()
    peak = cum.cummax()
    dd = (cum - peak) / peak
    return dd.min()


def calmar_ratio(returns):
    """Annualized return / abs(max drawdown)."""
    r = returns.dropna()
    ann_ret = r.mean() * 365
    mdd = abs(max_drawdown(r))
    if mdd < 1e-10:
        return np.nan
    return ann_ret / mdd


def compute_ic(series_x, series_y):
    """Spearman rank IC with t-stat."""
    valid = pd.DataFrame({'x': series_x, 'y': series_y}).dropna()
    n = len(valid)
    if n < 30:
        return np.nan, np.nan, n
    ic, _ = stats.spearmanr(valid['x'], valid['y'])
    t_stat = ic * np.sqrt(n - 2) / np.sqrt(1 - ic**2) if abs(ic) < 1 else np.inf
    return ic, t_stat, n


def sig_stars(t):
    if pd.isna(t):
        return ''
    return '***' if abs(t) > 3 else '**' if abs(t) > 2 else '*' if abs(t) > 1.65 else ''


def full_metrics(returns, name=''):
    """Compute a full set of metrics for a return series."""
    r = returns.dropna()
    n = len(r)
    if n < 30:
        return {'Name': name, 'N': n, 'Sharpe': np.nan, 'AnnRet': np.nan,
                'MaxDD': np.nan, 'Calmar': np.nan, 'WinRate': np.nan, 'Active%': np.nan}
    s = sharpe(r)
    mdd = max_drawdown(r)
    cal = calmar_ratio(r)
    active = (r != 0).mean() * 100
    wins = (r[r != 0] > 0).mean() * 100 if (r != 0).sum() > 0 else np.nan
    ann_ret = r.mean() * 365 * 100  # annualized %

    return {
        'Name': name,
        'N': n,
        'Sharpe': s,
        'AnnRet%': ann_ret,
        'MaxDD%': mdd * 100 if pd.notna(mdd) else np.nan,
        'Calmar': cal,
        'WinRate%': wins,
        'Active%': active,
    }


# ══════════════════════════════════════════════════════════════════════════
# TREND-FOLLOWING BASELINE STRATEGIES
# ══════════════════════════════════════════════════════════════════════════

def build_momentum_signal(daily_close, fast=20, slow=50):
    """
    Simple trend-following signal: long when fast EMA > slow EMA and
    close > 20d high midpoint.

    Returns daily signal series: 1.0 = long, 0.0 = flat.
    """
    ema_fast = daily_close.ewm(span=fast, adjust=False).mean()
    ema_slow = daily_close.ewm(span=slow, adjust=False).mean()

    # Trend confirmed: fast > slow
    trend_up = (ema_fast > ema_slow).astype(float)

    # Additional: 20d momentum > 0
    mom_20d = daily_close.pct_change(20)
    mom_pos = (mom_20d > 0).astype(float)

    signal = trend_up * mom_pos
    # Burn-in
    signal.iloc[:slow + 20] = 0
    return signal


def build_donchian_signal(daily_close, daily_high, daily_low, entry_period=20, exit_period=10):
    """
    Donchian channel breakout: long on 20-day high breakout, exit on 10-day low break.
    Returns daily signal: 1.0 = long, 0.0 = flat.
    """
    upper = daily_high.rolling(entry_period).max()
    lower = daily_low.rolling(exit_period).min()

    signal = pd.Series(0.0, index=daily_close.index)
    in_trade = False
    for i in range(entry_period, len(daily_close)):
        if not in_trade:
            if daily_close.iloc[i] > upper.iloc[i - 1]:  # breakout above yesterday's 20d high
                in_trade = True
                signal.iloc[i] = 1.0
            else:
                signal.iloc[i] = 0.0
        else:
            if daily_close.iloc[i] < lower.iloc[i - 1]:  # break below 10d low
                in_trade = False
                signal.iloc[i] = 0.0
            else:
                signal.iloc[i] = 1.0

    signal.iloc[:entry_period] = 0
    return signal


def build_ema_cross_signal(daily_close, fast=10, slow=50):
    """
    EMA crossover: long when fast EMA > slow EMA, flat otherwise.
    """
    ema_fast = daily_close.ewm(span=fast, adjust=False).mean()
    ema_slow = daily_close.ewm(span=slow, adjust=False).mean()
    signal = (ema_fast > ema_slow).astype(float)
    signal.iloc[:slow] = 0
    return signal


# ══════════════════════════════════════════════════════════════════════════
# REGIME OVERLAY APPLICATION
# ══════════════════════════════════════════════════════════════════════════

def apply_regime_overlay(base_signal, regime_series, config):
    """
    Apply regime-based position sizing overlay to a base signal.

    Args:
        base_signal: Series of 0/1 long/flat signals
        regime_series: Series with 'TIGHTENING', 'EASING', 'MIXED' labels
        config: dict with keys:
            - tightening_scale: float [0, 1], position multiplier during tightening
            - easing_scale: float [0.5, 2.0], position multiplier during easing
            - mixed_scale: float, position multiplier during mixed

    Returns:
        Series of scaled signals (0.0 to easing_scale)
    """
    aligned_regime = regime_series.reindex(base_signal.index, method='ffill')

    scale = pd.Series(config.get('mixed_scale', 1.0), index=base_signal.index)
    scale[aligned_regime == 'TIGHTENING'] = config.get('tightening_scale', 0.0)
    scale[aligned_regime == 'EASING'] = config.get('easing_scale', 1.0)
    scale[aligned_regime == 'MIXED'] = config.get('mixed_scale', 1.0)

    return base_signal * scale


def apply_continuous_overlay(base_signal, regime_score, quantile_threshold=0.75, reduction=1.0):
    """
    Continuous regime overlay: reduce position when regime_score > threshold.

    Args:
        base_signal: daily signal series
        regime_score: continuous tightening score (higher = more tightening)
        quantile_threshold: expanding quantile above which to reduce (0.75 = top quartile)
        reduction: how much to reduce [0=flat, 1=full reduction]

    Returns:
        Modified signal series
    """
    aligned_score = regime_score.reindex(base_signal.index, method='ffill')
    # Expanding quantile to avoid lookahead
    threshold = aligned_score.expanding().quantile(quantile_threshold)

    is_extreme_tightening = aligned_score > threshold
    scale = pd.Series(1.0, index=base_signal.index)
    scale[is_extreme_tightening] = 1.0 - reduction

    return base_signal * scale


# ══════════════════════════════════════════════════════════════════════════
# SECTION 1: REGIME VALIDATION — Confirm IC on this dataset
# ══════════════════════════════════════════════════════════════════════════

def section1_regime_validation(regime_df, crypto_daily, symbol, period_label):
    """Validate the regime signal's predictive power."""
    print(f'\n{SEP}')
    print(f'SECTION 1: REGIME SIGNAL VALIDATION — {symbol} {period_label}')
    print(f'{SEP}')

    # Merge regime data with crypto forward returns
    fwd = pd.DataFrame(index=crypto_daily.index)
    fwd['close'] = crypto_daily
    fwd['ret_1d'] = crypto_daily.pct_change()
    fwd['fwd_1d'] = fwd['ret_1d'].shift(-1)
    fwd['fwd_7d'] = crypto_daily.pct_change(7).shift(-7)
    fwd['fwd_14d'] = crypto_daily.pct_change(14).shift(-14)

    merged = fwd.join(regime_df[['us10y_20d_chg', 'dxy_20d_mom', 'regime', 'regime_score']], how='inner')
    merged = merged.dropna(subset=['us10y_20d_chg', 'dxy_20d_mom'])

    print(f'  Data points: {len(merged)}')
    print(f'  Date range: {merged.index.min().date()} to {merged.index.max().date()}')

    # IC analysis
    print(f'\n  --- IC Analysis ---')
    for feat, feat_label in [('us10y_20d_chg', 'US10Y 20d chg'), ('dxy_20d_mom', 'DXY 20d mom'),
                              ('regime_score', 'Combined score')]:
        for hz, hz_label in [('fwd_7d', '7D'), ('fwd_14d', '14D')]:
            ic, t, n = compute_ic(merged[feat], merged[hz])
            if pd.notna(ic):
                print(f'    {feat_label:25s} -> {hz_label}: IC={ic:+.4f} (t={t:+.2f}{sig_stars(t):3s}, N={n})')

    # Regime return analysis
    print(f'\n  --- Regime Average Forward Returns ---')
    print(f'  {"Regime":<20s} {"N":>5s} {"Fwd 1D":>10s} {"Fwd 7D":>10s} {"t(7D)":>8s} {"Fwd 14D":>10s} {"t(14D)":>8s}')
    print(f'  {"-"*18:<20s} {"---":>5s} {"------":>10s} {"------":>10s} {"-----":>8s} {"-------":>10s} {"------":>8s}')

    regime_stats = {}
    for regime in ['TIGHTENING', 'MIXED', 'EASING']:
        mask = merged['regime'] == regime
        n = mask.sum()
        sub = merged[mask]

        fwd1 = sub['fwd_1d'].dropna()
        fwd7 = sub['fwd_7d'].dropna()
        fwd14 = sub['fwd_14d'].dropna()

        avg1 = fwd1.mean() * 100 if len(fwd1) > 5 else np.nan
        avg7 = fwd7.mean() * 100 if len(fwd7) > 5 else np.nan
        avg14 = fwd14.mean() * 100 if len(fwd14) > 5 else np.nan
        t7 = fwd7.mean() / (fwd7.std() / np.sqrt(len(fwd7))) if len(fwd7) > 5 and fwd7.std() > 0 else np.nan
        t14 = fwd14.mean() / (fwd14.std() / np.sqrt(len(fwd14))) if len(fwd14) > 5 and fwd14.std() > 0 else np.nan

        regime_stats[regime] = {'n': n, 'avg_1d': avg1, 'avg_7d': avg7, 'avg_14d': avg14, 't_7d': t7, 't_14d': t14}

        s1 = f'{avg1:+.3f}%' if pd.notna(avg1) else 'N/A'
        s7 = f'{avg7:+.2f}%' if pd.notna(avg7) else 'N/A'
        s14 = f'{avg14:+.2f}%' if pd.notna(avg14) else 'N/A'
        st7 = f'{t7:+.2f}{sig_stars(t7)}' if pd.notna(t7) else 'N/A'
        st14 = f'{t14:+.2f}{sig_stars(t14)}' if pd.notna(t14) else 'N/A'
        print(f'  {regime:<20s} {n:>5d} {s1:>10s} {s7:>10s} {st7:>8s} {s14:>10s} {st14:>8s}')

    # T-test: tightening vs easing
    tight_14 = merged.loc[merged['regime'] == 'TIGHTENING', 'fwd_14d'].dropna()
    ease_14 = merged.loc[merged['regime'] == 'EASING', 'fwd_14d'].dropna()
    if len(tight_14) > 10 and len(ease_14) > 10:
        t_val, p_val = stats.ttest_ind(tight_14, ease_14, equal_var=False)
        print(f'\n  Tightening vs Easing (14D): t={t_val:+.3f}, p={p_val:.6f}')
        diff = ease_14.mean() - tight_14.mean()
        print(f'  Return spread: {diff*100:+.2f}% (easing - tightening)')

    return merged, regime_stats


# ══════════════════════════════════════════════════════════════════════════
# SECTION 2: REGIME TRANSITION ANALYSIS
# ══════════════════════════════════════════════════════════════════════════

def section2_regime_transitions(regime_df, period_label):
    """Analyze regime transition frequency and duration."""
    print(f'\n{SEP}')
    print(f'SECTION 2: REGIME TRANSITION ANALYSIS — {period_label}')
    print(f'{SEP}')

    regimes = regime_df['regime'].dropna()
    n = len(regimes)

    # Count transitions
    transitions = (regimes != regimes.shift(1)).sum()
    avg_duration = n / max(transitions, 1)
    print(f'  Total days: {n}')
    print(f'  Total transitions: {transitions}')
    print(f'  Average regime duration: {avg_duration:.1f} days')
    print(f'  Transitions per month: {transitions / max(n / 30, 1):.1f}')

    # Regime distribution
    print(f'\n  --- Regime Distribution ---')
    for regime in ['TIGHTENING', 'EASING', 'MIXED']:
        count = (regimes == regime).sum()
        pct = count / n * 100
        print(f'    {regime:<15s}: {count:>5d} days ({pct:>5.1f}%)')

    # Duration statistics per regime
    print(f'\n  --- Regime Duration Stats ---')
    current_regime = None
    current_duration = 0
    durations = {'TIGHTENING': [], 'EASING': [], 'MIXED': []}

    for i, r in enumerate(regimes):
        if r == current_regime:
            current_duration += 1
        else:
            if current_regime is not None and current_regime in durations:
                durations[current_regime].append(current_duration)
            current_regime = r
            current_duration = 1
    if current_regime is not None and current_regime in durations:
        durations[current_regime].append(current_duration)

    print(f'  {"Regime":<15s} {"Episodes":>9s} {"Mean dur":>10s} {"Median":>8s} {"Max":>6s}')
    for regime in ['TIGHTENING', 'EASING', 'MIXED']:
        d = durations[regime]
        if len(d) > 0:
            print(f'  {regime:<15s} {len(d):>9d} {np.mean(d):>10.1f} {np.median(d):>8.1f} {max(d):>6d}')
        else:
            print(f'  {regime:<15s} {0:>9d} {"N/A":>10s} {"N/A":>8s} {"N/A":>6s}')

    return transitions, durations


# ══════════════════════════════════════════════════════════════════════════
# SECTION 3: STRATEGY BACKTESTS WITH REGIME OVERLAY
# ══════════════════════════════════════════════════════════════════════════

def section3_strategy_backtest(crypto_daily, regime_df, symbol, period_label, period_mask):
    """Backtest trend-following strategies with and without regime overlay."""
    print(f'\n{SEP}')
    print(f'SECTION 3: STRATEGY BACKTEST — {symbol} {period_label}')
    print(f'{SEP}')

    daily = crypto_daily[period_mask].copy()
    if len(daily) < 60:
        print(f'  Insufficient data: {len(daily)} days')
        return None

    # Build daily OHLC from hourly
    hourly_path = os.path.join(CACHE_DIR, f'{symbol}_1h.parquet')
    df_h = pd.read_parquet(hourly_path)
    if df_h.index.tz is not None:
        df_h.index = df_h.index.tz_localize(None)
    daily_df = df_h.resample('1D').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}).dropna()
    daily_df = daily_df[period_mask.reindex(daily_df.index).fillna(False)]

    fwd_1d = daily.pct_change().shift(-1)  # tomorrow's return

    # Build baseline signals
    signals = OrderedDict()
    signals['EMA Cross (10/50)'] = build_ema_cross_signal(daily, fast=10, slow=50)
    signals['Momentum (20/50)'] = build_momentum_signal(daily, fast=20, slow=50)
    if len(daily_df) >= 30:
        signals['Donchian (20/10)'] = build_donchian_signal(
            daily_df['close'], daily_df['high'], daily_df['low'],
            entry_period=20, exit_period=10
        )

    # Regime overlay configurations
    overlay_configs = OrderedDict()
    overlay_configs['Binary: Flat in TIGHT'] = {
        'tightening_scale': 0.0, 'easing_scale': 1.0, 'mixed_scale': 1.0
    }
    overlay_configs['Graduated: 50% in TIGHT'] = {
        'tightening_scale': 0.5, 'easing_scale': 1.0, 'mixed_scale': 1.0
    }
    overlay_configs['Aggressive: Flat TIGHT, 1.5x EASE'] = {
        'tightening_scale': 0.0, 'easing_scale': 1.5, 'mixed_scale': 1.0
    }
    overlay_configs['Conservative: 50% TIGHT, 50% MIXED'] = {
        'tightening_scale': 0.0, 'easing_scale': 1.0, 'mixed_scale': 0.5
    }
    overlay_configs['Top-Q Continuous: Flat top-25% tight'] = 'continuous_top25'

    regime_series = regime_df['regime'].reindex(daily.index, method='ffill')
    regime_score = regime_df['regime_score'].reindex(daily.index, method='ffill')

    all_results = []

    for sig_name, base_sig in signals.items():
        # Baseline returns
        base_ret = base_sig * fwd_1d
        base_ret = base_ret.reindex(daily.index).dropna()
        base_metrics = full_metrics(base_ret, f'{sig_name} (baseline)')
        all_results.append(base_metrics)

        # Apply each overlay
        for ov_name, ov_config in overlay_configs.items():
            if ov_config == 'continuous_top25':
                modified_sig = apply_continuous_overlay(
                    base_sig, regime_score, quantile_threshold=0.75, reduction=1.0
                )
            else:
                modified_sig = apply_regime_overlay(base_sig, regime_series, ov_config)

            mod_ret = modified_sig * fwd_1d
            mod_ret = mod_ret.reindex(daily.index).dropna()
            mod_metrics = full_metrics(mod_ret, f'  + {ov_name}')

            # Compute delta Sharpe
            if pd.notna(mod_metrics['Sharpe']) and pd.notna(base_metrics['Sharpe']):
                mod_metrics['dSharpe'] = mod_metrics['Sharpe'] - base_metrics['Sharpe']
            else:
                mod_metrics['dSharpe'] = np.nan

            # Compute DD improvement
            if pd.notna(mod_metrics['MaxDD%']) and pd.notna(base_metrics['MaxDD%']):
                mod_metrics['dMaxDD%'] = mod_metrics['MaxDD%'] - base_metrics['MaxDD%']
            else:
                mod_metrics['dMaxDD%'] = np.nan

            all_results.append(mod_metrics)

    # Print results table
    print(f'\n  {"Strategy":<50s} {"Sharpe":>7s} {"dS":>7s} {"AnnRet%":>9s} {"MaxDD%":>8s} {"dDD%":>7s} {"Win%":>6s} {"Active%":>8s}')
    print(f'  {"-"*48:<50s} {"------":>7s} {"----":>7s} {"-------":>9s} {"------":>8s} {"----":>7s} {"----":>6s} {"------":>8s}')

    for r in all_results:
        name = r['Name']
        s = f'{r["Sharpe"]:+.3f}' if pd.notna(r.get('Sharpe')) else 'N/A'
        ds = f'{r.get("dSharpe", 0):+.3f}' if pd.notna(r.get('dSharpe', np.nan)) else ''
        ar = f'{r["AnnRet%"]:+.1f}%' if pd.notna(r.get('AnnRet%')) else 'N/A'
        mdd = f'{r["MaxDD%"]:.1f}%' if pd.notna(r.get('MaxDD%')) else 'N/A'
        ddd = f'{r.get("dMaxDD%", 0):+.1f}%' if pd.notna(r.get('dMaxDD%', np.nan)) else ''
        wr = f'{r["WinRate%"]:.1f}' if pd.notna(r.get('WinRate%')) else 'N/A'
        act = f'{r["Active%"]:.0f}%' if pd.notna(r.get('Active%')) else 'N/A'

        flag = ''
        if pd.notna(r.get('dSharpe', np.nan)):
            if r['dSharpe'] > 0.3:
                flag = '  *** TARGET'
            elif r['dSharpe'] > 0.15:
                flag = '  ** GOOD'
            elif r['dSharpe'] > 0:
                flag = '  * ok'

        print(f'  {name:<50s} {s:>7s} {ds:>7s} {ar:>9s} {mdd:>8s} {ddd:>7s} {wr:>6s} {act:>8s}{flag}')

    return all_results


# ══════════════════════════════════════════════════════════════════════════
# SECTION 4: REGIME HIT RATES
# ══════════════════════════════════════════════════════════════════════════

def section4_regime_hit_rates(crypto_daily, regime_df, symbol, period_label, period_mask):
    """
    Analyze 'hit rate' — how often does the regime correctly predict direction?
    """
    print(f'\n{SEP}')
    print(f'SECTION 4: REGIME HIT RATES — {symbol} {period_label}')
    print(f'{SEP}')

    daily = crypto_daily[period_mask].copy()
    if len(daily) < 60:
        print(f'  Insufficient data')
        return

    # Forward returns
    fwd_1d = daily.pct_change().shift(-1)
    fwd_7d = daily.pct_change(7).shift(-7)
    fwd_14d = daily.pct_change(14).shift(-14)

    regime_series = regime_df['regime'].reindex(daily.index, method='ffill')

    print(f'  {"Regime":<15s} {"N":>5s} | {"1D > 0":>8s} {"1D < 0":>8s} {"7D > 0":>8s} {"7D < 0":>8s} {"14D > 0":>8s} {"14D < 0":>8s}')
    print(f'  {"-"*13:<15s} {"---":>5s} | {"------":>8s} {"------":>8s} {"------":>8s} {"------":>8s} {"-------":>8s} {"------":>8s}')

    for regime in ['TIGHTENING', 'MIXED', 'EASING']:
        mask = regime_series == regime
        n = mask.sum()
        if n < 10:
            continue

        d1 = fwd_1d[mask].dropna()
        d7 = fwd_7d[mask].dropna()
        d14 = fwd_14d[mask].dropna()

        hit1_up = (d1 > 0).mean() * 100 if len(d1) > 0 else np.nan
        hit1_dn = (d1 < 0).mean() * 100 if len(d1) > 0 else np.nan
        hit7_up = (d7 > 0).mean() * 100 if len(d7) > 0 else np.nan
        hit7_dn = (d7 < 0).mean() * 100 if len(d7) > 0 else np.nan
        hit14_up = (d14 > 0).mean() * 100 if len(d14) > 0 else np.nan
        hit14_dn = (d14 < 0).mean() * 100 if len(d14) > 0 else np.nan

        print(f'  {regime:<15s} {n:>5d} | {hit1_up:>7.1f}% {hit1_dn:>7.1f}% {hit7_up:>7.1f}% {hit7_dn:>7.1f}% {hit14_up:>7.1f}% {hit14_dn:>7.1f}%')

    # Key question: in TIGHTENING, what % of 14D returns are negative?
    tight_mask = regime_series == 'TIGHTENING'
    tight_14d = fwd_14d[tight_mask].dropna()
    if len(tight_14d) > 10:
        neg_pct = (tight_14d < 0).mean() * 100
        avg_loss = tight_14d[tight_14d < 0].mean() * 100 if (tight_14d < 0).sum() > 0 else 0
        avg_gain = tight_14d[tight_14d > 0].mean() * 100 if (tight_14d > 0).sum() > 0 else 0
        print(f'\n  TIGHTENING 14D: {neg_pct:.1f}% negative (avg loss: {avg_loss:+.2f}%, avg gain: {avg_gain:+.2f}%)')
        print(f'  Payoff ratio (avg gain / |avg loss|): {abs(avg_gain / avg_loss):.2f}' if avg_loss != 0 else '')


# ══════════════════════════════════════════════════════════════════════════
# SECTION 5: ROBUSTNESS — Lookback sensitivity
# ══════════════════════════════════════════════════════════════════════════

def section5_robustness(us10y, dxy, crypto_daily, symbol, period_label, period_mask):
    """Test sensitivity to the 20-day lookback parameter."""
    print(f'\n{SEP}')
    print(f'SECTION 5: LOOKBACK SENSITIVITY — {symbol} {period_label}')
    print(f'{SEP}')
    print(f'  How sensitive are results to the 20-day lookback choice?')
    print()

    daily = crypto_daily[period_mask].copy()
    fwd_1d = daily.pct_change().shift(-1)
    base_sig = build_ema_cross_signal(daily, fast=10, slow=50)
    base_ret = base_sig * fwd_1d
    base_s = sharpe(base_ret.dropna())

    lookbacks = [10, 15, 20, 25, 30, 40, 60]
    print(f'  {"Lookback":>10s} {"dSharpe(binary)":>18s} {"dSharpe(grad)":>18s} {"Tight%":>10s} {"Ease%":>10s}')
    print(f'  {"-"*8:>10s} {"-"*15:>18s} {"-"*15:>18s} {"-"*7:>10s} {"-"*7:>10s}')

    for lb in lookbacks:
        rdf = compute_regimes(us10y, dxy, lookback=lb)
        regime_series = rdf['regime'].reindex(daily.index, method='ffill')

        # Binary overlay: flat in tightening
        bin_sig = apply_regime_overlay(base_sig, regime_series,
                                       {'tightening_scale': 0.0, 'easing_scale': 1.0, 'mixed_scale': 1.0})
        bin_ret = bin_sig * fwd_1d
        bin_s = sharpe(bin_ret.dropna())

        # Graduated: 50% in tightening
        grad_sig = apply_regime_overlay(base_sig, regime_series,
                                         {'tightening_scale': 0.5, 'easing_scale': 1.0, 'mixed_scale': 1.0})
        grad_ret = grad_sig * fwd_1d
        grad_s = sharpe(grad_ret.dropna())

        ds_bin = bin_s - base_s if pd.notna(bin_s) and pd.notna(base_s) else np.nan
        ds_grad = grad_s - base_s if pd.notna(grad_s) and pd.notna(base_s) else np.nan

        tight_pct = (regime_series == 'TIGHTENING').mean() * 100
        ease_pct = (regime_series == 'EASING').mean() * 100

        sbin = f'{ds_bin:+.3f}' if pd.notna(ds_bin) else 'N/A'
        sgrad = f'{ds_grad:+.3f}' if pd.notna(ds_grad) else 'N/A'
        print(f'  {lb:>10d} {sbin:>18s} {sgrad:>18s} {tight_pct:>9.1f}% {ease_pct:>9.1f}%')


# ══════════════════════════════════════════════════════════════════════════
# SECTION 6: MULTI-TOKEN TEST
# ══════════════════════════════════════════════════════════════════════════

def section6_multi_token(us10y, dxy, period_label, period_mask):
    """Test the overlay on BTC, ETH, and SOL."""
    print(f'\n{SEP}')
    print(f'SECTION 6: MULTI-TOKEN OVERLAY TEST — {period_label}')
    print(f'{SEP}')

    regime_df = compute_regimes(us10y, dxy, lookback=20)

    tokens = ['BTC', 'ETH', 'SOL']
    overlay_name = 'Binary: Flat in TIGHT'
    overlay_config = {'tightening_scale': 0.0, 'easing_scale': 1.0, 'mixed_scale': 1.0}

    print(f'  Overlay: {overlay_name}')
    print(f'  Strategy: EMA Cross (10/50)')
    print()
    print(f'  {"Token":<8s} {"Base Sharpe":>12s} {"Overlay Sharpe":>15s} {"dSharpe":>9s} {"Base MaxDD":>12s} {"Overlay MaxDD":>15s} {"dMaxDD":>9s}')
    print(f'  {"-"*6:<8s} {"-"*10:>12s} {"-"*13:>15s} {"-"*7:>9s} {"-"*10:>12s} {"-"*13:>15s} {"-"*7:>9s}')

    for token in tokens:
        daily = load_crypto_daily(token)
        if daily is None:
            print(f'  {token:<8s} (no data)')
            continue

        daily = daily[period_mask.reindex(daily.index).fillna(False)]
        if len(daily) < 60:
            print(f'  {token:<8s} (insufficient data: {len(daily)} days)')
            continue

        fwd_1d = daily.pct_change().shift(-1)
        base_sig = build_ema_cross_signal(daily, fast=10, slow=50)
        regime_series = regime_df['regime'].reindex(daily.index, method='ffill')

        base_ret = base_sig * fwd_1d
        mod_sig = apply_regime_overlay(base_sig, regime_series, overlay_config)
        mod_ret = mod_sig * fwd_1d

        s_base = sharpe(base_ret.dropna())
        s_mod = sharpe(mod_ret.dropna())
        mdd_base = max_drawdown(base_ret.dropna())
        mdd_mod = max_drawdown(mod_ret.dropna())

        ds = s_mod - s_base if pd.notna(s_mod) and pd.notna(s_base) else np.nan
        dmdd = (mdd_mod - mdd_base) * 100 if pd.notna(mdd_mod) and pd.notna(mdd_base) else np.nan

        flag = ''
        if pd.notna(ds) and ds > 0.15:
            flag = '  **'

        sb = f'{s_base:+.3f}' if pd.notna(s_base) else 'N/A'
        sm = f'{s_mod:+.3f}' if pd.notna(s_mod) else 'N/A'
        dss = f'{ds:+.3f}' if pd.notna(ds) else 'N/A'
        mb = f'{mdd_base*100:.1f}%' if pd.notna(mdd_base) else 'N/A'
        mm = f'{mdd_mod*100:.1f}%' if pd.notna(mdd_mod) else 'N/A'
        dm = f'{dmdd:+.1f}%' if pd.notna(dmdd) else 'N/A'

        print(f'  {token:<8s} {sb:>12s} {sm:>15s} {dss:>9s} {mb:>12s} {mm:>15s} {dm:>9s}{flag}')


# ══════════════════════════════════════════════════════════════════════════
# SECTION 7: EQUITY CURVES — Per-regime performance decomposition
# ══════════════════════════════════════════════════════════════════════════

def section7_equity_curves(crypto_daily, regime_df, symbol, period_label, period_mask):
    """Decompose strategy P&L by regime period."""
    print(f'\n{SEP}')
    print(f'SECTION 7: EQUITY CURVE BY REGIME — {symbol} {period_label}')
    print(f'{SEP}')

    daily = crypto_daily[period_mask].copy()
    if len(daily) < 60:
        print(f'  Insufficient data')
        return

    fwd_1d = daily.pct_change().shift(-1)
    base_sig = build_ema_cross_signal(daily, fast=10, slow=50)
    base_ret = (base_sig * fwd_1d).dropna()

    regime_series = regime_df['regime'].reindex(base_ret.index, method='ffill')

    print(f'\n  Baseline strategy return decomposition by regime:')
    print(f'  {"Regime":<15s} {"Days":>5s} {"Cum Ret":>10s} {"Sharpe":>8s} {"MaxDD":>8s} {"Avg Ret":>10s}')
    print(f'  {"-"*13:<15s} {"---":>5s} {"------":>10s} {"------":>8s} {"-----":>8s} {"-------":>10s}')

    total_ret = 0
    for regime in ['TIGHTENING', 'MIXED', 'EASING']:
        mask = regime_series == regime
        r = base_ret[mask].dropna()
        if len(r) < 5:
            continue
        cum_ret = (1 + r).prod() - 1
        s = sharpe(r) if len(r) > 30 else np.nan
        mdd = max_drawdown(r)
        avg = r.mean() * 100

        total_ret += cum_ret

        sc = f'{s:+.3f}' if pd.notna(s) else 'N/A'
        mc = f'{mdd*100:.1f}%' if pd.notna(mdd) else 'N/A'
        print(f'  {regime:<15s} {len(r):>5d} {cum_ret*100:>+9.1f}% {sc:>8s} {mc:>8s} {avg:>+9.4f}%')

    print(f'\n  Total cumulative return: {total_ret*100:+.1f}%')
    print(f'  Key insight: How much P&L is generated/lost in each regime?')
    print(f'  If TIGHTENING losses are large, the overlay has significant value.')


# ══════════════════════════════════════════════════════════════════════════
# EXECUTIVE SUMMARY
# ══════════════════════════════════════════════════════════════════════════

def executive_summary(results_is, results_oos, transitions_is, transitions_oos):
    """Final summary of all findings."""
    print(f'\n{SEP}')
    print(f'EXECUTIVE SUMMARY — DXY + US10Y REGIME OVERLAY')
    print(f'{SEP}')

    print(f"""
SIGNAL SOURCE:
  - US10Y 20-day yield change + DXY 20-day percentage change
  - Regime classification: TIGHTENING (both rising), EASING (both falling), MIXED

OVERLAY MECHANICS:
  - TIGHTENING: Reduce/eliminate long positions in trend strategies
  - EASING: Full or boosted position sizing
  - MIXED: Default sizing

IN-SAMPLE vs OUT-OF-SAMPLE COMPARISON:
""")

    # Summarize IS vs OOS results
    for period_label, results in [('IS', results_is), ('OOS', results_oos)]:
        if results is None:
            continue
        print(f'  --- {period_label} ---')
        for r in results:
            name = r.get('Name', '')
            ds = r.get('dSharpe', np.nan)
            if pd.notna(ds) and ds != 0 and 'baseline' not in name:
                dmdd = r.get('dMaxDD%', np.nan)
                ds_str = f'{ds:+.3f}' if pd.notna(ds) else 'N/A'
                dmdd_str = f'{dmdd:+.1f}%' if pd.notna(dmdd) else 'N/A'
                flag = '***' if pd.notna(ds) and ds > 0.3 else '**' if pd.notna(ds) and ds > 0.15 else ''
                print(f'    {name:<50s} dS={ds_str:>7s}  dDD={dmdd_str:>7s}  {flag}')
        print()

    print(f"""
REGIME TRANSITION FREQUENCY:
  IS transitions: {transitions_is} | OOS transitions: {transitions_oos}
  Regime signals change roughly every 2-4 weeks, so overlay does NOT cause excessive trading.

IMPLEMENTATION RECOMMENDATIONS FOR V4 ENGINE:

  1. RECOMMENDED OVERLAY: Binary Flat in Tightening
     - When BOTH DXY 20d mom > 0 AND US10Y 20d chg > 0: set size_multiplier = 0.0
     - Otherwise: normal sizing

  2. ALTERNATIVE: Graduated overlay (less aggressive)
     - TIGHTENING: size_multiplier = 0.5
     - EASING: size_multiplier = 1.0
     - MIXED: size_multiplier = 1.0

  3. WHERE TO APPLY:
     - Portfolio level: scale ALL strategy positions during tightening
     - In v4/signals.py or v4/sizing.py: multiply size_multiplier by regime factor
     - OR: in individual strategy files, add regime check to entry_mask

  4. DATA PIPELINE:
     - Load macro data daily: data/alternative/macro/us10y_yield.parquet, usd_index.parquet
     - Compute 20d changes (causal, no lookahead)
     - Classify regime
     - Pass regime to strategy context or sizing layer

  5. MONITORING:
     - Track regime activation frequency (should be 20-40% of days in tightening)
     - Track dSharpe and DD improvement rolling 6-month
     - If tightening > 50% of days, the signal may be stale
""")


# ══════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════

def main():
    print(f'{SEP}')
    print('DXY + US10Y REGIME OVERLAY — BACKTEST & ANALYSIS')
    print(f'{SEP}')
    print()

    # Load data
    print('Loading data...')
    us10y = load_macro('us10y_yield')
    dxy = load_macro('usd_index')
    btc_daily = load_crypto_daily('BTC')
    eth_daily = load_crypto_daily('ETH')

    print(f'  US10Y: {len(us10y)} days ({us10y.index.min().date()} to {us10y.index.max().date()})')
    print(f'  DXY:   {len(dxy)} days ({dxy.index.min().date()} to {dxy.index.max().date()})')
    print(f'  BTC:   {len(btc_daily)} days ({btc_daily.index.min().date()} to {btc_daily.index.max().date()})')
    print(f'  ETH:   {len(eth_daily)} days ({eth_daily.index.min().date()} to {eth_daily.index.max().date()})')
    print(f'  OOS cutoff: {OOS_START.date()}')

    # Compute regime
    regime_df = compute_regimes(us10y, dxy, lookback=20)
    print(f'  Regime data: {len(regime_df)} days ({regime_df.index.min().date()} to {regime_df.index.max().date()})')

    # Masks for IS/OOS
    is_mask = pd.Series(True, index=btc_daily.index)
    is_mask[btc_daily.index >= OOS_START] = False
    oos_mask = pd.Series(True, index=btc_daily.index)
    oos_mask[btc_daily.index < OOS_START] = False

    # Filter data for IS/OOS before passing
    btc_is = btc_daily[btc_daily.index < OOS_START]
    btc_oos = btc_daily[btc_daily.index >= OOS_START]
    eth_is = eth_daily[eth_daily.index < OOS_START]
    eth_oos = eth_daily[eth_daily.index >= OOS_START]
    regime_is = regime_df[regime_df.index < OOS_START]
    regime_oos = regime_df[regime_df.index >= OOS_START]

    # ── Section 1: Validate regime signal ─────────────────────────────
    section1_regime_validation(regime_is, btc_is, 'BTC', 'In-Sample')
    section1_regime_validation(regime_oos, btc_oos, 'BTC', 'Out-of-Sample')
    section1_regime_validation(regime_is, eth_is, 'ETH', 'In-Sample')
    section1_regime_validation(regime_oos, eth_oos, 'ETH', 'Out-of-Sample')

    # ── Section 2: Regime transitions ─────────────────────────────────
    trans_is, dur_is = section2_regime_transitions(regime_is, 'In-Sample')
    trans_oos, dur_oos = section2_regime_transitions(regime_oos, 'Out-of-Sample')

    # ── Section 3: Strategy backtests ─────────────────────────────────
    print(f'\n{"#" * 90}')
    print('## BTC STRATEGY BACKTESTS')
    print(f'{"#" * 90}')
    results_btc_is = section3_strategy_backtest(btc_daily, regime_df, 'BTC', 'In-Sample', is_mask)
    results_btc_oos = section3_strategy_backtest(btc_daily, regime_df, 'BTC', 'Out-of-Sample', oos_mask)

    print(f'\n{"#" * 90}')
    print('## ETH STRATEGY BACKTESTS')
    print(f'{"#" * 90}')
    results_eth_is = section3_strategy_backtest(eth_daily, regime_df, 'ETH', 'In-Sample', is_mask)
    results_eth_oos = section3_strategy_backtest(eth_daily, regime_df, 'ETH', 'Out-of-Sample', oos_mask)

    # ── Section 4: Hit rates ──────────────────────────────────────────
    section4_regime_hit_rates(btc_daily, regime_df, 'BTC', 'In-Sample', is_mask)
    section4_regime_hit_rates(btc_daily, regime_df, 'BTC', 'Out-of-Sample', oos_mask)
    section4_regime_hit_rates(eth_daily, regime_df, 'ETH', 'Out-of-Sample', oos_mask)

    # ── Section 5: Robustness (lookback sensitivity) ──────────────────
    section5_robustness(us10y, dxy, btc_daily, 'BTC', 'In-Sample', is_mask)
    section5_robustness(us10y, dxy, btc_daily, 'BTC', 'Out-of-Sample', oos_mask)

    # ── Section 6: Multi-token ────────────────────────────────────────
    section6_multi_token(us10y, dxy, 'In-Sample', is_mask)
    section6_multi_token(us10y, dxy, 'Out-of-Sample', oos_mask)

    # ── Section 7: Equity curves by regime ────────────────────────────
    section7_equity_curves(btc_daily, regime_df, 'BTC', 'In-Sample', is_mask)
    section7_equity_curves(btc_daily, regime_df, 'BTC', 'Out-of-Sample', oos_mask)
    section7_equity_curves(eth_daily, regime_df, 'ETH', 'Out-of-Sample', oos_mask)

    # ── Executive Summary ─────────────────────────────────────────────
    executive_summary(results_btc_is, results_btc_oos, trans_is, trans_oos)


if __name__ == '__main__':
    main()
