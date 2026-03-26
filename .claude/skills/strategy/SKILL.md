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

1. Write `strategy` to the process mode file:

```bash
echo "strategy" > "$CLAUDE_PROJECT_DIR/.process-mode"
```

2. Initialize the gate state file for tracking:

```bash
echo "gate0" > "$CLAUDE_PROJECT_DIR/.strategy-gate"
```

3. Confirm to the user:

> **Switched to strategy mode.** Strategy gate enforcement active.
>
> | Gate | Name | Time | Kill Rate | Key Kill Criterion |
> |------|------|------|-----------|-------------------|
> | 0 | Idea Screen | 5 min | ~50% | No mechanism / already failed |
> | 1 | Signal Lab | 30 min | ~90% | IC < 0.02, t-stat < 3.4 (Harvey-Liu) |
> | 2 | Knowledge + Dedup | 15 min | ~30% | >80% overlap with existing |
> | 3/3P/3O/V4-3 | Prototype | 15-30 min | ~10% | >1ms/call, can't vectorize |
> | 4 | Quick Validate (BTC) | 5 min | ~50% | BTC fails dual gate x3 |
> | V4-4 | V4 OOS (Jan-Mar) | 10 min | ~50% | OOS negative, March < -5% |
> | 5/5P/5O/V4-5 | Full Validate + Supremacy | 10 min | ~50% | Rate <20% / no mode passes |
> | 5.75 | Adversarial Quant Review | 10 min | ~20% | Bias found / structural risk |
> | 6 | Paper Trade | 1-4 wks | ~50% | Sharpe < 0.4x backtest |
> | 6.5 | Knowledge Update | 10 min | 0% | Checklist ensures all knowledge files current |
> | 7 | Production + Decay | ongoing | ~30%/yr | BOCPD + threshold decay |
>
> **~99.8% of ideas never reach production. This is normal.**

## Knowledge Loading Strategy

**Default at every gate:** Read `knowledge/STRATEGY_QUICK_REFERENCE.md` — the section for
your current gate. This single file (~350 lines) contains all thresholds, kill criteria,
dedup tables, cost tables, and the bias audit checklist.

**Deep dive only when:** investigating a specific failure, debugging validation results,
or the quick reference says "Deep dive: [file]".

**Never load all knowledge files at once.** That's ~10K+ lines across 17 files and will degrade quality.

## Strategy Classes

Four strategy classes, each with its own gate path:

| Class | Gate Path | When to Use |
|-------|-----------|-------------|
| A. Per-Token Signal | 0→1→2→3→4→5→6→7 | Single signal on individual tokens |
| B. Portfolio Strategy | 0→2→3P→5P→6→7 | Cross-token ranking, sector rotation, pairs (V3 modules) |
| C. Overlay | 0→2→3O→5O→6→7 | Regime weighting, signal agreement, risk scaling |
| D. V4 Portfolio Strategy | 0→2→V4-3→V4-4→V4-5→6→7 | V4 portfolio strategies — standalone, complement, or replacement |

**Choose class at Gate 0.** The class determines which gates you hit.

**When to use D (V4) vs B (V3 Portfolio):**

| Use V4 (Class D) | Use V3 (Class B) |
|-------------------|-------------------|
| Perp/combined strategies (standalone or complement) | Cross-sectional/sector ranking hypotheses |
| Bidirectional strategies (long+short) | Spot-only cross-token strategies |
| New portfolio baselines or complements to existing | Strategy requires per-token robustness proof |
| Shared capital + concentration limits needed | Independent V3-engine portfolio strategies |

## Gate Process

### Autonomous Mode (AIPIP-0020)

**When an active mission exists** (`status: active` in `.strategy-mission`), gates are
traversed autonomously without stopping for user approval:

- **Mission `kill_if` criteria are HARD kills.** No conditional passes, no judgment calls.
  If any mission kill rule fails, the strategy is killed immediately. Document and move
  to the next candidate.
- **Gate criteria are SOFT gates.** If a gate metric is closely met (within ~10-20% of
  threshold), grant a conditional pass with documented reasoning. The agent decides.
- **Iterate autonomously.** If a strategy is killed, proceed immediately to the next
  candidate idea from Gate 0. Screen multiple ideas, develop the best ones, kill fast.
