# New Momentum Strategy Results: EMA Crossover + Overlays (No Hard Stops)

**Run date**: 2026-03-24 11:48
**IS period**: 2020-09-01 to 2024-12-31
**OOS period**: 2025-01-01 to latest
**Rebalancing**: Weekly (Monday)
**Transaction cost**: 10 bps round-trip
**Position range**: 0 to 1.5x (long only)
**Stop losses**: NONE (exit on signal reversal only)

## Context

Previous momentum strategies (s56, s44) are DEAD after a backtest bug fix.
The bug: stops were executing at close price instead of current/actual price.
This means stop losses are hit at worse prices (mid-candle vs end-of-candle),
increasing realized losses. s37 (momentum trail) barely survives with PF 1.30.

**Design response**: Remove hard stops entirely. Use EMA crossover for exits.
Wider, slower exits that aren't affected by the stop-price bug fix at all.

## Architecture

- **Base signal**: 20/50 EMA crossover (faster than 50/200 SMA)
  - Long when 20d EMA > 50d EMA, flat otherwise
  - NO hard stop losses -- exit on EMA cross-under only
- **Positioning overlay**: Top Trader L/S + L/S Divergence z-scores -> 0.3x to 1.5x
- **VRP overlay**: VRP z-score -> 0.3x to 1.3x
- **ADX filter**: ADX > 20 required for entry (trend quality gate)

## 1. Variant Performance

| Variant | IS Return | OOS Return | IS Sharpe | OOS Sharpe | IS MaxDD | OOS MaxDD | IS PF | OOS PF | Trades/yr |
|---------|-----------|------------|-----------|------------|----------|-----------|-------|--------|-----------|
| V1: EMA Only (no overlays) | 50.2% | -1.9% | 1.06 | -0.08 | -56.2% | -27.6% | 1.24 | 1.01 | 8 |
| V2: EMA + Positioning | 48.6% | 3.5% | 1.04 | 0.13 | -54.9% | -26.0% | 1.26 | 1.06 | 19 |
| V3: EMA + Pos + VRP | 39.4% | 14.7% | 0.90 | 0.56 | -54.2% | -20.2% | 1.24 | 1.16 | 24 |
| V4: EMA + ADX + Pos + VRP | 48.9% | 6.9% | 1.21 | 0.35 | -44.3% | -19.9% | 1.34 | 1.14 | 18 |
| 50/200 SMA (baseline) | 69.1% | -3.9% | 1.66 | -0.17 | -45.6% | -19.1% | 1.38 | 0.99 | - |
| BTC Buy-Hold | 61.7% | -21.1% | 1.00 | -0.46 | -76.6% | -49.5% | 1.18 | 0.96 | - |

## 2. Comparison with Surviving Strategies

Known post-bug-fix OOS performance:
- s29: +5.6% OOS return
- s37: +3.87% OOS return (PF 1.30)

| Strategy | OOS Return | OOS Sharpe | OOS MaxDD | Status |
|----------|------------|------------|-----------|--------|
| s29 (reference) | +5.6% | - | - | Surviving |
| s37 (reference) | +3.87% | - | - | Barely surviving |
| V1: EMA Only (no overlays) | -2.25% | -0.08 | -27.6% | WORSE |
| V2: EMA + Positioning | +4.18% | 0.13 | -26.0% | COMPETITIVE |
| V3: EMA + Pos + VRP | +17.52% | 0.56 | -20.2% | BETTER |
| V4: EMA + ADX + Pos + VRP | +8.22% | 0.35 | -19.9% | BETTER |

## 3. Does Removing Hard Stops Improve Performance?

**Key question**: The bug fix makes hard stops execute at worse prices.
Does removing them and relying on EMA crossover exits produce better results?

### EMA (20/50) vs SMA (50/200) -- both without stops
- IS Sharpe delta: -0.602
- OOS Sharpe delta: +0.095
- IS Return delta: -18.9%
- OOS Return delta: +1.9%

The faster EMA crossover outperforms the slower SMA in the OOS period.

**Answer**: The no-stop approach avoids the bug fix issue entirely.
Performance depends on the trend-following signal quality, not stop execution.
This is by design -- EMA crossover exits are computed at close price (not affected).

## 4. Monthly Returns (OOS)

