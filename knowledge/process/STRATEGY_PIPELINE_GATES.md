# Strategy Pipeline Gates — Definitive Reference

> Last updated: 2026-03-30 (AIPIP-0031, AIPIP-0032, AIPIP-0033, OOS integrity)
>
> **When to read:** The coordinator reads ONLY the section for the current gate.
> Do NOT load this entire file at once — read the section you need.

## Strategy Classes

| Class | Gate Path | When to Use |
|-------|-----------|-------------|
| A. Per-Token Signal | 0→1→2→3→4→5→6→7 | Single signal on individual tokens |
| B. Portfolio Strategy | 0→2→3P→5P→6→7 | Cross-token ranking, sector rotation, pairs |
| C. Overlay | 0→2→3O→5O→6→7 | Regime weighting, signal agreement, risk scaling |

## Gate Report Format (mandatory at every transition)

```
## Gate N: [NAME] — [PASS / KILL / RECYCLE]

| Metric | Value | Threshold | Status |
|--------|-------|-----------|--------|
| [metric] | [value] | [threshold] | PASS/FAIL |

**Bias audit:** [CLEAN / issues found]
**Decision:** [PROCEED to Gate N+1 / KILL: reason / RECYCLE: proposed modification]

### Findings (for future sessions)
- [SIGNAL] [one-line insight]
- [DATA] [any data gap discovered]
- [PROCESS] [gate process feedback]
```

After outputting the report:
```bash
echo "gateN" > "$CLAUDE_PROJECT_DIR/.strategy-gate"
echo '{"ts":"...","strategy":"sNN","gate":N,"outcome":"pass|kill","metrics":{...},"finding":"...","category":"signal|data|process","affects":["file1"]}' >> findings/strategy-findings.jsonl
```

After kills/passes, update `memory/PROJECT_STATUS.md`:
- **Kill:** Add to graveyard table
- **Pass Gate 5/5P/5O:** Add to appropriate tier table

---

## GATE 0: Idea Screening (< 5 min)

> **Coordinator:** Gate 0 is lightweight — do this yourself (no subagent needed). Report verdict immediately.

### Load Project State

```
READ: memory/PROJECT_STATUS.md — open tasks, capability inventory, strategy tiers
READ: knowledge/STRATEGY_QUICK_REFERENCE.md — "Available Capabilities" + "Gate 0" sections
```

### Curate Findings (if >50 entries since last curation)

Review `findings/strategy-findings.jsonl`, promote undocumented insights, archive stale findings.

### Choose Strategy Class

| Class | Hypothesis Template |
|-------|---------------------|
| A. Per-Token | "[Signal] predicts [direction] on [token] over [hold] because [mechanism]" |
| B. Portfolio | "[Ranking/selection] across [universe] produces alpha because [mechanism]" |
| C. Overlay | "Applying [overlay] to [base strategy] improves [metric] because [mechanism]" |

### Write Hypothesis

Must include: signal description, economic mechanism (why should this work?), expected holding period.

### Check Graveyard + Tier C

Read `strategies/GRAVEYARD.md` and Tier C list. Kill if: already tried with no new evidence.

### Score the Idea

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
- **Overlay on delta-neutral base:** Directional overlays have no effect on delta-neutral strategies. Check first.

### On PASS

```bash
# Per-token: proceed to Gate 1
echo "gate1" > "$CLAUDE_PROJECT_DIR/.strategy-gate"
# Portfolio or Overlay: skip Gate 1, proceed to Gate 2
echo "gate2" > "$CLAUDE_PROJECT_DIR/.strategy-gate"
```

---

## GATE 1: Signal Lab IC Screen (< 30 min)

> **Coordinator:** Launch IC test + raw backtest as background subagents. Report results when they return. Stay responsive.

**Per-token strategies only.** Portfolio and overlay strategies skip to Gate 2.

**Read:** Quick Reference — "Gate 1" section

