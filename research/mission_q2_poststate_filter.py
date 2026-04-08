# ============================================================================
# INVALID / DO NOT TRUST — contaminated result, see session_2026_04_08 memory
# ============================================================================
# This script was part of the cascade-overlay research direction which was
# killed 2026-04-08 after three artifact discoveries:
#   1. Biased state classifier (30min pre-peak active, forward-looking pre_cascade)
#   2. Repricing artifact — close.asof(bar) is systematically 0.2% better for
#      the trader than the engine slippage-adjusted fill. shift=0 drift +$104k.
#   3. Path-dependent stops — tiny entry-price shifts re-roll stop-trigger outcomes
# When all three were controlled for, cascade-timing effect on s523c was ~0.
# Retained as historical artifact / methodology lesson, NOT as validation.
# See memory/STRATEGY_MISSION_BACKLOG.md 2026-04-08 entry for full context.
# ============================================================================
"""Mission Q2 — post-cascade-state filter on s523c trade log.

Mission Q's post-hoc diagnostic found:
    state at entry       n    mean_pnl   win_rate
    recovery_window      46   +$978      54%        (BEST)
    pre_cascade         383   +$842      44%        (normal)
    post_cascade        205   +$29       40%        (DEAD MONEY)

Q2 tests the natural follow-up: drop the 205 post_cascade entries (dead
money), optionally size-up the recovery_window entries. Four variants:

    skip_post             drop post_cascade trades only
    skip_post_size_1.25x  drop post_cascade + 1.25x size recovery_window
    skip_post_size_1.5x   drop post_cascade + 1.5x size recovery_window
    skip_post_size_2.0x   drop post_cascade + 2.0x size recovery_window

Baseline = unchanged s523c trade list.

Reuses Mission Q infrastructure (cascade detection, state classification).
"""
from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path("/workspace/crypto_backtest")
sys.path.insert(0, str(REPO / "research"))

import mission_q_cascade_overlay as mq  # noqa: E402
from mission_d_gate1_overlay import (  # noqa: E402
    compute_baseline_metrics,
)

TRADES_PATH = REPO / "results/v4/s523c_growth_12mo_50k_trades.json"
OUT_JSON = REPO / "research/mission_q2_results.json"


def apply_post_filter(
    trades: list[dict],
    cstates: mq.CascadeStates,
    recovery_size_mult: float = 1.0,
) -> tuple[list[dict], dict]:
    """Drop post_cascade entries. Optionally scale recovery_window PnL by mult.

    Scaling PnL by a multiplier is a first-order approximation of "put more
    capital into these trades". The assumption: notional scales linearly with
    PnL (and margin/fees scale with it too, but those are small relative to
    PnL for s523c so the first-order approx is fine for a screening test).
    """
    stats = {
        "n_in": len(trades),
        "dropped_post_cascade": 0,
        "scaled_recovery_window": 0,
        "kept_pre_cascade": 0,
        "kept_active_cascade": 0,
        "kept_normal": 0,
    }
    out: list[dict] = []
    for tr in trades:
        tr = deepcopy(tr)
        entry_bar = int(tr["entry_bar"])
        entry_ts = mq.ts_from_bar(entry_bar)
        state = cstates.classify(entry_ts)
        if state == "post_cascade":
            stats["dropped_post_cascade"] += 1
            continue
        if state == "recovery_window" and recovery_size_mult != 1.0:
            # Scale PnL (first-order approximation for larger position size).
            tr["pnl"] = float(tr["pnl"]) * recovery_size_mult
            stats["scaled_recovery_window"] += 1
        if state == "pre_cascade":
            stats["kept_pre_cascade"] += 1
        elif state == "active_cascade":
            stats["kept_active_cascade"] += 1
        elif state == "normal":
            stats["kept_normal"] += 1
        out.append(tr)
    stats["n_out"] = len(out)
    return out, stats


def delta_pct(new: dict, base: dict, key: str) -> float:
    b = float(base[key])
    if abs(b) < 1e-12:
        return 0.0
    return (float(new[key]) - b) / abs(b) * 100.0


