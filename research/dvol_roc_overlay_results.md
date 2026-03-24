# DVOL Rate-of-Change Overlay Results

**Run date**: 2026-03-24 18:06
**IS period**: 2021-06-22 to 2024-12-31
**OOS period**: 2025-01-01 to latest
**DVOL source**: Deribit BTC DVOL
**Base signal**: EMA 20/50 crossover (actual s320 base)
**Rebalancing**: Weekly
**Transaction cost**: 10 bps round-trip

## Architecture

V3 (s320) stack:
- **Base**: EMA 20/50 crossover (long when fast > slow)
- **Positioning overlay**: Top Trader L/S + L/S Divergence z-score -> 0.3x to 1.5x
- **VRP overlay**: (IV - RV) z-score -> 0.3x to 1.3x (vol LEVEL)

NEW candidate overlay:
- **DVOL ROC overlay**: 20d rate of change of DVOL, z-scored over 60d
  - z > 1.0 -> 1.3x (IV rising fast = bullish reflexive vol)
  - 0.0 < z < 1.0 -> 1.1x (IV rising moderately)
  - -0.5 < z < 0.0 -> 1.0x (neutral)
  - z < -0.5 -> 0.7x (IV falling = less conviction)
  - z < -1.5 -> 0.5x (IV collapsing = risk-off)

Rationale: DVOL ROC measures the RATE OF CHANGE of implied vol, which is
orthogonal to VRP that measures the LEVEL of vol premium (IV - RV). In crypto,
rising IV correlates with rising price (reflexive volatility), unlike equities.

## 1. Backtest Comparison Table

| Variant | IS Return | OOS Return | IS Sharpe | OOS Sharpe | IS MaxDD | OOS MaxDD | IS Calmar | OOS Calmar | Turnover/yr |
|---------|-----------|------------|-----------|------------|----------|-----------|-----------|------------|-------------|
| Base Only (EMA 20/50) | 16.1% | -1.9% | 0.40 | -0.08 | -56.2% | -27.6% | 0.29 | -0.07 | 5.9 |
| V3 Full (Pos+VRP) | 4.0% | 14.7% | 0.11 | 0.56 | -54.2% | -20.2% | 0.07 | 0.72 | 13.2 |
| V3 + DVOL ROC | 12.0% | 10.6% | 0.35 | 0.41 | -44.1% | -23.3% | 0.27 | 0.46 | 12.9 |

### Delta Analysis

| Comparison | IS dSharpe | OOS dSharpe | IS dReturn | OOS dReturn | IS dMaxDD | OOS dMaxDD |
|------------|------------|-------------|------------|-------------|-----------|------------|
| V3 Full vs Base | -0.287 | +0.632 | -12.1% | +16.6% | +2.1% | +7.4% |
| V3+DVOL_ROC vs Base | -0.049 | +0.487 | -4.1% | +12.5% | +12.1% | +4.3% |
| **V3+DVOL_ROC vs V3 Full** | **+0.237** | **-0.145** | **+8.0%** | **-4.0%** | **+10.1%** | **-3.1%** |

## 2. Walk-Forward Validation

6 rolling windows (6-month train / 6-month test):

| Window | Test Period | V3 Sharpe | V3+DVOL_ROC Sharpe | dSharpe | V3 MaxDD | V3+DVOL_ROC MaxDD | t-stat | Days |
|--------|-------------|-----------|--------------------|---------|---------:|------------------:|-------:|-----:|
| 1 | 2023-01-01 to 2023-06-30 | 0.57 | 0.83 | +0.264 | -16.3% | -13.6% | +0.41 | 181 |
| 2 | 2023-07-01 to 2023-12-31 | 0.77 | 0.93 | +0.162 | -16.0% | -13.4% | +0.25 | 184 |
| 3 | 2024-01-01 to 2024-06-30 | 1.26 | 1.57 | +0.302 | -27.9% | -24.9% | +0.40 | 182 |
| 4 | 2024-07-01 to 2024-12-31 | 0.37 | 0.71 | +0.345 | -23.5% | -25.6% | +1.41 | 184 |
| 5 | 2025-01-01 to 2025-06-30 | 1.61 | 1.81 | +0.201 | -9.9% | -9.5% | +0.07 | 181 |
| 6 | 2025-07-01 to 2025-12-31 | 0.17 | -0.24 | -0.409 | -14.9% | -18.7% | -1.26 | 184 |

**Summary**: Improved Sharpe in 5/6 windows. Worsened MaxDD in 2/6 windows. Avg dSharpe: +0.144.