- **Stop at Gate 6 (paper trading).** Present all results to the user before deploying:
  full gate history, final metrics vs mission baselines, any soft passes and reasoning.
- **Stop on genuine ambiguity.** If borderline on multiple mission criteria simultaneously,
  ask the user rather than guessing.

**When no active mission exists**, pause at each gate transition for user approval
(original behavior).

### At Each Gate

1. Read `knowledge/STRATEGY_QUICK_REFERENCE.md` — your current gate's section
2. Run the specified checks
3. Run the bias audit checklist (bottom of quick reference)
4. Output a gate report (format below)
5. Capture findings to `findings/strategy-findings.jsonl`
6. Update the gate state file
7. If autonomous mode: proceed immediately to next gate (or next candidate if killed)

### Gate Report Format (mandatory at every transition)

```
## Gate N: [NAME] — [PASS / SOFT PASS / KILL]

| Metric | Value | Threshold | Status |
|--------|-------|-----------|--------|
| [metric] | [value] | [threshold] | PASS/SOFT PASS/FAIL |

**Mission criteria (if active):**
| Kill Rule | Value | Threshold | Status |
|-----------|-------|-----------|--------|
| [mission kill_if] | [value] | [threshold] | PASS/FAIL |

**Soft pass justification (if any):** [why shortfall is acceptable]
**Bias audit:** [CLEAN / issues found]
**Decision:** [PROCEED to Gate N+1 / KILL: reason / ITERATE: next candidate]

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

### Candidate Ranking Table (AIPIP-0021)

On ANY kill at V4-Gate 3+ (or Gate 4+ for per-token), append the strategy to
`.claude/.strategy-candidates`. This tracks strategies that showed promise but failed
gate or mission criteria — the user can review to evaluate if criteria are too strict
or to resurrect candidates.

```yaml
# Append to .claude/.strategy-candidates
- id: sNN_name
  killed_at: v4gate5      # gate where killed
  killed_by: mission       # "mission" or "gate"
  kill_reason: "Specific reason with numbers"
  metrics:
    sharpe: X.XX
    calmar: X.XX
    max_dd: -X.XX
    sortino: X.XX
    total_return_pct: XXXXX
    total_trades: NNNN
  portfolio_metrics:       # if portfolio test was run
    sharpe_delta: +/-X.XX
    calmar_delta: +/-X.XX
    max_dd_delta: +/-X.XX
  composite_score: X.X     # 0.35*calmar + 0.25*sortino + 0.25*return + 0.15*sharpe (normalized)
  date: "YYYY-MM-DD"
```

**Rules:**
- Always append, never overwrite existing entries
- `killed_by: mission` = passed gate thresholds but failed mission `kill_if`
- `killed_by: gate` = failed the gate's own thresholds
- `killed_by: adversarial_review` = failed Gate 5.75 review
- Sort by composite score descending when displaying to user

---

## GATE 0: Idea Screening (< 5 min)

### Step 0: Load Project State

```
READ: .claude/.strategy-mission — FIRST. Check if file exists and has `status: active`.
      If active: load search goals, baselines, kill criteria, previous attempts, promising directions.
      If file doesn't exist or status is closed: proceed without mission context.
