"""Acceptance tests for Task 8: Trail tightening + target + liquidation (AC12, AC12b, AC13).

Tests verify:
  - Trail tightening formula: sentinel_stop = max(stop_price, highest - trail_mult * cur_atr)
  - Short direction: min formula
  - sentinel_highest tracks max of incoming prices
  - sentinel_highest resets on stops.json refresh
  - Guard: no trail tightening when convex_exit=True
  - Guard: no trail tightening when has_trail_schedule=True
  - Guard: no trail tightening when chandelier_lookback > 0
  - Target breach: mark_price >= target_price for longs
  - Target breach: no confirmation delay
  - Liquidation warning at 90%
  - Liquidation emergency exit at 100% (no confirmation)
  - Liquidation fires regardless of stop_active

All tests use synthetic data -- no real market data required.
These tests MUST FAIL until trail tightening is implemented (RED phase).
"""
from __future__ import annotations

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


def _make_detector(tmp_path, mode="shadow") -> BreachDetector:
    """Create a BreachDetector with a temporary state_dir."""
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
# Test: Trail tightening formula (long)
# ===================================================================

class TestTrailTighteningLong:
    """sentinel_stop = max(stop_price, highest - trail_mult * cur_atr) for longs."""

    def test_trail_tightens_on_new_high(self, tmp_path):
        """New high tightens sentinel_stop above the original stop_price."""
        detector = _make_detector(tmp_path)
        stop = _make_stop_level(
            direction=1, stop_price=64_000.0, trail_mult=3.0,
            cur_atr=1200.0, highest=70_000.0,
            convex_exit=False, has_trail_schedule=False, chandelier_lookback=0,
        )
        detector.update_stops([stop])

        # Price rallies to 72000 (new high)
        detector.check_price("BTC", 72_000.0, int(time.time() * 1000))

        # sentinel_stop = max(64000, 72000 - 3.0 * 1200) = max(64000, 68400) = 68400
        sentinel_stop = detector.get_sentinel_stop("BTC:s56:100:primary")
        assert sentinel_stop == pytest.approx(68_400.0)

    def test_trail_does_not_loosen(self, tmp_path):
        """Trail only tightens (monotonic max), never loosens."""
        detector = _make_detector(tmp_path)
        stop = _make_stop_level(
            direction=1, stop_price=64_000.0, trail_mult=3.0,
            cur_atr=1200.0, highest=70_000.0,
            convex_exit=False, has_trail_schedule=False, chandelier_lookback=0,
        )
        detector.update_stops([stop])

        # Rally to 72000 (tightens to 68400)
        detector.check_price("BTC", 72_000.0, int(time.time() * 1000))
        # Drop to 69000 (should NOT loosen)
        detector.check_price("BTC", 69_000.0, int(time.time() * 1000))

        sentinel_stop = detector.get_sentinel_stop("BTC:s56:100:primary")
        assert sentinel_stop == pytest.approx(68_400.0)

    def test_no_tightening_when_below_stop(self, tmp_path):
        """If the computed trail is below stop_price, stop_price holds."""
        detector = _make_detector(tmp_path)
        stop = _make_stop_level(
            direction=1, stop_price=64_000.0, trail_mult=3.0,
            cur_atr=1200.0, highest=66_000.0,
            convex_exit=False, has_trail_schedule=False, chandelier_lookback=0,
        )
        detector.update_stops([stop])

        # Price at 66500 -> highest=66500, trail=66500-3600=62900 < 64000
        detector.check_price("BTC", 66_500.0, int(time.time() * 1000))
        sentinel_stop = detector.get_sentinel_stop("BTC:s56:100:primary")
        assert sentinel_stop == pytest.approx(64_000.0)


# ===================================================================
# Test: Short direction min formula
# ===================================================================

