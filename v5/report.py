"""V5 Portfolio Backtest — Metrics computation and output formatting.

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

from v5.metrics import compute_metrics, build_equity_curve, PerformanceMetrics, load_benchmark_returns

from .position import ClosedTrade
from .simulator import SimulationState, RejectionStats, SignalDiagnostics


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
        "margin_calls": getattr(state, 'margin_calls', 0),
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
        for reason in ["portfolio_limit", "strategy_limit", "min_size", "adv_cap", "concentration", "capital", "direction_zero"]:
            if rej[reason] > 0:
                print(f"    {reason:20s} {rej[reason]:>6d}")
        print(f"    {'total':20s} {rej['total']:>6d}")
        if pf > 0:
            print(f"  Partial Fills:     {pf:>6d}")
    mc = extra_info.get("margin_calls", 0)
    if mc > 0:
        print(f"  Margin Calls:      {mc:>6d}")

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


def print_diagnostic_report(
    state: SimulationState,
    all_signals: dict,
):
    """Print signal funnel diagnostic report.

    Shows per-strategy signal funnel (raw -> liquidity -> WF -> opened)
    and rejection breakdown.
    """
    print("\n" + "=" * 70)
    print("  DIAGNOSTIC REPORT")
    print("=" * 70)

    # Aggregate signal funnel from TokenSignals
    strategy_funnel: dict[str, dict[str, int]] = {}
    for strategy_id, token_signals in all_signals.items():
        funnel = {"raw": 0, "post_liquidity": 0, "post_walkforward": 0, "tokens": 0}
        for token, sig in token_signals.items():
            funnel["raw"] += sig.raw_entry_count
            funnel["post_liquidity"] += sig.post_liquidity_count
            funnel["post_walkforward"] += sig.post_walkforward_count
            funnel["tokens"] += 1
        strategy_funnel[strategy_id] = funnel

    # Per-strategy signal funnel
    print("\n  Signal Funnel (per strategy):")
    print(f"  {'Strategy':>10s}  {'Tokens':>6s}  {'Raw':>8s}  {'PostLiq':>8s}  {'PostWF':>8s}  {'Opened':>8s}")
    print(f"  {'-'*10}  {'-'*6}  {'-'*8}  {'-'*8}  {'-'*8}  {'-'*8}")
    for sid in sorted(strategy_funnel.keys()):
        f = strategy_funnel[sid]
        opened = state.diagnostics.entries_opened.get(sid, 0)
        print(f"  {sid:>10s}  {f['tokens']:>6d}  {f['raw']:>8d}  {f['post_liquidity']:>8d}  "
              f"{f['post_walkforward']:>8d}  {opened:>8d}")

    # Rejection breakdown
    rej = state.rejections.to_dict()
    has_any = any(count > 0 for reason, count in rej.items() if reason != "total")
    if has_any:
        print("\n  Rejection Breakdown:")
        for reason, count in sorted(rej.items()):
            if reason != "total" and count > 0:
                print(f"    {reason:20s} {count:>6d}")
        print(f"    {'total':20s} {rej['total']:>6d}")

    # Funnel leakage check: entries that disappeared without being opened or rejected
    total_post_wf = sum(f["post_walkforward"] for f in strategy_funnel.values())
    total_opened = sum(state.diagnostics.entries_opened.get(sid, 0) for sid in strategy_funnel)
    total_rejected = rej["total"] + rej.get("raw_combined_skip", 0)
    leakage = total_post_wf - total_opened - total_rejected
    if leakage > 0:
        print(f"\n  WARNING: Funnel leakage = {leakage} entries unaccounted for")
        print(f"    post_walkforward={total_post_wf}  opened={total_opened}  rejected={total_rejected}")

    # Per-token signal summary (top-N tokens by signal count)
    token_signals_count: dict[str, int] = {}
    for strategy_id, token_signals in all_signals.items():
        for token, sig in token_signals.items():
            token_signals_count[token] = token_signals_count.get(token, 0) + sig.post_walkforward_count

    if token_signals_count:
        top_tokens = sorted(token_signals_count.items(), key=lambda x: x[1], reverse=True)[:15]
        print(f"\n  Top {len(top_tokens)} Tokens by Signal Count (post-WF):")
        for token, count in top_tokens:
            if count > 0:
                print(f"    {token:12s} {count:>6d}")

    print("=" * 70)
