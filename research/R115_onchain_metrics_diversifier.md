# R115 -- BTC On-Chain Metrics as Portfolio Diversifier for V3

**Date**: 2026-03-24
**Period**: 2024-03 to 2026-03 (725 days max, varies by source)
**Asset**: BTC spot
**Cost assumption**: 10 bps round-trip

## Objective

Test on-chain BTC metrics as diversifier signals for V3 (EMA trend-following).
On-chain data represents fundamentally different information (blockchain activity
vs price patterns), which should provide genuine decorrelation.

**Previous Finding #27**: BTC exchange netflow 5d sum IC=+0.149 (t=3.44). This
research performs DEEP validation of that result and extends to additional on-chain metrics.

## Data Sources

| Source | Period | Rows | Key Columns |
|--------|--------|------|-------------|
| Blockchain.com | 2024-03 to 2026-03 | 725 | tx_volume, addresses, transactions |
| Coinmetrics | 2024-09 to 2026-03 | 568 | NetFlowNtv, NetFlowUSD, FlowIn/Out |
| Santiment | 2025-06 to 2026-02 | 266 | exchange_balance, netflow |
| BTC Spot 1h | 2020-01 to 2026-03 | ~54k hourly | OHLCV |

**CAVEAT**: Short data history (725 days max). All conclusions should be treated as
preliminary. Santiment data (266 days) is flagged as insufficient for standalone conclusions.

## IC Scan (Spearman Rank Correlation)

| Signal | IC 1d | IC 3d | IC 7d | IC 14d | Max |IC| | N |
|--------|-------|-------|-------|--------|---------|---|
| composite_z_nf_heavy | +0.0671 | +0.1001 | **+0.1784** | **+0.1688** | 0.1784 | 280 |
| netflow_10d | +0.0459 | +0.0756 | **+0.1202** | **+0.1402** | 0.1402 | 542 |
| netflow_5d | +0.0367 | **+0.0864** | **+0.1022** | **+0.1344** | 0.1344 | 547 |
| netflow_20d | +0.0276 | +0.0375 | +0.0836 | **+0.1106** | 0.1106 | 532 |
| netflow_usd_5d | +0.0268 | +0.0648 | +0.0578 | +0.0751 | 0.0751 | 547 |
| exbal_change_14d | -0.0554 | -0.0418 | -0.0228 | +0.0737 | 0.0737 | 251 |
| txvol_mom_14d | -0.0210 | -0.0385 | -0.0736 | -0.0134 | 0.0736 | 693 |
| velocity_mom_14d | -0.0175 | -0.0324 | -0.0693 | -0.0112 | 0.0693 | 693 |
| addr_growth_7d | -0.0215 | -0.0356 | -0.0448 | -0.0566 | 0.0566 | 700 |
| netflow_usd_10d | +0.0265 | +0.0363 | +0.0510 | +0.0431 | 0.0510 | 542 |
| txvol_mom_7d | -0.0286 | -0.0017 | -0.0179 | -0.0506 | 0.0506 | 700 |
| velocity_mom_7d | -0.0245 | +0.0071 | -0.0156 | -0.0487 | 0.0487 | 700 |
| addr_growth_14d | -0.0289 | -0.0454 | -0.0369 | -0.0076 | 0.0454 | 693 |
| exbal_change_30d | -0.0358 | -0.0309 | -0.0450 | -0.0428 | 0.0450 | 235 |
| composite_z | -0.0362 | -0.0288 | -0.0047 | +0.0069 | 0.0362 | 647 |
| netflow_usd_20d | +0.0173 | +0.0113 | +0.0211 | +0.0119 | 0.0211 | 532 |

### IC Kill Results

- No signals killed by IC threshold

## V3 Correlation Analysis

