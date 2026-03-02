# Exhaustive Indicator Catalog for Crypto Swing Trading

> **TL;DR — All indicators ranked by usefulness**
> - Tier 1: EMA 12/26/50 (trend), ADX (strength, IC=0.067), RSI (extremes only with regime filter)
> - Volume analysis: vol_ratio for confirmation, taker buy ratio for order flow, OBV for accumulation
> - Redundant pairs to avoid: RSI ↔ BB_pct (95% corr), realized_vol ↔ parkinson_vol (99% corr)
> - Crypto-specific: funding rate divergence, liquidation cascades, exchange netflows
> **When to read full file:** Building new entry/exit filters, checking if an indicator exists in the engine
> **Sections:** 1-Trend/Momentum, 2-Mean Reversion, 3-Volatility, 4-Volume, 5-Oscillators, 6-On-Chain, 7-Derivatives, 8-Crypto-Specific, 9-Combination Rules, 10-Empirical IC/Regime Results, 11-Priority Matrix

**System context:** 1H timeframe, 18-720 hour holding periods, cryptocurrency markets.

---

## 1. TREND / MOMENTUM INDICATORS

### 1.1 Classic Trend Indicators

#### EMA Crossovers
- **Formula:** EMA_fast(close, n1) vs EMA_slow(close, n2); signal when fast crosses slow.
- **Causal mechanism:** Exponential weighting captures recent price acceleration. When short-term average exceeds long-term, buying pressure dominates across timeframes, reflecting genuine demand shift rather than noise. The lag acts as a noise filter.
- **Key citation:** Brock, Lakonishok & LeBaron (1992). "Simple Technical Trading Rules and the Stochastic Properties of Stock Returns." *Journal of Finance* 47(5), 1731-1764.
- **1H crypto suitability:** HIGH. Use 12/26 EMA (equivalent to ~12h/26h) or 21/55 EMA for swing trades. Crypto trends persist due to retail herding and 24/7 markets.
- **Signal type:** ENTRY (cross direction), EXIT (adverse cross), FILTER (slope of slow EMA for trend confirmation).

#### MACD (Moving Average Convergence Divergence)
- **Formula:** MACD = EMA(12) - EMA(26); Signal = EMA(MACD, 9); Histogram = MACD - Signal.
- **Causal mechanism:** Measures the rate of change of the trend. Histogram acceleration captures momentum inflection before price reversal. Divergence between MACD and price indicates exhaustion of the current trend's driving force.
- **Key citation:** Appel, G. (2005). *Technical Analysis: Power Tools for Active Investors.* FT Press. Originally developed 1979.
- **1H crypto suitability:** HIGH. Standard parameters work well. Histogram divergence is particularly valuable for spotting trend exhaustion in volatile crypto swings.
- **Signal type:** ENTRY (signal line cross, zero-line cross), EXIT (histogram reversal), FILTER (histogram polarity).

#### ADX / DMI (Average Directional Index / Directional Movement Index)
- **Formula:** +DI = 100 * EMA(+DM, n) / ATR(n); -DI similarly. DX = |+DI - -DI| / (+DI + -DI). ADX = EMA(DX, n). Typical n=14.
- **Causal mechanism:** ADX measures trend strength regardless of direction by quantifying the degree to which price expands its range directionally. High ADX indicates persistent order flow imbalance. DI crossovers identify direction.
- **Key citation:** Wilder, J.W. (1978). *New Concepts in Technical Trading Systems.* Trend Research.
- **1H crypto suitability:** MODERATE-HIGH. ADX > 25 is a strong trend filter. Crypto's regime-switching nature (trending vs ranging) makes ADX valuable for strategy selection. Use 14-period on 1H.
- **Signal type:** FILTER (ADX level for trend strength), ENTRY (DI cross when ADX > 20), SIZING (scale position with ADX strength).

#### Aroon Indicator
- **Formula:** Aroon Up = ((n - periods since n-period high) / n) * 100. Aroon Down similarly for lows. Aroon Oscillator = Up - Down. n typically 25.
- **Causal mechanism:** Measures how recently price made a new high or low. Recent new highs suggest sustained buying pressure from institutional participants; the recency of extremes is a proxy for order flow persistence.
- **Key citation:** Chande, T. (1994). "A Time Price Oscillator." *Technical Analysis of Stocks & Commodities* 13(9).
- **1H crypto suitability:** MODERATE. Works for identifying trend onset. Aroon > 70 / < 30 thresholds identify strong trends. Less noisy than some alternatives on 1H.
- **Signal type:** ENTRY (Aroon Up crosses above 70), FILTER (oscillator polarity), EXIT (Aroon Down crosses above 70).

#### Parabolic SAR (Stop and Reverse)
- **Formula:** SAR(t+1) = SAR(t) + AF * (EP - SAR(t)), where AF starts at 0.02, increments by 0.02 to max 0.20, EP = extreme point.
- **Causal mechanism:** Trailing stop that accelerates as the trend continues. The parabolic acceleration models the empirical observation that trends accelerate before reversing (buying climax / selling climax). Price crossing SAR signals exhaustion.
- **Key citation:** Wilder (1978). *New Concepts in Technical Trading Systems.*
- **1H crypto suitability:** MODERATE. Good for trailing exits in strong trends. Performs poorly in ranging markets. Consider AF=0.01 start for 1H crypto (lower sensitivity to reduce whipsaws in volatile sessions).
- **Signal type:** EXIT (primary use as trailing stop), ENTRY (SAR flip direction).

#### Supertrend
- **Formula:** Upper Band = (High + Low)/2 + Multiplier * ATR(n). Lower Band = (High + Low)/2 - Multiplier * ATR(n). Trend flips when price crosses a band. Typical: n=10, multiplier=3.
- **Causal mechanism:** Volatility-adaptive trend following. By using ATR-scaled bands, it adapts to the current volatility regime, avoiding false signals in high-vol environments and remaining sensitive in low-vol. The ATR multiplier acts as a noise threshold.
- **Key citation:** Pring, M.J. (2002). *Technical Analysis Explained.* McGraw-Hill. (Popularized by Olivier Seban.)
- **1H crypto suitability:** HIGH. ATR-adaptive nature handles crypto's varying volatility well. Multiplier=3, period=10 on 1H is a strong baseline. Very popular in crypto algo trading.
- **Signal type:** ENTRY (trend flip), EXIT (adverse flip), FILTER (current trend direction).

### 1.2 Modern / Adaptive Trend Indicators

#### KAMA (Kaufman Adaptive Moving Average)
- **Formula:** ER = |close - close[n]| / sum(|close - close[-1]|, n). SC = (ER * (fast_sc - slow_sc) + slow_sc)^2. KAMA(t) = KAMA(t-1) + SC * (close - KAMA(t-1)). fast_sc = 2/(2+1), slow_sc = 2/(30+1).
- **Causal mechanism:** Adapts smoothing to market efficiency. In trending markets (high ER), KAMA tracks price closely; in noisy markets (low ER), it barely moves. This models the empirical finding that signal-to-noise varies across regimes, and optimal lookback should adapt.
- **Key citation:** Kaufman, P. (1995). *Smarter Trading.* McGraw-Hill.
- **1H crypto suitability:** HIGH. Crypto's regime-switching between strong trends and noisy ranges makes adaptive MAs particularly valuable. KAMA reduces whipsaws vs fixed EMA.
- **Signal type:** ENTRY (price cross KAMA), FILTER (KAMA slope), EXIT (price cross KAMA adversely).

#### Hull Moving Average (HMA)
- **Formula:** HMA(n) = WMA(2 * WMA(n/2) - WMA(n), sqrt(n)).
- **Causal mechanism:** Reduces lag while maintaining smoothness by using weighted moving averages of half-period WMAs. The sqrt(n) final smoothing provides a near-zero-lag trend estimate, capturing momentum shifts earlier than traditional MAs.
- **Key citation:** Hull, A. (2005). "Hull Moving Average." *Alanhull.com.*
- **1H crypto suitability:** HIGH. The low-lag property is valuable in fast-moving crypto markets. HMA(20) on 1H provides responsive trend signals without excessive noise.
- **Signal type:** ENTRY (HMA direction change), FILTER (HMA slope), EXIT (HMA flattening/reversal).

#### MESA Adaptive Moving Average (MAMA)
- **Formula:** Uses Hilbert Transform to estimate the dominant cycle period, then adapts the alpha of the moving average. MAMA = MAMA[-1] + alpha * (price - MAMA[-1]), where alpha adapts based on the phase rate of the dominant cycle.
- **Causal mechanism:** Markets exhibit cycles at varying frequencies. By estimating the dominant cycle in real-time via the Hilbert Transform, the MA adapts its period to match current market structure. This provides theoretically optimal noise reduction for the current regime.
- **Key citation:** Ehlers, J.F. (2001). *Rocket Science for Traders.* Wiley.
- **1H crypto suitability:** MODERATE. Theoretically sound but complex to implement and tune. Crypto markets may not exhibit stable enough cycles for the Hilbert Transform to lock on reliably on 1H.
- **Signal type:** ENTRY (MAMA/FAMA crossover), FILTER (dominant cycle period for regime identification).

#### Ehlers Filters (Super Smoother, Roofing Filter, Decycler)
- **Formula:** Super Smoother: 2-pole Butterworth filter. Roofing Filter: highpass to remove trend + super smoother to remove noise. Decycler: EMA - EMA of EMA, isolating cycle components.
- **Causal mechanism:** DSP-based approach treats price as a signal with trend, cycle, and noise components. By isolating specific frequency bands, these filters extract tradeable oscillations while rejecting noise. The Nyquist constraint informs minimum observable cycle period.
- **Key citation:** Ehlers, J.F. (2013). *Cycle Analytics for Traders.* Wiley.
- **1H crypto suitability:** MODERATE-HIGH. The super smoother is an excellent drop-in replacement for traditional MAs with less lag. Roofing filter can extract swing-tradeable cycles. Requires careful parameter selection.
- **Signal type:** ENTRY (filtered signal direction change), FILTER (cycle component amplitude).

### 1.3 Research-Backed Momentum

#### Time-Series Momentum (TSMOM)
- **Formula:** Signal = sign(r_{t-K,t}) where r is the return over lookback K (typically 1-12 months). Position = signal * vol_target / realized_vol.
- **Causal mechanism:** Three mechanisms: (1) Gradual information diffusion -- news is incorporated slowly due to investor inattention and disagreement. (2) Behavioral biases -- initial underreaction (anchoring, conservatism) followed by delayed overreaction (herding, confirmation bias). (3) Hedging pressure in futures markets creates persistent return predictability.
- **Key citation:** Moskowitz, T., Ooi, Y.H. & Pedersen, L.H. (2012). "Time Series Momentum." *Journal of Financial Economics* 104(2), 228-250.
- **1H crypto suitability:** HIGH. Use hourly lookbacks: 24h, 48h, 168h (1 week), 720h (1 month). Crypto exhibits strong TSMOM due to retail herding, information asymmetry, and 24/7 news cycle. Vol-scaling is essential given crypto's volatility clustering.
- **Signal type:** ENTRY (sign of lookback return), SIZING (inverse vol scaling).

#### Cross-Sectional Momentum
- **Formula:** Rank assets by past K-period returns. Long top quintile, short bottom quintile. Typical K = 3-12 months for equities.
- **Causal mechanism:** Winners continue outperforming losers due to: (1) Slow-moving institutional capital allocation. (2) Momentum traders amplifying trends. (3) Disposition effect -- investors sell winners too early and hold losers, creating predictable return patterns.
- **Key citation:** Jegadeesh, N. & Titman, S. (1993). "Returns to Buying Winners and Selling Losers." *Journal of Finance* 48(1), 65-91.
- **1H crypto suitability:** HIGH. Rank crypto universe by 24h-168h returns. Go long relative outperformers. Particularly powerful during alt-season rotations. Skip periods help avoid short-term reversal (skip 1-4 hours).
- **Signal type:** ENTRY (top-ranked assets), FILTER (universe selection), SIZING (rank-weighted allocation).

#### Dual Momentum
- **Formula:** Combine absolute momentum (TSMOM > 0) AND relative momentum (asset outperforms benchmark). Only enter if both conditions are met, otherwise hold risk-free.
- **Causal mechanism:** Absolute momentum provides a bear market filter (avoids positions during drawdowns), while relative momentum selects the strongest assets. The combination captures the behavioral momentum premium while providing crash protection.
- **Key citation:** Antonacci, G. (2014). *Dual Momentum Investing.* McGraw-Hill.
- **1H crypto suitability:** HIGH. Absolute momentum: asset return > 0 over 168h. Relative momentum: asset outperforms BTC over 168h. Stablecoin allocation when both fail. Very effective risk management for crypto.
- **Signal type:** ENTRY (both momentum conditions met), EXIT (either condition fails), FILTER (absolute momentum as market regime).

### 1.4 Crypto-Specific Momentum

