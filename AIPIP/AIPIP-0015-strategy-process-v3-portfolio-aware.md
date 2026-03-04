---
id: AIPIP-0015
title: Strategy Process v3 — Portfolio-Aware Gates + Continuous Feedback Loop
status: accepted
author: @claude
created: 2026-03-04
supersedes: AIPIP-0014 (partially — extends, does not replace per-token gates)
---

# AIPIP-0015: Strategy Process v3 — Portfolio-Aware Gates + Continuous Feedback Loop

## Problem

Two related problems:

### Problem 1: New Capabilities Are Invisible

AIPIP-0014 established an 8-gate pipeline for strategy development. It works well for **per-token time-series strategies** (s07-s28). However, since its acceptance, we've built 6 major new modules that represent fundamentally different strategy types and portfolio tools:

| Module | Type | Results | In Process? |
|--------|------|---------|-------------|
| Cross-Sectional Momentum | Portfolio strategy | Sharpe +1.54, Tier A diversifier | No |
| Sector Rotation | Portfolio strategy | Sharpe +1.31, Tier A diversifier | No |
| Pairs Trading | Portfolio strategy | Sharpe +0.42, Tier B diversifier | No |
| Regime Weighting | Overlay | Sharpe +0.29 improvement | No |
| Signal Agreement | Overlay | Calmar +0.46 improvement | No |
| Dynamic Universe | Infrastructure | Bias quantified at +0.2-2.7% | No |

**These capabilities are invisible to the strategy process.** When `/strategy` is invoked and the agent reads the quick reference, it sees only per-token strategies and per-token gates. It has no idea it can:

- Build cross-sectional portfolios that rank tokens against each other
- Create sector rotation strategies across 10 thematic categories
- Design pairs trades using cointegration on perpetual futures
- Apply regime-conditional allocation to improve any strategy's risk profile
- Use signal agreement gating to filter for high-conviction entries
- Test portfolio-level metrics (shared cash pool, concentration caps)

### Specific Process Gaps

1. **Gate 0 (Idea Screen):** Only prompts for single-token signal hypotheses. No template for "I want to build a portfolio-level strategy" or "I want to add an overlay to existing strategies."

2. **Gate 1 (Signal Lab):** IC screening assumes a single signal on 49 tokens. Cross-sectional strategies don't have per-token IC — they have portfolio-level return/drawdown metrics.

3. **Gate 2 (Dedup):** Checks "entry signal overlaps >80% with existing." For a pairs trading strategy or sector rotation, this check is meaningless — they're structurally different from any per-token strategy.

4. **Gate 3 (Prototype):** Assumes `strategies/sNN_name.py` with a 6-layer signal stack. Portfolio strategies use completely different architectures (`v3/cross_sectional.py`, `v3/pairs_trading.py`).

5. **Gates 4-5 (Validation):** Run `v3/validation.py --strategy sNN --tokens BTC` then `--workers 4`. Portfolio strategies don't work this way — they have their own CLI interfaces and produce portfolio-level metrics, not per-token validation rates.

6. **Gate 6 (Paper Trading):** Infrastructure exists but the process doesn't describe how to paper-trade a cross-sectional or pairs strategy (they need different instance configs than per-token strategies).

7. **No Portfolio Assembly Gate:** There's no guidance on how to combine Tier A per-token strategies with diversifiers and overlays into an optimal portfolio. The correlation analysis tool exists but isn't referenced.

8. **Quick Reference doesn't mention capabilities:** The agent's primary reference document lists only per-token strategies. No mention of portfolio modules, overlays, or the tools that enable them.

### Impact of Not Fixing

- **Wasted ideation cycles:** Agent proposes more per-token momentum strategies (which are saturated at r=0.62) instead of exploring diversifiers
- **Hidden alpha:** Regime weighting (+0.29 Sharpe) and signal agreement (+0.46 Calmar) are proven improvements that won't be applied
- **No portfolio optimization:** Strategies get validated individually but never assembled into an optimal portfolio
- **Capability amnesia:** Each session starts fresh, doesn't know these modules exist, rediscovers them by accident (or doesn't)

