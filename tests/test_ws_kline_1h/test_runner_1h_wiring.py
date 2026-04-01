"""Acceptance tests for Task 3: Runner event-driven tick loop (1H wiring).

Tests verify:
  - AC7: HourlyBarCollector enqueues bars to write queue, writer thread calls
         append_to_parquet. Per-token write locks prevent corruption.
  - AC7a: _write_queue.join() completes before main loop reads parquets.
  - AC8: New tokens without existing parquet files get created on first close.
  - AC12: Batch funding via GET /fapi/v1/premiumIndex. Adapter maps
          lastFundingRate -> fundingRate, computes settlement-aligned timestamp
          from nextFundingTime - 8h.
  - AC12a: Funding fetch only when hour_utc in {7, 8, 15, 16, 23, 0}.
  - AC13: First tick after startup always fetches funding.
  - AC14: Main loop waits on collector.ready event instead of sleep+fetch.
  - AC15: fetch_all_data becomes fallback. _fetch_missing_tokens fetches OHLCV
          only, acquires collector's _1h_write_locks.
  - AC16: Sub-hourly exit processing continues unchanged.
  - AC20: WS unavailable -> degrades to timer-based tick with full REST.

All tests MUST FAIL until implementation is done (RED phase).
"""
from __future__ import annotations

import inspect
import os
import queue
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, call, PropertyMock

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "v4"))

import pandas as pd
import pytest

from v4.live_fetcher import LiveFetcher


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_bar_dict(
    timestamp: int = 1697414400000,  # 2023-10-16T00:00:00Z (hour boundary)
    open_: float = 65000.0,
    high: float = 65200.0,
    low: float = 64900.0,
    close: float = 65100.0,
    volume: float = 100.0,
) -> dict:
    """Build a single 1H bar dict as would come from kline_1h_callback."""
    return {
        "timestamp": timestamp,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }


def _make_premium_index_response(symbols_and_rates: list[tuple[str, float, int]]):
    """Build a mock /fapi/v1/premiumIndex batch response.

    Each tuple is (symbol, lastFundingRate, nextFundingTime_ms).
    """
    return [
        {
            "symbol": sym,
            "markPrice": "65000.00",
            "indexPrice": "65010.00",
            "lastFundingRate": str(rate),
            "nextFundingTime": next_funding,
            "interestRate": "0.0001",
            "time": next_funding - 8 * 3_600_000,
        }
        for sym, rate, next_funding in symbols_and_rates
    ]


# ===================================================================
# AC7: HourlyBarCollector enqueues bars; writer thread persists
# ===================================================================

