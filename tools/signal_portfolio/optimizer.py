"""Walk-forward parameter optimization for max annualized return."""

import itertools
import os
import time
import numpy as np
import pandas as pd
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Tuple

from .config import SignalPortfolioConfig
from .catalog_loader import TokenSignalProfile
from .strategy_generator import GroupSignalConfig, create_group_strategy


@dataclass
class OptimizationResult:
    """Result of parameter optimization for a group."""
    group_id: int
    best_params: Dict
    train_annual_return: float = 0.0
    val_annual_return: float = 0.0
    test_annual_return: float = 0.0
    train_max_dd: float = 0.0
    val_max_dd: float = 0.0
    test_max_dd: float = 0.0
    n_param_combos_tested: int = 0
    representative_tokens: List[str] = None

    def __post_init__(self):
        if self.representative_tokens is None:
            self.representative_tokens = []


def _select_representative_tokens(tokens: List[str],
                                    profiles: Dict[str, TokenSignalProfile],
                                    max_tokens: int = 5) -> List[str]:
    """Select 3-5 representative tokens per group for fast optimization.

    Picks tokens with most filtered signals (most data-rich).
    """
    scored = []
    for t in tokens:
        p = profiles.get(t)
        if p:
            scored.append((t, len(p.filtered_signals)))

    scored.sort(key=lambda x: x[1], reverse=True)
    return [t for t, _ in scored[:max_tokens]]


def _load_engine():
    """Load the v3 Engine class."""
    import sys
    import os
    import importlib.util

    if 'v3_engine' in sys.modules:
        return sys.modules['v3_engine']

    _v3_dir = os.path.join(os.path.dirname(__file__), '..', '..', 'v3')
    spec = importlib.util.spec_from_file_location(
        'v3_engine', os.path.join(_v3_dir, 'engine.py'))
    mod = importlib.util.module_from_spec(spec)
    sys.modules['v3_engine'] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_token_df(ticker: str, data_dir: str, market: str) -> Optional[pd.DataFrame]:
    """Load 1h OHLCV data for a token from parquet cache."""
    h1_path = os.path.join(data_dir, market, f'1h_cache/{ticker}_1h.parquet')
    if not os.path.exists(h1_path):
        return None
    df = pd.read_parquet(h1_path)
    if len(df) < 2000:
        return None
    return df


def _run_single_token(engine, strategy_fn, ticker: str,
                       data_dir: str, market: str,
                       bar_range: Optional[Tuple[int, int]] = None) -> Dict:
    """Run strategy on a single token, return performance metrics.

    Args:
        engine: v3 Engine instance
        strategy_fn: callable(StrategyContext) -> StrategyResult
        ticker: token name
        data_dir: path to data directory
        market: market type (spot/perp)
        bar_range: optional (start_bar, end_bar) to restrict simulation window

    Returns:
        Dict with 'trades', 'total_pnl', 'n_trades', 'max_dd', 'annual_return'
    """
    df_1h = _load_token_df(ticker, data_dir, market)
    if df_1h is None:
        return {'trades': [], 'total_pnl': 0.0, 'n_trades': 0,
                'max_dd': 0.0, 'annual_return': 0.0}

    ctx = engine._build_context(ticker, df_1h)
    if ctx is None:
        return {'trades': [], 'total_pnl': 0.0, 'n_trades': 0,
                'max_dd': 0.0, 'annual_return': 0.0}

    result = strategy_fn(ctx)
    n = len(ctx.ind_1h['close'])

    # Apply bar range mask if specified
    if bar_range is not None:
        start, end = bar_range
        mask = np.zeros(n, dtype=bool)
        mask[max(0, start):min(n, end)] = True
        result.entry_mask = result.entry_mask & mask

    trades, final_eq = engine._simulate(ctx, result)

    if not trades:
        return {'trades': [], 'total_pnl': 0.0, 'n_trades': 0,
                'max_dd': 0.0, 'annual_return': 0.0}

    # Compute metrics
    total_pnl = sum(t['pnl'] for t in trades)
    capital = engine.capital

    # Simple equity curve for DD calculation
    equity = [capital]
    for t in sorted(trades, key=lambda x: x['entry_bar']):
        equity.append(equity[-1] + t['pnl'])
    equity = np.array(equity)

    peak = np.maximum.accumulate(equity)
    dd = (equity - peak) / peak
    max_dd = float(dd.min()) if len(dd) > 0 else 0.0

    # Annualized return
    n_bars = n
    if bar_range:
        n_bars = bar_range[1] - bar_range[0]
    n_years = max(n_bars / 8760, 0.1)  # 8760 hours/year
    total_return = total_pnl / capital
    annual_return = (1 + total_return) ** (1 / n_years) - 1

    return {
        'trades': trades,
        'total_pnl': total_pnl,
        'n_trades': len(trades),
        'max_dd': max_dd,
        'annual_return': annual_return,
    }


