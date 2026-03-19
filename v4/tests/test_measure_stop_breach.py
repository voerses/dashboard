"""Acceptance tests for Task 12: Phase 0 measurement script (AC0a-AC0e, AC21).

Tests verify:
  - Intra-bar breach detection from 1m klines
  - Counterfactual PnL computation
  - False-trigger identification (breach then recovery)
  - Go/no-go report generation with thresholds
  - 30% realism haircut applied

All tests use synthetic data -- no real market data required.
These tests MUST FAIL until measure_stop_breach.py is implemented (RED phase).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "v4"))

import pytest

from tools.measure_stop_breach import (
    detect_intra_bar_breach,
    compute_counterfactual_pnl,
    identify_false_triggers,
    generate_go_nogo_report,
    apply_realism_haircut,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_1m_klines(prices: list[tuple[float, float, float, float]]) -> list[dict]:
    """Build synthetic 1m klines: list of (open, high, low, close) tuples.

    Returns list of dicts with standard kline fields.
    """
    klines = []
    base_ts = 1697380200000  # arbitrary start
    for i, (o, h, l, c) in enumerate(prices):
        klines.append({
            "open_time": base_ts + i * 60_000,
            "open": o,
            "high": h,
            "low": l,
            "close": c,
            "volume": 100.0,
        })
    return klines


def _make_trade_record(**overrides) -> dict:
    """Build a synthetic closed trade record."""
    defaults = dict(
        position_id="BTC:s56:100:primary",
        token="BTC",
        strategy_id="s56",
        direction=1,
        entry_price=66_000.0,
        exit_price=64_000.0,
        exit_bar=100,
        exit_reason="stop",
        margin_usd=10_000.0,
        quantity=0.15,
        pnl=-300.0,
        stop_price=64_000.0,
    )
    defaults.update(overrides)
    return defaults


# ===================================================================
# Test: Intra-bar breach detection from 1m klines
# ===================================================================

class TestIntraBarBreachDetection:
    """detect_intra_bar_breach identifies when stop was first breached."""

    def test_breach_detected_on_low(self):
        """Long stop breached when 1m low < stop_price."""
        klines = _make_1m_klines([
            (65_000.0, 65_500.0, 64_800.0, 65_200.0),  # no breach
            (65_200.0, 65_300.0, 63_800.0, 64_500.0),  # breach at low
            (64_500.0, 64_600.0, 64_000.0, 64_200.0),  # still breached
        ])
        stop_price = 64_000.0
        direction = 1

        result = detect_intra_bar_breach(klines, stop_price, direction)
        assert result is not None
        assert result["breach_bar_index"] == 1
        assert result["breach_price"] == pytest.approx(63_800.0)

    def test_no_breach_when_safe(self):
        """No breach detected when all 1m lows are above stop."""
        klines = _make_1m_klines([
            (65_000.0, 66_000.0, 64_500.0, 65_800.0),
            (65_800.0, 66_200.0, 65_000.0, 66_000.0),
        ])
        stop_price = 64_000.0
        direction = 1

        result = detect_intra_bar_breach(klines, stop_price, direction)
        assert result is None

    def test_short_breach_on_high(self):
        """Short stop breached when 1m high > stop_price."""
        klines = _make_1m_klines([
            (67_000.0, 67_500.0, 66_800.0, 67_200.0),  # no breach
            (67_200.0, 68_500.0, 67_000.0, 68_000.0),  # breach at high
        ])
        stop_price = 68_000.0
        direction = -1

        result = detect_intra_bar_breach(klines, stop_price, direction)
        assert result is not None
        assert result["breach_price"] == pytest.approx(68_500.0)


# ===================================================================
# Test: Counterfactual PnL computation
# ===================================================================

class TestCounterfactualPnl:
    """Counterfactual PnL: what if stop triggered at 1m breach instead of bar close."""

    def test_counterfactual_pnl_long(self):
        """Long position: earlier exit at breach price saves money vs bar close."""
        trade = _make_trade_record(
            direction=1, entry_price=66_000.0, exit_price=63_000.0,
            quantity=0.15,
        )
        breach_price = 63_800.0

        result = compute_counterfactual_pnl(trade, breach_price)
        # Actual PnL: 0.15 * (63000 - 66000) = -450
        # Counterfactual: 0.15 * (63800 - 66000) = -330
        # Improvement: -330 - (-450) = +120
        assert result["actual_pnl"] == pytest.approx(-450.0)
        assert result["counterfactual_pnl"] == pytest.approx(-330.0)
        assert result["improvement_usd"] == pytest.approx(120.0)

    def test_counterfactual_pnl_short(self):
        """Short position: earlier exit at breach price saves money."""
        trade = _make_trade_record(
            direction=-1, entry_price=66_000.0, exit_price=69_000.0,
            quantity=-0.15,
        )
        breach_price = 68_200.0

        result = compute_counterfactual_pnl(trade, breach_price)
        # Actual: abs(0.15) * (66000 - 69000) = -450
        # Counterfactual: abs(0.15) * (66000 - 68200) = -330
        # Improvement: -330 - (-450) = +120
        assert result["improvement_usd"] == pytest.approx(120.0)

    def test_slippage_in_bps(self):
        """Slippage computed in basis points."""
        trade = _make_trade_record(
            exit_price=63_000.0, stop_price=64_000.0,
        )
        breach_price = 63_800.0

        result = compute_counterfactual_pnl(trade, breach_price)
        # Slippage = |63000 - 63800| / 64000 * 10000 = 125 bps
        assert "slippage_bps" in result
        assert result["slippage_bps"] == pytest.approx(125.0, rel=0.01)


# ===================================================================
# Test: False-trigger identification
# ===================================================================

class TestFalseTriggerIdentification:
    """False triggers: breach intra-bar but position was NOT exited at bar close."""

    def test_false_trigger_detected(self):
        """Breach then recovery within the hour is a false trigger."""
        klines = _make_1m_klines([
            (65_000.0, 65_500.0, 63_800.0, 65_200.0),  # breach at low
            (65_200.0, 66_000.0, 65_000.0, 65_800.0),  # recovery
        ])
        stop_price = 64_000.0
        direction = 1
        bar_close_price = 65_800.0  # closed above stop -- not exited

        result = identify_false_triggers(klines, stop_price, direction, bar_close_price)
        assert result["is_false_trigger"] is True
        assert result["breach_price"] == pytest.approx(63_800.0)
        assert result["recovery_price"] == pytest.approx(65_800.0)

    def test_true_trigger_not_flagged(self):
        """Breach that stays breached is NOT a false trigger."""
        klines = _make_1m_klines([
            (65_000.0, 65_500.0, 63_800.0, 63_900.0),
            (63_900.0, 64_100.0, 63_500.0, 63_600.0),
        ])
        stop_price = 64_000.0
        direction = 1
        bar_close_price = 63_600.0  # closed below stop -- exited

        result = identify_false_triggers(klines, stop_price, direction, bar_close_price)
        assert result["is_false_trigger"] is False

    def test_no_breach_no_false_trigger(self):
        """No breach at all is not a false trigger."""
        klines = _make_1m_klines([
            (65_000.0, 66_000.0, 64_500.0, 65_800.0),
        ])
        stop_price = 64_000.0
        direction = 1
        bar_close_price = 65_800.0

        result = identify_false_triggers(klines, stop_price, direction, bar_close_price)
        assert result["is_false_trigger"] is False


# ===================================================================
# Test: Go/no-go report generation
# ===================================================================

class TestGoNoGoReport:
    """Go/no-go report with thresholds."""

    def test_report_contains_required_fields(self):
        """Report has per-portfolio rows with required columns."""
        portfolio_data = [
            {
                "portfolio_name": "portfolio_a",
                "num_stop_exits": 50,
                "avg_slippage_bps": 85.0,
                "worst_slippage_bps": 450.0,
                "gross_benefit_annualized_pct": 2.5,
                "false_trigger_cost_annualized_pct": 0.8,
            },
        ]

        report = generate_go_nogo_report(portfolio_data)
        assert "portfolio_name" in report[0]
        assert "net_benefit_pct" in report[0]
        assert "recommendation" in report[0]

    def test_proceed_when_net_benefit_above_1pct(self):
        """Recommendation is 'proceed' when net benefit > 1%."""
        portfolio_data = [
            {
                "portfolio_name": "portfolio_a",
                "num_stop_exits": 50,
                "avg_slippage_bps": 85.0,
                "worst_slippage_bps": 450.0,
                "gross_benefit_annualized_pct": 3.0,
                "false_trigger_cost_annualized_pct": 0.5,
            },
        ]

        report = generate_go_nogo_report(portfolio_data)
        # net = (3.0 - 0.5) * 0.7 = 1.75 > 1.0 => proceed
        assert report[0]["recommendation"] == "proceed"

    def test_kill_when_net_benefit_below_half_pct(self):
        """Recommendation is 'kill' when net benefit < 0.5%."""
        portfolio_data = [
            {
                "portfolio_name": "portfolio_b",
                "num_stop_exits": 10,
                "avg_slippage_bps": 20.0,
                "worst_slippage_bps": 50.0,
                "gross_benefit_annualized_pct": 0.5,
                "false_trigger_cost_annualized_pct": 0.3,
            },
        ]

        report = generate_go_nogo_report(portfolio_data)
        # net = (0.5 - 0.3) * 0.7 = 0.14 < 0.5 => kill
        assert report[0]["recommendation"] == "kill"

    def test_gray_zone_between_thresholds(self):
        """Recommendation is 'gray_zone' when 0.5% <= net < 1.0%."""
        portfolio_data = [
            {
                "portfolio_name": "portfolio_c",
                "num_stop_exits": 30,
                "avg_slippage_bps": 50.0,
                "worst_slippage_bps": 200.0,
                "gross_benefit_annualized_pct": 1.5,
                "false_trigger_cost_annualized_pct": 0.3,
            },
        ]

        report = generate_go_nogo_report(portfolio_data)
        # net = (1.5 - 0.3) * 0.7 = 0.84 => gray zone
        assert report[0]["recommendation"] == "gray_zone"


# ===================================================================
# Test: 30% realism haircut applied
# ===================================================================

class TestRealismHaircut:
    """30% realism haircut applied to net benefit."""

    def test_haircut_applied(self):
        """Net benefit is gross - cost, multiplied by 0.7 (30% haircut)."""
        gross = 3.0
        cost = 0.5
        result = apply_realism_haircut(gross, cost)
        expected = (gross - cost) * 0.7
        assert result == pytest.approx(expected)

    def test_haircut_on_zero_benefit(self):
        """Zero gross benefit with haircut produces negative or zero."""
        result = apply_realism_haircut(0.0, 0.5)
        assert result == pytest.approx(-0.35)

    def test_haircut_on_equal_cost_benefit(self):
        """Equal gross and cost produces zero after haircut."""
        result = apply_realism_haircut(1.0, 1.0)
        assert result == pytest.approx(0.0)
