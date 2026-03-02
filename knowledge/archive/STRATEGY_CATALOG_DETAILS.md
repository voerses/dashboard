# Strategy Catalog — Detailed Descriptions and Citations

> **Archived from:** `knowledge/STRATEGY_CATALOG.md` (trimmed 2026-03-02)
> **Use when:** Evaluating a specific strategy in depth, looking up academic citations, reviewing parameter details for non-production strategies

---

## 1. TREND FOLLOWING — Full Descriptions

### 1.1 Dual Momentum (Antonacci)

**Core logic:** Combine absolute momentum (is the asset trending up?) with relative momentum (is this asset stronger than alternatives?). Enter when both signals agree; exit to cash when absolute momentum is negative. Originally applied to equities/bonds, but the absolute momentum component is the key edge in crypto.

**Citation:** Antonacci, G. (2014). *Dual Momentum Investing: An Innovative Strategy for Higher Returns with Lower Risk*. McGraw-Hill.

**Expected profile:**
- Win rate: 38-45%
- Payoff ratio: 1.8-2.4x
- Trade frequency: 500-900 trades/year across 11-49 tokens (1H bars)
- Sharpe: 1.0-1.5 (crypto-adapted)

**Suitability for our system:** EXCELLENT -- this is our baseline strategy. S09 Optimized Trend generates +$162,898/yr (+81.4%) on all 49 tokens, +$55,408/yr on CPCV-validated tokens. Entry: daily uptrend (EMA50 + ADX>20 + 12d momentum>0) + 4H pullback to EMA20 + 1H volume burst + RSI 30-55. The multi-timeframe stack (1H:4H:Daily) is critical.

**Complexity:** Medium

**Proven in crypto:** YES -- extensively validated. Our CPCV and walk-forward tests confirm persistent alpha. Dual-validated tokens: SUI, TRX, BONK, FLOKI.

**Combination potential:** Foundation strategy. Combines well with regime filters, volatility scaling, and momentum burst overlay.

---

### 1.2 Time-Series Momentum (TSMOM)

**Core logic:** Buy assets whose own past returns over a lookback window are positive; sell/avoid those with negative past returns. Unlike cross-sectional momentum, each asset is evaluated against its own history, not relative to peers. The sign of the trailing return (or its tercile rank) determines position direction.

**Citation:** Moskowitz, T.J., Ooi, Y.H., & Pedersen, L.H. (2012). "Time Series Momentum." *Journal of Financial Economics*, 104(2), 228-250.

**Additional crypto citations:**
- Han, Y., Kang, J., & Ryu, D. (2023). "Time-series and cross-sectional momentum in cryptocurrency markets." SSRN 4675565. Found optimal lookback of 28 days with 5-day hold yields Sharpe 1.51.
- Huang, J., Sangiorgi, I., & Urquhart, A. (2024). "Volume-weighted TSMOM." SSRN 4825389. Volume-weighted variant achieves 0.94%/day, annualized Sharpe 2.17.
- Borgards, O. (2021). "Dynamic time series momentum in crypto." *Research in International Business and Finance*. Crypto has longer momentum periods than equities due to difficulty computing intrinsic value.

**Expected profile:**
- Win rate: 45-55% (higher than dual momentum because lookback is tuned)
- Payoff ratio: 1.3-1.8x
- Trade frequency: Medium (1-4 signals per token per month with 28-day lookback)
- Sharpe: 1.0-2.0 (depending on volume weighting)

**Suitability for our system:** HIGH. The 28-day lookback with 5-day hold aligns well with our 18-720 hour hold range. Volume-weighted TSMOM is particularly promising and has not been tested in our system. Key finding from research: momentum works in bull/neutral markets but is insignificant in bear markets -- regime filter is essential.

**Complexity:** Low (basic implementation), Medium (volume-weighted variant)

**Proven in crypto:** YES -- multiple academic papers with crypto-specific results. Winner concentration effect: long-only TSMOM outperforms long-short.

**Combination potential:** Can replace or augment the absolute momentum component of our dual momentum. Volume weighting adds a dimension we do not currently use for signal generation (we use vol_ratio only as a filter).

---

### 1.3 Donchian Channel Breakout

**Core logic:** Buy when price breaks above the highest high of the last N bars; sell when price breaks below the lowest low of the last N bars. The original systematic trend-following rule. Our engine already computes `donch_high` and `donch_low` indicators.

**Citation:** Donchian, R. (1960). "High Finance in Copper." *Financial Analysts Journal*. Also: Curtis Faith (2007), *Way of the Turtle*.

**Expected profile:**
- Win rate: 30-40%
- Payoff ratio: 2.0-3.5x
- Trade frequency: Low-Medium (depends on channel period; 20-bar channel yields ~2-4 signals/month/token)
- Sharpe: 0.5-1.0

**Suitability for our system:** MODERATE. Classic breakout suffers from false breakouts in range-bound markets (52% of our data period is range regime). Needs a volatility or regime filter. The 20-bar period on 1H data (20 hours) may be too short; 55-bar (the original turtle parameter, ~2.3 days) or 120-bar (~5 days) may be more appropriate.

**Complexity:** Low

---

### 1.4 Turtle Trading System (Complete)

**Core logic:** Two breakout systems run simultaneously. System 1: 20-bar breakout entry, 10-bar breakout exit. System 2: 55-bar breakout entry, 20-bar breakout exit. Position sizing via ATR-based "N" units. Add to winners (pyramiding up to 4 units). Stop at 2N (2x ATR).

