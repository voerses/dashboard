# Strategy Pipeline Gates -- Definitive Reference

> **TL;DR — Six gates from idea to production**
> - Return first: Calmar > 0.5, Sortino > 1.0, MaxDD < 25%, PF > 1.5; Sharpe is diagnostic only
> - Tier-based costs mandatory: 0.30% Tier 1, 0.37% Tier 2, 0.60% Tier 3 per side (Kraken)
> - V3 dual gate: WF + CPCV; >50% validation = Tier A, 20-50% = Tier B (max 3 cycles), <20% = archive
> - Paper trading: min 50 trades, acceptable degradation 0.6x backtest Sharpe
> **When to read full file:** Setting kill/promotion thresholds, interpreting validation results, full gate details
> **Sections:** 0-Idea, 1-Signal Lab, 2-Single-Token, 3-Multi-Token, 4-Paper Trading, 5-Live, Appendix-Metrics

> Single source of truth for strategy development gate criteria, kill thresholds, and quality checks.
> Cross-references: `knowledge/STRATEGY_LIFECYCLE.md` (tier system), `knowledge/SIGNAL_DEVELOPMENT.md` (signal structure).
> Sources: 8 research files in `knowledge/process/`.

## Core Objective
**Make more money. Don't lose big.**

Priority order for ALL gate decisions:
1. **Return** — does this strategy make serious money? Total return and CAGR are the goal.
2. **Drawdown protection** — can we survive the bad times? Max DD is the constraint.
3. **Calmar ratio** (return / max DD) — the single best metric for our goals.
4. **Sortino** over Sharpe — we WANT upside volatility. Sharpe penalizes big wins.
5. Risk-adjusted metrics (Sharpe, etc.) are diagnostic, not objectives.

A high-return strategy with acceptable drawdown beats a smooth low-return strategy every time.

---

## Gate System Overview

### Pipeline Diagram

```
IDEAS (many)
  |
  v  Gate 0: Idea Screening                 (~5 min)     Kill ~50%
HYPOTHESES
  |
  v  Gate 1: Signal Lab (IC Testing)         (~2-4 hrs)   Kill ~90%
CANDIDATE SIGNALS
  |
  v  Gate 2: Single-Instrument Backtest      (~1-2 days)  Kill ~70%
VALIDATED SIGNALS
  |
  v  Gate 3: Multi-Instrument Validation     (~1-2 days)  Kill ~50%
  |          (V3 dual gate: WF + CPCV)
ROBUST STRATEGIES
  |
  v  Gate 4: Paper Trading                   (~1-4 wks)   Kill ~50%
CONFIRMED STRATEGIES
  |
  v  Gate 5: Live Deployment                 (ongoing)    Decay detection
PRODUCTION
```

### Time Budget

| Gate | Time Budget | Compute Budget |
|------|-------------|----------------|
| 0: Idea Screening | 5 minutes | None |
| 1: Signal Lab | 2-4 hours | Signal Lab IC run |
| 2: Single-Instrument | 1-2 days | BTC-only V3 validation (~5 sec) |
| 3: Multi-Instrument | 1-2 days | Full 111-token V3 sweep (~32 sec/strategy, 4 workers) |
| 4: Paper Trading | 1-4 weeks | Live data feed, simulated execution |
| 5: Live Deployment | Ongoing | Real capital at risk |

**Target: Idea to validated result in ~25 minutes for Gates 0-3 (fast path).**

### Expected Kill Rates

| Gate | Cumulative Survival | Per-Gate Kill Rate |
|------|--------------------|--------------------|
| 0 | 50% | ~50% |
| 1 | 5% | ~90% |
| 2 | 1.5% | ~70% |
| 3 | 0.75% | ~50% |
| 4 | 0.35% | ~50% |
| 5 (first year) | 0.15-0.25% | ~30% (decay) |

**Expectation: ~99.8% of initial ideas never reach production. This is normal.**

---

## Gate 0: Idea Screening

**Purpose:** Kill obviously flawed ideas before any compute is spent.

**Time budget:** 5 minutes.

### What Qualifies as a Viable Idea

A viable idea must have ALL of the following:

1. **Economic rationale** -- an explainable mechanism for WHY the signal should exist (behavioral bias, structural inefficiency, information asymmetry, risk premium).
2. **Identified data sources** -- the data needed to compute the signal is available and accessible at decision time (no look-ahead by construction).
3. **Expected trade profile** -- holding period, trade frequency, expected win rate, expected payoff ratio.
4. **Novelty vs. existing strategies** -- not a duplicate of an existing Tier A/B strategy.

### Idea Scoring Rubric