def _evaluate_params(engine, group_config: GroupSignalConfig,
                      tokens: List[str],
                      params: Dict,
                      data_dir: str,
                      market: str,
                      bar_range: Optional[Tuple[int, int]] = None) -> Dict:
    """Evaluate a parameter set across representative tokens."""
    # Create modified config with these params
    modified_config = GroupSignalConfig(
        group_id=group_config.group_id,
        tokens=group_config.tokens,
        signals=group_config.signals,
        dominant_horizon=group_config.dominant_horizon,
        entry_threshold=params['entry_threshold'],
        min_vol_ratio=params['min_vol_ratio'],
        stop_mult=params['stop_mult'],
        trail_mult=params['trail_mult'],
        min_hold=params.get('min_hold', group_config.min_hold),
        max_hold=group_config.max_hold,
        regime_ics=group_config.regime_ics,
    )

    strategy_fn = create_group_strategy(modified_config)

    total_pnl = 0.0
    total_trades = 0
    worst_dd = 0.0
    annual_returns = []

    for token in tokens:
        result = _run_single_token(engine, strategy_fn, token, data_dir, market, bar_range)
        total_pnl += result['total_pnl']
        total_trades += result['n_trades']
        worst_dd = min(worst_dd, result['max_dd'])
        annual_returns.append(result['annual_return'])

    avg_annual = np.mean(annual_returns) if annual_returns else 0.0

    return {
        'total_pnl': total_pnl,
        'n_trades': total_trades,
        'max_dd': worst_dd,
        'annual_return': avg_annual,
        'params': params,
    }


def optimize_group(group_config: GroupSignalConfig,
                    profiles: Dict[str, TokenSignalProfile],
                    config: SignalPortfolioConfig) -> OptimizationResult:
    """Walk-forward optimization for a single group.

    Train: first 60%, Validation: next 20%, Test: final 20% (held out)
    Grid search over coarse params (432 combinations).
    Hard constraint: max_drawdown < -30%.
    """
    engine_mod = _load_engine()
    engine = engine_mod.Engine(
        data_dir=config.data_dir,
        market=config.market,
        capital=config.capital,
        exchange=config.exchange,
    )

    # Select representative tokens
    rep_tokens = _select_representative_tokens(
        group_config.tokens, profiles, max_tokens=5)

    if not rep_tokens:
        return OptimizationResult(group_id=group_config.group_id, best_params={})

    # Determine bar ranges from first representative token
    sample_df = _load_token_df(rep_tokens[0], config.data_dir, config.market)
    if sample_df is None or len(sample_df) < 1000:
        return OptimizationResult(group_id=group_config.group_id, best_params={})

    n_bars = len(sample_df)
    train_end = int(n_bars * config.train_pct)
    val_end = int(n_bars * (config.train_pct + config.val_pct))

    train_range = (0, train_end)
    val_range = (train_end, val_end)
    test_range = (val_end, n_bars)

    # Build parameter grid
    param_grid = list(itertools.product(
        config.entry_thresholds,
        config.min_vol_ratios,
        config.stop_mults,
        config.trail_mults,
        config.min_holds,
    ))

    print(f"  Group {group_config.group_id}: {len(param_grid)} param combos, "
          f"{len(rep_tokens)} rep tokens")

    # Phase 1: Grid search on train set with representative tokens
    t0 = time.time()
    train_results = []

    for i, (et, mvr, sm, tm, mh) in enumerate(param_grid):
        params = {
            'entry_threshold': et,
            'min_vol_ratio': mvr,
            'stop_mult': sm,
            'trail_mult': tm,
            'min_hold': mh,
        }

        result = _evaluate_params(
            engine, group_config, rep_tokens, params,
            config.data_dir, config.market, train_range)

        # Hard constraint: DD
        if result['max_dd'] < config.max_drawdown_limit:
            continue

        train_results.append(result)

        if (i + 1) % 100 == 0:
            print(f"    {i+1}/{len(param_grid)} tested ({time.time()-t0:.0f}s)")

    if not train_results:
        print(f"  Group {group_config.group_id}: No params passed DD constraint")
        return OptimizationResult(
            group_id=group_config.group_id,
            best_params={},
            n_param_combos_tested=len(param_grid),
            representative_tokens=rep_tokens,
        )

    # Sort by annual return
    train_results.sort(key=lambda x: x['annual_return'], reverse=True)

    # Phase 2: Validate top 3 on validation set
    top_3 = train_results[:3]
    best_val = None
    best_val_return = -np.inf

    for result in top_3:
        val_result = _evaluate_params(
            engine, group_config, rep_tokens, result['params'],
            config.data_dir, config.market, val_range)

        if val_result['max_dd'] < config.max_drawdown_limit:
            continue

        if val_result['annual_return'] > best_val_return:
            best_val_return = val_result['annual_return']
            best_val = {
                'params': result['params'],
                'train': result,
                'val': val_result,
            }

    if best_val is None:
        # Fall back to best train result
        best_val = {
            'params': top_3[0]['params'],
            'train': top_3[0],
            'val': {'annual_return': 0.0, 'max_dd': 0.0},
        }

    # Phase 3: Test set evaluation (held out)
    test_result = _evaluate_params(
        engine, group_config, rep_tokens, best_val['params'],
        config.data_dir, config.market, test_range)

    elapsed = time.time() - t0
    print(f"  Group {group_config.group_id} done ({elapsed:.0f}s): "
          f"train={best_val['train']['annual_return']:.1%}, "
          f"val={best_val['val']['annual_return']:.1%}, "
          f"test={test_result['annual_return']:.1%}")

    return OptimizationResult(
        group_id=group_config.group_id,
        best_params=best_val['params'],
        train_annual_return=best_val['train']['annual_return'],
        val_annual_return=best_val['val']['annual_return'],
        test_annual_return=test_result['annual_return'],
        train_max_dd=best_val['train']['max_dd'],
        val_max_dd=best_val['val']['max_dd'],
        test_max_dd=test_result['max_dd'],
        n_param_combos_tested=len(param_grid),
        representative_tokens=rep_tokens,
    )
