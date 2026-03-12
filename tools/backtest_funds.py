#!/usr/bin/env python3
"""Historical backtest with spot/perp fund tracking and rebalancing.

Replays strategy signals bar-by-bar through PaperPositionTracker to see
how fund balances and rebalancing would have performed historically.

Usage:
    python tools/backtest_funds.py                    # all strategies, last 12 months
    python tools/backtest_funds.py --months 3         # last 3 months
    python tools/backtest_funds.py --strategy s30     # single strategy
    python tools/backtest_funds.py --dashboard        # generate dashboard with results
"""
from __future__ import annotations

import argparse
import inspect
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "v3"))

import numpy as np
import pandas as pd
from v3.engine import Engine
from v3.paper_engine import _load_strategy_fn
from v3.universe import get_fee_rate

# ---------------------------------------------------------------------------
# Configuration — mirrors run_paper_live.py RUNS
# ---------------------------------------------------------------------------

RUNS = [
    {"strategy_id": "s30", "capital": 200_000.0, "label": "s30_basis_carry",
     "market": "combined"},
    {"strategy_id": "s32", "capital": 200_000.0, "label": "s32_regime_spot_perp",
     "market": "combined"},
    {"strategy_id": "s54", "capital": 200_000.0, "label": "s54_turbo_carry",
     "market": "combined",
     "tokens": [
         "AAVE", "ADA", "APT", "AVAX", "AXS", "BCH", "BNB", "BTC", "DOGE",
         "DOT", "ETH", "FIL", "HBAR", "LINK", "LTC", "NEAR", "OP", "SOL",
         "TRX", "UNI", "XRP", "ZEC",
     ]},
    {"strategy_id": "s58", "capital": 200_000.0, "label": "s58_multi_strategy",
     "market": "combined", "max_positions": 25,
     "sub_strategies": [
         {"strategy_id": "s56", "market": "perp", "tag": "mom"},
         {"strategy_id": "s57", "market": "combined", "tag": "carry"},
     ]},
]

DATA_DIR = str(PROJECT_ROOT / "data")
EXCHANGE = "binance"


# ---------------------------------------------------------------------------
# Signal pre-computation — build all signal arrays once per token
# ---------------------------------------------------------------------------

