# V3 Seasonal Overlay Test Results

**Generated:** 2026-03-24 12:58
**Data range:** 2020-01-01 to 2026-03-14
**IS period:** 2020-09-01 to 2024-12-31
**OOS period:** 2025-01-01 to 2026-03-14

## Executive Summary

**0 PASS / 4 KILL** out of 4 seasonal variants tested.

- **V1_binary_month**: **KILL** -- MaxDD slightly worse OOS: -24.1% vs base -20.2% (-3.9%, within 5pp tolerance)
- **V2_proportional_month**: **KILL** -- MaxDD slightly worse OOS: -23.9% vs base -20.2% (-3.7%, within 5pp tolerance)
- **V3s_quarter_based**: **KILL** -- MaxDD slightly worse OOS: -23.7% vs base -20.2% (-3.5%, within 5pp tolerance)
- **V4_sell_in_may**: **KILL** -- No risk-adjusted benefit: Sharpe +0.258 vs +0.556, Calmar 0.38 vs 0.72

## Monthly Performance Validation (IS period)

Confirming R68 seasonality findings on V3 base strategy:

| Month | Ann Return | Win Rate | Ann Vol |
|-------|-----------|----------|---------|
| Jan | +54.9% | 5% | 65.6% |
| Feb | +615.4% | 5% | 55.3% |
| Mar | +424.8% | 6% | 51.2% |
| Apr | -65.2% | 4% | 57.1% |
| May | -35.0% | 2% | 25.6% |
| Jun | -40.1% | 2% | 17.0% |
| Jul | -23.4% | 1% | 13.6% |
| Aug | -35.8% | 2% | 42.2% |
| Sep | -24.0% | 2% | 30.2% |
| Oct | +224.8% | 5% | 31.5% |
| Nov | +104.3% | 7% | 62.6% |
| Dec | +113.6% | 5% | 33.7% |

## Point-in-Time Results (IS + OOS)

| Variant | IS Sharpe | IS Return | IS MaxDD | OOS Sharpe | OOS Return | OOS MaxDD | dSharpe OOS |
|---------|-----------|-----------|----------|------------|------------|-----------|-------------|
| V3_base | +0.902 | +39.4% | -54.2% | +0.556 | +14.7% | -20.2% | +0.000 |
| BuyHold | +0.995 | +61.7% | -76.6% | -0.462 | -21.1% | -49.5% | -1.017 |
| V1_binary_month | +2.112 | +77.6% | -37.2% | -0.395 | -7.4% | -24.1% | -0.951 |
| V2_proportional_month | +1.516 | +70.2% | -51.0% | +0.054 | +1.4% | -23.9% | -0.502 |
| V3s_quarter_based | +1.459 | +71.3% | -55.3% | +0.126 | +3.3% | -23.7% | -0.430 |
| V4_sell_in_may | +1.132 | +44.7% | -49.1% | +0.258 | +4.7% | -12.4% | -0.297 |

## Walk-Forward Results (6 windows, 18mo IS + 6mo OOS)

### V3_base

| Window | OOS Period | IS Sharpe | OOS Sharpe | OOS Return | OOS MaxDD | dSharpe vs Base |
|--------|-----------|-----------|------------|------------|-----------|-----------------|
| W1 | 2022-07-01..2022-12-31 | +0.817 | -1.482 | -48.7% | -31.5% | 0.000 |
| W2 | 2023-01-01..2023-06-30 | -0.570 | +0.569 | +17.9% | -16.3% | 0.000 |
| W3 | 2023-07-01..2023-12-31 | -0.889 | +0.771 | +22.2% | -16.0% | 0.000 |
| W4 | 2024-01-01..2024-06-30 | -0.313 | +1.264 | +61.8% | -27.9% | 0.000 |
| W5 | 2024-07-01..2024-12-31 | +0.872 | +0.365 | +10.7% | -23.5% | 0.000 |
| W6 | 2025-01-01..2025-06-30 | +0.809 | +1.613 | +52.0% | -9.9% | 0.000 |
| **Mean** | | | **+0.517** | | | **+0.000** |
| **Positive/Improved** | | | 5/6 pos | | | 0/6 improved |

### V1_binary_month

