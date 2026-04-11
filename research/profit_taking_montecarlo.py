#!/usr/bin/env python3
"""
Monte Carlo profit-taking optimization per regime.

Loads s524g trades, reconstructs intra-trade MTM paths from 1h parquet data,
and simulates fixed TP, trailing stop, and partial TP+trail strategies on WINNERS only.

Uses the same unified-timeline approach as the simulator to correctly map
global_bar -> per-token local bars.
"""

import json
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

# ── Config ──────────────────────────────────────────────────────────────────
TRADES_PATH = "/tmp/bt_compound_fat_tail/s524g_hybrid_gate_51mo_150k_trades.json"
CACHE_DIR = Path("/workspace/crypto_backtest/data/perp/1h_cache")
BACKTEST_START = pd.Timestamp("2022-01-05")
LEVERAGE = 2.6
PRICE_TOL = 0.05  # 5% tolerance (entry_price includes slippage adjustment)

# Regime boundaries (by entry date)
REGIMES = {
    "2022_BEAR":        (pd.Timestamp("2022-01-01"), pd.Timestamp("2022-12-31")),
    "2023_RECOVERY":    (pd.Timestamp("2023-01-01"), pd.Timestamp("2023-12-31")),
    "2024_HALVING_BULL":(pd.Timestamp("2024-01-01"), pd.Timestamp("2024-12-31")),
    "2025_ALT_BLEED":   (pd.Timestamp("2025-01-01"), pd.Timestamp("2025-12-31")),
    "2026_DEEP_BEAR":   (pd.Timestamp("2026-01-01"), pd.Timestamp("2026-12-31")),
}

FIXED_TARGETS = [10, 20, 30, 50, 75, 100, 150, 200, 300]
TRAIL_WIDTHS = [10, 15, 20, 25, 30, 40, 50]
PARTIAL_TARGETS = [30, 50, 75, 100]
PARTIAL_TRAILS = [15, 20, 30]


# ── Load data ───────────────────────────────────────────────────────────────
def load_trades():
    with open(TRADES_PATH) as f:
        raw = json.load(f)
    trades = []
    for t in raw:
        trades.append({
            "token": t["token"],
            "direction": t["direction"],
            "entry_bar": t["entry_bar"],
            "exit_bar": t["exit_bar"],
            "entry_price": float(t["entry_price"]),
            "exit_price": float(t["exit_price"]),
            "pnl": float(t["pnl"]),
            "margin_usd": float(t["margin_usd"]),
            "hold_hours": t["hold_hours"],
            "exit_reason": t["exit_reason"],
        })
    return trades


_df_cache = {}

def get_token_df(token):
    if token not in _df_cache:
        path = CACHE_DIR / f"{token}_1h.parquet"
        if not path.exists():
            return None
        df = pd.read_parquet(path)
        if df.index.tz is not None:
            df.index = df.index.tz_localize(None)
        _df_cache[token] = df
    return _df_cache[token]


def build_unified_timeline(tokens):
    """Reconstruct the unified hourly timeline from all tokens used in trades.

    This mirrors v4/simulator.py build_unified_index(): collect all timestamps
    from all tokens (from BACKTEST_START onward), sort, deduplicate.
    Returns the sorted unified timestamp array.
    """
    all_ts = set()
    for token in tokens:
        df = get_token_df(token)
        if df is None:
            continue
        mask = df.index >= BACKTEST_START
        ts = df.index[mask]
        all_ts.update(ts.tolist())

    unified = np.array(sorted(all_ts), dtype="datetime64[ns]")
    return unified


def build_bar_map(token, unified_ts):
    """Build mapping from global_bar -> local_bar for a given token.

    Returns an array where bar_map[global_bar] = local_bar index into token's df,
    or -1 if the token has no data at that timestamp.
    """
    df = get_token_df(token)
    if df is None:
        return None

    token_ts = df.index.values.astype("datetime64[ns]")
    positions = np.searchsorted(token_ts, unified_ts)
    valid = (positions < len(token_ts)) & (
        token_ts[np.minimum(positions, len(token_ts) - 1)] == unified_ts
    )
    return np.where(valid, positions, -1)


def build_mtm_path_from_local(df, local_entry, hold_bars_global, bar_map, entry_bar_global,
                               direction, entry_price):
    """Build the MTM path using local bars mapped from global bars.

    We iterate through global bars from entry to entry+hold, map each to local bar,
    and compute MTM from the close price. Skip bars where token has no data (-1).
    """
    closes = df["close"].values
    mtm_list = []

    for g in range(entry_bar_global, min(entry_bar_global + hold_bars_global + 1, len(bar_map))):
        local = bar_map[g]
        if local == -1:
            continue
        if local >= len(closes):
            continue
        c = closes[local]
        if direction == -1:  # short
            mtm = (entry_price - c) / entry_price * 100 * LEVERAGE
        else:  # long
            mtm = (c - entry_price) / entry_price * 100 * LEVERAGE
        mtm_list.append(mtm)

    return np.array(mtm_list) if mtm_list else np.array([0.0])


