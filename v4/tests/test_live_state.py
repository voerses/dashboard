"""Acceptance tests for state persistence extensions (AC10, AC12, AC16).

Tests verify:
  - AC10: append_rebalances writes JSONL with correct schema
  - AC10: append_rebalances appends without overwriting
  - AC12: serialize/deserialize round-trip for independent mode (multiple strategy states)
  - AC12: Independent mode tick_counter stored at top level
  - AC16: _closed_trade_to_dict includes tick field
  - AC16: truncate_after_tick removes entries with tick > N from trades.jsonl
  - AC16: truncate_after_tick removes entries with tick > N from equity.csv
  - Malformed JSONL lines are skipped on read

All tests use synthetic data -- no real market data required.
These tests MUST FAIL until paper_state.py extensions are implemented (RED phase).
"""
from __future__ import annotations

import csv
import json
import os
import sys
import tempfile
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "v4"))

import numpy as np
import pytest

from v4.paper_state import (
    serialize_state,
    deserialize_state,
    append_trades,
    append_equity,
    _closed_trade_to_dict,
)
from v4.position import Position, ClosedTrade, PositionManager
from v4.simulator import SimulationState

# These functions don't exist yet — import lazily so tests can collect
def _get_append_rebalances():
    from v4.paper_state import append_rebalances
    return append_rebalances

def _get_truncate_after_tick():
    from v4.paper_state import truncate_after_tick
    return truncate_after_tick

def _get_read_trades_jsonl():
    from v4.paper_state import read_trades_jsonl
    return read_trades_jsonl

def _get_serialize_engine_state():
    from v4.paper_state import serialize_engine_state
    return serialize_engine_state

def _get_deserialize_engine_state():
    from v4.paper_state import deserialize_engine_state
    return deserialize_engine_state


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_test_position(
    token: str = "BTC",
    strategy_id: str = "s56",
    entry_bar: int = 42,
    entry_price: float = 68_000.0,
    margin: float = 10_000.0,
) -> Position:
    """Build a Position with representative fields for serialization tests."""
    quantity = margin / entry_price
    pid = f"{token}:{strategy_id}:{entry_bar}:primary"
    return Position(
        position_id=pid,
        token=token,
        strategy_id=strategy_id,
        leg="primary",
        entry_bar=entry_bar,
        entry_price=entry_price,
        direction=1,
        quantity=quantity,
        margin_usd=margin,
        leverage=1.0,
        is_perp=False,
        fee_rate=0.0005,
        stop_mult=2.5,
        trail_mult=3.0,
        target_mult=6.0,
        no_stop_bars=6,
        min_hold=12,
        max_hold=720,
        exit_regimes={4},
        convex_exit=False,
        rsi_exit_level=999.0,
        trail_schedule=None,
        stop_price=65_000.0,
        highest=72_000.0,
        lowest=68_000.0,
        initial_risk=2_000.0,
        cumulative_funding=0.0,
    )


def _make_closed_trade(
    token: str = "BTC",
    strategy_id: str = "s56",
    entry_bar: int = 10,
    exit_bar: int = 20,
    pnl: float = 500.0,
    tick: int = 5,
) -> ClosedTrade:
    """Build a ClosedTrade for testing."""
    return ClosedTrade(
        position_id=f"{token}:{strategy_id}:{entry_bar}:primary",
        token=token,
        strategy_id=strategy_id,
        leg="primary",
        entry_bar=entry_bar,
        exit_bar=exit_bar,
        entry_price=68_000.0,
        exit_price=69_000.0,
        direction=1,
        margin_usd=10_000.0,
        pnl=pnl,
        funding_cost=0.0,
        entry_fee=5.0,
        exit_fee=5.0,
        hold_bars=exit_bar - entry_bar,
        exit_reason="target",
        is_perp=False,
    )


def _make_rebalance_record():
    """Build a rebalance record dict for testing."""
    return {
        "timestamp": "2025-01-15T12:00:00Z",
        "tick": 42,
        "n_entry_candidates": 5,
        "spot_capital_needed": 10000.0,
        "perp_capital_needed": 8000.0,
        "spot_shadow_available": 50000.0,
        "perp_shadow_available": 45000.0,
        "transfer_needed_usd": 0.0,
        "direction": "none",
        "would_have_blocked_entries": 0,
        "blocked_entry_tokens": [],
        "spot_shadow_after": 50000.0,
        "perp_shadow_after": 45000.0,
        "spot_deployed": 30000.0,
        "perp_deployed": 25000.0,
        "imbalance_pct": 2.5,
    }


