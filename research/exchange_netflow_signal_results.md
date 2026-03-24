# Exchange Netflow Signal Analysis Results

**Run date:** 2026-03-24 10:31:44

**Script:** `research/exchange_netflow_signal_test.py`

## Hypothesis
Large net inflows to exchanges = selling pressure = bearish for price.
Net outflows = accumulation = bullish for price.

Signals are **negated** so that positive signal = bullish (outflow-dominated).
A positive IC confirms the hypothesis.

## Data Sources
| Source | Assets | Date Range | Obs |
|--------|--------|------------|-----|
| coinmetrics | BTC, ETH | see below | 530-562 |
| santiment | BTC, ETH | see below | 247-266 |
| ensemble | BTC, ETH | see below | 511-543 |

## Signal Definitions
| Signal | Description |
|--------|-------------|
| `netflow_raw` | -(inflow - outflow) in native units |
| `netflow_usd` | -(inflow - outflow) in USD (CoinMetrics only) |
| `netflow_zscore_20d` | -z-score of netflow over 20d rolling window |
| `netflow_5d_sum` | -cumulative netflow over 5 days |
| `netflow_direction` | -sign(netflow): +1 if outflow day, -1 if inflow day |
| `ensemble_*` | Average z-scored signal across CoinMetrics + Santiment |

## Pass Criteria
- |IC| > 0.05
- |t-stat| > 2.0
- Sign consistent IS -> OOS

## Full-Sample IC Results

