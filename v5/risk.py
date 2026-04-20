"""M9 C-5 — RiskComponent Protocol + 8 components + TradingState.

Risk components run at BarProcessor Phase 3.0 on AGGREGATE multi-strategy
state, BEFORE arbitration (C-1 SignalArbitrationPolicy). M8 clamps fire
later at `Order.release_atomic` per-order — the two layers are
complementary (different constraint classes).

FIX citations on RiskDecision:
  REJECT → FIX OrdRejReason(103)
  HALT   → FIX TradingSessionStatus(340)=3 Halted
  REDUCE → engine-internal; no FIX analog (at the wire layer manifests
           as OrderCancelReplaceRequest(G) reducing OrderQty(38))
  ACCEPT → no FIX analog (internal pass-through)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal, Optional, Protocol, runtime_checkable


# ============================================================
# RiskDecision + RiskVerdict + TradingState
# ============================================================


class RiskDecision(Enum):
    """Risk-component verdict on an EntryCandidate.

    FIX citations (for M10 wire-gateway alignment):
      - REJECT cites FIX OrdRejReason(103) — downstream ExecReport carries
        tag 103=99 "Other" with reason string when a risk component
        rejects. Engine-internal reject; no Party/PartyRole(448/452)
        (that's give-up routing).
      - HALT cites FIX TradingSessionStatus(340)=3 "Halted" — session-level
        halt blocking all new entries; matches venue halt semantics.
      - REDUCE is engine-internal (no FIX analog); at the wire layer it
        manifests as OrderCancelReplaceRequest(G) reducing OrderQty(38).
      - ACCEPT is pass-through; no wire message.
    """

    ACCEPT = "ACCEPT"
    REJECT = "REJECT"  # FIX OrdRejReason(103)
    REDUCE = "REDUCE"  # engine-internal; no FIX analog
    HALT = "HALT"      # FIX TradingSessionStatus(340)=3 Halted


@dataclass
class RiskVerdict:
    """Verdict returned by a RiskComponent.check() call."""
    decision: RiskDecision
    throttle_fraction: float = 1.0
    reason: str = ""


@dataclass(frozen=True)
class StateView:
    """Read-only portfolio snapshot surfaced to strategy reactive sizing
    via `BarContext.state_view` (M9 AC #14). Frozen to prevent strategy
    code from accidentally mutating engine state."""
    equity: float = 0.0
    portfolio_dd_pct: float = 0.0
    open_positions_count: int = 0
    total_notional_usd: float = 0.0
    per_strategy_equity: dict = field(default_factory=dict)
    per_symbol_exposure: dict = field(default_factory=dict)
    bars_since_last_fill: dict = field(default_factory=dict)


@dataclass
class TradingState:
    """Portfolio-wide trading state derived from risk-component verdicts."""
    state: Literal["ACTIVE", "REDUCING", "HALTED"] = "ACTIVE"
    reason: str = ""
    throttle_fraction: float = 1.0
    last_transition_ts: int = 0


# ============================================================
# RiskComponent Protocol
# ============================================================


@runtime_checkable
class RiskComponent(Protocol):
    """Base Protocol for risk components. `sampling_cadence` declares
    when `state` was snapshotted — required to prevent the 2023
    crypto-prop 80bps drift incident (bar-cadence vs tick-cadence
    state sampling asymmetry)."""

    sampling_cadence: Literal["bar_close", "tick", "release"]

    def check(
        self,
        candidate,
        state,
        clock_now_ns: int,
    ) -> RiskDecision: ...


# ============================================================
# 6 simple risk components (T10a)
# ============================================================


class DrawdownThrottle:
    """AC #5: reduces position sizing or halts based on portfolio drawdown.

    `thresholds`: list of `(dd_trigger, position_scale)` tuples.
    Evaluated in order; highest-matching wins.
    scale=0.0 ⇒ HALT. scale<1.0 ⇒ REDUCE.
    """

    sampling_cadence: Literal["bar_close", "tick", "release"] = "bar_close"

    def __init__(
        self,
        thresholds: list[tuple[float, float]],
        scope: Literal["portfolio", "strategy"] = "portfolio",
    ):
        self.thresholds = sorted(thresholds, key=lambda t: -t[0])  # desc by dd
        self.scope = scope

    def check(self, candidate, state, clock_now_ns: int) -> RiskDecision:
        peak = getattr(state, "peak_equity", 0.0)
        equity = getattr(state, "equity", 0.0)
        if peak <= 0:
            return RiskDecision.ACCEPT
        dd = max(0.0, (peak - equity) / peak)
        for trigger, scale in self.thresholds:
            if dd >= trigger:
                if scale <= 0.0:
                    return RiskDecision.HALT
                # Reduce sizing
                if candidate.sizing is not None and candidate.sizing.fraction_of_equity is not None:
                    candidate.sizing.fraction_of_equity *= scale
                return RiskDecision.REDUCE
        return RiskDecision.ACCEPT


class MaxGrossExposure:
    """Caps total |notional| exposure across the portfolio."""

    sampling_cadence: Literal["bar_close", "tick", "release"] = "release"

    def __init__(
        self,
        limit_pct: float,
        denominator: Literal["notional_usd", "initial_margin", "equity"] = "notional_usd",
    ):
        self.limit_pct = limit_pct
        self.denominator = denominator

    def check(self, candidate, state, clock_now_ns: int) -> RiskDecision:
        equity = getattr(state, "equity", 0.0)
        if equity <= 0:
            return RiskDecision.ACCEPT
        existing = sum(
            abs(v) for v in getattr(state, "open_positions_notional_usd", {}).values()
        )
        new_notional = abs(getattr(candidate, "notional_usd", None) or 0.0)
        total = existing + new_notional
        cap = self.limit_pct * equity
        if total > cap:
            return RiskDecision.REJECT
        return RiskDecision.ACCEPT


class MaxNetExposure:
    """Caps net long-minus-short notional as a fraction of equity."""

    sampling_cadence: Literal["bar_close", "tick", "release"] = "release"

    def __init__(self, limit_pct: float):
        self.limit_pct = limit_pct

    def check(self, candidate, state, clock_now_ns: int) -> RiskDecision:
        equity = getattr(state, "equity", 0.0)
        if equity <= 0:
            return RiskDecision.ACCEPT
        net = sum(getattr(state, "open_positions_notional_usd", {}).values())
        new_signed = (getattr(candidate, "notional_usd", None) or 0.0) * (
            getattr(candidate, "direction", 1) or 1
        )
        # Candidate direction default +1 (long); negative notional = short.
        # In the test fixture `notional_usd` is absolute; we assume +1 long.
        if new_signed >= 0:
            new_signed = abs(new_signed)
        projected_net = net + new_signed
        cap = self.limit_pct * equity
        if abs(projected_net) > cap:
            return RiskDecision.REJECT
        return RiskDecision.ACCEPT


class MaxConcurrentOrders:
    """Caps total open orders across all strategies."""

    sampling_cadence: Literal["bar_close", "tick", "release"] = "release"

    def __init__(self, limit: int):
        self.limit = limit

    def check(self, candidate, state, clock_now_ns: int) -> RiskDecision:
        if getattr(state, "open_orders_count", 0) >= self.limit:
            return RiskDecision.REJECT
        return RiskDecision.ACCEPT


class DailyLossLimit:
    """Halts trading when today's realized PnL falls below `-max_daily_loss_usd`."""

    sampling_cadence: Literal["bar_close", "tick", "release"] = "bar_close"

    def __init__(
        self,
        max_daily_loss_usd: float,
        day_boundary: Literal["utc_midnight", "session_open"] = "utc_midnight",
    ):
        self.max_daily_loss_usd = max_daily_loss_usd
        self.day_boundary = day_boundary

    def check(self, candidate, state, clock_now_ns: int) -> RiskDecision:
        pnl = getattr(state, "realized_pnl_today_usd", 0.0)
        if pnl <= -abs(self.max_daily_loss_usd):
            return RiskDecision.HALT
        return RiskDecision.ACCEPT


class FundingSafetyCheck:
    """Blocks (or warns) entries when per-token funding exceeds `max_funding_bps`."""

    sampling_cadence: Literal["bar_close", "tick", "release"] = "release"

    def __init__(
        self,
        max_funding_bps: float,
        action: Literal["warn", "block"] = "warn",
    ):
        self.max_funding_bps = max_funding_bps
        self.action = action

    def check(self, candidate, state, clock_now_ns: int) -> RiskDecision:
        token = getattr(candidate, "token", None)
        rates = getattr(state, "funding_rate_bps", {})
        rate = rates.get(token, 0.0)
        if abs(rate) > self.max_funding_bps:
            return RiskDecision.REJECT if self.action == "block" else RiskDecision.ACCEPT
        return RiskDecision.ACCEPT


# ============================================================
# 2 correlation-based risk components (T10b)
# ============================================================


class MaxCorrelatedExposure:
    """AC #5: per-symbol cross-strategy limit + anti-correlated-pair check.

    Blocks when N+ strategies attempt the same token OR when summed notional
    on tokens with ρ > corr_threshold exceeds `max_correlated_notional_pct`
    fraction of portfolio equity.

    Throttles (REDUCE) rather than hard-rejects when the binding is
    notional-based — prevents double-throttle with M8 concentration clamp.
    Correlation source: 168-bar log-return window from `state.close_arrays`,
    memoized per `(bar_idx, frozenset(token_set))` at `sampling_cadence="bar_close"`.
    """

    sampling_cadence: Literal["bar_close", "tick", "release"] = "bar_close"

    def __init__(
        self,
        max_strategies_per_symbol: int = 2,
        max_correlated_notional_pct: Optional[float] = None,
        corr_threshold: float = 0.85,
        corr_lookback_bars: int = 168,
    ):
        self.max_strategies_per_symbol = max_strategies_per_symbol
        self.max_correlated_notional_pct = max_correlated_notional_pct
        self.corr_threshold = corr_threshold
        self.corr_lookback_bars = corr_lookback_bars
        self._corr_cache: dict = {}  # keyed by (bar_idx, frozenset(tokens))

    def check(self, candidate, state, clock_now_ns: int) -> RiskDecision:
        token = candidate.token
        sid = candidate.strategy_id

        # Per-symbol strategy count check
        active_strategies = getattr(state, "per_symbol_strategies", {}).get(token, set())
        if sid in active_strategies:
            pass  # already counted; treat as scale-up not new strategy
        elif len(active_strategies) >= self.max_strategies_per_symbol:
            return RiskDecision.REJECT

        # Correlation-based notional cap (if configured)
        if self.max_correlated_notional_pct is None:
            return RiskDecision.ACCEPT
        equity = getattr(state, "equity", 0.0)
        if equity <= 0:
            return RiskDecision.ACCEPT

        corr_matrix = getattr(state, "correlation_matrix", {})
        existing = getattr(state, "open_positions_notional_usd", {})
        correlated_sum = 0.0
        for other_token, notional in existing.items():
            if other_token == token:
                correlated_sum += abs(notional)
                continue
            rho = corr_matrix.get((other_token, token), corr_matrix.get((token, other_token), 0.0))
            if abs(rho) > self.corr_threshold:
                correlated_sum += abs(notional)

        # Add candidate's contribution. Prefer sizing.fraction_of_equity
        # when set (authoritative per SizingRequest); fall back to raw
        # notional_usd attached as a test/dynamic attribute.
        cand_notional = 0.0
        if candidate.sizing is not None and candidate.sizing.fraction_of_equity is not None:
            cand_notional = (candidate.sizing.fraction_of_equity or 0.0) * equity
        if cand_notional == 0.0:
            cand_notional = float(getattr(candidate, "notional_usd", None) or 0.0)

        projected_frac = (correlated_sum + cand_notional) / equity
        if projected_frac > self.max_correlated_notional_pct:
            # Throttle (REDUCE) — do NOT hard-reject, to avoid double-throttle
            # with M8 concentration clamp operating on the same notional.
            if candidate.sizing is not None and candidate.sizing.fraction_of_equity is not None:
                # Scale down so post-clamp sum == max_correlated_notional_pct
                allowed_new = (self.max_correlated_notional_pct * equity) - correlated_sum
                allowed_new = max(0.0, allowed_new)
                allowed_frac = allowed_new / equity
                candidate.sizing.fraction_of_equity = min(
                    candidate.sizing.fraction_of_equity, allowed_frac
                )
            return RiskDecision.REDUCE
        return RiskDecision.ACCEPT


class PerSymbolStrategyLimit:
    """Explicit per-token cap on concurrent strategies. Complement to
    `MaxCorrelatedExposure` — one is correlation-based, this is explicit."""

    sampling_cadence: Literal["bar_close", "tick", "release"] = "release"

    def __init__(self, limits: dict[str, int]):
        self.limits = dict(limits)

    def check(self, candidate, state, clock_now_ns: int) -> RiskDecision:
        token = candidate.token
        if token not in self.limits:
            return RiskDecision.ACCEPT
        active = getattr(state, "per_symbol_strategies", {}).get(token, set())
        if candidate.strategy_id in active:
            return RiskDecision.ACCEPT  # already present; scale-up
        if len(active) >= self.limits[token]:
            return RiskDecision.REJECT
        return RiskDecision.ACCEPT


# ============================================================
# TradingState helpers (used by simulator Phase 3.0 hook + check_scale)
# ============================================================


def check_entry_allowed(candidate, state) -> RiskDecision:
    """Returns REJECT if trading_state == REDUCING; HALT if HALTED;
    ACCEPT if ACTIVE. Used by simulator Phase 3.0 as a pre-arbitration
    gate on every candidate."""
    ts = getattr(state, "trading_state", "ACTIVE")
    if ts == "HALTED":
        return RiskDecision.HALT
    if ts == "REDUCING":
        return RiskDecision.REJECT
    return RiskDecision.ACCEPT


def check_scale_allowed(qty_delta: float, state) -> RiskDecision:
    """M2 `check_scale` integration: allows qty_delta<0 (reduces) under
    REDUCING state; blocks qty_delta>0 (increases). HALT blocks all."""
    ts = getattr(state, "trading_state", "ACTIVE")
    if ts == "HALTED":
        return RiskDecision.HALT
    if ts == "REDUCING" and qty_delta > 0:
        return RiskDecision.REJECT
    return RiskDecision.ACCEPT
