# R100: Trump Presidency Sentiment -> Crypto Returns (Category-Specific Analysis)

**Date:** 2026-03-24
**Analyst:** Quantitative Research Agent
**Period:** Jan 20, 2025 - Mar 14, 2026 (price data cutoff)
**Overall Verdict:** KILL -- No category produces a reliable, tradeable signal for crypto returns.

---

## 1. Executive Summary

This study re-evaluates Trump social media sentiment as a trading signal for BTC/ETH, focused exclusively on the 2nd presidency period (Jan 20, 2025 onward). Posts are categorized into 5 economic themes (TARIFF, FED, GEOPOLITICAL, ECONOMIC_CONFIDENCE, CRYPTO) and tested for predictive power via IC analysis, event studies, and cross-asset validation.

**Bottom line:** Despite 7,539 posts over 14 months of presidency, no category produces IC > 0.05 with |t| > 2.0 for crypto forward returns. One narrow hourly result (negative tariff posts -> BTC 1h returns, t=-2.57) is interesting but not robust across horizons. The signal is KILLED for crypto but shows marginal promise for FX/rates (DXY correlation with FED/TARIFF sentiment).

---

## 2. Data Summary

| Source | Posts | Date Range |
|--------|-------|------------|
| Truth Social (presidency period) | 7,539 | 2025-01-20 to 2026-03-24 |
| Posts with text content | 5,694 | (75.5%) |
| BTC hourly bars | 54,141 | to 2026-03-14 |
| ETH hourly bars | 54,141 | to 2026-03-14 |
| DXY daily bars | 14,022 | to 2026-03-23 |
| US10Y daily bars | 16,070 | to 2026-03-23 |

### Category Distribution

| Category | Posts | % of Total | Active Days | Event Days (3+) |
|----------|-------|-----------|-------------|-----------------|
| TARIFF | 360 | 4.8% | 200 | 40 |
| FED | 145 | 1.9% | 110 | 9 |
| GEOPOLITICAL | 1,087 | 14.4% | 319 | 124 |
| ECONOMIC_CONFIDENCE | 1,112 | 14.7% | 330 | 133 |
| CRYPTO | 13 | 0.2% | 12 | 0 |
| Uncategorized | 5,593 | 74.2% | - | - |

**Key observation:** The vast majority of Trump's posts are not economically relevant. Only ~26% match any category. The CRYPTO category is almost empty -- Trump has posted about crypto only 13 times in 14 months of presidency.

---

## 3. IC Analysis (Category-Specific vs BTC/ETH Forward Returns)

### Kill Criteria Check

| Category | Best |IC| | Best |t| | IC > 0.05 & |t| > 2.0? | Verdict |
|----------|---------|---------|-------------------------|---------|
| TARIFF | 0.1376 (post_count->14d BTC) | 1.92 | NO (t < 2.0) | FAIL (sign flip IS/OOS) |
| FED | 0.1710 (sentiment->1d BTC) | 1.76 | NO (t < 2.0) | FAIL |
| GEOPOLITICAL | 0.1123 (count_zscore->14d ETH) | 1.91 | NO (t < 2.0) | FAIL |
| ECONOMIC_CONFIDENCE | 0.1083 (post_count->7d ETH) | 1.92 | NO (t < 2.0) | FAIL |
| CRYPTO | N/A | N/A | N/A | INSUFFICIENT DATA |

**No category passes the IC kill criteria.** The closest is FED sentiment -> 1d BTC (IC=-0.171, t=-1.76, p=0.081) but this fails the |t| > 2.0 bar.

### Notable IC Results (Top 10)

| Category | Asset | Signal | Horizon | IC | t-stat | p-val | IS/OOS Flip? |
|----------|-------|--------|---------|-----|--------|-------|-------------|
| FED | BTC | mean_sentiment | 1d | -0.171 | -1.76 | 0.081 | No |
| FED | BTC | post_count | 14d | -0.169 | -1.72 | 0.089 | No |
| FED | ETH | mean_sentiment | 1d | -0.157 | -1.61 | 0.109 | No |
| FED | ETH | mean_sentiment | 7d | -0.155 | -1.59 | 0.114 | YES |
| TARIFF | BTC | post_count | 14d | +0.138 | +1.92 | 0.057 | YES |
| FED | BTC | mean_sentiment | 14d | -0.132 | -1.34 | 0.184 | No |
| TARIFF | ETH | post_count | 14d | +0.124 | +1.72 | 0.088 | YES |
| GEO | ETH | count_zscore | 14d | -0.112 | -1.91 | 0.057 | No |
| TARIFF | ETH | sentiment_int | 3d | -0.110 | -1.53 | 0.127 | No |
| FED | ETH | post_count | 1d | -0.109 | -1.11 | 0.268 | No |

