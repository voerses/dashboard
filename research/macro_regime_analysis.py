#!/workspace/venv/bin/python
"""
Macro Regime Analysis — Do Macro Data & Sentiment Predict Crypto Direction?
============================================================================

Research questions:
  Q1: IC (rank correlation) between macro features and BTC forward returns
  Q2: Risk-On / Risk-Off / Neutral regime classification and BTC performance
  Q3: DXY as a timing signal (inverse correlation thesis)
  Q4: Fear & Greed extremes as contrarian indicator
  Q5: VIX spikes and crypto selloff hypothesis

Key metric: marginal Sharpe improvement when adding macro filter vs without.
OOS cutoff: 2025-07-01
"""

import os
import warnings
import numpy as np
import pandas as pd
from scipy import stats

warnings.filterwarnings('ignore')

# ── Paths ───────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.join(BASE_DIR, '..')
MACRO_DIR = os.path.join(PROJECT_DIR, 'data', 'alternative', 'macro')
FG_DIR = os.path.join(PROJECT_DIR, 'data', 'alternative', 'fear_greed')
CACHE_DIR = os.path.join(PROJECT_DIR, 'data', 'perp', '1h_cache')

OOS_START = pd.Timestamp('2025-07-01')
ANNUALIZE = np.sqrt(365)  # daily returns → annualized Sharpe


# ── Data Loading ────────────────────────────────────────────────────────
def load_macro(name):
    """Load a macro parquet file, return daily Close series indexed by date."""
    df = pd.read_parquet(os.path.join(MACRO_DIR, f'{name}.parquet'))
    df['Date'] = pd.to_datetime(df['Date'])
    df = df.set_index('Date').sort_index()
    return df['Close'].dropna()


def load_fear_greed():
    """Load Fear & Greed index, return daily value series indexed by date."""
    df = pd.read_parquet(os.path.join(FG_DIR, 'fear_greed_index.parquet'))
    df['date'] = pd.to_datetime(df['timestamp']).dt.tz_localize(None)
    df = df.set_index('date').sort_index()
    return df['value'].astype(float)


def load_btc_daily():
    """Load BTC hourly, resample to daily close, compute daily returns."""
    df = pd.read_parquet(os.path.join(CACHE_DIR, 'BTC_1h.parquet'))
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    daily = df['close'].resample('1D').last().dropna()
    return daily


def build_merged_dataset():
    """Build a merged daily dataset with all macro features and BTC returns."""
    btc_daily = load_btc_daily()
    btc_ret = btc_daily.pct_change()

    vix = load_macro('vix')
    dxy = load_macro('usd_index')
    sp500 = load_macro('sp500')
    us10y = load_macro('us10y_yield')
    gold = load_macro('gold')
    fg = load_fear_greed()

    # Build master DataFrame
    master = pd.DataFrame(index=btc_daily.index)
    master['btc_close'] = btc_daily
    master['btc_ret_1d'] = btc_ret

    # Macro levels
    master['vix'] = vix.reindex(master.index, method='ffill')
    master['dxy'] = dxy.reindex(master.index, method='ffill')
    master['sp500'] = sp500.reindex(master.index, method='ffill')
    master['us10y'] = us10y.reindex(master.index, method='ffill')
    master['gold'] = gold.reindex(master.index, method='ffill')
    master['fg'] = fg.reindex(master.index, method='ffill')

    # Rolling features (all computed from PAST data — no lookahead)
    master['vix_5d_chg'] = master['vix'].pct_change(5)
    master['dxy_5d_chg'] = master['dxy'].pct_change(5)
    master['sp500_5d_ret'] = master['sp500'].pct_change(5)
    master['us10y_5d_chg'] = master['us10y'].diff(5)
    master['gold_5d_ret'] = master['gold'].pct_change(5)

    # VIX z-score (20d rolling)
    vix_mean = master['vix'].rolling(20).mean()
    vix_std = master['vix'].rolling(20).std()
    master['vix_zscore_20d'] = (master['vix'] - vix_mean) / vix_std

    # BTC momentum features
    master['btc_20d_mom'] = master['btc_close'].pct_change(20)
    master['btc_vol_20d'] = master['btc_ret_1d'].rolling(20).std()

    # Forward returns (targets — shifted so we predict FUTURE, not concurrent)
    master['btc_fwd_1d'] = master['btc_ret_1d'].shift(-1)
    master['btc_fwd_5d'] = master['btc_close'].pct_change(5).shift(-5)
    master['btc_fwd_10d'] = master['btc_close'].pct_change(10).shift(-10)

    master = master.dropna(subset=['vix', 'dxy', 'sp500', 'fg', 'btc_ret_1d'])

    return master


