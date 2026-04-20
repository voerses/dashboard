# M8 — Sizing Redesign

**Summary**: Replace v4's opaque 9-layer sizing pipeline with a transparent 2-intent system (`FIXED_FRACTION` + `FIXED_NOTIONAL`) plus 6 explicit engine clamps, giving strategies direct control over position sizing while the engine enforces risk limits.

---

## Meta-Rule: design over code (M5-M10 v5 rebuild mode)

This milestone operates under the **design over code** meta-rule, inherited from the M5-M10 collective v5 rebuild policy. The v5 rebuild is a redesign, not incremental maintenance. Legacy behaviors exposed by the rebuild are **fixed, not preserved**, unless a hard parity gate explicitly requires preservation (e.g. hourly-only trade archives bit-identical per AC14).

**Consequences**:
- Hourly-only strategies: bit-identical backtest preservation (AC14-style).
- Non-trivial migrations: shadow-replay validation (AC41-style, 4 ULP / 5 bps / 10 bps tolerances) with documented per-strategy deltas in `fleet_behavior_delta.md`.
- Determinism (AC24-style within-build): absolute.
- Sign-off thresholds for documented behavior changes: ≤20 bps auto / 20-100 bps quant / >100 bps design review.

**Sunset**: meta-rule applies to M5-M10 (the v5 rebuild). Post-M10 reverts to CLAUDE.md default (`code over specs`).

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
- `v5/sizing/intents.py` — `SizingIntent(str, Enum)` — two values:
  - `FIXED_FRACTION` = `"FIXED_FRACTION"` — "size this at X% of portfolio equity"
  - `FIXED_NOTIONAL` = `"FIXED_NOTIONAL"` — "size this at $X notional"
  - `RISK_PER_TRADE` is NOT an engine intent. Risk-budget sizing is a strategy-side transformation; see `risk_budget_fraction` helper below. Any `SizingRequest` with an unknown intent raises `ValueError`.
  - Pattern matches M5/M7 FIX vocabulary (`ExecType`, `OrderStatus`, `ContingencyType`, `TriggerType`, `LegStatus`, `LegFillPolicy` — all `(str, Enum)` for wire alignment + type hygiene). M7-shipped `SizingRequest.intent: Literal[...]` is promoted to `SizingIntent` enum; string values preserved so paper-state JSON roundtrip stays byte-identical. Three existing strategy ports (s513/s523c/s524m) get a 1-line update each.
- `v5/sizing/helpers.py` — Strategy-side helper library (pure functions, no engine dependency):
  - `vol_target_fraction(target_vol_annual, realized_vol, vol_cap=4.0) -> float` — volatility-targeted sizing
  - `kelly_fraction(edge, variance, kelly_mult=0.25) -> float` — Kelly criterion sizing (full Kelly never used; MacLean-Thorp-Ziemba default 0.25)
  - `risk_budget_fraction(risk_usd, stop_distance_bps, equity, leverage=1.0) -> float` — convert "risk $X given stop at Y bps" into a fraction-of-equity. Strategies that want risk-per-trade sizing compose this into a `SizingRequest(FIXED_FRACTION, fraction=…)`. Engine stays dumb about stop semantics (avoids coupling stop price into the RELEASE-time clamp pipeline).
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
- `SizingRequest` dataclass (promoted from M7 stub; extends existing fields):
  - `intent: SizingIntent` — `FIXED_FRACTION` or `FIXED_NOTIONAL` (enum)
  - `fraction_of_equity: float | None` — set for FIXED_FRACTION (mutually exclusive with `notional_usd`)
  - `notional_usd: float | None` — set for FIXED_NOTIONAL (mutually exclusive with `fraction_of_equity`)
  - `leverage: float = 1.0` — explicit, never inferred
  - `reduce_only: bool = False` — Binance `reduceOnly=true` param (NOT vanilla FIX 4.4; venue extension). Engine rejects orders that would flip direction; partial-fill handling returns `REJECTED_REDUCE_ONLY_OVERFILL` when `Leg.cum_qty < target_qty` and remaining position is smaller than order.
  - `margin_mode: Literal["isolated", "cross"] = "isolated"` — venue-level margin mode. Cross mode pools equity across positions → free-capital clamp reads portfolio unrealized PnL, not per-position (critical correctness concern flagged by Risk-review focus below).
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
- **Funding 8h cadence parity** (deferred to M9): current backtest accrues funding per-hour via `funding_1h = raw_8h_rate / 8` forward-filled across the 8h window (v4/live_fetcher.py:549-580); paper engine applies lump-sum at 00/08/16 UTC (v5/paper_engine.py:2875-2927). Aggregate totals match at each 8h boundary, but intra-window position opens/closes produce different funding charges across the two paths. Known asymmetry; keep as-is for M8. M9 cleanups can align backtest to lump-sum cadence if/when a paper/backtest parity test flags the drift. `funding_buffer_pct` in sizing clamps is unaffected — it's a forward-looking reserve, not the settlement mechanic.