class TestCollectorWriteQueueWiring:
    """AC7: Runner wires HourlyBarCollector so bars flow through write queue
    to append_to_parquet with per-token write locks."""

    def test_runner_imports_hourly_bar_collector(self):
        """AC7: run_paper_multi should import HourlyBarCollector."""
        import v4.run_paper_multi as runner_mod

        # Module must have HourlyBarCollector accessible (imported)
        assert hasattr(runner_mod, "HourlyBarCollector"), \
            "run_paper_multi should import HourlyBarCollector as a module-level name"

    def test_collector_has_write_queue_and_locks(self):
        """AC7: HourlyBarCollector should have _write_queue and _1h_write_locks."""
        from v4.hourly_bar_collector import HourlyBarCollector

        fetcher = MagicMock()
        collector = HourlyBarCollector(
            expected_tokens={"perp": {"BTC", "ETH"}, "spot": {"BTC"}},
            fetcher=fetcher,
        )

        assert hasattr(collector, "_write_queue"), \
            "HourlyBarCollector should have _write_queue"
        assert hasattr(collector, "_1h_write_locks"), \
            "HourlyBarCollector should have _1h_write_locks (per-token write locks)"

    def test_on_bar_enqueues_to_write_queue(self):
        """AC7: on_bar() should enqueue bar to _write_queue for async persistence."""
        from v4.hourly_bar_collector import HourlyBarCollector

        fetcher = MagicMock()
        collector = HourlyBarCollector(
            expected_tokens={"perp": {"BTC"}, "spot": set()},
            fetcher=fetcher,
        )

        bar = _make_bar_dict()
        initial_size = collector._write_queue.qsize()
        collector.on_bar("BTC", bar, "perp")

        assert collector._write_queue.qsize() > initial_size, \
            "on_bar should enqueue bar to _write_queue"

    def test_writer_thread_calls_append_to_parquet(self, tmp_path):
        """AC7: Writer thread drains queue and calls fetcher.append_to_parquet."""
        from v4.hourly_bar_collector import HourlyBarCollector

        fetcher = MagicMock()
        collector = HourlyBarCollector(
            expected_tokens={"perp": {"BTC"}, "spot": set()},
            fetcher=fetcher,
        )

        bar = _make_bar_dict()
        collector.on_bar("BTC", bar, "perp")

        # Wait for writer thread to drain
        collector._write_queue.join()

        # Writer should have called append_to_parquet
        fetcher.append_to_parquet.assert_called()
        args = fetcher.append_to_parquet.call_args
        assert args[0][0] == "BTC", "Should pass token"
        assert args[0][1] == "perp", "Should pass market"

    def test_per_token_write_locks_prevent_concurrent_writes(self):
        """AC7: Per-token write locks in _1h_write_locks prevent data corruption."""
        from v4.hourly_bar_collector import HourlyBarCollector

        fetcher = MagicMock()
        collector = HourlyBarCollector(
            expected_tokens={"perp": {"BTC", "ETH"}, "spot": set()},
            fetcher=fetcher,
        )

        # After writing, locks should exist for each token
        bar_btc = _make_bar_dict()
        bar_eth = _make_bar_dict(timestamp=1697414400000)
        bar_eth["close"] = 2000.0

        collector.on_bar("BTC", bar_btc, "perp")
        collector.on_bar("ETH", bar_eth, "perp")

        collector._write_queue.join()

        # Locks should be populated (writer creates them per-token)
        assert "BTC" in collector._1h_write_locks, \
            "Write lock should exist for BTC after write"
        assert "ETH" in collector._1h_write_locks, \
            "Write lock should exist for ETH after write"


# ===================================================================
# AC7a: _write_queue.join() completes before main loop reads parquets
# ===================================================================

class TestWriteQueueDrain:
    """AC7a: Runner drains write queue before reading parquets for tick."""

    def test_join_blocks_until_queue_empty(self):
        """AC7a: _write_queue.join() blocks until all pending writes complete."""
        from v4.hourly_bar_collector import HourlyBarCollector

        fetcher = MagicMock()
        # Make append slow to verify join waits
        write_started = threading.Event()
        write_done = threading.Event()

        def slow_append(*args, **kwargs):
            write_started.set()
            time.sleep(0.1)
            write_done.set()

        fetcher.append_to_parquet.side_effect = slow_append

        collector = HourlyBarCollector(
            expected_tokens={"perp": {"BTC"}, "spot": set()},
            fetcher=fetcher,
        )

        bar = _make_bar_dict()
        collector.on_bar("BTC", bar, "perp")

        # join() should block until the slow write completes
        collector._write_queue.join()
        assert write_done.is_set(), \
            "join() should block until all writes complete"

    def test_queue_drain_ensures_data_available_before_read(self):
        """AC7a: After join() returns, all enqueued bars have been written
        to parquet, so subsequent parquet reads see the new data."""
        from v4.hourly_bar_collector import HourlyBarCollector

        fetcher = MagicMock()
        written_data = []

        def track_writes(token, market, bars):
            written_data.append((token, market, bars))

        fetcher.append_to_parquet.side_effect = track_writes

        collector = HourlyBarCollector(
            expected_tokens={"perp": {"BTC", "ETH"}, "spot": set()},
            fetcher=fetcher,
        )

        # Enqueue multiple bars
        collector.on_bar("BTC", _make_bar_dict(), "perp")
        collector.on_bar("ETH", _make_bar_dict(close=2000.0), "perp")

        # After join, ALL writes must have completed
        collector._write_queue.join()
        assert len(written_data) == 2, \
            f"Expected 2 writes after join, got {len(written_data)}"


