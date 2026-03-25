---
id: AIPIP-0016
title: V4 Portfolio Gates + Signal Lab Upgrades (Research-Backed)
status: accepted
author: @claude
created: 2026-03-10
supersedes: AIPIP-0015 (partially — extends with V4 engine path and signal lab improvements)
---

# AIPIP-0016: V4 Portfolio Gates + Signal Lab Upgrades (Research-Backed)

## Problem

### Problem 1: V4 Engine Has No Gate Path

The V4 portfolio simulator (`v4/portfolio_backtest.py`) is now our primary backtesting
engine. s58 is live in paper trading. Yet the strategy development process has **no V4
gate path** — it only knows about V3 per-token validation and V3 portfolio strategies.

**V4 vs V3 — what changed:**

| Dimension | V3 | V4 |
|-----------|----|----|
| Capital model | Per-token, infinite capital | Shared pool ($200K), concentration limits |
| Validation | Per-token WF + CPCV, 49-token rate | Single portfolio equity curve |
| Slippage | Flat tier-based | Square-root impact model |
| Multi-strategy | Not supported | Multiple strategies simultaneous |
| Metrics | Per-token validation rate | Portfolio Calmar, Sharpe, MaxDD, OOS returns |

**V3 validation metrics don't apply to V4.** Validation rate, per-token Calmar, PBO
per token — none of these exist in V4. A strategy killed in V3 (s28: 6.7% rate) can
thrive in V4 (+3,157% 12mo, +157% OOS). The process needs V4-specific gates.

**Currently missing:**

| What's Missing | Impact |
|----------------|--------|
| V4 gate path in SKILL.md | Agent doesn't know V4 exists, routes everything through V3 |
| V4 kill criteria | No thresholds for portfolio Calmar, OOS returns, complement test |
| V4 OOS template | No standard for train/test split on V4 portfolio |
| V4 vs V3 routing decision | Agent doesn't know when to use V4 vs V3 |

### Problem 2: Signal Lab Thresholds Are Outdated

Our Gate 1 signal lab uses basic IC > 0.02 and t-stat > 2.0. Research shows these
thresholds are insufficient and miss critical metrics:

| Current Gap | Research Basis | What to Add |
|-------------|----------------|-------------|
| No ICIR metric | Qian/Hua/Sorensen 2007 | ICIR > 0.3 (IC stability matters more than raw IC) |
| No multiple testing correction | Harvey & Liu 2020 | With N=37+ strategies tested, need t > 3.4, not 2.0 |
| No non-linear causality | Schreiber 2000, transfer entropy | Transfer entropy catches non-linear predictors IC misses |
| No feature importance clustering | Lopez de Prado 2020 | Clustered feature importance avoids correlated-feature bias |
| IC decay not tracked | Our signal discovery | Add ICIR_post_etf and decay classification from rolling IC |

### Problem 3: Expected Degradation Not Budgeted

Gate 6 uses "Paper Sortino / Backtest Sortino > 0.6x" but doesn't reference the academic
evidence. Suhonen et al. 2017 found **median 73% Sharpe ratio deterioration** across 215
strategies at 17 banks. Our s58 Sharpe of 7.29 should realistically be ~2.0 live. The
process should set explicit degradation expectations so we don't kill strategies prematurely.

### Problem 4: Decay Detection Is Threshold-Only

Gate 7 uses fixed thresholds (Sortino < 0, PF < 0.8, no high in 6mo). Research shows
Bayesian Online Changepoint Detection (BOCPD, Adams & MacKay 2007) distinguishes structural
decay from normal drawdown noise. Our threshold approach generates false alarms during
normal drawdowns and misses slow structural decay.

## Proposal

### Part A: V4 Portfolio Gate Path (New Class D)

Add a fourth strategy class to the routing system:

```
STRATEGY CLASSES:

A. Per-Token Signal           0→1→2→3→4→5→6→7        (existing)
B. Portfolio Strategy         0→2→3P→5P→6→7           (existing, V3 modules)
C. Overlay                    0→2→3O→5O→6→7           (existing)
D. V4 Portfolio Strategy      0→2→V4-3→V4-4→V4-5→6→7  (NEW)
```

