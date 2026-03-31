"""Acceptance tests for Task 2: REST fallback using klines endpoint.

Tests verify:
  - AC4: _fetch_perp_rest_prices uses /fapi/v1/klines per symbol (not premiumIndex)
  - AC4a: _fetch_single_perp_rest_price also uses klines endpoint

All tests MUST FAIL until implementation is done (RED phase).
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "v4"))

import pytest

from v4.price_monitor import PriceMonitor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_klines_rest_response(close_price: float = 65100.0, timestamp: int = 1697380200000):
    """Build a mock Binance REST /fapi/v1/klines response (single row).

    Klines response format: [[open_time, open, high, low, close, volume, close_time, quote_vol, trades, ...]]
    Close price is at index 4.
    """
    return [[
        timestamp,           # 0: open_time
        "65000.0",           # 1: open
        "65200.0",           # 2: high
        "64900.0",           # 3: low
        str(close_price),    # 4: close
        "100.0",             # 5: volume
        timestamp + 59999,   # 6: close_time
        "6500000.0",         # 7: quote_volume
        50,                  # 8: trades
        "50.0",              # 9: taker_buy_base
        "3250000.0",         # 10: taker_buy_quote
        "0",                 # 11: ignore
    ]]


# ===================================================================
# AC4: _fetch_perp_rest_prices uses /fapi/v1/klines
# ===================================================================

class TestPerpRestFallbackKlines:
    """AC4: REST fallback uses /fapi/v1/klines per symbol."""

    def test_rest_fallback_calls_klines_endpoint(self):
        """AC4: _fetch_perp_rest_prices calls /fapi/v1/klines, not /fapi/v1/premiumIndex."""
        received_urls = []
        received_params = []

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = _make_klines_rest_response(65100.0)
        mock_resp.raise_for_status = MagicMock()

        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp

        monitor = PriceMonitor(callback=lambda *a: None, venue="perp")
        monitor._rest_session = mock_session

        monitor._fetch_perp_rest_prices({"BTC"})

        # Check that GET was called
        assert mock_session.get.called, "REST session.get should have been called"

        # Extract URL from call
        call_args = mock_session.get.call_args
        url = call_args[0][0] if call_args[0] else call_args[1].get("url", "")

        assert "/klines" in url, f"REST URL should contain /klines, got: {url}"
        assert "premiumIndex" not in url, f"REST URL should NOT contain premiumIndex, got: {url}"

    def test_rest_fallback_extracts_close_from_row_4(self):
        """AC4: REST fallback extracts close price from row[4] of klines response."""
        received = []

        def on_price(token, price, timestamp):
            received.append((token, price))

        mock_resp = MagicMock()
        mock_resp.json.return_value = _make_klines_rest_response(65100.0)
        mock_resp.raise_for_status = MagicMock()

        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp

        monitor = PriceMonitor(callback=on_price, venue="perp")
        monitor._rest_session = mock_session

        monitor._fetch_perp_rest_prices({"BTC"})

        assert len(received) >= 1, "Price callback should have been called from REST fallback"
        # Close price should be 65100.0 (from row[4])
        assert received[0][1] == pytest.approx(65100.0), \
            f"Close price should be extracted from row[4], got: {received[0][1]}"

    def test_rest_fallback_per_symbol_for_specific_tokens(self):
        """AC4: REST fallback fetches per-symbol, only for specified tokens."""
        call_count = 0

        def mock_get(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            resp.json.return_value = _make_klines_rest_response(65100.0)
            resp.raise_for_status = MagicMock()
            return resp

        mock_session = MagicMock()
        mock_session.get.side_effect = mock_get

        monitor = PriceMonitor(callback=lambda *a: None, venue="perp")
        monitor._rest_session = mock_session

        tokens = {"BTC", "ETH", "SOL"}
        monitor._fetch_perp_rest_prices(tokens)

        # Should make one call per token (per-symbol, no batch for klines)
        assert call_count == len(tokens), \
            f"Should make {len(tokens)} REST calls (one per token), got: {call_count}"

    def test_rest_fallback_empty_tokens(self):
        """AC4: Empty tokens set makes zero REST calls."""
        mock_session = MagicMock()
        monitor = PriceMonitor(callback=lambda *a: None, venue="perp")
        monitor._rest_session = mock_session

        monitor._fetch_perp_rest_prices(set())

        assert not mock_session.get.called, \
            "Empty tokens should make zero REST calls"


# ===================================================================
# AC4a: _fetch_single_perp_rest_price uses klines endpoint
# ===================================================================

class TestSinglePerpRestKlines:
    """AC4a: _fetch_single_perp_rest_price uses klines, not premiumIndex."""

    def test_single_rest_price_uses_klines_url(self):
        """AC4a: _fetch_single_perp_rest_price URL contains /klines."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = _make_klines_rest_response(65100.0)
        mock_resp.raise_for_status = MagicMock()

        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp

        monitor = PriceMonitor(callback=lambda *a: None, venue="perp")
        monitor._rest_session = mock_session

        result = monitor._fetch_single_perp_rest_price("BTC")

        assert mock_session.get.called
        call_args = mock_session.get.call_args
        url = call_args[0][0] if call_args[0] else ""

        assert "/klines" in url, f"Single REST URL should contain /klines, got: {url}"
        assert "premiumIndex" not in url, \
            f"Single REST URL should NOT contain premiumIndex, got: {url}"

    def test_single_rest_price_returns_close_price(self):
        """AC4a: _fetch_single_perp_rest_price returns close price from klines."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = _make_klines_rest_response(42000.5)
        mock_resp.raise_for_status = MagicMock()

        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp

        monitor = PriceMonitor(callback=lambda *a: None, venue="perp")
        monitor._rest_session = mock_session

        result = monitor._fetch_single_perp_rest_price("BTC")

        assert result is not None, "Should return a price"
        assert result == pytest.approx(42000.5), \
            f"Should return close price from klines row[4], got: {result}"

    def test_single_rest_price_failure_returns_none(self):
        """AC4a: _fetch_single_perp_rest_price returns None on failure."""
        import requests

        mock_session = MagicMock()
        mock_session.get.side_effect = requests.RequestException("Network error")

        monitor = PriceMonitor(callback=lambda *a: None, venue="perp")
        monitor._rest_session = mock_session

        result = monitor._fetch_single_perp_rest_price("BTC")
        assert result is None, "Should return None on request failure"
