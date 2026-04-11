# V4 Sizing System -- Complete Reference

> Last updated: 2026-04-10. Source files: `v4/sizing.py`, `v4/config.py`,
> `v4/simulator.py`, `v4/universe.py`, `v4/engine.py`.

---

## 1. Architecture Overview

```
Strategy code                Config JSON                    Engine defaults
     |                            |                              |
     v                            v                              v
StrategyResult fields      StrategySpec.sizing_overrides    SizingDefaults
(edge, size_multiplier,    (kelly_mult_override, etc.)     (edge_minimum=0.10,
 cap_multiplier,                                            target_vol=0.02, ...)
 max_trade_pct, leverage)         |                              |
     |                            +----------+-------------------+
     |                                       |
     |                              resolve_sizing()
     |                           (merge overrides + rails)
     |                                       |
     |                                       v
     |                              Resolved SizingDefaults
     |                                       |
     +----------+----------------------------+
                |
                v
     SizingModel.compute_size()          <-- pluggable via registry
     (default: KellySizing)
                |
                v
          pos_usd (raw)
                |
                v
     Simulator constraints:
       - min_position_usd ($200)
       - ADV hard cap (adv_cap_pct)
       - concentration_limit (scale down)
       - free_capital (scale down)
                |
                v
          margin_usd (final)
```

---

## 2. The EXACT Sizing Formula

### Step 1: Resolve kelly_mult and cap_pct

These come from either a fixed override or the ADV curve:

```python
# If kelly_mult_override > 0: use it directly
kelly_mult = kelly_mult_override
# Else: compute from ADV curve
adv_m = max(rolling_adv, 1.0) / 1_000_000
log_adv = log10(max(adv_m, 0.1))
frac = clip((log_adv + 1) / adv_scaling_divisor, 0.0, 1.0)
kelly_mult = kelly_mult_floor + kelly_mult_range * frac    # default: 0.15 + 0.35*frac
kelly_mult *= kelly_mult_scale                              # post-curve multiplier

# Same logic for cap_pct:
# If cap_pct_override > 0: use it directly
# Else: cap_pct = (cap_pct_floor + cap_pct_range * frac) * cap_pct_scale
```

### Step 2: Compute raw Kelly size

```python
kelly_frac = kelly_mult * edge * size_multiplier
vol_adj    = target_vol / max(volatility, vol_floor)        # volatility = atr/close
raw        = strategy_equity * kelly_frac * vol_adj
```

### Step 3: Compute caps

```python
cap     = strategy_equity * cap_pct * cap_multiplier
adv_cap = rolling_adv * adv_cap_pct                         # default 5% of ADV
```

### Step 4: Take minimum + apply optional caps

```python
pos_usd = min(raw, cap, adv_cap)

if max_trade_pct > 0:
    pos_usd = min(pos_usd, strategy_equity * max_trade_pct)

if adv_sizing_enabled:
    adv_mult = min(1.0, max(adv_sizing_floor, sqrt(rolling_adv / adv_sizing_base)))
    pos_usd *= adv_mult

if leverage <= 1.0:  # spot mode
    pos_usd = min(pos_usd, strategy_equity * spot_max_equity_pct)
```

### Step 5: Simulator post-sizing constraints (non-raw mode)

```python
# Constraint 5: min_position_usd ($200)
if pos_usd < min_position_usd: REJECT

# Constraint 6: ADV hard cap
if pos_usd > rolling_adv * adv_cap_pct: REJECT

# Constraint 7: concentration_limit (SCALE DOWN, not reject)
existing_margin = total_margin_for_token(token)
max_for_token = concentration_limit * portfolio_eq - existing_margin
if pos_usd > max_for_token:
    pos_usd = max_for_token  # scale down

# Constraint 8: free_capital (SCALE DOWN, not reject)
if free_capital < margin_usd + entry_fee:
    pos_usd = affordable_amount  # scale down
```

### Step 6: Margin vs Notional

```python
margin_usd = pos_usd                          # capital locked
if is_perp and leverage > 1.0:
    notional_usd = pos_usd * leverage         # actual exposure
else:
    notional_usd = pos_usd
```

---

## 3. Complete Parameter Reference

### 3.1 SizingDefaults (engine-level, v4/config.py)

