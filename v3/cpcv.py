"""
Combinatorial Purged Cross-Validation — V3 standalone copy.
============================================================

Copied from v2/cpcv.py (split generation + deflated Sharpe only).
V2 is untouched.

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
    """Deflated Sharpe ratio (Bailey & de Prado, 2014)."""
    if len(returns) < 3:
        return 0.0
    sr = np.mean(returns) / max(np.std(returns), 1e-10)
    n = len(returns)
    kurt = float(pd.Series(returns).kurtosis())
    try:
        e_max_sr = sqrt(2 * log(n)) * (1 - log(log(n)) / (2 * log(n)))
        psr = 0.5 * erfc(-(sr - e_max_sr * 0.5) / sqrt(max(1 + 0.5 * kurt, 0.01)) * sqrt(max(n - 1, 1)))
    except (ValueError, ZeroDivisionError):
        psr = 0.0
    return psr
