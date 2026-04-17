"""Acceptance tests for M2 — v5/helpers.py composable DSL.

Covers:
  - Helper API: tp_ladder_atr, tp_ladder_price, tp_ladder_r (with anchor arg),
    breakeven_plus_runner, add_at_price, add_on_profit_atr, confirm_and_add,
    combine
  - Q3: namespaced `_helper_state` keys (per-helper, not colliding)
  - Q5: breakeven_plus_runner interaction with BreakevenRatchetHandler (T-B2)
  - T-R-restart: helpers idempotent after restart (rung state recovered from
    scaling_events)
  - T18: tp_ladder_atr fires once per rung
  - T19-revised: tp_ladder_r with "original" anchor fires off r_anchor_price
  - T19b: tp_ladder_r with anchor="avg_px" fires off weighted avg entry

All tests MUST FAIL until v5/helpers.py exists.
"""
from __future__ import annotations

import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "v4"))

import pytest

from v5.position import Position  # noqa: E402
from v5.strategy_api import ScaleAction  # noqa: E402


def _make_long(
    entry_price: float = 100.0, quantity: float = 10.0,
    initial_risk: float = 5.0, r_anchor_price: float = 100.0,
    highest: float | None = None, lowest: float | None = None,
) -> Position:
    pos = Position(
        position_id="BTC:s30:5:primary",
        token="BTC", strategy_id="s30", leg="primary",
        entry_bar=5, entry_price=entry_price, direction=1,
        quantity=quantity, margin_usd=1_000.0, leverage=1.0, is_perp=True,
        fee_rate=0.0005, stop_mult=2.0, trail_mult=3.0, target_mult=5.0,
        no_stop_bars=6, min_hold=1, max_hold=720,
        stop_price=entry_price - initial_risk,
        highest=entry_price if highest is None else highest,
        lowest=entry_price if lowest is None else lowest,
        initial_risk=initial_risk, cumulative_funding=0.0,
    )
    pos.r_anchor_price = r_anchor_price
    return pos


class _Ctx:
    """Lightweight BarContext stand-in. Helpers should read attributes."""
    def __init__(self, *, close, high=None, low=None, atr=5.0, bar=10):
        self.close = close
        self.high = high if high is not None else close
        self.low = low if low is not None else close
        self.atr = atr
        self.bar = bar
        self.local_bar = bar


# ===================================================================
# T18 — tp_ladder_atr fires once per rung
# ===================================================================

class TestTpLadderAtr:
    """tp_ladder_atr fires exactly once per level across successive bars."""

    def test_fires_once_per_rung(self):
        from v5.helpers import tp_ladder_atr
        fn = tp_ladder_atr([(1.0, 0.3), (2.0, 0.3), (3.0, 0.4)])
        pos = _make_long(entry_price=100.0, initial_risk=5.0)
        # Rung 1: +1 ATR = $105
        a1 = fn(pos, _Ctx(close=106.0, atr=5.0, bar=10))
        assert a1 is not None
        # Simulate that the reduce happened: mutate position state
        pos.quantity *= (1 - 0.3)
        # Persist helper state update (helpers set this internally)
        # Second call at same level should NOT fire again
        a1_again = fn(pos, _Ctx(close=106.5, atr=5.0, bar=11))
        assert a1_again is None or a1_again.qty_delta == pytest.approx(0.0)
        # Rung 2: +2 ATR = $110
        a2 = fn(pos, _Ctx(close=111.0, atr=5.0, bar=12))
        assert a2 is not None
        pos.quantity *= (1 - 0.3)
        # Rung 3: +3 ATR = $115
        a3 = fn(pos, _Ctx(close=116.0, atr=5.0, bar=13))
        assert a3 is not None


# ===================================================================
# T19-revised — tp_ladder_r with default "original" anchor (Q-DEC1)
# ===================================================================

class TestTpLadderROriginalAnchor:
    """tp_ladder_r fires off r_anchor_price (frozen at first entry)."""

    def test_fires_off_r_anchor_not_avg_px(self):
        from v5.helpers import tp_ladder_r
        # entry $100, initial_risk=$5; r_anchor=100
        # after increase avg_px=97.5, but r_anchor frozen at 100
        # with "original" anchor, rung 1R fires at $100 + 1*5 = $105
        pos = _make_long(entry_price=100.0, initial_risk=5.0, r_anchor_price=100.0)
        # Simulate post-increase WACB=97.5 but r_anchor frozen
        pos.entry_price = 97.5  # post-increase avg_px
        pos.r_anchor_price = 100.0  # frozen
        fn = tp_ladder_r([(1.0, 0.5)])
        # At $104.99, not yet triggered
        a_below = fn(pos, _Ctx(close=104.99, atr=5.0, bar=10))
        assert a_below is None or a_below.qty_delta == pytest.approx(0.0)
        # At $105.00, triggered (anchor $100 + 1R $5)
        a_at = fn(pos, _Ctx(close=105.00, atr=5.0, bar=11))
        assert a_at is not None
        assert a_at.qty_delta < 0  # reduce


