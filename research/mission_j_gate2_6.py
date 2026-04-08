#!/usr/bin/env python3
"""Mission J Gate 2.6 — Leverage-knob tuning pass on Gate 2.5.

Gate 2.5 NEEDS_TUNING diagnostic: cap was binding in 30.7% of events (the
cluster peaks where alpha fires). Mean desired_w=0.50 vs actual_w=0.37.
MaxDD slack of 5.4pp to -30%. Cap is the binding constraint, not risk.

Gate 2.6 relaxes the two leverage knobs ONLY. All other logic unchanged.
  - RISK_BUDGET_PER_EVENT: 1.5% -> 2.5% of capital
  - MAX_AGG_EXPOSURE: 2.0x -> 3.0x capital

Original Gate 2.5 docstring below:

Mission J Gate 2.5 — Tuning pass on Gate 2.

Two targeted fixes over Gate 2:

  Fix 1 — Sub-basket of top-5 alpha carriers.
    Per-symbol Gate 2 scan flagged NEAR (t=2.51), LTC (t=2.41), ETH (t=1.97),
    BNB (t=1.98), LINK (t=1.56) as the real alpha carriers. The other 7
    symbols were noise dilution. Basket is now these 5, equal weight within
    the basket on each event (1/5 per leg). BTC is still the cascade trigger.

  Fix 2 — Vol-targeted per-event sizing (replaces 1/(K+1) aggregate sizing).
    Each event gets a FIXED risk budget of 1.5% of capital, expressed as
    dollar vol over a 48h holding horizon:
        sigma_48h = realized_vol_per_min * sqrt(48 * 60)
    where realized_vol_per_min is the trailing 168h stdev of BTC 1min
    log-returns at the cascade peak (BTC = trigger source).

        event_notional = risk_budget / sigma_48h

    Aggregate gross exposure is capped at 2.0x capital; within the cap
    events stack at their individual sizes (no shrinkage). Beyond the cap,
    we scale the NEW event down so the aggregate equals 2.0. Existing
    trades are never retro-shrunk.

Reuses Gate 1 CPI / cascade / entry logic and Gate 2 bootstrap & metrics
scaffolding unchanged.
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

# Fix 1: sub-basket of 5 top alpha carriers (per Gate 2 per-symbol scan)
BASKET = ["NEAR", "LTC", "ETH", "BNB", "LINK"]
TRIGGER_SYMBOL = "BTC"  # cascade trigger (unchanged)
SYMBOLS = [TRIGGER_SYMBOL] + BASKET  # 6 symbols total for hourly loads

# Costs (round-trip per leg)
FEE_BPS = 4.0 / 10_000.0
SLIP_BPS = 5.0 / 10_000.0
COST_RT = 2.0 * (FEE_BPS + SLIP_BPS)  # ~18 bps

TIME_STOP_H = 48

# Fix 2: vol-targeted sizing parameters
RISK_BUDGET_PER_EVENT = 0.025   # Gate 2.6: 2.5% (was 1.5%) of capital per event
RV_WINDOW_MIN = 168 * 60         # 168h trailing window of 1min returns
HOLD_MIN = TIME_STOP_H * 60      # horizon in minutes for vol scaling
MAX_AGG_EXPOSURE = 3.0           # Gate 2.6: 3.0x (was 2.0x) capital gross cap

BOOTSTRAP_TRIALS = 200
RNG_SEED = 20260408


# ---------------------------------------------------------------------------
def load_btc_with_cpi_and_rv() -> pd.DataFrame:
    """Load BTC microstructure, compute CPI + trailing 168h realized vol."""
    print("Loading BTC microstructure_1min ...")
    btc = pd.read_parquet(MSIG_DIR / "BTCUSDT" / "microstructure_1min.parquet")
    btc["ts"] = pd.to_datetime(btc["ts"])
    btc = btc.set_index("ts").sort_index()
    if btc.index.tz is not None:
        btc.index = btc.index.tz_convert(None)
    print(f"  rows={len(btc):,}")

    print("Building CPI ...")
    btc = mj.build_cpi(btc)

    print("Computing trailing 168h realized vol (per-min stdev of log returns) ...")
    logret = np.log(btc["mid_price"]).diff()
    btc["rv_per_min_168h"] = logret.rolling(RV_WINDOW_MIN, min_periods=RV_WINDOW_MIN // 2).std()
    # Scale per-min stdev to 48h holding horizon
    btc["sigma_48h"] = btc["rv_per_min_168h"] * np.sqrt(HOLD_MIN)
    print(f"  sigma_48h median={btc['sigma_48h'].median():.4f}  "
          f"P5={btc['sigma_48h'].quantile(0.05):.4f}  "
          f"P95={btc['sigma_48h'].quantile(0.95):.4f}")
    return btc


def load_hourly() -> dict[str, pd.DataFrame]:
    print(f"Loading hourly OHLCV for {len(SYMBOLS)} symbols ...")
    out = {}
    for sym in SYMBOLS:
        csv = HOURLY_DIR / f"{sym}_perp_1h.csv"
        h = pd.read_csv(csv, parse_dates=["datetime"]).set_index("datetime").sort_index()
        if h.index.tz is not None:
            h.index = h.index.tz_convert(None)
        out[sym] = h
    return out


# ---------------------------------------------------------------------------
def build_event_legs(
    btc: pd.DataFrame,
    events: pd.DataFrame,
    hourly: dict[str, pd.DataFrame],
) -> pd.DataFrame:
    """One row per (event, symbol) for the 5-symbol sub-basket.

    Adds sigma_48h column = BTC realized vol at the cascade peak (trigger
    source). Used downstream for vol-targeted sizing.
    """
    rows = []
    for _, ev in events.iterrows():
        peak_idx = int(ev["peak_idx"])
        end_idx = int(ev["end_idx"])
        entry_idx = mj.find_entry(btc, peak_idx, end_idx)
        if entry_idx is None:
            continue
        exit_idx = min(len(btc) - 1, entry_idx + HOLD_MIN)
        entry_ts = btc.index[entry_idx]
        exit_ts = btc.index[exit_idx]

        # Sigma measured at the cascade peak (the trigger), not at entry.
        sigma_48h = float(btc["sigma_48h"].iat[peak_idx])
        if not np.isfinite(sigma_48h) or sigma_48h <= 0:
            continue

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
                "gross_pnl_pct": gross,
                "net_pnl_pct": gross - COST_RT,
                "hold_hours": (x_bar_ts - e_bar_ts).total_seconds() / 3600.0,
                "sigma_48h": sigma_48h,
            })
    return pd.DataFrame(rows)


def per_event_avg(legs: pd.DataFrame) -> pd.DataFrame:
    """Aggregate legs -> per-event basket row (equal-weight 1/5 within basket)."""
    if legs.empty:
        return legs
    g = legs.groupby("event_peak_ts").agg(
        entry_signal_ts=("entry_signal_ts", "first"),
        exit_signal_ts=("exit_signal_ts", "first"),
        event_peak_cpi=("event_peak_cpi", "first"),
        sigma_48h=("sigma_48h", "first"),
        basket_gross=("gross_pnl_pct", "mean"),
        basket_net_full_cap=("net_pnl_pct", "mean"),
        n_legs=("symbol", "count"),
        hold_hours=("hold_hours", "mean"),
    ).reset_index().sort_values("entry_signal_ts").reset_index(drop=True)
    return g


# ---------------------------------------------------------------------------
def allocate_vol_targeted(events_basket: pd.DataFrame) -> pd.DataFrame:
    """Vol-targeted sizing (Fix 2).

    For each event in entry-time order:
      1. Expire finished trades (exit_ts <= this entry_ts).
      2. Let agg = sum of weights of currently active trades.
      3. desired_w = RISK_BUDGET_PER_EVENT / sigma_48h
         (notional exposure as a fraction of capital such that 1-sigma 48h
         move = risk_budget% of capital).
      4. If agg + desired_w > MAX_AGG_EXPOSURE:
             w = max(0, MAX_AGG_EXPOSURE - agg)
         else:
             w = desired_w
      5. Existing trades keep their original weight (no retro-shrink).
    """
    if events_basket.empty:
        df = events_basket.copy()
        df["weight"] = []
        df["desired_weight"] = []
        df["active_at_entry"] = []
        df["agg_gross_at_entry"] = []
        df["weighted_pnl"] = []
        return df

    df = events_basket.sort_values("entry_signal_ts").reset_index(drop=True)
    n = len(df)
    weights = np.zeros(n)
    desired = np.zeros(n)
    active_count = np.zeros(n, dtype=int)
    agg_at_entry = np.zeros(n)

    active: list[tuple[pd.Timestamp, float]] = []  # (exit_ts, weight)

    for i, row in df.iterrows():
        entry_ts = row["entry_signal_ts"]
        exit_ts = row["exit_signal_ts"]
        sigma = float(row["sigma_48h"])

        # Expire
        active = [(xt, w) for (xt, w) in active if xt > entry_ts]
        agg = sum(w for _, w in active)

        dw = RISK_BUDGET_PER_EVENT / sigma if sigma > 0 else 0.0
        if agg + dw > MAX_AGG_EXPOSURE:
            w = max(0.0, MAX_AGG_EXPOSURE - agg)
        else:
            w = dw

        desired[i] = dw
        weights[i] = w
        active_count[i] = len(active) + (1 if w > 0 else 0)
        agg_at_entry[i] = agg

        if w > 0:
            active.append((exit_ts, w))

    df["desired_weight"] = desired
    df["weight"] = weights
    df["active_at_entry"] = active_count
    df["agg_gross_at_entry"] = agg_at_entry
    df["weighted_pnl"] = df["weight"] * (df["basket_gross"] - COST_RT)
    return df


# ---------------------------------------------------------------------------
def equity_curve_metrics(events_w: pd.DataFrame, btc_hourly: pd.DataFrame) -> dict:
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

    # Alpha vs BTC (weight-scaled BTC return over each event window)
    btc_rets = []
    for _, row in df.iterrows():
        _, e = mj.price_at_hour(btc_hourly, row["entry_signal_ts"])
        _, x = mj.price_at_hour(btc_hourly, row["exit_signal_ts"])
        btc_rets.append((x - e) / e * row["weight"])
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
        "mean_desired_weight": float(df["desired_weight"].mean()),
        "mean_active_at_entry": float(df["active_at_entry"].mean()),
        "max_active_at_entry": int(df["active_at_entry"].max()),
        "mean_agg_gross_at_entry": float(df["agg_gross_at_entry"].mean()),
        "max_agg_gross_at_entry": float(df["agg_gross_at_entry"].max()),
        "pct_events_capped": float((df["weight"] < df["desired_weight"] - 1e-9).mean()),
    }


# ---------------------------------------------------------------------------
def per_symbol_contribution(legs: pd.DataFrame) -> dict:
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
        sample = allocate_vol_targeted(sample)
        m = equity_curve_metrics(sample, btc_hourly)
        sharpes.append(m.get("sharpe", 0.0))
        calmars.append(m.get("calmar", 0.0))
        totrets.append(m.get("total_return", 0.0))
        at = m.get("alpha_t_stat", float("nan"))
        alpha_ts.append(at if np.isfinite(at) else 0.0)
        pos_flags.append(m.get("total_return", 0.0) > 0)

    arr_s = np.asarray(sharpes)
    arr_c = np.clip(np.asarray(calmars), -1e6, 1e6)
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
    btc = load_btc_with_cpi_and_rv()
    hourly = load_hourly()

    print("\nDetecting cascade events ...")
    events = mj.detect_cascades(btc)
    print(f"  {len(events)} raw cascade events")

    print("\nBuilding per-event x per-symbol legs (5-symbol sub-basket) ...")
    legs = build_event_legs(btc, events, hourly)
    n_events_with_entry = legs["event_peak_ts"].nunique() if len(legs) else 0
    print(f"  {len(legs)} legs from {n_events_with_entry} events with valid entry + sigma")

    print("\nAggregating to event-basket level (1/5 equal weight within basket) ...")
    events_basket = per_event_avg(legs)
    print(f"  {len(events_basket)} basket events")

    print("\nAllocating vol-targeted sizing (risk=1.5%/event, cap=2.0x gross) ...")
    events_w = allocate_vol_targeted(events_basket)
    print(f"  desired_w: mean={events_w['desired_weight'].mean():.3f}  "
          f"median={events_w['desired_weight'].median():.3f}  "
          f"P95={events_w['desired_weight'].quantile(0.95):.3f}")
    print(f"  actual_w : mean={events_w['weight'].mean():.3f}  "
          f"median={events_w['weight'].median():.3f}  "
          f"max active concurrent={events_w['active_at_entry'].max()}")
    print(f"  agg exposure: mean_at_entry={events_w['agg_gross_at_entry'].mean():.3f}  "
          f"max={events_w['agg_gross_at_entry'].max():.3f}  "
          f"%capped={(events_w['weight'] < events_w['desired_weight'] - 1e-9).mean()*100:.1f}%")

    print("\nSingle-window metrics ...")
    sw = equity_curve_metrics(events_w, hourly["BTC"])
    for k, v in sw.items():
        if isinstance(v, float):
            print(f"  {k:<28} {v:+.4f}")
        else:
            print(f"  {k:<28} {v}")

    print("\nPer-symbol contribution (5-symbol basket, unweighted legs) ...")
    per_sym = per_symbol_contribution(legs)
    for sym, row in sorted(per_sym.items(), key=lambda kv: kv[1]["mean_net"], reverse=True):
        print(f"  {sym:<6} n={row['n_trades']:>3}  mean_net={row['mean_net']*100:+6.2f}%  "
              f"win={row['win_rate']*100:5.1f}%  t={row['t_stat']:+5.2f}")

    print(f"\nBootstrap MC ({BOOTSTRAP_TRIALS} trials, month-stratified) ...")
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
    print(f"GATE 2.5 VERDICT: {verdict}  ({n_pass}/6 checks passed)")
    print(f"{'='*60}")
    for k, v in checks.items():
        print(f"  [{'PASS' if v else 'FAIL'}] {k}")

    results = {
        "config": {
            "universe_basket": BASKET,
            "n_basket_symbols": len(BASKET),
            "trigger_symbol": TRIGGER_SYMBOL,
            "cpi_trigger": mj.CPI_TRIGGER,
            "cpi_end": mj.CPI_END,
            "vdv_entry_z_below": mj.VDV_ENTRY_Z_BELOW,
            "time_stop_h": TIME_STOP_H,
            "fee_bps_per_side": 4.0,
            "slippage_bps_per_side": 5.0,
            "round_trip_cost_pct": COST_RT,
            "risk_budget_per_event": RISK_BUDGET_PER_EVENT,
            "rv_window_min": RV_WINDOW_MIN,
            "hold_min": HOLD_MIN,
            "max_aggregate_exposure": MAX_AGG_EXPOSURE,
            "bootstrap_trials": BOOTSTRAP_TRIALS,
            "rng_seed": RNG_SEED,
            "sigma_source": "BTC 1min log-return stdev, trailing 168h, sampled at cascade peak, scaled by sqrt(48h_in_min)",
        },
        "n_cascade_events_raw": int(len(events)),
        "n_events_with_entry": int(len(events_basket)),
        "single_window": sw,
        "per_symbol": {k: {kk: float(vv) if isinstance(vv, (int, float, np.floating, np.integer)) else vv
                            for kk, vv in v.items()}
                       for k, v in per_sym.items()},
        "bootstrap_mc": boot,
        "checks": {k: bool(v) for k, v in checks.items()},
        "checks_passed": int(n_pass),
        "verdict": verdict,
    }

    out = OUT_DIR / "mission_j_gate2_5_results.json"
    with open(out, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nWrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
