"""M4 — MTF backtest: arbitrary resolution + triple-subscription callbacks.

Covers:
  - AC12 T-B5: Backtest runs at base_resolution=BarSpec.from_minutes(N) for
    N in canonical set. Bar count = ceil((end_ts - start_ts) / N) ±1.
  - AC13 T-B6: Triple-subscription strategy with (signal=1h, entry=1h, exit=1m)
    over 10 hourly windows — exactly 10 on_signal, 10 Stage-3, 600 Stage-1
    invocations; <= 10 on_scale.

All tests MUST FAIL today — v5.simulator MTF API doesn't exist yet.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


CANONICAL_MINUTES = (1, 3, 5, 10, 15, 30, 60, 120, 240, 360, 480, 720, 1440)


class TestAC12ArbitraryResolutionBacktest:
    """AC12 T-B5: backtest runs at any canonical base resolution."""

    @pytest.mark.parametrize("n", CANONICAL_MINUTES)
    def test_backtest_runs_for_canonical_resolution(self, n):
        """AC12 T-B5: simulator completes with base_resolution=N for every
        canonical N AND emits the expected bar count within ±1."""
        import math

        from v5.bar_spec import BarSpec
        from v5.simulator import run_backtest_mtf

        spec = BarSpec.from_minutes(n)
        # 24-hour window in nanoseconds.
        start_ts = 1_770_000_000 * 1_000_000_000
        end_ts = start_ts + 24 * 3600 * 1_000_000_000
        result = run_backtest_mtf(
            base_resolution=spec,
            start_ts_ns=start_ts,
            end_ts_ns=end_ts,
            strategies=[],
            tokens=["BTC"],
        )
        assert result is not None
        expected_bar_count = math.ceil(
            (end_ts - start_ts) / (n * 60 * 1_000_000_000)
        )
        assert abs(len(result.unified_ts) - expected_bar_count) <= 1, (
            f"AC12 bar_count mismatch for N={n}: "
            f"got {len(result.unified_ts)}, expected {expected_bar_count} ±1"
        )

    def test_bar_count_matches_expected_10m(self):
        """T-B5: bar_count == ceil((end-start)/N) ±1 for 10m base over 24h."""
        from v5.bar_spec import BarSpec
        from v5.simulator import run_backtest_mtf
        import math

        spec = BarSpec.from_minutes(10)
        start_ts = 1_770_000_000 * 1_000_000_000
        end_ts = start_ts + 24 * 3600 * 1_000_000_000
        result = run_backtest_mtf(
            base_resolution=spec,
            start_ts_ns=start_ts, end_ts_ns=end_ts,
            strategies=[], tokens=["BTC"],
        )
        expected = math.ceil((end_ts - start_ts) / (10 * 60 * 1_000_000_000))
        assert abs(result.bar_count - expected) <= 1


class TestAC13TripleSubscriptionCallbacks:
    """AC13 T-B6: (1h, 1h, 1m) over 10 hourly windows — precise invocation counts."""

    def test_on_signal_fires_exactly_ten_times(self):
        """T-B6: 10 hourly windows => exactly 10 on_signal."""
        from v5.bar_spec import BarSpec
        from v5.simulator import run_backtest_mtf
        from v5.strategy_spec import StrategySpec

        counts = {"on_signal": 0, "stage_3": 0, "stage_1": 0, "on_scale": 0}

        class TestStrategy:
            strategy_id = "test_tb6"
            bar_subscriptions = {
                "signal": BarSpec.from_minutes(60),
                "entry": BarSpec.from_minutes(60),
                "exit": BarSpec.from_minutes(1),
            }

            def on_signal(self, bar_ctx):
                counts["on_signal"] += 1

            def on_stage_3(self, bar_ctx):
                counts["stage_3"] += 1

            def on_stage_1(self, bar_ctx):
                counts["stage_1"] += 1

            def on_scale(self, bar_ctx):
                counts["on_scale"] += 1

        start_ts = 1_770_000_000 * 1_000_000_000
        end_ts = start_ts + 10 * 3600 * 1_000_000_000
        run_backtest_mtf(
            base_resolution=BarSpec.from_minutes(1),
            start_ts_ns=start_ts, end_ts_ns=end_ts,
            strategies=[TestStrategy()], tokens=["BTC"],
        )
        assert counts["on_signal"] == 10, (
            f"Expected 10 on_signal; got {counts['on_signal']}"
        )

    def test_stage_3_fires_exactly_ten_times(self):
        """T-B6: Stage 3 dispatched on entry resolution (1h) == 10 times."""
        from v5.bar_spec import BarSpec
        from v5.simulator import run_backtest_mtf

        counts = {"stage_3": 0}

        class TestStrategy:
            strategy_id = "t_s3"
            bar_subscriptions = {
                "signal": BarSpec.from_minutes(60),
                "entry": BarSpec.from_minutes(60),
                "exit": BarSpec.from_minutes(1),
            }
            def on_stage_3(self, bar_ctx):
                counts["stage_3"] += 1

        start_ts = 1_770_000_000 * 1_000_000_000
        end_ts = start_ts + 10 * 3600 * 1_000_000_000
        run_backtest_mtf(
            base_resolution=BarSpec.from_minutes(1),
            start_ts_ns=start_ts, end_ts_ns=end_ts,
            strategies=[TestStrategy()], tokens=["BTC"],
        )
        assert counts["stage_3"] == 10

    def test_stage_1_fires_exactly_600_times(self):
        """T-B6: Stage 1 dispatched on exit resolution (1m) over 10h = 600 times."""
        from v5.bar_spec import BarSpec
        from v5.simulator import run_backtest_mtf

        counts = {"stage_1": 0}

        class TestStrategy:
            strategy_id = "t_s1"
            bar_subscriptions = {
                "signal": BarSpec.from_minutes(60),
                "entry": BarSpec.from_minutes(60),
                "exit": BarSpec.from_minutes(1),
            }
            def on_stage_1(self, bar_ctx):
                counts["stage_1"] += 1

        start_ts = 1_770_000_000 * 1_000_000_000
        end_ts = start_ts + 10 * 3600 * 1_000_000_000
        run_backtest_mtf(
            base_resolution=BarSpec.from_minutes(1),
            start_ts_ns=start_ts, end_ts_ns=end_ts,
            strategies=[TestStrategy()], tokens=["BTC"],
        )
        assert counts["stage_1"] == 600

    def test_on_scale_cap_is_at_most_ten(self):
        """AC9 + T-B6: per-hourly-bar scale invocation cap — <= 10 calls in 10h."""
        from v5.bar_spec import BarSpec
        from v5.simulator import run_backtest_mtf

        counts = {"on_scale": 0}

        class TestStrategy:
            strategy_id = "t_cap"
            bar_subscriptions = {
                "signal": BarSpec.from_minutes(60),
                "entry": BarSpec.from_minutes(60),
                "exit": BarSpec.from_minutes(1),
            }
            def on_scale(self, bar_ctx):
                counts["on_scale"] += 1

        start_ts = 1_770_000_000 * 1_000_000_000
        end_ts = start_ts + 10 * 3600 * 1_000_000_000
        run_backtest_mtf(
            base_resolution=BarSpec.from_minutes(1),
            start_ts_ns=start_ts, end_ts_ns=end_ts,
            strategies=[TestStrategy()], tokens=["BTC"],
        )
        assert counts["on_scale"] <= 10, (
            f"Scale cap exceeded: {counts['on_scale']} > 10"
        )