| Signal | Pearson r | p-value | Signal Corr | N | Verdict |
|--------|-----------|---------|-------------|---|---------|
| composite_z_nf_heavy | -0.3874 | 0.0000 | +0.2342 | 281 | OK |
| netflow_10d | +0.2078 | 0.0000 | +0.4483 | 543 | OK |
| netflow_5d | +0.0582 | 0.1738 | +0.3164 | 548 | OK |
| netflow_20d | +0.1920 | 0.0000 | +0.5634 | 533 | OK |
| netflow_usd_5d | +0.0654 | 0.1265 | +0.3478 | 548 | OK |
| exbal_change_14d | -0.3877 | 0.0000 | -0.0527 | 251 | OK |
| txvol_mom_14d | -0.1179 | 0.0019 | +0.1290 | 694 | OK |
| velocity_mom_14d | -0.1425 | 0.0002 | +0.0631 | 694 | OK |
| addr_growth_7d | -0.1415 | 0.0002 | +0.0556 | 701 | OK |
| netflow_usd_10d | +0.2436 | 0.0000 | +0.4689 | 543 | OK |
| txvol_mom_7d | -0.1313 | 0.0005 | +0.0279 | 701 | OK |
| velocity_mom_7d | -0.1358 | 0.0003 | +0.0021 | 701 | OK |
| addr_growth_14d | -0.1554 | 0.0000 | +0.0556 | 694 | OK |
| exbal_change_30d | -0.1781 | 0.0062 | -0.0214 | 235 | OK |
| composite_z | -0.1441 | 0.0002 | +0.0893 | 648 | OK |
| netflow_usd_20d | +0.2181 | 0.0000 | +0.5446 | 533 | OK |

- No signals killed by V3 correlation threshold

## Rolling IC Stationarity Check (90-day window)

| Signal | Mean IC | Std IC | % Positive | Recent IC | Sign Stable |
|--------|---------|--------|------------|-----------|-------------|
| composite_z_nf_heavy | -0.0376 | 0.2094 | 40% | +0.0430 | **No** |
| netflow_10d | +0.0044 | 0.1929 | 60% | +0.1163 | **No** |
| netflow_5d | +0.0333 | 0.1869 | 59% | +0.1527 | **No** |
| netflow_20d | -0.0547 | 0.1505 | 45% | -0.0876 | Yes |
| netflow_usd_5d | +0.0097 | 0.1885 | 55% | +0.1380 | **No** |
| exbal_change_14d | -0.0298 | 0.0579 | 26% | -0.0354 | Yes |
| txvol_mom_14d | -0.1083 | 0.1247 | 21% | -0.0451 | Yes |
| velocity_mom_14d | -0.0841 | 0.1044 | 20% | -0.0095 | Yes |
| addr_growth_7d | -0.0712 | 0.1602 | 41% | +0.0659 | Yes |
| netflow_usd_10d | -0.0197 | 0.2028 | 54% | +0.1058 | **No** |
| txvol_mom_7d | -0.0665 | 0.1470 | 35% | -0.0514 | **No** |
| velocity_mom_7d | -0.0553 | 0.1394 | 36% | -0.0198 | **No** |
| addr_growth_14d | -0.0404 | 0.1826 | 50% | -0.1006 | **No** |
| exbal_change_30d | +0.0353 | 0.0581 | 81% | +0.0544 | Yes |
| composite_z | -0.0252 | 0.1692 | 47% | +0.0839 | **No** |
| netflow_usd_20d | -0.0838 | 0.1485 | 36% | -0.1090 | Yes |

## Walk-Forward Analysis (120d train / 90d test)

### composite_z_nf_heavy

**0/1 positive windows** | Mean OOS Sharpe: **-0.006** | Median: -0.006

| Window | Train Period | Test Period | Train Sharpe | OOS Sharpe | OOS Return | Params |
|--------|-------------|-------------|-------------|-----------|-----------|--------|
| W1 | 2025-03-27 to 2025-09-29 | 2025-09-30 to 2026-03-14 | 0.825 | -0.006 | -0.1% | thr=60, long_only |

**Verdict**: FLAGGED (insufficient WF windows)

### netflow_10d

**2/4 positive windows** | Mean OOS Sharpe: **0.487** | Median: 0.320

| Window | Train Period | Test Period | Train Sharpe | OOS Sharpe | OOS Return | Params |
|--------|-------------|-------------|-------------|-----------|-----------|--------|
| W1 | 2024-11-13 to 2025-03-12 | 2025-03-13 to 2025-06-10 | 1.386 | -0.538 | -6.4% | thr=50, bidirectional |
| W2 | 2025-02-11 to 2025-06-10 | 2025-06-11 to 2025-09-08 | 1.885 | +2.046 | 7.1% | thr=50, long_only |
| W3 | 2025-05-12 to 2025-09-08 | 2025-09-09 to 2025-12-07 | 2.248 | -0.739 | -4.9% | thr=50, long_only |
| W4 | 2025-08-10 to 2025-12-07 | 2025-12-08 to 2026-03-14 | 3.470 | +1.179 | 11.9% | thr=70, bidirectional |

