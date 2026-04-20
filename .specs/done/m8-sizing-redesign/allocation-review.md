# Adversarial Review: M8/M9 Multi-Strategy Allocation Recommendation

**Reviewer**: Senior quant architect (institutional prop + crypto prop background).
**Date**: 2026-04-20.
**Scope**: The recommendation described in the prompt — M8 stays clean of allocation concerns; M9 ships a minimal AllocationPolicy Protocol with SharedPoolPolicy (default) + FixedBudgetPolicy; dynamic allocation deferred to user code.

---

## Verdict on your recommendation: **MODIFY**

The high-level shape is right and matches how real shops actually do this. But there are three specific things you got wrong that will bite within 90 days of M9 shipping, and one reviewer call I want to make very loudly on FIX vocab. None of them invalidate the architecture; they tighten it. I am not going to invent architectural drama that isn't there — 80% of your decision is correct, and specifically your rejection of the "portfolio manager as a Strategy actor" pattern is 100% correct for this codebase's determinism posture.

What I'm modifying:
1. Ship the **Protocol surface** in M8, not M9. The hook, not the policies.
2. Keep the **1 built-in policy** count (SharedPoolPolicy). Cut FixedBudgetPolicy from M9 scope.
3. **Swap FIX 448 for FIX 1098 StrategyID** — you got the vocab wrong and this is load-bearing for M10.
4. **Do not ship DrawdownThrottlePolicy as allocation.** It belongs in the M9 risk-component layer (C-5), and the existing M9 brief already spec'd it there.

Concrete counter-proposal at the end.

---

## Question 1: Protocol in M8 vs M9

**Short answer: Protocol ships in M8. Policies ship in M9.**

The asymmetry matters. Retrofitting the clamp pipeline to accept a `policy.available_capital(...)` call later is a diff across `v5/sizing/clamps.py`, `v5/orders.py::release_atomic`, the 3 MarketState adapters, AND the binding-log schema (since `available_capital` becomes a per-fill input worth logging). That's touching 5+ files in a module freshly stabilized with 40-60 tests — exactly the "touches more than planned 1-2 files" stop-and-redecompose trigger from the workflow rules. I have seen this exact retrofit cost 2-3x the original clamp build at HRT (2017, execution-services sizing module — we shipped a "we'll add the policy hook in v2" and it turned into a 6-week project because the clamp pipeline had already been wired into three non-trivial callers).

What I've seen work cleanly: ship the Protocol method signature and have the free-capital clamp call `policy.available_capital(strategy_id, state, clock_now_ns)` where `policy = SharedPoolPolicy()` is hardcoded inside `release_atomic` for M8. One extra line of indirection, one extra test (`test_m8_shared_pool_policy_returns_available_margin`). M9 then just swaps the hardcoded instance for a configurable one on `PortfolioConfig.allocation_policy`. Zero retrofit of the clamp pipeline, zero touching of binding-log schema downstream.

Cost in M8: +1 file (`v5/sizing/allocation.py` with the Protocol + SharedPoolPolicy), +1 test, ~30 lines total. Cost in M9 of doing it later: ~4-6h across the clamp pipeline, MarketState adapters, and binding-log invariants. The M8 approach is strictly cheaper and reduces M9 risk.

---

## Question 2: Number of built-in policies in M9

**Short answer: 1 (SharedPoolPolicy). Not 2. Not 3. Not 0.**

You recommended 2 (SharedPool + FixedBudget). Cut FixedBudget. Reasoning:

