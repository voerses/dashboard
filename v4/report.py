"""V4 Portfolio Backtest — Metrics computation and output formatting.

Reuses v3/metrics.py compute_metrics() with portfolio-level additions:
  - Rejection breakdown by reason
  - Per-strategy attribution
  - Exposure by market/direction
"""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

# Import v3 metrics
_v3_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "v3")
if _v3_dir not in sys.path:
    sys.path.insert(0, _v3_dir)
from metrics import compute_metrics, build_equity_curve, PerformanceMetrics, load_benchmark_returns

from .position import ClosedTrade
from .simulator import SimulationState, RejectionStats


def _trades_to_dicts(trades: list[ClosedTrade]) -> list[dict]:
    """Convert ClosedTrade objects to dicts matching v3 metrics format."""
    result = []
    for t in trades:
        result.append({
            "pnl": t.pnl,
            "hold_hours": t.hold_bars,  # 1h bars
            "exit_bar": t.exit_bar,
            "entry_bar": t.entry_bar,
            "funding_cost": t.funding_cost,
            "position_id": t.position_id,
            "token": t.token,
            "strategy_id": t.strategy_id,
            "leg": t.leg,
            "direction": t.direction,
            "margin_usd": t.margin_usd,
            "entry_price": t.entry_price,
            "exit_price": t.exit_price,
            "entry_fee": t.entry_fee,
            "exit_fee": t.exit_fee,
            "exit_reason": t.exit_reason,
            "is_perp": t.is_perp,
        })
    return result


def compute_portfolio_metrics(
    state: SimulationState,
    config_capital: float,
) -> tuple[PerformanceMetrics, dict, pd.Series]:
    """Compute portfolio-level metrics from simulation state.

    Returns:
        (metrics, extra_info, eq_daily) where extra_info has rejections, per-strategy stats, exposure.
    """
    trades = state.position_manager.closed_trades
    trade_dicts = _trades_to_dicts(trades)

    # Build equity curve from snapshots
    if state.equity_snapshots:
        timestamps = [ts for ts, _ in state.equity_snapshots]
        equities = [eq for _, eq in state.equity_snapshots]
        eq_index = pd.DatetimeIndex(timestamps)
        eq_series = pd.Series(equities, index=eq_index)
        # Resample to daily
        eq_daily = eq_series.resample("1D").last().dropna()
    else:
        eq_daily = pd.Series([config_capital])

    # Load benchmark
    data_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    benchmark = load_benchmark_returns(data_dir, market="perp")

    metrics = compute_metrics(trade_dicts, eq_daily, benchmark_returns=benchmark, capital=config_capital)

    # Per-strategy attribution
    strategy_stats = defaultdict(lambda: {"trades": 0, "pnl": 0.0, "funding": 0.0})
    for t in trades:
        s = strategy_stats[t.strategy_id]
        s["trades"] += 1
        s["pnl"] += t.pnl
        s["funding"] += t.funding_cost

    # Exposure by market type and direction
    exposure = {
        "spot_long": 0, "spot_short": 0,
        "perp_long": 0, "perp_short": 0,
    }
    for t in trades:
        market = "perp" if t.is_perp else "spot"
        direction = "long" if t.direction == 1 else "short"
        key = f"{market}_{direction}"
        exposure[key] += 1

    extra_info = {
        "rejections": state.rejections.to_dict(),
        "partial_fills": state.partial_fills,
        "strategy_attribution": dict(strategy_stats),
        "exposure": exposure,
        "total_trades": len(trades),
        "total_funding": state.total_funding,
        "total_fees": state.total_fees,
        "final_equity": state.portfolio_equity,
    }

    return metrics, extra_info, eq_daily


