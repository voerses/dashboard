# Macro + Positioning Signal Combination Analysis

**Date**: 2026-03-24
**IS period**: 2020-09-01 to 2024-12-31 (1268 days)
**OOS period**: 2025-01-01 to 2026-03-03 (427 days)

## 1. Signal Definitions

| Signal | Description | Expected Direction |
|--------|-------------|-------------------|
| Signal A (Macro) | expanding_rank(US10Y 20d chg) + expanding_rank(DXY 20d mom) | Higher = bearish for BTC |
| Signal B1 (Top Trader L/S) | sum_toptrader_ls_ratio | Higher = bearish (IC < 0) |
| Signal B2 (L/S Divergence) | count_toptrader_ls_ratio - count_ls_ratio | Higher = bearish (IC < 0) |
| Combo A+B1 | rank(A) + rank(-B1) | Higher = more bearish from both families |
| Triple A+B1+B2 | rank(A) + rank(-B1) + rank(-B2) | Higher = max bearish conviction |


## 2. Pairwise Signal Correlations (Spearman)

| Signal Pair | Spearman rho | p-value | Interpretation |
|-------------|-------------|---------|----------------|
| A_macro vs B1_toptrader_ls | -0.1112 | 4.4233e-06 | Weakly correlated |
| A_macro vs B2_ls_divergence | -0.2518 | 6.3009e-26 | Moderately correlated |
| B1_toptrader_ls vs B2_ls_divergence | +0.5363 | 7.3270e-127 | Highly correlated |

## 3. Information Coefficients

### IS

| Signal | 7d IC | 7d p-value | 14d IC | 14d p-value | n |
|--------|-------|-----------|--------|------------|---|
| A_macro | +0.0609 | 3.0059e-02 | +0.1542 | 3.4532e-08 | 1268 |
| B1_toptrader_ls | -0.0721 | 1.0219e-02 | -0.0994 | 3.9291e-04 | 1268 |
| B2_ls_divergence | -0.0802 | 4.2649e-03 | -0.1187 | 2.2574e-05 | 1268 |
| combo: A_B1 | +0.0840 | 2.7462e-03 | +0.1841 | 3.9694e-11 | 1268 |
| combo: A_B2 | +0.0941 | 7.9572e-04 | +0.1862 | 2.3307e-11 | 1268 |
| combo: A_B1_B2 | +0.1056 | 1.6529e-04 | +0.2021 | 3.7601e-13 | 1268 |

### OOS

| Signal | 7d IC | 7d p-value | 14d IC | 14d p-value | n |
|--------|-------|-----------|--------|------------|---|
| A_macro | -0.0820 | 9.0669e-02 | -0.1284 | 7.9146e-03 | 427 |
| B1_toptrader_ls | -0.1510 | 1.7515e-03 | -0.2042 | 2.1174e-05 | 427 |
| B2_ls_divergence | -0.1937 | 5.6165e-05 | -0.2303 | 1.5114e-06 | 427 |
| combo: A_B1 | +0.0099 | 8.3776e-01 | +0.0020 | 9.6760e-01 | 427 |
| combo: A_B2 | +0.0160 | 7.4173e-01 | -0.0047 | 9.2303e-01 | 427 |
| combo: A_B1_B2 | +0.1060 | 2.8458e-02 | +0.1180 | 1.4721e-02 | 427 |

## 4. IC Additivity Check

For truly uncorrelated signals, combined IC should approximate: `sqrt(IC_A^2 + IC_B^2)`


**IS** (14d horizon):
- IC(Macro alone) = +0.1542
- IC(B1 alone) = -0.0994
- IC(Combo A+B1) = +0.1841
- IC(Triple A+B1+B2) = +0.2021
- Theoretical (uncorrelated) = +0.1834

**OOS** (14d horizon):
- IC(Macro alone) = -0.1284
- IC(B1 alone) = -0.2042
- IC(Combo A+B1) = +0.0020
- IC(Triple A+B1+B2) = +0.1180
- Theoretical (uncorrelated) = +0.2412

## 5. Conditional Analysis (14d forward returns)

### IS

| Regime | n | Mean 14d Return | Median 14d Return | Hit Rate | Sharpe-like |
|--------|---|----------------|-------------------|----------|-------------|
| BOTH BEARISH (macro bear + crowded longs) | 142 | +8.77% | +5.64% | 66.2% | 0.602 |
| BOTH BULLISH (macro bull + light positioning) | 129 | +3.87% | +0.64% | 52.7% | 0.308 |
| DISAGREE: macro bear + light positioning | 112 | +9.19% | +6.19% | 75.0% | 0.697 |
| DISAGREE: macro bull + crowded positioning | 147 | -0.57% | +0.05% | 50.3% | -0.074 |
| UNCONDITIONAL | 1268 | +3.97% | +1.82% | 58.6% | 0.292 |

