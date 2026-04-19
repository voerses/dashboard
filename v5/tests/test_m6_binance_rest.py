"""M6 — BinanceRESTClient weight tracker + time-drift sync (T-D9, T-D10).

All tests MUST FAIL today — v5.data.clients.binance_rest does not exist.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


class TestRestWeightTracker:
    """T-D9 / AC-D10 — weight tracker + RateLimitExceeded."""

    def test_initial_weight_budget_matches_binance_cap(self):
        from v5.data.clients.binance_rest import BinanceRESTClient
        assert BinanceRESTClient()._weight_budget == 1200

    def test_check_budget_consumes_weight(self):
        from v5.data.clients.binance_rest import BinanceRESTClient
        c = BinanceRESTClient()
        c._check_budget(10)
        assert c._weight_budget == 1190

    def test_check_budget_raises_when_exhausted(self):
        from v5.data.clients.binance_rest import BinanceRESTClient
        from v5.data.exceptions import RateLimitExceeded
        c = BinanceRESTClient()
        c._check_budget(1200)
        assert c._weight_budget == 0
        c._weight_window_reset_ts = 2**62  # force retry_after_ms > 0
        with pytest.raises(RateLimitExceeded) as ei:
            c._check_budget(1)
        assert hasattr(ei.value, "retry_after_ms")
        assert ei.value.retry_after_ms > 0

    def test_on_response_resets_from_venue_header(self):
        from v5.data.clients.binance_rest import BinanceRESTClient
        c = BinanceRESTClient()
        c._weight_budget = 50
        c._on_response({"X-MBX-USED-WEIGHT-1M": "200"})
        assert c._weight_budget == 1000

    def test_on_response_missing_header_graceful(self):
        from v5.data.clients.binance_rest import BinanceRESTClient
        c = BinanceRESTClient()
        c._weight_budget = 500
        c._on_response({})
        assert c._weight_budget == 1200


class TestRestTimeDriftSync:
    """T-D10 / AC-D11 — time-drift sync + severity-based events."""

    def test_sync_populates_venue_clock_offset_ms(self):
        from v5.data.clients.binance_rest import BinanceRESTClient
        c = BinanceRESTClient()
        c._apply_time_sync(venue_ms=1500, local_ms=0)
        assert abs(c.venue_clock_offset_ms - 1500) < 2

    def test_drift_over_500ms_warns(self, caplog):
        from v5.data.clients.binance_rest import BinanceRESTClient
        c = BinanceRESTClient()
        with caplog.at_level(logging.WARNING):
            c._apply_time_sync(venue_ms=800, local_ms=0)
        assert any(r.levelno >= logging.WARNING for r in caplog.records)

    def test_drift_over_5000ms_emits_clock_drift_high(self):
        from v5.data.bus import MessageBus
        from v5.data.clients.binance_rest import BinanceRESTClient
        from v5.data.exceptions import ClockDriftHigh
        bus = MessageBus()
        c = BinanceRESTClient(bus=bus)
        seen: list = []
        c.subscribe_drift_events(seen.append)
        c._apply_time_sync(venue_ms=6000, local_ms=0)
        assert any(isinstance(ev, ClockDriftHigh) for ev in seen)
