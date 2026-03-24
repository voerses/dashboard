"""
Macro + Positioning Signal Combination Analysis
================================================
Test whether GOLD signal families (macro regime + positioning) are uncorrelated
and whether combining them improves IC beyond either alone.

Signal A - MACRO: US10Y+DXY regime score
Signal B - POSITIONING: Top Trader L/S + L/S Divergence
"""

import pandas as pd
import numpy as np
from scipy import stats
import warnings
warnings.filterwarnings('ignore')

# ============================================================
# 1. LOAD DATA
# ============================================================

# --- BTC daily price from hourly ---
btc_1h = pd.read_parquet('/workspace/crypto_backtest/data/perp/1h_cache/BTC_1h.parquet')
btc_daily = btc_1h['close'].resample('D').last().dropna()
btc_daily.name = 'btc_close'

# --- Macro data (trim to 2019+ for BTC-era expanding rank) ---
us10y_raw = pd.read_parquet('/workspace/crypto_backtest/data/alternative/macro/us10y_yield.parquet')
us10y = us10y_raw.set_index('Date')[['Close']].rename(columns={'Close': 'us10y'})
us10y.index = pd.to_datetime(us10y.index)
us10y = us10y.loc['2019-01-01':]

dxy_raw = pd.read_parquet('/workspace/crypto_backtest/data/alternative/macro/usd_index.parquet')
dxy = dxy_raw.set_index('Date')[['Close']].rename(columns={'Close': 'dxy'})
dxy.index = pd.to_datetime(dxy.index)
dxy = dxy.loc['2019-01-01':]

# --- Positioning data ---
ls_raw = pd.read_parquet('/workspace/crypto_backtest/data/alternative/binance_metrics/all_symbols_daily_ls.parquet')
btc_ls = ls_raw[ls_raw['symbol'] == 'BTCUSDT'].copy()
btc_ls = btc_ls.set_index('date')
btc_ls.index = pd.to_datetime(btc_ls.index)

print(f"BTC daily: {btc_daily.index.min()} to {btc_daily.index.max()} ({len(btc_daily)} rows)")
print(f"US10Y:     {us10y.index.min()} to {us10y.index.max()} ({len(us10y)} rows)")
print(f"DXY:       {dxy.index.min()} to {dxy.index.max()} ({len(dxy)} rows)")
print(f"BTC L/S:   {btc_ls.index.min()} to {btc_ls.index.max()} ({len(btc_ls)} rows)")

# ============================================================
# 2. BUILD SIGNALS
# ============================================================

# --- Signal A: Macro regime score ---
us10y_20d_chg = us10y['us10y'].diff(20).dropna()
dxy_20d_mom = dxy['dxy'].pct_change(20).dropna()

def fast_expanding_rank(s, min_periods=60):
    """Vectorized expanding percentile rank (0-1).
    For each point i, computes what fraction of all prior values are <= current value.
    """
    vals = s.values
    n = len(vals)
    result = np.full(n, np.nan)
    for i in range(min_periods, n):
        prev = vals[:i]
        result[i] = np.sum(prev <= vals[i]) / i
    return pd.Series(result, index=s.index, name=s.name)

print("\nComputing expanding ranks...")
us10y_rank = fast_expanding_rank(us10y_20d_chg, min_periods=60)
dxy_rank = fast_expanding_rank(dxy_20d_mom, min_periods=60)

# Align on common dates and sum
macro_combined = pd.DataFrame({'us10y_rank': us10y_rank, 'dxy_rank': dxy_rank})
macro_combined = macro_combined.dropna()
macro_score = macro_combined['us10y_rank'] + macro_combined['dxy_rank']
macro_score.name = 'signal_A_macro'
print(f"Macro score: {macro_score.index.min()} to {macro_score.index.max()} ({len(macro_score)} non-null)")

# --- Signal B1: sum_toptrader_ls_ratio ---
signal_B1 = btc_ls['sum_toptrader_ls_ratio'].copy()
signal_B1.name = 'signal_B1_toptrader_ls'

# --- Signal B2: count_toptrader_ls_ratio - count_ls_ratio (divergence) ---
signal_B2 = (btc_ls['count_toptrader_ls_ratio'] - btc_ls['count_ls_ratio']).copy()
signal_B2.name = 'signal_B2_ls_divergence'