### Problem 2: Strategy Work Produces Findings That Die With the Session

Every strategy run through the gates generates valuable insights — new capabilities unlocked, bugs found, data gaps discovered, signal limitations identified, correlation findings, cost model refinements. **None of this feeds back into the process automatically.**

Current feedback paths and their failures:

| Finding Type | Where It's Recorded | Fed Back Automatically? |
|-------------|--------------------|-----------------------|
| Gate kill/pass decision | Agent's chat output | **No** — lost when session ends |
| Validation results | `results/{strategy}_{ts}.json` | **No** — files accumulate, nothing reads them |
| New capability built | Code exists in `v3/` | **No** — not in skill or quick reference |
| Tier classification | `STRATEGY_LIFECYCLE.md` (manual) | **Partially** — only if agent updates the file |
| Killed strategy | `GRAVEYARD.md` (manual) | **No** — not checked at Gate 0 |
| Correlation findings | Agent memory | **No** — lost when session ends |
| Data gaps discovered | Nowhere | **No** — rediscovered next time |
| Process improvement ideas | Nowhere | **No** — user has to remind agent |

**The result:** Each session starts nearly from scratch. The agent rediscovers things the previous session already knew. The user must manually remind the agent what capabilities exist, what was tried, and what the current portfolio looks like. This is the opposite of compounding — it's forgetting.

**What should happen (the OODA loop):**

```
Strategy Run (observe) → Gate Outcome (orient) → Finding Captured (decide) → Knowledge Updated (act)
     ↑                                                                              |
     └──────────────── Next session starts with updated context ────────────────────┘
```

Reference: Stanford/SambaNova ACE framework (arXiv:2510.04618) — Generate-Reflect-Curate pattern for evolving agent knowledge. The key insight: capture at the finest grain (per-gate), curate periodically, consume at the appropriate grain (per-context).

## Proposal

### Part A: Portfolio-Aware Gates (Visibility)

### 1. Add Strategy Classes to Gate 0

Extend the Idea Screen with three strategy classes, each with its own hypothesis template:

```
STRATEGY CLASSES:

A. Per-Token Signal (existing)
   Hypothesis: "[Signal] predicts [direction] on [token] over [holding period] because [mechanism]"
   Gate path: 0 → 1 → 2 → 3 → 4 → 5 → 6 → 7

B. Portfolio Strategy (NEW)
   Hypothesis: "[Ranking/selection method] across [universe] produces alpha because [mechanism]"
   Types: cross-sectional momentum, sector rotation, pairs/stat arb, dynamic factor
   Gate path: 0 → 2 → 3P → 5P → 6 → 7
   (Skip Gate 1 IC screen — portfolio strategies don't have per-token IC)
   (Gates 3P/5P are portfolio-specific variants — see below)

C. Overlay / Combination (NEW)
   Hypothesis: "Applying [overlay] to [base strategy] improves [metric] because [mechanism]"
   Types: regime weighting, signal agreement, rebalancing rules, risk scaling
   Gate path: 0 → 2 → 3O → 5O → 6 → 7
   (Skip Gate 1 — overlays modify allocation, not signals)
   (Gates 3O/5O are overlay-specific variants — see below)
```

### 2. Add Portfolio-Specific Gates

**Gate 3P: Portfolio Prototype + Performance**

| Criterion | Threshold | Kill |
|-----------|-----------|------|
| Uses existing module? | Extend `v3/cross_sectional.py`, `v3/sector_rotation.py`, or `v3/pairs_trading.py` | If new module needed, justify |
| Rebalance frequency | Weekly or slower | Daily = too much turnover |
| Universe coverage | >=20 tokens eligible | <20 = insufficient breadth |
| Turnover | <50% per rebalance | >50% = fee drag kills edge |
| Vectorized? | Yes | For-loops over bars = kill |

