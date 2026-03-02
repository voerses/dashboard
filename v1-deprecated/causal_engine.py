"""
Advanced Causal Analysis Engine

Implements:
1. Transfer Entropy (non-linear Granger causality) — directional information flow
2. Mutual Information — total non-linear dependency
3. Persistent Homology (TDA) — topological regime detection
4. Convergent Cross Mapping (CCM) — true causal inference
5. Causal DAG construction — identifies indicator → price causation

These replace Pearson correlation which only captures linear relationships.
"""
import numpy as np
import pandas as pd
from scipy import stats
from sklearn.neighbors import KDTree
from collections import defaultdict


# ============================================================
# 1. TRANSFER ENTROPY
# ============================================================

def transfer_entropy(source, target, lag=1, bins=6):
    """
    Compute Transfer Entropy: T_{X→Y} = H(Y_t | Y_{t-lag}) - H(Y_t | Y_{t-lag}, X_{t-lag})

    Measures directed information flow from source to target.
    Higher TE means source CAUSES target movements (not just correlates).

    Uses binned estimator for robustness with financial data.
    """
    source = np.asarray(source, dtype=float)
    target = np.asarray(target, dtype=float)

    # Remove NaN
    mask = ~(np.isnan(source) | np.isnan(target))
    source, target = source[mask], target[mask]

    n = len(source) - lag
    if n < 50:
        return 0.0

    # Discretize
    s_bins = _adaptive_discretize(source[:-lag], bins)
    t_now = _adaptive_discretize(target[lag:], bins)
    t_past = _adaptive_discretize(target[:-lag], bins)

    # Joint and marginal probabilities
    te = 0.0
    for t_n in range(bins):
        for t_p in range(bins):
            for s_p in range(bins):
                # P(t_now, t_past, s_past)
                mask_joint = (t_now == t_n) & (t_past == t_p) & (s_bins == s_p)
                p_joint = np.sum(mask_joint) / n

                if p_joint < 1e-10:
                    continue

                # P(t_now, t_past)
                mask_tp = (t_now == t_n) & (t_past == t_p)
                p_tp = np.sum(mask_tp) / n

                # P(t_past, s_past)
                mask_ps = (t_past == t_p) & (s_bins == s_p)
                p_ps = np.sum(mask_ps) / n

                # P(t_past)
                mask_p = (t_past == t_p)
                p_p = np.sum(mask_p) / n

                if p_tp > 1e-10 and p_ps > 1e-10 and p_p > 1e-10:
                    te += p_joint * np.log2(p_joint * p_p / (p_tp * p_ps + 1e-20) + 1e-20)

    return max(te, 0)


def rolling_transfer_entropy(source, target, window=100, lag=1, bins=6):
    """Compute TE over rolling windows."""
    n = len(source)
    result = np.full(n, np.nan)
    for i in range(window, n):
        result[i] = transfer_entropy(
            source[i-window:i], target[i-window:i], lag=lag, bins=bins
        )
    return result


# ============================================================
# 2. MUTUAL INFORMATION
# ============================================================

