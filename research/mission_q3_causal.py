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
"""Mission Q3-causal — bias-free rerun of Q3 smart entry timing.

Q3 original had look-ahead bias in two places:
  1. active_cascade classified as starting 30 min BEFORE peak
     (requires knowing future peak)
  2. pre_cascade explicitly checked "next peak >2h in future"
     (requires knowing future peaks)
  3. Event list was precomputed over the full 12mo window, so even the
     "recovery_ts" markers were effectively known ahead of time.

Q3-causal rewrites the state classifier to use ONLY past-and-current
information:

  - Walk BTC 1-min series forward, tracking (in_active_cascade, last_recovery_ts)
  - At each minute t, decide state using only data [0..t]:
      * if CPI[t] > TRIGGER: in_active_cascade = True, state = "active"
      * if in_active AND CPI[t] < END AND vdv[t] >= 0: recovery just happened,
        last_recovery_ts = t, state = "recovery_window"
      * if last_recovery_ts exists:
          - (t - last_recovery_ts) <= 2h → "recovery_window"
          - (t - last_recovery_ts) <= 24h → "post_cascade"
          - else → "normal"
      * else → "normal"

  - There is NO "pre_cascade" state (it required seeing future peaks).
  - There is NO 30-min pre-peak active classification.
  - The classifier is a pure forward-pass over the data; state[t] depends
    only on data[0..t].

Shift rule (also strictly causal):
  At s523d entry trigger time orig_ts, look up state[orig_ts]:
    - normal or recovery_window → enter at orig_ts (no shift, it's fine)
    - active → wait, watching the causal state forward. Enter at the FIRST
      minute where state transitions to recovery_window (cap wait at 12h).
    - post_cascade → we know last_recovery_ts (past, fully observed).
      The 24h window ends at last_recovery_ts + 24h. Defer to that moment
      (cap at 12h from orig_ts). At that moment state is "normal", enter.

All of the above uses only information that a real-time agent observing
BTC microstructure could have at the moment of decision. No look-ahead.

Variants tested:
  baseline               no shift
  causal_lookahead_6h    scan up to 6h ahead
  causal_lookahead_12h   scan up to 12h ahead
  causal_lookahead_24h   scan up to 24h ahead (same as biased Q3 for comparison)
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
OUT_JSON = REPO / "research/mission_q3_causal_results.json"

# CPI thresholds (match Mission J Gate 1)
CPI_TRIGGER = 0.75
CPI_END = 0.40
RECOVERY_WINDOW_H = 2
POST_CASCADE_H = 24


# ---------------------------------------------------------------------------
# Strictly causal state precomputation
# ---------------------------------------------------------------------------
def build_causal_states(btc: pd.DataFrame) -> pd.DataFrame:
    """Walk BTC 1-min series forward, compute state[t] using only [0..t].

    Returns a DataFrame indexed by ts with columns:
      state, in_active, last_recovery_ts
    """
    cpi = btc["cpi"].to_numpy() if "cpi" in btc.columns else None
    if cpi is None:
        # Build CPI from components via mission_j_gate1
        cpi = mq.mjg.build_cpi(btc).to_numpy()
    vdv = btc["vdv_5min"].to_numpy()
    idx = btc.index

    n = len(btc)
    state = np.empty(n, dtype=object)
    in_active_arr = np.zeros(n, dtype=bool)
    last_recov_ix = np.full(n, -1, dtype=np.int64)

    in_active = False
    active_seen_trigger = False  # to avoid classifying brief dips as recoveries
    last_recov = -1  # index of last recovery moment

    for i in range(n):
        c = cpi[i]
        v = vdv[i]

        # Transition logic using only current tick
        if np.isfinite(c):
            if c > CPI_TRIGGER:
                in_active = True
                active_seen_trigger = True
            elif in_active and c < CPI_END and active_seen_trigger:
                # Cascade is de-escalating. If vdv has crossed back through 0,
                # declare recovery at this minute.
                if np.isfinite(v) and v >= 0:
                    in_active = False
                    active_seen_trigger = False
                    last_recov = i

        # Determine state for this minute
        if in_active:
            state[i] = "active"
        elif last_recov >= 0:
            dt_min = (idx[i] - idx[last_recov]).total_seconds() / 60.0
            if dt_min <= RECOVERY_WINDOW_H * 60:
                state[i] = "recovery_window"
            elif dt_min <= POST_CASCADE_H * 60:
                state[i] = "post_cascade"
            else:
                state[i] = "normal"
        else:
            state[i] = "normal"

        in_active_arr[i] = in_active
        last_recov_ix[i] = last_recov

    out = pd.DataFrame({
        "state": state,
        "in_active": in_active_arr,
        "last_recov_ix": last_recov_ix,
    }, index=idx)
    return out


def state_at(states_df: pd.DataFrame, ts: pd.Timestamp) -> tuple[str, int]:
    """Look up the causal state at or just before ts (forward-fill semantics)."""
    try:
        pos = states_df.index.get_indexer([ts], method="pad")[0]
    except Exception:
        pos = states_df.index.searchsorted(ts, side="right") - 1
    if pos < 0:
        return "normal", -1
    return str(states_df["state"].iloc[pos]), int(states_df["last_recov_ix"].iloc[pos])


# ---------------------------------------------------------------------------
# Causal shift rule
# ---------------------------------------------------------------------------
def causal_shift_ts(
    states_df: pd.DataFrame,
    orig_ts: pd.Timestamp,
    lookahead_hours: int,
) -> tuple[pd.Timestamp, str]:
    """Compute the shifted entry ts using only causal information.

    Returns (new_ts, reason). reason is a label explaining the shift:
      'no_shift'               - state already good, entered at orig_ts
      'waited_for_recovery'    - was in active cascade, waited until causal
                                 recovery was observed
      'waited_out_postcascade' - was in post_cascade, deferred to
                                 last_recovery + 24h (known from past)
      'cap_hit'                - tried to wait but hit lookahead cap
    """
    st, last_recov_ix = state_at(states_df, orig_ts)

    if st in ("normal", "recovery_window"):
        return orig_ts, "no_shift"

    max_ts = orig_ts + pd.Timedelta(hours=lookahead_hours)

    if st == "post_cascade":
        # We know last_recovery_ts (it's in the past). The 24h window ends at
        # last_recov + 24h. Defer to that moment (state becomes "normal").
        if last_recov_ix < 0 or last_recov_ix >= len(states_df):
            return orig_ts, "no_shift"
        last_recov_ts = states_df.index[last_recov_ix]
        target = last_recov_ts + pd.Timedelta(hours=POST_CASCADE_H)
        # Add a small buffer (1 min) to be safe the state has flipped to normal
        target = target + pd.Timedelta(minutes=1)
        if target <= orig_ts:
            return orig_ts, "no_shift"  # shouldn't happen, but defensive
        if target > max_ts:
            # Hit the cap — wait as long as we can
            return max_ts, "cap_hit"
        return target, "waited_out_postcascade"

    if st == "active":
        # Walk forward minute-by-minute, watch the causal state. First minute
        # where state becomes recovery_window (or normal, as a fallback), enter.
        try:
            start_pos = states_df.index.get_indexer([orig_ts], method="pad")[0]
        except Exception:
            start_pos = states_df.index.searchsorted(orig_ts, side="right") - 1
        if start_pos < 0:
            return orig_ts, "no_shift"
        end_pos = min(len(states_df) - 1,
                      start_pos + int(lookahead_hours * 60))
        for j in range(start_pos + 1, end_pos + 1):
            s_j = str(states_df["state"].iloc[j])
            if s_j == "recovery_window":
                return states_df.index[j], "waited_for_recovery"
            if s_j == "normal":
                return states_df.index[j], "waited_for_recovery"
        # Never recovered within lookahead — give up
        return max_ts, "cap_hit"

    # Unknown state — don't shift
    return orig_ts, "no_shift"


# ---------------------------------------------------------------------------
# Apply to trade list
# ---------------------------------------------------------------------------
def apply_causal_timing(
    trades: list[dict],
    states_df: pd.DataFrame,
    token_closes: dict[str, pd.Series],
    lookahead_hours: int,
) -> tuple[list[dict], dict]:
    stats = {
        "n_in": len(trades),
        "no_shift": 0,
        "waited_for_recovery": 0,
        "waited_out_postcascade": 0,
        "cap_hit": 0,
        "shift_failed_reprice": 0,
        "shifts_applied": 0,
        "orig_state_counts": {},
        "new_state_counts": {},
        "shift_hours": [],
    }
    out: list[dict] = []
    for tr in trades:
        tr = deepcopy(tr)
        entry_bar = int(tr["entry_bar"])
        exit_bar = int(tr["exit_bar"])
        orig_entry_ts = mq.ts_from_bar(entry_bar)
        orig_exit_ts = mq.ts_from_bar(exit_bar)
        orig_state, _ = state_at(states_df, orig_entry_ts)
        stats["orig_state_counts"][orig_state] = stats["orig_state_counts"].get(orig_state, 0) + 1

        new_ts, reason = causal_shift_ts(states_df, orig_entry_ts, lookahead_hours)
        stats[reason] = stats.get(reason, 0) + 1

        if reason == "no_shift" or new_ts == orig_entry_ts:
            new_state, _ = state_at(states_df, new_ts)
            stats["new_state_counts"][new_state] = stats["new_state_counts"].get(new_state, 0) + 1
            out.append(tr)
            continue

        new_entry_bar = mq.bar_from_ts(new_ts)
        if new_entry_bar >= exit_bar:
            stats["shift_failed_reprice"] += 1
            out.append(tr)
            continue
        tok = tr["token"]
        close = token_closes.get(tok)
        if close is None:
            stats["shift_failed_reprice"] += 1
            out.append(tr)
            continue
        try:
            new_entry_px = float(close.asof(mq.ts_from_bar(new_entry_bar)))
            exit_px_ref = float(close.asof(orig_exit_ts))
        except Exception:
            stats["shift_failed_reprice"] += 1
            out.append(tr)
            continue
        if not (np.isfinite(new_entry_px) and np.isfinite(exit_px_ref)):
            stats["shift_failed_reprice"] += 1
            out.append(tr)
            continue

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

        new_state, _ = state_at(states_df, mq.ts_from_bar(new_entry_bar))
        stats["new_state_counts"][new_state] = stats["new_state_counts"].get(new_state, 0) + 1
        stats["shifts_applied"] += 1
        stats["shift_hours"].append((new_entry_bar - entry_bar))
        out.append(tr)

    if stats["shift_hours"]:
        arr = np.array(stats["shift_hours"])
        stats["shift_hours_mean"] = float(arr.mean())
        stats["shift_hours_median"] = float(np.median(arr))
        stats["shift_hours_max"] = int(arr.max())
    stats["n_out"] = len(out)
    return out, stats


def dpct(new, base, k):
    b = float(base[k])
    if abs(b) < 1e-12:
        return 0.0
    return (float(new[k]) - b) / abs(b) * 100.0


def main() -> None:
    print("[1/5] Loading BTC microstructure CPI parquet...")
    btc = mq.load_btc_mincpi()
    print(f"      {len(btc):,} rows")

    print("[2/5] Building CAUSAL state series (forward pass only)...")
    states = build_causal_states(btc)
    state_dist = states["state"].value_counts()
    print(f"      state distribution over 12mo:")
    for s, n in state_dist.items():
        print(f"        {s:>18}: {n:>8,} min  ({n/len(states)*100:.1f}%)")

    print("[3/5] Loading s523d trade log...")
    with open(TRADES_PATH) as f:
        trades = json.load(f)
    print(f"      {len(trades)} trades")

    print("[4/5] Loading hourly token closes...")
    tokens = sorted({t["token"] for t in trades})
    token_closes = {}
    for tok in tokens:
        s = mq.load_token_close(tok)
        if s is not None:
            token_closes[tok] = s
    print(f"      {len(token_closes)}/{len(tokens)} tokens")

    # Original entry state distribution (causal)
    orig_state_counts = {}
    for t in trades:
        ts = mq.ts_from_bar(int(t["entry_bar"]))
        st, _ = state_at(states, ts)
        orig_state_counts[st] = orig_state_counts.get(st, 0) + 1
    print(f"      ORIGINAL s523d entry states (causal): {orig_state_counts}")

    # Per-state baseline PnL
    from collections import defaultdict
    state_pnl = defaultdict(list)
    for t in trades:
        ts = mq.ts_from_bar(int(t["entry_bar"]))
        st, _ = state_at(states, ts)
        state_pnl[st].append(float(t["pnl"]))
    print("      per-state s523d avg PnL (CAUSAL classifier):")
    for st, ps in state_pnl.items():
        arr = np.array(ps)
        print(f"        {st:>18}: n={len(arr):4d}  mean=${arr.mean():+8.1f}  "
              f"total=${arr.sum():+10.0f}  win={((arr>0).mean()):.2f}")

    baseline = compute_baseline_metrics(trades)
    print(f"\n      baseline: Return={baseline['total_return_pct']:+.2f}%  "
          f"MaxDD={baseline['max_drawdown_pct']:+.2f}%  "
          f"Sharpe={baseline['sharpe']:+.2f}  Calmar={baseline['calmar']:+.2f}")

    print("\n[5/5] Running causal shift variants...")
    results = {
        "config": {
            "cpi_trigger": CPI_TRIGGER,
            "cpi_end": CPI_END,
            "recovery_window_h": RECOVERY_WINDOW_H,
            "post_cascade_h": POST_CASCADE_H,
        },
        "state_distribution_12mo": state_dist.to_dict(),
        "orig_entry_state_counts": orig_state_counts,
        "per_state_pnl": {
            st: {"n": len(ps), "mean": float(np.mean(ps)),
                 "total": float(np.sum(ps)), "win_rate": float((np.array(ps) > 0).mean())}
            for st, ps in state_pnl.items()
        },
        "baseline": baseline,
        "variants": {},
    }

    variants = [
        ("causal_lookahead_6h",  6),
        ("causal_lookahead_12h", 12),
        ("causal_lookahead_24h", 24),
    ]

    print("\n" + "=" * 125)
    print("MISSION Q3-causal — bias-free smart timing on s523d")
    print("=" * 125)
    print(f"{'Variant':<24} {'Trades':>7} {'Shifts':>8} {'Return':>10} {'MaxDD':>9} "
          f"{'Sharpe':>8} {'Calmar':>8} {'dRet':>9} {'dMaxDD':>10} {'dSharpe':>10} {'dCalmar':>10}")
    print(f"{'baseline_s523d':<24} {len(trades):>7} {'—':>8} "
          f"{baseline['total_return_pct']:>+9.2f}% {baseline['max_drawdown_pct']:>+8.2f}% "
          f"{baseline['sharpe']:>+8.2f} {baseline['calmar']:>+8.2f}")

    rows_for_detail = []
    for label, hrs in variants:
        modified, stats = apply_causal_timing(trades, states, token_closes, hrs)
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
        rows_for_detail.append(row)
        print(f"{label:<24} {stats['n_out']:>7} {stats['shifts_applied']:>8} "
              f"{m['total_return_pct']:>+9.2f}% {m['max_drawdown_pct']:>+8.2f}% "
              f"{m['sharpe']:>+8.2f} {m['calmar']:>+8.2f} "
              f"{row['delta_return_pct']:>+8.1f}% {row['delta_maxdd_pct']:>+9.1f}% "
              f"{row['delta_sharpe_pct']:>+9.1f}% {row['delta_calmar_pct']:>+9.1f}%")

    print("\nShift detail:")
    for r in rows_for_detail:
        s = r["stats"]
        print(f"  {r['label']}: applied={s.get('shifts_applied', 0)}  "
              f"no_shift={s.get('no_shift', 0)}  "
              f"waited_for_recovery={s.get('waited_for_recovery', 0)}  "
              f"waited_out_postcascade={s.get('waited_out_postcascade', 0)}  "
              f"cap_hit={s.get('cap_hit', 0)}")
        if "shift_hours_mean" in s:
            print(f"     shift hours mean={s['shift_hours_mean']:.1f}, "
                  f"median={s['shift_hours_median']:.1f}, max={s['shift_hours_max']}")
        print(f"     new_state_counts: {s.get('new_state_counts', {})}")

    # Verdict
    best = None
    best_calmar_gain = -999
    for r in rows_for_detail:
        if r["delta_calmar_pct"] > best_calmar_gain and r["delta_return_pct"] >= -5.0:
            best = r
            best_calmar_gain = r["delta_calmar_pct"]

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
        print(f"    dReturn={best['delta_return_pct']:+.1f}%  "
              f"dMaxDD={best['delta_maxdd_pct']:+.1f}%  "
              f"dSharpe={best['delta_sharpe_pct']:+.1f}%  "
              f"dCalmar={best['delta_calmar_pct']:+.1f}%")

    OUT_JSON.write_text(json.dumps(results, indent=2, default=str))
    print(f"\nSaved -> {OUT_JSON}")


if __name__ == "__main__":
    main()
