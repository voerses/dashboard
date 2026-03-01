#!/usr/bin/env python3
"""
research_post_etf.py — Comprehensive Post-ETF Regime Analysis
=============================================================
Analyzes what changed in crypto markets after the BTC spot ETF approval (Jan 10, 2024).

Covers:
  1. Correlation structure changes
  2. Volatility regime shifts
  3. Momentum decay
  4. Mean reversion effectiveness
  5. Volume profile changes
  6. Auto-correlation structure changes
  7. Lead-lag relationships (BTC vs alts)
  8. Transfer Entropy (BTC -> alts, alts -> BTC)
  9. VPIN (Volume-Synchronized Probability of Informed Trading)
 10. Information Coefficients for 15+ signals across pre/post ETF

Uses numpy/pandas only. Transfer entropy via histogram-based estimation.
"""

import sys
import os
import warnings
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
warnings.filterwarnings('ignore')

import numpy as np
import pandas as pd

# ============================================================
# CONFIG
# ============================================================
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'real_data')
ETF_DATE = pd.Timestamp('2024-01-10')

# Tokens with enough history (pre-ETF data required)
# Top 20 alts by market cap that have data starting before 2023
TARGET_ALTS = [
    'ETH', 'BNB', 'SOL', 'XRP', 'ADA', 'DOGE', 'AVAX', 'DOT', 'LINK',
    'UNI', 'ATOM', 'LTC', 'BCH', 'FIL', 'NEAR', 'AAVE', 'SNX', 'CRV',
    'ALGO', 'FTM'
]
ALL_TOKENS = ['BTC'] + TARGET_ALTS

# Pre-ETF window: 2 years before ETF (Jan 2022 - Jan 2024)
PRE_START = pd.Timestamp('2022-01-10')
PRE_END = ETF_DATE
# Post-ETF window: ETF date to latest data
POST_START = ETF_DATE

# Forward return horizons for IC analysis
FWD_HORIZONS = [1, 5, 20]

# ============================================================
# DATA LOADING
# ============================================================

def load_all_data():
    """Load OHLCV data for all target tokens."""
    data = {}
    for token in ALL_TOKENS:
        path = os.path.join(DATA_DIR, f'{token}_daily.csv')
        if os.path.exists(path):
            df = pd.read_csv(path, index_col=0, parse_dates=True)
            df.columns = [c.lower().strip() for c in df.columns]
            # Ensure sorted
            df = df.sort_index()
            # Drop any duplicate dates
            df = df[~df.index.duplicated(keep='first')]
            data[token] = df
        else:
            print(f"  WARNING: {token} data not found at {path}")
    return data


def build_returns_panel(data):
    """Build aligned close price and return DataFrames."""
    close_dict = {}
    volume_dict = {}
    high_dict = {}
    low_dict = {}
    open_dict = {}
    for token, df in data.items():
        close_dict[token] = df['close']
        volume_dict[token] = df['volume']
        high_dict[token] = df['high']
        low_dict[token] = df['low']
        open_dict[token] = df['open']

    close = pd.DataFrame(close_dict)
    volume = pd.DataFrame(volume_dict)
    high = pd.DataFrame(high_dict)
    low = pd.DataFrame(low_dict)
    opn = pd.DataFrame(open_dict)
    returns = close.pct_change()
    log_returns = np.log(close / close.shift(1))
    return close, volume, high, low, opn, returns, log_returns


# ============================================================
# SECTION 1: CORRELATION STRUCTURE
# ============================================================

def analyze_correlations(returns):
    """Compare correlation matrices pre vs post ETF."""
    print("\n" + "=" * 80)
    print("SECTION 1: CORRELATION STRUCTURE CHANGES")
    print("=" * 80)

    pre = returns.loc[PRE_START:PRE_END].dropna(how='all', axis=1)
    post = returns.loc[POST_START:].dropna(how='all', axis=1)
    common = sorted(set(pre.columns) & set(post.columns))
    pre = pre[common].dropna()
    post = post[common].dropna()

    corr_pre = pre.corr()
    corr_post = post.corr()

    # Average pairwise correlation (excluding diagonal)
    n = len(common)
    mask = np.ones((n, n), dtype=bool)
    np.fill_diagonal(mask, False)

    avg_corr_pre = corr_pre.values[mask].mean()
    avg_corr_post = corr_post.values[mask].mean()
    median_corr_pre = np.median(corr_pre.values[mask])
    median_corr_post = np.median(corr_post.values[mask])

    print(f"\n  Tokens analyzed: {n}")
    print(f"  Pre-ETF period:  {PRE_START.date()} to {PRE_END.date()} ({len(pre)} days)")
    print(f"  Post-ETF period: {POST_START.date()} to {post.index[-1].date()} ({len(post)} days)")
    print(f"\n  Average pairwise correlation:")
    print(f"    Pre-ETF:  {avg_corr_pre:.4f}")
    print(f"    Post-ETF: {avg_corr_post:.4f}")
    print(f"    Change:   {avg_corr_post - avg_corr_pre:+.4f}")
    print(f"\n  Median pairwise correlation:")
    print(f"    Pre-ETF:  {median_corr_pre:.4f}")
    print(f"    Post-ETF: {median_corr_post:.4f}")
    print(f"    Change:   {median_corr_post - median_corr_pre:+.4f}")

    # BTC correlation with each alt
    print(f"\n  BTC correlation with alts:")
    print(f"  {'Token':<10} {'Pre-ETF':>10} {'Post-ETF':>10} {'Change':>10}")
    print(f"  {'-'*40}")
    btc_corr_changes = {}
    for token in common:
        if token == 'BTC':
            continue
        c_pre = corr_pre.loc['BTC', token]
        c_post = corr_post.loc['BTC', token]
        btc_corr_changes[token] = c_post - c_pre
        print(f"  {token:<10} {c_pre:>10.4f} {c_post:>10.4f} {c_post - c_pre:>+10.4f}")

    avg_btc_change = np.mean(list(btc_corr_changes.values()))
    print(f"\n  Avg BTC-alt correlation change: {avg_btc_change:+.4f}")
    if avg_corr_post > avg_corr_pre + 0.03:
        print("  --> FINDING: Market is MORE correlated post-ETF (herd behavior / macro dominance)")
    elif avg_corr_post < avg_corr_pre - 0.03:
        print("  --> FINDING: Market is LESS correlated post-ETF (dispersion / alpha opportunity)")
    else:
        print("  --> FINDING: Correlation structure roughly stable")

    # Rolling correlation (BTC-ETH as proxy)
    if 'ETH' in common:
        rolling_corr = returns['BTC'].rolling(60).corr(returns['ETH'])
        rc_pre = rolling_corr.loc[PRE_START:PRE_END].dropna()
        rc_post = rolling_corr.loc[POST_START:].dropna()
        print(f"\n  BTC-ETH 60d rolling correlation:")
        print(f"    Pre-ETF:  mean={rc_pre.mean():.4f}, std={rc_pre.std():.4f}")
        print(f"    Post-ETF: mean={rc_post.mean():.4f}, std={rc_post.std():.4f}")

    return corr_pre, corr_post