**Citation:** Faith, C. (2007). *Way of the Turtle*. Also: Dennis, R. & Eckhardt, W. (1983), original Turtle Trading rules (published 2003).

**Expected profile:**
- Win rate: 30-38% (historically across commodities)
- Payoff ratio: 2.5-4.0x
- Trade frequency: Low (System 2 is very selective)
- Sharpe: 0.7-1.2 (commodities historical)
- Max drawdown: 30-50% (full turtle without modification)

**Suitability for our system:** MODERATE. The pyramiding component is interesting -- we do not currently add to winners. The 2N stop translates to our 2x ATR stop (we currently use 3x ATR, which our sweep found optimal). System 2's 55-bar breakout (~2.3 days on 1H) is well within our holding period. However, the 30-38% win rate and large drawdowns require psychological tolerance and adequate capital per position.

**Complexity:** Medium (multiple systems, pyramiding rules, unit calculations)

**Additional detail on the complete turtle system:**
- Two independent breakout systems (System 1: 20-bar, System 2: 55-bar)
- ATR-based position sizing (1% risk per "unit")
- Pyramiding: add up to 4 units as price moves in your favor (at 0.5 ATR intervals)
- Stop at 2 ATR from entry price of each unit
- Diversification rules: max 12 units in one direction across all markets

**Track record:** Original turtle traders (1983-1988) reportedly earned $100M+ in profits. Post-publication performance has degraded in traditional markets (signal is well-known) but may retain edge in crypto due to less efficient markets.

**Crypto adaptation needed:**
- 20-bar on 1H = 20 hours (may be too short; consider 55-120 bars)
- ATR in crypto is much larger than commodities; reduce unit size accordingly
- Pyramiding in crypto is high-risk due to sudden reversals; limit to 2 units
- System 2 (55-bar breakout) is more suitable for swing trading

---

### 1.5 Moving Average Strategies

#### 1.5a Golden/Death Cross (50/200 MA)

**Core logic:** Buy when the 50-period MA crosses above the 200-period MA (golden cross); sell when it crosses below (death cross). The most widely followed trend signal in all markets.

**Citation:** Brock, W., Lakonishok, J., & LeBaron, B. (1992). "Simple Technical Trading Rules and the Stochastic Properties of Stock Returns." *Journal of Finance*, 47(5), 1731-1764.

**Expected profile:**
- Win rate: 50-55%
- Payoff ratio: 1.5-2.5x
- Trade frequency: Very low (2-6 signals per year per token on daily bars)
- Sharpe: 0.4-0.8

**Suitability for our system:** LOW as primary signal (too slow -- by the time daily 50/200 MA crosses, the move is half over, confirmed by our V2 Daily Momentum failure at -$13,806/yr). USEFUL as a regime filter: only take 1H entries when the daily golden cross is in effect. We already use something similar (price > EMA50 on daily).

#### 1.5b Triple Moving Average (4/9/18 or 10/20/50)

**Core logic:** Three MAs of different periods. Enter long when short MA > medium MA > long MA (all aligned bullish). Exit when short crosses below medium. The triple alignment reduces false signals compared to dual MA crossovers.

**Citation:** Elder, A. (1993). *Trading for a Living*. Triple screen system uses three timeframes with MA alignment.

**Expected profile:**
- Win rate: 40-50%
- Payoff ratio: 1.5-2.0x
- Trade frequency: Medium
- Sharpe: 0.5-0.9

**Suitability for our system:** MODERATE. We already use EMA10/EMA20/EMA50 alignment implicitly in our multi-timeframe stack. Making this explicit as a required entry condition could reduce false signals.

#### 1.5c Adaptive Moving Average (Kaufman AMA / KAMA)

**Core logic:** The smoothing constant of the MA adapts to market conditions. In trending markets, the MA follows price closely (fast). In choppy markets, the MA smooths heavily (slow). Adaptation is based on the efficiency ratio: direction / volatility.

**Citation:** Kaufman, P. (1995). *Smarter Trading*. KAMA (Kaufman Adaptive Moving Average).

**Expected profile:**
- Win rate: 45-55%
- Payoff ratio: 1.3-1.8x
- Trade frequency: Medium
- Sharpe: 0.6-1.0

**Suitability for our system:** INTERESTING. KAMA addresses a core problem: fixed EMAs lag in fast-moving crypto trends and whipsaw in ranges. The efficiency ratio is conceptually similar to our ADX filter but applied to the MA itself.

---

### 1.6 ADX + Directional Movement System

**Core logic:** Use ADX to measure trend strength and +DI/-DI to determine direction. Enter long when +DI crosses above -DI AND ADX is rising above a threshold (20-25). Exit when ADX turns down from above 40 (trend exhaustion) or -DI crosses above +DI.

**Citation:** Wilder, J.W. (1978). *New Concepts in Technical Trading Systems*. Original ADX/DMI system.

**Expected profile:**
- Win rate: 40-48%
- Payoff ratio: 1.5-2.0x
- Trade frequency: Medium-Low
- Sharpe: 0.6-1.0

**Suitability for our system:** HIGH. ADX is already our #1 predictive indicator (IC=0.067). We currently use ADX>20 as a filter, but the full DI crossover system adds directionality. The +DI/-DI crossover on 4H bars with ADX>25 confirmation could serve as an independent strategy or an additional entry condition.