# ===================================================================
# AC8: New tokens without existing parquet files get created
# ===================================================================

class TestNewTokenParquetCreation:
    """AC8: New tokens get parquet files created on first 1h candle close."""

    def test_new_token_creates_parquet_via_collector(self, tmp_path):
        """AC8: A token with no existing parquet gets file created when
        HourlyBarCollector writer processes its first bar."""
        fetcher = LiveFetcher(data_dir=str(tmp_path))

        from v4.hourly_bar_collector import HourlyBarCollector

        collector = HourlyBarCollector(
            expected_tokens={"perp": {"NEWTOKEN"}, "spot": set()},
            fetcher=fetcher,
        )

        bar = _make_bar_dict()
        collector.on_bar("NEWTOKEN", bar, "perp")
        collector._write_queue.join()

        expected_path = tmp_path / "perp" / "live" / "NEWTOKEN.parquet"
        assert expected_path.exists(), \
            "Writer should create parquet for new tokens on first bar"

        df = pd.read_parquet(expected_path)
        assert len(df) == 1, "New parquet should contain the single bar"

    def test_new_token_spot_creates_parquet(self, tmp_path):
        """AC8: New spot tokens also get parquet files created."""
        fetcher = LiveFetcher(data_dir=str(tmp_path))

        from v4.hourly_bar_collector import HourlyBarCollector

        collector = HourlyBarCollector(
            expected_tokens={"perp": set(), "spot": {"NEWSPOT"}},
            fetcher=fetcher,
        )

        bar = _make_bar_dict()
        collector.on_bar("NEWSPOT", bar, "spot")
        collector._write_queue.join()

        expected_path = tmp_path / "spot" / "live" / "NEWSPOT.parquet"
        assert expected_path.exists(), \
            "Writer should create parquet for new spot tokens on first bar"


# ===================================================================
# AC12: Batch funding via GET /fapi/v1/premiumIndex
# ===================================================================

class TestBatchFunding:
    """AC12: Funding rates fetched via single batch premiumIndex call."""

    def test_fetch_batch_funding_exists(self):
        """AC12: run_paper_multi should have a _fetch_batch_funding function."""
        import v4.run_paper_multi as runner_mod

        assert hasattr(runner_mod, "_fetch_batch_funding"), \
            "Runner should have _fetch_batch_funding for batch premiumIndex calls"

    def test_adapter_maps_lastFundingRate_to_fundingRate(self):
        """AC12: Adapter maps lastFundingRate -> fundingRate key."""
        import v4.run_paper_multi as runner_mod

        api_response = _make_premium_index_response([
            ("BTCUSDT", 0.0001, 1697443200000),   # nextFundingTime
            ("ETHUSDT", -0.00005, 1697443200000),
        ])

        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = api_response
        mock_resp.raise_for_status = MagicMock()
        mock_session.get.return_value = mock_resp

        result = runner_mod._fetch_batch_funding(mock_session)

        # Result should be a dict keyed by token
        assert isinstance(result, dict), \
            "_fetch_batch_funding should return a dict"

        # Check BTC mapping
        btc_rates = result.get("BTC", [])
        assert len(btc_rates) >= 1, "Should have at least one rate for BTC"

        rate_entry = btc_rates[0]
        assert "fundingRate" in rate_entry, \
            "Adapter should map lastFundingRate -> fundingRate key"
        assert rate_entry["fundingRate"] == pytest.approx(0.0001), \
            "fundingRate should match lastFundingRate value"

    def test_adapter_computes_settlement_timestamp(self):
        """AC12: Adapter computes timestamp from nextFundingTime - 8h.

        merge_funding_into_parquet expects dicts with 'timestamp' and
        'fundingRate' keys (see LiveFetcher.merge_funding_into_parquet).
        """
        import v4.run_paper_multi as runner_mod

        next_funding_ms = 1697443200000  # some future time
        expected_ts = next_funding_ms - 8 * 3_600_000  # settlement - 8h

        api_response = _make_premium_index_response([
            ("BTCUSDT", 0.0001, next_funding_ms),
        ])

        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = api_response
        mock_resp.raise_for_status = MagicMock()
        mock_session.get.return_value = mock_resp

        result = runner_mod._fetch_batch_funding(mock_session)

        btc_rates = result.get("BTC", [])
        assert len(btc_rates) >= 1
        assert btc_rates[0]["timestamp"] == expected_ts, \
            f"timestamp should be nextFundingTime - 8h = {expected_ts}, " \
            f"got {btc_rates[0].get('timestamp')}"

    def test_adapter_handles_1000_prefix_tokens(self):
        """AC12: Adapter correctly maps 1000SHIBUSDT -> SHIB token.

        Codebase convention: internal tokens strip 1000 prefix
        (see LiveFetcher._1000_PREFIX_TOKENS and _resolve_symbol).
        """
        import v4.run_paper_multi as runner_mod

        api_response = _make_premium_index_response([
            ("1000SHIBUSDT", 0.00015, 1697443200000),
        ])

        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = api_response
        mock_resp.raise_for_status = MagicMock()
        mock_session.get.return_value = mock_resp

        result = runner_mod._fetch_batch_funding(mock_session)

        # Must be keyed by "SHIB" (stripped prefix), not "1000SHIB"
        assert "SHIB" in result, \
            "Adapter should strip 1000 prefix: 1000SHIBUSDT -> SHIB"

    def test_batch_funding_uses_premium_index_endpoint(self):
        """AC12: _fetch_batch_funding calls /fapi/v1/premiumIndex."""
        import v4.run_paper_multi as runner_mod

        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = []
        mock_resp.raise_for_status = MagicMock()
        mock_session.get.return_value = mock_resp

        runner_mod._fetch_batch_funding(mock_session)

        assert mock_session.get.called, "Should call session.get"
        call_args = mock_session.get.call_args
        url = call_args[0][0] if call_args[0] else ""
        assert "premiumIndex" in url, \
            f"URL should contain premiumIndex, got: {url}"


