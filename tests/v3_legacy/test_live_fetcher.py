"""AC1, AC2, AC3, AC4: Live data fetching from Binance via CCXT.

Tests verify:
- AC1: LiveFetcher polls Binance CCXT for 1H OHLCV (spot + perp),
        appends to JSONL WAL overlay (never modifies historical parquets)
- AC2: Fetcher also fetches perpetual funding rates
- AC3: Bar-close gating — verifies candle is fully closed before triggering engine
- AC4: Gap detection and backfill of missed bars
"""

import json
import os
import time

import pytest

from v3.live_fetcher import LiveFetcher


# ---------------------------------------------------------------------------
# AC1: OHLCV polling and JSONL WAL append
# ---------------------------------------------------------------------------


class TestOhlcvPolling:
    """LiveFetcher polls CCXT for 1H OHLCV candles."""

    def test_fetch_spot_ohlcv_returns_list_of_bars(self, tmp_path):
        """fetch_ohlcv('BTC/USDT', 'spot') returns a list of bar dicts."""
        fetcher = LiveFetcher(
            exchange="binance",
            data_dir=str(tmp_path / "data"),
        )
        bars = fetcher.fetch_ohlcv(token="BTC/USDT", market="spot")
        assert isinstance(bars, list)
        assert len(bars) > 0

    def test_fetch_perp_ohlcv_returns_list_of_bars(self, tmp_path):
        """fetch_ohlcv('BTC/USDT', 'perp') returns a list of bar dicts."""
        fetcher = LiveFetcher(
            exchange="binance",
            data_dir=str(tmp_path / "data"),
        )
        bars = fetcher.fetch_ohlcv(token="BTC/USDT", market="perp")
        assert isinstance(bars, list)
        assert len(bars) > 0

    def test_bar_has_required_fields(self, tmp_path):
        """Each bar must have timestamp, open, high, low, close, volume."""
        fetcher = LiveFetcher(
            exchange="binance",
            data_dir=str(tmp_path / "data"),
        )
        bars = fetcher.fetch_ohlcv(token="BTC/USDT", market="spot")
        bar = bars[0]
        for field in ("timestamp", "open", "high", "low", "close", "volume"):
            assert field in bar, f"Missing field: {field}"

    def test_bar_timestamp_is_integer_ms(self, tmp_path):
        """Timestamps should be integer milliseconds."""
        fetcher = LiveFetcher(
            exchange="binance",
            data_dir=str(tmp_path / "data"),
        )
        bars = fetcher.fetch_ohlcv(token="BTC/USDT", market="spot")
        ts = bars[0]["timestamp"]
        assert isinstance(ts, (int, float))
        # Reasonable range: > year 2020 in ms
        assert ts > 1_577_836_800_000


class TestJsonlWalAppend:
    """OHLCV data appends to JSONL WAL overlay, never modifies parquets."""

    def test_append_creates_jsonl_file(self, tmp_path):
        """Appending bars creates a JSONL WAL file at the expected path."""
        fetcher = LiveFetcher(
            exchange="binance",
            data_dir=str(tmp_path / "data"),
        )
        bars = [
            {"timestamp": 1700000000000, "open": 100, "high": 101,
             "low": 99, "close": 100.5, "volume": 1000},
        ]
        fetcher.append_to_wal(token="BTC/USDT", market="spot", bars=bars)
        wal_path = os.path.join(
            str(tmp_path / "data"), "live", "spot", "BTC_live.jsonl"
        )
        assert os.path.exists(wal_path)

    def test_append_writes_valid_jsonl(self, tmp_path):
        """Each line in the WAL file is valid JSON."""
        fetcher = LiveFetcher(
            exchange="binance",
            data_dir=str(tmp_path / "data"),
        )
        bars = [
            {"timestamp": 1700000000000, "open": 100, "high": 101,
             "low": 99, "close": 100.5, "volume": 1000},
            {"timestamp": 1700003600000, "open": 100.5, "high": 102,
             "low": 100, "close": 101.5, "volume": 1100},
        ]
        fetcher.append_to_wal(token="BTC/USDT", market="spot", bars=bars)
        wal_path = os.path.join(
            str(tmp_path / "data"), "live", "spot", "BTC_live.jsonl"
        )
        with open(wal_path, "r") as f:
            lines = [l for l in f.readlines() if l.strip()]
        assert len(lines) == 2
        for line in lines:
            parsed = json.loads(line.strip())
            assert "timestamp" in parsed

    def test_append_is_additive(self, tmp_path):
        """Multiple appends grow the file, never overwrite."""
        fetcher = LiveFetcher(
            exchange="binance",
            data_dir=str(tmp_path / "data"),
        )
        bar1 = [{"timestamp": 1700000000000, "open": 100, "high": 101,
                  "low": 99, "close": 100.5, "volume": 1000}]
        bar2 = [{"timestamp": 1700003600000, "open": 100.5, "high": 102,
                  "low": 100, "close": 101.5, "volume": 1100}]
        fetcher.append_to_wal(token="BTC/USDT", market="spot", bars=bar1)
        fetcher.append_to_wal(token="BTC/USDT", market="spot", bars=bar2)
        wal_path = os.path.join(
            str(tmp_path / "data"), "live", "spot", "BTC_live.jsonl"
        )
        with open(wal_path, "r") as f:
            lines = [l for l in f.readlines() if l.strip()]
        assert len(lines) == 2

    def test_append_does_not_modify_parquet(self, tmp_path):
        """Historical parquet files must not be modified by append."""
        # Pre-create a fake parquet file
        parquet_dir = tmp_path / "data" / "spot"
        parquet_dir.mkdir(parents=True)
        parquet_path = parquet_dir / "BTC.parquet"
        parquet_path.write_text("FAKE_PARQUET_CONTENT")
        original_mtime = os.path.getmtime(str(parquet_path))

        fetcher = LiveFetcher(
            exchange="binance",
            data_dir=str(tmp_path / "data"),
        )
        bars = [{"timestamp": 1700000000000, "open": 100, "high": 101,
                  "low": 99, "close": 100.5, "volume": 1000}]
        fetcher.append_to_wal(token="BTC/USDT", market="spot", bars=bars)

        # Parquet must be untouched
        assert os.path.getmtime(str(parquet_path)) == original_mtime
        assert parquet_path.read_text() == "FAKE_PARQUET_CONTENT"

    def test_deduplicates_on_append(self, tmp_path):
        """Appending bars with duplicate timestamps does not create duplicates."""
        fetcher = LiveFetcher(
            exchange="binance",
            data_dir=str(tmp_path / "data"),
        )
        bar = [{"timestamp": 1700000000000, "open": 100, "high": 101,
                 "low": 99, "close": 100.5, "volume": 1000}]
        fetcher.append_to_wal(token="BTC/USDT", market="spot", bars=bar)
        fetcher.append_to_wal(token="BTC/USDT", market="spot", bars=bar)
        wal_path = os.path.join(
            str(tmp_path / "data"), "live", "spot", "BTC_live.jsonl"
        )
        with open(wal_path, "r") as f:
            lines = [l for l in f.readlines() if l.strip()]
        # Should be 1, not 2
        assert len(lines) == 1


