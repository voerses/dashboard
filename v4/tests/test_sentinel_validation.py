"""Acceptance tests for Task 9: Exit validation + dynamic subscriptions (AC14, AC15).

Tests verify:
  - Price validation: exit confirmed when WS and REST within 150 bps
  - Exit held pending when divergence > 150 bps
  - Override: execute if WS consistent and REST failed/timed out
  - Dynamic subscription: new token subscribed when appears in stops.json
  - Unsubscribe when token removed
  - Cascade logging: >5 exits in 5 min logged as cascade_event

All tests use synthetic data -- no real market data required.
These tests MUST FAIL until validation is implemented (RED phase).
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "v4"))

import pytest

from v4.breach_detector import BreachDetector
from v4.price_monitor import PriceMonitor
from v4.stop_store import StopLevel, StopStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_stop_level(**overrides) -> StopLevel:
    """Build a StopLevel with representative defaults."""
    defaults = dict(
        position_id="BTC:s56:100:primary",
        token="BTC",
        strategy_id="s56",
        direction=1,
        stop_price=64_000.0,
        cb_price=60_000.0,
        target_price=80_000.0,
        estimated_liq_price=50_000.0,
        entry_price=66_000.0,
        margin_usd=10_000.0,
        quantity=0.15,
        leverage=1.0,
        is_perp=True,
        no_stop_bars=0,
        bars_held=10,
        stop_active=True,
        convex_exit=False,
        trail_mult=3.0,
        cur_atr=1200.0,
        highest=70_000.0,
        lowest=62_000.0,
        has_trail_schedule=False,
        chandelier_lookback=0,
        cumulative_funding=-12.5,
        fee_rate=0.0004,
    )
    defaults.update(overrides)
    return StopLevel(**defaults)


def _make_detector(tmp_path, mode="live") -> BreachDetector:
    """Create a BreachDetector in live mode for validation tests."""
    store = StopStore(state_dir=tmp_path)
    tiers = {"btc_eth": 30, "top10": 60, "other": 90}
    return BreachDetector(
        portfolio_name="test_portfolio",
        stop_store=store,
        state_dir=tmp_path,
        sentinel_mode=mode,
        confirmation_tiers=tiers,
    )


# ===================================================================
# Test: Price validation -- WS and REST within 150 bps
# ===================================================================

class TestPriceValidationPass:
    """Exit confirmed when WS and REST prices diverge < 150 bps."""

    def test_validation_passes_within_threshold(self, tmp_path):
        """Exit confirmed when WS=63500 and REST=63550 (< 150 bps)."""
        detector = _make_detector(tmp_path)
        ws_price = 63_500.0
        rest_price = 63_550.0
        max_divergence_bps = 150

        divergence_bps = abs(ws_price - rest_price) / ws_price * 10_000
        assert divergence_bps < max_divergence_bps

        result = detector.validate_exit_price(ws_price, rest_price, max_divergence_bps)
        assert result["valid"] is True

    def test_validation_passes_exact_match(self, tmp_path):
        """Exit confirmed when WS and REST prices are identical."""
        detector = _make_detector(tmp_path)
        result = detector.validate_exit_price(63_500.0, 63_500.0, 150)
        assert result["valid"] is True


# ===================================================================
# Test: Exit held pending when divergence > 150 bps
# ===================================================================

class TestPriceValidationFail:
    """Exit held pending when WS and REST diverge > 150 bps."""

    def test_validation_fails_above_threshold(self, tmp_path):
        """Exit held pending when WS=63500 and REST=64600 (> 150 bps)."""
        detector = _make_detector(tmp_path)
        ws_price = 63_500.0
        rest_price = 64_600.0  # ~173 bps divergence
        max_divergence_bps = 150

        divergence_bps = abs(ws_price - rest_price) / ws_price * 10_000
        assert divergence_bps > max_divergence_bps

        result = detector.validate_exit_price(ws_price, rest_price, max_divergence_bps)
        assert result["valid"] is False

    def test_large_divergence_held_pending(self, tmp_path):
        """Very large divergence (>5%) holds exit pending."""
        detector = _make_detector(tmp_path)
        ws_price = 63_500.0
        rest_price = 67_000.0  # ~550 bps
        result = detector.validate_exit_price(ws_price, rest_price, 150)
        assert result["valid"] is False


# ===================================================================
# Test: Override -- execute if WS consistent and REST failed
# ===================================================================

class TestPriceValidationOverride:
    """Override: execute exit if WS consistent and REST failed/timed out."""

    def test_override_on_rest_failure(self, tmp_path):
        """Exit executed when REST is unavailable and WS is consistent."""
        detector = _make_detector(tmp_path)
        ws_price = 63_500.0
        rest_price = None  # REST failed
        result = detector.validate_exit_price(
            ws_price, rest_price, 150, ws_consistent=True,
        )
        assert result["valid"] is True
        assert result.get("override") is True

    def test_no_override_when_ws_inconsistent(self, tmp_path):
        """No override when WS is inconsistent and REST failed."""
        detector = _make_detector(tmp_path)
        ws_price = 63_500.0
        rest_price = None  # REST failed
        result = detector.validate_exit_price(
            ws_price, rest_price, 150, ws_consistent=False,
        )
        assert result["valid"] is False


# ===================================================================
# Test: Dynamic subscription -- new token subscribed
# ===================================================================

class TestDynamicSubscription:
    """Dynamic stream subscription when stops.json changes."""

    def test_new_token_subscribed(self):
        """New token in stops.json triggers subscription."""
        monitor = PriceMonitor(callback=lambda *a: None)
        monitor._subscribed_tokens = {"BTC", "ETH"}

        new_tokens = {"BTC", "ETH", "SOL"}
        to_subscribe, to_unsubscribe = monitor.compute_subscription_changes(
            current=monitor._subscribed_tokens,
            desired=new_tokens,
        )

        assert "SOL" in to_subscribe
        assert len(to_unsubscribe) == 0

    def test_removed_token_unsubscribed(self):
        """Token removed from stops.json triggers unsubscription."""
        monitor = PriceMonitor(callback=lambda *a: None)
        monitor._subscribed_tokens = {"BTC", "ETH", "SOL"}

        new_tokens = {"BTC", "ETH"}
        to_subscribe, to_unsubscribe = monitor.compute_subscription_changes(
            current=monitor._subscribed_tokens,
            desired=new_tokens,
        )

        assert "SOL" in to_unsubscribe
        assert len(to_subscribe) == 0

    def test_no_changes_when_same(self):
        """No subscription changes when token set unchanged."""
        monitor = PriceMonitor(callback=lambda *a: None)
        monitor._subscribed_tokens = {"BTC", "ETH"}

        to_subscribe, to_unsubscribe = monitor.compute_subscription_changes(
            current=monitor._subscribed_tokens,
            desired={"BTC", "ETH"},
        )

        assert len(to_subscribe) == 0
        assert len(to_unsubscribe) == 0


# ===================================================================
# Test: Cascade logging
# ===================================================================

class TestCascadeLogging:
    """Cascade event logged when >5 exits in 5 minutes."""

    def test_cascade_event_logged(self, tmp_path):
        """More than 5 exits in 5 minutes logs a cascade_event."""
        detector = _make_detector(tmp_path, mode="live")
        base_time = time.time()

        # Record 6 exits within 5 minutes
        for i in range(6):
            detector.record_exit_timestamp(base_time + i * 30)  # 30s apart

        cascade_events = detector.get_cascade_events()
        assert len(cascade_events) >= 1
        assert cascade_events[0]["event_type"] == "cascade_event"
        assert cascade_events[0]["exit_count"] >= 6

    def test_no_cascade_below_threshold(self, tmp_path):
        """Fewer than 6 exits in 5 minutes does NOT log cascade."""
        detector = _make_detector(tmp_path, mode="live")
        base_time = time.time()

        # Record only 4 exits
        for i in range(4):
            detector.record_exit_timestamp(base_time + i * 30)

        cascade_events = detector.get_cascade_events()
        assert len(cascade_events) == 0

    def test_no_cascade_spread_over_time(self, tmp_path):
        """6 exits spread over 10 minutes does NOT trigger cascade."""
        detector = _make_detector(tmp_path, mode="live")
        base_time = time.time()

        # 6 exits, 2 minutes apart (total span = 10 minutes)
        for i in range(6):
            detector.record_exit_timestamp(base_time + i * 120)

        cascade_events = detector.get_cascade_events()
        assert len(cascade_events) == 0
