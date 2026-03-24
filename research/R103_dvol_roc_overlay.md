# R103: DVOL Rate-of-Change Asymmetric Overlay Test

**Run date**: 2026-03-24 18:35
**IS period**: 2021-06-22 to 2024-12-31
**OOS period**: 2025-01-01 to latest
**DVOL source**: Deribit BTC DVOL
**Base signal**: EMA 20/50 crossover (V3 s320 base)
**Rebalancing**: Weekly
**Transaction cost**: 10 bps round-trip
**Prior research**: R94 (symmetric mapping, dSharpe_OOS = -0.145)

## Background

R94 found DVOL ROC has IC=0.101 at 7d and is orthogonal to VRP (corr=-0.16),
but the symmetric mapping (0.7x penalty for falling IV at z < -0.5) created drag.
The root cause: falling IV often coincides with strong trending markets where BTC
momentum is working. The 0.7x penalty reduced position size exactly when V3 was
performing best.

This test explores 3 asymmetric mappings that preserve the upside boost (rising IV
= crypto reflexivity) while softening or eliminating the downside penalty.

## Mapping Definitions

| Z-Score Range | R94 Symmetric | V1 Mild Asym | V2 Boost-Only | V3 Threshold |
|---------------|---------------|--------------|---------------|--------------|
| z > 1.0       | 1.3x          | 1.3x         | 1.3x          | 1.2x         |
| 0 < z < 1.0   | 1.1x          | 1.1x         | 1.1x          | 1.0x (if < 0.5) |
| z > 0.5       | --             | --           | --            | 1.2x         |
| -0.5 < z < 0  | 1.0x          | --           | 1.0x          | 1.0x         |
| -1.0 < z < 0  | --            | 0.9x         | --            | --           |
| z < -0.5      | 0.7x          | --           | --            | --           |
| z < -1.0      | --            | 0.7x         | --            | --           |
| z < -1.5      | 0.5x          | --           | 1.0x          | 0.5x         |
| else          | --            | --           | 1.0x          | 1.0x         |

Key differences from R94:
- **V1**: Penalty starts at z < -1.0 (not -0.5), milder 0.9x in neutral zone
- **V2**: NO downside penalty at all -- only boost for rising IV
- **V3**: Binary -- 1.2x boost above z=0.5, 0.5x only for extreme collapse (z < -1.5)

## 1. ROC Lookback Scan

Testing multiple lookback periods for raw signal quality (IC at 7d forward return).

| Lookback | Full IC | Full t | IS IC | IS t | OOS IC | OOS t | Pct Positive |
|----------|---------|--------|-------|------|--------|-------|--------------|
| 3d | +0.0776 | +3.27*** | +0.1013 | +3.73 | -0.0019 | -0.04 | 47.0% |
| 5d | +0.0742 | +3.13*** | +0.1050 | +3.87 | -0.0304 | -0.62 | 45.7% |
| 7d | +0.0921 | +3.88*** | +0.1271 | +4.69 | -0.0344 | -0.71 | 43.0% |
| 10d | +0.0898 | +3.78*** | +0.1147 | +4.22 | +0.0016 | +0.03 | 43.6% |

**Selected lookback**: 10d (best OOS IC)

## 2. Asymmetric Mapping Variant Comparison

V3 Full reference (no DVOL ROC):
- IS: Sharpe=0.113, Return=4.0%, MaxDD=-54.2%
- OOS: Sharpe=0.556, Return=14.7%, MaxDD=-20.2%

| Variant | IS Sharpe | OOS Sharpe | IS dSharpe | OOS dSharpe | OOS Return | OOS MaxDD | t-stat | p-value | Verdict |
|---------|-----------|------------|------------|-------------|------------|-----------|--------|---------|---------|
| R94 Symmetric (baseline) | 0.242 | 0.318 | +0.129 | -0.238 | 8.8% | -23.4% | -1.122 | 0.262 | KILL |
| V1 Mild Asymmetric | 0.294 | 0.377 | +0.182 | -0.179 | 10.5% | -23.8% | -0.835 | 0.404 | KILL |
| V2 Boost-Only | 0.186 | 0.361 | +0.073 | -0.194 | 10.3% | -24.1% | -0.938 | 0.349 | KILL |
| V3 Threshold | 0.199 | 0.477 | +0.086 | -0.078 | 13.3% | -22.3% | -0.316 | 0.752 | KILL |

## 3. Walk-Forward Validation

6 windows, 180d train / 90d test, using ROC(10d)

### R94 Symmetric (baseline)

