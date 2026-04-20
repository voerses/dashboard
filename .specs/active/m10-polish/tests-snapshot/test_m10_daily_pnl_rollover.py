"""M10 AC #15 — realized-PnL day-boundary rollover + DailyLossLimit clear.

A 48 x 1h fixture straddles UTC midnight ``2026-01-02T00:00:00Z``. The
first 24 bars are day 1 (start ``2026-01-01T00:00:00Z``); bars 24-47 are
day 2. Two forced trades close in the range bar 12-18 (day 1) and two
more in bar 36-42 (day 2).

Three invariants are asserted on the resulting ``SimulationState``:

1. ``realized_pnl_today_usd`` per-bar series resets to ``0.0`` at the
   first day-2 bar (index 24), regardless of how negative it was at
   the end of bar 23.
2. If the DailyLossLimit risk component flipped ``trading_state`` to
   HALTED on day 1 (threshold breached), the HALT is CLEARED at the
   first day-2 bar and new entries can be admitted from bar 24 onward.
3. Lifetime ``cumulative_pnl_usd`` strictly monotonically accumulates
   across the boundary — the reset is local to the day counter only.

All tests MUST FAIL today — ``ReplayFixtureBuilder`` is a stub and
``SimulationState`` does not yet expose ``realized_pnl_today_usd``
or ``cumulative_pnl_usd`` as per-bar arrays.
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
BOUNDARY_BAR = 24  # first bar of day 2
INITIAL_CAPITAL = 100_000.0
START_TS_UTC = 1_767_225_600_000_000_000  # 2026-01-01T00:00:00Z in ns


@pytest.fixture
def sim_state_48h_rollover():
    from v5.config import PortfolioConfig
    from v5.simulator import simulate_portfolio

    builder = ReplayFixtureBuilder(
        tokens=["BTC"],
        n_bars=N_BARS,
        start_ts_utc=START_TS_UTC,
        seed=7,
        scenario=ScenarioSpec(
            random_walk_stddev=0.002,
            forced_trades=4,  # builder spaces evenly → ~2 day-1 + ~2 day-2
        ),
    )
    all_signals = builder.build()
    cfg = PortfolioConfig(
        capital=INITIAL_CAPITAL,
        max_portfolio_positions=1,
    )
    state = simulate_portfolio(all_signals, strategy_specs={}, config=cfg)
    return state


class TestAC15RealizedTodayResetsAtMidnight:
    """AC #15.1 — realized_pnl_today_usd resets to 0 at first day-2 bar."""

    def test_realized_today_resets_at_boundary_bar(self, sim_state_48h_rollover):
        state = sim_state_48h_rollover
        rpt = np.asarray(state.realized_pnl_today_usd_per_bar)
        assert rpt.shape == (N_BARS,), (
            f"realized_pnl_today_usd_per_bar must be per-bar array of length "
            f"{N_BARS}; got shape {rpt.shape}"
        )
        # Resets exactly at boundary bar — strictly zero on entry to day 2
        # (before any day-2 trade closes).
        assert rpt[BOUNDARY_BAR] == pytest.approx(0.0, abs=1e-12), (
            f"realized_pnl_today_usd[{BOUNDARY_BAR}] should be 0 at UTC "
            f"midnight rollover; got {rpt[BOUNDARY_BAR]:.6f}"
        )
        # Bar BEFORE boundary accumulated some trade PnL (could be + or -).
        assert rpt[BOUNDARY_BAR - 1] != 0.0, (
            f"Fixture must have closed trades on day 1 — "
            f"rpt[{BOUNDARY_BAR - 1}]={rpt[BOUNDARY_BAR - 1]:.6f}"
        )


class TestAC15DailyLossLimitClears:
    """AC #15.2 — DailyLossLimit HALT clears at UTC midnight."""

    def test_halt_state_clears_on_day2_first_bar(self, sim_state_48h_rollover):
        state = sim_state_48h_rollover
        # Trading-state history per bar — engine must expose this to
        # let risk-component decisions be auditable.
        ts_history = list(state.trading_state_per_bar)
        assert len(ts_history) == N_BARS, (
            f"trading_state_per_bar must have length {N_BARS}; "
            f"got {len(ts_history)}"
        )
        day1_last = ts_history[BOUNDARY_BAR - 1]
        day2_first = ts_history[BOUNDARY_BAR]
        # If day1 ended HALTED, day 2 must NOT still be HALTED.
        if getattr(day1_last, "state", day1_last) == "HALTED":
            day2_state = getattr(day2_first, "state", day2_first)
            assert day2_state == "ACTIVE", (
                f"HALT from day 1 did not clear at boundary: "
                f"day1_last={day1_last!r}, day2_first={day2_first!r}"
            )


class TestAC15LifetimeCumulativeKeepsGoing:
    """AC #15.3 — cumulative_pnl_usd continues across midnight."""

    def test_lifetime_cumulative_accumulates_across_boundary(
        self, sim_state_48h_rollover,
    ):
        state = sim_state_48h_rollover
        cum = np.asarray(state.cumulative_pnl_usd)
        assert cum.shape == (N_BARS,), (
            f"cumulative_pnl_usd must be per-bar array of length {N_BARS}; "
            f"got shape {cum.shape}"
        )
        # Lifetime counter must NEVER reset — it is monotone in the sense
        # that only a new closed trade can change it. Bar-to-bar identity:
        # cum[BOUNDARY_BAR] >= cum[BOUNDARY_BAR - 1] OR <= depending on sign,
        # but must match Σ closed_trades up through each bar.
        closed = list(state.position_manager.closed_trades)
        for bar_n in (BOUNDARY_BAR - 1, BOUNDARY_BAR, N_BARS - 1):
            expected = sum(ct.pnl for ct in closed if ct.exit_bar <= bar_n)
            assert cum[bar_n] == pytest.approx(expected, abs=1e-9), (
                f"Bar {bar_n}: cumulative_pnl_usd={cum[bar_n]:.6f} "
                f"!= Σ closed pnl up to bar {bar_n} = {expected:.6f}"
            )
        # And crucially — cumulative from bar 23 does NOT drop to 0 at bar 24.
        if cum[BOUNDARY_BAR - 1] != 0.0:
            assert cum[BOUNDARY_BAR] == pytest.approx(
                cum[BOUNDARY_BAR - 1], abs=1e-9,
            ) or cum[BOUNDARY_BAR] != 0.0, (
                f"Lifetime cumulative wrongly reset at boundary: "
                f"{cum[BOUNDARY_BAR - 1]:.6f} -> {cum[BOUNDARY_BAR]:.6f}"
            )
