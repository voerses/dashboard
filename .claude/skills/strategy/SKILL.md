---
name: strategy
description: "Switch to strategy development mode (8-gate validation pipeline)"
user_invocable: true
---

# Strategy Development Mode

Switch the process to strategy development mode. This is a gate-based workflow for building and validating trading strategies with explicit kill criteria at each stage.

**Core objective: Make more money. Don't lose big.**
- Return is the primary goal (Calmar, Sortino, total return)
- Max drawdown is the hard constraint (never blow up)
- Sharpe is diagnostic only — don't penalize upside volatility

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

> **Switched to strategy mode.** Dev workflow hooks are paused. Strategy gate enforcement active.
>
> **8-Gate Validation Pipeline** (return-first, kill losers fast):
>
> | Gate | Name | Time | Kill Rate | Key Kill Criterion |
> |------|------|------|-----------|-------------------|
> | 0 | Idea Screen | 5 min | ~50% | No mechanism / already failed |
> | 1 | Signal Lab | 30 min | ~90% | IC < 0.02, t-stat < 2.0 |
> | 2 | Knowledge + Dedup | 15 min | ~30% | >80% overlap with existing |
> | 3 | Prototype | 30 min | ~10% | >1ms/call, can't vectorize |
> | 4 | Quick Validate (BTC) | 5 min | ~50% | BTC fails dual gate x3 |
> | 5 | Full Validate (49 tokens) | 10 min | ~50% | Rate <20%, DSR p>0.05 |
> | 6 | Paper Trade | 1-4 wks | ~50% | Returns <60% of backtest |
> | 7 | Production + Decay | ongoing | ~30%/yr | Rolling Calmar < 0 |
>
> **~99.8% of ideas never reach production. This is normal and desired.**
>
> Use `/dev` to switch to full dev workflow, `/free` to switch to freeflow.

## Gate Process — Detailed Instructions

At each gate, you MUST:
1. Read the required knowledge files listed for that gate
2. Run the checks specified
3. Output a gate report (format below)
4. Update the gate state file before proceeding

### Gate Report Format (mandatory at every transition)

```
## Gate N: [NAME] — [PASS / KILL / RECYCLE]

**Knowledge files consulted:**
- [x] file1.md
- [x] file2.md

| Metric | Value | Threshold | Status |
|--------|-------|-----------|--------|
| [metric] | [value] | [threshold] | PASS/FAIL |

**Decision:** [PROCEED to Gate N+1 / KILL: reason / RECYCLE: proposed modification]
```

After outputting the report, update the gate state:
```bash
echo "gateN" > "$CLAUDE_PROJECT_DIR/.strategy-gate"
```

---

## GATE 0: Idea Screening (< 5 min)

### Required Knowledge Consultation
```
READ: knowledge/process/STRATEGY_PIPELINE_GATES.md — Section "Gate 0"
READ: knowledge/STRATEGY_LIFECYCLE.md — Tier C archived strategies (don't repeat failures)
READ: knowledge/process/SCOPE_AND_CONTEXT.md — Objective hierarchy (return first)
```

### What to Do
1. Write a hypothesis: what signal, why it should work (economic mechanism), expected holding period
2. Check if it was already tried and archived (Tier C list in STRATEGY_LIFECYCLE.md)
3. Estimate expected trade count in available data (need >30)
4. Score the idea on the rubric from STRATEGY_PIPELINE_GATES.md Gate 0

### Kill Criteria (kill if ANY fail)
- No explainable economic mechanism for why the edge exists
- Signal requires data not available at decision time (look-ahead bias)
- Already tested and failed (check Tier C list) with no new evidence
- Expected trade count < 30 in available data
- Idea score < 5/10 on the rubric

### On PASS
```bash
echo "gate1" > "$CLAUDE_PROJECT_DIR/.strategy-gate"
```

---

## GATE 1: Signal Lab IC Screen (< 30 min)

