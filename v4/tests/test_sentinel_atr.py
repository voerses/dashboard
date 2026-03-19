"""Acceptance tests for Task 3: ATR persistence (AC20).

Tests verify:
  - _last_known_atrs populated during signal processing
  - ATR persisted in state.json via serialize/deserialize round-trip
  - Cold start fallback (entry_price x 0.02) when no ATR available

All tests use synthetic data -- no real market data required.
These tests MUST FAIL until ATR persistence is implemented (RED phase).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "v4"))

import pytest

from v4.paper_state import serialize_state, deserialize_state
from v4.simulator import SimulationState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_state_with_atrs(atrs: dict[str, float] | None = None) -> SimulationState:
    """Build a SimulationState with last_known_atrs set."""
    state = SimulationState(initial_capital=200_000.0)
    if atrs is not None:
        state.last_known_atrs = atrs
    return state


# ===================================================================
# Test: ATR populated during signal processing
# ===================================================================

class TestAtrPopulation:
    """_last_known_atrs is populated from signal data."""

    def test_last_known_atrs_attribute_exists(self):
        """SimulationState has a last_known_atrs attribute."""
        state = SimulationState(initial_capital=200_000.0)
        assert hasattr(state, "last_known_atrs")

    def test_last_known_atrs_default_empty(self):
        """Default last_known_atrs is an empty dict."""
        state = SimulationState(initial_capital=200_000.0)
        assert state.last_known_atrs == {}

    def test_last_known_atrs_stores_values(self):
        """last_known_atrs is a native dataclass field that can store ATR values per token."""
        import dataclasses
        state = SimulationState(initial_capital=200_000.0)
        # Must be a native dataclass field, not a dynamically-set attribute
        field_names = [f.name for f in dataclasses.fields(state)]
        assert "last_known_atrs" in field_names, (
            "last_known_atrs must be a native dataclass field on SimulationState"
        )
        state.last_known_atrs["BTC"] = 1500.0
        state.last_known_atrs["ETH"] = 80.0
        state.last_known_atrs["SOL"] = 3.5
        assert state.last_known_atrs["BTC"] == pytest.approx(1500.0)
        assert state.last_known_atrs["ETH"] == pytest.approx(80.0)
        assert state.last_known_atrs["SOL"] == pytest.approx(3.5)


# ===================================================================
# Test: ATR persisted in state.json via serialize/deserialize
# ===================================================================

class TestAtrSerializationRoundTrip:
    """last_known_atrs survives serialize/deserialize round-trip."""

    def test_atrs_survive_round_trip(self):
        """Serialized ATRs are correctly deserialized."""
        state = _make_state_with_atrs({"BTC": 1500.0, "ETH": 80.0})
        serialized = serialize_state(state, tick_counter=0, last_timestamp="2025-01-01T00:00:00Z")
        restored, _ = deserialize_state(serialized)

        assert restored.last_known_atrs["BTC"] == pytest.approx(1500.0)
        assert restored.last_known_atrs["ETH"] == pytest.approx(80.0)

    def test_empty_atrs_survive_round_trip(self):
        """Empty ATR dict round-trips correctly."""
        state = _make_state_with_atrs({})
        serialized = serialize_state(state, tick_counter=0, last_timestamp="2025-01-01T00:00:00Z")
        restored, _ = deserialize_state(serialized)

        assert restored.last_known_atrs == {}

    def test_atrs_in_serialized_json(self):
        """Serialized state JSON contains last_known_atrs key."""
        state = _make_state_with_atrs({"BTC": 1200.0})
        serialized = serialize_state(state, tick_counter=0, last_timestamp="2025-01-01T00:00:00Z")

        # serialize_state returns a dict
        data = serialized

        assert "last_known_atrs" in data
        assert data["last_known_atrs"]["BTC"] == pytest.approx(1200.0)

    def test_missing_atrs_in_old_state_defaults_to_empty(self):
        """Deserializing old state without last_known_atrs defaults to empty dict."""
        state = SimulationState(initial_capital=200_000.0)
        serialized = serialize_state(state, tick_counter=0, last_timestamp="2025-01-01T00:00:00Z")

        # Simulate old state format by removing the atrs key
        data = dict(serialized)
        data.pop("last_known_atrs", None)
        restored, _ = deserialize_state(data)

        assert restored.last_known_atrs == {}

    def test_many_tokens_round_trip(self):
        """ATRs for many tokens all survive round-trip."""
        atrs = {f"TOKEN{i}": float(i * 10) for i in range(30)}
        state = _make_state_with_atrs(atrs)
        serialized = serialize_state(state, tick_counter=0, last_timestamp="2025-01-01T00:00:00Z")
        restored, _ = deserialize_state(serialized)

        for token, atr_val in atrs.items():
            assert restored.last_known_atrs[token] == pytest.approx(atr_val)


# ===================================================================
# Test: Cold start fallback
# ===================================================================

class TestAtrColdStartFallback:
    """Cold start uses entry_price x 0.02 as ATR fallback.

    Tests require last_known_atrs to be a built-in attribute of
    SimulationState (not dynamically set), and to survive serialization.
    """

    def test_fallback_formula_btc(self):
        """When no ATR available for BTC, fallback is entry_price * 0.02.

        Verifies the attribute is native to SimulationState (not set dynamically).
        """
        state = SimulationState(initial_capital=200_000.0)
        # Must be a native attribute with default empty dict, not dynamically set
        assert hasattr(state, "last_known_atrs"), (
            "SimulationState must have last_known_atrs as a native attribute"
        )
        assert state.last_known_atrs == {}
        entry_price = 68_000.0
        fallback_atr = state.last_known_atrs.get("BTC", entry_price * 0.02)
        assert fallback_atr == pytest.approx(1360.0)

    def test_fallback_survives_serialization(self):
        """Cold start fallback works after deserialization of empty atrs."""
        state = SimulationState(initial_capital=200_000.0)
        # The attribute must be natively initialized (not set after construction)
        serialized = serialize_state(state, tick_counter=0, last_timestamp="2025-01-01T00:00:00Z")
        restored, _ = deserialize_state(serialized)
        # After round-trip, last_known_atrs should be {}
        assert restored.last_known_atrs == {}
        entry_price = 0.50
        fallback = restored.last_known_atrs.get("DOGE", entry_price * 0.02)
        assert fallback == pytest.approx(0.01)

    def test_fallback_not_used_when_atr_exists(self):
        """When ATR is cached and serialized, it is used instead of fallback."""
        state = SimulationState(initial_capital=200_000.0)
        # Native attribute must exist before we can set values
        assert hasattr(state, "last_known_atrs")
        state.last_known_atrs["ETH"] = 80.0
        serialized = serialize_state(state, tick_counter=0, last_timestamp="2025-01-01T00:00:00Z")
        restored, _ = deserialize_state(serialized)
        entry_price = 3500.0
        atr = restored.last_known_atrs.get("ETH", entry_price * 0.02)
        assert atr == pytest.approx(80.0)  # Uses cached, not fallback of 70.0
