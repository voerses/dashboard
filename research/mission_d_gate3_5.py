#!/workspace/venv/bin/python
"""Mission D Gate 3.5 — exploratory tuning of Gate 3 FIF×drift.

Two structural changes vs Gate 3:
  1. z-threshold: 2.0 → 1.5
  2. universe   : BTC/ETH/SOL → BTC/ETH/SOL/BNB/XRP/DOGE/ADA/AVAX/LINK

Everything else is unchanged (72h time stop, daily cadence, 200-sample z,
7 bps/side fees, sqrt slippage, $50k, 1× leverage, 2022-01-01 → 2026-04-08).

Output:
  research/mission_d_gate3_5_results.json  (machine-readable)
  research/mission_d_gate3_5_report.txt    (harness window report)
  prints side-by-side comparison vs Gate 3
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

REPO = Path("/workspace/crypto_backtest")
sys.path.insert(0, str(REPO))

from tools.raw_backtest import Backtest  # noqa: E402
from strategies.s_fif_drift import (     # noqa: E402
    HOLD_HOURS,
    DIRECTION,
    SAMPLE_STEP_BARS,
    ZSCORE_WINDOW,
    FIF_WINDOW,
    run_strategy,
)

# ── Gate 3.5 overrides ──
GATE35_TOKENS_CANDIDATE = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE",
                           "ADA", "AVAX", "LINK"]
GATE35_THRESHOLD = 1.5

START = "2022-01-01"
END = "2026-04-08"
CAPITAL = 50_000
FEE_BPS = 7
LEVERAGE_MAX = 1.0

OUT_REPORT = REPO / "research/mission_d_gate3_5_report.txt"
OUT_RESULTS = REPO / "research/mission_d_gate3_5_results.json"
GATE3_RESULTS = REPO / "research/mission_d_gate3_results.json"


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


def verify_universe(bt: Backtest, candidate: list) -> list:
    """Keep only tokens with both OHLCV parquet and funding_1h column."""
    kept = []
    for tok in candidate:
        try:
            df = bt.data.load(tok)
        except FileNotFoundError:
            print(f"  SKIP {tok}: no parquet")
            continue
        if "funding_1h" not in df.columns:
            print(f"  SKIP {tok}: no funding_1h")
            continue
        kept.append(tok)
    return kept


def per_token_pnl(trades_df: pd.DataFrame) -> dict:
    if trades_df.empty:
        return {}
    grp = trades_df.groupby("token")
    out = {}
    for tok, g in grp:
        out[tok] = {
            "n_trades": int(len(g)),
            "net_pnl_usd": float(g["net_pnl"].sum()),
            "win_rate": float((g["net_pnl"] > 0).mean()),
            "avg_pnl": float(g["net_pnl"].mean()),
        }
    return out


def main() -> None:
    print("=" * 72)
    print(" MISSION D GATE 3.5 — FIF×drift (thr=1.5, 9-token universe)")
    print("=" * 72)

    bt = Backtest(
        capital=CAPITAL,
        fee_bps=FEE_BPS,
        market="perp",
        leverage_max=LEVERAGE_MAX,
        start=START,
        end=END,
        slippage_model="sqrt",
    )

    print(" Verifying universe...")
    tokens = verify_universe(bt, GATE35_TOKENS_CANDIDATE)
    print(f" Kept universe: {tokens} (n={len(tokens)})")
    print(f" threshold  : {GATE35_THRESHOLD}")
    print(f" hold       : {HOLD_HOURS}h time stop")
    print(f" cadence    : every {SAMPLE_STEP_BARS} bars (FIF={FIF_WINDOW}, z={ZSCORE_WINDOW})")
    print(f" capital    : ${CAPITAL:,.0f}  fee={FEE_BPS}bps  lev={LEVERAGE_MAX}x")
    print(f" period     : {START} → {END}")
    print()

    run_strategy(bt, tokens=tokens, threshold=GATE35_THRESHOLD, hold_hours=HOLD_HOURS)

    print("\n--- Harness report ---\n")
    res = bt.report(name="Mission D Gate 3.5 — FIF×drift thr=1.5 9tok",
                    save_path=str(OUT_REPORT))

    windows_json = {wn: serialize_window(w) for wn, w in res["windows"].items()}
    full_json = serialize_window(res["full"]) if res.get("full") else {}

    trades_df = bt.get_trades_df()
    if not trades_df.empty:
        gross = float(trades_df["gross_pnl"].sum())
        fees = float(trades_df["fees"].sum())
        funding = float(trades_df["funding"].sum())
        slip = float(trades_df["slippage"].sum())
        net = float(trades_df["net_pnl"].sum())
    else:
        gross = fees = funding = slip = net = 0.0

    tok_pnl = per_token_pnl(trades_df)

    # ── Gate 3.5 promote checks ──
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

    # ── Load Gate 3 for regression check ──
    gate3 = json.loads(GATE3_RESULTS.read_text())
    g3_full_sharpe = gate3["full_period"]["sharpe"]
    g3_full_trades = gate3["trades_total"]
    g3_l12m_sharpe = gate3["windows"]["L12M"]["sharpe"]
    g3_l12m_trades = gate3["windows"]["L12M"]["trades"]

    g35_full_sharpe = res["full"]["sharpe"] if res.get("full") else 0.0
    g35_full_trades = int(res["trades"])
    g35_l12m_sharpe = res["windows"]["L12M"]["sharpe"]
    g35_l12m_trades = res["windows"]["L12M"]["trades"]

    regressed = (
        g35_full_sharpe < g3_full_sharpe - 0.05
        or g35_full_trades < g3_full_trades
    )

    if n_windows_failed == 0:
        verdict = "PROMOTE"
    elif regressed:
        verdict = "KILL"
    else:
        verdict = "NEEDS_TUNING"

    summary = {
        "config": {
            "tokens_candidate": GATE35_TOKENS_CANDIDATE,
            "tokens_used": tokens,
            "threshold": GATE35_THRESHOLD,
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
        "per_token_pnl": tok_pnl,
        "gate35_promote_checks": promote_checks,
        "gate35_fail_count_per_window": fail_count_per_window,
        "gate35_verdict": verdict,
        "comparison_vs_gate3": {
            "full_sharpe": {"gate3": g3_full_sharpe, "gate35": g35_full_sharpe},
            "full_trades": {"gate3": g3_full_trades, "gate35": g35_full_trades},
            "l12m_sharpe": {"gate3": g3_l12m_sharpe, "gate35": g35_l12m_sharpe},
            "l12m_trades": {"gate3": g3_l12m_trades, "gate35": g35_l12m_trades},
            "regressed": regressed,
        },
    }

    OUT_RESULTS.write_text(json.dumps(summary, indent=2, default=str))
    print(f"\nSaved → {OUT_RESULTS}")
    print(f"Saved → {OUT_REPORT}")

    # ── Side-by-side comparison table ──
    g3w = gate3["windows"]
    g35w = res["windows"]
    rows = [
        ("L12M Sharpe",  g3w["L12M"]["sharpe"],   g35w["L12M"]["sharpe"]),
        ("L12M Calmar",  g3w["L12M"]["calmar"],   g35w["L12M"]["calmar"]),
        ("L12M Trades",  g3w["L12M"]["trades"],   g35w["L12M"]["trades"]),
        ("L12M MaxDD %", g3w["L12M"]["maxdd"]*100, g35w["L12M"]["maxdd"]*100),
        ("L12M AnnRet%", g3w["L12M"]["ann_ret"]*100, g35w["L12M"]["ann_ret"]*100),
        ("L6M  Sharpe",  g3w["L6M"]["sharpe"],    g35w["L6M"]["sharpe"]),
        ("L6M  Trades",  g3w["L6M"]["trades"],    g35w["L6M"]["trades"]),
        ("L3M  Sharpe",  g3w["L3M"]["sharpe"],    g35w["L3M"]["sharpe"]),
        ("L3M  Trades",  g3w["L3M"]["trades"],    g35w["L3M"]["trades"]),
        ("4yr Sharpe",   gate3["full_period"]["sharpe"],  res["full"]["sharpe"]),
        ("4yr AnnRet%",  gate3["full_period"]["ann_ret"]*100, res["full"]["ann_ret"]*100),
        ("4yr MaxDD %",  gate3["full_period"]["maxdd"]*100,  res["full"]["maxdd"]*100),
        ("4yr Trades",   gate3["trades_total"],   int(res["trades"])),
    ]
    print("\n" + "=" * 72)
    print(" GATE 3 vs GATE 3.5 COMPARISON")
    print("=" * 72)
    print(f" {'Metric':<14} | {'Gate 3 (2.0/3tok)':>20} | {'Gate 3.5 (1.5/9tok)':>22}")
    print(" " + "-" * 64)
    for name, g3v, g35v in rows:
        print(f" {name:<14} | {g3v:>20.2f} | {g35v:>22.2f}")

    print("\n" + "=" * 72)
    print(f" GATE 3.5 VERDICT: {verdict}")
    print("=" * 72)
    for wn, fc in fail_count_per_window.items():
        print(f"  {wn}: {fc} failed promote checks  {promote_checks[wn]}")

    # Per-token PnL contribution
    if tok_pnl:
        print("\n Per-token full-period contribution (sorted by net PnL):")
        sorted_tok = sorted(tok_pnl.items(), key=lambda kv: -kv[1]["net_pnl_usd"])
        for tok, stats in sorted_tok:
            print(f"  {tok:<5} trades={stats['n_trades']:>4}  "
                  f"net=${stats['net_pnl_usd']:>+9,.0f}  "
                  f"win={stats['win_rate']:.2f}  avg=${stats['avg_pnl']:+.0f}")


if __name__ == "__main__":
    main()
