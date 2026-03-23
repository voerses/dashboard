#!/workspace/venv/bin/python
"""
Funding Rate Dispersion & Cross-Sectional Funding Structure Analysis
=====================================================================

HYPOTHESIS: The distribution of funding rates across tokens contains
information about market regime and can predict returns.

SIGNALS TESTED:
  1. Funding rate dispersion (cross-sectional std)
  2. Funding skew (cross-sectional skewness)
  3. Fraction of tokens with positive funding (crowding indicator)
  4. Mean absolute funding (overall leverage indicator)
  5. Funding rate rank (cross-sectional percentile per token)

TARGETS:
  - Individual token forward returns (1d, 3d, 7d)
  - Market-wide returns (equal-weight basket)
  - Cross-sectional momentum spread

TEMPORAL SPLIT: train < 2025-07-01, test >= 2025-07-01
"""

import os
import warnings
import numpy as np
import pandas as pd
from scipy import stats as scipy_stats

warnings.filterwarnings('ignore')

# ── Paths & Config ─────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.join(BASE_DIR, '..')
CACHE_DIR = os.path.join(PROJECT_DIR, 'data', 'perp', '1h_cache')

TRAIN_END = pd.Timestamp('2025-07-01')
FWD_HORIZONS = {'1d': 24, '3d': 72, '7d': 168}  # in hours
MIN_HISTORY_HOURS = 500  # minimum data before train_end
MIN_OOS_HOURS = 200      # minimum data after train_end
MIN_AVG_VOLUME = 50_000  # USD


# ── Data Loading ───────────────────────────────────────────────────────
def load_all_tokens():
    """Load all qualifying tokens into a dict of DataFrames."""
    files = sorted([f for f in os.listdir(CACHE_DIR) if f.endswith('.parquet')])
    token_data = {}
    skipped = {'short_history': 0, 'short_oos': 0, 'low_volume': 0, 'no_funding': 0}

    for f in files:
        token = f.replace('_1h.parquet', '')
        df = pd.read_parquet(os.path.join(CACHE_DIR, f))

        if df.index.tz is not None:
            df.index = df.index.tz_localize(None)

        # Pick best funding column
        if 'funding_1h' in df.columns and df['funding_1h'].notna().sum() > 100:
            funding_col = 'funding_1h'
        elif 'funding_rate' in df.columns and df['funding_rate'].notna().sum() > 100:
            funding_col = 'funding_rate'
        else:
            skipped['no_funding'] += 1
            continue

        # Filter checks
        pre_train = df[df.index < TRAIN_END]
        post_train = df[df.index >= TRAIN_END]

        if len(pre_train) < MIN_HISTORY_HOURS:
            skipped['short_history'] += 1
            continue
        if len(post_train) < MIN_OOS_HOURS:
            skipped['short_oos'] += 1
            continue
        if post_train['volume'].mean() < MIN_AVG_VOLUME:
            skipped['low_volume'] += 1
            continue

        df = df[['open', 'high', 'low', 'close', 'volume', funding_col]].copy()
        df.rename(columns={funding_col: 'funding'}, inplace=True)

        token_data[token] = df

    print(f"Loaded {len(token_data)} tokens (skipped: {skipped})")
    return token_data


# ── Build Panel ────────────────────────────────────────────────────────
def build_panel(token_data):
    """Build aligned panel DataFrames for close prices and funding rates."""
    # Collect all closes and funding rates
    close_dict = {}
    funding_dict = {}
    volume_dict = {}

    for token, df in token_data.items():
        close_dict[token] = df['close']
        funding_dict[token] = df['funding']
        volume_dict[token] = df['volume']

    close_panel = pd.DataFrame(close_dict)
    funding_panel = pd.DataFrame(funding_dict)
    volume_panel = pd.DataFrame(volume_dict)

    # Only keep timestamps where we have at least 10 tokens
    min_tokens = 10
    valid_mask = funding_panel.notna().sum(axis=1) >= min_tokens
    close_panel = close_panel[valid_mask]
    funding_panel = funding_panel[valid_mask]
    volume_panel = volume_panel[valid_mask]

    print(f"Panel: {close_panel.shape[0]} timestamps x {close_panel.shape[1]} tokens")
    print(f"Date range: {close_panel.index.min()} to {close_panel.index.max()}")
    print(f"Tokens with funding at each timestamp: "
          f"min={funding_panel.notna().sum(axis=1).min()}, "
          f"median={funding_panel.notna().sum(axis=1).median():.0f}, "
          f"max={funding_panel.notna().sum(axis=1).max()}")

    return close_panel, funding_panel, volume_panel


