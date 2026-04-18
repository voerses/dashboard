"""M4 — Sub-hourly trail stop best-practice + ATR source combinations.

Covers:
  - AC40 T-B32: trail_schedule=[(1.0, 0.0), (2.0, 1.0)] at exit_resolution=1m
    tightens stop at 1m cadence as profit crosses milestones (not only hourly).
    Same math as hourly; different event cadence.
  - AC40 T-B32b: chandelier ATR source — four combinations:
      (declared_fine_atr=yes, chandelier_atr_source='fine') -> Stop_A
      (yes, 'hourly')                                        -> Stop_B != Stop_A
      (no, 'fine')                                           -> ValueError at StrategySpec
      (no, 'hourly')                                         -> uses hourly-frozen ATR, no error
    Indicator key is canonically bar_ctx.indicator_snapshot["atr_hourly"].

All tests MUST FAIL today — v5.trailing_stop_handler M4 API doesn't exist.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


class TestAC40TrailScheduleSubHourly:
    """T-B32: trail_schedule tightens at 1m cadence."""

    def test_stop_tightens_on_1m_bar_crossing_milestone(self):
        """T-B32: as 1m close crosses 1R profit level, stop moves to breakeven
        (+0R); as 2m close crosses 2R, stop moves to +1R — at 1m cadence."""
        from v5.bar_processor import BarProcessor
        from v5.bar_spec import BarSpec
        from v5.position import Position
        from v5.exit_handlers import TrailingStopHandler

        # Long position, entry 100, risk 1.0 (stop at 99).
        pos = Position(token="BTC", direction=1, entry_price=100.0, quantity=1.0)
        pos.stop_price = 99.0
        pos.initial_risk = 1.0

        handler = TrailingStopHandler(
            trail_schedule=[(1.0, 0.0), (2.0, 1.0)],
        )

        # Before any milestone (close=100.5, below 1R): stop should NOT move.
        bar_ctx_1 = type("BC", (), {
            "ts_ns": 0, "close": 100.5, "high": 100.6, "low": 100.3,
            "bar_spec": BarSpec.from_minutes(1),
            "indicator_snapshot": {"atr_hourly": 0.5},
        })()
        handler.update_state(pos, bar_ctx_1)
        assert pos.stop_price == 99.0

        # Close crosses 1R (close=101.0) on a 1m bar => stop to breakeven.
        bar_ctx_2 = type("BC", (), {
            "ts_ns": 60_000_000_000, "close": 101.0, "high": 101.1, "low": 100.8,
            "bar_spec": BarSpec.from_minutes(1),
            "indicator_snapshot": {"atr_hourly": 0.5},
        })()
        handler.update_state(pos, bar_ctx_2)
        assert pos.stop_price == 100.0, (
            f"Expected breakeven 100.0; got {pos.stop_price}"
        )

        # Close crosses 2R (close=102.0) on next 1m bar => stop to +1R (101.0).
        bar_ctx_3 = type("BC", (), {
            "ts_ns": 120_000_000_000, "close": 102.0, "high": 102.1, "low": 101.9,
            "bar_spec": BarSpec.from_minutes(1),
            "indicator_snapshot": {"atr_hourly": 0.5},
        })()
        handler.update_state(pos, bar_ctx_3)
        assert pos.stop_price == 101.0

    def test_trail_monotonicity_preserved(self):
        """AC40 T-B32: stop tightens exactly to the expected level on each bar
        (no permissive fallback; monotonicity is enforced by the schedule,
        not by `or pos.stop_price >= expected`)."""
        from v5.bar_spec import BarSpec
        from v5.position import Position
        from v5.exit_handlers import TrailingStopHandler

        F32_ATOL = 1e-6

        pos = Position(token="BTC", direction=1, entry_price=100.0, quantity=1.0)
        pos.stop_price = 99.0
        pos.initial_risk = 1.0

        handler = TrailingStopHandler(trail_schedule=[(1.0, 0.0), (2.0, 1.0)])

        bars = [
            (102.0, 101.0),  # 2R hit, stop should be 101.0
            (100.5, 101.0),  # pullback below 1R — stop MUST NOT loosen
            (99.5, 101.0),   # further pullback — stop still 101.0 until hit
        ]
        for close, expected_stop in bars:
            bc = type("BC", (), {
                "ts_ns": 0, "close": close, "high": close + 0.1, "low": close - 0.1,
                "bar_spec": BarSpec.from_minutes(1),
                "indicator_snapshot": {"atr_hourly": 0.5},
            })()
            handler.update_state(pos, bc)
            assert pos.stop_price == pytest.approx(expected_stop, abs=F32_ATOL), (
                f"Expected stop exactly {expected_stop}; got {pos.stop_price}"
            )


class TestAC40ChandelierATRSource:
    """T-B32b: chandelier ATR source — four combinations."""

    def test_fine_atr_declared_and_source_fine_uses_fine(self):
        """Combination 1: (declared_fine_atr=yes, chandelier_atr_source='fine')
        -> uses fine-bar ATR."""
        from v5.bar_spec import BarSpec
        from v5.strategy_spec import StrategySpec
        from v5.exit_handlers import ChandelierStopHandler
        from v5.position import Position

        spec = StrategySpec(
            strategy_id="s_fine",
            bar_subscriptions={
                "signal": BarSpec.from_minutes(60),
                "entry": BarSpec.from_minutes(60),
                "exit": BarSpec.from_minutes(1),
            },
            chandelier_atr_source="fine",
            declared_fine_atr=True,
            chandelier_lookback=20,
        )
        handler = ChandelierStopHandler(spec=spec)
        pos = Position(token="BTC", direction=1, entry_price=100.0, quantity=1.0)
        pos.stop_price = 99.0
        bc_a = type("BC", (), {
            "close": 101.0, "high": 101.2, "low": 100.8,
            "bar_spec": BarSpec.from_minutes(1),
            "indicator_snapshot": {"atr_fine": 0.3, "atr_hourly": 0.5},
            "rolling_max": 101.5,
        })()
        stop_a = handler.compute_stop(pos, bc_a)
        # Fine ATR = 0.3 yields a different stop than hourly ATR = 0.5.
        expected_fine = 101.5 - 3.0 * 0.3  # chandelier = max - k * atr
        assert abs(stop_a - expected_fine) < 1e-6

    def test_fine_atr_declared_and_source_hourly_uses_hourly_frozen(self):
        """Combination 2: (yes, 'hourly') -> uses hourly-frozen ATR, different Stop_B."""
        from v5.bar_spec import BarSpec
        from v5.strategy_spec import StrategySpec
        from v5.exit_handlers import ChandelierStopHandler
        from v5.position import Position

        spec = StrategySpec(
            strategy_id="s_hourly",
            bar_subscriptions={
                "signal": BarSpec.from_minutes(60),
                "entry": BarSpec.from_minutes(60),
                "exit": BarSpec.from_minutes(1),
            },
            chandelier_atr_source="hourly",
            declared_fine_atr=True,
            chandelier_lookback=20,
        )
        handler = ChandelierStopHandler(spec=spec)
        pos = Position(token="BTC", direction=1, entry_price=100.0, quantity=1.0)
        pos.stop_price = 99.0
        bc_b = type("BC", (), {
            "close": 101.0, "high": 101.2, "low": 100.8,
            "bar_spec": BarSpec.from_minutes(1),
            "indicator_snapshot": {"atr_fine": 0.3, "atr_hourly": 0.5},
            "rolling_max": 101.5,
        })()
        stop_b = handler.compute_stop(pos, bc_b)
        expected_hourly = 101.5 - 3.0 * 0.5
        assert abs(stop_b - expected_hourly) < 1e-6
        # Stop_A (fine) != Stop_B (hourly).
        expected_fine = 101.5 - 3.0 * 0.3
        assert abs(expected_fine - expected_hourly) > 1e-6

    def test_no_fine_atr_and_source_fine_raises_at_spec_construction(self):
        """Combination 3: (no, 'fine') -> ValueError at StrategySpec construction."""
        from v5.bar_spec import BarSpec
        from v5.strategy_spec import StrategySpec

        with pytest.raises(ValueError) as exc_info:
            StrategySpec(
                strategy_id="s_bad",
                bar_subscriptions={
                    "signal": BarSpec.from_minutes(60),
                    "entry": BarSpec.from_minutes(60),
                    "exit": BarSpec.from_minutes(1),
                },
                chandelier_atr_source="fine",
                declared_fine_atr=False,  # not declared — incompatible with 'fine'
                chandelier_lookback=20,
            )
        msg = str(exc_info.value).lower()
        assert "fine" in msg or "atr" in msg or "declared" in msg

    def test_no_fine_atr_and_source_hourly_uses_hourly_no_error(self):
        """Combination 4: (no, 'hourly') -> no error; uses hourly-frozen ATR."""
        from v5.bar_spec import BarSpec
        from v5.strategy_spec import StrategySpec
        from v5.exit_handlers import ChandelierStopHandler
        from v5.position import Position

        spec = StrategySpec(
            strategy_id="s_h_only",
            bar_subscriptions={
                "signal": BarSpec.from_minutes(60),
                "entry": BarSpec.from_minutes(60),
                "exit": BarSpec.from_minutes(1),
            },
            chandelier_atr_source="hourly",
            declared_fine_atr=False,
            chandelier_lookback=20,
        )
        handler = ChandelierStopHandler(spec=spec)
        pos = Position(token="BTC", direction=1, entry_price=100.0, quantity=1.0)
        pos.stop_price = 99.0
        bc = type("BC", (), {
            "close": 101.0, "high": 101.2, "low": 100.8,
            "bar_spec": BarSpec.from_minutes(1),
            "indicator_snapshot": {"atr_hourly": 0.5},
            "rolling_max": 101.5,
        })()
        stop = handler.compute_stop(pos, bc)
        assert abs(stop - (101.5 - 3.0 * 0.5)) < 1e-6
