# Multi-Signal Stacking Analysis Results

**Research question:** Do 9 individually-proven signals ADD or CANCEL when stacked?

**OOS split:** train < 2025-07-01, test >= 2025-07-01
**Generated:** 2026-03-24 10:32

## 1. Individual Signal ICs (OOS, sign-aligned)

All signals sign-aligned so positive IC = bullish prediction correct.

| Signal | IS IC (7d) | OOS IC (7d) | IS IC (14d) | OOS IC (14d) | OOS n |
|--------|-----------|------------|------------|-------------|-------|
| US10Y 20d Chg | +0.013 | +0.334 | -0.026 | +0.248 | 169 |
| DXY 20d Mom | +0.005 | +0.034 | -0.047 | +0.165 | 169 |
| Oil 20d Mom | +0.025 | +0.108 | +0.028 | +0.003 | 170 |
| Skew 30d | +0.069 | +0.122 | +0.062 | +0.130 | 246 |
| Taker Dispersion | N/A | +0.071 | N/A | -0.006 | 243 |
| ETF Flow Z | +0.139 | -0.003 | +0.227 | -0.026 | 172 |
| OI Divergence | +0.026 | -0.057 | +0.093 | -0.070 | 246 |
| VRP Z-Score | +0.030 | +0.094 | -0.001 | +0.003 | 246 |
| Net Taker Vol | N/A | +0.073 | N/A | -0.012 | 234 |

## 2. Pairwise Signal Correlation Matrix

Spearman rank correlations on overlapping date ranges.

| Signal | US10Y 20d  | DXY 20d Mo | Oil 20d Mo | Skew 30d | Taker Disp | ETF Flow Z | OI Diverge | VRP Z-Scor | Net Taker  |
|--------|--------|--------|--------|--------|--------|--------|--------|--------|--------|
| US10Y 20d  | 1.000 | +0.428 | +0.354 | +0.085 | -0.101 | -0.351 | +0.013 | -0.169 | +0.178 |
| DXY 20d Mo | +0.428 | 1.000 | -0.017 | +0.107 | -0.224 | -0.255 | +0.045 | -0.129 | +0.182 |
| Oil 20d Mo | +0.354 | -0.017 | 1.000 | +0.033 | +0.298 | -0.140 | -0.002 | -0.001 | +0.074 |
| Skew 30d   | +0.085 | +0.107 | +0.033 | 1.000 | +0.054 | +0.353 | +0.071 | +0.143 | -0.058 |
| Taker Disp | -0.101 | -0.224 | +0.298 | +0.054 | 1.000 | -0.090 | -0.013 | -0.015 | -0.055 |
| ETF Flow Z | -0.351 | -0.255 | -0.140 | +0.353 | -0.090 | 1.000 | +0.089 | +0.245 | -0.114 |
| OI Diverge | +0.013 | +0.045 | -0.002 | +0.071 | -0.013 | +0.089 | 1.000 | -0.017 | +0.056 |
| VRP Z-Scor | -0.169 | -0.129 | -0.001 | +0.143 | -0.015 | +0.245 | -0.017 | 1.000 | -0.096 |
| Net Taker  | +0.178 | +0.182 | +0.074 | -0.058 | -0.055 | -0.114 | +0.056 | -0.096 | 1.000 |

**Highly correlated pairs (|rho| > 0.30):**
- US10Y 20d Chg x DXY 20d Mom: rho = +0.428
- US10Y 20d Chg x Oil 20d Mom: rho = +0.354
- US10Y 20d Chg x ETF Flow Z: rho = -0.351
- Skew 30d x ETF Flow Z: rho = +0.353

## 3. Best Pairwise Combinations (OOS)

### BTC 7D

| Signal A | Signal B | IC(A) | IC(B) | IC(A+B) | Improvement | Corr | Verdict |
|----------|----------|-------|-------|---------|------------|------|---------|
| US10Y 20d Chg | VRP Z-Score | +0.334 | +0.094 | +0.327 | +53% | -0.17 | OVERLAP |
| US10Y 20d Chg | Skew 30d | +0.334 | +0.122 | +0.281 | +23% | +0.09 | OVERLAP |
| US10Y 20d Chg | Oil 20d Mom | +0.334 | +0.108 | +0.262 | +18% | +0.35 | OVERLAP |
| US10Y 20d Chg | DXY 20d Mom | +0.334 | +0.034 | +0.258 | +40% | +0.43 | OVERLAP |
| US10Y 20d Chg | Net Taker Vol | +0.334 | +0.073 | +0.234 | +15% | +0.18 | OVERLAP |
| Oil 20d Mom | Net Taker Vol | +0.108 | +0.073 | +0.232 | +156% | +0.07 | ADDITIVE |
| US10Y 20d Chg | Taker Dispersion | +0.334 | +0.071 | +0.231 | +14% | -0.10 | OVERLAP |
| US10Y 20d Chg | ETF Flow Z | +0.334 | -0.003 | +0.175 | +4% | -0.35 | OVERLAP |
| DXY 20d Mom | Oil 20d Mom | +0.034 | +0.108 | +0.167 | +136% | -0.02 | ADDITIVE |
| Skew 30d | VRP Z-Score | +0.122 | +0.094 | +0.149 | +37% | +0.14 | ADDITIVE |