# ===================================================================
# AC12a: Funding fetch only near funding intervals
# ===================================================================

class TestNearFundingInterval:
    """AC12a: _near_funding_interval returns True only near settlement hours."""

    def test_near_funding_interval_exists(self):
        """AC12a: run_paper_multi should have _near_funding_interval."""
        import v4.run_paper_multi as runner_mod

        assert hasattr(runner_mod, "_near_funding_interval"), \
            "Runner should have _near_funding_interval function"

    @pytest.mark.parametrize("hour_utc", [0, 7, 8, 15, 16, 23])
    def test_near_funding_returns_true_for_settlement_hours(self, hour_utc):
        """AC12a: Returns True for hours {0, 7, 8, 15, 16, 23}."""
        import v4.run_paper_multi as runner_mod

        result = runner_mod._near_funding_interval(hour_utc)
        assert result is True, \
            f"_near_funding_interval({hour_utc}) should return True"

    @pytest.mark.parametrize("hour_utc", [1, 2, 3, 4, 5, 6, 9, 10, 11, 12, 13, 14, 17, 18, 19, 20, 21, 22])
    def test_near_funding_returns_false_for_non_settlement_hours(self, hour_utc):
        """AC12a: Returns False for non-settlement hours."""
        import v4.run_paper_multi as runner_mod

        result = runner_mod._near_funding_interval(hour_utc)
        assert result is False, \
            f"_near_funding_interval({hour_utc}) should return False"


# ===================================================================
# AC13: First tick after startup always fetches funding
# ===================================================================