# ── Simulation functions ────────────────────────────────────────────────────

def sim_fixed_target(mtm_path, target_pct, actual_pnl, margin_usd):
    """Simulate fixed profit target exit.
    Returns (exit_pnl_dollar, hit_target)."""
    for h in range(len(mtm_path)):
        if mtm_path[h] >= target_pct:
            exit_pnl = margin_usd * target_pct / 100.0
            return exit_pnl, True
    return actual_pnl, False


def sim_trailing_stop(mtm_path, trail_pct, actual_pnl, margin_usd):
    """Simulate trailing stop from peak MTM.
    Trail only activates once MTM has been positive (we're in profit).
    Returns (exit_pnl_dollar, triggered, capture_vs_peak)."""
    peak_mtm = 0.0
    activated = False
    for h in range(len(mtm_path)):
        mtm = mtm_path[h]
        if mtm > 0:
            activated = True
        if mtm > peak_mtm:
            peak_mtm = mtm
        if activated and peak_mtm > 0:
            drawdown_from_peak = peak_mtm - mtm
            if drawdown_from_peak >= trail_pct:
                exit_pnl = margin_usd * mtm / 100.0
                capture = mtm / peak_mtm * 100 if peak_mtm > 0 else 0
                return exit_pnl, True, capture
    # Never triggered - exit at actual
    final_mtm = mtm_path[-1] if len(mtm_path) > 0 else 0
    capture = final_mtm / peak_mtm * 100 if peak_mtm > 0 else 100
    return actual_pnl, False, capture


def sim_partial_tp_trail(mtm_path, partial_target, trail_pct, actual_pnl, margin_usd):
    """Simulate partial TP (50% at target) + trailing stop on remainder.
    Returns total PnL from both halves."""
    half_margin = margin_usd / 2.0
    partial_hit = False
    partial_pnl = 0.0

    # Phase 1: wait for partial target
    remainder_start = 0
    for h in range(len(mtm_path)):
        if mtm_path[h] >= partial_target:
            partial_pnl = half_margin * partial_target / 100.0
            partial_hit = True
            remainder_start = h
            break

    if not partial_hit:
        # Never hit partial target, exit everything at actual
        return actual_pnl

    # Phase 2: trail the remainder from its peak after partial exit
    peak_mtm = mtm_path[remainder_start]
    remainder_pnl = actual_pnl / 2.0  # default: half of actual

    for h in range(remainder_start, len(mtm_path)):
        mtm = mtm_path[h]
        if mtm > peak_mtm:
            peak_mtm = mtm
        dd = peak_mtm - mtm
        if peak_mtm > 0 and dd >= trail_pct:
            remainder_pnl = half_margin * mtm / 100.0
            break
    else:
        # Trail never triggered on remainder, use actual exit for remainder half
        remainder_pnl = actual_pnl / 2.0

    return partial_pnl + remainder_pnl


# ── Main analysis ───────────────────────────────────────────────────────────