def main() -> None:
    print("[1/5] Loading BTC microstructure CPI parquet...")
    btc = mq.load_btc_mincpi()
    print(f"      rows={len(btc):,}")

    print("[2/5] Detecting cascade events...")
    events = mq.detect_cascades_q(btc)
    events = mq.compute_recovery_ts(btc, events)
    print(f"      {len(events)} cascade events")
    cstates = mq.CascadeStates(events)

    print("[3/5] Loading s523c trade log...")
    with open(TRADES_PATH) as f:
        trades = json.load(f)
    print(f"      {len(trades)} trades")

    print("[4/5] Baseline metrics...")
    baseline = compute_baseline_metrics(trades)
    print(f"      Return={baseline['total_return_pct']:+.2f}%  "
          f"MaxDD={baseline['max_drawdown_pct']:+.2f}%  "
          f"Sharpe={baseline['sharpe']:+.2f}  "
          f"Calmar={baseline['calmar']:+.2f}")

    print("[5/5] Running post-filter variants...")
    variants = [
        ("skip_post",            1.00),
        ("skip_post_size_1.25x", 1.25),
        ("skip_post_size_1.5x",  1.50),
        ("skip_post_size_2.0x",  2.00),
    ]

    results = {
        "config": {
            "cpi_trigger": mq.CPI_TRIGGER_Q,
            "merge_gap_min": mq.MERGE_GAP_MIN_Q,
            "recovery_window_h": mq.RECOVERY_WINDOW_H,
        },
        "baseline": baseline,
        "variants": {},
    }

    rows = []
    for label, mult in variants:
        modified, stats = apply_post_filter(trades, cstates, recovery_size_mult=mult)
        m = compute_baseline_metrics(modified)
        row = {
            "label": label,
            "recovery_size_mult": mult,
            "stats": stats,
            "metrics": m,
            "delta_return_pct":    delta_pct(m, baseline, "total_return_pct"),
            "delta_maxdd_pct":     delta_pct(m, baseline, "max_drawdown_pct"),
            "delta_sharpe_pct":    delta_pct(m, baseline, "sharpe"),
            "delta_calmar_pct":    delta_pct(m, baseline, "calmar"),
        }
        results["variants"][label] = row
        rows.append(row)

    # --- comparison table ---
    print("\n" + "=" * 115)
    print("MISSION Q2 — drop post_cascade entries + optionally size-up recovery_window")
    print("=" * 115)
    print(f"{'Variant':<24} {'Trades':>7} {'Return':>10} {'MaxDD':>9} {'Sharpe':>8} "
          f"{'Calmar':>8} {'ΔRet':>9} {'ΔMaxDD':>10} {'ΔSharpe':>10} {'ΔCalmar':>10}")
    print(f"{'baseline':<24} {len(trades):>7} "
          f"{baseline['total_return_pct']:>+9.2f}% "
          f"{baseline['max_drawdown_pct']:>+8.2f}% "
          f"{baseline['sharpe']:>+8.2f} "
          f"{baseline['calmar']:>+8.2f} "
          f"{'—':>9} {'—':>10} {'—':>10} {'—':>10}")
    for r in rows:
        m = r["metrics"]
        print(f"{r['label']:<24} {r['stats']['n_out']:>7} "
              f"{m['total_return_pct']:>+9.2f}% "
              f"{m['max_drawdown_pct']:>+8.2f}% "
              f"{m['sharpe']:>+8.2f} "
              f"{m['calmar']:>+8.2f} "
              f"{r['delta_return_pct']:>+8.1f}% "
              f"{r['delta_maxdd_pct']:>+9.1f}% "
              f"{r['delta_sharpe_pct']:>+9.1f}% "
              f"{r['delta_calmar_pct']:>+9.1f}%")

    print("\nEntry handling detail:")
    for r in rows:
        s = r["stats"]
        print(f"  {r['label']}: dropped={s['dropped_post_cascade']} "
              f"scaled={s['scaled_recovery_window']} kept_pre={s['kept_pre_cascade']} "
              f"kept_normal={s['kept_normal']}")

    # --- verdict ---
    def passes(r):
        # PROMOTE if:
        #  - Calmar +10% without hurting Sharpe by more than 5%, OR
        #  - MaxDD shrunk by 20% relative without hurting return by more than 10%, OR
        #  - Sharpe +15% stretch
        calm_ok = r["delta_calmar_pct"] >= 10.0 and r["delta_sharpe_pct"] >= -5.0
        dd_ok = r["delta_maxdd_pct"] >= 20.0 and r["delta_return_pct"] >= -10.0
        sharpe_ok = r["delta_sharpe_pct"] >= 15.0
        return calm_ok, dd_ok, sharpe_ok

    any_promote = False
    print("\nPass criteria evaluation:")
    for r in rows:
        calm_ok, dd_ok, sharpe_ok = passes(r)
        reasons = []
        if calm_ok:
            reasons.append("CALMAR")
        if dd_ok:
            reasons.append("MAXDD")
        if sharpe_ok:
            reasons.append("SHARPE")
        status = ("PASS: " + ",".join(reasons)) if reasons else "fail"
        print(f"  {r['label']:<24} → {status}")
        if reasons:
            any_promote = True

    if any_promote:
        winner = max(rows, key=lambda r: r["delta_calmar_pct"])
        verdict = "PROMOTE"
        winner_label = winner["label"]
    else:
        # Any improvement at all?
        any_improved = any(
            r["delta_calmar_pct"] > 0 or r["delta_sharpe_pct"] > 0
            or r["delta_maxdd_pct"] > 0 or r["delta_return_pct"] > 0
            for r in rows
        )
        if any_improved:
            verdict = "NEEDS_TUNING"
            winner = max(rows, key=lambda r: r["delta_calmar_pct"])
            winner_label = winner["label"]
        else:
            verdict = "KILL"
            winner_label = None

    results["verdict"] = verdict
    results["winner"] = winner_label
    print(f"\n>>> VERDICT: {verdict}")
    if winner_label:
        print(f"    Winner: {winner_label}")

    OUT_JSON.write_text(json.dumps(results, indent=2, default=str))
    print(f"\nSaved → {OUT_JSON}")


if __name__ == "__main__":
    main()
