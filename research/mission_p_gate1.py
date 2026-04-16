"""
Mission P Gate 1 — 24-month validation of breadth direction cull.

Constraint from user: don't put much weight beyond 1 year, max 24 months.

Tests:
1. Apply best 12mo variant (g3d_br80_th14d) to full 24mo backtest
2. Split 24mo into two non-overlapping 12mo halves: H1 (older) and H2 (recent)
3. Walk-forward: pick best parameter set on H1, test on H2 — does the
   parameter island survive out-of-sample?
4. Re-run parameter robustness sweep on each half independently

Acceptance criteria:
- Best 12mo variant must produce positive improvement on H2 (recent year)
- Walk-forward must produce positive improvement on H2
- Parameter island must contain SOME positive overlap between H1 and H2
- If recent year (H2) is positive AND walk-forward positive → PASS Gate 1
- Recent year carries more weight than older year (per user constraint)
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path("/workspace/crypto_backtest")
TRADES_PATH_24 = REPO / "results/v4/s523c_growth_24mo_50k_trades.json"
OHLCV_DIR = REPO / "data/perp/binance/1h_ohlcv"
OUT_REPORT = REPO / "research/mission_p_gate1_report.md"
OUT_RESULTS = REPO / "research/mission_p_gate1_results.json"

LEVERAGE = 2.6
COST_8BPS = 0.0008
COST_50BPS = 0.005
BACKTEST_END = pd.Timestamp("2026-04-05 16:00", tz="UTC")
BACKTEST_START = BACKTEST_END - pd.Timedelta(days=730)  # 24 months
H1_END = BACKTEST_START + pd.Timedelta(days=365)  # midpoint, end of older year


def load_ohlcv(token: str) -> pd.DataFrame | None:
    p = OHLCV_DIR / f"{token}_perp_1h.csv"
    if not p.exists():
        return None
    df = pd.read_csv(p, parse_dates=["datetime"]).set_index("datetime").sort_index()
    if df.index.tz is not None:
        df.index = df.index.tz_convert(None)
    return df


def main() -> None:
    print("Loading 24-month trade log…")
    with open(TRADES_PATH_24) as f:
        trades_raw = json.load(f)
    print(f"  {len(trades_raw)} trades")

    trades = []
    for tr in trades_raw:
        entry_dt = (BACKTEST_START + pd.Timedelta(hours=int(tr["entry_bar"]))).tz_localize(None)
        exit_dt = (BACKTEST_START + pd.Timedelta(hours=int(tr["exit_bar"]))).tz_localize(None)
        trades.append({
            "token": tr["token"],
            "direction": int(tr["direction"]),
            "entry_dt": entry_dt,
            "exit_dt": exit_dt,
            "entry_price": float(tr["entry_price"]),
            "pnl_dollars": float(tr["pnl"]),
            "margin_usd": float(tr["margin_usd"]),
        })

    # ---- partition by entry date ----
    h1_trades_idx = [i for i, t in enumerate(trades) if t["entry_dt"] < H1_END.tz_localize(None)]
    h2_trades_idx = [i for i, t in enumerate(trades) if t["entry_dt"] >= H1_END.tz_localize(None)]
    print(f"  H1 (older year, 2024-04 → 2025-04): {len(h1_trades_idx)} trades")
    print(f"  H2 (recent year, 2025-04 → 2026-04): {len(h2_trades_idx)} trades")

    # ---- cache OHLCV ----
    print("Caching OHLCV…")
    tokens = sorted({t["token"] for t in trades})
    ohlcv: dict[str, pd.DataFrame | None] = {tok: load_ohlcv(tok) for tok in tokens}
    n_with = sum(1 for v in ohlcv.values() if v is not None)
    print(f"  {n_with}/{len(tokens)} tokens have OHLCV")

    def price_at(token: str, dt: pd.Timestamp) -> float | None:
        df = ohlcv.get(token)
        if df is None:
            return None
        try:
            ix = df.index.get_indexer([dt], method="pad")[0]
            return float(df.iloc[ix]["close"]) if ix >= 0 else None
        except Exception:
            return None

    # ---- snapshot builder ----
    days = pd.date_range(BACKTEST_START.tz_localize(None), BACKTEST_END.tz_localize(None), freq="D")

    def build_snapshots(grace_days: int) -> pd.DataFrame:
        rows = []
        for day in days:
            for i, tr in enumerate(trades):
                if tr["entry_dt"] + pd.Timedelta(days=grace_days) > day:
                    continue
                if day >= tr["exit_dt"]:
                    continue
                p = price_at(tr["token"], day)
                if p is None:
                    continue
                cur_ret = (p - tr["entry_price"]) / tr["entry_price"] * tr["direction"]
                rows.append({
                    "day": day,
                    "trade_idx": i,
                    "direction": tr["direction"],
                    "current_pnl_pct_lev": cur_ret * LEVERAGE,
                    "current_close_price": p,
                    "underwater": int(cur_ret < 0),
                })
        return pd.DataFrame(rows)

    # ---- simulator (subset-aware) ----
    def simulate(
        grace_days: int, breadth_thr: float, throttle_days: int,
        snapshots: pd.DataFrame, cost: float, trade_filter_idx: list[int] | None = None,
    ) -> dict:
        forced: dict[int, dict] = {}
        last_cull: dict[int, pd.Timestamp] = {}

        # Restrict snapshots to filtered trades if needed (for H1 / H2 isolation)
        snaps = snapshots
        if trade_filter_idx is not None:
            allowed = set(trade_filter_idx)
            snaps = snapshots[snapshots["trade_idx"].isin(allowed)]

        for day, day_snap in snaps.groupby("day"):
            for direction in [1, -1]:
                l = last_cull.get(direction)
                if l is not None and (day - l).days < throttle_days:
                    continue
                ds = day_snap[day_snap["direction"] == direction]
                if len(ds) < 5:
                    continue
                if ds["underwater"].mean() < breadth_thr:
                    continue
                culled = 0
                for _, s in ds.iterrows():
                    idx = int(s["trade_idx"])
                    if idx in forced:
                        continue
                    forced[idx] = {
                        "day": day,
                        "price": s["current_close_price"],
                        "pnl_lev": s["current_pnl_pct_lev"],
                    }
                    culled += 1
                if culled > 0:
                    last_cull[direction] = day

        # ---- score (only over filtered trades) ----
        if trade_filter_idx is None:
            target_trades = list(range(len(trades)))
        else:
            target_trades = trade_filter_idx

        total_orig = 0.0
        total_new = 0.0
        impacts: list[float] = []
        for i in target_trades:
            tr = trades[i]
            orig = tr["pnl_dollars"]
            total_orig += orig
            if i in forced:
                cf_pct = forced[i]["pnl_lev"] - cost
                cf_dollars = cf_pct * tr["margin_usd"]
                total_new += cf_dollars
                impacts.append(cf_dollars - orig)
            else:
                total_new += orig

        impacts_arr = np.array(impacts) if impacts else np.array([0.0])
        return {
            "n_trades_in_set": len(target_trades),
            "grace_days": grace_days,
            "breadth_thr": breadth_thr,
            "throttle_days": throttle_days,
            "cost_bps": cost * 10000,
            "n_force_closed": sum(1 for i in forced if i in (target_trades if trade_filter_idx is not None else set(forced.keys()) | set(target_trades))),
            "n_helped": int((impacts_arr > 0).sum()),
            "n_hurt": int((impacts_arr < 0).sum()),
            "saved_dollars": float(impacts_arr[impacts_arr > 0].sum()),
            "given_back_dollars": float(impacts_arr[impacts_arr < 0].sum()),
            "total_orig": float(total_orig),
            "total_new": float(total_new),
            "total_improvement": float(total_new - total_orig),
            "improvement_pct": float((total_new - total_orig) / abs(total_orig)) if total_orig != 0 else 0.0,
        }

    # ---- build snapshots once for the best grace ----
    print("Building snapshots (grace=3d)…")
    snaps_3d = build_snapshots(3)
    print(f"  {len(snaps_3d)} snapshots")

    # ============================================================
    # TEST 1 — Best 12mo variant on full 24mo
    # ============================================================
    print("\n" + "=" * 80)
    print("TEST 1 — Best 12mo variant (g3d_br80_th14d) on full 24mo")
    print("=" * 80)
    r_full_8bps = simulate(3, 0.80, 14, snaps_3d, COST_8BPS)
    r_full_50bps = simulate(3, 0.80, 14, snaps_3d, COST_50BPS)
    print(f"At 8bps:  Δ${r_full_8bps['total_improvement']:+,.0f} ({r_full_8bps['improvement_pct']:+.1%}), helped={r_full_8bps['n_helped']}, hurt={r_full_8bps['n_hurt']}")
    print(f"At 50bps: Δ${r_full_50bps['total_improvement']:+,.0f} ({r_full_50bps['improvement_pct']:+.1%}), helped={r_full_50bps['n_helped']}, hurt={r_full_50bps['n_hurt']}")

    # ============================================================
    # TEST 2 — Per-half analysis (H1 vs H2 isolated)
    # ============================================================
    print("\n" + "=" * 80)
    print("TEST 2 — Per-half breakdown (best 12mo variant)")
    print("=" * 80)
    r_h1 = simulate(3, 0.80, 14, snaps_3d, COST_8BPS, trade_filter_idx=h1_trades_idx)
    r_h2 = simulate(3, 0.80, 14, snaps_3d, COST_8BPS, trade_filter_idx=h2_trades_idx)
    r_h1_50 = simulate(3, 0.80, 14, snaps_3d, COST_50BPS, trade_filter_idx=h1_trades_idx)
    r_h2_50 = simulate(3, 0.80, 14, snaps_3d, COST_50BPS, trade_filter_idx=h2_trades_idx)
    print(f"H1 (older year):  Δ${r_h1['total_improvement']:+,.0f} ({r_h1['improvement_pct']:+.1%}) | 50bps: Δ${r_h1_50['total_improvement']:+,.0f} ({r_h1_50['improvement_pct']:+.1%})")
    print(f"H2 (recent year): Δ${r_h2['total_improvement']:+,.0f} ({r_h2['improvement_pct']:+.1%}) | 50bps: Δ${r_h2_50['total_improvement']:+,.0f} ({r_h2_50['improvement_pct']:+.1%})")

    # ============================================================
    # TEST 3 — Per-half parameter sweep
    # ============================================================
    print("\n" + "=" * 80)
    print("TEST 3 — Parameter sweep on each half independently")
    print("=" * 80)
    print("\nH1 sweep (older year, 2024-04 → 2025-04):")
    print(f"{'breadth':>8} {'throttle':>10} {'Δ$':>14} {'Δ%':>9} {'culls':>8}")
    h1_results = {}
    for breadth in [0.65, 0.70, 0.75, 0.80, 0.85]:
        for throttle in [7, 10, 14, 18, 21]:
            r = simulate(3, breadth, throttle, snaps_3d, COST_8BPS, trade_filter_idx=h1_trades_idx)
            h1_results[(breadth, throttle)] = r
            if abs(r['improvement_pct']) > 0.01:
                print(f"{breadth:>8.0%} {throttle:>9}d ${r['total_improvement']:>+13,.0f} {r['improvement_pct']:>+8.1%} {r['n_force_closed']:>8}")

    print("\nH2 sweep (recent year, 2025-04 → 2026-04):")
    print(f"{'breadth':>8} {'throttle':>10} {'Δ$':>14} {'Δ%':>9} {'culls':>8}")
    h2_results = {}
    for breadth in [0.65, 0.70, 0.75, 0.80, 0.85]:
        for throttle in [7, 10, 14, 18, 21]:
            r = simulate(3, breadth, throttle, snaps_3d, COST_8BPS, trade_filter_idx=h2_trades_idx)
            h2_results[(breadth, throttle)] = r
            if abs(r['improvement_pct']) > 0.01:
                print(f"{breadth:>8.0%} {throttle:>9}d ${r['total_improvement']:>+13,.0f} {r['improvement_pct']:>+8.1%} {r['n_force_closed']:>8}")

    # ============================================================
    # TEST 4 — Walk-forward: pick best on H1, test on H2
    # ============================================================
    print("\n" + "=" * 80)
    print("TEST 4 — Walk-forward: parameters picked on H1, tested on H2")
    print("=" * 80)
    h1_best_key = max(h1_results.items(), key=lambda x: x[1]["improvement_pct"])
    h1_best_breadth, h1_best_throttle = h1_best_key[0]
    h1_best_metric = h1_best_key[1]
    print(f"H1 best: breadth={h1_best_breadth:.0%}, throttle={h1_best_throttle}d → {h1_best_metric['improvement_pct']:+.1%}")
    # Apply that on H2
    r_wf = simulate(3, h1_best_breadth, h1_best_throttle, snaps_3d, COST_8BPS, trade_filter_idx=h2_trades_idx)
    print(f"Applied on H2: Δ${r_wf['total_improvement']:+,.0f} ({r_wf['improvement_pct']:+.1%}), helped={r_wf['n_helped']}, hurt={r_wf['n_hurt']}")
    r_wf_50 = simulate(3, h1_best_breadth, h1_best_throttle, snaps_3d, COST_50BPS, trade_filter_idx=h2_trades_idx)
    print(f"H2 at 50bps: Δ${r_wf_50['total_improvement']:+,.0f} ({r_wf_50['improvement_pct']:+.1%})")

    # Reverse direction too
    h2_best_key = max(h2_results.items(), key=lambda x: x[1]["improvement_pct"])
    h2_best_breadth, h2_best_throttle = h2_best_key[0]
    h2_best_metric = h2_best_key[1]
    print(f"\nH2 best: breadth={h2_best_breadth:.0%}, throttle={h2_best_throttle}d → {h2_best_metric['improvement_pct']:+.1%}")
    r_wf_rev = simulate(3, h2_best_breadth, h2_best_throttle, snaps_3d, COST_8BPS, trade_filter_idx=h1_trades_idx)
    print(f"Applied on H1: Δ${r_wf_rev['total_improvement']:+,.0f} ({r_wf_rev['improvement_pct']:+.1%})")

    # ============================================================
    # TEST 5 — Edge island overlap
    # ============================================================
    print("\n" + "=" * 80)
    print("TEST 5 — Parameter combinations positive in BOTH halves")
    print("=" * 80)
    overlap = []
    for k, v in h1_results.items():
        h2 = h2_results[k]
        if v["improvement_pct"] > 0 and h2["improvement_pct"] > 0:
            overlap.append((k, v["improvement_pct"], h2["improvement_pct"]))
    overlap.sort(key=lambda x: x[1] + x[2], reverse=True)
    print(f"{'breadth':>8} {'throttle':>10} {'H1 Δ%':>10} {'H2 Δ%':>10} {'sum':>10}")
    for k, h1_pct, h2_pct in overlap:
        b, t = k
        print(f"{b:>8.0%} {t:>9}d {h1_pct:>+9.1%} {h2_pct:>+9.1%} {h1_pct+h2_pct:>+9.1%}")
    if not overlap:
        print("  NO parameter combinations are positive in both halves.")

    # ============================================================
    # VERDICT
    # ============================================================
    h2_pct = r_h2["improvement_pct"]
    h2_50_pct = r_h2_50["improvement_pct"]
    wf_h2_pct = r_wf["improvement_pct"]
    wf_h2_50_pct = r_wf_50["improvement_pct"]

    verdict = "KILL"
    reasons: list[str] = []
    # User said: weight recent year heavily. Recent year = H2.
    if h2_pct >= 0.05 and h2_50_pct >= 0.03 and wf_h2_pct >= 0.03 and len(overlap) >= 1:
        verdict = "PASS"
        reasons.append(f"Recent year (H2) with best 12mo variant: {h2_pct:+.1%} at 8bps, {h2_50_pct:+.1%} at 50bps")
        reasons.append(f"Walk-forward (H1→H2): {wf_h2_pct:+.1%} at 8bps")
        reasons.append(f"Edge island overlap: {len(overlap)} parameter combos positive in both halves")
    elif h2_pct > 0 and wf_h2_pct > 0:
        verdict = "MARGINAL"
        reasons.append(f"H2 positive ({h2_pct:+.1%}) but below threshold")
        reasons.append(f"Walk-forward H2 positive ({wf_h2_pct:+.1%}) but below threshold")
    else:
        if h2_pct <= 0:
            reasons.append(f"Recent year (H2) negative or zero: {h2_pct:+.1%}")
        if wf_h2_pct <= 0:
            reasons.append(f"Walk-forward H2 negative: {wf_h2_pct:+.1%}")
        if not overlap:
            reasons.append("No parameter combos positive in both halves")

    results = {
        "verdict": verdict,
        "verdict_reasons": reasons,
        "test1_full_24mo_8bps": r_full_8bps,
        "test1_full_24mo_50bps": r_full_50bps,
        "test2_h1": r_h1,
        "test2_h2": r_h2,
        "test2_h1_50bps": r_h1_50,
        "test2_h2_50bps": r_h2_50,
        "test4_wf_h1_to_h2": r_wf,
        "test4_wf_h1_to_h2_50bps": r_wf_50,
        "test4_wf_h2_to_h1": r_wf_rev,
        "test5_overlap_count": len(overlap),
        "test5_overlap": [{"breadth": k[0], "throttle": k[1], "h1_pct": h1p, "h2_pct": h2p} for k, h1p, h2p in overlap],
    }
    OUT_RESULTS.write_text(json.dumps(results, indent=2, default=str))

    print(f"\n{'='*80}")
    print(f"VERDICT: {verdict}")
    for r in reasons:
        print(f"  {r}")
    print(f"{'='*80}")


if __name__ == "__main__":
    main()
