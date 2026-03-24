# Trump Presidency Sentiment Signal Analysis

**Date**: 2026-03-24
**Research ID**: R94 (follow-up to R93)
**Period**: Jan 20, 2025 - Mar 14, 2026 (presidency only)
**Previous Finding**: R93 tested ALL data (2020-2026) and found tariff IC=-0.076 at 7d. Signal flipped between eras.

## Motivation

R93 mixed pre-presidency posts (2020-2024) where Trump's social media had no direct policy impact with presidency posts (2025+) where posts move markets. This analysis isolates the presidency period only and builds broader economic sentiment categories beyond simple "tariff" keyword matching.

---

## 1. Presidency Period Data Summary

| Metric | Value |
|--------|-------|
| Total presidency posts | 7,539 |
| Date range | 2025-01-20 to 2026-03-24 |
| Calendar days | 429 |
| Posting days | 426 (99.3% of days) |
| Average posts/day | 17.6 |
| Median posts/day | 14 |
| Max posts/day | 142 (2025-03-10) |
| Source | 100% Truth Social |

**Monthly Posting Volume:**
| Month | Posts |
|-------|-------|
| 2025-01 | 165 |
| 2025-02 | 504 |
| 2025-03 | 638 |
| 2025-04 | 461 |
| 2025-05 | 515 |
| 2025-06 | 504 |
| 2025-07 | 613 |
| 2025-08 | 570 |
| 2025-09 | 415 |
| 2025-10 | 482 |
| 2025-11 | 609 |
| 2025-12 | 476 |
| 2026-01 | 606 |
| 2026-02 | 555 |
| 2026-03 | 426 |

**Peak posting hours (ET):** 5-6 PM (537), 10 AM (535), 6-7 PM (502), 3 PM (487), 9 AM (485). Mornings and late afternoon dominate.

---

## 2. Category Classification Results

### Category Totals (Presidency Period)

| Category | Posts | % of Total |
|----------|-------|------------|
| **A. Trade War / Tariff** | | |
| trade_war (any trade keyword) | 648 | 8.6% |
| trade_escalation | 250 | 3.3% |
| trade_deescalation | 152 | 2.0% |
| **B. Fed / Monetary Policy** | | |
| fed_monetary | 193 | 2.6% |
| fed_hawkish_complaint | 50 | 0.7% |
| fed_dovish_support | 114 | 1.5% |
| **C. Geopolitical Risk** | | |
| geopolitical | 1,533 | 20.3% |
| geo_high_risk | 394 | 5.2% |
| geo_low_risk | 320 | 4.2% |
| **D. Economic Confidence** | | |
| econ_confidence | 1,359 | 18.0% |
| econ_bullish | 957 | 12.7% |
| econ_bearish | 125 | 1.7% |
| **E. Crypto-Specific** | | |
| crypto | 64 | 0.8% |
| crypto_pro | 36 | 0.5% |
| crypto_anti | 5 | 0.1% |

**Key observation:** Geopolitical (20.3%) and economic confidence (18.0%) dominate. Trade posts are 8.6%. Crypto posts are extremely sparse (0.8%, only 64 posts in 14 months). Fed commentary is 2.6%.

### Monthly Category Heatmap