def print_report(
    metrics: PerformanceMetrics,
    extra_info: dict,
    config_capital: float,
    strategies: list[str],
):
    """Print console report."""
    final_eq = extra_info["final_equity"]
    total_return = (final_eq / config_capital - 1) * 100

    print("\n" + "=" * 70)
    print("  V4 PORTFOLIO BACKTEST RESULTS")
    print("=" * 70)
    print(f"  Strategies:      {', '.join(strategies)}")
    print(f"  Initial Capital: ${config_capital:,.0f}")
    print(f"  Final Equity:    ${final_eq:,.0f}")
    print(f"  Total Return:    {total_return:+.1f}%")
    print(f"  Annualized:      {metrics.annualized_return_pct:+.1f}%")
    print()
    print(f"  Sharpe:          {metrics.sharpe_ratio:.2f}")
    print(f"  Sortino:         {metrics.sortino_ratio:.2f}")
    print(f"  Calmar:          {metrics.calmar_ratio:.2f}")
    print(f"  Max Drawdown:    {metrics.max_drawdown_pct:.1f}%")
    print(f"  Max DD Duration: {metrics.max_drawdown_duration_days}d")
    print()
    print(f"  Total Trades:    {metrics.total_trades}")
    print(f"  Win Rate:        {metrics.win_rate_pct:.1f}%")
    print(f"  Payoff Ratio:    {metrics.payoff_ratio:.2f}")
    print(f"  Profit Factor:   {metrics.profit_factor:.2f}")
    print(f"  Avg Trade PnL:   ${metrics.avg_trade_pnl:.0f}")
    print(f"  Avg Hold:        {metrics.avg_hold_hours:.0f}h")
    print()
    print(f"  Total Fees:      ${extra_info['total_fees']:,.0f}")
    print(f"  Total Funding:   ${extra_info['total_funding']:,.0f}")

    # Rejections & partial fills
    rej = extra_info["rejections"]
    pf = extra_info.get("partial_fills", 0)
    if rej["total"] > 0 or pf > 0:
        print()
        print("  Entry Rejections:")
        for reason in ["portfolio_limit", "strategy_limit", "min_size", "adv_cap", "concentration", "capital"]:
            if rej[reason] > 0:
                print(f"    {reason:20s} {rej[reason]:>6d}")
        print(f"    {'total':20s} {rej['total']:>6d}")
        if pf > 0:
            print(f"  Partial Fills:     {pf:>6d}")

    # Per-strategy
    sa = extra_info["strategy_attribution"]
    if len(sa) > 1:
        print()
        print("  Per-Strategy Attribution:")
        for sid, stats in sorted(sa.items()):
            print(f"    {sid:8s}  trades={stats['trades']:4d}  pnl=${stats['pnl']:>+12,.0f}  funding=${stats['funding']:>+10,.0f}")

    # Sanity bounds
    print()
    if total_return < -50 or total_return > 500:
        print(f"  WARNING: Return {total_return:+.1f}% outside sanity bounds [-50%, +500%]")
    if metrics.sharpe_ratio < -1 or metrics.sharpe_ratio > 5:
        print(f"  WARNING: Sharpe {metrics.sharpe_ratio:.2f} outside sanity bounds [-1, 5]")
    if abs(metrics.max_drawdown_pct) < 5 or abs(metrics.max_drawdown_pct) > 80:
        print(f"  WARNING: Max DD {metrics.max_drawdown_pct:.1f}% outside sanity bounds [5%, 80%]")

    print("=" * 70)


def save_results(
    metrics: PerformanceMetrics,
    extra_info: dict,
    trade_log: list[ClosedTrade],
    output_dir: str,
    label: str,
    equity_curve: Optional[pd.Series] = None,
):
    """Save results as JSON files."""
    os.makedirs(output_dir, exist_ok=True)

    # Metrics summary
    metrics_dict = asdict(metrics)
    metrics_dict.update(extra_info)
    with open(os.path.join(output_dir, f"{label}_metrics.json"), "w") as f:
        json.dump(metrics_dict, f, indent=2, default=str)

    # Full trade log
    trades = _trades_to_dicts(trade_log)
    with open(os.path.join(output_dir, f"{label}_trades.json"), "w") as f:
        json.dump(trades, f, indent=2, default=str)

    # Daily equity curve
    if equity_curve is not None and len(equity_curve) > 0:
        eq_data = {str(dt.date()): round(val, 2) for dt, val in equity_curve.items()}
        with open(os.path.join(output_dir, f"{label}_equity_curve.json"), "w") as f:
            json.dump(eq_data, f, indent=2)

    print(f"  Results saved to {output_dir}/")
