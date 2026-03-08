"""Cluster tokens by signal profile similarity."""

import numpy as np
from sklearn.cluster import AgglomerativeClustering
from typing import Dict, List, Tuple

from .catalog_loader import TokenSignalProfile
from .config import SignalPortfolioConfig


def build_ic_matrix(profiles: Dict[str, TokenSignalProfile]) -> Tuple[np.ndarray, List[str]]:
    """Build IC matrix from token profiles.

    Returns:
        (ic_matrix, token_names): matrix shape (n_tokens, n_features), ordered token list
    """
    tokens = sorted([t for t, p in profiles.items() if p.ic_vector is not None])
    if not tokens:
        return np.empty((0, 0)), []

    matrix = np.vstack([profiles[t].ic_vector for t in tokens])
    return matrix, tokens


def compute_token_distance(ic_matrix: np.ndarray) -> np.ndarray:
    """Compute pairwise distance: 1 - |corr(ic_vector_i, ic_vector_j)|.

    Uses Pearson correlation on IC vectors.
    """
    n = ic_matrix.shape[0]
    if n < 2:
        return np.zeros((n, n))

    # Compute correlation matrix
    # Handle constant rows (all zeros) by setting their correlation to 0
    corr = np.corrcoef(ic_matrix)
    corr = np.nan_to_num(corr, nan=0.0)

    # Distance = 1 - |correlation|
    distance = 1.0 - np.abs(corr)
    np.fill_diagonal(distance, 0.0)

    # Clip to valid range
    distance = np.clip(distance, 0.0, 1.0)

    return distance


def cluster_tokens(profiles: Dict[str, TokenSignalProfile],
                    config: SignalPortfolioConfig) -> Dict[int, List[str]]:
    """Cluster tokens by IC profile similarity.

    Uses agglomerative clustering with correlation distance.
    Tunes distance_threshold to produce min_clusters to max_clusters groups.

    Returns:
        Dict mapping cluster_id to list of token names.
    """
    ic_matrix, token_names = build_ic_matrix(profiles)

    if len(token_names) < 2:
        return {0: token_names}

    distance_matrix = compute_token_distance(ic_matrix)

    # Binary search for distance_threshold that yields target cluster count
    best_labels = None
    best_n_clusters = 0
    target_range = (config.min_clusters, config.max_clusters)

    # Try a range of thresholds
    for threshold in np.arange(0.1, 1.0, 0.05):
        clustering = AgglomerativeClustering(
            n_clusters=None,
            metric='precomputed',
            linkage='average',
            distance_threshold=threshold,
        )
        labels = clustering.fit_predict(distance_matrix)
        n_clusters = labels.max() + 1

        if target_range[0] <= n_clusters <= target_range[1]:
            best_labels = labels
            best_n_clusters = n_clusters
            break

        # Keep the best we've seen so far
        if best_labels is None or abs(n_clusters - 4) < abs(best_n_clusters - 4):
            best_labels = labels
            best_n_clusters = n_clusters

    # If we couldn't hit the target range, use fixed n_clusters
    if best_labels is None or not (target_range[0] <= best_n_clusters <= target_range[1]):
        target_n = min(config.max_clusters, len(token_names))
        clustering = AgglomerativeClustering(
            n_clusters=target_n,
            metric='precomputed',
            linkage='average',
        )
        best_labels = clustering.fit_predict(distance_matrix)

    # Build cluster dict
    groups = {}
    for i, token in enumerate(token_names):
        cluster_id = int(best_labels[i])
        if cluster_id not in groups:
            groups[cluster_id] = []
        groups[cluster_id].append(token)

    return groups


def characterize_group(group_tokens: List[str],
                        profiles: Dict[str, TokenSignalProfile],
                        common_features: List[str]) -> Dict:
    """Characterize a cluster by its dominant signals.

    Returns dict with group info: dominant features, mean IC vector, etc.
    """
    # Collect IC vectors
    vectors = []
    all_signals = []
    for token in group_tokens:
        profile = profiles.get(token)
        if profile and profile.ic_vector is not None:
            vectors.append(profile.ic_vector)
        if profile:
            all_signals.extend(profile.filtered_signals)

    if not vectors:
        return {'tokens': group_tokens, 'dominant_signals': [], 'mean_ic': np.array([])}

    mean_ic = np.mean(vectors, axis=0)

    # Find dominant features (highest |mean IC| across group)
    feature_ics = sorted(
        zip(common_features, mean_ic),
        key=lambda x: abs(x[1]),
        reverse=True
    )
    dominant = [(f, float(ic)) for f, ic in feature_ics[:5] if abs(ic) > 0.01]

    return {
        'tokens': group_tokens,
        'n_tokens': len(group_tokens),
        'dominant_signals': dominant,
        'mean_ic': mean_ic,
    }


def print_cluster_summary(groups: Dict[int, List[str]],
                           profiles: Dict[str, TokenSignalProfile],
                           common_features: List[str]):
    """Print human-readable cluster summary."""
    print(f"\n{'='*60}")
    print(f"Token Clustering: {len(groups)} groups")
    print(f"{'='*60}")

    for gid in sorted(groups.keys()):
        tokens = groups[gid]
        info = characterize_group(tokens, profiles, common_features)

        print(f"\nGroup {gid} ({info['n_tokens']} tokens):")
        print(f"  Tokens: {', '.join(sorted(tokens))}")
        if info['dominant_signals']:
            print(f"  Dominant signals:")
            for feat, ic in info['dominant_signals']:
                print(f"    {feat}: IC={ic:+.4f}")