**Gate 5P: Portfolio Full Validation**

| Criterion | Threshold | Kill |
|-----------|-----------|------|
| Calmar ratio | >0.5 | <0.5 |
| Max drawdown | <40% | >40% |
| Annual return | >20% | <20% |
| Trade count | >200 total | <200 |
| **Correlation vs best Tier A** | **<0.5** | **>0.7 = no diversification value** |
| Turnover-adjusted Sharpe | >0.8 after costs | <0.8 |

Note: Portfolio strategies are NOT tiered by "validation rate across 49 tokens" — they produce a single portfolio equity curve. Tier assignment uses portfolio-level metrics directly.

### 3. Add Overlay-Specific Gates

**Gate 3O: Overlay Prototype**

| Criterion | Threshold | Kill |
|-----------|-----------|------|
| Base strategy identified? | Must specify which Tier A strategy to overlay | No base = kill |
| Module exists? | `v3/regime_analysis.py`, `v3/signal_agreement.py`, or new | Justify if new |
| Hypothesis testable? | Can measure improvement on historical data | Untestable = kill |

**Gate 5O: Overlay Validation**

| Criterion | Before Overlay | After Overlay | Kill If |
|-----------|---------------|---------------|---------|
| Calmar | baseline | must improve | Calmar degrades |
| Max drawdown | baseline | must improve or hold | DD worsens >2pp |
| Sharpe | baseline | should improve | Sharpe degrades >0.1 |
| Trade count | baseline | may decrease | <50 trades remain |
| Regime robustness | — | works in 2+ regimes | Only works in 1 regime |

### 4. Add Portfolio Assembly Gate (New Gate 5.5)

After all component strategies and overlays pass their respective Gate 5:

```
GATE 5.5: PORTFOLIO ASSEMBLY (< 30 min)

Required:
  1. Run v3/correlation.py on all candidate strategies
  2. Compute marginal Sharpe contribution of each
  3. Test allocation methods: equal-weight, risk parity, regime-weighted
  4. Simulate combined portfolio with shared cash pool (v3/portfolio.py)

Metrics:
  | Metric | Target |
  |--------|--------|
  | Portfolio Calmar | > 1.0 |
  | Portfolio max DD | < 20% |
  | Effective N (strategies) | > 3.0 |
  | Max pairwise correlation | < 0.7 |
  | Worst regime (DOWNTREND) PnL | > -5% annualized |

Output:
  - Allocation table: strategy → weight
  - Expected portfolio metrics (Calmar, Sortino, DD)
  - Regime performance heatmap
  - Recommended overlays (if any)
```

### 5. Update Quick Reference

Add a new section at the top of `STRATEGY_QUICK_REFERENCE.md`:

```markdown
## Available Capabilities (What You Can Build)

Before ideating, know what tools exist:

### Strategy Types
| Type | Module | Example |
|------|--------|---------|
| Per-token signal | `strategies/sNN_*.py` | s11 momentum burst |
| Cross-sectional ranking | `v3/cross_sectional.py` | Long top quintile by trailing return |
| Sector rotation | `v3/sector_rotation.py` | Long top 2 sectors by category momentum |
| Pairs / stat arb | `v3/pairs_trading.py` | Cointegrated pairs on perps, z-score entry |

### Overlays
| Overlay | Module | Effect |
|---------|--------|--------|
| Regime weighting | `v3/regime_analysis.py` | Scale allocation by BTC regime (0% crisis → 100% uptrend) |
| Signal agreement | `v3/signal_agreement.py` | AND/N-of-M gating for high-conviction entries |

### Portfolio Tools
| Tool | Module | Purpose |
|------|--------|---------|
| Portfolio simulation | `v3/portfolio.py` | Shared cash pool, realistic capital |
| Correlation analysis | `v3/correlation.py` | Pairwise corr, marginal Sharpe, greedy selection |
| Dynamic universe | `v3/dynamic_universe.py` | Point-in-time token eligibility |

### Market Capabilities
| Capability | Details |
|-----------|---------|
| Spot trading | Long only, Binance 116 tokens |
| Perpetual futures | Long AND short, leverage, funding rates |
| Exchanges | Binance, Kraken, Hyperliquid (different fee tiers) |
| Data | 1H candles, 2020-2026, merged in data/1h_cache/ |

### Known Constraints
- All 6 Tier A per-token strategies are momentum variants (median r=+0.62)
- To improve portfolio Sharpe, need DIFFERENT strategy families, not more momentum
- Cross-sectional (+0.25 corr), sector rotation (+0.20), pairs (-0.06) provide genuine diversification
```