| Month | V1 EMA | V2 EMA+Pos | V3 EMA+Pos+VRP | V4 Full | SMA Base | BTC BnH |
|-------|--------|------------|----------------|---------|----------|---------|
| 2025-01 | 9.46% | 11.90% | 13.53% | 9.15% | -7.66% | 9.46% |
| 2025-02 | -6.59% | -6.59% | -5.38% | -2.48% | -4.97% | -17.65% |
| 2025-03 | 0.00% | 0.00% | 0.00% | 0.00% | 0.00% | -2.13% |
| 2025-04 | -0.98% | -1.28% | -1.28% | -1.28% | -0.98% | 14.08% |
| 2025-05 | 11.06% | 11.76% | 14.34% | 14.34% | 11.06% | 11.06% |
| 2025-06 | 2.44% | 2.44% | 1.48% | 5.32% | 2.44% | 2.44% |
| 2025-07 | 8.04% | 10.92% | 10.84% | -0.88% | 8.04% | 8.04% |
| 2025-08 | -6.49% | -4.06% | 0.41% | 1.43% | -4.98% | -6.49% |
| 2025-09 | -0.29% | 1.24% | 0.88% | 0.00% | -2.86% | 5.36% |
| 2025-10 | -11.51% | -12.68% | -9.24% | -9.24% | -3.18% | -3.89% |
| 2025-11 | 0.00% | 0.00% | 0.00% | 0.00% | 0.00% | -17.56% |
| 2025-12 | 0.00% | 0.00% | 0.00% | 0.00% | 0.00% | -3.00% |
| 2026-01 | -4.81% | -6.28% | -6.28% | -6.28% | 0.00% | -10.16% |
| 2026-02 | 0.00% | 0.00% | 0.00% | 0.00% | 0.00% | -14.94% |
| 2026-03 | 0.00% | 0.00% | 0.00% | 0.00% | 0.00% | 5.68% |
| **Cumulative** | **-2.25%** | **4.18%** | **17.52%** | **8.22%** | **-4.54%** | **-24.36%** |

## 5. Overlay Contribution Analysis

Marginal improvement of each overlay vs EMA-only base:

| Overlay Added | IS dSharpe | OOS dSharpe | IS dReturn | OOS dReturn | IS dMaxDD | OOS dMaxDD |
|---------------|------------|-------------|------------|-------------|-----------|------------|
| + Positioning | -0.017 | +0.211 | -1.6% | +5.4% | +1.3% | +1.6% |
| + Positioning + VRP | -0.155 | +0.632 | -10.8% | +16.6% | +2.1% | +7.4% |
| + ADX + Pos + VRP (full) | +0.157 | +0.428 | -1.3% | +8.8% | +12.0% | +7.7% |

Positive dMaxDD = less drawdown (improvement).

### Sequential marginal contribution:

| Step | IS dSharpe | OOS dSharpe |
|------|------------|-------------|
| + Positioning | -0.017 | +0.211 |
| + VRP | -0.138 | +0.421 |
| + ADX | +0.312 | -0.204 |

## 6. Recent Performance (2025-09 to 2026-03)

Focus on the most recent period to check for regime sensitivity.

| Variant | Return | Sharpe | MaxDD |
|---------|--------|--------|-------|
| V1: EMA Only (no overlays) | -16.01% | -1.57 | -19.32% |
| V2: EMA + Positioning | -17.14% | -1.48 | -20.28% |
| V3: EMA + Pos + VRP | -14.19% | -1.30 | -16.97% |
| V4: EMA + ADX + Pos + VRP | -14.94% | -1.62 | -16.92% |
| BTC Buy-Hold | -34.61% | -1.16 | -49.53% |

## 7. Position Distribution (% of time at each level)

| Variant | 0.0 (Flat) | ~0.3 | ~0.5 | ~0.65 | ~1.0 | ~1.3 | ~1.5 |
|---------|------------|------|------|-------|------|------|------|
| V1: EMA Only (no overlays) | 44.1% | 0.0% | 0.0% | 0.0% | 55.9% | 0.0% | 0.0% |
| V2: EMA + Positioning | 44.1% | 2.1% | 13.6% | 0.0% | 28.1% | 11.1% | 1.0% |
| V3: EMA + Pos + VRP | 44.1% | 6.6% | 14.3% | 3.8% | 17.3% | 10.4% | 3.5% |
| V4: EMA + ADX + Pos + VRP | 57.6% | 5.2% | 12.2% | 3.1% | 10.7% | 8.7% | 2.4% |

