# Funding Rate & IV Dynamics Research Results

**Date:** 2026-03-24
**Scope:** Funding rate dynamics (Task A) + DVOL / implied volatility signals (Task B)
**Method:** Spearman IC, IS/OOS split at midpoint, kill threshold |IC| < 0.02 or |t| < 2.0
**Data:** Binance funding rates (2019-2026, 1200 8h records), Deribit DVOL (2021-2026, 1827 daily), BTC spot 1h (2020-2026)

---

## 1. Funding Rate Current State Report

### BTC Funding
- **Latest daily mean rate:** 0.000040 (2026-03-24)
- **30d rolling mean:** -0.000009 (annualized: -0.97%)
- **Current 30d mean percentile:** 8.4% (historically very low)
- **Last time 30d mean > 0.01%:** 2020-08-28 (over 5.5 years ago)

### ETH Funding
- **Latest daily mean rate:** 0.000025 (2026-03-24)
- **30d rolling mean:** -0.000012 (annualized: -1.27%)
- **Last time 30d mean > 0.01%:** 2020-11-14

### Historical Distribution (BTC Daily Mean Funding Rate)

| Percentile | Rate | Annualized |
|------------|-----:|------------|
| P5 | -0.000115 | -12.56% |
| P25 | 0.000012 | 1.30% |
| P50 (median) | 0.000100 | 10.95% |
| P75 | 0.000100 | 10.95% |
| P95 | 0.000542 | 59.40% |

### Recovery Assessment

The current 30d mean is at the **8th percentile** of all historical observations -- deeply depressed. The historical median funding rate annualizes to ~11%, but the current regime is essentially zero to slightly negative. The last sustained period of positive carry (30d mean > 0.01%) ended in mid-2020. Recent trajectory shows a **slightly negative bias** with no recovery signal.

**Verdict: Carry is NOT recovering.** The funding rate regime has structurally shifted to near-zero. Carry strategies (s29, s65) should remain dormant.

### Cross-Token Live Funding Snapshot (last 5 days, March 4-9 2026)

| Token | Avg Rate | Annualized |
|-------|--------:|-----------:|
| UNI | 0.000032 | 3.48% |
| BTC | -0.000008 | -0.85% |
| LINK | -0.000010 | -1.08% |
| AVAX | -0.000027 | -2.95% |
| ETH | -0.000030 | -3.33% |
| DOGE | -0.000057 | -6.22% |
| ADA | -0.000058 | -6.30% |
| SOL | -0.000069 | -7.58% |
| XRP | -0.000075 | -8.25% |
| DOT | -0.000189 | -20.67% |

- Cross-token mean: -0.000049 (ann: -5.37%)
- Cross-token dispersion (std): 0.000056
- **Interpretation:** Most tokens are in net negative funding -- shorts are paying longs. This is unusual and suggests bearish positioning dominance across the board. DOT is an extreme outlier at -20.67% annualized.

---

## 2. IC Tables -- All Signals Tested

### Task A: Funding Rate Signals

#### A1: Raw Funding Rate Level

| Horizon | IC | IC_IS | IC_OOS | t-stat | n | Verdict |
|---------|---:|------:|-------:|-------:|--:|---------|
| 1d | 0.0215 | -0.1241 | 0.1872 | 0.36 | 287 | KILL |
| 3d | -0.0243 | -0.2578 | 0.2102 | -0.41 | 285 | KILL |
| 5d | -0.0446 | -0.3228 | 0.2564 | -0.75 | 283 | KILL |
| 7d | -0.0767 | -0.3909 | 0.2498 | -1.28 | 281 | KILL |
| 14d | -0.0785 | -0.5596 | 0.4103 | -1.30 | 274 | KILL |

Note: IS and OOS have opposite signs at every horizon -- completely unstable. The raw funding rate level has no predictive power for price direction.

#### A2: 30d Mean Funding Rate

| Horizon | IC | IC_IS | IC_OOS | t-stat | n | Verdict |
|---------|---:|------:|-------:|-------:|--:|---------|
| 1d | -0.0445 | -0.1112 | 0.0480 | -0.73 | 273 | KILL |
| 3d | -0.1619 | -0.2530 | -0.0413 | -2.67 | 271 | PASS |
| 5d | -0.2177 | -0.3202 | -0.0677 | -3.57 | 269 | PASS |
| 7d | -0.2431 | -0.3815 | -0.0453 | -3.97 | 267 | PASS |
| 14d | -0.3655 | -0.5838 | -0.0807 | -5.89 | 260 | PASS |

