# R114: Realized Volatility Structure as BTC Diversifier Signal

Date: 2026-03-24 20:46

V3 Baseline Sharpe (full period): 1.071

## 1. Information Coefficient Scan

| Signal | 1d IC | 3d IC | 7d IC | 14d IC | Best Horizon |
|--------|-------|-------|-------|--------|-------------|
| S1_vol_ratio | -0.0198 | -0.0211 | -0.0502* | -0.0613** | 14d (-0.0613) |
| S2_vol_slope | -0.0093 | -0.0139 | -0.0389 | -0.0520* | 14d (-0.0520) |
| S3_skewness | +0.0134 | +0.0460* | +0.0756*** | +0.0906*** | 14d (+0.0906) |
| S4_volvol | +0.0214 | +0.0554** | +0.0706*** | +0.1105*** | 14d (+0.1105) |
| S5_parkinson_ratio | -0.0068 | +0.0130 | -0.0028 | -0.0271 | 14d (-0.0271) |
| S6_vol_regime_cont | -0.0400 | -0.0621** | -0.0563** | -0.0089 | 3d (-0.0621) |

## 2. Rolling IC Stationarity (last 2 years)

| Signal | Mean IC (2y) | Pct Positive | Stable? |
|--------|-------------|-------------|---------|
| S1_vol_ratio | +0.0007 | 48.5% | No |
| S2_vol_slope | -0.0126 | 47.7% | No |
| S3_skewness | +0.0221 | 54.7% | No |
| S4_volvol | -0.0242 | 31.9% | No |
| S5_parkinson_ratio | -0.1217 | 27.5% | No |
| S6_vol_regime_cont | -0.0047 | 52.2% | No |

## 3. Signal-by-Signal Results

### S1_vol_ratio

**Verdict: KILLED**
- PASS: max |IC| = 0.0613 >= 0.02
- PASS: |corr V3| = 0.093 <= 0.5
- INFO: WF mean OOS Sharpe = 2.067 (outlier-sensitive)
- KILL: WF median OOS Sharpe = -0.202 < 0.3
- PASS: 50% positive OOS windows >= 50%
- PASS: Portfolio improves over V3-only at some allocation

#### Walk-Forward Results (10 windows)
- Mean OOS Sharpe: 2.067
- Median OOS Sharpe: -0.202
- Positive windows: 50%
- Mean OOS return: 127.36%
- Mean OOS MaxDD: -17.60%

| Window | Train Period | Test Period | Train Sharpe | OOS Sharpe | OOS Return | OOS MaxDD | Params |
|--------|-------------|-------------|-------------|-----------|-----------|----------|--------|
| 1 | 2020-03-30..2020-09-25 | 2020-09-26..2020-12-24 | 1.848 | 0.805 | 13.79% | -4.43% | {'low_thresh': 0.5, 'high_thresh': 1.6} |
| 2 | 2020-06-28..2020-12-24 | 2020-12-25..2021-03-24 | 1.214 | 15.419 | 1081.21% | -19.94% | {'low_thresh': 0.8, 'high_thresh': 1.6} |
| 3 | 2020-09-26..2021-03-24 | 2021-03-25..2021-06-22 | 9.682 | -0.848 | -62.46% | -34.14% | {'low_thresh': 0.8, 'high_thresh': 1.5} |
| 4 | 2020-12-25..2021-06-22 | 2021-06-23..2021-09-20 | 5.607 | -0.475 | -21.22% | -15.86% | {'low_thresh': 0.7, 'high_thresh': 1.6} |
| 5 | 2021-03-25..2021-09-20 | 2021-09-21..2021-12-19 | 0.593 | 0.072 | 2.08% | -10.06% | {'low_thresh': 0.7, 'high_thresh': 1.6} |
| 6 | 2021-06-23..2021-12-19 | 2021-12-20..2022-03-19 | 1.414 | -0.892 | -22.45% | -16.97% | {'low_thresh': 0.5, 'high_thresh': 1.6} |
| 7 | 2021-09-21..2022-03-19 | 2022-03-20..2022-06-17 | 0.562 | 7.015 | 274.45% | -6.80% | {'low_thresh': 0.5, 'high_thresh': 1.6} |
| 8 | 2021-12-20..2022-06-17 | 2022-06-18..2022-09-15 | 4.316 | -2.048 | -74.82% | -31.84% | {'low_thresh': 0.5, 'high_thresh': 1.4} |
| 9 | 2022-03-20..2022-09-15 | 2022-09-16..2022-12-14 | 1.517 | 2.886 | 132.12% | -10.65% | {'low_thresh': 0.5, 'high_thresh': 1.4} |
| 10 | 2022-06-18..2022-12-14 | 2022-12-15..2023-03-14 | 0.057 | -1.266 | -49.09% | -25.37% | {'low_thresh': 0.6, 'high_thresh': 1.3} |

