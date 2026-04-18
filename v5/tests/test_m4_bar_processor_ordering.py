"""M4 — BarProcessor Stage 1/2/3 ordering + same-timestamp emission order.

Covers:
  - AC2 T-B1: Stage ordering — Stage 1 completes for all positions BEFORE
    Stage 2; Stage 2 before Stage 3. Verified via call-order-recording mock
    across >= 3 positions.
  - AC25 T-B15: Same-timestamp emission order within one sim-clock step
    (fine exit bars -> coarse exit -> Stage 1 -> fine entry -> coarse entry
    -> Stage 3 -> signal bar -> on_signal -> Stage 2).

All tests MUST FAIL today — v5.bar_processor does not exist.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


class TestAC2StageOrdering:
    """AC2 T-B1: Stage 1 finishes for all N positions BEFORE Stage 2 begins."""

    def test_stage_1_before_stage_2_across_three_positions(self):
        """T-B1: >=3 positions — all Stage 1 calls precede any Stage 2 call."""
        from v5.bar_processor import BarProcessor

        call_log: list[tuple[str, int]] = []

        pos_a = MagicMock(name="pos_A", token="BTC")
        pos_b = MagicMock(name="pos_B", token="ETH")
        pos_c = MagicMock(name="pos_C", token="SOL")
        positions = [pos_a, pos_b, pos_c]

        # Record stage calls via a recording processor hook.
        bp = BarProcessor(
            stage_recorder=lambda stage, pos_idx: call_log.append(
                (stage, pos_idx),
            ),
        )

        bar_ctx = MagicMock(name="bar_ctx")
        bar_ctx.ts_ns = 1_700_000_000_000_000_000
        bar_ctx.hourly_bar_index = 0

        bp.process_bar(bar_ctx=bar_ctx, positions=positions, pending_entries=[])

        # Find the index of the last Stage 1 call and the first Stage 2 call.
        last_stage_1 = max(i for i, (s, _) in enumerate(call_log) if s == "stage_1")
        first_stage_2 = min(
            (i for i, (s, _) in enumerate(call_log) if s == "stage_2"),
            default=None,
        )
        assert first_stage_2 is not None, "No Stage 2 invocation recorded"
        assert last_stage_1 < first_stage_2, (
            f"Stage 1 did not complete before Stage 2; log={call_log}"
        )

    def test_stage_2_before_stage_3(self):
        """T-B1: Stage 2 completes for all positions before Stage 3."""
        from v5.bar_processor import BarProcessor

        call_log: list[tuple[str, int]] = []
        pos_a = MagicMock(token="BTC")
        pos_b = MagicMock(token="ETH")
        pos_c = MagicMock(token="SOL")

        bp = BarProcessor(
            stage_recorder=lambda stage, pos_idx: call_log.append(
                (stage, pos_idx),
            ),
        )

        bar_ctx = MagicMock()
        bar_ctx.ts_ns = 1_700_000_000_000_000_000
        bar_ctx.hourly_bar_index = 0

        bp.process_bar(
            bar_ctx=bar_ctx, positions=[pos_a, pos_b, pos_c], pending_entries=[],
        )

        last_stage_2 = max(
            (i for i, (s, _) in enumerate(call_log) if s == "stage_2"),
            default=None,
        )
        first_stage_3 = min(
            (i for i, (s, _) in enumerate(call_log) if s == "stage_3"),
            default=None,
        )
        assert last_stage_2 is not None, "No Stage 2 call recorded"
        assert first_stage_3 is not None, "No Stage 3 call recorded"
        assert last_stage_2 < first_stage_3, (
            f"Stage 2 did not complete before Stage 3; log={call_log}"
        )


class TestAC25SameTimestampEmissionOrder:
    """AC25 T-B15: Within a single sim-clock step, callbacks fire in documented order."""

    def test_fine_exit_before_coarse_exit(self):
        """AC25 step 1-2: fine exit bars ascending ts precede coarse exit bar;
        additionally fine_exit emissions are in STRICTLY ASCENDING ts_ns order."""
        from v5.bar_processor import BarProcessor
        from v5.bar_spec import BarSpec

        emission_log: list[tuple[str, int]] = []

        bp = BarProcessor(
            event_recorder=lambda evt, ts_ns: emission_log.append((evt, ts_ns)),
        )

        T = 1_700_000_000_000_000_000
        # Subscribe exit at 1m (fine) and declare a coarse exit at 1h.
        bp.tick_sim_clock(
            ts_ns=T,
            fine_exit_bars=[
                {"spec": BarSpec.from_minutes(1), "ts_ns": T - 120_000_000_000},
                {"spec": BarSpec.from_minutes(1), "ts_ns": T - 60_000_000_000},
            ],
            coarse_exit_bar={"spec": BarSpec.from_minutes(60), "ts_ns": T},
            fine_entry_bars=[],
            coarse_entry_bar=None,
            signal_bar={"spec": BarSpec.from_minutes(60), "ts_ns": T},
        )
        # Extract ordering keys.
        order = [evt for evt, _ in emission_log]
        idx_fine_exit = [i for i, e in enumerate(order) if e == "fine_exit"]
        idx_coarse_exit = order.index("coarse_exit")
        assert all(i < idx_coarse_exit for i in idx_fine_exit)

        # AC25: fine_exit emissions must be in strictly ascending ts_ns order.
        fine_exit_ts = [ts for evt, ts in emission_log if evt == "fine_exit"]
        assert all(a < b for a, b in zip(fine_exit_ts, fine_exit_ts[1:])), (
            f"fine_exit emissions not strictly ascending by ts_ns: {fine_exit_ts}"
        )

    def test_stage_1_between_coarse_exit_and_fine_entry(self):
        """AC25 step 3: Stage 1 fires after coarse exit, before fine entry."""
        from v5.bar_processor import BarProcessor
        from v5.bar_spec import BarSpec

        log: list[str] = []
        bp = BarProcessor(event_recorder=lambda evt, ts_ns: log.append(evt))

        T = 1_700_000_000_000_000_000
        bp.tick_sim_clock(
            ts_ns=T,
            fine_exit_bars=[{"spec": BarSpec.from_minutes(1), "ts_ns": T - 60_000_000_000}],
            coarse_exit_bar={"spec": BarSpec.from_minutes(60), "ts_ns": T},
            fine_entry_bars=[{"spec": BarSpec.from_minutes(1), "ts_ns": T - 60_000_000_000}],
            coarse_entry_bar={"spec": BarSpec.from_minutes(60), "ts_ns": T},
            signal_bar={"spec": BarSpec.from_minutes(60), "ts_ns": T},
        )
        idx_coarse_exit = log.index("coarse_exit")
        idx_stage_1 = log.index("stage_1")
        idx_fine_entry = log.index("fine_entry")
        assert idx_coarse_exit < idx_stage_1 < idx_fine_entry

    def test_signal_before_stage_2(self):
        """AC25 step 7-9: signal bar -> on_signal -> Stage 2 (scaling)."""
        from v5.bar_processor import BarProcessor
        from v5.bar_spec import BarSpec

        log: list[str] = []
        bp = BarProcessor(event_recorder=lambda evt, ts_ns: log.append(evt))

        T = 1_700_000_000_000_000_000
        bp.tick_sim_clock(
            ts_ns=T,
            fine_exit_bars=[],
            coarse_exit_bar={"spec": BarSpec.from_minutes(60), "ts_ns": T},
            fine_entry_bars=[],
            coarse_entry_bar={"spec": BarSpec.from_minutes(60), "ts_ns": T},
            signal_bar={"spec": BarSpec.from_minutes(60), "ts_ns": T},
        )
        idx_signal = log.index("signal_bar")
        idx_on_signal = log.index("on_signal")
        idx_stage_2 = log.index("stage_2")
        assert idx_signal < idx_on_signal < idx_stage_2

    def test_stage_3_before_signal_bar(self):
        """AC25 step 5-7: Stage 3 fires after coarse entry bar, before signal bar."""
        from v5.bar_processor import BarProcessor
        from v5.bar_spec import BarSpec

        log: list[str] = []
        bp = BarProcessor(event_recorder=lambda evt, ts_ns: log.append(evt))

        T = 1_700_000_000_000_000_000
        bp.tick_sim_clock(
            ts_ns=T,
            fine_exit_bars=[],
            coarse_exit_bar={"spec": BarSpec.from_minutes(60), "ts_ns": T},
            fine_entry_bars=[],
            coarse_entry_bar={"spec": BarSpec.from_minutes(60), "ts_ns": T},
            signal_bar={"spec": BarSpec.from_minutes(60), "ts_ns": T},
        )
        idx_coarse_entry = log.index("coarse_entry")
        idx_stage_3 = log.index("stage_3")
        idx_signal = log.index("signal_bar")
        assert idx_coarse_entry < idx_stage_3 < idx_signal

    def test_ascending_fine_entry_ts(self):
        """AC25 step 4: fine entry bars fire in ascending ts_ns order."""
        from v5.bar_processor import BarProcessor
        from v5.bar_spec import BarSpec

        log: list[tuple[str, int]] = []
        bp = BarProcessor(event_recorder=lambda evt, ts_ns: log.append((evt, ts_ns)))

        T = 1_700_000_000_000_000_000
        fine_entries = [
            {"spec": BarSpec.from_minutes(1), "ts_ns": T - 180_000_000_000},
            {"spec": BarSpec.from_minutes(1), "ts_ns": T - 120_000_000_000},
            {"spec": BarSpec.from_minutes(1), "ts_ns": T - 60_000_000_000},
        ]
        bp.tick_sim_clock(
            ts_ns=T,
            fine_exit_bars=[],
            coarse_exit_bar=None,
            fine_entry_bars=fine_entries,
            coarse_entry_bar=None,
            signal_bar=None,
        )
        fine_entry_ts = [ts for evt, ts in log if evt == "fine_entry"]
        assert fine_entry_ts == sorted(fine_entry_ts), (
            f"fine_entry emission not ascending by ts_ns: {fine_entry_ts}"
        )
