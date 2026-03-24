# R68: V3 Momentum Strategy — Regime Analysis Results

**Run date**: 2026-03-24 12:01
**IS period**: 2020-09-01 to 2024-12-31
**OOS period**: 2025-01-01 to latest data (2026-03-14)
**Rebalancing**: Weekly (Monday)
**Transaction cost**: 10 bps round-trip

## Strategy Definition

- **V1 (Base)**: Long when 20d EMA > 50d EMA, flat otherwise. No stop losses.
- **V3 (Full)**: V1 + Positioning overlay + VRP overlay
  - Positioning: Binance Top Trader L/S + L/S Divergence combined z-score (30d rolling)
    - z>1.5 -> 0.3x, z>0.5 -> 0.5x, neutral -> 1.0x, z<-0.5 -> 1.3x, z<-1.5 -> 1.5x
  - VRP: (IV - RV) z-score over 60d
    - z>1 -> 1.3x, z>-0.5 -> 1.0x, z>-1.5 -> 0.5x, z<-1.5 -> 0.3x
  - Position range: [0, 1.5x]. Weekly rebalancing.

## Regime Definition

- **UPTREND**: 50d SMA > 200d SMA AND close > 50d SMA
- **DOWNTREND**: 50d SMA < 200d SMA AND close < 50d SMA
- **RANGE**: 50d SMA and 200d SMA within 5% OR close between the two SMAs
- **CRISIS**: 50d return < -20% (overrides other regimes)

---

## 1. Overall Performance Summary

| Strategy | Period | Ann Return | Sharpe | MaxDD | Calmar | Profit Factor | Days |
|----------|--------|------------|--------|-------|--------|---------------|------|
| BH | IS | 61.7% | 1.00 | -76.6% | 0.80 | 1.18 | 1583 |
| V1 | IS | 50.2% | 1.06 | -56.2% | 0.89 | 1.24 | 1583 |
| V3 | IS | 39.4% | 0.90 | -54.2% | 0.73 | 1.24 | 1583 |
| BH | OOS | -21.1% | -0.46 | -49.5% | -0.43 | 0.96 | 431 |
| V1 | OOS | -1.9% | -0.08 | -27.6% | -0.07 | 1.01 | 431 |
| V3 | OOS | 14.7% | 0.56 | -20.2% | 0.72 | 1.16 | 431 |
| BH | FULL | 38.7% | 0.66 | -76.6% | 0.50 | 1.14 | 2014 |
| V1 | FULL | 37.1% | 0.85 | -56.2% | 0.66 | 1.21 | 2014 |
| V3 | FULL | 33.7% | 0.83 | -54.2% | 0.62 | 1.23 | 2014 |

### Overlay Alpha (V3 - V1)

| Period | dReturn | dSharpe | dMaxDD |
|--------|---------|---------|--------|
| IS | -10.8% | -0.155 | +2.1% |
| OOS | +16.6% | +0.632 | +7.4% |
| FULL | -3.4% | -0.020 | +2.1% |

---

## 2. Regime-Specific Performance

### Regime Distribution

| Regime | IS Days | IS % | OOS Days | OOS % | Total Days | Total % |
|--------|---------|------|----------|-------|------------|---------|
| UPTREND | 634 | 40% | 135 | 31% | 769 | 38% |
| DOWNTREND | 243 | 15% | 55 | 13% | 298 | 15% |
| RANGE | 521 | 33% | 189 | 44% | 710 | 35% |
| CRISIS | 185 | 12% | 52 | 12% | 237 | 12% |

### IS Regime Performance

| Regime | V1 AnnRet | V1 Sharpe | V1 MaxDD | V3 AnnRet | V3 Sharpe | V3 MaxDD | V3 PF | Overlay Alpha (dSharpe) | Days |
|--------|-----------|-----------|----------|-----------|-----------|----------|-------|-------------------------|------|
| UPTREND | 574.2% | 9.58 | -25.2% | 353.2% | 6.42 | -30.8% | 1.69 | -3.159 | 634 |
| DOWNTREND | -46.6% | -1.52 | -34.9% | -46.6% | -1.47 | -34.4% | 0.41 | +0.048 | 243 |
| RANGE | -49.7% | -1.16 | -64.5% | -40.0% | -1.01 | -59.9% | 0.79 | +0.147 | 521 |
| CRISIS | -25.9% | -1.77 | -8.3% | -7.0% | -1.92 | -2.1% | 0.00 | -0.149 | 185 |

### OOS Regime Performance

