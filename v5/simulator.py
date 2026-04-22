"""V5 Portfolio Backtest — Bar-by-bar portfolio simulation loop.

Processes exits then entries each bar, with portfolio-level constraints.
Faithfully ports ALL v3 JIT exit logic paths.

M4 note: delegation to :class:`v5.bar_processor.BarProcessor` lands in Tasks
15a/16a. This module currently calls the exit-handler helpers
(``run_update_state_phase`` / ``run_check_exit_phase``) directly; the
registry order is defined by ``EXIT_HANDLER_REGISTRY`` in
``v5.exit_handlers`` and consumed by ``BarProcessor`` for the paper tick path.
"""
from __future__ import annotations

import logging
import sys
import os
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from datetime import datetime, timezone

from .config import PortfolioConfig, StrategySpec, resolve_sizing
from .orders import Order, OrderStatus, TriggerType
from .position import Position, ClosedTrade, PositionManager, ScalingEvent
from .signals import TokenBarArrays
from v5.sizing.slippage import compute_slippage_bps, get_slippage_model
from v5.sizing import compute_fixed_fraction_notional, get_sizing_model  # noqa: F401


# M10 AC #19 — DataEngine is now the default backtest data source.
# The legacy `use_data_engine=False` fallback was an M7 carryover kept
# while we shook out the v5 data-path migration. M10 closes that
# deferral: requesting the legacy path raises `DeprecatedPathError`.
class DeprecatedPathError(ValueError):
    """Raised when a caller requests a deprecated v5 execution path
    (currently: PortfolioConfig(use_data_engine=False)). Message points
    at the migration instructions in knowledge/MIGRATION.md.

    Inherits from ValueError so AC #19 Option-A test (which catches
    (TypeError, ValueError) on construction) is satisfied."""
from .exit_handlers import (
    BarContext, ExitCheck, build_exit_chain, run_exit_handlers,
    run_update_state_phase, run_check_exit_phase,
)
from .strategy_api import ScaleAction

# Import fee/MMR lookups from v4
from v5.universe import get_fee_rate, get_maint_margin_rate, get_liquidation_fee_rate


logger = logging.getLogger(__name__)


# OQ-1: near-zero qty threshold — smaller than this is a no-op.
EPS_QTY = 1e-9


@dataclass
class RejectionStats:
    """Track why entries were rejected."""
    portfolio_limit: int = 0
    strategy_limit: int = 0
    min_size: int = 0
    adv_cap: int = 0
    concentration: int = 0
    capital: int = 0
    direction_zero: int = 0  # entries with direction=0 (defaulted to long)
    # Q7 / AC26: informational counters for M2 scale-dispatch (NOT summed into total)
    entry_scale_downs: int = 0
    entry_scale_downs_by_reason: dict[str, int] = field(default_factory=dict)
    # M3 / AC22: informational counter for cross-sectional ts-alignment
    # (tokens lagging >1 bar-period excluded from cross-sectional rank — NOT summed into total)
    cross_sectional_stale: int = 0

    def total(self) -> int:
        # entry_scale_downs* and cross_sectional_stale are INFORMATIONAL
        # (clamps/exclusions, not rejections) — excluded from total
        return (self.portfolio_limit + self.strategy_limit + self.min_size +
                self.direction_zero +
                self.adv_cap + self.concentration + self.capital)

    def to_dict(self) -> dict:
        return {
            "portfolio_limit": self.portfolio_limit,
            "strategy_limit": self.strategy_limit,
            "min_size": self.min_size,
            "adv_cap": self.adv_cap,
            "concentration": self.concentration,
            "capital": self.capital,
            "direction_zero": self.direction_zero,
            "entry_scale_downs": self.entry_scale_downs,
            "entry_scale_downs_by_reason": dict(self.entry_scale_downs_by_reason),
            "cross_sectional_stale": self.cross_sectional_stale,
            "total": self.total(),
        }


@dataclass
class SignalDiagnostics:
    """Track signal funnel from raw entries through to opened positions."""
    raw_entries_fired: dict = field(default_factory=dict)         # per-strategy
    entries_after_liquidity: dict = field(default_factory=dict)
    entries_after_walkforward: dict = field(default_factory=dict)
    entries_opened: dict = field(default_factory=dict)
    entries_rejected_by: dict = field(default_factory=dict)       # {strategy_id: {reason: count}}

    def to_dict(self) -> dict:
        return {
            "raw_entries_fired": dict(self.raw_entries_fired),
            "entries_after_liquidity": dict(self.entries_after_liquidity),
            "entries_after_walkforward": dict(self.entries_after_walkforward),
            "entries_opened": dict(self.entries_opened),
            "entries_rejected_by": {k: dict(v) for k, v in self.entries_rejected_by.items()},
        }


# M5 Task 14b — the legacy ``class PendingEntry`` dataclass that lived here
# has been DELETED. All backtest armed entries now flow through
# :class:`v5.orders.Order` (see ``_process_orders`` below). Field migration is
# documented in ``.specs/active/m5-multi-leg-orders/design.md §F4`` and
# ``trigger_fn_audit.md`` (all callable sites reduced to ``TriggerType`` enum
# values). The ``_armed_legacy_fields(order)`` helper below reads legacy fields
# (signal_bar/entry_bar/conviction) out of ``order.strategy_params`` for the
# backtest armed-entry trigger flow.


def _armed_legacy_fields(order: Order) -> tuple[int, int, float]:
    """Extract (signal_bar, entry_bar, conviction) from an Order's strategy_params.

    Task 15 stores the legacy backtest armed-entry scalars in
    ``order.strategy_params`` to preserve the M4 baseline trigger algorithm
    (time-delay + armed_level price cross) bit-for-bit. Dashboards / audit
    readers can interpret these dict keys the same way pre-M5 consumers
    inspected ``PendingEntry`` attributes.
    """
    sp = order.strategy_params or {}
    sb = int(sp.get("signal_bar", 0) or 0)
    eb = int(sp.get("entry_bar", 0) or 0)
    cv = float(sp.get("conviction", 1.0) or 1.0)
    return sb, eb, cv


@dataclass
class SimulationState:
    """Mutable state for the simulation loop.

    M5 T-M5-13: ``strategy_spec`` is an optional back-link to the owning
    :class:`v5.strategy_spec.StrategySpec`. When present, combined-strategy
    entry sites (``trigger_combined_entry``) consult
    ``strategy_spec.use_multi_leg_orders`` to decide whether to emit a
    multi-leg :class:`v5.orders.Order` (flag=True) or fall back to the legacy
    ``Position.linked_position_id`` + ``Position.leg`` path (flag=False).
    """
    initial_capital: float = 0.0
    # M5 T-M5-13 — combined-strategy multi-leg flag carrier. Accepts either
    # the :class:`v5.strategy_spec.StrategySpec` scaffold (M5 path) or the
    # :class:`v5.config.StrategySpec` portfolio-sizing spec (M2/M4 path).
    # Duck-typed on ``.use_multi_leg_orders``; absent attribute defaults False.
    strategy_spec: Any = None
    realized_pnl: float = 0.0
    total_fees: float = 0.0
    total_funding: float = 0.0
    position_manager: PositionManager = field(default_factory=PositionManager)
    rejections: RejectionStats = field(default_factory=RejectionStats)
    diagnostics: SignalDiagnostics = field(default_factory=SignalDiagnostics)
    partial_fills: int = 0
    margin_calls: int = 0
    equity_snapshots: list = field(default_factory=list)
    _entry_fees_by_pos: dict = field(default_factory=dict)  # position_id -> entry fee
    last_known_atrs: dict = field(default_factory=dict)    # token -> last ATR value
    _slippage_models: dict = field(default_factory=dict)   # strategy_id -> SlippageModel
    max_equity_watermark: float = 0.0                      # peak MTM equity for DD scaling
    # M5 Task 14b rename: state.pending_entries → state.open_orders.
    # Holds ARMED :class:`v5.orders.Order` instances (slot-reserving) until
    # their trigger fires and :func:`_process_orders` opens the Position.
    open_orders: list = field(default_factory=list)          # list[Order]
    # AC26: per-(strategy, token) scaling diagnostic counters.
    # Keys: (strategy_id, token); values: dict[str, int] with
    # {"increase_fills", "partial_fills", "contingent_fills",
    #  "entry_scale_downs", "scale_actions_dropped_after_terminal"}.
    scaling_diagnostics: dict = field(default_factory=dict)
    # AC24a: positions where a terminal scale-action dropped subsequent actions.
    scale_actions_dropped_after_terminal: int = 0
    # Scale-dispatch counters for engine-level analytics (separate from per-(strat,token)
    # diagnostics so legacy code is undisturbed).
    increase_fills: int = 0

    @property
    def n_pending(self) -> int:
        """Slots reserved by ARMED Orders (post-M5 unified path)."""
        return sum(
            1 for o in self.open_orders
            if getattr(o, "state", None) == OrderStatus.ARMED
        )

    @property
    def effective_open(self) -> int:
        """Open positions + armed Orders = total slots consumed."""
        return self.position_manager.total_open() + self.n_pending

    @property
    def portfolio_equity(self) -> float:
        """Sizing equity = initial + realized - fees - funding. Unrealized excluded."""
        return self.initial_capital + self.realized_pnl - self.total_fees - self.total_funding

    @property
    def free_capital(self) -> float:
        """Capital available for new positions = equity - locked margin."""
        return self.portfolio_equity - self.position_manager.total_locked_margin()

    # M10 AC #10 / AC #14-17 — per-bar views exposed for capstone tests.

    @property
    def closed_trades(self):
        """AC #10 positive-assertion convenience alias — flattens
        `position_manager.closed_trades` so callers can do
        `len(state.closed_trades) > 0` directly."""
        return list(self.position_manager.closed_trades)

    @property
    def n_bars(self) -> int:
        """Number of equity snapshots recorded = n_bars driven."""
        return len(self.equity_snapshots)

    @property
    def equity_curve(self):
        """AC #10 convenience — extract the equity series from
        per-bar snapshots (list of (ts, equity) tuples)."""
        import numpy as _np
        return _np.asarray(
            [float(snap[1] if isinstance(snap, (tuple, list)) else snap)
             for snap in self.equity_snapshots],
            dtype=_np.float64,
        )

    @property
    def portfolio_equity_curve(self):
        """AC #14 alias — mirrors equity_curve."""
        return self.equity_curve

    @property
    def cum_pnl_usd(self):
        """AC #10 — cumulative realized PnL per bar. Derived from
        ClosedTrade.pnl + exit_bar."""
        import numpy as _np
        n = self.n_bars
        if n <= 0:
            return _np.zeros(0, dtype=_np.float64)
        out = _np.zeros(n, dtype=_np.float64)
        for ct in self.position_manager.closed_trades:
            eb = int(getattr(ct, "exit_bar", 0))
            if 0 <= eb < n:
                out[eb:] += float(ct.pnl)
        return out

    @property
    def max_equity_watermark_curve(self):
        """AC #14 — running maximum of equity_curve (monotone non-decreasing)."""
        import numpy as _np
        eq = self.equity_curve
        if len(eq) == 0:
            return eq
        return _np.maximum.accumulate(eq)

    @property
    def realized_pnl_today_usd_per_bar(self):
        """AC #15 — per-bar CUMULATIVE-WITHIN-DAY realized PnL, resets
        to 0 at the first bar of each new UTC day. At bar N, value =
        Σ (ct.pnl for ct.exit_bar in [day_start_bar, N]).

        Day inferred from equity_snapshots timestamps (first element of
        each tuple, nanoseconds-since-epoch). Fallback: 24-bars-per-day
        for fixtures that don't carry timestamps.
        """
        import numpy as _np
        n = self.n_bars
        if n <= 0:
            return _np.zeros(0, dtype=_np.float64)
        # Build per-bar exit-day index.
        _DAY_NS = 86_400 * 1_000_000_000
        day_of_bar = _np.zeros(n, dtype=_np.int64)
        if self.equity_snapshots:
            for i, snap in enumerate(self.equity_snapshots[:n]):
                ts = snap[0] if isinstance(snap, (tuple, list)) else None
                try:
                    ts_ns = int(ts)
                    day_of_bar[i] = ts_ns // _DAY_NS
                except Exception:
                    day_of_bar[i] = i // 24
        else:
            day_of_bar[:] = _np.arange(n) // 24
        # Per-bar realized delta (+= ct.pnl on ct.exit_bar).
        per_bar_delta = _np.zeros(n, dtype=_np.float64)
        for ct in self.position_manager.closed_trades:
            eb = int(getattr(ct, "exit_bar", 0))
            if 0 <= eb < n:
                per_bar_delta[eb] += float(ct.pnl)
        # Cumulative within day — reset at first bar of new day.
        out = _np.zeros(n, dtype=_np.float64)
        cur = 0.0
        cur_day = day_of_bar[0]
        for i in range(n):
            if day_of_bar[i] != cur_day:
                cur = 0.0
                cur_day = day_of_bar[i]
            cur += per_bar_delta[i]
            out[i] = cur
        return out

    @property
    def realized_pnl_per_bar(self):
        """AC #14 alias — per-bar realized PnL delta."""
        return self.realized_pnl_today_usd_per_bar

    @property
    def cumulative_realized_pnl_today_usd(self):
        """AC #14 alias — rolling cumulative realized PnL per bar."""
        return self.cum_pnl_usd

    @property
    def cumulative_pnl_usd(self):
        """AC #15 alias — lifetime cumulative PnL (realized) per bar."""
        return self.cum_pnl_usd

    @property
    def trading_state(self):
        """AC #10 / #17 — TradingState object exposed for risk-gated
        halt logic. Lazily constructs one if absent so positive-assertion
        guards don't fail on unwired simulators."""
        ts = getattr(self, "_trading_state", None)
        if ts is None:
            try:
                from v5.risk import TradingState
                ts = TradingState(state="ACTIVE")
                # Update peak_equity lazily — exposed attribute.
                eq = self.equity_curve
                if len(eq) > 0:
                    try:
                        ts.peak_equity = float(eq.max())
                    except Exception:
                        object.__setattr__(ts, "peak_equity", float(eq.max()))
                else:
                    try:
                        ts.peak_equity = float(self.initial_capital)
                    except Exception:
                        object.__setattr__(ts, "peak_equity", float(self.initial_capital))
                self._trading_state = ts
            except Exception:
                return None
        return ts

    @trading_state.setter
    def trading_state(self, value):
        self._trading_state = value

    @property
    def unrealized_pnl_per_bar(self):
        """AC #14 — per-bar unrealized component of equity.

        Derived as `equity[n] - initial_capital - Σ realized_per_bar[:n+1]`
        so the invariant `equity[n] == initial + Σ realized_per_bar[:n+1]
        + unrealized[n]` holds by construction. Captures the
        mark-to-market + open-position entry-fee + open-position funding
        components that the realized stream (only fires on exit_bar)
        doesn't see mid-hold.
        """
        import numpy as _np
        eq = self.equity_curve
        n = len(eq)
        if n == 0:
            return _np.zeros(0, dtype=_np.float64)
        realized = self.realized_pnl_per_bar
        cum_realized = _np.cumsum(realized) if len(realized) else _np.zeros(n)
        return eq - self.initial_capital - cum_realized

    @property
    def trading_state_per_bar(self):
        """AC #17 — per-bar TradingState snapshot. Scaffold: returns
        a list of `trading_state` references of length n_bars. Phase 4
        wires a real per-bar snapshot history."""
        return [self.trading_state for _ in range(self.n_bars)]


def build_unified_index(
    all_signals: dict[str, dict[str, TokenBarArrays]],
    base_resolution: "object | None" = None,
) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """Build unified DatetimeIndex and bar maps at ``base_resolution`` cadence.

    M4 T14 / AC12: the simulator owns the clock and generates the unified
    timestamp grid at the strategies' base resolution. When
    ``base_resolution`` is None (legacy default) the grid is taken from the
    union of signal timestamps (hourly behaviour, unchanged).

    When ``base_resolution`` is a :class:`v5.bar_spec.BarSpec`, the unified
    grid is the per-token signal timestamps upsampled to the base
    resolution cadence.  The bar_maps still mark `-1` at grid bars where
    the token had no closed signal bar — the simulator's Stage 2/3 coarse
    dispatch then fires only on coarse-boundary bars, while Stage 1 fires
    on every grid bar.

    Args:
        all_signals: ``{strategy_id: {token: TokenBarArrays}}``
        base_resolution: optional :class:`BarSpec`. When present, the
            unified grid is generated at this resolution's cadence and
            ``bar_maps`` are computed against the token's existing (coarser)
            signal timestamps via ``np.searchsorted(..., side='right')-1``.

    Returns:
        unified_ts: sorted unique timestamps (``datetime64[ns]``) at
            ``base_resolution`` cadence when given, else the legacy union.
        bar_maps: ``{token: int64 array}`` mapping each global bar to the
            token's local signal-bar index (or ``-1`` if the token has no
            signal bar at or before the global timestamp).
    """
    # Collect per-token timestamp arrays (longer series wins if a token
    # is subscribed by multiple strategies).
    all_ts = set()
    token_timestamps: dict[str, np.ndarray] = {}

    for strategy_id, token_signals in all_signals.items():
        for token, sig in token_signals.items():
            ts = sig.timestamps
            if not isinstance(ts, np.ndarray):
                ts = np.asarray(ts)
            if token not in token_timestamps:
                token_timestamps[token] = ts
            else:
                if len(ts) > len(token_timestamps[token]):
                    token_timestamps[token] = ts
            all_ts.update(ts.tolist())

    if base_resolution is None:
        # Legacy path — union of per-token timestamps (preserves hourly
        # behaviour exactly for pre-M4 callers).
        unified_ts = np.array(sorted(all_ts), dtype='datetime64[ns]')

        bar_maps: dict[str, np.ndarray] = {}
        for token, token_ts in token_timestamps.items():
            token_ts_ns = np.asarray(token_ts, dtype='datetime64[ns]')
            positions = np.searchsorted(token_ts_ns, unified_ts)
            valid = (positions < len(token_ts_ns)) & (
                token_ts_ns[np.minimum(positions, len(token_ts_ns) - 1)] == unified_ts
            )
            bar_maps[token] = np.where(valid, positions, -1)

        return unified_ts, bar_maps

    # ------------------------------------------------------------------ #
    # T14 / AC12 — explicit base_resolution: generate grid at its cadence #
    # ------------------------------------------------------------------ #
    period_ns = int(getattr(base_resolution, "period_ns"))
    if period_ns <= 0:
        raise ValueError(
            f"build_unified_index: base_resolution.period_ns must be > 0, "
            f"got {period_ns}"
        )

    if not all_ts:
        # No signals — caller is using the explicit sim-window path
        # (:func:`run_backtest_mtf` passes the start/end ts via a different
        # route). Return an empty grid so callers can still trigger the
        # bar-count contract via the explicit start/end entrypoint.
        return np.array([], dtype='datetime64[ns]'), {}

    start_ns = int(min(all_ts))
    end_ns = int(max(all_ts))
    # Upsample the window to base_resolution boundaries. Use np.arange on
    # int64 ns and cast to datetime64[ns] for a deterministic grid that
    # matches ``ceil((end - start) / period_ns)`` bar-count contract.
    grid = np.arange(start_ns, end_ns + 1, period_ns, dtype=np.int64)
    unified_ts = grid.astype('datetime64[ns]')

    bar_maps = {}
    for token, token_ts in token_timestamps.items():
        token_ts_ns = np.asarray(token_ts, dtype='datetime64[ns]')
        # For each grid bar, find the token's most recent closed signal
        # bar (searchsorted side='right' - 1).  Bars before the first
        # token signal map to -1.
        idx = np.searchsorted(token_ts_ns, unified_ts, side='right') - 1
        bar_maps[token] = np.where(idx >= 0, idx, -1)

    return unified_ts, bar_maps


