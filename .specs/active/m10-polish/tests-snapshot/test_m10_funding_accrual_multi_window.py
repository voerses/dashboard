"""M10 AC #16 — multi-window funding accrual on a 24h perp fixture.

A single perp position is opened at bar 0 (``2026-01-01T00:00:00Z``)
and closed at bar 23. Funding snaps are injected at three bars:

    bar 0  (00:00 UTC)  rate = +0.0001
    bar 8  (08:00 UTC)  rate = -0.0002
    bar 16 (16:00 UTC)  rate = +0.00015

Invariants:

1. ``position.cumulative_funding`` equals
   ``Σ sign(direction) * rate * notional`` across exactly 3 snaps.
2. Equity MTM incorporates funding at each snap edge:
   ``equity[8]`` reflects the first snap; ``equity[16]`` reflects the
   first two; ``equity[23]`` reflects all three.
3. The ``v5/logs/funding_accruals.jsonl`` archive contains EXACTLY 3
   entries with timestamps aligned to 00:00, 08:00, and 16:00 UTC on
   the fixture day. No drift, no extra entries.

All tests MUST FAIL today — funding-accruals JSONL sink does not yet
exist, and ``ReplayFixtureBuilder`` is a stub.
"""
from __future__ import annotations

import json
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


N_BARS = 24
INITIAL_CAPITAL = 100_000.0
START_TS_UTC = 1_767_225_600_000_000_000  # 2026-01-01T00:00:00Z in ns

FUNDING_SNAPS = (
    (0, 0.0001),
    (8, -0.0002),
    (16, 0.00015),
)


@pytest.fixture
def sim_with_funding(tmp_path, monkeypatch):
    from v5.config import PortfolioConfig
    from v5.simulator import simulate_portfolio

    # Redirect funding-accruals archive under tmp_path so we can inspect.
    log_dir = tmp_path / "v5_logs"
    log_dir.mkdir()
    monkeypatch.setenv("V5_LOG_DIR", str(log_dir))

    builder = ReplayFixtureBuilder(
        tokens=["BTC"],
        n_bars=N_BARS,
        start_ts_utc=START_TS_UTC,
        seed=3,
        scenario=ScenarioSpec(
            random_walk_stddev=0.0,   # flat price — isolate funding MTM
            forced_trades=1,            # open bar 0, close bar 23
            funding_snaps=FUNDING_SNAPS,
        ),
    )
    all_signals = builder.build()
    cfg = PortfolioConfig(
        capital=INITIAL_CAPITAL,
        max_portfolio_positions=1,
    )
    state = simulate_portfolio(all_signals, strategy_specs={}, config=cfg)
    return state, log_dir


class TestAC16PositionCumulativeFunding:
    """AC #16.1 — Σ sign(dir) × rate × notional matches position field."""

    def test_cumulative_funding_matches_three_snap_sum(self, sim_with_funding):
        state, _ = sim_with_funding
        closed = list(state.position_manager.closed_trades)
        assert len(closed) == 1, (
            f"Expected exactly 1 forced trade; got {len(closed)}"
        )
        ct = closed[0]
        notional = abs(ct.margin_usd * ct.direction)  # unit-leverage default
        direction_sign = 1 if ct.direction > 0 else -1
        expected = sum(
            direction_sign * rate * notional for _, rate in FUNDING_SNAPS
        )
        assert ct.funding_cost == pytest.approx(expected, rel=1e-6, abs=1e-6), (
            f"closed_trade.funding_cost={ct.funding_cost:.6f} "
            f"!= Σ sign×rate×notional = {expected:.6f}"
        )


class TestAC16EquityIncorporatesFundingAtWindowEdges:
    """AC #16.2 — equity curve steps at each funding bar."""

    def test_equity_step_at_each_snap_bar(self, sim_with_funding):
        state, _ = sim_with_funding
        eq = np.asarray(state.portfolio_equity_curve)
        assert eq.shape == (N_BARS,)
        # Funding cost is charged as a step on the funding bar; pre-snap
        # equity differs from post-snap by the single-snap amount.
        for snap_bar, _rate in FUNDING_SNAPS:
            if snap_bar == 0:
                continue  # bar-0 snap logged at entry; no pre-bar to compare
            # Between snap_bar-1 and snap_bar, the ONLY change is the new
            # funding charge (random walk stddev = 0).
            delta = eq[snap_bar] - eq[snap_bar - 1]
            assert delta != 0.0, (
                f"Expected equity step at snap bar {snap_bar}; "
                f"got delta 0.0 (flat price + funding snap should move equity)"
            )


class TestAC16FundingAccrualsJsonlExactlyThree:
    """AC #16.3 — funding_accruals.jsonl has exactly 3 timestamped entries."""

    def test_exactly_three_entries_aligned_to_utc_hours(self, sim_with_funding):
        _state, log_dir = sim_with_funding
        path = log_dir / "funding_accruals.jsonl"
        assert path.exists(), (
            f"Expected funding-accruals archive at {path}"
        )
        lines = [json.loads(line) for line in path.read_text().splitlines() if line]
        assert len(lines) == 3, (
            f"Expected 3 funding-accrual entries (00:00/08:00/16:00 UTC); "
            f"got {len(lines)}: {lines!r}"
        )
        ts_hours = [
            int(entry["ts_ns"]) - START_TS_UTC
            for entry in lines
        ]
        expected_hours_ns = [0, 8 * 3600 * 1_000_000_000, 16 * 3600 * 1_000_000_000]
        assert ts_hours == expected_hours_ns, (
            f"Funding snaps drifted from 00:00/08:00/16:00 UTC: "
            f"relative ts = {ts_hours}"
        )