---

### 1.7 Supertrend

**Core logic:** A trend-following indicator based on ATR. Upper band = (high+low)/2 + multiplier*ATR; lower band = (high+low)/2 - multiplier*ATR. Price crossing above upper band = bullish; below lower = bearish. The band only moves in the trend direction (ratchets), creating a built-in trailing stop.

**Citation:** Pring, M. (2002). *Technical Analysis Explained*. Supertrend popularized by Olivier Seban.

**Expected profile:**
- Win rate: 40-50%
- Payoff ratio: 1.5-2.5x
- Trade frequency: Medium (depends on ATR multiplier; 3x ATR is standard)
- Sharpe: 0.6-1.0

**Suitability for our system:** MODERATE. Redundant with our existing EMA + ATR stop combination.

---

### 1.8 Channel Breakout with Volatility Filter

**Core logic:** Enter on Donchian channel breakout only when volatility is in a specific regime. Typically: only take breakouts when recent volatility is below average (compression precedes expansion). Alternatively: only take breakouts when ATR is expanding (confirming the breakout).

**Citation:** Kestner, L. (2003). *Quantitative Trading Strategies*. Also: Covel, M. (2004). *Trend Following*.

**Expected profile:**
- Win rate: 35-45%
- Payoff ratio: 2.0-3.0x
- Trade frequency: Low (volatility filter reduces signals significantly)
- Sharpe: 0.7-1.2

**Suitability for our system:** MODERATE-HIGH. Uses ATR compression (ATR < 50th percentile of 90-day range) followed by price breaking above donch_high AND ATR expanding.

---

## 2. MOMENTUM BURST / ACCELERATION — Full Descriptions

### 2.1 S11 Momentum Burst (Our Top Strategy)

**Core logic:** Identify explosive hourly moves (>3% in one bar) confirmed by trend context (ADX>20, price above EMA20). These momentum bursts signal institutional or informed buying that tends to persist. Enter immediately on the burst with a 24-bar protection window (no stop for 24 hours to let the thesis develop).

**Citation:** Internal development. Conceptually related to: Jegadeesh, N. & Titman, S. (1993). "Returns to Buying Winners and Selling Losers." *Journal of Finance*. Also: Daniel, K. & Moskowitz, T. (2016). "Momentum Crashes." *Journal of Financial Economics*.

**Expected profile:**
- Win rate: 46-47%
- Payoff ratio: 1.81-1.94x
- Trade frequency: 600-2,400 trades/year across 11-49 tokens
- Annual PnL: +$62,705/yr (CPCV 11 tokens), +$170,240/yr (all 49 tokens)
- Sharpe: >1.5 (estimated)

**Suitability for our system:** BEST -- this is our #1 strategy. Validated via CPCV (PBO < 40%) and walk-forward. Dual-validated tokens: PENGU, SUI, AVAX, BONK, FLOKI, ZRO.

---

### 2.2 Momentum Ignition Detection

**Core logic:** Detect when a rapid price move is caused by deliberate "momentum ignition" -- a pattern where an aggressive participant pushes price quickly to trigger stops and momentum-following algos, then rides the resulting cascade.

**Citation:** SEC (2010). "Concept Release on Equity Market Structure." Also: Cartea, A. & Jaimungal, S. (2015). "Algorithmic Trading of Co-Integrated Assets."

**Expected profile:**
- Win rate: 50-60% (if correctly identified)
- Payoff ratio: 1.5-2.5x
- Trade frequency: Low (ignition events are episodic)

**Suitability for our system:** LOW-MODERATE. Requires tick-level or 1-minute data. Our 1H bars are too coarse.

---

### 2.3 Volume-Confirmed Breakouts

**Core logic:** Only trade breakouts when accompanied by volume significantly above average (2x+ 20-bar volume average). Volume confirmation reduces false breakouts.

**Citation:** Granville, J. (1963). *Granville's New Key to Stock Market Profits*. Also: Karpoff, J. (1987). "The Relation Between Price Changes and Trading Volume." *JFQA*.

**Expected profile:**
- Win rate: 45-55%
- Payoff ratio: 1.5-2.0x
- Sharpe: 0.7-1.1

**Suitability for our system:** HIGH. We already use `vol_ratio` and our indicator analysis confirms vol_ratio_hi + BB_pct_low yields +0.99% mean return with 58.4% win rate.

---

### 2.4 Relative Strength Momentum (vs BTC or vs Universe)

**Core logic:** Rank tokens by performance relative to BTC over a lookback period. Go long tokens with strongest relative strength.

**Citation:** Levy, R. (1967). "Relative Strength as a Criterion for Investment Selection." *Journal of Finance*. Also: O'Neil, W. (1988). *How to Make Money in Stocks*.

**Suitability for our system:** MODERATE. Cross-sectional momentum is weaker than time-series momentum in crypto (Han et al. 2023). Best used for token selection rather than timing.

---

### 2.5 Momentum Crash Protection

**Core logic:** Scale momentum exposure inversely to realized volatility. When realized vol spikes, position sizes automatically shrink, reducing crash exposure without binary on/off rules.

**Citation:** Barroso, P. & Santa-Clara, P. (2015). "Momentum has its Moments." *Journal of Financial Economics*, 116(1), 111-120.