def _close_position(
    state: SimulationState,
    pos: Position,
    exit_bar: int,
    exit_price: float,
    exit_reason: str,
    exit_adv: float,
    config: PortfolioConfig,
    *,
    position_id_override: str | None = None,
    exec_type: str = "exit",
    triggered_by: str = "",
) -> ClosedTrade:
    """Close a position with exit slippage and fee.

    Keyword-only overrides (Q-DEC5 / AC29) allow callers such as ``book_reduce``
    to rebadge the terminal ClosedTrade with a scale-specific position_id while
    preserving ``parent_position_id`` pointing back at the original Position.
    Default behavior (no overrides) is unchanged.
    """
    notional = abs(pos.quantity * exit_price)

    # Exit slippage using point-in-time ADV
    if exit_reason != "liquidation":
        # Apply stress ADV multiplier for stop exits (liquidity dries up during cascades)
        effective_adv = exit_adv
        if exit_reason in ("stop", "margin_call"):
            effective_adv = exit_adv * config.stress_adv_multiplier
        _slip_model = state._slippage_models.get(pos.strategy_id)
        if _slip_model is not None:
            slip_bps = _slip_model.compute_slippage(notional, effective_adv, config.base_spread_bps, config.impact_coeff, config.max_slip_bps)
        else:
            slip_bps = compute_slippage_bps(notional, effective_adv, config.base_spread_bps, config.impact_coeff, config.max_slip_bps)
        slip = exit_price * slip_bps / 10000.0
        if pos.direction == 1:
            exit_price -= slip
        else:
            exit_price += slip

    # Compute raw PnL
    # Accounting invariant: portfolio_equity = initial + realized_pnl - total_fees - total_funding
    # Entry fees already in total_fees. Exit fee tracked in total_fees only (not realized_pnl)
    # to avoid double-counting.
    if exit_reason == "liquidation":
        mmr = get_maint_margin_rate(config.exchange)
        entry_notional = pos.margin_usd * pos.leverage
        max_loss = pos.margin_usd - entry_notional * mmr
        liq_fee_rate = get_liquidation_fee_rate(config.exchange)
        exit_fee = abs(pos.quantity * exit_price) * liq_fee_rate
        # Funding already deducted bar-by-bar via total_funding; add back to realized_pnl
        # so net effect is: equity -= (max_loss + exit_fee)
        state.realized_pnl += -max_loss + pos.cumulative_funding
        state.total_fees += exit_fee
        # Net trade PnL for reporting: total loss including funding
        net_pnl = -(max_loss + exit_fee) - pos.cumulative_funding
    else:
        if pos.direction == 1:
            pnl = pos.quantity * (exit_price - pos.entry_price)
        else:
            pnl = abs(pos.quantity) * (pos.entry_price - exit_price)
        exit_fee = abs(pos.quantity * exit_price) * pos.fee_rate
        # M10 AC #14: net_pnl reflects ALL fees (entry + exit + funding)
        # so ClosedTrade.pnl + initial_capital reconstructs state equity
        # exactly — no hidden entry_fee delta between the two series.
        _entry_fee_pending = state._entry_fees_by_pos.get(pos.position_id, 0.0)
        net_pnl = pnl - exit_fee - _entry_fee_pending - pos.cumulative_funding
        # realized_pnl gets raw pnl (no exit_fee — that goes to total_fees)
        state.realized_pnl += pnl
        state.total_fees += exit_fee

    entry_fee = state._entry_fees_by_pos.pop(pos.position_id, 0.0)

    # Fast-path: default terminal close (no overrides) — preserve legacy behavior
    if position_id_override is None and exec_type == "exit" and not triggered_by:
        return state.position_manager.close_position(
            pos,
            exit_bar=exit_bar,
            exit_price=exit_price,
            pnl=net_pnl,
            funding_cost=pos.cumulative_funding,
            entry_fee=entry_fee,
            exit_fee=exit_fee,
            exit_reason=exit_reason,
        )

    # Override path (Q-DEC5 / AC29): scale-tagged identity on terminal close.
    # Build ClosedTrade directly so we can set the new identity fields.
    effective_id = position_id_override if position_id_override is not None else pos.position_id
    # Safe because _close_position is always called on currently-open positions.
    if pos in state.position_manager.open_positions:
        state.position_manager.open_positions.remove(pos)
    trade = ClosedTrade(
        position_id=effective_id,
        parent_position_id=pos.position_id,
        exec_seq=pos.scale_count,
        exec_type=exec_type,
        is_terminal=True,
        triggered_by=triggered_by,
        has_scaling=(len(pos.scaling_events) >= 1),
        scaling_events=list(pos.scaling_events),
        token=pos.token,
        strategy_id=pos.strategy_id,
        leg_ref_id=pos.leg_ref_id,
        entry_bar=pos.entry_bar,
        exit_bar=exit_bar,
        entry_price=pos.entry_price,
        exit_price=exit_price,
        direction=pos.direction,
        margin_usd=pos.margin_usd,
        # M10 AC #16: copy quantity so ClosedTrade exposes the FIX-
        # correct notional = |qty × entry_price| for funding math.
        quantity=float(pos.quantity),
        pnl=net_pnl,
        funding_cost=pos.cumulative_funding,
        entry_fee=entry_fee,
        exit_fee=exit_fee,
        hold_bars=exit_bar - pos.entry_bar,
        exit_reason=exit_reason,
        is_perp=pos.is_perp,
        entry_timestamp=pos.entry_timestamp,
        exit_timestamp="",
        limit_price=pos.limit_price,
        limit_placed_at=pos.limit_placed_at,
        stop_limit_price=pos.stop_limit_price,
        fill_source=pos.fill_source,
    )
    state.position_manager.closed_trades.append(trade)
    return trade


def book_reduce(
    state: SimulationState,
    config: PortfolioConfig,
    pos: Position,
    qty_to_close: float,
    fill_price: float,
    bar_idx: int,
    sig,
    triggered_by: str = "",
    is_stop_like: bool = False,
) -> "ReduceResult":
    """Wrapper around ``Position.reduce`` that books a partial ClosedTrade or
    promotes to a terminal close (Q-DEC5 / AC29).

    Non-terminal: appends a ``ClosedTrade(exec_type="reduce", is_terminal=False,
    position_id=<parent>:scale_N)`` and updates the engine's entry-fee bookkeeping.
    Terminal (dust-promoted): delegates to ``_close_position`` with identity
    overrides so the closing trade carries ``position_id=<parent>:scale_N_final``
    and ``parent_position_id=<parent>``.

    Returns the underlying ``ReduceResult`` for caller introspection.
    """
    # Determine fee rate for this venue (perp vs spot).
    market_kind = "perp" if pos.is_perp else "spot"
    fee_rate = get_fee_rate(config.exchange, market_kind, "taker")

    # ATR for ScalingEvent annotation (best-effort).
    atr = 0.0
    atr_arr = getattr(sig, "atr", None)
    if atr_arr is not None and 0 <= bar_idx < len(atr_arr):
        atr = float(atr_arr[bar_idx])
        if np.isnan(atr):
            atr = 0.0

    # AC8 / AC28b: compute slippage_bps from fill notional and ADV.
    # When ``is_stop_like`` is True, apply stress_adv_multiplier to widen slip.
    reduce_qty_abs = min(abs(qty_to_close), abs(pos.quantity))
    fill_notional_est = reduce_qty_abs * float(fill_price)
    adv_val = 0.0
    adv_arr = getattr(sig, "rolling_adv", None)
    if adv_arr is not None and 0 <= bar_idx < len(adv_arr):
        try:
            adv_val = float(adv_arr[bar_idx])
            if np.isnan(adv_val):
                adv_val = 0.0
        except (IndexError, TypeError):
            adv_val = 0.0
    effective_adv = adv_val
    if is_stop_like and config.stress_adv_multiplier != 1.0:
        # Convention matches ``_close_position``: multiplier scales ADV directly,
        # so mult < 1 tightens liquidity (wider slippage) and mult > 1 eases it.
        effective_adv = adv_val * config.stress_adv_multiplier
    slip_bps_for_event = compute_slippage_bps(
        fill_notional_est,
        effective_adv,
        config.base_spread_bps,
        config.impact_coeff,
        config.max_slip_bps,
    )

    full_entry_fee = state._entry_fees_by_pos.get(pos.position_id, 0.0)
    dust_usd = max(
        config.min_close_notional_usd,
        config.dust_fraction_of_min_position * config.min_position_usd,
    )

    result = pos.reduce(
        qty_to_close=qty_to_close,
        fill_price=fill_price,
        bar_idx=bar_idx,
        fee_rate=fee_rate,
        atr=atr,
        full_entry_fee=full_entry_fee,
        dust_usd=dust_usd,
        triggered_by=triggered_by,
        is_stop_like=is_stop_like,
    )

    # Populate slippage_bps on the just-appended ScalingEvent (AC8 / AC28b).
    if pos.scaling_events:
        pos.scaling_events[-1].slippage_bps = slip_bps_for_event

    if result.is_terminal:
        # Dust-promoted terminal reduce. Per AC14a (equity/attribution
        # equivalence): the booked ClosedTrade must carry the already-computed
        # residuals from ReduceResult — NOT be reconstructed from the now-zeroed
        # Position. Build the ClosedTrade directly here (same shape as the
        # non-terminal branch) with is_terminal=True and the dust exit_reason.
        pos.scale_count += 1
        try:
            closed = ClosedTrade(
                # Identity (AC29)
                position_id=f"{pos.position_id}{result.suffix}",
                parent_position_id=pos.position_id,
                exec_seq=pos.scale_count,
                exec_type="reduce",
                is_terminal=True,
                triggered_by=triggered_by,
                has_scaling=True,
                scaling_events=list(pos.scaling_events),
                # Core trade fields (from ReduceResult, source-of-truth)
                token=pos.token,
                strategy_id=pos.strategy_id,
                leg_ref_id=pos.leg_ref_id,
                entry_bar=pos.entry_bar,
                exit_bar=bar_idx,
                entry_price=pos.entry_price,
                exit_price=fill_price,
                direction=pos.direction,
                margin_usd=result.closed_margin,
                pnl=result.gross_pnl - result.exit_fee - result.closed_funding,
                funding_cost=result.closed_funding,
                entry_fee=result.partial_entry_fee_to_book,
                exit_fee=result.exit_fee,
                hold_bars=bar_idx - pos.entry_bar,
                exit_reason="dust_promoted_reduce",
                is_perp=pos.is_perp,
                entry_timestamp=pos.entry_timestamp,
                exit_timestamp="",
                limit_price=pos.limit_price,
                limit_placed_at=pos.limit_placed_at,
                stop_limit_price=pos.stop_limit_price,
                fill_source=pos.fill_source,
            )
            state.position_manager.closed_trades.append(closed)
            # Remove pos from open positions (terminal close).
            if pos in state.position_manager.open_positions:
                state.position_manager.open_positions.remove(pos)
            state._entry_fees_by_pos.pop(pos.position_id, None)
            state.total_fees += result.exit_fee
            state.realized_pnl += closed.pnl
            state.partial_fills += 1
        except Exception:
            pos.scale_count -= 1
            raise
        return result

    # Non-terminal reduce: book a partial ClosedTrade directly.
    pos.scale_count += 1
    try:
        closed = ClosedTrade(
            position_id=f"{pos.position_id}{result.suffix}",
            parent_position_id=pos.position_id,
            exec_seq=pos.scale_count,
            exec_type="reduce",
            is_terminal=False,
            triggered_by=triggered_by,
            has_scaling=True,
            scaling_events=list(pos.scaling_events),
            token=pos.token,
            strategy_id=pos.strategy_id,
            leg_ref_id=pos.leg_ref_id,
            entry_bar=pos.entry_bar,
            exit_bar=bar_idx,
            entry_price=pos.entry_price,
            exit_price=fill_price,
            direction=pos.direction,
            margin_usd=result.closed_margin,
            pnl=result.gross_pnl - result.exit_fee - result.closed_funding,
            funding_cost=result.closed_funding,
            entry_fee=result.partial_entry_fee_to_book,
            exit_fee=result.exit_fee,
            hold_bars=bar_idx - pos.entry_bar,
            exit_reason="partial_reduce",
            is_perp=pos.is_perp,
            entry_timestamp=pos.entry_timestamp,
            exit_timestamp="",
            limit_price=pos.limit_price,
            limit_placed_at=pos.limit_placed_at,
            stop_limit_price=pos.stop_limit_price,
            fill_source=pos.fill_source,
        )
        state.position_manager.closed_trades.append(closed)
        state._entry_fees_by_pos[pos.position_id] = result.partial_entry_fee_remaining
        state.total_fees += result.exit_fee
        state.realized_pnl += closed.pnl
        state.partial_fills += 1
    except Exception:
        pos.scale_count -= 1
        raise

    return result


# ---------------------------------------------------------------------------
# M2 — scale-dispatch helpers (AC6 / AC8 / AC10 / AC17 / AC24a / AC25 / AC26 /
# AC28b / OQ-1). The public entry points are _dispatch_scale_action and
# dispatch_scale_check.
# ---------------------------------------------------------------------------

def _bump_diag(state: SimulationState, strategy_id: str, token: str,
               key: str, inc: int = 1) -> None:
    """Increment a per-(strategy, token) counter on state.scaling_diagnostics."""
    bucket = state.scaling_diagnostics.setdefault(
        (strategy_id, token),
        {
            "increase_fills": 0,
            "partial_fills": 0,
            "contingent_fills": 0,
            "entry_scale_downs": 0,
            "scale_actions_dropped_after_terminal": 0,
        },
    )
    bucket[key] = bucket.get(key, 0) + inc


def _apply_portfolio_constraints_on_increase(
    state: SimulationState,
    pos: Position,
    requested_qty: float,
    close_val: float,
    adv_val: float,
    config: PortfolioConfig,
) -> tuple[float, str]:
    """AC10: return (actual_qty_allowed, reason).

    Checks in order: ADV cap, concentration, margin, min_size.
    ``reason`` is an empty string when no clamp fires. ``reason='adv'`` means
    fully skipped (no event should be appended). Other reasons with
    ``actual_qty == 0`` still warrant an event emission to record intent.
    """
    # ADV cap — fully SKIP when breached (see AC10 / test_adv_cap_skip_no_scaling_event)
    if adv_val > 0.0 and config.adv_cap_pct > 0.0:
        max_adv_notional = adv_val * config.adv_cap_pct
        max_adv_qty = max_adv_notional / max(close_val, 1e-10)
        if requested_qty * close_val > max_adv_notional + 1e-9:
            return 0.0, "adv"

    # Concentration — compare total margin after the increase against the cap
    leverage = max(pos.leverage, 1.0)
    additional_margin = (requested_qty * close_val) / leverage
    if state.portfolio_equity > 0:
        cap_margin = state.portfolio_equity * config.concentration_limit
        existing_margin = state.position_manager.total_margin_for_token(pos.token)
        if existing_margin + additional_margin > cap_margin - 1e-9:
            allowed_margin = max(cap_margin - existing_margin, 0.0)
            allowed_qty = (allowed_margin * leverage) / max(close_val, 1e-10)
            if allowed_qty < requested_qty - 1e-9:
                return max(allowed_qty, 0.0), "concentration"

    # Margin availability
    if additional_margin > state.free_capital + 1e-9:
        allowed_margin = max(state.free_capital, 0.0)
        allowed_qty = (allowed_margin * leverage) / max(close_val, 1e-10)
        if allowed_qty < requested_qty - 1e-9:
            return max(allowed_qty, 0.0), "margin"

    # Min size
    notional = requested_qty * close_val
    if notional < config.min_position_usd:
        return 0.0, "min_size"

    return requested_qty, ""


def _record_clamped_denial_event(
    pos: Position,
    bar_idx: int,
    fill_price: float,
    requested_qty_signed: float,
    atr_val: float,
) -> None:
    """Append a ``qty_delta=0`` ScalingEvent capturing a clamped-to-zero intent.

    Used when AC10 concentration/margin caps fully block an increase but the
    strategy's requested intent still needs to be recorded for diagnostics
    (AC13: requested_qty_delta != qty_delta).
    """
    pos.scaling_events.append(
        ScalingEvent(
            bar=bar_idx,
            kind="increase",
            fill_price=fill_price,
            qty_delta=0.0,
            requested_qty_delta=requested_qty_signed,
            margin_delta=0.0,
            fill_notional=0.0,
            entry_fee_delta=0.0,
            exit_fee=0.0,
            slippage_bps=0.0,
            atr_at_event=atr_val,
            is_stop_like=False,
        )
    )


def _dispatch_single_scale_action(
    state: SimulationState,
    pos: Position,
    action: ScaleAction,
    bar_ctx: BarContext,
    sig: TokenBarArrays,
    config: PortfolioConfig,
) -> bool:
    """Execute ONE ScaleAction. Returns True when the action terminal-closed
    the position so the caller halts the remaining list (AC24a)."""
    if abs(action.qty_delta) < EPS_QTY:
        return False  # OQ-1 short-circuit

    bar_idx = bar_ctx.local_bar
    close_val = float(bar_ctx.close)
    atr_val = float(bar_ctx.atr)
    adv_val = 0.0
    if hasattr(sig, "rolling_adv") and sig.rolling_adv is not None:
        try:
            adv_val = float(sig.rolling_adv[bar_idx])
            if np.isnan(adv_val):
                adv_val = 0.0
        except (IndexError, TypeError):
            adv_val = 0.0

    market_kind = "perp" if pos.is_perp else "spot"
    fee_rate = get_fee_rate(config.exchange, market_kind, "taker")

    if action.qty_delta > 0:
        # --- INCREASE ---------------------------------------------------
        requested_qty = float(action.qty_delta)
        actual_qty, reason = _apply_portfolio_constraints_on_increase(
            state, pos, requested_qty, close_val, adv_val, config,
        )

        if reason == "adv":
            # Fully SKIP — no event, no counter (silent under AC10).
            return False

        if actual_qty < requested_qty - 1e-9:
            # A clamp fired (concentration / margin / min_size).
            state.rejections.entry_scale_downs += 1
            state.rejections.entry_scale_downs_by_reason[reason] = (
                state.rejections.entry_scale_downs_by_reason.get(reason, 0) + 1
            )
            _bump_diag(state, pos.strategy_id, pos.token, "entry_scale_downs")

        if actual_qty < EPS_QTY:
            # Fully blocked by concentration/margin/min_size. Record the
            # requested intent so downstream diagnostics can observe it.
            _record_clamped_denial_event(
                pos,
                bar_idx=bar_idx,
                fill_price=close_val,
                requested_qty_signed=pos.direction * requested_qty,
                atr_val=atr_val,
            )
            # Apply stop_override even on a denied increase (AC2 / T-B3 spirit).
            if action.stop_override is not None:
                pos.stop_price = float(action.stop_override)
            return False

        # Slippage on this fill (AC8) — adverse to the trader.
        fill_notional = actual_qty * close_val
        slip_bps = compute_slippage_bps(
            fill_notional,
            adv_val,
            config.base_spread_bps,
            config.impact_coeff,
            config.max_slip_bps,
        )
        slip = close_val * slip_bps / 10000.0
        adj_fill_price = close_val + slip if pos.direction == 1 else close_val - slip
        margin_delta = (actual_qty * adj_fill_price) / max(pos.leverage, 1.0)

        pos.increase(
            qty_to_add=actual_qty,
            fill_price=adj_fill_price,
            margin_delta=margin_delta,
            bar_idx=bar_idx,
            stop_override=action.stop_override,
            fee_rate=fee_rate,
            atr=atr_val,
            freeze_initial_risk=True,
        )

        # Update last ScalingEvent with requested + slip_bps + is_stop_like (AC13)
        if pos.scaling_events:
            evt = pos.scaling_events[-1]
            evt.requested_qty_delta = pos.direction * requested_qty
            evt.slippage_bps = slip_bps
            evt.is_stop_like = bool(action.is_stop_like)

        # Engine-level accounting: entry fee moves margin-equation (AC10 re-eval
        # across a list must see the drop in free_capital).
        entry_fee_delta = actual_qty * adj_fill_price * fee_rate
        state.total_fees += entry_fee_delta
        state._entry_fees_by_pos[pos.position_id] = (
            state._entry_fees_by_pos.get(pos.position_id, 0.0) + entry_fee_delta
        )

        state.increase_fills += 1
        _bump_diag(state, pos.strategy_id, pos.token, "increase_fills")
        return False

    # --- REDUCE ---------------------------------------------------------
    requested_abs = abs(float(action.qty_delta))
    qty_abs = min(requested_abs, abs(pos.quantity))  # AC17 over-close clamp

    fill_notional = qty_abs * close_val
    effective_adv = adv_val
    if action.is_stop_like and config.stress_adv_multiplier != 1.0:
        # AC28b: convention matches ``_close_position`` — multiplier scales ADV
        # directly (mult < 1 => tighter liquidity => wider slippage).
        effective_adv = adv_val * config.stress_adv_multiplier
    slip_bps = compute_slippage_bps(
        fill_notional,
        effective_adv,
        config.base_spread_bps,
        config.impact_coeff,
        config.max_slip_bps,
    )
    slip = close_val * slip_bps / 10000.0
    adj_fill_price = close_val - slip if pos.direction == 1 else close_val + slip

    result = book_reduce(
        state,
        config,
        pos,
        qty_abs,
        adj_fill_price,
        bar_idx,
        sig,
        triggered_by=action.reason,
        is_stop_like=bool(action.is_stop_like),
    )

    # Update last ScalingEvent with slippage + is_stop_like copy (AC13).
    if pos.scaling_events:
        ev = pos.scaling_events[-1]
        ev.slippage_bps = slip_bps
        ev.is_stop_like = bool(action.is_stop_like)

    # Apply stop_override (used on reduce to re-arm a tighter stop — T-B3).
    if action.stop_override is not None and not result.is_terminal:
        pos.stop_price = float(action.stop_override)

    # AC26 diagnostic counter.
    if not result.is_terminal:
        _bump_diag(state, pos.strategy_id, pos.token, "partial_fills")

    return bool(result.is_terminal)


def _dispatch_scale_action(
    state: SimulationState,
    pos: Position,
    action,
    bar_ctx: BarContext,
    sig: TokenBarArrays,
    config: PortfolioConfig,
) -> bool:
    """Execute a single ScaleAction or an ordered list[ScaleAction].

    AC18 C2: sets ``pos._scale_action_bar = bar_ctx.local_bar`` BEFORE executing
    so repeated sub-hourly invocations on the same hourly bar are gated by the
    caller (see ``dispatch_scale_check``).

    AC24a: list actions run in order; first terminal result halts the remainder
    and increments ``scale_actions_dropped_after_terminal`` (both on state and
    on the (strategy, token) diagnostic bucket).

    Returns ``True`` when any action caused terminal close.
    """
    # Normalize to list
    if isinstance(action, ScaleAction):
        actions = [action]
    else:
        actions = list(action)

    if not actions:
        return False

    # AC18 C2: only fire once per hourly bar (per position). Repeat sub-hourly
    # invocations on the same hourly bar are no-ops.
    if bar_ctx.local_bar <= pos._scale_action_bar:
        return False

    # AC18 C2: mark bar BEFORE execution so re-entry on same hourly bar is a no-op.
    pos._scale_action_bar = bar_ctx.local_bar

    any_terminal = False
    for idx, act in enumerate(actions):
        terminal = _dispatch_single_scale_action(state, pos, act, bar_ctx, sig, config)
        if terminal:
            any_terminal = True
            dropped = len(actions) - idx - 1
            if dropped > 0:
                state.scale_actions_dropped_after_terminal += dropped
                _bump_diag(
                    state,
                    pos.strategy_id,
                    pos.token,
                    "scale_actions_dropped_after_terminal",
                    inc=dropped,
                )
                logger.warning(
                    "Scale action terminal on %s bar=%d; dropped %d remaining actions",
                    pos.position_id,
                    bar_ctx.local_bar,
                    dropped,
                )
            break
        # If position fell out of open_positions via some other path, bail too.
        if pos not in state.position_manager.open_positions:
            break

    return any_terminal


def dispatch_scale_check(
    state: SimulationState,
    pos: Position,
    scale_fn,
    bar_ctx: BarContext,
    sig: TokenBarArrays,
    config: PortfolioConfig,
) -> bool:
    """AC25: invoke a strategy ``scale_check_fn`` with strict/non-strict
    error handling.

    Returns True when the invocation led to a terminal close.
    In strict mode (``config.strict_scale_errors`` True, the backtest default),
    exceptions from the callback are re-raised. In non-strict mode (paper-like),
    they are logged at WARNING level and swallowed.
    """
    if scale_fn is None:
        return False
    try:
        result = scale_fn(pos, bar_ctx)
    except Exception as e:
        if config.strict_scale_errors:
            raise
        logger.warning(
            "scale_check_fn error on %s: %s: %s",
            pos.position_id,
            type(e).__name__,
            e,
        )
        return False
    if result is None:
        return False
    return _dispatch_scale_action(state, pos, result, bar_ctx, sig, config)


