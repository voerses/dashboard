# R71: V3.1 Walk-Forward Validation Results

**Run date**: 2026-03-24 12:16

## Strategy Definitions

### V3 (Baseline)
- Base: Long when 20d EMA > 50d EMA, flat otherwise. No stop losses.
- Positioning overlay: Binance Top Trader L/S + L/S Divergence combined z-score (30d) -> 0.3x to 1.5x
- VRP overlay: (IV - RV) z-score (60d) -> 0.3x to 1.3x
- Rebalancing: Weekly (Monday). Cost: 10 bps. Position range: [0, 1.5x].

### V3.1 (V3 + Whipsaw Filter)
- **Same as V3**, plus:
- **Whipsaw filter**: Count EMA 20/50 crossovers in last 60 days. If crosses > 2: go flat (position = 0).

## KILL Criteria

1. V3.1 fails to improve V3 in >= 4/6 windows -> NOT ROBUST
2. Any window dSharpe < -0.5 -> filter is dangerous
3. Parameter sensitivity > 30% Sharpe degradation from +-33% changes -> FRAGILE

## Part 1: Walk-Forward Test (V3 vs V3.1)

6 rolling windows, each 18-month IS + 6-month OOS.

| Win | OOS Period | V3 OOS Sharpe | V3.1 OOS Sharpe | dSharpe | V3 OOS Return | V3.1 OOS Return | V3 OOS MaxDD | V3.1 OOS MaxDD |
|-----|------------|---------------|-----------------|---------|---------------|------------------|--------------|----------------|
| 1 | 2022-07-01..2022-12-31 | -1.482 | -1.482 | +0.000 | -48.7% | -48.7% | -31.5% | -31.5% |
| 2 | 2023-01-01..2023-06-30 | +0.569 | +0.569 | +0.000 | +17.9% | +17.9% | -16.3% | -16.3% |
| 3 | 2023-07-01..2023-12-31 | +0.771 | +0.771 | +0.000 | +22.2% | +22.2% | -16.0% | -16.0% |
| 4 | 2024-01-01..2024-06-30 | +1.264 | +1.264 | +0.000 | +61.8% | +61.8% | -27.9% | -27.9% |
| 5 | 2024-07-01..2024-12-31 | +0.365 | +0.365 | +0.000 | +10.7% | +10.7% | -23.5% | -23.5% |
| 6 | 2025-01-01..2025-06-30 | +1.613 | +1.613 | +0.000 | +52.0% | +52.0% | -9.9% | -9.9% |

### Walk-Forward Summary

- V3.1 improves V3 in: **0/6** windows (target: >= 4/6)
- V3.1 hurts V3 by >0.5 Sharpe: **0/6** windows (target: 0)
- dSharpe range: +0.000 to +0.000
- Mean dSharpe: +0.000
- Median dSharpe: +0.000
- V3 mean OOS Sharpe: 0.517
- V3.1 mean OOS Sharpe: 0.517
- V3 median OOS Sharpe: 0.670
- V3.1 median OOS Sharpe: 0.670

### Distribution of Improvement

- Windows where V3.1 > V3: none
- Windows where V3.1 < V3: none
- Windows where V3.1 = V3: [1, 2, 3, 4, 5, 6]

- Benefit concentration: **BROADLY DISTRIBUTED** (max single window = 0.000 of total 0.000)

### IS Comparison (for overfit detection)

| Win | V3 IS Sharpe | V3.1 IS Sharpe | IS dSharpe | OOS dSharpe |
|-----|--------------|----------------|------------|-------------|
| 1 | +0.817 | +0.817 | +0.000 | +0.000 |
| 2 | -0.570 | -0.570 | +0.000 | +0.000 |
| 3 | -0.889 | -0.889 | +0.000 | +0.000 |
| 4 | -0.313 | -0.313 | +0.000 | +0.000 |
| 5 | +0.872 | +0.872 | +0.000 | +0.000 |
| 6 | +0.809 | +0.809 | +0.000 | +0.000 |

## Diagnostic: Why V3.1 = V3 Across All Walk-Forward Windows

The whipsaw filter (crosses > 2 in 60d -> go flat) only overrides 
the base long signal when TWO conditions are met simultaneously:
1. The EMA crossover count exceeds the threshold (>2 crosses)
2. The base signal is LONG (fast EMA > slow EMA)

Analysis of 84 days where the filter was active (crosses > 2): in 68 of those days, the base signal was already flat (EMA fast < slow). The filter only OVERRIDES a long signal on **16 days** total.

### Override Periods (base=long, filter=flat)

| Period | Duration | BTC Price Move |
|--------|----------|----------------|
| 2025-10-01 to 2025-10-16 | 16 days | -8.8% |