### 6. Update Gate 2 Dedup Rules

Current rule only checks entry signal overlap. Add:

```markdown
## Gate 2 Dedup — Extended Rules

**Per-token strategies (existing):**
- Entry signal overlaps >80% with existing Tier A/B → KILL
- Signal type saturated (2+ in Tier A) → KILL

**Portfolio strategies (NEW):**
- Compare strategy CLASS, not entry signal (cross-sectional vs sector vs pairs)
- Check correlation vs existing portfolio strategies: >0.7 → KILL
- If same class exists (e.g., another cross-sectional variant): must show
  improvement on Calmar or max DD, not just different parameters

**Overlays (NEW):**
- Check if overlay already applied to base strategy
- Multiple overlays on same base OK if they target different aspects
  (regime = allocation, signal agreement = entry, risk scaling = position size)
```

### 7. Update SKILL.md

Add strategy class selection to the Gate 0 instructions:

```markdown
## GATE 0: Idea Screening (< 5 min)

**Step 1: Choose strategy class**
Read `knowledge/STRATEGY_QUICK_REFERENCE.md` — "Available Capabilities" section.

| Class | Gate Path | Example |
|-------|-----------|---------|
| A. Per-Token Signal | 0→1→2→3→4→5→6→7 | "RSI divergence predicts reversal on BTC" |
| B. Portfolio Strategy | 0→2→3P→5P→6→7 | "Top-3 sector rotation beats equal-weight" |
| C. Overlay | 0→2→3O→5O→6→7 | "Regime weighting improves S11 Calmar" |

**Step 2: Write hypothesis** (use template for your class)
**Step 3: Check graveyard + Tier C** (don't repeat failures)
**Step 4: Score idea** (>=5/10 to proceed)
```

### 8. Hook `memory/PROJECT_STATUS.md` Into Session Resume and Strategy Startup

**Problem:** `memory/PROJECT_STATUS.md` captures project state (open tasks, capabilities, tiers, findings) but neither `session-resume.sh` nor the `/strategy` SKILL.md reads it. The agent starts each session blind to what's been built and what's outstanding.

**Fix A: Update `session-resume.sh`** to emit the open items section:

```bash
# --- Show open tasks from PROJECT_STATUS (if exists) ---
STATUS_FILE="$PROJECT_DIR/memory/PROJECT_STATUS.md"
if [ -f "$STATUS_FILE" ]; then
  echo "=== OPEN TASKS ==="
  # Extract the Open / Outstanding table
  sed -n '/^### Open/,/^###/p' "$STATUS_FILE" | head -20
  echo ""
fi
```

**Fix B: Update `/strategy` SKILL.md Gate 0** to require reading PROJECT_STATUS:

```markdown
## GATE 0: Idea Screening (< 5 min)

**Step 0: Load project state**
READ: `memory/PROJECT_STATUS.md` — open tasks, capability inventory, strategy tiers, key findings.
This tells you what's been built, what's broken, and what tools you have available.
```