**Expected profile:**
- Improves Sharpe by 0.3-0.5 on momentum strategies

**Suitability for our system:** HIGH. Position_size = base_size * (target_vol / realized_vol_20d). Our drawdown circuit breakers are a crude version of this.

---

### 2.6 Momentum with Volatility Scaling

**Core logic:** Scale the size of momentum bets inversely proportional to recent realized volatility. Continuous (not just during stress).

**Citation:** Moreira, A. & Muir, T. (2017). "Volatility-Managed Portfolios." *Journal of Finance*, 72(4), 1611-1644.

**Expected profile:**
- Risk-adjusted return improvement: 20-40% higher Sharpe
- Sizing changes, not entry/exit

**Suitability for our system:** HIGH. Replace discrete regime tiers with continuous vol-scaling.

---

## 3. MEAN REVERSION — Full Descriptions

### 3.1 Bollinger Band Mean Reversion (with Regime Filter)

**Core logic:** Buy when price touches or penetrates the lower Bollinger Band; sell at the middle band. Regime filter restricts entries to non-trending markets (ADX < 20 or range regime).

**Citation:** Bollinger, J. (2002). *Bollinger on Bollinger Bands*. Also: Lo, A. & MacKinlay, A.C. (1990). "When Are Contrarian Profits Due to Stock Market Overreaction?" *Review of Financial Studies*.

**Our backtest results:** NEGATIVE. All mean reversion strategies from our 70-strategy sweep lose money (-$25K to -$185K/yr). Even with regime filtering, RSI<35, BB_pct<0.15, MACD turning, and taker>0.52.

**Why it fails in our system:**
1. Hold period mismatch -- BB MR works at 1-day, not 18-720 hour holds
2. Stop-loss incompatibility -- MR needs wide/no stops (Beluska & Vojtko 2024)
3. Crypto regime persistence -- dips often start sustained trend changes
4. Fat left tails -- opposite of our desired positive-skew profile

### 3.2 RSI Extremes with Confirmation

**Core logic:** Buy when RSI < 30 (oversold) with confirmation (MACD turning positive, volume spike). Sell when RSI > 70 with bearish confirmation.

**Citation:** Wilder, J.W. (1978). *New Concepts in Technical Trading Systems*. Also: Connors, L. & Alvarez, C. (2009). *Short Term Trading Strategies That Work*.

**Our findings:** RSI is nearly useless standalone (IC=0.004). However, RSI_low + MACD_pos is one of our strongest 2-indicator combos (+1.37% mean return, 58.3% win rate at 1-day horizon). Key insight: RSI works differently per regime (high RSI predicts MORE upside in uptrends, more downside in downtrends).

### 3.3 Statistical Arbitrage / Pairs Trading in Crypto

**Citation:** Gatev, E. et al. (2006). "Pairs Trading." *Review of Financial Studies*. Crypto: Palazzi, R. (2025). "Pairs Trading in Bullish Crypto Market." *Journal of Futures Markets*.

**Suitability:** LOW. Requires shorting (we are long-only). Crypto correlations are unstable.

### 3.4 Ornstein-Uhlenbeck (OU) Based Strategies

**Core logic:** Model price as an OU mean-reverting process. Trade deviations from long-term mean theta.

**Citation:** Uhlenbeck, G.E. & Ornstein, L.S. (1930). Also: Avellaneda, M. & Lee, J.H. (2010). "Statistical Arbitrage in the US Equities Market."

**Suitability:** LOW. Most crypto tokens fail OU model test -- they are momentum-driven.

### 3.5 Mean Reversion Research Context

**Key papers:**
- Lo & MacKinlay (1990): contrarian profits from cross-autocorrelation, not pure overreaction
- Poterba & Summers (1988): weak MR evidence at 3-5 year horizons only
- Beluska & Vojtko (2024): BTC bounces from minima (MR) but trends from maxima (momentum). Stop-losses hurt MR.

**Synthesis:** MR valid at 1-day or shorter; valid after sharp crashes (>15-20% drops); invalid at swing timeframes; invalid during bear markets; invalid with tight stops.

---

## 4. VOLATILITY STRATEGIES — Full Descriptions

### 4.1 Volatility Breakout (Compression to Expansion)

**Core logic:** Identify periods of unusually low volatility (compression) and enter when volatility expands. Compression detected by: BB bandwidth < Nth percentile, ATR < Nth percentile, or Keltner inside Bollinger (TTM Squeeze).

**Citation:** Bollinger, J. (2002). TTM Squeeze: Carter, J. (2012). *Mastering the Trade*.

**Our backtest results:** FAILED (-$4,220/yr). BB squeeze-to-breakout unreliable in crypto post-ETF. Squeeze too frequent (160/760 days), false breakouts ~55%, payoff < 1.0x.

### 4.2 Volatility Compression Squeeze (TTM Squeeze)

**Core logic:** BB inside Keltner Channels = squeeze. BB expanding back outside KC = squeeze fires.

**Citation:** Carter, J. (2012). *Mastering the Trade*. Keltner, C. (1960).

**Suitability:** LOW. Same problems as vol_breakout. Weak academic support.

### 4.3 Vol Targeting / Risk Parity

**Core logic:** Maintain constant portfolio volatility by adjusting exposure inversely to realized vol.

