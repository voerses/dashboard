"""M6 — Gap detection 3-mode policy (T-D5, T-D5a, T-D5b / AC-D5).

All tests MUST FAIL today — v5.data.gaps does not exist.
"""
from __future__ import annotations

import logging
import math
import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


def _mk_bar(ts, close, spec, inst):
    class _B: pass
    b = _B()
    b.instrument_id = inst; b.bar_spec = spec
    b.ts_event = ts; b.ts_init = ts + 1
    b.open = b.high = b.low = b.close = close
    b.volume = 1000.0
    return b


def _stream():
    from v5.bar_spec import BarSpec
    from v5.data.streams import BarData, DataStream, InstrumentId, Venue
    inst = InstrumentId(symbol="BTCUSDT", venue=Venue.BINANCE, asset_class="perp")
    return DataStream(instrument=inst, data_class=BarData,
                      bar_spec=BarSpec.from_minutes(60))


class _NoRefill:
    def __init__(self): self.calls = 0
    def request(self, s, a, b):
        self.calls += 1; return []


class _Refill:
    def __init__(self, fill): self._fill = fill; self.calls = 0
    def request(self, s, a, b):
        self.calls += 1; return [self._fill]


class TestGapPolicyStrict:
    """T-D5 / AC-D5 — STRICT raises after N retries; rejects dupes/stales."""

    def test_duplicate_ts_event_rejected_strict(self):
        from v5.bar_spec import BarSpec
        from v5.data.gaps import GapDetector
        from v5.data.streams import GapPolicy
        s = _stream(); spec = BarSpec.from_minutes(60)
        det = GapDetector(stream=s, policy=GapPolicy.STRICT,
                          rest_client=_NoRefill(), max_retries=3)
        det.on_bar(_mk_bar(10 * spec.period_ns, 100.0, spec, s.instrument))
        with pytest.raises(ValueError):
            det.on_bar(_mk_bar(10 * spec.period_ns, 101.0, spec, s.instrument))

    def test_stale_ts_event_rejected(self):
        from v5.bar_spec import BarSpec
        from v5.data.gaps import GapDetector
        from v5.data.streams import GapPolicy
        s = _stream(); spec = BarSpec.from_minutes(60)
        det = GapDetector(stream=s, policy=GapPolicy.STRICT,
                          rest_client=_NoRefill(), max_retries=3)
        det.on_bar(_mk_bar(10 * spec.period_ns, 100.0, spec, s.instrument))
        with pytest.raises(ValueError):
            det.on_bar(_mk_bar(5 * spec.period_ns, 99.0, spec, s.instrument))

    def test_strict_raises_data_gap_error_after_retries(self):
        from v5.bar_spec import BarSpec
        from v5.data.exceptions import DataGapError
        from v5.data.gaps import GapDetector
        from v5.data.streams import GapPolicy
        s = _stream(); spec = BarSpec.from_minutes(60)
        rest = _NoRefill()
        det = GapDetector(stream=s, policy=GapPolicy.STRICT,
                          rest_client=rest, max_retries=3)
        det.on_bar(_mk_bar(1 * spec.period_ns, 100.0, spec, s.instrument))
        with pytest.raises(DataGapError):
            det.on_bar(_mk_bar(11 * spec.period_ns, 200.0, spec, s.instrument))
        assert rest.calls >= 3

    def test_strict_publishes_gap_detected_event(self):
        from v5.bar_spec import BarSpec
        from v5.data.bus import MessageBus
        from v5.data.gaps import GapDetected, GapDetector
        from v5.data.streams import GapPolicy
        s = _stream(); spec = BarSpec.from_minutes(60)
        bus = MessageBus(); seen: list = []
        bus.subscribe(s, seen.append)
        fill = _mk_bar(2 * spec.period_ns, 150.0, spec, s.instrument)
        det = GapDetector(stream=s, policy=GapPolicy.STRICT,
                          rest_client=_Refill(fill), max_retries=3, bus=bus)
        det.on_bar(_mk_bar(1 * spec.period_ns, 100.0, spec, s.instrument))
        det.on_bar(_mk_bar(3 * spec.period_ns, 200.0, spec, s.instrument))
        assert any(isinstance(ev, GapDetected) for ev in seen)