**When to use D vs B:**

| Use V4 (Class D) When | Use V3 (Class B) When |
|------------------------|-----------------------|
| Building portfolio components for s58 | Testing new cross-sectional hypotheses |
| Strategy uses perp/combined market | Strategy is spot-only cross-token ranking |
| Need shared capital + concentration limits | Need per-token robustness proof |
| Complements for sideways/choppy markets | Independent portfolio strategy |
| Strategy individually weak but portfolio-valuable | Strategy must stand alone |

### V4-GATE 3: V4 Prototype + 12-Month Backtest (< 15 min)

```bash
/workspace/venv/bin/python v4/portfolio_backtest.py --strategy sNN --months 12 --capital 200000
```

| Criterion | Threshold | Kill |
|-----------|-----------|------|
| 12-month total return | > +50% | < +50% |
| Portfolio Calmar | > 0.5 | < 0.5 |
| Max drawdown | < 25% | > 25% |
| Trade count | > 100 | < 100 |
| Vectorized (< 1ms/call) | Yes | No |
| Uses strategies/sNN_*.py template | Yes | Ad-hoc code |

```bash
echo "v4gate4" > "$CLAUDE_PROJECT_DIR/.strategy-gate"
```

### V4-GATE 4: V4 OOS Validation — Train→Dec, Trade Jan-Mar (< 10 min)

The critical test: does the strategy survive the March 2026 sideways market?

**Method:** Adjust `config.train_bars` to push the training mask to end on Dec 31, 2025.
The strategy trades OOS from Jan 1 to present (~Mar 10).

| Criterion | Threshold | Kill |
|-----------|-----------|------|
| OOS total return | > 0% (positive) | Negative |
| OOS months positive | >= 2 of 3 | < 2 of 3 |
| OOS max drawdown | < 30% | > 30% |
| March 2026 PnL | > -5% | < -5% (sideways stress test) |

**Sideways market checklist (mandatory for Class D):**
- [ ] Uses perp or combined market (bidirectional)
- [ ] Profitable in RANGE and QUIET regimes
- [ ] Shows positive March 2026 PnL
- [ ] Low correlation with s56 (momentum) and s57 (carry)

```bash
echo "v4gate5" > "$CLAUDE_PROJECT_DIR/.strategy-gate"
```

### V4-GATE 5: Portfolio Complement Test (< 15 min)

Does this strategy improve s58 when added?

**Method:** Run V4 portfolio backtest with s58 + new strategy. Compare portfolio metrics.

| Criterion | Threshold | Kill |
|-----------|-----------|------|
| Portfolio Sharpe delta | > 0 (improves) | Sharpe decreases |
| Portfolio MaxDD delta | <= +2pp | MaxDD worsens > 2pp |
| Correlation vs s56 | < 0.5 | > 0.7 |
| Correlation vs s57 | < 0.5 | > 0.7 |
| Marginal Sharpe contribution | > 0 | Negative (drags portfolio) |
| Regime coverage | Covers 2+ regimes not covered by s58 | Same regime profile as s58 |

```bash
echo "gate6" > "$CLAUDE_PROJECT_DIR/.strategy-gate"
```

### Part B: Signal Lab Upgrades (Gate 1 Enhancement)

Update Gate 1 thresholds and add new metrics based on academic research:

**Updated Gate 1 Kill Criteria:**

| Metric | Old Threshold | New Threshold | Source |
|--------|---------------|---------------|--------|
| Mean IC (post-ETF) | > 0.02 | > 0.02 (unchanged) | — |
| IC t-statistic | > 2.0 | > 3.4 (if N strategies > 37) | Harvey & Liu 2020 |
| IC hit rate | > 55% | > 55% (unchanged) | — |
| **ICIR (IC / std(IC))** | > 0.5 | **> 0.3** | Qian/Hua/Sorensen 2007 |
| Gross profit factor | > 1.3 | > 1.3 (unchanged) | — |
| Trade count | > 50 | > 50 (unchanged) | — |

**New metrics (informational, not kill):**

