"""
V3 Strategy Correlation Matrix & Combination Analysis
======================================================

Computes pairwise correlation between strategy portfolio equity curves,
marginal Sharpe contribution, and identifies redundant vs complementary
strategies.

Usage:
    python v3/correlation.py --strategies s11 s09 s13 s21 s17 s18 --workers 4
    python v3/correlation.py --tier A --workers 4
    python v3/correlation.py --tier AB --workers 4  # Both Tier A and B
"""

import sys
import os
import re
import time
import json
import argparse
import dataclasses
import importlib.util
import numpy as np
import pandas as pd
from datetime import datetime
from typing import List, Dict, Tuple, Optional

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


_portfolio_mod = _load_v3('portfolio')
_universe_mod = _load_v3('universe')

extract_token_trades = _portfolio_mod.extract_token_trades
simulate_portfolio = _portfolio_mod.simulate_portfolio
PortfolioConfig = _portfolio_mod.PortfolioConfig
resolve_universe = _universe_mod.resolve_universe


# =============================================================================
# Strategy Resolution
# =============================================================================

def resolve_strategy_path(strategy_name: str) -> Optional[str]:
    """Resolve strategy name to file path."""
    import glob
    strat_dir = os.path.join(os.path.dirname(_v3_dir), 'strategies')
    candidates = [
        os.path.join(strat_dir, f'{strategy_name}.py'),
        os.path.join(strat_dir, f'{strategy_name}_*.py'),
    ]
    for c in candidates:
        matches = glob.glob(c)
        if matches:
            return matches[0]
    return None


def get_tier_strategies(tier: str = 'A') -> List[str]:
    """Get strategy names from the latest sweep summary."""
    results_dir = os.path.join(os.path.dirname(_v3_dir), 'results')
    sweeps = sorted(
        [f for f in os.listdir(results_dir) if f.startswith('sweep_summary_')],
        reverse=True,
    )
    if not sweeps:
        return []
    with open(os.path.join(results_dir, sweeps[0])) as f:
        data = json.load(f)

    strategies = []
    if 'A' in tier.upper():
        strategies.extend(data.get('tier_a', []))
    if 'B' in tier.upper():
        strategies.extend(data.get('tier_b', []))
    return strategies


# =============================================================================
# Strategy Market Detection
# =============================================================================

def _detect_strategy_market(strategy_path: str) -> str:
    """Detect whether a strategy targets 'combined', 'perp', or 'spot'.

    Checks for MarketType.COMBINED or PERP assignment in the strategy file.
    Returns 'combined' if found, then 'perp', else 'spot' (the default).
    """
    try:
        with open(strategy_path, 'r') as f:
            source = f.read()
        if re.search(r'MarketType\.COMBINED', source):
            return 'combined'
        if re.search(r'market_type\s*=\s*MarketType\.PERP', source):
            return 'perp'
    except (OSError, IOError):
        pass
    return 'spot'


# =============================================================================
# Equity Curve Generation (per strategy)
# =============================================================================

def generate_strategy_equity(
    strategy_name: str,
    tokens: List[str],
    config: PortfolioConfig,
) -> Optional[pd.Series]:
    """Run portfolio simulation for a single strategy, return daily equity curve."""
    strategy_path = resolve_strategy_path(strategy_name)
    if strategy_path is None:
        print(f"  [SKIP] Strategy not found: {strategy_name}")
        return None

    strategy_path = os.path.abspath(strategy_path)

    # Extract trades
    token_trades = extract_token_trades(strategy_path, tokens, config)
    total_trades = sum(len(t) for t in token_trades.values())

    if len(token_trades) < 1 or total_trades < 1:
        print(f"  [SKIP] {strategy_name}: no trades")
        return None

    # Simulate portfolio
    fee_rate = _universe_mod.get_fee_rate(config.exchange, config.market, 'taker')
    portfolio_eq, accepted, skipped, info = simulate_portfolio(
        token_trades, config.capital, config.max_token_pct, config.min_position_usd,
        mtm=config.mtm, dynamic_concentration=config.dynamic_concentration,
        fee_rate=fee_rate,
    )

    if len(portfolio_eq) < 30:
        print(f"  [SKIP] {strategy_name}: equity curve too short ({len(portfolio_eq)} days)")
        return None

    print(f"  {strategy_name}: {len(accepted)} trades, {len(token_trades)} tokens, "
          f"{len(portfolio_eq)} days, final=${portfolio_eq.iloc[-1]:,.0f}")
    return portfolio_eq


