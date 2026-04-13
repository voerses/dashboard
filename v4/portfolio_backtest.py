#!/usr/bin/env python3
"""V4 Portfolio Backtest — CLI entry point and orchestrator.

Usage:
    python v4/portfolio_backtest.py --strategy s30 --months 12
    python v4/portfolio_backtest.py --strategy s30,s32 --months 6 --capital 200000
    python v4/portfolio_backtest.py --strategy s30 --capital 100000,500000,1000000,5000000  # sweep
"""
from __future__ import annotations

import argparse
import dataclasses
import os
import sys
import time
from pathlib import Path

import pandas as pd

# Ensure project root is importable
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from v4.config import PortfolioConfig, StrategySpec
from v4.signals import precompute_strategy_signals, discover_tokens, infer_data_end_date
from v4.simulator import simulate_portfolio
from v4.report import compute_portfolio_metrics, print_report, save_results, print_diagnostic_report
from v4.data_maintenance import ensure_data_fresh


def _detect_strategy_type(strategy_id: str) -> str:
    """Detect if a strategy is per_token (Class A) or portfolio (Class B).

    Checks for STRATEGY_TYPE module attribute in the strategy file.
    """
    import importlib.util
    strategies_dir = os.path.join(str(PROJECT_ROOT), "strategies")
    for fname in os.listdir(strategies_dir):
        if (fname == strategy_id + ".py" or fname.startswith(strategy_id + "_")) and fname.endswith(".py"):
            fpath = os.path.join(strategies_dir, fname)
            spec = importlib.util.spec_from_file_location(f"_detect_{strategy_id}", fpath)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return getattr(mod, 'STRATEGY_TYPE', 'per_token')
    return 'per_token'


def _load_strategy_module_attrs(strategy_id: str) -> dict:
    """Load optional module-level attributes from strategy file.

    Returns dict with keys: sizing_overrides, regime_params (if present).
    """
    import importlib.util
    result = {}
    strategies_dir = os.path.join(str(PROJECT_ROOT), "strategies")
    for fname in os.listdir(strategies_dir):
        if (fname == strategy_id + ".py" or fname.startswith(strategy_id + "_")) and fname.endswith(".py"):
            fpath = os.path.join(strategies_dir, fname)
            spec = importlib.util.spec_from_file_location(f"_attrs_{strategy_id}", fpath)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            if hasattr(mod, 'SIZING_OVERRIDES'):
                result['sizing_overrides'] = mod.SIZING_OVERRIDES
            if hasattr(mod, 'REGIME_PARAMS'):
                result['regime_params'] = mod.REGIME_PARAMS
            if hasattr(mod, 'MAX_CONCURRENT_PER_TOKEN'):
                result['max_concurrent_per_token'] = mod.MAX_CONCURRENT_PER_TOKEN
            if hasattr(mod, 'DD_SCALING'):
                result['dd_scaling'] = mod.DD_SCALING
            if hasattr(mod, 'PORTFOLIO_CONFIG'):
                result['portfolio_config'] = mod.PORTFOLIO_CONFIG
            break
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="V4 Portfolio Backtest Engine")
    parser.add_argument("--strategy", type=str, required=True,
                        help="Comma-separated strategy IDs (e.g. s30,s32)")
    parser.add_argument("--months", type=int, default=12,
                        help="Lookback period in months (default: 12)")
    parser.add_argument("--capital", type=str, default="200000",
                        help="Initial capital (single or comma-separated for sweep)")
    parser.add_argument("--exchange", type=str, default="binance",
                        help="Exchange (default: binance)")
    parser.add_argument("--concentration", type=float, default=0.10,
                        help="Per-token concentration limit (default: 0.10)")
    parser.add_argument("--adv-cap", type=float, default=0.05,
                        help="ADV hard cap (default: 0.05)")
    parser.add_argument("--max-positions", type=int, default=None,
                        help="Per-strategy max positions (default: from strategy or 15)")
    parser.add_argument("--max-portfolio-positions", type=int, default=None,
                        help="Portfolio-wide max positions (default: from strategy or 40)")
    parser.add_argument("--conviction-mode", type=str, default=None,
                        choices=["shuffle", "ranked", "hybrid"],
                        help="Entry conviction mode (default: from strategy or shuffle)")
    parser.add_argument("--seed", type=int, default=42,
                        help="Random seed (default: 42)")
    parser.add_argument("--output", type=str, default="results/v4",
                        help="Output directory for JSON results")
    parser.add_argument("--market", type=str, required=True,
                        choices=["spot", "perp", "combined"],
                        help="Market type for all strategies (spot/perp/combined) — REQUIRED")
    parser.add_argument("--raw", action="store_true",
                        help="Raw mode: skip portfolio constraints, use strategy's own sizing")
    parser.add_argument("--skip-wf", action="store_true",
                        help="Skip walk-forward masking (orthogonal to --raw)")
    parser.add_argument("--refresh", action="store_true",
                        help="Fetch fresh data before running backtest (AC12)")
    parser.add_argument("--oos-monthly", action="store_true",
                        help="Run month-by-month OOS with hard data cap (AC13)")
    parser.add_argument("--end-date", type=str, default=None,
                        help="Pin backtest end date (e.g. 2026-04-05) for reproducibility. "
                             "Default: use latest available data.")
    parser.add_argument("--entry-delay", type=int, default=0,
                        help="Armed entry delay in bars (0=immediate, 120=5d). "
                             "Signal reserves slot, entry at delayed bar's price.")
    return parser.parse_args()


