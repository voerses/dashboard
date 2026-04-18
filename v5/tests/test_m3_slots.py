"""M3 acceptance tests — @dataclass(slots=True) on 5 dataclasses.

Covers:
  - AC8: Position, ClosedTrade, BarContext, TokenSignals, ScalingEvent all
    use `@dataclass(slots=True)` — instances have `__slots__`, no `__dict__`.
  - AC15c: slots + field(default_factory=...) compatibility —
    pickle.dumps/loads roundtrip, copy.deepcopy roundtrip, all fields preserved.
  - AC15b: M2 test suite still passes 145/145 under M3 changes (subprocess
    invocation of `pytest v5/tests/test_m2_*.py`).

All tests MUST FAIL today — slots not yet applied. Import-level checks of
`__slots__` existence will fail; pickle roundtrip of dataclasses without slots
still succeeds, so `not hasattr(inst, '__dict__')` is the load-bearing check.

Seed: 42.
"""
from __future__ import annotations

import copy
import pickle
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


# ===================================================================
# AC8 — slots applied to the 5 dataclasses
# ===================================================================

class TestAC8SlotsApplied:
    """Each dataclass defines __slots__ and instances reject __dict__."""

    def test_position_has_slots(self):
        from v5.position import Position
        assert hasattr(Position, "__slots__"), (
            "Position must use @dataclass(slots=True)"
        )

    def test_position_instance_has_no_dict(self):
        from v5.position import Position
        pos = Position(
            position_id="BTC:s30:5:primary",
            token="BTC", strategy_id="s30", leg="primary",
            entry_bar=0, entry_price=100.0, direction=1,
            quantity=1.0, margin_usd=100.0, leverage=1.0,
            is_perp=True, fee_rate=0.0005,
            stop_mult=2.0, trail_mult=3.0, target_mult=5.0,
            no_stop_bars=6, min_hold=6, max_hold=720,
            stop_price=90.0, highest=100.0, lowest=100.0,
            initial_risk=10.0,
        )
        assert not hasattr(pos, "__dict__"), (
            "Position instance must not have __dict__ (slots=True)"
        )

    def test_closed_trade_has_slots(self):
        from v5.position import ClosedTrade
        assert hasattr(ClosedTrade, "__slots__")

    def test_closed_trade_instance_has_no_dict(self):
        from v5.position import ClosedTrade
        ct = ClosedTrade(
            position_id="BTC:s30:5:primary", token="BTC", strategy_id="s30",
            leg="primary", entry_bar=0, exit_bar=10, entry_price=100.0,
            exit_price=110.0, direction=1, margin_usd=100.0, pnl=10.0,
            funding_cost=0.0, entry_fee=0.5, exit_fee=0.5, hold_bars=10,
            exit_reason="target",
        )
        assert not hasattr(ct, "__dict__")

    def test_scaling_event_has_slots(self):
        from v5.position import ScalingEvent
        assert hasattr(ScalingEvent, "__slots__")

    def test_scaling_event_instance_has_no_dict(self):
        from v5.position import ScalingEvent
        ev = ScalingEvent(
            bar=5, kind="reduce", fill_price=100.0, qty_delta=-1.0,
            requested_qty_delta=-1.0, margin_delta=-100.0,
            fill_notional=100.0, entry_fee_delta=0.0, exit_fee=0.05,
            slippage_bps=1.0, atr_at_event=5.0, is_stop_like=False,
        )
        assert not hasattr(ev, "__dict__")

    def test_bar_context_has_slots(self):
        from v5.exit_handlers import BarContext
        assert hasattr(BarContext, "__slots__")

    def test_bar_context_instance_has_no_dict(self):
        from v5.exit_handlers import BarContext
        bc = BarContext(
            close=100.0, high=101.0, low=99.0, atr=5.0,
            rsi=float("nan"), regime=0, bars_held=0,
            local_bar=0, funding_val=0.0,
        )
        assert not hasattr(bc, "__dict__")

    def test_token_signals_has_slots(self):
        from v5.signals import TokenSignals
        assert hasattr(TokenSignals, "__slots__")


# ===================================================================
# AC15c — slots + field(default_factory=...) composability
# ===================================================================