**CAUTION:** Passes the threshold but with very large IS/OOS gap. IS IC is 3-7x the OOS IC. The negative sign means high funding predicts negative returns (overcrowded longs get punished). However, with n=260-273, sample is thin for a daily signal from sparse 5-day sampled data. Treat with skepticism.

#### A3: Funding Rate Acceleration (7d delta of 7d mean)

| Horizon | IC | IC_IS | IC_OOS | t-stat | n | Verdict |
|---------|---:|------:|-------:|-------:|--:|---------|
| 1d | 0.0348 | -0.0223 | 0.0861 | 0.58 | 277 | KILL |
| 3d | 0.0791 | -0.0046 | 0.1060 | 1.31 | 275 | KILL |
| 5d | 0.0767 | -0.0294 | 0.1759 | 1.27 | 273 | KILL |
| 7d | 0.1047 | 0.0022 | 0.2149 | 1.72 | 271 | KILL |
| 14d | 0.2089 | -0.0053 | 0.4518 | 3.39 | 264 | PASS |

IS is near-zero across all horizons -- the signal only appears in OOS. This is a red flag for overfitting.

#### A3b: Funding Rate Acceleration (14d delta of 14d mean)

| Horizon | IC | IC_IS | IC_OOS | t-stat | n | Verdict |
|---------|---:|------:|-------:|-------:|--:|---------|
| 1d | 0.1023 | 0.0322 | 0.1595 | 1.67 | 267 | KILL |
| 3d | 0.1437 | -0.0126 | 0.3222 | 2.34 | 265 | PASS |
| 5d | 0.1746 | -0.0681 | 0.4536 | 2.83 | 263 | PASS |
| 7d | 0.1855 | -0.0990 | 0.5130 | 3.00 | 261 | PASS |
| 14d | 0.2229 | -0.1473 | 0.5421 | 3.55 | 254 | PASS |

Same issue: IS is negative, OOS is extremely positive. Sign inconsistency = unreliable.

#### A4: Funding Rate Z-Score (60d window)

| Horizon | IC | IC_IS | IC_OOS | t-stat | n | Verdict |
|---------|---:|------:|-------:|-------:|--:|---------|
| 1d | 0.1027 | 0.0211 | 0.2162 | 1.65 | 258 | KILL |
| 3d | 0.1562 | 0.0035 | 0.3410 | 2.50 | 256 | PASS |
| 5d | 0.2023 | -0.0064 | 0.4255 | 3.22 | 254 | PASS |
| 7d | 0.1817 | -0.0542 | 0.4499 | 2.88 | 252 | PASS |
| 14d | 0.2335 | -0.1620 | 0.6191 | 3.65 | 245 | PASS |

OOS IC of 0.62 at 14d is suspiciously high. IS near-zero or negative. Not trustworthy.

#### A4b: Funding Rate Z-Score Contrarian (negative sign)

Mirror of A4 with flipped signs -- same instability issues.

#### A5: Cross-Token Funding Dispersion (19 tokens, extended parquet)

| Horizon | IC | IC_IS | IC_OOS | t-stat | n | Verdict |
|---------|---:|------:|-------:|-------:|--:|---------|
| 1d | -0.0200 | 0.0374 | 0.0481 | -0.31 | 242 | KILL |
| 3d | -0.0453 | 0.0234 | 0.0926 | -0.70 | 240 | KILL |
| 5d | -0.0434 | 0.0684 | 0.1116 | -0.67 | 238 | KILL |
| 7d | -0.0325 | 0.1390 | 0.0815 | -0.50 | 236 | KILL |
| 14d | -0.0311 | 0.1919 | 0.1343 | -0.47 | 229 | KILL |

Full sample IC near zero, t-stats well below 2. Cross-token funding dispersion does not predict BTC returns.

---

### Task B: DVOL / Implied Volatility Signals

#### B1: DVOL Rate-of-Change (5d)