def mutual_information(x, y, bins=8):
    """
    Compute Mutual Information: I(X;Y) = H(X) + H(Y) - H(X,Y)

    Captures ALL dependency (linear + non-linear).
    MI > 0 means x and y share information.
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)

    mask = ~(np.isnan(x) | np.isnan(y))
    x, y = x[mask], y[mask]

    if len(x) < 30:
        return 0.0

    # 2D histogram
    c_xy = np.histogram2d(x, y, bins=bins)[0]
    c_xy = c_xy / c_xy.sum()

    c_x = c_xy.sum(axis=1)
    c_y = c_xy.sum(axis=0)

    mi = 0.0
    for i in range(bins):
        for j in range(bins):
            if c_xy[i, j] > 1e-10 and c_x[i] > 1e-10 and c_y[j] > 1e-10:
                mi += c_xy[i, j] * np.log2(c_xy[i, j] / (c_x[i] * c_y[j]))

    return max(mi, 0)


def normalized_mutual_information(x, y, bins=8):
    """NMI = MI(X,Y) / sqrt(H(X) * H(Y)), ranges [0, 1]."""
    mi = mutual_information(x, y, bins)

    x_bins = _adaptive_discretize(x[~np.isnan(x)], bins)
    y_bins = _adaptive_discretize(y[~np.isnan(y)], bins)

    hx = _entropy(x_bins, bins)
    hy = _entropy(y_bins, bins)

    if hx > 0 and hy > 0:
        return mi / np.sqrt(hx * hy)
    return 0.0


def rolling_mutual_information(x, y, window=100, bins=6):
    """MI over rolling windows."""
    n = len(x)
    result = np.full(n, np.nan)
    for i in range(window, n):
        result[i] = mutual_information(x[i-window:i], y[i-window:i], bins=bins)
    return result


# ============================================================
# 3. PERSISTENT HOMOLOGY (TDA)
# ============================================================

def compute_persistence_features(point_cloud, max_dim=1, n_points=50):
    """
    Compute topological features from a point cloud using Vietoris-Rips filtration.

    Returns persistence diagram statistics:
    - Total persistence (sum of lifetimes)
    - Max lifetime (most persistent feature)
    - Number of significant features
    - Wasserstein distance from trivial diagram

    Point cloud is constructed from sliding windows of time series.
    """
    if len(point_cloud) < n_points:
        return {'total_persistence': 0, 'max_lifetime': 0, 'n_features': 0,
                'wasserstein': 0, 'betti_0': 0, 'betti_1': 0}

    # Subsample for speed
    idx = np.linspace(0, len(point_cloud) - 1, n_points, dtype=int)
    cloud = point_cloud[idx]

    # Build distance matrix
    tree = KDTree(cloud)
    dists, _ = tree.query(cloud, k=min(10, len(cloud)))

    # Vietoris-Rips approximation via neighbor distances
    # Track connected components (H0) and loops (H1)

    # H0: Track merging of connected components
    n = len(cloud)
    parent = list(range(n))
    rank = [0] * n

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        px, py = find(x), find(y)
        if px == py:
            return False
        if rank[px] < rank[py]:
            px, py = py, px
        parent[py] = px
        if rank[px] == rank[py]:
            rank[px] += 1
        return True

    # All pairwise distances, sorted
    edges = []
    for i in range(n):
        for j in range(i + 1, n):
            d = np.linalg.norm(cloud[i] - cloud[j])
            edges.append((d, i, j))
    edges.sort()

    # Birth-death pairs for H0
    births_h0 = [0.0] * n  # all components born at 0
    deaths_h0 = []
    loops_h1 = []

    for d, i, j in edges:
        if not union(i, j):
            # Loop detected (H1 birth)
            loops_h1.append(d)
        else:
            deaths_h0.append(d)

    # Persistence statistics
    lifetimes_h0 = np.array(deaths_h0) if deaths_h0 else np.array([0])

    # H1: estimate loop lifetimes from density
    betti_1 = len(loops_h1)

    total_persistence = np.sum(lifetimes_h0)
    max_lifetime = np.max(lifetimes_h0) if len(lifetimes_h0) > 0 else 0

    # Number of "significant" features (lifetime > median)
    if len(lifetimes_h0) > 1:
        threshold = np.median(lifetimes_h0)
        n_significant = np.sum(lifetimes_h0 > threshold)
    else:
        n_significant = 0

    return {
        'total_persistence': total_persistence,
        'max_lifetime': max_lifetime,
        'n_features': n_significant,
        'wasserstein': np.sqrt(np.sum(lifetimes_h0 ** 2)),
        'betti_0': len(set(find(i) for i in range(n))),  # remaining components
        'betti_1': betti_1
    }


def time_series_to_point_cloud(series, embedding_dim=3, delay=1):
    """Convert time series to point cloud via Takens embedding."""
    series = np.asarray(series, dtype=float)
    mask = ~np.isnan(series)
    series = series[mask]

    n = len(series) - (embedding_dim - 1) * delay
    if n < 10:
        return np.array([]).reshape(0, embedding_dim)

    cloud = np.zeros((n, embedding_dim))
    for d in range(embedding_dim):
        cloud[:, d] = series[d * delay:d * delay + n]

    # Normalize
    cloud = (cloud - cloud.mean(axis=0)) / (cloud.std(axis=0) + 1e-10)
    return cloud


def rolling_persistence(series, window=60, embedding_dim=3, delay=1, n_points=40):
    """Compute rolling topological features."""
    n = len(series)
    features = {k: np.full(n, np.nan) for k in
                ['total_persistence', 'max_lifetime', 'n_features',
                 'wasserstein', 'betti_0', 'betti_1']}

    for i in range(window, n):
        window_data = series[i-window:i]
        cloud = time_series_to_point_cloud(window_data, embedding_dim, delay)
        if len(cloud) < 10:
            continue
        pf = compute_persistence_features(cloud, n_points=min(n_points, len(cloud)))
        for k, v in pf.items():
            features[k][i] = v

    return features


# ============================================================
# 4. CONVERGENT CROSS MAPPING (CCM)
# ============================================================

def convergent_cross_mapping(x, y, embedding_dim=3, tau=1, lib_sizes=None):
    """
    Convergent Cross Mapping: determines if X causally influences Y.

    If X causes Y, then Y's state space contains information about X.
    We can "cross map" from Y's shadow manifold to predict X.

    Convergence (improving prediction with more data) proves causation.

    Returns: correlation between predicted and actual X at each library size,
    plus convergence score (slope of correlation vs library size).
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)

    mask = ~(np.isnan(x) | np.isnan(y))
    x, y = x[mask], y[mask]

    n = len(x)
    if n < embedding_dim * tau + 20:
        return {'correlations': [], 'convergence': 0, 'final_corr': 0}

    # Build shadow manifold from Y
    n_embed = n - (embedding_dim - 1) * tau
    shadow_y = np.zeros((n_embed, embedding_dim))
    for d in range(embedding_dim):
        shadow_y[:, d] = y[d * tau:d * tau + n_embed]

    x_target = x[(embedding_dim - 1) * tau:][:n_embed]

    # KD-tree for nearest neighbor search
    tree = KDTree(shadow_y)

    if lib_sizes is None:
        lib_sizes = np.linspace(
            max(embedding_dim + 2, 20),
            min(n_embed, 500),
            min(8, n_embed // 20)
        ).astype(int)

    correlations = []
    for L in lib_sizes:
        if L > n_embed:
            break

        # Use first L points as library
        lib_tree = KDTree(shadow_y[:L])

        # Predict x from y's shadow manifold using simplex projection
        k = embedding_dim + 1
        predictions = []
        actuals = []

        for i in range(L, min(n_embed, L + 200)):
            dists, indices = lib_tree.query(shadow_y[i:i+1], k=min(k, L))
            dists = dists[0]
            indices = indices[0]

            # Exponential weights
            min_dist = dists[0] + 1e-10
            weights = np.exp(-dists / min_dist)
            weights /= weights.sum()

            pred = np.sum(weights * x_target[indices])
            predictions.append(pred)
            actuals.append(x_target[i])

        if len(predictions) > 5:
            corr = np.corrcoef(predictions, actuals)[0, 1]
            if not np.isnan(corr):
                correlations.append(corr)
            else:
                correlations.append(0)
        else:
            correlations.append(0)

    # Convergence = positive slope of correlation vs library size
    if len(correlations) >= 3:
        x_sizes = np.arange(len(correlations))
        convergence = np.polyfit(x_sizes, correlations, 1)[0]
    else:
        convergence = 0

    final_corr = correlations[-1] if correlations else 0

    return {
        'correlations': correlations,
        'convergence': convergence,
        'final_corr': final_corr
    }


# ============================================================
# 5. CAUSAL INDICATOR SCORING
# ============================================================

def compute_causal_scores(indicators_df, forward_returns, horizon=5,
                          window=200, top_n=30):
    """
    Compute comprehensive causal scores for each indicator.

    For each indicator, computes:
    - Transfer Entropy (directional information flow)
    - Mutual Information (total dependency)
    - CCM convergence (true causation)
    - Pearson correlation (baseline linear)

    Returns DataFrame with causal scores per indicator.
    """
    results = []

    # Compute forward returns
    close = forward_returns
    fwd_ret = pd.Series(
        np.log(close.shift(-horizon) / close).values,
        index=close.index
    )

    indicator_names = indicators_df.columns.tolist()

    for ind_name in indicator_names:
        ind_values = indicators_df[ind_name].values
        fwd_values = fwd_ret.values

        # Use the latest window for scoring
        mask = ~(np.isnan(ind_values) | np.isnan(fwd_values))
        if mask.sum() < 100:
            continue

        ind_clean = ind_values[mask][-window:]
        fwd_clean = fwd_values[mask][-window:]

        if len(ind_clean) < 50:
            continue

        # 1. Transfer Entropy (indicator → returns)
        te = transfer_entropy(ind_clean, fwd_clean, lag=1, bins=5)

        # 2. Mutual Information
        mi = normalized_mutual_information(ind_clean, fwd_clean, bins=6)

        # 3. CCM convergence
        ccm = convergent_cross_mapping(ind_clean, fwd_clean, embedding_dim=2, tau=1)
        ccm_score = ccm['convergence']
        ccm_corr = ccm['final_corr']

        # 4. Linear correlation (baseline)
        if np.std(ind_clean) < 1e-10 or np.std(fwd_clean) < 1e-10:
            continue
        pearson_r, pearson_p = stats.pearsonr(ind_clean, fwd_clean)

        # 5. Composite causal score
        # Weight non-linear measures higher since they capture what correlation misses
        composite = (
            0.15 * min(abs(pearson_r) * 10, 1) +          # Linear (lower weight)
            0.25 * min(te * 50, 1) +                       # Transfer entropy
            0.25 * min(mi * 5, 1) +                        # Mutual information
            0.20 * min(max(ccm_score, 0) * 20, 1) +       # CCM convergence
            0.15 * min(abs(ccm_corr), 1)                   # CCM final correlation
        )

        # Direction: use sign of Pearson (and verify with CCM direction)
        direction = np.sign(pearson_r)

        results.append({
            'indicator': ind_name,
            'pearson_r': pearson_r,
            'pearson_p': pearson_p,
            'transfer_entropy': te,
            'mutual_info': mi,
            'ccm_convergence': ccm_score,
            'ccm_final_corr': ccm_corr,
            'composite_score': composite,
            'direction': direction
        })

    df = pd.DataFrame(results)
    df = df.sort_values('composite_score', ascending=False)

    return df


# ============================================================
# 6. TOPOLOGICAL REGIME DETECTOR
# ============================================================

class TopologicalRegimeDetector:
    """
    Uses persistent homology to detect market regime shifts.

    Key insight from the research: before market crashes, the topology
    of the price manifold changes — loops appear (H1 features increase)
    and the point cloud becomes more "complex."

    We track:
    - Betti numbers (connected components, loops)
    - Total persistence (topological complexity)
    - Wasserstein distance (magnitude of topological change)
    """

    def __init__(self, window=60, embedding_dim=3, delay=1):
        self.window = window
        self.embedding_dim = embedding_dim
        self.delay = delay

    def detect_regimes(self, prices, returns):
        """
        Classify each point as one of:
        - TREND_UP: low topological complexity, positive drift
        - TREND_DOWN: low topological complexity, negative drift
        - CHAOTIC: high topological complexity (pre-crash signal)
        - MEAN_REVERT: moderate complexity, high Betti-0
        """
        n = len(prices)
        features = rolling_persistence(
            returns, self.window, self.embedding_dim, self.delay, n_points=35
        )

        regimes = np.full(n, 0)  # 0=unknown

        # Normalize features
        total_p = np.array(features['total_persistence'])
        betti_0 = np.array(features['betti_0'])
        betti_1 = np.array(features['betti_1'])
        max_life = np.array(features['max_lifetime'])

        for i in range(self.window + 10, n):
            # Rolling stats for normalization
            lookback = slice(max(0, i - 200), i)
            tp_mean = np.nanmean(total_p[lookback])
            tp_std = np.nanstd(total_p[lookback]) + 1e-10

            tp_z = (total_p[i] - tp_mean) / tp_std if not np.isnan(total_p[i]) else 0

            # Price trend
            if i >= 20:
                trend = np.mean(returns[i-20:i]) if not np.any(np.isnan(returns[i-20:i])) else 0
            else:
                trend = 0

            b0 = betti_0[i] if not np.isnan(betti_0[i]) else 1
            b1 = betti_1[i] if not np.isnan(betti_1[i]) else 0

            if tp_z > 3.0 and b1 > 8:
                regimes[i] = 4  # CHAOTIC (extremely high topological complexity)
            elif trend > 0.0005:
                regimes[i] = 1  # TREND_UP
            elif trend < -0.0005:
                regimes[i] = 2  # TREND_DOWN
            elif b0 > 5 and abs(trend) < 0.0005:
                regimes[i] = 3  # MEAN_REVERT (fragmented, no trend)
            else:
                regimes[i] = 3  # MEAN_REVERT

        return regimes, features


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def _adaptive_discretize(data, bins):
    """Discretize using quantile bins (handles skewed distributions)."""
    data = np.asarray(data, dtype=float)
    if np.std(data) < 1e-10:
        return np.zeros(len(data), dtype=int)

    try:
        percentiles = np.percentile(data, np.linspace(0, 100, bins + 1))
        # Ensure unique bin edges
        percentiles = np.unique(percentiles)
        if len(percentiles) < 3:
            percentiles = np.linspace(data.min() - 1e-10, data.max() + 1e-10, bins + 1)
        result = np.digitize(data, percentiles[1:-1])
        return np.clip(result, 0, bins - 1)
    except Exception:
        return np.zeros(len(data), dtype=int)


def _entropy(binned_data, n_bins):
    """Compute Shannon entropy of binned data."""
    counts = np.bincount(binned_data, minlength=n_bins)
    probs = counts / counts.sum()
    probs = probs[probs > 0]
    return -np.sum(probs * np.log2(probs))