#### Correlation with V3
- Full period: 0.093
- Crash days: 0.063
- V3 losing days: 0.099
- Rolling 60d: 0.107 +/- 0.323

#### Portfolio Combination
| Allocation | Sharpe | Ann Return | Max DD |
|-----------|--------|-----------|--------|
| V3_only | 1.071 | 47.86% | -58.95% |
| signal_only | 0.433 | 20.30% | -65.37% |
| 50/50 | 1.006 | 34.08% | -41.32% |
| 60/40 | 1.079 | 36.83% | -44.87% |
| 70/30 | 1.116 | 39.59% | -48.43% |
| risk_parity | 0.724 | 22.56% | -45.84% |

#### Regime Analysis
| Regime | Days | Signal Sharpe | V3 Sharpe | Correlation |
|--------|------|--------------|----------|-------------|
| UPTREND | 867 | 0.247 | 2.750 | 0.272 |
| DOWNTREND | 616 | 0.493 | -0.158 | -0.086 |
| RANGE | 775 | 0.634 | -0.747 | -0.073 |

### S2_vol_slope

**Verdict: KILLED**
- PASS: max |IC| = 0.0520 >= 0.02
- PASS: |corr V3| = 0.187 <= 0.5
- INFO: WF mean OOS Sharpe = 1.199 (outlier-sensitive)
- KILL: WF median OOS Sharpe = -0.433 < 0.3
- KILL: Only 40% positive OOS windows (need >= 50%)
- PASS: Portfolio improves over V3-only at some allocation

#### Walk-Forward Results (10 windows)
- Mean OOS Sharpe: 1.199
- Median OOS Sharpe: -0.433
- Positive windows: 40%
- Mean OOS return: 78.00%
- Mean OOS MaxDD: -29.30%

| Window | Train Period | Test Period | Train Sharpe | OOS Sharpe | OOS Return | OOS MaxDD | Params |
|--------|-------------|-------------|-------------|-----------|-----------|----------|--------|
| 1 | 2020-04-29..2020-10-25 | 2020-10-26..2021-01-23 | 1.727 | -1.272 | -88.87% | -47.57% | {'neg_thresh': -5, 'pos_thresh': 5} |
| 2 | 2020-07-28..2021-01-23 | 2021-01-24..2021-04-23 | -0.752 | 8.496 | 617.52% | -19.30% | {'neg_thresh': -10, 'pos_thresh': 10} |
| 3 | 2020-10-26..2021-04-23 | 2021-04-24..2021-07-22 | 1.175 | -0.823 | -74.69% | -38.99% | {'neg_thresh': -10, 'pos_thresh': 10} |
| 4 | 2021-01-24..2021-07-22 | 2021-07-23..2021-10-20 | 0.474 | 5.448 | 328.42% | -22.40% | {'neg_thresh': -7, 'pos_thresh': 10} |
| 5 | 2021-04-24..2021-10-20 | 2021-10-21..2022-01-18 | 0.573 | -1.423 | -75.97% | -36.27% | {'neg_thresh': -3, 'pos_thresh': 10} |
| 6 | 2021-07-23..2022-01-18 | 2022-01-19..2022-04-18 | 0.538 | 0.616 | 39.27% | -18.63% | {'neg_thresh': -3, 'pos_thresh': 5} |
| 7 | 2021-10-21..2022-04-18 | 2022-04-19..2022-07-17 | -0.601 | -0.185 | -13.60% | -32.90% | {'neg_thresh': -3, 'pos_thresh': 3} |
| 8 | 2022-01-19..2022-07-17 | 2022-07-18..2022-10-15 | 0.214 | -0.681 | -34.00% | -25.40% | {'neg_thresh': -3, 'pos_thresh': 5} |
| 9 | 2022-04-19..2022-10-15 | 2022-10-16..2023-01-13 | -0.174 | 2.948 | 140.99% | -13.40% | {'neg_thresh': -3, 'pos_thresh': 5} |
| 10 | 2022-07-18..2023-01-13 | 2023-01-14..2023-04-13 | 0.659 | -1.134 | -59.08% | -38.15% | {'neg_thresh': -3, 'pos_thresh': 10} |

