"""Acceptance tests for Task 1: PriceMonitor 1h kline stream support.

Tests verify:
  - AC1:  __init__() accepts kline_1h_callback and streams parameters.
          Default streams=None preserves existing behavior.
  - AC2:  _build_perp_stream_names() with streams=["kline_1h"] returns
          ["btcusdt@kline_1h", ...].  Default returns ["btcusdt@kline_1m", ...].
  - AC3:  _build_spot_stream_names() with streams=["kline_1h"] returns
          ["btcusdt@kline_1h", ...].  Default returns ["btcusdt@miniTicker", ...].
  - AC4:  _handle_perp_message() routes by k.i: "1m" -> kline_callback,
          "1h" -> kline_1h_callback (only on close).
          Price callback on every 1m update (unchanged).
  - AC5:  _handle_spot_message() routes kline 1h to kline_1h_callback on close.
          miniTicker messages still dispatch price callback.
  - AC6:  update_subscriptions() works with configurable streams.
  - AC17: Spot miniTicker behavior unchanged.
  - AC18: Perp kline_1m behavior unchanged.

All tests MUST FAIL until implementation is done (RED phase).
"""
from __future__ import annotations

import json
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


def _make_spot_miniticker_message(
    symbol: str,
    close: str = "65000.0",
    timestamp: int = 1697380200000,
) -> dict:
    """Build a mock Binance Spot 24hrMiniTicker message."""
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
# AC1: __init__ accepts kline_1h_callback and streams parameters
# ===================================================================

class TestInitAcceptsNewParams:
    """AC1: PriceMonitor.__init__() accepts kline_1h_callback and streams."""

    def test_init_accepts_kline_1h_callback(self):
        """AC1: PriceMonitor accepts kline_1h_callback parameter."""
        calls = []
        monitor = PriceMonitor(
            callback=lambda *a: None,
            venue="perp",
            kline_1h_callback=lambda t, b: calls.append((t, b)),
        )
        assert monitor.kline_1h_callback is not None

    def test_init_accepts_streams_parameter(self):
        """AC1: PriceMonitor accepts streams parameter."""
        monitor = PriceMonitor(
            callback=lambda *a: None,
            venue="perp",
            streams=["kline_1h"],
        )
        # The streams parameter should be stored
        assert hasattr(monitor, "_streams") or hasattr(monitor, "streams")

    def test_init_kline_1h_callback_default_none(self):
        """AC1: kline_1h_callback defaults to None."""
        monitor = PriceMonitor(callback=lambda *a: None, venue="perp")
        assert monitor.kline_1h_callback is None

    def test_init_streams_default_none(self):
        """AC1: streams defaults to None, preserving existing behavior."""
        monitor = PriceMonitor(callback=lambda *a: None, venue="perp")
        # With default streams=None, perp should still build kline_1m streams
        streams = monitor._build_perp_stream_names(["BTC"])
        assert "btcusdt@kline_1m" in streams

    def test_init_both_new_params_together(self):
        """AC1: Both kline_1h_callback and streams can be passed together."""
        monitor = PriceMonitor(
            callback=lambda *a: None,
            venue="perp",
            kline_1h_callback=lambda t, b: None,
            streams=["kline_1h"],
        )
        assert monitor.kline_1h_callback is not None


# ===================================================================
# AC2: _build_perp_stream_names with streams parameter
# ===================================================================

