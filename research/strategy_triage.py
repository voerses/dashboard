#!/usr/bin/env python3
"""Quick strategy triage — BTC-only OOS backtest for Tier A strategies.

Runs each strategy on BTC for the last ~18 months, then extracts
last-6-month performance (2025-09-01 to latest).

Usage:
    /workspace/venv/bin/python research/strategy_triage.py
"""
import sys
import os
import gc
import time
import json
import traceback
from pathlib import Path

# Ensure project root + v4 importable
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "v4"))

import numpy as np
import pandas as pd
from v4.config import PortfolioConfig, StrategySpec
from v4.signals import precompute_strategy_signals, infer_data_end_date
from v4.simulator import simulate_portfolio
from v4.report import compute_portfolio_metrics


# Tier A strategies with their market types
STRATEGIES = [
    ("s29", "perp"),       # Funding carry
    ("s32", "combined"),   # Regime spot-perp
    ("s37", "perp"),       # Momentum trail progression (wraps s11 — spot-like but uses perp for shorts)
    ("s44", "combined"),   # Basis carry trail progression
    ("s56", "perp"),       # Max leverage momentum
    ("s65", "perp"),       # Funding carry v4
    ("s72", "perp"),       # s65 + time trail
]

# s58 is a multi-strategy portfolio runner, not a standard strategy — skip it.

OOS_START = pd.Timestamp("2025-09-01")
CAPITAL = 200_000
MONTHS = 24  # enough history for walk-forward warmup

# Top liquid tokens for the triage
TOKENS = ["BTC", "ETH", "SOL", "XRP", "DOGE", "BNB", "SUI", "ADA", "PEPE", "LINK"]


def run_single_strategy(strategy_id, market, months=MONTHS, capital=CAPITAL):
    """Run a single strategy on top liquid tokens and return results dict."""
    spec = StrategySpec(
        strategy_id=strategy_id,
        market=market,
        max_positions=15,
    )
    config = PortfolioConfig(
        capital=capital,
        max_portfolio_positions=30,
        seed=42,
    )

    # Determine data end date from the appropriate market
    data_end = infer_data_end_date(market if market != "combined" else "perp")

    tokens = TOKENS

    t0 = time.time()
    signals = precompute_strategy_signals(spec, tokens, config, months, end_date=data_end)

    if not signals:
        return {"error": "No signals for any token", "elapsed": time.time() - t0}

    n_entries = sum(sig.entry_mask.sum() for sig in signals.values())
    n_tokens_with_data = len(signals)

    # Run simulation — all_signals is {strategy_id: {token: TokenSignals}}
    all_signals = {strategy_id: signals}
    strategy_specs = {strategy_id: spec}
    state = simulate_portfolio(
        all_signals=all_signals,
        strategy_specs=strategy_specs,
        config=config,
    )
    elapsed = time.time() - t0

    # Extract all closed trades
    all_trades = state.position_manager.closed_trades
    total_trades = len(all_trades)

    # Full period metrics
    final_equity = state.portfolio_equity
    total_return_pct = (final_equity / capital - 1) * 100

    # Build token -> timestamps lookup from signals
    token_timestamps = {}
    for token, sig_item in signals.items():
        token_timestamps[token] = sig_item.timestamps

    # Separate OOS trades (entry after OOS_START)
    oos_trades = []
    all_period_trades = []
    for ct in all_trades:
        # Convert entry_bar to timestamp using per-token timestamps
        token_ts = token_timestamps.get(ct.token)
        entry_ts = None
        if token_ts is not None and ct.entry_bar < len(token_ts):
            entry_ts = token_ts[ct.entry_bar]
        trade_info = {
            "pnl": ct.pnl,
            "hold_bars": ct.hold_bars,
            "exit_reason": ct.exit_reason,
            "entry_bar": ct.entry_bar,
            "exit_bar": ct.exit_bar,
            "entry_ts": entry_ts,
            "margin_usd": ct.margin_usd,
            "token": ct.token,
        }
        all_period_trades.append(trade_info)
        if entry_ts is not None and pd.Timestamp(entry_ts) >= OOS_START:
            oos_trades.append(trade_info)

    # OOS metrics
    oos_pnl = sum(t["pnl"] for t in oos_trades) if oos_trades else 0
    oos_return_pct = (oos_pnl / capital) * 100
    oos_n_trades = len(oos_trades)

    if oos_trades:
        oos_wins = sum(1 for t in oos_trades if t["pnl"] > 0)
        oos_win_rate = (oos_wins / oos_n_trades) * 100
        oos_avg_pnl = oos_pnl / oos_n_trades
        winning_pnl = [t["pnl"] for t in oos_trades if t["pnl"] > 0]
        losing_pnl = [t["pnl"] for t in oos_trades if t["pnl"] < 0]
        avg_win = np.mean(winning_pnl) if winning_pnl else 0
        avg_loss = np.mean(losing_pnl) if losing_pnl else 0
        profit_factor = abs(sum(winning_pnl) / sum(losing_pnl)) if losing_pnl else float('inf')

        # Compute OOS max drawdown from trade PnLs
        cumulative = np.cumsum([t["pnl"] for t in oos_trades])
        running_max = np.maximum.accumulate(cumulative)
        drawdowns = cumulative - running_max
        oos_max_dd = float(np.min(drawdowns))
        oos_max_dd_pct = (oos_max_dd / capital) * 100
    else:
        oos_win_rate = 0
        oos_avg_pnl = 0
        avg_win = 0
        avg_loss = 0
        profit_factor = 0
        oos_max_dd = 0
        oos_max_dd_pct = 0

    # Per-token OOS breakdown
    per_token_oos = {}
    for t in oos_trades:
        tk = t.get("token", "UNK")
        if tk not in per_token_oos:
            per_token_oos[tk] = {"n_trades": 0, "pnl": 0}
        per_token_oos[tk]["n_trades"] += 1
        per_token_oos[tk]["pnl"] += t["pnl"]

    # Verdict
    if oos_n_trades == 0:
        verdict = "DEAD"
    elif oos_return_pct > 2.0:
        verdict = "ALIVE"
    elif oos_return_pct > 0:
        verdict = "MARGINAL"
    else:
        verdict = "DEAD"

    return {
        "strategy": strategy_id,
        "market": market,
        "elapsed": elapsed,
        "total_entries": int(n_entries),
        "n_tokens": n_tokens_with_data,
        "full_period": {
            "total_trades": total_trades,
            "total_return_pct": total_return_pct,
            "final_equity": final_equity,
        },
        "oos": {
            "n_trades": oos_n_trades,
            "total_pnl": oos_pnl,
            "return_pct": oos_return_pct,
            "win_rate": oos_win_rate,
            "avg_pnl": oos_avg_pnl,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "profit_factor": profit_factor,
            "max_dd": oos_max_dd,
            "max_dd_pct": oos_max_dd_pct,
            "per_token": per_token_oos,
        },
        "verdict": verdict,
    }