# ---------------------------------------------------------------------------
# AC2: Funding rate fetching
# ---------------------------------------------------------------------------


class TestFundingRateFetch:
    """Fetcher retrieves perpetual funding rates."""

    def test_fetch_funding_rates_returns_list(self, tmp_path):
        """fetch_funding_rates('BTC/USDT') returns a list of rate records."""
        fetcher = LiveFetcher(
            exchange="binance",
            data_dir=str(tmp_path / "data"),
        )
        rates = fetcher.fetch_funding_rates(token="BTC/USDT")
        assert isinstance(rates, list)

    def test_funding_rate_record_has_required_fields(self, tmp_path):
        """Each funding rate record has timestamp, symbol, fundingRate."""
        fetcher = LiveFetcher(
            exchange="binance",
            data_dir=str(tmp_path / "data"),
        )
        rates = fetcher.fetch_funding_rates(token="BTC/USDT")
        assert len(rates) > 0
        rate = rates[0]
        for field in ("timestamp", "symbol", "fundingRate"):
            assert field in rate, f"Missing field: {field}"

    def test_funding_rate_is_numeric(self, tmp_path):
        """Funding rate must be a float."""
        fetcher = LiveFetcher(
            exchange="binance",
            data_dir=str(tmp_path / "data"),
        )
        rates = fetcher.fetch_funding_rates(token="BTC/USDT")
        assert isinstance(rates[0]["fundingRate"], (int, float))

    def test_funding_rates_appended_to_wal(self, tmp_path):
        """Funding rates are persisted to a separate JSONL WAL."""
        fetcher = LiveFetcher(
            exchange="binance",
            data_dir=str(tmp_path / "data"),
        )
        rates = [
            {"timestamp": 1700000000000, "symbol": "BTC/USDT:USDT",
             "fundingRate": 0.0001},
        ]
        fetcher.append_funding_wal(token="BTC/USDT", rates=rates)
        funding_path = os.path.join(
            str(tmp_path / "data"), "live", "funding", "BTC_funding.jsonl"
        )
        assert os.path.exists(funding_path)
        with open(funding_path, "r") as f:
            line = f.readline().strip()
        parsed = json.loads(line)
        assert parsed["fundingRate"] == pytest.approx(0.0001)


# ---------------------------------------------------------------------------
# AC3: Bar-close gating
# ---------------------------------------------------------------------------