def precompute_signals(
    strategy_id: str, market: str, tokens: list[str], months: int,
) -> dict[str, dict]:
    """Pre-compute strategy signal arrays for all tokens.

    Returns {token: {arrays, indices, n_bars}} where arrays contains
    per-bar entry/exit/regime/sizing data.
    """
    strategy_fn = _load_strategy_fn(strategy_id)
    is_single_ctx = len(inspect.signature(strategy_fn).parameters) == 1

    eng_spot = Engine(data_dir=DATA_DIR, market="spot", capital=200_000, exchange=EXCHANGE)
    eng_perp = Engine(data_dir=DATA_DIR, market="perp", capital=200_000, exchange=EXCHANGE)

    results = {}

    for token in tokens:
        try:
            # Load full history
            spot_pq = Path(DATA_DIR) / "spot" / "1h_cache" / f"{token}_1h.parquet"
            perp_pq = Path(DATA_DIR) / "perp" / "1h_cache" / f"{token}_1h.parquet"
            if not spot_pq.exists() or not perp_pq.exists():
                continue

            df_spot = pd.read_parquet(spot_pq)
            df_perp = pd.read_parquet(perp_pq)

            # Trim to requested period + warmup (tz-naive to match parquet)
            cutoff = pd.Timestamp.now().tz_localize(None) - pd.DateOffset(months=months + 2)
            df_spot = df_spot[df_spot.index >= cutoff]
            df_perp = df_perp[df_perp.index >= cutoff]

            if len(df_spot) < 500 or len(df_perp) < 500:
                continue

            ctx_spot = eng_spot._build_context(token, df_spot, min_bars=210,
                                                market_override="spot")
            ctx_perp = eng_perp._build_context(token, df_perp, min_bars=210,
                                                market_override="perp")
            if ctx_spot is None or ctx_perp is None:
                continue

            # Keep liquidity_mask intact — it filters out illiquid bars
            # where position entry would be unrealistic

            if is_single_ctx:
                ctx = ctx_perp if market == "perp" else ctx_spot
                sr = strategy_fn(ctx)
            else:
                sr = strategy_fn(ctx_spot, ctx_perp)

            n = len(ctx_spot.ind_1h["close"])

            # Extract arrays
            def _to_array(val, n):
                if isinstance(val, np.ndarray):
                    return val
                return np.full(n, float(val) if val is not None else 0.0)

            entry_mask = sr.entry_mask
            direction = sr.direction
            sec_entry = sr.secondary_entry_mask if sr.secondary_entry_mask is not None else np.zeros(n, dtype=bool)
            sec_dir = sr.secondary_direction if sr.secondary_direction is not None else np.ones(n, dtype=int)

            # Apply liquidity mask (same as engine._simulate_multi_leg)
            if ctx_spot.liquidity_mask is not None:
                liq = ctx_spot.liquidity_mask[:n]
                entry_mask = entry_mask[:n] & liq
            if ctx_perp.liquidity_mask is not None and sr.secondary_entry_mask is not None:
                liq_p = ctx_perp.liquidity_mask[:n]
                sec_entry = sec_entry[:n] & liq_p

            # Use the minimum of all array lengths to avoid OOB
            n_safe = min(n, len(entry_mask), len(ctx_spot.idx_1h),
                         len(ctx_spot.regime_1h))

            results[token] = {
                "entry_mask": entry_mask[:n_safe],
                "direction": direction[:n_safe],
                "sec_entry_mask": sec_entry[:n_safe],
                "sec_direction": sec_dir[:n_safe],
                "regime_1h": ctx_spot.regime_1h[:n_safe],
                "exit_regimes": sr.exit_regimes,
                "spot_close": ctx_spot.ind_1h["close"][:n_safe],
                "perp_close": ctx_perp.ind_1h["close"][:n_safe],
                "spot_atr": ctx_spot.ind_1h["atr"][:n_safe],
                "perp_atr": ctx_perp.ind_1h["atr"][:n_safe],
                "spot_adv": ctx_spot.rolling_adv[:n_safe],
                "perp_adv": ctx_perp.rolling_adv[:n_safe],
                "timestamps": ctx_spot.idx_1h[:n_safe],
                "n_bars": n_safe,
                "is_single_ctx": is_single_ctx,
                "market": market,
                # Sizing params
                "size_multiplier": _to_array(sr.size_multiplier, n),
                "cap_multiplier": float(sr.cap_multiplier),
                "leverage": _to_array(sr.leverage, n),
                "secondary_leverage": float(getattr(sr, 'secondary_leverage', 1.0)),
                "edge": float(sr.edge) if hasattr(sr, 'edge') else 0.3,
                "capital_split": float(getattr(sr, 'capital_split', 0.5)),
                # Trade management
                "stop_mult": _to_array(sr.stop_mult, n),
                "trail_mult": _to_array(sr.trail_mult, n),
                "target_mult": float(sr.target_mult),
                "no_stop_bars": int(sr.no_stop_bars),
                "min_hold": int(sr.min_hold),
                "max_hold": int(sr.max_hold),
                "trail_schedule": sr.trail_schedule.tolist() if sr.trail_schedule is not None else None,
                # Secondary leg trade params
                "sec_stop_mult": float(sr.secondary_stop_mult) if sr.secondary_stop_mult is not None else None,
                "sec_trail_mult": float(sr.secondary_trail_mult) if sr.secondary_trail_mult is not None else None,
                "sec_target_mult": float(sr.secondary_target_mult) if sr.secondary_target_mult is not None else None,
                "sec_no_stop_bars": int(sr.secondary_no_stop_bars) if sr.secondary_no_stop_bars is not None else None,
                "sec_min_hold": int(sr.secondary_min_hold) if sr.secondary_min_hold is not None else None,
                "sec_max_hold": int(sr.secondary_max_hold) if sr.secondary_max_hold is not None else None,
            }
        except Exception as e:
            print(f"    {token}: error - {e}")
            continue

    return results


