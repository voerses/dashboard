"""
V3 Signal Agreement Gating — Multi-Strategy Entry Confirmation
================================================================

Tests whether combining entry signals from multiple strategies via agreement
gating improves hit rate and risk-adjusted returns.

Modes:
  AND gate:  Enter only when ALL strategies agree (strictest)
  N-of-M:   Enter when >= N out of M strategies agree
  ANY gate:  Enter when ANY strategy agrees (union — most permissive)

Usage:
    python v3/signal_agreement.py --strategies s11 s09 --workers 4
    python v3/signal_agreement.py --strategies s11 s09 s18 --mode 2-of-3 --workers 4
    python v3/signal_agreement.py --strategies s11 s09 --universe liquid --compare
"""

import sys
import os
import time
import json
import argparse
import numpy as np
import pandas as pd
import importlib.util
from dataclasses import dataclass, asdict
from typing import Optional, List, Dict, Tuple
from datetime import datetime
from concurrent.futures import ProcessPoolExecutor, as_completed

# ---------------------------------------------------------------------------
# Import reusable V3 modules
# ---------------------------------------------------------------------------
_v3_dir = os.path.dirname(os.path.abspath(__file__))


def _load_v3(name):
    full_name = f'v3_{name}'
    if full_name in sys.modules:
        return sys.modules[full_name]
    spec = importlib.util.spec_from_file_location(full_name, os.path.join(_v3_dir, f'{name}.py'))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = mod
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_engine_mod = _load_v3('engine')
_universe_mod = _load_v3('universe')
_portfolio_mod = _load_v3('portfolio')

resolve_universe = _universe_mod.resolve_universe
get_fee_rate = _universe_mod.get_fee_rate
compute_portfolio_metrics = _portfolio_mod.compute_portfolio_metrics
simulate_portfolio = _portfolio_mod.simulate_portfolio
PortfolioMetrics = _portfolio_mod.PortfolioMetrics
PortfolioConfig = _portfolio_mod.PortfolioConfig
Engine = _engine_mod.Engine


# ---------------------------------------------------------------------------
# Strategy Resolution
# ---------------------------------------------------------------------------

def resolve_strategy_path(name: str) -> Optional[str]:
    """Resolve strategy name (e.g. 's11') to absolute file path."""
    import glob as _glob
    strat_dir = os.path.join(os.path.dirname(_v3_dir), 'strategies')
    candidates = [
        os.path.join(strat_dir, f'{name}.py'),
        os.path.join(strat_dir, f'{name}_*.py'),
    ]
    for c in candidates:
        matches = _glob.glob(c)
        if matches:
            return os.path.abspath(matches[0])
    return None


