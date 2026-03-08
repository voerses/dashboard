"""Convert feature names to numpy arrays from StrategyContext data.

Replicates computation logic from tools/signal_discovery/features.py
for individual features (not the full 300-feature generator).
"""

import re
import numpy as np
import pandas as pd
from typing import Optional


def _zscore(arr, window=100):
    """Rolling z-score. Returns np array with NaNs for warmup."""
    s = pd.Series(arr)
    mu = s.rolling(window, min_periods=window).mean()
    sigma = s.rolling(window, min_periods=window).std()
    return ((s - mu) / sigma.clip(lower=1e-10)).values


def _momentum(arr, window):
    """Simple momentum: arr[t] - arr[t-window]."""
    out = np.full_like(arr, np.nan)
    out[window:] = arr[window:] - arr[:-window]
    return out


def _align_higher_to_lower(higher_idx, higher_vals, lower_idx):
    """Forward-fill higher timeframe values to lower timeframe index."""
    s = pd.Series(higher_vals, index=higher_idx)
    return s.reindex(lower_idx, method='ffill').values.copy()


# Regex patterns for feature name parsing
_CROSS_TF_RE = re.compile(r'^(.+)_1h_vs_4h$')
_REGIME_COND_RE = re.compile(r'^(.+)_in_(CRISIS|QUIET|UPTREND|RANGE|DOWNTREND)$')
_MOMENTUM_RE = re.compile(r'^(.+)_mom_(\d+)h$')
_ZSCORE_RE = re.compile(r'^(.+)_zscore_(\d+)h$')
_ACCEL_RE = re.compile(r'^(.+)_accel_(\d+)h$')
_PRODUCT_RE = re.compile(r'^(.+)_product_(.+)$')
_RATIO_RE = re.compile(r'^(.+)_ratio_(.+)$')
_DIFF_RE = re.compile(r'^(.+)_diff_(.+)$')

# Regime name to int mapping
_REGIME_MAP = {
    'CRISIS': 0, 'QUIET': 1, 'UPTREND': 2, 'RANGE': 3, 'DOWNTREND': 4,
}


def compute_feature(ctx, feature_name: str) -> Optional[np.ndarray]:
    """Compute a single feature from StrategyContext.

    Parses the feature name format to dispatch to the correct computation.
    Returns None if the feature cannot be computed.

    Supported formats:
        - ret_1_1h_vs_4h        -> cross-TF divergence
        - ema_10_in_UPTREND     -> regime-conditional
        - rsi_product_vol_20    -> interaction (product)
        - rsi_ratio_atr         -> interaction (ratio)
        - rsi_diff_vol_20       -> interaction (diff)
        - adx_zscore_48h        -> momentum z-score
        - rsi_mom_24h           -> momentum
        - rsi_accel_24h         -> acceleration
    """
    # Cross-timeframe divergence: indicator_1h_vs_4h
    m = _CROSS_TF_RE.match(feature_name)
    if m:
        ind_name = m.group(1)
        return _compute_cross_tf(ctx, ind_name)

    # Regime-conditional: indicator_in_REGIME
    m = _REGIME_COND_RE.match(feature_name)
    if m:
        ind_name = m.group(1)
        regime_name = m.group(2)
        return _compute_regime_cond(ctx, ind_name, regime_name)

    # Momentum: indicator_mom_Nh
    m = _MOMENTUM_RE.match(feature_name)
    if m:
        ind_name = m.group(1)
        window = int(m.group(2))
        return _compute_momentum(ctx, ind_name, window)

    # Z-score: indicator_zscore_Nh
    m = _ZSCORE_RE.match(feature_name)
    if m:
        ind_name = m.group(1)
        window = int(m.group(2))
        return _compute_zscore(ctx, ind_name, window)

    # Acceleration: indicator_accel_Nh
    m = _ACCEL_RE.match(feature_name)
    if m:
        ind_name = m.group(1)
        window = int(m.group(2))
        return _compute_accel(ctx, ind_name, window)

    # Interaction product: ind_a_product_ind_b
    m = _PRODUCT_RE.match(feature_name)
    if m:
        ind_a = m.group(1)
        ind_b = m.group(2)
        return _compute_interaction(ctx, ind_a, ind_b, 'product')

    # Interaction ratio: ind_a_ratio_ind_b
    m = _RATIO_RE.match(feature_name)
    if m:
        ind_a = m.group(1)
        ind_b = m.group(2)
        return _compute_interaction(ctx, ind_a, ind_b, 'ratio')

    # Interaction diff: ind_a_diff_ind_b
    m = _DIFF_RE.match(feature_name)
    if m:
        ind_a = m.group(1)
        ind_b = m.group(2)
        return _compute_interaction(ctx, ind_a, ind_b, 'diff')

    # Direct indicator (fallback)
    arr = ctx.ind_1h.get(feature_name)
    if arr is not None:
        return _zscore(arr)

    return None


def _get_indicator(ctx, name: str) -> Optional[np.ndarray]:
    """Get indicator array from context, checking 1h first, then 4h."""
    arr = ctx.ind_1h.get(name)
    if arr is not None:
        return arr
    return None


def _compute_cross_tf(ctx, ind_name: str) -> Optional[np.ndarray]:
    """Cross-timeframe divergence: z_score(1h) - z_score(4h aligned)."""
    val_1h = ctx.ind_1h.get(ind_name)
    val_4h = ctx.ind_4h.get(ind_name)
    if val_1h is None or val_4h is None:
        return None

    aligned_4h = _align_higher_to_lower(ctx.idx_4h, val_4h, ctx.idx_1h)
    z1 = _zscore(val_1h)
    z4 = _zscore(aligned_4h)
    return z1 - z4


def _compute_regime_cond(ctx, ind_name: str, regime_name: str) -> Optional[np.ndarray]:
    """Regime-conditional: indicator value masked by regime label."""
    arr = ctx.ind_1h.get(ind_name)
    if arr is None:
        return None

    regime_id = _REGIME_MAP.get(regime_name)
    if regime_id is None:
        return None

    n = len(arr)
    masked = np.full(n, np.nan)
    mask = ctx.regime_1h == regime_id
    masked[mask] = arr[mask]
    return masked


def _compute_momentum(ctx, ind_name: str, window: int) -> Optional[np.ndarray]:
    """Momentum: indicator[t] - indicator[t-window]."""
    arr = ctx.ind_1h.get(ind_name)
    if arr is None:
        return None
    return _momentum(arr, window)


def _compute_zscore(ctx, ind_name: str, window: int) -> Optional[np.ndarray]:
    """Rolling z-score of indicator."""
    arr = ctx.ind_1h.get(ind_name)
    if arr is None:
        return None
    return _zscore(arr, window)


def _compute_accel(ctx, ind_name: str, window: int) -> Optional[np.ndarray]:
    """Acceleration: momentum of momentum."""
    arr = ctx.ind_1h.get(ind_name)
    if arr is None:
        return None
    mom = _momentum(arr, window)
    return _momentum(mom, window)


def _compute_interaction(ctx, ind_a: str, ind_b: str,
                          interaction_type: str) -> Optional[np.ndarray]:
    """Interaction feature between two indicators."""
    a = ctx.ind_1h.get(ind_a)
    b = ctx.ind_1h.get(ind_b)
    if a is None or b is None:
        return None

    if interaction_type == 'product':
        za = _zscore(a)
        zb = _zscore(b)
        return za * zb
    elif interaction_type == 'ratio':
        return a / np.maximum(np.abs(b), 1e-10)
    elif interaction_type == 'diff':
        za = _zscore(a)
        zb = _zscore(b)
        return za - zb

    return None
