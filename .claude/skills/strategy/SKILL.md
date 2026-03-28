---
name: strategy
description: "Switch to strategy development mode (8-gate validation pipeline)"
user_invocable: true
---

# Strategy Development Mode

Gate-based workflow for building and validating trading strategies.

**Core objective: Make more money. Don't lose big.**
- Return is the primary goal (Calmar, Sortino, total return)
- Max drawdown is the hard constraint (never blow up)
- Sharpe is diagnostic only

## Instructions

### Step 1: Set mode files

```bash
echo "strategy" > "$CLAUDE_PROJECT_DIR/.process-mode"
echo "gate0" > "$CLAUDE_PROJECT_DIR/.strategy-gate"
```

### Step 2: Load state

Read these files and extract key information for the briefing:

```
READ: memory/RESEARCH_COORDINATOR.md — your role identity, operating model, anti-patterns
READ: memory/RESEARCH_STATUS.md — recent experiments, signal scoreboard, next actions
READ: memory/PROJECT_STATUS.md — capability inventory, strategy tiers, open items
READ: .claude/.strategy-mission — active mission (if file exists and status: active)
```

Skim `findings/strategy-findings.jsonl` (last 10 entries) for recent insights.

### Step 3: Present startup briefing

Output a briefing in this format (fill in from loaded state):

```
**Research Coordinator online.** I coordinate parallel signal research,
decide kill/pass/double-down based on data, and iterate autonomously.
I never ask "what next?" — I decide based on data and keep moving.

## Current State
- **Strategies:** [N Tier A, N Tier B, N in graveyard]
- **Signals tested:** [N total — N GOLD, N PASS, N KILLED]
- **Active mission:** [mission summary or "none"]
- **Last session:** [1-2 line summary from RESEARCH_STATUS.md]
- **Key finding:** [most recent actionable finding]

## What's Next
[2-3 bullet points of highest-priority research items from RESEARCH_STATUS.md]

## Gate Pipeline (reference)
| Gate | Name | Kill Rate |
|------|------|-----------|
| 0 | Idea Screen | ~50% |
| 1 | Signal Lab | ~90% |
| 2 | Knowledge + Dedup | ~30% |
| 3 | Prototype | ~10% |
| 4 | Quick Validate | ~50% |
| 5 | Full Validate | ~50% |
| 6 | Paper Trade | ~50% |
| 7 | Production + Decay | ~30%/yr |

~99.8% of ideas never reach production. This is normal.

Starting research now.
```

### Step 4: Begin autonomous research

After presenting the briefing:
- If RESEARCH_STATUS.md has prioritized next actions → begin executing them immediately
  (launch background agents, start Gate 0 screening, etc.) without waiting for user input.
- If an active mission exists → use mission goals and promising directions to choose research.
- If no clear next action exists → present 2-3 candidate research directions and ask
  which to pursue.

**Do NOT wait for the user to propose an idea.** You are the coordinator — you decide
what to research based on the state you loaded. The user can redirect you at any time.

### Step 5: Session wrap-up (before session ends)

**Trigger:** Before context window expires, when user says "stop"/"done"/"wrap up", or
when shifting to a different mode. This is NON-NEGOTIABLE — losing state wastes time and money.

**Update these files (only what changed this session):**

| File | When to Update | What to Write |
|------|---------------|---------------|
| `memory/RESEARCH_STATUS.md` | **Always** | New signal verdicts (GOLD/PASS/KILLED), updated scoreboard, next actions list |
| `findings/strategy-findings.jsonl` | **Always** | Append one finding per gate outcome or significant discovery |
| `memory/PROJECT_STATUS.md` | If tiers changed or new capabilities added | Strategy tier moves, new tools, deployment changes |
| `memory/INDEX.md` | If new files created or files significantly changed | Add/update file entries with current sizes and summaries |
| `knowledge/QUANT_METHODOLOGY.md` | If new statistical insights discovered | New thresholds, methodology corrections, crypto-specific learnings |
| `knowledge/STRATEGY_QUICK_REFERENCE.md` | If dedup tables or capability lists changed | New strategies in tables, updated baselines |