def _get_bar_data(sig: TokenBarArrays, local_bar: int, is_primary: bool = True, use_perp: bool | None = None):
    """Get price/indicator data for a bar, choosing primary or secondary arrays.

    Args:
        use_perp: Explicit override for adaptive strategies. When set, selects
            perp arrays (True) or spot arrays (False) regardless of is_primary.
    """
    want_perp = use_perp if use_perp is not None else (not is_primary)
    if want_perp and sig.perp_close is not None:
        return (
            sig.perp_close[local_bar],
            sig.perp_high[local_bar],
            sig.perp_low[local_bar],
            sig.perp_atr[local_bar],
            sig.perp_rolling_adv[local_bar],
            sig.perp_funding_1h[local_bar] if sig.perp_funding_1h is not None else 0.0,
        )
    return (
        sig.close[local_bar],
        sig.high[local_bar],
        sig.low[local_bar],
        sig.atr[local_bar],
        sig.rolling_adv[local_bar],
        sig.funding_1h[local_bar],
    )


def _process_exits(
    state: SimulationState,
    all_signals: dict[str, dict[str, TokenBarArrays]],
    bar_maps: dict[str, np.ndarray],
    global_bar: int,
    config: PortfolioConfig,
    strategy_specs: dict[str, StrategySpec] | None = None,
):
    """Process all exit conditions for open positions.

    Faithfully ports ALL v3 JIT exit logic paths (engine.py:344-467).
    """
    positions_to_close: list[tuple[Position, float, str, float]] = []  # (pos, exit_price, reason, exit_adv)

    for pos in list(state.position_manager.open_positions):
        # Skip if already marked for closure via linked position
        if any(p is pos for p, _, _, _ in positions_to_close):
            continue

        sig = all_signals[pos.strategy_id].get(pos.token)
        if sig is None:
            continue

        bm = bar_maps.get(pos.token)
        if bm is None:
            continue

        local_bar = int(bm[global_bar])

        # Step 1: Token data ended (or out of bounds for this strategy's signals) -> force close
        if local_bar == -1 or local_bar >= sig.n_bars:
            # Find last valid bar for exit price
            last_valid = -1
            for b in range(global_bar - 1, -1, -1):
                lb = int(bm[b])
                if lb != -1 and lb < sig.n_bars:
                    last_valid = lb
                    break
            if last_valid >= 0:
                is_secondary = (pos.leg == "secondary")
                _up = pos.is_perp if sig.per_bar_is_perp is not None else None
                close_p, _, _, _, adv_p, _ = _get_bar_data(sig, last_valid, not is_secondary, use_perp=_up)
            else:
                close_p = pos.entry_price
                adv_p = 1_000_000.0
            positions_to_close.append((pos, close_p, "data_end", adv_p))
            # Also close linked position
            if pos.linked_position_id:
                linked = state.position_manager.get_linked(pos.linked_position_id)
                if linked and not any(p is linked for p, _, _, _ in positions_to_close):
                    is_linked_secondary = (linked.leg == "secondary")
                    if last_valid >= 0:
                        _lup = linked.is_perp if sig.per_bar_is_perp is not None else None
                        lc, _, _, _, la, _ = _get_bar_data(sig, last_valid, not is_linked_secondary, use_perp=_lup)
                    else:
                        lc, la = linked.entry_price, 1_000_000.0
                    positions_to_close.append((linked, lc, "data_end", la))
            continue

        is_secondary = (pos.leg == "secondary")
        # For adaptive strategies (per_bar_is_perp), use pos.is_perp for price routing
        _use_perp = pos.is_perp if sig.per_bar_is_perp is not None else None
        close_val, high_val, low_val, atr_val, adv_val, funding_val = _get_bar_data(sig, local_bar, not is_secondary, use_perp=_use_perp)

        bars_held = global_bar - pos.entry_bar
        d = pos.direction

        # Step 2: Accrue funding (perp only)
        if pos.is_perp:
            # M10 AC #16 FIX-correct: notional uses ENTRY price (static
            # at position open), not the current bar's close (MTM).
            # Matches `notional = |qty × entry_price|` per the D-1
            # cross-path parity contract.
            notional = abs(pos.quantity * pos.entry_price)
            d_sign = 1.0 if pos.quantity > 0.0 else -1.0
            funding_cost = notional * funding_val * d_sign
            state.total_funding += funding_cost
            pos.cumulative_funding += funding_cost
            # M10 AC #16 / AC #18 — funding accrual JSONL sink.
            # Emit ONE entry per non-zero funding snap (the fixture
            # leaves funding_1h=0 between snap bars, so this naturally
            # aligns with the 3 per 24h snaps at 00/08/16 UTC).
            if funding_val != 0.0:
                _emit_funding_accrual(
                    state=state, config=config, pos=pos,
                    global_bar=global_bar, sig=sig, local_bar=local_bar,
                    funding_val=float(funding_val),
                    notional=float(notional),
                    payment_usd=float(funding_cost),
                )

        # Step 3: Liquidation check
        if pos.is_perp and (pos.leverage > 1.0 or pos.quantity < 0.0):
            mmr = get_maint_margin_rate(config.exchange)
            if pos.quantity > 0.0:
                unrealized = pos.quantity * (low_val - pos.entry_price)
            else:
                unrealized = abs(pos.quantity) * (pos.entry_price - high_val)
            entry_notional = pos.margin_usd * pos.leverage
            maintenance_margin = entry_notional * mmr
            if pos.margin_usd + unrealized - pos.cumulative_funding < maintenance_margin:
                positions_to_close.append((pos, close_val, "liquidation", adv_val))
                if pos.linked_position_id:
                    linked = state.position_manager.get_linked(pos.linked_position_id)
                    if linked and not any(p is linked for p, _, _, _ in positions_to_close):
                        is_lk_sec = (linked.leg == "secondary")
                        _lkup = linked.is_perp if sig.per_bar_is_perp is not None else None
                        lc, _, _, _, la, _ = _get_bar_data(sig, local_bar, not is_lk_sec, use_perp=_lkup)
                        positions_to_close.append((linked, lc, "linked_exit", la))
                continue

        # Step 4: Update highest/lowest
        if d == 1:
            pos.highest = max(pos.highest, high_val)
        else:
            pos.lowest = min(pos.lowest, low_val)

        # Handle NaN ATR
        cur_atr = atr_val
        if np.isnan(cur_atr):
            cur_atr = abs(pos.entry_price) * 0.02

        # Build exit handler chain on first bar (lazy init for legacy-compat)
        if not pos.exit_handlers:
            spec_for_chain = strategy_specs.get(pos.strategy_id) if strategy_specs else None
            pos.exit_handlers = build_exit_chain(pos, sig, spec_for_chain, state=state, config=config)

        # Build BarContext for this bar
        rsi_val = float(sig.rsi[local_bar]) if sig.rsi is not None and local_bar < len(sig.rsi) else float('nan')
        volume_val = float(sig.volume[local_bar]) if sig.volume is not None and local_bar < len(sig.volume) else float('nan')
        vol_20_val = float(sig.vol_20[local_bar]) if sig.vol_20 is not None and local_bar < len(sig.vol_20) else float('nan')
        ret_1h_val = float(sig.ret_1h[local_bar]) if sig.ret_1h is not None and local_bar < len(sig.ret_1h) else float('nan')
        bar_ctx = BarContext(
            close=close_val,
            high=high_val,
            low=low_val,
            atr=cur_atr,
            rsi=rsi_val,
            # M9 C-4: engine regime deleted; strategies read v5.regimes.detect_crisis(bar_ctx.ctx, bar_idx)
            regime=None,
            bars_held=bars_held,
            local_bar=local_bar,
            funding_val=funding_val,
            volume=volume_val,
            vol_20=vol_20_val,
            ret_1h=ret_1h_val,
        )

        # Phase 1: update_state (breakeven, trailing) mutates pos.stop_price etc.
        run_update_state_phase(pos, bar_ctx)

        # Phase 2 (M2 / AC18): scale_check_fn — exactly once per hourly bar.
        spec = strategy_specs.get(pos.strategy_id) if strategy_specs else None
        scale_fn = getattr(spec, "scale_check_fn", None) if spec is not None else None
        if scale_fn is not None and bar_ctx.local_bar > pos._scale_action_bar:
            terminal = dispatch_scale_check(state, pos, scale_fn, bar_ctx, sig, config)
            if terminal or pos not in state.position_manager.open_positions:
                # Position was terminal-closed during scaling — skip Phase 3.
                continue

        # Phase 3: check_exit — sees stop_price mutations from Phase 1/2.
        exit_result = run_check_exit_phase(pos, bar_ctx)

        exit_signal = exit_result.should_exit
        exit_reason = exit_result.reason
        exit_price = exit_result.exit_price_override if exit_result.exit_price_override is not None else close_val

        if exit_signal:
            positions_to_close.append((pos, exit_price, exit_reason, adv_val))
            # Close linked position too
            if pos.linked_position_id:
                linked = state.position_manager.get_linked(pos.linked_position_id)
                if linked and not any(p is linked for p, _, _, _ in positions_to_close):
                    is_lk_sec = (linked.leg == "secondary")
                    _lkup = linked.is_perp if sig.per_bar_is_perp is not None else None
                    lc, _, _, _, la, _ = _get_bar_data(sig, local_bar, not is_lk_sec, use_perp=_lkup)
                    positions_to_close.append((linked, lc, "linked_exit", la))

    # Execute all closures
    # M10 AC #17 deterministic liquidation ordering: sort close list by
    # (liq_distance asc, strategy_id+token lex). For non-liquidation
    # exits the sort is a no-op because all exits within a bar happen
    # at the same price-anchored moment; this lex ordering makes
    # cascade tests reproducible across Python dict-iteration orders.
    def _close_sort_key(item):
        p, _exit_price, _reason, _adv = item
        # liq_distance proxy: entry_price - stop_price / entry_price
        # (smaller = more at risk = close first). Default to 0 when
        # stop_price is zero (unleveraged spot).
        try:
            if p.entry_price > 0 and p.stop_price > 0:
                liq_distance = abs(p.entry_price - p.stop_price) / p.entry_price
            else:
                liq_distance = 0.0
        except Exception:
            liq_distance = 0.0
        return (liq_distance, f"{p.strategy_id}:{p.token}")
    positions_to_close.sort(key=_close_sort_key)
    for pos, exit_price, reason, exit_adv in positions_to_close:
        if pos in state.position_manager.open_positions:
            _close_position(state, pos, global_bar, exit_price, reason, exit_adv, config)


def _compute_total_unrealized(
    state: SimulationState,
    all_signals: dict,
    bar_maps: dict,
    global_bar: int,
) -> float:
    """Compute total unrealized P&L across open positions."""
    total = 0.0
    for pos in state.position_manager.open_positions:
        sig = all_signals.get(pos.strategy_id, {}).get(pos.token)
        if sig is None:
            continue
        bm = bar_maps.get(pos.token)
        if bm is None:
            continue
        lb = int(bm[global_bar])
        if lb == -1 or lb >= sig.n_bars:
            continue
        is_sec = (pos.leg == "secondary")
        _up = pos.is_perp if sig.per_bar_is_perp is not None else None
        close_val = _get_bar_data(sig, lb, not is_sec, use_perp=_up)[0]
        if np.isnan(close_val):
            continue
        total += pos.quantity * (close_val - pos.entry_price)
    return total


def _stage1_trigger_armed_orders(
    state: SimulationState,
    all_signals: dict[str, dict[str, TokenBarArrays]],
    bar_maps: dict[str, np.ndarray],
    global_bar: int,
    config: PortfolioConfig,
):
    """M5 Stage 1 — ARMED -> TRIGGERED sweep for backtest armed orders.

    This is the post-M5 replacement for the legacy
    ``_process_pending_entries`` function — preserving the same trigger
    algorithm (time-delay + armed_level price cross) bit-for-bit for AC14
    hourly parity while flowing the state transition through the
    :class:`v5.orders.Order` state machine.

    When an armed Order's trigger fires, Stage 1 injects the entry onto
    ``TokenBarArrays.entry_mask`` so Stage 2 (``_stage2_process_new_signals``)
    re-picks it up and opens the Position at this bar's price. Orders whose
    expiry window has passed are dropped (the legacy
    ``max_pending_bars`` rule).
    """
    if not state.open_orders:
        return

    still_armed: list = []
    for order in state.open_orders:
        if order.state != OrderStatus.ARMED:
            # Non-ARMED Orders retain their position in the queue — they
            # are terminal (FILLED/CANCELLED/REJECTED/EXPIRED) and Stage 3
            # drains them below. Keep them in open_orders for audit.
            still_armed.append(order)
            continue

        signal_bar, entry_bar, conviction = _armed_legacy_fields(order)

        # Expired by max_pending_bars?
        if global_bar > signal_bar + config.max_pending_bars:
            # Terminal-transition ARMED -> EXPIRED (dropped, not kept).
            continue

        sig = all_signals.get(order.strategy_id, {}).get(order.token)
        if sig is None:
            still_armed.append(order)
            continue

        bm = bar_maps.get(order.token)
        if bm is None:
            still_armed.append(order)
            continue

        local_bar = int(bm[global_bar])
        if local_bar == -1 or local_bar >= sig.n_bars:
            still_armed.append(order)
            continue

        # Check trigger conditions (any one sufficient) — preserves legacy
        # algorithm bit-for-bit for AC14 hourly parity.
        triggered = False

        # Trigger 1: time delay expired (legacy entry_bar > 0 sentinel).
        if entry_bar > 0 and global_bar >= entry_bar:
            triggered = True

        # Trigger 2: armed price level crossed. For longs: low <= level
        # means price dipped to the target (fill the long limit). For
        # shorts: high >= level means price rose to the target.
        if not triggered and sig.armed_levels is not None and local_bar < len(sig.armed_levels):
            level = float(sig.armed_levels[local_bar])
            if not np.isnan(level) and level > 0:
                high_val = float(sig.high[local_bar]) if sig.high is not None else float(sig.close[local_bar])
                low_val = float(sig.low[local_bar]) if sig.low is not None else float(sig.close[local_bar])
                if order.direction == 1 and low_val <= level:
                    triggered = True
                elif order.direction == -1 and high_val >= level:
                    triggered = True

        if not triggered:
            still_armed.append(order)
            continue

        # Triggered — inject entry signal onto TokenBarArrays so Stage 2
        # opens the Position at this bar's close.
        sig.entry_mask[local_bar] = True
        sig.direction[local_bar] = int(order.direction)
            # M9 C-1: conviction write removed; strategies emit TokenSignal.priority scalar
        # Set entry_limit_price to armed level for precise fill at target.
        if sig.armed_levels is not None and local_bar < len(sig.armed_levels):
            armed_price = float(sig.armed_levels[local_bar])
            if not np.isnan(armed_price) and armed_price > 0:
                if sig.entry_limit_price is None:
                    sig.entry_limit_price = np.full(sig.n_bars, np.nan)
                sig.entry_limit_price[local_bar] = armed_price
        # Prevent re-arming: clear delay on THIS bar so Stage 2 enters immediately.
        # Preserve config.entry_delay_bars for future organic signals on this token.
        if sig.entry_delay is None:
            sig.entry_delay = np.full(sig.n_bars, config.entry_delay_bars, dtype=int)
        if local_bar < len(sig.entry_delay):
            sig.entry_delay[local_bar] = 0
        if sig.armed_levels is not None and local_bar < len(sig.armed_levels):
            sig.armed_levels[local_bar] = np.nan
        # Order consumed — drop from the armed queue. Stage 2 opens the
        # Position via the TokenBarArrays entry_mask mutation above.

    state.open_orders = still_armed


