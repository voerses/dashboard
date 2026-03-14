"""AC7, AC11, AC12: Portfolio groups with shared capital and concentration caps.

Tests verify:
- AC7: Portfolio strategies use same batch pipeline as backtest — DataLoader
        builds panels, calls same rank_and_select() functions
- AC11: Shared capital pools with concentration caps (default 15% per token)
- AC12: Multiple concurrent groups with independent capital
"""

import pytest

from v3.portfolio_group import PortfolioGroup, GroupConfig, StrategyAllocation


# ---------------------------------------------------------------------------
# GroupConfig and StrategyAllocation structure
# ---------------------------------------------------------------------------


class TestGroupConfigStructure:
    """GroupConfig has required fields."""

    def test_group_config_has_group_id(self, sample_portfolio_group_config):
        config = GroupConfig(**sample_portfolio_group_config)
        assert config.group_id == "momentum_group"

    def test_group_config_has_capital(self, sample_portfolio_group_config):
        config = GroupConfig(**sample_portfolio_group_config)
        assert config.capital == pytest.approx(100000.0)

    def test_group_config_has_max_token_pct(self, sample_portfolio_group_config):
        config = GroupConfig(**sample_portfolio_group_config)
        assert config.max_token_pct == pytest.approx(0.15)

    def test_group_config_has_strategies(self, sample_portfolio_group_config):
        config = GroupConfig(**sample_portfolio_group_config)
        assert len(config.strategies) == 3


class TestStrategyAllocationStructure:
    """StrategyAllocation has required fields."""

    def test_allocation_has_strategy_id(self):
        alloc = StrategyAllocation(
            strategy_id="s11", weight=0.5, type="momentum",
        )
        assert alloc.strategy_id == "s11"

    def test_allocation_has_weight(self):
        alloc = StrategyAllocation(
            strategy_id="s11", weight=0.5, type="momentum",
        )
        assert alloc.weight == pytest.approx(0.5)

    def test_allocation_has_type(self):
        alloc = StrategyAllocation(
            strategy_id="s11", weight=0.5, type="momentum",
        )
        assert alloc.type == "momentum"


# ---------------------------------------------------------------------------
# AC7: Portfolio batch pipeline
# ---------------------------------------------------------------------------


class TestPortfolioBatchPipeline:
    """Portfolio strategies use same batch pipeline as backtest."""

    def test_portfolio_group_has_rank_and_select(self, sample_portfolio_group_config):
        """PortfolioGroup exposes rank_and_select method."""
        config = GroupConfig(**sample_portfolio_group_config)
        group = PortfolioGroup(config=config)
        assert hasattr(group, "rank_and_select")
        assert callable(group.rank_and_select)

    def test_rank_and_select_returns_token_list(self, sample_portfolio_group_config):
        """rank_and_select returns a list of selected tokens."""
        config = GroupConfig(**sample_portfolio_group_config)
        group = PortfolioGroup(config=config)
        # Provide a mock panel (dict of token -> score)
        scores = {
            "BTC/USDT": 0.85,
            "ETH/USDT": 0.72,
            "SOL/USDT": 0.90,
            "DOGE/USDT": 0.30,
            "AVAX/USDT": 0.65,
        }
        selected = group.rank_and_select(scores)
        assert isinstance(selected, list)
        assert len(selected) > 0

    def test_rank_and_select_ranks_by_score(self, sample_portfolio_group_config):
        """Top-scoring tokens are selected first."""
        config = GroupConfig(**sample_portfolio_group_config)
        group = PortfolioGroup(config=config)
        scores = {
            "BTC/USDT": 0.50,
            "ETH/USDT": 0.30,
            "SOL/USDT": 0.90,
        }
        selected = group.rank_and_select(scores)
        # SOL should be first (highest score)
        assert selected[0] == "SOL/USDT"

    def test_rank_and_select_respects_concentration_cap(
        self, sample_portfolio_group_config,
    ):
        """Number of selected tokens is bounded by concentration cap."""
        config = GroupConfig(**sample_portfolio_group_config)
        group = PortfolioGroup(config=config)
        # max_token_pct=0.15, capital=100000 → max 100000*0.15=15000 per token
        # With many high-scoring tokens, cap limits exposure
        scores = {f"TOKEN{i}/USDT": 0.9 for i in range(20)}
        selected = group.rank_and_select(scores)
        # With 15% cap, each position ≤ 15% of capital
        # So at least ceil(1/0.15)=7 positions needed to fill capital
        # The selected count should reflect the concentration limit
        max_positions = int(1.0 / config.max_token_pct)
        assert len(selected) >= max_positions, (
            f"With {config.max_token_pct:.0%} cap, expected at least "
            f"{max_positions} tokens selected, got {len(selected)}"
        )


# ---------------------------------------------------------------------------
# AC11: Shared capital pools with concentration caps
# ---------------------------------------------------------------------------


