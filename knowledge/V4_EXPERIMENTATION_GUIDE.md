# V4 Engine Experimentation Guide

> **Audience:** Strategy researchers using `/strategy` mode.
> **Purpose:** Comprehensive reference for experimenting with exits, sizing, regimes,
> and other extension points. Goes beyond the quick reference to explain *how the engine
> works under the hood* so you can design experiments that the engine actually supports.
>
> **Last updated:** 2026-03-26

---

## Table of Contents

1. [Architecture: Strategy ↔ Engine Separation](#1-architecture-strategy--engine-separation)
2. [The Data Pipeline: StrategyResult → TokenSignals → Simulator](#2-the-data-pipeline)
3. [Exit Handler System](#3-exit-handler-system)
4. [Sizing Model Registry](#4-sizing-model-registry)
5. [Regime Detection & Customization](#5-regime-detection--customization)
6. [Raw Mode vs Normal Mode](#6-raw-mode-vs-normal-mode)
7. [Indicator Pipeline & Custom Data](#7-indicator-pipeline--custom-data)
8. [Extension Points for Researchers](#8-extension-points-for-researchers)
9. [Experimentation Recipes](#9-experimentation-recipes)

---

## 1. Architecture: Strategy ↔ Engine Separation

The V4 engine enforces a strict boundary between **strategy logic** (what to trade)
and **execution logic** (how to trade it). Strategies are pure functions that return
arrays; the engine handles sizing, constraints, fills, and exits.

```
Strategy file (strategies/sNN_*.py)
  │
  │  strategy(ctx) → StrategyResult
  │  (pure function: arrays in, arrays out)
  │
  ▼
Signal Pipeline (v4/signals.py)
  │
  │  StrategyResult → TokenSignals
  │  (applies WF mask, extracts arrays, normalizes types)
  │
  ▼
Simulator (v4/simulator.py)
  │
  │  TokenSignals → SimulationState
  │  (sizing, constraints, fills, exits, PnL tracking)
  │
  ▼
Report (v4/report.py)
  │
  │  SimulationState → metrics (Sharpe, Calmar, etc.)
```

**What strategies control:** Entry signals (`entry_mask`, `direction`), trade
parameters (stop, trail, target, max hold, edge), sizing hints (`size_multiplier`,
`cap_multiplier`), exit behavior (which exits are active, their thresholds).

**What strategies cannot control:** Fee calculation, portfolio constraints
(concentration, ADV cap, capital allocation), walk-forward masking, entry ordering.

**What strategies CAN now configure (via StrategySpec):**
- **Sizing model:** `sizing_model` field (default `"kelly"`) — selects from the sizing model registry
- **Slippage model:** `slippage_model` field (default `"sqrt"`) — selects from the slippage model registry
- **Sizing curve shape:** `kelly_mult_floor`, `kelly_mult_range`, `cap_pct_floor`, `cap_pct_range`, `adv_scaling_divisor` are now overridable via `sizing_overrides` (bounded by SAFETY_RAILS)
- **Concurrent positions:** `max_concurrent_per_token` (default 1) — allows multiple open positions per token+strategy. Set via `MAX_CONCURRENT_PER_TOKEN` module constant in strategy file.
- **Drawdown control:** `dd_scaling` — list of `(dd_threshold, size_fraction)` tuples that reduce position sizing during drawdowns. Set via `DD_SCALING` module constant in strategy file. Uses portfolio-level MTM watermark. Applies in both raw and normal modes.

**Key implication:** To experiment with different sizing or exit behavior, you modify
*StrategyResult fields* or *StrategySpec config* — never the simulator itself.

---

## 2. The Data Pipeline

### StrategyResult (what your strategy returns)

Every strategy function returns a `StrategyResult` dataclass (`v4/engine.py:404`).
Here is the complete field inventory grouped by purpose:

**Entry signals:**
| Field | Type | Default | Purpose |
|-------|------|---------|---------|
| `entry_mask` | `np.ndarray[bool]` | required | Per-bar entry signals |
| `direction` | `np.ndarray[int8]` | required | 1=long, -1=short |
| `conviction_score` | `Optional[np.ndarray]` | None | Per-bar [0,1] strength for entry ordering |

**Trade management — stops and trails:**
| Field | Type | Default | Purpose |
|-------|------|---------|---------|
| `stop_mult` | `float` or `ndarray` | 3.0 | Initial stop distance (ATR multiplier) |
| `trail_mult` | `float` or `ndarray` | 1.5 | Trailing stop distance (ATR multiplier) |
| `target_mult` | `float` | 999.0 | Take-profit distance (ATR mult). 999=disabled |
| `trail_schedule` | `Optional[ndarray]` | None | Progressive trail: `[[profit_atr, trail_mult], ...]` |
| `time_trail_schedule` | `Optional[ndarray]` | None | Time-decay trail: `[[bars_held, trail_mult], ...]` |
| `max_trail_mult` | `Optional[ndarray]` | None | Per-bar ceiling on effective trail multiplier |
| `breakeven_atr` | `float` | 0.0 | Move stop to entry after +N ATR profit. 0=disabled |
| `chandelier_lookback` | `int` | 0 | Trail from N-bar high/low instead of all-time. 0=disabled |
| `no_stop_bars` | `int` | 0 | Bars of grace before stop activates |

**Trade management — targets and holds:**
| Field | Type | Default | Purpose |
|-------|------|---------|---------|
| `min_hold` | `int` | 6 | Minimum hold period (hours) |
| `max_hold` | `int` | 720 | Maximum hold period (hours, 30 days) |
| `bear_target_mult` | `float` | 0.0 | Tighter TP in DOWNTREND. 0=use target_mult |
| `bear_max_hold` | `int` | 0 | Shorter hold in DOWNTREND. 0=use max_hold |

**Exit behavior:**
| Field | Type | Default | Purpose |
|-------|------|---------|---------|
| `exit_regimes` | `set[int]` | `{0}` (CRISIS) | Force exit in these regimes |
| `rsi_exit_level` | `float` | 999.0 | RSI threshold for exit. 999=disabled |
| `convex_exit` | `bool` | False | Use convex exit mode (different trail logic) |
| `mean_target_vals` | `Optional[ndarray]` | None | Convex mean-target exit values |
| `funding_exit_threshold` | `float` | 0.0 | Exit when cumulative funding/margin > threshold |
| `regime_exit_min_bars` | `int` | 6 | Min bars before regime exit triggers |

**Partial profit-taking:**
| Field | Type | Default | Purpose |
|-------|------|---------|---------|
| `partial_tp_atr` | `float` | 0.0 | Close partial at this ATR profit. 0=disabled |
| `partial_tp_pct` | `float` | 0.5 | Fraction to close (0.5 = close half) |
| `partial_tp_trail` | `float` | 1.5 | Tighter trail for remainder after partial |

**Sizing hints:**
| Field | Type | Default | Purpose |
|-------|------|---------|---------|
| `edge` | `float` | 0.35 | Kelly numerator + entry gate |
| `size_multiplier` | `float` or `ndarray` | 1.0 | Scales Kelly fraction (conviction) |
| `cap_multiplier` | `float` or `ndarray` | 1.0 | Scales capital cap (for high-ADV tokens) |
| `max_trade_pct` | `float` | 0.0 | Hard cap: position ≤ equity × this. 0=disabled |
| `leverage` | `float` or `ndarray` | 1.0 | Notional = margin × leverage |

**Market/venue:**
| Field | Type | Default | Purpose |
|-------|------|---------|---------|
| `market_type` | `int` | 0 (SPOT) | MarketType: SPOT=0, PERP=1, COMBINED=2 |
| `exchange` | `str` | "binance" | Fee/funding lookup key |

**Combined strategy (secondary leg):**
| Field | Type | Default | Purpose |
|-------|------|---------|---------|
| `secondary_entry_mask` | `Optional[ndarray]` | None | When to enter secondary leg |
| `secondary_direction` | `Optional[ndarray]` | None | Direction for secondary leg |
| `secondary_market_type` | `int` | 1 (PERP) | Venue for secondary leg |
| `secondary_leverage` | `float` | 1.0 | Leverage for secondary leg |
| `capital_split` | `float` | 0.5 | Fraction of capital for primary leg |
| `secondary_stop_mult` | `Optional[float]` | None | Stop for secondary (None=use primary) |
| `secondary_trail_mult` | `Optional[float]` | None | Trail for secondary |
| `secondary_target_mult` | `Optional[float]` | None | Target for secondary |
| `secondary_no_stop_bars` | `Optional[int]` | None | Grace bars for secondary |
| `secondary_min_hold` | `Optional[int]` | None | Min hold for secondary |
| `secondary_max_hold` | `Optional[int]` | None | Max hold for secondary |

### TokenSignals (engine's internal representation)

`TokenSignals` (`v4/signals.py:33`) is what the engine actually works with. The signal
pipeline converts `StrategyResult` → `TokenSignals`, normalizing types along the way:

- Scalar `float` fields become `np.ndarray` (via `_to_array()`)
- `cap_multiplier` **must** end up as `ndarray` — the simulator indexes it per-bar
- Walk-forward mask is applied to `entry_mask`
- Signal diagnostic counters are populated (raw/post-liquidity/post-WF entry counts)

**You never construct TokenSignals directly** — the pipeline handles it. But
understanding the conversion helps debug unexpected behavior.

---

## 3. Exit Handler System

The V4 engine uses a **pluggable exit handler chain** (`v4/exit_handlers.py`). Exit
logic is fully decoupled from the simulator — new exit types never require simulator
changes.

### Handler lifecycle

```
Position opened → build_exit_chain(pos, sig, spec) → handlers stored on Position
                                                           │
Each bar:                                                  │
  1. update_state() on ALL handlers (trailing, breakeven)  │
  2. process_partial() on PartialTPHandler (side-effect)   │
  3. check_exit() on each handler until first match wins   │
                                                           ▼
                                                      ExitCheck(should_exit, reason)
```

### Available exit handlers (priority order)

| # | Handler | Phase | What it does |
|---|---------|-------|-------------|
| 0 | `BreakevenRatchetHandler` | state | Moves stop to entry price after +N ATR profit |
| 1 | `ConvexTrailingHandler` | state | Convex-exit trailing stop (mature/early phases) |
| 1 | `TrailingStopHandler` | state | Standard trailing: profit schedule, time schedule, chandelier, ceiling |
| 2 | `PartialTPHandler` | side-effect | Close a fraction at profit threshold, tighten trail on remainder |
| 3 | `CircuitBreakerHandler` | check | Emergency exit at Nx initial risk (bypasses no_stop_bars) |
| 4 | `StopLossHandler` | check | Stop-loss on intra-bar high/low |
| 5 | `TakeProfitHandler` | check | Take-profit, regime-conditional (tighter in DOWNTREND) |
| 6 | `RegimeExitHandler` | check | Force exit when regime enters `exit_regimes` set |
| 7 | `RSIExitHandler` | check | RSI-based exit (longs exit high RSI, shorts exit low) |
| 8 | `MeanTargetHandler` | check | Mean-target exit for convex strategies |
| 9 | `MaxHoldHandler` | check | Maximum hold time, regime-conditional (shorter in DOWNTREND) |
| 10 | `FundingCeilingHandler` | check | Exit when cumulative funding drag exceeds threshold |

### How to configure exits from your strategy

All exit behavior is controlled through `StrategyResult` fields. Examples:

```python
# Basic: 3 ATR stop, 1.5 ATR trail, exit on CRISIS, 30-day max hold
return StrategyResult(
    entry_mask=mask, direction=direction,
    stop_mult=3.0, trail_mult=1.5, target_mult=999,
    exit_regimes={0},  # CRISIS only
    max_hold=720,
)

# Aggressive: breakeven ratchet + partial TP + bear-conditional hold
return StrategyResult(
    entry_mask=mask, direction=direction,
    stop_mult=3.0, trail_mult=1.5,
    breakeven_atr=0.5,           # move stop to entry after +0.5 ATR
    partial_tp_atr=2.0,          # close 50% at +2 ATR
    partial_tp_pct=0.5,          # fraction to close
    partial_tp_trail=1.0,        # tighter trail on remainder
    bear_max_hold=12,            # 12h hold in DOWNTREND
    max_hold=504,                # 21 days normally
)

# Per-bar dynamic trail (tightens with time)
schedule = np.array([[24, 1.2], [48, 0.8], [96, 0.5]])
return StrategyResult(
    entry_mask=mask, direction=direction,
    time_trail_schedule=schedule,  # trail tightens as position ages
)

# RSI exit + regime exit
return StrategyResult(
    entry_mask=mask, direction=direction,
    rsi_exit_level=75.0,         # longs exit when RSI > 75
    exit_regimes={0, 4},         # exit on CRISIS or DOWNTREND
)
```

### Exit interactions to understand

1. **Trail schedule vs time schedule:** Both can run simultaneously. The effective
   trail multiplier is `min(profit_schedule_value, time_schedule_value)`. Use both
   when you want "tighten as price moves in our favor AND as time passes."

2. **Breakeven + trail:** Breakeven ratchet fires once (moves stop to entry).
   Trail continues tightening above entry. They compose naturally.

3. **Partial TP + trail:** After partial close, the remaining position gets a tighter
   trail (`partial_tp_trail`). The position is flagged `partial_closed=True` so it
   only fires once.

4. **Circuit breaker vs stop:** Circuit breaker ignores `no_stop_bars` — it always
   fires. Stop-loss respects the grace period. Set `circuit_breaker_r` on
   `StrategySpec` (not `StrategyResult`) for emergency protection.

5. **RegimeExit + exit_regimes:** Add regime IDs to `exit_regimes`. CRISIS (0) is
   standard. Adding DOWNTREND (4) is aggressive. The handler checks
   `bars_held > regime_exit_min_bars` to prevent whipsawing on regime changes.

6. **Convex vs standard exit:** `convex_exit=True` switches to a two-phase trailing
   system (early/mature) with risk-based targets instead of ATR-based. Mutually
   exclusive with standard `TrailingStopHandler` — set one or the other.

---

## 4. Sizing Model Registry

### How it works

Position sizing is pluggable via the `SizingModel` protocol (`v4/sizing.py`). The
simulator resolves each strategy's sizing model once and caches it:

```
StrategySpec.sizing_model = "kelly"
         │
         ▼
get_sizing_model("kelly") → _SIZING_MODELS["kelly"] → KellySizing instance
         │
         ▼
sizing_model.compute_size(strategy_equity, rolling_adv, volatility, ...) → pos_usd
```

The registry (`_SIZING_MODELS`) maps string names to `SizingModel` instances.
Currently only `"kelly"` is registered. The infrastructure is ready for alternatives.

### The SizingModel protocol

```python
@runtime_checkable
class SizingModel(Protocol):
    def compute_size(
        self,
        strategy_equity: float,
        rolling_adv: float,
        volatility: float,
        edge: float,
        size_multiplier: float,
        cap_multiplier: float,
        max_trade_pct: float,
        adv_cap_pct: float,
        adv_sizing_enabled: bool,
        adv_sizing_base: float,
        adv_sizing_floor: float,
        edge_minimum: float,
        target_vol: float,
        vol_floor: float,
        spot_max_equity_pct: float,
        leverage: float,
        kelly_mult_override: float,
        kelly_mult_scale: float,
        cap_pct_override: float,
        cap_pct_scale: float,
        kelly_mult_floor: float,
        kelly_mult_range: float,
        cap_pct_floor: float,
        cap_pct_range: float,
        adv_scaling_divisor: float,
    ) -> float: ...
```

### How to create a custom sizing model

1. **Implement the protocol:**

```python
# In your research script or a new file
class FixedFractionSizing:
    """Simple fixed-fraction sizing: always risk X% of equity."""
    def __init__(self, fraction: float = 0.02):
        self.fraction = fraction

    def compute_size(self, strategy_equity: float, rolling_adv: float,
                     volatility: float, edge: float, **kwargs) -> float:
        if edge < kwargs.get('edge_minimum', 0.10):
            return 0.0
        pos = strategy_equity * self.fraction
        adv_cap = rolling_adv * kwargs.get('adv_cap_pct', 0.05)
        return min(pos, adv_cap)
```

2. **Register it:**

```python
from v4.sizing import _SIZING_MODELS
_SIZING_MODELS["fixed_fraction"] = FixedFractionSizing(fraction=0.02)
```

3. **Select it in your strategy's config:**

```json
{
    "strategy_id": "s99",
    "sizing_model": "fixed_fraction"
}
```

Or in Python:
```python
StrategySpec(strategy_id="s99", sizing_model="fixed_fraction")
```

4. **The simulator automatically uses it.** No other changes needed.

### Sizing configuration layers

Three layers of configuration, in order of precedence:

| Layer | Where | What it controls |
|-------|-------|-----------------|
| 1. Engine defaults | `SizingDefaults` in `v4/config.py` | Kelly curve shape, vol floor, funding buffer |
| 2. Portfolio config | `PortfolioConfig` in configs JSON | Capital, ADV cap, concentration, slippage |
| 3. Strategy-level | `StrategyResult` fields + `sizing_overrides` | Per-bar multipliers, edge, Kelly overrides |

The strategy layer has two sub-channels:
- **Signal layer (per-bar):** `size_multiplier`, `cap_multiplier`, `leverage`, `max_trade_pct`, `edge` — these come from `StrategyResult` and vary per bar
- **Config layer (static):** `sizing_overrides` dict on `StrategySpec` — these override `SizingDefaults` parameters (e.g., `kelly_mult_override`, `spot_max_equity_pct`)

> Deep dive on the Kelly formula, ADV curve, and safety rails: `knowledge/V4_SIZING_PIPELINE.md`

---

## 4.5. Slippage Model Registry

### How it works

Slippage calculation is pluggable via the `SlippageModel` protocol (`v4/sizing.py`).
The simulator resolves each strategy's slippage model once and stores it on
`SimulationState._slippage_models`:

```
StrategySpec.slippage_model = "sqrt"
         │
         ▼
get_slippage_model("sqrt") → _SLIPPAGE_MODELS["sqrt"] → SqrtImpactSlippage instance
         │
         ▼
slippage_model.compute_slippage(pos_usd, adv, base_spread_bps, ...) → slip_bps
```

The registry (`_SLIPPAGE_MODELS`) maps string names to `SlippageModel` instances.
Currently only `"sqrt"` is registered (the existing square-root impact model).

### The SlippageModel protocol

```python
@runtime_checkable
class SlippageModel(Protocol):
    def compute_slippage(
        self,
        pos_usd: float,
        adv: float,
        base_spread_bps: float,
        impact_coeff: float,
        max_slip_bps: float,
    ) -> float: ...
```

### Default: SqrtImpactSlippage

```python
class SqrtImpactSlippage:
    def compute_slippage(self, pos_usd, adv, base_spread_bps=3.0,
                         impact_coeff=0.03, max_slip_bps=300.0) -> float:
        participation = pos_usd / max(adv / 24.0, 1.0)
        slip_bps = base_spread_bps + impact_coeff * sqrt(participation) * 10000
        return min(slip_bps, max_slip_bps)
```

### How to create a custom slippage model

1. **Implement the protocol:**

```python
class LinearSlippage:
    """Linear impact model for research comparison."""
    def compute_slippage(self, pos_usd, adv, base_spread_bps=3.0,
                         impact_coeff=0.03, max_slip_bps=300.0) -> float:
        participation = pos_usd / max(adv / 24.0, 1.0)
        slip_bps = base_spread_bps + impact_coeff * participation * 10000
        return min(slip_bps, max_slip_bps)
```

2. **Register it:**

```python
from v4.sizing import _SLIPPAGE_MODELS
_SLIPPAGE_MODELS["linear"] = LinearSlippage()
```

3. **Select it in your strategy's config:**

```json
{
    "strategy_id": "s99",
    "slippage_model": "linear"
}
```

4. **The simulator uses it everywhere** — entries, exits, partial closes, margin calls,
   and data-end closures all go through the same slippage model for each strategy.

### Key implementation detail

Slippage models are stored on `SimulationState._slippage_models` (not in a local cache)
because slippage is needed in both entry and exit code paths. The `state` object is
available at all 6 call sites in the simulator.

---

## 4.6. Expanded BarContext for Custom Exit Handlers

Exit handlers receive a `BarContext` (`v4/exit_handlers.py`) with per-bar data.
Three new fields were added for custom exit handler development:

| Field | Type | Default | Source |
|-------|------|---------|--------|
| `close` | `float` | required | `ctx.ind_1h['close']` |
| `high` | `float` | required | `ctx.ind_1h['high']` |
| `low` | `float` | required | `ctx.ind_1h['low']` |
| `atr` | `float` | required | `ctx.ind_1h['atr']` |
| `rsi` | `float` | NaN | `ctx.ind_1h['rsi']` |
| `regime` | `int` | 0 | Regime at this bar |
| `volume` | `float` | **NaN** | `ctx.ind_1h['volume']` **(NEW)** |
| `vol_20` | `float` | **NaN** | `ctx.ind_1h['vol_20']` — 20-period rolling volatility **(NEW)** |
| `ret_1h` | `float` | **NaN** | `ctx.ind_1h['ret_1']` — 1-hour log return **(NEW)** |

These fields are populated from `TokenSignals` arrays in both `signals.py` (per-token)
and `portfolio_signals.py` (portfolio mode). NaN defaults ensure backward compatibility
— existing handlers that don't use these fields are unaffected.

**Use case:** Custom exit handlers can now implement volume-aware exits (exit on volume
spike), volatility-scaled stops (tighten when vol_20 drops), or momentum-based exits
(exit when return flips sign).

---

## 5. Regime Detection & Customization

### Default regime detection

The engine detects 5 regimes from daily indicators (`v4/engine.py:289`):

| ID | Name | Detection logic |
|----|------|----------------|
| 0 | CRISIS | `vol_20 > expanding_p75(vol_20) × crisis_mult` |
| 1 | QUIET | `vol_20 < expanding_p25(vol_20) × quiet_mult` (not crisis) |
| 2 | UPTREND | `ADX > adx_threshold` AND `EMA_fast > EMA_slow` (not crisis/quiet) |
| 3 | RANGE | Default — none of the above |
| 4 | DOWNTREND | `ADX > adx_threshold` AND `EMA_fast <= EMA_slow` (not crisis/quiet) |

Key properties:
- Uses **expanding (causal) percentiles** — no look-ahead bias
- Computed on **daily** data, then aligned to 1h bars
- Shifted by 1 day (`np.roll(regimes, 1)`) to avoid using today's regime for today's signals
- First 20 daily bars default to RANGE (insufficient data)
- Available in strategy context as `ctx.regime_1h`

### Per-strategy regime customization

Each `StrategySpec` can override regime detection via `regime_params`:

```json
{
    "strategy_id": "s99",
    "regime_params": {
        "adx_threshold": 30,
        "crisis_mult": 2.5,
        "quiet_mult": 0.5,
        "ema_pair": [10, 30],
        "min_periods": 90
    }
}
```

These create a `RegimeConfig` (`v4/config.py:71`) that is passed to `detect_daily_regime()`
during signal precomputation. The regime arrays in `TokenSignals` are then strategy-specific.

**Experiment ideas:**
- Different ADX thresholds (higher = only strong trends, fewer UPTREND/DOWNTREND bars)
- Different EMA pairs (shorter = more responsive to trend changes, more whipsaws)
- Higher crisis_mult (fewer crisis detections, fewer forced exits)
- Higher min_periods (more stable early-data regimes, less noise)

### How strategies use regimes

Regimes affect strategy behavior at multiple levels:

1. **Entry filtering:** `ctx.regime_1h != 0` — skip entries in CRISIS
2. **Direction selection:** Enter long only in UPTREND, short only in DOWNTREND
3. **Exit forcing:** `exit_regimes={0}` forces exit when regime becomes CRISIS
4. **Target adjustment:** `bear_target_mult > 0` tightens TP in DOWNTREND
5. **Hold adjustment:** `bear_max_hold > 0` shortens max hold in DOWNTREND
6. **Sizing overlay:** `size_multiplier` can vary by regime (e.g., reduce size in CRISIS)

---

## 6. Raw Mode vs Normal Mode

The simulator has two modes controlled by `PortfolioConfig.raw_mode`:

### Normal mode (default, `raw_mode=False`)

Full portfolio simulation with realistic constraints:

```
Entry candidate → sizing → concentration check → ADV cap → free capital check
                         → position limit → min size check → slippage → fill
```

- Shared capital pool across all strategies
- Concentration limit (default 10% per token)
- ADV cap (default 5% of rolling ADV)
- Position limits (per-strategy and portfolio-wide)
- Strategy weights control equity allocation
- Conviction-based entry ordering (shuffle/ranked/hybrid)
- Walk-forward masking applied

### Raw mode (`raw_mode=True`)

Simplified simulation that skips portfolio-level constraints:

```
Entry candidate → sizing (fixed equity) → slippage → fill
```

- **Skips:** Concentration limits, position limits, free capital checks
- **Uses:** Fixed sizing equity (initial capital, no dynamic equity updates)
- **Skips:** Combined strategy processing entirely
- **Purpose:** Diagnostic testing, signal quality assessment, isolated strategy analysis
- **Safety cap:** `raw_max_positions=500` prevents runaway position accumulation

**When to use raw mode:**
- Testing a new signal's raw behavior without portfolio interference
- Comparing signal quality across strategies on equal footing
- Debugging: "Is my signal bad, or is the portfolio rejecting entries?"
- Quick iteration during Gate 3 prototyping

**When NOT to use raw mode:**
- Any result you plan to report or compare against production
- Portfolio complement tests (Gate V4-5)
- Combined spot+perp strategies (not supported in raw mode)
- Capacity assessment (raw mode has no capital constraints)

```python
config = PortfolioConfig(
    raw_mode=True,         # enable raw mode
    skip_walk_forward=True,  # optionally also skip WF mask
    raw_max_positions=500,   # safety cap
)
```

### Walk-forward masking (orthogonal to raw mode)

`skip_walk_forward=True` skips the train/purge masking of `entry_mask`. This is
separate from raw mode — you can use either independently:

| Combination | Use case |
|-------------|----------|
| `raw_mode=False, skip_walk_forward=False` | Production backtest |
| `raw_mode=True, skip_walk_forward=False` | Signal quality with WF discipline |
| `raw_mode=True, skip_walk_forward=True` | Full diagnostic (all bars tradeable) |
| `raw_mode=False, skip_walk_forward=True` | Portfolio sim without WF (overfitting risk) |

---

## 7. Indicator Pipeline & Custom Data

### How indicators reach your strategy

The engine computes indicators at three timeframes (1h, 4h, daily) and makes them
available via `ctx.ind_1h`, `ctx.ind_4h`, `ctx.ind_d` dicts. The computation has
three tiers:

```
Raw parquet data (OHLCV + funding)
  │
  ▼
Tier 1: Core indicators (always computed)
  │  ATR, returns, volatility, taker ratio, OHLCV passthrough
  │
  ▼
Tier 2: Indicator groups (all on by default, selectable)
  │  EMA, MACD, RSI, Bollinger, ADX, Volume ratio, Donchian
  │
  ▼
Tier 3: Indicator plugins (@register_indicator)
  │  OBV, VWAP, momentum returns, funding z-score, squeeze,
  │  multi-TF alignment, positioning overlay, VRP overlay, etc.
  │  Writes to ctx.custom dict
  │
  ▼
StrategyContext — passed to strategy(ctx)
```

### Tier 1: Core indicators (always available)

`compute_core()` in `v4/engine.py:133`. These are on every timeframe:

| Key | Indicator | Formula |
|-----|-----------|---------|
| `close`, `high`, `low`, `volume` | Raw OHLCV | Passthrough from parquet |
| `atr` | ATR (14-period) | EMA of true range |
| `ret_1` | Log return | `log(close / prev_close)` |
| `vol_20` | 20-bar volatility | Rolling std of log returns |
| `taker` | Taker buy ratio | `taker_buy_base / volume` (0.5 if unavailable) |

### Tier 2: Indicator groups (registered in `_INDICATOR_GROUPS`)

All computed by default. Strategies can opt into specific groups only via
`compute_indicators_selective(groups={'rsi', 'bb'})`.

| Group | Keys | What |
|-------|------|------|
| `ema` | `ema_10`, `ema_20`, `ema_50` | Exponential moving averages |
| `macd` | `macd`, `macd_signal`, `macd_hist` | MACD line, signal, histogram |
| `rsi` | `rsi` | 14-period RSI |
| `bb` | `bb_upper`, `bb_lower`, `bb_width`, `bb_pct` | Bollinger Bands (20, 2σ) |
| `adx` | `adx`, `plus_di`, `minus_di` | ADX + directional indicators |
| `volume` | `vol_ratio` | Volume vs 20-bar SMA |
| `donchian` | `donch_high`, `donch_low` | 20-bar Donchian channels |

**Usage in strategies:**
```python
rsi = ctx.ind_1h['rsi']            # 1h RSI
adx_daily = ctx.ind_d['adx']       # daily ADX
ema_4h = ctx.ind_4h['ema_20']      # 4h EMA-20
```

### Tier 3: Indicator plugins (`ctx.custom`)

Plugins use `@register_indicator` to run after core + group indicators.
They write to `ctx.custom` — an open dict any strategy can read.

**Currently registered plugins:**

| Plugin | `ctx.custom` keys | Source |
|--------|-------------------|--------|
| OBV | `obv`, `obv_slope` | Cumulative signed volume |
| VWAP | `vwap_20`, `vwap_dev` | 20-bar VWAP + deviation ratio |
| Momentum returns | `ret_6h`, `ret_12h`, `ret_24h`, `ret_48h`, `ret_120h`, `ret_1d`, `ret_3d`, `ret_7d` | Multi-period returns (1h + daily) |
| OBV divergence | `obv_divergence`, `price_slope` | OBV slope vs price slope |
| Momentum accel | `momentum_accel` | 2nd derivative of 6h returns |
| Funding z-score | `funding_zscore` | Rolling 168h funding z-score |
| Squeeze intensity | `squeeze_intensity` | `1 - (bb_width / bb_avg_240)` |
| Multi-TF alignment | `multi_tf_alignment` | 0-3 score: 1h MACD + 4h EMA + daily EMA agreement |
| Positioning overlay | `pos_z`, `pos_mult` | Top Trader L/S z-score → sizing multiplier |
| VRP overlay | `vrp_z`, `vrp_mult` | Volatility risk premium → sizing multiplier |
| Enriched signals | `enr_vpin`, `enr_realized_vol`, etc. | From `all_tokens_enriched.parquet` (if exists) |

### How to add a new indicator

**Option A: Compute inline in your strategy (simplest, no engine changes)**

```python
def strategy(ctx):
    close = ctx.ind_1h['close']
    high = ctx.ind_1h['high']
    low = ctx.ind_1h['low']

    # Custom indicator: Williams %R (14-period)
    highest = pd.Series(high).rolling(14).max().values
    lowest = pd.Series(low).rolling(14).min().values
    williams_r = -100 * (highest - close) / np.maximum(highest - lowest, 1e-10)

    entry_mask = williams_r < -80  # oversold
    ...
```

Best for: one-off experiments, strategy-specific signals, quick prototyping.

**Option B: Register a plugin (shared across strategies)**

```python
# Add to v4/engine.py or import at startup
@register_indicator(name='williams_r')
def _compute_williams_r(ctx: StrategyContext):
    """Williams %R — available in ctx.custom['williams_r']."""
    close = ctx.ind_1h['close']
    high = ctx.ind_1h['high']
    low = ctx.ind_1h['low']
    highest = pd.Series(high).rolling(14).max().values
    lowest = pd.Series(low).rolling(14).min().values
    ctx.custom['williams_r'] = -100 * (highest - close) / np.maximum(highest - lowest, 1e-10)
```

Then in any strategy: `wr = ctx.custom.get('williams_r')`.

Named plugins (`name='williams_r'`) support opt-in via `REQUIRED_PLUGINS`:
```python
# In your strategy file — only runs named plugins you need
REQUIRED_PLUGINS = ['williams_r', 'obv']
```

Unnamed plugins (`@register_indicator` without `name=`) always run for all strategies.

**Option C: Add a new indicator group (for standard TA indicators)**

```python
# Add to _INDICATOR_GROUPS dict in v4/engine.py
def compute_stochastic(close, high, low, volume, tr):
    """Stochastic oscillator (14, 3, 3)."""
    highest = pd.Series(high).rolling(14).max().values
    lowest = pd.Series(low).rolling(14).min().values
    k = 100 * (close - lowest) / np.maximum(highest - lowest, 1e-10)
    d = pd.Series(k).rolling(3).mean().values
    return {'stoch_k': k, 'stoch_d': d}

_INDICATOR_GROUPS['stoch'] = lambda c, h, l, v, tr: compute_stochastic(c, h, l, v, tr)
```

Then access via `ctx.ind_1h['stoch_k']` on all timeframes.

### Data available to strategies

**What's automatically loaded per token:**

| Source | Columns | Access |
|--------|---------|--------|
| Spot parquet | open, high, low, close, volume | `ctx.df_1h`, `ctx.ind_1h` |
| Perp parquet | open, high, low, close, volume, funding_rate, funding_1h | `ctx.df_1h`, `ctx.funding_1h`, `ctx.funding_raw` |
| Liquidity mask | bool array | `ctx.liquidity_mask` |
| Rolling ADV | float array | `ctx.rolling_adv` |
| Regime | int8 array (0-4) | `ctx.regime_1h` |
| Enriched parquet | vpin, realized_vol, taker_buy_ratio, etc. | `ctx.enriched` DataFrame, `ctx.custom['enr_*']` |

**What exists but is NOT auto-loaded (`data/alternative/`):**

| Data | Files | Coverage |
|------|-------|----------|
| Positioning (L/S ratios) | `binance_positioning_hourly/`, `binance_top_trader_ls_*.json` | BTC/ETH, hourly |
| Open interest | `binance_oi/`, `binance_open_interest_hourly.json` | Multi-token, hourly |
| Funding rates | `binance_funding_rates_full.json`, `dydx_v4_funding_btc.json` | Multi-exchange |
| Taker buy/sell | `binance_taker_buy_sell_hourly.json` | BTC/ETH |
| Options (Deribit) | `deribit_options/` | IV, skew, term structure |
| ETF flows | `etf_flows/` | BTC ETF daily flows |
| Macro | `macro/` | US10Y, DXY, oil, gold |
| Fear & Greed | `fear_greed/` | Daily index |
| On-chain | `onchain_extended/` | Address growth, MVRV, etc. |
| Exchange netflow | `exchange_netflow/` | BTC exchange balances |
| Liquidations | `liquidations/` | Forced close events |
| Trump social | `trump_social/` | Sentiment data |

### How to use alternative data in a strategy

**Pattern: Load, align, and write to `ctx.custom`**

The positioning overlay plugin (`v4/engine.py:720`) is the canonical example:

```python
@register_indicator
def _compute_macro_overlay(ctx: StrategyContext):
    """Load macro data and expose as sizing overlay."""
    n = len(ctx.ind_1h['close'])
    try:
        # 1. Load the data
        import json
        with open('data/alternative/macro/us10y_daily.json') as f:
            raw = json.load(f)
        df = pd.DataFrame(raw)
        df['date'] = pd.to_datetime(df['date'])
        df = df.set_index('date').sort_index()

        # 2. Compute your signal
        us10y_change = df['value'].pct_change(20)  # 20-day change

        # 3. Align to 1h bars (forward-fill daily data)
        aligned = us10y_change.reindex(ctx.idx_1h, method='ffill').values
        aligned = np.nan_to_num(aligned, nan=0.0)

        # 4. Z-score for stationarity
        z = rolling_zscore(aligned, 504)  # 21-day rolling window

        # 5. Map to sizing multiplier
        ctx.custom['macro_z'] = z
        ctx.custom['macro_mult'] = np.clip(1.0 - z * 0.3, 0.3, 2.0)
    except Exception:
        ctx.custom['macro_z'] = np.zeros(n, dtype=np.float64)
        ctx.custom['macro_mult'] = np.ones(n, dtype=np.float64)
```

**Key rules for alternative data:**

1. **Always align to `ctx.idx_1h`** using `reindex(method='ffill')` — the simulator
   operates on 1h bars
2. **Forward-fill only** — never backfill (look-ahead bias)
3. **Handle missing data** — `np.nan_to_num` and fallback to neutral values (1.0 for
   multipliers, 0.0 for z-scores)
4. **Wrap in try/except** — data files may not exist on all machines
5. **Use `ctx.align_daily_to_1h()`** for daily arrays — it handles the forward-fill
   and index alignment in one call

### Cross-token and global data

`StrategyContext` is per-token, but plugins can load global data:

```python
@register_indicator
def _compute_btc_dominance(ctx: StrategyContext):
    """BTC dominance affects alt sizing — global signal, per-token application."""
    n = len(ctx.ind_1h['close'])
    try:
        # Load BTC data regardless of current token
        btc_df = pd.read_parquet('data/spot/1h_cache/BTC_1h.parquet')
        btc_close = btc_df['close'].values

        # Compute BTC-specific signal
        btc_mom = np.zeros(len(btc_close))
        btc_mom[24:] = (btc_close[24:] - btc_close[:-24]) / btc_close[:-24]

        # Align to this token's index
        btc_series = pd.Series(btc_mom, index=btc_df.index)
        aligned = btc_series.reindex(ctx.idx_1h, method='ffill').values
        ctx.custom['btc_momentum'] = np.nan_to_num(aligned, nan=0.0)

        # For alts: boost size when BTC is trending (alts follow)
        if ctx.ticker != 'BTC':
            ctx.custom['btc_follow_mult'] = np.where(aligned > 0.05, 1.3, 1.0)
        else:
            ctx.custom['btc_follow_mult'] = np.ones(n)
    except Exception:
        ctx.custom['btc_momentum'] = np.zeros(n)
        ctx.custom['btc_follow_mult'] = np.ones(n)
```

### Data pipeline summary

| What | Auto-loaded? | Extension method | Engine change needed? |
|------|-------------|-----------------|----------------------|
| New technical indicator from OHLCV | N/A — compute it | Inline, `@register_indicator`, or `_INDICATOR_GROUPS` | No (inline/plugin), Yes (group) |
| Alternative data (JSON/parquet) | No | Plugin loads file, aligns to `ctx.idx_1h`, writes `ctx.custom` | No |
| Cross-token data | No | Plugin loads other token's parquet, aligns | No |
| New column in core parquet | No | Modify `tools/build_parquet_cache.py` + data fetch scripts | Yes |
| New exchange data | No | Add fetch script + parquet builder | Yes |

---

## 8. Extension Points for Researchers

### What you CAN experiment with (no engine changes needed)

| Extension point | How to use | Example |
|----------------|-----------|---------|
| **Different exit combinations** | Set StrategyResult fields | Add RSI exit + breakeven to existing strategy |
| **Custom sizing model** | Implement SizingModel, register in `_SIZING_MODELS` | Fixed-fraction, risk-parity, volatility-scaled |
| **Regime parameters** | Set `regime_params` on StrategySpec | Different ADX threshold, EMA pair |
| **Entry ordering** | Set `conviction_mode` on PortfolioConfig | "ranked" for conviction-first ordering |
| **Per-bar sizing overlay** | Return `size_multiplier` array from strategy | Regime-based sizing: reduce in CRISIS, boost in UPTREND |
| **Trail schedules** | Return `trail_schedule` or `time_trail_schedule` | Tighten trail as profit grows or time passes |
| **Partial profit-taking** | Return `partial_tp_atr`, `partial_tp_pct` | Close 50% at +2 ATR, trail remainder |
| **Chandelier stop** | Return `chandelier_lookback > 0` | Trail from 20-bar high instead of all-time |
| **Bear-conditional behavior** | Return `bear_target_mult`, `bear_max_hold` | Tighter targets + shorter holds in DOWNTREND |
| **Circuit breakers** | Set `circuit_breaker_r` on StrategySpec | Emergency exit at 4x initial risk |
| **Pump filters** | Set `pump_filter_*` on StrategySpec | Block entries during funding or range anomalies |
| **Concentration limit** | Set `concentration_limit` on PortfolioConfig | Per-portfolio concentration sweep |
| **Combined strategies** | Return secondary_* fields | Spot+perp legs with independent management |
| **New indicator (inline)** | Compute in strategy function from `ctx.ind_1h`/`ctx.df_1h` | Williams %R, custom oscillator, signal composite |
| **New indicator (shared)** | `@register_indicator` plugin writes to `ctx.custom` | Stochastic, Ichimoku, custom factor |
| **Alternative data overlay** | Plugin loads JSON/parquet, aligns to `ctx.idx_1h` | Macro (US10Y, DXY), positioning, options IV |
| **Cross-token signals** | Plugin loads other token's parquet | BTC dominance, alt-correlation regime |
| **New indicator group** | Add to `_INDICATOR_GROUPS` dict in `v4/engine.py` | Stochastic, Ichimoku, Keltner channels |

### What requires engine changes (discuss before attempting)

- New exit handler types (need a new class in `exit_handlers.py`)
- ~~New slippage models~~ — **NOW PLUGGABLE** via `SlippageModel` registry (see §4.5)
- New constraint types in the entry chain
- Changes to the bar processing order (exits before entries)
- New columns in core parquet (modify `tools/build_parquet_cache.py`)
- New exchange data source (add fetch script + parquet builder)

---

## 9. Experimentation Recipes

### Recipe 1: Different exit strategies on the same signal

Test how exit configuration affects performance for a base strategy:

```python
# In strategies/s99_exit_experiment.py
from strategies.s56_signal_timed_momentum import strategy as base_strategy

def strategy(ctx):
    """s56 base with experimental exits."""
    result = base_strategy(ctx)

    # Experiment: replace default exits
    return StrategyResult(
        entry_mask=result.entry_mask,
        direction=result.direction,
        edge=result.edge,
        size_multiplier=result.size_multiplier,

        # --- EXIT EXPERIMENT ---
        stop_mult=2.5,                    # tighter stop (was 3.0)
        trail_mult=1.0,                   # tighter trail (was 1.5)
        breakeven_atr=0.3,               # breakeven at +0.3 ATR
        exit_regimes={0, 4},             # exit on CRISIS + DOWNTREND
        rsi_exit_level=80.0,             # RSI exit for longs
        max_hold=336,                    # 14 days (was 30)
        bear_max_hold=24,               # 1 day in DOWNTREND
    )
```

Run the experiment:
```bash
# Compare base vs experiment
python v4/portfolio_backtest.py --strategy s56 --months 72 --capital 200000
python v4/portfolio_backtest.py --strategy s99 --months 72 --capital 200000
```

### Recipe 2: Custom sizing model

Test a fixed-fraction sizing model against Kelly:

```python
# In research/sizing_experiment.py
from v4.sizing import SizingModel, _SIZING_MODELS

class VolTargetSizing:
    """Size positions to target a specific portfolio volatility."""
    def __init__(self, target_vol: float = 0.15):
        self.target_vol = target_vol

    def compute_size(self, strategy_equity, rolling_adv, volatility,
                     edge, size_multiplier, cap_multiplier, max_trade_pct,
                     adv_cap_pct=0.05, **kwargs):
        if edge < kwargs.get('edge_minimum', 0.10):
            return 0.0
        if volatility <= 0:
            return 0.0
        # Target vol sizing: pos = equity * (target_vol / asset_vol)
        raw = strategy_equity * (self.target_vol / volatility) * size_multiplier
        # Still respect ADV cap
        adv_cap = rolling_adv * adv_cap_pct
        # Still respect capital cap
        cap_pct = kwargs.get('cap_pct_override', 0) or 0.10
        capital_cap = strategy_equity * cap_pct * cap_multiplier
        pos = min(raw, capital_cap, adv_cap)
        if max_trade_pct > 0:
            pos = min(pos, strategy_equity * max_trade_pct)
        return max(pos, 0.0)

# Register and run
_SIZING_MODELS["vol_target"] = VolTargetSizing(target_vol=0.15)

from v4.config import StrategySpec
spec = StrategySpec(strategy_id="s56", sizing_model="vol_target")
# ... run backtest with this spec
```

### Recipe 3: Regime-adaptive sizing

Create a wrapper that reduces sizing in unfavorable regimes:

```python
# In strategies/s99_regime_sized.py
from strategies.s56_signal_timed_momentum import strategy as base_strategy
from v4.engine import StrategyResult, CRISIS, QUIET, UPTREND, RANGE, DOWNTREND

def strategy(ctx):
    result = base_strategy(ctx)

    # Build per-bar size multiplier based on regime
    regime = ctx.regime_1h
    regime_scale = np.ones(len(regime), dtype=np.float32)
    regime_scale[regime == CRISIS] = 0.0       # no entries in CRISIS
    regime_scale[regime == DOWNTREND] = 0.3    # 30% size in DOWNTREND
    regime_scale[regime == RANGE] = 0.5        # 50% size in RANGE
    regime_scale[regime == QUIET] = 0.7        # 70% size in QUIET
    regime_scale[regime == UPTREND] = 1.0      # full size in UPTREND

    # Combine with base strategy's size_multiplier
    base_sm = np.broadcast_to(
        np.atleast_1d(result.size_multiplier),
        regime_scale.shape,
    ).copy()
    combined = base_sm * regime_scale

    return StrategyResult(
        entry_mask=result.entry_mask,
        direction=result.direction,
        size_multiplier=combined,
        # ... copy other fields from result
    )
```

### Recipe 4: Custom regime parameters per strategy

Test different regime sensitivity for a carry strategy:

```json
{
    "strategy_id": "s65",
    "market": "perp",
    "regime_params": {
        "adx_threshold": 35,
        "crisis_mult": 3.0,
        "ema_pair": [10, 30]
    }
}
```

This makes regime detection more conservative (higher ADX bar, higher crisis threshold)
and more responsive (shorter EMA pair). The carry strategy may benefit from staying in
trades longer during moderate volatility.

### Recipe 5: Partial profit-taking + time-decay trail

Combine two exit techniques for a momentum strategy:

```python
def strategy(ctx):
    # ... entry logic ...

    # Time-based trail: tightens over position age
    time_schedule = np.array([
        [24, 1.5],   # first 24 hours: 1.5 ATR trail
        [48, 1.2],   # 24-48 hours: 1.2 ATR
        [96, 0.8],   # 48-96 hours: 0.8 ATR (aggressive)
    ])

    return StrategyResult(
        entry_mask=mask, direction=direction,
        stop_mult=3.0,
        trail_mult=1.5,
        time_trail_schedule=time_schedule,
        # Partial TP: close half at +2 ATR profit
        partial_tp_atr=2.0,
        partial_tp_pct=0.5,
        partial_tp_trail=1.0,  # tight trail on remaining half
    )
```

### Recipe 6: Concentration limit sweep

Test how concentration limits affect portfolio performance:

```bash
# Sweep across concentration limits
for conc in 0.05 0.10 0.15 0.20 0.30 0.50 1.0; do
    echo "=== concentration_limit=$conc ==="
    python v4/portfolio_backtest.py \
        --strategy s56,s57 --months 72 --capital 200000 \
        --concentration-limit $conc
done
```

Or use the dedicated tool: `python tools/concentration_sweep.py`

### Recipe 7: Custom indicator as entry signal

Add a Williams %R oscillator as an entry filter:

```python
# In strategies/s99_williams_entry.py
import numpy as np
import pandas as pd
from engine import StrategyContext, StrategyResult, CRISIS

def strategy(ctx: StrategyContext) -> StrategyResult:
    close = ctx.ind_1h['close']
    high = ctx.ind_1h['high']
    low = ctx.ind_1h['low']
    n = len(close)

    # Williams %R (14-period)
    highest = pd.Series(high).rolling(14).max().values
    lowest = pd.Series(low).rolling(14).min().values
    wr = -100 * (highest - close) / np.maximum(highest - lowest, 1e-10)

    # Entry: oversold + uptrend regime
    regime = ctx.regime_1h
    entry_mask = (wr < -80) & (regime == 2)  # oversold in UPTREND
    direction = np.ones(n, dtype=np.int8)     # long only

    return StrategyResult(
        entry_mask=entry_mask,
        direction=direction,
        edge=0.25,
        stop_mult=3.0,
        trail_mult=1.5,
        exit_regimes={0},
    )
```

### Recipe 8: Alternative data overlay (macro signal)

Use US10Y yield changes as a sizing overlay:

```python
# In strategies/s99_macro_sized.py
import json
import numpy as np
import pandas as pd
from engine import StrategyContext, StrategyResult
from strategies.s11_momentum_burst import strategy as base_strategy

def _load_macro_signal(ctx):
    """Load US10Y 20d change, align to 1h bars, z-score."""
    try:
        with open('data/alternative/macro/us10y_daily.json') as f:
            raw = json.load(f)
        df = pd.DataFrame(raw)
        df['date'] = pd.to_datetime(df['date'])
        df = df.set_index('date').sort_index()
        change_20d = df['value'].pct_change(20)
        # Align daily → 1h (forward-fill)
        aligned = change_20d.reindex(ctx.idx_1h, method='ffill').values
        aligned = np.nan_to_num(aligned, nan=0.0)
        # Z-score (504h = 21 days)
        s = pd.Series(aligned)
        mu = s.rolling(504, min_periods=100).mean()
        sigma = s.rolling(504, min_periods=100).std().clip(lower=1e-10)
        z = ((s - mu) / sigma).values
        return np.clip(np.nan_to_num(z, nan=0.0), -3, 3)
    except Exception:
        return np.zeros(len(ctx.ind_1h['close']))

def strategy(ctx: StrategyContext) -> StrategyResult:
    result = base_strategy(ctx)
    macro_z = _load_macro_signal(ctx)

    # Rising yields (positive z) → risk-off → reduce size
    # Falling yields (negative z) → risk-on → boost size
    macro_mult = np.clip(1.0 - macro_z * 0.25, 0.3, 1.5)

    # Combine with base size_multiplier
    base_sm = np.broadcast_to(
        np.atleast_1d(result.size_multiplier),
        macro_mult.shape,
    ).copy()

    return StrategyResult(
        entry_mask=result.entry_mask,
        direction=result.direction,
        edge=result.edge,
        size_multiplier=base_sm * macro_mult,
        stop_mult=result.stop_mult,
        trail_mult=result.trail_mult,
        target_mult=result.target_mult,
    )
```

### Recipe 9: Shared indicator plugin for multiple strategies

Register once, use everywhere:

```python
# Add to v4/engine.py (or a separate indicators file imported at startup)
@register_indicator(name='keltner')
def _compute_keltner(ctx: StrategyContext):
    """Keltner Channels: EMA(20) ± 2*ATR(14)."""
    ema = ctx.ind_1h['ema_20']
    atr = ctx.ind_1h['atr']
    ctx.custom['kc_upper'] = ema + 2 * atr
    ctx.custom['kc_lower'] = ema - 2 * atr
    ctx.custom['kc_pct'] = (ctx.ind_1h['close'] - ctx.custom['kc_lower']) / \
                            np.maximum(ctx.custom['kc_upper'] - ctx.custom['kc_lower'], 1e-10)
```

Then in any strategy:
```python
def strategy(ctx):
    kc_pct = ctx.custom.get('kc_pct')
    if kc_pct is None:
        return StrategyResult(entry_mask=np.zeros(n, dtype=bool), direction=np.ones(n, dtype=np.int8))

    # Entry: price breaks above upper Keltner channel
    entry_mask = kc_pct > 1.0
    ...
```

If using `REQUIRED_PLUGINS`:
```python
REQUIRED_PLUGINS = ['keltner']  # only this plugin runs (faster)
```

---

## Quick Reference: StrategyResult Defaults (Copy-Paste)

```python
StrategyResult(
    # REQUIRED
    entry_mask=mask,                    # np.ndarray[bool]
    direction=direction,                # np.ndarray[int8]

    # STOPS & TRAILS
    stop_mult=3.0,                     # initial stop (ATR)
    trail_mult=1.5,                    # trailing stop (ATR)
    target_mult=999.0,                 # take-profit (ATR), 999=disabled
    no_stop_bars=0,                    # grace period (bars)
    breakeven_atr=0.0,                 # breakeven ratchet (0=off)
    chandelier_lookback=0,             # chandelier stop (0=off)
    trail_schedule=None,               # profit-progressive trail
    time_trail_schedule=None,          # time-progressive trail
    max_trail_mult=None,               # per-bar trail ceiling

    # HOLD LIMITS
    min_hold=6,                        # minimum hold (hours)
    max_hold=720,                      # maximum hold (hours)
    bear_target_mult=0.0,              # tighter TP in DOWNTREND (0=off)
    bear_max_hold=0,                   # shorter hold in DOWNTREND (0=off)

    # EXITS
    exit_regimes={0},                  # force exit in these regimes
    rsi_exit_level=999.0,              # RSI exit (999=off)
    convex_exit=False,                 # convex exit mode
    funding_exit_threshold=0.0,        # cumulative funding exit (0=off)
    regime_exit_min_bars=6,            # min bars before regime exit

    # PARTIAL TP
    partial_tp_atr=0.0,               # partial TP threshold (0=off)
    partial_tp_pct=0.5,               # fraction to close
    partial_tp_trail=1.5,             # trail for remainder

    # SIZING
    edge=0.35,                         # Kelly numerator + entry gate
    size_multiplier=1.0,               # conviction scaler [0,1]
    cap_multiplier=1.0,                # ADV cap scaler
    max_trade_pct=0.0,                 # hard cap (0=off)
    leverage=1.0,                      # notional multiplier

    # MARKET
    market_type=0,                     # SPOT=0, PERP=1, COMBINED=2
)
```
