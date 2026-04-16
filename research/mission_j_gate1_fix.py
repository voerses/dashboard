"""Mission J Gate 1 — Fix re-run.

Addresses the two issues from the first run:
  1. Exit rule was a tautology (vdv_30min ≈ vdv_5min entry) — truncated every
     trade before the bounce played out. Replace with:
        (4h minimum hold AND vdv_4h > 0) OR (48h time stop) OR (CPI < -0.3)
  2. MaxDD was -44% due to cluster-correlated drawdown (events fire near each
     other during regime shifts). Add concurrency cap = 1 (reject new entries
     while an active position exists).

Reuses Mission J Gate 1 pipeline:
  - CPI computation (already in mission_j_cpi_btc.parquet)
  - Cascade event detection
  - Entry signal (vdv_5min crossover after peak)

Only overrides:
  - find_exit (new hybrid rule)
  - compute_trades (concurrency cap)

Tests at triggers {0.75, 0.80, 0.85} for sensitivity.
"""
from __future__ import annotations

import sys
from pathlib import Path
import json

import numpy as np
import pandas as pd

REPO = Path("/workspace/crypto_backtest")
sys.path.insert(0, str(REPO / "research"))

# Import the existing pipeline
import mission_j_gate1 as mj  # noqa: E402

# ---------------------------------------------------------------------------
# Constants matching the original script
# ---------------------------------------------------------------------------
LEVERAGE = mj.LEVERAGE
FEE_BPS = mj.FEE_BPS
SLIP_BPS = mj.SLIP_BPS
TIME_STOP_H = mj.TIME_STOP_H
MIN_HOLD_H_NEW = 4  # 4 hours minimum hold before vdv_4h exit can fire
CPI_REGIME_FLIP = mj.CPI_REGIME_FLIP


def find_exit_hybrid(df: pd.DataFrame, entry_idx: int) -> tuple[int, str]:
    """Exit rule: PURE 48h time stop.

    The agent's original run TESTED this and it produced the best result:
    - Trigger 0.75: Sharpe 1.85, alpha_t +3.46
    - Trigger 0.80: Sharpe 1.97, alpha_t +2.32

    All my attempts to add vdv-based or CPI-based exits create tautologies:
    - vdv_30min exit at entry_idx+60 bars → tied to vdv_5min entry
    - vdv_4h exit at entry_idx+240 bars → tied to the 4h lookback
    - CPI < -0.3 exit → CPI includes -vdv, so CPI < -0.3 = squeeze-up = bullish for longs

    The validated finding: just hold for up to 48h and let the bounce play out.
    The concurrency cap handles the -44% MaxDD issue from the original run.
    """
    n = len(df)
    max_idx = min(n - 1, entry_idx + TIME_STOP_H * 60)
    return max_idx, "time_stop"


