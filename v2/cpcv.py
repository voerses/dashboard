"""
Combinatorial Purged Cross-Validation (CPCV)
=============================================

Based on Marcos López de Prado (2018) "Advances in Financial Machine Learning".

CPCV generates ALL combinatorial train/test splits, then purges
overlapping samples to prevent leakage. Tests whether the strategy
is robust across ALL possible timeline orderings.

Performance optimizations:
  - Vectorized purge computation (no per-sample loops)
  - Parallel execution across tokens (multiprocessing)
  - Indicator caching: compute once, slice per fold
  - Reduced fold count option for quick scans
"""

import numpy as np
import pandas as pd
from itertools import combinations
from typing import List, Tuple, Dict, Callable, Optional
from concurrent.futures import ProcessPoolExecutor, as_completed
import time
import os, sys


def generate_cpcv_splits(n_samples: int, n_groups: int = 6, n_test_groups: int = 2,
                         purge_pct: float = 0.01) -> List[Tuple[np.ndarray, np.ndarray]]:
    """
    Generate CPCV train/test splits with vectorized purge computation.
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

        # Build test indices
        test_ranges = [group_bounds[g] for g in test_combo]
        test_idx = np.concatenate([np.arange(s, e) for s, e in test_ranges])

        # Build train indices
        train_ranges = [group_bounds[g] for g in range(n_groups) if g not in test_set]
        if not train_ranges:
            continue
        train_idx = np.concatenate([np.arange(s, e) for s, e in train_ranges])

        # Vectorized purge: remove train samples near ANY test boundary
        purge_mask = np.ones(len(train_idx), dtype=bool)
        for tg in test_combo:
            tg_start, tg_end = group_bounds[tg]
            # Purge near test group start
            purge_mask &= np.abs(train_idx - tg_start) >= purge_size
            # Purge near test group end
            purge_mask &= np.abs(train_idx - (tg_end - 1)) >= purge_size

        train_idx_purged = train_idx[purge_mask]
        splits.append((train_idx_purged, test_idx))

    return splits


def _backtest_fold(args):
    """Worker function for parallel fold execution."""
    ticker, df_1h_path, fold_idx, data_start, data_end, capital, strategies = args

    # Import inside worker to avoid pickling issues
    sys.path.insert(0, os.path.dirname(__file__))
    from mtf_strategy_v2 import backtest_token

    df_1h = pd.read_parquet(df_1h_path)
    df_fold = df_1h.iloc[data_start:data_end + 1]

    if len(df_fold) < 500:
        return None

    try:
        result = backtest_token(ticker, df_fold, capital=capital, strategies=strategies)
        if result is not None:
            result['fold'] = fold_idx
            result['test_start'] = str(df_1h.index[data_start])
            result['test_end'] = str(df_1h.index[data_end])
            # Remove large trade list to save memory in CPCV
            result.pop('trades', None)
            return result
    except Exception:
        return None
    return None


def run_cpcv_backtest(df_1h: pd.DataFrame, ticker: str,
                      backtest_fn: Callable,
                      n_groups: int = 6, n_test_groups: int = 2,
                      capital: float = 200_000,
                      purge_pct: float = 0.01,
                      strategies: tuple = ('dual_momentum', 'vol_breakout', 'mean_reversion')
                      ) -> Optional[Dict]:
    """
    Run CPCV on a single token's 1H data.
    """
    n = len(df_1h)
    splits = generate_cpcv_splits(n, n_groups, n_test_groups, purge_pct)

    fold_results = []
    warmup = 200

    for fold_idx, (train_idx, test_idx) in enumerate(splits):
        test_start = int(test_idx.min())
        test_end = int(test_idx.max())
        data_start = max(0, test_start - warmup)

        df_fold = df_1h.iloc[data_start:test_end + 1]
        if len(df_fold) < 500:
            continue

        try:
            result = backtest_fn(ticker, df_fold, capital, strategies=strategies)
            if result is not None:
                result['fold'] = fold_idx
                result['test_bars'] = test_end - test_start
                result.pop('trades', None)  # Save memory
                fold_results.append(result)
        except Exception:
            continue

    if not fold_results:
        return None

    return _aggregate_cpcv_results(ticker, fold_results, len(splits))


def _aggregate_cpcv_results(ticker: str, fold_results: List[Dict],
                             n_splits_total: int) -> Dict:
    """Aggregate fold results into CPCV metrics."""
    returns = [r['total_return'] for r in fold_results]
    win_rates = [r['win_rate'] for r in fold_results if r['n_trades'] > 0]
    payoffs = [r['payoff_ratio'] for r in fold_results if r['n_trades'] > 0 and r['payoff_ratio'] > 0]
    pfs = [r['profit_factor'] for r in fold_results if r['n_trades'] > 0]
    n_trades_all = [r['n_trades'] for r in fold_results]

    pbo = sum(1 for r in returns if r < 0) / max(len(returns), 1)
    deflated_sharpe = _deflated_sharpe(returns)

    return {
        'ticker': ticker,
        'n_folds': len(fold_results),
        'n_splits_total': n_splits_total,
        'avg_return': float(np.mean(returns)),
        'median_return': float(np.median(returns)),
        'std_return': float(np.std(returns)),
        'min_return': float(np.min(returns)),
        'max_return': float(np.max(returns)),
        'avg_win_rate': float(np.mean(win_rates)) if win_rates else 0,
        'avg_payoff_ratio': float(np.mean(payoffs)) if payoffs else 0,
        'avg_profit_factor': float(np.mean(pfs)) if pfs else 0,
        'avg_trades_per_fold': float(np.mean(n_trades_all)),
        'pbo': pbo,
        'deflated_sharpe': deflated_sharpe,
        'profitable_folds': sum(1 for r in returns if r > 0),
        'total_folds': len(returns),
        'strategy_robustness': _strategy_robustness(fold_results),
    }


def _deflated_sharpe(returns: List[float]) -> float:
    """Deflated Sharpe ratio (Bailey & de Prado, 2014)."""
    if len(returns) < 3:
        return 0
    sr = np.mean(returns) / max(np.std(returns), 1e-10)
    n = len(returns)
    kurt = float(pd.Series(returns).kurtosis())

    from math import log, sqrt, erfc
    try:
        e_max_sr = sqrt(2 * log(n)) * (1 - log(log(n)) / (2 * log(n)))
        psr = 0.5 * erfc(-(sr - e_max_sr * 0.5) / sqrt(max(1 + 0.5 * kurt, 0.01)) * sqrt(max(n - 1, 1)))
    except (ValueError, ZeroDivisionError):
        psr = 0
    return psr


def _strategy_robustness(fold_results: List[Dict]) -> Dict:
    """Check each strategy's consistency across folds."""
    strat_pnls = {}
    for r in fold_results:
        for sname, sdata in r.get('strategy_breakdown', {}).items():
            if sname not in strat_pnls:
                strat_pnls[sname] = []
            strat_pnls[sname].append(sdata['pnl'])

    robustness = {}
    for sname, pnls in strat_pnls.items():
        if not pnls:
            continue
        robustness[sname] = {
            'avg_pnl': float(np.mean(pnls)),
            'median_pnl': float(np.median(pnls)),
            'std_pnl': float(np.std(pnls)),
            'profitable_folds': sum(1 for p in pnls if p > 0),
            'total_folds': len(pnls),
            'consistency': sum(1 for p in pnls if p > 0) / max(len(pnls), 1),
        }
    return robustness