class TestTrailTighteningShort:
    """sentinel_stop = min(stop_price, lowest + trail_mult * cur_atr) for shorts."""

    def test_short_trail_tightens_on_new_low(self, tmp_path):
        """New low tightens sentinel_stop below the original stop_price for shorts."""
        detector = _make_detector(tmp_path)
        stop = _make_stop_level(
            direction=-1, stop_price=68_000.0, trail_mult=3.0,
            cur_atr=1200.0, lowest=62_000.0,
            convex_exit=False, has_trail_schedule=False, chandelier_lookback=0,
        )
        detector.update_stops([stop])

        # Price drops to 60000 (new low)
        detector.check_price("BTC", 60_000.0, int(time.time() * 1000))

        # sentinel_stop = min(68000, 60000 + 3.0 * 1200) = min(68000, 63600) = 63600
        sentinel_stop = detector.get_sentinel_stop("BTC:s56:100:primary")
        assert sentinel_stop == pytest.approx(63_600.0)

    def test_short_trail_does_not_loosen(self, tmp_path):
        """Short trail only tightens (monotonic min), never loosens."""
        detector = _make_detector(tmp_path)
        stop = _make_stop_level(
            direction=-1, stop_price=68_000.0, trail_mult=3.0,
            cur_atr=1200.0, lowest=62_000.0,
            convex_exit=False, has_trail_schedule=False, chandelier_lookback=0,
        )
        detector.update_stops([stop])

        # Drop to 60000 (tightens to 63600)
        detector.check_price("BTC", 60_000.0, int(time.time() * 1000))
        # Bounce to 64000 (should NOT loosen)
        detector.check_price("BTC", 64_000.0, int(time.time() * 1000))

        sentinel_stop = detector.get_sentinel_stop("BTC:s56:100:primary")
        assert sentinel_stop == pytest.approx(63_600.0)


# ===================================================================
# Test: sentinel_highest tracks max of incoming prices
# ===================================================================

class TestSentinelHighest:
    """sentinel_highest tracks the max price from the 1s stream."""

    def test_highest_updates_on_new_high(self, tmp_path):
        """sentinel_highest increases when a higher price arrives."""
        detector = _make_detector(tmp_path)
        stop = _make_stop_level(highest=70_000.0,
                                convex_exit=False, has_trail_schedule=False,
                                chandelier_lookback=0)
        detector.update_stops([stop])

        detector.check_price("BTC", 71_000.0, int(time.time() * 1000))
        detector.check_price("BTC", 72_000.0, int(time.time() * 1000))

        highest = detector.get_sentinel_highest("BTC:s56:100:primary")
        assert highest == pytest.approx(72_000.0)

    def test_highest_does_not_decrease(self, tmp_path):
        """sentinel_highest does not decrease on lower prices."""
        detector = _make_detector(tmp_path)
        stop = _make_stop_level(highest=70_000.0,
                                convex_exit=False, has_trail_schedule=False,
                                chandelier_lookback=0)
        detector.update_stops([stop])

        detector.check_price("BTC", 72_000.0, int(time.time() * 1000))
        detector.check_price("BTC", 69_000.0, int(time.time() * 1000))

        highest = detector.get_sentinel_highest("BTC:s56:100:primary")
        assert highest == pytest.approx(72_000.0)


# ===================================================================
# Test: sentinel_highest resets on stops.json refresh
# ===================================================================

class TestSentinelHighestReset:
    """sentinel_highest preserves intra-hour trail state across refreshes (F8 fix)."""

    def test_highest_preserves_intra_hour_high(self, tmp_path):
        """update_stops preserves sentinel-tracked high when it exceeds stops.json value.

        F8 fix: The sentinel may detect intra-bar highs that the hourly engine
        (calculating from 1H bars) hasn't seen.  Resetting to the hourly value
        would lose this real-time trail state.
        """
        detector = _make_detector(tmp_path)
        stop = _make_stop_level(highest=70_000.0,
                                convex_exit=False, has_trail_schedule=False,
                                chandelier_lookback=0)
        detector.update_stops([stop])

        # Price rallies sentinel_highest to 75000
        detector.check_price("BTC", 75_000.0, int(time.time() * 1000))
        assert detector.get_sentinel_highest("BTC:s56:100:primary") == pytest.approx(75_000.0)

        # Stops.json refresh with authoritative highest=71000 (lower than sentinel's 75000)
        new_stop = _make_stop_level(highest=71_000.0,
                                     convex_exit=False, has_trail_schedule=False,
                                     chandelier_lookback=0)
        detector.update_stops([new_stop])

        # F8: sentinel preserves the higher value (75000 > 71000)
        highest = detector.get_sentinel_highest("BTC:s56:100:primary")
        assert highest == pytest.approx(75_000.0)

    def test_highest_adopts_higher_stops_value(self, tmp_path):
        """update_stops adopts stops.json value when it exceeds sentinel-tracked high."""
        detector = _make_detector(tmp_path)
        stop = _make_stop_level(highest=70_000.0,
                                convex_exit=False, has_trail_schedule=False,
                                chandelier_lookback=0)
        detector.update_stops([stop])

        # Price rallies sentinel_highest to 72000
        detector.check_price("BTC", 72_000.0, int(time.time() * 1000))
        assert detector.get_sentinel_highest("BTC:s56:100:primary") == pytest.approx(72_000.0)

        # Stops.json refresh with authoritative highest=76000 (higher than sentinel's 72000)
        new_stop = _make_stop_level(highest=76_000.0,
                                     convex_exit=False, has_trail_schedule=False,
                                     chandelier_lookback=0)
        detector.update_stops([new_stop])

        # F8: stops.json value wins because it's higher
        highest = detector.get_sentinel_highest("BTC:s56:100:primary")
        assert highest == pytest.approx(76_000.0)


