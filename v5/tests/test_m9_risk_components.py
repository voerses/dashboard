"""M9 AC #5 — Risk Components + TradingState + FIX citations.

Covers AC #5 components:
  - DrawdownThrottle([(0.10, 0.5), (0.20, 0.0)]) reduces at 10%, halts at 20%
  - MaxGrossExposure(0.80, "notional_usd") blocks when |notional|>80% equity
  - MaxNetExposure(0.30) caps net long/short imbalance
  - MaxConcurrentOrders(40) caps total open orders across strategies
  - DailyLossLimit(5000.0, "utc_midnight") halts at $5k/day loss
  - FundingSafetyCheck(50.0, "block") blocks at >50 bps funding
  - MaxCorrelatedExposure(2, corr_threshold=0.85) blocks 3+ strats on same
    token + no-double-throttle with concentration clamp
  - PerSymbolStrategyLimit({"BTCUSDT": 2}) hard per-token strategy cap
  - TradingState ACTIVE → REDUCING (scale qty_delta<0 ALLOWED, qty_delta>0
    BLOCKED) → HALTED transitions
  - RiskDecision.REJECT cites FIX OrdRejReason(103); HALT cites
    TradingSessionStatus(340)=3

All tests MUST FAIL today — v5.risk module does not exist.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _candidate(strategy_id: str, token: str, notional_usd: float = 10_000.0,
               fraction_of_equity: float = 0.05):
    """Minimal EntryCandidate carrying a SizingRequest."""
    from v5.arbitration import EntryCandidate
    from v5.sizing.intents import SizingIntent, SizingRequest
    return EntryCandidate(
        strategy_id=strategy_id,
        token=token,
        priority=1.0,
        sizing=SizingRequest(
            intent=SizingIntent.FRACTION_OF_EQUITY,
            fraction_of_equity=fraction_of_equity,
            notional_usd=notional_usd,
        ),
    )


def _sim_state(
    *,
    equity: float = 100_000.0,
    peak_equity: float = 100_000.0,
    open_positions_notional_usd: dict | None = None,
    open_orders_count: int = 0,
    realized_pnl_today_usd: float = 0.0,
    funding_rate_bps: dict | None = None,
    trading_state: str = "ACTIVE",
    per_symbol_strategies: dict | None = None,
    correlation_matrix: dict | None = None,
    close_arrays: dict | None = None,
    bar_idx: int = 1000,
):
    """Construct SimulationState with keyword-only aggregate fields for tests.

    Field names must match the v5.risk public SimulationState schema; the
    current test stub imports from v5.arbitration (which also re-exports the
    dataclass in M9)."""
    from v5.arbitration import SimulationState
    return SimulationState(
        equity=equity,
        peak_equity=peak_equity,
        open_positions_notional_usd=open_positions_notional_usd or {},
        open_orders_count=open_orders_count,
        realized_pnl_today_usd=realized_pnl_today_usd,
        funding_rate_bps=funding_rate_bps or {},
        trading_state=trading_state,
        per_symbol_strategies=per_symbol_strategies or {},
        correlation_matrix=correlation_matrix or {},
        close_arrays=close_arrays or {},
        bar_idx=bar_idx,
    )


# ---------------------------------------------------------------------------
# DrawdownThrottle
# ---------------------------------------------------------------------------


class TestDrawdownThrottle:
    """AC #5: DrawdownThrottle reduces sizing at 10% dd, halts at 20% dd."""

    def test_drawdown_throttle_no_reduce_below_threshold(self):
        """5% dd: no throttle, decision ACCEPT, fraction unchanged."""
        from v5.risk import DrawdownThrottle, RiskDecision
        throttle = DrawdownThrottle(
            thresholds=[(0.10, 0.5), (0.20, 0.0)], scope="portfolio"
        )
        cand = _candidate("sA", "BTC", fraction_of_equity=0.10)
        state = _sim_state(equity=95_000.0, peak_equity=100_000.0)  # 5% dd
        decision = throttle.check(cand, state, clock_now_ns=0)
        assert decision == RiskDecision.ACCEPT
        assert cand.sizing.fraction_of_equity == pytest.approx(0.10)

    def test_drawdown_throttle_reduces_at_10pct_dd(self):
        """10% dd: sizing × 0.5; decision REDUCE."""
        from v5.risk import DrawdownThrottle, RiskDecision
        throttle = DrawdownThrottle(
            thresholds=[(0.10, 0.5), (0.20, 0.0)], scope="portfolio"
        )
        cand = _candidate("sA", "BTC", fraction_of_equity=0.10)
        state = _sim_state(equity=90_000.0, peak_equity=100_000.0)  # 10% dd
        decision = throttle.check(cand, state, clock_now_ns=0)
        assert decision == RiskDecision.REDUCE
        assert cand.sizing.fraction_of_equity == pytest.approx(0.05)

    def test_drawdown_throttle_halts_at_20pct_dd(self):
        """20% dd: sizing × 0.0 halts; decision HALT."""
        from v5.risk import DrawdownThrottle, RiskDecision
        throttle = DrawdownThrottle(
            thresholds=[(0.10, 0.5), (0.20, 0.0)], scope="portfolio"
        )
        cand = _candidate("sA", "BTC", fraction_of_equity=0.10)
        state = _sim_state(equity=80_000.0, peak_equity=100_000.0)  # 20% dd
        decision = throttle.check(cand, state, clock_now_ns=0)
        assert decision == RiskDecision.HALT


