"""M4 — Determinism: MTF bit-identical two-run + scale-cap crash-restart.

Covers:
  - AC24 T-B14: Two MTF backtest runs with identical inputs/seeds/initial
    state produce bit-identical trade archives AND bit-identical
    BarProcessor.stats. No datetime.now(), no wall-clock reads.
  - AC26 T-B16: Mid-hour crash + restart does NOT produce a second scale on
    an already-scaled position; Position._scale_action_bar serialization retained.

All tests MUST FAIL today — MTF simulator + stats + crash-restart don't exist.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from v5.testing import TestClock  # noqa: E402


class TestAC24MTFDeterminism:
    """AC24 T-B14: two runs same seed same build -> bit-identical."""

    def test_two_runs_produce_bit_identical_trade_archive(self, tmp_path):
        """T-B14: two runs -> identical trade archive bytes."""
        from v5.bar_spec import BarSpec
        from v5.simulator import run_backtest_mtf

        start_ts = 1_770_000_000 * 1_000_000_000
        end_ts = start_ts + 24 * 3600 * 1_000_000_000

        def _one_run(path: Path) -> bytes:
            _ = TestClock(epoch_iso="2026-03-01T00:00:00Z", seed=42)
            run_backtest_mtf(
                base_resolution=BarSpec.from_minutes(1),
                start_ts_ns=start_ts, end_ts_ns=end_ts,
                strategies=[], tokens=["BTC"],
                output_path=path, seed=42,
            )
            return path.read_bytes()

        a = _one_run(tmp_path / "run_a.bin")
        b = _one_run(tmp_path / "run_b.bin")
        assert a == b, "Two MTF runs diverged in trade archive bytes"

    def test_two_runs_produce_bit_identical_stats(self, tmp_path):
        """T-B14: BarProcessor.stats hashes identically across runs."""
        from v5.bar_spec import BarSpec
        from v5.simulator import run_backtest_mtf

        def _run_and_hash(path: Path) -> str:
            _ = TestClock(epoch_iso="2026-03-01T00:00:00Z", seed=42)
            result = run_backtest_mtf(
                base_resolution=BarSpec.from_minutes(1),
                start_ts_ns=1_770_000_000 * 1_000_000_000,
                end_ts_ns=1_770_000_000 * 1_000_000_000 + 10 * 3600 * 1_000_000_000,
                strategies=[], tokens=["BTC"],
                output_path=path, seed=42,
            )
            stats_json = json.dumps(result.stats_dict(), sort_keys=True)
            return hashlib.sha256(stats_json.encode()).hexdigest()

        h1 = _run_and_hash(tmp_path / "a.bin")
        h2 = _run_and_hash(tmp_path / "b.bin")
        assert h1 == h2

    def test_no_wall_clock_in_simulator(self):
        """AC24: no wall-clock calls in the BarProcessor hot path. Expanded
        heuristic covers monotonic / perf_counter / process_time / asyncio
        event-loop clock / utcnow / today (still a heuristic but much
        stronger than the 2-member set)."""
        import inspect
        from v5 import bar_processor, simulator

        forbidden = {
            "datetime.now(",
            "time.time(",
            "time.monotonic(",
            "time.perf_counter(",
            "time.process_time(",
            "asyncio.get_running_loop().time(",
            "datetime.utcnow(",
            "datetime.today(",
        }
        for mod in (bar_processor, simulator):
            src = inspect.getsource(mod)
            for token in forbidden:
                assert token not in src, (
                    f"{mod.__name__} calls {token!r} — breaks AC24 determinism"
                )


class TestAC26ScaleCapCrashRestart:
    """AC26 T-B16: scale-cap survives crash-restart; no second scale."""

    def test_scale_action_bar_persists_across_restart(self, tmp_path):
        """AC26 T-B16: mid-hour scale fires; serialized state is reloaded; on
        replay of remaining sub-hourly bars NO second scale fires.
        Verified via the public scaling-history API, not the private
        `_scale_action_bar` field (behavioral, not implementation-detail)."""
        from v5.bar_processor import BarProcessor
        from v5.bar_spec import BarSpec
        from v5.position import Position
        from v5.paper_state import write_paper_state, read_paper_state
        from v5.position_manager import position_manager  # forward reference; M4 target

        pos = Position(token="BTC", direction=1, entry_price=100.0, quantity=1.0)
        pos_id = pos.position_id

        bp = BarProcessor()

        class Strat:
            strategy_id = "t"
            bar_subscriptions = {
                "signal": BarSpec.from_minutes(60),
                "entry": BarSpec.from_minutes(60),
                "exit": BarSpec.from_minutes(1),
            }

            def check_scale(self, pos, bar_ctx):
                return "increase"

        bp.register_strategy(Strat())

        # First mid-hour scale fires.
        bc1 = type("BC", (), {
            "ts_ns": 5 * 60_000_000_000, "close": 101.0, "high": 101.2, "low": 100.8,
            "volume": 1000.0, "bar_spec": BarSpec.from_minutes(1),
            "hourly_bar_index": 5,
        })()
        bp.process_bar_for_position(pos=pos, bar_ctx=bc1)
        history_before = position_manager.get_scaling_history(pos_id)
        assert len(history_before) == 1, (
            f"Expected exactly 1 scale event after first bar; got {len(history_before)}"
        )

        # Serialize and reload via paper_state (crash-restart).
        state_path = tmp_path / "paper_state.json"
        write_paper_state(state_path, positions=[pos])
        restored = read_paper_state(state_path)
        pos_r = restored.positions[0]

        # Feed another sub-hourly bar within the SAME hourly bar — no second scale.
        bc2 = type("BC", (), {
            "ts_ns": 6 * 60_000_000_000, "close": 101.5, "high": 101.6, "low": 101.3,
            "volume": 1000.0, "bar_spec": BarSpec.from_minutes(1),
            "hourly_bar_index": 5,  # same hourly bar index
        })()
        bp.process_bar_for_position(pos=pos_r, bar_ctx=bc2)
        history_after = position_manager.get_scaling_history(pos_id)
        assert len(history_after) == len(history_before), (
            f"Scale cap broken across restart: history {len(history_before)} -> "
            f"{len(history_after)}"
        )

    def test_no_second_scale_after_restart(self, tmp_path):
        """T-B16: mid-hour crash — restart at same hour — scale attempt blocked."""
        from v5.bar_processor import BarProcessor
        from v5.bar_spec import BarSpec
        from v5.position import Position
        from v5.paper_state import write_paper_state, read_paper_state

        pos = Position(token="BTC", direction=1, entry_price=100.0, quantity=1.0)
        pos._scale_action_bar = 7  # already scaled at bar index 7
        state_path = tmp_path / "paper_state.json"
        write_paper_state(state_path, positions=[pos])
        restored = read_paper_state(state_path)

        bp = BarProcessor()
        scale_calls = {"n": 0}

        class Strat:
            strategy_id = "t"
            bar_subscriptions = {
                "signal": BarSpec.from_minutes(60),
                "entry": BarSpec.from_minutes(60),
                "exit": BarSpec.from_minutes(60),
            }
            def check_scale(self, pos, bar_ctx):
                scale_calls["n"] += 1
                return "increase"

        strat = Strat()
        bp.register_strategy(strat)
        # Replay bar index 7 after restart — already-scaled guard must hold.
        bp.process_bar_for_position(
            pos=restored.positions[0], bar_ctx=type("BC", (), {
                "ts_ns": 0, "close": 101.0, "high": 102.0, "low": 100.0,
                "volume": 1000.0, "bar_spec": BarSpec.from_minutes(60),
                "hourly_bar_index": 7,
            })(),
        )
        assert scale_calls["n"] == 0, (
            "Stage 2 invoked despite same hourly_bar_index after restart"
        )
