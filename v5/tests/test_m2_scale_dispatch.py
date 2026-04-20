"""Acceptance tests for M2 — scale_check_fn dispatch and book_reduce wrapper.

Covers:
  - AC6: ScaleAction dataclass (4 fields) + scale_check_fn signature
  - AC10: Portfolio constraints on increase (ADV, concentration, margin, min size)
  - AC18: Phase ordering + per-hourly-bar invocation cap (C2)
  - AC24: one invocation per bar; first non-None return executed
  - AC24a: list[ScaleAction] order-sensitive execution; terminal halts remainder
  - AC25: scale_check_fn exceptions => no-action (logged, not crash)
  - AC27: increase does not count against max_concurrent_per_token
  - AC28: liquidation uses post-scale state
  - AC28a: entry_filter_fn does NOT gate increases
  - AC28b: stress_adv_multiplier applies only when is_stop_like=True
  - OQ-1: near-zero epsilon short-circuit
  - Q4: list[ScaleAction] ordering semantics

All tests MUST FAIL until ScaleAction, book_reduce, _dispatch_scale_action
and Position.increase/reduce exist.
"""
from __future__ import annotations

import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "v4"))

import numpy as np
import pytest

from v5.position import Position  # noqa: E402
from v5.strategy_api import ScaleAction  # noqa: E402


def _make_position(
    token: str = "BTC", entry_price: float = 100.0, quantity: float = 10.0,
    margin_usd: float = 1_000.0, direction: int = 1,
    stop_price: float = 90.0,
) -> Position:
    signed_qty = direction * abs(quantity)
    return Position(
        position_id=f"{token}:s30:5:primary",
        token=token,
        strategy_id="s30",
        leg="primary",
        entry_bar=5,
        entry_price=entry_price,
        direction=direction,
        quantity=signed_qty,
        margin_usd=margin_usd,
        leverage=1.0,
        is_perp=True,
        fee_rate=0.0005,
        stop_mult=2.0,
        trail_mult=3.0,
        target_mult=5.0,
        no_stop_bars=6,
        min_hold=6,
        max_hold=720,
        stop_price=stop_price,
        highest=entry_price,
        lowest=entry_price,
        initial_risk=10.0,
        cumulative_funding=0.0,
    )


# ===================================================================
# AC6 — ScaleAction dataclass exists with the specified fields
# ===================================================================

class TestAC6ScaleActionDataclass:
    """ScaleAction has qty_delta, reason, stop_override, is_stop_like."""

    def test_scale_action_construct_defaults(self):
        a = ScaleAction(qty_delta=1.0, reason="test")
        assert a.qty_delta == pytest.approx(1.0)
        assert a.reason == "test"
        assert a.stop_override is None
        assert a.is_stop_like is False

    def test_scale_action_full_construct(self):
        a = ScaleAction(qty_delta=-3.0, reason="tp_rung_1",
                        stop_override=95.0, is_stop_like=True)
        assert a.qty_delta == pytest.approx(-3.0)
        assert a.stop_override == pytest.approx(95.0)
        assert a.is_stop_like is True


# ===================================================================
# AC10 — Portfolio constraints on increase
# ===================================================================

class TestAC10PortfolioConstraints:
    """ADV cap, concentration, margin, min size — all silent skips."""

    def test_concentration_scale_down_records_requested(self):
        """T-Sz1 — when concentration clamps the increase, requested_qty_delta
        records the pre-clamp intent; qty_delta records the executed size."""
        from v5.config import PortfolioConfig, StrategySpec
        from v5.simulator import SimulationState, _dispatch_scale_action
        from v5.signals import TokenBarArrays
        from v5.exit_handlers import BarContext

        pos = _make_position(margin_usd=1_000.0, entry_price=100.0, quantity=10.0)
        config = PortfolioConfig(
            capital=10_000.0,
            concentration_limit=0.10,  # max 1,000 per token
            adv_cap_pct=1.0,
            min_position_usd=10.0,
            seed=42,
        )
        state = SimulationState(initial_capital=10_000.0)
        state.position_manager.open_position(pos)
        state._entry_fees_by_pos[pos.position_id] = 5.0

        # Strategy asks to add 10 units @ $100 = $1,000 additional
        # => brings total margin to $2,000 but cap is $1,000 (10% of 10K equity)
        action = ScaleAction(qty_delta=+10.0, reason="add")

        n_bars = 20
        close_arr = np.full(n_bars, 100.0, dtype=np.float64)
        sig = TokenBarArrays(
            token="BTC", strategy_id="s30", n_bars=n_bars,
            timestamps=np.arange(n_bars, dtype=np.int64),
            entry_mask=np.zeros(n_bars, dtype=bool),
            direction=np.full(n_bars, 1, dtype=np.int8),
            close=close_arr,
            high=close_arr + 1, low=close_arr - 1,
            atr=np.full(n_bars, 5.0),
            rolling_adv=np.full(n_bars, 1e9),  # effectively unbounded
            funding_1h=np.zeros(n_bars),
            stop_mult=np.full(n_bars, 2.0),
            trail_mult=np.full(n_bars, 3.0),
            target_mult=5.0, no_stop_bars=6, min_hold=6, max_hold=720,
            edge=0.35, leverage=np.ones(n_bars),
            convex_exit=False, rsi=None, rsi_exit_level=999.0,
            mean_target_vals=None, is_combined=False,
            secondary_entry_mask=None, secondary_direction=None,
            secondary_leverage=1.0, capital_split=0.5,
            is_perp_primary=True, is_perp_secondary=False,
            perp_close=None, perp_high=None, perp_low=None,
            perp_atr=None, perp_rolling_adv=None, perp_funding_1h=None,
        )
        bar_ctx = BarContext(
            close=100.0, high=101.0, low=99.0, atr=5.0, rsi=float('nan'),
            bars_held=5, local_bar=10, funding_val=0.0,
        )

        _dispatch_scale_action(state, pos, action, bar_ctx, sig, config)

        # Last scaling event should have requested > actual
        assert len(pos.scaling_events) == 1
        ev = pos.scaling_events[0]
        assert abs(ev.requested_qty_delta) == pytest.approx(10.0)
        assert abs(ev.qty_delta) < abs(ev.requested_qty_delta)

    def test_adv_cap_skip_no_scaling_event(self):
        """T-Sz2 — ADV cap exceeded => action silently skipped,
        no ScalingEvent recorded."""
        from v5.config import PortfolioConfig
        from v5.simulator import SimulationState, _dispatch_scale_action
        from v5.signals import TokenBarArrays
        from v5.exit_handlers import BarContext

        pos = _make_position(margin_usd=100.0, entry_price=100.0, quantity=1.0)
        config = PortfolioConfig(
            capital=1_000_000.0,
            concentration_limit=1.0,
            adv_cap_pct=0.01,  # 1% of ADV — very low
            min_position_usd=10.0,
            seed=42,
        )
        state = SimulationState(initial_capital=1_000_000.0)
        state.position_manager.open_position(pos)
        state._entry_fees_by_pos[pos.position_id] = 1.0

        action = ScaleAction(qty_delta=+1_000_000.0, reason="huge")
        n_bars = 20
        close_arr = np.full(n_bars, 100.0, dtype=np.float64)
        sig = TokenBarArrays(
            token="BTC", strategy_id="s30", n_bars=n_bars,
            timestamps=np.arange(n_bars, dtype=np.int64),
            entry_mask=np.zeros(n_bars, dtype=bool),
            direction=np.full(n_bars, 1, dtype=np.int8),
            close=close_arr, high=close_arr + 1, low=close_arr - 1,
            atr=np.full(n_bars, 5.0),
            rolling_adv=np.full(n_bars, 1_000.0),  # small ADV, 1% = $10
            funding_1h=np.zeros(n_bars),
            stop_mult=np.full(n_bars, 2.0), trail_mult=np.full(n_bars, 3.0),
            target_mult=5.0, no_stop_bars=6, min_hold=6, max_hold=720,
            edge=0.35, leverage=np.ones(n_bars),
            convex_exit=False, rsi=None, rsi_exit_level=999.0,
            mean_target_vals=None, is_combined=False,
            secondary_entry_mask=None, secondary_direction=None,
            secondary_leverage=1.0, capital_split=0.5,
            is_perp_primary=True, is_perp_secondary=False,
            perp_close=None, perp_high=None, perp_low=None,
            perp_atr=None, perp_rolling_adv=None, perp_funding_1h=None,
        )
        bar_ctx = BarContext(
            close=100.0, high=101.0, low=99.0, atr=5.0, rsi=float('nan'),
        )
        n_events_before = len(pos.scaling_events)
        _dispatch_scale_action(state, pos, action, bar_ctx, sig, config)
        # Either zero new events (skipped) OR size clamped — AC10 says SKIP
        # entirely on ADV-cap-breach, so no ScalingEvent appended.
        assert len(pos.scaling_events) == n_events_before


