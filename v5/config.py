"""V5 Portfolio Backtest — Configuration dataclasses."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import TYPE_CHECKING, Literal, Optional, Tuple

if TYPE_CHECKING:
    from v5.bar_spec import BarSpec


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

    # --- Liquidity gating ---
    min_adv_usd: float = 500_000      # minimum ADV to trade
    adv_lookback_days: int = 30       # rolling ADV window

    # --- Simulator constraints (NOT strategy-overridable) ---
    funding_buffer_pct: float = 0.01    # reserve for funding costs
    vol_floor: float = 0.005            # min volatility (prevents division explosion)


# Parameters strategies can override via sizing_overrides in StrategySpec
SAFETY_RAILS: dict[str, tuple[float, float]] = {
    "edge_minimum":        (0.05, 0.50),
    "target_vol":          (0.005, 0.05),
    "spot_max_equity_pct": (0.10, 1.0),
    "min_adv_usd":         (100_000, 10_000_000),
    "adv_lookback_days":   (7, 90),
}

# Engine-absolute — strategies CANNOT override these (safety-critical params)
NON_OVERRIDABLE = {
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

    Raises ValueError for unknown keys, non-overridable keys, or out-of-bounds values.
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
            lo, hi = SAFETY_RAILS[key]
            if not (lo <= value <= hi):
                raise ValueError(
                    f"Sizing override '{key}' = {value} is out of bounds [{lo}, {hi}]"
                )

    merged = {**asdict(defaults), **overrides}
    # Enforce int fields (JSON parses as float)
    if "adv_lookback_days" in merged:
        merged["adv_lookback_days"] = int(merged["adv_lookback_days"])
    return SizingDefaults(**merged)


# ---------------------------------------------------------------------------
# StrategySpec
# ---------------------------------------------------------------------------

@dataclass
class StrategySpec:
    """Specification for one strategy in the portfolio."""
    strategy_id: str = ""         # e.g. "s30"; may be set via name= alias
    weight: float = 1.0           # fraction of portfolio equity
    max_positions: int = 15       # per-strategy position limit
    market: str = "combined"      # "spot", "perp", "combined"
    # M9 C-1: slot-reservation guarantee. Arbitration pre-fills up to N
    # slots for this strategy BEFORE cross-strategy contention runs.
    # Effective guarantee = min(min_slot_guarantee, max_positions,
    # deduped_candidate_count post-Phase 3.05). Unused reserved slots
    # are NOT released to cross-strategy pool (deterministic
    # over-allocation per Millennium-pod convention).
    min_slot_guarantee: int = 0
    # M9 compat alias — tests may pass `name=`; collapses to strategy_id.
    name: str = ""
    # Per-strategy risk controls (0 = disabled)
    circuit_breaker_r: float = 0.0               # emergency exit at Nx initial risk (e.g. 4.0)
    # ADV-scaled position sizing
    adv_sizing_base: float = 100_000_000
    adv_sizing_floor: float = 0.20
    # Bar resolution: 0=hourly only; 1/5/15/30=sub-hourly WebSocket candles for entries+exits
    bar_resolution: int = 0
    # Sizing parameter overrides (validated by resolve_sizing)
    sizing_overrides: dict = field(default_factory=dict)
    # Sizing model selection ("kelly" = default, extensible via sizing registry)
    sizing_model: str = "kelly"
    # Slippage model selection ("sqrt" = default, extensible via slippage registry)
    slippage_model: str = "sqrt"
    # Max concurrent positions per token+strategy (1 = no re-entry, >1 = allow N concurrent)
    max_positions_per_symbol: int = 1
    # Optional entry filter: strategy-defined function called before each entry.
    # Receives (token, direction, closed_trades_for_token) and returns conviction
    # multiplier (1.0=allow, 0.0=block, 0.5=demote). None=disabled (default).
    # Signature: Callable[[str, int, list[ClosedTrade]], float]
    entry_filter_fn: object = None
    # Optional custom exit check: strategy-defined function called each bar per open position.
    # Receives (position, bar_context) and returns ExitCheck or None.
    # None = no opinion (continue to next handler). ExitCheck(should_exit=True) = close.
    # Runs AFTER state-mutating handlers (breakeven, trail) but BEFORE built-in exit checks.
    # Signature: Callable[[Position, BarContext], Optional[ExitCheck]]
    exit_check_fn: object = None
    # Optional M2 scale-check: strategy-defined function called each bar per open position.
    # Receives (position, bar_context) and returns ScaleAction | list[ScaleAction] | None.
    # Runs in Phase 2 (between update_state and check_exit).
    # Signature: Callable[[Position, BarContext], Optional[ScaleAction | list[ScaleAction]]]
    scale_check_fn: object = None

    def __post_init__(self):
        # M9 compat: accept `name=` as alias for `strategy_id=`
        if self.name and not self.strategy_id:
            self.strategy_id = self.name
        elif self.strategy_id and not self.name:
            self.name = self.strategy_id
        if not self.strategy_id:
            raise ValueError("StrategySpec requires strategy_id (or name alias)")

        if isinstance(self.max_positions_per_symbol, bool):
            raise ValueError(
                f"max_positions_per_symbol must be an integer, got bool"
            )
        self.max_positions_per_symbol = int(self.max_positions_per_symbol)
        if self.max_positions_per_symbol < 1:
            raise ValueError(
                f"max_positions_per_symbol must be >= 1, got {self.max_positions_per_symbol}"
            )
        # AC20 / TG5: validate on construction when both fields are set at once.
        # Deferred validation (setattr after construction) handled by
        # ``validate_scaling_compat()``.
        self.validate_scaling_compat()

    def validate_scaling_compat(self) -> None:
        """AC20 / TG5: scale_check_fn requires max_positions_per_symbol == 1.

        Raises ``ValueError`` when both ``scale_check_fn`` is set and
        ``max_positions_per_symbol`` exceeds 1. Safe to call repeatedly.
        """
        if getattr(self, "scale_check_fn", None) is not None:
            if int(getattr(self, "max_positions_per_symbol", 1)) > 1:
                raise ValueError(
                    "max_positions_per_symbol must be 1 when scale_check_fn is set; "
                    f"got {self.max_positions_per_symbol}"
                )

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
            circuit_breaker_r=d.get("circuit_breaker_r", 0.0),
            adv_sizing_base=d.get("adv_sizing_base", 100_000_000),
            adv_sizing_floor=d.get("adv_sizing_floor", 0.20),
            bar_resolution=d.get("bar_resolution", 0),
            sizing_overrides=d.get("sizing_overrides", {}),
            sizing_model=d.get("sizing_model", "kelly"),
            slippage_model=d.get("slippage_model", "sqrt"),
            max_positions_per_symbol=d.get("max_positions_per_symbol", 1),
        )


# ---------------------------------------------------------------------------
# PortfolioConfig
# ---------------------------------------------------------------------------

class ConfigError(Exception):
    """M9 C-9: raised when a config field that cannot be safely hot-swapped
    is mutated (e.g., capital_allocation_policy with non-empty position
    book). Paper-mode reload footgun guard."""
    pass


def _default_arbitration_policy():
    """Lazy default factory for PortfolioConfig.arbitration_policy —
    avoids circular import at config.py module load time."""
    from v5.arbitration import RandomShuffle
    return RandomShuffle()


@dataclass
class PortfolioConfig:
    """Portfolio-level configuration for the backtest."""
    strategies: list[StrategySpec] = field(default_factory=list)
    capital: float = 200_000
    max_portfolio_positions: int = 40
    concentration_limit: float = 0.10    # 10% per-token across strategies
    adv_cap_pct: float = 0.05           # 5% of rolling ADV
    min_position_usd: float = 200.0
    exchange: str = "binance"
    base_spread_bps: float = 3.0
    impact_coeff: float = 0.03
    seed: int = 42
    stress_adv_multiplier: float = 1.0  # ADV multiplier for stop/margin_call exits (1.0 = no stress)
    max_slip_bps: float = 300           # max slippage cap in basis points
    max_sizing_equity: Optional[float] = None  # cap portfolio equity used for position sizing (None = uncapped)
    sizing_defaults: SizingDefaults = field(default_factory=SizingDefaults)
    # M8 rollback flag (design §5.2). Default False → legacy
    # sizing_model.compute_size path (pre-M8 behavior preserved). Set
    # True → Order.release_atomic clamp pipeline with 6-clamp sequence
    # + binding-log JSONL. Flag preserved until M9 post-paper validation.
    use_m8_clamps: bool = False
    # Maximum rows to keep in hist_cache per token (0 = unlimited for backtest)
    cache_max_rows: int = 0
    # Delayed/armed entry: signal fires on bar B, actual entry on bar B + entry_delay_bars.
    # Slot is reserved (counts toward max_positions) during the delay.
    # 0 = current behavior (enter immediately). Typical: 24-168 (1-7 days).
    entry_delay_bars: int = 0
    # Maximum bars a pending entry can wait before expiring (safety valve).
    max_pending_bars: int = 240  # 10 days default
    # --- M2 scale-dispatch: dust promotion + error strictness ---
    # AC10a: dust promotion threshold (notional)
    min_close_notional_usd: float = 1.0
    # AC10a: dust = max(min_close_notional_usd, dust_fraction_of_min_position * min_position_usd)
    dust_fraction_of_min_position: float = 0.05
    # Q2: backtest default re-raises scale_check_fn exceptions; paper may set False
    strict_scale_errors: bool = True
    # M3 (AC23): signal recomputation mode.
    # "full" = legacy pd.Series.ewm() recompute across the window every tick;
    # "incremental" = RollingCache-backed O(1)-per-tick dispatch (Task 7+).
    signal_mode: Literal["full", "incremental"] = "incremental"
    # M4 T18b (AC28 / quant architect Q2): optional override for the MTF sim-
    # loop base resolution. When ``None`` (default), the engine infers the
    # effective base resolution via the canonical normalization function
    # ``v5.bar_processor.resolve_base_resolution`` — finest declared
    # ``exit`` subscription across loaded strategies, falling back to the
    # interned ``BarSpec.from_minutes(60)``.
    #
    # When set, the override is validated against the coarsest strategy
    # ``exit`` subscription via the per-strategy exit BarSpec lookup.
    # Violation raises ``ValueError`` at resolution time — a coarser base
    # cannot drive a finer exit handler.
    base_resolution: Optional["BarSpec"] = None
    # M9 C-1: pluggable SignalArbitrationPolicy. Default = RandomShuffle
    # (fairness-first, prevents starvation on uncalibrated priorities).
    arbitration_policy: object = field(default_factory=_default_arbitration_policy)
    # M9 C-5: list of RiskComponent instances run at Phase 3.0 pre-arbitration.
    # Empty list = no risk checks (default). Strategies opt in via config.
    risk_components: list = field(default_factory=list)
    # M9 C-9: pluggable CapitalAllocationPolicy (M8 shipped SharedPoolPolicy).
    # Free-capital clamp reads this instead of hardcoded SharedPoolPolicy.
    capital_allocation_policy: object = None  # post-init sets default to avoid circular import

    def __post_init__(self):
        if self.signal_mode not in ("full", "incremental"):
            raise ValueError(
                f"signal_mode must be 'full' or 'incremental', got {self.signal_mode!r}"
            )
        # M9 AC #3(g): sum(min_slot_guarantee) must not exceed max_portfolio_positions.
        # Prevents deterministic over-reservation that would starve all strategies.
        total_guarantees = sum(
            int(getattr(s, "min_slot_guarantee", 0) or 0)
            for s in (self.strategies or [])
        )
        if total_guarantees > self.max_portfolio_positions:
            raise ValueError(
                f"sum(min_slot_guarantee)={total_guarantees} exceeds "
                f"max_portfolio_positions={self.max_portfolio_positions}; "
                f"reduce guarantees or raise max_portfolio_positions"
            )
        # M9 C-9: lazy default for capital_allocation_policy (avoid circular
        # import at config.py module load time).
        if self.capital_allocation_policy is None:
            from v5.sizing.allocation import SharedPoolPolicy
            object.__setattr__(
                self, "capital_allocation_policy", SharedPoolPolicy()
            )
        # Position-book attach point (None until engine attaches). Used by
        # hot-swap protection in __setattr__ to block policy changes mid-run.
        object.__setattr__(self, "_position_book", None)

    def _attach_position_book(self, book) -> None:
        """M9 C-9 test hook — engine (or test) attaches a position-book
        reference so `capital_allocation_policy` hot-swap can check
        whether open positions exist."""
        object.__setattr__(self, "_position_book", book)

    def __setattr__(self, name, value):
        """M9 C-9 hot-swap protection: policy changes with a non-empty
        position book raise ConfigError. Paper-mode reload footgun guard.
        Other fields pass through normally."""
        if name == "capital_allocation_policy":
            book = getattr(self, "_position_book", None)
            if book is not None:
                try:
                    empty = len(book) == 0 or bool(book.is_empty())
                except Exception:
                    empty = False
                if not empty:
                    raise ConfigError(
                        "Cannot hot-swap capital_allocation_policy with "
                        "non-empty position book (paper-mode reload footgun). "
                        "Stop and restart the runner with the new policy."
                    )
        object.__setattr__(self, name, value)