| Horizon | IC | IC_IS | IC_OOS | t-stat | n | Verdict |
|---------|---:|------:|-------:|-------:|--:|---------|
| 1d | 0.0558 | 0.0319 | 0.0741 | 2.38 | 1821 | PASS |
| 3d | 0.0798 | 0.0584 | 0.0934 | 3.40 | 1819 | PASS |
| 5d | 0.0767 | 0.0639 | 0.0780 | 3.27 | 1817 | PASS |
| 7d | 0.0730 | 0.0554 | 0.0764 | 3.11 | 1815 | PASS |
| 14d | 0.0692 | 0.0406 | 0.0754 | 2.94 | 1808 | PASS |

#### B1: DVOL Rate-of-Change (10d)

| Horizon | IC | IC_IS | IC_OOS | t-stat | n | Verdict |
|---------|---:|------:|-------:|-------:|--:|---------|
| 1d | 0.0643 | 0.0544 | 0.0655 | 2.74 | 1816 | PASS |
| 3d | 0.0824 | 0.0549 | 0.0989 | 3.51 | 1814 | PASS |
| 5d | 0.0847 | 0.0732 | 0.0793 | 3.60 | 1812 | PASS |
| 7d | 0.0819 | 0.0648 | 0.0775 | 3.48 | 1810 | PASS |
| 14d | 0.0909 | 0.0482 | 0.1084 | 3.86 | 1803 | PASS |

#### B1: DVOL Rate-of-Change (20d)

| Horizon | IC | IC_IS | IC_OOS | t-stat | n | Verdict |
|---------|---:|------:|-------:|-------:|--:|---------|
| 1d | 0.0564 | 0.0363 | 0.0738 | 2.40 | 1806 | PASS |
| 3d | 0.0846 | 0.0461 | 0.1203 | 3.59 | 1804 | PASS |
| 5d | 0.0899 | 0.0636 | 0.1125 | 3.81 | 1802 | PASS |
| 7d | 0.1011 | 0.0948 | 0.1018 | 4.29 | 1800 | PASS |
| 14d | 0.1030 | 0.1409 | 0.0468 | 4.36 | 1793 | PASS |

**HEADLINE RESULT: DVOL ROC is a robust directional signal.** All three lookback variants (5d, 10d, 20d) pass at every horizon with consistent IS/OOS signs. The positive IC means rising DVOL predicts rising BTC prices. This is COUNTER-INTUITIVE -- rising implied volatility is typically associated with fear. Interpretation: IV rises alongside price during rallies (volatility-of-upside effect in crypto).

Best variant: **20d ROC at 7d horizon, IC=0.1011, IS=0.0948, OOS=0.1018** -- near-perfect IS/OOS match.

#### B2: DVOL Z-Score (60d window)

| Horizon | IC | IC_IS | IC_OOS | t-stat | n | Verdict |
|---------|---:|------:|-------:|-------:|--:|---------|
| 1d | 0.0582 | 0.0568 | 0.0586 | 2.47 | 1797 | PASS |
| 3d | 0.0813 | 0.0781 | 0.0817 | 3.45 | 1795 | PASS |
| 5d | 0.0921 | 0.1077 | 0.0723 | 3.90 | 1793 | PASS |
| 7d | 0.0969 | 0.1206 | 0.0672 | 4.10 | 1791 | PASS |
| 14d | 0.1012 | 0.1365 | 0.0457 | 4.27 | 1784 | PASS |

#### B2: DVOL Z-Score (90d window)

| Horizon | IC | IC_IS | IC_OOS | t-stat | n | Verdict |
|---------|---:|------:|-------:|-------:|--:|---------|
| 1d | 0.0488 | 0.0507 | 0.0435 | 2.06 | 1782 | PASS |
| 3d | 0.0687 | 0.0623 | 0.0631 | 2.90 | 1780 | PASS |
| 5d | 0.0808 | 0.0995 | 0.0508 | 3.41 | 1778 | PASS |
| 7d | 0.0882 | 0.1188 | 0.0436 | 3.72 | 1776 | PASS |
| 14d | 0.1132 | 0.1666 | 0.0266 | 4.76 | 1769 | PASS |

#### B2: DVOL Z-Score (120d window)

