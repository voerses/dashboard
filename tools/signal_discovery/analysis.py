"""Temporal analysis layer — time-indexed ICs, decay curves, rolling IC, lead/lag causality."""

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from .config import DiscoveryConfig, REGIME_NAMES
from .evaluator import _forward_returns, _spearman_ic


# ---------------------------------------------------------------------------
# 1. Time-Indexed Split ICs
# ---------------------------------------------------------------------------
def compute_time_indexed_ics(results, idx_1h):
    """Tag each split IC with its date range.

    Parameters
    ----------
    results : list of dict
        Significant results with 'split_ics' and 'split_ranges'.
    idx_1h : pd.DatetimeIndex
        1h bar timestamps.

    Returns
    -------
    list of dict : one entry per result, with 'temporal_splits' added:
        [{start_date, end_date, ic}, ...]
    """
    temporal_results = []

    for r in results:
        split_ics = r.get('split_ics', [])
        split_ranges = r.get('split_ranges', [])

        temporal_splits = []
        for ic, (start_idx, end_idx) in zip(split_ics, split_ranges):
            # Clamp indices to valid range
            si = min(start_idx, len(idx_1h) - 1)
            ei = min(end_idx - 1, len(idx_1h) - 1)
            temporal_splits.append({
                'start_date': str(idx_1h[si].date()),
                'end_date': str(idx_1h[ei].date()),
                'start_idx': start_idx,
                'end_idx': end_idx,
                'ic': ic,
            })

        temporal_results.append({
            'feature': r['feature'],
            'horizon': r['horizon'],
            'mean_ic': r['mean_ic'],
            'temporal_splits': temporal_splits,
        })

    return temporal_results


# ---------------------------------------------------------------------------
# 2. IC Decay Curves
# ---------------------------------------------------------------------------
def compute_ic_decay_curves(results):
    """Group results by feature and show IC progression across horizons.

    Parameters
    ----------
    results : list of dict
        Significant results (all horizons).

    Returns
    -------
    list of dict : one per feature, with 'decay_curve':
        {feature, horizons: [h1, h2, ...], ics: [ic1, ic2, ...],
         t_stats: [t1, t2, ...], peak_horizon, half_life_horizon}
    """
    # Group by feature
    by_feature = {}
    for r in results:
        fname = r['feature']
        if fname not in by_feature:
            by_feature[fname] = []
        by_feature[fname].append(r)

    decay_curves = []

    for fname, feature_results in by_feature.items():
        # Sort by horizon
        feature_results.sort(key=lambda x: x['horizon'])

        horizons = [r['horizon'] for r in feature_results]
        ics = [r['mean_ic'] for r in feature_results]
        t_stats = [r.get('t_stat', 0) for r in feature_results]
        abs_ics = [abs(ic) for ic in ics]

        # Peak horizon: where |IC| is maximum
        peak_idx = np.argmax(abs_ics)
        peak_horizon = horizons[peak_idx]
        peak_ic = abs_ics[peak_idx]

        # Half-life horizon: first horizon after peak where |IC| drops below peak/2
        half_life_horizon = None
        if len(horizons) > 1:
            half_threshold = peak_ic / 2
            for i in range(peak_idx + 1, len(horizons)):
                if abs_ics[i] < half_threshold:
                    half_life_horizon = horizons[i]
                    break

        # IC sign consistency across horizons
        signs = [np.sign(ic) for ic in ics if ic != 0]
        sign_consistent = all(s == signs[0] for s in signs) if signs else True

        decay_curves.append({
            'feature': fname,
            'horizons': horizons,
            'ics': ics,
            't_stats': t_stats,
            'peak_horizon': peak_horizon,
            'peak_ic': ics[peak_idx],
            'half_life_horizon': half_life_horizon,
            'sign_consistent': sign_consistent,
        })

    # Sort by peak |IC|
    decay_curves.sort(key=lambda x: abs(x['peak_ic']), reverse=True)
    return decay_curves


