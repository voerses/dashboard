# Backtesting & Validation Best Practices

> **TL;DR — Rigorous backtesting and validation methodology**
> - Walk-forward mandatory: 1yr train, 90d OOS, quarterly recalibration; CPCV required for multi-asset
> - Deflated Sharpe Ratio: reject if DSR < 0.05 p-value; PBO < 40% for CPCV pass
> - Tier-based transaction costs: 0.30% Tier 1, 0.37% Tier 2, 0.60% Tier 3 per side (never flat 0.10%)
> - Bias audit: no full-array statistics (use expanding), CPCV non-contiguous folds masked, point-in-time data
> **When to read full file:** Investigating validation failures, debugging CPCV/WF issues, reviewing bias checklist details
> **Sections:** 1-WF, 2-CPCV, 3-Paper Trading, 4-DSR, 5-Metrics, 6-Monte Carlo, 7-Regime, 8-OOS Degradation, 9-Costs, 10-Bias, 11-PBO, 12-Min Length, 13-Capacity, 14-Pipeline, 15-References, 16-Audit Checklist

Comprehensive reference for building statistically rigorous backtesting and validation
pipelines for trading strategies. Drawn from quantitative finance literature, practitioner
consensus, and the work of Lopez de Prado, Bailey, Harvey, and Liu.

---

## Table of Contents

