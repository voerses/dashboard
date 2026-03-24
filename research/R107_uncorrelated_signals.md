# R107 -- Uncorrelated Signal Search for V3 Portfolio Diversification

**Date**: 2026-03-24
**Period**: 2021-01-01 to 2026-03-31
**Asset**: BTC spot
**Cost assumption**: 10 bps round-trip

## Objective

Find signals GENUINELY UNCORRELATED with V3 Momentum (EMA20>EMA50 trend-following).
All trend-following variants are correlated with V3 (R105: corr=0.688).
For portfolio diversification, we need signals that fire INDEPENDENTLY of BTC's trend state.

## V3 Momentum Baseline

| Metric | Value |
|--------|-------|
| Ann. Return | 29.40% |
| Ann. Volatility | 43.05% |
| Sharpe Ratio | **0.683** |
| Max Drawdown | -58.95% |
| Calmar Ratio | 0.499 |
| Total Return | 184.74% |

## Correlation Matrix (Daily Returns)

| | V3 Momentum | S1: Mean-Rev | S2: VRP | S3: Macro | S4: Funding | S5: Intraday |
|---|---|---|---|---|---|---|
| V3 Momentum | 1.000 | -0.022 | 0.005 | 0.010 | -0.167 | 0.007 |
| S1: Mean-Rev | -0.022 | 1.000 | 0.008 | 0.044 | 0.207 | -0.013 |
| S2: VRP | 0.005 | 0.008 | 1.000 | -0.154 | -0.008 | 0.051 |
| S3: Macro | 0.010 | 0.044 | -0.154 | 1.000 | -0.038 | 0.061 |
| S4: Funding | -0.167 | 0.207 | -0.008 | -0.038 | 1.000 | 0.078 |
| S5: Intraday | 0.007 | -0.013 | 0.051 | 0.061 | 0.078 | 1.000 |

## Signal Results

### Signal 1: Mean-Reversion with Volatility Regime Gate

**Standalone Metrics:**

| Metric | Value |
|--------|-------|
| Ann. Return | -19.59% |
| Ann. Volatility | 29.21% |
| Sharpe Ratio | **-0.671** |
| Max Drawdown | -72.98% |
| Total Return | -71.10% |

**Correlation with V3:**

| Metric | Value |
|--------|-------|
| Pearson r | **-0.0216** |
| Pearson p-value | 3.49e-01 |
| Spearman r | -0.0150 |
| Active-day Pearson r | -0.0210
| N days | 1892 |

**50/50 Portfolio with V3:**

| Metric | V3 Only | 50/50 Portfolio | Delta |
|--------|---------|----------------|-------|
| Sharpe | 0.683 | 0.190 | -0.492 |
| Ann. Return | 29.40% | 4.90% | |
| Max DD | -58.95% | -52.94% | |

**Verdict**: **KILLED**
- Kill reason: Non-positive Sharpe: -0.671

---

### Signal 2: VRP (Volatility Risk Premium) Direction

**Standalone Metrics:**

| Metric | Value |
|--------|-------|
| Ann. Return | 14.17% |
| Ann. Volatility | 36.10% |
| Sharpe Ratio | **0.393** |
| Max Drawdown | -47.28% |
| Total Return | 46.13% |

**Correlation with V3:**

| Metric | Value |
|--------|-------|
| Pearson r | **0.0051** |
| Pearson p-value | 8.30e-01 |
| Spearman r | 0.0361 |
| Active-day Pearson r | 0.0049
| N days | 1810 |

**Walk-Forward Results:**

| Window | Test Period | OOS Sharpe | OOS Corr | Portfolio Sharpe | Positive? |
|--------|-------------|-----------|----------|-----------------|-----------|
| W1 | 2022-01-01 to 2022-07-01 | -0.533 | 0.274 | -1.078 | NO |
| W2 | 2022-07-01 to 2023-01-01 | 1.252 | 0.156 | -0.412 | YES |
| W3 | 2023-01-01 to 2023-07-01 | -0.744 | -0.250 | 0.741 | NO |
| W4 | 2023-07-01 to 2024-01-01 | 0.138 | 0.566 | 1.295 | YES |
| W5 | 2024-01-01 to 2024-07-01 | -0.403 | 0.029 | 1.261 | NO |
| W6 | 2024-07-01 to 2025-01-01 | -0.666 | -0.036 | 0.599 | NO |

**50/50 Portfolio with V3:**

| Metric | V3 Only | 50/50 Portfolio | Delta |
|--------|---------|----------------|-------|
| Sharpe | 0.683 | 0.616 | -0.067 |
| Ann. Return | 29.40% | 16.45% | |
| Max DD | -58.95% | -42.00% | |

**Verdict**: **KILLED**
- Kill reason: Walk-forward: 2/6 positive (< 3/6)

---

### Signal 3: Macro Regime Rotation

**Standalone Metrics:**

| Metric | Value |
|--------|-------|
| Ann. Return | 15.49% |
| Ann. Volatility | 39.00% |
| Sharpe Ratio | **0.397** |
| Max Drawdown | -50.40% |
| Total Return | 50.98% |

**Correlation with V3:**

| Metric | Value |
|--------|-------|
| Pearson r | **0.0097** |
| Pearson p-value | 6.72e-01 |
| Spearman r | -0.0452 |
| Active-day Pearson r | 0.0095
| N days | 1892 |

**Walk-Forward Results:**

