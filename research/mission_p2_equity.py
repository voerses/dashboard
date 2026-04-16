"""
Mission P.2 — Compute equity curves + MaxDD for each variant on H2.

Builds REALIZED daily equity curves (running sum of trade exit pnls) for:
- Baseline (no rule)
- Mission P (close losers 100%)
- Variant B +50% lever winners (alone)
- Variant B +100% lever winners (alone)
- Variant C combined (close losers 100% + lever winners +50%)
- Variant C extreme (close losers 100% + lever winners +100%)

For trades touched by the rule, the closed/added portions realize P&L at the
ACTION day, not the exit day. Cost on the touched portion is applied at action day.

Reports: total return, MaxDD, Sharpe, Calmar for each variant.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path("/workspace/crypto_backtest")
TRADES_PATH_24 = REPO / "results/v4/s523c_growth_24mo_50k_trades.json"
OHLCV_DIR = REPO / "data/perp/binance/1h_ohlcv"
OUT_REPORT = REPO / "research/mission_p2_equity_report.md"
OUT_RESULTS = REPO / "research/mission_p2_equity_results.json"

LEVERAGE = 2.6
COST_8BPS = 0.0008
COST_50BPS = 0.005
BACKTEST_END = pd.Timestamp("2026-04-05 16:00", tz="UTC")
BACKTEST_START = BACKTEST_END - pd.Timedelta(days=730)
H1_END = BACKTEST_START + pd.Timedelta(days=365)


def load_ohlcv(token):
    p = OHLCV_DIR / f"{token}_perp_1h.csv"
    if not p.exists():
        return None
    df = pd.read_csv(p, parse_dates=["datetime"]).set_index("datetime").sort_index()
    if df.index.tz is not None:
        df.index = df.index.tz_convert(None)
    return df


def compute_metrics(equity: pd.Series, initial: float = 50000) -> dict:
    """Total return, MaxDD, Sharpe (daily annualized), Calmar."""
    if len(equity) < 2:
        return {"total_return_pct": 0, "maxdd_pct": 0, "sharpe": 0, "calmar": 0, "final_equity": initial}
    final = equity.iloc[-1]
    total_ret = (final / initial - 1) * 100
    rp = equity.cummax()
    dd = (equity - rp) / rp
    maxdd_pct = dd.min() * 100
    daily = equity.pct_change().dropna()
    sharpe = daily.mean() / daily.std() * np.sqrt(365) if daily.std() > 0 else 0
    calmar = total_ret / abs(maxdd_pct) if maxdd_pct < 0 else 0
    return {
        "total_return_pct": float(total_ret),
        "final_equity": float(final),
        "maxdd_pct": float(maxdd_pct),
        "sharpe": float(sharpe),
        "calmar": float(calmar),
    }


def build_equity_curve(trade_events: list[tuple[pd.Timestamp, float]], initial: float = 50000) -> pd.Series:
    """Sort events by date, compound running equity."""
    if not trade_events:
        return pd.Series([initial], index=[BACKTEST_START.tz_localize(None)])
    events = sorted(trade_events, key=lambda x: x[0])
    # Build daily series
    days = pd.date_range(BACKTEST_START.tz_localize(None), BACKTEST_END.tz_localize(None), freq="D")
    eq = pd.Series(0.0, index=days)
    for dt, pnl in events:
        # Round dt to date boundary (use floor to day)
        d = dt.normalize()
        if d in eq.index:
            eq.loc[d] += pnl
        elif d > eq.index[-1]:
            eq.iloc[-1] += pnl
        else:
            # Find nearest day
            ix = eq.index.get_indexer([d], method="pad")[0]
            if ix >= 0:
                eq.iloc[ix] += pnl
    eq = initial + eq.cumsum()
    return eq


def main() -> None:
    print("Loading 24-month trade log…")
    with open(TRADES_PATH_24) as f:
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
            "exit_price": float(tr["exit_price"]),
            "pnl_dollars": float(tr["pnl"]),
            "margin_usd": float(tr["margin_usd"]),
        })

    h2_idx = set(i for i, t in enumerate(trades) if t["entry_dt"] >= H1_END.tz_localize(None))
    print(f"  H2: {len(h2_idx)} trades")

    print("Caching OHLCV…")
    tokens = sorted({t["token"] for t in trades})
    ohlcv: dict[str, pd.DataFrame | None] = {tok: load_ohlcv(tok) for tok in tokens}

    def price_at(token, dt):
        df = ohlcv.get(token)
        if df is None:
            return None
        try:
            ix = df.index.get_indexer([dt], method="pad")[0]
            return float(df.iloc[ix]["close"]) if ix >= 0 else None
        except Exception:
            return None

    days = pd.date_range(BACKTEST_START.tz_localize(None), BACKTEST_END.tz_localize(None), freq="D")

    # Build snapshots once for grace=3d
    print("Building snapshots (grace=3d)…")
    rows = []
    for day in days:
        for i, tr in enumerate(trades):
            if tr["entry_dt"] + pd.Timedelta(days=3) > day:
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
                "current_pnl_pct_lev": cur_ret * LEVERAGE,
                "current_close_price": p,
                "underwater": int(cur_ret < 0),
            })
    snaps = pd.DataFrame(rows)
    print(f"  {len(snaps)} snapshots")

    def detect_actions(
        snapshots: pd.DataFrame,
        breadth_thr_lose: float,
        breadth_thr_win: float,
        throttle_days: int,
        deleverage_frac_loser: float,
        leverage_boost_winner: float,
    ) -> dict[int, dict]:
        forced: dict[int, dict] = {}
        last_lose: dict[int, pd.Timestamp] = {}
        last_win: dict[int, pd.Timestamp] = {}
        for day, day_snap in snapshots.groupby("day"):
            for direction in [1, -1]:
                ds = day_snap[day_snap["direction"] == direction]
                if len(ds) < 5:
                    continue
                breadth_uw = ds["underwater"].mean()
                breadth_w = 1.0 - breadth_uw

                if deleverage_frac_loser > 0:
                    l = last_lose.get(direction)
                    if (l is None or (day - l).days >= throttle_days) and breadth_uw >= breadth_thr_lose:
                        culled = 0
                        for _, s in ds.iterrows():
                            idx = int(s["trade_idx"])
                            if idx in forced:
                                continue
                            forced[idx] = {
                                "action": "deleverage",
                                "frac": deleverage_frac_loser,
                                "day": day,
                                "price": s["current_close_price"],
                            }
                            culled += 1
                        if culled > 0:
                            last_lose[direction] = day

                if leverage_boost_winner > 0:
                    l = last_win.get(direction)
                    if (l is None or (day - l).days >= throttle_days) and breadth_w >= breadth_thr_win:
                        for _, s in ds.iterrows():
                            idx = int(s["trade_idx"])
                            if idx in forced:
                                continue
                            if s["underwater"]:
                                continue
                            forced[idx] = {
                                "action": "lever_up",
                                "frac": leverage_boost_winner,
                                "day": day,
                                "price": s["current_close_price"],
                            }
                        last_win[direction] = day
        return forced

    def variant_to_events(forced: dict, cost: float, only_h2: bool = True) -> list[tuple[pd.Timestamp, float]]:
        events = []
        for i, tr in enumerate(trades):
            if only_h2 and i not in h2_idx:
                continue
            if i in forced:
                act = forced[i]
                d = act["day"]
                pre_pct = (act["price"] - tr["entry_price"]) / tr["entry_price"] * tr["direction"] * LEVERAGE
                pre = pre_pct * tr["margin_usd"]
                post = tr["pnl_dollars"] - pre
                f = act["frac"]
                cost_dollars = cost * tr["margin_usd"] * f * LEVERAGE
                if act["action"] == "deleverage":
                    # Closed fraction f at day D
                    realized_at_D = f * pre - cost_dollars
                    realized_at_exit = (1 - f) * (pre + post)
                    events.append((d, realized_at_D))
                    events.append((tr["exit_dt"], realized_at_exit))
                elif act["action"] == "lever_up":
                    # Added fraction f at day D
                    realized_at_D = -cost_dollars
                    realized_at_exit = (pre + post) + f * post
                    events.append((d, realized_at_D))
                    events.append((tr["exit_dt"], realized_at_exit))
            else:
                events.append((tr["exit_dt"], tr["pnl_dollars"]))
        return events

    # ============================================================
    # COMPUTE VARIANTS
    # ============================================================
    print("\nComputing equity curves for each variant…")

    # Baseline: no rule
    baseline_forced = {}
    baseline_events = variant_to_events(baseline_forced, COST_8BPS)
    baseline_eq = build_equity_curve(baseline_events)
    baseline_metrics = compute_metrics(baseline_eq)

    # Mission P (close 100% losers only)
    mp_forced = detect_actions(snaps, 0.70, 1.01, 10, 1.0, 0.0)
    mp_events = variant_to_events(mp_forced, COST_8BPS)
    mp_eq = build_equity_curve(mp_events)
    mp_metrics = compute_metrics(mp_eq)

    # Variant B +50%
    vb50_forced = detect_actions(snaps, 1.01, 0.75, 10, 0.0, 0.5)
    vb50_events = variant_to_events(vb50_forced, COST_8BPS)
    vb50_eq = build_equity_curve(vb50_events)
    vb50_metrics = compute_metrics(vb50_eq)

    # Variant B +100%
    vb100_forced = detect_actions(snaps, 1.01, 0.75, 10, 0.0, 1.0)
    vb100_events = variant_to_events(vb100_forced, COST_8BPS)
    vb100_eq = build_equity_curve(vb100_events)
    vb100_metrics = compute_metrics(vb100_eq)

    # Variant C: close 100% losers + lever +50% winners
    vc50_forced = detect_actions(snaps, 0.70, 0.75, 10, 1.0, 0.5)
    vc50_events = variant_to_events(vc50_forced, COST_8BPS)
    vc50_eq = build_equity_curve(vc50_events)
    vc50_metrics = compute_metrics(vc50_eq)

    # Variant C extreme: close 100% losers + lever +100% winners
    vc100_forced = detect_actions(snaps, 0.70, 0.75, 10, 1.0, 1.0)
    vc100_events = variant_to_events(vc100_forced, COST_8BPS)
    vc100_eq = build_equity_curve(vc100_events)
    vc100_metrics = compute_metrics(vc100_eq)

    print("\n" + "=" * 90)
    print("EQUITY-CURVE METRICS (recent year H2 only, $50K starting, realized P&L curve)")
    print("=" * 90)
    print(f"{'Variant':<48} {'Final $':>11} {'Ret %':>8} {'MaxDD':>9} {'Sharpe':>8} {'Calmar':>8}")
    print("-" * 90)
    for label, m in [
        ("baseline (no rule)", baseline_metrics),
        ("Mission P (close losers 100%)", mp_metrics),
        ("Variant B alone: lever winners +50%", vb50_metrics),
        ("Variant B alone: lever winners +100%", vb100_metrics),
        ("Variant C: close 100% + lever +50%", vc50_metrics),
        ("Variant C: close 100% + lever +100%", vc100_metrics),
    ]:
        print(f"{label:<48} ${m['final_equity']:>10,.0f} {m['total_return_pct']:>+7.1f}% {m['maxdd_pct']:>+8.1f}% {m['sharpe']:>8.2f} {m['calmar']:>8.2f}")

    # ============================================================
    # CALMAR DELTA
    # ============================================================
    print("\n" + "=" * 90)
    print("CALMAR DELTA vs baseline")
    print("=" * 90)
    base_calmar = baseline_metrics["calmar"]
    for label, m in [
        ("Mission P (close losers 100%)", mp_metrics),
        ("Variant B alone +50%", vb50_metrics),
        ("Variant B alone +100%", vb100_metrics),
        ("Variant C +50%", vc50_metrics),
        ("Variant C +100%", vc100_metrics),
    ]:
        delta = ((m["calmar"] - base_calmar) / base_calmar * 100) if base_calmar != 0 else 0
        ret_delta = m["total_return_pct"] - baseline_metrics["total_return_pct"]
        dd_delta = m["maxdd_pct"] - baseline_metrics["maxdd_pct"]
        print(f"{label:<48} ΔCalmar {delta:>+7.1f}%  ΔRet {ret_delta:>+7.1f}pp  ΔMaxDD {dd_delta:>+7.1f}pp")

    results = {
        "baseline": baseline_metrics,
        "mission_p_close_100": mp_metrics,
        "variant_b_lever_50": vb50_metrics,
        "variant_b_lever_100": vb100_metrics,
        "variant_c_combined_50": vc50_metrics,
        "variant_c_combined_100": vc100_metrics,
    }
    OUT_RESULTS.write_text(json.dumps(results, indent=2, default=str))
    print(f"\nSaved → {OUT_RESULTS}")


if __name__ == "__main__":
    main()
