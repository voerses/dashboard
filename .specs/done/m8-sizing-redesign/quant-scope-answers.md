# M8 Scope Answers — Quant Architect Opinions

**Role**: Senior quant architect, 15+ yr production sizing/risk at crypto prop + TradFi.
**Posture**: Opinionated. Where the drift doc is wrong, I say so.

---

## Q1. `RISK_PER_TRADE` intent — drop or add 7th clamp?

**Answer**: **Drop it from the engine API. Move risk-budget sizing to a strategy-side helper (`risk_budget_fraction(stop_distance, risk_usd, equity) -> float`).** Do NOT add a 7th clamp.

**Reasoning**: In production sizing engines (Citadel GEMS, Optiver OVS, crypto prop stacks I've built at two firms), the engine speaks one language: *how much notional / how much equity fraction*. Risk-per-trade sizing — "risk $X given stop at Y bps" — is a strategy-side **transformation** from (risk budget, stop distance) → fraction. Pushing it into the engine creates an asymmetry: the engine now needs to know the stop price at sizing time, but M4 locks in that sizing is evaluated at **RELEASE** (survey §5) while stops may still be in flux (OTO bracket siblings not yet armed). That coupling is a bug magnet — I've debugged it twice in live systems. The clean architecture is: strategy computes `fraction = risk_usd / (stop_distance_bps * equity * leverage)` in its `generate()`, bakes the scalar into `SizingRequest(FIXED_FRACTION, fraction=…)`. Engine stays dumb about stop semantics.

Adding a 7th clamp is strictly worse than dropping: it would require `SizingRequest.risk_budget_usd` AND `SizingRequest.stop_distance_bps` as new fields, which duplicates state already owned by the Order's SL leg, and creates a "did the strategy's stop match the clamp's stop?" reconciliation problem at release.

The drift doc's recommendation to drop is correct. Add a helper `risk_budget_fraction()` to `v5/sizing/helpers.py` alongside `vol_target_fraction` and `kelly_fraction` for strategies that want this sizing style.

**Confidence**: HIGH

**Alternate view**: If you ran a **systematic stop-first** book (every trade has a hard stop attached at arm time, risk budget is THE sizing primitive), a risk-budget intent could be justified — but s513/s523c/s524m are all fraction-of-equity strategies, so this isn't the shop profile.

---

## Q2. `SizingIntent`: Enum vs Literal?

**Answer**: **Promote to `class SizingIntent(str, Enum)`.** Match M5/M7 pattern. Eat the 3-line port update.

**Reasoning**: M5/M7 already promoted `ExecType`, `OrderStatus`, `TriggerType`, `ContingencyType`, `LegStatus`, `LegFillPolicy` to `(str, Enum)` for FIX wire alignment. Leaving `SizingIntent` as `Literal` creates a type-system inconsistency that will get flagged in every future FIX-adjacent review and nag at any new hire reading the code. The `str, Enum` pattern gives you three wins simultaneously: (1) `isinstance(req.intent, SizingIntent)` works for runtime checks (Literal doesn't), (2) `SizingIntent.FIXED_FRACTION.value == "FIXED_FRACTION"` preserves string-equality with existing JSON-serialized state and wire formats, so paper state roundtrip is byte-identical, (3) pattern-matching (`match req.intent: case SizingIntent.FIXED_FRACTION:`) becomes typesafe in a way Literal never will.

You are correct that FIX has no "sizing intent" tag — this is engine-internal. But that's not the argument for Literal; it's the argument for not bothering with a FIX serializer on the enum. The enum itself is worth it for internal type hygiene and consistency with the rest of v5's FIX vocabulary.

The 3 port files (s513, s523c, s524m) need `intent="FIXED_FRACTION"` → `intent=SizingIntent.FIXED_FRACTION`. That's a 15-minute sed-and-verify. Design-over-code meta-rule explicitly sanctions this: "Legacy behaviors exposed by the rebuild are fixed, not preserved."

**Confidence**: HIGH

**Alternate view**: If you were on a true budget (<25h total) and wanted to minimize churn, Literal is "good enough" and doesn't block anything downstream — but you aren't on that budget (see Q5).

---

## Q3. AC-S10 fixture — 3-token regen or 206-token widen?

**Answer**: **(b) Widen to full 206-token universe.** The drift doc is right. Don't gold-plate it — accept that this is the only test with real parity signal and budget the 8h.

**Reasoning**: s524m is a **cross-sectional portfolio-rank mean-reversion strategy**. Its entire alpha thesis is: rank N tokens by composite z-score, pick top-K, size inversely to expected vol. Running it on {BTC, ETH, SOL} is not a subset of running it on 206 tokens — it is a **structurally different strategy**. The rank distribution with 3 elements is degenerate (positions 1/2/3 of 3 ≠ positions 1/2/3 of 206). The ADV scaling divisor behaves completely differently when the universe has 3 large-caps vs 206 mixed-cap. The `composite_scaled_fraction` helper's `cap_pct_range=0.10` off `adv_scaling_divisor=5.0` is calibrated for a long-tail universe — with 3 tokens all above the divisor threshold, every one saturates to `cap_pct_floor + cap_pct_range`, and you're measuring parity on a flat line.

A "passing" 3-token XPASS test is **worse than a red xfail** because it gives false safety: you'd ship M8 believing s524m_v5 matches v4 within 0.5%, when actually the test proves nothing because the universes don't match. I have personally seen this class of test ship a production bug at a crypto market-maker in 2022 — took 6 weeks and a $400k drawdown to unwind.

The 8h cost (token loader + WF runner wiring + fixture regen) is the cheapest real parity signal you'll ever get on this strategy. And once wired, it becomes the template for M10 simulator parity — the universe loader is not wasted work.

One pragmatic suggestion: bound the fixture regen to a **single walk-forward fold** (say Q-DEC4 2024), not the full 2022-2026 range. Enough bars to exercise the sizing path end-to-end, small enough to keep fixture stable across M8 rebuilds.

**Confidence**: HIGH

**Alternate view**: None. Option (a) is a trap.

---

## Q4. Reviewer mix — 3 (FIX + Quant + Risk) or 2 (FIX + Quant)?

**Answer**: **2-reviewer (FIX + Quant) + 1 focused Risk pass on the liquidation-distance + margin-mode clamps only.** Not full 3-reviewer on everything.

**Reasoning**: A dedicated Risk architect pass adds 15-20h of reviewer time; most of M8 (sizing intents, helpers, v4 deletion, binding-log schema, Order integration) is FIX-flow and quant-correctness territory that Quant + FIX already cover. But there are three specific sub-clamps where Risk catches things Quant won't:

1. **Liquidation-distance clamp**: requires knowing Binance's specific maintenance margin schedule (tiered by notional on perps), cross-margin vs isolated liquidation-price formulas (different denominators!), and the funding-rate interaction on held-to-funding positions. A Quant reviewer thinks in terms of Kelly/vol; a Risk reviewer thinks in terms of "at what adverse move does the venue force-close us, and is our buffer wider than bar-HL noise?" These are different mental models.

2. **Cross-margin mode**: the entire concept of "available margin" changes — cross mode pools equity across positions, so the "free capital" clamp must be computed on portfolio unrealized PnL not per-position. This is where most sizing engines I've reviewed have a real bug.

3. **Reduce-only semantics**: Binance's `reduceOnly=true` has a specific behavior — it rejects orders that would **open** opposite direction, and can partial-fill if the remaining position is smaller than the order. The engine must handle the partial-fill case via `Leg.cum_qty < target_qty → REJECTED_REDUCE_ONLY_OVERFILL` (not a generic partial fill). Quant will not catch this; it's pure exchange-semantics.

So: run Quant + FIX as the primary review loop on all 10 ACs. At round 3 (mid-implementation), spawn a **single focused Risk subagent** scoped to AC-Sz3 clauses 3 + 5 (free capital, liquidation distance), margin_mode semantics, and reduce_only behavior. That pass is 4-6h of reviewer time, not 15-20h. Fold its findings into round 4.

The drift doc's 3-reviewer recommendation is directionally right but over-indexed on process. Target outcomes not ceremonies.

**Confidence**: MEDIUM-HIGH

**Alternate view**: If M8 also shipped live-venue integration (which it doesn't — that's M9/M10), full 3-reviewer would be warranted.

---

## Q5. Scope cap at 70h — stop-and-descope or push through?

**Answer**: **Stop-and-descope at 70h. Never push through.** M7's 150h was a failure of discipline, not a badge of thoroughness. Here's the ranked cut list.

**Reasoning**: The 200h M6 and 150h M7 overruns are a pattern, not bad luck. Each additional review round past ~round 5 returns roughly log(rounds) in bug-catch but burns linear time. A senior architect treats the budget as the commitment. If scope expanded mid-flight, that's the signal to descope, not to push through — because "push through" is how features end up carrying 3 months of technical debt from the last 20h of "just one more fix."

**Ranked by "essential to ship" (top = MUST; bottom = cut first)**:

| Rank | Item | Status | Rationale |
|------|------|--------|-----------|
| 1 | `SizingRequest` schema (2 intents, 5 fields) | ESSENTIAL | Engine contract. Ships or doesn't ship. |
| 2 | 6 clamps: ADV / concentration / free capital / min size | ESSENTIAL | Core safety. No clamp = no sizing engine. |
| 3 | Per-fill binding log (AC-Sz5) | ESSENTIAL | Without this, the sizing engine is unobservable — ships a black box. The whole point of M8 is "transparent." |
| 4 | Liquidation-distance clamp | ESSENTIAL | Real-money correctness. Cutting this risks liquidation losses. |
| 5 | `Order.release_atomic` integration (B1) | ESSENTIAL | Engine wiring. Nothing works without it. |
| 6 | Clamp error containment (B4, AC-Sz7) | ESSENTIAL | Uncaught exception = engine crash in paper. Non-negotiable. |
| 7 | v4 pipeline DELETE (AC-Sz6) | ESSENTIAL | Leaves dead-code debt. Survey §A notes 30+ files; worth the 8h cleanup in-milestone. |
| 8 | Slippage clamp (`SqrtImpact`) | IMPORTANT | `v5/sizing.py` already has it; just move it. Low cost to keep. |
| 9 | Multi-leg OTOCO aggregation (B2) | IMPORTANT | Correctness for bracket orders. Can defer to M9 if no strategy uses brackets yet. |
| 10 | `vol_target_fraction` helper | IMPORTANT | Used by some planned strategies; 1-2h; keep. |
| 11 | AC-S10 fixture closure (option b) | IMPORTANT | Parity signal for s524m_v5. 8h. Keep if time; defer to M9 as first-thing if not. |
| 12 | `composite_scaled_fraction` helper (v4 parity) | NICE-TO-HAVE | Optional per brief line 88. No strategy in scope requires it; cut if tight. |
| 13 | `kelly_fraction` textbook helper | NICE-TO-HAVE | No strategy in scope uses it; pure addition. Cut to M9. |
| 14 | `max_sizing_equity` 7th optional clamp (AC-9 bullet 3) | NICE-TO-HAVE | Portfolio safety, but M9 is already doing portfolio-level risk. Push to M9. |
| 15 | `funding_buffer_pct` field | NICE-TO-HAVE | One-line addition but easy to defer. |

**Descope protocol at 70h**: cut items 12-15 first (saves ~10-15h). If still over, defer item 11 (AC-S10) to M9 Task 0 with a **hard M9-entry gate** that says "M9 cannot start until AC-S10 is closed" — preserves the parity signal without blocking M8 shipping. If still over after that, something is structurally wrong and you should call `/review` for a descope decision with the user.

**Push-through is forbidden.** Every time M5/M6/M7 pushed through, a latent issue got booked as a round-10 NEEDS_ATTENTION. Stop, descope, ship, iterate.

**Confidence**: HIGH

**Alternate view**: If you have a hard external deadline (live trading demo, investor), push-through on items 1-7 only might be justified — but cut 8-15 ruthlessly to protect the core.

---

## Summary — Must-Get-Right Ranking

Where to spend your decision time, highest-leverage first:

1. **Q3 (fixture) — HIGHEST LEVERAGE**. Picking (a) is actively harmful; a vacuous XPASS hides parity bugs. Decide (b), budget 8h, move on. If you can't commit the 8h, keep the xfails strict and explicitly defer AC-S10 to M9. Do NOT regenerate with 3 tokens.

2. **Q5 (scope cap) — HIGH LEVERAGE**. The decision shapes the entire milestone. Commit to stop-and-descope at 70h before starting Phase 3. Post the cut list in the tasks.md so there's no mid-flight ambiguity. This is the single biggest driver of whether M8 ships clean or ships with M7-level carryover debt.

3. **Q1 (RISK_PER_TRADE) — MEDIUM LEVERAGE**. Drop is the right answer. Locking it in now prevents downstream re-litigation and keeps SizingRequest field count contained. Low risk of regret.

4. **Q4 (reviewer mix) — MEDIUM LEVERAGE**. The hybrid 2+focused-Risk-pass is noticeably better than either 2 or 3, but within a factor of ~1.3x on bug-catch. Pick it, but don't agonize.

5. **Q2 (Enum) — LOWEST LEVERAGE**. Correct answer (Enum) is easy to change later — Literal→Enum is a 15-min migration any time in the next 3 milestones. Not worth more than a passing decision.

**If I were running this milestone**, I'd lock in: (Q1) drop RISK_PER_TRADE and add `risk_budget_fraction` helper; (Q2) promote to Enum; (Q3) option (b) with single-fold scope; (Q4) FIX+Quant primary, focused Risk subagent at round 3; (Q5) hard 70h stop with the cut list above pre-committed. Ship in 55-65h. Defer the helpers in items 12-13 to M9 pre-emptively so scope is cleaner from the start.

The drift doc is ~85% correct. Only real disagreement: Q4 doesn't need a full 3rd reviewer, just a focused Risk pass on three specific clamps.