#### Funding Rate Momentum
- **Formula:** Signal = EMA(funding_rate, n) or cumulative funding over K periods. Contrarian: extreme positive funding -> short, extreme negative -> long.
- **Causal mechanism:** Funding rate reflects the cost of leveraged directional bets. Extreme positive funding means longs are paying shorts heavily, indicating crowded long positioning that is unsustainable. The cost of carry eventually forces liquidation, creating mean reversion. Moderate positive funding confirms healthy uptrend.
- **Key citation:** Alexander, C. & Heck, D. (2020). "Price Discovery in Bitcoin: The Role of Perpetual Swaps." *Journal of Futures Markets*.
- **1H crypto suitability:** HIGH. Collect 8-hourly funding rates from perpetual futures. Extreme funding (>0.1%) is a contrarian exit/filter signal. Moderate positive funding confirms trend.
- **Signal type:** FILTER (extreme funding as contrarian warning), EXIT (funding spike), SIZING (reduce size at extreme funding).

#### Open Interest Momentum
- **Formula:** OI_change = (OI(t) - OI(t-n)) / OI(t-n). Signal: rising OI + rising price = trend confirmation; rising OI + falling price = distribution.
- **Causal mechanism:** Open interest represents the total capital committed to derivative positions. Rising OI with price = new money entering the trend (genuine conviction). Rising OI against price = aggressive shorting/hedging that can fuel short squeezes or indicates informed selling.
- **Key citation:** Bybit Research (2021). "Open Interest and Its Implications for Bitcoin Trading." See also: Garcia et al. (2014). "The Digital Traces of Bubbles: Feedback Cycles between Socio-Economic Signals in the Bitcoin Economy." *JRSI*.
- **1H crypto suitability:** HIGH. OI data available from exchanges at hourly frequency. OI divergence from price is one of the strongest crypto-specific signals.
- **Signal type:** FILTER (OI confirmation of trend), ENTRY (OI divergence setups), EXIT (OI collapse during trend).

#### Exchange Flow Momentum
- **Formula:** Net exchange flow = inflow - outflow (BTC/ETH). Signal: large net inflows indicate selling pressure (depositing to sell); net outflows indicate accumulation.
- **Causal mechanism:** Coins must be deposited to exchanges to be sold. Large inflows from whale wallets to exchanges precede sell pressure. Outflows indicate coins being moved to cold storage (long-term holding conviction). This is a direct measure of supply-side pressure.
- **Key citation:** Glassnode Research (2020). "Exchange Flows and Their Market Impact." See also: Anouar & Ausloos (2020).
- **1H crypto suitability:** MODERATE. On-chain data has variable latency (1-6 block confirmations). Better as a 4H+ filter. Useful for BTC/ETH, less reliable for smaller tokens with lower on-chain activity.
- **Signal type:** FILTER (net flow direction), EXIT (large inflow spike), ENTRY (sustained outflow pattern).

---

## 2. MEAN REVERSION / OSCILLATORS

### 2.1 Classic Oscillators