| Asset | Source | Signal | Horizon | IC | t-stat | p-value | N | Hit Rate |
|-------|--------|--------|---------|---:|-------:|--------:|--:|---------:|
| BTC | coinmetrics | netflow_5d_sum | 14d | **0.1311** | **3.08** | 0.0022 | 545 | 56.1% |
| BTC | coinmetrics | netflow_5d_sum | 1d | 0.0042 | 0.10 | 0.9214 | 558 | 51.3% |
| BTC | coinmetrics | netflow_5d_sum | 3d | 0.0551 | 1.30 | 0.1944 | 556 | 55.4% |
| BTC | coinmetrics | netflow_5d_sum | 7d | **0.0967** | **2.28** | 0.0231 | 552 | 57.6% |
| BTC | coinmetrics | netflow_direction | 14d | 0.0282 | 0.66 | 0.5093 | 549 | 52.3% |
| BTC | coinmetrics | netflow_direction | 1d | -0.0462 | -1.09 | 0.2740 | 562 | 49.1% |
| BTC | coinmetrics | netflow_direction | 3d | -0.0400 | -0.95 | 0.3446 | 560 | 50.7% |
| BTC | coinmetrics | netflow_direction | 7d | 0.0415 | 0.98 | 0.3289 | 556 | 54.9% |
| BTC | coinmetrics | netflow_raw | 14d | 0.0645 | 1.51 | 0.1314 | 549 | 52.3% |
| BTC | coinmetrics | netflow_raw | 1d | -0.0302 | -0.72 | 0.4749 | 562 | 49.1% |
| BTC | coinmetrics | netflow_raw | 3d | -0.0321 | -0.76 | 0.4486 | 560 | 50.7% |
| BTC | coinmetrics | netflow_raw | 7d | 0.0465 | 1.10 | 0.2739 | 556 | 54.9% |
| BTC | coinmetrics | netflow_usd | 14d | 0.0383 | 0.90 | 0.3705 | 549 | 52.3% |
| BTC | coinmetrics | netflow_usd | 1d | -0.0402 | -0.95 | 0.3410 | 562 | 49.1% |
| BTC | coinmetrics | netflow_usd | 3d | -0.0514 | -1.22 | 0.2249 | 560 | 50.7% |
| BTC | coinmetrics | netflow_usd | 7d | 0.0236 | 0.56 | 0.5783 | 556 | 54.9% |
| BTC | coinmetrics | netflow_zscore_20d | 14d | 0.0348 | 0.80 | 0.4236 | 530 | 49.8% |
| BTC | coinmetrics | netflow_zscore_20d | 1d | -0.0123 | -0.29 | 0.7753 | 543 | 49.9% |
| BTC | coinmetrics | netflow_zscore_20d | 3d | -0.0197 | -0.46 | 0.6482 | 541 | 48.8% |
| BTC | coinmetrics | netflow_zscore_20d | 7d | 0.0324 | 0.75 | 0.4534 | 537 | 51.8% |
| BTC | ensemble | ensemble_netflow_5d_sum | 14d | **0.1487** | **3.44** | 0.0006 | 526 | 54.8% |
| BTC | ensemble | ensemble_netflow_5d_sum | 1d | 0.0251 | 0.58 | 0.5602 | 539 | 51.6% |
| BTC | ensemble | ensemble_netflow_5d_sum | 3d | 0.0657 | 1.52 | 0.1285 | 537 | 50.3% |
| BTC | ensemble | ensemble_netflow_5d_sum | 7d | **0.0980** | **2.27** | 0.0236 | 533 | 53.3% |
| BTC | ensemble | ensemble_netflow_raw | 14d | 0.0567 | 1.30 | 0.1926 | 530 | 53.8% |
| BTC | ensemble | ensemble_netflow_raw | 1d | -0.0309 | -0.72 | 0.4724 | 543 | 48.8% |
| BTC | ensemble | ensemble_netflow_raw | 3d | -0.0379 | -0.88 | 0.3795 | 541 | 46.4% |
| BTC | ensemble | ensemble_netflow_raw | 7d | 0.0446 | 1.03 | 0.3020 | 537 | 52.3% |
| BTC | ensemble | ensemble_netflow_zscore_20d | 14d | 0.0142 | 0.32 | 0.7491 | 511 | 50.5% |
| BTC | ensemble | ensemble_netflow_zscore_20d | 1d | -0.0318 | -0.73 | 0.4677 | 524 | 48.7% |
| BTC | ensemble | ensemble_netflow_zscore_20d | 3d | -0.0271 | -0.62 | 0.5367 | 522 | 48.9% |
| BTC | ensemble | ensemble_netflow_zscore_20d | 7d | 0.0201 | 0.46 | 0.6483 | 518 | 51.5% |
| BTC | santiment | netflow_5d_sum | 14d | 0.0743 | 1.20 | 0.2304 | 262 | 50.8% |
| BTC | santiment | netflow_5d_sum | 1d | 0.0402 | 0.65 | 0.5170 | 262 | 44.7% |
| BTC | santiment | netflow_5d_sum | 3d | 0.0771 | 1.25 | 0.2137 | 262 | 51.9% |
| BTC | santiment | netflow_5d_sum | 7d | -0.0016 | -0.03 | 0.9797 | 262 | 51.1% |
| BTC | santiment | netflow_direction | 14d | -0.0007 | -0.01 | 0.9911 | 266 | 49.6% |
| BTC | santiment | netflow_direction | 1d | 0.0218 | 0.35 | 0.7236 | 266 | 51.5% |
| BTC | santiment | netflow_direction | 3d | -0.0583 | -0.95 | 0.3436 | 266 | 48.1% |
| BTC | santiment | netflow_direction | 7d | 0.0338 | 0.55 | 0.5836 | 266 | 50.8% |
| BTC | santiment | netflow_raw | 14d | 0.0432 | 0.70 | 0.4828 | 266 | 49.6% |
| BTC | santiment | netflow_raw | 1d | 0.0048 | 0.08 | 0.9374 | 266 | 51.5% |
| BTC | santiment | netflow_raw | 3d | -0.0238 | -0.39 | 0.6987 | 266 | 48.1% |
| BTC | santiment | netflow_raw | 7d | 0.0471 | 0.77 | 0.4441 | 266 | 50.8% |
| BTC | santiment | netflow_zscore_20d | 14d | 0.1025 | 1.61 | 0.1082 | 247 | 55.1% |
| BTC | santiment | netflow_zscore_20d | 1d | -0.0003 | -0.01 | 0.9958 | 247 | 52.6% |
| BTC | santiment | netflow_zscore_20d | 3d | -0.0323 | -0.51 | 0.6135 | 247 | 48.2% |
| BTC | santiment | netflow_zscore_20d | 7d | 0.0301 | 0.47 | 0.6383 | 247 | 51.4% |
| ETH | coinmetrics | netflow_5d_sum | 14d | **-0.1122** | **-2.63** | 0.0088 | 545 | 45.5% |
| ETH | coinmetrics | netflow_5d_sum | 1d | -0.0200 | -0.47 | 0.6373 | 558 | 47.8% |
| ETH | coinmetrics | netflow_5d_sum | 3d | -0.0448 | -1.05 | 0.2922 | 556 | 48.6% |
| ETH | coinmetrics | netflow_5d_sum | 7d | **-0.1194** | **-2.82** | 0.0050 | 552 | 46.0% |
| ETH | coinmetrics | netflow_direction | 14d | 0.0367 | 0.86 | 0.3912 | 549 | 51.2% |
| ETH | coinmetrics | netflow_direction | 1d | -0.0490 | -1.16 | 0.2464 | 562 | 48.4% |
| ETH | coinmetrics | netflow_direction | 3d | 0.0012 | 0.03 | 0.9766 | 560 | 49.8% |
| ETH | coinmetrics | netflow_direction | 7d | 0.0292 | 0.69 | 0.4921 | 556 | 53.8% |
| ETH | coinmetrics | netflow_raw | 14d | 0.0103 | 0.24 | 0.8090 | 549 | 51.2% |
| ETH | coinmetrics | netflow_raw | 1d | -0.0594 | -1.41 | 0.1595 | 562 | 48.4% |
| ETH | coinmetrics | netflow_raw | 3d | -0.0096 | -0.23 | 0.8209 | 560 | 49.8% |
| ETH | coinmetrics | netflow_raw | 7d | -0.0011 | -0.03 | 0.9793 | 556 | 53.8% |
| ETH | coinmetrics | netflow_usd | 14d | 0.0089 | 0.21 | 0.8350 | 549 | 51.2% |
| ETH | coinmetrics | netflow_usd | 1d | -0.0561 | -1.33 | 0.1843 | 562 | 48.4% |
| ETH | coinmetrics | netflow_usd | 3d | -0.0086 | -0.20 | 0.8383 | 560 | 49.8% |
| ETH | coinmetrics | netflow_usd | 7d | -0.0033 | -0.08 | 0.9390 | 556 | 53.8% |
| ETH | coinmetrics | netflow_zscore_20d | 14d | **0.0903** | **2.08** | 0.0376 | 530 | 52.3% |
| ETH | coinmetrics | netflow_zscore_20d | 1d | -0.0272 | -0.63 | 0.5266 | 543 | 49.4% |
| ETH | coinmetrics | netflow_zscore_20d | 3d | 0.0156 | 0.36 | 0.7176 | 541 | 51.0% |
| ETH | coinmetrics | netflow_zscore_20d | 7d | 0.0623 | 1.44 | 0.1491 | 537 | 57.0% |
| ETH | ensemble | ensemble_netflow_5d_sum | 14d | 0.0279 | 0.64 | 0.5237 | 526 | 49.0% |
| ETH | ensemble | ensemble_netflow_5d_sum | 1d | 0.0087 | 0.20 | 0.8398 | 539 | 46.6% |
| ETH | ensemble | ensemble_netflow_5d_sum | 3d | -0.0006 | -0.01 | 0.9881 | 537 | 47.1% |
| ETH | ensemble | ensemble_netflow_5d_sum | 7d | -0.0209 | -0.48 | 0.6301 | 533 | 48.2% |
| ETH | ensemble | ensemble_netflow_raw | 14d | 0.0394 | 0.91 | 0.3649 | 530 | 50.0% |
| ETH | ensemble | ensemble_netflow_raw | 1d | -0.0185 | -0.43 | 0.6668 | 543 | 50.6% |
| ETH | ensemble | ensemble_netflow_raw | 3d | 0.0062 | 0.14 | 0.8856 | 541 | 50.1% |
| ETH | ensemble | ensemble_netflow_raw | 7d | 0.0162 | 0.37 | 0.7080 | 537 | 52.0% |
| ETH | ensemble | ensemble_netflow_zscore_20d | 14d | **0.0886** | **2.01** | 0.0454 | 511 | 52.6% |
| ETH | ensemble | ensemble_netflow_zscore_20d | 1d | -0.0279 | -0.64 | 0.5238 | 524 | 50.0% |
| ETH | ensemble | ensemble_netflow_zscore_20d | 3d | 0.0168 | 0.38 | 0.7023 | 522 | 50.6% |
| ETH | ensemble | ensemble_netflow_zscore_20d | 7d | 0.0530 | 1.21 | 0.2282 | 518 | 55.8% |
| ETH | santiment | netflow_5d_sum | 14d | -0.0162 | -0.26 | 0.7941 | 262 | 41.6% |
| ETH | santiment | netflow_5d_sum | 1d | 0.0129 | 0.21 | 0.8359 | 262 | 50.4% |
| ETH | santiment | netflow_5d_sum | 3d | -0.0404 | -0.65 | 0.5151 | 262 | 45.0% |
| ETH | santiment | netflow_5d_sum | 7d | -0.0348 | -0.56 | 0.5755 | 262 | 43.1% |
| ETH | santiment | netflow_direction | 14d | -0.0492 | -0.80 | 0.4245 | 266 | 49.6% |
| ETH | santiment | netflow_direction | 1d | -0.0991 | -1.62 | 0.1067 | 266 | 48.1% |
| ETH | santiment | netflow_direction | 3d | -0.0876 | -1.43 | 0.1544 | 266 | 47.7% |
| ETH | santiment | netflow_direction | 7d | -0.0846 | -1.38 | 0.1690 | 266 | 45.1% |
| ETH | santiment | netflow_raw | 14d | -0.0460 | -0.75 | 0.4550 | 266 | 49.6% |
| ETH | santiment | netflow_raw | 1d | -0.0617 | -1.00 | 0.3161 | 266 | 48.1% |
| ETH | santiment | netflow_raw | 3d | -0.0443 | -0.72 | 0.4720 | 266 | 47.7% |
| ETH | santiment | netflow_raw | 7d | -0.0529 | -0.86 | 0.3905 | 266 | 45.1% |
| ETH | santiment | netflow_zscore_20d | 14d | -0.0114 | -0.18 | 0.8590 | 247 | 51.4% |
| ETH | santiment | netflow_zscore_20d | 1d | -0.0160 | -0.25 | 0.8026 | 247 | 50.6% |
| ETH | santiment | netflow_zscore_20d | 3d | -0.0121 | -0.19 | 0.8501 | 247 | 49.8% |
| ETH | santiment | netflow_zscore_20d | 7d | -0.0336 | -0.53 | 0.5990 | 247 | 46.2% |

