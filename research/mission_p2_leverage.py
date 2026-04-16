"""
Mission P.2 — Leverage adjustment variants of Mission P direction cull.

Tests three variants on the 24-month s523c trade log (validated H2 = recent year):

Variant A — Soft deleverage of losers:
  When breadth threshold fires for losing direction, close PARTIAL position
  (25% / 50% / 75% / 100%). Preserves recovery upside on trades that bounce.

Variant B — Lever up winners:
  When the winning direction's breadth crosses high threshold (≥75% winning),
  add to those positions at current price (1.25x / 1.5x / 2.0x leverage boost).
  Lean into the winning regime.

Variant C — Combined: deleverage losers AND lever up winners on same trigger.

Mechanism:
  For each trade, split P&L into pre-action and post-action segments using
  the day-D price snapshot.
    - Pre-D PnL = (price_at_D - entry_price)/entry_price × dir × LEV × margin
    - Post-D PnL = original_pnl - pre_D
  Apply leverage adjustment:
    - Deleverage by f: new_pnl = pre_D + post_D × (1 - f) - cost on the f portion
    - Lever up by f: new_pnl = pre_D + post_D × (1 + f) - cost on the f portion
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path("/workspace/crypto_backtest")
TRADES_PATH_24 = REPO / "results/v4/s523c_growth_24mo_50k_trades.json"
OHLCV_DIR = REPO / "data/perp/binance/1h_ohlcv"
OUT_REPORT = REPO / "research/mission_p2_report.md"
OUT_RESULTS = REPO / "research/mission_p2_results.json"

LEVERAGE = 2.6
COST_8BPS = 0.0008
COST_50BPS = 0.005
BACKTEST_END = pd.Timestamp("2026-04-05 16:00", tz="UTC")
BACKTEST_START = BACKTEST_END - pd.Timedelta(days=730)
H1_END = BACKTEST_START + pd.Timedelta(days=365)


def load_ohlcv(token: str) -> pd.DataFrame | None:
    p = OHLCV_DIR / f"{token}_perp_1h.csv"
    if not p.exists():
        return None
    df = pd.read_csv(p, parse_dates=["datetime"]).set_index("datetime").sort_index()
    if df.index.tz is not None:
        df.index = df.index.tz_convert(None)
    return df


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

    h2_idx = [i for i, t in enumerate(trades) if t["entry_dt"] >= H1_END.tz_localize(None)]
    print(f"  H2 (recent year): {len(h2_idx)} trades")

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

    def build_snapshots(grace_days):
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
                    "current_pnl_pct_lev": cur_ret * LEVERAGE,
                    "current_close_price": p,
                    "underwater": int(cur_ret < 0),
                })
        return pd.DataFrame(rows)

    def split_pnl(trade_idx: int, action_day: pd.Timestamp, action_price: float) -> tuple[float, float]:
        """Split a trade's $ pnl into pre-action and post-action segments at the action day."""
        tr = trades[trade_idx]
        pre_pct = (action_price - tr["entry_price"]) / tr["entry_price"] * tr["direction"] * LEVERAGE
        pre_dollars = pre_pct * tr["margin_usd"]
        post_dollars = tr["pnl_dollars"] - pre_dollars
        return pre_dollars, post_dollars

    # ---- simulator with pluggable action ----
    def simulate(
        grace_days: int,
        breadth_thr_lose: float,
        breadth_thr_win: float,
        throttle_days: int,
        snapshots: pd.DataFrame,
        cost: float,
        deleverage_frac_loser: float,  # 0=no action, 0.5=close 50%, 1.0=full close (Mission P baseline)
        leverage_boost_winner: float,  # 0=no action, 0.5=lever up by 1.5x, 1.0=lever up by 2.0x
        trade_filter_idx: list[int] | None = None,
    ) -> dict:
        forced_actions: dict[int, dict] = {}  # trade_idx → {action, day, price, pre, post}
        last_cull_lose: dict[int, pd.Timestamp] = {}
        last_lever_win: dict[int, pd.Timestamp] = {}

        snaps = snapshots
        if trade_filter_idx is not None:
            allowed = set(trade_filter_idx)
            snaps = snapshots[snapshots["trade_idx"].isin(allowed)]

        for day, day_snap in snaps.groupby("day"):
            for direction in [1, -1]:
                ds = day_snap[day_snap["direction"] == direction]
                if len(ds) < 5:
                    continue
                breadth_uw = ds["underwater"].mean()
                breadth_winning = 1.0 - breadth_uw

                # Loser cull/deleverage check
                if deleverage_frac_loser > 0:
                    l = last_cull_lose.get(direction)
                    if (l is None or (day - l).days >= throttle_days) and breadth_uw >= breadth_thr_lose:
                        culled = 0
                        for _, s in ds.iterrows():
                            idx = int(s["trade_idx"])
                            if idx in forced_actions:
                                continue
                            forced_actions[idx] = {
                                "action": "deleverage",
                                "frac": deleverage_frac_loser,
                                "day": day,
                                "price": s["current_close_price"],
                            }
                            culled += 1
                        if culled > 0:
                            last_cull_lose[direction] = day

                # Winner lever-up check
                if leverage_boost_winner > 0:
                    l = last_lever_win.get(direction)
                    if (l is None or (day - l).days >= throttle_days) and breadth_winning >= breadth_thr_win:
                        levered = 0
                        for _, s in ds.iterrows():
                            idx = int(s["trade_idx"])
                            if idx in forced_actions:
                                continue
                            # Only lever up trades that are individually winning at this snapshot
                            if s["underwater"]:
                                continue
                            forced_actions[idx] = {
                                "action": "lever_up",
                                "frac": leverage_boost_winner,
                                "day": day,
                                "price": s["current_close_price"],
                            }
                            levered += 1
                        if levered > 0:
                            last_lever_win[direction] = day

        # ---- score ----
        if trade_filter_idx is None:
            target_trades = list(range(len(trades)))
        else:
            target_trades = trade_filter_idx

        total_orig = 0.0
        total_new = 0.0
        impacts: list[float] = []
        n_deleverage = 0
        n_lever_up = 0

        for i in target_trades:
            tr = trades[i]
            orig = tr["pnl_dollars"]
            total_orig += orig

            if i in forced_actions:
                act = forced_actions[i]
                pre, post = split_pnl(i, act["day"], act["price"])
                if act["action"] == "deleverage":
                    # close fraction f at day → post pnl scaled by (1 - f), cost on the closed portion
                    f = act["frac"]
                    new_pnl = pre + post * (1 - f) - cost * tr["margin_usd"] * f * LEVERAGE
                    n_deleverage += 1
                elif act["action"] == "lever_up":
                    # add fraction f at day → post pnl scaled by (1 + f), cost on the added portion
                    f = act["frac"]
                    new_pnl = pre + post * (1 + f) - cost * tr["margin_usd"] * f * LEVERAGE
                    n_lever_up += 1
                else:
                    new_pnl = orig
                total_new += new_pnl
                impacts.append(new_pnl - orig)
            else:
                total_new += orig

        impacts_arr = np.array(impacts) if impacts else np.array([0.0])
        return {
            "n_trades_in_set": len(target_trades),
            "n_deleverage_actions": n_deleverage,
            "n_lever_up_actions": n_lever_up,
            "n_helped": int((impacts_arr > 0).sum()),
            "n_hurt": int((impacts_arr < 0).sum()),
            "saved": float(impacts_arr[impacts_arr > 0].sum()),
            "given_back": float(impacts_arr[impacts_arr < 0].sum()),
            "total_orig": float(total_orig),
            "total_new": float(total_new),
            "total_improvement": float(total_new - total_orig),
            "improvement_pct": float((total_new - total_orig) / abs(total_orig)) if total_orig != 0 else 0.0,
        }

    snaps_3d = build_snapshots(3)
    print(f"\n{len(snaps_3d)} (day, trade) snapshots")

    # ============================================================
    # SWEEP — focus on RECENT YEAR (H2) since user weights it more
    # ============================================================
    print("\n" + "=" * 92)
    print("VARIANT A — Soft deleverage losers (vary fraction, fix breadth=70% throttle=10d)")
    print("=" * 92)
    print(f"{'fraction':<12} {'Δ$ (H2 8bps)':>15} {'Δ% (H2)':>10} {'Δ$ (H2 50bps)':>15} {'Δ% (H2)':>10} {'helped':>8} {'hurt':>8}")
    variant_a = {}
    for f in [0.25, 0.50, 0.75, 1.00]:
        r8 = simulate(3, 0.70, 1.01, 10, snaps_3d, COST_8BPS, f, 0.0, h2_idx)
        r50 = simulate(3, 0.70, 1.01, 10, snaps_3d, COST_50BPS, f, 0.0, h2_idx)
        variant_a[f] = {"r8": r8, "r50": r50}
        print(f"close {int(f*100)}%   ${r8['total_improvement']:>+13,.0f} {r8['improvement_pct']:>+9.1%} "
              f"${r50['total_improvement']:>+13,.0f} {r50['improvement_pct']:>+9.1%} "
              f"{r8['n_helped']:>8} {r8['n_hurt']:>8}")

    print("\n" + "=" * 92)
    print("VARIANT B — Lever up winners (vary boost, fix breadth_win=75% throttle=10d, no loser action)")
    print("=" * 92)
    print(f"{'boost':<12} {'Δ$ (H2 8bps)':>15} {'Δ% (H2)':>10} {'Δ$ (H2 50bps)':>15} {'Δ% (H2)':>10} {'helped':>8} {'hurt':>8}")
    variant_b = {}
    for b in [0.25, 0.50, 0.75, 1.00]:
        r8 = simulate(3, 1.01, 0.75, 10, snaps_3d, COST_8BPS, 0.0, b, h2_idx)
        r50 = simulate(3, 1.01, 0.75, 10, snaps_3d, COST_50BPS, 0.0, b, h2_idx)
        variant_b[b] = {"r8": r8, "r50": r50}
        print(f"+{int(b*100)}%       ${r8['total_improvement']:>+13,.0f} {r8['improvement_pct']:>+9.1%} "
              f"${r50['total_improvement']:>+13,.0f} {r50['improvement_pct']:>+9.1%} "
              f"{r8['n_helped']:>8} {r8['n_hurt']:>8}")

    print("\n" + "=" * 92)
    print("VARIANT C — COMBINED (deleverage losers + lever up winners on same trigger)")
    print("=" * 92)
    print(f"{'lose_f':<8} {'win_f':<8} {'Δ$ (H2 8bps)':>15} {'Δ% (H2)':>10} {'helped':>8} {'hurt':>8} {'delev':>8} {'lever':>8}")
    variant_c = {}
    for lf in [0.50, 1.00]:
        for wf in [0.25, 0.50]:
            r8 = simulate(3, 0.70, 0.75, 10, snaps_3d, COST_8BPS, lf, wf, h2_idx)
            variant_c[(lf, wf)] = r8
            print(f"{int(lf*100):>5}%   {int(wf*100):>5}%   ${r8['total_improvement']:>+13,.0f} {r8['improvement_pct']:>+9.1%} "
                  f"{r8['n_helped']:>8} {r8['n_hurt']:>8} {r8['n_deleverage_actions']:>8} {r8['n_lever_up_actions']:>8}")

    print("\n" + "=" * 92)
    print("BASELINE — Mission P best (full close, no lever up) on H2 for comparison")
    print("=" * 92)
    r_base = simulate(3, 0.70, 1.01, 10, snaps_3d, COST_8BPS, 1.0, 0.0, h2_idx)
    print(f"Mission P (close 100%): Δ${r_base['total_improvement']:+,.0f} ({r_base['improvement_pct']:+.1%}), "
          f"helped={r_base['n_helped']}, hurt={r_base['n_hurt']}")

    # Save
    results = {
        "baseline_mission_p": r_base,
        "variant_a_deleverage": {str(k): v for k, v in variant_a.items()},
        "variant_b_lever_winners": {str(k): v for k, v in variant_b.items()},
        "variant_c_combined": {f"{lf}_{wf}": v for (lf, wf), v in variant_c.items()},
    }
    OUT_RESULTS.write_text(json.dumps(results, indent=2, default=str))
    print(f"\nSaved → {OUT_RESULTS}")


if __name__ == "__main__":
    main()
