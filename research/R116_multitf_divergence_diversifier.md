# R116: Multi-Timeframe Divergence as BTC Portfolio Diversifier

**Date**: 2026-03-24
**Period**: 2020-01-01 to 2026-03-14
**Asset**: BTC spot (derived from 1h data)
**Cost assumption**: 10 bps round-trip
**Walk-forward**: 10 windows, 180d train / 90d test, rolling 90d

## V3 Momentum Baseline

| Metric | Value |
|--------|-------|
| Ann. Return | 42.6% |
| Ann. Volatility | 44.1% |
| Sharpe Ratio | **0.965** |
| Max Drawdown | -56.3% |
| Total Return | 663.9% |
| Regime Sharpe (RANGE) | **-1.01** |

## Phase 1: IC Scan Results

| Signal | 1d IC | 3d IC | 7d IC | 14d IC | Max |IC| | Verdict |
|--------|-------|-------|-------|--------|---------|---------|
| S1: RSI Divergence | 0.0416 | 0.0351 | 0.0456 | 0.0623 | 0.0623 | PASS |
| S2: EMA Alignment | -0.0345 | -0.0126 | 0.0056 | 0.0573 | 0.0573 | PASS |
| S2: EMA Divergence | 0.0079 | 0.0012 | 0.0070 | -0.0061 | 0.0079 | KILL |
| S3: Momentum Div Z | -0.0442 | -0.0131 | -0.0157 | -0.0155 | 0.0442 | PASS |
| S4: Acceleration Div | -0.0036 | -0.0231 | -0.0193 | 0.0111 | 0.0231 | PASS |
| S5: Vol-Adj Divergence | -0.0295 | -0.0419 | -0.0542 | -0.0579 | 0.0579 | PASS |
| S6: MeanRev Timing | -0.0060 | -0.0419 | -0.0264 | -0.0344 | 0.0419 | PASS |

**6/7 signals passed IC >= 0.02 threshold**

## Phase 2: Full-Sample Backtest & V3 Correlation

### S1: RSI Divergence

| Metric | Value |
|--------|-------|
| Ann. Return | 39.3% |
| Ann. Volatility | 32.3% |
| Sharpe Ratio | **1.216** |
| Max Drawdown | -37.5% |
| Total Return | 721.3% |
| Win Rate | 49.8% |
| N Trades | 57 |
| V3 Corr (Pearson) | **0.5739** |
| V3 Corr (Spearman) | 0.5369 |
| Best Params | {'long_thresh': 10, 'short_thresh': 5, 'rebalance': 'weekly', 'mode': 'long_only'} |

**Regime Performance:**

| Regime | Sharpe | Ann. Return | Days |
|--------|--------|-------------|------|
| UPTREND | 2.46 | 85.6% | 811 |
| DOWNTREND | -0.72 | -18.8% | 462 |
| RANGE | 0.87 | 28.4% | 982 |

**50/50 Portfolio with V3:**

| Metric | V3 Only | 50/50 Portfolio | Delta |
|--------|---------|----------------|-------|
| Sharpe | 0.965 | 1.204 | +0.239 |
| Ann. Return | 42.6% | 41.0% | |
| Max DD | -56.3% | -40.7% | |

**Verdict**: **KILLED** (V3 correlation > 0.5)

---

### S2: EMA Alignment

| Metric | Value |
|--------|-------|
| Ann. Return | 13.2% |
| Ann. Volatility | 32.9% |
| Sharpe Ratio | **0.401** |
| Max Drawdown | -58.2% |
| Total Return | 62.5% |
| Win Rate | 29.6% |
| N Trades | 400 |
| V3 Corr (Pearson) | **0.5038** |
| V3 Corr (Spearman) | 0.4181 |
| Best Params | {'long_thresh': 0.5, 'short_thresh': 0.0, 'rebalance': 'daily', 'mode': 'long_only'} |

**Regime Performance:**

| Regime | Sharpe | Ann. Return | Days |
|--------|--------|-------------|------|
| UPTREND | 2.14 | 85.5% | 811 |
| DOWNTREND | -2.35 | -56.1% | 462 |
| RANGE | -0.47 | -14.0% | 983 |

**50/50 Portfolio with V3:**