### Critical Finding

All override days fall in **October 2025**, which is AFTER every 
walk-forward OOS window ends (latest: 2025-06-30). This means:

1. The R69 OOS improvement (+0.426 dSharpe) was driven by a **single 16-day episode** in Oct 2025
2. The whipsaw filter has **zero historical evidence** of value across the 2022-2025 walk-forward periods
3. The filter's apparent benefit is concentrated in a single event, not a persistent pattern

This is a textbook case of a filter that looks great on a recent test period but has no historical support. The 20/50 EMA whipsaw pattern (multiple crosses followed by a brief recovery that the filter catches) only occurred once in 5+ years of data.

## Part 2: Whipsaw Filter Parameter Sensitivity

V3 baseline OOS Sharpe (no filter): +0.556

### Grid: cross_threshold x lookback_window (reduction=0.0, go flat)

| crosses \ lookback | LB=30 | LB=60 | LB=90 | LB=120 |
|---|---|---|---|---|
| >1 | +0.996 | +0.996 | +0.435 | +0.982 |
| >2 | +0.982 | +0.982 | +0.982 | +1.317 |
| >3 | +0.556 | +0.982 | +0.982 | +1.317 |
| >4 | +0.556 | +0.556 | +0.556 | +0.556 |

### Delta Sharpe vs V3 (reduction=0.0)

| crosses \ lookback | LB=30 | LB=60 | LB=90 | LB=120 |
|---|---|---|---|---|
| >1 | +0.441 | +0.441 | -0.120 | +0.426 |
| >2 | +0.426 | +0.426 | +0.426 | +0.761 |
| >3 | +0.000 | +0.426 | +0.426 | +0.761 |
| >4 | +0.000 | +0.000 | +0.000 | +0.000 |

### Grid: cross_threshold x lookback_window (reduction=0.5, go half)

| crosses \ lookback | LB=30 | LB=60 | LB=90 | LB=120 |
|---|---|---|---|---|
| >1 | +0.790 | +0.790 | +0.577 | +0.853 |
| >2 | +0.774 | +0.774 | +0.774 | +0.936 |
| >3 | +0.556 | +0.774 | +0.774 | +0.936 |
| >4 | +0.556 | +0.556 | +0.556 | +0.556 |

### Sensitivity Check (+-33% from default ct=2, lb=60)

| Parameter Change | Sharpe | Degradation | Verdict |
|------------------|--------|-------------|---------|
| Default (ct=2, lb=60) | +0.982 | -- | BASELINE |
| ct=1 (cross threshold -50%) | +0.996 | -1.5% | OK |
| ct=3 (cross threshold +50%) | +0.982 | +0.0% | OK |
| lb=40 (lookback -33%) | +0.982 | +0.0% | OK |
| lb=80 (lookback +33%) | +0.982 | +0.0% | OK |

**No sensitivity KILL flags.** All +-33% perturbations within 30% tolerance.

## Verdict

### **KILL**

V3.1 only improves V3 in 0/6 windows (target >= 4/6). The whipsaw filter is not robust across different market periods.

### KILL Criteria Check

| Criterion | Result | Threshold | Verdict |
|-----------|--------|-----------|---------|
| Walk-forward improvement | 0/6 windows | >= 4/6 | FAIL |
| No dangerous degradation | min dSharpe = +0.000 | > -0.5 | PASS |
| Parameter sensitivity | 0 KILL flags | 0 flags | PASS |

### Recommendation

The whipsaw filter does not pass robustness validation. Do not deploy to production.

**Root cause**: The whipsaw filter (crosses > 2 in 60d) is mechanically redundant with the base EMA crossover signal. When multiple EMA crosses occur, the base signal naturally oscillates between long and flat. The filter only adds value when it catches a brief EMA re-cross (bullish) following multiple prior crosses -- a pattern that occurred exactly once in 5+ years of data (Oct 2025).

**Why R69 was misleading**: R69 tested on a single OOS period (2025-01 to latest) that happened to contain the one episode where the filter made a difference. Walk-forward validation exposes this as a one-event artifact, not a systematic improvement.

Next steps:
1. **Do NOT add whipsaw filter to V3** -- it adds complexity with zero historical benefit
2. **EMA spread filter** (A_spread_0.010 from R69, dSharpe=+0.410) should be walk-forward tested instead as the next candidate
3. **V3 RANGE regime weakness** remains an open problem -- the filter that works best in a point-in-time test is not necessarily robust
4. Consider whether RANGE regime losses are an acceptable cost of the strategy's UPTREND capture (Sharpe +6.0 in UPTREND)