| Month | Trade | Trade Esc | Trade De-esc | Fed | Geo | Econ Bull | Econ Bear | Crypto |
|-------|-------|-----------|-------------|-----|-----|-----------|-----------|--------|
| 2025-01 | 6 | 1 | 1 | 3 | 9 | 7 | 2 | 1 |
| 2025-02 | 49 | 22 | 12 | 4 | 76 | 36 | 7 | 2 |
| 2025-03 | 68 | 20 | 5 | 21 | 102 | 50 | 6 | 6 |
| 2025-04 | 69 | 27 | 19 | 12 | 75 | 48 | 4 | 3 |
| 2025-05 | 50 | 16 | 14 | 12 | 86 | 63 | 2 | 2 |
| 2025-06 | 46 | 10 | 14 | 15 | 133 | 45 | 5 | 9 |
| 2025-07 | 58 | 22 | 20 | 18 | 87 | 67 | 8 | 12 |
| 2025-08 | 34 | 16 | 5 | 17 | 111 | 51 | 8 | 7 |
| 2025-09 | 35 | 18 | 5 | 10 | 74 | 27 | 12 | 1 |
| 2025-10 | 37 | 13 | 10 | 4 | 135 | 85 | 7 | 2 |
| 2025-11 | 36 | 16 | 8 | 21 | 199 | 177 | 18 | 2 |
| 2025-12 | 28 | 10 | 3 | 12 | 77 | 47 | 9 | 6 |
| 2026-01 | 49 | 19 | 14 | 24 | 127 | 100 | 15 | 4 |
| 2026-02 | 50 | 23 | 18 | 9 | 134 | 117 | 13 | 3 |
| 2026-03 | 33 | 17 | 4 | 11 | 108 | 37 | 9 | 4 |

**Observations:**
- Trade posts peaked in Feb-Apr 2025 (Liberation Day period)
- Geo posts surged Nov 2025 (116 posts on Nov 3 alone)
- Econ bullish posts peaked Nov 2025 (177 posts)
- Crypto posts peaked Jun-Jul 2025 (post-GENIUS Act)

---

## 3. IC Table: Sentiment Signals vs BTC Forward Returns

### Raw Signal ICs (Spearman rank correlation, N=409 days)

| Signal | 1d IC | 3d IC | 7d IC | 14d IC |
|--------|-------|-------|-------|--------|
| **trade_war** | 0.0251 | 0.0299 | -0.0057 | 0.0681 |
| **trade_escalation** | 0.0054 | 0.0568 | 0.0432 | 0.0632 |
| **trade_deescalation** | -0.0504 | -0.0478 | **-0.0786** | 0.0209 |
| **trade_net** | **0.0798** | **0.1142** | **0.1085** | 0.0575 |
| fed_monetary | -0.0270 | -0.0147 | **0.0914** | 0.0740 |
| fed_net | **0.0872** | 0.0473 | 0.0098 | -0.0147 |
| geopolitical | -0.0077 | 0.0060 | -0.0384 | -0.0489 |
| geo_high_risk | 0.0346 | -0.0458 | -0.0563 | -0.0055 |
| geo_net | 0.0783 | -0.0147 | 0.0269 | 0.0702 |
| econ_confidence | 0.0001 | -0.0153 | -0.0402 | -0.0273 |
| econ_bullish | -0.0079 | -0.0429 | -0.0308 | -0.0401 |
| **econ_bearish** | 0.0514 | 0.0064 | -0.0404 | **-0.0898** |
| econ_net | -0.0326 | -0.0567 | -0.0241 | -0.0254 |
| crypto | 0.0078 | -0.0078 | 0.0116 | 0.0669 |
| crypto_pro | -0.0123 | -0.0075 | 0.0409 | 0.0586 |
| crypto_net | -0.0493 | -0.0022 | 0.0513 | 0.0507 |
| policy_uncertainty | -0.0203 | 0.0069 | -0.0225 | -0.0056 |
| total_posts | 0.0001 | 0.0349 | -0.0144 | -0.0301 |

### Z-Score Normalized ICs (14-day rolling z-score)

| Signal | 1d IC | 3d IC | 7d IC | 14d IC |
|--------|-------|-------|-------|--------|
| **trade_net** | 0.0572 | **0.0873** | **0.1165** | **0.0848** |
| trade_deescalation | -0.0316 | -0.0650 | **-0.1581** | -0.0813 |
| **econ_bearish** | **0.0887** | **0.1024** | **0.1094** | **0.1421** |
| fed_net | **0.0980** | **0.1162** | 0.0737 | 0.0169 |
| crypto (z-scored) | -0.0397 | -0.0979 | **-0.1623** | **-0.2408** |