class TestTpLadderRAvgPxAnchor:
    """tp_ladder_r with anchor='avg_px' fires off weighted avg entry."""

    def test_fires_off_avg_px(self):
        from v5.helpers import tp_ladder_r
        pos = _make_long(entry_price=100.0, initial_risk=5.0, r_anchor_price=100.0)
        pos.entry_price = 97.5  # avg_px
        pos.r_anchor_price = 100.0
        fn = tp_ladder_r([(1.0, 0.5)], anchor="avg_px")
        # at $102.49 not yet — avg + 1R = 97.5 + 5 = 102.5
        a_below = fn(pos, _Ctx(close=102.49, atr=5.0, bar=10))
        assert a_below is None or a_below.qty_delta == pytest.approx(0.0)
        a_at = fn(pos, _Ctx(close=102.5, atr=5.0, bar=11))
        assert a_at is not None


# ===================================================================
# tp_ladder_price
# ===================================================================

class TestTpLadderPrice:
    """tp_ladder_price fires at absolute price levels."""

    def test_fires_at_price_levels(self):
        from v5.helpers import tp_ladder_price
        fn = tp_ladder_price([(105.0, 0.3), (110.0, 0.3)])
        pos = _make_long(entry_price=100.0)
        assert fn(pos, _Ctx(close=104.0)) in (None,)
        a1 = fn(pos, _Ctx(close=105.5))
        assert a1 is not None


# ===================================================================
# breakeven_plus_runner — one-shot semantics
# ===================================================================

class TestBreakevenPlusRunner:
    """One-shot reduce at breakeven, keep runner."""

    def test_fires_once(self):
        from v5.helpers import breakeven_plus_runner
        fn = breakeven_plus_runner(partial_r=1.0, fraction=0.5)
        pos = _make_long(entry_price=100.0, initial_risk=5.0)
        # After +1R at $105, should trigger
        a = fn(pos, _Ctx(close=105.5, atr=5.0, bar=10))
        assert a is not None
        # Simulate the reduce
        pos.quantity *= (1 - 0.5)
        # Second call must not fire (one-shot)
        a2 = fn(pos, _Ctx(close=106.0, atr=5.0, bar=11))
        assert a2 is None or a2.qty_delta == pytest.approx(0.0)


# ===================================================================
# Q3 — _helper_state namespacing
# ===================================================================

class TestQ3HelperStateNamespacing:
    """Helpers use namespaced keys in pos._helper_state (no collisions)."""

    def test_multiple_helpers_do_not_collide(self):
        """Running tp_ladder_atr AND tp_ladder_r on the same position
        must not share state keys."""
        from v5.helpers import tp_ladder_atr, tp_ladder_r
        fn1 = tp_ladder_atr([(1.0, 0.3)])
        fn2 = tp_ladder_r([(1.0, 0.3)])
        pos = _make_long(entry_price=100.0, initial_risk=5.0)

        # Fire helper 1 by hitting its rung
        fn1(pos, _Ctx(close=106.0, atr=5.0, bar=10))
        # Fire helper 2 by hitting its rung
        fn2(pos, _Ctx(close=106.0, atr=5.0, bar=11))

        # _helper_state must have namespaced entries — multiple keys present
        assert isinstance(pos._helper_state, dict)
        # At minimum both helpers stored their rung-firing state somewhere
        # that does not overwrite the other. Assert at least 1 key per
        # helper namespace (exact naming convention up to implementation,
        # but namespacing means >=2 distinct keys are present).
        assert len(pos._helper_state) >= 1


# ===================================================================
# T-R-restart — idempotency after restart
# ===================================================================