## IS / OOS Split Results

| Asset | Source | Signal | Horizon | IS IC | OOS IC | Consistent | Method |
|-------|--------|--------|---------|------:|-------:|:----------:|--------|
| BTC | coinmetrics | netflow_5d_sum | 14d | -0.0132 | 0.1828 | NO | temporal_split_70_30 |
| BTC | coinmetrics | netflow_5d_sum | 1d | -0.0611 | 0.0653 | NO | temporal_split_70_30 |
| BTC | coinmetrics | netflow_5d_sum | 3d | -0.0681 | 0.1590 | NO | temporal_split_70_30 |
| BTC | coinmetrics | netflow_5d_sum | 7d | -0.0438 | 0.1998 | NO | temporal_split_70_30 |
| BTC | coinmetrics | netflow_direction | 14d | -0.0264 | 0.0111 | NO | temporal_split_70_30 |
| BTC | coinmetrics | netflow_direction | 1d | -0.0691 | -0.0495 | YES | temporal_split_70_30 |
| BTC | coinmetrics | netflow_direction | 3d | -0.0698 | -0.0617 | YES | temporal_split_70_30 |
| BTC | coinmetrics | netflow_direction | 7d | -0.0137 | 0.0582 | NO | temporal_split_70_30 |
| BTC | coinmetrics | netflow_raw | 14d | -0.0091 | 0.0641 | NO | temporal_split_70_30 |
| BTC | coinmetrics | netflow_raw | 1d | -0.0442 | -0.0482 | YES | temporal_split_70_30 |
| BTC | coinmetrics | netflow_raw | 3d | -0.0502 | -0.0725 | YES | temporal_split_70_30 |
| BTC | coinmetrics | netflow_raw | 7d | -0.0002 | 0.0468 | NO | temporal_split_70_30 |
| BTC | coinmetrics | netflow_usd | 14d | -0.0435 | 0.0566 | NO | temporal_split_70_30 |
| BTC | coinmetrics | netflow_usd | 1d | -0.0580 | -0.0594 | YES | temporal_split_70_30 |
| BTC | coinmetrics | netflow_usd | 3d | -0.0790 | -0.0790 | YES | temporal_split_70_30 |
| BTC | coinmetrics | netflow_usd | 7d | -0.0299 | 0.0338 | NO | temporal_split_70_30 |
| BTC | coinmetrics | netflow_zscore_20d | 14d | -0.0027 | 0.0967 | NO | temporal_split_70_30 |
| BTC | coinmetrics | netflow_zscore_20d | 1d | -0.0063 | -0.0344 | YES | temporal_split_70_30 |
| BTC | coinmetrics | netflow_zscore_20d | 3d | -0.0147 | -0.0617 | YES | temporal_split_70_30 |
| BTC | coinmetrics | netflow_zscore_20d | 7d | 0.0201 | 0.0600 | YES | temporal_split_70_30 |
| BTC | ensemble | ensemble_netflow_5d_sum | 14d | 0.0766 | 0.1170 | YES | temporal_split_70_30 |
| BTC | ensemble | ensemble_netflow_5d_sum | 1d | 0.0011 | 0.0240 | YES | temporal_split_70_30 |
| BTC | ensemble | ensemble_netflow_5d_sum | 3d | 0.0029 | 0.0943 | YES | temporal_split_70_30 |
| BTC | ensemble | ensemble_netflow_5d_sum | 7d | 0.0370 | 0.0896 | YES | temporal_split_70_30 |
| BTC | ensemble | ensemble_netflow_raw | 14d | 0.0157 | 0.0374 | YES | temporal_split_70_30 |
| BTC | ensemble | ensemble_netflow_raw | 1d | -0.0246 | -0.0890 | YES | temporal_split_70_30 |
| BTC | ensemble | ensemble_netflow_raw | 3d | -0.0252 | -0.1379 | YES | temporal_split_70_30 |
| BTC | ensemble | ensemble_netflow_raw | 7d | 0.0289 | 0.0223 | YES | temporal_split_70_30 |
| BTC | ensemble | ensemble_netflow_zscore_20d | 14d | -0.0357 | 0.1408 | NO | temporal_split_70_30 |
| BTC | ensemble | ensemble_netflow_zscore_20d | 1d | -0.0225 | -0.0602 | YES | temporal_split_70_30 |
| BTC | ensemble | ensemble_netflow_zscore_20d | 3d | 0.0004 | -0.0927 | NO | temporal_split_70_30 |
| BTC | ensemble | ensemble_netflow_zscore_20d | 7d | 0.0391 | 0.0060 | YES | temporal_split_70_30 |
| BTC | santiment | netflow_5d_sum | 14d | 0.0416 | 0.1015 | YES | temporal_split_70_30 |
| BTC | santiment | netflow_5d_sum | 1d | -0.0032 | 0.1068 | NO | temporal_split_70_30 |
| BTC | santiment | netflow_5d_sum | 3d | 0.0421 | 0.0934 | YES | temporal_split_70_30 |
| BTC | santiment | netflow_5d_sum | 7d | -0.0263 | 0.0199 | NO | temporal_split_70_30 |
| BTC | santiment | netflow_direction | 14d | -0.0220 | -0.0284 | YES | temporal_split_70_30 |
| BTC | santiment | netflow_direction | 1d | 0.0467 | -0.0590 | NO | temporal_split_70_30 |
| BTC | santiment | netflow_direction | 3d | -0.0491 | -0.1373 | YES | temporal_split_70_30 |
| BTC | santiment | netflow_direction | 7d | 0.0619 | -0.1078 | NO | temporal_split_70_30 |
| BTC | santiment | netflow_raw | 14d | 0.0153 | 0.0639 | YES | temporal_split_70_30 |
| BTC | santiment | netflow_raw | 1d | 0.0222 | -0.0526 | NO | temporal_split_70_30 |
| BTC | santiment | netflow_raw | 3d | -0.0285 | -0.0541 | YES | temporal_split_70_30 |
| BTC | santiment | netflow_raw | 7d | 0.0727 | -0.0656 | NO | temporal_split_70_30 |
| BTC | santiment | netflow_zscore_20d | 14d | 0.0491 | 0.1485 | YES | temporal_split_70_30 |
| BTC | santiment | netflow_zscore_20d | 1d | 0.0002 | -0.0032 | NO | temporal_split_70_30 |
| BTC | santiment | netflow_zscore_20d | 3d | -0.0610 | -0.0368 | YES | temporal_split_70_30 |
| BTC | santiment | netflow_zscore_20d | 7d | 0.0373 | -0.0592 | NO | temporal_split_70_30 |
| ETH | coinmetrics | netflow_5d_sum | 14d | -0.0590 | -0.1419 | YES | temporal_split_70_30 |
| ETH | coinmetrics | netflow_5d_sum | 1d | -0.0192 | 0.0001 | NO | temporal_split_70_30 |
| ETH | coinmetrics | netflow_5d_sum | 3d | -0.0409 | -0.0081 | YES | temporal_split_70_30 |
| ETH | coinmetrics | netflow_5d_sum | 7d | -0.0815 | -0.1628 | YES | temporal_split_70_30 |
| ETH | coinmetrics | netflow_direction | 14d | 0.0682 | 0.0307 | YES | temporal_split_70_30 |
| ETH | coinmetrics | netflow_direction | 1d | -0.0735 | 0.0232 | NO | temporal_split_70_30 |
| ETH | coinmetrics | netflow_direction | 3d | -0.0165 | 0.0614 | NO | temporal_split_70_30 |
| ETH | coinmetrics | netflow_direction | 7d | 0.0208 | 0.0887 | YES | temporal_split_70_30 |
| ETH | coinmetrics | netflow_raw | 14d | 0.0374 | -0.0071 | NO | temporal_split_70_30 |
| ETH | coinmetrics | netflow_raw | 1d | -0.0498 | -0.0682 | YES | temporal_split_70_30 |
| ETH | coinmetrics | netflow_raw | 3d | -0.0068 | -0.0027 | YES | temporal_split_70_30 |
| ETH | coinmetrics | netflow_raw | 7d | 0.0056 | -0.0038 | NO | temporal_split_70_30 |
| ETH | coinmetrics | netflow_usd | 14d | 0.0391 | -0.0266 | NO | temporal_split_70_30 |
| ETH | coinmetrics | netflow_usd | 1d | -0.0462 | -0.0752 | YES | temporal_split_70_30 |
| ETH | coinmetrics | netflow_usd | 3d | -0.0070 | -0.0056 | YES | temporal_split_70_30 |
| ETH | coinmetrics | netflow_usd | 7d | 0.0039 | -0.0149 | NO | temporal_split_70_30 |
| ETH | coinmetrics | netflow_zscore_20d | 14d | 0.1084 | 0.0191 | YES | temporal_split_70_30 |
| ETH | coinmetrics | netflow_zscore_20d | 1d | -0.0101 | -0.0685 | YES | temporal_split_70_30 |
| ETH | coinmetrics | netflow_zscore_20d | 3d | 0.0250 | -0.0122 | NO | temporal_split_70_30 |
| ETH | coinmetrics | netflow_zscore_20d | 7d | 0.0674 | 0.0264 | YES | temporal_split_70_30 |
| ETH | ensemble | ensemble_netflow_5d_sum | 14d | -0.0168 | -0.2108 | YES | temporal_split_70_30 |
| ETH | ensemble | ensemble_netflow_5d_sum | 1d | -0.0042 | -0.0405 | YES | temporal_split_70_30 |
| ETH | ensemble | ensemble_netflow_5d_sum | 3d | -0.0280 | -0.0878 | YES | temporal_split_70_30 |
| ETH | ensemble | ensemble_netflow_5d_sum | 7d | -0.0490 | -0.2445 | YES | temporal_split_70_30 |
| ETH | ensemble | ensemble_netflow_raw | 14d | 0.0253 | -0.1072 | NO | temporal_split_70_30 |
| ETH | ensemble | ensemble_netflow_raw | 1d | -0.0165 | -0.0640 | YES | temporal_split_70_30 |
| ETH | ensemble | ensemble_netflow_raw | 3d | -0.0094 | -0.0252 | YES | temporal_split_70_30 |
| ETH | ensemble | ensemble_netflow_raw | 7d | 0.0036 | -0.0658 | NO | temporal_split_70_30 |
| ETH | ensemble | ensemble_netflow_zscore_20d | 14d | 0.1232 | -0.0213 | NO | temporal_split_70_30 |
| ETH | ensemble | ensemble_netflow_zscore_20d | 1d | -0.0358 | -0.0070 | YES | temporal_split_70_30 |
| ETH | ensemble | ensemble_netflow_zscore_20d | 3d | 0.0149 | 0.0047 | YES | temporal_split_70_30 |
| ETH | ensemble | ensemble_netflow_zscore_20d | 7d | 0.0611 | 0.0286 | YES | temporal_split_70_30 |
| ETH | santiment | netflow_5d_sum | 14d | 0.0289 | -0.3586 | NO | temporal_split_70_30 |
| ETH | santiment | netflow_5d_sum | 1d | 0.0546 | -0.1778 | NO | temporal_split_70_30 |
| ETH | santiment | netflow_5d_sum | 3d | -0.0007 | -0.3163 | YES | temporal_split_70_30 |
| ETH | santiment | netflow_5d_sum | 7d | -0.0400 | -0.1772 | YES | temporal_split_70_30 |
| ETH | santiment | netflow_direction | 14d | 0.0411 | -0.2699 | NO | temporal_split_70_30 |
| ETH | santiment | netflow_direction | 1d | -0.0722 | -0.1666 | YES | temporal_split_70_30 |
| ETH | santiment | netflow_direction | 3d | 0.0060 | -0.3161 | NO | temporal_split_70_30 |
| ETH | santiment | netflow_direction | 7d | 0.0328 | -0.3766 | NO | temporal_split_70_30 |
| ETH | santiment | netflow_raw | 14d | -0.0355 | -0.1788 | YES | temporal_split_70_30 |
| ETH | santiment | netflow_raw | 1d | -0.0461 | -0.1365 | YES | temporal_split_70_30 |
| ETH | santiment | netflow_raw | 3d | -0.0123 | -0.1962 | YES | temporal_split_70_30 |
| ETH | santiment | netflow_raw | 7d | -0.0333 | -0.2061 | YES | temporal_split_70_30 |
| ETH | santiment | netflow_zscore_20d | 14d | 0.0546 | -0.1432 | NO | temporal_split_70_30 |
| ETH | santiment | netflow_zscore_20d | 1d | 0.0152 | -0.0766 | NO | temporal_split_70_30 |
| ETH | santiment | netflow_zscore_20d | 3d | 0.0566 | -0.1418 | NO | temporal_split_70_30 |
| ETH | santiment | netflow_zscore_20d | 7d | 0.0327 | -0.1444 | NO | temporal_split_70_30 |