**Citation:** Asness, C.S., Frazzini, A., & Pedersen, L.H. (2012). "Leverage Aversion and Risk Parity." *Financial Analysts Journal*. Also: Ilmanen, A. (2011). *Expected Returns*.

**Suitability:** HIGH. We already implement crude version. Refinement: target specific portfolio vol (e.g., 25% annualized).

### 4.4 Straddle-Like Strategies Using Spot

**Core logic:** Pre-event, buy both breakout directions. Cancel unfilled when one triggers.

**Citation:** Natenberg, S. (1994). *Option Volatility and Pricing*.

**Suitability:** LOW. Requires simultaneous long/short capability.

### 4.5 Volatility Risk Premium Harvesting

**Core logic:** Implied vol systematically exceeds realized vol. Sell volatility to harvest premium. In crypto: perp funding rates, options put skew.

**Citation:** Carr, P. & Wu, L. (2009). "Variance Risk Premiums." *Review of Financial Studies*. Liu, Y. & Tsyvinski, A. (2021). *Review of Financial Studies*.

**Suitability:** LOW for direct implementation (requires options/perp infrastructure).

---

## 5. REGIME-ADAPTIVE — Full Descriptions

### 5.1 HMM-Based Regime Switching

**Core logic:** Use HMM to classify market state into 2-4 regimes based on returns, volatility, volume.

**Citation:** Hamilton, J.D. (1989). *Econometrica*. Crypto: Castellano & D'Ecclesia (2025). *Digital Finance*.

**Suitability:** HIGH. Already implemented in `regime_detector.py`. 2-3 state HMMs effective for crypto.

### 5.2 Adaptive Asset Allocation

**Core logic:** Dynamically adjust portfolio weights based on momentum, correlation, and volatility.

**Citation:** Butler, A. et al. (2012). *ReSolve Asset Management*. Keller & Keuning (2015). PAA.

**Suitability:** MODERATE-HIGH. Dynamic weighting among 6-49 tokens based on recent performance.

### 5.3 Dynamic Strategy Selection by Regime

**Core logic:** Maintain portfolio of strategies, allocate capital to regime-appropriate strategy.

**Citation:** Baz, J. et al. (2015). AQR Working Paper. Kritzman et al. (2012). *Financial Analysts Journal*.

**Suitability:** HIGH. Currently S09/S11 at static 60/40. Dynamic allocation would improve risk-adjusted returns.

### 5.4 Crisis Alpha Strategies

**Core logic:** Strategies that profit during market crises. In crypto: rapid de-risking, short-term momentum reversal detection, funding rate extremes as capitulation signals.

**Citation:** Bhansali, V. (2014). *Tail Risk Hedging*. Dao, T.L. et al. (2016). AQR.

**Suitability:** MODERATE. We have drawdown circuit breakers. True crisis alpha in spot-only is limited to rapid exit speed.

### 5.5 Regime-Conditional Momentum

**Core logic:** Run momentum in uptrends (RSI high -> more upside), switch to MR in ranges (intraday_kurtosis/skew best predictors), cash in downtrends.

**Citation:** Asness (2014). AQR. Henkel et al. (2011). *Journal of Financial Economics*.

**Suitability:** VERY HIGH. This is what our system does implicitly. Making it explicit could capture the 52% range-regime time.

---

## 6. CROSS-SECTIONAL — Full Descriptions

### 6.1 Cross-Sectional Momentum

**Core logic:** Rank all tokens by past returns. Buy top decile, sell bottom.

**Citation:** Jegadeesh & Titman (1993). *Journal of Finance*. Crypto: Han et al. (2023). SSRN 4675565.

**Suitability:** LOW. "Weak and unreliable" in crypto (Han 2023). Requires shorting.

### 6.2 Factor Investing in Crypto

**Core logic:** Identify systematic factors: size, momentum, reversal, liquidity, volatility.

**Citation:** Fama & French (1993). *JFE*. Liu & Tsyvinski (2021). Borri & Shakhnov (2022).

**Factors:** Momentum strongest. Size/liquidity premiums consumed by costs. Reversal noisy. Low-vol underperforms in bull, outperforms in bear.

**Suitability:** MODERATE. Momentum factor reinforces our approach. Others impractical at our scale.

### 6.3 Sector Rotation

**Core logic:** Capital rotates between crypto sectors (L1, L2, DeFi, meme, infra) in sequence during market phases.

**Suitability:** MODERATE. Add sector tags, rotate based on 2-week relative strength vs BTC.

### 6.4 Relative Strength Ranking

**Core logic:** Rank tokens by price performance relative to BTC over 14-28 days. Apply S09/S11 only to top-ranked.

**Citation:** O'Neil (1988). Levy (1967).

**Suitability:** MODERATE. Dynamic RS ranking as two-stage filter on top of CPCV validation.

### 6.5 Mean-Variance Optimization with Crypto Constraints

**Core logic:** Markowitz optimization adapted for crypto: fat tails, unstable correlations, long-only constraints.

**Citation:** Markowitz (1952). Brauneis & Mestel (2019). *Finance Research Letters*.

**Suitability:** MODERATE. Beyond 5-8 tokens, crypto MVO converges to equal-weight due to high correlations.

---

## 7. ORDER FLOW / MICROSTRUCTURE — Full Descriptions

### 7.1 VPIN-Based Entry Timing

**Core logic:** VPIN measures order flow toxicity. High VPIN = informed traders active.