**Verdict**: PASSED

### netflow_5d

**3/4 positive windows** | Mean OOS Sharpe: **1.004** | Median: 1.027

| Window | Train Period | Test Period | Train Sharpe | OOS Sharpe | OOS Return | Params |
|--------|-------------|-------------|-------------|-----------|-----------|--------|
| W1 | 2024-11-13 to 2025-03-12 | 2025-03-13 to 2025-06-10 | 0.202 | +2.200 | 12.5% | thr=60, long_only |
| W2 | 2025-02-11 to 2025-06-10 | 2025-06-11 to 2025-09-08 | -0.373 | +0.463 | 1.6% | thr=60, long_only |
| W3 | 2025-05-12 to 2025-09-08 | 2025-09-09 to 2025-12-07 | 2.523 | -0.237 | -1.3% | thr=60, long_only |
| W4 | 2025-08-10 to 2025-12-07 | 2025-12-08 to 2026-03-14 | 2.321 | +1.591 | 16.4% | thr=60, bidirectional |

**Verdict**: PASSED

### netflow_20d

**2/4 positive windows** | Mean OOS Sharpe: **0.021** | Median: 0.024

| Window | Train Period | Test Period | Train Sharpe | OOS Sharpe | OOS Return | Params |
|--------|-------------|-------------|-------------|-----------|-----------|--------|
| W1 | 2024-11-13 to 2025-03-12 | 2025-03-13 to 2025-06-10 | 1.440 | +0.667 | 6.5% | thr=50, bidirectional |
| W2 | 2025-02-11 to 2025-06-10 | 2025-06-11 to 2025-09-08 | 1.636 | -1.193 | -4.3% | thr=50, long_only |
| W3 | 2025-05-12 to 2025-09-08 | 2025-09-09 to 2025-12-07 | 0.782 | -0.618 | -4.4% | thr=40, long_only |
| W4 | 2025-08-10 to 2025-12-07 | 2025-12-08 to 2026-03-14 | 1.712 | +1.228 | 13.4% | thr=50, bidirectional |

**Verdict**: KILLED (mean OOS Sharpe < 0.3)

### netflow_usd_5d

**3/4 positive windows** | Mean OOS Sharpe: **0.833** | Median: 0.380

| Window | Train Period | Test Period | Train Sharpe | OOS Sharpe | OOS Return | Params |
|--------|-------------|-------------|-------------|-----------|-----------|--------|
| W1 | 2024-11-13 to 2025-03-12 | 2025-03-13 to 2025-06-10 | 3.732 | -0.743 | -4.5% | thr=70, long_only |
| W2 | 2025-02-11 to 2025-06-10 | 2025-06-11 to 2025-09-08 | -0.519 | +0.498 | 1.7% | thr=70, long_only |
| W3 | 2025-05-12 to 2025-09-08 | 2025-09-09 to 2025-12-07 | 1.879 | +0.261 | 1.2% | thr=70, long_only |
| W4 | 2025-08-10 to 2025-12-07 | 2025-12-08 to 2026-03-14 | 3.317 | +3.318 | 29.1% | thr=40, bidirectional |

**Verdict**: PASSED

### exbal_change_14d

**1/1 positive windows** | Mean OOS Sharpe: **0.422** | Median: 0.422

| Window | Train Period | Test Period | Train Sharpe | OOS Sharpe | OOS Return | Params |
|--------|-------------|-------------|-------------|-----------|-----------|--------|
| W1 | 2025-07-27 to 2025-11-23 | 2025-11-24 to 2026-02-21 | -1.723 | +0.422 | 5.2% | thr=40, bidirectional |

**Verdict**: FLAGGED (insufficient WF windows)

### txvol_mom_14d

**2/6 positive windows** | Mean OOS Sharpe: **0.229** | Median: -0.571

