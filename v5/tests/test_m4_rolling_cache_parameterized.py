"""M4 — RollingCache parameterized by BarSpec + role-aware lookback + demand-driven.

Covers:
  - AC20 T-B12: RollingCache accepts a BarSpec. BarType.ONE_MIN/ONE_HOUR/DAILY
    alias to BarSpec.from_minutes(1/60/1440). Sidecar key format preserved:
    f"{token}__{bar_spec.label}".
  - AC35 T-B27: maxlen_for_bar_spec(spec, role) with defaults
    signal=250d, entry=7d, exit=2d; memory-budget assertion at sim init
    trips when projected RSS exceeds 1.2GB.
  - AC36 T-B28: only resolutions declared in bar_subscriptions materialize;
    chunked iteration activates when projected memory > 2GB.

All tests MUST FAIL today — M4 extensions to rolling_cache do not exist.
"""
from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


class TestAC20RollingCacheParameterized:
    """AC20 T-B12: RollingCache(bar_spec, lookback) with sidecar key preserved."""

    def test_rolling_cache_accepts_bar_spec(self):
        """RollingCache construction takes BarSpec instead of closed enum."""
        from v5.bar_spec import BarSpec
        from v5.rolling_cache import RollingCache

        spec = BarSpec.from_minutes(60)
        cache = RollingCache(
            token="BTC", bar_spec=spec, lookback=timedelta(days=7),
        )
        assert cache.bar_spec == spec

    def test_bar_type_one_min_aliases_to_1m(self):
        """AC20 migration: BarType.ONE_MIN IS the interned canonical
        BarSpec.from_minutes(1) (identity-equal, not merely structurally
        equal — migration alias points to the same interned instance)."""
        from v5.bar_spec import BarSpec
        from v5.rolling_cache import BarType

        assert BarType.ONE_MIN is BarSpec.from_minutes(1)

    def test_bar_type_one_hour_aliases_to_60m(self):
        """AC20: BarType.ONE_HOUR is the interned BarSpec.from_minutes(60)."""
        from v5.bar_spec import BarSpec
        from v5.rolling_cache import BarType
        assert BarType.ONE_HOUR is BarSpec.from_minutes(60)

    def test_bar_type_daily_aliases_to_1440m(self):
        """AC20: BarType.DAILY is the interned BarSpec.from_minutes(1440)."""
        from v5.bar_spec import BarSpec
        from v5.rolling_cache import BarType
        assert BarType.DAILY is BarSpec.from_minutes(1440)

    @pytest.mark.parametrize("minutes,label", [(1, "1m"), (60, "1h"), (1440, "1d")])
    def test_sidecar_key_format_preserved(self, minutes, label):
        """AC20: sidecar key = f'{token}__{bar_spec.label}' — legacy-compat."""
        from v5.bar_spec import BarSpec
        from v5.rolling_cache import RollingCache

        spec = BarSpec.from_minutes(minutes)
        cache = RollingCache(
            token="BTC", bar_spec=spec, lookback=timedelta(hours=2),
        )
        assert cache.sidecar_key == f"BTC__{label}", (
            f"Expected BTC__{label}, got {cache.sidecar_key}"
        )