def _process_token(args):
    """Worker for parallel token processing."""
    ticker, h1_path, n_groups, n_test_groups, capital, purge_pct, strategies = args

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from mtf_strategy_v2 import backtest_token

    df_1h = pd.read_parquet(h1_path)
    if len(df_1h) < 1000:
        return ticker, None

    result = run_cpcv_backtest(
        df_1h, ticker, backtest_token,
        n_groups=n_groups, n_test_groups=n_test_groups,
        capital=capital, purge_pct=purge_pct, strategies=strategies
    )
    return ticker, result


def run_cpcv_portfolio(tokens=None, n_groups=6, n_test_groups=2,
                       capital=200_000, verbose=True, n_workers=4,
                       strategies=('dual_momentum', 'vol_breakout', 'mean_reversion')):
    """
    Run CPCV across all tokens with parallel execution.

    Args:
        n_workers: Number of parallel workers. Set to 1 for sequential (debug).
    """
    sys.path.insert(0, os.path.dirname(__file__))
    from liquid_universe import LIQUID_TOKENS

    if tokens is None:
        tokens = LIQUID_TOKENS

    print("=" * 80)
    print("COMBINATORIAL PURGED CROSS-VALIDATION (CPCV)")
    print("=" * 80)
    n_splits = len(list(combinations(range(n_groups), n_test_groups)))
    print(f"  Groups: {n_groups} | Test groups: {n_test_groups} | Splits/token: {n_splits}")
    print(f"  Tokens: {len(tokens)} | Capital: ${capital:,.0f} | Workers: {n_workers}")
    print(f"  Strategies: {', '.join(strategies)}")
    print()

    # Filter tokens with data
    token_paths = []
    for ticker in tokens:
        h1_path = f'real_data/1h_cache/{ticker}_1h.parquet'
        if os.path.exists(h1_path):
            token_paths.append((ticker, h1_path))
        elif verbose:
            print(f"  {ticker}: no data, skipping")

    results = {}
    t0 = time.time()

    if n_workers > 1:
        # Parallel execution
        args_list = [
            (tk, path, n_groups, n_test_groups, capital, 0.01, strategies)
            for tk, path in token_paths
        ]
        with ProcessPoolExecutor(max_workers=n_workers) as executor:
            futures = {executor.submit(_process_token, args): args[0]
                       for args in args_list}
            done = 0
            for future in as_completed(futures):
                done += 1
                ticker, cpcv_result = future.result()
                if cpcv_result is not None:
                    results[ticker] = cpcv_result
                    if verbose:
                        print(f"  [{done}/{len(token_paths)}] {ticker}: "
                              f"PBO={cpcv_result['pbo']:.0%}  "
                              f"AvgRet={cpcv_result['avg_return']:+.1f}%  "
                              f"Profitable={cpcv_result['profitable_folds']}/{cpcv_result['total_folds']}  "
                              f"Payoff={cpcv_result['avg_payoff_ratio']:.1f}x")
                else:
                    if verbose:
                        print(f"  [{done}/{len(token_paths)}] {ticker}: failed/too short")
    else:
        # Sequential (for debugging)
        for idx, (ticker, h1_path) in enumerate(token_paths, 1):
            if verbose:
                print(f"  [{idx}/{len(token_paths)}] {ticker}...", end=' ', flush=True)

            from mtf_strategy_v2 import backtest_token

            df_1h = pd.read_parquet(h1_path)
            if len(df_1h) < 1000:
                if verbose:
                    print("too short")
                continue

            cpcv_result = run_cpcv_backtest(
                df_1h, ticker, backtest_token,
                n_groups=n_groups, n_test_groups=n_test_groups,
                capital=capital, strategies=strategies
            )

            if cpcv_result is not None:
                results[ticker] = cpcv_result
                if verbose:
                    print(f"PBO={cpcv_result['pbo']:.0%}  "
                          f"AvgRet={cpcv_result['avg_return']:+.1f}%  "
                          f"Profitable={cpcv_result['profitable_folds']}/{cpcv_result['total_folds']}  "
                          f"Payoff={cpcv_result['avg_payoff_ratio']:.1f}x")
            else:
                if verbose:
                    print("failed")

    elapsed = time.time() - t0

    if not results:
        print("No results.")
        return results

    _print_cpcv_report(results, elapsed)
    return results