| Horizon | IC | IC_IS | IC_OOS | t-stat | n | Verdict |
|---------|---:|------:|-------:|-------:|--:|---------|
| 1d | 0.0511 | 0.0628 | 0.0402 | 2.15 | 1767 | PASS |
| 3d | 0.0770 | 0.0879 | 0.0595 | 3.23 | 1765 | PASS |
| 5d | 0.0874 | 0.1230 | 0.0453 | 3.67 | 1763 | PASS |
| 7d | 0.0995 | 0.1440 | 0.0438 | 4.18 | 1761 | PASS |
| 14d | 0.1326 | 0.1907 | 0.0453 | 5.55 | 1754 | PASS |

DVOL z-score is positive at all windows/horizons. Same direction as ROC: higher IV relative to recent history = higher future returns. Best IS/OOS consistency at 60d window.

#### B3: VRP(7d) Acceleration (5d change)

| Horizon | IC | IC_IS | IC_OOS | t-stat | n | Verdict |
|---------|---:|------:|-------:|-------:|--:|---------|
| 1d | -0.0077 | -0.0181 | 0.0026 | -0.33 | 1804 | KILL |
| 3d | 0.0030 | 0.0105 | -0.0088 | 0.13 | 1802 | KILL |
| 5d | 0.0190 | 0.0268 | 0.0082 | 0.81 | 1800 | KILL |
| 7d | 0.0492 | 0.0490 | 0.0494 | 2.08 | 1798 | PASS |
| 14d | 0.0613 | 0.0834 | 0.0420 | 2.60 | 1791 | PASS |

#### B3: VRP(7d) Acceleration (10d change)

| Horizon | IC | IC_IS | IC_OOS | t-stat | n | Verdict |
|---------|---:|------:|-------:|-------:|--:|---------|
| 1d | 0.0471 | 0.0323 | 0.0633 | 2.00 | 1799 | KILL |
| 3d | 0.0618 | 0.0461 | 0.0821 | 2.62 | 1797 | PASS |
| 5d | 0.0886 | 0.0886 | 0.0883 | 3.75 | 1795 | PASS |
| 7d | 0.1130 | 0.1221 | 0.1030 | 4.79 | 1793 | PASS |
| 14d | 0.0786 | 0.1452 | 0.0033 | 3.32 | 1786 | PASS |

**STANDOUT: VRP(7d) 10d acceleration at 5d and 7d horizons.** IC=0.0886 with IS=0.0886/OOS=0.0883 at 5d is remarkably stable. IC=0.1130 at 7d is the highest in this family.

#### B3: VRP(14d) Acceleration (5d change)

| Horizon | IC | IC_IS | IC_OOS | t-stat | n | Verdict |
|---------|---:|------:|-------:|-------:|--:|---------|
| 1d | 0.0720 | 0.0481 | 0.1020 | 3.06 | 1804 | PASS |
| 3d | 0.1012 | 0.0998 | 0.1055 | 4.29 | 1802 | PASS |
| 5d | 0.0879 | 0.1239 | 0.0434 | 3.73 | 1800 | PASS |
| 7d | 0.0854 | 0.1351 | 0.0218 | 3.62 | 1798 | PASS |
| 14d | 0.0671 | 0.1110 | 0.0100 | 2.84 | 1791 | PASS |

**BEST SINGLE CELL: VRP(14d) 5d accel at 3d horizon, IC=0.1012, IS=0.0998, OOS=0.1055.** Near-perfect IS/OOS stability.

#### B3: VRP(14d) Acceleration (10d change)

| Horizon | IC | IC_IS | IC_OOS | t-stat | n | Verdict |
|---------|---:|------:|-------:|-------:|--:|---------|
| 1d | 0.0337 | 0.0413 | 0.0253 | 1.43 | 1799 | KILL |
| 3d | 0.0650 | 0.0942 | 0.0304 | 2.76 | 1797 | PASS |
| 5d | 0.0733 | 0.1368 | -0.0029 | 3.11 | 1795 | PASS |
| 7d | 0.0604 | 0.1284 | -0.0194 | 2.56 | 1793 | PASS |
| 14d | 0.0338 | 0.0615 | 0.0038 | 1.43 | 1786 | KILL |

OOS degrades at longer horizons -- signal too slow for its own lookback.

#### B3: VRP(30d) Acceleration (5d change)