**Top findings:**
1. **trade_net** (escalation minus de-escalation) is the strongest raw signal: IC=0.114 at 3d, 0.109 at 7d. This is POSITIVE -- escalation predicts BTC UP, counterintuitively.
2. **econ_bearish** z-scored shows IC=0.142 at 14d -- when Trump's bearish economic posts spike above recent normal, BTC falls over 2 weeks.
3. **fed_net** z-scored shows IC=0.116 at 3d -- when Fed complaints spike, BTC rises short-term.
4. **crypto** z-scored is strongly NEGATIVE at longer horizons (IC=-0.24 at 14d) -- but sample is tiny (64 posts).

### Bootstrap IC Confidence Intervals (1000 iterations)

| Signal | Horizon | IC | 95% CI | p(>0) | Verdict |
|--------|---------|-----|--------|-------|---------|
| trade_net | 3d | 0.1142 | [0.017, 0.208] | 99.1% | WEAK |
| trade_net | 7d | 0.1085 | [0.015, 0.203] | 98.7% | WEAK |
| trade_net | 1d | 0.0798 | [-0.013, 0.177] | 95.6% | NOISE |
| fed_net | 1d | 0.0872 | [-0.010, 0.188] | 96.1% | WEAK |
| fed_monetary | 7d | 0.0914 | [-0.008, 0.189] | 96.6% | WEAK |
| econ_bearish | 14d | -0.0898 | [-0.189, 0.009] | 4.0% | WEAK |
| All others | - | - | - | - | NOISE |

**trade_net at 3d is the only signal where the 95% CI excludes zero** (barely). Everything else has CIs crossing zero. Sample size (~409 days) is insufficient for high confidence.

---

## 4. Cross-Asset Impact Table

### IC at 3-Day Horizon

| Signal | BTC | Gold | DXY | US10Y | VIX | SP500 | Oil |
|--------|-----|------|-----|-------|-----|-------|-----|
| trade_war | 0.030 | -0.055 | -0.003 | **0.105** | -0.073 | 0.065 | -0.027 |
| trade_net | **0.114** | 0.052 | -0.046 | -0.056 | -0.029 | 0.001 | -0.081 |
| trade_escalation | 0.057 | 0.016 | 0.037 | 0.017 | -0.060 | 0.022 | -0.031 |
| policy_uncertainty | 0.007 | -0.078 | 0.037 | 0.072 | -0.067 | 0.046 | 0.082 |
| fed_monetary | -0.015 | 0.024 | 0.068 | **0.140** | **-0.109** | **0.115** | 0.069 |
| geopolitical | 0.006 | -0.074 | 0.025 | 0.003 | -0.044 | 0.003 | **0.139** |
| econ_net | -0.057 | 0.009 | 0.085 | **0.129** | -0.044 | 0.040 | **0.169** |

### IC at 7-Day Horizon

| Signal | BTC | Gold | DXY | US10Y | VIX | SP500 | Oil |
|--------|-----|------|-----|-------|-----|-------|-----|
| trade_war | -0.006 | 0.030 | -0.040 | **0.163** | 0.018 | -0.029 | 0.031 |
| trade_net | **0.109** | 0.096 | -0.075 | 0.000 | 0.037 | -0.080 | -0.108 |
| trade_escalation | 0.043 | 0.089 | -0.056 | 0.047 | 0.071 | **-0.117** | -0.038 |
| policy_uncertainty | -0.023 | -0.069 | 0.014 | **0.131** | 0.005 | -0.010 | 0.106 |
| fed_monetary | **0.091** | 0.099 | 0.041 | 0.036 | -0.079 | 0.075 | **0.128** |
| geopolitical | -0.038 | **-0.147** | 0.011 | 0.088 | -0.009 | 0.013 | 0.103 |