| Window | OOS Period | IS Sharpe | OOS Sharpe | OOS Return | OOS MaxDD | dSharpe vs Base |
|--------|-----------|-----------|------------|------------|-----------|-----------------|
| W1 | 2022-07-01..2022-12-31 | +1.192 | -1.191 | -34.9% | -22.7% | +0.291 |
| W2 | 2023-01-01..2023-06-30 | -0.542 | +1.769 | +36.1% | -9.4% | +1.200 |
| W3 | 2023-07-01..2023-12-31 | -0.240 | +2.281 | +56.9% | -6.0% | +1.510 |
| W4 | 2024-01-01..2024-06-30 | +0.456 | +5.235 | +202.4% | -12.0% | +3.971 |
| W5 | 2024-07-01..2024-12-31 | +2.965 | +3.148 | +73.4% | -7.5% | +2.783 |
| W6 | 2025-01-01..2025-06-30 | +3.416 | +0.672 | +15.5% | -9.5% | -0.941 |
| **Mean** | | | **+1.986** | | | **+1.469** |
| **Positive/Improved** | | | 5/6 pos | | | 5/6 improved |

### V2_proportional_month

| Window | OOS Period | IS Sharpe | OOS Sharpe | OOS Return | OOS MaxDD | dSharpe vs Base |
|--------|-----------|-----------|------------|------------|-----------|-----------------|
| W1 | 2022-07-01..2022-12-31 | +1.629 | -1.364 | -55.8% | -37.1% | +0.118 |
| W2 | 2023-01-01..2023-06-30 | -0.260 | +1.362 | +32.3% | -10.4% | +0.793 |
| W3 | 2023-07-01..2023-12-31 | -0.630 | +1.120 | +36.1% | -16.4% | +0.348 |
| W4 | 2024-01-01..2024-06-30 | -0.227 | +3.028 | +139.6% | -19.0% | +1.763 |
| W5 | 2024-07-01..2024-12-31 | +1.781 | +0.937 | +30.0% | -23.5% | +0.572 |
| W6 | 2025-01-01..2025-06-30 | +1.651 | +0.782 | +23.9% | -12.2% | -0.831 |
| **Mean** | | | **+0.977** | | | **+0.461** |
| **Positive/Improved** | | | 5/6 pos | | | 5/6 improved |

### V3s_quarter_based

| Window | OOS Period | IS Sharpe | OOS Sharpe | OOS Return | OOS MaxDD | dSharpe vs Base |
|--------|-----------|-----------|------------|------------|-----------|-----------------|
| W1 | 2022-07-01..2022-12-31 | +1.262 | -1.364 | -55.8% | -37.1% | +0.118 |
| W2 | 2023-01-01..2023-06-30 | -0.481 | +1.339 | +39.0% | -12.2% | +0.771 |
| W3 | 2023-07-01..2023-12-31 | -0.697 | +1.029 | +34.8% | -16.4% | +0.258 |
| W4 | 2024-01-01..2024-06-30 | -0.179 | +3.510 | +168.1% | -15.4% | +2.246 |
| W5 | 2024-07-01..2024-12-31 | +1.885 | +0.776 | +26.6% | -24.0% | +0.411 |
| W6 | 2025-01-01..2025-06-30 | +1.679 | +1.031 | +30.2% | -12.2% | -0.582 |
| **Mean** | | | **+1.054** | | | **+0.537** |
| **Positive/Improved** | | | 5/6 pos | | | 5/6 improved |

### V4_sell_in_may

| Window | OOS Period | IS Sharpe | OOS Sharpe | OOS Return | OOS MaxDD | dSharpe vs Base |
|--------|-----------|-----------|------------|------------|-----------|-----------------|
| W1 | 2022-07-01..2022-12-31 | +0.622 | -1.325 | -39.2% | -25.4% | +0.157 |
| W2 | 2023-01-01..2023-06-30 | -0.915 | +0.845 | +25.8% | -15.1% | +0.276 |
| W3 | 2023-07-01..2023-12-31 | -0.719 | +1.038 | +24.8% | -6.0% | +0.267 |
| W4 | 2024-01-01..2024-06-30 | -0.060 | +2.417 | +113.4% | -20.7% | +1.152 |
| W5 | 2024-07-01..2024-12-31 | +1.413 | +1.684 | +32.5% | -7.6% | +1.319 |
| W6 | 2025-01-01..2025-06-30 | +1.613 | +1.005 | +24.9% | -9.9% | -0.608 |
| **Mean** | | | **+0.944** | | | **+0.427** |
| **Positive/Improved** | | | 5/6 pos | | | 5/6 improved |

