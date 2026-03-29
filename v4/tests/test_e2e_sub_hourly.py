"""End-to-end simulation tests for sub-hourly exit pipeline.

Tests the full data flow WITHOUT network access or touching real state:
  1. Synthetic price ticks fed to CandleAggregator.on_price()
  2. CandleAggregator buffers and emits completed candles
  3. PaperPortfolioEngine.process_sub_hourly_exits() processes exits
  4. State persists correctly (trades.jsonl, state.json, equity.csv)
  5. Position state (highest/lowest, stop_price) updates correctly across candles

Exit logic notes (from check_candle_exits):
  - Exit price is ALWAYS candle close `c` (not stop_price or target_price)
  - Trail tightening runs BEFORE stop check: highest updates → trail computes → stop moves
  - Stop check: long stop breached if L <= stop_price
  - Target check: long target if H >= entry + target_mult * ATR
  - CB check: if L <= entry - cb_r * initial_risk

Covers all supported timeframes: 1m, 5m, 15m, 30m.
No network access required. No risk to existing paper trader state.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))

import pytest

from v4.candle_aggregator import CandleAggregator
from v4.paper_engine import PaperPortfolioEngine
from v4.paper_config import PaperConfig
from v4.config import StrategySpec
from v4.position import Position


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ts_ms(minutes: float) -> int:
    """Convert minutes to timestamp in milliseconds."""
    return int(minutes * 60 * 1000)


def _make_config(state_dir: str, exit_resolution: int = 5) -> PaperConfig:
    return PaperConfig(
        strategies=[
            StrategySpec(strategy_id="s56", weight=0.5, market="combined", max_positions=10, exit_resolution=exit_resolution),
            StrategySpec(strategy_id="s99", weight=0.5, market="combined", max_positions=10, exit_resolution=exit_resolution),
        ],
        capital=200_000.0,
        mode="pool",
        pool_name="e2e_sim",
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
        drawdown_alert_pct=5.0,
        stress_adv_multiplier=0.3,
        max_slip_bps=300,
        exit_resolution=exit_resolution,
        state_dir=state_dir,
    )


def _make_position(
    token: str = "BTC",
    strategy_id: str = "s56",
    entry_price: float = 100.0,
    direction: int = 1,
    margin_usd: float = 10_000.0,
    stop_price: float = 90.0,
    trail_mult: float = 3.0,
    target_mult: float = 5.0,
    initial_risk: float = 2.0,
    entry_bar: int = 0,
    highest: float = 0.0,
    lowest: float = 0.0,
) -> Position:
    quantity = direction * margin_usd / entry_price
    return Position(
        position_id=f"{token}:{strategy_id}:0:primary",
        token=token,
        strategy_id=strategy_id,
        leg="primary",
        entry_bar=entry_bar,
        entry_price=entry_price,
        direction=direction,
        quantity=quantity,
        margin_usd=margin_usd,
        leverage=1.0,
        is_perp=True,
        fee_rate=0.0005,
        stop_mult=2.0,
        trail_mult=trail_mult,
        target_mult=target_mult,
        no_stop_bars=0,
        min_hold=0,
        max_hold=720,
        exit_regimes=set(),
        convex_exit=False,
        stop_price=stop_price,
        highest=highest if highest != 0.0 else entry_price,
        lowest=lowest if lowest != 0.0 else entry_price,
        initial_risk=initial_risk,
        breakeven_atr=0.0,  # Disable by default; enable explicitly in breakeven tests
    )


def _simulate_price_ticks(agg: CandleAggregator, token: str, ticks: list[tuple[float, float]]):
    """Feed synthetic price ticks to aggregator.

    ticks: list of (minute_offset, price)
    """
    for minute, price in ticks:
        agg.on_price(token, price, _ts_ms(minute))


def _setup_engine(tmpdir, exit_resolution, positions, bar_data_overrides=None):
    """Create engine with positions and cached bar data in isolated tmpdir."""
    config = _make_config(tmpdir, exit_resolution=exit_resolution)
    engine = PaperPortfolioEngine(config)
    engine.tick_counter = 10  # Simulate post-first-tick

    for pos in positions:
        engine.state.position_manager.open_position(pos)
        engine._cached_bar_data[(pos.strategy_id, pos.token)] = bar_data_overrides or {
            "atr": 5.0,
            "adv": 1e6,
            "regime": 0,
            "bear_target_mult": 0.0,
        }

    return engine


# ---------------------------------------------------------------------------
# Test 1: Full pipeline — ticks → aggregator → engine → persist
# ---------------------------------------------------------------------------

class TestE2EFullPipeline:
    """Simulates the complete data flow: ticks → candles → exits → persist."""

    def test_stop_breach_full_flow_5m(self):
        """5-minute candle: ticks accumulate, candle completes, stop breaches, state persists.

        Trail tightening: highest=100 (entry), trail = 100 - 3*5 = 85.
        Original stop 90 > trail 85, so stop stays at 90.
        L=88 <= stop=90 → exit at C=88 (candle close).
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            agg = CandleAggregator(5)
            pos = _make_position(
                entry_price=100.0, stop_price=90.0, direction=1, entry_bar=5,
            )
            engine = _setup_engine(tmpdir, exit_resolution=5, positions=[pos])

            # --- Interval 0 (minutes 0-4): Price drifts down ---
            _simulate_price_ticks(agg, "BTC", [
                (0.0, 100.0),   # Open
                (1.0, 99.0),    # Dip
                (2.0, 98.0),    # Further dip
                (3.0, 89.0),    # Below stop
                (4.0, 88.0),    # Even lower (candle close)
            ])
            candles = agg.flush_completed()
            assert candles == {}, "No candle should complete within interval 0"

            # --- Minute 5: New interval triggers candle completion ---
            agg.on_price("BTC", 91.0, _ts_ms(5.0))
            candles = agg.flush_completed()

            assert "BTC" in candles, "BTC candle should complete at interval boundary"
            h, l, c = candles["BTC"]
            assert h == 100.0  # Max of all ticks in interval 0
            assert l == 88.0   # Min of all ticks in interval 0
            assert c == 88.0   # Last tick before boundary

            # --- Process exits ---
            closed = engine.process_sub_hourly_exits(candles)
            assert closed == 1

            # --- Verify trade ---
            trade = engine.state.position_manager.closed_trades[0]
            assert trade.exit_reason == "stop"
            assert trade.exit_price < c  # Long exit: slippage reduces exit price
            assert trade.token == "BTC"

            # --- Verify persistence ---
            assert os.path.exists(os.path.join(tmpdir, "trades.jsonl"))
            assert os.path.exists(os.path.join(tmpdir, "state.json"))
            assert os.path.exists(os.path.join(tmpdir, "equity.csv"))

            with open(os.path.join(tmpdir, "trades.jsonl")) as f:
                persisted_trade = json.loads(f.readline())
            assert persisted_trade["exit_reason"] == "stop"
            assert persisted_trade["token"] == "BTC"

            with open(os.path.join(tmpdir, "equity.csv")) as f:
                lines = f.readlines()
            assert len(lines) == 2  # header + 1 data row

    def test_target_breach_full_flow_1m(self):
        """1-minute candle: fast exit on target hit.

        Target = entry(100) + target_mult(3) * ATR(5) = 115.
        Use wide trail (trail_mult=10) so trail stop = highest(116) - 10*5 = 66,
        well below L. H=116 >= 115 → target hit, exit at C.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            agg = CandleAggregator(1)
            pos = _make_position(
                entry_price=100.0, stop_price=80.0, direction=1,
                target_mult=3.0, trail_mult=10.0, entry_bar=5,
            )
            engine = _setup_engine(tmpdir, exit_resolution=1, positions=[pos])

            # Interval 0 (minute 0): price spikes up
            _simulate_price_ticks(agg, "BTC", [
                (0.0, 100.0),
                (0.25, 105.0),
                (0.5, 116.0),   # Exceeds target = 100 + 3*5 = 115
                (0.75, 115.0),
            ])
            assert agg.flush_completed() == {}

            # Minute 1: new interval
            agg.on_price("BTC", 114.0, _ts_ms(1.0))
            candles = agg.flush_completed()

            assert "BTC" in candles
            h, l, c = candles["BTC"]
            assert h == 116.0
            assert l == 100.0
            assert c == 115.0

            closed = engine.process_sub_hourly_exits(candles)
            assert closed == 1
            trade = engine.state.position_manager.closed_trades[0]
            assert trade.exit_reason == "target"
            assert trade.exit_price < c  # Long exit: slippage reduces exit price

    def test_no_exit_price_within_range_15m(self):
        """15-minute candle: position stays open when price stays safe."""
        with tempfile.TemporaryDirectory() as tmpdir:
            agg = CandleAggregator(15)
            pos = _make_position(
                entry_price=100.0, stop_price=80.0, direction=1,
                target_mult=10.0, trail_mult=10.0, entry_bar=5,
            )
            engine = _setup_engine(tmpdir, exit_resolution=15, positions=[pos])

            # Interval 0 (minutes 0-14): normal price action, well within stops
            _simulate_price_ticks(agg, "BTC", [
                (0.0, 100.0),
                (3.0, 101.0),
                (7.0, 99.0),
                (10.0, 102.0),
                (14.0, 101.5),
            ])
            assert agg.flush_completed() == {}

            # Minute 15: boundary
            agg.on_price("BTC", 102.0, _ts_ms(15.0))
            candles = agg.flush_completed()
            assert "BTC" in candles

            closed = engine.process_sub_hourly_exits(candles)
            assert closed == 0
            assert len(engine.state.position_manager.open_positions) == 1

            # No persistence files should be created (no exits)
            assert not os.path.exists(os.path.join(tmpdir, "trades.jsonl"))

    def test_circuit_breaker_30m(self):
        """30-minute candle: circuit breaker fires on catastrophic drop.

        cb_r=3.0, initial_risk=5.0 → CB threshold = 3*5 = 15
        Entry=100, so CB fires when L <= 100 - 15 = 85.
        Crash to 79 triggers CB. Exit at candle close.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            agg = CandleAggregator(30)
            pos = _make_position(
                entry_price=100.0, stop_price=90.0, direction=1,
                initial_risk=5.0, entry_bar=5,
            )
            engine = _setup_engine(tmpdir, exit_resolution=30, positions=[pos])

            # Explicitly enable circuit breaker on the strategy spec
            engine.config.strategies[0].circuit_breaker_r = 3.0

            # CB threshold = 3.0 * 5.0 = 15 → fires when L <= 85
            _simulate_price_ticks(agg, "BTC", [
                (0.0, 100.0),
                (10.0, 99.0),
                (20.0, 95.0),
                (25.0, 79.0),    # Catastrophic drop, well below 85
                (29.0, 81.0),
            ])
            assert agg.flush_completed() == {}

            agg.on_price("BTC", 82.0, _ts_ms(30.0))
            candles = agg.flush_completed()
            assert "BTC" in candles

            closed = engine.process_sub_hourly_exits(candles)
            assert closed == 1
            trade = engine.state.position_manager.closed_trades[0]
            assert trade.exit_reason == "circuit_breaker"


