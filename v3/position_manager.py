"""Position state manager for the live paper trading engine.

Tracks open positions with entry price, size, unrealized PnL, funding
accrued.  Persists state atomically via write-tmp-then-rename.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from typing import List, Optional


_FUNDING_INTERVAL_S = 8 * 3600  # 8 hours in seconds


@dataclass
class Position:
    """A single open position."""

    token: str
    market: str             # 'spot' or 'perp'
    side: str               # 'long' or 'short'
    entry_price: float
    size_usd: float
    size_units: float
    stop_price: float
    trail_price: float
    funding_accrued: float = 0.0
    last_funding_time: int = 0
    entry_bar: int = 0
    strategy_id: str = ""


class PositionManager:
    """Manages open positions with atomic persistence."""

    def __init__(self, state_dir: str):
        self.state_dir = state_dir
        self._positions: list[Position] = []

    # ------------------------------------------------------------------
    # Open / close
    # ------------------------------------------------------------------

    def open_position(
        self,
        token: str,
        market: str,
        side: str,
        entry_price: float,
        size_usd: float,
        size_units: float,
        stop_price: float,
        trail_price: float,
        entry_bar: int,
        strategy_id: str,
    ) -> Position:
        pos = Position(
            token=token,
            market=market,
            side=side,
            entry_price=entry_price,
            size_usd=size_usd,
            size_units=size_units,
            stop_price=stop_price,
            trail_price=trail_price,
            funding_accrued=0.0,
            last_funding_time=0,
            entry_bar=entry_bar,
            strategy_id=strategy_id,
        )
        self._positions.append(pos)
        return pos

    def close_position(self, token: str, strategy_id: str) -> Position:
        for i, pos in enumerate(self._positions):
            if pos.token == token and pos.strategy_id == strategy_id:
                return self._positions.pop(i)
        raise KeyError(
            f"No open position for {token} / {strategy_id}"
        )

    def get_open_positions(self) -> list[Position]:
        return list(self._positions)

    def get_position(self, token: str, strategy_id: str) -> Position:
        for pos in self._positions:
            if pos.token == token and pos.strategy_id == strategy_id:
                return pos
        raise KeyError(
            f"No open position for {token} / {strategy_id}"
        )

    # ------------------------------------------------------------------
    # AC10: Funding rate application on 8h settlement schedule
    # ------------------------------------------------------------------

    def apply_funding(
        self,
        token: str,
        strategy_id: str,
        funding_rate: float,
        settlement_time: int,
    ) -> bool:
        """Apply a funding rate to a perp position if 8h have elapsed.

        Returns True if funding was applied, False otherwise.
        """
        pos = self.get_position(token, strategy_id)

        # Spot positions never accrue funding.
        if pos.market != "perp":
            return False

        if pos.last_funding_time > 0:
            # Normal path: check 8h interval since last settlement.
            elapsed = settlement_time - pos.last_funding_time
            if elapsed < _FUNDING_INTERVAL_S:
                return False
        else:
            # First settlement after position open.  Align to the funding
            # schedule: apply only if settlement_time falls in the latter
            # half of the current 8h period (implying the position existed
            # before the most recent settlement boundary).
            offset = settlement_time % _FUNDING_INTERVAL_S
            if offset < _FUNDING_INTERVAL_S // 2:
                # Too early in the current period — record time, skip.
                pos.last_funding_time = settlement_time
                return False

        # Apply: funding_accrued += size_usd * funding_rate
        pos.funding_accrued += pos.size_usd * funding_rate
        pos.last_funding_time = settlement_time
        return True

    # ------------------------------------------------------------------
    # Atomic persistence: write-tmp-then-rename
    # ------------------------------------------------------------------

    def _state_path(self) -> str:
        return os.path.join(self.state_dir, "positions.json")

    def _tmp_path(self) -> str:
        return os.path.join(self.state_dir, "positions.json.tmp")

    def persist(self) -> None:
        """Atomically write current state to disk."""
        os.makedirs(self.state_dir, exist_ok=True)
        data = {
            "positions": [asdict(p) for p in self._positions],
        }
        tmp = self._tmp_path()
        final = self._state_path()
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, final)

    def load(self) -> None:
        """Load positions from persisted state (ignores stale .tmp files)."""
        path = self._state_path()
        if not os.path.exists(path):
            self._positions = []
            return
        with open(path, "r") as f:
            data = json.load(f)
        self._positions = [
            Position(**p) for p in data.get("positions", [])
        ]
