# Wave 5 Signal Scout Results

**Date:** 2026-03-24
**Temporal split:** Train < 2025-07-01 (IS), Test >= 2025-07-01 (OOS)
**Pass criteria:** IC > 0.05, t-stat > 2, sign consistent IS -> OOS, hit rate > 0.55

---

## Signal 1: CTREND (Cross-Sectional Trend Quality)

### Hypothesis
Rank tokens by trend **quality** (not raw return). Use ADX, directional efficiency ratio (DER = net displacement / total path), and R-squared of log-price regression. Composite signal = signed_R2 * DER. Long tokens with high trend quality, short those with low quality. Different from existing cross-sectional momentum which uses raw return.

### Data
Price data from `data/perp/1h_cache/`. 10 tokens: BTC, ETH, SOL, DOGE, ADA, XRP, DOT, LINK, AVAX, UNI. Resampled to daily. 30-day lookback for DER and R2, 14-period ADX.

### Results (per-token IC, Spearman rank correlation)

| Token | Horizon | IS IC | OOS IC | t-stat | Hit Rate | Sign Consistent |
|-------|---------|-------|--------|--------|----------|----------------|
| BTC | 7d | 0.0934 | -0.0123 | -6.48 | 0.28 | NO |
| ETH | 7d | 0.1046 | 0.1101 | -6.96 | 0.22 | YES |
| SOL | 7d | 0.1241 | 0.0520 | -4.22 | 0.33 | YES |
| DOGE | 7d | 0.0240 | -0.0959 | -4.08 | 0.34 | NO |
| ADA | 7d | 0.0750 | -0.0284 | -7.80 | 0.22 | NO |
| XRP | 7d | -0.0401 | -0.1472 | -4.40 | 0.35 | YES |
| DOT | 7d | 0.0687 | -0.1922 | -6.57 | 0.29 | NO |
| LINK | 7d | 0.0100 | 0.0282 | -4.25 | 0.34 | YES |
| AVAX | 7d | 0.0864 | 0.0481 | -5.75 | 0.18 | YES |
| UNI | 7d | 0.0907 | -0.0034 | -5.94 | 0.23 | NO |
| BTC | 14d | 0.1208 | -0.0551 | -4.43 | 0.30 | NO |
| ETH | 14d | 0.1296 | 0.1439 | -4.35 | 0.32 | YES |
| SOL | 14d | 0.1263 | -0.0024 | -4.67 | 0.32 | NO |
| DOT | 14d | 0.0910 | -0.3186 | -7.96 | 0.17 | NO |
| XRP | 14d | -0.1192 | -0.2001 | -3.29 | 0.38 | YES |

**Panel IC (all tokens pooled):**

| Horizon | IS IC | OOS IC | t-stat | Hit Rate | Sign Consistent |
|---------|-------|--------|--------|----------|----------------|
| 7d | 0.0701 | -0.0132 | -3.90 | 0.20 | NO |
| 14d | 0.0744 | -0.0397 | -2.22 | 0.32 | NO |

### Verdict: KILL

Strong IS signal (IC ~0.07-0.12) completely breaks down OOS. Panel IC flips sign. t-stats are deeply negative (wrong-way signal OOS). Hit rates below 35%. Classic overfitting pattern -- trend quality works in-sample but the OOS period (2H 2025 - 2026) is a different regime. The composite R2*DER metric has no predictive power for forward returns out of sample.

---

## Signal 2: Liquidation Cascade Risk Model

### Hypothesis
When leverage is dangerously high (extreme absolute funding rates, high price fragility), the market is prone to cascading liquidations. Two sub-signals:
1. **Leverage risk composite**: |funding_rate_z| + fragility_z (where fragility = realized_vol / log(volume))
2. **Funding contrarian**: inverted funding rate z-score (high positive funding = bearish, negative = bullish)

### Data
Funding rate from hourly OHLCV files (embedded column). OKX liquidation tick data available but only covers ~1 day (insufficient for IS/OOS). Binance OI data only covers 2026-03-03 to 2026-03-23 (20 days, unusable for splits).

### Results

**Leverage risk composite (|funding_z| + fragility_z):**

| Token | Horizon | IS IC | OOS IC | t-stat | Hit Rate | Sign Consistent |
|-------|---------|-------|--------|--------|----------|----------------|
| BTC | 7d | 0.0152 | -0.0048 | -0.30 | 0.50 | NO |
| BTC | 14d | -0.0271 | -0.0454 | -0.19 | 0.50 | YES |
| ETH | 7d | 0.0250 | 0.0071 | 0.21 | 0.38 | YES |
| ETH | 14d | 0.0393 | -0.0394 | 0.26 | 0.62 | NO |
| SOL | 7d | 0.0593 | -0.0889 | -1.87 | 0.12 | NO |
| SOL | 14d | 0.0419 | -0.0977 | -4.82 | 0.12 | NO |
| DOGE | 7d | -0.0001 | 0.0322 | 0.51 | 0.62 | NO |
| DOGE | 14d | 0.0288 | -0.0446 | -0.97 | 0.50 | NO |

