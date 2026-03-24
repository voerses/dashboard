#!/workspace/venv/bin/python
"""
BTC-Alt Rolling Correlation as Regime Conditioner for Macro Signals
====================================================================

HYPOTHESIS: When BTC-alt correlations are high (>0.7), the market moves as
a bloc and macro signals (DXY, 10Y) should be stronger predictors. When
correlations break down (<0.5), idiosyncratic factors dominate and macro
signals lose power.

Steps:
  1. Load hourly price data for BTC + 5 major alts (ETH, SOL, DOGE, XRP, LINK)
  2. Compute 30-day rolling correlation between BTC and each alt (daily returns)
  3. Average cross-correlation = "correlation regime" indicator
  4. Split: HIGH (>0.7), MEDIUM (0.4-0.7), LOW (<0.4)
  5. For each regime, compute IC of top 3 signals:
     - US10Y 20d change
     - Skew_30d (from BTC daily returns)
     - DXY 20d momentum
  6. Compare IC by regime (IS vs OOS)
  7. Test correlation regime itself as a predictive signal

Temporal split: train < 2025-07-01, test >= 2025-07-01
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
ANNUALIZE = np.sqrt(365)
SEP = '=' * 90
THIN = '-' * 90

ALTS = ['ETH', 'SOL', 'DOGE', 'XRP', 'LINK']
CORR_WINDOW = 30  # 30-day rolling window for correlations


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
# STEP 1-3: CORRELATION REGIME COMPUTATION
# ══════════════════════════════════════════════════════════════════════════

def compute_correlation_regime():
    """
    Compute 30-day rolling BTC-alt correlations and average cross-correlation.

    Returns DataFrame indexed by date with columns:
      - corr_{ALT}: pairwise BTC-alt rolling correlation
      - avg_corr: mean of all pairwise correlations (the regime indicator)
      - regime: HIGH / MEDIUM / LOW
    """
    # Load BTC daily returns
    btc_daily = load_crypto_daily('BTC')
    btc_ret = btc_daily.pct_change().dropna()
    btc_ret.name = 'BTC'

    # Load alt daily returns
    alt_rets = {}
    for alt in ALTS:
        alt_daily = load_crypto_daily(alt)
        if alt_daily is not None:
            alt_ret = alt_daily.pct_change().dropna()
            alt_ret.name = alt
            alt_rets[alt] = alt_ret

    # Align all returns to common dates
    all_rets = pd.DataFrame({'BTC': btc_ret})
    for alt, ret in alt_rets.items():
        all_rets[alt] = ret
    all_rets = all_rets.dropna()

    print(f"[DATA] Combined returns: {len(all_rets)} days, "
          f"{all_rets.index.min().date()} to {all_rets.index.max().date()}")
    print(f"[DATA] Tokens: BTC + {list(alt_rets.keys())}")

    # Compute 30-day rolling pairwise correlation of each alt with BTC
    result = pd.DataFrame(index=all_rets.index)
    corr_cols = []
    for alt in alt_rets.keys():
        col = f'corr_{alt}'
        result[col] = all_rets['BTC'].rolling(CORR_WINDOW).corr(all_rets[alt])
        corr_cols.append(col)

    # Average cross-correlation
    result['avg_corr'] = result[corr_cols].mean(axis=1)

    # Regime labels
    result['regime'] = 'MEDIUM'
    result.loc[result['avg_corr'] > 0.7, 'regime'] = 'HIGH'
    result.loc[result['avg_corr'] < 0.4, 'regime'] = 'LOW'

    result = result.dropna(subset=['avg_corr'])

    return result, all_rets


# ══════════════════════════════════════════════════════════════════════════
# STEP 4-5: SIGNAL COMPUTATION AND IC BY REGIME
# ══════════════════════════════════════════════════════════════════════════

def compute_signals_and_targets(corr_df, all_rets):
    """
    Build a master DataFrame with correlation regime, signals, and forward returns.

    Signals:
      - us10y_20d_chg: 20-day change in US 10Y yield
      - skew_30d: 30-day rolling skewness of BTC daily returns
      - dxy_20d_mom: 20-day percentage change in DXY

    Targets:
      - btc_fwd_1d, btc_fwd_5d, btc_fwd_14d
    """
    # Load macro data
    us10y = load_macro('us10y_yield')
    dxy = load_macro('usd_index')

    # Build master
    master = corr_df.copy()

    # BTC daily close and returns for forward return computation
    btc_daily = load_crypto_daily('BTC')
    master['btc_close'] = btc_daily.reindex(master.index, method='ffill')
    master['btc_ret_1d'] = all_rets['BTC'].reindex(master.index)

    # === Signals (all causal — no lookahead) ===

    # 1. US10Y 20d change
    us10y_aligned = us10y.reindex(master.index, method='ffill')
    master['us10y_20d_chg'] = us10y_aligned.diff(20)

    # 2. Skew 30d (rolling skewness of BTC daily returns)
    master['skew_30d'] = master['btc_ret_1d'].rolling(30).skew()

    # 3. DXY 20d momentum
    dxy_aligned = dxy.reindex(master.index, method='ffill')
    master['dxy_20d_mom'] = dxy_aligned.pct_change(20)

    # === Forward returns (targets — shifted to avoid lookahead) ===
    master['btc_fwd_1d'] = master['btc_ret_1d'].shift(-1)
    master['btc_fwd_5d'] = master['btc_close'].pct_change(5).shift(-5)
    master['btc_fwd_14d'] = master['btc_close'].pct_change(14).shift(-14)

    # Drop rows without all needed data
    master = master.dropna(subset=['us10y_20d_chg', 'skew_30d', 'dxy_20d_mom',
                                    'btc_fwd_1d', 'avg_corr'])

    print(f"\n[MASTER] {len(master)} rows, {master.index.min().date()} to {master.index.max().date()}")

    return master


def compute_ic(df, signal_col, target_col):
    """Compute Spearman rank IC with t-stat."""
    valid = df[[signal_col, target_col]].dropna()
    if len(valid) < 30:
        return np.nan, np.nan, len(valid)
    ic, _ = stats.spearmanr(valid[signal_col], valid[target_col])
    n = len(valid)
    t_stat = ic * np.sqrt(n - 2) / np.sqrt(1 - ic**2) if abs(ic) < 1 else np.inf
    return ic, t_stat, n


def compute_rolling_ic(df, signal_col, target_col, window=60):
    """Compute rolling IC over a window for stability analysis."""
    ics = []
    dates = []
    for i in range(window, len(df)):
        subset = df.iloc[i-window:i]
        valid = subset[[signal_col, target_col]].dropna()
        if len(valid) >= 20:
            ic, _ = stats.spearmanr(valid[signal_col], valid[target_col])
            ics.append(ic)
        else:
            ics.append(np.nan)
        dates.append(df.index[i])
    return pd.Series(ics, index=dates)


# ══════════════════════════════════════════════════════════════════════════
# ANALYSIS SECTIONS
# ══════════════════════════════════════════════════════════════════════════

def section1_correlation_regime_descriptives(master):
    """Describe the correlation regime indicator."""
    print(f'\n{SEP}')
    print('SECTION 1: CORRELATION REGIME DESCRIPTIVES')
    print(SEP)

    # Overall statistics
    print(f'\nAverage BTC-Alt Correlation (30d rolling):')
    print(f'  Mean:   {master["avg_corr"].mean():.3f}')
    print(f'  Median: {master["avg_corr"].median():.3f}')
    print(f'  Std:    {master["avg_corr"].std():.3f}')
    print(f'  Min:    {master["avg_corr"].min():.3f}')
    print(f'  Max:    {master["avg_corr"].max():.3f}')
    print(f'  Q25:    {master["avg_corr"].quantile(0.25):.3f}')
    print(f'  Q75:    {master["avg_corr"].quantile(0.75):.3f}')

    # Per-alt correlation stats
    print(f'\nPer-Alt Correlation with BTC (30d rolling mean):')
    for alt in ALTS:
        col = f'corr_{alt}'
        if col in master.columns:
            print(f'  {alt:6s}: mean={master[col].mean():.3f}  '
                  f'std={master[col].std():.3f}  '
                  f'min={master[col].min():.3f}  '
                  f'max={master[col].max():.3f}')

    # Regime distribution
    regime_counts = master['regime'].value_counts()
    regime_pcts = master['regime'].value_counts(normalize=True) * 100
    print(f'\nRegime Distribution:')
    for regime in ['HIGH', 'MEDIUM', 'LOW']:
        if regime in regime_counts.index:
            print(f'  {regime:8s}: {regime_counts[regime]:5d} days ({regime_pcts[regime]:.1f}%)')

    # IS/OOS split
    is_data = master[master.index < OOS_START]
    oos_data = master[master.index >= OOS_START]

    print(f'\nIS ({is_data.index.min().date()} to {is_data.index.max().date()}, N={len(is_data)}):')
    for regime in ['HIGH', 'MEDIUM', 'LOW']:
        ct = (is_data['regime'] == regime).sum()
        pct = 100 * ct / len(is_data) if len(is_data) > 0 else 0
        print(f'  {regime:8s}: {ct:5d} days ({pct:.1f}%)')

    print(f'\nOOS ({oos_data.index.min().date()} to {oos_data.index.max().date()}, N={len(oos_data)}):')
    for regime in ['HIGH', 'MEDIUM', 'LOW']:
        ct = (oos_data['regime'] == regime).sum()
        pct = 100 * ct / len(oos_data) if len(oos_data) > 0 else 0
        print(f'  {regime:8s}: {ct:5d} days ({pct:.1f}%)')

    return is_data, oos_data


def section2_ic_by_regime(master, is_data, oos_data):
    """Compute IC of each signal within each correlation regime."""
    print(f'\n{SEP}')
    print('SECTION 2: SIGNAL IC BY CORRELATION REGIME')
    print(SEP)

    signals = ['us10y_20d_chg', 'skew_30d', 'dxy_20d_mom']
    signal_labels = ['US10Y 20d Chg', 'Skew 30d', 'DXY 20d Mom']
    targets = ['btc_fwd_1d', 'btc_fwd_5d', 'btc_fwd_14d']
    target_labels = ['Fwd 1D', 'Fwd 5D', 'Fwd 14D']
    regimes = ['HIGH', 'MEDIUM', 'LOW']

    results_is = []
    results_oos = []

    for sig, sig_label in zip(signals, signal_labels):
        for tgt, tgt_label in zip(targets, target_labels):
            # Overall (no regime filter)
            ic_is, t_is, n_is = compute_ic(is_data, sig, tgt)
            ic_oos, t_oos, n_oos = compute_ic(oos_data, sig, tgt)

            results_is.append({
                'Signal': sig_label, 'Target': tgt_label, 'Regime': 'ALL',
                'IC': ic_is, 't-stat': t_is, 'N': n_is
            })
            results_oos.append({
                'Signal': sig_label, 'Target': tgt_label, 'Regime': 'ALL',
                'IC': ic_oos, 't-stat': t_oos, 'N': n_oos
            })

            # Per regime
            for regime in regimes:
                is_regime = is_data[is_data['regime'] == regime]
                oos_regime = oos_data[oos_data['regime'] == regime]

                ic_is_r, t_is_r, n_is_r = compute_ic(is_regime, sig, tgt)
                ic_oos_r, t_oos_r, n_oos_r = compute_ic(oos_regime, sig, tgt)

                results_is.append({
                    'Signal': sig_label, 'Target': tgt_label, 'Regime': regime,
                    'IC': ic_is_r, 't-stat': t_is_r, 'N': n_is_r
                })
                results_oos.append({
                    'Signal': sig_label, 'Target': tgt_label, 'Regime': regime,
                    'IC': ic_oos_r, 't-stat': t_oos_r, 'N': n_oos_r
                })

    df_is = pd.DataFrame(results_is)
    df_oos = pd.DataFrame(results_oos)

    # Print IS results
    print(f'\n--- IN-SAMPLE (< {OOS_START.date()}) ---')
    _print_ic_table(df_is)

    # Print OOS results
    print(f'\n--- OUT-OF-SAMPLE (>= {OOS_START.date()}) ---')
    _print_ic_table(df_oos)

    return df_is, df_oos


def _print_ic_table(df):
    """Format and print IC results table."""
    for sig_label in df['Signal'].unique():
        print(f'\n  Signal: {sig_label}')
        print(f'  {"Regime":<10} {"Fwd 1D IC":>12} {"t":>7} {"Fwd 5D IC":>12} {"t":>7} {"Fwd 14D IC":>12} {"t":>7} {"N":>6}')
        print(f'  {THIN[:80]}')

        for regime in ['ALL', 'HIGH', 'MEDIUM', 'LOW']:
            row_data = df[(df['Signal'] == sig_label) & (df['Regime'] == regime)]
            if len(row_data) == 0:
                continue

            parts = [f'  {regime:<10}']
            for tgt_label in ['Fwd 1D', 'Fwd 5D', 'Fwd 14D']:
                r = row_data[row_data['Target'] == tgt_label]
                if len(r) > 0 and pd.notna(r.iloc[0]['IC']):
                    ic_val = r.iloc[0]['IC']
                    t_val = r.iloc[0]['t-stat']
                    sig_marker = '***' if abs(t_val) > 3 else '**' if abs(t_val) > 2 else '*' if abs(t_val) > 1.65 else ''
                    parts.append(f'{ic_val:>+10.4f}  {t_val:>+6.2f}{sig_marker}')
                else:
                    parts.append(f'{"N/A":>10}  {"N/A":>6} ')

            # N from 14D row (most representative)
            r14 = row_data[row_data['Target'] == 'Fwd 14D']
            n_val = int(r14.iloc[0]['N']) if len(r14) > 0 and pd.notna(r14.iloc[0]['N']) else 0
            parts.append(f'{n_val:>6}')
            print(' '.join(parts))


def section3_ic_improvement(df_is, df_oos):
    """Quantify whether regime conditioning improves signal IC."""
    print(f'\n{SEP}')
    print('SECTION 3: IC IMPROVEMENT FROM REGIME CONDITIONING')
    print(SEP)
    print('\nKey question: Does the HIGH-corr regime produce meaningfully')
    print('higher absolute IC than the overall (unconditional) IC?')

    for label, df in [('IS', df_is), ('OOS', df_oos)]:
        print(f'\n--- {label} ---')
        print(f'  {"Signal":<20} {"Target":<10} {"IC(ALL)":>10} {"IC(HIGH)":>10} {"IC(LOW)":>10} {"Lift":>10} {"Verdict":>15}')
        print(f'  {THIN[:90]}')

        for sig_label in df['Signal'].unique():
            for tgt_label in ['Fwd 5D', 'Fwd 14D']:
                all_row = df[(df['Signal'] == sig_label) & (df['Regime'] == 'ALL') & (df['Target'] == tgt_label)]
                high_row = df[(df['Signal'] == sig_label) & (df['Regime'] == 'HIGH') & (df['Target'] == tgt_label)]
                low_row = df[(df['Signal'] == sig_label) & (df['Regime'] == 'LOW') & (df['Target'] == tgt_label)]

                ic_all = all_row.iloc[0]['IC'] if len(all_row) > 0 else np.nan
                ic_high = high_row.iloc[0]['IC'] if len(high_row) > 0 else np.nan
                ic_low = low_row.iloc[0]['IC'] if len(low_row) > 0 else np.nan
                t_high = high_row.iloc[0]['t-stat'] if len(high_row) > 0 else np.nan

                if pd.notna(ic_all) and pd.notna(ic_high):
                    lift = abs(ic_high) - abs(ic_all)
                    if lift > 0.02 and abs(t_high) > 1.65:
                        verdict = 'IMPROVED'
                    elif lift > 0.01:
                        verdict = 'MARGINAL'
                    elif lift < -0.02:
                        verdict = 'DEGRADED'
                    else:
                        verdict = 'NO CHANGE'
                else:
                    lift = np.nan
                    verdict = 'N/A'

                ic_all_s = f'{ic_all:+.4f}' if pd.notna(ic_all) else 'N/A'
                ic_high_s = f'{ic_high:+.4f}' if pd.notna(ic_high) else 'N/A'
                ic_low_s = f'{ic_low:+.4f}' if pd.notna(ic_low) else 'N/A'
                lift_s = f'{lift:+.4f}' if pd.notna(lift) else 'N/A'

                print(f'  {sig_label:<20} {tgt_label:<10} {ic_all_s:>10} {ic_high_s:>10} {ic_low_s:>10} {lift_s:>10} {verdict:>15}')


def section4_corr_regime_as_signal(master, is_data, oos_data):
    """Test whether correlation regime itself predicts forward returns."""
    print(f'\n{SEP}')
    print('SECTION 4: CORRELATION REGIME AS A STANDALONE SIGNAL')
    print(SEP)
    print('\nDoes avg_corr itself predict BTC forward returns?')

    # IC of avg_corr vs forward returns
    targets = ['btc_fwd_1d', 'btc_fwd_5d', 'btc_fwd_14d']
    target_labels = ['Fwd 1D', 'Fwd 5D', 'Fwd 14D']

    print(f'\n  {"Sample":<20} ', end='')
    for tl in target_labels:
        print(f'{tl + " IC":>12} {"t":>7}', end='  ')
    print(f'{"N":>6}')
    print(f'  {THIN[:80]}')

    for label, data in [('IS (full)', is_data), ('OOS (full)', oos_data)]:
        print(f'  {label:<20} ', end='')
        for tgt in targets:
            ic, t, n = compute_ic(data, 'avg_corr', tgt)
            if pd.notna(ic):
                sig = '***' if abs(t) > 3 else '**' if abs(t) > 2 else '*' if abs(t) > 1.65 else ''
                print(f'{ic:>+10.4f}  {t:>+6.2f}{sig}', end='  ')
            else:
                print(f'{"N/A":>10}  {"N/A":>6} ', end='  ')
        print(f'{n:>6}')

    # Mean forward returns by regime
    print(f'\n  Mean BTC Forward Returns by Correlation Regime:')
    print(f'\n  --- IS ---')
    _print_regime_returns(is_data)
    print(f'\n  --- OOS ---')
    _print_regime_returns(oos_data)

    # t-test: HIGH vs LOW regime forward returns
    print(f'\n  T-test: HIGH vs LOW Regime Mean Returns')
    for label, data in [('IS', is_data), ('OOS', oos_data)]:
        print(f'\n  --- {label} ---')
        for tgt, tgt_label in zip(targets, target_labels):
            high_ret = data.loc[data['regime'] == 'HIGH', tgt].dropna()
            low_ret = data.loc[data['regime'] == 'LOW', tgt].dropna()
            if len(high_ret) >= 10 and len(low_ret) >= 10:
                t_stat, p_val = stats.ttest_ind(high_ret, low_ret, equal_var=False)
                diff = high_ret.mean() - low_ret.mean()
                print(f'    {tgt_label}: HIGH mean={high_ret.mean()*100:+.3f}%, '
                      f'LOW mean={low_ret.mean()*100:+.3f}%, '
                      f'diff={diff*100:+.3f}%, t={t_stat:+.2f}, p={p_val:.4f}')
            else:
                print(f'    {tgt_label}: insufficient data (HIGH={len(high_ret)}, LOW={len(low_ret)})')


def _print_regime_returns(data):
    """Print mean forward returns by regime with annualized Sharpe."""
    targets = ['btc_fwd_1d', 'btc_fwd_5d', 'btc_fwd_14d']
    target_labels = ['Fwd 1D', 'Fwd 5D', 'Fwd 14D']

    print(f'  {"Regime":<10} {"N":>6}', end='')
    for tl in target_labels:
        print(f'  {tl + " mean":>14}  {tl + " Sharpe":>12}', end='')
    print()
    print(f'  {THIN[:90]}')

    for regime in ['HIGH', 'MEDIUM', 'LOW', 'ALL']:
        if regime == 'ALL':
            subset = data
        else:
            subset = data[data['regime'] == regime]

        if len(subset) < 10:
            continue

        print(f'  {regime:<10} {len(subset):>6}', end='')
        for tgt in targets:
            vals = subset[tgt].dropna()
            if len(vals) >= 10:
                mean_ret = vals.mean()
                sharpe = (vals.mean() / vals.std()) * ANNUALIZE if vals.std() > 0 else 0
                print(f'  {mean_ret*100:>+12.4f}%  {sharpe:>+10.2f}', end='')
            else:
                print(f'  {"N/A":>13}  {"N/A":>11}', end='')
        print()


def section5_conditional_sharpe_improvement(master, is_data, oos_data):
    """
    Compare signal-based strategy Sharpe when conditioned on correlation regime
    vs unconditional.
    """
    print(f'\n{SEP}')
    print('SECTION 5: CONDITIONAL SHARPE — SIGNAL PERFORMANCE BY REGIME')
    print(SEP)
    print('\nFor each signal, compute long-short (quintile) strategy Sharpe')
    print('conditioned on HIGH-corr regime vs unconditional.')

    signals = ['us10y_20d_chg', 'skew_30d', 'dxy_20d_mom']
    signal_labels = ['US10Y 20d Chg', 'Skew 30d', 'DXY 20d Mom']
    # Use directional knowledge: US10Y rising is bearish, DXY rising is bearish
    signal_dirs = [-1, 1, -1]  # multiply signal by this for long direction

    for label, data in [('IS', is_data), ('OOS', oos_data)]:
        print(f'\n--- {label} ---')
        print(f'  {"Signal":<20} {"Uncond Sharpe":>15} {"HIGH Sharpe":>15} {"LOW Sharpe":>15} {"Improvement":>15}')
        print(f'  {THIN[:80]}')

        for sig, sig_label, sig_dir in zip(signals, signal_labels, signal_dirs):
            # Unconditional: go long when signal is favorable
            uncond = data[[sig, 'btc_fwd_1d']].dropna()
            if len(uncond) < 60:
                print(f'  {sig_label:<20} {"N/A":>15} {"N/A":>15} {"N/A":>15} {"N/A":>15}')
                continue

            # Signal-weighted: sign(signal * dir) * forward return
            uncond_pos = (sig_dir * uncond[sig] > uncond[sig].median()).astype(int)
            uncond_rets = uncond_pos * uncond['btc_fwd_1d']
            uncond_sharpe = (uncond_rets.mean() / uncond_rets.std() * ANNUALIZE
                           if uncond_rets.std() > 0 else 0)

            # HIGH regime only
            high_data = data[data['regime'] == 'HIGH'][[sig, 'btc_fwd_1d']].dropna()
            if len(high_data) >= 30:
                high_pos = (sig_dir * high_data[sig] > high_data[sig].median()).astype(int)
                high_rets = high_pos * high_data['btc_fwd_1d']
                high_sharpe = (high_rets.mean() / high_rets.std() * ANNUALIZE
                              if high_rets.std() > 0 else 0)
            else:
                high_sharpe = np.nan

            # LOW regime only
            low_data = data[data['regime'] == 'LOW'][[sig, 'btc_fwd_1d']].dropna()
            if len(low_data) >= 30:
                low_pos = (sig_dir * low_data[sig] > low_data[sig].median()).astype(int)
                low_rets = low_pos * low_data['btc_fwd_1d']
                low_sharpe = (low_rets.mean() / low_rets.std() * ANNUALIZE
                             if low_rets.std() > 0 else 0)
            else:
                low_sharpe = np.nan

            improvement = high_sharpe - uncond_sharpe if pd.notna(high_sharpe) else np.nan
            high_s = f'{high_sharpe:+.3f}' if pd.notna(high_sharpe) else 'N/A'
            low_s = f'{low_sharpe:+.3f}' if pd.notna(low_sharpe) else 'N/A'
            imp_s = f'{improvement:+.3f}' if pd.notna(improvement) else 'N/A'

            print(f'  {sig_label:<20} {uncond_sharpe:>+13.3f}   {high_s:>13}   {low_s:>13}   {imp_s:>13}')


def section6_stability_analysis(master, is_data, oos_data):
    """Check time stability of the correlation regime and signal conditioning."""
    print(f'\n{SEP}')
    print('SECTION 6: TEMPORAL STABILITY')
    print(SEP)

    # Yearly breakdown of correlation regime
    print(f'\n  Yearly Average BTC-Alt Correlation:')
    yearly = master.groupby(master.index.year)['avg_corr'].agg(['mean', 'std', 'count'])
    print(f'  {"Year":<6} {"Mean":>8} {"Std":>8} {"N":>6}')
    for yr, row in yearly.iterrows():
        print(f'  {yr:<6} {row["mean"]:>8.3f} {row["std"]:>8.3f} {int(row["count"]):>6}')

    # Yearly regime distribution
    print(f'\n  Yearly Regime Distribution (%):')
    print(f'  {"Year":<6} {"HIGH":>8} {"MEDIUM":>8} {"LOW":>8}')
    for yr in sorted(master.index.year.unique()):
        yr_data = master[master.index.year == yr]
        for regime in ['HIGH', 'MEDIUM', 'LOW']:
            pct = 100 * (yr_data['regime'] == regime).mean()
            print(f'{pct:>8.1f}', end='')
        print(f'  (year={yr}, N={len(yr_data)})')

    # Rolling IC of us10y_20d_chg conditioned on HIGH regime vs unconditional
    print(f'\n  Rolling 90d IC of US10Y 20d Chg (Fwd 14D):')
    print(f'  Comparing: unconditional vs HIGH-corr regime')
    rolling_all = compute_rolling_ic(is_data, 'us10y_20d_chg', 'btc_fwd_14d', window=90)
    high_is = is_data[is_data['regime'] == 'HIGH']
    if len(high_is) >= 90:
        rolling_high = compute_rolling_ic(high_is, 'us10y_20d_chg', 'btc_fwd_14d', window=90)
        print(f'    Unconditional: mean IC = {rolling_all.mean():.4f}, std = {rolling_all.std():.4f}')
        print(f'    HIGH regime:   mean IC = {rolling_high.mean():.4f}, std = {rolling_high.std():.4f}')
        print(f'    Pct of time IC has same sign: '
              f'{100*(rolling_all.dropna() * rolling_high.reindex(rolling_all.index).fillna(0) > 0).mean():.1f}%')
    else:
        print(f'    Insufficient HIGH-regime data for rolling IC')


def section7_summary_and_recommendation(df_is, df_oos, master, is_data, oos_data):
    """Synthesize findings into actionable recommendation."""
    print(f'\n{SEP}')
    print('SECTION 7: SUMMARY & RECOMMENDATION')
    print(SEP)

    # Key metrics for recommendation
    # 1. Does correlation regime improve IC of macro signals?
    signals = ['US10Y 20d Chg', 'Skew 30d', 'DXY 20d Mom']
    improvements = []
    oos_improvements = []

    for sig in signals:
        for tgt in ['Fwd 5D', 'Fwd 14D']:
            all_is = df_is[(df_is['Signal'] == sig) & (df_is['Target'] == tgt) & (df_is['Regime'] == 'ALL')]
            high_is = df_is[(df_is['Signal'] == sig) & (df_is['Target'] == tgt) & (df_is['Regime'] == 'HIGH')]
            if len(all_is) > 0 and len(high_is) > 0:
                ic_all = all_is.iloc[0]['IC']
                ic_high = high_is.iloc[0]['IC']
                if pd.notna(ic_all) and pd.notna(ic_high):
                    improvements.append(abs(ic_high) - abs(ic_all))

            all_oos = df_oos[(df_oos['Signal'] == sig) & (df_oos['Target'] == tgt) & (df_oos['Regime'] == 'ALL')]
            high_oos = df_oos[(df_oos['Signal'] == sig) & (df_oos['Target'] == tgt) & (df_oos['Regime'] == 'HIGH')]
            if len(all_oos) > 0 and len(high_oos) > 0:
                ic_all = all_oos.iloc[0]['IC']
                ic_high = high_oos.iloc[0]['IC']
                if pd.notna(ic_all) and pd.notna(ic_high):
                    oos_improvements.append(abs(ic_high) - abs(ic_all))

    avg_is_improvement = np.mean(improvements) if improvements else np.nan
    avg_oos_improvement = np.mean(oos_improvements) if oos_improvements else np.nan

    # 2. Is correlation regime itself predictive?
    ic_corr_is, t_corr_is, _ = compute_ic(is_data, 'avg_corr', 'btc_fwd_14d')
    ic_corr_oos, t_corr_oos, _ = compute_ic(oos_data, 'avg_corr', 'btc_fwd_14d')

    # 3. OOS regime distribution: do we have enough data?
    oos_high_pct = 100 * (oos_data['regime'] == 'HIGH').mean() if len(oos_data) > 0 else 0
    oos_low_pct = 100 * (oos_data['regime'] == 'LOW').mean() if len(oos_data) > 0 else 0

    print(f'\n  KEY FINDINGS:')
    print(f'  1. Mean IC lift from HIGH-corr conditioning:')
    print(f'     IS:  {avg_is_improvement:+.4f}' if pd.notna(avg_is_improvement) else '     IS:  N/A')
    print(f'     OOS: {avg_oos_improvement:+.4f}' if pd.notna(avg_oos_improvement) else '     OOS: N/A')
    print(f'  2. Correlation regime as standalone signal (14D IC):')
    print(f'     IS:  IC={ic_corr_is:+.4f}, t={t_corr_is:+.2f}' if pd.notna(ic_corr_is) else '     IS:  N/A')
    print(f'     OOS: IC={ic_corr_oos:+.4f}, t={t_corr_oos:+.2f}' if pd.notna(ic_corr_oos) else '     OOS: N/A')
    print(f'  3. OOS regime distribution: HIGH={oos_high_pct:.1f}%, LOW={oos_low_pct:.1f}%')

    # Decision logic
    is_lift_significant = pd.notna(avg_is_improvement) and avg_is_improvement > 0.02
    oos_lift_positive = pd.notna(avg_oos_improvement) and avg_oos_improvement > 0.0
    oos_corr_significant = pd.notna(t_corr_oos) and abs(t_corr_oos) > 1.65
    oos_has_data = len(oos_data) >= 60

    print(f'\n  DECISION CRITERIA:')
    print(f'    IS lift > 0.02?          {"YES" if is_lift_significant else "NO"}')
    print(f'    OOS lift > 0?            {"YES" if oos_lift_positive else "NO"}')
    print(f'    Corr regime OOS |t|>1.65? {"YES" if oos_corr_significant else "NO"}')
    print(f'    OOS has 60+ days?        {"YES" if oos_has_data else "NO"}')

    if is_lift_significant and oos_lift_positive:
        verdict = 'USE as conditioner'
        detail = ('Correlation regime conditioning improves macro signal IC both IS and OOS. '
                  'Recommend using avg BTC-alt correlation as a regime filter: '
                  'upweight macro signals when avg_corr > 0.7, downweight when < 0.4.')
    elif is_lift_significant and not oos_lift_positive:
        verdict = 'SKIP — IS only'
        detail = ('IC improvement is IS-only and does not hold OOS. '
                  'Likely overfit to the correlation structure of the training period.')
    elif not is_lift_significant:
        verdict = 'SKIP — no lift'
        detail = ('Correlation regime conditioning does not meaningfully improve '
                  'macro signal IC even in-sample. The hypothesis is rejected.')
    else:
        verdict = 'NEEDS MORE DATA'
        detail = 'Insufficient OOS data to draw conclusions.'

    print(f'\n  ┌──────────────────────────────────────────────────┐')
    print(f'  │  RECOMMENDATION: {verdict:<33}│')
    print(f'  └──────────────────────────────────────────────────┘')
    print(f'\n  Detail: {detail}')

    return verdict, detail, {
        'avg_is_improvement': avg_is_improvement,
        'avg_oos_improvement': avg_oos_improvement,
        'ic_corr_is': ic_corr_is,
        't_corr_is': t_corr_is,
        'ic_corr_oos': ic_corr_oos,
        't_corr_oos': t_corr_oos,
        'oos_high_pct': oos_high_pct,
        'oos_low_pct': oos_low_pct,
    }


# ══════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════

def main():
    print(SEP)
    print('BTC-Alt Rolling Correlation as Regime Conditioner for Macro Signals')
    print(SEP)
    print(f'OOS cutoff: {OOS_START.date()}')
    print(f'Correlation window: {CORR_WINDOW} days')
    print(f'Alts: {ALTS}')

    # Step 1-3: Correlation regime
    corr_df, all_rets = compute_correlation_regime()

    # Step 4-5: Signals and targets
    master = compute_signals_and_targets(corr_df, all_rets)

    # Analysis sections
    is_data, oos_data = section1_correlation_regime_descriptives(master)
    df_is, df_oos = section2_ic_by_regime(master, is_data, oos_data)
    section3_ic_improvement(df_is, df_oos)
    section4_corr_regime_as_signal(master, is_data, oos_data)
    section5_conditional_sharpe_improvement(master, is_data, oos_data)
    section6_stability_analysis(master, is_data, oos_data)
    verdict, detail, metrics = section7_summary_and_recommendation(
        df_is, df_oos, master, is_data, oos_data)

    print(f'\n{SEP}')
    print('ANALYSIS COMPLETE')
    print(SEP)

    return verdict, detail, metrics, df_is, df_oos, master


if __name__ == '__main__':
    main()
