"""M8 AC-Sz3 — Clamp ordering determinism.

6 clamps fire in fixed order:
  ADV cap → concentration → free capital → min size → liq distance → slippage

binding_constraint records the FIRST clamp to reduce below requested.

Canonical API:
  run_clamp_pipeline(order, *, available_capital_usd, market_state, policy, config)
  → returns (new_order, binding_log_dict)

All tests MUST FAIL today — v5.sizing clamps do not exist.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


EXPECTED_ORDER = [
    "adv_cap",
    "concentration",
    "free_capital",
    "min_size",
    "liquidation_distance",
    "slippage",
]


class _SyntheticMarketState:
    def __init__(self, **state):
        self._state = dict(state)

    def adv(self, token):
        return self._state["adv"]

    def rolling_adv(self, token, window_hours=24):
        return self._state["adv"]

    def mark_price(self, token):
        return self._state["mark_price"]

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


def _market_state(**kw):
    base = dict(
        adv=1_000_000_000.0,
        mark_price=50_000.0,
        available_margin=10_000_000.0,
        equity=10_000_000.0,
        liquidation_distance_bps=10_000.0,
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
                                      equity=10_000_000.0)


def _make_order(ctx, *, notional_usd):
    from v5.orders import TriggerType
    from v5.sizing.intents import SizingIntent, SizingRequest
    order = ctx.orders.arm(
        symbol="BTC", direction="LONG", size=1.0,
        trigger=TriggerType.PRICE_ABOVE, trigger_price=50_000.0,
        sizing=SizingRequest(
            intent=SizingIntent.FIXED_NOTIONAL,
            notional_usd=notional_usd,
        ),
    )
    return order.trigger_immediately()


class TestClampOrderingFirstBindingWins:
    """AC-Sz3 — first clamp to reduce below requested wins binding_constraint."""

    def test_adv_beats_concentration_when_both_would_bind(self, ctx):
        """ADV clamp fires before concentration — ADV bound goes first."""
        from v5.sizing.clamps import run_clamp_pipeline
        order = _make_order(ctx, notional_usd=1_000_000.0)
        _, log = run_clamp_pipeline(
            order,
            available_capital_usd=10_000_000.0,
            market_state=_market_state(adv=100_000.0, equity=50_000.0),
            policy=_policy(),
            config=_config(adv_cap_pct=0.05, concentration_limit=0.10),
        )
        assert log["binding_constraint"] == "adv_cap", (
            f"ADV must bind first; got {log['binding_constraint']!r}"
        )

    def test_concentration_beats_free_capital(self, ctx):
        """When both concentration and free_capital would reduce, concentration wins."""
        from v5.sizing.clamps import run_clamp_pipeline
        order = _make_order(ctx, notional_usd=100_000.0)
        _, log = run_clamp_pipeline(
            order,
            available_capital_usd=5_000.0,
            market_state=_market_state(available_margin=5_000.0, equity=50_000.0),
            policy=_policy(),
            config=_config(concentration_limit=0.10),
        )
        assert log["binding_constraint"] == "concentration"

    def test_free_capital_beats_min_size_as_first_reducer(self, ctx):
        """Free-capital is the first reducer; min-size floor REJECTS afterwards.

        Semantics: binding_constraint = 'free_capital' (first reducer); the
        order also transitions to REJECTED with reject_reason='min_size'.
        """
        from v5.orders import OrderStatus
        from v5.sizing.clamps import run_clamp_pipeline
        order = _make_order(ctx, notional_usd=1_000.0)
        new_order, log = run_clamp_pipeline(
            order,
            available_capital_usd=50.0,
            market_state=_market_state(available_margin=50.0),
            policy=_policy(),
            config=_config(min_position_usd=100.0),
        )
        # free_capital reduces first ($50), then min_size floor rejects.
        assert log["binding_constraint"] == "free_capital"
        assert new_order.state == OrderStatus.REJECTED
        assert new_order.reject_reason == "min_size"


class TestClampOrderingConstant:
    """AC-Sz3 — the engine exposes the clamp order list for introspection."""

    def test_clamp_order_constant_exists(self):
        from v5.sizing.clamps import CLAMP_ORDER
        assert list(CLAMP_ORDER) == EXPECTED_ORDER

    def test_clamp_order_length_six(self):
        from v5.sizing.clamps import CLAMP_ORDER
        assert len(CLAMP_ORDER) == 6
