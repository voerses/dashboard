# M8 — Sizing Redesign

**Summary**: Replace v4's opaque 9-layer sizing pipeline with a transparent 2-intent system (`FIXED_FRACTION` + `FIXED_NOTIONAL`) plus 6 explicit engine clamps, giving strategies direct control over position sizing while the engine enforces risk limits.

---

## Problem

v4's sizing pipeline is a 9-parameter black box that no one fully understands:

1. **9 interacting shape parameters**: `adv_to_sizing` curve, `kelly_mult_floor/range/override/scale`, `cap_pct_floor/range/override/scale`, `adv_scaling_divisor`, `size_multiplier`, `cap_multiplier`, `max_trade_pct` — these interact in non-obvious ways, making it impossible to reason about why a specific position got a specific size.

2. **No per-fill transparency**: When a position is sized smaller than requested, there's no log of which constraint was the binding one (was it ADV? concentration? free capital? liquidation distance?).

3. **Strategy can't express intent**: Strategies set a "conviction score" (0-1 float) that gets mapped through the opaque pipeline. They can't say "I want 2% of equity" or "$5,000 notional" directly.

4. **Leverage is implicit**: The pipeline computes leverage indirectly through margin calculations. There's no explicit leverage parameter.

5. **reduce_only and margin_mode are afterthoughts**: Bolted on as special cases rather than first-class fields.

---

## Scope

### In scope

- `v5/sizing/__init__.py` — package root
- `v5/sizing/intents.py` — Two sizing intents:
  - `FIXED_FRACTION(fraction=0.02)` — "size this at 2% of portfolio equity"
  - `FIXED_NOTIONAL(usd=5000)` — "size this at $5,000 notional"
- `v5/sizing/helpers.py` — Strategy-side helper library (pure functions, no engine dependency):
  - `vol_target_fraction(target_vol_annual, realized_vol, vol_cap=4.0) -> float` — volatility-targeted sizing
  - `kelly_fraction(edge, variance, kelly_mult=0.25) -> float` — Kelly criterion sizing (full Kelly never used; MacLean-Thorp-Ziemba default 0.25)
  - `composite_scaled_fraction(base_fraction, composite_score, adv, config=V4_DEFAULTS) -> float` — mirrors v4 s524-family ADV curve for migration. Uses config-object pattern:
    ```python
    @dataclass(frozen=True)
    class ADVScalingConfig:
        kelly_mult_floor: float = 0.15
        kelly_mult_range: float = 0.35
        cap_pct_floor: float = 0.02
        cap_pct_range: float = 0.10
        adv_scaling_divisor: float = 5.0

    V4_DEFAULTS = ADVScalingConfig()
    ```
    Uses v4's `kelly_mult * edge` formula (NOT textbook `edge/variance`)
- `SizingRequest` dataclass:
  - `intent: SizingIntent` — `FIXED_FRACTION` or `FIXED_NOTIONAL`
  - `fraction_of_equity: float | None` — set for FIXED_FRACTION (mutually exclusive with `notional_usd`)
  - `notional_usd: float | None` — set for FIXED_NOTIONAL (mutually exclusive with `fraction_of_equity`)
  - `leverage: float = 1.0` — explicit, never inferred
  - `reduce_only: bool = False` — FIX `ExecInst=E`
  - `margin_mode: Literal["isolated", "cross"] = "isolated"` — FIX-aligned
- 6 explicit engine clamps (applied in order, logged):
  1. **ADV cap**: position notional <= X% of 24h ADV
  2. **Concentration**: position notional <= X% of portfolio equity
  3. **Free capital**: position notional <= available margin
  4. **Min size**: position notional >= exchange minimum (else skip)
  5. **Liquidation distance**: ensure liquidation price is >= N% away
  6. **Slippage**: `SqrtImpact(notional, adv)` — adjusts fill_price (not a reject; industry-standard square-root impact model)
- Per-fill binding-constraint logging: each fill emits structured log with request -> each clamp value -> binding clamp -> filled size (PM-debuggable)
- Delete v4's opaque sizing pipeline (adv_to_sizing curve, 9 shape params, conviction-to-size mapping)

### Out of scope

- Portfolio-level risk limits (M9 — MaxGrossExposure, DrawdownThrottle)
- Margin calculation engine (use exchange-reported values)
- Dynamic leverage adjustment based on market conditions
- Multi-account sizing
- Strategy migration (only s524m via composite_scaled_fraction as proof; others migrate when user decides)

### Strategy Rewrite Note

Strategy rewrite removes the v4 parity requirement. `composite_scaled_fraction` becomes an OPTIONAL convenience helper (not a parity shim). Strategies may use it for reference but are expected to use `vol_target_fraction` or `FIXED_FRACTION` with explicit fractions.

---

## Key Acceptance Criteria

1. **Two intents only (AC-Sz1)**: The engine accepts exactly `FIXED_FRACTION` or `FIXED_NOTIONAL` as sizing input. No other sizing mode exists (no KELLY or VOL_TARGET as engine intents — those are strategy-side helpers). A test verifies that an unknown intent raises ValueError.

2. **SizingRequest explicit fields (AC-Sz2)**: Every sizing request includes:
   - `intent: SizingIntent`
   - `fraction_of_equity: float | None` OR `notional_usd: float | None` (mutually exclusive)
   - `leverage: float = 1.0` (explicit, never inferred)
   - `reduce_only: bool = False` (FIX `ExecInst=E`)
   - `margin_mode: Literal["isolated", "cross"] = "isolated"` (FIX-aligned)
   No implicit derivation. A test creates a SizingRequest and verifies all fields are set.

