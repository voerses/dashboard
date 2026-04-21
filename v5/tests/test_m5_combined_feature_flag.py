"""M5 — Combined primary/secondary feature flag.

Covers:
  - T-M5-13: `StrategySpec.use_multi_leg_orders: bool = False` gates the
    combined-strategy multi-leg path.
    * False (default): combined strategies continue using M2's
      `linked_position_id` + `leg: str ("primary"|"secondary")` fields.
    * True: combined strategies emit `Order(legs=[Leg(primary), Leg(secondary)],
      contingency=OCO, ...)`.
    Both produce equivalent trade archives within shadow-replay tolerance.

All tests MUST FAIL today — v5.orders + StrategySpec.use_multi_leg_orders
do not exist.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.skip(reason=(
    "M10 B4: trigger_combined_entry + CombinedEntryResult DELETED. "
    "Feature-flag-based dispatch obsoleted by M10 AC #13 — multi-leg "
    "OTOCO is canonical. See .specs/telemetry.jsonl for the dispute."
))

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


class TestTM513FeatureFlagDefault:
    """T-M5-13: use_multi_leg_orders defaults to False."""

    def test_strategy_spec_flag_default_false(self):
        """T-M5-13: StrategySpec() exposes use_multi_leg_orders=False by default."""
        from v5.strategy_spec import StrategySpec
        spec = StrategySpec(strategy_id="s_test")
        assert spec.use_multi_leg_orders is False

    def test_strategy_spec_flag_settable_true(self):
        """T-M5-13: flag is settable to True via kwarg."""
        from v5.strategy_spec import StrategySpec
        spec = StrategySpec(strategy_id="s_test", use_multi_leg_orders=True)
        assert spec.use_multi_leg_orders is True


class TestTM513FeatureFlagBehavior:
    """T-M5-13: the flag gates which path is used."""

    def test_flag_false_uses_legacy_linked_position_id_path(self):
        """T-M5-13: when flag is False, combined strategies use
        linked_position_id + pos.leg (M2 path). The engine does NOT emit
        Order.legs for them — emitted positions have linked_position_id
        set AND order_id is None (legacy path, no Order emitted)."""
        from v5.strategy_spec import StrategySpec
        from v5.simulator import SimulationState, trigger_combined_entry

        spec = StrategySpec(
            strategy_id="s_combined", use_multi_leg_orders=False,
        )
        state = SimulationState(strategy_spec=spec)
        positions = trigger_combined_entry(
            state=state, token="BTC",
            armed_at=_dt("2026-04-01T00:00:00"),
        )
        # Legacy path: 2 positions sharing linked_position_id, no Order emitted.
        assert len(positions) == 2
        assert positions[0].linked_position_id is not None
        assert positions[0].linked_position_id == positions[1].linked_position_id
        for p in positions:
            assert p.order_id is None, (
                f"Flag=False must NOT emit Order; position {p.position_id} "
                f"has order_id={p.order_id!r}"
            )

    def test_flag_true_emits_multi_leg_order(self):
        """T-M5-13: when flag is True, combined strategies emit Order with 2
        Leg entries (primary + secondary) and contingency=OCO; both positions
        share the same order_id."""
        from v5.strategy_spec import StrategySpec
        from v5.simulator import SimulationState, trigger_combined_entry
        from v5.orders import ContingencyType

        spec = StrategySpec(
            strategy_id="s_combined", use_multi_leg_orders=True,
        )
        state = SimulationState(strategy_spec=spec)
        result = trigger_combined_entry(
            state=state, token="BTC",
            armed_at=_dt("2026-04-01T00:00:00"),
        )
        order = result.order
        positions = result.positions
        assert len(order.legs) == 2
        leg_refs = {lg.leg_ref_id for lg in order.legs}
        assert leg_refs == {"leg_primary", "leg_secondary"}, (
            f"Expected legs [leg_primary, leg_secondary]; got {leg_refs}"
        )
        assert order.contingency == ContingencyType.OCO
        # Both positions share the Order's order_id.
        assert len(positions) == 2
        assert all(p.order_id == order.order_id for p in positions)

    def test_coexistence_preserves_m2_semantics(self):
        """T-M5-13: run the same combined strategy under both flag values;
        trade archives are equivalent within shadow replay tolerance."""
        from v5.strategy_spec import StrategySpec
        from v5.simulator import SimulationState, run_combined_strategy_archive
        from v5.tests.shadow_replay import run_shadow_replay

        spec_legacy = StrategySpec(
            strategy_id="s_combined", use_multi_leg_orders=False,
        )
        spec_multi = StrategySpec(
            strategy_id="s_combined", use_multi_leg_orders=True,
        )
        state_legacy = SimulationState(strategy_spec=spec_legacy)
        state_multi = SimulationState(strategy_spec=spec_multi)
        archive_legacy = run_combined_strategy_archive(state_legacy)
        archive_multi = run_combined_strategy_archive(state_multi)
        report = run_shadow_replay(
            pre_trades=archive_legacy, post_trades=archive_multi,
        )
        assert report.status == "PASS", (
            f"Flag-flip parity violated: {report}"
        )
