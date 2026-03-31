"""Acceptance tests for Task 1: PriceMonitor kline stream support.

Tests verify:
  - AC1: Perp PriceMonitor subscribes to @kline_1m streams (not @markPrice@1s)
  - AC2: _handle_perp_message dispatches price callback on kline events (every update)
  - AC3: kline_callback called with full bar on candle close (x:true), not on interim updates
  - AC5: PriceMonitor accepts kline_callback parameter; None is safe
  - AC16: update_subscriptions produces @kline_1m stream names
  - AC18: Spot venue still uses @miniTicker streams, not @kline_1m

All tests MUST FAIL until implementation is done (RED phase).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "v4"))

import pytest

from v4.price_monitor import PriceMonitor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_kline_message(
    symbol: str,
    close: str = "65100.0",
    open_: str = "65000.0",
    high: str = "65200.0",
    low: str = "64900.0",
    volume: str = "100.0",
    timestamp: int = 1697380200000,
    close_time: int = 1697380259999,
    is_closed: bool = False,
    interval: str = "1m",
    num_trades: int = 50,
    quote_volume: str = "6500000.0",
) -> dict:
    """Build a mock Binance kline WebSocket message."""
    return {
        "e": "kline",
        "E": timestamp,
        "s": symbol,
        "k": {
            "t": timestamp,
            "T": close_time,
            "s": symbol,
            "i": interval,
            "o": open_,
            "c": close,
            "h": high,
            "l": low,
            "v": volume,
            "n": num_trades,
            "x": is_closed,
            "q": quote_volume,
        },
    }


# ===================================================================
# AC1: Perp PriceMonitor subscribes to @kline_1m streams
# ===================================================================

class TestKlineStreamNames:
    """AC1: PriceMonitor(venue='perp') uses @kline_1m streams."""

    def test_perp_build_stream_names_uses_kline_1m(self):
        """AC1: _build_stream_names for perp returns @kline_1m streams, not @markPrice@1s."""
        monitor = PriceMonitor(callback=lambda *a: None, venue="perp")
        streams = monitor._build_stream_names(["BTC", "ETH"])

        assert "btcusdt@kline_1m" in streams, "BTC stream should be btcusdt@kline_1m"
        assert "ethusdt@kline_1m" in streams, "ETH stream should be ethusdt@kline_1m"
        # Must NOT contain markPrice streams
        for s in streams:
            assert "markPrice" not in s, f"Perp stream should not contain markPrice: {s}"

    def test_perp_stream_names_1000_prefix_tokens(self):
        """AC1: 1000-prefix tokens (e.g., SHIB) produce 1000shibusdt@kline_1m."""
        monitor = PriceMonitor(callback=lambda *a: None, venue="perp")
        streams = monitor._build_stream_names(["SHIB"])

        assert "1000shibusdt@kline_1m" in streams, \
            "SHIB should map to 1000shibusdt@kline_1m"

    def test_perp_stream_names_multiple_1000_prefix(self):
        """AC1: Multiple 1000-prefix tokens all use kline_1m."""
        monitor = PriceMonitor(callback=lambda *a: None, venue="perp")
        streams = monitor._build_stream_names(["PEPE", "FLOKI", "BONK"])

        assert "1000pepeusdt@kline_1m" in streams
        assert "1000flokiusdt@kline_1m" in streams
        assert "1000bonkusdt@kline_1m" in streams

    def test_perp_empty_list_returns_empty(self):
        """AC1: Empty token list returns empty stream list."""
        monitor = PriceMonitor(callback=lambda *a: None, venue="perp")
        assert monitor._build_stream_names([]) == []


# ===================================================================
# AC2: _handle_perp_message dispatches on kline events
# ===================================================================

class TestKlinePriceCallback:
    """AC2: Price callback fires on every kline update (both x:false and x:true)."""

    def test_price_callback_on_non_closed_kline(self):
        """AC2: Price callback called with (token, close_price, timestamp) on x:false."""
        received = []

        def on_price(token, price, timestamp):
            received.append((token, price, timestamp))

        monitor = PriceMonitor(callback=on_price, venue="perp")
        msg = _make_kline_message("BTCUSDT", close="65100.0", timestamp=1697380200000, is_closed=False)
        monitor._handle_perp_message(msg)

        assert len(received) == 1, "Price callback should fire on non-closed kline update"
        assert received[0][0] == "BTC"
        assert received[0][1] == pytest.approx(65100.0)
        assert received[0][2] == 1697380200000

    def test_price_callback_on_closed_kline(self):
        """AC2: Price callback ALSO fires on closed kline (x:true)."""
        received = []

        def on_price(token, price, timestamp):
            received.append((token, price, timestamp))

        monitor = PriceMonitor(callback=on_price, venue="perp")
        msg = _make_kline_message("ETHUSDT", close="3400.5", timestamp=1697380260000, is_closed=True)
        monitor._handle_perp_message(msg)

        assert len(received) == 1, "Price callback should fire on closed kline too"
        assert received[0][0] == "ETH"
        assert received[0][1] == pytest.approx(3400.5)

    def test_price_callback_fires_every_update(self):
        """AC2: Callback fires on EVERY kline update regardless of x status."""
        received = []

        def on_price(token, price, timestamp):
            received.append((token, price))

        monitor = PriceMonitor(callback=on_price, venue="perp")

        # Simulate 3 interim updates and 1 close
        monitor._handle_perp_message(_make_kline_message("BTCUSDT", close="65050.0", is_closed=False))
        monitor._handle_perp_message(_make_kline_message("BTCUSDT", close="65080.0", is_closed=False))
        monitor._handle_perp_message(_make_kline_message("BTCUSDT", close="65100.0", is_closed=False))
        monitor._handle_perp_message(_make_kline_message("BTCUSDT", close="65100.0", is_closed=True))

        assert len(received) == 4, "Price callback should fire on every kline update"

    def test_perp_handler_ignores_non_kline_events(self):
        """AC2: _handle_perp_message ignores non-kline event types."""
        received = []
        monitor = PriceMonitor(callback=lambda t, p, ts: received.append(t), venue="perp")

        # Old markPriceUpdate messages should be ignored
        monitor._handle_perp_message({
            "e": "markPriceUpdate", "s": "BTCUSDT", "p": "65000.0", "E": 1,
        })
        assert len(received) == 0, "Perp handler should ignore markPriceUpdate events"

    def test_1000_prefix_token_callback(self):
        """AC2: 1000-prefix symbols are mapped back to internal token names."""
        received = []

        def on_price(token, price, timestamp):
            received.append(token)

        monitor = PriceMonitor(callback=on_price, venue="perp")
        msg = _make_kline_message("1000SHIBUSDT", close="0.00002345")
        monitor._handle_perp_message(msg)

        assert len(received) == 1
        assert received[0] == "SHIB", "1000SHIBUSDT should map to SHIB token"


# ===================================================================
# AC3: kline_callback called with full bar on candle close only
# ===================================================================

class TestKlineCallbackOnClose:
    """AC3: kline_callback receives full OHLCV bar on candle close (x:true) only."""

    def test_kline_callback_called_on_close(self):
        """AC3: kline_callback(token, bar) called when k.x is true."""
        kline_bars = []

        def on_kline(token, bar):
            kline_bars.append((token, bar))

        monitor = PriceMonitor(
            callback=lambda *a: None,
            venue="perp",
            kline_callback=on_kline,
        )

        msg = _make_kline_message(
            "BTCUSDT",
            open_="65000.0", close="65100.0", high="65200.0", low="64900.0",
            volume="100.0", timestamp=1697380200000, is_closed=True,
        )
        monitor._handle_perp_message(msg)

        assert len(kline_bars) == 1, "kline_callback should fire on candle close"
        token, bar = kline_bars[0]
        assert token == "BTC"
        assert bar["timestamp"] == 1697380200000
        assert bar["open"] == pytest.approx(65000.0)
        assert bar["high"] == pytest.approx(65200.0)
        assert bar["low"] == pytest.approx(64900.0)
        assert bar["close"] == pytest.approx(65100.0)
        assert bar["volume"] == pytest.approx(100.0)

    def test_kline_callback_not_called_on_interim(self):
        """AC3: kline_callback is NOT called on non-closed updates (x:false)."""
        kline_bars = []

        def on_kline(token, bar):
            kline_bars.append((token, bar))

        monitor = PriceMonitor(
            callback=lambda *a: None,
            venue="perp",
            kline_callback=on_kline,
        )

        msg = _make_kline_message("BTCUSDT", is_closed=False)
        monitor._handle_perp_message(msg)

        assert len(kline_bars) == 0, "kline_callback should NOT fire on interim updates"

    def test_kline_callback_bar_has_all_fields_with_correct_types(self):
        """AC3: Bar dict has all required fields with correct types (timestamp int, OHLCV float)."""
        kline_bars = []

        monitor = PriceMonitor(
            callback=lambda *a: None,
            venue="perp",
            kline_callback=lambda t, b: kline_bars.append(b),
        )

        msg = _make_kline_message("BTCUSDT", is_closed=True)
        monitor._handle_perp_message(msg)

        assert len(kline_bars) == 1
        bar = kline_bars[0]
        required_keys = {"timestamp", "open", "high", "low", "close", "volume"}
        assert required_keys.issubset(bar.keys()), \
            f"Bar missing keys: {required_keys - bar.keys()}"
        # Type assertions — AC3 requires timestamp int, OHLCV float
        assert isinstance(bar["timestamp"], int), \
            f"timestamp should be int, got {type(bar['timestamp'])}"
        for field in ("open", "high", "low", "close", "volume"):
            assert isinstance(bar[field], float), \
                f"{field} should be float, got {type(bar[field])}"

    def test_both_callbacks_fire_on_close(self):
        """AC2+AC3: Both price callback AND kline_callback fire on candle close."""
        price_calls = []
        kline_calls = []

        monitor = PriceMonitor(
            callback=lambda t, p, ts: price_calls.append((t, p)),
            venue="perp",
            kline_callback=lambda t, b: kline_calls.append((t, b)),
        )

        msg = _make_kline_message("BTCUSDT", close="65100.0", is_closed=True)
        monitor._handle_perp_message(msg)

        assert len(price_calls) == 1, "Price callback should fire on close"
        assert len(kline_calls) == 1, "Kline callback should also fire on close"


# ===================================================================
# AC5: kline_callback parameter — None is safe
# ===================================================================

class TestKlineCallbackOptional:
    """AC5: PriceMonitor accepts kline_callback; None doesn't crash."""

    def test_kline_callback_none_no_crash_on_close(self):
        """AC5: kline_callback=None doesn't crash when a candle closes."""
        monitor = PriceMonitor(callback=lambda *a: None, venue="perp")
        # Default kline_callback should be None
        msg = _make_kline_message("BTCUSDT", is_closed=True)
        # Should not raise
        monitor._handle_perp_message(msg)

    def test_kline_callback_parameter_accepted(self):
        """AC5: PriceMonitor.__init__ accepts kline_callback parameter."""
        # Should not raise TypeError
        monitor = PriceMonitor(
            callback=lambda *a: None,
            venue="perp",
            kline_callback=lambda t, b: None,
        )
        assert monitor is not None

    def test_kline_callback_explicit_none(self):
        """AC5: Explicitly passing kline_callback=None is safe."""
        monitor = PriceMonitor(
            callback=lambda *a: None,
            venue="perp",
            kline_callback=None,
        )
        msg = _make_kline_message("BTCUSDT", is_closed=True)
        # Should not raise
        monitor._handle_perp_message(msg)