class TestBuildPerpStreamNames1h:
    """AC2: _build_perp_stream_names with streams=["kline_1h"]."""

    def test_perp_streams_kline_1h(self):
        """AC2: streams=["kline_1h"] returns @kline_1h stream names."""
        monitor = PriceMonitor(
            callback=lambda *a: None,
            venue="perp",
            streams=["kline_1h"],
        )
        names = monitor._build_perp_stream_names(["BTC", "ETH"])
        assert "btcusdt@kline_1h" in names
        assert "ethusdt@kline_1h" in names

    def test_perp_streams_kline_1h_no_kline_1m(self):
        """AC2: streams=["kline_1h"] does NOT include @kline_1m."""
        monitor = PriceMonitor(
            callback=lambda *a: None,
            venue="perp",
            streams=["kline_1h"],
        )
        names = monitor._build_perp_stream_names(["BTC"])
        assert all("@kline_1m" not in n for n in names)

    def test_perp_streams_both_1m_and_1h(self):
        """AC2: streams=["kline_1m", "kline_1h"] returns both stream types."""
        monitor = PriceMonitor(
            callback=lambda *a: None,
            venue="perp",
            streams=["kline_1m", "kline_1h"],
        )
        names = monitor._build_perp_stream_names(["BTC"])
        assert "btcusdt@kline_1m" in names
        assert "btcusdt@kline_1h" in names

    def test_perp_streams_default_returns_kline_1m(self):
        """AC2: Default streams=None returns @kline_1m (backward compatible)."""
        monitor = PriceMonitor(callback=lambda *a: None, venue="perp")
        names = monitor._build_perp_stream_names(["BTC", "ETH"])
        assert "btcusdt@kline_1m" in names
        assert "ethusdt@kline_1m" in names

    def test_perp_streams_kline_1h_with_1000_prefix_token(self):
        """AC2: 1000-prefix tokens map correctly with kline_1h."""
        monitor = PriceMonitor(
            callback=lambda *a: None,
            venue="perp",
            streams=["kline_1h"],
        )
        names = monitor._build_perp_stream_names(["SHIB"])
        assert "1000shibusdt@kline_1h" in names

    def test_perp_streams_empty_tokens(self):
        """AC2: Empty token list returns empty regardless of streams."""
        monitor = PriceMonitor(
            callback=lambda *a: None,
            venue="perp",
            streams=["kline_1h"],
        )
        names = monitor._build_perp_stream_names([])
        assert names == []


# ===================================================================
# AC3: _build_spot_stream_names with streams parameter
# ===================================================================

class TestBuildSpotStreamNames1h:
    """AC3: _build_spot_stream_names with streams=["kline_1h"]."""

    def test_spot_streams_kline_1h(self):
        """AC3: streams=["kline_1h"] returns @kline_1h for spot."""
        monitor = PriceMonitor(
            callback=lambda *a: None,
            venue="spot",
            streams=["kline_1h"],
        )
        names = monitor._build_spot_stream_names(["BTC", "ETH"])
        assert "btcusdt@kline_1h" in names
        assert "ethusdt@kline_1h" in names

    def test_spot_streams_kline_1h_no_miniticker(self):
        """AC3: streams=["kline_1h"] does NOT include @miniTicker."""
        monitor = PriceMonitor(
            callback=lambda *a: None,
            venue="spot",
            streams=["kline_1h"],
        )
        names = monitor._build_spot_stream_names(["BTC"])
        assert all("@miniTicker" not in n for n in names)

    def test_spot_streams_default_returns_miniticker(self):
        """AC3: Default streams=None returns @miniTicker (backward compatible)."""
        monitor = PriceMonitor(callback=lambda *a: None, venue="spot")
        names = monitor._build_spot_stream_names(["BTC", "ETH"])
        assert "btcusdt@miniTicker" in names
        assert "ethusdt@miniTicker" in names

    def test_spot_streams_both_miniticker_and_kline_1h(self):
        """AC3: streams=["miniTicker", "kline_1h"] returns both types."""
        monitor = PriceMonitor(
            callback=lambda *a: None,
            venue="spot",
            streams=["miniTicker", "kline_1h"],
        )
        names = monitor._build_spot_stream_names(["BTC"])
        assert "btcusdt@miniTicker" in names
        assert "btcusdt@kline_1h" in names

    def test_spot_streams_kline_1h_no_1000_prefix(self):
        """AC3: Spot kline_1h streams do NOT use 1000-prefix for SHIB."""
        monitor = PriceMonitor(
            callback=lambda *a: None,
            venue="spot",
            streams=["kline_1h"],
        )
        names = monitor._build_spot_stream_names(["SHIB"])
        assert "shibusdt@kline_1h" in names
        assert all("1000" not in n for n in names)

    def test_spot_streams_empty_tokens(self):
        """AC3: Empty token list returns empty regardless of streams."""
        monitor = PriceMonitor(
            callback=lambda *a: None,
            venue="spot",
            streams=["kline_1h"],
        )
        names = monitor._build_spot_stream_names([])
        assert names == []