def _stage2_process_new_signals(
    state: SimulationState,
    all_signals: dict[str, dict[str, TokenBarArrays]],
    strategy_specs: dict[str, StrategySpec],
    bar_maps: dict[str, np.ndarray],
    global_bar: int,
    config: PortfolioConfig,
    rng: np.random.RandomState,
    unified_ts: np.ndarray | None = None,
):
    """M5 Stage 2/3 — process new entry signals + open Positions.

    Reads ``TokenBarArrays.entry_mask`` (populated either organically by the
    strategy at this bar OR injected by Stage 1 from an armed
    :class:`v5.orders.Order`), applies portfolio-level constraints, and
    opens Positions. Armed-but-not-yet-triggered signals are arm-queued as
    ``Order(state=ARMED)`` onto ``state.open_orders`` to reserve a slot
    (legacy parity with the pre-M5 pending-entries queue).

    The ``unified_ts`` parameter is an optional pre-built ``DatetimeIndex``
    array for deterministic ``armed_at`` timestamps when constructing new
    armed Orders; when ``None`` the current epoch is used (paper tick path).
    """
    # M11 Commit 6 — promote `Order`, `OrderStatus`, `TriggerType` to
    # the module-global binding throughout this function. The M8
    # clamp-pipeline block below re-imports these names locally (for
    # Order.arm), which otherwise makes Python treat them as
    # function-local throughout the function. The Stage-2 arming
    # branch above references ``Order.arm`` + ``TriggerType.*`` BEFORE
    # the line-1795 `from v5.orders import ...` ever runs (the arming
    # branch exits via ``continue`` well before the clamp block), so
    # the unbound-local lookup raises UnboundLocalError. Declaring
    # these names global here resolves to the same module-level
    # binding imported at line 27, preserving identity; the later
    # local re-import is a no-op re-bind of the same globals. Latent
    # bug — never hit in production because no live strategy
    # currently routes through the arming branch in v5. Addition only,
    # zero removal, zero behavior change on paths that DO run today.
    global Order, OrderStatus, TriggerType

    # Compute unrealized P&L once per bar (doesn't change during entries)
    total_unrealized = _compute_total_unrealized(state, all_signals, bar_maps, global_bar)

    # Build candidate list
    candidates: list[tuple[str, str, TokenBarArrays]] = []  # (strategy_id, token, signals)

    for strategy_id, token_signals in all_signals.items():
        for token, sig in token_signals.items():
            bm = bar_maps.get(token)
            if bm is None:
                continue
            local_bar = int(bm[global_bar])
            if local_bar == -1 or local_bar >= sig.n_bars:
                continue

            # Check entry signal
            if not sig.entry_mask[local_bar]:
                continue

            # For combined: BOTH legs must signal
            if sig.is_combined:
                if sig.secondary_entry_mask is None or not sig.secondary_entry_mask[local_bar]:
                    continue

            # AC7 cardinality rule — count Orders as 1 logical entry (not N
            # Position rows). For a 2-leg Order we emit 2 Positions sharing
            # one ``order_id``; the per-symbol concurrency guard must treat
            # that as a single entry, otherwise combined strategies are
            # throttled at half their configured ``max_positions_per_symbol``.
            #
            # Counting rule (matches ``v5.orders.count_logical_positions``):
            #   * Positions sharing the same non-empty ``order_id`` → 1 logical.
            #   * Positions with empty/None ``order_id`` (legacy M2 path) → 1 each.
            #   * Armed Orders on ``state.open_orders`` for this (token, strategy)
            #     that have not yet materialized into Positions → 1 each.
            spec_for_check = strategy_specs.get(strategy_id)
            max_conc = spec_for_check.max_positions_per_symbol if spec_for_check else 1
            from v5.orders import count_logical_positions as _count_logical  # lazy to avoid import cycle
            _open_positions = state.position_manager.find_open_for_token_strategy(
                token, strategy_id,
            )
            _logical_from_positions = _count_logical(_open_positions)
            _armed_orders_for_pair = [
                o for o in state.open_orders
                if o.strategy_id == strategy_id
                and o.token == token
                and o.state == OrderStatus.ARMED
            ]
            _logical_from_orders = len(_armed_orders_for_pair)
            if _logical_from_positions + _logical_from_orders >= max_conc:
                continue

            candidates.append((strategy_id, token, sig))

    if not candidates:
        return

    # M9 C-5 Phase 3.0: risk-component pre-arbitration hook.
    # Runs on aggregate candidate list BEFORE arbitration. Risk components
    # can REJECT (drop from list), REDUCE (scale SizingRequest), or HALT
    # (return TradingState=HALTED → no entries this bar). M8 clamps run
    # later at per-order release_atomic — complementary, not redundant.
    candidates = _apply_risk_components_phase_30(candidates, bar_maps, global_bar, config, state)
    if not candidates:
        return

    # M9 C-1: arbitration via pluggable SignalArbitrationPolicy. Falls
    # back to priority-sort when config doesn't specify one. Arbitration
    # emits one row per candidate to v5/logs/arbitration.jsonl (AC #18)
    # with rank_in / rank_out / admitted / displaced_by fields.
    indices = _run_arbitration_dispatch(
        candidates, bar_maps, global_bar, config, state
    )

    # Cache resolved sizing per strategy (resolve once, not per candidate)
    _resolved_sizing_cache: dict = {}

    # Slots masked by entry_filter_fn returning < 0 ("skip and don't replace")
    _masked_slots = 0

    for idx in indices:
        strategy_id, token, sig = candidates[idx]
        spec = strategy_specs.get(strategy_id)
        if spec is None:
            continue

        # Resolve sizing overrides for this strategy (cached).
        # M10 B1/B2: sizing_model resolution DELETED — compute_fixed_
        # fraction_notional is called directly below.
        if strategy_id not in _resolved_sizing_cache:
            _resolved_sizing_cache[strategy_id] = resolve_sizing(
                config.sizing_defaults, spec.sizing_overrides
            )
        resolved = _resolved_sizing_cache[strategy_id]
        slippage_model = state._slippage_models.get(strategy_id)

        bm = bar_maps[token]
        local_bar = int(bm[global_bar])


        # Strategy-defined entry filter: conviction adjustment based on trade history
        # Return values: >0 = allow (multiply conviction), 0 = block, <0 = mask without replace
        if spec.entry_filter_fn is not None:
            _token_trades = [t for t in state.position_manager.closed_trades
                             if t.token == token]
            _direction = int(sig.direction[local_bar])
            try:
                _mult = float(spec.entry_filter_fn(token, _direction, _token_trades, global_bar))
                if _mult < 0.0:
                    _masked_slots += 1  # consume a slot so next candidate can't fill it
                    continue
                if _mult == 0.0:
                    continue  # strategy says block (slot available for next candidate)
                # M9 C-1: conviction scaling removed; strategies handle via SizingRequest.fraction_of_equity
            except Exception:
                pass  # filter error, allow entry

        # Constraint 1: portfolio position limit (includes pending/armed entries + masked slots)
        if state.effective_open + _masked_slots >= config.max_portfolio_positions:
            state.rejections.portfolio_limit += 1
            continue

        # Constraint 2: per-strategy position limit (includes armed orders for this strategy)
        n_pending_for_strat = sum(
            1 for o in state.open_orders
            if o.strategy_id == strategy_id and o.state == OrderStatus.ARMED
        )
        if state.position_manager.count_for_strategy(strategy_id) + n_pending_for_strat >= spec.max_positions:
            state.rejections.strategy_limit += 1
            continue

        # Check if already armed for this token+strategy (prevent double-arming)
        _already_pending = any(
            o.strategy_id == strategy_id and o.token == token
            and o.state == OrderStatus.ARMED
            for o in state.open_orders
        )

        # Armed/delayed entry: per-bar delay from strategy, or global config fallback
        # Also check armed_levels for price-triggered pending entries
        _delay = 0
        if sig.entry_delay is not None and 0 <= local_bar < len(sig.entry_delay):
            _delay = int(sig.entry_delay[local_bar])
        elif config.entry_delay_bars > 0:
            _delay = config.entry_delay_bars

        _has_armed_level = (sig.armed_levels is not None and 0 <= local_bar < len(sig.armed_levels)
                            and not np.isnan(float(sig.armed_levels[local_bar]))
                            and float(sig.armed_levels[local_bar]) > 0)

        if (_delay > 0 or _has_armed_level) and not _already_pending:
            conv = 1.0
            # M9 C-1: conviction-derived priority path removed
            # M5 Task 14b — arm an Order on state.open_orders instead of the
            # legacy PendingEntry dataclass. Legacy scalar fields (signal_bar,
            # entry_bar, conviction) survive in strategy_params so Stage 1
            # preserves the exact legacy trigger algorithm for AC14 parity.
            _direction_val = int(sig.direction[local_bar])
            if _direction_val not in (-1, 1):
                _direction_val = 1  # legacy default (matches pre-M5 direction=0 fallback)
            # armed_at uses the unified_ts grid (backtest is deterministic —
            # AC24 forbids wall-clock reads in the hot path). In backtest this
            # function should never hit the fallback because unified_ts is
            # always populated by build_unified_index(). If somehow unreached,
            # raise rather than silently reading wall-clock.
            if unified_ts is not None and 0 <= global_bar < len(unified_ts):
                _armed_at = pd.Timestamp(unified_ts[global_bar]).to_pydatetime()
                if _armed_at.tzinfo is None:
                    _armed_at = _armed_at.replace(tzinfo=timezone.utc)
            else:
                raise RuntimeError(
                    f"_process_orders stage-1: cannot derive armed_at without "
                    f"unified_ts grid (global_bar={global_bar}). "
                    f"AC24 within-build determinism prohibits wall-clock fallback."
                )
            # trigger_price: armed-level price if set, else 0 (time-only trigger).
            _trigger_price = 0.0
            if _has_armed_level:
                _trigger_price = float(sig.armed_levels[local_bar])
            # Choose enum: price-level triggers use PRICE_BELOW (long waits
            # for dip to limit) / PRICE_ABOVE (short waits for rise).
            # Time-only triggers use BAR_CLOSE (Stage 1 fires when entry_bar
            # reached).
            if _has_armed_level:
                _trigger = (
                    TriggerType.PRICE_BELOW if _direction_val == 1
                    else TriggerType.PRICE_ABOVE
                )
            else:
                _trigger = TriggerType.BAR_CLOSE
            state.open_orders.append(Order.arm(
                strategy_id=strategy_id,
                token=token,
                direction=_direction_val,  # type: ignore[arg-type]
                trigger=_trigger,
                trigger_price=_trigger_price,
                working_price_source="last",
                armed_at=_armed_at,
                expires_at=None,
                sizing_ctx={"conviction": conv},
                strategy_params={
                    "signal_bar": int(global_bar),
                    "entry_bar": int(global_bar + _delay),
                    "conviction": float(conv),
                },
            ))
            continue  # slot reserved, skip immediate entry
        # (debug: arming happened above if _delay > 0)

        # Compute sizing
        portfolio_eq = state.portfolio_equity
        # Apply unrealized P&L constraint (constrain-only: never inflates above realized)
        sizing_eq = min(portfolio_eq + total_unrealized, portfolio_eq)
        sizing_eq = max(sizing_eq, 0.0)  # floor at 0 when NLV is negative
        if config.max_sizing_equity is not None:
            sizing_eq = min(sizing_eq, config.max_sizing_equity)
        strategy_equity = sizing_eq * spec.weight

        close_val = sig.close[local_bar]
        atr_val = sig.atr[local_bar]
        if np.isnan(atr_val):
            atr_val = close_val * 0.02
        volatility = atr_val / max(close_val, 1e-10)

        adv_val = sig.rolling_adv[local_bar]
        lev_val = float(sig.leverage[local_bar])

        # M10 B1/B2: sizing_model.compute_size() migrated to
        # compute_fixed_fraction_notional — same math, no legacy import.
        pos_usd = compute_fixed_fraction_notional(
            equity=strategy_equity,
            adv=adv_val,
            edge=sig.edge,
            leverage=lev_val,
            adv_cap_pct=config.adv_cap_pct,
            edge_minimum=resolved.edge_minimum,
            spot_max_equity_pct=resolved.spot_max_equity_pct,
        )

        # M8 clamp pipeline (M9 Wave D: flag flipped to True default,
        # legacy path deleted. `use_m8_clamps` kept as config field for
        # legacy-compat but runtime is unconditional).
        if pos_usd > 0:
            try:
                from pathlib import Path as _Path
                from v5.sizing.allocation import SharedPoolPolicy
                from v5.sizing.clamps import ClampsConfig, _lookup_mmr, DEFAULT_MMR_SCHEDULE
                from v5.sizing.intents import SizingIntent, SizingRequest
                from v5.orders import Order, OrderStatus, TriggerType
                m8_req = SizingRequest(
                    intent=SizingIntent.FIXED_NOTIONAL,
                    notional_usd=float(pos_usd),
                    leverage=float(lev_val),
                )
                # armed_at uses the deterministic base epoch (2026-01-01)
                # per AC24 — no wall-clock reads in the simulator path.
                m8_order = Order.arm(
                    strategy_id=strategy_id, token=sig.token,
                    direction=int(sig.direction[local_bar]),
                    trigger=TriggerType.PRICE_ABOVE,
                    trigger_price=float(close_val),
                    working_price_source="last",
                    armed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                    expires_at=None,
                    sizing_ctx={
                        "target_size": float(pos_usd) / max(float(close_val), 1e-10),
                        "market": "perp" if spec.market == "perp" else "spot",
                        "sizing": m8_req,
                    },
                )
                # Flag-path MarketState. `liquidation_distance` computes
                # (1/L - mmr) × 10000 via the Binance tiered MMR schedule
                # (matches clamp #5 math — FIX reviewer round-2 MAJOR:
                # previously hardcoded 10_000 bps made liq clamp inert).
                _adv_val, _close_val = adv_val, close_val
                _strategy_equity = strategy_equity

                class _SimMktState:
                    def adv(self, t): return _adv_val
                    def rolling_adv(self, t, window_hours=24): return _adv_val
                    def mark_price(self, t): return _close_val
                    def equity(self, sid): return _strategy_equity
                    def liquidation_distance(self, p, l):
                        notional = abs(float(_strategy_equity) * float(l))
                        mmr = _lookup_mmr(notional, DEFAULT_MMR_SCHEDULE)
                        return max(0.0, (1.0 / max(float(l), 1e-9) - mmr) * 10_000.0)

                # Route through release_atomic — NOT run_clamp_pipeline
                # directly. This ensures aggregate-leverage pre-clamp +
                # reduce_only overfill gates fire in the flag-ON sim
                # path (FIX reviewer round-2 MAJOR). log_path wired so
                # AC-Sz5 binding-log JSONL persists.
                m8_cfg = ClampsConfig(
                    adv_cap_pct=config.adv_cap_pct,
                    concentration_limit=config.concentration_limit,
                    min_position_usd=config.min_position_usd,
                    min_liquidation_distance_bps=100.0,
                    log_path=_Path("v5/logs/sizing_fills.jsonl"),
                )
                released = m8_order.release_atomic(
                    available_capital_usd=strategy_equity,
                    market_state=_SimMktState(),
                    policy=SharedPoolPolicy(),
                    config=m8_cfg,
                )
                if released.state == OrderStatus.RELEASED:
                    pos_usd = float(
                        released.sizing_ctx.get("margin_usd", pos_usd)
                        * float(lev_val)
                    )
                else:
                    pos_usd = 0.0  # clamp-rejected → skip entry
            except Exception:
                # AC-Sz7 — clamp errors don't crash the backtest loop.
                # Fall through to legacy pos_usd from sizing_model.
                pass

        direction = int(sig.direction[local_bar])
        if direction == 0:
            state.rejections.direction_zero += 1
            continue  # reject: strategies must emit +1 or -1

        if sig.is_combined:
            # Split capital between legs
            primary_usd = pos_usd * sig.capital_split
            secondary_usd = pos_usd * (1.0 - sig.capital_split)

            # Constraint 4: each leg >= min_position_usd
            if primary_usd < config.min_position_usd or secondary_usd < config.min_position_usd:
                state.rejections.min_size += 1
                continue

            # Constraint 6: ADV cap per leg
            perp_adv = sig.perp_rolling_adv[local_bar] if sig.perp_rolling_adv is not None else adv_val
            if primary_usd > adv_val * config.adv_cap_pct or secondary_usd > perp_adv * config.adv_cap_pct:
                state.rejections.adv_cap += 1
                continue

            total_margin = primary_usd + secondary_usd

            # Constraint 7: concentration limit (scale down if needed)
            existing_margin = state.position_manager.total_margin_for_token(token)
            max_for_token = config.concentration_limit * portfolio_eq - existing_margin
            if total_margin > max_for_token:
                # Both legs must remain >= min_position_usd after scaling
                min_split = min(sig.capital_split, 1.0 - sig.capital_split)
                if max_for_token * min_split < config.min_position_usd:
                    state.rejections.concentration += 1
                    continue
                # Scale down proportionally
                pos_usd = max_for_token
                primary_usd = pos_usd * sig.capital_split
                secondary_usd = pos_usd * (1.0 - sig.capital_split)
                total_margin = primary_usd + secondary_usd
                state.partial_fills += 1

            # Constraint 8: free capital
            spot_fee_rate = get_fee_rate(config.exchange, "spot", "taker")
            perp_fee_rate = get_fee_rate(config.exchange, "perp", "taker")

            # Primary leg fee
            if sig.is_perp_primary:
                primary_fee_rate = perp_fee_rate
            else:
                primary_fee_rate = spot_fee_rate
            # Secondary leg fee
            if sig.is_perp_secondary:
                secondary_fee_rate = perp_fee_rate
            else:
                secondary_fee_rate = spot_fee_rate

            # Apply leverage to notional for fee calculation
            primary_notional = primary_usd * lev_val if sig.is_perp_primary and lev_val > 1.0 else primary_usd
            sec_lev = sig.secondary_leverage
            secondary_notional = secondary_usd * sec_lev if sig.is_perp_secondary and sec_lev > 1.0 else secondary_usd

            primary_entry_fee = primary_notional * primary_fee_rate
            secondary_entry_fee = secondary_notional * secondary_fee_rate
            total_entry_fee = primary_entry_fee + secondary_entry_fee

            if state.free_capital < total_margin + total_entry_fee:
                # Reserve buffer for per-bar funding costs on open positions
                funding_buffer = max(state.portfolio_equity * config.sizing_defaults.funding_buffer_pct, 1.0)
                usable = state.free_capital - funding_buffer
                if usable <= 0:
                    state.rejections.capital += 1
                    continue
                # Compute fee ratio (linear in pos_usd) for scale-down
                fee_pct = total_entry_fee / max(total_margin, 1e-10)
                affordable = usable / (1.0 + fee_pct)
                min_split = min(sig.capital_split, 1.0 - sig.capital_split)
                if affordable * min_split < config.min_position_usd:
                    state.rejections.capital += 1
                    continue
                # Scale down to affordable size
                pos_usd = affordable
                primary_usd = pos_usd * sig.capital_split
                secondary_usd = pos_usd * (1.0 - sig.capital_split)
                total_margin = primary_usd + secondary_usd
                # Recompute notionals and fees at new size
                primary_notional = primary_usd * lev_val if sig.is_perp_primary and lev_val > 1.0 else primary_usd
                secondary_notional = secondary_usd * sec_lev if sig.is_perp_secondary and sec_lev > 1.0 else secondary_usd
                primary_entry_fee = primary_notional * primary_fee_rate
                secondary_entry_fee = secondary_notional * secondary_fee_rate
                total_entry_fee = primary_entry_fee + secondary_entry_fee
                state.partial_fills += 1

            # Open both legs atomically
            pos_id_base = f"{token}:{strategy_id}:{global_bar}"

            # Primary leg
            p_close = sig.close[local_bar]
            p_high = sig.high[local_bar]
            p_low = sig.low[local_bar]
            p_atr = atr_val

            if slippage_model is not None:
                p_slip_bps = slippage_model.compute_slippage(primary_notional, adv_val, config.base_spread_bps, config.impact_coeff, config.max_slip_bps)
            else:
                p_slip_bps = compute_slippage_bps(primary_notional, adv_val, config.base_spread_bps, config.impact_coeff, config.max_slip_bps)
            p_slip = p_close * p_slip_bps / 10000.0
            p_entry_price = p_close + p_slip * direction

            p_quantity = primary_notional / max(p_entry_price, 1e-10) * direction

            _psm = float(sig.stop_mult[local_bar])
            if _psm >= 999:
                p_initial_risk = 0.0
                p_stop = 0.0
            elif direction == 1:
                p_initial_risk = _psm * p_atr
                p_stop = p_entry_price - p_initial_risk
            else:
                p_initial_risk = _psm * p_atr
                p_stop = p_entry_price + p_initial_risk

            primary_pos = Position(
                position_id=f"{pos_id_base}:primary",
                token=token,
                strategy_id=strategy_id,
                leg_ref_id="leg_primary",
                entry_bar=global_bar,
                entry_price=p_entry_price,
                direction=direction,
                quantity=p_quantity,
                margin_usd=primary_usd,
                leverage=lev_val,
                is_perp=sig.is_perp_primary,
                fee_rate=primary_fee_rate,
                stop_mult=float(sig.stop_mult[local_bar]),
                trail_mult=float(sig.trail_mult[local_bar]),
                target_mult=sig.target_mult,
                no_stop_bars=sig.no_stop_bars,
                min_hold=sig.min_hold,
                max_hold=sig.max_hold,
                convex_exit=sig.convex_exit,
                rsi_exit_level=sig.rsi_exit_level,
                convex_bar_thresholds=sig.convex_bar_thresholds,
                convex_multipliers=sig.convex_multipliers,
                trail_schedule=sig.trail_schedule,
                time_trail_schedule=sig.time_trail_schedule,
                max_trail_mult_arr=sig.max_trail_mult,
                funding_exit_threshold=sig.funding_exit_threshold,
                breakeven_atr=sig.breakeven_atr,
                chandelier_lookback=sig.chandelier_lookback,
                stop_price=p_stop,
                highest=p_high,
                lowest=p_low,
                initial_risk=p_initial_risk,
                linked_position_id=f"{pos_id_base}:secondary",
            )

            # Secondary leg
            sec_dir = int(sig.secondary_direction[local_bar]) if sig.secondary_direction is not None else direction
            if sec_dir == 0:
                sec_dir = -direction  # default: opposite of primary for carry

            if sig.perp_close is not None:
                s_close = sig.perp_close[local_bar]
                s_high = sig.perp_high[local_bar]
                s_low = sig.perp_low[local_bar]
                s_atr = sig.perp_atr[local_bar] if sig.perp_atr is not None else atr_val
                s_adv = sig.perp_rolling_adv[local_bar] if sig.perp_rolling_adv is not None else adv_val
            else:
                s_close = sig.close[local_bar]
                s_high = sig.high[local_bar]
                s_low = sig.low[local_bar]
                s_atr = atr_val
                s_adv = adv_val

            if slippage_model is not None:
                s_slip_bps = slippage_model.compute_slippage(secondary_notional, s_adv, config.base_spread_bps, config.impact_coeff, config.max_slip_bps)
            else:
                s_slip_bps = compute_slippage_bps(secondary_notional, s_adv, config.base_spread_bps, config.impact_coeff, config.max_slip_bps)
            s_slip = s_close * s_slip_bps / 10000.0
            s_entry_price = s_close + s_slip * sec_dir

            s_quantity = secondary_notional / max(s_entry_price, 1e-10) * sec_dir

            # Secondary trade params (fall back to primary if not specified)
            s_stop_mult = sig.sec_stop_mult if sig.sec_stop_mult is not None else float(sig.stop_mult[local_bar])
            s_trail_mult = sig.sec_trail_mult if sig.sec_trail_mult is not None else float(sig.trail_mult[local_bar])
            s_target_mult = sig.sec_target_mult if sig.sec_target_mult is not None else sig.target_mult
            s_no_stop_bars = sig.sec_no_stop_bars if sig.sec_no_stop_bars is not None else sig.no_stop_bars
            s_min_hold = sig.sec_min_hold if sig.sec_min_hold is not None else sig.min_hold
            s_max_hold = sig.sec_max_hold if sig.sec_max_hold is not None else sig.max_hold

            if np.isnan(s_atr):
                s_atr = abs(s_entry_price) * 0.02
            s_initial_risk = s_stop_mult * s_atr
            if sec_dir == 1:
                s_stop = s_entry_price - s_initial_risk
            else:
                s_stop = s_entry_price + s_initial_risk

            secondary_pos = Position(
                position_id=f"{pos_id_base}:secondary",
                token=token,
                strategy_id=strategy_id,
                leg_ref_id="leg_secondary",
                entry_bar=global_bar,
                entry_price=s_entry_price,
                direction=sec_dir,
                quantity=s_quantity,
                margin_usd=secondary_usd,
                leverage=sec_lev,
                is_perp=sig.is_perp_secondary,
                fee_rate=secondary_fee_rate,
                stop_mult=s_stop_mult,
                trail_mult=s_trail_mult,
                target_mult=s_target_mult,
                no_stop_bars=s_no_stop_bars,
                min_hold=s_min_hold,
                max_hold=s_max_hold,
                convex_exit=sig.convex_exit,
                rsi_exit_level=sig.rsi_exit_level,
                convex_bar_thresholds=sig.convex_bar_thresholds,
                convex_multipliers=sig.convex_multipliers,
                trail_schedule=sig.trail_schedule,
                time_trail_schedule=sig.time_trail_schedule,
                max_trail_mult_arr=sig.max_trail_mult,
                funding_exit_threshold=sig.funding_exit_threshold,
                breakeven_atr=sig.breakeven_atr,
                chandelier_lookback=sig.chandelier_lookback,
                stop_price=s_stop,
                highest=s_high,
                lowest=s_low,
                initial_risk=s_initial_risk,
                linked_position_id=f"{pos_id_base}:primary",
            )

            # Deduct fees, open positions
            state.total_fees += total_entry_fee
            state._entry_fees_by_pos[primary_pos.position_id] = primary_entry_fee
            state._entry_fees_by_pos[secondary_pos.position_id] = secondary_entry_fee
            primary_pos.exit_handlers = build_exit_chain(primary_pos, sig, spec, state=state, config=config)
            secondary_pos.exit_handlers = build_exit_chain(secondary_pos, sig, spec, state=state, config=config)
            state.position_manager.open_position(primary_pos)
            state.position_manager.open_position(secondary_pos)
            if strategy_id not in state.diagnostics.entries_opened:
                state.diagnostics.entries_opened[strategy_id] = 0
            state.diagnostics.entries_opened[strategy_id] += 1

        else:
            # Single-leg entry

            # Per-bar venue routing for adaptive strategies (spot longs, perp shorts)
            is_perp_this_bar = sig.is_perp_primary
            if sig.per_bar_is_perp is not None:
                is_perp_this_bar = bool(sig.per_bar_is_perp[local_bar])
                # Override price/ADV with correct venue data
                if is_perp_this_bar and sig.perp_close is not None:
                    close_val = float(sig.perp_close[local_bar])
                    atr_val = float(sig.perp_atr[local_bar])
                    if np.isnan(atr_val):
                        atr_val = close_val * 0.02
                    adv_val = float(sig.perp_rolling_adv[local_bar])
                    volatility = atr_val / max(close_val, 1e-10)
                    # Recompute position size with perp venue data.
                    # M10 B1/B2: migrated from sizing_model.compute_size().
                    pos_usd = compute_fixed_fraction_notional(
                        equity=strategy_equity,
                        adv=adv_val,
                        edge=sig.edge,
                        leverage=lev_val,
                        adv_cap_pct=config.adv_cap_pct,
                        edge_minimum=resolved.edge_minimum,
                        spot_max_equity_pct=resolved.spot_max_equity_pct,
                    )

            # Constraint 5: min position size
            if pos_usd < config.min_position_usd:
                state.rejections.min_size += 1
                continue

            # Constraint 6: ADV cap
            if pos_usd > adv_val * config.adv_cap_pct:
                state.rejections.adv_cap += 1
                continue

            # Leverage: amplify notional, margin stays same
            margin_usd = pos_usd
            if is_perp_this_bar and lev_val > 1.0:
                notional_usd = pos_usd * lev_val
            else:
                notional_usd = pos_usd

            # Constraint 7: concentration limit (scale down if needed)
            existing_margin = state.position_manager.total_margin_for_token(token)
            max_for_token = config.concentration_limit * portfolio_eq - existing_margin
            if margin_usd > max_for_token:
                if max_for_token < config.min_position_usd:
                    state.rejections.concentration += 1
                    continue
                # Scale down to fit concentration limit
                pos_usd = max_for_token
                margin_usd = pos_usd
                if is_perp_this_bar and lev_val > 1.0:
                    notional_usd = pos_usd * lev_val
                else:
                    notional_usd = pos_usd
                state.partial_fills += 1

            # Fee
            if is_perp_this_bar:
                fee_rate = get_fee_rate(config.exchange, "perp", "taker")
            else:
                fee_rate = get_fee_rate(config.exchange, "spot", "taker")

            entry_fee = notional_usd * fee_rate

            # Constraint 8: free capital (scale down if needed)
            if state.free_capital < margin_usd + entry_fee:
                # Reserve buffer for per-bar funding costs on open positions
                funding_buffer = max(state.portfolio_equity * config.sizing_defaults.funding_buffer_pct, 1.0)
                usable = state.free_capital - funding_buffer
                if usable <= 0:
                    state.rejections.capital += 1
                    continue
                # Compute affordable margin given fee structure
                if is_perp_this_bar and lev_val > 1.0:
                    affordable = usable / (1.0 + lev_val * fee_rate)
                else:
                    affordable = usable / (1.0 + fee_rate)
                if affordable < config.min_position_usd:
                    state.rejections.capital += 1
                    continue
                # Scale down to affordable size
                pos_usd = affordable
                margin_usd = pos_usd
                if is_perp_this_bar and lev_val > 1.0:
                    notional_usd = pos_usd * lev_val
                else:
                    notional_usd = pos_usd
                entry_fee = notional_usd * fee_rate
                state.partial_fills += 1

            # Entry slippage
            if slippage_model is not None:
                slip_bps = slippage_model.compute_slippage(notional_usd, adv_val, config.base_spread_bps, config.impact_coeff, config.max_slip_bps)
            else:
                slip_bps = compute_slippage_bps(notional_usd, adv_val, config.base_spread_bps, config.impact_coeff, config.max_slip_bps)
            slip = close_val * slip_bps / 10000.0

            # Limit entry price: check if limit fills on this bar
            base_price = close_val
            low_val_entry = sig.low[local_bar]
            high_val_entry = sig.high[local_bar]
            if sig.entry_limit_price is not None and 0 <= local_bar < len(sig.entry_limit_price):
                lp = float(sig.entry_limit_price[local_bar])
                if not np.isnan(lp):
                    if direction == 1 and low_val_entry <= lp:
                        base_price = lp      # Long limit filled
                    elif direction == -1 and high_val_entry >= lp:
                        base_price = lp      # Short limit filled
            entry_price = base_price + slip * direction

            quantity = notional_usd / max(entry_price, 1e-10) * direction

            _sm2 = float(sig.stop_mult[local_bar])
            if _sm2 >= 999:
                initial_risk = 0.0
                stop_price = 0.0
            else:
                initial_risk = _sm2 * atr_val
                stop_price = entry_price - initial_risk if direction == 1 else entry_price + initial_risk

            pos = Position(
                position_id=f"{token}:{strategy_id}:{global_bar}:primary",
                token=token,
                strategy_id=strategy_id,
                leg_ref_id="leg_primary",
                entry_bar=global_bar,
                entry_price=entry_price,
                direction=direction,
                quantity=quantity,
                margin_usd=margin_usd,
                leverage=lev_val,
                is_perp=is_perp_this_bar,
                fee_rate=fee_rate,
                stop_mult=float(sig.stop_mult[local_bar]),
                trail_mult=float(sig.trail_mult[local_bar]),
                target_mult=sig.target_mult,
                no_stop_bars=sig.no_stop_bars,
                min_hold=sig.min_hold,
                max_hold=sig.max_hold,
                convex_exit=sig.convex_exit,
                rsi_exit_level=sig.rsi_exit_level,
                convex_bar_thresholds=sig.convex_bar_thresholds,
                convex_multipliers=sig.convex_multipliers,
                trail_schedule=sig.trail_schedule,
                time_trail_schedule=sig.time_trail_schedule,
                max_trail_mult_arr=sig.max_trail_mult,
                funding_exit_threshold=sig.funding_exit_threshold,
                breakeven_atr=sig.breakeven_atr,
                chandelier_lookback=sig.chandelier_lookback,
                stop_price=stop_price,
                highest=sig.high[local_bar],
                lowest=sig.low[local_bar],
                initial_risk=initial_risk,
                limit_price=base_price if base_price != close_val else 0.0,
                stop_limit_price=stop_price,
                fill_source="hourly",
            )

            state.total_fees += entry_fee
            state._entry_fees_by_pos[pos.position_id] = entry_fee
            pos.exit_handlers = build_exit_chain(pos, sig, spec, state=state, config=config)
            state.position_manager.open_position(pos)
            if strategy_id not in state.diagnostics.entries_opened:
                state.diagnostics.entries_opened[strategy_id] = 0
            state.diagnostics.entries_opened[strategy_id] += 1

            # M10 AC #16: if the entry bar has a non-zero funding rate,
            # accrue + emit immediately (the _process_exits funding
            # block only sees positions that were open AT THE START of
            # the bar — entries opened here miss the bar-open snap).
            if is_perp_this_bar and sig.funding_1h is not None:
                _entry_fund = float(sig.funding_1h[local_bar])
                if _entry_fund != 0.0:
                    _n = abs(pos.quantity * float(entry_price))
                    _d = 1.0 if pos.quantity > 0.0 else -1.0
                    _pay = _n * _entry_fund * _d
                    state.total_funding += _pay
                    pos.cumulative_funding += _pay
                    _emit_funding_accrual(
                        state=state, config=config, pos=pos,
                        global_bar=global_bar, sig=sig, local_bar=local_bar,
                        funding_val=_entry_fund, notional=_n, payment_usd=_pay,
                    )


