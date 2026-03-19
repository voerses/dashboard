"""Acceptance tests for Task 7: BreachDetector core + shadow logging (AC4, AC5).

Tests verify:
  - Breach detection: long stop breached when price < stop_price
  - Breach detection: short stop
  - cb_price fires regardless of stop_active
  - Confirmation timer starts on breach
  - Timer cancels on recovery (wick filter)
  - Different confirmation delays per liquidity tier
  - Shadow mode logs to sentinel_shadow.jsonl
  - Shadow mode writes sentinel_recent.json (ring buffer of 50)
  - Shadow mode does NOT write exit_events.jsonl
  - Live mode writes exit_events.jsonl
  - One detector per portfolio (not shared state)

All tests use synthetic data -- no real market data required.
These tests MUST FAIL until breach_detector.py is implemented (RED phase).
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "v4"))

import pytest

from v4.breach_detector import BreachDetector
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
        estimated_liq_price=55_000.0,
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


def _make_detector(tmp_path, mode="shadow", confirmation_tiers=None) -> BreachDetector:
    """Create a BreachDetector with a temporary state_dir."""
    store = StopStore(state_dir=tmp_path)
    tiers = confirmation_tiers or {"btc_eth": 30, "top10": 60, "other": 90}
    return BreachDetector(
        portfolio_name="test_portfolio",
        stop_store=store,
        state_dir=tmp_path,
        sentinel_mode=mode,
        confirmation_tiers=tiers,
    )


# ===================================================================
# Test: Breach detection -- long stop
# ===================================================================

class TestLongStopBreach:
    """Long stop breached when mark_price < stop_price."""

    def test_long_stop_breach_detected(self, tmp_path):
        """Price below stop_price triggers a breach for long position."""
        detector = _make_detector(tmp_path)
        stop = _make_stop_level(direction=1, stop_price=64_000.0, stop_active=True)
        detector.update_stops([stop])

        result, _ = detector.check_price("BTC", 63_500.0, int(time.time() * 1000))
        assert result is not None
        assert result["event_type"] == "breach"

    def test_long_no_breach_above_stop(self, tmp_path):
        """Price above stop_price does NOT trigger breach for long."""
        detector = _make_detector(tmp_path)
        stop = _make_stop_level(direction=1, stop_price=64_000.0, stop_active=True)
        detector.update_stops([stop])

        result, _ = detector.check_price("BTC", 65_000.0, int(time.time() * 1000))
        # No breach event when price is safe
        assert result is None or result.get("event_type") != "breach"


# ===================================================================
# Test: Breach detection -- short stop
# ===================================================================

class TestShortStopBreach:
    """Short stop breached when mark_price > stop_price."""

    def test_short_stop_breach_detected(self, tmp_path):
        """Price above stop_price triggers breach for short position."""
        detector = _make_detector(tmp_path)
        stop = _make_stop_level(direction=-1, stop_price=68_000.0, stop_active=True)
        detector.update_stops([stop])

        result, _ = detector.check_price("BTC", 68_500.0, int(time.time() * 1000))
        assert result is not None
        assert result["event_type"] == "breach"

    def test_short_no_breach_below_stop(self, tmp_path):
        """Price below stop_price does NOT trigger breach for short."""
        detector = _make_detector(tmp_path)
        stop = _make_stop_level(direction=-1, stop_price=68_000.0, stop_active=True)
        detector.update_stops([stop])

        result, _ = detector.check_price("BTC", 67_000.0, int(time.time() * 1000))
        assert result is None or result.get("event_type") != "breach"


# ===================================================================
# Test: cb_price fires regardless of stop_active
# ===================================================================

class TestCircuitBreakerFiring:
    """Circuit breaker fires even when stop_active=False."""

    def test_cb_fires_during_grace_period(self, tmp_path):
        """CB breach detected even when stop_active=False (grace period)."""
        detector = _make_detector(tmp_path)
        stop = _make_stop_level(
            direction=1, stop_price=64_000.0, cb_price=60_000.0,
            stop_active=False,  # In grace period
        )
        detector.update_stops([stop])

        result, _ = detector.check_price("BTC", 59_500.0, int(time.time() * 1000))
        assert result is not None
        assert "cb" in result.get("exit_reason", "").lower() or \
               result.get("event_type") == "breach"


# ===================================================================
# Test: Confirmation timer starts on breach
# ===================================================================

class TestConfirmationTimer:
    """Confirmation timer starts when breach is detected."""

    def test_breach_starts_confirmation_timer(self, tmp_path):
        """First breach starts a pending confirmation timer."""
        detector = _make_detector(tmp_path)
        stop = _make_stop_level(direction=1, stop_price=64_000.0, stop_active=True)
        detector.update_stops([stop])

        detector.check_price("BTC", 63_500.0, int(time.time() * 1000))

        # Should have an active confirmation timer
        active = detector.get_active_confirmations()
        assert len(active) >= 1
        assert any(c["position_id"] == "BTC:s56:100:primary" for c in active)


# ===================================================================
# Test: Timer cancels on recovery (wick filter)
# ===================================================================

class TestWickFilter:
    """Price recovery cancels confirmation timer (wick filter)."""

    def test_recovery_cancels_timer(self, tmp_path):
        """Price returning above stop cancels confirmation timer."""
        detector = _make_detector(tmp_path)
        stop = _make_stop_level(direction=1, stop_price=64_000.0, stop_active=True)
        detector.update_stops([stop])

        # Breach
        detector.check_price("BTC", 63_500.0, int(time.time() * 1000))
        assert len(detector.get_active_confirmations()) >= 1

        # Recovery
        detector.check_price("BTC", 65_000.0, int(time.time() * 1000))
        active = detector.get_active_confirmations()
        btc_confirmations = [c for c in active
                             if c["position_id"] == "BTC:s56:100:primary"]
        assert len(btc_confirmations) == 0

    def test_wick_filtered_event_logged(self, tmp_path):
        """Wick filter event is logged with expected fields."""
        detector = _make_detector(tmp_path)
        stop = _make_stop_level(direction=1, stop_price=64_000.0, stop_active=True)
        detector.update_stops([stop])

        # Breach then recovery
        detector.check_price("BTC", 63_500.0, int(time.time() * 1000))
        detector.check_price("BTC", 65_000.0, int(time.time() * 1000))

        filtered = detector.get_wick_filtered_events()
        assert len(filtered) >= 1
        event = filtered[0]
        assert event["position_id"] == "BTC:s56:100:primary"
        assert "breach_price" in event
        assert "recovery_price" in event
        assert "duration_s" in event
        assert "liquidity_tier" in event


# ===================================================================
# Test: Different confirmation delays per liquidity tier
# ===================================================================

class TestLiquidityTierDelays:
    """Confirmation delays vary by liquidity tier."""

    def test_btc_eth_tier_delay(self, tmp_path):
        """BTC/ETH tier has 30s confirmation delay."""
        tiers = {"btc_eth": 30, "top10": 60, "other": 90}
        detector = _make_detector(tmp_path, confirmation_tiers=tiers)
        delay = detector._get_confirmation_delay("BTC")
        assert delay == 30

    def test_top10_tier_delay(self, tmp_path):
        """Top-10 alt tier has 60s confirmation delay."""
        tiers = {"btc_eth": 30, "top10": 60, "other": 90}
        detector = _make_detector(tmp_path, confirmation_tiers=tiers)
        delay = detector._get_confirmation_delay("SOL")  # top-10 alt
        assert delay == 60

    def test_other_tier_delay(self, tmp_path):
        """Other tokens have 90s confirmation delay."""
        tiers = {"btc_eth": 30, "top10": 60, "other": 90}
        detector = _make_detector(tmp_path, confirmation_tiers=tiers)
        delay = detector._get_confirmation_delay("OBSCURECOIN")
        assert delay == 90


# ===================================================================
# Test: Shadow mode logs to sentinel_shadow.jsonl
# ===================================================================

class TestShadowLogging:
    """Shadow mode logs events to sentinel_shadow.jsonl."""

    def test_shadow_event_logged(self, tmp_path):
        """Confirmed breach in shadow mode writes to sentinel_shadow.jsonl."""
        detector = _make_detector(tmp_path, mode="shadow")
        stop = _make_stop_level(direction=1, stop_price=64_000.0, stop_active=True)
        detector.update_stops([stop])

        # Simulate confirmed breach (breach + time passage)
        now_ms = int(time.time() * 1000)
        detector.check_price("BTC", 63_500.0, now_ms)
        detector.confirm_pending_exits(current_time_s=time.time() + 31)

        shadow_file = tmp_path / "sentinel_shadow.jsonl"
        assert shadow_file.exists()

        lines = shadow_file.read_text().strip().split("\n")
        assert len(lines) >= 1
        event = json.loads(lines[0])
        assert event["position_id"] == "BTC:s56:100:primary"
        assert event["token"] == "BTC"
        assert "event_type" in event
        assert "breach_price" in event


# ===================================================================
# Test: Shadow mode writes sentinel_recent.json (ring buffer of 50)
# ===================================================================

class TestSentinelRecentRingBuffer:
    """Shadow mode writes sentinel_recent.json with ring buffer of 50."""

    def test_sentinel_recent_written(self, tmp_path):
        """sentinel_recent.json is written on confirmed breach."""
        detector = _make_detector(tmp_path, mode="shadow")
        stop = _make_stop_level(direction=1, stop_price=64_000.0, stop_active=True)
        detector.update_stops([stop])

        now_ms = int(time.time() * 1000)
        detector.check_price("BTC", 63_500.0, now_ms)
        detector.confirm_pending_exits(current_time_s=time.time() + 31)

        recent_file = tmp_path / "sentinel_recent.json"
        assert recent_file.exists()

        data = json.loads(recent_file.read_text())
        assert isinstance(data, list)
        assert len(data) >= 1

    def test_ring_buffer_capped_at_50(self, tmp_path):
        """sentinel_recent.json never exceeds 50 entries."""
        detector = _make_detector(tmp_path, mode="shadow")

        # Generate 60 breach events
        for i in range(60):
            pid = f"TOKEN{i}:s56:100:primary"
            token = f"TOKEN{i}"
            stop = _make_stop_level(
                position_id=pid, token=token, direction=1,
                stop_price=100.0, stop_active=True,
            )
            detector.update_stops([stop])
            detector.check_price(token, 90.0, int(time.time() * 1000))
            detector.confirm_pending_exits(current_time_s=time.time() + 91)

        recent_file = tmp_path / "sentinel_recent.json"
        data = json.loads(recent_file.read_text())
        assert len(data) <= 50


# ===================================================================
# Test: Shadow mode does NOT write exit_events.jsonl
# ===================================================================

class TestShadowNoExitEvents:
    """Shadow mode does not write exit_events.jsonl."""

    def test_no_exit_events_in_shadow_mode(self, tmp_path):
        """Shadow mode confirmed breach does NOT create exit_events.jsonl."""
        detector = _make_detector(tmp_path, mode="shadow")
        stop = _make_stop_level(direction=1, stop_price=64_000.0, stop_active=True)
        detector.update_stops([stop])

        now_ms = int(time.time() * 1000)
        detector.check_price("BTC", 63_500.0, now_ms)
        detector.confirm_pending_exits(current_time_s=time.time() + 31)

        exit_events_file = tmp_path / "exit_events.jsonl"
        assert not exit_events_file.exists()


# ===================================================================
# Test: Live mode writes exit_events.jsonl
# ===================================================================

class TestLiveExitEvents:
    """Live mode writes exit_events.jsonl via StopStore."""

    def test_exit_events_written_in_live_mode(self, tmp_path):
        """Live mode confirmed breach writes to exit_events.jsonl."""
        detector = _make_detector(tmp_path, mode="live")
        stop = _make_stop_level(direction=1, stop_price=64_000.0, stop_active=True)
        detector.update_stops([stop])

        now_ms = int(time.time() * 1000)
        detector.check_price("BTC", 63_500.0, now_ms)
        detector.confirm_pending_exits(current_time_s=time.time() + 31)

        exit_events_file = tmp_path / "exit_events.jsonl"
        assert exit_events_file.exists()


# ===================================================================
# Test: One detector per portfolio (not shared state)
# ===================================================================

class TestDetectorIsolation:
    """Each portfolio gets its own BreachDetector with isolated state."""

    def test_separate_detectors_no_shared_state(self, tmp_path):
        """Two detectors for different portfolios have independent state."""
        dir_a = tmp_path / "portfolio_a"
        dir_b = tmp_path / "portfolio_b"
        dir_a.mkdir()
        dir_b.mkdir()

        det_a = _make_detector(dir_a)
        det_b = _make_detector(dir_b)

        stop_a = _make_stop_level(position_id="BTC:s56:100:primary", token="BTC")
        stop_b = _make_stop_level(position_id="ETH:s60:100:primary", token="ETH",
                                   stop_price=3200.0)

        det_a.update_stops([stop_a])
        det_b.update_stops([stop_b])

        # Breach in A should not affect B
        det_a.check_price("BTC", 63_500.0, int(time.time() * 1000))
        active_a = det_a.get_active_confirmations()
        active_b = det_b.get_active_confirmations()

        assert len(active_a) >= 1
        assert len(active_b) == 0


# ===================================================================
# Test: Venue filtering — spot prices only match spot stops
# ===================================================================

class TestVenueFiltering:
    """is_perp parameter filters stops by venue."""

    def test_perp_price_only_checks_perp_stops(self, tmp_path):
        """Perp price (is_perp=True) only triggers perp stop breaches."""
        detector = _make_detector(tmp_path)
        perp_stop = _make_stop_level(
            position_id="BTC:s56:100:primary", token="BTC",
            direction=1, stop_price=64_000.0, stop_active=True, is_perp=True,
        )
        spot_stop = _make_stop_level(
            position_id="BTC:s86:100:primary", token="BTC",
            direction=1, stop_price=64_000.0, stop_active=True, is_perp=False,
        )
        detector.update_stops([perp_stop, spot_stop])

        # Perp price should only trigger perp stop
        result, _ = detector.check_price("BTC", 63_500.0, int(time.time() * 1000), is_perp=True)
        assert result is not None
        assert result["position_id"] == "BTC:s56:100:primary"

    def test_spot_price_only_checks_spot_stops(self, tmp_path):
        """Spot price (is_perp=False) only triggers spot stop breaches."""
        detector = _make_detector(tmp_path)
        perp_stop = _make_stop_level(
            position_id="BTC:s56:100:primary", token="BTC",
            direction=1, stop_price=64_000.0, stop_active=True, is_perp=True,
        )
        spot_stop = _make_stop_level(
            position_id="BTC:s86:100:primary", token="BTC",
            direction=1, stop_price=64_000.0, stop_active=True, is_perp=False,
        )
        detector.update_stops([perp_stop, spot_stop])

        # Spot price should only trigger spot stop
        result, _ = detector.check_price("BTC", 63_500.0, int(time.time() * 1000), is_perp=False)
        assert result is not None
        assert result["position_id"] == "BTC:s86:100:primary"

    def test_spot_price_does_not_trigger_perp_stop(self, tmp_path):
        """Spot price does NOT trigger a perp-only stop."""
        detector = _make_detector(tmp_path)
        perp_stop = _make_stop_level(
            position_id="BTC:s56:100:primary", token="BTC",
            direction=1, stop_price=64_000.0, stop_active=True, is_perp=True,
        )
        detector.update_stops([perp_stop])

        result, _ = detector.check_price("BTC", 63_500.0, int(time.time() * 1000), is_perp=False)
        assert result is None

    def test_perp_price_does_not_trigger_spot_stop(self, tmp_path):
        """Perp price does NOT trigger a spot-only stop."""
        detector = _make_detector(tmp_path)
        spot_stop = _make_stop_level(
            position_id="BTC:s86:100:primary", token="BTC",
            direction=1, stop_price=64_000.0, stop_active=True, is_perp=False,
        )
        detector.update_stops([spot_stop])

        result, _ = detector.check_price("BTC", 63_500.0, int(time.time() * 1000), is_perp=True)
        assert result is None


# ===================================================================
# Test: Mixed venue positions — same token, both spot and perp
# ===================================================================

class TestMixedVenuePositions:
    """Same token with both spot and perp positions."""

    def test_both_venues_breach_independently(self, tmp_path):
        """Spot and perp stops for same token breach independently."""
        detector = _make_detector(tmp_path)
        perp_stop = _make_stop_level(
            position_id="BTC:s56:100:primary", token="BTC",
            direction=1, stop_price=64_000.0, stop_active=True, is_perp=True,
        )
        spot_stop = _make_stop_level(
            position_id="BTC:s86:100:primary", token="BTC",
            direction=1, stop_price=63_000.0, stop_active=True, is_perp=False,
        )
        detector.update_stops([perp_stop, spot_stop])

        # Perp breach at 63_500 — below perp stop (64k) but above spot stop (63k)
        result_perp, _ = detector.check_price("BTC", 63_500.0, int(time.time() * 1000), is_perp=True)
        assert result_perp is not None
        assert result_perp["position_id"] == "BTC:s56:100:primary"

        # Spot price at 63_500 — above spot stop (63k), no breach
        result_spot, _ = detector.check_price("BTC", 63_500.0, int(time.time() * 1000), is_perp=False)
        assert result_spot is None  # 63_500 > 63_000, no breach

    def test_both_venues_breach_at_deep_price(self, tmp_path):
        """Both spot and perp breach when price drops below both stops."""
        detector = _make_detector(tmp_path)
        perp_stop = _make_stop_level(
            position_id="BTC:s56:100:primary", token="BTC",
            direction=1, stop_price=64_000.0, stop_active=True, is_perp=True,
        )
        spot_stop = _make_stop_level(
            position_id="BTC:s86:100:primary", token="BTC",
            direction=1, stop_price=63_000.0, stop_active=True, is_perp=False,
        )
        detector.update_stops([perp_stop, spot_stop])

        # Both breach at 62_000 — below both stops
        result_perp, _ = detector.check_price("BTC", 62_000.0, int(time.time() * 1000), is_perp=True)
        assert result_perp is not None

        result_spot, _ = detector.check_price("BTC", 62_000.0, int(time.time() * 1000), is_perp=False)
        assert result_spot is not None


# ===================================================================
# Test: Venue filtering backward compatibility
# ===================================================================

class TestVenueFilteringBackwardCompat:
    """is_perp=None (default) matches all stops — backward compatible."""

    def test_none_matches_all_stops(self, tmp_path):
        """is_perp=None checks all stops regardless of venue."""
        detector = _make_detector(tmp_path)
        perp_stop = _make_stop_level(
            position_id="BTC:s56:100:primary", token="BTC",
            direction=1, stop_price=64_000.0, stop_active=True, is_perp=True,
        )
        detector.update_stops([perp_stop])

        # Default is_perp=None should still detect breach
        result, _ = detector.check_price("BTC", 63_500.0, int(time.time() * 1000))
        assert result is not None

    def test_none_matches_spot_stops(self, tmp_path):
        """is_perp=None also matches spot stops."""
        detector = _make_detector(tmp_path)
        spot_stop = _make_stop_level(
            position_id="BTC:s86:100:primary", token="BTC",
            direction=1, stop_price=64_000.0, stop_active=True, is_perp=False,
        )
        detector.update_stops([spot_stop])

        result, _ = detector.check_price("BTC", 63_500.0, int(time.time() * 1000))
        assert result is not None

    def test_existing_tests_unaffected(self, tmp_path):
        """Existing check_price calls (no is_perp) still work correctly."""
        detector = _make_detector(tmp_path)
        stop = _make_stop_level(direction=1, stop_price=64_000.0, stop_active=True)
        detector.update_stops([stop])

        # This is exactly how existing tests call check_price — no is_perp param
        result, _ = detector.check_price("BTC", 63_500.0, int(time.time() * 1000))
        assert result is not None
        assert result["event_type"] == "breach"