**Cross-asset interpretation:**
- **trade_net -> BTC**: strongest single cross-asset IC (0.114 at 3d). Trade escalation rhetoric predicts BTC UP and gold UP, DXY DOWN -- consistent with "risk-on safe haven" narrative where tariff escalation weakens USD.
- **fed_monetary -> US10Y**: IC=0.140 at 3d. Fed commentary spikes predict higher yields. SP500 UP, VIX DOWN -- markets interpret Fed complaints as signaling eventual cuts.
- **geopolitical -> Oil**: IC=0.139 at 3d. Geopolitical posts predict oil UP. Expected.
- **trade_war -> US10Y**: IC=0.163 at 7d. Trade posts predict higher yields -- consistent with inflation fears.
- **econ_net -> Oil**: IC=0.169 at 3d. Economic optimism predicts oil UP.

The trade_net signal is BTC-specific; it does NOT show up in SP500 (IC=0.001) or gold (IC=0.052). This suggests it captures crypto-specific dynamics, not broad risk appetite.

---

## 5. Event Study Results

### Known Policy Events

| Date | Event | BTC -1d | BTC +1d | BTC +3d | BTC +7d |
|------|-------|---------|---------|---------|---------|
| 2025-01-20 | Inauguration | +0.9% | **+3.8%** | +1.6% | -0.2% |
| 2025-01-23 | Crypto EO #1 | +0.2% | +0.9% | -1.2% | +0.8% |
| 2025-02-01 | Tariffs on CA/MX/CN | -1.8% | **-2.9%** | **-2.9%** | **-4.2%** |
| 2025-02-03 | Tariff pause CA/MX | +3.7% | **-3.5%** | **-4.7%** | **-3.9%** |
| 2025-03-04 | Re-impose 25% tariffs | +1.2% | +3.8% | -0.6% | **-5.0%** |
| 2025-03-06 | **Strategic BTC Reserve** | -0.7% | **-3.5%** | **-10.2%** | **-9.8%** |
| 2025-03-07 | Digital Asset Summit | -3.5% | -0.7% | **-9.5%** | -3.3% |
| 2025-04-02 | **Liberation Day** | -3.1% | +0.8% | +1.2% | +0.1% |
| 2025-04-09 | **90-day tariff pause** | **+8.3%** | -3.6% | +3.2% | +1.7% |
| 2025-04-11 | Semiconductor exemption | +4.8% | +2.2% | +1.4% | +1.3% |
| 2025-05-12 | US-China tariff reduction | -1.3% | +1.3% | +1.0% | +2.7% |
| 2025-07-09 | 90-day pause expires | +2.1% | **+4.3%** | **+5.6%** | **+6.7%** |
| 2025-08-07 | Reciprocal tariffs resume | +2.2% | -0.7% | +1.6% | +0.7% |
| 2026-02-20 | SCOTUS rules tariffs unlawful | +1.5% | -0.1% | **-5.0%** | -3.2% |

**Key observations:**
- **Strategic BTC Reserve EO (Mar 6)**: "Buy the rumor, sell the news" -- BTC dumped 10% over 3 days despite bullish news. Classic.
- **Liberation Day (Apr 2)**: Muted BTC reaction (+0.1% at 7d) despite massive equity crash. BTC decoupled from equities here.
- **90-day tariff pause (Apr 9)**: +8.3% intraday reversal. Strongest single-day move.
- **Tariffs on CA/MX/CN (Feb 1)**: Clean -4.2% at 7d. Tariff imposition was bearish for BTC.
- **90-day pause expiry (Jul 9)**: +6.7% at 7d. Market had already priced in deals.

**Paradox**: Tariff imposition (Feb 1, Mar 4) was bearish, but the biggest event (Liberation Day) was neutral. The 90-day pause was the most bullish event. This explains why trade_net (escalation - de-escalation) shows POSITIVE IC -- de-escalation posts correlate with MORE negative BTC returns, possibly because de-escalation rhetoric follows market crashes.

### Data-Driven Event Study: Top 20 Trade Days

**Average BTC returns on high trade-post days:**
| Window | Mean | Median |
|--------|------|--------|
| +1d | -0.82% | -0.58% |
| +3d | -0.48% | -0.05% |
| +7d | -0.74% | -0.07% |

