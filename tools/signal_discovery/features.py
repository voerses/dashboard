"""Feature generation — indicator interactions, momentum, cross-TF, regime-conditional."""

import numpy as np
import pandas as pd

from .config import (
    BASE_INDICATORS, INTERACTION_PAIRS, MOMENTUM_INDICATORS,
    MOMENTUM_WINDOWS, CROSS_TF_INDICATORS, REGIME_COND_INDICATORS,
    REGIME_NAMES, CRISIS, QUIET, UPTREND, RANGE, DOWNTREND,
)


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


def _acceleration(arr, window):
    """Acceleration: momentum of momentum."""
    mom = _momentum(arr, window)
    return _momentum(mom, window)


def generate_interaction_features(ind_1h):
    """Generate pairwise interaction features from 1h indicators.

    Parameters
    ----------
    ind_1h : dict
        Output of compute_indicators_fast on 1h data.

    Returns
    -------
    dict : {feature_name: np.ndarray}
    """
    features = {}
    n = len(ind_1h['close'])

    for ind_a, ind_b, interaction_types in INTERACTION_PAIRS:
        a = ind_1h.get(ind_a)
        b = ind_1h.get(ind_b)
        if a is None or b is None:
            continue

        for itype in interaction_types:
            name = f'{ind_a}_{itype}_{ind_b}'
            if itype == 'product':
                # Z-score both before multiplying to avoid scale issues
                za = _zscore(a)
                zb = _zscore(b)
                features[name] = za * zb
            elif itype == 'ratio':
                features[name] = a / np.maximum(np.abs(b), 1e-10)
            elif itype == 'diff':
                za = _zscore(a)
                zb = _zscore(b)
                features[name] = za - zb

    return features


def generate_momentum_features(ind_1h):
    """Generate momentum and acceleration features for key indicators.

    Parameters
    ----------
    ind_1h : dict
        Output of compute_indicators_fast on 1h data.

    Returns
    -------
    dict : {feature_name: np.ndarray}
    """
    features = {}

    for ind_name in MOMENTUM_INDICATORS:
        arr = ind_1h.get(ind_name)
        if arr is None:
            continue

        for window in MOMENTUM_WINDOWS:
            features[f'{ind_name}_mom_{window}h'] = _momentum(arr, window)
            features[f'{ind_name}_zscore_{window}h'] = _zscore(arr, window)

        # Acceleration at 24h only
        features[f'{ind_name}_accel_24h'] = _acceleration(arr, 24)

    return features


def generate_cross_tf_features(ind_1h, ind_4h, idx_1h, idx_4h):
    """Generate cross-timeframe divergence features.

    Compares 1h indicator value to its 4h counterpart (forward-filled to 1h).

    Parameters
    ----------
    ind_1h : dict
        1h indicators from compute_indicators_fast.
    ind_4h : dict
        4h indicators from compute_indicators_fast.
    idx_1h : pd.DatetimeIndex
        1h bar timestamps.
    idx_4h : pd.DatetimeIndex
        4h bar timestamps.

    Returns
    -------
    dict : {feature_name: np.ndarray}
    """
    # Lazy import to avoid circular dependency at module load
    import importlib.util
    import os
    _v3_dir = os.path.join(os.path.dirname(__file__), '..', '..', 'v3')
    spec = importlib.util.spec_from_file_location(
        'v3_engine', os.path.join(_v3_dir, 'engine.py'))
    _engine = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(_engine)
    _align = _engine._align_higher_to_lower

    features = {}

    for ind_name in CROSS_TF_INDICATORS:
        val_1h = ind_1h.get(ind_name)
        val_4h = ind_4h.get(ind_name)
        if val_1h is None or val_4h is None:
            continue

        # Forward-fill 4h values to 1h grid
        aligned_4h = _align(idx_4h, val_4h, idx_1h)

        # Divergence: z-scored 1h minus z-scored 4h-on-1h-grid
        z1 = _zscore(val_1h)
        z4 = _zscore(aligned_4h)
        features[f'{ind_name}_1h_vs_4h'] = z1 - z4

    return features


def generate_regime_features(ind_1h, regime_1h):
    """Generate regime-conditional and regime-transition features.

    Parameters
    ----------
    ind_1h : dict
        1h indicators from compute_indicators_fast.
    regime_1h : np.ndarray
        Regime labels on 1h grid (0-4), from detect_daily_regime
        aligned to 1h via _align_higher_to_lower.

    Returns
    -------
    dict : {feature_name: np.ndarray}
    """
    n = len(regime_1h)
    features = {}

    # Regime-conditional: indicator value masked by regime
    for ind_name in REGIME_COND_INDICATORS:
        arr = ind_1h.get(ind_name)
        if arr is None:
            continue

        for regime_id, regime_name in REGIME_NAMES.items():
            fname = f'{ind_name}_in_{regime_name}'
            masked = np.full(n, np.nan)
            mask = regime_1h == regime_id
            masked[mask] = arr[mask]
            features[fname] = masked

    # Regime transition features
    regime_changes = np.zeros(n, dtype=np.int8)
    regime_changes[1:] = (regime_1h[1:] != regime_1h[:-1]).astype(np.int8)

    for regime_id, regime_name in REGIME_NAMES.items():
        # Just entered this regime
        entered = np.zeros(n, dtype=np.int8)
        mask = (regime_1h == regime_id) & (regime_changes == 1)
        entered[mask] = 1
        features[f'just_entered_{regime_name}'] = entered.astype(np.float64)

    # Bars in current regime (counter resets on transition)
    bars_in_regime = np.zeros(n, dtype=np.float64)
    counter = 0
    for i in range(n):
        if regime_changes[i]:
            counter = 0
        counter += 1
        bars_in_regime[i] = counter
    features['bars_in_regime'] = bars_in_regime

    # Transition speed: rolling count of regime changes in last 168h
    features['transition_speed'] = pd.Series(
        regime_changes.astype(np.float64)
    ).rolling(168, min_periods=1).sum().values

    # Any regime change flag
    features['regime_change'] = regime_changes.astype(np.float64)

    return features


def generate_all_features(ind_1h, ind_4h, idx_1h, idx_4h, regime_1h):
    """Generate all feature categories and return combined dict.

    Parameters
    ----------
    ind_1h : dict
        1h indicators.
    ind_4h : dict
        4h indicators.
    idx_1h : pd.DatetimeIndex
        1h bar timestamps.
    idx_4h : pd.DatetimeIndex
        4h bar timestamps.
    regime_1h : np.ndarray
        Regime labels on 1h grid.

    Returns
    -------
    dict : {feature_name: np.ndarray} — all generated features
    """
    all_feats = {}

    interactions = generate_interaction_features(ind_1h)
    all_feats.update(interactions)

    momentum = generate_momentum_features(ind_1h)
    all_feats.update(momentum)

    cross_tf = generate_cross_tf_features(ind_1h, ind_4h, idx_1h, idx_4h)
    all_feats.update(cross_tf)

    regime = generate_regime_features(ind_1h, regime_1h)
    all_feats.update(regime)

    return all_feats