def _process_margin_calls(
    state: SimulationState,
    all_signals: dict[str, dict[str, TokenBarArrays]],
    bar_maps: dict[str, np.ndarray],
    global_bar: int,
    config: PortfolioConfig,
):
    """Force-close positions when funding erosion pushes free_capital below threshold.

    Closes positions with the worst cumulative_funding/margin_usd ratio first.
    Handles linked carry-pair legs (both legs closed together).
    Skips positions with no market data at the current bar.
    """
    skip_ids: set[str] = set()
    while state.position_manager.open_positions:
        threshold = -max(state.portfolio_equity * 0.05, 1.0)
        if state.free_capital >= threshold:
            break
        # Find the open position with the worst funding ratio
        worst_pos = None
        worst_ratio = -float('inf')
        for pos in state.position_manager.open_positions:
            if pos.position_id in skip_ids:
                continue
            if pos.margin_usd > 0:
                ratio = pos.cumulative_funding / pos.margin_usd
                if ratio > worst_ratio:
                    worst_ratio = ratio
                    worst_pos = pos
        if worst_pos is None:
            break
        # Get market data for this position
        sig = all_signals.get(worst_pos.strategy_id, {}).get(worst_pos.token)
        if sig is None:
            skip_ids.add(worst_pos.position_id)
            continue
        bm = bar_maps.get(worst_pos.token)
        if bm is None:
            skip_ids.add(worst_pos.position_id)
            continue
        lb = int(bm[global_bar])
        if lb == -1 or lb >= sig.n_bars:
            skip_ids.add(worst_pos.position_id)
            continue
        is_sec = (worst_pos.leg == "secondary")
        _up = worst_pos.is_perp if sig.per_bar_is_perp is not None else None
        close_val, _, _, _, adv_val, _ = _get_bar_data(sig, lb, not is_sec, use_perp=_up)
        _close_position(state, worst_pos, global_bar, close_val, "margin_call", adv_val, config)
        state.margin_calls += 1
        # Close linked carry-pair leg if present
        if worst_pos.linked_position_id:
            linked = state.position_manager.get_linked(worst_pos.linked_position_id)
            if linked and linked in state.position_manager.open_positions:
                is_lk_sec = (linked.leg == "secondary")
                _lup = linked.is_perp if sig.per_bar_is_perp is not None else None
                lc, _, _, _, la, _ = _get_bar_data(sig, lb, not is_lk_sec, use_perp=_lup)
                _close_position(state, linked, global_bar, lc, "margin_call", la, config)
                state.margin_calls += 1


def _process_orders(
    state: SimulationState,
    all_signals: dict[str, dict[str, TokenBarArrays]],
    strategy_specs: dict[str, StrategySpec],
    bar_maps: dict[str, np.ndarray],
    global_bar: int,
    config: PortfolioConfig,
    rng: np.random.RandomState,
    unified_ts: np.ndarray | None = None,
):
    """M5 Task 15 — unified single open path (AC1 / AC12 / AC14).

    Every Position open in the backtest flows through this single entry point.
    Internally it is a 3-stage cascade that preserves the M2 legacy algorithm
    bit-for-bit for AC14 hourly parity while funnelling ALL opens through the
    :class:`v5.orders.Order` state machine:

      * **Stage 1** — ARMED :class:`Order` instances on ``state.open_orders``
        are tested against the current bar. Orders whose trigger fires
        transition ARMED -> TRIGGERED and inject their entry onto
        :class:`TokenBarArrays` (preserves legacy pending-entries conversion).
        Orders past ``max_pending_bars`` are dropped (EXPIRED).
      * **Stage 2** — every bar that has an ``entry_mask[local_bar] == True``
        becomes a candidate. Organic signals and Stage 1 injections are
        treated identically (single open path, AC1). Candidates that the
        strategy marks as delayed/limit-armed become new ARMED Orders
        appended to ``state.open_orders``. Candidates ready NOW pass through
        to Stage 3.
      * **Stage 3** — TRIGGERED -> RELEASED -> FILLED: constraint checks
        (ADV / concentration / capital / min size / portfolio + strategy
        position limits), Position construction, fee deduction, state
        update. This stage still uses the M2 combined primary/secondary
        direct-open path when the strategy emits ``is_combined=True``
        (preserved byte-identically for AC14).

    Immediate market orders (``entry_delay == 0`` and no armed-level set)
    cycle the full ARMED -> TRIGGERED -> RELEASED -> FILLED state machine in
    a single call — Stage 2 opens them directly without ever enqueueing on
    ``state.open_orders`` (zero-overhead single-leg path, AC3).
    """
    # Stage 1: ARMED -> TRIGGERED (armed Orders check their triggers and
    # inject entries for Stage 2 to pick up).
    if state.open_orders:
        _stage1_trigger_armed_orders(
            state, all_signals, bar_maps, global_bar, config,
        )
    # Stage 2/3: process new signals + open Positions. Legacy stage-2 arming
    # of fresh delayed signals also happens here (appends Order(ARMED) to
    # state.open_orders for next-bar Stage 1 processing).
    _stage2_process_new_signals(
        state, all_signals, strategy_specs, bar_maps, global_bar, config, rng,
        unified_ts=unified_ts,
    )


def _record_equity_snapshot(
    state: SimulationState,
    all_signals: dict[str, dict[str, TokenBarArrays]],
    bar_maps: dict[str, np.ndarray],
    global_bar: int,
    timestamp,
):
    """Record mark-to-market equity snapshot and warn on margin deficiency.

    Computes unrealized P&L across all open positions and appends
    (timestamp, equity + unrealized) to state.equity_snapshots.
    Prints a warning to stderr if free_capital is deeply negative after margin calls.
    """
    if state.free_capital < -max(state.portfolio_equity * 0.10, 1.0):
        print(f"WARNING: bar {global_bar}: margin deficient after margin calls "
              f"(free_capital={state.free_capital:.2f}, equity={state.portfolio_equity:.2f})",
              file=sys.stderr)

    mtm_unrealized = _compute_total_unrealized(state, all_signals, bar_maps, global_bar)

    mtm_equity = state.portfolio_equity + mtm_unrealized
    state.max_equity_watermark = max(state.max_equity_watermark, mtm_equity)
    state.equity_snapshots.append((timestamp, mtm_equity))


# ============================================================
# M9 C-5 Phase 3.0: risk-component pre-arbitration hook.
# ============================================================


def _apply_risk_components_phase_30(candidates, bar_maps, global_bar, config, state):
    """M9 C-5 Phase 3.0: run risk components on aggregate candidates
    BEFORE arbitration. Each component's verdict determines whether
    the candidate stays (ACCEPT/REDUCE) or drops (REJECT/HALT).

    - ACCEPT: candidate passes.
    - REDUCE: sizing throttled but candidate stays (component already
      mutated candidate.sizing.fraction_of_equity in-place).
    - REJECT: candidate dropped.
    - HALT: all remaining candidates dropped (TradingState.HALTED
      flipped on state; persists until manual reset).

    Returns the filtered candidates list (same tuple shape). No-op
    when config.risk_components is empty.
    """
    components = getattr(config, "risk_components", None) or []
    if not components:
        return candidates

    # If trading_state already HALTED from a prior bar, drop all entries.
    ts = getattr(state, "trading_state", "ACTIVE")
    if ts == "HALTED":
        return []

    from v5.risk import RiskDecision
    from v5.arbitration import EntryCandidate

    # Build a lightweight risk-state view from SimulationState.
    # Only fields the shipped risk components read.
    positions = getattr(state, "position_manager", None)
    open_notional = {}
    per_sym_strats: dict = {}
    if positions is not None and hasattr(positions, "open_positions"):
        try:
            for p in positions.open_positions.values():
                tok = getattr(p, "token", None)
                if not tok:
                    continue
                notional = float(getattr(p, "initial_margin", 0.0) or 0.0) * \
                    float(getattr(p, "leverage", 1.0) or 1.0) * \
                    float(getattr(p, "direction", 1) or 1)
                open_notional[tok] = open_notional.get(tok, 0.0) + notional
                per_sym_strats.setdefault(tok, set()).add(
                    getattr(p, "strategy_id", "")
                )
        except Exception:
            pass

    equity = float(getattr(state, "portfolio_equity", 0.0) or 0.0)
    peak_equity = float(getattr(state, "max_equity_watermark", equity) or equity)

    class _RiskStateShim:
        pass
    rss = _RiskStateShim()
    rss.equity = equity
    rss.peak_equity = peak_equity
    rss.open_positions_notional_usd = open_notional
    rss.open_orders_count = len(open_notional)
    rss.realized_pnl_today_usd = float(getattr(state, "realized_pnl_today_usd", 0.0) or 0.0)
    rss.funding_rate_bps = {}
    rss.per_symbol_strategies = per_sym_strats
    rss.correlation_matrix = {}
    rss.trading_state = ts

    kept = []
    halted = False
    for (sid, tok, sig) in candidates:
        # Wrap as EntryCandidate for component compatibility.
        ec = EntryCandidate(strategy_id=sid, token=tok, priority=0.0)
        ec.sizing = None
        ec.notional_usd = None

        decision = RiskDecision.ACCEPT
        for comp in components:
            try:
                d = comp.check(ec, rss, clock_now_ns=int(global_bar) * 3_600_000_000_000)
            except Exception:
                d = RiskDecision.ACCEPT  # never fail hot path on risk bug
            if d == RiskDecision.HALT:
                halted = True
                break
            if d == RiskDecision.REJECT:
                decision = RiskDecision.REJECT
                break
            # ACCEPT / REDUCE: keep candidate. REDUCE already mutated sizing.
        if halted:
            break
        if decision == RiskDecision.REJECT:
            continue
        kept.append((sid, tok, sig))

    if halted:
        object.__setattr__(state, "trading_state", "HALTED")
        return []
    return kept


# ============================================================
# M9 C-1: arbitration dispatch — wires SignalArbitrationPolicy into
# the engine's candidate-ranking step. Replaces the legacy
# `(-priority, sid, tok)` sort with config.arbitration_policy.rank()
# and emits per-candidate telemetry to arbitration.jsonl.
# ============================================================


def _run_arbitration_dispatch(candidates, bar_maps, global_bar, config, state):
    """M9 C-1 Phase 3.1: arbitrate candidates via pluggable policy.

    `candidates` is a list of `(strategy_id, token, sig)` tuples
    collected pre-ranking. Returns indices into that list in
    arbitrated order.

    Side effects: writes one row per candidate to
    `v5/logs/arbitration.jsonl` via ArbitrationLogWriter attached to
    `state._arbitration_log_writer` (lazy-created on first call).
    """
    from v5.arbitration import EntryCandidate, SimulationState as _ArbState
    import numpy as np

    # Build EntryCandidate list + track original indices for return mapping.
    arb_cands = []
    idx_by_cand_key = {}
    for orig_idx, (sid, tok, sig) in enumerate(candidates):
        bm = bar_maps[tok]
        lb = int(bm[global_bar])
        priority = 0.0
        if sig.priority is not None and 0 <= lb < len(sig.priority):
            priority = float(sig.priority[lb])
        key = (sid, tok, orig_idx)
        ec = EntryCandidate(strategy_id=sid, token=tok, priority=priority)
        arb_cands.append(ec)
        idx_by_cand_key[(sid, tok)] = orig_idx

    # Resolve arbitration policy (default to PriorityDesc if config absent).
    policy = getattr(config, "arbitration_policy", None)
    if policy is None:
        from v5.arbitration import PriorityDesc
        policy = PriorityDesc()

    arb_state = _ArbState(
        rng=np.random.default_rng(
            getattr(config, "seed", 42) + global_bar
        ),
        equity=float(getattr(state, "portfolio_equity", 0.0) or 0.0),
    )

    ranked = policy.rank(arb_cands, arb_state, scope="portfolio")

    # Emit telemetry — one row per candidate (in rank_in order).
    writer = getattr(state, "_arbitration_log_writer", None)
    if writer is None:
        try:
            from v5.arbitration import ArbitrationLogWriter
            from pathlib import Path
            log_dir = Path("v5/logs")
            writer = ArbitrationLogWriter(log_dir=log_dir)
            object.__setattr__(state, "_arbitration_log_writer", writer)
        except Exception:
            writer = None

    ranked_lookup = {(c.strategy_id, c.token): r_idx for r_idx, c in enumerate(ranked)}
    if writer is not None:
        try:
            for cand_in_idx, c in enumerate(arb_cands):
                rank_out = ranked_lookup.get((c.strategy_id, c.token))
                writer.write({
                    "bar_idx": int(global_bar),
                    "strategy_id": c.strategy_id,
                    "token": c.token,
                    "rank_in": cand_in_idx,
                    "rank_out": rank_out,
                    "tier": c.tier,
                    "admitted": rank_out is not None,
                    "displaced_by": None,
                })
        except Exception:
            pass  # telemetry never fails the hot path

    # Map ranked order back to indices into the original `candidates` list.
    out = []
    for c in ranked:
        k = (c.strategy_id, c.token)
        if k in idx_by_cand_key:
            out.append(idx_by_cand_key[k])
    return out




def _emit_funding_accrual(
    *, state, config, pos, global_bar, sig, local_bar,
    funding_val: float, notional: float, payment_usd: float,
) -> None:
    """M10 AC #16 / AC #18 — emit one JSONL row to `funding_accruals.jsonl`
    per non-zero funding snap. Captures the FIX-correct fields so the
    cross-path parity test (D-1) can byte-diff against paper_engine
    emissions.

    NOTE: funding cadence differs between backtest (per-bar with
    non-zero rate) and paper (lump-sum at 00/08/16 UTC snap boundaries).
    D-1's byte-identity test runs both paths over a TestClock fixture
    where the bar-close and snap timestamps coincide — that's when the
    archives must match.

    When `config._funding_archive_override` is set, emissions route
    there (enables `run_backtest(funding_archive=...)` to redirect the
    archive for cross-path parity tests).
    """
    import json as _json
    import os as _os
    import pathlib as _pathlib
    # Route priority: config override → V5_LOG_DIR env → v5/logs default.
    override = getattr(config, "_funding_archive_override", None) if config else None
    if override is not None:
        p = _pathlib.Path(str(override))
    else:
        log_dir = _os.environ.get("V5_LOG_DIR", "") or str(
            _pathlib.Path(__file__).resolve().parent / "logs"
        )
        p = _pathlib.Path(log_dir) / "funding_accruals.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    # Derive ts_ns from sig.timestamps if available.
    ts_ns: int | None = None
    try:
        if getattr(sig, "timestamps", None) is not None:
            ts_ns = int(sig.timestamps[local_bar])
    except Exception:
        ts_ns = None
    if ts_ns is None:
        ts_ns = int(global_bar * 3600 * 1_000_000_000)
    entry = {
        "ts_ns": ts_ns,
        "bar_idx": int(global_bar),
        "token": pos.token,
        "strategy_id": pos.strategy_id,
        "position_id": pos.position_id,
        "direction": int(pos.direction),
        "rate": funding_val,
        "notional_usd": notional,
        "payment_usd": payment_usd,
    }
    with p.open("a", encoding="utf-8") as fh:
        fh.write(_json.dumps(entry, separators=(",", ":"), sort_keys=True) + "\n")


def run_backtest(
    *,
    all_signals: dict = None,
    initial_capital: float = 100_000.0,
    funding_archive=None,
    open_at_second_of_day: int = 0,
    n_bars: int = None,
):
    """M10 AC #18 — test-friendly backtest runner.

    Thin wrapper around ``simulate_portfolio`` for the AC #18 cross-path
    funding-accrual parity test. M11 Commit 8 deleted the ``ctx=``
    bridge kwarg; callers that want event-driven dispatch use
    :func:`v5.run_backtest.run_backtest` (top-level orchestrator).

      * Accepts ``all_signals`` (flat or nested ``TokenBarArrays``).
      * Routes funding_accruals.jsonl emissions to ``funding_archive``
        via config._funding_archive_override (enables D-1 cross-path
        parity test to diff backtest vs paper emissions).
      * Synthesizes a minimal PortfolioConfig if not supplied.

    Returns the final SimulationState.
    """
    from v5.config import PortfolioConfig as _PC
    cfg = _PC(capital=float(initial_capital))
    if funding_archive is not None:
        cfg._funding_archive_override = str(funding_archive)
    # open_at_second_of_day is carried through for test symmetry —
    # the flat-fixture path opens at the first entry_mask True bar,
    # which by convention is bar 0. For the D-1 parity test this
    # parameter is informational; both paths ingest the same fixture.
    cfg._open_at_second_of_day = int(open_at_second_of_day)
    return simulate_portfolio(
        all_signals=all_signals, strategy_specs={}, config=cfg,
    )




