# M8 Brief — Drift & Review Notes (pre-Phase-1 alignment)

**Status**: Draft. These are implementer-surfaced findings from cross-referencing the current M8 brief against the M1-M7 shipped state + current v5 code. Goal: flag drift before we start Phase 1 specify review so the brief reflects reality.

---

## TL;DR

M8 brief is **structurally sound** — 10 ACs, 6 clamps, 2 intents, helper library, v4 pipeline deletion — but was written at M5/M6-era and has drifted:

- **3 spec-vs-reality drifts** (M7 already delivered parts of what the brief says it will do)
- **5 design gaps** where M1-M7 invariants aren't wired into the ACs but must be
- **2 time-estimate corrections** based on M7 reviewer churn pattern
- **1 scope question** the M7 carry-over forces

Recommending brief amendments before Phase 1 review; nothing here blocks the Phase 1 value-first questioning.

---

## A. Spec-vs-Reality Drift (brief claims vs actual v5)

### A1. SizingRequest already exists with 3 fields + `RISK_PER_TRADE` intent

**Brief says** (AC-Sz2): add 5 fields total: `intent`, `fraction_of_equity | notional_usd`, `leverage=1.0`, `reduce_only=False`, `margin_mode`.

**Current** (`v5/strategy_api.py:70-78`):
```python
@dataclass
class SizingRequest:
    intent: Literal["FIXED_FRACTION", "FIXED_NOTIONAL", "RISK_PER_TRADE"] = "FIXED_FRACTION"
    fraction_of_equity: float = 0.0
    leverage: float = 1.0
    # M8 extends: reduce_only, margin_mode, notional_usd, risk_budget
```

Drift items:
1. M7 already shipped `intent` as `Literal` (not a separate `SizingIntent` enum). Brief AC-Sz1 calls for a `SizingIntent` type — decide: keep `Literal` (cheaper, M7-shipped) or promote to `Enum`?
2. **Brief says only 2 intents (FIXED_FRACTION, FIXED_NOTIONAL) — but M7 shipped 3 including `RISK_PER_TRADE`**. AC-Sz1 says "unknown intent raises ValueError". M8 must decide: delete RISK_PER_TRADE (minor breakage in strategy ports — none of s513/s523c/s524m use it), or add RISK_PER_TRADE to the design and give it a clamp.
3. M7 ports pass `intent="FIXED_FRACTION"` as string. If M8 promotes to enum, we break the call sites — design-over-code says fix it.

**Recommendation**: promote `SizingIntent` to a real `str, Enum` (matches `ExecType` style from M5/M7). Drop `RISK_PER_TRADE` unless the brief adds an explicit clamp for it (not in current scope). Update 3 strategy ports.

### A2. M7 strategy ports already pre-index `size_multiplier` / `leverage` per brief invariant

**Brief M4 Impact** says sizing function signature is `(sizing_ctx: SizingContext, market_state: MarketState) -> SizingResult` — NOT stateful.

**Current** (`v5/strategies/s524m_v5.py:244-256`): ports already bake per-bar scalars into SizingRequest at generate time:
```python
size_multiplier = float(cfg.get("size_multiplier", 1.0))
leverage = float(cfg.get("leverage_override", self.LEVERAGE))
signals[token] = TokenSignal(
    ...
    sizing=SizingRequest(
        intent="FIXED_FRACTION",
        fraction_of_equity=size_multiplier / self.MAX_POSITIONS_HINT,
        leverage=leverage,
    ),
)
```

This is the **per-bar-to-per-fill handoff** invariant (survey §4). The brief doesn't make it explicit but the architecture relies on it. M8 must document that the sizing engine **never** sees arrays — only the scalar `SizingRequest` baked at `generate()` time.

**Recommendation**: add an AC explicitly stating "SizingRequest fields are scalar; engine never reads bar-arrays; strategies pre-index at generate() time" (currently implicit).

### A3. Two-sided sizing: FIX `ExecInst=E` is listed but FIX tag is wrong

**Brief AC-Sz2** says `reduce_only: bool = False` maps to FIX `ExecInst=E`.

**Actual FIX**: `ExecInst(18)=E` is "Reduce-only" only in Binance's documented extension. Vanilla FIX 4.4/5.0 has no standard `reduce_only` flag. Binance uses `reduceOnly=true` as a separate param (not ExecInst).

**Recommendation**: update the FIX annotation to "Binance `reduceOnly` param (no vanilla FIX 4.4 tag)" OR cite `ExecInst(18)=E` as "venue extension" explicitly. Minor — not a blocker but worth aligning with M5/M7 FIX precision.