# ---------------------------------------------------------------------------
# MaxGrossExposure / MaxNetExposure / MaxConcurrentOrders
# ---------------------------------------------------------------------------


class TestMaxGrossExposure:
    """AC #5: MaxGrossExposure blocks when |notional| sum > 80% equity."""

    def test_gross_exposure_block_when_over_limit(self):
        """Sum |notional| already at 85k on 100k equity; new 10k order rejected."""
        from v5.risk import MaxGrossExposure, RiskDecision
        comp = MaxGrossExposure(limit_pct=0.80, denominator="notional_usd")
        state = _sim_state(
            equity=100_000.0,
            open_positions_notional_usd={"BTC": 50_000.0, "ETH": -35_000.0},
        )
        cand = _candidate("sA", "SOL", notional_usd=10_000.0)
        decision = comp.check(cand, state, clock_now_ns=0)
        # 85k + 10k = 95k > 80% * 100k = 80k -> reject
        assert decision == RiskDecision.REJECT

    def test_gross_exposure_accept_when_within_limit(self):
        """Sum |notional| 50k + new 10k = 60k < 80k limit -> accept."""
        from v5.risk import MaxGrossExposure, RiskDecision
        comp = MaxGrossExposure(limit_pct=0.80, denominator="notional_usd")
        state = _sim_state(
            equity=100_000.0,
            open_positions_notional_usd={"BTC": 50_000.0},
        )
        cand = _candidate("sA", "SOL", notional_usd=10_000.0)
        decision = comp.check(cand, state, clock_now_ns=0)
        assert decision == RiskDecision.ACCEPT


class TestMaxNetExposure:
    """AC #5: MaxNetExposure(0.30) caps long/short imbalance at 30% equity."""

    def test_max_net_exposure_block_when_over_limit(self):
        """Net long 35k on 100k equity + new 10k long -> reject (45k>30k cap)."""
        from v5.risk import MaxNetExposure, RiskDecision
        comp = MaxNetExposure(limit_pct=0.30)
        state = _sim_state(
            equity=100_000.0,
            open_positions_notional_usd={"BTC": 50_000.0, "ETH": -15_000.0},
        )
        # net = +35k already; adding +10k -> 45k > 30k cap
        cand = _candidate("sA", "SOL", notional_usd=10_000.0)
        decision = comp.check(cand, state, clock_now_ns=0)
        assert decision == RiskDecision.REJECT


class TestMaxConcurrentOrders:
    """AC #5: MaxConcurrentOrders(40) caps total open orders."""

    def test_max_concurrent_orders_rejects_at_cap(self):
        """40 open orders already; 41st rejected."""
        from v5.risk import MaxConcurrentOrders, RiskDecision
        comp = MaxConcurrentOrders(limit=40)
        state = _sim_state(open_orders_count=40)
        cand = _candidate("sA", "BTC")
        decision = comp.check(cand, state, clock_now_ns=0)
        assert decision == RiskDecision.REJECT


# ---------------------------------------------------------------------------
# DailyLossLimit / FundingSafetyCheck
# ---------------------------------------------------------------------------


