# Quant Fund Research-to-Production Processes

> Knowledge base compiled from public sources on how top quantitative trading firms structure
> their strategy development pipelines. Adapted for crypto trading strategy development.

---

## Table of Contents

1. [Renaissance Technologies (Medallion Fund)](#1-renaissance-technologies-medallion-fund)
2. [Two Sigma](#2-two-sigma)
3. [D.E. Shaw](#3-de-shaw)
4. [AQR Capital Management](#4-aqr-capital-management)
5. [Citadel / Citadel Securities](#5-citadel--citadel-securities)
6. [Jump Trading](#6-jump-trading)
7. [Alameda Research (Anti-Patterns)](#7-alameda-research-anti-patterns)
8. [Cross-Firm Synthesis: Kill Criteria & Gate Metrics](#8-cross-firm-synthesis-kill-criteria--gate-metrics)
9. [Actionable Framework for Crypto Strategy Development](#9-actionable-framework-for-crypto-strategy-development)

---

## 1. Renaissance Technologies (Medallion Fund)

### Overview
- Founded 1982 by Jim Simons (mathematician, former NSA codebreaker)
- Medallion Fund: 71.8% annualized returns (1994-2014, before fees)
- ~90 PhDs in mathematics, physics, computer science
- Capped at ~$10B AUM; returns all profits every 6 months to prevent growth drag

### Research-to-Production Pipeline

**Single-Model Architecture:** Everyone works on ONE integrated model. No compartmentalized teams
or siloed strategies. The entire codebase (millions of lines) is readable by every researcher. A
string theorist can modify the commodity trading algorithm if they have an idea.

**Signal Discovery Process:**
1. Researchers explore any data source (weather, shipping, lunar cycles, etc.)
2. Model the market as a complex, data-rich environment
3. Test thousands of signals continuously
4. Discard 99%+ of tested signals
5. Deploy only those with extreme statistical confidence across multiple validation layers

**What Makes Them Win:**
- The edge is NOT a single brilliant insight but a system for discovering, testing, and deploying
  thousands of small insights while ruthlessly eliminating failures
- Right on only ~50.75% of trades, but across millions of trades that compounds
- Earn 0.01%-0.05% per trade, meaningless individually, extraordinary at scale
- Use 10-20x leverage because the high win rate and tight volatility management allow it
- Process 150,000+ trades daily
- 50,000 compute cores, 40TB daily data processing
- Models reinvented every ~2 years; no "holy grail"

### Kill Criteria (Inferred)
- Signal must survive extreme statistical confidence thresholds
- Must work across multiple validation layers (not just one backtest period)
- 99%+ rejection rate is the norm

### Team Structure
- Hire ONLY scientists (physicists, mathematicians, statisticians, CS PhDs)
- Financial knowledge is learnable; raw scientific talent is not
- Deeply collaborative, non-hierarchical research culture
- Key contributors: Baum (HMM/cryptanalysis), Berlekamp (new models), Laufer (scaling),
  Straus (data backbone), Brown & Mercer (integrated trading system from IBM speech recognition)

### Key Takeaway for Crypto
**Build one integrated system, not a collection of independent strategies.** Encourage
cross-pollination. Keep the strategy pool small (AUM-capped equivalent). Iterate the model
continuously rather than seeking a permanent edge.

---

## 2. Two Sigma

### Overview
- Founded 2001; manages $60B+
- 1,700+ employees; 70% in R&D
- Philosophy: scientific method applied to financial markets
- 300+ petabytes of data from 10,000+ sources
- Runs 48,000+ simulations daily

### Research-to-Production Pipeline (4 Stages)

| Stage | Description |
|-------|-------------|
| **1. Data Sourcing & Preparation** | Source, organize, enrich economically relevant data. 10,000+ data sources. Deep expertise to identify the most suitable datasets for specific hypotheses. |
| **2. Modeling** | Style-agnostic research process. Toolkit ranges from ridge regressions to NLP. Extract signal from noise across traditional and non-traditional data. Transform data into features (data + IP). |
| **3. Portfolio Construction** | Determine optimal portfolio allocation across signals. |
| **4. Execution** | Efficient trading strategies to capture alpha. Reinforcement learning for trade execution. |

### Iteration Speed
- Some QRs build backtesting frameworks and improve internal research platforms
- Open-source DNA (BeakerX, Flint) — engineering-first culture
- Computing power ranks in world's top 5 supercomputer sites
- 380+ PB storage capacity
- QRs move fluidly between research and the code that operationalizes it

### Evolution: AI Integration (2020s)
- Deep neural networks with millions of parameters for price prediction
- Reinforcement learning for trade execution optimization
- LLMs via secure internal "workbench" for unstructured text feature extraction
- Foundation models with "rigorous governance standards to enable rapid, reliable iteration"

### Kill Criteria (Inferred)
- Hypothesis must be testable against specific datasets
- Style-agnostic: no bias toward any single factor or approach
- Survived the 2007 "quant quake" by iterating on data infrastructure and risk models
- Strategies must be robust across multiple market regimes

### Team Structure
- QR (Quantitative Researcher) is the core role
- QRs are expected to both research AND write production code
- Engineers build platform infrastructure
- More like a tech company than a hedge fund

### Key Takeaway for Crypto
**Invest heavily in data infrastructure first.** The research platform IS the edge. Make it
trivially easy to go from hypothesis to backtest. 48,000 simulations/day means ideas die fast
if they don't work.

---

## 3. D.E. Shaw

### Overview
- Founded 1989 by David Shaw (CS professor)
- $85B+ in investment and committed capital (Dec 2025)
- Pioneer in systematic investing, now ~50% discretionary
- 700+ developers and engineers
- ~20% annualized net returns since 2020

### Research-to-Production Pipeline

**Hybrid Systematic-Discretionary Model:**
- Strategies span the full continuum from pure systematic to pure discretionary
- Systematic: quantitative and computational techniques developed over 35+ years
- Discretionary: fundamental analysis with a quant-driven, process-oriented approach
- Both share data and technological resources

**Systematic Strategy Process:**
1. Identify statistically robust market inefficiencies via scientific research
2. Build on practical knowledge of capital markets
3. Sophisticated data processing and advanced computational methods
4. When platforms don't meet standards, build their own
5. Versioned data infrastructure (Versioned HDF5) for reproducible, time-travel-capable research

**Evolution Pattern:**
- Started with equity stat arb (several % of all US equity volume)
- Margins eroded by competition -> expanded to systematic futures (equity indexes, rates, FX, commodities)
- Added macro, volatility, equity arbitrage, ABS, credit
- Qualitative elements grew as firm diversified

### Kill Criteria (Inferred)
- "Culture of analytical rigor" as bedrock
- Strategies must demonstrate statistical robustness
- Reproducibility enforced through versioned datasets
- When competition erodes margins, move to new markets rather than forcing old strategies

### Team Structure
- 700+ developers/engineers alongside researchers
- Discretionary PMs with quant support
- Shared infrastructure across all strategy types
- Not a "pod shop" like Millennium/Citadel for discretionary

### Key Takeaway for Crypto
**Version your data and make research reproducible.** When an edge erodes, have the infrastructure
to pivot to new markets/instruments quickly. The hybrid model works: use systematic for core
alpha, discretionary for macro overlays.

---

## 4. AQR Capital Management

### Overview
- Founded 1998 by Cliff Asness, Robert Krail, John Liew (Chicago PhD program)
- Factor-based systematic investing
- "Publish-then-trade" philosophy: academic transparency does not erode returns
- Core factors: Value, Momentum, Defensive, Carry

### Research-to-Production Pipeline

**Academic-to-Trading Pipeline:**
1. Research begins in academic literature and internal hypothesis generation
2. Identify factors with economic or behavioral rationale
3. Build model to test strategy viability
4. Backtest and analyze across markets and time periods
5. Deploy with systematic, consistent portfolio construction
6. Publish findings openly (papers, public datasets)

**Model Inputs:**
- Traditional valuation measures
- Momentum indicators
- Price signals
- Textual analysis of financial reporting
- Terms of trade information
- Proprietary signal construction methodology
- Optimization process and trading approach

**The "Publish Then Trade" Evidence:**
- Research shows majority of factors hold up AFTER public discovery
- Factor efficacy is STRENGTHENED (not weakened) by the large number of observed factors
- Global evidence further strengthens factor persistence
- Hundreds of factors reduce to a smaller number of core themes
- Factor strategies provide diversification independent of market conditions

### Kill Criteria
- Factor must have economic or behavioral rationale (no data-mining)
- Must survive out-of-sample testing across multiple markets
- Must persist after publication (if it doesn't, it was likely overfitting)
- "High conviction in the process, not high conviction in any particular stock"
- Strategy must demonstrate robustness across market regimes

### Team Structure
- Deep academic connections (founders from Chicago PhD program)
- Research-first culture
- Publish research openly and share datasets
- No secrecy advantage; edge comes from disciplined, systematic execution

### Key Takeaway for Crypto
**If a signal can't survive being published, it's not robust enough to trade.** Demand economic
rationale for every signal. Test across multiple crypto markets and time periods. Factor
diversification (using Value + Momentum + Carry + Defensive together) outperforms any
single factor.

---

## 5. Citadel / Citadel Securities

### Overview
- Founded by Ken Griffin
- $66B+ investment capital; ~$400B AUM (mid-2025)
- Five core strategies: Equities, Commodities, Fixed Income & Macro, Global Quantitative Strategies, Credit
- "Pod shop" model: decentralized teams under tight risk controls
- 2025 returns: Wellington 10.2%, Tactical 18.6%, Equity 14.5%, Fixed Income 9.4%

### Research-to-Production Pipeline

**Multi-Strategy Pod Architecture:**
- Independent teams manage distinct strategies
- Each pod has tight risk limits and drawdown controls
- No overexposure to any single sector or asset class
- Rapid pivoting capability during market dislocations
- Cross-asset integration via unified trading systems

**Quantitative Process:**
1. GQS (Global Quantitative Strategies) builds and executes algorithmic strategies
2. EQR (Equity Quantitative Research) identifies market inefficiencies through structural analysis
3. Teams of physicists, actuaries, engineers create advanced quantitative models
4. Portfolios continuously adjusted using detailed data and regular analysis
5. Near-zero market exposure maintained (dollar-neutral)

**Execution Edge:**
- Active in 50+ markets, 150+ trading venues
- Proprietary tools and models developed with investment teams
- AI integrated as productivity tool; decades-long ML use in equity trading
- Hired PhD meteorologists for commodities weather forecasting
- Petabytes of structured and unstructured data

**Risk Management:**
- Systems designed to function even if assets reduced by 90%
- Pod-level drawdown limits prevent single-strategy blowups
- Liquidity treated as tactical asset
- Volatility technicals and institutional positioning monitored continuously
- Equity index hedges added proactively during low implied volatility

### Kill Criteria
- Pod-level: strict drawdown limits; if exceeded, position sizes cut or pod shut down
- Strategy must demonstrate alpha independent of other strategies
- Must work across multiple market conditions
- "Know your edge and press it when right, maintain flexibility to pivot when wrong"

### Team Structure
- Pod model: each PM/team operates quasi-independently
- Shared infrastructure, risk management, and execution
- 0.4% internship acceptance rate (more selective than Harvard)
- Specialized domain experts (e.g., meteorologists for commodities)

### Key Takeaway for Crypto
**Pod-level risk limits are essential for multi-strategy.** Each strategy should have
independent drawdown limits. Design systems to survive 90% drawdowns. Treat liquidity as a
first-class strategic asset (especially critical in crypto).

---

## 6. Jump Trading

### Overview
- Headquartered in Chicago
- One of the world's largest HFT firms
- Specializes in algorithmic/high-frequency trading across global markets
- Custom hardware (ASICs, FPGAs), microwave towers, co-location
- Strategies span microseconds (HFT) to longer-term macro

### Research-to-Production Pipeline

**Signal Validation Philosophy:**
- "Extremely low signal-to-noise ratio makes it incredibly easy to fool yourself"
- "If data is not pristine, you'll fit the noise, not the signal"
- Hard performance constraints: model must process every symbol under tight hardware constraints
- "Easy to demo ideas in a notebook; truly valuable is 99.9% accuracy on real data, parsed on the fly"
- "Everything gets tested, deployed, and improved -- fast"

**Infrastructure-First Approach:**
1. Custom hardware: ASICs, FPGAs for sub-microsecond execution
2. Specialized networking protocols and switching technologies
3. Lock-free algorithms operating at physical limits of speed
4. Real-time processing across heterogeneous environments near major exchanges
5. Microwave tower infrastructure for lowest-latency market access

**AI/ML Integration:**
- Classical models executed with extreme scale and precision
- Frontier-level deep learning, reinforcement learning, LLM, generative modeling
- Partnership with UCL Gatsby Computational Neuroscience Unit
- Focus: systems that generalize, adapt under distribution shift, operate in noisy environments
- "Rigorous theory, real constraints, fast feedback"

### Kill Criteria
- Model must achieve 99.9% accuracy on real data parsed in real-time
- Must meet hard performance constraints (latency, throughput per symbol)
- "If it works in a notebook but not in production, it doesn't work"
- Signal must survive pristine data requirements (any data quality issue = reject)
- Must handle distribution shift and regime changes

### Team Structure
- Quantitative researchers, engineers, ML specialists
- Research-first methodology
- Strong emphasis on candidates who can bridge theory and production
- PhD scholarships to cultivate future talent pipeline

### Key Takeaway for Crypto
**Data quality is non-negotiable.** If your data pipeline has gaps, your signals are noise.
Production performance constraints must be defined BEFORE research begins. The gap between
notebook demo and production reality kills most strategies.

---

## 7. Alameda Research (Anti-Patterns)

> **This section documents what NOT to do.** Every failure here is a risk management lesson.

### What Went Wrong

**1. No Internal Controls**
- No risk management system
- No audits by established firms
- No coherent record keeping
- Expenses approved via color-coded emojis
- Wire request messages auto-deleted after processing

**2. Catastrophic Conflict of Interest**
- FTX customer deposits secretly used to fund Alameda investments
- Alameda had virtually unlimited line of credit from FTX funded by customer assets
- Alameda exempt from risk mitigation measures on FTX platform
- Over half of FTX customer funds lent to Alameda after May/June 2022 losses

**3. No Governance Structure**
- No board of directors
- No independent oversight
- 130+ affiliated companies with no centralized risk management
- Single person (SBF) with unchecked authority

**4. Circular Collateral**
- Balance sheet heavily reliant on self-created tokens (FTT, SER)
- Reserves backed by internally created, theoretically valueless tokens
- When confidence broke, the circular collateral collapsed instantly

**5. No Position Limits or Drawdown Controls**
- "The tolerance level for risk knew no limits"
- All-or-nothing strategy with no solid foundation of business processes
- No early warning systems
- No scenario planning or stress testing

### The Unraveling
- CoinDesk published Alameda's balance sheet reliance on FTT for collateral
- Triggered a bank run
- $32B in value destroyed
- John Ray III (Enron bankruptcy CEO): "Never in my career have I seen such a complete failure
  of corporate controls"

### Anti-Pattern Checklist (Things That Must Never Be True)

| Anti-Pattern | Why It Kills |
|---|---|
| No independent risk management function | Trader incentives override survival |
| Collateral backed by self-created assets | Circular; collapses under any stress |
| No position limits or drawdown controls | Single bad trade can destroy everything |
| No audit trail | Can't detect problems until too late |
| Conflicts of interest between entities | Losses get hidden, compounding |
| No board or independent oversight | No one to say "stop" |
| Auto-deletion of financial records | Fraud becomes invisible |
| FOMO-driven risk-taking | Replaces analysis with emotion |

### Key Takeaway for Crypto
**Every item on the anti-pattern checklist above must have an explicit, automated control in
your system.** Position limits, drawdown controls, independent risk monitoring, and audit
trails are not optional -- they are survival requirements. In crypto especially, where
counterparty risk is elevated and regulation is thin, self-imposed governance must be
MORE rigorous than TradFi, not less.

---

## 8. Cross-Firm Synthesis: Kill Criteria & Gate Metrics

### Universal Strategy Kill Criteria

These criteria appear across multiple top firms. A strategy should be killed or paused if ANY
of these are true:

| Kill Criterion | Threshold | Source Firms |
|---|---|---|
| **Probability of Backtest Overfitting (PBO)** | > 0.5 | Industry standard |
| **Backtest Sharpe has no predictive power** | Sharpe ratio from backtest doesn't correlate with live | Quantopian study (888 strategies) |
| **No economic rationale** | Can't explain WHY the signal works | AQR, RenTec |
| **Fails out-of-sample testing** | Doesn't work on unseen data | All firms |
| **Fails cross-market testing** | Only works on one market/instrument | AQR, Two Sigma |
| **Fails walk-forward analysis** | In-sample success doesn't translate forward | Industry standard |
| **Data quality issues** | Gaps, look-ahead bias, survivorship bias | Jump Trading, RenTec |
| **Can't meet production constraints** | Works in notebook but not in real-time | Jump Trading |
| **Drawdown exceeds pod limit** | Strategy-specific max drawdown breached | Citadel |
| **Edge erosion detected** | Returns decay over time without recovery | D.E. Shaw |
| **More than 3 failed fix attempts** | Excessive debugging without resolution | General best practice |

### Multi-Layer Validation Pipeline (Industry Consensus)

```
Stage 1: HYPOTHESIS
  - Must have economic/behavioral rationale
  - Kill if: pure data-mining with no explainable mechanism

Stage 2: INITIAL BACKTEST
  - Basic in-sample test
  - Kill if: Sharpe < minimum threshold or signal doesn't beat transaction costs

Stage 3: OVERFITTING DETECTION
  - Deflated Sharpe Ratio (corrects for multiple testing)
  - PBO calculation (probability of backtest overfitting)
  - Kill if: PBO > 0.5 or Deflated Sharpe insignificant

Stage 4: OUT-OF-SAMPLE VALIDATION
  - Walk-Forward Analysis (WFA) -- industry standard
  - Combinatorial Purged Cross-Validation (CPCV) for ML models
  - Kill if: significant performance degradation vs in-sample

Stage 5: ROBUSTNESS TESTING
  - Monte Carlo simulation (9+ perturbation types)
  - Cross-market / cross-timeframe testing
  - Stress testing under regime changes
  - Kill if: fails any Monte Carlo variant or doesn't generalize

Stage 6: PAPER TRADING / FORWARD TEST
  - Minimum 50+ trades in live market conditions
  - Compare actual fills vs backtest assumptions
  - Kill if: execution slippage destroys the edge

Stage 7: SMALL CAPITAL LIVE
  - Deploy with minimal capital
  - Stress tests: restart during market hours, redeploy, clone
  - Kill if: production behavior diverges from backtest

Stage 8: FULL PRODUCTION
  - Continuous monitoring of strategy vs expected behavior
  - Kill if: returns decay significantly or risk metrics breach limits
```

### Iteration Speed Benchmarks

| Firm Type | Idea to Live | Notes |
|---|---|---|
| **HFT (Jump, RenTec)** | Days to weeks | Existing infrastructure; signals are small |
| **Systematic quant (Two Sigma, D.E. Shaw)** | 1-3 months | Full pipeline with robust validation |
| **Factor-based (AQR)** | Months to years | Academic rigor; publish-then-trade |
| **Multi-strategy pod (Citadel)** | Weeks to months | Pod-level autonomy speeds iteration |
| **Crypto target (recommended)** | 2-4 weeks | Fast enough to capture market shifts; slow enough for validation |

---

## 9. Actionable Framework for Crypto Strategy Development

### Principles Extracted from Top Firms

1. **One Integrated System** (RenTec): Build a single backtest/execution platform, not
   scattered scripts. Every strategy should be testable in the same framework.

2. **Data Infrastructure First** (Two Sigma): Invest in data quality, storage, and retrieval
   before writing strategies. 10,000+ data sources isn't the goal; pristine data IS.

3. **Version Everything** (D.E. Shaw): Versioned datasets enable reproducible research and
   eliminate look-ahead bias. You must be able to "time travel" through your data.

4. **Economic Rationale Required** (AQR): No black-box signals. Every strategy must have an
   explainable mechanism for WHY it works.

5. **Pod-Level Risk Limits** (Citadel): Each strategy gets independent drawdown limits, position
   limits, and kill switches. No strategy can threaten the whole portfolio.

6. **Production = Truth** (Jump): If it doesn't work in production constraints, it doesn't work.
   Define latency, throughput, and data quality requirements BEFORE research.

7. **Anti-Alameda Controls** (Alameda): Independent risk monitoring, audit trails, no circular
   collateral, no conflicts of interest, automated position limits.

### Recommended Gate Structure for Crypto Strategies

```
GATE 0: IDEA SCREENING (< 1 day)
  Required:
    - Written hypothesis with economic rationale
    - Identified data sources
    - Expected holding period and trade frequency
  Kill if:
    - No explainable mechanism
    - Required data doesn't exist or can't be obtained
    - Conflicts with existing portfolio strategies

GATE 1: QUICK BACKTEST (1-3 days)
  Required:
    - Basic backtest on 1+ year of data
    - Sharpe ratio calculation
    - Transaction cost estimation
  Kill if:
    - Sharpe < 1.0 (after estimated costs)
    - < 100 trades in test period
    - Edge smaller than realistic bid-ask + fees

GATE 2: STATISTICAL VALIDATION (3-5 days)
  Required:
    - Walk-Forward Analysis
    - PBO calculation
    - Deflated Sharpe Ratio
    - Out-of-sample test (minimum 6 months held out)
  Kill if:
    - PBO > 0.5
    - Deflated Sharpe not significant at p < 0.05
    - Out-of-sample Sharpe < 50% of in-sample

GATE 3: ROBUSTNESS (3-5 days)
  Required:
    - Monte Carlo perturbation tests
    - Cross-pair testing (if works on BTC/USDT, test on ETH/USDT, SOL/USDT)
    - Regime analysis (bull, bear, sideways, high-vol, low-vol)
    - Stress test: March 2020 crash, May 2021 crash, Nov 2022 FTX, etc.
  Kill if:
    - Fails on 2+ crypto pairs
    - Negative returns in any major regime
    - Drawdown > 2x expected during stress events

GATE 4: PAPER TRADING (1-2 weeks)
  Required:
    - Live paper trading with real market data
    - Minimum 50 trades
    - Execution quality metrics (slippage, fill rate)
    - Production system stress tests (restarts, redeployments)
  Kill if:
    - Live Sharpe < 60% of backtest Sharpe
    - Slippage > 50% of expected edge
    - Any production stability issues

GATE 5: SMALL CAPITAL LIVE (2-4 weeks)
  Required:
    - Deploy with 1-5% of target allocation
    - Full monitoring and alerting
    - Daily reconciliation vs expected behavior
    - Independent risk monitoring
  Kill if:
    - Drawdown exceeds strategy-specific limit
    - Returns diverge significantly from paper trading
    - Risk metrics breach any threshold

GATE 6: FULL DEPLOYMENT
  Required:
    - All previous gates passed
    - Strategy-specific risk limits configured
    - Automated kill switch active
    - Monitoring dashboards live
  Ongoing kill triggers:
    - Rolling Sharpe drops below threshold for N days
    - Max drawdown breached
    - Correlation with other live strategies exceeds limit
    - Edge decay detected (returns trending toward zero)
```

### Risk Management Controls (Non-Negotiable)

Derived from Citadel's "survive 90% drawdown" philosophy and Alameda's failures:

| Control | Implementation |
|---|---|
| **Per-strategy drawdown limit** | Auto-flatten if max DD breached |
| **Portfolio-level drawdown limit** | Auto-flatten ALL strategies if breached |
| **Position size limits** | Hard caps per strategy and per instrument |
| **Correlation monitoring** | Alert if strategy correlations exceed threshold |
| **Daily reconciliation** | Compare actual vs expected P&L |
| **Independent risk monitor** | Separate process/system from execution |
| **Audit trail** | Every trade, every parameter change, every override logged |
| **No self-referential collateral** | Never use your own tokens as collateral |
| **Counterparty limits** | Max exposure per exchange/venue |
| **Kill switch** | One-click flatten of all positions |

---

## Sources

### Renaissance Technologies
- [Renaissance Technologies - Wikipedia](https://en.wikipedia.org/wiki/Renaissance_Technologies)
- [Renaissance Technologies and The Medallion Fund - Quartr](https://quartr.com/insights/edge/renaissance-technologies-and-the-medallion-fund)
- [Renaissance Technologies Business Breakdown - Daniel Scrivner](https://www.danielscrivner.com/renaissance-technologies-business-breakdown/)
- [Renaissance Technologies: The $100B Built on Statistical Arbitrage](https://navnoorbawa.substack.com/p/renaissance-technologies-the-100)
- [Inside the Medallion Fund - Medium](https://medium.com/@trading.dude/cracking-the-code-inside-the-medallion-fund-and-jim-simons-secretive-empire-b9af08415b4f)

### Two Sigma
- [Two Sigma Investment Management](https://www.twosigma.com/businesses/investment-management/)
- [Two Sigma Quantitative Research & Data Science](https://www.twosigma.com/careers/quantitative-research-data-science/)
- [Inside Two Sigma - Institutional Investor](https://www.institutionalinvestor.com/article/2bsw4ehe37jv5y886qtxc/corner-office/inside-the-geeky-quirky-and-wildly-successful-world-of-quant-shop-two-sigma)
- [Two Sigma - TrendSpider](https://trendspider.com/learning-center/two-sigma-investments/)
- [How Two Sigma & Nubank Rewire Finance - Gradient Flow](https://gradientflow.com/how-two-sigma-nubank-rewire-finance-with-foundation-models/)

### D.E. Shaw
- [D.E. Shaw - What We Do](https://www.deshaw.com/what-we-do/investment-management)
- [D.E. Shaw: A Quantitative Powerhouse - Medium](https://medium.com/@tzjy/d-e-shaw-a-quantitative-powerhouse-in-the-multi-strategy-hedge-fund-space-1ac5b12a694a)
- [D.E. Shaw: The Quant King - Substack](https://rupakghose.substack.com/p/de-shaw-the-quant-king)
- [D.E. Shaw: Inside the Quiet Giant - Quartr](https://quartr.com/insights/company-research/de-shaw-and-co-inside-the-quiet-giant-of-quant-finance)
- [How D.E. Shaw Generated $11.1B](https://navnoorbawa.substack.com/p/how-de-shaw-generated-111-billion)

### AQR Capital
- [AQR - Understanding Factor Investing](https://funds.aqr.com/Insights/Strategies/Understanding-Factor-Investing)
- [AQR Factor Research Papers](https://www.aqr.com/Insights/Research/Tax-Aware-Investing/AQR-Factor-Research-Papers-Win-Prestigious-Academic-Awards)
- [AQR Capital - Wikipedia](https://en.wikipedia.org/wiki/AQR_Capital)
- [AQR - Fact, Fiction and Factor Investing (PDF)](https://www.aqr.com/-/media/AQR/Documents/Journal-Articles/AQRJPMQuant23FactFictionandFactorInvesting.pdf)

### Citadel / Citadel Securities
- [Citadel: Relentless Optimization - Quartr](https://quartr.com/insights/company-research/citadel-relentless-optimization-at-global-scale)
- [Citadel Global Quantitative Strategies](https://www.citadel.com/what-we-do/global-quantitative-strategies/)
- [Ken Griffin: Market Tactics Explained - LuxAlgo](https://www.luxalgo.com/blog/ken-griffin-market-tactics-explained/)
- [Citadel flagship rises 10.2% in 2025 - CNBC](https://www.cnbc.com/2026/01/02/ken-griffins-flagship-hedge-fund-at-citadel-rises-10point2percent-in-volatile-2025.html)

### Jump Trading
- [Jump Trading AI/ML](https://www.jumptrading.com/ai-ml)
- [Jump Trading - Grokipedia](https://grokipedia.com/page/Jump_Trading)
- [Jump Trading - MarketsWiki](https://marketswiki.com/wiki/Jump_Trading_LLC)

### Alameda Research / FTX
- [Lessons from the Fall of FTX - Internal Auditor](https://internalauditor.theiia.org/en/voices/2024/august/on-the-frontlines-lessons-from-the-fall-of-ftx/)
- [Six Cross-Industry Lessons from FTX - Walton College](https://walton.uark.edu/insights/posts/six-cross-industry-lessons-from-the-rise-and-fall-of-ftx.php)
- [Culture, Controls, and Corporate Governance: FTX - Walton College](https://walton.uark.edu/insights/posts/culture-controls-and-corporate-governance-lessons-from-the-ftx-fiasco.php)
- [FTX & Alameda Research - TrendSpider](https://trendspider.com/learning-center/ftx-alameda-research/)
- [FTX Scandal: Issues, Resolution, Key Takeaways - Identomat](https://www.identomat.com/blog/ftx-scandal-issues-resolution-and-key-takeaways)

### Strategy Validation & Kill Criteria
- [Hypothesis-Driven Trading Validation Framework - arXiv](https://arxiv.org/html/2512.12924v1)
- [Backtesting - Quant Beckman](https://www.quantbeckman.com/p/with-code-backtesting)
- [Quantitative Trading Summary - Headlands Technologies](https://blog.headlandstech.com/2017/08/03/quantitative-trading-summary/)
