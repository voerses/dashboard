"""M10 A3 — AC-S10 positive-assertion guards (run BEFORE tolerance checks).

Closes the AC #10 "positive-assertion guards (mandatory, must run
BEFORE the parity comparisons)" clause from brief.md. These guards
reject silent-zero scaffolding and ensure the bridge inner-loop
actually mutated SimulationState — a pre-condition to *any* parity
number being meaningful.

Guards (exact spec from brief AC #10):
  1. ``len(state.closed_trades) > 0`` — at least one trade materialized
  2. ``len(state.equity_curve) == state.n_bars`` — equity curve populated
     across the full fold
  3. ``state.cum_pnl_usd[-1] != 0.0`` — cumulative PnL is non-zero
     (rejects silent-zero scaffolding)
  4. ``not hasattr(state, "_bridge_signals")`` — the private attribute
     attached by the M9 C-7 structural shim has been deleted (AC-S10
     wiring replaces it with real metric computation)
  5. ``state.trading_state.value in {"ACTIVE", "REDUCING"}`` — not
     HALTED due to wiring bug

Scope: a SINGLE 3-month fold (fast feedback). The full 5 per-year
per-metric comparison lives in
``test_m10_ac_s10_per_year_parity.py`` — this file is the gate that
ensures those tolerance checks run on real numbers, not scaffolding.

All tests MUST FAIL today:
  - AC-S10 bridge inner-loop not wired → no closed_trades materialize
  - ``SimulationState`` today exposes ``equity_snapshots`` and
    ``realized_pnl`` — ``equity_curve``, ``n_bars``, ``cum_pnl_usd``,
    and ``trading_state`` public attrs land in Phase 4 as part of
    AC-S10 wiring.
  - ``state._bridge_signals`` IS currently attached by the M9 C-7
    structural shim (see v5/simulator.py:2649); its absence is the
    exact signal that Phase-4 wiring completed.

Reference: brief AC #10 + tasks.md A3 ("Runs BEFORE tolerance check").
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


# Single-fold fast-feedback fixture window: 3 months, Q-DEC4 2025
# (2025-10-01 to 2025-12-31). Picked to mirror the existing M7 baseline
# window so Phase-4 can reuse the same universe-load machinery.
FOLD_START = "2025-10-01"
FOLD_END = "2025-12-31"


@pytest.fixture(scope="module")
def v5_s524m_fold_state():
    """Run s524m v5 through simulate_portfolio over a single 3-month fold.

    Returns the ``SimulationState`` instance. Phase-4 AC-S10 bridge
    wiring must populate: closed_trades, equity_curve, n_bars,
    cum_pnl_usd, trading_state (and must NOT leave _bridge_signals
    attached — that's the M9 C-7 structural shim signal).
    """
    from v5.config import PortfolioConfig
    from v5.simulator import simulate_portfolio
    from v5.strategies.s524m_v5 import S524M
    from v5.validation import load_oos_window

    data_bundle = load_oos_window(
        tokens=None,
        start_date=FOLD_START,
        end_date=FOLD_END,
    )
    config = PortfolioConfig(
        strategies=[],
        capital=100_000.0,
        max_portfolio_positions=50,
        adv_cap_pct=0.005,
        seed=42,
    )
    state = simulate_portfolio(
        strategies={"s524m": S524M()},
        ctx=data_bundle,
        config=config,
    )
    return state


@pytest.mark.ac_s10
class TestACS10PositiveAssertionGuards:
    """AC #10 — mandatory positive guards run BEFORE any tolerance check."""

    def test_closed_trades_non_empty(self, v5_s524m_fold_state):
        """At least one trade must materialize in a 3-month fold.

        Without this guard, the parity comparison could silently pass
        on a zero-trade run (0 == 0 within any tolerance).
        """
        state = v5_s524m_fold_state
        closed_trades = getattr(state, "closed_trades", None)
        assert closed_trades is not None, (
            "SimulationState.closed_trades attribute missing; AC-S10 "
            "wiring must expose it as a public list[ClosedTrade]."
        )
        assert len(closed_trades) > 0, (
            f"expected len(state.closed_trades) > 0; got "
            f"{len(closed_trades)}. A 3-month s524m fold with 50 max "
            "positions on the full perp universe must materialize trades."
        )

    def test_equity_curve_length_equals_n_bars(self, v5_s524m_fold_state):
        """The equity curve must be populated for every bar in the fold."""
        state = v5_s524m_fold_state
        equity_curve = getattr(state, "equity_curve", None)
        n_bars = getattr(state, "n_bars", None)
        assert equity_curve is not None, (
            "SimulationState.equity_curve attribute missing; AC-S10 "
            "wiring must expose it as a public np.ndarray or list."
        )
        assert n_bars is not None, (
            "SimulationState.n_bars attribute missing; AC-S10 wiring "
            "must expose it as a public int."
        )
        assert len(equity_curve) == n_bars, (
            f"len(state.equity_curve)={len(equity_curve)} != "
            f"state.n_bars={n_bars}; every bar in the fold must have a "
            "corresponding equity snapshot."
        )

    def test_cum_pnl_usd_terminal_nonzero(self, v5_s524m_fold_state):
        """Terminal cumulative PnL must be non-zero (rejects silent-zero
        scaffolding where the bridge returns an empty-metrics state)."""
        state = v5_s524m_fold_state
        cum_pnl_usd = getattr(state, "cum_pnl_usd", None)
        assert cum_pnl_usd is not None, (
            "SimulationState.cum_pnl_usd attribute missing; AC-S10 "
            "wiring must expose it as a public np.ndarray of length "
            "n_bars."
        )
        assert len(cum_pnl_usd) > 0, (
            f"state.cum_pnl_usd is empty; expected non-zero length. "
            "AC-S10 wiring populates this from the vectorized inner loop."
        )
        terminal = float(cum_pnl_usd[-1])
        assert terminal != 0.0, (
            f"state.cum_pnl_usd[-1]={terminal!r}; expected a non-zero "
            "terminal value. A zero terminal signals silent-zero "
            "scaffolding (the bridge returned a structural state with "
            "no metric computation)."
        )

    def test_bridge_signals_attr_deleted(self, v5_s524m_fold_state):
        """``_bridge_signals`` is the M9 C-7 structural shim
        (v5/simulator.py:2649 attaches it today). AC-S10 wiring replaces
        that shim with real metric computation and MUST delete the attr.
        """
        state = v5_s524m_fold_state
        assert not hasattr(state, "_bridge_signals"), (
            "state._bridge_signals is still attached — the M9 C-7 "
            "structural shim is still active. AC-S10 wiring must "
            "delete the attach line at v5/simulator.py:2649 and "
            "feed the precomputed arrays into the vectorized inner "
            "loop (_process_orders / _process_exits) instead."
        )

    def test_trading_state_not_halted_by_wiring_bug(self, v5_s524m_fold_state):
        """TradingState must be ACTIVE or REDUCING — never HALTED due to
        a wiring bug (e.g., a risk component erroneously halting at bar 0
        because peak_equity never got set by the bridge path)."""
        state = v5_s524m_fold_state
        trading_state = getattr(state, "trading_state", None)
        assert trading_state is not None, (
            "SimulationState.trading_state attribute missing; AC-S10 "
            "wiring must expose a TradingState object (from v5.risk)."
        )
        # v5.risk.TradingState.state is a Literal["ACTIVE", "REDUCING",
        # "HALTED"]. The brief AC #10 spec uses ``.value`` semantics,
        # which this test interprets as "the state-literal string":
        # prefer ``.state`` on the dataclass but fall back to ``.value``
        # if the implementation chose an Enum.
        state_value = (
            getattr(trading_state, "state", None)
            or getattr(trading_state, "value", None)
        )
        assert state_value is not None, (
            f"state.trading_state={trading_state!r} has neither .state "
            "nor .value attribute; expected one of them to carry the "
            "ACTIVE/REDUCING/HALTED literal."
        )
        assert state_value in {"ACTIVE", "REDUCING"}, (
            f"state.trading_state.value={state_value!r}; expected "
            "ACTIVE or REDUCING. HALTED at the end of a clean fold "
            "indicates a wiring bug (risk component halted at bar 0, "
            "or peak_equity=0 tripped DrawdownThrottle)."
        )