### Required Knowledge Consultation
```
READ: knowledge/process/SIGNAL_DISCOVERY_METHODS.md — IC testing section
READ: knowledge/process/STRATEGY_PIPELINE_GATES.md — Section "Gate 1"
READ: knowledge/INDICATOR_ANALYSIS.md — existing signal IC results
READ: knowledge/INDICATOR_CATALOG.md — check if indicator already exists in engine
```

### What to Do
1. Run the signal through `v2/signal_lab.py` (or compute IC manually)
2. Record: Mean IC, IC t-statistic, ICIR, IC hit rate
3. Test on post-ETF data (Jan 2024+) as primary window
4. Check stability across 2+ forward horizons (1d, 5d, 10d)
5. Check if signal flipped sign post-ETF

### Kill Criteria
| Metric | Kill Threshold |
|--------|---------------|
| Mean IC (post-ETF) | < +0.02 |
| IC t-statistic | < 2.0 |
| IC hit rate | < 55% |
| Gross profit factor (quick check) | < 1.3 |
| Trade count | < 50 |

### Recycle Rule
If profit factor > 1.1 but other metrics borderline → ONE retry with modified parameters. Still fails → KILL.

### On PASS
```bash
echo "gate2" > "$CLAUDE_PROJECT_DIR/.strategy-gate"
```

---

## GATE 2: Knowledge Base + Dedup Check (< 15 min)

### Required Knowledge Consultation (ALL mandatory)
```
READ: knowledge/STRATEGY_LIFECYCLE.md — full pre-development checklist (Section 2)
READ: knowledge/SIGNAL_DEVELOPMENT.md — signal structure requirements
READ: knowledge/INDICATOR_CATALOG.md — check if indicator exists
READ: knowledge/STRATEGY_CATALOG.md — check if strategy type already implemented
READ: knowledge/PERFORMANCE_PATTERNS.md — vectorization requirements
READ: knowledge/process/CRYPTO_MICROSTRUCTURE_POST_ETF.md — if using crypto-specific signals
```

### What to Do
1. Read ALL files in the pre-development checklist
2. Compare proposed entry signal against ALL existing Tier A/B entry signals:

   | Strategy | Rate | Core Entry Signal |
   |----------|------|-------------------|
   | s11 | 75.5% | `ret_1 > 0.03` (momentum burst) |
   | s09 | 73.5% | EMA stack + daily EMA50 + ADX > 30 |
   | s13 | 67.3% | Volume-weighted cumulative returns |
   | s21 | 63.3% | `rolling_skew > 0.3` + ret > 0 |
   | s17 | 55.1% | `ret_1 > 0.02` + ADX > 25 + +DI > -DI |
   | s18 | 51.0% | `ret_24h > ret_72h/3` (acceleration) |

3. Document overlap assessment with specific signal-level comparison
4. Check `results/sweep_summary_*.json` for latest tier classifications

### Kill Criteria
- Entry signal overlaps > 80% with existing Tier A/B strategy
- Knowledge base reveals approach was already tried and failed
- Proposed signal type already has 2+ strategies in Tier A (saturation)

### Recycle
- Overlap 30-80%: propose as a FILTER to existing strategy instead of new strategy

### On PASS
```bash
echo "gate3" > "$CLAUDE_PROJECT_DIR/.strategy-gate"
```

---

## GATE 3: Prototype + Performance Check (< 30 min)

### Required Knowledge Consultation
```
READ: knowledge/SIGNAL_DEVELOPMENT.md — signal structure: Regime → Trend → Entry → Exit → Sizing
READ: knowledge/PERFORMANCE_PATTERNS.md — vectorization patterns, no Python for-loops
READ: strategies/TEMPLATE.py — template to copy
```