**Rules:**
- **Append, don't rewrite.** Add new entries to state files; don't restructure existing content.
- **Promote on validation.** Raw findings → `findings/strategy-findings.jsonl`. Only promote to
  knowledge files after confirmation (multiple analyses or explicit review).
- **Size discipline.** If a file exceeds its target size (see KNOWLEDGE_BASE_GUIDELINES.md),
  archive older sections rather than letting it grow unbounded.
- **Staleness marker.** When updating a knowledge file, add/update `Last updated: YYYY-MM-DD`
  in the file header.
- **No bloat.** Don't add raw agent outputs, intermediate calculations, or tool dumps.

## Knowledge Loading Strategy

**Default at every gate:** Read `knowledge/STRATEGY_QUICK_REFERENCE.md` — the section for
your current gate. This single file (~350 lines) contains all thresholds, kill criteria,
dedup tables, cost tables, and the bias audit checklist.

**Deep dive only when:** investigating a specific failure, debugging validation results,
or the quick reference says "Deep dive: [file]".

**Never load all knowledge files at once.** That's ~10K+ lines across 17 files and will degrade quality.

## Strategy Classes

Three strategy classes, each with its own gate path:

| Class | Gate Path | When to Use |
|-------|-----------|-------------|
| A. Per-Token Signal | 0→1→2→3→4→5→6→7 | Single signal on individual tokens |
| B. Portfolio Strategy | 0→2→3P→5P→6→7 | Cross-token ranking, sector rotation, pairs |
| C. Overlay | 0→2→3O→5O→6→7 | Regime weighting, signal agreement, risk scaling |

**Choose class at Gate 0.** The class determines which gates you hit.

## Gate Process

At each gate:
1. Read `knowledge/STRATEGY_QUICK_REFERENCE.md` — your current gate's section
2. Run the specified checks
3. Run the bias audit checklist (bottom of quick reference)
4. Output a gate report (format below)
5. Capture findings to `findings/strategy-findings.jsonl`
6. Update the gate state file

### Gate Report Format (mandatory at every transition)

```
## Gate N: [NAME] — [PASS / KILL / RECYCLE]

| Metric | Value | Threshold | Status |
|--------|-------|-----------|--------|
| [metric] | [value] | [threshold] | PASS/FAIL |

**Bias audit:** [CLEAN / issues found]
**Decision:** [PROCEED to Gate N+1 / KILL: reason / RECYCLE: proposed modification]

### Findings (for future sessions)
- [SIGNAL] [one-line insight about the signal that future strategies should know]
- [DATA] [any data gap or limitation discovered]
- [PROCESS] [anything the gate process got right or wrong]
```

After outputting the report:

```bash
# Update gate state
echo "gateN" > "$CLAUDE_PROJECT_DIR/.strategy-gate"

# Append finding to strategy-findings.jsonl (mandatory)
echo '{"ts": "YYYY-MM-DDTHH:MM:SSZ", "strategy": "sNN_name", "gate": N, "outcome": "pass|kill|recycle", "metrics": {...}, "finding": "One-line insight", "category": "signal|capability|portfolio|data|infra|process|bug", "affects": ["file1", "file2"]}' >> findings/strategy-findings.jsonl
```

After gate kills or passes, also update `memory/PROJECT_STATUS.md`:
- **Kill:** Add to graveyard table, update Tier C if applicable
- **Pass Gate 5/5P/5O:** Add to appropriate tier table
- **New capability:** Add to capability inventory
- **Bug found:** Add to open items

---

## GATE 0: Idea Screening (< 5 min)

### Step 0: Load Project State

```
READ: memory/PROJECT_STATUS.md — open tasks, capability inventory, strategy tiers, key findings
READ: knowledge/STRATEGY_QUICK_REFERENCE.md — "Available Capabilities" + "Gate 0" sections
```

This tells you what's been built, what's broken, and what tools you have.

### Step 0.5: Curate Findings (if needed)

If `findings/strategy-findings.jsonl` has >50 entries since last curation:
1. Review findings grouped by category
2. Promote undocumented insights to their target knowledge files
3. Archive findings older than 6 months with zero references
4. Update `memory/PROJECT_STATUS.md` with any changes