Top trade days show mildly negative returns, but not statistically significant (t-test p=0.14 at 1d).

### Data-Driven Event Study: Top 20 Geopolitical Days

**Average BTC returns on high geo-post days:**
| Window | Mean | Median |
|--------|------|--------|
| +1d | +0.63% | +0.20% |
| +3d | +1.33% | +0.91% |
| +7d | +0.82% | +0.49% |

Geopolitical spikes show mildly positive BTC returns -- BTC as "digital gold" narrative partially holds.

### Significance Tests

| Comparison | Horizon | Event Mean | Other Mean | t-stat | p-value |
|-----------|---------|-----------|-----------|--------|---------|
| Top 20 Trade Days vs Others | 1d | -0.82% | -0.01% | -1.49 | 0.138 |
| Top 20 Trade Days vs Others | 3d | -0.48% | -0.18% | -0.35 | 0.729 |
| Top 20 Trade Days vs Others | 7d | -0.74% | -0.51% | -0.18 | 0.858 |
| Top 20 Uncertainty vs Others | 1d | +0.46% | -0.08% | 0.96 | 0.336 |
| Top 20 Uncertainty vs Others | 7d | -0.33% | -0.53% | 0.16 | 0.872 |

**None of the event studies achieve statistical significance.** All p-values > 0.10.

---

## 6. Conditional Analysis

### Escalation vs De-escalation Days

| Condition | 1d Mean | 3d Mean | 7d Mean | N |
|-----------|---------|---------|---------|---|
| Escalation (trade_net > 0) | +0.10% | **+0.62%** | +0.25% | 109 |
| De-escalation (trade_net < 0) | -0.50% | **-0.82%** | **-1.10%** | 38 |
| Neutral (trade_net = 0) | -0.05% | -0.45% | -0.76% | 261 |

**This is the key finding.** De-escalation days (when Trump posts about trade deals, pauses, agreements) predict NEGATIVE BTC returns. Escalation days predict positive or flat returns. The spread is 1.44% at 3d and 1.35% at 7d.

**Interpretation:** De-escalation posts often come AFTER major market disruptions (the pause comes after the crash). They're reactive, not predictive. The "relief" has already been priced in by the time Trump posts about deals.

### Quintile Analysis: trade_net -> 7d BTC Return

| Quintile | trade_net Mean | Mean 7d Return | Median 7d Return | N |
|----------|---------------|---------------|-----------------|---|
| Q1 (most de-escalation) | -0.60 | **-2.17%** | -2.03% | 81 |
| Q2 | 0.00 | +2.40% | +1.78% | 80 |
| Q3 | 0.00 | -1.51% | -1.45% | 80 |
| Q4 | 0.33 | -1.56% | -0.45% | 80 |
| Q5 (most escalation) | 1.33 | +0.24% | +0.92% | 81 |
| **Q5-Q1 spread** | | **+2.41%** | | |

Monotonic Q1->Q5 is not perfect, but the extreme quintiles have the right directional spread.

---

## 7. Lead/Lag Analysis

| Signal | vs Past 1d | vs Past 3d | vs Fwd 1d | vs Fwd 3d |
|--------|-----------|-----------|----------|----------|
| trade_net | -0.015 | -0.062 | **+0.080** | **+0.114** |
| policy_uncertainty | 0.001 | 0.017 | -0.020 | 0.007 |
| crypto | -0.039 | -0.037 | 0.008 | -0.008 |
| total_posts | 0.000 | -0.052 | 0.000 | 0.035 |

**trade_net is forward-looking, not reactive.** Correlation with past returns is near-zero (-0.015 at 1d, -0.062 at 3d), but correlation with future returns is meaningfully positive (0.080 at 1d, 0.114 at 3d). The signal contains predictive information.

---

## 8. Sub-Period Stability

