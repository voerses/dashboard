"""
V3 Validation Engine — Walk-Forward + CPCV + Dual Gate
=======================================================

Combines:
  1. Rolling walk-forward with purged recalibration windows
  2. Combinatorial Purged Cross-Validation (CPCV)
  3. Full quant metrics suite (Sharpe, Sortino, Calmar, Beta, Alpha, ...)
  4. Dual gate: token must pass BOTH WF and CPCV to be "validated"

Uses v2's strategy protocol (StrategyContext/StrategyResult).
Does NOT touch v1 or v2.

Usage:
    python v3/validation.py --strategy s11 --tokens BTC ETH SOL --workers 4
    python v3/validation.py --strategy s11 --wf-only
    python v3/validation.py --strategy s11 --cpcv-only
"""

import sys
import os
import time
import json
import importlib
import argparse
import numpy as np
import pandas as pd
from dataclasses import dataclass, asdict
from typing import Optional, List, Dict, Tuple
from datetime import datetime
from concurrent.futures import ProcessPoolExecutor, as_completed

_v3_dir = os.path.dirname(os.path.abspath(__file__))

# Use importlib to explicitly load v3 modules (avoids conflicts when v2 is also on sys.path)
def _load_v3(name):
    import importlib.util
    full_name = f'v3_{name}'
    # Return cached if already loaded (avoids double-loading in workers)
    if full_name in sys.modules:
        return sys.modules[full_name]
    spec = importlib.util.spec_from_file_location(full_name, os.path.join(_v3_dir, f'{name}.py'))
    mod = importlib.util.module_from_spec(spec)
    # Register in sys.modules BEFORE exec to satisfy Numba cache and
    # to ensure strategy `from engine import ...` gets the same module object
    sys.modules[full_name] = mod
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod

_engine_mod = _load_v3('engine')
_metrics_mod = _load_v3('metrics')
_cpcv_mod = _load_v3('cpcv')
_universe_mod = _load_v3('universe')

Engine = _engine_mod.Engine
StrategyContext = _engine_mod.StrategyContext
StrategyResult = _engine_mod.StrategyResult
StrategyFn = _engine_mod.StrategyFn
CRISIS = _engine_mod.CRISIS
QUIET = _engine_mod.QUIET
UPTREND = _engine_mod.UPTREND
RANGE = _engine_mod.RANGE
DOWNTREND = _engine_mod.DOWNTREND

PerformanceMetrics = _metrics_mod.PerformanceMetrics
compute_metrics = _metrics_mod.compute_metrics
build_equity_curve = _metrics_mod.build_equity_curve
load_benchmark_returns = _metrics_mod.load_benchmark_returns

generate_cpcv_splits = _cpcv_mod.generate_cpcv_splits
deflated_sharpe = _cpcv_mod.deflated_sharpe

LIQUID_TOKENS = _universe_mod.LIQUID_TOKENS
get_tier = _universe_mod.get_tier


# =============================================================================
# Configuration
# =============================================================================

@dataclass
class WalkForwardConfig:
    train_days: int = 365
    recalibrate_every: int = 90
    purge_days: int = 5


@dataclass
class CPCVConfig:
    n_groups: int = 6
    n_test_groups: int = 2
    purge_pct: float = 0.01
    warmup_bars: int = 200


@dataclass
class ValidationConfig:
    wf: WalkForwardConfig = None
    cpcv: CPCVConfig = None
    capital: float = 200_000
    pbo_threshold: float = 0.40
    data_dir: str = 'data'
    workers: int = 4

    def __post_init__(self):
        if self.wf is None:
            self.wf = WalkForwardConfig()
        if self.cpcv is None:
            self.cpcv = CPCVConfig()


# =============================================================================
# Result Types
# =============================================================================