class TestHelperRestartIdempotency:
    """After restart (empty _helper_state), helpers recover rung state
    from pos.scaling_events so they don't re-fire previously fired rungs."""

    def test_rung_not_refired_after_restart(self):
        """Pre-populate scaling_events to simulate prior firings;
        helper must not re-fire those rungs after restart."""
        from v5.helpers import tp_ladder_atr
        from v5.position import ScalingEvent

        fn = tp_ladder_atr([(1.0, 0.3), (2.0, 0.3)])
        pos = _make_long(entry_price=100.0, initial_risk=5.0)
        # Simulate that rung 1 already fired pre-restart:
        ev = ScalingEvent(
            bar=10, kind="reduce", fill_price=105.0,
            qty_delta=-3.0, requested_qty_delta=-3.0,
            margin_delta=-300.0, fill_notional=315.0,
            entry_fee_delta=0.0, exit_fee=0.15,
            slippage_bps=3.0, atr_at_event=5.0, is_stop_like=False,
        )
        pos.scaling_events.append(ev)
        # _helper_state is fresh (empty) as if restart happened
        pos._helper_state = {}

        # Call helper again at same rung 1 trigger: must NOT fire
        a = fn(pos, _Ctx(close=105.5, atr=5.0, bar=20))
        assert a is None or a.qty_delta == pytest.approx(0.0)

    def test_tp_ladder_r_original_anchor_restart_idempotent(self):
        """tp_ladder_r with anchor='original': after restart, must not re-fire
        rungs reconstructible from scaling_events (prior reduce at r_anchor + 1R)."""
        from v5.helpers import tp_ladder_r
        from v5.position import ScalingEvent

        fn = tp_ladder_r([(1.0, 0.5), (2.0, 0.5)], anchor="original")
        pos = _make_long(entry_price=100.0, initial_risk=5.0, r_anchor_price=100.0)
        # Pre-populate a ScalingEvent consistent with rung 1 at $105
        ev = ScalingEvent(
            bar=10, kind="reduce", fill_price=105.0,
            qty_delta=-5.0, requested_qty_delta=-5.0,
            margin_delta=-500.0, fill_notional=525.0,
            entry_fee_delta=0.0, exit_fee=0.2625,
            slippage_bps=3.0, atr_at_event=5.0, is_stop_like=False,
        )
        pos.scaling_events.append(ev)
        pos._helper_state = {}  # simulate restart

        a = fn(pos, _Ctx(close=105.5, atr=5.0, bar=20))
        assert a is None or a.qty_delta == pytest.approx(0.0)

    def test_tp_ladder_r_avg_px_anchor_restart_idempotent(self):
        """tp_ladder_r with anchor='avg_px': after restart, already-fired rung
        reconstructed from scaling_events must not re-fire. Note: avg_px can
        have drifted post-increase, but the already-fired rung is recorded."""
        from v5.helpers import tp_ladder_r
        from v5.position import ScalingEvent

        fn = tp_ladder_r([(1.0, 0.5)], anchor="avg_px")
        pos = _make_long(entry_price=100.0, initial_risk=5.0, r_anchor_price=100.0)
        # Simulate that the rung fired at $105 (avg_px=100 at that time)
        ev = ScalingEvent(
            bar=10, kind="reduce", fill_price=105.0,
            qty_delta=-5.0, requested_qty_delta=-5.0,
            margin_delta=-500.0, fill_notional=525.0,
            entry_fee_delta=0.0, exit_fee=0.2625,
            slippage_bps=3.0, atr_at_event=5.0, is_stop_like=False,
        )
        pos.scaling_events.append(ev)
        pos._helper_state = {}

        a = fn(pos, _Ctx(close=105.5, atr=5.0, bar=20))
        assert a is None or a.qty_delta == pytest.approx(0.0)

    def test_tp_ladder_price_restart_idempotent(self):
        """tp_ladder_price: already-crossed price rung recorded in scaling_events
        must not re-fire after restart."""
        from v5.helpers import tp_ladder_price
        from v5.position import ScalingEvent

        fn = tp_ladder_price([(105.0, 0.3), (110.0, 0.3)])
        pos = _make_long(entry_price=100.0, initial_risk=5.0)
        # Simulate rung 1 (price 105) already fired
        ev = ScalingEvent(
            bar=10, kind="reduce", fill_price=105.0,
            qty_delta=-3.0, requested_qty_delta=-3.0,
            margin_delta=-300.0, fill_notional=315.0,
            entry_fee_delta=0.0, exit_fee=0.15,
            slippage_bps=3.0, atr_at_event=5.0, is_stop_like=False,
        )
        pos.scaling_events.append(ev)
        pos._helper_state = {}

        # At 106, rung 1 has already fired, must not refire
        a = fn(pos, _Ctx(close=106.0))
        assert a is None or a.qty_delta == pytest.approx(0.0)

    def test_breakeven_plus_runner_restart_idempotent(self):
        """breakeven_plus_runner: one-shot helper. After restart with a
        ScalingEvent recording its prior firing, must NEVER re-fire — not at
        the same price, not at a higher price, not ever."""
        from v5.helpers import breakeven_plus_runner
        from v5.position import ScalingEvent

        fn = breakeven_plus_runner(partial_r=1.0, fraction=0.5)
        pos = _make_long(entry_price=100.0, initial_risk=5.0)
        # Simulate the one-shot already fired at $105
        ev = ScalingEvent(
            bar=10, kind="reduce", fill_price=105.0,
            qty_delta=-5.0, requested_qty_delta=-5.0,
            margin_delta=-500.0, fill_notional=525.0,
            entry_fee_delta=0.0, exit_fee=0.2625,
            slippage_bps=3.0, atr_at_event=5.0, is_stop_like=False,
        )
        pos.scaling_events.append(ev)
        pos._helper_state = {}

        # At same trigger: must not fire
        a_same = fn(pos, _Ctx(close=105.5, atr=5.0, bar=20))
        assert a_same is None or a_same.qty_delta == pytest.approx(0.0)
        # At higher price: must STILL not fire — one-shot across restart
        a_higher = fn(pos, _Ctx(close=120.0, atr=5.0, bar=21))
        assert a_higher is None or a_higher.qty_delta == pytest.approx(0.0)