# ===================================================================
# AC4: _handle_perp_message routes by k.i interval
# ===================================================================

class TestPerpMessageRouting1h:
    """AC4: _handle_perp_message routes by k.i: 1m -> kline_callback, 1h -> kline_1h_callback."""

    def test_1m_close_fires_kline_callback(self):
        """AC4: 1m candle close routes to kline_callback (not kline_1h_callback)."""
        kline_calls = []
        kline_1h_calls = []

        monitor = PriceMonitor(
            callback=lambda *a: None,
            venue="perp",
            kline_callback=lambda t, b: kline_calls.append((t, b)),
            kline_1h_callback=lambda t, b: kline_1h_calls.append((t, b)),
            streams=["kline_1m", "kline_1h"],
        )

        msg = _make_kline_message("BTCUSDT", close="65100.0", is_closed=True, interval="1m")
        monitor._handle_perp_message(msg)

        assert len(kline_calls) == 1, "kline_callback should fire on 1m close"
        assert len(kline_1h_calls) == 0, "kline_1h_callback should NOT fire on 1m close"

    def test_1h_close_fires_kline_1h_callback(self):
        """AC4: 1h candle close routes to kline_1h_callback (not kline_callback)."""
        kline_calls = []
        kline_1h_calls = []

        monitor = PriceMonitor(
            callback=lambda *a: None,
            venue="perp",
            kline_callback=lambda t, b: kline_calls.append((t, b)),
            kline_1h_callback=lambda t, b: kline_1h_calls.append((t, b)),
            streams=["kline_1m", "kline_1h"],
        )

        msg = _make_kline_message("BTCUSDT", close="65100.0", is_closed=True, interval="1h")
        monitor._handle_perp_message(msg)

        assert len(kline_1h_calls) == 1, "kline_1h_callback should fire on 1h close"
        assert len(kline_calls) == 0, "kline_callback should NOT fire on 1h close"

    def test_1h_interim_does_not_fire_kline_1h_callback(self):
        """AC4: 1h interim updates (x:false) do NOT fire kline_1h_callback."""
        kline_1h_calls = []

        monitor = PriceMonitor(
            callback=lambda *a: None,
            venue="perp",
            kline_1h_callback=lambda t, b: kline_1h_calls.append((t, b)),
            streams=["kline_1m", "kline_1h"],
        )

        msg = _make_kline_message("BTCUSDT", close="65100.0", is_closed=False, interval="1h")
        monitor._handle_perp_message(msg)

        assert len(kline_1h_calls) == 0, "kline_1h_callback should NOT fire on 1h interim"

    def test_1m_price_callback_fires_every_update(self):
        """AC4: Price callback fires on every 1m update (unchanged behavior)."""
        price_calls = []

        monitor = PriceMonitor(
            callback=lambda t, p, ts: price_calls.append((t, p)),
            venue="perp",
            kline_1h_callback=lambda t, b: None,
            streams=["kline_1m", "kline_1h"],
        )

        # 1m interim
        monitor._handle_perp_message(
            _make_kline_message("BTCUSDT", close="65050.0", is_closed=False, interval="1m")
        )
        # 1m close
        monitor._handle_perp_message(
            _make_kline_message("BTCUSDT", close="65100.0", is_closed=True, interval="1m")
        )

        assert len(price_calls) == 2, "Price callback should fire on every 1m update"

    def test_1h_does_not_fire_price_callback(self):
        """AC4: 1h kline messages do NOT fire the price callback.

        Price callback only fires for 1m updates (the real-time price feed).
        1h klines are a separate data channel for hourly OHLCV bars.
        """
        price_calls = []

        monitor = PriceMonitor(
            callback=lambda t, p, ts: price_calls.append((t, p)),
            venue="perp",
            kline_1h_callback=lambda t, b: None,
            streams=["kline_1m", "kline_1h"],
        )

        msg = _make_kline_message("BTCUSDT", close="65100.0", is_closed=True, interval="1h")
        monitor._handle_perp_message(msg)

        assert len(price_calls) == 0, "Price callback should NOT fire on 1h kline"

    def test_1h_close_bar_has_correct_ohlcv(self):
        """AC4: kline_1h_callback receives full OHLCV bar on 1h close."""
        kline_1h_calls = []

        monitor = PriceMonitor(
            callback=lambda *a: None,
            venue="perp",
            kline_1h_callback=lambda t, b: kline_1h_calls.append((t, b)),
            streams=["kline_1m", "kline_1h"],
        )

        msg = _make_kline_message(
            "BTCUSDT",
            open_="64000.0", close="65100.0", high="65500.0", low="63900.0",
            volume="5000.0", timestamp=1697378400000, is_closed=True, interval="1h",
        )
        monitor._handle_perp_message(msg)

        assert len(kline_1h_calls) == 1
        token, bar = kline_1h_calls[0]
        assert token == "BTC"
        assert bar["timestamp"] == 1697378400000
        assert bar["open"] == pytest.approx(64000.0)
        assert bar["high"] == pytest.approx(65500.0)
        assert bar["low"] == pytest.approx(63900.0)
        assert bar["close"] == pytest.approx(65100.0)
        assert bar["volume"] == pytest.approx(5000.0)

    def test_1h_close_with_1000_prefix_token(self):
        """AC4: 1h close for 1000-prefix tokens maps to correct internal token."""
        kline_1h_calls = []

        monitor = PriceMonitor(
            callback=lambda *a: None,
            venue="perp",
            kline_1h_callback=lambda t, b: kline_1h_calls.append((t, b)),
            streams=["kline_1m", "kline_1h"],
        )

        msg = _make_kline_message(
            "1000SHIBUSDT", close="0.00002345", is_closed=True, interval="1h",
        )
        monitor._handle_perp_message(msg)

        assert len(kline_1h_calls) == 1
        assert kline_1h_calls[0][0] == "SHIB"

    def test_kline_1h_callback_none_no_crash(self):
        """AC4: kline_1h_callback=None does not crash on 1h close."""
        monitor = PriceMonitor(
            callback=lambda *a: None,
            venue="perp",
            streams=["kline_1m", "kline_1h"],
        )
        # kline_1h_callback defaults to None
        msg = _make_kline_message("BTCUSDT", is_closed=True, interval="1h")
        # Should not raise
        monitor._handle_perp_message(msg)


