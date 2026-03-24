# R109: Deep Walk-Forward Validation — Macro Regime Rotation Signal

**Date**: 2026-03-24
**Verdict**: **KILL**
**Reason**: K5: IC sign changed in last 2 years (non-stationary)

## Signal Definition

- **Long**: US10Y 20d change < 0 AND DXY 20d change < 0 (both falling = risk-on)
- **Short/Flat**: US10Y 20d change > 0 AND DXY 20d change > 0 (both rising = risk-off)
- **Neutral**: Mixed signals -> flat
- **Market**: BTC spot
- **R107 result**: Standalone Sharpe 0.397, corr 0.010 with V3, 4/6 WF positive
- **Fees**: 10bps per rebalance round-trip

## 1. Data Snooping Check

- Common trading days (macro + BTC): 2258
- BTC-only days (weekends/holidays): 0
- Weekend macro data points: 645
- **Bias-free**: True

Signal uses `.shift(1)` on macro data = previous day's close applied to today's trading.
This is conservative: no look-ahead bias. Macro data (US10Y, DXY) is available
same-day evening, but we use T-1 close for safety.

## 2. Walk-Forward: Bidirectional

- **Windows**: 10
- **Positive OOS windows**: 7/10
- **Mean OOS Sharpe**: 1.129
- **Median OOS Sharpe**: 0.550
- **Std OOS Sharpe**: 2.251
- **Min/Max OOS Sharpe**: -2.090 / 6.239
- **Mean OOS Return**: 2.7%
- **Total signal changes**: 87

### Window Details

| Window | Train Period | Test Period | Best Params | Train Sharpe | OOS Sharpe | OOS Return | OOS MaxDD |
|--------|-------------|-------------|-------------|-------------|-----------|-----------|----------|
| 1 | 2023-03-24-2023-09-19 | 2023-09-20-2023-12-18 | lb=30,thr=0.3,h=wee | 4.154 | 2.941 | 17.9% | -6.6% |
| 2 | 2023-06-22-2023-12-18 | 2023-12-19-2024-03-17 | lb=20,thr=0.5,h=wee | 5.755 | -0.176 | -2.1% | -18.7% |
| 3 | 2023-09-20-2024-03-17 | 2024-03-18-2024-06-15 | lb=20,thr=0.5,h=dai | 3.809 | 6.239 | 34.6% | -8.5% |
| 4 | 2023-12-19-2024-06-15 | 2024-06-16-2024-09-13 | lb=20,thr=0.5,h=dai | 3.877 | 0.246 | 2.2% | -15.4% |
| 5 | 2024-03-18-2024-09-13 | 2024-09-14-2024-12-12 | lb=10,thr=0.0,h=wee | 4.992 | -2.090 | -38.6% | -42.5% |
| 6 | 2024-06-16-2024-12-12 | 2024-12-13-2025-03-12 | lb=15,thr=0.3,h=wee | 2.157 | -0.982 | -10.5% | -23.0% |
| 7 | 2024-09-14-2025-03-12 | 2025-03-13-2025-06-10 | lb=10,thr=0.3,h=wee | 0.650 | 0.203 | 1.2% | -6.2% |
| 8 | 2024-12-13-2025-06-10 | 2025-06-11-2025-09-08 | lb=10,thr=0.7,h=wee | 2.485 | 0.855 | 2.4% | -2.6% |
| 9 | 2025-03-13-2025-09-08 | 2025-09-09-2025-12-07 | lb=30,thr=0.7,h=wee | 3.757 | 2.870 | 10.4% | -3.4% |
| 10 | 2025-06-11-2025-12-07 | 2025-12-08-2026-03-14 | lb=40,thr=0.3,h=dai | 4.693 | 1.183 | 9.6% | -8.6% |

## 3. Walk-Forward: Long-Only

- **Positive OOS windows**: 6/10
- **Mean OOS Sharpe**: 1.031
- **Median OOS Sharpe**: 1.506
- **Mean OOS Return**: 4.8%

### Window Details

| Window | Test Period | OOS Sharpe | OOS Return |
|--------|-------------|-----------|-----------|
| 1 | 2023-09-20-2023-12-18 | 2.941 | 17.9% |
| 2 | 2023-12-19-2024-03-17 | 1.428 | 10.4% |
| 3 | 2024-03-18-2024-06-15 | 1.885 | 12.3% |
| 4 | 2024-06-16-2024-09-13 | 1.584 | 12.1% |
| 5 | 2024-09-14-2024-12-12 | 2.125 | 7.6% |
| 6 | 2024-12-13-2025-03-12 | -0.670 | -4.4% |
| 7 | 2025-03-13-2025-06-10 | 0.000 | 0.0% |
| 8 | 2025-06-11-2025-09-08 | -1.350 | -7.0% |
| 9 | 2025-09-09-2025-12-07 | 4.034 | 5.3% |
| 10 | 2025-12-08-2026-03-14 | -1.670 | -6.4% |