| Metric | V3 Only | 50/50 Portfolio | Delta |
|--------|---------|----------------|-------|
| Sharpe | 0.965 | 0.832 | -0.133 |
| Ann. Return | 42.6% | 27.9% | |
| Max DD | -56.3% | -53.6% | |

**Verdict**: **KILLED** (V3 correlation > 0.5)

---

### S3: Momentum Div Z

| Metric | Value |
|--------|-------|
| Ann. Return | 5.8% |
| Ann. Volatility | 14.0% |
| Sharpe Ratio | **0.417** |
| Max Drawdown | -16.8% |
| Total Return | 34.4% |
| Win Rate | 45.2% |
| N Trades | 16 |
| V3 Corr (Pearson) | **0.2659** |
| V3 Corr (Spearman) | 0.2422 |
| Best Params | {'long_thresh': 2.0, 'short_thresh': 0.0, 'rebalance': 'weekly', 'mode': 'long_only'} |

**Regime Performance:**

| Regime | Sharpe | Ann. Return | Days |
|--------|--------|-------------|------|
| UPTREND | 1.14 | 20.5% | 811 |
| DOWNTREND | -1.17 | -8.6% | 462 |
| RANGE | 0.03 | 0.3% | 947 |

**50/50 Portfolio with V3:**

| Metric | V3 Only | 50/50 Portfolio | Delta |
|--------|---------|----------------|-------|
| Sharpe | 0.965 | 0.908 | -0.057 |
| Ann. Return | 42.6% | 22.5% | |
| Max DD | -56.3% | -36.9% | |

---

### S4: Acceleration Div

| Metric | Value |
|--------|-------|
| Ann. Return | 16.5% |
| Ann. Volatility | 29.3% |
| Sharpe Ratio | **0.562** |
| Max Drawdown | -64.3% |
| Total Return | 109.8% |
| Win Rate | 46.2% |
| N Trades | 74 |
| V3 Corr (Pearson) | **0.3695** |
| V3 Corr (Spearman) | 0.3713 |
| Best Params | {'long_thresh': 0.5, 'short_thresh': 0.0, 'rebalance': 'weekly', 'mode': 'long_only'} |

**Regime Performance:**

| Regime | Sharpe | Ann. Return | Days |
|--------|--------|-------------|------|
| UPTREND | 2.70 | 84.9% | 811 |
| DOWNTREND | -2.00 | -63.7% | 462 |
| RANGE | -0.12 | -3.1% | 946 |

**50/50 Portfolio with V3:**

| Metric | V3 Only | 50/50 Portfolio | Delta |
|--------|---------|----------------|-------|
| Sharpe | 0.965 | 0.907 | -0.058 |
| Ann. Return | 42.6% | 27.8% | |
| Max DD | -56.3% | -58.3% | |

---

### S5: Vol-Adj Divergence

| Metric | Value |
|--------|-------|
| Ann. Return | 4.8% |
| Ann. Volatility | 9.7% |
| Sharpe Ratio | **0.491** |
| Max Drawdown | -10.6% |
| Total Return | 30.3% |
| Win Rate | 44.6% |
| N Trades | 5 |
| V3 Corr (Pearson) | **-0.0012** |
| V3 Corr (Spearman) | 0.0004 |
| Best Params | {'long_thresh': 2.0, 'short_thresh': 0.0, 'rebalance': 'daily', 'mode': 'long_only'} |

**Regime Performance:**

| Regime | Sharpe | Ann. Return | Days |
|--------|--------|-------------|------|
| UPTREND | 0.00 | 0.0% | 811 |
| DOWNTREND | 0.94 | 19.9% | 462 |
| RANGE | 0.60 | 1.6% | 970 |

**50/50 Portfolio with V3:**

| Metric | V3 Only | 50/50 Portfolio | Delta |
|--------|---------|----------------|-------|
| Sharpe | 0.965 | 1.009 | +0.043 |
| Ann. Return | 42.6% | 22.7% | |
| Max DD | -56.3% | -26.1% | |

---

### S6: MeanRev Timing

| Metric | Value |
|--------|-------|
| Ann. Return | 19.8% |
| Ann. Volatility | 41.5% |
| Sharpe Ratio | **0.479** |
| Max Drawdown | -86.1% |
| Total Return | 100.6% |
| Win Rate | 47.0% |
| N Trades | 87 |
| V3 Corr (Pearson) | **0.5577** |
| V3 Corr (Spearman) | 0.4974 |
| Best Params | {'long_thresh': 0.0, 'short_thresh': 0.0, 'rebalance': 'weekly', 'mode': 'long_only'} |