# ===================================================================
# Test: AC10 — append_rebalances writes JSONL with correct schema
# ===================================================================

class TestAppendRebalances:
    """AC10: append_rebalances writes JSONL with correct schema."""

    def test_writes_jsonl_with_correct_schema(self):
        """append_rebalances writes records as JSON lines."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "rebalances.jsonl")
            records = [_make_rebalance_record()]

            append_rebalances = _get_append_rebalances()
            append_rebalances(records, path)

            assert os.path.exists(path)
            with open(path) as f:
                lines = f.readlines()
            assert len(lines) == 1

            parsed = json.loads(lines[0])
            assert parsed["tick"] == 42
            assert parsed["timestamp"] == "2025-01-15T12:00:00Z"
            assert parsed["direction"] == "none"
            assert isinstance(parsed["blocked_entry_tokens"], list)

    def test_appends_without_overwriting(self):
        """append_rebalances appends to existing file without overwriting."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "rebalances.jsonl")
            append_rebalances = _get_append_rebalances()

            # Write first batch
            records1 = [_make_rebalance_record()]
            append_rebalances(records1, path)

            # Write second batch
            rec2 = _make_rebalance_record()
            rec2["tick"] = 43
            append_rebalances([rec2], path)

            with open(path) as f:
                lines = f.readlines()
            assert len(lines) == 2
            assert json.loads(lines[0])["tick"] == 42
            assert json.loads(lines[1])["tick"] == 43


# ===================================================================
# Test: AC12 — serialize/deserialize round-trip for independent mode
# ===================================================================

class TestIndependentModeSerialization:
    """AC12: serialize/deserialize handles independent mode (multiple strategy states)."""

    def test_independent_mode_roundtrip(self):
        """Serializing and deserializing independent mode state preserves all strategy states."""
        # Build two strategy states
        state_s56 = SimulationState(initial_capital=100_000.0)
        state_s56.realized_pnl = 1500.0
        state_s56.total_fees = 50.0
        state_s56.total_funding = -10.0
        pos = _make_test_position(token="BTC", strategy_id="s56")
        state_s56.position_manager.open_position(pos)

        state_s57 = SimulationState(initial_capital=100_000.0)
        state_s57.realized_pnl = -200.0
        state_s57.total_fees = 30.0
        state_s57.total_funding = 5.0

        strategy_states = {"s56": state_s56, "s57": state_s57}

        # Serialize in independent mode
        serialize_engine_state = _get_serialize_engine_state()
        deserialize_engine_state = _get_deserialize_engine_state()
        data = serialize_engine_state(
            strategy_states=strategy_states,
            tick_counter=10,
            last_timestamp="2025-01-15T12:00:00Z",
            mode="independent",
        )

        # Verify structure
        assert data["mode"] == "independent"
        assert "strategy_states" in data
        assert "s56" in data["strategy_states"]
        assert "s57" in data["strategy_states"]

        # Deserialize
        restored_states, tick, last_ts = deserialize_engine_state(data)

        assert tick == 10
        assert last_ts == "2025-01-15T12:00:00Z"
        assert "s56" in restored_states
        assert "s57" in restored_states
        assert restored_states["s56"].realized_pnl == pytest.approx(1500.0)
        assert restored_states["s57"].realized_pnl == pytest.approx(-200.0)
        assert restored_states["s56"].position_manager.total_open() == 1

    def test_tick_counter_at_top_level(self):
        """AC12: In independent mode, tick_counter is stored at top level, not per-strategy."""
        state_s56 = SimulationState(initial_capital=100_000.0)
        state_s57 = SimulationState(initial_capital=100_000.0)

        serialize_engine_state = _get_serialize_engine_state()
        data = serialize_engine_state(
            strategy_states={"s56": state_s56, "s57": state_s57},
            tick_counter=25,
            last_timestamp="2025-01-15T13:00:00Z",
            mode="independent",
        )

        # tick_counter must be at top level
        assert data["tick_counter"] == 25
        # tick_counter must NOT be in individual strategy states
        for sid, sdata in data["strategy_states"].items():
            assert "tick_counter" not in sdata or sdata.get("tick_counter", None) is None