@dataclass
class TokenValidationResult:
    ticker: str

    # Walk-forward
    wf_metrics: Optional[PerformanceMetrics] = None
    wf_pass: bool = False

    # CPCV
    cpcv_pbo: float = 1.0
    cpcv_avg_return: float = 0.0
    cpcv_folds_profitable: int = 0
    cpcv_total_folds: int = 0
    cpcv_deflated_sharpe: float = 0.0
    cpcv_pass: bool = False

    # Combined
    validated: bool = False


# =============================================================================
# Walk-Forward Validation
# =============================================================================

def _run_walk_forward(engine: Engine, strategy_fn: StrategyFn, ticker: str,
                      df_1h: pd.DataFrame, config: WalkForwardConfig,
                      benchmark_returns: Optional[pd.Series] = None,
                      capital: float = 200_000) -> Tuple[Optional[PerformanceMetrics], bool, list]:
    """
    Rolling walk-forward validation for a single token.

    Algorithm:
    1. Build StrategyContext ONCE from full df_1h, call strategy ONCE.
    2. Copy entry_mask, mask out:
       - First train_bars (training period)
       - purge_bars after each recalibration point
    3. Simulate with masked entries → OOS trades only.
    4. Build equity curve, compute full metrics.
    5. WF gate: wf_pass = oos_pnl > 0

    NOTE on indicator look-ahead: Indicators (SMA, RSI, etc.) are computed on
    the full dataset before OOS masking. This is an intentional design choice —
    the strategy can only ENTER during OOS windows, so the trading PnL is
    unbiased. The indicators themselves (e.g. 200-bar SMA) would see the same
    values at any given bar whether computed on full data or incrementally,
    since they depend only on past bars. The CPCV path builds fresh contexts
    per fold for independent cross-validation.
    """
    ctx = engine._build_context(ticker, df_1h)
    if ctx is None:
        return None, False, []

    result = strategy_fn(ctx)
    n = len(ctx.ind_1h['close'])

    train_bars = config.train_days * 24
    recal_bars = config.recalibrate_every * 24
    purge_bars = config.purge_days * 24

    if n <= train_bars + purge_bars:
        return None, False, []

    # Mask entries: only allow OOS entries
    masked_entry = result.entry_mask.copy()

    # Zero out training period
    masked_entry[:train_bars] = False

    # Zero out purge windows after each recalibration point
    recal_point = train_bars
    while recal_point < n:
        purge_end = min(recal_point + purge_bars, n)
        masked_entry[recal_point:purge_end] = False
        recal_point += recal_bars

    # Create masked result
    masked_result = StrategyResult(
        entry_mask=masked_entry,
        direction=result.direction,
        stop_mult=result.stop_mult,
        trail_mult=result.trail_mult,
        target_mult=result.target_mult,
        no_stop_bars=result.no_stop_bars,
        min_hold=result.min_hold,
        max_hold=result.max_hold,
        edge=result.edge,
        exit_regimes=result.exit_regimes,
        rsi_exit_level=result.rsi_exit_level,
        convex_exit=result.convex_exit,
        mean_target_vals=result.mean_target_vals,
        name=result.name,
    )

    trades, final_equity = engine._simulate(ctx, masked_result)

    if not trades:
        return PerformanceMetrics(), False, []

    # Build daily equity curve
    eq_curve = build_equity_curve(trades, n, capital, index=ctx.idx_1h)

    # Compute full metrics
    metrics = compute_metrics(trades, eq_curve, benchmark_returns, capital)

    oos_pnl = final_equity - capital
    wf_pass = oos_pnl > 0

    return metrics, wf_pass, trades


# =============================================================================
# CPCV Validation
# =============================================================================

