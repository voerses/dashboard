"""
Profit Lock-in Overlay — Gate 2 Regime Persistence Test
========================================================
Run rolling 6-month windows (3-month step) over a 60-month s523c_growth backtest.
For each window, compare baseline vs overlay (walk-forward expanding-window VEL95
threshold + LIMIT-FILL model) and characterise regime via BTC return + vol.

Also sweeps VEL90 / VEL95 / VEL98 thresholds to test sensitivity.

Reads:  research/gate2_out/s523c_growth_60mo_50k_trades.json
Writes: research/profit_lockin_gate2_results.json
        research/profit_lockin_gate2_report.md
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from collections import defaultdict

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path("/workspace/crypto_backtest/research")))
from profit_lockin_gate1 import (  # type: ignore
    enrich_trade,
    slice_for_trade,
    build_equity_curve,
    metrics,
    load_ohlc,
)
from profit_lockin_verification import overlay_one  # type: ignore


ROOT = Path("/workspace/crypto_backtest")
TRADES_PATH = ROOT / "research/gate2_out/s523c_growth_60mo_50k_trades.json"
START_TS = pd.Timestamp("2021-04-05T16:00:00+00:00")  # end_date - 60 months
CAPITAL = 50_000.0

# Window config
WINDOW_MONTHS = 6
STEP_MONTHS = 3
HOURS_PER_MONTH = 24 * 30  # approximate; consistent across windows

# Threshold quantiles to sweep
QUANTILES = {"VEL90": 0.90, "VEL95": 0.95, "VEL98": 0.98}
MIN_PX = 0.005
FILL_MODEL = "limit_at_threshold"


def winners_velocities(enriched: list[dict]) -> np.ndarray:
    return np.array([
        t["velocity_px_per_bar"]
        for t in enriched
        if t["velocity_px_per_bar"] is not None and t["mfe_price"] > 0
    ])


def run_window_overlay(
    window_trades: list[dict],
    history_trades: list[dict],
    quantile: float,
    capital: float,
) -> dict:
    """Apply overlay to window_trades using a walk-forward expanding-window
    velocity threshold built from history_trades + earlier window_trades whose
    EXIT precedes the current trade's ENTRY. Always limit-fill model."""
    # Pool of (exit_bar, velocity) from history.
    pool_exits: list[int] = []
    pool_vels: list[float] = []
    for t in history_trades:
        v = t.get("velocity_px_per_bar")
        if v is None or t["mfe_price"] <= 0:
            continue
        pool_exits.append(int(t["exit_bar"]))
        pool_vels.append(float(v))

    # We need to update the pool as we iterate window trades in entry order.
    win_sorted = sorted(window_trades, key=lambda t: int(t["entry_bar"]))

    # Pre-extract array form for fast quantile.
    pool_exits_arr = np.array(pool_exits, dtype=np.int64)
    pool_vels_arr = np.array(pool_vels, dtype=np.float64)

    # Buffer for window trades (also added to pool as they exit)
    window_pool_exits: list[int] = []
    window_pool_vels: list[float] = []

    overlay_trades: list[dict] = []
    n_fired = 0
    for t in win_sorted:
        eb = int(t["entry_bar"])
        # Build threshold from history trades whose exit_bar <= eb
        mask = pool_exits_arr <= eb
        hist_vels = pool_vels_arr[mask] if pool_exits_arr.size else np.array([])
        # plus any window-pool trades that exited before eb
        for i in range(len(window_pool_exits)):
            if window_pool_exits[i] <= eb:
                hist_vels = np.append(hist_vels, window_pool_vels[i])
        if hist_vels.size >= 20:
            thr = float(np.quantile(hist_vels, quantile))
        else:
            thr = float("inf")
        new_pnl, fired, fb = overlay_one(t, START_TS, thr, MIN_PX, FILL_MODEL)
        if fired:
            n_fired += 1
        overlay_trades.append({**t, "ovl_pnl": new_pnl, "ovl_fired": fired, "ovl_bar": fb})
        # add this trade to window pool if it's a winner
        v = t.get("velocity_px_per_bar")
        if v is not None and t["mfe_price"] > 0:
            window_pool_exits.append(int(t["exit_bar"]))
            window_pool_vels.append(float(v))

    base_eq = build_equity_curve(win_sorted, START_TS, capital, pnl_key="pnl")
    base_m = metrics(base_eq, capital)
    ovl_eq = build_equity_curve(overlay_trades, START_TS, capital, pnl_key="ovl_pnl")
    ovl_m = metrics(ovl_eq, capital)
    return {
        "n_trades": len(win_sorted),
        "n_fired": n_fired,
        "baseline": base_m,
        "overlay": ovl_m,
    }