| Period | trade_net 3d | trade_net 7d | fed_net 3d | econ_bearish 3d |
|--------|-------------|-------------|-----------|----------------|
| Pre-Liberation (Jan-Mar 25) | **0.169** | **0.248** | -0.046 | 0.107 |
| Liberation (Apr-Jun 25) | **0.123** | 0.018 | 0.099 | 0.113 |
| Post-Liberation (Jul-Sep 25) | **0.216** | **0.313** | -0.013 | 0.039 |
| Late 2025 (Oct-Dec 25) | 0.020 | -0.063 | 0.080 | 0.143 |
| 2026 (Jan-Mar 26) | **0.150** | **0.187** | 0.040 | -0.186 |

**trade_net is positive in 4/5 sub-periods at 3d.** It failed in Late 2025 only. This is reasonable stability for an alternative data signal, especially given 60-90 day sub-periods.

Rolling IC (60-day window): trade_net 3d has mean IC=0.128, std=0.082, positive 94.5% of windows. This is the most stable signal in the study.

---

## 9. Composite Signal

| Signal | 1d IC | 3d IC | 7d IC | 14d IC |
|--------|-------|-------|-------|--------|
| composite_v1 (trade_net - econ_bearish) | 0.024 | **0.082** | **0.116** (p=0.020) | **0.105** (p=0.037) |
| composite_v2 (z-scored) | 0.061 | **0.092** | **0.095** (p=0.058) | 0.054 |

The simple composite (composite_v1) achieves IC=0.116 at 7d with p=0.020, the only signal that crosses the p<0.05 threshold. It combines trade escalation sentiment with bearish economic language for a net "policy disruption" score.

---

## 10. Verdict Summary

| Category | Signal | Best IC | Horizon | Bootstrap CI | Stability | Verdict |
|----------|--------|---------|---------|-------------|-----------|---------|
| A. Trade War | trade_net | **0.114** | 3d | [0.017, 0.208] | 4/5 periods | **CONDITIONAL PASS** |
| A. Trade War | trade_deescalation | -0.079 | 7d | crosses zero | unstable | KILL |
| B. Fed/Monetary | fed_net | 0.087 | 1d | crosses zero | unstable | KILL |
| B. Fed/Monetary | fed_monetary | 0.091 | 7d | crosses zero | unstable | KILL |
| C. Geopolitical | geopolitical | -0.038 | 7d | crosses zero | unstable | KILL |
| C. Geopolitical | geo_net | 0.078 | 1d | crosses zero | unstable | KILL |
| D. Economic | econ_bearish | -0.090 | 14d | [-0.189, 0.009] | 3/5 periods | KILL |
| D. Economic | econ_net | -0.057 | 3d | crosses zero | unstable | KILL |
| E. Crypto | crypto | 0.012-0.067 | varies | crosses zero | unstable (N=64) | KILL |
| E. Crypto | crypto_net | varies | varies | crosses zero | too sparse | KILL |
| F. Policy Unc. | policy_uncertainty | -0.023 | 7d | crosses zero | unstable | KILL |
| Composite | trade_net - bearish | **0.116** | 7d | p=0.020 | TBD | **CONDITIONAL PASS** |

### PASS Categories

**trade_net (trade escalation minus de-escalation count):**
- IC = 0.114 at 3d, 0.109 at 7d (raw), 0.117 at 7d (z-scored)
- Bootstrap 95% CI barely excludes zero at 3d: [0.017, 0.208]
- Rolling IC positive 94.5% of 60-day windows
- Forward-looking (not reactive to past returns)
- Stable across 4/5 sub-periods
- Q5-Q1 spread: 2.41% at 7d
- BUT: counterintuitive direction (escalation -> BTC up) limits conviction

**Composite (trade_net - econ_bearish):**
- IC = 0.116 at 7d with p=0.020
- Combines the two partially informative signals

### KILL Categories