# ===================================================================
# Test: AC16 — _closed_trade_to_dict includes tick field
# ===================================================================

class TestClosedTradeDictTick:
    """AC16: _closed_trade_to_dict includes tick field for recovery truncation."""

    def test_closed_trade_dict_has_tick(self):
        """_closed_trade_to_dict output includes a 'tick' key."""
        trade = _make_closed_trade(tick=7)
        d = _closed_trade_to_dict(trade, tick=7)

        assert "tick" in d
        assert d["tick"] == 7


# ===================================================================
# Test: AC16 — truncate_after_tick for trades.jsonl
# ===================================================================

class TestTruncateAfterTickTrades:
    """AC16: truncate_after_tick removes entries with tick > N from trades.jsonl."""

    def test_truncates_trades_after_tick(self):
        """Entries with tick > max_tick are removed from trades.jsonl."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "trades.jsonl")

            # Write trades with various tick values
            trades = [
                {"position_id": "BTC:s56:1:primary", "tick": 1, "pnl": 100},
                {"position_id": "ETH:s56:2:primary", "tick": 2, "pnl": -50},
                {"position_id": "SOL:s56:3:primary", "tick": 3, "pnl": 200},
                {"position_id": "DOGE:s56:4:primary", "tick": 4, "pnl": -100},
            ]
            with open(path, "w") as f:
                for t in trades:
                    f.write(json.dumps(t) + "\n")

            # Truncate after tick 2
            truncate_after_tick = _get_truncate_after_tick()
            truncate_after_tick(path, max_tick=2, format="jsonl")

            with open(path) as f:
                remaining = [json.loads(line) for line in f]
            assert len(remaining) == 2
            assert all(t["tick"] <= 2 for t in remaining)


# ===================================================================
# Test: AC16 — truncate_after_tick for equity.csv
# ===================================================================

class TestTruncateAfterTickEquity:
    """AC16: truncate_after_tick removes entries with tick > N from equity.csv."""

    def test_truncates_equity_after_tick(self):
        """Entries with tick > max_tick are removed from equity.csv."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "equity.csv")

            # Write equity CSV with tick column
            with open(path, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["timestamp", "tick", "portfolio_equity", "mark_to_market_equity"])
                writer.writerow(["2025-01-15T10:00:00Z", 1, 200000, 200100])
                writer.writerow(["2025-01-15T11:00:00Z", 2, 200500, 200600])
                writer.writerow(["2025-01-15T12:00:00Z", 3, 201000, 201200])
                writer.writerow(["2025-01-15T13:00:00Z", 4, 200800, 200900])

            truncate_after_tick = _get_truncate_after_tick()
            truncate_after_tick(path, max_tick=2, format="csv")

            import pandas as pd
            df = pd.read_csv(path)
            assert len(df) == 2
            assert df["tick"].max() <= 2


# ===================================================================
# Test: Malformed JSONL lines are skipped on read
# ===================================================================

class TestMalformedJSONLHandling:
    """Malformed JSONL lines are skipped on read (crash recovery safety)."""

    def test_malformed_lines_skipped(self):
        """Reading trades.jsonl with partial/corrupt lines skips them gracefully."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "trades.jsonl")

            # Write some valid lines and one corrupt line
            with open(path, "w") as f:
                f.write(json.dumps({"position_id": "BTC:s56:1:primary", "tick": 1, "pnl": 100}) + "\n")
                f.write('{"position_id": "ETH:s56:2:prim\n')  # Truncated/corrupt
                f.write(json.dumps({"position_id": "SOL:s56:3:primary", "tick": 3, "pnl": 200}) + "\n")

            # Read should skip the malformed line
            read_trades_jsonl = _get_read_trades_jsonl()
            trades = read_trades_jsonl(path)

            assert len(trades) == 2
            assert trades[0]["position_id"] == "BTC:s56:1:primary"
            assert trades[1]["position_id"] == "SOL:s56:3:primary"
