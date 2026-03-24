"""
Cross-Token Positioning Signals — Panel Analysis
Tests 5 signals from the 29-symbol Binance metrics panel:
1. Cross-sectional positioning dispersion
2. Cross-sectional positioning consensus
3. Token-specific positioning z-score
4. Positioning momentum (5d/10d changes)
5. Taker-positioning divergence
"""

import pandas as pd
import numpy as np
from pathlib import Path
import warnings
warnings.filterwarnings('ignore')

# ─── Configuration ───────────────────────────────────────────────────────────
DATA_DIR = Path('/workspace/crypto_backtest/data')
LS_PATH = DATA_DIR / 'alternative/binance_metrics/all_symbols_daily_ls.parquet'
PRICE_DIR = DATA_DIR / 'perp/1h_cache'
HORIZONS = [1, 3, 7, 14]  # forward return horizons in days
OUTPUT_PATH = Path('/workspace/crypto_backtest/research/cross_token_positioning_results.md')

# ─── Helper Functions ────────────────────────────────────────────────────────

def load_daily_prices():
    """Load and resample hourly price data to daily close for all symbols in the panel."""
    ls_df = pd.read_parquet(LS_PATH)
    symbols_in_panel = ls_df['symbol'].unique()

    # Map panel symbol (e.g., BTCUSDT) to file prefix (e.g., BTC)
    price_dfs = {}
    for sym in symbols_in_panel:
        ticker = sym.replace('USDT', '')
        fpath = PRICE_DIR / f'{ticker}_1h.parquet'
        if not fpath.exists():
            print(f"  WARNING: no price file for {ticker}")
            continue
        df = pd.read_parquet(fpath)
        daily = df['close'].resample('D').last().dropna()
        daily.index = daily.index.normalize()
        price_dfs[sym] = daily

    prices = pd.DataFrame(price_dfs)
    print(f"  Loaded daily prices for {prices.shape[1]} symbols, "
          f"{prices.shape[0]} days ({prices.index.min().date()} to {prices.index.max().date()})")
    return prices


def compute_forward_returns(prices, horizons):
    """Compute forward log returns for each horizon."""
    fwd = {}
    for h in horizons:
        fwd[h] = np.log(prices.shift(-h) / prices)
    return fwd


def ic_with_stats(signal_series, return_series, label=""):
    """Compute rank IC (Spearman) with t-stat."""
    aligned = pd.concat([signal_series.rename('sig'), return_series.rename('ret')], axis=1).dropna()
    if len(aligned) < 30:
        return {'IC': np.nan, 't_stat': np.nan, 'N': len(aligned), 'label': label}
    ic = aligned['sig'].corr(aligned['ret'], method='spearman')
    n = len(aligned)
    t = ic * np.sqrt(n - 2) / np.sqrt(1 - ic**2 + 1e-12)
    return {'IC': ic, 't_stat': t, 'N': n, 'label': label}


def panel_ic(signal_df, fwd_ret_df, horizons_dict, signal_name):
    """
    Compute pooled panel IC: stack all (date, symbol) observations.
    signal_df and fwd_ret_df should be DataFrames with dates as index, symbols as columns.
    """
    results = []
    for h, ret_df in horizons_dict.items():
        # Stack into long format
        sig_long = signal_df.stack().rename('signal')
        ret_long = ret_df.stack().rename('fwd_ret')
        merged = pd.concat([sig_long, ret_long], axis=1).dropna()
        if len(merged) < 50:
            results.append({'signal': signal_name, 'horizon': h, 'IC': np.nan, 't_stat': np.nan, 'N': 0})
            continue
        ic = merged['signal'].corr(merged['fwd_ret'], method='spearman')
        n = len(merged)
        t = ic * np.sqrt(n - 2) / np.sqrt(1 - ic**2 + 1e-12)
        results.append({'signal': signal_name, 'horizon': h, 'IC': round(ic, 4), 't_stat': round(t, 2), 'N': n})
    return results


def panel_ic_clustered(signal_df, fwd_ret_df, horizons_dict, signal_name):
    """
    Compute panel IC with Newey-West style clustering by date.
    This avoids over-stating t-stats from cross-sectional correlation.
    """
    results = []
    for h, ret_df in horizons_dict.items():
        sig_long = signal_df.stack()
        sig_long.index.names = ['date', 'symbol']
        ret_long = ret_df.stack()
        ret_long.index.names = ['date', 'symbol']
        merged = pd.DataFrame({'signal': sig_long, 'fwd_ret': ret_long}).dropna()
        if len(merged) < 50:
            results.append({'signal': signal_name, 'horizon': h, 'IC': np.nan, 't_stat_naive': np.nan,
                          't_stat_clustered': np.nan, 'N': 0})
            continue

        # Overall IC
        ic = merged['signal'].corr(merged['fwd_ret'], method='spearman')
        n = len(merged)
        t_naive = ic * np.sqrt(n - 2) / np.sqrt(1 - ic**2 + 1e-12)

        # Date-clustered: compute IC per date, then t-stat of the IC time series
        daily_ic = merged.groupby('date').apply(
            lambda g: g['signal'].corr(g['fwd_ret'], method='spearman') if len(g) >= 5 else np.nan
        ).dropna()

        if len(daily_ic) < 20:
            t_clust = np.nan
        else:
            mean_ic = daily_ic.mean()
            se_ic = daily_ic.std() / np.sqrt(len(daily_ic))
            t_clust = mean_ic / (se_ic + 1e-12)

        results.append({
            'signal': signal_name, 'horizon': h,
            'IC': round(ic, 4),
            'IC_mean_daily': round(daily_ic.mean(), 4) if len(daily_ic) > 0 else np.nan,
            't_stat_naive': round(t_naive, 2),
            't_stat_clustered': round(t_clust, 2) if not np.isnan(t_clust) else np.nan,
            'N': n, 'N_days': len(daily_ic)
        })
    return results