| Parameter | Type | Default | Safety Rails | Overridable | Per-bar? | Description |
|-----------|------|---------|--------------|-------------|----------|-------------|
| `edge_minimum` | float | 0.10 | [0.05, 0.50] | Yes | Scalar | Min edge to allow entry. Below this: pos_usd = 0. |
| `target_vol` | float | 0.02 | [0.005, 0.05] | Yes | Scalar | Numerator in vol_adj = target_vol / volatility |
| `vol_floor` | float | 0.005 | NON_OVERRIDABLE | No | Scalar | Min volatility denominator (prevents division explosion) |
| `spot_max_equity_pct` | float | 1.0 | [0.10, 1.0] | Yes | Scalar | Max spot position as fraction of equity |
| `kelly_mult_floor` | float | 0.15 | [0.05, 0.40] | Yes | Scalar | ADV curve: minimum kelly_mult (for micro-cap) |
| `kelly_mult_range` | float | 0.35 | [0.10, 0.80] | Yes | Scalar | ADV curve: range added to floor (max kelly = floor+range) |
| `cap_pct_floor` | float | 0.02 | [0.005, 0.08] | Yes | Scalar | ADV curve: minimum cap_pct (for micro-cap) |
| `cap_pct_range` | float | 0.10 | [0.02, 0.30] | Yes | Scalar | ADV curve: range added to floor |
| `adv_scaling_divisor` | float | 5.0 | [1.0, 20.0] | Yes | Scalar | ADV curve: log-scale denominator |
| `kelly_mult_override` | float | 0.0 | [0.05, 0.50] | Yes | Scalar | Fixed kelly_mult (0 = use ADV curve) |
| `kelly_mult_scale` | float | 1.0 | [0.5, 2.0] | Yes | Scalar | Post-curve multiplier on kelly_mult |
| `cap_pct_override` | float | 0.0 | [0.01, 0.30] | Yes | Scalar | Fixed cap_pct (0 = use ADV curve) |
| `cap_pct_scale` | float | 1.0 | [0.5, 2.0] | Yes | Scalar | Post-curve multiplier on cap_pct |
| `min_adv_usd` | float | 500,000 | [100K, 10M] | Yes | Scalar | Min ADV to trade (liquidity gate) |
| `adv_lookback_days` | int | 30 | [7, 90] | Yes | Scalar | Rolling ADV window |
| `unrealized_pnl_floor` | float | 0.85 | NON_OVERRIDABLE | No | Scalar | sizing_eq >= portfolio_eq * this |
| `funding_buffer_pct` | float | 0.01 | NON_OVERRIDABLE | No | Scalar | Reserve for funding costs |

### 3.2 PortfolioConfig (portfolio-level)

| Parameter | Type | Default | Set where | Description |
|-----------|------|---------|-----------|-------------|
| `capital` | float | 200,000 | Config JSON | Initial equity |
| `adv_cap_pct` | float | 0.05 | Config JSON | Hard cap: pos <= 5% of ADV |
| `concentration_limit` | float | 0.10 | Config JSON | Per-token max: 10% of portfolio equity |
| `min_position_usd` | float | 200.0 | Config JSON | Below this: entry rejected |
| `max_portfolio_positions` | int | 40 | Config JSON | Global position limit |
| `max_sizing_equity` | float | None | Config JSON | Cap equity used for sizing (None=uncapped) |
| `base_spread_bps` | float | 3.0 | Config JSON | Slippage base spread |
| `impact_coeff` | float | 0.03 | Config JSON | Market impact coefficient |
| `max_slip_bps` | float | 300 | Config JSON | Slippage ceiling |

### 3.3 StrategySpec (per-strategy, config JSON)

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `weight` | float | 1.0 | Fraction of portfolio equity: strategy_equity = sizing_eq * weight |
| `max_positions` | int | 15 | Per-strategy position limit |
| `sizing_overrides` | dict | {} | Override SizingDefaults fields (validated by resolve_sizing) |
| `sizing_model` | str | "kelly" | Select sizing model from registry |
| `slippage_model` | str | "sqrt" | Select slippage model from registry |
| `adv_sizing_enabled` | bool | False | Reduce sizing for low-ADV tokens |
| `adv_sizing_base` | float | 100M | Reference ADV for sqrt scaling |
| `adv_sizing_floor` | float | 0.20 | Min multiplier from ADV sizing |
| `dd_scaling` | tuple | () | Drawdown-based position sizing reduction |

### 3.4 StrategyResult (per-bar, set by strategy code)

