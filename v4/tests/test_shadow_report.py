"""Acceptance tests for Task 13: Shadow reconciliation script (AC18).

Tests verify:
  - Joins shadow events with trades.jsonl by position_id
  - Classifies true_positive, false_trigger, preempted correctly
  - Computes gross benefit and false trigger cost
  - Applies 30% haircut
  - Per-strategy breakdown
  - Per-tier breakdown
  - Go/no-go thresholds (green/yellow/red)

All tests use synthetic data -- no real market data required.
These tests MUST FAIL until sentinel_shadow_report.py is implemented (RED phase).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "v4"))

import pytest

from tools.sentinel_shadow_report import (
    join_shadow_with_trades,
    classify_event,
    compute_gross_benefit,
    compute_false_trigger_cost,
    apply_haircut,
    generate_shadow_report,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_shadow_event(**overrides) -> dict:
    """Build a synthetic shadow event from sentinel_shadow.jsonl."""
    defaults = dict(
        timestamp="2025-10-15T14:30:30Z",
        portfolio="portfolio_a",
        position_id="BTC:s56:100:primary",
        token="BTC",
        strategy_id="s56",
        direction=1,
        event_type="breach_confirmed",
        exit_reason="sentinel_stop",
        entry_price=66_000.0,
        stop_price=64_000.0,
        breach_price=63_800.0,
        exit_price=63_500.0,
        confirmation_delay_s=30,
        liquidity_tier="btc_eth",
        slippage_bps=78.0,
        margin_usd=10_000.0,
        quantity=0.15,
        sentinel_pnl_estimate_usd=-375.0,
    )
    defaults.update(overrides)
    return defaults


def _make_trade(**overrides) -> dict:
    """Build a synthetic trade record from trades.jsonl."""
    defaults = dict(
        position_id="BTC:s56:100:primary",
        token="BTC",
        strategy_id="s56",
        direction=1,
        entry_price=66_000.0,
        exit_price=63_000.0,
        exit_reason="stop",
        exit_bar=100,
        pnl=-450.0,
        margin_usd=10_000.0,
        quantity=0.15,
    )
    defaults.update(overrides)
    return defaults


# ===================================================================
# Test: Join shadow events with trades.jsonl by position_id
# ===================================================================

class TestShadowTradeJoin:
    """Shadow events are joined with trades by position_id."""

    def test_join_by_position_id(self):
        """Matching position_id links shadow event to trade."""
        shadow_events = [_make_shadow_event(position_id="BTC:s56:100:primary")]
        trades = [_make_trade(position_id="BTC:s56:100:primary")]

        joined = join_shadow_with_trades(shadow_events, trades)
        assert len(joined) == 1
        assert joined[0]["shadow"]["position_id"] == joined[0]["trade"]["position_id"]

    def test_unmatched_shadow_event(self):
        """Shadow event with no matching trade still appears in results."""
        shadow_events = [_make_shadow_event(position_id="BTC:s56:100:primary")]
        trades = []  # No trades

        joined = join_shadow_with_trades(shadow_events, trades)
        assert len(joined) == 1
        assert joined[0]["trade"] is None

    def test_multiple_joins(self):
        """Multiple shadow events join with their respective trades."""
        shadow_events = [
            _make_shadow_event(position_id="BTC:s56:100:primary"),
            _make_shadow_event(position_id="ETH:s60:200:primary", token="ETH"),
        ]
        trades = [
            _make_trade(position_id="BTC:s56:100:primary"),
            _make_trade(position_id="ETH:s60:200:primary", token="ETH"),
        ]

        joined = join_shadow_with_trades(shadow_events, trades)
        assert len(joined) == 2


# ===================================================================
# Test: Classify events correctly
# ===================================================================

class TestEventClassification:
    """Events classified as true_positive, false_trigger, or preempted."""

    def test_true_positive(self):
        """Hourly also exited with stop -> true_positive."""
        shadow = _make_shadow_event(exit_reason="sentinel_stop")
        trade = _make_trade(exit_reason="stop")

        classification = classify_event(shadow, trade)
        assert classification == "true_positive"

    def test_false_trigger(self):
        """Hourly did NOT exit the position -> false_trigger."""
        shadow = _make_shadow_event()
        trade = None  # No corresponding trade (position recovered)

        classification = classify_event(shadow, trade)
        assert classification == "false_trigger"

    def test_preempted(self):
        """Hourly exited for a different reason -> preempted."""
        shadow = _make_shadow_event(exit_reason="sentinel_stop")
        trade = _make_trade(exit_reason="regime")  # Different exit reason

        classification = classify_event(shadow, trade)
        assert classification == "preempted"


# ===================================================================
# Test: Compute gross benefit
# ===================================================================

class TestGrossBenefit:
    """Gross benefit: PnL difference between sentinel and hourly exit."""

    def test_gross_benefit_positive(self):
        """Earlier sentinel exit saves money -> positive gross benefit."""
        # Sentinel exit at 63800, hourly at 63000 for a long
        # Improvement: 0.15 * (63800 - 63000) = 120
        events = [
            {
                "shadow": _make_shadow_event(exit_price=63_800.0),
                "trade": _make_trade(exit_price=63_000.0),
                "classification": "true_positive",
            },
        ]

        benefit = compute_gross_benefit(events)
        assert benefit == pytest.approx(120.0)

    def test_gross_benefit_multiple_trades(self):
        """Gross benefit sums across multiple true_positive events."""
        events = [
            {
                "shadow": _make_shadow_event(exit_price=63_800.0, quantity=0.15),
                "trade": _make_trade(exit_price=63_000.0, quantity=0.15),
                "classification": "true_positive",
            },
            {
                "shadow": _make_shadow_event(exit_price=63_500.0, quantity=0.10,
                                              position_id="ETH:s60:200:primary"),
                "trade": _make_trade(exit_price=63_000.0, quantity=0.10,
                                      position_id="ETH:s60:200:primary"),
                "classification": "true_positive",
            },
        ]

        benefit = compute_gross_benefit(events)
        # 0.15 * 800 + 0.10 * 500 = 120 + 50 = 170
        assert benefit == pytest.approx(170.0)

    def test_gross_benefit_ignores_false_triggers(self):
        """False triggers do not contribute to gross benefit."""
        events = [
            {
                "shadow": _make_shadow_event(),
                "trade": None,
                "classification": "false_trigger",
            },
        ]

        benefit = compute_gross_benefit(events)
        assert benefit == pytest.approx(0.0)


# ===================================================================
# Test: Compute false trigger cost
# ===================================================================

class TestFalseTriggerCost:
    """False trigger cost: positions exited that would have recovered."""

    def test_false_trigger_cost_computed(self):
        """False trigger cost = sentinel estimate of forfeited PnL."""
        events = [
            {
                "shadow": _make_shadow_event(sentinel_pnl_estimate_usd=-375.0),
                "trade": None,
                "classification": "false_trigger",
            },
        ]

        cost = compute_false_trigger_cost(events)
        assert cost > 0  # Cost is positive (lost opportunity)

    def test_no_false_triggers_zero_cost(self):
        """No false triggers -> zero cost."""
        events = [
            {
                "shadow": _make_shadow_event(),
                "trade": _make_trade(),
                "classification": "true_positive",
            },
        ]

        cost = compute_false_trigger_cost(events)
        assert cost == pytest.approx(0.0)


# ===================================================================
# Test: 30% haircut applied
# ===================================================================

class TestHaircutApplied:
    """30% realism haircut on net benefit."""

    def test_haircut_30_percent(self):
        """Net benefit has 30% haircut: net = (gross - cost) * 0.7."""
        gross = 500.0
        cost = 100.0
        result = apply_haircut(gross, cost, haircut_pct=0.30)
        expected = (gross - cost) * 0.70
        assert result == pytest.approx(expected)

    def test_haircut_negative_result(self):
        """Haircut can produce negative net benefit."""
        gross = 100.0
        cost = 200.0
        result = apply_haircut(gross, cost, haircut_pct=0.30)
        assert result < 0


# ===================================================================
# Test: Per-strategy breakdown
# ===================================================================

class TestPerStrategyBreakdown:
    """Report includes per-strategy breakdown."""

    def test_per_strategy_stats(self):
        """Report has stats grouped by strategy_id."""
        events = [
            {
                "shadow": _make_shadow_event(strategy_id="s56"),
                "trade": _make_trade(strategy_id="s56"),
                "classification": "true_positive",
            },
            {
                "shadow": _make_shadow_event(strategy_id="s60", position_id="ETH:s60:200:primary"),
                "trade": None,
                "classification": "false_trigger",
            },
        ]

        report = generate_shadow_report(events, capital=200_000.0)
        assert "per_strategy" in report
        strategy_ids = [s["strategy_id"] for s in report["per_strategy"]]
        assert "s56" in strategy_ids
        assert "s60" in strategy_ids


# ===================================================================
# Test: Per-tier breakdown
# ===================================================================

class TestPerTierBreakdown:
    """Report includes per-liquidity-tier breakdown."""

    def test_per_tier_stats(self):
        """Report has stats grouped by liquidity_tier."""
        events = [
            {
                "shadow": _make_shadow_event(liquidity_tier="btc_eth"),
                "trade": _make_trade(),
                "classification": "true_positive",
            },
            {
                "shadow": _make_shadow_event(
                    liquidity_tier="other",
                    position_id="OBSCURE:s63:100:primary",
                ),
                "trade": None,
                "classification": "false_trigger",
            },
        ]

        report = generate_shadow_report(events, capital=200_000.0)
        assert "per_tier" in report
        tiers = [t["tier"] for t in report["per_tier"]]
        assert "btc_eth" in tiers
        assert "other" in tiers


# ===================================================================
# Test: Go/no-go thresholds
# ===================================================================

class TestGoNoGoThresholds:
    """Go/no-go thresholds: green, yellow, red."""

    def test_green_high_benefit_low_false_trigger(self):
        """Green: net benefit > 1% AND false trigger rate < 30%."""
        events = [
            {
                "shadow": _make_shadow_event(exit_price=63_800.0),
                "trade": _make_trade(exit_price=63_000.0),
                "classification": "true_positive",
            },
        ] * 8 + [
            {
                "shadow": _make_shadow_event(position_id=f"FT:{i}"),
                "trade": None,
                "classification": "false_trigger",
            }
            for i in range(2)
        ]

        report = generate_shadow_report(events, capital=200_000.0)
        # false_trigger_rate = 2/10 = 20% < 30%, net benefit is high -> green
        assert report["go_nogo"] == "green"

    def test_red_high_false_trigger_rate(self):
        """Red: false trigger rate > 40%."""
        events = [
            {
                "shadow": _make_shadow_event(exit_price=63_800.0),
                "trade": _make_trade(exit_price=63_000.0),
                "classification": "true_positive",
            },
        ] * 3 + [
            {
                "shadow": _make_shadow_event(position_id=f"FT:{i}"),
                "trade": None,
                "classification": "false_trigger",
            }
            for i in range(7)
        ]

        report = generate_shadow_report(events, capital=200_000.0)
        # false_trigger_rate = 7/10 = 70% > 40% -> red
        assert report["go_nogo"] == "red"

    def test_red_low_net_benefit(self):
        """Red: net benefit < 0.5%."""
        # Single small true positive, no false triggers
        events = [
            {
                "shadow": _make_shadow_event(
                    exit_price=63_990.0, quantity=0.001,
                ),
                "trade": _make_trade(exit_price=63_980.0, quantity=0.001),
                "classification": "true_positive",
            },
        ]

        report = generate_shadow_report(events, capital=200_000.0)
        # Tiny benefit -> likely red or yellow
        assert report["go_nogo"] in ("red", "yellow")