| Window | Train Period | Test Period | Train Sharpe | OOS Sharpe | OOS Return | Params |
|--------|-------------|-------------|-------------|-----------|-----------|--------|
| W1 | 2024-05-11 to 2024-09-09 | 2024-09-10 to 2024-12-10 | -1.442 | +4.385 | 23.9% | thr=70, long_only |
| W2 | 2024-08-11 to 2024-12-10 | 2024-12-11 to 2025-03-10 | 3.611 | -1.065 | -10.5% | thr=70, long_only |
| W3 | 2024-11-09 to 2025-03-10 | 2025-03-11 to 2025-06-08 | 0.642 | -1.884 | -11.0% | thr=70, long_only |
| W4 | 2025-02-09 to 2025-06-08 | 2025-06-09 to 2025-09-08 | -0.819 | -1.579 | -8.5% | thr=60, long_only |
| W5 | 2025-05-10 to 2025-09-08 | 2025-09-09 to 2025-12-07 | -1.188 | +1.591 | 7.1% | thr=70, long_only |
| W6 | 2025-08-10 to 2025-12-07 | 2025-12-08 to 2026-03-14 | 1.768 | -0.077 | -0.8% | thr=70, bidirectional |

**Verdict**: KILLED (mean OOS Sharpe < 0.3)

### velocity_mom_14d

**2/6 positive windows** | Mean OOS Sharpe: **-0.504** | Median: -0.811

| Window | Train Period | Test Period | Train Sharpe | OOS Sharpe | OOS Return | Params |
|--------|-------------|-------------|-------------|-----------|-----------|--------|
| W1 | 2024-05-11 to 2024-09-09 | 2024-09-10 to 2024-12-10 | -1.160 | +1.679 | 12.8% | thr=70, bidirectional |
| W2 | 2024-08-11 to 2024-12-10 | 2024-12-11 to 2025-03-10 | 4.422 | -1.247 | -12.8% | thr=70, long_only |
| W3 | 2024-11-09 to 2025-03-10 | 2025-03-11 to 2025-06-08 | 0.565 | -1.909 | -11.1% | thr=70, long_only |
| W4 | 2025-02-09 to 2025-06-08 | 2025-06-09 to 2025-09-08 | -0.833 | -1.439 | -7.7% | thr=60, long_only |
| W5 | 2025-05-10 to 2025-09-08 | 2025-09-09 to 2025-12-07 | -1.075 | +0.268 | 1.5% | thr=70, long_only |
| W6 | 2025-08-10 to 2025-12-07 | 2025-12-08 to 2026-03-14 | 1.555 | -0.375 | -5.6% | thr=50, bidirectional |

**Verdict**: KILLED (mean OOS Sharpe < 0.3)

### addr_growth_7d

**2/6 positive windows** | Mean OOS Sharpe: **0.270** | Median: -0.444

| Window | Train Period | Test Period | Train Sharpe | OOS Sharpe | OOS Return | Params |
|--------|-------------|-------------|-------------|-----------|-----------|--------|
| W1 | 2024-05-11 to 2024-09-07 | 2024-09-08 to 2024-12-06 | 1.710 | +3.650 | 18.9% | thr=70, long_only |
| W2 | 2024-08-09 to 2024-12-06 | 2024-12-07 to 2025-03-06 | 2.254 | -0.554 | -5.1% | thr=70, long_only |
| W3 | 2024-11-07 to 2025-03-06 | 2025-03-07 to 2025-06-04 | 1.677 | +1.133 | 9.2% | thr=40, long_only |
| W4 | 2025-02-05 to 2025-06-04 | 2025-06-05 to 2025-09-02 | 0.141 | -0.937 | -5.2% | thr=40, long_only |
| W5 | 2025-05-06 to 2025-09-02 | 2025-09-03 to 2025-12-07 | 0.223 | -1.336 | -11.9% | thr=50, long_only |
| W6 | 2025-08-04 to 2025-12-07 | 2025-12-08 to 2026-03-14 | 0.172 | -0.334 | -4.5% | thr=70, bidirectional |

**Verdict**: KILLED (mean OOS Sharpe < 0.3)

### netflow_usd_10d

**3/4 positive windows** | Mean OOS Sharpe: **0.470** | Median: 0.762