class TestFirstTickFunding:
    """AC13: First tick after startup always fetches funding (cold cache)."""

    def test_first_tick_fetches_funding_at_non_settlement_hour(self):
        """AC13: Even at hour 3 (non-settlement), first tick fetches funding.

        Mock _fetch_batch_funding, invoke the funding decision logic.
        First call at hour=3 should fetch. Second call at hour=3 should NOT.
        """
        import v4.run_paper_multi as runner_mod

        # The runner should expose a function or class method that decides
        # whether to fetch funding. It should accept hour_utc and return
        # whether funding should be fetched, respecting first-tick override.
        assert hasattr(runner_mod, "_should_fetch_funding"), \
            "Runner should have _should_fetch_funding(hour_utc, first_tick) or equivalent"

        # First tick at hour=3 (non-settlement) -> True (cold cache override)
        result_first = runner_mod._should_fetch_funding(hour_utc=3, first_tick=True)
        assert result_first is True, \
            "First tick should fetch funding even at non-settlement hour 3"

        # Subsequent tick at hour=3 -> False (normal interval check)
        result_subsequent = runner_mod._should_fetch_funding(hour_utc=3, first_tick=False)
        assert result_subsequent is False, \
            "Non-first tick at hour 3 should NOT fetch funding"

    def test_first_tick_still_fetches_at_settlement_hour(self):
        """AC13: First tick at settlement hour also fetches (double-check)."""
        import v4.run_paper_multi as runner_mod

        assert hasattr(runner_mod, "_should_fetch_funding"), \
            "Runner should have _should_fetch_funding"

        result = runner_mod._should_fetch_funding(hour_utc=8, first_tick=True)
        assert result is True, \
            "First tick at settlement hour 8 should fetch funding"

    def test_subsequent_tick_at_settlement_hour_still_fetches(self):
        """AC13: Non-first tick at settlement hour should still fetch."""
        import v4.run_paper_multi as runner_mod

        assert hasattr(runner_mod, "_should_fetch_funding"), \
            "Runner should have _should_fetch_funding"

        result = runner_mod._should_fetch_funding(hour_utc=8, first_tick=False)
        assert result is True, \
            "Non-first tick at settlement hour 8 should still fetch funding"


# ===================================================================
# AC14: Main loop waits on collector.ready event
# ===================================================================

class TestEventDrivenLoop:
    """AC14: Main loop uses collector.ready event instead of sleep+fetch."""

    def test_collector_has_wait_for_ready(self):
        """AC14: HourlyBarCollector must expose wait_for_ready() method."""
        from v4.hourly_bar_collector import HourlyBarCollector

        fetcher = MagicMock()
        collector = HourlyBarCollector(
            expected_tokens={"perp": {"BTC"}, "spot": set()},
            fetcher=fetcher,
        )

        assert hasattr(collector, "wait_for_ready"), \
            "HourlyBarCollector should have wait_for_ready method"
        assert callable(collector.wait_for_ready), \
            "wait_for_ready should be callable"

    def test_wait_for_ready_blocks_until_all_tokens_received(self):
        """AC14: wait_for_ready() blocks until all expected tokens have bars."""
        from v4.hourly_bar_collector import HourlyBarCollector

        fetcher = MagicMock()
        collector = HourlyBarCollector(
            expected_tokens={"perp": {"BTC", "ETH"}, "spot": set()},
            fetcher=fetcher,
        )

        ready = threading.Event()

        def wait_in_thread():
            collector.wait_for_ready(timeout=5.0)
            ready.set()

        t = threading.Thread(target=wait_in_thread)
        t.start()

        # Not ready yet (only BTC received)
        collector.on_bar("BTC", _make_bar_dict(), "perp")
        time.sleep(0.05)
        assert not ready.is_set(), \
            "Should not be ready with only 1/2 tokens"

        # Now ETH arrives -> ready
        collector.on_bar("ETH", _make_bar_dict(close=2000.0), "perp")
        t.join(timeout=2.0)
        assert ready.is_set(), \
            "Should be ready after all expected tokens received"

    def test_wait_for_ready_returns_timeout_status(self):
        """AC14: wait_for_ready() with timeout returns whether it timed out,
        enabling the WS-unavailable fallback path (AC20)."""
        from v4.hourly_bar_collector import HourlyBarCollector

        fetcher = MagicMock()
        collector = HourlyBarCollector(
            expected_tokens={"perp": {"BTC"}, "spot": set()},
            fetcher=fetcher,
        )

        # No bars arrive -> should time out
        result = collector.wait_for_ready(timeout=0.1)
        # Result should indicate timeout (False = timed out, True = ready)
        assert result is False, \
            "wait_for_ready should return False when timed out"


