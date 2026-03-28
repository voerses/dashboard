# V4 Sizing Pipeline Reference

> **Rule #1: Spot positions MUST NEVER exceed 100% of equity.** The engine enforces
> `spot_max_equity_pct` (default 1.0, max safety rail 1.0). Any strategy returning
> `size_multiplier > 1.0` on spot will be capped. There is NO leverage on spot.

## Sizing Model (Pluggable Registry)

Position sizing is dispatched through the `SizingModel` protocol (`v4/sizing.py`).
Each strategy selects its model via `StrategySpec.sizing_model` (default `"kelly"`).
The simulator caches resolved models in `_sizing_model_cache` per strategy.
See `knowledge/V4_EXPERIMENTATION_GUIDE.md` §4 for custom sizing model implementation.

## How Position Size is Computed

```
strategy_equity = sizing_eq × weight × dd_mult   [dd_mult from dd_scaling overlay]
pos_usd = min(raw_kelly, capital_cap, adv_cap)
         → then min(max_trade_pct cap)
         → then min(adv_sizing reduction)      [if enabled]
         → then min(spot_equity_cap)            [if spot]
         → then reject if < min_position_usd ($200)
         → then scale down for concentration/capital
```

### Drawdown Scaling Overlay

When `dd_scaling` is configured on StrategySpec, a multiplier is applied to `strategy_equity`
before the sizing model runs. The multiplier is computed from the portfolio's drawdown
relative to its MTM equity high-water mark:

```python
dd = 1.0 - (portfolio_equity + unrealized) / max_equity_watermark
# Walk dd_scaling list: for each (threshold, fraction), if dd >= threshold, mult = fraction
# Last matching threshold wins (list should be sorted ascending by threshold)
```

Example: `DD_SCALING = [(0.05, 0.75), (0.10, 0.50), (0.15, 0.25), (0.20, 0.0)]`
- At 5% DD: size × 0.75 (reduce 25%)
- At 10% DD: size × 0.50 (half size)
- At 15% DD: size × 0.25 (quarter size)
- At 20% DD: size × 0.0 (stop trading)

The watermark uses MTM equity (realized + unrealized), updated at bar-close.
Applies in both raw and normal modes. Empty list = disabled (default).

### Formula Detail

```
raw_kelly   = strategy_equity × kelly_frac × vol_adj
  where kelly_frac = kelly_mult × edge × size_multiplier
  where vol_adj    = target_vol / max(volatility, vol_floor)

capital_cap = strategy_equity × cap_pct × cap_multiplier
adv_cap     = rolling_adv × adv_cap_pct
spot_cap    = strategy_equity × spot_max_equity_pct
```

## Three Configuration Layers

### Layer 1: Engine Defaults (SizingDefaults) — v4/config.py
Immutable engine constants. Non-overridable params marked with ❌.

| Parameter | Default | Overridable | Safety Rails | Purpose |
|-----------|---------|-------------|--------------|---------|
| edge_minimum | 0.10 | ✅ | 0.05–0.50 | Min edge to enter |
| target_vol | 0.02 | ✅ | 0.005–0.05 | Vol scaling numerator |
| spot_max_equity_pct | 1.0 | ✅ | 0.10–1.0 | Spot position cap |
| kelly_mult_override | 0.0 | ✅ | 0.05–0.50 | Fixed Kelly (0=use ADV curve) |
| kelly_mult_scale | 1.0 | ✅ | 0.5–2.0 | Scale ADV-curve Kelly |
| cap_pct_override | 0.0 | ✅ | 0.01–0.15 | Fixed cap_pct (0=use curve) |
| cap_pct_scale | 1.0 | ✅ | 0.5–2.0 | Scale ADV-curve cap |
| min_adv_usd | 500K | ✅ | 100K–10M | Liquidity gate |
| adv_lookback_days | 30 | ✅ | 7–90 | ADV window |
| kelly_mult_floor | 0.15 | ✅ | 0.05–0.40 | ADV curve floor |
| kelly_mult_range | 0.35 | ✅ | 0.10–0.80 | ADV curve range |
| cap_pct_floor | 0.02 | ✅ | 0.005–0.08 | Cap curve floor |
| cap_pct_range | 0.10 | ✅ | 0.02–0.30 | Cap curve range |
| adv_scaling_divisor | 5.0 | ✅ | 1.0–20.0 | ADV log-scale denominator |
| vol_floor | 0.005 | ❌ | — | Min volatility |
| unrealized_pnl_floor | 0.85 | ❌ | — | Sizing equity floor |
| funding_buffer_pct | 0.01 | ❌ | — | Funding cost reserve |

> **Changed 2026-03-26:** `kelly_mult_floor`, `kelly_mult_range`, `cap_pct_floor`,
> `cap_pct_range`, and `adv_scaling_divisor` moved from NON_OVERRIDABLE to SAFETY_RAILS.
> Now overridable per-strategy via `sizing_overrides` with bounded min/max values.
> Only `vol_floor`, `unrealized_pnl_floor`, and `funding_buffer_pct` remain non-overridable.

### Layer 2: Portfolio Config (PortfolioConfig) — configs/*.json
Set per-portfolio in the JSON config. NOT strategy-overridable.

| Parameter | Default | Purpose |
|-----------|---------|---------|
| capital | 200,000 | Initial equity |
| min_position_usd | 200 | Entry rejection floor |
| adv_cap_pct | 0.05 | ADV hard cap (5% of daily volume) |
| concentration_limit | 0.10 | Per-token max (10% of portfolio) |
| base_spread_bps | 3.0 | Slippage base spread |
| impact_coeff | 0.03 | Market impact coefficient |
| max_slip_bps | 300 | Slippage ceiling |