| Criterion | Score 0 (Kill) | Score 1 (Weak) | Score 2 (Strong) |
|-----------|---------------|----------------|-----------------|
| Economic rationale | No explanation for why the edge exists | Plausible but unverified story | Published academic basis or clear structural reason |
| Data availability | Data doesn't exist or has look-ahead bias | Data exists but quality unknown | Clean, point-in-time data already in pipeline |
| Signal novelty | >80% overlap with existing Tier A/B entry signal | 30-80% overlap | <30% overlap with all existing strategies |
| Expected frequency | <30 trades in available history | 30-100 trades expected | >100 trades expected |
| Regime breadth | Works in one regime only | Works in 2 regimes | Works in 3+ regimes or is regime-adaptive |

**Minimum to proceed:** No zeros. Total score >= 5/10.

### Required Research Before Proceeding

```
[ ] Read knowledge/STRATEGY_LIFECYCLE.md -- check tier classifications, dedup table
[ ] Read knowledge/SIGNAL_DEVELOPMENT.md -- signal structure requirements
[ ] Read knowledge/INDICATOR_CATALOG.md -- check if indicator already exists
[ ] Read knowledge/STRATEGY_CATALOG.md -- check if strategy type already implemented
[ ] Check results/sweep_summary_*.json -- current sweep results
[ ] Check idea graveyard -- has this been tried and failed before?
```

### Kill Criteria (kill if ANY fail)

| Criterion | Threshold |
|-----------|-----------|
| No economic rationale | Cannot explain WHY the signal works |
| Look-ahead by construction | Signal relies on data not available at decision time |
| Duplicate strategy | >80% overlap with existing Tier A/B entry signal |
| Insufficient frequency | <30 expected trades in available data history |
| Data unavailable | Required data source doesn't exist or can't be obtained |
| Previously failed | Same idea tested and killed (check graveyard), no new evidence |

**Decision:** PASS (proceed to Gate 1) or KILL (log to idea graveyard with reason).

---

## Gate 1: Signal Lab (IC Testing)

**Purpose:** Determine whether the raw signal has any predictive power before running expensive backtests.

**Time budget:** 2-4 hours (includes writing the MVS prototype).

**Tool:** `tools/signal_lab.py`

### Minimum IC Threshold

| Metric | Minimum Threshold | Rationale |
|--------|-------------------|-----------|
| Mean IC (Spearman rank correlation) | > +0.02 post-ETF (Jan 2024+) | Below this, signal is indistinguishable from noise |
| IC t-statistic | > 2.0 | Statistical significance at ~95% confidence |
| IC Information Ratio (ICIR = mean(IC)/std(IC)) | > 0.3 | Signal must be consistent, not just high on average |
| IC hit rate (% of periods with IC > 0) | > 55% | Must be right more often than not |

### Statistical Significance Requirements

- IC must be computed on post-ETF data (Jan 2024+) as the primary window.
- Signal must be stable across at least 2 forward horizons (1d, 5d, or 10d).
- Check if signal flipped sign post-ETF (some did -- see s22_supertrend_adx notes).
- Apply Benjamini-Hochberg FDR correction at alpha = 0.05 if testing multiple signals simultaneously.

### Correlation with Existing Signals (Dedup)

Before proceeding, compute cross-correlation of the new signal with ALL existing Tier A/B entry signals:

| Existing Strategy | Core Entry Signal | Signal Type |
|-------------------|-------------------|-------------|
| s11 (75.5%) | `ret_1 > 0.03` | Momentum burst |
| s09 (73.5%) | EMA stack + daily EMA50 + ADX > 30 | Dual momentum |
| s13 (67.3%) | Volume-weighted cumulative returns | Vol-weighted TSMOM |
| s21 (63.3%) | `rolling_skew > 0.3` + ret > 0 | Skew momentum |
| s17 (55.1%) | `ret_1 > 0.02` + ADX > 25 + +DI > -DI | Trend strength burst |
| s18 (51.0%) | `ret_24h > ret_72h/3` (acceleration) | Momentum acceleration |

**Rule:** If signal correlation > 0.7 with any existing Tier A signal, it is not novel enough. Propose it as a FILTER to the existing strategy instead.

### MVS Quick Backtest Thresholds

| Metric | Threshold | Rationale |
|--------|-----------|-----------|
| Sharpe ratio (gross, before costs) | > 1.0 | Must clear a high bar before costs erode it |
| Max drawdown | < 30% | Unacceptable tail risk beyond this |
| Number of trades | > 50 | Insufficient sample for inference below this |
| Profit factor | > 1.3 | Edge must be material |

### Kill Criteria

| Criterion | Threshold |
|-----------|-----------|
| Mean IC | < +0.02 post-ETF |
| IC t-stat | < 2.0 |
| Gross Sharpe | < 1.0 |
| Max drawdown | > 30% |
| Trade count | < 50 |
| Signal correlation with existing Tier A | > 0.7 |

