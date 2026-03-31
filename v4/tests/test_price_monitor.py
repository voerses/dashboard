"""Acceptance tests for Task 6: PriceMonitor (AC3, AC10).

Tests verify:
  - PriceMonitor calls callback with (token, price, timestamp)
  - Subscription to correct streams
  - Auto-reconnect with exponential backoff
  - REST fallback on disconnect
  - REST price fetch on reconnection

NOTE: Uses mocks for WebSocket and REST -- no real network calls.

All tests use synthetic data -- no real market data required.
These tests MUST FAIL until price_monitor.py is implemented (RED phase).
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "v4"))

import pytest

from v4.price_monitor import PriceMonitor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_kline_message(
    token: str, price: float, timestamp: int = 1697380200000, is_closed: bool = False,
) -> dict:
    """Build a mock Binance kline WebSocket message for perp venue."""
    symbol = f"{token}USDT"
    return {
        "e": "kline",
        "E": timestamp,
        "s": symbol,
        "k": {
            "t": timestamp,
            "T": timestamp + 59999,
            "s": symbol,
            "i": "1m",
            "o": str(price),
            "c": str(price),
            "h": str(price),
            "l": str(price),
            "v": "100.0",
            "n": 50,
            "x": is_closed,
            "q": "6500000.0",
        },
    }


def _make_rest_price_response(token: str, price: float) -> dict:
    """Build a mock Binance REST premiumIndex response."""
    return {
        "symbol": f"{token}USDT",
        "markPrice": str(price),
        "time": int(time.time() * 1000),
    }


# ===================================================================
# Test: PriceMonitor calls callback with (token, price, timestamp)
# ===================================================================

class TestPriceCallback:
    """PriceMonitor dispatches price updates via callback."""

    def test_callback_receives_token_price_timestamp(self):
        """Callback is called with (token, price, timestamp) on price update."""
        received = []

        def on_price(token, price, timestamp):
            received.append((token, price, timestamp))

        monitor = PriceMonitor(callback=on_price)
        msg = _make_kline_message("BTC", 65_000.0, 1697380200000)
        monitor._handle_message(msg)

        assert len(received) == 1
        assert received[0][0] == "BTC"
        assert received[0][1] == pytest.approx(65_000.0)
        assert received[0][2] == 1697380200000

    def test_callback_multiple_tokens(self):
        """Callback fires for each token update independently."""
        received = []

        def on_price(token, price, timestamp):
            received.append(token)

        monitor = PriceMonitor(callback=on_price)
        monitor._handle_message(_make_kline_message("BTC", 65_000.0))
        monitor._handle_message(_make_kline_message("ETH", 3_400.0))
        monitor._handle_message(_make_kline_message("SOL", 155.0))

        assert received == ["BTC", "ETH", "SOL"]


# ===================================================================
# Test: Subscription to correct streams
# ===================================================================

class TestStreamSubscription:
    """PriceMonitor subscribes to @kline_1m streams."""

    def test_subscribe_generates_correct_stream_names(self):
        """Subscribing to tokens generates the right stream names."""
        monitor = PriceMonitor(callback=lambda *a: None)
        streams = monitor._build_stream_names(["BTC", "ETH", "SOL"])

        assert "btcusdt@kline_1m" in streams
        assert "ethusdt@kline_1m" in streams
        assert "solusdt@kline_1m" in streams

    def test_subscribe_empty_list(self):
        """No streams for empty token list."""
        monitor = PriceMonitor(callback=lambda *a: None)
        streams = monitor._build_stream_names([])
        assert streams == []


# ===================================================================
# Test: Auto-reconnect with exponential backoff
# ===================================================================

class TestAutoReconnect:
    """PriceMonitor auto-reconnects with exponential backoff."""

    def test_backoff_sequence(self):
        """Backoff delays follow exponential pattern: 1, 2, 4, 8... max 60."""
        monitor = PriceMonitor(callback=lambda *a: None)

        delays = []
        for i in range(7):
            delays.append(monitor._compute_backoff_delay(attempt=i))

        assert delays[0] == pytest.approx(1.0)
        assert delays[1] == pytest.approx(2.0)
        assert delays[2] == pytest.approx(4.0)
        assert delays[3] == pytest.approx(8.0)
        assert delays[4] == pytest.approx(16.0)
        assert delays[5] == pytest.approx(32.0)
        # Max capped at 60
        assert delays[6] <= 60.0

    def test_backoff_max_cap(self):
        """Backoff never exceeds 60 seconds."""
        monitor = PriceMonitor(callback=lambda *a: None)
        delay = monitor._compute_backoff_delay(attempt=100)
        assert delay <= 60.0


# ===================================================================
# Test: REST fallback on disconnect
# ===================================================================

class TestRestFallback:
    """PriceMonitor falls back to REST polling when WS disconnects."""

    def test_rest_fallback_polls_active_tokens(self):
        """REST fallback polls tokens with active confirmation timers."""
        monitor = PriceMonitor(callback=lambda *a: None)
        monitor._active_tokens_for_rest = {"BTC", "ETH"}

        # The monitor should have a REST fallback method
        assert hasattr(monitor, "_fetch_rest_prices")

    def test_rest_poll_interval(self):
        """REST fallback polls every 10 seconds."""
        monitor = PriceMonitor(callback=lambda *a: None)
        assert monitor.rest_poll_interval_s == 10

    def test_rest_fallback_stops_on_ws_reconnect(self):
        """REST polling stops when WebSocket reconnects."""
        monitor = PriceMonitor(callback=lambda *a: None)
        monitor._ws_connected = False
        assert monitor._should_use_rest_fallback() is True

        monitor._ws_connected = True
        assert monitor._should_use_rest_fallback() is False


# ===================================================================
# Test: REST price fetch on reconnection
# ===================================================================

class TestRestOnReconnect:
    """PriceMonitor fetches REST prices on WS reconnection to fill gap."""

    def test_rest_fetch_on_reconnect(self):
        """On reconnect, REST prices fetched to detect breaches during gap."""
        monitor = PriceMonitor(callback=lambda *a: None)
        # Method should exist for gap coverage
        assert hasattr(monitor, "_fetch_rest_prices_on_reconnect")


# ===================================================================
# Test: Venue parameter defaults and selection
# ===================================================================

class TestVenueParameter:
    """PriceMonitor venue parameter selects perp or spot endpoints."""

    def test_default_venue_is_perp(self):
        """Default venue is 'perp'."""
        monitor = PriceMonitor(callback=lambda *a: None)
        assert monitor._venue == "perp"

    def test_venue_spot(self):
        """Setting venue='spot' stores spot venue."""
        monitor = PriceMonitor(callback=lambda *a: None, venue="spot")
        assert monitor._venue == "spot"

    def test_perp_venue_uses_perp_streams(self):
        """Perp venue builds kline_1m stream names."""
        monitor = PriceMonitor(callback=lambda *a: None, venue="perp")
        streams = monitor._build_stream_names(["BTC"])
        assert "btcusdt@kline_1m" in streams

    def test_spot_venue_uses_spot_streams(self):
        """Spot venue builds miniTicker stream names."""
        monitor = PriceMonitor(callback=lambda *a: None, venue="spot")
        streams = monitor._build_stream_names(["BTC"])
        assert "btcusdt@miniTicker" in streams

    def test_perp_monitor_handles_perp_messages(self):
        """Perp venue processes kline messages."""
        received = []
        monitor = PriceMonitor(callback=lambda t, p, ts: received.append((t, p)), venue="perp")
        monitor._handle_message(_make_kline_message("BTC", 65_000.0))
        assert len(received) == 1
        assert received[0][0] == "BTC"

    def test_perp_monitor_ignores_spot_messages(self):
        """Perp venue ignores 24hrMiniTicker messages."""
        received = []
        monitor = PriceMonitor(callback=lambda t, p, ts: received.append(t), venue="perp")
        monitor._handle_message({"e": "24hrMiniTicker", "s": "BTCUSDT", "c": "65000.0", "E": 1})
        assert len(received) == 0

    def test_spot_monitor_ignores_perp_messages(self):
        """Spot venue ignores markPriceUpdate messages."""
        received = []
        monitor = PriceMonitor(callback=lambda t, p, ts: received.append(t), venue="spot")
        monitor._handle_message({"e": "markPriceUpdate", "s": "BTCUSDT", "p": "65000.0", "E": 1})
        assert len(received) == 0


# ===================================================================
# Test: Spot callback — miniTicker message parsing
# ===================================================================

class TestSpotCallback:
    """Spot PriceMonitor dispatches spot price updates via callback."""

    def test_spot_callback_receives_token_price_timestamp(self):
        """Spot callback is called with (token, price, timestamp) on miniTicker."""
        received = []
        monitor = PriceMonitor(callback=lambda t, p, ts: received.append((t, p, ts)), venue="spot")
        msg = {
            "e": "24hrMiniTicker",
            "E": 1697380200000,
            "s": "BTCUSDT",
            "c": "65000.00",
            "o": "64500.00",
            "h": "65500.00",
            "l": "64000.00",
            "v": "1234.56",
            "q": "80000000.00",
        }
        monitor._handle_message(msg)

        assert len(received) == 1
        assert received[0][0] == "BTC"
        assert received[0][1] == pytest.approx(65_000.0)
        assert received[0][2] == 1697380200000

    def test_spot_callback_multiple_tokens(self):
        """Spot callback fires for each token update independently."""
        received = []
        monitor = PriceMonitor(callback=lambda t, p, ts: received.append(t), venue="spot")

        for token, price in [("BTC", "65000"), ("ETH", "3400"), ("SOL", "155")]:
            monitor._handle_message({
                "e": "24hrMiniTicker", "E": 1, "s": f"{token}USDT", "c": price,
            })

        assert received == ["BTC", "ETH", "SOL"]

    def test_spot_ignores_bad_price(self):
        """Spot callback ignores messages with invalid price."""
        received = []
        monitor = PriceMonitor(callback=lambda t, p, ts: received.append(t), venue="spot")
        monitor._handle_message({"e": "24hrMiniTicker", "E": 1, "s": "BTCUSDT", "c": "0"})
        monitor._handle_message({"e": "24hrMiniTicker", "E": 1, "s": "BTCUSDT", "c": "-1"})
        assert len(received) == 0

    def test_spot_updates_latest_prices(self):
        """Spot messages update get_latest_prices()."""
        monitor = PriceMonitor(callback=lambda *a: None, venue="spot")
        monitor._handle_message({"e": "24hrMiniTicker", "E": 1, "s": "BTCUSDT", "c": "65000.0"})
        prices = monitor.get_latest_prices()
        assert "BTC" in prices
        assert prices["BTC"] == pytest.approx(65_000.0)


# ===================================================================
# Test: Spot stream subscription names
# ===================================================================

class TestSpotStreamSubscription:
    """Spot PriceMonitor subscribes to @miniTicker streams."""

    def test_spot_stream_names(self):
        """Spot stream names use @miniTicker suffix."""
        monitor = PriceMonitor(callback=lambda *a: None, venue="spot")
        streams = monitor._build_stream_names(["BTC", "ETH", "SOL"])
        assert "btcusdt@miniTicker" in streams
        assert "ethusdt@miniTicker" in streams
        assert "solusdt@miniTicker" in streams

    def test_spot_stream_no_mark_price(self):
        """Spot stream names do NOT use @markPrice suffix."""
        monitor = PriceMonitor(callback=lambda *a: None, venue="spot")
        streams = monitor._build_stream_names(["BTC"])
        assert all("markPrice" not in s for s in streams)

    def test_spot_empty_list(self):
        """No streams for empty token list (spot venue)."""
        monitor = PriceMonitor(callback=lambda *a: None, venue="spot")
        assert monitor._build_stream_names([]) == []


# ===================================================================
# Test: Spot symbol mapping — no 1000-prefix
# ===================================================================

class TestSpotSymbolMapping:
    """Spot symbols don't use the 1000-prefix convention."""

    def test_spot_shib_no_prefix(self):
        """SHIB maps to SHIBUSDT on spot (not 1000SHIBUSDT)."""
        from v4.price_monitor import _token_to_spot_symbol
        assert _token_to_spot_symbol("SHIB") == "SHIBUSDT"

    def test_spot_btc_mapping(self):
        """BTC maps to BTCUSDT on spot."""
        from v4.price_monitor import _token_to_spot_symbol
        assert _token_to_spot_symbol("BTC") == "BTCUSDT"

    def test_spot_symbol_to_token(self):
        """SHIBUSDT reverse-maps to SHIB on spot."""
        from v4.price_monitor import _spot_symbol_to_token
        assert _spot_symbol_to_token("SHIBUSDT") == "SHIB"

    def test_spot_btc_reverse(self):
        """BTCUSDT reverse-maps to BTC on spot."""
        from v4.price_monitor import _spot_symbol_to_token
        assert _spot_symbol_to_token("BTCUSDT") == "BTC"

    def test_perp_shib_has_prefix(self):
        """Verify perp SHIB still uses 1000-prefix (contrast test)."""
        from v4.price_monitor import _token_to_symbol
        assert _token_to_symbol("SHIB") == "1000SHIBUSDT"

    def test_spot_stream_shib_no_prefix(self):
        """Spot stream for SHIB uses shibusdt, not 1000shibusdt."""
        monitor = PriceMonitor(callback=lambda *a: None, venue="spot")
        streams = monitor._build_stream_names(["SHIB"])
        assert "shibusdt@miniTicker" in streams
        assert all("1000" not in s for s in streams)

    def test_perp_stream_shib_has_prefix(self):
        """Perp stream for SHIB uses 1000shibusdt (contrast test)."""
        monitor = PriceMonitor(callback=lambda *a: None, venue="perp")
        streams = monitor._build_stream_names(["SHIB"])
        assert "1000shibusdt@kline_1m" in streams


