# Exhaustive Catalog of Systematic Trading Strategies

**Compiled: February 2026**
**Context: Crypto swing trading system, $200K capital, 1H timeframe, 18-720 hour holds, 6-49 tokens**

This catalog organizes every known systematic trading strategy relevant to our system, with academic citations, expected profiles, and suitability assessments. Strategies are annotated with our own backtest findings where available.

---

## Table of Contents

1. [Trend Following](#1-trend-following)
2. [Momentum Burst / Acceleration](#2-momentum-burst--acceleration)
3. [Mean Reversion](#3-mean-reversion)
4. [Volatility Strategies](#4-volatility-strategies)
5. [Regime-Adaptive Strategies](#5-regime-adaptive-strategies)
6. [Cross-Sectional / Relative Value](#6-cross-sectional--relative-value)
7. [Order Flow / Microstructure Strategies](#7-order-flow--microstructure-strategies)
8. [Multi-Factor Combination](#8-multi-factor-combination)
9. [Proven Classic Strategies](#9-proven-classic-strategies)
10. [Cutting-Edge / Recent Research](#10-cutting-edge--recent-research)

---

## 1. TREND FOLLOWING

Trend following is the single strongest systematic edge in crypto markets. Our backtests confirm this: every profitable strategy we have found is fundamentally trend-following. ADX is the #1 predictive indicator (IC=0.067, increasing with horizon), and crypto's regime persistence makes trends longer and stronger than in equities (Borgards 2021).

---

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
- Payoff ratio: 2.0-3.5x (relies on large wins from sustained trends)
- Trade frequency: Low-Medium (depends on channel period; 20-bar channel yields ~2-4 signals/month/token)
- Sharpe: 0.5-1.0

**Suitability for our system:** MODERATE. Classic breakout suffers from false breakouts in range-bound markets (52% of our data period is range regime). Needs a volatility or regime filter. The 20-bar period on 1H data (20 hours) may be too short; 55-bar (the original turtle parameter, ~2.3 days) or 120-bar (~5 days) may be more appropriate.

**Complexity:** Low

**Proven in crypto:** Partially. The principle works (trend following is validated), but raw Donchian without filters underperforms our multi-timeframe approach.

**Combination potential:** Good as a breakout confirmation signal within our existing framework. When price exceeds `donch_high(120)` AND our ADX/EMA filters confirm, the signal quality should improve.

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

**Proven in crypto:** Not specifically validated in academic crypto literature. The underlying principle (trend breakout + ATR sizing + trailing stop) is exactly what our profitable strategies do.

**Combination potential:** The pyramiding logic could be layered onto S09/S11 -- add to winning positions when the trend strengthens (ADX increasing, price making new highs). This is unexplored in our system.

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

**Complexity:** Low

**Proven in crypto:** Mixed. The signal itself generates poor alpha due to lag, but as a filter it is standard practice.

#### 1.5b Triple Moving Average (4/9/18 or 10/20/50)

**Core logic:** Three MAs of different periods. Enter long when short MA > medium MA > long MA (all aligned bullish). Exit when short crosses below medium. The triple alignment reduces false signals compared to dual MA crossovers.

**Citation:** Elder, A. (1993). *Trading for a Living*. Triple screen system uses three timeframes with MA alignment.

**Expected profile:**
- Win rate: 40-50%
- Payoff ratio: 1.5-2.0x
- Trade frequency: Medium
- Sharpe: 0.5-0.9

**Suitability for our system:** MODERATE. We already use EMA10/EMA20/EMA50 alignment implicitly in our multi-timeframe stack. Making this explicit as a required entry condition could reduce false signals. The 10/20/50 EMA stack on 1H bars gives alignment periods of 10h/20h/50h, which is appropriate for our hold times.

**Complexity:** Low

#### 1.5c Adaptive Moving Average (Kaufman AMA / KAMA)

**Core logic:** The smoothing constant of the MA adapts to market conditions. In trending markets, the MA follows price closely (fast). In choppy markets, the MA smooths heavily (slow). Adaptation is based on the efficiency ratio: direction / volatility.

**Citation:** Kaufman, P. (1995). *Smarter Trading*. KAMA (Kaufman Adaptive Moving Average).

**Expected profile:**
- Win rate: 45-55%
- Payoff ratio: 1.3-1.8x
- Trade frequency: Medium
- Sharpe: 0.6-1.0

**Suitability for our system:** INTERESTING. KAMA addresses a core problem in our system: fixed EMAs lag in fast-moving crypto trends and whipsaw in ranges. Implementing KAMA as a replacement for EMA20 in our entry logic could improve signal quality. The efficiency ratio is conceptually similar to our ADX filter but applied to the MA itself.

**Complexity:** Medium (KAMA computation is straightforward, but tuning the fast/slow constants adds parameters)

**Proven in crypto:** Limited academic validation in crypto specifically, but the adaptive principle is well-suited to crypto's regime-switching behavior.

**Combination potential:** Replace fixed EMAs with KAMA in S09/S11 entry logic. Alternatively, use the KAMA slope as a trend filter.

---

### 1.6 ADX + Directional Movement System

**Core logic:** Use ADX to measure trend strength and +DI/-DI to determine direction. Enter long when +DI crosses above -DI AND ADX is rising above a threshold (20-25). Exit when ADX turns down from above 40 (trend exhaustion) or -DI crosses above +DI.

**Citation:** Wilder, J.W. (1978). *New Concepts in Technical Trading Systems*. Original ADX/DMI system.

**Expected profile:**
- Win rate: 40-48%
- Payoff ratio: 1.5-2.0x
- Trade frequency: Medium-Low
- Sharpe: 0.6-1.0

**Suitability for our system:** HIGH. ADX is already our #1 predictive indicator (IC=0.067). We currently use ADX>20 as a filter, but the full DI crossover system adds directionality. The +DI/-DI crossover on 4H bars (our intermediate timeframe) with ADX>25 confirmation could serve as an independent strategy or an additional entry condition.

**Complexity:** Low (indicators already computed in our engine: `adx`, `plus_di`, `minus_di`)

**Proven in crypto:** Yes -- ADX's predictive power in crypto is well-documented in our own indicator analysis and in academic literature.

**Combination potential:** Excellent. The DI crossover adds a dimension our current strategies do not explicitly use (we check ADX level but not DI direction). Adding +DI > -DI as an entry condition to S09/S11 could improve win rate.

---

### 1.7 Supertrend

**Core logic:** A trend-following indicator based on ATR. Upper band = (high+low)/2 + multiplier*ATR; lower band = (high+low)/2 - multiplier*ATR. Price crossing above upper band = bullish; below lower = bearish. The band only moves in the trend direction (ratchets), creating a built-in trailing stop.

**Citation:** Pring, M. (2002). *Technical Analysis Explained*. Supertrend popularized by Olivier Seban.

**Expected profile:**
- Win rate: 40-50%
- Payoff ratio: 1.5-2.5x
- Trade frequency: Medium (depends on ATR multiplier; 3x ATR is standard)
- Sharpe: 0.6-1.0

**Suitability for our system:** MODERATE. Supertrend is essentially an ATR-based trend line with a trailing stop built in. Our system already uses ATR trailing stops. The incremental value would be using Supertrend as a trend direction filter (only long when price is above Supertrend line) rather than as a primary entry signal. Redundant with our existing EMA + ATR stop combination.

**Complexity:** Low

**Proven in crypto:** Popular among retail crypto traders; limited academic validation. Functionally equivalent to our existing ATR-based stops.

**Combination potential:** Could replace the EMA-based trend direction with Supertrend-based direction in our entry logic. Unlikely to add significant alpha given overlap with existing ATR usage.

---

### 1.8 Channel Breakout with Volatility Filter

**Core logic:** Enter on Donchian channel breakout (N-bar high) only when volatility is in a specific regime. Typically: only take breakouts when recent volatility is below average (compression precedes expansion). Alternatively: only take breakouts when ATR is expanding (confirming the breakout).

**Citation:** Kestner, L. (2003). *Quantitative Trading Strategies*. Also: Covel, M. (2004). *Trend Following*.

**Expected profile:**
- Win rate: 35-45%
- Payoff ratio: 2.0-3.0x
- Trade frequency: Low (volatility filter reduces signals significantly)
- Sharpe: 0.7-1.2

**Suitability for our system:** MODERATE-HIGH. This addresses a known problem: our vol_breakout strategy failed (-$4,220/yr) because BB squeeze-to-breakout was unreliable. However, the failure may be in the BB squeeze definition, not the concept. Using ATR compression (ATR < 50th percentile of 90-day range) followed by price breaking above donch_high AND ATR expanding could yield better results than our BB-based approach.

**Complexity:** Low-Medium

**Proven in crypto:** The concept is sound but our specific implementation failed. Needs reformulation with ATR-based compression rather than BB squeeze.

**Combination potential:** Could serve as a specialized entry condition within S09/S11: only take trend entries when coming out of volatility compression.

---

## 2. MOMENTUM BURST / ACCELERATION

Momentum burst strategies identify sudden, explosive moves that signal the beginning of a sustained trend. These exploit the empirical finding that large short-term moves in crypto tend to continue rather than revert (at swing timeframes).

---

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

**Complexity:** Medium (requires multi-timeframe stack, 24-bar protection window management)

**Proven in crypto:** YES -- extensively validated in our system.

**Combination potential:** Already the core. Can be enhanced with DI crossover direction, volume-weighted momentum, or regime-conditional sizing.

---

### 2.2 Momentum Ignition Detection

**Core logic:** Detect when a rapid price move is caused by deliberate "momentum ignition" -- a pattern where an aggressive participant pushes price quickly to trigger stops and momentum-following algos, then rides the resulting cascade. Identified by: sharp price move + volume spike + rapid order book thinning on the opposite side. Trade in the direction of ignition if confirmed by broader trend; fade if against trend.

**Citation:** SEC (2010). "Concept Release on Equity Market Structure." Also: Cartea, A. & Jaimungal, S. (2015). "Algorithmic Trading of Co-Integrated Assets." *International Journal of Theoretical and Applied Finance*.

**Expected profile:**
- Win rate: 50-60% (if correctly identified)
- Payoff ratio: 1.5-2.5x
- Trade frequency: Low (ignition events are episodic)
- Sharpe: Variable

**Suitability for our system:** LOW-MODERATE. Requires tick-level or 1-minute data to detect ignition patterns. Our 1H bars are too coarse to distinguish true ignition from organic moves. However, our 1-minute microstructure data (in `1m_cache/`) could support ignition detection as a daily signal. The concept is partially captured by our S11 momentum burst (3% hourly move is often ignition-triggered).

**Complexity:** High (requires order book data or tick data for proper detection)

**Proven in crypto:** Ignition patterns are well-documented in crypto due to thin order books and cascading liquidations. Not academically validated as a trading strategy.

**Combination potential:** Could serve as a filter to distinguish "real" momentum bursts from ignition artifacts in S11.

---

### 2.3 Volume-Confirmed Breakouts

**Core logic:** Only trade breakouts (price exceeding N-bar high/low or resistance/support) when accompanied by volume significantly above average (2x+ 20-bar volume average). Volume confirmation reduces false breakouts. The combination of price breakout + volume surge indicates genuine institutional interest.

**Citation:** Granville, J. (1963). *Granville's New Key to Stock Market Profits*. Also: Karpoff, J. (1987). "The Relation Between Price Changes and Trading Volume." *Journal of Financial and Quantitative Analysis*.

**Expected profile:**
- Win rate: 45-55%
- Payoff ratio: 1.5-2.0x
- Trade frequency: Medium-Low
- Sharpe: 0.7-1.1

**Suitability for our system:** HIGH. We already use `vol_ratio` (volume relative to 20-bar average) as a filter, and our indicator analysis confirms vol_ratio_hi + BB_pct_low yields +0.99% mean return with 58.4% win rate across 45 tokens. Our S11 strategy implicitly captures this via the volume burst condition. Making it more explicit (require vol_ratio > 2.0 for breakout entries) could improve signal quality.

**Complexity:** Low

**Proven in crypto:** YES -- confirmed in our indicator combination analysis.

**Combination potential:** Already partially integrated. Could be strengthened by adding volume rate-of-change (acceleration of volume, not just level).

---

### 2.4 Relative Strength Momentum (vs BTC or vs Universe)

**Core logic:** Rank tokens by their performance relative to BTC (or the token universe average) over a lookback period. Go long tokens showing the strongest relative strength (outperforming BTC); avoid or short tokens showing relative weakness. The alpha comes from capital rotation toward strength.

**Citation:** Levy, R. (1967). "Relative Strength as a Criterion for Investment Selection." *Journal of Finance*. Also: O'Neil, W. (1988). *How to Make Money in Stocks* (RS rating concept).

**Expected profile:**
- Win rate: 45-55%
- Payoff ratio: 1.3-1.8x
- Trade frequency: Low (rebalance weekly or biweekly)
- Sharpe: 0.5-1.0

**Suitability for our system:** MODERATE. Cross-sectional momentum is weaker and less reliable than time-series momentum in crypto (Han et al. 2023). However, relative strength vs BTC is useful for token selection rather than timing. Our system currently selects tokens via CPCV robustness (static), but dynamic token selection based on RS could improve rotation into the tokens with the strongest current trends.

**Complexity:** Low-Medium

**Proven in crypto:** Weak evidence. Han et al. (2023) found cross-sectional momentum unreliable in crypto, especially for small-cap tokens. Works better among large caps.

**Combination potential:** Best used as a token selection layer rather than a signal generator. Rank tokens by relative strength vs BTC, then apply S09/S11 only to the top-ranked tokens.

---

### 2.5 Momentum Crash Protection

**Core logic:** Momentum strategies are vulnerable to "crashes" -- sudden, violent reversals where past winners collapse and past losers surge (typically during market stress). Protection methods: (1) scale momentum exposure inversely to realized volatility, (2) reduce exposure when recent momentum dispersion is extreme, (3) use a stop-loss on the momentum portfolio itself.

**Citation:** Barroso, P. & Santa-Clara, P. (2015). "Momentum has its Moments." *Journal of Financial Economics*, 116(1), 111-120.

**Expected profile:**
- Win rate: N/A (risk management overlay, not a signal)
- Payoff ratio: Improves Sharpe by 0.3-0.5 on momentum strategies
- Trade frequency: N/A (adjusts sizing, not entries)
- Sharpe improvement: 0.3-0.5 added Sharpe on top of base momentum

**Suitability for our system:** HIGH. Our drawdown circuit breakers (-15% half size, -25% go to cash) partially address this, but Barroso & Santa-Clara's method is more elegant: scale momentum bet size by 1/sigma_recent. When realized vol spikes, position sizes automatically shrink, reducing crash exposure without binary on/off rules.

**Complexity:** Low (simple volatility scaling formula)

**Proven in crypto:** The principle is validated in traditional markets. In crypto, momentum crashes are severe (bear market transitions). Our regime filter (no momentum signals in bear regime) is a crude version of this.

**Combination potential:** Layer directly onto S09/S11 as dynamic position sizing: position_size = base_size * (target_vol / realized_vol_20d). This is essentially vol-targeting applied to momentum.

---

### 2.6 Momentum with Volatility Scaling

**Core logic:** Scale the size of momentum bets inversely proportional to recent realized volatility. In low-vol environments, take larger positions (the signal is cleaner); in high-vol environments, take smaller positions (noise increases). This is distinct from crash protection in that it is applied continuously, not just during stress.

**Citation:** Moreira, A. & Muir, T. (2017). "Volatility-Managed Portfolios." *Journal of Finance*, 72(4), 1611-1644.

**Expected profile:**
- Win rate: Same as base strategy
- Payoff ratio: Same as base strategy
- Risk-adjusted return improvement: 20-40% higher Sharpe
- Trade frequency: Same (sizing changes, not entry/exit)

**Suitability for our system:** HIGH. We already use vol-parity position sizing across tokens (via ATR-based sizing). Moreira & Muir's insight is to also scale total exposure: in low-vol periods, the strategy should deploy more capital overall; in high-vol periods, less. This is our regime-conditional sizing table (80-90% deployed in bull, 10-20% in bear) but formalized as a continuous function.

**Complexity:** Low

**Proven in crypto:** Validated in traditional markets. The logic applies directly to crypto: our own data shows that "low volatility predicts continuation" in uptrends (ATR_pct IC = -0.072 in uptrends).

**Combination potential:** Direct enhancement to S09/S11 position sizing. Replace discrete regime tiers with continuous vol-scaling.

---

## 3. MEAN REVERSION

Mean reversion strategies buy when prices deviate significantly below a fair-value estimate and sell when they revert. In crypto swing trading, our backtests have shown that **mean reversion consistently loses money at our timeframe and hold periods**. This section documents strategies, explains why they fail, and identifies the narrow conditions where MR might work.

---

### 3.1 Bollinger Band Mean Reversion (with Regime Filter)

**Core logic:** Buy when price touches or penetrates the lower Bollinger Band (2 standard deviations below 20-period MA); sell at the middle band (MA). The regime filter restricts entries to non-trending markets (ADX < 20 or range regime). The theory: in range-bound markets, prices oscillate around the mean, and extreme deviations revert.

**Citation:** Bollinger, J. (2002). *Bollinger on Bollinger Bands*. Also: Lo, A. & MacKinlay, A.C. (1990). "When Are Contrarian Profits Due to Stock Market Overreaction?" *Review of Financial Studies*, 3(2), 175-205.

**Expected profile (theoretical):**
- Win rate: 55-65%
- Payoff ratio: 0.8-1.2x (wins are small mean-reversions; losses can be large breakdowns)
- Trade frequency: Medium
- Sharpe: 0.3-0.7

**Our backtest results:** NEGATIVE. All mean reversion strategies from our 70-strategy sweep lose money (-$25K to -$185K/yr). Even with regime filtering (range/quiet regimes only), RSI<35, BB_pct<0.15, MACD turning, and taker>0.52, mean reversion loses on swing timeframes.

**Why it fails in our system:**
1. **Hold period mismatch.** BB mean reversion works on 1-day holds (our indicator analysis shows vol_ratio_hi + BB_pct_low = +0.99% at 1-day forward). But at 18-720 hour holds, the reversion signal decays and the position is exposed to trend continuation risk.
2. **Stop-loss incompatibility.** Research (Beluska & Vojtko 2024) confirms that stop-losses destroy MR performance. Wide stops or no stops perform best. But our system requires stops for risk management.
3. **Crypto regime persistence.** When crypto trends, it trends for weeks to months. A "dip" that looks like a mean reversion setup is often the start of a sustained trend change.
4. **Fat left tails.** Mean reversion produces fat left tails (large occasional losses), which is the opposite of our desired positive-skew profile.

**Suitability for our system:** VERY LOW for swing holds. Could work as a very short-term (1-3 day) overlay with separate capital allocation.

**Complexity:** Low

**Proven in crypto:** Works at 1-day horizon per academic research, but NOT at swing timeframes per our extensive testing.

---

### 3.2 RSI Extremes with Confirmation

**Core logic:** Buy when RSI drops below 30 (oversold) and a confirmation signal appears (MACD turning positive, price reclaiming a short-term MA, volume spike). Sell when RSI rises above 70 (overbought) with bearish confirmation. The confirmation reduces false signals from RSI alone.

**Citation:** Wilder, J.W. (1978). *New Concepts in Technical Trading Systems*. Also: Connors, L. & Alvarez, C. (2009). *Short Term Trading Strategies That Work*.

**Expected profile (traditional markets):**
- Win rate: 55-65%
- Payoff ratio: 0.9-1.3x
- Trade frequency: Medium
- Sharpe: 0.4-0.8

**Our findings:** RSI is nearly useless as a standalone predictor in crypto (IC=0.004). However, RSI_low + MACD_pos is one of our strongest 2-indicator combinations (+1.37% mean return, 58.3% win rate at 1-day horizon). The key insight from our regime analysis: RSI works differently in each regime. In uptrends, high RSI predicts MORE upside (momentum). In downtrends, high RSI predicts more downside (fade bounces). Using RSI in its "textbook" mean-reversion sense only works in range markets.

**Suitability for our system:** LOW as primary strategy. USEFUL as a confirmation filter within regime-conditioned logic. RSI < 30 in an uptrend (our 4H pullback condition) is effectively a trend continuation dip-buy, not true mean reversion.

**Complexity:** Low

**Proven in crypto:** RSI extremes predict 1-day returns with confirmation. Not reliable at swing timeframes.

---

### 3.3 Statistical Arbitrage / Pairs Trading in Crypto

**Core logic:** Identify pairs of tokens whose prices move together (cointegrated or highly correlated). When the spread between them deviates from its mean by more than a threshold (e.g., 2 standard deviations), go long the underperformer and short the outperformer, betting on convergence.

**Citation:** Gatev, E., Goetzmann, W., & Rouwenhorst, K.G. (2006). "Pairs Trading: Performance of a Relative-Value Arbitrage Rule." *Review of Financial Studies*, 19(3), 797-827. Crypto-specific: Palazzi, R. (2025). "Pairs Trading in Bullish Crypto Market." *Journal of Futures Markets*.

**Expected profile:**
- Win rate: 55-70%
- Payoff ratio: 0.8-1.5x
- Trade frequency: Medium (depends on pair and threshold)
- Sharpe: 0.5-1.2 (market-neutral)

**Suitability for our system:** LOW. Pairs trading requires shorting capability (we are long-only in spot). Crypto correlations are unstable -- pairs that are cointegrated in one period frequently break in the next (regime changes, protocol upgrades, narrative shifts). Palazzi (2025) found pairs trading effective in bullish crypto markets, but this requires continuous monitoring of cointegration relationships.

**Complexity:** High (cointegration testing, dynamic pair selection, spread calculation, two-legged execution)

**Proven in crypto:** Yes in academic studies, but with caveats about relationship instability and short-selling costs (funding rates on perps).

**Combination potential:** Not directly usable in our long-only spot system. Could inform relative token selection (buy the underperformer of a cointegrated pair when the spread is wide and the overall trend is up).

---

### 3.4 Ornstein-Uhlenbeck (OU) Based Strategies

**Core logic:** Model the price process as an Ornstein-Uhlenbeck process (continuous-time mean-reverting process). Estimate three parameters: mean-reversion speed (kappa), long-term mean (theta), and volatility (sigma). Trade when price deviates from theta by more than a threshold derived from the OU parameters. The half-life of mean reversion (ln(2)/kappa) determines the expected holding period.

**Citation:** Uhlenbeck, G.E. & Ornstein, L.S. (1930). "On the Theory of the Brownian Motion." *Physical Review*. Applied to finance: Leung, T. & Li, X. (2016). "Optimal Mean Reversion Trading." Also: Avellaneda, M. & Lee, J.H. (2010). "Statistical Arbitrage in the US Equities Market."

**Expected profile:**
- Win rate: 55-65%
- Payoff ratio: 0.9-1.3x
- Trade frequency: Depends on half-life (for crypto, half-lives tend to be short when they exist)
- Sharpe: 0.3-0.8

**Suitability for our system:** LOW. Most crypto tokens fail the OU model test -- they are momentum-driven, not mean-reverting. An OU process requires negative autocorrelation (prices tend to reverse), but crypto shows positive autocorrelation at multi-day horizons. The OU model is more applicable to crypto spreads (e.g., ETH-BTC ratio) than to individual token prices.

**Complexity:** Medium-High (parameter estimation via maximum likelihood, half-life calculation, optimal entry/exit thresholds)

**Proven in crypto:** Not validated for individual crypto tokens. May work for crypto spread trading or stablecoin depegging events.

---

### 3.5 Research Context: When Does Mean Reversion Work?

**Key papers:**
- Lo, A. & MacKinlay, A.C. (1990). "When Are Contrarian Profits Due to Stock Market Overreaction?" Found that contrarian profits exist in equities but are driven by cross-autocorrelation (lead-lag) rather than pure overreaction.
- Poterba, J. & Summers, L. (1988). "Mean Reversion in Stock Prices: Evidence and Implications." Found weak evidence of mean reversion in equity prices at 3-5 year horizons. Short-term: momentum dominates. Long-term: possible mean reversion.
- Beluska, M. & Vojtko, T. (2024). "Revisiting Trend-Following and Mean-Reversion in Bitcoin." BTC bounces from local minima (mean-reverts) but trends from local maxima (momentum). Stop-losses hurt MR performance.

**Synthesis for our system:**

Mean reversion in crypto is:
- **VALID at 1-day or shorter horizons** (our indicator combos confirm 1-day predictive power)
- **VALID after sharp crashes** (>15-20% drops in otherwise trending markets) -- our V3 Liquidity Contrarian captures this (63% win rate, but only 108 trades in 2 years)
- **INVALID at swing timeframes (18-720 hours)** -- every MR strategy we tested loses money
- **INVALID during bear markets** regardless of timeframe
- **INVALID with tight stops** -- MR needs wide stops or no stops, which conflicts with our risk management

The universal finding: **mean reversion is parameter-dependent and regime-dependent, not universally present in crypto**. It exists in narrow conditions (short hold, after extreme drops, in range regimes) but is overwhelmed by momentum at our target timeframe.

---

## 4. VOLATILITY STRATEGIES

---

### 4.1 Volatility Breakout (Compression to Expansion)

**Core logic:** Identify periods of unusually low volatility (compression) and enter when volatility expands, betting that the breakout direction will persist. Compression detected by: BB bandwidth < Nth percentile of recent history, or ATR < Nth percentile, or Keltner channel inside Bollinger Bands (TTM Squeeze). Enter in the direction of the breakout.

**Citation:** Bollinger, J. (2002). *Bollinger on Bollinger Bands*. TTM Squeeze: Carter, J. (2012). *Mastering the Trade*.

**Expected profile (theoretical):**
- Win rate: 45-55%
- Payoff ratio: 1.5-2.5x
- Trade frequency: Low-Medium
- Sharpe: 0.5-1.0

**Our backtest results:** FAILED. Vol_breakout consistently loses money (-$4,220/yr). BB squeeze-to-breakout is unreliable in crypto post-ETF era.

**Why it failed in our system:**
1. **Squeeze definition too loose.** BB squeeze occurs frequently (160 days out of 760 in our data), diluting signal quality.
2. **False breakouts.** After compression, price often breaks out in one direction and immediately reverses (fakeout). Our 24-bar protection window may help, but the initial direction is wrong ~55% of the time.
3. **Post-ETF regime change.** Bitcoin's volatility structure changed after the spot ETF launch in January 2024. Vol compression periods became more frequent but breakouts less directional.
4. **Payoff < 1.0x.** Even when breakouts work, the gains are smaller than the losses from false breakouts, yielding a negative expectancy.

**Possible modifications:**
- Use ATR percentile instead of BB squeeze (different compression metric)
- Require volume confirmation (vol_ratio > 2x) on the breakout bar
- Only take breakouts aligned with the higher-timeframe trend (daily trend direction)
- Longer compression requirement (20+ bars instead of 12)
- Use taker_buy_ratio to determine breakout direction (if taker > 0.55, long; if < 0.45, short)

**Suitability for our system:** LOW unless significantly reformulated. The concept is sound but our implementation needs fundamental changes.

**Complexity:** Medium

---

### 4.2 Volatility Compression Squeeze (TTM Squeeze)

**Core logic:** When Bollinger Bands move inside Keltner Channels, the market is in a "squeeze" -- an extremely compressed state. When BBs expand back outside KCs, the squeeze "fires" and a directional move begins. The momentum oscillator (typically MACD or momentum-based) at the time of the squeeze firing determines direction.

**Citation:** Carter, J. (2012). *Mastering the Trade* (TTM Squeeze). Also: Keltner, C. (1960). *How to Make Money in Commodities*.

**Expected profile:**
- Win rate: 50-55%
- Payoff ratio: 1.5-2.0x
- Trade frequency: Low
- Sharpe: 0.5-0.9

**Suitability for our system:** LOW. This is a more refined version of our vol_breakout, using the BB-inside-KC condition. The same problems apply: false breakouts, direction uncertainty, and post-ETF regime change. TTM Squeeze is popular among retail traders but has weak academic support.

**Complexity:** Medium (requires both BB and KC calculation)

---

### 4.3 Vol Targeting / Risk Parity (Asness et al.)

**Core logic:** Maintain a constant level of portfolio volatility by adjusting leverage/exposure inversely to realized volatility. When vol is low, increase exposure; when vol is high, decrease. This is not a directional signal but a sizing overlay that improves Sharpe ratio of any underlying strategy.

**Citation:** Asness, C.S., Frazzini, A., & Pedersen, L.H. (2012). "Leverage Aversion and Risk Parity." *Financial Analysts Journal*, 68(1), 47-59. Also: Ilmanen, A. (2011). *Expected Returns*.

**Expected profile:**
- Win rate: N/A (sizing overlay)
- Sharpe improvement: 0.2-0.5 added Sharpe
- Drawdown reduction: 20-40% lower max drawdown

**Suitability for our system:** HIGH. We already implement a crude version via ATR-based position sizing (vol-parity across tokens) and regime-conditional total exposure. The refinement would be to target a specific portfolio volatility (e.g., 25% annualized) and continuously adjust gross exposure to maintain it. This is the Moreira & Muir (2017) approach applied at the portfolio level.

**Complexity:** Low-Medium

**Proven in crypto:** The principle is validated. Our regime-conditional sizing table is a discretized version.

**Combination potential:** Direct overlay on entire portfolio. Replace discrete regime tiers (80%/60%/30%/10% deployed) with continuous vol-targeting.

---

### 4.4 Straddle-Like Strategies Using Spot (Vol Trading Without Options)

**Core logic:** When you expect a large move but are uncertain of direction (e.g., pre-announcement, pre-event), buy both breakout directions simultaneously: set a buy stop above recent highs and a sell stop below recent lows. When one triggers, cancel the other. This synthetically replicates a long straddle using spot positions.

**Citation:** Natenberg, S. (1994). *Option Volatility and Pricing*. Adapted to spot by various practitioners.

**Expected profile:**
- Win rate: 40-50%
- Payoff ratio: 1.5-3.0x (when moves are large enough to overcome entry cost of both stops)
- Trade frequency: Low (event-driven)

**Suitability for our system:** LOW. This requires simultaneous long and short capability. In spot-only, you can approximate by placing buy stops only (volatility expansion trade). But the real value is in the directionless bet, which requires either options or perps. Also, crypto events are often "buy the rumor, sell the news" -- the move has happened before the event.

**Complexity:** Medium

**Proven in crypto:** Not validated as a systematic strategy. Works anecdotally around major events (ETF approvals, halvings, protocol upgrades).

---

### 4.5 Volatility Risk Premium Harvesting in Crypto

**Core logic:** In traditional markets, implied volatility systematically exceeds realized volatility (the volatility risk premium, or VRP). Selling volatility (selling options) harvests this premium. In crypto, the VRP exists in perp funding rates (positive funding = longs paying shorts) and in options markets (put skew).

**Citation:** Carr, P. & Wu, L. (2009). "Variance Risk Premiums." *Review of Financial Studies*, 22(3), 1311-1341. Crypto-specific: Liu, Y. & Tsyvinski, A. (2021). "Risks and Returns of Cryptocurrency." *Review of Financial Studies*.

**Expected profile:**
- Win rate: 60-75% (premium collection)
- Payoff ratio: 0.3-0.8x (small wins, occasional large losses when vol spikes)
- Trade frequency: Continuous
- Sharpe: 0.5-1.0 (before tail events)

**Suitability for our system:** LOW for direct implementation (requires options or perp markets, not spot). However, the VRP concept informs our regime model: when implied vol (or funding rates) is extremely high relative to realized vol, the market is pricing fear that may not materialize -- a contrarian long signal. This is how our funding rate signal works.

**Complexity:** High (requires options or perp infrastructure)

**Proven in crypto:** VRP exists in crypto options and funding rates. Systematically harvesting it requires perp/options infrastructure.

---

## 5. REGIME-ADAPTIVE STRATEGIES

Regime detection is the single most important enhancement to any strategy. Our data shows that 52% of the time crypto is in a range regime, 19% uptrend, 18% quiet, 10% downtrend, and 1% crisis. Strategies that adapt to regime outperform static strategies.

---

### 5.1 Hidden Markov Model (HMM) Based Regime Switching

**Core logic:** Use an HMM to classify market state into 2-4 regimes (e.g., bull, bear, sideways, crisis) based on observable features (returns, volatility, volume). The HMM infers the hidden state probabilistically. Strategy selection, position sizing, and risk limits change based on the inferred regime.

**Citation:** Hamilton, J.D. (1989). "A New Approach to the Economic Analysis of Nonstationary Time Series and the Business Cycle." *Econometrica*, 57(2), 357-384. Crypto: Castellano, R. & D'Ecclesia, R.L. (2025). "Regime Switching Forecasting for Cryptocurrencies." *Digital Finance*.

**Expected profile:**
- Win rate: N/A (regime overlay, not a signal)
- Improvement: 10-30% better risk-adjusted returns vs regime-unaware strategies
- Regime detection accuracy: 60-80% (lagging by design)

**Suitability for our system:** HIGH. We already implement HMM-based regime detection in `regime_detector.py`. Our regime classifications (crisis, quiet, uptrend, range, downtrend) drive allocation decisions. The academic literature confirms that 2-3 state HMMs are effective for crypto regime identification, with improved performance when using both return and volatility observations.

**Complexity:** Medium-High (HMM training, online inference, state interpretation)

**Proven in crypto:** YES -- both in academic literature and in our own system.

**Combination potential:** Already integrated. Refinement: retrain HMM periodically, add volume and funding rate as additional observations.

---

### 5.2 Adaptive Asset Allocation (Butler et al.)

**Core logic:** Dynamically adjust portfolio weights based on recent momentum, correlation, and volatility of available assets. Assets with stronger recent momentum, lower correlation to the portfolio, and lower volatility receive higher weights. Rebalance monthly or at threshold drift.

**Citation:** Butler, A., Philbrick, M., Gordillo, R., & Varadi, D. (2012). "Adaptive Asset Allocation: A Primer." *ReSolve Asset Management*. Also: Keller, W. & Keuning, T. (2015). "Protective Asset Allocation (PAA)."

**Expected profile:**
- Win rate: N/A (portfolio construction method)
- Sharpe improvement: 0.3-0.6 over static allocation
- Max drawdown improvement: 20-40%
- Rebalance frequency: Monthly or threshold-based

**Suitability for our system:** MODERATE-HIGH. We currently use CPCV-validated tokens as a static universe. Adaptive allocation would dynamically weight among our 6-49 tokens based on recent performance, correlation, and volatility. This is particularly relevant because crypto correlations shift significantly between regimes (all corr ~1.0 in panic, more dispersed in normal markets).

**Complexity:** Medium

**Proven in crypto:** Not specifically validated in crypto academic literature, but the principles (momentum-weighted, vol-adjusted) are confirmed in our data.

**Combination potential:** Could serve as the capital allocation layer above S09/S11 signal generation. Currently, we allocate equally to validated tokens; adaptive allocation would tilt toward the strongest.

---

### 5.3 Dynamic Strategy Selection Based on Market Regime

**Core logic:** Maintain a portfolio of strategies and dynamically allocate capital to the strategy best suited for the current regime. In trending markets: use momentum/trend-following. In range markets: use mean-reversion (if applicable) or reduce exposure. In crisis: hedge or go to cash. The regime detector drives strategy weights.

**Citation:** Baz, J., Granger, N., Harvey, C.R., Le Roux, N., & Rattray, S. (2015). "Dissecting Investment Strategies in the Cross Section and Time Series." *AQR Working Paper*. Also: Kritzman, M., Page, S., & Turkington, D. (2012). "Regime Shifts: Implications for Dynamic Strategies." *Financial Analysts Journal*.

**Expected profile:**
- Improvement over single strategy: 15-30% better Sharpe
- Drawdown improvement: 25-50%

**Suitability for our system:** HIGH. We currently run S09 and S11 simultaneously but with static allocation (60/40). Dynamic allocation based on regime (e.g., more S11 momentum burst in trending markets, reduce both in range/bear) would improve risk-adjusted returns. Our regime detector already provides the signal.

**Complexity:** Medium

**Proven in crypto:** Not explicitly tested, but the principle is well-established and our regime detector is already in place.

**Combination potential:** This IS the combination method -- it determines how S09, S11, and any future strategies share capital.

---

### 5.4 Crisis Alpha Strategies (Tail Risk Hedging)

**Core logic:** Strategies designed to profit during market crises when most assets lose value. In traditional markets: long volatility, long puts, trend-following in futures. In crypto: rapid de-risking rules, short-term momentum reversal detection, funding rate extremes as capitulation signals.

**Citation:** Bhansali, V. (2014). *Tail Risk Hedging: Creating Robust Portfolios for Volatile Markets*. Also: Dao, T.L., Deremble, C., Lempérière, Y., Potters, M., & Bouchaud, J.P. (2016). "Tail Protection for Long Investors: Trend Convexity at Work." Crypto: multiple practitioners note that systematic trend following provides "crisis alpha" by going short or flat during crashes.

**Expected profile:**
- Win rate: 30-40% (most of the time the hedge costs money)
- Payoff ratio: 5-20x (massive gains during crises)
- Trade frequency: Very low
- Annual cost: -2% to -5% drag in normal markets

**Suitability for our system:** MODERATE. We already have drawdown circuit breakers (-15% half size, -25% go to cash), which provide crude tail protection. True crisis alpha in crypto would involve: (1) detecting crisis onset early (VPIN spike, funding rate collapse, OI liquidation cascade), (2) aggressive position reduction, (3) potentially initiating short positions via perps. In spot-only, crisis alpha is limited to rapid exit speed.

**Complexity:** Medium-High

**Proven in crypto:** Trend-following strategies with regime filters naturally produce crisis alpha by reducing exposure during downtrends. Our S09 does this via the ADX + regime filter.

---

### 5.5 Regime-Conditional Momentum (Momentum in Uptrend, Reversion in Range)

**Core logic:** The key insight from our regime analysis: different strategies work in different regimes. Run momentum in uptrends (where RSI high predicts MORE upside), switch to mean-reversion in ranges (where intraday_kurtosis and intraday_skew are the best predictors), and go to cash in downtrends/crises (where RSI high predicts more downside).

**Citation:** Asness, C.S. (2014). "Momentum in Japan is Real... But is it a 'Dynamic' Strategy?" AQR. Also: Henkel, S.J., Martin, J.S., & Nardari, F. (2011). "Time-Varying Short-Horizon Predictability." *Journal of Financial Economics*.

**Expected profile:**
- Improvement: 20-40% better Sharpe than unconditional momentum
- Win rate: Depends on regime accuracy
- Trade frequency: Same as base strategy within active regimes

**Suitability for our system:** VERY HIGH. This is essentially what our system already does implicitly (trend following with regime filter), but making it explicit and adding a range-market strategy could capture the 52% of time we currently sit out. The challenge: our MR strategies all lose money at swing timeframes, even in range markets. The range-market strategy might need to be very short-term (1-3 day) or simply "no trade" with tighter capital allocation.

**Complexity:** Medium

**Proven in crypto:** Yes -- our regime-conditional indicator analysis confirms that different signals work in each regime.

**Combination potential:** This is the organizing principle for our entire strategy portfolio.

---

## 6. CROSS-SECTIONAL / RELATIVE VALUE

---

### 6.1 Cross-Sectional Momentum (Long Winners, Short Losers)

**Core logic:** Rank all tokens by their past returns over a lookback period (typically 1-12 months). Buy the top decile (winners), sell the bottom decile (losers). The alpha comes from the persistence of relative performance.

**Citation:** Jegadeesh, N. & Titman, S. (1993). "Returns to Buying Winners and Selling Losers: Implications for Stock Market Efficiency." *Journal of Finance*, 48(1), 65-91. Crypto: Han, Y., Kang, J., & Ryu, D. (2023). SSRN 4675565.

**Expected profile:**
- Win rate: 50-55%
- Payoff ratio: 1.0-1.5x
- Trade frequency: Low (rebalance monthly)
- Sharpe: 0.3-0.8

**Suitability for our system:** LOW. Han et al. (2023) found cross-sectional momentum "weak and unreliable" in crypto. The long-short structure requires shorting (not available in spot). Long-only cross-sectional momentum (buy winners only) is partially captured by our relative strength token selection, but the academic evidence suggests time-series momentum dominates cross-sectional in crypto.

**Complexity:** Low-Medium

**Proven in crypto:** Weak evidence. Not recommended as primary strategy.

---

### 6.2 Factor Investing in Crypto

**Core logic:** Identify systematic factors (characteristics that predict cross-sectional return differences) in crypto markets. Potential factors: size (market cap), momentum (past returns), reversal (short-term past returns with opposite sign), liquidity (bid-ask spread, volume), and volatility (realized vol).

**Citation:** Fama, E. & French, K. (1993). "Common Risk Factors in the Returns on Stocks and Bonds." *Journal of Financial Economics*. Crypto-specific: Liu, Y. & Tsyvinski, A. (2021). "Risks and Returns of Cryptocurrency." *Review of Financial Studies*. Also: Borri, N. & Shakhnov, K. (2022). "The Cross-Section of Cryptocurrency Returns."

**Factors identified in crypto research:**
1. **Momentum factor:** Strongest factor in crypto (Liu & Tsyvinski 2021). Works for 1-4 week lookbacks.
2. **Size factor:** Smaller tokens have higher expected returns but also higher risk and worse liquidity. Not practically exploitable at $200K scale.
3. **Reversal factor:** 1-week reversal exists in crypto but is noisy and difficult to exploit after transaction costs.
4. **Liquidity factor:** Illiquid tokens earn a premium, but execution costs eat the premium.
5. **Volatility factor:** Low-vol tokens tend to underperform in bull markets, outperform in bear markets (opposite to equities).

**Expected profile:**
- Varies by factor
- Momentum factor Sharpe: 0.5-1.0
- Size/liquidity factors: alpha disappears after realistic transaction costs

**Suitability for our system:** MODERATE. The momentum factor reinforces our existing approach. Other factors (size, liquidity, reversal) are difficult to exploit in practice with our capital size and instrument universe. Factor investing is more useful for token screening (avoid illiquid tokens, favor momentum) than as a standalone strategy.

**Complexity:** Medium-High (factor construction, portfolio optimization)

---

### 6.3 Sector Rotation (DeFi vs L1 vs Meme vs Infrastructure)

**Core logic:** Crypto tokens cluster into sectors (L1 smart contracts, L2 scaling, DeFi, meme tokens, infrastructure, privacy, etc.). Capital rotates between sectors during different market phases. In early bull markets, capital flows to BTC, then L1s, then DeFi, then meme tokens. In bears, capital retreats in reverse order. Trade the rotation by overweighting the sector with the strongest recent relative performance.

**Citation:** No specific academic paper for crypto sector rotation. Traditional: Bernstein, R. (2001). *Style Investing*. Crypto sector frameworks: Messari, CoinGecko sector indices.

**Expected profile:**
- Win rate: 45-55%
- Payoff ratio: 1.2-1.8x
- Trade frequency: Low (rebalance monthly or at BTC.D inflection points)
- Sharpe: 0.4-0.8

**Suitability for our system:** MODERATE. Our token universe (49 tokens) spans multiple sectors. We could add sector tags and implement a rotation overlay: increase allocation to the sector showing strongest 2-week relative strength vs BTC. This aligns with the BTC.D / ETH-BTC cross-asset signals we already track.

**Complexity:** Medium (sector classification, relative strength calculation, allocation adjustment)

**Proven in crypto:** Anecdotally well-established (the "altseason rotation" is widely observed). Not rigorously validated academically.

**Combination potential:** Could serve as a higher-level allocation layer: allocate capital to sectors, then within each sector apply S09/S11 to individual tokens.

---

### 6.4 Relative Strength Index Across Tokens (RS Ranking)

**Core logic:** Compute each token's price performance relative to BTC over a rolling window (14-28 days). Rank tokens by relative strength. Allocate capital only to the top N tokens by RS rank. Rebalance weekly or biweekly.

**Citation:** O'Neil, W. (1988). *How to Make Money in Stocks* (IBD Relative Strength Rating). Levy, R. (1967). "Relative Strength as a Criterion for Investment Selection."

**Expected profile:**
- Win rate: 50-55%
- Payoff ratio: 1.2-1.8x
- Trade frequency: Low (rebalance period)
- Sharpe: 0.5-0.9

**Suitability for our system:** MODERATE. We currently select tokens via static CPCV validation. Adding dynamic RS ranking would create a two-stage filter: (1) pass CPCV robustness, (2) rank by current relative strength, (3) apply S09/S11 only to top-ranked tokens. This could improve capital efficiency by concentrating on the tokens currently in favor.

**Complexity:** Low

**Combination potential:** Excellent as a token selection overlay on S09/S11.

---

### 6.5 Mean-Variance Optimization with Crypto Constraints

**Core logic:** Markowitz portfolio optimization adapted for crypto's unique characteristics: extreme fat tails, unstable correlations, limited shorting. Use robust covariance estimation (shrinkage estimators, exponentially-weighted), constrain weights to long-only and max single position, and add cardinality constraints (max N positions).

**Citation:** Markowitz, H. (1952). "Portfolio Selection." *Journal of Finance*. Crypto: Brauneis, A. & Mestel, R. (2019). "Cryptocurrency-Portfolios in a Mean-Variance Framework." *Finance Research Letters*.

**Expected profile:**
- Improvement over equal-weight: 10-25% better Sharpe
- Max drawdown improvement: 10-20%

**Suitability for our system:** MODERATE. Mean-variance optimization in crypto is challenged by unstable correlations and non-normal returns. The research consensus (from our prior research) is that beyond 5-8 tokens, crypto portfolio optimization converges to equal-weight due to high within-class correlations. Our current approach (risk parity for core, momentum-weighted for satellite) may be close to optimal.

**Complexity:** Medium-High (robust covariance estimation, optimization with constraints)

---

## 7. ORDER FLOW / MICROSTRUCTURE STRATEGIES

---

### 7.1 VPIN-Based Entry Timing

**Core logic:** VPIN (Volume-Synchronized Probability of Informed Trading) measures the toxicity of order flow. High VPIN indicates that informed traders are active, predicting imminent volatility or directional moves. Use VPIN spikes as timing signals: high VPIN + trend confirmation = enter with conviction; high VPIN + no trend = stay out (imminent whipsaw).

**Citation:** Easley, D., Lopez de Prado, M., & O'Hara, M. (2012). "Flow Toxicity and Liquidity in a High-Frequency World." *Review of Financial Studies*, 25(5), 1457-1493. Crypto: Easley et al. (2024). SSRN 4814346. Average VPIN in crypto is 0.45-0.47 vs 0.22-0.23 in traditional futures.

**Expected profile:**
- Win rate: Improvement of 2-5% on top of base strategy
- Payoff ratio: Similar to base
- Trade frequency: Reduces trades (filter effect)

**Our backtest results:** VPIN as filter HURT performance. Reduced trades from 2,457 to 1,692 without improving quality. VPIN data quality issue: many tokens show 0.5 (no real data) for early period. The filter was too aggressive, cutting good entries along with bad.

**Suitability for our system:** LOW with current data quality. POTENTIALLY HIGH with better data (1-minute aggregated VPIN from `1m_cache/`). VPIN is the most independent indicator in our set (max correlation 0.31 with other indicators). The signal is there but the data quality is not.

**Complexity:** Medium (VPIN calculation from tick/1-minute data)

**Proven in crypto:** Academically validated for predicting volatility and price jumps. Our implementation failed due to data quality.

**Combination potential:** Revisit with 1-minute computed VPIN. Use as a conviction scaler (size up when VPIN confirms direction, size down when VPIN is ambiguous) rather than a binary filter.

---

### 7.2 Funding Rate Signals

**Core logic:** Perpetual futures funding rates reflect the balance of long/short positioning. Extreme positive funding (>0.03% per 8 hours) indicates overleveraged longs -- contrarian bearish. Extreme negative funding (<-0.01%) indicates overleveraged shorts -- contrarian bullish. Sustained funding rate divergence from price (price rising but funding falling) signals weakening conviction.

**Citation:** Inan, E. (2025). "Predictability of Funding Rates." SSRN 5576424. Also: QuantJourney (2024). "Funding Rates in Crypto: The Hidden Alpha."

**Expected profile:**
- Win rate: 55-65% (contrarian at extremes)
- Payoff ratio: 1.0-1.5x
- Trade frequency: Low (extreme funding is episodic)
- Signal horizon: 1-7 days

**Suitability for our system:** MODERATE. Funding rates are a derivatives signal and we trade spot. The information content is real (extreme funding predicts reversals), but we need to access funding rate data via API. Best used as a tertiary signal: don't enter new longs when funding is extremely positive (market overheated), lean into longs when funding is extremely negative (capitulation).

**Complexity:** Low (data acquisition is the main challenge)

**Proven in crypto:** YES -- well-documented contrarian signal. Funding rate arbitrage is a core strategy for crypto market-neutral funds.

**Combination potential:** Overlay on S09/S11 as an entry filter or sizing adjuster.

---

### 7.3 Liquidation Cascade Prediction

**Core logic:** When a large number of leveraged positions are concentrated near specific price levels, a move to those levels triggers cascading liquidations -- forced selling that amplifies the move. Predict liquidation levels from open interest heatmaps and funding rate data. Trade in the direction of expected cascades.

**Citation:** No single academic paper. Related: Brunnermeier, M. & Pedersen, L.H. (2009). "Market Liquidity and Funding Liquidity." *Review of Financial Studies*. Crypto-specific: Coinglass liquidation data, various practitioner analysis.

**Expected profile:**
- Win rate: 55-65%
- Payoff ratio: 1.5-3.0x (cascades produce outsized moves)
- Trade frequency: Low (cascade events)
- Signal horizon: Hours to 1-2 days

**Suitability for our system:** LOW-MODERATE. Requires real-time open interest heatmap data (available from exchanges like Coinglass but not currently in our data pipeline). The signal is powerful when it works (cascading liquidations are some of the largest moves in crypto) but difficult to systematize. Could be approximated by: (OI at record high + price approaching key support/resistance + high leverage indicated by extreme funding rates) = cascade risk elevated.

**Complexity:** High (data requirements, real-time monitoring)

**Proven in crypto:** Anecdotally very powerful. Not academically validated as a systematic strategy.

---

### 7.4 Order Book Imbalance Strategies

**Core logic:** Measure the imbalance between bid and ask depth in the order book. When bids significantly outweigh asks (buy-side imbalance), price is likely to move up. When asks outweigh bids (sell-side imbalance), price is likely to move down. The imbalance predicts short-term price direction.

**Citation:** Cont, R., Kukanov, A., & Stoikov, S. (2014). "The Price Impact of Order Book Events." *Journal of Financial Econometrics*, 12(1), 47-88.

**Expected profile:**
- Win rate: 52-58%
- Payoff ratio: 0.8-1.2x
- Trade frequency: High (intraday signal)
- Signal horizon: Minutes to hours
- Sharpe: 0.5-1.0 (before costs)

**Suitability for our system:** LOW. Order book imbalance is a very short-term signal (minutes to hours), much shorter than our 18-720 hour holding period. The signal decays rapidly (our taker buy ratio IC drops from 0.035 at 1-day to 0.008 at 5-day). Order book data requires real-time exchange connectivity.

**Complexity:** High (real-time data feed, order book processing)

**Proven in crypto:** YES for short-term prediction. NOT suitable for swing trading.

---

### 7.5 Taker Buy Ratio Divergence

**Core logic:** Track the ratio of taker buy volume to total volume. When taker buy ratio diverges from price (e.g., price making new highs but taker ratio declining), it signals weakening buying pressure -- an exhaustion signal. When taker ratio surges on a pullback, it signals strong buying interest -- a dip-buy signal.

**Citation:** Related to: Anastasopoulos, A. & Gradojevic, N. (2025). "Order Flow and Cryptocurrency Returns." EFMA 2025.

**Expected profile:**
- Win rate: 50-55%
- Payoff ratio: 1.0-1.5x
- Trade frequency: Medium
- Signal horizon: 1-3 days

**Our findings:** Taker buy ratio > 0.55 produces +0.42% mean 1-day return (53.3% win rate) across 19 tokens. Combined with positive daily return: +1.18% mean return (52.1% win rate). The signal is strongest at 1-day horizon and decays to noise by 5 days.

**Suitability for our system:** MODERATE as a confirmation signal for entries. Our S11 already uses taker ratio implicitly. Making it an explicit confirmation (require taker > 0.52 for long entries) could improve signal quality at the cost of fewer trades.

**Complexity:** Low (indicator already computed)

**Proven in crypto:** YES -- confirmed in our own indicator analysis.

**Combination potential:** Already partially integrated. Strengthen by using taker divergence (taker direction vs price direction) as an exit signal.

---

## 8. MULTI-FACTOR COMBINATION

---

### 8.1 Signal Combination Methods

#### Equal Weight Combination

**Core logic:** Assign equal weight to all signals. If 3 out of 5 signals are bullish, the combined signal is bullish. Simple, robust, hard to overfit.

**Citation:** DeMiguel, V., Garlappi, L., & Uppal, R. (2009). "Optimal Versus Naive Diversification: How Inefficient is the 1/N Portfolio?" *Review of Financial Studies*.

**Expected profile:** Modest improvement over best single signal. Very robust out-of-sample.

**Suitability:** MODERATE. We implicitly use this: multiple conditions must align (ADX, EMA, RSI, volume) for entry. Making weights explicit allows systematic tuning.

#### IC-Weighted Combination

**Core logic:** Weight each signal by its information coefficient (predictive power for forward returns). Higher IC signals get more weight. Requires estimating IC, which can be unstable.

**Citation:** Grinold, R. & Kahn, R. (2000). *Active Portfolio Management*. IC as the fundamental building block of alpha.

**Expected profile:** Better than equal weight when IC estimates are stable. Worse when they are noisy.

**Suitability:** MODERATE-HIGH. We have IC estimates from our indicator analysis (ADX IC=0.067 at top, RSI IC=0.004 at bottom). We could weight our entry conditions by IC, giving ADX the most influence and RSI the least.

#### ML-Based Combination

**Core logic:** Use a machine learning model (gradient boosted trees, neural network) to learn the optimal weighting of signals. The model captures nonlinear interactions between signals and adapts to regime changes.

**Citation:** Gu, S., Kelly, B., & Xiu, D. (2020). "Empirical Asset Pricing via Machine Learning." *Review of Financial Studies*, 33(5), 2223-2273. Crypto: arXiv:2410.06935 (XGBoost with chi-squared feature selection achieving 86-92% accuracy).

**Expected profile:** Highest in-sample performance. Risk of overfitting without rigorous validation.

**Suitability:** MODERATE but high overfitting risk. Our CPCV validation framework would be essential to validate any ML signal. The 86-92% accuracy in the arXiv study may not survive realistic transaction costs and out-of-sample testing.

---

### 8.2 Ensemble Strategies

**Core logic:** Run multiple independent strategies simultaneously and combine their signals. Strategies can be combined at the signal level (vote on direction) or at the portfolio level (each strategy manages its own allocation). Ensemble diversification reduces strategy-specific risk.

**Citation:** Breiman, L. (1996). "Bagging Predictors." *Machine Learning*, 24(2), 123-140. Applied to trading: Luo, J. et al. (2015). "Ensemble Trading Strategies."

**Expected profile:**
- Sharpe improvement: 20-40% over best single strategy (if strategies are uncorrelated)
- Max drawdown improvement: 15-30%
- Win rate: typically higher than individual strategies

**Suitability for our system:** HIGH. We already have two validated strategies (S09 and S11) with different signal profiles. Running them as an ensemble (Option C in our recommendations: 60% S11 + 40% S09) is the natural path. Adding V3 Liquidity Contrarian (very different profile: 63% win rate, low frequency) would further diversify.

**Complexity:** Low-Medium

**Proven in crypto:** Our own results confirm that S09 and S11 together are better than either alone for diversification.

**Combination potential:** This IS the combination method. Key: ensure strategies are genuinely independent (not the same signal with different parameters).

---

### 8.3 Kelly Criterion for Multi-Strategy Allocation

**Core logic:** Extend the Kelly criterion from single bets to multiple simultaneous strategies/positions. The multi-asset Kelly optimal allocation accounts for correlations between strategies. In practice, use fractional Kelly (quarter or half) due to estimation error.

**Citation:** Kelly, J.L. (1956). "A New Interpretation of Information Rate." *Bell System Technical Journal*. Multi-asset: Thorp, E. (2006). "The Kelly Criterion in Blackjack, Sports Betting, and the Stock Market." Also: MacLean, L., Thorp, E., & Ziemba, W. (2011). *The Kelly Capital Growth Investment Criterion*.

**Expected profile:**
- Theoretical: maximum long-term growth rate
- Practical (half-Kelly): ~75% of max growth with ~50% less drawdown
- Practical (quarter-Kelly): ~56% of max growth with manageable drawdown

**Our current approach:** Quarter-Kelly for T2/T3 tokens, half-Kelly for T1. Risk per trade: 1-2% of equity ($2-4K).

**Suitability for our system:** ALREADY IMPLEMENTED at the position level. Enhancement: compute multi-strategy Kelly considering the correlation between S09 and S11 returns. If correlation is low (they trigger on different tokens/times), the combined Kelly allocation can be higher than the sum of individual Kelly fractions.

**Complexity:** Medium (requires strategy-level return covariance estimation)

---

### 8.4 Avoiding P-Hacking: Multiple Testing Corrections

**Core logic:** When testing many strategies, some will appear profitable by chance. Multiple testing corrections adjust p-values or set higher thresholds to account for the number of tests performed. Without corrections, a naive researcher will find "significant" strategies that are pure noise.

**Citation:** Harvey, C.R., Liu, Y., & Zhu, H. (2016). "...and the Cross-Section of Expected Returns." *Review of Financial Studies*, 29(1), 5-68. They recommend t-stat > 3.0 (not 2.0) for new factors due to publication bias and multiple testing. Also: White, H. (2000). "A Reality Check for Data Snooping." *Econometrica*, 68(5), 1097-1126.

**Methods:**

#### Bonferroni Correction
Divide the significance level by the number of tests. If testing 70 strategies at alpha=0.05, each strategy needs p < 0.05/70 = 0.00071. Very conservative; high false-negative rate.

#### Benjamini-Hochberg-Yekutieli (BHY)
Controls the false discovery rate (FDR) instead of the family-wise error rate. Less conservative than Bonferroni. Allows a proportion of false discoveries. Better suited for strategy screening.

#### White's Reality Check
A bootstrap test that accounts for data snooping. Generates the distribution of the best strategy's performance under the null (no real edge) and tests whether the observed best strategy exceeds this distribution.

**Citation:** White, H. (2000). "A Reality Check for Data Snooping." *Econometrica*.

#### Hansen's Superior Predictive Ability (SPA) Test
An improvement on White's Reality Check that is more powerful (fewer false negatives). Tests whether any strategy has genuine predictive ability after accounting for the full universe of strategies tested.

**Citation:** Hansen, P.R. (2005). "A Test for Superior Predictive Ability." *Journal of Business & Economic Statistics*, 23(4), 365-380.

**Suitability for our system:** CRITICAL. We tested 70 strategies in our sweep. Without correction, we expect 3-4 strategies to appear significant by chance at the 5% level. Our CPCV validation (PBO < 40%) and walk-forward testing are our primary defenses against overfitting, but adding formal multiple testing corrections would provide additional rigor. Harvey et al.'s t-stat > 3.0 threshold is a practical rule: any strategy with t-stat < 3.0 should be treated with skepticism.

**Complexity:** Medium

**Proven relevance:** Essential for any systematic strategy research. Our CPCV framework is the gold standard for strategy validation, which partially addresses multiple testing through combinatorial cross-validation.

---

## 9. PROVEN CLASSIC STRATEGIES (LONG TRACK RECORD)

---

### 9.1 Turtle Trading Rules (Complete System)

See Section 1.4 for full description.

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

### 9.2 Aberration System (Fitschen)

**Core logic:** A trend-following system using Bollinger Bands as trend channels. Buy when price breaks above the upper BB on a closing basis; sell when price breaks below the lower BB. The "aberration" is the deviation from the mean that signals a trend initiation.

**Citation:** Fitschen, K. (2013). *Building Reliable Trading Systems*. Aberration system developed in the 1990s.

**Expected profile:**
- Win rate: 35-40%
- Payoff ratio: 2.0-3.0x
- Trade frequency: Low-Medium
- Sharpe: 0.5-0.8

**Suitability for our system:** MODERATE. This is conceptually similar to our vol_breakout strategy but uses BB penetration (close above upper band) as the signal rather than BB squeeze-to-expansion. It may perform differently because it does not require prior compression. However, our BB-based strategies generally underperform EMA/ADX-based strategies in crypto.

**Complexity:** Low

---

### 9.3 Larry Connors' Strategies

#### RSI-2 Strategy
**Core logic:** Use an extremely short-period RSI (2-period) to identify short-term oversold conditions in uptrending instruments. Buy when RSI(2) drops below 10 and the stock is above its 200-day MA. Exit when RSI(2) rises above 70. Designed for 1-4 day holds.

**Citation:** Connors, L. & Alvarez, C. (2009). *Short Term Trading Strategies That Work*.

**Expected profile:**
- Win rate: 70-80% (in equities with trend filter)
- Payoff ratio: 0.4-0.8x (small wins, occasional large losses)
- Trade frequency: Medium
- Sharpe: 0.5-0.9

**Suitability for our system:** LOW for our timeframe. RSI-2 is a very short-term mean reversion strategy (1-4 day holds). In our system, RSI has IC=0.004 and mean reversion consistently loses money at swing timeframes. The 70-80% win rate in equities likely does not translate to crypto. Would need to be tested as a very short-term overlay with dedicated capital.

**Complexity:** Low

#### Connors RSI (ConnorsRSI)
**Core logic:** A composite indicator combining: RSI(3), Up/Down streak length RSI, and Rate of Change percentile rank. Designed to capture both momentum and mean-reversion signals in a single metric.

**Suitability:** LOW. Same issues as RSI-2 for our system.

---

### 9.4 Gary Antonacci's Dual Momentum

See Section 1.1. This is our baseline strategy foundation.

**Additional context:** Antonacci's original system uses 12-month lookback for absolute and relative momentum, applied to US equity, international equity, and bonds. Our crypto adaptation shortens the lookback dramatically (10-28 days) because crypto cycles are faster. The principle (combine absolute and relative momentum with monthly rebalancing) is proven across multiple decades and asset classes.

---

### 9.5 Andreas Clenow's "Following the Trend" Approach

**Core logic:** A diversified trend-following system applied across many markets simultaneously. Uses exponential moving average (50-day) for trend direction, ATR for position sizing, and a simple rule: go long when price is above EMA(50) and the EMA is rising. Exit when price crosses below EMA or stop is hit. The edge comes from diversification across many uncorrelated markets.

**Citation:** Clenow, A. (2013). *Following the Trend: Diversified Managed Futures Trading*.

**Expected profile:**
- Win rate: 35-42%
- Payoff ratio: 1.8-2.5x
- Trade frequency: Low-Medium per market, but high across all markets
- Sharpe: 0.6-1.0

**Suitability for our system:** HIGH in principle. Clenow's approach is simple, robust, and designed for diversified futures portfolios. Our crypto adaptation (49 tokens = 49 "markets") provides the diversification. The EMA(50) trend direction + ATR position sizing is essentially our S09 foundation. Clenow emphasizes that the edge is statistical (catch a few big trends out of many attempts), not per-trade alpha.

**Complexity:** Low

**Proven in crypto:** The principle is proven. Our S09 is a more complex version of Clenow's approach.

---

### 9.6 AQR's Time-Series Momentum Factor (TSMOM)

**Core logic:** AQR's systematic implementation of TSMOM across asset classes. For each asset, compute the excess return over the past 12 months. Go long if positive, short if negative. Position size inversely proportional to recent volatility. Applied across equities, bonds, commodities, and currencies.

**Citation:** AQR Capital Management. Moskowitz, T.J., Ooi, Y.H., & Pedersen, L.H. (2012). "Time Series Momentum." *Journal of Financial Economics*. Also: Hurst, B., Ooi, Y.H., & Pedersen, L.H. (2017). "A Century of Evidence on Trend-Following Investing." *AQR*.

**Expected profile:**
- Win rate: 45-55%
- Payoff ratio: 1.2-1.8x
- Sharpe: 0.6-1.0 (across asset classes)
- Crisis alpha: positive during major market crises (GFC, COVID)

**Suitability for our system:** HIGH. The vol-scaled TSMOM approach is the institutional gold standard for trend following. Our S09/S11 are more aggressive variants (shorter lookbacks, 1H entry timing). The 12-month lookback is too long for crypto (cycles are shorter), but the principle of vol-scaling and sign-of-returns as direction applies directly.

**Complexity:** Low

**Proven in crypto:** The shortened lookback version (10-28 days) is validated in crypto academic literature and in our own system.

---

### 9.7 Cliff Asness' "Value and Momentum Everywhere"

**Core logic:** Both value and momentum factors exist across all major asset classes (equities, bonds, commodities, currencies). The two factors are negatively correlated, so combining them reduces risk. For each asset, compute a value score (undervaluation) and a momentum score (trend), then take positions proportional to the combined score.

**Citation:** Asness, C.S., Moskowitz, T.J., & Pedersen, L.H. (2013). "Value and Momentum Everywhere." *Journal of Finance*, 68(3), 929-985.

**Expected profile:**
- Sharpe of combined V+M: 1.0-1.5 (higher than either alone)
- Win rate: 50-55%
- Payoff ratio: 1.2-1.8x
- Correlation between V and M: -0.3 to -0.5 (diversification benefit)

**Suitability for our system:** MODERATE. The momentum component is directly applicable (we already implement it). The value component is challenging in crypto because intrinsic value is difficult to define. Possible value proxies for crypto: NVT ratio (network value to transaction volume), MVRV ratio (market value to realized value), price relative to long-term moving average (price/MA200 as a "cheapness" indicator). These are slow-moving signals best used as medium-term overlays.

**Complexity:** Medium (value metric definition in crypto is the hard part)

**Proven in crypto:** Momentum is proven. Value is conceptually applicable but under-researched in crypto.

**Combination potential:** Add a "crypto value" score (e.g., price below MA200 + NVT below historical median) as a secondary weight to our momentum signals. This would increase allocation to tokens that are both undervalued AND trending up.

---

## 10. CUTTING-EDGE / RECENT RESEARCH

---

### 10.1 Deep Reinforcement Learning for Execution

**Core logic:** Use deep RL (DQN, PPO, A3C) to learn optimal execution policies: when to enter, how much to trade, and when to exit. The agent learns from simulated market data, optimizing cumulative PnL or Sharpe ratio. The learned policy can adapt to market conditions without explicit rule specification.

**Citation:** Mnih, V. et al. (2015). "Human-level control through deep reinforcement learning." *Nature*. Applied to trading: Deng, Y. et al. (2017). "Deep Direct Reinforcement Learning for Financial Signal Representation and Trading." *IEEE Trans. on Neural Networks*. Crypto-specific: Sattarov, O. et al. (2020). "Recommending Cryptocurrency Trading Points with Deep RL."

**Expected profile:**
- Win rate: Varies (RL optimizes cumulative reward, not per-trade metrics)
- Payoff ratio: Varies
- Performance: Can match or exceed hand-crafted rules in-sample. Out-of-sample performance is highly dependent on training methodology and regime stability.

**Suitability for our system:** LOW-MODERATE. RL is promising but has critical weaknesses for live trading: (1) reward shaping is tricky (training for Sharpe vs PnL vs drawdown produces very different policies), (2) sim-to-real gap is large in financial markets, (3) the agent may learn spurious patterns from training data, (4) catastrophic forgetting during regime changes. RL is better suited for execution optimization (given a signal, optimize entry timing and size) than for signal generation.

**Complexity:** Very High

**Proven in crypto:** Academic papers show promise. Real-world deployment is rare due to the challenges above.

---

### 10.2 Transformer-Based Price Prediction

**Core logic:** Apply transformer architectures (attention mechanisms) to financial time series prediction. The self-attention mechanism can capture long-range dependencies and non-stationary patterns. Temporal Fusion Transformer (TFT) and similar architectures incorporate known inputs (time features, exogenous variables) with attention-weighted historical data.

**Citation:** Vaswani, A. et al. (2017). "Attention is All You Need." *NeurIPS*. Applied to finance: Lim, B. et al. (2021). "Temporal Fusion Transformers for Interpretable Multi-Horizon Time Series Forecasting." *International Journal of Forecasting*. Crypto: Wu, Z. et al. (2023). "TimesNet: Temporal 2D-Variation Modeling for General Time Series Analysis."

**Expected profile:**
- Directional accuracy: 55-65% (in-sample; lower out-of-sample)
- Performance degrades significantly with distribution shift (regime changes)
- Requires large training datasets and GPU compute

**Suitability for our system:** LOW. Transformers are powerful for sequence modeling but financial time series present unique challenges: low signal-to-noise ratio, non-stationarity, regime shifts, and adversarial dynamics (the market adapts to learned patterns). For our 49-token universe with ~2 years of 1H data, the training data is insufficient for reliable transformer training. Also, the interpretability is low -- we cannot explain why the model takes a position.

**Complexity:** Very High

**Proven in crypto:** Academic papers show marginal improvements over simpler models when properly validated. Real-world deployment is rare.

---

### 10.3 Graph Neural Networks for Token Relationships

**Core logic:** Model the crypto ecosystem as a graph where tokens are nodes and edges represent relationships (correlation, same-sector, shared liquidity pool, on-chain interactions). A GNN can learn how information propagates through the graph, predicting token returns from the returns of related tokens.

**Citation:** Kipf, T.N. & Welling, M. (2017). "Semi-Supervised Classification with Graph Convolutional Networks." *ICLR*. Applied to finance: Chen, Y. et al. (2022). "Stock Movement Prediction with Graph Neural Networks." Also: Various 2024-2025 papers on crypto GNNs.

**Expected profile:**
- Directional accuracy: 52-60%
- Performance: captures lead-lag relationships between tokens
- Best for: predicting altcoin returns from BTC/ETH movements

**Suitability for our system:** LOW-MODERATE. GNNs could capture the BTC -> ETH -> large-cap alts -> small-cap rotation pattern systematically. However, the graph structure in crypto is unstable (new tokens emerge, old ones die, correlations shift). The primary value would be identifying lead-lag relationships: if token A moves and token B is connected with a lag, enter token B early. This is a form of statistical arbitrage.

**Complexity:** Very High (graph construction, GNN training, feature engineering)

**Proven in crypto:** Emerging research area. Not yet proven for live trading.

---

### 10.4 Causal Inference for Strategy Validation (Lopez de Prado)

**Core logic:** Apply causal inference methods to distinguish genuine causal relationships from spurious correlations in financial data. Key techniques: (1) combinatorial purged cross-validation (CPCV) to avoid temporal leakage, (2) feature importance via mean decrease impurity with corrections, (3) meta-labeling (train a secondary model to predict whether the primary model's signal will be profitable).

**Citation:** Lopez de Prado, M. (2018). *Advances in Financial Machine Learning*. Key concepts: CPCV (Chapter 12), meta-labeling (Chapter 3), feature importance (Chapter 8), bet sizing (Chapter 10).

**Key strategies from the book:**
1. **Triple barrier labeling:** Label each observation with the first barrier hit (profit target, stop loss, or max holding period). This produces three-way labels (win/loss/timeout) instead of binary direction.
2. **Meta-labeling:** Train a secondary model to predict whether the primary model's signal will result in a win. This effectively learns "when to trade" rather than "what direction."
3. **Fractionally differentiated features:** Apply fractional differentiation to price series to make them stationary while preserving memory. This preserves long-term dependencies that standard differencing destroys.
4. **Structural breaks (CUSUM filter):** Detect regime changes using cumulative sum filters. Only generate signals after structural breaks, avoiding signals during stable periods where the edge is lower.

**Suitability for our system:** HIGH. We already use CPCV from Lopez de Prado's framework (Chapter 12). Meta-labeling is directly applicable: train a classifier to predict whether an S09/S11 signal will result in a win, and only take signals with high meta-label confidence. Triple barrier labeling is our existing framework (stop, trail, max hold). Fractional differentiation and CUSUM filters are unexplored.

**Complexity:** Medium-High

**Proven relevance:** CPCV is our primary validation tool. Meta-labeling, fractional differentiation, and CUSUM are unexplored but directly implementable.

---

### 10.5 Machine Learning Alpha Signals That Survive Transaction Costs

**Core logic:** Most ML signals produce raw alpha that is consumed by transaction costs (fees, slippage, market impact). The signals that survive are: (1) low-turnover (change positions infrequently), (2) concentrated on liquid instruments, (3) sized appropriately for market depth. Feature importance studies consistently find that momentum and volatility features dominate, while complex features (sentiment, alternative data) often fail to survive costs.

**Citation:** Gu, S., Kelly, B., & Xiu, D. (2020). "Empirical Asset Pricing via Machine Learning." *Review of Financial Studies*. Also: Freyberger, J., Neuhierl, A., & Weber, M. (2020). "Dissecting Characteristics Nonparametrically."

**Key finding for our system:** The ML literature consistently identifies the SAME features we already use as most important:
1. Past returns (momentum) -- our TSMOM lookback
2. Volatility -- our ATR-based sizing and regime detection
3. Volume -- our vol_ratio filter
4. Trend strength -- our ADX filter

More exotic ML features (NLP sentiment, social media, alternative data) rarely survive transaction costs and out-of-sample testing in academic studies.

**Suitability for our system:** The implication is that our existing feature set is likely optimal. Adding ML complexity (transformers, GNNs) on top of the same features may not improve after-cost returns. The marginal return to model complexity is low when the underlying features are simple momentum/volatility/volume signals.

**Complexity:** N/A (this is a meta-finding about feature selection)

---

## SYNTHESIS: PRIORITY STRATEGIES FOR IMPLEMENTATION

Based on this catalog, our backtest results, and the academic evidence, the strategies are ranked by implementation priority:

### Tier 1: Already Implemented and Validated

| Strategy | Status | Annual PnL |
|----------|--------|-----------|
| S11 Momentum Burst | LIVE (our #1) | +$170K/yr (all 49) |
| S09 Dual Momentum Trend | LIVE (our #2) | +$163K/yr (all 49) |
| V3 Liquidity Contrarian | LIVE (complement) | +$8K/yr |
| HMM Regime Detection | LIVE (overlay) | Integrated |

### Tier 2: High Priority for New Implementation

| Strategy | Rationale | Expected Lift | Complexity |
|----------|-----------|---------------|------------|
| **Volatility scaling (Moreira & Muir)** | Replace discrete regime tiers with continuous vol-targeting | +0.3-0.5 Sharpe | Low |
| **Momentum crash protection (Barroso)** | Dynamic sizing inversely proportional to recent vol | Better drawdowns | Low |
| **DI crossover direction** | Add +DI > -DI as entry condition to S09/S11 | Higher win rate | Low |
| **Volume-weighted TSMOM** | Volume-weighted lookback return for signal | Sharpe 2.17 (literature) | Medium |
| **KAMA (adaptive MA)** | Replace fixed EMAs with market-condition-adaptive MAs | Fewer whipsaws | Medium |
| **Meta-labeling (Lopez de Prado)** | Secondary model predicts which S09/S11 signals will win | +10-20% hit rate | Medium |

### Tier 3: Worth Testing

| Strategy | Rationale | Expected Lift | Complexity |
|----------|-----------|---------------|------------|
| Dynamic strategy allocation by regime | Shift capital between S09/S11/V3 based on regime | +15-30% Sharpe | Medium |
| RS-based token selection | Dynamic token ranking by relative strength vs BTC | Better concentration | Low |
| Sector rotation overlay | Weight sectors by relative momentum | Capture altseason | Medium |
| Pyramiding (add to winners) | Turtle-style adds when trend strengthens | Larger trend capture | Medium |
| Channel breakout (ATR-based) | Reformulated vol breakout with ATR compression | Replace failed vol_breakout | Medium |
| CUSUM structural break filter | Only trade after regime changes detected | Fewer noise trades | Medium |
| Fractional differentiation | Stationary features preserving memory | Better ML inputs | Medium |

### Tier 4: Not Recommended for Our System

| Strategy | Reason |
|----------|--------|
| All mean reversion (swing timeframe) | Consistently loses money in our backtests |
| Cross-sectional momentum | Weak and unreliable in crypto (Han 2023) |
| Pairs trading | Requires shorting; correlation instability |
| OU-based strategies | Crypto is momentum-driven, not OU-revertible |
| Order book imbalance | Too short-term for swing trading |
| Deep RL for signals | High complexity, low out-of-sample reliability |
| Transformer prediction | Insufficient data, non-stationarity, low interpretability |
| RSI-2 / Connors strategies | Too short-term; RSI has IC=0.004 in crypto |
| Vol breakout (BB squeeze) | Failed in backtests; unreliable post-ETF |

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

---

*This catalog is a living document. Update as new strategies are tested and new research emerges.*
*Last backtest validation: February 2026 using 49 tokens, Jan 2024 - Jan 2026 data.*