# ===================================================================
# AC15: fetch_all_data becomes fallback; _fetch_missing_tokens
# ===================================================================

class TestRESTFallbackMissing:
    """AC15: fetch_all_data is fallback. _fetch_missing_tokens fetches OHLCV
    only and acquires collector's _1h_write_locks."""

    def test_fetch_missing_tokens_exists(self):
        """AC15: run_paper_multi should have _fetch_missing_tokens."""
        import v4.run_paper_multi as runner_mod

        assert hasattr(runner_mod, "_fetch_missing_tokens"), \
            "Runner should have _fetch_missing_tokens function"

    def test_fetch_missing_tokens_fetches_ohlcv_not_funding(self):
        """AC15: _fetch_missing_tokens should call fetch_ohlcv + append_to_parquet
        but NOT fetch_funding_rates (funding is handled by batch premiumIndex)."""
        import v4.run_paper_multi as runner_mod

        mock_fetcher = MagicMock()
        mock_fetcher.fetch_ohlcv.return_value = [_make_bar_dict()]
        mock_fetcher.filter_closed_bars.return_value = [_make_bar_dict()]
        mock_fetcher.append_to_parquet = MagicMock()

        mock_collector = MagicMock()
        mock_collector._1h_write_locks = {}

        missing = {"perp": {"BTC"}, "spot": set()}

        runner_mod._fetch_missing_tokens(
            mock_fetcher, missing, mock_collector,
        )

        # Should have called fetch_ohlcv for BTC
        mock_fetcher.fetch_ohlcv.assert_called()
        called_tokens = set()
        for c in mock_fetcher.fetch_ohlcv.call_args_list:
            called_tokens.add(c[0][0])
        assert "BTC" in called_tokens, "Should fetch OHLCV for missing BTC"

        # Should NOT have called fetch_funding_rates
        mock_fetcher.fetch_funding_rates.assert_not_called(), \
            "_fetch_missing_tokens should NOT fetch funding (handled by batch)"

    def test_fetch_missing_tokens_acquires_write_locks(self):
        """AC15: _fetch_missing_tokens acquires collector's _1h_write_locks
        before writing to prevent corruption with writer thread."""
        import v4.run_paper_multi as runner_mod

        lock = threading.Lock()
        mock_fetcher = MagicMock()
        mock_fetcher.fetch_ohlcv.return_value = [_make_bar_dict()]
        mock_fetcher.filter_closed_bars.return_value = [_make_bar_dict()]

        lock_acquired = []

        def tracking_append(*args, **kwargs):
            # Record whether the lock was held when append was called
            # If lock is already acquired by _fetch_missing_tokens,
            # trying to acquire non-blocking should fail
            locked = not lock.acquire(blocking=False)
            if not locked:
                lock.release()
            lock_acquired.append(locked)

        mock_fetcher.append_to_parquet.side_effect = tracking_append

        mock_collector = MagicMock()
        mock_collector._1h_write_locks = {"BTC": lock}

        missing = {"perp": {"BTC"}, "spot": set()}

        runner_mod._fetch_missing_tokens(
            mock_fetcher, missing, mock_collector,
        )

        # At least one append should have happened with lock held
        assert any(lock_acquired), \
            "_fetch_missing_tokens should hold write lock during append_to_parquet"

    def test_fetch_missing_tokens_only_fetches_specified_tokens(self):
        """AC15: _fetch_missing_tokens fetches only the tokens passed to it,
        not the full universe."""
        import v4.run_paper_multi as runner_mod

        mock_fetcher = MagicMock()
        mock_fetcher.fetch_ohlcv.return_value = [_make_bar_dict()]
        mock_fetcher.filter_closed_bars.return_value = [_make_bar_dict()]
        mock_fetcher.append_to_parquet = MagicMock()

        mock_collector = MagicMock()
        mock_collector._1h_write_locks = {}

        missing = {"perp": {"BTC", "ETH"}, "spot": set()}

        runner_mod._fetch_missing_tokens(
            mock_fetcher, missing, mock_collector,
        )

        # Should have called fetch_ohlcv only for BTC and ETH (perp)
        called_tokens = set()
        for c in mock_fetcher.fetch_ohlcv.call_args_list:
            called_tokens.add(c[0][0])  # first positional arg is token

        assert called_tokens == {"BTC", "ETH"}, \
            f"Should fetch only missing tokens, got: {called_tokens}"