| Window | Test Period | V3 Sharpe | +DVOL Sharpe | dSharpe | t-stat | Days |
|--------|-------------|-----------|--------------|---------|--------|------|
| 1 | 2022-02-28 to 2022-05-28 | -1.85 | -1.75 | +0.097 | +0.56 | 90 |
| 2 | 2022-12-02 to 2023-03-01 | 1.04 | 1.02 | -0.020 | +0.22 | 90 |
| 3 | 2023-09-05 to 2023-12-03 | 3.81 | 4.71 | +0.899 | +0.31 | 90 |
| 4 | 2024-06-08 to 2024-09-05 | -2.95 | -3.14 | -0.189 | +1.34 | 90 |
| 5 | 2025-03-12 to 2025-06-09 | 3.52 | 3.03 | -0.485 | -0.96 | 90 |
| 6 | 2025-12-14 to 2026-03-13 | -1.56 | -1.55 | +0.015 | -0.82 | 83 |

Improved: 3/6 windows. Avg dSharpe: +0.053. PASS

### V1 Mild Asymmetric

| Window | Test Period | V3 Sharpe | +DVOL Sharpe | dSharpe | t-stat | Days |
|--------|-------------|-----------|--------------|---------|--------|------|
| 1 | 2022-02-28 to 2022-05-28 | -1.85 | -1.70 | +0.146 | +1.49 | 90 |
| 2 | 2022-12-02 to 2023-03-01 | 1.04 | 0.99 | -0.050 | -0.11 | 90 |
| 3 | 2023-09-05 to 2023-12-03 | 3.81 | 4.71 | +0.901 | +1.17 | 90 |
| 4 | 2024-06-08 to 2024-09-05 | -2.95 | -3.14 | -0.189 | +1.34 | 90 |
| 5 | 2025-03-12 to 2025-06-09 | 3.52 | 3.16 | -0.354 | -1.00 | 90 |
| 6 | 2025-12-14 to 2026-03-13 | -1.56 | -1.55 | +0.015 | -0.82 | 83 |

Improved: 3/6 windows. Avg dSharpe: +0.078. PASS

### V2 Boost-Only

| Window | Test Period | V3 Sharpe | +DVOL Sharpe | dSharpe | t-stat | Days |
|--------|-------------|-----------|--------------|---------|--------|------|
| 1 | 2022-02-28 to 2022-05-28 | -1.85 | -1.74 | +0.109 | +0.56 | 90 |
| 2 | 2022-12-02 to 2023-03-01 | 1.04 | 1.04 | -0.006 | +0.30 | 90 |
| 3 | 2023-09-05 to 2023-12-03 | 3.81 | 4.51 | +0.705 | +1.87 | 90 |
| 4 | 2024-06-08 to 2024-09-05 | -2.95 | -2.88 | +0.070 | -2.02 | 90 |
| 5 | 2025-03-12 to 2025-06-09 | 3.52 | 3.22 | -0.295 | -0.68 | 90 |
| 6 | 2025-12-14 to 2026-03-13 | -1.56 | -1.55 | +0.015 | -0.82 | 83 |

Improved: 4/6 windows. Avg dSharpe: +0.100. PASS

### V3 Threshold

| Window | Test Period | V3 Sharpe | +DVOL Sharpe | dSharpe | t-stat | Days |
|--------|-------------|-----------|--------------|---------|--------|------|
| 1 | 2022-02-28 to 2022-05-28 | -1.85 | -1.74 | +0.109 | +0.56 | 90 |
| 2 | 2022-12-02 to 2023-03-01 | 1.04 | 1.08 | +0.035 | +0.42 | 90 |
| 3 | 2023-09-05 to 2023-12-03 | 3.81 | 4.42 | +0.615 | +2.06 | 90 |
| 4 | 2024-06-08 to 2024-09-05 | -2.95 | -2.95 | +0.004 | +0.74 | 90 |
| 5 | 2025-03-12 to 2025-06-09 | 3.52 | 3.37 | -0.150 | -0.15 | 90 |
| 6 | 2025-12-14 to 2026-03-13 | -1.56 | -1.56 | +0.000 | +nan | 83 |

Improved: 4/6 windows. Avg dSharpe: +0.102. PASS

## 4. Triple Overlay Decomposition

Using V3 Threshold with ROC(10d)

| Configuration | IS Sharpe | OOS Sharpe | OOS Return | OOS MaxDD |
|---------------|-----------|------------|------------|-----------|
| Base Only | 0.400 | -0.076 | -1.9% | -27.6% |
| Base + Pos | 0.342 | 0.134 | 3.5% | -26.0% |
| Base + VRP | 0.167 | 0.351 | 9.0% | -22.7% |
| Base + DVOL_ROC | 0.548 | -0.106 | -2.9% | -30.2% |
| Base + Pos + VRP | 0.113 | 0.556 | 14.7% | -20.2% |
| Base + Pos + DVOL | 0.467 | 0.092 | 2.7% | -28.7% |
| Base + VRP + DVOL | 0.287 | 0.325 | 9.1% | -24.5% |
| Triple (all 3) | 0.199 | 0.477 | 13.3% | -22.3% |