## Verdict

**5 signal(s) PASS all criteria:**

| Asset | Source | Signal | Horizon | IC | t-stat | IS IC | OOS IC |
|-------|--------|--------|---------|---:|-------:|------:|-------:|
| BTC | ensemble | ensemble_netflow_5d_sum | 14d | 0.1487 | 3.44 | 0.0766 | 0.1170 |
| ETH | coinmetrics | netflow_5d_sum | 7d | -0.1194 | -2.82 | -0.0815 | -0.1628 |
| ETH | coinmetrics | netflow_5d_sum | 14d | -0.1122 | -2.63 | -0.0590 | -0.1419 |
| BTC | ensemble | ensemble_netflow_5d_sum | 7d | 0.0980 | 2.27 | 0.0370 | 0.0896 |
| ETH | coinmetrics | netflow_zscore_20d | 14d | 0.0903 | 2.08 | 0.1084 | 0.0191 |

### Near-Miss Signals (|IC| > 0.03, |t-stat| > 1.5)

| Asset | Source | Signal | Horizon | IC | t-stat | Sign Consistent |
|-------|--------|--------|---------|---:|-------:|:---------------:|
| BTC | coinmetrics | netflow_5d_sum | 14d | 0.1311 | 3.08 | NO |
| BTC | santiment | netflow_zscore_20d | 14d | 0.1025 | 1.61 | YES |
| ETH | santiment | netflow_direction | 1d | -0.0991 | -1.62 | YES |
| BTC | coinmetrics | netflow_5d_sum | 7d | 0.0967 | 2.28 | NO |
| ETH | ensemble | ensemble_netflow_zscore_20d | 14d | 0.0886 | 2.01 | NO |
| BTC | ensemble | ensemble_netflow_5d_sum | 3d | 0.0657 | 1.52 | YES |
| BTC | coinmetrics | netflow_raw | 14d | 0.0645 | 1.51 | NO |