# =============================================================================
# Correlation Analysis
# =============================================================================

def compute_correlation_matrix(
    equity_curves: Dict[str, pd.Series],
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Compute pairwise correlation matrix of daily returns.

    Returns:
        corr_matrix: Correlation matrix (DataFrame)
        returns_df: Aligned daily returns (DataFrame)
    """
    # Align all equity curves to common date index
    combined = pd.DataFrame(equity_curves)
    combined = combined.dropna(how='all').ffill()

    # Compute daily returns
    returns = combined.pct_change().dropna()
    returns = returns.replace([np.inf, -np.inf], 0.0).fillna(0.0)

    # Drop columns with zero variance
    valid_cols = returns.columns[returns.std() > 1e-10]
    returns = returns[valid_cols]

    corr = returns.corr()
    return corr, returns


def compute_strategy_metrics(returns_df: pd.DataFrame) -> pd.DataFrame:
    """Compute per-strategy metrics from daily returns."""
    records = []
    for col in returns_df.columns:
        r = returns_df[col]
        mean_d = r.mean()
        std_d = r.std()
        sharpe = (mean_d / std_d) * np.sqrt(365) if std_d > 1e-10 else 0.0

        neg = r[r < 0]
        downside_std = np.sqrt(np.mean(neg ** 2)) if len(neg) > 0 else 1e-10
        sortino = (mean_d / downside_std) * np.sqrt(365) if downside_std > 1e-10 else 0.0

        cumret = (1 + r).cumprod()
        peak = cumret.cummax()
        dd = (cumret - peak) / peak
        max_dd = float(dd.min()) * 100

        ann_ret = ((1 + r.sum()) ** (365.0 / max(len(r), 1)) - 1) * 100
        calmar = ann_ret / abs(max_dd) if abs(max_dd) > 0.01 else 0.0

        records.append({
            'strategy': col,
            'ann_return_pct': ann_ret,
            'sharpe': sharpe,
            'sortino': sortino,
            'calmar': calmar,
            'max_dd_pct': max_dd,
            'daily_vol': std_d * np.sqrt(365) * 100,
        })
    return pd.DataFrame(records).set_index('strategy')


def compute_marginal_sharpe(returns_df: pd.DataFrame) -> pd.DataFrame:
    """Compute marginal Sharpe contribution of each strategy.

    For each strategy S, compute:
    - Portfolio Sharpe WITHOUT S (equal-weight all others)
    - Portfolio Sharpe WITH S (equal-weight all)
    - Marginal contribution = with - without
    """
    n = len(returns_df.columns)
    if n < 2:
        return pd.DataFrame()

    records = []
    all_cols = list(returns_df.columns)

    # Full portfolio (equal weight)
    full_ret = returns_df.mean(axis=1)
    full_sharpe = (full_ret.mean() / full_ret.std()) * np.sqrt(365) if full_ret.std() > 1e-10 else 0.0

    for col in all_cols:
        others = [c for c in all_cols if c != col]
        if not others:
            continue

        # Portfolio without this strategy
        without_ret = returns_df[others].mean(axis=1)
        without_sharpe = ((without_ret.mean() / without_ret.std()) * np.sqrt(365)
                          if without_ret.std() > 1e-10 else 0.0)

        # Standalone
        solo_ret = returns_df[col]
        solo_sharpe = ((solo_ret.mean() / solo_ret.std()) * np.sqrt(365)
                       if solo_ret.std() > 1e-10 else 0.0)

        # Average correlation with others
        avg_corr = returns_df[others].corrwith(returns_df[col]).mean()

        records.append({
            'strategy': col,
            'standalone_sharpe': solo_sharpe,
            'portfolio_without': without_sharpe,
            'portfolio_with': full_sharpe,
            'marginal_sharpe': full_sharpe - without_sharpe,
            'avg_corr_with_others': avg_corr,
        })

    df = pd.DataFrame(records).set_index('strategy')
    df = df.sort_values('marginal_sharpe', ascending=False)
    return df


def find_best_combination(returns_df: pd.DataFrame, min_strategies: int = 2) -> Dict:
    """Find the subset of strategies that maximizes portfolio Sharpe.

    Uses greedy forward selection: start with the best standalone strategy,
    then add the one that most improves portfolio Sharpe, repeat.
    """
    cols = list(returns_df.columns)
    if len(cols) < min_strategies:
        return {}

    # Start with best standalone Sharpe
    standalone_sharpes = {}
    for col in cols:
        r = returns_df[col]
        standalone_sharpes[col] = (r.mean() / r.std()) * np.sqrt(365) if r.std() > 1e-10 else 0.0

    selected = [max(standalone_sharpes, key=standalone_sharpes.get)]
    remaining = [c for c in cols if c not in selected]
    selection_log = [{
        'step': 1,
        'added': selected[0],
        'portfolio_sharpe': standalone_sharpes[selected[0]],
        'improvement': standalone_sharpes[selected[0]],
    }]

    while remaining:
        best_candidate = None
        best_sharpe = -999
        current_ret = returns_df[selected].mean(axis=1)
        current_sharpe = ((current_ret.mean() / current_ret.std()) * np.sqrt(365)
                          if current_ret.std() > 1e-10 else 0.0)

        for candidate in remaining:
            trial = selected + [candidate]
            trial_ret = returns_df[trial].mean(axis=1)
            trial_sharpe = ((trial_ret.mean() / trial_ret.std()) * np.sqrt(365)
                            if trial_ret.std() > 1e-10 else 0.0)
            if trial_sharpe > best_sharpe:
                best_sharpe = trial_sharpe
                best_candidate = candidate

        if best_candidate is None:
            break

        improvement = best_sharpe - current_sharpe
        selected.append(best_candidate)
        remaining.remove(best_candidate)
        selection_log.append({
            'step': len(selected),
            'added': best_candidate,
            'portfolio_sharpe': best_sharpe,
            'improvement': improvement,
        })

    return {
        'optimal_set': selected,
        'final_sharpe': selection_log[-1]['portfolio_sharpe'] if selection_log else 0.0,
        'selection_log': selection_log,
    }


# =============================================================================
# Report
# =============================================================================

def print_report(
    corr_matrix: pd.DataFrame,
    strategy_metrics: pd.DataFrame,
    marginal: pd.DataFrame,
    best_combo: Dict,
    returns_df: pd.DataFrame,
):
    """Print formatted correlation analysis report."""
    print(f"\n{'='*80}")
    print("STRATEGY CORRELATION & COMBINATION ANALYSIS")
    print(f"{'='*80}\n")

    # --- Per-strategy metrics ---
    print("Per-Strategy Metrics (portfolio-level):")
    print(f"{'Strategy':<30} {'AnnRet%':>8} {'Sharpe':>7} {'Sortino':>8} "
          f"{'Calmar':>7} {'MaxDD%':>7} {'AnnVol%':>8}")
    print('-' * 80)
    for idx, row in strategy_metrics.iterrows():
        print(f"{idx:<30} {row['ann_return_pct']:>+7.1f} {row['sharpe']:>+6.2f} "
              f"{row['sortino']:>+7.2f} {row['calmar']:>+6.2f} "
              f"{row['max_dd_pct']:>6.1f} {row['daily_vol']:>7.1f}")

    # --- Correlation matrix ---
    n = len(corr_matrix)
    print(f"\nPairwise Correlation Matrix (daily returns):")

    # Short names for display
    short = {s: s.replace('_', ' ')[:20] for s in corr_matrix.columns}
    header = f"{'':>20} " + ' '.join(f"{short[c]:>10}" for c in corr_matrix.columns)
    print(header)
    print('-' * len(header))
    for row_name in corr_matrix.index:
        vals = ' '.join(
            f"{corr_matrix.loc[row_name, col]:>+9.3f}" + ' '
            for col in corr_matrix.columns
        )
        print(f"{short[row_name]:>20} {vals}")

    # Summary stats
    mask = np.triu(np.ones_like(corr_matrix, dtype=bool), k=1)
    upper = corr_matrix.values[mask]
    if len(upper) > 0:
        print(f"\n  Median pairwise correlation: {np.median(upper):+.3f}")
        print(f"  Mean pairwise correlation:   {np.mean(upper):+.3f}")
        print(f"  Min pairwise correlation:    {np.min(upper):+.3f}")
        print(f"  Max pairwise correlation:    {np.max(upper):+.3f}")

        # Flag high correlations
        high_corr = []
        for i in range(n):
            for j in range(i + 1, n):
                if abs(corr_matrix.iloc[i, j]) > 0.7:
                    high_corr.append((
                        corr_matrix.index[i],
                        corr_matrix.columns[j],
                        corr_matrix.iloc[i, j],
                    ))
        if high_corr:
            print(f"\n  WARNING: {len(high_corr)} highly correlated pairs (|r| > 0.7):")
            for s1, s2, r in high_corr:
                print(f"    {s1} <-> {s2}: {r:+.3f}")
        else:
            print(f"\n  No highly correlated pairs (|r| > 0.7) — good diversification.")

    # --- Marginal Sharpe ---
    if not marginal.empty:
        print(f"\nMarginal Sharpe Contribution (equal-weight portfolio):")
        print(f"{'Strategy':<30} {'Solo Sharpe':>11} {'Without':>8} {'With All':>9} "
              f"{'Marginal':>9} {'AvgCorr':>8}")
        print('-' * 80)
        for idx, row in marginal.iterrows():
            flag = ' *' if row['marginal_sharpe'] < 0 else ''
            print(f"{idx:<30} {row['standalone_sharpe']:>+10.2f} "
                  f"{row['portfolio_without']:>+7.2f} {row['portfolio_with']:>+8.2f} "
                  f"{row['marginal_sharpe']:>+8.3f} {row['avg_corr_with_others']:>+7.3f}{flag}")
        print("  * = negative marginal contribution (removing it improves portfolio)")

    # --- Best combination ---
    if best_combo:
        print(f"\nGreedy Forward Selection (maximize portfolio Sharpe):")
        print(f"{'Step':>5} {'Added':<30} {'Portfolio Sharpe':>16} {'Improvement':>12}")
        print('-' * 65)
        for step in best_combo['selection_log']:
            print(f"{step['step']:>5} {step['added']:<30} "
                  f"{step['portfolio_sharpe']:>+15.3f} {step['improvement']:>+11.3f}")

        # Show equal-weight combined equity stats
        optimal = best_combo['optimal_set']
        opt_ret = returns_df[optimal].mean(axis=1)
        opt_cumret = (1 + opt_ret).cumprod()
        peak = opt_cumret.cummax()
        max_dd = float(((opt_cumret - peak) / peak).min()) * 100
        print(f"\n  Optimal set ({len(optimal)} strategies): {', '.join(optimal)}")
        print(f"  Combined Sharpe: {best_combo['final_sharpe']:+.3f}")
        print(f"  Combined MaxDD:  {max_dd:.1f}%")

    print()


# =============================================================================
# Main
# =============================================================================

def run_correlation_analysis(
    strategy_names: List[str],
    tokens: Optional[List[str]] = None,
    config: Optional[PortfolioConfig] = None,
    verbose: bool = True,
) -> Dict:
    """Run full correlation analysis pipeline.

    Returns dict with correlation matrix, metrics, marginal Sharpe, best combination.
    """
    if config is None:
        config = PortfolioConfig()

    auto_market = config.market == 'auto'

    if tokens is None and not auto_market:
        tokens = resolve_universe('filtered', market=config.market, verbose=verbose)

    if auto_market:
        print(f"\nCorrelation analysis: {len(strategy_names)} strategies, auto-detecting market per strategy")
    else:
        print(f"\nCorrelation analysis: {len(strategy_names)} strategies, {len(tokens)} tokens")

    # Generate equity curves for each strategy
    equity_curves = {}
    strategy_markets = {}  # track detected market per strategy for audit trail
    t0 = time.time()
    for i, name in enumerate(strategy_names):
        print(f"\n[{i+1}/{len(strategy_names)}] Running {name}...")
        if auto_market:
            # Resolve per-strategy tokens based on detected market
            strat_path = resolve_strategy_path(name)
            detected = _detect_strategy_market(os.path.abspath(strat_path)) if strat_path else 'spot'
            strategy_markets[name] = detected
            print(f"  [AUTO] {name}: detected market={detected}")
            strat_tokens = tokens if tokens is not None else resolve_universe('filtered', market=detected, verbose=False)
            strat_config = dataclasses.replace(config, market=detected)
            eq = generate_strategy_equity(name, strat_tokens, strat_config)
        else:
            strategy_markets[name] = config.market
            eq = generate_strategy_equity(name, tokens, config)
        if eq is not None:
            equity_curves[name] = eq

    elapsed = time.time() - t0
    print(f"\nGenerated {len(equity_curves)}/{len(strategy_names)} equity curves in {elapsed:.1f}s")

    if len(equity_curves) < 2:
        print("Need at least 2 strategies with valid equity curves.")
        return {}

    # Compute correlation
    corr_matrix, returns_df = compute_correlation_matrix(equity_curves)
    strategy_metrics = compute_strategy_metrics(returns_df)
    marginal = compute_marginal_sharpe(returns_df)
    best_combo = find_best_combination(returns_df)

    # Report
    if verbose:
        print_report(corr_matrix, strategy_metrics, marginal, best_combo, returns_df)

    # Save results
    results_dir = os.path.join(os.path.dirname(_v3_dir), 'results')
    os.makedirs(results_dir, exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    out_path = os.path.join(results_dir, f'correlation_{ts}.json')

    result_data = {
        'timestamp': datetime.now().isoformat(),
        'config': {
            'market': config.market,
            'exchange': config.exchange,
            'capital': config.capital,
            'data_dir': config.data_dir,
        },
        'strategy_markets': {k: v for k, v in strategy_markets.items() if k in equity_curves},
        'strategies': list(equity_curves.keys()),
        'n_tokens': len(tokens) if tokens is not None else None,
        'elapsed_s': elapsed,
        'correlation_matrix': corr_matrix.to_dict(),
        'strategy_metrics': strategy_metrics.reset_index().to_dict(orient='records'),
        'marginal_sharpe': marginal.reset_index().to_dict(orient='records'),
        'best_combination': {
            'optimal_set': best_combo.get('optimal_set', []),
            'final_sharpe': best_combo.get('final_sharpe', 0),
            'selection_log': best_combo.get('selection_log', []),
        },
        'pairwise_summary': {
            'median': float(np.median(corr_matrix.values[np.triu_indices_from(corr_matrix.values, k=1)])),
            'mean': float(np.mean(corr_matrix.values[np.triu_indices_from(corr_matrix.values, k=1)])),
            'min': float(np.min(corr_matrix.values[np.triu_indices_from(corr_matrix.values, k=1)])),
            'max': float(np.max(corr_matrix.values[np.triu_indices_from(corr_matrix.values, k=1)])),
        },
    }

    with open(out_path, 'w') as f:
        json.dump(result_data, f, indent=2, default=str)
    print(f"Results saved to {out_path}")

    return {
        'correlation_matrix': corr_matrix,
        'returns_df': returns_df,
        'strategy_metrics': strategy_metrics,
        'marginal_sharpe': marginal,
        'best_combination': best_combo,
    }


def main():
    parser = argparse.ArgumentParser(description='V3 Strategy Correlation Analysis')
    parser.add_argument('--strategies', nargs='+', help='Strategy names (e.g., s11_momentum_burst)')
    parser.add_argument('--tier', default=None, help='Use tier from sweep: A, B, AB (default: A)')
    parser.add_argument('--tokens', nargs='+', help='Specific tokens (overrides --universe)')
    parser.add_argument('--universe', choices=['all', 'filtered', 'liquid'],
                        default='filtered', help='Token universe (default: filtered)')
    parser.add_argument('--capital', type=float, default=200_000, help='Capital per strategy')
    parser.add_argument('--market', default='auto', choices=['auto', 'spot', 'perp', 'combined'])
    parser.add_argument('--exchange', default='binance',
                        choices=['binance', 'kraken', 'hyperliquid'])
    parser.add_argument('--workers', type=int, default=4, help='Parallel workers')

    args = parser.parse_args()

    # Resolve strategies
    if args.strategies:
        strategy_names = args.strategies
    elif args.tier:
        strategy_names = get_tier_strategies(args.tier)
        if not strategy_names:
            print(f"No strategies found for tier '{args.tier}'")
            sys.exit(1)
        print(f"Tier {args.tier} strategies: {', '.join(strategy_names)}")
    else:
        # Default: Tier A
        strategy_names = get_tier_strategies('A')
        if not strategy_names:
            print("No Tier A strategies found. Use --strategies or --tier.")
            sys.exit(1)
        print(f"Using Tier A strategies: {', '.join(strategy_names)}")

    # Resolve tokens (defer to per-strategy resolution in 'auto' mode)
    tokens = args.tokens
    if tokens is None and args.market != 'auto':
        tokens = resolve_universe(args.universe, market=args.market, verbose=True)

    config = PortfolioConfig(
        capital=args.capital,
        data_dir='data',
        market=args.market,
        exchange=args.exchange,
        workers=args.workers,
    )

    run_correlation_analysis(strategy_names, tokens, config)


if __name__ == '__main__':
    os.chdir(os.path.dirname(_v3_dir) or '.')
    main()