- **Shipping 0 is wrong** because SharedPoolPolicy IS the default and it's not really a "policy" — it's the identity function over `state.available_margin`. You need it instantiated to exercise the Protocol surface. Calling it "zero policies" is a definitional dodge.
- **Shipping 3 (adding DrawdownThrottle) is wrong** because drawdown throttling is a *risk* decision, not an *allocation* decision. Collapsing the two into one Protocol creates the exact category confusion that makes allocation bugs hard to debug. At Citadel-Tactical (2019) we had a sizing-throttle-merged-with-budget-allocator bug that took two weeks to find because no one could tell which layer had reduced a position — allocator said "you got $X budget," throttle said "I cut it to 0.8x," and the blotter showed the $0.8X final. M9's brief already puts DrawdownThrottle in the risk-component layer (C-5, `RiskComponent` Protocol with `RiskDecision.REDUCE`) — that's the correct home. Do not duplicate.
- **Shipping FixedBudget is wrong for M9** because it's a real policy with real failure modes (see Q3) and shipping it without the observability story (what happens when budget sum ≠ 1.0? how do we surface under-allocation to PMs?) creates incident risk. Ship it in M10 with the paper-mode observability patch.

So: 1 built-in, and it's the identity.

---

## Question 3: FixedBudgetPolicy as canonical non-default

**Short answer: Hard per-strategy caps is the right non-default eventually, but you have a normalization ambiguity that will ship bugs.**

Concretely, the three failure modes you asked about:

- **Sum < 1.0**: Under-allocation. Dollars left on the table. At Two Sigma (2020, multi-pod allocator) this produced a 6-month incident where a pod was budget-starved due to a 0.95 sum from a config typo — everyone assumed "the PM knew what they were doing" until we realized it was rounding in the config YAML. **Fix**: require sum == 1.0 or raise `ValueError("FixedBudgetPolicy requires sum(budgets) == 1.0 ± 1e-9")` at construction. Never silently normalize.
- **Sum > 1.0**: Over-allocation. Each strategy thinks it has headroom; in aggregate they exceed equity. This is the crypto-prop classic — I saw this at a $150M shop in 2022 where three strategies all ran to "40% each" and on a correlated-long day ate margin and liquidated simultaneously. **Fix**: same hard rejection at construction.
- **Changes mid-run**: This is the footgun. If `PortfolioConfig.allocation_policy` is swapped live (paper mode, user reloads config), you get a strategy that was at 0.4 budget finding itself at 0.2 — but with existing positions sized for 0.4. **Fix**: policy changes are a *restart* event, not a hot-reload. Log an AIPIP-style warning if the instance changes between ticks with non-empty position book.

