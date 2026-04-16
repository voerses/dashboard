"""
Profit Lock-in Overlay — Gate 1 VERIFICATION PASS
==================================================
Stress-test the claimed VEL95 x A-FULL improvement on s523c_growth from
profit_lockin_gate1.py / profit_lockin_gate1_report.md.

Tests applied:
  1) Out-of-sample velocity threshold (in-sample first 50% of trades trains
     the threshold, applied to second 50%).
  2) Walk-forward (expanding window) threshold: each new trade uses the 95th
     percentile computed only over trades whose EXIT bar precedes its ENTRY bar.
  3) Realistic intrabar fill models:
       - optimistic (matches original sim): fill at bar HIGH (the move that
         triggered the velocity test).
       - midpoint: fill at (high + close) / 2.
       - close-only: fill at bar CLOSE (most pessimistic — assumes you can
         only act on confirmed close, then exit at... close).
       - limit-at-prev-close + 1.33%: simulate placing a limit at the
         threshold price the bar BEFORE; if the bar's high reaches it, fill at
         that limit price (= entry * (1 + threshold)). If close > limit, fill
         at limit (better fill not allowed). This is the most realistic.

We DO NOT modify research/profit_lockin_gate1.py. We import its helpers.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from collections import defaultdict

import numpy as np
import pandas as pd

# Reuse helpers from the original script (no edits to it).
sys.path.insert(0, str(Path("/workspace/crypto_backtest/research")))
from profit_lockin_gate1 import (  # type: ignore
    BASELINES,
    enrich_trade,
    slice_for_trade,
    build_equity_curve,
    metrics,
)


def winners_velocities(enriched: list[dict]) -> np.ndarray:
    return np.array([
        t["velocity_px_per_bar"]
        for t in enriched
        if t["velocity_px_per_bar"] is not None and t["mfe_price"] > 0
    ])


# ---------------------------------------------------------------------------
# Per-trade overlay sim with explicit threshold and fill model
# ---------------------------------------------------------------------------
def overlay_one(t: dict, start_ts: pd.Timestamp, vth: float, min_px: float, fill_model: str) -> tuple[float, bool, int | None]:
    """Return (overlay_pnl, fired, fire_bar)."""
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

    bars = slice_for_trade(token, start_ts, eb, xb)
    if bars is None or len(bars) == 0:
        return base_pnl, False, None

    highs = bars["high"].to_numpy()
    lows = bars["low"].to_numpy()
    closes = bars["close"].to_numpy()

    if direction == 1:
        fav_hi = (highs - entry_px) / entry_px
        fav_cl = (closes - entry_px) / entry_px
    else:
        fav_hi = (entry_px - lows) / entry_px
        fav_cl = (entry_px - closes) / entry_px

    fire_bar = None
    for i in range(len(fav_hi)):
        v = fav_hi[i] / (i + 1)
        if v >= vth and fav_hi[i] >= min_px:
            fire_bar = i
            break
    if fire_bar is None:
        return base_pnl, False, None

    # Fill price models:
    if fill_model == "optimistic":
        # Original behavior: lock in at bar high (the favorable extreme).
        lock = fav_hi[fire_bar]
    elif fill_model == "midpoint":
        # (high + close) / 2.
        lock = 0.5 * fav_hi[fire_bar] + 0.5 * fav_cl[fire_bar]
        if lock < 0:
            lock = max(0.0, fav_cl[fire_bar])
    elif fill_model == "close":
        # End-of-bar close — most pessimistic if price reverts.
        lock = fav_cl[fire_bar]
    elif fill_model == "limit_at_threshold":
        # Place a limit at threshold-price * entry the prior bar.
        # Fill iff bar high >= threshold; price = threshold (no slippage favor).
        lock = max(vth * (fire_bar + 1), 0.0)  # = threshold velocity * bars elapsed
        # Cap at actual high to avoid fictitious fills above the bar
        lock = min(lock, fav_hi[fire_bar])
    else:
        raise ValueError(f"unknown fill_model: {fill_model}")

    # Convert price-move delta to pnl delta.
    new_pnl = base_pnl + margin * lev * (lock - float(t["realized_price"]))
    return new_pnl, True, fire_bar


def run_overlay(
    enriched: list[dict],
    start_ts: pd.Timestamp,
    capital: float,
    vth: float | None,                    # static threshold; if None use per-trade walkforward
    min_px: float = 0.005,
    fill_model: str = "optimistic",
    walkforward: bool = False,
) -> dict:
    out_trades = []
    n_fired = 0

    if walkforward:
        # Sort by entry time so that "prior" velocities are well defined.
        ord_idx = sorted(range(len(enriched)), key=lambda i: int(enriched[i]["entry_bar"]))
        # Precompute (exit_bar, velocity) for winners only.
        prior_pool: list[tuple[int, float]] = []
        # We'll iterate in entry order. For each new trade, prune prior_pool to
        # entries whose exit_bar <= this entry_bar (no future leakage), then take p95.
        # Build per-trade thresholds.
        thresholds: dict[int, float] = {}
        # Pre-sort prior pool by exit_bar for efficient prefix percentiles.
        exits = []
        vels = []
        for i in sorted(range(len(enriched)), key=lambda i: int(enriched[i]["exit_bar"])):
            v = enriched[i].get("velocity_px_per_bar")
            if v is None or enriched[i]["mfe_price"] <= 0:
                continue
            exits.append(int(enriched[i]["exit_bar"]))
            vels.append(float(v))
        exits = np.array(exits)
        vels = np.array(vels)
        # For each trade in entry order:
        for i in ord_idx:
            entry_bar = int(enriched[i]["entry_bar"])
            mask = exits <= entry_bar
            if mask.sum() >= 20:  # need enough history
                thr = float(np.quantile(vels[mask], 0.95))
            else:
                thr = float("inf")  # no firing if insufficient history
            thresholds[i] = thr

        for i, t in enumerate(enriched):
            v_use = thresholds.get(i, float("inf"))
            new_pnl, fired, fb = overlay_one(t, start_ts, v_use, min_px, fill_model)
            if fired:
                n_fired += 1
            out_trades.append({**t, "ovl_pnl": new_pnl, "ovl_fired": fired, "ovl_bar": fb})
    else:
        for t in enriched:
            new_pnl, fired, fb = overlay_one(t, start_ts, vth, min_px, fill_model)
            if fired:
                n_fired += 1
            out_trades.append({**t, "ovl_pnl": new_pnl, "ovl_fired": fired, "ovl_bar": fb})

    eq = build_equity_curve(out_trades, start_ts, capital, pnl_key="ovl_pnl")
    m = metrics(eq, capital)
    return {"n_fired": n_fired, **m}


def main():
    cfg = BASELINES["s523c"]
    trades_raw = json.load(open(cfg["trades"]))
    start = cfg["start"]
    capital = cfg["capital"]

    print(f"Enriching {len(trades_raw)} trades...")
    enriched = []
    for tr in trades_raw:
        e = enrich_trade(tr, start)
        if e is not None:
            enriched.append(e)
    print(f"  enriched: {len(enriched)}")

    # Sort chronologically by entry_bar
    enriched.sort(key=lambda t: int(t["entry_bar"]))

    # Baseline metrics (reconstructed)
    base_eq = build_equity_curve(enriched, start, capital, pnl_key="pnl")
    base_m = metrics(base_eq, capital)
    print(f"Baseline (reconstructed): {base_m}")

    # Full-sample VEL95 (matches original report's threshold)
    full_v = winners_velocities(enriched)
    full_v95 = float(np.quantile(full_v, 0.95))
    print(f"\nFull-sample winner velocity p95 = {full_v95:.5f} (~{full_v95*100:.3f}% per bar)")

    # ----- Section A: Replicate original (optimistic fill, full-sample threshold) -----
    print("\n=== A. Replicate original (full-sample VEL95, optimistic fill) ===")
    repl = run_overlay(enriched, start, capital, vth=full_v95, fill_model="optimistic")
    print(f"  {repl}")

    # ----- Section B: Out-of-sample 50/50 split -----
    print("\n=== B. Out-of-sample 50/50 split ===")
    n = len(enriched)
    half = n // 2
    is_trades = enriched[:half]
    oos_trades = enriched[half:]
    is_v = winners_velocities(is_trades)
    is_v95 = float(np.quantile(is_v, 0.95)) if len(is_v) else float("inf")
    oos_v = winners_velocities(oos_trades)
    oos_v95 = float(np.quantile(oos_v, 0.95)) if len(oos_v) else float("inf")
    print(f"  in-sample (first half) p95: {is_v95:.5f}")
    print(f"  out-of-sample p95:          {oos_v95:.5f}")

    # Baseline on OOS slice only
    oos_base_eq = build_equity_curve(oos_trades, start, capital, pnl_key="pnl")
    oos_base_m = metrics(oos_base_eq, capital)
    print(f"  OOS baseline (recomputed):  {oos_base_m}")

    # Apply IS threshold to OOS trades, optimistic fill
    oos_overlay = run_overlay(oos_trades, start, capital, vth=is_v95, fill_model="optimistic")
    print(f"  OOS overlay (IS threshold, optimistic fill): {oos_overlay}")

    # And with limit-at-threshold fill
    oos_overlay_limit = run_overlay(oos_trades, start, capital, vth=is_v95, fill_model="limit_at_threshold")
    print(f"  OOS overlay (IS threshold, limit fill):      {oos_overlay_limit}")

    # And midpoint fill
    oos_overlay_mid = run_overlay(oos_trades, start, capital, vth=is_v95, fill_model="midpoint")
    print(f"  OOS overlay (IS threshold, midpoint fill):   {oos_overlay_mid}")

    # ----- Section C: Walk-forward expanding-window threshold -----
    print("\n=== C. Walk-forward (expanding) threshold ===")
    wf_opt = run_overlay(enriched, start, capital, vth=None, fill_model="optimistic", walkforward=True)
    wf_lim = run_overlay(enriched, start, capital, vth=None, fill_model="limit_at_threshold", walkforward=True)
    wf_mid = run_overlay(enriched, start, capital, vth=None, fill_model="midpoint", walkforward=True)
    print(f"  walk-forward, optimistic fill: {wf_opt}")
    print(f"  walk-forward, midpoint fill:   {wf_mid}")
    print(f"  walk-forward, limit fill:      {wf_lim}")

    # ----- Section D: Realistic-fill on FULL sample with full-sample threshold -----
    print("\n=== D. Realistic fill, full-sample threshold (isolates fill bias from threshold bias) ===")
    full_mid = run_overlay(enriched, start, capital, vth=full_v95, fill_model="midpoint")
    full_lim = run_overlay(enriched, start, capital, vth=full_v95, fill_model="limit_at_threshold")
    full_close = run_overlay(enriched, start, capital, vth=full_v95, fill_model="close")
    print(f"  full-sample threshold, optimistic: {repl}")
    print(f"  full-sample threshold, midpoint:   {full_mid}")
    print(f"  full-sample threshold, limit:      {full_lim}")
    print(f"  full-sample threshold, close-only: {full_close}")

    # Build a JSON dump for the report
    all_results = {
        "baseline_full": base_m,
        "baseline_oos_only": oos_base_m,
        "full_sample_threshold": full_v95,
        "in_sample_threshold": is_v95,
        "oos_observed_threshold": oos_v95,
        "A_replicate": repl,
        "B_oos_optimistic": oos_overlay,
        "B_oos_limit": oos_overlay_limit,
        "B_oos_midpoint": oos_overlay_mid,
        "C_walkforward_optimistic": wf_opt,
        "C_walkforward_limit": wf_lim,
        "C_walkforward_midpoint": wf_mid,
        "D_full_midpoint": full_mid,
        "D_full_limit": full_lim,
        "D_full_close": full_close,
        "n_trades_total": n,
        "n_trades_oos": len(oos_trades),
    }

    out_path = Path("/workspace/crypto_backtest/research/profit_lockin_verification_results.json")
    out_path.write_text(json.dumps(all_results, indent=2, default=str))
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