1. [Walk-Forward Analysis](#1-walk-forward-analysis)
2. [Combinatorially Purged Cross-Validation (CPCV)](#2-combinatorially-purged-cross-validation-cpcv)
3. [Paper Trading Best Practices](#3-paper-trading-best-practices)
4. [Deflated Sharpe Ratio](#4-deflated-sharpe-ratio)
5. [Strategy Validation Metrics Hierarchy](#5-strategy-validation-metrics-hierarchy)
6. [Monte Carlo Simulation for Strategy Validation](#6-monte-carlo-simulation-for-strategy-validation)
7. [Regime-Specific Validation](#7-regime-specific-validation)
8. [Out-of-Sample Degradation](#8-out-of-sample-degradation)
9. [Transaction Cost Modeling](#9-transaction-cost-modeling)
10. [Survivorship Bias and Look-Ahead Bias](#10-survivorship-bias-and-look-ahead-bias)
11. [The Backtest Overfitting Problem (PBO)](#11-the-backtest-overfitting-problem-pbo)
12. [Minimum Backtest Length](#12-minimum-backtest-length)
13. [Strategy Capacity Estimation](#13-strategy-capacity-estimation)
14. [Building a Proper Validation Pipeline](#14-building-a-proper-validation-pipeline)
15. [Key Academic References](#15-key-academic-references)
16. [Backtesting Bias Audit Checklist](#16-backtesting-bias-audit-checklist)

---

## 1. Walk-Forward Analysis

### Overview

Walk-forward analysis (WFA) is a dynamic backtesting methodology that repeatedly optimizes a
strategy on an in-sample (IS) window, then tests it on a subsequent out-of-sample (OOS) window,
rolling both windows forward through time. First presented by Robert E. Pardo in 1992, it
simulates the actual process of periodically re-fitting and trading a strategy.

### Anchored vs. Rolling Windows

| Approach | Description | Best For |
|----------|-------------|----------|
| **Rolling** | Both IS and OOS windows slide forward by a fixed increment. The IS window is a fixed-length lookback. | Intraday strategies, regime-adaptive models, strategies that need to forget old data |
| **Anchored (Expanding)** | The IS window start is fixed at the initial date; it grows with each step. The OOS window slides forward. | Strategies that benefit from maximum data, stable long-term patterns |

**Key trade-offs:**
- Rolling windows respond faster to regime changes but use less data per optimization.
- Anchored windows leverage more data but may be slower to adapt to structural breaks.
- Using **both** approaches provides a more comprehensive view of robustness.

### Optimal Window Sizes

There are no universal optimal window sizes. Guidelines:

- **IS window:** Typically 3-5x the length of the OOS window. Must be long enough to
  capture at least one full market cycle (bull + bear + sideways).
- **OOS window:** Long enough to generate a statistically meaningful number of trades
  (typically 30+ trades minimum, ideally 100+).
- Common practical configuration: 5-year IS window, 1-year OOS window.
- Too short an IS window fails to capture sufficient market conditions.
- Too long an IS window reduces the number of OOS validation periods.
- The ratio of IS to OOS windows significantly impacts results and introduces biases.

### Purging and Embargo

Introduced by Marcos Lopez de Prado in *Advances in Financial Machine Learning* (2018),
these techniques address data leakage at the boundary between training and test sets.

#### Purging

Purging removes from the training set any observation whose label formation period overlaps
with the test set. This prevents future information from leaking into the training process.

- Applies to training observations both **before** and **after** the test set.
- Critical when labels span multiple time bars (e.g., triple-barrier method labels).
- Without purging, the model can learn information that will be used to assess its performance.

#### Embargo

After the end of each test period, a fixed percentage of subsequent observations are removed
from the training set. This guards against leakage from:

- Delayed market reactions to events in the test period.
- Auto-correlated features that carry information forward.
- Serial correlation in returns.

**Rule of thumb:** An embargo of h = 0.01T (where T is total observations) often suffices.
Unlike purging, embargo only applies **after** the test set.

### Limitations of Walk-Forward Analysis

- Tests only a **single historical path** -- results are contingent on one specific sequence.
- Can still overfit to the particular ordering of market regimes encountered.
- High variance in performance estimation.
- Reacts to regime shifts rather than predicting them.
- Does not provide a distribution of performance metrics.

These limitations motivate the use of CPCV (Section 2).

---

## 2. Combinatorially Purged Cross-Validation (CPCV)

### Why CPCV Exists

Standard k-fold cross-validation fails in finance because observations are not IID (independently
and identically distributed). Walk-forward analysis tests only a single path. CPCV, developed
by Lopez de Prado (AFML, 2018, Chapter 7), generates **multiple backtest paths** from all
possible train/test combinations, yielding a **distribution** of performance metrics rather
than a single estimate.

### How It Works

**Step 1: Partition into N sequential groups.**
Divide T observations into N non-overlapping, temporally-ordered groups.

**Step 2: Generate combinatorial splits.**
Select k groups (where k < N) as test sets. The remaining N - k groups form the training set.
The number of possible splits is C(N, k) = N! / (k! * (N-k)!).

Example: N=6 groups, k=2 test groups => C(6,2) = 15 possible splits.

**Step 3: Construct backtest paths.**
Each split tests k groups. Total tested groups = k * C(N,k). Since all combinations are
enumerated, tested groups are uniformly distributed across all N groups.
Number of distinct backtest paths = (k * C(N,k)) / N.

Example: (2 * 15) / 6 = 5 distinct backtest paths.

**Step 4: Apply purging and embargo.**
For each split, purge any training observations whose labels overlap with test periods,
and apply an embargo buffer after each test fold.

**Step 5: Evaluate the distribution.**
Each path produces a Sharpe ratio (or other metric). The result is a **distribution of
Sharpe ratios**, not a single point estimate. This enables:
- Computing the Probability of Backtest Overfitting (PBO).
- Computing the Deflated Sharpe Ratio (DSR).
- Statistical inference with confidence intervals.

### Key Advantages Over Walk-Forward

| Feature | Walk-Forward | CPCV |
|---------|-------------|------|
| Number of test paths | 1 | Multiple (C(N,k)/N paths) |
| Performance estimate | Single point | Distribution |
| Sensitivity to data ordering | High | Low |
| Overfitting detection | Weak | Strong (lower PBO) |
| Sample efficiency | Low | High |
| Statistical rigor | Moderate | High |

### Implementation

Available implementations:
- **skfolio** (Python): `CombinatorialPurgedCV` with parameters `n_folds`, `n_test_folds`,
  `purged_size`, `embargo_size`.
- **mlfinlab** (Hudson & Thames): `combinatorial.py` module.
- **Custom**: Follow AFML Chapter 7 carefully. Beware of off-by-one errors in purging logic,
  which cause silent data leakage.

### Implementation Pitfalls

- The logic is significantly more complex than a standard backtest.
- Off-by-one errors in purging can re-introduce leakage (silent failure).
- Incorrect index handling can cause look-ahead bias.
- Bugs in higher-moment statistics can invalidate results.
- The most dangerous aspect: these bugs result in **silent failures** -- the code runs,
  produces results, but those results are wrong.

### Non-Contiguous Fold Contamination (CRITICAL)

**Bug found March 2026 in our own CPCV implementation.**

When CPCV selects two non-adjacent test groups (e.g., groups 0 and 2 out of 6), there is a
gap of in-sample bars between them (group 1). If the entry mask is not restricted to only
test-group bars, trades can enter on gap bars and their PnL contaminates the fold's
out-of-sample result.

**Example:** With 6 groups and 2 test groups, 10 of the 15 folds have non-contiguous test
groups. In those folds, any entry signal firing on a gap bar produces a trade whose PnL is
counted as "out-of-sample" when it is actually in-sample data.

**Impact:** Validation rates appear higher than reality. Strategies look more robust than they
are. In our case, ETH went from PASS to FAIL once the bug was fixed — its edge was a
false positive inflated by contaminated folds.

**Fix:** After extracting the fold's data slice, build an explicit test-only mask:

```python
fold_indices = np.arange(data_start, data_start + fold_len)
test_only_mask = np.isin(fold_indices, test_idx)
masked_entries = result.entry_mask.copy() & test_only_mask
```

**Detection checklist:**
- [ ] For each fold, verify entries occur ONLY on bars whose global index is in `test_idx`
- [ ] Count entries per fold — non-contiguous folds should NOT have more entries than
      contiguous folds of the same total bar count
- [ ] Compare validation rates before/after applying the mask — any change means
      contamination was present

---

## 3. Paper Trading Best Practices

### Duration Guidelines

| Context | Minimum Duration |
|---------|-----------------|
| New strategy, new API | 2-3 months |
| Established strategy, new parameters | 2-4 weeks |
| High-frequency strategy (300+ trades/month) | 1-2 months |
| Low-frequency strategy (<20 trades/month) | 3-6 months |

Key benchmarks:
- At least 50-150 executed setups before going live.
- Executed-to-planned ratio > 70% over 30 trades.
- 60% of algorithmic traders use demos for at least 3 months.
- Maintain paper trading account for at least 1 month to spot slippage discrepancies.

### Metrics to Track During Paper Trading

| Category | Metrics |
|----------|---------|
| **Profitability** | Net profit, profit factor, win ratio, average gain vs. average loss |
| **Risk** | Maximum drawdown (MDD), max drawdown duration, Sharpe ratio, Sortino ratio |
| **Execution Quality** | Fill rate, slippage per trade, latency, partial fill rate |
| **Consistency** | Equity curve smoothness, rolling Sharpe stability, streak analysis |
| **Expectancy** | Per-trade expectancy = (win_rate * avg_win) - (loss_rate * avg_loss) |

Track every fill and latency metric. Adjust strategy parameters only after 50+ trades.

### When to Go Live: Readiness Criteria

1. **Consistency**: Paper trade results match backtest within expected degradation bounds
   (Section 8).
2. **Statistical significance**: Sufficient trades for p < 0.05 on key metrics.
3. **Execution validation**: Slippage and fill rates are within modeled assumptions.
4. **Capital staging**: Start with 10-20% of intended capital for first 3-6 months.
5. **Risk controls**: Kill switches, position limits, and drawdown circuit breakers are tested.
6. **Regime coverage**: Paper trading period included at least one adverse market condition.

### Transition Protocol

1. Go live with **small capital** ($500-$1,000 or 10-20% of target allocation).
2. Limit risk to 1% per trade initially.
3. Run paper and live accounts in parallel for comparison.
4. Scale up only after 3-6 months of live results matching expectations.
5. Never deploy full capital on day one.

---

## 4. Deflated Sharpe Ratio

### The Multiple Testing Problem

The most critical piece of information missing from virtually all published backtests is the
**number of trials attempted**. Without this information, it is impossible to assess the
relevance of a backtest result.

When testing N strategy configurations, the probability of finding at least one with a
"significant" Sharpe ratio grows rapidly -- even if all strategies have zero true alpha.

### The False Strategy Theorem

Even if all tested strategies have true Sharpe Ratios of zero, the highest observed SR will
be positive and appear significant. The Expected Maximum Sharpe Ratio under the null:

**E[max(SR)] grows with sqrt(2 * ln(N))**

Example: After 1,000 independent backtests with E[SR] = 0 and V[SR] = 1, the expected
maximum Sharpe Ratio is **3.26** -- purely from luck.

This is the theoretical foundation: the more configurations tested, the higher the lucky
SR that emerges by chance.

### The Deflated Sharpe Ratio Formula

```
DSR = Phi( (SR* - SR_0) * sqrt(T-1) / sqrt(1 - gamma_3 * SR_0 + ((gamma_4 - 1)/4) * SR_0^2) )
```

Where:
- `SR*` = observed Sharpe ratio of the selected strategy
- `SR_0` = expected maximum SR under the null (from False Strategy Theorem)
- `T` = number of observations
- `gamma_3` = skewness of returns
- `gamma_4` = kurtosis of returns
- `Phi` = standard normal CDF

DSR outputs a **probability** (0 to 1) that the observed SR is statistically significant
after correcting for:
1. Selection bias from multiple testing (via SR_0)
2. Non-normality of returns (via skewness and kurtosis corrections)
3. Sample length (via T)

### Relationship to the Probabilistic Sharpe Ratio (PSR)

The PSR corrects for sample length, frequency, skewness, and kurtosis but assumes **only
1 trial** was run. It answers: "Is this SR significantly different from a benchmark SR?"

The DSR extends the PSR by incorporating the number of trials (N). It answers: "Is this SR
significant given that we selected the best of N strategies?"

### The Unbounded Nature of the Problem

The False Strategy Theorem shows that the optimal outcome of multiple simulations is
**right-unbounded**: with enough trials, no Sharpe ratio is large enough to reject the
null hypothesis. It is **guaranteed** that a researcher will find a misleadingly profitable
strategy after sufficient trials.

### Practical Usage

1. Record **all** trials attempted, not just the winning strategy.
2. Determine clusters of effectively independent trials (correlated trials count as fewer).
3. Plan multiple testing exercises in advance to minimize the number of trials.
4. Every additional trial irremediably increases the probability of a false positive.
5. A DSR > 0.95 provides reasonable confidence the strategy is not a statistical fluke.

### Limitations

- Relies on a normal approximation corrected by skew and kurtosis.
- Requires an assumed form for the distribution of SRs under the null.
- Requires an accurate count of N (often difficult to reconstruct).
- DSR is about statistical credibility, not economic robustness.

---

## 5. Strategy Validation Metrics Hierarchy

### Tier 1: Core Risk-Adjusted Returns

#### Sharpe Ratio

**Formula:** (Mean return - Risk-free rate) / Standard deviation of returns

**Problems with the Sharpe Ratio:**
- Assumes normally distributed returns (penalizes upside volatility equally).
- Sensitive to the measurement period and frequency.
- Easily inflated by multiple testing (see Section 4).
- Does not distinguish between upside and downside volatility.
- Can be gamed by strategies that sell far-OTM options (collect premium, rare blowups).
- Live Sharpe is typically **30-50% lower** than backtested Sharpe.

**Benchmarks:** SR > 0.5 acceptable, SR > 1.0 good, SR > 2.0 excellent (pre-haircut).

#### Sortino Ratio

**Formula:** (Mean return - Risk-free rate) / Downside deviation

- Focuses exclusively on **downside risk** by using only negative return deviations.
- Does not penalize positive volatility.
- Superior to Sharpe for asymmetric return distributions.
- **Best for:** Trend-following strategies, any strategy with positively skewed returns.

**Benchmarks:** Sortino > 1.0 means earning more than downside volatility.

#### Calmar Ratio

**Formula:** Annualized return / Maximum drawdown

- Measures return earned per unit of maximum drawdown.
- High drawdowns devastate compounding, making this metric critical.
- **Best for:** Conservative strategies, capital preservation mandates.

**Benchmarks:** Calmar > 2.0 indicates good risk-adjusted performance.

### Tier 2: Profitability Metrics

#### Profit Factor

**Formula:** Gross profits / Gross losses

- Simple, intuitive measure of overall profitability.
- Profit Factor > 1 = profitable; > 1.5 = good; > 2.0 = strong.
- Does not account for risk or volatility.

#### Win Rate vs. Payoff Ratio Matrix

The win rate alone is meaningless without the payoff ratio (average win / average loss).

| Win Rate | Payoff Ratio | Strategy Type | Viability |
|----------|-------------|---------------|-----------|
| 30-40% | 2.0-5.0x | Trend following | Viable -- few winners, but large |
| 50-60% | 1.0-1.5x | Mean reversion | Viable -- frequent small wins |
| 60-70% | 0.5-1.0x | High-probability setups | Marginal -- small edge, needs volume |
| 80%+ | < 0.5x | Premium selling | Dangerous -- tail risk blowups |

**The key relationship:** Expected value = (Win% * Avg_Win) - (Loss% * Avg_Loss)

A 30% win rate with a 3:1 payoff ratio produces the same expectancy as a 60% win rate
with a 1:1 payoff ratio. But their risk profiles differ dramatically.

### Tier 3: Drawdown and Tail Metrics

#### Maximum Drawdown (MDD)

- The largest peak-to-trough decline in portfolio value.
- **Maximum Drawdown Duration** (MDDD) is equally important: how long until recovery?
- A 50% drawdown requires a 100% gain to recover.
- Strategies should be evaluated on **both** the depth and duration of drawdowns.

**Guideline:** MDD > 25% in backtesting is a red flag for most strategies.

#### Tail Ratio

**Formula:** 95th percentile of returns / abs(5th percentile of returns)

- Compares extreme positive returns to extreme negative returns.
- Tail Ratio > 1.0 means positive tails are larger than negative tails.
- **Best for:** Mean-reversion strategies (tells you if extreme moves are in your favor).
- Trend-following strategies typically have tail ratios of 3-10x (winners much larger
  than losers), making the metric less discriminating for that style.

#### Common Sense Ratio

**Formula:** Profit Factor * Tail Ratio

- Combines profitability assessment with tail behavior.
- Popularized by Laurent Bernut (Alpha Secure Capital).
- Works for both mean-reversion and trend-following strategies.
- Captures the essential character of a strategy in a single number.
- Higher is better; there is no fixed benchmark, but compare across candidate strategies.

### Metrics Selection by Strategy Type

| Strategy Type | Primary Metrics | Secondary Metrics |
|---------------|----------------|-------------------|
| Trend following | Sortino, Tail Ratio, Max DD Duration | Calmar, Common Sense Ratio |
| Mean reversion | Tail Ratio, Win Rate, Profit Factor | Sharpe, Sortino |
| Market making | Sharpe, Win Rate, Max DD | Profit Factor, Sortino |
| Statistical arbitrage | Sharpe, Calmar, Max DD | Sortino, Profit Factor |
| Momentum | Sortino, Calmar, Profit Factor | Tail Ratio, Common Sense Ratio |

### Multi-Metric Validation Rule

**Never trust a single metric.** A robust strategy must pass multiple filters. If one
metric is excellent but another is terrible, investigate before deploying.

---

## 6. Monte Carlo Simulation for Strategy Validation

### Purpose

Monte Carlo simulation generates thousands of synthetic equity curves by resampling a
strategy's historical returns, producing a **distribution of possible outcomes** rather
than a single backtest path. This reveals path-dependent risks that a single backtest hides.

### Sampling Methods

#### Method 1: Sampling Without Replacement (Permutation)

- Randomly shuffles the order of historical returns.
- **Does not change** the mean return or volatility -- only the sequence.
- Best for: drawdown analysis, equity curve shape analysis.
- Answers: "Given these returns in a different order, how bad could drawdowns get?"

#### Method 2: Sampling With Replacement (Bootstrap)

- Draws returns randomly from the historical set, allowing repeats.
- **Does change** the distribution -- some returns are duplicated, others omitted.
- Produces higher dispersion in results than permutation.
- Best for: stress testing, confidence interval estimation.
- Makes no distributional assumptions (non-parametric).

#### Method 3: Parametric Simulation

- Fits a statistical distribution (normal, t-distribution, etc.) to historical returns.
- Generates synthetic returns from the fitted distribution.
- Allows modeling of fat tails, skewness, and other features.
- Best for: sensitivity analysis, extreme scenario generation.
- Requires careful distribution selection; wrong assumptions invalidate results.

### What Monte Carlo Reveals

| Analysis | Method | Output |
|----------|--------|--------|
| Maximum drawdown distribution | Permutation or Bootstrap | 95th percentile worst-case MDD |
| Probability of ruin | Bootstrap | P(account falls below threshold) |
| Confidence intervals on returns | Bootstrap | 5th/95th percentile annualized return |
| Tail risk assessment | Parametric with fat tails | Expected shortfall (CVaR) |
| Equity curve dispersion | All methods | Range of possible equity paths |

### Procedure for Strategy Validation

1. Collect the strategy's per-trade or per-period returns (including fees and slippage).
2. Choose a simulation method (bootstrap is most common).
3. Run 10,000+ simulation paths.
4. For each path, compute the equity curve and all key metrics (SR, MDD, Calmar, etc.).
5. Analyze the **distribution** of each metric.
6. Set confidence thresholds: "Is the 5th percentile Sharpe Ratio still positive?"
7. If the 5th percentile outcome is unacceptable, the strategy is too risky.

### Choosing Between Trade Returns and Period Returns

- **Trade returns**: Better for swing traders who focus on individual trade analysis.
- **Period returns** (daily, weekly): Better for diversified portfolios, easier to compare
  across strategies.

### Limitations

- Bootstrap assumes returns are approximately independent (autocorrelation violates this).
- Cannot simulate regime changes or structural breaks.
- Fat-tailed distributions are poorly captured by normal assumptions.
- Does not model market impact or liquidity constraints.
- Overfitted strategies will produce optimistic Monte Carlo results (garbage in, garbage out).

### Variance Reduction Techniques

- **Antithetic variates**: For each random draw, also use its negation. Reduces sampling error.
- **Control variates**: Use a correlated instrument with known analytics to reduce variance.

---

## 7. Regime-Specific Validation

### Why Regime Validation Matters

A strategy that works only in bull markets or only in low-volatility environments is not
robust. Many strategies that appear profitable over a long backtest are actually capturing
returns from a single favorable regime.

### Market Regime Categories

| Regime | Characteristics | Duration |
|--------|----------------|----------|
| Bull / Low volatility | Rising prices, tight spreads, low VIX | Months to years |
| Bear / High volatility | Falling prices, wide spreads, high VIX, correlation spikes | Weeks to months |
| Sideways / Choppy | Range-bound, mean-reverting, moderate volatility | Weeks to months |
| Crisis / Tail event | Extreme moves, liquidity collapse, correlation -> 1 | Days to weeks |
| Recovery | Transition from bear to bull, high dispersion | Weeks to months |

### Regime Detection Methods

#### Hidden Markov Models (HMMs)

- Model the market as a system with hidden (unobservable) states.
- Each state generates observable returns from a different distribution.
- **Bullish regime**: Positive mean, low variance Gaussian.
- **Bearish regime**: Negative or zero mean, high variance Gaussian.
- Typically 2-3 hidden states are used.
- Three parameters to tune: number of hidden states, covariance type, and maximum iterations
  for the EM (expectation-maximization) algorithm.
- Must retrain periodically (e.g., daily with a sliding window) to stay current.

#### Rule-Based Methods (Harding-Pagan)

- Define bull/bear regimes based on observable rules (e.g., 20% drawdown from peak = bear).
- More transparent and less subjective than HMM parameter choices.
- Better suited for ex-post regime dating.
- Simpler to implement and audit.

### Regime-Specific Validation Protocol

1. **Classify historical data into regimes** using HMM or rule-based methods.
2. **Evaluate strategy performance in each regime separately.**
3. **Require positive expectancy in at least 2 out of 3 major regimes.**
4. **Document which regimes the strategy struggles in** -- this informs risk management.
5. **Train regime-specific models** (optional): Separate models for each regime, activated
   by the regime detector.

### Regime-Aware Risk Management

- Use the HMM as a **risk filter**: disallow new positions when a high-volatility regime
  is detected.
- Close open positions upon regime transition to bear/crisis.
- Reduce position sizes during ambiguous regime transitions.
- This approach can reduce max drawdown by 50%+ vs. buy-and-hold, though Sharpe may not
  increase dramatically.

### Validation Requirements

- The backtest **must** include data from at least one major crisis period (2008, 2020,
  2022 crypto winter).
- A strategy that has never been tested in a bear market is untested.
- Any returns data used for regime detection must **not** overlap with the backtest
  test period (prevent look-ahead bias).

---

## 8. Out-of-Sample Degradation

### Expected Performance Loss

| Stage | Typical Performance Retained |
|-------|------------------------------|
| In-sample (IS) | 100% (by definition) |
| Out-of-sample (OOS) | 70-85% of IS |
| Paper trading | 60-80% of IS |
| Live trading | 50-80% of IS |

**Rule of thumb from Quantpedia:** OOS performance is approximately **4/5 (80%)** of IS
performance for published academic strategies.

**Sharpe decay:** Research shows the Sharpe of newly published factors decays by
approximately 5% per year.

### Sources of Degradation

| Source | Impact | Mitigation |
|--------|--------|------------|
| Overfitting | 10-40% loss | CPCV, PBO analysis, parameter parsimony |
| Transaction costs | 5-20% loss | Realistic cost models (Section 9) |
| Slippage | 2-10% loss | Market simulation, limit order modeling |
| Regime change | 0-100% loss | Regime validation (Section 7) |
| Data quality issues | Variable | Point-in-time data, survivorship-free datasets |
| Alpha decay | 5%/year | Monitor signal half-life, adapt |
| Behavioral effects | 5-15% loss | Automation, rules-based execution |

### The Progressive Decline Pattern

A well-constructed strategy shows a **gradual, expected** decline at each stage:

Example: SR = 1.4 (development) -> 1.2 (optimization) -> 1.1 (OOS) -> 0.8-0.9 (live)

If the decline is **sudden and severe** (e.g., SR 1.4 -> 0.2), the strategy is overfit.

### Mitigation Strategies

1. **Apply a haircut**: Assume live performance will be 50-70% of backtested performance.
   Budget for this decline.
2. **Use CPCV**: Generates a distribution of performance, not a single optimistic number.
3. **Include realistic costs**: All backtests must include transaction costs (Section 9).
4. **Walk-forward validation**: Continuously validate on unseen data.
5. **Monitor live performance vs. expectations**: Set tripwires for when live performance
   diverges too far from expected.
6. **Regime-aware expectations**: Accept lower performance in adverse regimes.

---

## 9. Transaction Cost Modeling

### Components of Transaction Costs

#### 1. Explicit Costs (Easily Measured)

| Cost Type | Description | Typical Range |
|-----------|-------------|---------------|
| Exchange fees | Per-trade fees charged by the exchange | 0.01-0.10% (maker/taker) |
| Brokerage commissions | Broker's per-trade fee | 0.01-0.05% |
| Clearing & settlement | Post-trade processing fees | Minimal for crypto |
| Funding rates | Cost of leveraged positions (perpetual swaps) | Variable, -0.1% to +0.1% per 8h |

#### 2. Slippage (Harder to Measure)

Slippage is the difference between the expected execution price and the actual fill price.

**Determinants:**
- Bid-ask spread width
- Order size relative to available liquidity
- Market volatility at execution time
- Order type (market vs. limit)
- Execution algorithm quality

**Directional bias:**
- **Momentum strategies** suffer more slippage (buying into rising prices, selling into falling).
- **Mean-reversion strategies** suffer less (buying into dips, selling into rallies).

#### 3. Market Impact (Most Complex)

Market impact is the cost incurred from supply/demand dynamics when trading.

**Temporary impact:** Price displacement that reverts after the trade.
**Permanent impact:** Lasting price change from information revealed by the trade.

**Models:**

| Model | Formula | Accuracy | Complexity |
|-------|---------|----------|------------|
| Flat | Fixed cost per trade | Low | Low |
| Linear | Impact proportional to order size | Medium | Low |
| Square root | Impact proportional to sqrt(order size) | High | Medium |
| Quadratic | Impact proportional to order size squared | Highest | High |

The **square root model** is the most empirically validated:
`Impact = sigma * sqrt(Q / V)` where sigma = volatility, Q = order quantity, V = daily volume.

### Realistic Cost Assumptions for Crypto

| Component | Conservative Estimate | Aggressive Estimate |
|-----------|----------------------|---------------------|
| Exchange fee (maker) | 0.02% | 0.00% (rebate) |
| Exchange fee (taker) | 0.05% | 0.03% |
| Slippage per trade | 0.05-0.10% | 0.02-0.05% |
| Market impact (small orders) | 0.01-0.05% | Negligible |
| Market impact (large orders) | 0.10-0.50%+ | 0.05-0.20% |
| Funding rate (per 8h, perps) | -0.01% to +0.03% | Variable |

### Exchange-Specific and Tier-Based Costs (IMPORTANT)

**Lesson learned March 2026:** Using a single flat fee/slippage for all tokens materially
understates costs and inflates validation rates. Different tokens have vastly different
execution costs depending on liquidity.

The V3 engine uses **tier-based costs** defined in `v3/universe.py` (`TIER_COSTS`):

| Tier | Tokens | Fee (taker) | Slippage (bps) | Total per side | Round-trip |
|------|--------|-------------|----------------|----------------|------------|
| 1 (>$50M ADV) | BTC, ETH, SOL, SUI, XRP | 0.22% | 8 bps | 0.30% | 0.60% |
| 2 ($10-50M ADV) | ADA, AVAX, DOT, LINK, NEAR | 0.22% | 15 bps | 0.37% | 0.74% |
| 3 ($5-10M ADV) | BONK, FLOKI, PENGU, DENT, OM | 0.25% | 35 bps | 0.60% | 1.20% |

These are calibrated to **Kraken Pro at $200K-$500K monthly volume** (taker side). See
`knowledge/KRAKEN_FEES.md` for the full tier schedule and derivation.

**Previously the engine used 0.10% fee + 5 bps slippage (0.15% per side) for all tokens.**
This was Binance base-tier pricing — approximately 2x too low for Tier 1 on Kraken and
3-5x too low for Tier 3. When we applied realistic costs, ETH dropped from PASS to FAIL
on S11 — its edge was a false positive that only survived under optimistic cost assumptions.

**Rule: Never use a single flat cost model.** Always differentiate by:
1. Exchange (Kraken vs Binance have very different fee schedules)
2. Volume tier (monthly volume determines your fee tier)
3. Token liquidity (BTC spread is 0.01%; BONK spread can be 1%+)

### Best Practices for Cost Modeling

1. **Never backtest without transaction costs.** A zero-cost backtest is fiction.
2. **Use tier-based costs** that reflect your actual exchange, volume tier, and token liquidity.
3. **Use the square root model** for market impact, not flat fees.
4. **Add a safety margin**: Include an extra 0.05-0.10% beyond measured costs.
5. **Calibrate regularly**: Compare modeled costs to actual execution costs.
6. **Account for funding rates**: For strategies using perpetual swaps, funding costs can
   dominate for longer holding periods.
7. **Use event-driven simulation** for the most realistic execution modeling.
8. **Model partial fills**: Large orders may not fill completely at the quoted price.
9. **Re-validate when changing exchanges.** A strategy validated on Binance costs may fail
   on Kraken costs. Always re-run validation after changing cost assumptions.

### Transaction Cost Analysis (TCA)

- **Pre-trade TCA**: Estimate costs before execution to choose optimal algo and parameters.
- **Post-trade TCA**: Compare actual execution vs. benchmarks (TWAP, arrival price).
- Run TCA as a continuous feedback loop to improve execution quality.

---

## 10. Survivorship Bias and Look-Ahead Bias

### Survivorship Bias

#### What It Is

Survivorship bias occurs when analysis includes only entities that have survived (remain
listed, remain solvent, remain in an index) while ignoring those that failed, delisted,
or were removed.

#### Impact

- Can inflate annual returns by **1-4%** with a compounding effect over time.
- A momentum strategy on the S&P 100 showed CAGR of 26% with bias vs. 12.2% without --
  most of the "alpha" was survivorship bias.
- Hedge fund drawdowns underestimated by **14 percentage points** on average when
  survivorship bias is present.
- QIM discovered actual returns were 8% vs. projected 20% after correcting for survivorship.

#### Detection

- Unusually high returns or low volatility in the dataset.
- Historical data that does not include delisted, bankrupt, or removed entities.
- Using current index constituents to represent historical periods.

#### Prevention

1. Use **point-in-time (PIT) data** that includes all entities that existed at each
   historical date, regardless of their current status.
2. Use a **time-varying universe**: the constituent list should match what was actually
   available to trade at each point in time.
3. Include **delisted, merged, bankrupt, and removed** securities in the dataset.
4. Use databases like CRSP, Compustat, or survivorship-free crypto data providers.
5. For crypto: include tokens that have gone to zero, been delisted from exchanges,
   or lost liquidity.

### Look-Ahead Bias

#### What It Is

Look-ahead bias occurs when information that would not have been available at the time of
a trading decision is incorporated into the backtest.

#### Common Sources

- Using **end-of-day prices** for intraday decisions.
- Calculating indicators using **future prices** (e.g., a moving average that includes
  data from after the decision point).
- Using **today's index constituents** for historical analysis (preinclusion bias).
- Accessing **financial reports** before their actual publication date.
- Using **adjusted prices** without accounting for the adjustment date.
- **Regime detection using full-dataset statistics** (see below).

#### Regime/Indicator Look-Ahead (CRITICAL)

**Bug found March 2026 in our own engine.** `detect_daily_regime()` computed volatility
percentiles using `np.percentile(vol_20, 75)` across the entire dataset. This means a bar
in 2022 was classified using 2022–2026 volatility statistics — classic look-ahead.

**Why it's insidious:** The regime filter doesn't directly predict returns, so its look-ahead
bias is subtle. It doesn't make the backtest look "too good to be true." Instead, it
makes regime transitions slightly more prescient than they should be, inflating regime
filter effectiveness by a few percent.

**Fix:** Use expanding (causal) statistics with a minimum lookback period:

```python
# WRONG: uses future data
vol_p75 = np.percentile(vol_20, 75)

# RIGHT: causal — each bar sees only its own past
vol_p75 = pd.Series(vol_20).expanding(min_periods=60).quantile(0.75).values
```

**General rule:** Any function that computes a statistic over the full array and uses it
to classify individual bars is a look-ahead bug. This includes:
- `np.percentile()`, `np.mean()`, `np.std()` on the full array
- Scikit-learn's `StandardScaler.fit_transform()` on the full dataset
- HMM/clustering fitted on the entire history

**Audit checklist for regime/indicator code:**
- [ ] Every percentile, mean, or std is computed with `.expanding()` or `.rolling()`
- [ ] `min_periods` is set to a reasonable value (30–60 bars minimum)
- [ ] Early bars where statistics are unstable default to a neutral/conservative state
- [ ] No sklearn `.fit()` on the full dataset — use `.partial_fit()` or rolling windows

#### Detection

- Backtest performance that seems "too good to be true."
- Strategy performs perfectly at turning points (suspiciously prescient entries/exits).
- Same code crashes or produces different results in live trading.
- Regime distribution is suspiciously well-balanced (look-ahead makes classification
  more "accurate" than it would be in real-time).

#### Prevention

1. **Timestamp all input data** with its original release/availability date.
2. Calculate indicators using **only past data** at each decision point.
3. Use the **same code** for backtesting and live trading.
4. Implement strict **data access controls** in the backtesting engine.
5. Apply **execution delays** that match real-world latency.
6. Conduct **out-of-sample testing** on truly unseen data.
7. Audit code rigorously for any forward-looking references.
8. **Use expanding/rolling statistics, never full-dataset statistics** for any classification
   or threshold used in trading decisions.

### Other Biases to Watch

| Bias | Description | Mitigation |
|------|-------------|------------|
| Data snooping | Testing many hypotheses on the same data until one "works" | DSR, PBO, Bonferroni correction |
| Selection bias | Reporting only successful strategies | Record all trials |
| Period selection | Backtesting only in favorable market conditions | Include all regimes |
| Optimization bias | Over-fitting parameters to historical data | Walk-forward, CPCV |
| Confirmation bias | Interpreting results to confirm prior beliefs | Blinded analysis |

---

## 11. The Backtest Overfitting Problem (PBO)

### The Problem

It is trivially easy for a computer to explore millions of strategy parameter combinations
and pick the one that performed best in-sample. This "optimal" strategy often performs
terribly out-of-sample because the parameters were overfit to noise.

**Backtest overfitting is the principal reason many systematic funds disappoint.**

### Probability of Backtest Overfitting (PBO)

PBO, developed by Bailey, Borwein, Lopez de Prado, and Zhu (2013/2015), estimates the
probability that a strategy's in-sample performance will not generalize out-of-sample.

#### How PBO Works: Combinatorially Symmetric Cross-Validation (CSCV)

1. Divide the return matrix into N segments.
2. Generate all C(N, N/2) combinations of segments into two equal halves.
3. For each combination: optimize on one half (IS), evaluate on the other (OOS).
4. Compute the rank of the IS-optimal strategy among all OOS strategies.
5. If the IS-optimal strategy ranks below the median OOS, it is considered overfit.
6. **PBO = fraction of combinations where the IS-optimal strategy underperforms the
   OOS median.**

#### Interpretation

| PBO | Interpretation |
|-----|---------------|
| < 0.10 | Low overfitting risk -- strategy is likely robust |
| 0.10 - 0.30 | Moderate risk -- investigate further |
| 0.30 - 0.50 | High risk -- significant overfitting likely |
| > 0.50 | Very high risk -- strategy is probably overfit |

#### Additional PBO Outputs

- **Performance degradation**: How much worse does IS-optimal perform OOS?
- **Probability of loss**: What fraction of OOS evaluations show negative returns?
- **Stochastic dominance**: Does any alternative strategy dominate the "optimal" one?

### Why Standard Holdout Fails

Standard hold-out testing (split data into train/test) is unreliable for investment
backtests because:
- A single split is high-variance (depends heavily on which period is held out).
- Researchers often run many holdout tests and report the best one (data leakage).
- The holdout set may not represent future market conditions.

### Implementation

- R package: `pbo` on CRAN
- Python: Custom implementations following Bailey et al.
- The method is **model-free and non-parametric**: it works with any performance metric
  (Sharpe, Sortino, Jensen's Alpha, etc.).

---

## 12. Minimum Backtest Length

### Statistical Floor

| Requirement | Minimum | Rationale |
|-------------|---------|-----------|
| Central Limit Theorem | ~30 trades | Distribution of sample means approximates normal |
| Basic metric reliability | ~100 trades | Sufficient for basic Sharpe, win rate estimates |
| Institutional confidence | 200-500 trades | Lopez de Prado recommendation |
| Statistical significance | p < 0.05 | Depends on effect size |

### Time Coverage Requirements

| Strategy Type | Minimum Time Coverage |
|---------------|----------------------|
| High-frequency (>10 trades/day) | 3-6 months |
| Intraday (1-10 trades/day) | 6-12 months |
| Swing trading (1-5 trades/week) | 2-3 years |
| Position trading (1-5 trades/month) | 5-10 years |
| Long-term investing | 10-20+ years |

**Critical requirement:** The backtest MUST include bull, bear, AND sideways market data.
500 trades in 6 months (one regime) is less reliable than 100 trades over 5 years
(multiple regimes).

### Why Small Samples Are Dangerous

- A 65% win rate over 20 trades has a p-value > 0.2 (not significant).
- The same 65% win rate over 200 trades has a p-value < 0.01 (significant).
- Small samples cannot distinguish real edge from random noise.

### The Correlated Trades Problem

- 300 highly correlated trades may provide less information than 80 independent trades.
- Correlation between trades reduces **effective sample size**.
- Strategies that trade the same instrument on consecutive bars have highly correlated returns.
- Effective N = N / (1 + 2 * sum(autocorrelations)).

### Multiple Testing Correction for Minimum Length

- Testing 10 parameter variations raises overfit probability significantly.
- Testing 100+ variations makes spurious results highly likely.
- The **Minimum Backtest Length (MinBTL)** increases with the number of configurations tested.
- Research shows: testing just 7 strategy variations can produce at least one 2-year
  backtest with annualized SR > 1.0 purely by chance.

### Minimum Track Record Length (MinTRL)

Lopez de Prado provides the formula for the minimum number of observations needed
to conclude that a strategy's Sharpe Ratio is statistically different from a benchmark:

```
MinTRL = 1 + (1 - gamma_3 * SR + (gamma_4 - 1)/4 * SR^2) * (z_alpha / SR)^2
```

Where gamma_3 is skewness, gamma_4 is kurtosis, SR is the observed Sharpe Ratio,
and z_alpha is the critical value for the desired significance level.

---

## 13. Strategy Capacity Estimation

### What Is Capacity?

Capacity is the maximum capital a strategy can deploy before market impact degrades
performance below an acceptable threshold. Every strategy has a finite capacity.

### How Market Impact Degrades Returns

As capital increases:
1. Orders become larger relative to available liquidity.
2. Slippage increases non-linearly (square root law).
3. The strategy's own trades move the market against it.
4. Alpha per trade decreases.
5. At some point, transaction costs exceed the alpha generated.

### Key Determinants of Capacity

| Factor | Effect on Capacity |
|--------|-------------------|
| Asset liquidity (volume, depth) | Higher liquidity = higher capacity |
| Signal persistence (half-life) | Longer-lasting signals = higher capacity |
| Trade frequency / turnover | Higher turnover = lower capacity |
| Number of tradeable assets | More assets = higher capacity (diversification) |
| Execution algorithm quality | Better algos = higher capacity |
| Strategy alpha strength | Stronger alpha = higher capacity (can absorb more cost) |
| Market microstructure | Varies by venue, time of day, asset |

### Empirical Benchmarks

| Strategy Type | Typical Capacity |
|---------------|-----------------|
| HFT / Market making | $1M - $50M |
| Statistical arbitrage (equities) | $100M - $1B |
| Momentum (equal-weighted) | ~$200M |
| Momentum (value-weighted) | ~$2B |
| Quality / Value (low turnover) | $5B - $50B+ |
| Crypto momentum (major pairs) | $1M - $50M (varies with market conditions) |
| Crypto mean reversion (altcoins) | $100K - $5M |

### Estimating Capacity

**Method 1: Empirical scaling test**
- Simulate the strategy at increasing capital levels.
- Plot Sharpe ratio vs. AUM.
- Identify the inflection point where returns begin to degrade.

**Method 2: Regression approach**
- Regress realized Sharpe ratio against AUM for similar strategies.
- Extrapolate the AUM at which SR drops below the minimum acceptable level.

**Method 3: Volume participation limit**
- Set a maximum percentage of daily volume per instrument (typically 1-5%).
- Capacity = max_participation_rate * average_daily_volume * number_of_instruments.

### Managing Capacity Constraints

1. **Monitor capacity utilization**: Track what fraction of estimated capacity is being used.
2. **Diversify across assets**: Trade more instruments to increase total capacity.
3. **Reduce turnover**: Longer holding periods reduce market impact per unit of alpha.
4. **Improve execution**: Better algorithms extract more alpha per trade.
5. **Close the strategy to new capital** before performance degrades.
6. **Watch for alpha decay**: As more capital chases the same signals, capacity shrinks.

---

## 14. Building a Proper Validation Pipeline

### The Sequential Gate Model

A validation pipeline is a series of sequential quality gates. A strategy must pass each
gate before proceeding to the next. Earlier gates are cheaper and faster; later gates are
more expensive and realistic. This structure prevents wasting resources on flawed strategies.

```
Gate 1: Data Quality & Bias Audit
    |
    v
Gate 2: In-Sample Development (Initial Signal Validation)
    |
    v
Gate 3: Walk-Forward / CPCV Validation
    |
    v
Gate 4: Statistical Significance Testing
    |
    v
Gate 5: Monte Carlo Stress Testing
    |
    v
Gate 6: Regime-Specific Validation
    |
    v
Gate 7: Transaction Cost & Capacity Analysis
    |
    v
Gate 8: Paper Trading (Forward Test)
    |
    v
Gate 9: Live Deployment (Small Capital)
    |
    v
Gate 10: Continuous Monitoring & Adaptation
```

### Gate Details

#### Gate 1: Data Quality & Bias Audit

- Verify data is survivorship-bias-free.
- Confirm point-in-time accuracy (no look-ahead bias).
- Check for data gaps, outliers, and corporate actions.
- Validate the tradeable universe at each historical date.
- **Kill criterion:** Data fails quality checks.

#### Gate 2: In-Sample Development

- Develop the strategy using 60% of available data.
- Focus on economic rationale, not just statistical fit.
- Minimize the number of free parameters.
- Document every trial and configuration tested.
- Record the total number of trials (N) for DSR calculation.
- **Kill criterion:** No economic rationale for the signal.

#### Gate 3: Walk-Forward / CPCV Validation

WF and CPCV are **complementary, not alternatives** (Lopez de Prado 2018, Arian et al. 2024):
- **WF** simulates production deployment (rolling retrain) — answers "does this strategy work?"
- **CPCV** tests robustness across all combinatorial paths — answers "should you believe it?"
- Deploy only strategy/token pairs that pass BOTH gates.

Steps:
- Run walk-forward analysis (365d train, 90d test, 5d purge).
- Run CPCV with purging and embargo (6 groups, 2 test, 15 splits).
- Generate a distribution of Sharpe ratios, not a single number.
- Compute PBO from the CPCV results.
- **Kill criteria:** PBO > 0.30, median OOS Sharpe < 0.5.

#### Gate 4: Statistical Significance Testing

- Compute the Deflated Sharpe Ratio using the total number of trials.
- Compute the haircut Sharpe Ratio (Harvey-Liu method).
- Verify DSR > 0.95 (95% confidence the SR is not a fluke).
- Verify Minimum Track Record Length is satisfied.
- **Kill criteria:** DSR < 0.95, haircut SR < 0.5.

#### Gate 5: Monte Carlo Stress Testing

- Run 10,000+ bootstrap simulations.
- Evaluate the 5th percentile worst-case for all key metrics.
- Verify the strategy survives extreme drawdown scenarios.
- Assess probability of ruin at the planned capital level.
- **Kill criteria:** 5th percentile Sharpe < 0, P(ruin) > 5%.

#### Gate 6: Regime-Specific Validation

- Classify historical data into regimes (bull/bear/sideways/crisis).
- Evaluate strategy performance in each regime separately.
- Require positive expectancy in at least 2 of 3 major regimes.
- Document which regimes are adverse and how the strategy should be managed during them.
- **Kill criterion:** Strategy loses money in 2+ regimes.

#### Gate 7: Transaction Cost & Capacity Analysis

- Model all transaction costs (fees, slippage, market impact, funding).
- Re-run the backtest with realistic costs.
- Estimate strategy capacity at the planned capital level.
- Verify that after-cost returns are still acceptable.
- **Kill criteria:** After-cost Sharpe < 0.5, capital exceeds 50% of estimated capacity.

#### Gate 8: Paper Trading (Forward Test)

- Deploy the strategy in a paper trading environment for 1-3 months.
- Compare paper trading results to backtested expectations.
- Monitor execution quality, slippage, and fill rates.
- Track at least 50 trades before proceeding.
- **Kill criteria:** Paper results degrade > 50% from backtest, execution issues identified.

#### Gate 9: Live Deployment (Small Capital)

- Deploy with 10-20% of target capital.
- Run for 3-6 months alongside the paper trading system.
- Compare live results to paper trading and backtest.
- Implement kill switches and circuit breakers.
- **Kill criteria:** Live results degrade > 30% from paper trading.

#### Gate 10: Continuous Monitoring & Adaptation

- Monitor daily: portfolio temperature, position performance, correlation drift.
- Monitor weekly: rolling Sharpe, regime classification, capacity utilization.
- Monitor monthly: strategy-level statistics, alpha decay, cost analysis.
- Pre-set stop-loss levels for individual positions and the overall strategy.
- Define re-decomposition triggers (when to redesign or retire the strategy).

### Pipeline Principles

1. **Be conservative:** It is better to reject a potentially profitable strategy than to
   deploy one that will lose money.
2. **Document everything:** Every trial, every configuration, every decision.
3. **Fail fast:** Earlier gates should be quick to evaluate. Don't run expensive Monte
   Carlo simulations on a strategy that fails basic statistical tests.
4. **Never skip gates:** Each gate catches different types of problems.
5. **Automate where possible:** Manual gates introduce human bias and inconsistency.

---

## 15. Key Academic References

### Core Papers

#### Bailey, Borwein, Lopez de Prado -- "Pseudo-Mathematics and Financial Charlatanism" (2014)

Published in the *Notices of the American Mathematical Society*, Vol. 61(5), pp. 458-471.

**Key findings:**
1. High simulated performance is easily achievable after backtesting a small number
   of strategy configurations.
2. The higher the number of configurations tried, the greater the probability of overfitting.
3. Most financial analysts rarely report the number of configurations tried, making it
   impossible for investors to evaluate the degree of overfitting.
4. Investors can be easily misled into allocating capital to strategies that appear
   mathematically sound but are statistical flukes.
5. The problem is pervasive in both industry offerings and academic research.

**Companion tools developed:**
- Combinatorially Symmetric Cross-Validation (CSCV) for estimating PBO.
- Deflated Sharpe Ratio (DSR) for correcting performance inflation.
- Online demonstration tools for illustrating backtest overfitting.

**Implication:** Any backtest result presented without the number of trials attempted is
essentially unfalsifiable and should be treated with extreme skepticism.

#### Harvey, Liu, Zhu -- "...and the Cross-Section of Expected Returns" (2016)

Published in *The Review of Financial Studies*, Vol. 29(1), pp. 5-68.

**Key findings:**
1. Hundreds of papers claiming to explain the cross-section of returns are subject to
   massive multiple testing bias.
2. The standard t-ratio threshold of 2.0 is inadequate. A newly discovered factor needs
   a **t-ratio > 3.0** to be credible.
3. **Most claimed research findings in financial economics are likely false** (echoing
   concerns from the medical literature).
4. Three multiple testing adjustment methods are prescribed:
   - **Bonferroni**: Most conservative, inflates p-values by the number of tests.
   - **Holm**: Sequential adjustment, less conservative than Bonferroni.
   - **BHY** (Benjamini-Hochberg-Yekutieli): Controls false discovery rate, most lenient.

#### Harvey, Liu -- "Backtesting" (2015)

**The Haircut Sharpe Ratio:**
- Adjusts an observed Sharpe Ratio for the number of trials, autocorrelation between
  trials, overall performance level, and presumed correlation between trials.
- Typical haircut: **50-60%** of the original Sharpe Ratio.
- Example: An observed SR of 0.75 might have a haircut SR of only 0.325.
- Implementation available in R's `quantstrat` package (`SharpeRatio.haircut`).

**The mixed distribution model:**
- Null hypothesis (zero alpha): Normal distribution.
- Alternative hypothesis (non-zero alpha): Exponential distribution.
- Reflects the economic intuition that more profitable strategies are less likely to
  exist (economic scarcity).

### Additional Key References

| Paper | Authors | Year | Key Contribution |
|-------|---------|------|-----------------|
| *Advances in Financial Machine Learning* | Lopez de Prado | 2018 | CPCV, purging/embargo, triple barrier method |
| *The Probability of Backtest Overfitting* | Bailey, Borwein, LdP, Zhu | 2015 | PBO framework, CSCV methodology |
| *The Deflated Sharpe Ratio* | Bailey, LdP | 2014 | DSR formula, False Strategy Theorem |
| *The Sharpe Ratio Efficient Frontier* | LdP | 2016 | Optimal combination of strategies |
| *Building Diversified Portfolios that Outperform OOS* | LdP | 2016 | Hierarchical Risk Parity (HRP) |
| *Machine Learning for Asset Managers* | LdP | 2020 | Feature importance, clustering, labeling |

---

## 16. Backtesting Bias Audit Checklist

Run this checklist whenever modifying engine code, adding indicators, or reviewing
validation results. Each item addresses a real bug found in production backtesting systems.

### Cost Model Biases

```
[ ] Fee rate reflects actual exchange tier, not Binance base rate
    - Kraken Tier 1: 0.22% taker, Tier 3: 0.25%. NOT 0.10% flat.
[ ] Slippage varies by token liquidity tier
    - BTC: 8 bps, mid-cap: 15 bps, small-cap: 35 bps
[ ] Costs applied on BOTH entry and exit
[ ] If exchange or volume tier changed, re-validate all strategies
```

### Look-Ahead Biases

```
[ ] No np.percentile/np.mean/np.std on full arrays for bar-level classification
    - Use .expanding() or .rolling() with min_periods instead
[ ] Regime detection uses only data available at bar start
[ ] No sklearn fit_transform() on full dataset for normalization/clustering
[ ] Early bars (before min_periods) default to neutral/conservative state
[ ] Forward-fill gaps use shift(1) — never fill with current bar's data
```

### CPCV Implementation Biases

```
[ ] Non-contiguous fold entries restricted to test_idx bars only
    - Gap bars between non-adjacent test groups must be masked out
[ ] Purge/embargo fraction >= 0.01 of total data
[ ] Warmup bars excluded from PnL calculation (not just from entries)
[ ] Entry mask applied AFTER fold slicing, not before
```

### Data Biases

```
[ ] Universe uses point-in-time constituents (no survivorship bias)
[ ] Delisted tokens included in historical analysis
[ ] Daily data aligned to UTC midnight, not exchange local time
[ ] No future data in any feature: verify with shift(1) or expanding()
```

---

## Summary: The Non-Negotiable Rules

1. **Never backtest without realistic, tier-based transaction costs.** A flat 0.10% fee
   for all tokens is fiction. Use exchange-specific, liquidity-tiered costs.
2. **Never report a Sharpe ratio without the number of trials.** Without N, the SR is meaningless.
3. **Never trust a single backtest path.** Use CPCV to generate a distribution.
4. **Always compute PBO and DSR.** These are the minimum credibility checks.
5. **Apply a 30-50% haircut** to backtested Sharpe ratios for live expectations.
6. **Test across regimes.** A strategy that only works in one regime is a time bomb.
7. **Use point-in-time data.** Survivorship and look-ahead bias silently inflate results.
8. **Never use full-dataset statistics for bar-level decisions.** Use expanding/rolling.
9. **Verify CPCV fold isolation.** Non-contiguous folds contaminate silently.
10. **Paper trade before live trading.** Minimum 50 trades, ideally 1-3 months.
11. **Start small.** 10-20% of target capital for the first 3-6 months of live trading.
12. **Document everything.** Every trial, every configuration, every decision.
13. **Automate the pipeline.** Manual validation introduces human bias.
14. **Know your capacity.** Exceeding it destroys the very alpha you are trying to capture.
15. **Re-validate when assumptions change.** New exchange, new fee tier, new token list →
    re-run the full validation pipeline.
