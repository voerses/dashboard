"""M10 E6 — WS 429 backoff retry sequence (AC #25a — pytest half).

AC #25a scopes the deterministic retry-logic half of the brief's WS
rate-limit AC. The live-Binance half lives in
``tools/ws_ratelimit_parallel_smoke.sh`` (AC #25b, operator-run).

This test verifies the backoff sequence declared at
``v5/paper_engine.py:857`` (``BACKOFF = [30, 60, 120]``) fires correctly
under a mocked 429 response:

    * first 3 fetches raise a 429-shaped exception
    * 4th fetch succeeds
    * ``time.sleep`` is invoked with ``[30, 60, 120]`` in order
    * total fetch call count == 4

``time.sleep`` is patched so the test runs instantly regardless of
Phase-4 implementation choices (the real runner currently sleeps
``BACKOFF[attempt] * 0.001`` for test-speed; the Phase-4 production
path sleeps the full seconds).

MUST FAIL TODAY (RED):
    * Existing ``_fetch_with_retry`` at paper_engine.py:3671 swallows
      all exceptions generically — it does not distinguish 429 from
      other errors, does not sleep the canonical [30, 60, 120]
      sequence in seconds, and does not surface the retry outcome to
      callers via a testable seam.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _RateLimited(Exception):
    """Synthetic 429 marker. Phase 4 must detect 429 by status_code
    attribute or dedicated exception class — this marker is what the
    Phase-3 test contract expects the retry path to recognise.
    """

    status_code = 429


def _make_fetcher_429_then_200(success_payload):
    """Fetcher returning 429 on first 3 calls, then success_payload."""
    fetcher = MagicMock()
    fetcher.fetch_ohlcv.side_effect = [
        _RateLimited("rate-limited"),
        _RateLimited("rate-limited"),
        _RateLimited("rate-limited"),
        success_payload,
    ]
    return fetcher


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestBackoffConstantIsCanonical:
    """AC #25a — PaperPortfolioEngine.BACKOFF == [30, 60, 120]."""

    def test_backoff_constant_matches_brief(self) -> None:
        from v5.paper_engine import PaperPortfolioEngine

        assert PaperPortfolioEngine.BACKOFF == [30, 60, 120], (
            f"BACKOFF constant must be [30, 60, 120] per AC #25a; got "
            f"{PaperPortfolioEngine.BACKOFF!r}."
        )


class TestBackoffSequenceOnRateLimit:
    """AC #25a — 3 retries with [30, 60, 120] sleep sequence, then success."""

    def test_429_triggers_canonical_sleep_sequence(self) -> None:
        from v5.paper_engine import PaperPortfolioEngine

        expected_success = [{"ts": 1, "close": 68_000.0}]
        fetcher = _make_fetcher_429_then_200(expected_success)

        # Phase 4 exposes a classmethod / public seam that takes a fetcher +
        # returns the data on success (re-raises after max retries). Today
        # the only entry point is the instance method `_fetch_with_retry`
        # which takes no fetcher arg — so this import/call surface is the
        # RED contract Phase 4 must implement.
        from v5.paper_engine import fetch_with_backoff

        with patch("v5.paper_engine.time.sleep") as mock_sleep:
            result = fetch_with_backoff(fetcher=fetcher)

        assert result == expected_success
        assert fetcher.fetch_ohlcv.call_count == 4, (
            f"fetch_ohlcv must be called exactly 4 times "
            f"(3 failures + 1 success); got {fetcher.fetch_ohlcv.call_count}."
        )

        sleep_args = [call.args[0] for call in mock_sleep.call_args_list]
        assert sleep_args == [30, 60, 120], (
            f"time.sleep must be called with [30, 60, 120] in order; "
            f"got {sleep_args!r}."
        )


class TestBackoffGivesUpAfterMaxRetries:
    """AC #25a — exhausted retries re-raise/return None with alert."""

    def test_persistent_429_raises_after_three_attempts(self) -> None:
        from v5.paper_engine import fetch_with_backoff

        fetcher = MagicMock()
        fetcher.fetch_ohlcv.side_effect = _RateLimited("permanent-429")

        with patch("v5.paper_engine.time.sleep") as mock_sleep:
            with pytest.raises(_RateLimited):
                fetch_with_backoff(fetcher=fetcher, max_attempts=3)

        # 3 attempts → 2 sleeps between them (no sleep after final failure).
        # Either that shape OR 3 sleeps is acceptable; the invariant is
        # that every sleep argument is drawn from BACKOFF in order.
        from v5.paper_engine import PaperPortfolioEngine

        sleep_args = [call.args[0] for call in mock_sleep.call_args_list]
        assert all(
            arg in PaperPortfolioEngine.BACKOFF for arg in sleep_args
        ), (
            f"All sleep args must come from BACKOFF {PaperPortfolioEngine.BACKOFF}; "
            f"got {sleep_args!r}."
        )
        assert fetcher.fetch_ohlcv.call_count == 3