**Fix C: Update `/dev` SKILL.md Step 0** to check PROJECT_STATUS for outstanding dev items:

```markdown
## Step 0: Unfinished Work Check

Before starting any new feature:
1. Check `.specs/active/` for active specs (existing)
2. **NEW:** Check `memory/PROJECT_STATUS.md` — "Open / Outstanding" section for known bugs and dev tasks
3. Present open items to user: "There are N open items. Want to tackle one of these, or start something new?"
```

This ensures that:
- Every session start surfaces outstanding work (via hook)
- Every `/strategy` invocation shows available capabilities before ideation
- Every `/dev` invocation reminds about known bugs/tasks before starting new work

### 9. Update Gap Analysis Status

Update `memory/QUANT_PROCESS_GAP_ANALYSIS.md` priority table to reflect current status:

| Priority | Improvement | Status |
|----------|-------------|--------|
| P1 | Portfolio-level backtest | **Done** — `v3/portfolio.py` |
| P2 | Strategy correlation | **Done** — `v3/correlation.py` |
| P3 | Dynamic universe | **Done** — `v3/dynamic_universe.py` |
| P4 | Token selection | **Done** — `v3/universe.py` |
| P5 | Cross-sectional momentum | **Done** — `v3/cross_sectional.py` |
| P6 | Pairs trading | **Done** — `v3/pairs_trading.py` |
| P7 | Sector rotation | **Done** — `v3/sector_rotation.py` |
| P8 | Survivorship-bias-free data | Not started (external dependency) |
| P9 | Signal agreement | **Done** — `v3/signal_agreement.py` |
| P10 | Regime-conditional weighting | **Done** — `v3/regime_analysis.py` |

### Part B: Continuous Feedback Loop (Learning)

### 10. Structured Findings Capture — `findings/strategy-findings.jsonl`

Every gate outcome (pass, kill, recycle) and every significant discovery during strategy work gets captured in an append-only JSONL file. This is the raw source of truth — never edited, only appended.

**Format:**

```jsonl
{"ts": "2026-03-03T23:21:00Z", "strategy": "s28_momentum_burst_perp", "gate": 5, "outcome": "kill", "metrics": {"rate": 6.7, "sharpe": -0.2}, "finding": "Bidirectional momentum on perps killed by funding rate drag on shorts. Spot-only variant (s11) works because no carry cost.", "category": "signal", "affects": ["knowledge/SHORT_STRATEGY_RESEARCH.md", "knowledge/STRATEGY_QUICK_REFERENCE.md"]}
{"ts": "2026-03-03T23:44:00Z", "strategy": "sector_rotation", "gate": "5P", "outcome": "pass", "metrics": {"sharpe": 1.31, "calmar": 1.73, "corr_vs_s11": 0.20}, "finding": "Sector rotation provides genuine diversification (corr +0.20) vs Tier A momentum cluster (+0.62). Privacy and Meme sectors drove most returns.", "category": "capability", "affects": ["memory/PROJECT_STATUS.md", "knowledge/STRATEGY_CATALOG.md"]}
{"ts": "2026-03-04T00:48:00Z", "strategy": "pairs_trading", "gate": "5P", "outcome": "pass_conditional", "metrics": {"sharpe": 0.42, "corr_vs_s11": -0.06}, "finding": "Standalone Sharpe too low for Tier A, but portfolio value exceptional: 50/50 blend cuts DD from -18% to -8%. Classify as Tier B diversifier.", "category": "portfolio", "affects": ["memory/PROJECT_STATUS.md"]}
```

**Categories:** `signal` | `capability` | `portfolio` | `data` | `infra` | `process` | `bug`

**When to capture:**
- Every gate kill or pass (mandatory — added to gate report format)
- When a new module is built or validated
- When a data gap or bug is discovered
- When a process limitation is hit

### 11. Mandatory Debrief Step — Add to Gate Report Format