### Step 1: Choose Strategy Class

| Class | Gate Path | Hypothesis Template |
|-------|-----------|---------------------|
| A. Per-Token Signal | 0→1→2→3→4→5→6→7 | "[Signal] predicts [direction] on [token] over [hold] because [mechanism]" |
| B. Portfolio Strategy | 0→2→3P→5P→6→7 | "[Ranking/selection] across [universe] produces alpha because [mechanism]" |
| C. Overlay | 0→2→3O→5O→6→7 | "Applying [overlay] to [base strategy] improves [metric] because [mechanism]" |

**Portfolio subtypes:** cross-sectional momentum, sector rotation, pairs/stat arb, dynamic factor
**Overlay subtypes:** regime weighting, signal agreement, rebalancing rules, risk scaling

### Step 2: Write Hypothesis

Use the template for your class. Must include:
- Signal/method description
- Economic mechanism (why should this work?)
- Expected holding period or rebalance frequency

### Step 3: Check Graveyard + Tier C

Read `strategies/GRAVEYARD.md` and Tier C list in Quick Reference.
Kill if: already tried with no new evidence.

### Step 4: Score the Idea

| Criterion | Threshold |
|-----------|-----------|
| Economic mechanism | Must be explainable |
| Expected trade count | > 30 in available data |
| Idea score | >= 5/10 on rubric |
| Already tried & failed? | Check Tier C list |
| Look-ahead bias risk | Signal must use only past data |

### Kill Criteria
- No explainable economic mechanism
- Signal requires data not available at decision time (look-ahead bias)
- Already tested and failed with no new evidence
- Expected trade count < 30
- Idea score < 5/10
- **Overlay on delta-neutral base:** Directional risk overlays (weekend sizing, regime
  sizing for directional regimes) have no effect on delta-neutral strategies (e.g., long
  spot + short perp). Check if the base strategy is delta-neutral — if so, skip
  directional overlays at Gate 0.

### On PASS

```bash
# Per-token: proceed to Gate 1
echo "gate1" > "$CLAUDE_PROJECT_DIR/.strategy-gate"
# Portfolio or Overlay: skip Gate 1, proceed to Gate 2
echo "gate2" > "$CLAUDE_PROJECT_DIR/.strategy-gate"
```

---

## GATE 1: Signal Lab IC Screen (< 30 min)

**Per-token strategies only.** Portfolio and overlay strategies skip to Gate 2.

**Read:** Quick Reference — "Gate 1" section (IC thresholds, top predictors, redundancy)

### What to Do
1. Run `tools/signal_lab.py` or compute IC manually
2. Record: Mean IC, t-stat, ICIR, hit rate
3. Test post-ETF (Jan 2024+), check 2+ horizons
4. Kill if: IC <0.02, t-stat <2.0, hit rate <55%, PF <1.3, trades <50
5. Recycle: PF >1.1 but borderline → ONE retry

**Deep dive if needed:** `knowledge/process/SIGNAL_DISCOVERY_METHODS.md`,
`knowledge/INDICATOR_CATALOG.md`

```bash
echo "gate2" > "$CLAUDE_PROJECT_DIR/.strategy-gate"
```

---

## GATE 2: Knowledge + Dedup (< 15 min)

**Read:** Quick Reference — "Gate 2" + "Existing Tier A/B" sections

### What to Do
1. Compare against ALL existing strategies (table in quick ref)
2. Document overlap assessment
3. Check `results/sweep_summary_*.json`

### Dedup Rules by Class

**Per-token strategies:**
- Entry signal overlaps >80% with existing Tier A/B → KILL
- Already tried and failed (Tier C) with no new evidence → KILL
- Signal type has 2+ strategies in Tier A → saturated, KILL
- Overlap 30-80% → propose as FILTER to existing strategy, not new strategy

**Portfolio strategies:**
- Compare strategy CLASS, not entry signal (cross-sectional vs sector vs pairs)
- Check correlation vs existing portfolio strategies: >0.7 → KILL
- If same class exists: must show improvement on Calmar or DD, not just different parameters

