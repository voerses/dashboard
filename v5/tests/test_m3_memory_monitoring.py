"""M3 acceptance tests — RSS memory monitoring in the paper runner.

Covers:
  - AC10: RSS logged every 60s in paper runner; WARNING at 1.2GB threshold.
          Also asserts `_read_rss_mb()` helper exists and returns a positive float.

All tests MUST FAIL today — no `_read_rss_mb` helper, no periodic RSS logging,
no 1.2GB warning logic.

Seed: 42. TestClock epoch: 2026-03-01T00:00:00Z.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


# ===================================================================
# AC10 — _read_rss_mb helper exists
# ===================================================================

class TestAC10ReadRSSHelper:
    """A helper returns the paper runner's current RSS in MB."""

    def test_read_rss_mb_importable(self):
        """Either `v5.paper_engine._read_rss_mb` or `v5.paper_utils._read_rss_mb`."""
        try:
            from v5.paper_engine import _read_rss_mb  # noqa: F401
            return
        except ImportError:
            pass
        try:
            from v5.paper_utils import _read_rss_mb  # noqa: F401
            return
        except ImportError:
            pass
        pytest.fail(
            "Expected _read_rss_mb in v5.paper_engine or v5.paper_utils"
        )

    def test_read_rss_mb_returns_positive_float(self):
        try:
            from v5.paper_engine import _read_rss_mb
        except ImportError:
            from v5.paper_utils import _read_rss_mb
        val = _read_rss_mb()
        assert isinstance(val, float)
        assert val > 0.0


# ===================================================================
# AC10 — periodic RSS log cadence (every 60s)
# ===================================================================

class TestAC10PeriodicRSSLog:
    """Engine logs RSS every 60s of wall clock. We fake the clock and RSS
    reader to observe the cadence without waiting 60 real seconds.
    """

    def test_rss_logged_at_least_once_per_60s_on_ticks(self, caplog):
        """Run two ticks separated by 65s of simulated time and assert a
        single RSS INFO log appears between the two."""
        from v5.paper_config import PaperConfig
        from v5.paper_engine import PaperPortfolioEngine

        config = PaperConfig(strategies=[])
        engine = PaperPortfolioEngine(config)

        caplog.set_level(logging.INFO)
        # Patch both the clock and the rss reader.
        fake_time_base = 1_770_000_000.0
        times = iter([fake_time_base, fake_time_base + 65.0])

        def _fake_time():
            try:
                return next(times)
            except StopIteration:
                return fake_time_base + 65.0

        def _fake_rss():
            return 500.0  # MB, well below 1.2GB

        # We only need the periodic-log branch; patch the internal helper
        # and simulate two "tick-top" invocations. The AC exposes this via
        # engine._periodic_rss_check() or similar — if the method exists,
        # call it, else fail with a clear message.
        method = getattr(engine, "_periodic_rss_check", None)
        if method is None:
            pytest.fail(
                "Engine must expose _periodic_rss_check() or equivalent"
                " per AC10 — not present."
            )
        with patch("time.time", _fake_time):
            try:
                from v5 import paper_engine as pe_mod
                with patch.object(pe_mod, "_read_rss_mb", _fake_rss,
                                   create=True):
                    method()
                    method()
            except AttributeError:
                method()
                method()
        rss_info = [
            r.getMessage() for r in caplog.records
            if r.levelno == logging.INFO
            and ("rss" in r.getMessage().lower() or "memory" in r.getMessage().lower())
        ]
        assert len(rss_info) >= 1


# ===================================================================
# AC10 — warning at 1.2GB threshold
# ===================================================================

class TestAC10ThresholdWarning:
    """RSS above 1200 MB emits WARNING."""

    def test_rss_above_threshold_logs_warning(self, caplog):
        from v5.paper_config import PaperConfig
        from v5.paper_engine import PaperPortfolioEngine

        config = PaperConfig(strategies=[])
        engine = PaperPortfolioEngine(config)
        caplog.set_level(logging.WARNING)

        def _fake_rss_over_threshold():
            return 1_500.0  # MB, above 1.2GB

        method = getattr(engine, "_periodic_rss_check", None)
        if method is None:
            pytest.fail("Engine must expose _periodic_rss_check() per AC10")

        from v5 import paper_engine as pe_mod
        with patch.object(pe_mod, "_read_rss_mb", _fake_rss_over_threshold,
                          create=True):
            method()

        warn_msgs = [
            r.getMessage() for r in caplog.records
            if r.levelno >= logging.WARNING
        ]
        assert any(
            "1.2" in m or "1200" in m or "memory" in m.lower() or "rss" in m.lower()
            for m in warn_msgs
        ), f"Expected WARNING about RSS above 1.2GB; got {warn_msgs}"

    def test_rss_below_threshold_no_warning(self, caplog):
        """500 MB: no WARNING, INFO only."""
        from v5.paper_config import PaperConfig
        from v5.paper_engine import PaperPortfolioEngine

        config = PaperConfig(strategies=[])
        engine = PaperPortfolioEngine(config)
        caplog.set_level(logging.WARNING)

        def _fake_rss_low():
            return 500.0

        method = getattr(engine, "_periodic_rss_check", None)
        if method is None:
            pytest.fail("Engine must expose _periodic_rss_check() per AC10")

        from v5 import paper_engine as pe_mod
        with patch.object(pe_mod, "_read_rss_mb", _fake_rss_low, create=True):
            method()

        warn_msgs = [
            r.getMessage() for r in caplog.records
            if r.levelno >= logging.WARNING
            and ("rss" in r.getMessage().lower() or "memory" in r.getMessage().lower())
        ]
        assert warn_msgs == [], (
            f"Did not expect memory/rss WARNING below threshold; got {warn_msgs}"
        )