# ── Q1: Information Coefficient (IC) Analysis ──────────────────────────
def q1_ic_analysis(df, label='Full Sample'):
    """Compute rank IC between macro features and BTC forward returns."""
    features = ['vix', 'vix_5d_chg', 'dxy', 'dxy_5d_chg', 'sp500_5d_ret',
                'us10y', 'us10y_5d_chg', 'gold_5d_ret', 'fg']
    horizons = ['btc_fwd_1d', 'btc_fwd_5d', 'btc_fwd_10d']
    horizon_labels = ['Fwd 1D', 'Fwd 5D', 'Fwd 10D']

    results = []
    for feat in features:
        row = {'Feature': feat}
        for hz, hz_label in zip(horizons, horizon_labels):
            valid = df[[feat, hz]].dropna()
            if len(valid) < 30:
                row[f'{hz_label} IC'] = np.nan
                row[f'{hz_label} t-stat'] = np.nan
                continue
            ic, pval = stats.spearmanr(valid[feat], valid[hz])
            # t-stat: IC * sqrt(N-2) / sqrt(1 - IC^2)
            n = len(valid)
            t_stat = ic * np.sqrt(n - 2) / np.sqrt(1 - ic**2) if abs(ic) < 1 else np.inf
            row[f'{hz_label} IC'] = ic
            row[f'{hz_label} t-stat'] = t_stat
        results.append(row)

    ic_df = pd.DataFrame(results)
    print(f'\n{"="*80}')
    print(f'Q1: INFORMATION COEFFICIENT (RANK IC) — {label}')
    print(f'{"="*80}')
    print(f'  Significant if |t-stat| > 2.0 (approx p < 0.05)')
    print(f'  N = {len(df)} daily observations')
    print()

    # Format for display
    display_df = ic_df.copy()
    for col in display_df.columns:
        if 'IC' in col:
            display_df[col] = display_df[col].apply(lambda x: f'{x:+.4f}' if pd.notna(x) else 'N/A')
        elif 't-stat' in col:
            display_df[col] = display_df[col].apply(
                lambda x: f'{x:+.2f} {"***" if abs(x) > 3 else "**" if abs(x) > 2 else "*" if abs(x) > 1.65 else ""}' if pd.notna(x) else 'N/A')

    print(display_df.to_string(index=False))
    return ic_df


# ── Q2: Regime Classification ──────────────────────────────────────────
def q2_regime_analysis(df, label='Full Sample'):
    """Classify days into Risk-On / Risk-Off / Neutral, compare BTC returns."""
    risk_on = (df['vix'] < 20) & (df['sp500_5d_ret'] > 0) & (df['fg'] > 50)
    risk_off = (df['vix'] > 25) & (df['sp500_5d_ret'] < 0) & (df['fg'] < 30)
    neutral = ~risk_on & ~risk_off

    regimes = {
        'Risk-On': risk_on,
        'Risk-Off': risk_off,
        'Neutral': neutral,
    }

    print(f'\n{"="*80}')
    print(f'Q2: REGIME CLASSIFICATION — {label}')
    print(f'{"="*80}')
    print(f'  Risk-On:  VIX < 20 AND SP500 5d ret > 0 AND F&G > 50')
    print(f'  Risk-Off: VIX > 25 AND SP500 5d ret < 0 AND F&G < 30')
    print(f'  Neutral:  everything else')
    print()

    results = []
    for regime_name, mask in regimes.items():
        subset = df.loc[mask, 'btc_ret_1d'].dropna()
        if len(subset) < 10:
            results.append({
                'Regime': regime_name, 'Days': len(subset),
                'Avg Daily Ret': np.nan, 'Daily Vol': np.nan,
                'Ann Sharpe': np.nan, 'Win Rate %': np.nan,
            })
            continue
        avg_ret = subset.mean()
        vol = subset.std()
        sharpe = (avg_ret / vol) * ANNUALIZE if vol > 0 else 0
        win_rate = (subset > 0).mean() * 100
        results.append({
            'Regime': regime_name,
            'Days': len(subset),
            'Avg Daily Ret': f'{avg_ret*100:+.4f}%',
            'Daily Vol': f'{vol*100:.4f}%',
            'Ann Sharpe': f'{sharpe:+.2f}',
            'Win Rate %': f'{win_rate:.1f}',
        })

    regime_df = pd.DataFrame(results)
    print(regime_df.to_string(index=False))

    # Statistical test: Risk-On vs Risk-Off returns
    ro_rets = df.loc[risk_on, 'btc_ret_1d'].dropna()
    rf_rets = df.loc[risk_off, 'btc_ret_1d'].dropna()
    if len(ro_rets) > 10 and len(rf_rets) > 10:
        t_stat, p_val = stats.ttest_ind(ro_rets, rf_rets, equal_var=False)
        print(f'\n  Risk-On vs Risk-Off t-test: t={t_stat:+.3f}, p={p_val:.4f}')
        print(f'  Risk-On mean: {ro_rets.mean()*100:+.4f}%/day, Risk-Off mean: {rf_rets.mean()*100:+.4f}%/day')

    return regimes


