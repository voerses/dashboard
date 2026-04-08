#!/workspace/venv/bin/python
"""Mission D Gate 3 — FIF×drift through tools/raw_backtest harness.

Wires `strategies/s_fif_drift.py` into the raw_backtest harness mandated by
AIPIP-0031, runs it on the full available history, and reports the
L12M / L6M / L3M verdict against the standard PROMOTE thresholds:

  Sharpe > 2.0, Calmar > 3.0, MaxDD > -25%, ann ret > 0%, L12M trades > 30
  on EVERY window.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path("/workspace/crypto_backtest")
sys.path.insert(0, str(REPO))

from tools.raw_backtest import Backtest  # noqa: E402
from strategies.s_fif_drift import (    # noqa: E402
    TOKENS,
    THRESHOLD,
    HOLD_HOURS,
    DIRECTION,
    SAMPLE_STEP_BARS,
    ZSCORE_WINDOW,
    FIF_WINDOW,
    run_strategy,
)

# Match Mission D Gate 1/2 backtest window-end. Use early start so the
# 200-day burn-in still leaves >2y of trading.
START = "2022-01-01"
END = "2026-04-08"
CAPITAL = 50_000        # match Gate 1/2
FEE_BPS = 7             # harness default — STRICTER than Gate 1's 4 bps/side
LEVERAGE_MAX = 1.0

OUT_REPORT = REPO / "research/mission_d_gate3_report.txt"
OUT_RESULTS = REPO / "research/mission_d_gate3_results.json"


def serialize_window(w: dict) -> dict:
    out = {}
    for k, v in w.items():
        if hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        elif isinstance(v, (int, float)):
            out[k] = float(v) if not isinstance(v, bool) else v
        else:
            out[k] = str(v)
    return out


def main() -> None:
    print("=" * 72)
    print(" MISSION D GATE 3 — FIF×drift via raw_backtest harness")
    print("=" * 72)
    print(f" tokens     : {TOKENS}")
    print(f" threshold  : {THRESHOLD} (cross-up)")
    print(f" hold       : {HOLD_HOURS}h time stop")
    print(f" direction  : {DIRECTION}")
    print(f" cadence    : every {SAMPLE_STEP_BARS} bars (FIF window={FIF_WINDOW},"
          f" z window={ZSCORE_WINDOW})")
    print(f" capital    : ${CAPITAL:,.0f}  fee={FEE_BPS}bps  lev={LEVERAGE_MAX}x")
    print(f" period     : {START} → {END}")
    print()

    bt = Backtest(
        capital=CAPITAL,
        fee_bps=FEE_BPS,
        market="perp",
        leverage_max=LEVERAGE_MAX,
        start=START,
        end=END,
        slippage_model="sqrt",
    )

    run_strategy(bt, tokens=TOKENS, threshold=THRESHOLD, hold_hours=HOLD_HOURS)

    print("\n--- Harness report ---\n")
    res = bt.report(name="Mission D Gate 3 — FIF×drift",
                    save_path=str(OUT_REPORT))

    # ── Build JSON summary ──
    windows_json = {wn: serialize_window(w) for wn, w in res["windows"].items()}
    full_json = serialize_window(res["full"]) if res.get("full") else {}

    # Cost breakdown
    trades_df = bt.get_trades_df()
    if not trades_df.empty:
        gross = float(trades_df["gross_pnl"].sum())
        fees = float(trades_df["fees"].sum())
        funding = float(trades_df["funding"].sum())
        slip = float(trades_df["slippage"].sum())
        net = float(trades_df["net_pnl"].sum())
    else:
        gross = fees = funding = slip = net = 0.0

    # ── Verdict logic per Gate 3 spec ──
    promote_checks = {}
    fail_count_per_window = {}
    for wn in ("L12M", "L6M", "L3M"):
        m = res["windows"][wn]
        checks = {
            "sharpe_gt_2": m["sharpe"] > 2.0,
            "calmar_gt_3": m["calmar"] > 3.0,
            "maxdd_gt_neg25": m["maxdd"] > -0.25,
            "annret_gt_0":  m["ann_ret"] > 0,
        }
        if wn == "L12M":
            checks["trades_gt_30"] = m["trades"] > 30
        promote_checks[wn] = checks
        fail_count_per_window[wn] = sum(1 for v in checks.values() if not v)

    n_windows_failed = sum(1 for n in fail_count_per_window.values() if n > 0)
    total_failed_checks = sum(fail_count_per_window.values())

    if n_windows_failed == 0:
        verdict = "PROMOTE"
    elif n_windows_failed >= 2 and total_failed_checks >= 4:
        verdict = "KILL"
    elif n_windows_failed >= 2:
        verdict = "NEEDS_TUNING"
    else:
        verdict = "NEEDS_TUNING"

    summary = {
        "config": {
            "tokens": TOKENS,
            "threshold": THRESHOLD,
            "hold_hours": HOLD_HOURS,
            "direction": DIRECTION,
            "sample_step_bars": SAMPLE_STEP_BARS,
            "fif_window": FIF_WINDOW,
            "zscore_window": ZSCORE_WINDOW,
            "start": START,
            "end": END,
            "capital": CAPITAL,
            "fee_bps": FEE_BPS,
            "leverage_max": LEVERAGE_MAX,
            "slippage_model": "sqrt",
        },
        "harness_verdict": res["verdict"],
        "harness_kill_reasons": res.get("kill_reasons", []),
        "windows": windows_json,
        "full_period": full_json,
        "trades_total": int(res["trades"]),
        "liquidations": int(res["liquidations"]),
        "violations": int(res["violations"]),
        "cost_breakdown_usd": {
            "gross_pnl": gross,
            "fees": fees,
            "funding": funding,
            "slippage": slip,
            "net_pnl": net,
        },
        "gate3_promote_checks": promote_checks,
        "gate3_fail_count_per_window": fail_count_per_window,
        "gate3_verdict": verdict,
    }

    OUT_RESULTS.write_text(json.dumps(summary, indent=2, default=str))
    print(f"\nSaved → {OUT_RESULTS}")
    print(f"Saved → {OUT_REPORT}")
    print(f"\nGATE 3 VERDICT: {verdict}")
    for wn, fc in fail_count_per_window.items():
        print(f"  {wn}: {fc} failed checks {promote_checks[wn]}")


if __name__ == "__main__":
    main()