| Window | Train Period | Test Period | Train Sharpe | OOS Sharpe | OOS Return | Params |
|--------|-------------|-------------|-------------|-----------|-----------|--------|
| W1 | 2024-11-13 to 2025-03-12 | 2025-03-13 to 2025-06-10 | 1.200 | -0.668 | -6.4% | thr=60, bidirectional |
| W2 | 2025-02-11 to 2025-06-10 | 2025-06-11 to 2025-09-08 | 2.172 | +0.655 | 2.2% | thr=70, long_only |
| W3 | 2025-05-12 to 2025-09-08 | 2025-09-09 to 2025-12-07 | 1.772 | +0.868 | 8.0% | thr=40, bidirectional |
| W4 | 2025-08-10 to 2025-12-07 | 2025-12-08 to 2026-03-14 | 3.972 | +1.024 | 10.2% | thr=70, bidirectional |

**Verdict**: PASSED

### txvol_mom_7d

**2/6 positive windows** | Mean OOS Sharpe: **-0.676** | Median: -0.971

| Window | Train Period | Test Period | Train Sharpe | OOS Sharpe | OOS Return | Params |
|--------|-------------|-------------|-------------|-----------|-----------|--------|
| W1 | 2024-05-11 to 2024-09-09 | 2024-09-10 to 2024-12-10 | 1.193 | -1.470 | -26.0% | thr=40, bidirectional |
| W2 | 2024-08-11 to 2024-12-10 | 2024-12-11 to 2025-03-10 | 2.229 | -1.880 | -22.0% | thr=50, long_only |
| W3 | 2024-11-09 to 2025-03-10 | 2025-03-11 to 2025-06-08 | -0.344 | +0.496 | 3.1% | thr=60, long_only |
| W4 | 2025-02-09 to 2025-06-08 | 2025-06-09 to 2025-09-08 | 0.435 | -1.290 | -8.2% | thr=40, long_only |
| W5 | 2025-05-10 to 2025-09-08 | 2025-09-09 to 2025-12-07 | -0.834 | +0.741 | 4.9% | thr=40, long_only |
| W6 | 2025-08-10 to 2025-12-07 | 2025-12-08 to 2026-03-14 | 4.903 | -0.653 | -10.6% | thr=50, bidirectional |

**Verdict**: KILLED (mean OOS Sharpe < 0.3)

### velocity_mom_7d

**2/6 positive windows** | Mean OOS Sharpe: **-0.589** | Median: -1.051

| Window | Train Period | Test Period | Train Sharpe | OOS Sharpe | OOS Return | Params |
|--------|-------------|-------------|-------------|-----------|-----------|--------|
| W1 | 2024-05-11 to 2024-09-09 | 2024-09-10 to 2024-12-10 | 1.175 | -1.220 | -19.5% | thr=40, bidirectional |
| W2 | 2024-08-11 to 2024-12-10 | 2024-12-11 to 2025-03-10 | 2.840 | -1.658 | -16.9% | thr=60, long_only |
| W3 | 2024-11-09 to 2025-03-10 | 2025-03-11 to 2025-06-08 | 0.423 | +0.281 | 1.8% | thr=60, long_only |
| W4 | 2025-02-09 to 2025-06-08 | 2025-06-09 to 2025-09-08 | 1.161 | -1.523 | -10.2% | thr=40, long_only |
| W5 | 2025-05-10 to 2025-09-08 | 2025-09-09 to 2025-12-07 | -0.684 | +1.465 | 8.6% | thr=40, long_only |
| W6 | 2025-08-10 to 2025-12-07 | 2025-12-08 to 2026-03-14 | 2.422 | -0.881 | -12.6% | thr=70, bidirectional |

**Verdict**: KILLED (mean OOS Sharpe < 0.3)

### addr_growth_14d

**5/6 positive windows** | Mean OOS Sharpe: **2.398** | Median: 1.536

