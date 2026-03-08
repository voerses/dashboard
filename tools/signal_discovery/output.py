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


def save_analysis(analysis, cfg, token=None):
    """Save full temporal analysis results.

    Parameters
    ----------
    analysis : dict
        Output of run_full_analysis with keys:
        temporal_ics, decay_curves, rolling_ic, lead_lag.
    cfg : DiscoveryConfig
        Configuration.
    token : str, optional
        If per-token mode, the token name.
    """
    out_dir = cfg.output_dir
    os.makedirs(out_dir, exist_ok=True)
    suffix = f'_{token}' if token else ''

    # 1. Temporal ICs
    path = os.path.join(out_dir, f'temporal_ics{suffix}.json')
    with open(path, 'w') as f:
        json.dump(_make_serializable(analysis['temporal_ics']), f, indent=2)
    print(f'  Saved temporal ICs: {path}')

    # 2. IC Decay Curves
    path = os.path.join(out_dir, f'ic_decay_curves{suffix}.json')
    with open(path, 'w') as f:
        json.dump(_make_serializable(analysis['decay_curves']), f, indent=2)
    print(f'  Saved IC decay curves: {path}')

    # 3. Decay curves as CSV for quick analysis
    rows = []
    for dc in analysis['decay_curves']:
        for h, ic, t in zip(dc['horizons'], dc['ics'], dc['t_stats']):
            rows.append({
                'feature': dc['feature'],
                'horizon': h,
                'ic': ic,
                't_stat': t,
                'peak_horizon': dc['peak_horizon'],
                'half_life_horizon': dc['half_life_horizon'],
                'sign_consistent': dc['sign_consistent'],
            })
    if rows:
        df = pd.DataFrame(rows)
        path = os.path.join(out_dir, f'ic_decay_curves{suffix}.csv')
        df.to_csv(path, index=False, float_format='%.6f')

    # 4. Rolling IC
    path = os.path.join(out_dir, f'rolling_ic{suffix}.json')
    with open(path, 'w') as f:
        json.dump(_make_serializable(analysis['rolling_ic']), f, indent=2)
    print(f'  Saved rolling IC: {path} ({len(analysis["rolling_ic"])} signals)')

    # 5. Rolling IC summary CSV
    rows = []
    for ri in analysis['rolling_ic']:
        rows.append({
            'feature': ri['feature'],
            'horizon': ri['horizon'],
            'early_ic': ri['early_ic'],
            'late_ic': ri['late_ic'],
            'ic_shift': ri['ic_shift'],
            'annual_drift': ri['annual_drift'],
            'status': ri['status'],
        })
    if rows:
        df = pd.DataFrame(rows)
        path = os.path.join(out_dir, f'rolling_ic_summary{suffix}.csv')
        df.to_csv(path, index=False, float_format='%.6f')
        print(f'  Saved rolling IC summary: {path}')

    # 6. Lead/Lag analysis
    path = os.path.join(out_dir, f'lead_lag{suffix}.json')
    with open(path, 'w') as f:
        json.dump(_make_serializable(analysis['lead_lag']), f, indent=2)
    print(f'  Saved lead/lag analysis: {path} ({len(analysis["lead_lag"])} tests)')

    # 7. Lead/Lag CSV
    if analysis['lead_lag']:
        df = pd.DataFrame(analysis['lead_lag'])
        path = os.path.join(out_dir, f'lead_lag{suffix}.csv')
        df.to_csv(path, index=False, float_format='%.6f')