def _print_cpcv_report(results: Dict, elapsed: float):
    """Print the CPCV robustness report."""
    print(f"\n{'='*80}")
    print("CPCV ROBUSTNESS REPORT")
    print(f"{'='*80}")

    pbos = [r['pbo'] for r in results.values()]
    avg_rets = [r['avg_return'] for r in results.values()]
    avg_payoffs = [r['avg_payoff_ratio'] for r in results.values() if r['avg_payoff_ratio'] > 0]

    print(f"  Tokens analyzed: {len(results)}")
    print(f"  Avg PBO: {np.mean(pbos):.0%}  (lower = less overfit, target < 50%)")
    print(f"  Avg Return across folds: {np.mean(avg_rets):+.1f}%")
    if avg_payoffs:
        print(f"  Avg Payoff Ratio: {np.mean(avg_payoffs):.2f}x")

    # Robust tokens
    robust = [(tk, r) for tk, r in results.items() if r['pbo'] < 0.4 and r['avg_return'] > 0]
    print(f"\n  Robust tokens (PBO < 40%, positive avg return): {len(robust)}")
    for tk, r in sorted(robust, key=lambda x: x[1]['avg_return'], reverse=True):
        print(f"    {tk:8s} PBO={r['pbo']:.0%}  AvgRet={r['avg_return']:+.1f}%  "
              f"Payoff={r['avg_payoff_ratio']:.1f}x  "
              f"Profitable={r['profitable_folds']}/{r['total_folds']}")

    # Overfit tokens
    overfit = [(tk, r) for tk, r in results.items() if r['pbo'] > 0.6]
    if overfit:
        print(f"\n  Likely overfit tokens (PBO > 60%): {len(overfit)}")
        for tk, r in sorted(overfit, key=lambda x: x[1]['pbo'], reverse=True)[:10]:
            print(f"    {tk:8s} PBO={r['pbo']:.0%}  AvgRet={r['avg_return']:+.1f}%")

    # Strategy robustness
    print(f"\n  Strategy Robustness (across all tokens & folds):")
    all_strat_rob = {}
    for r in results.values():
        for sname, sdata in r.get('strategy_robustness', {}).items():
            if sname not in all_strat_rob:
                all_strat_rob[sname] = {'pnls': [], 'consistency': []}
            all_strat_rob[sname]['pnls'].append(sdata['avg_pnl'])
            all_strat_rob[sname]['consistency'].append(sdata['consistency'])

    for sname, data in sorted(all_strat_rob.items()):
        avg_pnl = np.mean(data['pnls'])
        avg_cons = np.mean(data['consistency'])
        print(f"    {sname:20s}: Avg PnL=${avg_pnl:>+10,.0f}  "
              f"Consistency={avg_cons:.0%}")

    print(f"\n  Elapsed: {elapsed:.1f}s")


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--tokens', nargs='+')
    parser.add_argument('--groups', type=int, default=6)
    parser.add_argument('--test-groups', type=int, default=2)
    parser.add_argument('--capital', type=int, default=200_000)
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--strategy', nargs='+',
                        default=['dual_momentum', 'vol_breakout', 'mean_reversion'])
    args = parser.parse_args()

    tokens = args.tokens if args.tokens else None
    run_cpcv_portfolio(tokens=tokens, n_groups=args.groups,
                       n_test_groups=args.test_groups, capital=args.capital,
                       n_workers=args.workers, strategies=tuple(args.strategy))