### Strategy Rewrite Note

Strategy rewrite removes the v4 parity requirement. `composite_scaled_fraction` becomes an OPTIONAL convenience helper (not a parity shim). Strategies may use it for reference but are expected to use `vol_target_fraction` or `FIXED_FRACTION` with explicit fractions.

---

## Key Acceptance Criteria

1. **Two intents only (AC-Sz1)**: The engine accepts exactly `SizingIntent.FIXED_FRACTION` or `SizingIntent.FIXED_NOTIONAL`. No KELLY, VOL_TARGET, or RISK_PER_TRADE engine intents (those are strategy-side helpers per Scope). `SizingIntent` is a `(str, Enum)` matching M5/M7 FIX-vocabulary convention; `.value` preserves the string form for JSON roundtrip. A test verifies that a `SizingRequest` with a string literal not matching the enum raises `ValueError`.

2. **SizingRequest explicit fields (AC-Sz2)**: Every sizing request includes:
   - `intent: SizingIntent` (enum)
   - `fraction_of_equity: float | None` OR `notional_usd: float | None` (mutually exclusive)
   - `leverage: float = 1.0` (explicit, never inferred)
   - `reduce_only: bool = False` (Binance `reduceOnly` venue extension)
   - `margin_mode: Literal["isolated", "cross"] = "isolated"` (venue-level margin mode)
   No implicit derivation. A test creates a SizingRequest and verifies all fields are set. **Scalar-only invariant**: fields are pure scalars; the engine never reads bar-arrays. Strategies pre-index any per-bar inputs (size_multiplier, leverage arrays) at `generate()` time and bake scalars into the SizingRequest — matches M7 survey §4 per-bar-to-per-fill handoff already live in s513/s523c/s524m ports.

3. **6 clamps applied in order (AC-Sz3)**: Engine applies ADV cap → concentration → free capital → min size → liquidation distance → slippage. Each logs binding constraint.
   - **ADV cap**: `max_fill_notional = rolling_adv × config.adv_cap_pct`
   - **Concentration**: `max_per_symbol = strategy_equity × config.concentration_limit`
   - **Free capital**: `max_margin = state.available_margin`. Under `margin_mode="cross"` this reads portfolio-level unrealized PnL + equity pool; under `"isolated"` it reads per-position margin. Both paths MUST be tested — cross-margin is the common production bug site.
   - **Min position**: `config.min_position_usd` floor
   - **Liquidation distance**: rejects if `notional × leverage` would push liquidation price inside current stop distance. Uses Binance's tiered maintenance-margin schedule on perps (notional-bucketed) — test with a position spanning two tiers.
   - **Slippage**: `SqrtImpact(notional, adv)` — adjusts fill_price (not a reject; industry-standard square-root impact). Lifted verbatim from `v5/sizing.py:SqrtImpactSlippage`.

   A test with a position that triggers each clamp individually verifies the correct clamp fires. **Clamp entry point**: all 6 clamps fire inside `Order.release_atomic(available_capital_usd)` at `v5/orders.py:~1085` — the ONLY pipeline hook (no separate dispatch path). The RELEASE-time evaluation contract from M4 (survey §5) is preserved: clamps run at TRIGGERED → RELEASED transition, not at ARMED.

   **Multi-leg aggregation**: clamps respect `ContingencyType` rules from `v5/orders.py:compute_reserved_capital()` — OTOCO reserves `entry + max(siblings)`, OCO reserves `max`, OTO sums. Free-capital and concentration clamps aggregate across legs accordingly. ADV + slippage remain per-leg (siblings on different symbols).

   **Data source**: clamps read market state via an adapter that works against BOTH `PriceMonitor` (legacy, `config.use_data_engine=False`) AND `DataEngine` (M6, `use_data_engine=True`). Byte-identity between the two paths required — M7 AC-P3 24h shadow replay covers this post-M8.

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