| Parameter | Type | Default | Per-bar? | Description |
|-----------|------|---------|----------|-------------|
| `edge` | float | 0.35 | Scalar | Kelly numerator + gate check. If < edge_minimum: pos = 0. |
| `size_multiplier` | float/ndarray | 1.0 | Yes (array ok) | Scales kelly_frac. Conviction overlay. |
| `cap_multiplier` | float/ndarray | 1.0 | Yes (array ok) | Scales the capital cap. Lift for high-ADV tokens (BTC). |
| `max_trade_pct` | float | 0.0 | Scalar | Absolute cap: pos <= equity * this. 0=disabled. |
| `leverage` | float/ndarray | 1.0 | Yes (array ok) | Perp leverage: notional = margin * leverage. |

---

## 4. Answers to Specific Questions

### Q1: What creates the "cliff" where edge=0.1 gives $5K but edge=0.05 gives $0?

The `edge_minimum` parameter (default 0.10). In `KellySizing.compute_size()`:

```python
if edge < edge_minimum:
    return 0.0
```

This is a hard gate, not a smooth function. At edge=0.10: position computed normally.
At edge=0.099: position = $0. This is intentional -- low-edge trades aren't worth the
slippage and fees.

**To soften the cliff:** Set `edge_minimum` lower via `sizing_overrides`:
```json
"sizing_overrides": { "edge_minimum": 0.05 }
```
Safety rail allows down to 0.05.

### Q2: What is kelly_mult and where is it set? Can the strategy override it?

`kelly_mult` is the Kelly criterion fraction multiplier. It determines what fraction of
Kelly-optimal sizing to use. Source: either the ADV curve or a fixed override.

**Where it's set (priority order):**
1. `sizing_overrides.kelly_mult_override` > 0: use this fixed value
2. Else: ADV curve computes it: `kelly_mult_floor + kelly_mult_range * frac`
3. Then multiplied by `kelly_mult_scale`

**Can the strategy override it?**
- YES, via `sizing_overrides` in the config JSON (per-strategy)
- YES, via `sizing_defaults` in the config JSON (global)
- NOT directly from StrategyResult -- the strategy code returns `edge` and `size_multiplier`,
  but not kelly_mult. However, `size_multiplier` in StrategyResult effectively scales it
  because `kelly_frac = kelly_mult * edge * size_multiplier`.

**Default range:** kelly_mult is in [0.15, 0.50] from the ADV curve.
For $100M ADV: ~0.36. For $10M ADV: ~0.29.

### Q3: How does ADV cap work?

Two separate ADV-related caps:

**ADV hard cap (portfolio-level):** `adv_cap = rolling_adv * adv_cap_pct`
- Default: 5% of daily volume
- Applied inside compute_size: `pos_usd = min(raw, cap, adv_cap)`
- Also checked again in the simulator as Constraint 6 (REJECT if exceeded)
- Set via PortfolioConfig.adv_cap_pct

**ADV sizing reduction (strategy-level):** When `adv_sizing_enabled=True`:
```python
adv_mult = min(1.0, max(adv_sizing_floor, sqrt(rolling_adv / adv_sizing_base)))
pos_usd *= adv_mult
```
- A $10M ADV token with base=$100M gets `sqrt(0.1) = 0.316`, floored at 0.20
- A $1B ADV token gets `sqrt(10) = 3.16`, capped at 1.0
- This is a conviction-weighted size reduction for low-liquidity tokens

### Q4: What is cap_multiplier and how does it interact with Kelly?

`cap_multiplier` scales the capital cap: `cap = strategy_equity * cap_pct * cap_multiplier`

- Set per-bar via StrategyResult.cap_multiplier (can be array)
- Default: 1.0
- Purpose: lift the cap for high-ADV tokens where the ADV curve gives a low cap_pct
- Example: BTC with ADV > $1B. cap_pct from curve might be 0.10, giving $20K cap on $200K equity.
  Setting cap_multiplier=8.0 lifts it to $160K.
- Does NOT affect the raw Kelly term -- only the cap

### Q5: What is size_multiplier and how does it interact?

`size_multiplier` scales the raw Kelly fraction: `kelly_frac = kelly_mult * edge * size_multiplier`

- Set per-bar via StrategyResult.size_multiplier (can be array)
- Default: 1.0
- Purpose: conviction scaling. Strategy sets size_multiplier to, e.g., its conviction score [0,1]
- It multiplies BOTH the Kelly fraction AND the vol adjustment, so its effect on raw_kelly is linear
- Does NOT affect cap or adv_cap

### Q6: Is there a way to use FIXED FRACTIONAL sizing (e.g., 1/40th of capital per position)?

**Yes, by combining overrides:**

