"""
s523d — s523c_growth + Mission P breadth direction cull (post-hoc backtest tool).

Runs the standard s523c_growth backtest, then applies the validated Mission P
breadth-cull rule post-hoc and outputs a modified trade log + equity curve in
the same format as results/v4/.

This is a Gate 3 prototype tool — no engine changes, no blast radius. The cull
rule uses MTM-based breadth (the validated signal from Mission P Gate 1), which
requires post-hoc replay of the trade log against per-day OHLCV. Engine
implementation (Path 2) comes later, after Gate 3 results are confirmed.

VALIDATED PARAMETERS (from Mission P Gate 1):
  - Grace: 3 days post-entry (matches s523c no_stop_bars)
  - Trigger: ≥70% of one direction's post-grace open positions individually underwater
  - Action: close 100% of that direction's post-grace positions
  - Throttle: 14 days between culls per direction
  - Note: s523c excludes BTC. The cull operates only on s523c's traded universe.

Usage:
  /workspace/venv/bin/python tools/s523d_run_with_cull.py --months 12
  /workspace/venv/bin/python tools/s523d_run_with_cull.py --months 24
  /workspace/venv/bin/python tools/s523d_run_with_cull.py --months 12 --breadth 0.80 --throttle 14
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path("/workspace/crypto_backtest")
OHLCV_DIR = REPO / "data/perp/binance/1h_ohlcv"
RESULTS_DIR = REPO / "results/v4"

LEVERAGE = 2.6  # s523c default
# Defaults match the Gate 1 validated + Monte Carlo survivor parameters
# (bootstrap mean ΔCalmar +6.5%, 60% positive across 80 trials).
# Do NOT change without re-running the Monte Carlo validation.
DEFAULT_GRACE_DAYS = 3
DEFAULT_BREADTH = 0.80  # Gate 1 validated — breadth threshold for direction cull
DEFAULT_THROTTLE_DAYS = 14
DEFAULT_COST_BPS = 50  # realistic alt-perp basket-close slippage estimate


def run_baseline_backtest(months: int, capital: int, end_date: str) -> Path:
    """Run the standard s523c_growth backtest if not already cached."""
    trades_path = RESULTS_DIR / f"s523c_growth_{months}mo_{capital//1000}k_trades.json"
    if trades_path.exists():
        print(f"[skip] baseline already exists: {trades_path}")
        return trades_path

    print(f"Running baseline s523c_growth backtest ({months}mo, ${capital//1000}K)…")
    cmd = [
        "/workspace/venv/bin/python", "v4/portfolio_backtest.py",
        "--strategy", "s523c_growth",
        "--months", str(months),
        "--capital", str(capital),
        "--market", "perp",
        "--conviction-mode", "ranked",
        "--max-portfolio-positions", "30",
        "--concentration", "0.30",
        "--skip-wf",
        "--end-date", end_date,
    ]
    res = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True)
    if res.returncode != 0:
        print(res.stderr)
        raise RuntimeError("baseline backtest failed")
    print(res.stdout[-800:])
    return trades_path


def load_ohlcv(token: str) -> pd.DataFrame | None:
    p = OHLCV_DIR / f"{token}_perp_1h.csv"
    if not p.exists():
        return None
    df = pd.read_csv(p, parse_dates=["datetime"]).set_index("datetime").sort_index()
    if df.index.tz is not None:
        df.index = df.index.tz_convert(None)
    return df


def apply_breadth_cull(
    trades_raw: list[dict],
    backtest_start: pd.Timestamp,
    backtest_end: pd.Timestamp,
    grace_days: int,
    breadth_thr: float,
    throttle_days: int,
    cost_bps: float,
) -> tuple[list[dict], dict]:
    """Apply Mission P breadth direction cull post-hoc.
    Returns modified trade list and metadata about the rule's actions.
    """
    bs = backtest_start.tz_localize(None) if backtest_start.tz else backtest_start
    be = backtest_end.tz_localize(None) if backtest_end.tz else backtest_end
    cost = cost_bps / 10000

    trades = []
    for tr in trades_raw:
        entry_dt = bs + pd.Timedelta(hours=int(tr["entry_bar"]))
        exit_dt = bs + pd.Timedelta(hours=int(tr["exit_bar"]))
        trades.append({
            **tr,
            "_entry_dt": entry_dt,
            "_exit_dt": exit_dt,
            "_pnl_orig": float(tr["pnl"]),
            "_margin": float(tr["margin_usd"]),
            "_entry_price": float(tr["entry_price"]),
            "_direction": int(tr["direction"]),
            "_token": tr["token"],
        })

    print(f"Caching OHLCV for {len({t['_token'] for t in trades})} tokens…")
    tokens = sorted({t["_token"] for t in trades})
    ohlcv: dict[str, pd.DataFrame | None] = {tok: load_ohlcv(tok) for tok in tokens}
    n_have = sum(1 for v in ohlcv.values() if v is not None)
    print(f"  {n_have}/{len(tokens)} tokens have OHLCV")

    def price_at(token: str, dt: pd.Timestamp) -> float | None:
        df = ohlcv.get(token)
        if df is None:
            return None
        try:
            ix = df.index.get_indexer([dt], method="pad")[0]
            return float(df.iloc[ix]["close"]) if ix >= 0 else None
        except Exception:
            return None

    # Build daily snapshots
    print(f"Building daily snapshots (grace={grace_days}d)…")
    days = pd.date_range(bs, be, freq="D")
    snapshot_rows = []
    for day in days:
        for i, tr in enumerate(trades):
            if tr["_entry_dt"] + pd.Timedelta(days=grace_days) > day:
                continue
            if day >= tr["_exit_dt"]:
                continue
            p = price_at(tr["_token"], day)
            if p is None:
                continue
            cur_ret = (p - tr["_entry_price"]) / tr["_entry_price"] * tr["_direction"]
            snapshot_rows.append({
                "day": day,
                "trade_idx": i,
                "direction": tr["_direction"],
                "current_close_price": p,
                "current_pnl_pct_lev": cur_ret * LEVERAGE,
                "underwater": int(cur_ret < 0),
            })
    snaps = pd.DataFrame(snapshot_rows)
    print(f"  {len(snaps)} (day, trade) snapshots")

    # Detect cull events
    print(f"Detecting culls (breadth ≥ {breadth_thr:.0%}, throttle {throttle_days}d)…")
    forced_exits: dict[int, dict] = {}
    last_cull: dict[int, pd.Timestamp] = {}
    cull_events = []

    for day, day_snap in snaps.groupby("day"):
        for direction in [1, -1]:
            l = last_cull.get(direction)
            if l is not None and (day - l).days < throttle_days:
                continue
            ds = day_snap[day_snap["direction"] == direction]
            if len(ds) < 5:
                continue
            breadth = ds["underwater"].mean()
            if breadth < breadth_thr:
                continue
            culled = 0
            culled_idxs = []
            for _, s in ds.iterrows():
                idx = int(s["trade_idx"])
                if idx in forced_exits:
                    continue
                forced_exits[idx] = {
                    "day": day,
                    "price": s["current_close_price"],
                    "pnl_pct_lev": s["current_pnl_pct_lev"],
                }
                culled += 1
                culled_idxs.append(idx)
            if culled > 0:
                last_cull[direction] = day
                cull_events.append({
                    "day": str(day.date()),
                    "direction": "long" if direction == 1 else "short",
                    "breadth": float(breadth),
                    "n_culled": culled,
                    "n_in_basket": int(len(ds)),
                })

    print(f"  {len(cull_events)} cull events, {len(forced_exits)} trades force-closed")

    # Apply cull to trade pnls
    modified_trades = []
    for i, tr in enumerate(trades):
        new_tr = {k: v for k, v in tr.items() if not k.startswith("_")}
        if i in forced_exits:
            act = forced_exits[i]
            pre_pct = (act["price"] - tr["_entry_price"]) / tr["_entry_price"] * tr["_direction"] * LEVERAGE
            pre = pre_pct * tr["_margin"]
            cost_dollars = cost * tr["_margin"] * LEVERAGE  # full close, all on cost
            new_pnl = pre - cost_dollars
            new_tr["pnl"] = str(new_pnl)
            new_tr["exit_bar"] = int((act["day"] - bs).total_seconds() / 3600)
            new_tr["exit_price"] = act["price"]
            new_tr["exit_reason"] = "breadth_cull"
            new_tr["hold_hours"] = new_tr["exit_bar"] - int(tr["entry_bar"])
        modified_trades.append(new_tr)

    meta = {
        "n_trades_total": len(trades),
        "n_trades_force_closed": len(forced_exits),
        "n_cull_events": len(cull_events),
        "cull_events": cull_events,
        "params": {
            "grace_days": grace_days,
            "breadth_threshold": breadth_thr,
            "throttle_days": throttle_days,
            "cost_bps": cost_bps,
        },
    }
    return modified_trades, meta


def compute_metrics_from_trades(
    modified_trades: list[dict],
    backtest_start: pd.Timestamp,
    backtest_end: pd.Timestamp,
    initial_capital: int,
) -> tuple[dict, pd.Series]:
    """Build a realized equity curve and compute portfolio metrics."""
    bs = backtest_start.tz_localize(None) if backtest_start.tz else backtest_start
    be = backtest_end.tz_localize(None) if backtest_end.tz else backtest_end

    days = pd.date_range(bs, be, freq="D")
    realized = pd.Series(0.0, index=days)
    for tr in modified_trades:
        exit_dt = bs + pd.Timedelta(hours=int(tr["exit_bar"]))
        d = exit_dt.normalize()
        if d in realized.index:
            realized.loc[d] += float(tr["pnl"])
        elif d > realized.index[-1]:
            realized.iloc[-1] += float(tr["pnl"])
        else:
            ix = realized.index.get_indexer([d], method="pad")[0]
            if ix >= 0:
                realized.iloc[ix] += float(tr["pnl"])

    equity = initial_capital + realized.cumsum()
    final = equity.iloc[-1]
    total_ret = (final / initial_capital - 1) * 100
    rp = equity.cummax()
    dd = (equity - rp) / rp
    maxdd = dd.min() * 100
    daily = equity.pct_change().dropna()
    sharpe = daily.mean() / daily.std() * np.sqrt(365) if daily.std() > 0 else 0
    calmar = total_ret / abs(maxdd) if maxdd < 0 else 0

    n_winners = sum(1 for tr in modified_trades if float(tr["pnl"]) > 0)
    win_rate = n_winners / len(modified_trades) if modified_trades else 0
    total_pnl = sum(float(tr["pnl"]) for tr in modified_trades)

    metrics = {
        "initial_capital": initial_capital,
        "final_equity": float(final),
        "total_return_pct": float(total_ret),
        "max_drawdown_pct": float(maxdd),
        "sharpe": float(sharpe),
        "calmar": float(calmar),
        "total_trades": len(modified_trades),
        "win_rate": float(win_rate),
        "total_pnl": float(total_pnl),
    }
    return metrics, equity


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", type=int, default=12)
    ap.add_argument("--capital", type=int, default=50000)
    ap.add_argument("--end-date", default="2026-04-05T16:00:00")
    ap.add_argument("--grace", type=int, default=DEFAULT_GRACE_DAYS)
    ap.add_argument("--breadth", type=float, default=DEFAULT_BREADTH)
    ap.add_argument("--throttle", type=int, default=DEFAULT_THROTTLE_DAYS)
    ap.add_argument("--cost-bps", type=float, default=DEFAULT_COST_BPS)
    args = ap.parse_args()

    end_date = pd.Timestamp(args.end_date, tz="UTC")
    start_date = end_date - pd.Timedelta(days=args.months * 30 + (5 if args.months == 12 else 0))
    # Use exact 365/730 days to match v4 backtest convention
    if args.months == 12:
        start_date = end_date - pd.Timedelta(days=365)
    elif args.months == 24:
        start_date = end_date - pd.Timedelta(days=730)

    # 1) Run baseline (or use cached)
    baseline_trades_path = run_baseline_backtest(args.months, args.capital, args.end_date)

    with open(baseline_trades_path) as f:
        baseline_trades = json.load(f)
    print(f"\nLoaded baseline: {len(baseline_trades)} trades")

    # 2) Compute baseline metrics
    print("\n" + "=" * 80)
    print("BASELINE (s523c_growth)")
    print("=" * 80)
    base_metrics, base_equity = compute_metrics_from_trades(
        baseline_trades, start_date, end_date, args.capital
    )
    for k, v in base_metrics.items():
        if isinstance(v, float):
            if "pct" in k:
                print(f"  {k:<25} {v:>+10.2f}%")
            elif "return" in k or "calmar" in k or "sharpe" in k:
                print(f"  {k:<25} {v:>+10.2f}")
            else:
                print(f"  {k:<25} {v:>+10,.2f}")
        else:
            print(f"  {k:<25} {v:>10}")

    # 3) Apply breadth cull
    print("\n" + "=" * 80)
    print(f"s523d (s523c + Mission P breadth cull)")
    print(f"  grace={args.grace}d, breadth={args.breadth:.0%}, throttle={args.throttle}d, cost={args.cost_bps}bps")
    print("=" * 80)
    modified_trades, cull_meta = apply_breadth_cull(
        baseline_trades, start_date, end_date,
        args.grace, args.breadth, args.throttle, args.cost_bps,
    )

    s523d_metrics, s523d_equity = compute_metrics_from_trades(
        modified_trades, start_date, end_date, args.capital
    )
    for k, v in s523d_metrics.items():
        if isinstance(v, float):
            if "pct" in k:
                print(f"  {k:<25} {v:>+10.2f}%")
            elif "return" in k or "calmar" in k or "sharpe" in k:
                print(f"  {k:<25} {v:>+10.2f}")
            else:
                print(f"  {k:<25} {v:>+10,.2f}")
        else:
            print(f"  {k:<25} {v:>10}")

    # 4) Side-by-side comparison
    print("\n" + "=" * 80)
    print("COMPARISON")
    print("=" * 80)
    print(f"{'Metric':<22} {'Baseline':>14} {'s523d':>14} {'Δ':>14}")
    for k in ["total_return_pct", "max_drawdown_pct", "sharpe", "calmar"]:
        b = base_metrics[k]
        d = s523d_metrics[k]
        delta = d - b
        if "pct" in k:
            print(f"{k:<22} {b:>+13.2f}% {d:>+13.2f}% {delta:>+13.2f}%")
        else:
            print(f"{k:<22} {b:>+14.2f} {d:>+14.2f} {delta:>+14.2f}")

    calmar_delta_pct = (s523d_metrics["calmar"] - base_metrics["calmar"]) / base_metrics["calmar"] * 100 if base_metrics["calmar"] != 0 else 0
    print(f"\nCalmar improvement: {calmar_delta_pct:+.1f}%")
    print(f"Cull events: {cull_meta['n_cull_events']}, trades force-closed: {cull_meta['n_trades_force_closed']}")

    # 5) Save outputs
    suffix = f"{args.months}mo_{args.capital//1000}k"
    out_trades = RESULTS_DIR / f"s523d_growth_{suffix}_trades.json"
    out_equity = RESULTS_DIR / f"s523d_growth_{suffix}_equity_curve.json"
    out_metrics = RESULTS_DIR / f"s523d_growth_{suffix}_metrics.json"
    out_meta = RESULTS_DIR / f"s523d_growth_{suffix}_cull_meta.json"

    with open(out_trades, "w") as f:
        json.dump(modified_trades, f, indent=2, default=str)
    with open(out_equity, "w") as f:
        json.dump({str(d.date()): float(v) for d, v in s523d_equity.items()}, f, indent=2)
    with open(out_metrics, "w") as f:
        out_metrics_payload = {
            "baseline": base_metrics,
            "s523d": s523d_metrics,
            "params": cull_meta["params"],
            "calmar_improvement_pct": calmar_delta_pct,
        }
        json.dump(out_metrics_payload, f, indent=2)
    with open(out_meta, "w") as f:
        json.dump(cull_meta, f, indent=2, default=str)

    print(f"\nOutputs saved:")
    for p in [out_trades, out_equity, out_metrics, out_meta]:
        print(f"  {p}")


if __name__ == "__main__":
    main()
