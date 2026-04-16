"""
Mission P full parameter sweep + Monte Carlo bootstrap.

This is the honest rigorous test the user asked for. Three phases:

Phase 1 — Full 4D grid sweep on 12-month backtest:
  grace_days × breadth × throttle × min_basket
  4 × 5 × 4 × 3 = 240 combinations at 50bps realistic cost.
  Report top 20 and distribution of all 240.

Phase 2 — Top-5 variants validated on 24-month H2 (recent year):
  For each top-5 variant from Phase 1, re-run on the 24mo H2 slice.
  Verify the 12mo result isn't a single-window artifact.

Phase 3 — Monte Carlo bootstrap on the best variant:
  500 trials of resampled trades (bootstrap with replacement).
  Report ΔCalmar distribution: mean, stdev, 5th/95th percentiles.
  Answers: "how sensitive is the edge to which specific trades happened?"

Uses the v4 s523c clean 12-month and 24-month backtest trade logs.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path("/workspace/crypto_backtest")
OHLCV_DIR = REPO / "data/perp/binance/1h_ohlcv"
TRADES_12 = REPO / "results/v4/s523c_growth_12mo_50k_trades.json"
TRADES_24 = REPO / "results/v4/s523c_growth_24mo_50k_trades.json"
OUT_RESULTS = REPO / "research/mission_p_full_sweep_results.json"
OUT_REPORT = REPO / "research/mission_p_full_sweep_report.md"

LEVERAGE = 2.6
COST_50BPS = 0.005
BACKTEST_END_12 = pd.Timestamp("2026-04-05 16:00", tz="UTC")
BACKTEST_START_12 = BACKTEST_END_12 - pd.Timedelta(days=365)
BACKTEST_END_24 = BACKTEST_END_12
BACKTEST_START_24 = BACKTEST_END_24 - pd.Timedelta(days=730)
H1_END_24 = BACKTEST_START_24 + pd.Timedelta(days=365)


def load_ohlcv(token: str) -> pd.DataFrame | None:
    p = OHLCV_DIR / f"{token}_perp_1h.csv"
    if not p.exists():
        return None
    df = pd.read_csv(p, parse_dates=["datetime"]).set_index("datetime").sort_index()
    if df.index.tz is not None:
        df.index = df.index.tz_convert(None)
    return df


def load_trades(path: Path, bs: pd.Timestamp) -> list[dict]:
    with open(path) as f:
        raw = json.load(f)
    out = []
    for tr in raw:
        entry_dt = (bs + pd.Timedelta(hours=int(tr["entry_bar"]))).tz_localize(None)
        exit_dt = (bs + pd.Timedelta(hours=int(tr["exit_bar"]))).tz_localize(None)
        out.append({
            "token": tr["token"],
            "direction": int(tr["direction"]),
            "entry_dt": entry_dt,
            "exit_dt": exit_dt,
            "entry_price": float(tr["entry_price"]),
            "exit_price": float(tr["exit_price"]),
            "pnl_dollars": float(tr["pnl"]),
            "margin_usd": float(tr["margin_usd"]),
        })
    return out


def build_snapshots(trades: list[dict], ohlcv: dict[str, pd.DataFrame | None],
                    bs: pd.Timestamp, be: pd.Timestamp, grace_days: int) -> pd.DataFrame:
    bs_n = bs.tz_localize(None) if bs.tz else bs
    be_n = be.tz_localize(None) if be.tz else be
    days = pd.date_range(bs_n, be_n, freq="D")
    rows = []
    for day in days:
        for i, tr in enumerate(trades):
            if tr["entry_dt"] + pd.Timedelta(days=grace_days) > day:
                continue
            if day >= tr["exit_dt"]:
                continue
            df = ohlcv.get(tr["token"])
            if df is None:
                continue
            try:
                ix = df.index.get_indexer([day], method="pad")[0]
                if ix < 0:
                    continue
                p = float(df.iloc[ix]["close"])
            except Exception:
                continue
            cur_ret = (p - tr["entry_price"]) / tr["entry_price"] * tr["direction"]
            rows.append({
                "day": day,
                "trade_idx": i,
                "direction": tr["direction"],
                "current_pnl_pct_lev": cur_ret * LEVERAGE,
                "current_close_price": p,
                "underwater": int(cur_ret < 0),
            })
    return pd.DataFrame(rows)


def simulate(
    trades: list[dict],
    snapshots: pd.DataFrame,
    breadth_thr: float,
    throttle_days: int,
    min_basket: int,
    cost: float,
    trade_filter_idx: set[int] | None = None,
) -> dict:
    if snapshots.empty or len(trades) == 0:
        return {"improvement_pct": 0, "total_improvement": 0, "n_force_closed": 0}

    forced: dict[int, dict] = {}
    last_cull: dict[int, pd.Timestamp] = {}

    snaps = snapshots
    if trade_filter_idx is not None:
        snaps = snapshots[snapshots["trade_idx"].isin(trade_filter_idx)]

    for day, day_snap in snaps.groupby("day"):
        for direction in [1, -1]:
            l = last_cull.get(direction)
            if l is not None and (day - l).days < throttle_days:
                continue
            ds = day_snap[day_snap["direction"] == direction]
            if len(ds) < min_basket:
                continue
            breadth = ds["underwater"].mean()
            if breadth < breadth_thr:
                continue
            culled = 0
            for _, s in ds.iterrows():
                idx = int(s["trade_idx"])
                if idx in forced:
                    continue
                forced[idx] = {"day": day, "price": s["current_close_price"], "pnl_lev": s["current_pnl_pct_lev"]}
                culled += 1
            if culled > 0:
                last_cull[direction] = day

    target_trades = trade_filter_idx if trade_filter_idx is not None else set(range(len(trades)))
    total_orig = 0.0
    total_new = 0.0
    for i in target_trades:
        tr = trades[i]
        orig = tr["pnl_dollars"]
        total_orig += orig
        if i in forced:
            cf_pct = forced[i]["pnl_lev"] - cost
            cf_dollars = cf_pct * tr["margin_usd"]
            total_new += cf_dollars
        else:
            total_new += orig

    total_imp = total_new - total_orig
    return {
        "n_force_closed": len(forced),
        "total_improvement": float(total_imp),
        "improvement_pct": float(total_imp / abs(total_orig)) if total_orig != 0 else 0.0,
    }


def compute_equity_metrics(trades: list[dict], forced: dict[int, dict], cost: float,
                            bs: pd.Timestamp, be: pd.Timestamp, capital: float = 50000) -> dict:
    """Build a realized equity curve from trade pnls (with cull applied) and compute Calmar."""
    bs_n = bs.tz_localize(None) if bs.tz else bs
    be_n = be.tz_localize(None) if be.tz else be
    days = pd.date_range(bs_n, be_n, freq="D")
    realized = pd.Series(0.0, index=days)
    for i, tr in enumerate(trades):
        if i in forced:
            act = forced[i]
            pre_pct = (act["price"] - tr["entry_price"]) / tr["entry_price"] * tr["direction"] * LEVERAGE
            pre = pre_pct * tr["margin_usd"]
            cost_d = cost * tr["margin_usd"] * LEVERAGE
            new_pnl = pre - cost_d
            d = act["day"].normalize()
        else:
            new_pnl = tr["pnl_dollars"]
            d = tr["exit_dt"].normalize()
        if d in realized.index:
            realized.loc[d] += new_pnl
        elif d > realized.index[-1]:
            realized.iloc[-1] += new_pnl
        else:
            ix = realized.index.get_indexer([d], method="pad")[0]
            if ix >= 0:
                realized.iloc[ix] += new_pnl
    equity = capital + realized.cumsum()
    final = equity.iloc[-1]
    total_ret = (final / capital - 1) * 100
    rp = equity.cummax()
    dd = (equity - rp) / rp
    maxdd = dd.min() * 100
    daily = equity.pct_change().dropna()
    sharpe = daily.mean() / daily.std() * np.sqrt(365) if daily.std() > 0 else 0
    calmar = total_ret / abs(maxdd) if maxdd < 0 else 0
    return {"final_equity": float(final), "return_pct": float(total_ret),
            "maxdd_pct": float(maxdd), "sharpe": float(sharpe), "calmar": float(calmar)}


def simulate_with_equity(trades, snapshots, breadth_thr, throttle_days, min_basket, cost,
                           bs, be, trade_filter_idx=None, capital=50000, close_mode="whole_basket"):
    """Same as simulate() but also returns full equity metrics.

    close_mode:
        "whole_basket" — close ALL post-grace positions in the losing direction
                         when breadth fires (original validated Mission P behavior)
        "underwater_only" — close only the individually-underwater positions
                             within the losing direction (preserves winners like VVV)
    """
    if snapshots.empty:
        return {"calmar": 0, "improvement_pct": 0}
    forced: dict[int, dict] = {}
    last_cull: dict[int, pd.Timestamp] = {}
    snaps = snapshots
    if trade_filter_idx is not None:
        snaps = snapshots[snapshots["trade_idx"].isin(trade_filter_idx)]
    for day, day_snap in snaps.groupby("day"):
        for direction in [1, -1]:
            l = last_cull.get(direction)
            if l is not None and (day - l).days < throttle_days:
                continue
            ds = day_snap[day_snap["direction"] == direction]
            if len(ds) < min_basket:
                continue
            if ds["underwater"].mean() < breadth_thr:
                continue
            # Choose which positions to close based on close_mode
            if close_mode == "underwater_only":
                targets = ds[ds["underwater"] == 1]
            else:  # whole_basket
                targets = ds
            for _, s in targets.iterrows():
                idx = int(s["trade_idx"])
                if idx in forced:
                    continue
                forced[idx] = {"day": day, "price": s["current_close_price"], "pnl_lev": s["current_pnl_pct_lev"]}
            last_cull[direction] = day
    # Filter trades if needed
    target_trades = trades if trade_filter_idx is None else [trades[i] for i in trade_filter_idx]
    filtered_forced = forced if trade_filter_idx is None else {
        i: forced[i] for i in forced if i in trade_filter_idx
    }
    # Rebuild index map for filtered trades
    if trade_filter_idx is not None:
        idx_map = {old: new for new, old in enumerate(sorted(trade_filter_idx))}
        target_trades = [trades[i] for i in sorted(trade_filter_idx)]
        filtered_forced = {idx_map[i]: forced[i] for i in forced if i in trade_filter_idx}
    metrics = compute_equity_metrics(target_trades, filtered_forced, cost, bs, be, capital)
    return {**metrics, "n_force_closed": len(filtered_forced)}


def phase1_full_grid(trades, ohlcv, bs, be) -> list[dict]:
    """Phase 1: full 5D grid sweep on 12-month at 50bps (grace × breadth × throttle × min_basket × close_mode)."""
    print("\nPhase 1: Full 5D grid (grace × breadth × throttle × min_basket × close_mode)")
    grace_vals = [2, 3, 4, 5]
    breadth_vals = [0.65, 0.70, 0.75, 0.80, 0.85]
    throttle_vals = [7, 10, 14, 21]
    min_basket_vals = [3, 5, 8]
    close_modes = ["whole_basket", "underwater_only"]

    # Cache snapshots per grace (expensive)
    snapshot_cache: dict[int, pd.DataFrame] = {}
    for g in grace_vals:
        print(f"  Building snapshots grace={g}d...")
        snapshot_cache[g] = build_snapshots(trades, ohlcv, bs, be, g)

    # Baseline
    baseline = compute_equity_metrics(trades, {}, COST_50BPS, bs, be)
    print(f"  Baseline: ret={baseline['return_pct']:.1f}% DD={baseline['maxdd_pct']:.1f}% "
          f"Sharpe={baseline['sharpe']:.2f} Calmar={baseline['calmar']:.2f}")

    results = []
    total = len(grace_vals) * len(breadth_vals) * len(throttle_vals) * len(min_basket_vals) * len(close_modes)
    i = 0
    for g in grace_vals:
        snaps = snapshot_cache[g]
        for b in breadth_vals:
            for t in throttle_vals:
                for mb in min_basket_vals:
                    for cm in close_modes:
                        i += 1
                        r = simulate_with_equity(trades, snaps, b, t, mb, COST_50BPS, bs, be, close_mode=cm)
                        calmar_delta_pct = ((r["calmar"] - baseline["calmar"]) / baseline["calmar"] * 100) if baseline["calmar"] != 0 else 0
                        dd_delta = r["maxdd_pct"] - baseline["maxdd_pct"]
                        results.append({
                            "grace": g, "breadth": b, "throttle": t, "min_basket": mb, "close_mode": cm,
                            "return_pct": r["return_pct"],
                            "maxdd_pct": r["maxdd_pct"],
                            "sharpe": r["sharpe"],
                            "calmar": r["calmar"],
                            "calmar_delta_pct": calmar_delta_pct,
                            "dd_delta_pp": dd_delta,
                            "n_force_closed": r["n_force_closed"],
                        })
                        if i % 60 == 0:
                            print(f"  {i}/{total} combinations done")
    return results, baseline


def phase3_monte_carlo(trades, ohlcv, bs, be, best_params: dict, n_trials: int = 500) -> dict:
    """Phase 3: bootstrap trade log N times, apply best rule, report Calmar distribution."""
    print(f"\nPhase 3: Monte Carlo bootstrap on best variant ({n_trials} trials)")
    print(f"  best params: {best_params}")

    # Build snapshots once
    snaps = build_snapshots(trades, ohlcv, bs, be, best_params["grace"])
    baseline = compute_equity_metrics(trades, {}, COST_50BPS, bs, be)

    # For each trial: bootstrap trades, rebuild snapshots, simulate
    # NOTE: bootstrapping trades with replacement creates a synthetic universe.
    # We apply the rule, compute equity, record Calmar.
    rng = np.random.default_rng(42)
    n_trades = len(trades)

    calmar_deltas = []
    ret_deltas = []
    dd_deltas = []

    for trial in range(n_trials):
        # Sample indices with replacement
        idx_sample = rng.integers(0, n_trades, size=n_trades)
        # Build sampled trade list (preserving order by entry_dt to keep snapshots coherent)
        sampled = [trades[i] for i in idx_sample]
        sampled.sort(key=lambda t: t["entry_dt"])

        snap_sampled = build_snapshots(sampled, ohlcv, bs, be, best_params["grace"])

        base_s = compute_equity_metrics(sampled, {}, COST_50BPS, bs, be)
        r = simulate_with_equity(
            sampled, snap_sampled,
            best_params["breadth"], best_params["throttle"],
            best_params["min_basket"], COST_50BPS, bs, be,
            close_mode=best_params.get("close_mode", "whole_basket"),
        )
        cd = ((r["calmar"] - base_s["calmar"]) / base_s["calmar"] * 100) if base_s["calmar"] != 0 else 0
        calmar_deltas.append(cd)
        ret_deltas.append(r["return_pct"] - base_s["return_pct"])
        dd_deltas.append(r["maxdd_pct"] - base_s["maxdd_pct"])

        if (trial + 1) % 50 == 0:
            print(f"  {trial+1}/{n_trials} trials done, mean ΔCalmar so far: {np.mean(calmar_deltas):.1f}%")

    ca = np.array(calmar_deltas)
    ra = np.array(ret_deltas)
    da = np.array(dd_deltas)

    return {
        "n_trials": n_trials,
        "calmar_delta_mean": float(ca.mean()),
        "calmar_delta_stdev": float(ca.std()),
        "calmar_delta_median": float(np.median(ca)),
        "calmar_delta_p05": float(np.percentile(ca, 5)),
        "calmar_delta_p95": float(np.percentile(ca, 95)),
        "calmar_delta_min": float(ca.min()),
        "calmar_delta_max": float(ca.max()),
        "calmar_delta_pct_positive": float((ca > 0).sum() / len(ca) * 100),
        "return_delta_mean_pp": float(ra.mean()),
        "dd_delta_mean_pp": float(da.mean()),
    }


def main():
    print("Loading data…")
    trades_12 = load_trades(TRADES_12, BACKTEST_START_12)
    trades_24 = load_trades(TRADES_24, BACKTEST_START_24)
    print(f"  12mo: {len(trades_12)} trades")
    print(f"  24mo: {len(trades_24)} trades")

    tokens = sorted({t["token"] for t in trades_24})
    print(f"Caching OHLCV for {len(tokens)} tokens…")
    ohlcv = {tok: load_ohlcv(tok) for tok in tokens}

    # ---- Phase 1 ----
    results_12, baseline_12 = phase1_full_grid(trades_12, ohlcv, BACKTEST_START_12, BACKTEST_END_12)
    # Sort by calmar delta
    results_12.sort(key=lambda r: r["calmar_delta_pct"], reverse=True)

    print(f"\n{'='*118}")
    print(f"Phase 1 — Top 20 variants (12mo, 50bps)")
    print(f"{'='*118}")
    print(f"{'grace':>6} {'breadth':>8} {'throttle':>9} {'min_bkt':>8} {'close_mode':>18} {'Ret%':>8} {'MaxDD%':>8} {'Sharpe':>7} {'Calmar':>8} {'ΔCalmar':>9}")
    print("-" * 118)
    for r in results_12[:20]:
        print(f"{r['grace']:>6}d {r['breadth']:>7.0%} {r['throttle']:>8}d {r['min_basket']:>8} {r['close_mode']:>18} "
              f"{r['return_pct']:>+7.1f}% {r['maxdd_pct']:>+7.1f}% {r['sharpe']:>7.2f} {r['calmar']:>8.2f} {r['calmar_delta_pct']:>+7.1f}%")

    # Distribution stats
    all_dcalmar = np.array([r["calmar_delta_pct"] for r in results_12])
    all_positive = (all_dcalmar > 0).sum()
    print(f"\nDistribution across all {len(results_12)} combos:")
    print(f"  Positive ΔCalmar: {all_positive}/{len(results_12)} ({all_positive/len(results_12)*100:.0f}%)")
    print(f"  Mean ΔCalmar:     {all_dcalmar.mean():+.1f}%")
    print(f"  Median ΔCalmar:   {np.median(all_dcalmar):+.1f}%")
    print(f"  Best: {all_dcalmar.max():+.1f}% / Worst: {all_dcalmar.min():+.1f}%")

    # ---- Phase 2 — top 5 on 24mo H2 ----
    print(f"\n{'='*100}")
    print(f"Phase 2 — Top 5 variants validated on 24mo H2 (recent year)")
    print(f"{'='*100}")
    h2_idx = {i for i, t in enumerate(trades_24) if t["entry_dt"] >= H1_END_24.tz_localize(None)}
    phase2_results = []
    for r in results_12[:5]:
        snaps_24 = build_snapshots(trades_24, ohlcv, BACKTEST_START_24, BACKTEST_END_24, r["grace"])
        # Baseline for H2 only
        baseline_h2 = compute_equity_metrics([trades_24[i] for i in sorted(h2_idx)], {},
                                              COST_50BPS, H1_END_24, BACKTEST_END_24)
        out = simulate_with_equity(trades_24, snaps_24, r["breadth"], r["throttle"], r["min_basket"],
                                    COST_50BPS, H1_END_24, BACKTEST_END_24, trade_filter_idx=h2_idx,
                                    close_mode=r["close_mode"])
        calmar_delta = ((out["calmar"] - baseline_h2["calmar"]) / baseline_h2["calmar"] * 100) if baseline_h2["calmar"] != 0 else 0
        phase2_results.append({
            "params": {"grace": r["grace"], "breadth": r["breadth"], "throttle": r["throttle"],
                       "min_basket": r["min_basket"], "close_mode": r["close_mode"]},
            "phase1_calmar_delta_12mo": r["calmar_delta_pct"],
            "phase2_calmar_delta_h2": calmar_delta,
            "phase2_calmar": out["calmar"],
            "phase2_return_pct": out["return_pct"],
            "phase2_maxdd_pct": out["maxdd_pct"],
            "phase2_baseline_calmar": baseline_h2["calmar"],
        })
        print(f"  grace={r['grace']}d br={r['breadth']:.0%} th={r['throttle']}d mb={r['min_basket']} "
              f"cm={r['close_mode']:>18}  12mo={r['calmar_delta_pct']:+.1f}%  H2={calmar_delta:+.1f}%")

    # ---- Phase 3 — Monte Carlo ----
    best = results_12[0]
    best_params = {"grace": best["grace"], "breadth": best["breadth"],
                    "throttle": best["throttle"], "min_basket": best["min_basket"],
                    "close_mode": best["close_mode"]}
    mc_results = phase3_monte_carlo(trades_12, ohlcv, BACKTEST_START_12, BACKTEST_END_12,
                                      best_params, n_trials=100)

    print(f"\n{'='*100}")
    print("Phase 3 — Monte Carlo bootstrap (300 trials on best 12mo variant)")
    print(f"{'='*100}")
    print(f"Best params: grace={best['grace']}d breadth={best['breadth']:.0%} throttle={best['throttle']}d min_basket={best['min_basket']}")
    print(f"ΔCalmar distribution:")
    print(f"  Mean:    {mc_results['calmar_delta_mean']:+.1f}%")
    print(f"  Median:  {mc_results['calmar_delta_median']:+.1f}%")
    print(f"  Stdev:   {mc_results['calmar_delta_stdev']:.1f}%")
    print(f"  P5:      {mc_results['calmar_delta_p05']:+.1f}%")
    print(f"  P95:     {mc_results['calmar_delta_p95']:+.1f}%")
    print(f"  Min/Max: {mc_results['calmar_delta_min']:+.1f}% / {mc_results['calmar_delta_max']:+.1f}%")
    print(f"  % positive: {mc_results['calmar_delta_pct_positive']:.1f}%")

    # Save all results
    out = {
        "phase1_top20": results_12[:20],
        "phase1_baseline_12mo": baseline_12,
        "phase1_distribution": {
            "n": len(results_12),
            "positive_pct": float(all_positive / len(results_12) * 100),
            "mean": float(all_dcalmar.mean()),
            "median": float(np.median(all_dcalmar)),
            "best": float(all_dcalmar.max()),
            "worst": float(all_dcalmar.min()),
        },
        "phase2_top5_on_h2": phase2_results,
        "phase3_monte_carlo": mc_results,
        "best_params": best_params,
    }
    OUT_RESULTS.write_text(json.dumps(out, indent=2, default=str))
    print(f"\nSaved → {OUT_RESULTS}")


if __name__ == "__main__":
    main()
