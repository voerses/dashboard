---
id: AIPIP-0014
title: Strategy Development Process v2 — Gate-Based Validation Pipeline
status: accepted
author: @claude
created: 2026-03-01
---

# AIPIP-0014: Strategy Development Process v2 — Gate-Based Validation Pipeline

## Problem

The current `/strategy` skill defines a simple 6-stage linear workflow (Signal Lab, Knowledge Check, Dedup Check, Prototype, Validate, Tier Assignment). While functional, it has several weaknesses exposed by the knowledge base research compiled in `FAST_ITERATION_FRAMEWORK.md` and `QUANT_FUND_PROCESSES.md`:

1. **No explicit kill criteria.** The current process tells you to validate but never defines quantitative thresholds for killing a strategy at each stage. Strategies linger in Tier B without a structured kill decision.

2. **Knowledge base consultation is a checklist, not a gate.** The pre-development checklist in `STRATEGY_LIFECYCLE.md` is comprehensive but advisory. Nothing enforces that all knowledge files were actually consulted before prototyping begins.

3. **Dedup checks lack rigor.** The current process compares entry signals informally. There is no quantitative overlap metric (e.g., signal correlation > 0.8 = duplicate) and no mandatory documentation of how the new signal differs.

4. **Statistical validation is limited to the V3 dual gate.** The current process runs Walk-Forward and CPCV via `v3/validation.py`, but does not require Deflated Sharpe Ratio (DSR) correction for multiple testing, IC significance testing, or parameter sensitivity analysis. These are industry-standard checks (see Cross-Firm Synthesis, Section 8 of `QUANT_FUND_PROCESSES.md`).

5. **No paper trading gate.** The pipeline goes directly from backtest validation to tier assignment. Every major quant fund requires a paper trading / forward test stage before live deployment (see Gate 4 in `FAST_ITERATION_FRAMEWORK.md` and Gate 4 in the Quant Fund recommended gate structure).

6. **No strategy decay monitoring.** Once a strategy reaches Tier A, there is no structured process for detecting edge decay. The sweep protocol catches gross failures but does not track rolling Sharpe degradation, increasing correlation with other strategies, or regime-shift divergence.

7. **The SKILL.md does not reference the process knowledge files.** The strategy skill should direct the agent to consult `FAST_ITERATION_FRAMEWORK.md`, `QUANT_FUND_PROCESSES.md`, and related knowledge files during strategy development.

## Proposal

Replace the current 6-stage linear process with an 8-gate pipeline that incorporates kill criteria, statistical validation, and paper trading. The new process draws directly from the synthesized frameworks in the knowledge base.

### New Gate Structure