### What to Do
1. Copy `strategies/TEMPLATE.py` → `strategies/sNN_name.py`
2. Implement following the signal structure (regime filter, trend, entry, exit, sizing)
3. ALL code MUST be vectorized — no Python for-loops over bar arrays
4. Run performance check:
   ```bash
   python -c "
   import sys; sys.path.insert(0, 'v3'); sys.path.insert(0, 'v2')
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

### Kill Criteria
- Performance > 1ms per call on 40K bars
- Cannot implement without Python for-loops over bar arrays
- Missing mandatory components (no regime filter, no exit logic)
- Strategy requires indicators not in engine AND adding them is a blast-radius change

### On PASS
```bash
echo "gate4" > "$CLAUDE_PROJECT_DIR/.strategy-gate"
```

---

## GATE 4: Quick Validation — BTC Only (< 5 min)

### Required Knowledge Consultation
```
READ: knowledge/process/STRATEGY_PIPELINE_GATES.md — Gate 4 section
READ: knowledge/process/BACKTESTING_VALIDATION_BEST_PRACTICES.md — walk-forward basics (skim)
```

### What to Do
1. Run V3 validation on BTC:
   ```bash
   cd /workspace/crypto_backtest
   /workspace/venv/bin/python v3/validation.py --strategy sNN --tokens BTC --workers 1
   ```
2. Check: does BTC pass the dual WF+CPCV gate?
3. If FAIL: tune parameters (NOT core logic), retry

### Kill Criteria
- BTC fails dual gate after 3 tuning attempts
- Tuning requires changing core signal logic (not just parameters)

### 3-Attempt Rule
Max 3 parameter tuning attempts. If BTC still fails → hypothesis is wrong → KILL and archive.

### On PASS
```bash
echo "gate5" > "$CLAUDE_PROJECT_DIR/.strategy-gate"
```

---

## GATE 5: Full Validation — 49 Tokens + Statistics (< 10 min)

### Required Knowledge Consultation
```
READ: knowledge/process/STRATEGY_PIPELINE_GATES.md — Gate 5 section (metrics, thresholds)
READ: knowledge/process/BACKTESTING_VALIDATION_BEST_PRACTICES.md — DSR, parameter sensitivity
READ: knowledge/process/RISK_PORTFOLIO_CONSTRUCTION.md — correlation checks (skim)
```

### What to Do
1. Run full validation:
   ```bash
   /workspace/venv/bin/python v3/validation.py --strategy sNN --workers 4
   ```
2. Record validation rate → tier assignment
3. Check return-first metrics (from STRATEGY_PIPELINE_GATES.md):

   | Metric | Threshold | Priority |
   |--------|-----------|----------|
   | Calmar ratio | > 0.5 | PRIMARY |
   | Sortino ratio | > 1.0 | PRIMARY |
   | Profit factor | > 1.5 | PRIMARY |
   | Max drawdown | < 25% | CONSTRAINT |
   | Trade count | > 100 | VALIDITY |
   | WFE | > 50% | VALIDITY |

4. Run parameter sensitivity: +/- 20% on key parameters → Calmar shouldn't degrade > 30%
5. Analyze which tokens fail and why

### Tier Assignment
| Rate | Tier | Action |
|------|------|--------|
| > 50% | A | Proceed to Gate 6 |
| 20-50% | B | Iterate (max 3 cycles, parameters only). Time-box: 30 days max |
| < 20% | C | ARCHIVE immediately |

### Kill Criteria
- Validation rate < 20% (Tier C)
- Calmar < 0.5 on passing tokens
- Max drawdown > 25% on BTC
- WFE < 50% (overfitting)
- Parameter sensitivity > 30% Calmar degradation
- Tier B after 3 iteration cycles without reaching 50%

### On PASS (Tier A)
```bash
echo "gate6" > "$CLAUDE_PROJECT_DIR/.strategy-gate"
```

---

## GATE 6: Paper Trading (1-4 weeks)

### Required Knowledge Consultation
```
READ: knowledge/process/STRATEGY_PIPELINE_GATES.md — Gate 6 section
READ: knowledge/PAPER_TRADING_PRO_FRAMEWORK.md — setup and monitoring
READ: knowledge/PAPER_TRADING_ANTI_FRAMEWORK.md — what to avoid
```

### What to Do
1. Deploy strategy on live data feed with simulated execution
2. Track: net profit, profit factor, win ratio, max DD, slippage per trade, fill rate
3. Minimum 50 trades before any go-live decision
4. Compare live metrics against backtest metrics

### Acceptable Degradation
| Metric | Maximum Degradation |
|--------|-------------------|
| Paper Sortino / Backtest Sortino | > 0.6x |
| Slippage vs modeled | < 2x backtest assumption |
| Max drawdown | < 1.5x backtest |
| Fill rate | > 95% |

### Kill Criteria
- Paper returns < 60% of backtest returns
- Slippage > 50% of expected edge per trade
- Max drawdown > 1.5x worst backtest drawdown
- < 10 trades generated (insufficient signal frequency)

### On PASS
```bash
echo "gate7" > "$CLAUDE_PROJECT_DIR/.strategy-gate"
```

---

## GATE 7: Production Deployment + Decay Monitoring (ongoing)

### Required Knowledge Consultation
```
READ: knowledge/process/STRATEGY_PIPELINE_GATES.md — Gate 7 section (risk limits, decay)
READ: knowledge/process/RISK_PORTFOLIO_CONSTRUCTION.md — position sizing, Kelly, drawdown mgmt
READ: knowledge/KRAKEN_FEES.md — fee structure for sizing calculations
```

### Deployment Rules
- Initial allocation: 1-5% of target for first 3-6 months
- Per-trade risk: 1-2% of strategy allocation (use 1/4 Kelly for crypto)
- Volume participation: < 5% of average daily volume per instrument

### Circuit Breakers
| Trigger | Action |
|---------|--------|
| Daily loss > 3% | Halt for remainder of day |
| Rolling 5-day DD > Y% | Reduce allocation 50% |
| Max DD from peak > 15% | Pull from production |
| Portfolio daily loss > 5% | Reduce ALL positions 50% |

### Decay Detection (monthly review)
```
ALERT if:
  - Rolling 60d Sortino < 0.0 → flag
  - Rolling 90d profit factor < 0.8 → flag
  - No new equity high in 6 months → flag
  - Correlation with another Tier A > 0.7 → flag
  - V3 validation rate dropped > 10pp → flag

