"""V5 Portfolio Backtest — Position and trade tracking."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np


@dataclass
class ScalingEvent:
    """A single scale (increase or reduce) execution event recorded on a Position.

    AC13: 12-field definition. `requested_qty_delta` records strategy intent
    pre-AC10-clamp; `qty_delta` records what actually executed. `is_stop_like`
    copied verbatim from `ScaleAction.is_stop_like` so analysis can distinguish
    stop/trail reduces from voluntary reduces.
    """
    bar: int
    kind: str                           # "increase" or "reduce"
    fill_price: float
    qty_delta: float                    # signed actual: + on increase, - on reduce
    requested_qty_delta: float          # signed pre-AC10-clamp
    margin_delta: float                 # signed actual: + on increase, - on reduce
    fill_notional: float                # abs(qty_delta) * fill_price
    entry_fee_delta: float              # > 0 on increase
    exit_fee: float                     # > 0 on reduce
    slippage_bps: float                 # actual slippage paid
    atr_at_event: float
    is_stop_like: bool                  # copied from ScaleAction.is_stop_like


@dataclass
class ReduceResult:
    """Pure-accounting result bundle returned by Position.reduce (AC4).

    State-free domain bundle — simulator wrapper (`book_reduce`) consumes this
    to produce a ClosedTrade + update engine counters.
    """
    closed_qty_signed: float             # signed in pos-direction units
    closed_qty_abs: float                # absolute
    closed_margin: float                 # margin pro-rata removed
    closed_funding: float                # cumulative_funding pro-rata removed
    partial_entry_fee_to_book: float     # -> ClosedTrade.entry_fee
    partial_entry_fee_remaining: float   # -> state._entry_fees_by_pos[pos_id] new value
    exit_fee: float                      # -> ClosedTrade.exit_fee
    slip_bps: float                      # -> ScalingEvent.slippage_bps
    gross_pnl: float                     # closed_qty_signed * (fill_price - entry_price)
    is_terminal: bool                    # True if reduce promoted to full close (AC14)
    suffix: str                          # ":scale_N" or ":scale_N_final"


@dataclass
class Position:
    """An open position in the portfolio."""
    position_id: str             # "BTC:s30:5000:primary"
    token: str
    strategy_id: str
    leg: str                     # "primary" or "secondary"
    entry_bar: int               # global bar index
    entry_price: float
    direction: int               # +1 or -1
    quantity: float              # signed (direction * notional / entry_price)
    margin_usd: float            # capital locked (before leverage)
    leverage: float
    is_perp: bool
    fee_rate: float              # market-specific fee rate (spot vs perp)
    # Trade params (frozen at entry from per-bar arrays)
    stop_mult: float
    trail_mult: float
    target_mult: float
    no_stop_bars: int
    min_hold: int
    max_hold: int
    convex_exit: bool = False
    rsi_exit_level: float = 999.0
    # Configurable exit constants
    convex_bar_thresholds: tuple = (48, 12)
    convex_multipliers: tuple = (2.0, 1.5, 0.3)
    trail_schedule: Optional[np.ndarray] = None
    time_trail_schedule: Optional[np.ndarray] = None
    max_trail_mult_arr: Optional[np.ndarray] = None
    funding_exit_threshold: float = 0.0
    # Breakeven ratchet
    breakeven_atr: float = 0.0        # profit threshold in ATR to trigger breakeven (0 = disabled)
    breakeven_triggered: bool = False  # True after stop moved to entry price
    # Chandelier stop: trail from highest-high over N-bar lookback (0 = disabled)
    chandelier_lookback: int = 0
    # Mutable state (updated each bar)
    stop_price: float = 0.0
    highest: float = 0.0
    lowest: float = 999999.0
    initial_risk: float = 0.0    # stop_mult * atr at entry
    cumulative_funding: float = 0.0
    linked_position_id: Optional[str] = None
    entry_timestamp: str = ""           # wall-clock time when opened (paper trading)
    # Limit order metadata (for analysis)
    limit_price: float = 0.0             # Limit order price (BB band level)
    limit_placed_at: str = ""            # When limit was determined
    stop_limit_price: float = 0.0        # Initial stop price at entry
    fill_source: str = ""                # "hourly" or "sub_hourly"
    # Exit handler chain (built at entry, not serialized to ClosedTrade)
    exit_handlers: list = field(default_factory=list)
    # M2 scaling fields (all with back-compat defaults)
    scale_count: int = 0
    scaling_events: list = field(default_factory=list)
    r_anchor_price: float = 0.0          # set to entry_price at __post_init__ when 0
    _helper_state: dict = field(default_factory=dict)
    _scale_action_bar: int = -1

    def __post_init__(self) -> None:
        # Anchor R-multiple calculations on original entry when not explicitly set.
        if self.r_anchor_price == 0.0:
            self.r_anchor_price = self.entry_price

    # ------------------------------------------------------------------
    # M2: Position.increase (AC1, AC2, AC3, AC21, Q2)
    # ------------------------------------------------------------------
    def increase(
        self,
        qty_to_add: float,
        fill_price: float,
        margin_delta: float,
        bar_idx: int,
        stop_override: Optional[float] = None,
        fee_rate: Optional[float] = None,
        atr: Optional[float] = None,
        freeze_initial_risk: bool = True,
    ) -> None:
        """Increase position size with weighted-average entry (WACB / FIX AvgPx).

        qty_to_add: unsigned absolute base units (Q2: must be > 0).
        Does NOT book a ClosedTrade (AC1). Appends a ScalingEvent.
        """
        if qty_to_add <= 0:
            raise ValueError("qty_to_add must be > 0")

        old_qty_abs = abs(self.quantity)
        old_entry = self.entry_price
        # Weighted-average entry price (AC1)
        new_entry = (
            (old_qty_abs * old_entry + qty_to_add * fill_price)
            / (old_qty_abs + qty_to_add)
        )
        self.entry_price = new_entry
        self.margin_usd += margin_delta
        self.quantity += self.direction * qty_to_add

        # AC2: stop_price update
        if stop_override is not None:
            self.stop_price = stop_override
        elif atr is not None:
            # Never-loosen default
            if self.direction == 1:
                candidate = new_entry - self.stop_mult * atr
                self.stop_price = max(self.stop_price, candidate)
            else:
                candidate = new_entry + self.stop_mult * atr
                self.stop_price = min(self.stop_price, candidate)
        # else: atr is None and no override -> leave stop unchanged

        # AC3: initial_risk and r_anchor_price freeze/refresh behavior
        if not freeze_initial_risk:
            if atr is not None:
                self.initial_risk = self.stop_mult * atr
            self.r_anchor_price = new_entry
        # else: both frozen — preserve existing initial_risk and r_anchor_price

        # AC3: breakeven_triggered preserved (do NOT reset)
        # AC16: highest/lowest unchanged (do NOT reset)
        # AC21: entry_bar / entry_timestamp unchanged
        # AC26: scale_count unchanged (only reduce increments it)

        entry_fee_delta = qty_to_add * fill_price * fee_rate if fee_rate else 0.0
        self.scaling_events.append(
            ScalingEvent(
                bar=bar_idx,
                kind="increase",
                fill_price=fill_price,
                qty_delta=self.direction * qty_to_add,
                requested_qty_delta=self.direction * qty_to_add,
                margin_delta=margin_delta,
                fill_notional=qty_to_add * fill_price,
                entry_fee_delta=entry_fee_delta,
                exit_fee=0.0,
                slippage_bps=0.0,
                atr_at_event=atr if atr is not None else float("nan"),
                is_stop_like=False,
            )
        )

    # ------------------------------------------------------------------
    # M2: Position.reduce (AC4, AC5, AC7, AC9, AC14, AC17, AC22)
    # ------------------------------------------------------------------
    def reduce(
        self,
        qty_to_close: float,
        fill_price: float,
        bar_idx: int,
        fee_rate: float,
        atr: float,
        full_entry_fee: float,
        dust_usd: float,
        triggered_by: str = "",
        is_stop_like: bool = False,
    ) -> ReduceResult:
        """Reduce position; returns ReduceResult (caller books ClosedTrade).

        Pure accounting — simulator wrapper handles ClosedTrade booking.
        """
        old_qty_abs = abs(self.quantity)

        # AC17: over-close clamp
        if qty_to_close >= old_qty_abs:
            qty_to_close = old_qty_abs

        close_fraction = qty_to_close / old_qty_abs if old_qty_abs > 0 else 0.0

        closed_qty_signed = self.direction * qty_to_close
        closed_margin = self.margin_usd * close_fraction
        closed_funding = self.cumulative_funding * close_fraction  # AC9
        partial_entry_fee_to_book = full_entry_fee * close_fraction  # AC7
        partial_entry_fee_remaining = full_entry_fee - partial_entry_fee_to_book
        exit_fee = qty_to_close * fill_price * fee_rate
        gross_pnl = closed_qty_signed * (fill_price - self.entry_price)

        # Mutate position (AC4, AC5 entry_price unchanged)
        self.quantity *= (1.0 - close_fraction)
        self.margin_usd *= (1.0 - close_fraction)
        self.cumulative_funding *= (1.0 - close_fraction)

        # AC22: sign invariant — avoid negative-zero residuals
        tiny = 1e-12
        if abs(self.quantity) < tiny:
            self.quantity = 0.0

        # AC14: dust threshold check
        remaining_notional = abs(self.quantity) * fill_price
        if self.quantity == 0.0 or remaining_notional < dust_usd:
            self.quantity = 0.0
            is_terminal = True
            # AC9 / QC1 T-P1c: dust-promoted terminal must flush residual funding
            # and margin into the booked ClosedTrade. Without this, a reduce that
            # trips the dust threshold leaves tiny residuals on the position that
            # can never be booked (the position is terminal-closed).
            closed_funding += self.cumulative_funding
            closed_margin += self.margin_usd
            self.cumulative_funding = 0.0
            self.margin_usd = 0.0
        else:
            is_terminal = False

        # Suffix uses scale_count + 1 (caller increments scale_count on booking)
        next_seq = self.scale_count + 1
        if is_terminal:
            suffix = f":scale_{next_seq}_final"
        else:
            suffix = f":scale_{next_seq}"

        # Append ScalingEvent (caller may overwrite slippage_bps post-slippage-calc)
        self.scaling_events.append(
            ScalingEvent(
                bar=bar_idx,
                kind="reduce",
                fill_price=fill_price,
                qty_delta=-closed_qty_signed,
                requested_qty_delta=-closed_qty_signed,
                margin_delta=-closed_margin,
                fill_notional=qty_to_close * fill_price,
                entry_fee_delta=0.0,
                exit_fee=exit_fee,
                slippage_bps=0.0,
                atr_at_event=atr,
                is_stop_like=is_stop_like,
            )
        )

        return ReduceResult(
            closed_qty_signed=closed_qty_signed,
            closed_qty_abs=qty_to_close,
            closed_margin=closed_margin,
            closed_funding=closed_funding,
            partial_entry_fee_to_book=partial_entry_fee_to_book,
            partial_entry_fee_remaining=partial_entry_fee_remaining,
            exit_fee=exit_fee,
            slip_bps=0.0,
            gross_pnl=gross_pnl,
            is_terminal=is_terminal,
            suffix=suffix,
        )

    def reduce_fraction(
        self,
        fraction: float,
        fill_price: float,
        bar_idx: int,
        fee_rate: float,
        atr: float,
        full_entry_fee: float,
        dust_usd: float,
        triggered_by: str = "",
        is_stop_like: bool = False,
    ) -> ReduceResult:
        """Thin convenience wrapper — delegates to reduce() with qty = |pos.quantity| * fraction."""
        qty_to_close = abs(self.quantity) * fraction
        return self.reduce(
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


@dataclass
class ClosedTrade:
    """A completed trade."""
    position_id: str
    token: str
    strategy_id: str
    leg: str
    entry_bar: int
    exit_bar: int
    entry_price: float
    exit_price: float
    direction: int
    margin_usd: float
    pnl: float                  # net of fees, slippage, funding
    funding_cost: float
    entry_fee: float
    exit_fee: float
    hold_bars: int
    exit_reason: str            # "stop","target","regime","max_hold","liquidation","rsi","mean_target","funding","data_end","margin_call","linked_exit","partial_tp","partial_reduce","dust_promoted_reduce"
    is_perp: bool = False
    entry_timestamp: str = ""   # wall-clock time when opened (paper trading)
    exit_timestamp: str = ""    # wall-clock time when closed (paper trading)
    # Limit order metadata (for analysis)
    limit_price: float = 0.0
    limit_placed_at: str = ""
    stop_limit_price: float = 0.0
    fill_source: str = ""
    # M2 identity fields (AC29, all with backward-compat defaults)
    parent_position_id: str = ""
    exec_seq: int = 0
    exec_type: str = "exit"                    # "reduce" | "exit" | "linked_reduce"
    is_terminal: bool = True                    # default for existing single-close trades
    triggered_by: str = ""
    has_scaling: bool = False
    scaling_events: list = field(default_factory=list)


class PositionManager:
    """Manages open positions and closed trades."""

    def __init__(self):
        self.open_positions: list[Position] = []
        self.closed_trades: list[ClosedTrade] = []

    def open_position(self, pos: Position) -> None:
        self.open_positions.append(pos)

    def close_position(
        self,
        pos: Position,
        exit_bar: int,
        exit_price: float,
        pnl: float,
        funding_cost: float,
        entry_fee: float,
        exit_fee: float,
        exit_reason: str,
        exit_timestamp: str = "",
    ) -> ClosedTrade:
        self.open_positions.remove(pos)
        trade = ClosedTrade(
            position_id=pos.position_id,
            token=pos.token,
            strategy_id=pos.strategy_id,
            leg=pos.leg,
            entry_bar=pos.entry_bar,
            exit_bar=exit_bar,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            direction=pos.direction,
            margin_usd=pos.margin_usd,
            pnl=pnl,
            funding_cost=funding_cost,
            entry_fee=entry_fee,
            exit_fee=exit_fee,
            hold_bars=exit_bar - pos.entry_bar,
            exit_reason=exit_reason,
            is_perp=pos.is_perp,
            entry_timestamp=pos.entry_timestamp,
            exit_timestamp=exit_timestamp,
            limit_price=pos.limit_price,
            limit_placed_at=pos.limit_placed_at,
            stop_limit_price=pos.stop_limit_price,
            fill_source=pos.fill_source,
        )
        self.closed_trades.append(trade)
        return trade

    def total_margin_for_token(self, token: str) -> float:
        """Sum margin_usd across ALL strategies for this token."""
        return sum(p.margin_usd for p in self.open_positions if p.token == token)

    def count_for_strategy(self, strategy_id: str) -> int:
        return sum(1 for p in self.open_positions if p.strategy_id == strategy_id)

    def total_open(self) -> int:
        return len(self.open_positions)

    def total_locked_margin(self) -> float:
        return sum(p.margin_usd for p in self.open_positions)

    def get_linked(self, position_id: str) -> Optional[Position]:
        for p in self.open_positions:
            if p.position_id == position_id:
                return p
        return None

    def find_open_for_token_strategy(self, token: str, strategy_id: str) -> list[Position]:
        """Check if already in a position (prevent re-entry while open)."""
        return [p for p in self.open_positions if p.token == token and p.strategy_id == strategy_id]