#### Correlation with V3
- Full period: 0.187
- Crash days: 0.134
- V3 losing days: 0.206
- Rolling 60d: 0.220 +/- 0.369

#### Portfolio Combination
| Allocation | Sharpe | Ann Return | Max DD |
|-----------|--------|-----------|--------|
| V3_only | 1.071 | 47.86% | -58.95% |
| signal_only | 0.461 | 26.21% | -65.21% |
| 50/50 | 0.942 | 37.03% | -53.67% |
| 60/40 | 1.025 | 39.20% | -53.04% |
| 70/30 | 1.079 | 41.36% | -53.94% |
| risk_parity | 0.761 | 27.87% | -59.76% |

#### Regime Analysis
| Regime | Days | Signal Sharpe | V3 Sharpe | Correlation |
|--------|------|--------------|----------|-------------|
| UPTREND | 867 | 0.580 | 2.750 | 0.347 |
| DOWNTREND | 616 | 0.187 | -0.158 | -0.091 |
| RANGE | 775 | 0.651 | -0.747 | 0.134 |

### S3_skewness

**Verdict: ALIVE**
- PASS: max |IC| = 0.0906 >= 0.02
- PASS: |corr V3| = 0.111 <= 0.5
- INFO: WF mean OOS Sharpe = 0.772 (outlier-sensitive)
- PASS: WF median OOS Sharpe = 0.904 >= 0.3
- PASS: 60% positive OOS windows >= 50%
- PASS: Portfolio improves over V3-only at some allocation

#### Walk-Forward Results (10 windows)
- Mean OOS Sharpe: 0.772
- Median OOS Sharpe: 0.904
- Positive windows: 60%
- Mean OOS return: 36.82%
- Mean OOS MaxDD: -19.53%

| Window | Train Period | Test Period | Train Sharpe | OOS Sharpe | OOS Return | OOS MaxDD | Params |
|--------|-------------|-------------|-------------|-----------|-----------|----------|--------|
| 1 | 2020-03-16..2020-09-11 | 2020-09-12..2020-12-10 | -1.053 | -1.113 | -30.48% | -14.80% | {'neg_thresh': -0.5, 'pos_thresh': 0.2} |
| 2 | 2020-06-14..2020-12-10 | 2020-12-11..2021-03-10 | 0.028 | 2.050 | 148.67% | -26.07% | {'neg_thresh': -0.7, 'pos_thresh': 0.2} |
| 3 | 2020-09-12..2021-03-10 | 2021-03-11..2021-06-08 | 1.112 | 0.237 | 14.90% | -23.69% | {'neg_thresh': -0.7, 'pos_thresh': 0.5} |
| 4 | 2020-12-11..2021-06-08 | 2021-06-09..2021-09-06 | 3.020 | 1.630 | 67.30% | -13.23% | {'neg_thresh': -0.5, 'pos_thresh': 0.5} |
| 5 | 2021-03-11..2021-09-06 | 2021-09-07..2021-12-05 | 4.429 | -1.143 | -75.89% | -37.67% | {'neg_thresh': -0.3, 'pos_thresh': 0.2} |
| 6 | 2021-06-09..2021-12-05 | 2021-12-06..2022-03-05 | 0.481 | -0.593 | -33.56% | -23.05% | {'neg_thresh': -0.2, 'pos_thresh': 0.2} |
| 7 | 2021-09-07..2022-03-05 | 2022-03-06..2022-06-03 | -0.865 | 3.773 | 137.75% | -6.90% | {'neg_thresh': -0.2, 'pos_thresh': 0.7} |
| 8 | 2021-12-06..2022-06-03 | 2022-06-04..2022-09-01 | 2.187 | 1.571 | 100.29% | -20.47% | {'neg_thresh': -0.2, 'pos_thresh': 0.3} |
| 9 | 2022-03-06..2022-09-01 | 2022-09-02..2022-11-30 | 4.690 | -0.428 | -24.75% | -17.17% | {'neg_thresh': -0.2, 'pos_thresh': 0.7} |
| 10 | 2022-06-04..2022-11-30 | 2022-12-01..2023-02-28 | 1.913 | 1.739 | 63.96% | -12.25% | {'neg_thresh': -0.2, 'pos_thresh': 0.5} |