| Horizon | IC | IC_IS | IC_OOS | t-stat | n | Verdict |
|---------|---:|------:|-------:|-------:|--:|---------|
| 1d | 0.0694 | 0.0510 | 0.0904 | 2.95 | 1804 | PASS |
| 3d | 0.0836 | 0.0685 | 0.1019 | 3.55 | 1802 | PASS |
| 5d | 0.0691 | 0.0657 | 0.0725 | 2.93 | 1800 | PASS |
| 7d | 0.0655 | 0.0546 | 0.0760 | 2.78 | 1798 | PASS |
| 14d | 0.0579 | 0.0591 | 0.0552 | 2.45 | 1791 | PASS |

Very consistent IS/OOS across all horizons. Lower peak IC but excellent stability.

#### B3: VRP(30d) Acceleration (10d change)

| Horizon | IC | IC_IS | IC_OOS | t-stat | n | Verdict |
|---------|---:|------:|-------:|-------:|--:|---------|
| 1d | 0.0532 | 0.0451 | 0.0609 | 2.25 | 1799 | PASS |
| 3d | 0.0683 | 0.0465 | 0.0961 | 2.90 | 1797 | PASS |
| 5d | 0.0702 | 0.0576 | 0.0831 | 2.97 | 1795 | PASS |
| 7d | 0.0611 | 0.0565 | 0.0608 | 2.59 | 1793 | PASS |
| 14d | 0.0373 | 0.0347 | 0.0381 | 1.58 | 1786 | KILL |

#### B4: DVOL Mean-Reversion (DVOL level -> forward DVOL change)

| Horizon | IC | IC_IS | IC_OOS | t-stat | n | Verdict |
|---------|---:|------:|-------:|-------:|--:|---------|
| 5d | -0.1910 | -0.1723 | -0.2124 | -8.15 | 1821 | PASS |
| 10d | -0.2715 | -0.2469 | -0.2963 | -11.57 | 1816 | PASS |
| 20d | -0.3021 | -0.2718 | -0.3950 | -12.84 | 1806 | PASS |
| 30d | -0.3345 | -0.2767 | -0.4656 | -14.18 | 1796 | PASS |

**EXTREMELY STRONG MEAN-REVERSION IN DVOL.** IC=-0.33 at 30d with perfect IS/OOS consistency. High DVOL strongly predicts DVOL decline. This is the strongest single IC in this entire study. Exploitable for volatility-specific strategies (options, VRP timing).

#### B5: DVOL Level -> Forward BTC Return

| Horizon | IC | IC_IS | IC_OOS | t-stat | n | Verdict |
|---------|---:|------:|-------:|-------:|--:|---------|
| 1d | 0.0032 | 0.0180 | 0.0372 | 0.14 | 1825 | KILL |
| 3d | 0.0008 | 0.0184 | 0.0699 | 0.03 | 1823 | KILL |
| 5d | 0.0031 | 0.0333 | 0.0735 | 0.13 | 1821 | KILL |
| 7d | -0.0200 | 0.0167 | 0.0659 | -0.85 | 1819 | KILL |
| 14d | -0.0617 | 0.0126 | 0.0470 | -2.63 | 1812 | PASS |

DVOL level has no meaningful predictive power for price direction at short horizons. The 14d "PASS" has inconsistent IS/OOS signs.

#### B5b: Negative DVOL -> Forward BTC Return (contrarian)

Mirror of B5 -- same lack of signal.

#### B6: ETH DVOL ROC (10d) -> BTC Return

| Horizon | IC | IC_IS | IC_OOS | t-stat | n | Verdict |
|---------|---:|------:|-------:|-------:|--:|---------|
| 1d | 0.0473 | 0.0591 | 0.0208 | 2.02 | 1815 | PASS |
| 3d | 0.0784 | 0.0705 | 0.0655 | 3.34 | 1813 | PASS |
| 5d | 0.0864 | 0.0934 | 0.0575 | 3.68 | 1811 | PASS |
| 7d | 0.0931 | 0.0882 | 0.0729 | 3.96 | 1809 | PASS |
| 14d | 0.0884 | 0.0477 | 0.0918 | 3.75 | 1802 | PASS |

ETH DVOL ROC also works for predicting BTC returns -- confirming the cross-asset nature of the signal.

#### B7: BTC-ETH DVOL Spread -> BTC Return