class TestAC15cSlotsDefaultFactoryCompat:
    """Python 3.10+ @dataclass(slots=True) with default_factory must:
       - instantiate with defaults
       - pickle.dumps/loads roundtrip
       - copy.deepcopy roundtrip
       - preserve all field values
    """

    def _make_position(self):
        from v5.position import Position
        return Position(
            position_id="BTC:s30:5:primary",
            token="BTC", strategy_id="s30", leg="primary",
            entry_bar=0, entry_price=100.0, direction=1,
            quantity=1.0, margin_usd=100.0, leverage=1.0,
            is_perp=True, fee_rate=0.0005,
            stop_mult=2.0, trail_mult=3.0, target_mult=5.0,
            no_stop_bars=6, min_hold=6, max_hold=720,
            stop_price=90.0, highest=100.0, lowest=100.0,
            initial_risk=10.0,
        )

    def _make_closed_trade(self):
        from v5.position import ClosedTrade
        return ClosedTrade(
            position_id="BTC:s30:5:primary", token="BTC", strategy_id="s30",
            leg="primary", entry_bar=0, exit_bar=10, entry_price=100.0,
            exit_price=110.0, direction=1, margin_usd=100.0, pnl=10.0,
            funding_cost=0.0, entry_fee=0.5, exit_fee=0.5, hold_bars=10,
            exit_reason="target",
        )

    def _make_scaling_event(self):
        from v5.position import ScalingEvent
        return ScalingEvent(
            bar=5, kind="reduce", fill_price=100.0, qty_delta=-1.0,
            requested_qty_delta=-1.0, margin_delta=-100.0,
            fill_notional=100.0, entry_fee_delta=0.0, exit_fee=0.05,
            slippage_bps=1.0, atr_at_event=5.0, is_stop_like=False,
        )

    def test_position_pickle_roundtrip(self):
        pos = self._make_position()
        # Exercise default_factory field (scaling_events defaults to list)
        pos.scaling_events.append("touch")
        data = pickle.dumps(pos)
        restored = pickle.loads(data)
        assert restored.position_id == "BTC:s30:5:primary"
        assert restored.scaling_events == ["touch"]
        assert not hasattr(restored, "__dict__")

    def test_position_deepcopy_roundtrip(self):
        pos = self._make_position()
        pos.scaling_events.append("x")
        dup = copy.deepcopy(pos)
        assert dup.scaling_events == ["x"]
        # Deepcopy must actually copy the list (not share reference)
        dup.scaling_events.append("y")
        assert pos.scaling_events == ["x"]
        assert not hasattr(dup, "__dict__")

    def test_closed_trade_pickle_roundtrip(self):
        ct = self._make_closed_trade()
        ct.scaling_events.append({"kind": "reduce"})
        data = pickle.dumps(ct)
        restored = pickle.loads(data)
        assert restored.scaling_events == [{"kind": "reduce"}]
        assert not hasattr(restored, "__dict__")

    def test_closed_trade_deepcopy_roundtrip(self):
        ct = self._make_closed_trade()
        ct.scaling_events.append({"kind": "reduce"})
        dup = copy.deepcopy(ct)
        assert dup.scaling_events == [{"kind": "reduce"}]
        dup.scaling_events.append({"kind": "increase"})
        assert ct.scaling_events == [{"kind": "reduce"}]

    def test_scaling_event_pickle_roundtrip(self):
        """ScalingEvent has no factory fields but slots must still pickle."""
        ev = self._make_scaling_event()
        data = pickle.dumps(ev)
        restored = pickle.loads(data)
        assert restored.kind == "reduce"
        assert restored.qty_delta == pytest.approx(-1.0)
        assert not hasattr(restored, "__dict__")

    def test_scaling_event_deepcopy_roundtrip(self):
        ev = self._make_scaling_event()
        dup = copy.deepcopy(ev)
        assert dup.kind == "reduce"
        assert not hasattr(dup, "__dict__")


# ===================================================================
# AC15b — M2 regression guard
# ===================================================================

class TestAC15bM2Regression:
    """After M3 changes (slots/float32/incremental), M2 tests still pass 145/145."""

    def test_m2_test_suite_passes(self):
        """Run `pytest v5/tests/test_m2_*.py` as a subprocess; must be green.

        RE-ENTRY GUARD: skips when `M3_SUBPROCESS_NESTED=1` is in the env.
        The outer invocation sets this env var on the subprocess child so the
        child's pytest observes the flag on its own test_m3_slots.py scan and
        skips these two subprocess tests — preventing fork-bomb recursion
        that otherwise eats ~5 GB of RSS (observed 2026-04-18).
        """
        import os
        if os.environ.get("M3_SUBPROCESS_NESTED") == "1":
            pytest.skip("re-entry guard: already inside a nested subprocess")

        m2_glob = sorted((_project_root / "v5" / "tests").glob("test_m2_*.py"))
        assert m2_glob, "Expected at least one v5/tests/test_m2_*.py file"
        args = [sys.executable, "-m", "pytest"]
        args.extend(str(p) for p in m2_glob)
        args.extend(["-q", "--tb=short", "--no-header"])

        env = {**os.environ, "M3_SUBPROCESS_NESTED": "1"}
        result = subprocess.run(
            args,
            capture_output=True, text=True,
            cwd=str(_project_root),
            timeout=600,
            env=env,
        )
        assert result.returncode == 0, (
            f"M2 suite regressed under M3 changes (exit {result.returncode}):\n"
            f"STDOUT:\n{result.stdout[-2000:]}\n"
            f"STDERR:\n{result.stderr[-1000:]}"
        )

    def test_full_v5_suite_passes(self):
        """Run `pytest v5/tests/` (full suite) as a subprocess; must be green.

        RE-ENTRY GUARD: same pattern as `test_m2_test_suite_passes`.
        Uses `--ignore-glob=**/test_m3_slots.py` (narrower than ignoring all M3
        tests) so the full suite still runs the other M3 test files, while
        this file's subprocess tests are excluded from the nested pytest run.
        """
        import os
        if os.environ.get("M3_SUBPROCESS_NESTED") == "1":
            pytest.skip("re-entry guard: already inside a nested subprocess")

        args = [
            sys.executable, "-m", "pytest",
            str(_project_root / "v5" / "tests"),
            "--ignore-glob=**/test_m3_slots.py",  # narrower: only skip the re-entry-risk file
            "-q", "--tb=short", "--no-header",
        ]
        env = {**os.environ, "M3_SUBPROCESS_NESTED": "1"}
        result = subprocess.run(
            args,
            capture_output=True, text=True,
            cwd=str(_project_root),
            timeout=900,
            env=env,
        )
        assert result.returncode == 0, (
            f"Full v5 suite regressed under M3 changes (exit {result.returncode}):\n"
            f"STDOUT:\n{result.stdout[-2000:]}\n"
            f"STDERR:\n{result.stderr[-1000:]}"
        )
