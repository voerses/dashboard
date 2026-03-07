"""Portfolio group orchestrator for the live paper trading engine.

Manages shared capital pools with concentration caps across multiple
strategies.  Multiple groups operate independently with separate capital.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class StrategyAllocation:
    """Allocation of capital to a single strategy within a group."""

    strategy_id: str
    weight: float
    type: str


@dataclass
class GroupConfig:
    """Configuration for a portfolio group."""

    group_id: str
    capital: float
    strategies: list
    max_token_pct: float = 0.15

    def __post_init__(self):
        # Convert raw strategy dicts to StrategyAllocation objects
        self.strategies = [
            s if isinstance(s, StrategyAllocation)
            else StrategyAllocation(**s)
            for s in self.strategies
        ]


class PortfolioGroup:
    """Manages a group of strategies sharing a capital pool.

    Features:
    - Shared capital pool: all strategies draw from the same capital
    - Concentration caps: no single token exceeds ``max_token_pct`` of capital
    - Independent groups: each group instance tracks its own allocations
    """

    def __init__(self, config: GroupConfig):
        self.config = config
        self._allocated: float = 0.0
        self._proposed: float = 0.0
        self._allocations: list[dict] = []

    # ------------------------------------------------------------------
    # Capital management
    # ------------------------------------------------------------------

    def available_capital(self) -> float:
        """Return capital not yet allocated to positions."""
        return self.config.capital - self._allocated

    def allocate(
        self, strategy_id: str, token: str, amount: float,
    ) -> None:
        """Allocate capital to a position.  Raises if exceeding pool."""
        if amount > self.available_capital():
            raise ValueError(
                f"Cannot allocate {amount:.2f}: only "
                f"{self.available_capital():.2f} available"
            )
        self._allocated += amount
        self._allocations.append({
            "strategy_id": strategy_id,
            "token": token,
            "amount": amount,
        })

    def release(
        self, strategy_id: str, token: str, amount: float,
    ) -> None:
        """Release capital from a closed position back to the pool."""
        self._allocated = max(0.0, self._allocated - amount)
        self._allocations = [
            a for a in self._allocations
            if not (a["strategy_id"] == strategy_id and a["token"] == token)
        ]

    def get_allocations(self) -> list[dict]:
        """Return current allocations."""
        return list(self._allocations)

    # ------------------------------------------------------------------
    # Position sizing with concentration cap
    # ------------------------------------------------------------------

    def reset_proposed(self) -> None:
        """Reset proposed exposure counter (call before a new sizing round)."""
        self._proposed = 0.0

    def compute_position_size(
        self, token: str, signal_strength: float = 1.0,
    ) -> float:
        """Compute position size respecting the concentration cap.

        The position size is the minimum of:
        - ``signal_strength * remaining_capital``
        - ``max_token_pct * total_capital`` (concentration cap)
        - Remaining capital (available minus already-proposed)

        Each call accumulates proposed exposure so that the total
        across all calls within a sizing round stays within capital.
        """
        max_per_token = self.config.capital * self.config.max_token_pct
        remaining = self.available_capital() - self._proposed
        if remaining <= 0:
            return 0.0
        desired = signal_strength * remaining
        size = min(desired, max_per_token, remaining)
        self._proposed += size
        return size

    # ------------------------------------------------------------------
    # Portfolio batch: rank and select tokens
    # ------------------------------------------------------------------

    def rank_and_select(self, scores: dict[str, float]) -> list[str]:
        """Rank tokens by score and select up to the concentration limit.

        Returns tokens sorted descending by score, up to
        ``floor(1 / max_token_pct)`` entries (the maximum number of
        positions that fit within the concentration cap).
        """
        max_positions = int(1.0 / self.config.max_token_pct)
        ranked = sorted(scores.keys(), key=lambda t: scores[t], reverse=True)
        return ranked[:max_positions]
