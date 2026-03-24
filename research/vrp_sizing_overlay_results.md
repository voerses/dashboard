# VRP Sizing Overlay Results

**Run date**: 2026-03-24 11:31
**IS period**: 2021-03-24 to 2024-12-31
**OOS period**: 2025-01-01 to latest
**DVOL source**: Deribit BTC DVOL
**Rebalancing**: Weekly (Monday)
**Transaction cost**: 10 bps round-trip

## Architecture

- **Base**: Trend following (50/200 SMA crossover with hysteresis)
- **Positioning overlay**: Top Trader L/S + L/S Divergence z-score -> 0.3x to 1.5x
- **VRP overlay**: VRP z-score -> 0.3x to 1.3x
  - VRP = Implied Vol (DVOL) - Realized Vol (20d)
  - High VRP z > 1: vol overpriced, market complacent -> 1.3x
  - Normal -0.5 < z < 1 -> 1.0x
  - Low VRP z < -0.5: vol cheap, turbulence expected -> 0.5x
  - Very low z < -1.5: extreme -> 0.3x

## 1. Variant Performance Table

| Variant | IS Return | OOS Return | IS Sharpe | OOS Sharpe | IS MaxDD | OOS MaxDD | IS Calmar | OOS Calmar |
|---------|-----------|------------|-----------|------------|----------|-----------|-----------|------------|
| Base Only (Trend) | 25.1% | -3.9% | 0.74 | -0.17 | -45.6% | -19.1% | 0.55 | -0.20 |
| Base + Positioning | 19.8% | 4.3% | 0.65 | 0.19 | -43.8% | -16.7% | 0.45 | 0.26 |
| Base + VRP | 16.9% | 4.7% | 0.52 | 0.21 | -48.6% | -13.6% | 0.35 | 0.35 |
| Base + Positioning + VRP | 11.6% | 15.9% | 0.41 | 0.68 | -45.4% | -11.9% | 0.26 | 1.34 |

## 2. VRP Contribution vs Positioning (Marginal Analysis)

### vs Base Only

| Overlay | IS dSharpe | OOS dSharpe | IS dReturn | OOS dReturn | IS dMaxDD | OOS dMaxDD |
|---------|------------|-------------|------------|-------------|-----------|------------|
| + Positioning | -0.091 | +0.359 | -5.3% | +8.2% | +1.8% | +2.5% |
| + VRP | -0.222 | +0.380 | -8.2% | +8.6% | -3.0% | +5.5% |
| + Positioning + VRP | -0.336 | +0.856 | -13.5% | +19.8% | +0.2% | +7.2% |

### Marginal VRP contribution (on top of positioning)

- IS dSharpe: -0.245
- OOS dSharpe: +0.498
- IS dReturn: -8.2%
- OOS dReturn: +11.6%
- IS dMaxDD: -1.6%
- OOS dMaxDD: +4.8%

## 3. Correlation Between VRP and Positioning Overlays

Low correlation = independent signals = diversification benefit.

### Full period
- Multiplier correlation: -0.032
- Z-score Spearman correlation: 0.031 (p=0.2059)
- Both reduce: 7.3%
- Both boost: 4.6%
- Disagree: 13.1%
- Both neutral: 21.7%

### IS period
- Multiplier correlation: -0.000
- Z-score Spearman correlation: -0.003

### OOS period
- Multiplier correlation: -0.125
- Z-score Spearman correlation: 0.123

**Independence verdict**: INDEPENDENT

## VRP Information Coefficient

Does VRP z-score predict forward volatility (its intended use) and/or returns?

| Target | IS IC | IS t-stat | OOS IC | OOS t-stat |
|--------|-------|-----------|--------|------------|
| Fwd 7D RV | -0.0711 | -2.62 | -0.1361 | -2.82 |
| Fwd 7D Return | +0.0018 | +0.07 | +0.1996 | +4.18 |
| Fwd 1D Return | +0.0189 | +0.69 | +0.0829 | +1.71 |

## VRP Regime Performance (OOS)

| VRP Regime | Days | Base Ann Return | VRP Ann Return | Base Vol | VRP Vol |
|------------|------|-----------------|----------------|----------|---------|
| z > 1 (complacent) | 79 | +30.9% | +33.4% | 17.3% | 18.8% |
| -0.5 < z <= 1 (normal) | 212 | +4.3% | +9.1% | 23.1% | 24.9% |
| -1.5 < z <= -0.5 (caution) | 85 | -20.8% | +2.5% | 23.0% | 19.3% |
| z <= -1.5 (extreme) | 55 | -39.9% | -31.2% | 26.5% | 22.8% |

## 4. Monthly Returns (OOS) -- All Variants