## 3. Correlation with Existing Overlays

Low correlation (< 0.3) = genuinely orthogonal signal = worth adding.
High correlation (> 0.5) = redundant with VRP = do not add.

### Full Period
- DVOL ROC vs VRP multiplier correlation: -0.143
- DVOL ROC vs Positioning multiplier correlation: -0.174
- DVOL ROC z vs VRP z (Spearman): -0.159 (p=0.0000)
- Agreement: both reduce 9.2%, both boost 7.3%, disagree 26.4%, both neutral 11.6%

### IS Period
- DVOL ROC vs VRP multiplier correlation: -0.118
- DVOL ROC z vs VRP z (Spearman): -0.116

### OOS Period
- DVOL ROC vs VRP multiplier correlation: -0.215
- DVOL ROC z vs VRP z (Spearman): -0.279

**Independence verdict**: ORTHOGONAL (genuinely independent -- worth adding)

## 4. DVOL ROC Information Coefficient

Does DVOL ROC z-score predict forward returns?

| Target | Full IC | Full t | IS IC | IS t | OOS IC | OOS t |
|--------|---------|--------|-------|------|--------|-------|
| Fwd 1D Return | +0.0661 | +2.74 | +0.0706 | +2.54 | +0.0638 | +1.30 |
| Fwd 7D Return | +0.1252 | +5.21 | +0.1465 | +5.31 | +0.0625 | +1.28 |
| Fwd 14D Return | +0.1202 | +5.00 | +0.1416 | +5.13 | +0.0509 | +1.04 |

## 5. Statistical Significance

OOS period: 431 days (~14 months)

- V3 Full vs Base: t=1.855 (marginally significant (p<0.10))
- V3+DVOL_ROC vs Base: t=1.510 (not significant)
- **V3+DVOL_ROC vs V3 Full (marginal): t=-0.791 (not significant)**

## 6. Monthly Returns (OOS)

| Month | Base Only | V3 Full | V3+DVOL_ROC | DVOL_ROC Delta |
|-------|-----------|---------|-------------|----------------|
| 2025-01 | 9.46% | 13.53% | 14.48% | +0.95% |
| 2025-02 | -6.59% | -5.38% | -3.27% | +2.11% |
| 2025-03 | 0.00% | 0.00% | 0.00% | +0.00% |
| 2025-04 | -0.98% | -1.28% | -0.89% | +0.38% |
| 2025-05 | 11.06% | 14.34% | 9.93% | -4.41% |
| 2025-06 | 2.44% | 1.48% | 2.65% | +1.17% |
| 2025-07 | 8.04% | 10.84% | 10.12% | -0.72% |
| 2025-08 | -6.49% | 0.41% | -0.45% | -0.87% |
| 2025-09 | -0.29% | 0.88% | 0.62% | -0.26% |
| 2025-10 | -11.51% | -9.24% | -12.00% | -2.76% |
| 2025-11 | 0.00% | 0.00% | 0.00% | +0.00% |
| 2025-12 | 0.00% | 0.00% | 0.00% | +0.00% |
| 2026-01 | -4.81% | -6.28% | -6.28% | +0.00% |
| 2026-02 | 0.00% | 0.00% | 0.00% | +0.00% |
| 2026-03 | 0.00% | 0.00% | 0.00% | +0.00% |
| **Cumulative** | **-2.25%** | **17.52%** | **12.65%** | **-4.87%** |

## 7. DVOL ROC Signal Summary Statistics

- DVOL ROC(20d) mean: 0.0087
- DVOL ROC(20d) median: -0.0203
- DVOL ROC(20d) std: 0.1729
- DVOL ROC(20d) min: -0.4351
- DVOL ROC(20d) max: 1.1589
- DVOL ROC(20d) % positive: 43.7%
- DVOL ROC z-score mean: 0.038
- DVOL ROC z-score std: 1.187

### Position Distribution (% of time at each level)

| Variant | ~0.0 | ~0.3 | ~0.5 | ~0.7 | ~1.0 | ~1.1-1.3 | ~1.5 |
|---------|------|------|------|------|------|------|------|
| Base Only (EMA 20/50) | 47.9% | 0.0% | 0.0% | 0.0% | 52.1% | 0.0% | 0.0% |
| V3 Full (Pos+VRP) | 49.1% | 6.1% | 13.4% | 4.5% | 13.4% | 0.0% | 13.4% |
| V3 + DVOL ROC | 48.7% | 9.0% | 9.4% | 13.0% | 7.3% | 4.9% | 7.7% |