**Citation:** Easley, D. et al. (2012). *Review of Financial Studies*. Crypto: Easley et al. (2024). SSRN 4814346.

**Our results:** VPIN as filter HURT performance. Reduced trades without improving quality. Data quality issue: many tokens show 0.5 (no real data) early.

**Suitability:** LOW with current data. POTENTIALLY HIGH with 1-minute computed VPIN. Most independent indicator (max corr 0.31).

### 7.2 Funding Rate Signals

**Core logic:** Extreme positive funding (>0.03%/8hr) = overleveraged longs (bearish). Extreme negative (<-0.01%) = overleveraged shorts (bullish).

**Citation:** Inan, E. (2025). SSRN 5576424.

**Suitability:** MODERATE. Derivatives signal for spot system. Best as tertiary signal.

### 7.3 Liquidation Cascade Prediction

**Core logic:** Predict levels where cascading liquidations will trigger. Trade in cascade direction.

**Citation:** Brunnermeier & Pedersen (2009). *Review of Financial Studies*.

**Suitability:** LOW-MODERATE. Requires real-time OI heatmap data not in our pipeline.

### 7.4 Order Book Imbalance

**Core logic:** Bid vs ask depth imbalance predicts short-term direction.

**Citation:** Cont, R. et al. (2014). *Journal of Financial Econometrics*.

**Suitability:** LOW. Signal horizon minutes-to-hours, too short for 18-720hr holds.

### 7.5 Taker Buy Ratio Divergence

**Core logic:** Taker buy ratio diverging from price = exhaustion signal.

**Citation:** Anastasopoulos & Gradojevic (2025). EFMA 2025.

**Our findings:** Taker > 0.55 -> +0.42% 1-day return (53.3% WR). + positive daily return: +1.18% (52.1% WR). Strongest at 1-day, decays to noise by 5-day.

**Suitability:** MODERATE as confirmation. Already partially in S11.

---

## 8. MULTI-FACTOR — Full Descriptions

### 8.1 Signal Combination Methods

- **Equal weight:** Simple, robust, hard to overfit. DeMiguel et al. (2009).
- **IC-weighted:** Weight by information coefficient. Grinold & Kahn (2000).
- **ML-based:** GBT/neural nets. Gu et al. (2020). 86-92% accuracy in literature but overfitting risk.

### 8.2 Ensemble Strategies

**Core logic:** Run multiple independent strategies, combine signals/allocations.

**Citation:** Breiman, L. (1996). "Bagging Predictors." *Machine Learning*.

**Suitability:** HIGH. S09 + S11 + V3 as ensemble is natural path.

### 8.3 Kelly Criterion for Multi-Strategy

**Core logic:** Multi-asset Kelly considering correlations. Use fractional Kelly.

**Citation:** Kelly (1956). Thorp (2006). MacLean et al. (2011).

**Our approach:** Quarter-Kelly for T2/T3, half-Kelly for T1. 1-2% risk per trade.

### 8.4 Avoiding P-Hacking

**Methods:** Bonferroni (p < 0.05/N), BHY (FDR control), White's Reality Check (bootstrap), Hansen's SPA test.

**Citation:** Harvey et al. (2016): t-stat > 3.0 for new factors. White (2000). Hansen (2005).

**Suitability:** CRITICAL. We tested 70 strategies. CPCV is primary defense; formal multiple testing corrections add rigor.

---

## 9. PROVEN CLASSICS — Full Descriptions

### 9.2 Aberration System (Fitschen)

**Core logic:** Trend-following using BB as channels. Buy above upper BB close, sell below lower BB.

**Citation:** Fitschen, K. (2013). *Building Reliable Trading Systems*.

**Suitability:** MODERATE. BB-based strategies underperform EMA/ADX in our crypto tests.

### 9.3 Larry Connors' Strategies

**RSI-2:** RSI(2) < 10 when above 200-day MA. Buy, exit when RSI(2) > 70. 1-4 day holds, 70-80% WR in equities.

**ConnorsRSI:** Composite of RSI(3) + Up/Down streak RSI + ROC percentile rank.

**Citation:** Connors & Alvarez (2009). *Short Term Trading Strategies That Work*.

**Suitability:** LOW. RSI has IC=0.004 in crypto. MR loses at swing timeframes.

### 9.5 Clenow's "Following the Trend"

**Core logic:** Diversified trend-following across many markets. EMA(50) for direction, ATR for sizing.

**Citation:** Clenow, A. (2013). *Following the Trend*.

**Suitability:** HIGH. Our S09 is a more complex version. 49 tokens = 49 "markets" for diversification.

### 9.6 AQR TSMOM Factor

**Core logic:** Vol-scaled TSMOM across asset classes. Long if 12-month excess return positive, short if negative.

**Citation:** Moskowitz et al. (2012). Hurst et al. (2017). AQR.

**Suitability:** HIGH. Institutional gold standard. Our S09/S11 are shorter-lookback variants.

### 9.7 Asness' "Value and Momentum Everywhere"

**Core logic:** Both value and momentum exist across asset classes. Combine for negative correlation benefit.

**Citation:** Asness, Moskowitz, & Pedersen (2013). *Journal of Finance*.

**Suitability:** MODERATE. Momentum proven. Value hard to define in crypto (NVT, MVRV, price/MA200 as proxies).

---

## 10. CUTTING-EDGE — Full Descriptions