| Month | Base Only | Base+Pos | Base+VRP | Base+Pos+VRP |
|-------|-----------|----------|----------|--------------|
| 2025-01 | -7.66% | -7.66% | -7.66% | -7.66% |
| 2025-02 | -4.97% | -4.97% | -3.07% | -3.07% |
| 2025-03 | 0.00% | 0.00% | 0.00% | 0.00% |
| 2025-04 | -0.98% | -1.28% | -0.98% | -1.28% |
| 2025-05 | 11.06% | 11.76% | 14.53% | 15.97% |
| 2025-06 | 2.44% | 2.44% | 1.48% | 1.48% |
| 2025-07 | 8.04% | 10.92% | 7.46% | 10.84% |
| 2025-08 | -4.98% | -2.51% | -1.49% | 1.41% |
| 2025-09 | -2.86% | -1.75% | -2.86% | -1.75% |
| 2025-10 | -3.18% | -0.24% | -0.33% | 3.68% |
| 2025-11 | 0.00% | 0.00% | 0.00% | 0.00% |
| 2025-12 | 0.00% | 0.00% | 0.00% | 0.00% |
| 2026-01 | 0.00% | 0.00% | 0.00% | 0.00% |
| 2026-02 | 0.00% | 0.00% | 0.00% | 0.00% |
| 2026-03 | 0.00% | 0.00% | 0.00% | 0.00% |
| **Cumulative** | **-4.54%** | **5.12%** | **5.57%** | **19.07%** |

## 5. Verdict: Does VRP Add On Top of Positioning?

### Key Numbers

- Positioning standalone OOS dSharpe: +0.359
- VRP standalone OOS dSharpe: +0.380
- VRP marginal OOS dSharpe (on top of positioning): +0.498
- Overlay correlation (Spearman z-scores): 0.031
- VRP marginal t-stat: 1.890

### **YES -- VRP adds independent value**

VRP provides a marginal OOS Sharpe improvement of +0.498 on top of positioning. The overlays are independent (Spearman rho=0.031), confirming VRP captures different information (volatility regime vs. crowd positioning). The full stack (Base + Positioning + VRP) achieves OOS Sharpe of 0.68 vs -0.17 for base only.

### Statistical Significance

- OOS period: 431 days (~14 months)
- Positioning vs Base: t=1.462 (not significant)
- VRP vs Base: t=1.740 (marginally significant (p<0.10))
- Full vs Base: t=2.332 (significant (p<0.05))

- VRP marginal (Pos+VRP vs Pos): t=1.890

### Recommendations

1. **Adopt full stack** (Base + Positioning + VRP) as production configuration
2. VRP and Positioning capture different information -- both add value
3. Continue accumulating DVOL data for more robust VRP estimation

## Appendix: VRP Summary Statistics

- VRP mean: 9.3
- VRP median: 10.3
- VRP std: 13.6
- VRP min: -46.9
- VRP max: 45.3
- VRP % positive: 78.3%
- RV 20d mean: 56.3%
- IV mean: 61.9%

## Appendix: Quarterly Equity Curve (normalized to 1.0)

| Date | Base Only | Base+Pos | Base+VRP | Base+Pos+VRP |
|------|-----------|----------|----------|--------------|
| 2021-03-31 | 4.78 | 4.24 | 4.78 | 4.24 |
| 2021-06-30 | 4.40 | 4.06 | 4.47 | 4.12 |
| 2021-09-30 | 4.27 | 4.22 | 4.10 | 4.17 |
| 2021-12-31 | 4.87 | 4.56 | 4.16 | 4.16 |
| 2022-03-31 | 4.87 | 4.56 | 4.16 | 4.16 |
| 2022-06-30 | 4.87 | 4.56 | 4.16 | 4.16 |
| 2022-09-30 | 4.87 | 4.56 | 4.16 | 4.16 |
| 2022-12-31 | 4.87 | 4.56 | 4.16 | 4.16 |
| 2023-03-31 | 6.06 | 5.50 | 4.86 | 4.64 |
| 2023-06-30 | 6.01 | 5.34 | 4.77 | 4.44 |
| 2023-09-30 | 5.76 | 5.04 | 4.58 | 4.18 |
| 2023-12-31 | 8.53 | 6.83 | 6.33 | 5.16 |
| 2024-03-31 | 12.03 | 10.17 | 9.15 | 7.83 |
| 2024-06-30 | 9.01 | 7.64 | 6.66 | 5.71 |
| 2024-09-30 | 7.04 | 5.97 | 5.26 | 4.52 |
| 2024-12-31 | 10.30 | 7.64 | 7.97 | 5.84 |
| 2025-03-31 | 9.04 | 6.70 | 7.13 | 5.23 |
| 2025-06-30 | 10.19 | 7.58 | 8.21 | 6.08 |
| 2025-09-30 | 10.16 | 8.05 | 8.44 | 6.71 |
| 2025-12-31 | 9.83 | 8.03 | 8.41 | 6.96 |
| 2026-03-14 | 9.83 | 8.03 | 8.41 | 6.96 |
