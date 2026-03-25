#!/workspace/venv/bin/python
"""
Exit Features Ablation Test
============================

Tests three V4 exit features that are IMPLEMENTED but DISABLED in all strategies:
  1. trail_schedule — Progressive trail tightening based on profit in ATR
  2. rsi_exit_level — RSI-based exit (exit longs when RSI > threshold)
  3. chandelier_lookback — Chandelier stop (trail from N-bar lookback high/low)

Approach:
  - Run each strategy through the full V4 portfolio backtest engine
  - Precompute signals once per strategy, then patch TokenSignals exit params
  - Re-simulate for each configuration to isolate the effect of each feature
  - Test across 60mo, 12mo, 3mo periods
  - Compare Sharpe, Return, MaxDD, Win Rate, Avg PnL, Trade Count

Strategies tested:
  - s320a (Binary Gates, BTC spot)
  - s62 (Conservative Funding Carry, perp)
  - s65 (Funding Carry, perp)
"""
from __future__ import annotations

import copy
import os
import sys
import time
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path("/workspace/crypto_backtest")
sys.path.insert(0, str(PROJECT_ROOT))

from v4.config import PortfolioConfig, StrategySpec
from v4.signals import (
    TokenSignals,
    precompute_strategy_signals,
    discover_tokens,
    infer_data_end_date,
)
from v4.simulator import simulate_portfolio
from v4.report import compute_portfolio_metrics

# ---------------------------------------------------------------------------
# Configurations to test
# ---------------------------------------------------------------------------

TRAIL_SCHEDULE = np.array(
    [
        [1.0, 2.0],   # At 1 ATR profit: 2.0x trail
        [2.0, 1.5],   # At 2 ATR: tighten to 1.5
        [3.0, 1.0],   # At 3 ATR: very tight 1.0
        [5.0, 0.75],  # At 5 ATR: lock in profits
    ],
    dtype=np.float32,
)

RSI_EXIT_LEVEL = 73.0
CHANDELIER_LOOKBACK = 18

CONFIGS = {
    "baseline":    {"trail_schedule": None,           "rsi_exit_level": 999.0, "chandelier_lookback": 0},
    "trail_sched": {"trail_schedule": TRAIL_SCHEDULE, "rsi_exit_level": 999.0, "chandelier_lookback": 0},
    "rsi_exit":    {"trail_schedule": None,           "rsi_exit_level": RSI_EXIT_LEVEL, "chandelier_lookback": 0},
    "chandelier":  {"trail_schedule": None,           "rsi_exit_level": 999.0, "chandelier_lookback": CHANDELIER_LOOKBACK},
    "all_three":   {"trail_schedule": TRAIL_SCHEDULE, "rsi_exit_level": RSI_EXIT_LEVEL, "chandelier_lookback": CHANDELIER_LOOKBACK},
}

# Strategy definitions: id -> (market, description)
STRATEGIES = {
    "s320a": ("spot", "Binary Gates (BTC spot)"),
    "s62":   ("perp", "Conservative Funding Carry"),
    "s65":   ("perp", "Funding Carry"),
}

PERIODS = [60, 12, 3]  # months

CAPITAL = 200_000


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _detect_strategy_type(strategy_id: str) -> str:
    """Detect if a strategy is per_token or portfolio."""
    import importlib.util
    # Ensure v4/ is on sys.path so `from engine import ...` resolves
    v4_dir = str(PROJECT_ROOT / "v4")
    if v4_dir not in sys.path:
        sys.path.insert(0, v4_dir)
    strategies_dir = str(PROJECT_ROOT / "strategies")
    for fname in os.listdir(strategies_dir):
        if fname.startswith(strategy_id + "_") and fname.endswith(".py"):
            fpath = os.path.join(strategies_dir, fname)
            spec = importlib.util.spec_from_file_location(f"_detect_{strategy_id}", fpath)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return getattr(mod, "STRATEGY_TYPE", "per_token")
    return "per_token"