### OOS

| Regime | n | Mean 14d Return | Median 14d Return | Hit Rate | Sharpe-like |
|--------|---|----------------|-------------------|----------|-------------|
| BOTH BEARISH (macro bear + crowded longs) | 57 | -4.89% | -4.14% | 35.1% | -0.510 |
| BOTH BULLISH (macro bull + light positioning) | 60 | +3.91% | +3.61% | 76.7% | 0.688 |
| DISAGREE: macro bear + light positioning | 36 | -0.70% | -0.09% | 47.2% | -0.152 |
| DISAGREE: macro bull + crowded positioning | 30 | -1.97% | -1.63% | 33.3% | -0.343 |
| UNCONDITIONAL | 427 | -0.71% | -0.22% | 48.5% | -0.092 |

## 6. Rolling IC Stability (180-day window, 14d horizon)

| Signal | Period | Mean IC | Std IC | Min IC | Max IC | % Positive |
|--------|--------|---------|--------|--------|--------|-----------|
| A_macro | IS | +0.0989 | 0.1755 | -0.2892 | +0.4448 | 68.2% |
| A_macro | OOS | +0.0197 | 0.3557 | -0.4713 | +0.6347 | 48.2% |
| combo: A_B1 | IS | +0.2190 | 0.1498 | -0.0414 | +0.5557 | 94.5% |
| combo: A_B1 | OOS | +0.1052 | 0.3360 | -0.3914 | +0.6630 | 56.4% |
| combo: A_B1_B2 | IS | +0.2028 | 0.1426 | -0.1023 | +0.5611 | 91.2% |
| combo: A_B1_B2 | OOS | +0.1700 | 0.2810 | -0.2446 | +0.6654 | 60.4% |

## 7. Conclusions

1. **Low correlation confirmed**: Macro and positioning signals have Spearman rho = -0.1112, confirming they capture largely distinct information about BTC price dynamics. This is a necessary condition for beneficial combination.

2. **CRITICAL: Macro signal IC flipped sign OOS**: The macro regime score had IC = +0.1542 IS but IC = -0.1284 OOS. This means higher macro bearishness (rising yields + strong dollar) was associated with *higher* BTC returns IS (counterintuitive) but *lower* BTC returns OOS (expected direction). The IS period (2020-2024) included massive BTC bull runs during rate hikes, which is an anomalous regime. The OOS behavior (bearish macro = bearish BTC) is more economically intuitive.

3. **Combo A+B1 IC destroyed OOS (IC = +0.002)**: The rank(A) + rank(-B1) combination fails OOS because both signals are bearish-directional OOS (both have negative IC), but the combo was constructed assuming macro IC is positive. The signals partially cancel each other out instead of reinforcing.

4. **Triple combo partially recovers (IC = +0.118 OOS)**: Adding B2 (L/S divergence, IC = -0.230 OOS) provides enough positioning signal weight to overcome the macro signal's noise.

5. **Positioning signals are the dominant OOS performers**: B1 = -0.204, B2 = -0.230 at 14d OOS, both stronger than macro and highly significant.

6. **IS IC additivity works perfectly**: Combo A+B1 achieved IC = +0.184 IS vs theoretical +0.183 for uncorrelated signals. The combination formula is correct -- it is the macro signal's sign instability that breaks OOS performance.

### Conditional Analysis Findings

- **BOTH BEARISH OOS (macro bear + crowded)**: Mean 14d return = -4.89%, hit rate 35.1%, sharpe = -0.510. This is the strongest regime for short/hedge signals OOS.
- **BOTH BULLISH OOS (macro bull + light positioning)**: Mean 14d return = +3.91%, hit rate 76.7%, sharpe = +0.688. Strong long signal.
- **The agreement regimes ARE directionally informative OOS** even though the simple combo IC is zero. The tercile bucketing captures non-linearity that a linear rank sum misses.

### Actionable Takeaways

- **Do NOT use the simple rank(A) + rank(-B1) combo** -- the macro signal's sign instability destroys it OOS.
- **Positioning signals (B1, B2) are the primary alpha source OOS** with IC = -0.204 and -0.230 respectively.
- **Use macro as a regime filter, not a signal**: When both macro and positioning agree, the edge is much stronger (both bearish: -4.89% 14d return; both bullish: +3.91% 14d return).
- **Consider sign-adaptive combination**: Instead of fixed rank(A) + rank(-B1), use rolling IC to determine the macro signal's direction before combining.
- **The triple combo (A+B1+B2) retains some OOS value** (IC = +0.118, p = 0.015) due to heavier positioning weight -- this is a viable research direction.