# ===================================================================
# AC5: _handle_spot_message routes kline 1h
# ===================================================================

class TestSpotMessageRouting1h:
    """AC5: _handle_spot_message routes kline 1h to kline_1h_callback."""

    def test_spot_kline_1h_close_fires_kline_1h_callback(self):
        """AC5: Spot kline 1h close routes to kline_1h_callback."""
        kline_1h_calls = []

        monitor = PriceMonitor(
            callback=lambda *a: None,
            venue="spot",
            kline_1h_callback=lambda t, b: kline_1h_calls.append((t, b)),
            streams=["kline_1h"],
        )

        msg = _make_kline_message(
            "BTCUSDT",
            open_="64000.0", close="65100.0", high="65500.0", low="63900.0",
            volume="5000.0", timestamp=1697378400000, is_closed=True, interval="1h",
        )
        monitor._handle_spot_message(msg)

        assert len(kline_1h_calls) == 1
        token, bar = kline_1h_calls[0]
        assert token == "BTC"
        assert bar["close"] == pytest.approx(65100.0)

    def test_spot_kline_1h_interim_does_not_fire_callback(self):
        """AC5: Spot kline 1h interim (x:false) does NOT fire kline_1h_callback."""
        kline_1h_calls = []

        monitor = PriceMonitor(
            callback=lambda *a: None,
            venue="spot",
            kline_1h_callback=lambda t, b: kline_1h_calls.append((t, b)),
            streams=["kline_1h"],
        )

        msg = _make_kline_message("BTCUSDT", is_closed=False, interval="1h")
        monitor._handle_spot_message(msg)

        assert len(kline_1h_calls) == 0

    def test_spot_miniticker_still_dispatches_price(self):
        """AC5/AC17: miniTicker messages still dispatch price callback on spot."""
        price_calls = []

        monitor = PriceMonitor(
            callback=lambda t, p, ts: price_calls.append((t, p)),
            venue="spot",
            kline_1h_callback=lambda t, b: None,
            streams=["miniTicker", "kline_1h"],
        )

        msg = _make_spot_miniticker_message("BTCUSDT", close="65000.0")
        monitor._handle_spot_message(msg)

        assert len(price_calls) == 1
        assert price_calls[0][0] == "BTC"
        assert price_calls[0][1] == pytest.approx(65000.0)

    def test_spot_kline_1h_does_not_fire_price_callback(self):
        """AC5: Spot kline 1h close does NOT fire the price callback."""
        price_calls = []

        monitor = PriceMonitor(
            callback=lambda t, p, ts: price_calls.append((t, p)),
            venue="spot",
            kline_1h_callback=lambda t, b: None,
            streams=["miniTicker", "kline_1h"],
        )

        msg = _make_kline_message("BTCUSDT", is_closed=True, interval="1h")
        monitor._handle_spot_message(msg)

        assert len(price_calls) == 0, "Spot price callback should NOT fire on 1h kline"

    def test_spot_kline_1h_callback_none_no_crash(self):
        """AC5: kline_1h_callback=None does not crash on spot 1h close."""
        monitor = PriceMonitor(
            callback=lambda *a: None,
            venue="spot",
            streams=["miniTicker", "kline_1h"],
        )
        msg = _make_kline_message("BTCUSDT", is_closed=True, interval="1h")
        # Should not raise
        monitor._handle_spot_message(msg)

    def test_spot_kline_1h_no_1000_prefix(self):
        """AC5: Spot kline 1h uses standard symbol mapping (no 1000-prefix)."""
        kline_1h_calls = []

        monitor = PriceMonitor(
            callback=lambda *a: None,
            venue="spot",
            kline_1h_callback=lambda t, b: kline_1h_calls.append((t, b)),
            streams=["kline_1h"],
        )

        msg = _make_kline_message("SHIBUSDT", close="0.00002345", is_closed=True, interval="1h")
        monitor._handle_spot_message(msg)

        assert len(kline_1h_calls) == 1
        assert kline_1h_calls[0][0] == "SHIB"