class TestConcentrationCaps:
    """Default 15% per token concentration cap."""

    def test_default_max_token_pct(self):
        """Default max_token_pct is 0.15 (15%)."""
        config = GroupConfig(
            group_id="test",
            capital=100000.0,
            strategies=[],
        )
        assert config.max_token_pct == pytest.approx(0.15)

    def test_position_size_respects_cap(self, sample_portfolio_group_config):
        """Individual position size cannot exceed max_token_pct * capital."""
        config = GroupConfig(**sample_portfolio_group_config)
        group = PortfolioGroup(config=config)
        max_per_token = config.capital * config.max_token_pct
        size = group.compute_position_size(
            token="BTC/USDT", signal_strength=1.0,
        )
        assert size <= max_per_token

    def test_custom_concentration_cap(self):
        """Custom max_token_pct overrides default."""
        config = GroupConfig(
            group_id="custom",
            capital=100000.0,
            max_token_pct=0.25,
            strategies=[],
        )
        assert config.max_token_pct == pytest.approx(0.25)

    def test_total_exposure_bounded_by_capital(self, sample_portfolio_group_config):
        """Total exposure across all positions cannot exceed capital."""
        config = GroupConfig(**sample_portfolio_group_config)
        group = PortfolioGroup(config=config)
        tokens = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "AVAX/USDT",
                   "DOGE/USDT", "BONK/USDT", "SUI/USDT"]
        total = sum(
            group.compute_position_size(token=t, signal_strength=1.0)
            for t in tokens
        )
        assert total <= config.capital


class TestSharedCapitalPool:
    """Strategies in a group share the same capital pool."""

    def test_strategies_draw_from_same_pool(self, sample_portfolio_group_config):
        """Multiple strategies in a group reduce available capital."""
        config = GroupConfig(**sample_portfolio_group_config)
        group = PortfolioGroup(config=config)
        initial_available = group.available_capital()
        assert initial_available == pytest.approx(config.capital)

        # Allocate capital to first strategy
        group.allocate(strategy_id="s11", token="BTC/USDT", amount=15000.0)
        remaining = group.available_capital()
        assert remaining == pytest.approx(config.capital - 15000.0)

    def test_allocation_reduces_available(self, sample_portfolio_group_config):
        config = GroupConfig(**sample_portfolio_group_config)
        group = PortfolioGroup(config=config)
        group.allocate(strategy_id="s11", token="BTC/USDT", amount=10000.0)
        group.allocate(strategy_id="s09", token="ETH/USDT", amount=8000.0)
        assert group.available_capital() == pytest.approx(
            config.capital - 10000.0 - 8000.0
        )

    def test_release_returns_capital(self, sample_portfolio_group_config):
        config = GroupConfig(**sample_portfolio_group_config)
        group = PortfolioGroup(config=config)
        group.allocate(strategy_id="s11", token="BTC/USDT", amount=10000.0)
        group.release(strategy_id="s11", token="BTC/USDT", amount=10000.0)
        assert group.available_capital() == pytest.approx(config.capital)

    def test_cannot_allocate_beyond_capital(self, sample_portfolio_group_config):
        """Allocation exceeding available capital raises an error."""
        config = GroupConfig(**sample_portfolio_group_config)
        group = PortfolioGroup(config=config)
        with pytest.raises(Exception):
            group.allocate(
                strategy_id="s11", token="BTC/USDT",
                amount=config.capital + 1000.0,
            )


# ---------------------------------------------------------------------------
# AC12: Multiple concurrent groups with independent capital
# ---------------------------------------------------------------------------


class TestMultipleConcurrentGroups:
    """Multiple groups operate with independent capital pools."""

    def test_two_groups_independent_capital(
        self, sample_portfolio_group_config, sample_portfolio_group_config_b,
    ):
        """Two groups have independent capital — one's allocation doesn't affect the other."""
        config_a = GroupConfig(**sample_portfolio_group_config)
        config_b = GroupConfig(**sample_portfolio_group_config_b)
        group_a = PortfolioGroup(config=config_a)
        group_b = PortfolioGroup(config=config_b)

        group_a.allocate(strategy_id="s11", token="BTC/USDT", amount=50000.0)

        # Group B should still have its full capital
        assert group_b.available_capital() == pytest.approx(config_b.capital)

    def test_groups_have_different_ids(
        self, sample_portfolio_group_config, sample_portfolio_group_config_b,
    ):
        config_a = GroupConfig(**sample_portfolio_group_config)
        config_b = GroupConfig(**sample_portfolio_group_config_b)
        assert config_a.group_id != config_b.group_id

    def test_groups_have_different_strategy_sets(
        self, sample_portfolio_group_config, sample_portfolio_group_config_b,
    ):
        config_a = GroupConfig(**sample_portfolio_group_config)
        config_b = GroupConfig(**sample_portfolio_group_config_b)
        strategy_ids_a = {s["strategy_id"] for s in sample_portfolio_group_config["strategies"]}
        strategy_ids_b = {s["strategy_id"] for s in sample_portfolio_group_config_b["strategies"]}
        # Groups should have non-overlapping strategies (in this test setup)
        assert strategy_ids_a.isdisjoint(strategy_ids_b)

    def test_group_tracks_own_positions_only(
        self, sample_portfolio_group_config, sample_portfolio_group_config_b,
    ):
        """Each group only knows about its own allocations."""
        config_a = GroupConfig(**sample_portfolio_group_config)
        config_b = GroupConfig(**sample_portfolio_group_config_b)
        group_a = PortfolioGroup(config=config_a)
        group_b = PortfolioGroup(config=config_b)

        group_a.allocate(strategy_id="s11", token="BTC/USDT", amount=15000.0)
        group_b.allocate(strategy_id="s30", token="ETH/USDT", amount=10000.0)

        allocations_a = group_a.get_allocations()
        allocations_b = group_b.get_allocations()

        # Group A should not contain group B's allocation
        a_tokens = [a["token"] for a in allocations_a]
        b_tokens = [a["token"] for a in allocations_b]
        assert "BTC/USDT" in a_tokens
        assert "ETH/USDT" in b_tokens
        assert "ETH/USDT" not in a_tokens, (
            "Group A should not contain Group B's ETH/USDT allocation"
        )
        assert "BTC/USDT" not in b_tokens, (
            "Group B should not contain Group A's BTC/USDT allocation"
        )