```
GATE 0: IDEA SCREENING (< 15 min)
  Required:
    - Written hypothesis with economic rationale
    - Expected holding period, trade frequency, target regime
    - Consult knowledge/process/QUANT_FUND_PROCESSES.md Section 8 (kill criteria)
  Kill if:
    - No explainable mechanism for why the signal should exist
    - Signal relies on data not available at decision time (lookahead bias)
    - Idea already tested and failed (check strategies/archive/ and STRATEGY_LIFECYCLE.md Tier C)
    - Expected trade count < 30 in available data

GATE 1: SIGNAL LAB IC SCREEN (< 30 min)
  Required:
    - Run signal through v2/signal_lab.py
    - Record IC, IC t-stat, and horizon stability
    - Consult v2/knowledge/INDICATOR_ANALYSIS.md for existing signal results
    - Consult v2/knowledge/INDICATOR_CATALOG.md to check if indicator already exists
  Kill if:
    - Mean IC < +0.02 post-ETF (Jan 2024+)
    - IC t-stat < 2.0
    - Signal unstable across horizons (works on 1d but not 5d or 10d)
    - Signal flipped sign post-ETF without explanation

GATE 2: KNOWLEDGE BASE + DEDUP CHECK (< 15 min)
  Required:
    - Read ALL files in the pre-development checklist (STRATEGY_LIFECYCLE.md Section 2)
    - Compare proposed entry signal against ALL existing Tier A/B entry signals
    - Document the overlap assessment with specific signal-level comparison
    - Check results/sweep_summary_*.json for current tier classifications
  Kill if:
    - Entry signal overlaps > 80% with an existing Tier A/B strategy
    - Knowledge base review reveals the approach was already tried and failed
    - Proposed signal type already has 2+ strategies in Tier A (saturation)
  Recycle if:
    - Partial overlap (30-80%): propose as a filter addition to existing strategy instead

GATE 3: PROTOTYPE + PERFORMANCE CHECK (< 30 min)
  Required:
    - Copy strategies/TEMPLATE.py, implement using SIGNAL_DEVELOPMENT.md structure
    - All code vectorized per PERFORMANCE_PATTERNS.md
    - Performance check: < 1ms per call on 40K bars
    - Include all mandatory components: regime filter, trend alignment, entry, exit, sizing
  Kill if:
    - Performance > 1ms per call (not vectorized enough)
    - Cannot implement the signal without Python for-loops over bar arrays
    - Strategy requires indicators not available in the engine (and adding them
      would be a blast-radius change)

GATE 4: QUICK VALIDATION — SINGLE TOKEN (< 5 min)
  Required:
    - Run v3/validation.py --strategy sNN --tokens BTC --workers 1
    - BTC must pass dual WF+CPCV gate
  Kill if:
    - BTC fails after 3 tuning attempts (hypothesis is likely wrong)
    - Tuning requires changing the core signal logic (not just parameters)

GATE 5: FULL VALIDATION — STATISTICAL (< 10 min)
  Required:
    - Run v3/validation.py --strategy sNN --workers 4 (filtered universe, ~111 tokens)
    - Record validation rate
    - Compute Deflated Sharpe Ratio (DSR) corrected for number of strategies tested
    - Run parameter sensitivity analysis (+/- 20% on key parameters)
    - Check cross-token consistency: which tokens fail and why
  Kill if:
    - Validation rate < 20% (Tier C — archive immediately)
    - DSR p-value > 0.05 (not significant after multiple-testing correction)
    - Parameter sensitivity: Sharpe degrades > 30% with +/- 20% parameter change
    - Validation rate 20-50% AND no clear path to improvement after analysis
  Tier assignment:
    - Rate > 50%: Tier A (proceed to Gate 6)
    - Rate 20-50%: Tier B (up to 3 iteration cycles, then kill or promote)
    - Rate < 20%: Tier C (archive, skip remaining gates)

GATE 6: PAPER TRADING CONFIRMATION (1-2 weeks, Tier A only)
  Required:
    - Run strategy on live data feed with simulated execution
    - Minimum 50 trades observed
    - Record execution quality: slippage vs backtest assumptions
    - Compare live signal generation timing with backtest signal timing
  Kill if:
    - Live Sharpe < 60% of backtest Sharpe
    - Slippage > 50% of expected edge per trade
    - Operational failures > 1 per week
    - Strategy generates < 10 trades in the paper trading period (insufficient data)

GATE 7: PRODUCTION DEPLOYMENT + DECAY MONITORING (ongoing)
  Required:
    - Deploy with strategy-specific risk limits configured
    - Automated monitoring dashboard active
    - Monthly sweep comparison against historical validation rates
  Ongoing kill triggers:
    - Rolling 3-month Sharpe < 0.0
    - Validation rate drops below 40% across 2 consecutive sweeps
    - Single catastrophic sweep where rate drops below 20%
    - Correlation with another Tier A strategy exceeds 0.7 (diversification lost)
    - Rolling drawdown exceeds 1.5x worst historical drawdown
```

### SKILL.md Changes

Update `.claude/skills/strategy/SKILL.md` to:

1. Reference the new 8-gate process instead of the current 6-stage list.
2. Add mandatory knowledge file consultation instructions:
   - `knowledge/process/FAST_ITERATION_FRAMEWORK.md` — gate structure, kill criteria, IC screening, SPRT
   - `knowledge/process/QUANT_FUND_PROCESSES.md` — cross-firm synthesis, validation pipeline, risk controls
   - `knowledge/STRATEGY_LIFECYCLE.md` — tier system, dedup table, sweep protocol
   - `knowledge/SIGNAL_DEVELOPMENT.md` — signal structure patterns
   - `knowledge/PERFORMANCE_PATTERNS.md` — vectorization requirements
3. Add the gate confirmation output format so the agent reports gate pass/fail status at each transition.

### Proposed SKILL.md Content

```yaml
---
name: strategy
description: "Switch to strategy development mode (8-gate validation pipeline)"
user_invocable: true
---
```

```markdown
# Strategy Development Mode

Switch the process to strategy development mode. This is a gate-based workflow
for building and validating trading strategies with explicit kill criteria at
each stage.

## Instructions

1. Write `strategy` to the process mode file:

\`\`\`bash
echo "strategy" > "$CLAUDE_PROJECT_DIR/.process-mode"
\`\`\`

2. Confirm to the user:

> **Switched to strategy mode.** Dev workflow hooks are paused.
>
> Strategy validation pipeline (8 gates):
> 0. **Idea Screen** — hypothesis + economic rationale (kill: no mechanism, already failed)
> 1. **Signal Lab** — IC screen on historical data (kill: IC < 0.02, t-stat < 2.0)
> 2. **Knowledge + Dedup** — consult KB, compare against existing strategies (kill: > 80% overlap)
> 3. **Prototype** — write vectorized strategy from template (kill: > 1ms/call)
> 4. **Quick Validate** — BTC-only dual gate (kill: fails after 3 attempts)
> 5. **Full Validate** — filtered universe + DSR + parameter sensitivity (kill: rate < 20%, DSR p > 0.05)
> 6. **Paper Trade** — live data, simulated execution, 50+ trades (kill: Sharpe < 60% of backtest)
> 7. **Production** — deploy with risk limits + decay monitoring (kill: rolling Sharpe < 0)
>
> **Before starting, consult these knowledge files:**
> - `knowledge/process/FAST_ITERATION_FRAMEWORK.md` (gate details, kill thresholds)
> - `knowledge/process/QUANT_FUND_PROCESSES.md` (cross-firm best practices)
> - `knowledge/STRATEGY_LIFECYCLE.md` (tier system, dedup table, sweep protocol)
>
> Use `/dev` to switch to full dev workflow, `/free` to switch to freeflow.
```

