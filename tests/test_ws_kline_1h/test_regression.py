"""Regression tests for Task 6 (AC19): Verify existing PriceMonitor behavior is preserved.

These tests document the CURRENT behavior before ws-kline-1h-feed changes.
They serve as a safety net: if any test here breaks after implementation,
existing functionality has regressed.

Tests verify:
  1. PriceMonitor with NO new params behaves identically to before
  2. _build_perp_stream_names returns @kline_1m only (no @kline_1h)
  3. _build_spot_stream_names returns @miniTicker only
  4. _handle_perp_message with 1m kline dispatches price + kline callbacks as before
  5. _handle_spot_message with miniTicker dispatches price callback as before
  6. No 1h routing when kline_1h_callback is None (or absent)
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

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
            "n": 50,
            "x": is_closed,
            "q": "6500000.0",
        },
    }


def _make_miniticker_message(
    symbol: str,
    close: str = "65000.0",
    timestamp: int = 1697380200000,
) -> dict:
    """Build a mock Binance spot 24hrMiniTicker WebSocket message."""
    return {
        "e": "24hrMiniTicker",
        "E": timestamp,
        "s": symbol,
        "c": close,
        "o": "64500.00",
        "h": "65500.00",
        "l": "64000.00",
        "v": "1234.56",
        "q": "80000000.00",
    }


# ===================================================================
# 1. PriceMonitor with NO new params — identical behavior to before
# ===================================================================

class TestDefaultConstructorRegression:
    """PriceMonitor created with only existing params behaves as before."""

    def test_default_init_only_callback(self):
        """PriceMonitor(callback=...) succeeds with only callback arg."""
        monitor = PriceMonitor(callback=lambda *a: None)
        assert monitor._venue == "perp"
        assert monitor.rest_poll_interval_s == 10
        assert monitor.kline_callback is None

    def test_default_init_with_venue_perp(self):
        """PriceMonitor(callback=..., venue='perp') is the default perp behavior."""
        monitor = PriceMonitor(callback=lambda *a: None, venue="perp")
        assert monitor._venue == "perp"
        assert monitor.kline_callback is None

    def test_default_init_with_venue_spot(self):
        """PriceMonitor(callback=..., venue='spot') stores spot venue."""
        monitor = PriceMonitor(callback=lambda *a: None, venue="spot")
        assert monitor._venue == "spot"

    def test_default_init_with_kline_callback(self):
        """PriceMonitor with kline_callback stores it correctly."""
        cb = MagicMock()
        monitor = PriceMonitor(callback=lambda *a: None, kline_callback=cb)
        assert monitor.kline_callback is cb

    def test_default_init_kline_callback_none(self):
        """PriceMonitor without kline_callback defaults to None."""
        monitor = PriceMonitor(callback=lambda *a: None)
        assert monitor.kline_callback is None

    def test_initial_state_ws_not_connected(self):
        """Newly created PriceMonitor is not connected."""
        monitor = PriceMonitor(callback=lambda *a: None)
        assert monitor._ws_connected is False
        assert monitor._reconnect_attempt == 0
        assert monitor._subscribed_tokens == set()
        assert monitor._latest_prices == {}


# ===================================================================
# 2. _build_perp_stream_names returns @kline_1m only (no @kline_1h)
# ===================================================================

class TestPerpStreamNamesRegression:
    """Perp stream names use @kline_1m, with no @kline_1h streams."""

    def test_perp_streams_single_token(self):
        """Single token produces exactly one @kline_1m stream."""
        monitor = PriceMonitor(callback=lambda *a: None, venue="perp")
        streams = monitor._build_perp_stream_names(["BTC"])
        assert streams == ["btcusdt@kline_1m"]

    def test_perp_streams_multiple_tokens(self):
        """Multiple tokens each get one @kline_1m stream."""
        monitor = PriceMonitor(callback=lambda *a: None, venue="perp")
        streams = monitor._build_perp_stream_names(["BTC", "ETH", "SOL"])
        assert len(streams) == 3
        assert "btcusdt@kline_1m" in streams
        assert "ethusdt@kline_1m" in streams
        assert "solusdt@kline_1m" in streams

    def test_perp_streams_no_kline_1h(self):
        """Default perp streams contain NO @kline_1h entries."""
        monitor = PriceMonitor(callback=lambda *a: None, venue="perp")
        streams = monitor._build_perp_stream_names(["BTC", "ETH", "SOL"])
        for s in streams:
            assert "@kline_1h" not in s, f"Default perp should not have @kline_1h: {s}"

    def test_perp_streams_empty(self):
        """Empty token list returns empty stream list."""
        monitor = PriceMonitor(callback=lambda *a: None, venue="perp")
        assert monitor._build_perp_stream_names([]) == []

    def test_perp_streams_1000_prefix_token(self):
        """1000-prefix tokens produce correct @kline_1m streams."""
        monitor = PriceMonitor(callback=lambda *a: None, venue="perp")
        streams = monitor._build_perp_stream_names(["SHIB"])
        assert streams == ["1000shibusdt@kline_1m"]

    def test_perp_streams_count_matches_tokens(self):
        """Number of streams equals number of tokens (1:1 mapping, only 1m)."""
        monitor = PriceMonitor(callback=lambda *a: None, venue="perp")
        tokens = ["BTC", "ETH", "SOL", "DOGE", "SHIB"]
        streams = monitor._build_perp_stream_names(tokens)
        assert len(streams) == len(tokens), (
            f"Expected {len(tokens)} streams (one @kline_1m each), got {len(streams)}"
        )


# ===================================================================
# 3. _build_spot_stream_names returns @miniTicker only
# ===================================================================

class TestSpotStreamNamesRegression:
    """Spot stream names use @miniTicker, no @kline_1m or @kline_1h."""

    def test_spot_streams_single_token(self):
        """Single token produces exactly one @miniTicker stream."""
        monitor = PriceMonitor(callback=lambda *a: None, venue="spot")
        streams = monitor._build_spot_stream_names(["BTC"])
        assert streams == ["btcusdt@miniTicker"]

    def test_spot_streams_multiple_tokens(self):
        """Multiple tokens each get one @miniTicker stream."""
        monitor = PriceMonitor(callback=lambda *a: None, venue="spot")
        streams = monitor._build_spot_stream_names(["BTC", "ETH"])
        assert len(streams) == 2
        assert "btcusdt@miniTicker" in streams
        assert "ethusdt@miniTicker" in streams

    def test_spot_streams_no_kline(self):
        """Spot streams contain no kline entries at all."""
        monitor = PriceMonitor(callback=lambda *a: None, venue="spot")
        streams = monitor._build_spot_stream_names(["BTC", "ETH", "SOL"])
        for s in streams:
            assert "kline" not in s, f"Spot streams should not contain kline: {s}"

    def test_spot_streams_empty(self):
        """Empty token list returns empty stream list."""
        monitor = PriceMonitor(callback=lambda *a: None, venue="spot")
        assert monitor._build_spot_stream_names([]) == []

    def test_spot_no_1000_prefix(self):
        """Spot SHIB maps to shibusdt, not 1000shibusdt."""
        monitor = PriceMonitor(callback=lambda *a: None, venue="spot")
        streams = monitor._build_spot_stream_names(["SHIB"])
        assert streams == ["shibusdt@miniTicker"]


# ===================================================================
# 4. _handle_perp_message dispatches price + kline callbacks as before
# ===================================================================

class TestHandlePerpMessageRegression:
    """_handle_perp_message dispatches callbacks exactly as current code does."""

    def test_price_callback_on_every_kline_update(self):
        """Price callback fires on both interim (x:false) and close (x:true) updates."""
        received = []
        monitor = PriceMonitor(
            callback=lambda t, p, ts: received.append((t, p, ts)),
            venue="perp",
        )

        # Interim update
        msg1 = _make_kline_message("BTCUSDT", close="65050.0", is_closed=False)
        monitor._handle_perp_message(msg1)
        assert len(received) == 1
        assert received[0][0] == "BTC"
        assert received[0][1] == pytest.approx(65050.0)

        # Close update
        msg2 = _make_kline_message("BTCUSDT", close="65100.0", is_closed=True)
        monitor._handle_perp_message(msg2)
        assert len(received) == 2
        assert received[1][1] == pytest.approx(65100.0)

    def test_kline_callback_only_on_close(self):
        """kline_callback fires ONLY on candle close (x:true), not on interim."""
        kline_bars = []
        monitor = PriceMonitor(
            callback=lambda *a: None,
            venue="perp",
            kline_callback=lambda t, b: kline_bars.append((t, b)),
        )

        # Interim: no kline_callback
        monitor._handle_perp_message(
            _make_kline_message("BTCUSDT", close="65050.0", is_closed=False)
        )
        assert len(kline_bars) == 0

        # Close: kline_callback fires
        monitor._handle_perp_message(
            _make_kline_message("BTCUSDT", close="65100.0", is_closed=True)
        )
        assert len(kline_bars) == 1
        assert kline_bars[0][0] == "BTC"
        assert kline_bars[0][1]["close"] == pytest.approx(65100.0)

    def test_kline_callback_bar_fields(self):
        """Bar dict from kline_callback has timestamp, open, high, low, close, volume."""
        kline_bars = []
        monitor = PriceMonitor(
            callback=lambda *a: None,
            venue="perp",
            kline_callback=lambda t, b: kline_bars.append(b),
        )

        monitor._handle_perp_message(_make_kline_message(
            "BTCUSDT",
            open_="65000.0", close="65100.0", high="65200.0",
            low="64900.0", volume="100.0", timestamp=1697380200000,
            is_closed=True,
        ))

        assert len(kline_bars) == 1
        bar = kline_bars[0]
        assert bar["timestamp"] == 1697380200000
        assert bar["open"] == pytest.approx(65000.0)
        assert bar["high"] == pytest.approx(65200.0)
        assert bar["low"] == pytest.approx(64900.0)
        assert bar["close"] == pytest.approx(65100.0)
        assert bar["volume"] == pytest.approx(100.0)

    def test_perp_ignores_non_kline_events(self):
        """_handle_perp_message ignores non-kline event types."""
        received = []
        monitor = PriceMonitor(
            callback=lambda t, p, ts: received.append(t),
            venue="perp",
        )
        monitor._handle_perp_message({"e": "markPriceUpdate", "s": "BTCUSDT", "p": "65000.0", "E": 1})
        monitor._handle_perp_message({"e": "24hrMiniTicker", "s": "BTCUSDT", "c": "65000.0", "E": 1})
        assert len(received) == 0

    def test_perp_updates_latest_prices(self):
        """_handle_perp_message updates internal _latest_prices dict."""
        monitor = PriceMonitor(callback=lambda *a: None, venue="perp")
        monitor._handle_perp_message(
            _make_kline_message("BTCUSDT", close="65100.0")
        )
        prices = monitor.get_latest_prices()
        assert "BTC" in prices
        assert prices["BTC"] == pytest.approx(65100.0)

    def test_perp_ignores_zero_price(self):
        """_handle_perp_message silently ignores zero close price."""
        received = []
        monitor = PriceMonitor(
            callback=lambda t, p, ts: received.append(t),
            venue="perp",
        )
        monitor._handle_perp_message(
            _make_kline_message("BTCUSDT", close="0")
        )
        assert len(received) == 0

    def test_perp_ignores_negative_price(self):
        """_handle_perp_message silently ignores negative close price."""
        received = []
        monitor = PriceMonitor(
            callback=lambda t, p, ts: received.append(t),
            venue="perp",
        )
        monitor._handle_perp_message(
            _make_kline_message("BTCUSDT", close="-100.0")
        )
        assert len(received) == 0

    def test_1000_prefix_symbol_mapping(self):
        """1000-prefix symbols (e.g. 1000SHIBUSDT) map to correct token."""
        received = []
        monitor = PriceMonitor(
            callback=lambda t, p, ts: received.append(t),
            venue="perp",
        )
        monitor._handle_perp_message(
            _make_kline_message("1000SHIBUSDT", close="0.00002345")
        )
        assert received == ["SHIB"]


# ===================================================================
# 5. _handle_spot_message dispatches price callback as before
# ===================================================================

class TestHandleSpotMessageRegression:
    """_handle_spot_message dispatches price callback on miniTicker as before."""

    def test_spot_price_callback_on_miniticker(self):
        """Spot price callback fires on 24hrMiniTicker with correct args."""
        received = []
        monitor = PriceMonitor(
            callback=lambda t, p, ts: received.append((t, p, ts)),
            venue="spot",
        )
        monitor._handle_spot_message(
            _make_miniticker_message("BTCUSDT", close="65000.0", timestamp=1697380200000)
        )

        assert len(received) == 1
        assert received[0][0] == "BTC"
        assert received[0][1] == pytest.approx(65000.0)
        assert received[0][2] == 1697380200000

    def test_spot_multiple_tokens(self):
        """Spot price callback fires independently for each token."""
        received = []
        monitor = PriceMonitor(
            callback=lambda t, p, ts: received.append(t),
            venue="spot",
        )
        monitor._handle_spot_message(_make_miniticker_message("BTCUSDT", close="65000"))
        monitor._handle_spot_message(_make_miniticker_message("ETHUSDT", close="3400"))
        monitor._handle_spot_message(_make_miniticker_message("SOLUSDT", close="155"))
        assert received == ["BTC", "ETH", "SOL"]

    def test_spot_ignores_non_miniticker(self):
        """Spot handler ignores non-24hrMiniTicker event types."""
        received = []
        monitor = PriceMonitor(
            callback=lambda t, p, ts: received.append(t),
            venue="spot",
        )
        monitor._handle_spot_message({"e": "kline", "s": "BTCUSDT", "E": 1, "k": {"c": "65000.0"}})
        monitor._handle_spot_message({"e": "markPriceUpdate", "s": "BTCUSDT", "p": "65000.0", "E": 1})
        assert len(received) == 0

    def test_spot_ignores_zero_price(self):
        """Spot handler ignores zero price."""
        received = []
        monitor = PriceMonitor(
            callback=lambda t, p, ts: received.append(t),
            venue="spot",
        )
        monitor._handle_spot_message(_make_miniticker_message("BTCUSDT", close="0"))
        assert len(received) == 0

    def test_spot_updates_latest_prices(self):
        """Spot handler updates internal _latest_prices dict."""
        monitor = PriceMonitor(callback=lambda *a: None, venue="spot")
        monitor._handle_spot_message(
            _make_miniticker_message("ETHUSDT", close="3400.5")
        )
        prices = monitor.get_latest_prices()
        assert "ETH" in prices
        assert prices["ETH"] == pytest.approx(3400.5)

    def test_spot_symbol_to_token_mapping(self):
        """Spot symbol SHIBUSDT maps to token SHIB (no 1000-prefix on spot)."""
        received = []
        monitor = PriceMonitor(
            callback=lambda t, p, ts: received.append(t),
            venue="spot",
        )
        monitor._handle_spot_message(
            _make_miniticker_message("SHIBUSDT", close="0.00002345")
        )
        assert received == ["SHIB"]


# ===================================================================
# 6. No 1h routing when kline_1h_callback is None (or absent)
# ===================================================================

class TestNoKline1hRoutingRegression:
    """No 1h kline routing occurs with default PriceMonitor construction."""

    def test_no_kline_callback_on_close_when_none(self):
        """When kline_callback is None, candle close does NOT call any kline handler."""
        received = []
        monitor = PriceMonitor(
            callback=lambda t, p, ts: received.append(("price", t, p)),
            venue="perp",
            kline_callback=None,
        )

        msg = _make_kline_message("BTCUSDT", close="65100.0", is_closed=True)
        monitor._handle_perp_message(msg)

        # Price callback fires (1 call), but no kline callback
        assert len(received) == 1
        assert received[0][0] == "price"

    def test_handle_message_routing_perp(self):
        """_handle_message routes to _handle_perp_message for perp venue."""
        received = []
        monitor = PriceMonitor(
            callback=lambda t, p, ts: received.append(t),
            venue="perp",
        )
        msg = _make_kline_message("BTCUSDT", close="65100.0")
        monitor._handle_message(msg)
        assert received == ["BTC"]

    def test_handle_message_routing_spot(self):
        """_handle_message routes to _handle_spot_message for spot venue."""
        received = []
        monitor = PriceMonitor(
            callback=lambda t, p, ts: received.append(t),
            venue="spot",
        )
        msg = _make_miniticker_message("BTCUSDT", close="65000.0")
        monitor._handle_message(msg)
        assert received == ["BTC"]

    def test_perp_does_not_process_spot_messages(self):
        """Perp venue _handle_message ignores miniTicker messages."""
        received = []
        monitor = PriceMonitor(
            callback=lambda t, p, ts: received.append(t),
            venue="perp",
        )
        monitor._handle_message(
            _make_miniticker_message("BTCUSDT", close="65000.0")
        )
        assert len(received) == 0

    def test_spot_does_not_process_kline_messages(self):
        """Spot venue _handle_message ignores kline messages."""
        received = []
        monitor = PriceMonitor(
            callback=lambda t, p, ts: received.append(t),
            venue="spot",
        )
        monitor._handle_message(
            _make_kline_message("BTCUSDT", close="65100.0")
        )
        assert len(received) == 0

    def test_kline_callback_exception_does_not_crash(self):
        """kline_callback that raises does not crash _handle_perp_message."""
        def bad_kline_cb(token, bar):
            raise RuntimeError("kline_callback error")

        price_calls = []
        monitor = PriceMonitor(
            callback=lambda t, p, ts: price_calls.append(t),
            venue="perp",
            kline_callback=bad_kline_cb,
        )

        # Close message triggers the bad kline_callback, but price callback
        # already fired before kline_callback, so price_calls has 1 entry
        msg = _make_kline_message("BTCUSDT", close="65100.0", is_closed=True)
        # Should not raise -- defensive try/except in _handle_perp_message
        monitor._handle_perp_message(msg)
        assert len(price_calls) == 1

    def test_build_stream_names_routes_by_venue(self):
        """_build_stream_names dispatches to venue-specific builder."""
        perp_monitor = PriceMonitor(callback=lambda *a: None, venue="perp")
        spot_monitor = PriceMonitor(callback=lambda *a: None, venue="spot")

        perp_streams = perp_monitor._build_stream_names(["BTC"])
        spot_streams = spot_monitor._build_stream_names(["BTC"])

        assert perp_streams == ["btcusdt@kline_1m"]
        assert spot_streams == ["btcusdt@miniTicker"]