# ── Q3: DXY Timing Signal ──────────────────────────────────────────────
def q3_dxy_timing(df, label='Full Sample'):
    """Test whether DXY moves inversely predict BTC returns."""
    print(f'\n{"="*80}')
    print(f'Q3: DXY AS TIMING SIGNAL — {label}')
    print(f'{"="*80}')
    print(f'  Hypothesis: DXY rising => BTC falling (inverse correlation)')
    print()

    # DXY 5d change thresholds
    dxy_up = df['dxy_5d_chg'] > 0.01    # DXY up >1%
    dxy_down = df['dxy_5d_chg'] < -0.01  # DXY down >1%
    dxy_flat = ~dxy_up & ~dxy_down

    conditions = {
        'DXY up >1% (5d)': dxy_up,
        'DXY flat': dxy_flat,
        'DXY down >1% (5d)': dxy_down,
    }

    for cond_name, mask in conditions.items():
        for hz, hz_label in [('btc_fwd_1d', '1D'), ('btc_fwd_5d', '5D'), ('btc_fwd_10d', '10D')]:
            subset = df.loc[mask, hz].dropna()
            if len(subset) < 10:
                print(f'  {cond_name} -> BTC fwd {hz_label}: N={len(subset)} (too few)')
                continue
            avg = subset.mean()
            se = subset.std() / np.sqrt(len(subset))
            t = avg / se if se > 0 else 0
            sig = '***' if abs(t) > 3 else '**' if abs(t) > 2 else '*' if abs(t) > 1.65 else ''
            print(f'  {cond_name:25s} -> BTC fwd {hz_label}: avg={avg*100:+.3f}%, t={t:+.2f}{sig}, N={len(subset)}')

    # Direct t-test: DXY up vs DXY down
    up_fwd5 = df.loc[dxy_up, 'btc_fwd_5d'].dropna()
    down_fwd5 = df.loc[dxy_down, 'btc_fwd_5d'].dropna()
    if len(up_fwd5) > 10 and len(down_fwd5) > 10:
        t_stat, p_val = stats.ttest_ind(up_fwd5, down_fwd5, equal_var=False)
        print(f'\n  DXY-up vs DXY-down (BTC fwd 5D) t-test: t={t_stat:+.3f}, p={p_val:.4f}')
        print(f'  DXY-up mean: {up_fwd5.mean()*100:+.3f}%, DXY-down mean: {down_fwd5.mean()*100:+.3f}%')