### What to Do
1. Run `tools/signal_lab.py` or compute IC manually
2. Record: Mean IC, t-stat, ICIR, hit rate
3. Test post-ETF (Jan 2024+), check 2+ horizons
4. Kill if: IC <0.02, t-stat <2.0, hit rate <55%, PF <1.3, trades <50
5. Recycle: PF >1.1 but borderline → ONE retry
6. **MANDATORY: Quick raw backtest on BTC** (AIPIP-0031):
   ```python
   from tools.raw_backtest import Backtest
   bt = Backtest(capital=100_000, fee_bps=7, market='perp',
                 start='2024-01-01', end='2026-03-17')
   bt.from_signals('BTC', signals, size_usd=50_000)
   result = bt.report('Signal Name - Gate 1 Quick Check')
   ```
   - KILL if L12M net return is negative (signal doesn't survive fees)
   - This takes <2 min. No signal advances without a positive OOS P&L.

**Deep dive if needed:** `knowledge/process/SIGNAL_DISCOVERY_METHODS.md`, `knowledge/INDICATOR_CATALOG.md`

```bash
echo "gate2" > "$CLAUDE_PROJECT_DIR/.strategy-gate"
```

---

## GATE 2: Knowledge + Dedup (< 15 min)

> **Coordinator:** Gate 2 is a knowledge check — do this yourself (read files, compare). Quick, no subagent needed.

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
- Compare strategy CLASS, not entry signal
- Check correlation vs existing: >0.7 → KILL
- Same class exists: must show improvement on Calmar or DD

**Overlays:**
- Check if overlay already applied to base strategy
- Multiple overlays on same base OK if targeting different aspects

**Deep dive if needed:** `knowledge/STRATEGY_CATALOG.md`, `knowledge/process/CRYPTO_MICROSTRUCTURE_POST_ETF.md`

```bash
echo "gate3" > "$CLAUDE_PROJECT_DIR/.strategy-gate"   # Per-token
echo "gate3p" > "$CLAUDE_PROJECT_DIR/.strategy-gate"  # Portfolio
echo "gate3o" > "$CLAUDE_PROJECT_DIR/.strategy-gate"  # Overlay
```

---

## GATE 3: Prototype — Per-Token (< 30 min)

> **Coordinator:** Launch prototype coding + raw backtest as background subagent. Report verdict when it returns.

**Read:** Quick Reference — "Gate 3" section + `strategies/TEMPLATE.py`

### What to Do
1. Copy `strategies/TEMPLATE.py` → `strategies/sNN_name.py`
2. Implement 6-layer signal stack (regime, trend, entry, volume, exit, sizing)
3. ALL code vectorized — no Python for-loops over bar arrays
4. Performance check: must be <1ms/call. Kill if for-loops or missing regime/exit.
5. **MANDATORY: Full raw backtest through harness** (AIPIP-0031):
   ```python
   from tools.raw_backtest import Backtest
   bt = Backtest(capital=100_000, fee_bps=7, market='perp',
                 leverage_max=1.0, start='2024-01-01', end='2026-03-17')
   result = bt.report('sNN_name - Gate 3')
   ```
   - Verdict must be PASS (Sharpe >2, Calmar >3, MaxDD >-25% in ALL windows)
   - KILL verdict = KILL the strategy
6. **Sizing verification** (if SIZING_OVERRIDES declared):
   ```bash
   python tools/verify_sizing.py sNN --save .specs/active/sNN/sizing_verification.json
   ```

```bash
echo "gate4" > "$CLAUDE_PROJECT_DIR/.strategy-gate"
```

---

## GATE 3P: Prototype — Portfolio Strategy (< 30 min)

> **Coordinator:** Launch prototype coding + raw backtest as background subagent. Report verdict when it returns.

**Portfolio strategies only.** Write a research script that generates weight matrices,
then validate through `tools/raw_backtest.py` using `bt.from_weights()`.

### What to Do
1. Write a research script that produces a weight matrix (token → weight over time)
2. All code vectorized — no for-loops over bar arrays
3. Run through raw backtest harness:
   ```python
   from tools.raw_backtest import Backtest
   bt = Backtest(capital=100_000, fee_bps=7, market='perp',
                 leverage_max=1.0, start='2024-01-01', end='2026-03-17')
   bt.from_weights(weight_matrix, rebalance_freq='1W')
   result = bt.report('Portfolio Strategy - Gate 3P')
   ```
4. Verdict must be PASS

| Criterion | Threshold | Kill |
|-----------|-----------|------|
| Rebalance frequency | Weekly or slower | Daily = too much turnover |
| Universe coverage | >=20 tokens eligible | <20 = insufficient |
| Turnover | <50% per rebalance | >50% = fee drag kills edge |
| Vectorized? | Yes | For-loops = kill |
| Raw backtest verdict | PASS | KILL = kill strategy |

```bash
echo "gate5p" > "$CLAUDE_PROJECT_DIR/.strategy-gate"
```

---

## GATE 3O: Prototype — Overlay (< 30 min)

> **Coordinator:** Launch prototype coding + raw backtest as background subagent. Report verdict when it returns.

**Overlay strategies only.** Modifies allocation/entry of existing strategies.

### Rule: Never Modify Base Strategies (AIPIP-0018)

Overlays MUST be **new wrapper strategy files** that import and call the base strategy.

```python
# strategies/sNN_base_with_overlay.py  (NEW file)
from strategies.sXX_base_strategy import strategy as base_strategy

def strategy(ctx):
    result = base_strategy(ctx)
    size_mult = compute_overlay(ctx)
    return StrategyResult(...result fields..., size_multiplier=size_mult)
```

### What to Do
1. Identify base strategy (must be Tier A/B)
2. Create wrapper file
3. Implement overlay logic in wrapper only

| Criterion | Threshold | Kill |
|-----------|-----------|------|
| Base strategy identified? | Must specify Tier A/B | No base = kill |
| Wrapper file created? | New `sNN_*.py`, NOT modifying base | Modified base = revert |
| Vectorized? | Yes | For-loops = fix before Gate 5O |
| < 1ms/call? | Performance check | > 1ms = optimize |

### StrategyResult Field Checklist (if new engine field needed)

- [ ] Added to `StrategyResult` dataclass with default value
- [ ] Applied in `_simulate()` and `_simulate_combined()`
- [ ] Forwarded in `_run_walk_forward()` and `_run_walk_forward_combined()`
- [ ] Paper trader reads from strategy result

```bash
echo "gate5o" > "$CLAUDE_PROJECT_DIR/.strategy-gate"
```

---

## GATE 4: Quick Validate — BTC Only (< 5 min)

> **Coordinator:** Launch walk-forward backtest as background subagent. Report verdict when it returns.

**Per-token strategies only.** Portfolio/overlay skip to Gate 5P/5O.

1. **Raw backtest walk-forward** (AIPIP-0031): Multiple start dates to check stability:
   ```python
   from tools.raw_backtest import Backtest
   for start in ['2023-01-01', '2023-06-01', '2024-01-01', '2024-06-01']:
       bt = Backtest(capital=100_000, fee_bps=7, market='perp',
                     start=start, end='2026-03-17')
       bt.from_signals('BTC', signals, size_usd=50_000)
       result = bt.report(f'sNN BTC WF ({start})')
   ```
2. ALL windows must produce PASS verdict
3. FAIL → tune parameters (NOT core logic), max 3 attempts
4. 3 failures → KILL

```bash
echo "gate5" > "$CLAUDE_PROJECT_DIR/.strategy-gate"
```

---

## GATE 5: Full Validate — Per-Token, 49 Tokens (< 10 min)

> **Coordinator:** Launch full-universe backtest as background subagent. Heaviest gate — stay responsive.

1. **Raw backtest on full universe** (AIPIP-0031):
   ```python
   from tools.raw_backtest import Backtest
   bt = Backtest(capital=100_000, fee_bps=7, market='perp',
                 leverage_max=1.0, start='2024-01-01', end='2026-03-17')
   tokens = bt.load_tokens(min_adv=2_000_000)
   result = bt.report('sNN Full Universe - Gate 5')
   ```
2. Verdict must be PASS across L12M/L6M/L3M
3. Confirm Gate 3 results hold at scale (no liquidity crowding)
4. Parameter sensitivity: +/-20% → Calmar shouldn't degrade >30%
5. Kill if: any window fails (Sharpe <2, Calmar <3, MaxDD >25%)
6. **OOS integrity check**: Run `python run_oos_monthly.py` for true OOS month-by-month breakdown. Each month's signals recomputed with data capped at that month's end. Zero forward bias. All months should be consistent with the full-window backtest.

**Deep dive if needed:** `knowledge/process/BACKTESTING_VALIDATION_BEST_PRACTICES.md`

```bash
echo "gate6" > "$CLAUDE_PROJECT_DIR/.strategy-gate"
```

---

## GATE 5P: Full Validate — Portfolio Strategy

> **Coordinator:** Launch portfolio backtest as background subagent. Report verdict when it returns.

1. **Run through raw backtest harness** (AIPIP-0031) using `bt.from_weights()`
2. Verdict must be PASS (Sharpe >2, Calmar >3, MaxDD >-25% in ALL windows)
3. Check correlation vs existing Tier A strategies (compute return correlation manually)
4. Kill if: verdict is KILL, or corr >0.7 vs existing portfolio strategies

```bash
echo "gate6" > "$CLAUDE_PROJECT_DIR/.strategy-gate"
```

---

## GATE 5O: Full Validate — Overlay

> **Coordinator:** Launch base + overlay backtests as two parallel background subagents. Compare when both return.

1. Run base WITHOUT overlay through `tools/raw_backtest.py` → baseline
2. Run base WITH overlay through `tools/raw_backtest.py` → improved
3. Compare L12M/L6M/L3M tables side by side

| Criterion | Before | After | Kill If |
|-----------|--------|-------|---------|
| Calmar | baseline | must improve | Degrades |
| Max DD | baseline | must improve or hold | Worsens >2pp |
| Sharpe | baseline | should improve | Degrades >0.1 |
| Trade count | baseline | may decrease | <50 trades remain |
| Regime robustness | — | works in 2+ regimes | Only 1 regime |

5. Kill if: overlay makes any primary metric worse

```bash
echo "gate6" > "$CLAUDE_PROJECT_DIR/.strategy-gate"
```

---

## GATE 5.5: Portfolio Assembly (< 30 min)

> **Coordinator:** Launch correlation analysis + portfolio simulation as background subagents.

**Optional.** Run when 2+ strategies pass Gate 5.

1. Compute pairwise return correlations across all candidate strategies
2. Test allocation: equal-weight, risk parity, regime-weighted
3. Simulate combined portfolio through `tools/raw_backtest.py` using `bt.from_weights()`

| Metric | Target |
|--------|--------|
| Portfolio Calmar | > 1.0 |
| Portfolio max DD | < 20% |
| Effective N | > 3.0 |
| Max pairwise corr | < 0.7 |
| Worst regime PnL | > -5% ann. |

```bash
echo "gate6" > "$CLAUDE_PROJECT_DIR/.strategy-gate"
```

---

## GATE 6: Paper Trading (1-4 weeks)

> **Coordinator:** Deploy paper trader, monitor daily. Report to user weekly.

1. Deploy on live data, simulated execution
2. Min 50 trades before go-live
3. Kill if: returns <60% backtest, slippage >50% edge, MaxDD >1.5x, <10 trades

**Deep dive if needed:** `knowledge/PAPER_TRADING_PRO_FRAMEWORK.md`

```bash
echo "gate7" > "$CLAUDE_PROJECT_DIR/.strategy-gate"
```

---

## GATE 7: Production + Decay (ongoing)

> **Coordinator:** Monitor production metrics. Alert user on circuit breaker hits or decay signals.

1. Allocation: 1-5% target, 1/4 Kelly, <5% ADV
2. Circuit breakers: -3% daily halt, -15% pull
3. Monthly decay: Sortino <0, PF <0.8, no high 6mo → escalate

---

## Strategy Graveyard

When killed at any gate:
```bash
echo "$(date -I) | sNN_name | Killed at Gate X | Reason: [specific reason]" >> "$CLAUDE_PROJECT_DIR/../strategies/GRAVEYARD.md"
```