| Metric | Purpose | How to Compute |
|--------|---------|----------------|
| ICIR post-ETF | IC stability in current regime | IC / std(IC) on Jan 2024+ data |
| Transfer entropy | Non-linear causality check | From `tools/signal_discovery/` outputs |
| IC decay classification | Signal health over time | From `outputs/signal_discovery/rolling_ic_summary*.csv` |
| Clustered feature importance | Avoid correlated-feature bias | Group by correlation > 0.7, take cluster IC |
| Lead/lag asymmetry | Confirm signal is LEADING | From `outputs/signal_discovery/lead_lag*.json` |

**Harvey-Liu adjustment rule:**
- When the total number of tested strategies+signals exceeds N=37, the required
  t-statistic for a new signal rises to `t_adj = sqrt(2 * ln(N))` ≈ 3.4 for N=37.
- This accounts for data mining bias from multiple testing.
- Apply to NEW signals only — existing Tier A strategies are grandfathered.

**Signal stability hierarchy (from our signal discovery):**

| Stability Class | Drift Rate | Examples | Use As |
|-----------------|-----------|---------|--------|
| STABLE | < 0.001/yr | Cross-TF divergence, vol clustering | Primary entry signals |
| DECAYING | 0.001-0.01/yr | Regime-conditional EMAs, RSI patterns | Use with caution, monitor |
| DEAD | > 0.01/yr | Microstructure, order flow proxies | Avoid — post-ETF regime killed them |

### Part C: Degradation Budget (Gate 6 Enhancement)

Update Gate 6 with explicit degradation expectations:

**Research-backed degradation model (Suhonen et al. 2017):**
- 215 strategies across 17 banks studied
- Median Sharpe ratio deterioration: **73%**
- For our purposes: budget **50-60% Sharpe degradation** backtest → live

**Updated Gate 6 thresholds:**

| Metric | Current | Updated | Rationale |
|--------|---------|---------|-----------|
| Paper Sortino / Backtest Sortino | > 0.6x | > 0.4x (first 2 wks), > 0.6x (steady state) | Allow settling period |
| Expected live Sharpe | Not specified | Backtest Sharpe × 0.4 (lower bound) | Budget for 60% degradation |
| Slippage vs modeled | < 2x | < 2x (unchanged) | — |
| Max drawdown | < 1.5x backtest | < 1.5x (unchanged) | — |

**Implication for s58:** Backtest Sharpe 7.29 → expected live Sharpe **2.9-4.4**. This
was the prediction. **POST-MTM REALITY: s58 = -32.5% (12mo). Sharpe 7.29 was pre-MTM fiction.**

### Part D: Decay Detection Upgrade (Gate 7 Enhancement)

Add BOCPD alongside existing threshold-based monitoring:

**Current (keep):** Fixed thresholds — Sortino < 0, PF < 0.8, no high 6mo.

**Add: BOCPD changepoint detection:**

| Metric | Method | Trigger |
|--------|--------|---------|
| Rolling 30d Sharpe | BOCPD with hazard rate 1/90 | Changepoint probability > 0.8 |
| Rolling 30d return | BOCPD with hazard rate 1/90 | Downward shift detected |
| Rolling 60d realized vol | BOCPD with hazard rate 1/180 | Vol regime change |

**Interpretation:**
- Threshold fires + BOCPD fires → **Structural decay confirmed.** Pull from production.
- Threshold fires + BOCPD silent → **Normal drawdown noise.** Continue monitoring, reduce 25%.
- BOCPD fires + threshold OK → **Early warning.** Investigate regime change.

**Implementation note:** BOCPD can be implemented with ~50 lines of numpy (conjugate
normal-inverse-gamma model). No external dependency needed.

### Part E: Regime-Conditional Evaluation

Add regime reporting to all gates that produce metrics (Gates 5, 5P, 5O, V4-4, V4-5):

**Mandatory regime breakdown (report, not kill):**

| Regime | Metric | Purpose |
|--------|--------|---------|
| UPTREND (2) | Return, Sharpe | Is this a trend-only strategy? |
| DOWNTREND (4) | Return, Sharpe | Does it survive bear markets? |
| RANGE (3) | Return, Sharpe | Sideways performance (critical gap) |
| QUIET (1) | Return, Sharpe | Low-vol performance |
| CRISIS (0) | Return, max DD | Tail risk |