# ── Cross-Sectional Signals ───────────────────────────────────────────
def compute_cross_sectional_signals(funding_panel):
    """Compute cross-sectional funding signals at each timestamp."""
    signals = pd.DataFrame(index=funding_panel.index)

    # 1. Funding rate dispersion (cross-sectional std)
    signals['funding_dispersion'] = funding_panel.std(axis=1)

    # 2. Funding skew (cross-sectional skewness)
    signals['funding_skew'] = funding_panel.apply(
        lambda row: row.dropna().skew() if row.dropna().shape[0] >= 5 else np.nan, axis=1
    )

    # 3. Fraction of tokens with positive funding
    signals['frac_positive_funding'] = (funding_panel > 0).sum(axis=1) / funding_panel.notna().sum(axis=1)

    # 4. Mean absolute funding (leverage indicator)
    signals['mean_abs_funding'] = funding_panel.abs().mean(axis=1)

    # 5. Mean funding (directional bias)
    signals['mean_funding'] = funding_panel.mean(axis=1)

    # 6. Median funding
    signals['median_funding'] = funding_panel.median(axis=1)

    # 7. Funding IQR (robust dispersion)
    q75 = funding_panel.quantile(0.75, axis=1)
    q25 = funding_panel.quantile(0.25, axis=1)
    signals['funding_iqr'] = q75 - q25

    # 8. Extreme funding count (|funding| > 0.001)
    signals['n_extreme_funding'] = (funding_panel.abs() > 0.001).sum(axis=1)
    signals['frac_extreme_funding'] = signals['n_extreme_funding'] / funding_panel.notna().sum(axis=1)

    # Smooth signals with 8h rolling mean to reduce noise
    for col in signals.columns:
        signals[f'{col}_8h'] = signals[col].rolling(8, min_periods=4).mean()

    print(f"\nCross-sectional signals computed: {[c for c in signals.columns if '_8h' not in c]}")
    return signals


def compute_funding_ranks(funding_panel):
    """Compute cross-sectional funding percentile rank for each token at each timestamp."""
    ranks = funding_panel.rank(axis=1, pct=True)
    return ranks


# ── Forward Returns ────────────────────────────────────────────────────
def compute_forward_returns(close_panel):
    """Compute forward returns at multiple horizons."""
    fwd_returns = {}
    for label, hours in FWD_HORIZONS.items():
        fwd_returns[label] = close_panel.shift(-hours) / close_panel - 1
    return fwd_returns


def compute_market_returns(close_panel):
    """Compute equal-weight market basket returns."""
    # Simple equal-weight return for each hour
    hourly_returns = close_panel.pct_change(1)
    market_return = hourly_returns.mean(axis=1)

    mkt_fwd = {}
    for label, hours in FWD_HORIZONS.items():
        # Cumulative market return over next N hours
        cum = (1 + market_return).rolling(hours).apply(lambda x: x.prod() - 1, raw=True)
        mkt_fwd[label] = cum.shift(-hours)

    return mkt_fwd, market_return


def compute_momentum_spread(close_panel):
    """Top quintile minus bottom quintile return spread (cross-sectional momentum)."""
    ret_24h = close_panel.pct_change(24)
    spreads = {}

    for label, hours in FWD_HORIZONS.items():
        fwd = close_panel.shift(-hours) / close_panel - 1

        def _spread(row_idx):
            r = ret_24h.iloc[row_idx].dropna()
            f = fwd.iloc[row_idx].dropna()
            common = r.index.intersection(f.index)
            if len(common) < 10:
                return np.nan
            r, f = r[common], f[common]
            top_q = r.quantile(0.8)
            bot_q = r.quantile(0.2)
            top_fwd = f[r >= top_q].mean()
            bot_fwd = f[r <= bot_q].mean()
            return top_fwd - bot_fwd

        # Compute at daily frequency for speed
        daily_idx = list(range(0, len(close_panel), 24))
        vals = [_spread(i) for i in daily_idx]
        spread_series = pd.Series(vals, index=close_panel.index[daily_idx], name=f'mom_spread_{label}')
        spreads[label] = spread_series

    return spreads


