"""
Profit Lock-in Overlay — Gate 2 Kill Autopsy
=============================================
Audit the Gate 2 kill of Mission G against 8 specific methodology issues.
Reuses the 60-month enriched trade log and the existing overlay helpers.
Produces research/profit_lockin_autopsy_report.md.

Run: /workspace/venv/bin/python research/profit_lockin_autopsy.py
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from collections import defaultdict

import numpy as np
import pandas as pd

ROOT = Path("/workspace/crypto_backtest")
sys.path.insert(0, str(ROOT / "research"))

from profit_lockin_gate1 import (  # type: ignore
    enrich_trade,
    slice_for_trade,
    build_equity_curve,
    metrics,
)
from profit_lockin_verification import overlay_one  # type: ignore

TRADES_PATH = ROOT / "research/gate2_out/s523c_growth_60mo_50k_trades.json"
START_TS = pd.Timestamp("2021-04-05T16:00:00+00:00")
CAPITAL = 50_000.0
WINDOW_MONTHS = 6
STEP_MONTHS = 3
HOURS_PER_MONTH = 24 * 30
QUANTILE = 0.95
MIN_PX = 0.005


# ---------------------------------------------------------------------------
# Local overlay actions for Issue 6 (A-HALFLOCK, A-25)
# ---------------------------------------------------------------------------
def overlay_one_action(t: dict, vth: float, fill_model: str, action: str) -> tuple[float, bool, int | None]:
    """Apply velocity overlay with action variant.

    A-FULL: close 100% at lock price (this is what overlay_one does)
    A-25:   close 25% at lock price; remainder runs to original exit
    A-HALFLOCK: don't close, but cap loss-from-here at entry+50%*unrealized.
                Implementation: realized = max(realized, entry + 0.5*lock)
                In price-fraction terms: new_realized = max(realized_price, 0.5 * lock)
    """
    base_pnl = float(t["pnl"])
    lev = t.get("leverage_inferred")
    if lev is None:
        return base_pnl, False, None
    token = t["token"]
    eb = int(t["entry_bar"])
    xb = int(t["exit_bar"])
    direction = int(t["direction"])
    entry_px = float(t["entry_price"])
    margin = float(t["margin_usd"])

    bars = slice_for_trade(token, START_TS, eb, xb)
    if bars is None or len(bars) == 0:
        return base_pnl, False, None

    highs = bars["high"].to_numpy()
    lows = bars["low"].to_numpy()
    closes = bars["close"].to_numpy()
    opens = bars["open"].to_numpy()

    if direction == 1:
        fav_hi = (highs - entry_px) / entry_px
        fav_cl = (closes - entry_px) / entry_px
        fav_op = (opens - entry_px) / entry_px
    else:
        fav_hi = (entry_px - lows) / entry_px
        fav_cl = (entry_px - closes) / entry_px
        fav_op = (entry_px - opens) / entry_px

    fire_bar = None
    for i in range(len(fav_hi)):
        v = fav_hi[i] / (i + 1)
        if v >= vth and fav_hi[i] >= MIN_PX:
            fire_bar = i
            break
    if fire_bar is None:
        return base_pnl, False, None

    # Fill price
    if fill_model == "limit_at_threshold":
        lock = max(vth * (fire_bar + 1), 0.0)
        lock = min(lock, fav_hi[fire_bar])
    elif fill_model == "open_or_threshold":
        # Issue 5: max(trigger_price, bar_open) — i.e. we got at least the open
        # if open already exceeded trigger; otherwise we got the trigger.
        thr_lock = max(vth * (fire_bar + 1), 0.0)
        lock = max(thr_lock, fav_op[fire_bar])
        lock = min(lock, fav_hi[fire_bar])
    else:
        raise ValueError(fill_model)

    realized = float(t["realized_price"])

    if action == "A-FULL":
        new_pnl = base_pnl + margin * lev * (lock - realized)
    elif action == "A-25":
        # 25% of the position closes at lock, 75% rides to original exit
        new_pnl = base_pnl + 0.25 * margin * lev * (lock - realized)
    elif action == "A-HALFLOCK":
        # Move stop to lock in half of unrealized profit. Effectively the
        # realized return cannot fall below 0.5 * lock from here on.
        # If original realized >= 0.5*lock, no change. Otherwise we get 0.5*lock.
        floor_realized = 0.5 * lock
        if realized >= floor_realized:
            return base_pnl, True, fire_bar  # fired but no $ effect
        new_pnl = base_pnl + margin * lev * (floor_realized - realized)
    else:
        raise ValueError(action)

    return new_pnl, True, fire_bar


# ---------------------------------------------------------------------------
# Per-window evaluation that captures EVERYTHING we need
# ---------------------------------------------------------------------------
def run_window(
    win_trades: list[dict],
    history_trades: list[dict],
    quantile: float,
    fill_model: str = "limit_at_threshold",
    action: str = "A-FULL",
) -> dict:
    pool_exits = []
    pool_vels = []
    for t in history_trades:
        v = t.get("velocity_px_per_bar")
        if v is None or t["mfe_price"] <= 0:
            continue
        pool_exits.append(int(t["exit_bar"]))
        pool_vels.append(float(v))

    pool_exits_arr = np.array(pool_exits, dtype=np.int64)
    pool_vels_arr = np.array(pool_vels, dtype=np.float64)
    pool_size_at_window_start = int(pool_vels_arr.size)

    win_sorted = sorted(win_trades, key=lambda t: int(t["entry_bar"]))

    window_pool_exits = []
    window_pool_vels = []

    overlay_trades = []
    n_fired = 0
    trade_helps = 0
    trade_hurts = 0
    trade_neutral = 0
    abs_trade_pnl_diffs = []
    for t in win_sorted:
        eb = int(t["entry_bar"])
        if pool_exits_arr.size:
            mask = pool_exits_arr <= eb
            hist_vels = pool_vels_arr[mask]
        else:
            hist_vels = np.array([], dtype=np.float64)
        for i in range(len(window_pool_exits)):
            if window_pool_exits[i] <= eb:
                hist_vels = np.append(hist_vels, window_pool_vels[i])
        if hist_vels.size >= 20:
            thr = float(np.quantile(hist_vels, quantile))
        else:
            thr = float("inf")
        new_pnl, fired, fb = overlay_one_action(t, thr, fill_model, action)
        if fired:
            n_fired += 1
        diff = float(new_pnl) - float(t["pnl"])
        abs_trade_pnl_diffs.append(diff)
        if diff > 0.01:
            trade_helps += 1
        elif diff < -0.01:
            trade_hurts += 1
        else:
            trade_neutral += 1
        overlay_trades.append({**t, "ovl_pnl": float(new_pnl), "ovl_fired": fired, "ovl_bar": fb})

        v = t.get("velocity_px_per_bar")
        if v is not None and t["mfe_price"] > 0:
            window_pool_exits.append(int(t["exit_bar"]))
            window_pool_vels.append(float(v))

    base_eq = build_equity_curve(win_sorted, START_TS, CAPITAL, pnl_key="pnl")
    base_m = metrics(base_eq, CAPITAL)
    ovl_eq = build_equity_curve(overlay_trades, START_TS, CAPITAL, pnl_key="ovl_pnl")
    ovl_m = metrics(ovl_eq, CAPITAL)

    base_total_pnl = float(sum(t["pnl"] for t in win_sorted))
    ovl_total_pnl = float(sum(t["ovl_pnl"] for t in overlay_trades))

    bc = base_m["calmar"]
    oc = ovl_m["calmar"]
    if bc != 0 and not math.isnan(bc):
        d_calmar = (oc / bc - 1) * 100
    else:
        d_calmar = None

    return {
        "n_trades": len(win_sorted),
        "n_fired": n_fired,
        "fire_rate": n_fired / max(1, len(win_sorted)),
        "pool_size_at_start": pool_size_at_window_start,
        "baseline_calmar": bc,
        "overlay_calmar": oc,
        "calmar_delta_pct": d_calmar,
        "baseline_return_pct": base_m["total_return_pct"],
        "overlay_return_pct": ovl_m["total_return_pct"],
        "abs_return_delta_pct": ovl_m["total_return_pct"] - base_m["total_return_pct"],
        "baseline_dd_pct": base_m["max_dd_pct"],
        "overlay_dd_pct": ovl_m["max_dd_pct"],
        "abs_dd_delta_pct": ovl_m["max_dd_pct"] - base_m["max_dd_pct"],
        "baseline_sharpe": base_m["sharpe"],
        "overlay_sharpe": ovl_m["sharpe"],
        "sharpe_delta": ovl_m["sharpe"] - base_m["sharpe"],
        "baseline_total_pnl_usd": base_total_pnl,
        "overlay_total_pnl_usd": ovl_total_pnl,
        "abs_pnl_delta_usd": ovl_total_pnl - base_total_pnl,
        "trade_helps": trade_helps,
        "trade_hurts": trade_hurts,
        "trade_neutral": trade_neutral,
        "max_trade_diff_usd": float(max(abs_trade_pnl_diffs)) if abs_trade_pnl_diffs else 0.0,
        "min_trade_diff_usd": float(min(abs_trade_pnl_diffs)) if abs_trade_pnl_diffs else 0.0,
    }


def build_windows(enriched: list[dict], step_months: int):
    last_eb = int(enriched[-1]["entry_bar"])
    window_h = WINDOW_MONTHS * HOURS_PER_MONTH
    step_h = step_months * HOURS_PER_MONTH
    total_hours = last_eb + 1
    starts = list(range(0, max(1, total_hours - window_h + 1), step_h))
    out = []
    for ws in starts:
        we = ws + window_h
        win = [t for t in enriched if ws <= int(t["entry_bar"]) < we]
        if len(win) < 20:
            continue
        history = [t for t in enriched if int(t["entry_bar"]) < ws]
        out.append((ws, we, win, history))
    return out


def summarise(deltas: list[float]) -> dict:
    if not deltas:
        return {}
    arr = np.array(deltas, dtype=np.float64)
    return {
        "n": int(arr.size),
        "mean": float(arr.mean()),
        "median": float(np.median(arr)),
        "stdev": float(arr.std(ddof=1)) if arr.size > 1 else 0.0,
        "min": float(arr.min()),
        "max": float(arr.max()),
        "n_pos": int((arr > 0).sum()),
        "n_neg": int((arr < 0).sum()),
        "se_mean": (float(arr.std(ddof=1)) / math.sqrt(arr.size)) if arr.size > 1 else 0.0,
    }


def fmt(x, d=1):
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return "n/a"
    return f"{x:.{d}f}"


# ---------------------------------------------------------------------------
# Issue 8: full-sample (60 month) overlay vs baseline
# ---------------------------------------------------------------------------
def full_sample_walkforward(enriched: list[dict], fill_model: str = "limit_at_threshold") -> dict:
    """Apply walk-forward expanding-window VEL95 overlay across the full
    60-month enriched trade list. Returns full-sample metrics for baseline
    and overlay (single window, no slicing)."""
    by_entry = sorted(enriched, key=lambda t: int(t["entry_bar"]))
    pool_exits = []
    pool_vels = []
    overlay_trades = []
    n_fired = 0
    base_total = 0.0
    ovl_total = 0.0
    for t in by_entry:
        eb = int(t["entry_bar"])
        # Build threshold from prior winners whose exit precedes this entry
        if pool_vels:
            ev = np.array(pool_exits, dtype=np.int64)
            vv = np.array(pool_vels, dtype=np.float64)
            mask = ev <= eb
            hist = vv[mask]
        else:
            hist = np.array([], dtype=np.float64)
        if hist.size >= 20:
            thr = float(np.quantile(hist, QUANTILE))
        else:
            thr = float("inf")
        new_pnl, fired, fb = overlay_one_action(t, thr, fill_model, "A-FULL")
        if fired:
            n_fired += 1
        overlay_trades.append({**t, "ovl_pnl": float(new_pnl)})
        base_total += float(t["pnl"])
        ovl_total += float(new_pnl)
        v = t.get("velocity_px_per_bar")
        if v is not None and t["mfe_price"] > 0:
            pool_exits.append(int(t["exit_bar"]))
            pool_vels.append(float(v))

    base_eq = build_equity_curve(by_entry, START_TS, CAPITAL, pnl_key="pnl")
    ovl_eq = build_equity_curve(overlay_trades, START_TS, CAPITAL, pnl_key="ovl_pnl")
    base_m = metrics(base_eq, CAPITAL)
    ovl_m = metrics(ovl_eq, CAPITAL)
    return {
        "n_trades": len(by_entry),
        "n_fired": n_fired,
        "baseline": base_m,
        "overlay": ovl_m,
        "baseline_total_pnl_usd": float(base_total),
        "overlay_total_pnl_usd": float(ovl_total),
        "abs_pnl_delta_usd": float(ovl_total - base_total),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print("Loading 60-month trades from", TRADES_PATH)
    raw = json.load(open(TRADES_PATH))
    print(f"  raw: {len(raw)}")

    # Coerce
    coerced = []
    for tr in raw:
        c = dict(tr)
        for k in ("pnl", "funding_cost", "margin_usd", "entry_price", "exit_price",
                  "entry_fee", "exit_fee"):
            if k in c and isinstance(c[k], str):
                c[k] = float(c[k])
        coerced.append(c)

    print("Enriching (this hits OHLC)...")
    enriched = []
    missing = 0
    for tr in coerced:
        e = enrich_trade(tr, START_TS)
        if e is None:
            missing += 1
            continue
        enriched.append(e)
    enriched.sort(key=lambda t: int(t["entry_bar"]))
    print(f"  enriched: {len(enriched)}  missing: {missing}")

    # ----- Re-run Gate 2 windows with extra metrics (limit fill, A-FULL, VEL95) -----
    print("\n[REBASE] re-running 17 Gate 2 windows with full metrics...")
    windows_3mo = build_windows(enriched, STEP_MONTHS)
    base_results = []
    for ws, we, win, hist in windows_3mo:
        r = run_window(win, hist, QUANTILE, "limit_at_threshold", "A-FULL")
        r["window_start_dt"] = (START_TS + pd.Timedelta(hours=ws)).isoformat()
        r["window_end_dt"] = (START_TS + pd.Timedelta(hours=we)).isoformat()
        base_results.append(r)
        print(f"  {r['window_start_dt'][:10]} n={r['n_trades']:3d} fire={r['n_fired']:3d} "
              f"poolN={r['pool_size_at_start']:3d} ΔCalmar={fmt(r['calmar_delta_pct'])}% "
              f"Δ$={r['abs_pnl_delta_usd']:+.0f}")

    delta_calmar = [r["calmar_delta_pct"] for r in base_results if r["calmar_delta_pct"] is not None]
    summary_calmar = summarise(delta_calmar)
    print(f"\n[REBASE] mean ΔCalmar={summary_calmar['mean']:+.1f}% median={summary_calmar['median']:+.1f}%")

    # ----- Issue 1: Warmup contamination -----
    print("\n[ISSUE 1] Warmup contamination — pool size cutoffs")
    issue1 = {}
    for cutoff in (50, 100, 200, 300):
        survivors = [r for r in base_results if r["pool_size_at_start"] >= cutoff
                     and r["calmar_delta_pct"] is not None]
        if survivors:
            arr = np.array([r["calmar_delta_pct"] for r in survivors])
            issue1[cutoff] = {
                "n_windows": len(survivors),
                "mean": float(arr.mean()),
                "median": float(np.median(arr)),
                "n_pos": int((arr > 0).sum()),
            }
            print(f"  cutoff>={cutoff:3d}: n={len(survivors):2d}  mean={arr.mean():+.1f}%  med={np.median(arr):+.1f}%  pos={int((arr>0).sum())}")
        else:
            issue1[cutoff] = {"n_windows": 0}

    # ----- Issue 2: Sharpe / abs $ / trade-level views -----
    print("\n[ISSUE 2] Multiple metrics view")
    deltas_return = [r["abs_return_delta_pct"] for r in base_results]
    deltas_dd = [r["abs_dd_delta_pct"] for r in base_results]
    deltas_sharpe = [r["sharpe_delta"] for r in base_results]
    deltas_pnl = [r["abs_pnl_delta_usd"] for r in base_results]
    issue2 = {
        "calmar_pct": summarise(delta_calmar),
        "abs_return_pp": summarise(deltas_return),
        "abs_dd_pp": summarise(deltas_dd),
        "sharpe_pts": summarise(deltas_sharpe),
        "abs_pnl_usd": summarise(deltas_pnl),
        "trade_helps_total": int(sum(r["trade_helps"] for r in base_results)),
        "trade_hurts_total": int(sum(r["trade_hurts"] for r in base_results)),
        "trade_neutral_total": int(sum(r["trade_neutral"] for r in base_results)),
    }
    for name, s in (("Calmar%", issue2["calmar_pct"]),
                    ("AbsRetΔpp", issue2["abs_return_pp"]),
                    ("AbsDDΔpp", issue2["abs_dd_pp"]),
                    ("SharpeΔ", issue2["sharpe_pts"]),
                    ("Δ$", issue2["abs_pnl_usd"])):
        print(f"  {name:12s}: mean={s['mean']:+8.2f} median={s['median']:+8.2f} pos/n={s['n_pos']}/{s['n']}")
    print(f"  trade-level: helps={issue2['trade_helps_total']} hurts={issue2['trade_hurts_total']} neutral={issue2['trade_neutral_total']}")

    # ----- Issue 3: 366% outlier window -----
    print("\n[ISSUE 3] +366% outlier window")
    outlier = max(base_results, key=lambda r: r["calmar_delta_pct"] if r["calmar_delta_pct"] is not None else -1e18)
    issue3 = {
        "window_start": outlier["window_start_dt"][:10],
        "window_end": outlier["window_end_dt"][:10],
        "n_trades": outlier["n_trades"],
        "n_fired": outlier["n_fired"],
        "baseline_calmar": outlier["baseline_calmar"],
        "overlay_calmar": outlier["overlay_calmar"],
        "calmar_delta_pct": outlier["calmar_delta_pct"],
        "baseline_return_pct": outlier["baseline_return_pct"],
        "overlay_return_pct": outlier["overlay_return_pct"],
        "abs_return_delta_pp": outlier["abs_return_delta_pct"],
        "abs_pnl_delta_usd": outlier["abs_pnl_delta_usd"],
        "max_trade_diff_usd": outlier["max_trade_diff_usd"],
        "min_trade_diff_usd": outlier["min_trade_diff_usd"],
        "trade_helps": outlier["trade_helps"],
        "trade_hurts": outlier["trade_hurts"],
    }
    for k, v in issue3.items():
        print(f"  {k}: {v}")

    # ----- Issue 4: Fire rate vs delta -----
    print("\n[ISSUE 4] Fire rate vs window outcome")
    issue4_rows = [
        {
            "window": r["window_start_dt"][:10],
            "n_trades": r["n_trades"],
            "n_fired": r["n_fired"],
            "fire_rate": r["fire_rate"],
            "calmar_delta_pct": r["calmar_delta_pct"],
            "abs_pnl_delta_usd": r["abs_pnl_delta_usd"],
        }
        for r in base_results
    ]
    # Correlate fire rate with delta
    fr = np.array([r["fire_rate"] for r in base_results])
    cd = np.array([r["calmar_delta_pct"] if r["calmar_delta_pct"] is not None else 0 for r in base_results])
    if fr.std() > 0 and cd.std() > 0:
        fr_corr = float(np.corrcoef(fr, cd)[0, 1])
    else:
        fr_corr = None
    low_fire = [r for r in base_results if r["n_fired"] < 5]
    issue4 = {
        "rows": issue4_rows,
        "fire_rate_calmar_corr": fr_corr,
        "n_low_fire_windows": len(low_fire),
        "low_fire_mean_delta": float(np.mean([r["calmar_delta_pct"] or 0 for r in low_fire])) if low_fire else None,
    }
    print(f"  fire_rate vs ΔCalmar Pearson: {fr_corr}")
    print(f"  windows with <5 fires: {len(low_fire)}")

    # ----- Issue 5: open-or-threshold fill -----
    print("\n[ISSUE 5] Re-running with open_or_threshold fill model...")
    issue5_results = []
    for ws, we, win, hist in windows_3mo:
        r = run_window(win, hist, QUANTILE, "open_or_threshold", "A-FULL")
        r["window_start_dt"] = (START_TS + pd.Timedelta(hours=ws)).isoformat()
        issue5_results.append(r)
    issue5_calmar = [r["calmar_delta_pct"] for r in issue5_results if r["calmar_delta_pct"] is not None]
    issue5_pnl = [r["abs_pnl_delta_usd"] for r in issue5_results]
    issue5 = {
        "calmar_pct": summarise(issue5_calmar),
        "abs_pnl_usd": summarise(issue5_pnl),
    }
    print(f"  open_or_threshold: Calmar mean={issue5['calmar_pct']['mean']:+.1f}% med={issue5['calmar_pct']['median']:+.1f}% pos={issue5['calmar_pct']['n_pos']}/{issue5['calmar_pct']['n']}")
    print(f"  open_or_threshold: Δ$ mean={issue5['abs_pnl_usd']['mean']:+.0f} median={issue5['abs_pnl_usd']['median']:+.0f}")

    # ----- Issue 6: A-HALFLOCK and A-25 -----
    print("\n[ISSUE 6] Action variants A-HALFLOCK and A-25 (limit fill, VEL95)...")
    issue6 = {}
    for action in ("A-25", "A-HALFLOCK"):
        rs = []
        for ws, we, win, hist in windows_3mo:
            r = run_window(win, hist, QUANTILE, "limit_at_threshold", action)
            rs.append(r)
        cd = [r["calmar_delta_pct"] for r in rs if r["calmar_delta_pct"] is not None]
        pn = [r["abs_pnl_delta_usd"] for r in rs]
        issue6[action] = {
            "calmar_pct": summarise(cd),
            "abs_pnl_usd": summarise(pn),
        }
        s = issue6[action]["calmar_pct"]
        print(f"  {action}: mean={s['mean']:+.1f}% med={s['median']:+.1f}% pos={s['n_pos']}/{s['n']}")

    # ----- Issue 7: 1-month stepping (more windows) -----
    print("\n[ISSUE 7] Re-stepping at 1-month intervals for ~50 windows...")
    windows_1mo = build_windows(enriched, 1)
    print(f"  built {len(windows_1mo)} windows")
    iss7_results = []
    for ws, we, win, hist in windows_1mo:
        r = run_window(win, hist, QUANTILE, "limit_at_threshold", "A-FULL")
        iss7_results.append(r)
    iss7_calmar = [r["calmar_delta_pct"] for r in iss7_results if r["calmar_delta_pct"] is not None]
    iss7_pnl = [r["abs_pnl_delta_usd"] for r in iss7_results]
    issue7 = {
        "n_windows": len(iss7_results),
        "calmar_pct": summarise(iss7_calmar),
        "abs_pnl_usd": summarise(iss7_pnl),
    }
    s = issue7["calmar_pct"]
    ci_low = s["mean"] - 1.96 * s["se_mean"]
    ci_high = s["mean"] + 1.96 * s["se_mean"]
    issue7["calmar_pct_ci95_low"] = ci_low
    issue7["calmar_pct_ci95_high"] = ci_high
    print(f"  n={s['n']}  mean={s['mean']:+.1f}% med={s['median']:+.1f}% pos={s['n_pos']}/{s['n']}  95%CI=[{ci_low:+.1f}%, {ci_high:+.1f}%]")
    print(f"  abs $: mean={issue7['abs_pnl_usd']['mean']:+.0f} median={issue7['abs_pnl_usd']['median']:+.0f}")

    # ----- Issue 8: full-sample 60-month single-window view -----
    print("\n[ISSUE 8] Full-sample (60 month) overlay vs baseline (no slicing)...")
    full_lim = full_sample_walkforward(enriched, "limit_at_threshold")
    full_oot = full_sample_walkforward(enriched, "open_or_threshold")
    issue8 = {
        "limit_fill": full_lim,
        "open_or_threshold_fill": full_oot,
    }
    for label, r in (("limit", full_lim), ("open_or_thr", full_oot)):
        print(f"  {label}: baseline ret={r['baseline']['total_return_pct']:.1f}% DD={r['baseline']['max_dd_pct']:.1f}% Calmar={r['baseline']['calmar']:.2f}")
        print(f"  {label}: overlay  ret={r['overlay']['total_return_pct']:.1f}% DD={r['overlay']['max_dd_pct']:.1f}% Calmar={r['overlay']['calmar']:.2f}")
        print(f"  {label}: Δ$={r['abs_pnl_delta_usd']:+.0f}  fired={r['n_fired']}/{r['n_trades']}")

    # ----- Persist all data -----
    out = {
        "issue1_warmup": issue1,
        "issue2_metrics": issue2,
        "issue3_outlier": issue3,
        "issue4_firerate": issue4,
        "issue5_open_fill": issue5,
        "issue6_actions": issue6,
        "issue7_monthly_step": issue7,
        "issue8_full_sample": issue8,
        "base_results": base_results,
    }
    out_path = ROOT / "research/profit_lockin_autopsy_results.json"
    out_path.write_text(json.dumps(out, indent=2, default=str))
    print(f"\nWrote {out_path}")
    return out


if __name__ == "__main__":
    main()
