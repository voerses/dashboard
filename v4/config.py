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
    # Circuit breaker: emergency exit during no_stop_bars window (0 = disabled)
    circuit_breaker_r: float = 4.0        # exit when loss >= Nx initial_risk
    # Pump-and-dump entry filters (all default enabled)
    pump_filter_range_threshold: float = 4.0   # block entry when (high-low)/ATR > threshold (0 = disabled)
    pump_filter_adv_floor: float = 5_000_000   # ADV below this gets pump penalty applied (0 = disabled)
    pump_filter_adv_penalty: float = 0.5       # sizing multiplier for tokens below adv_floor
    pump_filter_funding_zscore: float = 3.0    # block LONG entries when funding z-score > this (0 = disabled)