**Funding contrarian (-funding_z):**

| Token | Horizon | IS IC | OOS IC | t-stat | Hit Rate | Sign Consistent |
|-------|---------|-------|--------|--------|----------|----------------|
| BTC | 7d | 0.0766 | 0.0861 | -0.01 | 0.50 | YES |
| BTC | 14d | 0.0499 | 0.0708 | 0.11 | 0.50 | YES |
| ETH | 7d | 0.0588 | -0.0097 | -1.59 | 0.25 | NO |
| ETH | 14d | 0.0427 | 0.0487 | -0.62 | 0.50 | YES |
| SOL | 7d | -0.0045 | 0.1081 | 0.55 | 0.62 | NO |
| SOL | 14d | 0.0056 | 0.1379 | 0.91 | 0.62 | YES |
| DOGE | 7d | 0.0532 | -0.0139 | -0.29 | 0.50 | NO |
| DOGE | 14d | 0.0218 | 0.0746 | 0.83 | 0.62 | YES |

**Extreme regime quintile analysis (OOS only, 7d forward returns):**

| Token | Low Leverage Avg | High Leverage Avg | Spread |
|-------|-----------------|-------------------|--------|
| BTC | +0.06% | -0.53% | +0.59% |
| ETH | -0.49% | +0.26% | -0.74% (wrong way) |
| SOL | -0.14% | -1.55% | +1.41% |

### Verdict: KILL (leverage composite) / WEAK PASS (funding contrarian, BTC only)

The leverage risk composite has no signal -- ICs near zero, no consistency. The funding contrarian shows a **persistent positive IC for BTC** (IS: 0.077, OOS: 0.086 at 7d; IS: 0.050, OOS: 0.071 at 14d) with sign consistency. However, t-stats are near zero and hit rates are 50% -- the signal exists but is too noisy to trade standalone. The quintile analysis confirms: high leverage days do see worse BTC returns (+0.59% spread for BTC), but the effect is small and inconsistent across tokens.

**Note:** The actual liquidation data (OKX) and OI data (Binance) cover only ~1-20 days. A proper test requires 6+ months of hourly OI and liquidation flow data. This is a DATA LIMITATION, not a kill of the hypothesis.

**Action item:** Fetch historical OI + liquidation data (Coinalyze API, CryptoQuant, or Glassnode) covering 2024-2026 for a proper test. Signal is plausible but cannot be validated with current data.

---

## Signal 3: Bitcoin Dominance / Rotation Signal

### Hypothesis
BTC dominance changes predict alt-season vs BTC-season. Constructed as BTC N-day return minus average alt N-day return (relative performance proxy). Three sub-tests:
1. **Continuation**: BTC outperformance predicts continued BTC outperformance
2. **Rotation**: BTC outperformance predicts alt underperformance
3. **Reversal**: Extreme BTC dominance mean-reverts (alts bounce)

### Data
Derived from price data (no external BTC.D feed needed). Tested with 14d, 30d, and 60d lookback windows. Z-scored over 90-day rolling window.

### Results (30d lookback, representative)

**Test 3a: BTC dominance -> BTC forward returns (continuation)**

| Lookback | Horizon | IS IC | OOS IC | t-stat | Hit Rate | Sign Consistent |
|----------|---------|-------|--------|--------|----------|----------------|
| 14d | 7d | -0.0176 | -0.0737 | 0.88 | 0.75 | YES |
| 14d | 14d | 0.0440 | -0.2146 | 0.17 | 0.50 | NO |
| 30d | 7d | 0.0235 | -0.1358 | 0.62 | 0.62 | NO |
| 30d | 14d | 0.0798 | -0.1269 | 0.94 | 0.50 | NO |
| 60d | 7d | 0.0632 | -0.1352 | 0.24 | 0.62 | NO |
| 60d | 14d | 0.0692 | -0.1970 | -0.11 | 0.50 | NO |

**Test 3b: BTC dominance -> alt forward returns**

| Token | Lookback | Horizon | IS IC | OOS IC | Sign Consistent |
|-------|----------|---------|-------|--------|----------------|
| ETH | 30d | 14d | 0.0298 | -0.2912 | NO |
| SOL | 30d | 14d | 0.0848 | -0.2140 | NO |
| DOGE | 30d | 14d | 0.0459 | -0.0659 | NO |
| ADA | 30d | 14d | 0.1269 | -0.1369 | NO |

