"""M8 AC-Sz7 — Clamp error containment.

Any exception raised inside a clamp:
  - Order transitions to state=REJECTED with reject_reason="clamp_error_{clamp_name}"
  - Binding-log entry has `error` populated
  - BarProcessor does NOT crash

Canonical API: run_clamp_pipeline(order, *, available_capital_usd, market_state,
policy, config) is called by Order.release_atomic internally.

All tests MUST FAIL today — clamp error containment not implemented.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


@pytest.fixture
def ctx():
    from v5.universe_context import UniverseContext
    return UniverseContext.build_test(tokens=["BTC"], bars=100, seed=0, equity=150_000.0)


CLAMP_NAMES = [
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
        available_margin=150_000.0,
        equity=150_000.0,
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


class TestClampErrorRejectsOrder:
    """AC-Sz7 — raising clamp → Order REJECTED with reject_reason='clamp_error_*'."""

    @pytest.mark.parametrize("clamp_name", CLAMP_NAMES)
    def test_clamp_exception_yields_reject(self, ctx, tmp_path, monkeypatch, clamp_name):
        from v5.orders import OrderStatus, TriggerType
        from v5.sizing import clamps as clamps_mod

        def boom(*a, **kw):
            raise RuntimeError(f"boom-{clamp_name}")

        # Each clamp is a private callable named _<clamp_name>_clamp on the module.
        attr = f"_{clamp_name}_clamp"
        monkeypatch.setattr(clamps_mod, attr, boom, raising=False)

        order = ctx.orders.arm(
            symbol="BTC", direction="LONG", size=1.0,
            trigger=TriggerType.PRICE_ABOVE, trigger_price=50_000.0,
        )
        order = order.trigger_immediately()
        order = order.release_atomic(
            available_capital_usd=150_000.0,
            market_state=_market_state(),
            policy=_policy(),
            config=_config(),
        )
        assert order.state == OrderStatus.REJECTED
        assert order.reject_reason == f"clamp_error_{clamp_name}"

    @pytest.mark.parametrize("clamp_name", CLAMP_NAMES)
    def test_clamp_exception_logs_error_field(self, ctx, tmp_path, monkeypatch, clamp_name):
        from v5.orders import TriggerType
        from v5.sizing import clamps as clamps_mod

        def boom(*a, **kw):
            raise RuntimeError(f"boom-{clamp_name}")

        monkeypatch.setattr(clamps_mod, f"_{clamp_name}_clamp", boom, raising=False)

        log_path = tmp_path / "sizing_fills.jsonl"

        order = ctx.orders.arm(
            symbol="BTC", direction="LONG", size=1.0,
            trigger=TriggerType.PRICE_ABOVE, trigger_price=50_000.0,
        )
        order = order.trigger_immediately()
        order.release_atomic(
            available_capital_usd=150_000.0,
            market_state=_market_state(),
            policy=_policy(),
            config=_config(log_path=log_path),
        )
        entry = json.loads(log_path.read_text().splitlines()[0])
        assert entry["error"] is not None
        assert f"boom-{clamp_name}" in str(entry["error"])


class TestBarProcessorNotCrashed:
    """AC-Sz7 — BarProcessor does NOT raise when a clamp raises."""

    def test_bar_processor_survives_clamp_exception(self, ctx, monkeypatch):
        """A clamp exception must not bubble up to the BarProcessor loop."""
        from v5.orders import TriggerType
        from v5.sizing import clamps as clamps_mod

        def boom(*a, **kw):
            raise RuntimeError("clamp blew up")

        monkeypatch.setattr(clamps_mod, "_adv_cap_clamp", boom, raising=False)

        order = ctx.orders.arm(
            symbol="BTC", direction="LONG", size=1.0,
            trigger=TriggerType.PRICE_ABOVE, trigger_price=50_000.0,
        )
        order = order.trigger_immediately()
        # Call release_atomic — must return a REJECTED Order, NOT raise.
        try:
            out = order.release_atomic(
                available_capital_usd=150_000.0,
                market_state=_market_state(),
                policy=_policy(),
                config=_config(),
            )
        except Exception as exc:
            pytest.fail(
                f"release_atomic must contain clamp exceptions; got {exc!r}"
            )
        assert out is not None