**Regime Performance:**

| Regime | Sharpe | Ann. Return | Days |
|--------|--------|-------------|------|
| UPTREND | 3.43 | 140.4% | 811 |
| DOWNTREND | -3.49 | -157.4% | 462 |
| RANGE | 0.09 | 3.7% | 983 |

**50/50 Portfolio with V3:**

| Metric | V3 Only | 50/50 Portfolio | Delta |
|--------|---------|----------------|-------|
| Sharpe | 0.965 | 0.827 | -0.139 |
| Ann. Return | 42.6% | 31.2% | |
| Max DD | -56.3% | -73.6% | |

**Verdict**: **KILLED** (V3 correlation > 0.5)

---

## Phase 3: Walk-Forward Validation

### S3: Momentum Div Z

| Window | Test Period | OOS Sharpe | OOS Return | Train Sharpe | Positive? |
|--------|-------------|-----------|------------|-------------|-----------|
| W1 | 2023-09-26 to 2023-12-25 | 2.774 | 20.8% | 0.787 | YES |
| W2 | 2023-12-25 to 2024-03-24 | 2.538 | 26.8% | 2.427 | YES |
| W3 | 2024-03-24 to 2024-06-22 | -1.634 | -3.0% | 2.753 | NO |
| W4 | 2024-06-22 to 2024-09-20 | 3.858 | 21.0% | 1.554 | YES |
| W5 | 2024-09-20 to 2024-12-19 | 1.081 | 3.6% | 2.151 | YES |
| W6 | 2024-12-19 to 2025-03-19 | -2.082 | -4.0% | 2.665 | NO |
| W7 | 2025-03-19 to 2025-06-17 | -1.759 | -11.4% | 1.841 | NO |
| W8 | 2025-06-17 to 2025-09-15 | 3.403 | 10.7% | 0.907 | YES |
| W9 | 2025-09-15 to 2025-12-14 | -1.438 | -8.9% | 1.691 | NO |
| W10 | 2025-12-14 to 2026-03-14 | -0.527 | -0.6% | 2.036 | NO |

**Summary:**
- Mean OOS Sharpe: **0.621**
- Median OOS Sharpe: 0.277
- Positive windows: **5/10**
- OOS V3 Correlation: 0.3928

**Verdict**: **PASSED**

---

### S4: Acceleration Div

| Window | Test Period | OOS Sharpe | OOS Return | Train Sharpe | Positive? |
|--------|-------------|-----------|------------|-------------|-----------|
| W1 | 2023-09-26 to 2023-12-25 | -2.047 | -17.8% | 1.414 | NO |
| W2 | 2023-12-25 to 2024-03-24 | 1.165 | 11.7% | 2.152 | YES |
| W3 | 2024-03-24 to 2024-06-22 | 0.257 | 0.7% | 1.845 | YES |
| W4 | 2024-06-22 to 2024-09-20 | -2.871 | -0.2% | 1.283 | NO |
| W5 | 2024-09-20 to 2024-12-19 | 0.617 | 2.5% | 1.874 | YES |
| W6 | 2024-12-19 to 2025-03-19 | 0.540 | 2.5% | 2.020 | YES |
| W7 | 2025-03-19 to 2025-06-17 | 1.488 | 13.1% | 2.363 | YES |
| W8 | 2025-06-17 to 2025-09-15 | 2.081 | 10.5% | 2.641 | YES |
| W9 | 2025-09-15 to 2025-12-14 | -4.918 | -34.5% | 1.630 | NO |
| W10 | 2025-12-14 to 2026-03-14 | 0.554 | 1.3% | 0.670 | YES |

**Summary:**
- Mean OOS Sharpe: **-0.314**
- Median OOS Sharpe: 0.547
- Positive windows: **7/10**
- OOS V3 Correlation: 0.2774

**Verdict**: **KILLED**
- Mean OOS Sharpe -0.314 < 0.3

---

### S5: Vol-Adj Divergence