| Regime | V1 AnnRet | V1 Sharpe | V1 MaxDD | V3 AnnRet | V3 Sharpe | V3 MaxDD | V3 PF | Overlay Alpha (dSharpe) | Days |
|--------|-----------|-----------|----------|-----------|-----------|----------|-------|-------------------------|------|
| UPTREND | 146.0% | 4.58 | -9.1% | 193.1% | 6.02 | -8.9% | 1.78 | +1.434 | 135 |
| DOWNTREND | -27.4% | -1.83 | -6.4% | -34.4% | -1.76 | -8.3% | 0.40 | +0.065 | 55 |
| RANGE | -44.8% | -1.80 | -29.8% | -28.4% | -1.06 | -21.6% | 0.76 | +0.741 | 189 |
| CRISIS | 0.0% | 0.00 | 0.0% | 0.0% | 0.00 | 0.0% | inf | +0.000 | 52 |

### FULL Regime Performance

| Regime | V1 AnnRet | V1 Sharpe | V1 MaxDD | V3 AnnRet | V3 Sharpe | V3 MaxDD | V3 PF | Overlay Alpha (dSharpe) | Days |
|--------|-----------|-----------|----------|-----------|-----------|----------|-------|-------------------------|------|
| UPTREND | 464.8% | 8.29 | -25.2% | 319.9% | 6.18 | -30.8% | 1.70 | -2.107 | 769 |
| DOWNTREND | -43.5% | -1.53 | -38.4% | -44.6% | -1.49 | -39.7% | 0.41 | +0.035 | 298 |
| RANGE | -48.4% | -1.24 | -71.5% | -37.1% | -1.01 | -62.0% | 0.78 | +0.231 | 710 |
| CRISIS | -20.9% | -1.61 | -8.3% | -5.5% | -1.71 | -2.1% | 0.00 | -0.095 | 237 |

---

## 3. Regime Transition Analysis

10-day return after each regime transition:

| Transition | Count | V1 Avg 10d | V3 Avg 10d | BH Avg 10d | V3 Better? |
|------------|-------|------------|------------|------------|------------|
| RANGE -> CRISIS | 11 | -1.10% | -0.28% | -5.86% | YES |
| CRISIS -> RANGE | 10 | 0.25% | 0.06% | 0.95% | NO |

### Full Transition Count Matrix

| From \\ To | CRISIS | DOWNTREND | RANGE | UPTREND |
|---|---|---|---|---|
| CRISIS | 0 | 16 | 10 | 0 |
| DOWNTREND | 16 | 0 | 22 | 0 |
| RANGE | 11 | 22 | 0 | 32 |
| UPTREND | 0 | 0 | 32 | 0 |

---

## 4. Position Sizing Distribution Per Regime

V3 held position statistics by regime (full period):

| Regime | Mean Pos | Median Pos | Std | % Flat | % Reduced (<0.9) | % Full (0.9-1.1) | % Levered (>1.1) |
|--------|----------|------------|-----|--------|-------------------|-------------------|------------------|
| UPTREND | 0.768 | 1.000 | 0.405 | 5.7% | 43.4% | 33.4% | 17.4% |
| DOWNTREND | 0.100 | 0.000 | 0.329 | 91.3% | 0.7% | 3.0% | 5.0% |
| RANGE | 0.465 | 0.250 | 0.534 | 47.3% | 22.5% | 11.7% | 18.5% |
| CRISIS | 0.001 | 0.000 | 0.016 | 99.6% | 0.4% | 0.0% | 0.0% |

**Expected behavior**: Overlays should reduce position in CRISIS/DOWNTREND and maintain/boost in UPTREND.

---

## 5. Drawdown Attribution

### V3 Top 5 Drawdowns

| # | Depth | Start | Trough | End | Duration | Dominant Regime | Avg V3 Pos | V1 DD | BH DD | Overlay Helped? |
|---|-------|-------|--------|-----|----------|-----------------|------------|-------|-------|-----------------|
| 1 | -54.2% | 2021-11-09 | 2023-10-12 | 2024-03-11 | 853d | RANGE | 0.30 | -55.9% | -76.4% | YES |
| 2 | -44.8% | 2024-04-09 | 2024-10-10 | 2025-07-10 | 457d | RANGE | 0.47 | -42.2% | -28.1% | NO |
| 3 | -30.8% | 2021-01-09 | 2021-01-27 | 2021-02-08 | 30d | UPTREND | 0.97 | -24.3% | -24.3% | NO |
| 4 | -22.7% | 2021-04-14 | 2021-04-25 | 2021-08-07 | 115d | RANGE | 1.08 | -32.9% | -52.8% | YES |
| 5 | -20.2% | 2025-08-14 | 2026-01-25 | 2026-03-14 (ongoing) | 212d | RANGE | 0.24 | -24.5% | -49.5% | YES |