3. **6 clamps applied in order (AC-Sz3)**: Engine applies ADV cap -> concentration -> free capital -> min size -> liquidation distance -> slippage. Each logs binding constraint.
   - **ADV cap**: `max_fill_notional = rolling_adv x config.adv_cap_pct`
   - **Concentration**: `max_per_symbol = strategy_equity x config.concentration_limit`
   - **Free capital**: `max_margin = state.available_margin`
   - **Min position**: `config.min_position_usd` floor
   - **Liquidation distance**: rejects if `notional x leverage` would push liquidation price inside current stop distance
   - **Slippage**: `SqrtImpact(notional, adv)` — adjusts fill_price (not a reject)
   A test with a position that triggers each clamp individually verifies the correct clamp fires.

4. **Per-fill binding log (AC-Sz5)**: Every fill emits structured log with: `request -> each clamp value -> binding clamp -> filled size`. Format is PM-debuggable. JSON lines to `v5/logs/sizing_fills.jsonl` with schema: `{timestamp, symbol, strategy_id, intent, requested_fraction, requested_notional, leverage, clamp_values: {adv_cap, concentration, free_capital, min_size, liq_distance}, binding_constraint, filled_margin, filled_notional, fill_price, slippage_bps}`. A test triggers the concentration clamp and verifies the log entry contains `binding_constraint`, requested size, and final size.

5. **composite_scaled_fraction helper (AC-Sz4)**: Full signature: `composite_scaled_fraction(base_fraction, composite_score, adv, config=V4_DEFAULTS) -> float` — mirrors v4 ADV curve using config-object pattern (`ADVScalingConfig` dataclass). Uses v4's `kelly_mult * edge` formula (NOT textbook `edge/variance`). This is an OPTIONAL convenience helper — strategies may use it for reference but are expected to use `vol_target_fraction` or `FIXED_FRACTION` with explicit fractions. A parameterized test covers known inputs/outputs to verify formula correctness.

6. **v4 pipeline DELETED (AC-Sz6)**: The following are removed from v5 — no code, no config references:
   - ADV-indexed Kelly curve (`adv_to_sizing()`)
   - Silent `vol_adj` 4x multiplier
   - `unrealized_pnl_floor` (loss-masking anti-pattern, per AC-R3)
   - `adv_sizing_enabled` sqrt mult
   - `size_multiplier` / `cap_multiplier` / `max_trade_pct`
   - `dd_scaling` (unused)
   - `pump_filter_*` (unused)
   - `kelly_mult_floor/range/override/scale`, `cap_pct_floor/range/override/scale`, `adv_scaling_divisor`

7. **Helper library (AC-Sz4)**: Pure functions with no engine dependency:
   - `vol_target_fraction(target_vol_annual, realized_vol, vol_cap=4.0) -> float`
   - `kelly_fraction(edge, variance, kelly_mult=0.25) -> float` (MacLean-Thorp-Ziemba default 0.25). **Note**: `kelly_fraction` uses textbook Kelly `edge / variance * kelly_mult` which is DIFFERENT from v4's `kelly_mult * edge` (no variance). `composite_scaled_fraction` uses v4 formula; `kelly_fraction` uses textbook. Both are documented.
   Each has a unit test with known inputs/outputs.

8. **No sizing floor (AC-R3)**: `unrealized_pnl_floor=0.85` DELETED. Strategies wanting drawdown-aware sizing write explicit logic: `fraction *= max(0, 1 - dd/dd_limit)`. No silent loss-masking.

9. **Dropped v4 checks disposition**: Since strategies are being rewritten (no parity requirement):
   - `edge_minimum`: strategy self-gates (don't emit signal if edge < threshold)
   - `spot_max_equity_pct`: strategy declares `fraction_of_equity` respecting spot cap itself
   - `max_sizing_equity`: add as optional 7th engine clamp on PortfolioConfig (keep for portfolio-level safety)
   - `unrealized_pnl_floor`: DELETED (confirmed)
   - `funding_buffer_pct`: add as optional field on PortfolioConfig that reduces available margin — keep for safety

10. **Sizing validation**: Strategy authors validate their own sizing is correct via backtest comparison. No v4 parity requirement since strategies are rewritten.

---

## Dependencies

| Milestone | Relationship |
|-----------|-------------|
| **M7** (Strategy API) | **Required** — Strategies declare `SizingRequest` in their `generate()` return value (via `TokenSignal.sizing`) |
| **M4** (BarProcessor) | **Required** — BarProcessor Phase 3 passes SizingRequest to the sizing engine |
| **M1** (v5 Fork) | **Required** — v5 namespace |

---

## Time Estimate

**20-30 hours**

- ~4h: SizingIntent + SizingRequest design
- ~5h: 6 engine clamps implementation + ordering
- ~4h: Per-fill binding-constraint logging
- ~4h: composite_scaled_fraction helper + parity testing
- ~3h: vol_target_fraction + kelly_fraction helpers
- ~3h: Delete v4 pipeline + config cleanup
- ~4h: Tests (unit + parity)
- ~2h: Documentation

---

## Validation Gate

- Strategy authors validate their own sizing is correct via backtest comparison
- No v4 parity requirement since strategies are rewritten
- `composite_scaled_fraction()` is an optional convenience helper, not a parity shim
- All v5 tests pass
- No config references to deleted v4 sizing parameters