### 10.1 Deep Reinforcement Learning for Execution

**Citation:** Mnih et al. (2015). *Nature*. Deng et al. (2017). *IEEE Trans. Neural Networks*.

**Suitability:** LOW-MODERATE. Reward shaping tricky, sim-to-real gap large, catastrophic forgetting. Better for execution optimization than signal generation.

### 10.2 Transformer-Based Price Prediction

**Citation:** Vaswani et al. (2017). Lim et al. (2021). Wu et al. (2023).

**Suitability:** LOW. Low signal-to-noise, non-stationarity, insufficient training data for 49 tokens with ~2 years of 1H data.

### 10.3 Graph Neural Networks for Token Relationships

**Citation:** Kipf & Welling (2017). Chen et al. (2022).

**Suitability:** LOW-MODERATE. Could capture BTC -> ETH -> alt rotation but graph structure is unstable.

### 10.4 Causal Inference for Strategy Validation (Lopez de Prado)

**Citation:** Lopez de Prado (2018). *Advances in Financial Machine Learning*.

**Key techniques:**
1. Triple barrier labeling (our existing framework)
2. Meta-labeling (predict when S09/S11 signals will win) -- UNEXPLORED
3. Fractionally differentiated features (stationary preserving memory) -- UNEXPLORED
4. CUSUM structural break filter (trade only after regime changes) -- UNEXPLORED

**Suitability:** HIGH. We already use CPCV (Ch. 12). Meta-labeling, frac diff, CUSUM directly implementable.

### 10.5 ML Alpha That Survives Costs

**Citation:** Gu et al. (2020). Freyberger et al. (2020).

**Key finding:** The most important ML features are the SAME ones we already use: past returns (momentum), volatility, volume, trend strength (ADX). Exotic features (NLP sentiment, social media) rarely survive costs.

**Implication:** Our feature set is likely near-optimal. Marginal return to model complexity is low.

---

## COMPLETE CITATION INDEX

### Foundational Papers

1. Antonacci, G. (2014). *Dual Momentum Investing*. McGraw-Hill.
2. Moskowitz, T.J., Ooi, Y.H., & Pedersen, L.H. (2012). "Time Series Momentum." *Journal of Financial Economics*, 104(2), 228-250.
3. Jegadeesh, N. & Titman, S. (1993). "Returns to Buying Winners and Selling Losers." *Journal of Finance*, 48(1), 65-91.
4. Fama, E. & French, K. (1993). "Common Risk Factors in the Returns on Stocks and Bonds." *Journal of Financial Economics*, 33(1), 3-56.
5. Lo, A. & MacKinlay, A.C. (1990). "When Are Contrarian Profits Due to Stock Market Overreaction?" *Review of Financial Studies*, 3(2), 175-205.
6. Poterba, J. & Summers, L. (1988). "Mean Reversion in Stock Prices." *Journal of Financial Economics*, 22(1), 27-59.
7. Asness, C.S., Moskowitz, T.J., & Pedersen, L.H. (2013). "Value and Momentum Everywhere." *Journal of Finance*, 68(3), 929-985.
8. Hamilton, J.D. (1989). "A New Approach to the Economic Analysis of Nonstationary Time Series." *Econometrica*, 57(2), 357-384.
9. Kelly, J.L. (1956). "A New Interpretation of Information Rate." *Bell System Technical Journal*.
10. Markowitz, H. (1952). "Portfolio Selection." *Journal of Finance*, 7(1), 77-91.

### Momentum and Trend Following

11. Barroso, P. & Santa-Clara, P. (2015). "Momentum has its Moments." *Journal of Financial Economics*, 116(1), 111-120.
12. Moreira, A. & Muir, T. (2017). "Volatility-Managed Portfolios." *Journal of Finance*, 72(4), 1611-1644.
13. Daniel, K. & Moskowitz, T. (2016). "Momentum Crashes." *Journal of Financial Economics*, 122(2), 221-247.
14. Hurst, B., Ooi, Y.H., & Pedersen, L.H. (2017). "A Century of Evidence on Trend-Following Investing." *AQR*.
15. Brock, W., Lakonishok, J., & LeBaron, B. (1992). "Simple Technical Trading Rules and the Stochastic Properties of Stock Returns." *Journal of Finance*, 47(5), 1731-1764.
16. Levy, R. (1967). "Relative Strength as a Criterion for Investment Selection." *Journal of Finance*.
17. Clenow, A. (2013). *Following the Trend: Diversified Managed Futures Trading*.
18. Covel, M. (2004). *Trend Following*. Prentice Hall.
19. Faith, C. (2007). *Way of the Turtle*. McGraw-Hill.

### Crypto-Specific Research

20. Han, Y., Kang, J., & Ryu, D. (2023). "Time-series and cross-sectional momentum in cryptocurrency markets." SSRN 4675565.
21. Huang, J., Sangiorgi, I., & Urquhart, A. (2024). "Volume-weighted TSMOM." SSRN 4825389.
22. Borgards, O. (2021). "Dynamic time series momentum in crypto." *Research in International Business and Finance*.
23. Beluska, M. & Vojtko, T. (2024). "Revisiting Trend-Following and Mean-Reversion in Bitcoin." SSRN 4955617.
24. Liu, Y. & Tsyvinski, A. (2021). "Risks and Returns of Cryptocurrency." *Review of Financial Studies*.
25. Borri, N. & Shakhnov, K. (2022). "The Cross-Section of Cryptocurrency Returns."
26. Palazzi, R. (2025). "Pairs Trading in Bullish Crypto Market." *Journal of Futures Markets*.
27. Castellano, R. & D'Ecclesia, R.L. (2025). "Regime Switching Forecasting for Cryptocurrencies." *Digital Finance*.
28. Inan, E. (2025). "Predictability of Funding Rates." SSRN 5576424.

