# AIPIP-0021: Strategy Process V5 — Candidate Tracking, Adversarial Review, Supremacy Evaluation, Mission Evolution

**Status:** accepted
**Author:** Claude
**Created:** 2026-03-11

## Problem

Four gaps identified in the strategy development pipeline after s61's mission kill:

1. **No candidate tracking.** When a strategy passes gate criteria but fails mission criteria (e.g., s61: Calmar 70.6 > 47.0 but Sharpe 6.99 < 7.0), it disappears into the graveyard with no ranking. The user can't see HOW CLOSE candidates were, making it impossible to evaluate whether the mission itself is too strict.

2. **No adversarial review.** After a strategy passes backtest and beats existing strategies, there's no independent challenge. Backtester and evaluator are the same agent — confirmation bias risk. A quant adversarial review would catch survivorship bias, curve fitting, unrealistic assumptions.

3. **Portfolio-only evaluation.** V4-Gate 5 only asks "does this improve s58?" but never asks "is this BETTER than s58 standalone?" If a new strategy has Sharpe 9.0 and MaxDD -1.5%, it should become the new standard, not be forced into a portfolio complement role. The process should always find the globally optimal configuration.

4. **Static missions.** The mission file stays fixed until Gate 6 manual review. After paper trading, the agent should proactively draft an improved mission based on everything learned — new baselines, tighter/looser criteria, updated promising directions. The best result becomes the new standard.

## Solution

### 1. Candidate Ranking Table (`.claude/.strategy-candidates`)

After any gate kill at V4-Gate 3 or later, append the strategy to a ranking table:

```yaml
# .claude/.strategy-candidates
# Strategies that passed backtests but failed mission criteria
# Sorted by composite score (descending). Review to evaluate mission difficulty.

candidates:
  - id: s61_funding_carry_v4
    killed_at: v4gate5
    killed_by: mission  # or "gate"
    kill_reason: "Sharpe 6.99 < 7.0 mission threshold; MaxDD -4.71% > -3% mission threshold"
    metrics:
      sharpe: 6.99
      calmar: 70.60
      max_dd: -4.71
      sortino: 23.86
      total_return_pct: 862553.6
      total_trades: 21086
    portfolio_metrics:  # vs baseline
      sharpe_delta: -0.61
      calmar_delta: +23.56
      max_dd_delta: -0.78
    composite_score: 7.2  # weighted: 0.4*calmar_rank + 0.3*sortino_rank + 0.2*return_rank + 0.1*sharpe_rank
    date: "2026-03-11"
```

**Rules:**
- Append on ANY kill at V4-Gate 3+ (or Gate 4+ for per-token)
- Include both standalone AND portfolio metrics when available
- Sort by composite score (Calmar-weighted per "core objective: make more money, don't lose big")
- Gate kills use `killed_by: gate`, mission kills use `killed_by: mission`
- User can review to adjust mission criteria or resurrect candidates

### 2. Adversarial Quant Review (new Gate 5.75)

After V4-Gate 5 PASS (or Gate 5/5P/5O PASS), before Gate 6, insert an adversarial review:

**Gate 5.75: Adversarial Quant Review**

