# Causal Analysis for Crypto Trading Strategy Development

*Comprehensive research survey for scientifically rigorous strategy discovery*
*Last updated: 2026-02-28*

---

## Table of Contents

1. [Causal Inference Frameworks for Trading](#1-causal-inference-frameworks-for-trading)
2. [Avoiding Overfitting and P-Hacking](#2-avoiding-overfitting-and-p-hacking)
3. [What Actually Causes Crypto Price Movements](#3-what-actually-causes-crypto-price-movements)
4. [Indicator Selection via Causal Analysis](#4-indicator-selection-via-causal-analysis)
5. [Recombination Testing Methodology](#5-recombination-testing-methodology)
6. [Key Recent Papers (2020-2026)](#6-key-recent-papers-2020-2026)

---

## 1. Causal Inference Frameworks for Trading

### 1.1 Granger Causality

**Definition:** A signal X Granger-causes Y if past values of X improve the prediction of Y beyond what past values of Y alone provide (Granger, 1969).

**Proper implementation:**
- Estimate a VAR(p) model: Y_t = a + sum(b_i * Y_{t-i}) + sum(c_i * X_{t-i}) + e_t
- Test H0: all c_i = 0 via F-test or likelihood ratio
- Select lag order p via AIC/BIC, not arbitrary choice
- Stationarity is REQUIRED: apply ADF test first, difference if needed
- Use multivariate VAR for systems with >2 variables to avoid omitted variable bias

**Critical pitfalls (proven, not speculative):**
1. **Spurious causality from common drivers:** If Z drives both X and Y with different lags, X may Granger-cause Y spuriously. Multivariate VAR including Z resolves this.
2. **Nonlinearity blindness:** Granger causality is fundamentally linear. Financial time series exhibit well-documented nonlinearities (volatility clustering, regime switches). Linear GC will miss nonlinear causal channels entirely. Marinazzo et al. (2008) showed linear and nonlinear GC can give contradictory results.
3. **Fixed lag assumption:** Standard GC assumes a fixed time delay between cause and effect. In financial markets, the lag between an information signal and price adjustment varies with liquidity, volatility regime, and market attention. Amornbunchornvej et al. (2021) proposed Variable-lag Granger Causality to address this (ACM TKDD).
4. **High-dimensional failure:** With many candidate causes, standard GC becomes inefficient and prone to mis-fitting. LASSO-VAR or penalized GC methods are needed for high-dimensional systems (Shojaie & Fox, 2022, Annual Review of Statistics).
5. **Non-stationarity:** Financial returns may be locally stationary but exhibit structural breaks. Rolling-window GC or time-varying parameter VAR is more appropriate than full-sample estimation.

**Recommendation for our system:** Use Granger causality as a first-pass linear filter, but never as the sole causal test. Always supplement with nonlinear methods.

### 1.2 Transfer Entropy

**Definition:** An information-theoretic, model-free generalization of Granger causality. Measures the reduction in uncertainty about Y's future when conditioning on X's past, beyond what Y's own past provides. Formally:

TE(X->Y) = H(Y_t | Y_{t-1:t-k}) - H(Y_t | Y_{t-1:t-k}, X_{t-1:t-k})

Where H is Shannon entropy.

**Advantages over Granger causality:**
- Captures nonlinear dependencies (proven: Schreiber, 2000, Physical Review Letters)
- Model-free: no assumption about functional form
- Asymmetric: TE(X->Y) != TE(Y->X), enabling directional inference
- Can detect information flow that linear methods miss entirely

**Pitfalls:**
1. **Sample size hunger:** Nonparametric entropy estimation requires substantially more data than parametric GC. With daily crypto data (762 days in our dataset), estimation variance may be large. Kernel density and k-nearest-neighbor estimators (Kraskov et al., 2004) help but don't eliminate the issue.
2. **Bin size sensitivity:** Histogram-based TE is sensitive to the number of bins. Use adaptive binning or continuous estimators.
3. **Computational cost:** Scales poorly with embedding dimension. For practical use, limit to pairwise TE with moderate lag orders (k <= 5 for daily data).
4. **Statistical significance:** Unlike GC which has well-defined F-statistics, TE significance requires surrogate/bootstrap testing. Use time-shifted surrogates (Theiler et al., 1992) or block bootstrap.

**Application to our indicators:** Transfer entropy is the right tool to test whether ADX's predictive power (IC=0.067) represents genuine information flow about future returns versus a statistical artifact. Compute TE(ADX -> returns) and compare against surrogate distribution.

**Key reference:** Dimpfl & Peter (2013), "Using Transfer Entropy to Measure Information Flows Between Financial Markets," *Studies in Nonlinear Dynamics & Econometrics*.

### 1.3 Convergent Cross Mapping (CCM)

**Definition:** Developed by Sugihara et al. (2012, Science). Based on Takens' theorem: if X and Y are causally coupled in a dynamical system, they share a common attractor. One can reconstruct (or "cross-map") the state of X from the shadow manifold of Y, and the accuracy of this reconstruction converges as library size increases.

**When CCM is appropriate:**
- When the underlying system is deterministic and nonlinear (plausible for some aspects of market microstructure)
- When variables are coupled in a dynamical system (feedback loops)
- When GC and TE give inconclusive results due to weak coupling

**When CCM is NOT appropriate:**
- Stochastic-dominated systems (much of financial returns at daily frequency)
- High noise levels overwhelm the attractor structure
- Short time series (CCM needs convergence, which requires substantial data)

**CRITICAL WARNING for financial applications:** Krakovska et al. (2018) and our search results indicate that the false positive rate of surrogate CCM in financial data is approximately 90% for stock indices. Correlation effectively acts as a proxy for what CCM detects in financial data, meaning CCM rarely adds information beyond simpler methods in this domain.

**Recommendation:** CCM is theoretically appealing but practically unreliable for daily crypto data. Use only as a supplementary check, never as a primary causal test. If used, always compare against correlation-based null models.

### 1.4 Directed Acyclic Graphs (DAGs) and Structural Causal Models

**The Pearl framework (Pearl, 2009, *Causality: Models, Reasoning, and Inference*):**

Pearl's Structural Causal Model (SCM) framework provides the most rigorous theoretical foundation for causal reasoning. Key components:

1. **Structural equations:** Each variable is a deterministic function of its parents plus noise: X_i = f_i(PA_i, U_i)
2. **Graphical representation:** DAG encodes which variables directly cause which others
3. **Do-calculus:** Three rules for computing interventional distributions P(Y | do(X=x)) from observational data P(Y | X=x), given the graph structure
4. **Pearl's Causal Hierarchy:**
   - Level 1 (Association): P(Y|X) -- what we observe
   - Level 2 (Intervention): P(Y|do(X)) -- what happens if we act
   - Level 3 (Counterfactual): P(Y_x|X=x', Y=y') -- what would have happened

**Application to trading -- an honest assessment:**

The DAG/SCM framework is the gold standard for causal reasoning, but its application to trading faces fundamental challenges:

- **Graph discovery is hard:** Algorithms like PC, FCI, and GES require assumptions (faithfulness, causal sufficiency) that are unlikely to hold in financial markets. Latent confounders (unobserved institutional flows, regulatory changes, whale activity) violate causal sufficiency.
- **Interventional reasoning is limited:** Traders observe markets; they don't intervene on macroeconomic variables. The "do" operator is conceptually useful (what would happen to BTC if we "set" DXY to 100?) but practically unidentifiable without strong assumptions.
- **Graph instability:** The causal graph among financial variables changes over time (regime shifts). A static DAG is misleading.

**Where DAGs ARE useful for us:**
- Encoding domain knowledge about causal structure (e.g., "Fed rate -> DXY -> BTC" is a reasonable causal chain)
- Identifying which conditioning sets avoid confounding when estimating indicator effects
- Guiding feature selection: if we believe ADX -> trend_continuation -> returns, then ADX is a legitimate predictor; if instead returns -> trend -> ADX (reverse causation), ADX is merely descriptive

**Practical approach:** Don't try to learn the full causal graph from data. Instead, postulate a small DAG based on domain knowledge, test its implications, and use it to guide analysis.

### 1.5 Lopez de Prado's Approach

In *Advances in Financial Machine Learning* (2018), Lopez de Prado takes a pragmatic rather than purely theoretical approach to causality:

1. **Feature importance via MDI and MDA:** Mean Decrease Impurity and Mean Decrease Accuracy from Random Forests. MDI is biased toward high-cardinality features; MDA with shuffled features is more reliable (Chapter 8).
2. **SHAP values:** For understanding which features drive predictions in tree-based models. Not strictly causal, but reveals conditional dependencies.
3. **Purged/embarked cross-validation:** The primary defense against false discovery, rather than explicit causal modeling (Chapter 7).
4. **Triple barrier method:** Labels returns based on first-to-hit of profit-take, stop-loss, or time expiry. This changes the prediction target from "what will the return be?" to "will price hit target X before stop Y within horizon T?" -- a more causally meaningful question for trading.
5. **Meta-labeling:** A second model predicts the probability that the primary model's signal is correct. This is implicitly a causal question: "given the signal, what causes it to succeed or fail?"

**Key insight from Lopez de Prado:** The biggest enemy of strategy discovery is not lack of causal theory but lack of proper statistical hygiene. A well-validated correlation-based strategy beats a poorly validated "causal" strategy every time.

### 1.6 Summary: Which Framework to Use When

| Method | Best For | Worst For | Data Needs | Our Priority |
|--------|----------|-----------|------------|-------------|
| Granger Causality | Linear lead-lag, first pass | Nonlinear systems | Moderate (200+ obs) | HIGH |
| Transfer Entropy | Nonlinear info flow, direction | Small samples, high-dim | High (500+ obs) | MEDIUM |
| CCM | Deterministic dynamical systems | Stochastic, noisy data | Very high | LOW |
| DAGs/SCMs | Domain knowledge encoding | Automated discovery in finance | Domain expertise | MEDIUM |
| Lopez de Prado ML | Practical feature importance | Formal causal claims | Moderate | HIGH |

---

## 2. Avoiding Overfitting and P-Hacking

This is the single most important section. The probability that a discovered trading strategy is a false positive is far higher than most practitioners realize.

### 2.1 The Scale of the Problem

**Harvey, Liu, Zhu (2016), "...and the Cross-Section of Expected Returns," *Review of Financial Studies*, 29(1), 5-68:**

- Catalogued 316 factors claimed to predict equity returns (1967-2014)
- Found that the standard t-statistic threshold of 2.0 is woefully inadequate given the multiple testing implicit in decades of factor research
- **New minimum threshold: t > 3.0** (and likely higher given unreported tests)
- Using their criterion, only 9 of 313 return-correlated variables survive
- Estimated that 71% of all tried factors were never published (the "file drawer problem"), meaning the true multiple testing burden is far larger than what appears in the literature

**Direct implications for our work:** We tested 18 indicators across 49 tokens with multiple forward horizons. That's roughly 18 x 49 x 4 = 3,528 implicit tests. At a naive 5% significance level, we'd expect ~176 false positives. ADX's IC of 0.067 needs to be evaluated against this backdrop.

### 2.2 Multiple Testing Corrections

#### Bonferroni Correction
- Simplest: divide alpha by number of tests. If testing 100 strategies at 5%, each needs p < 0.0005.
- **Too conservative** when tests are correlated (as financial indicators are). Controls Family-Wise Error Rate (FWER) but at the cost of power.

#### Benjamini-Hochberg (BH) / Benjamini-Hochberg-Yekutieli (BHY)
- Controls False Discovery Rate (FDR) rather than FWER
- BH assumes independence or positive dependence among tests
- BHY allows arbitrary dependence (more conservative but safer for financial data where correlation structure is complex)
- **Harvey et al. (2016) use BHY** and find the implied t-ratio threshold ranges from 2.78 to 3.68 depending on significance level and assumptions

#### Romano-Wolf Stepdown
- Romano & Wolf (2005), "Stepwise Multiple Testing as Formalized Data Snooping," *Econometrica*, 73, 1237-1282
- More powerful than Bonferroni: can identify multiple outperforming strategies while controlling FWER
- Stepwise procedure: first step is like White's Reality Check, subsequent steps remove identified strategies and re-test
- Uses bootstrap to account for correlation among test statistics
- **Recommended** when we want to identify ALL strategies that genuinely beat a benchmark, not just test whether the best one does

**Recommendation for our system:** Use BHY for initial indicator screening (controls FDR). Use Romano-Wolf when comparing a set of candidate strategies against a benchmark.

### 2.3 White's Reality Check and Hansen's SPA Test

#### White's Reality Check (White, 2000, *Econometrica*, 68(5), 1097-1126)
- Tests H0: the best model found by data mining has no predictive superiority over a benchmark
- Uses stationary bootstrap to generate the null distribution
- Accounts for the full universe of models searched, not just the one selected
- **Weakness:** Conservative because it evaluates under the "least favorable configuration" -- performance of poor models inflates the null distribution

#### Hansen's Superior Predictive Ability Test (Hansen, 2005, *JBES*, 23(4), 365-380)
- Improves on White's RC by:
  1. Using a studentized test statistic (reduces influence of erratic forecasts)
  2. Using a sample-dependent null distribution (not least favorable configuration)
- More powerful: detects genuine outperformance that White's RC misses
- Less sensitive to the inclusion of many poor alternatives

#### Stepwise SPA (Hsu, Hsu & Kuan, 2010, *Journal of Empirical Finance*, 17(3), 471-484)
- Extends SPA to identify ALL predictive models (not just test whether the best one is real)
- Proven to be more powerful than Romano-Wolf in the specific context of trading rule evaluation
- Consistent: identifies violated nulls with probability approaching 1
- Controls FWER at any pre-specified level

**Practical application:** After identifying candidate strategies via our indicator analysis, submit the full set of strategies (including many we've discarded) to Hansen's SPA test. The included "bad" strategies are important -- they represent the search space we implicitly tested.

### 2.4 CPCV -- Combinatorially Purged Cross-Validation

**Source:** Lopez de Prado (2018), *Advances in Financial Machine Learning*, Chapter 12. Also Bailey, Borwein, Lopez de Prado & Zhu (2017).

**Already documented in our VALIDATION_METHODS.md.** Key points for causal analysis context:

- CPCV generates a DISTRIBUTION of out-of-sample Sharpe ratios, not a single point estimate
- PBO (Probability of Backtest Overfitting) = fraction of CPCV paths where in-sample optimized strategy underperforms OOS
- If PBO > 50%, the strategy is more likely overfit than not
- Our implementation: 6 groups, 2 test groups = 15 combinatorial splits

**Connection to causal analysis:** A genuinely causal relationship (ADX predicts returns because trend strength causes momentum continuation) should produce LOW PBO. A spurious correlation should produce HIGH PBO because the relationship won't persist across different timeline orderings. CPCV is thus an indirect test of causality.

### 2.5 Minimum Backtest Length (MinBTL)

**Source:** Bailey & Lopez de Prado (2012), "The Sharpe Ratio Efficient Frontier," *Journal of Risk*, 15(2).

Formula: MinBTL = (1 + (1 - skew * SR + ((kurtosis - 1)/4) * SR^2)) * (z_alpha / SR)^2

Where SR is the annualized Sharpe ratio and z_alpha is the critical value.

**Key insight:** For a strategy with SR=1.0 and normal returns, you need at least ~4 years of data to be 95% confident the SR is positive. For SR=0.5 (more realistic for crypto), you need ~16 years. With non-normal returns (high kurtosis, as in crypto), the requirement is even longer.

**Implications for our system:** Our dataset spans 762 trading days (~2.1 years). This is INSUFFICIENT to validate strategies with Sharpe ratios below approximately 1.5 at 95% confidence with normal returns. Given crypto's fat tails (documented in our FAT_TAIL_ANALYSIS.md), the effective minimum is even higher.

**Mitigation:** Cross-token validation provides pseudo-independent observations. If a strategy works across 20+ tokens, the effective sample size is larger than any single token's history. But tokens are correlated (especially during market-wide moves), so the effective independence is partial.

### 2.6 Deflated Sharpe Ratio (DSR)

**Source:** Bailey & Lopez de Prado (2014), "The Deflated Sharpe Ratio: Correcting for Selection Bias, Backtest Overfitting, and Non-Normality," *Journal of Portfolio Management*, 40(5), 94-107.

DSR adjusts a strategy's Sharpe ratio for:
1. **Number of strategies tried** (the more you try, the more likely you find a false positive)
2. **Non-normality of returns** (skewness and kurtosis inflate apparent SR)
3. **Sample length** (shorter backtests have wider SR confidence intervals)

Formula incorporates: the variance of SR estimates across trials, the maximum expected SR from N independent trials, and corrections for skewness/kurtosis.

**DSR < 0 means the observed SR is likely a statistical artifact.**

**For our system:** If we tested 50 strategy variants and our best has SR=2.0, the DSR will be substantially lower. The exact value depends on the distribution of SRs across all variants tested.

### 2.7 The False Strategy Theorem

**Source:** Bailey, Borwein, Lopez de Prado & Zhu (2014), "Pseudo-Mathematics and Financial Charlatanism: The Effects of Backtest Overfitting on Out-of-Sample Performance," *Notices of the AMS*, 61(5), 458-471.

**Core result:** Given enough trials, ANY Sharpe ratio can be achieved in-sample, even from random data. Specifically, for N independent strategy trials on T observations, the expected maximum Sharpe ratio is approximately:

E[max SR] ~ (1 - gamma) * Phi^{-1}(1 - 1/N) * sqrt(2 * ln(N))

where gamma is the Euler-Mascheroni constant and Phi^{-1} is the inverse normal CDF.

For N=100 trials: E[max SR] ~ 2.56 (annualized, from pure noise)
For N=1000 trials: E[max SR] ~ 3.26
For N=10000 trials: E[max SR] ~ 3.89

**This is the most sobering result in quantitative finance.** It means that without rigorous multiple testing correction, nearly any backtested strategy can appear profitable.

### 2.8 Practical Anti-Overfitting Protocol for Our System

Based on the above, the required protocol is:

1. **Pre-register hypotheses:** Before testing, write down which indicators you expect to predict returns and why (causal reasoning). This limits effective N.
2. **Count ALL trials:** Include indicator selection, parameter tuning, lookback period selection, and threshold selection in the trial count.
3. **Apply BHY correction** to all indicator IC values at the screening stage.
4. **Run CPCV** on any strategy that passes screening. Require PBO < 0.40.
5. **Compute DSR** using the full set of strategies attempted (not just the ones that worked).
6. **Check MinBTL:** If the strategy's implied MinBTL exceeds our data length, treat results as preliminary.
7. **Cross-token validation:** Strategy must work on majority of tokens, not just the ones where it was discovered.
8. **Out-of-time holdout:** Reserve the most recent 6 months of data as a final holdout that is NEVER used during development.

---

## 3. What Actually Causes Crypto Price Movements

### 3.1 Established Causal Channels (Strong Evidence)

#### 3.1.1 Monetary Policy -> Liquidity -> Crypto

**Mechanism:** Fed rate hikes -> higher DXY -> tighter global liquidity -> reduced risk appetite -> crypto drawdowns. The reverse (rate cuts, QE) drives crypto rallies.

**Evidence:**
- DXY-BTC correlation: approximately -0.4 to -0.8 over 2019-2024 (time-varying)
- Q4 2022: Fed hikes pushed DXY above 114; BTC collapsed from $47K to under $16K
- Q4 2023: Dollar weakened; BTC surged past $40K
- DXY-BTC correlation was -0.65 in Q1 2024

**Causal direction:** Strongly one-directional (macro -> crypto). BTC does not meaningfully cause DXY movements. Confirmed by VAR analysis and Granger causality tests.

**Caveat:** The relationship is time-varying and regime-dependent. Post-ETF approval (January 2024), BTC's correlation with equities increased while its negative correlation with DXY persisted but may be evolving. A VAR study on July 2023 - July 2024 data found DXY had no significant impact on crypto volatility during that specific window, suggesting the relationship is not constant.

**References:**
- Wavelet analysis of BTC-DXY (2025, Journal of Risk and Financial Management)
- Frontiers in Blockchain (2025): "Long-term nexus of macroeconomic and financial fundamentals with cryptocurrencies"

#### 3.1.2 Funding Rate -> Leveraged Unwinding -> Price

**Mechanism:** Extreme positive funding rates in perpetual futures indicate crowded long positions. When prices dip, liquidation cascades force leveraged longs to sell, amplifying the move. Extreme negative funding rates indicate crowded shorts and can trigger short squeezes.

**Evidence:**
- Perpetual futures now account for 93% of crypto futures markets and >$100B daily volume (Kim & Park, 2025)
- He, Manela, Ross & von Wachter (2024, "Fundamentals of Perpetual Futures") provide theoretical foundations
- Empirically: total liquidation events correlate with funding rate extremes and subsequent reversals
- BIS Working Paper 1087 (2024, "Crypto Carry") documents that leveraged positions and the basis are strongly connected

**Causal direction:** Bidirectional feedback loop. High funding -> vulnerability to liquidation -> price crash -> funding reset. This is a genuine causal mechanism, not mere correlation.

**Practical implication:** Funding rate extremes are among the most defensible causal predictors in crypto. We should add funding rate data to our feature set.

#### 3.1.3 BTC -> Altcoin Returns (Cross-Asset Spillover)

**Evidence:**
- Bayesian network analysis (Springer, 2024) confirms Bitcoin and Ethereum significantly influence price fluctuations of other cryptocurrencies
- Tether behaves distinctly and stands isolated
- The causal direction is primarily BTC -> alts, not bidirectional

**Mechanism:** BTC price changes trigger portfolio rebalancing, liquidations of BTC-margined altcoin positions, and narrative/sentiment spillovers. This is well-established and non-controversial.

### 3.2 Partially Established Channels (Moderate Evidence)

#### 3.2.1 Momentum (Short-Term)

**Evidence:**
- Han, Kang & Ryu (2023, SSRN): Comprehensive analysis of time-series and cross-sectional momentum in crypto under realistic assumptions
- Huang, Sangiorgi & Urquhart (2024, SSRN): Volume-weighted time-series momentum shows predictability
- Cryptocurrency Factor Momentum (2023, *Quantitative Finance*, 23(12)): Factor premia show consistent patterns -- past winners outperform losers

**Causal explanations (competing, not resolved):**
1. **Behavioral underreaction:** Investors are slow to update beliefs on new information (Hong & Stein, 1999). Supported by crypto's retail-dominated market, which is more susceptible to behavioral biases (Journal of Economic Dynamics and Control, 2025).
2. **Information diffusion:** News propagates slowly across crypto's fragmented market structure. Early informed traders create initial moves; the rest follow.
3. **Reflexive feedback:** Price increases attract attention, which attracts buying, which increases prices. Soros's reflexivity theory. This is a genuine causal mechanism but creates fragile momentum that reverses sharply.

**Important nuance:** Momentum in crypto is SHORT-LIVED. Unlike equity momentum (6-12 month horizon), crypto momentum operates on days to weeks. The European Journal of Finance study found "very limited overall predictability of Bitcoin returns" except for short-horizon momentum.

#### 3.2.2 On-Chain Metrics -> Price

**MVRV (Market Value to Realized Value):**
- First proposed by Mahmudov & Puell (2018)
- Glassnode (2023) documents its use for cycle identification: high MVRV -> overvaluation, low MVRV -> undervaluation
- MDPI (2024) found MVRV peaks align with price highs and troughs with price lows in a Fama-MacBeth framework

**Exchange flows:**
- Net exchange inflows (coins moving to exchanges) often precede selling pressure
- Net outflows suggest accumulation
- Forecasting Bitcoin Volatility from Whale Transactions (ResearchGate) found CryptoQuant data (including exchange whale ratio, fund flow ratio) helps predict extreme volatility spikes

**Causal status:** These are DESCRIPTIVE of aggregate investor behavior, not independent causal drivers. MVRV high => many holders are in profit => likely to sell. The "cause" is the distribution of investor cost bases, which MVRV merely measures. Still useful for prediction, but the causal chain is: market structure -> MVRV signal -> predicted sell pressure.

#### 3.2.3 Social Media / Attention -> Price

- Amirzadeh et al. (2023, SSRN) found social media (Twitter data) is the most significant influencing factor for altcoin prices
- JEDAC (2025) found increased investor attention leads to higher returns and greater volatility
- However, the causal direction is ambiguous: does attention cause price moves, or do price moves cause attention? Likely bidirectional.

### 3.3 Weakly Established Channels (Speculative/Unproven)

#### 3.3.1 Technical Indicators as Independent Causes

**The hard truth:** Technical indicators are transformations of price and volume data. They cannot contain information that is not already in the raw data. They are SUMMARIES, not independent information sources.

Why do some predict returns? Three possibilities:
1. **Behavioral:** Enough traders watch the same indicators that they become self-fulfilling prophecies (coordination equilibrium). This is fragile and prone to crowding.
2. **Information compression:** The indicator efficiently compresses a pattern in price/volume that human eyes would miss. The causality runs from the underlying pattern, not the indicator.
3. **Spurious:** The indicator's apparent predictive power is a multiple testing artifact.

This is directly relevant to our finding that ADX has IC=0.067. See Section 4.

### 3.4 Regime Dependence

**A critical finding across the literature:** Causal relationships in crypto are regime-dependent.

- AIMS Press (2025): "Causality indicated more stable and statistically significant causal relationships in the calm regime than in the volatile regime."
- The DXY-BTC relationship varies from -0.8 to near-zero depending on the period
- Momentum works in trending markets, mean-reversion works in ranging markets
- Funding rate signals are most useful at extremes, noise in between

**Implication:** Any causal model that assumes time-invariant relationships will fail. Our strategy system MUST be regime-aware. This is not optional.

---

## 4. Indicator Selection via Causal Analysis

### 4.1 Mutual Information vs Correlation

**Correlation** measures linear dependence. For financial data with documented nonlinearities, it systematically underestimates true dependence.

**Mutual Information** (MI) captures ALL statistical dependency (linear and nonlinear). It equals zero if and only if two variables are truly independent. This makes it strictly superior to correlation as a dependence measure.

**Practical caveats:**
- MI estimation requires more data than correlation (nonparametric)
- For normally distributed data, MI and correlation contain the same information: MI = -0.5 * log(1 - rho^2)
- For non-normal data (i.e., financial returns), MI can detect dependencies that correlation misses
- Use k-nearest-neighbor estimators (Kraskov et al., 2004) rather than binning for continuous data

**Recommendation:** Compute MI between each indicator and forward returns as a complement to IC (Information Coefficient = rank correlation). If MI >> what correlation implies, there's nonlinear predictive content.

### 4.2 LASSO / Elastic Net for Sparse Signal Extraction

**Why LASSO matters for our problem:** We have 18 indicators, many correlated (see INDICATOR_ANALYSIS.md). OLS regression on all 18 will overfit. LASSO (L1 penalty) forces coefficients to exactly zero, performing automatic feature selection.

**Implementation notes:**
- Use time-series cross-validation (purged) for lambda selection, NOT standard CV
- Elastic Net (L1 + L2) is more stable than pure LASSO when predictors are correlated (our indicators have correlations up to 0.95)
- The features that survive LASSO selection are the ones with genuine marginal predictive power after accounting for redundancy
- LASSO selects ONE from each correlated group; which one it selects depends on noise, so don't over-interpret which specific indicator survives

**Recent advance:** LLM-Lasso (2025, arXiv) integrates domain knowledge into penalty terms, potentially useful for incorporating our prior beliefs about which indicators should matter.

### 4.3 Random Forest / SHAP for Feature Importance

**Not causal, but informative:**
- Random Forest feature importance (MDI) is biased toward high-cardinality features (Lopez de Prado, 2018)
- Mean Decrease Accuracy (MDA) with permutation is more reliable: shuffle one feature, measure performance drop
- SHAP values (Lundberg & Lee, 2017) provide consistent, locally accurate feature attributions

**Causal interpretation:** SHAP tells you which features the model uses, not which features causally affect returns. A model might use a spuriously correlated feature if it happens to predict well in-sample. Always validate feature importance OOS.

### 4.4 Conditional Independence Testing

**The right question:** Does indicator X predict returns AFTER controlling for all other indicators?

**Methods:**
- Partial correlation: linear version, easy to compute
- Conditional mutual information: nonlinear version, data-hungry
- PC algorithm: uses conditional independence tests to learn DAG structure
- Conditional independence testing via kernels (Zhang et al., 2012, UAI): nonparametric, handles nonlinear confounding

**Application to our indicator set:** Test whether ADX predicts returns conditionally on volatility measures (realized_vol, ATR_pct). If ADX's IC vanishes after conditioning on volatility, then ADX is merely a proxy for volatility, not an independent signal.

### 4.5 Our Finding: ADX is #1 Predictor (IC=0.067) -- Causal Mechanism Analysis

**ADX (Average Directional Index)** was created by J. Welles Wilder (1978). It measures trend strength regardless of direction by smoothing the difference between +DI and -DI over 14 periods.

**Why ADX might genuinely predict forward returns (causal hypothesis):**

1. **Regime classification channel:** ADX identifies whether the market is trending (ADX > 25) or ranging (ADX < 20). In trending regimes, returns are serially correlated (momentum). Therefore, knowing the regime (via ADX) predicts the distribution of future returns. This is a legitimate information channel.

2. **Volatility proxy channel:** ADX is correlated with volatility measures (correlation with BB_width = 0.68). Our data shows realized_vol is the #2 predictor (IC=0.053). ADX may be partially a volatility proxy, and volatility predicts returns through risk premia channels.

3. **Trend persistence channel:** If ADX is high, the current trend has been strong and persistent. Our positive IC values at 1-5 day horizons (+0.050 to +0.079 increasing with horizon) are consistent with trend continuation -- exactly what momentum predicts. The increasing IC with horizon suggests genuine signal, not noise (noise would decay).

4. **Self-fulfilling channel:** Many traders use ADX > 25 as a trend confirmation signal. Their trading activity conditional on ADX readings creates the very momentum that ADX then predicts.

**Why ADX might be spurious:**
- ADX is a lagging indicator (smoothed over 14 periods). By the time ADX signals "strong trend," much of the move has occurred.
- The IC of 0.067 is modest. After multiple testing correction across 18 indicators x 49 tokens x 4 horizons, it may not survive BHY correction.
- ADX conflates trend strength and volatility, making the causal channel ambiguous.

**Tests to resolve:**
1. Compute IC of ADX conditional on realized_vol (partial rank correlation)
2. Compute transfer entropy TE(ADX -> returns) vs TE(returns -> ADX)
3. Decompose ADX signal into regime classification (above/below threshold) and continuous value to see which component drives predictive power
4. Run BHY correction on all ICs and check if ADX survives

### 4.6 Our Finding: RSI is Nearly Useless (IC=0.004) -- Why

**RSI (Relative Strength Index)** measures the ratio of recent up moves to total moves over 14 periods. It's the most popular momentum oscillator.

**Why RSI fails as a standalone predictor:**

1. **Mean-reversion vs momentum conflict:** RSI is designed to identify overbought (>70) and oversold (<30) conditions, implying mean-reversion. But crypto is predominantly a momentum market at short horizons. An RSI signal to "sell" when RSI is high would be FIGHTING the trend.

2. **Noise dominance in mid-range:** RSI spends most of its time between 30-70, where it carries essentially zero information about future returns. The predictive content (if any) is concentrated in the extremes, which occur rarely. Averaging IC across all observations dilutes any extreme-specific signal to near zero.

3. **Redundancy with price returns:** RSI is 95.2% correlated with Bollinger Band %B in our data, and both are transformations of recent returns. RSI doesn't add information beyond what ret_1 already provides (ret_1 IC=0.009, also near zero).

4. **Crowding effect:** RSI is the most widely used indicator in retail trading. Any predictive content has been arbitraged away by the sheer number of traders acting on it.

**Potential rehabilitation:**
- RSI might work as a CONDITIONAL signal: RSI extreme + high ADX (trending market) might predict reversal
- RSI divergences (price makes new high, RSI doesn't) might have more predictive power than RSI levels
- RSI at extreme lookback periods (very short: 2-3 days, or very long: 50+ days) might capture different signals

---

## 5. Recombination Testing Methodology

### 5.1 The Combinatorial Explosion Problem

With 10 non-redundant indicators, 4 possible forward horizons, and even just 2 combination modes (AND/OR), the search space is enormous:
- Pairs: C(10,2) = 45
- Triples: C(10,3) = 120
- With parameter variants: thousands
- With horizon and direction: tens of thousands

Each test increases the multiple testing burden. This is the fundamental tension: we NEED to explore combinations to find multi-factor strategies, but exploration inflates false discovery risk.

### 5.2 Principled Search Strategies

#### Strategy 1: Theory-First (Recommended)

Pre-register combinations based on causal reasoning BEFORE testing:
- ADX + vol_ratio: "Trend strength confirmed by above-average volume should predict stronger continuation"
- ADX + taker: "Trend strength confirmed by aggressive order flow should predict continuation"
- RSI extreme + ADX low: "Oversold in a ranging market should predict mean-reversion"

This limits the effective number of tests to a manageable set (10-20 combinations), preserving statistical power.

#### Strategy 2: LASSO/Elastic Net Selection

Let LASSO select the combination for you:
- Fit penalized regression of forward returns on all indicator interactions
- The surviving terms are the "selected" combinations
- Apply stability selection (Meinshausen & Buhlmann, 2010) to ensure robust selection

This is data-driven but controls overfitting through the penalty.

#### Strategy 3: Genetic Programming

**Koza (1992), *Genetic Programming*:** Evolve mathematical expressions that combine indicators.

**Recent advances:**
- Vectorial GP (2025, arXiv): Extends standard GP to work with time series vectors, giving the evolved programs awareness of past data
- Warm Start GP for Quant Investment (2024, arXiv): Uses previously discovered alpha factors to initialize the population, dramatically reducing search time
- Alpha Mining with GP: Evolves formulaic alpha factors that combine indicators using arithmetic operations

**Overfitting controls for GP:**
- Chen & Kuo (2003): "Overfitting or poor learning: A critique of current financial applications of GP"
- Kim et al. (2008): Constrained GP with explicit overfitting penalties
- Practical: randomize training data, segment into sub-periods, evaluate on held-out periods
- Our addition: evaluate GP-discovered formulas through CPCV pipeline

**GP is powerful but dangerous.** The flexibility to discover arbitrary functional forms means the search space is effectively infinite, making false discovery almost certain without rigorous validation.

#### Strategy 4: Symbolic Regression

Modern symbolic regression (e.g., PySR, gplearn) searches for closed-form mathematical expressions:
- More interpretable than black-box ML
- Can discover nonlinear interactions (e.g., ADX^2 / vol_ratio) that linear methods miss
- Subject to same overfitting risks as GP

### 5.3 Signal Combination Methods

Once predictive indicators are identified, how to combine them:

#### Averaging (Simple)
- Equal-weight average of z-scored signals
- Most robust to estimation error (DeMiguel et al., 2009)
- Underperforms if signals have very different predictive power

#### Optimized Weighting
- IC-weighted averaging: weight proportional to each signal's IC
- Regression-weighted: fit a regression of returns on signals, use coefficients as weights
- Risk of overfitting the weights themselves

#### Stacking / Boosting
- Train a meta-model (Random Forest, XGBoost) on indicator values to predict returns
- Powerful but black-box and prone to overfitting
- Requires very careful cross-validation (CPCV, not standard k-fold)

#### Multi-Signal Framework (Recommended)
1. Each indicator generates a standardized signal (z-score or percentile)
2. Signals are combined via IC-weighted average (simple, robust)
3. Composite signal is thresholded for trade entry
4. SEPARATE model (meta-labeling) predicts probability of trade success
5. Position size proportional to meta-label confidence

### 5.4 Validation Pipeline for Combinations

For any discovered combination:

1. **In-sample IC** of combined signal (must exceed individual ICs to justify combination)
2. **BHY-corrected p-value** accounting for number of combinations tested
3. **CPCV with PBO < 0.40** (overfitting check)
4. **Walk-forward performance** (realistic sequential simulation)
5. **Cross-token consistency** (works on majority of tokens, not cherry-picked subset)
6. **Regime robustness** (works in both trending and ranging markets, or explicitly designed for one regime)
7. **DSR > 0** after accounting for all combinations tried
8. **Out-of-time holdout** (final 6 months, untouched during development)

---

## 6. Key Recent Papers (2020-2026)

### 6.1 Crypto Market Predictability

| # | Authors | Year | Title | Venue | Key Finding |
|---|---------|------|-------|-------|-------------|
| 1 | Liu & Tsyvinski | 2021 | "Risks and Returns of Cryptocurrency" | *Review of Financial Studies* | Crypto returns predicted by momentum and investor attention; no link to standard equity/macro factors |
| 2 | Cong, Karolyi, Tang & Zhao | 2022 | "Value Premium, Network Adoption, and Factor Pricing of Crypto Assets" | Working Paper | Network adoption drives crypto value premium; adapts Fama-French to crypto |
| 3 | Han, Kang & Ryu | 2023 | "Time-Series and Cross-Sectional Momentum in the Cryptocurrency Market" | SSRN | Comprehensive momentum analysis under realistic assumptions (transaction costs, short-selling constraints) |
| 4 | Bianchi & Babiak | 2022 | "On the Performance of Cryptocurrency Funds" | *Journal of Banking & Finance* | Factor models explain crypto fund returns; momentum and size factors significant |
| 5 | European Journal of Finance | 2022 | "The Dynamics of Returns Predictability in Cryptocurrency Markets" | EJF | Very limited overall BTC return predictability except for short-horizon momentum |
| 6 | Huang, Sangiorgi & Urquhart | 2024 | "Cryptocurrency Volume-Weighted Time Series Momentum" | SSRN | Volume-weighted momentum improves on standard momentum in crypto |
| 7 | *Quantitative Finance* | 2023 | "Cryptocurrency Factor Momentum" | QF 23(12) | Factor premia (momentum among factors, not assets) are predictable in crypto |
| 8 | MDPI | 2024 | "Optimizing Cryptocurrency Returns: Factor-Based Investing" | Mathematics | MVRV, size, momentum as crypto factors via Fama-MacBeth regressions |

### 6.2 Systematic Trading Strategy Design

| # | Authors | Year | Title | Venue | Key Finding |
|---|---------|------|-------|-------|-------------|
| 9 | Lopez de Prado | 2020 | "Machine Learning for Asset Managers" | Cambridge UP | Codistance, feature importance, portfolio construction via ML |
| 10 | Jansen | 2020 | "Machine Learning for Algorithmic Trading" (2nd ed.) | Packt | Comprehensive ML-for-trading framework with factor research pipeline |
| 11 | Mann | 2025 | "Quantitative Alpha in Crypto Markets" | SSRN | Systematic review: cross-exchange arb, factor models, ML applications spanning 2018-2025 |
| 12 | arXiv | 2025 | "Evolving Financial Trading Strategies with Vectorial GP" | arXiv | GP with vector operations for context-aware strategy evolution |
| 13 | arXiv | 2024 | "Alpha Mining and Enhancing via Warm Start GP" | arXiv | Warm-starting GP from known factors accelerates discovery |
| 14 | Springer | 2025 | "Forecasting and Trading Cryptocurrencies with ML Under Changing Market Conditions" | Springer | Moving-window ML with regime awareness; best ensemble: 1-10% annualized even in downturns |

### 6.3 Causal Inference in Financial Markets

| # | Authors | Year | Title | Venue | Key Finding |
|---|---------|------|-------|-------|-------------|
| 15 | Krakovska et al. | 2023 | "Linear and Nonlinear Causality in Financial Markets" | arXiv | CCM has ~90% false positive rate in financial data; nonlinear causality is significant but hard to detect |
| 16 | Khan et al. | 2025 | "Causal Estimation of FTX Collapse on Cryptocurrency" | *Financial Innovation* | Bayesian structural time series for causal impact estimation of market events |
| 17 | Springer | 2024 | "Dynamic Evolution of Causal Relationships Among Cryptocurrencies via Bayesian Networks" | KAIS | BTC and ETH significantly influence other crypto prices; relationships evolve over time |
| 18 | AIMS Press | 2025 | "Regime-Dependent Causality in Cryptocurrency Markets" | DSFE | Causal relationships are more stable in calm regimes than volatile regimes |
| 19 | ScienceDirect | 2024 | "The Relationship Between Cryptocurrencies and Conventional Financial Markets" | IREF | Symmetric and asymmetric causality between traditional and crypto markets is cyclical and short-term |

### 6.4 Strategy Validation and Avoiding Overfitting

| # | Authors | Year | Title | Venue | Key Finding |
|---|---------|------|-------|-------|-------------|
| 20 | Harvey, Liu & Zhu | 2016 | "...and the Cross-Section of Expected Returns" | *Review of Financial Studies* | t > 3.0 threshold for new factors; 316 factors catalogued, most likely false |
| 21 | Bailey, Borwein, Lopez de Prado & Zhu | 2017 | "The Probability of Backtest Overfitting" | *J. Computational Finance* | CSCV framework for estimating PBO |
| 22 | Bailey & Lopez de Prado | 2014 | "The Deflated Sharpe Ratio" | *J. Portfolio Management* | DSR corrects for selection bias, multiple testing, non-normality |
| 23 | Bailey et al. | 2014 | "Pseudo-Mathematics and Financial Charlatanism" | *Notices of the AMS* | False Strategy Theorem: expected max SR from N random trials |
| 24 | Arian et al. | 2024 | "Backtest Overfitting in the ML Era" | *Knowledge-Based Systems* | CPCV outperforms traditional methods; lower PBO and higher DSR |
| 25 | Hansen | 2005 | "A Test for Superior Predictive Ability" | *JBES* | SPA test improves on White's Reality Check; more powerful with poor alternatives |
| 26 | Hsu, Hsu & Kuan | 2010 | "Testing Predictive Ability Without Data Snooping Bias" | *J. Empirical Finance* | Stepwise SPA identifies ALL predictive models with FWER control |
| 27 | Romano & Wolf | 2005 | "Stepwise Multiple Testing as Formalized Data Snooping" | *Econometrica* | Stepdown procedure controls FWER while maximizing discoveries |

---

## 7. Synthesis and Recommendations

### What We Know (High Confidence)

1. **Overfitting is the dominant failure mode** in strategy discovery. The False Strategy Theorem proves that ANY Sharpe ratio can be achieved from noise given enough trials. Our entire methodology must be built around this fact.

2. **Crypto returns have limited predictability.** Short-horizon momentum is the most robust finding. Everything else is weak, regime-dependent, or likely spurious.

3. **ADX's predictive power is plausibly genuine** but needs rigorous validation. The increasing IC with forecast horizon (0.050 -> 0.079 from 1d to 5d) is consistent with trend persistence rather than noise. However, it must survive multiple testing correction and conditional independence testing against volatility measures.

4. **RSI is useless standalone** because it measures mean-reversion tendency in a market dominated by short-term momentum. It may have value as a conditional signal (e.g., RSI extreme in low-ADX environments).

5. **Causal relationships are regime-dependent.** Any strategy that assumes stable relationships will fail when regimes shift. Regime detection (which ADX partially provides) is a prerequisite, not an enhancement.

6. **Funding rates are causally meaningful** and should be added to our feature set. They represent actual economic mechanisms (leveraged position vulnerability) rather than technical pattern extraction.

### What We Should Do Next

1. **Immediate:** Apply BHY correction to all indicator ICs. Determine which survive.
2. **Immediate:** Compute transfer entropy TE(ADX -> returns) to test causal direction.
3. **Immediate:** Test ADX's IC conditional on realized_vol (partial correlation).
4. **Short-term:** Add funding rate data to feature set and compute ICs.
5. **Short-term:** Pre-register 10-15 indicator combinations based on causal reasoning (Section 5.2, Strategy 1).
6. **Medium-term:** Run combinations through full validation pipeline (Section 5.4).
7. **Medium-term:** Compute DSR for our best strategies accounting for all trials.
8. **Ongoing:** Maintain a trial registry documenting every strategy variant tested, enabling honest DSR computation.

### What We Should NOT Do

1. Do NOT search over thousands of indicator combinations without pre-registration
2. Do NOT trust any strategy that hasn't passed CPCV with PBO < 0.40
3. Do NOT assume causal relationships are stable across regimes
4. Do NOT use CCM as a primary causal test for our daily data
5. Do NOT interpret RSI failure as evidence that mean-reversion doesn't exist in crypto (it may, but at different timescales or under specific conditions)
6. Do NOT abandon statistical rigor because "crypto is different" -- the math of multiple testing doesn't care about the asset class

---

## References (Complete)

### Causal Inference
- Granger, C.W.J. (1969). "Investigating Causal Relations by Econometric Models and Cross-spectral Methods." *Econometrica*, 37(3), 424-438.
- Pearl, J. (2009). *Causality: Models, Reasoning, and Inference* (2nd ed.). Cambridge University Press.
- Schreiber, T. (2000). "Measuring Information Transfer." *Physical Review Letters*, 85(2), 461-464.
- Sugihara, G. et al. (2012). "Detecting Causality in Complex Ecosystems." *Science*, 338(6106), 496-500.
- Dimpfl, T. & Peter, F.J. (2013). "Using Transfer Entropy to Measure Information Flows Between Financial Markets." *Studies in Nonlinear Dynamics & Econometrics*, 17(1).
- Amornbunchornvej, C. et al. (2021). "Variable-lag Granger Causality and Transfer Entropy for Time Series Analysis." *ACM TKDD*, 15(4).

### Multiple Testing and Overfitting
- White, H. (2000). "A Reality Check for Data Snooping." *Econometrica*, 68(5), 1097-1126.
- Hansen, P.R. (2005). "A Test for Superior Predictive Ability." *JBES*, 23(4), 365-380.
- Romano, J.P. & Wolf, M. (2005). "Stepwise Multiple Testing as Formalized Data Snooping." *Econometrica*, 73, 1237-1282.
- Hsu, P.-H., Hsu, Y.-C. & Kuan, C.-M. (2010). "Testing the Predictive Ability of Technical Analysis Using a New Stepwise Test Without Data Snooping Bias." *Journal of Empirical Finance*, 17(3), 471-484.
- Harvey, C.R., Liu, Y. & Zhu, H. (2016). "...and the Cross-Section of Expected Returns." *Review of Financial Studies*, 29(1), 5-68.
- Bailey, D.H. & Lopez de Prado, M. (2014). "The Deflated Sharpe Ratio." *Journal of Portfolio Management*, 40(5), 94-107.
- Bailey, D.H., Borwein, J.M., Lopez de Prado, M. & Zhu, Q.J. (2014). "Pseudo-Mathematics and Financial Charlatanism." *Notices of the AMS*, 61(5), 458-471.
- Bailey, D.H., Borwein, J.M., Lopez de Prado, M. & Zhu, Q.J. (2017). "The Probability of Backtest Overfitting." *Journal of Computational Finance*, 20(4).
- Lopez de Prado, M. (2018). *Advances in Financial Machine Learning*. Wiley.
- Arian, H. et al. (2024). "Backtest Overfitting in the Machine Learning Era." *Knowledge-Based Systems*.

### Crypto Markets
- Liu, Y. & Tsyvinski, A. (2021). "Risks and Returns of Cryptocurrency." *Review of Financial Studies*, 34(6), 2689-2727.
- Han, C., Kang, B. & Ryu, J. (2023). "Time-Series and Cross-Sectional Momentum in the Cryptocurrency Market." SSRN 4675565.
- Huang, Z.-C., Sangiorgi, I. & Urquhart, A. (2024). "Cryptocurrency Volume-Weighted Time Series Momentum." SSRN 4825389.
- Mann, W. (2025). "Quantitative Alpha in Crypto Markets." SSRN 5225612.
- Khan et al. (2025). "Causal Estimation of FTX Collapse on Cryptocurrency." *Financial Innovation*.
- Kim, S. & Park, H. (2025). "Designing Funding Rates for Perpetual Futures in Cryptocurrency Markets." arXiv:2506.08573.
- He, S., Manela, A., Ross, O. & von Wachter, V. (2024). "Fundamentals of Perpetual Futures." arXiv:2212.06888.
- BIS (2024). "Crypto Carry." Working Paper No. 1087.

### Feature Selection and ML
- Kraskov, A., Stogbauer, H. & Grassberger, P. (2004). "Estimating Mutual Information." *Physical Review E*, 69(6).
- Lundberg, S. & Lee, S.-I. (2017). "A Unified Approach to Interpreting Model Predictions." *NeurIPS*.
- Meinshausen, N. & Buhlmann, P. (2010). "Stability Selection." *JRSS-B*, 72(4), 417-473.
- Koza, J.R. (1992). *Genetic Programming*. MIT Press.
- DeMiguel, V., Garlappi, L. & Uppal, R. (2009). "Optimal Versus Naive Diversification." *Review of Financial Studies*, 22(5), 1915-1953.