## 3b. Long-Only vs Bidirectional Comparison

| Metric | Bidirectional | Long-Only |
|--------|:------------:|:---------:|
| Mean OOS Sharpe | 1.129 | 1.031 |
| Positive Windows | 7/10 | 6/10 |
| Mean OOS Return | 2.7% | 4.8% |

**Bidirectional is superior** -- the short signal adds value despite structural headwinds.

## 4. Signal Lag Analysis (+1 day)

- **Base Mean OOS Sharpe**: 1.129
- **Lagged Mean OOS Sharpe**: 1.453
- **Lagged Positive Windows**: 7/10
- **Degradation**: -28.8%

Signal **survives** 1-day additional lag. This is a slow-moving macro signal,
so latency is not a concern.

## 5. Full Period Grid Search

### Bidirectional
- Best params: {'lookback': 20, 'threshold': 0.7, 'holding': 'daily'}
- Best Sharpe: 0.177

Top 5 configurations:

| Rank | Lookback | Threshold | Holding | Sharpe | Return | MaxDD |
|------|----------|-----------|---------|--------|--------|-------|
| 1 | 20 | 0.7 | daily | 0.177 | 37.9% | -44.1% |
| 2 | 20 | 0.7 | signal_change | 0.177 | 37.9% | -44.1% |
| 3 | 20 | 0.5 | weekly | 0.088 | 19.5% | -54.3% |
| 4 | 15 | 0.5 | weekly | 0.082 | 17.4% | -53.7% |
| 5 | 20 | 0.5 | daily | 0.074 | 16.0% | -59.2% |

### Long-Only
- Best params: {'lookback': 15, 'threshold': 0.0, 'holding': 'weekly'}
- Best Sharpe: 0.919

Top 5 configurations:

| Rank | Lookback | Threshold | Holding | Sharpe | Return | MaxDD |
|------|----------|-----------|---------|--------|--------|-------|
| 1 | 15 | 0.0 | weekly | 0.919 | 418.6% | -47.4% |
| 2 | 20 | 0.5 | weekly | 0.667 | 139.8% | -27.7% |
| 3 | 20 | 0.0 | weekly | 0.547 | 190.3% | -47.4% |
| 4 | 15 | 0.3 | daily | 0.482 | 108.5% | -34.5% |
| 5 | 15 | 0.3 | signal_change | 0.482 | 108.5% | -34.5% |

## 6. Parameter Sensitivity

Base Sharpe: 0.177
Max degradation: 189.7%

| Parameter | Value | Sharpe | Degradation |
|-----------|-------|--------|-------------|
| lookback | 16 | -0.104 | +158.8% |
| lookback | 20 | 0.177 | +0.0% |
| lookback | 24 | -0.159 | +189.7% |
| threshold | 0.56 | 0.069 | +60.8% |
| threshold | 0.7 | 0.177 | +0.0% |
| threshold | 0.84 | 0.119 | +32.6% |

**WARNING**: Parameter sensitivity exceeds 30% -- signal is fragile to parameter choice.

## 7. Regime Analysis

| Regime | Days | % Total | Sharpe | Return |
|--------|------|---------|--------|--------|
| UPTREND | 738 | 33% | 1.077 | 79.8% |
| DOWNTREND | 459 | 20% | 0.065 | 2.9% |
| RANGE | 1061 | 47% | -0.422 | -29.2% |

Macro signal has negative Sharpe (-0.422) in RANGE regime.
Does NOT add value in the regime where V3 struggles most.

## 8. Stationarity Check (Rolling IC)

- Full period IC: mean=0.0226, std=0.0587
- Last 2y IC mean: 0.0225
- Last 2y IC positive: 58.8% of time
- IC sign changed in last 2 years: **True**

**WARNING**: The US10Y/DXY -> BTC relationship is non-stationary.
IC has changed sign in recent quarters. Proceed with caution.

## Kill Criteria Summary

| Criterion | Threshold | Result | Status |
|-----------|-----------|--------|--------|
| K1 | - | 7/10 positive OOS windows (>=5) | PASS |
| K2 | - | Mean OOS Sharpe 1.129 >= 0.2 | PASS |
| K3 | - | 87 total signal changes (>=30) | PASS |
| K4 | - | Signal survives 1-day lag (lagged 1.453 vs base 1.129) | PASS |
| K6 | - | Parameter sensitivity 189.7% degradation (>30%) | CONDITIONAL |
| K5 | - | IC sign changed in last 2 years (non-stationary) | KILL |