**Recycle rule:** If Sharpe > 0.7 but other metrics are borderline, allow ONE retry with modified parameters. If still fails, KILL.

**Decision:** PROCEED to Gate 2, RECYCLE (one retry), or KILL.

---

## Gate 2: Single-Instrument Backtest

**Purpose:** Confirm the signal survives realistic conditions on a single instrument (BTC).

**Time budget:** 1-2 days.

**Tool:** V3 validation engine with BTC-only (`v3/validation.py --strategy sNN --tokens BTC`)

### Performance Requirements

The strategy must be implemented as a vectorized function following `strategies/TEMPLATE.py`, hitting the performance constraint of < 1ms per call on 40K bars.

### Minimum Metrics (Return-First)

| Metric | Threshold | Priority | Rationale |
|--------|-----------|----------|-----------|
| Calmar ratio | > 0.5 | **PRIMARY** | Return must justify drawdown — this is THE metric |
| Sortino ratio | > 1.0 | **PRIMARY** | Earning more than downside risk (doesn't penalize big wins) |
| Profit factor | > 1.5 | **PRIMARY** | Strong positive expectancy |
| Max drawdown | < 25% | **CONSTRAINT** | Hard limit — beyond this we lose too much to recover |
| Number of trades | > 100 | **VALIDITY** | Statistical significance for all metrics |
| Win rate * payoff ratio | > 1.0 | **VALIDITY** | Positive expected value per trade |
| Net Sharpe (after costs) | > 0.5 | Secondary | Diagnostic only — low bar because Sharpe penalizes upside vol |

### Transaction Cost Requirements

Backtests must use **tier-based costs** that reflect the target exchange and token liquidity.
The V3 engine applies these automatically via `v3/universe.py` (`TIER_COSTS`).

**Kraken Pro costs (default — $200K–$500K monthly volume):**

| Tier | Tokens | Fee (taker) | Slippage | Per-side | Round-trip |
|------|--------|-------------|----------|----------|------------|
| 1 (>$50M ADV) | BTC, ETH, SOL, SUI | 0.22% | 8 bps | 0.30% | 0.60% |
| 2 ($10–50M ADV) | ADA, AVAX, DOT, LINK | 0.22% | 15 bps | 0.37% | 0.74% |
| 3 ($5–10M ADV) | BONK, FLOKI, PENGU | 0.25% | 35 bps | 0.60% | 1.20% |

**Do NOT use a single flat cost for all tokens.** A strategy that passes validation at
0.15% per-side may fail at realistic tier-based costs — this is a real false positive
we caught in March 2026 (ETH on S11 went from PASS to FAIL).

See `knowledge/KRAKEN_FEES.md` for full derivation and Binance comparison.

Components modeled: exchange fees (maker/taker), spread cost, slippage per liquidity tier.

### Walk-Forward Efficiency Threshold

| Metric | Threshold | Rationale |
|--------|-----------|-----------|
| Walk-Forward Efficiency (WFE = OOS return / IS return) | > 50% | Below 50% indicates significant overfitting |
| OOS windows profitable | > 60% of windows | Cannot rely on a single favorable period |
| Parameter sensitivity (Sharpe degradation at +/-20% params) | < 30% | Cannot be a knife-edge optimization |
| Deflated Sharpe Ratio (DSR) p-value | < 0.05 | Must survive multiple-testing correction |

**WFE interpretation:**
- \> 80%: Excellent -- minimal overfitting
- 50-80%: Good -- acceptable
- 30-50%: Marginal -- likely some overfitting
- < 30%: Poor -- significant overfitting, KILL

### Outlier Dependence Check

- Remove the top 5 most profitable trades and recompute metrics.
- Strategy must remain profitable after removal.
- If profitability depends on outlier trades, the edge is not real.

### Kill Criteria (Return-First)

| Criterion | Threshold | Why |
|-----------|-----------|-----|
| Calmar ratio | < 0.5 | Not making enough money relative to pain |
| Sortino ratio | < 1.0 | Downside risk exceeds returns |
| Max drawdown | > 25% | **Hard constraint** — unacceptable loss |
| Trade count | < 100 | Can't trust the numbers |
| WFE | < 50% | Overfitting — returns won't persist |
| DSR p-value | > 0.05 | Edge not statistically real |
| Parameter sensitivity | > 30% Calmar degradation | Knife-edge optimization |
| BTC fails dual WF+CPCV gate | Strategy cannot pass on most liquid asset |
| Unprofitable after removing top 5 trades | Outlier-dependent — no real edge |
| Performance > 1ms per call | Not vectorized properly |

Note: We do NOT kill on low Sharpe alone — a strategy with Sharpe 0.6 but Calmar 1.5 and Sortino 2.0 is excellent (it just has big upside swings).

**3-attempt rule:** If BTC fails the dual gate after 3 tuning attempts, the hypothesis is likely wrong. Archive the prototype.

**Decision:** PROCEED to Gate 3 or KILL.

---

## Gate 3: Multi-Instrument Validation (V3 Dual Gate)

**Purpose:** Confirm the signal generalizes across instruments and assign a tier.

**Time budget:** 1-2 days.

**Tool:** V3 validation on filtered universe (`v3/validation.py --strategy sNN --workers 4`, default `--universe filtered` = 111 tokens)

### CPCV Requirements

The V3 validation engine runs a dual gate: Walk-Forward Analysis + Combinatorial Purged Cross-Validation.

| Parameter | Value |
|-----------|-------|
| N (groups) | 6-20 (engine default) |
| k (test groups) | 2-4 |
| Target backtest paths | >= 100 for stable distributions |
| Purge length | lookback_window + forecast_horizon - 1 |
| Embargo fraction | 0.01-0.05 of total data |

**Per-token pass criteria (dual gate):**
- Token must pass BOTH walk-forward AND CPCV gates simultaneously.
- PBO (Probability of Backtest Overfitting) < 30% (fraction of CPCV paths with Sharpe < 0).
- 10th percentile path Sharpe > 0.

### Token Pass Rate Threshold (Tier System)

The validation rate = % of filtered-universe tokens passing the dual WF+CPCV gate.

| Tier | Validation Rate | Status | Action |
|------|----------------|--------|--------|
| **A** | > 50% | Production | Deploy, monitor, iterate |
| **B** | 20-50% | Experimental | Up to 3 iteration cycles, then archive if no improvement |
| **C** | < 20% | Archive | Move to `strategies/archive/` immediately |

**Tier B iteration rules:**
- Maximum 3 iteration cycles.
- Adjust parameters ONLY (not core logic) -- wider/tighter filters, different thresholds.
- Re-validate after each change.
- If rate doesn't exceed 50% after 3 cycles, archive.

**Tier A promotion from B requires:**
- Validation rate > 50% on latest sweep.
- Rate stable or improving across 2+ sweep dates.
- No >80% overlap with existing Tier A entry signals.

**Tier A demotion to B triggers:**
- Validation rate drops below 40% (10% buffer below threshold).
- Decline persists across 2 consecutive sweeps.
- OR single catastrophic sweep where rate drops below 20%.

### Regime Robustness Checks

| Check | Requirement |
|-------|-------------|
| Positive expectancy in 2+ of 3 major regimes (bull, bear, sideways) | Mandatory |
| Backtest includes at least one crisis period (Mar 2020, May 2021, Nov 2022) | Mandatory |
| Stress test: drawdown < 2x expected during historical stress events | Mandatory |
| Negative returns in any major regime | Flag for review (not auto-kill) |
| Regime detection uses causal (expanding) statistics only | Mandatory |

**Regime look-ahead audit (mandatory for any strategy using regime filters):**
- [ ] `detect_daily_regime()` uses `.expanding()` or `.rolling()`, never `np.percentile()`
      on the full array
- [ ] Early bars (before `min_periods`) default to neutral regime (RANGE), not a
      classification based on insufficient data
- [ ] Any custom regime logic added by a strategy follows the same causal rules
- See: `knowledge/process/BACKTESTING_VALIDATION_BEST_PRACTICES.md` Section 10

### Portfolio-Level Checks

| Metric | Threshold |
|--------|-----------|
| Correlation with existing live strategies | < 0.5 (provides diversification) |
| Portfolio Sharpe improvement (incremental) | > 0.3 improvement when added |
| Diversification ratio | > 1.2 (benefit from multi-asset) |
| Worst-case drawdown (stress scenarios) | < 40% |

### Kill Criteria

| Criterion | Threshold |
|-----------|-----------|
| Validation rate | < 20% (Tier C -- archive immediately) |
| Fails on BTC | If BTC doesn't pass, the strategy likely fails broadly |
| PBO | > 50% on majority of tokens |
| Correlation with existing Tier A strategy | > 0.7 (not differentiated enough) |
| Negative expectancy in all bear regimes | No crisis resilience |
| 3 iteration cycles exhausted without reaching 50% | Archive |

**Decision:** Tier A (Gate 4), Tier B (iterate up to 3x), or Tier C (KILL/archive).

---

## Gate 4: Paper Trading

**Purpose:** Validate that the strategy works in real-time with live data feeds and realistic execution.

**Time budget:** 1-4 weeks minimum.

### Minimum Duration

| Context | Minimum Duration |
|---------|-----------------|
| New strategy, new execution path | 2-3 months |
| Established strategy type, new parameters | 2-4 weeks |
| High-frequency (300+ trades/month) | 1-2 months |
| Low-frequency (<20 trades/month) | 3-6 months |

### Minimum Trade Count for Significance

- **Absolute minimum:** 50 trades before any go-live decision.
- **Preferred:** 150+ trades for robust statistical inference.
- **SPRT (Sequential Probability Ratio Test):** Use to make accept/reject decisions with minimum data. H0: Sharpe = 0. H1: Sharpe = target (e.g., 1.0). Alpha = 0.05, beta = 0.10.

### Metrics to Track

| Category | Metrics |
|----------|---------|
| Profitability | Net profit, profit factor, win ratio, avg gain vs avg loss |
| Risk | MDD, max DD duration, Sharpe, Sortino |
| Execution quality | Fill rate, slippage per trade, latency, partial fill rate |
| Consistency | Equity curve smoothness, rolling Sharpe stability |
| Expectancy | Per-trade expectancy = (win% * avg_win) - (loss% * avg_loss) |

### Acceptable Degradation vs. Backtest

| Metric | Maximum Acceptable Degradation |
|--------|-------------------------------|
| Paper Sharpe / Backtest Sharpe | > 0.6x (must retain at least 60% of backtest Sharpe) |
| Slippage vs. modeled | < 2x backtest assumption |
| Fill rate | > 95% at expected prices |
| Max drawdown | < 1.5x backtest expectation |
| Operational failures | < 1 per week |
| SPRT verdict | Must reach "Accept H1" (strategy works) |

**Expected degradation context:**
- Live Sharpe is typically 30-50% lower than backtested Sharpe.
- SR shortfall > 0.5 = strong evidence of overfitting.
- SR shortfall > 1.0 = strategy almost certainly overfit.

### Kill Criteria

| Criterion | Threshold |
|-----------|-----------|
| Paper Sharpe / Backtest Sharpe | < 0.6 |
| Slippage | > 50% of expected edge (edge destroyed by execution) |
| Slippage vs modeled | > 2x backtest assumption |
| SPRT verdict | "Reject" (strategy has no edge) |
| Operational failures | > 1 per week |
| Max drawdown | > 1.5x worst backtest drawdown |
| Fill rate | < 95% |

**Decision:** GO LIVE (proceed to Gate 5) or KILL.

---

## Gate 5: Live Deployment

**Purpose:** Deploy with real capital and continuously monitor for strategy health.

### Position Sizing Rules

| Rule | Specification |
|------|---------------|
| Initial allocation | 1-5% of target allocation for first 3-6 months |
| Per-trade risk | 1-2% of strategy allocation (never > 5% regardless of Kelly output) |
| Kelly sizing | Use 1/4 to 1/3 of full Kelly for crypto given parameter uncertainty |
| Volume participation | < 5% of average daily volume per instrument |
| Max position per instrument | Hard cap per strategy and per instrument |
| Scale-up criterion | 3+ consecutive profitable months before increasing allocation |

### Risk Limits

**Strategy-Level Circuit Breakers:**

| Limit | Threshold | Action |
|-------|-----------|--------|
| Daily loss limit | Strategy-specific (e.g., -3%) | Halt strategy for remainder of day |
| Consecutive losing trades | N losses in a row (strategy-specific) | Pause for reassessment |
| Rolling 5-day drawdown | > Y% (strategy-specific) | Reduce allocation 50% |
| Max drawdown from peak | > 15% | Pull from production |

**Portfolio-Level Circuit Breakers:**

| Limit | Threshold | Action |
|-------|-----------|--------|
| Portfolio daily loss | > 3-5% in a day | Reduce all positions 50% |
| Portfolio max drawdown | > 20-25% | Halt all trading, require manual override |
| Cross-strategy correlation spike | > 0.7 | Reduce aggregate exposure |
| Counterparty exposure | Max per exchange/venue | Hard cap |

**Drawdown-Triggered Deleveraging Schedule:**

```
Drawdown < 5%:   leverage_multiplier = 1.00  (full allocation)
Drawdown < 10%:  leverage_multiplier = 0.75  (reduce 25%)
Drawdown < 15%:  leverage_multiplier = 0.50  (reduce 50%)
Drawdown < 20%:  leverage_multiplier = 0.25  (reduce 75%)
Drawdown >= 20%: leverage_multiplier = 0.00  (full halt)
```

### Monitoring Requirements

| Monitor | Frequency | Alert Threshold |
|---------|-----------|-----------------|
| Rolling Sharpe (30d, 60d, 90d) | Daily | 60d Sharpe < 0.0 |
| Rolling profit factor (90d) | Daily | < 0.8 |
| Max consecutive losing days | Daily | > 20 days |
| Months since new equity high | Monthly | > 6 months |
| Correlation with other live strategies | Weekly | > 0.7 |
| Execution quality (slippage, fills) | Per-trade | Slippage > 2x model |
| Daily P&L reconciliation | Daily | Actual vs expected diverges > 2 sigma |

### Strategy Decay Detection -- When to Pull

Pre-committed decay thresholds:

```
DECAY_THRESHOLDS = {
    "rolling_sharpe_60d":             0.0,    # Below zero = losing money
    "rolling_sharpe_90d":             0.3,    # Well below deployment baseline
    "max_consecutive_losing_days":    20,     # Extended losing streak
    "drawdown_from_peak":            -0.15,   # 15% strategy-level drawdown
    "profit_factor_90d":              0.8,    # Losing more than winning
    "months_since_new_equity_high":   6,      # No new high in 6 months
}
```

**Escalation ladder:**
- Breach 1 threshold: Flag for review, reduce allocation 25%.
- Breach 2 thresholds: Reduce allocation 50%, intensive review.
- Breach 3+ thresholds: Pull from production, full post-mortem.

**Tier A demotion triggers:**
- Validation rate drops below 40% on re-sweep.
- Decline persists across 2 consecutive sweeps.
- Single catastrophic sweep where rate drops below 20%.

**Non-negotiable controls (anti-Alameda):**
- Per-strategy drawdown limit with auto-flatten.
- Portfolio-level drawdown limit with auto-flatten ALL.
- Independent risk monitor (separate process from execution).
- Audit trail: every trade, parameter change, and override logged.
- Kill switch: one-click flatten of all positions.
- No self-referential collateral.

---

## Appendix: Metrics Reference

### Formulas

| Metric | Formula | Annualization |
|--------|---------|---------------|
| **Sharpe Ratio** | (Mean return - Rf) / Std(returns) | Multiply by sqrt(N) where N = periods/year. Crypto: sqrt(365*24) for hourly, sqrt(365) for daily |
| **Sortino Ratio** | (Mean return - Rf) / Downside deviation | Same annualization as Sharpe. Downside deviation uses only negative returns |
| **Calmar Ratio** | Annualized return / Max drawdown | Already annualized by definition |
| **Profit Factor** | Gross profits / Gross losses | Not annualized; computed over full period |
| **Win Rate** | Winning trades / Total trades | N/A |
| **Payoff Ratio** | Average winning trade / Average losing trade | N/A |
| **Expected Value** | (Win% * Avg_Win) - (Loss% * Avg_Loss) | Per-trade metric |
| **Information Coefficient (IC)** | Spearman rank corr(signal, forward returns) | Computed per-period, then averaged |
| **ICIR** | Mean(IC) / Std(IC) | N/A |
| **Walk-Forward Efficiency (WFE)** | Annualized_Return_OOS / Annualized_Return_IS | Ratio; no annualization needed |
| **PBO** | Fraction of CPCV paths with Sharpe < 0 | N/A |
| **Deflated Sharpe Ratio (DSR)** | Phi((SR_obs - SR_0) * sqrt(T-1) / sqrt(1 - gamma3*SR0 + (gamma4-1)/4 * SR0^2)) | Output is a probability (0-1) |
| **Max Drawdown** | max(peak - trough) / peak over entire period | Not annualized |
| **Tail Ratio** | 95th percentile return / abs(5th percentile return) | N/A |
| **Kelly Fraction** | f* = (bp - q) / b, where b=payoff ratio, p=win rate, q=1-p | Use 1/4 to 1/3 of full Kelly for crypto |

### "Good Enough" Thresholds (Return-First Priority)

**Primary metrics (what we optimize for):**

| Metric | Minimum Acceptable | Good | Excellent |
|--------|--------------------|------|-----------|
| Calmar Ratio (return/maxDD) | > 0.5 | > 1.0 | > 2.0 |
| Sortino Ratio | > 1.0 | > 1.5 | > 2.5 |
| Profit Factor | > 1.3 | > 1.5 | > 2.0 |
| Max Drawdown (hard constraint) | < 25% | < 15% | < 10% |
| V3 Validation Rate | > 20% (Tier B) | > 50% (Tier A) | > 70% |

**Secondary metrics (diagnostic, not goals):**

| Metric | Minimum Acceptable | Good | Excellent |
|--------|--------------------|------|-----------|
| Sharpe Ratio (net, after costs) | > 0.5 | > 1.0 | > 2.0 |
| Win Rate (trend-following) | > 30% | > 40% | > 50% |
| Win Rate (mean-reversion) | > 50% | > 60% | > 70% |
| IC (mean) | > 0.02 | > 0.05 | > 0.10 |
| ICIR | > 0.3 | > 0.5 | > 1.0 |
| WFE | > 50% | > 65% | > 80% |
| PBO | < 30% | < 15% | < 5% |
| DSR | > 0.85 | > 0.95 | > 0.99 |

**Key insight:** A strategy with Sortino 1.5, Calmar 1.2, and max DD 20% is BETTER than one with Sharpe 2.0, Calmar 0.4, and max DD 8%. The first makes more money with acceptable risk. The second barely makes money but looks smooth.

### Red Flag Thresholds

These indicate something is likely wrong -- investigate before proceeding.

| Metric | Red Flag | Likely Cause |
|--------|----------|--------------|
| Sharpe > 3.0 (backtest) | Suspiciously high | Look-ahead bias, overfitting, or cost omission |
| Annualized return > 100% | Unrealistic | Look-ahead bias or survivorship bias |
| Max drawdown < 5% | Too smooth | Look-ahead bias or data error |
| Win rate > 80% | Suspicious | Selling tail risk (premium collection) or data issue |
| SR shortfall (IS vs OOS) > 0.5 | Overfitting | Strategy is curve-fitted |
| SR shortfall > 1.0 | Severe overfitting | Strategy is almost certainly overfit |
| PBO > 50% | Overfit | More than half of CPCV paths are unprofitable |
| Profitable only after removing losing trades | Narrative bias | Cherry-picked backtest |
| Strategy works on only 1-5 tokens | Not generalizable | Likely curve-fitted to specific assets |
| V3 rate drops > 15% between consecutive sweeps | Decay or regime shift | Re-evaluate hypothesis |

### Metrics Selection by Strategy Type (Return-First)

| Strategy Type | Return Metrics (primary) | Risk Constraints (hard limits) | Diagnostic |
|---------------|-------------------------|-------------------------------|------------|
| Trend following | Calmar, Sortino, Profit Factor | Max DD < 25%, DD duration < 90 days | Sharpe, Tail Ratio |
| Mean reversion | Calmar, Profit Factor, Win Rate | Max DD < 20%, losing streak < 10 | Sharpe, Sortino |
| Momentum | Calmar, Sortino, Total Return | Max DD < 25% | Sharpe, Tail Ratio |
| Breakout | Calmar, Payoff Ratio, Profit Factor | Max DD < 25% | Sharpe, Win Rate |

---

## V4 Portfolio Strategy Development (New — March 2026)

### When to Use V4 vs V3

| Use V4 When | Use V3 When |
|-------------|-------------|
| Strategy targets portfolio-level edge | Strategy must prove per-token robustness |
| Strategy was killed at V3 Gate 3 but has portfolio value | New untested hypothesis needs IC/signal validation |
| Building complementary strategies for existing portfolio | Need CPCV/WF validation metrics |
| Testing sideways/choppy market strategies | Evaluating overlay wrappers on proven base |

**Key insight:** V3-killed strategies can succeed in V4. V4's shared capital + multi-token diversification transforms individually weak strategies into useful portfolio components.

### V4 Gate System (AIPIP-0016)

**Class D gate path:** 0→2→V4-3→V4-4→V4-5→6→7

Gate 0 (Idea Screen) and Gate 2 (Knowledge+Dedup) are shared with the V3 pipeline.
V4-specific gates start at V4-3.

```
GATE 0:  Idea Screening (shared)                   (~5 min)     Kill ~50%
  |
  v  GATE 2: Knowledge + Dedup (shared)             (~15 min)    Kill ~30%
  |
  v  V4-GATE 3: 12-Month V4 Backtest               (~15 min)    Kill ~50%
  |  Run: v4/portfolio_backtest.py --strategy sNN --months 12 --capital 200000
  |  Kill: return <+50%, Calmar <0.5, MaxDD >25%, trades <100
  |
  v  V4-GATE 4: OOS Validation (Train→Dec, Jan-Mar) (~10 min)    Kill ~50%
  |  Adjust config.train_bars to end on Dec 31 2025
  |  Kill: OOS negative, <2/3 months positive, March <-5%
  |
  v  V4-GATE 5: Portfolio Complement Test           (~15 min)    Kill ~30%
  |  Add to s58 portfolio, check Sharpe/MaxDD delta
  |  Kill: Sharpe decreases, MaxDD >+2pp, corr >0.7 vs s56/s57
  |
  v  GATE 6: Paper Trading (shared)                 (~1-4 wks)   Kill ~50%
  |  Budget 50-60% Sharpe degradation (Suhonen 2017)
  |
  v  GATE 7: Production (shared)                    (ongoing)
  |  BOCPD + threshold decay detection
```

### V4 OOS Test Template (Train→Dec, Trade Jan-Mar)

```python
from v4.config import PortfolioConfig, StrategySpec
from v4.signals import precompute_strategy_signals, discover_tokens, infer_data_end_date
from v4.simulator import simulate_portfolio
from v4.report import compute_portfolio_metrics
import pandas as pd

strategy_specs = {
    'sNN': StrategySpec(strategy_id='sNN', weight=1.0, max_positions=15, market='perp'),
}
config = PortfolioConfig(
    strategies=list(strategy_specs.values()),
    capital=200_000, exchange='binance',
)

data_end = infer_data_end_date('perp')  # or 'combined', 'spot'
# Push walk-forward mask to Jan 1
trade_start_raw = data_end - pd.DateOffset(months=3)
extra = int((pd.Timestamp('2026-01-01') - trade_start_raw).total_seconds() / 3600)
config.train_bars = 8760 + extra  # 365 days + gap to Jan 1

all_signals = {}
for sid, spec in strategy_specs.items():
    tokens = discover_tokens(spec.market)
    all_signals[sid] = precompute_strategy_signals(spec, tokens, config, 3, end_date=data_end)

state = simulate_portfolio(all_signals, strategy_specs, config)
metrics, extra_info, eq_daily = compute_portfolio_metrics(state, 200_000)
```

### Sideways/Choppy Market Strategy Checklist

Strategies complementing s58 in sideways markets should have:

```
[ ] 1. BIDIRECTIONAL — can go long AND short (requires perp or combined market)
[ ] 2. REGIME-STABLE — positive PnL in RANGE and QUIET regimes (not just UPTREND)
[ ] 3. LOW CORRELATION with s56/s57 — check both 12-month and March-specific correlation
[ ] 4. FUNDING/CARRY EDGE — funding harvesting or basis arbitrage works in all regimes
[ ] 5. V4 OOS MARCH TEST — must be profitable in March 2026 (10 days of choppy sideways)
```

**Priority candidates from V4 sweep (March 2026):**

| Strategy | March PnL | Why It Works Sideways | Next Step |
|----------|-----------|----------------------|-----------|
| s27 funding_mean_rev | +$16.2K | Funding extremes revert in all regimes | Build V4-native version |
| s28 momentum_burst_perp | +$15.2K | Bidirectional catches both sides | Build V4-native version |
| s29 funding_carry | +$8.1K | Pure carry, regime-stable | Already exists, add to portfolio |
| s25 vol_spike_reversal | +$8.4K | Vol spikes in both directions | Build V4-native perp version |

---

## Quick Reference: Gate Decision Matrix

**Remember: Return first, don't lose big.**

```
=== V3 PATH (per-token validation) ===

GATE 0:  Has rationale? + Novel? + Data exists? + Enough trades?
         ALL YES -> Gate 1.  ANY NO -> KILL.

GATE 1:  IC > 0.02? + t-stat > 2.0? + Gross PF > 1.3? + >50 trades?
         ALL PASS -> Gate 2.  Borderline PF -> ONE RETRY.  ELSE -> KILL.

GATE 2:  Calmar > 0.5? + Sortino > 1.0? + DD < 25%? + WFE > 50%? + BTC passes?
         ALL PASS -> Gate 3.  3 ATTEMPTS FAILED -> KILL.
         (Low Sharpe alone does NOT kill — check Calmar/Sortino first)

GATE 3:  V3 rate > 50% -> Tier A -> Gate 4.
         V3 rate 20-50% -> Tier B -> ITERATE (max 3x).
         V3 rate < 20% -> Tier C -> ARCHIVE.

GATE 4:  Paper returns reasonable? + SPRT accepts? + DD < 1.5x backtest?
         ALL PASS -> Gate 5.  ANY FAIL -> KILL.

GATE 5:  Continuous monitoring. Breach 3+ decay thresholds -> PULL.
         Key decay signal: 6 months without new equity high.

=== V4 PATH (Class D — portfolio-level validation, AIPIP-0016) ===

GATE 0:    Same idea screening (choose Class D at routing step).
GATE 2:    Same knowledge + dedup (compare vs V4 candidates, not per-token Tier A).
V4-GATE 3: 12mo backtest: return >+50% + Calmar >0.5 + DD <25% + >100 trades?
V4-GATE 4: OOS Jan-Mar: positive in 2/3 months? March >-5%?
V4-GATE 5: Portfolio complement: Sharpe improves? DD <+2pp? Corr <0.5 vs s56/s57?
GATE 6:    Paper trading: budget 50-60% Sharpe degradation (Suhonen 2017).
GATE 7:    Production: BOCPD + threshold decay detection (Adams & MacKay 2007).

Use V4 path for: portfolio components, sideways strategies, V3-killed strategies with portfolio value.
Use V3 path for: new hypotheses needing IC validation, per-token robustness proof.
```
