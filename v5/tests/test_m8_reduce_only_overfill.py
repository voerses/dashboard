"""M8 AC-Sz8 — reduce_only overfill semantics.

When reduce_only=True and order quantity would open or flip direction,
engine returns state=REJECTED with reject_reason="reduce_only_overfill".

Three scenarios:
  - long_5x_reduce_7x: long 5 BTC, submit reduce_only 7 BTC
    → 5-BTC fill + 2-BTC overfill rejection.
    Fill triple (FIX ExecReport): first Fill has last_qty=5, cum_qty=5,
    leaves_qty=0; second Fill (reject) has last_qty=0, leaves_qty=2.
  - short_3_reduce_5: short 3 BTC, reduce_only 5 BTC → same pattern.
  - flip_direction_attempt: long 5 BTC, reduce_only SHORT 3 BTC → would flip.

All tests MUST FAIL today — reduce_only overfill enforcement not wired.
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
            current_positions_notional={},
        )
        return policy.available_capital(strategy_id, state, 0)

    def liquidation_distance(self, position, leverage):
        return self._state.get("liquidation_distance_bps", 10_000.0)

    def equity(self, strategy_id):
        return self._state["equity"]

    def current_position_qty(self, token):
        return self._state.get("current_position_qty", 0.0)


def _market_state(**kw):
    base = dict(
        adv=1_000_000_000.0,
        mark_price=50_000.0,
        available_margin=1_000_000.0,
        equity=1_000_000.0,
        liquidation_distance_bps=10_000.0,
        current_position_qty=0.0,
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
def ctx_with_long_5btc():
    from v5.universe_context import UniverseContext
    ctx = UniverseContext.build_test(tokens=["BTC"], bars=100, seed=0, equity=1_000_000.0)
    # Open a long 5 BTC position via direct state (no ad-hoc API dependency).
    ctx.positions.open_manual(symbol="BTC", direction="LONG", qty=5.0, entry_price=50_000.0)
    return ctx


@pytest.fixture
def ctx_with_short_3btc():
    from v5.universe_context import UniverseContext
    ctx = UniverseContext.build_test(tokens=["BTC"], bars=100, seed=0, equity=1_000_000.0)
    ctx.positions.open_manual(symbol="BTC", direction="SHORT", qty=3.0, entry_price=50_000.0)
    return ctx


class TestReduceOnlyLongOverfill:
    """Scenario: long 5 BTC; reduce_only 7 BTC → 5 filled + 2 rejected."""

    def test_long_5x_reduce_7x_emits_partial_fill_plus_reject(self, ctx_with_long_5btc):
        from v5.orders import TriggerType, ExecType
        from v5.sizing.intents import SizingIntent, SizingRequest
        order = ctx_with_long_5btc.orders.arm(
            symbol="BTC", direction="SHORT", size=7.0,
            trigger=TriggerType.PRICE_BELOW, trigger_price=50_000.0,
            sizing=SizingRequest(
                intent=SizingIntent.FIXED_NOTIONAL,
                notional_usd=7.0 * 50_000.0,
                reduce_only=True,
            ),
        )
        order = order.trigger_immediately()
        order = order.release_atomic(
            available_capital_usd=1_000_000.0,
            market_state=_market_state(current_position_qty=5.0),
            policy=_policy(),
            config=_config(),
        )
        exec_types = [e.get("exec_type") for e in order.emitted_events()]
        assert ExecType.TRADE in exec_types, (
            f"Expected ExecType.TRADE for 5 BTC fill; got {exec_types}"
        )
        assert ExecType.REJECTED in exec_types, (
            f"Expected ExecType.REJECTED for 2-BTC overage; got {exec_types}"
        )

    def test_long_5x_reduce_7x_fill_triple_qty_accounting(self, ctx_with_long_5btc):
        """FIX Fill triple: trade.last_qty=5, trade.cum_qty=5, trade.leaves_qty=0;
        reject.last_qty=0, reject.leaves_qty=2 (the overage).
        """
        from v5.orders import TriggerType, ExecType
        from v5.sizing.intents import SizingIntent, SizingRequest
        order = ctx_with_long_5btc.orders.arm(
            symbol="BTC", direction="SHORT", size=7.0,
            trigger=TriggerType.PRICE_BELOW, trigger_price=50_000.0,
            sizing=SizingRequest(
                intent=SizingIntent.FIXED_NOTIONAL,
                notional_usd=7.0 * 50_000.0,
                reduce_only=True,
            ),
        )
        order = order.trigger_immediately()
        order = order.release_atomic(
            available_capital_usd=1_000_000.0,
            market_state=_market_state(current_position_qty=5.0),
            policy=_policy(),
            config=_config(),
        )
        events = list(order.emitted_events())
        trade_events = [e for e in events if e.get("exec_type") == ExecType.TRADE]
        reject_events = [e for e in events if e.get("exec_type") == ExecType.REJECTED]
        assert len(trade_events) >= 1, f"expected TRADE event; got {events}"
        assert len(reject_events) >= 1, f"expected REJECTED event; got {events}"

        trade = trade_events[0]
        reject = reject_events[0]
        # Trade fill for the 5 BTC that legitimately reduces the position.
        assert trade.get("last_qty") == pytest.approx(5.0)
        assert trade.get("cum_qty") == pytest.approx(5.0)
        assert trade.get("leaves_qty") == pytest.approx(0.0)
        # Reject for the 2 BTC overage.
        assert reject.get("last_qty") == pytest.approx(0.0)
        assert reject.get("leaves_qty") == pytest.approx(2.0)


class TestReduceOnlyShortOverfill:
    """Scenario: short 3 BTC; reduce_only 5 BTC → 3 filled + 2 rejected."""

    def test_short_3_reduce_5_emits_partial_fill_plus_reject(self, ctx_with_short_3btc):
        from v5.orders import TriggerType, ExecType
        from v5.sizing.intents import SizingIntent, SizingRequest
        order = ctx_with_short_3btc.orders.arm(
            symbol="BTC", direction="LONG", size=5.0,
            trigger=TriggerType.PRICE_ABOVE, trigger_price=50_000.0,
            sizing=SizingRequest(
                intent=SizingIntent.FIXED_NOTIONAL,
                notional_usd=5.0 * 50_000.0,
                reduce_only=True,
            ),
        )
        order = order.trigger_immediately()
        order = order.release_atomic(
            available_capital_usd=1_000_000.0,
            market_state=_market_state(current_position_qty=-3.0),
            policy=_policy(),
            config=_config(),
        )
        exec_types = [e.get("exec_type") for e in order.emitted_events()]
        assert ExecType.TRADE in exec_types
        assert ExecType.REJECTED in exec_types


class TestReduceOnlyFlipDirectionAttempt:
    """Scenario: long 5 BTC; reduce_only SHORT order would flip → full reject."""

    def test_flip_direction_rejected(self, ctx_with_long_5btc):
        from v5.orders import OrderStatus, TriggerType
        from v5.sizing.intents import SizingIntent, SizingRequest
        # reduce_only=True but direction matches existing position (LONG vs LONG)
        # is the flip-direction attempt — a reducing order must go opposite.
        order = ctx_with_long_5btc.orders.arm(
            symbol="BTC", direction="LONG", size=3.0,
            trigger=TriggerType.PRICE_ABOVE, trigger_price=50_000.0,
            sizing=SizingRequest(
                intent=SizingIntent.FIXED_NOTIONAL,
                notional_usd=3.0 * 50_000.0,
                reduce_only=True,
            ),
        )
        order = order.trigger_immediately()
        order = order.release_atomic(
            available_capital_usd=1_000_000.0,
            market_state=_market_state(current_position_qty=5.0),
            policy=_policy(),
            config=_config(),
        )
        assert order.state == OrderStatus.REJECTED
        assert order.reject_reason == "reduce_only_overfill"