Update the gate report template to include a findings extraction:

```markdown
## Gate N: [NAME] — [PASS / KILL / RECYCLE]

| Metric | Value | Threshold | Status |
|--------|-------|-----------|--------|

**Decision:** [PROCEED / KILL / RECYCLE]

### Findings (for future sessions)
- [SIGNAL] [one-line insight about the signal that future strategies should know]
- [DATA] [any data gap or limitation discovered]
- [PROCESS] [anything the gate process got right or wrong]

→ Append to `findings/strategy-findings.jsonl`
```

This step is the **Reflect** phase of the ACE loop. It forces the agent to extract the generalizable lesson before moving on, instead of just recording pass/fail.

### 12. Session-Start Injection — Recent Findings in `session-resume.sh`

When in strategy mode, the session-resume hook surfaces the last 5 findings so the agent starts with context:

```bash
# --- Recent strategy findings (strategy mode only) ---
if [[ "$MODE" == "strategy" ]]; then
  FINDINGS_FILE="$PROJECT_DIR/findings/strategy-findings.jsonl"
  if [ -f "$FINDINGS_FILE" ]; then
    echo "=== RECENT STRATEGY FINDINGS ==="
    tail -5 "$FINDINGS_FILE" | /workspace/venv/bin/python3 -c "
import sys, json
for line in sys.stdin:
    try:
        f = json.loads(line.strip())
        print(f'  [{f[\"ts\"][:10]}] {f[\"strategy\"]} gate {f[\"gate\"]}: {f[\"finding\"][:120]}')
    except: pass
" 2>/dev/null
    echo ""
  fi
fi
```

This is lightweight (<1s execution), fires automatically, and ensures the agent sees why recent strategies died before proposing new ones.

### 13. Gate-Aware Context Injection — Extend `workflow-nudge.sh`

When the agent is at a specific gate, inject findings relevant to THAT gate:

```bash
# In workflow-nudge.sh, after injecting gate number:
if [[ "$MODE" == "strategy" ]] && [ -f "$FINDINGS_FILE" ]; then
  CURRENT_GATE=$(cat "$PROJECT_DIR/.strategy-gate" | tr -d '[:space:]' | sed 's/gate//')
  GATE_FINDINGS=$(grep "\"gate\": $CURRENT_GATE" "$FINDINGS_FILE" | tail -3)
  if [ -n "$GATE_FINDINGS" ]; then
    echo "Recent findings at this gate:"
    echo "$GATE_FINDINGS" | /workspace/venv/bin/python3 -c "
import sys, json
for line in sys.stdin:
    try:
        f = json.loads(line.strip())
        print(f'  - {f[\"strategy\"]}: {f[\"finding\"][:100]}')
    except: pass
" 2>/dev/null
  fi
fi
```

**Effect:** At Gate 0, the agent sees "last 3 kills were momentum variants — consider a different family." At Gate 5, it sees "s26/s27/s28 all failed on validation rate < 20%." This is the **Orient** phase — contextualizing the current decision with prior experience.

### 14. Auto-Update PROJECT_STATUS.md on Gate Outcomes

After each gate kill or pass, the strategy skill instructs the agent to update `memory/PROJECT_STATUS.md`:

- **Kill:** Add to graveyard table, update Tier C list if applicable
- **Pass Gate 5:** Add to appropriate tier table (A/B/diversifier/overlay)
- **New capability built:** Add to capability inventory table
- **Bug found:** Add to open items table
- **Data gap found:** Add to blocked items table

This is the **Act** phase — the finding becomes part of the project's persistent state. Since `PROJECT_STATUS.md` is read by session-resume (proposal #8), the update automatically surfaces next session.

### 15. Periodic Curation — Promote Findings to Knowledge Files

Findings accumulate in `strategy-findings.jsonl`. Periodically (every ~10 strategy runs, or when the file exceeds 50 entries), the agent should curate:

