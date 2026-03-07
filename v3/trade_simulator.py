"""Trade execution simulator for the live paper trading engine.

Wraps CostModel to simulate realistic fills with slippage and fees.
Returns SimulatedFill objects for entries and exits.
"""

from __future__ import annotations

from dataclasses import dataclass

from v3.cost_model import CostModel


@dataclass
class SimulatedFill:
    """Result of a simulated trade execution."""

    token: str
    side: str           # 'buy' or 'sell'
    price: float        # market price at time of fill
    fill_price: float   # adjusted price after costs
    slippage_bps: float
    fee: float          # fee in USD
    size_usd: float


class TradeSimulator:
    """Simulates trade fills using a CostModel for realistic pricing."""

    def __init__(self, cost_model: CostModel):
        self.cost_model = cost_model

    def simulate_entry(
        self,
        token: str,
        market_price: float,
        size_usd: float,
        token_tier: int,
        adv: float,
    ) -> SimulatedFill:
        """Simulate a buy entry — fill_price > market_price."""
        fill_price = self.cost_model.apply_entry_cost(
            market_price, order_size=size_usd, adv=adv, token_tier=token_tier,
        )
        slippage_bps = self.cost_model.compute_slippage(
            token_tier, size_usd, adv,
        ) * 10_000
        fee = size_usd * self.cost_model.taker_fee

        return SimulatedFill(
            token=token,
            side="buy",
            price=market_price,
            fill_price=fill_price,
            slippage_bps=slippage_bps,
            fee=fee,
            size_usd=size_usd,
        )

    def simulate_exit(
        self,
        token: str,
        market_price: float,
        size_usd: float,
        token_tier: int,
        adv: float,
    ) -> SimulatedFill:
        """Simulate a sell exit — fill_price < market_price."""
        fill_price = self.cost_model.apply_exit_cost(
            market_price, order_size=size_usd, adv=adv, token_tier=token_tier,
        )
        slippage_bps = self.cost_model.compute_slippage(
            token_tier, size_usd, adv,
        ) * 10_000
        fee = size_usd * self.cost_model.taker_fee

        return SimulatedFill(
            token=token,
            side="sell",
            price=market_price,
            fill_price=fill_price,
            slippage_bps=slippage_bps,
            fee=fee,
            size_usd=size_usd,
        )