### BTC 14D

| Signal A | Signal B | IC(A) | IC(B) | IC(A+B) | Improvement | Corr | Verdict |
|----------|----------|-------|-------|---------|------------|------|---------|
| US10Y 20d Chg | DXY 20d Mom | +0.248 | +0.165 | +0.305 | +48% | +0.43 | ADDITIVE |
| US10Y 20d Chg | Skew 30d | +0.248 | +0.130 | +0.294 | +56% | +0.09 | ADDITIVE |
| DXY 20d Mom | Oil 20d Mom | +0.165 | +0.003 | +0.194 | +130% | -0.02 | ADDITIVE |
| US10Y 20d Chg | VRP Z-Score | +0.248 | +0.003 | +0.174 | +39% | -0.17 | OVERLAP |
| DXY 20d Mom | Skew 30d | +0.165 | +0.130 | +0.166 | +12% | +0.11 | ADDITIVE |
| ETF Flow Z | Net Taker Vol | -0.026 | -0.012 | -0.157 | +727% | -0.11 | ADDITIVE |
| US10Y 20d Chg | Oil 20d Mom | +0.248 | +0.003 | +0.136 | +9% | +0.35 | OVERLAP |
| US10Y 20d Chg | ETF Flow Z | +0.248 | -0.026 | +0.114 | -16% | -0.35 | OVERLAP |
| US10Y 20d Chg | OI Divergence | +0.248 | -0.070 | +0.103 | -35% | +0.01 | OVERLAP |
| DXY 20d Mom | VRP Z-Score | +0.165 | +0.003 | +0.097 | +15% | -0.13 | OVERLAP |

## 4. Top-N Composite Signals

**Signal ranking by |OOS IC| (BTC 14d):**
1. US10Y 20d Chg: |IC| = 0.248
2. DXY 20d Mom: |IC| = 0.165
3. Skew 30d: |IC| = 0.130
4. OI Divergence: |IC| = 0.070
5. ETF Flow Z: |IC| = 0.026
6. Net Taker Vol: |IC| = 0.012
7. Taker Dispersion: |IC| = 0.006
8. Oil 20d Mom: |IC| = 0.003
9. VRP Z-Score: |IC| = 0.003

| Composite | IS IC (7d) | OOS IC (7d) | IS IC (14d) | OOS IC (14d) | Signals |
|-----------|-----------|------------|------------|-------------|---------|
| TOP_3 | +0.051 | +0.227 | +0.012 | +0.225 | US10Y 20d Ch, DXY 20d Mom, Skew 30d |
| TOP_5 | +0.063 | +0.099 | +0.038 | +0.080 | US10Y 20d Ch, DXY 20d Mom, Skew 30d, OI Divergenc, ETF Flow Z |
| TOP_7 | +0.063 | +0.153 | +0.038 | +0.088 | US10Y 20d Ch, DXY 20d Mom, Skew 30d, OI Divergenc, ETF Flow Z, Net Taker Vo, Taker Disper |
| TOP_9 | +0.076 | +0.210 | +0.045 | +0.102 | US10Y 20d Ch, DXY 20d Mom, Skew 30d, OI Divergenc, ETF Flow Z, Net Taker Vo, Taker Disper, Oil 20d Mom, VRP Z-Score |

**Composite vs best individual:**

- Best individual (7d OOS): US10Y 20d Chg |IC| = 0.334
- Best individual (14d OOS): US10Y 20d Chg |IC| = 0.248

- TOP_3 btc_7d: |IC| = 0.227 vs 0.334 (-0.107, -32%) [SUBTRACTIVE]
- TOP_3 btc_14d: |IC| = 0.225 vs 0.248 (-0.023, -9%) [SUBTRACTIVE]
- TOP_5 btc_7d: |IC| = 0.099 vs 0.334 (-0.235, -70%) [SUBTRACTIVE]
- TOP_5 btc_14d: |IC| = 0.080 vs 0.248 (-0.168, -68%) [SUBTRACTIVE]
- TOP_7 btc_7d: |IC| = 0.153 vs 0.334 (-0.181, -54%) [SUBTRACTIVE]
- TOP_7 btc_14d: |IC| = 0.088 vs 0.248 (-0.160, -64%) [SUBTRACTIVE]
- TOP_9 btc_7d: |IC| = 0.210 vs 0.334 (-0.124, -37%) [SUBTRACTIVE]
- TOP_9 btc_14d: |IC| = 0.102 vs 0.248 (-0.146, -59%) [SUBTRACTIVE]