1. Read `findings/strategy-findings.jsonl`
2. Group by `affects` target file
3. For each target: check if the finding is already covered. If not, propose a delta edit.
4. Check target file size against knowledge base guidelines (200/400/800 line limits)
5. If target file is over limit: propose archiving older sections before adding new content

**Trigger:** Add to Gate 0 instructions:

```markdown
**Step 0.5: Curate findings (if needed)**
If `findings/strategy-findings.jsonl` has >50 entries since last curation:
1. Review findings grouped by category
2. Promote undocumented insights to their target knowledge files
3. Archive findings older than 6 months with zero "helpful" references
4. Update `memory/PROJECT_STATUS.md` with any changes
```

This prevents the findings file from growing unbounded while ensuring insights don't stay in raw form forever.

### 16. Knowledge Freshness Checks

Add a lightweight freshness check to `session-resume.sh`:

```bash
# --- Knowledge freshness warnings ---
for f in "$PROJECT_DIR"/knowledge/STRATEGY_LIFECYCLE.md \
         "$PROJECT_DIR"/knowledge/STRATEGY_QUICK_REFERENCE.md \
         "$PROJECT_DIR"/memory/PROJECT_STATUS.md; do
  if [ -f "$f" ]; then
    last_mod=$(stat -c %Y "$f" 2>/dev/null || stat -f %m "$f" 2>/dev/null)
    now=$(date +%s)
    age_days=$(( (now - last_mod) / 86400 ))
    if [ "$age_days" -gt 7 ]; then
      echo "WARNING: $(basename $f) last updated ${age_days}d ago. May be stale."
    fi
  fi
done
```

If `STRATEGY_LIFECYCLE.md` hasn't been updated in 7+ days but strategy work has been happening (findings exist), the agent is warned that knowledge files may be out of date.

## Alternatives Considered

### A. Just update the Quick Reference, don't change the gate structure

Add a "capabilities" section to the quick reference but keep Gates 0-7 per-token only. Portfolio and overlay strategies would remain outside the formal process.