**Overlays:**
- Check if overlay already applied to base strategy
- Multiple overlays on same base OK if targeting different aspects
  (regime=allocation, agreement=entry, risk=sizing)

**Deep dive if needed:** `knowledge/STRATEGY_CATALOG.md`,
`knowledge/process/CRYPTO_MICROSTRUCTURE_POST_ETF.md`

```bash
# Per-token: proceed to Gate 3
echo "gate3" > "$CLAUDE_PROJECT_DIR/.strategy-gate"
# Portfolio: proceed to Gate 3P
echo "gate3p" > "$CLAUDE_PROJECT_DIR/.strategy-gate"
# Overlay: proceed to Gate 3O
echo "gate3o" > "$CLAUDE_PROJECT_DIR/.strategy-gate"
```

---

## GATE 3: Prototype — Per-Token (< 30 min)

**Read:** Quick Reference — "Gate 3" section + `strategies/TEMPLATE.py`

### What to Do
1. Copy `strategies/TEMPLATE.py` → `strategies/sNN_name.py`
2. Implement 6-layer signal stack (regime, trend, entry, volume, exit, sizing)
3. ALL code vectorized — no Python for-loops over bar arrays
4. Performance check:
   ```bash
   python -c "
   import sys; sys.path.insert(0, 'v3')
   from engine import Engine
   import pandas as pd, time
   eng = Engine(data_dir='data')
   df = pd.read_parquet('data/1h_cache/BTC_1h.parquet')
   ctx = eng._build_context('BTC', df)
   from strategies.sNN_name import strategy
   t0 = time.perf_counter()
   for _ in range(1000): strategy(ctx)
   print(f'{(time.perf_counter()-t0)/1000*1000:.3f}ms/call')
   "
   ```
5. Kill if: >1ms/call, for-loops, missing regime/exit

**Deep dive if needed:** `knowledge/SIGNAL_DEVELOPMENT.md`,
`knowledge/PERFORMANCE_PATTERNS.md`

```bash
echo "gate4" > "$CLAUDE_PROJECT_DIR/.strategy-gate"
```

---

## GATE 3P: Prototype — Portfolio Strategy (< 30 min)

**Portfolio strategies only.** Uses `v3/` modules, not `strategies/sNN_*.py`.

**Read:** Quick Reference — "Gate 3" + "Available Capabilities" sections