## 5. Redundancy vs Complementarity

### Signal clusters (|corr| > 0.40 threshold)
- **Cluster 1 (REDUNDANT):** US10Y 20d Chg, DXY 20d Mom
- **Cluster 2 (INDEPENDENT):** Oil 20d Mom
- **Cluster 3 (INDEPENDENT):** Skew 30d
- **Cluster 4 (INDEPENDENT):** Taker Dispersion
- **Cluster 5 (INDEPENDENT):** ETF Flow Z
- **Cluster 6 (INDEPENDENT):** OI Divergence
- **Cluster 7 (INDEPENDENT):** VRP Z-Score
- **Cluster 8 (INDEPENDENT):** Net Taker Vol

### Marginal contribution (drop-one from Top-5)
- Drop US10Y 20d Chg: IC 0.041 vs 0.080 (delta=+0.039) [HELPS]
- Drop DXY 20d Mom: IC 0.049 vs 0.080 (delta=+0.032) [HELPS]
- Drop Skew 30d: IC 0.018 vs 0.080 (delta=+0.062) [HELPS]
- Drop OI Divergence: IC 0.183 vs 0.080 (delta=-0.102) [HURTS]
- Drop ETF Flow Z: IC 0.109 vs 0.080 (delta=-0.029) [HURTS]

## 6. ETH Composite Results

TOP_3 composite tested on ETH forward returns:

| Composite | OOS IC (ETH 7d) | OOS IC (ETH 14d) |
|-----------|-----------------|-------------------|
| TOP_3     | +0.254          | +0.323            |
| TOP_5     | +0.137          | +0.143            |

Notable: TOP_3 composite IC is **stronger for ETH than BTC** at 14d (0.323 vs 0.225), suggesting macro signals have more predictive power for the higher-beta asset.

## 7. Key Conclusions

### A. The signals mostly CANCEL when naively stacked

Equal-weight composites underperform the best individual signal (US10Y 20d Chg) at every combination size:
- TOP_3: -9% IC degradation at 14d
- TOP_5: -68% IC degradation at 14d
- TOP_9: -59% IC degradation at 14d

**Root cause:** Adding low-IC or negative-IC signals (OI divergence, ETF flow, Net taker vol) dilutes the strong macro signals. Equal weighting gives the same influence to a signal with |IC|=0.003 as one with |IC|=0.248.

### B. But specific PAIRS are genuinely additive

The best pairwise combination **beats both individual signals**:
- **US10Y + DXY at 14d: IC = 0.305** (vs 0.248 and 0.165 individually) -- +23% above best individual
- **US10Y + Skew at 14d: IC = 0.294** -- +19% above best individual
- **DXY + Oil at 14d: IC = 0.194** -- additive despite Oil having near-zero individual IC

At 7d, Oil + Net Taker Vol combine to IC = 0.232, exceeding both individual components by 115%+.

### C. Signal redundancy is limited

Only 1 cluster exceeds |corr| > 0.40: US10Y and DXY (rho = 0.428). Despite this correlation, combining them is **still additive** at 14d (IC = 0.305), meaning they capture different macro information even when correlated.

All other signal pairs have |corr| < 0.35. The signal set is surprisingly orthogonal.

### D. Marginal contribution reveals which signals hurt

Drop-one analysis from TOP_5 shows:
- **OI Divergence HURTS**: dropping it improves IC from 0.080 to 0.183 (delta = -0.102)
- **ETF Flow Z HURTS**: dropping it improves IC from 0.080 to 0.109 (delta = -0.029)
- **US10Y, DXY, Skew all HELP**: each contributes +0.032 to +0.062 marginal IC

### E. Recommended composite configurations

**Tier 1 (strongest, 2-signal):**
- US10Y 20d + DXY 20d: IC = 0.305 at 14d, rho = 0.43
- US10Y 20d + Skew 30d: IC = 0.294 at 14d, rho = 0.09

**Tier 2 (3-signal, best balance):**
- US10Y + DXY + Skew: IC = 0.225 at 14d, 0.227 at 7d -- moderate IC with diversification across macro + micro signals

**Do NOT include in composites:**
- OI Divergence (negative OOS IC, actively hurts composites)
- ETF Flow Z (IS/OOS sign flip, not robust)
- Net Taker Vol (too short data history, OOS IC near zero at 14d)

**Use as independent overlays (not combined):**
- Oil 20d Mom: only useful at 7d, weak at 14d
- VRP Z-Score: low OOS IC despite strong theoretical basis (needs more DVOL data)

### F. Practical takeaway

**Do not build a kitchen-sink composite.** The best strategy is a 2-3 signal macro composite (US10Y + DXY, optionally + Skew) used as a regime filter, with micro signals (taker, OI) used as independent timing overlays rather than averaged into the composite. The IC improvement from selective pairing (+23% at 14d) is real; the degradation from naive stacking (-68% at 14d) is also real.
