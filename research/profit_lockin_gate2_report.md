# Profit Lock-in Overlay — Gate 2 Regime Persistence Report

_Generated 2026-04-07T16:14:40.572341+00:00_

**Verdict: OVERFIT**

Setup: 60-month s523c_growth backtest sliced into 6-month rolling windows 
(3-month step). For each window: baseline trades vs walk-forward expanding-
window velocity threshold overlay using LIMIT-FILL model. Threshold pool is 
seeded with all trades whose exit precedes each window-trade entry (no leakage).

Trades enriched: 511 (missing OHLC: 1002). 
Windows analysed: 17.

## Rolling Window Results (VEL95 + limit fill)

| Window Start | Window End | n | BTC Ret % | BTC Vol | Base Calmar | Ovl Calmar | ΔCalmar % | Fired |
|--------------|------------|---:|----------:|--------:|------------:|-----------:|----------:|------:|
| 2021-10-02 | 2022-03-31 | 31 | -3.2 | 0.64 | 1.13 | 1.06 | -6.5 | 2 |
| 2021-12-31 | 2022-06-29 | 45 | -57.7 | 0.72 | 0.67 | 1.05 | 57.5 | 19 |
| 2022-03-31 | 2022-09-27 | 47 | -56.5 | 0.69 | 0.22 | 0.14 | -35.7 | 28 |
| 2022-06-29 | 2022-12-26 | 43 | -16.0 | 0.56 | 1.98 | 2.98 | 50.8 | 24 |
| 2022-09-27 | 2023-03-26 | 59 | 45.5 | 0.51 | -0.37 | -0.02 | -95.0 | 30 |
| 2022-12-26 | 2023-06-24 | 76 | 80.6 | 0.48 | 0.60 | 1.18 | 96.3 | 21 |
| 2023-03-26 | 2023-09-22 | 74 | -3.9 | 0.38 | 1.24 | 1.13 | -8.5 | 11 |
| 2023-06-24 | 2023-12-21 | 74 | 43.8 | 0.37 | -0.46 | -0.40 | -13.2 | 14 |
| 2023-09-22 | 2024-03-20 | 73 | 139.4 | 0.48 | 0.40 | 0.38 | -4.1 | 16 |
| 2023-12-21 | 2024-06-18 | 64 | 48.0 | 0.52 | 4.04 | 2.50 | -38.1 | 17 |
| 2024-03-20 | 2024-09-16 | 56 | -8.4 | 0.53 | 0.58 | -0.19 | -133.6 | 13 |
| 2024-06-18 | 2024-12-15 | 60 | 58.8 | 0.52 | -0.14 | -0.68 | 366.1 | 17 |
| 2024-09-16 | 2025-03-15 | 62 | 45.9 | 0.54 | -0.68 | -0.90 | 32.5 | 24 |
| 2024-12-15 | 2025-06-13 | 54 | 2.2 | 0.51 | -0.49 | -0.44 | -10.0 | 22 |
| 2025-03-15 | 2025-09-11 | 49 | 35.4 | 0.38 | 8.27 | 10.83 | 30.9 | 17 |
| 2025-06-13 | 2025-12-10 | 55 | -13.0 | 0.38 | 6.65 | 6.07 | -8.6 | 18 |
| 2025-09-11 | 2026-03-10 | 53 | -37.7 | 0.47 | 5.36 | 2.47 | -54.0 | 22 |

## Robustness Summary

| Threshold | n Win | Mean Δ% | Median Δ% | Stdev | Min | Max | Loss wins | Severe (<-20%) |
|-----------|------:|--------:|----------:|------:|----:|----:|----------:|---------------:|
| VEL90 | 17 | -61.8 | -66.6 | 107.8 | -254.6 | 201.5 | 13 | 12 |
| VEL95 | 17 | 13.3 | -8.5 | 106.4 | -133.6 | 366.1 | 11 | 5 |
| VEL98 | 17 | -2.8 | -10.1 | 75.6 | -156.4 | 201.7 | 9 | 3 |

## Regime Correlation (VEL95)

- Pearson(VEL95 ΔCalmar, BTC return) = 0.248
- Pearson(VEL95 ΔCalmar, BTC realized vol) = 0.044

## Threshold Sensitivity (VEL90 vs VEL95 vs VEL98)

Best on mean ΔCalmar: **VEL95** (+13.3%)

Per-window best threshold tally:
- VEL90: best in 2 windows
- VEL95: best in 9 windows
- VEL98: best in 6 windows

## Verdict + Rationale

**OVERFIT**

VEL95 overlay fails to deliver +15% mean improvement across rolling windows 
when threshold is built walk-forward and fills are realistic. The original 
+166% claim was an artifact of (a) full-sample threshold (b) optimistic 
intrabar fills. **KILL Mission G.**

## Source data

- 60-mo trades: `research/gate2_out/s523c_growth_60mo_50k_trades.json`
- Raw results: `research/profit_lockin_gate2_results.json`
- This script: `research/profit_lockin_gate2.py`