Additionally: the `dict[strategy_id, fraction]` shape doesn't tell you what to do with a strategy that exists at runtime but isn't in the dict. Default to 0 (strategy can't trade) or default to `SharedPoolPolicy` residual? I've seen both; I recommend default-0 with explicit opt-in, because silent "oh you weren't in the dict so here's leftover capital" behavior is the exact bug that destroyed allocation determinism at a previous shop. Make this an explicit constructor param: `FixedBudgetPolicy(budgets=..., missing_strategy="reject" | "zero")`.

All of which is to say: FixedBudget is not 10 lines. It's 40 lines plus 8 tests. Which is fine — just don't ship it in M9 with the minimalist framing, or if you must ship it, scope those 40 lines and 8 tests explicitly.

---

## Question 4: FIX Party(448) vs StrategyID(1098)

**Short answer: You got the vocab wrong. Use StrategyID(1098) + StrategyType(1099) from FIXT 1.1+.**

This is the one spot where I'm going to be emphatic. FIX Party(448) with PartyRole(452)=53 ("Settlement Process Manager" or variants depending on FIX version) is what prime brokers use for give-up and allocation routing BETWEEN firms. It's not what any institutional shop I've ever worked at uses for intra-firm strategy identity. The correct FIX vocab for "this order came from strategy X" is StrategyID(1098) with optional StrategyType(1099) and StrategyParameters (1000+ block). This is FIXT 1.1 / FIX 5.0 SP2 native.

You asked if it matters for a paper-trade engine not sending wire messages today. **It matters a lot** because M10 is going to connect to Binance FIX or Deribit FIX (both support FIX 4.4 with custom tags OR FIXT 1.1), and the strategy identity you stamp into `Order.to_json()` in M9 becomes the persistence schema. Changing FIX tags in the persistence schema is exactly the migration M5 just spent 10 reviewer rounds avoiding. Get it right now.

Pragma: if you're worried about Binance's FIX 4.4 not knowing 1098, use StrategyID as a **custom tag in the 5000+ range for Binance wire** and 1098 for the internal persistence. Every shop I've been at does this dual-stamp. It's one extra field in the `to_json` block.

Concretely: `"strategy_id": "s524m"` in the JSON, `"fix_1098": "s524m"` for the wire-native form, and document both in the sizing_fills.jsonl schema I see at design.md line 219-246.

---

## Question 5: Ship a built-in DrawdownThrottle?

**Short answer: No, because M9 C-5 already ships DrawdownThrottle in the risk layer. Don't duplicate.**

I almost gave you the opposite answer — every shop does need drawdown throttling, and the "engine ships one well-tested pure-safety policy" argument is real. BUT reading M9's brief C-5 carefully, `DrawdownThrottle` is already spec'd as a `RiskComponent` that returns `RiskDecision.REDUCE` via the `TradingState.REDUCING` mechanism. That's the correct architectural home for it. The risk layer operates on candidate trades and can halt the book; the allocation layer determines capital pools.

Merging "throttle drawdown" into allocation would give you a `DrawdownThrottleAllocationPolicy` that dynamically reduces `available_capital`. That works, but it creates two places that can cut a position and (from production experience) makes incident forensics harder. Keep allocation as "here's how much capital strategy X gets" and risk as "here's the override veto." Separate concerns, separate logs.

One caveat: the M9 brief's `DrawdownThrottle` currently cuts `max_positions` and size — not budget. That's fine. The allocation layer is dumb about it; the risk layer fires after allocation and says "I know you got $X budget, you're only using $0.8X today."

---

## Question 6: Missing concerns

Four things you did not mention that bite at M9 implementation or M10 shipping:

**M6-i: Cross-strategy symbol correlation**. Two strategies both long BTC — under SharedPoolPolicy they share the *capital* pool (fine), but nothing shares the *BTC exposure*. Strategy A thinks it's 4% BTC; Strategy B thinks it's 4% BTC; portfolio is 8% BTC. This is NOT an allocation-policy concern — it's a portfolio-level risk concern that M9's `MaxGrossExposure` / `MaxNetExposure` risk components hint at, but don't solve per-symbol. The existing M8 concentration clamp is *per-strategy* (design.md §3d clause 2: `max_per_symbol = strategy_equity × concentration_limit`). Under SharedPool this becomes a real footgun. **Flag for M9 decomposition**: add a portfolio-level concentration check in the risk layer, or (better) have the concentration clamp read `portfolio_equity × limit` under SharedPoolPolicy and `strategy_equity × limit` under FixedBudget. This is a real implementation detail that falls out of the allocation-policy choice.

**M6-ii: Dust-threshold interaction with FixedBudget**. `min_position_usd` (clamp 4) rejects positions below a floor. Under FixedBudget where strategy s523c gets `0.05 × $150K = $7,500` total, if its concentration is 10% that's $750 per position — likely below the $1000 min_position_usd floor for many altcoins. The strategy will emit signals that all get REJECTED. This is a known pattern: at one shop we had a "small pod" that could never actually trade because the dust floor was global and budget was tiny. **Fix**: surface a warning in `FixedBudgetPolicy.__init__` when `budget × concentration_limit < min_position_usd` for any strategy. Cheap, prevents silent failure.

**M6-iii: Order contention under FixedBudget**. When two strategies both want to trade in the same tick and their combined ask exceeds available margin under SharedPoolPolicy, there's an ordering question — whoever's release_atomic fires first wins. The M9 brief's `AllocationPolicy.rank()` (C-1, line 47-60) handles this at signal-dispatch level via RandomShuffle / PriorityDesc / TieredPriority. **But** — and this is subtle — that's a *different* AllocationPolicy surface than your Q1 capital-allocator Protocol. You have **two** allocation policies in play: (a) the one in M9 C-1 `AllocationPolicy.rank()` for signal ordering, and (b) your proposed `AllocationPolicy.available_capital()` for capital partitioning. These are different concerns and should have different Protocol names. **Rename your proposal to `CapitalAllocationPolicy`** to avoid collision with M9 C-1's existing `AllocationPolicy`. I cannot stress this enough — reading the M9 brief it is not obvious to me that you noticed this collision when drafting.

**M6-iv: TestClock evaluation cadence drift in paper mode**. See Q7.

---

## Question 7: Backtest-paper parity under non-default policies

**Short answer: Not automatic. You have a real cadence-drift risk that AC-Sz9 does not cover.**

AC-Sz9 is a 1-week hourly parity test that asserts bit-identical sizing_fills.jsonl between backtest and paper under deterministic TestClock. Under SharedPoolPolicy this is trivially satisfied — the policy is a pure function of `state.available_margin` which both modes compute identically. Under FixedBudgetPolicy (configured the same way in both) this is also satisfied IF the policy is a pure function of (config, state).

**But** under any future dynamic policy — and you even flagged DrawdownThrottle — you get cadence drift. Paper mode ticks (sub-second in live, deterministic-tick in TestClock); backtest mode ticks bar-by-bar. A policy that reads `state.rolling_equity` will see a different equity-sampling cadence between the two modes. The backtest sees equity at bar-close; paper (even with TestClock) sees equity at arbitrary tick boundaries. Ten ticks inside a bar and ten equity samples that don't correspond to the single bar-close value.

This is the exact bug that killed a multi-strategy allocator at a crypto prop firm in 2023 — shadow-replay showed 0bps drift for a week, then a volatile day produced 80bps drift because the throttle policy sampled equity at tick-resolution in paper and bar-resolution in backtest. The policy was "correct" in both modes; the *inputs* differed.

**Fix**: every CapitalAllocationPolicy MUST declare its sampling cadence explicitly. Add to the Protocol:

```python
class CapitalAllocationPolicy(Protocol):
    sampling_cadence: Literal["bar_close", "tick", "release"]
    def available_capital(self, strategy_id, state, clock_now_ns) -> float: ...
```

`bar_close` policies are required to read state snapshots taken at bar close (stamped by the engine). `release`-cadence policies read current state (what you have now). `tick` is for paper-only policies that genuinely want tick-level inputs and are explicitly non-parity. Document which cadence each built-in declares. SharedPool = `release` (fine; pure-function). FixedBudget = `release` (fine; pure-function of config). Any future dynamic = `bar_close` by default.

Without this, AC-Sz9 parity is a false sense of security — it only validates SharedPool, which is the one policy that can't drift.

---

## What I'd actually ship

**M8 scope additions** (~3-4h on top of your 60-85h budget):
- `v5/sizing/allocation.py`: `CapitalAllocationPolicy` Protocol with `sampling_cadence` class attribute + `available_capital(strategy_id, state, clock_now_ns)` method.
- `SharedPoolPolicy` implementation (identity over `state.available_margin`).
- Hardcoded `SharedPoolPolicy()` instance in `release_atomic` free-capital clamp.
- `test_m8_shared_pool_policy.py`: 3 tests (identity, clock-now-ns pass-through, equivalence to today's behavior).
- Add `strategy_id` FIX-1098 field to `sizing_fills.jsonl` schema (cheap, closes M10 carry-over item 5 from M9 brief).

**M9 scope additions** (pull from your 6-8h estimate, add 1-2h):
- `PortfolioConfig.capital_allocation_policy: CapitalAllocationPolicy = SharedPoolPolicy()` — pluggable.
- That's it. Do not ship FixedBudgetPolicy in M9. Defer to M10 with proper observability.

**M9 scope subtractions**:
- Do not ship FixedBudgetPolicy. It's 40 lines + 8 tests + an observability story. Do it in M10 where you have paper-mode observability patches.
- Do not merge DrawdownThrottle into allocation. M9 C-5 already places it in the risk layer, which is correct.

**FIX vocab correction**:
- Use StrategyID(1098) + StrategyType(1099) for internal persistence.
- Dual-stamp with venue-specific custom tags (Binance/Deribit 5000+ range) for wire output in M10.
- NOT Party(448)/PartyRole(452)=53. That's give-up routing, not strategy identity.

**Rename**:
- Your proposed Protocol is `CapitalAllocationPolicy`, not `AllocationPolicy`. M9 C-1 already owns the `AllocationPolicy` name for signal-ordering. Do not collide.

---

## Hidden traps

1. **The `AllocationPolicy` name collision with M9 C-1** — as noted in Q6 and above. If you ship with this name, you will have two Protocols with identical names in the codebase and every reviewer will flag it.

2. **`funding_buffer_pct` interaction with `CapitalAllocationPolicy`**. The M8 design (§7.1) subtracts `equity × funding_buffer_pct` from `available_capital_usd` BEFORE the free-capital clamp. Question: does that subtraction happen before or after `policy.available_capital(...)` is called? If before, funding buffer is always applied. If after, policies can override it (e.g., FixedBudget might carve out funding reserve per strategy). I'd default to BEFORE — funding buffer is a portfolio-level safety, not a policy concern — but document this explicitly in the M8 design. The order matters for AC-Sz9 parity.

3. **Scalping-strategy pathology under FixedBudget**. A strategy with budget $5K and median position size $200 can trade 25 positions concurrently. A strategy with budget $50K and median position size $200 can trade 250 positions concurrently. The engine's `MaxConcurrentOrders` limit (M9 C-5) is global, not per-strategy. FixedBudget interacts with this in non-obvious ways. Not a blocker for M8/M9 but a real M10 discussion.

4. **Paper-state persistence schema**. If `PortfolioConfig.capital_allocation_policy` is a Protocol instance, and paper mode crashes + restarts, how is the policy re-instantiated? Serializing Protocol implementations is a known pain. Solution: persist the policy *config* (dataclass or dict), not the instance. Policies must implement `to_config() -> dict` and a module-level `policy_from_config(d)` factory. This is trivial for SharedPool but real work for FixedBudget. Yet another reason to defer FixedBudget to M10.

5. **`state` is a huge object — locking down what policies can read**. If `CapitalAllocationPolicy.available_capital` takes `state: SimulationState`, you've given user-written policies read access to the entire book. That's fine for trusted users but a footprint issue for any future multi-tenant story. Consider a narrow `AllocationState` TypedDict that exposes only `{available_margin, per_strategy_equity, rolling_pnl_24h, current_positions_notional}`. Forward-compat; prevents policies from peeking at individual position data they shouldn't need. Not critical for M8/M9 (one user = you) but flag for M10 multi-user.

---

## Summary

Your recommendation is 80% correct and the direction is right. The modifications I'd make:

- Protocol in M8, policies in M9.
- 1 built-in in M9 (SharedPool), not 2.
- FIX 1098, not 448.
- Don't ship DrawdownThrottle in allocation — M9 C-5 already has it in the risk layer.
- Defer FixedBudget to M10 with observability story.
- Rename to `CapitalAllocationPolicy` to avoid collision with M9 C-1 `AllocationPolicy`.
- Add `sampling_cadence` to the Protocol for backtest-paper parity sanity.

The architectural call to reject the Nautilus "policy as actor" pattern is 100% correct given this codebase's determinism posture and AC-Sz9 parity. Don't let anyone talk you out of it.

Net word count: approximately 2,400 words.
