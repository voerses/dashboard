# Indicator Catalog -- Formula Reference (Archived)

> **Archived from:** `knowledge/INDICATOR_CATALOG.md` sections 1-4
> **Reason:** Standard textbook formulas, derivable from any reference. Kept for lookup only.
> **Active file:** `knowledge/INDICATOR_CATALOG.md` (empirical results, priority matrix, crypto-specific signals)

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