def signal_at_bar(token_data: dict, bar_idx: int) -> dict:
    """Extract a signal dict at a specific bar index, matching the format
    expected by PaperPositionTracker.process_signals()."""
    d = token_data
    n = d["n_bars"]
    if bar_idx < 0 or bar_idx >= n:
        return {}

    regime = int(d["regime_1h"][bar_idx])
    in_exit = regime in d["exit_regimes"]

    sm = float(d["size_multiplier"][bar_idx])
    lev = float(d["leverage"][bar_idx])
    sec_lev = d["secondary_leverage"]

    trade_params = {
        "stop_mult": float(d["stop_mult"][bar_idx]),
        "trail_mult": float(d["trail_mult"][bar_idx]),
        "target_mult": d["target_mult"],
        "no_stop_bars": d["no_stop_bars"],
        "min_hold": d["min_hold"],
        "max_hold": d["max_hold"],
    }
    sec_trade_params = {
        "stop_mult": d["sec_stop_mult"] if d["sec_stop_mult"] is not None else trade_params["stop_mult"],
        "trail_mult": d["sec_trail_mult"] if d["sec_trail_mult"] is not None else trade_params["trail_mult"],
        "target_mult": d["sec_target_mult"] if d["sec_target_mult"] is not None else trade_params["target_mult"],
        "no_stop_bars": d["sec_no_stop_bars"] if d["sec_no_stop_bars"] is not None else trade_params["no_stop_bars"],
        "min_hold": d["sec_min_hold"] if d["sec_min_hold"] is not None else trade_params["min_hold"],
        "max_hold": d["sec_max_hold"] if d["sec_max_hold"] is not None else trade_params["max_hold"],
    }

    if d["is_single_ctx"]:
        mkt = d["market"]
        entry = bool(d["entry_mask"][bar_idx])
        direction = int(d["direction"][bar_idx])
        spot_entry = entry if mkt == "spot" else False
        perp_entry = entry if mkt == "perp" else False
        spot_dir = direction if mkt == "spot" else 0
        perp_dir = direction if mkt == "perp" else 0
    else:
        spot_entry = bool(d["entry_mask"][bar_idx])
        perp_entry = bool(d["sec_entry_mask"][bar_idx])
        spot_dir = int(d["direction"][bar_idx])
        perp_dir = int(d["sec_direction"][bar_idx])

    return {
        "spot_entry": spot_entry,
        "perp_entry": perp_entry,
        "spot_dir": spot_dir,
        "perp_dir": perp_dir,
        "regime": regime,
        "in_exit_regime": in_exit,
        "spot_close": float(d["spot_close"][bar_idx]),
        "perp_close": float(d["perp_close"][bar_idx]),
        "spot_atr": float(d["spot_atr"][bar_idx]),
        "perp_atr": float(d["perp_atr"][bar_idx]),
        "spot_adv": float(d["spot_adv"][bar_idx]),
        "perp_adv": float(d["perp_adv"][bar_idx]),
        "basis_bps": round(
            (float(d["perp_close"][bar_idx]) - float(d["spot_close"][bar_idx]))
            / float(d["spot_close"][bar_idx]) * 10_000, 2
        ),
        "sizing": {
            "size_multiplier": sm,
            "cap_multiplier": d["cap_multiplier"],
            "leverage": lev,
            "secondary_leverage": sec_lev,
            "edge": d["edge"],
        },
        "trade_params": trade_params,
        "sec_trade_params": sec_trade_params,
        "trail_schedule": d["trail_schedule"],
        "capital_split": d["capital_split"],
    }


# ---------------------------------------------------------------------------
# Historical simulation
# ---------------------------------------------------------------------------

def discover_tokens() -> list[str]:
    """Find tokens with both spot and perp data."""
    spot_dir = Path(DATA_DIR) / "spot" / "1h_cache"
    perp_dir = Path(DATA_DIR) / "perp" / "1h_cache"
    spot = {f.replace("_1h.parquet", "") for f in os.listdir(spot_dir)
            if f.endswith(".parquet")} if spot_dir.is_dir() else set()
    perp = {f.replace("_1h.parquet", "") for f in os.listdir(perp_dir)
            if f.endswith(".parquet")} if perp_dir.is_dir() else set()
    return sorted(spot & perp)