Option A -- cap_pct_override as the binding constraint:
```json
"sizing_overrides": {
    "cap_pct_override": 0.025,     // 2.5% of equity per position
    "kelly_mult_override": 0.50    // set high so cap binds, not Kelly
}
```
With kelly_mult=0.50 and typical edge=0.35, raw Kelly would be ~17.5% * vol_adj.
The cap_pct of 2.5% will almost always bind, giving effectively fixed fractional sizing.

Option B -- max_trade_pct from strategy:
Set `StrategyResult.max_trade_pct = 0.025` to hard-cap every position at 2.5% of equity.
This is the simplest approach but requires strategy code changes.

Option C -- Custom sizing model:
Register a `FixedFractionalSizing` class in the `_SIZING_MODELS` registry.

### Q7: Can the strategy set a custom sizing model?

**Yes.** Via `StrategySpec.sizing_model`:
```json
{ "strategy_id": "s500", "sizing_model": "fixed_frac" }
```

The model must be registered in `_SIZING_MODELS` dict in `v4/sizing.py`:
```python
class FixedFractionalSizing:
    def compute_size(self, strategy_equity, ..., cap_pct_override, **kw):
        return strategy_equity * cap_pct_override  # simple fixed fraction

_SIZING_MODELS["fixed_frac"] = FixedFractionalSizing()
```

The model must implement the `SizingModel` protocol (accept all the same parameters).

### Q8: What sizing_overrides are available in the config?

Everything in SAFETY_RAILS is overridable:
- `edge_minimum` [0.05, 0.50]
- `target_vol` [0.005, 0.05]
- `spot_max_equity_pct` [0.10, 1.0]
- `kelly_mult_override` [0.05, 0.50] (0.0 sentinel = disabled)
- `kelly_mult_scale` [0.5, 2.0]
- `cap_pct_override` [0.01, 0.30] (0.0 sentinel = disabled)
- `cap_pct_scale` [0.5, 2.0]
- `min_adv_usd` [100K, 10M]
- `adv_lookback_days` [7, 90]
- `kelly_mult_floor` [0.05, 0.40]
- `kelly_mult_range` [0.10, 0.80]
- `cap_pct_floor` [0.005, 0.08]
- `cap_pct_range` [0.02, 0.30]
- `adv_scaling_divisor` [1.0, 20.0]

NON_OVERRIDABLE: `unrealized_pnl_floor`, `funding_buffer_pct`, `vol_floor`.

### Q9: How does concentration_limit interact with sizing?

`concentration_limit` (default 0.10 = 10%) caps total margin per token across all strategies:

```python
existing_margin = position_manager.total_margin_for_token(token)
max_for_token = concentration_limit * portfolio_eq - existing_margin
if margin_usd > max_for_token:
    if max_for_token < min_position_usd:
        REJECT  # can't fit even minimum size
    else:
        pos_usd = max_for_token  # scale down
```

This is a SCALE-DOWN constraint, not a reject (unless the remaining room is below $200).
It applies AFTER compute_size, so it can reduce positions below what Kelly would give.

### Q10: What about the composite safety check?

`_validate_composite()` rejects dangerous parameter combinations:
```python
max_vol_adj = target_vol / vol_floor                    # default: 0.02/0.005 = 4.0
max_kelly = kelly_mult_override or (floor+range)*scale  # default: 0.50
worst_case_frac = max_kelly * max_vol_adj               # default: 2.0
if worst_case_frac > 8.0:
    raise ValueError("Composite sizing check failed")
```

---

## 5. Sizing Equity Computation

There are TWO paths depending on `raw_mode`:

### Raw mode (backtesting with raw_mode=True)
```python
strategy_equity = config.capital * spec.weight  # FIXED, no PnL adjustment
strategy_equity *= dd_mult                       # dd_scaling overlay if configured
```

### Normal mode (portfolio simulation)
```python
portfolio_eq = initial_capital + realized_pnl - total_fees - total_funding
# Unrealized PnL: constrain-only (never inflates above realized)
sizing_eq = max(
    min(portfolio_eq + total_unrealized, portfolio_eq),
    portfolio_eq * unrealized_pnl_floor  # 85% floor
)
sizing_eq = max(sizing_eq, 0.0)
if max_sizing_equity is not None:
    sizing_eq = min(sizing_eq, max_sizing_equity)
strategy_equity = sizing_eq * spec.weight
strategy_equity *= dd_mult  # dd_scaling overlay
```

---

## 6. ADV Curve Deep Dive

The ADV-to-sizing curve maps Average Daily Volume to (kelly_mult, cap_pct):