# ===================================================================
# AC6: update_subscriptions works with configurable streams
# ===================================================================

class TestUpdateSubscriptionsConfigurableStreams:
    """AC6: update_subscriptions uses the configured streams parameter."""

    def test_update_subscriptions_kline_1h_perp(self):
        """AC6: update_subscriptions with streams=["kline_1h"] sends @kline_1h."""
        monitor = PriceMonitor(
            callback=lambda *a: None,
            venue="perp",
            streams=["kline_1h"],
        )
        mock_ws = MagicMock()
        monitor._ws = mock_ws
        monitor._ws_connected = True
        monitor._subscribed_tokens = {"BTC"}

        monitor.update_subscriptions({"BTC", "ETH"})

        assert mock_ws.send.called, "update_subscriptions should send WS message"
        sent_raw = mock_ws.send.call_args_list[0][0][0]
        sent_msg = json.loads(sent_raw)
        assert sent_msg["method"] == "SUBSCRIBE"
        params = sent_msg["params"]
        assert any("@kline_1h" in p for p in params), \
            f"Subscribe params should contain @kline_1h, got: {params}"

    def test_update_subscriptions_kline_1h_spot(self):
        """AC6: update_subscriptions with streams=["kline_1h"] on spot sends @kline_1h."""
        monitor = PriceMonitor(
            callback=lambda *a: None,
            venue="spot",
            streams=["kline_1h"],
        )
        mock_ws = MagicMock()
        monitor._ws = mock_ws
        monitor._ws_connected = True
        monitor._subscribed_tokens = {"BTC"}

        monitor.update_subscriptions({"BTC", "ETH"})

        assert mock_ws.send.called
        sent_raw = mock_ws.send.call_args_list[0][0][0]
        sent_msg = json.loads(sent_raw)
        params = sent_msg["params"]
        assert any("@kline_1h" in p for p in params), \
            f"Spot subscribe should contain @kline_1h, got: {params}"

    def test_update_subscriptions_both_streams(self):
        """AC6: update_subscriptions with both stream types sends both."""
        monitor = PriceMonitor(
            callback=lambda *a: None,
            venue="perp",
            streams=["kline_1m", "kline_1h"],
        )
        mock_ws = MagicMock()
        monitor._ws = mock_ws
        monitor._ws_connected = True
        monitor._subscribed_tokens = {"BTC"}

        monitor.update_subscriptions({"BTC", "ETH"})

        assert mock_ws.send.called
        sent_raw = mock_ws.send.call_args_list[0][0][0]
        sent_msg = json.loads(sent_raw)
        params = sent_msg["params"]
        # New token is ETH, should have both stream types
        assert any("@kline_1m" in p for p in params), \
            f"Should include @kline_1m streams, got: {params}"
        assert any("@kline_1h" in p for p in params), \
            f"Should include @kline_1h streams, got: {params}"

    def test_unsubscribe_kline_1h(self):
        """AC6: Unsubscribe sends @kline_1h stream names."""
        monitor = PriceMonitor(
            callback=lambda *a: None,
            venue="perp",
            streams=["kline_1h"],
        )
        mock_ws = MagicMock()
        monitor._ws = mock_ws
        monitor._ws_connected = True
        monitor._subscribed_tokens = {"BTC", "ETH"}

        monitor.update_subscriptions({"BTC"})

        unsub_found = False
        for c in mock_ws.send.call_args_list:
            msg = json.loads(c[0][0])
            if msg["method"] == "UNSUBSCRIBE":
                unsub_found = True
                assert any("@kline_1h" in p for p in msg["params"]), \
                    f"Unsubscribe should contain @kline_1h, got: {msg['params']}"
        assert unsub_found, "UNSUBSCRIBE message should have been sent"

    def test_update_subscriptions_default_streams_still_kline_1m(self):
        """AC6: Default streams=None produces @kline_1m in subscriptions."""
        monitor = PriceMonitor(callback=lambda *a: None, venue="perp")
        mock_ws = MagicMock()
        monitor._ws = mock_ws
        monitor._ws_connected = True
        monitor._subscribed_tokens = {"BTC"}

        monitor.update_subscriptions({"BTC", "ETH"})

        assert mock_ws.send.called
        sent_raw = mock_ws.send.call_args_list[0][0][0]
        sent_msg = json.loads(sent_raw)
        params = sent_msg["params"]
        assert any("@kline_1m" in p for p in params), \
            f"Default should use @kline_1m, got: {params}"