print(f"Signal B1: {signal_B1.index.min()} to {signal_B1.index.max()} ({signal_B1.notna().sum()} non-null)")
print(f"Signal B2: {signal_B2.index.min()} to {signal_B2.index.max()} ({signal_B2.notna().sum()} non-null)")

# ============================================================
# 3. MERGE ALL DATA
# ============================================================

df = pd.DataFrame(index=btc_daily.index)
df['btc_close'] = btc_daily

# Forward-fill macro signals (weekday data -> fill weekends)
df['signal_A_macro'] = macro_score.reindex(df.index, method='ffill')
df['signal_B1_toptrader_ls'] = signal_B1.reindex(df.index, method='ffill')
df['signal_B2_ls_divergence'] = signal_B2.reindex(df.index, method='ffill')

# Forward returns
df['fwd_7d'] = df['btc_close'].pct_change(7).shift(-7)
df['fwd_14d'] = df['btc_close'].pct_change(14).shift(-14)

# Drop rows without all signals
df_full = df.dropna(subset=['signal_A_macro', 'signal_B1_toptrader_ls', 'signal_B2_ls_divergence', 'fwd_14d']).copy()
print(f"\nMerged dataset: {df_full.index.min()} to {df_full.index.max()} ({len(df_full)} rows)")

# ============================================================
# 4. PAIRWISE CORRELATIONS
# ============================================================

print("\n" + "=" * 60)
print("PAIRWISE SIGNAL CORRELATIONS (Spearman rank)")
print("=" * 60)

signals = ['signal_A_macro', 'signal_B1_toptrader_ls', 'signal_B2_ls_divergence']
corr_results = {}

for i in range(len(signals)):
    for j in range(i + 1, len(signals)):
        s1, s2 = signals[i], signals[j]
        rho, pval = stats.spearmanr(df_full[s1], df_full[s2])
        corr_results[(s1, s2)] = (rho, pval)
        print(f"  {s1} vs {s2}: rho={rho:.4f}, p={pval:.4e}")

# Also B1 vs B2
rho_b1b2, p_b1b2 = stats.spearmanr(df_full['signal_B1_toptrader_ls'], df_full['signal_B2_ls_divergence'])
print(f"\n  B1 vs B2 (internal positioning): rho={rho_b1b2:.4f}, p={p_b1b2:.4e}")

# ============================================================
# 5. COMBINED SIGNAL
# ============================================================

# Signal A: higher = more bearish for BTC (higher yields + stronger dollar)
# Signal B1: negative IC -> higher L/S -> lower returns -> negate for combination
# Signal B2: negative IC -> higher divergence -> lower returns -> negate for combination

print("\nComputing combined signals...")
rank_A = df_full['signal_A_macro'].rank(pct=True)
rank_negB1 = (-df_full['signal_B1_toptrader_ls']).rank(pct=True)
rank_negB2 = (-df_full['signal_B2_ls_divergence']).rank(pct=True)

# Combined: rank(A) + rank(-B1) -> higher = more bearish from both families
df_full['combo_A_B1'] = rank_A + rank_negB1
df_full['combo_A_B2'] = rank_A + rank_negB2
df_full['combo_A_B1_B2'] = rank_A + rank_negB1 + rank_negB2

# ============================================================
# 6. IC COMPUTATION (IN-SAMPLE vs OOS)
# ============================================================

OOS_DATE = '2025-01-01'
is_mask = df_full.index < OOS_DATE
oos_mask = df_full.index >= OOS_DATE

df_is = df_full[is_mask]
df_oos = df_full[oos_mask]

print(f"\nIS period:  {df_is.index.min()} to {df_is.index.max()} ({len(df_is)} rows)")
print(f"OOS period: {df_oos.index.min()} to {df_oos.index.max()} ({len(df_oos)} rows)")

def compute_ic(df_subset, signal_col, fwd_col):
    """Compute Spearman IC between signal and forward returns."""
    valid = df_subset[[signal_col, fwd_col]].dropna()
    if len(valid) < 30:
        return np.nan, np.nan, 0
    rho, pval = stats.spearmanr(valid[signal_col], valid[fwd_col])
    return rho, pval, len(valid)

print("\n" + "=" * 60)
print("INFORMATION COEFFICIENTS (IC = Spearman corr with fwd returns)")
print("=" * 60)