# ===================================================================
# AC16: update_subscriptions produces @kline_1m stream names
# ===================================================================

class TestUpdateSubscriptionsKline:
    """AC16: update_subscriptions sends @kline_1m stream names."""

    def test_update_subscriptions_sends_kline_streams(self):
        """AC16: update_subscriptions sends SUBSCRIBE with @kline_1m stream names."""
        monitor = PriceMonitor(callback=lambda *a: None, venue="perp")
        # Simulate a connected WS
        mock_ws = MagicMock()
        monitor._ws = mock_ws
        monitor._ws_connected = True
        monitor._subscribed_tokens = {"BTC"}

        # Subscribe to a new token
        monitor.update_subscriptions({"BTC", "ETH"})

        # Verify the subscribe message was sent
        assert mock_ws.send.called, "update_subscriptions should send WS message"
        sent_raw = mock_ws.send.call_args_list[0][0][0]
        sent_msg = json.loads(sent_raw)
        assert sent_msg["method"] == "SUBSCRIBE"
        # Stream names should be kline_1m
        params = sent_msg["params"]
        assert any("@kline_1m" in p for p in params), \
            f"Subscribe params should contain @kline_1m streams, got: {params}"
        assert all("markPrice" not in p for p in params), \
            f"Subscribe params should NOT contain markPrice: {params}"

    def test_unsubscribe_sends_kline_streams(self):
        """AC16: unsubscribe sends UNSUBSCRIBE with @kline_1m stream names."""
        monitor = PriceMonitor(callback=lambda *a: None, venue="perp")
        mock_ws = MagicMock()
        monitor._ws = mock_ws
        monitor._ws_connected = True
        monitor._subscribed_tokens = {"BTC", "ETH"}

        # Remove ETH
        monitor.update_subscriptions({"BTC"})

        # Find the UNSUBSCRIBE message
        unsub_found = False
        for c in mock_ws.send.call_args_list:
            msg = json.loads(c[0][0])
            if msg["method"] == "UNSUBSCRIBE":
                unsub_found = True
                assert any("@kline_1m" in p for p in msg["params"]), \
                    "Unsubscribe params should contain @kline_1m streams"
        assert unsub_found, "UNSUBSCRIBE message should have been sent"