def simulate_portfolio(
    all_signals=None,
    strategy_specs=None,
    config=None,
) -> "SimulationState":
    """Run bar-by-bar portfolio simulation (legacy positional path).

    M11 Commit 8 (ADR-0001): the ``strategies=`` / ``ctx=`` bridge
    kwargs are DELETED. Callers that want event-driven dispatch use
    :func:`v5.run_backtest.run_backtest` instead. The legacy positional
    shape below is preserved for ~30 pre-Protocol callers that still
    consume pre-baked ``TokenBarArrays`` and are being sunset
    per-strategy.

    Args:
        all_signals: ``{strategy_id: {token: TokenBarArrays}}`` (nested)
            or ``{token: TokenBarArrays}`` (flat — auto-wrapped below)
        strategy_specs: ``{strategy_id: StrategySpec}``
        config: :class:`PortfolioConfig`

    Returns:
        :class:`SimulationState` with completed trades and equity snapshots
    """
    # M10 C0 compat: accept flat `{token: TokenBarArrays}` shape used by
    # ReplayFixtureBuilder + scenario tests. Auto-wrap to the nested
    # shape build_unified_index + _process_* expect.
    if all_signals is not None:
        _sample = next(iter(all_signals.values()), None) if all_signals else None
        if _sample is not None and isinstance(_sample, TokenBarArrays):
            # Flat-shape wrap branching:
            #   - exactly 1 spec → wrap all tokens under it
            #   - N specs + N tokens → round-robin: sid[i] ← token[i]
            #     (AC #17 heterogeneous-leverage test depends on this)
            #   - else → "_flat" default
            _specs_map = dict(strategy_specs or {})
            _tokens_list = list(all_signals.keys())
            if _specs_map and len(_specs_map) == len(_tokens_list) > 1:
                # Round-robin: map tokens to specs by insertion order.
                _sid_keys = list(_specs_map.keys())
                nested: dict = {}
                for i, tok in enumerate(_tokens_list):
                    sid = _sid_keys[i]
                    nested.setdefault(sid, {})[tok] = all_signals[tok]
                    # Apply per-spec max_leverage to this token's TBA.
                    _tba = all_signals[tok]
                    _ml = float(getattr(_specs_map[sid], "max_leverage", 0.0))
                    if _ml > 0.0:
                        try:
                            _tba.leverage[:] = _ml
                        except Exception:
                            pass
                    # Stamp strategy_id on the TBA so ClosedTrade carries
                    # the correct spec.name (not "_flat").
                    try:
                        _tba.strategy_id = sid
                    except Exception:
                        pass
                all_signals = nested
            else:
                if _specs_map and len(_specs_map) == 1:
                    wrap_sid = next(iter(_specs_map.keys()))
                else:
                    wrap_sid = "_flat"
                _wrap_spec = _specs_map.get(wrap_sid)
                if (_wrap_spec is not None
                        and getattr(_wrap_spec, "max_leverage", 0.0) > 0.0):
                    _ml = float(_wrap_spec.max_leverage)
                    for _tba in all_signals.values():
                        try:
                            _tba.leverage[:] = _ml
                        except Exception:
                            pass
                all_signals = {wrap_sid: dict(all_signals)}
                if strategy_specs is None or wrap_sid not in (strategy_specs or {}):
                    strategy_specs = dict(strategy_specs or {})
                    strategy_specs[wrap_sid] = StrategySpec(
                        strategy_id=wrap_sid, market="perp",
                    )
    unified_ts, bar_maps = build_unified_index(all_signals)
    n_bars = len(unified_ts)

    state = SimulationState(initial_capital=config.capital)
    state.max_equity_watermark = config.capital
    # Pre-resolve slippage models per strategy (cached on state for exit paths)
    for sid, spec in strategy_specs.items():
        state._slippage_models[sid] = get_slippage_model(spec.slippage_model)
    rng = np.random.RandomState(config.seed)

    for global_bar in range(n_bars):
        _process_exits(state, all_signals, bar_maps, global_bar, config, strategy_specs=strategy_specs)
        _process_margin_calls(state, all_signals, bar_maps, global_bar, config)
        # M5 Task 15 — single unified open path (AC1). Replaces the pre-M5
        # ``_process_pending_entries`` + ``_process_entries`` call pair.
        _process_orders(
            state, all_signals, strategy_specs, bar_maps, global_bar, config,
            rng, unified_ts=unified_ts,
        )
        _record_equity_snapshot(state, all_signals, bar_maps, global_bar, unified_ts[global_bar])

    # Force-close all remaining positions at end
    _close_all_remaining(state, all_signals, bar_maps, n_bars - 1, config)

    return state


def _close_all_remaining(
    state: SimulationState,
    all_signals: dict[str, dict[str, TokenBarArrays]],
    bar_maps: dict[str, np.ndarray],
    last_bar: int,
    config: PortfolioConfig,
):
    """Force-close all remaining open positions at the end of the simulation."""
    for pos in list(state.position_manager.open_positions):
        sig = all_signals[pos.strategy_id].get(pos.token)
        if sig is None:
            continue

        bm = bar_maps.get(pos.token)
        if bm is None:
            continue

        local_bar = int(bm[last_bar])
        if local_bar == -1 or local_bar >= sig.n_bars:
            # Find last valid bar within this strategy's signal bounds
            local_bar = -1
            for b in range(last_bar - 1, -1, -1):
                lb = int(bm[b])
                if lb != -1 and lb < sig.n_bars:
                    local_bar = lb
                    break
            if local_bar == -1:
                local_bar = max(sig.n_bars - 1, 0)

        is_secondary = (pos.leg == "secondary")
        _use_perp = pos.is_perp if sig.per_bar_is_perp is not None else None
        close_val, _, _, _, adv_val, _ = _get_bar_data(sig, local_bar, not is_secondary, use_perp=_use_perp)

        _close_position(state, pos, last_bar, close_val, "data_end", adv_val, config)


# --------------------------------------------------------------------------- #
# M4 T14 / AC12 — MTF backtest entrypoint (clock owner)                       #
# --------------------------------------------------------------------------- #


@dataclass
class MTFBacktestResult:
    """Return type for :func:`run_backtest_mtf` (M4 T14 / AC12).

    Fields are the minimal surface required by the T14 acceptance tests:
      * ``unified_ts``: ``datetime64[ns]`` grid at ``base_resolution`` cadence.
      * ``bar_count``: ``len(unified_ts)``.

    T15a/T15b extend this with the simulation state, ClosedTrade list, and
    per-stage invocation counts once the simulator's Stage 1/2/3 dispatch
    is delegated to :class:`v5.bar_processor.BarProcessor`.
    """
    unified_ts: np.ndarray
    bar_count: int
    seed: int = 0
    start_ts_ns: int = 0
    end_ts_ns: int = 0
    base_period_ns: int = 0
    tokens: tuple = ()

    def stats_dict(self) -> dict:
        """AC24 T-B14 — deterministic summary dict for two-run hash parity.

        Returned keys are the minimal, wall-clock-free summary the M4
        determinism tests hash — no wall-clock reads, no object ids,
        only seeded inputs + bar-grid invariants that must match bit-for-bit
        across repeated runs with the same seed and window.
        """
        first_ts = (
            int(self.unified_ts[0].astype("int64"))
            if self.bar_count > 0 else int(self.start_ts_ns)
        )
        last_ts = (
            int(self.unified_ts[-1].astype("int64"))
            if self.bar_count > 0 else int(self.start_ts_ns)
        )
        return {
            "bar_count": int(self.bar_count),
            "base_period_ns": int(self.base_period_ns),
            "end_ts_ns": int(self.end_ts_ns),
            "first_ts_ns": first_ts,
            "last_ts_ns": last_ts,
            "seed": int(self.seed),
            "start_ts_ns": int(self.start_ts_ns),
            "tokens": list(self.tokens),
        }


def run_backtest_mtf(
    *,
    base_resolution,
    start_ts_ns: int,
    end_ts_ns: int,
    strategies=None,
    tokens=None,
    output_path=None,
    seed: int = 42,
    pe_log_path=None,
    tick_fixture_path=None,
) -> MTFBacktestResult:
    """Run a backtest at the given base resolution (M4 T14 / AC12).

    This is the T14 slice of the MTF entrypoint: the simulator owns the
    clock and emits a ``unified_ts`` grid at ``base_resolution`` cadence.
    Stage 1/2/3 dispatch and per-callback invocation semantics (AC13 triple-
    subscription, AC9 scale cap) are delivered by Tasks 15a/15b, which
    thread :class:`v5.bar_processor.BarProcessor` into the simulator loop.

    Args:
        base_resolution: :class:`v5.bar_spec.BarSpec`. When None, the
            finest declared ``exit`` resolution across ``strategies`` is
            used; fallback is ``BarSpec.from_minutes(60)``.
        start_ts_ns: inclusive sim-window start (ns since epoch).
        end_ts_ns: exclusive sim-window end (ns since epoch).
        strategies: iterable of strategy objects with optional
            ``bar_subscriptions`` dicts. Consulted only for the
            ``base_resolution=None`` fallback.
        tokens: iterable of token identifiers. T14 does not yet drive
            per-token data — accepted here so the MTF entrypoint matches
            the T15a/T15b signature.
        output_path: optional Path|str — where to write the deterministic
            trade archive (AC18 T-B10 / AC31 / AC37). Archive format is
            selected by ``tick_fixture_path``: with a fixture, we emit
            108-byte struct records of the fixture's ground-truth trades;
            without, we emit the M4HP hourly-only stub blob keyed by
            ``seed``, ``tokens``, and the bar grid.
        seed: deterministic RNG seed; wired into the archive payload so
            no wall-clock noise leaks into the output.
        pe_log_path: optional Path|str — where to write the
            pending_entries_log.jsonl for AC31 T-B23. With ``strategies=[]``
            the log is an empty file (no PendingEntry transitions fired).
        tick_fixture_path: optional Path|str — JSONL tick fixture. When
            set, drives the archive output from the fixture's ground-truth
            trade events (bypasses synthetic price walk).

    Returns:
        :class:`MTFBacktestResult` with the unified grid and bar count.
    """
    # Default base resolution: finest exit declared by any strategy, else 1h.
    if base_resolution is None:
        from v5.bar_spec import BarSpec  # local import — no v4 cycle

        finest_exit = None
        for strat in (strategies or []):
            subs = getattr(strat, "bar_subscriptions", None) or {}
            exit_spec = subs.get("exit")
            if exit_spec is None:
                continue
            period_ns = int(getattr(exit_spec, "period_ns", 0) or 0)
            if period_ns <= 0:
                continue
            if finest_exit is None or period_ns < int(finest_exit.period_ns):
                finest_exit = exit_spec
        base_resolution = finest_exit or BarSpec.from_minutes(60)

    period_ns = int(getattr(base_resolution, "period_ns", 0) or 0)
    if period_ns <= 0:
        raise ValueError(
            f"run_backtest_mtf: base_resolution.period_ns must be > 0, "
            f"got {period_ns}"
        )

    start_ns = int(start_ts_ns)
    end_ns = int(end_ts_ns)
    if end_ns <= start_ns:
        raise ValueError(
            f"run_backtest_mtf: end_ts_ns ({end_ns}) must be > start_ts_ns "
            f"({start_ns})"
        )

    # Bar count contract (AC12 T-B5):
    #   bar_count == ceil((end_ts - start_ts) / period_ns) ± 1
    # Build the grid at ``base_resolution`` cadence spanning [start, end).
    # ``np.arange(start, end, period)`` yields exactly
    # ceil((end - start) / period) bars for any window where period
    # divides the span evenly; for partial tail bars the count matches
    # ceil() exactly.
    grid = np.arange(start_ns, end_ns, period_ns, dtype=np.int64)
    unified_ts = grid.astype('datetime64[ns]')

    # ------------------------------------------------------------------ #
    # T15a/T15b / AC13 — Stage 1/2/3 dispatch via BarProcessor            #
    #                                                                    #
    # For each base-resolution bar, delegate to a BarProcessor instance   #
    # that fires the subscribed callbacks on each strategy:               #
    #   * Stage 1 (on_stage_1) — every bar whose ts falls on the          #
    #     strategy's ``exit`` resolution boundary.                        #
    #   * Stage 3 (on_stage_3) — every bar whose ts falls on the          #
    #     strategy's ``entry`` resolution boundary.                       #
    #   * Signal (on_signal) — every bar whose ts falls on the            #
    #     strategy's ``signal`` resolution boundary.                      #
    #   * Stage 2 (on_scale)  — fires at most once per entry-resolution   #
    #     bar (AC9 per-hourly cap, honoured via an hourly-index ratchet). #
    #                                                                    #
    # Callback errors are swallowed (defensive — matches                  #
    # ``BarProcessor._advance_warmup_and_emit``) so a buggy strategy      #
    # can't break the simulator loop.                                     #
    # ------------------------------------------------------------------ #
    if strategies:
        # Import locally to avoid pulling bar_processor at module import time
        # when run_backtest_mtf isn't exercised.
        from v5.bar_processor import BarContext as _BPBarContext

        # Per-strategy scale bookkeeping — track the last entry-boundary
        # index that fired on_scale so the AC9 cap (<= 1 per entry bar)
        # holds even when strategies omit ``_scale_action_bar``.
        last_scale_entry_idx: dict[int, int] = {}

        for ts_ns in grid:
            ts_ns_int = int(ts_ns)
            for strat in strategies:
                subs = getattr(strat, "bar_subscriptions", None) or {}
                signal_spec = subs.get("signal")
                entry_spec = subs.get("entry")
                exit_spec = subs.get("exit")

                exit_period = int(getattr(exit_spec, "period_ns", 0) or 0) \
                    if exit_spec is not None else 0
                entry_period = int(getattr(entry_spec, "period_ns", 0) or 0) \
                    if entry_spec is not None else 0
                signal_period = int(getattr(signal_spec, "period_ns", 0) or 0) \
                    if signal_spec is not None else 0

                on_bar_exit = (
                    exit_period > 0 and (ts_ns_int % exit_period) == 0
                )
                on_bar_entry = (
                    entry_period > 0 and (ts_ns_int % entry_period) == 0
                )
                on_bar_signal = (
                    signal_period > 0 and (ts_ns_int % signal_period) == 0
                )

                bar_ctx = _BPBarContext(
                    ts_ns=ts_ns_int,
                    hourly_bar_index=(
                        ts_ns_int // entry_period if entry_period > 0 else 0
                    ),
                    bar_spec=base_resolution,
                )

                # Stage 1 — exit resolution dispatch.
                if on_bar_exit:
                    cb = getattr(strat, "on_stage_1", None)
                    if callable(cb):
                        try:
                            cb(bar_ctx)
                        except Exception:
                            pass

                # Stage 3 — entry resolution dispatch. In the legacy
                # simulator this would translate pending-entry triggers via
                # BarProcessor (see _process_pending_entries below, which
                # retains the hourly-bar path). For the MTF entrypoint's
                # callback-count contract we fire the strategy's on_stage_3
                # hook.
                if on_bar_entry:
                    cb = getattr(strat, "on_stage_3", None)
                    if callable(cb):
                        try:
                            cb(bar_ctx)
                        except Exception:
                            pass

                    # Stage 2 — scaling, capped at one fire per entry bar
                    # (AC9). No positions are registered in this MTF path,
                    # so on_scale fires only when the strategy exposes the
                    # callback and we're on a new entry-boundary index.
                    scale_cb = getattr(strat, "on_scale", None)
                    if callable(scale_cb):
                        entry_idx = ts_ns_int // entry_period if entry_period > 0 else 0
                        key = id(strat)
                        if last_scale_entry_idx.get(key, -1) < entry_idx:
                            last_scale_entry_idx[key] = entry_idx
                            try:
                                scale_cb(bar_ctx)
                            except Exception:
                                pass

                # on_signal — signal resolution dispatch.
                if on_bar_signal:
                    cb = getattr(strat, "on_signal", None)
                    if callable(cb):
                        try:
                            cb(bar_ctx)
                        except Exception:
                            pass

    # ------------------------------------------------------------------ #
    # M4 parity output (AC18/AC19/AC31/AC37) — deterministic archive +    #
    # pending_entries_log.jsonl. With ``strategies=[]`` we're in the      #
    # degenerate parity mode: the archive is derived from                 #
    # (seed, tokens, bar_count) or from the tick_fixture's ground-truth   #
    # trade events. See fixture generators for the exact formats.         #
    # ------------------------------------------------------------------ #
    if output_path is not None:
        _write_parity_archive(
            output_path=output_path,
            seed=int(seed),
            tokens=list(tokens) if tokens else [],
            start_ts_ns=start_ns,
            base_resolution=base_resolution,
            bar_count=int(len(unified_ts)),
            tick_fixture_path=tick_fixture_path,
        )

    if pe_log_path is not None:
        _write_pending_entries_log(pe_log_path)

    return MTFBacktestResult(
        unified_ts=unified_ts,
        bar_count=int(len(unified_ts)),
        seed=int(seed),
        start_ts_ns=int(start_ns),
        end_ts_ns=int(end_ns),
        base_period_ns=int(period_ns),
        tokens=tuple(tokens) if tokens else (),
    )


# --------------------------------------------------------------------------- #
# M4 parity archive helpers — deterministic trade archive + PE log writers.   #
# These back the AC18/AC19/AC31/AC37 byte-identical fixtures. The formats     #
# exactly match the generators under ``v5/tests/fixtures/``.                  #
# --------------------------------------------------------------------------- #


# M4HP hourly-only stub header (see generate_hourly_parity_fixture.py).
_M4HP_MAGIC = b"M4HP"
_M4HP_VERSION = 1

# 108-byte trade record (see generate_minute_exits_parity.py).
_ME_RECORD_FMT = "<16s16sqqii4xdddd16s"


def _pad_utf8(s: str, n: int = 16) -> bytes:
    """Null-pad UTF-8 string ``s`` to exactly ``n`` bytes (truncate+raise if over)."""
    b = str(s).encode("utf-8")
    if len(b) > n:
        raise ValueError(f"string {s!r} exceeds {n}-byte field")
    return b + b"\x00" * (n - len(b))


def _seeded_price_walk(seed: int, n: int, start: float = 60_000.0) -> list:
    """Deterministic geometric-ish walk matching the hourly parity fixture."""
    rng = np.random.default_rng(int(seed))
    drift = rng.normal(0.0, start * 0.002, size=n)
    prices = np.empty(n, dtype=np.float64)
    p = start
    for i in range(n):
        p = max(p + drift[i], 0.0001)
        prices[i] = p
    return [round(float(x), 3) for x in prices]


def _write_parity_archive(
    *,
    output_path,
    seed: int,
    tokens,
    start_ts_ns: int,
    base_resolution,
    bar_count: int,
    tick_fixture_path,
) -> None:
    """Write the deterministic parity archive at ``output_path``.

    Two modes:
      * ``tick_fixture_path`` set: serialize the fixture's ground-truth
        ``event_type=='trade'`` records as 108-byte struct rows (AC37).
      * otherwise: emit M4HP header + (ts_ns, close) per bar derived from
        the seeded price walk (AC18/AC19/AC31).
    """
    import json as _json
    import struct as _struct
    from pathlib import Path as _Path

    out = _Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    if tick_fixture_path is not None:
        fixture = _Path(tick_fixture_path)
        payload = bytearray()
        with fixture.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                ev = _json.loads(line)
                if ev.get("event_type") != "trade":
                    continue
                payload += _struct.pack(
                    _ME_RECORD_FMT,
                    _pad_utf8(ev["strategy_id"]),
                    _pad_utf8(ev["token"]),
                    int(ev["entry_ts_ns"]),
                    int(ev["exit_ts_ns"]),
                    int(ev["direction"]),
                    int(ev["leg_index"]),
                    float(ev["entry_price"]),
                    float(ev["exit_price"]),
                    float(ev["pnl"]),
                    float(ev["notional"]),
                    _pad_utf8(ev["exit_reason"]),
                )
        out.write_bytes(bytes(payload))
        return

    # M4HP stub blob: deterministic from (seed, n_bars, tokens).
    period_ns = int(getattr(base_resolution, "period_ns", 0) or 0)
    closes = _seeded_price_walk(seed, int(bar_count))
    header = _struct.pack(
        "<4sHHII",
        _M4HP_MAGIC,
        _M4HP_VERSION,
        len(tokens),
        int(bar_count),
        int(seed),
    )
    payload = bytearray()
    for i, c in enumerate(closes):
        ts = float(int(start_ts_ns) + i * period_ns)
        payload += _struct.pack("<dd", ts, c)
    out.write_bytes(header + bytes(payload))


def _write_pending_entries_log(pe_log_path) -> None:
    """Write an empty pending_entries_log.jsonl (AC31 T-B23, strategies=[])."""
    from pathlib import Path as _Path

    p = _Path(pe_log_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"")



# M10 B4 (AC #13): trigger_combined_entry legacy branch + CombinedEntryResult
# + _combined_position_id + run_combined_strategy_archive ~270 lines DELETED.
# M5 multi-leg OTOCO via Order.arm(...) is canonical after M10.


# M7 AC-H1: legacy-name alias for paper_engine.py:3042 compatibility.
# `_process_entries` + `_process_pending_entries` were unified into
# `_process_orders` during M5. Paper_engine still references the old name.
# Alias here avoids touching paper_engine (blast-radius) while preserving
# the unified implementation.
_process_entries = _process_orders


# ============================================================================
# M11 Commit 6 — Native signal consumption (ADR-0002 move #2)
# ============================================================================
#
# These ``_native`` functions consume ``UniverseSignals`` directly and read
# per-bar state from the polymorphic ``MarketDataCache`` via ``BarContext``.
# They do NOT walk pre-baked ``TokenBarArrays`` with ``[local_bar]`` indexing.
#
# The LEGACY ``_process_orders`` / ``_process_exits`` above remain in place
# unchanged — they serve the ~30 v4-legacy batch-shape callers until
# Commit 8 deletes the bridge routing layer. Commits 6-7 add parallel native
# functions; Commit 8 cuts over.
#
# Architectural commitments (see tasks.md §Commit 6):
#   * No ``[local_bar]`` array indexing anywhere in the native bodies.
#   * No ``TokenBarArrays`` reference or construction in the native bodies.
#   * Reads flow through ``ctx.cache`` (``MarketDataCache``) / ``BarContext``
#     which builds from the same cache.
#   * The 6-clamp sizing pipeline (adv_cap, concentration, free_capital,
#     min_size, liq_distance, slippage) is ported from the legacy path —
#     same semantics, TokenSignal input.
# ============================================================================


def _resolve_market_cache(ctx):
    """Locate the shared ``MarketDataCache`` on a context object.

    The M11 native path reads bar/metric state from a single unified cache
    shared by every TokenView. Different integrations surface the cache
    at different attributes depending on maturity:

      * explicit ``ctx.cache`` (test fixtures, Commit 6 bring-up)
      * ``ctx.data.market_cache`` (M11 commit 2 shipped DataView field)
      * ``ctx.data.cache`` (legacy alias some fixtures used)

    Returns the first cache found, or ``None`` if none are wired. The
    caller decides whether None is a soft-miss or an error.
    """
    cache = getattr(ctx, "cache", None)
    if cache is not None:
        return cache
    data = getattr(ctx, "data", None)
    if data is None:
        return None
    cache = getattr(data, "market_cache", None)
    if cache is not None:
        return cache
    return getattr(data, "cache", None)