def main():
    trades = load_trades()
    winners = [t for t in trades if t["pnl"] > 0]
    print(f"Total trades: {len(trades)}")
    print(f"Winners: {len(winners)}")
    print()

    # Build unified timeline from all tokens
    all_tokens = sorted(set(t["token"] for t in trades))
    print(f"Loading {len(all_tokens)} tokens and building unified timeline...")
    unified_ts = build_unified_timeline(all_tokens)
    print(f"Unified timeline: {len(unified_ts)} bars, {pd.Timestamp(unified_ts[0])} to {pd.Timestamp(unified_ts[-1])}")

    # Build bar maps for all tokens (cache)
    bar_maps = {}
    for token in all_tokens:
        bm = build_bar_map(token, unified_ts)
        if bm is not None:
            bar_maps[token] = bm

    print(f"Bar maps built for {len(bar_maps)} tokens")
    print()

    # Build MTM paths for all winners
    enriched = []
    skipped = 0
    misaligned = 0

    for t in winners:
        token = t["token"]
        df = get_token_df(token)
        if df is None or token not in bar_maps:
            skipped += 1
            continue

        bm = bar_maps[token]
        entry_bar_g = t["entry_bar"]
        exit_bar_g = t["exit_bar"]
        hold_bars = exit_bar_g - entry_bar_g

        # Resolve entry to local bar
        if entry_bar_g >= len(bm):
            skipped += 1
            continue

        local_entry = bm[entry_bar_g]
        if local_entry == -1:
            # Token doesn't have data at this global bar - try nearby
            found = False
            for delta in range(1, 5):
                for d in [delta, -delta]:
                    g2 = entry_bar_g + d
                    if 0 <= g2 < len(bm) and bm[g2] != -1:
                        local_entry = bm[g2]
                        found = True
                        break
                if found:
                    break
            if not found:
                skipped += 1
                continue

        # Verify price alignment
        closes = df["close"].values
        if local_entry >= len(closes):
            skipped += 1
            continue

        price_at_bar = closes[local_entry]
        price_err = abs(price_at_bar - t["entry_price"]) / t["entry_price"]
        if price_err > PRICE_TOL:
            misaligned += 1
            # Still include but use close price as reference for MTM path
            # (the difference is slippage adjustment in the simulator)

        # Use the actual close price at entry bar for MTM calculation
        # This ensures the MTM path is internally consistent with the price data
        # The actual PnL from the trade already accounts for slippage
        ref_price = price_at_bar if price_err <= 0.15 else t["entry_price"]
        if price_err > 0.15:
            # Too far off - this is likely a data issue, skip
            skipped += 1
            continue

        # Build MTM path
        mtm_path = build_mtm_path_from_local(
            df, local_entry, hold_bars, bm, entry_bar_g,
            t["direction"], ref_price
        )

        if len(mtm_path) < 2:
            skipped += 1
            continue

        # Determine entry date
        entry_date = pd.Timestamp(unified_ts[entry_bar_g])

        enriched.append({
            **t,
            "mtm_path": mtm_path,
            "entry_date": entry_date,
            "peak_mtm": float(np.max(mtm_path)),
        })

    print(f"Enriched winners: {len(enriched)}")
    print(f"Skipped (no data/short path): {skipped}")
    print(f"Price >5% slippage (included with close ref): {misaligned}")
    print()

    # ── Per-regime analysis ─────────────────────────────────────────────────
    for regime_name, (start, end) in REGIMES.items():
        regime_trades = [
            e for e in enriched
            if start <= e["entry_date"] <= end
        ]
        if not regime_trades:
            continue

        actual_total = sum(e["pnl"] for e in regime_trades)
        n = len(regime_trades)

        print("=" * 80)
        print(f"  {regime_name}")
        print("=" * 80)
        print(f"  N winners analyzed: {n}")
        print(f"  Actual total PnL from winners: ${actual_total:,.0f}")
        print(f"  Avg actual PnL: ${actual_total / n:,.0f}")
        print(f"  Avg peak MTM: {np.mean([e['peak_mtm'] for e in regime_trades]):.1f}%")
        print()

        # ── SIMULATION 1: Fixed Profit Target ──────────────────────────────
        print("  FIXED TARGET:")
        print(f"  {'Target':>8s} | {'Hit%':>6s} | {'Avg PnL hit':>12s} | {'Avg PnL miss':>12s} | {'Total PnL':>12s} | {'vs Actual':>10s}")
        print(f"  {'-'*8} | {'-'*6} | {'-'*12} | {'-'*12} | {'-'*12} | {'-'*10}")

        best_fixed_pnl = -1e18
        best_fixed_cfg = ""

        for target in FIXED_TARGETS:
            hit_pnls = []
            miss_pnls = []
            total_pnl = 0.0

            for e in regime_trades:
                pnl, hit = sim_fixed_target(
                    e["mtm_path"], target, e["pnl"], e["margin_usd"]
                )
                total_pnl += pnl
                if hit:
                    hit_pnls.append(pnl)
                else:
                    miss_pnls.append(pnl)

            hit_pct = len(hit_pnls) / n * 100
            avg_hit = np.mean(hit_pnls) if hit_pnls else 0
            avg_miss = np.mean(miss_pnls) if miss_pnls else 0
            vs_actual = (total_pnl - actual_total) / abs(actual_total) * 100 if actual_total != 0 else 0

            print(f"  {target:>7d}% | {hit_pct:5.1f}% | ${avg_hit:>10,.0f} | ${avg_miss:>10,.0f} | ${total_pnl:>10,.0f} | {vs_actual:>+9.1f}%")

            if total_pnl > best_fixed_pnl:
                best_fixed_pnl = total_pnl
                best_fixed_cfg = f"Fixed TP {target}%"

        print()

        # ── SIMULATION 2: Trailing Stop ────────────────────────────────────
        print("  TRAILING STOP:")
        print(f"  {'Trail%':>8s} | {'Triggers%':>9s} | {'Avg capture':>12s} | {'Total PnL':>12s} | {'vs Actual':>10s}")
        print(f"  {'-'*8} | {'-'*9} | {'-'*12} | {'-'*12} | {'-'*10}")

        best_trail_pnl = -1e18
        best_trail_cfg = ""

        for trail in TRAIL_WIDTHS:
            total_pnl = 0.0
            triggered = 0
            captures = []

            for e in regime_trades:
                pnl, trig, cap = sim_trailing_stop(
                    e["mtm_path"], trail, e["pnl"], e["margin_usd"]
                )
                total_pnl += pnl
                if trig:
                    triggered += 1
                captures.append(cap)

            trig_pct = triggered / n * 100
            avg_capture = np.mean(captures)
            vs_actual = (total_pnl - actual_total) / abs(actual_total) * 100 if actual_total != 0 else 0

            print(f"  {trail:>7d}% | {trig_pct:>8.1f}% | {avg_capture:>10.1f}% | ${total_pnl:>10,.0f} | {vs_actual:>+9.1f}%")

            if total_pnl > best_trail_pnl:
                best_trail_pnl = total_pnl
                best_trail_cfg = f"Trail {trail}%"

        print()

        # ── SIMULATION 3: Partial TP + Trail ───────────────────────────────
        print("  PARTIAL TP + TRAIL (50% at target, trail remainder):")
        print(f"  {'Target/Trail':>14s} | {'Avg PnL':>10s} | {'Total PnL':>12s} | {'vs Actual':>10s}")
        print(f"  {'-'*14} | {'-'*10} | {'-'*12} | {'-'*10}")

        best_combo_pnl = -1e18
        best_combo_cfg = ""

        for pt in PARTIAL_TARGETS:
            for tr in PARTIAL_TRAILS:
                total_pnl = 0.0
                for e in regime_trades:
                    pnl = sim_partial_tp_trail(
                        e["mtm_path"], pt, tr, e["pnl"], e["margin_usd"]
                    )
                    total_pnl += pnl

                avg_pnl = total_pnl / n
                vs_actual = (total_pnl - actual_total) / abs(actual_total) * 100 if actual_total != 0 else 0

                print(f"  {pt:>5d}%/{tr:<2d}%    | ${avg_pnl:>8,.0f} | ${total_pnl:>10,.0f} | {vs_actual:>+9.1f}%")

                if total_pnl > best_combo_pnl:
                    best_combo_pnl = total_pnl
                    best_combo_cfg = f"Partial {pt}%/Trail {tr}%"

        print()

        # ── Best config ────────────────────────────────────────────────────
        all_best = [
            (best_fixed_pnl, best_fixed_cfg),
            (best_trail_pnl, best_trail_cfg),
            (best_combo_pnl, best_combo_cfg),
        ]
        overall_best = max(all_best, key=lambda x: x[0])
        vs_actual = (overall_best[0] - actual_total) / abs(actual_total) * 100 if actual_total != 0 else 0
        print(f"  >>> BEST CONFIG for {regime_name}: {overall_best[1]}")
        print(f"      Total PnL: ${overall_best[0]:,.0f} (vs actual ${actual_total:,.0f}, {vs_actual:+.1f}%)")
        print()

    # ── Cross-regime summary ────────────────────────────────────────────────
    print()
    print("=" * 80)
    print("  CROSS-REGIME SUMMARY")
    print("=" * 80)
    print()

    actual_all = sum(e["pnl"] for e in enriched)
    print(f"  Total winners analyzed: {len(enriched)}")
    print(f"  Actual total PnL: ${actual_all:,.0f}")
    print()

    # Run one more pass to find universal best
    for sim_name, sim_configs in [
        ("FIXED TARGET", [(t,) for t in FIXED_TARGETS]),
        ("TRAILING STOP", [(t,) for t in TRAIL_WIDTHS]),
    ]:
        print(f"  {sim_name} -- All regimes combined:")
        for cfg in sim_configs:
            total_all = 0.0
            for e in enriched:
                if sim_name == "FIXED TARGET":
                    pnl, _ = sim_fixed_target(e["mtm_path"], cfg[0], e["pnl"], e["margin_usd"])
                else:
                    pnl, _, _ = sim_trailing_stop(e["mtm_path"], cfg[0], e["pnl"], e["margin_usd"])
                total_all += pnl
            vs = (total_all - actual_all) / abs(actual_all) * 100 if actual_all != 0 else 0
            label = f"{cfg[0]}%"
            print(f"    {label:>6s}: ${total_all:>12,.0f}  (vs actual {vs:+.1f}%)")
        print()

    print(f"  PARTIAL TP + TRAIL -- All regimes combined:")
    for pt in PARTIAL_TARGETS:
        for tr in PARTIAL_TRAILS:
            total_all = 0.0
            for e in enriched:
                total_all += sim_partial_tp_trail(e["mtm_path"], pt, tr, e["pnl"], e["margin_usd"])
            vs = (total_all - actual_all) / abs(actual_all) * 100 if actual_all != 0 else 0
            print(f"    {pt:>3d}%/{tr:<3d}%: ${total_all:>12,.0f}  (vs actual {vs:+.1f}%)")
    print()


if __name__ == "__main__":
    main()
