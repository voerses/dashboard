"""M11 AC-8 — Explicit LCD cadence semantics on DataEngine.

All tests RED today — `register_strategy_cadences`, `advance_one_tick`,
`strategies_due_at` do not exist on DataEngine. Pins:

  - `DataEngine.register_strategy_cadences(strategies)` computes per-strategy
    cadence from `required_data()` bar specs; also GCD across all
  - `DataEngine.advance_one_tick()` advances clock by GCD ns
  - `DataEngine.strategies_due_at(bar_idx)` returns strategies whose
    cadence aligns at the current clock tick
  - Mixed 1h + 5m portfolio drives strategies at their own cadences
"""
from __future__ import annotations

import math

import pytest

from v5.bar_spec import BarSpec
from v5.data.streams import InstrumentId, Venue


def _inst(sym: str = "BTCUSDT") -> InstrumentId:
    return InstrumentId(symbol=sym, venue=Venue.BINANCE, asset_class="perp")


class _CadenceStrategy:
    """Minimal strategy with required_data() declaring a single bar cadence."""
    def __init__(self, sid: str, minutes: int, instrument=None):
        self.id = sid
        self.strategy_id = sid
        self._minutes = minutes
        self._instrument = instrument or _inst()

    def required_data(self):
        from v5.data.streams import BarData, DataStream, Subscription

        stream = DataStream(
            instrument=self._instrument,
            data_class=BarData,
            bar_spec=BarSpec.from_minutes(self._minutes),
        )
        return [Subscription(stream=stream, handler=lambda _: None)]

    def generate(self, ctx, bar_idx):
        from v5.strategy_api import UniverseSignals
        return UniverseSignals(bar_idx=bar_idx, signals={})


class _TickClock:
    def __init__(self, start_ns: int = 0):
        self._now = start_ns
        self.current_bar_idx = 0

    def now_ns(self):
        return self._now

    def advance(self, ns: int):
        self._now += int(ns)
        return self._now


# ----------------------------------------------------------------------
# register_strategy_cadences
# ----------------------------------------------------------------------


def test_register_computes_cadences():
    """Per-strategy cadence derived from bar_spec in required_data()."""
    from v5.data.engine import DataEngine

    eng = DataEngine(clock=_TickClock())
    s_1h = _CadenceStrategy("s1h", minutes=60)
    s_5m = _CadenceStrategy("s5m", minutes=5)
    eng.register_strategy_cadences([s_1h, s_5m])

    # Internal map should record cadence ns per strategy
    cadences = eng._strategy_cadences
    assert cadences["s1h"] == 60 * 60 * 1_000_000_000
    assert cadences["s5m"] == 5 * 60 * 1_000_000_000


def test_gcd_clock_advance():
    """GCD(1h, 5m) = 5m = 300s = 300 * 1e9 ns."""
    from v5.data.engine import DataEngine

    eng = DataEngine(clock=_TickClock())
    eng.register_strategy_cadences([
        _CadenceStrategy("s1h", minutes=60),
        _CadenceStrategy("s5m", minutes=5),
    ])
    gcd_ns = eng._clock_advance_ns
    expected = math.gcd(60 * 60 * 1_000_000_000, 5 * 60 * 1_000_000_000)
    assert gcd_ns == expected
    assert gcd_ns == 5 * 60 * 1_000_000_000


def test_advance_one_tick():
    """advance_one_tick() walks clock forward by GCD ns."""
    from v5.data.engine import DataEngine

    clk = _TickClock(start_ns=0)
    eng = DataEngine(clock=clk)
    eng.register_strategy_cadences([
        _CadenceStrategy("s1h", minutes=60),
        _CadenceStrategy("s5m", minutes=5),
    ])
    before = clk.now_ns()
    eng.advance_one_tick()
    after = clk.now_ns()
    assert after - before == 5 * 60 * 1_000_000_000


def test_strategies_due_at_cadence_boundary():
    """After 12 GCD-ticks (=1h) the 1h strategy is due; 5m strategy is
    due every tick."""
    from v5.data.engine import DataEngine

    clk = _TickClock(start_ns=0)
    eng = DataEngine(clock=clk)
    eng.register_strategy_cadences([
        _CadenceStrategy("s1h", minutes=60),
        _CadenceStrategy("s5m", minutes=5),
    ])
    # Advance 12 ticks → 12 * 5m = 60m = 1h
    for _ in range(12):
        eng.advance_one_tick()
    due = set(eng.strategies_due_at(clk.current_bar_idx))
    assert "s1h" in due
    assert "s5m" in due

    # Advance 1 more tick — 5m strategy still due, 1h should NOT be due yet
    eng.advance_one_tick()
    due = set(eng.strategies_due_at(clk.current_bar_idx))
    assert "s5m" in due
    assert "s1h" not in due


def test_mixed_cadence_portfolio_1h_plus_5m():
    """12 GCD-ticks → 5m strategy invoked 12x, 1h strategy invoked 1x."""
    from v5.data.engine import DataEngine

    clk = _TickClock(start_ns=0)
    eng = DataEngine(clock=clk)

    s_1h = _CadenceStrategy("s1h", minutes=60)
    s_5m = _CadenceStrategy("s5m", minutes=5)
    eng.register_strategy_cadences([s_1h, s_5m])

    calls = {"s1h": 0, "s5m": 0}
    # Advance 12 GCD-ticks = 1h
    for _ in range(12):
        eng.advance_one_tick()
        for sid in eng.strategies_due_at(clk.current_bar_idx):
            calls[sid] += 1

    assert calls["s5m"] == 12
    assert calls["s1h"] == 1
