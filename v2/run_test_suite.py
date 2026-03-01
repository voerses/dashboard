"""
Strategy Test Suite — runs any combination of strategies and logs results.
Does NOT modify any strategy code. Reads from the strategy modules and the
existing mtf_strategy_v2.py backtesting engine.

Usage:
    python run_test_suite.py                          # Run all strategies
    python run_test_suite.py --strategy s01 s04       # Run specific strategies
    python run_test_suite.py --tokens SUI TRX BONK    # Specific tokens
    python run_test_suite.py --cpcv-only              # Only CPCV-robust tokens
    python run_test_suite.py --compare                # Side-by-side comparison
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import argparse
import json
import numpy as np
import pandas as pd
import time
from datetime import datetime
from pathlib import Path

from liquid_universe import LIQUID_TOKENS, TIER1, TIER2, TIER3, get_tier
from mtf_strategy_v2 import backtest_token, aggregate_to_timeframe

# CPCV-robust tokens (PBO < 40%)
CPCV_ROBUST_TOKENS = [
    'PENGU', 'SUI', 'OM', 'TRX', 'DOT', 'AVAX',
    'BONK', 'FIL', 'FLOKI', 'DENT', 'ZRO'
]


def load_enriched_daily():
    path = 'real_data/all_tokens_enriched.parquet'
    if not os.path.exists(path):
        return None
    df = pd.read_parquet(path)
    df['date'] = pd.to_datetime(df['date'])
    df = df.set_index('date')
    return df


def run_strategy(strategy_key, tokens, capital=200_000, enriched=None):
    """Run a single strategy across all tokens. Returns results dict."""
    results = {}

    for ticker in tokens:
        h1_path = f'real_data/1h_cache/{ticker}_1h.parquet'
        if not os.path.exists(h1_path):
            continue
        df_1h = pd.read_parquet(h1_path)
        if len(df_1h) < 500:
            continue

        if strategy_key == 's01_dual_momentum':
            r = backtest_token(ticker, df_1h, capital=capital,
                             strategies=('dual_momentum',))
        elif strategy_key == 's02_mean_reversion':
            r = backtest_token(ticker, df_1h, capital=capital,
                             strategies=('mean_reversion',))
        elif strategy_key == 's03_vol_breakout':
            r = backtest_token(ticker, df_1h, capital=capital,
                             strategies=('vol_breakout',))
        elif strategy_key == 's04_v3_contrarian':
            from strategy_comparison import backtest_prior_v3_contrarian
            df_daily = aggregate_to_timeframe(df_1h, hours=24)
            r = backtest_prior_v3_contrarian(ticker, df_daily, enriched, capital=capital)
        elif strategy_key == 's05_vpin_enhanced':
            from strategy_comparison import backtest_token_vpin_enhanced
            r = backtest_token_vpin_enhanced(ticker, df_1h, enriched, capital=capital)
        elif strategy_key == 's06_v2_daily_momentum':
            from strategy_comparison import backtest_prior_v2_momentum
            df_daily = aggregate_to_timeframe(df_1h, hours=24)
            r = backtest_prior_v2_momentum(ticker, df_daily, capital=capital)
        elif strategy_key == 'combo_dm_mr':
            r = backtest_token(ticker, df_1h, capital=capital,
                             strategies=('dual_momentum', 'mean_reversion'))
        elif strategy_key == 'combo_all3':
            r = backtest_token(ticker, df_1h, capital=capital,
                             strategies=('dual_momentum', 'vol_breakout', 'mean_reversion'))
        else:
            print(f"  Unknown strategy: {strategy_key}")
            return results

        if r is not None:
            results[ticker] = r

    return results


def aggregate_results(results, capital=200_000, label=''):
    """Compute portfolio-level metrics from token results."""
    if not results:
        return {'label': label, 'total_pnl': 0, 'n_tokens': 0}

    total_pnl = sum(r['equity'] - capital for r in results.values())
    total_trades = sum(r['n_trades'] for r in results.values())
    profitable = sum(1 for r in results.values() if r['equity'] > capital)

    all_trades = []
    for r in results.values():
        all_trades.extend(r.get('trades', []))

    wins = [t for t in all_trades if t['pnl'] > 0]
    losers = [t for t in all_trades if t['pnl'] <= 0]
    avg_win = np.mean([t['pnl'] for t in wins]) if wins else 0
    avg_loss = abs(np.mean([t['pnl'] for t in losers])) if losers else 1

    years = 2.0  # ~2 years of data
    return {
        'label': label,
        'total_pnl': total_pnl,
        'annual_pnl': total_pnl / years,
        'annual_pct': (total_pnl / capital) / years * 100,
        'n_tokens': len(results),
        'n_trades': total_trades,
        'profitable': profitable,
        'win_rate': len(wins) / max(len(all_trades), 1) * 100,
        'payoff_ratio': avg_win / max(avg_loss, 1),
        'avg_win': avg_win,
        'avg_loss': avg_loss,
    }


def log_result(result, strategy_key, token_set, output_dir='results'):
    """Save result to JSON log file."""
    os.makedirs(output_dir, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    fname = f'{output_dir}/{strategy_key}_{token_set}_{timestamp}.json'

    log_entry = {
        'timestamp': datetime.now().isoformat(),
        'strategy': strategy_key,
        'token_set': token_set,
        **{k: v for k, v in result.items() if k != 'label'},
    }
    with open(fname, 'w') as f:
        json.dump(log_entry, f, indent=2, default=str)
    return fname


def print_result(agg, verbose=True):
    """Pretty-print aggregated results."""
    print(f"  {agg['label']:45s} "
          f"PnL=${agg['total_pnl']:>+10,.0f}  "
          f"Annual=${agg['annual_pnl']:>+8,.0f}/yr ({agg['annual_pct']:>+5.1f}%)  "
          f"Trades={agg['n_trades']:>5d}  "
          f"WR={agg['win_rate']:>4.0f}%  "
          f"Payoff={agg['payoff_ratio']:>5.2f}x  "
          f"Tokens={agg['profitable']}/{agg['n_tokens']}")


def verify_data_integrity():
    """Verify all data files exist and are intact."""
    print("=== DATA INTEGRITY CHECK ===")
    issues = []

    # 1H cache
    h1_files = list(Path('real_data/1h_cache').glob('*.parquet'))
    print(f"  1H cache: {len(h1_files)} files", end='')
    if len(h1_files) < 49:
        issues.append(f"Expected 49 1H files, found {len(h1_files)}")
        print(f" WARNING: expected 49")
    else:
        print(" OK")

    # 4H cache
    h4_files = list(Path('real_data/4h_cache').glob('*.parquet'))
    print(f"  4H cache: {len(h4_files)} files", end='')
    if len(h4_files) < 49:
        issues.append(f"Expected 49 4H files, found {len(h4_files)}")
        print(f" WARNING: expected 49")
    else:
        print(" OK")

    # 1M cache
    m1_files = list(Path('real_data/1m_cache').glob('*.parquet'))
    print(f"  1M cache: {len(m1_files)} files", end='')
    if len(m1_files) < 49:
        issues.append(f"Expected 49 1M files, found {len(m1_files)}")
        print(f" WARNING: expected 49")
    else:
        print(" OK")

    # Enriched
    enr_path = Path('real_data/all_tokens_enriched.parquet')
    print(f"  Enriched: {'exists' if enr_path.exists() else 'MISSING'}", end='')
    if not enr_path.exists():
        issues.append("Enriched parquet missing")
        print(" WARNING")
    else:
        df = pd.read_parquet(enr_path)
        print(f" ({df.shape[0]:,} rows, {df['ticker'].nunique()} tokens) OK")

    # Daily CSVs
    daily_files = list(Path('real_data').glob('*_daily.csv'))
    print(f"  Daily CSVs: {len(daily_files)} files", end='')
    if len(daily_files) < 49:
        issues.append(f"Expected 57 daily CSVs, found {len(daily_files)}")
        print(f" (expected ~57)")
    else:
        print(" OK")

    if issues:
        print(f"\n  ISSUES: {len(issues)}")
        for i in issues:
            print(f"    - {i}")
    else:
        print(f"\n  ALL DATA INTACT")

    return len(issues) == 0


def main():
    parser = argparse.ArgumentParser(description='Strategy Test Suite')
    parser.add_argument('--strategy', nargs='+',
                        help='Strategy keys to test (e.g. s01 s04)')
    parser.add_argument('--tokens', nargs='+',
                        help='Specific tokens to test')
    parser.add_argument('--cpcv-only', action='store_true',
                        help='Only test CPCV-robust tokens')
    parser.add_argument('--compare', action='store_true',
                        help='Run comparison across all strategies')
    parser.add_argument('--capital', type=int, default=200_000)
    parser.add_argument('--verify', action='store_true',
                        help='Verify data integrity')
    args = parser.parse_args()

    # Always verify data first
    data_ok = verify_data_integrity()
    if not data_ok:
        print("\nWARNING: Data integrity issues detected. Proceeding anyway...")

    enriched = load_enriched_daily()

    # Determine tokens
    if args.tokens:
        tokens = args.tokens
        token_set = 'custom'
    elif args.cpcv_only:
        tokens = CPCV_ROBUST_TOKENS
        token_set = 'cpcv_robust'
    else:
        tokens = LIQUID_TOKENS
        token_set = 'all'

    if args.verify:
        return

    # Determine strategies
    if args.compare:
        strategy_keys = [
            's01_dual_momentum', 's02_mean_reversion', 's03_vol_breakout',
            's04_v3_contrarian', 'combo_dm_mr', 'combo_all3'
        ]
    elif args.strategy:
        strategy_keys = []
        for s in args.strategy:
            # Allow partial match: "s01" matches "s01_dual_momentum"
            matches = [k for k in ['s01_dual_momentum', 's02_mean_reversion',
                                    's03_vol_breakout', 's04_v3_contrarian',
                                    's05_vpin_enhanced', 's06_v2_daily_momentum',
                                    'combo_dm_mr', 'combo_all3']
                       if s in k]
            strategy_keys.extend(matches if matches else [s])
    else:
        strategy_keys = ['combo_dm_mr']  # default: best known config

    print(f"\n{'='*100}")
    print(f"STRATEGY TEST SUITE")
    print(f"{'='*100}")
    print(f"  Tokens: {len(tokens)} ({token_set})")
    print(f"  Strategies: {', '.join(strategy_keys)}")
    print(f"  Capital: ${args.capital:,.0f}")
    print()

    all_agg = []
    for sk in strategy_keys:
        t0 = time.time()
        results = run_strategy(sk, tokens, capital=args.capital, enriched=enriched)
        elapsed = time.time() - t0

        label = f"{sk} ({token_set})"
        agg = aggregate_results(results, capital=args.capital, label=label)
        agg['elapsed'] = elapsed
        all_agg.append(agg)

        print_result(agg)

        # Log to file
        log_file = log_result(agg, sk, token_set)

    # Summary if multiple strategies
    if len(all_agg) > 1:
        print(f"\n{'='*100}")
        print("COMPARISON (sorted by annual PnL)")
        print(f"{'='*100}")
        for agg in sorted(all_agg, key=lambda x: -x['annual_pnl']):
            print_result(agg)

    print(f"\nResults logged to results/")


if __name__ == '__main__':
    main()
