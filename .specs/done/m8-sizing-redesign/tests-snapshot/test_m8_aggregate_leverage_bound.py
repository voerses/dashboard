"""M8 — TokenSignal fraction_of_equity × leverage aggregate bound.

From brief §M7 Impact item 2: the engine rejects or scales when the
aggregate `fraction_of_equity × leverage` exceeds 1.0 across the active
position book.

Default: reject the marginal signal with reject_reason="over_leveraged".
Escape hatch: SizingRequest(allow_over_leveraged=True) accepts the
marginal request without aggregate enforcement.

All tests MUST FAIL today — aggregate leverage clamp + allow_over_leveraged
field do not exist.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


class _SyntheticMarketState:
    def __init__(self, **state):
        self._state = dict(state)

    def adv(self, token): return self._state["adv"]
    def rolling_adv(self, token, window_hours=24): return self._state["adv"]
    def mark_price(self, token): return self._state["mark_price"]

    def free_margin(self, strategy_id, policy):
        from v5.sizing.allocation import AllocationState
        state = AllocationState(
            available_margin=self._state["available_margin"],
            per_strategy_equity={strategy_id: self._state["equity"]},
            rolling_pnl_24h={},
            current_positions_notional=self._state.get(
                "current_positions_notional", {}
            ),
        )
        return policy.available_capital(strategy_id, state, 0)

    def liquidation_distance(self, position, leverage):
        return self._state.get("liquidation_distance_bps", 10_000.0)

    def equity(self, strategy_id):
        return self._state["equity"]

    # Active-position book shape the clamp reads to sum fraction × leverage.
    def active_position_leverage_sum(self, strategy_id):
        return self._state.get("active_position_leverage_sum", 0.0)


def _market_state(**kw):
    base = dict(
        adv=1_000_000_000.0,
        mark_price=50_000.0,
        available_margin=1_000_000.0,
        equity=100_000.0,
        liquidation_distance_bps=10_000.0,
        active_position_leverage_sum=0.0,
    )
    base.update(kw)
    return _SyntheticMarketState(**base)


def _policy():
    from v5.sizing.allocation import SharedPoolPolicy
    return SharedPoolPolicy()


def _config(**kw):
    from v5.sizing.clamps import ClampsConfig
    base = dict(
        adv_cap_pct=1.0,
        concentration_limit=1.0,
        min_position_usd=10.0,
        min_liquidation_distance_bps=100.0,
    )
    base.update(kw)
    return ClampsConfig(**base)


@pytest.fixture
def ctx():
    from v5.universe_context import UniverseContext
    return UniverseContext.build_test(tokens=["BTC"], bars=100, seed=0,
                                      equity=100_000.0)


def _make_order(ctx, *, fraction, leverage, allow_over_leveraged=False):
    from v5.orders import TriggerType
    from v5.sizing.intents import SizingIntent, SizingRequest
    order = ctx.orders.arm(
        symbol="BTC", direction="LONG", size=1.0,
        trigger=TriggerType.PRICE_ABOVE, trigger_price=50_000.0,
        sizing=SizingRequest(
            intent=SizingIntent.FIXED_FRACTION,
            fraction_of_equity=fraction,
            leverage=leverage,
            allow_over_leveraged=allow_over_leveraged,
        ),
    )
    return order.trigger_immediately()


class TestAggregateLeverageBound:
    """Default behavior: reject marginal signal when aggregate > 1.0."""

    def test_single_position_at_1x_aggregate_passes(self, ctx):
        """fraction=0.5 × leverage=2.0 = 1.0 aggregate → accepted (on the line)."""
        from v5.orders import OrderStatus
        order = _make_order(ctx, fraction=0.5, leverage=2.0)
        order = order.release_atomic(
            available_capital_usd=100_000.0,
            market_state=_market_state(active_position_leverage_sum=0.0),
            policy=_policy(),
            config=_config(),
        )
        assert order.state != OrderStatus.REJECTED or (
            order.reject_reason != "over_leveraged"
        )

    def test_two_positions_aggregate_exceeds_1_rejects(self, ctx):
        """Position A (0.3 × 2.0 = 0.6) + request B (0.3 × 2.0 = 0.6) = 1.2 → REJECT."""
        from v5.orders import OrderStatus
        order = _make_order(ctx, fraction=0.3, leverage=2.0)
        # market_state reports Position A already contributes 0.6 to the sum.
        order = order.release_atomic(
            available_capital_usd=100_000.0,
            market_state=_market_state(active_position_leverage_sum=0.6),
            policy=_policy(),
            config=_config(),
        )
        assert order.state == OrderStatus.REJECTED
        assert order.reject_reason == "over_leveraged"

    def test_allow_over_leveraged_escape_hatch(self, ctx):
        """Same 1.2-aggregate setup, but allow_over_leveraged=True → accepted."""
        from v5.orders import OrderStatus
        order = _make_order(
            ctx, fraction=0.3, leverage=2.0, allow_over_leveraged=True,
        )
        order = order.release_atomic(
            available_capital_usd=100_000.0,
            market_state=_market_state(active_position_leverage_sum=0.6),
            policy=_policy(),
            config=_config(),
        )
        # allow_over_leveraged bypasses the aggregate check — must not reject
        # WITH that reason.
        if order.state == OrderStatus.REJECTED:
            assert order.reject_reason != "over_leveraged"

    def test_rejected_order_has_reject_reason_over_leveraged(self, ctx):
        """brief §M7 Impact item 2: reject_reason must be 'over_leveraged'."""
        from v5.orders import OrderStatus
        order = _make_order(ctx, fraction=0.4, leverage=3.0)
        # 0.4 × 3.0 = 1.2 > 1.0 on its own
        order = order.release_atomic(
            available_capital_usd=100_000.0,
            market_state=_market_state(active_position_leverage_sum=0.0),
            policy=_policy(),
            config=_config(),
        )
        assert order.state == OrderStatus.REJECTED
        assert order.reject_reason == "over_leveraged"