# ---------------------------------------------------------------------------
# Test 2: Multi-token pipeline
# ---------------------------------------------------------------------------

class TestE2EMultiToken:
    """Tests with multiple tokens flowing through the same aggregator."""

    def test_two_tokens_independent_exits(self):
        """BTC hits stop, ETH stays open — processed in same candle flush."""
        with tempfile.TemporaryDirectory() as tmpdir:
            agg = CandleAggregator(5)

            btc_pos = _make_position(
                token="BTC", strategy_id="s56",
                entry_price=100.0, stop_price=90.0, direction=1, entry_bar=5,
            )
            eth_pos = _make_position(
                token="ETH", strategy_id="s56",
                entry_price=3000.0, stop_price=2500.0, direction=1, entry_bar=5,
            )
            eth_pos.position_id = "ETH:s56:0:primary"

            engine = _setup_engine(tmpdir, exit_resolution=5, positions=[btc_pos, eth_pos])
            engine._cached_bar_data[("s56", "ETH")] = {
                "atr": 100.0, "adv": 5e5, "regime": 0, "bear_target_mult": 0.0,
            }

            # Both tokens get ticks in interval 0
            _simulate_price_ticks(agg, "BTC", [
                (0.0, 100.0), (2.0, 89.0), (4.0, 88.0),  # BTC crashes
            ])
            _simulate_price_ticks(agg, "ETH", [
                (0.0, 3000.0), (2.0, 2950.0), (4.0, 2980.0),  # ETH normal
            ])

            # Trigger interval 1
            agg.on_price("BTC", 89.0, _ts_ms(5.0))
            agg.on_price("ETH", 2990.0, _ts_ms(5.0))
            candles = agg.flush_completed()

            assert "BTC" in candles
            assert "ETH" in candles

            closed = engine.process_sub_hourly_exits(candles)
            assert closed == 1  # Only BTC

            # BTC closed, ETH still open
            open_tokens = {p.token for p in engine.state.position_manager.open_positions}
            assert "ETH" in open_tokens
            assert "BTC" not in open_tokens

    def test_both_tokens_exit_same_candle(self):
        """Both BTC and ETH hit stops in the same candle flush."""
        with tempfile.TemporaryDirectory() as tmpdir:
            agg = CandleAggregator(5)

            btc_pos = _make_position(
                token="BTC", strategy_id="s56",
                entry_price=100.0, stop_price=90.0, direction=1, entry_bar=5,
            )
            eth_pos = _make_position(
                token="ETH", strategy_id="s56",
                entry_price=3000.0, stop_price=2800.0, direction=1, entry_bar=5,
            )
            eth_pos.position_id = "ETH:s56:0:primary"

            engine = _setup_engine(tmpdir, exit_resolution=5, positions=[btc_pos, eth_pos])
            engine._cached_bar_data[("s56", "ETH")] = {
                "atr": 100.0, "adv": 5e5, "regime": 0, "bear_target_mult": 0.0,
            }

            # Both crash in interval 0
            _simulate_price_ticks(agg, "BTC", [(0.0, 100.0), (3.0, 85.0)])
            _simulate_price_ticks(agg, "ETH", [(0.0, 3000.0), (3.0, 2700.0)])

            agg.on_price("BTC", 86.0, _ts_ms(5.0))
            agg.on_price("ETH", 2750.0, _ts_ms(5.0))
            candles = agg.flush_completed()

            closed = engine.process_sub_hourly_exits(candles)
            assert closed == 2

            assert len(engine.state.position_manager.open_positions) == 0
            assert len(engine.state.position_manager.closed_trades) == 2

            # Verify persistence has both trades
            with open(os.path.join(tmpdir, "trades.jsonl")) as f:
                trade_lines = f.readlines()
            assert len(trade_lines) == 2