### Interpretation

The FED category shows the most consistent signal direction (negative IC across horizons for BTC), suggesting that Fed-related posts weakly predict negative crypto returns. However:
- None reach statistical significance at the 5% level
- Several show IS/OOS sign flips at longer horizons
- Sample size is small (n=103-105 for FED)

The TARIFF post_count -> 14d return shows a positive IC (more tariff posts = higher 14d returns), but this flips IS/OOS, suggesting it's driven by a specific period rather than a stable relationship.

---

## 4. Event Studies (Event Day = 3+ Posts in Category)

### Daily Event Study Results

| Category | Asset | Event Days | Horizon | Event Mean | Non-Event Mean | Diff | t-stat | p-val |
|----------|-------|-----------|---------|-----------|---------------|------|--------|-------|
| TARIFF | BTC | 40 | 1d | -0.200% | +0.053% | -0.253% | -0.57 | 0.568 |
| TARIFF | BTC | 40 | 14d | +0.160% | -1.164% | +1.325% | +0.93 | 0.354 |
| FED | BTC | 9 | 1d | -0.834% | +0.225% | -1.059% | -1.29 | 0.228 |
| FED | BTC | 9 | 14d | -3.670% | -0.016% | -3.655% | -1.06 | 0.315 |
| GEO | BTC | 120 | 1d | +0.096% | +0.015% | +0.081% | +0.29 | 0.773 |
| ECON_CONF | ETH | 133 | 7d | +0.585% | -1.295% | +1.880% | +1.53 | 0.126 |

**No event study result is statistically significant at the 5% level.**

The FED category event days show large negative point estimates (BTC -0.83% on day 1, -3.67% over 14d) but with only 9 event days, statistical power is negligible.

ECONOMIC_CONFIDENCE event days show a +1.88% 7-day excess return for ETH (t=1.53, p=0.126) -- the most interesting directional finding -- but not significant.

---

## 5. Negative Sentiment Deep Dive (Hourly)

This test examines whether negative-sentiment posts within each category predict short-term crypto drops.

| Category | Asset | Window | N Events | Mean Return | t-stat | p-val | % Negative |
|----------|-------|--------|----------|-------------|--------|-------|-----------|
| **TARIFF** | **BTC** | **1h** | **35** | **-0.243%** | **-2.57** | **0.015** | **65.7%** |
| TARIFF | BTC | 4h | 35 | -0.283% | -1.62 | 0.115 | 57.1% |
| TARIFF | BTC | 24h | 35 | -0.382% | -0.87 | 0.390 | 51.4% |
| TARIFF | BTC | 48h | 35 | +0.234% | +0.49 | 0.625 | 51.4% |
| GEO | BTC | 1h | 94 | -0.071% | -1.38 | 0.171 | 50.0% |
| FED | BTC | 1h | 17 | -0.009% | -0.07 | 0.948 | 58.8% |

**One significant result:** Negative tariff posts predict a -0.24% BTC move in the following hour (t=-2.57, p=0.015, 65.7% of events negative). However:
- The effect dissipates rapidly (4h: t=-1.62, 24h: t=-0.87)
- Only 35 observations
- Mean return of -0.24% per event is modest relative to BTC hourly vol
- Does not persist beyond 1 hour, suggesting a very short-lived reaction

This is consistent with a "market reacts to tariff news" effect that is partially conveyed via Trump posts, but the effect is too short-lived and small to be tradeable after transaction costs.

---

## 6. Cross-Asset Validation (DXY, US10Y)

**This is the most interesting finding in the study.** Trump social media sentiment shows meaningful correlation with FX/rates, even where crypto correlations fail.

| Category | Macro Asset | Signal | Horizon | IC | t-stat | p-val |
|----------|-------------|--------|---------|-----|--------|-------|
| **FED** | **DXY** | **sentiment** | **1d** | **+0.238** | **+2.28** | **0.025** |
| **TARIFF** | **DXY** | **sentiment** | **3d** | **+0.187** | **+2.35** | **0.020** |
| FED | US10Y | post_count | 1d | -0.207 | -1.97 | 0.052 |
| FED | DXY | sentiment | 3d | +0.188 | +1.77 | 0.080 |
| TARIFF | US10Y | post_count | 7d | +0.149 | +1.85 | 0.066 |
| ECON_CONF | DXY | sentiment | 3d | +0.126 | +1.95 | 0.052 |