#### Correlation with V3
- Full period: -0.111
- Crash days: -0.193
- V3 losing days: -0.224
- Rolling 60d: -0.053 +/- 0.497

#### Portfolio Combination
| Allocation | Sharpe | Ann Return | Max DD |
|-----------|--------|-----------|--------|
| V3_only | 1.071 | 47.86% | -58.95% |
| signal_only | 0.259 | 13.97% | -86.46% |
| 50/50 | 0.934 | 30.91% | -33.90% |
| 60/40 | 1.055 | 34.30% | -39.01% |
| 70/30 | 1.122 | 37.69% | -43.93% |
| risk_parity | 0.661 | 19.94% | -50.06% |

#### Regime Analysis
| Regime | Days | Signal Sharpe | V3 Sharpe | Correlation |
|--------|------|--------------|----------|-------------|
| UPTREND | 867 | 0.321 | 2.750 | -0.026 |
| DOWNTREND | 616 | 0.272 | -0.158 | -0.029 |
| RANGE | 775 | 0.178 | -0.747 | -0.323 |

### S4_volvol

**Verdict: KILLED**
- PASS: max |IC| = 0.1105 >= 0.02
- PASS: |corr V3| = 0.094 <= 0.5
- INFO: WF mean OOS Sharpe = 1.218 (outlier-sensitive)
- KILL: WF median OOS Sharpe = -0.128 < 0.3
- KILL: Only 40% positive OOS windows (need >= 50%)
- WARN: No portfolio allocation improves over V3-only Sharpe (1.071)

#### Walk-Forward Results (10 windows)
- Mean OOS Sharpe: 1.218
- Median OOS Sharpe: -0.128
- Positive windows: 40%
- Mean OOS return: 32.99%
- Mean OOS MaxDD: -16.64%

| Window | Train Period | Test Period | Train Sharpe | OOS Sharpe | OOS Return | OOS MaxDD | Params |
|--------|-------------|-------------|-------------|-----------|-----------|----------|--------|
| 1 | 2020-03-19..2020-09-14 | 2020-09-15..2020-12-13 | 5.469 | 7.910 | 225.15% | -2.71% | {'low_thresh': -1.0, 'high_thresh': 1.5} |
| 2 | 2020-06-17..2020-12-13 | 2020-12-14..2021-03-13 | 8.048 | -1.061 | -51.38% | -28.77% | {'low_thresh': -1.0, 'high_thresh': 1.5} |
| 3 | 2020-09-15..2021-03-13 | 2021-03-14..2021-06-11 | 3.957 | -0.174 | -11.49% | -22.82% | {'low_thresh': 0.0, 'high_thresh': 1.5} |
| 4 | 2020-12-14..2021-06-11 | 2021-06-12..2021-09-09 | -0.320 | 2.643 | 132.87% | -12.65% | {'low_thresh': 0.0, 'high_thresh': 1.5} |
| 5 | 2021-03-14..2021-09-09 | 2021-09-10..2021-12-08 | 4.255 | 4.029 | 159.68% | -11.50% | {'low_thresh': -1.0, 'high_thresh': 0.3} |
| 6 | 2021-06-12..2021-12-08 | 2021-12-09..2022-03-08 | 1.694 | -1.629 | -68.24% | -35.26% | {'low_thresh': -1.0, 'high_thresh': 1.5} |
| 7 | 2021-09-10..2022-03-08 | 2022-03-09..2022-06-06 | -0.376 | -1.502 | -63.55% | -22.51% | {'low_thresh': -1.0, 'high_thresh': 0.3} |
| 8 | 2021-12-09..2022-06-06 | 2022-06-07..2022-09-04 | -1.358 | -0.837 | -33.36% | -14.84% | {'low_thresh': -0.5, 'high_thresh': 0.5} |
| 9 | 2022-03-09..2022-09-04 | 2022-09-05..2022-12-03 | -0.342 | -0.081 | -2.66% | -13.01% | {'low_thresh': -1.0, 'high_thresh': 0.3} |
| 10 | 2022-06-07..2022-12-03 | 2022-12-04..2023-03-03 | 0.249 | 2.880 | 42.89% | -2.35% | {'low_thresh': -1.0, 'high_thresh': 1.0} |