**Test 3c: BTC dominance REVERSAL -> alt forward returns**

| Token | Lookback | Horizon | IS IC | OOS IC | Sign Consistent |
|-------|----------|---------|-------|--------|----------------|
| ETH | 60d | 14d | -0.0836 | 0.2965 | NO |
| SOL | 60d | 14d | -0.0857 | 0.3561 | NO |
| DOGE | 60d | 14d | -0.0636 | 0.1881 | NO |
| ADA | 60d | 14d | -0.1480 | 0.2601 | NO |

### Verdict: KILL (continuation/rotation) / INTERESTING BUT UNSTABLE (reversal)

The continuation and rotation hypotheses are dead -- IS IC flips sign OOS across all lookback windows and all tokens. The market does NOT trend in a consistent BTC-vs-alt rotation pattern.

The **reversal** hypothesis shows a curious pattern: OOS ICs are large and positive (0.19 - 0.36) at 60d lookback for 14d forward, meaning extreme BTC dominance IS followed by alt outperformance. However, IS ICs are negative (wrong sign in-sample), meaning the effect is **regime-dependent** and only appeared in 2H 2025 - 2026. This is likely an artifact of the recent BTC correction / alt rally cycle and would not survive a longer OOS window. t-stats are near zero. Not actionable.

---

## Signal 4: Correlation Breakdown

### Hypothesis
When BTC-alt correlations drop (dispersion increases), it signals a regime change. Three sub-signals:
1. **Average BTC-alt correlation level** -> BTC forward returns
2. **Correlation change (7d delta)** -> alt forward returns
3. **Cross-alt correlation dispersion** -> BTC forward returns

### Data
Rolling 30-day Spearman correlation between BTC daily returns and each of 9 alts. 1972 daily observations across 10 tokens.

### Results

**Test 4a: Avg BTC-alt correlation level -> BTC forward returns**

| Horizon | IS IC | OOS IC | t-stat | Hit Rate | Sign Consistent |
|---------|-------|--------|--------|----------|----------------|
| 7d | -0.0928 | -0.0651 | 0.53 | 0.50 | YES |
| 14d | -0.0553 | -0.0047 | 0.43 | 0.62 | YES |

**Test 4b: Individual BTC-alt correlation change -> alt forward returns**

| Token | Horizon | IS IC | OOS IC | t-stat | Hit Rate | Sign Consistent |
|-------|---------|-------|--------|--------|----------|----------------|
| ETH | 7d | -0.0459 | -0.0160 | 0.41 | 0.38 | YES |
| SOL | 7d | -0.1138 | -0.0069 | 0.19 | 0.50 | YES |
| DOGE | 7d | -0.0842 | 0.1007 | 1.43 | 0.62 | NO |
| ADA | 7d | -0.0947 | -0.1414 | -0.03 | 0.50 | YES |
| DOGE | 14d | -0.0444 | 0.2074 | 2.07 | 0.75 | NO |
| ADA | 14d | -0.1270 | -0.0873 | 0.54 | 0.62 | YES |

**Test 4c: Cross-alt correlation dispersion -> BTC forward returns**

| Horizon | IS IC | OOS IC | t-stat | Hit Rate | Sign Consistent |
|---------|-------|--------|--------|----------|----------------|
| 7d | 0.0493 | 0.1127 | -0.06 | 0.38 | YES |
| 14d | 0.0075 | 0.0331 | -0.16 | 0.50 | YES |

### Verdict: KILL (weak, noisy, not actionable)

The correlation level signal (4a) shows consistent negative IC -- lower correlation = slightly better BTC returns -- but the effect is tiny (OOS IC: -0.065 at 7d, -0.005 at 14d) and t-stats are well below 2. The correlation change signal (4b) has sporadic large OOS ICs (DOGE 14d: 0.207) but sign-inconsistent and t-stats too low. The dispersion signal (4c) shows sign consistency but negligible IC and negative t-stats.

The correlation structure is **descriptive** (it tells you what regime you're in) but not **predictive** (it doesn't forecast forward returns reliably). Could serve as a regime filter for other signals but not standalone.

---

## Signal 5: VWAP Deviation

### Hypothesis
Price deviation from VWAP (Volume-Weighted Average Price) over rolling windows is a mean-reversion signal. When price is far above VWAP, it reverts down. Tested both momentum (deviation predicts continuation) and contrarian (deviation predicts reversal). Tested 24h, 72h, 168h VWAP windows.