```python
adv_m = max(adv, 1.0) / 1_000_000
log_adv = log10(max(adv_m, 0.1))
frac = clip((log_adv + 1) / adv_scaling_divisor, 0.0, 1.0)
```

With default `adv_scaling_divisor=5.0`:

| ADV | adv_m | log10(adv_m) | (log+1)/5 = frac | kelly_mult | cap_pct |
|-----|-------|-------------|------------------|------------|---------|
| $100K | 0.1 | -1.0 | 0.0 | 0.15 | 0.02 |
| $1M | 1.0 | 0.0 | 0.20 | 0.22 | 0.04 |
| $10M | 10.0 | 1.0 | 0.40 | 0.29 | 0.06 |
| $100M | 100.0 | 2.0 | 0.60 | 0.36 | 0.08 |
| $1B | 1000.0 | 3.0 | 0.80 | 0.43 | 0.10 |
| $10B+ | 10000+ | 4.0+ | 1.0 | 0.50 | 0.12 |

**Key insight:** The ADV curve makes kelly_mult and cap_pct LIQUIDITY-ADAPTIVE.
Low-ADV tokens get smaller Kelly fractions (less aggressive) and tighter caps.

---

## 7. Common Sizing Configurations

### 7.1 Default Kelly (no overrides)
```json
{ "strategy_id": "s100", "market": "perp" }
```
ADV curve determines everything. Works for most strategies.

### 7.2 Fixed Kelly (bypass ADV curve for kelly_mult)
```json
{
  "strategy_id": "s100",
  "sizing_overrides": { "kelly_mult_override": 0.25 }
}
```
kelly_mult fixed at 0.25 regardless of ADV. cap_pct still from ADV curve.

### 7.3 Fixed Fractional (2.5% per position)
```json
{
  "strategy_id": "s100",
  "sizing_overrides": {
    "cap_pct_override": 0.025,
    "kelly_mult_override": 0.50
  }
}
```
Kelly will usually produce >2.5%, so cap_pct_override binds and every position is ~2.5% of equity.
(Actually the min of raw Kelly and cap is taken, so if vol is very low on some bars,
Kelly might still be below 2.5% and the position will be smaller.)

### 7.4 Even smaller: 40 positions at 2.5% each
```json
{
  "strategy_id": "s523",
  "max_positions": 40,
  "sizing_overrides": {
    "cap_pct_override": 0.025,
    "kelly_mult_override": 0.25
  }
}
```
Plus in PortfolioConfig:
```json
{
  "max_portfolio_positions": 40,
  "concentration_limit": 0.05
}
```

### 7.5 Ultra-small positions for breadth
```json
{
  "strategy_id": "s523",
  "max_positions": 60,
  "sizing_overrides": {
    "cap_pct_override": 0.02,
    "kelly_mult_scale": 0.5
  }
}
```

---

## 8. Known Gotchas

### 8.1 The Edge Cliff
`edge < edge_minimum` -> $0 position. No smooth transition. If your strategy uses
edge values near the threshold, small fluctuations can cause on/off behavior.
Fix: lower `edge_minimum` or ensure edge is well above threshold.

### 8.2 Vol Floor Amplification
When volatility drops below `vol_floor` (0.005), `vol_adj = target_vol / vol_floor = 4.0`.
This can produce large positions on very quiet bars. The cap_pct constraint catches most cases,
but be aware that low-vol regimes amplify raw Kelly.

### 8.3 ADV Cap Can Dominate
For tokens with ADV < $10M and adv_cap_pct=0.05, the hard cap is $500K. But positions
on a $200K portfolio are usually << $500K, so this rarely binds for small portfolios.
For large portfolios ($1M+), the ADV cap becomes the binding constraint on low-ADV tokens.

### 8.4 Concentration Limit Scales Down Silently
If you have multiple strategies trading the same token, concentration_limit=0.10
means total margin across all strategies for that token <= 10% of portfolio equity.
The second strategy's entry gets scaled down silently. Check `state.partial_fills`.

### 8.5 Raw Mode Uses Fixed Capital
In raw_mode, `strategy_equity = config.capital * spec.weight` (no PnL adjustment).
Positions don't grow or shrink with profits/losses. This is intentional for backtesting
to isolate signal quality from capital management.

### 8.6 cap_pct_override Rail
The safety rail for `cap_pct_override` is [0.01, 0.30]. You CANNOT set it below 1%
or above 30% via sizing_overrides. If you need outside these bounds, modify the engine.