READ: memory/PROJECT_STATUS.md — open tasks, capability inventory, strategy tiers, key findings
READ: knowledge/STRATEGY_QUICK_REFERENCE.md — "Available Capabilities" + "Gate 0" sections
READ: memory/RESEARCH_COORDINATOR.md — research coordination role, auto-research tools, anti-patterns
READ: memory/RESEARCH_STATUS.md — signal scoreboard (skim Signal Scoreboard table for GOLD/PASS/KILLED verdicts)
```

This tells you what's been built, what's broken, what you're searching for, what tools you have,
and what signals have already been tested (so you don't repeat killed research).

**If an active mission exists:** The mission defines the current strategy search goal, baselines
to beat, and mission-specific kill criteria. These are ADDITIVE to standard gate thresholds —
a strategy must pass both. Use the mission to:
- Screen ideas at Gate 0 (kill ideas that can't meet mission baselines)
- Apply mission `kill_if` rules at every subsequent gate (V4-Gate 3/4/5 especially)
- Avoid repeating `previous_attempts` listed in the mission
- Focus on `promising_directions` the mission identifies

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
| D. V4 Portfolio Strategy | 0→2→V4-3→V4-4→V4-5→6→7 | "[Strategy] generates alpha in [regime/market] because [mechanism]" |

**Portfolio subtypes:** cross-sectional momentum, sector rotation, pairs/stat arb, dynamic factor
**Overlay subtypes:** regime weighting, signal agreement, rebalancing rules, risk scaling
**V4 subtypes:** standalone alpha, funding harvester, bidirectional momentum, vol reversal, portfolio complement

### Step 2: Write Hypothesis

Use the template for your class. Must include:
- Signal/method description
- Economic mechanism (why should this work?)
- Expected holding period or rebalance frequency

### Step 3: Check Graveyard + Tier C + Mission Previous Attempts

Read `strategies/GRAVEYARD.md` and Tier C list in Quick Reference.
Kill if: already tried with no new evidence.

**If active mission exists:** Also check `.strategy-mission` `previous_attempts` section.
Kill if the idea uses the same approach as a previous attempt with no new evidence or mechanism.

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
# V4 Portfolio: skip Gate 1, proceed to Gate 2
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
4. Kill if: IC <0.02, t-stat <2.0 (or t >3.4 if N strategies >37, Harvey-Liu), hit rate <55%, PF <1.3, trades <50
5. **NEW:** Check ICIR (IC / std(IC)) > 0.3 — stability matters more than raw IC (Qian/Hua/Sorensen 2007)
6. **NEW:** Check IC decay class from `outputs/signal_discovery/rolling_ic_summary*.csv` — prefer STABLE signals
7. **NEW:** Check lead/lag asymmetry from `outputs/signal_discovery/lead_lag*.json` — signal must be LEADING
8. Recycle: PF >1.1 but borderline → ONE retry

**Harvey-Liu multiple testing adjustment:** When total strategies+signals tested exceeds
N=37, required t-stat rises to `t_adj = sqrt(2 * ln(N))` ≈ 3.4. Apply to NEW signals
only — existing Tier A strategies are grandfathered.

**Signal stability hierarchy:**

| Class | Drift Rate | Examples | Use As |
|-------|-----------|---------|--------|
| STABLE | < 0.001/yr | Cross-TF divergence, vol clustering | Primary entry signals |
| DECAYING | 0.001-0.01/yr | Regime-conditional EMAs, RSI patterns | Use with caution |
| DEAD | > 0.01/yr | Microstructure, order flow proxies | Avoid (post-ETF killed) |

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
# V4 Portfolio: proceed to V4-Gate 3
echo "v4gate3" > "$CLAUDE_PROJECT_DIR/.strategy-gate"
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

### What to Do
1. Identify base strategy to overlay (must be Tier A/B)
2. Use existing module or create new one:
   - Regime weighting: `v3/regime_analysis.py`
   - Signal agreement: `v3/signal_agreement.py`
   - New overlay: create `v3/new_overlay.py`
3. Check:

| Criterion | Threshold | Kill |
|-----------|-----------|------|
| Base strategy identified? | Must specify which Tier A/B strategy | No base = kill |
| Module exists? | Use existing if possible | Justify if new |
| Hypothesis testable? | Can measure improvement on historical data | Untestable = kill |

```bash
echo "gate5o" > "$CLAUDE_PROJECT_DIR/.strategy-gate"
```

---

## V4-GATE 3: V4 Prototype + Full-History Backtest (< 15 min)

**V4 Portfolio strategies only (Class D).** Uses `v4/portfolio_backtest.py`.

**Read:** Quick Reference — "V4 Portfolio" section + `strategies/TEMPLATE.py` (bidirectional perp example)

**Mission criteria (if active mission loaded at Gate 0):** Apply mission `kill_if` rules
in addition to gate thresholds below. Mission criteria are ADDITIVE — strategy must pass
both gate thresholds AND mission criteria.

### What to Do
1. Copy `strategies/TEMPLATE.py` → `strategies/sNN_name.py` (use bidirectional perp template)
2. ALL code vectorized — no Python for-loops over bar arrays
3. Run V4 full-history backtest (use max available data — currently 72 months):
   ```bash
   /workspace/venv/bin/python v4/portfolio_backtest.py --strategy sNN --months 72 --capital 200000
   ```
4. Record portfolio metrics:

| Criterion | Threshold | Kill |
|-----------|-----------|------|
| Full-history total return | > +50% | < +50% |
| Portfolio Calmar | > 0.5 | < 0.5 |
| Max drawdown | < 25% | > 25% |
| Trade count | > 100 | < 100 |
| Vectorized (< 1ms/call) | Yes | No |

**Why 72 months:** Covers COVID crash, 2021 bull, 2022 bear, 2023 recovery, 2024 ETF rally,
2025-2026 consolidation. Walk-forward handles regime adaptation; deep data prevents overfitting.

5. Record per-regime metrics (report, not kill):

| Regime | Metric to Record |
|--------|-----------------|
| UPTREND (2) | Return, Sharpe |
| DOWNTREND (4) | Return, Sharpe |
| RANGE (3) | Return, Sharpe |
| QUIET (1) | Return, Sharpe |
| CRISIS (0) | Return, max DD |

```bash
echo "v4gate4" > "$CLAUDE_PROJECT_DIR/.strategy-gate"
```

---

## V4-GATE 4: V4 OOS Validation — Train→Dec, Trade Jan-Mar (< 10 min)

**V4 Portfolio strategies only (Class D).** The critical sideways market survival test.

**Mission criteria (if active mission loaded at Gate 0):** Apply mission `kill_if` rules
in addition to gate thresholds below (e.g., OOS PnL must beat mission baseline, March
daily PnL must exceed mission threshold).

**Method:** Adjust `config.train_bars` to end training on Dec 31 2025. Strategy trades
OOS from Jan 1 to present (~Mar 10).

| Criterion | Threshold | Kill |
|-----------|-----------|------|
| OOS total return | > 0% (positive) | Negative |
| OOS months positive | >= 2 of 3 | < 2 of 3 |
| OOS max drawdown | < 30% | > 30% |
| March 2026 PnL | > -5% | < -5% (sideways stress test) |

**Sideways market checklist (mandatory for Class D):**
- [ ] Uses perp or combined market (bidirectional for short capability)
- [ ] Profitable in RANGE and QUIET regimes (not just UPTREND)
- [ ] Shows positive March 2026 PnL (sideways stress test)
- [ ] Low correlation with s56 (momentum) and s57 (carry)

```bash
echo "v4gate5" > "$CLAUDE_PROJECT_DIR/.strategy-gate"
```

---

## V4-GATE 5: Portfolio & Supremacy Evaluation (< 15 min)

**V4 Portfolio strategies only (Class D).** Three evaluation modes — run ALL, pick best.

**Mission criteria (if active mission loaded at Gate 0):** Apply mission `kill_if` rules
in addition to gate thresholds below.

### Evaluation Modes (AIPIP-0021)

Run all three modes. The best configuration wins — a new strategy can complement, replace,
or form a new multi-strategy portfolio.

**Mode A: Portfolio Complement** — Does this strategy improve the current best portfolio?
```bash
# Run: current production strategies + new strategy
# Use whatever strategies are currently in production (check .strategy-mission for baseline)
/workspace/venv/bin/python v4/portfolio_backtest.py --strategy <production_ids>,sNN --months 74 --capital 200000
```

**Mode B: Standalone** — Can the new strategy stand on its own or form a new portfolio baseline?
```bash
# Run: new strategy standalone
/workspace/venv/bin/python v4/portfolio_backtest.py --strategy sNN --months 74 --capital 200000
```

**Mode C: Best Multi-Portfolio** — What's the best combination of ALL available strategies?
```bash
# Run: all viable strategies together (existing + new)
# Test 2-3 promising combinations — including ones that DROP current production components
# The best portfolio wins, even if it doesn't include current production strategies
```

### Gate Criteria (per mode)

| Criterion | Threshold | Kill |
|-----------|-----------|------|
| Portfolio Sharpe delta | > 0 (improves over production) | Sharpe decreases |
| Portfolio MaxDD delta | <= +2pp | MaxDD worsens > 2pp |
| Correlation vs existing | < 0.5 | > 0.7 |
| Marginal Sharpe contribution | > 0 | Negative (drags portfolio) |
| Regime coverage | Covers 2+ regimes | Same regime profile |

### Decision Matrix

| Best Mode | Action |
|-----------|--------|
| Mode A wins | Deploy as complement to existing portfolio |
| Mode B wins | New strategy replaces production baseline. Update mission. |
| Mode C wins | New multi-strategy portfolio becomes production standard. Update mission. |
| None pass | Kill strategy. Append to candidate table. |

**Composite metric for cross-mode comparison:**
```
composite = 0.35 * calmar_norm + 0.25 * sortino_norm + 0.25 * return_norm + 0.15 * sharpe_norm
```
Where each metric is normalized `(value - min) / (max - min)` across all configurations tested.

If Mode B or C wins, the agent updates `.strategy-mission` baseline to reflect the new
best configuration. The old baseline becomes a historical reference.

**Deep dive if needed:** `knowledge/process/STRATEGY_PIPELINE_GATES.md` (V4 OOS template)

```bash
echo "gate575" > "$CLAUDE_PROJECT_DIR/.strategy-gate"
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