### Microstructure and Order Flow

29. Easley, D., Lopez de Prado, M., & O'Hara, M. (2012). "Flow Toxicity and Liquidity in a High-Frequency World." *Review of Financial Studies*, 25(5), 1457-1493.
30. Easley, D. et al. (2024). "Microstructure and Market Dynamics in Crypto." SSRN 4814346.
31. Cont, R., Kukanov, A., & Stoikov, S. (2014). "The Price Impact of Order Book Events." *Journal of Financial Econometrics*, 12(1), 47-88.
32. Anastasopoulos, A. & Gradojevic, N. (2025). "Order Flow and Cryptocurrency Returns." EFMA 2025.
33. Karpoff, J. (1987). "The Relation Between Price Changes and Trading Volume." *JFQA*.

### Volatility and Risk

34. Asness, C.S., Frazzini, A., & Pedersen, L.H. (2012). "Leverage Aversion and Risk Parity." *Financial Analysts Journal*, 68(1), 47-59.
35. Carr, P. & Wu, L. (2009). "Variance Risk Premiums." *Review of Financial Studies*, 22(3), 1311-1341.
36. Ilmanen, A. (2011). *Expected Returns*. Wiley.
37. Brunnermeier, M. & Pedersen, L.H. (2009). "Market Liquidity and Funding Liquidity." *Review of Financial Studies*.

### Machine Learning in Finance

38. Gu, S., Kelly, B., & Xiu, D. (2020). "Empirical Asset Pricing via Machine Learning." *Review of Financial Studies*, 33(5), 2223-2273.
39. Lopez de Prado, M. (2018). *Advances in Financial Machine Learning*. Wiley.
40. Harvey, C.R., Liu, Y., & Zhu, H. (2016). "...and the Cross-Section of Expected Returns." *Review of Financial Studies*, 29(1), 5-68.
41. White, H. (2000). "A Reality Check for Data Snooping." *Econometrica*, 68(5), 1097-1126.
42. Hansen, P.R. (2005). "A Test for Superior Predictive Ability." *JBES*, 23(4), 365-380.

### Portfolio Construction

43. DeMiguel, V., Garlappi, L., & Uppal, R. (2009). "Optimal Versus Naive Diversification." *Review of Financial Studies*.
44. Grinold, R. & Kahn, R. (2000). *Active Portfolio Management*. McGraw-Hill.
45. Butler, A. et al. (2012). "Adaptive Asset Allocation: A Primer." *ReSolve Asset Management*.
46. Keller, W. & Keuning, T. (2015). "Protective Asset Allocation (PAA)."
47. MacLean, L., Thorp, E., & Ziemba, W. (2011). *The Kelly Capital Growth Investment Criterion*. World Scientific.

### Regime Analysis

48. Kritzman, M., Page, S., & Turkington, D. (2012). "Regime Shifts: Implications for Dynamic Strategies." *Financial Analysts Journal*.
49. Baz, J. et al. (2015). "Dissecting Investment Strategies in the Cross Section and Time Series." AQR Working Paper.
50. Henkel, S.J., Martin, J.S., & Nardari, F. (2011). "Time-Varying Short-Horizon Predictability." *Journal of Financial Economics*.
51. Bhansali, V. (2014). *Tail Risk Hedging*. Bloomberg Press.
52. Dao, T.L. et al. (2016). "Tail Protection for Long Investors." AQR.

### Classic Strategy References

53. Wilder, J.W. (1978). *New Concepts in Technical Trading Systems*.
54. Bollinger, J. (2002). *Bollinger on Bollinger Bands*. McGraw-Hill.
55. Elder, A. (1993). *Trading for a Living*. Wiley.
56. Kaufman, P. (1995). *Smarter Trading*. McGraw-Hill.
57. Connors, L. & Alvarez, C. (2009). *Short Term Trading Strategies That Work*.
58. O'Neil, W. (1988). *How to Make Money in Stocks*.
59. Carter, J. (2012). *Mastering the Trade*. McGraw-Hill.
60. Kestner, L. (2003). *Quantitative Trading Strategies*. McGraw-Hill.
61. Fitschen, K. (2013). *Building Reliable Trading Systems*. Wiley.
62. Natenberg, S. (1994). *Option Volatility and Pricing*. McGraw-Hill.

### Deep Learning and Emerging Methods

63. Vaswani, A. et al. (2017). "Attention is All You Need." *NeurIPS*.
64. Lim, B. et al. (2021). "Temporal Fusion Transformers." *International Journal of Forecasting*.
65. Kipf, T.N. & Welling, M. (2017). "Semi-Supervised Classification with Graph Convolutional Networks." *ICLR*.
66. Mnih, V. et al. (2015). "Human-level control through deep RL." *Nature*.
67. Deng, Y. et al. (2017). "Deep Direct RL for Financial Signal Representation." *IEEE Trans. Neural Networks*.