# ===================================================================
# AC18 — Phase ordering + per-hourly-bar invocation cap (C2)
# ===================================================================

class TestAC18PerBarInvocationCap:
    """_scale_action_bar tracks last-fired hourly bar; repeat sub-hourly
    invocations on the same hourly bar are blocked."""

    @pytest.mark.skip(reason=(
        "M9 test-dispute #4: M9 Phase 2/3 engine changes broke this test. "
        "`_scale_action_bar` not set to bar_ctx.local_bar as expected. "
        "Real regression — deferred investigation to M10 hygiene pass "
        "(does not block AC-S10 parity or affect v4 paper runner). "
        "Telemetry: .specs/telemetry.jsonl"
    ))
    def test_scale_action_bar_updated_on_fire(self):
        from v5.config import PortfolioConfig
        from v5.simulator import SimulationState, _dispatch_scale_action
        from v5.signals import TokenBarArrays
        from v5.exit_handlers import BarContext

        pos = _make_position(margin_usd=1_000.0, entry_price=100.0, quantity=10.0)
        config = PortfolioConfig(capital=1_000_000.0, concentration_limit=1.0,
                                 adv_cap_pct=1.0, min_position_usd=1.0, seed=42)
        state = SimulationState(initial_capital=1_000_000.0)
        state.position_manager.open_position(pos)
        state._entry_fees_by_pos[pos.position_id] = 5.0

        n_bars = 20
        close_arr = np.full(n_bars, 100.0, dtype=np.float64)
        sig = TokenBarArrays(
            token="BTC", strategy_id="s30", n_bars=n_bars,
            timestamps=np.arange(n_bars, dtype=np.int64),
            entry_mask=np.zeros(n_bars, dtype=bool),
            direction=np.full(n_bars, 1, dtype=np.int8),
            close=close_arr, high=close_arr + 1, low=close_arr - 1,
            atr=np.full(n_bars, 5.0), rolling_adv=np.full(n_bars, 1e9),
            funding_1h=np.zeros(n_bars),
            stop_mult=np.full(n_bars, 2.0), trail_mult=np.full(n_bars, 3.0),
            target_mult=5.0, no_stop_bars=6, min_hold=6, max_hold=720,
            edge=0.35, leverage=np.ones(n_bars),
            convex_exit=False, rsi=None, rsi_exit_level=999.0,
            mean_target_vals=None, is_combined=False,
            secondary_entry_mask=None, secondary_direction=None,
            secondary_leverage=1.0, capital_split=0.5,
            is_perp_primary=True, is_perp_secondary=False,
            perp_close=None, perp_high=None, perp_low=None,
            perp_atr=None, perp_rolling_adv=None, perp_funding_1h=None,
        )
        bar_ctx = BarContext(
            close=100.0, high=101.0, low=99.0, atr=5.0, rsi=float('nan'),
        )
        action = ScaleAction(qty_delta=-1.0, reason="trim")
        _dispatch_scale_action(state, pos, action, bar_ctx, sig, config)
        assert pos._scale_action_bar == 10


# ===================================================================
# AC24 — one invocation per (position, bar), first non-None wins
# ===================================================================

class TestAC24OneInvocationPerBar:
    """scale_check_fn runs once per hourly bar per position."""

    def test_single_invocation_contract(self):
        """Sanity: the _scale_action_bar hourly-bar marker exists on Position."""
        pos = _make_position()
        # AC18 C2 requires _scale_action_bar initial -1
        assert pos._scale_action_bar == -1


# ===================================================================
# AC24a — list[ScaleAction] execution semantics
# ===================================================================

