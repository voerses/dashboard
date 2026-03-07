"""AC4: Realistic cost simulation tests.

Tests verify:
- Correct fee rates per exchange (Kraken, Binance) at base tier
- Fee tier override mechanism
- Volume-dependent slippage: base_bps + (order_size / adv) * impact_factor
- Exchange-specific slippage defaults by token tier
- Latency buffer per exchange
- apply_entry_cost returns price ABOVE market (worse for buyer)
- apply_exit_cost returns price BELOW market (worse for seller)
"""

import pytest

from v3.cost_model import CostModel


class TestKrakenBaseTierFees:
    """Kraken base tier: 0.25% maker / 0.40% taker."""

    def test_kraken_base_tier_maker_fee(self):
        model = CostModel(exchange="kraken")
        assert model.maker_fee == pytest.approx(0.0025)

    def test_kraken_base_tier_taker_fee(self):
        model = CostModel(exchange="kraken")
        assert model.taker_fee == pytest.approx(0.0040)


class TestBinanceBaseTierFees:
    """Binance base tier: 0.10% maker / 0.10% taker."""

    def test_binance_base_tier_maker_fee(self):
        model = CostModel(exchange="binance")
        assert model.maker_fee == pytest.approx(0.0010)

    def test_binance_base_tier_taker_fee(self):
        model = CostModel(exchange="binance")
        assert model.taker_fee == pytest.approx(0.0010)


class TestFeeTierOverride:
    """Fee tier override replaces defaults when provided."""

    def test_override_kraken_maker_fee(self):
        model = CostModel(exchange="kraken", maker_fee=0.0016, taker_fee=0.0026)
        assert model.maker_fee == pytest.approx(0.0016)

    def test_override_kraken_taker_fee(self):
        model = CostModel(exchange="kraken", maker_fee=0.0016, taker_fee=0.0026)
        assert model.taker_fee == pytest.approx(0.0026)

    def test_override_binance_maker_fee(self):
        model = CostModel(exchange="binance", maker_fee=0.00075, taker_fee=0.00075)
        assert model.maker_fee == pytest.approx(0.00075)


class TestSlippageModel:
    """Volume-dependent slippage: base_bps + (order_size / adv) * impact_factor.

    Exchange-specific slippage defaults:
      Binance  T1=5bps, T2=12bps, T3=25bps
      Kraken   T1=12bps, T2=30bps, T3=60bps
    """

    def test_binance_t1_slippage_default(self):
        model = CostModel(exchange="binance")
        slippage = model.compute_slippage(
            token_tier=1, order_size=1000.0, adv=1_000_000.0
        )
        # base_bps for Binance T1 = 5bps = 0.0005
        expected_base = 0.0005
        expected_impact = (1000.0 / 1_000_000.0) * model.impact_factor
        assert slippage == pytest.approx(expected_base + expected_impact, rel=1e-4)

    def test_binance_t2_slippage_default(self):
        model = CostModel(exchange="binance")
        slippage = model.compute_slippage(
            token_tier=2, order_size=1000.0, adv=500_000.0
        )
        expected_base = 0.0012  # 12bps
        expected_impact = (1000.0 / 500_000.0) * model.impact_factor
        assert slippage == pytest.approx(expected_base + expected_impact, rel=1e-4)

    def test_binance_t3_slippage_default(self):
        model = CostModel(exchange="binance")
        slippage = model.compute_slippage(
            token_tier=3, order_size=1000.0, adv=100_000.0
        )
        expected_base = 0.0025  # 25bps
        expected_impact = (1000.0 / 100_000.0) * model.impact_factor
        assert slippage == pytest.approx(expected_base + expected_impact, rel=1e-4)

    def test_kraken_t1_slippage_default(self):
        model = CostModel(exchange="kraken")
        slippage = model.compute_slippage(
            token_tier=1, order_size=2000.0, adv=800_000.0
        )
        expected_base = 0.0012  # 12bps
        expected_impact = (2000.0 / 800_000.0) * model.impact_factor
        assert slippage == pytest.approx(expected_base + expected_impact, rel=1e-4)

    def test_kraken_t2_slippage_default(self):
        model = CostModel(exchange="kraken")
        slippage = model.compute_slippage(
            token_tier=2, order_size=2000.0, adv=400_000.0
        )
        expected_base = 0.0030  # 30bps
        expected_impact = (2000.0 / 400_000.0) * model.impact_factor
        assert slippage == pytest.approx(expected_base + expected_impact, rel=1e-4)

    def test_kraken_t3_slippage_default(self):
        model = CostModel(exchange="kraken")
        slippage = model.compute_slippage(
            token_tier=3, order_size=2000.0, adv=200_000.0
        )
        expected_base = 0.0060  # 60bps
        expected_impact = (2000.0 / 200_000.0) * model.impact_factor
        assert slippage == pytest.approx(expected_base + expected_impact, rel=1e-4)

    def test_slippage_increases_with_larger_order(self):
        model = CostModel(exchange="binance")
        small = model.compute_slippage(token_tier=1, order_size=100.0, adv=1_000_000.0)
        large = model.compute_slippage(token_tier=1, order_size=10_000.0, adv=1_000_000.0)
        assert large > small


class TestLatencyBuffer:
    """Kraken 200ms, Binance 100ms."""

    def test_kraken_latency_buffer(self):
        model = CostModel(exchange="kraken")
        assert model.latency_ms == 200

    def test_binance_latency_buffer(self):
        model = CostModel(exchange="binance")
        assert model.latency_ms == 100


class TestApplyCost:
    """apply_entry_cost: price ABOVE market.  apply_exit_cost: price BELOW market."""

    def test_entry_cost_above_market(self):
        model = CostModel(exchange="binance")
        market_price = 40_000.0
        adjusted = model.apply_entry_cost(
            market_price, order_size=1000.0, adv=1_000_000.0, token_tier=1
        )
        assert adjusted > market_price

    def test_exit_cost_below_market(self):
        model = CostModel(exchange="binance")
        market_price = 40_000.0
        adjusted = model.apply_exit_cost(
            market_price, order_size=1000.0, adv=1_000_000.0, token_tier=1
        )
        assert adjusted < market_price

    def test_entry_cost_includes_fees_and_slippage(self):
        model = CostModel(exchange="kraken")
        market_price = 2500.0
        adjusted = model.apply_entry_cost(
            market_price, order_size=500.0, adv=500_000.0, token_tier=2
        )
        # Must be worse than just fee alone
        fee_only = market_price * (1 + model.taker_fee)
        assert adjusted > market_price
        # The adjusted price should be at least as much as fee-only
        assert adjusted >= fee_only

    def test_exit_cost_includes_fees_and_slippage(self):
        model = CostModel(exchange="kraken")
        market_price = 2500.0
        adjusted = model.apply_exit_cost(
            market_price, order_size=500.0, adv=500_000.0, token_tier=2
        )
        fee_only = market_price * (1 - model.taker_fee)
        assert adjusted < market_price
        assert adjusted <= fee_only