class TestDailyLossLimit:
    """AC #5: DailyLossLimit(5000.0, 'utc_midnight') halts at $5k/day loss."""

    def test_daily_loss_limit_halts_at_threshold(self):
        """Today's realized pnl = -$5,001 -> HALT (crosses the cap)."""
        from v5.risk import DailyLossLimit, RiskDecision
        comp = DailyLossLimit(
            max_daily_loss_usd=5_000.0, day_boundary="utc_midnight"
        )
        state = _sim_state(realized_pnl_today_usd=-5_001.0)
        cand = _candidate("sA", "BTC")
        decision = comp.check(cand, state, clock_now_ns=0)
        assert decision == RiskDecision.HALT


class TestFundingSafetyCheck:
    """AC #5: FundingSafetyCheck(50.0, 'block') blocks entries at >50 bps funding."""

    def test_funding_safety_blocks_above_threshold(self):
        """BTC funding = 75 bps > 50 bps cap, action='block' -> REJECT."""
        from v5.risk import FundingSafetyCheck, RiskDecision
        comp = FundingSafetyCheck(max_funding_bps=50.0, action="block")
        state = _sim_state(funding_rate_bps={"BTC": 75.0})
        cand = _candidate("sA", "BTC")
        decision = comp.check(cand, state, clock_now_ns=0)
        assert decision == RiskDecision.REJECT


# ---------------------------------------------------------------------------
# MaxCorrelatedExposure + no-double-throttle
# ---------------------------------------------------------------------------


class TestMaxCorrelatedExposure:
    """AC #5: MaxCorrelatedExposure blocks 3+ strategies on the same symbol
    (and throttles — not hard-rejects — to avoid double-throttle with M8
    concentration clamp)."""

    def test_blocks_third_strategy_on_same_token(self):
        """2 strategies already on BTC; 3rd rejected (max_strategies_per_symbol=2)."""
        from v5.risk import MaxCorrelatedExposure, RiskDecision
        comp = MaxCorrelatedExposure(
            max_strategies_per_symbol=2, corr_threshold=0.85
        )
        state = _sim_state(per_symbol_strategies={"BTC": {"sA", "sB"}})
        cand = _candidate("sC", "BTC")
        decision = comp.check(cand, state, clock_now_ns=0)
        assert decision == RiskDecision.REJECT

    def test_throttles_rather_than_hard_rejects_on_notional_cap(self):
        """No-double-throttling AC: with max_correlated_notional_pct=0.50 and
        3 BTC-correlated candidates, component ADJUSTS fraction_of_equity (not
        hard reject), ensuring post-M8-concentration sum ≤ 50%."""
        from v5.risk import MaxCorrelatedExposure, RiskDecision
        comp = MaxCorrelatedExposure(
            max_strategies_per_symbol=5,  # slack; we test the ρ-based branch
            max_correlated_notional_pct=0.50,
            corr_threshold=0.85,
        )
        cand = _candidate("sC", "ETH", fraction_of_equity=0.40)
        state = _sim_state(
            equity=100_000.0,
            open_positions_notional_usd={"BTC": 40_000.0},
            # ETH vs BTC ρ > threshold
            correlation_matrix={("BTC", "ETH"): 0.92, ("ETH", "BTC"): 0.92},
        )
        decision = comp.check(cand, state, clock_now_ns=0)
        # existing 40k (0.40 equity) + new candidate would cross 0.50 cap
        # component REDUCES rather than REJECT to avoid double-throttle
        assert decision == RiskDecision.REDUCE
        assert cand.sizing.fraction_of_equity < 0.40


# ---------------------------------------------------------------------------
# PerSymbolStrategyLimit
# ---------------------------------------------------------------------------