A subagent (independent context) receives ONLY the strategy code, backtest results, and
equity curve. It plays adversarial quant — its job is to FIND reasons the strategy will fail
in production. The subagent must NOT have access to the development reasoning or gate history
(prevents anchoring on the developer's justifications).

**Bias & Look-Forward Audit (mandatory — check every line of strategy code):**

| Bias Type | What to Check | How to Detect |
|-----------|--------------|---------------|
| **Look-ahead bias** | Does any signal use future data? | Trace every array: is `close[i]` ever compared to `close[i+k]` where k>0? Check rolling windows use only past bars. Check `funding_ma` uses trailing window not centered. |
| **Survivorship bias** | Are only winning tokens in the universe? | Check if token universe includes only tokens that survived to present. Delisted/dead tokens should be in data. |
| **Selection bias** | Was this signal picked BECAUSE it worked on this data? | Count total signals tested (Harvey-Liu N). If N>37, t-stat must exceed 3.4. |
| **Regime overfitting** | Does the strategy only work in 1 regime? | Check PnL distribution across regimes. If >70% from one regime → fragile. |
| **Parameter sensitivity** | Do small changes break it? | Check: +/-20% on each parameter. If Calmar degrades >50% → overfit. |
| **Indexing errors** | Off-by-one in warmup, entry/exit bars? | Verify `entry[:200] = False` matches indicator warmup. Check exit signals don't peek at current bar close. |
| **Funding model realism** | Are funding costs accurately modeled? | Compare assumed funding rate to actual historical distribution. Check if strategy's funding cost matches expectations. |
| **Execution assumptions** | Can trades actually be filled at these prices? | Check if entry/exit prices assume best-case fills. Slippage model present? |

**Structural Risk Assessment:**

| Check | What to Look For |
|-------|-----------------|
| Tail risk / short gamma | Is the strategy systematically selling insurance? Positive skew or negative? |
| Capacity constraint | At $1M+ capital, does market impact eat the edge? ADV checks? |
| Correlation stability | Does correlation with existing portfolio hold in drawdowns? (crisis correlation = 1.0 problem) |
| Crowding risk | Is this a well-known strategy that could get crowded? (funding carry = very crowded) |
| Regime dependency | If the regime detector is wrong, does the strategy blow up? |
| Concentration risk | Does >50% PnL come from <5 tokens? |

**The subagent MUST read the strategy source code line by line and flag ANY of the above.**
It outputs a structured report:

**Output:** PASS / CONDITIONAL PASS / FAIL with specific concerns and line references.
- FAIL → strategy goes to candidate table with `killed_by: adversarial_review`, specific bias cited
- CONDITIONAL PASS → document concerns with line references, proceed to Gate 6 with caveats
- PASS → no biases found, proceed to Gate 6

### 3. Supremacy Evaluation (expanded V4-Gate 5 / Gate 5)

V4-Gate 5 currently only tests "does this complement s58?" Add a parallel evaluation:

**Three evaluation modes (run ALL, pick best):**

| Mode | Question | When New Strategy Wins |
|------|----------|----------------------|
| A. Portfolio Complement | Does new strategy improve s58? | Combined portfolio > s58 alone |
| B. Standalone Supremacy | Is new strategy BETTER than s58 alone? | New strategy replaces s58 as baseline |
| C. Multi-Portfolio | Is new multi-strategy combination the global best? | New portfolio becomes production standard |

**Decision matrix:**
- Mode A wins: Deploy as complement (current behavior)
- Mode B wins: New strategy becomes the standard. Update mission baseline.
- Mode C wins: New portfolio combination becomes the standard. Update mission.
- All three tested, best configuration selected by composite metric (Calmar-weighted)

**Composite metric for comparison:**
```
composite = 0.35 * calmar_normalized + 0.25 * sortino_normalized + 0.25 * return_normalized + 0.15 * sharpe_normalized
```
Where each metric is normalized: `(value - min) / (max - min)` across all configurations.

### 4. Mission Evolution (expanded Gate 6)

After paper trading deployment, the agent MUST draft an improved mission:

```
**Mission Evolution Draft:**

Based on [N] strategies tested, [M] killed, [K] deployed:

**Proposed new baseline:**
  all_time: [best configuration] — [metrics]
  oos_3mo: [OOS metrics]

**Proposed kill_if adjustments:**
  [List criteria with proposed changes and reasoning]
  Example: "Relax MaxDD from 3% to 5% — baseline itself is at 3.93%,
  making 3% impossible to meet by addition. 14 of 14 candidates killed by this."

**Promising directions for next cycle:**
  [Updated based on what worked/failed]

**Candidate resurrection recommendations:**
  [Strategies from candidate table that would pass relaxed criteria]
```

Present to user with options:
1. **Accept draft** — new mission replaces old
2. **Modify** — user adjusts the draft
3. **Reject** — keep current mission
4. **Close mission** — no further search

## Changes to SKILL.md

1. **Gate Report Format:** Add candidate table append on kills
2. **New Gate 5.75:** Adversarial Quant Review (subagent)
3. **V4-Gate 5 / Gate 5:** Expand to include supremacy evaluation (modes A/B/C)
4. **Gate 6:** Add mission evolution draft (mandatory)

## Changelog

- 2026-03-11: Created
- 2026-03-11: Accepted. Removed engine look-ahead audit per user feedback (separate concern).
- 2026-03-11: Implemented all 4 changes to SKILL.md. Created .strategy-candidates with s61 entry.