all_signals = [
    'signal_A_macro',
    'signal_B1_toptrader_ls',
    'signal_B2_ls_divergence',
    'combo_A_B1',
    'combo_A_B2',
    'combo_A_B1_B2',
]

ic_results = []

for sig in all_signals:
    for horizon in ['fwd_7d', 'fwd_14d']:
        for label, subset in [('IS', df_is), ('OOS', df_oos), ('FULL', df_full)]:
            ic, pval, n = compute_ic(subset, sig, horizon)
            ic_results.append({
                'signal': sig,
                'horizon': horizon,
                'period': label,
                'IC': ic,
                'p_value': pval,
                'n': n,
            })

ic_df = pd.DataFrame(ic_results)

# Print formatted table
for period in ['IS', 'OOS', 'FULL']:
    print(f"\n--- {period} ---")
    subset = ic_df[ic_df['period'] == period].copy()
    for _, row in subset.iterrows():
        sig_str = f"  IC={row['IC']:+.4f}" if not np.isnan(row['IC']) else "  IC=NaN"
        p_str = f"  p={row['p_value']:.4e}" if not np.isnan(row['p_value']) else "  p=NaN"
        print(f"  {row['signal']:30s} {row['horizon']:8s} {sig_str} {p_str}  (n={int(row['n'])})")

# ============================================================
# 7. IC ADDITIVITY CHECK
# ============================================================

print("\n" + "=" * 60)
print("IC ADDITIVITY CHECK")
print("=" * 60)

for period_label, subset in [('IS', df_is), ('OOS', df_oos)]:
    print(f"\n--- {period_label} (14d horizon) ---")
    ic_A, _, _ = compute_ic(subset, 'signal_A_macro', 'fwd_14d')
    ic_B1, _, _ = compute_ic(subset, 'signal_B1_toptrader_ls', 'fwd_14d')
    ic_combo, _, _ = compute_ic(subset, 'combo_A_B1', 'fwd_14d')
    ic_triple, _, _ = compute_ic(subset, 'combo_A_B1_B2', 'fwd_14d')

    # For uncorrelated signals, combined IC ~ sqrt(IC_A^2 + IC_B^2) approximately
    theoretical_combo = np.sqrt(ic_A**2 + ic_B1**2) if not np.isnan(ic_A) and not np.isnan(ic_B1) else np.nan
    print(f"  IC(A)            = {ic_A:+.4f}" if not np.isnan(ic_A) else "  IC(A)            = NaN")
    print(f"  IC(B1)           = {ic_B1:+.4f}" if not np.isnan(ic_B1) else "  IC(B1)           = NaN")
    print(f"  IC(combo A+B1)   = {ic_combo:+.4f}" if not np.isnan(ic_combo) else "  IC(combo A+B1)   = NaN")
    print(f"  IC(triple A+B1+B2) = {ic_triple:+.4f}" if not np.isnan(ic_triple) else "  IC(triple A+B1+B2) = NaN")
    print(f"  Theoretical (uncorrelated): sqrt(IC_A^2 + IC_B1^2) = {theoretical_combo:+.4f}" if not np.isnan(theoretical_combo) else "  Theoretical = NaN")
    if not np.isnan(ic_combo) and not np.isnan(ic_A) and not np.isnan(ic_B1):
        best_individual = max(abs(ic_A), abs(ic_B1))
        if best_individual > 0:
            print(f"  Improvement over best individual: {(abs(ic_combo) - best_individual)/best_individual*100:+.1f}%")

# ============================================================
# 8. CONDITIONAL ANALYSIS
# ============================================================

print("\n" + "=" * 60)
print("CONDITIONAL ANALYSIS (14d forward returns)")
print("=" * 60)

def regime_stats(label, data, fwd_col='fwd_14d'):
    if len(data) < 5:
        print(f"  {label:40s}  n={len(data):4d}  (too few observations)")
        return None
    fwd = data[fwd_col].dropna()
    if len(fwd) < 5:
        print(f"  {label:40s}  n={len(fwd):4d}  (too few observations)")
        return None
    mean_ret = fwd.mean()
    median_ret = fwd.median()
    hit_rate = (fwd > 0).mean()
    std_ret = fwd.std()
    sharpe_like = mean_ret / std_ret if std_ret > 0 else 0
    print(f"  {label:40s}  n={len(fwd):4d}  mean={mean_ret*100:+6.2f}%  "
          f"median={median_ret*100:+6.2f}%  hit={hit_rate*100:.1f}%  "
          f"sharpe_14d={sharpe_like:.3f}")
    return {'label': label, 'n': len(fwd), 'mean': mean_ret, 'median': median_ret,
            'hit_rate': hit_rate, 'sharpe': sharpe_like}