*Overlay Helped = V3 drawdown shallower than V1 (YES means V3 depth > V1 depth, i.e., less negative)*

### Drawdown Narratives

**DD #1: -54.2%** (2021-11-09 to 2023-10-12)

- BTC price: $66,948 -> $26,760 (-60.0%)
- Regime breakdown: RANGE: 251, DOWNTREND: 191, CRISIS: 133, UPTREND: 128
- Average V3 position: 0.30
- V1 drawdown: -55.9%, BH drawdown: -76.4%
- Positioning z: avg=-0.03
- VRP z: avg=-0.17
- **Overlay HELPED**: V3 drawdown (-54.2%) shallower than V1 (-55.9%)

**DD #2: -44.8%** (2024-04-09 to 2024-10-10)

- BTC price: $69,146 -> $60,326 (-12.8%)
- Regime breakdown: RANGE: 95, UPTREND: 56, DOWNTREND: 34
- Average V3 position: 0.47
- V1 drawdown: -42.2%, BH drawdown: -28.1%
- Positioning z: avg=-0.08
- VRP z: avg=0.09
- **Overlay HURT**: V3 drawdown (-44.8%) deeper than V1 (-42.2%)

**DD #3: -30.8%** (2021-01-09 to 2021-01-27)

- BTC price: $40,088 -> $30,366 (-24.3%)
- Regime breakdown: UPTREND: 19
- Average V3 position: 0.97
- V1 drawdown: -24.3%, BH drawdown: -24.3%
- Positioning z: avg=-0.18
- **Overlay HURT**: V3 drawdown (-30.8%) deeper than V1 (-24.3%)

---

## 6. Calendar Analysis

### V3 Monthly Return Heatmap (%)

| Year | Jan | Feb | Mar | Apr | May | Jun | Jul | Aug | Sep | Oct | Nov | Dec | Annual |
|------|------|------|------|------|------|------|------|------|------|------|------|------|------|
| 2020 | -- | -- | -- | -- | -- | -- | -- | -- | -8.5% | +21.4% | +42.0% | +36.7% | **+91.6%** |
| 2021 | +11.4% | +27.0% | +35.6% | -4.1% | -7.1% | +0.0% | +0.0% | +24.2% | -2.4% | +9.9% | -6.6% | -2.9% | **+84.9%** |
| 2022 | +0.0% | +0.0% | -3.5% | -14.2% | +0.0% | +0.0% | +0.0% | -11.4% | +0.0% | +0.0% | -19.4% | +0.0% | **-48.5%** |
| 2023 | +2.1% | +1.8% | +13.0% | +2.1% | -9.7% | +0.3% | -5.6% | -6.6% | +0.0% | +11.2% | +5.9% | +6.6% | **+21.0%** |
| 2024 | +2.3% | +43.5% | +19.7% | -16.3% | +2.9% | -15.9% | -3.3% | -16.5% | -0.2% | +11.9% | +19.1% | -2.0% | **+45.1%** |
| 2025 | +13.5% | -5.4% | +0.0% | -1.3% | +14.3% | +1.5% | +10.8% | +0.4% | +0.9% | -9.2% | +0.0% | +0.0% | **+25.6%** |
| 2026 | -6.3% | +0.0% | +0.0% | -- | -- | -- | -- | -- | -- | -- | -- | -- | **-6.3%** |

### Monthly Averages and Win Rates

| Month | Avg Return | Std Dev | Win Rate |
|-------|------------|---------|----------|
| Jan | +3.8% | 7.4% | 57% |
| Feb | +11.1% | 19.5% | 43% |
| Mar | +10.8% | 15.1% | 43% |
| Apr | -6.8% | 8.1% | 14% |
| May | +0.1% | 9.5% | 29% |
| Jun | -2.8% | 7.4% | 29% |
| Jul | +0.4% | 6.3% | 14% |
| Aug | -2.0% | 15.9% | 29% |
| Sep | -1.7% | 3.5% | 14% |
| Oct | +7.5% | 10.7% | 57% |
| Nov | +6.8% | 21.5% | 43% |
| Dec | +6.4% | 15.2% | 29% |

### Quarterly Performance

| Quarter | Avg Return | Std Dev | Win Rate | N |
|---------|------------|---------|----------|---|
| Q1 | +30.4% | 42.5% | 67% | 6 |
| Q2 | -9.2% | 15.3% | 20% | 5 |
| Q3 | -2.9% | 15.9% | 33% | 6 |
| Q4 | +27.1% | 56.6% | 50% | 6 |

### Seasonality Summary

**Best months**: Feb, Mar, Oct (avg: +11.1%, +10.8%, +7.5%)
**Worst months**: Apr, Jun, Aug (avg: -6.8%, -2.8%, -2.0%)