| Window | Train Period | Test Period | Train Sharpe | OOS Sharpe | OOS Return | Params |
|--------|-------------|-------------|-------------|-----------|-----------|--------|
| W1 | 2024-05-11 to 2024-09-07 | 2024-09-08 to 2024-12-06 | -1.146 | +9.554 | 46.6% | thr=40, long_only |
| W2 | 2024-08-09 to 2024-12-06 | 2024-12-07 to 2025-03-06 | 4.588 | +1.017 | 7.7% | thr=70, long_only |
| W3 | 2024-11-07 to 2025-03-06 | 2025-03-07 to 2025-06-04 | 2.402 | -1.159 | -7.3% | thr=70, long_only |
| W4 | 2025-02-05 to 2025-06-04 | 2025-06-05 to 2025-09-02 | -0.293 | +2.830 | 10.5% | thr=60, long_only |
| W5 | 2025-05-06 to 2025-09-02 | 2025-09-03 to 2025-12-07 | 1.940 | +2.054 | 10.8% | thr=60, long_only |
| W6 | 2025-08-04 to 2025-12-07 | 2025-12-08 to 2026-03-14 | 5.583 | +0.091 | 1.2% | thr=40, bidirectional |

**Verdict**: PASSED

### exbal_change_30d

**0/1 positive windows** | Mean OOS Sharpe: **-0.377** | Median: -0.377

| Window | Train Period | Test Period | Train Sharpe | OOS Sharpe | OOS Return | Params |
|--------|-------------|-------------|-------------|-----------|-----------|--------|
| W1 | 2025-07-27 to 2025-11-23 | 2025-11-24 to 2026-02-21 | -0.788 | -0.377 | -5.4% | thr=40, bidirectional |

**Verdict**: FLAGGED (insufficient WF windows)

### composite_z

**1/5 positive windows** | Mean OOS Sharpe: **-0.611** | Median: -0.883

| Window | Train Period | Test Period | Train Sharpe | OOS Sharpe | OOS Return | Params |
|--------|-------------|-------------|-------------|-----------|-----------|--------|
| W1 | 2024-08-15 to 2024-12-12 | 2024-12-13 to 2025-03-12 | 3.191 | -0.967 | -10.9% | thr=40, long_only |
| W2 | 2024-11-13 to 2025-03-12 | 2025-03-13 to 2025-06-10 | 0.446 | -2.339 | -18.9% | thr=70, bidirectional |
| W3 | 2025-02-11 to 2025-06-10 | 2025-06-11 to 2025-09-08 | -0.510 | -0.236 | -1.1% | thr=40, long_only |
| W4 | 2025-05-12 to 2025-09-08 | 2025-09-09 to 2025-12-07 | 0.154 | +1.370 | 7.5% | thr=60, long_only |
| W5 | 2025-08-10 to 2025-12-07 | 2025-12-08 to 2026-03-14 | 6.467 | -0.883 | -15.2% | thr=50, bidirectional |

**Verdict**: KILLED (mean OOS Sharpe < 0.3)

### netflow_usd_20d

**2/4 positive windows** | Mean OOS Sharpe: **0.147** | Median: 0.061

| Window | Train Period | Test Period | Train Sharpe | OOS Sharpe | OOS Return | Params |
|--------|-------------|-------------|-------------|-----------|-----------|--------|
| W1 | 2024-11-13 to 2025-03-12 | 2025-03-13 to 2025-06-10 | 3.111 | +0.978 | 9.2% | thr=50, bidirectional |
| W2 | 2025-02-11 to 2025-06-10 | 2025-06-11 to 2025-09-08 | 1.820 | +0.452 | 1.9% | thr=50, long_only |
| W3 | 2025-05-12 to 2025-09-08 | 2025-09-09 to 2025-12-07 | 1.194 | -0.513 | -3.4% | thr=50, long_only |
| W4 | 2025-08-10 to 2025-12-07 | 2025-12-08 to 2026-03-14 | 1.712 | -0.329 | -4.9% | thr=50, bidirectional |

**Verdict**: KILLED (mean OOS Sharpe < 0.3)

## Deep Netflow Validation (Finding #27 Confirmation)

### Lag Sensitivity
Testing whether the netflow signal survives realistic execution delays (1-3 day lags).
See console output for detailed lag-by-horizon IC values.

### Subsample Stability
- First half IC: -0.0857
- Second half IC: +0.2101
- Sign consistent: **False**

### Quintile Analysis
- Top quintile (outflow) vs bottom quintile (inflow) 7d return spread: **2.032%**
- Monotonic quintiles: **True**