# ---------------------------------------------------------------------------
# 3. Rolling IC (Expanding Window)
# ---------------------------------------------------------------------------
def compute_rolling_ic(features, close, regime_1h, idx_1h, significant_results, cfg,
                       step_bars=720, min_window=8760):
    """Compute expanding-window IC over time for significant signals.

    Every `step_bars` (default 30 days), compute IC using all data from start
    up to that point. Shows whether signal's predictive power is growing,
    stable, or decaying.

    Parameters
    ----------
    features : dict
        {feature_name: np.ndarray}.
    close : np.ndarray
        Close prices on 1h grid.
    regime_1h : np.ndarray
        Regime labels on 1h grid.
    idx_1h : pd.DatetimeIndex
        1h timestamps.
    significant_results : list of dict
        Significant results (feature, horizon pairs to analyze).
    cfg : DiscoveryConfig
        Configuration.
    step_bars : int
        Step size in bars (default 720 = 30 days).
    min_window : int
        Minimum window size in bars to start computing (default 8760 = 1 year).

    Returns
    -------
    list of dict : one per (feature, horizon), with 'rolling_ic':
        [{date, window_bars, ic, n_obs}, ...]
    """
    # Deduplicate (feature, horizon) pairs
    seen = set()
    pairs = []
    for r in significant_results:
        key = (r['feature'], r['horizon'])
        if key not in seen:
            seen.add(key)
            pairs.append(key)

    # Limit to top 30 signals by |IC| to keep computation manageable
    pairs_with_ic = []
    for r in significant_results:
        key = (r['feature'], r['horizon'])
        if key in seen:
            pairs_with_ic.append((key, abs(r['mean_ic'])))
            seen.discard(key)  # only add once
    pairs_with_ic.sort(key=lambda x: x[1], reverse=True)
    pairs = [p[0] for p in pairs_with_ic[:30]]

    n = len(close)
    rolling_results = []

    for feat_name, horizon in pairs:
        feat_vals = features.get(feat_name)
        if feat_vals is None:
            continue

        fwd = _forward_returns(close, horizon)

        ic_series = []
        # Expanding window: start at min_window, step by step_bars
        for end_bar in range(min_window, n, step_bars):
            f_window = feat_vals[:end_bar]
            r_window = fwd[:end_bar]

            ic, _ = _spearman_ic(f_window, r_window)
            if np.isfinite(ic):
                date_idx = min(end_bar - 1, len(idx_1h) - 1)
                ic_series.append({
                    'date': str(idx_1h[date_idx].date()),
                    'window_bars': end_bar,
                    'ic': float(ic),
                })

        if ic_series:
            # Compute trend: linear regression slope of IC over time
            if len(ic_series) >= 3:
                ic_vals = np.array([s['ic'] for s in ic_series])
                x = np.arange(len(ic_vals))
                slope = np.polyfit(x, ic_vals, 1)[0]
                # Normalize slope to IC-per-year
                steps_per_year = 8760 / step_bars
                annual_drift = float(slope * steps_per_year)
            else:
                annual_drift = 0.0

            # Recent IC vs early IC
            if len(ic_series) >= 4:
                half = len(ic_series) // 2
                early_ic = np.mean([s['ic'] for s in ic_series[:half]])
                late_ic = np.mean([s['ic'] for s in ic_series[half:]])
                ic_shift = float(late_ic - early_ic)
            else:
                early_ic = ic_series[0]['ic']
                late_ic = ic_series[-1]['ic']
                ic_shift = float(late_ic - early_ic)

            rolling_results.append({
                'feature': feat_name,
                'horizon': horizon,
                'rolling_ic': ic_series,
                'annual_drift': annual_drift,
                'early_ic': float(early_ic),
                'late_ic': float(late_ic),
                'ic_shift': ic_shift,
                'status': _classify_drift(annual_drift, ic_shift, late_ic),
            })

    return rolling_results


def _classify_drift(annual_drift, ic_shift, late_ic):
    """Classify signal health based on IC drift."""
    abs_late = abs(late_ic)
    if abs_late < 0.01:
        return 'DEAD'
    if abs(annual_drift) < 0.005 and abs(ic_shift) < 0.02:
        return 'STABLE'
    if annual_drift * np.sign(late_ic) > 0.005:
        return 'STRENGTHENING'
    if annual_drift * np.sign(late_ic) < -0.005:
        return 'DECAYING'
    return 'STABLE'