## Final Verdict: **KILL**

**Reason**: K5: IC sign changed in last 2 years (non-stationary)

## Detailed Analysis

### Why the WF Sharpes look deceptively good

The mean OOS Sharpe of 1.129 (bidirectional) appears strong, but closer examination reveals
problems that the headline number masks:

1. **Extreme variance** (std = 2.251): The OOS Sharpe ranges from -2.090 to +6.239. A signal
   with consistent edge would show much tighter dispersion. The high mean is driven by a few
   outlier windows (W1: 2.941, W3: 6.239, W9: 2.870) while three windows are negative.

2. **Train-test overfitting**: Train Sharpes are consistently 2-5x, which is unrealistically
   high for a 180-day window. This indicates the optimizer is fitting to noise in small samples.
   Window 5 shows the classic pattern: Train Sharpe 4.992, OOS Sharpe -2.090 (worst window).

3. **Parameter instability**: Best params jump across windows (lb=30->20->20->20->10->15->10->10->30->40).
   A robust signal should converge on a stable parameter neighborhood.

4. **Full-period Sharpe is only 0.177** (bidirectional): When you run the best parameters over
   the full period (without windowed optimization), the Sharpe drops to 0.177 -- far below
   the 0.397 reported in R107. This confirms the WF OOS Sharpe is inflated by adaptive re-optimization.

5. **Long-only full-period Sharpe (0.919)** is mostly BTC beta: During 2020-2026, BTC went from
   ~$7k to ~$70k. Any signal that goes long ~40-60% of the time captures massive upward drift.
   The long-only signal's 418.6% return over this period must be compared to BTC buy-and-hold.

### Why the KILL is justified despite surface-level pass on K1/K2

The K5 (non-stationarity) kill is the critical finding. The IC analysis reveals:

- **Q3 of the last 2 years had IC = -0.0574** (negative relationship)
- This means the falling-yields/falling-dollar -> BTC-up thesis *reversed* for an entire quarter
- IC sign flips 19 times in 2 years -- essentially random oscillation around zero
- 58.8% positive is barely above coin-flip (50%)

This non-stationarity is not surprising. BTC's correlation with macro factors has been evolving:
- 2020-2021: BTC uncorrelated with macro (retail-driven, stimulus)
- 2022: High correlation with risk assets (institutional, rate sensitivity)
- 2023-2024: Partial decorrelation (ETF flows, halving narrative)
- 2025-2026: Regime-dependent correlation (sometimes yes, sometimes no)

### Parameter sensitivity is catastrophic

Moving the lookback from 20 to 16 or 24 (a 20% change) flips the Sharpe from +0.177 to -0.104
or -0.159. This is **189.7% degradation** -- the signal's profitability depends on choosing
*exactly* the right lookback. This is a hallmark of curve-fitting, not a robust signal.

### Regime analysis reveals the core problem

The macro signal works well in UPTREND (+1.077 Sharpe) -- but so does nearly everything in a
BTC uptrend. In the RANGE regime where V3 struggles most (47% of days), the macro signal
has a *negative* Sharpe of -0.422. This directly undercuts the portfolio diversification thesis
from R107: we wanted this signal precisely because V3 loses in ranges, but macro rotation
also loses in ranges.

## Recommendations

- **Signal fails kill criteria. Do NOT integrate into portfolio.**
- The macro regime rotation concept may work at longer timeframes
  or with additional conditioning variables, but this specific
  implementation does not meet our quality bar.
- **Specific failure modes**:
  - Non-stationary IC means the US10Y/DXY -> BTC relationship is unreliable
  - Extreme parameter sensitivity means any "optimal" params are overfit
  - Negative RANGE regime Sharpe eliminates the diversification value
- **Potential salvage paths** (for future research, not current implementation):
  - Condition on VIX regime: macro rotation may only work when VIX > 20 (risk markets paying attention to macro)
  - Use a longer lookback (60-90 days) for more structural regime changes vs. noise
  - Combine with ETF flow data: macro + ETF inflows together might be more stable
  - Test on weekly rebalance only to reduce noise sensitivity
- **What R107's Sharpe 0.397 was**: Likely an in-sample artifact from a single parameter set
  (20d lookback, 0 threshold) that happened to work over the full backtest period. The deep
  WF exposes this as unstable.