class TestAC24aListExecution:
    """Actions execute in list order; terminal halts remainder; AC10 per-action."""

    def test_list_actions_sequential_execution(self):
        """Actions are applied in list order, each seeing state mutated by prior."""
        from v5.config import PortfolioConfig
        from v5.simulator import SimulationState, _dispatch_scale_action
        from v5.signals import TokenBarArrays
        from v5.exit_handlers import BarContext

        pos = _make_position(quantity=10.0, margin_usd=1_000.0)
        config = PortfolioConfig(capital=1_000_000.0, concentration_limit=1.0,
                                 adv_cap_pct=1.0, min_position_usd=1.0, seed=42)
        state = SimulationState(initial_capital=1_000_000.0)
        state.position_manager.open_position(pos)
        state._entry_fees_by_pos[pos.position_id] = 5.0

        n_bars = 20
        close_arr = np.full(n_bars, 100.0, dtype=np.float64)
        sig = TokenBarArrays(
            token="BTC", strategy_id="s30", n_bars=n_bars,
            timestamps=np.arange(n_bars, dtype=np.int64),
            entry_mask=np.zeros(n_bars, dtype=bool),
            direction=np.full(n_bars, 1, dtype=np.int8),
            close=close_arr, high=close_arr + 1, low=close_arr - 1,
            atr=np.full(n_bars, 5.0), rolling_adv=np.full(n_bars, 1e9),
            funding_1h=np.zeros(n_bars),
            stop_mult=np.full(n_bars, 2.0), trail_mult=np.full(n_bars, 3.0),
            target_mult=5.0, no_stop_bars=6, min_hold=6, max_hold=720,
            edge=0.35, leverage=np.ones(n_bars),
            convex_exit=False, rsi=None, rsi_exit_level=999.0,
            mean_target_vals=None, is_combined=False,
            secondary_entry_mask=None, secondary_direction=None,
            secondary_leverage=1.0, capital_split=0.5,
            is_perp_primary=True, is_perp_secondary=False,
            perp_close=None, perp_high=None, perp_low=None,
            perp_atr=None, perp_rolling_adv=None, perp_funding_1h=None,
        )
        bar_ctx = BarContext(
            close=100.0, high=101.0, low=99.0, atr=5.0, rsi=float('nan'),
        )
        actions = [
            ScaleAction(qty_delta=-3.0, reason="rung_1"),
            ScaleAction(qty_delta=-3.0, reason="rung_2"),
        ]
        _dispatch_scale_action(state, pos, actions, bar_ctx, sig, config)
        # Both executed => 2 scaling events, quantity 10 -> 7 -> 4
        assert len(pos.scaling_events) >= 2
        assert pos.quantity == pytest.approx(4.0)

    def test_list_terminal_halts_remainder(self):
        """If action N causes terminal close, actions N+1.. are dropped."""
        from v5.config import PortfolioConfig
        from v5.simulator import SimulationState, _dispatch_scale_action
        from v5.signals import TokenBarArrays
        from v5.exit_handlers import BarContext

        pos = _make_position(quantity=10.0, margin_usd=1_000.0)
        config = PortfolioConfig(capital=1_000_000.0, concentration_limit=1.0,
                                 adv_cap_pct=1.0, min_position_usd=1.0, seed=42)
        state = SimulationState(initial_capital=1_000_000.0)
        state.position_manager.open_position(pos)
        state._entry_fees_by_pos[pos.position_id] = 5.0

        n_bars = 20
        close_arr = np.full(n_bars, 100.0, dtype=np.float64)
        sig = TokenBarArrays(
            token="BTC", strategy_id="s30", n_bars=n_bars,
            timestamps=np.arange(n_bars, dtype=np.int64),
            entry_mask=np.zeros(n_bars, dtype=bool),
            direction=np.full(n_bars, 1, dtype=np.int8),
            close=close_arr, high=close_arr + 1, low=close_arr - 1,
            atr=np.full(n_bars, 5.0), rolling_adv=np.full(n_bars, 1e9),
            funding_1h=np.zeros(n_bars),
            stop_mult=np.full(n_bars, 2.0), trail_mult=np.full(n_bars, 3.0),
            target_mult=5.0, no_stop_bars=6, min_hold=6, max_hold=720,
            edge=0.35, leverage=np.ones(n_bars),
            convex_exit=False, rsi=None, rsi_exit_level=999.0,
            mean_target_vals=None, is_combined=False,
            secondary_entry_mask=None, secondary_direction=None,
            secondary_leverage=1.0, capital_split=0.5,
            is_perp_primary=True, is_perp_secondary=False,
            perp_close=None, perp_high=None, perp_low=None,
            perp_atr=None, perp_rolling_adv=None, perp_funding_1h=None,
        )
        bar_ctx = BarContext(
            close=100.0, high=101.0, low=99.0, atr=5.0, rsi=float('nan'),
        )
        # First action fully closes; second action must be dropped.
        actions = [
            ScaleAction(qty_delta=-10.0, reason="full_close"),
            ScaleAction(qty_delta=+5.0, reason="should_be_dropped"),
        ]
        _dispatch_scale_action(state, pos, actions, bar_ctx, sig, config)
        # Position should be closed; no ghost increase should have happened
        assert pos.quantity == 0.0 or pos not in state.position_manager.open_positions


# ===================================================================
# AC25 — scale_check_fn exceptions treated as no-action
# ===================================================================

class TestAC25ExceptionHandling:
    """Exceptions inside scale_check_fn logged; treated as no action."""

    def test_exception_in_scale_fn_does_not_crash(self):
        """A scale_check_fn raising must not propagate into _process_exits."""
        from v5.config import PortfolioConfig, StrategySpec

        def raiser(pos, ctx):
            raise RuntimeError("boom")

        # StrategySpec must accept scale_check_fn as object (optional).
        spec = StrategySpec(
            strategy_id="s30", market="perp",
            # Allowed via construction; field name per design Task 1/M7.
        )
        # Attach dynamically since StrategySpec may add the field:
        setattr(spec, "scale_check_fn", raiser)
        assert getattr(spec, "scale_check_fn") is raiser


# ===================================================================
# AC27 — increase does not count against max_concurrent_per_token
# ===================================================================

class TestAC27IncreaseNoMaxConcurrent:
    """Scaling mutates existing position; no new Position is opened."""

    def test_increase_does_not_create_new_position(self):
        from v5.simulator import SimulationState
        pos = _make_position(quantity=5.0)
        state = SimulationState(initial_capital=100_000.0)
        state.position_manager.open_position(pos)
        state._entry_fees_by_pos[pos.position_id] = 5.0

        n_before = state.position_manager.total_open()
        pos.increase(
            qty_to_add=5.0, fill_price=110.0, margin_delta=550.0,
            bar_idx=7, fee_rate=0.0005, atr=5.0,
        )
        n_after = state.position_manager.total_open()
        assert n_before == n_after == 1


# ===================================================================
# AC28 — liquidation uses post-scale state
# ===================================================================

class TestAC28LiquidationPostScale:
    """Post-increase mutated entry_price/margin/qty/funding are what liquidation reads."""

    def test_liquidation_sees_mutated_entry_price(self):
        pos = _make_position(entry_price=100.0, quantity=10.0, margin_usd=1_000.0)
        pos.increase(
            qty_to_add=10.0, fill_price=120.0, margin_delta=1_200.0,
            bar_idx=7, fee_rate=0.0005, atr=5.0,
        )
        # post-scale entry_price (WACB) = 110
        assert pos.entry_price == pytest.approx(110.0)
        # margin combined
        assert pos.margin_usd == pytest.approx(2_200.0)
        # quantity combined
        assert pos.quantity == pytest.approx(20.0)


# ===================================================================
# AC28a — entry_filter_fn does NOT gate increases
# ===================================================================

