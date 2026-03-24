# R105 -- V3 Momentum vs Trend+Pullback Correlation Analysis

**Date**: 2026-03-24
**Period**: 2021-01-01 to 2026-03-14
**Asset**: BTC spot

## Strategy Definitions

### V3 Momentum (s320)
- Signal: Long when daily EMA(20) > EMA(50), flat otherwise
- Rebalance: Weekly (end of Monday)
- No overlays for this comparison (binary position only)

### Trend+Pullback
- Signal: Long when daily EMA(20) > EMA(50) AND 4h RSI(14) crosses UP through 40
- Exit: 2x ATR(14) trailing stop on 4h bars OR 168h max hold
- No overlays

## 1. Correlation of Daily Returns

| Metric | Value |
|--------|-------|
| Pearson r (all days) | **0.6883** |
| Pearson p-value | 8.89e-266 |
| Spearman r (all days) | **0.6739** |
| Spearman p-value | 9.53e-251 |
| Pearson r (active days only) | 0.6882 |
| Spearman r (active days only) | 0.6438 |
| Total days | 1892 |
| Active days (at least 1 in trade) | 1053 |

> **Interpretation**: HIGH correlation -- strategies are substantially overlapping

### Rolling 90-Day Correlation

| Statistic | Value |
|-----------|-------|
| Mean | 0.6795 |
| Median | 0.7004 |
| Std Dev | 0.1574 |
| Min | 0.0581 |
| Max | 0.9692 |
| % of windows < 0.3 | 0.8% |
| % of windows < 0.0 | 0.0% |

#### Yearly Rolling Correlation Summary

| Year | Mean Corr | Min | Max |
|------|----------|-----|-----|
| 2021 | 0.782 | 0.544 | 0.950 |
| 2022 | 0.695 | 0.058 | 0.969 |
| 2023 | 0.624 | 0.124 | 0.893 |
| 2024 | 0.605 | 0.239 | 0.860 |
| 2025 | 0.722 | 0.368 | 0.863 |
| 2026 | 0.602 | 0.374 | 0.932 |

### Regime-Specific Correlation

| Regime | Pearson r | p-value | N days |
|--------|----------|---------|--------|
| UPTREND | 0.7279 | 4.38e-134 | 807 |
| DOWNTREND | 0.4583 | 4.28e-34 | 631 |
| RANGE | 0.6716 | 7.40e-61 | 454 |

Regime distribution:
- UPTREND: 807 days (42.7%)
- DOWNTREND: 631 days (33.4%)
- RANGE: 454 days (24.0%)

## 2. Entry Signal Overlap

### Position Overlap (daily)

| State | Days | % |
|-------|------|---|
| Both in trade | 489 | 25.8% |
| V3 only (T+P flat) | 554 | 29.3% |
| T+P only (V3 flat) | 10 | 0.5% |
| Both flat | 839 | 44.3% |

- V3 in trade: 1043 days (55.1%)
- T+P in trade: 499 days (26.4%)
- **T+P overlap with V3**: 98.0% of T+P in-trade days overlap with V3

### Entry-Level Analysis

- Total T+P entry signals: **152**
- When T+P fires entry, V3 is already long: **149/152** (98.0%)
- When V3 is flat, T+P fires: **10** days out of 849 V3-flat days

> **Key Finding**: T+P almost always fires when V3 is already long. This is expected since both require EMA20 > EMA50. T+P is a *subset* of V3 -- it picks specific *entry moments* within the broader V3 uptrend.

## 3. Portfolio Simulation

| Portfolio | Ann. Return | Ann. Vol | Sharpe | Max DD | Calmar | Total Return |
|-----------|-----------|---------|--------|--------|--------|-------------|
| Buy & Hold | 34.23% | 58.75% | **0.583** | -76.63% | 0.45 | 141.31% |
| V3 Momentum | 29.98% | 43.05% | **0.696** | -58.62% | 0.51 | 193.43% |
| Trend+Pullback | 11.73% | 30.07% | **0.390** | -43.98% | 0.27 | 45.29% |
| 50/50 Equal Weight | 20.85% | 33.69% | **0.619** | -46.73% | 0.45 | 119.69% |
| Risk Parity | 19.47% | 32.43% | **0.600** | -42.76% | 0.46 | 108.92% |

Risk parity average weights: V3=42.1%, T+P=57.9%

### Portfolio Improvement vs V3 Alone

| Metric | V3 Only | Equal Weight | Risk Parity |
|--------|---------|-------------|-------------|
| Sharpe | 0.696 | 0.619 (-11.1%) | 0.600 (-13.8%) |
| Max DD | -58.62% | -46.73% | -42.76% |
| Calmar | 0.51 | 0.45 | 0.46 |

## 4. Conditional Analysis

### When V3 Is Losing (30-day rolling return < 0)

- V3 losing periods: 706 days
- V3 mean daily return during loss: -0.3540%
- T+P mean daily return during V3 loss: -0.2125%
- T+P cumulative return during V3 loss periods: -149.99%
- V3 cumulative return during V3 loss periods: -249.95%
- T+P in trade during V3 loss: 26.9% of days

### When V3 Is Flat (EMA20 < EMA50)

- V3 flat days: 849
- T+P mean daily return when V3 flat: 0.006004%

### During Choppy Markets (>4 V3 position flips in 30 days)

- Choppy days: 0

## 5. Verdict

### Correlation Assessment

- Pearson correlation: **0.6883** -- FAIL (>= 0.3 threshold)
- Active-day correlation: **0.6882** -- FAIL

### Diversification Value

- Portfolio Sharpe improvement: **-11.1%** -- no improvement (adding T+P hurts)

### Structural Overlap

- T+P in-trade overlap with V3: **98.0%**
- T+P is structurally a **subset** of V3 -- it can only fire within V3 uptrends
- The two strategies are NOT independent signal generators
- T+P fires when V3 flat: **10** days

### Final Assessment

**REDUNDANT: T+P is a subset of V3, no diversification**

Both high correlation and structural overlap. T+P adds nothing to V3.

### Summary Statistics

| Metric | Value |
|--------|-------|
| Pearson correlation (daily returns) | 0.6883 |
| Active-day correlation | 0.6882 |
| T+P entry overlap with V3 | 98.0% |
| Portfolio Sharpe improvement (EW) | -11.1% |
| T+P fires when V3 flat | 10 days |
| V3 Sharpe | 0.696 |
| T+P Sharpe | 0.390 |
| EW Portfolio Sharpe | 0.619 |
| RP Portfolio Sharpe | 0.600 |