# ---------------------------------------------------------------------------
# Test 3: Multi-strategy ATR cache
# ---------------------------------------------------------------------------

class TestE2EMultiStrategy:
    """Two strategies with same token but different ATR values."""

    def test_per_strategy_atr_affects_trail(self):
        """Strategy s56 (ATR=5) has tighter trail than s99 (ATR=10).

        Both start with highest=120, trail_mult=3:
        - s56 trail: 120 - 3*5 = 105 → stop tightens to 105
        - s99 trail: 120 - 3*10 = 90 → stop tightens to 90

        Candle L=103:
        - s56: 103 <= 105 → stop hit (exit at C=103)
        - s99: 103 > 90 → safe (stays open)
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            agg = CandleAggregator(5)

            pos_s56 = _make_position(
                token="BTC", strategy_id="s56",
                entry_price=100.0, stop_price=80.0, direction=1,
                trail_mult=3.0, target_mult=20.0, entry_bar=5,
                highest=120.0,
            )
            pos_s99 = _make_position(
                token="BTC", strategy_id="s99",
                entry_price=100.0, stop_price=80.0, direction=1,
                trail_mult=3.0, target_mult=20.0, entry_bar=5,
                highest=120.0,
            )
            pos_s99.position_id = "BTC:s99:0:primary"

            config = _make_config(tmpdir, exit_resolution=5)
            engine = PaperPortfolioEngine(config)
            engine.tick_counter = 10

            engine.state.position_manager.open_position(pos_s56)
            engine.state.position_manager.open_position(pos_s99)

            engine._cached_bar_data[("s56", "BTC")] = {
                "atr": 5.0, "adv": 1e6, "regime": 0, "bear_target_mult": 0.0,
            }
            engine._cached_bar_data[("s99", "BTC")] = {
                "atr": 10.0, "adv": 1e6, "regime": 0, "bear_target_mult": 0.0,
            }

            # Candle: H=121 (updates highest to 121), L=103, C=103
            # s56 trail: 121 - 3*5 = 106 → stop = max(80, 106) = 106 → L=103 <= 106 → exit
            # s99 trail: 121 - 3*10 = 91 → stop = max(80, 91) = 91 → L=103 > 91 → safe
            _simulate_price_ticks(agg, "BTC", [
                (0.0, 121.0), (2.0, 103.0), (4.0, 103.0),
            ])
            agg.on_price("BTC", 104.0, _ts_ms(5.0))
            candles = agg.flush_completed()

            closed = engine.process_sub_hourly_exits(candles)

            assert closed == 1
            trade = engine.state.position_manager.closed_trades[0]
            assert trade.strategy_id == "s56"

            open_positions = engine.state.position_manager.open_positions
            assert len(open_positions) == 1
            assert open_positions[0].strategy_id == "s99"


# ---------------------------------------------------------------------------
# Test 4: State continuity across multiple candle flushes
# ---------------------------------------------------------------------------

class TestE2EStateContinuity:
    """Verifies position state (highest/lowest, stop_price) updates across candles."""

    def test_trail_tightens_across_candles(self):
        """Trail stop tightens as price makes new highs across candle boundaries.

        ATR=5, trail_mult=3:
        - Candle 1: H=110 → trail = 110 - 15 = 95, stop = max(85, 95) = 95, L=99 > 95 → safe
        - Candle 2: H=120 → trail = 120 - 15 = 105, stop = max(95, 105) = 105, L=108 > 105 → safe
        - Candle 3: H=120 (unchanged), trail stays 105, L=103 <= 105 → exit at C
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            agg = CandleAggregator(5)
            pos = _make_position(
                entry_price=100.0, stop_price=85.0, direction=1,
                trail_mult=3.0, target_mult=20.0, entry_bar=5,
            )
            engine = _setup_engine(tmpdir, exit_resolution=5, positions=[pos])

            # --- Candle 1 (interval 0): Price rises to 110 ---
            _simulate_price_ticks(agg, "BTC", [
                (0.0, 100.0), (2.0, 108.0), (4.0, 110.0),
            ])
            agg.on_price("BTC", 109.0, _ts_ms(5.0))
            candles = agg.flush_completed()
            closed = engine.process_sub_hourly_exits(candles)
            assert closed == 0

            assert pos.highest == 110.0
            assert pos.stop_price >= 95.0

            # --- Candle 2 (interval 1): Price rises further to 120, L stays above trail ---
            _simulate_price_ticks(agg, "BTC", [
                (5.0, 109.0), (7.0, 115.0), (9.0, 120.0),
            ])
            agg.on_price("BTC", 118.0, _ts_ms(10.0))
            candles = agg.flush_completed()
            closed = engine.process_sub_hourly_exits(candles)
            assert closed == 0

            assert pos.highest == 120.0
            assert pos.stop_price >= 105.0

            # --- Candle 3 (interval 2): Price drops to breach tightened stop ---
            # H=118 (no new high), trail stays at 120-15=105, L=103 <= 105 → stop
            _simulate_price_ticks(agg, "BTC", [
                (10.0, 118.0), (12.0, 108.0), (14.0, 103.0),
            ])
            agg.on_price("BTC", 104.0, _ts_ms(15.0))
            candles = agg.flush_completed()
            closed = engine.process_sub_hourly_exits(candles)

            assert closed == 1
            trade = engine.state.position_manager.closed_trades[0]
            assert trade.exit_reason == "stop"
            assert trade.exit_price < 103.0  # Long exit: slippage reduces exit price

    def test_breakeven_ratchet_then_stop(self):
        """Breakeven ratchet moves stop to entry, then price drops to hit it.

        ATR=5, breakeven_atr=2.0, entry=100, trail_mult=3:
        - Candle 1: H=112, L=101, C=111
          highest→112, profit_atr=(112-100)/5=2.4 >= 2.0 → breakeven triggered!
          stop = max(85, 100) = 100 (breakeven)
          trail = 112 - 15 = 97 < 100, stop stays 100
          L=101 > 100 → safe

        - Candle 2: H=110, L=99, C=99
          highest stays 112, trail = 112-15 = 97 < 100, stop stays 100
          L=99 <= 100 → stop hit! Exit at C=99
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            agg = CandleAggregator(5)
            pos = _make_position(
                entry_price=100.0, stop_price=85.0, direction=1,
                trail_mult=3.0, target_mult=20.0, entry_bar=5,
            )
            pos.breakeven_atr = 2.0
            engine = _setup_engine(tmpdir, exit_resolution=5, positions=[pos])

            # --- Candle 1: H=112, L=101, C=111 ---
            # L=101 is ABOVE the breakeven stop of 100, so safe
            _simulate_price_ticks(agg, "BTC", [
                (0.0, 101.0), (2.0, 112.0), (4.0, 111.0),
            ])
            agg.on_price("BTC", 110.0, _ts_ms(5.0))
            candles = agg.flush_completed()
            closed = engine.process_sub_hourly_exits(candles)
            assert closed == 0

            assert pos.breakeven_triggered is True
            assert pos.stop_price >= 100.0

            # --- Candle 2: H=110, L=99, C=99 ---
            # L=99 <= stop=100 → stop hit! Exit at C=99
            _simulate_price_ticks(agg, "BTC", [
                (5.0, 110.0), (7.0, 105.0), (9.0, 99.0),
            ])
            agg.on_price("BTC", 101.0, _ts_ms(10.0))
            candles = agg.flush_completed()
            closed = engine.process_sub_hourly_exits(candles)

            assert closed == 1
            trade = engine.state.position_manager.closed_trades[0]
            assert trade.exit_reason == "stop"
            assert trade.exit_price < 99.0  # Long exit: slippage reduces exit price


# ---------------------------------------------------------------------------
# Test 5: Persistence integrity
# ---------------------------------------------------------------------------

class TestE2EPersistence:
    """Verifies all three persistence artifacts are correct."""

    def test_full_persistence_after_exit(self):
        """Verify trades.jsonl, state.json, and equity.csv content after an exit."""
        with tempfile.TemporaryDirectory() as tmpdir:
            agg = CandleAggregator(5)
            pos = _make_position(
                entry_price=100.0, stop_price=90.0, direction=1,
                margin_usd=10_000.0, entry_bar=5,
            )
            engine = _setup_engine(tmpdir, exit_resolution=5, positions=[pos])

            _simulate_price_ticks(agg, "BTC", [(0.0, 100.0), (3.0, 89.0)])
            agg.on_price("BTC", 91.0, _ts_ms(5.0))
            candles = agg.flush_completed()
            engine.process_sub_hourly_exits(candles)

            # --- trades.jsonl ---
            trades_path = os.path.join(tmpdir, "trades.jsonl")
            with open(trades_path) as f:
                trade = json.loads(f.readline())
            assert trade["token"] == "BTC"
            assert trade["exit_reason"] == "stop"
            assert trade["exit_price"] < 89.0  # Long exit: slippage reduces exit price
            assert trade["direction"] == 1
            assert "exit_timestamp" in trade

            # --- state.json ---
            state_path = os.path.join(tmpdir, "state.json")
            with open(state_path) as f:
                state = json.load(f)
            assert state["tick_counter"] == 10

            # --- equity.csv ---
            equity_path = os.path.join(tmpdir, "equity.csv")
            with open(equity_path) as f:
                lines = f.readlines()
            assert len(lines) == 2  # header + data
            header = lines[0].strip()
            assert "portfolio_equity" in header
            assert "mark_to_market_equity" in header

    def test_multiple_exits_accumulate_in_trades_jsonl(self):
        """Multiple candle flushes should append to trades.jsonl, not overwrite."""
        with tempfile.TemporaryDirectory() as tmpdir:
            agg = CandleAggregator(5)

            pos1 = _make_position(
                token="BTC", entry_price=100.0, stop_price=90.0,
                direction=1, entry_bar=5,
            )
            engine = _setup_engine(tmpdir, exit_resolution=5, positions=[pos1])

            _simulate_price_ticks(agg, "BTC", [(0.0, 100.0), (3.0, 89.0)])
            agg.on_price("BTC", 91.0, _ts_ms(5.0))
            candles = agg.flush_completed()
            engine.process_sub_hourly_exits(candles)

            # Add second position
            pos2 = _make_position(
                token="ETH", strategy_id="s56",
                entry_price=3000.0, stop_price=2800.0,
                direction=1, entry_bar=5,
            )
            pos2.position_id = "ETH:s56:0:primary"
            engine.state.position_manager.open_position(pos2)
            engine._cached_bar_data[("s56", "ETH")] = {
                "atr": 100.0, "adv": 5e5, "regime": 0, "bear_target_mult": 0.0,
            }

            _simulate_price_ticks(agg, "ETH", [(5.0, 3000.0), (8.0, 2700.0)])
            agg.on_price("ETH", 2750.0, _ts_ms(10.0))
            candles = agg.flush_completed()
            engine.process_sub_hourly_exits(candles)

            with open(os.path.join(tmpdir, "trades.jsonl")) as f:
                trade_lines = f.readlines()
            assert len(trade_lines) == 2
            t1 = json.loads(trade_lines[0])
            t2 = json.loads(trade_lines[1])
            assert t1["token"] == "BTC"
            assert t2["token"] == "ETH"

    def test_last_known_prices_updated(self):
        """_last_known_prices should reflect candle close after sub-hourly exit."""
        with tempfile.TemporaryDirectory() as tmpdir:
            agg = CandleAggregator(5)
            pos = _make_position(
                entry_price=100.0, stop_price=90.0, direction=1, entry_bar=5,
            )
            engine = _setup_engine(tmpdir, exit_resolution=5, positions=[pos])

            _simulate_price_ticks(agg, "BTC", [(0.0, 100.0), (3.0, 88.5)])
            agg.on_price("BTC", 91.0, _ts_ms(5.0))
            candles = agg.flush_completed()
            engine.process_sub_hourly_exits(candles)

            assert engine._last_known_prices.get("BTC") == 88.5


# ---------------------------------------------------------------------------
# Test 6: All resolutions produce correct candle boundaries
# ---------------------------------------------------------------------------

class TestE2EResolutions:
    """Verify candle boundaries for all supported resolutions."""

    @pytest.mark.parametrize("resolution,boundary_minute", [
        (1, 1),
        (5, 5),
        (15, 15),
        (30, 30),
    ])
    def test_candle_boundary(self, resolution, boundary_minute):
        """Candle completes exactly at the resolution boundary."""
        agg = CandleAggregator(resolution)

        half = boundary_minute / 2
        agg.on_price("BTC", 100.0, _ts_ms(0))
        agg.on_price("BTC", 105.0, _ts_ms(half))
        agg.on_price("BTC", 95.0, _ts_ms(boundary_minute - 0.1))

        assert agg.flush_completed() == {}

        agg.on_price("BTC", 101.0, _ts_ms(boundary_minute))
        candles = agg.flush_completed()

        assert "BTC" in candles
        h, l, c = candles["BTC"]
        assert h == 105.0
        assert l == 95.0
        assert c == 95.0  # Last tick before boundary

    @pytest.mark.parametrize("resolution", [1, 5, 15, 30])
    def test_exit_at_resolution(self, resolution):
        """Stop breach detected at each supported resolution."""
        with tempfile.TemporaryDirectory() as tmpdir:
            agg = CandleAggregator(resolution)
            pos = _make_position(
                entry_price=100.0, stop_price=90.0, direction=1, entry_bar=5,
            )
            engine = _setup_engine(tmpdir, exit_resolution=resolution, positions=[pos])

            agg.on_price("BTC", 100.0, _ts_ms(0))
            agg.on_price("BTC", 88.0, _ts_ms(resolution * 0.5))

            agg.on_price("BTC", 89.0, _ts_ms(resolution))
            candles = agg.flush_completed()

            closed = engine.process_sub_hourly_exits(candles)
            assert closed == 1
            assert engine.state.position_manager.closed_trades[0].exit_reason == "stop"


# ---------------------------------------------------------------------------
# Test 7: Short position e2e
# ---------------------------------------------------------------------------

class TestE2EShortPositions:
    """Verify short position exits work through the full pipeline."""

    def test_short_stop_breach(self):
        """Short position: high breaches stop → exit at candle close."""
        with tempfile.TemporaryDirectory() as tmpdir:
            agg = CandleAggregator(5)
            pos = _make_position(
                entry_price=100.0, stop_price=110.0, direction=-1,
                entry_bar=5, highest=100.0, lowest=100.0,
            )
            engine = _setup_engine(tmpdir, exit_resolution=5, positions=[pos])

            _simulate_price_ticks(agg, "BTC", [
                (0.0, 100.0), (2.0, 108.0), (4.0, 112.0),
            ])
            agg.on_price("BTC", 111.0, _ts_ms(5.0))
            candles = agg.flush_completed()

            closed = engine.process_sub_hourly_exits(candles)
            assert closed == 1
            trade = engine.state.position_manager.closed_trades[0]
            assert trade.exit_reason == "stop"
            assert trade.exit_price > 112.0  # Short exit: slippage raises exit price
            assert engine.state.realized_pnl < 0

    def test_short_target_hit(self):
        """Short position: price drops to target → exit at candle close.

        Short target = entry(100) - target_mult(3) * ATR(5) = 85.
        Use wide trail_mult=10 so trail stop = lowest(83) + 10*5 = 133, well above H.
        L=83 <= 85 → target hit, exit at C=84.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            agg = CandleAggregator(5)
            pos = _make_position(
                entry_price=100.0, stop_price=120.0, direction=-1,
                target_mult=3.0, trail_mult=10.0, entry_bar=5,
                highest=100.0, lowest=100.0,
            )
            engine = _setup_engine(tmpdir, exit_resolution=5, positions=[pos])

            # H=100, L=83, C=84
            # Trail tightening: lowest = min(100, 83) = 83
            # trail = 83 + 10*5 = 133 → stop = min(120, 133) = 120 (no change)
            # Target check: L=83 <= entry(100) - target(3)*ATR(5) = 85 → target hit
            _simulate_price_ticks(agg, "BTC", [
                (0.0, 100.0), (2.0, 90.0), (4.0, 83.0),
            ])
            agg.on_price("BTC", 84.0, _ts_ms(5.0))
            candles = agg.flush_completed()

            closed = engine.process_sub_hourly_exits(candles)
            assert closed == 1
            trade = engine.state.position_manager.closed_trades[0]
            assert trade.exit_reason == "target"
            assert trade.exit_price > 83.0  # Short exit: slippage raises exit price
            assert engine.state.realized_pnl > 0
