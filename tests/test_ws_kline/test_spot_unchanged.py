"""Acceptance tests for Task 6: Regression — spot venue unchanged.

Tests verify:
  - AC19: Spot PriceMonitor stream names still use @miniTicker (not kline)
  - AC19: Spot handler still processes 24hrMiniTicker events correctly
  - AC19: Spot REST fallback unchanged

All tests MUST FAIL until implementation is done (RED phase) — but these
are primarily regression tests that verify spot behavior is preserved.
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

from v4.price_monitor import PriceMonitor


# ===================================================================
# AC19: Spot stream names use @miniTicker
# ===================================================================

class TestSpotStreamNamesRegression:
    """AC19: Spot PriceMonitor stream names still use @miniTicker."""

    def test_spot_uses_miniticker_not_kline(self):
        """AC19: Spot venue builds @miniTicker streams, not @kline_1m."""
        monitor = PriceMonitor(callback=lambda *a: None, venue="spot")
        streams = monitor._build_stream_names(["BTC", "ETH", "SOL"])

        for stream in streams:
            assert "@miniTicker" in stream, f"Spot stream should use @miniTicker: {stream}"
            assert "kline" not in stream, f"Spot stream should NOT contain kline: {stream}"

    def test_spot_stream_format(self):
        """AC19: Spot stream names follow {symbol}@miniTicker format."""
        monitor = PriceMonitor(callback=lambda *a: None, venue="spot")
        streams = monitor._build_stream_names(["BTC"])

        assert streams == ["btcusdt@miniTicker"], \
            f"Expected ['btcusdt@miniTicker'], got: {streams}"

    def test_spot_shib_no_1000_prefix(self):
        """AC19: Spot SHIB uses shibusdt, not 1000shibusdt."""
        monitor = PriceMonitor(callback=lambda *a: None, venue="spot")
        streams = monitor._build_stream_names(["SHIB"])

        assert "shibusdt@miniTicker" in streams
        for s in streams:
            assert "1000" not in s, f"Spot should not use 1000 prefix: {s}"


# ===================================================================
# AC19: Spot handler processes 24hrMiniTicker correctly
# ===================================================================

class TestSpotHandlerRegression:
    """AC19: Spot handler still processes 24hrMiniTicker events correctly."""

    def test_spot_handler_processes_miniticker(self):
        """AC19: Spot handler dispatches price callback on 24hrMiniTicker."""
        received = []

        def on_price(token, price, timestamp):
            received.append((token, price, timestamp))

        monitor = PriceMonitor(callback=on_price, venue="spot")
        monitor._handle_message({
            "e": "24hrMiniTicker",
            "E": 1697380200000,
            "s": "BTCUSDT",
            "c": "65000.0",
            "o": "64500.0",
            "h": "65500.0",
            "l": "64000.0",
            "v": "1234.56",
            "q": "80000000.00",
        })

        assert len(received) == 1
        assert received[0][0] == "BTC"
        assert received[0][1] == pytest.approx(65000.0)
        assert received[0][2] == 1697380200000

    def test_spot_handler_multiple_tokens(self):
        """AC19: Spot handler correctly processes updates from multiple tokens."""
        received = []

        def on_price(token, price, timestamp):
            received.append(token)

        monitor = PriceMonitor(callback=on_price, venue="spot")

        for token, price in [("BTC", "65000"), ("ETH", "3400"), ("SOL", "155")]:
            monitor._handle_message({
                "e": "24hrMiniTicker",
                "E": 1697380200000,
                "s": f"{token}USDT",
                "c": price,
            })

        assert received == ["BTC", "ETH", "SOL"]

    def test_spot_handler_ignores_bad_price(self):
        """AC19: Spot handler ignores zero/negative prices."""
        received = []
        monitor = PriceMonitor(callback=lambda t, p, ts: received.append(t), venue="spot")

        monitor._handle_message({
            "e": "24hrMiniTicker", "E": 1, "s": "BTCUSDT", "c": "0",
        })
        monitor._handle_message({
            "e": "24hrMiniTicker", "E": 1, "s": "BTCUSDT", "c": "-1",
        })

        assert len(received) == 0

    def test_spot_handler_updates_latest_prices(self):
        """AC19: Spot handler updates the latest_prices dict."""
        monitor = PriceMonitor(callback=lambda *a: None, venue="spot")
        monitor._handle_message({
            "e": "24hrMiniTicker", "E": 1, "s": "BTCUSDT", "c": "65000.0",
        })

        prices = monitor.get_latest_prices()
        assert "BTC" in prices
        assert prices["BTC"] == pytest.approx(65000.0)

    def test_spot_handler_ignores_perp_event_types(self):
        """AC19: Spot handler ignores markPriceUpdate and kline events."""
        received = []
        monitor = PriceMonitor(callback=lambda t, p, ts: received.append(t), venue="spot")

        # markPriceUpdate should be ignored
        monitor._handle_message({
            "e": "markPriceUpdate", "s": "BTCUSDT", "p": "65000.0", "E": 1,
        })
        # kline should be ignored
        monitor._handle_message({
            "e": "kline", "E": 1, "s": "BTCUSDT",
            "k": {"t": 1, "T": 2, "s": "BTCUSDT", "i": "1m",
                   "o": "65000", "c": "65100", "h": "65200", "l": "64900",
                   "v": "100", "n": 50, "x": True, "q": "6500000"},
        })

        assert len(received) == 0, "Spot handler should ignore perp/kline event types"


# ===================================================================
# AC19: Spot REST fallback unchanged
# ===================================================================

class TestSpotRestFallbackRegression:
    """AC19: Spot REST fallback still uses /api/v3/ticker/price."""

    def test_spot_rest_uses_ticker_price(self):
        """AC19: Spot _fetch_single_spot_rest_price uses /api/v3/ticker/price."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"symbol": "BTCUSDT", "price": "65000.0"}
        mock_resp.raise_for_status = MagicMock()

        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp

        monitor = PriceMonitor(callback=lambda *a: None, venue="spot")
        monitor._rest_session = mock_session

        result = monitor._fetch_single_spot_rest_price("BTC")

        assert mock_session.get.called
        call_args = mock_session.get.call_args
        url = call_args[0][0] if call_args[0] else ""

        assert "/ticker/price" in url, \
            f"Spot REST should use /ticker/price, got: {url}"
        assert "kline" not in url.lower(), \
            f"Spot REST should NOT use klines endpoint: {url}"

    def test_spot_rest_returns_correct_price(self):
        """AC19: Spot REST returns the price from the response."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"symbol": "BTCUSDT", "price": "65000.0"}
        mock_resp.raise_for_status = MagicMock()

        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp

        monitor = PriceMonitor(callback=lambda *a: None, venue="spot")
        monitor._rest_session = mock_session

        result = monitor._fetch_single_spot_rest_price("BTC")

        assert result == pytest.approx(65000.0)

    def test_spot_fetch_single_routes_to_spot(self):
        """AC19: fetch_single_rest_price on spot venue routes to spot REST."""
        monitor = PriceMonitor(callback=lambda *a: None, venue="spot")
        called = []
        monitor._fetch_single_spot_rest_price = lambda t: called.append(t) or 65000.0
        result = monitor.fetch_single_rest_price("BTC")
        assert called == ["BTC"]
