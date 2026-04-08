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
"""Mission Q — BTC cascade-timing overlay on s523c_growth 12mo trade log.

Hypothesis: BTC liquidation cascades (detected via Mission J's CPI) mark
transient regimes where new s523c entries should be delayed or skipped, and
where existing shorts should be closed into the panic. This is a REGIME-BASED
overlay on the existing s523c trade list — we do NOT rerun the backtest.

Variants tested:
  (A,X)  delay entries during active cascade  + close shorts at peak
  (A,Y)  delay entries during active cascade  + hold shorts
  (B,X)  skip  entries during active cascade  + close shorts at peak
  (B,Y)  skip  entries during active cascade  + hold shorts
  baseline: no modification

Re-uses:
  - mission_j_gate1.build_cpi (from cached parquet) + detect_cascades
  - mission_d_gate1_overlay.compute_baseline_metrics + _eq_metrics (equity curve
    and metrics from a trade list).
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

import mission_j_gate1 as mjg  # noqa: E402
from mission_d_gate1_overlay import (  # noqa: E402
    BACKTEST_END,
    BACKTEST_START,
    INITIAL_CAPITAL,
    _eq_metrics,
    compute_baseline_metrics,
)

TRADES_PATH = REPO / "results/v4/s523c_growth_12mo_50k_trades.json"
CPI_PARQUET = REPO / "research/mission_j_cpi_btc.parquet"
BTC_HOURLY = REPO / "data/perp/binance/1h_ohlcv/BTC_perp_1h.csv"
OUT_JSON = REPO / "research/mission_q_results.json"

# Mission Q spec parameters (overlay scope)
CPI_TRIGGER_Q = 0.75        # spec: "CPI > 0.75 sustained >= 5 min"
MERGE_GAP_MIN_Q = 60        # merge events < 60 min apart
ACTIVE_PRE_PEAK_MIN = 30    # active_cascade starts 30min before peak
RECOVERY_WINDOW_H = 2       # recovery window duration in hours
DELAY_MAX_H = 8             # max look-ahead for recovery when delaying entries
PEAK_CLOSE_LAG_MIN = 5      # close shorts 5 min after the peak
MIN_RET_FOR_NOTIONAL = 0.01 # if |orig_ret| below this, infer notional differently
MAX_LEVERAGE_CAP = 10.0     # hard cap on inferred leverage (s523c <= ~5x typical)
FALLBACK_LEVERAGE = 3.0     # typical s523c leverage for fallback


def load_btc_mincpi() -> pd.DataFrame:
    """Load cached CPI parquet (1-min index) and restrict to backtest window +/- buffer."""
    df = pd.read_parquet(CPI_PARQUET)
    df["ts"] = pd.to_datetime(df["ts"])
    df = df.set_index("ts").sort_index()
    # Buffer: look a bit wider than [start, end] so events spanning boundaries are kept
    lo = BACKTEST_START - pd.Timedelta(days=1)
    hi = BACKTEST_END + pd.Timedelta(days=1)
    return df.loc[(df.index >= lo) & (df.index <= hi)]


def detect_cascades_q(df: pd.DataFrame) -> pd.DataFrame:
    """Mission J Gate 1 detector with overridden trigger=0.75."""
    # Temporarily override module globals so mjg.detect_cascades uses our trigger
    orig_trig = mjg.CPI_TRIGGER
    orig_end = mjg.CPI_END
    orig_gap = mjg.MIN_EVENT_GAP_MIN
    mjg.CPI_TRIGGER = CPI_TRIGGER_Q
    mjg.CPI_END = max(0.2, CPI_TRIGGER_Q - 0.35)  # symmetric-ish downthreshold
    mjg.MIN_EVENT_GAP_MIN = MERGE_GAP_MIN_Q
    try:
        events = mjg.detect_cascades(df)
    finally:
        mjg.CPI_TRIGGER = orig_trig
        mjg.CPI_END = orig_end
        mjg.MIN_EVENT_GAP_MIN = orig_gap
    return events


def compute_recovery_ts(btc: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    """For each event, compute the recovery_ts = first bar after peak where
    vdv_5min >= 0. Fall back to end_ts if never recovers before event end.
    """
    vdv = btc["vdv_5min"].to_numpy()
    idx = btc.index
    recov_ts = []
    peak_px = []
    for _, ev in events.iterrows():
        peak_ts = ev["peak_ts"]
        end_ts = ev["end_ts"]
        try:
            peak_i = idx.get_loc(peak_ts)
        except KeyError:
            peak_i = idx.searchsorted(peak_ts, side="left")
        try:
            end_i = idx.get_loc(end_ts)
        except KeyError:
            end_i = idx.searchsorted(end_ts, side="left")
        # scan from peak_i to end_i + 120 min buffer
        scan_to = min(len(idx) - 1, end_i + 120)
        recov = None
        for i in range(peak_i + 1, scan_to + 1):
            v = vdv[i]
            if np.isfinite(v) and v >= 0.0:
                recov = idx[i]
                break
        if recov is None:
            recov = end_ts
        recov_ts.append(recov)
        peak_px.append(float(btc["mid_price"].iloc[peak_i]))
    ev_out = events.copy()
    ev_out["recovery_ts"] = recov_ts
    ev_out["peak_price"] = peak_px
    return ev_out


# ---------------------------------------------------------------------------
# Cascade-state utilities (vectorized using searchsorted)
# ---------------------------------------------------------------------------
class CascadeStates:
    """Cascade event index with O(log N) state lookups for any timestamp."""

    def __init__(self, events: pd.DataFrame):
        self.events = events.reset_index(drop=True)
        self.peak_ts = pd.DatetimeIndex(events["peak_ts"].values).sort_values()
        self.recov_ts = pd.DatetimeIndex(events["recovery_ts"].values)
        # align recovery to sorted peak order
        order = np.argsort(events["peak_ts"].values)
        self.peak_ts_sorted = pd.DatetimeIndex(events["peak_ts"].values[order])
        self.recov_ts_sorted = pd.DatetimeIndex(events["recovery_ts"].values[order])
        self.peak_price_sorted = np.asarray(events["peak_price"].values[order], dtype=float)

    def classify(self, ts: pd.Timestamp) -> str:
        if len(self.peak_ts_sorted) == 0:
            return "normal"
        # find idx of most recent peak <= ts
        pos = self.peak_ts_sorted.searchsorted(ts, side="right") - 1
        if pos >= 0:
            peak = self.peak_ts_sorted[pos]
            recov = self.recov_ts_sorted[pos]
            if peak - pd.Timedelta(minutes=ACTIVE_PRE_PEAK_MIN) <= ts <= recov:
                return "active_cascade"
            if recov < ts <= recov + pd.Timedelta(hours=RECOVERY_WINDOW_H):
                return "recovery_window"
            if recov + pd.Timedelta(hours=RECOVERY_WINDOW_H) < ts <= recov + pd.Timedelta(hours=24):
                return "post_cascade"
        # check forward-looking pre_cascade (next peak within 2h)
        pos_next = self.peak_ts_sorted.searchsorted(ts, side="left")
        next_ok = pos_next >= len(self.peak_ts_sorted) or (
            self.peak_ts_sorted[pos_next] - ts > pd.Timedelta(hours=2)
        )
        prev_ok = pos < 0 or (ts - self.recov_ts_sorted[pos] > pd.Timedelta(hours=2))
        if next_ok and prev_ok:
            return "pre_cascade"
        if not next_ok and prev_ok:
            return "pre_cascade"  # still 'clear' until active
        return "normal"

    def next_recovery_ts(self, ts: pd.Timestamp) -> pd.Timestamp | None:
        """Return the next recovery_ts at or after ts (for Variant A delay)."""
        if len(self.recov_ts_sorted) == 0:
            return None
        # find active cascade containing ts
        pos = self.peak_ts_sorted.searchsorted(ts, side="right") - 1
        if pos >= 0 and self.peak_ts_sorted[pos] - pd.Timedelta(minutes=ACTIVE_PRE_PEAK_MIN) <= ts <= self.recov_ts_sorted[pos]:
            return self.recov_ts_sorted[pos]
        # else find next recovery strictly after ts
        pos2 = self.recov_ts_sorted.searchsorted(ts, side="left")
        if pos2 < len(self.recov_ts_sorted):
            return self.recov_ts_sorted[pos2]
        return None

    def active_peak_in_range(self, entry_ts: pd.Timestamp, exit_ts: pd.Timestamp) -> tuple[pd.Timestamp, float] | None:
        """Return (peak_ts, peak_price) for the first cascade peak that falls
        within (entry_ts, exit_ts], or None."""
        lo = self.peak_ts_sorted.searchsorted(entry_ts, side="right")
        hi = self.peak_ts_sorted.searchsorted(exit_ts, side="right")
        if lo >= hi:
            return None
        return self.peak_ts_sorted[lo], float(self.peak_price_sorted[lo])


# ---------------------------------------------------------------------------
# Hourly BTC for entry price lookups (we use BTC close at the recovery-hour
# as proxy; per-token close at recovery would be ideal but BTC is a reasonable
# reference and consistent with Mission J). Actually for Variant A we need to
# convert bar_ts -> entry_bar (integer hours since BACKTEST_START).
# The s523c trade log's pnl already bakes in entry_price/exit_price, so we
# cannot truly "replay" PnL without the token price at the shifted entry bar.
# Approach: shift entry_bar to the recovery ts's hour bucket; re-price using
# the original token's hourly close.
# ---------------------------------------------------------------------------
OHLCV_DIR = REPO / "data/perp/binance/1h_ohlcv"


def load_token_close(token: str) -> pd.Series | None:
    p = OHLCV_DIR / f"{token}_perp_1h.csv"
    if not p.exists():
        return None
    df = pd.read_csv(p, parse_dates=["datetime"]).set_index("datetime").sort_index()
    if df.index.tz is not None:
        df.index = df.index.tz_convert(None)
    return df["close"].astype(np.float64)


def infer_notional(tr: dict) -> float:
    """Robust notional inference from a trade.

    Preference order:
      1. If |orig_ret| >= MIN_RET_FOR_NOTIONAL, use pnl / orig_ret, capped by
         margin * MAX_LEVERAGE_CAP.
      2. Otherwise use margin * FALLBACK_LEVERAGE.
    Always returns a positive value.
    """
    entry_px = float(tr["entry_price"])
    exit_px = float(tr["exit_price"])
    direction = int(tr["direction"])
    pnl = float(tr["pnl"])
    margin = float(tr["margin_usd"])
    orig_ret = (exit_px - entry_px) / entry_px * direction
    cap = abs(margin) * MAX_LEVERAGE_CAP
    fallback = abs(margin) * FALLBACK_LEVERAGE
    if abs(orig_ret) >= MIN_RET_FOR_NOTIONAL and abs(pnl) > 1e-9:
        n = pnl / orig_ret
        if not np.isfinite(n):
            return fallback
        n = abs(n)
        if n > cap:
            return cap
        if n < abs(margin) * 0.5:  # unreasonably small — use fallback
            return fallback
        return n
    return fallback


def bar_from_ts(ts: pd.Timestamp) -> int:
    return int((ts.floor("h") - BACKTEST_START).total_seconds() // 3600)


def ts_from_bar(bar: int) -> pd.Timestamp:
    return BACKTEST_START + pd.Timedelta(hours=int(bar))


# ---------------------------------------------------------------------------
# Variant application
# ---------------------------------------------------------------------------
def apply_variant(
    trades: list[dict],
    cstates: CascadeStates,
    token_closes: dict[str, pd.Series],
    entry_mode: str,   # 'A'=delay, 'B'=skip
    short_mode: str,   # 'X'=close-at-peak, 'Y'=hold
) -> tuple[list[dict], dict]:
    """Return (modified_trades, stats)."""
    stats = {
        "n_in": len(trades),
        "entries_in_cascade": 0,
        "entries_delayed": 0,
        "entries_skipped": 0,
        "entries_delay_failed_too_far": 0,
        "exits_closed_at_peak": 0,
        "exits_unchanged": 0,
    }
    out: list[dict] = []
    for tr in trades:
        tr = deepcopy(tr)
        # parse numeric fields
        for k in ("pnl", "margin_usd", "entry_price", "exit_price",
                  "entry_fee", "exit_fee", "funding_cost"):
            if k in tr:
                try:
                    tr[k] = float(tr[k])
                except (TypeError, ValueError):
                    pass
        entry_bar = int(tr["entry_bar"])
        exit_bar = int(tr["exit_bar"])
        direction = int(tr["direction"])
        entry_ts = ts_from_bar(entry_bar)
        exit_ts = ts_from_bar(exit_bar)
        state = cstates.classify(entry_ts)

        # ---- ENTRY HANDLING ----
        if state == "active_cascade":
            stats["entries_in_cascade"] += 1
            if entry_mode == "B":
                # Skip: zero-PnL, zero-margin trade (effectively drop)
                stats["entries_skipped"] += 1
                continue  # drop from trade list entirely
            else:
                # A: delay to next recovery_ts if <= DELAY_MAX_H away
                recov_ts = cstates.next_recovery_ts(entry_ts)
                if recov_ts is None or (recov_ts - entry_ts) > pd.Timedelta(hours=DELAY_MAX_H):
                    stats["entries_delay_failed_too_far"] += 1
                    # Fall back to skip (cannot delay realistically)
                    continue
                # Shift entry_bar to the hour bucket of recovery_ts
                new_entry_bar = bar_from_ts(recov_ts)
                if new_entry_bar >= exit_bar:
                    # Original exit already passed the recovery — skip
                    stats["entries_delay_failed_too_far"] += 1
                    continue
                # Re-price entry using token's hourly close at new_entry_bar
                tok = tr["token"]
                close = token_closes.get(tok)
                if close is None:
                    # Cannot re-price; keep original PnL but shift timing only
                    tr["entry_bar"] = new_entry_bar
                    out.append(tr)
                    stats["entries_delayed"] += 1
                    continue
                new_entry_ts = ts_from_bar(new_entry_bar)
                orig_entry_ts = ts_from_bar(entry_bar)
                orig_exit_ts = ts_from_bar(exit_bar)
                try:
                    new_entry_px = float(close.asof(new_entry_ts))
                    orig_entry_px_ref = float(close.asof(orig_entry_ts))
                    exit_px_ref = float(close.asof(orig_exit_ts))
                except Exception:
                    out.append(tr)
                    stats["entries_delayed"] += 1
                    continue
                if not (np.isfinite(new_entry_px) and np.isfinite(orig_entry_px_ref) and np.isfinite(exit_px_ref)):
                    out.append(tr)
                    stats["entries_delayed"] += 1
                    continue
                notional = infer_notional(tr)
                new_ret = (exit_px_ref - new_entry_px) / new_entry_px * direction
                new_pnl = notional * new_ret - (tr.get("entry_fee", 0.0) + tr.get("exit_fee", 0.0))
                tr["entry_bar"] = new_entry_bar
                tr["entry_price"] = new_entry_px
                tr["pnl"] = new_pnl
                out.append(tr)
                stats["entries_delayed"] += 1
                continue
        # state is not active_cascade → keep entry as-is, then process exit

        # ---- EXISTING-POSITION (SHORT) HANDLING ----
        if direction == -1 and short_mode == "X":
            hit = cstates.active_peak_in_range(entry_ts, exit_ts)
            if hit is not None:
                peak_ts, peak_px = hit
                new_exit_ts = peak_ts + pd.Timedelta(minutes=PEAK_CLOSE_LAG_MIN)
                new_exit_bar = bar_from_ts(new_exit_ts)
                if new_exit_bar > entry_bar and new_exit_bar < exit_bar:
                    # Need a token-specific price at peak; use token's hourly
                    # close at the peak hour as a proxy (BTC micro peak does
                    # not necessarily align exactly with alt peak but cascades
                    # are highly correlated).
                    tok = tr["token"]
                    close = token_closes.get(tok)
                    if close is not None:
                        try:
                            tok_px_at_peak = float(close.asof(new_exit_ts))
                        except Exception:
                            tok_px_at_peak = None
                        if tok_px_at_peak is not None and np.isfinite(tok_px_at_peak):
                            new_ret = (tr["entry_price"] - tok_px_at_peak) / tr["entry_price"]
                            # direction = -1 short ⇒ profit when entry > new_exit
                            notional = infer_notional(tr)
                            new_pnl = notional * new_ret - (tr.get("entry_fee", 0.0) + tr.get("exit_fee", 0.0))
                            tr["exit_bar"] = new_exit_bar
                            tr["exit_price"] = tok_px_at_peak
                            tr["pnl"] = new_pnl
                            stats["exits_closed_at_peak"] += 1
                            out.append(tr)
                            continue
        stats["exits_unchanged"] += 1
        out.append(tr)

    stats["n_out"] = len(out)
    return out, stats


def delta(new: dict, base: dict, key: str) -> float:
    return float(new[key] - base[key])


def delta_pct(new: dict, base: dict, key: str) -> float:
    b = float(base[key])
    if abs(b) < 1e-12:
        return 0.0
    return (float(new[key]) - b) / abs(b) * 100.0


def main() -> None:
    print("[1/6] Loading BTC microstructure CPI parquet...")
    btc = load_btc_mincpi()
    print(f"      rows={len(btc):,}  range={btc.index.min()} .. {btc.index.max()}")

    print("[2/6] Detecting cascade events (trigger=0.75, merge<60min)...")
    events = detect_cascades_q(btc)
    print(f"      {len(events)} events")
    if len(events) == 0:
        print("No cascade events found — aborting.")
        return

    events = compute_recovery_ts(btc, events)
    print(f"      first peak: {events['peak_ts'].min()}")
    print(f"      last  peak: {events['peak_ts'].max()}")
    print(f"      median peak CPI: {events['peak_cpi'].median():.3f}")
    dur_min = (events["recovery_ts"] - events["peak_ts"]).dt.total_seconds() / 60.0
    print(f"      median peak->recovery: {dur_min.median():.1f} min")

    cstates = CascadeStates(events)

    print("[3/6] Loading s523c trade log...")
    with open(TRADES_PATH) as f:
        trades = json.load(f)
    print(f"      {len(trades)} trades ({sum(1 for t in trades if int(t['direction'])==1)} long / "
          f"{sum(1 for t in trades if int(t['direction'])==-1)} short)")

    # Pre-load token close series for all unique tokens
    print("[4/6] Loading hourly closes for unique tokens...")
    tokens = sorted({t["token"] for t in trades})
    token_closes: dict[str, pd.Series] = {}
    for tok in tokens:
        s = load_token_close(tok)
        if s is not None:
            token_closes[tok] = s
    print(f"      {len(token_closes)}/{len(tokens)} tokens have hourly data")

    # Classify entry states for diagnostics
    from collections import Counter, defaultdict
    state_counts = Counter(cstates.classify(ts_from_bar(int(t["entry_bar"]))) for t in trades)
    print(f"      entry state distribution: {dict(state_counts)}")

    # Per-state avg PnL to test predictive value
    state_pnl = defaultdict(list)
    for t in trades:
        s = cstates.classify(ts_from_bar(int(t["entry_bar"])))
        state_pnl[s].append(float(t["pnl"]))
    print("      per-state s523c avg PnL:")
    state_pnl_summary = {}
    for s, ps in state_pnl.items():
        arr = np.array(ps)
        summary = {"n": int(len(arr)), "mean": float(arr.mean()),
                   "median": float(np.median(arr)), "total": float(arr.sum()),
                   "win_rate": float((arr > 0).mean())}
        state_pnl_summary[s] = summary
        print(f"        {s:>18}: n={summary['n']:4d} mean=${summary['mean']:+8.1f} "
              f"total=${summary['total']:+10.0f} win={summary['win_rate']:.2f}")

    print("[5/6] Computing baseline metrics...")
    baseline = compute_baseline_metrics(trades)
    print(f"      Return={baseline['total_return_pct']:+.2f}%  MaxDD={baseline['max_drawdown_pct']:+.2f}%  "
          f"Sharpe={baseline['sharpe']:+.2f}  Calmar={baseline['calmar']:+.2f}")

    print("[6/6] Running 4 overlay variants...")
    variant_specs = [
        ("A", "X", "(A,X) delay+close_shorts"),
        ("A", "Y", "(A,Y) delay+hold_shorts"),
        ("B", "X", "(B,X) skip+close_shorts"),
        ("B", "Y", "(B,Y) skip+hold_shorts"),
    ]

    results = {
        "config": {
            "cpi_trigger": CPI_TRIGGER_Q,
            "merge_gap_min": MERGE_GAP_MIN_Q,
            "active_pre_peak_min": ACTIVE_PRE_PEAK_MIN,
            "recovery_window_h": RECOVERY_WINDOW_H,
            "delay_max_h": DELAY_MAX_H,
            "peak_close_lag_min": PEAK_CLOSE_LAG_MIN,
        },
        "cascade_events": {
            "n_events": int(len(events)),
            "median_peak_cpi": float(events["peak_cpi"].median()),
            "median_duration_min": float(dur_min.median()),
        },
        "entry_state_distribution": dict(state_counts),
        "per_state_pnl": state_pnl_summary,
        "baseline": baseline,
        "variants": {},
    }

    rows = []
    for em, sm, label in variant_specs:
        modified, stats = apply_variant(trades, cstates, token_closes, em, sm)
        m = compute_baseline_metrics(modified)
        v_row = {
            "label": label,
            "entry_mode": em,
            "short_mode": sm,
            "stats": stats,
            "metrics": m,
            "delta_return": delta(m, baseline, "total_return_pct"),
            "delta_maxdd": delta(m, baseline, "max_drawdown_pct"),
            "delta_sharpe": delta(m, baseline, "sharpe"),
            "delta_calmar": delta(m, baseline, "calmar"),
            "delta_calmar_pct": delta_pct(m, baseline, "calmar"),
            "delta_maxdd_pct": delta_pct(m, baseline, "max_drawdown_pct"),
            "delta_sharpe_pct": delta_pct(m, baseline, "sharpe"),
            "delta_return_pct": delta_pct(m, baseline, "total_return_pct"),
        }
        results["variants"][label] = v_row
        rows.append(v_row)

    # --- table ---
    print("\n" + "=" * 110)
    print("MISSION Q — cascade overlay on s523c_growth 12mo trade log")
    print("=" * 110)
    print(f"{'Variant':<32} {'Trades':>7} {'Return':>9} {'MaxDD':>9} {'Sharpe':>8} {'Calmar':>8} {'ΔCalmar':>10} {'ΔMaxDD':>10}")
    print(f"{'baseline':<32} {len(trades):>7} "
          f"{baseline['total_return_pct']:>+8.2f}% "
          f"{baseline['max_drawdown_pct']:>+8.2f}% "
          f"{baseline['sharpe']:>+8.2f} "
          f"{baseline['calmar']:>+8.2f} "
          f"{'—':>10} {'—':>10}")
    for r in rows:
        m = r["metrics"]
        print(f"{r['label']:<32} {r['stats']['n_out']:>7} "
              f"{m['total_return_pct']:>+8.2f}% "
              f"{m['max_drawdown_pct']:>+8.2f}% "
              f"{m['sharpe']:>+8.2f} "
              f"{m['calmar']:>+8.2f} "
              f"{r['delta_calmar_pct']:>+9.1f}% "
              f"{r['delta_maxdd_pct']:>+9.1f}%")

    print("\nEntry handling detail per variant:")
    for r in rows:
        s = r["stats"]
        print(f"  {r['label']}: in_cascade={s['entries_in_cascade']}  "
              f"delayed={s['entries_delayed']}  skipped={s['entries_skipped']}  "
              f"delay_failed={s['entries_delay_failed_too_far']}  "
              f"shorts_closed_at_peak={s['exits_closed_at_peak']}")

    # --- verdict ---
    def passes(r):
        # PROMOTE if:
        # Calmar +10% and Sharpe not hurt by more than -5%
        calm_ok = r["delta_calmar_pct"] >= 10.0 and r["delta_sharpe_pct"] >= -5.0
        # MaxDD shrunk by 20% relative (maxdd less negative, i.e., delta_maxdd > 0)
        # and total return not hurt by more than 10%
        # Note: delta_maxdd_pct is (new-base)/|base|; if maxdd goes from -20 to -16,
        # delta is +4 / 20 = +20% improvement.
        dd_ok = r["delta_maxdd_pct"] >= 20.0 and r["delta_return_pct"] >= -10.0
        sharpe_ok = r["delta_sharpe_pct"] >= 15.0
        return calm_ok, dd_ok, sharpe_ok

    any_promote = False
    any_improve_some = False
    any_hurt_all = True
    print("\nPass criteria evaluation:")
    for r in rows:
        calm_ok, dd_ok, sharpe_ok = passes(r)
        print(f"  {r['label']}:")
        print(f"    ΔCalmar={r['delta_calmar_pct']:+.1f}% (need ≥+10, Sharpe Δ={r['delta_sharpe_pct']:+.1f}%≥-5) → {calm_ok}")
        print(f"    ΔMaxDD={r['delta_maxdd_pct']:+.1f}% (need ≥+20 improvement, Ret Δ={r['delta_return_pct']:+.1f}%≥-10) → {dd_ok}")
        print(f"    ΔSharpe={r['delta_sharpe_pct']:+.1f}% (stretch ≥+15) → {sharpe_ok}")
        if calm_ok or dd_ok or sharpe_ok:
            any_promote = True
        if (r["delta_calmar_pct"] > 0 or r["delta_sharpe_pct"] > 0
                or r["delta_maxdd_pct"] > 0 or r["delta_return_pct"] > 0):
            any_improve_some = True
        if (r["delta_calmar_pct"] >= 0 and r["delta_sharpe_pct"] >= 0
                and r["delta_maxdd_pct"] >= 0 and r["delta_return_pct"] >= 0):
            any_hurt_all = False

    if any_promote:
        # pick the winner — biggest calmar gain
        winner = max(rows, key=lambda r: r["delta_calmar_pct"])
        verdict = "PROMOTE"
        winner_label = winner["label"]
    elif any_improve_some:
        verdict = "NEEDS_TUNING"
        winner = max(rows, key=lambda r: r["delta_calmar_pct"])
        winner_label = winner["label"]
    else:
        verdict = "KILL"
        winner_label = None

    results["verdict"] = verdict
    results["winner"] = winner_label
    print(f"\n>>> VERDICT: {verdict}")
    if winner_label:
        print(f"    Winner: {winner_label}")

    OUT_JSON.write_text(json.dumps(results, indent=2, default=str))
    print(f"\nSaved → {OUT_JSON}")


if __name__ == "__main__":
    main()