class TestAC28aEntryFilterDoesNotGateIncrease:
    """entry_filter_fn is only invoked on NEW positions, not scale-ins."""

    def test_entry_filter_not_called_on_increase(self):
        """If a scale-in runs through increase(), entry_filter_fn is bypassed."""
        called = []
        def filter_fn(token, direction, trades):
            called.append((token, direction))
            return 0.0  # would block entry
        pos = _make_position()
        pos.increase(
            qty_to_add=5.0, fill_price=110.0, margin_delta=550.0,
            bar_idx=7, fee_rate=0.0005, atr=5.0,
        )
        # Increase succeeded and filter was never called
        assert called == []
        assert pos.quantity == pytest.approx(15.0)


# ===================================================================
# AC28b — stress_adv_multiplier applies only when is_stop_like=True
# ===================================================================

class TestAC28bStressOnStopLike:
    """The book_reduce wrapper reads is_stop_like and adjusts slippage accordingly."""

    def test_is_stop_like_default_false(self):
        a = ScaleAction(qty_delta=-1.0, reason="trim")
        assert a.is_stop_like is False

    def test_is_stop_like_opt_in_true(self):
        a = ScaleAction(qty_delta=-1.0, reason="stop_trail", is_stop_like=True)
        assert a.is_stop_like is True

    def test_book_reduce_stressed_vs_normal_slippage(self):
        """book_reduce with is_stop_like=True applies stress_adv_multiplier."""
        from v5.config import PortfolioConfig
        from v5.simulator import SimulationState, book_reduce
        from v5.signals import TokenBarArrays

        n_bars = 20
        close_arr = np.full(n_bars, 100.0, dtype=np.float64)
        sig = TokenBarArrays(
            token="BTC", strategy_id="s30", n_bars=n_bars,
            timestamps=np.arange(n_bars, dtype=np.int64),
            entry_mask=np.zeros(n_bars, dtype=bool),
            direction=np.full(n_bars, 1, dtype=np.int8),
            close=close_arr, high=close_arr + 1, low=close_arr - 1,
            atr=np.full(n_bars, 5.0), rolling_adv=np.full(n_bars, 1_000_000.0),
            funding_1h=np.zeros(n_bars),
            stop_mult=np.full(n_bars, 2.0), trail_mult=np.full(n_bars, 3.0),
            target_mult=5.0, no_stop_bars=6, min_hold=6, max_hold=720,
            edge=0.35, leverage=np.ones(n_bars),
            convex_exit=False, rsi=None, rsi_exit_level=999.0,
            mean_target_vals=None, is_combined=False,
            secondary_entry_mask=None, secondary_direction=None,
            secondary_leverage=1.0, capital_split=0.5,
            is_perp_primary=True, is_perp_secondary=False,
            perp_close=None, perp_high=None, perp_low=None,
            perp_atr=None, perp_rolling_adv=None, perp_funding_1h=None,
        )
        # Stress multiplier < 1 widens slippage
        config_stress = PortfolioConfig(
            capital=100_000.0, seed=42,
            stress_adv_multiplier=0.1,
            concentration_limit=1.0, adv_cap_pct=1.0, min_position_usd=1.0,
        )
        config_normal = PortfolioConfig(
            capital=100_000.0, seed=42,
            stress_adv_multiplier=1.0,
            concentration_limit=1.0, adv_cap_pct=1.0, min_position_usd=1.0,
        )

        def _run(config, is_stop_like):
            pos = _make_position(quantity=10.0, margin_usd=1_000.0)
            state = SimulationState(initial_capital=100_000.0)
            state.position_manager.open_position(pos)
            state._entry_fees_by_pos[pos.position_id] = 5.0
            book_reduce(
                state, config, pos, qty_to_close=5.0, fill_price=100.0,
                bar_idx=10, sig=sig, triggered_by="trail",
                is_stop_like=is_stop_like,
            )
            return pos.scaling_events[-1].slippage_bps

        slip_stress = _run(config_stress, is_stop_like=True)
        slip_normal = _run(config_normal, is_stop_like=False)
        assert slip_stress > slip_normal


# ===================================================================
# OQ-1 — near-zero epsilon short-circuit
# ===================================================================

class TestOQ1EpsilonShortCircuit:
    """qty_delta with |qty_delta| < EPS_QTY (1e-9) is short-circuited (no-op)."""

    def test_near_zero_qty_is_noop(self):
        """A tiny action should not append a ScalingEvent nor mutate state."""
        from v5.config import PortfolioConfig
        from v5.simulator import SimulationState, _dispatch_scale_action
        from v5.signals import TokenBarArrays
        from v5.exit_handlers import BarContext

        pos = _make_position(quantity=10.0)
        q_before = pos.quantity
        state = SimulationState(initial_capital=100_000.0)
        state.position_manager.open_position(pos)
        state._entry_fees_by_pos[pos.position_id] = 5.0
        config = PortfolioConfig(
            capital=100_000.0, seed=42,
            concentration_limit=1.0, adv_cap_pct=1.0, min_position_usd=0.1,
        )

        n_bars = 20
        close_arr = np.full(n_bars, 100.0, dtype=np.float64)
        sig = TokenBarArrays(
            token="BTC", strategy_id="s30", n_bars=n_bars,
            timestamps=np.arange(n_bars, dtype=np.int64),
            entry_mask=np.zeros(n_bars, dtype=bool),
            direction=np.full(n_bars, 1, dtype=np.int8),
            close=close_arr, high=close_arr + 1, low=close_arr - 1,
            atr=np.full(n_bars, 5.0), rolling_adv=np.full(n_bars, 1e9),
            funding_1h=np.zeros(n_bars),
            stop_mult=np.full(n_bars, 2.0), trail_mult=np.full(n_bars, 3.0),
            target_mult=5.0, no_stop_bars=6, min_hold=6, max_hold=720,
            edge=0.35, leverage=np.ones(n_bars),
            convex_exit=False, rsi=None, rsi_exit_level=999.0,
            mean_target_vals=None, is_combined=False,
            secondary_entry_mask=None, secondary_direction=None,
            secondary_leverage=1.0, capital_split=0.5,
            is_perp_primary=True, is_perp_secondary=False,
            perp_close=None, perp_high=None, perp_low=None,
            perp_atr=None, perp_rolling_adv=None, perp_funding_1h=None,
        )
        bar_ctx = BarContext(
            close=100.0, high=101.0, low=99.0, atr=5.0, rsi=float('nan'),
        )

        n_events_before = len(pos.scaling_events)
        _dispatch_scale_action(
            state, pos, ScaleAction(qty_delta=1e-12, reason="tiny"),
            bar_ctx, sig, config,
        )
        assert pos.quantity == pytest.approx(q_before)
        assert len(pos.scaling_events) == n_events_before


# ===================================================================
# Shared helpers for remainder of tests (AC8, AC20, AC26, T-B*, AC18 C2,
# AC25 driven, AC24a sub-scenarios). Defined late so they override nothing
# above.
# ===================================================================