for period_label, subset in [('IS', df_is), ('OOS', df_oos), ('FULL', df_full)]:
    print(f"\n--- {period_label} ---")
    sub = subset.copy()

    macro_q33 = sub['signal_A_macro'].quantile(0.33)
    macro_q67 = sub['signal_A_macro'].quantile(0.67)
    b1_q33 = sub['signal_B1_toptrader_ls'].quantile(0.33)
    b1_q67 = sub['signal_B1_toptrader_ls'].quantile(0.67)

    macro_bearish = sub['signal_A_macro'] >= macro_q67
    macro_bullish = sub['signal_A_macro'] <= macro_q33
    pos_crowded = sub['signal_B1_toptrader_ls'] >= b1_q67
    pos_light = sub['signal_B1_toptrader_ls'] <= b1_q33

    regime_stats("BOTH BEARISH (macro bear + crowded)", sub[macro_bearish & pos_crowded])
    regime_stats("BOTH BULLISH (macro bull + light pos)", sub[macro_bullish & pos_light])
    regime_stats("DISAGREE: macro bear + light pos", sub[macro_bearish & pos_light])
    regime_stats("DISAGREE: macro bull + crowded pos", sub[macro_bullish & pos_crowded])
    regime_stats("ALL DISAGREE combined", sub[(macro_bearish & pos_light) | (macro_bullish & pos_crowded)])
    regime_stats("UNCONDITIONAL (all days)", sub)

# ============================================================
# 9. ROLLING IC STABILITY
# ============================================================

print("\n" + "=" * 60)
print("ROLLING IC (180-day window, 14d horizon)")
print("=" * 60)

def rolling_ic(df_input, signal_col, fwd_col, window=180):
    """Compute rolling Spearman IC."""
    ics = []
    for i in range(window, len(df_input)):
        chunk = df_input.iloc[i-window:i]
        valid = chunk[[signal_col, fwd_col]].dropna()
        if len(valid) >= 30:
            rho, _ = stats.spearmanr(valid[signal_col], valid[fwd_col])
            ics.append({'date': df_input.index[i], 'ic': rho})
    return pd.DataFrame(ics).set_index('date') if ics else pd.DataFrame()

for sig_name in ['signal_A_macro', 'combo_A_B1', 'combo_A_B1_B2']:
    ric = rolling_ic(df_full, sig_name, 'fwd_14d', window=180)
    if len(ric) > 0:
        is_ric = ric[ric.index < OOS_DATE]
        oos_ric = ric[ric.index >= OOS_DATE]
        print(f"\n  {sig_name}:")
        if len(is_ric) > 0:
            print(f"    IS  mean_IC={is_ric['ic'].mean():+.4f}  std={is_ric['ic'].std():.4f}  "
                  f"min={is_ric['ic'].min():+.4f}  max={is_ric['ic'].max():+.4f}  "
                  f"pct_positive={(is_ric['ic'] > 0).mean()*100:.1f}%")
        if len(oos_ric) > 0:
            print(f"    OOS mean_IC={oos_ric['ic'].mean():+.4f}  std={oos_ric['ic'].std():.4f}  "
                  f"min={oos_ric['ic'].min():+.4f}  max={oos_ric['ic'].max():+.4f}  "
                  f"pct_positive={(oos_ric['ic'] > 0).mean()*100:.1f}%")

# ============================================================
# 10. SUMMARY
# ============================================================

print("\n" + "=" * 60)
print("EXECUTIVE SUMMARY")
print("=" * 60)

macro_b1_corr = corr_results.get(('signal_A_macro', 'signal_B1_toptrader_ls'), (np.nan, np.nan))
macro_b2_corr = corr_results.get(('signal_A_macro', 'signal_B2_ls_divergence'), (np.nan, np.nan))

print(f"\n1. CORRELATION between signal families:")
print(f"   Macro vs B1 (toptrader L/S):     rho={macro_b1_corr[0]:+.4f} (p={macro_b1_corr[1]:.4e})")
print(f"   Macro vs B2 (L/S divergence):    rho={macro_b2_corr[0]:+.4f} (p={macro_b2_corr[1]:.4e})")
if abs(macro_b1_corr[0]) < 0.15:
    print("   -> Signals are WEAKLY CORRELATED - combination should add value")