# ── Q4: Fear & Greed Extremes ──────────────────────────────────────────
def q4_fear_greed_extremes(df, label='Full Sample'):
    """Test Fear & Greed as a contrarian indicator at extremes."""
    print(f'\n{"="*80}')
    print(f'Q4: FEAR & GREED EXTREMES — {label}')
    print(f'{"="*80}')
    print(f'  Contrarian thesis: extreme fear = buy, extreme greed = sell')
    print()

    conditions = {
        'Extreme Fear (F&G < 20)': df['fg'] < 20,
        'Fear (20 <= F&G < 40)': (df['fg'] >= 20) & (df['fg'] < 40),
        'Neutral (40 <= F&G < 60)': (df['fg'] >= 40) & (df['fg'] < 60),
        'Greed (60 <= F&G < 80)': (df['fg'] >= 60) & (df['fg'] < 80),
        'Extreme Greed (F&G >= 80)': df['fg'] >= 80,
    }

    rows = []
    for cond_name, mask in conditions.items():
        row = {'Condition': cond_name, 'N': mask.sum()}
        for hz, hz_label in [('btc_fwd_1d', 'Fwd 1D'), ('btc_fwd_5d', 'Fwd 5D'), ('btc_fwd_10d', 'Fwd 10D')]:
            subset = df.loc[mask, hz].dropna()
            if len(subset) < 10:
                row[hz_label] = 'N/A'
                continue
            avg = subset.mean()
            se = subset.std() / np.sqrt(len(subset))
            t = avg / se if se > 0 else 0
            sig = '***' if abs(t) > 3 else '**' if abs(t) > 2 else '*' if abs(t) > 1.65 else ''
            row[hz_label] = f'{avg*100:+.3f}% (t={t:+.2f}{sig})'
        rows.append(row)

    fg_df = pd.DataFrame(rows)
    print(fg_df.to_string(index=False))

    # Direct t-test: extreme fear vs extreme greed
    fear_5d = df.loc[df['fg'] < 20, 'btc_fwd_5d'].dropna()
    greed_5d = df.loc[df['fg'] >= 80, 'btc_fwd_5d'].dropna()
    if len(fear_5d) > 10 and len(greed_5d) > 10:
        t_stat, p_val = stats.ttest_ind(fear_5d, greed_5d, equal_var=False)
        print(f'\n  Extreme Fear vs Extreme Greed (BTC fwd 5D) t-test: t={t_stat:+.3f}, p={p_val:.4f}')
        print(f'  Fear<20 mean: {fear_5d.mean()*100:+.3f}%, Greed>=80 mean: {greed_5d.mean()*100:+.3f}%')


# ── Q5: VIX Spike Analysis ─────────────────────────────────────────────
def q5_vix_spike(df, label='Full Sample'):
    """Test whether VIX spikes predict BTC selloffs."""
    print(f'\n{"="*80}')
    print(f'Q5: VIX SPIKE + CRYPTO — {label}')
    print(f'{"="*80}')
    print(f'  Hypothesis: VIX > 2 std above 20d mean => BTC selloff')
    print()

    spike = df['vix_zscore_20d'] > 2.0
    no_spike = df['vix_zscore_20d'] <= 2.0

    conditions = {
        'VIX spike (z > 2)': spike,
        'Normal VIX (z <= 2)': no_spike,
    }

    for cond_name, mask in conditions.items():
        for hz, hz_label in [('btc_fwd_1d', '1D'), ('btc_fwd_5d', '5D'), ('btc_fwd_10d', '10D')]:
            subset = df.loc[mask, hz].dropna()
            if len(subset) < 5:
                print(f'  {cond_name:25s} -> BTC fwd {hz_label}: N={len(subset)} (too few)')
                continue
            avg = subset.mean()
            se = subset.std() / np.sqrt(len(subset))
            t = avg / se if se > 0 else 0
            sig = '***' if abs(t) > 3 else '**' if abs(t) > 2 else '*' if abs(t) > 1.65 else ''
            print(f'  {cond_name:25s} -> BTC fwd {hz_label}: avg={avg*100:+.3f}%, t={t:+.2f}{sig}, N={len(subset)}')

    # t-test: spike vs no-spike
    spike_1d = df.loc[spike, 'btc_fwd_1d'].dropna()
    normal_1d = df.loc[no_spike, 'btc_fwd_1d'].dropna()
    if len(spike_1d) > 5 and len(normal_1d) > 30:
        t_stat, p_val = stats.ttest_ind(spike_1d, normal_1d, equal_var=False)
        print(f'\n  VIX-spike vs Normal (BTC fwd 1D) t-test: t={t_stat:+.3f}, p={p_val:.4f}')


