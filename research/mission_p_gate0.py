"""
Mission P Gate 0 — Event-Driven, Breadth-Based Direction Cull on s523c.

Refines Mission O which failed because:
1. Daily evaluation = 277 trigger days/year = noise, not regime
2. Mean basket P&L drowned by outliers and dispersion

Mission P fixes both:
- Event-driven trigger gating: only evaluate on days with meaningful regime
  events (vol spike, dispersion compression, decisive move, drawdown peak)
- Breadth measure: % of direction's open positions individually underwater
  (more robust than mean)
- Throttle: once a direction is culled, don't re-cull that direction for N days

Reuses Mission O's snapshot logic + the basket regime cache from
research/s523c_universe_basket.parquet built earlier this session.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path("/workspace/crypto_backtest")
TRADES_PATH = REPO / "results/v4/s523c_growth_12mo_50k_trades.json"
OHLCV_DIR = REPO / "data/perp/binance/1h_ohlcv"
BASKET_PATH = REPO / "research/s523c_universe_basket.parquet"
OUT_REPORT = REPO / "research/mission_p_gate0_report.md"
OUT_RESULTS = REPO / "research/mission_p_gate0_results.json"

LEVERAGE = 2.6
ROUND_TRIP_COST = 0.0008
BACKTEST_END = pd.Timestamp("2026-04-05 16:00", tz="UTC")
BACKTEST_START = BACKTEST_END - pd.Timedelta(days=365)


def load_ohlcv(token: str) -> pd.DataFrame | None:
    path = OHLCV_DIR / f"{token}_perp_1h.csv"
    if not path.exists():
        return None
    df = pd.read_csv(path, parse_dates=["datetime"]).set_index("datetime").sort_index()
    if df.index.tz is not None:
        df.index = df.index.tz_convert(None)
    return df


def main() -> None:
    print("Loading trade log…")
    with open(TRADES_PATH) as f:
        trades_raw = json.load(f)

    trades = []
    for tr in trades_raw:
        entry_dt = (BACKTEST_START + pd.Timedelta(hours=int(tr["entry_bar"]))).tz_localize(None)
        exit_dt = (BACKTEST_START + pd.Timedelta(hours=int(tr["exit_bar"]))).tz_localize(None)
        trades.append({
            "token": tr["token"],
            "direction": int(tr["direction"]),
            "entry_dt": entry_dt,
            "exit_dt": exit_dt,
            "entry_price": float(tr["entry_price"]),
            "pnl_dollars": float(tr["pnl"]),
            "margin_usd": float(tr["margin_usd"]),
        })

    print("Caching OHLCV…")
    tokens = sorted({t["token"] for t in trades})
    ohlcv: dict[str, pd.DataFrame | None] = {tok: load_ohlcv(tok) for tok in tokens}

    def price_at(token: str, dt: pd.Timestamp) -> float | None:
        df = ohlcv.get(token)
        if df is None:
            return None
        try:
            ix = df.index.get_indexer([dt], method="pad")[0]
            if ix < 0:
                return None
            return float(df.iloc[ix]["close"])
        except Exception:
            return None

    # ---- load basket regime features ----
    print("Loading basket regime cache…")
    basket = pd.read_parquet(BASKET_PATH)
    basket.index = pd.to_datetime(basket.index).tz_localize(None) if basket.index.tz is None else pd.to_datetime(basket.index).tz_convert(None)
    basket = basket.sort_index()

    # Restrict to backtest window with some pre-history for rolling stats
    bs = basket.loc[(BACKTEST_START - pd.Timedelta(days=120)).tz_localize(None):]
    # Compute event triggers, all using ONLY trailing data (no look-ahead)
    bs["vol_z90"] = (bs["basket_vol_30d"] - bs["basket_vol_30d"].rolling(90, min_periods=20).mean()) / bs["basket_vol_30d"].rolling(90, min_periods=20).std()
    bs["disp_z90"] = (bs["basket_dispersion_24h"] - bs["basket_dispersion_24h"].rolling(90, min_periods=20).mean()) / bs["basket_dispersion_24h"].rolling(90, min_periods=20).std()
    bs["ret_abs_24h"] = bs["basket_ret_24h"].abs()
    bs["dd_change_3d"] = bs["basket_drawdown_30d"] - bs["basket_drawdown_30d"].shift(3)
    # Lag everything by 1 day to remove same-day look-ahead
    for c in ["vol_z90", "disp_z90", "ret_abs_24h", "basket_ret_24h", "dd_change_3d"]:
        bs[c + "_lag"] = bs[c].shift(1)

    # ---- build daily snapshots ----
    print("Building daily snapshots…")
    days = pd.date_range(BACKTEST_START.tz_localize(None), BACKTEST_END.tz_localize(None), freq="D")

    def build_snapshots(grace_days: int) -> pd.DataFrame:
        rows = []
        for day in days:
            for i, tr in enumerate(trades):
                if tr["entry_dt"] + pd.Timedelta(days=grace_days) > day:
                    continue
                if day >= tr["exit_dt"]:
                    continue
                p = price_at(tr["token"], day)
                if p is None:
                    continue
                cur_ret = (p - tr["entry_price"]) / tr["entry_price"] * tr["direction"]
                rows.append({
                    "day": day,
                    "trade_idx": i,
                    "direction": tr["direction"],
                    "current_pnl_pct_unlev": cur_ret,
                    "current_pnl_pct_lev": cur_ret * LEVERAGE,
                    "current_close_price": p,
                    "underwater": int(cur_ret < 0),
                })
        return pd.DataFrame(rows)

    # ---- event filter library ----
    def event_active(day: pd.Timestamp, event_type: str) -> bool:
        # Snapshot days are at 16:00 (backtest start time); basket index is at midnight
        day_norm = day.normalize()
        if day_norm not in bs.index:
            return False
        row = bs.loc[day_norm]
        if event_type == "vol_spike":
            return bool(row.get("vol_z90_lag", 0) > 1.5)
        if event_type == "disp_compression":
            return bool(row.get("disp_z90_lag", 0) < -1.0)
        if event_type == "decisive_move":
            return bool(row.get("ret_abs_24h_lag", 0) > 0.03)
        if event_type == "dd_deepening":
            return bool(row.get("dd_change_3d_lag", 0) < -0.05)  # DD deepened > 5pp in 3d
        if event_type == "any":
            return any(event_active(day, e) for e in ["vol_spike", "disp_compression", "decisive_move", "dd_deepening"])
        if event_type == "none":
            return True  # always evaluate (control)
        return False

    # ---- simulator ----
    def simulate(
        grace_days: int,
        breadth_threshold: float,
        event_type: str,
        snapshots: pd.DataFrame,
        throttle_days: int = 14,
    ) -> dict:
        if snapshots.empty:
            return {}
        forced_exits: dict[int, dict] = {}
        last_cull_day: dict[int, pd.Timestamp] = {}  # direction → last cull day

        n_long_trigs = 0
        n_short_trigs = 0
        n_event_days = 0

        # iterate over days that have snapshots
        for day, day_snap in snapshots.groupby("day"):
            if not event_active(day, event_type):
                continue
            n_event_days += 1

            for direction in [1, -1]:
                # throttle
                last = last_cull_day.get(direction)
                if last is not None and (day - last).days < throttle_days:
                    continue

                dir_snap = day_snap[day_snap["direction"] == direction]
                if len(dir_snap) < 5:  # need a meaningful sample
                    continue
                breadth = dir_snap["underwater"].mean()
                if breadth < breadth_threshold:
                    continue

                # Cull: force-exit all post-grace trades on this direction not yet forced
                culled = 0
                for _, s in dir_snap.iterrows():
                    idx = int(s["trade_idx"])
                    if idx in forced_exits:
                        continue
                    forced_exits[idx] = {
                        "forced_exit_day": day,
                        "forced_exit_price": s["current_close_price"],
                        "current_pnl_pct_lev": s["current_pnl_pct_lev"],
                    }
                    culled += 1
                if culled > 0:
                    last_cull_day[direction] = day
                    if direction == 1:
                        n_long_trigs += 1
                    else:
                        n_short_trigs += 1

        # ---- score ----
        total_orig = 0.0
        total_new = 0.0
        impacts = []
        for i, tr in enumerate(trades):
            orig = tr["pnl_dollars"]
            total_orig += orig
            if i in forced_exits:
                cf_pct = forced_exits[i]["current_pnl_pct_lev"] - ROUND_TRIP_COST
                cf_dollars = cf_pct * tr["margin_usd"]
                total_new += cf_dollars
                impacts.append(cf_dollars - orig)
            else:
                total_new += orig

        impacts_arr = np.array(impacts) if impacts else np.array([0.0])
        return {
            "grace_days": grace_days,
            "breadth_threshold": breadth_threshold,
            "event_type": event_type,
            "throttle_days": throttle_days,
            "n_event_days": n_event_days,
            "n_long_culls": n_long_trigs,
            "n_short_culls": n_short_trigs,
            "n_trades_force_closed": len(forced_exits),
            "n_helped": int((impacts_arr > 0).sum()),
            "n_hurt": int((impacts_arr < 0).sum()),
            "saved_dollars": float(impacts_arr[impacts_arr > 0].sum()),
            "given_back_dollars": float(impacts_arr[impacts_arr < 0].sum()),
            "median_impact": float(np.median(impacts_arr)),
            "mean_impact": float(np.mean(impacts_arr)),
            "total_orig": float(total_orig),
            "total_new": float(total_new),
            "total_improvement": float(total_new - total_orig),
            "improvement_pct": float((total_new - total_orig) / abs(total_orig)) if total_orig != 0 else 0.0,
        }

    # ---- sweep ----
    print("Running sweep…")
    snaps_3d = build_snapshots(3)
    snaps_5d = build_snapshots(5)

    results: dict = {
        "config": {"leverage": LEVERAGE, "round_trip_cost": ROUND_TRIP_COST},
        "sweep": {},
    }

    sweep = []
    for event_type in ["vol_spike", "disp_compression", "decisive_move", "dd_deepening", "any", "none"]:
        for grace, snaps in [(3, snaps_3d), (5, snaps_5d)]:
            for breadth in [0.65, 0.75, 0.85]:
                for throttle in [7, 14, 21]:
                    r = simulate(grace, breadth, event_type, snaps, throttle)
                    key = f"{event_type}_g{grace}d_br{int(breadth*100)}_th{throttle}d"
                    results["sweep"][key] = r
                    sweep.append((key, r))

    # Best by improvement_pct
    sweep_sorted = sorted(sweep, key=lambda x: x[1]["improvement_pct"], reverse=True)
    best_key, best = sweep_sorted[0]
    results["best"] = {"key": best_key, **best}

    # Top 10
    print("\nTop 10 by improvement %:")
    print(f"{'Variant':<48} {'Δ$':>12} {'Δ%':>8} {'culls':>8} {'helped':>7} {'hurt':>6}")
    for k, r in sweep_sorted[:10]:
        print(f"{k:<48} ${r['total_improvement']:>+11,.0f} {r['improvement_pct']:>+7.1%} "
              f"{r['n_long_culls']+r['n_short_culls']:>8} {r['n_helped']:>7} {r['n_hurt']:>6}")

    # Bottom 5
    print("\nBottom 5:")
    for k, r in sweep_sorted[-5:]:
        print(f"{k:<48} ${r['total_improvement']:>+11,.0f} {r['improvement_pct']:>+7.1%}")

    # ---- verdict ----
    impr = best["improvement_pct"]
    saved_ratio = abs(best["saved_dollars"] / best["given_back_dollars"]) if best["given_back_dollars"] != 0 else float("inf")
    verdict = "KILL"
    reasons: list[str] = []
    if impr >= 0.10 and saved_ratio >= 1.5:
        verdict = "PASS"
        reasons.append(f"Best {best_key}: {impr:+.1%}, saved/given_back {saved_ratio:.2f}x")
    elif impr > 0 and saved_ratio > 1.0:
        verdict = "MARGINAL"
        reasons.append(f"MARGINAL {best_key}: {impr:+.1%}, saved/given_back {saved_ratio:.2f}x")
    else:
        reasons.append(f"Best {best_key}: {impr:+.1%}, saved/given_back {saved_ratio:.2f}x")

    results["verdict"] = verdict
    results["verdict_reasons"] = reasons

    OUT_RESULTS.write_text(json.dumps(results, indent=2, default=str))

    # ---- markdown report ----
    lines: list[str] = []
    lines.append("# Mission P Gate 0 — Event-Driven Breadth-Based Direction Cull")
    lines.append(f"\nGenerated: 2026-04-08")
    lines.append(f"\n**Verdict: {verdict}**\n")
    for r in reasons:
        lines.append(f"- {r}")

    lines.append("\n## Hypothesis (refined Mission O)\n")
    lines.append("Mission O failed because daily evaluation × mean spread = 277 trigger days/year of noise. Mission P fixes both:")
    lines.append("- **Event-driven**: only evaluate on regime-confirming days (vol spike / dispersion compression / decisive move / drawdown deepening)")
    lines.append("- **Breadth not mean**: % of direction's open positions that are individually underwater (robust to outliers)")
    lines.append("- **Throttle**: once a direction is culled, do not re-cull for N days (avoid repeated firing)")

    lines.append("\n## Top 10 variants\n")
    lines.append("| Variant | Δ$ | Δ% | Culls (L+S) | Event days | Helped | Hurt | Saved | Given back |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for k, r in sweep_sorted[:10]:
        lines.append(
            f"| `{k}` | ${r['total_improvement']:+,.0f} | {r['improvement_pct']:+.1%} | "
            f"{r['n_long_culls']+r['n_short_culls']} | {r['n_event_days']} | {r['n_helped']} | {r['n_hurt']} | "
            f"${r['saved_dollars']:+,.0f} | ${r['given_back_dollars']:+,.0f} |"
        )

    lines.append("\n## Best variant detail\n")
    lines.append(f"- Variant: `{best_key}`")
    lines.append(f"- Event type: {best['event_type']}")
    lines.append(f"- Grace: {best['grace_days']}d, Breadth threshold: {best['breadth_threshold']:.0%}, Throttle: {best['throttle_days']}d")
    lines.append(f"- Event days in year: {best['n_event_days']}")
    lines.append(f"- Long culls: {best['n_long_culls']}, Short culls: {best['n_short_culls']}")
    lines.append(f"- Trades force-closed: {best['n_trades_force_closed']}")
    lines.append(f"- Helped: {best['n_helped']}, Hurt: {best['n_hurt']}")
    lines.append(f"- Saved $: ${best['saved_dollars']:+,.0f}")
    lines.append(f"- Given back $: ${best['given_back_dollars']:+,.0f}")
    lines.append(f"- Total improvement: ${best['total_improvement']:+,.0f} ({best['improvement_pct']:+.1%})")

    lines.append("\n## Diagnosis\n")
    if verdict == "PASS":
        lines.append(
            "The event-driven breadth filter produces meaningful improvement at realistic costs. "
            "Recommend Gate 1: validate on multi-year rolling windows + finer event/breadth grid + "
            "test alternative event triggers."
        )
    elif verdict == "MARGINAL":
        lines.append(
            "Edge exists but is too small to commit to engine code. Worth narrower sweep around best "
            "variant before declaring dead — particularly testing with realistic 50bps slippage instead "
            "of the 8bps used here."
        )
    else:
        lines.append(
            "Even with event gating + breadth measure + throttle, no rule combination beats baseline "
            "meaningfully. The token-level dispersion that destroys aggregate signals (the unifying "
            "explanation across G/M/N/O) survives even the sharpest event-driven filter. s523c is "
            "structurally immune to direction-based interference."
        )

    OUT_REPORT.write_text("\n".join(lines))
    print(f"\nVerdict: {verdict}")
    for r in reasons:
        print(f"  {r}")


if __name__ == "__main__":
    main()