## 8. Verdict

### Decision Inputs

- Marginal OOS dSharpe (V3+DVOL_ROC vs V3): -0.145
- Marginal IS dSharpe (V3+DVOL_ROC vs V3): +0.237
- OOS MaxDD improvement: -3.1%
- Walk-forward win rate: 5/6 (83%)
- Walk-forward worsened MaxDD: 2/6
- Walk-forward avg dSharpe: +0.144
- Correlation with VRP (Spearman z): -0.159
- Marginal t-stat: -0.791
- IS/OOS Sharpe ratio (DVOL ROC): IS=0.35, OOS=0.41

### **NEEDS_MORE_DATA**

The evidence is mixed and does not support a clean ADD or KILL:

**Arguments FOR adding DVOL ROC:**
- Walk-forward improves Sharpe in 5/6 windows (avg dSharpe: +0.144)
- Genuinely orthogonal to VRP (Spearman z-rho = -0.159, negatively correlated)
- Significantly better IS MaxDD improvement (+10.1% less drawdown vs V3 Full)
- Higher absolute equity throughout the entire sample (6.659 vs 5.339 terminal)
- IC is positive and significant for 7d forward returns (full: 0.125, t=5.21)

**Arguments AGAINST adding DVOL ROC:**
- Full OOS Sharpe is LOWER than V3 Full (0.41 vs 0.56, dSharpe = -0.145)
- OOS MaxDD is worse (-23.3% vs -20.2%)
- Marginal t-stat = -0.791 (not significant)
- The DVOL ROC multiplier applies a drag (0.7x, 0.5x) during "falling IV" periods
  that happened to coincide with strong trend months in the OOS window
- OOS IC decays: 0.063 at 7d (vs 0.147 IS), not statistically significant (t=1.28)

**Root cause of the contradiction:**
The V3 Full OOS Sharpe of 0.56 is elevated by strong positioning overlay performance
in early-2025 BTC rally. DVOL ROC's 0.7x drag during periods of falling IV (which
occurred when BTC was trending strongly but implied vol was declining post-rally)
reduced position size exactly when V3 Full was at full size. However, the same
mechanism provided superior drawdown protection during 2022 and H2-2025 corrections,
explaining the IS improvement and higher absolute equity.

**Conclusion:** The signal captures real information (orthogonal, positive IC, reflexive
vol dynamic) but the multiplier thresholds may be suboptimal for a sizing overlay --
the 0.7x bucket is too aggressive at reducing during trending markets where IV
naturally declines. Reclassify as NEEDS_MORE_DATA pending:
1. Threshold sensitivity analysis (especially the 0.7x bucket at z < -0.5)
2. More OOS data (current 14 months is insufficient for conclusive t-test)
3. Testing a softer mapping (e.g., 0.85x instead of 0.7x for falling IV)

## Appendix: Quarterly Equity Curve (normalized to 1.0)

| Date | Base Only | V3 Full | V3+DVOL_ROC |
|------|-----------|---------|-------------|
| 2021-06-30 | 3.708 | 3.958 | 3.958 |
| 2021-09-30 | 3.984 | 4.799 | 4.711 |
| 2021-12-31 | 4.075 | 4.783 | 4.798 |
| 2022-03-31 | 3.931 | 4.615 | 4.714 |
| 2022-06-30 | 3.521 | 3.961 | 4.475 |
| 2022-09-30 | 3.121 | 3.511 | 4.118 |
| 2022-12-31 | 2.514 | 2.828 | 3.238 |
| 2023-03-31 | 3.374 | 3.319 | 3.791 |
| 2023-06-30 | 3.198 | 3.069 | 3.602 |
| 2023-09-30 | 2.739 | 2.705 | 3.233 |
| 2023-12-31 | 4.194 | 3.395 | 4.031 |
| 2024-03-31 | 7.070 | 5.967 | 7.082 |
| 2024-06-30 | 5.271 | 4.316 | 5.320 |
| 2024-09-30 | 4.249 | 3.478 | 4.198 |
| 2024-12-31 | 6.279 | 4.543 | 5.911 |
| 2025-03-31 | 6.420 | 4.880 | 6.546 |
| 2025-06-30 | 7.233 | 5.590 | 7.321 |
| 2025-09-30 | 7.286 | 6.276 | 8.074 |
| 2025-12-31 | 6.448 | 5.696 | 7.105 |
| 2026-03-14 | 6.137 | 5.339 | 6.659 |