# ===================================================================
# AC17: Spot miniTicker behavior unchanged
# ===================================================================

class TestSpotMiniTickerUnchanged:
    """AC17: Spot miniTicker behavior is entirely unchanged."""

    def test_spot_default_streams_still_miniticker(self):
        """AC17: Spot with default streams still builds @miniTicker."""
        monitor = PriceMonitor(callback=lambda *a: None, venue="spot")
        names = monitor._build_spot_stream_names(["BTC", "ETH"])
        assert "btcusdt@miniTicker" in names
        assert "ethusdt@miniTicker" in names

    def test_spot_miniticker_price_callback(self):
        """AC17: miniTicker messages dispatch price callback as before."""
        received = []
        monitor = PriceMonitor(
            callback=lambda t, p, ts: received.append((t, p, ts)),
            venue="spot",
        )
        monitor._handle_spot_message(_make_spot_miniticker_message(
            "ETHUSDT", close="3400.0", timestamp=1697380200000,
        ))

        assert len(received) == 1
        assert received[0][0] == "ETH"
        assert received[0][1] == pytest.approx(3400.0)
        assert received[0][2] == 1697380200000

    def test_spot_miniticker_updates_latest_prices(self):
        """AC17: miniTicker updates get_latest_prices() as before."""
        monitor = PriceMonitor(callback=lambda *a: None, venue="spot")
        monitor._handle_spot_message(
            _make_spot_miniticker_message("BTCUSDT", close="65000.0")
        )
        prices = monitor.get_latest_prices()
        assert "BTC" in prices
        assert prices["BTC"] == pytest.approx(65000.0)

    def test_spot_miniticker_ignores_bad_price(self):
        """AC17: miniTicker with zero or negative price is ignored."""
        received = []
        monitor = PriceMonitor(
            callback=lambda t, p, ts: received.append(t),
            venue="spot",
        )
        monitor._handle_spot_message(
            _make_spot_miniticker_message("BTCUSDT", close="0")
        )
        monitor._handle_spot_message(
            _make_spot_miniticker_message("BTCUSDT", close="-1")
        )
        assert len(received) == 0

    def test_spot_miniticker_multiple_tokens(self):
        """AC17: miniTicker fires for each token independently."""
        received = []
        monitor = PriceMonitor(
            callback=lambda t, p, ts: received.append(t),
            venue="spot",
        )
        for tok, price in [("BTC", "65000"), ("ETH", "3400"), ("SOL", "155")]:
            monitor._handle_spot_message(
                _make_spot_miniticker_message(f"{tok}USDT", close=price)
            )
        assert received == ["BTC", "ETH", "SOL"]