### What to Do
1. Extend existing module or create new one in `v3/`:
   - Cross-sectional: `v3/cross_sectional.py`
   - Sector rotation: `v3/sector_rotation.py`
   - Pairs/stat arb: `v3/pairs_trading.py`
   - New type: create `v3/new_type.py` (justify why existing modules don't fit)
2. All code vectorized — no Python for-loops over bar arrays
3. Check:

| Criterion | Threshold | Kill |
|-----------|-----------|------|
| Uses existing module? | Extend existing if possible | If new module, justify |
| Rebalance frequency | Weekly or slower | Daily = too much turnover |
| Universe coverage | >=20 tokens eligible | <20 = insufficient breadth |
| Turnover | <50% per rebalance | >50% = fee drag kills edge |
| Vectorized? | Yes | For-loops over bars = kill |

```bash
echo "gate5p" > "$CLAUDE_PROJECT_DIR/.strategy-gate"
```

---

## GATE 3O: Prototype — Overlay (< 30 min)

**Overlay strategies only.** Modifies allocation/entry of existing strategies.

**Read:** Quick Reference — "Available Capabilities" (Overlays section)

### Rule: Never Modify Base Strategies (AIPIP-0018)

Overlays MUST be implemented as **new wrapper strategy files** that import and call the
base strategy. NEVER add overlay logic (flags, conditionals, sizing code) to an existing
validated strategy file.

**Correct pattern — wrapper file:**
```python
# strategies/sNN_base_with_overlay.py  (NEW file)
from strategies.sXX_base_strategy import strategy as base_strategy

def strategy(ctx):
    result = base_strategy(ctx)         # Call base UNCHANGED
    size_mult = compute_overlay(ctx)    # Overlay logic here
    return StrategyResult(
        ...result fields...,
        size_multiplier=size_mult,
        name='sNN_base_with_overlay',
    )
```

**Forbidden — modifying base:**
```python
# strategies/sXX_base_strategy.py  <-- DO NOT MODIFY
ENABLE_OVERLAY = True  # <-- NO: adding overlay logic to base
```

### What to Do
1. Identify base strategy to overlay (must be Tier A/B)
2. Create a **new wrapper file** `strategies/sNN_name.py` that imports the base
3. Implement overlay logic in the wrapper only
4. If the overlay needs a new `StrategyResult` field (e.g., `size_multiplier`):
   - Add to `StrategyResult` dataclass with backward-compatible default
   - Apply generically in engine (not overlay-specific logic)
   - Use the **StrategyResult field checklist** below
5. Use existing module or create new one for overlay computation:
   - Regime weighting: `v3/regime_analysis.py`
   - Signal agreement: `v3/signal_agreement.py`
   - New overlay: create `v3/new_overlay.py`
6. Check:

| Criterion | Threshold | Kill |
|-----------|-----------|------|
| Base strategy identified? | Must specify which Tier A/B strategy | No base = kill |
| Wrapper file created? | New `sNN_*.py`, NOT modifying base | Modified base = revert + redo |
| Module exists? | Use existing if possible | Justify if new |
| Hypothesis testable? | Can measure improvement on historical data | Untestable = kill |
| Vectorized? | No Python for-loops over bar arrays | For-loops = fix before Gate 5O |
| < 1ms/call? | Performance check on wrapper | > 1ms = optimize |

### StrategyResult Field Checklist (for new engine support)

When an overlay needs a new generic field in `StrategyResult`:

- [ ] Added to `StrategyResult` dataclass with default value
- [ ] Applied in `_simulate()` (single-leg path)
- [ ] Applied in `_simulate_combined()` (dual-leg path)
- [ ] Forwarded in `_run_walk_forward()` masked result construction
- [ ] Forwarded in `_run_walk_forward_combined()` masked result construction
- [ ] Paper trader reads from strategy result (no hardcoding)

```bash
echo "gate5o" > "$CLAUDE_PROJECT_DIR/.strategy-gate"
```

---

## GATE 4: Quick Validate — BTC Only (< 5 min)

**Per-token strategies only.** Portfolio/overlay strategies skip to Gate 5P/5O.

**Read:** Quick Reference — "Gate 4" section

1. Run: `/workspace/venv/bin/python v3/validation.py --strategy sNN --tokens BTC --workers 1`
2. BTC must pass BOTH walk-forward AND CPCV (PBO < 40%)
3. FAIL → tune parameters (NOT core logic), max 3 attempts
4. 3 failures → KILL

```bash
echo "gate5" > "$CLAUDE_PROJECT_DIR/.strategy-gate"
```

---

## GATE 5: Full Validate — Per-Token, 49 Tokens (< 10 min)

**Read:** Quick Reference — "Gate 5" section (metrics, tier assignment, costs)

1. Run: `/workspace/venv/bin/python v3/validation.py --strategy sNN --workers 4`
2. Tier assignment: A >50%, B 20-50%, C <20%
3. Metrics: Calmar >0.5, Sortino >1.0, PF >1.5, MaxDD <25%
4. Parameter sensitivity: +/-20% → Calmar shouldn't degrade >30%
5. Kill if: rate <20%, Calmar <0.5, MaxDD >25%, WFE <50%

**Deep dive if needed:** `knowledge/process/BACKTESTING_VALIDATION_BEST_PRACTICES.md`,
`knowledge/process/RISK_PORTFOLIO_CONSTRUCTION.md`

```bash
echo "gate6" > "$CLAUDE_PROJECT_DIR/.strategy-gate"
```

---

## GATE 5P: Full Validate — Portfolio Strategy

**Portfolio strategies only.** Produces a single portfolio equity curve, not per-token validation rates.

**Read:** Quick Reference — "Gate 5" section for cost tables

### What to Do
1. Run the portfolio strategy's own validation (module-specific CLI)
2. Record portfolio-level metrics:

| Criterion | Threshold | Kill |
|-----------|-----------|------|
| Calmar ratio | >0.5 | <0.5 |
| Max drawdown | <40% | >40% |
| Annual return | >20% | <20% |
| Trade count | >200 total | <200 |
| **Corr vs best Tier A** | **<0.5** | **>0.7 = no diversification value** |
| Turnover-adjusted Sharpe | >0.8 after costs | <0.8 |

3. Run `v3/correlation.py` to check correlation vs existing Tier A strategies
4. Kill if: Calmar <0.5, DD >40%, corr >0.7 vs existing portfolio strategies

**Note:** Tier assignment uses portfolio metrics directly, not "validation rate across 49 tokens."

```bash
echo "gate6" > "$CLAUDE_PROJECT_DIR/.strategy-gate"
```

---

## GATE 5O: Full Validate — Overlay

**Overlay strategies only.** Measures improvement over base strategy.

### What to Do
1. Run base strategy WITHOUT overlay → record baseline metrics
2. Run base strategy WITH overlay → record improved metrics
3. Compare:

| Criterion | Before Overlay | After Overlay | Kill If |
|-----------|---------------|---------------|---------|
| Calmar | baseline | must improve | Calmar degrades |
| Max drawdown | baseline | must improve or hold | DD worsens >2pp |
| Sharpe | baseline | should improve | Sharpe degrades >0.1 |
| Trade count | baseline | may decrease | <50 trades remain |
| Regime robustness | — | works in 2+ regimes | Only works in 1 regime |

4. Kill if: overlay makes any primary metric worse

```bash
echo "gate6" > "$CLAUDE_PROJECT_DIR/.strategy-gate"
```

---

## GATE 5.5: Portfolio Assembly (< 30 min)

**Optional.** Run when you have 2+ strategies passing their respective Gate 5 and want to build an optimal combined portfolio.

**Read:** Quick Reference — "Available Capabilities" (Portfolio Tools section)

### What to Do
1. Run `v3/correlation.py` on all candidate strategies
2. Compute marginal Sharpe contribution of each
3. Test allocation methods: equal-weight, risk parity, regime-weighted
4. Simulate combined portfolio with shared cash pool (`v3/portfolio.py`)

### Metrics

| Metric | Target |
|--------|--------|
| Portfolio Calmar | > 1.0 |
| Portfolio max DD | < 20% |
| Effective N (strategies) | > 3.0 |
| Max pairwise correlation | < 0.7 |
| Worst regime (DOWNTREND) PnL | > -5% annualized |

### Output
- Allocation table: strategy → weight
- Expected portfolio metrics (Calmar, Sortino, DD)
- Regime performance heatmap
- Recommended overlays (if any)

```bash
echo "gate6" > "$CLAUDE_PROJECT_DIR/.strategy-gate"
```

---

## GATE 6: Paper Trading (1-4 weeks)

**Read:** Quick Reference — "Gate 6" section

1. Deploy on live data, simulated execution
2. Min 50 trades before go-live
3. Kill if: returns <60% backtest, slippage >50% edge, MaxDD >1.5x, <10 trades

**Deep dive if needed:** `knowledge/PAPER_TRADING_PRO_FRAMEWORK.md`

```bash
echo "gate7" > "$CLAUDE_PROJECT_DIR/.strategy-gate"
```

---

## GATE 7: Production + Decay (ongoing)

**Read:** Quick Reference — "Gate 7" section

1. Allocation: 1-5% target, 1/4 Kelly, <5% ADV
2. Circuit breakers: -3% daily halt, -15% pull
3. Monthly decay: Sortino <0, PF <0.8, no high 6mo → escalate

**Deep dive if needed:** `knowledge/process/RISK_PORTFOLIO_CONSTRUCTION.md`,
`knowledge/KRAKEN_FEES.md`

---

## Strategy Graveyard

When killed at any gate:
```bash
echo "$(date -I) | sNN_name | Killed at Gate X | Reason: [specific reason]" >> "$CLAUDE_PROJECT_DIR/../strategies/GRAVEYARD.md"
```