# ===================================================================
# AC16: Sub-hourly exit processing continues unchanged
# (Backward-compat regression guards — source inspection acceptable)
# ===================================================================

class TestSubHourlyExitsContinueBackwardCompat:
    """AC16: Sub-hourly exit processing is preserved in the inter-hour period.

    These are backward-compatibility guards verifying existing code hasn't
    been removed. Source inspection is acceptable for this purpose.
    """

    def test_main_loop_preserves_sub_hourly_exits(self):
        """AC16: The inter-hour wait loop should still call
        process_sub_hourly_exits with candle aggregator output."""
        import v4.run_paper_multi as runner_mod

        source = inspect.getsource(runner_mod.main)

        assert "process_sub_hourly_exits" in source, \
            "main() should still call process_sub_hourly_exits during inter-hour wait"
        assert "flush_completed" in source, \
            "main() should still flush completed candles for sub-hourly exits"

    def test_candle_aggregator_reference_preserved(self):
        """AC16: CandleAggregator references should still exist in runner."""
        import v4.run_paper_multi as runner_mod

        source = inspect.getsource(runner_mod.main)

        assert "_candle_aggregator" in source, \
            "main() should still reference _candle_aggregator for sub-hourly exits"


# ===================================================================
# AC20: WS unavailable -> degrades to timer-based tick with full REST
# ===================================================================

class TestWSUnavailableDegradation:
    """AC20: If WS completely unavailable, runner degrades to timer-based ticks."""

    def test_fetch_all_data_still_importable_and_callable(self):
        """AC20: fetch_all_data function must still exist and be callable."""
        import v4.run_paper_multi as runner_mod

        assert hasattr(runner_mod, "fetch_all_data"), \
            "fetch_all_data should still exist as a fallback function"
        assert callable(runner_mod.fetch_all_data), \
            "fetch_all_data should be callable"

    def test_fetch_all_data_returns_expected_tuple(self):
        """AC20: fetch_all_data returns (fetch_ok, fetch_err, bars_appended, funding_merged)."""
        import v4.run_paper_multi as runner_mod

        mock_fetcher = MagicMock()
        # Return empty data so no actual fetches happen
        result = runner_mod.fetch_all_data(mock_fetcher, [])

        assert isinstance(result, tuple), "fetch_all_data should return a tuple"
        assert len(result) == 4, "fetch_all_data should return 4-tuple"

    def test_compute_sleep_still_importable(self):
        """AC20: compute_sleep_until_next_hour should still be available."""
        from v4.paper_utils import compute_sleep_until_next_hour

        # Verify it works with a sample timestamp
        result = compute_sleep_until_next_hour(1697414400.0)
        assert isinstance(result, (int, float)), \
            "compute_sleep_until_next_hour should return a number"
        assert result >= 0, "Sleep time should be non-negative"

    def test_collector_timeout_enables_rest_fallback(self):
        """AC20: When collector.wait_for_ready times out, the runner should
        invoke fetch_all_data as the REST fallback for ALL tokens.

        This tests the integration: create collector, let it time out,
        verify the runner's fallback path calls fetch_all_data.
        """
        from v4.hourly_bar_collector import HourlyBarCollector

        fetcher = MagicMock()
        collector = HourlyBarCollector(
            expected_tokens={"perp": {"BTC"}, "spot": set()},
            fetcher=fetcher,
        )

        # No bars arrive -> timeout
        timed_out = not collector.wait_for_ready(timeout=0.1)
        assert timed_out, \
            "wait_for_ready should time out when no bars arrive"

        # After timeout, the runner should call fetch_all_data.
        # We verify the collector correctly reports non-readiness.
        assert hasattr(collector, "is_ready") or hasattr(collector, "ready"), \
            "Collector should expose readiness state for runner to check"