**Rejected** because the gate structure IS the process. Without portfolio-specific gates, the agent will try to force cross-sectional strategies through per-token gates (running `validation.py --strategy cross_sectional` which doesn't work) or skip validation entirely.

### B. Create entirely separate pipelines for each strategy class

Three independent SKILL.md files: `/strategy-token`, `/strategy-portfolio`, `/strategy-overlay`.

**Rejected** because the gates share ~70% of their logic (idea screen, dedup, paper trading, production). Separate skills would duplicate rules and diverge over time. Better to have one pipeline with class-specific gate variants (3P/5P, 3O/5O).

### C. Add a single "Portfolio Gate" at the end instead of modifying existing gates

Keep Gates 0-5 per-token, add Gate 5.5 for portfolio assembly only.

**Partially adopted** — Gate 5.5 (Portfolio Assembly) is included. But without class-specific gates at 0/3/5, portfolio strategies would still be forced through inapplicable per-token gates. The class system at Gate 0 is necessary to route correctly.

### D. Defer until more portfolio strategies are validated

Wait for 3+ portfolio strategy results before formalizing the process.

**Rejected** because we already have 3 validated portfolio strategies (cross-sectional, sector rotation, pairs) and 2 validated overlays (regime weighting, signal agreement). The data is sufficient to define thresholds. Deferring means more sessions where the agent doesn't know these capabilities exist.

### E. Use a vector database for knowledge retrieval instead of files

Build embedding-based retrieval over knowledge files using a local vector store.

**Rejected.** The knowledge base is ~5K lines across 17 files. Grep is sufficient and more transparent. Research shows plain filesystem scores 74% on memory tasks, beating specialized vector-store approaches for this scale (HackerNoon agent memory survey). Overhead not justified.

### F. Auto-rewrite knowledge files after each strategy run

Let the agent automatically update STRATEGY_LIFECYCLE.md, STRATEGY_CATALOG.md, etc. after every gate outcome without human review.

**Rejected** for knowledge files (too risky — could degrade signal-to-noise). **Adopted** for PROJECT_STATUS.md (lower stakes, designed to be agent-maintained) and for `findings/strategy-findings.jsonl` (append-only, no edits). Knowledge file updates go through the curate step with explicit instruction, which is a lighter-weight review than a full AIPIP.

### G. Build a separate memory service (SQLite, MCP server, etc.)

Use a persistent memory backend instead of flat files.

**Rejected for now.** The append-only JSONL pattern is simpler, grep-friendly, and sufficient for the current volume (~5-20 findings per week). If volume exceeds 500 entries or we need cross-field queries, revisit with a SQLite approach.

## Impact

### Modified Files

| File | Change | Part |
|------|--------|------|
| `.claude/skills/strategy/SKILL.md` | Add strategy classes, Gates 3P/5P/3O/5O/5.5, PROJECT_STATUS read at startup, mandatory debrief step at each gate, curation trigger at Gate 0 | A+B |
| `.claude/skills/dev/SKILL.md` | Add PROJECT_STATUS check to Step 0 (unfinished work check) | A |
| `.claude/hooks/session-resume.sh` | Emit open tasks from PROJECT_STATUS + recent findings from strategy-findings.jsonl + knowledge freshness warnings | A+B |
| `.claude/hooks/workflow-nudge.sh` | Inject gate-relevant findings when in strategy mode | B |
| `knowledge/STRATEGY_QUICK_REFERENCE.md` | Add "Available Capabilities" section, update Gate 2 dedup rules | A |
| `memory/PROJECT_STATUS.md` | Already created — auto-updated by agent on gate outcomes | A+B |
| `memory/QUANT_PROCESS_GAP_ANALYSIS.md` | Update status column for P5-P10 to Done | A |

### New Files

| File | Purpose |
|------|---------|
| `findings/strategy-findings.jsonl` | Append-only log of gate outcomes and insights |
| `findings/README.md` | Format spec for findings entries |

### New Capabilities

**Part A — Visibility:**
- Strategy class routing at Gate 0 (per-token / portfolio / overlay)
- Portfolio-specific validation gates with appropriate metrics
- Overlay validation gates that measure improvement over baseline
- Portfolio Assembly gate for combining strategies optimally
- Capabilities inventory surfaced at ideation time
- Open tasks visible on every session start (via hook)
- Dev work backlog visible when starting `/dev`

**Part B — Feedback Loop:**
- Every gate outcome produces a structured finding (append-only JSONL)
- Session start injects last 5 findings (why recent strategies died)
- Per-gate context injection (findings relevant to current gate)
- PROJECT_STATUS auto-updated on kills, passes, capability builds
- Periodic curation promotes raw findings into knowledge files
- Knowledge freshness warnings flag stale files

### What Does NOT Change

- Per-token Gates 1-7 remain identical (no regression for existing workflow)
- V3 engine, validation.py, existing strategies — no code changes
- Tier A/B/C classification for per-token strategies
- Hook enforcement (strategy-gate-guard.sh tracks gate number, class is metadata)
- Sweep protocol
- Knowledge base structure (files only grow via curated promotion)

### Downstream Impact

- **Low blast radius:** Changes to 2 skill definitions, 2 hooks (non-blocking additions), 2 knowledge/memory files, 1 new directory
- **All hook changes are non-blocking:** session-resume and workflow-nudge additions only emit text, never block
- **No settings.json changes** (hooks are already wired)
- **Graceful degradation:** If `findings/strategy-findings.jsonl` doesn't exist, hooks skip silently. No new failure modes.

## Change Log

| Date | Change |
|------|--------|
| 2026-03-04 | Initial proposal |
| 2026-03-04 | Accepted by user. Implementing all changes. |