11. **Clamp error containment (AC-Sz7, new)**: Any exception raised inside a clamp (stale data, division-by-zero, venue-schedule lookup failure) MUST be caught: the Order transitions to `state=REJECTED` with `reject_reason=f"clamp_error_{clamp_name}"` and the per-fill binding-log entry records the raised exception payload for PM debugging. The BarProcessor is never killed by a clamp exception. Matches M7 AC-S5 error-containment pattern — fail-contained for operational callbacks, never fail-fast on data/math errors.

12. **reduce_only overfill semantics (AC-Sz8, new)**: When `reduce_only=True` and the order quantity would open or flip direction (i.e. `|order.qty| > |current_position.qty|` OR signs disagree), the engine returns `state=REJECTED` with `reject_reason="reduce_only_overfill"`. Partial-fill case: `Leg.cum_qty < Leg.target_qty` with remaining position smaller than order → emits `ExecType.TRADE` for the portion that legitimately reduces + final `ExecType.REJECTED` (custom reason) for the overage. Mirrors Binance venue behavior; test against `{long_5x_reduce_7x, short_3_reduce_5, flip_direction_attempt}` cases.

13. **v5 paper-vs-backtest sizing parity (AC-Sz9, new)**: The same strategy + same input bars + same seed must produce **bit-identical** `v5/logs/sizing_fills.jsonl` entries across backtest (`v5.simulator.run_backtest`) and paper (`v5.paper_engine` with deterministic TestClock). M4's AC18 (hourly trade archives bit-identical) and AC41 (sub-hourly within 4 ULP / 5 bps / 10 bps tolerances) cover the final trade outcomes, but M8 introduces a new observability surface (the binding-log) that can drift even when final fills match — different clamp fire-order would produce same fills but different `binding_constraint` records. This AC closes that gap.

    **Test shape**:
    - Build a deliberate parity fixture (`v5/tests/fixtures/m8_sizing_parity/`) with one token + a strategy designed to hit all 6 clamps at known bars (e.g., an ADV-saturating FIXED_FRACTION request, a concentration-exceeding one, etc.).
    - Run the fixture through `v5.simulator.run_backtest` (seed=42) → emit backtest `sizing_fills.jsonl`.
    - Run the same fixture through `v5.paper_engine` under a deterministic `TestClock` + replayable data feed → emit paper `sizing_fills.jsonl`.
    - Assert: both files byte-identical (M3 tolerance constants from `v5/tests/shadow_replay_tolerances.py` applied per-field for float columns: `filled_margin`, `filled_notional`, `fill_price`, `slippage_bps`, all clamp values). Non-float columns (`binding_constraint`, `intent`, `symbol`, `strategy_id`) must match exactly.
    - Runtime bound: 1 week of hourly bars (~168 bars). Longer windows covered by AC-P3 24h shadow replay and M10 full-cycle parity.

    **Why this matters**: M8 clamps read market state via an adapter over `PriceMonitor` (flag=OFF) OR `DataEngine` (flag=ON). Any drift in the adapter (ADV lookup rounding, mark-price tz-handling, free-margin cross-mode formula) shows up here first. Without this AC a subtle clamp-order bug can ship silently — fills match, logs don't, PM debugging fails.

---

## Dependencies

| Milestone | Relationship |
|-----------|-------------|
| **M7** (Strategy API) | **Required** — Strategies declare `SizingRequest` in their `generate()` return value (via `TokenSignal.sizing`) |
| **M4** (BarProcessor) | **Required** — BarProcessor Phase 3 passes SizingRequest to the sizing engine |
| **M1** (v5 Fork) | **Required** — v5 namespace |

