"""Real-Time Price Monitor for the Exit Sentinel.

Subscribes to Binance Futures @kline_1m or Binance Spot @miniTicker
WebSocket streams and dispatches price updates to a callback.  Falls back to
REST polling when the WebSocket disconnects, with exponential backoff
reconnection.

The ``venue`` parameter selects between ``"perp"`` (Futures kline 1m streams)
and ``"spot"`` (Spot last-price) endpoints.  All reconnection, REST fallback,
subscription management, and thread-safety logic is shared.

For perp venue, the kline close price is used as the real-time price (aligning
with backtest trade-price klines).  An optional ``kline_callback`` fires on
candle close (``k.x == true``) with the full OHLCV bar dict.

Implementation uses websocket-client for the WebSocket connection and requests
for REST fallback.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Callable, Optional
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

# Binance Futures WebSocket and REST endpoints
WS_BASE_URL = "wss://fstream.binance.com/stream"
REST_BASE_URL = "https://fapi.binance.com"
REST_KLINES = "/fapi/v1/klines"

# Binance Spot WebSocket and REST endpoints
SPOT_WS_BASE_URL = "wss://stream.binance.com:9443/stream"
SPOT_REST_BASE_URL = "https://api.binance.com"
SPOT_REST_TICKER_PRICE = "/api/v3/ticker/price"

# T5-1 fix: Tokens that use 1000-prefix on Binance Futures
# e.g., SHIB trades as 1000SHIBUSDT, PEPE as 1000PEPEUSDT
_1000_PREFIX_TOKENS = {
    "SHIB", "PEPE", "FLOKI", "BONK", "LUNC", "SATS", "RATS", "CAT",
    "CHEEMS", "WHY", "X", "XEC",
}

# T5-8 fix: Max reconnection attempts before alerting
MAX_RECONNECT_ATTEMPTS = 20  # ~15+ minutes of reconnection at max backoff


def _token_to_symbol(token: str) -> str:
    """Convert internal token name to Binance Futures symbol (e.g., SHIB -> 1000SHIBUSDT)."""
    if token in _1000_PREFIX_TOKENS:
        return f"1000{token}USDT"
    return f"{token}USDT"


def _symbol_to_token(symbol: str) -> str:
    """Convert Binance Futures symbol to internal token name (e.g., 1000SHIBUSDT -> SHIB)."""
    if not symbol.endswith("USDT"):
        return symbol
    base = symbol[:-4]  # Strip USDT
    if base.startswith("1000") and base[4:] in _1000_PREFIX_TOKENS:
        return base[4:]
    return base


def _token_to_spot_symbol(token: str) -> str:
    """Convert internal token to Binance spot symbol.  No 1000-prefix."""
    return f"{token}USDT"


def _spot_symbol_to_token(symbol: str) -> str:
    """Convert Binance spot symbol to internal token name."""
    if symbol.endswith("USDT"):
        return symbol[:-4]
    return symbol


class PriceMonitor:
    """WebSocket-based price monitor with REST fallback.

    Parameters
    ----------
    callback : (token: str, price: float, timestamp: int) -> None
        Invoked for each price update.
    rest_poll_interval_s : int
        Seconds between REST fallback polls (default 10).
    venue : str
        ``"perp"`` for Binance Futures mark-price streams (default),
        ``"spot"`` for Binance Spot miniTicker streams.
    """

    MAX_BACKOFF_S = 60.0

    def __init__(
        self,
        callback: Callable[[str, float, int], None],
        rest_poll_interval_s: int = 10,
        venue: str = "perp",
        kline_callback: Optional[Callable[[str, dict], None]] = None,
        kline_1h_callback: Optional[Callable[[str, dict], None]] = None,
        streams: Optional[list] = None,
    ) -> None:
        self.callback = callback
        self.kline_callback = kline_callback
        self.kline_1h_callback = kline_1h_callback
        self.rest_poll_interval_s = rest_poll_interval_s
        self._venue = venue
        self._streams = streams

        self._ws_connected: bool = False
        self._reconnect_attempt: int = 0
        self._subscribed_tokens: set[str] = set()

        # WebSocket state
        self._ws = None
        self._ws_thread: Optional[threading.Thread] = None
        self._shutdown: bool = False
        self._latest_prices: dict[str, float] = {}
        self._price_lock = threading.Lock()  # R2-2 fix

        # T5-13 fix: Event for interruptible sleep during backoff
        self._shutdown_event = threading.Event()

        # Callbacks for metrics tracking (R2-8 fix)
        self._on_ws_connect_cb: Optional[Callable[[], None]] = None
        self._on_ws_disconnect_cb: Optional[Callable[[], None]] = None
        # F5 fix: Callback for reconnection events
        self._on_ws_reconnect_cb: Optional[Callable[[], None]] = None

        # T5-8 fix: Callback when max reconnect attempts exceeded
        self._on_max_reconnect_cb: Optional[Callable[[int], None]] = None
        # R2-F6 fix: Fire max reconnect alert only once per connection cycle
        self._max_reconnect_alerted: bool = False

        # REST session (connection pooling)
        self._rest_session: Optional[requests.Session] = None
        # T5-12 fix: Lock for lazy session creation
        self._rest_session_lock = threading.Lock()

        # R2-9 fix: Monotonic counter for WS message IDs
        self._msg_id_counter: int = 0

        # T5-6 fix: Lock for _subscribed_tokens and _msg_id_counter
        self._sub_lock = threading.Lock()

    # ------------------------------------------------------------------
    # WebSocket message handling
    # ------------------------------------------------------------------

    def _handle_message(self, msg: dict) -> None:
        """Process a single WebSocket message, routing by venue."""
        if self._venue == "spot":
            self._handle_spot_message(msg)
        else:
            self._handle_perp_message(msg)

    def _handle_perp_message(self, msg: dict) -> None:
        """Process a single WebSocket kline message (perp venue).

        Kline message format:
          {"e":"kline","E":<ts>,"s":"BTCUSDT","k":{"t":<open_ts>,"T":<close_ts>,
           "s":"BTCUSDT","i":"1m","o":"65000.0","c":"65100.0","h":"65200.0",
           "l":"64900.0","v":"100.0","n":50,"x":true/false,"q":"6500000.0"}}

        Routes by interval (k.i):
        - "1m": Price callback on every update, kline_callback on close (existing).
        - "1h": kline_1h_callback on close only. No price callback (separate data channel).
        """
        event_type = msg.get("e", "")
        if event_type != "kline":
            return

        k = msg.get("k", {})
        interval = k.get("i", "")
        symbol = msg.get("s", "")
        # T5-1 fix: Use proper symbol-to-token mapping (handles 1000-prefix)
        token = _symbol_to_token(symbol)

        if interval == "1h":
            # 1H handling: only fire kline_1h_callback on candle close
            if k.get("x") and self.kline_1h_callback is not None:
                try:
                    bar = {
                        "timestamp": int(k["t"]),
                        "open": float(k["o"]),
                        "high": float(k["h"]),
                        "low": float(k["l"]),
                        "close": float(k["c"]),
                        "volume": float(k["v"]),
                    }
                except (KeyError, ValueError, TypeError) as exc:
                    logger.warning("Bad OHLCV fields in 1h kline close for %s: %s", symbol, exc)
                    return
                try:
                    self.kline_1h_callback(token, bar)
                except Exception as exc:
                    logger.error("Error in kline_1h_callback: %s", exc, exc_info=True)
            return

        # 1m handling (default): price callback on every update + kline_callback on close
        try:
            price = float(k["c"])
        except (KeyError, ValueError, TypeError) as exc:
            logger.warning("Bad price in kline WS message %s: %s", symbol, exc)
            return

        # R8-2 fix: `not (price > 0)` catches NaN, zero, and negative
        if not (price > 0):
            return

        timestamp = msg.get("E", int(time.time() * 1000))
        with self._price_lock:
            self._latest_prices[token] = price
        self.callback(token, price, timestamp)

        # On candle close, dispatch kline_callback with full OHLCV bar
        if k.get("x") and self.kline_callback is not None:
            try:
                bar = {
                    "timestamp": int(k["t"]),
                    "open": float(k["o"]),
                    "high": float(k["h"]),
                    "low": float(k["l"]),
                    "close": float(k["c"]),
                    "volume": float(k["v"]),
                }
            except (KeyError, ValueError, TypeError) as exc:
                logger.warning("Bad OHLCV fields in kline close for %s: %s", symbol, exc)
                return
            try:
                self.kline_callback(token, bar)
            except Exception as exc:
                logger.error("Error in kline_callback: %s", exc, exc_info=True)

    def _handle_spot_message(self, msg: dict) -> None:
        """Process a single WebSocket message (spot venue).

        Handles two event types:
        - ``24hrMiniTicker``: dispatches price callback (existing behavior).
        - ``kline`` with ``i="1h"``: dispatches kline_1h_callback on close.
        """
        event_type = msg.get("e", "")

        if event_type == "24hrMiniTicker":
            symbol = msg.get("s", "")
            token = _spot_symbol_to_token(symbol)

            try:
                price = float(msg["c"])
            except (KeyError, ValueError, TypeError) as exc:
                logger.warning("Bad price in spot WS message %s: %s", symbol, exc)
                return

            if not (price > 0):
                return

            timestamp = msg.get("E", int(time.time() * 1000))
            with self._price_lock:
                self._latest_prices[token] = price
            self.callback(token, price, timestamp)

        elif event_type == "kline":
            k = msg.get("k", {})
            if k.get("i") == "1h" and k.get("x") and self.kline_1h_callback is not None:
                symbol = msg.get("s", "")
                token = _spot_symbol_to_token(symbol)
                try:
                    bar = {
                        "timestamp": int(k["t"]),
                        "open": float(k["o"]),
                        "high": float(k["h"]),
                        "low": float(k["l"]),
                        "close": float(k["c"]),
                        "volume": float(k["v"]),
                    }
                except (KeyError, ValueError, TypeError) as exc:
                    logger.warning("Bad OHLCV fields in spot 1h kline close for %s: %s", symbol, exc)
                    return
                try:
                    self.kline_1h_callback(token, bar)
                except Exception as exc:
                    logger.error("Error in kline_1h_callback: %s", exc, exc_info=True)

    # ------------------------------------------------------------------
    # Dynamic subscription management (AC15)
    # ------------------------------------------------------------------

    @staticmethod
    def compute_subscription_changes(
        current: set[str], desired: set[str],
    ) -> tuple[set[str], set[str]]:
        """Compute tokens to subscribe and unsubscribe.

        Returns (to_subscribe, to_unsubscribe).
        """
        to_subscribe = desired - current
        to_unsubscribe = current - desired
        return to_subscribe, to_unsubscribe

    # ------------------------------------------------------------------
    # Stream subscription
    # ------------------------------------------------------------------

    def _build_stream_names(self, tokens: list[str]) -> list[str]:
        """Build Binance combined-stream names, routing by venue."""
        if not tokens:
            return []
        if self._venue == "spot":
            return self._build_spot_stream_names(tokens)
        return self._build_perp_stream_names(tokens)

    def _build_perp_stream_names(self, tokens: list[str]) -> list[str]:
        """Build Binance Futures combined-stream names.

        Uses self._streams to determine stream types. Default (None) preserves
        existing behavior (kline_1m only). With streams=["kline_1h"], returns
        @kline_1h streams only.
        """
        stream_types = self._streams or ["kline_1m"]
        names = []
        for t in tokens:
            sym = _token_to_symbol(t).lower()
            for st in stream_types:
                names.append(f"{sym}@{st}")
        return names

    def _build_spot_stream_names(self, tokens: list[str]) -> list[str]:
        """Build Binance Spot combined-stream names.

        Uses self._streams to determine stream types. Default (None) preserves
        existing behavior (miniTicker only). With streams=["kline_1h"], returns
        @kline_1h streams only.
        """
        stream_types = self._streams or ["miniTicker"]
        names = []
        for t in tokens:
            sym = _token_to_spot_symbol(t).lower()
            for st in stream_types:
                names.append(f"{sym}@{st}")
        return names

    # ------------------------------------------------------------------
    # Reconnect backoff
    # ------------------------------------------------------------------

    def _compute_backoff_delay(self, attempt: int) -> float:
        """Exponential backoff: 2^attempt seconds, capped at MAX_BACKOFF_S."""
        delay = min(2.0 ** attempt, self.MAX_BACKOFF_S)
        return delay

    # ------------------------------------------------------------------
    # REST fallback (AC10)
    # ------------------------------------------------------------------

    def _should_use_rest_fallback(self) -> bool:
        """Return True when WS is disconnected and REST should poll."""
        return not self._ws_connected

    def _get_rest_session(self) -> requests.Session:
        """Get or create a REST session with connection pooling.

        T5-12 fix: Thread-safe lazy initialization.
        """
        if self._rest_session is not None:
            return self._rest_session
        with self._rest_session_lock:
            # Double-check under lock
            if self._rest_session is None:
                session = requests.Session()
                session.headers.update({
                    "Accept": "application/json",
                })
                self._rest_session = session
        return self._rest_session

    def _fetch_rest_prices(self, tokens: set[str]) -> list[dict]:
        """Fetch prices via REST, routing by venue."""
        if self._venue == "spot":
            return self._fetch_spot_rest_prices(tokens)
        return self._fetch_perp_rest_prices(tokens)

    def _fetch_perp_rest_prices(self, tokens: set[str]) -> list[dict]:
        """Fetch close prices via Binance Futures REST klines API.

        Uses per-symbol /fapi/v1/klines endpoint (klines doesn't support
        multi-symbol batch). Extracts close price from row[4].

        Returns list of klines response rows.
        """
        if not tokens:
            return []

        session = self._get_rest_session()
        results = []

        for token in tokens:
            symbol = _token_to_symbol(token)
            try:
                resp = session.get(
                    f"{REST_BASE_URL}{REST_KLINES}",
                    params={"symbol": symbol, "interval": "1m", "limit": 1},
                    timeout=5,
                )
                resp.raise_for_status()
                data = resp.json()
                if isinstance(data, list) and len(data) > 0:
                    row = data[-1]
                    results.append(row)
                    try:
                        price = float(row[4])  # close price at index 4
                        ts = int(time.time() * 1000)  # wall clock, not bar open_time
                        if price > 0:
                            with self._price_lock:
                                self._latest_prices[token] = price
                            self.callback(token, price, ts)
                    except (IndexError, ValueError, TypeError):
                        pass
                else:
                    logger.debug("REST klines returned empty for %s", symbol)
            except (requests.RequestException, json.JSONDecodeError) as exc:
                logger.warning("REST klines failed for %s: %s", symbol, exc)

        return results

    def _fetch_rest_prices_on_reconnect(self, tokens: set[str]) -> list[dict]:
        """Fetch REST prices on WS reconnection to cover the gap.

        Called once after reconnect to detect breaches that may have
        occurred while the WebSocket was disconnected.
        """
        return self._fetch_rest_prices(tokens)

    def fetch_single_rest_price(self, token: str) -> Optional[float]:
        """Fetch a single token's price via REST for exit validation (AC14).

        Routes to spot or perp REST endpoint based on venue.
        Returns the price as float, or None on failure.
        """
        if self._venue == "spot":
            return self._fetch_single_spot_rest_price(token)
        return self._fetch_single_perp_rest_price(token)

    def _fetch_single_perp_rest_price(self, token: str) -> Optional[float]:
        """Fetch a single token's close price via Futures REST klines."""
        session = self._get_rest_session()
        symbol = _token_to_symbol(token)
        try:
            resp = session.get(
                f"{REST_BASE_URL}{REST_KLINES}",
                params={"symbol": symbol, "interval": "1m", "limit": 1},
                timeout=5,
            )
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list) and len(data) > 0:
                price = float(data[-1][4])  # close price at index 4
                return price if price > 0 else None
            return None
        except (requests.RequestException, ValueError, TypeError, IndexError) as exc:
            logger.warning("REST klines price fetch failed for %s: %s", symbol, exc)
            return None

    def _fetch_single_spot_rest_price(self, token: str) -> Optional[float]:
        """Fetch a single token's last price via Spot REST ticker/price."""
        session = self._get_rest_session()
        symbol = _token_to_spot_symbol(token)
        try:
            resp = session.get(
                f"{SPOT_REST_BASE_URL}{SPOT_REST_TICKER_PRICE}",
                params={"symbol": symbol},
                timeout=5,
            )
            resp.raise_for_status()
            data = resp.json()
            price = float(data.get("price", 0))
            return price if price > 0 else None
        except (requests.RequestException, ValueError, TypeError) as exc:
            logger.warning("Spot REST price fetch failed for %s: %s", symbol, exc)
            return None

    def _fetch_spot_rest_prices(self, tokens: set[str]) -> list[dict]:
        """Fetch last prices via Binance Spot REST ticker/price API.

        Uses the batch endpoint (no symbol param) when fetching multiple tokens.
        Returns list of ticker/price response dicts ({symbol, price}).
        """
        if not tokens:
            return []

        session = self._get_rest_session()
        wanted_symbols = {_token_to_spot_symbol(t): t for t in tokens}

        # Try batch fetch first
        if len(tokens) > 1:
            try:
                resp = session.get(
                    f"{SPOT_REST_BASE_URL}{SPOT_REST_TICKER_PRICE}",
                    timeout=10,
                )
                resp.raise_for_status()
                all_data = resp.json()
                if isinstance(all_data, list):
                    results = []
                    for item in all_data:
                        sym = item.get("symbol", "")
                        if sym in wanted_symbols:
                            results.append(item)
                            token = wanted_symbols[sym]
                            try:
                                price = float(item.get("price", 0))
                                ts = int(time.time() * 1000)
                                if price > 0:
                                    with self._price_lock:
                                        self._latest_prices[token] = price
                                    self.callback(token, price, ts)
                            except (ValueError, TypeError):
                                pass
                    return results
            except (requests.RequestException, json.JSONDecodeError) as exc:
                logger.warning("Batch spot REST ticker/price failed, falling back to per-token: %s", exc)

        # Per-token fallback
        results = []
        for token in tokens:
            symbol = _token_to_spot_symbol(token)
            try:
                resp = session.get(
                    f"{SPOT_REST_BASE_URL}{SPOT_REST_TICKER_PRICE}",
                    params={"symbol": symbol},
                    timeout=5,
                )
                resp.raise_for_status()
                data = resp.json()
                results.append(data)
                try:
                    price = float(data.get("price", 0))
                    ts = int(time.time() * 1000)
                    if price > 0:
                        with self._price_lock:
                            self._latest_prices[token] = price
                        self.callback(token, price, ts)
                except (ValueError, TypeError):
                    pass
            except (requests.RequestException, json.JSONDecodeError) as exc:
                logger.warning("Spot REST ticker/price failed for %s: %s", symbol, exc)

        return results

    @property
    def ws_connected(self) -> bool:
        """Thread-safe read of WebSocket connection status (F4 fix)."""
        return self._ws_connected

    def get_latest_prices(self) -> dict[str, float]:
        """Return a copy of the latest known prices for all tokens.
        R2-2 fix: Acquires lock for thread safety."""
        with self._price_lock:
            return dict(self._latest_prices)

    # ------------------------------------------------------------------
    # WebSocket connection (AC3)
    # ------------------------------------------------------------------

    def connect(self, tokens: list[str]) -> None:
        """Start WebSocket connection to Binance Futures mark price streams.

        Launches a background thread that maintains the connection,
        handles reconnection with exponential backoff, and dispatches
        price updates via the callback.

        T5-4 fix: If already connected, disconnect first to prevent orphaned threads.
        """
        import websocket

        # Prevent orphaned threads from duplicate connect() calls
        if self._ws_thread is not None and self._ws_thread.is_alive():
            self.disconnect()
            self._ws_thread.join(timeout=5)

        with self._sub_lock:
            self._subscribed_tokens = set(tokens)
        self._shutdown = False
        self._shutdown_event.clear()

        def _ws_thread_target():
            while not self._shutdown:
                # R3-1/T5-6 fix: Use current _subscribed_tokens under lock
                with self._sub_lock:
                    current_tokens = list(self._subscribed_tokens)
                try:
                    self._connect_ws(current_tokens)
                except Exception as exc:
                    logger.error("WebSocket error: %s", exc)

                if self._shutdown:
                    break

                delay = self._compute_backoff_delay(self._reconnect_attempt)
                self._reconnect_attempt += 1

                # R2-F6 fix: Alert only once when max reconnect attempts exceeded
                if (self._reconnect_attempt >= MAX_RECONNECT_ATTEMPTS
                        and not self._max_reconnect_alerted):
                    self._max_reconnect_alerted = True
                    logger.error(
                        "WebSocket reconnection failed %d times — alerting",
                        self._reconnect_attempt,
                    )
                    if self._on_max_reconnect_cb:
                        try:
                            self._on_max_reconnect_cb(self._reconnect_attempt)
                        except Exception:
                            pass

                logger.info(
                    "WebSocket disconnected. Reconnecting in %.1fs (attempt %d)",
                    delay, self._reconnect_attempt,
                )

                # F5 fix: Record reconnection attempt in metrics
                if self._on_ws_reconnect_cb:
                    try:
                        self._on_ws_reconnect_cb()
                    except Exception:
                        pass

                # R3-3 fix: REST fallback using _subscribed_tokens (not dead _active_tokens_for_rest)
                with self._sub_lock:
                    fallback_tokens = set(self._subscribed_tokens)
                if fallback_tokens:
                    try:
                        self._fetch_rest_prices(fallback_tokens)
                    except Exception as exc:
                        logger.warning("REST fallback during reconnect failed: %s", exc)

                # T5-13 fix: Interruptible sleep — disconnect() can wake us up
                if self._shutdown_event.wait(timeout=delay):
                    break  # Shutdown requested during backoff

        self._ws_thread = threading.Thread(
            target=_ws_thread_target,
            name=f"sentinel-ws-{self._venue}",
            daemon=True,
        )
        self._ws_thread.start()

    def _connect_ws(self, tokens: list[str]) -> None:
        """Establish WebSocket connection and run message loop."""
        import websocket

        streams = self._build_stream_names(tokens)
        if not streams:
            return

        # Combined stream URL — venue-appropriate base
        stream_param = "/".join(streams)
        base_url = SPOT_WS_BASE_URL if self._venue == "spot" else WS_BASE_URL
        url = f"{base_url}?streams={stream_param}"

        def on_open(ws):
            logger.info("WebSocket connected to %d streams", len(streams))
            self._ws_connected = True
            self._reconnect_attempt = 0
            self._max_reconnect_alerted = False  # R2-F6: reset alert flag on reconnect

            # R2-8 fix: Record ws_connect in on_open (after handshake)
            if self._on_ws_connect_cb:
                self._on_ws_connect_cb()

            # T5-5 fix: Fetch REST prices in a thread to avoid blocking WS event loop
            # R6-3 fix: Read _subscribed_tokens under lock
            with self._sub_lock:
                gap_fill_tokens = set(self._subscribed_tokens)
            if gap_fill_tokens:
                def _gap_fill(tokens=gap_fill_tokens):
                    try:
                        self._fetch_rest_prices_on_reconnect(tokens)
                    except Exception as exc:
                        logger.warning("REST gap-fill failed: %s", exc)
                threading.Thread(target=_gap_fill, name="sentinel-gap-fill", daemon=True).start()

        def on_message(ws, raw_msg):
            try:
                data = json.loads(raw_msg)
            except json.JSONDecodeError as exc:
                logger.warning("Failed to parse WS message: %s", exc)
                return

            try:
                # Combined stream wraps data in {"stream": ..., "data": {...}}
                if "data" in data:
                    self._handle_message(data["data"])
                else:
                    self._handle_message(data)
            except Exception as exc:
                # R3-16 fix: Log at ERROR for callback failures (programming errors)
                logger.error("Error in price callback: %s", exc, exc_info=True)

        def on_error(ws, error):
            logger.warning("WebSocket error: %s", error)

        def on_close(ws, close_status_code, close_msg):
            logger.info("WebSocket closed: %s %s", close_status_code, close_msg)
            self._ws_connected = False
            # R2-3 fix: Record disconnect once in the callback, not every tick
            if self._on_ws_disconnect_cb:
                self._on_ws_disconnect_cb()

        self._ws = websocket.WebSocketApp(
            url,
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
        )

        # run_forever blocks until connection closes
        # Support HTTP(S) proxy via environment variables
        proxy_kwargs: dict = {}
        proxy_url = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy")
        if proxy_url:
            parsed = urlparse(proxy_url)
            proxy_kwargs["http_proxy_host"] = parsed.hostname
            proxy_kwargs["http_proxy_port"] = parsed.port
            proxy_kwargs["proxy_type"] = "http"
            if parsed.username:
                proxy_kwargs["http_proxy_auth"] = (parsed.username, parsed.password)
        self._ws.run_forever(ping_interval=30, ping_timeout=10, **proxy_kwargs)

    def update_subscriptions(self, new_tokens: set[str]) -> None:
        """Update WebSocket subscriptions dynamically (AC15).

        Sends subscribe/unsubscribe requests via the existing connection.
        T5-10 fix: Always updates internal _subscribed_tokens so the next
        reconnect picks up the new token set, even if WS is currently down.
        """
        # R4-4 fix: Single lock region to avoid TOCTOU between compute and update
        with self._sub_lock:
            to_sub, to_unsub = self.compute_subscription_changes(
                self._subscribed_tokens, new_tokens,
            )

            if not to_sub and not to_unsub:
                return

            if self._ws and self._ws_connected:
                # Subscribe to new streams
                if to_sub:
                    sub_streams = self._build_stream_names(list(to_sub))
                    self._msg_id_counter += 1
                    subscribe_msg = json.dumps({
                        "method": "SUBSCRIBE",
                        "params": sub_streams,
                        "id": self._msg_id_counter,
                    })
                    try:
                        self._ws.send(subscribe_msg)
                        logger.info("Subscribed to %d new streams", len(sub_streams))
                    except Exception as exc:
                        logger.warning("Failed to subscribe: %s", exc)

                # Unsubscribe from removed streams
                if to_unsub:
                    unsub_streams = self._build_stream_names(list(to_unsub))
                    self._msg_id_counter += 1
                    unsubscribe_msg = json.dumps({
                        "method": "UNSUBSCRIBE",
                        "params": unsub_streams,
                        "id": self._msg_id_counter,
                    })
                    try:
                        self._ws.send(unsubscribe_msg)
                        logger.info("Unsubscribed from %d streams", len(unsub_streams))
                    except Exception as exc:
                        logger.warning("Failed to unsubscribe: %s", exc)

            # R5-4/T5-10 fix: Always update internal state regardless of WS send result.
            # On next reconnect, _subscribed_tokens is used to build the full stream list.
            self._subscribed_tokens = (self._subscribed_tokens | to_sub) - to_unsub

    def disconnect(self) -> None:
        """Gracefully close WebSocket connection and stop threads.

        T5-13 fix: Sets shutdown event to interrupt backoff sleep.
        R4-7 fix: Joins ws_thread to prevent post-disconnect callbacks.
        """
        self._shutdown = True
        self._shutdown_event.set()  # Wake up any sleeping backoff
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
        # R4-7 fix: Wait for WS thread to finish (bounded timeout)
        if self._ws_thread is not None and self._ws_thread.is_alive():
            self._ws_thread.join(timeout=5)
        if self._rest_session:
            try:
                self._rest_session.close()
            except Exception:
                pass
        self._ws_connected = False

    # ------------------------------------------------------------------
    # REST-only polling mode (for environments without WebSocket)
    # ------------------------------------------------------------------

    def run_rest_poll_loop(self, tokens: set[str], duration_s: float = 0) -> None:
        """Run a REST-only polling loop (no WebSocket).

        Useful for testing or environments where WebSocket is unavailable.
        If duration_s > 0, runs for that many seconds then returns.
        If duration_s == 0, runs until self._shutdown is set.
        """
        with self._sub_lock:
            self._subscribed_tokens = tokens
        start = time.time()

        while not self._shutdown:
            self._fetch_rest_prices(tokens)
            # R4-5 fix: Use event.wait for interruptible sleep
            if self._shutdown_event.wait(timeout=self.rest_poll_interval_s):
                break

            if duration_s > 0 and (time.time() - start) >= duration_s:
                break