def print_analysis_summary(analysis):
    """Print human-readable analysis summary."""
    print('\n' + '=' * 70)
    print('TEMPORAL ANALYSIS')
    print('=' * 70)

    # Decay curves
    curves = analysis.get('decay_curves', [])
    if curves:
        print(f'\nIC Decay Curves ({len(curves)} features):')
        print(f'{"Feature":<40} {"Peak Hz":>8} {"Peak IC":>9} {"Half-life":>10} {"Consistent":>11}')
        print('-' * 80)
        for dc in curves[:15]:
            hl = str(dc['half_life_horizon']) + 'h' if dc['half_life_horizon'] else 'none'
            cons = 'yes' if dc['sign_consistent'] else 'NO'
            print(f'{dc["feature"]:<40} {dc["peak_horizon"]:>7}h '
                  f'{dc["peak_ic"]:>+9.4f} {hl:>10} {cons:>11}')

    # Rolling IC — signal health
    rolling = analysis.get('rolling_ic', [])
    if rolling:
        print(f'\nSignal Health (Rolling IC, {len(rolling)} signals):')
        print(f'{"Feature":<40} {"Hz":>4} {"Early IC":>9} {"Late IC":>9} {"Drift/yr":>9} {"Status":>14}')
        print('-' * 88)
        for ri in rolling[:20]:
            print(f'{ri["feature"]:<40} {ri["horizon"]:>4} '
                  f'{ri["early_ic"]:>+9.4f} {ri["late_ic"]:>+9.4f} '
                  f'{ri["annual_drift"]:>+9.5f} {ri["status"]:>14}')

        # Summary counts
        statuses = [ri['status'] for ri in rolling]
        for s in ['STABLE', 'STRENGTHENING', 'DECAYING', 'DEAD']:
            count = statuses.count(s)
            if count:
                print(f'  {s}: {count}')

    # Lead/lag — causal direction
    lead_lag = analysis.get('lead_lag', [])
    if lead_lag:
        leading = [ll for ll in lead_lag if ll['direction'] == 'LEADING']
        symmetric = [ll for ll in lead_lag if ll['direction'] == 'SYMMETRIC']
        lagging = [ll for ll in lead_lag if ll['direction'] == 'LAGGING']

        print(f'\nCausal Direction ({len(lead_lag)} tests):')
        print(f'  LEADING (feature predicts returns): {len(leading)}')
        print(f'  SYMMETRIC (concurrent):             {len(symmetric)}')
        print(f'  LAGGING (feature reacts to returns): {len(lagging)}')

        if leading:
            print(f'\n  Top LEADING signals (true predictors):')
            print(f'  {"Feature":<38} {"Hz":>4} {"IC_fwd":>8} {"IC_rev":>8} {"Ratio":>7} {"Conf":>6}')
            print('  ' + '-' * 74)
            for ll in leading[:10]:
                print(f'  {ll["feature"]:<38} {ll["horizon"]:>4} '
                      f'{ll["ic_forward"]:>+8.4f} {ll["ic_reverse"]:>+8.4f} '
                      f'{ll["asymmetry_ratio"]:>7.2f} {ll["confidence"]:>6}')

        if lagging:
            print(f'\n  LAGGING signals (reactive, not predictive):')
            for ll in lagging[:5]:
                print(f'  {ll["feature"]:<38} {ll["horizon"]:>4} '
                      f'fwd={ll["ic_forward"]:+.4f} rev={ll["ic_reverse"]:+.4f}')

    # Time-indexed splits — flag signals that died in recent windows
    temporal = analysis.get('temporal_ics', [])
    if temporal:
        dead_recently = []
        for t in temporal:
            splits = t.get('temporal_splits', [])
            if len(splits) >= 4:
                last_2 = [s['ic'] for s in splits[-2:]]
                first_half = [s['ic'] for s in splits[:len(splits) // 2]]
                if abs(np.mean(last_2)) < abs(np.mean(first_half)) * 0.3:
                    dead_recently.append(t)

        if dead_recently:
            print(f'\n  WARNING: {len(dead_recently)} signals with IC collapse in recent windows:')
            for t in dead_recently[:5]:
                splits = t['temporal_splits']
                last = splits[-1]
                print(f'    {t["feature"]} (h={t["horizon"]}): '
                      f'IC dropped to {last["ic"]:+.4f} in {last["start_date"]}→{last["end_date"]}')

    print('=' * 70)


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
