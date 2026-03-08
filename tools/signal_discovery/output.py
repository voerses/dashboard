"""Catalog serialization — JSON and CSV output."""

import json
import os

import numpy as np
import pandas as pd

from .config import DiscoveryConfig


def _make_serializable(obj):
    """Convert numpy types to Python types for JSON serialization."""
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, dict):
        return {k: _make_serializable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_make_serializable(v) for v in obj]
    return obj


def save_signal_catalog(results, cfg, token=None):
    """Save full signal catalog as JSON.

    Parameters
    ----------
    results : list of dict
        Significant results from evaluator + clustering.
    cfg : DiscoveryConfig
        Configuration used for the run.
    token : str, optional
        If per-token mode, the token name.
    """
    out_dir = cfg.output_dir
    os.makedirs(out_dir, exist_ok=True)

    catalog = {
        'config': {
            'market': cfg.market,
            'horizons': cfg.horizons,
            'fdr_alpha': cfg.fdr_alpha,
            'min_abs_ic': cfg.min_abs_ic,
            'min_t_stat': cfg.min_t_stat,
            'max_corr': cfg.max_corr,
            'purge_gap_bars': cfg.purge_gap_bars,
            'test_window_bars': cfg.test_window_bars,
            'bootstrap_samples': cfg.bootstrap_samples,
        },
        'n_signals': len(results),
        'signals': _make_serializable(results),
    }

    if token:
        filename = f'signal_catalog_{token}.json'
    else:
        filename = 'signal_catalog.json'

    path = os.path.join(out_dir, filename)
    with open(path, 'w') as f:
        json.dump(catalog, f, indent=2, default=str)
    print(f'  Saved catalog: {path} ({len(results)} signals)')


def save_rankings_csv(results, cfg, token=None):
    """Save signal rankings as CSV for quick analysis.

    Parameters
    ----------
    results : list of dict
        Significant results.
    cfg : DiscoveryConfig
        Configuration.
    token : str, optional
        If per-token mode, the token name.
    """
    out_dir = cfg.output_dir
    os.makedirs(out_dir, exist_ok=True)

    rows = []
    for r in results:
        row = {
            'feature': r['feature'],
            'horizon': r['horizon'],
            'mean_ic': r['mean_ic'],
            'std_ic': r.get('std_ic'),
            't_stat': r.get('t_stat'),
            'n_splits': r['n_splits'],
            'fdr_pvalue': r.get('fdr_pvalue'),
            'ic_ci_lower': r.get('ic_ci_lower'),
            'ic_ci_upper': r.get('ic_ci_upper'),
        }
        # Add per-regime ICs as columns
        for rname in ['CRISIS', 'QUIET', 'UPTREND', 'RANGE', 'DOWNTREND']:
            row[f'ic_{rname}'] = r.get('per_regime_ic', {}).get(rname)
        rows.append(row)

    df = pd.DataFrame(rows)
    if len(df) > 0:
        df = df.sort_values('mean_ic', key=abs, ascending=False)

    if token:
        filename = f'rankings_{token}.csv'
    else:
        filename = 'rankings.csv'

    path = os.path.join(out_dir, filename)
    df.to_csv(path, index=False, float_format='%.6f')
    print(f'  Saved rankings: {path} ({len(df)} rows)')


def save_regime_signals(regime_signals, cfg):
    """Save per-regime orthogonal signal sets as JSON.

    Parameters
    ----------
    regime_signals : dict
        {regime_name: [(feature_name, regime_ic), ...]}.
    cfg : DiscoveryConfig
        Configuration.
    """
    out_dir = cfg.output_dir
    os.makedirs(out_dir, exist_ok=True)

    path = os.path.join(out_dir, 'regime_signals.json')
    with open(path, 'w') as f:
        json.dump(_make_serializable(regime_signals), f, indent=2)
    print(f'  Saved regime signals: {path}')


def print_summary(results, regime_signals, n_tokens, n_total_features):
    """Print human-readable summary to stdout."""
    print('\n' + '=' * 60)
    print('SIGNAL DISCOVERY SUMMARY')
    print('=' * 60)
    print(f'Tokens analyzed:      {n_tokens}')
    print(f'Features generated:   {n_total_features}')
    print(f'Significant signals:  {len(results)}')

    if results:
        # Top 10 by |IC|
        print(f'\nTop 10 signals by |IC|:')
        print(f'{"Feature":<45} {"Hz":>4} {"IC":>8} {"t":>6} {"CI":>16}')
        print('-' * 82)
        for r in results[:10]:
            ci_lo = r.get('ic_ci_lower', '')
            ci_hi = r.get('ic_ci_upper', '')
            ci_str = f'[{ci_lo:.4f},{ci_hi:.4f}]' if isinstance(ci_lo, float) else ''
            print(f'{r["feature"]:<45} {r["horizon"]:>4} '
                  f'{r["mean_ic"]:>8.4f} {r.get("t_stat", 0):>6.2f} {ci_str:>16}')

    if regime_signals:
        print(f'\nTop signals per regime:')
        for regime_name, sigs in regime_signals.items():
            if sigs:
                print(f'\n  {regime_name} ({len(sigs)} signals):')
                for fname, ric in sigs[:5]:
                    print(f'    {fname:<42} IC={ric:>8.4f}')

    print('=' * 60)