class TestAC35RoleAwareLookback:
    """AC35 T-B27: maxlen_for_bar_spec(spec, role) defaults + memory budget."""

    def test_maxlen_exit_1m_is_2_days(self):
        """T-B27: maxlen_for_bar_spec(1m, 'exit') == 2880 (2d * 1440 bars/d)."""
        from v5.bar_spec import BarSpec
        from v5.rolling_cache import maxlen_for_bar_spec

        assert maxlen_for_bar_spec(BarSpec.from_minutes(1), "exit") == 2880

    def test_maxlen_entry_1m_is_7_days(self):
        """T-B27: maxlen_for_bar_spec(1m, 'entry') == 10080 (7d * 1440)."""
        from v5.bar_spec import BarSpec
        from v5.rolling_cache import maxlen_for_bar_spec

        assert maxlen_for_bar_spec(BarSpec.from_minutes(1), "entry") == 10080

    def test_maxlen_signal_1m_is_250_days(self):
        """T-B27: maxlen_for_bar_spec(1m, 'signal') == 360000 (250d * 1440)."""
        from v5.bar_spec import BarSpec
        from v5.rolling_cache import maxlen_for_bar_spec

        assert maxlen_for_bar_spec(BarSpec.from_minutes(1), "signal") == 360000

    def test_maxlen_signal_1h_scales_correctly(self):
        """maxlen_for_bar_spec(1h, 'signal') == 250 * 24 = 6000."""
        from v5.bar_spec import BarSpec
        from v5.rolling_cache import maxlen_for_bar_spec

        assert maxlen_for_bar_spec(BarSpec.from_minutes(60), "signal") == 6000

    def test_unknown_role_raises(self):
        """Unknown role argument must raise (Literal-typed surface)."""
        from v5.bar_spec import BarSpec
        from v5.rolling_cache import maxlen_for_bar_spec

        with pytest.raises((ValueError, KeyError, TypeError)):
            maxlen_for_bar_spec(BarSpec.from_minutes(60), "bogus_role")

    def test_memory_budget_assertion_trips_at_threshold(self):
        """AC35: projected memory (strategies pre-loaded with their
        subscriptions) > 1.2GB ceiling. compute_projected_memory_mb returns
        a single float; assertion happens against the 1200 MB ceiling.
        The function infers fields/bytes internally."""
        from v5.bar_processor import compute_projected_memory_mb  # forward reference; M4 target
        from v5.bar_spec import BarSpec
        from v5.strategy_spec import StrategySpec

        # Intentionally over-provisioned config: 1m signal lookback on 500 tokens.
        strategies = [
            StrategySpec(
                strategy_id=f"s{i}",
                bar_subscriptions={
                    "signal": BarSpec.from_minutes(1),
                    "entry": BarSpec.from_minutes(1),
                    "exit": BarSpec.from_minutes(1),
                },
            )
            for i in range(3)
        ]
        tokens = [f"TOK{i}" for i in range(500)]
        projected_mb = compute_projected_memory_mb(
            strategies=strategies, tokens=tokens,
        )
        assert projected_mb > 1200, (
            f"Over-provisioned config must project > 1200 MB; got {projected_mb}"
        )

    def test_memory_budget_passes_within_limit(self):
        """Small configuration projects under the 1200 MB ceiling."""
        from v5.bar_processor import compute_projected_memory_mb  # forward reference; M4 target
        from v5.bar_spec import BarSpec
        from v5.strategy_spec import StrategySpec

        strategies = [
            StrategySpec(
                strategy_id=f"s{i}",
                bar_subscriptions={
                    "signal": BarSpec.from_minutes(60),
                    "entry": BarSpec.from_minutes(60),
                    "exit": BarSpec.from_minutes(60),
                },
            )
            for i in range(3)
        ]
        tokens = [f"TOK{i}" for i in range(50)]
        projected_mb = compute_projected_memory_mb(
            strategies=strategies, tokens=tokens,
        )
        assert projected_mb <= 1200, (
            f"Small config must project <= 1200 MB; got {projected_mb}"
        )


class TestAC36DemandDrivenMaterialization:
    """AC36 T-B28: only subscribed resolutions materialize; chunked > 2GB."""

    def test_only_subscribed_resolutions_materialized(self):
        """AC36: unsubscribed resolutions are not materialized."""
        from v5.bar_spec import BarSpec
        from v5.data_resampler import DataResampler

        resampler = DataResampler()
        # Declare subscriptions for 1h + 5m only.
        resampler.declare_subscriptions({
            BarSpec.from_minutes(60), BarSpec.from_minutes(5),
        })
        materialized = resampler.materialized_specs()
        assert BarSpec.from_minutes(60) in materialized
        assert BarSpec.from_minutes(5) in materialized
        # Non-subscribed resolutions must NOT be materialized.
        assert BarSpec.from_minutes(1440) not in materialized
        assert BarSpec.from_minutes(15) not in materialized

    def test_chunked_iteration_activates_above_2gb(self):
        """AC36: projected memory > 2GB triggers chunked iteration (30-day windows)."""
        from v5.bar_spec import BarSpec
        from v5.data_resampler import DataResampler

        resampler = DataResampler()
        # Construct subscription at 1m + many tokens = projects > 2GB.
        resampler.declare_subscriptions(
            {BarSpec.from_minutes(1)},
            tokens=[f"TOK{i}" for i in range(500)],
            duration_days=365,
        )
        assert resampler.iteration_mode() == "chunked"
        assert resampler.chunk_window_days() == 30

    def test_eager_iteration_under_2gb(self):
        """Sub-threshold projection uses eager materialization, no chunking."""
        from v5.bar_spec import BarSpec
        from v5.data_resampler import DataResampler

        resampler = DataResampler()
        resampler.declare_subscriptions(
            {BarSpec.from_minutes(60)}, tokens=["BTC", "ETH"], duration_days=30,
        )
        assert resampler.iteration_mode() == "eager"

    def test_chunk_boundaries_aligned_utc_00_00(self):
        """AC36: chunk boundaries aligned to UTC 00:00 on day-N — deterministic."""
        from v5.bar_spec import BarSpec
        from v5.data_resampler import DataResampler

        resampler = DataResampler()
        resampler.declare_subscriptions(
            {BarSpec.from_minutes(1)},
            tokens=[f"TOK{i}" for i in range(500)],
            duration_days=365,
        )
        boundaries = resampler.chunk_boundaries_ns()
        # Each boundary must be aligned to UTC 00:00 (ts % 86400s == 0).
        for b in boundaries:
            assert b % (86400 * 1_000_000_000) == 0, (
                f"Boundary {b} not aligned to UTC 00:00"
            )