### Layer 3: Strategy-Level Controls

**Via StrategyResult (signal layer, per-bar):**

| Field | Type | Default | Effect |
|-------|------|---------|--------|
| size_multiplier | float/ndarray | 1.0 | Scales kelly_frac (0-1 typical) |
| cap_multiplier | float/ndarray | 1.0 | Scales capital_cap (>1 for BTC-class ADV) |
| max_trade_pct | float | 0.0 | Absolute cap: pos ≤ equity × this (0=disabled) |
| edge | float | 0.35 | Gate + Kelly numerator |
| leverage | float/ndarray | 1.0 | Notional = margin × leverage |

**Via sizing_overrides in config JSON:**

```json
{
  "strategy_id": "s320",
  "sizing_overrides": {
    "kelly_mult_override": 0.50,
    "spot_max_equity_pct": 0.95
  }
}
```

Validated by `resolve_sizing()` against SAFETY_RAILS.

## ADV Curve: How ADV Maps to Kelly/Cap

```python
adv_m = adv / 1,000,000
frac = clip((log10(max(adv_m, 0.1)) + 1) / adv_scaling_divisor, 0, 1)
kelly_mult = kelly_mult_floor + kelly_mult_range × frac   # [0.15, 0.50]
cap_pct    = cap_pct_floor + cap_pct_range × frac         # [0.02, 0.12]
```

| ADV | frac | kelly_mult | cap_pct |
|-----|------|------------|---------|
| $100K | 0.0 | 0.15 | 0.02 |
| $10M | 0.40 | 0.29 | 0.06 |
| $100M | 0.60 | 0.36 | 0.08 |
| $1B+ | 0.80+ | 0.43+ | 0.10+ |

**Override short-circuits the curve:** `kelly_mult_override > 0` → use fixed value.

**ADV curve shape is now per-strategy configurable** via `sizing_overrides`: `kelly_mult_floor`, `kelly_mult_range`, `cap_pct_floor`, `cap_pct_range`, `adv_scaling_divisor` can all be overridden within SAFETY_RAILS bounds.

## Entry Rejection Order

1. Min conviction → REJECT
2. Pump filter (range anomaly) → REJECT
3. Pump filter (funding z-score) → REJECT
4. Portfolio position limit → REJECT
5. Strategy position limit → REJECT
6. `sizing_model.compute_size()` → compute pos_usd (dispatched via `StrategySpec.sizing_model`)
7. `pos_usd < min_position_usd` → REJECT (min_size)
8. `pos_usd > rolling_adv × adv_cap_pct` → REJECT (adv_cap)
9. Concentration limit → SCALE DOWN (not reject)
10. Free capital check → SCALE DOWN (not reject)

## Sizing Equity (Dynamic)

```
sizing_eq = max(
    min(portfolio_equity + unrealized_pnl, portfolio_equity),  # no inflation
    portfolio_equity × unrealized_pnl_floor                     # 85% floor
)
strategy_equity = sizing_eq × strategy_weight
```

## Slippage Model (Pluggable Registry)

The slippage model is now pluggable via the `SlippageModel` protocol in `v4/sizing.py`.
Each strategy can select its slippage model via `StrategySpec.slippage_model` (default `"sqrt"`).

**Default: SqrtImpactSlippage (`"sqrt"`)**
```
participation = pos_usd / (rolling_adv / 24)
slip_bps = base_spread_bps + impact_coeff × sqrt(participation) × 10000
slip_bps = min(slip_bps, max_slip_bps)
```

Custom slippage models can be registered in `_SLIPPAGE_MODELS` and selected per-strategy.
See `knowledge/V4_EXPERIMENTATION_GUIDE.md` §4.5 for implementation details.

## Common Sizing Patterns

### BTC-only spot strategy (s320-style)
```json
sizing_overrides: { "kelly_mult_override": 0.50, "spot_max_equity_pct": 0.95 }
```
StrategyResult: `cap_multiplier=8.0, max_trade_pct=0.95`
- Fixed 0.50 Kelly (not ADV-curve dependent)
- cap_multiplier=8.0 lifts ADV cap from $24K to $192K (BTC ADV is $1B+)
- spot_max_equity_pct=0.95 → max position 95% of equity
- max_trade_pct=0.95 → redundant safety cap

### Multi-token perp strategy (typical)
No sizing_overrides needed — ADV curve handles it.
StrategyResult: `size_multiplier=conviction_array, cap_multiplier=1.0`

### Low-liquidity altcoin strategy
```json
sizing_overrides: { "min_adv_usd": 200000 }
```
StrategySpec: `adv_sizing_enabled=True, adv_sizing_base=50000000, adv_sizing_floor=0.3`

## Critical Rules

1. **SPOT ≤ 100% EQUITY.** No exceptions. `spot_max_equity_pct` max is 1.0 by safety rail.
2. **Never set kelly_mult_override > 0.50.** Safety rail blocks it.
3. **size_multiplier is a conviction scaler, NOT a leverage knob.** Keep in [0, 1].
4. **cap_multiplier > 1 is for high-ADV tokens only.** For BTC ($1B+ ADV) it safely lifts the cap. For a $10M ADV token, cap_multiplier=8 could breach ADV limits.
5. **min_position_usd rejections** indicate the strategy is trying to enter with tiny size. Fix by increasing kelly_mult or checking size_multiplier isn't near zero at entry bars.