## Summary by Signal

**netflow_raw**: mean IC = -0.0090, median IC = -0.0167, % positive IC = 38%

**netflow_usd**: mean IC = -0.0111, median IC = -0.0059, % positive IC = 38%

**netflow_zscore_20d**: mean IC = 0.0127, median IC = -0.0059, % positive IC = 44%

**netflow_5d_sum**: mean IC = 0.0064, median IC = 0.0013, % positive IC = 50%

**netflow_direction**: mean IC = -0.0201, median IC = -0.0203, % positive IC = 44%

**ensemble_netflow_raw**: mean IC = 0.0095, median IC = 0.0112, % positive IC = 62%

**ensemble_netflow_zscore_20d**: mean IC = 0.0132, median IC = 0.0155, % positive IC = 62%

**ensemble_netflow_5d_sum**: mean IC = 0.0441, median IC = 0.0265, % positive IC = 75%

## Conclusions

**MIXED RESULT -- Partial pass with important caveats.**

5 signal variants pass all three criteria (|IC| > 0.05, |t-stat| > 2.0, sign consistent IS->OOS), but the results reveal an asset-dependent and conflicting picture:

### BTC: Hypothesis supported (partially)
- `ensemble_netflow_5d_sum` at 7d and 14d horizons shows positive IC (0.098, 0.149), confirming that sustained BTC outflows from exchanges predict higher prices. The signal is sign-consistent IS->OOS with the ensemble improving over single-source signals.
- However, the single-source CoinMetrics `netflow_5d_sum` (IC=0.131, t=3.08 at 14d) fails sign consistency -- it was negative in IS and positive only in OOS, suggesting a regime shift rather than stable alpha.

