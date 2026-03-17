"""V4 Portfolio Backtest — Configuration dataclasses."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class StrategySpec:
    """Specification for one strategy in the portfolio."""
    strategy_id: str              # e.g. "s30"
    weight: float = 1.0           # fraction of portfolio equity
    max_positions: int = 15       # per-strategy position limit
    market: str = "combined"      # "spot", "perp", "combined"
    strategy_type: str = "per_token"  # "per_token" (Class A) or "portfolio" (Class B)
    # Per-strategy risk controls (0 = disabled)
    circuit_breaker_r: float = 0.0               # emergency exit at Nx initial risk (e.g. 4.0)
    pump_filter_funding_zscore: float = 0.0      # block long entries when funding z-score > threshold
    pump_filter_range_threshold: float = 0.0     # block entries when bar range/ATR > threshold

    @classmethod
    def from_dict(cls, d: dict) -> StrategySpec:
        """Create from a JSON-parsed dict.  Single source of truth for field mapping."""
        return cls(
            strategy_id=d["strategy_id"],
            weight=d.get("weight", 1.0),
            max_positions=d.get("max_positions", 15),
            market=d.get("market", "combined"),
            strategy_type=d.get("strategy_type", "per_token"),
            circuit_breaker_r=d.get("circuit_breaker_r", 0.0),
            pump_filter_funding_zscore=d.get("pump_filter_funding_zscore", 0.0),
            pump_filter_range_threshold=d.get("pump_filter_range_threshold", 0.0),
        )


@dataclass
class PortfolioConfig:
    """Portfolio-level configuration for the backtest."""
    strategies: list[StrategySpec] = field(default_factory=list)
    capital: float = 200_000
    max_portfolio_positions: int = 40
    concentration_limit: float = 0.10    # 10% per-token across strategies
    adv_cap_pct: float = 0.05           # 5% of rolling ADV (v4-only constraint)
    min_position_usd: float = 200.0
    exchange: str = "binance"
    base_spread_bps: float = 3.0
    impact_coeff: float = 0.03
    seed: int = 42
    train_bars: int = 8760              # 365 days walk-forward
    recal_bars: int = 2160              # 90 days recalibration
    purge_bars: int = 120               # 5 days purge
    stress_adv_multiplier: float = 1.0  # ADV multiplier for stop/liquidation exits (1.0 = no stress)
    max_slip_bps: float = 300           # max slippage cap in basis points
    # Conviction-based entry ordering: "shuffle" (random, default), "ranked" (by conviction),
    # "hybrid" (top-N by conviction tiers, shuffle within tiers)
    conviction_mode: str = "shuffle"
    min_conviction_threshold: float = 0.0  # skip entries below this conviction level (0 = no filter)