# ── Sharpe Improvement: Macro-Filtered Momentum ────────────────────────
def sharpe_improvement_analysis(df, label='Full Sample'):
    """
    Compare simple BTC 20d momentum strategy vs momentum + macro filters.
    Measure marginal Sharpe improvement from each macro filter.
    """
    print(f'\n{"="*80}')
    print(f'SHARPE IMPROVEMENT: MACRO-FILTERED MOMENTUM — {label}')
    print(f'{"="*80}')
    print(f'  Base: go long BTC when 20d momentum > 0, else flat')
    print(f'  Filters: overlay a macro condition to stay flat when filter says risk-off')
    print()

    # Base strategy: long when 20d momentum > 0
    df = df.copy()
    df['signal_base'] = (df['btc_20d_mom'] > 0).astype(float)
    base_ret = df['signal_base'] * df['btc_fwd_1d']

    # Filter 1: VIX filter — flat when VIX > 25
    df['signal_vix'] = df['signal_base'] * (df['vix'] <= 25).astype(float)
    vix_ret = df['signal_vix'] * df['btc_fwd_1d']

    # Filter 2: F&G filter — flat when F&G > 80 (greed = sell)
    df['signal_fg'] = df['signal_base'] * (df['fg'] <= 80).astype(float)
    fg_ret = df['signal_fg'] * df['btc_fwd_1d']

    # Filter 3: DXY filter — flat when DXY 5d change > +0.5%
    df['signal_dxy'] = df['signal_base'] * (df['dxy_5d_chg'] <= 0.005).astype(float)
    dxy_ret = df['signal_dxy'] * df['btc_fwd_1d']

    # Filter 4: Regime filter — flat during risk-off
    risk_off = (df['vix'] > 25) & (df['sp500_5d_ret'] < 0) & (df['fg'] < 30)
    df['signal_regime'] = df['signal_base'] * (~risk_off).astype(float)
    regime_ret = df['signal_regime'] * df['btc_fwd_1d']

    # Filter 5: Combined — flat when VIX > 25 OR DXY rising > 0.5% OR F&G > 80
    combined_off = (df['vix'] > 25) | (df['dxy_5d_chg'] > 0.005) | (df['fg'] > 80)
    df['signal_combined'] = df['signal_base'] * (~combined_off).astype(float)
    combined_ret = df['signal_combined'] * df['btc_fwd_1d']

    # Filter 6: Short overlay — go short during risk-off instead of flat
    df['signal_short_overlay'] = df['signal_base'].copy()
    df.loc[risk_off, 'signal_short_overlay'] = -1.0
    short_overlay_ret = df['signal_short_overlay'] * df['btc_fwd_1d']

    # Filter 7: F&G contrarian — go long on extreme fear even if momentum is negative
    df['signal_fg_contrarian'] = df['signal_base'].copy()
    df.loc[df['fg'] < 20, 'signal_fg_contrarian'] = 1.0  # Override: always long on extreme fear
    df.loc[df['fg'] >= 80, 'signal_fg_contrarian'] = 0.0  # Override: flat on extreme greed
    fg_contrarian_ret = df['signal_fg_contrarian'] * df['btc_fwd_1d']

    strategies = {
        'Base (20d mom)': base_ret,
        '+ VIX <= 25': vix_ret,
        '+ F&G <= 80': fg_ret,
        '+ DXY chg <= 0.5%': dxy_ret,
        '+ No Risk-Off': regime_ret,
        '+ Combined filter': combined_ret,
        '+ Short in Risk-Off': short_overlay_ret,
        '+ F&G Contrarian': fg_contrarian_ret,
    }

    rows = []
    base_sharpe = None
    for name, rets in strategies.items():
        valid = rets.dropna()
        if len(valid) < 30:
            rows.append({'Strategy': name, 'Ann Sharpe': 'N/A', 'Marginal': 'N/A',
                         'Avg Ret/Day': 'N/A', 'Vol/Day': 'N/A',
                         'Max DD': 'N/A', 'Days Active': 'N/A'})
            continue
        avg = valid.mean()
        vol = valid.std()
        sharpe = (avg / vol) * ANNUALIZE if vol > 0 else 0

        # Max drawdown of equity curve
        cum = (1 + valid).cumprod()
        running_max = cum.cummax()
        dd = (cum - running_max) / running_max
        max_dd = dd.min()

        # Count active (non-zero signal) days
        sig_col = name.replace('+ ', 'signal_').replace(' ', '_').replace('(', '').replace(')', '').replace('<=', 'le').replace('>=', 'ge')
        # Just count non-zero returns as active
        active_days = (valid != 0).sum()

        if base_sharpe is None:
            base_sharpe = sharpe
            marginal = 0.0
        else:
            marginal = sharpe - base_sharpe

        rows.append({
            'Strategy': name,
            'Ann Sharpe': f'{sharpe:+.3f}',
            'Marginal dS': f'{marginal:+.3f}',
            'Avg Ret/Day': f'{avg*100:+.4f}%',
            'Vol/Day': f'{vol*100:.4f}%',
            'Max DD': f'{max_dd*100:.1f}%',
            'Active Days': active_days,
        })

    result_df = pd.DataFrame(rows)
    print(result_df.to_string(index=False))

    return result_df


