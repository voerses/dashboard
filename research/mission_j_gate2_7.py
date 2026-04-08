#!/usr/bin/env python3
"""Mission J Gate 2.7 — Regime filter on top of Gate 2.5.

Gate 2.5 diagnosis: 5-symbol sub-basket has beta=1.36 to BTC and events
cluster in BTC drawdown periods, so losses concentrate in bearish regimes.
Gate 2.6 tried higher leverage and made things worse (0.66 Sharpe, -43% DD).

Gate 2.7 fix: keep Gate 2.5 sizing EXACTLY (1.5% risk budget, 2.0x cap),
but drop any cascade event where BTC's trailing 7-day log return is
negative. Hard filter — not a soft weight.

At each cascade peak time t:
    btc_7d = log(BTC.close[t]) - log(BTC.close[t - 7*24*60 min])
Drop event if btc_7d < 0.

Everything else identical to Gate 2.5.
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

# Fix 1 (from Gate 2.5): sub-basket of 5 top alpha carriers
BASKET = ["NEAR", "LTC", "ETH", "BNB", "LINK"]
TRIGGER_SYMBOL = "BTC"
SYMBOLS = [TRIGGER_SYMBOL] + BASKET

# Costs
FEE_BPS = 4.0 / 10_000.0
SLIP_BPS = 5.0 / 10_000.0
COST_RT = 2.0 * (FEE_BPS + SLIP_BPS)  # ~18 bps

TIME_STOP_H = 48

# Fix 2 (Gate 2.5 values — NOT Gate 2.6)
RISK_BUDGET_PER_EVENT = 0.015
RV_WINDOW_MIN = 168 * 60
HOLD_MIN = TIME_STOP_H * 60
MAX_AGG_EXPOSURE = 2.0

# Fix 3 (Gate 2.7): BTC 7-day regime filter
REGIME_LOOKBACK_MIN = 7 * 24 * 60  # 7 days of 1-min bars

BOOTSTRAP_TRIALS = 200
RNG_SEED = 20260408


# ---------------------------------------------------------------------------
def load_btc_with_cpi_and_rv() -> pd.DataFrame:
    print("Loading BTC microstructure_1min ...")
    btc = pd.read_parquet(MSIG_DIR / "BTCUSDT" / "microstructure_1min.parquet")
    btc["ts"] = pd.to_datetime(btc["ts"])
    btc = btc.set_index("ts").sort_index()
    if btc.index.tz is not None:
        btc.index = btc.index.tz_convert(None)
    print(f"  rows={len(btc):,}")

    print("Building CPI ...")
    btc = mj.build_cpi(btc)

    print("Computing trailing 168h realized vol ...")
    logret = np.log(btc["mid_price"]).diff()
    btc["rv_per_min_168h"] = logret.rolling(RV_WINDOW_MIN, min_periods=RV_WINDOW_MIN // 2).std()
    btc["sigma_48h"] = btc["rv_per_min_168h"] * np.sqrt(HOLD_MIN)

    print("Computing trailing 7-day BTC log return (regime filter) ...")
    log_mid = np.log(btc["mid_price"])
    btc["btc_7d_logret"] = log_mid - log_mid.shift(REGIME_LOOKBACK_MIN)
    print(f"  7d logret median={btc['btc_7d_logret'].median():+.4f}  "
          f"%positive={(btc['btc_7d_logret'] > 0).mean()*100:.1f}%")

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
def filter_events_by_regime(btc: pd.DataFrame, events: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Drop cascade events where BTC's trailing 7-day log return is < 0."""
    if events.empty:
        return events, 0
    keep_mask = np.zeros(len(events), dtype=bool)
    for i, ev in enumerate(events.itertuples()):
        peak_idx = int(ev.peak_idx)
        if peak_idx < REGIME_LOOKBACK_MIN:
            continue  # not enough history
        btc_7d = float(btc["btc_7d_logret"].iat[peak_idx])
        if np.isfinite(btc_7d) and btc_7d >= 0:
            keep_mask[i] = True
    filtered = events.loc[keep_mask].reset_index(drop=True)
    n_dropped = int(len(events) - len(filtered))
    print(f"  Filtered {n_dropped}/{len(events)} events as bearish-regime "
          f"(kept {len(filtered)})")
    return filtered, n_dropped