### 8.7 Composite Safety Check
If you set kelly_mult_override=0.50 AND target_vol=0.05:
worst_case = 0.50 * (0.05/0.005) = 5.0 -- passes (< 8.0).
But kelly_mult_override=0.50 AND target_vol=0.05 AND kelly_mult_scale=2.0:
max_kelly = 0.50 (override ignores scale), worst = 0.50 * 10 = 5.0 -- still passes.
The check uses override directly when > 0, not override * scale.

---

## 9. Practical Recommendations: Fitting More Positions

### Goal: Go from 22 concurrent positions to 40

The key constraint is capital allocation. With $200K capital and 22 positions,
each position averages ~$9K. To fit 40 positions, each needs to average ~$5K.

**Approach 1: Lower cap_pct_override (SIMPLEST)**
```json
{
  "sizing_overrides": { "cap_pct_override": 0.025 }
}
```
This caps each position at 2.5% of equity = $5K on $200K. The raw Kelly term
will usually be higher (especially with high kelly_mult and edge), so cap binds.
`cap_pct_override: 0.025` is within safety rails [0.01, 0.30].

**Approach 2: Lower kelly_mult**
```json
{
  "sizing_overrides": {
    "kelly_mult_override": 0.10,
    "cap_pct_override": 0.025
  }
}
```
Both kelly_mult and cap are reduced. This makes the RAW Kelly smaller too,
so you get more consistent sizing near 2.5%.
Note: `kelly_mult_override: 0.10` is within safety rails [0.05, 0.50].

**Approach 3: Scale down existing Kelly**
```json
{
  "sizing_overrides": { "kelly_mult_scale": 0.5 }
}
```
Halves the ADV-curve kelly_mult (e.g., 0.36 -> 0.18 for $100M ADV tokens).
Keeps the ADV-adaptive behavior but makes everything smaller.
`kelly_mult_scale: 0.5` is the minimum safety rail value.

**Approach 4: max_trade_pct from strategy code**
In your strategy function:
```python
return StrategyResult(
    ...,
    max_trade_pct=0.025,  # 2.5% absolute cap
)
```
This is the simplest code change, but it's per-strategy and requires modifying
strategy files.

**Approach 5: Custom sizing model (most flexible)**
```python
class FixedFractionalSizing:
    def __init__(self, fraction=0.025):
        self.fraction = fraction

    def compute_size(self, strategy_equity, edge, edge_minimum, rolling_adv,
                     adv_cap_pct, volatility, vol_floor, **kw):
        if edge < edge_minimum:
            return 0.0
        pos = strategy_equity * self.fraction
        adv_cap = rolling_adv * adv_cap_pct
        return min(pos, adv_cap)

_SIZING_MODELS["fixed_frac"] = FixedFractionalSizing(0.025)
```
Then in config: `"sizing_model": "fixed_frac"`

### Also adjust portfolio constraints:
```json
{
  "max_portfolio_positions": 40,
  "concentration_limit": 0.05
}
```
- Raise max_portfolio_positions to 40 (or higher)
- Lower concentration_limit from 0.10 to 0.05 if you want more diversification
  (prevents any single token from consuming >5% of portfolio)

### Recommended configuration for 40 positions:
```json
{
  "max_portfolio_positions": 40,
  "concentration_limit": 0.05,
  "strategies": [
    {
      "strategy_id": "s523",
      "max_positions": 40,
      "market": "perp",
      "sizing_overrides": {
        "cap_pct_override": 0.025,
        "kelly_mult_scale": 0.5
      }
    }
  ]
}
```

This gives:
- Max 40 positions, each capped at 2.5% of equity ($5K on $200K)
- Kelly fraction halved so raw sizes are closer to the cap
- Max 5% concentration per token
- Total deployed: up to 100% of equity (40 * 2.5%)

---

## 10. File Reference

| File | What it contains |
|------|-----------------|
| `v4/sizing.py` | SizingModel protocol, KellySizing class, SlippageModel, registries |
| `v4/config.py` | SizingDefaults, SAFETY_RAILS, NON_OVERRIDABLE, resolve_sizing(), StrategySpec, PortfolioConfig |
| `v4/universe.py` | `adv_to_sizing()` -- the ADV-to-(kelly_mult, cap_pct) curve |
| `v4/simulator.py` | `_process_entries()` -- where compute_size is called + all constraints applied |
| `v4/engine.py` | StrategyResult dataclass -- what strategy functions return |
| `v4/paper_config.py` | PaperConfig + load_paper_config -- how JSON configs are parsed |