def load_strategy_fn(path: str):
    """Load a strategy function from file path."""
    spec = importlib.util.spec_from_file_location('strat_mod', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.strategy


# ---------------------------------------------------------------------------
# Core: Signal Agreement per Token
# ---------------------------------------------------------------------------

def _get_agreement_trades(args):
    """Run multiple strategies on one token, combine signals, simulate.

    Worker function for parallel execution.
    """
    ticker, strategy_paths, data_dir, market, capital, exchange, n_required, base_strategy_idx = args

    v3_dir = os.path.dirname(os.path.abspath(__file__))
    if v3_dir not in sys.path:
        sys.path.insert(0, v3_dir)

    eng_mod = _load_v3('engine')
    EngineLocal = eng_mod.Engine

    effective_market = market if market != 'combined' else 'spot'
    h1_path = os.path.join(data_dir, effective_market, f'1h_cache/{ticker}_1h.parquet')
    if not os.path.exists(h1_path):
        return ticker, None

    df_1h = pd.read_parquet(h1_path)
    if len(df_1h) < 2000:
        return ticker, None

    engine = EngineLocal(data_dir=data_dir, market=effective_market,
                         capital=capital, exchange=exchange)
    ctx = engine._build_context(ticker, df_1h)
    if ctx is None:
        return ticker, None

    n = len(ctx.ind_1h['close'])

    # Run all strategies on same context
    results = []
    for spath in strategy_paths:
        try:
            strat_fn = load_strategy_fn(spath)
            r = strat_fn(ctx)
            results.append(r)
        except Exception:
            return ticker, None

    if len(results) != len(strategy_paths):
        return ticker, None

    # Combine entry masks via N-of-M voting
    entry_masks = [np.asarray(r.entry_mask, dtype=np.bool_) for r in results]
    stacked = np.stack(entry_masks, axis=0)  # shape: (M, n_bars)
    n_agree = stacked.sum(axis=0)            # shape: (n_bars,)
    combined_entry = n_agree >= n_required

    # Use base strategy's trade parameters
    base = results[base_strategy_idx]
    base.entry_mask = combined_entry

    # Apply walk-forward masking (same as portfolio.py)
    train_bars = 365 * 24
    recal_bars = 90 * 24
    purge_bars = 5 * 24

    if n <= train_bars + purge_bars:
        return ticker, None

    masked_entry = base.entry_mask.copy()
    masked_entry[:train_bars] = False

    recal_point = train_bars
    while recal_point < n:
        purge_end = min(recal_point + purge_bars, n)
        masked_entry[recal_point:purge_end] = False
        recal_point += recal_bars

    base.entry_mask = masked_entry
    trades, final_equity = engine._simulate(ctx, base)

    if not trades:
        return ticker, None

    # Add timestamps and token name
    idx = ctx.idx_1h
    for t in trades:
        eb = t['entry_bar']
        xb = t['exit_bar']
        t['token'] = ticker
        t['entry_time'] = idx[min(eb, len(idx) - 1)]
        t['exit_time'] = idx[min(xb, len(idx) - 1)]

    return ticker, trades


def extract_agreement_trades(
    strategy_paths: List[str],
    tokens: List[str],
    config: PortfolioConfig,
    n_required: int = None,
    base_strategy_idx: int = 0,
) -> Dict[str, List[Dict]]:
    """Extract trades using signal agreement across multiple strategies."""
    if n_required is None:
        n_required = len(strategy_paths)  # AND gate by default

    all_trades = {}
    args_list = [
        (tk, strategy_paths, config.data_dir, config.market,
         config.capital, config.exchange, n_required, base_strategy_idx)
        for tk in tokens
    ]

    if config.workers > 1 and len(tokens) > 1:
        with ProcessPoolExecutor(max_workers=config.workers) as executor:
            futures = {executor.submit(_get_agreement_trades, a): a[0] for a in args_list}
            for future in as_completed(futures):
                try:
                    tk, trades = future.result()
                    if trades:
                        all_trades[tk] = trades
                except Exception:
                    pass
    else:
        for a in args_list:
            try:
                tk, trades = _get_agreement_trades(a)
                if trades:
                    all_trades[tk] = trades
            except Exception:
                pass

    return all_trades


# ---------------------------------------------------------------------------
# Pipeline: Run one variant (agreement or solo) through portfolio sim
# ---------------------------------------------------------------------------

def _run_variant(label, strategy_paths, tokens, config, n_required, base_idx=0):
    """Run a single variant and return portfolio results."""
    t0 = time.time()

    token_trades = extract_agreement_trades(
        strategy_paths, tokens, config,
        n_required=n_required, base_strategy_idx=base_idx,
    )

    total_trades = sum(len(t) for t in token_trades.values())
    if total_trades == 0:
        return None

    fee_rate = get_fee_rate(config.exchange, config.market, 'taker')
    portfolio_eq, accepted, skipped, info = simulate_portfolio(
        token_trades, config.capital, config.max_token_pct,
        config.min_position_usd, mtm=config.mtm,
        dynamic_concentration=config.dynamic_concentration,
        fee_rate=fee_rate,
    )

    if len(portfolio_eq) < 30:
        return None

    metrics = compute_portfolio_metrics(portfolio_eq, accepted, skipped, info)
    elapsed = time.time() - t0

    return {
        'label': label,
        'equity': portfolio_eq,
        'accepted': accepted,
        'skipped': skipped,
        'metrics': metrics,
        'info': info,
        'n_tokens': len(token_trades),
        'total_trades': total_trades,
        'elapsed': elapsed,
    }


# ---------------------------------------------------------------------------
# Comparison Report
# ---------------------------------------------------------------------------

def print_comparison(results: List[Dict], config: PortfolioConfig):
    """Print side-by-side comparison of all variants."""
    fee_rate = get_fee_rate(config.exchange, config.market, 'taker')

    print(f"\n{'='*100}")
    print(f"SIGNAL AGREEMENT GATING — COMPARISON")
    print(f"  Capital: ${config.capital:,.0f} | Market: {config.market} | "
          f"Exchange: {config.exchange} | Fee: {fee_rate*100:.2f}%/side")
    print(f"{'='*100}\n")

    # Header
    labels = [r['label'] for r in results]
    col_w = max(20, max(len(l) for l in labels) + 2)
    header = f"{'Metric':<25}" + "".join(f"{l:>{col_w}}" for l in labels)
    print(header)
    print("-" * len(header))

    def row(name, key, fmt="+.2f"):
        vals = []
        for r in results:
            m = r['metrics']
            v = getattr(m, key, None) if hasattr(m, key) else r.get(key)
            if v is None:
                vals.append("N/A")
            else:
                vals.append(f"{v:{fmt}}")
        print(f"{name:<25}" + "".join(f"{v:>{col_w}}" for v in vals))

    # Performance
    row("Total Return %", "total_return_pct", "+.1f")
    row("Ann. Return %", "annualized_return_pct", "+.1f")
    row("Sharpe", "sharpe_ratio", "+.2f")
    row("Sortino", "sortino_ratio", "+.2f")
    row("Calmar", "calmar_ratio", "+.2f")
    print()

    # Risk
    row("Max Drawdown %", "max_drawdown_pct", ".1f")
    row("Max DD Days", "max_drawdown_duration_days", "d")
    print()

    # Trade quality
    def row_info(name, key, fmt=".1f"):
        vals = []
        for r in results:
            v = r.get(key)
            if v is None:
                v = r['metrics']
                v = getattr(v, key, None) if hasattr(v, key) else None
            if v is None:
                vals.append("N/A")
            else:
                vals.append(f"{v:{fmt}}")
        print(f"{name:<25}" + "".join(f"{v:>{col_w}}" for v in vals))

    row_info("Tokens traded", "n_tokens", "d")
    row("Total trades", "accepted_trades", "d")
    row("Win rate %", "win_rate_pct", ".1f")
    row("Profit factor", "profit_factor", ".2f")
    row("Avg trade PnL $", "avg_trade_pnl", ",.0f")
    print()

    # Capital
    row("Peak concurrent", "peak_concurrent_positions", "d")
    row("Skip rate %", "skip_rate_pct", ".1f")
    print()

    # Correlation between variants
    if len(results) >= 2:
        print("Daily Return Correlation Between Variants:")
        equities = {r['label']: r['equity'] for r in results}
        combined = pd.DataFrame(equities).dropna(how='all').ffill().dropna()
        returns = combined.pct_change().dropna().replace([np.inf, -np.inf], 0)
        corr = returns.corr()
        for i in range(len(results)):
            for j in range(i + 1, len(results)):
                li, lj = results[i]['label'], results[j]['label']
                c = corr.loc[li, lj]
                print(f"  {li} vs {lj}: {c:+.4f}")
        print()

    # Trade reduction analysis
    if len(results) >= 2:
        base = results[0]
        print("Trade Reduction vs Base:")
        for r in results[1:]:
            base_trades = base['metrics'].accepted_trades
            this_trades = r['metrics'].accepted_trades
            if base_trades > 0:
                reduction = (1 - this_trades / base_trades) * 100
                sharpe_delta = r['metrics'].sharpe_ratio - base['metrics'].sharpe_ratio
                wr_delta = r['metrics'].win_rate_pct - base['metrics'].win_rate_pct
                print(f"  {r['label']}: {reduction:+.0f}% trades, "
                      f"{sharpe_delta:+.2f} Sharpe, {wr_delta:+.1f}pp win rate")
        print()


# ---------------------------------------------------------------------------
# Main Pipeline
# ---------------------------------------------------------------------------

def run_signal_agreement(
    strategy_names: List[str],
    tokens: List[str],
    config: PortfolioConfig,
    n_required: Optional[int] = None,
    compare: bool = True,
    verbose: bool = True,
) -> Dict:
    """Run signal agreement analysis.

    If compare=True, runs each strategy solo AND the combined version.
    """
    # Resolve strategy paths
    strategy_paths = []
    for name in strategy_names:
        path = resolve_strategy_path(name)
        if path is None:
            print(f"ERROR: Strategy not found: {name}")
            return {}
        strategy_paths.append(path)
        if verbose:
            print(f"  {name} -> {os.path.basename(path)}")

    if n_required is None:
        n_required = len(strategy_paths)  # AND gate

    mode_label = (f"{n_required}-of-{len(strategy_paths)}"
                  if n_required < len(strategy_paths) else "AND")
    combined_label = f"gate({mode_label})"

    if verbose:
        print(f"\nMode: {mode_label} agreement | Tokens: {len(tokens)} | "
              f"Workers: {config.workers}")

    results = []

    # Run each strategy solo for comparison
    if compare:
        for i, (name, path) in enumerate(zip(strategy_names, strategy_paths)):
            if verbose:
                print(f"\nRunning {name} solo...")
            r = _run_variant(name, [path], tokens, config, n_required=1, base_idx=0)
            if r:
                results.append(r)
                if verbose:
                    m = r['metrics']
                    print(f"  {name}: {m.accepted_trades} trades, "
                          f"Sharpe={m.sharpe_ratio:+.2f}, "
                          f"WR={m.win_rate_pct:.1f}%, "
                          f"PF={m.profit_factor:.2f}")

    # Run combined
    if verbose:
        print(f"\nRunning {combined_label} ({' + '.join(strategy_names)})...")
    r_combined = _run_variant(combined_label, strategy_paths, tokens, config,
                              n_required=n_required, base_idx=0)
    if r_combined:
        results.append(r_combined)
        if verbose:
            m = r_combined['metrics']
            print(f"  {combined_label}: {m.accepted_trades} trades, "
                  f"Sharpe={m.sharpe_ratio:+.2f}, "
                  f"WR={m.win_rate_pct:.1f}%, "
                  f"PF={m.profit_factor:.2f}")

    if not results:
        print("ERROR: No valid results")
        return {}

    # Print comparison
    if verbose:
        print_comparison(results, config)

    # Save results
    results_dir = os.path.join(os.path.dirname(_v3_dir), 'results')
    os.makedirs(results_dir, exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    strat_str = '_'.join(strategy_names)
    out_path = os.path.join(results_dir, f'signal_agreement_{strat_str}_{mode_label}_{ts}.json')

    save_data = {
        'strategy': 'signal_agreement',
        'strategies': strategy_names,
        'mode': mode_label,
        'n_required': n_required,
        'variants': [],
        'timestamp': ts,
    }
    for r in results:
        save_data['variants'].append({
            'label': r['label'],
            'metrics': asdict(r['metrics']),
            'info': {k: v for k, v in r['info'].items() if not isinstance(v, (pd.Series, pd.DataFrame))},
            'n_tokens': r['n_tokens'],
            'total_trades': r['total_trades'],
            'elapsed': r['elapsed'],
        })

    with open(out_path, 'w') as f:
        json.dump(save_data, f, indent=2, default=str)
    if verbose:
        print(f"Results saved to {out_path}")

    return {
        'results': results,
        'save_path': out_path,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description='V3 Signal Agreement Gating — Multi-Strategy Entry Confirmation')
    parser.add_argument('--strategies', nargs='+', required=True,
                        help='Strategy names to combine (e.g. s11 s09)')
    parser.add_argument('--mode', default=None,
                        help='Agreement mode: "and" (all agree), "any", or "N-of-M" (e.g. "2-of-3")')
    parser.add_argument('--capital', type=float, default=200_000)
    parser.add_argument('--max-weight', type=float, default=0.15)
    parser.add_argument('--universe', default='liquid',
                        choices=['all', 'filtered', 'liquid'])
    parser.add_argument('--tokens', nargs='+', help='Specific tokens')
    parser.add_argument('--market', default='spot', choices=['spot', 'perp'])
    parser.add_argument('--exchange', default='binance')
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--compare', action='store_true', default=True,
                        help='Compare combined vs each solo (default: True)')
    parser.add_argument('--no-compare', dest='compare', action='store_false',
                        help='Skip solo comparison')
    args = parser.parse_args()

    # Parse mode
    n_strategies = len(args.strategies)
    if args.mode is None or args.mode.lower() == 'and':
        n_required = n_strategies
    elif args.mode.lower() == 'any':
        n_required = 1
    elif '-of-' in args.mode:
        n_required = int(args.mode.split('-of-')[0])
    else:
        n_required = int(args.mode)

    # Resolve tokens
    if args.tokens:
        tokens = args.tokens
    else:
        tokens = resolve_universe(args.universe, market=args.market, verbose=True)
    print(f"Universe: {len(tokens)} tokens")

    config = PortfolioConfig(
        capital=args.capital,
        max_token_pct=args.max_weight,
        data_dir='data',
        market=args.market,
        exchange=args.exchange,
        workers=args.workers,
    )

    run_signal_agreement(
        args.strategies, tokens, config,
        n_required=n_required,
        compare=args.compare,
    )


if __name__ == '__main__':
    main()
