"""Purged walk-forward IC evaluation with FDR correction and bootstrap CI."""

import numpy as np
from scipy.stats import spearmanr
from statsmodels.stats.multitest import multipletests

from .config import DiscoveryConfig, REGIME_NAMES


def _forward_returns(close, horizon):
    """Compute forward log returns at given horizon (hours).

    Parameters
    ----------
    close : np.ndarray
        Close prices (1h).
    horizon : int
        Forward horizon in hours/bars.

    Returns
    -------
    np.ndarray : forward returns with NaN at the tail.
    """
    n = len(close)
    fwd = np.full(n, np.nan)
    fwd[:n - horizon] = np.log(close[horizon:] / np.maximum(close[:n - horizon], 1e-10))
    return fwd


def _anchored_walk_forward_splits(n_bars, cfg):
    """Generate anchored walk-forward (train, test) index splits with purge gap.

    Train: expanding from start (>= min_train_bars).
    Purge: cfg.purge_gap_bars gap between train end and test start.
    Test: cfg.test_window_bars.

    Parameters
    ----------
    n_bars : int
        Total number of bars.
    cfg : DiscoveryConfig
        Configuration with min_train_bars, test_window_bars, purge_gap_bars.

    Yields
    ------
    (train_start, train_end, test_start, test_end) : tuple of int
        Index ranges (exclusive end).
    """
    test_size = cfg.test_window_bars
    purge = cfg.purge_gap_bars
    min_train = cfg.min_train_bars

    # First test window starts after min_train + purge
    test_start = min_train + purge
    while test_start + test_size <= n_bars:
        train_end = test_start - purge
        test_end = test_start + test_size
        yield (0, train_end, test_start, test_end)
        test_start = test_end  # non-overlapping test windows


def _spearman_ic(feature, forward_ret):
    """Compute Spearman rank IC between feature and forward returns.

    Handles NaN by masking both arrays.

    Returns
    -------
    (ic, pvalue) or (np.nan, np.nan) if insufficient data.
    """
    mask = np.isfinite(feature) & np.isfinite(forward_ret)
    if mask.sum() < 30:
        return np.nan, np.nan
    corr, pval = spearmanr(feature[mask], forward_ret[mask])
    return corr, pval


def evaluate_single_feature(feature_values, close, regime_1h, horizon, cfg):
    """Evaluate a single feature at a single horizon using purged walk-forward IC.

    Parameters
    ----------
    feature_values : np.ndarray
        Feature values on 1h grid.
    close : np.ndarray
        Close prices on 1h grid.
    regime_1h : np.ndarray
        Regime labels on 1h grid.
    horizon : int
        Forward return horizon in hours.
    cfg : DiscoveryConfig
        Configuration.

    Returns
    -------
    dict with keys:
        mean_ic, std_ic, t_stat, n_splits,
        per_regime_ic: {regime_name: mean_ic},
        split_ics: list of per-split ICs
    """
    fwd = _forward_returns(close, horizon)
    n = len(close)

    split_ics = []
    split_ranges = []  # (test_start, test_end) for each valid split
    # Per-regime IC accumulators: {regime_id: [ic_values]}
    regime_ics = {rid: [] for rid in REGIME_NAMES}

    for train_start, train_end, test_start, test_end in _anchored_walk_forward_splits(n, cfg):
        feat_test = feature_values[test_start:test_end]
        fwd_test = fwd[test_start:test_end]
        regime_test = regime_1h[test_start:test_end]

        # Overall IC on test window
        ic, _ = _spearman_ic(feat_test, fwd_test)
        if np.isfinite(ic):
            split_ics.append(ic)
            split_ranges.append((test_start, test_end))

        # Per-regime IC within this test window
        for rid in REGIME_NAMES:
            rmask = regime_test == rid
            if rmask.sum() < 20:
                continue
            ric, _ = _spearman_ic(feat_test[rmask], fwd_test[rmask])
            if np.isfinite(ric):
                regime_ics[rid].append(ric)

    if len(split_ics) == 0:
        return {
            'mean_ic': np.nan, 'std_ic': np.nan, 't_stat': np.nan,
            'n_splits': 0, 'per_regime_ic': {}, 'split_ics': [],
            'split_ranges': [],
        }

    mean_ic = np.mean(split_ics)
    std_ic = np.std(split_ics, ddof=1) if len(split_ics) > 1 else np.nan
    t_stat = mean_ic / (std_ic / np.sqrt(len(split_ics))) if np.isfinite(std_ic) and std_ic > 0 else np.nan

    per_regime_ic = {}
    for rid, rname in REGIME_NAMES.items():
        if regime_ics[rid]:
            per_regime_ic[rname] = float(np.mean(regime_ics[rid]))

    return {
        'mean_ic': float(mean_ic),
        'std_ic': float(std_ic) if np.isfinite(std_ic) else None,
        't_stat': float(t_stat) if np.isfinite(t_stat) else None,
        'n_splits': len(split_ics),
        'per_regime_ic': per_regime_ic,
        'split_ics': [float(x) for x in split_ics],
        'split_ranges': split_ranges,
    }