class TestBarCloseGating:
    """Verifies candle is fully closed before triggering engine."""

    def test_is_bar_closed_true_for_past_candle(self, tmp_path):
        """A candle whose close time is in the past is considered closed."""
        fetcher = LiveFetcher(
            exchange="binance",
            data_dir=str(tmp_path / "data"),
        )
        # 1H candle that closed 2 hours ago
        bar_ts = int((time.time() - 7200) * 1000)
        assert fetcher.is_bar_closed(bar_timestamp_ms=bar_ts, timeframe="1h") is True

    def test_is_bar_closed_false_for_current_candle(self, tmp_path):
        """A candle whose close time is in the future is NOT closed."""
        fetcher = LiveFetcher(
            exchange="binance",
            data_dir=str(tmp_path / "data"),
        )
        # 1H candle that closes 30 minutes from now
        bar_ts = int((time.time() + 1800) * 1000)
        assert fetcher.is_bar_closed(bar_timestamp_ms=bar_ts, timeframe="1h") is False

    def test_only_closed_bars_passed_to_engine(self, tmp_path):
        """filter_closed_bars removes unclosed candles."""
        fetcher = LiveFetcher(
            exchange="binance",
            data_dir=str(tmp_path / "data"),
        )
        now_ms = int(time.time() * 1000)
        bars = [
            {"timestamp": now_ms - 7_200_000, "close": 100},  # 2h ago — closed
            {"timestamp": now_ms - 3_600_000, "close": 101},  # 1h ago — closed
            {"timestamp": now_ms + 1_800_000, "close": 102},  # 30m future — NOT closed
        ]
        closed = fetcher.filter_closed_bars(bars, timeframe="1h")
        assert len(closed) == 2
        assert all(b["timestamp"] < now_ms for b in closed)


# ---------------------------------------------------------------------------
# AC4: Gap detection and backfill
# ---------------------------------------------------------------------------


class TestGapDetection:
    """Detects missed bars and backfills them."""

    def test_detect_gap_returns_missing_timestamps(self, tmp_path):
        """Given bars with a gap, detect_gaps returns the missing timestamps."""
        fetcher = LiveFetcher(
            exchange="binance",
            data_dir=str(tmp_path / "data"),
        )
        # 1H bars: 0, 1h, 2h, [gap at 3h], 4h
        base = 1700000000000
        bars = [
            {"timestamp": base},
            {"timestamp": base + 3_600_000},
            {"timestamp": base + 7_200_000},
            # gap: base + 10_800_000 missing
            {"timestamp": base + 14_400_000},
        ]
        gaps = fetcher.detect_gaps(bars, timeframe="1h")
        assert len(gaps) == 1
        assert gaps[0] == base + 10_800_000

    def test_no_gap_returns_empty(self, tmp_path):
        """Consecutive bars produce no gaps."""
        fetcher = LiveFetcher(
            exchange="binance",
            data_dir=str(tmp_path / "data"),
        )
        base = 1700000000000
        bars = [
            {"timestamp": base},
            {"timestamp": base + 3_600_000},
            {"timestamp": base + 7_200_000},
        ]
        gaps = fetcher.detect_gaps(bars, timeframe="1h")
        assert gaps == []

    def test_detect_multiple_gaps(self, tmp_path):
        """Multiple missing bars are all detected."""
        fetcher = LiveFetcher(
            exchange="binance",
            data_dir=str(tmp_path / "data"),
        )
        base = 1700000000000
        bars = [
            {"timestamp": base},
            # gap: base + 3_600_000
            # gap: base + 7_200_000
            {"timestamp": base + 10_800_000},
        ]
        gaps = fetcher.detect_gaps(bars, timeframe="1h")
        assert len(gaps) == 2

    def test_backfill_fetches_missing_bars(self, tmp_path):
        """backfill() fetches and appends missing bars to the WAL."""
        fetcher = LiveFetcher(
            exchange="binance",
            data_dir=str(tmp_path / "data"),
        )
        base = 1700000000000
        missing_timestamps = [base + 10_800_000]
        result = fetcher.backfill(
            token="BTC/USDT",
            market="spot",
            missing_timestamps=missing_timestamps,
        )
        # backfill returns the filled bars
        assert isinstance(result, list)
        assert len(result) == len(missing_timestamps)

    def test_backfill_appends_to_wal(self, tmp_path):
        """After backfill, the WAL contains the previously missing bar."""
        fetcher = LiveFetcher(
            exchange="binance",
            data_dir=str(tmp_path / "data"),
        )
        base = 1700000000000
        # Pre-populate WAL with bars that have a gap
        existing = [
            {"timestamp": base, "open": 100, "high": 101, "low": 99,
             "close": 100.5, "volume": 1000},
            {"timestamp": base + 7_200_000, "open": 101, "high": 102, "low": 100,
             "close": 101.5, "volume": 1100},
        ]
        fetcher.append_to_wal(token="BTC/USDT", market="spot", bars=existing)

        # Backfill the gap
        fetcher.backfill(
            token="BTC/USDT",
            market="spot",
            missing_timestamps=[base + 3_600_000],
        )

        wal_path = os.path.join(
            str(tmp_path / "data"), "live", "spot", "BTC_live.jsonl"
        )
        with open(wal_path, "r") as f:
            lines = [l for l in f.readlines() if l.strip()]
        timestamps = [json.loads(l)["timestamp"] for l in lines]
        assert base + 3_600_000 in timestamps