### Bootstrap Confidence Interval
- 95% CI: [0.0189, 0.1862]
- CI excludes zero: **True**

## Portfolio Analysis (50/50 with V3)

### netflow_10d

| Metric | V3 Only | Signal Only | 50/50 Portfolio |
|--------|---------|-------------|-----------------|
| Sharpe | 0.381 | 0.285 | **0.501** |
| Ann. Return | 17.5% | 11.1% | 17.6% |
| Ann. Volatility | 46.0% | 38.8% | 35.1% |
| Max Drawdown | -40.6% | -34.0% | -26.2% |

Portfolio Sharpe improvement: **+0.120** (diversification benefit)

### netflow_5d

| Metric | V3 Only | Signal Only | 50/50 Portfolio |
|--------|---------|-------------|-----------------|
| Sharpe | 0.273 | -0.019 | **0.293** |
| Ann. Return | 12.6% | -0.8% | 10.0% |
| Ann. Volatility | 46.0% | 43.0% | 34.2% |
| Max Drawdown | -40.6% | -45.5% | -29.6% |

Portfolio Sharpe improvement: **+0.019** (diversification benefit)

### netflow_usd_5d

| Metric | V3 Only | Signal Only | 50/50 Portfolio |
|--------|---------|-------------|-----------------|
| Sharpe | 0.273 | 0.098 | **0.360** |
| Ann. Return | 12.6% | 4.5% | 12.9% |
| Ann. Volatility | 46.0% | 45.5% | 35.9% |
| Max Drawdown | -40.6% | -47.5% | -32.6% |

Portfolio Sharpe improvement: **+0.087** (diversification benefit)

### netflow_usd_10d

| Metric | V3 Only | Signal Only | 50/50 Portfolio |
|--------|---------|-------------|-----------------|
| Sharpe | 0.381 | 0.631 | **0.654** |
| Ann. Return | 17.5% | 24.9% | 24.1% |
| Ann. Volatility | 46.0% | 39.5% | 36.9% |
| Max Drawdown | -40.6% | -29.8% | -26.3% |

Portfolio Sharpe improvement: **+0.273** (diversification benefit)

### addr_growth_14d

| Metric | V3 Only | Signal Only | 50/50 Portfolio |
|--------|---------|-------------|-----------------|
| Sharpe | -0.514 | -0.262 | **-0.422** |
| Ann. Return | -24.4% | -12.2% | -13.6% |
| Ann. Volatility | 47.4% | 46.5% | 32.2% |
| Max Drawdown | -59.3% | -57.4% | -52.0% |

Portfolio Sharpe improvement: **+0.092** (diversification benefit)

### composite_z_nf_heavy

| Metric | V3 Only | Signal Only | 50/50 Portfolio |
|--------|---------|-------------|-----------------|
| Sharpe | 0.155 | -0.199 | **0.288** |
| Ann. Return | 8.0% | -6.1% | 7.0% |
| Ann. Volatility | 51.6% | 30.7% | 24.2% |
| Max Drawdown | -38.8% | -18.1% | -16.1% |

Portfolio Sharpe improvement: **+0.133** (diversification benefit)

### exbal_change_14d

| Metric | V3 Only | Signal Only | 50/50 Portfolio |
|--------|---------|-------------|-----------------|
| Sharpe | -0.276 | -0.535 | **-0.453** |
| Ann. Return | -11.9% | -22.6% | -13.5% |
| Ann. Volatility | 43.0% | 42.3% | 29.8% |
| Max Drawdown | -40.6% | -33.2% | -32.9% |

Portfolio Sharpe change: -0.177 (no diversification benefit)

### exbal_change_30d

| Metric | V3 Only | Signal Only | 50/50 Portfolio |
|--------|---------|-------------|-----------------|
| Sharpe | -0.347 | -0.508 | **-0.457** |
| Ann. Return | -15.2% | -21.9% | -15.7% |
| Ann. Volatility | 43.7% | 43.2% | 34.3% |
| Max Drawdown | -40.6% | -28.0% | -28.3% |

Portfolio Sharpe change: -0.110 (no diversification benefit)

## Final Summary