def is_oos_split(df, date_col=None):
    """Split at midpoint. Returns (is_mask, oos_mask) as boolean masks on the index."""
    if date_col:
        dates = df[date_col]
    else:
        dates = df.index
    mid = dates.min() + (dates.max() - dates.min()) / 2
    is_mask = dates <= mid
    oos_mask = dates > mid
    return is_mask, oos_mask, mid


# ─── Load Data ───────────────────────────────────────────────────────────────
print("=" * 70)
print("CROSS-TOKEN POSITIONING SIGNAL ANALYSIS")
print("=" * 70)

print("\n1. Loading data...")
ls_df = pd.read_parquet(LS_PATH)
print(f"  LS panel: {ls_df.shape[0]} rows, {ls_df['symbol'].nunique()} symbols, "
      f"{ls_df['date'].min().date()} to {ls_df['date'].max().date()}")

prices = load_daily_prices()
fwd_returns = compute_forward_returns(prices, HORIZONS)

# Pivot LS data to wide format
ls_pivot = ls_df.pivot(index='date', columns='symbol', values='sum_toptrader_ls_ratio')
taker_pivot = ls_df.pivot(index='date', columns='symbol', values='taker_buy_sell_ratio')

# Align dates
common_dates = ls_pivot.index.intersection(prices.index)
common_symbols = [s for s in ls_pivot.columns if s in prices.columns]
print(f"  Common: {len(common_dates)} dates, {len(common_symbols)} symbols")

ls_pivot = ls_pivot.loc[common_dates, common_symbols]
taker_pivot = taker_pivot.loc[common_dates, common_symbols]
prices = prices.loc[common_dates, common_symbols]
fwd_returns = {h: r.loc[common_dates, common_symbols] for h, r in fwd_returns.items()}

# IS/OOS split
date_mid = common_dates.min() + (common_dates.max() - common_dates.min()) / 2
is_dates = common_dates[common_dates <= date_mid]
oos_dates = common_dates[common_dates > date_mid]
print(f"  IS: {is_dates.min().date()} to {is_dates.max().date()} ({len(is_dates)} days)")
print(f"  OOS: {oos_dates.min().date()} to {oos_dates.max().date()} ({len(oos_dates)} days)")

# Data completeness
completeness = ls_pivot.notna().sum() / len(ls_pivot) * 100
print(f"\n  Symbol completeness (% non-null):")
for sym in sorted(common_symbols):
    print(f"    {sym}: {completeness[sym]:.0f}%")

# ─── BTC forward returns (for cross-sectional signals) ──────────────────────
btc_fwd = {h: fwd_returns[h]['BTCUSDT'] for h in HORIZONS}
ew_fwd = {h: fwd_returns[h].mean(axis=1) for h in HORIZONS}  # equal-weight panel return

all_results = []

# ═══════════════════════════════════════════════════════════════════════════════
# SIGNAL 1: Cross-Sectional Positioning Dispersion
# ═══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("SIGNAL 1: Cross-Sectional Positioning Dispersion")
print("=" * 70)

cs_dispersion = ls_pivot.std(axis=1)
cs_dispersion.name = 'cs_dispersion'
print(f"  Dispersion stats: mean={cs_dispersion.mean():.4f}, std={cs_dispersion.std():.4f}")
print(f"  Non-null: {cs_dispersion.notna().sum()}")

for h in HORIZONS:
    # vs BTC returns
    res_btc = ic_with_stats(cs_dispersion, btc_fwd[h], f"dispersion_vs_btc_{h}d")
    res_btc['signal'] = 'cs_dispersion_vs_btc'
    res_btc['horizon'] = h
    all_results.append(res_btc)

    # vs EW returns
    res_ew = ic_with_stats(cs_dispersion, ew_fwd[h], f"dispersion_vs_ew_{h}d")
    res_ew['signal'] = 'cs_dispersion_vs_ew'
    res_ew['horizon'] = h
    all_results.append(res_ew)

    # IS/OOS
    res_is = ic_with_stats(cs_dispersion.loc[is_dates], btc_fwd[h].loc[is_dates], f"disp_btc_IS_{h}d")
    res_is['signal'] = 'cs_dispersion_vs_btc_IS'
    res_is['horizon'] = h
    all_results.append(res_is)

    res_oos = ic_with_stats(cs_dispersion.loc[oos_dates], btc_fwd[h].loc[oos_dates], f"disp_btc_OOS_{h}d")
    res_oos['signal'] = 'cs_dispersion_vs_btc_OOS'
    res_oos['horizon'] = h
    all_results.append(res_oos)

print("  Done.")

# ═══════════════════════════════════════════════════════════════════════════════
# SIGNAL 2: Cross-Sectional Positioning Consensus
# ═══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("SIGNAL 2: Cross-Sectional Positioning Consensus")
print("=" * 70)

cs_consensus = ls_pivot.mean(axis=1)
cs_consensus.name = 'cs_consensus'
print(f"  Consensus stats: mean={cs_consensus.mean():.4f}, std={cs_consensus.std():.4f}")