# ===================================================================
# AC18: Perp kline_1m behavior unchanged
# ===================================================================

class TestPerpKline1mUnchanged:
    """AC18: Perp kline_1m behavior is entirely unchanged."""

    def test_perp_default_streams_still_kline_1m(self):
        """AC18: Perp with default streams still builds @kline_1m."""
        monitor = PriceMonitor(callback=lambda *a: None, venue="perp")
        names = monitor._build_perp_stream_names(["BTC", "ETH"])
        assert "btcusdt@kline_1m" in names
        assert "ethusdt@kline_1m" in names

    def test_perp_1m_price_callback_every_update(self):
        """AC18: 1m kline price callback fires on every update (unchanged)."""
        received = []
        monitor = PriceMonitor(
            callback=lambda t, p, ts: received.append((t, p)),
            venue="perp",
        )
        # Interim
        monitor._handle_perp_message(
            _make_kline_message("BTCUSDT", close="65050.0", is_closed=False, interval="1m")
        )
        # Close
        monitor._handle_perp_message(
            _make_kline_message("BTCUSDT", close="65100.0", is_closed=True, interval="1m")
        )

        assert len(received) == 2

    def test_perp_1m_kline_callback_on_close(self):
        """AC18: 1m kline_callback fires on close (unchanged)."""
        kline_calls = []
        monitor = PriceMonitor(
            callback=lambda *a: None,
            venue="perp",
            kline_callback=lambda t, b: kline_calls.append((t, b)),
        )
        monitor._handle_perp_message(
            _make_kline_message("BTCUSDT", close="65100.0", is_closed=True, interval="1m")
        )

        assert len(kline_calls) == 1
        assert kline_calls[0][0] == "BTC"

    def test_perp_1m_kline_callback_not_on_interim(self):
        """AC18: 1m kline_callback does NOT fire on interim (unchanged)."""
        kline_calls = []
        monitor = PriceMonitor(
            callback=lambda *a: None,
            venue="perp",
            kline_callback=lambda t, b: kline_calls.append((t, b)),
        )
        monitor._handle_perp_message(
            _make_kline_message("BTCUSDT", close="65050.0", is_closed=False, interval="1m")
        )

        assert len(kline_calls) == 0

    def test_perp_1m_updates_latest_prices(self):
        """AC18: 1m kline updates get_latest_prices() (unchanged)."""
        monitor = PriceMonitor(callback=lambda *a: None, venue="perp")
        monitor._handle_perp_message(
            _make_kline_message("BTCUSDT", close="65100.0", is_closed=False, interval="1m")
        )
        prices = monitor.get_latest_prices()
        assert "BTC" in prices
        assert prices["BTC"] == pytest.approx(65100.0)

    def test_perp_ignores_non_kline_events(self):
        """AC18: Perp handler ignores non-kline events (unchanged)."""
        received = []
        monitor = PriceMonitor(
            callback=lambda t, p, ts: received.append(t),
            venue="perp",
        )
        monitor._handle_perp_message({
            "e": "markPriceUpdate", "s": "BTCUSDT", "p": "65000.0", "E": 1,
        })
        assert len(received) == 0