# ============================================================
# SECTION 2: VOLATILITY REGIME
# ============================================================

def analyze_volatility(returns, close):
    """Compare volatility regimes pre vs post ETF."""
    print("\n" + "=" * 80)
    print("SECTION 2: VOLATILITY REGIME SHIFTS")
    print("=" * 80)

    print(f"\n  Annualized volatility (realized, 20d rolling):")
    print(f"  {'Token':<10} {'Pre-ETF':>10} {'Post-ETF':>10} {'Change':>10} {'Ratio':>10}")
    print(f"  {'-'*50}")

    vol_changes = {}
    for token in ALL_TOKENS:
        if token not in returns.columns:
            continue
        r = returns[token]
        rvol = r.rolling(20).std() * np.sqrt(365)

        rvol_pre = rvol.loc[PRE_START:PRE_END].dropna()
        rvol_post = rvol.loc[POST_START:].dropna()
        if len(rvol_pre) < 20 or len(rvol_post) < 20:
            continue

        v_pre = rvol_pre.mean()
        v_post = rvol_post.mean()
        vol_changes[token] = (v_pre, v_post)
        ratio = v_post / v_pre if v_pre > 0 else np.nan
        print(f"  {token:<10} {v_pre:>10.1%} {v_post:>10.1%} {v_post - v_pre:>+10.1%} {ratio:>10.2f}x")

    # Vol-of-vol
    print(f"\n  Volatility-of-Volatility (20d rolling vol of 20d vol):")
    print(f"  {'Token':<10} {'Pre-ETF':>10} {'Post-ETF':>10} {'Change':>10}")
    print(f"  {'-'*40}")
    for token in ['BTC', 'ETH', 'SOL', 'XRP', 'LINK']:
        if token not in returns.columns:
            continue
        r = returns[token]
        rvol = r.rolling(20).std() * np.sqrt(365)
        vov = rvol.rolling(20).std()
        vov_pre = vov.loc[PRE_START:PRE_END].dropna().mean()
        vov_post = vov.loc[POST_START:].dropna().mean()
        print(f"  {token:<10} {vov_pre:>10.4f} {vov_post:>10.4f} {vov_post - vov_pre:>+10.4f}")

    # Summary
    pre_vols = [v[0] for v in vol_changes.values()]
    post_vols = [v[1] for v in vol_changes.values()]
    avg_ratio = np.mean(post_vols) / np.mean(pre_vols) if np.mean(pre_vols) > 0 else np.nan
    print(f"\n  Average vol ratio (post/pre): {avg_ratio:.2f}x")
    if avg_ratio < 0.85:
        print("  --> FINDING: Significant vol COMPRESSION post-ETF")
    elif avg_ratio > 1.15:
        print("  --> FINDING: Significant vol EXPANSION post-ETF")
    else:
        print("  --> FINDING: Volatility roughly stable post-ETF")


# ============================================================
# SECTION 3: MOMENTUM DECAY
# ============================================================