def run_backtest(
    strategy_ids: list[str],
    months: int,
    capital: float,
    config: PortfolioConfig,
    market: str,
    precomputed_signals: dict | None = None,
    end_date: pd.Timestamp | None = None,
    per_strategy_max_positions: int | None = None,
) -> tuple:
    """Run a single backtest with given parameters.

    Args:
        market: Market type — "spot", "perp", or "combined". Required.
        per_strategy_max_positions: Per-strategy position cap. If None, derived
            from max_portfolio_positions split across strategies.

    Returns (metrics, extra_info, trades, precomputed_signals, eq_daily)
    """
    if market not in ("spot", "perp", "combined"):
        raise ValueError(f"Invalid market '{market}'. Must be 'spot', 'perp', or 'combined'.")
    strategy_specs = {}
    n_strats = len(strategy_ids)
    for sid in strategy_ids:
        stype = _detect_strategy_type(sid)
        mod_attrs = _load_strategy_module_attrs(sid)
        pconf = mod_attrs.get('portfolio_config', {})
        # Per-strategy max_positions: explicit arg > PORTFOLIO_CONFIG > split from portfolio max
        if per_strategy_max_positions is not None:
            max_pos = per_strategy_max_positions
        elif 'max_positions' in pconf:
            max_pos = pconf['max_positions']
        else:
            max_pos = config.max_portfolio_positions // n_strats if n_strats > 1 else config.max_portfolio_positions
        spec = StrategySpec(
            strategy_id=sid,
            weight=1.0 / n_strats,
            max_positions=max_pos,
            market=market,
            strategy_type=stype,
            sizing_overrides=mod_attrs.get('sizing_overrides', {}),
            regime_params=mod_attrs.get('regime_params', None),
            max_concurrent_per_token=mod_attrs.get('max_concurrent_per_token', 1),
            dd_scaling=mod_attrs.get('dd_scaling', []),
            entry_resolution=pconf.get('entry_resolution', 0),
        )
        # Cap per-strategy positions at portfolio max
        spec.max_positions = min(spec.max_positions, config.max_portfolio_positions)
        strategy_specs[sid] = spec

    # Precompute signals (or reuse)
    if precomputed_signals is None:
        precomputed_signals = {}
        for sid, spec in strategy_specs.items():
            tokens = discover_tokens(spec.market)
            print(f"\n  Precomputing signals for {sid} ({len(tokens)} tokens, {spec.market})...")
            t0 = time.time()
            signals = precompute_strategy_signals(spec, tokens, config, months, end_date=end_date)
            print(f"  Done: {len(signals)} tokens with signals ({time.time()-t0:.1f}s)")
            precomputed_signals[sid] = signals

    # Run simulation
    config_run = dataclasses.replace(config,
        strategies=list(strategy_specs.values()),
        capital=capital,
    )

    print(f"\n  Simulating portfolio (capital=${capital:,.0f})...")
    t0 = time.time()
    state = simulate_portfolio(precomputed_signals, strategy_specs, config_run)
    print(f"  Done: {len(state.position_manager.closed_trades)} trades ({time.time()-t0:.1f}s)")

    metrics, extra_info, eq_daily = compute_portfolio_metrics(state, capital)
    return metrics, extra_info, state.position_manager.closed_trades, precomputed_signals, eq_daily