class TestGapPolicyNanFill:
    """T-D5a / AC-D5 — NAN_FILL emits NaN bars at missing slots."""

    def test_nan_bar_at_gap_slot(self):
        from v5.bar_spec import BarSpec
        from v5.data.gaps import GapDetector
        from v5.data.streams import GapPolicy
        s = _stream(); spec = BarSpec.from_minutes(60)
        delivered: list = []
        det = GapDetector(stream=s, policy=GapPolicy.NAN_FILL,
                          rest_client=_NoRefill(), max_retries=1,
                          handler=delivered.append)
        det.on_bar(_mk_bar(1 * spec.period_ns, 100.0, spec, s.instrument))
        det.on_bar(_mk_bar(3 * spec.period_ns, 300.0, spec, s.instrument))
        assert len(delivered) == 3
        assert delivered[1].ts_event == 2 * spec.period_ns
        assert math.isnan(delivered[1].close)
        assert math.isnan(delivered[1].high)
        assert math.isnan(delivered[1].low)

    def test_nan_fill_monotonicity_preserved(self):
        from v5.bar_spec import BarSpec
        from v5.data.gaps import GapDetector
        from v5.data.streams import GapPolicy
        s = _stream(); spec = BarSpec.from_minutes(60)
        delivered: list = []
        det = GapDetector(stream=s, policy=GapPolicy.NAN_FILL,
                          rest_client=_NoRefill(), max_retries=1,
                          handler=delivered.append)
        det.on_bar(_mk_bar(1 * spec.period_ns, 100.0, spec, s.instrument))
        det.on_bar(_mk_bar(4 * spec.period_ns, 400.0, spec, s.instrument))
        ts = [b.ts_event for b in delivered]
        assert ts == sorted(ts) and len(set(ts)) == len(ts)


class TestGapPolicySkip:
    """T-D5b / AC-D5 — SKIP silently drops, logs WARN."""

    def test_skip_drops_gap_and_warns(self, caplog):
        from v5.bar_spec import BarSpec
        from v5.data.gaps import GapDetector
        from v5.data.streams import GapPolicy
        s = _stream(); spec = BarSpec.from_minutes(60)
        delivered: list = []
        det = GapDetector(stream=s, policy=GapPolicy.SKIP,
                          rest_client=_NoRefill(), max_retries=1,
                          handler=delivered.append)
        with caplog.at_level(logging.WARNING):
            det.on_bar(_mk_bar(1 * spec.period_ns, 100.0, spec, s.instrument))
            det.on_bar(_mk_bar(4 * spec.period_ns, 400.0, spec, s.instrument))
        assert len(delivered) == 2
        assert any(r.levelno >= logging.WARNING for r in caplog.records)