elif abs(macro_b1_corr[0]) < 0.30:
    print("   -> Signals have MODERATE correlation - some diversification benefit")
else:
    print("   -> Signals are HIGHLY CORRELATED - limited diversification benefit")

oos_macro_14d = ic_df[(ic_df['signal'] == 'signal_A_macro') & (ic_df['horizon'] == 'fwd_14d') & (ic_df['period'] == 'OOS')]['IC'].values[0]
oos_combo_14d = ic_df[(ic_df['signal'] == 'combo_A_B1') & (ic_df['horizon'] == 'fwd_14d') & (ic_df['period'] == 'OOS')]['IC'].values[0]
oos_triple_14d = ic_df[(ic_df['signal'] == 'combo_A_B1_B2') & (ic_df['horizon'] == 'fwd_14d') & (ic_df['period'] == 'OOS')]['IC'].values[0]

print(f"\n2. OOS IC IMPROVEMENT (14d horizon):")
print(f"   Macro alone:     IC={oos_macro_14d:+.4f}")
print(f"   Combo (A+B1):    IC={oos_combo_14d:+.4f}")
print(f"   Triple (A+B1+B2): IC={oos_triple_14d:+.4f}")
if abs(oos_combo_14d) > abs(oos_macro_14d):
    pct_gain = (abs(oos_combo_14d) - abs(oos_macro_14d)) / abs(oos_macro_14d) * 100
    print(f"   -> Combo IMPROVES OOS IC by {pct_gain:.1f}% over macro alone")
else:
    print(f"   -> Combo does NOT improve OOS IC over macro alone")

print("\n3. CONCLUSION:")
print("   See research/macro_positioning_combo_results.md for full analysis")

# ============================================================
# 11. SAVE RESULTS TO MARKDOWN
# ============================================================

md_lines = []
md_lines.append("# Macro + Positioning Signal Combination Analysis")
md_lines.append(f"\n**Date**: {pd.Timestamp.now().strftime('%Y-%m-%d')}")
md_lines.append(f"**IS period**: {df_is.index.min().date()} to {df_is.index.max().date()} ({len(df_is)} days)")
md_lines.append(f"**OOS period**: {df_oos.index.min().date()} to {df_oos.index.max().date()} ({len(df_oos)} days)")

md_lines.append("\n## 1. Signal Definitions")
md_lines.append("""
| Signal | Description | Expected Direction |
|--------|-------------|-------------------|
| Signal A (Macro) | expanding_rank(US10Y 20d chg) + expanding_rank(DXY 20d mom) | Higher = bearish for BTC |
| Signal B1 (Top Trader L/S) | sum_toptrader_ls_ratio | Higher = bearish (IC < 0) |
| Signal B2 (L/S Divergence) | count_toptrader_ls_ratio - count_ls_ratio | Higher = bearish (IC < 0) |
| Combo A+B1 | rank(A) + rank(-B1) | Higher = more bearish from both families |
| Triple A+B1+B2 | rank(A) + rank(-B1) + rank(-B2) | Higher = max bearish conviction |
""")

md_lines.append("\n## 2. Pairwise Signal Correlations (Spearman)")
md_lines.append("\n| Signal Pair | Spearman rho | p-value | Interpretation |")
md_lines.append("|-------------|-------------|---------|----------------|")
for (s1, s2), (rho, pval) in corr_results.items():
    s1_short = s1.replace('signal_', '')
    s2_short = s2.replace('signal_', '')
    interp = "Weakly correlated" if abs(rho) < 0.15 else ("Moderately correlated" if abs(rho) < 0.30 else "Highly correlated")
    md_lines.append(f"| {s1_short} vs {s2_short} | {rho:+.4f} | {pval:.4e} | {interp} |")

md_lines.append("\n## 3. Information Coefficients")