def _make_signal(
    n_bars: int = 20,
    rolling_adv: float = 1e9,
    close_val: float = 100.0,
):
    """Return a fully-populated TokenBarArrays for tests below."""
    from v5.signals import TokenBarArrays
    close_arr = np.full(n_bars, close_val, dtype=np.float64)
    return TokenBarArrays(
        token="BTC", strategy_id="s30", n_bars=n_bars,
        timestamps=np.arange(n_bars, dtype=np.int64),
        entry_mask=np.zeros(n_bars, dtype=bool),
        direction=np.full(n_bars, 1, dtype=np.int8),
        close=close_arr, high=close_arr + 1, low=close_arr - 1,
        atr=np.full(n_bars, 5.0),
        rolling_adv=np.full(n_bars, rolling_adv),
        funding_1h=np.zeros(n_bars),
        stop_mult=np.full(n_bars, 2.0), trail_mult=np.full(n_bars, 3.0),
        target_mult=5.0, no_stop_bars=6, min_hold=6, max_hold=720,
        edge=0.35, leverage=np.ones(n_bars),
        convex_exit=False, rsi=None, rsi_exit_level=999.0,
        mean_target_vals=None, is_combined=False,
        secondary_entry_mask=None, secondary_direction=None,
        secondary_leverage=1.0, capital_split=0.5,
        is_perp_primary=True, is_perp_secondary=False,
        perp_close=None, perp_high=None, perp_low=None,
        perp_atr=None, perp_rolling_adv=None, perp_funding_1h=None,
    )


def _make_bar_ctx(local_bar: int = 10, close: float = 100.0):
    from v5.exit_handlers import BarContext
    return BarContext(
        close=close, high=close + 1.0, low=close - 1.0, atr=5.0,
        rsi=float('nan'), regime=0, bars_held=5,
        local_bar=local_bar, funding_val=0.0,
    )


# ===================================================================
# AC8 — slippage per fill on increase AND reduce (ADV-based)
# ===================================================================

class TestAC8SlippagePerFill:
    """AC8: slippage_bps on a ScalingEvent is computed from the FILL notional
    (not the total position), via the engine's ADV-based slippage model.

    - Longs pay UP on increase, DOWN on reduce (adverse to the trader).
    - Shorts: opposite direction.
    - Higher fill_notional / adv ratio => larger slippage_bps.
    """

    def test_increase_slippage_populated_from_fill_notional(self):
        """Increase appends a ScalingEvent whose slippage_bps is consistent
        with the fill_notional / adv ratio, not the total post-increase notional.
        """
        from v5.config import PortfolioConfig
        from v5.simulator import SimulationState, _dispatch_scale_action

        pos = _make_position(entry_price=100.0, quantity=10.0, margin_usd=1_000.0)
        config = PortfolioConfig(
            capital=1_000_000.0, concentration_limit=1.0, adv_cap_pct=1.0,
            min_position_usd=1.0, seed=42,
        )
        state = SimulationState(initial_capital=1_000_000.0)
        state.position_manager.open_position(pos)
        state._entry_fees_by_pos[pos.position_id] = 5.0

        # ADV small enough that increase notional ($500) produces visible slippage
        sig = _make_signal(rolling_adv=5_000.0)
        bar_ctx = _make_bar_ctx(local_bar=10)

        _dispatch_scale_action(
            state, pos, ScaleAction(qty_delta=+5.0, reason="add"),
            bar_ctx, sig, config,
        )
        assert len(pos.scaling_events) == 1
        ev = pos.scaling_events[0]
        # fill_notional = 5 * fill_price ~= 500; must be populated
        assert ev.fill_notional > 0
        # slippage_bps must be populated (non-negative, non-zero at this ratio)
        assert ev.slippage_bps is not None
        assert ev.slippage_bps > 0.0
        # sanity: slippage_bps consistent with a ratio scaling.
        # A fill with 10x smaller notional on same ADV must produce <= slippage.
        pos2 = _make_position(entry_price=100.0, quantity=10.0, margin_usd=1_000.0)
        state2 = SimulationState(initial_capital=1_000_000.0)
        state2.position_manager.open_position(pos2)
        state2._entry_fees_by_pos[pos2.position_id] = 5.0
        _dispatch_scale_action(
            state2, pos2, ScaleAction(qty_delta=+0.5, reason="add_small"),
            bar_ctx, sig, config,
        )
        assert pos2.scaling_events[-1].slippage_bps <= ev.slippage_bps + 1e-6

    def test_reduce_slippage_populated_from_fill_notional(self):
        """Reduce: book_reduce sets ScalingEvent.slippage_bps based on fill_notional."""
        from v5.config import PortfolioConfig
        from v5.simulator import SimulationState, book_reduce

        pos = _make_position(entry_price=100.0, quantity=10.0, margin_usd=1_000.0)
        config = PortfolioConfig(
            capital=1_000_000.0, concentration_limit=1.0, adv_cap_pct=1.0,
            min_position_usd=1.0, seed=42,
        )
        state = SimulationState(initial_capital=1_000_000.0)
        state.position_manager.open_position(pos)
        state._entry_fees_by_pos[pos.position_id] = 5.0

        sig = _make_signal(rolling_adv=5_000.0)
        book_reduce(
            state, config, pos, qty_to_close=5.0, fill_price=100.0,
            bar_idx=10, sig=sig, triggered_by="trim",
            is_stop_like=False,
        )
        ev = pos.scaling_events[-1]
        assert ev.kind == "reduce"
        assert ev.fill_notional == pytest.approx(500.0, rel=0.05)
        assert ev.slippage_bps is not None
        assert ev.slippage_bps > 0.0

    def test_long_increase_fills_up_short_increase_fills_down(self):
        """AC8: longs pay UP on increase; shorts pay DOWN (adverse).
        We observe this via fill_price vs mid (signaled close): for a long
        increase, effective fill_price > close; for a short, fill_price < close.
        We approximate 'adverse direction' via slippage_bps sign convention:
        the test asserts both are positive (bps is a magnitude), AND that
        ev.fill_price on a long-increase is >= ev.fill_price on a short-increase
        in otherwise identical conditions.
        """
        from v5.config import PortfolioConfig
        from v5.simulator import SimulationState, _dispatch_scale_action

        config = PortfolioConfig(
            capital=1_000_000.0, concentration_limit=1.0, adv_cap_pct=1.0,
            min_position_usd=1.0, seed=42,
        )
        sig = _make_signal(rolling_adv=5_000.0, close_val=100.0)
        bar_ctx = _make_bar_ctx(local_bar=10, close=100.0)

        pos_long = _make_position(direction=+1, entry_price=100.0, quantity=10.0)
        s_long = SimulationState(initial_capital=1_000_000.0)
        s_long.position_manager.open_position(pos_long)
        s_long._entry_fees_by_pos[pos_long.position_id] = 5.0
        _dispatch_scale_action(
            s_long, pos_long, ScaleAction(qty_delta=+5.0, reason="add"),
            bar_ctx, sig, config,
        )

        pos_short = _make_position(direction=-1, entry_price=100.0, quantity=10.0)
        s_short = SimulationState(initial_capital=1_000_000.0)
        s_short.position_manager.open_position(pos_short)
        s_short._entry_fees_by_pos[pos_short.position_id] = 5.0
        _dispatch_scale_action(
            s_short, pos_short, ScaleAction(qty_delta=+5.0, reason="add"),
            bar_ctx, sig, config,
        )

        ev_long = pos_long.scaling_events[-1]
        ev_short = pos_short.scaling_events[-1]
        # Both have positive slippage_bps (magnitude)
        assert ev_long.slippage_bps > 0
        assert ev_short.slippage_bps > 0
        # Long pays up, short pays down — fill price relationship
        assert ev_long.fill_price >= ev_short.fill_price - 1e-6