---

## Time Estimate

**60-85 hours** (updated from initial 20-30h → 50-70h after drift review → 60-85h after path-I AC-S10 expansion. M5/M6/M7 precedent was 80h/200h/150h actualized; M8 scope is comparable.)

- ~4h: SizingIntent enum + SizingRequest schema extension (promotion from M7 stub)
- ~8h: 6 engine clamps implementation + ordering + `release_atomic` integration
- ~4h: Multi-leg OTOCO aggregation across clamps
- ~4h: Per-fill binding-constraint JSONL log + schema
- ~4h: `composite_scaled_fraction` helper + parity testing
- ~3h: `vol_target_fraction` + `kelly_fraction` + `risk_budget_fraction` helpers
- ~8h: Delete v4 pipeline + config cleanup (30+ files cascade per drift doc)
- ~15-20h: AC-S10 backtest-vs-backtest parity closure (path I — real OHLCV loader 6h + WalkForwardRunner → v5.simulator wiring 6h + fixture regen + test alignment 4h)
- ~5h: clamp error containment + reduce_only overfill semantics
- ~6h: Tests (unit + parity + margin-mode cross path)
- ~3h: Documentation
- ~3-4h: `CapitalAllocationPolicy` Protocol hook + `SharedPoolPolicy` default + `AllocationState` TypedDict (M9-forward-compat; ships default in M8, policies in M9+)
- ~8-15h: Reviewer loop (FIX + Quant primary rounds + focused Risk pass on liquidation-distance + margin-mode + reduce_only clamps at round ~3)

**No scope cap** — per user directive 2026-04-20, push through to full acceptance regardless of hours. Reviewer-loop rounds continue until both reviewers return PASS verdicts (M7 pattern: 10 rounds to SHIP).

---

## Validation Gate

- Strategy authors validate their own sizing is correct via backtest comparison
- No v4 parity requirement for engine clamps since strategies are rewritten
- `composite_scaled_fraction()` is an optional convenience helper, not a parity shim
- **AC-S10 parity gate**: 5 xfailed s524m metric-parity tests flip to real PASS (not vacuous XPASS) within 0.5% of v4 reference, via **backtest-vs-backtest** comparison (path I) — `WalkForwardRunner.run()` drives `v5.simulator.run_backtest` on real OHLCV for v4's 206-token universe over a single WF fold (Q-DEC4 2025). If the xfails flip to PASS but assertions fail, STOP and investigate (structural divergence, not a clamp bug).
- **Margin-mode cross path tested**: free-capital clamp under `margin_mode="cross"` with multi-position portfolio unrealized PnL ≠ per-position sum (catches the cross-margin formula bug common in sizing engines).
- **Reduce-only overfill tested**: 3 scenarios verified per AC-Sz8.
- **Reviewer sign-off**: FIX + Quant primary review rounds both return PASS verdict. One focused Risk-architect subagent pass at ~round 3 scoped to liquidation-distance clamp + margin_mode + reduce_only semantics (4-6h reviewer time).
- All v5 tests pass (current baseline: 1526 passed / 7 xfailed / 2 xpassed — M8 drops the 5 s524m xfails to real PASS → baseline becomes 1531 passed / 2 xfailed / 2 xpassed).
- No config references to deleted v4 sizing parameters.

---

## M4 Impact (minor — sizing-at-trigger contract)

M4 introduces `PendingEntry.sizing_ctx: SizingContext` with an explicit "sizing recomputed at trigger (RELEASE), not at arm (INITIALIZED)" semantic. This brief requires one note:

- **Sizing-at-trigger contract** (new M8 note): When a `PendingEntry` transitions from ARMED → TRIGGERED → RELEASED, the sizing function re-runs with current market context (price, volatility, ADV, free capital). The `sizing_ctx` captured at arm time is the *inputs*; the output is computed at release. Matches alpha-preservation best practice (Cartea & Wang SSRN 3439440) and preserves current v5 behavior where portfolio constraints (portfolio_limit, strategy_limit, max_concurrent, concentration, capital+funding buffer) are re-evaluated at trigger.
- **Implication for sizing API**: M8's sizing function signature must accept `(sizing_ctx: SizingContext, market_state: MarketState) -> SizingResult` — not a stateful function that binds market state at construction.
- **No new ACs required** — existing M8 scope is compatible. Just add a contract note in the brief to prevent design drift.