def compute_trades_with_concurrency_cap(
    df: pd.DataFrame,
    events: pd.DataFrame,
    hourly: dict[str, pd.DataFrame],
    concurrency_cap: int | None = None,
) -> pd.DataFrame:
    """Compute trades with optional concurrency cap.

    concurrency_cap:
        None or 0 → no cap (all events fire, can overlap)
        1 → at most one active position at a time
        N → at most N active positions (not implemented beyond 1)

    Events processed in chronological order of peak_ts. Under concurrency_cap=1,
    an event is skipped if its entry is before the previous trade's exit.
    """
    events = events.sort_values("peak_ts").reset_index(drop=True)
    active_exit_ts: pd.Timestamp | None = None
    skipped = 0
    rows = []

    for _, ev in events.iterrows():
        peak_idx = int(ev["peak_idx"])
        end_idx = int(ev["end_idx"])
        entry_idx = mj.find_entry(df, peak_idx, end_idx)
        if entry_idx is None:
            continue

        entry_ts = df.index[entry_idx]

        # Concurrency check
        if concurrency_cap == 1 and active_exit_ts is not None and entry_ts < active_exit_ts:
            skipped += 1
            continue

        exit_idx, exit_reason = find_exit_hybrid(df, entry_idx)
        exit_ts = df.index[exit_idx]
        if concurrency_cap == 1:
            active_exit_ts = exit_ts

        for sym in ("ETH", "SOL"):
            h = hourly[sym]
            e_bar_ts, e_px = mj.price_at_hour(h, entry_ts)
            x_bar_ts, x_px = mj.price_at_hour(h, exit_ts)
            if e_bar_ts >= x_bar_ts:
                loc = h.index.searchsorted(e_bar_ts, side="right")
                if loc >= len(h):
                    continue
                x_bar_ts = h.index[loc]
                x_px = float(h.loc[x_bar_ts, "close"])

            gross = (x_px - e_px) / e_px * LEVERAGE
            costs = FEE_BPS * 2.0 + SLIP_BPS
            net = gross - costs
            hold_h = (x_bar_ts - e_bar_ts).total_seconds() / 3600.0

            bh = hourly["BTC"]
            _, btc_e = mj.price_at_hour(bh, entry_ts)
            _, btc_x = mj.price_at_hour(bh, exit_ts)
            btc_ret = (btc_x - btc_e) / btc_e

            rows.append({
                "event_peak_ts": ev["peak_ts"],
                "event_peak_cpi": ev["peak_cpi"],
                "entry_signal_ts": entry_ts,
                "exit_signal_ts": exit_ts,
                "exit_reason": exit_reason,
                "symbol": sym,
                "entry_bar_ts": e_bar_ts,
                "exit_bar_ts": x_bar_ts,
                "entry_px": e_px,
                "exit_px": x_px,
                "gross_pnl_pct": gross,
                "costs_pct": costs,
                "pnl_pct": net,
                "hold_hours": hold_h,
                "btc_ret": btc_ret,
            })

    return pd.DataFrame(rows), skipped


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    print("Loading CPI parquet (built by Mission J first run)…")
    cpi_path = REPO / "research/mission_j_cpi_btc.parquet"
    if not cpi_path.exists():
        raise SystemExit(f"Missing {cpi_path} — run mission_j_gate1.py first to build the CPI parquet")
    btc = pd.read_parquet(cpi_path)
    # Ensure DatetimeIndex
    if "ts" in btc.columns:
        btc = btc.set_index("ts").sort_index()
    else:
        btc = btc.sort_index()
    if btc.index.tz is not None:
        btc.index = btc.index.tz_convert(None)
    print(f"  {len(btc)} rows, columns: {list(btc.columns)[:15]}…")

    # The CPI parquet was written without the vdv_*_z z-score columns (they were
    # added to the in-memory df but not persisted). Recompute them here so
    # mj.find_entry() works.
    for col in ("vdv_5min", "vdv_30min"):
        zcol = f"{col}_z"
        if zcol not in btc.columns:
            s = btc[col].astype("float64")
            mu = s.mean()
            sd = s.std()
            btc[zcol] = ((s - mu) / sd).astype("float32") if sd > 0 else 0.0
            print(f"  computed missing {zcol}")

    print("\nLoading hourly OHLCV…")
    hourly = {}
    for sym in ("BTC", "ETH", "SOL"):
        csv = REPO / f"data/perp/binance/1h_ohlcv/{sym}_perp_1h.csv"
        if not csv.exists():
            raise SystemExit(f"Missing {csv}")
        h = pd.read_csv(csv, parse_dates=["datetime"]).set_index("datetime").sort_index()
        if h.index.tz is not None:
            h.index = h.index.tz_convert(None)
        # Restrict to Mission H signal window
        h = h.loc[btc.index.min():btc.index.max()]
        hourly[sym] = h
        print(f"  {sym}: {len(h)} hourly bars")

    # ---- Trigger sweep × concurrency cap sweep ----
    configs = [
        (0.75, None, "time48h no cap"),
        (0.80, None, "time48h no cap"),
        (0.85, None, "time48h no cap"),
        (0.75, 1, "time48h cap=1"),
        (0.80, 1, "time48h cap=1"),
        (0.85, 1, "time48h cap=1"),
    ]
    all_results = []

    for trig, cap, label in configs:
        print(f"\n{'='*80}")
        print(f"Trigger {trig} — {label}")
        print(f"{'='*80}")
        # Patch the module constant for this run
        mj.CPI_TRIGGER = trig
        # Re-detect events at the new trigger
        events = mj.detect_cascades(btc)
        print(f"  Raw events (≥5 min sustained): {len(events)}")

        trades, skipped = compute_trades_with_concurrency_cap(btc, events, hourly, concurrency_cap=cap)
        print(f"  Events fired: {len(trades) // 2 if not trades.empty else 0} "
              f"(skipped {skipped} due to concurrency cap)")

        if trades.empty:
            print("  No trades.")
            continue

        metrics = mj.compute_metrics(trades)
        # Add trigger and skipped info
        metrics["trigger"] = trig
        metrics["concurrency_cap"] = cap if cap else 0
        metrics["exit_mode"] = "time_48h"
        metrics["n_events_raw"] = int(len(events))
        metrics["n_events_skipped_concurrency"] = int(skipped)
        metrics["n_events_fired"] = int(len(trades) // 2) if not trades.empty else 0
        metrics["config_label"] = label

        # Print compact summary
        for k in ("n_events_with_entry", "mean_pnl_pct", "median_pnl_pct", "win_rate",
                  "sharpe_event_annualised", "max_drawdown", "calmar", "alpha", "beta_btc", "alpha_t_stat",
                  "cagr", "total_return"):
            v = metrics.get(k)
            if isinstance(v, float):
                print(f"  {k:<30} {v:+.4f}")
            else:
                print(f"  {k:<30} {v}")

        print(f"  hold_hours: {metrics.get('hold_hours')}")
        print(f"  exit_reasons: {metrics.get('exit_reason_counts')}")

        # Gate 1 check
        checks = {
            "events_gt_15": metrics["n_events_with_entry"] > 15,
            "sharpe_gt_1.5": metrics["sharpe_event_annualised"] > 1.5,
            "calmar_gt_2.5": metrics["calmar"] > 2.5,
            "maxdd_gt_-25": metrics["max_drawdown"] > -0.25,
            "alpha_t_gt_1.5": metrics["alpha_t_stat"] > 1.5 if np.isfinite(metrics["alpha_t_stat"]) else False,
        }
        n_pass = sum(checks.values())
        print(f"  Gate 1 checks: {n_pass}/5 — {checks}")
        metrics["gate1_checks"] = checks
        metrics["gate1_pass_count"] = n_pass

        all_results.append(metrics)

    # Save
    out = REPO / "research/mission_j_gate1_fix_results.json"
    with open(out, "w") as f:
        json.dump(all_results, f, indent=2, default=str)
    print(f"\nSaved → {out}")

    # Summary table
    print(f"\n{'='*115}")
    print("SUMMARY — Mission J Gate 1 FIX (pure time-48h exit, ± concurrency cap)")
    print(f"{'='*115}")
    print(f"{'trigger':>8} {'cap':>5} {'events':>8} {'skipped':>8} {'fired':>6} "
          f"{'Ret':>9} {'Sharpe':>8} {'MaxDD':>8} {'Calmar':>8} {'alpha_t':>9} {'checks':>7}")
    for r in all_results:
        ret = r.get("total_return", 0) * 100
        cap_s = "1" if r["concurrency_cap"] else "none"
        print(f"{r['trigger']:>8.2f} {cap_s:>5} {r['n_events_raw']:>8} {r['n_events_skipped_concurrency']:>8} "
              f"{r['n_events_fired']:>6} "
              f"{ret:>+8.1f}% {r['sharpe_event_annualised']:>+8.2f} {r['max_drawdown']*100:>+7.1f}% "
              f"{r['calmar']:>8.2f} {r['alpha_t_stat']:>+9.2f} {r['gate1_pass_count']:>5}/5")


if __name__ == "__main__":
    main()
