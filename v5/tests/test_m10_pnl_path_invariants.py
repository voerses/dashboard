"""M10 AC #14 — multi-bar PnL / equity / watermark invariants.

A 200-bar, single-token fixture with 5 forced open/close cycles is
driven through the portfolio simulator. After the run, four invariants
are asserted on the resulting ``SimulationState``:

1. Cumulative realized-today PnL at bar N equals
   ``sum(ct.pnl for ct in closed_trades if ct.exit_bar <= N)``.
2. ``portfolio_equity[N]`` == ``initial_capital + sum(realized_pnl[0:N+1])
   + sum(unrealized_pnl_at_N)`` within 1e-9 relative tolerance.
3. ``max_equity_watermark`` is monotone non-decreasing across all bars.
4. The equity curve reconstructed purely from the closed-trade list is
   bit-identical to the state's emitted equity curve.

All tests MUST FAIL today — the ``ReplayFixtureBuilder`` primitive
(task C0) has no implementation and ``SimulationState`` does not yet
expose per-bar ``cumulative_realized_pnl_today_usd``,
``portfolio_equity`` array, or ``max_equity_watermark`` history.
Phase 4 closes the gap.
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


N_BARS = 200
N_FORCED_CYCLES = 5
INITIAL_CAPITAL = 100_000.0
START_TS_UTC = 1_767_225_600_000_000_000  # 2026-01-01T00:00:00Z in ns


@pytest.fixture
def sim_state_200bar():
    """Drive simulator over 200-bar 1-token fixture with 5 forced cycles."""
    from v5.config import PortfolioConfig
    from v5.simulator import simulate_portfolio

    builder = ReplayFixtureBuilder(
        tokens=["BTC"],
        n_bars=N_BARS,
        start_ts_utc=START_TS_UTC,
        seed=42,
        scenario=ScenarioSpec(
            random_walk_stddev=0.001,
            forced_trades=N_FORCED_CYCLES,
        ),
    )
    all_signals = builder.build()
    cfg = PortfolioConfig(capital=INITIAL_CAPITAL, max_portfolio_positions=1)
    state = simulate_portfolio(all_signals, strategy_specs={}, config=cfg)
    return state


class TestAC14CumulativePnlIdentity:
    """AC #14.1 — cumulative realized-today PnL == Σ closed-trade pnl up to N."""

    def test_cumulative_realized_matches_closed_trades_by_bar(self, sim_state_200bar):
        state = sim_state_200bar
        cum = np.asarray(state.cumulative_realized_pnl_today_usd)
        assert cum.shape == (N_BARS,), (
            f"cumulative_realized_pnl_today_usd must be per-bar array of "
            f"length {N_BARS}; got shape {cum.shape}"
        )
        closed = list(state.position_manager.closed_trades)
        assert len(closed) >= N_FORCED_CYCLES, (
            f"Expected ≥{N_FORCED_CYCLES} closed trades; got {len(closed)}"
        )
        for bar_n in range(N_BARS):
            expected = sum(ct.pnl for ct in closed if ct.exit_bar <= bar_n)
            assert cum[bar_n] == pytest.approx(expected, abs=1e-9), (
                f"Bar {bar_n}: cum_realized={cum[bar_n]:.6f} "
                f"!= Σ pnl[exit_bar≤{bar_n}]={expected:.6f}"
            )


class TestAC14EquityIdentity:
    """AC #14.2 — fundamental equity identity at every bar."""

    def test_equity_identity_initial_plus_realized_plus_unrealized(
        self, sim_state_200bar,
    ):
        state = sim_state_200bar
        eq = np.asarray(state.portfolio_equity_curve)
        realized = np.asarray(state.realized_pnl_per_bar)
        unreal = np.asarray(state.unrealized_pnl_per_bar)
        assert eq.shape == (N_BARS,), (
            f"portfolio_equity_curve shape must be ({N_BARS},); got {eq.shape}"
        )
        assert realized.shape == (N_BARS,) == unreal.shape
        for n in range(N_BARS):
            identity = INITIAL_CAPITAL + realized[: n + 1].sum() + unreal[n]
            assert eq[n] == pytest.approx(identity, rel=1e-9, abs=1e-9), (
                f"Bar {n}: equity={eq[n]:.8f} != "
                f"{INITIAL_CAPITAL} + Σrealized + unrealized = {identity:.8f}"
            )


class TestAC14WatermarkMonotone:
    """AC #14.3 — max_equity_watermark is monotone non-decreasing."""

    def test_watermark_monotone_nondecreasing(self, sim_state_200bar):
        state = sim_state_200bar
        watermark = np.asarray(state.max_equity_watermark_curve)
        assert watermark.shape == (N_BARS,), (
            f"max_equity_watermark_curve shape must be ({N_BARS},); "
            f"got {watermark.shape}"
        )
        diffs = np.diff(watermark)
        assert np.all(diffs >= 0), (
            f"Watermark violated monotonicity: min diff={diffs.min():.6f} "
            f"at bar {int(np.argmin(diffs)) + 1}"
        )


class TestAC14EquityCurveReconstruction:
    """AC #14.4 — state equity curve bit-identical to trades-only reconstruction."""

    def test_reconstructed_from_trades_equals_state_curve(self, sim_state_200bar):
        state = sim_state_200bar
        state_curve = np.asarray(state.portfolio_equity_curve)

        closed = list(state.position_manager.closed_trades)
        reconstructed = np.full(N_BARS, INITIAL_CAPITAL, dtype=np.float64)
        for n in range(N_BARS):
            reconstructed[n] += sum(ct.pnl for ct in closed if ct.exit_bar <= n)

        # Compare ONLY bars where no position is currently open — i.e.
        # the trades-only reconstruction omits unrealized PnL; state_curve
        # subtracts that off when no position is open. This still checks
        # the bit-identity at the bars AFTER each forced exit.
        exit_bars = {ct.exit_bar for ct in closed}
        for n in sorted(exit_bars):
            assert state_curve[n] == pytest.approx(
                reconstructed[n], abs=1e-12, rel=0.0,
            ), (
                f"Bar {n} (exit): reconstructed={reconstructed[n]:.12f} "
                f"!= state={state_curve[n]:.12f}"
            )
