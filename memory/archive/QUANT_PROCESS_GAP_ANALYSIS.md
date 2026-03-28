# Quantitative Process Gap Analysis: Crypto Backtesting System V3

> **Purpose:** Identify and prioritize the gaps between our current per-token validation engine and quant best practices for portfolio-level crypto trading.
> **Sources:** Academic papers (2024-2026), quant firm research (AQR, Lopez de Prado), QuantConnect documentation, crypto fund reports.

---

## Gap 1: Per-Token vs Portfolio Validation

### What the Gap Is
Our system validates each strategy on each token independently (walk-forward + CPCV), then implicitly assumes that running validated strategies across the validated token set will produce a viable portfolio. There is no portfolio-level backtest that simulates simultaneous positions, capital allocation, or aggregate risk.

### Why It Matters

**Lopez de Prado's assembly-line paradigm** explicitly separates signal research from portfolio construction as distinct production stages. Per-token validation is signal research; portfolio construction is a separate, downstream step that cannot be skipped. "Portfolio construction is arguably even more important [than price prediction]" ([Lopez de Prado, AFML](https://www.quantresearch.org/Lectures.htm)).

Key failure modes when skipping portfolio validation:

1. **Correlation blow-up:** Crypto assets that appear diversified in normal markets become highly correlated during stress. Research shows correlation and contagion between cryptocurrencies increases during financial crises ([arXiv:2507.08915](https://arxiv.org/html/2507.08915v1)). A portfolio of N independently-validated momentum strategies on N tokens may produce a single concentrated momentum bet during drawdowns.

2. **Capital over-allocation:** Without portfolio-level sizing, the system implicitly assumes unlimited capital. In practice, running the validated set simultaneously requires capital allocation rules -- equal weight, risk parity, or vol-targeting. Each produces materially different risk/return profiles.

3. **Execution interference:** Simultaneous entry/exit signals across correlated tokens create execution pressure. Slippage compounds non-linearly when many positions trade the same direction at the same time.

4. **Aggregation illusion:** Summing per-token equity curves overstates the portfolio Sharpe ratio if token returns are correlated. For uncorrelated strategies, Sharpe scales as sqrt(N); for correlated strategies, the benefit is much smaller. Without measuring correlation, the aggregated result is unreliable.

### What Quant Practice Recommends

- **Stage separation:** Signal validation (per-token) -> portfolio construction -> portfolio backtest -> execution simulation. Each stage has its own validation criteria ([Lopez de Prado, Meta-Strategies](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2547325)).
- **Covariance estimation:** Use dynamic covariance (EWMA with ~63-day half-life for vol, ~125-day for correlation) rather than static assumptions ([Stanford/Boyd crypto portfolio research](https://web.stanford.edu/~boyd/papers/pdf/crypto_portfolio.pdf)).
- **Hierarchical Risk Parity (HRP):** Lopez de Prado's HRP method builds diversified portfolios using the covariance matrix without requiring matrix inversion, making it suitable for large token universes with noisy correlation estimates ([Building Diversified Portfolios](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2708678)).
- **Strategy orthogonality target:** Keep monthly return correlation between any two strategy "sleeves" in the -0.2 to +0.2 band to ensure genuine diversification.

### How It Applies to Our System

We need a portfolio simulation layer that takes the per-token validated signals and runs them through realistic capital allocation, position sizing, and aggregate risk measurement. The minimum viable version: equal-weight allocation across the validated set, with aggregate drawdown and Sharpe measured at the portfolio level. The production version: dynamic allocation using HRP or risk-parity weighting.

---

## Gap 2: Dynamic Token / Universe Selection

### What the Gap Is
Our universe is static -- "all tokens with available data on the exchange." Tokens do not enter or exit the tradeable universe based on evolving conditions (liquidity, market cap, structural breaks). We have no systematic handling of delistings, forks, or liquidity regime changes.

### Why It Matters

**Survivorship bias is severe in crypto.** A comprehensive CoinGecko study covering 2013-2025 found 18,622 active coins alongside 29,230 inactive/delisted coins. Including only survivors can quadruple apparent returns and drastically improve Sharpe ratios ([Ammann et al., SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4287573)). If our universe only includes tokens that currently exist, our validation results are biased upward.

**Liquidity evaporation:** Tokens that pass validation during high-liquidity periods may become untradeable during drawdowns. Thin order books mean backtested trades may never fill at scale. "Backtests may look good on paper, but the moment you deploy, they collapse under real-world execution" ([CoinAPI](https://www.coinapi.io/blog/backtest-crypto-strategies-with-real-market-data)).

**Token breadth deterioration:** Pantera Capital's 2026 outlook documented that total crypto market cap excluding BTC/ETH/stables peaked late 2024 and declined ~44% through end of 2025 ([Pantera Capital](https://panteracapital.com/blockchain-letter/navigating-crypto-in-2026/)). A static universe fails to adapt to this contraction.

### What Quant Practice Recommends

- **Dynamic universe rebalancing:** QuantConnect's crypto universe framework supports periodic re-evaluation (e.g., weekly) based on USD volume, market cap, and technical indicators ([QuantConnect Crypto Universe](https://www.quantconnect.com/docs/v2/writing-algorithms/universes/crypto)).
- **Minimum liquidity thresholds:** Filter by trailing dollar volume (e.g., top N by VolumeInUsd). Tokens falling below threshold exit the universe.
- **Point-in-time constituent lists:** Maintain listing/delisting dates for every token. Backtest only uses tokens that were tradeable at each historical point.
- **Fork/airdrop handling:** Adjust historical balances for hard forks (e.g., BCH from BTC). Maintain an asset master list with event metadata.
- **Graduated entry/exit:** New listings enter the universe after a burn-in period (e.g., 90 days of sufficient volume). Delisting candidates get reduced allocation before removal.

### How It Applies to Our System

Three concrete improvements:
1. **Survivorship-bias-free dataset:** Include delisted tokens in historical data. Validate strategies on the full historical universe, not just current survivors.
2. **Liquidity gate:** Add minimum trailing dollar-volume threshold for universe inclusion, re-evaluated at each walk-forward window boundary.
3. **Point-in-time universe:** At each backtest timestamp, the token set should reflect what was actually tradeable at that time.

---

## Gap 3: Multi-Token / Cross-Sectional Strategies

### What the Gap Is
Every strategy in our system examines a single token in isolation. There are no strategies that look at relationships between tokens -- no pairs trading, no cross-sectional momentum (long winners / short losers), no relative value, no sector rotation.

### Why It Matters

**Cross-sectional momentum is particularly well-suited to crypto.** Research confirms that momentum works better under a cross-sectional framework for cryptocurrencies than time-series momentum, and that higher volatility assets produce higher Sharpe ratios from momentum strategies ([Han, Kang, Ryu - SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4675565)).

**Pairs trading outperforms passive strategies.** A 2025 study in the Journal of Futures Markets found that cointegrated pairs trading with adaptive trailing stop-loss and volatility filtering "consistently outperforms conventional pairs trading and passive approaches, generating significant risk-adjusted excess returns while maintaining low market exposure" ([Palazzi, 2025](https://onlinelibrary.wiley.com/doi/full/10.1002/fut.70018)).

**Sector rotation is a documented source of returns.** In 2025, capital rotated from meme coins (peak late 2024) to RWA and Layer 1 (2025 leaders) to privacy and DeFi infrastructure (late 2025). RWA tokens returned +185.76% on average, while meme coins declined ~69% from peak ([CoinGecko Narratives 2025](https://www.coingecko.com/research/publications/most-profitable-crypto-narratives)). A category-momentum strategy that systematically rotates into the strongest sector would have captured this.

**Statistical arbitrage with ML:** Deep learning-based pairs trading using LSTM models for co-integrated cryptocurrency pairs is showing superior performance for real-time forecasting and mean-reversion trades ([Frontiers, 2026](https://www.frontiersin.org/journals/applied-mathematics-and-statistics/articles/10.3389/fams.2026.1749337/full)).

### What Quant Practice Recommends

- **Cross-sectional momentum:** Rank tokens by trailing returns (e.g., 1-month, 3-month). Go long top quintile, short bottom quintile (or long-only top quintile on spot). Rebalance weekly or monthly.
- **Pairs trading:** Identify cointegrated pairs (e.g., ARB/OP with 0.91 correlation per Crypto.com Alpha Navigator). Trade the spread mean-reversion.
- **Sector/narrative rotation:** Classify tokens by category (L1, DeFi, meme, AI, RWA). Allocate to categories with strongest trailing momentum. Rotate on 1-4 week horizons.
- **Risk-managed momentum:** Apply volatility scaling to momentum portfolios. Research shows crypto momentum is subject to severe crashes, and volatility management mitigates this ([Grobys, 2025](https://link.springer.com/article/10.1007/s11408-025-00474-9)).

### How It Applies to Our System

Our engine's per-token architecture makes cross-sectional strategies impossible without a new layer. We need:
1. A **cross-sectional ranking module** that compares token metrics at each rebalance point.
2. A **pairs/cointegration scanner** that identifies tradeable relationships between tokens.
3. A **sector classification system** that tags tokens by category and tracks category-level momentum.

These represent new strategy types, not modifications to existing per-token strategies.

---

## Gap 4: Token Selection as Alpha

### What the Gap Is
Our system treats token selection as a pass/fail outcome of validation ("does the strategy validate on this token?"). It does not treat the choice of *which* tokens to trade as an independent source of alpha. There is no pre-filtering, quality screening, or liquidity-weighted selection.

### Why It Matters

**Factor screening works in crypto.** Crypto.com's monthly Alpha Navigator applies traditional equity-style factor screens (momentum, value, growth, risk) to crypto tokens across categories (L1/L2, DeFi, Gaming, NFT), demonstrating that systematic screening adds value ([Crypto.com Alpha Navigator](https://crypto.com/us/research/alpha-navigator-dec-2025)).

**Universe construction is itself a strategy decision.** The William Mann systematic review (SSRN, 2025) identifies that "traditional factor models can be adapted for cryptocurrency markets, with size, momentum, and liquidity factors demonstrating statistical significance" ([Mann, SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5225612)). The choice of which tokens enter the universe is a factor-loading decision, whether intentional or not.

**Pre-filtering affects validation rates.** If we pre-filter for high-liquidity, large-cap tokens, our strategy validation rate may increase (fewer noisy, illiquid tokens that fail). But this also changes the character of the returns -- large-cap momentum behaves differently from small-cap momentum. A March 2025 paper found that "cryptocurrency momentum appears to be a phenomenon associated with large-cap cryptocurrencies" ([Grobys, 2025](https://link.springer.com/article/10.1007/s11408-025-00474-9)).

**Extreme dispersion creates opportunity.** Pantera noted 2025 was "a year characterized by extreme dispersion, stronger performance from the majors, and prolonged weakness outside of bitcoin." A quality/liquidity filter would have concentrated capital in the tokens that actually performed.

### What Quant Practice Recommends

- **Layered filtering:** Liquidity gate (minimum dollar volume) -> quality gate (market cap, age, exchange coverage) -> factor score (momentum, volatility, on-chain metrics) -> final tradeable universe.
- **Multiple measures for robustness:** AQR's research shows "a multiple-measure approach can reduce the measurement noise associated with any one measure" ([AQR Craftsmanship Alpha](https://www.aqr.com/-/media/AQR/Documents/Insights/Working-Papers/AQR--Craftsmanship-Alpha.pdf)). Apply multiple screens rather than a single filter.
- **Periodic reconstitution:** Re-evaluate the filtered universe at fixed intervals (weekly/monthly). Tokens that no longer meet criteria are removed; new qualifiers enter.
- **Separate alpha from selection:** Measure how much return comes from the *selection* of tokens vs the *timing* within those tokens. This decomposition reveals whether selection itself is adding value.

### How It Applies to Our System

We currently validate on "everything" and take what passes. This is equivalent to no selection alpha -- the universe is a passive, unfiltered input. Adding a pre-validation quality/liquidity filter and measuring its impact on validation rates and out-of-sample returns would quantify whether selection adds alpha. This is a relatively low-effort improvement with potentially high impact.

---

## Gap 5: Strategy Correlation and Combination

### What the Gap Is
We have multiple validated strategies (Tier A and Tier B, spot and perp) but no systematic method for combining them. We do not measure inter-strategy correlation, do not optimize the allocation across strategies, and do not know whether adding marginal strategies helps or hurts.

### Why It Matters

**Correlation determines diversification benefit.** Quantpedia's analysis found that "some of the momentum strategies are not even positively correlated, while some are slightly positively correlated, making diversification benefits possible" ([Quantpedia](https://quantpedia.com/how-to-combine-different-momentum-strategies/)). Without measuring correlation, we cannot know if our Tier A strategies are providing six independent bets or six copies of the same bet.

**Combination method matters.** Research identifies multiple approaches with different properties:
- Equal weight + volatility targeting
- Equal Risk Contribution (risk parity)
- Maximum diversification
- Signal-level integration (combine scores before portfolio construction)
- Portfolio-level mixing (separate portfolios, then combine)

Goldman Sachs research found "no strong evidence to favor the Integrated or Mixed approach" in long/short contexts, but AQR's "Craftsmanship Alpha" paper argues that signal integration (combining signals before constructing portfolios) tends to outperform naive mixing ([GSAM](https://www.gsam.com/content/dam/gsam/pdfs/institutions/en/articles/2018/Combining_Investment_Signals_in_LongShort_Strategies.pdf), [AQR](https://www.aqr.com/-/media/AQR/Documents/Insights/Working-Papers/AQR--Craftsmanship-Alpha.pdf)).

**Signal speed blending:** Combining slow and fast momentum signals is important because "slow signals tend to be unreactive to changes in trend, and fast signals are often false alarms." The weight should be adjusted based on signal interactions ([Quantpedia](https://quantpedia.com/combining-smart-factors-momentum-and-market-portfolio)).

**Deep learning enhancement:** Oxford-Man Institute research shows Deep Momentum Networks using LSTM architectures "outperformed conventional methods by more than twice in the absence of transaction costs" by jointly learning trend estimation and position sizing ([Oxford-Man Institute](https://oxford-man.ox.ac.uk/projects/deep-learning-for-quant-finance-strategies/)).

### What Quant Practice Recommends

- **Correlation matrix of strategies:** Compute rolling correlation between all strategy equity curves. Target pairwise correlations in the -0.2 to +0.2 range.
- **Marginal contribution analysis:** Before adding a new strategy to the portfolio, measure its marginal Sharpe improvement. A new strategy helps only if its correlation with the existing portfolio is sufficiently low relative to its standalone Sharpe.
- **Risk parity allocation:** Allocate capital across strategies so each contributes equal risk. This prevents high-volatility strategies from dominating the portfolio.
- **Signal agreement gating:** For tokens where multiple strategies produce signals, trade only when signals agree, or increase position size on agreement and reduce on disagreement.
- **Regime-conditional weighting:** Different strategy combinations may be optimal in different market regimes (trending vs. mean-reverting, high vs. low volatility).

### How It Applies to Our System

Immediate steps:
1. **Compute the correlation matrix** across all validated strategy equity curves.
2. **Measure marginal Sharpe contribution** of each strategy to the combined portfolio.
3. **Implement at least one combination method** (risk parity is a good starting point).
4. **Test signal agreement** -- for tokens where multiple strategies validate, does combining signals improve results?

---

## Prioritized Process Improvements

Ranked by expected impact on risk-adjusted returns and implementation feasibility:

| Priority | Improvement | Expected Impact | Effort | Status |
|----------|-------------|-----------------|--------|--------|
| **1** | Portfolio-level backtest (Gap 1) | **Critical** | Medium | **Done** — `v3/portfolio.py` (shared cash pool, MTM equity, concentration caps) |
| **2** | Strategy correlation matrix & combination (Gap 5) | **High** | Low | **Done** — `v3/correlation.py` (pairwise corr, marginal Sharpe, greedy selection). Finding: all 6 Tier A strategies are momentum variants with median r=+0.62, 3 pairs >0.7. s11 alone (Sharpe 1.74) beats any equal-weight combo (1.22). Use as a gate when building portfolio strategies. |
| **3** | Dynamic liquidity-gated universe (Gap 2) | **High** | Medium | **Done** — `v3/dynamic_universe.py` (point-in-time eligibility at each WF window, ADV-based filtering). Static gate in `get_filtered_universe()` also retained. |
| **4** | Token selection as alpha / pre-filtering (Gap 4) | **Medium-High** | Low | **Done** — `universe.py` quality/bar-count/ADV filters, `--universe` CLI flag |
| **5** | Cross-sectional momentum strategy (Gap 3) | **Medium-High** | High | **Done** — `v3/cross_sectional.py` (long top quintile by 14d return, Sharpe 1.54, corr +0.25 vs S11). Tier A diversifier. |
| **6** | Pairs trading / statistical arbitrage (Gap 3) | **Medium** | High | **Done** — `v3/pairs_trading.py` (cointegrated pairs on perps, z-score entry, Sharpe 0.42, corr -0.06). Tier B diversifier. |
| **7** | Sector/narrative rotation (Gap 3) | **Medium** | Medium | **Done** — `v3/sector_rotation.py` (long top 2 of 10 sectors by category momentum, Sharpe 1.31, corr +0.20). Tier A diversifier. |
| **8** | Survivorship-bias-free historical dataset (Gap 2) | **Medium** | Medium | Not started. Improves all backtest validity. Can be built incrementally using CoinMarketCap/CoinGecko APIs for delisted token data. |
| **9** | Signal agreement gating across strategies (Gap 5) | **Medium** | Low | **Done** — `v3/signal_agreement.py` (AND/N-of-M gating. S11+S09 AND: trades -66%, Calmar +0.46, DD +6.7pp). |
| **10** | Regime-conditional strategy weighting (Gap 5) | **Low-Medium** | High | **Done** — `v3/regime_analysis.py` (scale allocation by BTC regime. S11+S09: Sharpe +0.29, DD +4.5pp). |

### Critical Path

**9 of 10 improvements complete.** P1-P7, P9, P10 all done. Only P8 (survivorship-bias-free dataset) remains.

**Key finding from P2:** All 6 Tier A per-token strategies are momentum/trend variants (median pairwise r=+0.62, effective N=2.02). This drove P5-P7 (new strategy families for diversification).

**Key results from P5-P7 (portfolio strategies):**
- Cross-sectional momentum (`v3/cross_sectional.py`): Sharpe 1.54, corr +0.25 vs S11 — genuine diversifier
- Sector rotation (`v3/sector_rotation.py`): Sharpe 1.31, corr +0.20 vs S11 — genuine diversifier
- Pairs trading (`v3/pairs_trading.py`): Sharpe 0.42, corr -0.06 vs S11 — weak standalone, excellent diversifier

**Key results from P9-P10 (overlays):**
- Signal agreement (`v3/signal_agreement.py`): S11+S09 AND gate → trades -66%, Calmar +0.46, DD +6.7pp
- Regime weighting (`v3/regime_analysis.py`): S11+S09 regime scale → Sharpe +0.29, DD +4.5pp

**Remaining:**
1. **P8** — Survivorship-bias-free dataset (not started, requires exchange API work)

---

## Key Sources

- [Lopez de Prado - Quantitative Meta-Strategies (SSRN)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2547325)
- [Lopez de Prado - Building Diversified Portfolios / HRP (SSRN)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2708678)
- [Lopez de Prado - 10 Reasons ML Funds Fail (GARP)](https://www.garp.org/hubfs/Whitepapers/a1Z1W0000054x6lUAA.pdf)
- [Han, Kang, Ryu - Time-Series and Cross-Sectional Momentum in Crypto (SSRN)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4675565)
- [Palazzi 2025 - Pairs Trading in Crypto (Journal of Futures Markets)](https://onlinelibrary.wiley.com/doi/full/10.1002/fut.70018)
- [Mann 2025 - Quantitative Alpha in Crypto Markets (SSRN)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5225612)
- [Grobys 2025 - Cryptocurrency Momentum Crashes (FMPM)](https://link.springer.com/article/10.1007/s11408-025-00474-9)
- [Ammann et al. - Survivorship and Delisting Bias in Crypto (SSRN)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4287573)
- [Stanford/Boyd - Simple and Effective Crypto Portfolio Construction](https://web.stanford.edu/~boyd/papers/pdf/crypto_portfolio.pdf)
- [Quantpedia - Combining Momentum Strategies](https://quantpedia.com/how-to-combine-different-momentum-strategies/)
- [AQR - Craftsmanship Alpha](https://www.aqr.com/-/media/AQR/Documents/Insights/Working-Papers/AQR--Craftsmanship-Alpha.pdf)
- [GSAM - Combining Investment Signals in Long/Short](https://www.gsam.com/content/dam/gsam/pdfs/institutions/en/articles/2018/Combining_Investment_Signals_in_LongShort_Strategies.pdf)
- [QuantConnect - Crypto Universe Selection](https://www.quantconnect.com/docs/v2/writing-algorithms/universes/crypto)
- [Crypto.com - Alpha Navigator](https://crypto.com/us/research/alpha-navigator-dec-2025)
- [CoinGecko - Most Profitable Crypto Narratives 2025](https://www.coingecko.com/research/publications/most-profitable-crypto-narratives)
- [Pantera Capital - Navigating Crypto in 2026](https://panteracapital.com/blockchain-letter/navigating-crypto-in-2026/)
- [Oxford-Man Institute - Deep Learning for Quant Strategies](https://oxford-man.ox.ac.uk/projects/deep-learning-for-quant-finance-strategies/)
- [Frontiers 2026 - Deep Learning Pairs Trading in Crypto](https://www.frontiersin.org/journals/applied-mathematics-and-statistics/articles/10.3389/fams.2026.1749337/full)
- [Crypto Portfolio Risk Simulation (arXiv)](https://arxiv.org/html/2507.08915v1)
