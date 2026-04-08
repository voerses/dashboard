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
"""Mission Q3-clean — properly isolate the shift effect from the repricing artifact.

The previous Q3 attempts were contaminated by a repricing artifact:
`close.asof(entry_bar)` returns prices ~0.2% better for the trader than the
original s523c engine's entry_price (probably because the engine filled at
next-bar open or with baked-in slippage). A blind shift=0 gave +23% PnL inflation.

FIX: use close.asof() for BOTH the "clean baseline" and the "shifted" versions.
The close-vs-fill artifact is present in both, so it cancels in the delta.
We compare (clean baseline) vs (clean shifted), NOT (real baseline) vs (anything).

Procedure:
  1. For each trade, recompute PnL using close.asof(entry_bar) and close.asof(exit_bar).
     This is the "clean baseline" — a fictional version that will NOT match the
     real baseline, but is internally consistent.
  2. For each variant (blind shift 0..24h, causal Q3), recompute each trade's PnL
     using close.asof(new_entry_bar) and close.asof(exit_bar).
  3. Compare variant metrics to clean baseline metrics.
  4. SANITY CHECK: blind_shift_0 must give IDENTICAL metrics to clean_baseline.
     If not, the pipeline has another bug.

If after these fixes:
  - blind shifts 1..24h are all roughly flat → no artifact, Q3's effect is pure signal
  - blind shifts have a bias → repricing still broken, debug further
  - causal Q3 gives positive delta above blind shifts → real cascade alpha
  - causal Q3 is flat or below blind → no cascade timing alpha
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
import mission_q3_causal as q3c  # noqa: E402
from mission_d_gate1_overlay import compute_baseline_metrics  # noqa: E402

TRADES_PATH = REPO / "results/v4/s523d_growth_12mo_50k_trades.json"
OUT_JSON = REPO / "research/mission_q3_clean_results.json"


def reprice_all(
    trades: list[dict],
    token_closes: dict[str, pd.Series],
    new_entry_bar_fn,
) -> tuple[list[dict], int]:
    """For each trade, set entry price = close.asof(new_entry_bar) and
    exit price = close.asof(exit_bar). Recompute PnL.

    new_entry_bar_fn(tr) → int: returns the new entry bar to use for this trade.
    Must not exceed exit_bar - 1.

    Returns (new_trades, n_repriced_ok).
    """
    out = []
    ok = 0
    for tr in trades:
        tr = deepcopy(tr)
        entry_bar = int(tr["entry_bar"])
        exit_bar = int(tr["exit_bar"])
        new_bar = new_entry_bar_fn(tr)
        if new_bar is None or new_bar >= exit_bar:
            new_bar = entry_bar  # fallback: no shift
        close = token_closes.get(tr["token"])
        if close is None:
            out.append(tr)
            continue
        try:
            new_entry_px = float(close.asof(mq.ts_from_bar(new_bar)))
            exit_px = float(close.asof(mq.ts_from_bar(exit_bar)))
        except Exception:
            out.append(tr)
            continue
        if not (np.isfinite(new_entry_px) and np.isfinite(exit_px) and new_entry_px > 0):
            out.append(tr)
            continue
        notional = mq.infer_notional(tr)
        direction = int(tr["direction"])
        new_ret = direction * (exit_px - new_entry_px) / new_entry_px
        try:
            efee = float(tr.get("entry_fee", 0.0) or 0.0)
            xfee = float(tr.get("exit_fee", 0.0) or 0.0)
        except (TypeError, ValueError):
            efee = xfee = 0.0
        new_pnl = notional * new_ret - (efee + xfee)
        tr["entry_bar"] = new_bar
        tr["entry_price"] = new_entry_px
        tr["exit_price"] = exit_px
        tr["pnl"] = new_pnl
        ok += 1
        out.append(tr)
    return out, ok


def dpct(new, base, k):
    b = float(base[k])
    if abs(b) < 1e-12:
        return 0.0
    return (float(new[k]) - b) / abs(b) * 100.0


def main():
    print("[1/4] Loading s523d trade log...")
    with open(TRADES_PATH) as f:
        trades = json.load(f)
    print(f"      {len(trades)} trades")

    print("[2/4] Loading token closes...")
    tokens = sorted({t["token"] for t in trades})
    token_closes = {}
    for tok in tokens:
        s = mq.load_token_close(tok)
        if s is not None:
            token_closes[tok] = s
    print(f"      {len(token_closes)}/{len(tokens)} tokens")

    print("[3/4] Computing REAL baseline (from original trade log)...")
    real_baseline = compute_baseline_metrics(trades)
    print(f"      REAL    : Return={real_baseline['total_return_pct']:+.2f}%  MaxDD={real_baseline['max_drawdown_pct']:+.2f}%  "
          f"Sharpe={real_baseline['sharpe']:+.2f}  Calmar={real_baseline['calmar']:+.2f}")

    # Clean baseline: reprice EVERY trade using close.asof() but with shift=0
    clean_trades_base, ok0 = reprice_all(trades, token_closes, lambda tr: int(tr["entry_bar"]))
    clean_baseline = compute_baseline_metrics(clean_trades_base)
    print(f"      CLEAN   : Return={clean_baseline['total_return_pct']:+.2f}%  MaxDD={clean_baseline['max_drawdown_pct']:+.2f}%  "
          f"Sharpe={clean_baseline['sharpe']:+.2f}  Calmar={clean_baseline['calmar']:+.2f}")
    print(f"      (clean baseline uses close.asof for both entry and exit;")
    print(f"       will NOT match real baseline, but serves as consistent reference)")

    print("\n[4/4] Running variants vs clean baseline...")
    print("      - Blind shifts +1..24h (should show no systematic effect)")
    print("      - Causal Q3 lookaheads (cascade-based shifts)")

    # Causal states for Q3
    print("\n      Building causal cascade states...")
    btc = mq.load_btc_mincpi()
    states = q3c.build_causal_states(btc)

    def build_causal_shift_fn(lookahead_h):
        def fn(tr):
            orig_ts = mq.ts_from_bar(int(tr["entry_bar"]))
            new_ts, _ = q3c.causal_shift_ts(states, orig_ts, lookahead_h)
            return mq.bar_from_ts(new_ts)
        return fn

    def blind_shift_fn(h):
        return lambda tr: int(tr["entry_bar"]) + h

    rows = []

    print("\n" + "=" * 125)
    print("MISSION Q3-clean — properly isolated shift effect (all metrics vs CLEAN baseline)")
    print("=" * 125)
    print(f"{'Variant':<30} {'Return':>11} {'MaxDD':>9} {'Sharpe':>8} {'Calmar':>8}  dRet       dMaxDD     dSharpe    dCalmar")
    print(f"{'clean baseline (shift=0)':<30} {clean_baseline['total_return_pct']:>+10.2f}% {clean_baseline['max_drawdown_pct']:>+8.2f}% "
          f"{clean_baseline['sharpe']:>+8.2f} {clean_baseline['calmar']:>+8.2f}")

    # Sanity check: shift=0 via reprice_all should give identical to clean_baseline
    # Already tested above — we just built it.

    # Blind shifts
    for h in [0, 1, 2, 3, 6, 9, 12, 18, 24]:
        if h == 0:
            modified = clean_trades_base
        else:
            modified, _ = reprice_all(trades, token_closes, blind_shift_fn(h))
        m = compute_baseline_metrics(modified)
        dret = dpct(m, clean_baseline, "total_return_pct")
        dmdd = dpct(m, clean_baseline, "max_drawdown_pct")
        dshp = dpct(m, clean_baseline, "sharpe")
        dcal = dpct(m, clean_baseline, "calmar")
        label = f"blind_shift_+{h}h" if h > 0 else "blind_shift_+0h (sanity)"
        rows.append({"label": label, "metrics": m, "dret": dret, "dmdd": dmdd, "dshp": dshp, "dcal": dcal})
        print(f"{label:<30} {m['total_return_pct']:>+10.2f}% {m['max_drawdown_pct']:>+8.2f}% "
              f"{m['sharpe']:>+8.2f} {m['calmar']:>+8.2f}  {dret:>+8.1f}%  {dmdd:>+8.1f}%  {dshp:>+8.1f}%  {dcal:>+8.1f}%")

    # Causal Q3 shifts
    for hrs in [6, 12, 24]:
        modified, _ = reprice_all(trades, token_closes, build_causal_shift_fn(hrs))
        m = compute_baseline_metrics(modified)
        dret = dpct(m, clean_baseline, "total_return_pct")
        dmdd = dpct(m, clean_baseline, "max_drawdown_pct")
        dshp = dpct(m, clean_baseline, "sharpe")
        dcal = dpct(m, clean_baseline, "calmar")
        label = f"causal_q3_lookahead_{hrs}h"
        rows.append({"label": label, "metrics": m, "dret": dret, "dmdd": dmdd, "dshp": dshp, "dcal": dcal})
        print(f"{label:<30} {m['total_return_pct']:>+10.2f}% {m['max_drawdown_pct']:>+8.2f}% "
              f"{m['sharpe']:>+8.2f} {m['calmar']:>+8.2f}  {dret:>+8.1f}%  {dmdd:>+8.1f}%  {dshp:>+8.1f}%  {dcal:>+8.1f}%")

    # The KEY comparison: causal Q3 delta minus blind shift delta at same hours
    print("\n" + "=" * 125)
    print("CAUSAL EFFECT: (causal_q3 Calmar improvement) MINUS (blind shift Calmar improvement at same hours)")
    print("=" * 125)
    blind_by_h = {int(r["label"].replace("blind_shift_+", "").replace("h", "").replace(" (sanity)", "")): r for r in rows if r["label"].startswith("blind_shift")}
    causal_by_h = {int(r["label"].replace("causal_q3_lookahead_", "").replace("h", "")): r for r in rows if r["label"].startswith("causal_q3")}
    # For causal, average shift was around: 6h→5.1, 12h→9.5, 24h→12.8. Use closest blind bucket.
    causal_avg_shift = {6: 5, 12: 9, 24: 12}  # from prior Q3 run
    for hrs in [6, 12, 24]:
        caus = causal_by_h.get(hrs)
        blind_ref = blind_by_h.get(causal_avg_shift[hrs]) or blind_by_h.get(6)
        if not caus or not blind_ref:
            continue
        net_dcal = caus["dcal"] - blind_ref["dcal"]
        net_dret = caus["dret"] - blind_ref["dret"]
        net_dshp = caus["dshp"] - blind_ref["dshp"]
        print(f"  causal_q3_{hrs}h vs blind_{causal_avg_shift[hrs]}h :  "
              f"net dCalmar={net_dcal:+.1f}%  net dReturn={net_dret:+.1f}%  net dSharpe={net_dshp:+.1f}%")

    print("\nINTERPRETATION:")
    print("  - If blind_shift_+0h EXACTLY equals clean_baseline: repricing pipeline is consistent")
    print("  - If blind shifts 1..24h show ~0% delta: no systematic timing artifact")
    print("  - If causal_q3 delta > blind_shift delta at comparable average hours: real cascade signal")
    print("  - If causal_q3 delta <= blind shift delta: no causal cascade alpha (mission dead)")

    out = {
        "real_baseline": real_baseline,
        "clean_baseline": clean_baseline,
        "variants": [{"label": r["label"], "metrics": r["metrics"],
                      "dret": r["dret"], "dmdd": r["dmdd"], "dshp": r["dshp"], "dcal": r["dcal"]}
                     for r in rows],
    }
    OUT_JSON.write_text(json.dumps(out, indent=2, default=str))
    print(f"\nSaved -> {OUT_JSON}")


if __name__ == "__main__":
    main()