## Kill Criteria Evaluation

Criteria applied to each variant:
1. **Walk-forward**: < 4/6 windows show Sharpe improvement over base V3 -> KILL
2. **Overfit check**: PIT OOS positive but WF negative -> KILL
3. **MaxDD worsens >5pp** in PIT OOS -> KILL
4. **No risk-adjusted benefit**: both Sharpe AND Calmar worsen in PIT OOS -> KILL

### V1_binary_month: **KILL**

- MaxDD slightly worse OOS: -24.1% vs base -20.2% (-3.9%, within 5pp tolerance)
- No risk-adjusted benefit: Sharpe -0.395 vs +0.556, Calmar -0.30 vs 0.72
- NOTE: Walk-forward shows 5/6 windows improved (mean WF dSharpe=+1.469). PIT OOS kill may be period-specific.

### V2_proportional_month: **KILL**

- MaxDD slightly worse OOS: -23.9% vs base -20.2% (-3.7%, within 5pp tolerance)
- No risk-adjusted benefit: Sharpe +0.054 vs +0.556, Calmar 0.06 vs 0.72
- NOTE: Walk-forward shows 5/6 windows improved (mean WF dSharpe=+0.461). PIT OOS kill may be period-specific.

### V3s_quarter_based: **KILL**

- MaxDD slightly worse OOS: -23.7% vs base -20.2% (-3.5%, within 5pp tolerance)
- No risk-adjusted benefit: Sharpe +0.126 vs +0.556, Calmar 0.14 vs 0.72
- NOTE: Walk-forward shows 5/6 windows improved (mean WF dSharpe=+0.537). PIT OOS kill may be period-specific.

### V4_sell_in_may: **KILL**

- No risk-adjusted benefit: Sharpe +0.258 vs +0.556, Calmar 0.38 vs 0.72
- NOTE: Walk-forward shows 5/6 windows improved (mean WF dSharpe=+0.427). PIT OOS kill may be period-specific.

## Walk-Forward vs Point-in-Time Divergence

Several variants were killed by PIT OOS criteria but showed strong walk-forward performance. This divergence warrants analysis:

| Variant | WF Windows Improved | WF Mean dSharpe | PIT OOS dSharpe | Verdict |
|---------|--------------------|-----------------|-----------------|---------| 
| V1_binary_month | 5/6 | +1.469 | -0.951 | KILL |
| V2_proportional_month | 5/6 | +0.461 | -0.502 | KILL |
| V3s_quarter_based | 5/6 | +0.537 | -0.430 | KILL |
| V4_sell_in_may | 5/6 | +0.427 | -0.297 | KILL |

The PIT OOS period (2025-01-01 to 2026-03-14) is a single contiguous window that may not be representative. Walk-forward covers 6 diverse market regimes (bear, recovery, bull, chop). When WF strongly supports a variant but PIT OOS kills it, the PIT result is likely period-specific rather than structural.

## Conclusion

All variants are killed by PIT OOS criteria, but walk-forward analysis reveals a more nuanced picture:

- **V1_binary_month** shows 5/6 WF windows improved with mean dSharpe=+1.469
- **V2_proportional_month** shows 5/6 WF windows improved with mean dSharpe=+0.461
- **V3s_quarter_based** shows 5/6 WF windows improved with mean dSharpe=+0.537
- **V4_sell_in_may** shows 5/6 WF windows improved with mean dSharpe=+0.427

The PIT OOS period (2025-01-01-2026-03-14) happens to be a period where the base V3 strategy performed well. Seasonal overlays that reduce sizing in weak months naturally underperform when those months turn out not to be weak in a specific sample. This is the fundamental tension: seasonal patterns are probabilistic, not deterministic.

**Walk-forward evidence** suggests the seasonal effect is real and exploitable across diverse market conditions. However, **PIT OOS evidence** shows the overlay can hurt when applied to a period that doesn't exhibit the expected seasonal pattern.

**Recommendation:** The seasonal overlay is not ready for production. The walk-forward signal is encouraging but the risk of underperformance in non-seasonal periods is material. Consider a weaker version: conditional seasonal sizing that only activates when other signals (e.g., macro regime, VRP level) confirm the seasonal weakness, rather than a blanket calendar rule.