| Window | Test Period | OOS Sharpe | OOS Corr | Portfolio Sharpe | Positive? |
|--------|-------------|-----------|----------|-----------------|-----------|
| W1 | 2022-01-01 to 2022-07-01 | 1.047 | -0.269 | 0.366 | YES |
| W2 | 2022-07-01 to 2023-01-01 | -0.683 | 0.414 | -1.490 | NO |
| W3 | 2023-01-01 to 2023-07-01 | 2.508 | 0.063 | 2.610 | YES |
| W4 | 2023-07-01 to 2024-01-01 | 2.191 | 0.417 | 2.413 | YES |
| W5 | 2024-01-01 to 2024-07-01 | 1.280 | -0.074 | 2.178 | YES |
| W6 | 2024-07-01 to 2025-01-01 | -0.222 | 0.139 | 0.820 | NO |

**50/50 Portfolio with V3:**

| Metric | V3 Only | 50/50 Portfolio | Delta |
|--------|---------|----------------|-------|
| Sharpe | 0.683 | 0.769 | +0.086 |
| Ann. Return | 29.40% | 22.45% | |
| Max DD | -58.95% | -31.58% | |

**Verdict**: **PASSED**
- Correlation: 0.010 (< 0.4 threshold)
- Sharpe: 0.397 (> 0.0)
- Walk-forward: 4/6 positive
- Portfolio Sharpe: 0.769 vs V3: 0.683

---

### Signal 4: Funding Rate Carry

**Standalone Metrics:**

| Metric | Value |
|--------|-------|
| Ann. Return | -12.47% |
| Ann. Volatility | 24.30% |
| Sharpe Ratio | **-0.513** |
| Max Drawdown | -56.46% |
| Total Return | -55.17% |

**Correlation with V3:**

| Metric | Value |
|--------|-------|
| Pearson r | **-0.1671** |
| Pearson p-value | 2.58e-13 |
| Spearman r | -0.1231 |
| Active-day Pearson r | -0.1667
| N days | 1892 |

**50/50 Portfolio with V3:**

| Metric | V3 Only | 50/50 Portfolio | Delta |
|--------|---------|----------------|-------|
| Sharpe | 0.683 | 0.370 | -0.313 |
| Ann. Return | 29.40% | 8.47% | |
| Max DD | -58.95% | -37.47% | |

**Verdict**: **KILLED**
- Kill reason: Non-positive Sharpe: -0.513

---

### Signal 5: Intraday Momentum Breakout

**Standalone Metrics:**

| Metric | Value |
|--------|-------|
| Ann. Return | 11.40% |
| Ann. Volatility | 19.19% |
| Sharpe Ratio | **0.594** |
| Max Drawdown | -22.40% |
| Total Return | 64.32% |

**Correlation with V3:**

| Metric | Value |
|--------|-------|
| Pearson r | **0.0068** |
| Pearson p-value | 7.67e-01 |
| Spearman r | 0.0574 |
| Active-day Pearson r | 0.0062
| N days | 1892 |

**Walk-Forward Results:**

| Window | Test Period | OOS Sharpe | OOS Corr | Portfolio Sharpe | Positive? |
|--------|-------------|-----------|----------|-----------------|-----------|
| W1 | 2022-01-01 to 2022-07-01 | 1.492 | 0.029 | 0.366 | YES |
| W2 | 2022-07-01 to 2023-01-01 | 0.837 | 0.128 | -1.385 | YES |
| W3 | 2023-01-01 to 2023-07-01 | -1.079 | 0.199 | 0.834 | NO |
| W4 | 2023-07-01 to 2024-01-01 | 0.087 | 0.373 | 1.782 | YES |
| W5 | 2024-01-01 to 2024-07-01 | 2.266 | 0.017 | 2.292 | YES |
| W6 | 2024-07-01 to 2025-01-01 | 0.957 | -0.119 | 1.636 | YES |

**50/50 Portfolio with V3:**

| Metric | V3 Only | 50/50 Portfolio | Delta |
|--------|---------|----------------|-------|
| Sharpe | 0.683 | 0.863 | +0.181 |
| Ann. Return | 29.40% | 20.40% | |
| Max DD | -58.95% | -26.94% | |

**Verdict**: **PASSED**
- Correlation: 0.007 (< 0.4 threshold)
- Sharpe: 0.594 (> 0.0)
- Walk-forward: 5/6 positive
- Portfolio Sharpe: 0.863 vs V3: 0.683

---

## Summary

| Signal | Corr w/ V3 | Standalone Sharpe | Portfolio Sharpe | Verdict |
|--------|-----------|------------------|-----------------|---------|
| S1: Mean-Rev Vol Gate | -0.022 | -0.671 | 0.190 | KILLED |
| S2: VRP Direction | 0.005 | 0.393 | 0.616 | KILLED |
| S3: Macro Regime | 0.010 | 0.397 | 0.769 | PASSED |
| S4: Funding Carry | -0.167 | -0.513 | 0.370 | KILLED |
| S5: Intraday Momentum | 0.007 | 0.594 | 0.863 | PASSED |

## Conclusions

**2 signal(s) passed all kill criteria:**
- **S3: Macro Regime**: Sharpe=0.397, Corr=0.010, Portfolio Sharpe=0.769
- **S5: Intraday Momentum**: Sharpe=0.594, Corr=0.007, Portfolio Sharpe=0.863

These signals provide genuine diversification value when combined with V3.

### Kill Reasons

- **S1: Mean-Rev Vol Gate**: Non-positive Sharpe: -0.671
- **S2: VRP Direction**: Walk-forward: 2/6 positive (< 3/6)
- **S4: Funding Carry**: Non-positive Sharpe: -0.513