| Window | Test Period | OOS Sharpe | OOS Return | Train Sharpe | Positive? |
|--------|-------------|-----------|------------|-------------|-----------|
| W1 | 2023-09-26 to 2023-12-25 | -2.642 | -17.7% | 2.008 | NO |
| W2 | 2023-12-25 to 2024-03-24 | 0.000 | 0.0% | 1.598 | NO |
| W3 | 2024-03-24 to 2024-06-22 | 0.000 | 0.0% | 1.867 | NO |
| W4 | 2024-06-22 to 2024-09-20 | 1.861 | 21.8% | 1.644 | YES |
| W5 | 2024-09-20 to 2024-12-19 | -3.851 | -38.9% | 3.646 | NO |
| W6 | 2024-12-19 to 2025-03-19 | 0.000 | 0.0% | 2.448 | NO |
| W7 | 2025-03-19 to 2025-06-17 | -0.061 | -0.4% | 1.428 | NO |
| W8 | 2025-06-17 to 2025-09-15 | 0.000 | 0.0% | 2.267 | NO |
| W9 | 2025-09-15 to 2025-12-14 | 1.723 | 8.5% | 2.267 | YES |
| W10 | 2025-12-14 to 2026-03-14 | 1.554 | 8.2% | 2.294 | YES |

**Summary:**
- Mean OOS Sharpe: **-0.142**
- Median OOS Sharpe: 0.000
- Positive windows: **3/10**
- OOS V3 Correlation: -0.3333

**Verdict**: **KILLED**
- Mean OOS Sharpe -0.142 < 0.3
- Positive windows 3/10 < 5/10

---

## Summary

| Signal | IC (max) | Sharpe | V3 Corr | WF Mean Sharpe | WF +ive | Verdict |
|--------|----------|--------|---------|----------------|---------|---------|
| S1: RSI Divergence | 0.0623 | 1.216 | 0.5739 | N/A | N/A | KILLED (Corr) |
| S2: EMA Alignment | 0.0573 | 0.401 | 0.5038 | N/A | N/A | KILLED (Corr) |
| S2: EMA Divergence | 0.0079 | N/A | N/A | N/A | N/A | KILLED (IC) |
| S3: Momentum Div Z | 0.0442 | 0.417 | 0.2659 | 0.621 | 5/10 | **PASSED** |
| S4: Acceleration Div | 0.0231 | 0.562 | 0.3695 | -0.314 | 7/10 | KILLED (WF) |
| S5: Vol-Adj Divergence | 0.0579 | 0.491 | -0.0012 | -0.142 | 3/10 | KILLED (WF) |
| S6: MeanRev Timing | 0.0419 | 0.479 | 0.5577 | N/A | N/A | KILLED (Corr) |

## Conclusions

**1 signal(s) passed all kill criteria:**

- **S3: Momentum Div Z**: Sharpe=0.417, V3 Corr=0.2659, WF Sharpe=0.621, WF +ive=5/10
  - RANGE regime Sharpe: 0.03 (V3 RANGE Sharpe: -1.01 — complementary)

### Portfolio Diversification Value

**S3: Momentum Div Z** 50/50 portfolio with V3:
- Portfolio Sharpe: 0.908 vs V3 alone: 0.965 (-0.057)
- Portfolio MaxDD: -36.9% vs V3 alone: -56.3%

### Kill Reasons

- **S1: RSI Divergence**: V3 Correlation > 0.5
- **S2: EMA Alignment**: V3 Correlation > 0.5
- **S2: EMA Divergence**: IC < 0.02 across all horizons
- **S4: Acceleration Div**: Mean OOS Sharpe -0.314 < 0.3
- **S5: Vol-Adj Divergence**: Mean OOS Sharpe -0.142 < 0.3
- **S5: Vol-Adj Divergence**: Positive windows 3/10 < 5/10
- **S6: MeanRev Timing**: V3 Correlation > 0.5

## Methodology Notes

- All signals computed from BTC 1h spot data (resampled to 4h/daily as needed)
- No external data used — purely price-derived signals
- V3 baseline uses simplified EMA20/50 crossover with weekly rebalance (no positioning/VRP overlays)
- Walk-forward: 10 windows x 180d train / 90d test, rolling 90d
- Fees: 10 bps round-trip per position change
- IC computed as Spearman rank correlation with forward returns
- Regime classification: UPTREND (SMA50>SMA200 & close>SMA50), DOWNTREND (SMA50<SMA200 & close<SMA50), RANGE (other)