---

## 7. What Kills V3?

### Maximum Consecutive Losing Weeks

- **4 weeks** ending around 2021-10-04

### Worst 10 Weekly Returns

| Week | Return | Regime | V3 Position | BTC Weekly |
|------|--------|--------|-------------|------------|
| 2022-11-14 | -19.37% | DOWNTREND | 0.00 | 4.92% |
| 2022-04-11 | -19.35% | DOWNTREND | 1.30 | -8.55% |
| 2024-08-05 | -19.20% | RANGE | 0.00 | -17.38% |
| 2021-01-25 | -15.68% | UPTREND | 1.30 | -8.31% |
| 2021-11-22 | -14.94% | RANGE | 1.30 | -6.70% |
| 2024-06-24 | -12.14% | RANGE | 0.00 | -7.35% |
| 2024-04-15 | -11.48% | RANGE | 1.30 | -10.46% |
| 2022-08-22 | -11.27% | DOWNTREND | 0.00 | -8.11% |
| 2020-09-07 | -10.95% | RANGE | 1.00 | -8.60% |
| 2023-04-24 | -9.85% | UPTREND | 1.30 | -4.46% |

### Worst 30-Day Rolling Return Periods

| End Date | 30d Return | Dominant Regime | Avg Position |
|----------|------------|-----------------|--------------|
| 2022-11-09 | -24.18% | DOWNTREND | 0.10 |
| 2022-04-11 | -21.98% | RANGE | 0.56 |
| 2022-04-17 | -21.35% | RANGE | 0.81 |
| 2022-11-13 | -21.03% | DOWNTREND | 0.23 |
| 2024-08-05 | -20.83% | UPTREND | 0.23 |

### Vulnerability Analysis

**Regime whipsaw** (>4 transitions in 30 days):
- Whipsaw periods: 328 days, Sharpe=1.95, AnnRet=40.5%
- Non-whipsaw periods: Sharpe=0.75, AnnRet=32.4%

**EMA whipsaw** (>2 EMA 20/50 crosses in 30 days):
- 25 days, Sharpe=-2.05, AnnRet=-85.0%

### Kill Scenarios (When V3 Loses Money)

Based on the analysis above, V3 is most vulnerable to:

1. **RANGE regime**: IS Sharpe=-1.01. The EMA crossover generates false signals in range-bound markets.
1. **RANGE regime (OOS)**: Sharpe=-1.06. Choppy markets cause whipsaw losses.

---

## 8. Actionable Summary

### Strategy Health

| Metric | V1 IS | V1 OOS | V3 IS | V3 OOS |
|--------|-------|--------|-------|--------|
| Ann Return | 50.2% | -1.9% | 39.4% | 14.7% |
| Sharpe | 1.06 | -0.08 | 0.90 | 0.56 |
| MaxDD | -56.2% | -27.6% | -54.2% | -20.2% |

### Regimes to Worry About

- **DOWNTREND**: Sharpe=-1.49, AnnRet=-44.6% -- V3 underperforms here
- **RANGE**: Sharpe=-1.01, AnnRet=-37.1% -- V3 underperforms here
- **CRISIS**: Sharpe=-1.71, AnnRet=-5.5% -- V3 underperforms here

### Does the Overlay Help?

**Overlay HELPS in:**
- RANGE: +0.231 Sharpe improvement

**Overlay HURTS in:**
- UPTREND: -2.107 Sharpe degradation
- CRISIS: -0.095 Sharpe degradation

### Kill Signals (When to Reduce/Stop V3)

Based on this analysis, consider reducing V3 exposure when:

1. **Regime enters RANGE**: SMAs converging with no clear trend. V3 Sharpe in RANGE = -1.01. Consider reducing position to 0.5x.
2. **50d return drops below -15%**: Approaching CRISIS territory. V3 Sharpe in CRISIS = -1.71. The EMA signal is a lagging indicator.
3. **>4 regime transitions in 30 days**: Whipsaw indicator. V3 Sharpe during whipsaw = 1.95.
4. **>2 EMA crosses in 30 days**: EMA whipsaw indicator. V3 Sharpe during EMA whipsaw = -2.05.
5. **Max consecutive losing weeks hits 2+**: Historical max is 4 weeks. Exceeding this may indicate regime shift.

### Bottom Line

**WARNING**: Significant IS-to-OOS degradation in Sharpe (0.90 -> 0.56). This suggests possible overfitting of overlay parameters.

Main risk: **DOWNTREND** regime accounts for the bulk of losses. The EMA crossover's main weakness is response time -- it enters late and exits late, which the positioning/VRP overlays can partially compensate for but not fully eliminate.