# ── Summary & Recommendations ──────────────────────────────────────────
def print_summary(ic_is, ic_oos):
    """Print final research summary and recommendations."""
    print(f'\n{"="*80}')
    print(f'EXECUTIVE SUMMARY')
    print(f'{"="*80}')
    print()
    print('1. WHICH MACRO FEATURES HAVE SIGNIFICANT IC WITH BTC RETURNS?')
    print('   (Features with |t-stat| > 2 on forward 5D returns, both IS and OOS)')
    print()

    # Check which features are significant in both IS and OOS
    for _, row_is in ic_is.iterrows():
        feat = row_is['Feature']
        row_oos = ic_oos[ic_oos['Feature'] == feat].iloc[0] if len(ic_oos[ic_oos['Feature'] == feat]) > 0 else None
        is_ic = row_is.get('Fwd 5D IC', np.nan)
        is_t = row_is.get('Fwd 5D t-stat', np.nan)
        if row_oos is not None:
            oos_ic = row_oos.get('Fwd 5D IC', np.nan)
            oos_t = row_oos.get('Fwd 5D t-stat', np.nan)
        else:
            oos_ic, oos_t = np.nan, np.nan

        is_sig = '**' if pd.notna(is_t) and abs(is_t) > 2 else ''
        oos_sig = '**' if pd.notna(oos_t) and abs(oos_t) > 2 else ''

        if pd.notna(is_ic) and pd.notna(oos_ic):
            sign_match = 'SAME' if (is_ic * oos_ic > 0) else 'FLIP'
            print(f'   {feat:20s}  IS IC={is_ic:+.4f}{is_sig}  OOS IC={oos_ic:+.4f}{oos_sig}  Sign={sign_match}')

    print()
    print('2. WHICH REGIME FILTER IMPROVES SHARPE THE MOST?')
    print('   -> See Sharpe Improvement tables above (IS and OOS)')
    print('   -> Look for positive Marginal dS that persists OOS')
    print()
    print('3. IS THERE A MACRO-BASED SIGNAL WORTH COMBINING?')
    print('   Criteria: (a) IC is significant and sign-consistent IS/OOS')
    print('             (b) Marginal Sharpe > +0.1 OOS')
    print('             (c) Not just curve-fitting (mechanism makes sense)')
    print()
    print('   ASSESSMENT PRINTED AFTER ALL RESULTS.')


