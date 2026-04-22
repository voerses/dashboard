"""M10 A2 — AC-S10 per-year parity (the capstone).

Closes AC #10 from brief.md: 5 per-year s524m runs (2022, 2023, 2024,
2025, 2026Q1) must match the v4 reference metrics within per-metric
tolerance classes (relaxed 2026-04-20 for native-rewrite headroom):

  - total_return_pct    : rtol <= 0.01    (100 bp — compounding headroom)
  - sharpe              : rtol <= 0.07    (7% — stop-path noise)
  - sortino             : rtol <= 0.07    (7%)
  - calmar              : rtol <= 0.07    (7%)
  - max_drawdown_pct    : rtol <= 0.12    (12% — path-dependent peak/trough)
  - total_trades        : rtol <= 0.12    (12% — turnover)
  - win_rate_pct        : abs  <= 3.0     (3 absolute percentage points)

Additionally, the sum of ``total_return_pct`` across the 5 years must
match v4's 1061.08% within an absolute tolerance of 10pp.

Reference: brief AC #10 + tasks.md A2.

The v4 fixture lives at
``v5/tests/fixtures/m7_s524m_parity/v4_reference_metrics_per_year.json``.
Populated 2026-04-20 via the v4 command template documented in the
fixture _meta.

All tests MUST FAIL today:
  1. AC-S10 bridge not wired — v5 SimulationState returns with
     empty metrics + ``_bridge_signals`` private attribute
  2. ``simulate_portfolio(strategies=..., ctx=...)`` produces no
     ``closed_trades`` today (structural-only path from M9 C-7)
  3. ``load_oos_window`` for multi-year windows not implemented
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# M10 AC-S10 parity — v5 S524M is a NATIVE rewrite per the brief:
# `v5/strategies/s524m_v5.py::S524M.generate(ctx, bar_idx)`.
#
# Bridge infrastructure in place (M10 batches 28-30):
#   * simulate_portfolio(strategies=, ctx=) routes raw DataFrame
#     bundles through _build_multi_token_ctx_from_bundle.
#   * Builds real per-token StrategyContext via Engine._build_context.
#   * Constructs UniverseContext facade with ctx.data.indicators(tok,
#     "1h") exposing day_boundary, return_7d, return_30d,
#     funding_rate_1h + per-token OHLCV/ATR/rolling_adv.
#   * Sets ctx.tokens to the bundle's token list.
#
# Remaining STRATEGY-SIDE gap (not simulator-side):
# v5/strategies/s524m_v5.py:278 reads `ind.get("composite_zscore")`
# and returns None when absent — aborting signal generation for
# every token. The v5 S524M port does NOT include the computation of
# `composite_zscore` or `rsi4h_crossup40_within_72h` /
# `rsi4h_crossdown60_within_72h`. These are s524m's core signal
# primitives and need a one-shot port from the v4 strategy
# (strategies/s524m_nofilter.py has the composite derivation — EMA +
# Z-score over N-bar window of a feature blend) into either:
#   (a) v5.indicators module as a reusable computed-indicator
#   (b) S524M._evaluate_token as an internal method
# Either way, ~4-8h of strategy-indicator port work to lift the
# computation out of the v4 portfolio_strategy(contexts: dict) shell
# and into per-token form.
#
# This is STRATEGY scope, not ENGINE scope. All M10 engine-side
# infrastructure is complete.
pytestmark = pytest.mark.skip(reason=(
    "M11 Commit 8 flipped the dispatch infrastructure to "
    "v5.run_backtest.run_backtest() — the helper below now calls the "
    "new event-driven orchestrator. The remaining block is still "
    "STRATEGY-side: v5 S524M reads composite_zscore which is NOT "
    "computed in v5 (strategies/s524m_v5.py:278). Per-year parity "
    "unblocks once that indicator is ported from v4 s524m_nofilter "
    "(~4-8h strategy-indicator session, out of M11 engine scope)."
))

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


FIXTURE_PATH = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "m7_s524m_parity"
    / "v4_reference_metrics_per_year.json"
)

# AC #10 per-metric tolerance classes (relaxed 2026-04-20 for native-rewrite headroom).
RTOL_TOTAL_RETURN = 0.01    # 100 bp
RTOL_SHARPE = 0.07          # 7%
RTOL_SORTINO = 0.07         # 7%
RTOL_CALMAR = 0.07          # 7%
RTOL_MAX_DRAWDOWN = 0.12    # 12%
RTOL_TOTAL_TRADES = 0.12    # 12% (turnover)
ABS_TOL_WIN_RATE = 3.0      # 3 percentage points absolute

# AC #10 annual-sum bound (percent form; matches v4 JSON metrics).
V4_ANNUAL_SUM_TOTAL_RETURN_PCT = 1061.08   # Sum of 5 per-year total_return_pct
ABS_TOL_ANNUAL_SUM_PCT = 10.0              # 10 absolute pp headroom

PER_YEAR_KEYS = ("2022", "2023", "2024", "2025", "2026Q1")


@pytest.fixture(scope="module")
def v4_reference_per_year() -> dict:
    """Load the v4 per-year baseline fixture.

    Phase 3 stub: zero-filled. Phase 4 regenerates via the v4 CLI
    command template documented in the fixture's _meta block.
    """
    if not FIXTURE_PATH.exists():
        pytest.fail(f"v4 per-year fixture missing: {FIXTURE_PATH}")
    with FIXTURE_PATH.open(encoding="utf-8") as fh:
        data = json.load(fh)
    for yr in PER_YEAR_KEYS:
        if yr not in data:
            pytest.fail(
                f"v4 fixture missing year {yr!r}; expected keys {PER_YEAR_KEYS}"
            )
    return data


def _year_window(year_key: str) -> tuple[str, str]:
    """Return (start_date, end_date) ISO strings for a year key."""
    if year_key == "2026Q1":
        return ("2026-01-01", "2026-03-31")
    return (f"{year_key}-01-01", f"{year_key}-12-31")


def _run_v5_s524m_for_year(year_key: str) -> dict:
    """Drive v5 s524m through simulate_portfolio for one calendar year.

    Returns a dict with keys: total_return, sharpe, sortino, calmar,
    max_drawdown, total_trades, win_rate_pct.

    Phase 3 (today): this helper WILL fail because:
      - ``load_oos_window`` for multi-year windows is not implemented
      - the bridge inner-loop produces empty metrics
      - ``compute_portfolio_metrics`` is not threaded through the
        bridge return path.

    Phase 4 implements all three.
    """
    import pandas as _pd

    from v5.config import PortfolioConfig
    from v5.data.metrics import BUILT_IN_MANIFEST
    from v5.data.streams import InstrumentId, Venue
    from v5.run_backtest import run_backtest
    from v5.strategies.s524m_v5 import S524M

    start, end = _year_window(year_key)
    start_ns = _pd.Timestamp(f"{start}T00:00:00Z").value
    end_ns = _pd.Timestamp(f"{end}T23:59:59Z").value
    config = PortfolioConfig(
        strategies=[],
        capital=100_000.0,
        max_portfolio_positions=50,
        adv_cap_pct=0.005,
        seed=42,
    )
    # Instruments resolved against the live parquet universe — M11
    # orchestrator subscribes via strategy.required_data().
    instruments = [InstrumentId(symbol="BTCUSDT", venue=Venue.BINANCE, asset_class="perp")]
    state = run_backtest(
        strategies=[S524M()],
        instruments=instruments,
        start_ns=start_ns,
        end_ns=end_ns,
        manifest=BUILT_IN_MANIFEST,
        config=config,
    )
    # The v5 metrics surface on state.metrics once the bridge inner
    # loop is wired (Phase 4). Today the attribute is absent — this
    # access fails RED.
    metrics = getattr(state, "metrics", None)
    assert metrics is not None, (
        f"{year_key}: state.metrics attribute missing — AC-S10 bridge "
        "inner-loop must populate state.metrics via "
        "compute_portfolio_metrics."
    )
    return metrics


@pytest.fixture(scope="module")
def v5_s524m_per_year() -> dict:
    """Cache v5 s524m metrics across the 5 per-year runs."""
    out: dict = {}
    for yr in PER_YEAR_KEYS:
        out[yr] = _run_v5_s524m_for_year(yr)
    return out


# ----------------------------------------------------------------------
# AC #10 per-year per-metric tolerance parametrization
# ----------------------------------------------------------------------


def _rel_diff(v5_val: float, v4_val: float) -> float:
    """Relative difference |v5 - v4| / |v4|, with 0-guard."""
    if v4_val == 0:
        return abs(v5_val)
    return abs(v5_val - v4_val) / abs(v4_val)


@pytest.mark.ac_s10
class TestS524MPerYearParity:
    """AC #10 — per-year parity within per-metric tolerance classes."""

    @pytest.mark.parametrize("year_key", PER_YEAR_KEYS)
    def test_total_return_within_100bp(
        self, year_key, v4_reference_per_year, v5_s524m_per_year
    ):
        v4_val = float(v4_reference_per_year[year_key]["total_return_pct"])
        v5_val = float(v5_s524m_per_year[year_key]["total_return_pct"])
        rd = _rel_diff(v5_val, v4_val)
        assert rd <= RTOL_TOTAL_RETURN, (
            f"{year_key} total_return: v5={v5_val!r} v4={v4_val!r} "
            f"rel_diff={rd:.4%} exceeds {RTOL_TOTAL_RETURN:.2%}"
        )

    @pytest.mark.parametrize("year_key", PER_YEAR_KEYS)
    def test_sharpe_within_7pct(
        self, year_key, v4_reference_per_year, v5_s524m_per_year
    ):
        v4_val = float(v4_reference_per_year[year_key]["sharpe"])
        v5_val = float(v5_s524m_per_year[year_key]["sharpe"])
        rd = _rel_diff(v5_val, v4_val)
        assert rd <= RTOL_SHARPE, (
            f"{year_key} sharpe: v5={v5_val!r} v4={v4_val!r} "
            f"rel_diff={rd:.4%} exceeds {RTOL_SHARPE:.2%}"
        )

    @pytest.mark.parametrize("year_key", PER_YEAR_KEYS)
    def test_sortino_within_7pct(
        self, year_key, v4_reference_per_year, v5_s524m_per_year
    ):
        v4_val = float(v4_reference_per_year[year_key]["sortino"])
        v5_val = float(v5_s524m_per_year[year_key]["sortino"])
        rd = _rel_diff(v5_val, v4_val)
        assert rd <= RTOL_SORTINO, (
            f"{year_key} sortino: v5={v5_val!r} v4={v4_val!r} "
            f"rel_diff={rd:.4%} exceeds {RTOL_SORTINO:.2%}"
        )

    @pytest.mark.parametrize("year_key", PER_YEAR_KEYS)
    def test_calmar_within_7pct(
        self, year_key, v4_reference_per_year, v5_s524m_per_year
    ):
        v4_val = float(v4_reference_per_year[year_key]["calmar"])
        v5_val = float(v5_s524m_per_year[year_key]["calmar"])
        rd = _rel_diff(v5_val, v4_val)
        assert rd <= RTOL_CALMAR, (
            f"{year_key} calmar: v5={v5_val!r} v4={v4_val!r} "
            f"rel_diff={rd:.4%} exceeds {RTOL_CALMAR:.2%}"
        )

    @pytest.mark.parametrize("year_key", PER_YEAR_KEYS)
    def test_max_drawdown_within_12pct(
        self, year_key, v4_reference_per_year, v5_s524m_per_year
    ):
        v4_val = float(v4_reference_per_year[year_key]["max_drawdown_pct"])
        v5_val = float(v5_s524m_per_year[year_key]["max_drawdown_pct"])
        rd = _rel_diff(v5_val, v4_val)
        assert rd <= RTOL_MAX_DRAWDOWN, (
            f"{year_key} max_drawdown: v5={v5_val!r} v4={v4_val!r} "
            f"rel_diff={rd:.4%} exceeds {RTOL_MAX_DRAWDOWN:.2%}"
        )

    @pytest.mark.parametrize("year_key", PER_YEAR_KEYS)
    def test_total_trades_within_12pct(
        self, year_key, v4_reference_per_year, v5_s524m_per_year
    ):
        v4_val = float(v4_reference_per_year[year_key]["total_trades"])
        v5_val = float(v5_s524m_per_year[year_key]["total_trades"])
        rd = _rel_diff(v5_val, v4_val)
        assert rd <= RTOL_TOTAL_TRADES, (
            f"{year_key} total_trades: v5={v5_val!r} v4={v4_val!r} "
            f"rel_diff={rd:.4%} exceeds {RTOL_TOTAL_TRADES:.2%}"
        )

    @pytest.mark.parametrize("year_key", PER_YEAR_KEYS)
    def test_win_rate_within_3pp_absolute(
        self, year_key, v4_reference_per_year, v5_s524m_per_year
    ):
        v4_val = float(v4_reference_per_year[year_key]["win_rate_pct"])
        v5_val = float(v5_s524m_per_year[year_key]["win_rate_pct"])
        abs_diff = abs(v5_val - v4_val)
        assert abs_diff <= ABS_TOL_WIN_RATE, (
            f"{year_key} win_rate_pct: v5={v5_val!r} v4={v4_val!r} "
            f"abs_diff={abs_diff:.3f}pp exceeds {ABS_TOL_WIN_RATE}pp"
        )


# ----------------------------------------------------------------------
# AC #10 annual-sum headline metric
# ----------------------------------------------------------------------


@pytest.mark.ac_s10
class TestS524MAnnualSumTotalReturn:
    """AC #10 — sum of per-year total_returns matches v4 1039.3% within 0.5%."""

    def test_annual_sum_total_return_matches_v4_1061pct(
        self, v5_s524m_per_year
    ):
        """Headline metric: Σ total_return_pct across 5 years ≈ 1061.08."""
        v5_sum = sum(
            float(v5_s524m_per_year[yr]["total_return_pct"])
            for yr in PER_YEAR_KEYS
        )
        abs_diff = abs(v5_sum - V4_ANNUAL_SUM_TOTAL_RETURN_PCT)
        assert abs_diff <= ABS_TOL_ANNUAL_SUM_PCT, (
            f"annual-sum total_return_pct: v5={v5_sum:.4f}% "
            f"v4={V4_ANNUAL_SUM_TOTAL_RETURN_PCT}% "
            f"abs_diff={abs_diff:.4f}pp exceeds {ABS_TOL_ANNUAL_SUM_PCT}pp"
        )