def _run_cpcv(engine: Engine, strategy_fn: StrategyFn, ticker: str,
              df_1h: pd.DataFrame, config: CPCVConfig,
              capital: float = 200_000) -> Tuple[float, float, int, int, float]:
    """
    CPCV validation for a single token.

    For each of C(n_groups, n_test_groups) folds:
    1. Slice df_1h to test portion + warmup_bars
    2. Build FRESH StrategyContext on slice
    3. Zero warmup entries, simulate, collect PnL
    4. PBO = fraction of folds with negative return
    """
    n = len(df_1h)
    if n < 2000:
        return 1.0, 0.0, 0, 0, 0.0

    splits = generate_cpcv_splits(n, config.n_groups, config.n_test_groups, config.purge_pct)

    fold_returns = []

    for train_idx, test_idx in splits:
        test_start = int(test_idx.min())
        test_end = int(test_idx.max())

        # Include warmup before test start
        data_start = max(0, test_start - config.warmup_bars)
        df_fold = df_1h.iloc[data_start:test_end + 1]

        if len(df_fold) < 500:
            continue

        ctx = engine._build_context(ticker, df_fold)
        if ctx is None:
            continue

        result = strategy_fn(ctx)

        # Copy entry_mask to avoid mutating the StrategyResult in-place
        masked_entries = result.entry_mask.copy()

        # Zero out warmup entries
        warmup_in_fold = test_start - data_start
        if warmup_in_fold > 0:
            masked_entries[:warmup_in_fold] = False

        # Replace with the safe copy
        result.entry_mask = masked_entries

        trades, final_equity = engine._simulate(ctx, result)
        fold_pnl = final_equity - capital
        fold_return = fold_pnl / capital * 100
        fold_returns.append(fold_return)

    if not fold_returns:
        return 1.0, 0.0, 0, 0, 0.0

    folds_profitable = sum(1 for r in fold_returns if r > 0)
    total_folds = len(fold_returns)
    pbo = 1.0 - (folds_profitable / total_folds)
    avg_return = float(np.mean(fold_returns))
    ds = deflated_sharpe(fold_returns)

    return pbo, avg_return, folds_profitable, total_folds, ds


# =============================================================================
# Single Token Validation (used by parallel workers)
# =============================================================================

