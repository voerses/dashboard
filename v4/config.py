"""V4 Portfolio Backtest — Configuration dataclasses."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional, Tuple


# ---------------------------------------------------------------------------
# SizingDefaults — engine-level sizing constants
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SizingDefaults:
    """Engine-level sizing constants. Immutable once constructed."""

    # --- Position sizing model ---
    edge_minimum: float = 0.10        # min edge to allow entry
    target_vol: float = 0.02          # vol scaling numerator
    spot_max_equity_pct: float = 1.0  # max position as fraction of equity on spot

    # --- ADV-to-sizing curve defaults ---
    kelly_mult_floor: float = 0.15
    kelly_mult_range: float = 0.35
    cap_pct_floor: float = 0.02
    cap_pct_range: float = 0.10
    adv_scaling_divisor: float = 5.0

    # --- Strategy-level direct overrides (bypass ADV curve) ---
    kelly_mult_override: float = 0.0  # If > 0, use this fixed Kelly instead of ADV curve
    kelly_mult_scale: float = 1.0     # Multiply ADV-curve output by this (1.0 = no change)
    cap_pct_override: float = 0.0     # If > 0, use this fixed cap_pct instead of ADV curve
    cap_pct_scale: float = 1.0        # Multiply ADV-curve output by this

    # --- Liquidity gating ---
    min_adv_usd: float = 500_000      # minimum ADV to trade
    adv_lookback_days: int = 30       # rolling ADV window

    # --- Simulator constraints (NOT strategy-overridable) ---
    unrealized_pnl_floor: float = 0.85  # sizing_eq >= portfolio_eq * this
    funding_buffer_pct: float = 0.01    # reserve for funding costs
    vol_floor: float = 0.005            # min volatility (prevents division explosion)


# Parameters strategies can override via sizing_overrides in StrategySpec
SAFETY_RAILS: dict[str, tuple[float, float]] = {
    "edge_minimum":        (0.05, 0.50),
    "target_vol":          (0.005, 0.05),
    "spot_max_equity_pct": (0.10, 1.0),
    "kelly_mult_override": (0.05, 0.50),
    "kelly_mult_scale":    (0.5, 2.0),
    "cap_pct_override":    (0.01, 0.30),
    "cap_pct_scale":       (0.5, 2.0),
    "min_adv_usd":         (100_000, 10_000_000),
    "adv_lookback_days":   (7, 90),
    # ADV-to-sizing curve shape (wide bounds for research experimentation)
    "kelly_mult_floor":    (0.05, 0.40),
    "kelly_mult_range":    (0.10, 0.80),
    "cap_pct_floor":       (0.005, 0.08),
    "cap_pct_range":       (0.02, 0.30),
    "adv_scaling_divisor": (1.0, 20.0),
}

# Engine-absolute — strategies CANNOT override these (safety-critical params)
NON_OVERRIDABLE = {
    "unrealized_pnl_floor",
    "funding_buffer_pct",
    "vol_floor",
}


@dataclass(frozen=True)
class RegimeConfig:
    """Custom regime detection parameters for per-strategy regime overrides."""
    adx_threshold: float = 25.0
    crisis_mult: float = 2.0
    quiet_mult: float = 0.7
    ema_pair: Tuple[int, int] = (20, 50)
    min_periods: int = 60

    def __post_init__(self):
        # JSON parses lists not tuples; coerce to tuple for equality checks
        if isinstance(self.ema_pair, list):
            object.__setattr__(self, 'ema_pair', tuple(self.ema_pair))


def resolve_sizing(defaults: SizingDefaults, overrides: dict) -> SizingDefaults:
    """Merge strategy overrides into defaults, enforcing safety rails.

    Raises ValueError for unknown keys, non-overridable keys, out-of-bounds values,
    or dangerous parameter combinations.
    """
    if not overrides:
        return defaults

    all_fields = {f.name for f in defaults.__dataclass_fields__.values()}

    for key in overrides:
        if key not in all_fields:
            raise ValueError(f"Unknown sizing override key '{key}'")
        if key in NON_OVERRIDABLE:
            raise ValueError(f"Sizing parameter '{key}' is not overridable by strategies")

    for key, value in overrides.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(
                f"Sizing override '{key}' must be numeric, got {type(value).__name__}"
            )
        if key in SAFETY_RAILS:
            # Allow sentinel 0.0 for override fields (means "disabled/use curve")
            if key.endswith("_override") and value == 0.0:
                continue
            lo, hi = SAFETY_RAILS[key]
            if not (lo <= value <= hi):
                raise ValueError(
                    f"Sizing override '{key}' = {value} is out of bounds [{lo}, {hi}]"
                )

    merged = {**asdict(defaults), **overrides}
    # Enforce int fields (JSON parses as float)
    if "adv_lookback_days" in merged:
        merged["adv_lookback_days"] = int(merged["adv_lookback_days"])
    resolved = SizingDefaults(**merged)

    _validate_composite(resolved)
    return resolved


def _validate_composite(resolved: SizingDefaults) -> None:
    """Reject dangerous parameter combinations.

    Even if individual params are within rails, the combination can be dangerous.
    """
    max_vol_adj = resolved.target_vol / resolved.vol_floor
    max_kelly = (resolved.kelly_mult_override if resolved.kelly_mult_override > 0
                 else (resolved.kelly_mult_floor + resolved.kelly_mult_range) * resolved.kelly_mult_scale)
    worst_case_frac = max_kelly * max_vol_adj
    if worst_case_frac > 8.0:
        raise ValueError(
            f"Composite sizing check failed: worst-case position = {worst_case_frac:.1f}x equity "
            f"(kelly={max_kelly:.2f}, vol_adj={max_vol_adj:.1f}). "
            f"Reduce target_vol or kelly to keep worst-case below 8x."
        )


def _validate_dd_scaling(dd_scaling) -> tuple:
    """Validate and sort dd_scaling entries. Ensures ascending threshold order.

    Returns tuple of tuples for immutability.
    Raises ValueError for malformed entries (wrong length, out-of-range values).
    """
    if not dd_scaling:
        return ()
    for i, entry in enumerate(dd_scaling):
        if len(entry) != 2:
            raise ValueError(
                f"dd_scaling[{i}] must be a (threshold, fraction) pair, got {entry}"
            )
        threshold, fraction = entry
        if not (0 < threshold <= 1.0):
            raise ValueError(
                f"dd_scaling[{i}] threshold must be in (0, 1], got {threshold}"
            )
        if not (0 <= fraction <= 1.0):
            raise ValueError(
                f"dd_scaling[{i}] fraction must be in [0, 1], got {fraction}"
            )
    # Check for duplicate thresholds
    thresholds = [t for t, _ in dd_scaling]
    if len(thresholds) != len(set(thresholds)):
        raise ValueError(
            f"dd_scaling has duplicate thresholds: {sorted(thresholds)}"
        )
    # Sort by threshold ascending so last-match-wins gives the correct (deepest DD) multiplier
    # Return tuple of tuples for full immutability
    return tuple(tuple(entry) for entry in sorted(dd_scaling, key=lambda x: x[0]))


# ---------------------------------------------------------------------------
# StrategySpec
# ---------------------------------------------------------------------------

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
    # ADV-scaled position sizing
    adv_sizing_enabled: bool = False
    adv_sizing_base: float = 100_000_000
    adv_sizing_floor: float = 0.20
    # Sub-hourly exit resolution (0=hourly only, 1/5/15/30=sub-hourly WebSocket candles)
    exit_resolution: int = 0
    # Sub-hourly entry resolution (0=hourly, 1=1m cross detection via MinuteExitCache)
    entry_resolution: int = 0
    # Sizing parameter overrides (validated by resolve_sizing)
    sizing_overrides: dict = field(default_factory=dict)
    # Custom regime detection parameters (None = use engine defaults)
    regime_params: Optional[dict] = None
    # Sizing model selection ("kelly" = default, extensible via sizing registry)
    sizing_model: str = "kelly"
    # Slippage model selection ("sqrt" = default, extensible via slippage registry)
    slippage_model: str = "sqrt"
    # Max concurrent positions per token+strategy (1 = no re-entry, >1 = allow N concurrent)
    max_concurrent_per_token: int = 1
    # Drawdown scaling: tuple of (dd_threshold, size_fraction) pairs, sorted by threshold ascending.
    # Example: ((0.05, 0.75), (0.10, 0.50), (0.15, 0.25), (0.20, 0.0))
    # Empty tuple = disabled (default, no behavior change).
    dd_scaling: tuple = ()

    def __post_init__(self):
        if isinstance(self.max_concurrent_per_token, bool):
            raise ValueError(
                f"max_concurrent_per_token must be an integer, got bool"
            )
        self.max_concurrent_per_token = int(self.max_concurrent_per_token)
        if self.max_concurrent_per_token < 1:
            raise ValueError(
                f"max_concurrent_per_token must be >= 1, got {self.max_concurrent_per_token}"
            )
        # Always normalize dd_scaling to tuple of tuples (validates if non-empty)
        self.dd_scaling = _validate_dd_scaling(self.dd_scaling)

    @classmethod
    def from_dict(cls, d: dict) -> StrategySpec:
        """Create from a JSON-parsed dict.  Single source of truth for field mapping."""
        if "market" not in d:
            raise ValueError(
                f"Strategy '{d.get('strategy_id', '?')}' missing required 'market' field. "
                f"Must be 'spot', 'perp', or 'combined'."
            )
        return cls(
            strategy_id=d["strategy_id"],
            weight=d.get("weight", 1.0),
            max_positions=d.get("max_positions", 15),
            market=d["market"],
            strategy_type=d.get("strategy_type", "per_token"),
            circuit_breaker_r=d.get("circuit_breaker_r", 0.0),
            pump_filter_funding_zscore=d.get("pump_filter_funding_zscore", 0.0),
            pump_filter_range_threshold=d.get("pump_filter_range_threshold", 0.0),
            adv_sizing_enabled=d.get("adv_sizing_enabled", False),
            adv_sizing_base=d.get("adv_sizing_base", 100_000_000),
            adv_sizing_floor=d.get("adv_sizing_floor", 0.20),
            exit_resolution=d.get("exit_resolution", 0),
            entry_resolution=d.get("entry_resolution", 0),
            sizing_overrides=d.get("sizing_overrides", {}),
            regime_params=d.get("regime_params", None),
            sizing_model=d.get("sizing_model", "kelly"),
            slippage_model=d.get("slippage_model", "sqrt"),
            max_concurrent_per_token=d.get("max_concurrent_per_token", 1),
            dd_scaling=[tuple(x) for x in d.get("dd_scaling", [])],
        )


# ---------------------------------------------------------------------------
# PortfolioConfig
# ---------------------------------------------------------------------------

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
    purge_bars: int = 168               # 7 days purge
    stress_adv_multiplier: float = 1.0  # ADV multiplier for stop/margin_call exits (1.0 = no stress)
    max_slip_bps: float = 300           # max slippage cap in basis points
    # Conviction-based entry ordering: "shuffle" (random, default), "ranked" (by conviction),
    # "hybrid" (top-N by conviction tiers, shuffle within tiers)
    conviction_mode: str = "shuffle"
    min_conviction_threshold: float = 0.0  # skip entries below this conviction level (0 = no filter)
    max_sizing_equity: Optional[float] = None  # cap portfolio equity used for position sizing (None = uncapped)
    sizing_defaults: SizingDefaults = field(default_factory=SizingDefaults)
    # Raw mode: skip portfolio constraints, use strategy's own sizing pipeline
    raw_mode: bool = False
    # Skip walk-forward masking (orthogonal to raw_mode)
    skip_walk_forward: bool = False
    # Opt-in true walk-forward: per-window signal recomputation
    true_walk_forward: bool = False
    # Safety cap for concurrent positions in raw mode
    raw_max_positions: int = 500
    # Maximum rows to keep in hist_cache per token (0 = unlimited for backtest)
    cache_max_rows: int = 0

    def __post_init__(self):
        if self.true_walk_forward and self.skip_walk_forward:
            raise ValueError(
                "Cannot set both true_walk_forward=True and skip_walk_forward=True. "
                "true_walk_forward re-computes signals per OOS window; "
                "skip_walk_forward disables walk-forward entirely."
            )