ESCALATION:
  - 1 flag: reduce allocation 25%
  - 2 flags: reduce allocation 50%, intensive review
  - 3+ flags: pull from production, post-mortem
```

---

## Strategy Graveyard

When a strategy is killed at any gate, log it:

```bash
# Append to graveyard log
echo "$(date -I) | sNN_name | Killed at Gate X | Reason: [specific reason]" >> "$CLAUDE_PROJECT_DIR/../strategies/GRAVEYARD.md"
```

This prevents re-testing failed ideas without new evidence.

---

## Knowledge Base Quick Reference

| When | Read These |
|------|-----------|
| Starting any strategy work | `process/SCOPE_AND_CONTEXT.md`, `process/STRATEGY_PIPELINE_GATES.md` |
| Ideation (Gate 0) | `STRATEGY_LIFECYCLE.md` (Tier C list) |
| Signal testing (Gate 1) | `process/SIGNAL_DISCOVERY_METHODS.md`, `INDICATOR_ANALYSIS.md` |
| Dedup (Gate 2) | `STRATEGY_CATALOG.md`, `INDICATOR_CATALOG.md`, `SIGNAL_DEVELOPMENT.md` |
| Prototyping (Gate 3) | `PERFORMANCE_PATTERNS.md`, `SIGNAL_DEVELOPMENT.md`, `TEMPLATE.py` |
| Validation (Gate 4-5) | `process/BACKTESTING_VALIDATION_BEST_PRACTICES.md` |
| Paper trading (Gate 6) | `PAPER_TRADING_PRO_FRAMEWORK.md`, `PAPER_TRADING_ANTI_FRAMEWORK.md` |
| Production (Gate 7) | `process/RISK_PORTFOLIO_CONSTRUCTION.md`, `KRAKEN_FEES.md` |
| Looking for new signals | `process/CRYPTO_MICROSTRUCTURE_POST_ETF.md`, `process/DATA_GAP_ANALYSIS.md` |
| Understanding quant best practices | `process/QUANT_FUND_PROCESSES.md`, `process/FAST_ITERATION_FRAMEWORK.md` |