## GATE 5.75: Adversarial Quant Review (AIPIP-0021)

**All strategy classes.** Mandatory after passing Gate 5 / 5P / 5O / V4-5. Before Gate 6.

**Purpose:** Independent adversarial challenge. The reviewing subagent's job is to FIND
reasons the strategy will fail in production. It receives ONLY the strategy source code,
backtest metrics, and equity curve — NO development reasoning, gate history, or
justifications (prevents anchoring on the developer's confirmation bias).

### How to Run

Launch a subagent (Task tool, `subagent_type: "general-purpose"`) with:
- Strategy source file path
- Backtest metrics JSON
- Equity curve JSON (if available)
- Instruction: "You are an adversarial quant reviewer. Your job is to find reasons this
  strategy will fail. Read the strategy code line by line and check every item below."

### Bias & Look-Forward Audit (mandatory — check every line of strategy code)

| Bias Type | What to Check | How to Detect |
|-----------|--------------|---------------|
| **Look-ahead bias** | Does any signal use future data? | Trace every array: is `close[i]` ever compared to `close[i+k]` where k>0? Check rolling windows use only past bars. Check funding/indicator windows are trailing, not centered. |
| **Survivorship bias** | Are only winning tokens in the universe? | Check if token universe includes only tokens that survived to present. |
| **Selection bias** | Was this signal picked BECAUSE it worked on this data? | Count total signals tested (Harvey-Liu N). If N>37, t-stat must exceed 3.4. |
| **Regime overfitting** | Does the strategy only work in 1 regime? | Check PnL distribution across regimes. If >70% from one regime → fragile. |
| **Parameter sensitivity** | Do small changes break it? | Check: +/-20% on each parameter. If Calmar degrades >50% → overfit. |
| **Indexing errors** | Off-by-one in warmup, entry/exit bars? | Verify warmup guard matches indicator window. Check exit signals don't peek at current bar close. |
| **Funding model realism** | Are funding costs accurately modeled? | Compare assumed funding rate to actual historical distribution. |
| **Execution assumptions** | Can trades actually be filled at these prices? | Check if entry/exit prices assume best-case fills. Slippage model present? |

### Structural Risk Assessment

| Check | What to Look For |
|-------|-----------------|
| Tail risk / short gamma | Is the strategy systematically selling insurance? Positive skew or negative? |
| Capacity constraint | At $1M+ capital, does market impact eat the edge? ADV checks? |
| Correlation stability | Does correlation with existing portfolio hold in drawdowns? |
| Crowding risk | Is this a well-known strategy that could get crowded? |
| Regime dependency | If the regime detector is wrong, does the strategy blow up? |
| Concentration risk | Does >50% PnL come from <5 tokens? |

### Output

The subagent outputs a structured report with line references for every concern found.

| Verdict | Meaning | Action |
|---------|---------|--------|
| **PASS** | No biases or structural risks found | Proceed to Gate 6 |
| **CONDITIONAL PASS** | Minor concerns documented | Proceed to Gate 6 with caveats noted |
| **FAIL** | Critical bias or structural risk found | Kill. Append to candidate table with `killed_by: adversarial_review` |

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

### Single Deployment Path (AIPIP-0022 + AIPIP-0024)

There is exactly ONE paper trading deployment path. **NEVER create new runner scripts,
standalone launchers, or alternative dashboard paths.**

| Component | Single Path | Forbidden |
|-----------|-------------|-----------|
| Runner | `v4/run_paper_multi.py` | Creating new `run_paper*.py` files |
| Config | `configs/multi_v4_paper.json` | Per-strategy standalone configs for running |
| Dashboard | Runner pushes after each tick; CLI reads same config | Filesystem glob discovery, `--once` mode |
| State | `state/v4_paper_*/` directories | Running engines outside the multi-runner |

### Deployment Checklist

1. **Add pool to multi-runner config** (`configs/multi_v4_paper.json`):
   - Read the existing config first — NEVER overwrite it
   - APPEND the new portfolio entry to the `"portfolios"` array
   - Set a unique `state_dir` (e.g., `state/v4_paper_sNN/`)
   - Verify the config has ALL existing portfolios plus the new one

2. **Create state directory** and copy config:
   ```bash
   mkdir -p state/v4_paper_sNN
   # Create config.json with the pool's strategy list — must match
   # the entry you just added to multi_v4_paper.json
   ```

3. **Kill the existing runner and verify only ONE process exists:**
   ```bash
   # Find and kill ALL runner processes (stale ones accumulate)
   pkill -f "run_paper_multi" && sleep 2
   # Verify clean — this MUST return empty
   ps aux | grep run_paper_multi | grep -v grep
   # Clean PID lock
   rm -f state/v4_paper_multi/paper.pid
   ```

4. **Start the runner:**
   ```bash
   nohup /workspace/venv/bin/python -m v4.run_paper_multi \
     --config configs/multi_v4_paper.json > /tmp/paper_multi.log 2>&1 &
   ```

5. **Verify deployment (ALL must pass before setting gate6):**
   ```bash
   # a) Config contains new pool
   grep "pool_name.*sNN" configs/multi_v4_paper.json
   # b) Only ONE runner process
   ps aux | grep run_paper_multi | grep python | grep -v grep | wc -l  # must be 1
   # c) Runner started with all pools
   head -1 /tmp/paper_multi.log  # must list ALL portfolio names
   # d) Dashboard CLI produces same ordering as runner
   python tools/generate_dashboard_v2.py 2>&1 | head -10
   ```
   - After first tick: dashboard must show all portfolio tabs
   - New portfolio should start with $200k equity and no prior positions

### Metrics to Track
1. Deploy on live data, simulated execution
2. Min 50 trades before go-live
3. Kill if: returns <60% backtest, slippage >50% edge, MaxDD >1.5x, <10 trades

**Degradation budget (Suhonen et al. 2017):**
- 215 strategies across 17 banks: median 73% Sharpe deterioration
- Budget **50-60% Sharpe degradation** backtest → live
- Allow settling period: **0.4x Sortino first 2 weeks**, 0.6x steady state
- Example: s58 backtest Sharpe 7.29 → expected live Sharpe **2.9-4.4**
- If paper Sharpe < 2.9 → investigate; if < 1.5 → kill

**Deep dive if needed:** `knowledge/PAPER_TRADING_PRO_FRAMEWORK.md`

### Mission Lifecycle Review (mandatory if active mission exists)

After deploying a strategy to paper trading, the agent MUST:

**Step 1: Draft a mission evolution** (AIPIP-0021) — always produce this before asking the user.

```
**Mission Evolution Draft:**

Based on [N] strategies tested, [M] killed, [K] deployed this cycle:

**Current best configuration:** [strategy/portfolio that won at V4-Gate 5]
  all_time: [metrics]
  oos_3mo: [metrics]

**Proposed new baseline:** (if new config beats current mission baseline)
  [Updated baseline with the new best metrics]

**Proposed kill_if adjustments:**
  [List each criterion with proposed change and reasoning]
  Example: "Relax MaxDD from 3% to 5% — current production baseline is 3.93%,
  making <3% impossible to improve by addition. N of M candidates killed by this."

**Promising directions for next cycle:**
  [Updated based on what signal types worked/failed, which regimes need coverage]

**Candidate resurrection recommendations:**
  [Strategies from .strategy-candidates that would pass relaxed criteria, with scores]
```

**Step 2: Present to user with options:**

```
**Mission Review:** Strategy sNN deployed to paper trading.

The active mission goal was: [quote mission goal]

1. **Accept evolution draft** — new mission replaces old with updated baselines, criteria,
   and directions. The best configuration becomes the new standard to beat.

2. **Modify draft** — user adjusts the proposed mission before it takes effect.

3. **Keep mission as-is** — strategy deployed but mission goal not yet fully met.
   Continue searching with current criteria in next /strategy session.

4. **Close mission** — goal achieved or abandoned, no further search needed.
   Logs closure to findings/strategy-findings.jsonl with outcome summary.
```

Use `AskUserQuestion` with these four options. Based on the user's choice:
- **Accept:** Replace `.strategy-mission` with the evolution draft, add sNN to previous_attempts.
- **Modify:** Let user provide changes, then update `.strategy-mission`.
- **Keep:** Add sNN to previous_attempts, keep everything else unchanged.
- **Close:** Set `status: closed` in `.strategy-mission`, append closure finding.

```bash
echo "gate6.5" > "$CLAUDE_PROJECT_DIR/.strategy-gate"
```

---

## GATE 6.5: Knowledge Update (< 10 min) — AIPIP-0023

**All strategy classes.** Mandatory after paper trading deployment confirmed (first tick
logged). Runs immediately — do NOT skip to Gate 7 without completing this.

### What to Do

Update ALL of the following files:

1. **`memory/PROJECT_STATUS.md`**:
   - Add strategy to V4 Validated or V4 Production table
   - Add key findings to Key Findings section (numbered, continue sequence)
   - Update "Active" line in header
   - Add any killed strategies to Graveyard table

2. **`knowledge/STRATEGY_QUICK_REFERENCE.md`**:
   - Add strategy to appropriate dedup table (Per-Token Perp, Combined, etc.)
   - Update Paper Trading table with new pool
   - Update Tier C list if any strategies were killed during this cycle
   - Update Key Constraints if new edge families were validated

3. **`.claude/.strategy-mission`**:
   - Update baselines with new portfolio metrics
   - Add strategy to Previous Attempts section
   - Update kill criteria if new baselines are higher
   - Update edge family coverage
   - If mission is achieved, change status to `paper-trading-monitoring`
   - Add concrete Next Actions

4. **`findings/strategy-findings.jsonl`**:
   - Verify all gate findings were logged (should already be done at each gate)
   - Curate if >50 entries since last curation

5. **`strategies/GRAVEYARD.md`**:
   - Add any strategies killed during this development cycle

### Gate Report Format

```
## Gate 6.5: Knowledge Update — DONE

| File | Updated | Changes |
|------|---------|---------|
| PROJECT_STATUS.md | Yes | Added sNN to V4 Validated, findings NN-NN, graveyard entries |
| STRATEGY_QUICK_REFERENCE.md | Yes | Added sNN to Perp table, updated Paper Trading table |
| .strategy-mission | Yes | Updated baselines, added attempts, status → monitoring |
| findings/strategy-findings.jsonl | Yes | N entries logged, no curation needed |
| GRAVEYARD.md | Yes | Added sNN_killed_strategy |

**All knowledge files current. Ready for next development cycle.**
```

```bash
echo "gate7" > "$CLAUDE_PROJECT_DIR/.strategy-gate"
```

---

## GATE 7: Production + Decay (ongoing)

**Read:** Quick Reference — "Gate 7" section

1. Allocation: 1-5% target, 1/4 Kelly, <5% ADV
2. Circuit breakers: -3% daily halt, -15% pull
3. Monthly decay (thresholds): Sortino <0, PF <0.8, no high 6mo → escalate

**BOCPD decay detection (Adams & MacKay 2007):**
- Run alongside threshold checks for structural vs noise discrimination
- Monitor rolling 30d Sharpe + rolling 30d return with BOCPD (hazard rate 1/90)
- **Threshold fires + BOCPD fires** → Structural decay. Pull from production.
- **Threshold fires + BOCPD silent** → Normal drawdown noise. Reduce 25%, continue.
- **BOCPD fires + threshold OK** → Early warning. Investigate regime change.

**Deep dive if needed:** `knowledge/process/RISK_PORTFOLIO_CONSTRUCTION.md`,
`knowledge/KRAKEN_FEES.md`

---

## Strategy Graveyard

When killed at any gate:
```bash
echo "$(date -I) | sNN_name | Killed at Gate X | Reason: [specific reason]" >> "$CLAUDE_PROJECT_DIR/../strategies/GRAVEYARD.md"
```