def _validate_token(ticker: str, strategy_module_path: str, config_dict: dict,
                    run_wf: bool = True, run_cpcv: bool = True) -> Optional[dict]:
    """
    Validate a single token. Designed to run in a ProcessPoolExecutor worker.
    Re-imports strategy inside worker to avoid pickling issues.
    """
    # Re-import v3 modules explicitly in worker process via importlib
    # (avoids fragile sys.path manipulation that can cause v2/v3 conflicts)
    v3_dir = os.path.dirname(os.path.abspath(__file__))

    _eng = _load_v3('engine')
    _met = _load_v3('metrics')
    Engine = _eng.Engine
    load_benchmark_returns = _met.load_benchmark_returns

    # Put v3 first on sys.path so strategy's `from engine import ...` resolves to v3
    # This is needed because strategies do `from engine import StrategyContext, StrategyResult`
    if v3_dir not in sys.path:
        sys.path.insert(0, v3_dir)

    config = ValidationConfig(
        wf=WalkForwardConfig(**config_dict.get('wf', {})),
        cpcv=CPCVConfig(**config_dict.get('cpcv', {})),
        capital=config_dict.get('capital', 200_000),
        pbo_threshold=config_dict.get('pbo_threshold', 0.40),
        data_dir=config_dict.get('data_dir', 'real_data'),
    )

    # Load strategy
    try:
        spec = importlib.util.spec_from_file_location("strategy_mod", strategy_module_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        strategy_fn = mod.strategy
    except Exception as e:
        return {'ticker': ticker, 'error': f'strategy_load: {e}'}

    # Load data
    h1_path = os.path.join(config.data_dir, f'1h_cache/{ticker}_1h.parquet')
    if not os.path.exists(h1_path):
        return {'ticker': ticker, 'error': 'no_data'}

    df_1h = pd.read_parquet(h1_path)
    if len(df_1h) < 2000:
        return {'ticker': ticker, 'error': 'insufficient_data'}

    engine = Engine(data_dir=config.data_dir, capital=config.capital)

    result = TokenValidationResult(ticker=ticker)

    # Walk-Forward
    if run_wf:
        benchmark = load_benchmark_returns(config.data_dir)
        wf_metrics, wf_pass, wf_trades = _run_walk_forward(
            engine, strategy_fn, ticker, df_1h, config.wf, benchmark, config.capital)
        result.wf_metrics = wf_metrics
        result.wf_pass = wf_pass

    # CPCV
    if run_cpcv:
        pbo, avg_ret, folds_prof, total_folds, ds = _run_cpcv(
            engine, strategy_fn, ticker, df_1h, config.cpcv, config.capital)
        result.cpcv_pbo = pbo
        result.cpcv_avg_return = avg_ret
        result.cpcv_folds_profitable = folds_prof
        result.cpcv_total_folds = total_folds
        result.cpcv_deflated_sharpe = ds
        result.cpcv_pass = pbo < config.pbo_threshold

    # Dual gate
    if run_wf and run_cpcv:
        result.validated = result.wf_pass and result.cpcv_pass
    elif run_wf:
        result.validated = result.wf_pass
    elif run_cpcv:
        result.validated = result.cpcv_pass

    # Serialize
    out = {
        'ticker': ticker,
        'wf_pass': result.wf_pass,
        'cpcv_pass': result.cpcv_pass,
        'cpcv_pbo': result.cpcv_pbo,
        'cpcv_avg_return': result.cpcv_avg_return,
        'cpcv_folds_profitable': result.cpcv_folds_profitable,
        'cpcv_total_folds': result.cpcv_total_folds,
        'cpcv_deflated_sharpe': result.cpcv_deflated_sharpe,
        'validated': result.validated,
    }
    if result.wf_metrics is not None:
        out['wf_metrics'] = asdict(result.wf_metrics)
    return out


# =============================================================================
# Orchestrator
# =============================================================================

def validate_strategy(strategy_path: str, tokens: Optional[List[str]] = None,
                      config: Optional[ValidationConfig] = None,
                      run_wf: bool = True, run_cpcv: bool = True,
                      verbose: bool = True) -> Dict:
    """
    Run full V3 validation on a strategy across tokens.

    Args:
        strategy_path: Path to strategy .py file (must have `strategy(ctx)` function)
        tokens: Token list (default: LIQUID_TOKENS)
        config: Validation configuration
        run_wf: Run walk-forward validation
        run_cpcv: Run CPCV validation
        verbose: Print progress table
    """
    if config is None:
        config = ValidationConfig()
    if tokens is None:
        tokens = LIQUID_TOKENS

    strategy_path = os.path.abspath(strategy_path)
    strategy_name = os.path.splitext(os.path.basename(strategy_path))[0]

    # Try to get strategy display name
    try:
        spec = importlib.util.spec_from_file_location("strat_peek", strategy_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        # Call strategy on a dummy to get .name — not worth it, just use filename
        display_name = getattr(mod, '__doc__', strategy_name) or strategy_name
        display_name = display_name.strip().split('\n')[0][:60]
    except Exception:
        display_name = strategy_name

    # Filter tokens with data
    valid_tokens = []
    for tk in tokens:
        h1_path = os.path.join(config.data_dir, f'1h_cache/{tk}_1h.parquet')
        if os.path.exists(h1_path):
            valid_tokens.append(tk)

    if not valid_tokens:
        print("No tokens with data found.")
        return {}

    config_dict = {
        'wf': {'train_days': config.wf.train_days,
               'recalibrate_every': config.wf.recalibrate_every,
               'purge_days': config.wf.purge_days},
        'cpcv': {'n_groups': config.cpcv.n_groups,
                 'n_test_groups': config.cpcv.n_test_groups,
                 'purge_pct': config.cpcv.purge_pct,
                 'warmup_bars': config.cpcv.warmup_bars},
        'capital': config.capital,
        'pbo_threshold': config.pbo_threshold,
        'data_dir': config.data_dir,
    }

    # Header
    mode = []
    if run_wf:
        mode.append(f"WF: train={config.wf.train_days}d, recal={config.wf.recalibrate_every}d, purge={config.wf.purge_days}d")
    if run_cpcv:
        mode.append(f"CPCV: {config.cpcv.n_groups}g/{config.cpcv.n_test_groups}t, purge={config.cpcv.purge_pct:.0%}")

    print(f"\nV3 VALIDATION: {display_name}")
    print(f"  {' | '.join(mode)}")
    print("=" * 120)

    t0 = time.time()
    results = {}

    if config.workers > 1 and len(valid_tokens) > 1:
        # Parallel execution
        with ProcessPoolExecutor(max_workers=config.workers) as executor:
            futures = {}
            for tk in valid_tokens:
                f = executor.submit(_validate_token, tk, strategy_path,
                                    config_dict, run_wf, run_cpcv)
                futures[f] = tk

            done = 0
            for future in as_completed(futures):
                done += 1
                tk = futures[future]
                try:
                    r = future.result()
                    if r and 'error' not in r:
                        results[tk] = r
                    if verbose:
                        if r and 'error' in r:
                            print(f"  [{done}/{len(valid_tokens)}] {tk}: {r['error']}")
                        else:
                            _print_token_progress(r, done, len(valid_tokens))
                except Exception as e:
                    if verbose:
                        print(f"  [{done}/{len(valid_tokens)}] {tk}: ERROR {e}")
    else:
        # Sequential
        for idx, tk in enumerate(valid_tokens, 1):
            r = _validate_token(tk, strategy_path, config_dict, run_wf, run_cpcv)
            if r and 'error' not in r:
                results[tk] = r
            if verbose:
                if r and 'error' in r:
                    print(f"  [{idx}/{len(valid_tokens)}] {tk}: {r['error']}")
                else:
                    _print_token_progress(r, idx, len(valid_tokens))

    elapsed = time.time() - t0

    # Print summary table
    if verbose and results:
        _print_validation_table(results, run_wf, run_cpcv)

    validated_tokens = [tk for tk, r in results.items() if r.get('validated', False)]
    print(f"\n  Validated: {len(validated_tokens)}/{len(valid_tokens)} | Time: {elapsed:.1f}s")
    if validated_tokens:
        print(f"  Tokens: {', '.join(validated_tokens)}")

    # Save results
    _save_results(results, strategy_name, config, elapsed, validated_tokens)

    return {
        'strategy': strategy_name,
        'results': results,
        'validated_tokens': validated_tokens,
        'elapsed': elapsed,
    }


def _print_token_progress(r: dict, idx: int, total: int):
    """Print single-line progress for a token."""
    if r is None:
        return
    tk = r['ticker']
    wm = r.get('wf_metrics', {})
    ann_ret = wm.get('annualized_return_pct', 0) if wm else 0
    sharpe = wm.get('sharpe_ratio', 0) if wm else 0
    wf = 'PASS' if r.get('wf_pass') else 'FAIL'
    pbo = r.get('cpcv_pbo', 1.0)
    cpcv = 'PASS' if r.get('cpcv_pass') else 'FAIL'
    status = 'VALIDATED' if r.get('validated') else 'REJECTED'
    print(f"  [{idx}/{total}] {tk:>8s}  AnnRet={ann_ret:+6.1f}%  "
          f"Sharpe={sharpe:+5.2f}  WF={wf}  PBO={pbo:.0%}  CPCV={cpcv}  → {status}")


def _print_validation_table(results: Dict, run_wf: bool, run_cpcv: bool):
    """Print the final validation table with full quant metrics."""
    width = 180
    print()
    print("=" * width)

    # Two-row header for compactness
    hdr1 = "{:>8s}".format("Token")
    hdr2 = "{:>8s}".format("")
    if run_wf:
        hdr1 += "  {:>7s}  {:>6s}  {:>7s}  {:>7s}  {:>6s}".format(
            "AnnRet", "Sharpe", "Sortno", "MaxDD", "Calmar")
        hdr1 += "  {:>5s}  {:>6s}  {:>6s}".format("WinR%", "ProfF", "Payoff")
        hdr1 += "  {:>5s}  {:>5s}  {:>6s}  {:>5s}  {:>6s}".format(
            "Trds", "AvgHH", "Beta", "Corr", "Alpha")
        hdr1 += "  {:>4s}".format("WF")
    if run_cpcv:
        hdr1 += "  {:>5s}  {:>7s}  {:>4s}".format("PBO", "Folds+", "CPCV")
    hdr1 += "  {:>10s}".format("Status")
    print(hdr1)
    print("-" * width)

    sorted_results = sorted(results.items(),
                            key=lambda x: x[1].get('wf_metrics', {}).get('annualized_return_pct', 0)
                            if x[1].get('wf_metrics') else 0,
                            reverse=True)

    for tk, r in sorted_results:
        wm = r.get('wf_metrics', {})
        line = "  {:>8s}".format(tk)

        if run_wf and wm:
            ann = wm.get('annualized_return_pct', 0)
            sharpe = wm.get('sharpe_ratio', 0)
            sortino = wm.get('sortino_ratio', 0)
            maxdd = wm.get('max_drawdown_pct', 0)
            calmar = wm.get('calmar_ratio', 0)
            win_rate = wm.get('win_rate_pct', 0)
            profit_f = wm.get('profit_factor', 0)
            payoff = wm.get('payoff_ratio', 0)
            trades = wm.get('total_trades', 0)
            avg_hh = wm.get('avg_hold_hours', 0)
            beta = wm.get('beta', 0)
            corr = wm.get('correlation', 0)
            alpha = wm.get('alpha_annualized', 0)
            wf_str = 'PASS' if r.get('wf_pass') else 'FAIL'
            line += "  {:+7.1f}%  {:+6.2f}  {:+7.2f}  {:+7.1f}%  {:+6.2f}".format(
                ann, sharpe, sortino, maxdd, calmar)
            line += "  {:5.1f}  {:6.2f}  {:6.2f}".format(win_rate, profit_f, payoff)
            line += "  {:5d}  {:5.0f}  {:6.4f}  {:5.2f}  {:+6.1f}".format(
                trades, avg_hh, beta, corr, alpha)
            line += "  {:>4s}".format(wf_str)
        elif run_wf:
            line += "  {:>7s}  {:>6s}  {:>7s}  {:>7s}  {:>6s}".format(
                "---", "---", "---", "---", "---")
            line += "  {:>5s}  {:>6s}  {:>6s}".format("---", "---", "---")
            line += "  {:>5s}  {:>5s}  {:>6s}  {:>5s}  {:>6s}".format(
                "---", "---", "---", "---", "---")
            line += "  {:>4s}".format("FAIL")

        if run_cpcv:
            pbo = r.get('cpcv_pbo', 1.0)
            fp = r.get('cpcv_folds_profitable', 0)
            tf = r.get('cpcv_total_folds', 0)
            cpcv_str = 'PASS' if r.get('cpcv_pass') else 'FAIL'
            line += "  {:5.0%}  {:2d}/{:<2d}    {:>4s}".format(pbo, fp, tf, cpcv_str)

        status = 'VALIDATED' if r.get('validated') else 'REJECTED'
        line += "  {:>10s}".format(status)
        print(line)

    print("-" * width)


def _save_results(results: Dict, strategy_name: str, config: ValidationConfig,
                  elapsed: float, validated_tokens: list):
    """Save validation results to JSON."""
    results_dir = os.path.join(os.path.dirname(_v3_dir), 'results')
    os.makedirs(results_dir, exist_ok=True)

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    fname = os.path.join(results_dir, f'validation_{strategy_name}_{timestamp}.json')

    report = {
        'strategy': strategy_name,
        'timestamp': datetime.now().isoformat(),
        'config': {
            'wf': asdict(config.wf),
            'cpcv': asdict(config.cpcv),
            'capital': config.capital,
            'pbo_threshold': config.pbo_threshold,
        },
        'elapsed_seconds': elapsed,
        'validated_tokens': validated_tokens,
        'n_validated': len(validated_tokens),
        'n_total': len(results),
        'per_token': results,
    }

    with open(fname, 'w') as f:
        json.dump(report, f, indent=2, default=str)
    print(f"  Results saved to {fname}")


# =============================================================================
# Strategy Resolution
# =============================================================================

def _resolve_strategy_path(strategy_arg: str) -> str:
    """Resolve a strategy argument to an absolute .py path."""
    # Direct path
    if os.path.isfile(strategy_arg):
        return os.path.abspath(strategy_arg)

    _root_dir = os.path.dirname(_v3_dir)

    # Short name like "s11" → look in top-level strategies/
    strats_dir = os.path.join(_root_dir, 'strategies')
    if os.path.isdir(strats_dir):
        for fname in os.listdir(strats_dir):
            if fname.startswith(strategy_arg) and fname.endswith('.py'):
                return os.path.join(strats_dir, fname)
        # Try as module name
        candidate = os.path.join(strats_dir, f'{strategy_arg}.py')
        if os.path.isfile(candidate):
            return candidate

    # Fallback: legacy v2/strategies/ path
    v2_strats = os.path.join(_root_dir, 'v2', 'strategies')
    if os.path.isdir(v2_strats):
        for fname in os.listdir(v2_strats):
            if fname.startswith(strategy_arg) and fname.endswith('.py'):
                return os.path.join(v2_strats, fname)

    raise FileNotFoundError(f"Cannot find strategy: {strategy_arg}\n"
                            f"Tried: {strategy_arg}, strategies/{strategy_arg}*.py")


# =============================================================================
# CLI
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description='V3 Validation Engine')
    parser.add_argument('--strategy', nargs='+', required=True,
                        help='Strategy names or paths (e.g., s11 s09 or path/to/strat.py)')
    parser.add_argument('--tokens', nargs='+', default=None,
                        help='Tokens to validate (default: all liquid)')
    parser.add_argument('--workers', type=int, default=4,
                        help='Parallel workers')
    parser.add_argument('--wf-only', action='store_true',
                        help='Run walk-forward only')
    parser.add_argument('--cpcv-only', action='store_true',
                        help='Run CPCV only')
    parser.add_argument('--train-days', type=int, default=365,
                        help='Walk-forward training window (days)')
    parser.add_argument('--recal-days', type=int, default=90,
                        help='Walk-forward recalibration interval (days)')
    parser.add_argument('--purge-days', type=int, default=5,
                        help='Walk-forward purge window (days)')
    parser.add_argument('--cpcv-groups', type=int, default=6,
                        help='CPCV number of groups')
    parser.add_argument('--pbo-threshold', type=float, default=0.40,
                        help='PBO threshold for CPCV pass')
    parser.add_argument('--capital', type=float, default=200_000,
                        help='Starting capital')
    parser.add_argument('--data-dir', type=str, default='data',
                        help='Data directory')
    args = parser.parse_args()

    run_wf = not args.cpcv_only
    run_cpcv = not args.wf_only

    config = ValidationConfig(
        wf=WalkForwardConfig(
            train_days=args.train_days,
            recalibrate_every=args.recal_days,
            purge_days=args.purge_days,
        ),
        cpcv=CPCVConfig(
            n_groups=args.cpcv_groups,
            n_test_groups=2,
            purge_pct=0.01,
            warmup_bars=200,
        ),
        capital=args.capital,
        pbo_threshold=args.pbo_threshold,
        data_dir=args.data_dir,
        workers=args.workers,
    )

    tokens = args.tokens if args.tokens else None

    for strat_arg in args.strategy:
        try:
            strat_path = _resolve_strategy_path(strat_arg)
            print(f"\nStrategy: {strat_arg} → {strat_path}")
        except FileNotFoundError as e:
            print(f"ERROR: {e}")
            continue

        validate_strategy(
            strategy_path=strat_path,
            tokens=tokens,
            config=config,
            run_wf=run_wf,
            run_cpcv=run_cpcv,
        )


if __name__ == '__main__':
    main()
