"""M10 E4 — clock-drift detection (AC #23).

``v5/run_paper_multi.py`` must compare ``LiveClock.now_ns()`` vs
``BinanceRESTClient.server_time_ns()`` every 60s and emit to
``v5/logs/clock_drift.jsonl``:

    * |delta| > 500ms  → WARN entry (continue trading)
    * |delta| > 5s     → HALT entry + flip TradingState.state to "HALTED"

We inject drift by monkeypatching ``server_time_ns`` and drive a single
check cycle via the runner's exposed ``_check_clock_drift()`` hook
(Phase 4 surface). For test speed the poll interval is overridden to
0.1s OR we call the hook directly — whichever the Phase-4 contract
allows.

MUST FAIL TODAY (RED):
    * ``BinanceRESTClient`` does not expose ``server_time_ns``.
    * ``v5.run_paper_multi`` has no ``_check_clock_drift`` entry point.
    * ``v5/logs/clock_drift.jsonl`` is not emitted anywhere.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_LOCAL_NS = 1_735_689_600 * 1_000_000_000  # 2025-01-01 00:00:00 UTC
_WARN_DRIFT_NS = 600 * 1_000_000          # +600 ms
_HALT_DRIFT_NS = 6 * 1_000_000_000        # +6 s


def _read_drift_entries(log_path: Path) -> list[dict]:
    if not log_path.exists():
        return []
    return [
        json.loads(line)
        for line in log_path.read_text().splitlines()
        if line.strip()
    ]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestClockDriftWarnThreshold:
    """AC #23 — |delta| > 500ms emits WARN line to clock_drift.jsonl."""

    def test_600ms_drift_emits_warn_entry(self, tmp_path: Path) -> None:
        from v5 import run_paper_multi
        from v5.clock import LiveClock  # noqa: F401

        log_path = tmp_path / "clock_drift.jsonl"

        # Patch LiveClock to return a deterministic local time, and patch
        # the REST client to return local + 600ms drift.
        with patch.object(
            run_paper_multi.LiveClock, "now_ns", return_value=_LOCAL_NS
        ), patch.object(
            run_paper_multi.BinanceRESTClient,
            "server_time_ns",
            return_value=_LOCAL_NS + _WARN_DRIFT_NS,
            create=True,
        ):
            # Phase 4 exposes `_check_clock_drift(log_path)` on the runner;
            # today this attribute is missing -> AttributeError (RED).
            run_paper_multi._check_clock_drift(log_path=log_path)

        entries = _read_drift_entries(log_path)
        assert len(entries) >= 1, (
            "600ms drift must emit at least one WARN entry to "
            "clock_drift.jsonl."
        )
        warn_entries = [e for e in entries if e.get("level") == "WARN"]
        assert len(warn_entries) >= 1, (
            f"Expected at least one level=WARN entry; got {entries!r}."
        )
        entry = warn_entries[0]
        assert abs(entry["delta_ms"] - 600) < 50, (
            f"delta_ms must report ~600 (tolerance 50 ms); got "
            f"{entry['delta_ms']!r}."
        )


class TestClockDriftHaltThreshold:
    """AC #23 — |delta| > 5s emits HALT + flips TradingState."""

    def test_6s_drift_emits_halt_and_flips_trading_state(
        self, tmp_path: Path
    ) -> None:
        from v5 import run_paper_multi
        from v5.risk import TradingState

        log_path = tmp_path / "clock_drift.jsonl"
        trading_state = TradingState(state="ACTIVE")

        with patch.object(
            run_paper_multi.LiveClock, "now_ns", return_value=_LOCAL_NS
        ), patch.object(
            run_paper_multi.BinanceRESTClient,
            "server_time_ns",
            return_value=_LOCAL_NS + _HALT_DRIFT_NS,
            create=True,
        ):
            # Phase 4 exposes `_check_clock_drift(log_path, trading_state)`.
            run_paper_multi._check_clock_drift(
                log_path=log_path,
                trading_state=trading_state,
            )

        entries = _read_drift_entries(log_path)
        halt_entries = [e for e in entries if e.get("level") == "HALT"]
        assert len(halt_entries) >= 1, (
            f"6s drift must emit at least one level=HALT entry; got "
            f"{entries!r}."
        )
        entry = halt_entries[0]
        assert abs(entry["delta_ms"] - 6000) < 200, (
            f"delta_ms must report ~6000 (tolerance 200 ms); got "
            f"{entry['delta_ms']!r}."
        )

        assert trading_state.state == "HALTED", (
            f"TradingState must flip to HALTED on >5s drift; got "
            f"{trading_state.state!r}."
        )


class TestClockDriftSink:
    """AC #23 — clock_drift.jsonl is the exclusive sink."""

    def test_sink_path_configurable_and_no_drift_leaves_sink_absent(
        self, tmp_path: Path
    ) -> None:
        from v5 import run_paper_multi
        from v5.risk import TradingState

        log_path = tmp_path / "clock_drift.jsonl"
        trading_state = TradingState(state="ACTIVE")

        # |delta| == 0 → no entry, no sink.
        with patch.object(
            run_paper_multi.LiveClock, "now_ns", return_value=_LOCAL_NS
        ), patch.object(
            run_paper_multi.BinanceRESTClient,
            "server_time_ns",
            return_value=_LOCAL_NS,
            create=True,
        ):
            run_paper_multi._check_clock_drift(
                log_path=log_path,
                trading_state=trading_state,
            )

        assert not log_path.exists() or _read_drift_entries(log_path) == [], (
            "Zero drift must not emit any entry to clock_drift.jsonl."
        )
        assert trading_state.state == "ACTIVE"