#### Correlation with V3
- Full period: 0.094
- Crash days: 0.176
- V3 losing days: 0.189
- Rolling 60d: 0.215 +/- 0.414

#### Portfolio Combination
| Allocation | Sharpe | Ann Return | Max DD |
|-----------|--------|-----------|--------|
| V3_only | 1.071 | 47.86% | -58.95% |
| signal_only | -0.303 | -16.28% | -90.71% |
| 50/50 | 0.432 | 15.79% | -63.15% |
| 60/40 | 0.618 | 22.20% | -61.24% |
| 70/30 | 0.784 | 28.62% | -59.48% |
| risk_parity | 0.275 | 9.46% | -66.24% |

#### Regime Analysis
| Regime | Days | Signal Sharpe | V3 Sharpe | Correlation |
|--------|------|--------------|----------|-------------|
| UPTREND | 867 | -0.250 | 2.750 | 0.024 |
| DOWNTREND | 616 | -0.320 | -0.158 | 0.104 |
| RANGE | 775 | -0.351 | -0.747 | 0.228 |

### S5_parkinson_ratio

**Verdict: KILLED**
- PASS: max |IC| = 0.0271 >= 0.02
- PASS: |corr V3| = 0.055 <= 0.5
- INFO: WF mean OOS Sharpe = 0.711 (outlier-sensitive)
- KILL: WF median OOS Sharpe = -0.255 < 0.3
- KILL: Only 30% positive OOS windows (need >= 50%)
- WARN: No portfolio allocation improves over V3-only Sharpe (1.071)

#### Walk-Forward Results (10 windows)
- Mean OOS Sharpe: 0.711
- Median OOS Sharpe: -0.255
- Positive windows: 30%
- Mean OOS return: 51.34%
- Mean OOS MaxDD: -21.55%

| Window | Train Period | Test Period | Train Sharpe | OOS Sharpe | OOS Return | OOS MaxDD | Params |
|--------|-------------|-------------|-------------|-----------|-----------|----------|--------|
| 1 | 2020-03-20..2020-09-15 | 2020-09-16..2020-12-14 | 1.731 | -0.288 | -13.48% | -28.68% | {'low_thresh': 0.0, 'high_thresh': 1.0} |
| 2 | 2020-06-18..2020-12-14 | 2020-12-15..2021-03-14 | 0.983 | 11.164 | 694.92% | -23.46% | {'low_thresh': 0.0, 'high_thresh': 1.5} |
| 3 | 2020-09-16..2021-03-14 | 2021-03-15..2021-06-12 | 3.427 | -1.408 | -91.35% | -45.31% | {'low_thresh': 0.0, 'high_thresh': 1.5} |
| 4 | 2020-12-15..2021-06-12 | 2021-06-13..2021-09-10 | 0.608 | -1.363 | -67.48% | -27.50% | {'low_thresh': 0.0, 'high_thresh': 1.0} |
| 5 | 2021-03-15..2021-09-10 | 2021-09-11..2021-12-09 | -0.874 | -0.222 | -9.35% | -20.38% | {'low_thresh': 0.0, 'high_thresh': 1.0} |
| 6 | 2021-06-13..2021-12-09 | 2021-12-10..2022-03-09 | -0.026 | -0.057 | -2.36% | -26.06% | {'low_thresh': -1.0, 'high_thresh': 1.0} |
| 7 | 2021-09-11..2022-03-09 | 2022-03-10..2022-06-07 | 0.309 | -1.381 | -36.79% | -16.30% | {'low_thresh': -1.0, 'high_thresh': 1.0} |
| 8 | 2021-12-10..2022-06-07 | 2022-06-08..2022-09-05 | 0.281 | 1.386 | 50.04% | -10.72% | {'low_thresh': -0.5, 'high_thresh': 1.0} |
| 9 | 2022-03-10..2022-09-05 | 2022-09-06..2022-12-04 | -0.697 | 0.307 | 9.47% | -10.34% | {'low_thresh': -1.0, 'high_thresh': 0.3} |
| 10 | 2022-06-08..2022-12-04 | 2022-12-05..2023-03-04 | 0.186 | -1.028 | -20.26% | -6.78% | {'low_thresh': -1.0, 'high_thresh': 1.5} |

