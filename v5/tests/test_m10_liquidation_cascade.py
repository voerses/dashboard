"""M10 AC #17 — 3-position simultaneous liquidation cascade.

A 3-token, 48 x 1h fixture is flat until bar 24 where a uniform -25%
price shock is applied to every token. All 3 positions are opened at
bar 0 with 5x leverage, so each position crosses its liquidation
threshold on bar 24.

Invariants:

1. Every closed trade has ``exit_bar == 24`` and
   ``exit_reason == "liquidation"``.
2. Liquidation-exit ordering inside bar 24 is deterministic —
   ``(liq_distance asc, strategy_id + token lex tie-break)``. Three
   expected orderings are enumerated below.
3. ``state.portfolio_equity_curve[24]`` is >= 0 — a simultaneous
   cascade must never leave the portfolio with negative equity.
4. If the cumulative drawdown from the crash exceeds
   ``drawdown_halt_pct``, ``state.trading_state`` at end-of-bar-24 is
   ``HALTED``.

All tests MUST FAIL today — ``ReplayFixtureBuilder`` is a stub and
the liquidation-ordering contract in the simulator does not yet emit
a deterministic ``(liq_distance, strategy_id+token)`` key.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from v5.tests.fixtures._replay_builder import (  # noqa: E402
    ReplayFixtureBuilder,
    ScenarioSpec,
)


N_BARS = 48
CRASH_BAR = 24
CRASH_PCT = -0.25
INITIAL_CAPITAL = 300_000.0
TOKENS = ("BTC", "ETH", "SOL")
START_TS_UTC = 1_767_225_600_000_000_000  # 2026-01-01T00:00:00Z in ns


@pytest.fixture
def sim_cascade():
    from v5.config import PortfolioConfig, StrategySpec
    from v5.simulator import simulate_portfolio

    builder = ReplayFixtureBuilder(
        tokens=list(TOKENS),
        n_bars=N_BARS,
        start_ts_utc=START_TS_UTC,
        seed=11,
        scenario=ScenarioSpec(
            random_walk_stddev=0.0,     # flat until crash
            forced_trades=1,              # one open per token at bar 0
            crash_bar=CRASH_BAR,
            crash_pct=CRASH_PCT,
        ),
    )
    all_signals = builder.build()
    spec = StrategySpec(name="cascade_test", max_leverage=5.0)
    cfg = PortfolioConfig(
        capital=INITIAL_CAPITAL,
        max_portfolio_positions=3,
        strategies=[spec],
    )
    state = simulate_portfolio(all_signals, strategy_specs={"cascade_test": spec}, config=cfg)
    return state


class TestAC17SimultaneousLiquidation:
    """AC #17.1 — all 3 positions close at bar 24 with liquidation reason."""

    def test_all_three_closed_with_liquidation_reason(self, sim_cascade):
        state = sim_cascade
        closed = list(state.position_manager.closed_trades)
        assert len(closed) == 3, (
            f"Expected 3 liquidations; got {len(closed)}"
        )
        for ct in closed:
            assert ct.exit_bar == CRASH_BAR, (
                f"{ct.token}: exit_bar={ct.exit_bar} != {CRASH_BAR}"
            )
            assert ct.exit_reason == "liquidation", (
                f"{ct.token}: exit_reason={ct.exit_reason!r} != 'liquidation'"
            )


class TestAC17DeterministicOrdering:
    """AC #17.2 — exit order is (liq_distance asc, strategy_id+token lex)."""

    def test_exit_order_matches_deterministic_tiebreak(self, sim_cascade):
        state = sim_cascade
        closed = list(state.position_manager.closed_trades)
        # ClosedTrades appear in the PositionManager in engine-exit order.
        # The 3 positions in this fixture have equal liq_distance (uniform
        # -25% shock on uniform entry prices), so tie-break is strict
        # alphabetical on strategy_id+token key.
        exit_keys = [f"{ct.strategy_id}:{ct.token}" for ct in closed]
        expected = sorted(exit_keys)  # deterministic lex order
        assert exit_keys == expected, (
            f"Non-deterministic exit order: got {exit_keys}, "
            f"expected {expected}"
        )

    def test_exit_order_tokens_alphabetical(self, sim_cascade):
        state = sim_cascade
        closed = list(state.position_manager.closed_trades)
        tokens = [ct.token for ct in closed]
        assert tokens == ["BTC", "ETH", "SOL"], (
            f"Cascade token order diverged from alphabetical tie-break: "
            f"{tokens}"
        )

    def test_exit_order_stable_across_two_runs(self):
        """Running the cascade twice with identical seed → identical order."""
        from v5.config import PortfolioConfig, StrategySpec
        from v5.simulator import simulate_portfolio

        def _one_run():
            builder = ReplayFixtureBuilder(
                tokens=list(TOKENS),
                n_bars=N_BARS,
                start_ts_utc=START_TS_UTC,
                seed=11,
                scenario=ScenarioSpec(
                    random_walk_stddev=0.0,
                    forced_trades=1,
                    crash_bar=CRASH_BAR,
                    crash_pct=CRASH_PCT,
                ),
            )
            spec = StrategySpec(name="cascade_test", max_leverage=5.0)
            cfg = PortfolioConfig(
                capital=INITIAL_CAPITAL,
                max_portfolio_positions=3,
                strategies=[spec],
            )
            s = simulate_portfolio(
                builder.build(), strategy_specs={"cascade_test": spec}, config=cfg,
            )
            return [ct.token for ct in s.position_manager.closed_trades]

        assert _one_run() == _one_run()


class TestAC17EquityFloor:
    """AC #17.3 — equity never goes negative after cascade."""

    def test_equity_at_cascade_bar_nonnegative(self, sim_cascade):
        state = sim_cascade
        eq = np.asarray(state.portfolio_equity_curve)
        assert eq.shape == (N_BARS,)
        assert eq[CRASH_BAR] >= 0.0, (
            f"Equity at cascade bar {CRASH_BAR} is negative: {eq[CRASH_BAR]:.6f}"
        )
        assert np.all(eq >= 0.0), (
            f"Equity went negative at some bar; min={eq.min():.6f} "
            f"at bar {int(eq.argmin())}"
        )


class TestAC17TradingStateHalt:
    """AC #17.4 — drawdown halt fires when cascade breaches threshold."""

    def test_trading_state_halted_after_cascade_if_drawdown_exceeded(
        self, sim_cascade,
    ):
        state = sim_cascade
        eq = np.asarray(state.portfolio_equity_curve)
        peak = eq[:CRASH_BAR].max()
        drawdown = (eq[CRASH_BAR] - peak) / peak if peak > 0 else 0.0
        # Cascade at -25% with 5x leverage → ~100% equity wipe per-position
        # → well past any realistic halt threshold.
        if drawdown <= -0.50:
            end_state = state.trading_state_per_bar[CRASH_BAR]
            state_val = getattr(end_state, "state", end_state)
            assert state_val == "HALTED", (
                f"Drawdown {drawdown:.2%} exceeded halt threshold but "
                f"trading_state at bar {CRASH_BAR} is {state_val!r}"
            )