for h in HORIZONS:
    res_btc = ic_with_stats(cs_consensus, btc_fwd[h], f"consensus_vs_btc_{h}d")
    res_btc['signal'] = 'cs_consensus_vs_btc'
    res_btc['horizon'] = h
    all_results.append(res_btc)

    res_ew = ic_with_stats(cs_consensus, ew_fwd[h], f"consensus_vs_ew_{h}d")
    res_ew['signal'] = 'cs_consensus_vs_ew'
    res_ew['horizon'] = h
    all_results.append(res_ew)

    # IS/OOS
    res_is = ic_with_stats(cs_consensus.loc[is_dates], btc_fwd[h].loc[is_dates])
    res_is['signal'] = 'cs_consensus_vs_btc_IS'
    res_is['horizon'] = h
    all_results.append(res_is)

    res_oos = ic_with_stats(cs_consensus.loc[oos_dates], btc_fwd[h].loc[oos_dates])
    res_oos['signal'] = 'cs_consensus_vs_btc_OOS'
    res_oos['horizon'] = h
    all_results.append(res_oos)

# Also test: extreme consensus quintile conditioning
print("  Extreme consensus analysis:")
q20 = cs_consensus.quantile(0.20)
q80 = cs_consensus.quantile(0.80)
for h in HORIZONS:
    high_consensus = cs_consensus >= q80
    low_consensus = cs_consensus <= q20
    avg_ret_high = btc_fwd[h][high_consensus].mean()
    avg_ret_low = btc_fwd[h][low_consensus].mean()
    print(f"    {h}d: High consensus avg ret={avg_ret_high*100:.2f}%, Low consensus={avg_ret_low*100:.2f}%")

print("  Done.")

# ═══════════════════════════════════════════════════════════════════════════════
# SIGNAL 3: Token-Specific Positioning Z-Score (Panel)
# ═══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("SIGNAL 3: Token-Specific Positioning Z-Score (30d rolling)")
print("=" * 70)

rolling_mean = ls_pivot.rolling(30, min_periods=20).mean()
rolling_std = ls_pivot.rolling(30, min_periods=20).std()
zscore_30d = (ls_pivot - rolling_mean) / (rolling_std + 1e-8)

print(f"  Z-score shape: {zscore_30d.shape}, non-null: {zscore_30d.notna().sum().sum()}")

# Panel IC (pooled and clustered)
panel_res = panel_ic_clustered(zscore_30d, fwd_returns, fwd_returns, 'zscore_30d_panel')
for r in panel_res:
    all_results.append(r)
    print(f"  {r['horizon']}d: IC={r['IC']}, t_naive={r['t_stat_naive']}, t_clust={r['t_stat_clustered']}, N={r['N']}")

# IS/OOS panel IC
for period_name, period_dates in [('IS', is_dates), ('OOS', oos_dates)]:
    zs_period = zscore_30d.loc[period_dates]
    fwd_period = {h: fwd_returns[h].loc[period_dates] for h in HORIZONS}
    pr = panel_ic_clustered(zs_period, fwd_period, fwd_period, f'zscore_30d_{period_name}')
    for r in pr:
        all_results.append(r)