def run_historical_sim(run_cfg: dict, months: int) -> dict:
    """Run a historical simulation for one strategy configuration."""
    from run_paper_live import PaperPositionTracker
    import tempfile

    label = run_cfg["label"]
    capital = run_cfg["capital"]
    max_positions = run_cfg.get("max_positions", 15)
    all_tokens = run_cfg.get("tokens", discover_tokens())

    print(f"\n{'='*60}")
    print(f"  {label} (${capital:,.0f}, {len(all_tokens)} tokens, {months}mo)")
    print(f"{'='*60}")

    # Pre-compute signals for all sub-strategies
    if "sub_strategies" in run_cfg:
        all_signals_by_sub = {}
        for sub in run_cfg["sub_strategies"]:
            tag = sub["tag"]
            print(f"  Pre-computing {sub['strategy_id']} ({tag})...", end=" ", flush=True)
            t0 = time.time()
            sigs = precompute_signals(sub["strategy_id"], sub.get("market", "combined"),
                                       all_tokens, months)
            print(f"{len(sigs)} tokens in {time.time()-t0:.1f}s")
            all_signals_by_sub[tag] = sigs
    else:
        print(f"  Pre-computing {run_cfg['strategy_id']}...", end=" ", flush=True)
        t0 = time.time()
        all_signals_data = precompute_signals(
            run_cfg["strategy_id"], run_cfg.get("market", "combined"),
            all_tokens, months,
        )
        print(f"{len(all_signals_data)} tokens in {time.time()-t0:.1f}s")

    # Create tracker in a temp directory
    tmpdir = tempfile.mkdtemp()
    tracker = PaperPositionTracker(
        strategy_id=run_cfg.get("strategy_id", label.split("_")[0]),
        label=label, capital=capital,
        state_dir=tmpdir, data_dir=DATA_DIR,
        exchange=EXCHANGE, max_positions=max_positions,
    )

    # Find common bar range across all tokens
    if "sub_strategies" in run_cfg:
        all_timestamps = set()
        for tag, sigs in all_signals_by_sub.items():
            for token, data in sigs.items():
                for ts in data["timestamps"]:
                    all_timestamps.add(ts)
    else:
        all_timestamps = set()
        for token, data in all_signals_data.items():
            for ts in data["timestamps"]:
                all_timestamps.add(ts)

    all_timestamps = sorted(all_timestamps)

    # Trim to requested months
    cutoff = pd.Timestamp.now().tz_localize(None) - pd.DateOffset(months=months)
    bar_timestamps = [ts for ts in all_timestamps if ts >= cutoff]

    if not bar_timestamps:
        print(f"  No bars in requested period.")
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)
        return {}

    print(f"  Simulating {len(bar_timestamps)} bars "
          f"({bar_timestamps[0].strftime('%Y-%m-%d')} → "
          f"{bar_timestamps[-1].strftime('%Y-%m-%d')})...")

    # Record history for dashboard
    equity_history = []
    fund_history = []
    rebalance_events = []
    n_ticks = 0
    last_pct = -1

    for tick_idx, ts in enumerate(bar_timestamps):
        tick_str = ts.strftime("%Y-%m-%d %H:%M UTC")

        # Build all_signals for this bar
        all_sigs = {}
        if "sub_strategies" in run_cfg:
            for sub in run_cfg["sub_strategies"]:
                tag = sub["tag"]
                sigs = all_signals_by_sub[tag]
                for token, data in sigs.items():
                    # Find bar index for this timestamp
                    idx_arr = data["timestamps"]
                    matches = np.where(idx_arr == ts)[0]
                    if len(matches) == 0:
                        continue
                    bar_idx = matches[0]
                    sig = signal_at_bar(data, bar_idx)
                    if sig:
                        all_sigs[f"{token}:{tag}"] = sig
        else:
            for token, data in all_signals_data.items():
                idx_arr = data["timestamps"]
                matches = np.where(idx_arr == ts)[0]
                if len(matches) == 0:
                    continue
                bar_idx = matches[0]
                sig = signal_at_bar(data, bar_idx)
                if sig:
                    all_sigs[token] = sig

        # Process signals through tracker
        result = tracker.process_signals(all_sigs, tick_str)

        # Record state
        unrealized = sum(
            p.get("unrealized_pnl", 0) for p in tracker.open_positions.values()
        )
        final_eq = tracker.equity + unrealized
        t_iso = ts.strftime("%Y-%m-%dT%H:%M:%SZ")

        equity_history.append({"t": t_iso, "eq": round(final_eq, 2)})
        fund_history.append({
            "t": t_iso,
            "spot_funds": round(tracker.spot_funds, 2),
            "perp_funds": round(tracker.perp_funds, 2),
            "equity": round(final_eq, 2),
        })

        if result.get("rebalance"):
            rb = result["rebalance"]
            rb["tick_idx"] = tick_idx
            rebalance_events.append(rb)

        n_ticks += 1

        # Progress
        pct = tick_idx * 100 // len(bar_timestamps)
        if pct >= last_pct + 10:
            last_pct = pct
            n_open = len(tracker.open_positions)
            n_closed = len(tracker.closed_trades)
            print(f"    {pct:3d}% | bar {tick_idx}/{len(bar_timestamps)} | "
                  f"equity ${final_eq:,.0f} | {n_open} open, {n_closed} closed | "
                  f"spot ${tracker.spot_funds:,.0f} / perp ${tracker.perp_funds:,.0f} | "
                  f"{len(rebalance_events)} rebalances")

    # Final summary
    unrealized = sum(
        p.get("unrealized_pnl", 0) for p in tracker.open_positions.values()
    )
    final_eq = tracker.equity + unrealized
    realized = sum(t.get("pnl", 0) for t in tracker.closed_trades)
    n_open = len(tracker.open_positions)
    n_closed = len(tracker.closed_trades)

    print(f"\n  Results:")
    print(f"    Final equity:  ${final_eq:,.2f} ({(final_eq/capital - 1)*100:+.2f}%)")
    print(f"    Realized P&L:  ${realized:,.2f}")
    print(f"    Unrealized:    ${unrealized:,.2f}")
    print(f"    Spot funds:    ${tracker.spot_funds:,.2f}")
    print(f"    Perp funds:    ${tracker.perp_funds:,.2f}")
    print(f"    Positions:     {n_open} open, {n_closed} closed")
    print(f"    Rebalances:    {len(rebalance_events)}")

    # Build dashboard-compatible sim data
    sim = tracker.to_dashboard_sim()
    sim["equity_history"] = equity_history
    sim["fund_history"] = fund_history
    sim["rebalance_history"] = rebalance_events[-50:]
    sim["name"] = f"Backtest: {label} ({months}mo)"

    import shutil
    shutil.rmtree(tmpdir, ignore_errors=True)

    return sim


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Historical backtest with spot/perp fund tracking",
    )
    parser.add_argument("--months", type=int, default=12, help="Months of history")
    parser.add_argument("--strategy", type=str, default=None,
                        help="Single strategy label (e.g. s30, s58)")
    parser.add_argument("--dashboard", action="store_true",
                        help="Generate dashboard HTML with results")
    args = parser.parse_args()

    runs = RUNS
    if args.strategy:
        runs = [r for r in RUNS if args.strategy in r["strategy_id"] or args.strategy in r["label"]]
        if not runs:
            print(f"Strategy '{args.strategy}' not found. Available: "
                  + ", ".join(r["strategy_id"] for r in RUNS))
            return

    print(f"Fund Tracking Backtest")
    print(f"  Strategies: {', '.join(r['strategy_id'] for r in runs)}")
    print(f"  Period: {args.months} months")
    print(f"  Capital: ${sum(r['capital'] for r in runs):,.0f}")
    print(f"  Compounding: after exits only (realized P&L)")

    t0 = time.time()
    sims_data = []
    for run_cfg in runs:
        sim = run_historical_sim(run_cfg, args.months)
        if sim:
            sims_data.append(sim)

    elapsed = time.time() - t0
    print(f"\nTotal time: {elapsed:.0f}s")

    if not sims_data:
        print("No results.")
        return

    # Save results
    out_dir = PROJECT_ROOT / "state" / "backtest_funds"
    out_dir.mkdir(parents=True, exist_ok=True)
    results_path = out_dir / "results.json"
    with open(results_path, "w") as f:
        json.dump(sims_data, f, indent=2, default=str)
    print(f"\nResults saved to {results_path}")

    if args.dashboard:
        print("\nGenerating dashboard...")
        from tools.generate_dashboard import generate_html
        docs_dir = PROJECT_ROOT / "docs"
        docs_dir.mkdir(exist_ok=True)
        html = generate_html(sims_data)
        out = docs_dir / "backtest_funds.html"
        out.write_text(html)
        print(f"Dashboard: {out} ({len(html)/1024:.0f} KB)")


if __name__ == "__main__":
    main()