---

## B. Design Gaps (M1-M7 invariants that must be honored)

### B1. Order.state machine: sizing is evaluated at **RELEASE**, not at trigger (survey §5)

Brief AC-Sz3 lists 6 clamps but doesn't specify **when** they fire. M5/M7 locked in:
- **ARMED → TRIGGERED**: predicate hit; no constraint check
- **TRIGGERED → RELEASED**: `Order.release_atomic()` or `compute_reserved_capital()` fires; **sizing + all 6 clamps evaluated here**
- **RELEASED → FILLED/PARTIALLY_FILLED**: fill recorded

The brief's M4 Impact section touches this but doesn't tie it to the specific `Order.release_atomic()` hook. **M8 must plumb the 6-clamp pipeline into `Order.release_atomic()` at `v5/orders.py:1078-1099`**, not build a separate dispatch.

**Recommendation**: add an AC citing `Order.release_atomic(available_capital_usd)` as the clamp entry point, and clarify that the pipeline produces either a new Order with `state=RELEASED` + adjusted `filled_qty/leaves_qty` or `state=REJECTED` with `reject_reason="clamp_X"`.

### B2. Multi-leg OTOCO capital aggregation (M7 round-2 MAJOR fix, not in brief)

M7 reviewer round 2 added `ContingencyType.OTOCO` with `entry_margin + max(sibling_margins)` aggregation in `compute_reserved_capital()`. The brief's 6 clamps are all stated per-single-order. For a bracket Order:
- **Concentration** and **free capital** clamps must aggregate across legs via the `ContingencyType` rule (survey §2)
- **ADV cap** is per-symbol per-leg (siblings on different symbols aggregate separately)
- **Slippage** is per-leg fill

**Recommendation**: add a sub-AC under AC-Sz3: "Clamp aggregation across multi-leg Orders follows `ContingencyType` rules from `v5/orders.py:compute_reserved_capital` — OTOCO reserves entry + max(siblings); OCO reserves max; default sums."

### B3. Clamp must work with M7 `run_paper_multi` feature flag (survey M6/M7)

The brief references `config.use_data_engine` which is **already shipped** (M6, flipped OFF by default per M7 Task 15 deferred). M8's clamps need to read market data (ADV, mark price, free margin) — must work both paths:
- `use_data_engine=False` → reads from `PriceMonitor` (legacy)
- `use_data_engine=True` → reads from `DataEngine.venue(...)` (M6)

**Recommendation**: add a note in scope "clamps MUST work against both `PriceMonitor` (flag=OFF) and `DataEngine` (flag=ON) via a thin `MarketState` adapter; byte-identity between paths required for the AC-P3 24h shadow replay" — this is a design note, not a new AC.

### B4. AC-S5 error containment: clamp errors must not crash the engine (survey §16)

M7 locked in error containment: `generate/check_scale/check_exit/filter_entry` are logged + treated as no-op on exception. The brief's clamp pipeline has no equivalent — if the ADV cap clamp raises (e.g., stale data, division by zero), should the Order go to REJECTED with `reject_reason="clamp_error"` or fall through?

**Recommendation**: add AC-Sz7: "Clamp exceptions → Order.state=REJECTED with `reject_reason='clamp_error_<name>'`; never crash the BarProcessor. Per-fill log records the raised exception for PM debugging."

### B5. Integration with AC-S10 parity trip-wire (M7 carry-over, brief §M7 Impact)