def analyze_momentum(returns):
    """Test whether momentum signals have decayed post-ETF."""
    print("\n" + "=" * 80)
    print("SECTION 3: MOMENTUM PERSISTENCE / DECAY")
    print("=" * 80)

    lookbacks = [5, 10, 20, 60]
    print(f"\n  Auto-correlation of returns (lag-1) as momentum proxy:")
    print(f"  {'Token':<10} {'Pre-ETF':>10} {'Post-ETF':>10} {'Change':>10}")
    print(f"  {'-'*40}")
    for token in ['BTC', 'ETH', 'SOL', 'XRP', 'LINK', 'AVAX', 'DOT', 'ADA']:
        if token not in returns.columns:
            continue
        r = returns[token].dropna()
        r_pre = r.loc[PRE_START:PRE_END]
        r_post = r.loc[POST_START:]
        if len(r_pre) < 60 or len(r_post) < 60:
            continue
        ac_pre = r_pre.autocorr(lag=1)
        ac_post = r_post.autocorr(lag=1)
        print(f"  {token:<10} {ac_pre:>10.4f} {ac_post:>10.4f} {ac_post - ac_pre:>+10.4f}")

    # Momentum returns: sort tokens by past N-day return, long top quintile, short bottom
    print(f"\n  Cross-sectional momentum returns (long top / short bottom quintile):")
    print(f"  {'Lookback':<12} {'Pre-ETF (ann)':>14} {'Post-ETF (ann)':>15} {'Change':>10}")
    print(f"  {'-'*51}")

    common_tokens = [t for t in ALL_TOKENS if t in returns.columns]
    ret = returns[common_tokens].loc[PRE_START:]
    ret = ret.dropna(axis=1, thresh=int(len(ret) * 0.7))
    common_tokens = list(ret.columns)

    for lb in lookbacks:
        past_ret = ret.rolling(lb).sum().shift(1)
        fwd_ret = ret.shift(-1)  # next day return

        # Pre-ETF
        pre_past = past_ret.loc[PRE_START:PRE_END].dropna(how='all')
        pre_fwd = fwd_ret.loc[PRE_START:PRE_END].dropna(how='all')
        common_idx = pre_past.index.intersection(pre_fwd.index)
        pre_past = pre_past.loc[common_idx]
        pre_fwd = pre_fwd.loc[common_idx]

        # Post-ETF
        post_past = past_ret.loc[POST_START:].dropna(how='all')
        post_fwd = fwd_ret.loc[POST_START:].dropna(how='all')
        common_idx2 = post_past.index.intersection(post_fwd.index)
        post_past = post_past.loc[common_idx2]
        post_fwd = post_fwd.loc[common_idx2]

        def xsec_mom_ret(past, fwd):
            """Compute daily long-short return from cross-sectional momentum."""
            daily_ls = []
            for dt in past.index:
                row_past = past.loc[dt].dropna()
                row_fwd = fwd.loc[dt].dropna()
                common_t = row_past.index.intersection(row_fwd.index)
                if len(common_t) < 5:
                    continue
                rp = row_past[common_t]
                rf = row_fwd[common_t]
                q = max(1, len(common_t) // 5)
                sorted_idx = rp.sort_values().index
                short_tokens = sorted_idx[:q]
                long_tokens = sorted_idx[-q:]
                ls_ret = rf[long_tokens].mean() - rf[short_tokens].mean()
                daily_ls.append(ls_ret)
            return np.array(daily_ls)

        ls_pre = xsec_mom_ret(pre_past, pre_fwd)
        ls_post = xsec_mom_ret(post_past, post_fwd)
        ann_pre = np.mean(ls_pre) * 365 if len(ls_pre) > 0 else np.nan
        ann_post = np.mean(ls_post) * 365 if len(ls_post) > 0 else np.nan
        change = ann_post - ann_pre if not (np.isnan(ann_pre) or np.isnan(ann_post)) else np.nan
        print(f"  {lb}d{'':<8} {ann_pre:>14.1%} {ann_post:>15.1%} {change:>+10.1%}")

    print("\n  Interpretation:")
    print("    Positive = momentum profits, Negative = momentum reversal")
    print("    Decline in post-ETF = momentum decay (institutional arb)")


# ============================================================
# SECTION 4: MEAN REVERSION
# ============================================================

def analyze_mean_reversion(returns, close):
    """Test Z-score mean reversion effectiveness pre vs post ETF."""
    print("\n" + "=" * 80)
    print("SECTION 4: MEAN REVERSION EFFECTIVENESS")
    print("=" * 80)

    windows = [20, 50]
    for window in windows:
        print(f"\n  Z-score ({window}d) mean reversion — next-day return by Z-bucket:")
        print(f"  {'Z-bucket':<15} {'Pre-ETF':>10} {'Post-ETF':>10} {'Diff':>10}")
        print(f"  {'-'*45}")

        z_pre_all = []
        r_pre_all = []
        z_post_all = []
        r_post_all = []

        for token in ALL_TOKENS:
            if token not in close.columns:
                continue
            c = close[token]
            mu = c.rolling(window).mean()
            sigma = c.rolling(window).std()
            z = (c - mu) / sigma
            fwd = returns[token].shift(-1)

            mask = z.notna() & fwd.notna()
            pre_mask = mask & (z.index >= PRE_START) & (z.index <= PRE_END)
            post_mask = mask & (z.index >= POST_START)

            z_pre_all.extend(z[pre_mask].values)
            r_pre_all.extend(fwd[pre_mask].values)
            z_post_all.extend(z[post_mask].values)
            r_post_all.extend(fwd[post_mask].values)

        z_pre_all = np.array(z_pre_all)
        r_pre_all = np.array(r_pre_all)
        z_post_all = np.array(z_post_all)
        r_post_all = np.array(r_post_all)

        buckets = [(-np.inf, -2), (-2, -1), (-1, 0), (0, 1), (1, 2), (2, np.inf)]
        bucket_labels = ['Z < -2', '-2 < Z < -1', '-1 < Z < 0', '0 < Z < 1', '1 < Z < 2', 'Z > 2']

        for (lo, hi), label in zip(buckets, bucket_labels):
            pre_mask = (z_pre_all >= lo) & (z_pre_all < hi)
            post_mask = (z_post_all >= lo) & (z_post_all < hi)
            r_pre = r_pre_all[pre_mask].mean() * 100 if pre_mask.sum() > 10 else np.nan
            r_post = r_post_all[post_mask].mean() * 100 if post_mask.sum() > 10 else np.nan
            diff = r_post - r_pre if not (np.isnan(r_pre) or np.isnan(r_post)) else np.nan
            print(f"  {label:<15} {r_pre:>9.3f}% {r_post:>9.3f}% {diff:>+9.3f}%")

        # MR signal: short when Z > 1, long when Z < -1
        pre_short = r_pre_all[z_pre_all > 1]
        pre_long = r_pre_all[z_pre_all < -1]
        post_short = r_post_all[z_post_all > 1]
        post_long = r_post_all[z_post_all < -1]

        mr_pre = np.mean(pre_long) - np.mean(pre_short) if len(pre_long) > 10 and len(pre_short) > 10 else np.nan
        mr_post = np.mean(post_long) - np.mean(post_short) if len(post_long) > 10 and len(post_short) > 10 else np.nan
        print(f"\n  MR edge (long Z<-1, short Z>1): Pre={mr_pre*100:.3f}%/day, Post={mr_post*100:.3f}%/day")


# ============================================================
# SECTION 5: VOLUME PROFILE
# ============================================================

def analyze_volume(volume, returns):
    """Compare volume profiles pre vs post ETF."""
    print("\n" + "=" * 80)
    print("SECTION 5: VOLUME PROFILE CHANGES")
    print("=" * 80)

    print(f"\n  Average daily volume (USD-normalized to BTC):")
    print(f"  {'Token':<10} {'Pre-ETF':>15} {'Post-ETF':>15} {'Ratio':>10}")
    print(f"  {'-'*50}")

    for token in ALL_TOKENS[:10]:
        if token not in volume.columns:
            continue
        v = volume[token]
        v_pre = v.loc[PRE_START:PRE_END].dropna().mean()
        v_post = v.loc[POST_START:].dropna().mean()
        ratio = v_post / v_pre if v_pre > 0 else np.nan
        print(f"  {token:<10} {v_pre:>15,.0f} {v_post:>15,.0f} {ratio:>10.2f}x")

    # Volume-return relationship
    print(f"\n  Volume-return correlation (|return| vs volume):")
    print(f"  {'Token':<10} {'Pre-ETF':>10} {'Post-ETF':>10}")
    print(f"  {'-'*30}")
    for token in ['BTC', 'ETH', 'SOL', 'XRP', 'LINK']:
        if token not in returns.columns or token not in volume.columns:
            continue
        abs_r = returns[token].abs()
        v = volume[token]
        combined = pd.concat([abs_r, v], axis=1).dropna()
        combined.columns = ['abs_ret', 'vol']
        pre = combined.loc[PRE_START:PRE_END]
        post = combined.loc[POST_START:]
        if len(pre) > 20 and len(post) > 20:
            c_pre = pre['abs_ret'].corr(pre['vol'])
            c_post = post['abs_ret'].corr(post['vol'])
            print(f"  {token:<10} {c_pre:>10.4f} {c_post:>10.4f}")


# ============================================================
# SECTION 6: AUTO-CORRELATION STRUCTURE
# ============================================================

def analyze_autocorrelation(returns):
    """Analyze auto-correlation at multiple lags."""
    print("\n" + "=" * 80)
    print("SECTION 6: AUTO-CORRELATION STRUCTURE")
    print("=" * 80)

    lags = [1, 2, 3, 5, 10, 20]
    for token in ['BTC', 'ETH', 'SOL']:
        if token not in returns.columns:
            continue
        r = returns[token].dropna()
        r_pre = r.loc[PRE_START:PRE_END]
        r_post = r.loc[POST_START:]

        print(f"\n  {token} auto-correlation by lag:")
        print(f"  {'Lag':<8} {'Pre-ETF':>10} {'Post-ETF':>10} {'Change':>10}")
        print(f"  {'-'*38}")
        for lag in lags:
            ac_pre = r_pre.autocorr(lag=lag) if len(r_pre) > lag + 10 else np.nan
            ac_post = r_post.autocorr(lag=lag) if len(r_post) > lag + 10 else np.nan
            change = ac_post - ac_pre if not (np.isnan(ac_pre) or np.isnan(ac_post)) else np.nan
            print(f"  {lag:<8} {ac_pre:>10.4f} {ac_post:>10.4f} {change:>+10.4f}")

    # Abs return autocorrelation (volatility clustering)
    print(f"\n  Absolute return autocorrelation (volatility clustering):")
    print(f"  {'Token':<10} {'Lag':>5} {'Pre-ETF':>10} {'Post-ETF':>10}")
    print(f"  {'-'*35}")
    for token in ['BTC', 'ETH', 'SOL', 'XRP', 'LINK']:
        if token not in returns.columns:
            continue
        abs_r = returns[token].abs().dropna()
        pre = abs_r.loc[PRE_START:PRE_END]
        post = abs_r.loc[POST_START:]
        for lag in [1, 5]:
            ac_pre = pre.autocorr(lag=lag) if len(pre) > lag + 10 else np.nan
            ac_post = post.autocorr(lag=lag) if len(post) > lag + 10 else np.nan
            print(f"  {token:<10} {lag:>5} {ac_pre:>10.4f} {ac_post:>10.4f}")


# ============================================================
# SECTION 7: LEAD-LAG RELATIONSHIPS
# ============================================================

def analyze_lead_lag(returns):
    """Does BTC lead alts more or less post-ETF?"""
    print("\n" + "=" * 80)
    print("SECTION 7: LEAD-LAG RELATIONSHIPS (BTC -> ALTS)")
    print("=" * 80)

    if 'BTC' not in returns.columns:
        print("  BTC data not available")
        return

    btc = returns['BTC']
    print(f"\n  Correlation of BTC(t) with ALT(t+1) — BTC leading alts by 1 day:")
    print(f"  {'Token':<10} {'Pre-ETF':>10} {'Post-ETF':>10} {'Change':>10}")
    print(f"  {'-'*40}")

    lead_lag_changes = []
    for token in TARGET_ALTS:
        if token not in returns.columns:
            continue
        alt = returns[token]
        # BTC today vs alt tomorrow
        btc_today = btc.shift(1)  # shift so alignment gives btc(t) vs alt(t+1)
        combined = pd.concat([btc_today.rename('btc_lag'), alt.rename('alt')], axis=1).dropna()

        pre = combined.loc[PRE_START:PRE_END]
        post = combined.loc[POST_START:]

        if len(pre) > 30 and len(post) > 30:
            c_pre = pre['btc_lag'].corr(pre['alt'])
            c_post = post['btc_lag'].corr(post['alt'])
            lead_lag_changes.append(c_post - c_pre)
            print(f"  {token:<10} {c_pre:>10.4f} {c_post:>10.4f} {c_post - c_pre:>+10.4f}")

    if lead_lag_changes:
        avg_change = np.mean(lead_lag_changes)
        print(f"\n  Average lead-lag change: {avg_change:+.4f}")
        if avg_change > 0.02:
            print("  --> FINDING: BTC leads alts MORE post-ETF (information flows from BTC ETF)")
        elif avg_change < -0.02:
            print("  --> FINDING: BTC leads alts LESS post-ETF (market more efficient)")
        else:
            print("  --> FINDING: Lead-lag relationship roughly stable")


# ============================================================
# SECTION 8: TRANSFER ENTROPY
# ============================================================

def compute_transfer_entropy(source, target, bins=8, lag=1):
    """
    Compute Transfer Entropy from source to target using histogram-based estimation.
    TE(X->Y) = H(Y_t | Y_{t-1}) - H(Y_t | Y_{t-1}, X_{t-1})

    Using discretized returns into `bins` uniform-width bins.
    """
    # Align and create lagged series
    s = source.dropna()
    t = target.dropna()
    common = s.index.intersection(t.index)
    if len(common) < 100:
        return np.nan
    s = s.loc[common].values
    t = t.loc[common].values

    # Discretize into bins
    def discretize(x, n_bins):
        # Use percentile-based binning for robustness
        percentiles = np.linspace(0, 100, n_bins + 1)
        edges = np.percentile(x, percentiles)
        edges[0] = -np.inf
        edges[-1] = np.inf
        return np.digitize(x, edges[1:])

    s_d = discretize(s, bins)
    t_d = discretize(t, bins)

    # Create lagged arrays: Y_t, Y_{t-lag}, X_{t-lag}
    n = len(t_d)
    if n <= lag:
        return np.nan

    y_t = t_d[lag:]
    y_past = t_d[:-lag]
    x_past = s_d[:-lag]

    # Joint distributions via histogram counting
    def joint_prob_2d(a, b, n_bins):
        """P(a, b)"""
        counts = np.zeros((n_bins, n_bins))
        for i in range(len(a)):
            counts[a[i] % n_bins, b[i] % n_bins] += 1
        return counts / counts.sum()

    def joint_prob_3d(a, b, c, n_bins):
        """P(a, b, c)"""
        counts = np.zeros((n_bins, n_bins, n_bins))
        for i in range(len(a)):
            counts[a[i] % n_bins, b[i] % n_bins, c[i] % n_bins] += 1
        return counts / counts.sum()

    eps = 1e-12

    # P(Y_t, Y_past, X_past)
    p_yyx = joint_prob_3d(y_t, y_past, x_past, bins)
    # P(Y_past, X_past)
    p_yx = joint_prob_2d(y_past, x_past, bins)
    # P(Y_t, Y_past)
    p_yy = joint_prob_2d(y_t, y_past, bins)
    # P(Y_past) marginal
    p_y_past = np.zeros(bins)
    for i in range(len(y_past)):
        p_y_past[y_past[i] % bins] += 1
    p_y_past = p_y_past / p_y_past.sum()

    # TE = sum P(y_t, y_past, x_past) * log[ P(y_t | y_past, x_past) / P(y_t | y_past) ]
    # = sum P(y_t, y_past, x_past) * log[ P(y_t, y_past, x_past) * P(y_past) / (P(y_past, x_past) * P(y_t, y_past)) ]
    te = 0.0
    for i in range(bins):
        for j in range(bins):
            for k in range(bins):
                p3 = p_yyx[i, j, k]
                if p3 < eps:
                    continue
                p2_yx = p_yx[j, k]
                p2_yy = p_yy[i, j]
                p1_y = p_y_past[j]
                if p2_yx < eps or p2_yy < eps or p1_y < eps:
                    continue
                te += p3 * np.log2(p3 * p1_y / (p2_yx * p2_yy))

    return te


def analyze_transfer_entropy(returns):
    """Compute Transfer Entropy BTC <-> alts pre vs post ETF."""
    print("\n" + "=" * 80)
    print("SECTION 8: TRANSFER ENTROPY (BTC <-> ALTS)")
    print("=" * 80)
    print("  TE(X->Y) measures information flow from X to Y beyond Y's own history")
    print("  Higher TE = more predictive power from source to target\n")

    if 'BTC' not in returns.columns:
        print("  BTC data not available")
        return

    btc_pre = returns['BTC'].loc[PRE_START:PRE_END].dropna()
    btc_post = returns['BTC'].loc[POST_START:].dropna()

    print(f"  {'Token':<10} {'TE(BTC->ALT)':>12} {'TE(ALT->BTC)':>12} {'TE(BTC->ALT)':>12} {'TE(ALT->BTC)':>12} {'Net Flow':>10}")
    print(f"  {'':10} {'Pre-ETF':>12} {'Pre-ETF':>12} {'Post-ETF':>12} {'Post-ETF':>12} {'Change':>10}")
    print(f"  {'-'*70}")

    te_results = {}
    for token in TARGET_ALTS:
        if token not in returns.columns:
            continue
        alt_pre = returns[token].loc[PRE_START:PRE_END].dropna()
        alt_post = returns[token].loc[POST_START:].dropna()

        if len(alt_pre) < 100 or len(alt_post) < 100:
            continue

        te_btc_alt_pre = compute_transfer_entropy(btc_pre, alt_pre, bins=6)
        te_alt_btc_pre = compute_transfer_entropy(alt_pre, btc_pre, bins=6)
        te_btc_alt_post = compute_transfer_entropy(btc_post, alt_post, bins=6)
        te_alt_btc_post = compute_transfer_entropy(alt_post, btc_post, bins=6)

        # Net flow: positive = BTC dominates info flow to alt
        net_pre = te_btc_alt_pre - te_alt_btc_pre if not (np.isnan(te_btc_alt_pre) or np.isnan(te_alt_btc_pre)) else np.nan
        net_post = te_btc_alt_post - te_alt_btc_post if not (np.isnan(te_btc_alt_post) or np.isnan(te_alt_btc_post)) else np.nan
        net_change = net_post - net_pre if not (np.isnan(net_pre) or np.isnan(net_post)) else np.nan

        te_results[token] = {
            'btc_alt_pre': te_btc_alt_pre, 'alt_btc_pre': te_alt_btc_pre,
            'btc_alt_post': te_btc_alt_post, 'alt_btc_post': te_alt_btc_post,
            'net_pre': net_pre, 'net_post': net_post, 'net_change': net_change
        }

        print(f"  {token:<10} {te_btc_alt_pre:>12.4f} {te_alt_btc_pre:>12.4f} "
              f"{te_btc_alt_post:>12.4f} {te_alt_btc_post:>12.4f} {net_change:>+10.4f}")

    # Summary
    if te_results:
        avg_btc_alt_pre = np.nanmean([v['btc_alt_pre'] for v in te_results.values()])
        avg_btc_alt_post = np.nanmean([v['btc_alt_post'] for v in te_results.values()])
        avg_net_change = np.nanmean([v['net_change'] for v in te_results.values()])

        print(f"\n  Average TE(BTC->ALT): Pre={avg_btc_alt_pre:.4f}, Post={avg_btc_alt_post:.4f}")
        print(f"  Average net flow change: {avg_net_change:+.4f}")
        if avg_net_change > 0.005:
            print("  --> FINDING: BTC information dominance INCREASED post-ETF")
        elif avg_net_change < -0.005:
            print("  --> FINDING: BTC information dominance DECREASED post-ETF")
        else:
            print("  --> FINDING: Information flow structure roughly stable")

    return te_results


# ============================================================
# SECTION 9: VPIN
# ============================================================

def compute_vpin(close_s, volume_s, n_buckets=50):
    """
    Compute VPIN (Volume-Synchronized Probability of Informed Trading).

    Uses the Bulk Volume Classification (BVC) method:
    - Classify each bar's volume as buy/sell using close-to-close return
    - Aggregate into volume buckets (each bucket = avg daily volume)
    - VPIN = mean(|buy_volume - sell_volume| / total_volume) over last n_buckets
    """
    c = close_s.dropna()
    v = volume_s.dropna()
    common = c.index.intersection(v.index)
    c = c.loc[common]
    v = v.loc[common]

    if len(c) < 200:
        return pd.Series(dtype=float)

    # Classify volume using BVC (bulk volume classification)
    ret = c.pct_change()
    sigma = ret.rolling(20).std()
    z = ret / sigma.replace(0, np.nan)
    z = z.fillna(0).clip(-5, 5)
    # Approximate CDF: 0.5 * (1 + tanh(z * 0.7978845608))
    buy_frac = 0.5 * (1 + np.tanh(z.values * 0.7978845608))

    v_vals = v.values
    buy_vol = v_vals * buy_frac
    sell_vol = v_vals * (1 - buy_frac)

    # Bucket size = average daily volume (so ~1 bucket per day on average)
    bucket_size = np.nanmean(v_vals)
    if bucket_size <= 0 or np.isnan(bucket_size):
        return pd.Series(dtype=float)

    dates = c.index
    vpin_values = []
    vpin_dates = []
    cum_buy = 0.0
    cum_sell = 0.0
    cum_vol = 0.0
    bucket_vpins = []

    for i in range(len(c)):
        b = buy_vol[i]
        s = sell_vol[i]
        vol = v_vals[i]
        if np.isnan(b) or np.isnan(s) or np.isnan(vol) or vol <= 0:
            continue

        cum_buy += b
        cum_sell += s
        cum_vol += vol

        while cum_vol >= bucket_size and bucket_size > 0:
            total = cum_buy + cum_sell
            bucket_imbalance = abs(cum_buy - cum_sell) / total if total > 0 else 0
            bucket_vpins.append(bucket_imbalance)

            if len(bucket_vpins) >= n_buckets:
                vpin_val = np.mean(bucket_vpins[-n_buckets:])
                vpin_values.append(vpin_val)
                vpin_dates.append(dates[i])

            overflow = cum_vol - bucket_size
            ratio = overflow / cum_vol if cum_vol > 0 else 0
            cum_buy *= ratio
            cum_sell *= ratio
            cum_vol = overflow

    if not vpin_values:
        return pd.Series(dtype=float)

    # Deduplicate dates (keep last per day)
    result = pd.Series(vpin_values, index=vpin_dates)
    result = result[~result.index.duplicated(keep='last')]
    return result


def analyze_vpin(close, volume, returns):
    """Compute and compare VPIN pre vs post ETF."""
    print("\n" + "=" * 80)
    print("SECTION 9: VPIN (Volume-Synchronized Prob. of Informed Trading)")
    print("=" * 80)
    print("  VPIN measures order flow toxicity / informed trading probability")
    print("  Higher VPIN = more informed trading = potential adverse selection\n")

    print(f"  {'Token':<10} {'VPIN Pre':>10} {'VPIN Post':>10} {'Change':>10} {'VPIN-Ret Corr Pre':>18} {'VPIN-Ret Corr Post':>18}")
    print(f"  {'-'*76}")

    vpin_data = {}
    for token in ALL_TOKENS[:15]:  # Top 15
        if token not in close.columns or token not in volume.columns:
            continue

        vpin_series = compute_vpin(close[token], volume[token])
        if len(vpin_series) < 50:
            continue

        vpin_pre = vpin_series.loc[PRE_START:PRE_END]
        vpin_post = vpin_series.loc[POST_START:]

        if len(vpin_pre) < 10 or len(vpin_post) < 10:
            continue

        mean_pre = vpin_pre.mean()
        mean_post = vpin_post.mean()

        # VPIN predictive power: correlation of VPIN with next-day absolute returns
        abs_fwd = returns[token].abs().shift(-1)
        combined = pd.concat([vpin_series.rename('vpin'), abs_fwd.rename('abs_fwd')], axis=1).dropna()
        pre_comb = combined.loc[PRE_START:PRE_END]
        post_comb = combined.loc[POST_START:]

        corr_pre = pre_comb['vpin'].corr(pre_comb['abs_fwd']) if len(pre_comb) > 10 else np.nan
        corr_post = post_comb['vpin'].corr(post_comb['abs_fwd']) if len(post_comb) > 10 else np.nan

        vpin_data[token] = {'pre': mean_pre, 'post': mean_post, 'corr_pre': corr_pre, 'corr_post': corr_post}
        print(f"  {token:<10} {mean_pre:>10.4f} {mean_post:>10.4f} {mean_post - mean_pre:>+10.4f} "
              f"{corr_pre:>18.4f} {corr_post:>18.4f}")

    if vpin_data:
        avg_pre = np.nanmean([v['pre'] for v in vpin_data.values()])
        avg_post = np.nanmean([v['post'] for v in vpin_data.values()])
        print(f"\n  Average VPIN: Pre={avg_pre:.4f}, Post={avg_post:.4f}, Change={avg_post - avg_pre:+.4f}")
        if avg_post > avg_pre + 0.01:
            print("  --> FINDING: VPIN INCREASED post-ETF (more informed trading / toxicity)")
        elif avg_post < avg_pre - 0.01:
            print("  --> FINDING: VPIN DECREASED post-ETF (less informed trading)")
        else:
            print("  --> FINDING: VPIN levels roughly stable")

    return vpin_data


# ============================================================
# SECTION 10: SIGNAL INFORMATION COEFFICIENTS
# ============================================================

def compute_signal_ic(signal_df, returns_df, fwd_horizons, period_mask):
    """
    Compute Information Coefficient (rank correlation of signal vs forward return).
    Returns dict of horizon -> (mean_ic, std_ic, t_stat, hit_rate).
    """
    results = {}
    for horizon in fwd_horizons:
        fwd = returns_df.shift(-horizon).rolling(horizon).sum() if horizon > 1 else returns_df.shift(-1)

        ic_values = []
        for dt in signal_df.index:
            if not period_mask.get(dt, False):
                continue
            sig_row = signal_df.loc[dt].dropna()
            if dt not in fwd.index:
                continue
            fwd_row = fwd.loc[dt].dropna()
            common = sig_row.index.intersection(fwd_row.index)
            if len(common) < 5:
                continue
            # Spearman rank correlation
            ranks_sig = sig_row[common].rank()
            ranks_fwd = fwd_row[common].rank()
            n = len(common)
            d = ranks_sig - ranks_fwd
            rho = 1 - 6 * (d ** 2).sum() / (n * (n ** 2 - 1))
            ic_values.append(rho)

        if len(ic_values) < 20:
            results[horizon] = (np.nan, np.nan, np.nan, np.nan)
        else:
            ic_arr = np.array(ic_values)
            mean_ic = np.mean(ic_arr)
            std_ic = np.std(ic_arr)
            t_stat = mean_ic / (std_ic / np.sqrt(len(ic_arr))) if std_ic > 0 else 0
            hit_rate = np.mean(ic_arr > 0)
            results[horizon] = (mean_ic, std_ic, t_stat, hit_rate)

    return results


def build_signals(close, volume, high, low, opn, returns, log_returns):
    """Build all signals for IC analysis."""
    signals = {}
    tokens = list(returns.columns)

    # --- MOMENTUM SIGNALS ---
    for lb in [5, 10, 20, 60]:
        signals[f'mom_{lb}d'] = returns.rolling(lb).sum()

    # --- MEAN REVERSION SIGNALS ---
    for window in [20, 50]:
        mu = close.rolling(window).mean()
        sigma = close.rolling(window).std()
        signals[f'zscore_{window}d'] = -(close - mu) / sigma  # Negative: low Z = buy signal

    # --- VOLATILITY SIGNALS ---
    # Realized vol ratio (short/long)
    vol_short = returns.rolling(5).std()
    vol_long = returns.rolling(60).std()
    signals['vol_ratio_5_60'] = vol_short / vol_long  # High = vol expanding

    # Vol-of-vol (20d rolling std of 20d vol)
    rvol_20 = returns.rolling(20).std()
    signals['vol_of_vol'] = rvol_20.rolling(20).std()

    # --- VOLUME SIGNALS ---
    # OBV slope (On-Balance Volume trend)
    obv_dict = {}
    for token in tokens:
        if token not in close.columns or token not in volume.columns:
            continue
        r = returns[token]
        v = volume[token]
        sign = np.sign(r)
        obv = (sign * v).cumsum()
        obv_slope = obv.rolling(20).apply(
            lambda x: np.polyfit(np.arange(len(x)), x.values, 1)[0] if len(x) == 20 else np.nan,
            raw=False
        )
        obv_dict[token] = obv_slope
    signals['obv_slope_20d'] = pd.DataFrame(obv_dict)

    # Volume momentum (volume ratio short/long)
    vol_ma_5 = volume.rolling(5).mean()
    vol_ma_20 = volume.rolling(20).mean()
    signals['volume_momentum'] = vol_ma_5 / vol_ma_20

    # --- MICROSTRUCTURE SIGNALS ---
    # Close-to-high ratio (how close to daily high -- buying pressure)
    rng = high - low
    signals['close_to_high'] = (close - low) / rng.replace(0, np.nan)

    # Range/volume (Amihud-like)
    pct_range = (high - low) / close
    signals['range_over_volume'] = pct_range / volume.replace(0, np.nan) * 1e6

    # Amihud illiquidity (|return| / volume)
    signals['amihud'] = returns.abs() / volume.replace(0, np.nan) * 1e6

    # --- CROSS-ASSET SIGNALS ---
    if 'BTC' in returns.columns:
        btc_ret = returns['BTC']
        # Rolling beta to BTC
        beta_dict = {}
        corr_dict = {}
        for token in tokens:
            if token == 'BTC' or token not in returns.columns:
                continue
            alt_ret = returns[token]
            roll_cov = alt_ret.rolling(60).cov(btc_ret)
            roll_var = btc_ret.rolling(60).var()
            beta_dict[token] = roll_cov / roll_var.replace(0, np.nan)
            corr_dict[token] = alt_ret.rolling(60).corr(btc_ret)
        signals['btc_beta_60d'] = pd.DataFrame(beta_dict)
        signals['btc_corr_60d'] = pd.DataFrame(corr_dict)

    return signals


def analyze_information_coefficients(close, volume, high, low, opn, returns, log_returns):
    """Compute ICs for all signals pre vs post ETF."""
    print("\n" + "=" * 80)
    print("SECTION 10: SIGNAL INFORMATION COEFFICIENTS (IC ANALYSIS)")
    print("=" * 80)
    print("  IC = Spearman rank correlation of signal today vs forward N-day return")
    print("  Positive IC = signal predicts direction correctly")
    print("  Negative IC = contrarian signal (flip it for profit)")
    print("  |IC| > 0.03 with |t| > 2 is interesting\n")

    signals = build_signals(close, volume, high, low, opn, returns, log_returns)

    # Period masks
    pre_mask = pd.Series(False, index=returns.index)
    pre_mask.loc[PRE_START:PRE_END] = True
    post_mask = pd.Series(False, index=returns.index)
    post_mask.loc[POST_START:] = True

    # Print results
    for horizon in FWD_HORIZONS:
        print(f"\n  Forward {horizon}d returns:")
        print(f"  {'Signal':<25} {'IC Pre':>8} {'t Pre':>7} {'IC Post':>8} {'t Post':>8} {'IC Chg':>8} {'Verdict':>20}")
        print(f"  {'-'*84}")

        for sig_name, sig_df in sorted(signals.items()):
            if isinstance(sig_df, pd.Series):
                continue  # Skip if not a DataFrame

            ic_pre = compute_signal_ic(sig_df, returns, [horizon], pre_mask)
            ic_post = compute_signal_ic(sig_df, returns, [horizon], post_mask)

            mean_pre, std_pre, t_pre, hr_pre = ic_pre.get(horizon, (np.nan,)*4)
            mean_post, std_post, t_post, hr_post = ic_post.get(horizon, (np.nan,)*4)

            if np.isnan(mean_pre) or np.isnan(mean_post):
                continue

            ic_change = mean_post - mean_pre

            # Verdict
            if abs(mean_post) > 0.03 and abs(t_post) > 2:
                if mean_post > 0:
                    verdict = "WORKS (long signal)"
                else:
                    verdict = "CONTRARIAN (flip it)"
            elif abs(mean_post) > 0.02 and abs(t_post) > 1.5:
                verdict = "MARGINAL"
            else:
                verdict = "DEAD"

            # Improvement/degradation
            if abs(mean_post) > abs(mean_pre) + 0.01:
                verdict += " [IMPROVED]"
            elif abs(mean_post) < abs(mean_pre) - 0.01:
                verdict += " [DEGRADED]"

            print(f"  {sig_name:<25} {mean_pre:>8.4f} {t_pre:>7.2f} {mean_post:>8.4f} {t_post:>8.2f} {ic_change:>+8.4f} {verdict:>20}")

    return signals


# ============================================================
# SECTION 11: TRANSFER ENTROPY IC (bonus signal)
# ============================================================

def compute_te_from_arrays(source_arr, target_arr, bins=6, lag=1):
    """
    Compute Transfer Entropy from numpy arrays directly.
    TE(X->Y) = H(Y_t | Y_{t-1}) - H(Y_t | Y_{t-1}, X_{t-1})
    """
    n = len(source_arr)
    if n <= lag + 10:
        return np.nan

    def discretize(x, n_bins):
        percentiles = np.linspace(0, 100, n_bins + 1)
        edges = np.percentile(x, percentiles)
        edges[0] = -np.inf
        edges[-1] = np.inf
        return np.digitize(x, edges[1:])

    s_d = discretize(source_arr, bins)
    t_d = discretize(target_arr, bins)

    y_t = t_d[lag:]
    y_past = t_d[:-lag]
    x_past = s_d[:-lag]

    eps = 1e-12

    # 3D joint: P(Y_t, Y_past, X_past)
    counts_3d = np.zeros((bins, bins, bins))
    for i in range(len(y_t)):
        counts_3d[y_t[i] % bins, y_past[i] % bins, x_past[i] % bins] += 1
    p_yyx = counts_3d / counts_3d.sum()

    # 2D: P(Y_past, X_past)
    p_yx = p_yyx.sum(axis=0)
    # 2D: P(Y_t, Y_past)
    p_yy = p_yyx.sum(axis=2)
    # 1D: P(Y_past)
    p_y_past = p_yy.sum(axis=0)

    te = 0.0
    for i in range(bins):
        for j in range(bins):
            for k in range(bins):
                p3 = p_yyx[i, j, k]
                if p3 < eps:
                    continue
                p2_yx = p_yx[j, k]
                p2_yy = p_yy[i, j]
                p1_y = p_y_past[j]
                if p2_yx < eps or p2_yy < eps or p1_y < eps:
                    continue
                te += p3 * np.log2(p3 * p1_y / (p2_yx * p2_yy))
    return te


def compute_te_signal(returns):
    """
    Compute rolling Transfer Entropy from BTC as a cross-sectional signal.
    Uses 60d rolling windows. Samples every 5 days for speed.
    """
    print("\n" + "=" * 80)
    print("SECTION 11: TRANSFER ENTROPY AS A SIGNAL")
    print("=" * 80)

    if 'BTC' not in returns.columns:
        print("  BTC data not available")
        return

    btc = returns['BTC']
    window = 60
    step = 5  # Sample every 5 days for speed

    print(f"\n  Rolling {window}d TE(BTC->ALT) as predictor of forward returns:")
    print(f"  (sampled every {step} days for efficiency)")
    print(f"  {'Token':<10} {'TE-Ret Corr Pre':>16} {'TE-Ret Corr Post':>17} {'TE mean Pre':>12} {'TE mean Post':>13}")
    print(f"  {'-'*68}")

    for token in TARGET_ALTS[:10]:
        if token not in returns.columns:
            continue
        alt = returns[token]
        common = btc.dropna().index.intersection(alt.dropna().index)
        if len(common) < window + 50:
            continue

        btc_aligned = btc.loc[common].values
        alt_aligned = alt.loc[common].values

        # Compute rolling TE (sampled)
        te_values = []
        te_dates = []
        for i in range(window, len(common), step):
            btc_w = btc_aligned[i - window:i]
            alt_w = alt_aligned[i - window:i]
            if np.any(np.isnan(btc_w)) or np.any(np.isnan(alt_w)):
                continue
            te = compute_te_from_arrays(btc_w, alt_w, bins=4, lag=1)
            if not np.isnan(te):
                te_values.append(te)
                te_dates.append(common[i])

        if len(te_values) < 30:
            continue

        te_series = pd.Series(te_values, index=te_dates)
        fwd_5d = alt.rolling(5).sum().shift(-5)
        combined = pd.concat([te_series.rename('te'), fwd_5d.rename('fwd')], axis=1).dropna()

        pre = combined.loc[PRE_START:PRE_END]
        post = combined.loc[POST_START:]

        corr_pre = pre['te'].corr(pre['fwd']) if len(pre) > 15 else np.nan
        corr_post = post['te'].corr(post['fwd']) if len(post) > 15 else np.nan
        te_pre = te_series.loc[PRE_START:PRE_END].mean() if len(te_series.loc[PRE_START:PRE_END]) > 0 else np.nan
        te_post = te_series.loc[POST_START:].mean() if len(te_series.loc[POST_START:]) > 0 else np.nan

        corr_pre_s = f"{corr_pre:>16.4f}" if not np.isnan(corr_pre) else f"{'N/A':>16}"
        corr_post_s = f"{corr_post:>17.4f}" if not np.isnan(corr_post) else f"{'N/A':>17}"
        te_pre_s = f"{te_pre:>12.4f}" if not np.isnan(te_pre) else f"{'N/A':>12}"
        te_post_s = f"{te_post:>13.4f}" if not np.isnan(te_post) else f"{'N/A':>13}"
        print(f"  {token:<10} {corr_pre_s} {corr_post_s} {te_pre_s} {te_post_s}")


# ============================================================
# FINAL SUMMARY
# ============================================================

def print_summary():
    """Print final summary."""
    print("\n" + "=" * 80)
    print("SUMMARY: KEY FINDINGS")
    print("=" * 80)
    print("""
  The analysis above compares market microstructure, signal effectiveness, and
  information flow before and after the BTC spot ETF approval (Jan 10, 2024).

  KEY QUESTIONS ANSWERED:

  1. CORRELATION: Has the market become more/less correlated? If more, alpha from
     diversification is harder. If less, cross-sectional strategies improve.

  2. VOLATILITY: Has vol compressed? Vol compression kills momentum and breakout
     strategies but can help mean reversion if ranges tighten predictably.

  3. MOMENTUM: Do trends persist? If momentum decays post-ETF, it suggests
     institutional arb is faster. Short lookback momentum may still work.

  4. MEAN REVERSION: Do Z-score extremes revert? If MR improves post-ETF,
     it suggests tighter ranges with reliable bounce-back behavior.

  5. LEAD-LAG: Does BTC lead alts? If BTC leads MORE post-ETF, the ETF is
     acting as the information entry point. Trading alts on BTC signals may work.

  6. TRANSFER ENTROPY: Where does information flow? Asymmetric TE reveals
     which tokens are information leaders vs followers.

  7. VPIN: Has informed trading increased? Higher VPIN post-ETF suggests
     institutional flow is more toxic (adverse selection risk).

  8. IC ANALYSIS: Which signals actually work? The IC table is the most
     actionable output — it tells you exactly which signals to use and which
     to flip or discard.

  STRATEGY IMPLICATIONS:
  - If momentum ICs are negative: use mean reversion
  - If BTC lead-lag increased: trade alt signals conditional on BTC direction
  - If VPIN predicts volatility: use VPIN as a vol filter
  - If vol compressed: size up on range-bound strategies, reduce breakout bets
  - Contrarian signals (negative IC) are profitable when flipped
""")


# ============================================================
# MAIN
# ============================================================

def main():
    print("=" * 80)
    print("POST-ETF REGIME ANALYSIS")
    print(f"BTC Spot ETF Approval Date: {ETF_DATE.date()}")
    print(f"Analysis run: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)

    print("\nLoading data...")
    data = load_all_data()
    print(f"  Loaded {len(data)} tokens")

    close, volume, high, low, opn, returns, log_returns = build_returns_panel(data)
    print(f"  Date range: {close.index.min().date()} to {close.index.max().date()}")
    print(f"  Pre-ETF window: {PRE_START.date()} to {PRE_END.date()}")
    print(f"  Post-ETF window: {POST_START.date()} to {close.index.max().date()}")

    # Run all analyses
    analyze_correlations(returns)
    analyze_volatility(returns, close)
    analyze_momentum(returns)
    analyze_mean_reversion(returns, close)
    analyze_volume(volume, returns)
    analyze_autocorrelation(returns)
    analyze_lead_lag(returns)
    analyze_transfer_entropy(returns)
    analyze_vpin(close, volume, returns)
    analyze_information_coefficients(close, volume, high, low, opn, returns, log_returns)
    compute_te_signal(returns)
    print_summary()

    print("\n" + "=" * 80)
    print("ANALYSIS COMPLETE")
    print("=" * 80)


if __name__ == '__main__':
    main()