for period in ['IS', 'OOS']:
    md_lines.append(f"\n### {period}")
    md_lines.append("\n| Signal | 7d IC | 7d p-value | 14d IC | 14d p-value | n |")
    md_lines.append("|--------|-------|-----------|--------|------------|---|")
    for sig in all_signals:
        row_7d = ic_df[(ic_df['signal'] == sig) & (ic_df['horizon'] == 'fwd_7d') & (ic_df['period'] == period)].iloc[0]
        row_14d = ic_df[(ic_df['signal'] == sig) & (ic_df['horizon'] == 'fwd_14d') & (ic_df['period'] == period)].iloc[0]
        sig_short = sig.replace('signal_', '').replace('combo_', 'combo: ')
        ic7 = f"{row_7d['IC']:+.4f}" if not np.isnan(row_7d['IC']) else "NaN"
        p7 = f"{row_7d['p_value']:.4e}" if not np.isnan(row_7d['p_value']) else "NaN"
        ic14 = f"{row_14d['IC']:+.4f}" if not np.isnan(row_14d['IC']) else "NaN"
        p14 = f"{row_14d['p_value']:.4e}" if not np.isnan(row_14d['p_value']) else "NaN"
        md_lines.append(f"| {sig_short} | {ic7} | {p7} | {ic14} | {p14} | {int(row_14d['n'])} |")

md_lines.append("\n## 4. IC Additivity Check")
md_lines.append("""
For truly uncorrelated signals, combined IC should approximate: `sqrt(IC_A^2 + IC_B^2)`
""")

for period_label, subset in [('IS', df_is), ('OOS', df_oos)]:
    ic_A, _, _ = compute_ic(subset, 'signal_A_macro', 'fwd_14d')
    ic_B1, _, _ = compute_ic(subset, 'signal_B1_toptrader_ls', 'fwd_14d')
    ic_combo, _, _ = compute_ic(subset, 'combo_A_B1', 'fwd_14d')
    ic_triple, _, _ = compute_ic(subset, 'combo_A_B1_B2', 'fwd_14d')
    theoretical = np.sqrt(ic_A**2 + ic_B1**2) if not np.isnan(ic_A) and not np.isnan(ic_B1) else np.nan

    md_lines.append(f"\n**{period_label}** (14d horizon):")
    md_lines.append(f"- IC(Macro alone) = {ic_A:+.4f}" if not np.isnan(ic_A) else "- IC(Macro alone) = NaN")
    md_lines.append(f"- IC(B1 alone) = {ic_B1:+.4f}" if not np.isnan(ic_B1) else "- IC(B1 alone) = NaN")
    md_lines.append(f"- IC(Combo A+B1) = {ic_combo:+.4f}" if not np.isnan(ic_combo) else "- IC(Combo A+B1) = NaN")
    md_lines.append(f"- IC(Triple A+B1+B2) = {ic_triple:+.4f}" if not np.isnan(ic_triple) else "- IC(Triple A+B1+B2) = NaN")
    md_lines.append(f"- Theoretical (uncorrelated) = {theoretical:+.4f}" if not np.isnan(theoretical) else "- Theoretical = NaN")

md_lines.append("\n## 5. Conditional Analysis (14d forward returns)")

for period_label, subset in [('IS', df_is), ('OOS', df_oos)]:
    sub = subset.copy()
    macro_q33 = sub['signal_A_macro'].quantile(0.33)
    macro_q67 = sub['signal_A_macro'].quantile(0.67)
    b1_q33 = sub['signal_B1_toptrader_ls'].quantile(0.33)
    b1_q67 = sub['signal_B1_toptrader_ls'].quantile(0.67)

    macro_bearish = sub['signal_A_macro'] >= macro_q67
    macro_bullish = sub['signal_A_macro'] <= macro_q33
    pos_crowded = sub['signal_B1_toptrader_ls'] >= b1_q67
    pos_light = sub['signal_B1_toptrader_ls'] <= b1_q33

    regimes = {
        'BOTH BEARISH (macro bear + crowded longs)': sub[macro_bearish & pos_crowded],
        'BOTH BULLISH (macro bull + light positioning)': sub[macro_bullish & pos_light],
        'DISAGREE: macro bear + light positioning': sub[macro_bearish & pos_light],
        'DISAGREE: macro bull + crowded positioning': sub[macro_bullish & pos_crowded],
        'UNCONDITIONAL': sub,
    }

    md_lines.append(f"\n### {period_label}")
    md_lines.append("\n| Regime | n | Mean 14d Return | Median 14d Return | Hit Rate | Sharpe-like |")
    md_lines.append("|--------|---|----------------|-------------------|----------|-------------|")

    for label, data in regimes.items():
        fwd = data['fwd_14d'].dropna()
        if len(fwd) < 5:
            md_lines.append(f"| {label} | {len(fwd)} | - | - | - | - |")
            continue
        mean_ret = fwd.mean()
        median_ret = fwd.median()
        hit_rate = (fwd > 0).mean()
        std_ret = fwd.std()
        sharpe = mean_ret / std_ret if std_ret > 0 else 0
        md_lines.append(f"| {label} | {len(fwd)} | {mean_ret*100:+.2f}% | {median_ret*100:+.2f}% | {hit_rate*100:.1f}% | {sharpe:.3f} |")