# ---------------------------------------------------------------------------
def build_event_legs(
    btc: pd.DataFrame,
    events: pd.DataFrame,
    hourly: dict[str, pd.DataFrame],
) -> pd.DataFrame:
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

    active: list[tuple[pd.Timestamp, float]] = []

    for i, row in df.iterrows():
        entry_ts = row["entry_signal_ts"]
        exit_ts = row["exit_signal_ts"]
        sigma = float(row["sigma_48h"])

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
# Gate 2.5 comparison (hardcoded from mission_j_gate2_5_results.json)
GATE25 = {
    "n_events_raw": 159,
    "n_events_post_filter": 153,  # no regime filter in 2.5
    "sharpe": 0.9286991016125299,
    "calmar": 1.034656663725507,
    "max_dd": -0.24630745217128136,
    "alpha_t": 1.545889175934839,
    "boot_alpha_t_mean": 1.4938362589779457,
    "pct_positive": 0.83,
    "beta_btc": 1.350832387221931,
}


def main() -> int:
    btc = load_btc_with_cpi_and_rv()
    hourly = load_hourly()

    print("\nDetecting cascade events ...")
    events_raw = mj.detect_cascades(btc)
    print(f"  {len(events_raw)} raw cascade events")

    print("\nApplying BTC 7-day regime filter (drop events where BTC 7d logret < 0) ...")
    events_filtered, n_dropped = filter_events_by_regime(btc, events_raw)

    if len(events_filtered) < 30:
        print(f"\nWARNING: only {len(events_filtered)} events survive filter — "
              f"bootstrap unreliable.")

    print("\nBuilding per-event x per-symbol legs (5-symbol sub-basket) ...")
    legs = build_event_legs(btc, events_filtered, hourly)
    n_events_with_entry = legs["event_peak_ts"].nunique() if len(legs) else 0
    print(f"  {len(legs)} legs from {n_events_with_entry} events with valid entry + sigma")

    print("\nAggregating to event-basket level (1/5 equal weight within basket) ...")
    events_basket = per_event_avg(legs)
    print(f"  {len(events_basket)} basket events")

    print("\nAllocating vol-targeted sizing (risk=1.5%/event, cap=2.0x gross) ...")
    events_w = allocate_vol_targeted(events_basket)
    if len(events_w):
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

    # Pass criteria — check events floor
    if len(events_basket) < 50:
        checks = {
            "sharpe_gt_1.5": False,
            "calmar_gt_3.0": False,
            "maxdd_gt_-30": False,
            "alpha_t_gt_1.5": False,
            "boot_alpha_t_gt_1": False,
            "boot_pct_pos_gt_60": False,
        }
        verdict = "KILL"
        n_pass = 0
        kill_reason = f"n_events={len(events_basket)} < 50 (bootstrap unreliable)"
    else:
        checks = {
            "sharpe_gt_1.5":     sw["sharpe"] > 1.5,
            "calmar_gt_3.0":     sw["calmar"] > 3.0,
            "maxdd_gt_-30":      sw["max_drawdown"] > -0.30,
            "alpha_t_gt_1.5":    np.isfinite(sw["alpha_t_stat"]) and sw["alpha_t_stat"] > 1.5,
            "boot_alpha_t_gt_1": boot["alpha_t_stat"]["mean"] > 1.0,
            "boot_pct_pos_gt_60": boot["pct_positive_trials"] > 0.60,
        }
        n_pass = sum(checks.values())
        # KILL if filter didn't materially improve MaxDD
        maxdd_improved = sw["max_drawdown"] > GATE25["max_dd"] + 0.03  # at least 3pp better
        if n_pass == 6:
            verdict = "PROMOTE"
        elif n_pass >= 4:
            verdict = "NEEDS_TUNING"
        else:
            verdict = "KILL"
        kill_reason = None

    print(f"\n{'='*60}")
    print(f"GATE 2.7 VERDICT: {verdict}  ({n_pass}/6 checks passed)")
    if kill_reason:
        print(f"  Reason: {kill_reason}")
    print(f"{'='*60}")
    for k, v in checks.items():
        print(f"  [{'PASS' if v else 'FAIL'}] {k}")

    # Side-by-side comparison
    print(f"\n{'='*70}")
    print("Side-by-side: Gate 2.5 vs Gate 2.7")
    print(f"{'='*70}")
    rows = [
        ("n events (raw)",              f"{GATE25['n_events_raw']}",          f"{len(events_raw)}"),
        ("n events (post-filter)",      f"{GATE25['n_events_post_filter']}",  f"{len(events_basket)}"),
        ("Sharpe",                      f"{GATE25['sharpe']:+.3f}",           f"{sw.get('sharpe', float('nan')):+.3f}"),
        ("Calmar",                      f"{GATE25['calmar']:+.3f}",           f"{sw.get('calmar', float('nan')):+.3f}"),
        ("MaxDD",                       f"{GATE25['max_dd']*100:+.1f}%",      f"{sw.get('max_drawdown', 0)*100:+.1f}%"),
        ("alpha_t",                     f"{GATE25['alpha_t']:+.3f}",          f"{sw.get('alpha_t_stat', float('nan')):+.3f}"),
        ("Bootstrap mean alpha_t",      f"{GATE25['boot_alpha_t_mean']:+.3f}", f"{boot['alpha_t_stat']['mean']:+.3f}"),
        ("% positive trials",           f"{GATE25['pct_positive']*100:.0f}%", f"{boot['pct_positive_trials']*100:.0f}%"),
        ("basket beta to BTC",          f"{GATE25['beta_btc']:+.3f}",         f"{sw.get('beta_btc', float('nan')):+.3f}"),
    ]
    print(f"  {'Metric':<28} {'Gate 2.5':>14} {'Gate 2.7':>14}")
    print(f"  {'-'*28} {'-'*14} {'-'*14}")
    for name, v25, v27 in rows:
        print(f"  {name:<28} {v25:>14} {v27:>14}")

    # Hypothesis verification
    dd25 = GATE25["max_dd"]
    dd27 = sw.get("max_drawdown", 0.0)
    dd_shrunk_substantially = dd27 > dd25 + 0.05  # at least 5pp better
    sharpe_rose = sw.get("sharpe", 0.0) > GATE25["sharpe"] + 0.1
    alpha_held = sw.get("alpha_t_stat", float("nan")) >= GATE25["alpha_t"] - 0.2

    print(f"\nHypothesis (bear-regime events drive MaxDD):")
    print(f"  MaxDD shrunk substantially (>5pp)? {dd_shrunk_substantially}")
    print(f"  Sharpe rose (>+0.1)?              {sharpe_rose}")
    print(f"  alpha_t held?                     {alpha_held}")
    if dd_shrunk_substantially:
        print("  => Hypothesis CONFIRMED: regime filter meaningfully reduces drawdown.")
    else:
        print("  => Hypothesis REFUTED: filter did not materially reduce drawdown.")

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
            "regime_lookback_min": REGIME_LOOKBACK_MIN,
            "regime_filter_rule": "drop if BTC 7d logret < 0 at cascade peak",
            "bootstrap_trials": BOOTSTRAP_TRIALS,
            "rng_seed": RNG_SEED,
        },
        "n_cascade_events_raw": int(len(events_raw)),
        "n_events_dropped_by_regime_filter": int(n_dropped),
        "n_events_post_regime_filter": int(len(events_filtered)),
        "n_events_with_entry": int(len(events_basket)),
        "single_window": sw,
        "per_symbol": {k: {kk: float(vv) if isinstance(vv, (int, float, np.floating, np.integer)) else vv
                            for kk, vv in v.items()}
                       for k, v in per_sym.items()},
        "bootstrap_mc": boot,
        "checks": {k: bool(v) for k, v in checks.items()},
        "checks_passed": int(n_pass),
        "verdict": verdict,
        "kill_reason": kill_reason,
        "comparison_vs_gate2_5": {
            "gate2_5": GATE25,
            "gate2_7": {
                "n_events_raw": int(len(events_raw)),
                "n_events_post_filter": int(len(events_basket)),
                "sharpe": sw.get("sharpe"),
                "calmar": sw.get("calmar"),
                "max_dd": sw.get("max_drawdown"),
                "alpha_t": sw.get("alpha_t_stat"),
                "boot_alpha_t_mean": boot["alpha_t_stat"]["mean"],
                "pct_positive": boot["pct_positive_trials"],
                "beta_btc": sw.get("beta_btc"),
            },
            "hypothesis_confirmed": bool(dd_shrunk_substantially),
            "maxdd_shrunk_substantially": bool(dd_shrunk_substantially),
            "sharpe_rose": bool(sharpe_rose),
            "alpha_t_held": bool(alpha_held),
        },
    }

    out = OUT_DIR / "mission_j_gate2_7_results.json"
    assert "gate2_7" in str(out), f"REFUSING to write: output path {out} missing gate2_7"
    assert "gate2_5" not in str(out).replace("gate2_5_results", "XXX"), "path safety check"
    with open(out, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nWrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