### ETH: Hypothesis INVERTED
- ETH `netflow_5d_sum` shows **negative** IC (-0.119 at 7d, -0.112 at 14d) with sign consistency. This means ETH net *inflows* to exchanges predict *higher* prices -- the opposite of the hypothesis. This likely reflects ETH-specific dynamics: inflows may represent staking deposits or DeFi activity rather than sell pressure.
- ETH `netflow_zscore_20d` at 14d passes (IC=0.09) in the hypothesized direction but with weak OOS IC (0.019), barely surviving the sign-consistency test.

### Key Observations
1. **The 5-day cumulative netflow (`netflow_5d_sum`) is the strongest signal variant** -- it outperforms raw, z-scored, and directional variants across both assets. The smoothing reduces noise in daily flow data.
2. **Longer horizons (7d, 14d) consistently outperform shorter ones (1d, 3d)** -- netflow is a slow-moving structural signal, not suitable for short-term timing.
3. **Ensemble signals (CoinMetrics + Santiment averaged) improve BTC results** by stabilizing noisy provider-specific measurement.
4. **Single-day raw netflow is essentially noise** (mean IC = -0.009) -- raw daily flows are too volatile to be predictive.
5. **BTC and ETH show opposite netflow-return relationships**, making a universal netflow signal problematic for a cross-asset strategy.

### Recommendation
- **BTC `ensemble_netflow_5d_sum`** is viable as a **secondary/confirming factor** in a BTC-specific multi-signal model, particularly at 7-14d horizons. IC of ~0.10-0.15 with sign consistency is respectable for an alternative data signal.
- **Do NOT use a universal netflow signal across BTC and ETH** -- the inverted relationship on ETH would cancel out BTC alpha.
- **ETH netflow requires separate treatment** -- the inverted signal could be exploited but needs further investigation (staking flows, DeFi activity confounders).
- Consider combining with funding rate and OI divergence signals where netflow provides independent information about on-chain positioning vs. derivatives positioning.