| Horizon | IC | IC_IS | IC_OOS | t-stat | n | Verdict |
|---------|---:|------:|-------:|-------:|--:|---------|
| 1d | 0.0402 | 0.0009 | 0.0832 | 1.72 | 1825 | KILL |
| 3d | 0.0602 | 0.0043 | 0.1288 | 2.57 | 1823 | PASS |
| 5d | 0.0764 | 0.0004 | 0.1649 | 3.26 | 1821 | PASS |
| 7d | 0.0995 | 0.0281 | 0.1837 | 4.24 | 1819 | PASS |
| 14d | 0.1317 | 0.0452 | 0.2403 | 5.61 | 1812 | PASS |

When BTC DVOL is high relative to ETH DVOL, BTC outperforms. IS IC is weak but consistently positive. The OOS is much stronger, suggesting this is an emerging signal. IC=0.1317 at 14d is notable but the IS/OOS asymmetry warrants caution.

---

## 3. Signal Verdicts Summary

### Task A: Funding Rate Signals

| Signal | Best Horizon | IC | IS/OOS Match | Verdict | Notes |
|--------|-------------|---:|:------------:|---------|-------|
| A1: Raw FR Level | - | ~0.02 | No | **KILL** | IS/OOS sign flip at every horizon |
| A2: 30d Mean FR | 14d | -0.3655 | Partial | **CAUTION** | Large IS/OOS gap, thin sample (n=260) |
| A3: FR Accel 7d | 14d | 0.2089 | No | **KILL** | IS near-zero, OOS inflated |
| A3b: FR Accel 14d | 14d | 0.2229 | No | **KILL** | IS negative, OOS positive |
| A4: FR Z-Score | 14d | 0.2335 | No | **KILL** | Extreme IS/OOS divergence |
| A4b: FR Z-Score Contrarian | - | - | No | **KILL** | Mirror of A4 |
| A5: Cross-Token Dispersion | - | ~-0.03 | No | **KILL** | Near-zero IC, no signal |

**Summary for Task A:** All funding rate signals are either killed outright or have severe IS/OOS instability that renders them unreliable. The 30d mean (A2) shows a plausible negative relationship at longer horizons but the IS/OOS gap is too large and the sample is too thin (n=260 effective daily observations from sparse 8h data). **No actionable funding rate signal found.**

### Task B: DVOL Signals

| Signal | Best Horizon | IC | IS/OOS Match | Verdict | Notes |
|--------|-------------|---:|:------------:|---------|-------|
| B1: DVOL ROC 5d | 3d | 0.0798 | Yes | **PASS** | Consistent IS/OOS |
| B1: DVOL ROC 10d | 14d | 0.0909 | Yes | **PASS** | Broad horizon stability |
| B1: DVOL ROC 20d | 7d | 0.1011 | Yes | **STRONG PASS** | IS=0.095, OOS=0.102 |
| B2: DVOL Z-Score 60d | 14d | 0.1012 | Yes | **PASS** | Best IS/OOS at 1-3d |
| B2: DVOL Z-Score 90d | 14d | 0.1132 | Partial | **PASS** | OOS weaker at longer h |
| B2: DVOL Z-Score 120d | 14d | 0.1326 | Partial | **PASS** | Highest IC but IS>OOS gap |
| B3: VRP(7d) Accel 10d | 7d | 0.1130 | Yes | **STRONG PASS** | IS=0.122, OOS=0.103 |
| B3: VRP(14d) Accel 5d | 3d | 0.1012 | Yes | **STRONG PASS** | IS=0.100, OOS=0.106 |
| B3: VRP(30d) Accel 5d | 3d | 0.0836 | Yes | **PASS** | Most stable IS/OOS of all VRP variants |
| B3: VRP(30d) Accel 10d | 5d | 0.0702 | Yes | **PASS** | Stable but lower IC |
| B4: DVOL Mean-Reversion | 30d | -0.3345 | Yes | **STRONG PASS** | Vol trading signal, not directional |
| B5: DVOL Level -> Price | - | ~0.003 | No | **KILL** | No directional power |
| B6: ETH DVOL ROC 10d | 7d | 0.0931 | Yes | **PASS** | Cross-asset confirmation |
| B7: BTC-ETH DVOL Spread | 14d | 0.1317 | Partial | **CONDITIONAL** | IS weak, OOS strong |

---

## 4. Key Findings & Recommendations

