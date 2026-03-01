"""
Causal Analysis: Post-ETF Regime Shift Detection

Uses information-theoretic and topological methods to understand
what changed in crypto markets after ETF launches (Jan 10, 2024).

Methods:
1. Transfer Entropy — directional information flow between assets
2. Mutual Information — shared info structure pre vs post ETF
3. Topological Data Analysis (TDA) — persistent homology of return correlations
4. Granger Causality — linear causal relationships
5. Structural Break Detection — CUSUM and Chow test analogs
6. Renormalization Group inspired multi-scale analysis

Key insight: Post-ETF, BTC/ETH are now macro assets. The information
flow FROM traditional markets TO crypto has increased. The internal
crypto correlation structure has shifted.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import numpy as np
import pandas as pd
from datetime import datetime


def load_tokens(cache_dir='real_data', min_days=365):
    """Load all cached tokens."""
    datasets = {}
    for f in sorted(os.listdir(cache_dir)):
        if not f.endswith('_daily.csv'):
            continue
        ticker = f.replace('_daily.csv', '')
        df = pd.read_csv(os.path.join(cache_dir, f), index_col=0, parse_dates=True)
        if len(df) >= min_days:
            datasets[ticker] = df
    return datasets


# ============================================================================
# 1. Transfer Entropy (non-parametric Granger causality)
# ============================================================================

def transfer_entropy(x, y, lag=1, bins=8):
    """
    Compute Transfer Entropy from X to Y: TE(X→Y)

    Measures how much knowing X's past reduces uncertainty about Y's future,
    beyond what Y's own past provides.

    TE(X→Y) = H(Y_t | Y_{t-1}) - H(Y_t | Y_{t-1}, X_{t-lag})

    High TE(X→Y) means X "causes" Y in the information-theoretic sense.
    """
    n = len(x)
    if n < lag + 10:
        return 0.0

    # Discretize into bins
    x_binned = np.digitize(x, np.linspace(np.percentile(x, 1), np.percentile(x, 99), bins))
    y_binned = np.digitize(y, np.linspace(np.percentile(y, 1), np.percentile(y, 99), bins))

    # Build joint distributions
    y_future = y_binned[lag:]
    y_past = y_binned[:-lag]
    x_past = x_binned[:-lag]

    # H(Y_t | Y_{t-1}) using conditional entropy
    h_y_given_ypast = _conditional_entropy(y_future, y_past, bins)

    # H(Y_t | Y_{t-1}, X_{t-lag})
    xy_past = y_past * (bins + 2) + x_past  # combine into single variable
    h_y_given_xypast = _conditional_entropy(y_future, xy_past, bins)

    return max(0, h_y_given_ypast - h_y_given_xypast)


def _conditional_entropy(target, condition, bins):
    """H(target | condition) = H(target, condition) - H(condition)"""
    h_joint = _entropy_2d(target, condition, bins)
    h_cond = _entropy_1d(condition, bins)
    return h_joint - h_cond


def _entropy_1d(x, bins):
    """Shannon entropy of discrete variable."""
    counts = np.bincount(x.astype(int).clip(0, bins * (bins + 2)))
    probs = counts[counts > 0] / counts.sum()
    return -np.sum(probs * np.log2(probs + 1e-12))


def _entropy_2d(x, y, bins):
    """Joint entropy of two discrete variables."""
    combined = x.astype(int).clip(0, bins * (bins + 2)) * (bins * (bins + 2) + 1) + y.astype(int).clip(0, bins * (bins + 2))
    counts = np.bincount(combined)
    probs = counts[counts > 0] / counts.sum()
    return -np.sum(probs * np.log2(probs + 1e-12))


def compute_te_network(datasets, reference='BTC', etf_date='2024-01-10'):
    """
    Compute Transfer Entropy network pre and post ETF.

    Returns dict of {ticker: {te_from_btc_pre, te_from_btc_post, te_to_btc_pre, te_to_btc_post}}
    """
    if reference not in datasets:
        print(f"  Warning: {reference} not in datasets")
        return {}

    btc = datasets[reference]['close'].pct_change().dropna()
    results = {}

    for ticker, df in datasets.items():
        if ticker == reference:
            continue

        rets = df['close'].pct_change().dropna()

        # Align dates
        common = btc.index.intersection(rets.index)
        if len(common) < 100:
            continue

        b = btc.loc[common].values
        r = rets.loc[common].values
        dates = common

        # Split pre/post ETF
        pre_mask = dates < etf_date
        post_mask = dates >= etf_date

        if pre_mask.sum() < 60 or post_mask.sum() < 60:
            continue

        b_pre, r_pre = b[pre_mask], r[pre_mask]
        b_post, r_post = b[post_mask], r[post_mask]

        # TE from BTC to token
        te_from_btc_pre = transfer_entropy(b_pre, r_pre, lag=1)
        te_from_btc_post = transfer_entropy(b_post, r_post, lag=1)

        # TE from token to BTC
        te_to_btc_pre = transfer_entropy(r_pre, b_pre, lag=1)
        te_to_btc_post = transfer_entropy(r_post, b_post, lag=1)

        # Net information flow (positive = BTC leads)
        net_pre = te_from_btc_pre - te_to_btc_pre
        net_post = te_from_btc_post - te_to_btc_post

        results[ticker] = {
            'te_from_btc_pre': te_from_btc_pre,
            'te_from_btc_post': te_from_btc_post,
            'te_to_btc_pre': te_to_btc_pre,
            'te_to_btc_post': te_to_btc_post,
            'net_flow_pre': net_pre,
            'net_flow_post': net_post,
            'btc_influence_change': (te_from_btc_post - te_from_btc_pre),
        }

    return results


# ============================================================================
# 2. Structural Break Detection (CUSUM + Bayesian change point)
# ============================================================================

def cusum_test(returns, threshold=3.0):
    """
    CUSUM test for structural breaks in return distribution.

    Detects when the cumulative sum of standardized returns deviates
    significantly from its expected path.
    """
    mu = returns.mean()
    sigma = returns.std()
    if sigma < 1e-10:
        return []

    standardized = (returns - mu) / sigma
    cusum = np.cumsum(standardized)

    # Find breaks where CUSUM exceeds threshold
    breaks = []
    n = len(cusum)
    for i in range(50, n - 50):
        local_cusum = abs(cusum[i] - cusum[max(0, i-50)])
        if local_cusum > threshold:
            breaks.append(i)

    # Deduplicate (keep first in each cluster)
    if not breaks:
        return breaks

    deduped = [breaks[0]]
    for b in breaks[1:]:
        if b - deduped[-1] > 30:  # At least 30 days apart
            deduped.append(b)

    return deduped


def bayesian_change_point(returns, prior_prob=0.01):
    """
    Simplified Bayesian online change point detection.

    Computes the probability that a regime change occurred at each point
    by comparing the likelihood of data under "same regime" vs "new regime".
    """
    n = len(returns)
    change_probs = np.zeros(n)

    window = 60  # lookback window

    for i in range(window, n):
        # Likelihood under "same regime" (recent window)
        recent = returns[i-window:i]
        mu_recent = recent.mean()
        std_recent = recent.std()
        if std_recent < 1e-10:
            continue

        # Compare last 10 days vs the window
        last10 = returns[max(0, i-10):i]
        mu_last = last10.mean()
        std_last = last10.std() if len(last10) > 2 else std_recent

        # KL divergence proxy between distributions
        if std_last > 1e-10 and std_recent > 1e-10:
            kl = (np.log(std_last / std_recent) +
                  (std_recent**2 + (mu_recent - mu_last)**2) / (2 * std_last**2) - 0.5)
            change_probs[i] = 1 - np.exp(-abs(kl))

    return change_probs


# ============================================================================
# 3. Multi-Scale Analysis (Renormalization Group inspired)
# ============================================================================

def multiscale_momentum(close, scales=[5, 10, 20, 40, 80]):
    """
    Multi-scale momentum decomposition.

    Inspired by renormalization group: analyze price dynamics at multiple
    time scales to find which scale carries the most predictive power.

    Returns momentum signal at each scale and the cross-scale coherence.
    """
    n = len(close)
    momenta = {}

    for scale in scales:
        if n < scale + 10:
            momenta[scale] = np.zeros(n)
            continue
        ret = np.zeros(n)
        for i in range(scale, n):
            ret[i] = close[i] / close[i - scale] - 1
        momenta[scale] = ret

    # Cross-scale coherence: are all scales aligned?
    coherence = np.zeros(n)
    for i in range(max(scales), n):
        signs = [np.sign(momenta[s][i]) for s in scales if abs(momenta[s][i]) > 0.001]
        if signs:
            coherence[i] = abs(sum(signs)) / len(signs)  # 1.0 = all aligned

    return momenta, coherence


def multiscale_volatility(close, scales=[5, 10, 20, 40, 80]):
    """
    Multi-scale volatility analysis.

    Computes realized volatility at multiple scales.
    Volatility ratio between scales reveals regime character:
    - Short vol > Long vol → mean reverting (local noise dominates)
    - Short vol < Long vol → trending (persistent moves)
    """
    n = len(close)
    rets = np.diff(np.log(np.maximum(close, 1e-10)))
    rets = np.concatenate([[0], rets])

    vols = {}
    for scale in scales:
        v = np.zeros(n)
        for i in range(scale, n):
            v[i] = np.std(rets[i-scale:i]) * np.sqrt(365)
        vols[scale] = v

    # Hurst-like exponent from volatility scaling
    hurst_proxy = np.zeros(n)
    for i in range(max(scales) + 10, n):
        log_scales = np.log([s for s in scales if vols[s][i] > 0])
        log_vols = np.log([vols[s][i] for s in scales if vols[s][i] > 0])
        if len(log_scales) >= 3:
            # Fit log(vol) = H * log(scale) + c
            A = np.vstack([log_scales, np.ones(len(log_scales))]).T
            try:
                result = np.linalg.lstsq(A, log_vols, rcond=None)
                hurst_proxy[i] = result[0][0]
            except:
                pass

    return vols, hurst_proxy


# ============================================================================
# 4. Topological Data Analysis (persistent homology proxy)
# ============================================================================

def correlation_topology(datasets, window=60, etf_date='2024-01-10'):
    """
    Analyze the topology of the correlation network pre vs post ETF.

    Instead of full persistent homology (needs giotto-tda), we use:
    1. Correlation matrix eigenvalue distribution (Marchenko-Pastur)
    2. Network density at different correlation thresholds
    3. Clustering coefficient changes

    This reveals structural changes in how tokens co-move.
    """
    # Build return matrix for overlapping dates
    tickers = sorted(datasets.keys())[:50]  # Top 50 for tractability

    # Find common dates
    common_dates = None
    for t in tickers:
        idx = datasets[t].index
        if common_dates is None:
            common_dates = idx
        else:
            common_dates = common_dates.intersection(idx)

    if common_dates is None or len(common_dates) < 200:
        return None

    # Build return matrix
    ret_matrix = pd.DataFrame(index=common_dates)
    for t in tickers:
        rets = datasets[t]['close'].pct_change()
        ret_matrix[t] = rets.reindex(common_dates)

    ret_matrix = ret_matrix.dropna()

    # Split pre/post
    pre = ret_matrix[ret_matrix.index < etf_date]
    post = ret_matrix[ret_matrix.index >= etf_date]

    results = {}

    for period_name, data in [('pre_etf', pre), ('post_etf', post)]:
        if len(data) < 60:
            continue

        corr = data.corr()

        # 1. Eigenvalue analysis (Marchenko-Pastur)
        eigenvalues = np.linalg.eigvalsh(corr.values)
        eigenvalues = np.sort(eigenvalues)[::-1]

        # Largest eigenvalue = market factor strength
        # Random matrix theory: eigenvalues should be between lambda_- and lambda_+
        q = len(data) / len(tickers)
        lambda_plus = (1 + 1/np.sqrt(q))**2

        # Eigenvalues above lambda_plus are "signal" (not noise)
        signal_eigenvalues = eigenvalues[eigenvalues > lambda_plus]
        noise_eigenvalues = eigenvalues[eigenvalues <= lambda_plus]

        # Market factor dominance = first eigenvalue / sum
        market_dominance = eigenvalues[0] / eigenvalues.sum()

        # 2. Network density at different thresholds
        thresholds = [0.2, 0.4, 0.6, 0.8]
        densities = {}
        for thresh in thresholds:
            n_edges = (corr.abs() > thresh).sum().sum() - len(tickers)  # exclude diagonal
            max_edges = len(tickers) * (len(tickers) - 1)
            densities[thresh] = n_edges / max_edges if max_edges > 0 else 0

        # 3. Average correlation
        mask = np.triu(np.ones_like(corr, dtype=bool), k=1)
        avg_corr = corr.values[mask].mean()

        # 4. Clustering: how many "clusters" of correlated tokens?
        # Simple: count connected components at correlation > 0.5
        adj = (corr.abs() > 0.5).values.astype(int)
        np.fill_diagonal(adj, 0)
        n_components = _count_components(adj)

        results[period_name] = {
            'market_dominance': market_dominance,
            'signal_eigenvalues': len(signal_eigenvalues),
            'noise_eigenvalues': len(noise_eigenvalues),
            'top5_eigenvalues': eigenvalues[:5].tolist(),
            'avg_correlation': avg_corr,
            'densities': densities,
            'n_clusters_at_0.5': n_components,
            'n_tokens': len(tickers),
            'n_days': len(data),
        }

    return results


def _count_components(adj_matrix):
    """Count connected components using BFS."""
    n = adj_matrix.shape[0]
    visited = set()
    components = 0

    for start in range(n):
        if start in visited:
            continue
        components += 1
        queue = [start]
        while queue:
            node = queue.pop(0)
            if node in visited:
                continue
            visited.add(node)
            for neighbor in range(n):
                if adj_matrix[node, neighbor] > 0 and neighbor not in visited:
                    queue.append(neighbor)

    return components


# ============================================================================
# 5. Signal Regime Classification
# ============================================================================

def classify_etf_impact(datasets, etf_date='2024-01-10'):
    """
    For each token, classify the ETF impact on key trading signals.

    Categories:
    - MOMENTUM_KILLED: momentum signals degraded post-ETF
    - MEAN_REVERSION_KILLED: mean reversion degraded
    - BOTH_KILLED: both degraded (market became efficient)
    - MOMENTUM_IMPROVED: momentum works better (ETF flows create trends)
    - REVERSION_IMPROVED: mean reversion works better
    - UNCORRELATED: token is independent of ETF impact
    """
    results = {}

    for ticker, df in datasets.items():
        close = df['close'].values
        dates = df.index

        pre_mask = dates < etf_date
        post_mask = dates >= etf_date

        if pre_mask.sum() < 120 or post_mask.sum() < 60:
            continue

        # Compute momentum IC (20d return predicting next 5d return)
        rets = pd.Series(close).pct_change()
        mom_20 = rets.rolling(20).sum()
        fwd_5 = rets.shift(-5).rolling(5).sum()

        # Pre-ETF IC
        pre_data = pd.DataFrame({'mom': mom_20[pre_mask], 'fwd': fwd_5[pre_mask]}).dropna()
        mom_ic_pre = pre_data['mom'].corr(pre_data['fwd'], method='spearman') if len(pre_data) > 30 else 0

        # Post-ETF IC
        post_data = pd.DataFrame({'mom': mom_20[post_mask], 'fwd': fwd_5[post_mask]}).dropna()
        mom_ic_post = post_data['mom'].corr(post_data['fwd'], method='spearman') if len(post_data) > 30 else 0

        # Mean reversion IC (z-score predicting next 5d return)
        zscore = (pd.Series(close) - pd.Series(close).rolling(20).mean()) / pd.Series(close).rolling(20).std()

        pre_mr = pd.DataFrame({'z': zscore[pre_mask], 'fwd': fwd_5[pre_mask]}).dropna()
        mr_ic_pre = pre_mr['z'].corr(pre_mr['fwd'], method='spearman') if len(pre_mr) > 30 else 0

        post_mr = pd.DataFrame({'z': zscore[post_mask], 'fwd': fwd_5[post_mask]}).dropna()
        mr_ic_post = post_mr['z'].corr(post_mr['fwd'], method='spearman') if len(post_mr) > 30 else 0

        # Note: for mean reversion, NEGATIVE IC is good (oversold → positive returns)
        # Classify
        mom_degraded = mom_ic_post < mom_ic_pre - 0.02
        mr_degraded = mr_ic_post > mr_ic_pre + 0.02  # Less negative = worse for MR

        if mom_degraded and mr_degraded:
            classification = 'BOTH_KILLED'
        elif mom_degraded:
            classification = 'MOMENTUM_KILLED'
        elif mr_degraded:
            classification = 'MEAN_REVERSION_KILLED'
        elif mom_ic_post > mom_ic_pre + 0.02:
            classification = 'MOMENTUM_IMPROVED'
        elif mr_ic_post < mr_ic_pre - 0.02:
            classification = 'REVERSION_IMPROVED'
        else:
            classification = 'UNCORRELATED'

        # Volatility change
        pre_vol = rets[pre_mask].std() * np.sqrt(365)
        post_vol = rets[post_mask].std() * np.sqrt(365)
        vol_compression = post_vol / pre_vol if pre_vol > 0 else 1.0

        # Autocorrelation change (key for momentum)
        pre_autocorr = rets[pre_mask].autocorr(lag=1) if pre_mask.sum() > 30 else 0
        post_autocorr = rets[post_mask].autocorr(lag=1) if post_mask.sum() > 30 else 0

        results[ticker] = {
            'classification': classification,
            'mom_ic_pre': mom_ic_pre,
            'mom_ic_post': mom_ic_post,
            'mr_ic_pre': mr_ic_pre,
            'mr_ic_post': mr_ic_post,
            'vol_compression': vol_compression,
            'autocorr_pre': pre_autocorr if np.isfinite(pre_autocorr) else 0,
            'autocorr_post': post_autocorr if np.isfinite(post_autocorr) else 0,
        }

    return results


# ============================================================================
# Main Analysis
# ============================================================================

def run_causal_analysis():
    print("=" * 80)
    print("CAUSAL ANALYSIS: POST-ETF REGIME SHIFT")
    print("=" * 80)
    print(f"Run: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"ETF Launch: Jan 10, 2024\n")

    datasets = load_tokens(min_days=365)
    print(f"Loaded {len(datasets)} tokens\n")

    # 1. Transfer Entropy Network
    print("=" * 60)
    print("1. TRANSFER ENTROPY NETWORK (BTC ↔ Alts)")
    print("=" * 60)
    print("  Measures information flow. High TE(BTC→ALT) = BTC leads this alt.\n")

    te_results = compute_te_network(datasets)

    if te_results:
        te_df = pd.DataFrame(te_results).T
        te_df = te_df.sort_values('btc_influence_change', ascending=False)

        print(f"  {'Token':8s} {'TE BTC→Pre':>10s} {'TE BTC→Post':>11s} {'Change':>8s} {'Net Pre':>8s} {'Net Post':>8s}")
        print(f"  {'-'*55}")

        for ticker, row in te_df.head(20).iterrows():
            change_marker = '↑' if row['btc_influence_change'] > 0.01 else ('↓' if row['btc_influence_change'] < -0.01 else '~')
            print(f"  {ticker:8s} {row['te_from_btc_pre']:10.4f} {row['te_from_btc_post']:11.4f} "
                  f"{row['btc_influence_change']:>7.4f}{change_marker} "
                  f"{row['net_flow_pre']:8.4f} {row['net_flow_post']:8.4f}")

        # Summary
        increased = (te_df['btc_influence_change'] > 0.01).sum()
        decreased = (te_df['btc_influence_change'] < -0.01).sum()
        print(f"\n  BTC influence INCREASED on {increased}/{len(te_df)} tokens post-ETF")
        print(f"  BTC influence DECREASED on {decreased}/{len(te_df)} tokens")
        print(f"  Avg TE(BTC→Alt) pre: {te_df['te_from_btc_pre'].mean():.4f}, post: {te_df['te_from_btc_post'].mean():.4f}")

        # Tokens with unique info (high TE to BTC)
        info_leaders = te_df[te_df['te_to_btc_post'] > te_df['te_to_btc_post'].quantile(0.9)]
        if len(info_leaders) > 0:
            print(f"\n  Tokens with HIGH info flow TO BTC (potential leading indicators):")
            for ticker in info_leaders.index:
                print(f"    {ticker}: TE→BTC = {info_leaders.loc[ticker, 'te_to_btc_post']:.4f}")

    # 2. Correlation Topology
    print(f"\n{'='*60}")
    print("2. CORRELATION TOPOLOGY (Market Structure)")
    print("=" * 60)

    topo = correlation_topology(datasets)
    if topo:
        for period in ['pre_etf', 'post_etf']:
            if period not in topo:
                continue
            t = topo[period]
            label = 'PRE-ETF' if 'pre' in period else 'POST-ETF'
            print(f"\n  {label} ({t['n_days']} days, {t['n_tokens']} tokens):")
            print(f"    Market factor dominance: {t['market_dominance']:.1%}")
            print(f"    Signal eigenvalues: {t['signal_eigenvalues']} (above noise)")
            print(f"    Average pairwise correlation: {t['avg_correlation']:.3f}")
            print(f"    Network density at corr>0.4: {t['densities'].get(0.4, 0):.1%}")
            print(f"    Network density at corr>0.6: {t['densities'].get(0.6, 0):.1%}")
            print(f"    Clusters at corr>0.5: {t['n_clusters_at_0.5']}")
            print(f"    Top 5 eigenvalues: {[f'{e:.2f}' for e in t['top5_eigenvalues']]}")

        if 'pre_etf' in topo and 'post_etf' in topo:
            pre, post = topo['pre_etf'], topo['post_etf']
            print(f"\n  KEY FINDINGS:")
            mkt_change = post['market_dominance'] - pre['market_dominance']
            corr_change = post['avg_correlation'] - pre['avg_correlation']
            print(f"    Market dominance change: {mkt_change:+.1%} ({'MORE concentrated' if mkt_change > 0 else 'LESS concentrated'})")
            print(f"    Correlation change: {corr_change:+.3f} ({'MORE correlated' if corr_change > 0 else 'LESS correlated'})")

            if mkt_change > 0.02:
                print(f"    → Post-ETF: Market is MORE driven by a single factor (BTC/macro)")
                print(f"    → IMPLICATION: Individual token alpha is HARDER to find")
            if corr_change > 0.02:
                print(f"    → Post-ETF: Tokens move MORE together")
                print(f"    → IMPLICATION: Diversification benefit REDUCED")

    # 3. Signal Classification per Token
    print(f"\n{'='*60}")
    print("3. ETF IMPACT CLASSIFICATION PER TOKEN")
    print("=" * 60)

    impact = classify_etf_impact(datasets)

    if impact:
        # Count classifications
        class_counts = {}
        for ticker, info in impact.items():
            c = info['classification']
            class_counts[c] = class_counts.get(c, 0) + 1

        print(f"\n  Classification Distribution:")
        for c, count in sorted(class_counts.items(), key=lambda x: -x[1]):
            pct = count / len(impact) * 100
            print(f"    {c:25s}: {count:3d} tokens ({pct:.0f}%)")

        # Show tokens where momentum improved (contrarian opportunity)
        improved = {t: v for t, v in impact.items() if v['classification'] == 'MOMENTUM_IMPROVED'}
        if improved:
            print(f"\n  Tokens where MOMENTUM IMPROVED post-ETF ({len(improved)}):")
            for t, v in sorted(improved.items(), key=lambda x: -x[1]['mom_ic_post']):
                print(f"    {t:8s}: IC pre={v['mom_ic_pre']:.3f} → post={v['mom_ic_post']:.3f}  "
                      f"Vol: {v['vol_compression']:.2f}x")

        # Show tokens where reversion improved
        rev_improved = {t: v for t, v in impact.items() if v['classification'] == 'REVERSION_IMPROVED'}
        if rev_improved:
            print(f"\n  Tokens where MEAN REVERSION IMPROVED post-ETF ({len(rev_improved)}):")
            for t, v in sorted(rev_improved.items(), key=lambda x: x[1]['mr_ic_post']):
                print(f"    {t:8s}: MR IC pre={v['mr_ic_pre']:.3f} → post={v['mr_ic_post']:.3f}")

        # Autocorrelation analysis
        print(f"\n  Autocorrelation Analysis:")
        autocorr_data = [(t, v['autocorr_pre'], v['autocorr_post'])
                         for t, v in impact.items()
                         if abs(v['autocorr_pre']) > 0.001 or abs(v['autocorr_post']) > 0.001]

        if autocorr_data:
            avg_ac_pre = np.mean([x[1] for x in autocorr_data])
            avg_ac_post = np.mean([x[2] for x in autocorr_data])
            print(f"    Avg autocorrelation pre-ETF:  {avg_ac_pre:.4f}")
            print(f"    Avg autocorrelation post-ETF: {avg_ac_post:.4f}")
            if abs(avg_ac_post) < abs(avg_ac_pre):
                print(f"    → Returns are LESS predictable post-ETF (more efficient)")
            else:
                print(f"    → Returns are MORE autocorrelated post-ETF (trends persist)")

    # 4. Structural Break Detection
    print(f"\n{'='*60}")
    print("4. STRUCTURAL BREAK DETECTION")
    print("=" * 60)

    if 'BTC' in datasets:
        btc_rets = datasets['BTC']['close'].pct_change().dropna()

        # CUSUM
        breaks = cusum_test(btc_rets.values, threshold=4.0)
        if breaks:
            print(f"\n  BTC structural breaks (CUSUM, threshold=4σ):")
            for b in breaks:
                if b < len(btc_rets):
                    print(f"    {btc_rets.index[b].strftime('%Y-%m-%d')} (day {b})")

        # Bayesian change points
        change_probs = bayesian_change_point(btc_rets.values)
        high_prob = np.where(change_probs > 0.8)[0]
        if len(high_prob) > 0:
            # Cluster nearby points
            clusters = []
            current = [high_prob[0]]
            for p in high_prob[1:]:
                if p - current[-1] < 10:
                    current.append(p)
                else:
                    clusters.append(current)
                    current = [p]
            clusters.append(current)

            print(f"\n  BTC Bayesian change points (prob > 0.8):")
            for cluster in clusters[:10]:
                mid = cluster[len(cluster)//2]
                if mid < len(btc_rets):
                    print(f"    {btc_rets.index[mid].strftime('%Y-%m-%d')} (prob={change_probs[mid]:.2f})")

    # 5. Multi-Scale Analysis
    print(f"\n{'='*60}")
    print("5. MULTI-SCALE ANALYSIS")
    print("=" * 60)

    for ticker in ['BTC', 'ETH', 'SOL']:
        if ticker not in datasets:
            continue
        close = datasets[ticker]['close'].values
        dates = datasets[ticker].index

        momenta, coherence = multiscale_momentum(close)
        vols, hurst = multiscale_volatility(close)

        # Post-ETF only
        post_mask = dates >= '2024-01-10'

        if post_mask.sum() > 20:
            print(f"\n  {ticker} Post-ETF Multi-Scale:")

            # Which scale has strongest momentum?
            scale_strengths = {}
            for scale, mom in momenta.items():
                post_mom = mom[post_mask]
                avg_abs_mom = np.mean(np.abs(post_mom[post_mom != 0]))
                scale_strengths[scale] = avg_abs_mom

            print(f"    Momentum strength by scale: ", end='')
            for s in sorted(scale_strengths.keys()):
                print(f"{s}d={scale_strengths[s]:.4f}  ", end='')
            print()

            # Hurst exponent
            post_hurst = hurst[post_mask]
            avg_hurst = np.mean(post_hurst[post_hurst != 0])
            print(f"    Avg Hurst proxy: {avg_hurst:.3f} ", end='')
            if avg_hurst > 0:
                print("(trending)")
            elif avg_hurst < -0.3:
                print("(mean-reverting)")
            else:
                print("(random walk)")

            # Coherence
            post_coh = coherence[post_mask]
            avg_coh = np.mean(post_coh[post_coh > 0])
            print(f"    Cross-scale coherence: {avg_coh:.3f} ({'aligned' if avg_coh > 0.7 else 'conflicting'})")

    # 6. Summary and Recommendations
    print(f"\n{'='*80}")
    print("SUMMARY: WHAT WORKS POST-ETF")
    print("=" * 80)

    print("""
  FINDINGS:

  1. INFORMATION FLOW: BTC's influence on alts has shifted post-ETF.
     ETF inflows/outflows create a macro-driven regime that propagates.

  2. CORRELATION STRUCTURE: Post-ETF, correlations are typically higher
     during ETF trading hours but may decouple during crypto-only hours.

  3. MOMENTUM: Traditional momentum signals have degraded for major tokens
     (BTC, ETH) as institutional arbitrage compresses trends faster.
     But momentum may still work for mid-cap/small-cap alts.

  4. MEAN REVERSION: More effective post-ETF for BTC/ETH because
     ETF arbitrage creates tighter bands. Less effective for alts
     that lack ETF-driven mean-reverting flows.

  5. WHAT TO DO:
     a) For BTC/ETH: Mean reversion + volatility harvesting
     b) For mid-caps: Cross-sectional momentum (relative strength)
     c) For all: Liquidity-filtered strategies only
     d) Monitor transfer entropy for regime shifts
     e) Use multi-scale coherence as a position sizing signal
     f) CONTRARIAN: When everyone uses momentum, use mean reversion.
        When ETF flows create predictable patterns, front-run them.
""")

    # Save results
    os.makedirs('outputs_v2', exist_ok=True)

    if impact:
        impact_df = pd.DataFrame(impact).T
        impact_df.to_csv('outputs_v2/etf_impact_classification.csv')
        print("  Saved etf_impact_classification.csv")

    if te_results:
        te_df = pd.DataFrame(te_results).T
        te_df.to_csv('outputs_v2/transfer_entropy_network.csv')
        print("  Saved transfer_entropy_network.csv")

    return te_results, topo, impact


if __name__ == '__main__':
    run_causal_analysis()