# ===================================================================
# Test: Guard -- no trail tightening when convex_exit=True
# ===================================================================

class TestConvexExitGuard:
    """No trail tightening for convex_exit positions."""

    def test_no_tightening_when_convex(self, tmp_path):
        """convex_exit=True disables intra-tick trail tightening."""
        detector = _make_detector(tmp_path)
        stop = _make_stop_level(
            direction=1, stop_price=64_000.0, trail_mult=3.0,
            cur_atr=1200.0, highest=70_000.0,
            convex_exit=True,
        )
        detector.update_stops([stop])

        # Rally to 75000 -- should NOT tighten trail
        detector.check_price("BTC", 75_000.0, int(time.time() * 1000))
        sentinel_stop = detector.get_sentinel_stop("BTC:s56:100:primary")
        assert sentinel_stop == pytest.approx(64_000.0)  # Unchanged


# ===================================================================
# Test: Guard -- no trail tightening when has_trail_schedule=True
# ===================================================================

class TestTrailScheduleGuard:
    """No trail tightening for positions with trail schedules."""

    def test_no_tightening_when_trail_schedule(self, tmp_path):
        """has_trail_schedule=True disables intra-tick trail tightening."""
        detector = _make_detector(tmp_path)
        stop = _make_stop_level(
            direction=1, stop_price=64_000.0, trail_mult=3.0,
            cur_atr=1200.0, highest=70_000.0,
            has_trail_schedule=True,
        )
        detector.update_stops([stop])

        detector.check_price("BTC", 75_000.0, int(time.time() * 1000))
        sentinel_stop = detector.get_sentinel_stop("BTC:s56:100:primary")
        assert sentinel_stop == pytest.approx(64_000.0)  # Unchanged


# ===================================================================
# Test: Guard -- no trail tightening when chandelier_lookback > 0
# ===================================================================

class TestChandelierGuard:
    """No trail tightening for positions with chandelier_lookback > 0."""

    def test_no_tightening_when_chandelier(self, tmp_path):
        """chandelier_lookback > 0 disables intra-tick trail tightening."""
        detector = _make_detector(tmp_path)
        stop = _make_stop_level(
            direction=1, stop_price=64_000.0, trail_mult=3.0,
            cur_atr=1200.0, highest=70_000.0,
            chandelier_lookback=14,
        )
        detector.update_stops([stop])

        detector.check_price("BTC", 75_000.0, int(time.time() * 1000))
        sentinel_stop = detector.get_sentinel_stop("BTC:s56:100:primary")
        assert sentinel_stop == pytest.approx(64_000.0)  # Unchanged


# ===================================================================
# Test: Target breach -- longs
# ===================================================================

