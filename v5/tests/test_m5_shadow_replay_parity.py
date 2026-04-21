"""M5 — Shadow replay parity for combined-strategy migration (AC41).

Covers:
  - T-M5-19: combined-strategy migration under `use_multi_leg_orders=True`
    produces trades within M4's shadow replay tolerances (4 ULP price,
    5 bps per-trade PnL, 10 bps aggregate). No new tolerances introduced
    by M5.

All tests MUST FAIL today — v5.orders does not exist; combined-strategy
flag path is not yet implemented; fixtures not yet generated.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.skip(reason=(
    "M10 B4: run_combined_strategy_archive + trigger_combined_entry "
    "DELETED. Shadow-replay validation of the flag flip is obsolete "
    "after M10 (multi-leg OTOCO is canonical)."
))

import copy
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


class TestTM519CombinedShadowReplayParity:
    """T-M5-19: combined strategy flag flip stays within AC41 tolerances."""

    def test_combined_flag_false_vs_true_passes_shadow_replay(self):
        """T-M5-19: run a combined primary/secondary strategy with
        use_multi_leg_orders=False and =True; shadow-replay trade archives;
        expect PASS (within 4 ULP / 5 bps / 10 bps).

        Uses synthetic matched fixtures — two canned combined-strategy trade
        archives: ``false_archive`` (legacy linked_position_id baseline) and
        ``true_archive`` (M5 Order.legs migrated). If the flag=True path is
        not yet implemented the shadow replay correctly fails (RED).
        """
        from v5.tests.shadow_replay import run_shadow_replay
        from v5.simulator import run_combined_strategy_archive, SimulationState
        from v5.strategy_spec import StrategySpec

        spec_false = StrategySpec(
            strategy_id="s_combined", use_multi_leg_orders=False,
        )
        spec_true = StrategySpec(
            strategy_id="s_combined", use_multi_leg_orders=True,
        )
        false_archive = run_combined_strategy_archive(
            SimulationState(strategy_spec=spec_false),
        )
        true_archive = run_combined_strategy_archive(
            SimulationState(strategy_spec=spec_true),
        )
        report = run_shadow_replay(
            pre_trades=false_archive, post_trades=true_archive,
        )
        # Accept PASS OR zero failing_trades within AC41 tolerance.
        assert report.status == "PASS" or report.failing_trades == 0, (
            f"Combined-strategy flag-flip exceeded AC41 tolerances: "
            f"{report.failing_trades} failing trades; status={report.status}"
        )

    def test_tolerance_constants_match_m4(self):
        """T-M5-19: M5 does not introduce new tolerances — reuses M4 constants."""
        from v5.tests.shadow_replay_tolerances import (
            PRICE_ULP_TOLERANCE,
            PER_TRADE_PNL_BPS_TOLERANCE,
            AGGREGATE_PNL_BPS_TOLERANCE,
        )
        assert PRICE_ULP_TOLERANCE == 4
        assert PER_TRADE_PNL_BPS_TOLERANCE == 5
        assert AGGREGATE_PNL_BPS_TOLERANCE == 10

    def test_shadow_replay_harness_available_for_m5(self):
        """T-M5-19: v5.tests.shadow_replay harness importable and runnable
        against an M5-shaped trade archive (6-dim identity tuple includes
        leg_index for multi-leg Orders)."""
        from v5.tests.shadow_replay import run_shadow_replay
        pre_combined = [
            {
                "strategy_id": "s_combined", "token": "BTC",
                "entry_ts": 1_770_003_600 * 1_000_000_000,
                "direction": 1, "leg_index": 0, "exit_reason": "take_profit",
                "entry_price": 100.0, "exit_price": 102.0,
                "pnl": 2.0, "notional": 100.0,
            },
            {
                "strategy_id": "s_combined", "token": "BTC",
                "entry_ts": 1_770_003_600 * 1_000_000_000,
                "direction": -1, "leg_index": 1, "exit_reason": "take_profit",
                "entry_price": 100.0, "exit_price": 99.0,
                "pnl": 1.0, "notional": 100.0,
            },
        ]
        post_flag_true = copy.deepcopy(pre_combined)
        report = run_shadow_replay(
            pre_trades=pre_combined, post_trades=post_flag_true,
        )
        assert report.status == "PASS"