class TestPerSymbolStrategyLimit:
    """AC #5: PerSymbolStrategyLimit({'BTCUSDT': 2}) hard per-token strategy cap."""

    def test_per_symbol_strategy_limit_blocks_third(self):
        """BTCUSDT limit=2; 3rd strategy attempting BTCUSDT rejected."""
        from v5.risk import PerSymbolStrategyLimit, RiskDecision
        comp = PerSymbolStrategyLimit(limits={"BTCUSDT": 2})
        state = _sim_state(
            per_symbol_strategies={"BTCUSDT": {"sA", "sB"}},
        )
        cand = _candidate("sC", "BTCUSDT")
        decision = comp.check(cand, state, clock_now_ns=0)
        assert decision == RiskDecision.REJECT

    def test_per_symbol_strategy_limit_accepts_unlimited_symbols(self):
        """Symbol not in limits dict -> unlimited -> accept."""
        from v5.risk import PerSymbolStrategyLimit, RiskDecision
        comp = PerSymbolStrategyLimit(limits={"BTCUSDT": 2})
        state = _sim_state(
            per_symbol_strategies={"ETHUSDT": {"sA", "sB", "sC", "sD"}},
        )
        cand = _candidate("sE", "ETHUSDT")
        decision = comp.check(cand, state, clock_now_ns=0)
        assert decision == RiskDecision.ACCEPT


# ---------------------------------------------------------------------------
# TradingState transitions
# ---------------------------------------------------------------------------


class TestTradingStateTransitions:
    """AC #5: TradingState ACTIVE → REDUCING → HALTED transitions.

    - REDUCING: new entries BLOCKED; check_scale(qty_delta<0) ALLOWED;
      check_scale(qty_delta>0) BLOCKED.
    - HALTED: all new entries + in-flight TRIGGERED cancelled.
    """

    def test_reducing_state_blocks_new_entries(self):
        """TradingState=REDUCING: fresh EntryCandidate rejected."""
        from v5.risk import check_entry_allowed, RiskDecision
        state = _sim_state(trading_state="REDUCING")
        cand = _candidate("sA", "BTC")
        decision = check_entry_allowed(cand, state)
        assert decision == RiskDecision.REJECT

    def test_reducing_state_allows_negative_qty_scale(self):
        """TradingState=REDUCING: check_scale(qty_delta=-0.5) ALLOWED."""
        from v5.risk import check_scale_allowed, RiskDecision
        state = _sim_state(trading_state="REDUCING")
        decision = check_scale_allowed(qty_delta=-0.5, state=state)
        assert decision == RiskDecision.ACCEPT

    def test_reducing_state_blocks_positive_qty_scale(self):
        """TradingState=REDUCING: check_scale(qty_delta=+0.5) BLOCKED."""
        from v5.risk import check_scale_allowed, RiskDecision
        state = _sim_state(trading_state="REDUCING")
        decision = check_scale_allowed(qty_delta=+0.5, state=state)
        assert decision == RiskDecision.REJECT

    def test_halted_state_blocks_all_new_entries(self):
        """TradingState=HALTED: new entries rejected."""
        from v5.risk import check_entry_allowed, RiskDecision
        state = _sim_state(trading_state="HALTED")
        cand = _candidate("sA", "BTC")
        decision = check_entry_allowed(cand, state)
        assert decision == RiskDecision.HALT


# ---------------------------------------------------------------------------
# FIX citations in RiskDecision docstrings
# ---------------------------------------------------------------------------


class TestRiskDecisionFIXCitations:
    """AC #5: RiskDecision.REJECT cites FIX OrdRejReason(103);
    HALT cites TradingSessionStatus(340)=3."""

    def test_risk_decision_reject_has_fix_citation_in_docstring(self):
        """RiskDecision.REJECT.__doc__ or member docstring references
        FIX OrdRejReason(103)."""
        from v5.risk import RiskDecision
        # Either the Enum class docstring or the member's inline doc must cite it
        src_doc = (RiskDecision.__doc__ or "") + \
                  str(getattr(RiskDecision.REJECT, "__doc__", "") or "")
        assert "OrdRejReason" in src_doc and "103" in src_doc, (
            "RiskDecision.REJECT must cite FIX OrdRejReason(103) per "
            "AC #5 FIX-citation contract."
        )

    def test_risk_decision_halt_has_fix_citation_in_docstring(self):
        """RiskDecision.HALT cites FIX TradingSessionStatus(340)=3 Halted."""
        from v5.risk import RiskDecision
        src_doc = (RiskDecision.__doc__ or "") + \
                  str(getattr(RiskDecision.HALT, "__doc__", "") or "")
        assert "TradingSessionStatus" in src_doc and "340" in src_doc, (
            "RiskDecision.HALT must cite FIX TradingSessionStatus(340)=3 per "
            "AC #5 FIX-citation contract."
        )