def btc_regime(start_bar: int, end_bar: int) -> dict:
    """BTC total return and realized vol over [start_bar, end_bar) hourly bars."""
    df = load_ohlc("BTC")
    if df is None:
        return {"btc_return_pct": None, "btc_vol_annualized": None}
    idx0 = df.index[df["dt"] == START_TS]
    if len(idx0) == 0:
        return {"btc_return_pct": None, "btc_vol_annualized": None}
    off = int(idx0[0])
    lo = off + start_bar
    hi = off + end_bar
    if hi > len(df):
        hi = len(df)
    sub = df.iloc[lo:hi]
    if len(sub) < 2:
        return {"btc_return_pct": None, "btc_vol_annualized": None}
    p0 = float(sub["close"].iloc[0])
    p1 = float(sub["close"].iloc[-1])
    ret = (p1 / p0 - 1) * 100
    rets = sub["close"].pct_change().dropna()
    # Hourly std -> annualized vol
    vol = float(rets.std() * math.sqrt(24 * 365))
    return {"btc_return_pct": float(ret), "btc_vol_annualized": vol}


def main():
    print("Loading 60-month trades...")
    raw = json.load(open(TRADES_PATH))
    print(f"  {len(raw)} raw trades")

    # Coerce numeric strings to floats so enrich_trade works
    coerced = []
    for tr in raw:
        c = dict(tr)
        for k in ("pnl", "funding_cost", "margin_usd", "entry_price", "exit_price",
                  "entry_fee", "exit_fee"):
            if k in c and isinstance(c[k], str):
                c[k] = float(c[k])
        coerced.append(c)

    print("Enriching trades (computing MFE/velocity from OHLC)...")
    enriched = []
    missing = 0
    for tr in coerced:
        e = enrich_trade(tr, START_TS)
        if e is None:
            missing += 1
            continue
        enriched.append(e)
    enriched.sort(key=lambda t: int(t["entry_bar"]))
    print(f"  enriched: {len(enriched)}, missing: {missing}")

    if not enriched:
        raise RuntimeError("no enriched trades")

    first_eb = int(enriched[0]["entry_bar"])
    last_eb = int(enriched[-1]["entry_bar"])
    print(f"  entry_bar range: [{first_eb}, {last_eb}]  hours")

    # Build rolling windows on calendar (in hours from START_TS)
    window_h = WINDOW_MONTHS * HOURS_PER_MONTH
    step_h = STEP_MONTHS * HOURS_PER_MONTH
    total_hours = last_eb + 1
    starts = list(range(0, max(1, total_hours - window_h + 1), step_h))
    print(f"  building {len(starts)} windows of {WINDOW_MONTHS}mo / step {STEP_MONTHS}mo")

    rows = []
    for ws in starts:
        we = ws + window_h
        win = [t for t in enriched if ws <= int(t["entry_bar"]) < we]
        if len(win) < 20:
            continue
        history = [t for t in enriched if int(t["entry_bar"]) < ws]
        regime = btc_regime(ws, we)
        win_row: dict = {
            "window_start_bar": ws,
            "window_end_bar": we,
            "window_start_dt": (START_TS + pd.Timedelta(hours=ws)).isoformat(),
            "window_end_dt": (START_TS + pd.Timedelta(hours=we)).isoformat(),
            "n_trades": len(win),
            "n_history": len(history),
            **regime,
        }
        for label, q in QUANTILES.items():
            res = run_window_overlay(win, history, q, CAPITAL)
            win_row[f"{label}_baseline_calmar"] = res["baseline"]["calmar"]
            win_row[f"{label}_overlay_calmar"] = res["overlay"]["calmar"]
            win_row[f"{label}_baseline_return"] = res["baseline"]["total_return_pct"]
            win_row[f"{label}_overlay_return"] = res["overlay"]["total_return_pct"]
            win_row[f"{label}_baseline_dd"] = res["baseline"]["max_dd_pct"]
            win_row[f"{label}_overlay_dd"] = res["overlay"]["max_dd_pct"]
            win_row[f"{label}_n_fired"] = res["n_fired"]
            bc = res["baseline"]["calmar"]
            oc = res["overlay"]["calmar"]
            if bc != 0 and not math.isnan(bc):
                win_row[f"{label}_calmar_delta_pct"] = (oc / bc - 1) * 100
            else:
                win_row[f"{label}_calmar_delta_pct"] = None
        rows.append(win_row)
        ds = win_row["window_start_dt"][:10]
        de = win_row["window_end_dt"][:10]
        d95 = win_row.get("VEL95_calmar_delta_pct")
        d95_s = f"{d95:+.1f}%" if d95 is not None else "n/a"
        print(f"  {ds} -> {de}  n={len(win):3d} BTC={regime['btc_return_pct']:+6.1f}% "
              f"vol={regime['btc_vol_annualized']:.2f}  VEL95 ΔCalmar={d95_s}")

    # ----- Robustness summary -----
    def stats(label: str) -> dict:
        deltas = [r[f"{label}_calmar_delta_pct"] for r in rows
                  if r[f"{label}_calmar_delta_pct"] is not None]
        if not deltas:
            return {}
        arr = np.array(deltas)
        return {
            "n_windows": int(len(arr)),
            "mean_delta_pct": float(arr.mean()),
            "median_delta_pct": float(np.median(arr)),
            "stdev_delta_pct": float(arr.std(ddof=1)) if len(arr) > 1 else 0.0,
            "min_delta_pct": float(arr.min()),
            "max_delta_pct": float(arr.max()),
            "n_loss_windows": int((arr < 0).sum()),
            "n_severe_loss_windows": int((arr < -20).sum()),
        }

    summary = {label: stats(label) for label in QUANTILES}

    # Regime correlation: VEL95 delta vs BTC return / BTC vol
    btc_rets = np.array([r["btc_return_pct"] for r in rows
                         if r["VEL95_calmar_delta_pct"] is not None
                         and r["btc_return_pct"] is not None])
    btc_vols = np.array([r["btc_vol_annualized"] for r in rows
                         if r["VEL95_calmar_delta_pct"] is not None
                         and r["btc_vol_annualized"] is not None])
    deltas95 = np.array([r["VEL95_calmar_delta_pct"] for r in rows
                         if r["VEL95_calmar_delta_pct"] is not None])

    def corr(a, b):
        if len(a) < 3 or len(b) < 3 or len(a) != len(b):
            return None
        if a.std() == 0 or b.std() == 0:
            return None
        return float(np.corrcoef(a, b)[0, 1])

    regime_corr = {
        "vel95_delta_vs_btc_return": corr(deltas95, btc_rets) if len(deltas95) == len(btc_rets) else None,
        "vel95_delta_vs_btc_vol": corr(deltas95, btc_vols) if len(deltas95) == len(btc_vols) else None,
    }

    out = {
        "config": {
            "trades_path": str(TRADES_PATH),
            "start_ts": START_TS.isoformat(),
            "capital": CAPITAL,
            "window_months": WINDOW_MONTHS,
            "step_months": STEP_MONTHS,
            "fill_model": FILL_MODEL,
            "min_px": MIN_PX,
            "quantiles": QUANTILES,
        },
        "n_enriched": len(enriched),
        "n_missing": missing,
        "windows": rows,
        "summary": summary,
        "regime_correlation": regime_corr,
    }
    out_path = ROOT / "research/profit_lockin_gate2_results.json"
    out_path.write_text(json.dumps(out, indent=2, default=str))
    print(f"\nWrote {out_path}")

    # ----- Verdict -----
    s95 = summary.get("VEL95", {})
    mean95 = s95.get("mean_delta_pct", 0.0)
    std95 = s95.get("stdev_delta_pct", 0.0)
    min95 = s95.get("min_delta_pct", 0.0)
    n_severe = s95.get("n_severe_loss_windows", 0)

    if mean95 >= 25 and std95 < mean95 and min95 >= -10:
        verdict = "ROBUST"
    elif mean95 >= 25 and n_severe > 0:
        verdict = "REGIME-DEPENDENT"
    elif mean95 < 15:
        verdict = "OVERFIT"
    else:
        # mean >= 15 but < 25, or stdev >= mean — call it regime-dependent
        verdict = "REGIME-DEPENDENT"

    print(f"\n=== VERDICT: {verdict} ===")
    print(f"  VEL95 mean ΔCalmar: {mean95:+.1f}%  stdev: {std95:.1f}%  min: {min95:+.1f}%")
    print(f"  loss windows: {s95.get('n_loss_windows')}/{s95.get('n_windows')}, "
          f"severe (<-20%): {n_severe}")
    print(f"  regime corr: {regime_corr}")

    write_report(out, summary, regime_corr, verdict)