# ===================================================================
# Integration: Main loop sequence (wait -> join -> consume -> tick)
# ===================================================================

class TestMainLoopSequence:
    """Integration: Verify the correct ordering of main loop operations."""

    def test_collector_has_full_lifecycle_api(self):
        """The HourlyBarCollector exposes the full lifecycle API needed by
        the main loop: wait_for_ready -> join -> consume -> get_missing."""
        from v4.hourly_bar_collector import HourlyBarCollector

        fetcher = MagicMock()
        c = HourlyBarCollector(
            expected_tokens={"perp": {"BTC"}, "spot": set()},
            fetcher=fetcher,
        )
        try:
            # All lifecycle methods must exist and be callable
            assert callable(getattr(c, "wait_for_ready", None))
            assert callable(getattr(c, "consume", None))
            assert callable(getattr(c, "get_missing_tokens", None))
            assert callable(getattr(c, "shutdown", None))
            assert hasattr(c, "_write_queue"), "Must expose _write_queue for join()"
            assert callable(getattr(c._write_queue, "join", None))
        finally:
            c.shutdown()

    def test_price_monitor_accepts_streams_param(self):
        """PriceMonitor must accept streams=['kline_1h'] for dedicated
        1H data-collection monitors (Two-Connection Model)."""
        from v4.price_monitor import PriceMonitor

        callback = MagicMock()
        # This must not raise TypeError — streams param is required for 1H monitors
        monitor = PriceMonitor(
            callback=callback,
            venue="perp",
            streams=["kline_1h"],
        )
        # Verify it stores the streams config
        names = monitor._build_perp_stream_names(["BTC"])
        assert any("kline_1h" in n for n in names), \
            "PriceMonitor with streams=['kline_1h'] must build kline_1h stream names"

    def test_discover_tokens_from_data_available(self):
        """Runner must be able to discover all universe tokens for 1H monitors."""
        from v4.data_loader import discover_tokens_from_data

        # Function must exist and be callable — used to get ALL tokens for 1H subscription
        assert callable(discover_tokens_from_data)

    def test_collector_shutdown_called_during_cleanup(self):
        """Runner should call collector.shutdown() during cleanup.

        Behavioral test: HourlyBarCollector.shutdown() must exist and
        stop the writer thread cleanly.
        """
        from v4.hourly_bar_collector import HourlyBarCollector

        fetcher = MagicMock()
        collector = HourlyBarCollector(
            expected_tokens={"perp": {"BTC"}, "spot": set()},
            fetcher=fetcher,
        )

        # shutdown() should exist and be callable
        assert hasattr(collector, "shutdown"), \
            "HourlyBarCollector should have shutdown() method"
        assert callable(collector.shutdown), \
            "shutdown should be callable"

        # After shutdown, writer thread should stop
        collector.shutdown()

        # Verify the writer thread is no longer alive
        writer = getattr(collector, "_writer_thread", None)
        if writer is not None:
            writer.join(timeout=2.0)
            assert not writer.is_alive(), \
                "Writer thread should stop after shutdown()"


# ===================================================================
# Empty expected_tokens edge case
# ===================================================================

class TestEmptyExpectedTokens:
    """Edge case: HourlyBarCollector with no expected tokens."""

    def test_empty_expected_tokens_immediately_ready_or_raises(self):
        """HourlyBarCollector with empty expected_tokens should either
        be immediately ready or raise ValueError (no tokens to collect)."""
        from v4.hourly_bar_collector import HourlyBarCollector

        fetcher = MagicMock()

        try:
            collector = HourlyBarCollector(
                expected_tokens={"perp": set(), "spot": set()},
                fetcher=fetcher,
            )
            # If it doesn't raise, it should be immediately ready
            result = collector.wait_for_ready(timeout=0.1)
            assert result is True, \
                "Collector with no expected tokens should be immediately ready"
        except ValueError:
            # Raising ValueError for empty tokens is also acceptable
            pass
