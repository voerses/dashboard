# Research Papers Catalog

Curated catalog of academic research papers relevant to cryptocurrency trading strategies, market microstructure, and quantitative finance. Organized by topic, prioritizing papers with backtested results.

Last updated: 2026-02-28

---

## Table of Contents

1. [Classic Foundational Papers](#1-classic-foundational-papers)
2. [Time-Series Momentum in Crypto](#2-time-series-momentum-in-crypto)
3. [Cross-Sectional Momentum and Factor Models](#3-cross-sectional-momentum-and-factor-models)
4. [Market Microstructure and Order Flow](#4-market-microstructure-and-order-flow)
5. [Carry Trade, Funding Rate, and Basis Arbitrage](#5-carry-trade-funding-rate-and-basis-arbitrage)
6. [Regime Switching and Adaptive Strategies](#6-regime-switching-and-adaptive-strategies)
7. [Volatility Forecasting and Timing](#7-volatility-forecasting-and-timing)
8. [Machine Learning for Crypto Trading](#8-machine-learning-for-crypto-trading)
9. [Mean Reversion and Pairs Trading](#9-mean-reversion-and-pairs-trading)
10. [Portfolio Optimization and Risk Management](#10-portfolio-optimization-and-risk-management)
11. [On-Chain Metrics and Alternative Data](#11-on-chain-metrics-and-alternative-data)
12. [Information Flow and Lead-Lag Relationships](#12-information-flow-and-lead-lag-relationships)
13. [Financial Machine Learning Methods](#13-financial-machine-learning-methods)

---

## 1. Classic Foundational Papers

### 1.1 Moskowitz, Ooi, Pedersen (2012) — "Time Series Momentum"

- **Citation:** Moskowitz, T., Ooi, Y.H., Pedersen, L.H. (2012). "Time Series Momentum." *Journal of Financial Economics*, 104(2), 228-250.
- **URL:** https://www.sciencedirect.com/science/article/pii/S0304405X11002613
- **Key Finding:** Documents significant time-series momentum across 58 liquid futures instruments (equity indices, currencies, commodities, bonds). Past 12-month excess return positively predicts future returns. Persistence in returns for 1-12 months, with partial reversal at longer horizons. A diversified TSMOM portfolio delivers substantial abnormal returns with minimal exposure to standard risk factors. TSMOM is distinct from cross-sectional momentum and performs best in extreme markets.
- **Actionable Insight:** Use 12-month lookback for signal generation. Diversify TSMOM across multiple crypto assets. Expect mean reversion beyond 12 months. TSMOM alpha is not captured by cross-sectional momentum, so both can coexist in a multi-strategy system.

### 1.2 Jegadeesh & Titman (1993) — Cross-Sectional Momentum

- **Citation:** Jegadeesh, N., Titman, S. (1993). "Returns to Buying Winners and Selling Losers: Implications for Stock Market Efficiency." *Journal of Finance*, 48(1), 65-91.
- **Key Finding:** Strategies that buy past winners and sell past losers generate significant positive returns over 3-12 month holding periods. The profits are not due to systematic risk or delayed stock price reactions to common factors.
- **Actionable Insight:** Cross-sectional momentum (ranking crypto assets by relative performance and going long top performers, short bottom) has mixed evidence in crypto. Works primarily among large-cap coins. Consider combining with volatility management to avoid momentum crashes.

### 1.3 Antonacci (2014) — Dual Momentum

- **Citation:** Antonacci, G. (2014). *Dual Momentum Investing: An Innovative Strategy for Higher Returns with Lower Risk.* McGraw-Hill.
- **URL:** https://www.amazon.com/Dual-Momentum-Investing-Innovative-Strategy/dp/0071849440
- **Key Finding:** Combines relative momentum (pick best-performing asset) with absolute momentum (trend filter — only hold when trend is positive, otherwise go to bonds/cash). The Global Equities Momentum (GEM) strategy delivered nearly 2x stock market returns over 40 years with significantly lower drawdowns by switching between US stocks, international stocks, and bonds.
- **Actionable Insight:** Apply dual momentum to crypto: use relative momentum to select which crypto to hold, then apply an absolute momentum filter (e.g., is the asset above its 12-month moving average?) to decide whether to be invested or in stablecoins/cash. This is directly implementable in our system.

### 1.4 Easley, Lopez de Prado, O'Hara — VPIN

- **Citation:** Easley, D., Lopez de Prado, M., O'Hara, M. (2012). "Flow Toxicity and Liquidity in a High-Frequency World." *Review of Financial Studies*, 25(5), 1457-1493.
- **URL:** https://www.quantresearch.org/VPIN.pdf
- **Key Finding:** VPIN (Volume-Synchronized Probability of Informed Trading) measures order flow toxicity in volume-time rather than clock-time. It predicted the 2010 Flash Crash. Bulk Volume Classification (BVC) enables real-time estimation without trade-level data. VPIN rises before major adverse price moves.
- **Actionable Insight:** Implement VPIN as a risk filter for our trading system. High VPIN = elevated informed trading = potential for adverse price moves. Use as a position-sizing or exit signal. In crypto, the aggressor flag is available on most exchanges, making VPIN computation even more accurate than in equities.

### 1.5 Almgren & Chriss (2000) — Optimal Execution

- **Citation:** Almgren, R., Chriss, N. (2000). "Optimal Execution of Portfolio Transactions." *Journal of Risk*, 3(2), 5-39.
- **URL:** https://www.smallake.kr/wp-content/uploads/2016/03/optliq.pdf
- **Key Finding:** Provides the foundational framework for splitting large orders over time to minimize the sum of temporary market impact and timing risk. The solution is front-loaded (most trading early). TWAP minimizes expected cost for risk-neutral investors; an exponential schedule for risk-averse investors. Introduces the efficient frontier in execution strategy space.
- **Actionable Insight:** For any strategy trading more than trivial size, implement execution optimization. In crypto, the Almgren-Chriss framework has been validated on Binance (bid-ask spread is a dominant execution cost). Even for our backtesting system, realistic market impact modeling should use temporary + permanent impact components. See Claremont Colleges thesis on crypto-specific application: https://scholarship.claremont.edu/cgi/viewcontent.cgi?article=3566&context=cmc_theses

### 1.6 Lopez de Prado (2018) — Advances in Financial Machine Learning

- **Citation:** Lopez de Prado, M. (2018). *Advances in Financial Machine Learning.* Wiley.
- **Key Finding:** Introduces the triple barrier method, meta-labeling, CPCV, fractional differentiation, and structural breaks. These methods collectively address the unique challenges of applying ML to finance: non-IID data, lookahead bias, and multiple testing. See Section 13 for detailed methods.
- **Actionable Insight:** Our backtesting validation framework should incorporate CPCV rather than simple walk-forward. Triple barrier labeling should replace fixed-horizon returns for ML signal generation. Meta-labeling can be used to size bets on primary signals.

---

## 2. Time-Series Momentum in Crypto

### 2.1 Huang, Sangiorgi, Urquhart (2024) — Volume-Weighted Crypto TSMOM

- **Citation:** Huang, Z.C., Sangiorgi, I., Urquhart, A. (2024). "Cryptocurrency Volume-Weighted Time Series Momentum." SSRN Working Paper.
- **URL:** https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4825389
- **Key Finding:** Strong and significant evidence of crypto TSMOM when using volume-weighted market returns. Volume-weighted approaches enhance traditional TSMOM performance, as volume carries information about the strength of price trends.
- **Actionable Insight:** Weight momentum signals by volume. A volume-confirmed trend signal is stronger than price-only momentum. Implement volume-weighted returns in lookback calculations.

### 2.2 Han, Kang, Ryu (2024) — Comprehensive Crypto Momentum Under Realistic Assumptions

- **Citation:** Han, C., Kang, B., Ryu, J. (2024). "Time-Series and Cross-Sectional Momentum in the Cryptocurrency Market: A Comprehensive Analysis under Realistic Assumptions." SSRN Working Paper.
- **URL:** https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4675565
- **Key Finding:** Prior studies ignore real-world considerations (transaction costs, slippage, liquidity constraints). Under realistic market conditions, momentum profitability is substantially reduced. Highlights the importance of proper backtesting assumptions.
- **Actionable Insight:** Always include realistic transaction costs (0.1% round-trip minimum for crypto), slippage, and liquidity constraints. Strategies that look great in frictionless backtests often fail in practice.

### 2.3 Cryptocurrency Momentum Has (Not) Its Moments (2025)

- **Citation:** Published in *Financial Markets and Portfolio Management*, Springer, March 2025.
- **URL:** https://link.springer.com/article/10.1007/s11408-025-00474-9
- **Key Finding:** Crypto momentum is subject to severe crashes. A single cryptocurrency can cause insignificant momentum portfolio returns. Volatility management (scaling positions inversely to recent volatility) mitigates momentum crashes. Crypto momentum is primarily a large-cap phenomenon.
- **Actionable Insight:** CRITICAL: Implement volatility scaling for all momentum strategies. Focus momentum signals on large-cap assets (BTC, ETH, SOL, etc.). Monitor for momentum crash conditions. Volatility-managed momentum significantly outperforms naive momentum.

### 2.4 Rosen & Wang (2025) — Bitcoin's Evolution from TSMOM to Size Factor

- **Citation:** Rosen, S., Wang, H. (2025). "From Time-Series Momentum to Size-Factor Comovement: Bitcoin's Continuing Evolution as a Financial Asset." SSRN Working Paper.
- **URL:** https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5732803
- **Key Finding:** Using weekly BTC data from 2011-2024, TSMOM and investor attention predicted BTC returns in early years, but these predictors have lost power since ~2019. BTC now correlates significantly with the Fama-French size factor (behaves like a small-cap stock). Market efficiency has increased.
- **Actionable Insight:** Pure TSMOM for BTC may be less effective than it was pre-2019. For BTC specifically, consider macro and equity factor exposure. TSMOM may still work better for altcoins with lower institutional participation and less efficiency.

### 2.5 Palazzi (2025) — Trading Games: Beating Passive Strategies

- **Citation:** Palazzi (2025). "Trading Games: Beating Passive Strategies in the Bullish Crypto Market." *Journal of Futures Markets*, Wiley.
- **URL:** https://onlinelibrary.wiley.com/doi/full/10.1002/fut.70018
- **Key Finding:** Cointegrated pairs trading with dynamic parameter optimization, adaptive trailing stop-loss, and volatility filtering consistently outperforms passive approaches, generating significant risk-adjusted excess returns even in bull markets.
- **Actionable Insight:** Dynamic risk management (adaptive stops, volatility filters) is key to outperforming buy-and-hold in crypto. Static strategies underperform.

---

## 3. Cross-Sectional Momentum and Factor Models

### 3.1 Liu, Tsyvinski, Wu — Three-Factor Crypto Model

- **Citation:** Liu, Y., Tsyvinski, A., Wu, X. (2022). "Common Risk Factors in Cryptocurrency." *Journal of Finance*, 77(2), 1133-1177.
- **Key Finding:** Three factors -- market, size, and momentum -- capture the cross-section of expected crypto returns. Ten cryptocurrency characteristics form successful long-short strategies accounted for by this three-factor model. Crypto factors are distinct from equity, currency, and commodity factors.
- **Actionable Insight:** Use market beta, size, and momentum as the foundational factors for crypto portfolio construction. This is the crypto equivalent of Fama-French.

### 3.2 Trend Factor for Cross-Section of Crypto Returns (JFQA, 2024)

- **Citation:** Published in *Journal of Financial and Quantitative Analysis*, Cambridge University Press, 2024.
- **URL:** https://www.cambridge.org/core/journals/journal-of-financial-and-quantitative-analysis/article/trend-factor-for-the-cross-section-of-cryptocurrency-returns/4C1509ACBA33D5DCAF0AC24379148178
- **Key Finding:** A "CTREND" factor for crypto generates a weekly alpha of 2.62% for long-short portfolios. CTREND correlates with momentum (beta 0.79) but provides independent alpha. Consistent with models where crypto investors infer information about adoption and valuation from price behavior.
- **Actionable Insight:** Trend signals (moving average crossovers, breakout indicators) contain cross-sectional information. Rank cryptos by trend strength for portfolio allocation. This is complementary to pure momentum.

### 3.3 Fieberg et al. (2023) — Cryptocurrency Factor Momentum

- **Citation:** Fieberg, C., et al. (2023). "Cryptocurrency Factor Momentum." *Quantitative Finance*, 23(12), 1853-1869.
- **URL:** https://www.tandfonline.com/doi/abs/10.1080/14697688.2023.2269999
- **Key Finding:** There is a momentum effect in cryptocurrency anomalies themselves (factor momentum). Analyzed 3,900+ coins from 2014-2022, replicating 34 anomalies. Factors that performed well recently tend to continue performing well.
- **Actionable Insight:** When selecting which signals/factors to use, recent factor performance is predictive. Overweight factors that have been working recently (meta-strategy for signal selection).

### 3.4 Crypto as Investable Asset Class — C-4 Factor Model (2025)

- **Citation:** "Cryptocurrency as an Investable Asset Class: Coming of Age." arXiv, October 2025.
- **URL:** https://arxiv.org/html/2510.14435v1
- **Key Finding:** The C-4 model explains 47.3% of cross-sectional variation in crypto returns. Higher-order terms account for ~25% and capture most insights from ML methods. Adding a Kolmogorov-Arnold neural network factor increases cross-sectional R-squared by ~25 percentage points.
- **Actionable Insight:** A 4-factor crypto model is now available for risk attribution and alpha generation. Consider incorporating KAN-based nonlinear factors for additional explanatory power.

### 3.5 Factor-Based Crypto Investing — Fama-MacBeth Application (2024)

- **Citation:** "Optimizing Cryptocurrency Returns: A Quantitative Study on Factor-Based Investing." *Mathematics* (MDPI), April 2024.
- **URL:** https://www.mdpi.com/2227-7390/12/9/1351
- **Key Finding:** Applied Fama-MacBeth cross-sectional regressions to 31 cryptos (2017-2023). Market, size, value, and momentum factors assessed. Weekly rebalancing accommodates crypto's constant fluctuations better than monthly.
- **Actionable Insight:** Use weekly rebalancing for factor-based strategies in crypto. Monthly is too slow given crypto market dynamics.

---

## 4. Market Microstructure and Order Flow

### 4.1 Bitcoin Wild Moves: VPIN and Price Jumps (2025)

- **Citation:** "Bitcoin Wild Moves: Evidence from Order Flow Toxicity and Price Jumps." *North American Journal of Economics and Finance* (ScienceDirect), 2025.
- **URL:** https://www.sciencedirect.com/science/article/pii/S0275531925004192
- **Key Finding:** VPIN significantly predicts future Bitcoin price jumps. Positive serial correlation in both VPIN and jump size, indicating persistent asymmetric information and momentum effects. High VPIN periods are followed by larger price movements.
- **Actionable Insight:** Implement real-time VPIN as a regime indicator. High VPIN signals approaching volatility and potential directional moves. Can be used to increase or decrease position sizes.

### 4.2 Easley et al. (2024) — Microstructure and Market Dynamics in Crypto

- **Citation:** Easley, D., et al. (2024). "Microstructure and Market Dynamics in Crypto Markets." Cornell University Working Paper.
- **URL:** https://stoye.economics.cornell.edu/docs/Easley_ssrn-4814346.pdf
- **Key Finding:** Post Bitcoin ETF approval (Jan 2024), BTC and ETH trade activity leads price changes and volatility in other cryptos. Microstructure measures with high explanatory power can have low predictive power and vice versa. These markets increasingly resemble traditional financial markets in microstructure.
- **Actionable Insight:** Monitor BTC and ETH microstructure as leading indicators for altcoin trading. Focus on microstructure features with demonstrated predictive (not just explanatory) power.

### 4.3 Anastasopoulos & Gradojevic (2025) — Order Flow and Crypto Returns

- **Citation:** Anastasopoulos, A., Gradojevic, N. (2025). "Order Flow and Cryptocurrency Returns." EFMA 2025 Conference Paper.
- **URL:** https://www.efmaefm.org/0EFMAMEETINGS/EFMA%20ANNUAL%20MEETINGS/2025-Greece/papers/OrderFlowpaper.pdf
- **Key Finding:** Weekly aggregated order flow reduces microstructure noise and explains more price variation than daily. US order flow accounts for 39% of variance, South Korea 24%. Together, top 2 countries explain 63% of total variance.
- **Actionable Insight:** Aggregate order flow signals at weekly frequency for best signal-to-noise ratio. Geographic decomposition of order flow matters -- US and Korean flows are most informative.

### 4.4 Explainable Patterns in Crypto Microstructure (2025)

- **Citation:** "Explainable Patterns in Cryptocurrency Microstructure." arXiv, 2025.
- **URL:** https://arxiv.org/html/2602.00776v1
- **Key Finding:** Stable cross-asset patterns in Binance Futures LOB microstructure (1-second frequency, Jan 2022 - Oct 2025). Feature rankings and partial effects are stable across assets. Order book imbalance is a potent predictor of near-term price movements, especially during instability. Model detected selling pressure buildup during the October 2025 flash crash.
- **Actionable Insight:** Order book imbalance is the single most important microstructure feature for short-term prediction. It is robust across assets and time periods. Implement order book imbalance as a high-frequency signal.

### 4.5 High-Frequency Dynamics of Bitcoin Futures (2025)

- **Citation:** "High-Frequency Dynamics of Bitcoin Futures: An Examination of Market Microstructure." *Borsa Istanbul Review* (ScienceDirect), 2025.
- **URL:** https://www.sciencedirect.com/science/article/pii/S2214845025001188
- **Key Finding:** Examined BTC and ETH perpetual futures on Binance (2020-2024). The Mixture of Distributions Hypothesis (MDH) fits crypto futures better than the Intraday Trading Invariance Hypothesis. Volume and volatility cluster together, driven by information arrival.
- **Actionable Insight:** Volume-volatility clustering is informative for crypto futures. Use volume bars (rather than time bars) for analysis, as information arrives in volume-time.

---

## 5. Carry Trade, Funding Rate, and Basis Arbitrage

### 5.1 Schmeling, Schrimpf, Todorov (2025) — Crypto Carry (BIS)

- **Citation:** Schmeling, M., Schrimpf, A., Todorov, K. (2025). "Crypto Carry." BIS Working Paper No. 1087.
- **URL:** https://www.bis.org/publ/work1087.pdf
- **Key Finding:** Average crypto carry (futures basis) is 6-8% p.a., frequently exceeding 20%, higher than any other asset class. Market segmentation from regulatory barriers drives persistent pricing distortions. Spot Bitcoin ETF introduction (Jan 2024) reduced carry by ~3pp across all exchanges and ~5pp on CME (97% reduction in CME-specific carry). This is causal evidence via difference-in-differences.
- **Actionable Insight:** Crypto carry/basis trade remains profitable but is compressing as markets mature. Focus on assets and exchanges where regulatory barriers still create segmentation. Monitor ETF approvals for new assets (SOL, XRP) as these will compress basis for those assets too.

### 5.2 Funding Rate Arbitrage on CEX and DEX (2025)

- **Citation:** "Exploring Risk and Return Profiles of Funding Rate Arbitrage on CEX and DEX." *Blockchain: Research and Applications* (ScienceDirect), 2025.
- **URL:** https://www.sciencedirect.com/science/article/pii/S2096720925000818
- **Key Finding:** Funding rate arb PnL decreases with market size (trading volume). Strategy examined across BTC, ETH, XRP, BNB, SOL on Binance, BitMEX, ApolloX, Drift. No correlation with HODL strategies (truly market-neutral). Leverage significantly affects risk-return profile.
- **Actionable Insight:** Implement funding rate arbitrage as a market-neutral return stream. Keep leverage conservative. Focus on smaller/newer assets where funding rate arb opportunities are larger. Diversify across exchanges (CEX + DEX) for cross-exchange arb.

### 5.3 Ackerer, Hugonnier, Jermann (2025) — Perpetual Futures Pricing

- **Citation:** Ackerer, D., Hugonnier, J., Jermann, U. (2025). "Perpetual Futures Pricing." *Mathematical Finance*, Wiley.
- **URL:** https://onlinelibrary.wiley.com/doi/10.1111/mafi.70018
- **Key Finding:** Derives explicit no-arbitrage pricing for linear, inverse, and quanto perpetual futures. The futures price equals the risk-neutral expectation of spot sampled at a random time reflecting the intensity of price anchoring. Provides the theoretical foundation for understanding when perpetual futures are mispriced.
- **Actionable Insight:** Use this theoretical framework to identify when perpetual futures deviate beyond no-arbitrage bounds, creating arb opportunities.

### 5.4 Dai, Li, Yang (2025) — Arbitrage in Perpetual Contracts

- **Citation:** Dai, M., Li, L., Yang, C. (2025). "Arbitrage in Perpetual Contracts." SSRN Working Paper.
- **URL:** https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5262988
- **Key Finding:** The clamping function in funding mechanisms is a key factor causing perpetual futures prices to deviate from spot. Model-free no-arbitrage bounds are derived. Deviations beyond these bounds create exploitable opportunities.
- **Actionable Insight:** Monitor the clamping function parameters across exchanges. Deviations outside no-arbitrage bounds are systematic arb opportunities.

### 5.5 Bitcoin Basis Trading Post-ETF (CME Group, 2025)

- **Citation:** CME Group / CF Benchmarks. "Spot ETFs Give Rise to Crypto Basis Trading." OpenMarkets, 2025.
- **URL:** https://www.cmegroup.com/openmarkets/equity-index/2025/Spot-ETFs-Give-Rise-to-Crypto-Basis-Trading.html
- **Key Finding:** Bitcoin basis approached 25% annualized in Feb 2024, exceeded 20% in Nov 2024. Briefly went below zero in March 2025 during risk-off. By May 2025, open interest ~32,000 contracts, basis ~10%. SOL and XRP front-month futures showed annualized basis spikes to 50% in July 2025.
- **Actionable Insight:** Basis trading on newer assets (SOL, XRP) shows much larger spreads than BTC/ETH. The basis is cyclical -- high during bull runs, compresses/inverts during selloffs. A dynamic approach that scales basis exposure with market conditions is optimal.

---

## 6. Regime Switching and Adaptive Strategies

### 6.1 Regime Switching Forecasting for Cryptocurrencies (2024)

- **Citation:** "Regime Switching Forecasting for Cryptocurrencies." *Digital Finance*, Springer, 2024.
- **URL:** https://link.springer.com/article/10.1007/s42521-024-00123-2
- **Key Finding:** Combines reinforcement learning with regime-switching concepts. Three regimes defined by volatility and return quantiles. RL model with regime-awareness shows potential for investment management. Cryptocurrency metadata is useful supplementary data.
- **Actionable Insight:** Define explicit volatility/return regimes (e.g., low-vol trending, high-vol trending, high-vol mean-reverting). Switch strategy parameters or strategy selection based on detected regime.

### 6.2 Regime Shifts in Crypto Markets — Bayesian Analysis (2025)

- **Citation:** "Regime Shifts in Cryptocurrency Markets: A Bayesian Analysis of Time-Varying Cryptocurrency Alphas." EFMA 2025.
- **URL:** https://www.efmaefm.org/0EFMAMEETINGS/EFMA%20ANNUAL%20MEETINGS/2025-Greece/papers/Bas%20et%20al%202024%20Alphas.pdf
- **Key Finding:** Combines Adaptive Bayesian Changepoints with Outliers (ABCO) methodology with hierarchical regime-switching. Identifies structural breaks in cryptocurrency alphas while handling extreme price movements and volatility clustering. Crypto alphas are time-varying and regime-dependent.
- **Actionable Insight:** Strategy alphas are not constant -- they shift with market regimes. A system that detects regime changes and adjusts accordingly will outperform a static strategy.

### 6.3 GMM-VAR Regime Identification (2025)

- **Citation:** "GMM-VAR Regime Identification for Cryptocurrency Markets." *Data Science in Finance and Economics*, AIMS Press, 2025.
- **URL:** https://www.aimspress.com/aimspress-data/dsfe/2025/3/PDF/DSFE-05-03-017.pdf
- **Key Finding:** GMMs outperform HMMs for crypto regime identification because they enable direct clustering without assuming time-dependent transitions. Crypto regime shifts are abrupt, non-sequential, and overlapping -- characteristics that HMMs handle poorly.
- **Actionable Insight:** Prefer GMM-based regime detection over HMM for crypto. HMM's Markov assumption (next state depends only on current state) is too rigid for crypto's sudden regime shifts. GMM allows for overlapping and non-sequential transitions.

### 6.4 Adaptive Crypto Trading: Directional Change + Meta-Learning (2024)

- **Citation:** Razmi, S., Barak, S. (2024). "Adaptive Crypto Trading Using Directional Change and Meta-Learning." SSRN Working Paper.
- **URL:** https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5017215
- **Key Finding:** Uses directional change (DC) events rather than fixed time intervals for sampling, combined with meta-learning for strategy adaptation. DC-based sampling aligns analysis with actual market dynamics rather than arbitrary time divisions.
- **Actionable Insight:** Consider directional-change-based sampling (event bars) instead of or alongside time bars. This aligns with Lopez de Prado's information-driven bars and can improve signal quality.

### 6.5 Volatility Jump Regimes in Crypto (2023)

- **Citation:** "The Risks of Trading on Cryptocurrencies: A Regime-Switching Approach Based on Volatility Jumps and Co-jumping Behaviours." *Applied Economics*, Taylor & Francis, 2023.
- **URL:** https://www.tandfonline.com/doi/full/10.1080/00036846.2023.2170970
- **Key Finding:** Bivariate SWARCH (regime-switching ARCH) models capture volatility regimes and correlation dynamics better than conventional GARCH models. State-dependent volatility and correlations are essential for accurate crypto portfolio risk estimation.
- **Actionable Insight:** Use regime-switching volatility models (not just single-regime GARCH) for risk management. Correlations between crypto assets change dramatically across regimes.

---

## 7. Volatility Forecasting and Timing

### 7.1 Machine Learning + GARCH Hybrid (2025)

- **Citation:** "Can Machine Learning Models Better Volatility Forecasting? A Combined Method." *European Journal of Finance*, Taylor & Francis, 2025.
- **URL:** https://www.tandfonline.com/doi/full/10.1080/1351847X.2025.2553053
- **Key Finding:** Hybrid ML + GARCH approaches outperform either method alone for BTC volatility forecasting. GARCH captures volatility clustering; ML captures nonlinear dynamics and extreme events.
- **Actionable Insight:** Combine GARCH as a base with ML overlay for volatility forecasting. Use GARCH for structure, ML for residual prediction.

### 7.2 GARCH-Family Comparison Across BTC, ETH, BNB (2025)

- **Citation:** "Volatility Dynamics of Cryptocurrencies: A Comparative Analysis Using GARCH-Family Models." *Future Business Journal*, Springer, 2025.
- **URL:** https://link.springer.com/article/10.1186/s43093-025-00568-w
- **Key Finding:** Different GARCH variants are optimal for different assets: TGARCH for BTC, EGARCH for ETH, CGARCH for BNB. Volatility dynamics differ significantly between market crashes and bull markets.
- **Actionable Insight:** Do not use the same volatility model for all assets. Asset-specific model selection improves forecasts. Consider ensemble approaches that weight models by recent performance.

### 7.3 GARCH(3,3) for Bitcoin (2025)

- **Citation:** Khoso, W. (2025). "Forecasting Bitcoin Price Volatility Using GARCH Models and Real-Time Data." SSRN Working Paper.
- **URL:** https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5279513
- **Key Finding:** Using daily BTC data from 2014-2024, GARCH(3,3) provides the best fit by AIC, BIC, MSE. Forecasted volatility steadily increases over the horizon, reflecting growing uncertainty. Expected returns oscillate near zero.
- **Actionable Insight:** For longer-horizon BTC volatility forecasting, allow for higher-order GARCH terms. The GARCH(1,1) commonly used as a default may be insufficient for BTC.

### 7.4 HAR vs. GARCH for Crypto (2025)

- **Citation:** "Cryptocurrency Price Returns Volatility Modeling and Forecasting with GARCH Models." *RAUSP Management Journal*, Emerald, 2025.
- **URL:** https://www.emerald.com/rausp/article/60/1/220/1276890
- **Key Finding:** For daily data, GARCH methods provide higher accuracy. For intraday data, HAR (Heterogeneous Autoregressive) models based on realized volatility outperform GARCH. Best GARCH methods are low-parameter, asymmetric models.
- **Actionable Insight:** Use HAR models when high-frequency data is available; GARCH for daily data. In either case, use asymmetric variants (EGARCH, TGARCH) to capture leverage effects.

### 7.5 Graph Neural Network Volatility Forecasting (2025)

- **Citation:** "Forecasting Cryptocurrency Volatility: A Novel Framework Based on the Evolving Multiscale Graph Neural Network." *Financial Innovation*, Springer, 2025.
- **URL:** https://link.springer.com/article/10.1186/s40854-025-00768-x
- **Key Finding:** An Evolving Multiscale Graph Neural Network (EMGNN) embeds cross-market interactions into volatility prediction. Captures time-varying relationships between crypto and traditional markets that simpler models miss.
- **Actionable Insight:** Crypto volatility is increasingly interconnected with traditional markets (equities, rates). Models that incorporate cross-market relationships outperform univariate approaches.

### 7.6 HAR + Sentiment Horserace (2024)

- **Citation:** "Crypto Volatility Forecasting: Mounting a HAR, Sentiment, and Machine Learning Horserace." *Asia-Pacific Financial Markets*, Springer, 2024.
- **URL:** https://link.springer.com/article/10.1007/s10690-024-09510-6
- **Key Finding:** ML (LightGBM, XGBoost, LSTM) improves upon HAR baseline. Investor sentiment influences crypto volatility nonlinearly and can only be captured by ML methods. Sentiment data provides incremental forecasting power.
- **Actionable Insight:** Incorporate sentiment signals into volatility forecasting. LightGBM and XGBoost are practical, efficient choices for sentiment-augmented vol models.

---

## 8. Machine Learning for Crypto Trading

### 8.1 Technical Analysis Meets ML: Bitcoin Evidence (2025)

- **Citation:** "Technical Analysis Meets Machine Learning: Bitcoin Evidence." arXiv, 2025.
- **URL:** https://arxiv.org/html/2511.00665v1
- **Key Finding:** LSTM achieved ~65% cumulative return in under a year (Jan-Dec 2024), outperforming LightGBM, EMA crossover, MACD+ADX, and buy-and-hold. Evaluated on the post-BTC-ETF period as a natural experiment.
- **Actionable Insight:** LSTM models can meaningfully outperform traditional TA for BTC trading. However, this is a single out-of-sample period. Use ensemble approaches and CPCV validation before relying on a single model.

### 8.2 Forecasting and Trading Under Changing Markets (2025)

- **Citation:** "Forecasting and Trading Cryptocurrencies with Machine Learning Under Changing Market Conditions." Springer, 2025.
- **URL:** https://link.springer.com/chapter/10.1007/978-981-96-6839-7_10
- **Key Finding:** In a bear market stress test (BTC -55%, ETH -77%, LTC -66%), best voting ensemble delivered annualized returns of 1.25%, 9.62%, 5.73% after 0.5% round-trip costs. Uses 43 exchange and blockchain features. Hyperparameters chosen by maximizing average return per trade, not accuracy.
- **Actionable Insight:** Optimize ML models for return-per-trade, not classification accuracy. Ensemble methods (voting) improve robustness in changing conditions. Include on-chain/blockchain features alongside price data.

### 8.3 Confidence-Threshold Framework (2025)

- **Citation:** "Machine Learning Analytics for Blockchain-Based Financial Markets: A Confidence-Threshold Framework for Cryptocurrency Price Direction Prediction." MDPI, 2025.
- **URL:** https://www.mdpi.com/2076-3417/15/20/11145
- **Key Finding:** Separating directional prediction from execution decisions provides a systematic way to manage uncertainty. Achieves 151 bps average profit per trade on 600-minute horizons. Consistent across 11 cryptocurrency pairs rather than optimized per asset.
- **Actionable Insight:** Implement a confidence threshold: only trade when the model's prediction confidence exceeds a threshold. This is essentially meta-labeling applied to ML signals. Cross-asset consistency is more reliable than per-asset optimization.

### 8.4 Simpler Models Can Rival Complex Ones (2025)

- **Citation:** "Machine Learning Approaches to Cryptocurrency Trading Optimization." *Discover AI*, Springer, 2025.
- **URL:** https://link.springer.com/article/10.1007/s44163-025-00519-y
- **Key Finding:** Naive/simple models sometimes outperform complex ML and deep learning models, suggesting crypto time series have similarity to Brownian noise at certain frequencies. Complexity is not always rewarded.
- **Actionable Insight:** Always benchmark ML strategies against simple baselines (buy-and-hold, simple momentum, random walk). If a complex model cannot significantly outperform simple baselines after costs, prefer the simpler approach.

### 8.5 Algorithmic Crypto Trading with Information-Driven Bars (2025)

- **Citation:** "Algorithmic Crypto Trading Using Information-Driven Bars, Triple Barrier Labeling and Deep Learning." *Financial Innovation*, Springer, 2025.
- **URL:** https://link.springer.com/article/10.1186/s40854-025-00866-w
- **Key Finding:** Alternative sampling methods (CUSUM filter, range bars, volume bars, dollar bars) combined with triple barrier labeling enhance ML trading strategies for BTC and ETH. Tick-level data from 2018-2023. Information-driven bars outperform time bars for ML signal generation.
- **Actionable Insight:** Switch from time bars to information-driven bars (dollar bars or volume bars) for ML feature generation. Combine with triple barrier labeling for target variable construction.

---

## 9. Mean Reversion and Pairs Trading

### 9.1 Deep Learning Pairs Trading with Cointegrated Crypto (2026)

- **Citation:** "Deep Learning-Based Pairs Trading: Real-Time Forecasting of Co-Integrated Cryptocurrency Pairs." *Frontiers in Applied Mathematics and Statistics*, 2026.
- **URL:** https://www.frontiersin.org/journals/applied-mathematics-and-statistics/articles/10.3389/fams.2026.1749337/full
- **Key Finding:** Establishes pairs trading using dynamic Johansen cointegration on 6 major pairs (BTC-ETH, BTC-LTC, BTC-XRP, ETH-LTC, ETH-XRP, LTC-XRP). DNN and LSTM model the spread for dynamic trading signals. Pairs selected based on mean-reversion properties (ADF, Hurst exponent).
- **Actionable Insight:** Use Johansen cointegration (not just Engle-Granger) for multi-asset pairs identification. DNN/LSTM spread models generate better signals than static z-score thresholds. Hurst exponent < 0.5 confirms mean-reverting spread suitability.

### 9.2 Pairs Trading Frequency Analysis (IEEE, 2020)

- **Citation:** "Pairs Trading in Cryptocurrency Markets." *IEEE Access*, 2020.
- **URL:** https://ieeexplore.ieee.org/document/9200323/
- **Key Finding:** Distance and cointegration methods tested on 26 Binance cryptos at 5-min, 1-hour, and daily frequencies. Higher frequency dramatically improves results: daily distance method returned -0.07%/month, but 5-minute returned 11.61%/month. Higher frequency = more trades = more profit.
- **Actionable Insight:** Pairs trading in crypto is primarily a high-frequency strategy. Daily frequency is insufficient. Target 5-minute to 1-hour bars minimum. Need low latency and low fees to capture these spreads.

### 9.3 BTC/ETH Ratio Mean Reversion (Crypto.com, 2024)

- **Citation:** Crypto.com Research. "Pair Trading." September 2024.
- **URL:** https://crypto.com/us/research/pair-trading-sep-2024
- **Key Finding:** BTC/ETH price ratio exhibited mean-reversion from Jan 2021 to Aug 2024. Z-score-based trading on the ratio outperformed individual asset trading. Portfolio approach across multiple Binance pairs yielded ~10x more trades and profit vs. single pair, with -29% max drawdown vs. BTC's -83%.
- **Actionable Insight:** BTC/ETH ratio is the most liquid and reliable crypto pair. Trade a diversified portfolio of pairs, not just one. Portfolio diversification reduces drawdowns dramatically.

### 9.4 Statistical Validation for Crypto Pairs

- **Citation:** Amberdata. "Crypto Pairs Trading: Verifying Mean Reversion with ADF and Hurst Tests." 2024.
- **URL:** https://blog.amberdata.io/crypto-pairs-trading-part-2-verifying-mean-reversion-with-adf-and-hurst-tests
- **Key Finding:** ADF test and Hurst exponent are essential for validating pair suitability. Hurst < 0.5 = anti-persistent (mean-reverting). Many apparently cointegrated pairs fail these tests when properly validated. Stop losses harm mean-reversion strategies unless very wide.
- **Actionable Insight:** Always validate pairs with ADF + Hurst exponent before trading. Do not use tight stop-losses with mean-reversion strategies. Z-score of +/-2 is a common entry threshold.

---

## 10. Portfolio Optimization and Risk Management

### 10.1 Stanford — Simple Crypto Portfolio Construction (2025)

- **Citation:** Boyd et al. (2025). "Simple and Effective Crypto Portfolio Construction." Stanford / Competition Policy International.
- **URL:** https://stanford.edu/~boyd/papers/pdf/crypto_portfolio_cpi.pdf
- **Key Finding:** Standard portfolio construction methods (mean-variance, risk parity) work for combined traditional + crypto portfolios. The DD90/10 allocation (90% traditional, 10% crypto) is proposed. Beyond 5% crypto allocation, volatility increases offset incremental return gains.
- **Actionable Insight:** For multi-asset portfolios including crypto, 5-10% allocation is optimal. Standard optimization methods work -- no need for exotic approaches. Focus on risk allocation rather than return maximization.

### 10.2 LSTM-Based Portfolio Weight Optimization (2025)

- **Citation:** "Cryptocurrency Price Prediction and Portfolio Optimization." *Quantitative Finance and Economics*, AIMS Press, 2025.
- **URL:** https://www.aimspress.com/aimspress-data/qfe/2025/3/PDF/QFE-09-03-023.pdf
- **Key Finding:** A novel loss function using the Negative Sharpe Ratio trains neural networks to directly optimize portfolio weights. Multi-task learning combines price prediction with allocation optimization.
- **Actionable Insight:** Train portfolio optimization models directly on risk-adjusted returns (Sharpe ratio) rather than prediction accuracy. End-to-end optimization outperforms predict-then-optimize approaches.

### 10.3 Sentiment-Aware Mean-Variance Optimization (2025)

- **Citation:** "Sentiment-Aware Mean-Variance Portfolio Optimization for Cryptocurrencies." arXiv, August 2025.
- **URL:** https://arxiv.org/pdf/2508.16378
- **Key Finding:** Incorporating sentiment extracted from crypto news (market updates, regulatory news, technological developments) into mean-variance optimization improves portfolio performance. Data spans Feb 2020 to Aug 2025.
- **Actionable Insight:** Sentiment data provides meaningful input for portfolio optimization. Integrate crypto news sentiment into expected return estimates.

### 10.4 Network Clustering for Portfolio Construction (2025)

- **Citation:** "Optimising Cryptocurrency Portfolios Through Stable Clustering of Price Correlation Networks." arXiv, May 2025.
- **URL:** https://arxiv.org/html/2505.24831v1
- **Key Finding:** Louvain community detection + consensus clustering identifies temporally stable groups of correlated cryptos. ARIMA-based price prediction ensures good performance for up to 14-day horizons. Risk parity allocation within clusters improves diversification.
- **Actionable Insight:** Use correlation network clustering to identify diversified crypto baskets. Risk parity allocation within clusters provides balanced risk exposure. Rebalance at least every 2 weeks.

### 10.5 Monte Carlo Crypto Portfolio Risk Framework (2025)

- **Citation:** "Quantifying Crypto Portfolio Risk: A Simulation-Based Framework Integrating Volatility, Hedging, Contagion, and Monte Carlo Modeling." arXiv, July 2025.
- **URL:** https://arxiv.org/html/2507.08915v1
- **Key Finding:** Modular framework integrating volatility stress testing, stablecoin hedging, contagion modeling, and Monte Carlo simulation. Validated with 2020-2024 BTC, ETH, USDT data. Traditional risk models miss crypto-specific tail risks.
- **Actionable Insight:** Use Monte Carlo simulation for portfolio risk assessment. Model contagion explicitly -- crypto assets are more correlated in crashes than in normal times. Include stablecoin hedging in risk scenarios.

---

## 11. On-Chain Metrics and Alternative Data

### 11.1 From On-Chain to Macro: Data Source Diversity (2025)

- **Citation:** "From On-chain to Macro: Assessing the Importance of Data Source Diversity in Cryptocurrency Market Forecasting." arXiv, June 2025.
- **URL:** https://arxiv.org/html/2506.21246
- **Key Finding:** Combining on-chain data (Coinmetrics), market data (CoinGecko), and macro data (ECB) improves forecasting beyond any single source. Data source diversity matters -- each source captures different information.
- **Actionable Insight:** Build feature sets from multiple data domains: on-chain (active addresses, NUPL, MVRV), exchange (funding rates, order flow), and macro (DXY, rates, equity factors). No single source is sufficient.

### 11.2 Glassnode: Predictive Power of On-Chain Data

- **Citation:** Glassnode Insights. "The Predictive Power of Glassnode Data." 2024.
- **URL:** https://insights.glassnode.com/the-predictive-power-of-glassnode-data/
- **Key Finding:** ML-based feature selection identifies which on-chain metrics have the most predictive power for BTC price movements. Boruta feature selection + CNN-LSTM achieved 82.44% accuracy in predicting Bitcoin price direction. Not all on-chain metrics are equally useful.
- **Actionable Insight:** Use systematic feature selection (Boruta, SHAP) to identify which on-chain metrics actually predict returns. Key metrics: NUPL, MVRV Z-score, SOPR, exchange reserves, whale tracking, NVT ratio. Most predictive metrics may change over time.

### 11.3 Key On-Chain Metrics Reference

| Metric | Description | Signal |
|--------|-------------|--------|
| NUPL | Net Unrealized Profit/Loss | Market tops (>0.75) and bottoms (<0) |
| MVRV Z-Score | Market Value / Realized Value ratio | Overvaluation (>7) / undervaluation (<0) |
| SOPR | Spent Output Profit Ratio | Selling at profit (>1) or loss (<1) |
| NVT Ratio | Network Value to Transactions | High = overvalued, Low = undervalued |
| Exchange Reserves | Coins held on exchanges | Declining = reduced sell pressure |
| Active Addresses | Daily unique addresses | Network activity / adoption proxy |
| Hash Rate | Mining computational power | Network security / miner confidence |

---

## 12. Information Flow and Lead-Lag Relationships

### 12.1 Transfer Entropy for Crypto Information Flow (2022)

- **Citation:** Almeida, D., Dionisio, A., Ferreira, P. (2022). "Using Transfer Entropy to Measure Information Flows Between Cryptocurrencies." *Physica A*, 586, 126484.
- **URL:** https://www.sciencedirect.com/science/article/abs/pii/S0378437121007573
- **Key Finding:** Transfer entropy detects nonlinear information flows that Granger causality misses. Bitcoin-Ripple have bidirectional flow; Ripple leads Ethereum unidirectionally. Dependencies are mostly nonlinear, making transfer entropy superior to linear methods.
- **Actionable Insight:** Use transfer entropy instead of Granger causality for lead-lag detection. It captures nonlinear dependencies that dominate crypto markets. Update lead-lag estimates regularly as they shift.

### 12.2 Barak (2023) — Transfer Entropy for Dynamic Feature Selection

- **Citation:** Barak, S. (2023). "Transfer-Entropy-Based Dynamic Feature Selection for Evaluating Bitcoin Price Drivers." *Journal of Futures Markets*, Wiley.
- **URL:** https://onlinelibrary.wiley.com/doi/abs/10.1002/fut.22453
- **Key Finding:** Uses transfer entropy as a feature selection mechanism for BTC forecasting. Significant change in information flow patterns during COVID-19 onset. TE-based feature selection outperformed traditional benchmarks for multi-step-ahead BTC return forecasting.
- **Actionable Insight:** Use transfer entropy for dynamic feature selection -- which inputs are most predictive changes over time. Regime shifts (like COVID) dramatically alter information flow patterns.

### 12.3 BTC as Dominant Information Driver (2025)

- **Citation:** Study on corporate Bitcoin treasury strategies and equity markets using transfer entropy, 2025.
- **Key Finding:** BTC is consistently the dominant information driver in the crypto-equity nexus. Brief feedback from stocks to BTC occurs only during major announcement events.
- **Actionable Insight:** BTC leads the crypto market and increasingly influences equity prices of crypto-exposed firms. Monitor BTC as the primary information source for the broader crypto ecosystem.

---

## 13. Financial Machine Learning Methods

### 13.1 Triple Barrier Method — Crypto Application (2025)

- **Citation:** "Algorithmic Crypto Trading Using Information-Driven Bars, Triple Barrier Labeling and Deep Learning." *Financial Innovation*, Springer, 2025.
- **URL:** https://link.springer.com/article/10.1186/s40854-025-00866-w
- **Key Finding:** Triple barrier labeling combined with information-driven sampling (CUSUM filter, volume/dollar bars) and deep learning significantly improves crypto trading performance. Uses tick-level BTC and ETH data (2018-2023).
- **Actionable Insight:** Replace fixed-horizon labeling with triple barrier method for all ML target generation. Combine with volume bars for optimal results.

### 13.2 GA-Optimized Triple Barrier for Pairs Trading (2024)

- **Citation:** "Enhanced Genetic-Algorithm-Driven Triple Barrier Labeling Method and Machine Learning Approach for Pair Trading Strategy in Cryptocurrency Markets." *Mathematics* (MDPI), 2024.
- **URL:** https://www.mdpi.com/2227-7390/12/5/780
- **Key Finding:** Genetic algorithms optimize triple barrier parameters, producing two signal types: High Risk/High Profit (HRHP) and Low Risk/Low Profit (LRLP). ML models trained on GA-optimized labels outperform fixed-parameter approaches for crypto pairs trading.
- **Actionable Insight:** Optimize triple barrier parameters per strategy and asset using GA or Bayesian optimization. Different risk/return profiles can be generated from the same data by varying barrier widths.

### 13.3 Combinatorial Purged Cross-Validation (CPCV)

- **Citation:** Lopez de Prado, M. (2018). "Advances in Financial Machine Learning." Wiley. Chapter 12.
- **URL:** https://en.wikipedia.org/wiki/Purged_cross-validation
- **Key Finding:** Standard k-fold CV fails for financial time series (non-IID data). CPCV partitions data into N groups, tests all k-group combinations, with purging (removing overlapping labels) and embargoing (small buffer between train/test). Produces a distribution of performance metrics rather than a single estimate. Significantly reduces overfitting compared to walk-forward.
- **Actionable Insight:** Use CPCV for all ML model validation in our system. Implementation available in skfolio (`CombinatorialPurgedCV`). Set embargo h ~ 0.01T. The goal is stable performance across paths, not peak performance on a single path.

### 13.4 Backtest Overfitting Comparison (2024)

- **Citation:** "Backtest Overfitting in the Machine Learning Era: A Comparison of Out-of-Sample Testing Methods in a Synthetic Controlled Environment." *Knowledge-Based Systems* (ScienceDirect), 2024.
- **URL:** https://www.sciencedirect.com/science/article/abs/pii/S0950705124011110
- **Key Finding:** CPCV demonstrates marked superiority in mitigating overfitting risks, outperforming walk-forward and standard k-fold as evidenced by lower Probability of Backtest Overfitting (PBO) and superior Deflated Sharpe Ratio (DSR).
- **Actionable Insight:** CPCV should be the default validation method. Walk-forward is a single historical path that can be easily overfit. CPCV provides statistical confidence in strategy robustness.

---

## Summary: What Works in Crypto (Evidence-Based)

### Strongest Evidence (Multiple Papers Confirm)

| Strategy | Evidence Level | Key Requirement |
|----------|---------------|-----------------|
| Time-series momentum | Strong (but declining for BTC) | Volatility scaling, volume weighting |
| Funding rate / basis arbitrage | Strong | Low leverage, cross-exchange diversification |
| Volatility-managed momentum | Strong | Inverse volatility position sizing |
| Order book imbalance (HF) | Strong | Sub-second data, low latency |
| VPIN as risk filter | Strong | Volume-time computation |
| Pairs trading (high frequency) | Strong | 5-min to 1-hour bars, statistical validation |

### Moderate Evidence (Some Papers, Needs More Validation)

| Strategy | Evidence Level | Key Caveat |
|----------|---------------|------------|
| Cross-sectional momentum | Moderate | Works mainly for large-caps, crash-prone |
| Factor models (market/size/momentum) | Moderate | Crypto-specific factors still evolving |
| ML-enhanced trend following | Moderate | Simple models often rival complex ones |
| On-chain metrics for timing | Moderate | Predictive metrics shift over time |
| Regime-switching strategies | Moderate | Regime detection is hard in real-time |

### Key Implementation Principles

1. **Volatility scaling is non-negotiable** -- every momentum/trend strategy must scale positions inversely to recent volatility
2. **Transaction costs matter enormously** -- strategies that look great frictionless often fail with realistic costs (0.1%+ round trip)
3. **Use information-driven bars** (volume, dollar) over time bars for ML applications
4. **CPCV over walk-forward** for strategy validation
5. **Triple barrier labeling** over fixed-horizon returns for ML target generation
6. **Weekly rebalancing** is better than monthly for factor strategies in crypto
7. **BTC efficiency is increasing** -- pure TSMOM works better on altcoins
8. **Diversify across strategies** -- momentum, mean-reversion, and carry are uncorrelated
9. **Monitor regime shifts** -- strategy alphas are time-varying
10. **Basis/carry is compressing** post-ETF -- focus on newer assets for larger spreads