### Gate Transition Reporting

At each gate, the agent must output a structured gate report:

```
## Gate N: [NAME] — [PASS / KILL / RECYCLE]

| Metric | Value | Threshold | Status |
|--------|-------|-----------|--------|
| [metric] | [value] | [threshold] | PASS/FAIL |

**Decision:** [PROCEED to Gate N+1 / KILL: reason / RECYCLE: proposed modification]
```

This makes the decision trail auditable and prevents strategies from silently advancing past gates.

### Strategy Decay Monitoring Protocol

For Tier A strategies in production (Gate 7), add a structured monthly review:

1. Run full sweep and compare validation rate against previous 2 sweeps.
2. Compute rolling 3-month Sharpe from paper/live trading data (when available).
3. Compute pairwise correlation matrix across all Tier A strategies.
4. Flag any strategy where:
   - Validation rate declined > 10 percentage points
   - Rolling Sharpe turned negative
   - Correlation with another Tier A strategy > 0.7
5. Flagged strategies enter a 1-sweep probation. If the issue persists, demote to Tier B.

### Tier B Iteration Rules (Tightened)

Current process allows unlimited iteration on Tier B strategies. New rules:

- Maximum 3 iteration cycles per Tier B strategy.
- Each cycle must change parameters only (not core signal logic).
- Each cycle must include a full filtered-universe validation run.
- If rate does not exceed 50% after 3 cycles, archive immediately.
- Time-box: if a Tier B strategy has not been promoted within 30 days of creation, archive it.

## Alternatives Considered

### A. Keep the current 6-stage process and just add kill criteria

Add quantitative kill criteria to the existing linear stages without restructuring. **Rejected** because the current process conflates idea screening with signal testing (both happen before prototype), lacks a paper trading stage entirely, and has no decay monitoring. Adding kill criteria to a structurally incomplete pipeline would leave gaps.

### B. Adopt the full 8-stage pipeline from QUANT_FUND_PROCESSES.md verbatim

The quant fund framework includes gates for "Small Capital Live" and "Full Production" as separate stages. **Simplified** because our crypto backtest system does not yet have a live trading execution layer. Gates 6 (Paper Trading) and 7 (Production + Decay Monitoring) cover the relevant concerns. When a live execution engine is added, Gate 7 can be split into "Small Capital" and "Full Deployment."

### C. Add SPRT (Sequential Probability Ratio Test) as a mandatory gate

The FAST_ITERATION_FRAMEWORK.md describes SPRT for early accept/reject decisions during paper trading. **Deferred** because SPRT requires a live data feed and sufficient trade volume. It is noted as a recommended technique within Gate 6 but not a hard requirement until the paper trading infrastructure matures.

### D. Add Monte Carlo robustness testing as a separate gate

The quant fund framework includes Monte Carlo perturbation tests (9+ perturbation types). **Folded into Gate 5** rather than creating a separate gate, since our V3 validation already includes walk-forward and CPCV. Monte Carlo tests can be added to the validation script as an enhancement without a process change.

## Impact

### Modified files

| File | Change |
|------|--------|
| `.claude/skills/strategy/SKILL.md` | Replace 6-stage process with 8-gate pipeline, add knowledge file references |
| `knowledge/STRATEGY_LIFECYCLE.md` | Update Section 3 (Research-to-Prototype Pipeline) to reference the new gate structure, add Gate 6-7 sections, add decay monitoring protocol |

### New capabilities

- Explicit quantitative kill criteria at every gate (no more subjective "does it look good?" decisions)
- Mandatory DSR correction for multiple testing in Gate 5
- Paper trading gate (Gate 6) before any production deployment
- Structured decay monitoring (Gate 7) for production strategies
- Gate transition reports create an auditable decision trail
- Tier B time-boxing prevents strategies from lingering indefinitely

### What does NOT change

- The V3 validation engine itself (`v3/validation.py`) — no code changes required
- The tier system (A/B/C thresholds remain at 50%/20%)
- Strategy numbering conventions
- The knowledge base files (they are consulted, not modified)
- The sweep protocol (still runs the same commands)
- The `/dev` and `/free` process modes

### Downstream impact

- Low blast radius: changes are confined to one skill definition and one knowledge file
- No hook changes required (strategy mode is not enforced by hooks — it is a lighter workflow)
- No settings.json changes

## Change Log

| Date | Change |
|------|--------|
| 2026-03-01 | Initial proposal |
| 2026-03-01 | Accepted by user. Implementing: SKILL.md rewrite, strategy-gate-guard.sh hook, workflow-nudge.sh update, settings.json wiring |