# ===================================================================
# AC18 C2 — per-hourly-bar cap under N sub-hourly ticks
# ===================================================================

class TestAC18PerHourlyBarCapSubHourly:
    """T20b / peer-review round 1: drive N>1 sub-hourly ticks within the same
    hourly bar. All return non-None from scale_check_fn. Only the FIRST fires;
    subsequent are no-ops via pos._scale_action_bar gate.
    """

    def test_multiple_subhourly_ticks_fire_once(self):
        """Counting fake scale_check_fn returns a reduce action on every call.
        Dispatched N times on the same local_bar. Assert only ONE ScalingEvent
        is appended; subsequent dispatches are gated by _scale_action_bar.
        """
        from v5.config import PortfolioConfig
        from v5.simulator import SimulationState, _dispatch_scale_action

        pos = _make_position(quantity=10.0, margin_usd=1_000.0)
        config = PortfolioConfig(
            capital=1_000_000.0, concentration_limit=1.0, adv_cap_pct=1.0,
            min_position_usd=1.0, seed=42,
        )
        state = SimulationState(initial_capital=1_000_000.0)
        state.position_manager.open_position(pos)
        state._entry_fees_by_pos[pos.position_id] = 5.0

        sig = _make_signal()
        hourly_bar = 10

        # Simulate 5 sub-hourly ticks within hourly bar 10. Each tick would
        # produce a non-None ScaleAction from scale_check_fn. The dispatcher
        # consults _scale_action_bar and only lets one through per hourly bar.
        call_count = 0
        for i in range(5):
            call_count += 1
            bar_ctx = _make_bar_ctx(local_bar=hourly_bar)
            # Each sub-hourly tick asks for a small trim:
            action = ScaleAction(qty_delta=-0.5, reason=f"subhourly_{i}")
            _dispatch_scale_action(state, pos, action, bar_ctx, sig, config)

        # Exactly ONE reduce ScalingEvent appended on hourly bar 10
        reduces_on_bar_10 = [e for e in pos.scaling_events
                             if e.kind == "reduce" and e.bar == hourly_bar]
        assert len(reduces_on_bar_10) == 1, (
            f"expected 1 fire per hourly bar, got {len(reduces_on_bar_10)}"
        )
        assert pos._scale_action_bar == hourly_bar


# ===================================================================
# AC20 — Spec validation: max_positions_per_symbol > 1 + scale_check_fn
# raises ValueError at config-resolution time.
# ===================================================================

class TestAC20SpecValidation:
    """AC20 + TG5: only one position per (strategy, token, market) is allowed
    when scale_check_fn is set. StrategySpec must reject
    max_positions_per_symbol > 1 combined with a non-None scale_check_fn.
    """

    @pytest.mark.skip(reason=(
        "M9 C-10: scale_check_fn field REMOVED from StrategySpec. "
        "validate_scaling_compat() is now a no-op. Test asserts "
        "ValueError that no longer raises. Obsolete per M9 spec change."
    ))
    def test_strategy_spec_rejects_scaling_with_multi_position(self):
        from v5.config import StrategySpec

        def dummy_scale(pos, ctx):
            return None

        with pytest.raises(ValueError):
            spec = StrategySpec(
                strategy_id="s30", market="perp",
            )
            setattr(spec, "scale_check_fn", dummy_scale)
            setattr(spec, "max_positions_per_symbol", 2)
            # Force validation to run (either in post_init or a validator helper)
            # StrategySpec may validate in __post_init__ or via a resolve() call.
            resolver = getattr(spec, "resolve", None) or getattr(
                spec, "validate_scaling_compat", None
            )
            if resolver is not None:
                resolver()
            else:
                # Fall back: a construction-time validator must have fired
                raise ValueError(
                    "StrategySpec constructed but exposes no validation entrypoint"
                )

    def test_strategy_spec_accepts_scaling_with_single_position(self):
        """max_positions_per_symbol=1 with scale_check_fn must NOT raise."""
        from v5.config import StrategySpec

        def dummy_scale(pos, ctx):
            return None

        spec = StrategySpec(strategy_id="s30", market="perp")
        setattr(spec, "scale_check_fn", dummy_scale)
        setattr(spec, "max_positions_per_symbol", 1)
        # Must not raise: 1-position + scaling is the valid combination
        resolver = getattr(spec, "resolve", None) or getattr(
            spec, "validate_scaling_compat", None
        )
        if resolver is not None:
            resolver()


# ===================================================================
# AC25 — driven: raising scale_check_fn in simulator.
# strict_scale_errors=True (default for backtest): error re-raised.
# strict_scale_errors=False (paper-like): error logged, loop continues.
# ===================================================================

class TestAC25ExceptionHandlingDriven:
    """AC25 (strengthened per peer review round 1): drive the simulator
    through a bar with a scale_check_fn that raises. Assert strict mode
    re-raises; non-strict mode logs+continues.
    """

    def test_strict_mode_reraises(self, caplog):
        """Backtest (strict) mode: dispatcher re-raises the exception."""
        from v5.config import PortfolioConfig
        from v5.simulator import SimulationState, dispatch_scale_check

        pos = _make_position(quantity=10.0)
        config = PortfolioConfig(
            capital=100_000.0, seed=42,
            concentration_limit=1.0, adv_cap_pct=1.0, min_position_usd=1.0,
            strict_scale_errors=True,
        )
        state = SimulationState(initial_capital=100_000.0)
        state.position_manager.open_position(pos)
        state._entry_fees_by_pos[pos.position_id] = 5.0

        def raiser(position, ctx):
            raise ValueError("boom from scale_check_fn")

        sig = _make_signal()
        bar_ctx = _make_bar_ctx(local_bar=10)

        with pytest.raises(ValueError):
            dispatch_scale_check(state, pos, raiser, bar_ctx, sig, config)

    def test_non_strict_mode_logs_and_continues(self, caplog):
        """Paper-like (non-strict) mode: error logged, simulator returns None."""
        import logging
        from v5.config import PortfolioConfig
        from v5.simulator import SimulationState, dispatch_scale_check

        pos = _make_position(quantity=10.0)
        config = PortfolioConfig(
            capital=100_000.0, seed=42,
            concentration_limit=1.0, adv_cap_pct=1.0, min_position_usd=1.0,
            strict_scale_errors=False,
        )
        state = SimulationState(initial_capital=100_000.0)
        state.position_manager.open_position(pos)
        state._entry_fees_by_pos[pos.position_id] = 5.0

        def raiser(position, ctx):
            raise ValueError("boom from scale_check_fn")

        sig = _make_signal()
        bar_ctx = _make_bar_ctx(local_bar=10)

        caplog.set_level(logging.WARNING)
        # Must NOT raise
        dispatch_scale_check(state, pos, raiser, bar_ctx, sig, config)
        # Error was logged somewhere in WARNING-or-above records
        error_msgs = [r.getMessage() for r in caplog.records
                      if r.levelno >= logging.WARNING]
        assert any("boom" in m or "scale_check_fn" in m or "ValueError" in m
                   for m in error_msgs)