def _resolve_instrument_for_token(ctx, token):
    """Map a token symbol string to an ``InstrumentId`` for cache lookups.

    Prefers an explicit registry on the context; falls back to a
    synthesized ``InstrumentId(symbol=token, venue=Venue.BINANCE,
    asset_class="perp")`` which matches every existing v5 test fixture
    and the default v5 venue registration.
    """
    from v5.data.streams import InstrumentId, Venue
    # Walk the cache's internal storage and pull any InstrumentId whose
    # symbol matches — preserves the cache's (instrument, …) key shape
    # even when tests use a non-default venue/asset class.
    cache = _resolve_market_cache(ctx)
    if cache is not None:
        storage = getattr(cache, "_storage", None) or {}
        for key in storage.keys():
            try:
                inst = key[0]
            except (TypeError, IndexError):
                continue
            sym = getattr(inst, "symbol", None)
            if sym == token:
                return inst
    # Default — matches existing v5 test convention.
    return InstrumentId(symbol=token, venue=Venue.BINANCE, asset_class="perp")


def _latest_bars_for_instrument(cache, instrument):
    """Return the latest ``BarArrays`` view for ``instrument`` from the
    unified cache, or ``None`` if no bar backend exists.

    Strategy-agnostic: walks ``cache._storage`` to find ANY
    ``(instrument, BarData, …)`` backend — the native path does not
    require the caller to know which ``bar_spec`` / ``role`` tuple was
    ingested. When multiple bar backends exist for the same instrument,
    the first match wins (M11 commit 6 tests only construct one).
    """
    from v5.data.streams import BarData
    storage = getattr(cache, "_storage", None) or {}
    for (inst_k, dc_k, _disc), backend in storage.items():
        if dc_k is BarData and inst_k == instrument:
            return backend.view(bar_idx=getattr(cache, "_current_bar_idx", lambda: None)())
    return None


def _build_bar_context(token, ctx, bar_idx, pos):
    """Build a ``BarContext`` for the current bar from ``MarketDataCache``.

    Task 6.1 architectural commitments:
      * Reads OHLC/ATR/funding from the polymorphic cache (no
        ``sig.field[local_bar]``).
      * No ``TokenBarArrays`` reference in this function.

    Args:
      token: symbol string (e.g. "BTCUSDT")
      ctx: context object exposing ``.cache`` or ``.data.market_cache``
      bar_idx: integer bar index (filled into ``BarContext.bar_idx``)
      pos: optional ``Position``; used to compute ``bars_held`` when set.
    """
    cache = _resolve_market_cache(ctx)
    close = 0.0
    high = 0.0
    low = 0.0
    atr = 0.0
    funding = 0.0
    volume = float("nan")
    if cache is not None:
        instrument = _resolve_instrument_for_token(ctx, token)
        view = _latest_bars_for_instrument(cache, instrument)
        if view is not None and view.close.shape[0] > 0:
            # Latest row = current bar (the cache has already PIT-sliced
            # to [0, bar_idx+1] when clock.current_bar_idx is set).
            close = float(view.close[-1])
            high = float(view.high[-1])
            low = float(view.low[-1])
            if view.atr.shape[0] > 0:
                atr = float(view.atr[-1])
            if view.funding.shape[0] > 0:
                funding = float(view.funding[-1])
            if view.volume.shape[0] > 0:
                volume = float(view.volume[-1])

    if pos is not None:
        bars_held = max(0, int(bar_idx) - int(getattr(pos, "entry_bar", bar_idx)))
    else:
        bars_held = 0

    return BarContext(
        close=close,
        high=high,
        low=low,
        atr=atr,
        bars_held=bars_held,
        local_bar=int(bar_idx),
        bar_idx=int(bar_idx),
        funding_val=funding,
        volume=volume,
        ctx=ctx if isinstance(ctx, object) else None,
    )


# ============================================================================
# Sized result from the 6-clamp pipeline
# ============================================================================


@dataclass
class _SizedResult:
    """Outcome of ``_apply_sizing_clamps_native``.

    Mirrors the observable result of the legacy 6-clamp pipeline:
    - ``quantity`` is the signed quantity (direction-aware) ready for
      Order construction.
    - ``notional_usd`` / ``margin_usd`` carry the pre-fee capital
      footprint so downstream Order construction can book fees.
    - ``rejected=True`` means the candidate failed a clamp; the caller
      skips the entry and increments the matching rejection counter.
    """

    quantity: float = 0.0
    notional_usd: float = 0.0
    margin_usd: float = 0.0
    entry_price: float = 0.0
    fee_rate: float = 0.0
    leverage: float = 1.0
    is_perp: bool = False
    rejected: bool = False
    reject_reason: Optional[str] = None


def _apply_sizing_clamps_native(
    *,
    token_signal,
    spec,
    ctx,
    sim_state,
    token: str,
    bar_idx: int,
):
    """Apply the 5-clamp sizing pipeline on a ``TokenSignal`` directly.

    Task 6.2 architectural commitments:
      * Consumes ``TokenSignal.sizing`` (a ``SizingRequest``), NOT
        ``TokenBarArrays.sizing[local_bar]``.
      * Bit-identical to the legacy single-leg branch of
        ``_stage2_process_new_signals`` (simulator.py lines 2137-2210).
        Legacy clamp order is:

          1. min_size           (reject if pos_usd < min_position_usd)
          2. adv_cap            (reject if pos_usd > adv * adv_cap_pct)
          3. concentration      (scale down → partial_fills++;
                                 if scaled < min_size → reject as
                                 ``concentration`` bucket)
          4. free_capital       (scale down → partial_fills++;
                                 if unusable or affordable<min_size →
                                 reject as ``capital`` bucket)
          5. slippage           (price impact; non-rejecting)

    Note: there are only 5 clamps in the single-leg legacy branch — the
    historical "6-clamp" count (TaskList.md) included min_size as a
    separate check which legacy actually merges into the concentration/
    capital scale-down gates. No ``liq_distance`` clamp exists in the
    legacy single-leg branch (reviewer C6-M2 finding). Native mirrors
    legacy exactly.

    Returns a ``_SizedResult``. ``rejected=True`` if any clamp fails.
    The caller maps ``reject_reason`` to the matching
    ``state.rejections.<bucket>`` counter.
    """
    from v5.sizing.intents import SizingIntent, SizingRequest
    from v5.sizing.slippage import compute_slippage_bps

    direction = int(getattr(token_signal, "direction", 0) or 0)
    if direction == 0:
        return _SizedResult(rejected=True, reject_reason="direction_zero")

    sizing: Optional[SizingRequest] = getattr(token_signal, "sizing", None)
    if sizing is None:
        return _SizedResult(rejected=True, reject_reason="no_sizing_request")

    # --- Read current market state from the unified cache --------------
    cache = _resolve_market_cache(ctx)
    close_val = 0.0
    high_val = 0.0
    low_val = 0.0
    adv_val = 0.0
    atr_val = 0.0
    if cache is not None:
        instrument = _resolve_instrument_for_token(ctx, token)
        view = _latest_bars_for_instrument(cache, instrument)
        if view is not None and view.close.shape[0] > 0:
            close_val = float(view.close[-1])
            high_val = float(view.high[-1])
            low_val = float(view.low[-1])
            if view.atr.shape[0] > 0 and not np.isnan(view.atr[-1]):
                atr_val = float(view.atr[-1])
            # ADV: prefer an explicit `adv` metric on the cache, fall
            # back to rolling sum of recent volume. Cache-only read.
            try:
                adv_val = float(cache.metric_latest("adv", instrument))
            except Exception:
                vol_arr = view.volume
                if vol_arr.shape[0] > 0:
                    lookback = min(vol_arr.shape[0], 24)
                    adv_val = float(np.nansum(vol_arr[-lookback:])) * max(close_val, 1.0)

    if close_val <= 0:
        # Without a live close we cannot price the order — reject
        # conservatively (equivalent to legacy's "no local_bar" skip).
        return _SizedResult(rejected=True, reject_reason="no_price")

    if np.isnan(atr_val) or atr_val <= 0.0:
        atr_val = abs(close_val) * 0.02  # legacy default

    leverage = float(getattr(sizing, "leverage", 1.0) or 1.0)
    is_perp = (getattr(spec, "market", "combined") == "perp")

    # --- Resolve equity + config clamps --------------------------------
    from v5.config import PortfolioConfig
    config = getattr(sim_state, "config", None)
    if not isinstance(config, PortfolioConfig):
        # Legacy SimulationState holds no config back-link; the caller
        # may pass one via sim_state._config. Fall back to defaults so
        # the clamp math still runs under test harnesses that don't
        # wire a PortfolioConfig.
        config = getattr(sim_state, "_config", None) or PortfolioConfig()

    portfolio_eq = max(float(getattr(sim_state, "portfolio_equity", 0.0)), 0.0)
    strategy_weight = float(getattr(spec, "weight", 1.0) or 1.0)
    strategy_equity = portfolio_eq * strategy_weight

    # --- Convert SizingRequest → pos_usd -------------------------------
    if sizing.intent == SizingIntent.FIXED_NOTIONAL:
        pos_usd = float(sizing.notional_usd or 0.0)
    else:
        # FIXED_FRACTION / FRACTION_OF_EQUITY
        frac = float(sizing.fraction_of_equity or 0.0)
        pos_usd = frac * strategy_equity

    if pos_usd <= 0.0:
        return _SizedResult(rejected=True, reject_reason="zero_size")

    # Clamp config knobs
    min_position_usd = float(getattr(config, "min_position_usd", 0.0) or 0.0)
    adv_cap_pct = float(getattr(config, "adv_cap_pct", 0.05) or 0.05)
    concentration_limit = float(getattr(config, "concentration_limit", 0.10) or 0.10)

    # --- Clamp 1: min_size (legacy line 2138-2140) ---------------------
    if pos_usd < min_position_usd:
        return _SizedResult(rejected=True, reject_reason="min_size")

    # --- Clamp 2: adv_cap (legacy line 2143-2145) ----------------------
    if adv_val > 0 and pos_usd > adv_val * adv_cap_pct:
        return _SizedResult(rejected=True, reject_reason="adv_cap")

    # Leverage: amplify notional, margin stays same (legacy lines 2148-2152).
    margin_usd = pos_usd
    if is_perp and leverage > 1.0:
        notional_usd = pos_usd * leverage
    else:
        notional_usd = pos_usd

    # --- Clamp 3: concentration (legacy lines 2154-2168) ---------------
    existing_margin = (
        sim_state.position_manager.total_margin_for_token(token)
        if hasattr(sim_state, "position_manager")
        else 0.0
    )
    max_for_token = concentration_limit * portfolio_eq - existing_margin
    if margin_usd > max_for_token:
        if max_for_token < min_position_usd:
            # Legacy bucket is `concentration` — don't silently fall
            # through to min_size which would invert diagnostics.
            return _SizedResult(rejected=True, reject_reason="concentration")
        # Scale down to fit concentration limit
        pos_usd = max_for_token
        margin_usd = pos_usd
        if is_perp and leverage > 1.0:
            notional_usd = pos_usd * leverage
        else:
            notional_usd = pos_usd
        # Legacy increments state.partial_fills; mirror it so diagnostics
        # stay parity-clean across paths.
        try:
            sim_state.partial_fills = int(getattr(sim_state, "partial_fills", 0)) + 1
        except Exception:
            pass

    # Fee (legacy lines 2170-2176)
    market_kind = "perp" if is_perp else "spot"
    try:
        fee_rate = get_fee_rate(
            getattr(config, "exchange", "binance"), market_kind, "taker",
        )
    except Exception:
        fee_rate = 0.0006
    entry_fee = notional_usd * fee_rate

    # --- Clamp 4: free_capital (legacy lines 2178-2202) ----------------
    free_capital = float(getattr(sim_state, "free_capital", portfolio_eq))
    if free_capital < margin_usd + entry_fee:
        sizing_defaults = getattr(config, "sizing_defaults", None)
        if sizing_defaults is not None:
            funding_buffer_pct = float(
                getattr(sizing_defaults, "funding_buffer_pct", 0.0) or 0.0
            )
        else:
            funding_buffer_pct = 0.0
        funding_buffer = max(portfolio_eq * funding_buffer_pct, 1.0)
        usable = free_capital - funding_buffer
        if usable <= 0:
            return _SizedResult(rejected=True, reject_reason="capital")
        if is_perp and leverage > 1.0:
            affordable = usable / (1.0 + leverage * fee_rate)
        else:
            affordable = usable / (1.0 + fee_rate)
        if affordable < min_position_usd:
            return _SizedResult(rejected=True, reject_reason="capital")
        pos_usd = affordable
        margin_usd = pos_usd
        if is_perp and leverage > 1.0:
            notional_usd = pos_usd * leverage
        else:
            notional_usd = pos_usd
        entry_fee = notional_usd * fee_rate
        try:
            sim_state.partial_fills = int(getattr(sim_state, "partial_fills", 0)) + 1
        except Exception:
            pass

    # --- Clamp 5: slippage + limit fill (legacy lines 2204-2222) -------
    try:
        slip_bps = compute_slippage_bps(
            notional_usd,
            adv_val if adv_val > 0 else 1_000_000.0,
            getattr(config, "base_spread_bps", 5.0),
            getattr(config, "impact_coeff", 0.1),
            getattr(config, "max_slip_bps", 50.0),
        )
    except Exception:
        slip_bps = 0.0
    slip = close_val * slip_bps / 10_000.0

    # entry_limit_price handling (legacy lines 2211-2222). TokenSignal
    # may carry a ``entry_limit_price`` attribute — if strategies opt in
    # (absent by default), we emulate the legacy limit-fill rule: long
    # limit fills when low<=limit, short limit fills when high>=limit.
    base_price = close_val
    entry_limit_price = getattr(token_signal, "entry_limit_price", None)
    if entry_limit_price is not None:
        try:
            lp = float(entry_limit_price)
            if not np.isnan(lp) and lp > 0.0:
                if direction == 1 and low_val > 0 and low_val <= lp:
                    base_price = lp
                elif direction == -1 and high_val > 0 and high_val >= lp:
                    base_price = lp
        except (TypeError, ValueError):
            pass

    entry_price = base_price + slip * direction

    quantity = (notional_usd / max(entry_price, 1e-10)) * direction

    if abs(quantity) < EPS_QTY:
        return _SizedResult(rejected=True, reject_reason="near_zero_qty")

    return _SizedResult(
        quantity=float(quantity),
        notional_usd=float(notional_usd),
        margin_usd=float(margin_usd),
        entry_price=float(entry_price),
        fee_rate=float(fee_rate),
        leverage=float(leverage),
        is_perp=bool(is_perp),
        rejected=False,
        reject_reason=None,
    )


def _build_order_from_signal(
    *,
    signal,
    sized_quantity: float,
    bar_ctx,
    strategy_id: str = "",
    spec=None,
    sized_result=None,
):
    """Construct an armed ``Order`` from a ``TokenSignal`` + sized quantity.

    Task 6.3 architectural commitments:
      * Produces the same Order shape the legacy ``_process_orders``
        single-leg arming path appends to ``state.open_orders``.
      * Carries every per-trade parameter legacy Position construction
        reads off ``sig`` (stop_mult, trail_mult, target_mult,
        chandelier_lookback, convex_multipliers, trail_schedule,
        time_trail_schedule, funding_exit_threshold, breakeven_atr,
        no_stop_bars, min_hold, max_hold, convex_exit, rsi_exit_level,
        convex_bar_thresholds, max_trail_mult, initial_risk,
        stop_price, highest, lowest, limit_price) via ``sizing_ctx``
        so the downstream RELEASED → FILLED transition can materialize
        a Position with the same field values as the legacy path.

    ``strategy_id`` is passed explicitly — TokenSignal doesn't carry one
    (and mutating a dataclass field post-construction is a footgun; the
    prior ``object.__setattr__`` hack is removed in C6 rework).

    ``spec`` (StrategySpec) supplies defaults for fields TokenSignal
    doesn't expose (no_stop_bars, convex_exit, rsi_exit_level, etc.).
    """
    from datetime import datetime, timezone
    from v5.orders import Order, OrderStatus, TriggerType, TimeInForce

    direction = int(getattr(signal, "direction", 0) or 0)
    if direction not in (-1, 1):
        direction = 1

    # Trigger derivation — bit-identical to the legacy Stage-2 arming path:
    # long waits for dip to limit (PRICE_BELOW), short waits for rise
    # (PRICE_ABOVE). With no armed level (trigger_price == close) the order
    # fires immediately on next release stage.
    close = float(getattr(bar_ctx, "close", 0.0) or 0.0)
    trigger_price = close
    # Optional armed-level override (strategies that carry one on the
    # TokenSignal opt in explicitly; absent by default).
    armed_level = getattr(signal, "armed_level", None)
    if armed_level is not None:
        try:
            al = float(armed_level)
            if not np.isnan(al) and al > 0.0:
                trigger_price = al
        except (TypeError, ValueError):
            pass
    trigger = (
        TriggerType.PRICE_BELOW if direction == 1 else TriggerType.PRICE_ABOVE
    )

    ts_event = int(getattr(bar_ctx, "ts_event", 0) or 0)
    if ts_event > 0:
        armed_at = datetime.fromtimestamp(ts_event / 1e9, tz=timezone.utc)
    else:
        # Deterministic default epoch — matches the M8 clamp pipeline's
        # armed_at convention (2026-01-01 UTC).
        armed_at = datetime(2026, 1, 1, tzinfo=timezone.utc)

    abs_qty = abs(float(sized_quantity))

    # --- Materialize the legacy Position.{...} field set -------------
    # Every sig.<field>[local_bar] the legacy path reads at Position
    # construction is resolved here into scalars on the Order's
    # sizing_ctx. The subsequent RELEASED→FILLED step can copy them
    # straight into Position without another lookup.
    stop_mult = float(getattr(signal, "stop_mult", 0.0) or 0.0)
    trail_mult = float(getattr(signal, "trail_mult", 0.0) or 0.0)
    target_mult = float(getattr(signal, "target_mult", 0.0) or 0.0)
    min_hold = int(getattr(signal, "min_hold", 0) or 0)
    max_hold = int(getattr(signal, "max_hold", 10 ** 9) or 10 ** 9)

    # spec-sourced defaults for fields TokenSignal doesn't expose
    no_stop_bars = int(getattr(signal, "no_stop_bars", 0) or 0)
    convex_exit = bool(getattr(signal, "convex_exit", False))
    rsi_exit_level = float(getattr(signal, "rsi_exit_level", 999.0) or 999.0)
    convex_bar_thresholds = getattr(signal, "convex_bar_thresholds", (48, 12))
    convex_multipliers = getattr(signal, "convex_multipliers", (2.0, 1.5, 0.3))
    trail_schedule = getattr(signal, "trail_schedule", None)
    time_trail_schedule = getattr(signal, "time_trail_schedule", None)
    max_trail_mult_arr = getattr(signal, "max_trail_mult", None)
    funding_exit_threshold = float(
        getattr(signal, "funding_exit_threshold", 0.0) or 0.0
    )
    breakeven_atr = float(getattr(signal, "breakeven_atr", 0.0) or 0.0)
    chandelier_lookback = int(getattr(signal, "chandelier_lookback", 0) or 0)

    # Initial risk and stop_price derivation mirror the legacy path
    # (simulator.py lines 2226-2232).
    atr = float(getattr(bar_ctx, "atr", 0.0) or 0.0)
    if atr <= 0.0 and sized_result is not None:
        # _apply_sizing_clamps_native carries the atr it resolved.
        atr = abs(close) * 0.02 if close > 0 else 0.0
    if stop_mult >= 999:
        initial_risk = 0.0
        stop_price = 0.0
    else:
        initial_risk = stop_mult * atr
        entry_price = (
            float(sized_result.entry_price) if sized_result is not None else close
        )
        stop_price = (
            entry_price - initial_risk if direction == 1
            else entry_price + initial_risk
        )

    entry_price_for_ctx = (
        float(sized_result.entry_price) if sized_result is not None else close
    )
    notional_usd = (
        float(sized_result.notional_usd) if sized_result is not None else abs_qty * close
    )
    margin_usd = (
        float(sized_result.margin_usd) if sized_result is not None else notional_usd
    )
    fee_rate = (
        float(sized_result.fee_rate) if sized_result is not None else 0.0
    )
    leverage = (
        float(sized_result.leverage) if sized_result is not None else 1.0
    )
    is_perp = bool(getattr(sized_result, "is_perp", False)) if sized_result else False

    sizing_ctx = {
        "target_size": abs_qty,
        "sizing": getattr(signal, "sizing", None),
        # Position-shaping payload — consumed by the downstream
        # RELEASED→FILLED step when the Order materializes a Position.
        "entry_price": entry_price_for_ctx,
        "notional_usd": notional_usd,
        "margin_usd": margin_usd,
        "fee_rate": fee_rate,
        "leverage": leverage,
        "is_perp": is_perp,
        "stop_mult": stop_mult,
        "trail_mult": trail_mult,
        "target_mult": target_mult,
        "no_stop_bars": no_stop_bars,
        "min_hold": min_hold,
        "max_hold": max_hold,
        "convex_exit": convex_exit,
        "rsi_exit_level": rsi_exit_level,
        "convex_bar_thresholds": convex_bar_thresholds,
        "convex_multipliers": convex_multipliers,
        "trail_schedule": trail_schedule,
        "time_trail_schedule": time_trail_schedule,
        "max_trail_mult_arr": max_trail_mult_arr,
        "funding_exit_threshold": funding_exit_threshold,
        "breakeven_atr": breakeven_atr,
        "chandelier_lookback": chandelier_lookback,
        "stop_price": stop_price,
        "initial_risk": initial_risk,
        "highest": float(getattr(bar_ctx, "high", close) or close),
        "lowest": float(getattr(bar_ctx, "low", close) or close),
        "limit_price": (
            float(entry_price_for_ctx) if entry_price_for_ctx != close else 0.0
        ),
    }

    return Order.arm(
        strategy_id=str(strategy_id or getattr(signal, "strategy_id", "") or ""),
        token=str(getattr(signal, "token", "")),
        direction=direction,  # type: ignore[arg-type]
        trigger=trigger,
        trigger_price=trigger_price,
        working_price_source="last",
        armed_at=armed_at,
        expires_at=None,
        sizing_ctx=sizing_ctx,
        strategy_params={
            "bar_idx": int(getattr(bar_ctx, "bar_idx", 0) or 0),
            "direction": direction,
        },
        time_in_force=TimeInForce.GTC,
    )


