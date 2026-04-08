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
"""Mission Q3 — Smart entry timing on s523d using BTC cascade state.

Current s523c/s523d rule: enter every position at `daily_close + 1h` blindly.
This ignores ~60 possible intraday entry minutes, some of which are much
better than others based on BTC cascade state.

The rule:
  Every entry timestamp T_orig from the s523d trade log is interpreted as
  "you must enter this position SOMETIME within the next 12 hours". We scan
  the 12h window [T_orig, T_orig + 12h] at minute granularity and pick the
  best minute based on cascade state preference:

    1. recovery_window  (0-2h after cascade recovery)   BEST  — score 3
    2. pre_cascade      (no cascade activity nearby)     OK   — score 2
    3. active_cascade   (inside a cascade)               BAD  — score 1
    4. post_cascade     (2-24h after cascade recovery)   WORST — score 0

  For each trade, scan the 12h window for the highest-score minute. If the
  original T_orig already sits in recovery_window, no shift. If T_orig is
  post_cascade, look for the next pre_cascade or recovery_window minute.
  Ties broken by "earliest minute" (favor entering sooner).

Four variants tested:
  baseline              no shift
  shift_lookahead_6h    scan 6h window
  shift_lookahead_12h   scan 12h window
  shift_lookahead_24h   scan 24h window

Also reports: how many trades were shifted, by how many hours, and the
distribution of new-minute states.

Pass criteria (same as Q/Q2):
  PROMOTE if any variant strictly improves Sharpe AND Calmar without
           hurting total return by more than 5%.
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
from mission_d_gate1_overlay import compute_baseline_metrics  # noqa: E402

TRADES_PATH = REPO / "results/v4/s523d_growth_12mo_50k_trades.json"
OUT_JSON = REPO / "research/mission_q3_results.json"

# Cascade state preference scores (higher = better entry moment)
STATE_SCORES = {
    "recovery_window": 3,  # best — 2.3x alpha on s523c, 1.4x on s523d
    "pre_cascade":     2,  # normal
    "active_cascade":  1,  # bad — volatility, drift
    "post_cascade":    0,  # worst — dead money
    "normal":          2,  # treat as pre_cascade
}

# Sample the lookahead window every N minutes (not every minute — too slow)
SAMPLE_EVERY_MIN = 15

# Minimum score improvement to bother shifting (otherwise no-op)
MIN_SCORE_GAIN = 1


def find_best_entry_ts(
    cstates: mq.CascadeStates,
    orig_ts: pd.Timestamp,
    lookahead_hours: int,
) -> tuple[pd.Timestamp, str, int]:
    """Scan [orig_ts, orig_ts + lookahead_hours] at SAMPLE_EVERY_MIN cadence.
    Return (best_ts, best_state, best_score). Ties broken by earliest minute.
    """
    orig_state = cstates.classify(orig_ts)
    orig_score = STATE_SCORES.get(orig_state, 2)
    best_ts = orig_ts
    best_state = orig_state
    best_score = orig_score
    # Scan
    end_ts = orig_ts + pd.Timedelta(hours=lookahead_hours)
    cur = orig_ts
    while cur <= end_ts:
        s = cstates.classify(cur)
        sc = STATE_SCORES.get(s, 2)
        if sc > best_score:
            best_ts = cur
            best_state = s
            best_score = sc
            # Early exit if we hit the best possible state
            if best_score == max(STATE_SCORES.values()):
                break
        cur = cur + pd.Timedelta(minutes=SAMPLE_EVERY_MIN)
    return best_ts, best_state, best_score


def apply_smart_timing(
    trades: list[dict],
    cstates: mq.CascadeStates,
    token_closes: dict[str, pd.Series],
    lookahead_hours: int,
) -> tuple[list[dict], dict]:
    stats = {
        "n_in": len(trades),
        "n_shifted": 0,
        "n_noop": 0,
        "n_shift_failed_reprice": 0,
        "shift_state_from": {},
        "shift_state_to": {},
        "shift_hours_hist": [],
    }
    out: list[dict] = []
    for tr in trades:
        tr = deepcopy(tr)
        entry_bar = int(tr["entry_bar"])
        exit_bar = int(tr["exit_bar"])
        orig_entry_ts = mq.ts_from_bar(entry_bar)
        orig_exit_ts = mq.ts_from_bar(exit_bar)
        best_ts, best_state, best_score = find_best_entry_ts(cstates, orig_entry_ts, lookahead_hours)
        orig_state = cstates.classify(orig_entry_ts)
        orig_score = STATE_SCORES.get(orig_state, 2)

        if best_score - orig_score < MIN_SCORE_GAIN:
            # No meaningful shift — keep as-is
            stats["n_noop"] += 1
            out.append(tr)
            continue

        # Shift entry to best_ts's hour bucket
        new_entry_bar = mq.bar_from_ts(best_ts)
        if new_entry_bar >= exit_bar:
            # Shift would put entry at or after exit — abandon shift, keep original
            stats["n_shift_failed_reprice"] += 1
            out.append(tr)
            continue
        # Re-price: use token's hourly close at new_entry_bar
        tok = tr["token"]
        close = token_closes.get(tok)
        if close is None:
            stats["n_shift_failed_reprice"] += 1
            out.append(tr)
            continue
        new_entry_ts = mq.ts_from_bar(new_entry_bar)
        try:
            new_entry_px = float(close.asof(new_entry_ts))
            exit_px_ref = float(close.asof(orig_exit_ts))
            orig_entry_px_ref = float(close.asof(orig_entry_ts))
        except Exception:
            stats["n_shift_failed_reprice"] += 1
            out.append(tr)
            continue
        if not (np.isfinite(new_entry_px) and np.isfinite(exit_px_ref)):
            stats["n_shift_failed_reprice"] += 1
            out.append(tr)
            continue
        # Recompute PnL with the new entry price
        direction = int(tr["direction"])
        notional = mq.infer_notional(tr)
        new_ret = (exit_px_ref - new_entry_px) / new_entry_px * direction
        try:
            efee = float(tr.get("entry_fee", 0.0) or 0.0)
            xfee = float(tr.get("exit_fee", 0.0) or 0.0)
        except (TypeError, ValueError):
            efee = xfee = 0.0
        new_pnl = notional * new_ret - (efee + xfee)
        tr["entry_bar"] = new_entry_bar
        tr["entry_price"] = new_entry_px
        tr["pnl"] = new_pnl

        stats["n_shifted"] += 1
        stats["shift_state_from"][orig_state] = stats["shift_state_from"].get(orig_state, 0) + 1
        stats["shift_state_to"][best_state] = stats["shift_state_to"].get(best_state, 0) + 1
        stats["shift_hours_hist"].append((new_entry_bar - entry_bar))
        out.append(tr)
    stats["n_out"] = len(out)
    if stats["shift_hours_hist"]:
        arr = np.array(stats["shift_hours_hist"])
        stats["shift_hours_mean"] = float(arr.mean())
        stats["shift_hours_median"] = float(np.median(arr))
        stats["shift_hours_max"] = int(arr.max())
    return out, stats


def dpct(new, base, k):
    b = float(base[k])
    if abs(b) < 1e-12:
        return 0.0
    return (float(new[k]) - b) / abs(b) * 100.0


def main() -> None:
    print("[1/5] Loading BTC CPI + detecting cascades...")
    btc = mq.load_btc_mincpi()
    events = mq.detect_cascades_q(btc)
    events = mq.compute_recovery_ts(btc, events)
    cstates = mq.CascadeStates(events)
    print(f"      {len(events)} cascade events")

    print("[2/5] Loading s523d trade log...")
    with open(TRADES_PATH) as f:
        trades = json.load(f)
    print(f"      {len(trades)} trades")

    print("[3/5] Loading hourly token closes...")
    tokens = sorted({t["token"] for t in trades})
    token_closes = {}
    for tok in tokens:
        s = mq.load_token_close(tok)
        if s is not None:
            token_closes[tok] = s
    print(f"      {len(token_closes)}/{len(tokens)} tokens with hourly data")

    print("[4/5] Baseline...")
    baseline = compute_baseline_metrics(trades)
    print(f"      Return={baseline['total_return_pct']:+.2f}%  MaxDD={baseline['max_drawdown_pct']:+.2f}%  "
          f"Sharpe={baseline['sharpe']:+.2f}  Calmar={baseline['calmar']:+.2f}")

    print("[5/5] Running smart-timing variants...")
    results = {
        "config": {
            "state_scores": STATE_SCORES,
            "sample_every_min": SAMPLE_EVERY_MIN,
            "min_score_gain": MIN_SCORE_GAIN,
        },
        "baseline": baseline,
        "variants": {},
    }

    variants = [
        ("shift_lookahead_6h",  6),
        ("shift_lookahead_12h", 12),
        ("shift_lookahead_24h", 24),
    ]

    print("\n" + "=" * 120)
    print("MISSION Q3 — smart intraday entry timing on s523d")
    print("=" * 120)
    print(f"{'Variant':<24} {'Trades':>7} {'Shifted':>9} {'Return':>10} {'MaxDD':>9} {'Sharpe':>8} "
          f"{'Calmar':>8} {'dRet':>9} {'dMaxDD':>10} {'dSharpe':>10} {'dCalmar':>10}")
    print(f"{'baseline_s523d':<24} {len(trades):>7} {'—':>9} "
          f"{baseline['total_return_pct']:>+9.2f}% {baseline['max_drawdown_pct']:>+8.2f}% "
          f"{baseline['sharpe']:>+8.2f} {baseline['calmar']:>+8.2f}")

    for label, hrs in variants:
        modified, stats = apply_smart_timing(trades, cstates, token_closes, hrs)
        m = compute_baseline_metrics(modified)
        row = {
            "label": label,
            "lookahead_hours": hrs,
            "stats": stats,
            "metrics": m,
            "delta_return_pct": dpct(m, baseline, "total_return_pct"),
            "delta_maxdd_pct": dpct(m, baseline, "max_drawdown_pct"),
            "delta_sharpe_pct": dpct(m, baseline, "sharpe"),
            "delta_calmar_pct": dpct(m, baseline, "calmar"),
        }
        results["variants"][label] = row
        print(f"{label:<24} {stats['n_out']:>7} {stats['n_shifted']:>9} "
              f"{m['total_return_pct']:>+9.2f}% {m['max_drawdown_pct']:>+8.2f}% "
              f"{m['sharpe']:>+8.2f} {m['calmar']:>+8.2f} "
              f"{row['delta_return_pct']:>+8.1f}% {row['delta_maxdd_pct']:>+9.1f}% "
              f"{row['delta_sharpe_pct']:>+9.1f}% {row['delta_calmar_pct']:>+9.1f}%")

    print("\nShift detail per variant:")
    for label, _ in variants:
        v = results["variants"][label]
        s = v["stats"]
        print(f"  {label}: shifted {s['n_shifted']}/{s['n_in']} "
              f"(noop {s['n_noop']}, reprice_fail {s['n_shift_failed_reprice']})")
        if "shift_hours_mean" in s:
            print(f"     shift hours mean={s['shift_hours_mean']:.1f}, median={s['shift_hours_median']:.1f}, max={s['shift_hours_max']}")
        print(f"     shift FROM states: {s['shift_state_from']}")
        print(f"     shift TO   states: {s['shift_state_to']}")

    # Verdict
    best = None
    best_calmar_gain = -999
    for label, _ in variants:
        v = results["variants"][label]
        if v["delta_calmar_pct"] > best_calmar_gain and v["delta_return_pct"] >= -5.0:
            best = v
            best_calmar_gain = v["delta_calmar_pct"]

    if best is None:
        verdict = "KILL"
    elif (best["delta_calmar_pct"] > 10.0 and
          best["delta_sharpe_pct"] > 0 and
          best["delta_return_pct"] > -5.0):
        verdict = "PROMOTE"
    elif best["delta_calmar_pct"] > 0 and best["delta_sharpe_pct"] > 0:
        verdict = "NEEDS_TUNING"
    else:
        verdict = "KILL"

    results["verdict"] = verdict
    results["winner"] = best["label"] if best else None
    print(f"\n>>> VERDICT: {verdict}")
    if best:
        print(f"    Winner: {best['label']}")
        print(f"    dReturn={best['delta_return_pct']:+.1f}%  dMaxDD={best['delta_maxdd_pct']:+.1f}%  "
              f"dSharpe={best['delta_sharpe_pct']:+.1f}%  dCalmar={best['delta_calmar_pct']:+.1f}%")

    OUT_JSON.write_text(json.dumps(results, indent=2, default=str))
    print(f"\nSaved -> {OUT_JSON}")


if __name__ == "__main__":
    main()