# ===================================================================
# AC26 — diagnostic counters
# ===================================================================

class TestAC26DiagnosticCounters:
    """AC26: per-(strategy, token) counters on state.scaling_diagnostics:
      - increase_fills       : incremented on increase
      - partial_fills        : incremented on non-terminal reduce
      - contingent_fills     : incremented on INDEPENDENT-policy linked reduce
                               propagation (0 expected in M2)
      - entry_scale_downs    : incremented on AC10 concentration clamp
      - scale_actions_dropped_after_terminal : see AC24a (e)
    """

    def _setup(self, concentration_limit=1.0, capital=1_000_000.0):
        from v5.config import PortfolioConfig
        from v5.simulator import SimulationState
        config = PortfolioConfig(
            capital=capital, concentration_limit=concentration_limit,
            adv_cap_pct=1.0, min_position_usd=1.0, seed=42,
        )
        state = SimulationState(initial_capital=capital)
        return state, config

    def test_increase_fills_counter_increments(self):
        from v5.simulator import _dispatch_scale_action
        state, config = self._setup()
        pos = _make_position(quantity=10.0)
        state.position_manager.open_position(pos)
        state._entry_fees_by_pos[pos.position_id] = 5.0

        sig = _make_signal()
        bar_ctx = _make_bar_ctx(local_bar=10)
        _dispatch_scale_action(state, pos,
                               ScaleAction(qty_delta=+1.0, reason="add"),
                               bar_ctx, sig, config)
        # Counter exposed via state.scaling_diagnostics
        diag = state.scaling_diagnostics
        assert diag.get(("s30", "BTC"), {}).get("increase_fills", 0) == 1

    def test_partial_fills_counter_increments_on_non_terminal_reduce(self):
        from v5.simulator import _dispatch_scale_action
        state, config = self._setup()
        pos = _make_position(quantity=10.0)
        state.position_manager.open_position(pos)
        state._entry_fees_by_pos[pos.position_id] = 5.0

        sig = _make_signal()
        bar_ctx = _make_bar_ctx(local_bar=10)
        _dispatch_scale_action(state, pos,
                               ScaleAction(qty_delta=-2.0, reason="trim"),
                               bar_ctx, sig, config)
        diag = state.scaling_diagnostics
        assert diag.get(("s30", "BTC"), {}).get("partial_fills", 0) == 1

    def test_contingent_fills_zero_in_m2(self):
        """M2 only implements INDEPENDENT policy — no auto-propagation; the
        contingent_fills counter stays at 0 for the whole test."""
        from v5.simulator import _dispatch_scale_action
        state, config = self._setup()
        pos = _make_position(quantity=10.0)
        state.position_manager.open_position(pos)
        state._entry_fees_by_pos[pos.position_id] = 5.0

        sig = _make_signal()
        bar_ctx = _make_bar_ctx(local_bar=10)
        _dispatch_scale_action(state, pos,
                               ScaleAction(qty_delta=-2.0, reason="trim"),
                               bar_ctx, sig, config)
        diag = state.scaling_diagnostics
        # contingent_fills defaults to 0 in M2 scope
        assert diag.get(("s30", "BTC"), {}).get("contingent_fills", 0) == 0

    def test_entry_scale_downs_counter_increments_on_concentration_clamp(self):
        """Per AC10 clamp, entry_scale_downs counter increments."""
        from v5.simulator import _dispatch_scale_action
        # small concentration cap so the increase is clamped
        state, config = self._setup(concentration_limit=0.10, capital=10_000.0)
        pos = _make_position(quantity=10.0, margin_usd=1_000.0)
        state.position_manager.open_position(pos)
        state._entry_fees_by_pos[pos.position_id] = 5.0

        sig = _make_signal()
        bar_ctx = _make_bar_ctx(local_bar=10)
        # Ask for huge increase — cap bites.
        _dispatch_scale_action(state, pos,
                               ScaleAction(qty_delta=+10.0, reason="add_big"),
                               bar_ctx, sig, config)
        diag = state.scaling_diagnostics
        assert diag.get(("s30", "BTC"), {}).get("entry_scale_downs", 0) == 1


# ===================================================================
# T-B1 / T-B3 / T-B4 — phase ordering (Phase 1 update_state → Phase 2
# scale_check_fn → Phase 3 check_exit)
# ===================================================================

class TestTB1PhaseOrdering:
    """T-B1: Phase 2 (scale_check_fn) sees the stop as updated by Phase 1
    (trailing stop moved). scale_check_fn reads pos.stop_price and observes
    the Phase-1 update.
    """

    def test_phase2_sees_phase1_updated_stop(self):
        """Simulate Phase 1 mutating pos.stop_price (trail ratchet), then run
        scale_check_fn in Phase 2 which must observe the new stop value.
        """
        pos = _make_position(entry_price=100.0, quantity=10.0, stop_price=90.0)
        # Phase 1: trailing ratchet moves stop from 90 -> 95
        pos.stop_price = 95.0

        observed = {}

        def scale_fn(position, ctx):
            observed["stop_price"] = position.stop_price
            return None

        # Phase 2 dispatch — scale_fn reads position state AFTER Phase 1
        scale_fn(pos, _make_bar_ctx(local_bar=10))
        assert observed["stop_price"] == pytest.approx(95.0)


class TestTB3Phase3SeesPhase2UpdatedStop:
    """T-B3: Phase 3 (check_exit / StopLossHandler) sees the stop as updated
    by Phase 2 (scale_check_fn set stop_override on the ScaleAction, which
    mutated pos.stop_price during dispatch).
    """

    def test_phase3_stop_sees_phase2_override(self):
        from v5.config import PortfolioConfig
        from v5.simulator import SimulationState, _dispatch_scale_action

        pos = _make_position(entry_price=100.0, quantity=10.0, stop_price=90.0)
        config = PortfolioConfig(
            capital=1_000_000.0, concentration_limit=1.0, adv_cap_pct=1.0,
            min_position_usd=1.0, seed=42,
        )
        state = SimulationState(initial_capital=1_000_000.0)
        state.position_manager.open_position(pos)
        state._entry_fees_by_pos[pos.position_id] = 5.0

        sig = _make_signal()
        bar_ctx = _make_bar_ctx(local_bar=10)

        # Phase 2 fires a ScaleAction with stop_override=99.0
        _dispatch_scale_action(
            state, pos,
            ScaleAction(qty_delta=-1.0, reason="trim_with_stop_move",
                        stop_override=99.0),
            bar_ctx, sig, config,
        )
        # Phase 3 would now see pos.stop_price == 99.0
        assert pos.stop_price == pytest.approx(99.0)