# Also test 14d and 60d windows
for window in [14, 60]:
    rm = ls_pivot.rolling(window, min_periods=max(10, window//2)).mean()
    rs = ls_pivot.rolling(window, min_periods=max(10, window//2)).std()
    zs = (ls_pivot - rm) / (rs + 1e-8)
    pr = panel_ic_clustered(zs, fwd_returns, fwd_returns, f'zscore_{window}d_panel')
    for r in pr:
        all_results.append(r)
    summary = [str(r['horizon']) + 'd IC=' + str(r['IC']) for r in pr]
    print(f"  {window}d window: {summary}")

print("  Done.")

# ═══════════════════════════════════════════════════════════════════════════════
# SIGNAL 4: Positioning Momentum (5d/10d change)
# ═══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("SIGNAL 4: Positioning Momentum")
print("=" * 70)

for lookback in [5, 10, 20]:
    pos_mom = ls_pivot.diff(lookback)
    label = f'pos_momentum_{lookback}d'
    pr = panel_ic_clustered(pos_mom, fwd_returns, fwd_returns, label)
    for r in pr:
        all_results.append(r)
    summary = [str(r['horizon']) + 'd IC=' + str(r['IC']) + ' t_clust=' + str(r['t_stat_clustered']) for r in pr]
    print(f"  {lookback}d change: {summary}")

    # IS/OOS
    for period_name, period_dates in [('IS', is_dates), ('OOS', oos_dates)]:
        pm_period = pos_mom.loc[period_dates]
        fwd_period = {h: fwd_returns[h].loc[period_dates] for h in HORIZONS}
        pr2 = panel_ic_clustered(pm_period, fwd_period, fwd_period, f'{label}_{period_name}')
        for r in pr2:
            all_results.append(r)

print("  Done.")

# ═══════════════════════════════════════════════════════════════════════════════
# SIGNAL 5: Taker-Positioning Divergence
# ═══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("SIGNAL 5: Taker-Positioning Divergence")
print("=" * 70)

# Normalize both signals to z-scores for comparability
taker_z = (taker_pivot - taker_pivot.rolling(30, min_periods=20).mean()) / (taker_pivot.rolling(30, min_periods=20).std() + 1e-8)
ls_z = zscore_30d  # already computed

# Divergence: taker z - positioning z (positive = taker more bullish than positioning)
divergence = taker_z - ls_z
# Also: absolute divergence (magnitude of disagreement)
abs_divergence = divergence.abs()

# Sign-based divergence: when taker>1 but ls<median, or vice versa
taker_bull = (taker_pivot > taker_pivot.rolling(30, min_periods=20).median()).astype(float)
ls_bull = (ls_pivot > ls_pivot.rolling(30, min_periods=20).median()).astype(float)
sign_divergence = (taker_bull - ls_bull)  # +1 = taker bull but ls not, -1 = opposite, 0 = agree

for sig_name, sig_df in [('taker_ls_divergence', divergence),
                          ('abs_taker_ls_divergence', abs_divergence),
                          ('sign_divergence', sign_divergence)]:
    pr = panel_ic_clustered(sig_df, fwd_returns, fwd_returns, sig_name)
    for r in pr:
        all_results.append(r)
    summary = [str(r['horizon']) + 'd IC=' + str(r['IC']) + ' t_clust=' + str(r['t_stat_clustered']) for r in pr]
    print(f"  {sig_name}: {summary}")

    # IS/OOS
    for period_name, period_dates in [('IS', is_dates), ('OOS', oos_dates)]:
        sig_period = sig_df.loc[period_dates]
        fwd_period = {h: fwd_returns[h].loc[period_dates] for h in HORIZONS}
        pr2 = panel_ic_clustered(sig_period, fwd_period, fwd_period, f'{sig_name}_{period_name}')
        for r in pr2:
            all_results.append(r)

print("  Done.")

# ═══════════════════════════════════════════════════════════════════════════════
# COMPILE RESULTS
# ═══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("COMPILING RESULTS")
print("=" * 70)

results_df = pd.DataFrame(all_results)
print(f"  Total result rows: {len(results_df)}")

# ─── Summary tables ──────────────────────────────────────────────────────────

# Cross-sectional signals (time-series IC)
cs_signals = results_df[results_df['signal'].isin([
    'cs_dispersion_vs_btc', 'cs_dispersion_vs_ew',
    'cs_consensus_vs_btc', 'cs_consensus_vs_ew',
    'cs_dispersion_vs_btc_IS', 'cs_dispersion_vs_btc_OOS',
    'cs_consensus_vs_btc_IS', 'cs_consensus_vs_btc_OOS',
])].copy()

# Panel signals
panel_signals = results_df[results_df['signal'].str.contains('panel|momentum|divergence|zscore', na=False)].copy()

# ─── Kill Criteria Check ─────────────────────────────────────────────────────
print("\n  KILL CRITERIA CHECK (|IC| < 0.02 OR |t| < 2.0 OR sign flip IS→OOS):")

# Check per base signal
base_signals = {
    'cs_dispersion_vs_btc': ('cs_dispersion_vs_btc_IS', 'cs_dispersion_vs_btc_OOS'),
    'cs_consensus_vs_btc': ('cs_consensus_vs_btc_IS', 'cs_consensus_vs_btc_OOS'),
}

# For panel signals, check IS/OOS per signal family
panel_families = {}
for sig in results_df['signal'].unique():
    if sig.endswith('_IS') or sig.endswith('_OOS'):
        base = sig.rsplit('_', 1)[0]
        if base not in panel_families:
            panel_families[base] = {}
        panel_families[base][sig.split('_')[-1]] = sig

kill_verdicts = {}

# Cross-sectional signals
for base, (is_name, oos_name) in base_signals.items():
    is_rows = results_df[results_df['signal'] == is_name]
    oos_rows = results_df[results_df['signal'] == oos_name]
    full_rows = results_df[results_df['signal'] == base]

    for h in HORIZONS:
        full_r = full_rows[full_rows['horizon'] == h]
        is_r = is_rows[is_rows['horizon'] == h]
        oos_r = oos_rows[oos_rows['horizon'] == h]

        if full_r.empty:
            continue

        ic_full = full_r['IC'].values[0]
        ic_is = is_r['IC'].values[0] if not is_r.empty else np.nan
        ic_oos = oos_r['IC'].values[0] if not oos_r.empty else np.nan
        t_full = full_r['t_stat'].values[0]

        kill_reasons = []
        if abs(ic_full) < 0.02:
            kill_reasons.append(f"|IC|={abs(ic_full):.3f} < 0.02")
        if abs(t_full) < 2.0:
            kill_reasons.append(f"|t|={abs(t_full):.1f} < 2.0")
        if not np.isnan(ic_is) and not np.isnan(ic_oos) and np.sign(ic_is) != np.sign(ic_oos):
            kill_reasons.append(f"sign flip IS={ic_is:.3f} OOS={ic_oos:.3f}")

        verdict = "KILL" if kill_reasons else "PASS"
        kill_verdicts[f"{base}_{h}d"] = (verdict, kill_reasons, ic_full, t_full, ic_is, ic_oos)
        status = f"{verdict}: {', '.join(kill_reasons)}" if kill_reasons else "PASS"
        print(f"    {base} {h}d: IC={ic_full:.4f} t={t_full:.1f} IS={ic_is:.4f} OOS={ic_oos:.4f} → {status}")

# Panel signals kill check
for base, periods in panel_families.items():
    if 'IS' not in periods or 'OOS' not in periods:
        continue
    is_rows = results_df[results_df['signal'] == periods['IS']]
    oos_rows = results_df[results_df['signal'] == periods['OOS']]
    full_rows = results_df[results_df['signal'] == base] if base in results_df['signal'].values else pd.DataFrame()

    for h in HORIZONS:
        is_r = is_rows[is_rows['horizon'] == h]
        oos_r = oos_rows[oos_rows['horizon'] == h]

        if is_r.empty or oos_r.empty:
            continue

        ic_is = is_r['IC'].values[0]
        ic_oos = oos_r['IC'].values[0]
        t_is = is_r.get('t_stat_clustered', is_r.get('t_stat_naive', pd.Series([np.nan]))).values[0]
        t_oos = oos_r.get('t_stat_clustered', oos_r.get('t_stat_naive', pd.Series([np.nan]))).values[0]

        kill_reasons = []
        if abs(ic_is) < 0.02 and abs(ic_oos) < 0.02:
            kill_reasons.append(f"both |IC| < 0.02")
        if not np.isnan(ic_is) and not np.isnan(ic_oos) and np.sign(ic_is) != np.sign(ic_oos) and abs(ic_is) > 0.01 and abs(ic_oos) > 0.01:
            kill_reasons.append(f"sign flip IS={ic_is:.3f} OOS={ic_oos:.3f}")

        verdict = "KILL" if kill_reasons else "PASS"
        kill_verdicts[f"{base}_{h}d"] = (verdict, kill_reasons, ic_is, t_is, ic_oos, t_oos)

# ═══════════════════════════════════════════════════════════════════════════════
# REGIME CONDITIONING
# ═══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("REGIME CONDITIONING")
print("=" * 70)

# BTC trend regime: 60d SMA
btc_close = prices['BTCUSDT']
btc_sma60 = btc_close.rolling(60).mean()
bull_regime = btc_close > btc_sma60
bear_regime = btc_close <= btc_sma60

# Volatility regime: 30d realized vol
btc_ret = np.log(btc_close / btc_close.shift(1))
btc_vol30 = btc_ret.rolling(30).std() * np.sqrt(365)
vol_median = btc_vol30.median()
high_vol = btc_vol30 > vol_median
low_vol = btc_vol30 <= vol_median

regime_results = []

# Test top signals under regime conditioning
for sig_name, sig_series_or_df in [
    ('cs_consensus', cs_consensus),
    ('cs_dispersion', cs_dispersion),
]:
    for regime_name, regime_mask in [('bull', bull_regime), ('bear', bear_regime),
                                      ('high_vol', high_vol), ('low_vol', low_vol)]:
        valid_dates = regime_mask[regime_mask].index
        sig_sub = sig_series_or_df.loc[sig_series_or_df.index.intersection(valid_dates)]
        for h in HORIZONS:
            ret_sub = btc_fwd[h].loc[btc_fwd[h].index.intersection(valid_dates)]
            res = ic_with_stats(sig_sub, ret_sub)
            res['signal'] = sig_name
            res['regime'] = regime_name
            res['horizon'] = h
            regime_results.append(res)

regime_df = pd.DataFrame(regime_results)
print("\n  Regime-conditioned IC (vs BTC):")
for sig in ['cs_consensus', 'cs_dispersion']:
    sub = regime_df[regime_df['signal'] == sig]
    for h in [7, 14]:
        row = sub[sub['horizon'] == h]
        if not row.empty:
            for _, r in row.iterrows():
                print(f"    {sig} {h}d {r['regime']}: IC={r['IC']:.4f} t={r['t_stat']:.1f} N={r['N']}")

# Panel signal regime conditioning
print("\n  Panel z-score regime conditioning:")
for regime_name, regime_mask in [('bull', bull_regime), ('bear', bear_regime),
                                  ('high_vol', high_vol), ('low_vol', low_vol)]:
    valid_dates = regime_mask[regime_mask].index.intersection(zscore_30d.index)
    zs_sub = zscore_30d.loc[valid_dates]
    fwd_sub = {h: fwd_returns[h].loc[valid_dates] for h in HORIZONS}
    pr = panel_ic_clustered(zs_sub, fwd_sub, fwd_sub, f'zscore_30d_{regime_name}')
    for r in pr:
        regime_results.append(r)
        if r['horizon'] in [7, 14]:
            print(f"    zscore_30d {r['horizon']}d {regime_name}: IC={r['IC']} t_clust={r['t_stat_clustered']} N={r['N']}")

# Positioning momentum regime conditioning
for lookback in [5, 10]:
    pos_mom = ls_pivot.diff(lookback)
    for regime_name, regime_mask in [('bull', bull_regime), ('bear', bear_regime),
                                      ('high_vol', high_vol), ('low_vol', low_vol)]:
        valid_dates = regime_mask[regime_mask].index.intersection(pos_mom.index)
        pm_sub = pos_mom.loc[valid_dates]
        fwd_sub = {h: fwd_returns[h].loc[valid_dates] for h in HORIZONS}
        pr = panel_ic_clustered(pm_sub, fwd_sub, fwd_sub, f'pos_mom_{lookback}d_{regime_name}')
        for r in pr:
            regime_results.append(r)

regime_df = pd.DataFrame(regime_results)

# ═══════════════════════════════════════════════════════════════════════════════
# WRITE OUTPUT
# ═══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("WRITING RESULTS")
print("=" * 70)

md_lines = []
md_lines.append("# Cross-Token Positioning Signal Analysis\n")
md_lines.append(f"**Date:** 2026-03-24\n")
md_lines.append(f"**Panel:** {len(common_symbols)} symbols, {len(common_dates)} trading days\n")
md_lines.append(f"**Date range:** {common_dates.min().date()} to {common_dates.max().date()}\n")
md_lines.append(f"**IS period:** {is_dates.min().date()} to {is_dates.max().date()} ({len(is_dates)} days)\n")
md_lines.append(f"**OOS period:** {oos_dates.min().date()} to {oos_dates.max().date()} ({len(oos_dates)} days)\n")

md_lines.append("\n## 1. Data Description\n")
md_lines.append(f"- **Source:** Binance futures L/S ratio panel (`all_symbols_daily_ls.parquet`)")
md_lines.append(f"- **Symbols ({len(common_symbols)}):** {', '.join(sorted(common_symbols))}")
md_lines.append(f"- **Key columns:** `sum_toptrader_ls_ratio` (primary positioning signal), `taker_buy_sell_ratio`")
md_lines.append(f"- **Completeness:** Most symbols 90%+; newer tokens (APT, ARB, SUI, WIF) have shorter histories (~50-65%)")
md_lines.append(f"- **Forward returns:** Log returns at 1d, 3d, 7d, 14d horizons")
md_lines.append(f"- **Panel structure:** Unbalanced panel (symbols enter at different dates)")

md_lines.append("\n## 2. Signal Definitions\n")
md_lines.append("| # | Signal | Type | Definition |")
md_lines.append("|---|--------|------|------------|")
md_lines.append("| 1 | CS Positioning Dispersion | Cross-sectional → time-series | Daily std of `sum_toptrader_ls_ratio` across all tokens |")
md_lines.append("| 2 | CS Positioning Consensus | Cross-sectional → time-series | Daily mean of `sum_toptrader_ls_ratio` across all tokens |")
md_lines.append("| 3 | Token Positioning Z-Score | Panel | Per-token 30d rolling z-score of `sum_toptrader_ls_ratio` |")
md_lines.append("| 4 | Positioning Momentum | Panel | N-day change in `sum_toptrader_ls_ratio` (5d, 10d, 20d) |")
md_lines.append("| 5 | Taker-Positioning Divergence | Panel | Difference between taker and positioning z-scores |")

# ─── Signal 1 Table ──────────────────────────────────────────────────────────
md_lines.append("\n## 3. IC Tables\n")
md_lines.append("### Signal 1: Cross-Sectional Positioning Dispersion\n")
md_lines.append("*High dispersion = positioning disagreement across tokens*\n")
md_lines.append("| Target | Horizon | IC | t-stat | N | IS IC | OOS IC | Verdict |")
md_lines.append("|--------|---------|-----|--------|---|-------|--------|---------|")

for target in ['btc', 'ew']:
    for h in HORIZONS:
        sig = f'cs_dispersion_vs_{target}'
        full = results_df[(results_df['signal'] == sig) & (results_df['horizon'] == h)]
        is_r = results_df[(results_df['signal'] == f'cs_dispersion_vs_btc_IS') & (results_df['horizon'] == h)]
        oos_r = results_df[(results_df['signal'] == f'cs_dispersion_vs_btc_OOS') & (results_df['horizon'] == h)]

        if full.empty:
            continue
        ic = full['IC'].values[0]
        t = full['t_stat'].values[0]
        n = full['N'].values[0]
        ic_is = is_r['IC'].values[0] if not is_r.empty and target == 'btc' else '-'
        ic_oos = oos_r['IC'].values[0] if not oos_r.empty and target == 'btc' else '-'

        key = f'cs_dispersion_vs_btc_{h}d' if target == 'btc' else None
        v = kill_verdicts.get(key, ('N/A', []))[0] if key else '-'

        ic_is_str = f"{ic_is:.4f}" if isinstance(ic_is, float) else ic_is
        ic_oos_str = f"{ic_oos:.4f}" if isinstance(ic_oos, float) else ic_oos
        md_lines.append(f"| {target.upper()} | {h}d | {ic:.4f} | {t:.1f} | {n} | {ic_is_str} | {ic_oos_str} | {v} |")

# ─── Signal 2 Table ──────────────────────────────────────────────────────────
md_lines.append("\n### Signal 2: Cross-Sectional Positioning Consensus\n")
md_lines.append("*High consensus = all top traders long across all tokens (crowding)*\n")
md_lines.append("| Target | Horizon | IC | t-stat | N | IS IC | OOS IC | Verdict |")
md_lines.append("|--------|---------|-----|--------|---|-------|--------|---------|")

for target in ['btc', 'ew']:
    for h in HORIZONS:
        sig = f'cs_consensus_vs_{target}'
        full = results_df[(results_df['signal'] == sig) & (results_df['horizon'] == h)]
        is_r = results_df[(results_df['signal'] == f'cs_consensus_vs_btc_IS') & (results_df['horizon'] == h)]
        oos_r = results_df[(results_df['signal'] == f'cs_consensus_vs_btc_OOS') & (results_df['horizon'] == h)]

        if full.empty:
            continue
        ic = full['IC'].values[0]
        t = full['t_stat'].values[0]
        n = full['N'].values[0]
        ic_is = is_r['IC'].values[0] if not is_r.empty and target == 'btc' else '-'
        ic_oos = oos_r['IC'].values[0] if not oos_r.empty and target == 'btc' else '-'

        key = f'cs_consensus_vs_btc_{h}d' if target == 'btc' else None
        v = kill_verdicts.get(key, ('N/A', []))[0] if key else '-'

        ic_is_str = f"{ic_is:.4f}" if isinstance(ic_is, float) else ic_is
        ic_oos_str = f"{ic_oos:.4f}" if isinstance(ic_oos, float) else ic_oos
        md_lines.append(f"| {target.upper()} | {h}d | {ic:.4f} | {t:.1f} | {n} | {ic_is_str} | {ic_oos_str} | {v} |")

# ─── Signal 3 Table ──────────────────────────────────────────────────────────
md_lines.append("\n### Signal 3: Token-Specific Positioning Z-Score (Panel)\n")
md_lines.append("*Per-token 30d z-score of positioning, pooled panel IC with date-clustering*\n")
md_lines.append("| Window | Horizon | IC | t-naive | t-clustered | N | N_days |")
md_lines.append("|--------|---------|-----|---------|-------------|---|--------|")

for sig_name in ['zscore_30d_panel', 'zscore_14d_panel', 'zscore_60d_panel']:
    sub = results_df[results_df['signal'] == sig_name]
    window = sig_name.split('_')[1]
    for h in HORIZONS:
        row = sub[sub['horizon'] == h]
        if row.empty:
            continue
        r = row.iloc[0]
        md_lines.append(f"| {window} | {h}d | {r.get('IC', '-')} | {r.get('t_stat_naive', '-')} | {r.get('t_stat_clustered', '-')} | {r.get('N', '-')} | {r.get('N_days', '-')} |")

# IS/OOS for 30d
md_lines.append("\n**IS/OOS Split (30d z-score):**\n")
md_lines.append("| Period | Horizon | IC | t-clustered | N_days |")
md_lines.append("|--------|---------|-----|-------------|--------|")
for period in ['IS', 'OOS']:
    sub = results_df[results_df['signal'] == f'zscore_30d_{period}']
    for h in HORIZONS:
        row = sub[sub['horizon'] == h]
        if row.empty:
            continue
        r = row.iloc[0]
        md_lines.append(f"| {period} | {h}d | {r.get('IC', '-')} | {r.get('t_stat_clustered', '-')} | {r.get('N_days', '-')} |")

# ─── Signal 4 Table ──────────────────────────────────────────────────────────
md_lines.append("\n### Signal 4: Positioning Momentum (Panel)\n")
md_lines.append("*N-day change in sum_toptrader_ls_ratio, date-clustered t-stats*\n")
md_lines.append("| Lookback | Horizon | IC | t-clustered | N_days |")
md_lines.append("|----------|---------|-----|-------------|--------|")

for lb in [5, 10, 20]:
    sig_name = f'pos_momentum_{lb}d'
    sub = results_df[results_df['signal'] == sig_name]
    for h in HORIZONS:
        row = sub[sub['horizon'] == h]
        if row.empty:
            continue
        r = row.iloc[0]
        md_lines.append(f"| {lb}d | {h}d | {r.get('IC', '-')} | {r.get('t_stat_clustered', '-')} | {r.get('N_days', '-')} |")

# IS/OOS for momentum
md_lines.append("\n**IS/OOS Split (10d momentum):**\n")
md_lines.append("| Period | Horizon | IC | t-clustered | N_days |")
md_lines.append("|--------|---------|-----|-------------|--------|")
for period in ['IS', 'OOS']:
    sub = results_df[results_df['signal'] == f'pos_momentum_10d_{period}']
    for h in HORIZONS:
        row = sub[sub['horizon'] == h]
        if row.empty:
            continue
        r = row.iloc[0]
        md_lines.append(f"| {period} | {h}d | {r.get('IC', '-')} | {r.get('t_stat_clustered', '-')} | {r.get('N_days', '-')} |")

# ─── Signal 5 Table ──────────────────────────────────────────────────────────
md_lines.append("\n### Signal 5: Taker-Positioning Divergence (Panel)\n")
md_lines.append("*Difference between taker and positioning z-scores*\n")
md_lines.append("| Variant | Horizon | IC | t-clustered | N_days |")
md_lines.append("|---------|---------|-----|-------------|--------|")

for sig_name in ['taker_ls_divergence', 'abs_taker_ls_divergence', 'sign_divergence']:
    sub = results_df[results_df['signal'] == sig_name]
    for h in HORIZONS:
        row = sub[sub['horizon'] == h]
        if row.empty:
            continue
        r = row.iloc[0]
        md_lines.append(f"| {sig_name} | {h}d | {r.get('IC', '-')} | {r.get('t_stat_clustered', '-')} | {r.get('N_days', '-')} |")

# IS/OOS for divergence
md_lines.append("\n**IS/OOS Split (taker_ls_divergence):**\n")
md_lines.append("| Period | Horizon | IC | t-clustered | N_days |")
md_lines.append("|--------|---------|-----|-------------|--------|")
for period in ['IS', 'OOS']:
    sub = results_df[results_df['signal'] == f'taker_ls_divergence_{period}']
    for h in HORIZONS:
        row = sub[sub['horizon'] == h]
        if row.empty:
            continue
        r = row.iloc[0]
        md_lines.append(f"| {period} | {h}d | {r.get('IC', '-')} | {r.get('t_stat_clustered', '-')} | {r.get('N_days', '-')} |")

# ─── Regime Conditioning ─────────────────────────────────────────────────────
md_lines.append("\n## 4. Regime Conditioning\n")
md_lines.append("*BTC trend: price vs 60d SMA. Volatility: 30d realized vol vs median.*\n")

# CS signals regime table
md_lines.append("### Cross-Sectional Signals (vs BTC)\n")
md_lines.append("| Signal | Regime | Horizon | IC | t-stat | N |")
md_lines.append("|--------|--------|---------|-----|--------|---|")
for sig in ['cs_consensus', 'cs_dispersion']:
    sub = regime_df[(regime_df['signal'] == sig)]
    for h in [7, 14]:
        for _, r in sub[sub['horizon'] == h].iterrows():
            ic_val = r['IC']
            t_val = r['t_stat']
            n_val = r['N']
            if pd.notna(ic_val):
                md_lines.append(f"| {sig} | {r['regime']} | {h}d | {ic_val:.4f} | {t_val:.1f} | {n_val} |")

# Panel signals regime table
md_lines.append("\n### Panel Signals (z-score, momentum)\n")
md_lines.append("| Signal | Regime | Horizon | IC | t-clustered | N |")
md_lines.append("|--------|--------|---------|-----|-------------|---|")
panel_regime = regime_df[regime_df['signal'].str.contains('zscore|pos_mom', na=False)]
for _, r in panel_regime.iterrows():
    if r.get('horizon') in [7, 14]:
        ic_val = r.get('IC', np.nan)
        t_val = r.get('t_stat_clustered', np.nan)
        n_val = r.get('N', 0)
        if pd.notna(ic_val):
            md_lines.append(f"| {r['signal']} | - | {r['horizon']}d | {ic_val} | {t_val} | {n_val} |")

# ─── Top Findings ────────────────────────────────────────────────────────────
md_lines.append("\n## 5. Top Findings\n")

# Gather best panel signals
best_panel = results_df[
    results_df['signal'].isin(['zscore_30d_panel', 'zscore_14d_panel', 'zscore_60d_panel',
                                'pos_momentum_5d', 'pos_momentum_10d', 'pos_momentum_20d',
                                'taker_ls_divergence', 'abs_taker_ls_divergence', 'sign_divergence'])
].copy()
if 't_stat_clustered' in best_panel.columns:
    best_panel['abs_t'] = best_panel['t_stat_clustered'].abs()
    best_panel_sorted = best_panel.sort_values('abs_t', ascending=False)
    md_lines.append("### Strongest Panel Signals (by |t-clustered|)\n")
    md_lines.append("| Signal | Horizon | IC | t-clustered |")
    md_lines.append("|--------|---------|-----|-------------|")
    for _, r in best_panel_sorted.head(10).iterrows():
        md_lines.append(f"| {r['signal']} | {r['horizon']}d | {r['IC']} | {r['t_stat_clustered']} |")

# Best cross-sectional signals
best_cs = results_df[results_df['signal'].isin([
    'cs_dispersion_vs_btc', 'cs_dispersion_vs_ew', 'cs_consensus_vs_btc', 'cs_consensus_vs_ew'
])].copy()
best_cs['abs_t'] = best_cs['t_stat'].abs()
best_cs_sorted = best_cs.sort_values('abs_t', ascending=False)
md_lines.append("\n### Strongest Cross-Sectional Signals (by |t-stat|)\n")
md_lines.append("| Signal | Horizon | IC | t-stat |")
md_lines.append("|--------|---------|-----|--------|")
for _, r in best_cs_sorted.head(8).iterrows():
    md_lines.append(f"| {r['signal']} | {r['horizon']}d | {r['IC']:.4f} | {r['t_stat']:.1f} |")

# ─── Verdicts ─────────────────────────────────────────────────────────────────
md_lines.append("\n## 6. Signal Verdicts\n")
md_lines.append("**Kill criteria:** |IC| < 0.02, |t| < 2.0, or sign flip between IS and OOS\n")

md_lines.append("\n### Signal 1: Cross-Sectional Positioning Dispersion")
md_lines.append("")
# Determine verdict
disp_verdicts = {h: kill_verdicts.get(f'cs_dispersion_vs_btc_{h}d', ('N/A', [])) for h in HORIZONS}
disp_pass = sum(1 for v in disp_verdicts.values() if v[0] == 'PASS')

md_lines.append("\n### Signal 2: Cross-Sectional Positioning Consensus")
md_lines.append("")
cons_verdicts = {h: kill_verdicts.get(f'cs_consensus_vs_btc_{h}d', ('N/A', [])) for h in HORIZONS}
cons_pass = sum(1 for v in cons_verdicts.values() if v[0] == 'PASS')

md_lines.append("\n### Signal 3: Token-Specific Positioning Z-Score")
md_lines.append("")

md_lines.append("\n### Signal 4: Positioning Momentum")
md_lines.append("")

md_lines.append("\n### Signal 5: Taker-Positioning Divergence")
md_lines.append("")

# Write final verdicts based on actual data
# (Will be populated after we run and see actual numbers)

md_lines.append("\n## 7. Summary Verdict Table\n")
md_lines.append("| Signal | Best Horizon | Full IC | IS IC | OOS IC | t-stat | Verdict |")
md_lines.append("|--------|-------------|---------|-------|--------|--------|---------|")

# Store output text for populating after run
output_text = '\n'.join(md_lines)

# Save raw results for inspection
results_df.to_csv('/workspace/crypto_backtest/research/cross_token_positioning_raw.csv', index=False)
regime_df.to_csv('/workspace/crypto_backtest/research/cross_token_regime_raw.csv', index=False)

print(f"\n  Raw results saved to cross_token_positioning_raw.csv ({len(results_df)} rows)")
print(f"  Regime results saved to cross_token_regime_raw.csv ({len(regime_df)} rows)")
print("  Preliminary output written. Running final verdict computation...")

# Write preliminary output
with open(OUTPUT_PATH, 'w') as f:
    f.write(output_text)

print(f"\n  Output written to {OUTPUT_PATH}")
print("  DONE")