### Interpretation

Two results pass the IC > 0.05 and |t| > 2.0 threshold for macro assets:
1. **FED sentiment -> DXY 1d** (IC=+0.238, t=+2.28): Positive Fed-related sentiment predicts dollar strength next day
2. **TARIFF sentiment -> DXY 3d** (IC=+0.187, t=+2.35): Positive tariff sentiment predicts dollar strength over 3 days

This validates the theoretical mechanism: Trump posts about trade/Fed policy do move macro variables (FX, rates). However, the crypto market does not react reliably to these same posts. The transmission chain "Trump post -> macro shift -> crypto impact" breaks at the second link.

---

## 7. Category Verdicts

| Category | Verdict | Rationale |
|----------|---------|-----------|
| **TARIFF** | **INSUFFICIENT DATA** | 40 event days (< 50 threshold). Marginal IC signals exist. Negative sentiment -> BTC 1h is significant but too short-lived. Need 6+ more months of data. |
| **FED** | **INSUFFICIENT DATA** | Only 9 event days. FED sentiment IC is the highest of any category (-0.171 for BTC 1d) but undersized. Strong DXY correlation validates mechanism. |
| **GEOPOLITICAL** | **KILL** | 120 event days (sufficient data). No IC signal passes any threshold. Event study shows zero differentiation. Despite being Trump's most common topic, geopolitical posts do not predict crypto moves. |
| **ECONOMIC_CONFIDENCE** | **NEEDS MORE DATA** | 133 event days. Marginal IC for ETH 7d returns (IC=+0.108, t=+1.92). Economic confidence event days show +1.88% ETH 7d excess but p=0.126. Not ready to kill. |
| **CRYPTO** | **INSUFFICIENT DATA** | Only 13 posts, 0 event days. Trump simply does not post about crypto often enough as President for this to be a tradeable signal. |

---

## 8. Key Findings

1. **Trump does not post about crypto enough as President.** 13 posts in 14 months. This category cannot generate a signal.

2. **The FED category is the most promising but undersized.** IC of -0.171 for 1d BTC returns and strong DXY correlation (IC=+0.238, t=+2.28). With more data, this could become significant.

3. **Negative tariff posts produce a real but untradeable 1-hour BTC dip** (t=-2.57). The effect reverses within 4 hours. This suggests markets react to tariff news, but the signal is too fast and too small for a systematic strategy.

4. **GEOPOLITICAL posts are definitively KILLED.** Despite being 14.4% of all posts, they show zero predictive power for crypto. The market apparently prices geopolitical risk through other channels.

5. **Cross-asset validation suggests the mechanism is real for FX/rates but does not transmit to crypto.** FED and TARIFF posts predict DXY moves at |t| > 2.0, but the same posts do not predict BTC/ETH moves.

6. **74% of posts are uncategorized** -- Trump talks mostly about politics, media, and personal topics that have no economic content.

---

## 9. Recommendations

1. **Do not build a Trump sentiment trading signal for crypto.** The data does not support it.

2. **Revisit FED/TARIFF categories in 6 months** (by Sep 2026) when event day counts should cross the 50-day threshold. The FED category especially deserves another look.

3. **Consider a DXY/rates signal instead.** The cross-asset results (FED sentiment -> DXY IC=+0.238, TARIFF sentiment -> DXY IC=+0.187) suggest Trump posts may have tradeable value for FX markets, which could then be used as an input to a macro-regime model for crypto positioning.

4. **If revisiting, improve categorization.** The current keyword approach misses context. A small LLM (e.g., Claude Haiku) could improve classification accuracy, especially for distinguishing "tariff announcement" from "tariff complaint."

---

## 10. Methodology Notes

- **IC computation:** Spearman rank correlation between daily signal values and forward returns
- **t-stat:** Derived from IC as `IC * sqrt(n-2) / sqrt(1 - IC^2)`
- **Event study:** Welch two-sample t-test comparing returns on event days (3+ category posts) vs non-event days
- **Hourly event study:** One-sample t-test of returns in windows after negative-sentiment posts
- **Cross-asset:** Same IC methodology applied to DXY and US10Y forward returns
- **IS/OOS split:** First half vs second half of sample (temporal split)

**Script:** `/workspace/crypto_backtest/research/R100_trump_presidency_sentiment.py`
**Data:** `/workspace/crypto_backtest/data/alternative/trump_social/posts_scored.csv`