- **Fed/Monetary**: Too few posts (193 total, 0.45/day), ICs inconsistent across horizons
- **Geopolitical**: High volume (1,533 posts) but zero predictive power for BTC. Predicts oil (+0.14 IC) but not crypto.
- **Economic Confidence**: Dominated by bullish cheerleading (957 bullish vs 125 bearish). Bullish posts have no signal. Bearish posts are too sparse for reliable analysis.
- **Crypto-Specific**: Only 64 posts total (0.15/day). Statistically meaningless sample. The z-scored IC of -0.24 at 14d is driven by 2-3 events.
- **Policy Uncertainty composite**: Noise. Dominated by geopolitical posts which have no BTC signal.

---

## 11. Recommended Signal Construction (for PASS categories)

### Signal: Trump Trade Sentiment (TTS)

```
For each day:
1. Count trade escalation posts (trade_escalation)
2. Count trade de-escalation posts (trade_deescalation)
3. trade_net = escalation_count - deescalation_count
4. Signal = 14-day rolling z-score of trade_net
5. BTC position sizing: proportional to signal (higher z-score -> larger long)
```

**Expected characteristics:**
- IC ~ 0.09-0.12 at 3-7 day horizon
- Turnover: low (signal changes slowly)
- Alpha: ~2-3% annualized (very rough estimate from Q5-Q1 spread)
- Risk: sample period is only 14 months; signal could be regime-specific

### Caveats

1. **Short sample**: 14 months of presidency data. One more tariff cycle (e.g., post-SCOTUS ruling in Feb 2026) could change results.
2. **Counterintuitive direction**: Escalation -> BTC up is hard to justify fundamentally. Possible explanations:
   - De-escalation posts follow crashes (reactive)
   - Escalation creates uncertainty that drives capital to BTC as hedge
   - Escalation rhetoric signals Trump confidence, which is risk-on
3. **Low IC magnitude**: IC of 0.11 is marginal for a standalone signal. Best used as overlay/tilt on an existing strategy.
4. **Data snooping**: We tested 20+ signals at 4 horizons = 80+ comparisons. Expected ~4 significant by chance at p<0.05. Only 1-2 survive, which is barely above noise.

### Recommendation

**Do not deploy as a standalone signal.** Use as a low-weight overlay (5-10% portfolio weight) on existing momentum or carry strategies, specifically:
- When trade_net z-score > 1.5: increase BTC long exposure by 10-20%
- When trade_net z-score < -1.5: reduce BTC exposure by 10-20%
- Re-evaluate after 6 more months of data accumulate

---

## Key Event Timeline Reference

- Jan 20, 2025: Inauguration
- Jan 23, 2025: Crypto Working Group EO
- Feb 1, 2025: Tariffs on CA/MX/CN
- Mar 6, 2025: Strategic Bitcoin Reserve EO
- Apr 2, 2025: Liberation Day tariffs
- Apr 9, 2025: 90-day tariff pause
- May 12, 2025: US-China tariff reduction
- Jul 9, 2025: Pause expiration
- Jul 2025: GENIUS Act signed
- Aug 7, 2025: Reciprocal tariffs resume
- Feb 20, 2026: SCOTUS rules IEEPA tariffs unlawful

Sources for event dates:
- [Liberation Day tariffs - Wikipedia](https://en.wikipedia.org/wiki/Liberation_Day_tariffs)
- [Tariffs in the second Trump administration - Wikipedia](https://en.wikipedia.org/wiki/Tariffs_in_the_second_Trump_administration)
- [U.S. Strategic Bitcoin Reserve - Wikipedia](https://en.wikipedia.org/wiki/U.S._Strategic_Bitcoin_Reserve)
- [Presidential 2025 Tariff Actions Timeline | Congress.gov](https://www.congress.gov/crs-product/R48549)
- [Trump's trade war timeline 2.0 | PIIE](https://www.piie.com/blogs/realtime-economics/2025/trumps-trade-war-timeline-20-date-guide)
- [White House Fact Sheet: Strategic Bitcoin Reserve](https://www.whitehouse.gov/fact-sheets/2025/03/fact-sheet-president-donald-j-trump-establishes-the-strategic-bitcoin-reserve-and-u-s-digital-asset-stockpile/)