#### Correlation with V3
- Full period: 0.055
- Crash days: 0.251
- V3 losing days: 0.171
- Rolling 60d: -0.029 +/- 0.391

#### Portfolio Combination
| Allocation | Sharpe | Ann Return | Max DD |
|-----------|--------|-----------|--------|
| V3_only | 1.071 | 47.86% | -58.95% |
| signal_only | -0.505 | -26.32% | -93.04% |
| 50/50 | 0.306 | 10.77% | -66.09% |
| 60/40 | 0.522 | 18.19% | -62.46% |
| 70/30 | 0.717 | 25.60% | -58.95% |
| risk_parity | 0.397 | 13.17% | -57.52% |

#### Regime Analysis
| Regime | Days | Signal Sharpe | V3 Sharpe | Correlation |
|--------|------|--------------|----------|-------------|
| UPTREND | 867 | 0.352 | 2.750 | -0.013 |
| DOWNTREND | 616 | -1.405 | -0.158 | -0.025 |
| RANGE | 775 | -0.506 | -0.747 | 0.201 |

### S6_vol_regime_cont

**Verdict: KILLED**
- PASS: max |IC| = 0.0621 >= 0.02
- PASS: |corr V3| = 0.234 <= 0.5
- INFO: WF mean OOS Sharpe = 0.370 (outlier-sensitive)
- KILL: WF median OOS Sharpe = -0.267 < 0.3
- KILL: Only 40% positive OOS windows (need >= 50%)
- PASS: Portfolio improves over V3-only at some allocation

#### Walk-Forward Results (10 windows)
- Mean OOS Sharpe: 0.370
- Median OOS Sharpe: -0.267
- Positive windows: 40%
- Mean OOS return: 41.83%
- Mean OOS MaxDD: -21.97%

| Window | Train Period | Test Period | Train Sharpe | OOS Sharpe | OOS Return | OOS MaxDD | Params |
|--------|-------------|-------------|-------------|-----------|-----------|----------|--------|
| 1 | 2020-04-18..2020-10-14 | 2020-10-15..2021-01-12 | -0.025 | -1.291 | -44.88% | -26.46% | {'z_thresh': 1.5} |
| 2 | 2020-07-17..2021-01-12 | 2021-01-13..2021-04-12 | -0.879 | 3.642 | 284.59% | -19.78% | {'z_thresh': 0.5} |
| 3 | 2020-10-15..2021-04-12 | 2021-04-13..2021-07-11 | 0.787 | -0.179 | -12.26% | -25.72% | {'z_thresh': 1.0} |
| 4 | 2021-01-13..2021-07-11 | 2021-07-12..2021-10-09 | 1.626 | 4.058 | 234.17% | -15.66% | {'z_thresh': 1.0} |
| 5 | 2021-04-13..2021-10-09 | 2021-10-10..2022-01-07 | 1.412 | 2.169 | 115.17% | -25.33% | {'z_thresh': 0.5} |
| 6 | 2021-07-12..2022-01-07 | 2022-01-08..2022-04-07 | 4.110 | 0.462 | 22.36% | -23.79% | {'z_thresh': 0.7} |
| 7 | 2021-10-10..2022-04-07 | 2022-04-08..2022-07-06 | 2.153 | -0.475 | -25.35% | -12.88% | {'z_thresh': 0.7} |
| 8 | 2022-01-08..2022-07-06 | 2022-07-07..2022-10-04 | 0.619 | -0.355 | -10.92% | -13.10% | {'z_thresh': 1.5} |
| 9 | 2022-04-08..2022-10-04 | 2022-10-05..2023-01-02 | 0.081 | -2.145 | -64.30% | -23.65% | {'z_thresh': 1.5} |
| 10 | 2022-07-07..2023-01-02 | 2023-01-03..2023-04-02 | 0.166 | -2.185 | -80.29% | -33.34% | {'z_thresh': 0.7} |

#### Correlation with V3
- Full period: -0.234
- Crash days: -0.238
- V3 losing days: -0.328
- Rolling 60d: -0.257 +/- 0.292