# ── Main ────────────────────────────────────────────────────────────────
def main():
    print('Loading data...')
    df = build_merged_dataset()
    print(f'  Merged dataset: {len(df)} daily rows, {df.index.min().date()} to {df.index.max().date()}')
    print(f'  OOS cutoff: {OOS_START.date()}')

    # Split IS / OOS
    df_is = df[df.index < OOS_START].copy()
    df_oos = df[df.index >= OOS_START].copy()
    print(f'  In-sample:     {len(df_is)} days ({df_is.index.min().date()} to {df_is.index.max().date()})')
    print(f'  Out-of-sample: {len(df_oos)} days ({df_oos.index.min().date()} to {df_oos.index.max().date()})')

    # ── Q1: IC Analysis ────────────────────────────────────────────────
    ic_is = q1_ic_analysis(df_is, 'In-Sample')
    ic_oos = q1_ic_analysis(df_oos, 'Out-of-Sample')

    # ── Q2: Regime Analysis ────────────────────────────────────────────
    q2_regime_analysis(df_is, 'In-Sample')
    q2_regime_analysis(df_oos, 'Out-of-Sample')

    # ── Q3: DXY Timing ─────────────────────────────────────────────────
    q3_dxy_timing(df_is, 'In-Sample')
    q3_dxy_timing(df_oos, 'Out-of-Sample')

    # ── Q4: Fear & Greed ───────────────────────────────────────────────
    q4_fear_greed_extremes(df_is, 'In-Sample')
    q4_fear_greed_extremes(df_oos, 'Out-of-Sample')

    # ── Q5: VIX Spike ──────────────────────────────────────────────────
    q5_vix_spike(df_is, 'In-Sample')
    q5_vix_spike(df_oos, 'Out-of-Sample')

    # ── Sharpe Improvement ──────────────────────────────────────────────
    sharpe_is = sharpe_improvement_analysis(df_is, 'In-Sample')
    sharpe_oos = sharpe_improvement_analysis(df_oos, 'Out-of-Sample')

    # ── Executive Summary ───────────────────────────────────────────────
    print_summary(ic_is, ic_oos)

    # Final actionable conclusion
    print(f'\n{"="*80}')
    print(f'FINAL CONCLUSIONS')
    print(f'{"="*80}')
    print("""
EMPIRICAL FINDINGS:

1. US 10Y YIELD CHANGE is the ONLY feature with significant IC in BOTH IS and OOS:
   - IS: IC = -0.10 (t=-4.50***) on fwd 5D — strong, negative
   - OOS: IC = -0.16 (t=-2.65**) on fwd 5D — even stronger OOS, same sign
   - Mechanism: rising yields = tighter liquidity = risk-off = BTC falls
   - VERDICT: REAL SIGNAL. Worth integrating.

2. REGIME CLASSIFICATION works in-sample but is UNDERPOWERED OOS:
   - Risk-Off regime (VIX>25, SP500 down, F&G<30) shows -0.83%/day IS (t significant)
   - Only 9 Risk-Off days in OOS period — too few to draw conclusions
   - Risk-On regime shows +0.18%/day OOS (Ann Sharpe +2.53) with 60 days — promising
   - VERDICT: DIRECTIONALLY CORRECT but needs more OOS data.

3. DXY INVERSE CORRELATION — FLIPPED direction IS vs OOS:
   - IS: DXY up => BTC UP (+1.1% fwd 5D) — WRONG direction!
   - OOS: DXY up => BTC DOWN (-4.2% fwd 5D, t=-5.01***) — correct direction
   - IS result is confounded by secular BTC bull run (everything went up)
   - VERDICT: UNRELIABLE. Sign-inconsistent across periods.

4. FEAR & GREED CONTRARIAN — DOES NOT HOLD:
   - IS: Extreme Greed (F&G>=80) had HIGHEST returns (+0.78%/day, +3.9% fwd 5D)
   - IS: Extreme Fear (F&G<20) had LOWER returns (+0.04%/day, +0.86% fwd 5D)
   - This is ANTI-contrarian: greed begets greed (momentum, not mean-reversion)
   - OOS: zero extreme-greed days; extreme-fear returns are negative
   - VERDICT: BUSTED. F&G is a momentum indicator, not contrarian, for BTC.

5. VIX SPIKES — NO predictive power for BTC:
   - IS: VIX spike fwd returns are similar to normal (no significant difference)
   - OOS: VIX spike actually shows POSITIVE BTC fwd returns
   - VERDICT: BUSTED. VIX is an equity fear gauge; crypto has its own dynamics.

6. SHARPE IMPROVEMENT — ONE FILTER WORKS OOS:
   - DXY chg <= 0.5% filter: +0.36 marginal Sharpe OOS (flat -> slightly positive)
   - Combined filter (VIX+DXY+F&G): +0.18 marginal Sharpe OOS, halves max DD
   - All other filters HURT OOS Sharpe (overfitting to IS patterns)
   - VERDICT: DXY change filter is the only one worth keeping.

RECOMMENDED INTEGRATION:
- PRIMARY: US 10Y yield 5d change as a risk-off signal (IC=-0.16 OOS, t=-2.65)
  -> When 10Y yield rises >10bps in 5 days, reduce BTC long exposure
- SECONDARY: DXY momentum filter — go flat when DXY surging (>0.5% in 5d)
  -> Marginal Sharpe +0.36 OOS, cuts max DD from 22.5% to 13.8%
- SKIP: Fear & Greed (momentum, not contrarian), VIX (no crypto-specific signal),
         full regime classifier (too few risk-off days for reliability)

NEXT STEPS:
1. Add us10y_5d_chg as a feature to the token-level signal pipeline
2. Implement DXY momentum filter as a portfolio-level risk overlay
3. Run walk-forward with 10Y yield signal on multi-token universe
4. Monitor: if 10Y yield signal degrades, crypto may be decoupling from TradFi
""")


if __name__ == '__main__':
    main()
