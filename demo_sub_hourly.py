#!/usr/bin/env python3
"""Live demo: Sub-hourly exit pipeline with real Binance WebSocket data.

Connects to Binance Futures mark-price WS, aggregates into 1-minute candles,
and runs exit checks against a synthetic position. Uses NO real state files —
everything lives in /tmp.

Usage:
    python demo_sub_hourly.py [--resolution 1] [--token BTC] [--seconds 180]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from v4.candle_aggregator import CandleAggregator
from v4.price_monitor import PriceMonitor
from v4.paper_engine import PaperPortfolioEngine
from v4.paper_config import PaperConfig
from v4.config import StrategySpec
from v4.position import Position


def _now_str() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


def main():
    parser = argparse.ArgumentParser(description="Live sub-hourly exit demo")
    parser.add_argument("--resolution", type=int, default=1, help="Candle resolution in minutes (default 1)")
    parser.add_argument("--token", type=str, default="BTC", help="Token to monitor (default BTC)")
    parser.add_argument("--seconds", type=int, default=180, help="Run for N seconds (default 180)")
    parser.add_argument("--stop-pct", type=float, default=0.5, help="Stop distance as %% below entry (default 0.5)")
    args = parser.parse_args()

    token = args.token.upper()
    resolution = args.resolution
    run_seconds = args.seconds

    print(f"{'='*70}")
    print(f"  SUB-HOURLY EXIT PIPELINE — LIVE DEMO")
    print(f"  Token: {token}  |  Resolution: {resolution}m  |  Duration: {run_seconds}s")
    print(f"{'='*70}")
    print()

    # ---- Step 1: Create CandleAggregator ----
    agg = CandleAggregator(resolution_minutes=resolution)
    tick_count = [0]
    first_price = [None]

    def on_price(tok: str, price: float, ts_ms: int):
        tick_count[0] += 1
        if first_price[0] is None and tok == token:
            first_price[0] = price
        if tick_count[0] <= 3 or tick_count[0] % 50 == 0:
            print(f"  [{_now_str()}] WS tick #{tick_count[0]:>5}: {tok} = ${price:,.2f}")

    print(f"[{_now_str()}] Connecting to Binance Futures WS for {token}...")
    monitor = PriceMonitor(callback=lambda tok, p, ts: (agg.on_price(tok, p, ts), on_price(tok, p, ts)), venue="perp")
    monitor.connect([token])

    # Wait for first price
    waited = 0
    while first_price[0] is None and waited < 15:
        time.sleep(0.5)
        waited += 0.5

    if first_price[0] is None:
        print(f"\n  ERROR: No price received for {token} after 15s. Check network/token name.")
        monitor.disconnect()
        return

    entry_price = first_price[0]
    stop_distance = entry_price * (args.stop_pct / 100)
    stop_price = entry_price - stop_distance
    initial_risk = stop_distance
    atr = stop_distance  # Use stop distance as ATR proxy

    print(f"\n[{_now_str()}] First price: ${entry_price:,.2f}")
    print(f"[{_now_str()}] Creating synthetic LONG position:")
    print(f"  Entry:  ${entry_price:,.2f}")
    print(f"  Stop:   ${stop_price:,.2f} ({args.stop_pct}% below)")
    print(f"  Target: ${entry_price + 5 * atr:,.2f} (5 ATR above)")
    print(f"  ATR:    ${atr:,.2f}")
    print()

    # ---- Step 2: Create engine in temp dir ----
    tmpdir = tempfile.mkdtemp(prefix="demo_sub_hourly_")
    print(f"[{_now_str()}] State dir: {tmpdir} (temp, auto-cleanup)")

    config = PaperConfig(
        strategies=[StrategySpec(strategy_id="s56", weight=1.0, market="perp", max_positions=10)],
        capital=200_000.0,
        mode="pool",
        pool_name="demo",
        max_portfolio_positions=40,
        concentration_limit=0.10,
        adv_cap_pct=0.10,
        min_position_usd=200.0,
        exchange="binance",
        seed=42,
        train_bars=0,
        recal_bars=99999,
        purge_bars=0,
        lookback_months=3,
        enable_purge_windows=False,
        exit_resolution=resolution,
        state_dir=tmpdir,
        sentinel_mode="off",
    )

    # Set exit_resolution=0 temporarily to avoid engine starting its own WS
    # We manage the aggregator externally for this demo
    saved_resolution = config.exit_resolution
    config.exit_resolution = 0
    engine = PaperPortfolioEngine(config)
    config.exit_resolution = saved_resolution
    engine.tick_counter = 10  # Simulate post-first-tick

    # Inject synthetic position
    quantity = 10_000.0 / entry_price
    pos = Position(
        position_id=f"{token}:s56:0:primary",
        token=token,
        strategy_id="s56",
        leg="primary",
        entry_bar=0,
        entry_price=entry_price,
        direction=1,
        quantity=quantity,
        margin_usd=10_000.0,
        leverage=1.0,
        is_perp=True,
        fee_rate=0.0005,
        stop_mult=2.0,
        trail_mult=3.0,
        target_mult=5.0,
        no_stop_bars=0,
        min_hold=0,
        max_hold=720,
        exit_regimes=set(),
        convex_exit=False,
        stop_price=stop_price,
        highest=entry_price,
        lowest=entry_price,
        initial_risk=initial_risk,
        breakeven_atr=0.0,
    )
    engine.state.position_manager.open_position(pos)
    engine._cached_bar_data[("s56", token)] = {
        "atr": atr,
        "adv": 1e9,
        "regime": "normal",
        "bear_target_mult": 1.0,
    }
    engine._last_known_prices[token] = entry_price

    print(f"[{_now_str()}] Engine ready. 1 open position.")
    print(f"\n{'='*70}")
    print(f"  MONITORING — flushing candles every 2s, exit checks on completed candles")
    print(f"{'='*70}\n")

    # ---- Step 3: Run the loop ----
    candle_count = 0
    start = time.time()
    last_stop = stop_price

    try:
        while time.time() - start < run_seconds:
            time.sleep(2.0)

            candles = agg.flush_completed()
            if not candles:
                elapsed = int(time.time() - start)
                remaining = run_seconds - elapsed
                open_count = len(engine.state.position_manager.open_positions)
                if open_count == 0:
                    print(f"\n  Position closed! Checking persistence...")
                    break
                sys.stdout.write(f"\r  [{_now_str()}] Waiting for candle... ({elapsed}s elapsed, {remaining}s left, ticks: {tick_count[0]}, open: {open_count})")
                sys.stdout.flush()
                continue

            candle_count += 1
            for tok, (h, l, c) in candles.items():
                print(f"\n  [{_now_str()}] CANDLE #{candle_count} ({resolution}m) for {tok}:")
                print(f"    H=${h:,.2f}  L=${l:,.2f}  C=${c:,.2f}")

            # Check exits
            open_before = len(engine.state.position_manager.open_positions)
            closed = engine.process_sub_hourly_exits(candles)

            # Show position state
            if engine.state.position_manager.open_positions:
                p = engine.state.position_manager.open_positions[0]
                trail_moved = "YES" if p.stop_price != last_stop else "no"
                print(f"    Stop: ${p.stop_price:,.2f} (trail moved: {trail_moved})")
                print(f"    Highest: ${p.highest:,.2f}  PnL: ${(c - p.entry_price) * p.quantity:,.2f}")
                last_stop = p.stop_price

            if closed > 0:
                print(f"\n    >>> EXIT TRIGGERED! {closed} position(s) closed <<<")
                break

    except KeyboardInterrupt:
        print(f"\n\n  Interrupted by user.")

    # ---- Step 4: Show results ----
    monitor.disconnect()

    print(f"\n{'='*70}")
    print(f"  RESULTS")
    print(f"{'='*70}")
    print(f"  Ticks received:  {tick_count[0]}")
    print(f"  Candles formed:  {candle_count}")
    print(f"  Open positions:  {len(engine.state.position_manager.open_positions)}")

    # Check persistence
    trades_path = Path(tmpdir) / "demo" / "trades.jsonl"
    state_path = Path(tmpdir) / "demo" / "state.json"
    equity_path = Path(tmpdir) / "demo" / "equity.csv"

    if trades_path.exists():
        with open(trades_path) as f:
            trades = [json.loads(line) for line in f if line.strip()]
        print(f"\n  Trades persisted: {len(trades)}")
        for t in trades:
            print(f"    {t['token']} {t.get('exit_reason','?')} @ ${t.get('exit_price',0):,.2f}  PnL: ${t.get('realised_pnl',0):,.2f}")
    else:
        print(f"\n  No trades (position still open)")

    if state_path.exists():
        with open(state_path) as f:
            state = json.load(f)
        print(f"  State file: {len(state.get('open_positions',[]))} open positions")

    if equity_path.exists():
        with open(equity_path) as f:
            lines = f.readlines()
        print(f"  Equity rows: {len(lines) - 1}")  # minus header

    print(f"\n  State dir: {tmpdir}")
    print(f"  (Safe to delete — no production data affected)")
    print()


if __name__ == "__main__":
    main()