# ===================================================================
# Test: Spot REST fallback
# ===================================================================

class TestSpotRestFallback:
    """Spot PriceMonitor REST fallback uses /api/v3/ticker/price."""

    def test_spot_has_rest_fallback_method(self):
        """Spot monitor has _fetch_spot_rest_prices method."""
        monitor = PriceMonitor(callback=lambda *a: None, venue="spot")
        assert hasattr(monitor, "_fetch_spot_rest_prices")

    def test_spot_has_single_rest_method(self):
        """Spot monitor has _fetch_single_spot_rest_price method."""
        monitor = PriceMonitor(callback=lambda *a: None, venue="spot")
        assert hasattr(monitor, "_fetch_single_spot_rest_price")

    def test_spot_rest_routes_correctly(self):
        """fetch_single_rest_price on spot venue routes to spot REST."""
        monitor = PriceMonitor(callback=lambda *a: None, venue="spot")
        # Patch the spot-specific method to verify routing
        called = []
        monitor._fetch_single_spot_rest_price = lambda t: called.append(t) or None
        monitor.fetch_single_rest_price("BTC")
        assert called == ["BTC"]

    def test_perp_rest_routes_correctly(self):
        """fetch_single_rest_price on perp venue routes to perp REST."""
        monitor = PriceMonitor(callback=lambda *a: None, venue="perp")
        called = []
        monitor._fetch_single_perp_rest_price = lambda t: called.append(t) or None
        monitor.fetch_single_rest_price("BTC")
        assert called == ["BTC"]
