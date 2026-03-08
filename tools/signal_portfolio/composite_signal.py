"""IC-weighted multi-signal combination with decorrelation guard."""

import numpy as np
from scipy.stats import spearmanr
from typing import Dict, List, Tuple

from .catalog_loader import SignalInfo
from .signal_converter import compute_feature, _zscore


def decorrelate_signals(signals: List[SignalInfo],
                         ctx,
                         max_corr: float = 0.7) -> List[SignalInfo]:
    """Remove redundant signals: keep higher |IC| when pairwise |corr| > max_corr.

    Greedy approach: iterate by descending |IC|, skip signals too correlated
    with already-selected ones.
    """
    if len(signals) <= 1:
        return signals

    # Sort by |IC| descending
    ranked = sorted(signals, key=lambda s: abs(s.mean_ic), reverse=True)

    selected = []
    selected_arrays = []

    for sig in ranked:
        arr = compute_feature(ctx, sig.feature)
        if arr is None:
            continue

        # Check correlation with already-selected
        too_correlated = False
        for sel_arr in selected_arrays:
            mask = np.isfinite(arr) & np.isfinite(sel_arr)
            if mask.sum() < 30:
                continue
            c, _ = spearmanr(arr[mask], sel_arr[mask])
            if np.isfinite(c) and abs(c) > max_corr:
                too_correlated = True
                break

        if not too_correlated:
            selected.append(sig)
            selected_arrays.append(arr)

    return selected


def build_composite_signal(signals: List[SignalInfo],
                            ctx,
                            max_signals: int = 5) -> Tuple[np.ndarray, List[SignalInfo]]:
    """Build IC-weighted composite signal from multiple features.

    composite = sum(sign(ic_i) * |ic_i| * zscore(feature_i)) / sum(|ic_i|)

    sign(ic) flips features so all point in return-prediction direction.
    |ic| weights stronger signals more heavily.

    Args:
        signals: List of SignalInfo objects (already decorrelated)
        ctx: StrategyContext
        max_signals: Maximum number of signals to combine

    Returns:
        (composite_array, used_signals): the composite z-score array and signals used
    """
    # Limit to max_signals
    ranked = sorted(signals, key=lambda s: abs(s.mean_ic), reverse=True)[:max_signals]

    n = len(ctx.ind_1h['close'])
    weighted_sum = np.zeros(n)
    total_weight = 0.0
    used = []

    for sig in ranked:
        arr = compute_feature(ctx, sig.feature)
        if arr is None:
            continue

        # Z-score the feature for standardization
        z = _zscore(arr)

        # Replace NaN with 0 for summation (neutral contribution)
        z_clean = np.nan_to_num(z, nan=0.0)

        ic = sig.mean_ic
        weight = abs(ic)
        sign = np.sign(ic)

        weighted_sum += sign * weight * z_clean
        total_weight += weight
        used.append(sig)

    if total_weight > 0:
        composite = weighted_sum / total_weight
    else:
        composite = np.zeros(n)

    return composite, used
