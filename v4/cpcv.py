"""
Combinatorial Purged Cross-Validation — V3 standalone copy.
============================================================

CPCV split generation + deflated Sharpe ratio.
Based on Lopez de Prado (2018) "Advances in Financial Machine Learning".
"""

import numpy as np
import pandas as pd
from itertools import combinations
from typing import List, Tuple
from math import log, sqrt, erfc


def generate_cpcv_splits(n_samples: int, n_groups: int = 6, n_test_groups: int = 2,
                         purge_pct: float = 0.01) -> List[Tuple[np.ndarray, np.ndarray]]:
    """
    Generate CPCV train/test splits with vectorized purge computation.

    For n_groups=6, n_test_groups=2: C(6,2) = 15 unique splits.
    Each split has a purged train set + contiguous test set.
    """
    group_size = n_samples // n_groups
    group_bounds = []
    for g in range(n_groups):
        start = g * group_size
        end = start + group_size if g < n_groups - 1 else n_samples
        group_bounds.append((start, end))

    splits = []
    purge_size = max(1, int(n_samples * purge_pct))

    for test_combo in combinations(range(n_groups), n_test_groups):
        test_set = set(test_combo)

        test_ranges = [group_bounds[g] for g in test_combo]
        test_idx = np.concatenate([np.arange(s, e) for s, e in test_ranges])

        train_ranges = [group_bounds[g] for g in range(n_groups) if g not in test_set]
        if not train_ranges:
            continue
        train_idx = np.concatenate([np.arange(s, e) for s, e in train_ranges])

        purge_mask = np.ones(len(train_idx), dtype=bool)
        for tg in test_combo:
            tg_start, tg_end = group_bounds[tg]
            purge_mask &= np.abs(train_idx - tg_start) >= purge_size
            purge_mask &= np.abs(train_idx - (tg_end - 1)) >= purge_size

        train_idx_purged = train_idx[purge_mask]
        splits.append((train_idx_purged, test_idx))

    return splits


def deflated_sharpe(returns: list) -> float:
    """Deflated Sharpe ratio — DEPRECATED.

    This implementation is incorrect (uses len(returns) as n_trials,
    omits skewness term). Use v4.metrics.deflated_sharpe_ratio() instead.

    Kept for backward compatibility. Issues a DeprecationWarning and
    delegates to the correct implementation.
    """
    import warnings
    warnings.warn(
        "deflated_sharpe() is deprecated and incorrect. "
        "Use v4.metrics.deflated_sharpe_ratio(sharpe, n_obs, skewness, kurtosis, n_trials) instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    if len(returns) < 3:
        return 0.0
    from v4.metrics import deflated_sharpe_ratio
    arr = np.array(returns)
    sr = float(np.mean(arr) / max(np.std(arr), 1e-10))
    n_obs = len(arr)
    skewness = float(pd.Series(arr).skew())
    kurtosis = float(pd.Series(arr).kurtosis() + 3)  # pandas kurtosis is excess; formula needs raw
    # Legacy behavior: use n_obs as n_trials (the original bug — preserved for compat)
    return deflated_sharpe_ratio(sr, n_obs, skewness, kurtosis, n_trials=n_obs)