def run_oos_monthly(
    strategy_ids: list[str],
    oos_months: int,
    capital: float,
    config: PortfolioConfig,
    market: str,
    per_strategy_max_positions: int | None = None,
) -> list[dict]:
    """Run month-by-month OOS backtest with compounding equity.

    Each month is an independent simulation where:
    - Signals are recomputed with end_date=month_end (no forward bias)
    - Starting capital = previous month's ending equity (compounding)
    - Positions start fresh each month (no carry-over)

    Returns a list of per-month result dicts.
    """
    data_end = infer_data_end_date(market)
    results = []
    running_capital = capital

    for i in range(oos_months):
        # Compute end_date for this month (counting backwards from data_end)
        month_offset = oos_months - 1 - i
        end_date = data_end - pd.DateOffset(months=month_offset)
        # Snap to month end, but never exceed actual data boundary.
        # Note: if data_end is mid-month, the final month is shorter and may
        # overlap with the previous month's lookback window. This is expected —
        # each month's signal computation is independent with its own end_date cap.
        end_date = end_date + pd.offsets.MonthEnd(0)
        clamped = end_date > data_end
        end_date = min(end_date, data_end)

        label_suffix = " (partial)" if clamped else ""
        print(f"\n  OOS Month {i+1}/{oos_months}: capital=${running_capital:,.0f}, "
              f"end_date={end_date.strftime('%Y-%m-%d')}{label_suffix}")

        metrics, extra_info, trades, signals, eq_daily = run_backtest(
            strategy_ids=strategy_ids,
            months=1,
            capital=running_capital,
            config=config,
            market=market,
            end_date=end_date,
            per_strategy_max_positions=per_strategy_max_positions,
        )

        month_label = end_date.strftime("%Y-%m")
        total_return = metrics.get("total_return_pct", 0) if isinstance(metrics, dict) else getattr(metrics, "total_return_pct", 0)
        max_dd = metrics.get("max_drawdown_pct", 0) if isinstance(metrics, dict) else getattr(metrics, "max_drawdown_pct", 0)
        final_equity = extra_info.get("final_equity", running_capital) if isinstance(extra_info, dict) else getattr(extra_info, "final_equity", running_capital)

        results.append({
            "month": month_label,
            "end_date": str(end_date),
            "metrics": metrics,
            "extra_info": extra_info,
            "start_capital": running_capital,
            "final_equity": final_equity,
            "total_return_pct": total_return,
            "max_drawdown_pct": max_dd,
        })

        # Compound: next month starts with this month's ending equity
        running_capital = final_equity

    # Print per-month table
    cumulative_return = (running_capital / capital - 1) * 100
    print(f"\n{'=' * 70}")
    print(f"  OOS MONTHLY RESULTS — COMPOUNDING ({oos_months} months)")
    print(f"{'=' * 70}")
    print(f"  {'Month':>10s}  {'Capital':>12s}  {'Return':>8s}  {'MaxDD':>8s}  {'Equity':>12s}")
    print(f"  {'-'*10}  {'-'*12}  {'-'*8}  {'-'*8}  {'-'*12}")
    for r in results:
        ret = r["total_return_pct"]
        dd = r["max_drawdown_pct"]
        print(f"  {r['month']:>10s}  ${r['start_capital']:>11,.0f}  {ret:>+7.1f}%  {dd:>7.1f}%  ${r['final_equity']:>11,.0f}")
    print(f"  {'-'*10}  {'-'*12}  {'-'*8}  {'-'*8}  {'-'*12}")
    print(f"  {'TOTAL':>10s}  ${capital:>11,.0f}  {cumulative_return:>+7.1f}%  {'':>8s}  ${running_capital:>11,.0f}")
    print(f"{'=' * 70}")

    return results