The brief's §M7 Impact section correctly lists AC-S10 trip-wire but **doesn't specify** what "v5 simulator bring-up" means concretely. Options:
- **(a) Full bar-loader + simulator + metrics** — 20h+ on its own (this is effectively AC-E* from M10's scope)
- **(b) Thin bar-fixture + real metric computation only** — enough to exercise SizingRequest → clamps → fills path

The brief implies (a) but budgets 0h. Picking (b) keeps M8 within 20-30h.

**Recommendation**: explicitly scope AC-S10 closure to "(b) — use existing `v5/tests/fixtures/m7_s524m_parity/v4_reference_metrics.json`, load a minimal OHLCV fixture for BTC/ETH/SOL over Q-DEC4, wire WF runner to a real metrics module. Full v5 simulator remains M10 scope."

---

## C. Time Estimate Correction

### C1. Original 20-30h is optimistic per M5/M6/M7 precedent

M5 actual: 80h+ (101 tests, 15 ACs, multi-leg Order). M6: 200h+ (175 tests, 20 ACs). M7: 150h+ (25 tasks, 23 ACs, 10 review rounds).

M8 has 10 ACs + 6 clamps + helper library + v4 deletion + M7 carry-over. Realistic budget: **50-70h** with reviewer loop.

**Recommendation**: update the time estimate to 50-70h with a note that the M5-M10 reviewer loop adds 20-40% over the implementation-only estimate.

### C2. Reviewer choice

M7 used FIX architect + Quant architect. M8 is sizing/risk — add a **Risk architect** perspective (liquidation-distance math, margin semantics, cross-margin vs isolated correctness). Cost: +15-20h over the 2-reviewer baseline.

**Recommendation**: 3-reviewer setup for M8: FIX + Quant + Risk. Review rounds target 4-6 (vs M7's 10); if round 6 still produces NEEDS_ATTENTION, escalate to a single-pass user-review instead of a 7th round.

---

## D. Scope Question (blocking — needs user decision)

### D1. Fixture universe for AC-S10 closure — (a) 3-token regen, or (b) full 206-token?

M7 carry-over §1 flags this as "M8 must decide." Tradeoff:
- **(a) Regenerate fixture with 3-token universe** (BTC/ETH/SOL only):
  - 2h work
  - Preserves the existing 0.5% tolerance
  - **Doesn't test s524m's real behavior** — s524m is a portfolio-rank strategy that needs 100+ tokens to rank against. A 3-token run is structurally incomparable to v4's 206-token run.
  - The xfails WILL flip to XPASS and pass — but the parity signal is vacuous
- **(b) Widen test to pass v4's 206-token universe**:
  - 6-10h work (build token universe loader, feed to WalkForwardRunner)
  - Real parity signal
  - Catches actual sizing bugs in s524m_v5 port

**Recommendation**: **(b)**. A vacuous XPASS is worse than a red xfail — it hides real parity bugs. Budget 8h for this closure and call it done when the 5 tests genuinely pass.

---

## E. Other findings (non-blocking)

- **`v5/sizing.py` current state**: 150 lines, `KellySizing` + `SqrtImpactSlippage`. `KellySizing` is explicitly marked for M8 replacement. `SqrtImpactSlippage` matches AC-Sz3 clause 6 — preserve (or copy verbatim into `v5/sizing/slippage.py`).
- **v4 pipeline deletion (AC-Sz6)**: surface greps show `adv_cap_pct`, `concentration_limit`, `min_position_usd`, `max_sizing_equity`, `funding_buffer_pct` are all still referenced in `v5/config.py` + `v5/simulator.py` + 20+ test files. Deletion cascade is larger than the brief implies — the 6 "DELETE" bullets touch ~30 files. Budget: 8h for the cleanup pass.
- **`Fill` dataclass missing FIX tags** (M9 carry-over): not strictly M8 scope but the binding-log AC-Sz5 schema would benefit from populating Symbol(55), OrderQty(38), OrdType(40) now — saves re-touching the binding log in M9.

---

## Proposed brief amendments (after your review)

1. **AC-Sz1**: decide `Literal` vs `Enum` for `SizingIntent`; remove `RISK_PER_TRADE` unless added as 7th clamp.
2. **AC-Sz2**: add per-bar-to-per-fill scalar-baking invariant (B1).
3. **AC-Sz3**: cite `Order.release_atomic` as clamp entry point (B1); add multi-leg aggregation sub-AC (B2); add AC-S5-style error containment (B4).
4. **AC-Sz5**: Fill-tag carry-over integration note (M9 tags landed here for free).
5. **AC-Sz7 (new)**: clamp error containment.
6. **M7 Impact**: pick (b) fixture approach explicitly; update wording.
7. **Time estimate**: 50-70h with 3-reviewer loop.
8. **FIX annotation fix** on `reduce_only` (A3).

---

## Questions for you before Phase 1

1. **`RISK_PER_TRADE` intent**: drop it (cleanest) or add a 7th clamp (e.g., "risk_usd = |entry - stop| × qty"; reject if exceeds `risk_budget`)?
2. **`SizingIntent` Enum vs Literal**: matching M7 `ExecType` style (Enum) makes FIX integration cleaner. Downside: 3 port files need 1-line updates.
3. **AC-S10 fixture approach**: (a) 3-token regen or (b) 206-token widen? I recommend (b).
4. **Reviewer mix**: FIX + Quant + Risk (3-reviewer) or just FIX + Quant (2)?
5. **Scope cap**: if we hit the 70h ceiling mid-implementation, stop-and-descope (e.g., defer `kelly_fraction` helper to M9) or push through?