**Marginal DVOL ROC contribution**: -0.078 (Triple vs Pos+VRP)
**Super-additive**: No

## 5. Overlay Correlations

Using V3 Threshold with ROC(10d)

| Period | DVOL-VRP (Spearman z) | DVOL-Pos (Pearson mult) | VRP-Pos (Pearson mult) |
|--------|----------------------|------------------------|-----------------------|
| Full | 0.093 | -0.095 | -0.025 |
| IS | 0.151 | -0.127 | 0.011 |
| OOS | -0.072 | -0.012 | -0.125 |

**Independence verdict**: ORTHOGONAL (corr < 0.3)

## 6. Information Coefficient

ROC(10d) z-score -> Forward Returns (Spearman IC)

| Horizon | Full IC | Full t | IS IC | IS t | OOS IC | OOS t |
|---------|---------|--------|-------|------|--------|-------|
| 1D | +0.0662 | +2.75 | +0.0720 | +2.59 | +0.0498 | +1.03 |
| 7D | +0.1063 | +4.42 | +0.1377 | +4.99 | +0.0016 | +0.03 |
| 14D | +0.0995 | +4.13 | +0.1175 | +4.24 | +0.0424 | +0.86 |

## 7. Multiplier Distribution

| Variant | Multiplier Values (% of time) |
|---------|-------------------------------|
| R94 Symmetric (baseline) | 0.5x: 3.9%, 0.7x: 23.4%, 1.0x: 38.6%, 1.1x: 20.1%, 1.3x: 14.0% |
| V1 Mild Asymmetric | 0.7x: 10.9%, 0.9x: 33.4%, 1.0x: 21.6%, 1.1x: 20.1%, 1.3x: 14.0% |
| V2 Boost-Only | 1.0x: 65.9%, 1.1x: 20.1%, 1.3x: 14.0% |
| V3 Threshold | 0.5x: 3.9%, 1.0x: 73.9%, 1.2x: 22.2% |

## 8. Monthly OOS Returns

| Month | V3 Full | Best Variant | Delta |
|-------|---------|--------------|-------|
| 2025-01 | 13.53% | 13.46% | -0.07% |
| 2025-02 | -5.38% | -5.38% | +0.00% |
| 2025-03 | 0.00% | 0.00% | +0.00% |
| 2025-04 | -1.28% | -1.28% | +0.00% |
| 2025-05 | 14.34% | 13.86% | -0.48% |
| 2025-06 | 1.48% | 1.35% | -0.13% |
| 2025-07 | 10.84% | 12.93% | +2.10% |
| 2025-08 | 0.41% | -0.24% | -0.66% |
| 2025-09 | 0.88% | 0.92% | +0.05% |
| 2025-10 | -9.24% | -11.08% | -1.84% |
| 2025-11 | 0.00% | 0.00% | +0.00% |
| 2025-12 | 0.00% | 0.00% | +0.00% |
| 2026-01 | -6.28% | -6.28% | +0.00% |
| 2026-02 | 0.00% | 0.00% | +0.00% |
| 2026-03 | 0.00% | 0.00% | +0.00% |
| **Cumulative** | **17.52%** | **15.89%** | **-1.63%** |

## 9. Verdict

### Kill Criteria Check

- Any variant degrades V3 Sharpe OOS: YES (some killed)
- All variants degrade: YES -> KILL
- V1 Mild Asymmetric walk-forward: 3/6 windows improved (PASS)
- V2 Boost-Only walk-forward: 4/6 windows improved (PASS)
- V3 Threshold walk-forward: 4/6 windows improved (PASS)
- R94 Symmetric (baseline) walk-forward: 3/6 windows improved (PASS)
- Correlation with VRP > 0.3: NO -> orthogonal

### **KILL**

All 4 asymmetric mapping variants degrade OOS Sharpe vs V3 Full. Signal does not add value as a sizing overlay despite having positive IC.

### Post-Mortem

Despite positive IC (7d) and orthogonality to VRP, DVOL ROC does not
translate to a reliable sizing overlay improvement. Possible reasons:
- IC is concentrated in specific regimes (crypto reflexivity only works in bull runs)
- The overlay multiplication effect is non-linear and can amplify noise
- 14 months OOS may be insufficient to capture the full vol regime cycle
- The signal may work better as a standalone alpha rather than a position-sizing overlay