class TestMonotonicityInvariantAcrossAllPolicies:
    """AC-D5: 'Cache rejects bars with ts_event <= last_ts regardless of mode'.

    The monotonicity invariant is policy-independent. A duplicate or stale
    bar MUST be rejected under STRICT, NAN_FILL, AND SKIP — otherwise an
    impl that only enforces monotonicity in STRICT mode would ship green.
    """

    @pytest.mark.parametrize("policy_name", ["STRICT", "NAN_FILL", "SKIP"])
    def test_duplicate_ts_event_rejected_regardless_of_policy(self, policy_name, caplog):
        """AC-D5 monotonicity invariant: duplicate ts_event is rejected
        (not forwarded to handler) under all three policies.

        - STRICT raises ValueError (loud, fail-fast)
        - NAN_FILL / SKIP silently drop + log WARN

        Rationale (reviewer-verified 2026-04-19): brief AC-D5 says "reject...
        log" not "raise"; design §2.5 SM says "reject, log, no forward" with
        raise reserved for STRICT. Industry precedent (Nautilus/Lean/
        Backtrader) all silent-drop duplicates. Binance WS reconnect routinely
        delivers duplicates, so raising on every dupe would crash on every
        reconnect — defeats NAN_FILL/SKIP's 'tolerate upstream hiccups' intent.
        """
        from v5.bar_spec import BarSpec
        from v5.data.gaps import GapDetector
        from v5.data.streams import GapPolicy
        policy = getattr(GapPolicy, policy_name)
        s = _stream(); spec = BarSpec.from_minutes(60)
        delivered: list = []
        det = GapDetector(
            stream=s, policy=policy,
            rest_client=_NoRefill(), max_retries=0,
            handler=delivered.append,
        )
        b1 = _mk_bar(1 * spec.period_ns, 100.0, spec, s.instrument)
        det.on_bar(b1)
        delivered_count_after_first = len(delivered)
        b1_dup = _mk_bar(1 * spec.period_ns, 999.0, spec, s.instrument)
        with caplog.at_level(logging.WARNING):
            if policy == GapPolicy.STRICT:
                with pytest.raises(ValueError):
                    det.on_bar(b1_dup)
            else:
                det.on_bar(b1_dup)  # no raise for NAN_FILL / SKIP
        # All policies: duplicate must not be forwarded to handler
        assert len(delivered) == delivered_count_after_first, (
            f"{policy_name}: duplicate ts_event was forwarded (rejection failed)"
        )
        # All policies log a WARN about the rejection
        assert any(r.levelno >= logging.WARNING for r in caplog.records), (
            f"{policy_name}: no WARN logged on monotonicity violation"
        )

    @pytest.mark.parametrize("policy_name", ["STRICT", "NAN_FILL", "SKIP"])
    def test_stale_ts_event_rejected_regardless_of_policy(self, policy_name, caplog):
        """Stale ts_event (backwards in time) rejected under all policies.
        Same split as duplicate case: STRICT raises, others drop-silent-with-WARN.
        """
        from v5.bar_spec import BarSpec
        from v5.data.gaps import GapDetector
        from v5.data.streams import GapPolicy
        policy = getattr(GapPolicy, policy_name)
        s = _stream(); spec = BarSpec.from_minutes(60)
        delivered: list = []
        det = GapDetector(
            stream=s, policy=policy,
            rest_client=_NoRefill(), max_retries=0,
            handler=delivered.append,
        )
        det.on_bar(_mk_bar(2 * spec.period_ns, 200.0, spec, s.instrument))
        delivered_after_forward = len(delivered)
        stale = _mk_bar(1 * spec.period_ns, 100.0, spec, s.instrument)
        with caplog.at_level(logging.WARNING):
            if policy == GapPolicy.STRICT:
                with pytest.raises(ValueError):
                    det.on_bar(stale)
            else:
                det.on_bar(stale)
        assert len(delivered) == delivered_after_forward, (
            f"{policy_name}: stale ts_event was forwarded"
        )
        assert any(r.levelno >= logging.WARNING for r in caplog.records), (
            f"{policy_name}: no WARN logged on stale bar"
        )


class TestBackpressureBehavior:
    """AC-D9 — slow subscriber (>100ms for 1m bar) logs a WARNING. No bars dropped.

    The brief (line 815) explicitly says: 'No backpressure or dropping —
    all bars are delivered. Strategies with heavy computation should offload
    to background threads.'
    """

    def test_slow_handler_logs_warning_but_delivers(self, caplog):
        """AC-D9: slow handler → WARN log, no dropping.

        Uses a 1m-matched stream/bar spec so gap detection does NOT fire
        (this test isolates backpressure behavior, not gap handling).
        """
        import time
        from v5.bar_spec import BarSpec
        from v5.data.gaps import GapDetector
        from v5.data.streams import BarData, DataStream, GapPolicy, InstrumentId, Venue
        spec = BarSpec.from_minutes(1)
        inst = InstrumentId(symbol="BTCUSDT", venue=Venue.BINANCE, asset_class="perp")
        s_1m = DataStream(instrument=inst, data_class=BarData, bar_spec=spec)
        delivered: list = []

        def slow_handler(bar):
            time.sleep(0.15)  # 150ms > 100ms threshold
            delivered.append(bar)

        det = GapDetector(
            stream=s_1m, policy=GapPolicy.STRICT,
            rest_client=_NoRefill(), max_retries=0,
            handler=slow_handler,
        )
        with caplog.at_level(logging.WARNING):
            det.on_bar(_mk_bar(1 * spec.period_ns, 100.0, spec, inst))
            det.on_bar(_mk_bar(2 * spec.period_ns, 200.0, spec, inst))
        # Both bars MUST be delivered (no dropping)
        assert len(delivered) == 2, f"backpressure dropped bars: {len(delivered)}/2 delivered"
        # At least one WARNING about slow handler
        assert any(
            r.levelno >= logging.WARNING and ("slow" in r.message.lower() or "100" in r.message)
            for r in caplog.records
        ), "expected WARN log about slow (>100ms) handler"
