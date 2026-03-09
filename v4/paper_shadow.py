"""V4 Paper Trading — Shadow dual-pool rebalancing.

Tracks shadow spot/perp fund pools for observability.
Trade execution uses the unified capital pool (matching v4 backtest).
Shadow pools model what WOULD happen with real exchange accounts.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional

from v4.paper_config import PaperConfig
from v4.config import StrategySpec
from v4.position import Position, ClosedTrade, PositionManager
from v4.simulator import SimulationState


@dataclass
class RebalanceRecord:
    """Per-tick shadow rebalance record (AC24 schema)."""
    timestamp: str
    tick: int
    n_entry_candidates: int
    spot_capital_needed: float
    perp_capital_needed: float
    spot_shadow_available: float
    perp_shadow_available: float
    transfer_needed_usd: float
    direction: str                  # "perp->spot", "spot->perp", "none"
    would_have_blocked_entries: int
    blocked_entry_tokens: list[str]
    spot_shadow_after: float
    perp_shadow_after: float
    spot_deployed: float
    perp_deployed: float
    imbalance_pct: float

    def to_dict(self) -> dict:
        """Convert to JSON-serializable dict."""
        return {
            "timestamp": self.timestamp,
            "tick": self.tick,
            "n_entry_candidates": self.n_entry_candidates,
            "spot_capital_needed": self.spot_capital_needed,
            "perp_capital_needed": self.perp_capital_needed,
            "spot_shadow_available": self.spot_shadow_available,
            "perp_shadow_available": self.perp_shadow_available,
            "transfer_needed_usd": self.transfer_needed_usd,
            "direction": self.direction,
            "would_have_blocked_entries": self.would_have_blocked_entries,
            "blocked_entry_tokens": list(self.blocked_entry_tokens),
            "spot_shadow_after": self.spot_shadow_after,
            "perp_shadow_after": self.perp_shadow_after,
            "spot_deployed": self.spot_deployed,
            "perp_deployed": self.perp_deployed,
            "imbalance_pct": self.imbalance_pct,
        }


class ShadowRebalancer:
    """Shadow dual-pool accounting for spot and perp funds.

    Tracks what WOULD happen with real exchange accounts without
    affecting trade execution (which uses the unified capital pool).
    """

    def __init__(self, config: PaperConfig):
        self.config = config
        self._last_total_funding = 0.0

        # Compute initial shadow pool split based on strategy market types
        spot_alloc = 0.0
        perp_alloc = 0.0

        for spec in config.strategies:
            strategy_capital = config.capital * spec.weight
            if spec.market == "combined":
                # Default capital_split = 0.5
                capital_split = getattr(spec, "capital_split", 0.5)
                spot_alloc += strategy_capital * capital_split
                perp_alloc += strategy_capital * (1.0 - capital_split)
            elif spec.market == "perp":
                perp_alloc += strategy_capital
            elif spec.market == "spot":
                spot_alloc += strategy_capital

        # Any unallocated capital (weights sum < 1.0) split proportionally
        allocated = spot_alloc + perp_alloc
        remaining = config.capital - allocated
        if remaining > 0.0 and allocated > 0.0:
            spot_frac = spot_alloc / allocated
            spot_alloc += remaining * spot_frac
            perp_alloc += remaining * (1.0 - spot_frac)
        elif remaining > 0.0:
            # No strategies allocated, split 50/50
            spot_alloc += remaining * 0.5
            perp_alloc += remaining * 0.5

        self.spot_funds_shadow: float = spot_alloc
        self.perp_funds_shadow: float = perp_alloc
        self.spot_deployed: float = 0.0
        self.perp_deployed: float = 0.0

    def accrue_funding(self, state: SimulationState) -> None:
        """Sync shadow perp pool with funding changes.

        Positive total_funding = cost paid by perp positions => perp pool shrinks.
        Negative total_funding = income received => perp pool grows.
        """
        delta = state.total_funding - self._last_total_funding
        self.perp_funds_shadow -= delta
        self._last_total_funding = state.total_funding

    def compute(
        self,
        state: SimulationState,
        all_signals: dict,
        specs: dict,
        bar_maps: dict,
        tick_counter: int,
        config: PaperConfig,
        entry_candidates: list[dict] | None = None,
    ) -> RebalanceRecord:
        """Scan entry candidates and compute shadow transfer needed.

        Args:
            entry_candidates: List of dicts with keys:
                - token: str
                - market_type: "spot" or "perp"
                - margin_needed: float
        """
        if entry_candidates is None:
            entry_candidates = []

        spot_needed = 0.0
        perp_needed = 0.0
        blocked_count = 0
        blocked_tokens: list[str] = []

        for candidate in entry_candidates:
            margin = candidate["margin_needed"]
            mtype = candidate["market_type"]
            if mtype == "spot":
                spot_needed += margin
            else:
                perp_needed += margin

        # Check which entries would be blocked without transfer
        spot_available = self.spot_funds_shadow - self.spot_deployed
        perp_available = self.perp_funds_shadow - self.perp_deployed

        spot_shortfall = max(0.0, spot_needed - max(0.0, spot_available))
        perp_shortfall = max(0.0, perp_needed - max(0.0, perp_available))

        # Identify blocked entries
        if spot_shortfall > 0.0:
            remaining_spot = max(0.0, spot_available)
            for c in entry_candidates:
                if c["market_type"] == "spot":
                    if remaining_spot < c["margin_needed"]:
                        blocked_count += 1
                        blocked_tokens.append(c["token"])
                    else:
                        remaining_spot -= c["margin_needed"]

        if perp_shortfall > 0.0:
            remaining_perp = max(0.0, perp_available)
            for c in entry_candidates:
                if c["market_type"] == "perp":
                    if remaining_perp < c["margin_needed"]:
                        blocked_count += 1
                        blocked_tokens.append(c["token"])
                    else:
                        remaining_perp -= c["margin_needed"]

        # Compute transfer needed
        transfer_usd = 0.0
        direction = "none"
        if spot_shortfall > 0.0 and perp_shortfall == 0.0:
            transfer_usd = spot_shortfall
            direction = "perp->spot"
        elif perp_shortfall > 0.0 and spot_shortfall == 0.0:
            transfer_usd = perp_shortfall
            direction = "spot->perp"
        elif spot_shortfall > 0.0 and perp_shortfall > 0.0:
            # Both pools short — transfer net direction
            if spot_shortfall > perp_shortfall:
                transfer_usd = spot_shortfall
                direction = "perp->spot"
            else:
                transfer_usd = perp_shortfall
                direction = "spot->perp"

        # Execute shadow transfer
        if direction == "perp->spot":
            actual_transfer = min(transfer_usd, max(0.0, perp_available))
            self.perp_funds_shadow -= actual_transfer
            self.spot_funds_shadow += actual_transfer
        elif direction == "spot->perp":
            actual_transfer = min(transfer_usd, max(0.0, spot_available))
            self.spot_funds_shadow -= actual_transfer
            self.perp_funds_shadow += actual_transfer

        # Compute imbalance
        total_shadow = self.spot_funds_shadow + self.perp_funds_shadow
        if total_shadow > 0.0:
            imbalance = abs(self.spot_funds_shadow - self.perp_funds_shadow) / total_shadow * 100.0
        else:
            imbalance = 0.0

        return RebalanceRecord(
            timestamp="",  # filled by caller
            tick=tick_counter,
            n_entry_candidates=len(entry_candidates),
            spot_capital_needed=spot_needed,
            perp_capital_needed=perp_needed,
            spot_shadow_available=spot_available,
            perp_shadow_available=perp_available,
            transfer_needed_usd=transfer_usd,
            direction=direction,
            would_have_blocked_entries=blocked_count,
            blocked_entry_tokens=blocked_tokens,
            spot_shadow_after=self.spot_funds_shadow,
            perp_shadow_after=self.perp_funds_shadow,
            spot_deployed=self.spot_deployed,
            perp_deployed=self.perp_deployed,
            imbalance_pct=imbalance,
        )

    def update_pools(
        self,
        state: SimulationState,
        open_before: int = 0,
        closed_before: int = 0,
        open_ids_before: set | None = None,
    ) -> None:
        """Attribute new entries and closed trade P&L to correct shadow pools.

        Args:
            open_before: count of open positions before tick (legacy, fragile if exits shift indices)
            closed_before: number of closed trades before this tick's exits
            open_ids_before: set of position IDs open before tick (preferred — robust to index shifts)
        """
        pm = state.position_manager

        # Prefer ID-based tracking (robust to exits shifting list indices)
        if open_ids_before is not None:
            new_positions = [p for p in pm.open_positions if p.position_id not in open_ids_before]
        else:
            new_positions = pm.open_positions[open_before:]
        for pos in new_positions:
            # Deduct margin + entry fee from shadow pool (entry fee already in total_fees
            # which reduces portfolio_equity, so shadow must track it to maintain invariant)
            entry_fee = state._entry_fees_by_pos.get(pos.position_id, 0.0)
            cost = pos.margin_usd + entry_fee
            if pos.is_perp:
                self.perp_funds_shadow -= cost
                self.perp_deployed += pos.margin_usd
            else:
                self.spot_funds_shadow -= cost
                self.spot_deployed += pos.margin_usd

        # Process new closed trades (trades closed since closed_before)
        new_closed = pm.closed_trades[closed_before:]
        for trade in new_closed:
            # Return margin + P&L (net of exit_fee) to correct pool.
            # trade.pnl = raw_pnl - exit_fee - funding_cost.
            # We add back ONLY funding_cost because funding was already deducted
            # bar-by-bar via accrue_funding() — adding it again would double-count.
            # We do NOT add back exit_fee: the exit fee is a real cost that reduces
            # the capital returned. portfolio_equity also subtracts it (via total_fees),
            # so both shadow and portfolio_equity reflect the same exit_fee deduction.
            margin_return = trade.margin_usd + trade.pnl + trade.funding_cost
            if trade.is_perp:
                self.perp_funds_shadow += margin_return
                self.perp_deployed -= trade.margin_usd
            else:
                self.spot_funds_shadow += margin_return
                self.spot_deployed -= trade.margin_usd

    def get_pool_state(self) -> dict:
        """Return current shadow pool state for serialization."""
        return {
            "spot_funds": self.spot_funds_shadow,
            "perp_funds": self.perp_funds_shadow,
            "spot_deployed": self.spot_deployed,
            "perp_deployed": self.perp_deployed,
            "last_total_funding": self._last_total_funding,
        }

    def restore_pool_state(self, pools: dict) -> None:
        """Restore shadow pool state from deserialized data."""
        self.spot_funds_shadow = pools["spot_funds"]
        self.perp_funds_shadow = pools["perp_funds"]
        self.spot_deployed = pools.get("spot_deployed", 0.0)
        self.perp_deployed = pools.get("perp_deployed", 0.0)
        self._last_total_funding = pools.get("last_total_funding", 0.0)
