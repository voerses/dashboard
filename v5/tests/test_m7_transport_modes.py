"""M7/M11 — VenueCapabilities.supported_transport_modes additive field (AC-D15).

Reviewer H5 fix: AC-D15 needs its own dedicated M7 test file (Tasks.md Task 16
previously pointed at M6-frozen test_m6_instruments.py). This file covers the
additive field + strategy-facing symmetry with supported_data_classes (M11).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


class TestSupportedTransportModesField:
    """AC-D15 — VenueCapabilities gains supported_transport_modes frozenset field."""

    def test_field_exists_in_annotations(self):
        from v5.data.instruments import VenueCapabilities
        ann = getattr(VenueCapabilities, "__annotations__", {}) or {}
        assert "supported_transport_modes" in ann, (
            "AC-D15: VenueCapabilities must declare 'supported_transport_modes: frozenset[TransportMode]'"
        )

    def test_binance_declares_push_pull_once_pull_scheduled(self):
        """AC-D15 — Binance perp declares its 3 transport modes (not REPLAY)."""
        from v5.data.instruments import InstrumentRegistry
        from v5.data.streams import TransportMode, Venue

        caps = InstrumentRegistry().capabilities(Venue.BINANCE)
        assert hasattr(caps, "supported_transport_modes"), (
            "AC-D15: attribute missing at runtime"
        )
        modes = caps.supported_transport_modes
        assert TransportMode.PUSH in modes
        assert TransportMode.PULL_ONCE in modes
        assert TransportMode.PULL_SCHEDULED in modes
        # Binance is live venue — no REPLAY
        assert TransportMode.REPLAY not in modes, (
            "AC-D15: live venue Binance should NOT declare REPLAY"
        )


class TestStrategyFacingSymmetry:
    """AC-D15 — strategy API gets venue.supports(TransportMode.PUSH) symmetry
    with existing venue.supports(TradeData)."""

    def test_venue_supports_push_returns_true_for_binance(self):
        from v5.data.instruments import InstrumentRegistry
        from v5.data.streams import TransportMode, Venue

        caps = InstrumentRegistry().capabilities(Venue.BINANCE)
        # Strategies get a symmetric supports() check — brief §M6 carryover item 3
        assert caps.supports(TransportMode.PUSH) is True

    def test_venue_supports_replay_returns_false_for_binance(self):
        from v5.data.instruments import InstrumentRegistry
        from v5.data.streams import TransportMode, Venue

        caps = InstrumentRegistry().capabilities(Venue.BINANCE)
        assert caps.supports(TransportMode.REPLAY) is False


class TestAdditiveFieldDoesNotBreakM6Tests:
    """AC-D15 — adding a field must be backward-compatible."""

    def test_venue_capabilities_constructor_accepts_new_field(self):
        """New field defaults to empty frozenset() OR required with explicit arg.
        Either way, M6/M11-style constructor calls must continue to work."""
        from v5.data.instruments import VenueCapabilities
        from v5.data.streams import BarData, TradeData, TransportMode, Venue

        caps = VenueCapabilities(
            venue=Venue.BINANCE,
            supported_asset_classes=frozenset({"spot", "perp"}),
            supported_data_classes=frozenset({BarData, TradeData}),
            min_bar_resolution_minutes=1,
            has_funding=True,
            has_mark_price=True,
            has_trade_tape=True,
            rest_weight_budget_per_min=1200,
            supported_price_types=frozenset({"LAST"}),
            supported_transport_modes=frozenset({TransportMode.PUSH}),
        )
        assert caps.supported_transport_modes == frozenset({TransportMode.PUSH})