def patch_signals(
    signals: dict[str, TokenSignals],
    trail_schedule: Optional[np.ndarray],
    rsi_exit_level: float,
    chandelier_lookback: int,
) -> dict[str, TokenSignals]:
    """Deep-copy signals and patch exit parameters."""
    patched = {}
    for token, sig in signals.items():
        # Shallow copy the dataclass (arrays are not mutated, just replaced)
        new_sig = copy.copy(sig)
        new_sig.trail_schedule = trail_schedule
        new_sig.rsi_exit_level = rsi_exit_level
        new_sig.chandelier_lookback = chandelier_lookback
        patched[token] = new_sig
    return patched


def run_single(
    strategy_id: str,
    market: str,
    months: int,
    signals_base: dict[str, TokenSignals],
    config_label: str,
    config_params: dict,
    end_date: pd.Timestamp,
) -> dict:
    """Run one configuration and return metrics dict."""
    # Patch signals
    patched = patch_signals(
        signals_base,
        trail_schedule=config_params["trail_schedule"],
        rsi_exit_level=config_params["rsi_exit_level"],
        chandelier_lookback=config_params["chandelier_lookback"],
    )

    stype = _detect_strategy_type(strategy_id)
    spec = StrategySpec(
        strategy_id=strategy_id,
        weight=1.0,
        max_positions=15,
        market=market,
        strategy_type=stype,
    )

    config = PortfolioConfig(
        strategies=[spec],
        capital=CAPITAL,
        max_portfolio_positions=15,
        concentration_limit=0.10,
        adv_cap_pct=0.05,
        min_position_usd=200.0,
        exchange="binance",
        seed=42,
    )

    strategy_specs = {strategy_id: spec}
    all_signals = {strategy_id: patched}

    state = simulate_portfolio(all_signals, strategy_specs, config)
    metrics, extra_info, eq_daily = compute_portfolio_metrics(state, CAPITAL)

    trades = state.position_manager.closed_trades
    n_trades = metrics.total_trades

    return {
        "strategy": strategy_id,
        "config": config_label,
        "months": months,
        "sharpe": metrics.sharpe_ratio,
        "return_pct": metrics.total_return_pct,
        "max_dd_pct": metrics.max_drawdown_pct,
        "win_rate": metrics.win_rate_pct,
        "avg_pnl": metrics.avg_trade_pnl,
        "trades": n_trades,
        "calmar": metrics.calmar_ratio,
        "sortino": metrics.sortino_ratio,
        "profit_factor": metrics.profit_factor,
        "ann_return": metrics.annualized_return_pct,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 80)
    print("  EXIT FEATURES ABLATION TEST")
    print("  trail_schedule | rsi_exit_level | chandelier_lookback")
    print("=" * 80)

    all_results = []

    for strategy_id, (market, desc) in STRATEGIES.items():
        print(f"\n{'='*80}")
        print(f"  STRATEGY: {strategy_id} — {desc} (market={market})")
        print(f"{'='*80}")

        for months in PERIODS:
            print(f"\n  --- Period: {months}mo ---")

            # Determine end date
            end_date = infer_data_end_date(market)
            print(f"  Data end: {end_date.strftime('%Y-%m-%d %H:%M')}")

            # Precompute signals ONCE for this strategy/period
            stype = _detect_strategy_type(strategy_id)
            spec = StrategySpec(
                strategy_id=strategy_id,
                weight=1.0,
                max_positions=15,
                market=market,
                strategy_type=stype,
            )
            config_base = PortfolioConfig(
                capital=CAPITAL,
                max_portfolio_positions=15,
                exchange="binance",
                seed=42,
            )

            tokens = discover_tokens(market)
            print(f"  Precomputing signals ({len(tokens)} tokens)...")
            t0 = time.time()
            signals_base = precompute_strategy_signals(
                spec, tokens, config_base, months, end_date=end_date,
            )
            print(f"  Done: {len(signals_base)} tokens ({time.time()-t0:.1f}s)")

            if not signals_base:
                print(f"  WARNING: No signals for {strategy_id} at {months}mo — skipping")
                continue

            # Run each configuration
            for cfg_name, cfg_params in CONFIGS.items():
                t0 = time.time()
                try:
                    result = run_single(
                        strategy_id, market, months,
                        signals_base, cfg_name, cfg_params, end_date,
                    )
                    all_results.append(result)
                    print(
                        f"    {cfg_name:>12s}: Sharpe={result['sharpe']:>6.2f}  "
                        f"Return={result['return_pct']:>+7.1f}%  "
                        f"MaxDD={result['max_dd_pct']:>6.1f}%  "
                        f"WinR={result['win_rate']:>5.1f}%  "
                        f"AvgPnL=${result['avg_pnl']:>8.1f}  "
                        f"Trades={result['trades']:>4d}  "
                        f"({time.time()-t0:.1f}s)"
                    )
                except Exception as e:
                    print(f"    {cfg_name:>12s}: ERROR — {e}")
                    import traceback
                    traceback.print_exc()

    # ---------------------------------------------------------------------------
    # Summary tables
    # ---------------------------------------------------------------------------
    if not all_results:
        print("\nNo results to summarize.")
        return

    df = pd.DataFrame(all_results)

    print("\n\n")
    print("=" * 120)
    print("  FULL COMPARISON TABLE")
    print("=" * 120)

    for strategy_id in STRATEGIES:
        mask = df["strategy"] == strategy_id
        if not mask.any():
            continue
        sdf = df[mask].copy()
        print(f"\n  Strategy: {strategy_id} — {STRATEGIES[strategy_id][1]}")
        print(f"  {'Config':>12s} {'Period':>6s} {'Sharpe':>7s} {'Return%':>8s} {'MaxDD%':>7s} "
              f"{'WinR%':>6s} {'AvgPnL':>9s} {'Trades':>6s} {'Calmar':>7s} {'Sortino':>8s} {'PF':>5s}")
        print(f"  {'-'*12} {'-'*6} {'-'*7} {'-'*8} {'-'*7} {'-'*6} {'-'*9} {'-'*6} {'-'*7} {'-'*8} {'-'*5}")

        for _, row in sdf.sort_values(["months", "config"], ascending=[False, True]).iterrows():
            print(
                f"  {row['config']:>12s} {row['months']:>4d}mo "
                f"{row['sharpe']:>7.2f} {row['return_pct']:>+7.1f}% {row['max_dd_pct']:>6.1f}% "
                f"{row['win_rate']:>5.1f}% ${row['avg_pnl']:>8.1f} {row['trades']:>6d} "
                f"{row['calmar']:>7.2f} {row['sortino']:>8.2f} {row['profit_factor']:>5.2f}"
            )

    # ---------------------------------------------------------------------------
    # Delta analysis: improvement vs baseline
    # ---------------------------------------------------------------------------
    print("\n\n")
    print("=" * 120)
    print("  DELTA vs BASELINE (positive = improvement)")
    print("=" * 120)

    for strategy_id in STRATEGIES:
        mask = df["strategy"] == strategy_id
        if not mask.any():
            continue
        sdf = df[mask].copy()
        print(f"\n  Strategy: {strategy_id}")
        print(f"  {'Config':>12s} {'Period':>6s} {'dSharpe':>8s} {'dReturn':>8s} {'dMaxDD':>7s} "
              f"{'dWinR':>6s} {'dAvgPnL':>9s} {'dTrades':>7s}")
        print(f"  {'-'*12} {'-'*6} {'-'*8} {'-'*8} {'-'*7} {'-'*6} {'-'*9} {'-'*7}")

        for months in PERIODS:
            baseline = sdf[(sdf["months"] == months) & (sdf["config"] == "baseline")]
            if baseline.empty:
                continue
            bl = baseline.iloc[0]
            for _, row in sdf[sdf["months"] == months].sort_values("config").iterrows():
                if row["config"] == "baseline":
                    continue
                ds = row["sharpe"] - bl["sharpe"]
                dr = row["return_pct"] - bl["return_pct"]
                dd = row["max_dd_pct"] - bl["max_dd_pct"]  # more negative = worse
                dw = row["win_rate"] - bl["win_rate"]
                da = row["avg_pnl"] - bl["avg_pnl"]
                dt = row["trades"] - bl["trades"]
                # For MaxDD, positive delta means LESS drawdown (improvement)
                dd_display = -dd  # flip sign: positive = improved (smaller DD)
                print(
                    f"  {row['config']:>12s} {months:>4d}mo "
                    f"{ds:>+7.2f} {dr:>+7.1f}% {dd_display:>+6.1f}% "
                    f"{dw:>+5.1f}% ${da:>+8.1f} {dt:>+6.0f}"
                )

    # ---------------------------------------------------------------------------
    # Winner recommendation
    # ---------------------------------------------------------------------------
    print("\n\n")
    print("=" * 120)
    print("  RECOMMENDATION — Best Configuration per Strategy (12mo reference)")
    print("=" * 120)

    for strategy_id in STRATEGIES:
        mask = (df["strategy"] == strategy_id) & (df["months"] == 12)
        if not mask.any():
            # Try 60mo if 12mo unavailable
            mask = (df["strategy"] == strategy_id) & (df["months"] == 60)
            if not mask.any():
                continue
        sdf = df[mask].copy()

        # Rank by Sharpe (primary), then by return (secondary)
        sdf = sdf.sort_values(["sharpe", "return_pct"], ascending=[False, False])
        best = sdf.iloc[0]
        baseline = sdf[sdf["config"] == "baseline"]
        bl = baseline.iloc[0] if not baseline.empty else best

        print(f"\n  {strategy_id}:")
        print(f"    Best config: {best['config']}")
        print(f"    Sharpe: {best['sharpe']:.2f} (baseline: {bl['sharpe']:.2f}, delta: {best['sharpe']-bl['sharpe']:+.2f})")
        print(f"    Return: {best['return_pct']:+.1f}% (baseline: {bl['return_pct']:+.1f}%)")
        print(f"    MaxDD:  {best['max_dd_pct']:.1f}% (baseline: {bl['max_dd_pct']:.1f}%)")
        print(f"    Trades: {best['trades']} (baseline: {bl['trades']})")

    # ---------------------------------------------------------------------------
    # Cross-strategy summary
    # ---------------------------------------------------------------------------
    print("\n\n")
    print("=" * 120)
    print("  CROSS-STRATEGY FEATURE IMPACT SUMMARY")
    print("=" * 120)

    for cfg_name in ["trail_sched", "rsi_exit", "chandelier", "all_three"]:
        sharpe_deltas = []
        for strategy_id in STRATEGIES:
            for months in PERIODS:
                bl = df[(df["strategy"] == strategy_id) & (df["months"] == months) & (df["config"] == "baseline")]
                cfg = df[(df["strategy"] == strategy_id) & (df["months"] == months) & (df["config"] == cfg_name)]
                if not bl.empty and not cfg.empty:
                    sharpe_deltas.append(cfg.iloc[0]["sharpe"] - bl.iloc[0]["sharpe"])
        if sharpe_deltas:
            avg_delta = np.mean(sharpe_deltas)
            pct_positive = sum(1 for d in sharpe_deltas if d > 0) / len(sharpe_deltas) * 100
            print(f"  {cfg_name:>12s}: avg Sharpe delta = {avg_delta:+.3f}, "
                  f"positive in {pct_positive:.0f}% of tests ({len(sharpe_deltas)} tests)")
        else:
            print(f"  {cfg_name:>12s}: no data")

    print("\n" + "=" * 120)
    print("  DONE")
    print("=" * 120)


if __name__ == "__main__":
    main()