#### RSI (Relative Strength Index)
- **Formula:** RSI = 100 - 100 / (1 + RS), where RS = EMA(gains, n) / EMA(losses, n), n=14.
- **Causal mechanism:** Measures the proportion of recent price movement that is upward. Extreme readings indicate exhaustion of directional order flow. At RSI > 70, buyers are exhausted; at RSI < 30, selling pressure is spent. The mechanism is crowd positioning -- when nearly all recent activity is one-directional, the remaining pool of participants to continue the move is depleted.
- **Key citation:** Wilder (1978). *New Concepts in Technical Trading Systems.* Empirically validated: Chong & Ng (2008). "Technical analysis and the London stock exchange." *Applied Financial Economics.*
- **1H crypto suitability:** HIGH. RSI(14) on 1H is a standard tool. In crypto, RSI > 80 and RSI < 20 are more appropriate thresholds due to stronger trends. RSI divergence (price makes new high, RSI doesn't) is particularly powerful.
- **Signal type:** ENTRY (extreme readings for mean reversion), FILTER (avoid overbought for new longs), EXIT (RSI divergence).

#### Stochastic Oscillator (%K, %D)
- **Formula:** %K = (close - lowest_low(n)) / (highest_high(n) - lowest_low(n)) * 100. %D = SMA(%K, 3). n=14 typical.
- **Causal mechanism:** In uptrends, closes tend to occur near the high of the range (buying pressure into the close). When closes migrate toward the low despite an uptrend, it signals distribution -- smart money is selling into retail buying. The oscillator captures this shift in close location within the range.
- **Key citation:** Lane, G. (1984). "Lane's Stochastics." *Technical Analysis of Stocks & Commodities* 2(3).
- **1H crypto suitability:** HIGH. Stochastic(14,3,3) on 1H. In crypto's 24/7 market, the "close" is simply the hourly close. Fast stochastic captures quick mean-reversion opportunities. Use with trend filter.
- **Signal type:** ENTRY (%K/%D cross in extreme zone), EXIT (cross in opposite extreme), FILTER (overbought/oversold zones).

#### CCI (Commodity Channel Index)
- **Formula:** CCI = (Typical_Price - SMA(TP, n)) / (0.015 * Mean_Deviation(TP, n)), where TP = (H+L+C)/3.
- **Causal mechanism:** Measures how far price has deviated from its statistical mean, normalized by the mean absolute deviation. Extreme readings (>100 or <-100) indicate price has moved beyond normal variation, which in efficient markets should be temporary. The 0.015 constant ensures ~75% of readings fall between -100 and +100.
- **Key citation:** Lambert, D. (1980). "Commodity Channel Index: Tools for Trading Cyclic Trends." *Commodities Magazine.*
- **1H crypto suitability:** MODERATE. CCI(20) on 1H. In crypto, CCI > 200 or < -200 are better extreme thresholds. Useful for identifying cycle turning points.
- **Signal type:** ENTRY (return from extreme zone), FILTER (extreme CCI as overextension warning).

#### Williams %R
- **Formula:** %R = (highest_high(n) - close) / (highest_high(n) - lowest_low(n)) * -100. n=14.
- **Causal mechanism:** Identical to inverted stochastic %K. Measures close position within the recent range. Values near 0 indicate close near the high (overbought), near -100 indicate close near the low (oversold).
- **Key citation:** Williams, L. (1979). *How I Made One Million Dollars Last Year Trading Commodities.* Windsor Books.
- **1H crypto suitability:** MODERATE. Redundant with Stochastic. If used, apply same logic. Williams %R(14) on 1H with -20/-80 extremes.
- **Signal type:** ENTRY (extreme zone reversal), FILTER.

#### ROC (Rate of Change) / Momentum
- **Formula:** ROC = (close - close[n]) / close[n] * 100. n typically 10-20.
- **Causal mechanism:** Pure momentum measurement. Positive and increasing ROC indicates accelerating buying. ROC divergence from price indicates weakening momentum (the trend is maintained by fewer participants). Zero-line crossovers confirm trend direction.
- **Key citation:** Pring, M.J. (2002). *Technical Analysis Explained.* Also: Levy, R. (1967). "Relative Strength as a Criterion for Investment Selection." *Journal of Finance.*
- **1H crypto suitability:** HIGH. ROC(24) = 24-hour return, ROC(168) = 1-week return. Simple but effective for crypto momentum.
- **Signal type:** ENTRY (extreme ROC for reversal, moderate ROC for momentum), FILTER (ROC sign), SIZING (ROC magnitude).

### 2.2 Bollinger Band Derivatives

#### %B (Bollinger Band Percent)
- **Formula:** %B = (close - lower_band) / (upper_band - lower_band). Bands = SMA(20) +/- 2*stdev(20).
- **Causal mechanism:** Normalizes price position within the volatility envelope. %B > 1 means price has broken above the upper band (statistical anomaly suggesting overextension). %B < 0 means below lower band. In mean-reverting regimes, extremes revert; in trending regimes, %B > 0.5 confirms trend.
- **Key citation:** Bollinger, J. (2001). *Bollinger on Bollinger Bands.* McGraw-Hill.
- **1H crypto suitability:** HIGH. %B on 1H with SMA(20), 2 stdev. Crypto tends to ride the upper/lower band in trends, so %B is better as a filter than a standalone entry signal.
- **Signal type:** ENTRY (mean reversion when %B < 0 or > 1 with confirmation), FILTER (%B > 0.5 for bullish bias).

#### Bollinger Bandwidth
- **Formula:** Bandwidth = (upper_band - lower_band) / middle_band * 100.
- **Causal mechanism:** Measures relative volatility. Low bandwidth = volatility compression = energy building for a directional move. This is rooted in the empirical observation that volatility mean-reverts and clusters: low vol begets high vol (and vice versa). The "squeeze" identifies periods where a breakout is statistically likely.
- **Key citation:** Bollinger (2001). Also: Mandelbrot, B. (1963). "The Variation of Certain Speculative Prices." *Journal of Business* (volatility clustering).
- **1H crypto suitability:** HIGH. Bandwidth percentile rank over 100-period lookback identifies squeezes. Crypto squeezes on 1H often precede 5-15% moves.
- **Signal type:** FILTER (low bandwidth = impending breakout, prepare for entry), SIZING (reduce in high bandwidth/extreme vol).

#### Bollinger Band Squeeze (TTM Squeeze)
- **Formula:** Squeeze = Bollinger Bands inside Keltner Channels. Momentum = close - midline of highest(high,n)/lowest(low,n) channel, smoothed.
- **Causal mechanism:** When Bollinger Bands contract within Keltner Channels, volatility has compressed below its historical norm (relative to ATR). The release from squeeze indicates volatility expansion. The momentum histogram within the squeeze indicates the likely breakout direction.
- **Key citation:** Carter, J.F. (2012). *Mastering the Trade.* McGraw-Hill. (TTM Squeeze concept.)
- **1H crypto suitability:** HIGH. One of the most effective crypto setups on 1H. Squeeze + momentum direction predicts breakout direction with reasonable accuracy. Combine with volume confirmation.
- **Signal type:** ENTRY (squeeze release + momentum direction), FILTER (squeeze active = wait for resolution).

### 2.3 Research-Based Mean Reversion

#### Ornstein-Uhlenbeck Half-Life
- **Formula:** Run regression: delta(price) = a + b * price[-1] + epsilon. Half-life = -ln(2) / b. Only valid if b < 0 (mean reverting).
- **Causal mechanism:** If a price series follows an OU process (stationary, mean-reverting), the half-life estimates how quickly deviations from the mean are corrected. Short half-lives indicate strong mean reversion (possibly driven by arbitrageurs or market makers providing liquidity). Long half-lives suggest persistent trends.
- **Key citation:** Chan, E. (2013). *Algorithmic Trading.* Wiley. Uhlenbeck & Ornstein (1930). "On the Theory of Brownian Motion." *Physical Review.*
- **1H crypto suitability:** HIGH. Estimate OU half-life on rolling windows (100-500 bars). If half-life is 18-200 hours, the series is suitable for swing mean reversion. If half-life is very short (<6 hours) or very long (>1000 hours), different strategies apply.
- **Signal type:** FILTER (half-life determines strategy type -- mean reversion vs momentum), SIZING (shorter half-life = higher confidence in mean reversion).

#### Hurst Exponent
- **Formula:** H estimated via rescaled range (R/S) analysis, DFA, or variance ratio. H < 0.5 = mean reverting, H = 0.5 = random walk, H > 0.5 = trending.
- **Causal mechanism:** Measures the long-range dependence in a time series. H > 0.5 indicates persistent series (trends continue) due to positive autocorrelation from herding behavior. H < 0.5 indicates anti-persistent series (reversals likely) due to overreactive mean reversion by market participants.
- **Key citation:** Hurst, H.E. (1951). "Long-term Storage Capacity of Reservoirs." *Transactions of the American Society of Civil Engineers.* Applied to finance: Mandelbrot & Wallis (1969). Peters, E. (1994). *Fractal Market Analysis.*
- **1H crypto suitability:** HIGH. Compute rolling Hurst exponent (window 100-500 bars). Most crypto assets show H > 0.5 (trending) during bull markets and H near 0.5 during consolidation. Use to dynamically switch between momentum and mean-reversion strategies.
- **Signal type:** FILTER (H > 0.55 -> momentum strategies, H < 0.45 -> mean reversion strategies), SIZING.

#### Variance Ratio Test
- **Formula:** VR(q) = Var(r_q) / (q * Var(r_1)), where r_q is q-period return. VR > 1 = trending, VR < 1 = mean reverting.
- **Causal mechanism:** Under random walk, multi-period variance scales linearly with holding period. Departures indicate predictable structure: VR > 1 means positive autocorrelation (momentum), VR < 1 means negative autocorrelation (reversal). Directly tests the trading hypothesis.
- **Key citation:** Lo, A.W. & MacKinlay, A.C. (1988). "Stock Market Prices Do Not Follow Random Walks." *Review of Financial Studies* 1(1), 41-66.
- **1H crypto suitability:** HIGH. Compute VR at q=6, 12, 24, 48, 168 hours on rolling windows. Provides direct evidence of exploitable structure at each horizon.
- **Signal type:** FILTER (VR regime determines strategy selection).

### 2.4 Modern Oscillators

#### ConnorsRSI
- **Formula:** ConnorsRSI = (RSI(close,3) + RSI(streak,2) + PercentRank(ROC(1),100)) / 3. Streak = consecutive up/down closes.
- **Causal mechanism:** Combines three complementary mean-reversion signals: short-term RSI (directional exhaustion), streak RSI (consecutive move exhaustion), and return percentile (statistical extremity). The combination reduces false signals from any single component.
- **Key citation:** Connors, L. & Alvarez, C. (2012). *Short-Term Trading Strategies That Work.* TradingMarkets.
- **1H crypto suitability:** MODERATE-HIGH. Very short-term (RSI period 3), so on 1H it captures 3-hour momentum exhaustion. Good for identifying entry points within larger trends.
- **Signal type:** ENTRY (extreme ConnorsRSI for mean reversion), EXIT.

#### Dynamic / Adaptive RSI
- **Formula:** RSI with adaptive period: n = f(volatility) or n = f(dominant_cycle_period). Low vol -> longer RSI period; high vol -> shorter period.
- **Causal mechanism:** Fixed-period RSI becomes inappropriate when market regime changes. In high-volatility regimes, a shorter RSI captures rapid reversals; in low-volatility regimes, a longer RSI avoids noise-driven false signals. Adapting the period to market conditions improves signal quality.
- **Key citation:** Ehlers, J.F. (2013). *Cycle Analytics for Traders.* Chande & Kroll (1994). *The New Technical Trader.*
- **1H crypto suitability:** HIGH. Crypto's extreme vol regime shifts make adaptive oscillators particularly valuable. Use ATR percentile to scale RSI period between 7 and 28.
- **Signal type:** ENTRY (adaptive RSI extremes), FILTER.

---

## 3. VOLUME / LIQUIDITY INDICATORS

### 3.1 Classic Volume Indicators

#### OBV (On-Balance Volume)
- **Formula:** OBV(t) = OBV(t-1) + sign(close(t) - close(t-1)) * volume(t).
- **Causal mechanism:** Cumulates volume on up-closes and subtracts on down-closes. The premise: volume precedes price. Smart money accumulates (buying on up-closes) before prices rise. OBV divergence from price (OBV rising while price is flat) indicates stealth accumulation. The indicator captures the pressure of informed flow.
- **Key citation:** Granville, J. (1963). *Granville's New Key to Stock Market Profits.* Prentice-Hall.
- **1H crypto suitability:** HIGH. OBV works well with crypto's transparent volume data. OBV divergence on 1H is a strong precursor to swing moves. Watch for OBV breakouts preceding price breakouts.
- **Signal type:** ENTRY (OBV divergence, OBV breakout), FILTER (OBV trend confirmation), EXIT (OBV divergence against position).

#### VWAP (Volume Weighted Average Price)
- **Formula:** VWAP = cumsum(price * volume) / cumsum(volume), typically reset daily (or session-based).
- **Causal mechanism:** VWAP represents the average price at which volume transacted, i.e., the average cost basis of participants. Price above VWAP means most recent buyers are profitable (bullish disposition), below VWAP means most are underwater (bearish pressure). Institutions use VWAP as a benchmark for execution quality.
- **Key citation:** Berkowitz, S.A., Logue, D.E. & Noser, E.A. (1988). "The Total Cost of Transactions on the NYSE." *Journal of Finance.*
- **1H crypto suitability:** MODERATE. Crypto has no "session" so VWAP anchoring is ambiguous. Use rolling VWAP (e.g., 24h rolling) or anchor to significant events. Most useful for BTC/ETH where institutional participation is high.
- **Signal type:** FILTER (price vs VWAP for directional bias), ENTRY (bounce off VWAP in trending market).

#### MFI (Money Flow Index)
- **Formula:** MFI = 100 - 100 / (1 + positive_flow / negative_flow). Positive flow = TP * volume when TP > TP[-1]. TP = (H+L+C)/3. n=14.
- **Causal mechanism:** Volume-weighted RSI. Incorporates the conviction (volume) behind price movements. High MFI with rising price = strong buying conviction. MFI divergence (price rising, MFI falling) indicates declining volume participation in the rally -- a sign of exhaustion.
- **Key citation:** Quong, G. & Soudack, A. (1989). "Volume Weighted RSI: Money Flow." *Technical Analysis of Stocks & Commodities.*
- **1H crypto suitability:** HIGH. MFI(14) on 1H. Volume-weighting makes MFI more reliable than raw RSI in crypto, where volume spikes carry significant information. MFI > 80 / < 20 for extremes.
- **Signal type:** ENTRY (MFI extremes), EXIT (MFI divergence), FILTER.

#### Accumulation/Distribution Line (A/D)
- **Formula:** CLV = ((close - low) - (high - close)) / (high - low). A/D = A/D[-1] + CLV * volume.
- **Causal mechanism:** Measures where the close falls within the bar's range, weighted by volume. Close near the high = accumulation (buyers in control at close). Close near the low = distribution (sellers dominating). Unlike OBV, it accounts for the degree of buying/selling pressure within each bar.
- **Key citation:** Williams, L. (1972). *How I Made One Million Dollars.* Chaikin, M. (developed the A/D variant).
- **1H crypto suitability:** MODERATE-HIGH. A/D divergence from price on 1H captures distribution before selloffs. More nuanced than OBV.
- **Signal type:** FILTER (A/D trend vs price trend confirmation), ENTRY (A/D divergence setup).

#### CMF (Chaikin Money Flow)
- **Formula:** CMF = sum(CLV * volume, n) / sum(volume, n), where n=20.
- **Causal mechanism:** Bounded version of A/D line. Positive CMF = buying pressure dominates over n periods (closes consistently near highs with volume). Negative CMF = selling pressure. The bounded nature (-1 to +1) makes it easier to identify extremes.
- **Key citation:** Chaikin, M. (1986). "Chaikin Money Flow." *Bomar Securities.*
- **1H crypto suitability:** MODERATE-HIGH. CMF(20) on 1H. Cross of zero line with trend confirms trend health. Divergence is valuable.
- **Signal type:** FILTER (CMF polarity for trend confirmation), ENTRY (CMF zero-line cross with momentum).

### 3.2 Modern Volume / Microstructure Indicators

#### VPIN (Volume-Synchronized Probability of Informed Trading)
- **Formula:** Classify volume into buy/sell using bulk volume classification (BVC). VPIN = sum(|V_buy - V_sell|) / (n * V_bucket). Computed over volume-time (fixed-volume buckets rather than fixed-time bars).
- **Causal mechanism:** Measures the probability that trading is driven by informed traders. High VPIN indicates toxic order flow -- informed traders are active, and market makers face adverse selection. Historically, VPIN spikes precede large price moves and liquidity crises (e.g., Flash Crash 2010).
- **Key citation:** Easley, D., Lopez de Prado, M. & O'Hara, M. (2012). "Flow Toxicity and Liquidity in a High-Frequency World." *Review of Financial Studies* 25(5), 1457-1493.
- **1H crypto suitability:** MODERATE. Requires tick/trade-level data for proper implementation. Can be approximated using 1-minute bars. High VPIN on crypto exchanges precedes large moves.
- **Signal type:** FILTER (high VPIN = increase caution / reduce size), SIZING (inverse VPIN scaling).

#### Kyle's Lambda
- **Formula:** Lambda from regression: delta_price = lambda * sign(OFI) * sqrt(|OFI|) + epsilon, where OFI = order flow imbalance. Alternatively: lambda = delta_price / delta_volume (price impact per unit volume).
- **Causal mechanism:** Measures permanent price impact of trading -- how much informed trading moves the price per dollar of flow. High lambda = illiquid market where informed traders have large impact. Changes in lambda indicate shifts in the information environment or liquidity provision.
- **Key citation:** Kyle, A. (1985). "Continuous Auctions and Insider Trading." *Econometrica* 53(6), 1315-1335.
- **1H crypto suitability:** MODERATE. Requires order flow data. Can estimate from 1-minute price/volume data. Higher lambda on altcoins makes them more susceptible to whale manipulation.
- **Signal type:** FILTER (high lambda = avoid, illiquid), SIZING (inverse lambda for position sizing).

#### Amihud Illiquidity Ratio
- **Formula:** ILLIQ = |r_t| / (Volume_t * Price_t). Average over n periods. Higher = more illiquid.
- **Causal mechanism:** Captures the price movement per unit of dollar volume traded. In illiquid markets, small volume moves price significantly, indicating thin order books and higher adverse selection risk. Illiquidity is a priced risk factor (Amihud 2002).
- **Key citation:** Amihud, Y. (2002). "Illiquidity and Stock Returns: Cross-Section and Time-Series Effects." *Journal of Financial Markets* 5(1), 31-56.
- **1H crypto suitability:** HIGH. Easy to compute from OHLCV data. Rolling Amihud ratio on 1H identifies liquidity regimes. High Amihud = reduce position size or avoid. Low Amihud = can take larger positions.
- **Signal type:** FILTER (liquidity threshold), SIZING (inverse Amihud scaling).

### 3.3 Order Flow Indicators

#### Delta Volume (Buy-Sell Imbalance)
- **Formula:** Delta = Buy_volume - Sell_volume per bar. Cumulative Delta = running sum of delta.
- **Causal mechanism:** Directly measures the net aggression of market participants. Taker buy volume represents urgency to buy (lifting the ask); taker sell volume represents urgency to sell (hitting the bid). Net positive delta = buy-side aggression dominates.
- **Key citation:** Dalton, J., Jones, E. & Dalton, R. (1993). *Mind Over Markets.* (Market Profile and order flow concepts.)
- **1H crypto suitability:** HIGH. Many crypto exchanges provide taker buy/sell volume directly. Cumulative delta divergence from price is a powerful crypto signal.
- **Signal type:** ENTRY (delta divergence), FILTER (delta confirmation of trend), EXIT (delta reversal).

#### Footprint / Volume Profile
- **Formula:** Aggregate volume at each price level within a bar (or session). Identify high-volume nodes (HVN) and low-volume nodes (LVN). Point of Control (POC) = price with highest volume.
- **Causal mechanism:** HVNs act as support/resistance because large volumes transacted there create a "fair value" consensus. LVNs represent price levels that the market moved through quickly (lack of acceptance), which become breakout zones. POC migration reveals shifting institutional interest.
- **Key citation:** Steidlmayer, P. (1986). *Markets and Market Logic.* (Market Profile originator.)
- **1H crypto suitability:** MODERATE. Requires tick data for proper volume profile. Simplified version using hourly OHLCV is possible but less granular. Very effective for BTC/ETH on larger exchanges.
- **Signal type:** ENTRY (bounce off HVN), EXIT (target opposite HVN), FILTER (POC location relative to price).

### 3.4 Crypto-Specific Volume Indicators

#### Taker Buy Ratio
- **Formula:** TBR = Taker_Buy_Volume / Total_Volume. TBR > 0.5 = buy aggression dominates.
- **Causal mechanism:** Taker buy ratio directly measures who is crossing the spread. Persistent TBR > 0.55 indicates sustained buy-side urgency, often from informed participants or momentum traders. TBR drops below 0.45 before sell-offs as informed sellers step in.
- **Key citation:** CryptoQuant research (2021). Also: Hasbrouck, J. & Saar, G. (2013) for general trade classification methodology.
- **1H crypto suitability:** HIGH. Available from most major exchanges. One of the most direct and useful crypto-specific indicators. Rolling average TBR(24) smooths noise.
- **Signal type:** ENTRY (TBR breakout above 0.55 with price confirmation), FILTER (TBR direction), EXIT (TBR collapse).

#### Exchange Netflow
- **Formula:** Netflow = Exchange_Inflow - Exchange_Outflow (in BTC/ETH units). Rolling sum over n periods.
- **Causal mechanism:** Coins deposited to exchanges are likely to be sold (exchange is needed to convert to fiat or other assets). Coins withdrawn are being moved to cold storage (long-term holding intent). Net negative flow = supply contraction on exchanges -> bullish. Net positive flow = supply expansion -> bearish.
- **Key citation:** Glassnode Academy. Also: Yin & Zhang (2021). "The impact of cryptocurrency fund flows on Bitcoin prices." *Economics Letters.*
- **1H crypto suitability:** MODERATE. On-chain data has latency (block confirmations). Better as a daily/4H indicator aggregated to 1H signals. Most relevant for BTC and ETH.
- **Signal type:** FILTER (7-day cumulative netflow direction), ENTRY (extreme outflow events).

#### Whale Transaction Count
- **Formula:** Count of transactions exceeding a threshold (e.g., >$1M, >$10M) per period. Rolling average over n periods.
- **Causal mechanism:** Large transactions indicate institutional or whale activity. Spikes in whale transactions often precede significant price movements. The direction of whale flow (to/from exchanges) combined with count provides a proxy for informed money activity.
- **Key citation:** Blockchain.com analytics. Chainalysis (2020). "Whale Watching: Understanding Large Bitcoin Transactions."
- **1H crypto suitability:** MODERATE. Available from on-chain data providers. Spike detection on rolling basis. Useful as a volatility/event filter.
- **Signal type:** FILTER (spike in whale txs = prepare for vol), SIZING (reduce size during whale activity spikes if direction uncertain).

---

## 4. VOLATILITY INDICATORS

### 4.1 Classic Volatility Indicators

#### ATR (Average True Range)
- **Formula:** TR = max(high - low, |high - close[-1]|, |low - close[-1]|). ATR = EMA(TR, n), n=14.
- **Causal mechanism:** Measures the average range of price movement, accounting for gaps. ATR captures the "normal" amplitude of price fluctuations. Positions sized by ATR normalize risk across assets with different volatilities. ATR expansion indicates regime change (breakout/panic); ATR contraction indicates consolidation.
- **Key citation:** Wilder (1978). *New Concepts in Technical Trading Systems.* Applications: Van Tharp (2006). *Trade Your Way to Financial Freedom.*
- **1H crypto suitability:** HIGH. Essential for crypto position sizing. ATR(14) on 1H provides the volatility-normalized unit for stop placement (e.g., 2 * ATR stop). ATR percentile rank identifies vol regime.
- **Signal type:** SIZING (ATR-based position sizing is foundational), FILTER (ATR expansion for breakout confirmation), EXIT (ATR-multiple trailing stops).

#### Bollinger Bandwidth
- **Formula:** See Section 2.2 above.
- **Signal type:** FILTER, SIZING.

#### Keltner Channel Width
- **Formula:** Upper = EMA(20) + 2 * ATR(10). Lower = EMA(20) - 2 * ATR(10). Width = (Upper - Lower) / EMA(20).
- **Causal mechanism:** ATR-based envelope around the trend. More stable than Bollinger Bands (ATR is smoother than standard deviation). Width measures volatility relative to price level. Expanding width = volatility expansion, contracting = compression.
- **Key citation:** Keltner, C. (1960). *How to Make Money in Commodities.* Modified by Stallman using ATR.
- **1H crypto suitability:** HIGH. Keltner Channels are often preferred over Bollinger in crypto because ATR handles gaps and wicks better. Width percentile is a good vol regime indicator.
- **Signal type:** FILTER (channel width for vol regime), ENTRY (price touching channel boundaries with reversal signal).

### 4.2 Modern Volatility Estimators

#### Realized Volatility (Close-to-Close)
- **Formula:** RV = sqrt(sum(r_t^2, n)) * sqrt(annualization_factor), where r_t = ln(close_t / close_{t-1}).
- **Causal mechanism:** Direct measurement of historical price variation. Unlike implied vol, realized vol is backward-looking and captures what actually happened. The gap between realized and implied vol (volatility risk premium) is itself a tradable signal.
- **Key citation:** Andersen, T.G. & Bollerslev, T. (1998). "Answering the Skeptics: Yes, Standard Volatility Models Do Provide Accurate Forecasts." *International Economic Review.*
- **1H crypto suitability:** HIGH. Compute 24h, 168h, and 720h realized vol. Use for vol-scaling positions and identifying vol regime changes.
- **Signal type:** SIZING (inverse vol scaling), FILTER (vol regime classification).

#### Parkinson Volatility Estimator
- **Formula:** sigma_P = sqrt(1/(4*n*ln2) * sum(ln(H/L)^2)).
- **Causal mechanism:** Uses high-low range instead of close-to-close returns. More efficient estimator (5x the information of close-to-close per bar) because it captures intrabar volatility. Particularly useful when close-to-close understates true volatility due to mean reversion within bars.
- **Key citation:** Parkinson, M. (1980). "The Extreme Value Method for Estimating the Variance of the Rate of Return." *Journal of Business* 53(1), 61-65.
- **1H crypto suitability:** HIGH. Crypto 1H bars have significant high-low range relative to close-to-close moves. Parkinson captures this information. Superior to close-to-close vol for position sizing.
- **Signal type:** SIZING (more accurate vol estimate for position sizing).

#### Garman-Klass Volatility Estimator
- **Formula:** sigma_GK = sqrt(1/n * sum(0.5 * ln(H/L)^2 - (2*ln2 - 1) * ln(C/O)^2)).
- **Causal mechanism:** Uses open, high, low, close to provide a more efficient volatility estimate than Parkinson. Theoretically 7-8x more efficient than close-to-close. Accounts for the information in all four price points.
- **Key citation:** Garman, M.B. & Klass, M.J. (1980). "On the Estimation of Security Price Volatilities from Historical Data." *Journal of Business* 53(1), 67-78.
- **1H crypto suitability:** HIGH. Best OHLC-based vol estimator. Use for position sizing and vol-regime detection on 1H crypto data.
- **Signal type:** SIZING, FILTER.

#### Yang-Zhang Volatility Estimator
- **Formula:** sigma_YZ^2 = sigma_overnight^2 + k * sigma_open^2 + (1-k) * sigma_RS^2. Combines overnight returns, open-to-close, and Rogers-Satchell estimator. k = 0.34 / (1.34 + (n+1)/(n-1)).
- **Causal mechanism:** Handles overnight jumps (gaps) that Parkinson and Garman-Klass assume away. The most comprehensive OHLC-based estimator. In crypto, "overnight" corresponds to cross-session volatility during low-liquidity hours.
- **Key citation:** Yang, D. & Zhang, Q. (2000). "Drift-Independent Volatility Estimation Based on High, Low, Open, and Close Prices." *Journal of Business* 73(3), 477-491.
- **1H crypto suitability:** MODERATE. Crypto is 24/7 so the "open" concept is somewhat arbitrary on 1H. Still useful for capturing gap-like moves between hours.
- **Signal type:** SIZING, FILTER.

### 4.3 GARCH Family

#### GARCH(1,1)
- **Formula:** sigma_t^2 = omega + alpha * r_{t-1}^2 + beta * sigma_{t-1}^2. Constraints: alpha + beta < 1 (stationarity), omega > 0.
- **Causal mechanism:** Models volatility clustering -- the empirical observation that large returns tend to follow large returns. The persistence parameter (alpha + beta) captures how long volatility shocks decay. Provides a conditional volatility forecast that is superior to historical vol for short horizons.
- **Key citation:** Bollerslev, T. (1986). "Generalized Autoregressive Conditional Heteroscedasticity." *Journal of Econometrics* 31(3), 307-327. Engle, R.F. (1982). "Autoregressive Conditional Heteroscedasticity." *Econometrica.*
- **1H crypto suitability:** HIGH. Crypto exhibits extreme volatility clustering. GARCH(1,1) forecasts 1-step-ahead vol for position sizing. Estimate on rolling window of ~1000 1H bars.
- **Signal type:** SIZING (GARCH-forecasted vol for position sizing), FILTER (conditional vol regime).

#### EGARCH (Exponential GARCH)
- **Formula:** ln(sigma_t^2) = omega + alpha * (|z_{t-1}| - E|z|) + gamma * z_{t-1} + beta * ln(sigma_{t-1}^2). z = standardized residual.
- **Causal mechanism:** Models the leverage effect: negative returns increase vol more than positive returns of the same magnitude. In crypto, large selloffs trigger cascading liquidations and margin calls, creating asymmetric vol response. The log specification ensures sigma^2 is always positive.
- **Key citation:** Nelson, D.B. (1991). "Conditional Heteroscedasticity in Asset Returns: A New Approach." *Econometrica* 59(2), 347-370.
- **1H crypto suitability:** HIGH. Crypto's liquidation cascades create strong asymmetric vol effects. EGARCH captures this better than symmetric GARCH.
- **Signal type:** SIZING (asymmetric vol forecast), FILTER.

#### GJR-GARCH (Threshold GARCH)
- **Formula:** sigma_t^2 = omega + (alpha + gamma * I(r_{t-1} < 0)) * r_{t-1}^2 + beta * sigma_{t-1}^2. I() is indicator function.
- **Causal mechanism:** Like EGARCH, captures asymmetric vol response, but with a simpler threshold mechanism. The gamma parameter directly measures the additional vol impact of negative returns.
- **Key citation:** Glosten, L.R., Jagannathan, R. & Runkle, D.E. (1993). "On the Relation between the Expected Value and the Volatility of the Nominal Excess Return on Stocks." *Journal of Finance* 48(5), 1779-1801.
- **1H crypto suitability:** HIGH. Simpler to estimate than EGARCH with similar asymmetric vol capture.
- **Signal type:** SIZING, FILTER.

#### HAR-RV (Heterogeneous Autoregressive Realized Volatility)
- **Formula:** RV_{t+1} = c + beta_D * RV_t^(D) + beta_W * RV_t^(W) + beta_M * RV_t^(M). D=daily (24h), W=weekly (168h), M=monthly (720h) realized vol.
- **Causal mechanism:** Different market participants operate at different frequencies (day traders, swing traders, institutional). The HAR model captures this heterogeneity by using multi-scale realized vol as predictors. Each frequency component captures the volatility dynamics of a different participant group.
- **Key citation:** Corsi, F. (2009). "A Simple Approximate Long-Memory Model of Realized Volatility." *Journal of Financial Econometrics* 7(2), 174-196.
- **1H crypto suitability:** HIGH. Natural mapping to crypto: RV(24h), RV(168h), RV(720h). Excellent vol forecasting for swing trade sizing.
- **Signal type:** SIZING (multi-horizon vol forecast), FILTER (vol regime from model coefficients).

### 4.4 Volatility Regime Indicators

#### Crypto Volatility Index Analogs (DVOL, BVIV)
- **Formula:** Computed from option prices using VIX methodology adapted for crypto options (e.g., Deribit DVOL for BTC). BVIV = Bitcoin Implied Volatility Index.
- **Causal mechanism:** Implied vol from options reflects the market's expectation of future realized vol. High IV relative to RV = volatility risk premium (market is afraid). IV crush after events provides timing signals. IV term structure (contango vs backwardation) signals regime.
- **Key citation:** CBOE VIX methodology (2003). Deribit DVOL whitepaper (2021). Alexander, C. & Imeraj, A. (2020). "The Bitcoin VIX."
- **1H crypto suitability:** MODERATE-HIGH. DVOL data available from Deribit. IV percentile rank provides regime context. High IV percentile = reduce position size. IV crush = post-event opportunity.
- **Signal type:** FILTER (IV regime), SIZING (inverse IV scaling), ENTRY (IV crush setups).

#### Vol-of-Vol
- **Formula:** Standard deviation of rolling realized volatility. Vol_of_vol = std(RV(n), m) where RV(n) is n-period realized vol computed over m periods.
- **Causal mechanism:** High vol-of-vol indicates unstable volatility regime (switching between calm and turbulent). This makes directional bets riskier because stop distances may be inadequate. Low vol-of-vol = stable regime where position sizing is more reliable.
- **Key citation:** Huang, X. & Tauchen, G. (2005). "The Relative Contribution of Jumps to Total Price Variance." *Journal of Financial Econometrics.*
- **1H crypto suitability:** HIGH. Compute vol-of-vol using 24h RV over 30-day rolling window. High vol-of-vol periods in crypto correspond to regime transitions (e.g., post-halving, regulatory events).
- **Signal type:** SIZING (reduce exposure during high vol-of-vol), FILTER.

#### Implied vs Realized Volatility Spread
- **Formula:** VRP = IV - RV. Positive VRP = market overpaying for protection (fear premium).
- **Causal mechanism:** The volatility risk premium exists because option buyers pay for insurance against tail risk. When VRP is high, the market is pricing in more risk than is realized -- historically, this premium is harvested by vol sellers. When VRP is negative, realized vol exceeds expectations -- dangerous environment.
- **Key citation:** Carr, P. & Wu, L. (2009). "Variance Risk Premiums." *Review of Financial Studies* 22(3), 1311-1341.
- **1H crypto suitability:** MODERATE. Requires options data (Deribit). VRP signals are better on daily+ timeframes, but hourly VRP changes can signal shifts in market fear.
- **Signal type:** FILTER (negative VRP = danger), SIZING (high positive VRP = favorable for directional bets).

---

## 5. MARKET MICROSTRUCTURE

### 5.1 Bid-Ask Spread Estimators

#### Roll Spread Estimator
- **Formula:** Spread = 2 * sqrt(-Cov(r_t, r_{t-1})) when Cov < 0. If Cov >= 0, spread is estimated as 0.
- **Causal mechanism:** In the Roll model, the bid-ask bounce creates negative serial correlation in returns. Wider spreads create more bounce, increasing the magnitude of negative covariance. The estimator extracts the effective spread from trade-level data without requiring quote data.
- **Key citation:** Roll, R. (1984). "A Simple Implicit Measure of the Effective Bid-Ask Spread in an Efficient Market." *Journal of Finance* 39(4), 1127-1139.
- **1H crypto suitability:** LOW-MODERATE. Works best with high-frequency data. On 1H bars, the bid-ask bounce is overwhelmed by other return dynamics. Better estimated from 1-minute data and aggregated.
- **Signal type:** FILTER (spread level for liquidity screening), SIZING.

#### Corwin-Schultz High-Low Spread Estimator
- **Formula:** S = 2*(exp(alpha) - 1) / (1 + exp(alpha)), where alpha is derived from the ratio of high-low ranges over 1-bar and 2-bar periods. beta = sum(ln(H/L)^2) for adjacent bars; gamma = ln(H_2bar/L_2bar)^2.
- **Causal mechanism:** High prices more likely reflect ask-side transactions; low prices reflect bid-side. The ratio of single-bar to two-bar ranges can disentangle the bid-ask bounce from true volatility. Wider spreads inflate the single-bar range relative to the two-bar range.
- **Key citation:** Corwin, S.A. & Schultz, P. (2012). "A Simple Way to Estimate Bid-Ask Spreads from Daily High and Low Prices." *Journal of Finance* 67(2), 719-760.
- **1H crypto suitability:** MODERATE-HIGH. Works with OHLC data -- no tick data needed. Can estimate hourly spread from 1H bars. Useful for altcoin liquidity screening.
- **Signal type:** FILTER (spread threshold for tradability), SIZING (inverse spread scaling).

#### Abdi-Ranaldo Spread Estimator
- **Formula:** S^2 = 4 * Cov(ln(close) - ln(mid), ln(close_next) - ln(mid_next)), where mid = (high + low) / 2.
- **Causal mechanism:** Uses the midpoint of the high-low range as a proxy for the efficient price. The covariance between consecutive close-to-mid deviations reflects the bid-ask bounce around the efficient price. More robust than Roll estimator for OHLC data.
- **Key citation:** Abdi, F. & Ranaldo, A. (2017). "A Simple Estimation of Bid-Ask Spreads from Daily Close, High, and Low Prices." *Review of Financial Studies* 30(12), 4437-4480.
- **1H crypto suitability:** MODERATE-HIGH. Modern OHLC-based spread estimator. Good for building a liquidity filter across the crypto universe on 1H data.
- **Signal type:** FILTER (liquidity screening), SIZING.

### 5.2 Price Impact Measures

#### Kyle's Lambda
- See Section 3.2 above.
- **Signal type:** FILTER, SIZING.

#### Amihud Illiquidity
- See Section 3.2 above.
- **Signal type:** FILTER, SIZING.

#### Square Root Law of Market Impact
- **Formula:** Impact = sigma * sqrt(Q / V), where Q = order size, V = daily volume, sigma = volatility.
- **Causal mechanism:** Empirically, market impact scales as the square root of order size, not linearly. This is explained by the interplay between informed and uninformed flow: market makers cannot distinguish the two, so they price protect proportionally to the information content, which scales sub-linearly.
- **Key citation:** Bouchaud, J.-P., Farmer, J.D. & Lillo, F. (2009). "How Markets Slowly Digest Changes in Supply and Demand." In: *Handbook of Financial Markets: Dynamics and Evolution.* Almgren, R. & Chriss, N. (2001). "Optimal Execution of Portfolio Transactions." *JRSI.*
- **1H crypto suitability:** MODERATE. Important for execution/slippage estimation rather than signal generation. Size your trades relative to average hourly volume to minimize impact.
- **Signal type:** SIZING (max position size = f(volume, target impact)).

### 5.3 Information-Based Indicators

#### VPIN
- See Section 3.2 above.
- **Signal type:** FILTER, SIZING.

#### PIN (Probability of Informed Trading)
- **Formula:** PIN = (alpha * mu) / (alpha * mu + 2 * epsilon), estimated via MLE on trade arrival rates. Alpha = prob of info event, mu = informed arrival rate, epsilon = uninformed arrival rate.
- **Causal mechanism:** In the Easley-O'Hara sequential trade model, informed traders trade aggressively when they have private information, creating imbalance between buy and sell order arrivals. High PIN means the order flow is likely driven by informed traders with superior information.
- **Key citation:** Easley, D., Kiefer, N.M. & O'Hara, M. (1997). "One Day in the Life of a Very Common Stock." *Review of Financial Studies* 10(3), 805-835.
- **1H crypto suitability:** LOW. Requires tick-level data and MLE estimation. VPIN is the practical alternative for crypto. PIN is more of a theoretical foundation.
- **Signal type:** FILTER.

### 5.4 Crypto Microstructure

#### Perpetual Funding Rate
- **Formula:** Funding = (Mark_Price - Index_Price) / Index_Price, settled every 8 hours. Predicted funding can be computed at higher frequency.
- **Causal mechanism:** Funding rate is the cost of maintaining a leveraged perpetual futures position. Positive funding = longs pay shorts (excess long demand). Extreme funding indicates crowded positioning. When funding cost exceeds the expected return, positions are unwound, causing reversals.
- **Key citation:** Deribit Academy. Alexander & Heck (2020). "Price Discovery in Bitcoin: The Role of Perpetual Swaps." See also BitMEX Research.
- **1H crypto suitability:** HIGH. Critical crypto-specific indicator. Monitor predicted funding (available at sub-8h frequency). Extreme funding (>0.1% per 8h) is one of the strongest contrarian signals in crypto.
- **Signal type:** FILTER (extreme funding = caution), EXIT (funding spike against position), ENTRY (extreme negative funding for contrarian longs).

#### Liquidation Cascades
- **Formula:** Liquidation_volume = sum of forced liquidation volumes per period. Cascade_indicator = liquidation_vol / total_vol.
- **Causal mechanism:** Leveraged positions have liquidation prices. When price reaches these levels, forced selling/buying occurs, driving price further in the same direction, triggering more liquidations (cascade). Liquidation cascades are a feedback loop unique to leveraged crypto markets. The aftermath of cascades creates mean-reversion opportunities.
- **Key citation:** Perez, M. (2020). "Liquidation Risk in Cryptocurrency Markets." Coinglass / Bybt analytics.
- **1H crypto suitability:** HIGH. Track liquidation volume from aggregators (Coinglass). Post-cascade = mean reversion opportunity. Pre-cascade (building OI + approaching liquidation clusters) = directional risk.
- **Signal type:** ENTRY (post-cascade mean reversion), FILTER (cascade risk assessment), SIZING (reduce size near liquidation clusters).

#### Open Interest Concentration (Heatmaps)
- **Formula:** OI_concentration = OI at specific price levels (from options or liquidation heatmaps). Identify max-pain for options and liquidation clusters.
- **Causal mechanism:** Price is attracted to levels with maximum pain (where most options expire worthless) or where large liquidation clusters exist (creating guaranteed order flow at those levels). Market makers and large players use this information to target stops and liquidation levels.
- **Key citation:** Crypto options analytics (Laevitas, Deribit). See also Ni et al. (2005). "Does Option Trading Convey Stock Price Information?" *JFE.*
- **1H crypto suitability:** HIGH. Options max-pain and liquidation heatmaps are freely available. Useful for identifying magnets and stop zones on 1H swing trades.
- **Signal type:** ENTRY (near max-pain/liquidation cluster targets), EXIT (price approaching large liquidation cluster that could cascade), FILTER.

---

## 6. SENTIMENT / ALTERNATIVE DATA

### 6.1 On-Chain Indicators (BTC/ETH)

#### MVRV (Market Value to Realized Value)
- **Formula:** MVRV = Market_Cap / Realized_Cap. Realized Cap = sum of each UTXO valued at its last moved price.
- **Causal mechanism:** MVRV measures the aggregate unrealized profit/loss of all holders. MVRV > 1 means the average holder is in profit (more likely to sell). MVRV > 3.5 historically marks tops (extreme greed). MVRV < 1 = average holder is underwater (capitulation zone, historically marks bottoms).
- **Key citation:** Puell, D. (2018). "Introducing Realized Capitalization." Coinmetrics. Also: Awe & Pal (2020). "MVRV Ratio Analysis."
- **1H crypto suitability:** MODERATE. MVRV changes slowly (daily resolution is typical). Use as a long-term regime filter on 1H system: MVRV > 2.5 = reduce long exposure, MVRV < 1.2 = increase long bias.
- **Signal type:** FILTER (macro valuation regime), SIZING (scale exposure inversely with MVRV extremes).

#### SOPR (Spent Output Profit Ratio)
- **Formula:** SOPR = sum(value of outputs at spent time) / sum(value of outputs at creation time). SOPR > 1 = coins moved at profit; < 1 = at loss.
- **Causal mechanism:** When SOPR > 1, holders are realizing profits, increasing sell pressure. When SOPR < 1, holders are selling at a loss (capitulation). SOPR returning to 1 from below in a bull market indicates capitulation is complete -- a buy signal. SOPR rejection at 1 from above in a bear market = sellers unwilling to take losses = continued pressure.
- **Key citation:** Renato Shirakashi (2019). "Introducing SOPR." Glassnode.
- **1H crypto suitability:** MODERATE. Daily resolution, best used as a filter. SOPR bouncing off 1.0 from below during uptrends = dip-buying opportunity. Available from Glassnode API.
- **Signal type:** FILTER (SOPR regime), ENTRY (SOPR bounce at 1.0 support in bull markets).

#### NVT Ratio (Network Value to Transactions)
- **Formula:** NVT = Market_Cap / Daily_Transaction_Volume (in USD). NVT Signal uses 90-day MA of tx volume.
- **Causal mechanism:** Analogous to P/E ratio. High NVT = network is overvalued relative to its economic throughput (speculative premium). Low NVT = undervalued or heavy usage. NVT captures the fundamental utility of the network.
- **Key citation:** Woo, W. (2017). "NVT Ratio." Woobull.com. Kalichkin, D. (2018). "Rethinking NVT Ratio." Cryptolab Capital.
- **1H crypto suitability:** LOW-MODERATE. Slow-moving indicator (daily). Use as macro filter only. NVT Signal > 150 for BTC = overvaluation warning.
- **Signal type:** FILTER (macro valuation), SIZING.

#### Active Address Count
- **Formula:** Count of unique addresses that transacted (sent or received) in a period. Growth rate = (AA_t - AA_{t-n}) / AA_{t-n}.
- **Causal mechanism:** Active addresses proxy for network usage demand. Growing active addresses = growing adoption, supporting higher valuations (network effects, Metcalfe's Law). Declining active addresses despite rising price = speculative divergence (bearish).
- **Key citation:** Metcalfe's Law applied to crypto: Peterson, T. (2018). "Metcalfe's Law as a Model for Bitcoin's Value." *Alternative Investment Analyst Review.* Also: Alabi (2017). "Digital Thomson: A Predictive Model of the Bitcoin Price Based on Users."
- **1H crypto suitability:** LOW. Daily resolution. Macro filter only.
- **Signal type:** FILTER (fundamental health check).

#### Hash Rate (BTC)
- **Formula:** Hash_rate = network total hash power (TH/s or EH/s). Hash ribbons = MA(30) vs MA(60) of hash rate.
- **Causal mechanism:** Hash rate represents miner commitment (capital investment in hardware). Declining hash rate signals miner capitulation (unprofitable miners shutting down), which historically precedes bottoms. Rising hash rate = growing miner confidence in future price. Hash ribbons (short MA crossing above long MA) signal end of capitulation.
- **Key citation:** Capriole, C. (2019). "Hash Ribbons — A Bitcoin Buy Signal." See also: Hayes, A. (2017). "Cryptocurrency Value Formation." *Telematics and Informatics.*
- **1H crypto suitability:** LOW. Changes slowly (daily). Use as a macro regime filter for BTC-related swing trades.
- **Signal type:** FILTER (hash ribbon buy signal for BTC bottom detection).

### 6.2 Social / Sentiment Indicators

#### Crypto Fear & Greed Index
- **Formula:** Composite of: volatility (25%), market momentum/volume (25%), social media (15%), surveys (15%), Bitcoin dominance (10%), Google Trends (10%). Scale 0-100.
- **Causal mechanism:** Behavioral finance: extreme fear = irrational selling, good time to buy. Extreme greed = euphoria, overextended positioning, good time to reduce exposure. Contrarian indicator based on crowd psychology.
- **Key citation:** Alternative.me Fear & Greed Index. See also: Baker & Wurgler (2006). "Investor Sentiment and the Cross-Section of Stock Returns." *Journal of Finance.*
- **1H crypto suitability:** MODERATE. Updated daily. Use as a daily regime filter on 1H system. Fear < 20 = increase long bias. Greed > 80 = reduce long exposure.
- **Signal type:** FILTER (contrarian regime), SIZING (scale with inverse sentiment).

#### Social Volume / Social Dominance
- **Formula:** Social_volume = count of mentions across Twitter, Reddit, Telegram per period. Social_dominance = token_mentions / total_crypto_mentions.
- **Causal mechanism:** Spikes in social volume often precede or coincide with large price moves. However, the relationship is complex: (1) Rising social + rising price = FOMO, potentially near top. (2) Rising social + falling price = panic, potentially near bottom. Social dominance shifts indicate capital rotation narratives.
- **Key citation:** Santiment data. Garcia et al. (2014). "The Digital Traces of Bubbles." Philippas et al. (2019). "Media attention and Bitcoin prices." *Finance Research Letters.*
- **1H crypto suitability:** MODERATE. Available at hourly resolution from providers like LunarCrush, Santiment. Social spikes as contrarian filter. Social momentum as confirming indicator.
- **Signal type:** FILTER (extreme social volume as contrarian), ENTRY (social + price divergence).

#### Weighted Sentiment
- **Formula:** NLP-derived sentiment score weighted by reach/engagement. Score = sum(sentiment_score * engagement_weight) / total_engagement.
- **Causal mechanism:** Aggregate market opinion weighted by influence. Extreme negative weighted sentiment often marks capitulation; extreme positive marks euphoria. The weighting by reach/engagement helps filter noise from low-influence accounts.
- **Key citation:** Santiment Weighted Sentiment. See also: Tetlock, P. (2007). "Giving Content to Investor Sentiment: The Role of Media in the Stock Market." *Journal of Finance.*
- **1H crypto suitability:** MODERATE. Available from Santiment API at daily resolution. Use as a daily filter overlaid on 1H signals.
- **Signal type:** FILTER (contrarian at extremes), ENTRY (sentiment + price divergence).

### 6.3 Derivatives Sentiment

#### Put-Call Ratio (Options)
- **Formula:** PCR = put_volume / call_volume (or put_OI / call_OI).
- **Causal mechanism:** High PCR = excessive hedging/bearish bets, often contrarian bullish (too much fear priced in). Low PCR = excessive call buying/complacency, contrarian bearish. In equities, PCR is a well-established contrarian indicator; in crypto options, it is gaining relevance as the market matures.
- **Key citation:** Pan & Poteshman (2006). "The Information in Option Volume for Future Stock Prices." *Review of Financial Studies.* Crypto: Deribit analytics.
- **1H crypto suitability:** MODERATE. Crypto options market is growing (Deribit). PCR available at daily resolution. Use as a daily filter.
- **Signal type:** FILTER (contrarian at extremes), SIZING.

#### Perpetual Basis (Futures Premium)
- **Formula:** Basis = (Futures_Price - Spot_Price) / Spot_Price. For perpetuals: approximated by cumulative funding.
- **Causal mechanism:** Positive basis = market in contango (bullish positioning, longs willing to pay premium). Extreme positive basis = crowded longs. Negative basis = backwardation (bearish positioning or delivery-related). Basis normalization (return toward 0) drives price moves.
- **Key citation:** Alexandrova-Kabadjova et al. (2020). See also Samuelson effect: Samuelson, P. (1965). "Proof That Properly Anticipated Prices Fluctuate Randomly."
- **1H crypto suitability:** HIGH. Perpetual basis (annualized funding rate) is available in real-time. Extreme basis is a strong mean-reversion signal. Monitor across BTC, ETH, and major alts.
- **Signal type:** FILTER (basis regime), EXIT (extreme basis against position), ENTRY (basis normalization setup).

### 6.4 Research Sentiment Indices

#### Baker-Wurgler Sentiment Index
- **Formula:** First principal component of six sentiment proxies: closed-end fund discount, NYSE share turnover, IPO volume, IPO first-day return, equity share of new issuance, dividend premium.
- **Causal mechanism:** Captures the common component of multiple sentiment indicators. High sentiment -> speculative overpricing of hard-to-value assets (small, young, volatile) -> subsequent low returns. Low sentiment -> underpricing -> subsequent high returns.
- **Key citation:** Baker, M. & Wurgler, J. (2006). "Investor Sentiment and the Cross-Section of Stock Returns." *Journal of Finance* 61(4), 1645-1680.
- **1H crypto suitability:** LOW. Equity-focused and monthly frequency. Conceptual framework applicable to crypto but indicator itself is not directly usable. Inspires building crypto-specific multi-factor sentiment indices.
- **Signal type:** FILTER (conceptual framework for sentiment construction).

#### News Sentiment (NLP-Based)
- **Formula:** Sentiment = f(NLP_model(news_text)). Typically: transformer-based classifier (FinBERT, etc.) applied to crypto news. Aggregate across sources with recency weighting.
- **Causal mechanism:** News drives narrative which drives flows. Positive news attracts retail and institutional capital. Negative news (hacks, regulatory crackdowns) triggers selling. The speed of sentiment shift is as important as the level -- rapid shifts indicate regime changes.
- **Key citation:** Loughran, T. & McDonald, B. (2011). "When Is a Liability Not a Liability? Textual Analysis, Dictionaries, and 10-Ks." *Journal of Finance.* Crypto: Aloosh, A. & Li, J. (2021). "Direct Evidence of Bitcoin Manipulations." Also: CryptoCompare News API.
- **1H crypto suitability:** MODERATE-HIGH. Real-time news sentiment available from multiple providers. Rapid sentiment shifts on 1H can precede or accompany large moves. Best used as a filter or volatility anticipation signal.
- **Signal type:** FILTER (sentiment regime), ENTRY (extreme shift), SIZING (reduce during high-uncertainty news flow).

---

## 7. STATISTICAL / ECONOMETRIC INDICATORS

### 7.1 Returns-Based Features

#### Multi-Horizon Returns
- **Formula:** r(k) = (close - close[k]) / close[k] for k = 1, 4, 12, 24, 48, 168, 336, 720 hours.
- **Causal mechanism:** Returns at different horizons capture different aspects of price dynamics. Short-term returns (1-4h) capture microstructure effects. Medium-term (24-168h) capture momentum. Long-term (336-720h) capture mean reversion. The combination provides a multi-scale view of market state.
- **Key citation:** Campbell, J.Y. & Shiller, R.J. (1988). "Stock Prices, Earnings, and Expected Dividends." *Journal of Finance.* Lo, A.W. (2004). "The Adaptive Markets Hypothesis."
- **1H crypto suitability:** HIGH. Foundational features for any ML-based trading system. Multi-horizon returns are the raw input for many higher-level indicators.
- **Signal type:** ENTRY (return-based momentum), FILTER (return sign at multiple horizons for trend confirmation).

#### Log Returns
- **Formula:** r_log = ln(close_t / close_{t-1}).
- **Causal mechanism:** Log returns are additive across time, approximately normally distributed, and symmetric for small returns. Preferred for statistical analysis. The distribution properties (fat tails, asymmetry) are informative for risk management.
- **Key citation:** Standard financial econometrics. See: Campbell, Lo & MacKinlay (1997). *The Econometrics of Financial Markets.*
- **1H crypto suitability:** HIGH. Use log returns for all statistical computations. Convert to simple returns only for P&L calculation.
- **Signal type:** INPUT (fundamental building block for all statistical indicators).

#### Excess Returns vs BTC
- **Formula:** r_excess = r_asset - r_BTC over period k.
- **Causal mechanism:** BTC is the market factor for crypto. Excess returns strip out the common market movement, isolating idiosyncratic alpha. Positive excess returns indicate asset-specific demand (not just beta to the market). This is the crypto equivalent of market-adjusted returns in equities.
- **Key citation:** Analogous to Jensen's alpha: Jensen, M. (1968). "The Performance of Mutual Funds in the Period 1945-1964." Crypto: Liu & Tsyvinski (2021). "Risks and Returns of Cryptocurrency." *Review of Financial Studies.*
- **1H crypto suitability:** HIGH. Compute excess returns over BTC at 24h, 168h horizons. Use for cross-sectional ranking and relative momentum.
- **Signal type:** ENTRY (top excess return assets for relative momentum), FILTER (positive excess return for strength confirmation).

### 7.2 Distribution Measures

#### Rolling Skewness
- **Formula:** Skew = n/((n-1)(n-2)) * sum(((r - mean)/std)^3) over window n.
- **Causal mechanism:** Negative skewness indicates the distribution has a left tail (crash risk is elevated). Positive skewness indicates right tail (potential for outsized gains). Changes in skewness signal shifts in the balance of bullish vs bearish participants and the distribution of expected outcomes.
- **Key citation:** Harvey, C.R. & Siddique, A. (2000). "Conditional Skewness in Asset Pricing Tests." *Journal of Finance.* Bali, T.G., Cakici, N. & Whitelaw, R.F. (2011). "Maxing Out: Stocks as Lotteries." *Journal of Financial Economics.*
- **1H crypto suitability:** HIGH. Rolling skewness over 100-500 bars. Crypto exhibits regime-dependent skewness. Negative skewness in uptrends = crash warning. Use window of 168-720h.
- **Signal type:** FILTER (skewness regime), SIZING (reduce position during negative skew), EXIT (skewness shift to negative).

#### Rolling Kurtosis
- **Formula:** Kurt = n(n+1)/((n-1)(n-2)(n-3)) * sum(((r - mean)/std)^4) - 3(n-1)^2/((n-2)(n-3)). Excess kurtosis (normal = 0).
- **Causal mechanism:** High kurtosis (fat tails) indicates increased probability of extreme moves (both up and down). This measures the "tailedness" of the return distribution. Periods of high kurtosis require wider stops and smaller positions because the normal distribution underestimates tail risk.
- **Key citation:** Mandelbrot, B. (1963). "The Variation of Certain Speculative Prices." Fama, E. (1965). "The Behavior of Stock-Market Prices." *Journal of Business.*
- **1H crypto suitability:** HIGH. Crypto is famously fat-tailed. Rolling kurtosis on 168-720h windows. High kurtosis = reduce size, widen stops. Kurtosis expansion often precedes major moves.
- **Signal type:** SIZING (inverse kurtosis position scaling), FILTER (tail risk regime).

#### CVaR (Conditional Value at Risk / Expected Shortfall)
- **Formula:** CVaR_alpha = E[r | r < VaR_alpha]. For alpha = 5%: average of returns below the 5th percentile.
- **Causal mechanism:** Measures the expected loss conditional on a tail event occurring. Unlike VaR (which only gives the threshold), CVaR quantifies the expected damage in the tail. Essential for position sizing in crypto, where tail events are more common than normal distribution assumptions suggest.
- **Key citation:** Artzner, P. et al. (1999). "Coherent Measures of Risk." *Mathematical Finance.* Rockafellar, R.T. & Uryasev, S. (2000). "Optimization of Conditional Value-at-Risk."
- **1H crypto suitability:** HIGH. Compute rolling CVaR from 1H returns over 720h window. Use for max position sizing and drawdown budget allocation.
- **Signal type:** SIZING (position size = f(CVaR, risk budget)), FILTER (CVaR regime for portfolio risk management).

#### Extreme Value Theory (EVT) Indicators
- **Formula:** Fit Generalized Pareto Distribution (GPD) to tail returns exceeding a threshold u. Shape parameter xi characterizes tail behavior. Tail risk = 1 + xi.
- **Causal mechanism:** EVT models the actual tail distribution rather than assuming normality. The shape parameter (xi) captures whether tails are thin (xi < 0), exponential (xi = 0), or fat (xi > 0, power law). Crypto consistently shows xi > 0 (Pareto tails), meaning extreme events are more likely than normal distribution implies.
- **Key citation:** Embrechts, P., Kluppelberg, C. & Mikosch, T. (1997). *Modelling Extremal Events.* Springer. Crypto: Gkillas, K. & Katsiampa, P. (2018). "An Application of Extreme Value Theory to Cryptocurrencies." *Economics Letters.*
- **1H crypto suitability:** MODERATE. Requires sufficient tail observations (100+ tail events). Estimate on rolling 720h+ windows. Use for setting extreme stop-loss levels and stress-testing position sizes.
- **Signal type:** SIZING (EVT-calibrated tail risk for position sizing), FILTER.

### 7.3 Correlation / Dependence

#### Rolling Correlation
- **Formula:** rho(X,Y,n) = Cov(X,Y,n) / (std(X,n) * std(Y,n)). Compute pairwise rolling correlations.
- **Causal mechanism:** Correlation between assets reflects the degree of common factor exposure. Rising correlations reduce diversification benefits (portfolio risk increases). In crypto, correlations spike during market stress (contagion), making diversification illusory when it's needed most.
- **Key citation:** Longin, F. & Solnik, B. (2001). "Extreme Correlation of International Equity Markets." *Journal of Finance.* Crypto: Bouri, E. et al. (2019). "On the hedge and safe haven properties of Bitcoin." *Finance Research Letters.*
- **1H crypto suitability:** HIGH. Rolling correlation over 168-720h windows. Intra-crypto correlation (BTC vs alts) reveals regime: high correlation = risk-off, low correlation = alt-season. Use for portfolio construction and risk management.
- **Signal type:** FILTER (correlation regime for portfolio construction), SIZING (reduce portfolio size when correlations spike).

#### DCC-GARCH (Dynamic Conditional Correlation)
- **Formula:** Two-stage: (1) Estimate univariate GARCH for each series. (2) Model time-varying correlation via DCC specification: Q_t = (1-a-b)*Q_bar + a*z_{t-1}*z_{t-1}' + b*Q_{t-1}. R_t = diag(Q_t)^{-1/2} * Q_t * diag(Q_t)^{-1/2}.
- **Causal mechanism:** Correlations are not constant -- they change with market conditions (stress, euphoria, regime shifts). DCC-GARCH provides a time-varying estimate that captures correlation clustering and mean-reversion. Critical for dynamic portfolio optimization.
- **Key citation:** Engle, R. (2002). "Dynamic Conditional Correlation: A Simple Class of Multivariate GARCH Models." *Journal of Business & Economic Statistics* 20(3), 339-350.
- **1H crypto suitability:** MODERATE-HIGH. Computationally intensive. Estimate on daily data and interpolate to hourly. Valuable for multi-asset crypto portfolios.
- **Signal type:** SIZING (correlation-adjusted portfolio weights), FILTER (correlation regime).

#### Copula-Based Dependence
- **Formula:** Model the joint distribution of asset returns using copulas (e.g., Clayton, Gumbel, t-copula) after transforming margins to uniform. Tail dependence coefficient lambda captures extreme co-movement.
- **Causal mechanism:** Linear correlation fails to capture nonlinear dependencies, especially in the tails. Copulas model the full dependence structure. High lower tail dependence means assets crash together (contagion risk). This is critical because crypto assets show much stronger lower tail dependence than upper tail dependence.
- **Key citation:** Embrechts, P., McNeil, A. & Straumann, D. (2002). "Correlation and Dependence in Risk Management." In: *Risk Management: Value at Risk and Beyond.* Crypto: Borri, N. (2019). "Conditional Tail-Risk in Cryptocurrency Markets." *JIE.*
- **1H crypto suitability:** MODERATE. Estimation requires substantial data. Use on daily or 4H data, apply insights to 1H position sizing. Focus on tail dependence coefficient for downside risk.
- **Signal type:** SIZING (tail-dependence-adjusted portfolio construction), FILTER.

### 7.4 Regime Detection

#### Hidden Markov Model (HMM)
- **Formula:** States S = {bull, bear, neutral}. Transition matrix A, emission probabilities B (returns | state), initial probabilities pi. Estimated via Baum-Welch (EM) algorithm. State inference via Viterbi algorithm.
- **Causal mechanism:** Markets transition between distinct regimes (bull/bear/sideways) driven by macro conditions, sentiment shifts, and structural changes. Each regime has characteristic return distributions. HMM identifies the current regime probabilistically, allowing strategy adaptation: momentum in bull, mean-reversion in range, reduced exposure in bear.
- **Key citation:** Hamilton, J.D. (1989). "A New Approach to the Economic Analysis of Nonstationary Time Series and the Business Cycle." *Econometrica* 57(2), 357-384. Crypto: Koki, C. et al. (2020). "Exploring the Predictability of Cryptocurrencies via Bayesian Hidden Markov Models." *Research in International Business and Finance.*
- **1H crypto suitability:** HIGH. Fit 2-3 state HMM on 1H returns, vol, and volume. Use state probabilities for strategy switching. Bull state -> momentum strategies. Bear state -> reduce exposure / short. Sideways -> mean reversion.
- **Signal type:** FILTER (regime determines strategy selection), SIZING (state-dependent position sizing), ENTRY/EXIT (state transitions).

#### BOCPD (Bayesian Online Change Point Detection)
- **Formula:** P(run_length = r | data) via message-passing algorithm. Run length = time since last change point. Uses hazard function H (prior on change rate) and predictive distribution.
- **Causal mechanism:** Detects structural breaks in real-time. When the statistical properties of the return series change abruptly (new regime), BOCPD identifies the change point with controlled delay. This triggers strategy re-evaluation or risk reduction until the new regime is characterized.
- **Key citation:** Adams, R.P. & MacKay, D.J.C. (2007). "Bayesian Online Changepoint Detection." *arXiv:0710.3742.*
- **1H crypto suitability:** HIGH. Fast enough for online estimation on 1H bars. Detects regime changes (e.g., crash onset, rally initiation, volatility regime shift) in real-time. Triggers strategy adaptation.
- **Signal type:** FILTER (change point detected = reassess), EXIT (rapid change point with adverse direction), SIZING (reduce size after change point until new regime is characterized).

#### Markov Switching Models (MSM)
- **Formula:** r_t = mu_{s_t} + sigma_{s_t} * epsilon_t, where s_t in {1,...,K} follows a Markov chain. Allows mean, variance, or both to switch between states.
- **Causal mechanism:** Extends HMM with explicit parametric modeling of the return process in each state. Different from HMM in that the switching dynamics are embedded in the return model directly. Captures the observation that both the mean return and volatility change when the market switches regimes.
- **Key citation:** Hamilton (1989). *Econometrica.* See also: Ang, A. & Bekaert, G. (2002). "Regime Switches in Interest Rates." *Journal of Business & Economic Statistics.*
- **1H crypto suitability:** HIGH. 2-state MSM (high-vol/low-vol or bull/bear) on 1H data. Use smoothed state probabilities for real-time regime identification.
- **Signal type:** FILTER (regime identification), SIZING (state-dependent vol and mean for position sizing).

---

## 8. CAUSAL / LEAD-LAG INDICATORS

### 8.1 Statistical Causality

#### Granger Causality Indicators
- **Formula:** Test: does past Y improve prediction of X beyond past X alone? F-test on restricted vs unrestricted VAR. Signal: rolling Granger F-statistic or p-value for each pair.
- **Causal mechanism:** If asset Y Granger-causes asset X, information in Y's past improves prediction of X's future. This can arise from: (1) Y is in a more informationally efficient market. (2) Y's participants are more informed. (3) Structural lead (e.g., BTC leads alts). The lead-lag relationship can be exploited by trading X based on Y's signal.
- **Key citation:** Granger, C.W.J. (1969). "Investigating Causal Relations by Econometric Models and Cross-Spectral Methods." *Econometrica.* Crypto: Bouri, E. et al. (2020). "Return connectedness across asset classes around the COVID-19 outbreak."
- **1H crypto suitability:** HIGH. Test BTC -> alt Granger causality at 1-24 hour lags. If BTC moves first and alts follow with lag, use BTC movement as a leading signal for alt entries. Re-estimate on rolling windows (the lead-lag relationship shifts).
- **Signal type:** ENTRY (leading asset signal applied to lagging asset), FILTER (Granger causality significance as confidence measure).

#### Transfer Entropy
- **Formula:** TE(Y->X) = sum p(x_{t+1}, x_t^k, y_t^l) * log(p(x_{t+1}|x_t^k, y_t^l) / p(x_{t+1}|x_t^k)). Measures information flow from Y to X beyond X's own past.
- **Causal mechanism:** Non-parametric generalization of Granger causality. Captures nonlinear information flow between time series. Directional: TE(BTC->ETH) != TE(ETH->BTC). In crypto, information flows from more liquid/established assets to less liquid ones, creating exploitable lead-lag.
- **Key citation:** Schreiber, T. (2000). "Measuring Information Transfer." *Physical Review Letters.* Crypto: Dimpfl, T. & Peter, F.J. (2019). "Group Transfer Entropy with an Application to Cryptocurrencies." *Physica A.*
- **1H crypto suitability:** MODERATE-HIGH. Computationally intensive. Estimate on 100-500 bar windows. Provides directional information flow graph for the crypto universe. Use strongest TE links for lead-lag trading.
- **Signal type:** ENTRY (leading asset move + high TE to lagging asset), FILTER (TE significance for pair selection).

### 8.2 Cross-Correlation Analysis

#### Cross-Correlation at Various Lags
- **Formula:** CCF(X,Y,k) = Corr(X_t, Y_{t+k}) for lags k = -24, ..., +24 hours. Peak lag identifies lead-lag.
- **Causal mechanism:** The lag at which cross-correlation peaks reveals the typical delay in information transmission between assets. If CCF(BTC, ALT) peaks at k=2 hours, ALT tends to follow BTC's moves with a 2-hour delay. This delay represents the time for arbitrageurs, algorithmic traders, and retail participants to react.
- **Key citation:** Box, G.E.P. & Jenkins, G.M. (1970). *Time Series Analysis: Forecasting and Control.* Applied to crypto: Katsiampa et al. (2019). "Volatility estimation for Bitcoin: A comparison of GARCH models." *Economics Letters.*
- **1H crypto suitability:** HIGH. Compute rolling cross-correlation between BTC and each alt at lags 1-24 hours. The peak lag tells you how much time you have to position before the alt follows. Some alts consistently lag 1-4 hours.
- **Signal type:** ENTRY (BTC move -> enter lagging alt at optimal lag), FILTER (cross-correlation strength determines trade confidence).

### 8.3 Cross-Asset Leading Indicators

#### ETH/BTC Ratio
- **Formula:** ETH_BTC = ETH_price / BTC_price. Signal: ratio trend, breakout, extreme levels.
- **Causal mechanism:** The ETH/BTC ratio is a barometer for risk appetite within crypto. Rising ETH/BTC = capital rotating from BTC to alts (risk-on, alt-season). Falling ETH/BTC = flight to quality (risk-off, BTC dominance). The ratio leads broader alt moves because ETH is the first stop in capital rotation.
- **Key citation:** Empirical crypto market structure observation. See: Makarov & Schoar (2020). "Trading and Arbitrage in Cryptocurrency Markets." *Journal of Financial Economics.*
- **1H crypto suitability:** HIGH. Monitor ETH/BTC on 1H with trend indicators. Rising ETH/BTC + breakout = increase alt exposure. Falling ETH/BTC = reduce alt exposure, concentrate in BTC.
- **Signal type:** FILTER (risk-on/risk-off regime for alt exposure), ENTRY (ETH/BTC breakout as alt-season trigger), SIZING (ETH/BTC trend for alt allocation).

#### BTC Dominance
- **Formula:** BTC_dom = BTC_Market_Cap / Total_Crypto_Market_Cap.
- **Causal mechanism:** Rising dominance = capital concentrating in BTC (risk-off within crypto, alt sell-off). Falling dominance = capital flowing to alts. Dominance is a proxy for the stage of the market cycle: early bull = BTC leads (dominance rises), mid bull = alts catch up (dominance falls), late bull = alt mania (dominance collapses), bear = dominance rises.
- **Key citation:** CoinMarketCap methodology. See: Elendner et al. (2018). "Cross-section of crypto-assets as financial assets." Crypto market cycle analysis.
- **1H crypto suitability:** MODERATE. Changes slowly (daily). Use as a daily regime filter. Falling dominance below key levels (e.g., 40%) signals alt-season.
- **Signal type:** FILTER (market cycle position), SIZING (alt allocation inversely proportional to dominance in bull markets).

#### DXY (US Dollar Index)
- **Formula:** DXY = geometric weighted mean of USD against EUR, JPY, GBP, CAD, SEK, CHF. Rolling correlation with crypto.
- **Causal mechanism:** Crypto is primarily denominated in USD. A strengthening dollar (rising DXY) reduces the attractiveness of risk assets globally, including crypto, as dollar-denominated assets become relatively cheaper and capital flows to USD-denominated safe assets. Weakening DXY makes crypto more attractive as an inflation hedge / risk asset.
- **Key citation:** Corbet, S. et al. (2018). "Exploring the Dynamic Relationships between Cryptocurrencies and Other Financial Assets." *Economics Letters.* Also: Federal Reserve research on dollar strength and risk assets.
- **1H crypto suitability:** HIGH. DXY available at hourly resolution. Rolling correlation between DXY and BTC varies over time (correlation regime). When correlation is strongly negative, DXY provides leading information for crypto.
- **Signal type:** FILTER (DXY trend for macro context), ENTRY (DXY reversal as crypto catalyst), SIZING (reduce crypto exposure during sharp DXY strengthening).

#### US10Y (10-Year Treasury Yield)
- **Formula:** US10Y yield level and changes. Signal: yield direction, rate of change, yield curve slope.
- **Causal mechanism:** Rising yields increase the opportunity cost of holding zero-yielding assets like crypto. Rising real yields (nominal minus inflation expectations) are particularly negative for crypto as they reduce the appeal of inflation hedges. The relationship is strongest during Fed tightening cycles.
- **Key citation:** Conlon, T. & McGee, R. (2020). "Safe Haven or Risky Hazard? Bitcoin during the COVID-19 Bear Market." *Finance Research Letters.* Choi, S. & Shin, J. (2022). "Bitcoin as a hedge: Is it actually anything more than a speculative asset?"
- **1H crypto suitability:** MODERATE-HIGH. Bond yields available at hourly resolution during trading hours. Rate shock events (FOMC announcements, CPI releases) create immediate crypto volatility. Monitor rate-of-change in yields.
- **Signal type:** FILTER (yield regime for macro context), EXIT (yield spike -> reduce crypto), ENTRY (yield decline reversal -> crypto positive).

#### SPX Correlation Regime
- **Formula:** Rolling correlation between S&P 500 and BTC/crypto over 30-90 day windows. Signal: correlation level and change.
- **Causal mechanism:** When BTC-SPX correlation is high, crypto trades as a risk asset (macro factor dominates). When correlation is low or negative, crypto-specific factors dominate. During high-correlation regimes, traditional risk-off events (equity sell-offs) directly impact crypto. During decoupled regimes, crypto can rally independently.
- **Key citation:** Bouri, E. et al. (2017). "On the Hedge and Safe Haven Properties of Bitcoin: Is It Really More Than a Diversifier?" *Finance Research Letters.* Smales, L.A. (2022). "Bitcoin as a safe haven."
- **1H crypto suitability:** HIGH. Compute rolling BTC-SPX correlation on 720h window. High correlation regime: monitor SPX closely, reduce crypto during equity risk-off. Low correlation regime: crypto-specific factors dominate, ignore equity signals.
- **Signal type:** FILTER (correlation regime determines which signals to weight), SIZING (reduce when BTC-SPX correlation is high and SPX is declining).

---

## 9. COMPOSITE / META-INDICATORS

### 9.1 Multi-Factor Signal Combinations

#### Momentum Quality Score
- **Formula:** MQS = weighted average of: TSMOM signal, ADX level, OBV confirmation, volume trend. Each component normalized to [0, 1].
- **Causal mechanism:** Single indicators have high false positive rates. Combining orthogonal signals (momentum, trend strength, volume confirmation) reduces noise. The composite captures trades where price momentum is supported by volume and trend structure, filtering out noise-driven moves.
- **Key citation:** Asness, C.S., Moskowitz, T.J. & Pedersen, L.H. (2013). "Value and Momentum Everywhere." *Journal of Finance.* (Multi-factor methodology.)
- **1H crypto suitability:** HIGH. Combine 3-5 orthogonal signals into a composite score. Only enter when MQS > threshold (e.g., 3 of 5 conditions met).
- **Signal type:** ENTRY (composite score threshold), SIZING (score-weighted position size).

#### Regime-Adaptive Strategy Selection
- **Formula:** Use HMM/MSM regime probabilities and Hurst exponent to select strategy: Momentum (bull + trending), Mean Reversion (range-bound), Defensive (bear + high vol).
- **Causal mechanism:** No single strategy works in all market conditions. By detecting the regime first and then applying the appropriate strategy, the system avoids the primary failure mode of systematic trading: strategy-regime mismatch.
- **Key citation:** Ang, A. & Timmermann, A. (2012). "Regime Changes and Financial Markets." *Annual Review of Financial Economics.* Bae, G.I. et al. (2014). "Dynamic asset allocation for varied financial markets under regime switching." *European Journal of Operational Research.*
- **1H crypto suitability:** HIGH. Foundational architecture for a crypto swing system. Regime detection on 1H feeds strategy selection.
- **Signal type:** META (determines which other signals to apply).

### 9.2 Risk-Adjusted Position Sizing

#### Kelly Criterion (Fractional)
- **Formula:** f* = (p * b - q) / b, where p = win probability, b = win/loss ratio, q = 1 - p. Use fraction (e.g., half-Kelly: f*/2) for safety.
- **Causal mechanism:** Maximizes the long-run geometric growth rate of capital. Optimal bet sizing given known edge and odds. Over-betting destroys capital faster than under-betting, so fractional Kelly is used in practice to account for estimation uncertainty.
- **Key citation:** Kelly, J.L. (1956). "A New Interpretation of Information Rate." *Bell System Technical Journal.* Thorp, E. (2006). "The Kelly Criterion in Blackjack, Sports Betting, and the Stock Market."
- **1H crypto suitability:** HIGH. Estimate win rate and payoff ratio from recent trades. Apply half-Kelly or quarter-Kelly. Update estimates on rolling basis. Crypto's fat tails make full Kelly dangerous.
- **Signal type:** SIZING (position size from estimated edge).

#### Volatility-Parity Sizing
- **Formula:** Weight_i = (1/sigma_i) / sum(1/sigma_j) for all assets j. Position_i = target_vol * weight_i / sigma_i.
- **Causal mechanism:** Equalizes the risk contribution of each position so that no single asset dominates portfolio variance. In a multi-asset crypto portfolio, vol-parity prevents BTC (lower vol) from being under-allocated and small alts (higher vol) from being over-allocated relative to their risk contribution.
- **Key citation:** Maillard, S., Roncalli, T. & Teiletche, J. (2010). "The Properties of Equally Weighted Risk Contribution Portfolios." *Journal of Portfolio Management.*
- **1H crypto suitability:** HIGH. Essential for multi-asset crypto portfolios. Use Garman-Klass or GARCH vol estimates for sigma_i. Rebalance sizing daily or when vol changes significantly.
- **Signal type:** SIZING.

---

## 10. EMPIRICAL RESULTS (49-Token Signal Lab Analysis)

*Source: Signal Lab IC analysis across 49 tokens, 2024-01-01 to 2026-01-31 (762 days)*

### IC Rankings (Forward Return Predictability)

| Rank | Indicator | Avg |IC| | Verdict | Notes |
|------|-----------|---------|---------|-------|
| 1 | **ADX** | 0.067 | PREDICTIVE | IC increases with horizon (0.05→0.08) |
| 2 | realized_vol | 0.053 | PREDICTIVE | Leading indicator of forward returns |
| 3 | BB_width | 0.039 | WEAK | Consistent across horizons |
| 4 | vol_ratio | 0.027 | WEAK | Decays with horizon |
| 5 | taker | 0.021 | WEAK | Useful at 1d only (+0.035), gone by 5d |
| 16 | RSI | 0.004 | NOISE | Useless standalone despite popularity |

### Redundant Pairs (|corr| > 0.7) — Drop One

| Pair | Corr | Keep |
|------|------|------|
| RSI ↔ BB_pct | 0.952 | RSI |
| realized_vol ↔ parkinson_vol | 0.992 | realized_vol |
| taker ↔ taker_buy_ratio | 1.000 | taker |
| ret_1 ↔ vwap_deviation | 0.827 | ret_1 |
| ATR_pct ↔ vol_20 | 0.805 | ATR_pct |

### Truly Independent Signals (max |corr| < 0.5 with all others)

VPIN (0.310), amihud_1m (0.337), intraday_skew (0.323), intraday_kurtosis (0.310)

### Best Indicator by Regime

| Regime | Best Indicator | IC | Key Insight |
|--------|---------------|-----|-------------|
| Uptrend | ATR_pct | -0.072 | Low vol predicts continuation; momentum works WITH trend |
| Downtrend | **RSI** | **-0.145** | High RSI bounces = sell signals (opposite of textbook!) |
| Range | intraday_kurtosis | -0.072 | Microstructure beats traditional indicators |
| Quiet | ret_1 | +0.074 | Short-term momentum + order flow dominant |

### Top Actionable Combinations (2-indicator)

| Combo | Mean Ret | WR | Tokens | Use |
|-------|----------|-----|--------|-----|
| RSI_low + MACD_pos | +1.37% | 58.3% | 27 | LONG: dip buy + momentum confirm |
| vol_ratio_hi + BB_pct_low | +0.99% | 58.4% | 45 | LONG: capitulation buy (most robust) |
| vol_ratio_hi + ret_neg | +0.92% | 56.2% | 49 | LONG: volume spike reversal (universal) |
| ADX_weak + MACD_neg | -1.58% | 37.0% | 29 | AVOID: no trend + negative momentum |

---

## 11. IMPLEMENTATION PRIORITY MATRIX

For a 1H crypto swing trading system (18-720 hour holds), prioritize indicators by impact and feasibility:

### Tier 1: Essential (Implement First)
| Indicator | Category | Rationale |
|-----------|----------|-----------|
| Multi-horizon returns | Statistical | Foundational feature for any system |
| ATR | Volatility | Position sizing and stop placement |
| EMA crossover / HMA | Trend | Primary trend identification |
| RSI | Oscillator | Mean-reversion entry/exit |
| Supertrend | Trend | Clean trend signals for crypto |
| Bollinger Squeeze | Volatility | High-probability breakout entries |
| OBV / Delta Volume | Volume | Volume confirmation of moves |
| Hurst Exponent | Statistical | Regime classification (trend vs mean-revert) |
| Funding Rate | Crypto-specific | Crowded positioning detection |
| Rolling Volatility (GK) | Volatility | Accurate vol for sizing |

### Tier 2: High Value (Implement Second)
| Indicator | Category | Rationale |
|-----------|----------|-----------|
| TSMOM (multi-horizon) | Momentum | Research-backed momentum signal |
| KAMA | Trend | Adaptive trend following |
| MFI | Volume | Volume-weighted momentum |
| HMM Regime | Statistical | Strategy selection framework |
| GARCH(1,1) | Volatility | Conditional vol forecast |
| Amihud Illiquidity | Microstructure | Liquidity filter |
| Open Interest momentum | Crypto-specific | Derivatives positioning |
| ETH/BTC ratio | Lead-lag | Alt-season detection |
| Cross-correlation lags | Lead-lag | BTC-alt lead-lag exploitation |
| Skewness / Kurtosis | Statistical | Tail risk management |

### Tier 3: Alpha Enhancement (Implement Third)
| Indicator | Category | Rationale |
|-----------|----------|-----------|
| Cross-sectional momentum | Momentum | Universe ranking |
| VPIN | Microstructure | Flow toxicity detection |
| BOCPD | Statistical | Real-time regime change detection |
| Taker Buy Ratio | Crypto-specific | Order flow aggression |
| Liquidation cascades | Crypto-specific | Forced-flow opportunities |
| DXY / US10Y | Macro | Macro regime filter |
| CVaR / EVT | Statistical | Tail-aware risk management |
| Transfer Entropy | Lead-lag | Nonlinear information flow |
| HAR-RV | Volatility | Multi-scale vol forecast |
| Corwin-Schultz spread | Microstructure | Liquidity screening |

### Tier 4: Supplementary (Optional)
| Indicator | Category | Rationale |
|-----------|----------|-----------|
| MVRV / SOPR / NVT | On-chain | Slow-moving macro filters |
| Social sentiment | Alternative | Contrarian filter |
| Fear & Greed Index | Alternative | Regime context |
| DCC-GARCH | Correlation | Dynamic correlation modeling |
| Copula dependence | Correlation | Tail dependence |
| MESA / Ehlers filters | Trend | Advanced cycle analysis |
| News NLP sentiment | Alternative | Event-driven filter |
| Hash Rate / Hash Ribbons | On-chain | BTC-specific bottom detection |

---

## 12. BIBLIOGRAPHY (SELECTED KEY PAPERS)

1. Amihud, Y. (2002). "Illiquidity and Stock Returns." *Journal of Financial Markets* 5(1), 31-56.
2. Andersen, T.G. & Bollerslev, T. (1998). "Answering the Skeptics." *International Economic Review* 39(4), 885-905.
3. Antonacci, G. (2014). *Dual Momentum Investing.* McGraw-Hill.
4. Artzner, P. et al. (1999). "Coherent Measures of Risk." *Mathematical Finance* 9(3), 203-228.
5. Baker, M. & Wurgler, J. (2006). "Investor Sentiment and the Cross-Section of Stock Returns." *Journal of Finance* 61(4), 1645-1680.
6. Bollerslev, T. (1986). "Generalized Autoregressive Conditional Heteroscedasticity." *Journal of Econometrics* 31(3), 307-327.
7. Brock, W., Lakonishok, J. & LeBaron, B. (1992). "Simple Technical Trading Rules." *Journal of Finance* 47(5), 1731-1764.
8. Corsi, F. (2009). "A Simple Approximate Long-Memory Model of Realized Volatility." *Journal of Financial Econometrics* 7(2), 174-196.
9. Corwin, S.A. & Schultz, P. (2012). "A Simple Way to Estimate Bid-Ask Spreads." *Journal of Finance* 67(2), 719-760.
10. Easley, D., Lopez de Prado, M. & O'Hara, M. (2012). "Flow Toxicity and Liquidity." *Review of Financial Studies* 25(5), 1457-1493.
11. Engle, R. (2002). "Dynamic Conditional Correlation." *Journal of Business & Economic Statistics* 20(3), 339-350.
12. Garman, M.B. & Klass, M.J. (1980). "On the Estimation of Security Price Volatilities." *Journal of Business* 53(1), 67-78.
13. Granger, C.W.J. (1969). "Investigating Causal Relations." *Econometrica* 37(3), 424-438.
14. Hamilton, J.D. (1989). "A New Approach to the Economic Analysis of Nonstationary Time Series." *Econometrica* 57(2), 357-384.
15. Harvey, C.R. & Siddique, A. (2000). "Conditional Skewness in Asset Pricing Tests." *Journal of Finance* 55(3), 1263-1295.
16. Hurst, H.E. (1951). "Long-term Storage Capacity of Reservoirs." *Transactions of the ASCE* 116, 770-808.
17. Jegadeesh, N. & Titman, S. (1993). "Returns to Buying Winners and Selling Losers." *Journal of Finance* 48(1), 65-91.
18. Kyle, A. (1985). "Continuous Auctions and Insider Trading." *Econometrica* 53(6), 1315-1335.
19. Liu, Y. & Tsyvinski, A. (2021). "Risks and Returns of Cryptocurrency." *Review of Financial Studies* 34(6), 2689-2727.
20. Lo, A.W. & MacKinlay, A.C. (1988). "Stock Market Prices Do Not Follow Random Walks." *Review of Financial Studies* 1(1), 41-66.
21. Moskowitz, T., Ooi, Y.H. & Pedersen, L.H. (2012). "Time Series Momentum." *Journal of Financial Economics* 104(2), 228-250.
22. Nelson, D.B. (1991). "Conditional Heteroscedasticity in Asset Returns." *Econometrica* 59(2), 347-370.
23. Parkinson, M. (1980). "The Extreme Value Method for Estimating the Variance." *Journal of Business* 53(1), 61-65.
24. Roll, R. (1984). "A Simple Implicit Measure of the Effective Bid-Ask Spread." *Journal of Finance* 39(4), 1127-1139.
25. Yang, D. & Zhang, Q. (2000). "Drift-Independent Volatility Estimation." *Journal of Business* 73(3), 477-491.
