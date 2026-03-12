"""V4 Portfolio Backtest — Position and trade tracking."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np


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
    exit_regimes: set
    convex_exit: bool = False
    rsi_exit_level: float = 999.0
    trail_schedule: Optional[np.ndarray] = None
    time_trail_schedule: Optional[np.ndarray] = None
    max_trail_mult_arr: Optional[np.ndarray] = None
    funding_exit_threshold: float = 0.0
    # Partial profit-taking params (frozen at entry)
    partial_tp_atr: float = 0.0       # profit threshold in ATR units (0 = disabled)
    partial_tp_pct: float = 0.5       # fraction to close
    partial_tp_trail: float = 1.5     # tighter trail for remainder
    # Mutable state (updated each bar)
    partial_closed: bool = False      # True after partial close executed
    stop_price: float = 0.0
    highest: float = 0.0
    lowest: float = 999999.0
    initial_risk: float = 0.0    # stop_mult * atr at entry
    cumulative_funding: float = 0.0
    linked_position_id: Optional[str] = None
    entry_timestamp: str = ""           # wall-clock time when opened (paper trading)


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
    exit_reason: str            # "stop","target","regime","max_hold","liquidation","rsi","mean_target","funding","data_end"
    is_perp: bool = False
    entry_timestamp: str = ""   # wall-clock time when opened (paper trading)


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