# ---------------------------------------------------------------------------
# 4. Granger-Style Lead/Lag Asymmetry
# ---------------------------------------------------------------------------
def compute_lead_lag_asymmetry(features, close, idx_1h, significant_results, cfg):
    """Test causal direction via IC asymmetry.

    For each significant (feature, horizon):
      IC_forward = corr(feature_t, return_{t→t+h})  [feature predicts returns]
      IC_reverse = corr(return_t, feature_{t+h})      [returns predict feature changes]

    If |IC_forward| >> |IC_reverse|: feature leads (causal predictor)
    If |IC_forward| ≈ |IC_reverse|: symmetric / concurrent
    If |IC_forward| << |IC_reverse|: feature lags (reactive indicator)

    Parameters
    ----------
    features : dict
        {feature_name: np.ndarray}.
    close : np.ndarray
        Close prices on 1h grid.
    idx_1h : pd.DatetimeIndex
        1h timestamps.
    significant_results : list of dict
        Significant results.
    cfg : DiscoveryConfig
        Configuration.

    Returns
    -------
    list of dict : one per (feature, horizon), with causality assessment:
        {feature, horizon, ic_forward, ic_reverse, asymmetry_ratio,
         direction: 'LEADING'|'SYMMETRIC'|'LAGGING', confidence}
    """
    # Deduplicate
    seen = set()
    pairs = []
    for r in significant_results:
        key = (r['feature'], r['horizon'])
        if key not in seen:
            seen.add(key)
            pairs.append((key, r))

    n = len(close)
    lag_results = []

    for (feat_name, horizon), result in pairs:
        feat_vals = features.get(feat_name)
        if feat_vals is None:
            continue

        # Forward IC: feature_t → return_{t→t+h}
        fwd_ret = _forward_returns(close, horizon)
        ic_forward, _ = _spearman_ic(feat_vals, fwd_ret)

        # Reverse IC: return_{t-h→t} → feature_t
        # Equivalent: corr(return_t, feature_{t+h})
        # We compute: backward returns at each point, then correlate with feature shifted forward
        # Simpler: compute returns up to bar t, correlate with feature at t+h
        if horizon >= n:
            continue

        # return_t = log(close_t / close_{t-h})
        backward_ret = np.full(n, np.nan)
        backward_ret[horizon:] = np.log(
            close[horizon:] / np.maximum(close[:-horizon], 1e-10))

        # Correlate backward_ret_t with feature_{t+h} → does the return predict future feature value?
        # That is: corr(backward_ret[t], feat[t+h])
        # Shift feature forward by h: feat_shifted[t] = feat[t+h]
        feat_shifted = np.full(n, np.nan)
        feat_shifted[:n - horizon] = feat_vals[horizon:]

        ic_reverse, _ = _spearman_ic(backward_ret, feat_shifted)

        if not np.isfinite(ic_forward) or not np.isfinite(ic_reverse):
            continue

        abs_fwd = abs(ic_forward)
        abs_rev = abs(ic_reverse)

        # Asymmetry ratio: how much stronger is forward vs reverse
        if abs_rev > 1e-6:
            asymmetry_ratio = abs_fwd / abs_rev
        else:
            asymmetry_ratio = float('inf') if abs_fwd > 0.01 else 1.0

        # Direction classification
        if asymmetry_ratio > 2.0:
            direction = 'LEADING'
        elif asymmetry_ratio < 0.5:
            direction = 'LAGGING'
        else:
            direction = 'SYMMETRIC'

        # Confidence: based on how different the two ICs are
        ic_diff = abs_fwd - abs_rev
        confidence = 'HIGH' if abs(ic_diff) > 0.05 else 'MEDIUM' if abs(ic_diff) > 0.02 else 'LOW'

        lag_results.append({
            'feature': feat_name,
            'horizon': horizon,
            'ic_forward': float(ic_forward),
            'ic_reverse': float(ic_reverse),
            'asymmetry_ratio': float(min(asymmetry_ratio, 999.0)),
            'direction': direction,
            'confidence': confidence,
        })

    # Sort: LEADING first, then by asymmetry_ratio descending
    direction_order = {'LEADING': 0, 'SYMMETRIC': 1, 'LAGGING': 2}
    lag_results.sort(key=lambda x: (direction_order[x['direction']], -x['asymmetry_ratio']))

    return lag_results


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
def run_full_analysis(features, close, regime_1h, idx_1h, significant_results, cfg):
    """Run all 4 temporal analyses.

    Parameters
    ----------
    features : dict
        {feature_name: np.ndarray}.
    close : np.ndarray
        Close prices on 1h grid.
    regime_1h : np.ndarray
        Regime labels on 1h grid.
    idx_1h : pd.DatetimeIndex
        1h timestamps.
    significant_results : list of dict
        Significant results from evaluator.
    cfg : DiscoveryConfig
        Configuration.

    Returns
    -------
    dict with keys:
        temporal_ics, decay_curves, rolling_ic, lead_lag
    """
    print('  Running temporal analysis...')

    print('    Time-indexed split ICs...')
    temporal_ics = compute_time_indexed_ics(significant_results, idx_1h)

    print('    IC decay curves...')
    decay_curves = compute_ic_decay_curves(significant_results)

    print('    Rolling IC (expanding window)...')
    rolling_ic = compute_rolling_ic(
        features, close, regime_1h, idx_1h, significant_results, cfg)

    print('    Lead/lag asymmetry...')
    lead_lag = compute_lead_lag_asymmetry(
        features, close, idx_1h, significant_results, cfg)

    return {
        'temporal_ics': temporal_ics,
        'decay_curves': decay_curves,
        'rolling_ic': rolling_ic,
        'lead_lag': lead_lag,
    }