def main():
    print("=" * 80)
    print("STRATEGY TRIAGE — BTC OOS (last 6 months: 2025-09-01 to latest)")
    print("=" * 80)
    print()

    results = {}
    for strategy_id, market in STRATEGIES:
        print(f"\n--- Running {strategy_id} ({market}) ---")
        try:
            r = run_single_strategy(strategy_id, market)
            results[strategy_id] = r
            if "error" in r:
                print(f"  ERROR: {r['error']}")
                continue

            oos = r["oos"]
            fp = r["full_period"]
            print(f"  Time: {r['elapsed']:.1f}s | Tokens: {r.get('n_tokens', 0)} | Entries: {r['total_entries']}")
            print(f"  Full period: {fp['total_trades']} trades, Return={fp['total_return_pct']:+.2f}%")
            print(f"  OOS (6mo):   {oos['n_trades']} trades, Return={oos['return_pct']:+.2f}%")
            print(f"               WinRate={oos['win_rate']:.1f}%, PF={oos['profit_factor']:.2f}")
            print(f"               MaxDD={oos['max_dd_pct']:.2f}%")
            if oos.get("per_token"):
                for tk, tp in sorted(oos["per_token"].items(), key=lambda x: -x[1]["pnl"]):
                    if tp["n_trades"] > 0:
                        print(f"    {tk:>6s}: {tp['n_trades']:>3d} trades, PnL=${tp['pnl']:>+10,.0f}")
            print(f"  VERDICT:     {r['verdict']}")
        except Exception as e:
            print(f"  EXCEPTION: {e}")
            traceback.print_exc()
            results[strategy_id] = {"error": str(e), "verdict": "ERROR"}
        gc.collect()

    # Summary table
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"{'Strategy':>8s}  {'Market':>8s}  {'OOS Trades':>10s}  {'OOS Return':>11s}  "
          f"{'Win Rate':>8s}  {'PF':>6s}  {'MaxDD':>8s}  {'Verdict':>10s}")
    print("-" * 85)

    for strategy_id, _ in STRATEGIES:
        r = results.get(strategy_id, {})
        if "error" in r and "oos" not in r:
            print(f"{strategy_id:>8s}  {'---':>8s}  {'---':>10s}  {'---':>11s}  "
                  f"{'---':>8s}  {'---':>6s}  {'---':>8s}  {'ERROR':>10s}")
            continue
        oos = r.get("oos", {})
        market = r.get("market", "?")
        print(f"{strategy_id:>8s}  {market:>8s}  {oos.get('n_trades', 0):>10d}  "
              f"{oos.get('return_pct', 0):>+10.2f}%  "
              f"{oos.get('win_rate', 0):>7.1f}%  "
              f"{oos.get('profit_factor', 0):>6.2f}  "
              f"{oos.get('max_dd_pct', 0):>7.2f}%  "
              f"{r.get('verdict', '?'):>10s}")

    # Save JSON results
    json_path = os.path.join(PROJECT_ROOT, "research", "strategy_triage_results.json")
    with open(json_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nJSON results saved to {json_path}")


if __name__ == "__main__":
    main()