# ===================================================================
# AC18: Spot venue unchanged — still uses @miniTicker
# ===================================================================

class TestSpotVenueUnchanged:
    """AC18: Spot venue still uses @miniTicker, not @kline_1m."""

    def test_spot_stream_names_still_miniticker(self):
        """AC18: Spot venue builds @miniTicker streams."""
        monitor = PriceMonitor(callback=lambda *a: None, venue="spot")
        streams = monitor._build_stream_names(["BTC", "ETH"])

        assert "btcusdt@miniTicker" in streams
        assert "ethusdt@miniTicker" in streams
        # No kline streams for spot
        for s in streams:
            assert "kline" not in s, f"Spot stream should not contain kline: {s}"

    def test_spot_handler_ignores_kline_messages(self):
        """AC18: Spot handler ignores kline event types."""
        received = []
        monitor = PriceMonitor(callback=lambda t, p, ts: received.append(t), venue="spot")

        msg = _make_kline_message("BTCUSDT", is_closed=True)
        monitor._handle_message(msg)

        assert len(received) == 0, "Spot handler should ignore kline messages"

    def test_spot_handler_still_processes_miniticker(self):
        """AC18: Spot handler still processes 24hrMiniTicker events correctly."""
        received = []

        def on_price(token, price, timestamp):
            received.append((token, price))

        monitor = PriceMonitor(callback=on_price, venue="spot")
        monitor._handle_message({
            "e": "24hrMiniTicker",
            "E": 1697380200000,
            "s": "BTCUSDT",
            "c": "65000.0",
        })

        assert len(received) == 1
        assert received[0][0] == "BTC"
        assert received[0][1] == pytest.approx(65000.0)