class TestTargetBreachLong:
    """Target breach: mark_price >= target_price for longs."""

    def test_long_target_breach(self, tmp_path):
        """Price reaching target triggers target breach for long."""
        detector = _make_detector(tmp_path)
        stop = _make_stop_level(
            direction=1, target_price=80_000.0, stop_active=True,
        )
        detector.update_stops([stop])

        result, _ = detector.check_price("BTC", 80_500.0, int(time.time() * 1000))
        assert result is not None
        assert "target" in result.get("exit_reason", "").lower() or \
               result.get("event_type") in ("target_breach", "breach")

    def test_short_target_breach(self, tmp_path):
        """Price reaching target triggers target breach for short."""
        detector = _make_detector(tmp_path)
        stop = _make_stop_level(
            direction=-1, target_price=55_000.0, stop_active=True,
            stop_price=68_000.0,
        )
        detector.update_stops([stop])

        result, _ = detector.check_price("BTC", 54_500.0, int(time.time() * 1000))
        assert result is not None
        assert "target" in result.get("exit_reason", "").lower() or \
               result.get("event_type") in ("target_breach", "breach")


# ===================================================================
# Test: Target breach -- no confirmation delay
# ===================================================================

class TestTargetNoConfirmation:
    """Target breaches execute immediately without confirmation delay."""

    def test_target_breach_immediate(self, tmp_path):
        """Target breach does not require confirmation timer."""
        detector = _make_detector(tmp_path)
        stop = _make_stop_level(
            direction=1, target_price=80_000.0, stop_active=True,
        )
        detector.update_stops([stop])

        result, _ = detector.check_price("BTC", 81_000.0, int(time.time() * 1000))
        # Target should trigger immediately, not be pending
        assert result is not None
        # Should NOT appear in active confirmations (no delay)
        pending = [c for c in detector.get_active_confirmations()
                   if "target" in c.get("exit_reason", "").lower()]
        assert len(pending) == 0


# ===================================================================
# Test: Liquidation warning at 90%
# ===================================================================

class TestLiquidationWarning:
    """Liquidation warning fires at 90% of distance to estimated_liq_price."""

    def test_liq_warning_at_90_pct(self, tmp_path):
        """Warning event when price enters 90% zone toward liq price."""
        detector = _make_detector(tmp_path)
        # Long: entry=66000, liq=50000, distance=16000, 90% zone = 50000 + 1600 = 51600
        stop = _make_stop_level(
            direction=1, entry_price=66_000.0, estimated_liq_price=50_000.0,
            stop_active=True,
        )
        detector.update_stops([stop])

        # Price at 51500 is within 10% of liq price
        result, _ = detector.check_price("BTC", 51_500.0, int(time.time() * 1000))
        assert result is not None
        assert "liq_warning" in result.get("event_type", "") or \
               "liq" in result.get("exit_reason", "").lower()


# ===================================================================
# Test: Liquidation emergency exit at 100% (no confirmation)
# ===================================================================

class TestLiquidationEmergencyExit:
    """Liquidation at 100% triggers immediate exit with no confirmation."""

    def test_liq_emergency_exit(self, tmp_path):
        """Price at estimated_liq_price triggers immediate exit."""
        detector = _make_detector(tmp_path)
        stop = _make_stop_level(
            direction=1, estimated_liq_price=50_000.0, stop_active=True,
        )
        detector.update_stops([stop])

        result, _ = detector.check_price("BTC", 49_500.0, int(time.time() * 1000))
        assert result is not None
        assert "liq" in result.get("exit_reason", "").lower()

    def test_liq_exit_no_confirmation_delay(self, tmp_path):
        """Liquidation exit does not wait for confirmation."""
        detector = _make_detector(tmp_path)
        stop = _make_stop_level(
            direction=1, estimated_liq_price=50_000.0, stop_active=True,
        )
        detector.update_stops([stop])

        detector.check_price("BTC", 49_500.0, int(time.time() * 1000))
        # Should NOT be in pending confirmations
        pending = [c for c in detector.get_active_confirmations()
                   if "liq" in c.get("exit_reason", "").lower()]
        assert len(pending) == 0


# ===================================================================
# Test: Liquidation fires regardless of stop_active
# ===================================================================

class TestLiquidationIgnoresStopActive:
    """Liquidation safety net fires even when stop_active=False."""

    def test_liq_fires_during_grace_period(self, tmp_path):
        """Liquidation triggers even in no_stop_bars grace period."""
        detector = _make_detector(tmp_path)
        stop = _make_stop_level(
            direction=1, estimated_liq_price=50_000.0,
            stop_active=False,  # Grace period
        )
        detector.update_stops([stop])

        result, _ = detector.check_price("BTC", 49_500.0, int(time.time() * 1000))
        assert result is not None
        assert "liq" in result.get("exit_reason", "").lower()