def evaluate_all_features(features, close, regime_1h, cfg):
    """Evaluate all features across all horizons.

    Parameters
    ----------
    features : dict
        {feature_name: np.ndarray} of feature values on 1h grid.
    close : np.ndarray
        Close prices.
    regime_1h : np.ndarray
        Regime labels on 1h grid.
    cfg : DiscoveryConfig
        Configuration.

    Returns
    -------
    list of dict : evaluation results per (feature, horizon), each with keys:
        feature, horizon, mean_ic, std_ic, t_stat, n_splits, per_regime_ic, split_ics
    """
    results = []

    for feat_name, feat_vals in features.items():
        for horizon in cfg.horizons:
            eval_result = evaluate_single_feature(
                feat_vals, close, regime_1h, horizon, cfg)
            eval_result['feature'] = feat_name
            eval_result['horizon'] = horizon
            results.append(eval_result)

    return results


def apply_fdr_correction(results, cfg):
    """Apply Benjamini-Hochberg FDR correction across all (feature, horizon) tests.

    Adds 'significant' and 'fdr_pvalue' fields to results in-place.
    Filters to results meeting min_abs_ic and min_t_stat thresholds.

    Parameters
    ----------
    results : list of dict
        Output of evaluate_all_features.
    cfg : DiscoveryConfig
        Configuration with fdr_alpha, min_abs_ic, min_t_stat.

    Returns
    -------
    list of dict : significant results only, sorted by |mean_ic| descending.
    """
    # Compute synthetic p-values from t-stats for FDR
    # Use two-sided p-value from t-distribution
    from scipy.stats import t as t_dist

    valid = [r for r in results if r['t_stat'] is not None and r['n_splits'] > 1]
    if not valid:
        return []

    pvalues = []
    for r in valid:
        df = r['n_splits'] - 1
        p = 2 * t_dist.sf(abs(r['t_stat']), df)
        pvalues.append(p)
        r['raw_pvalue'] = float(p)

    # Benjamini-Hochberg correction
    reject, pvals_corrected, _, _ = multipletests(
        pvalues, alpha=cfg.fdr_alpha, method='fdr_bh')

    for r, rej, pcorr in zip(valid, reject, pvals_corrected):
        r['significant'] = bool(rej)
        r['fdr_pvalue'] = float(pcorr)

    # Filter: significant AND meets IC/t-stat thresholds
    significant = [
        r for r in valid
        if r['significant']
        and abs(r['mean_ic']) >= cfg.min_abs_ic
        and abs(r['t_stat']) >= cfg.min_t_stat
    ]

    significant.sort(key=lambda r: abs(r['mean_ic']), reverse=True)
    return significant


def bootstrap_confidence_intervals(results, cfg):
    """Add block bootstrap confidence intervals to results.

    Uses block bootstrap with block size = horizon to preserve autocorrelation.

    Parameters
    ----------
    results : list of dict
        Must have 'split_ics' field.
    cfg : DiscoveryConfig
        Configuration with bootstrap_samples.

    Returns
    -------
    None (modifies results in-place, adds 'ic_ci_lower', 'ic_ci_upper').
    """
    rng = np.random.default_rng(42)

    for r in results:
        ics = np.array(r.get('split_ics', []))
        if len(ics) < 2:
            r['ic_ci_lower'] = r.get('mean_ic')
            r['ic_ci_upper'] = r.get('mean_ic')
            continue

        # Block bootstrap: resample split ICs (each split is already a block)
        boot_means = np.empty(cfg.bootstrap_samples)
        for b in range(cfg.bootstrap_samples):
            idx = rng.choice(len(ics), size=len(ics), replace=True)
            boot_means[b] = ics[idx].mean()

        r['ic_ci_lower'] = float(np.percentile(boot_means, 2.5))
        r['ic_ci_upper'] = float(np.percentile(boot_means, 97.5))
