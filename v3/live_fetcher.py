"""Live data fetcher for paper trading engine.

Polls Binance via CCXT REST for 1H OHLCV candles (spot + perp) and funding
rates.  Appends to JSONL WAL overlay files — never modifies historical
parquets.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

import ccxt


# Maps timeframe string to milliseconds.
_TF_MS = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
}


def _token_prefix(token: str) -> str:
    """'BTC/USDT' -> 'BTC'."""
    return token.split("/")[0]


class LiveFetcher:
    """Fetches live OHLCV + funding data and manages JSONL WAL files."""

    def __init__(self, exchange: str = "binance", data_dir: str = "data"):
        self.exchange_id = exchange
        self.data_dir = data_dir
        self._exchanges: dict[str, ccxt.Exchange] = {}

    # ------------------------------------------------------------------
    # Lazy CCXT exchange init
    # ------------------------------------------------------------------

    def _get_exchange(self, market: str = "spot") -> ccxt.Exchange:
        """Return (and cache) a CCXT exchange instance per market type."""
        if market not in self._exchanges:
            cls = getattr(ccxt, self.exchange_id)
            options: dict[str, Any] = {}
            if market == "perp":
                options["defaultType"] = "swap"
            config: dict[str, Any] = {
                "options": options,
                "enableRateLimit": True,
            }
            # Pass HTTP proxy from environment so CCXT works in
            # environments where DNS only resolves via proxy.
            proxy = os.environ.get(
                "HTTPS_PROXY", os.environ.get("https_proxy", ""),
            )
            if proxy:
                config["proxies"] = {"https": proxy, "http": proxy}
            self._exchanges[market] = cls(config)
        return self._exchanges[market]

    # ------------------------------------------------------------------
    # AC1: OHLCV fetching
    # ------------------------------------------------------------------

    def _resolve_perp_symbol(self, token: str) -> str:
        """Resolve the correct perp symbol for Binance-style exchanges.

        Some tokens trade as 1000TOKEN on perps (e.g. PEPE → 1000PEPE).
        Uses CCXT's loaded markets to find the correct symbol.
        """
        exchange = self._get_exchange("perp")
        if not exchange.markets:
            exchange.load_markets()

        # Try the standard symbol first
        standard = f"{token}:USDT"
        if standard in exchange.markets:
            return standard

        # Try 1000-prefixed variant (Binance meme coins)
        base = token.split("/")[0] if "/" in token else token
        variant = f"1000{base}/USDT:USDT"
        if variant in exchange.markets:
            return variant

        return standard  # fall back, let CCXT raise if wrong

    def _is_1000x_symbol(self, token: str) -> bool:
        """Check if the perp symbol uses a 1000x multiplier."""
        exchange = self._get_exchange("perp")
        if not exchange.markets:
            exchange.load_markets()
        standard = f"{token}:USDT"
        if standard in exchange.markets:
            return False
        base = token.split("/")[0] if "/" in token else token
        variant = f"1000{base}/USDT:USDT"
        return variant in exchange.markets

    def fetch_ohlcv(
        self,
        token: str,
        market: str = "spot",
        timeframe: str = "1h",
        limit: int = 10,
    ) -> list[dict]:
        """Fetch recent OHLCV candles from the exchange.

        Returns a list of bar dicts with keys:
        timestamp, open, high, low, close, volume.

        For perp tokens that trade as 1000TOKEN (e.g. 1000PEPE), prices
        are divided by 1000 so they match spot scale.
        """
        exchange = self._get_exchange(market)
        if market == "perp":
            symbol = self._resolve_perp_symbol(token)
            scale = 1000.0 if self._is_1000x_symbol(token) else 1.0
        else:
            symbol = token
            scale = 1.0
        raw = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        bars = []
        for candle in raw:
            bars.append({
                "timestamp": int(candle[0]),
                "open": float(candle[1]) / scale,
                "high": float(candle[2]) / scale,
                "low": float(candle[3]) / scale,
                "close": float(candle[4]) / scale,
                "volume": float(candle[5]) * scale,
            })
        return bars

    # ------------------------------------------------------------------
    # AC1: JSONL WAL append (never touch parquets)
    # ------------------------------------------------------------------

    def _wal_path(self, token: str, market: str) -> str:
        prefix = _token_prefix(token)
        return os.path.join(
            self.data_dir, "live", market, f"{prefix}_live.jsonl",
        )

    def _existing_timestamps(self, path: str) -> set[int]:
        """Read all timestamps already present in a WAL file."""
        timestamps: set[int] = set()
        if not os.path.exists(path):
            return timestamps
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        row = json.loads(line)
                        timestamps.add(int(row["timestamp"]))
                    except (json.JSONDecodeError, KeyError):
                        continue
        return timestamps

    def append_to_wal(
        self, token: str, market: str, bars: list[dict],
    ) -> None:
        """Append bars to the JSONL WAL, deduplicating on timestamp."""
        path = self._wal_path(token, market)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        existing = self._existing_timestamps(path)
        with open(path, "a") as f:
            for bar in bars:
                ts = int(bar["timestamp"])
                if ts not in existing:
                    f.write(json.dumps(bar) + "\n")
                    existing.add(ts)

    # ------------------------------------------------------------------
    # AC2: Funding rate fetching
    # ------------------------------------------------------------------

    def fetch_funding_rates(self, token: str) -> list[dict]:
        """Fetch recent funding rate records for a perpetual."""
        exchange = self._get_exchange(market="perp")
        symbol = self._resolve_perp_symbol(token)
        raw = exchange.fetch_funding_rate_history(symbol, limit=10)
        rates = []
        for entry in raw:
            rates.append({
                "timestamp": int(entry.get("timestamp", 0)),
                "symbol": entry.get("symbol", symbol),
                "fundingRate": float(entry.get("fundingRate", 0)),
            })
        return rates

    def _funding_wal_path(self, token: str) -> str:
        prefix = _token_prefix(token)
        return os.path.join(
            self.data_dir, "live", "funding", f"{prefix}_funding.jsonl",
        )

    def append_funding_wal(self, token: str, rates: list[dict]) -> None:
        """Append funding rate records to a separate JSONL WAL."""
        path = self._funding_wal_path(token)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        existing = self._existing_timestamps(path)
        with open(path, "a") as f:
            for rate in rates:
                ts = int(rate["timestamp"])
                if ts not in existing:
                    f.write(json.dumps(rate) + "\n")
                    existing.add(ts)

    # ------------------------------------------------------------------
    # AC3: Bar-close gating
    # ------------------------------------------------------------------

    def is_bar_closed(
        self, bar_timestamp_ms: int, timeframe: str = "1h",
    ) -> bool:
        """Return True if the candle at bar_timestamp_ms is fully closed.

        A bar is closed if its close time (bar_ts + timeframe_ms) is in the
        past.
        """
        tf_ms = _TF_MS.get(timeframe, 3_600_000)
        close_time_ms = bar_timestamp_ms + tf_ms
        now_ms = int(time.time() * 1000)
        return close_time_ms <= now_ms

    def filter_closed_bars(
        self, bars: list[dict], timeframe: str = "1h",
    ) -> list[dict]:
        """Return only bars whose candles are fully closed."""
        return [
            b for b in bars
            if self.is_bar_closed(b["timestamp"], timeframe)
        ]

    # ------------------------------------------------------------------
    # AC4: Gap detection and backfill
    # ------------------------------------------------------------------

    def detect_gaps(
        self, bars: list[dict], timeframe: str = "1h",
    ) -> list[int]:
        """Detect missing bar timestamps in a sorted sequence.

        Returns a list of missing timestamps (ms).
        """
        if len(bars) < 2:
            return []
        tf_ms = _TF_MS.get(timeframe, 3_600_000)
        timestamps = sorted(b["timestamp"] for b in bars)
        missing: list[int] = []
        for i in range(1, len(timestamps)):
            expected = timestamps[i - 1] + tf_ms
            while expected < timestamps[i]:
                missing.append(expected)
                expected += tf_ms
        return missing

    def backfill(
        self,
        token: str,
        market: str,
        missing_timestamps: list[int],
        timeframe: str = "1h",
    ) -> list[dict]:
        """Fetch bars for specific missing timestamps and append to WAL.

        Timestamps are snapped to the exchange's candle boundaries before
        fetching.  Each returned candle is stored with the original
        missing timestamp so it integrates with the caller's WAL.

        Returns the fetched bars.
        """
        if not missing_timestamps:
            return []
        tf_ms = _TF_MS.get(timeframe, 3_600_000)
        exchange = self._get_exchange(market)
        if market == "perp":
            symbol = self._resolve_perp_symbol(token)
        else:
            symbol = token

        # Snap requested timestamps to their containing candle boundary.
        # E.g. 22:13:20 → 22:00:00  (floor to timeframe)
        snapped = {ts: (ts // tf_ms) * tf_ms for ts in missing_timestamps}

        since = min(snapped.values())
        limit = len(missing_timestamps) + 5
        raw = exchange.fetch_ohlcv(
            symbol, timeframe=timeframe, since=since, limit=limit,
        )
        candle_map = {int(c[0]): c for c in raw}

        filled: list[dict] = []
        for orig_ts, snap_ts in snapped.items():
            if snap_ts in candle_map:
                c = candle_map[snap_ts]
                bar = {
                    "timestamp": orig_ts,
                    "open": float(c[1]),
                    "high": float(c[2]),
                    "low": float(c[3]),
                    "close": float(c[4]),
                    "volume": float(c[5]),
                }
                filled.append(bar)

        self.append_to_wal(token=token, market=market, bars=filled)
        return filled
