"""
Mission O Gate 0 — Portfolio-level direction cull on s523c.

Hypothesis: if after a grace period, the average current P&L of all open
LONG positions diverges sharply from the average current P&L of all open
SHORT positions, the regime decisively favors one direction. Close all
positions in the losing-direction basket.

This is a PORTFOLIO-LEVEL signal, not per-trade. Different from Mission G
(which cut winners) and Mission N (which cut individual losers by time).

Counterfactual: simulate the rule on the clean 12-month s523c backtest by
re-pricing every open trade on every calendar day, computing the
long-basket vs short-basket spread, and exiting losing-direction baskets
on divergence days.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path("/workspace/crypto_backtest")
TRADES_PATH = REPO / "results/v4/s523c_growth_12mo_50k_trades.json"
OHLCV_DIR = REPO / "data/perp/binance/1h_ohlcv"
OUT_REPORT = REPO / "research/mission_o_gate0_report.md"
OUT_RESULTS = REPO / "research/mission_o_gate0_results.json"

LEVERAGE = 2.6
ROUND_TRIP_COST = 0.0008
BACKTEST_END = pd.Timestamp("2026-04-05 16:00", tz="UTC")
BACKTEST_START = BACKTEST_END - pd.Timedelta(days=365)


def load_ohlcv_naive(token: str) -> pd.DataFrame | None:
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
    print(f"  {len(trades_raw)} trades")

    # ---- normalize trade list ----
    trades = []
    for tr in trades_raw:
        entry_bar = int(tr["entry_bar"])
        exit_bar = int(tr["exit_bar"])
        entry_dt = (BACKTEST_START + pd.Timedelta(hours=entry_bar)).tz_localize(None)
        exit_dt = (BACKTEST_START + pd.Timedelta(hours=exit_bar)).tz_localize(None)
        trades.append({
            "token": tr["token"],
            "direction": int(tr["direction"]),
            "entry_dt": entry_dt,
            "exit_dt": exit_dt,
            "entry_price": float(tr["entry_price"]),
            "exit_price": float(tr["exit_price"]),
            "pnl_dollars": float(tr["pnl"]),
            "margin_usd": float(tr["margin_usd"]),
        })

    # ---- cache OHLCV per token ----
    print("Caching OHLCV…")
    tokens = sorted({t["token"] for t in trades})
    ohlcv: dict[str, pd.DataFrame | None] = {}
    for tok in tokens:
        ohlcv[tok] = load_ohlcv_naive(tok)
    n_with_data = sum(1 for v in ohlcv.values() if v is not None)
    print(f"  {n_with_data}/{len(tokens)} tokens have OHLCV")

    # ---- helper: price lookup ----
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

    # ---- build daily snapshots ----
    # For each calendar day in the backtest, list the (trade_idx, current_pnl_pct, direction)
    # for all currently open post-grace trades
    print("Building daily snapshots…")
    days = pd.date_range(BACKTEST_START.tz_localize(None), BACKTEST_END.tz_localize(None), freq="D")

    def build_snapshots(grace_days: int) -> pd.DataFrame:
        rows = []
        for day in days:
            for i, tr in enumerate(trades):
                # Open and post-grace at this day?
                if tr["entry_dt"] + pd.Timedelta(days=grace_days) > day:
                    continue
                if day >= tr["exit_dt"]:
                    continue
                p = price_at(tr["token"], day)
                if p is None:
                    continue
                # Unleveraged price return × direction (positive = trade in profit)
                cur_ret = (p - tr["entry_price"]) / tr["entry_price"] * tr["direction"]
                rows.append({
                    "day": day,
                    "trade_idx": i,
                    "direction": tr["direction"],
                    "current_pnl_pct_unlev": cur_ret,
                    "current_pnl_pct_lev": cur_ret * LEVERAGE,
                    "current_close_price": p,
                })
        return pd.DataFrame(rows)

    # ---- simulator: apply rule and compute counterfactual pnl ----
    def simulate(grace_days: int, divergence_threshold: float, snapshots: pd.DataFrame) -> dict:
        """Returns metrics dict.
        Rule: each day, compute mean leveraged pnl_pct of post-grace longs and
        post-grace shorts. If long_mean - short_mean > threshold, close all
        post-grace shorts at that day's close (the losing direction). If
        short_mean - long_mean > threshold, close all post-grace longs.
        Once closed, a trade does not get reopened.
        """
        if snapshots.empty:
            return {"n_baskets_closed": 0, "n_trades_closed": 0, "total_orig": 0, "total_new": 0}

        # Aggregate by day, direction
        daily = (
            snapshots.groupby(["day", "direction"])["current_pnl_pct_lev"]
            .agg(["mean", "count"])
            .reset_index()
        )
        daily_wide = daily.pivot(index="day", columns="direction", values="mean").fillna(0)
        if 1 not in daily_wide.columns:
            daily_wide[1] = 0
        if -1 not in daily_wide.columns:
            daily_wide[-1] = 0
        daily_wide.columns = ["short_mean" if c == -1 else "long_mean" for c in daily_wide.columns]
        daily_wide["spread"] = daily_wide["long_mean"] - daily_wide["short_mean"]

        # Detect trigger days
        # Rule: spread > +threshold → close shorts; spread < -threshold → close longs
        # Avoid double-firing: per direction, only the first trigger across the trade's life matters
        forced_exits: dict[int, dict] = {}  # trade_idx → {forced_exit_day, forced_exit_price}

        for day, row in daily_wide.iterrows():
            spread = row["spread"]
            losing_dir = None
            if spread > divergence_threshold:
                losing_dir = -1  # close shorts
            elif spread < -divergence_threshold:
                losing_dir = 1   # close longs
            if losing_dir is None:
                continue
            # Find all post-grace trades on losing direction still open & not yet forced
            day_snap = snapshots[(snapshots["day"] == day) & (snapshots["direction"] == losing_dir)]
            for _, s in day_snap.iterrows():
                idx = int(s["trade_idx"])
                if idx in forced_exits:
                    continue
                forced_exits[idx] = {
                    "forced_exit_day": day,
                    "forced_exit_price": s["current_close_price"],
                    "current_pnl_pct_lev": s["current_pnl_pct_lev"],
                }

        # Compute counterfactual pnl
        total_orig = 0.0
        total_new = 0.0
        n_helped = 0
        n_hurt = 0
        n_long_baskets = sum(1 for d, r in daily_wide.iterrows() if r["spread"] < -divergence_threshold)
        n_short_baskets = sum(1 for d, r in daily_wide.iterrows() if r["spread"] > divergence_threshold)
        impacts = []
        for i, tr in enumerate(trades):
            orig = tr["pnl_dollars"]
            total_orig += orig
            if i in forced_exits:
                # Counterfactual pnl: leveraged pct return × margin − cost
                cf_pct = forced_exits[i]["current_pnl_pct_lev"] - ROUND_TRIP_COST
                cf_dollars = cf_pct * tr["margin_usd"]
                total_new += cf_dollars
                impact = cf_dollars - orig
                impacts.append(impact)
                if impact > 0:
                    n_helped += 1
                elif impact < 0:
                    n_hurt += 1
            else:
                total_new += orig

        impacts_arr = np.array(impacts)
        return {
            "grace_days": grace_days,
            "divergence_threshold": divergence_threshold,
            "n_long_basket_triggers": int(n_long_baskets),
            "n_short_basket_triggers": int(n_short_baskets),
            "n_trades_force_closed": len(forced_exits),
            "n_helped": n_helped,
            "n_hurt": n_hurt,
            "median_impact_dollars": float(np.median(impacts_arr)) if len(impacts_arr) > 0 else 0.0,
            "mean_impact_dollars": float(np.mean(impacts_arr)) if len(impacts_arr) > 0 else 0.0,
            "saved_dollars": float(impacts_arr[impacts_arr > 0].sum()) if len(impacts_arr) > 0 else 0.0,
            "given_back_dollars": float(impacts_arr[impacts_arr < 0].sum()) if len(impacts_arr) > 0 else 0.0,
            "total_orig_pnl": float(total_orig),
            "total_new_pnl": float(total_new),
            "total_improvement": float(total_new - total_orig),
            "improvement_pct_of_baseline": float((total_new - total_orig) / abs(total_orig)) if total_orig != 0 else 0.0,
        }

    # ---- sweep ----
    results: dict = {
        "config": {
            "leverage": LEVERAGE,
            "round_trip_cost": ROUND_TRIP_COST,
        },
        "by_grace_and_threshold": {},
    }

    grace_options = [2, 3, 5]
    threshold_options = [0.03, 0.05, 0.08, 0.12, 0.18]

    for g in grace_options:
        snaps = build_snapshots(g)
        print(f"Grace {g}d → {len(snaps)} (day, trade) snapshots")
        for t in threshold_options:
            r = simulate(g, t, snaps)
            key = f"grace_{g}d_thr_{int(t*100)}pct"
            results["by_grace_and_threshold"][key] = r

    # ---- pick best ----
    best_key, best = max(
        results["by_grace_and_threshold"].items(),
        key=lambda x: x[1]["improvement_pct_of_baseline"],
    )
    results["best"] = {"key": best_key, **best}

    # ---- verdict ----
    verdict = "KILL"
    reasons: list[str] = []
    impr_pct = best["improvement_pct_of_baseline"]
    saved_ratio = (
        abs(best["saved_dollars"] / best["given_back_dollars"])
        if best["given_back_dollars"] != 0
        else float("inf")
    )

    if impr_pct >= 0.05 and saved_ratio >= 1.5:
        verdict = "PASS"
        reasons.append(f"Best {best_key}: improvement {impr_pct:+.1%}, saved/given_back {saved_ratio:.2f}x")
    elif impr_pct > 0:
        verdict = "MARGINAL"
        reasons.append(f"Best {best_key}: improvement {impr_pct:+.1%} (positive but below 5% threshold), saved/given_back {saved_ratio:.2f}x")
    else:
        reasons.append(f"Best {best_key}: improvement {impr_pct:+.1%}, saved/given_back {saved_ratio:.2f}x — net negative")

    results["verdict"] = verdict
    results["verdict_reasons"] = reasons

    OUT_RESULTS.write_text(json.dumps(results, indent=2, default=str))

    # ---- markdown report ----
    lines: list[str] = []
    lines.append("# Mission O Gate 0 — Portfolio-Level Direction Cull")
    lines.append(f"\nGenerated: 2026-04-08")
    lines.append(f"\n**Verdict: {verdict}**\n")
    for r in reasons:
        lines.append(f"- {r}")

    lines.append("\n## Hypothesis\n")
    lines.append(
        "After a grace period, look at the aggregate current P&L of all open LONG positions vs all open SHORT positions. "
        "If one direction's basket is decisively winning while the other is decisively losing, the regime favors one direction. "
        "Close all post-grace positions in the LOSING direction as a single action. Preserves the winning direction entirely."
    )

    lines.append("\n## Sweep results\n")
    lines.append("| Grace | Threshold | Long basket trigs | Short basket trigs | Trades force-closed | Helped | Hurt | Saved $ | Given-back $ | Total Δ$ | Δ% baseline |")
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for key, r in results["by_grace_and_threshold"].items():
        lines.append(
            f"| {r['grace_days']}d | {r['divergence_threshold']*100:.0f}% | "
            f"{r['n_long_basket_triggers']} | {r['n_short_basket_triggers']} | "
            f"{r['n_trades_force_closed']} | {r['n_helped']} | {r['n_hurt']} | "
            f"${r['saved_dollars']:+,.0f} | ${r['given_back_dollars']:+,.0f} | "
            f"${r['total_improvement']:+,.0f} | {r['improvement_pct_of_baseline']:+.1%} |"
        )

    lines.append(f"\n## Best variant\n")
    lines.append(f"`{best_key}`: improvement {impr_pct:+.1%}, saved/given_back ratio {saved_ratio:.2f}x")
    lines.append(f"\n- Long-basket close triggers: {best['n_long_basket_triggers']} days")
    lines.append(f"- Short-basket close triggers: {best['n_short_basket_triggers']} days")
    lines.append(f"- Trades force-closed: {best['n_trades_force_closed']} of {len(trades)}")
    lines.append(f"- Helped: {best['n_helped']}, hurt: {best['n_hurt']}")
    lines.append(f"- Saved: ${best['saved_dollars']:+,.0f}, Given back: ${best['given_back_dollars']:+,.0f}")
    lines.append(f"- Total improvement: ${best['total_improvement']:+,.0f}")

    lines.append("\n## Diagnosis\n")
    if verdict == "PASS":
        lines.append(
            "The portfolio-level direction-cull rule produces meaningful improvement at realistic costs. "
            "Recommend Gate 1: parameter sweep on a wider grid + multi-year rolling window validation + "
            "alternative trigger formulations (median basket pnl, percentile breadth, dispersion-weighted)."
        )
    elif verdict == "MARGINAL":
        lines.append(
            "Edge exists but is too small to commit. The signal is real (the losing-direction basket "
            "really does keep losing) but the magnitude doesn't justify a new exit handler. Worth "
            "exploring at Gate 1 with a narrower threshold sweep before declaring dead."
        )
    else:
        lines.append(
            "The portfolio-level long-basket vs short-basket spread does NOT produce a profitable cull rule. "
            "Either the spread isn't decisive enough on the actual losing days, or the few cases where it IS "
            "decisive include trades that would have recovered. s523c's directional balance is well-calibrated "
            "and the existing exits handle the asymmetry adequately."
        )

    OUT_REPORT.write_text("\n".join(lines))
    print(f"\nVerdict: {verdict}")
    for r in reasons:
        print(f"  {r}")
    print(f"Best: {best_key}")
    print(f"  Total improvement: ${best['total_improvement']:+,.0f} ({impr_pct:+.1%})")
    print(f"  Saved: ${best['saved_dollars']:+,.0f}, Given-back: ${best['given_back_dollars']:+,.0f}")


if __name__ == "__main__":
    main()