---

## M7 Impact (carry-over — AC-S10 parity trip-wire + fraction bound)

M7 shipped 10 review rounds deep with two reviewer-SHIP verdicts. A handful of items were deferred to M8 because they depend on the v5 simulator bring-up that lives in this milestone's scope.

### In-scope additions

1. **AC-S10 metric-parity trip-wire closure** (`v5/tests/test_m7_s524m_parity.py`) — **backtest-vs-backtest parity** (path I)

   **Target**: run v5 S524M through the **real v5 backtest simulator** (`v5.simulator.run_backtest`, not the Wave-B scaffold), producing a metrics dict from the resulting equity curve, and compare to the v4 reference fixture `v5/tests/fixtures/m7_s524m_parity/v4_reference_metrics.json` within 0.5%. NOT paper-replay parity (paper is live-tied), NOT decision-level-only parity (fills + PnL needed to catch real sizing bugs).

   **Current blockers**:
   - `WalkForwardRunner.run()` at `v5/validation.py:886` returns `metrics={}` (Wave-B scaffold) — drives `strategy.generate(ctx, bar_idx)` against synthetic RNG-generated OHLCV from `UniverseContext.build_test`. No real data, no simulator, no equity curve.
   - Fixture was produced with `--max-portfolio-positions 30` on v4's full 206-token perp universe; test passes `tokens=['BTC','ETH','SOL']`. Structurally incomparable for a cross-sectional rank strategy.

   **M8 work to close** (split into three concrete sub-items, ~15-20h total):

   a. **Real OHLCV loader** (~6h): `v5/validation.py` gains `load_oos_window(tokens, window="Q-DEC4-2025") → dict[token, pd.DataFrame]`. Reads the existing parquet files in `data/perp/binance/1h_ohlcv/*.csv` / `data/perp/binance/1h.parquet`. Reuses v4's data conventions (no reimplementation). Returns 206-token × Q-DEC4 OHLCV + funding arrays.

   b. **WalkForwardRunner → v5.simulator wiring** (~6h): `WalkForwardRunner.run()` drives `v5.simulator.run_backtest(strategy, data_bundle, portfolio_config, seed)` (the real simulator used by `v5/portfolio_backtest.py`), collects the equity curve from the resulting state, and computes `{total_return, sharpe, sortino, calmar, max_drawdown}` via existing `v5/report.py` metrics (no reimplementation). Result surfaced via `WalkForwardResult.metrics: dict`.

   c. **Fixture regen + test alignment** (~4h): regenerate `v4_reference_metrics.json` if needed from v4 over the same 206-token / Q-DEC4 window using the same `seed=42 / --max-portfolio-positions 30 / --capital 100000 / --market perp / --conviction-mode ranked` CLI. Update the test to pass the 206-token universe (loaded via step a). Keep the scope bound to single-fold Q-DEC4 2025 to avoid 4-year-run reviewer churn.

   **Acceptance**: the 5 strict xfail-marked tests flip to real PASS (not vacuous XPASS) within 0.5% tolerance when M8 ships. If they flip but assertions fail, STOP and investigate — structural divergence, not a clamp bug.

   **Why backtest-vs-backtest (not paper-vs-backtest)**: paper runs are wall-clock-tied and non-replayable in the deterministic sense AC-S10 needs. Backtest-vs-backtest gives the 0.5% precision signal; paper parity is a separate concern covered by M7's AC-P3 24h shadow-replay gate (ohlcv_divergence_count == 0) which stays as-is.

2. **TokenSignal `fraction_of_equity` bound** (new AC in M8 scope)
   - Currently no global invariant that `sum(fractions) * leverage ≤ 1.0` across concurrent positions. Round-3 Quant MINOR-7 flagged this as a sizing-layer responsibility.
   - Acceptance: a new AC requiring the engine to reject or scale when aggregate `fraction_of_equity * leverage` exceeds 1.0 across the active position book. Default behavior: reject the marginal signal; escape hatch `allow_over_leveraged=True` for strategy explicit opt-in.