| Signal | Max |IC| | V3 Corr | WF Sharpe | WF Win/Total | Verdict |
|--------|---------|---------|-----------|-------------|---------|
| composite_z_nf_heavy | 0.1784 | -0.387 | -0.006 | 0/1 | FLAGGED |
| netflow_10d | 0.1402 | +0.208 | 0.487 | 2/4 | **PASSED** |
| netflow_5d | 0.1344 | +0.058 | 1.004 | 3/4 | **PASSED** |
| netflow_20d | 0.1106 | +0.192 | 0.021 | 2/4 | KILLED (WF) |
| netflow_usd_5d | 0.0751 | +0.065 | 0.833 | 3/4 | **PASSED** |
| exbal_change_14d | 0.0737 | -0.388 | 0.422 | 1/1 | FLAGGED |
| txvol_mom_14d | 0.0736 | -0.118 | 0.229 | 2/6 | KILLED (WF) |
| velocity_mom_14d | 0.0693 | -0.142 | -0.504 | 2/6 | KILLED (WF) |
| addr_growth_7d | 0.0566 | -0.142 | 0.270 | 2/6 | KILLED (WF) |
| netflow_usd_10d | 0.0510 | +0.244 | 0.470 | 3/4 | **PASSED** |
| txvol_mom_7d | 0.0506 | -0.131 | -0.676 | 2/6 | KILLED (WF) |
| velocity_mom_7d | 0.0487 | -0.136 | -0.589 | 2/6 | KILLED (WF) |
| addr_growth_14d | 0.0454 | -0.155 | 2.398 | 5/6 | **PASSED** |
| exbal_change_30d | 0.0450 | -0.178 | -0.377 | 0/1 | FLAGGED |
| composite_z | 0.0362 | -0.144 | -0.611 | 1/5 | KILLED (WF) |
| netflow_usd_20d | 0.0211 | +0.218 | 0.147 | 2/4 | KILLED (WF) |

## Kill Criteria Assessment

| Criterion | Threshold | Result | Status |
|-----------|-----------|--------|--------|
| IC across all horizons | < 0.02 all signals | Best: 0.1784 | PASS |
| WF windows available | < 3 | Max: 6 | PASS |
| WF mean Sharpe | < 0.3 | Best: 2.398 | PASS |
| V3 correlation | > 0.5 | Max: 0.388 | PASS |

## Conclusions

**5 signal(s) passed all kill criteria:**
- **netflow_10d**: WF Sharpe=0.487, 2/4 positive windows
- **netflow_5d**: WF Sharpe=1.004, 3/4 positive windows
- **netflow_usd_5d**: WF Sharpe=0.833, 3/4 positive windows
- **netflow_usd_10d**: WF Sharpe=0.470, 3/4 positive windows
- **addr_growth_14d**: WF Sharpe=2.398, 5/6 positive windows

**3 signal(s) flagged for insufficient data:**
- **composite_z_nf_heavy**: 1 WF windows, Sharpe=-0.006
- **exbal_change_14d**: 1 WF windows, Sharpe=0.422
- **exbal_change_30d**: 1 WF windows, Sharpe=-0.377

### Finding #27 Deep Validation

The exchange netflow signal (5d sum) shows MIXED results in deep validation:
- Bootstrap 95% CI excludes zero: True
- Subsample sign consistency: False
- Monotonic quintiles: True
- Quintile return spread: 2.032%

### Caveats

1. **Short history**: 725 days maximum (Blockchain.com), 568 days (Coinmetrics), 
   266 days (Santiment). All results are preliminary.
2. **Limited WF windows**: At best ~4 windows of 120d/90d. This is below the 
   10-window standard used in other research (R109).
3. **Finding #27 context**: The original IC=+0.149 was for BTC only; ETH showed 
   INVERTED relationship. Do not generalize across assets.
4. **On-chain data quality**: Blockchain.com and Coinmetrics may measure different 
   things (e.g., different exchange coverage). Results are source-dependent.

### Next Steps

1. Monitor signals that passed/flagged with additional data as it accumulates
2. Re-run with 12+ months of additional data for stronger WF conclusions
3. Test portfolio weights other than 50/50 (e.g., risk-parity)
4. Investigate whether netflow signal adds value as a FILTER for V3 entries
   (rather than standalone signal)