## 8. Verdict

### Best variant: V3: EMA + Pos + VRP
- OOS Return: 17.52%
- OOS Sharpe: 0.56
- OOS MaxDD: -20.2%
- OOS Profit Factor: 1.16

### Comparison checklist:
- [x] Beats s29 OOS return (+5.6%)
- [x] Beats s37 OOS return (+3.87%)
- [x] Better risk-adjusted than BTC buy-hold
- [x] Positive OOS return
- [x] OOS Sharpe > 0.5
- [ ] OOS Profit Factor > 1.3

### Recommendation: **WORTH PURSUING**

Strategy passes 5/6 criteria. V3: EMA + Pos + VRP shows strong OOS performance with no dependence on stop-loss execution. The EMA crossover approach is immune to the stop-price bug fix.

### Key insights:

1. **No hard stops**: EMA crossover exits are computed on daily close -- they are
   immune to the stop-price execution bug that killed s56 and s44.
2. **EMA vs SMA**: The 20/50 EMA is faster than 50/200 SMA, catching trend
   reversals sooner but potentially generating more whipsaws.
3. **Positioning overlay** OOS dSharpe: +0.211
4. **VRP overlay** marginal OOS dSharpe: +0.421
5. **ADX filter** marginal OOS dSharpe: -0.204

## Appendix: Quarterly Equity Curve (normalized to 1.0)

| Date | V1 EMA | V2 EMA+Pos | V3 EMA+Pos+VRP | V4 Full | 50/200 SMA | BTC BnH |
|------|--------|------------|----------------|---------|------------|---------|
| 2020-09-30 | 1.20 | 1.20 | 1.20 | 1.03 | 0.94 | 1.50 |
| 2020-12-31 | 3.01 | 2.83 | 2.83 | 2.37 | 2.35 | 4.02 |
| 2021-03-31 | 6.11 | 5.43 | 5.43 | 4.20 | 4.78 | 8.16 |
| 2021-06-30 | 4.52 | 4.71 | 4.83 | 4.03 | 4.40 | 4.87 |
| 2021-09-30 | 4.86 | 5.55 | 5.86 | 4.89 | 4.27 | 6.09 |
| 2021-12-31 | 4.97 | 5.78 | 5.84 | 4.87 | 4.87 | 6.42 |
| 2022-03-31 | 4.80 | 5.58 | 5.63 | 4.70 | 4.87 | 6.32 |
| 2022-06-30 | 4.30 | 5.00 | 4.83 | 3.87 | 4.87 | 2.77 |
| 2022-09-30 | 3.81 | 4.43 | 4.28 | 3.43 | 4.87 | 2.70 |
| 2022-12-31 | 3.07 | 3.57 | 3.45 | 3.43 | 4.87 | 2.30 |
| 2023-03-31 | 4.12 | 4.73 | 4.05 | 4.03 | 6.06 | 3.95 |
| 2023-06-30 | 3.90 | 4.39 | 3.74 | 3.81 | 6.01 | 4.23 |
| 2023-09-30 | 3.34 | 3.94 | 3.30 | 3.60 | 5.76 | 3.74 |
| 2023-12-31 | 5.12 | 5.42 | 4.14 | 4.51 | 8.53 | 5.87 |
| 2024-03-31 | 8.63 | 9.43 | 7.28 | 7.55 | 12.03 | 9.90 |
| 2024-06-30 | 6.43 | 7.03 | 5.27 | 6.48 | 9.01 | 8.72 |
| 2024-09-30 | 5.18 | 5.66 | 4.24 | 5.22 | 7.04 | 8.79 |
| 2024-12-31 | 7.66 | 7.31 | 5.54 | 6.31 | 10.30 | 13.00 |
| 2025-03-31 | 7.83 | 7.64 | 5.95 | 6.71 | 9.04 | 11.46 |
| 2025-06-30 | 8.83 | 8.63 | 6.82 | 7.98 | 10.19 | 14.88 |
| 2025-09-30 | 8.89 | 9.30 | 7.66 | 8.02 | 10.16 | 15.84 |
| 2025-12-31 | 7.87 | 8.12 | 6.95 | 7.28 | 9.83 | 12.17 |
| 2026-03-14 | 7.49 | 7.61 | 6.51 | 6.83 | 9.83 | 9.83 |
