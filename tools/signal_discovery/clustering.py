"""Signal deduplication via correlation clustering."""

import numpy as np
from scipy.stats import spearmanr
from sklearn.cluster import AgglomerativeClustering

from .config import REGIME_NAMES


def compute_feature_correlation_matrix(features, significant_names):
    """Compute pairwise Spearman correlation between significant features.

    Parameters
    ----------
    features : dict
        {feature_name: np.ndarray} of feature values.
    significant_names : list of str
        Feature names that passed significance tests.

    Returns
    -------
    (corr_matrix, ordered_names) : (np.ndarray, list of str)
        Correlation matrix and corresponding feature names.
    """
    names = [n for n in significant_names if n in features]
    if len(names) < 2:
        return np.eye(len(names)), names

    n = len(names)
    # Stack features, handle NaN by pairwise complete obs
    arrs = []
    for name in names:
        arrs.append(features[name])

    # Build correlation matrix
    corr = np.eye(n)
    for i in range(n):
        for j in range(i + 1, n):
            mask = np.isfinite(arrs[i]) & np.isfinite(arrs[j])
            if mask.sum() < 30:
                corr[i, j] = corr[j, i] = 0.0
                continue
            c, _ = spearmanr(arrs[i][mask], arrs[j][mask])
            if np.isfinite(c):
                corr[i, j] = corr[j, i] = c
            else:
                corr[i, j] = corr[j, i] = 0.0

    return corr, names


def cluster_and_select(corr_matrix, feature_names, results_by_feature, max_corr=0.7):
    """Cluster correlated features and select best representative per cluster.

    Parameters
    ----------
    corr_matrix : np.ndarray
        Pairwise correlation matrix.
    feature_names : list of str
        Feature names matching corr_matrix indices.
    results_by_feature : dict
        {feature_name: result_dict} with 'mean_ic' for ranking.
    max_corr : float
        Maximum correlation threshold for same cluster.

    Returns
    -------
    list of str : selected feature names (one per cluster).
    """
    n = len(feature_names)
    if n <= 1:
        return list(feature_names)

    # Distance = 1 - |correlation|
    distance_matrix = 1.0 - np.abs(corr_matrix)
    np.fill_diagonal(distance_matrix, 0)

    # Hierarchical clustering
    clustering = AgglomerativeClustering(
        n_clusters=None,
        metric='precomputed',
        linkage='average',
        distance_threshold=1.0 - max_corr,
    )
    labels = clustering.fit_predict(distance_matrix)

    # Select best feature per cluster (highest |mean_ic|)
    selected = []
    for cluster_id in range(labels.max() + 1):
        members = [feature_names[i] for i in range(n) if labels[i] == cluster_id]
        # Rank by |mean_ic| across all horizons
        best = max(members, key=lambda f: abs(
            results_by_feature.get(f, {}).get('mean_ic', 0)))
        selected.append(best)

    return selected


def select_regime_orthogonal(results, features, top_n=20, max_corr=0.7):
    """Select top non-redundant signals per regime.

    Parameters
    ----------
    results : list of dict
        Significant results from evaluator (with per_regime_ic).
    features : dict
        {feature_name: np.ndarray} of feature values.
    top_n : int
        Max signals per regime.
    max_corr : float
        Correlation dedup threshold.

    Returns
    -------
    dict : {regime_name: list of (feature_name, regime_ic)} — top signals per regime.
    """
    regime_signals = {}

    for regime_name in REGIME_NAMES.values():
        # Collect features with IC in this regime
        candidates = []
        for r in results:
            ric = r.get('per_regime_ic', {}).get(regime_name)
            if ric is not None and abs(ric) >= 0.02:
                candidates.append((r['feature'], ric, r))

        if not candidates:
            regime_signals[regime_name] = []
            continue

        # Sort by |regime IC|
        candidates.sort(key=lambda x: abs(x[1]), reverse=True)

        # Greedy dedup: add features that aren't too correlated with already-selected
        selected = []
        selected_arrs = []

        for feat_name, ric, result in candidates:
            if len(selected) >= top_n:
                break

            feat_arr = features.get(feat_name)
            if feat_arr is None:
                continue

            # Check correlation with already selected
            too_correlated = False
            for sel_arr in selected_arrs:
                mask = np.isfinite(feat_arr) & np.isfinite(sel_arr)
                if mask.sum() < 30:
                    continue
                c, _ = spearmanr(feat_arr[mask], sel_arr[mask])
                if np.isfinite(c) and abs(c) > max_corr:
                    too_correlated = True
                    break

            if not too_correlated:
                selected.append((feat_name, float(ric)))
                selected_arrs.append(feat_arr)

        regime_signals[regime_name] = selected

    return regime_signals
