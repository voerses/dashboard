"""AC9: Trade simulation with realistic fill prices via CostModel.

Tests verify:
- TradeSimulator produces SimulatedFill objects with token, side, price,
  fill_price, slippage_bps, fee, size_usd
- Uses CostModel for realistic fill price calculation
- Entry fills are worse than market (higher for buys)
- Exit fills are worse than market (lower for sells)
- Slippage and fees are included in the fill
"""

import pytest

from v3.trade_simulator import TradeSimulator, SimulatedFill
from v3.cost_model import CostModel


# ---------------------------------------------------------------------------
# SimulatedFill structure
# ---------------------------------------------------------------------------


class TestSimulatedFillStructure:
    """SimulatedFill has all required fields."""

    def test_fill_has_token(self):
        fill = SimulatedFill(
            token="BTC/USDT", side="buy", price=40000.0,
            fill_price=40050.0, slippage_bps=5.0, fee=40.0,
            size_usd=10000.0,
        )
        assert fill.token == "BTC/USDT"

    def test_fill_has_side(self):
        fill = SimulatedFill(
            token="BTC/USDT", side="buy", price=40000.0,
            fill_price=40050.0, slippage_bps=5.0, fee=40.0,
            size_usd=10000.0,
        )
        assert fill.side == "buy"

    def test_fill_has_price_and_fill_price(self):
        fill = SimulatedFill(
            token="BTC/USDT", side="buy", price=40000.0,
            fill_price=40050.0, slippage_bps=5.0, fee=40.0,
            size_usd=10000.0,
        )
        assert fill.price == pytest.approx(40000.0)
        assert fill.fill_price == pytest.approx(40050.0)

    def test_fill_has_slippage_bps(self):
        fill = SimulatedFill(
            token="BTC/USDT", side="buy", price=40000.0,
            fill_price=40050.0, slippage_bps=5.0, fee=40.0,
            size_usd=10000.0,
        )
        assert fill.slippage_bps == pytest.approx(5.0)

    def test_fill_has_fee(self):
        fill = SimulatedFill(
            token="BTC/USDT", side="buy", price=40000.0,
            fill_price=40050.0, slippage_bps=5.0, fee=40.0,
            size_usd=10000.0,
        )
        assert fill.fee == pytest.approx(40.0)

    def test_fill_has_size_usd(self):
        fill = SimulatedFill(
            token="BTC/USDT", side="buy", price=40000.0,
            fill_price=40050.0, slippage_bps=5.0, fee=40.0,
            size_usd=10000.0,
        )
        assert fill.size_usd == pytest.approx(10000.0)


# ---------------------------------------------------------------------------
# TradeSimulator with CostModel
# ---------------------------------------------------------------------------


class TestTradeSimulatorEntry:
    """Entry fills use CostModel — fill_price worse than market for buys."""

    def test_simulate_entry_returns_fill(self):
        cost_model = CostModel(exchange="binance")
        sim = TradeSimulator(cost_model=cost_model)
        fill = sim.simulate_entry(
            token="BTC/USDT",
            market_price=40000.0,
            size_usd=10000.0,
            token_tier=1,
            adv=1_000_000.0,
        )
        assert isinstance(fill, SimulatedFill)

    def test_entry_fill_price_above_market(self):
        """Buy entry: fill_price > market_price (worse for buyer)."""
        cost_model = CostModel(exchange="binance")
        sim = TradeSimulator(cost_model=cost_model)
        fill = sim.simulate_entry(
            token="BTC/USDT",
            market_price=40000.0,
            size_usd=10000.0,
            token_tier=1,
            adv=1_000_000.0,
        )
        assert fill.fill_price > fill.price

    def test_entry_slippage_is_positive(self):
        """Slippage on entry should be positive (cost)."""
        cost_model = CostModel(exchange="binance")
        sim = TradeSimulator(cost_model=cost_model)
        fill = sim.simulate_entry(
            token="BTC/USDT",
            market_price=40000.0,
            size_usd=10000.0,
            token_tier=1,
            adv=1_000_000.0,
        )
        assert fill.slippage_bps > 0

    def test_entry_fee_is_positive(self):
        """Fee on entry should be positive."""
        cost_model = CostModel(exchange="binance")
        sim = TradeSimulator(cost_model=cost_model)
        fill = sim.simulate_entry(
            token="BTC/USDT",
            market_price=40000.0,
            size_usd=10000.0,
            token_tier=1,
            adv=1_000_000.0,
        )
        assert fill.fee > 0

    def test_entry_side_is_buy(self):
        cost_model = CostModel(exchange="binance")
        sim = TradeSimulator(cost_model=cost_model)
        fill = sim.simulate_entry(
            token="BTC/USDT",
            market_price=40000.0,
            size_usd=10000.0,
            token_tier=1,
            adv=1_000_000.0,
        )
        assert fill.side == "buy"

    def test_larger_order_has_more_slippage(self):
        """Larger orders incur more slippage."""
        cost_model = CostModel(exchange="binance")
        sim = TradeSimulator(cost_model=cost_model)
        small = sim.simulate_entry(
            token="BTC/USDT", market_price=40000.0,
            size_usd=1000.0, token_tier=1, adv=1_000_000.0,
        )
        large = sim.simulate_entry(
            token="BTC/USDT", market_price=40000.0,
            size_usd=50000.0, token_tier=1, adv=1_000_000.0,
        )
        assert large.slippage_bps > small.slippage_bps


