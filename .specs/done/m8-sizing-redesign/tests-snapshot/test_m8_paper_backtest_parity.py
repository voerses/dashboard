"""M8 AC-Sz9 — v5 paper-vs-backtest sizing-fills parity.

The same strategy + same bars + same seed must produce bit-identical
sizing_fills.jsonl across backtest and paper paths.

Non-float columns (binding_constraint, intent, symbol, strategy_id) must
match exactly. Float columns within M3 tolerances from
v5/tests/shadow_replay_tolerances.py.

AGGREGATE_PNL_BPS_TOLERANCE (10 bps) is NOT applied here because AC-Sz9
is a per-fill parity gate. Aggregate-level drift is covered by M4 AC41
trade-archive parity.

Fixture must exercise all 6 clamps (ADV, concentration, free_capital,
min_size, liq_distance, slippage). The sampling_cadence drift test
documents that a TickCadencePolicy is paper-only opt-in and produces
expected drift from bar_close backtest output — not a bug.

All tests MUST FAIL today — paper parity fixture + runners not wired.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


NON_FLOAT_COLUMNS = ("binding_constraint", "intent", "symbol", "strategy_id")
FLOAT_COLUMNS = ("filled_margin", "filled_notional", "fill_price", "slippage_bps")
REQUIRED_BINDING_CONSTRAINTS = {
    "adv_cap", "concentration", "free_capital",
    "min_size", "liquidation_distance",
    # slippage is NOT a binding constraint — it's a price adjust — so we
    # assert slippage_bps > 0 somewhere instead.
}


@pytest.fixture
def parity_fixture_path(tmp_path):
    """Fixture that deliberately constructs scenarios hitting all 6 clamps."""
    from v5.sizing.parity_fixture import build_m8_parity_fixture
    fixture_dir = tmp_path / "m8_sizing_parity"
    build_m8_parity_fixture(fixture_dir, include_all_six_clamps=True)
    return fixture_dir


def _run_backtest(fixture, seed, log_path):
    """Run the fixture through v5.simulator.simulate_portfolio."""
    from v5.sizing.parity_fixture import run_backtest_parity
    run_backtest_parity(fixture=fixture, seed=seed, sizing_fills_log=log_path)


def _run_paper(fixture, seed, log_path, policy=None):
    """Run the fixture through v5.paper_engine.replay_paper_ticks."""
    from v5.sizing.parity_fixture import run_paper_parity
    run_paper_parity(
        fixture=fixture, seed=seed,
        use_test_clock=True,
        sizing_fills_log=log_path,
        policy=policy,
    )


class TestPaperBacktestParity:
    """AC-Sz9 — bit-identical (non-float) parity between simulator + paper."""

    def test_backtest_emits_fills_log(self, parity_fixture_path, tmp_path):
        bt_log = tmp_path / "backtest_sizing_fills.jsonl"
        _run_backtest(parity_fixture_path, seed=42, log_path=bt_log)
        assert bt_log.exists()
        assert len(bt_log.read_text().splitlines()) > 0

    def test_paper_emits_fills_log(self, parity_fixture_path, tmp_path):
        paper_log = tmp_path / "paper_sizing_fills.jsonl"
        _run_paper(parity_fixture_path, seed=42, log_path=paper_log)
        assert paper_log.exists()

    def test_non_float_columns_byte_identical(self, parity_fixture_path, tmp_path):
        bt_log = tmp_path / "bt.jsonl"
        paper_log = tmp_path / "paper.jsonl"
        _run_backtest(parity_fixture_path, seed=42, log_path=bt_log)
        _run_paper(parity_fixture_path, seed=42, log_path=paper_log)
        bt_entries = [json.loads(l) for l in bt_log.read_text().splitlines()]
        paper_entries = [json.loads(l) for l in paper_log.read_text().splitlines()]
        assert len(bt_entries) == len(paper_entries), (
            f"Entry count differs: backtest={len(bt_entries)}, paper={len(paper_entries)}"
        )
        for bt, pp in zip(bt_entries, paper_entries):
            for col in NON_FLOAT_COLUMNS:
                assert bt[col] == pp[col], (
                    f"non-float col {col!r} diverged: bt={bt[col]!r} vs paper={pp[col]!r}"
                )

    def test_float_columns_within_tolerance(self, parity_fixture_path, tmp_path):
        from v5.tests.shadow_replay_tolerances import (
            PER_TRADE_PNL_BPS_TOLERANCE,
            PRICE_ULP_TOLERANCE,
        )
        bt_log = tmp_path / "bt.jsonl"
        paper_log = tmp_path / "paper.jsonl"
        _run_backtest(parity_fixture_path, seed=42, log_path=bt_log)
        _run_paper(parity_fixture_path, seed=42, log_path=paper_log)
        bt_entries = [json.loads(l) for l in bt_log.read_text().splitlines()]
        paper_entries = [json.loads(l) for l in paper_log.read_text().splitlines()]
        assert len(bt_entries) == len(paper_entries)
        # PRICE_ULP_TOLERANCE is the float32-ULP-count tolerance (currently 4).
        # Convert to a relative-price tolerance using the max-float32-ULP
        # magnitude at the reference price (~2^-23 relative for float32).
        # The original test hardcoded 2^-20 which is looser than the M3
        # contract; the M3 constant is the single source of truth.
        _f32_eps = 2 ** (-23)
        price_rel_tolerance = PRICE_ULP_TOLERANCE * _f32_eps
        for bt, pp in zip(bt_entries, paper_entries):
            for col in FLOAT_COLUMNS:
                bt_v = bt[col] if bt[col] is not None else 0.0
                pp_v = pp[col] if pp[col] is not None else 0.0
                if col == "fill_price":
                    if bt_v == 0 and pp_v == 0:
                        continue
                    rel = abs(bt_v - pp_v) / max(abs(bt_v), abs(pp_v), 1e-9)
                    assert rel < price_rel_tolerance, (
                        f"fill_price diverged beyond PRICE_ULP_TOLERANCE="
                        f"{PRICE_ULP_TOLERANCE} ULP (rel<{price_rel_tolerance:.2e}): "
                        f"bt={bt_v} paper={pp_v}"
                    )
                else:
                    bps = abs(bt_v - pp_v) / max(abs(bt_v), 1.0) * 1e4
                    assert bps <= PER_TRADE_PNL_BPS_TOLERANCE, (
                        f"{col} diverged {bps:.2f} bps "
                        f"(max allowed {PER_TRADE_PNL_BPS_TOLERANCE})"
                    )


class TestFixtureExercisesAllSixClamps:
    """AC-Sz9 — parity fixture must deliberately hit ALL 6 clamps."""

    def test_fixture_binding_constraint_histogram_includes_five_reducers(
        self, parity_fixture_path, tmp_path,
    ):
        """All 5 reducer clamps must appear at least once across the fixture.

        Slippage is excluded — it's a price-adjust, NOT a binding_constraint.
        """
        bt_log = tmp_path / "bt.jsonl"
        _run_backtest(parity_fixture_path, seed=42, log_path=bt_log)
        entries = [json.loads(l) for l in bt_log.read_text().splitlines()]
        seen = {e.get("binding_constraint") for e in entries}
        missing = REQUIRED_BINDING_CONSTRAINTS - seen
        assert not missing, (
            f"Parity fixture must hit all 5 reducer clamps; missing: {missing}. "
            f"Seen: {seen}"
        )

    def test_fixture_has_at_least_one_entry_with_positive_slippage_bps(
        self, parity_fixture_path, tmp_path,
    ):
        """Slippage clamp is NEVER binding but MUST produce non-zero slippage_bps."""
        bt_log = tmp_path / "bt.jsonl"
        _run_backtest(parity_fixture_path, seed=42, log_path=bt_log)
        entries = [json.loads(l) for l in bt_log.read_text().splitlines()]
        positive_slip = [e for e in entries if (e.get("slippage_bps") or 0) > 0]
        assert positive_slip, (
            "Parity fixture must produce at least one entry with slippage_bps > 0 "
            "so the slippage clamp path is exercised."
        )


class TestSamplingCadenceDrift:
    """AC-Sz9 — sampling_cadence='tick' is paper-only opt-in; expected drift.

    Design §8 makes sampling_cadence load-bearing for parity. Under paper
    (tick evaluation), a TickCadencePolicy produces DIFFERENT fills than
    the same fixture under backtest (bar_close evaluation). This is
    EXPECTED drift, not a bug — the test codifies it as the documented
    opt-in behavior.
    """

    def test_tick_cadence_policy_drifts_from_bar_close(
        self, parity_fixture_path, tmp_path,
    ):
        from v5.sizing.allocation import SharedPoolPolicy

        class TickCadencePolicy(SharedPoolPolicy):
            sampling_cadence = "tick"

        bt_log = tmp_path / "bt.jsonl"
        paper_tick_log = tmp_path / "paper_tick.jsonl"
        _run_backtest(parity_fixture_path, seed=42, log_path=bt_log)
        _run_paper(
            parity_fixture_path, seed=42, log_path=paper_tick_log,
            policy=TickCadencePolicy(),
        )
        bt_entries = [json.loads(l) for l in bt_log.read_text().splitlines()]
        paper_entries = [
            json.loads(l) for l in paper_tick_log.read_text().splitlines()
        ]
        # Expected drift: either entry count differs OR some non-float column
        # value differs. Documented as opt-in behavior for tick sampling.
        differ = False
        if len(bt_entries) != len(paper_entries):
            differ = True
        else:
            for bt, pp in zip(bt_entries, paper_entries):
                if bt.get("binding_constraint") != pp.get("binding_constraint"):
                    differ = True
                    break
                if bt.get("filled_notional") != pp.get("filled_notional"):
                    differ = True
                    break
        assert differ, (
            "TickCadencePolicy MUST drift from bar_close backtest; the parity "
            "fixture is documented as opt-in — if they match, the "
            "sampling_cadence plumbing is inert."
        )

    def test_bar_close_cadence_policy_matches_backtest(
        self, parity_fixture_path, tmp_path,
    ):
        """Opposite: the DEFAULT SharedPoolPolicy (release cadence) must MATCH."""
        bt_log = tmp_path / "bt.jsonl"
        paper_log = tmp_path / "paper.jsonl"
        _run_backtest(parity_fixture_path, seed=42, log_path=bt_log)
        _run_paper(parity_fixture_path, seed=42, log_path=paper_log)
        bt_entries = [json.loads(l) for l in bt_log.read_text().splitlines()]
        paper_entries = [json.loads(l) for l in paper_log.read_text().splitlines()]
        assert len(bt_entries) == len(paper_entries)
        for bt, pp in zip(bt_entries, paper_entries):
            for col in NON_FLOAT_COLUMNS:
                assert bt[col] == pp[col]