# ── Signal Evaluation ──────────────────────────────────────────────────
def information_coefficient(signal, target):
    """Compute rank IC (Spearman correlation) between signal and target."""
    merged = pd.concat([signal, target], axis=1).dropna()
    if len(merged) < 30:
        return np.nan, np.nan, np.nan
    s = merged.iloc[:, 0]
    t = merged.iloc[:, 1]
    ic, pval = scipy_stats.spearmanr(s, t)

    # Compute rolling IC for t-stat (using ~monthly windows)
    window = min(720, len(merged) // 5)  # ~30 days or 1/5 of data
    if window < 50:
        return ic, np.nan, np.nan

    rolling_ics = []
    for i in range(0, len(merged) - window, window):
        chunk_s = merged.iloc[i:i+window, 0]
        chunk_t = merged.iloc[i:i+window, 1]
        chunk_ic, _ = scipy_stats.spearmanr(chunk_s, chunk_t)
        if not np.isnan(chunk_ic):
            rolling_ics.append(chunk_ic)

    if len(rolling_ics) < 3:
        return ic, np.nan, np.nan

    mean_ic = np.mean(rolling_ics)
    std_ic = np.std(rolling_ics, ddof=1)
    t_stat = mean_ic / (std_ic / np.sqrt(len(rolling_ics))) if std_ic > 0 else np.nan
    sign_consistency = np.mean([1 if x > 0 else 0 for x in rolling_ics])

    return ic, t_stat, sign_consistency


def evaluate_signal_vs_market(signal, market_fwd, label_prefix, split_date):
    """Evaluate one signal against market-wide forward returns."""
    results = []
    for horizon, mkt_ret in market_fwd.items():
        # Align
        merged = pd.concat([signal, mkt_ret], axis=1).dropna()
        if len(merged) < 50:
            continue

        # Train
        train = merged[merged.index < split_date]
        test = merged[merged.index >= split_date]

        for period_name, data in [('train', train), ('test', test)]:
            if len(data) < 30:
                ic_val, t_val, sign_val = np.nan, np.nan, np.nan
            else:
                ic_val, t_val, sign_val = information_coefficient(
                    data.iloc[:, 0], data.iloc[:, 1]
                )
            results.append({
                'signal': label_prefix,
                'target': f'market_{horizon}',
                'period': period_name,
                'IC': ic_val,
                't_stat': t_val,
                'sign_consistency': sign_val,
                'n_obs': len(data)
            })
    return results


def evaluate_signal_vs_momentum_spread(signal, spreads, label_prefix, split_date):
    """Evaluate one signal against momentum spread."""
    results = []
    for horizon, spread in spreads.items():
        merged = pd.concat([signal, spread], axis=1).dropna()
        if len(merged) < 20:
            continue

        train = merged[merged.index < split_date]
        test = merged[merged.index >= split_date]

        for period_name, data in [('train', train), ('test', test)]:
            if len(data) < 10:
                ic_val, t_val, sign_val = np.nan, np.nan, np.nan
            else:
                ic_val, t_val, sign_val = information_coefficient(
                    data.iloc[:, 0], data.iloc[:, 1]
                )
            results.append({
                'signal': label_prefix,
                'target': f'mom_spread_{horizon}',
                'period': period_name,
                'IC': ic_val,
                't_stat': t_val,
                'sign_consistency': sign_val,
                'n_obs': len(data)
            })
    return results


# ── Token-Level Rank Signal Analysis ───────────────────────────────────
def evaluate_funding_rank_signal(funding_ranks, fwd_returns, split_date):
    """
    Test: does a token's cross-sectional funding rank predict its forward return?
    Pool all token-timestamp observations. Also test extreme deciles.
    """
    results = []

    for horizon, fwd in fwd_returns.items():
        # Sample at 8h frequency to reduce autocorrelation
        sample_idx = list(range(0, len(funding_ranks), 8))
        ranks_sampled = funding_ranks.iloc[sample_idx]
        fwd_sampled = fwd.iloc[sample_idx]

        # Stack into long format
        ranks_long = ranks_sampled.stack()
        ranks_long.name = 'funding_rank'
        fwd_long = fwd_sampled.stack()
        fwd_long.name = 'fwd_return'

        merged = pd.concat([ranks_long, fwd_long], axis=1).dropna()

        # Split
        train_mask = merged.index.get_level_values(0) < split_date
        train = merged[train_mask]
        test = merged[~train_mask]

        for period_name, data in [('train', train), ('test', test)]:
            if len(data) < 100:
                continue

            # Overall IC
            ic_val, _ = scipy_stats.spearmanr(data['funding_rank'], data['fwd_return'])

            # Decile analysis
            data = data.copy()
            data['decile'] = pd.qcut(data['funding_rank'], 10, labels=False, duplicates='drop')
            decile_returns = data.groupby('decile')['fwd_return'].mean()

            # Top vs bottom decile spread
            top_decile = data[data['decile'] == 9]['fwd_return'].mean()
            bot_decile = data[data['decile'] == 0]['fwd_return'].mean()
            spread = top_decile - bot_decile

            # Monotonicity (rank correlation of decile vs mean return)
            mono_ic, _ = scipy_stats.spearmanr(decile_returns.index, decile_returns.values)

            results.append({
                'horizon': horizon,
                'period': period_name,
                'IC': ic_val,
                'top_decile_ret': top_decile,
                'bot_decile_ret': bot_decile,
                'spread': spread,
                'monotonicity': mono_ic,
                'n_obs': len(data)
            })

            # Print decile table
            print(f"\n  Funding Rank Decile Returns ({horizon}, {period_name}, n={len(data):,}):")
            for d in sorted(decile_returns.index):
                n_d = (data['decile'] == d).sum()
                print(f"    Decile {d}: {decile_returns[d]*100:+.4f}% (n={n_d:,})")

    return results


# ── Crowding Analysis ──────────────────────────────────────────────────
def crowding_analysis(signals, market_fwd, split_date):
    """
    Test: when >80% of tokens have positive funding (crowded long),
    is it a sell signal?
    """
    print("\n" + "="*80)
    print("CROWDING ANALYSIS: Fraction Positive Funding vs Forward Market Returns")
    print("="*80)

    frac_pos = signals['frac_positive_funding_8h']

    for horizon, mkt_ret in market_fwd.items():
        merged = pd.concat([frac_pos, mkt_ret], axis=1).dropna()

        # Only test period
        test_data = merged[merged.index >= split_date]
        train_data = merged[merged.index < split_date]

        for period_name, data in [('TRAIN', train_data), ('TEST', test_data)]:
            if len(data) < 50:
                continue

            # Quintile analysis
            data = data.copy()
            data.columns = ['frac_pos', 'fwd_ret']
            data['quintile'] = pd.qcut(data['frac_pos'], 5, labels=False, duplicates='drop')

            print(f"\n  {period_name} | {horizon} forward return by frac_positive quintile (n={len(data):,}):")
            for q in sorted(data['quintile'].unique()):
                subset = data[data['quintile'] == q]
                frac_range = f"[{subset['frac_pos'].min():.2f}-{subset['frac_pos'].max():.2f}]"
                print(f"    Q{q} {frac_range}: {subset['fwd_ret'].mean()*100:+.4f}% "
                      f"(n={len(subset):,}, sharpe_proxy={subset['fwd_ret'].mean()/subset['fwd_ret'].std()*np.sqrt(24/FWD_HORIZONS[horizon]*365) if subset['fwd_ret'].std()>0 else 0:.2f})")

            # Threshold test: >80% positive
            crowded = data[data['frac_pos'] > 0.80]
            uncrowded = data[data['frac_pos'] <= 0.80]
            if len(crowded) > 10 and len(uncrowded) > 10:
                print(f"    Crowded (>80%): {crowded['fwd_ret'].mean()*100:+.4f}% (n={len(crowded):,})")
                print(f"    Uncrowded (<=80%): {uncrowded['fwd_ret'].mean()*100:+.4f}% (n={len(uncrowded):,})")
                t_val, p_val = scipy_stats.ttest_ind(crowded['fwd_ret'], uncrowded['fwd_ret'])
                print(f"    T-test: t={t_val:.3f}, p={p_val:.4f}")


# ── Volatility Prediction ─────────────────────────────────────────────
def volatility_prediction_analysis(signals, close_panel, split_date):
    """
    Test: does mean absolute funding predict future realized volatility?
    """
    print("\n" + "="*80)
    print("VOLATILITY PREDICTION: Mean Abs Funding vs Forward Realized Vol")
    print("="*80)

    hourly_returns = close_panel.pct_change(1)
    market_return = hourly_returns.mean(axis=1)

    # Realized vol over various horizons
    for hours_label, hours in [('1d', 24), ('3d', 72), ('7d', 168)]:
        fwd_vol = market_return.rolling(hours).std().shift(-hours) * np.sqrt(hours)
        fwd_vol.name = f'fwd_vol_{hours_label}'

        signal = signals['mean_abs_funding_8h']
        merged = pd.concat([signal, fwd_vol], axis=1).dropna()

        train = merged[merged.index < split_date]
        test = merged[merged.index >= split_date]

        for period_name, data in [('TRAIN', train), ('TEST', test)]:
            if len(data) < 50:
                continue
            ic_val, t_val, sign_val = information_coefficient(data.iloc[:, 0], data.iloc[:, 1])
            print(f"  {period_name} | mean_abs_funding_8h -> fwd_vol_{hours_label}: "
                  f"IC={ic_val:.4f}, t={t_val:.2f}, sign={sign_val:.2f}, n={len(data):,}")

    # Also test dispersion -> volatility
    print("\n  Dispersion -> Volatility:")
    for hours_label, hours in [('1d', 24), ('3d', 72), ('7d', 168)]:
        fwd_vol = market_return.rolling(hours).std().shift(-hours) * np.sqrt(hours)
        fwd_vol.name = f'fwd_vol_{hours_label}'

        signal = signals['funding_dispersion_8h']
        merged = pd.concat([signal, fwd_vol], axis=1).dropna()

        train = merged[merged.index < split_date]
        test = merged[merged.index >= split_date]

        for period_name, data in [('TRAIN', train), ('TEST', test)]:
            if len(data) < 50:
                continue
            ic_val, t_val, sign_val = information_coefficient(data.iloc[:, 0], data.iloc[:, 1])
            print(f"  {period_name} | funding_dispersion_8h -> fwd_vol_{hours_label}: "
                  f"IC={ic_val:.4f}, t={t_val:.2f}, sign={sign_val:.2f}, n={len(data):,}")


# ── Extreme Funding Rank Reversal ──────────────────────────────────────
def extreme_rank_reversal(funding_ranks, fwd_returns, split_date):
    """
    Test: do tokens in the top/bottom funding decile experience mean-reversion?
    """
    print("\n" + "="*80)
    print("EXTREME FUNDING RANK REVERSAL (Top/Bottom Decile)")
    print("="*80)

    for horizon, fwd in fwd_returns.items():
        # Sample at 8h frequency
        sample_idx = list(range(0, len(funding_ranks), 8))
        ranks_sampled = funding_ranks.iloc[sample_idx]
        fwd_sampled = fwd.iloc[sample_idx]

        ranks_long = ranks_sampled.stack()
        ranks_long.name = 'funding_rank'
        fwd_long = fwd_sampled.stack()
        fwd_long.name = 'fwd_return'

        merged = pd.concat([ranks_long, fwd_long], axis=1).dropna()

        train_mask = merged.index.get_level_values(0) < split_date
        test = merged[~train_mask]

        if len(test) < 100:
            continue

        # Long bottom decile (most negative funding), short top decile
        top_decile = test[test['funding_rank'] >= 0.9]
        bot_decile = test[test['funding_rank'] <= 0.1]

        if len(top_decile) < 20 or len(bot_decile) < 20:
            continue

        long_ret = bot_decile['fwd_return'].mean()
        short_ret = -top_decile['fwd_return'].mean()
        ls_ret = long_ret + short_ret

        long_std = bot_decile['fwd_return'].std()
        short_std = top_decile['fwd_return'].std()

        print(f"\n  {horizon} (TEST):")
        print(f"    Long bottom decile (negative funding): {long_ret*100:+.4f}% "
              f"(std={long_std*100:.4f}%, n={len(bot_decile):,})")
        print(f"    Short top decile (positive funding): {short_ret*100:+.4f}% "
              f"(std={short_std*100:.4f}%, n={len(top_decile):,})")
        print(f"    L/S spread: {ls_ret*100:+.4f}%")

        # T-test
        t_val, p_val = scipy_stats.ttest_ind(bot_decile['fwd_return'], top_decile['fwd_return'])
        print(f"    T-test (bot vs top): t={t_val:.3f}, p={p_val:.4f}")

        # Also test top 5% vs bottom 5%
        top5 = test[test['funding_rank'] >= 0.95]
        bot5 = test[test['funding_rank'] <= 0.05]
        if len(top5) >= 10 and len(bot5) >= 10:
            ls5 = bot5['fwd_return'].mean() - top5['fwd_return'].mean()
            print(f"    Extreme 5%: long_bot={bot5['fwd_return'].mean()*100:+.4f}%, "
                  f"short_top={-top5['fwd_return'].mean()*100:+.4f}%, "
                  f"L/S={ls5*100:+.4f}% (n_bot={len(bot5):,}, n_top={len(top5):,})")


# ── High Dispersion Alpha ─────────────────────────────────────────────
def high_dispersion_alpha(signals, funding_ranks, fwd_returns, split_date):
    """
    Test: does high funding dispersion create more alpha opportunity?
    Condition on high dispersion, then test rank signal strength.
    """
    print("\n" + "="*80)
    print("HIGH DISPERSION = MORE ALPHA OPPORTUNITY?")
    print("="*80)

    disp = signals['funding_dispersion_8h']
    disp_median = disp[disp.index < split_date].median()

    for horizon, fwd in fwd_returns.items():
        # Sample at 8h
        sample_idx = list(range(0, len(funding_ranks), 8))
        ranks_sampled = funding_ranks.iloc[sample_idx]
        fwd_sampled = fwd.iloc[sample_idx]
        disp_sampled = disp.reindex(funding_ranks.index).iloc[sample_idx]

        ranks_long = ranks_sampled.stack()
        ranks_long.name = 'funding_rank'
        fwd_long = fwd_sampled.stack()
        fwd_long.name = 'fwd_return'
        disp_long = disp_sampled.reindex(ranks_long.index.get_level_values(0))
        disp_long.index = ranks_long.index

        merged = pd.concat([ranks_long, fwd_long, disp_long], axis=1).dropna()
        merged.columns = ['funding_rank', 'fwd_return', 'dispersion']

        test = merged[merged.index.get_level_values(0) >= split_date]
        if len(test) < 100:
            continue

        # Split by dispersion
        high_disp = test[test['dispersion'] >= disp_median]
        low_disp = test[test['dispersion'] < disp_median]

        ic_high, _ = scipy_stats.spearmanr(high_disp['funding_rank'], high_disp['fwd_return'])
        ic_low, _ = scipy_stats.spearmanr(low_disp['funding_rank'], low_disp['fwd_return'])

        # L/S spread in each regime
        def ls_spread(data):
            top = data[data['funding_rank'] >= 0.9]['fwd_return'].mean()
            bot = data[data['funding_rank'] <= 0.1]['fwd_return'].mean()
            return bot - top

        ls_high = ls_spread(high_disp)
        ls_low = ls_spread(low_disp)

        print(f"\n  {horizon} (TEST, median_disp={disp_median:.6f}):")
        print(f"    HIGH dispersion: IC={ic_high:.4f}, L/S spread={ls_high*100:+.4f}% (n={len(high_disp):,})")
        print(f"    LOW dispersion:  IC={ic_low:.4f}, L/S spread={ls_low*100:+.4f}% (n={len(low_disp):,})")
        print(f"    Alpha enhancement: {abs(ls_high) - abs(ls_low):.4f} "
              f"({'HIGH disp better' if abs(ls_high) > abs(ls_low) else 'LOW disp better'})")


# ── Regime Conditioning ────────────────────────────────────────────────
def regime_analysis(signals, market_fwd, split_date):
    """
    Characterize market regimes by funding structure and test signal stability.
    """
    print("\n" + "="*80)
    print("REGIME ANALYSIS: Funding Structure Describes Market Regime")
    print("="*80)

    # Define regimes by funding dispersion and mean funding
    disp = signals['funding_dispersion_8h']
    mean_f = signals['mean_funding_8h']
    frac_pos = signals['frac_positive_funding_8h']

    # Use train period thresholds
    train_disp = disp[disp.index < split_date]
    train_mean = mean_f[mean_f.index < split_date]

    disp_q33 = train_disp.quantile(0.33)
    disp_q67 = train_disp.quantile(0.67)
    mean_q33 = train_mean.quantile(0.33)
    mean_q67 = train_mean.quantile(0.67)

    # 9 regimes: (low/mid/high dispersion) x (negative/neutral/positive mean funding)
    def classify(row_idx):
        d = disp.iloc[row_idx] if row_idx < len(disp) else np.nan
        m = mean_f.iloc[row_idx] if row_idx < len(mean_f) else np.nan
        if pd.isna(d) or pd.isna(m):
            return 'unknown'
        d_cat = 'low_disp' if d < disp_q33 else ('mid_disp' if d < disp_q67 else 'high_disp')
        m_cat = 'neg_fund' if m < mean_q33 else ('neut_fund' if m < mean_q67 else 'pos_fund')
        return f"{d_cat}_{m_cat}"

    regime_labels = pd.Series(
        [classify(i) for i in range(len(disp))],
        index=disp.index
    )

    # Market returns by regime
    for horizon, mkt_ret in market_fwd.items():
        merged = pd.concat([regime_labels, mkt_ret], axis=1).dropna()
        merged.columns = ['regime', 'fwd_ret']

        test = merged[merged.index >= split_date]
        if len(test) < 50:
            continue

        print(f"\n  {horizon} forward market return by regime (TEST):")
        regime_stats = test.groupby('regime')['fwd_ret'].agg(['mean', 'std', 'count'])
        regime_stats['sharpe_proxy'] = regime_stats['mean'] / regime_stats['std'] * np.sqrt(365 * 24 / FWD_HORIZONS[horizon])
        regime_stats = regime_stats.sort_values('mean', ascending=False)
        for regime, row in regime_stats.iterrows():
            if row['count'] >= 10:
                print(f"    {regime:30s}: {row['mean']*100:+.4f}% "
                      f"(std={row['std']*100:.4f}%, sharpe={row['sharpe_proxy']:.2f}, n={int(row['count']):,})")


# ── Summary Table ──────────────────────────────────────────────────────
def print_summary_table(all_results):
    """Print a comprehensive summary table of all signal evaluations."""
    df = pd.DataFrame(all_results)
    if df.empty:
        print("No results to display.")
        return

    print("\n" + "="*80)
    print("COMPREHENSIVE SIGNAL EVALUATION SUMMARY")
    print("="*80)

    # Format
    for col in ['IC', 't_stat', 'sign_consistency']:
        if col in df.columns:
            df[col] = df[col].apply(lambda x: f"{x:.4f}" if pd.notna(x) else "N/A")

    # Print by signal
    for signal_name in df['signal'].unique():
        subset = df[df['signal'] == signal_name]
        print(f"\n  Signal: {signal_name}")
        print(f"  {'target':<25s} {'period':<8s} {'IC':<10s} {'t_stat':<10s} {'sign_cons':<12s} {'n_obs':<8s}")
        print(f"  {'-'*73}")
        for _, row in subset.iterrows():
            print(f"  {row['target']:<25s} {row['period']:<8s} {row['IC']:<10s} "
                  f"{row.get('t_stat','N/A'):<10s} {row.get('sign_consistency','N/A'):<12s} "
                  f"{str(int(row['n_obs'])) if pd.notna(row.get('n_obs')) else 'N/A':<8s}")


# ── Main ───────────────────────────────────────────────────────────────
def main():
    print("="*80)
    print("FUNDING RATE DISPERSION & CROSS-SECTIONAL STRUCTURE ANALYSIS")
    print("="*80)
    print(f"Train period: < {TRAIN_END}")
    print(f"Test period:  >= {TRAIN_END}")
    print(f"Forward horizons: {FWD_HORIZONS}")

    # 1. Load data
    print("\n" + "-"*40 + " DATA LOADING " + "-"*40)
    token_data = load_all_tokens()

    # 2. Build panel
    print("\n" + "-"*40 + " PANEL CONSTRUCTION " + "-"*40)
    close_panel, funding_panel, volume_panel = build_panel(token_data)

    # 3. Compute signals
    print("\n" + "-"*40 + " SIGNAL COMPUTATION " + "-"*40)
    cs_signals = compute_cross_sectional_signals(funding_panel)
    funding_ranks = compute_funding_ranks(funding_panel)

    # Descriptive stats for signals
    print("\n  Cross-sectional signal descriptive stats (full period):")
    raw_cols = [c for c in cs_signals.columns if '_8h' not in c]
    desc = cs_signals[raw_cols].describe().T[['mean', 'std', 'min', '25%', '50%', '75%', 'max']]
    print(desc.to_string())

    # 4. Forward returns
    print("\n" + "-"*40 + " FORWARD RETURNS " + "-"*40)
    fwd_returns = compute_forward_returns(close_panel)
    market_fwd, market_hourly = compute_market_returns(close_panel)
    mom_spreads = compute_momentum_spread(close_panel)

    for h in FWD_HORIZONS:
        train_mkt = market_fwd[h][market_fwd[h].index < TRAIN_END].dropna()
        test_mkt = market_fwd[h][market_fwd[h].index >= TRAIN_END].dropna()
        if len(train_mkt) > 0 and len(test_mkt) > 0:
            print(f"  {h} market fwd return: train mean={train_mkt.mean()*100:.4f}%, "
                  f"test mean={test_mkt.mean()*100:.4f}%")

    # 5. Evaluate cross-sectional signals vs market
    print("\n" + "-"*40 + " SIGNAL vs MARKET RETURNS " + "-"*40)
    all_results = []

    signal_cols_8h = [c for c in cs_signals.columns if c.endswith('_8h')]
    for sig_col in signal_cols_8h:
        sig = cs_signals[sig_col]
        results = evaluate_signal_vs_market(sig, market_fwd, sig_col, TRAIN_END)
        all_results.extend(results)

    # 6. Evaluate vs momentum spread
    print("\n" + "-"*40 + " SIGNAL vs MOMENTUM SPREAD " + "-"*40)
    for sig_col in signal_cols_8h:
        sig = cs_signals[sig_col]
        results = evaluate_signal_vs_momentum_spread(sig, mom_spreads, sig_col, TRAIN_END)
        all_results.extend(results)

    # 7. Print summary
    print_summary_table(all_results)

    # 8. Token-level funding rank analysis
    print("\n" + "-"*40 + " FUNDING RANK vs TOKEN RETURNS " + "-"*40)
    rank_results = evaluate_funding_rank_signal(funding_ranks, fwd_returns, TRAIN_END)

    print("\n  Funding Rank Signal Summary:")
    rank_df = pd.DataFrame(rank_results)
    if not rank_df.empty:
        print(f"  {'horizon':<8s} {'period':<8s} {'IC':<10s} {'spread':<12s} {'mono':<10s} {'n_obs':<10s}")
        print(f"  {'-'*58}")
        for _, row in rank_df.iterrows():
            print(f"  {row['horizon']:<8s} {row['period']:<8s} {row['IC']:+.4f}    "
                  f"{row['spread']*100:+.4f}%    {row['monotonicity']:+.4f}    {row['n_obs']:,}")

    # 9. Crowding analysis
    crowding_analysis(cs_signals, market_fwd, TRAIN_END)

    # 10. Volatility prediction
    volatility_prediction_analysis(cs_signals, close_panel, TRAIN_END)

    # 11. Extreme rank reversal
    extreme_rank_reversal(funding_ranks, fwd_returns, TRAIN_END)

    # 12. High dispersion alpha
    high_dispersion_alpha(cs_signals, funding_ranks, fwd_returns, TRAIN_END)

    # 13. Regime analysis
    regime_analysis(cs_signals, market_fwd, TRAIN_END)

    # ── Final Verdict ──────────────────────────────────────────────────
    print("\n" + "="*80)
    print("FINAL VERDICT & KEY FINDINGS")
    print("="*80)

    # Collect the strongest test-period results
    test_results = [r for r in all_results if r['period'] == 'test']
    if test_results:
        test_df = pd.DataFrame(test_results)
        test_df['IC_abs'] = test_df['IC'].apply(lambda x: abs(float(x)) if x != 'N/A' else 0)
        top = test_df.nlargest(10, 'IC_abs')
        print("\n  TOP 10 SIGNALS BY |IC| (TEST PERIOD):")
        for _, row in top.iterrows():
            print(f"    {row['signal']:40s} -> {row['target']:<20s}: IC={row['IC']}")

    if not rank_df.empty:
        test_ranks = rank_df[rank_df['period'] == 'test']
        if not test_ranks.empty:
            print("\n  FUNDING RANK SIGNAL (TEST):")
            for _, row in test_ranks.iterrows():
                verdict = "SIGNIFICANT" if abs(row['IC']) > 0.02 else "WEAK"
                print(f"    {row['horizon']}: IC={row['IC']:+.4f}, spread={row['spread']*100:+.4f}%, "
                      f"monotonicity={row['monotonicity']:+.4f} [{verdict}]")

    print("\n  INTERPRETATION GUIDE:")
    print("    |IC| > 0.05: Strong signal")
    print("    |IC| 0.02-0.05: Moderate signal, potentially tradeable with proper sizing")
    print("    |IC| < 0.02: Weak/noise")
    print("    t-stat > 2.0: Statistically significant")
    print("    Sign consistency > 0.6: Directionally reliable")
    print()


if __name__ == '__main__':
    main()