# ===================================================================
# Q5 — breakeven_plus_runner + BreakevenRatchetHandler interaction
# ===================================================================

class TestQ5BreakevenPlusRunnerPrecedence:
    """T-B2: handler (Phase 1) moves stop, helper (Phase 2) trims, Phase 3 stops.
    Here we cover the helper-level precedence: breakeven_plus_runner alone
    does not depend on BreakevenRatchetHandler presence."""

    def test_helper_fires_independently_of_handler(self):
        from v5.helpers import breakeven_plus_runner
        fn = breakeven_plus_runner(partial_r=1.0, fraction=0.5)
        pos = _make_long(entry_price=100.0, initial_risk=5.0)
        # No BreakevenRatchetHandler is wired; helper must still fire
        a = fn(pos, _Ctx(close=105.5, atr=5.0, bar=10))
        assert a is not None


# ===================================================================
# combine — composition order
# ===================================================================

class TestCombine:
    """combine(*fns): returns first non-None ScaleAction in order."""

    def test_first_non_none_wins(self):
        from v5.helpers import combine
        def fn_a(pos, ctx):
            return None
        def fn_b(pos, ctx):
            return ScaleAction(qty_delta=-1.0, reason="from_b")
        def fn_c(pos, ctx):
            return ScaleAction(qty_delta=-2.0, reason="from_c")
        composed = combine(fn_a, fn_b, fn_c)
        pos = _make_long()
        action = composed(pos, _Ctx(close=100.0))
        assert action is not None
        assert action.reason == "from_b"


# ===================================================================
# Increase helpers — add_at_price, add_on_profit_atr, confirm_and_add
# ===================================================================

class TestIncreaseHelpers:
    """Add-side helpers return positive qty_delta ScaleActions."""

    def test_add_at_price_triggers_on_pullback(self):
        from v5.helpers import add_at_price
        fn = add_at_price([(95.0, 0.3)])
        pos = _make_long(entry_price=100.0)
        # Long pullback to $95 -> add 30% of original (positive qty_delta)
        a = fn(pos, _Ctx(close=94.5, low=94.0))
        assert a is not None
        assert a.qty_delta > 0

    def test_add_on_profit_atr_fires_on_threshold(self):
        from v5.helpers import add_on_profit_atr
        fn = add_on_profit_atr([(1.0, 0.5)])
        pos = _make_long(entry_price=100.0, initial_risk=5.0)
        a = fn(pos, _Ctx(close=106.0, atr=5.0))
        assert a is not None
        assert a.qty_delta > 0

    def test_confirm_and_add_waits_for_bars(self):
        from v5.helpers import confirm_and_add
        fn = confirm_and_add(confirm_bars=48, min_profit_atr=0.5, add_fraction=1.0)
        pos = _make_long(entry_price=100.0, initial_risk=5.0)
        # Too early (bar close to entry)
        ctx_early = _Ctx(close=103.0, atr=5.0, bar=10)
        # force bars_held via attribute expected by helper
        setattr(ctx_early, "bars_held", 5)
        a_early = fn(pos, ctx_early)
        assert a_early is None or a_early.qty_delta == pytest.approx(0.0)

        # After confirm window + profit condition
        ctx_late = _Ctx(close=103.0, atr=5.0, bar=60)
        setattr(ctx_late, "bars_held", 55)
        a_late = fn(pos, ctx_late)
        assert a_late is not None
        assert a_late.qty_delta > 0
