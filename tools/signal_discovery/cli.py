"""CLI entrypoint for Signal Discovery Engine.

Usage:
    python -m tools.signal_discovery.cli --market perp --workers 4
    python -m tools.signal_discovery.cli --tokens BTC ETH SOL --quick
    python -m tools.signal_discovery.cli --per-token --primary-horizon 24
"""

import argparse
import os
import sys
import time
import importlib.util
from concurrent.futures import ProcessPoolExecutor, as_completed

import numpy as np
import pandas as pd

from .config import DiscoveryConfig, REGIME_NAMES
from .features import generate_all_features
from .evaluator import (
    evaluate_all_features, apply_fdr_correction, bootstrap_confidence_intervals,
)
from .clustering import (
    compute_feature_correlation_matrix, cluster_and_select,
    select_regime_orthogonal,
)
from .output import (
    save_signal_catalog, save_rankings_csv, save_regime_signals,
    print_summary, save_analysis, print_analysis_summary,
)
from .analysis import run_full_analysis


# ---------------------------------------------------------------------------
# Engine imports (read-only)
# ---------------------------------------------------------------------------
def _load_engine():
    """Load v3/engine.py via importlib (same pattern as engine itself)."""
    v3_dir = os.path.join(os.path.dirname(__file__), '..', '..', 'v3')
    v3_dir = os.path.abspath(v3_dir)
    spec = importlib.util.spec_from_file_location(
        'v3_engine', os.path.join(v3_dir, 'engine.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_engine = None

def _get_engine():
    global _engine
    if _engine is None:
        _engine = _load_engine()
    return _engine


# ---------------------------------------------------------------------------
# Per-token processing
# ---------------------------------------------------------------------------
def _load_token_data(token, market, data_dir):
    """Load 1h parquet for a token. Returns DataFrame or None."""
    cache_dir = os.path.join(data_dir, market, '1h_cache')
    path = os.path.join(cache_dir, f'{token}_1h.parquet')
    if not os.path.exists(path):
        return None
    df = pd.read_parquet(path)
    if not isinstance(df.index, pd.DatetimeIndex):
        if 'timestamp' in df.columns:
            df.index = pd.to_datetime(df['timestamp'], unit='ms')
        elif 'datetime' in df.columns:
            df.index = pd.to_datetime(df['datetime'])
    return df


def process_single_token(token, cfg, data_dir):
    """Run full signal discovery pipeline for a single token.

    Parameters
    ----------
    token : str
        Token symbol.
    cfg : DiscoveryConfig
        Configuration.
    data_dir : str
        Path to data/ directory.

    Returns
    -------
    dict or None : {token, n_features, significant_results, regime_signals, features}
    """
    engine = _get_engine()

    # Load 1h data
    df_1h = _load_token_data(token, cfg.market, data_dir)
    if df_1h is None or len(df_1h) < cfg.min_bars:
        return None

    # Extract arrays
    close = df_1h['close'].values.astype(np.float64)
    high = df_1h['high'].values.astype(np.float64)
    low = df_1h['low'].values.astype(np.float64)
    volume = df_1h['volume'].values.astype(np.float64)
    taker_buy = (df_1h['taker_buy_base'].values.astype(np.float64)
                 if 'taker_buy_base' in df_1h.columns else None)

    idx_1h = df_1h.index

    # Compute 1h indicators
    ind_1h = engine.compute_indicators_fast(close, high, low, volume, taker_buy)

    # Compute 4h aggregation + indicators
    df_4h = engine.aggregate_to_timeframe(df_1h, hours=4)
    c4 = df_4h['close'].values.astype(np.float64)
    h4 = df_4h['high'].values.astype(np.float64)
    l4 = df_4h['low'].values.astype(np.float64)
    v4 = df_4h['volume'].values.astype(np.float64)
    t4 = (df_4h['taker_buy_ratio'].values.astype(np.float64)
          if 'taker_buy_ratio' in df_4h.columns else None)
    ind_4h = engine.compute_indicators_fast(c4, h4, l4, v4, t4)
    idx_4h = df_4h.index

    # Compute daily aggregation + regime detection
    df_daily = engine.aggregate_to_timeframe(df_1h, hours=24)
    cd = df_daily['close'].values.astype(np.float64)
    hd = df_daily['high'].values.astype(np.float64)
    ld = df_daily['low'].values.astype(np.float64)
    vd = df_daily['volume'].values.astype(np.float64)
    ind_d = engine.compute_indicators_fast(cd, hd, ld, vd)
    regime_daily = engine.detect_daily_regime(ind_d)

    # Align regime to 1h grid
    regime_1h = engine._align_higher_to_lower(
        df_daily.index, regime_daily, idx_1h).astype(np.int8)

    # Generate all features
    features = generate_all_features(ind_1h, ind_4h, idx_1h, idx_4h, regime_1h)
    n_features = len(features)

    # Evaluate
    results = evaluate_all_features(features, close, regime_1h, cfg)

    # FDR correction
    significant = apply_fdr_correction(results, cfg)

    # Bootstrap CIs
    bootstrap_confidence_intervals(significant, cfg)

    return {
        'token': token,
        'n_bars': len(df_1h),
        'n_features': n_features,
        'significant_results': significant,
        'features': features,
        'close': close,
        'regime_1h': regime_1h,
        'idx_1h': idx_1h,
    }


def _process_token_wrapper(args):
    """Wrapper for multiprocessing — unpacks args tuple."""
    token, cfg_dict, data_dir = args
    cfg = DiscoveryConfig(**cfg_dict)
    try:
        return process_single_token(token, cfg, data_dir)
    except Exception as e:
        print(f'  ERROR processing {token}: {e}')
        return None


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------
def run_discovery(cfg):
    """Run the full signal discovery pipeline.

    Parameters
    ----------
    cfg : DiscoveryConfig
        Configuration.
    """
    t0 = time.time()
    data_dir = os.path.join(os.path.dirname(__file__), '..', '..', 'data')
    data_dir = os.path.abspath(data_dir)

    engine = _get_engine()

    # Discover tokens
    if cfg.tokens:
        tokens = cfg.tokens
    else:
        tokens = engine.get_all_tradeable(cfg.market)

    if cfg.quick and not cfg.tokens:
        # Quick mode: just top 5 tokens by data length
        token_lengths = []
        for t in tokens:
            path = os.path.join(data_dir, cfg.market, '1h_cache', f'{t}_1h.parquet')
            if os.path.exists(path):
                try:
                    n = len(pd.read_parquet(path, columns=['close']))
                    token_lengths.append((t, n))
                except Exception:
                    pass
        token_lengths.sort(key=lambda x: x[1], reverse=True)
        tokens = [t for t, _ in token_lengths[:5]]
        print(f'Quick mode: using top 5 tokens by data length: {tokens}')

    print(f'Signal Discovery Engine')
    print(f'  Market: {cfg.market}')
    print(f'  Tokens: {len(tokens)}')
    print(f'  Horizons: {cfg.horizons}')
    print(f'  Workers: {cfg.workers}')
    print()

    # Process tokens
    all_significant = []
    all_features = {}  # Merged feature arrays (from last token processed for clustering)
    best_result = None  # longest token's full result (for analysis)
    n_tokens_processed = 0
    n_total_features = 0

    if cfg.per_token:
        # Per-token mode: each token gets its own catalog
        for token in tokens:
            print(f'Processing {token}...')
            result = process_single_token(token, cfg, data_dir)
            if result is None:
                print(f'  Skipped (insufficient data)')
                continue
            n_tokens_processed += 1
            n_total_features = max(n_total_features, result['n_features'])

            if result['significant_results']:
                save_signal_catalog(result['significant_results'], cfg, token=token)
                save_rankings_csv(result['significant_results'], cfg, token=token)

                if cfg.analyze:
                    analysis = run_full_analysis(
                        result['features'], result['close'], result['regime_1h'],
                        result['idx_1h'], result['significant_results'], cfg)
                    save_analysis(analysis, cfg, token=token)
                    print_analysis_summary(analysis)
            else:
                print(f'  No significant signals found')
    else:
        # Pooled mode: aggregate across tokens
        if cfg.workers > 1 and len(tokens) > 1:
            # Serialize config for multiprocessing
            cfg_dict = {
                'market': cfg.market, 'tokens': None,
                'min_bars': cfg.min_bars, 'horizons': cfg.horizons,
                'min_train_bars': cfg.min_train_bars,
                'test_window_bars': cfg.test_window_bars,
                'purge_gap_bars': cfg.purge_gap_bars,
                'fdr_alpha': cfg.fdr_alpha, 'min_abs_ic': cfg.min_abs_ic,
                'min_t_stat': cfg.min_t_stat,
                'bootstrap_samples': cfg.bootstrap_samples,
                'max_corr': cfg.max_corr, 'top_per_regime': cfg.top_per_regime,
                'workers': 1, 'quick': cfg.quick,
                'per_token': False, 'primary_horizon': cfg.primary_horizon,
                'analyze': cfg.analyze, 'output_dir': cfg.output_dir,
            }
            args_list = [(t, cfg_dict, data_dir) for t in tokens]

            with ProcessPoolExecutor(max_workers=cfg.workers) as executor:
                futures = {executor.submit(_process_token_wrapper, a): a[0]
                           for a in args_list}
                for future in as_completed(futures):
                    token = futures[future]
                    try:
                        result = future.result()
                    except Exception as e:
                        print(f'  ERROR {token}: {e}')
                        continue

                    if result is None:
                        continue
                    n_tokens_processed += 1
                    n_total_features = max(n_total_features, result['n_features'])
                    all_significant.extend(result['significant_results'])
                    all_features.update(result['features'])
                    if best_result is None or result['n_bars'] > best_result['n_bars']:
                        best_result = result
                    print(f'  {token}: {result["n_bars"]} bars, '
                          f'{result["n_features"]} features, '
                          f'{len(result["significant_results"])} significant')
        else:
            # Sequential
            for token in tokens:
                print(f'Processing {token}...')
                result = process_single_token(token, cfg, data_dir)
                if result is None:
                    print(f'  Skipped (insufficient data)')
                    continue
                n_tokens_processed += 1
                n_total_features = max(n_total_features, result['n_features'])
                all_significant.extend(result['significant_results'])
                all_features.update(result['features'])
                if best_result is None or result['n_bars'] > best_result['n_bars']:
                    best_result = result
                print(f'  {token}: {result["n_bars"]} bars, '
                      f'{result["n_features"]} features, '
                      f'{len(result["significant_results"])} significant')

        if not all_significant:
            print('\nNo significant signals found across any token.')
            return

        # Deduplicate: group by feature name, keep best IC across tokens/horizons
        best_by_feature = {}
        for r in all_significant:
            key = (r['feature'], r['horizon'])
            if key not in best_by_feature or abs(r['mean_ic']) > abs(best_by_feature[key]['mean_ic']):
                best_by_feature[key] = r

        deduped = sorted(best_by_feature.values(),
                         key=lambda r: abs(r['mean_ic']), reverse=True)

        # Correlation clustering to remove redundant signals
        sig_feature_names = list(set(r['feature'] for r in deduped))
        if len(sig_feature_names) > 1 and all_features:
            corr_matrix, ordered_names = compute_feature_correlation_matrix(
                all_features, sig_feature_names)

            results_by_feature = {}
            for r in deduped:
                fn = r['feature']
                if fn not in results_by_feature or abs(r['mean_ic']) > abs(results_by_feature[fn]['mean_ic']):
                    results_by_feature[fn] = r

            selected_names = set(cluster_and_select(
                corr_matrix, ordered_names, results_by_feature, cfg.max_corr))

            # Filter deduped to only selected
            deduped = [r for r in deduped if r['feature'] in selected_names]

        # Per-regime orthogonal selection
        regime_signals = {}
        if all_features:
            regime_signals = select_regime_orthogonal(
                deduped, all_features, cfg.top_per_regime, cfg.max_corr)

        # Save outputs
        save_signal_catalog(deduped, cfg)
        save_rankings_csv(deduped, cfg)
        if regime_signals:
            save_regime_signals(regime_signals, cfg)

        # Print summary
        print_summary(deduped, regime_signals, n_tokens_processed, n_total_features)

        # Temporal analysis (uses longest token's data for feature arrays)
        if cfg.analyze and best_result is not None and deduped:
            analysis = run_full_analysis(
                best_result['features'], best_result['close'],
                best_result['regime_1h'], best_result['idx_1h'],
                deduped, cfg)
            save_analysis(analysis, cfg)
            print_analysis_summary(analysis)

    elapsed = time.time() - t0
    print(f'\nCompleted in {elapsed:.1f}s ({n_tokens_processed} tokens processed)')


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description='Signal Discovery Engine — find predictive indicator interactions')
    parser.add_argument('--market', default='perp', choices=['perp', 'spot', 'combined'],
                        help='Market type (default: perp)')
    parser.add_argument('--tokens', nargs='+', default=None,
                        help='Specific tokens to analyze')
    parser.add_argument('--workers', type=int, default=4,
                        help='Parallel workers (default: 4)')
    parser.add_argument('--quick', action='store_true',
                        help='Quick mode: 5 tokens only')
    parser.add_argument('--per-token', action='store_true',
                        help='Generate per-token signal catalogs')
    parser.add_argument('--primary-horizon', type=int, default=24,
                        help='Primary horizon for per-token mode (default: 24)')
    parser.add_argument('--horizons', nargs='+', type=int, default=None,
                        help='Forward return horizons in hours')
    parser.add_argument('--min-bars', type=int, default=8760,
                        help='Minimum 1h bars required (default: 8760 = 1 year)')
    parser.add_argument('--fdr-alpha', type=float, default=0.05,
                        help='FDR alpha level (default: 0.05)')
    parser.add_argument('--max-corr', type=float, default=0.7,
                        help='Max correlation for dedup (default: 0.7)')
    parser.add_argument('--no-analyze', action='store_true',
                        help='Skip temporal analysis (faster)')
    parser.add_argument('--output-dir', default='outputs/signal_discovery',
                        help='Output directory')

    args = parser.parse_args()

    cfg = DiscoveryConfig(
        market=args.market,
        tokens=args.tokens,
        min_bars=args.min_bars,
        horizons=args.horizons or [1, 4, 24, 72, 168],
        workers=args.workers,
        quick=args.quick,
        per_token=args.per_token,
        primary_horizon=args.primary_horizon,
        fdr_alpha=args.fdr_alpha,
        max_corr=args.max_corr,
        analyze=not args.no_analyze,
        output_dir=args.output_dir,
    )

    run_discovery(cfg)


if __name__ == '__main__':
    main()