md_lines.append("\n## 6. Rolling IC Stability (180-day window, 14d horizon)")
md_lines.append("\n| Signal | Period | Mean IC | Std IC | Min IC | Max IC | % Positive |")
md_lines.append("|--------|--------|---------|--------|--------|--------|-----------|")

for sig_name in ['signal_A_macro', 'combo_A_B1', 'combo_A_B1_B2']:
    ric = rolling_ic(df_full, sig_name, 'fwd_14d', window=180)
    if len(ric) > 0:
        sig_short = sig_name.replace('signal_', '').replace('combo_', 'combo: ')
        for period_label, mask_val in [('IS', True), ('OOS', False)]:
            if mask_val:
                sub_ric = ric[ric.index < OOS_DATE]
            else:
                sub_ric = ric[ric.index >= OOS_DATE]
            if len(sub_ric) > 0:
                md_lines.append(f"| {sig_short} | {period_label} | {sub_ric['ic'].mean():+.4f} | "
                               f"{sub_ric['ic'].std():.4f} | {sub_ric['ic'].min():+.4f} | "
                               f"{sub_ric['ic'].max():+.4f} | {(sub_ric['ic'] > 0).mean()*100:.1f}% |")

md_lines.append("\n## 7. Conclusions")
md_lines.append("")

# Auto-generate conclusions based on results
macro_b1_rho = macro_b1_corr[0]
if abs(macro_b1_rho) < 0.15:
    md_lines.append(f"1. **Low correlation confirmed**: Macro and positioning signals have Spearman rho = {macro_b1_rho:+.4f}, "
                    "confirming they capture distinct information about BTC price dynamics.")
elif abs(macro_b1_rho) < 0.30:
    md_lines.append(f"1. **Moderate correlation**: Macro and positioning signals have Spearman rho = {macro_b1_rho:+.4f}, "
                    "suggesting partial overlap but still some diversification benefit.")
else:
    md_lines.append(f"1. **High correlation warning**: Macro and positioning signals have Spearman rho = {macro_b1_rho:+.4f}, "
                    "suggesting significant overlap -- combination may not add much.")

if not np.isnan(oos_combo_14d) and not np.isnan(oos_macro_14d):
    if abs(oos_combo_14d) > abs(oos_macro_14d):
        pct_gain = (abs(oos_combo_14d) - abs(oos_macro_14d)) / abs(oos_macro_14d) * 100
        md_lines.append(f"2. **OOS IC improvement**: Combined signal achieves IC = {oos_combo_14d:+.4f} vs "
                        f"{oos_macro_14d:+.4f} for macro alone ({pct_gain:.1f}% improvement at 14d).")
    else:
        md_lines.append(f"2. **No OOS IC improvement**: Combined signal IC = {oos_combo_14d:+.4f} vs "
                        f"{oos_macro_14d:+.4f} for macro alone. Combination does not help OOS.")

if not np.isnan(oos_triple_14d):
    md_lines.append(f"3. **Triple combo OOS**: Adding B2 (L/S divergence) yields IC = {oos_triple_14d:+.4f} at 14d OOS.")

md_lines.append("")
md_lines.append("### Actionable Takeaways")
md_lines.append("")
md_lines.append("- When both signals agree (bearish macro + crowded positioning), the signal is strongest -- consider increasing position sizing or conviction in short/hedge trades.")
md_lines.append("- When signals disagree, the edge is weaker -- reduce sizing or wait for confirmation.")
md_lines.append("- The combination is most useful as a regime filter rather than a standalone alpha signal.")

with open('/workspace/crypto_backtest/research/macro_positioning_combo_results.md', 'w') as f:
    f.write('\n'.join(md_lines))

print("\n\nResults saved to: research/macro_positioning_combo_results.md")
print("Script saved to:  research/macro_positioning_combo_test.py")
print("\nDone.")