### Notes
- These do NOT change existing M8 ACs — both items slot into the existing sizing API surface (`SizingRequest.intent/fraction_of_equity/leverage`).
- The AC-S10 fixture regen should run AFTER the v5 simulator is proven deterministic, so the fixture is stable across M8 rebuilds.

---

## Process & Reviewer Decisions (locked 2026-04-20)

Decisions derived from M1-M7 survey + quant-expert review (`.specs/active/m8-sizing-redesign/quant-scope-answers.md`). Locked before Phase 1 so the brief review can focus on value-alignment, not mechanics.

**Scope decisions:**
1. **`RISK_PER_TRADE` intent**: DROPPED from engine. Replaced by `risk_budget_fraction()` strategy-side helper in `v5/sizing/helpers.py`. Engine speaks only `FIXED_FRACTION` + `FIXED_NOTIONAL`. Rationale: pushing stop semantics into release-time clamp pipeline couples unrelated concerns and has been a bug magnet in production sizing engines (quant expert HIGH confidence).
2. **`SizingIntent` type**: Promoted from M7's `Literal[...]` to `class SizingIntent(str, Enum)` — matches M5/M7 FIX vocabulary convention. `.value` preserves string form for JSON roundtrip. 3 strategy ports each get a 1-line update (s513/s523c/s524m). Quant expert HIGH confidence.
3. **AC-S10 fixture**: option (b) — widen to v4 206-token universe for a single Q-DEC4 fold. 8h budgeted. Option (a) 3-token regen actively harmful for rank-based strategies.
4. **No scope cap** (user directive 2026-04-20): push through to full acceptance. Reviewer loop continues until both reviewers return PASS (M7 pattern: 10 rounds).

**Reviewer configuration:**
- **Primary reviewers**: FIX architect + Quant architect, iterating each round until both return PASS.
- **Focused Risk pass**: ~round 3 (mid-implementation), spawn a dedicated Risk-architect subagent scoped ONLY to:
  - AC-Sz3 clause 3 (free capital under cross-margin)
  - AC-Sz3 clause 5 (liquidation distance vs Binance tiered maintenance-margin)
  - AC-Sz8 (reduce_only overfill semantics vs Binance behavior)
  - 4-6h reviewer time; findings fold into round 4
- Full 3-reviewer-on-everything setup rejected as ceremony; focused pass catches the real risk bugs (cross-margin formula, tiered liquidation math, reduceOnly partial-fill) without 15-20h of redundant review.

**Scope ranking (for reference, not a cut list since no scope cap is active)**:

| Rank | Item | Category |
|------|------|----------|
| 1 | `SizingRequest` schema (2 intents, 5 fields) | CORE |
| 2 | 6 clamps: ADV / concentration / free capital / min size / liq distance / slippage | CORE |
| 3 | Per-fill binding log (AC-Sz5) | CORE (without it M8 is a black box — defeats the whole point) |
| 4 | `Order.release_atomic` integration | CORE |
| 5 | Clamp error containment (AC-Sz7) | CORE |
| 6 | v4 pipeline DELETE (AC-Sz6, 30+ file cascade) | CORE |
| 7 | reduce_only overfill semantics (AC-Sz8) | CORE |
| 8 | Multi-leg OTOCO clamp aggregation | IMPORTANT |
| 9 | AC-S10 fixture closure (option b) | IMPORTANT |
| 10 | `vol_target_fraction` helper | IMPORTANT |
| 11 | `risk_budget_fraction` helper | IMPORTANT |
| 12 | `composite_scaled_fraction` v4-curve helper | IMPORTANT |
| 13 | `kelly_fraction` textbook helper | ADDITIONAL |
| 14 | `max_sizing_equity` optional 7th clamp | ADDITIONAL |
| 15 | `funding_buffer_pct` field | ADDITIONAL |

All 15 items ship per user directive. Ranking preserved in case a scope-cap gets re-introduced mid-flight.
