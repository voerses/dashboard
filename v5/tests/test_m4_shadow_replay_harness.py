"""M4 — Shadow replay harness self-test: 6-dim trade-tuple + price mutations.

Covers:
  - AC41 T-B33: Harness replays a synthetic 48h fixture through pre-M4 and
    post-M4 engine mocks producing known-equivalent outputs -> reports PASS.
    Mutate each of the 6 tuple dimensions (strategy_id, token, entry_ts,
    direction, leg_index, exit_reason) plus price -> harness reports FAIL
    with a structured diff identifying the failing dimension.

All tests MUST FAIL today — v5.tests.shadow_replay harness does not exist.
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


def _baseline_trade() -> dict:
    """One baseline trade tuple matching AC41's 6-dimension identity spec."""
    return {
        "strategy_id": "s524",
        "token": "BTC",
        "entry_ts": 1_770_003_600 * 1_000_000_000,  # float64 ns per AC41
        "direction": 1,
        "leg_index": 0,
        "exit_reason": "take_profit",
        "entry_price": 100.0,
        "exit_price": 102.0,
        "pnl": 2.0,
        "notional": 100.0,
    }


def _baseline_trade_list() -> list[dict]:
    """Synthetic pre-M4 / post-M4 trade list — a handful of trades."""
    trades = []
    for i in range(5):
        t = _baseline_trade()
        t["entry_ts"] = 1_770_003_600 * 1_000_000_000 + i * 3600 * 1_000_000_000
        t["token"] = ["BTC", "ETH", "SOL", "BNB", "XRP"][i]
        trades.append(t)
    return trades


class TestTB33HarnessSelfTest:
    """T-B33: pre == post baseline -> harness reports PASS."""

    def test_harness_reports_pass_on_equivalent_inputs(self):
        """T-B33: baseline trade lists identical -> harness returns PASS."""
        from v5.tests.shadow_replay import run_shadow_replay

        pre = _baseline_trade_list()
        post = _baseline_trade_list()
        report = run_shadow_replay(pre_trades=pre, post_trades=post)
        assert report.status == "PASS", f"Expected PASS; got {report.status}"
        assert not report.failing_trades


class TestTB33MutateSixTupleDimensions:
    """T-B33: mutating each of the 6 tuple dimensions causes FAIL with structured diff."""

    @pytest.mark.parametrize("dim", [
        "strategy_id", "token", "entry_ts", "direction",
        "leg_index", "exit_reason",
    ])
    def test_mutating_each_dim_reports_fail(self, dim):
        """Mutate one trade's tuple dimension -> harness FAIL + diff identifies dim."""
        from v5.tests.shadow_replay import run_shadow_replay

        pre = _baseline_trade_list()
        post = copy.deepcopy(pre)
        # Mutate the first trade's dimension.
        if dim == "strategy_id":
            post[0]["strategy_id"] = "different_strategy"
        elif dim == "token":
            post[0]["token"] = "XXX"
        elif dim == "entry_ts":
            post[0]["entry_ts"] += 1_000_000_000  # 1 second drift
        elif dim == "direction":
            post[0]["direction"] = -1
        elif dim == "leg_index":
            post[0]["leg_index"] = 1
        elif dim == "exit_reason":
            post[0]["exit_reason"] = "stop_loss"

        report = run_shadow_replay(pre_trades=pre, post_trades=post)
        assert report.status == "FAIL", (
            f"Expected FAIL when mutating {dim}; got {report.status}"
        )
        # Structured diff must identify the failing dimension.
        assert any(dim in str(d) for d in report.diffs), (
            f"Diff does not identify dimension '{dim}'; diffs={report.diffs}"
        )


class TestTB33PriceMutationFailsOnBpsBreach:
    """T-B33 + AC41 tolerance: 10 bps exit_price mutation -> FAIL."""

    def test_10_bps_exit_price_delta_reports_fail(self):
        """Mutating one trade's exit_price by 10 bps breaches the 5 bps per-trade
        budget -> harness reports FAIL."""
        from v5.tests.shadow_replay import run_shadow_replay

        pre = _baseline_trade_list()
        post = copy.deepcopy(pre)
        # 10 bps of 102.0 = 0.102 -> 102.102.
        post[0]["exit_price"] = post[0]["exit_price"] * (1.0 + 10e-4)
        report = run_shadow_replay(pre_trades=pre, post_trades=post)
        assert report.status == "FAIL", (
            f"Expected FAIL on 10 bps exit_price drift; got {report.status}"
        )

    def test_within_4_ulp_price_passes(self):
        """ULP-level drift on entry/exit prices within budget -> PASS."""
        from v5.tests.shadow_replay import run_shadow_replay
        import numpy as np

        pre = _baseline_trade_list()
        post = copy.deepcopy(pre)
        # Apply ~2 ULP drift to first trade's exit_price (float32).
        p = np.float32(post[0]["exit_price"])
        post[0]["exit_price"] = float(np.nextafter(np.nextafter(p, np.float32(np.inf)),
                                                  np.float32(np.inf)))
        report = run_shadow_replay(pre_trades=pre, post_trades=post)
        assert report.status == "PASS"


class TestTB33AggregatePnLBound:
    """AC41: aggregate fleet-wide PnL delta <= 10 bps of total notional."""

    def test_aggregate_drift_breaches_10_bps(self):
        """Small per-trade drifts in the same direction accumulate -> FAIL."""
        from v5.tests.shadow_replay import run_shadow_replay

        pre = _baseline_trade_list()
        post = copy.deepcopy(pre)
        # Each trade drifts 3 bps (under per-trade 5bps). Sum of 5 trades *
        # 3 bps = 15 bps total — above the 10 bps aggregate ceiling.
        for t in post:
            t["pnl"] += t["notional"] * 3e-4
        report = run_shadow_replay(pre_trades=pre, post_trades=post)
        assert report.status == "FAIL"

    def test_structured_diff_report_markdown_output(self, tmp_path):
        """AC41: harness emits shadow_replay_report.md with a single per-strategy
        breakdown section — exactly one `## Per-strategy breakdown` heading
        and exactly one table with the documented 4-column schema
        (Strategy | Pass | PnL delta (bps) | Trade count)."""
        from v5.tests.shadow_replay import run_shadow_replay

        pre = _baseline_trade_list()
        post = copy.deepcopy(pre)
        report = run_shadow_replay(
            pre_trades=pre, post_trades=post, report_path=tmp_path / "report.md",
        )
        report_file = tmp_path / "report.md"
        assert report_file.is_file()
        content = report_file.read_text()

        # Exactly one `## Per-strategy breakdown` heading.
        heading_count = sum(
            1 for line in content.splitlines()
            if line.strip() == "## Per-strategy breakdown"
        )
        assert heading_count == 1, (
            f"Expected exactly 1 '## Per-strategy breakdown' heading; got {heading_count}"
        )

        # Exactly one markdown table with the documented 4-column header.
        expected_cols = ("Strategy", "Pass", "PnL delta (bps)", "Trade count")
        table_header_count = 0
        for line in content.splitlines():
            stripped = line.strip()
            if not (stripped.startswith("|") and stripped.endswith("|")):
                continue
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if tuple(cells) == expected_cols:
                table_header_count += 1
        assert table_header_count == 1, (
            f"Expected exactly 1 table with columns {expected_cols!r}; "
            f"got {table_header_count}"
        )