**V4 addition:** Strategies CAN be regime-specific (e.g., range-only) if the portfolio
covers all regimes. Report per-regime metrics so portfolio assembly (Gate 5.5) can ensure
full regime coverage.

## Alternatives Considered

### A. Merge V4 into existing Class B (Portfolio Strategy)

Route V4 strategies through the existing 0→2→3P→5P→6→7 path with V4 metrics at 3P/5P.

**Rejected** because V3 and V4 have fundamentally different validation methods. V3
portfolio strategies (cross-sectional, sector rotation) produce a single equity curve
from V3 modules. V4 portfolio strategies run through the V4 simulator with shared capital,
concentration limits, and square-root slippage. Mixing them in one gate path would require
"if V4 then... else..." conditionals that obscure the process.

### B. Require CPCV/PBO for V4 (like V3)

Add CPCV and PBO computation to the V4 simulator.

**Deferred** — this is a good idea long-term (V4 has no CPCV, PBO, or DSR, which is
a significant validation gap). However, implementing CPCV on the V4 portfolio simulator
is complex (shared capital pool means folds aren't independent). For now, the OOS
validation (V4-Gate 4) provides the overfitting check. Add CPCV to V4 engine as a
future AIPIP when the implementation is designed.

### C. Raise all Gate 1 t-stat thresholds immediately to 3.4

Apply Harvey-Liu correction retroactively to all existing strategies.

**Rejected** because existing Tier A strategies were validated before we had 37+
strategies/signals in the pipeline. The correction should apply only to NEW signals
going forward. Existing strategies have additional evidence (CPCV, OOS, paper trading)
that validates them beyond the t-stat.

### D. Replace threshold decay detection entirely with BOCPD

Drop the simple threshold checks in Gate 7.

**Rejected** because thresholds are interpretable and have proven useful (they caught
the s19 mean reversion failure). BOCPD adds statistical rigor but shouldn't replace
the simple checks that are easy for humans to understand. Use both in parallel.

### E. Require regime profitability at every gate

Kill any strategy not profitable in 3+ regimes.

**Rejected** because some V4 strategies are intentionally regime-specific (e.g., s27
funding mean reversion works best in RANGE/QUIET). The portfolio covers all regimes
through diversification. Report regime metrics at every gate, but only kill at the
portfolio complement level (V4-Gate 5) if regime coverage is insufficient.

## Impact

### Modified Files

| File | Change | Part |
|------|--------|------|
| `.claude/skills/strategy/SKILL.md` | Add Class D (V4 Portfolio), V4-Gate 3/4/5, update Gate 1 thresholds, update Gate 6 degradation, update Gate 7 BOCPD, add regime reporting | A+B+C+D+E |
| `knowledge/STRATEGY_QUICK_REFERENCE.md` | Add V4 section with gate path, V4 kill criteria, updated Gate 1 metrics, regime table template, degradation budget, BOCPD description | A+B+C+D+E |
| `knowledge/process/STRATEGY_PIPELINE_GATES.md` | Add V4 gate definitions (already partially done, needs alignment with AIPIP) | A |

### What Does NOT Change

- V3 gates (0-7) remain identical — no regression
- Existing Class A/B/C routing unchanged
- V3 engine, validation.py, strategies/ files unchanged
- Hook enforcement unchanged (strategy-gate-guard.sh reads gate number strings)
- Findings capture format unchanged

### Downstream Impact

- **Low blast radius:** 2 protected files (SKILL.md, QUICK_REFERENCE.md) + 1 knowledge file
- **No code changes** — only process documentation
- **Backward compatible:** Existing V3 path works exactly as before
- **Gate guard hook compatible:** V4 gates use `v4gate3`, `v4gate4`, `v4gate5` strings,
  which the strategy-gate-guard.sh can parse (gate number >= 3 allows strategy writes)

## Change Log

| Date | Change |
|------|--------|
| 2026-03-10 | Initial proposal |
| 2026-03-10 | Accepted by user. Implementing all changes. |