### Finding F-88: Funding Rate Regime Shift Confirmed
- BTC funding has been structurally suppressed, sitting at 8th percentile of all history
- 30d mean is slightly negative (-0.97% annualized) -- shorts are paying longs
- Cross-token snapshot shows 9/10 top tokens in negative funding territory
- **Implication:** Carry strategies (s29, s65) remain non-viable indefinitely
- **Monitoring threshold:** Re-evaluate if 30d mean sustains above 0.005% for 2 consecutive weeks

### Finding F-89: Funding Rate Signals are Unreliable for Price Direction
- Every funding rate signal tested (raw level, smoothed, acceleration, z-score, dispersion) either kills outright or has fatal IS/OOS inconsistency
- The fundamental problem: Binance full data has only ~1200 8h records over 6+ years, yielding ~288 effective daily observations
- Cross-token dispersion (19 tokens, 243 days) also shows no signal
- **Verdict: KILL all funding-based directional signals.** Funding rate data is useful for carry/basis strategies but not for price prediction.

### Finding F-90: DVOL Rate-of-Change is a New Directional Signal (HEADLINE)
- **DVOL ROC 20d at 7d horizon: IC=0.1011, IS=0.0948, OOS=0.1018** -- the most stable signal in this study
- The positive IC means rising implied volatility predicts rising BTC prices
- This is COUNTER-INTUITIVE: in equities, rising IV typically signals fear/decline. In crypto, IV rises with price rallies (reflexive volatility)
- All three lookback variants (5d, 10d, 20d) pass at all horizons -- extremely robust
- **Action: Promote to strategy candidate for backtesting as standalone directional signal**

### Finding F-91: VRP Acceleration is a Strong Complementary Signal
- VRP(7d) 10d acceleration at 5d-7d horizon shows IC=0.089-0.113 with near-perfect IS/OOS stability
- VRP(14d) 5d acceleration at 3d horizon: IC=0.1012 with IS=0.100, OOS=0.106
- Interpretation: when VRP is rapidly increasing (IV expanding faster than RV), prices tend to follow upward
- **Action: Test as overlay to existing VRP sizing signal. May improve entry timing.**

### Finding F-92: DVOL is a Powerful Mean-Reverting Process
- DVOL level predicts its own future decline with IC=-0.33 at 30d (t=-14.2)
- Perfect IS/OOS consistency across all horizons
- **Does NOT translate to directional price signal** (B5 shows DVOL level has ~zero IC for price)
- **Action: Exploit for volatility-specific strategies (sell high IV, buy low IV). Not useful for directional crypto trading.**

### Finding F-93: BTC-ETH DVOL Spread is an Emerging Signal
- When BTC IV is elevated relative to ETH IV, BTC tends to outperform at 7-14d horizons
- IC=0.1317 at 14d but IS is much weaker than OOS -- regime-dependent
- **Action: Monitor. Add to watchlist but do not deploy until IS stabilizes.**

### Portfolio Action Recommendations

1. **Carry strategies (s29, s65): REMAIN DORMANT** -- no recovery signal detected
2. **NEW: DVOL ROC signal**: Build prototype strategy using DVOL ROC(20d) as directional signal at 5-7d horizon. Target IC > 0.08 in live OOS.
3. **ENHANCE: VRP sizing**: Add VRP acceleration (5d change in VRP(14d)) as entry timing overlay to existing VRP position sizing.
4. **VOLATILITY TRADING**: DVOL mean-reversion (IC=-0.33) is strong enough for a standalone vol strategy -- worth exploring if options/DVOL products are tradeable.
5. **KILL LIST**: Raw funding level, funding acceleration, funding z-score, funding dispersion, DVOL level as directional -- all killed.

---

## 5. Data Quality Notes

- **Binance funding_rates_full.json:** 1200 records per symbol (BTC, ETH), 8h intervals, 2019-2026. This is sparse -- only ~288 unique daily observations due to irregular API pagination. IC results for funding signals should be treated with extra skepticism due to thin sample.
- **Live funding JSONL:** 16 records per token, covering only March 4-9 2026 (5 days). Useful for current state snapshot only.
- **Extended funding parquet (19 tokens):** 243 daily observations (2025-07 to 2026-02). Adequate for cross-sectional analysis but short.
- **Deribit DVOL:** 1827 daily observations (2021-03 to 2026-03). Excellent sample size for IC analysis.
- **BTC spot 1h:** 54,141 hourly bars (2020-01 to 2026-03). Robust price data.