class TestTradeSimulatorExit:
    """Exit fills use CostModel — fill_price worse than market for sells."""

    def test_simulate_exit_returns_fill(self):
        cost_model = CostModel(exchange="binance")
        sim = TradeSimulator(cost_model=cost_model)
        fill = sim.simulate_exit(
            token="BTC/USDT",
            market_price=41000.0,
            size_usd=10000.0,
            token_tier=1,
            adv=1_000_000.0,
        )
        assert isinstance(fill, SimulatedFill)

    def test_exit_fill_price_below_market(self):
        """Sell exit: fill_price < market_price (worse for seller)."""
        cost_model = CostModel(exchange="binance")
        sim = TradeSimulator(cost_model=cost_model)
        fill = sim.simulate_exit(
            token="BTC/USDT",
            market_price=41000.0,
            size_usd=10000.0,
            token_tier=1,
            adv=1_000_000.0,
        )
        assert fill.fill_price < fill.price

    def test_exit_side_is_sell(self):
        cost_model = CostModel(exchange="binance")
        sim = TradeSimulator(cost_model=cost_model)
        fill = sim.simulate_exit(
            token="BTC/USDT",
            market_price=41000.0,
            size_usd=10000.0,
            token_tier=1,
            adv=1_000_000.0,
        )
        assert fill.side == "sell"

    def test_exit_fee_is_positive(self):
        cost_model = CostModel(exchange="binance")
        sim = TradeSimulator(cost_model=cost_model)
        fill = sim.simulate_exit(
            token="BTC/USDT",
            market_price=41000.0,
            size_usd=10000.0,
            token_tier=1,
            adv=1_000_000.0,
        )
        assert fill.fee > 0


class TestTradeSimulatorExchangeVariation:
    """Different exchanges produce different cost profiles."""

    def test_kraken_higher_fees_than_binance(self):
        """Kraken has higher base fees than Binance."""
        binance_sim = TradeSimulator(cost_model=CostModel(exchange="binance"))
        kraken_sim = TradeSimulator(cost_model=CostModel(exchange="kraken"))

        binance_fill = binance_sim.simulate_entry(
            token="BTC/USDT", market_price=40000.0,
            size_usd=10000.0, token_tier=1, adv=1_000_000.0,
        )
        kraken_fill = kraken_sim.simulate_entry(
            token="BTC/USDT", market_price=40000.0,
            size_usd=10000.0, token_tier=1, adv=1_000_000.0,
        )
        assert kraken_fill.fee > binance_fill.fee

    def test_fill_preserves_token_name(self):
        """Token name is preserved in the fill."""
        sim = TradeSimulator(cost_model=CostModel(exchange="binance"))
        fill = sim.simulate_entry(
            token="SOL/USDT", market_price=100.0,
            size_usd=5000.0, token_tier=2, adv=500_000.0,
        )
        assert fill.token == "SOL/USDT"

    def test_fill_preserves_size_usd(self):
        """size_usd in the fill matches the requested amount."""
        sim = TradeSimulator(cost_model=CostModel(exchange="binance"))
        fill = sim.simulate_entry(
            token="ETH/USDT", market_price=2500.0,
            size_usd=7500.0, token_tier=1, adv=2_000_000.0,
        )
        assert fill.size_usd == pytest.approx(7500.0)