def main():
    args = parse_args()

    strategy_ids = [s.strip() for s in args.strategy.split(",")]
    capital_levels = [float(c.strip()) for c in args.capital.split(",")]
    is_sweep = len(capital_levels) > 1

    # Build config — merge strategy-declared PORTFOLIO_CONFIG as defaults
    market = args.market

    # Collect PORTFOLIO_CONFIG from all strategies (later strategies override earlier)
    merged_portfolio = {}
    for sid in strategy_ids:
        mod_attrs = _load_strategy_module_attrs(sid)
        pconf = mod_attrs.get('portfolio_config', {})
        merged_portfolio.update(pconf)

    # CLI args override strategy defaults; None means "not explicitly provided"
    max_portfolio_pos = (
        args.max_portfolio_positions
        if args.max_portfolio_positions is not None
        else merged_portfolio.get('max_portfolio_positions', 40)
    )
    conviction_mode = (
        args.conviction_mode
        if args.conviction_mode is not None
        else merged_portfolio.get('conviction_mode', 'shuffle')
    )
    per_strategy_max = (
        args.max_positions
        if args.max_positions is not None
        else None  # let run_backtest() resolve from PORTFOLIO_CONFIG or portfolio max
    )

    config = PortfolioConfig(
        exchange=args.exchange,
        concentration_limit=args.concentration,
        adv_cap_pct=args.adv_cap,
        max_portfolio_positions=max_portfolio_pos,
        conviction_mode=conviction_mode,
        seed=args.seed,
        raw_mode=args.raw,
        skip_walk_forward=args.skip_wf,
        max_sizing_equity=merged_portfolio.get('max_sizing_equity', None),
        entry_delay_bars=args.entry_delay,
    )

    # Print which config came from the strategy module
    if merged_portfolio:
        # Show all strategy-declared params (portfolio-level applied here, per-spec applied in run_backtest)
        parts = [f"{k}={v}" for k, v in sorted(merged_portfolio.items())]
        print(f"  Config from strategy: {', '.join(parts)}")
        # Show effective values after CLI override
        print(f"  Effective: max_portfolio_positions={max_portfolio_pos}, conviction_mode={conviction_mode}")

    # AC12/AC15: --refresh calls ensure_data_fresh before backtest
    if args.refresh:
        try:
            # Create a ccxt exchange for data fetching
            import ccxt
            refresh_exchange = ccxt.binance({"enableRateLimit": True})
            refresh_summary = ensure_data_fresh(
                exchange=refresh_exchange,
                data_dir="data",
                caller="backtest_refresh",
            )
            print(f"  Data refresh: {refresh_summary.get('gaps_filled', 0)} gaps filled, "
                  f"{refresh_summary.get('bars_fetched', 0)} bars fetched, "
                  f"{refresh_summary.get('tokens_promoted', 0)} tokens promoted")
        except Exception as e:
            print(f"  WARNING: Data refresh failed: {e}. Running backtest on existing data.")

    # AC13/AC14: --oos-monthly routes to run_oos_monthly
    if args.oos_monthly:
        results = run_oos_monthly(
            strategy_ids=strategy_ids,
            oos_months=args.months,
            capital=capital_levels[0],
            config=config,
            market=market,
            per_strategy_max_positions=per_strategy_max,
        )
        # Save OOS monthly results to JSON
        try:
            import json
            os.makedirs(args.output, exist_ok=True)
            label = f"{'_'.join(strategy_ids)}_{args.months}mo_oos_monthly"
            out_path = os.path.join(args.output, f"{label}.json")
            # Serialize metrics dataclass and extra_info for JSON output
            serializable = []
            for r in results:
                entry = {k: v for k, v in r.items() if k not in ("metrics", "extra_info")}
                m = r.get("metrics")
                if m is not None and dataclasses.is_dataclass(m):
                    entry["metrics"] = dataclasses.asdict(m)
                elif isinstance(m, dict):
                    entry["metrics"] = m
                entry["extra_info"] = r.get("extra_info", {})
                serializable.append(entry)
            with open(out_path, "w") as f:
                json.dump(serializable, f, indent=2, default=str)
            print(f"  Results saved to {out_path}")
        except Exception as e:
            print(f"  WARNING: Could not save OOS results: {e}")
        return

    print("=" * 70)
    print("  V4 PORTFOLIO BACKTEST ENGINE")
    print("=" * 70)
    print(f"  Strategies: {', '.join(strategy_ids)}")
    print(f"  Months:     {args.months}")
    print(f"  Capital:    {', '.join(f'${c:,.0f}' for c in capital_levels)}")
    print(f"  Exchange:   {args.exchange}")
    print(f"  Market:     {market}")
    print(f"  Seed:       {args.seed}")
    if args.raw:
        print(f"  Raw Mode:   ON (portfolio constraints disabled)")
        if not args.skip_wf:
            print(f"  NOTE: --raw without --skip-wf still burns {config.train_bars}h training window.")
            print(f"         Add --skip-wf to use full data range for signal research.")
    if args.skip_wf:
        print(f"  Skip WF:    ON (walk-forward masking disabled)")

    # Infer data end date for deterministic backtesting
    if args.end_date:
        data_end = pd.Timestamp(args.end_date)
        print(f"  Data End:   {data_end.strftime('%Y-%m-%d %H:%M')} (pinned via --end-date)")
    else:
        data_end = infer_data_end_date(market)
        print(f"  Data End:   {data_end.strftime('%Y-%m-%d %H:%M')} (from data)")

    # Run simulation(s) via run_backtest()
    all_results = []
    shared_signals = None
    for capital in capital_levels:
        metrics, extra_info, trades, shared_signals, eq_daily = run_backtest(
            strategy_ids=strategy_ids,
            months=args.months,
            capital=capital,
            config=config,
            market=market,
            precomputed_signals=shared_signals,
            end_date=data_end,
            per_strategy_max_positions=per_strategy_max,
        )
        all_results.append((capital, metrics, extra_info, trades, eq_daily))

        try:
            print_report(metrics, extra_info, capital, strategy_ids)
        except Exception:
            pass  # Report printing is best-effort (may fail with mocked data)

        # Save results
        label = f"{'_'.join(strategy_ids)}_{args.months}mo_{int(capital/1000)}k"
        try:
            save_results(metrics, extra_info, trades,
                         args.output, label, equity_curve=eq_daily)
        except Exception:
            pass

    # Capital sweep comparison table
    if is_sweep:
        print("\n" + "=" * 70)
        print("  CAPITAL SWEEP COMPARISON")
        print("=" * 70)
        print(f"  {'Capital':>12s}  {'Return':>8s}  {'Sharpe':>7s}  {'MaxDD':>7s}  {'Trades':>7s}  {'Rej%':>6s}")
        print(f"  {'-'*12}  {'-'*8}  {'-'*7}  {'-'*7}  {'-'*7}  {'-'*6}")
        for capital, metrics, extra_info, trades, eq_daily in all_results:
            total_return = (extra_info["final_equity"] / capital - 1) * 100
            rej = extra_info["rejections"]
            total_candidates = metrics.total_trades + rej["total"]
            rej_pct = rej["total"] / max(total_candidates, 1) * 100
            print(f"  ${capital:>11,.0f}  {total_return:>+7.1f}%  {metrics.sharpe_ratio:>7.2f}  "
                  f"{metrics.max_drawdown_pct:>6.1f}%  {metrics.total_trades:>7d}  {rej_pct:>5.1f}%")
        print("=" * 70)


if __name__ == "__main__":
    main()