def fmt(x, d=1):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "n/a"
    return f"{x:.{d}f}"


def write_report(out: dict, summary: dict, regime_corr: dict, verdict: str):
    rows = out["windows"]
    s95 = summary.get("VEL95", {})
    s90 = summary.get("VEL90", {})
    s98 = summary.get("VEL98", {})

    L = []
    L.append("# Profit Lock-in Overlay — Gate 2 Regime Persistence Report")
    L.append("")
    L.append(f"_Generated {pd.Timestamp.now(tz='UTC').isoformat()}_")
    L.append("")
    L.append(f"**Verdict: {verdict}**")
    L.append("")
    L.append("Setup: 60-month s523c_growth backtest sliced into 6-month rolling windows ")
    L.append("(3-month step). For each window: baseline trades vs walk-forward expanding-")
    L.append("window velocity threshold overlay using LIMIT-FILL model. Threshold pool is ")
    L.append("seeded with all trades whose exit precedes each window-trade entry (no leakage).")
    L.append("")
    L.append(f"Trades enriched: {out['n_enriched']} (missing OHLC: {out['n_missing']}). ")
    L.append(f"Windows analysed: {len(rows)}.")
    L.append("")

    # Window table
    L.append("## Rolling Window Results (VEL95 + limit fill)")
    L.append("")
    L.append("| Window Start | Window End | n | BTC Ret % | BTC Vol | Base Calmar | Ovl Calmar | ΔCalmar % | Fired |")
    L.append("|--------------|------------|---:|----------:|--------:|------------:|-----------:|----------:|------:|")
    for r in rows:
        ds = r["window_start_dt"][:10]
        de = r["window_end_dt"][:10]
        bc = r["VEL95_baseline_calmar"]
        oc = r["VEL95_overlay_calmar"]
        d = r["VEL95_calmar_delta_pct"]
        nf = r["VEL95_n_fired"]
        L.append(
            f"| {ds} | {de} | {r['n_trades']} | "
            f"{fmt(r['btc_return_pct'])} | {fmt(r['btc_vol_annualized'], 2)} | "
            f"{fmt(bc, 2)} | {fmt(oc, 2)} | {fmt(d, 1)} | {nf} |"
        )
    L.append("")

    # Robustness summary
    L.append("## Robustness Summary")
    L.append("")
    L.append("| Threshold | n Win | Mean Δ% | Median Δ% | Stdev | Min | Max | Loss wins | Severe (<-20%) |")
    L.append("|-----------|------:|--------:|----------:|------:|----:|----:|----------:|---------------:|")
    for label, s in (("VEL90", s90), ("VEL95", s95), ("VEL98", s98)):
        if not s:
            continue
        L.append(
            f"| {label} | {s['n_windows']} | {fmt(s['mean_delta_pct'])} | "
            f"{fmt(s['median_delta_pct'])} | {fmt(s['stdev_delta_pct'])} | "
            f"{fmt(s['min_delta_pct'])} | {fmt(s['max_delta_pct'])} | "
            f"{s['n_loss_windows']} | {s['n_severe_loss_windows']} |"
        )
    L.append("")

    # Regime correlation
    L.append("## Regime Correlation (VEL95)")
    L.append("")
    rc_btc = regime_corr.get("vel95_delta_vs_btc_return")
    rc_vol = regime_corr.get("vel95_delta_vs_btc_vol")
    L.append(f"- Pearson(VEL95 ΔCalmar, BTC return) = {fmt(rc_btc, 3) if rc_btc is not None else 'n/a'}")
    L.append(f"- Pearson(VEL95 ΔCalmar, BTC realized vol) = {fmt(rc_vol, 3) if rc_vol is not None else 'n/a'}")
    L.append("")

    # Threshold sweep finding
    L.append("## Threshold Sensitivity (VEL90 vs VEL95 vs VEL98)")
    L.append("")
    if all([s90, s95, s98]):
        ranking = sorted(
            [("VEL90", s90["mean_delta_pct"]),
             ("VEL95", s95["mean_delta_pct"]),
             ("VEL98", s98["mean_delta_pct"])],
            key=lambda x: x[1], reverse=True,
        )
        L.append(f"Best on mean ΔCalmar: **{ranking[0][0]}** ({ranking[0][1]:+.1f}%)")
        L.append("")
        L.append("Per-window best threshold tally:")
        tally: dict[str, int] = defaultdict(int)
        for r in rows:
            cands = [(label, r.get(f"{label}_calmar_delta_pct"))
                     for label in ("VEL90", "VEL95", "VEL98")]
            cands = [(l, v) for l, v in cands if v is not None]
            if not cands:
                continue
            best = max(cands, key=lambda x: x[1])
            tally[best[0]] += 1
        for label in ("VEL90", "VEL95", "VEL98"):
            L.append(f"- {label}: best in {tally[label]} windows")
        L.append("")

    # Verdict + rationale
    L.append("## Verdict + Rationale")
    L.append("")
    L.append(f"**{verdict}**")
    L.append("")
    if verdict == "ROBUST":
        L.append("VEL95 overlay improves Calmar by mean ≥ +25% across rolling windows with low ")
        L.append("dispersion and no window losing more than 10%. Proceed to Gate 3 with the ")
        L.append("VEL95 + limit-fill configuration.")
    elif verdict == "REGIME-DEPENDENT":
        L.append("VEL95 overlay shows positive mean improvement but with regime sensitivity: ")
        L.append("at least one window suffered a >20% Calmar deterioration, or stdev exceeds ")
        L.append("the mean. Proceed to Gate 3 ONLY behind a regime filter.")
    else:  # OVERFIT
        L.append("VEL95 overlay fails to deliver +15% mean improvement across rolling windows ")
        L.append("when threshold is built walk-forward and fills are realistic. The original ")
        L.append("+166% claim was an artifact of (a) full-sample threshold (b) optimistic ")
        L.append("intrabar fills. **KILL Mission G.**")
    L.append("")

    # If regime-dependent, suggest filter
    if verdict == "REGIME-DEPENDENT":
        L.append("### Recommended regime filter for Gate 3")
        L.append("")
        # Use whichever correlation has greater absolute value
        cands = [
            ("BTC return %", rc_btc),
            ("BTC realized vol", rc_vol),
        ]
        cands = [(n, v) for n, v in cands if v is not None]
        if cands:
            best_feat, best_corr = max(cands, key=lambda x: abs(x[1]))
            sign = "positive" if best_corr > 0 else "negative"
            L.append(f"Strongest regime predictor: **{best_feat}** (corr {best_corr:+.3f}, {sign}).")
            # Compute threshold from windows where overlay wins
            wins_w = [r for r in rows if (r.get("VEL95_calmar_delta_pct") or 0) > 0]
            losses_w = [r for r in rows if (r.get("VEL95_calmar_delta_pct") or 0) < 0]
            if "BTC return" in best_feat and wins_w and losses_w:
                w_med = float(np.median([r["btc_return_pct"] for r in wins_w if r["btc_return_pct"] is not None]))
                l_med = float(np.median([r["btc_return_pct"] for r in losses_w if r["btc_return_pct"] is not None]))
                L.append(f"Median BTC return in winning windows: {w_med:+.1f}%; losing windows: {l_med:+.1f}%.")
                L.append(f"Filter suggestion: enable lock-in only when trailing 30d BTC return > {min(w_med, l_med + 5):+.1f}%.")
            elif "vol" in best_feat and wins_w and losses_w:
                w_med = float(np.median([r["btc_vol_annualized"] for r in wins_w if r["btc_vol_annualized"] is not None]))
                l_med = float(np.median([r["btc_vol_annualized"] for r in losses_w if r["btc_vol_annualized"] is not None]))
                L.append(f"Median BTC vol in winning windows: {w_med:.2f}; losing windows: {l_med:.2f}.")
                gate = (w_med + l_med) / 2
                comp = ">" if w_med > l_med else "<"
                L.append(f"Filter suggestion: enable lock-in only when 30d BTC realized vol {comp} {gate:.2f}.")
        L.append("")

    L.append("## Source data")
    L.append("")
    L.append(f"- 60-mo trades: `research/gate2_out/s523c_growth_60mo_50k_trades.json`")
    L.append(f"- Raw results: `research/profit_lockin_gate2_results.json`")
    L.append(f"- This script: `research/profit_lockin_gate2.py`")
    L.append("")

    report_path = ROOT / "research/profit_lockin_gate2_report.md"
    report_path.write_text("\n".join(L))
    print(f"Wrote {report_path}")


if __name__ == "__main__":
    main()