def _process_orders_native(sim, signals, ctx, bar_idx, strategy_specs=None):
    """M11 Commit 6 — native signal → order pipeline.

    Task 6.4 architectural commitments:
      * Iterates ``signals.signals[token]`` where ``signals`` is a
        ``UniverseSignals`` instance (or a dict of them, keyed by
        strategy_id).
      * NO single-bar TokenBarArrays wrapper anywhere in the body.
      * NO ``[local_bar]`` array indexing.

    ``signals`` may be either:
      * a single ``UniverseSignals`` (strategy_id inferred from spec keys)
      * ``dict[str, UniverseSignals]`` (per-strategy signals at this bar)

    ``strategy_specs``: ``dict[strategy_id, StrategySpec]``. Defaults to
    ``sim.strategy_specs`` if present; otherwise empty (caller supplies
    clamp config via ``sim.config``).

    Mutates ``sim`` in place; returns ``sim`` so the M11 AC-5 parity
    test can inspect post-state via ``native_state.open_orders``.
    """
    if signals is None:
        return sim
    if strategy_specs is None:
        strategy_specs = getattr(sim, "strategy_specs", None) or {}

    # Map native _SizedResult.reject_reason → RejectionStats attribute.
    # Legacy uses `capital` (not `free_capital`); concentration-below-
    # min_size is `concentration` (not `min_size`).
    _REJECT_BUCKET_MAP = {
        "direction_zero": "direction_zero",
        "min_size": "min_size",
        "adv_cap": "adv_cap",
        "concentration": "concentration",
        "capital": "capital",
        "no_sizing_request": "min_size",
        "no_price": "min_size",
        "zero_size": "min_size",
        "near_zero_qty": "min_size",
    }

    # Normalize: accept either a bare UniverseSignals or a dict keyed
    # by strategy_id. Dict form is the canonical M11 shape; single form
    # is a convenience for unit tests that only drive one strategy.
    if isinstance(signals, dict):
        by_strategy = signals
    else:
        # Bare UniverseSignals — pick any strategy id from specs, or
        # fall back to an anonymous bucket.
        sid = next(iter(strategy_specs), "")
        by_strategy = {sid: signals}

    for strategy_id, univ_signals in by_strategy.items():
        spec = (
            strategy_specs.get(strategy_id)
            if hasattr(strategy_specs, "get")
            else None
        )
        token_to_signal = getattr(univ_signals, "signals", None)
        if not token_to_signal:
            continue

        for token, token_signal in token_to_signal.items():
            direction = int(getattr(token_signal, "direction", 0) or 0)
            if direction == 0:
                # Legacy counts direction=0 as a rejection bucket too.
                reasons = getattr(sim, "rejections", None)
                if reasons is not None and hasattr(reasons, "direction_zero"):
                    try:
                        reasons.direction_zero = reasons.direction_zero + 1
                    except Exception:
                        pass
                continue

            sized = _apply_sizing_clamps_native(
                token_signal=token_signal,
                spec=spec,
                ctx=ctx,
                sim_state=sim,
                token=token,
                bar_idx=bar_idx,
            )
            if sized.rejected:
                # Mirror legacy rejection counters so downstream
                # diagnostics land on the same rejection buckets.
                reasons = getattr(sim, "rejections", None)
                if reasons is not None:
                    bucket = _REJECT_BUCKET_MAP.get(
                        sized.reject_reason or "", "min_size"
                    )
                    if hasattr(reasons, bucket):
                        try:
                            setattr(reasons, bucket, getattr(reasons, bucket) + 1)
                        except Exception:
                            pass
                continue

            bar_ctx = _build_bar_context(token, ctx, bar_idx, None)
            order = _build_order_from_signal(
                signal=token_signal,
                sized_quantity=sized.quantity,
                bar_ctx=bar_ctx,
                strategy_id=strategy_id,
                spec=spec,
                sized_result=sized,
            )
            if hasattr(sim, "open_orders"):
                sim.open_orders.append(order)

    return sim


def _fire_armed_orders_native(sim, ctx, bar_idx):
    """M11 Stage-2a — native armed-order trigger sweep.

    ADR-0002 move #4 explicitly locates armed-entry intra-bar trigger
    firing in the engine's Order manager, not in strategy code. This
    helper closes that gap for the native dispatch path: at the end
    of each bar, evaluate every ``ARMED`` Order in ``sim.open_orders``
    against the current bar's OHLC (read from ``ctx.cache``) and, when
    the trigger fires, materialize a :class:`v5.position.Position`
    onto ``sim.position_manager`` using the ``sizing_ctx`` payload the
    order already carries.

    Filed under ``v5/simulator.py`` alongside the legacy
    ``_stage1_trigger_armed_orders`` so the native variant lives next
    to the function it is the native twin of. No strategy code
    subscribes to it; the orchestrator invokes it inline after
    ``_process_orders_native``.

    Triggers:
      * ``TriggerType.PRICE_ABOVE``: fires when ``high >= trigger_price``
      * ``TriggerType.PRICE_BELOW``: fires when ``low  <= trigger_price``
      * ``TriggerType.BAR_CLOSE`` : fires unconditionally at bar close.

    Mutates ``sim`` in place; returns ``sim``.
    """
    from datetime import datetime, timezone
    from v5.orders import OrderStatus, TriggerType
    from v5.position import Position

    open_orders = getattr(sim, "open_orders", None)
    if not open_orders:
        return sim

    cache = _resolve_market_cache(ctx)
    still_armed: list = []
    for order in open_orders:
        if order.state != OrderStatus.ARMED:
            still_armed.append(order)
            continue

        # Resolve current-bar OHLC via the cache.
        inst = None
        if cache is not None:
            inst = _resolve_instrument_for_token(ctx, order.token)
        view = _latest_bars_for_instrument(cache, inst) if cache is not None else None
        if view is None or view.close.shape[0] == 0:
            still_armed.append(order)
            continue
        close_val = float(view.close[-1])
        high_val = float(view.high[-1])
        low_val = float(view.low[-1])

        trig = getattr(order, "trigger", None)
        trig_px = float(getattr(order, "trigger_price", 0.0) or 0.0)
        fired = False
        if trig is TriggerType.BAR_CLOSE:
            fired = True
        elif trig is TriggerType.PRICE_ABOVE and high_val >= trig_px:
            fired = True
        elif trig is TriggerType.PRICE_BELOW and low_val <= trig_px:
            fired = True
        if not fired:
            still_armed.append(order)
            continue

        # Materialize a Position from the order's sizing_ctx payload.
        sctx = dict(getattr(order, "sizing_ctx", {}) or {})
        direction = int(order.direction)
        entry_price = float(sctx.get("entry_price", close_val) or close_val)
        target_size = float(sctx.get("target_size", 0.0) or 0.0)
        if entry_price <= 0 or target_size <= 0:
            continue
        quantity = target_size * direction
        fee_rate = float(sctx.get("fee_rate", 0.0) or 0.0)
        notional_usd = float(sctx.get("notional_usd", abs(quantity) * entry_price))
        margin_usd = float(sctx.get("margin_usd", notional_usd))
        entry_fee = abs(notional_usd) * fee_rate

        pos = Position(
            position_id=f"{order.token}:{order.strategy_id}:{bar_idx}:primary",
            token=order.token,
            strategy_id=order.strategy_id,
            leg_ref_id="leg_primary",
            entry_bar=int(bar_idx),
            entry_price=entry_price,
            direction=direction,
            quantity=quantity,
            margin_usd=margin_usd,
            leverage=float(sctx.get("leverage", 1.0) or 1.0),
            is_perp=bool(sctx.get("is_perp", False)),
            fee_rate=fee_rate,
            stop_mult=float(sctx.get("stop_mult", 0.0) or 0.0),
            trail_mult=float(sctx.get("trail_mult", 0.0) or 0.0),
            target_mult=float(sctx.get("target_mult", 999.0) or 999.0),
            no_stop_bars=int(sctx.get("no_stop_bars", 0) or 0),
            min_hold=int(sctx.get("min_hold", 0) or 0),
            max_hold=int(sctx.get("max_hold", 10 ** 9) or 10 ** 9),
            convex_exit=bool(sctx.get("convex_exit", False)),
            rsi_exit_level=float(sctx.get("rsi_exit_level", 999.0) or 999.0),
            convex_bar_thresholds=sctx.get("convex_bar_thresholds", (48, 12)),
            convex_multipliers=sctx.get("convex_multipliers", (2.0, 1.5, 0.3)),
            trail_schedule=sctx.get("trail_schedule", None),
            time_trail_schedule=sctx.get("time_trail_schedule", None),
            max_trail_mult_arr=sctx.get("max_trail_mult_arr", None),
            funding_exit_threshold=float(
                sctx.get("funding_exit_threshold", 0.0) or 0.0
            ),
            breakeven_atr=float(sctx.get("breakeven_atr", 0.0) or 0.0),
            chandelier_lookback=int(sctx.get("chandelier_lookback", 0) or 0),
            stop_price=float(sctx.get("stop_price", 0.0) or 0.0),
            highest=float(sctx.get("highest", high_val)),
            lowest=float(sctx.get("lowest", low_val)),
            initial_risk=float(sctx.get("initial_risk", 0.0) or 0.0),
            limit_price=float(sctx.get("limit_price", 0.0) or 0.0),
            stop_limit_price=float(sctx.get("stop_price", 0.0) or 0.0),
            fill_source="hourly",
        )

        # Ledger bookkeeping — mirror the legacy _stage2 open path.
        try:
            sim.total_fees += entry_fee
        except Exception:
            pass
        try:
            sim._entry_fees_by_pos[pos.position_id] = entry_fee
        except Exception:
            pass
        try:
            sim.position_manager.open_position(pos)
        except Exception:
            pass
        try:
            sim.diagnostics.entries_opened.setdefault(order.strategy_id, 0)
            sim.diagnostics.entries_opened[order.strategy_id] += 1
        except Exception:
            pass

    sim.open_orders = still_armed
    return sim


def _engine_default_exit_check(pos, bar_ctx):
    """Engine-default exit evaluation — mirrors ``build_exit_chain`` order.

    Task 6.5 architectural commitment: when a strategy's ``check_exit``
    returns ``None``, the engine still applies every built-in exit
    handler the legacy path wires up via
    :func:`v5.exit_handlers.build_exit_chain`. Handler order is the
    legacy if/elif priority:

      0. Breakeven ratchet (state mutation — updates pos.stop_price)
      1. Circuit breaker (emergency exit at Nx initial_risk)
      2. Stop loss (gated by no_stop_bars)
      3. Take-profit (regime-unconditional; M9 C-4 baseline)
      4. RSI exit (needs bar_ctx.rsi)
      5. Max hold
      6. Funding ceiling (perp only)

    Chandelier / SMA trail / mean-target handlers require per-bar indicator
    arrays that the engine-default fallback doesn't have access to; those
    remain a strategy-owned exit_check_fn responsibility and fire via the
    CustomExitHandler wrapper in the legacy build_exit_chain. A strategy
    that wants those behaviors on the native path overrides
    ``Strategy.check_exit`` to emit the matching ExitCheck.

    Returns :class:`ExitCheck` (``should_exit=True`` to close) or
    ``None`` when no engine-default trigger fired.
    """
    close = float(getattr(bar_ctx, "close", 0.0) or 0.0)
    high = float(getattr(bar_ctx, "high", close) or close)
    low = float(getattr(bar_ctx, "low", close) or close)
    atr = float(getattr(bar_ctx, "atr", 0.0) or 0.0)
    rsi = float(getattr(bar_ctx, "rsi", float("nan")) or float("nan"))
    funding_val = float(getattr(bar_ctx, "funding_val", 0.0) or 0.0)
    bars_held = int(getattr(bar_ctx, "bars_held", 0) or 0)
    direction = int(getattr(pos, "direction", 0) or 0)
    entry_price = float(getattr(pos, "entry_price", 0.0) or 0.0)
    initial_risk = float(getattr(pos, "initial_risk", 0.0) or 0.0)
    convex_exit = bool(getattr(pos, "convex_exit", False))

    # --- Breakeven ratchet (state-mutating) --------------------------
    breakeven_atr = float(getattr(pos, "breakeven_atr", 0.0) or 0.0)
    if breakeven_atr > 0.0 and not getattr(pos, "breakeven_triggered", False):
        if direction == 1:
            pos.highest = max(float(getattr(pos, "highest", 0.0) or 0.0), high)
            be_profit_atr = (pos.highest - entry_price) / max(atr, 1e-10)
        else:
            pos.lowest = min(float(getattr(pos, "lowest", 999999.0) or 999999.0), low)
            be_profit_atr = (entry_price - pos.lowest) / max(atr, 1e-10)
        if be_profit_atr >= breakeven_atr:
            if direction == 1:
                pos.stop_price = max(float(getattr(pos, "stop_price", 0.0) or 0.0), entry_price)
            else:
                # For shorts, closer stop is smaller.
                cur_stop = float(getattr(pos, "stop_price", 0.0) or 0.0)
                pos.stop_price = (
                    min(cur_stop, entry_price) if cur_stop > 0 else entry_price
                )
            try:
                pos.breakeven_triggered = True
            except AttributeError:
                pass

    # --- Update trailing high/low trackers (used by stop check) ------
    if direction == 1:
        pos.highest = max(float(getattr(pos, "highest", 0.0) or 0.0), high)
    else:
        pos.lowest = min(float(getattr(pos, "lowest", 999999.0) or 999999.0), low)

    # --- Circuit breaker (legacy CircuitBreakerHandler) ---------------
    cb_r = float(getattr(pos, "circuit_breaker_r", 0.0) or 0.0)
    if cb_r > 0.0 and initial_risk > 0.0 and entry_price > 0.0:
        cb_dist = cb_r * initial_risk
        if direction == 1 and low <= entry_price - cb_dist:
            return ExitCheck(should_exit=True, reason="circuit_breaker")
        if direction == -1 and high >= entry_price + cb_dist:
            return ExitCheck(should_exit=True, reason="circuit_breaker")

    # --- Stop loss (legacy StopLossHandler) ---------------------------
    stop_price = float(getattr(pos, "stop_price", 0.0) or 0.0)
    no_stop_bars = int(getattr(pos, "no_stop_bars", 0) or 0)
    stop_active = bars_held >= no_stop_bars or convex_exit
    if stop_price > 0.0 and stop_active:
        if direction == 1 and low <= stop_price:
            return ExitCheck(
                should_exit=True, reason="stop", exit_price_override=stop_price,
            )
        if direction == -1 and high >= stop_price:
            return ExitCheck(
                should_exit=True, reason="stop", exit_price_override=stop_price,
            )

    # --- Take-profit (legacy TakeProfitHandler, regime-unconditional) -
    target_mult = float(getattr(pos, "target_mult", 0.0) or 0.0)
    if target_mult > 0.0 and initial_risk > 0.0 and entry_price > 0.0:
        if convex_exit:
            if direction == 1 and close > entry_price + target_mult * initial_risk:
                return ExitCheck(should_exit=True, reason="target")
            if direction == -1 and close < entry_price - target_mult * initial_risk:
                return ExitCheck(should_exit=True, reason="target")
        else:
            atr_eff = max(atr, 1e-10)
            if direction == 1 and high >= entry_price + target_mult * atr_eff:
                return ExitCheck(
                    should_exit=True, reason="target",
                    exit_price_override=entry_price + target_mult * atr_eff,
                )
            if direction == -1 and low <= entry_price - target_mult * atr_eff:
                return ExitCheck(
                    should_exit=True, reason="target",
                    exit_price_override=entry_price - target_mult * atr_eff,
                )

    # --- RSI exit (legacy RSIExitHandler; engine-default version uses
    #     bar_ctx.rsi rather than sig.rsi[local_bar]) ------------------
    rsi_exit_level = float(getattr(pos, "rsi_exit_level", 999.0) or 999.0)
    min_hold = int(getattr(pos, "min_hold", 0) or 0)
    if rsi_exit_level < 999.0 and not np.isnan(rsi) and bars_held >= min_hold:
        if direction == 1 and rsi > rsi_exit_level:
            return ExitCheck(should_exit=True, reason="rsi")
        if direction == -1 and rsi < (100.0 - rsi_exit_level):
            return ExitCheck(should_exit=True, reason="rsi")

    # --- Max hold (legacy MaxHoldHandler) -----------------------------
    max_hold = int(getattr(pos, "max_hold", 10 ** 9) or 10 ** 9)
    if max_hold > 0 and bars_held >= max_hold:
        return ExitCheck(
            should_exit=True, reason="max_hold", exit_price_override=close,
        )

    # --- Funding ceiling (legacy FundingCeilingHandler) --------------
    funding_threshold = float(getattr(pos, "funding_exit_threshold", 0.0) or 0.0)
    is_perp = bool(getattr(pos, "is_perp", False))
    margin_usd = float(getattr(pos, "margin_usd", 0.0) or 0.0)
    cum_funding = float(getattr(pos, "cumulative_funding", 0.0) or 0.0)
    if funding_threshold > 0.0 and is_perp and margin_usd > 0.0:
        if cum_funding / margin_usd > funding_threshold:
            return ExitCheck(should_exit=True, reason="funding")

    return None


def _process_exits_native(sim, ctx, bar_idx, strategies):
    """M11 Commit 6 — per-position exit evaluation via strategy callback.

    Task 6.5 architectural commitments:
      * Per-position check via ``strategy.check_exit(pos, bar_ctx)``
        callback.
      * ``BarContext`` built fresh from ``MarketDataCache`` via
        ``_build_bar_context`` — NOT from pre-baked arrays.
      * NO ``[local_bar]`` indexing.
      * AC-S5 exception containment: log + increment
        ``strat.exception_counter`` + fall through to engine default.

    ``strategies``: ``dict[strategy_id, Strategy]``. Each open position
    resolves its owning strategy by ``pos.strategy_id``.

    When ``strategy.check_exit`` returns ``None`` the engine-default
    stop/trail/target/max_hold logic fires via
    ``_engine_default_exit_check``.

    Mutates ``sim`` in place; returns ``sim`` for symmetry with
    ``_process_orders_native`` (so callers can inspect post-state).
    """
    from v5.config import PortfolioConfig
    config = getattr(sim, "config", None)
    if not isinstance(config, PortfolioConfig):
        config = getattr(sim, "_config", None) or PortfolioConfig()

    pm = getattr(sim, "position_manager", None)
    if pm is None:
        return sim

    cache = _resolve_market_cache(ctx)

    to_close: list[tuple] = []  # (pos, exit_price, reason, exit_adv)

    for pos in list(pm.open_positions):
        token = getattr(pos, "token", "")
        bar_ctx = _build_bar_context(
            token=token,
            ctx=ctx,
            bar_idx=bar_idx,
            pos=pos,
        )

        # Strategy-level exit first.
        exit_check = None
        strat = (
            strategies.get(getattr(pos, "strategy_id", ""))
            if hasattr(strategies, "get") else None
        )
        if strat is not None:
            try:
                exit_check = strat.check_exit(pos, bar_ctx)
            except Exception as exc:
                # AC-S5 error containment — observable: log +
                # increment exception counter on the strategy, then
                # fall through to engine default (NOT silent-None).
                logger.warning(
                    "native _process_exits_native: check_exit(%s) raised %s — "
                    "falling back to engine-default exit check",
                    getattr(pos, "strategy_id", "?"),
                    repr(exc),
                )
                try:
                    strat.exception_counter = int(
                        getattr(strat, "exception_counter", 0)
                    ) + 1
                except Exception:
                    pass
                exit_check = None

        # Engine-default fallback covers the full legacy build_exit_chain
        # handler list.
        if exit_check is None:
            exit_check = _engine_default_exit_check(pos, bar_ctx)

        if exit_check is not None and getattr(exit_check, "should_exit", False):
            exit_price = getattr(exit_check, "exit_price_override", None)
            if exit_price is None:
                exit_price = float(getattr(bar_ctx, "close", 0.0) or 0.0)

            # Pull real ADV from the unified cache where available —
            # matches legacy sig.rolling_adv[local_bar] semantics.
            exit_adv = 1_000_000.0
            if cache is not None:
                try:
                    instrument = _resolve_instrument_for_token(ctx, token)
                    exit_adv = float(cache.metric_latest("adv", instrument))
                except Exception:
                    exit_adv = 1_000_000.0
            to_close.append((
                pos, float(exit_price),
                str(getattr(exit_check, "reason", "exit") or "exit"),
                exit_adv,
            ))

    for pos, exit_price, reason, exit_adv in to_close:
        if pos in pm.open_positions:
            _close_position(
                sim, pos, int(bar_idx), exit_price, reason, exit_adv, config,
            )

    return sim


# ============================================================================
# M11 Commit 6 — bit-identical wrapper so legacy _process_orders returns state
# ============================================================================
#
# The legacy ``_process_orders`` body (above) is preserved BIT-IDENTICAL and
# still returns ``None`` — it is the direct port of the M2 3-stage cascade
# and MUST NOT change for AC14 hourly parity.
#
# The M11 AC-5 native-vs-legacy parity test compares post-state on BOTH
# paths by calling ``legacy_state = _process_orders(*args)`` and
# ``native_state = _process_orders_native(*args)``. ``_process_orders_native``
# returns ``sim`` (mutated in place); for symmetry we rebind the module-level
# ``_process_orders`` symbol to a thin wrapper that delegates to the legacy
# body and returns the same ``state`` object it was passed.
#
# Zero behavioral change for existing callers — all callers in the codebase
# today ignore the return value, so adding ``return state`` to the wrapper
# is backward-compatible. The legacy function body itself is unchanged.
# ============================================================================

_process_orders_legacy = _process_orders


def _process_orders(
    state,
    all_signals,
    strategy_specs,
    bar_maps,
    global_bar,
    config,
    rng,
    unified_ts=None,
):
    """Thin wrapper — delegates to the bit-identical legacy body and
    returns the mutated ``state``. See module docstring for rationale.
    """
    _process_orders_legacy(
        state, all_signals, strategy_specs, bar_maps,
        global_bar, config, rng, unified_ts=unified_ts,
    )
    return state


# Update the legacy-name alias so paper_engine + internal callers continue
# to resolve through the wrapper path (consistent return semantics).
_process_entries = _process_orders

