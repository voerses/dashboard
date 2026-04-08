#!/usr/bin/env python3
"""Mission J Gate 2 — Full 13-symbol microstructure universe + position-sized concurrency.

Builds on Gate 1 (mission_j_gate1.py + mission_j_gate1_fix.py):
  - Cascade trigger: BTC microstructure CPI composite (UNCHANGED from Gate 1)
  - Entry signal:    BTC vdv_5min_z reverts from <= -1.0 back through 0 after CPI peak
  - Exit:            PURE 48h time stop (Gate 1 finding — any vdv/CPI exit creates a tautology)
  - Recovery basket: ALL 13 symbols equal-weight long (was 2 in Gate 1: ETH+SOL)

Gate 2 fix — position-sized concurrency:
  - Aggregate exposure cap = 1.0 (never use >100% of capital)
  - When K events overlap, each gets weight 1/K_active at entry time
  - Once weighted, weight is FIXED for that trade's life (so freed capital
    flows into the NEXT new event, not back-propagating into existing trades)
  - This preserves the clustering alpha (Gate 1 showed cap=1 destroyed alpha)
    while bounding drawdown.

Costs: 4 bps fee/side + 5 bps slippage/side ≈ 18 bps round-trip per leg.

Validation: bootstrap MC (200 trials, month-stratified resample of cascade events).

Outputs:
  research/mission_j_gate2.py            (this script)
  research/mission_j_gate2_results.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path("/workspace/crypto_backtest")
sys.path.insert(0, str(REPO / "research"))

import mission_j_gate1 as mj  # CPI/cascade detection/entry rule

OUT_DIR = REPO / "research"
MSIG_DIR = REPO / "data" / "perp" / "binance"
HOURLY_DIR = REPO / "data" / "perp" / "binance" / "1h_ohlcv"

SYMBOLS = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE",
           "ADA", "AVAX", "LINK", "DOT", "NEAR", "ATOM", "LTC"]
BASKET = [s for s in SYMBOLS if s != "BTC"]  # 12 alts

# Costs (round-trip per leg)
FEE_BPS = 4.0 / 10_000.0
SLIP_BPS = 5.0 / 10_000.0
COST_RT = 2.0 * (FEE_BPS + SLIP_BPS)  # ~18 bps

TIME_STOP_H = 48
LEVERAGE = 1.0
MAX_AGG_EXPOSURE = 1.0

BOOTSTRAP_TRIALS = 200
RNG_SEED = 20260408


# ---------------------------------------------------------------------------
def load_btc_with_cpi() -> pd.DataFrame:
    """Load BTC microstructure parquet and compute CPI + entry-z columns."""
    print("Loading BTC microstructure_1min …")
    btc = pd.read_parquet(MSIG_DIR / "BTCUSDT" / "microstructure_1min.parquet")
    btc["ts"] = pd.to_datetime(btc["ts"])
    btc = btc.set_index("ts").sort_index()
    if btc.index.tz is not None:
        btc.index = btc.index.tz_convert(None)
    print(f"  rows={len(btc):,} range={btc.index.min()} .. {btc.index.max()}")

    print("Building CPI …")
    btc = mj.build_cpi(btc)
    print(f"  cpi range [{btc['cpi'].min():.3f}, {btc['cpi'].max():.3f}]  "
          f"std={btc['cpi'].std():.3f}")
    return btc


def load_hourly() -> dict[str, pd.DataFrame]:
    print(f"Loading hourly OHLCV for {len(SYMBOLS)} symbols …")
    out = {}
    for sym in SYMBOLS:
        csv = HOURLY_DIR / f"{sym}_perp_1h.csv"
        h = pd.read_csv(csv, parse_dates=["datetime"]).set_index("datetime").sort_index()
        if h.index.tz is not None:
            h.index = h.index.tz_convert(None)
        out[sym] = h
    print(f"  loaded {len(out)} symbols")
    return out


# ---------------------------------------------------------------------------
def build_event_legs(
    btc: pd.DataFrame,
    events: pd.DataFrame,
    hourly: dict[str, pd.DataFrame],
) -> pd.DataFrame:
    """One row per (event, symbol). Columns include entry/exit ts and pnl_per_unit.

    pnl_per_unit is the per-leg gross return (no weighting yet) — weighting happens
    in the concurrency-allocation pass.
    """
    rows = []
    for _, ev in events.iterrows():
        peak_idx = int(ev["peak_idx"])
        end_idx = int(ev["end_idx"])
        entry_idx = mj.find_entry(btc, peak_idx, end_idx)
        if entry_idx is None:
            continue
        exit_idx = min(len(btc) - 1, entry_idx + TIME_STOP_H * 60)
        entry_ts = btc.index[entry_idx]
        exit_ts = btc.index[exit_idx]

        for sym in BASKET:
            h = hourly[sym]
            e_bar_ts, e_px = mj.price_at_hour(h, entry_ts)
            x_bar_ts, x_px = mj.price_at_hour(h, exit_ts)
            if e_bar_ts >= x_bar_ts:
                loc = h.index.searchsorted(e_bar_ts, side="right")
                if loc >= len(h):
                    continue
                x_bar_ts = h.index[loc]
                x_px = float(h.loc[x_bar_ts, "close"])
            gross = (x_px - e_px) / e_px
            rows.append({
                "event_peak_ts": ev["peak_ts"],
                "event_peak_cpi": ev["peak_cpi"],
                "entry_signal_ts": entry_ts,
                "exit_signal_ts": exit_ts,
                "symbol": sym,
                "entry_bar_ts": e_bar_ts,
                "exit_bar_ts": x_bar_ts,
                "entry_px": e_px,
                "exit_px": x_px,
                "gross_pnl_pct": gross,            # leg gross (no costs yet)
                "net_pnl_pct": gross - COST_RT,    # leg net assuming full capital allocated
                "hold_hours": (x_bar_ts - e_bar_ts).total_seconds() / 3600.0,
            })
    return pd.DataFrame(rows)


def per_event_avg(legs: pd.DataFrame) -> pd.DataFrame:
    """Aggregate symbol legs into one row per event.

    Each event = equal-weight basket of all symbols that produced a leg.
    Returned columns:  event_peak_ts, entry_signal_ts, exit_signal_ts,
                       basket_gross, basket_net, n_legs, hold_hours
    """
    if legs.empty:
        return legs
    g = legs.groupby("event_peak_ts").agg(
        entry_signal_ts=("entry_signal_ts", "first"),
        exit_signal_ts=("exit_signal_ts", "first"),
        event_peak_cpi=("event_peak_cpi", "first"),
        basket_gross=("gross_pnl_pct", "mean"),
        basket_net_full_cap=("net_pnl_pct", "mean"),  # costs assuming weight=1.0
        n_legs=("symbol", "count"),
        hold_hours=("hold_hours", "mean"),
    ).reset_index().sort_values("entry_signal_ts").reset_index(drop=True)
    return g


# ---------------------------------------------------------------------------
def allocate_concurrency(events_basket: pd.DataFrame) -> pd.DataFrame:
    """Position-size each event under aggregate exposure cap = MAX_AGG_EXPOSURE.

    Algorithm:
        For each new event in entry-time order:
          1. Drop any active trades whose exit_ts <= this entry_ts.
          2. Count K_other = remaining active trades.
          3. weight = MAX_AGG_EXPOSURE / (K_other + 1)
          4. Add this event to active list with that fixed weight.
        weighted_pnl = basket_net * weight

    Note: weight is fixed at entry. Existing trades keep the smaller weight
    they were assigned earlier — that's the price of being early.
    """
    if events_basket.empty:
        events_basket = events_basket.copy()
        events_basket["weight"] = []
        events_basket["weighted_pnl"] = []
        events_basket["active_at_entry"] = []
        return events_basket

    df = events_basket.sort_values("entry_signal_ts").reset_index(drop=True)
    weights = np.zeros(len(df))
    active_count = np.zeros(len(df), dtype=int)

    # active = list of (exit_ts, idx)
    active: list[tuple[pd.Timestamp, int]] = []

    for i, row in df.iterrows():
        entry_ts = row["entry_signal_ts"]
        exit_ts = row["exit_signal_ts"]
        # Expire finished trades
        active = [(xt, idx) for (xt, idx) in active if xt > entry_ts]
        k_other = len(active)
        w = MAX_AGG_EXPOSURE / (k_other + 1)
        weights[i] = w
        active_count[i] = k_other + 1
        active.append((exit_ts, i))

    df["weight"] = weights
    df["active_at_entry"] = active_count
    # Costs scale with notional: a trade sized at weight*capital pays
    # weight * COST_RT in costs. weighted_pnl = weight * (gross - COST_RT).
    df["weighted_pnl"] = df["weight"] * (df["basket_gross"] - COST_RT)
    return df


# ---------------------------------------------------------------------------
def equity_curve_metrics(events_w: pd.DataFrame, btc_hourly: pd.DataFrame) -> dict:
    """Compute headline single-window metrics from a weighted event series.

    Equity is compounded from weighted returns in event-entry order. Note that
    because aggregate exposure is bounded by 1.0, sequential compounding of
    weighted_pnl is a fair proxy for capital growth (an event with weight 0.25
    contributes 0.25 * basket_net to NAV).
    """
    if events_w.empty:
        return {"n_events": 0}

    n = len(events_w)
    df = events_w.sort_values("entry_signal_ts").reset_index(drop=True)
    rets = df["weighted_pnl"].to_numpy(dtype="float64")
    eq = (1.0 + rets).cumprod()
    running_max = np.maximum.accumulate(eq)
    dd = eq / running_max - 1.0

    total_return = float(eq[-1] - 1.0)
    max_dd = float(dd.min())

    days = max(1, (df["entry_signal_ts"].max() - df["entry_signal_ts"].min()).days)
    years = days / 365.0
    cagr = float(eq[-1] ** (1.0 / max(years, 0.25)) - 1.0)
    calmar = cagr / abs(max_dd) if max_dd < 0 else float("inf")

    mean = float(rets.mean())
    sd = float(rets.std(ddof=1)) if n > 1 else 0.0
    events_per_year = n * 365.0 / days
    sharpe = (mean / sd) * np.sqrt(events_per_year) if sd > 0 else 0.0

    win_rate = float((rets > 0).sum()) / n

    # Alpha vs BTC: regress weighted_pnl on BTC return over the trade window
    bh = btc_hourly
    btc_rets = []
    for _, row in df.iterrows():
        _, e = mj.price_at_hour(bh, row["entry_signal_ts"])
        _, x = mj.price_at_hour(bh, row["exit_signal_ts"])
        btc_rets.append((x - e) / e * row["weight"])  # weight-scaled BTC ret for fair comparison
    x_arr = np.asarray(btc_rets, dtype="float64")
    y_arr = rets

    if n >= 3 and x_arr.std() > 0:
        xm = x_arr - x_arr.mean()
        ym = y_arr - y_arr.mean()
        beta = float((xm * ym).sum() / (xm * xm).sum())
        alpha = float(y_arr.mean() - beta * x_arr.mean())
        resid = y_arr - (alpha + beta * x_arr)
        sse = float((resid ** 2).sum())
        dof = n - 2
        sigma2 = sse / dof if dof > 0 else np.nan
        var_alpha = sigma2 * (1.0 / n + (x_arr.mean() ** 2) / (xm * xm).sum())
        se_alpha = float(np.sqrt(var_alpha)) if var_alpha > 0 else np.nan
        alpha_t = alpha / se_alpha if se_alpha and se_alpha > 0 else float("nan")
    else:
        alpha = beta = alpha_t = float("nan")

    return {
        "n_events": int(n),
        "days_span": int(days),
        "events_per_year": float(events_per_year),
        "mean_event_pnl": mean,
        "stdev_event_pnl": sd,
        "win_rate": win_rate,
        "sharpe": float(sharpe),
        "total_return": total_return,
        "cagr": cagr,
        "max_drawdown": max_dd,
        "calmar": float(calmar),
        "alpha": alpha,
        "beta_btc": beta,
        "alpha_t_stat": alpha_t,
        "mean_weight": float(df["weight"].mean()),
        "median_weight": float(df["weight"].median()),
        "mean_active_at_entry": float(df["active_at_entry"].mean()),
        "max_active_at_entry": int(df["active_at_entry"].max()),
    }


# ---------------------------------------------------------------------------
def per_symbol_contribution(legs: pd.DataFrame) -> dict:
    """Average net pnl per symbol leg + win rate (independent of weighting).

    This identifies which symbols carry the alpha. Note: these are unweighted
    leg returns — i.e. the answer to "if I'd traded just this symbol on each
    cascade, what would the average net trade have been?"
    """
    if legs.empty:
        return {}
    g = legs.groupby("symbol").agg(
        n_trades=("net_pnl_pct", "size"),
        mean_net=("net_pnl_pct", "mean"),
        median_net=("net_pnl_pct", "median"),
        win_rate=("net_pnl_pct", lambda s: float((s > 0).mean())),
        std_net=("net_pnl_pct", "std"),
    ).reset_index()
    g["t_stat"] = g["mean_net"] / (g["std_net"] / np.sqrt(g["n_trades"]))
    return g.set_index("symbol").to_dict(orient="index")


# ---------------------------------------------------------------------------
def bootstrap_mc(
    events_basket: pd.DataFrame,
    btc_hourly: pd.DataFrame,
    n_trials: int = BOOTSTRAP_TRIALS,
    seed: int = RNG_SEED,
) -> dict:
    """Month-stratified bootstrap. Each trial:
       1. For each calendar month, resample events with replacement (same N).
       2. Sort by entry_ts.
       3. Re-run concurrency allocation (weights depend on overlap pattern).
       4. Compute Sharpe / Calmar / total_return / alpha_t.
       Report mean & percentiles.
    """
    if events_basket.empty:
        return {"trials": 0}

    df = events_basket.copy()
    df["month"] = df["entry_signal_ts"].dt.to_period("M")

    rng = np.random.default_rng(seed)
    sharpes, calmars, totrets, alpha_ts, pos_flags = [], [], [], [], []

    months = df["month"].unique()
    indices_by_month = {m: df.index[df["month"] == m].to_numpy() for m in months}

    for t in range(n_trials):
        sampled_idx = []
        for m in months:
            pool = indices_by_month[m]
            if len(pool) == 0:
                continue
            sampled_idx.append(rng.choice(pool, size=len(pool), replace=True))
        sampled_idx = np.concatenate(sampled_idx) if sampled_idx else np.array([], dtype=int)
        sample = df.loc[sampled_idx].copy().reset_index(drop=True)
        sample = sample.sort_values("entry_signal_ts").reset_index(drop=True)
        sample = allocate_concurrency(sample)
        m = equity_curve_metrics(sample, btc_hourly)
        sharpes.append(m.get("sharpe", 0.0))
        calmars.append(m.get("calmar", 0.0))
        totrets.append(m.get("total_return", 0.0))
        at = m.get("alpha_t_stat", float("nan"))
        alpha_ts.append(at if np.isfinite(at) else 0.0)
        pos_flags.append(m.get("total_return", 0.0) > 0)

    arr_s = np.asarray(sharpes)
    arr_c = np.clip(np.asarray(calmars), -1e6, 1e6)  # cap inf
    arr_r = np.asarray(totrets)
    arr_a = np.asarray(alpha_ts)

    def stats(a):
        return {
            "mean": float(np.mean(a)),
            "p5": float(np.percentile(a, 5)),
            "p50": float(np.percentile(a, 50)),
            "p95": float(np.percentile(a, 95)),
        }

    return {
        "trials": int(n_trials),
        "sharpe": stats(arr_s),
        "calmar": stats(arr_c),
        "total_return": stats(arr_r),
        "alpha_t_stat": stats(arr_a),
        "pct_positive_trials": float(np.mean(pos_flags)),
        "pct_alpha_t_gt_1": float(np.mean(arr_a > 1.0)),
    }


# ---------------------------------------------------------------------------
def main() -> int:
    btc = load_btc_with_cpi()
    hourly = load_hourly()

    print("\nDetecting cascade events on full BTC 12-mo window …")
    events = mj.detect_cascades(btc)
    print(f"  {len(events)} raw cascade events")

    print("\nBuilding per-event x per-symbol legs (12 alts) …")
    legs = build_event_legs(btc, events, hourly)
    print(f"  {len(legs)} legs from {legs['event_peak_ts'].nunique()} events with valid entry")

    print("\nAggregating to event-basket level …")
    events_basket = per_event_avg(legs)
    print(f"  {len(events_basket)} basket events")

    # Diagnostic: unweighted (full-capital-per-event, no cap) — matches Gate 1 style
    print("\n[diagnostic] Full-capital-per-event (no cap, mirrors Gate 1 fix) …")
    ew_nocap = events_basket.copy().sort_values("entry_signal_ts").reset_index(drop=True)
    ew_nocap["weight"] = 1.0
    ew_nocap["active_at_entry"] = 1
    ew_nocap["weighted_pnl"] = ew_nocap["basket_net_full_cap"]
    sw_nocap = equity_curve_metrics(ew_nocap, hourly["BTC"])
    print(f"  [nocap] sharpe={sw_nocap['sharpe']:+.3f}  total_ret={sw_nocap['total_return']*100:+.1f}%  "
          f"maxdd={sw_nocap['max_drawdown']*100:+.1f}%  calmar={sw_nocap['calmar']:+.3f}  "
          f"alpha_t={sw_nocap['alpha_t_stat']:+.3f}")

    print("\nAllocating concurrency (aggregate cap = 1.0) …")
    events_w = allocate_concurrency(events_basket)
    print(f"  mean weight = {events_w['weight'].mean():.3f}, "
          f"median = {events_w['weight'].median():.3f}, "
          f"max active concurrent = {events_w['active_at_entry'].max()}")

    print("\nComputing single-window metrics …")
    sw = equity_curve_metrics(events_w, hourly["BTC"])
    for k, v in sw.items():
        if isinstance(v, float):
            print(f"  {k:<28} {v:+.4f}")
        else:
            print(f"  {k:<28} {v}")

    print("\nPer-symbol contribution (unweighted leg returns) …")
    per_sym = per_symbol_contribution(legs)
    for sym, row in sorted(per_sym.items(), key=lambda kv: kv[1]["mean_net"], reverse=True):
        print(f"  {sym:<6} n={row['n_trades']:>3}  mean_net={row['mean_net']*100:+6.2f}%  "
              f"win={row['win_rate']*100:5.1f}%  t={row['t_stat']:+5.2f}")

    print(f"\nBootstrap MC ({BOOTSTRAP_TRIALS} trials, month-stratified) …")
    boot = bootstrap_mc(events_basket, hourly["BTC"])
    print(f"  Sharpe       mean={boot['sharpe']['mean']:+.3f}  "
          f"P5={boot['sharpe']['p5']:+.3f}  P50={boot['sharpe']['p50']:+.3f}  "
          f"P95={boot['sharpe']['p95']:+.3f}")
    print(f"  Calmar       mean={boot['calmar']['mean']:+.3f}  "
          f"P5={boot['calmar']['p5']:+.3f}  P50={boot['calmar']['p50']:+.3f}  "
          f"P95={boot['calmar']['p95']:+.3f}")
    print(f"  TotalReturn  mean={boot['total_return']['mean']*100:+.2f}%  "
          f"P5={boot['total_return']['p5']*100:+.2f}%  P50={boot['total_return']['p50']*100:+.2f}%  "
          f"P95={boot['total_return']['p95']*100:+.2f}%")
    print(f"  alpha_t      mean={boot['alpha_t_stat']['mean']:+.3f}  "
          f"P5={boot['alpha_t_stat']['p5']:+.3f}  P50={boot['alpha_t_stat']['p50']:+.3f}  "
          f"P95={boot['alpha_t_stat']['p95']:+.3f}")
    print(f"  %positive trials = {boot['pct_positive_trials']*100:.1f}%")
    print(f"  %alpha_t>1       = {boot['pct_alpha_t_gt_1']*100:.1f}%")

    # ---- Verdict ----
    checks = {
        "sharpe_gt_1.5":     sw["sharpe"] > 1.5,
        "calmar_gt_3.0":     sw["calmar"] > 3.0,
        "maxdd_gt_-30":      sw["max_drawdown"] > -0.30,
        "alpha_t_gt_1.5":    np.isfinite(sw["alpha_t_stat"]) and sw["alpha_t_stat"] > 1.5,
        "boot_alpha_t_gt_1": boot["alpha_t_stat"]["mean"] > 1.0,
        "boot_pct_pos_gt_60": boot["pct_positive_trials"] > 0.60,
    }
    n_pass = sum(checks.values())
    if n_pass == 6:
        verdict = "PROMOTE"
    elif n_pass >= 4:
        verdict = "NEEDS_TUNING"
    else:
        verdict = "KILL"

    print(f"\n{'='*60}")
    print(f"GATE 2 VERDICT: {verdict}  ({n_pass}/6 checks passed)")
    print(f"{'='*60}")
    for k, v in checks.items():
        print(f"  [{'PASS' if v else 'FAIL'}] {k}")

    results = {
        "config": {
            "universe_basket": BASKET,
            "n_basket_symbols": len(BASKET),
            "cpi_trigger": mj.CPI_TRIGGER,
            "cpi_end": mj.CPI_END,
            "vdv_entry_z_below": mj.VDV_ENTRY_Z_BELOW,
            "time_stop_h": TIME_STOP_H,
            "fee_bps_per_side": 4.0,
            "slippage_bps_per_side": 5.0,
            "round_trip_cost_pct": COST_RT,
            "max_aggregate_exposure": MAX_AGG_EXPOSURE,
            "bootstrap_trials": BOOTSTRAP_TRIALS,
            "rng_seed": RNG_SEED,
        },
        "n_cascade_events_raw": int(len(events)),
        "n_events_with_entry": int(len(events_basket)),
        "single_window": sw,
        "diagnostic_nocap_full_capital": sw_nocap,
        "per_symbol": {k: {kk: float(vv) if isinstance(vv, (int, float, np.floating, np.integer)) else vv
                            for kk, vv in v.items()}
                       for k, v in per_sym.items()},
        "bootstrap_mc": boot,
        "checks": {k: bool(v) for k, v in checks.items()},
        "checks_passed": int(n_pass),
        "verdict": verdict,
    }

    out = OUT_DIR / "mission_j_gate2_results.json"
    with open(out, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nWrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