### Data
Hourly OHLCV data. VWAP = sum(typical_price * volume) / sum(volume) over rolling window. Signal = z-score of (close - VWAP) / VWAP.

### Results (representative, 72h VWAP window)

**VWAP deviation (momentum direction):**

| Token | VWAP Window | Horizon | IS IC | OOS IC | t-stat | Hit Rate | Sign Consistent |
|-------|-------------|---------|-------|--------|--------|----------|----------------|
| BTC | 24h | 7d | -0.0014 | 0.0347 | 0.65 | 0.62 | NO |
| BTC | 72h | 7d | -0.0230 | -0.0024 | -0.22 | 0.50 | YES |
| BTC | 168h | 7d | -0.0437 | -0.0272 | -0.91 | 0.50 | YES |
| ETH | 72h | 7d | 0.0203 | -0.0304 | 0.08 | 0.50 | NO |
| SOL | 72h | 7d | 0.0150 | 0.0187 | -0.16 | 0.62 | YES |
| DOGE | 72h | 14d | 0.0192 | -0.0812 | -4.31 | 0.12 | NO |

**VWAP deviation contrarian (inverted):**

| Token | Horizon | IS IC | OOS IC | t-stat | Hit Rate | Sign Consistent |
|-------|---------|-------|--------|--------|----------|----------------|
| BTC | 7d | 0.0230 | 0.0024 | 0.22 | 0.50 | YES |
| SOL | 14d | 0.0035 | 0.0645 | 1.61 | 0.75 | YES |
| DOGE | 14d | -0.0192 | 0.0812 | 4.31 | 0.88 | NO |

### Verdict: KILL

All ICs are microscopic (|IC| < 0.05 with rare exceptions). t-stats are near zero or negative. No VWAP window (24h, 72h, 168h) produces a reliable signal. The one outlier -- DOGE contrarian 14d with t=4.31 and hit=0.88 -- is sign-inconsistent (IS negative, OOS positive) and is likely a regime artifact from a single DOGE rally in the OOS window.

VWAP deviation has **no predictive power** for 7d or 14d crypto returns. The mean-reversion effect, if it exists, operates on much shorter timescales (minutes to hours) where market-making algorithms dominate, not on the multi-day horizons relevant for our trading system.

---

## Summary Table

| # | Signal | Verdict | Best OOS IC | t-stat | Notes |
|---|--------|---------|-------------|--------|-------|
| 1 | CTREND (trend quality) | **KILL** | -0.013 (panel 7d) | -3.90 | Strong IS, zero OOS. Classic overfit. |
| 2 | Liquidation cascade risk | **KILL** (composite) / **NEEDS DATA** (funding contrarian) | 0.086 (BTC funding ctr 7d) | ~0 | Funding contrarian has signal for BTC but too noisy. Need historical OI + liquidation data. |
| 3 | BTC dominance rotation | **KILL** | -0.136 (30d, BTC 7d) | 0.62 | No predictive power for rotation. Reversal effect is regime-dependent artifact. |
| 4 | Correlation breakdown | **KILL** | -0.065 (corr level 7d) | 0.53 | Descriptive, not predictive. Potential regime filter only. |
| 5 | VWAP deviation | **KILL** | 0.035 (BTC 24h 7d) | 0.65 | No signal at multi-day horizons. |

## Recommendations

1. **All 5 signals fail the pass bar** (IC > 0.05, t > 2, sign consistent). None should be promoted to strategy development.

2. **Data acquisition priority:** Signal 2 (liquidation cascade) is the only one with a plausible mechanism that cannot be properly tested due to data limitations. Action: fetch 12+ months of hourly OI (Coinalyze), liquidation flow (CryptoQuant/Glassnode), and aggregate leverage ratio data. This would enable a proper test of the "leverage fragility" hypothesis.

3. **Signal 4 (correlation) as regime filter:** While it has no standalone predictive power, the average BTC-alt correlation level could be used as a **conditioning variable** for existing signals. For example: only trade momentum signals when correlation > 0.7 (trending regime) and mean-reversion signals when correlation < 0.5 (dispersed regime). This would be a cheap addition to test in Wave 6.

4. **Next wave candidates to explore:**
   - **Stablecoin flow rate-of-change** (USDT/USDC mint/burn as demand proxy)
   - **Exchange netflow acceleration** (rate of change of netflow, not level)
   - **Implied vs realized vol term structure** (DVOL 1w vs 1m spread if Deribit data extends)
   - **On-chain active address momentum** (if Glassnode/CryptoQuant data acquired)
   - **Cross-exchange basis spreads** (Binance vs Bybit vs OKX perp basis)