#### Portfolio Combination
| Allocation | Sharpe | Ann Return | Max DD |
|-----------|--------|-----------|--------|
| V3_only | 1.071 | 47.86% | -58.95% |
| signal_only | 0.123 | 6.27% | -84.30% |
| 50/50 | 0.909 | 27.06% | -53.24% |
| 60/40 | 1.052 | 31.22% | -49.81% |
| 70/30 | 1.125 | 35.38% | -48.80% |
| risk_parity | 0.889 | 24.40% | -48.62% |

#### Regime Analysis
| Regime | Days | Signal Sharpe | V3 Sharpe | Correlation |
|--------|------|--------------|----------|-------------|
| UPTREND | 867 | -0.506 | 2.750 | -0.289 |
| DOWNTREND | 616 | 0.845 | -0.158 | -0.162 |
| RANGE | 775 | 0.070 | -0.747 | -0.286 |

## 4. Summary

**Alive signals (1):** S3_skewness
**Killed signals (5):** S1_vol_ratio, S2_vol_slope, S4_volvol, S5_parkinson_ratio, S6_vol_regime_cont

### Recommendation
- Best diversifier: **S3_skewness** (WF Sharpe=0.772, V3 corr=-0.111)
- Best allocation: 70/30 (Sharpe=1.122)
- RANGE regime Sharpe: 0.178 (V3 in RANGE: -0.747)
- **KEY FINDING**: Signal profitable in RANGE regime where V3 loses money

## 5. Kill Criteria Summary

| Signal | IC >= 0.02 | V3 Corr <= 0.5 | WF Sharpe >= 0.3 | Overall |
|--------|-----------|---------------|-----------------|---------|
| S1_vol_ratio | PASS | PASS | FAIL | **KILLED** |
| S2_vol_slope | PASS | PASS | FAIL | **KILLED** |
| S3_skewness | PASS | PASS | PASS | **ALIVE** |
| S4_volvol | PASS | PASS | FAIL | **KILLED** |
| S5_parkinson_ratio | PASS | PASS | FAIL | **KILLED** |
| S6_vol_regime_cont | PASS | PASS | FAIL | **KILLED** |

## 6. Honest Assessment & Caveats

### S3 Skewness: Conditional Pass with Caveats

S3 (30d rolling skewness of 1h returns) is the only survivor, but with important limitations:

1. **Rolling IC is NOT stable.** Mean 2y IC = +0.0221, only 54.7% of rolling windows positive. This is barely above coin-flip.
2. **WF is front-loaded.** The best OOS Sharpes (3.773, 2.050) come from 2021-2022 windows. Later windows (2022-2023) are mixed. This raises non-stationarity concerns.
3. **Standalone Sharpe is weak.** Full-period standalone Sharpe = 0.259 with -86% drawdown. This is NOT a viable standalone strategy.
4. **Portfolio value is marginal.** 70/30 (V3/skew) Sharpe = 1.122 vs V3 = 1.071 (+4.8%). The improvement comes entirely from drawdown reduction (MaxDD -43.9% vs -59.0%), not return enhancement.
5. **Negative V3 correlation is the real value.** Correlation = -0.111, crash-day correlation = -0.193. This is genuinely uncorrelated.

### Why Most Vol Structure Signals Fail as Standalone Strategies

The core problem: realized vol structure signals have **predictive power for vol regime changes** (high IC at 7-14d horizons) but **poor translation to directional return prediction**. This is because:

- Vol compression predicts a breakout, but not its DIRECTION
- Vol expansion predicts mean-reversion, but timing is imprecise
- The signal-to-position mapping (threshold-based) loses information
- BTC's strong positive drift means being flat/short during "uncertain" vol regimes costs the beta premium

### Recommendation

**CONDITIONAL PASS for S3_skewness as a V3 overlay/filter, NOT a standalone strategy.**

Best use: reduce V3 position size when skewness is strongly negative (crash risk). This is a risk-management signal, not an alpha signal. The 70/30 allocation with V3 reduces max drawdown by ~15 percentage points while giving up only ~10pp of annual return.

**Next steps if pursuing:**
- Test as V3 overlay: reduce V3 position by 50% when 30d skew < -0.5
- Compare with VRP overlay: correlation between skew signal and VRP to check redundancy
- Extend WF to 2024-2025 data (current WF ends at 2023-Q1)