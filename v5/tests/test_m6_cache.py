"""M6 / M11 — Cache 3-tuple key + memory budget (T-D1, T-D1a, T-D1b).

M11 migration (Commit 2): ``MultiInstrumentCache`` is deleted; the
unified polymorphic ``MarketDataCache`` provides the same 3-tuple
``(instrument, bar_spec, role)`` routing for ``BarData`` streams.
This file is migrated to exercise the M11 surface — tests assert the
SAME behavioral invariants M6 relied on (role-aware lookback,
monotonicity, memory projection) against the post-M11 cache class.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


def _mk_bar(ts, close, spec, inst):
    """Construct a post-M11 BarData instance for cache ingress.

    Pre-M11 this returned a duck-typed object with ``instrument_id``;
    the M11 cache normalizes both shapes via ``on_bar``, but we write
    to the ``on_data(BarData)`` strict surface here to exercise the
    type-dispatched path directly.
    """
    from v5.data.streams import BarData
    return BarData(
        instrument=inst, bar_spec=spec,
        ts_event=int(ts), ts_init=int(ts) + 1,
        open=close, high=close, low=close, close=close,
        volume=1000.0,
    )


def _inst(symbol="BTCUSDT"):
    from v5.data.streams import InstrumentId, Venue
    return InstrumentId(symbol=symbol, venue=Venue.BINANCE, asset_class="perp")


class TestMarketDataCacheLookback:
    """T-D1 / AC-D1."""

    def test_append_evicts_beyond_lookback(self):
        from v5.bar_spec import BarSpec
        from v5.data.cache import MarketDataCache
        from v5.data.streams import BarData, DataStream
        from v5.rolling_cache import maxlen_for_bar_spec
        cache = MarketDataCache()
        inst = _inst(); spec = BarSpec.from_minutes(60)
        expected = maxlen_for_bar_spec(spec, "signal")
        for i in range(expected + 500):
            cache.on_data(_mk_bar(i * spec.period_ns, float(i), spec, inst), role="signal")
        stream = DataStream(instrument=inst, data_class=BarData, bar_spec=spec)
        assert len(cache.arrays(stream, role="signal").close) == expected


class TestMarketDataCacheThreeTupleKey:
    """T-D1a / AC-D1a — (inst, spec, signal) and (inst, spec, entry) distinct."""

    def test_same_stream_distinct_roles_distinct_caches(self):
        from v5.bar_spec import BarSpec
        from v5.data.cache import MarketDataCache
        from v5.data.streams import BarData, DataStream
        cache = MarketDataCache()
        inst = _inst(); spec = BarSpec.from_minutes(60)
        for i in range(3):
            cache.on_data(_mk_bar(i * spec.period_ns, 100.0 + i, spec, inst), role="signal")
        cache.on_data(_mk_bar(99 * spec.period_ns, 999.0, spec, inst), role="entry")
        stream = DataStream(instrument=inst, data_class=BarData, bar_spec=spec)
        sig = cache.arrays(stream, role="signal")
        ent = cache.arrays(stream, role="entry")
        assert len(sig.close) == 3 and len(ent.close) == 1
        assert 999.0 not in list(sig.close)
        assert ent.close[-1] == 999.0

    @pytest.mark.parametrize("role", ["signal", "entry", "exit"])
    def test_role_driven_lookback_applied(self, role):
        from v5.bar_spec import BarSpec
        from v5.data.cache import MarketDataCache
        from v5.data.streams import BarData, DataStream
        from v5.rolling_cache import maxlen_for_bar_spec
        cache = MarketDataCache()
        inst = _inst(f"TOK_{role}"); spec = BarSpec.from_minutes(60)
        expected = maxlen_for_bar_spec(spec, role)
        for i in range(expected + 10):
            cache.on_data(_mk_bar(i * spec.period_ns, 100.0, spec, inst), role=role)
        stream = DataStream(instrument=inst, data_class=BarData, bar_spec=spec)
        assert len(cache.arrays(stream, role=role).close) == expected


class TestMarketDataCacheMemoryBudget:
    """T-D1b / AC-D1b — 236×3×3 stays ≤ 1200 MB.

    Cross-check against M4's shipped arithmetic (v5/rolling_cache.py constants)
    to prevent tautological impls from passing. `return 0.0` is explicitly
    rejected via the strictly-positive lower bound on a non-trivial fleet.
    """

    def test_projected_memory_nontrivial_on_synthetic_fleet(self):
        """Test the PROJECTION FUNCTION works — not a budget gate on a synthetic
        fleet. AC-D1b's 1200 MB budget is a runtime constraint on whatever
        portfolio the paper/backtest engine loads (strategies declare their
        subscriptions). This test asserts the projection returns a plausible,
        strictly-positive value on a 236 × 3 × 3 fleet — rejects `return 0.0`
        and fixed-constant tautological impls. Budget enforcement is verified
        separately against a realistic portfolio (next test).

        Dispute resolution 2026-04-19: original test asserted ≤1200 MB on the
        Cartesian fleet. Verified mathematically impossible — 1m@signal alone
        for 236 tokens = 1946 MB using M4's shipped constants. Reviewer
        confirmed TEST_BUG; user confirmed "size is what it is — just test
        functionality". Upper bound removed from synthetic fleet; runtime
        budget check moved to test_realistic_portfolio_fits_budget.
        """
        from v5.bar_spec import BarSpec
        from v5.data.cache import MarketDataCache
        from v5.data.streams import InstrumentId, Venue
        cache = MarketDataCache()
        specs = [BarSpec.from_minutes(m) for m in (1, 60, 1440)]
        for i in range(236):
            inst = InstrumentId(symbol=f"TOK{i:03d}USDT", venue=Venue.BINANCE, asset_class="perp")
            for spec in specs:
                for role in ("signal", "entry", "exit"):
                    cache.on_data(_mk_bar(0, 100.0, spec, inst), role=role)
        mb = cache.compute_projected_memory_mb()
        # Lower bound: rejects `return 0.0` and fixed-constant impls.
        # 236 × 3 × 3 = 2124 caches of non-trivial size must project > 100 MB.
        assert mb > 100.0, (
            f"projected {mb:.1f} MB implausibly small for 236×3×3 fleet — "
            f"impl may be returning fixed-constant or ignoring maxlen"
        )
        # Upper sanity: projection is finite, not NaN/Inf
        import math
        assert math.isfinite(mb), f"projected mb must be finite, got {mb}"

    def test_realistic_portfolio_fits_1200mb_budget(self):
        """AC-D1b budget enforcement on a realistic portfolio shape.

        Paper trader / backtest declares one role per (instrument, spec) via
        Subscription.role — it does NOT subscribe every token to every
        (spec × role) combination. A realistic 236-token portfolio uses
        e.g. signal@1h + entry@5m + exit@1m — three caches per token.
        This fits easily under M4's 1.2 GB budget.
        """
        from v5.bar_spec import BarSpec
        from v5.data.cache import MarketDataCache
        from v5.data.streams import InstrumentId, Venue
        cache = MarketDataCache()
        # Realistic strategy declaration: 1 role per (spec) per token
        role_by_spec = {
            BarSpec.from_minutes(1):  "exit",
            BarSpec.from_minutes(5):  "entry",
            BarSpec.from_minutes(60): "signal",
        }
        for i in range(236):
            inst = InstrumentId(symbol=f"TOK{i:03d}USDT", venue=Venue.BINANCE, asset_class="perp")
            for spec, role in role_by_spec.items():
                cache.on_data(_mk_bar(0, 100.0, spec, inst), role=role)
        mb = cache.compute_projected_memory_mb()
        assert mb <= 1200.0, f"realistic 236-token portfolio projects {mb:.1f} MB > 1200 MB"
        assert mb > 20.0, f"realistic fleet projects {mb:.1f} MB — impl ignores maxlen?"

    def test_projection_matches_m4_arithmetic(self):
        """Cross-check: MarketDataCache.compute_projected_memory_mb()
        should agree with the per-tuple sum using M4's shipped constants.

        Prevents a broken impl that uses wrong cell-size or field count from
        shipping green just because the result is <1200 MB.
        """
        from v5.bar_spec import BarSpec
        from v5.data.cache import MarketDataCache
        from v5.data.streams import InstrumentId, Venue
        from v5.rolling_cache import (
            maxlen_for_bar_spec,
            _FIELDS_PER_BAR,
            _BYTES_PER_CELL,
        )
        cache = MarketDataCache()
        iid = InstrumentId(symbol="BTCUSDT", venue=Venue.BINANCE, asset_class="perp")
        spec_1m = BarSpec.from_minutes(1)
        spec_1h = BarSpec.from_minutes(60)
        # Two caches, two roles — controlled so we can compute ground truth.
        cache.on_data(_mk_bar(0, 100.0, spec_1m, iid), role="signal")
        cache.on_data(_mk_bar(0, 100.0, spec_1h, iid), role="entry")
        expected_bytes = (
            maxlen_for_bar_spec(spec_1m, "signal") * _FIELDS_PER_BAR * _BYTES_PER_CELL
            + maxlen_for_bar_spec(spec_1h, "entry") * _FIELDS_PER_BAR * _BYTES_PER_CELL
        )
        expected_mb = expected_bytes / (1024 * 1024)
        got_mb = cache.compute_projected_memory_mb()
        # Allow 1% tolerance for float rounding / any small ring-buffer header overhead
        assert abs(got_mb - expected_mb) / max(expected_mb, 1e-9) < 0.01, (
            f"projected_mb {got_mb:.4f} disagrees with M4-arithmetic "
            f"expectation {expected_mb:.4f}"
        )

    def test_compute_projected_memory_mb_returns_float(self):
        from v5.data.cache import MarketDataCache
        mb = MarketDataCache().compute_projected_memory_mb()
        assert isinstance(mb, float) and mb >= 0.0

    def test_project_from_subscriptions_eager(self):
        """Design §2.4: eager projection from declared subscriptions at startup,
        before any bars flow. `return 0.0` must NOT pass here."""
        from v5.bar_spec import BarSpec
        from v5.data.cache import MarketDataCache
        from v5.data.streams import (
            BarData, DataStream, InstrumentId, Subscription, Venue,
        )
        subs: list = []
        for i in range(50):
            inst = InstrumentId(symbol=f"TOK{i:03d}USDT", venue=Venue.BINANCE, asset_class="perp")
            stream = DataStream(instrument=inst, data_class=BarData,
                                bar_spec=BarSpec.from_minutes(60))
            subs.append(Subscription(stream=stream, handler=lambda b: None, role="signal"))
        projected = MarketDataCache().project_from_subscriptions(subs)
        assert projected > 0.0, "eager projection must be > 0 for non-empty subscription set"
        assert projected <= 1200.0, f"eager projection {projected:.1f} exceeds budget"

    def test_rejects_duplicate_ts_event(self):
        """T-D5 cache-layer slice — monotonicity invariant."""
        from v5.bar_spec import BarSpec
        from v5.data.cache import MarketDataCache
        cache = MarketDataCache()
        inst = _inst(); spec = BarSpec.from_minutes(60)
        cache.on_data(_mk_bar(10 * spec.period_ns, 100.0, spec, inst), role="signal")
        with pytest.raises(ValueError):
            cache.on_data(_mk_bar(10 * spec.period_ns, 101.0, spec, inst), role="signal")