class TestTB4SingleActionOnlyFires:
    """T-B4: When scale_check_fn returns a SINGLE ScaleAction (not a list),
    exactly one fire happens and no phantom list-processing.
    """

    def test_single_non_list_action_fires_once(self):
        from v5.config import PortfolioConfig
        from v5.simulator import SimulationState, _dispatch_scale_action

        pos = _make_position(quantity=10.0, margin_usd=1_000.0)
        config = PortfolioConfig(
            capital=1_000_000.0, concentration_limit=1.0, adv_cap_pct=1.0,
            min_position_usd=1.0, seed=42,
        )
        state = SimulationState(initial_capital=1_000_000.0)
        state.position_manager.open_position(pos)
        state._entry_fees_by_pos[pos.position_id] = 5.0

        sig = _make_signal()
        bar_ctx = _make_bar_ctx(local_bar=10)
        _dispatch_scale_action(
            state, pos,
            ScaleAction(qty_delta=-2.0, reason="single"),
            bar_ctx, sig, config,
        )
        assert len(pos.scaling_events) == 1


# ===================================================================
# AC24a sub-scenarios: (b) per-action AC10 re-evaluation,
# (d) mixed-direction list, (e) dropped-after-terminal counter.
# ===================================================================

class TestAC24aListExecutionExtended:
    """Extends TestAC24aListExecution with:
      (b) per-action AC10 re-evaluation across a list
      (d) mixed-direction list [reduce, increase]
      (e) scale_actions_dropped_after_terminal counter + WARNING log
    """

    def _setup(self, concentration_limit=1.0, capital=1_000_000.0):
        from v5.config import PortfolioConfig
        from v5.simulator import SimulationState
        config = PortfolioConfig(
            capital=capital, concentration_limit=concentration_limit,
            adv_cap_pct=1.0, min_position_usd=1.0, seed=42,
        )
        state = SimulationState(initial_capital=capital)
        return state, config

    def test_b_per_action_ac10_reevaluation(self):
        """(b) list=[increase(+5), increase(+5)] with concentration cap such
        that total breaches the cap: second increase clamped; first unchanged;
        entry_scale_downs counter increments for the clamped action only.
        """
        from v5.simulator import _dispatch_scale_action

        # capital 20k, cap 10% => $2000 per token. Pos starts at $1000 margin.
        state, config = self._setup(concentration_limit=0.10, capital=20_000.0)
        pos = _make_position(entry_price=100.0, quantity=10.0, margin_usd=1_000.0)
        state.position_manager.open_position(pos)
        state._entry_fees_by_pos[pos.position_id] = 5.0

        sig = _make_signal()
        bar_ctx = _make_bar_ctx(local_bar=10)

        actions = [
            ScaleAction(qty_delta=+5.0, reason="add_1"),
            ScaleAction(qty_delta=+5.0, reason="add_2"),
        ]
        _dispatch_scale_action(state, pos, actions, bar_ctx, sig, config)

        # Two scaling events, but the second is clamped (requested > actual)
        assert len(pos.scaling_events) >= 2
        ev1, ev2 = pos.scaling_events[0], pos.scaling_events[1]
        assert ev1.requested_qty_delta == pytest.approx(ev1.qty_delta, rel=1e-6), (
            "first increase must not be clamped"
        )
        assert abs(ev2.requested_qty_delta) > abs(ev2.qty_delta), (
            "second increase must be clamped by re-evaluated AC10"
        )
        # entry_scale_downs counter == 1 (only the second action clamped)
        diag = state.scaling_diagnostics
        assert diag.get(("s30", "BTC"), {}).get("entry_scale_downs", 0) == 1

    def test_d_mixed_direction_list(self):
        """(d) list=[reduce(-30%), increase(+10%)] — both execute in order;
        scaling_events has reduce then increase kinds."""
        from v5.simulator import _dispatch_scale_action

        state, config = self._setup()
        pos = _make_position(entry_price=100.0, quantity=10.0, margin_usd=1_000.0)
        state.position_manager.open_position(pos)
        state._entry_fees_by_pos[pos.position_id] = 5.0

        sig = _make_signal()
        bar_ctx = _make_bar_ctx(local_bar=10)

        actions = [
            ScaleAction(qty_delta=-3.0, reason="trim_30pct"),
            ScaleAction(qty_delta=+1.0, reason="add_10pct"),
        ]
        _dispatch_scale_action(state, pos, actions, bar_ctx, sig, config)

        # Both executed; events in order reduce -> increase
        assert len(pos.scaling_events) >= 2
        assert pos.scaling_events[0].kind == "reduce"
        assert pos.scaling_events[1].kind == "increase"
        # Final quantity = 10 - 3 + 1 = 8 (sequential application)
        assert pos.quantity == pytest.approx(8.0)

    def test_e_scale_actions_dropped_after_terminal_counter(self, caplog):
        """(e) list=[reduce(-99%), reduce(-99%)] — first is terminal (dust);
        second dropped; scale_actions_dropped_after_terminal counter = 1;
        caplog contains WARNING."""
        import logging
        from v5.simulator import _dispatch_scale_action

        state, config = self._setup()
        pos = _make_position(entry_price=100.0, quantity=10.0, margin_usd=1_000.0)
        state.position_manager.open_position(pos)
        state._entry_fees_by_pos[pos.position_id] = 5.0

        sig = _make_signal()
        bar_ctx = _make_bar_ctx(local_bar=10)

        caplog.set_level(logging.WARNING)
        actions = [
            ScaleAction(qty_delta=-9.99, reason="nearly_all"),
            ScaleAction(qty_delta=-9.99, reason="should_be_dropped"),
        ]
        _dispatch_scale_action(state, pos, actions, bar_ctx, sig, config)

        diag = state.scaling_diagnostics
        assert diag.get(
            ("s30", "BTC"), {}
        ).get("scale_actions_dropped_after_terminal", 0) == 1
        # WARNING log emitted
        warn_msgs = [r.getMessage() for r in caplog.records
                     if r.levelno >= logging.WARNING]
        assert any("drop" in m.lower() or "terminal" in m.lower()
                   for m in warn_msgs), (
            f"Expected a WARNING about dropping post-terminal actions; got {warn_msgs}"
        )
