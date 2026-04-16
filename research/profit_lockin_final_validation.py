"""
Profit Lock-in Overlay — Final Validation
==========================================
Resolves Mission G one way or the other after the autopsy revealed that the
prior A-HALFLOCK result was a recompute artifact (the autopsy's "halflock"
implementation was structurally non-decreasing because it never simulated the
new stop walking forward — it just added max(0, 0.5*lock - realized) to PnL).

This script applies the CORRECT A-HALFLOCK semantics from Gate 1's
`simulate_overlay`:
    - On trigger fire, raise stop to entry + 0.5 * lock_fav (longs)
    - Walk forward bar-by-bar from fire_bar+1 to exit_bar
    - If a later bar's adverse excursion touches the new stop, exit there
    - Otherwise the original exit fires (realized unchanged)
This can HURT a trade (early stop-out before a bigger move) so the 17/17
positive-$ result will not survive correct simulation if it was an artifact.

Tests:
  1) Slippage robustness (0/25/50/100 bps) on full 60-month walk-forward
     A-HALFLOCK with limit-fill triggers + correct stop semantics.
  2) Data-horizon restriction: 1-month-step rolling windows where window_start
     >= 2023-06-01. Median ΔCalmar and positive-$ windows on filtered set,
     50bps slippage.
  3) All variants (A-FULL, A-HALFLOCK, A-25, A-50, A-75) at 50bps,
     full-60mo walk-forward.

Reuses gate2_out/s523c_growth_60mo_50k_trades.json via Gate 1's enrich_trade.
Run: /workspace/venv/bin/python research/profit_lockin_final_validation.py
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

TRADES_PATH = ROOT / "research/gate2_out/s523c_growth_60mo_50k_trades.json"
START_TS = pd.Timestamp("2021-04-05T16:00:00+00:00")
CAPITAL = 50_000.0
WINDOW_MONTHS = 6
HOURS_PER_MONTH = 24 * 30
QUANTILE = 0.95
MIN_PX = 0.005
HORIZON_FILTER = pd.Timestamp("2023-06-01T00:00:00+00:00")


# ---------------------------------------------------------------------------
# Per-trade overlay with CORRECT stop semantics + limit fill + slippage
# ---------------------------------------------------------------------------
def overlay_one_correct(
    t: dict,
    vth: float,
    action: str,
    slippage_bps: float = 0.0,
) -> tuple[float, bool, bool]:
    """Return (overlay_pnl, fired, stop_modified_exit).

    fired = velocity trigger fired
    stop_modified_exit = trade exited via the overlay-modified stop, not its
        original exit. Slippage applies only to these trades.

    A-FULL closes at lock => slippage applies whenever fired.
    A-HALFLOCK / A-25 / A-50 / A-75 only get slippage if the new stop actually
    fired (price walked back to the new stop level before the original exit).
    """
    base_pnl = float(t["pnl"])
    lev = t.get("leverage_inferred")
    if lev is None:
        return base_pnl, False, False
    token = t["token"]
    eb = int(t["entry_bar"])
    xb = int(t["exit_bar"])
    direction = int(t["direction"])
    entry_px = float(t["entry_price"])
    margin = float(t["margin_usd"])

    bars = slice_for_trade(token, START_TS, eb, xb)
    if bars is None or len(bars) == 0:
        return base_pnl, False, False

    highs = bars["high"].to_numpy()
    lows = bars["low"].to_numpy()
    closes = bars["close"].to_numpy()

    if direction == 1:
        fav_hi = (highs - entry_px) / entry_px
        fav_cl = (closes - entry_px) / entry_px
        adv = (lows - entry_px) / entry_px  # negative when underwater
    else:
        fav_hi = (entry_px - lows) / entry_px
        fav_cl = (entry_px - closes) / entry_px
        adv = (entry_px - highs) / entry_px

    # Find first velocity-trigger fire
    fire_bar = None
    for i in range(len(fav_hi)):
        v = fav_hi[i] / (i + 1)
        if v >= vth and fav_hi[i] >= MIN_PX:
            fire_bar = i
            break
    if fire_bar is None:
        return base_pnl, False, False

    # Limit-fill price (price-fraction units)
    lock = max(vth * (fire_bar + 1), 0.0)
    lock = min(lock, fav_hi[fire_bar])
    realized = float(t["realized_price"])
    atr_pct = t.get("atr_pct") or 0.01

    stop_modified_exit = False

    def walk_forward(stop_level: float, trail_atr_mult: float | None = None) -> tuple[float, bool]:
        """Walk fire_bar+1 .. end, applying stop. stop_level is a price-move
        threshold (fraction of entry, can be negative). Returns
        (new_realized_price_fraction, stop_hit_bool)."""
        running_fav = lock
        for j in range(fire_bar + 1, len(fav_hi)):
            if fav_hi[j] > running_fav:
                running_fav = fav_hi[j]
            sl = stop_level
            if trail_atr_mult is not None:
                trail_level = running_fav - trail_atr_mult * atr_pct
                sl = max(sl, trail_level)
            if adv[j] <= sl:
                return sl, True
        return realized, False  # original exit

    if action == "A-FULL":
        # Close 100% at lock immediately
        new_price_return = lock
        stop_modified_exit = True  # exit happened at fire bar via overlay
    elif action == "A-HALFLOCK":
        new_price_return, stop_hit = walk_forward(0.5 * lock, None)
        stop_modified_exit = stop_hit
    elif action == "A-50":
        # 50% closed at lock, 50% rides with stop at breakeven (0.0)
        rem, stop_hit = walk_forward(0.0, None)
        new_price_return = 0.5 * lock + 0.5 * rem
        stop_modified_exit = stop_hit  # close-half at fire bar always happens; rem may stop or not
    elif action == "A-75":
        # 75% closed at lock, 25% rides with stop at entry+1*ATR
        rem, stop_hit = walk_forward(atr_pct, None)
        new_price_return = 0.75 * lock + 0.25 * rem
        stop_modified_exit = stop_hit
    elif action == "A-25":
        # 25% closed at lock, 75% rides with tightened 1*ATR trailing stop
        rem, stop_hit = walk_forward(-1.0, 1.0)
        new_price_return = 0.25 * lock + 0.75 * rem
        stop_modified_exit = stop_hit
    else:
        raise ValueError(action)

    new_pnl = base_pnl + margin * lev * (new_price_return - realized)

    # Slippage: subtract slippage_bps * notional from trades that exited via
    # the overlay-modified stop. Notional = margin * lev.
    if slippage_bps > 0 and stop_modified_exit:
        notional = margin * lev
        new_pnl -= (slippage_bps / 10000.0) * notional

    return new_pnl, True, stop_modified_exit


# ---------------------------------------------------------------------------
# Walk-forward driver (full 60mo OR within a window)
# ---------------------------------------------------------------------------
def walkforward_run(
    trades: list[dict],
    history_trades: list[dict] | None,
    action: str,
    slippage_bps: float = 0.0,
) -> dict:
    """Apply walk-forward expanding-window VEL95 threshold across `trades`.

    history_trades: optional pre-window trades that seed the threshold pool.
    Returns full metrics (baseline vs overlay) for `trades`.
    """
    by_entry = sorted(trades, key=lambda t: int(t["entry_bar"]))

    pool_exits: list[int] = []
    pool_vels: list[float] = []
    if history_trades:
        for t in history_trades:
            v = t.get("velocity_px_per_bar")
            if v is None or t["mfe_price"] <= 0:
                continue
            pool_exits.append(int(t["exit_bar"]))
            pool_vels.append(float(v))

    overlay_trades = []
    n_fired = 0
    n_stop_modified = 0
    base_total_pnl = 0.0
    ovl_total_pnl = 0.0

    for t in by_entry:
        eb = int(t["entry_bar"])
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

        new_pnl, fired, stop_mod = overlay_one_correct(t, thr, action, slippage_bps)
        if fired:
            n_fired += 1
        if stop_mod:
            n_stop_modified += 1

        overlay_trades.append({**t, "ovl_pnl": float(new_pnl)})
        base_total_pnl += float(t["pnl"])
        ovl_total_pnl += float(new_pnl)

        v = t.get("velocity_px_per_bar")
        if v is not None and t["mfe_price"] > 0:
            pool_exits.append(int(t["exit_bar"]))
            pool_vels.append(float(v))

    base_eq = build_equity_curve(by_entry, START_TS, CAPITAL, pnl_key="pnl")
    ovl_eq = build_equity_curve(overlay_trades, START_TS, CAPITAL, pnl_key="ovl_pnl")
    base_m = metrics(base_eq, CAPITAL)
    ovl_m = metrics(ovl_eq, CAPITAL)

    bc = base_m["calmar"]
    oc = ovl_m["calmar"]
    if bc != 0 and not math.isnan(bc):
        d_calmar = (oc / bc - 1) * 100
    else:
        d_calmar = None

    return {
        "n_trades": len(by_entry),
        "n_fired": n_fired,
        "n_stop_modified": n_stop_modified,
        "baseline": base_m,
        "overlay": ovl_m,
        "baseline_total_pnl_usd": float(base_total_pnl),
        "overlay_total_pnl_usd": float(ovl_total_pnl),
        "abs_pnl_delta_usd": float(ovl_total_pnl - base_total_pnl),
        "calmar_delta_pct": d_calmar,
        "ret_delta_pp": ovl_m["total_return_pct"] - base_m["total_return_pct"],
        "dd_delta_pp": ovl_m["max_dd_pct"] - base_m["max_dd_pct"],
    }


def build_windows_1mo(enriched: list[dict]):
    last_eb = int(enriched[-1]["entry_bar"])
    window_h = WINDOW_MONTHS * HOURS_PER_MONTH
    step_h = HOURS_PER_MONTH
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


def summarise(arr_like) -> dict:
    arr = np.array([x for x in arr_like if x is not None], dtype=np.float64)
    if arr.size == 0:
        return {"n": 0}
    return {
        "n": int(arr.size),
        "mean": float(arr.mean()),
        "median": float(np.median(arr)),
        "stdev": float(arr.std(ddof=1)) if arr.size > 1 else 0.0,
        "min": float(arr.min()),
        "max": float(arr.max()),
        "n_pos": int((arr > 0).sum()),
    }


# ---------------------------------------------------------------------------
def main():
    print(f"Loading {TRADES_PATH}...")
    raw = json.load(open(TRADES_PATH))
    coerced = []
    for tr in raw:
        c = dict(tr)
        for k in ("pnl", "funding_cost", "margin_usd", "entry_price",
                  "exit_price", "entry_fee", "exit_fee"):
            if k in c and isinstance(c[k], str):
                c[k] = float(c[k])
        coerced.append(c)

    print("Enriching trades (OHLC + ATR + leverage inference)...")
    enriched = []
    missing = 0
    for tr in coerced:
        e = enrich_trade(tr, START_TS)
        if e is None:
            missing += 1
            continue
        enriched.append(e)
    enriched.sort(key=lambda t: int(t["entry_bar"]))
    print(f"  enriched: {len(enriched)} / missing: {missing}")

    results: dict = {}

    # ===================================================================
    # TEST 1 — Slippage sensitivity for A-HALFLOCK on full 60mo
    # ===================================================================
    print("\n[TEST 1] Slippage sensitivity — A-HALFLOCK full 60mo walk-forward")
    test1 = {}
    for slip in (0, 25, 50, 100):
        r = walkforward_run(enriched, history_trades=None, action="A-HALFLOCK", slippage_bps=slip)
        test1[f"slip_{slip}bps"] = r
        print(f"  {slip:3d} bps: ret={r['overlay']['total_return_pct']:7.2f}%  "
              f"DD={r['overlay']['max_dd_pct']:7.2f}%  "
              f"Calmar={r['overlay']['calmar']:6.2f}  "
              f"ΔCalmar={r['calmar_delta_pct']:+7.1f}%  "
              f"Δ$={r['abs_pnl_delta_usd']:+9.0f}  "
              f"fired={r['n_fired']}  stop_mod={r['n_stop_modified']}")

    base = test1["slip_0bps"]["baseline"]
    print(f"  baseline:    ret={base['total_return_pct']:7.2f}%  "
          f"DD={base['max_dd_pct']:7.2f}%  Calmar={base['calmar']:6.2f}")
    results["test1_slippage_halflock"] = test1

    # ===================================================================
    # TEST 2 — Data horizon restriction (windows starting >= 2023-06-01),
    # 1-month step, 6-month windows, A-HALFLOCK at 50 bps
    # ===================================================================
    print("\n[TEST 2] Data horizon restriction — 1mo step rolling, A-HALFLOCK @ 50bps")
    windows_1mo = build_windows_1mo(enriched)
    print(f"  total 1mo windows built: {len(windows_1mo)}")

    filtered_idx = []
    for i, (ws, we, win, hist) in enumerate(windows_1mo):
        ws_dt = START_TS + pd.Timedelta(hours=ws)
        if ws_dt >= HORIZON_FILTER:
            filtered_idx.append(i)
    print(f"  windows with start >= {HORIZON_FILTER.date()}: {len(filtered_idx)}")

    def run_set(indices, action, slip_bps):
        rs = []
        for i in indices:
            ws, we, win, hist = windows_1mo[i]
            r = walkforward_run(win, history_trades=hist, action=action, slippage_bps=slip_bps)
            r["window_start"] = (START_TS + pd.Timedelta(hours=ws)).isoformat()
            rs.append(r)
        return rs

    halflock_filtered = run_set(filtered_idx, "A-HALFLOCK", 50)
    halflock_unfiltered = run_set(list(range(len(windows_1mo))), "A-HALFLOCK", 50)

    def windows_summary(rs):
        d_calmar = [r["calmar_delta_pct"] for r in rs]
        d_pnl = [r["abs_pnl_delta_usd"] for r in rs]
        return {
            "calmar_delta_pct": summarise(d_calmar),
            "abs_pnl_delta_usd": summarise(d_pnl),
            "n_pos_pnl": int(sum(1 for x in d_pnl if x > 0)),
            "n_total": len(rs),
        }

    test2 = {
        "filtered_2023_06": windows_summary(halflock_filtered),
        "unfiltered": windows_summary(halflock_unfiltered),
        "horizon_filter": HORIZON_FILTER.isoformat(),
        "n_filtered": len(filtered_idx),
        "n_unfiltered": len(windows_1mo),
    }

    for label, s in (("filtered", test2["filtered_2023_06"]),
                     ("unfilt  ", test2["unfiltered"])):
        cd = s["calmar_delta_pct"]
        pn = s["abs_pnl_delta_usd"]
        print(f"  {label}: n={cd.get('n', 0)}  ΔCalmar mean={cd.get('mean', float('nan')):+6.1f}%  "
              f"med={cd.get('median', float('nan')):+6.1f}%  "
              f"pos${s['n_pos_pnl']}/{s['n_total']}  "
              f"meanΔ$={pn.get('mean', float('nan')):+8.0f}")
    results["test2_horizon"] = test2

    # ===================================================================
    # TEST 3 — Variant comparison at 50 bps slippage, full 60mo
    # ===================================================================
    print("\n[TEST 3] Variant comparison @ 50 bps slippage (full 60mo walk-forward)")
    test3 = {}
    variants = ["A-FULL", "A-HALFLOCK", "A-25", "A-50", "A-75"]
    for v in variants:
        r = walkforward_run(enriched, history_trades=None, action=v, slippage_bps=50)
        test3[v] = r
        print(f"  {v:11s}: ret={r['overlay']['total_return_pct']:7.2f}%  "
              f"DD={r['overlay']['max_dd_pct']:7.2f}%  "
              f"Calmar={r['overlay']['calmar']:6.2f}  "
              f"ΔCalmar={r['calmar_delta_pct']:+7.1f}%  "
              f"Δ$={r['abs_pnl_delta_usd']:+9.0f}  "
              f"fired={r['n_fired']}  stop_mod={r['n_stop_modified']}")
    base50 = test3["A-FULL"]["baseline"]
    print(f"  baseline:    ret={base50['total_return_pct']:7.2f}%  "
          f"DD={base50['max_dd_pct']:7.2f}%  Calmar={base50['calmar']:6.2f}")
    results["test3_variants_50bps"] = test3

    # ===================================================================
    # TEST 2b — also run filtered windows for any variant beating A-HALFLOCK
    #           in Test 3, so the report can show one alternative
    # ===================================================================
    print("\n[TEST 2b] Filtered windows for runner-up variant")
    # Pick the variant with highest Calmar at 50bps (excluding A-HALFLOCK)
    cand = [(v, test3[v]["overlay"]["calmar"]) for v in variants if v != "A-HALFLOCK"]
    cand.sort(key=lambda x: x[1], reverse=True)
    runner_up = cand[0][0]
    print(f"  runner_up by 50bps Calmar: {runner_up}")
    runner_filtered = run_set(filtered_idx, runner_up, 50)
    test2b = {
        "variant": runner_up,
        "filtered_2023_06": windows_summary(runner_filtered),
    }
    s = test2b["filtered_2023_06"]
    cd = s["calmar_delta_pct"]
    pn = s["abs_pnl_delta_usd"]
    print(f"  {runner_up} filtered: ΔCalmar mean={cd.get('mean', float('nan')):+6.1f}%  "
          f"med={cd.get('median', float('nan')):+6.1f}%  pos${s['n_pos_pnl']}/{s['n_total']}  "
          f"meanΔ$={pn.get('mean', float('nan')):+8.0f}")
    results["test2b_runner_up"] = test2b

    # ===================================================================
    # PERSIST
    # ===================================================================
    out_path = ROOT / "research/profit_lockin_final_validation_results.json"
    out_path.write_text(json.dumps(results, indent=2, default=str))
    print(f"\nWrote {out_path}")

    return results


if __name__ == "__main__":
    main()
