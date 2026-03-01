# Quantitative Crypto Trading Strategies: Research Report

**Compiled: February 2026**
**Target: $200K swing trading portfolio, 1H-Daily timeframes**

---

## Table of Contents

1. [Strategies That Work in Crypto (2023-2026)](#1-strategies-that-work-in-crypto-2023-2026)
2. [Technical Indicator Research for Crypto](#2-technical-indicator-research-for-crypto)
3. [Derivatives & On-Chain Signals](#3-derivatives--on-chain-signals)
4. [Cross-Asset Correlation Signals](#4-cross-asset-correlation-signals)
5. [Position Management](#5-position-management)
6. [Portfolio Construction](#6-portfolio-construction)
7. [What Top Crypto Quant Funds Do](#7-what-top-crypto-quant-funds-do)
8. [Actionable Framework for a $200K Swing Portfolio](#8-actionable-framework-for-a-200k-swing-portfolio)

---

## 1. Strategies That Work in Crypto (2023-2026)

### 1.1 Time-Series Momentum (TSMOM)

**Verdict: The single strongest systematic edge in crypto.**

Academic research overwhelmingly confirms time-series momentum in crypto markets. Key findings:

- **Optimal lookback/hold**: A strategy buying when the 28-day lookback return falls in the top third of historical returns, holding for 5 days, yields a **Sharpe ratio of 1.51** vs. 0.84 for buy-and-hold ([Han, Kang & Ryu, 2023 - SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4675565)).
- **Volume-weighted TSMOM**: Using volume-weighted market returns generates **0.94% per day** with an annualized Sharpe of 2.17 ([Huang, Sangiorgi & Urquhart, 2024 - SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4825389)).
- **Crypto has longer and stronger momentum periods** than equities because intrinsic value is harder to compute, meaning trends persist longer ([Borgards, 2021 - ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S1062940821000590)).
- **Winner concentration**: Momentum effects are concentrated among winners -- long-only momentum works better than long-short.
- **Market-state dependent**: Momentum works significantly in bull/neutral markets but is insignificant in bear markets. This means you need a regime filter.
- **Intraday TSMOM** performs especially well during falling markets -- it avoids drawdowns by detecting short-term trend reversals.

**Actionable parameters for 1H-Daily swing trading:**
- Lookback: 10-28 days (shorter tends to work better in crypto)
- Holding period: 3-7 days
- Entry: Buy when rolling return is in top tercile of its own history
- Filter: Only take momentum signals when a regime indicator confirms non-bear conditions

### 1.2 Cross-Sectional Momentum

**Verdict: Weak and unreliable compared to TSMOM.**

- Evidence of cross-sectional momentum is weak in crypto ([Han et al., 2023](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4675565)).
- Short-term cross-sectional momentum struggled on small-cap coins.
- Cross-sectional reversal (buying losers, selling winners) works better among the smallest coins, consistent with a liquidity premium.
- Not recommended as a primary strategy for a $200K portfolio due to execution challenges across many small-cap tokens.

### 1.3 Mean Reversion

**Verdict: Works after sharp drawdowns; fails in prolonged bears. No tight stops.**

Key research from [Beluska & Vojtko (2024)](https://quantpedia.com/revisiting-trend-following-and-mean-reversion-strategies-in-bitcoin/):

- BTC tends to **bounce from local minima** (mean-revert) but **trend from local maxima** (momentum continues).
- The MIN strategy (buy at N-day minimum) shows mean reversion behavior -- prices recover after sharp drops.
- **Win rate is high** (60-70% of trades) but individual losers can be large.
- **Stop-losses hurt mean reversion performance** in almost all backtests. Wide stops (3-4x ATR) or no stops at all performed best.
- **Fails during prolonged bear markets** (e.g., 2022) where prices deviate from averages for months.

**When to use mean reversion:**
- After sharp single-day or multi-day drops (>15-20%) in otherwise trending markets
- On higher-liquidity tokens (BTC, ETH, SOL) where recovery is more reliable
- With a regime filter that confirms the broader trend is not bearish

**When mean reversion fails:**
- During bear markets or structural regime changes
- On low-liquidity altcoins where drops can be permanent
- With tight stop-losses that cut trades before the reversion materializes

### 1.4 Trend Following with Fat-Tail Capture

**Verdict: The bread-and-butter strategy. Positive skew by design.**

Trend following in crypto captures the positive fat tails that are unique to the asset class:

- Crypto exhibits **"strong non-normal characteristics, large tail dependencies, and heavy distributions"** -- exactly what trend following is designed to exploit.
- The profit distribution of trend following has **fat right tails** (large occasional wins), while mean reversion has fat left tails (large occasional losses) ([The Hedge Fund Journal](https://thehedgefundjournal.com/making-fat-right-tails-fatter-with-trend-following/)).
- **Shorter lookback periods (10-20 days)** outperform longer ones in crypto ([QuantPedia](https://quantpedia.com/trend-following-and-mean-reversion-in-bitcoin/)).
- A multi-timeframe approach (higher timeframe trend confirmation + lower timeframe entry) improves robustness without optimization.

**Optimal implementation:**
- EMA crossover (e.g., 10/30 EMA) + ADX regime filter (ADX > 20 for trend confirmation)
- ATR-based position sizing (volatility normalization)
- Trailing stop at 2-3x ATR
- Multi-timeframe: daily trend direction confirmed on weekly, entry on 4H/1H
- Trade 10-20 liquid tokens to increase the chance of catching at least one big trend

### 1.5 Volatility Timing/Harvesting

**Verdict: Emerging edge, especially with regime conditioning.**

- Bitcoin's volatility regime has structurally changed post-ETF (2024+). Annualized realized vol dropped from 150%+ to more moderate levels, with spikes and drawdowns milder than previous cycles ([Fidelity Digital Assets](https://www.fidelitydigitalassets.com/research-and-insights/bitcoin-price-phases-navigating-bitcoins-volatility-trends)).
- ML-based volatility forecasting (LightGBM, XGBoost, LSTM) with sentiment data outperforms traditional HAR models ([Springer, 2024](https://link.springer.com/article/10.1007/s10690-024-09510-6)).
- **Volatility-harvesting DCA**: Increase position size by 50-100% when RSI(14) < 30 or price drops > 25% in a week.
- **Volatility compression** often precedes major breakouts -- low-vol periods (ATR < 50th percentile of 90-day range) followed by expansion offer trend-following entries.

**Actionable approach:**
- Track 30-day realized volatility relative to its 1-year percentile
- Low vol (< 25th percentile): Prepare for breakout, widen position limits
- High vol (> 75th percentile): Reduce position sizes, tighten risk
- Use VIX-analog metrics (Bitcoin Volatility Index) as a sentiment overlay

### 1.6 Microstructure Signals

**Verdict: Powerful but require exchange data. Best as confirmation, not primary signal.**

From [Easley et al.](https://stoye.economics.cornell.edu/docs/Easley_ssrn-4814346.pdf) and related research:

- **VPIN (Volume-Synchronized Probability of Informed Trading)**: Average VPIN in crypto is 0.45-0.47 vs. 0.22-0.23 in traditional futures -- crypto has 2x the informed trading toxicity.
- VPIN has **the best out-of-sample performance** among all microstructure measures for predicting future price dynamics.
- **VPIN significantly predicts future price jumps** in Bitcoin, with positive serial correlation in both VPIN and jump size.
- **Amihud illiquidity measure**: Together with VPIN, provides the highest feature importance for predicting market dynamics.
- Order flow (net buy/sell volume) has both permanent and temporary effects on crypto returns ([Anastasopoulos & Gradojevic, 2025](https://www.efmaefm.org/0EFMAMEETINGS/EFMA%20ANNUAL%20MEETINGS/2025-Greece/papers/OrderFlowpaper.pdf)).

**Practical use for swing trading:**
- Monitor VPIN spikes as early warning of imminent volatility
- Use Amihud illiquidity to avoid tokens that are too thin to trade
- Track order flow imbalance on your exchange for entry timing
- High VPIN + low liquidity = stay out; high VPIN + high liquidity = potential informed move

---

## 2. Technical Indicator Research for Crypto

### 2.1 Highest-Predictive-Power Indicators

From a 2024 arXiv study using XGBoost with chi-squared feature selection on Bitcoin ([arXiv:2410.06935](https://arxiv.org/html/2410.06935v1)):

**Top 8 features ranked by predictive importance:**
1. RSI(30)
2. MACD
3. Momentum(30)
4. Stochastic %D
5. Stochastic %D(0)
6. Stochastic %K(200)
7. Stochastic %K(30)
8. RSI(14)

Key insight: **Momentum and volatility indicators** consistently have the highest information content across multiple studies.

### 2.2 RSI in Crypto

- RSI is **the single most important technical feature** in ML models for crypto prediction.
- RSI(30) -- a longer-period RSI -- outperforms RSI(14) in crypto, likely because crypto trends last longer.
- RSI is more accurate than MACD when used alone for crypto signal generation ([ResearchGate, 2024](https://www.researchgate.net/publication/377921778_a-comparative-study-between-rsi-and-macd-to-predict-opportunities-in-cryptocurrency-market-from-2020-to-2022_1)).
- Best used for **overbought/oversold extremes** rather than crossover signals.
- RSI below 30 in an uptrend = high-probability mean-reversion entry.
- RSI above 70 in a downtrend = short/exit signal.
- RSI divergence (price makes new high, RSI does not) is a useful exhaustion signal.

### 2.3 MACD in Crypto

- Ranks #2 in feature importance behind RSI.
- Best for **trend direction and momentum shifts**, not precise timing.
- MACD signal-line crossovers are too frequent on sub-daily timeframes -- use on daily or higher.
- MACD histogram divergence from price is more reliable than crossovers.
- Combine with ADX: Only take MACD signals when ADX > 20 (trending market).

### 2.4 Bollinger Bands in Crypto

- Capture the **volatility dimension** that RSI and MACD miss.
- Mean-reversion signals (price touching lower band in uptrend) have high win rates.
- Bollinger Band squeeze (bandwidth < 20th percentile of 90-day range) precedes major moves.
- Best used as a **volatility regime detector** rather than a standalone signal generator.
- A 2025 study found the Bollinger-based mean reversion strategy was effective when volatility-adjusted parameters were used.

### 2.5 Multi-Indicator Confluence

Academic consensus: **combining RSI + MACD + Bollinger Bands** outperforms any single indicator, achieving 86-92%+ directional accuracy in ML models. The real power is **confluence** -- waiting for simultaneous signals:

- MACD bullish crossover + RSI confirming above 50 + price above Bollinger middle band = strong long signal
- RSI < 30 + price at lower Bollinger Band + MACD histogram turning up = mean reversion entry
- Bollinger squeeze + RSI neutral + MACD flat = prepare for breakout trade

---

## 3. Derivatives & On-Chain Signals

### 3.1 Funding Rate Signals

Perpetual futures funding rates are among the most crypto-native alpha signals:

- **Normal funding**: 0.005-0.01% per 8 hours. This is neutral.
- **High positive funding (> 0.03%)**: Market overheated with leveraged longs. Historically precedes corrections or increased volatility ([QuantJourney](https://quantjourney.substack.com/p/funding-rates-in-crypto-the-hidden)).
- **Negative funding**: Short-dominant. Contrarian bullish in many contexts.
- **Extremely negative funding**: Capitulation signal. Combined with other bottoming indicators, this is a high-conviction long entry.

**Trading signals:**
- Funding > 0.05% sustained for 3+ periods: Reduce longs, prepare for pullback
- Funding < -0.01% sustained: Look for long entries on other confirmations
- Funding rate divergence from price: Price rising + funding dropping = weakening conviction, potential top
- Funding rate can be a **carry trade**: Short funding-positive tokens, long funding-negative tokens (basis/carry arbitrage)

### 3.2 Open Interest Divergence

- **OI rising + price rising**: Healthy trend, new money entering. Hold positions.
- **OI rising + price falling**: Shorts building aggressively. Watch for short squeeze or continuation.
- **OI falling + price rising**: Short squeeze / short covering rally. Less sustainable.
- **OI falling + price falling**: Long liquidation cascade. Look for capitulation bottom.
- **OI at record highs**: Elevated liquidation risk regardless of direction. Reduce position sizes.
- CME Bitcoin OI hit record ~45,000 contracts in Nov 2024, with basis exceeding 20% -- unsustainable institutional premium that subsequently corrected.

### 3.3 On-Chain Signals

**MVRV Ratio (Market Value to Realized Value):**
- MVRV > 3.5: Historically marks cycle tops. Reduce exposure aggressively.
- MVRV < 1.0: Historically marks cycle bottoms. Maximum accumulation zone.
- Currently most useful for BTC and ETH on multi-week to multi-month timeframes.

**NVT Ratio (Network Value to Transactions):**
- High NVT = price outpacing network usage = overvalued.
- Low NVT = undervalued relative to network activity.
- Best used as a medium-term (weekly/monthly) valuation filter.

**Active Addresses:**
- Rising active addresses = growing adoption = bullish.
- Sharp drops signal falling interest before price follows.
- 60-75% accuracy for trend prediction when combined with other on-chain metrics.

**Whale Movements:**
- Whale accumulation during retail panic = high-conviction long signal.
- Whale distribution during retail euphoria = distribution/top signal.
- Track large wallet flows to exchanges (selling pressure) vs. from exchanges (accumulation).
- Tools: Glassnode, CryptoQuant, Nansen.

**Accuracy caveat:** On-chain signals achieve 60-75% trend prediction accuracy. They are best used as medium-term regime/valuation overlays, not intraday signals.

---

## 4. Cross-Asset Correlation Signals

### 4.1 BTC Dominance (BTC.D)

BTC.D tracks Bitcoin's share of total crypto market cap. As of early 2026, BTC.D has been in the 55-65% range.

**Regime signals:**
- **BTC.D rising**: Flight to quality within crypto. Favor BTC over alts.
- **BTC.D falling**: Altseason forming. Capital rotating into ETH/alts.
- **BTC.D peaking and rolling over**: Classic altseason trigger (typically happens mid-to-late bull cycle).

**Capital rotation sequence in bull markets:** BTC first -> ETH -> large-cap alts -> mid-cap alts -> small-cap speculation -> blow-off top.

### 4.2 ETH/BTC Ratio

- **ETH/BTC rising**: Greater risk appetite, DeFi/alt momentum building.
- **ETH/BTC falling**: Flight to BTC safety, risk reduction.
- **ETH/BTC breakout above multi-month resistance**: Often precedes broad altseason.
- Maintained 0.89 correlation coefficient with BTC in 2025, but institutional flows created significant deviation periods.
- A breakout in ETH/BTC is often a leading indicator for allocating into smaller L1s and DeFi tokens.

### 4.3 DXY (US Dollar Index)

- BTC-DXY correlation: **-0.4 to -0.8** over the past 5 years. Inverse but not constant.
- DXY had correlation of **-0.65** with Bitcoin in Q1 2024.
- **DXY peaks often coincide with BTC bottoms** and vice versa.
- DXY structural weakness (breaking below key MAs) is a macro tailwind for all crypto.
- The correlation breaks during extreme geopolitical crises (both dollar and BTC can rise as safe havens).

**Cross-asset trading framework:**

| BTC.D | DXY | ETH/BTC | Interpretation | Portfolio Action |
|-------|-----|---------|---------------|------------------|
| Rising | Rising | Falling | Max risk-off | Heavy BTC, minimal alts |
| Rising | Falling | Falling | BTC-led bull, early cycle | Overweight BTC, start adding ETH |
| Falling | Falling | Rising | Altseason | Rotate into alts, reduce BTC % |
| Falling | Rising | Falling | Bear market / confusion | Reduce total exposure, hold stables |

---

## 5. Position Management

### 5.1 Stop-Loss Placement

**ATR-based stops are the standard for crypto:**

| Trading Style | ATR Multiplier | ATR Period | Typical Stop Width (BTC) |
|--------------|----------------|------------|--------------------------|
| Intraday (1H) | 1.0-1.5x | 14 | 1-3% |
| Swing (Daily) | 2.0-3.0x | 14 | 3-8% |
| Position (Weekly) | 3.0-4.0x | 14 | 8-15% |

Key findings:
- A **2x ATR stop-loss reduces maximum drawdown by 32%** vs. static stops.
- For **mean reversion**: Wide stops (3-4x ATR) or no stops at all. Tight stops kill mean reversion.
- For **trend following**: 2-3x ATR trailing stops work best.
- **Percentage stops** (e.g., fixed 5%) are inferior to ATR stops because they do not adapt to volatility regimes.
- **Time stops**: Exit after N bars if the trade has not moved in your favor. Useful for momentum entries that should work quickly. 5-7 day time stop for swing trades.

### 5.2 Trailing Stops

- **ATR trailing stop**: Highest price minus (ATR x multiplier). Adjusts as trade moves in your favor.
- **2x ATR multiplier** is the most commonly recommended for swing trading ([LuxAlgo](https://www.luxalgo.com/blog/5-atr-stop-loss-strategies-for-risk-control/)).
- **Chandelier Exit**: A popular trailing stop variant based on ATR from highest high.
- For crypto specifically: Start with initial stop at 3x ATR, move to 2x ATR once trade is 1x ATR in profit. This gives room for initial volatility then tightens.

### 5.3 Kelly Criterion in Practice

The Kelly formula: **K% = W - [(1-W) / R]**
Where W = win rate, R = average win/average loss ratio.

**Critical practical adjustments for crypto:**

| Kelly Fraction | Growth | Drawdown | Recommendation |
|---------------|--------|----------|----------------|
| Full Kelly | Maximum | Extreme (50%+) | Never use in crypto |
| Half Kelly (0.5x) | ~75% of max | ~50% less DD | Upper bound for aggressive |
| Quarter Kelly (0.25x) | ~56% of max | Manageable | **Recommended for crypto** |

- **Half Kelly captures ~75% of optimal growth with ~50% less drawdown** ([Enlightened Stock Trading](https://enlightenedstocktrading.com/kelly-criterion/)).
- Most professional traders use **Quarter to Half Kelly**.
- For a $200K portfolio: If Kelly says risk 8%, use 2-4% (quarter to half Kelly).
- Liquidity constraints in crypto mean large Kelly positions may be impractical in low-liquidity tokens.
- Default to **1-2% risk per trade** (fixed fractional) if Kelly estimates are unreliable due to limited backtest data.

### 5.4 Position Sizing Formula

**Position Size = (Account Risk $) / (Stop Distance $)**

Example for $200K account:
- Risk per trade: 1% = $2,000
- Entry: $100,000 BTC
- Stop: 3% below entry = $97,000
- Stop distance: $3,000
- Position size: $2,000 / $3,000 = 0.667 BTC = $66,700 notional

This means you can have approximately 3 concurrent positions at 1% risk each with the position sizes varying by each token's volatility.

### 5.5 Correlation-Based Position Sizing

- When holding multiple positions, reduce size if tokens are highly correlated.
- BTC/ETH correlation: ~0.89 in 2025. Treat as ~1.6 positions, not 2.
- Rule of thumb: If correlation > 0.7, treat the combined position as (1 + corr) / 2 x the sum of individual risks.
- In altseason (falling BTC.D), alt correlations often converge toward 1.0 -- reduce individual sizes.
- Use a maximum portfolio heat of 5-6% (total risk across all open positions) rather than per-position limits alone.

---

## 6. Portfolio Construction

### 6.1 Optimal Number of Tokens

Research consensus for a $200K swing portfolio:

- **Risk parity models work best with fewer tokens** -- beyond 5-8 tokens, performance converges to equal-weight due to high crypto correlations ([WSEAS, 2024](https://wseas.com/journals/bae/2024/b165107-011(2024).pdf)).
- **Diminishing diversification returns**: Adding more tokens beyond a core set yields marginal benefit because crypto correlations within the asset class are high.
- **Optimal crypto-only allocation**: ~70% BTC, ~30% ETH achieves the highest Sharpe ratio ([VanEck](https://www.vaneck.com/us/en/blogs/digital-assets/matthew-sigel-optimal-crypto-allocation-for-portfolios/)).

**Recommended for $200K swing portfolio:**
- **Core (60-70%):** BTC + ETH (position-traded on weekly/daily signals)
- **Satellite (20-30%):** 3-5 liquid large/mid-cap alts (swing-traded on daily/4H signals)
- **Tactical (0-10%):** 1-2 high-conviction momentum plays (shorter-term, higher risk)
- **Cash reserve:** 10-20% in stablecoins for mean-reversion entries and drawdown buying
- Total active positions: **5-8 tokens maximum at any time**

### 6.2 Weighting Schemes

| Method | Pros | Cons | Best For |
|--------|------|------|----------|
| **Equal Weight** | Simple, no estimation error | Ignores risk differences | Small # of similar tokens |
| **Risk Parity** | Equalizes risk contribution | Can overweight low-vol (stables) | Core/satellite allocation |
| **Momentum-Weighted** | Tilts toward winners | Whipsaws in choppy markets | Altcoin satellite allocation |
| **Min Variance** | Lowest portfolio vol | Over-concentrates in BTC/ETH | Conservative approach |
| **Entropy-Based** | Distribution-free, heavy-tail friendly | Complex implementation | Advanced portfolios |

**Recommendation for $200K portfolio:** Use a hybrid approach:
- **Core:** Risk parity between BTC and ETH (equalizes risk contribution)
- **Satellite:** Momentum-weighted among 3-5 alts (overweight recent winners)
- **Overall:** Max single-position weight of 35%, min of 5%

### 6.3 Rebalancing Frequency

Research findings:
- For **pure crypto portfolios**: Shorter rebalancing intervals outperform, with daily showing the highest Sharpe ratio.
- For **Bitcoin in traditional portfolios**: Yearly rebalancing (letting winners run) outperformed quarterly and monthly.
- **Threshold-based rebalancing** (rebalance when allocation drifts >15%) outperforms time-based approaches ([Zignaly](https://zignaly.com/crypto-trading/risk-management/cryptocurrency-portfolio-rebalancing)).
- The **rebalancing premium** in crypto (from systematic selling winners/buying losers) adds 2-5% annually.
- **78% of rebalanced portfolios outperformed buy-and-hold** during the 2018 crash.

**Recommendation:**
- **Core (BTC/ETH):** Threshold rebalance at 15% drift (roughly monthly in normal markets)
- **Satellite (alts):** Weekly review, momentum-based rotation (replace weakest with strongest)
- **Risk management rebalance:** Immediate if any position exceeds 40% of portfolio or total drawdown exceeds 15%

### 6.4 Regime-Conditional Allocation

The most powerful portfolio enhancement. Adjust allocation based on detected market regime:

| Regime | Detection Method | BTC/ETH Core | Alt Satellite | Cash/Stables |
|--------|-----------------|--------------|---------------|-------------|
| **Bull (trending up)** | Price > 200 DMA, ADX > 25, BTC.D stable/falling | 50-60% | 30-40% | 0-10% |
| **Early Bull** | Price crossing above 200 DMA, BTC.D rising | 60-70% | 10-20% | 10-20% |
| **Range/Neutral** | Price near 200 DMA, ADX < 20 | 30-40% | 10-20% | 40-50% |
| **Bear (trending down)** | Price < 200 DMA, death cross, negative MVRV | 10-20% | 0-5% | 75-90% |
| **Capitulation** | MVRV < 1, extreme negative funding, RSI < 20 | 30-40% (accumulate) | 5-10% | 50-60% |

Regime detection methods from research:
- **200-day MA**: Simple and effective. Price above = bull, below = bear.
- **Hidden Markov Models**: More sophisticated, 2-3 state models ([Digital Finance, 2025](https://link.springer.com/article/10.1007/s42521-024-00123-2)).
- **Random Forest classifiers**: Using market breadth, volume, volatility features.
- **CryptoQuant Bull Score**: Composite of 10 on-chain metrics; when 8/10 flash red, bear confirmed.
- Coinbase research: Traditional 20% threshold does not work for crypto. Use volatility-adjusted definitions (1 standard deviation ~60% move).

---

## 7. What Top Crypto Quant Funds Do

### 7.1 Pythagoras Investment Management

**Strategy mix and 2024 performance:**
- **Alpha Long Biased**: BTC base position + two uncorrelated strategies. **204% return** in 2024 (vs. BTC's 121%). AUM grew to $230M+ ([CoinDesk](https://www.coindesk.com/markets/2025/01/07/this-crypto-fund-blew-past-bitcoins-121-price-gain-in-2024)).
- **Quant Long Short**: Market-neutral systematic trading. **30% return** in 2024.
- **Absolute Return**: Dollar-neutral strategies. **41.7% return** in 2024.
- **Arbitrage**: Cross-exchange and basis arbitrage. **18% return** in 2024.
- All strategies are **systematic, non-discretionary, and automated**.
- They specialize in **market-neutral and dollar-neutral strategies** that consistently outperform.

### 7.2 Wintermute

- Primary strategy: **High-frequency market making** across 350+ spot and derivatives pairs.
- Record $2.24B daily trading volume in November 2024 ([Finance Magnates](https://www.financemagnates.com/cryptocurrency/crypto-market-maker-wintermute-sees-record-224-billion-daily-trading-volume/)).
- OTC volumes surged 313% YoY in 2024 ([CryptoSlate](https://cryptoslate.com/wintermute-reports-240-surge-in-institutional-crypto-trading-via-otc-in-2024/)).
- Holding period: **Milliseconds to minutes** (HFT). Not applicable to swing trading.
- Profit source: Bid-ask spread capture, inventory management, cross-venue arbitrage.

### 7.3 Jump Trading / Jump Crypto

- **Ultra-low latency HFT** and market making.
- Profits from micro-price fluctuations.
- Holding period: **Sub-second to minutes**.
- Also runs longer-term systematic strategies but details are proprietary.

### 7.4 Common Patterns Across Top Funds

| Aspect | Market Makers (Wintermute, Jump) | Directional Quants (Pythagoras) | Relevant to $200K Swing |
|--------|--------------------------------|-------------------------------|------------------------|
| **Holding period** | Ms to minutes | Days to weeks | Days to weeks |
| **Signal type** | Order flow, microstructure | Momentum, mean reversion, basis | Momentum, mean reversion |
| **Edge source** | Speed, infrastructure | Statistical signals, regime | Statistical signals, regime |
| **Risk management** | Inventory limits | Volatility scaling, stop-losses | Volatility scaling, stop-losses |
| **Replicable?** | No (requires $10M+ infra) | Partially yes | Core of this framework |

**Key takeaway:** For a $200K swing portfolio, Pythagoras-style systematic momentum + mean reversion with regime conditioning is the most replicable institutional approach. Market making requires infrastructure that is not feasible at this scale.

---

## 8. Actionable Framework for a $200K Swing Portfolio

### 8.1 Strategy Allocation

| Strategy | Portfolio Weight | Timeframe | Expected Edge |
|----------|-----------------|-----------|---------------|
| **Trend Following (TSMOM)** | 40% ($80K) | Daily/4H | Sharpe 1.0-1.5 |
| **Mean Reversion** | 20% ($40K) | Daily/4H | Win rate 60-70% |
| **Momentum Rotation** | 20% ($40K) | Weekly/Daily | Alpha from sector rotation |
| **Cash/Stables** | 20% ($40K) | N/A | Drawdown buffer + dry powder |

### 8.2 Signal Stack (Priority Order)

**Primary signals (must-have for entry):**
1. Regime confirmation (bull/neutral via 200 DMA + ADX)
2. Trend direction (EMA crossover or TSMOM lookback signal)
3. RSI confirmation (not overbought for longs, not oversold for shorts)

**Secondary signals (improve conviction):**
4. MACD momentum alignment
5. Volume confirmation (volume-weighted momentum)
6. Bollinger Band position (volatility context)

**Tertiary signals (edge refinement):**
7. Funding rate (contrarian at extremes)
8. Open interest regime (OI divergence)
9. BTC.D / ETH-BTC trend (rotation timing)
10. DXY macro overlay

### 8.3 Risk Management Rules

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| **Risk per trade** | 1-2% of equity ($2-4K) | Quarter-Kelly conservative |
| **Max portfolio heat** | 6% total risk | Correlation-adjusted |
| **Max single position** | 33% of equity | Concentration limit |
| **Stop-loss (trend)** | 2-3x ATR trailing | Captures fat tails |
| **Stop-loss (mean rev)** | 3-4x ATR or time-based | Wide stops for MR |
| **Time stop** | 7 bars (daily) if no move | Cut dead-weight trades |
| **Max correlated positions** | 2 at > 0.7 correlation | Avoid hidden concentration |
| **Drawdown circuit breaker** | -15% equity: half all sizes | Preserve capital |
| **Drawdown circuit breaker** | -25% equity: go to cash | Survive to trade again |

### 8.4 Regime-Conditional Sizing

| Regime | Total Equity Deployed | Position Count | Per-Trade Risk |
|--------|----------------------|---------------|----------------|
| **Strong Bull** | 80-90% | 5-8 | 2% |
| **Moderate Bull** | 60-70% | 4-6 | 1.5% |
| **Neutral/Range** | 30-40% | 2-3 | 1% |
| **Bear** | 10-20% | 1-2 | 0.5% |
| **Capitulation** | 40-50% (accumulate) | 2-3 | 1% |

### 8.5 Token Universe

**Core (always in universe):** BTC, ETH
**Large-cap satellite (rotate among top 3-5):** SOL, BNB, XRP, ADA, AVAX, LINK, DOT
**Selection criteria:** Top 20 by market cap, daily volume > $100M, available on 3+ major exchanges

### 8.6 Execution Checklist (Per Trade)

1. Confirm regime (bull/neutral/bear) via 200 DMA + ADX
2. Check BTC.D trend and ETH/BTC for rotation context
3. Identify signal (TSMOM trigger, mean reversion setup, or momentum rotation candidate)
4. Confirm with RSI + MACD alignment
5. Check funding rate and OI for derivatives context
6. Calculate position size: Risk$ / (ATR x multiplier)
7. Set initial stop (ATR-based)
8. Set trailing stop mechanism
9. Set time stop (7 daily bars)
10. Log trade with thesis, entry, stop, target, and regime context

---

## Key Research Sources

### Academic Papers
- [Quantitative Alpha in Crypto Markets - SSRN (Mann, 2025)](https://papers.ssrn.com/sol3/Delivery.cfm/5225612.pdf?abstractid=5225612&mirid=1)
- [Pairs Trading in Bullish Crypto Market - Journal of Futures Markets (Palazzi, 2025)](https://onlinelibrary.wiley.com/doi/full/10.1002/fut.70018)
- [Time-Series and Cross-Sectional Momentum in Crypto - SSRN (Han, Kang & Ryu, 2023)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4675565)
- [Volume-Weighted TSMOM - SSRN (Huang, Sangiorgi & Urquhart, 2024)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4825389)
- [Dynamic Time Series Momentum - ScienceDirect (Borgards, 2021)](https://www.sciencedirect.com/science/article/abs/pii/S1062940821000590)
- [Revisiting Trend-following and Mean-Reversion in Bitcoin - SSRN (Beluska & Vojtko, 2024)](https://papers.ssrn.com/sol3/Delivery.cfm/4955617.pdf?abstractid=4955617&mirid=1)
- [Microstructure and Market Dynamics in Crypto - Cornell (Easley et al.)](https://stoye.economics.cornell.edu/docs/Easley_ssrn-4814346.pdf)
- [Bitcoin Intraday Time-Series Momentum - University of Reading](https://centaur.reading.ac.uk/100181/3/21Sep2021Bitcoin%20Intraday%20Time-Series%20Momentum.R2.pdf)
- [Order Flow and Cryptocurrency Returns - EFMA (Anastasopoulos & Gradojevic, 2025)](https://www.efmaefm.org/0EFMAMEETINGS/EFMA%20ANNUAL%20MEETINGS/2025-Greece/papers/OrderFlowpaper.pdf)
- [Regime Switching Forecasting for Cryptocurrencies - Springer (2025)](https://link.springer.com/article/10.1007/s42521-024-00123-2)
- [Crypto Portfolio Construction - arXiv (2024)](https://arxiv.org/html/2412.02654v1)
- [Cryptocurrency Volatility Review - ScienceDirect (2024)](https://www.sciencedirect.com/science/article/abs/pii/S0275531924002654)
- [Predictability of Funding Rates - SSRN (Inan, 2025)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5576424)
- [Bitcoin Wild Moves: Order Flow Toxicity - ScienceDirect (2025)](https://www.sciencedirect.com/science/article/pii/S0275531925004192)
- [Predicting Bitcoin with Technical Indicators - arXiv (2024)](https://arxiv.org/html/2410.06935v1)

### Industry & Practitioner Sources
- [Pythagoras 204% Return in 2024 - CoinDesk](https://www.coindesk.com/markets/2025/01/07/this-crypto-fund-blew-past-bitcoins-121-price-gain-in-2024)
- [Wintermute 2024 Review & 2025 Outlook](https://www.wintermute.com/insights/market-color/reports/wintermute-otc-2024-in-review-2025-outlook)
- [Wintermute Record Trading Volume - Finance Magnates](https://www.financemagnates.com/cryptocurrency/crypto-market-maker-wintermute-sees-record-224-billion-daily-trading-volume/)
- [Crypto Quant Strategy Index - 1Token](https://blog.1token.tech/crypto-quant-strategy-index-vii-oct-2025/)
- [Bitcoin Price Phases - Fidelity Digital Assets](https://www.fidelitydigitalassets.com/research-and-insights/bitcoin-price-phases-navigating-bitcoins-volatility-trends)
- [VanEck Optimal Crypto Allocation](https://www.vaneck.com/us/en/blogs/digital-assets/matthew-sigel-optimal-crypto-allocation-for-portfolios/)
- [Grayscale: Role of Crypto in a Portfolio](https://research.grayscale.com/reports/the-role-of-crypto-in-a-portfolio)
- [QuantPedia: Trend-following and Mean-Reversion in Bitcoin](https://quantpedia.com/revisiting-trend-following-and-mean-reversion-strategies-in-bitcoin/)
- [QuantPedia: Cryptocurrency Trading Research](https://quantpedia.com/cryptocurrency-trading-research/)
- [Coinbase Institutional: Defining Crypto Bear Markets (April 2025)](https://www.coinbase.com/institutional/research-insights/research/monthly-outlook/monthly-outlook-apr-2025)
- [Crypto Rebalancing Research - Crypto Research Report](https://cryptoresearch.report/crypto-research/optimal-rebalancing-strategy/)
- [ATR Stop-Loss Strategies - LuxAlgo](https://www.luxalgo.com/blog/5-atr-stop-loss-strategies-for-risk-control/)
- [Kelly Criterion for Trading - Enlightened Stock Trading](https://enlightenedstocktrading.com/kelly-criterion/)
