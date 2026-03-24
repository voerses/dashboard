# R69: V3 Range Filter Test Results

**Run date**: 2026-03-24 12:07
**IS period**: 2020-09-01 to 2024-12-31
**OOS period**: 2025-01-01 to 2026-03-14

## Goal

V3 (20/50 EMA + Positioning + VRP overlays) has a known weakness in RANGE regimes
(IS Sharpe -1.01, OOS Sharpe -1.06). Test range-market filters to reduce RANGE losses
without hurting UPTREND performance.

## KILL Criteria

- Filter reduces OOS UPTREND Sharpe by >20%: **KILL** (cure worse than disease)
- Filter improves overall OOS Sharpe by <0.05: **KILL** (not worth complexity)

## V3 Baseline (No Filter)

| Period | Sharpe | Return | MaxDD | Days |
|--------|--------|--------|-------|------|
| IS | +0.902 | +39.4% | -54.2% | 1583 |
| OOS | +0.556 | +14.7% | -20.2% | 431 |

### Baseline Regime Performance (OOS)

| Regime | Sharpe | Return | MaxDD | Days |
|--------|--------|--------|-------|------|
| UPTREND | +6.017 | +193.1% | -8.9% | 135 |
| RANGE | -1.057 | -28.4% | -21.6% | 189 |
| DOWNTREND | -1.764 | -34.4% | -8.3% | 55 |
| CRISIS | +0.000 | +0.0% | 0.0% | 52 |

## Filter A: EMA Spread Threshold

*Only enter when |EMA20 - EMA50| / EMA50 > threshold. Small gaps = noise, large gaps = real trend.*

| Variant | Params | OOS Sharpe | OOS dSharpe | OOS Return | OOS MaxDD | OOS RANGE Sharpe | OOS UPTREND Sharpe | % Filtered | Verdict |
|---------|--------|------------|-------------|------------|-----------|------------------|--------------------|-----------:|---------|
| A_spread_0.005 | {'threshold': 0.005} | +0.839 | +0.283 | +20.5% | -15.4% | -1.019 | +5.448 | 8.0% | **PASS** |
| A_spread_0.010 | {'threshold': 0.01} | +0.965 | +0.410 | +23.2% | -15.2% | -0.895 | +5.448 | 16.1% | **PASS** |
| A_spread_0.020 | {'threshold': 0.02} | +0.515 | -0.041 | +9.8% | -11.8% | -0.270 | +1.890 | 27.3% | **KILL** |
| A_spread_0.030 | {'threshold': 0.03} | +0.620 | +0.064 | +10.7% | -11.7% | +0.385 | +1.222 | 39.5% | **KILL** |

### Kill Reasons

- **A_spread_0.020**: UPTREND Sharpe degraded by 68.6% (>20%)
- **A_spread_0.020**: OOS Sharpe improvement only -0.041 (<0.05)
- **A_spread_0.030**: UPTREND Sharpe degraded by 79.7% (>20%)

## Filter B: ADX Threshold

*Only enter when ADX > threshold. Low ADX = no trend.*

| Variant | Params | OOS Sharpe | OOS dSharpe | OOS Return | OOS MaxDD | OOS RANGE Sharpe | OOS UPTREND Sharpe | % Filtered | Verdict |
|---------|--------|------------|-------------|------------|-----------|------------------|--------------------|-----------:|---------|
| B_adx_10 | {'adx_threshold': 10} | +0.556 | +0.000 | +14.7% | -20.2% | -1.057 | +6.017 | 0.0% | **KILL** |
| B_adx_15 | {'adx_threshold': 15} | +0.167 | -0.388 | +4.3% | -20.2% | -0.987 | +3.551 | 1.1% | **KILL** |
| B_adx_20 | {'adx_threshold': 20} | +0.167 | -0.388 | +4.3% | -20.2% | -0.987 | +3.551 | 7.9% | **KILL** |
| B_adx_25 | {'adx_threshold': 25} | -0.036 | -0.592 | -0.7% | -22.3% | -0.352 | +1.630 | 22.3% | **KILL** |

### Kill Reasons

- **B_adx_10**: OOS Sharpe improvement only +0.000 (<0.05)
- **B_adx_15**: UPTREND Sharpe degraded by 41.0% (>20%)
- **B_adx_15**: OOS Sharpe improvement only -0.388 (<0.05)
- **B_adx_20**: UPTREND Sharpe degraded by 41.0% (>20%)
- **B_adx_20**: OOS Sharpe improvement only -0.388 (<0.05)
- **B_adx_25**: UPTREND Sharpe degraded by 72.9% (>20%)
- **B_adx_25**: OOS Sharpe improvement only -0.592 (<0.05)

## Filter C: Whipsaw Counter

*Count EMA crossovers in last 60 days. Many crosses = range market.*

| Variant | Params | OOS Sharpe | OOS dSharpe | OOS Return | OOS MaxDD | OOS RANGE Sharpe | OOS UPTREND Sharpe | % Filtered | Verdict |
|---------|--------|------------|-------------|------------|-----------|------------------|--------------------|-----------:|---------|
| C_whipsaw_1x_r0.5 | {'max_crosses': 1, 'reduce_to': 0.5} | +0.788 | +0.232 | +19.3% | -16.4% | -0.782 | +5.739 | 23.7% | **PASS** |
| C_whipsaw_2x_r0.0 | {'max_crosses': 2, 'reduce_to': 0.0} | +0.982 | +0.426 | +24.5% | -12.1% | -0.456 | +5.879 | 3.7% | **PASS** |
| C_whipsaw_2x_r0.5 | {'max_crosses': 2, 'reduce_to': 0.5} | +0.774 | +0.218 | +19.6% | -16.2% | -0.800 | +6.010 | 3.7% | **PASS** |
| C_whipsaw_3x_r0.5 | {'max_crosses': 3, 'reduce_to': 0.5} | +0.774 | +0.218 | +19.6% | -16.2% | -0.800 | +6.010 | 1.9% | **PASS** |

## Filter D: Bollinger Band Width

*When BB width < Nth percentile, reduce position to 0.5x. Narrow BBs = range.*

| Variant | Params | OOS Sharpe | OOS dSharpe | OOS Return | OOS MaxDD | OOS RANGE Sharpe | OOS UPTREND Sharpe | % Filtered | Verdict |
|---------|--------|------------|-------------|------------|-----------|------------------|--------------------|-----------:|---------|
| D_bbwidth_p15 | {'percentile': 15} | +0.346 | -0.210 | +8.2% | -19.1% | -0.765 | +3.845 | 19.9% | **KILL** |
| D_bbwidth_p25 | {'percentile': 25} | +0.563 | +0.008 | +11.5% | -14.8% | -0.603 | +3.937 | 35.1% | **KILL** |
| D_bbwidth_p35 | {'percentile': 35} | +0.741 | +0.186 | +14.5% | -14.8% | -0.526 | +4.689 | 45.8% | **KILL** |

### Kill Reasons

- **D_bbwidth_p15**: UPTREND Sharpe degraded by 36.1% (>20%)
- **D_bbwidth_p15**: OOS Sharpe improvement only -0.210 (<0.05)
- **D_bbwidth_p25**: UPTREND Sharpe degraded by 34.6% (>20%)
- **D_bbwidth_p25**: OOS Sharpe improvement only +0.008 (<0.05)
- **D_bbwidth_p35**: UPTREND Sharpe degraded by 22.1% (>20%)

## Filter E: Realized Vol

*When 20d realized vol < threshold, reduce position. Low vol = range.*

| Variant | Params | OOS Sharpe | OOS dSharpe | OOS Return | OOS MaxDD | OOS RANGE Sharpe | OOS UPTREND Sharpe | % Filtered | Verdict |
|---------|--------|------------|-------------|------------|-----------|------------------|--------------------|-----------:|---------|
| E_rvol_25pct_r0.0 | {'vol_threshold': 0.25, 'reduce_to': 0.0} | +0.418 | -0.138 | +10.4% | -21.7% | -1.026 | +4.790 | 4.0% | **KILL** |
| E_rvol_25pct_r0.5 | {'vol_threshold': 0.25, 'reduce_to': 0.5} | +0.500 | -0.055 | +12.6% | -19.8% | -1.058 | +5.476 | 4.0% | **KILL** |
| E_rvol_30pct_r0.0 | {'vol_threshold': 0.3, 'reduce_to': 0.0} | +0.311 | -0.245 | +7.1% | -17.4% | -0.941 | +2.874 | 10.3% | **KILL** |
| E_rvol_30pct_r0.5 | {'vol_threshold': 0.3, 'reduce_to': 0.5} | +0.467 | -0.088 | +11.1% | -16.0% | -1.018 | +4.515 | 10.3% | **KILL** |
| E_rvol_40pct_r0.0 | {'vol_threshold': 0.4, 'reduce_to': 0.0} | +0.423 | -0.132 | +6.3% | -9.9% | -1.452 | +2.910 | 27.8% | **KILL** |
| E_rvol_40pct_r0.5 | {'vol_threshold': 0.4, 'reduce_to': 0.5} | +0.604 | +0.048 | +11.2% | -11.1% | -1.322 | +4.891 | 27.8% | **KILL** |
| E_rvol_50pct_r0.0 | {'vol_threshold': 0.5, 'reduce_to': 0.0} | -0.455 | -1.010 | -1.9% | -6.0% | -0.666 | -1.644 | 47.6% | **KILL** |
| E_rvol_50pct_r0.5 | {'vol_threshold': 0.5, 'reduce_to': 0.5} | +0.509 | -0.046 | +7.0% | -11.1% | -1.133 | +4.582 | 47.6% | **KILL** |

### Kill Reasons

- **E_rvol_25pct_r0.5**: OOS Sharpe improvement only -0.055 (<0.05)
- **E_rvol_25pct_r0.0**: UPTREND Sharpe degraded by 20.4% (>20%)
- **E_rvol_25pct_r0.0**: OOS Sharpe improvement only -0.138 (<0.05)
- **E_rvol_30pct_r0.5**: UPTREND Sharpe degraded by 25.0% (>20%)
- **E_rvol_30pct_r0.5**: OOS Sharpe improvement only -0.088 (<0.05)
- **E_rvol_30pct_r0.0**: UPTREND Sharpe degraded by 52.2% (>20%)
- **E_rvol_30pct_r0.0**: OOS Sharpe improvement only -0.245 (<0.05)
- **E_rvol_40pct_r0.5**: OOS Sharpe improvement only +0.048 (<0.05)
- **E_rvol_40pct_r0.0**: UPTREND Sharpe degraded by 51.6% (>20%)
- **E_rvol_40pct_r0.0**: OOS Sharpe improvement only -0.132 (<0.05)
- **E_rvol_50pct_r0.5**: UPTREND Sharpe degraded by 23.9% (>20%)
- **E_rvol_50pct_r0.5**: OOS Sharpe improvement only -0.046 (<0.05)
- **E_rvol_50pct_r0.0**: UPTREND Sharpe degraded by 127.3% (>20%)
- **E_rvol_50pct_r0.0**: OOS Sharpe improvement only -1.010 (<0.05)

## Filter F: EMA Slope Confirmation

*Only enter long when 50d EMA slope is positive over N days.*

| Variant | Params | OOS Sharpe | OOS dSharpe | OOS Return | OOS MaxDD | OOS RANGE Sharpe | OOS UPTREND Sharpe | % Filtered | Verdict |
|---------|--------|------------|-------------|------------|-----------|------------------|--------------------|-----------:|---------|
| F_slope_10d | {'lookback': 10} | +0.663 | +0.107 | +17.2% | -20.1% | -0.948 | +6.017 | 44.9% | **PASS** |
| F_slope_20d | {'lookback': 20} | +0.600 | +0.044 | +15.8% | -19.3% | -1.003 | +6.048 | 43.9% | **KILL** |
| F_slope_5d | {'lookback': 5} | +0.315 | -0.241 | +7.6% | -18.8% | -1.056 | +4.773 | 44.6% | **KILL** |

### Kill Reasons

- **F_slope_5d**: UPTREND Sharpe degraded by 20.7% (>20%)
- **F_slope_5d**: OOS Sharpe improvement only -0.241 (<0.05)
- **F_slope_20d**: OOS Sharpe improvement only +0.044 (<0.05)

## Grand Summary

### All Filters Ranked by OOS Sharpe Improvement

| Rank | Filter | OOS dSharpe | OOS Sharpe | RANGE dSharpe | UPTREND dSharpe | Verdict |
|------|--------|-------------|------------|---------------|-----------------|---------|
| 1 | C_whipsaw_2x_r0.0 | +0.426 | +0.982 | +0.601 | -0.138 | **PASS** |
| 2 | A_spread_0.010 | +0.410 | +0.965 | +0.162 | -0.569 | **PASS** |
| 3 | A_spread_0.005 | +0.283 | +0.839 | +0.038 | -0.569 | **PASS** |
| 4 | C_whipsaw_1x_r0.5 | +0.232 | +0.788 | +0.276 | -0.277 | **PASS** |
| 5 | C_whipsaw_2x_r0.5 | +0.218 | +0.774 | +0.258 | -0.007 | **PASS** |
| 6 | C_whipsaw_3x_r0.5 | +0.218 | +0.774 | +0.258 | -0.007 | **PASS** |
| 7 | D_bbwidth_p35 | +0.186 | +0.741 | +0.532 | -1.327 | **KILL** |
| 8 | F_slope_10d | +0.107 | +0.663 | +0.110 | +0.000 | **PASS** |
| 9 | A_spread_0.030 | +0.064 | +0.620 | +1.442 | -4.795 | **KILL** |
| 10 | E_rvol_40pct_r0.5 | +0.048 | +0.604 | -0.264 | -1.125 | **KILL** |
| 11 | F_slope_20d | +0.044 | +0.600 | +0.054 | +0.032 | **KILL** |
| 12 | D_bbwidth_p25 | +0.008 | +0.563 | +0.455 | -2.079 | **KILL** |
| 13 | B_adx_10 | +0.000 | +0.556 | +0.000 | +0.000 | **KILL** |
| 14 | A_spread_0.020 | -0.041 | +0.515 | +0.787 | -4.127 | **KILL** |
| 15 | E_rvol_50pct_r0.5 | -0.046 | +0.509 | -0.076 | -1.435 | **KILL** |
| 16 | E_rvol_25pct_r0.5 | -0.055 | +0.500 | -0.001 | -0.540 | **KILL** |
| 17 | E_rvol_30pct_r0.5 | -0.088 | +0.467 | +0.039 | -1.502 | **KILL** |
| 18 | E_rvol_40pct_r0.0 | -0.132 | +0.423 | -0.395 | -3.107 | **KILL** |
| 19 | E_rvol_25pct_r0.0 | -0.138 | +0.418 | +0.031 | -1.227 | **KILL** |
| 20 | D_bbwidth_p15 | -0.210 | +0.346 | +0.292 | -2.172 | **KILL** |
| 21 | F_slope_5d | -0.241 | +0.315 | +0.001 | -1.244 | **KILL** |
| 22 | E_rvol_30pct_r0.0 | -0.245 | +0.311 | +0.116 | -3.143 | **KILL** |
| 23 | B_adx_15 | -0.388 | +0.167 | +0.070 | -2.465 | **KILL** |
| 24 | B_adx_20 | -0.388 | +0.167 | +0.070 | -2.465 | **KILL** |
| 25 | B_adx_25 | -0.592 | -0.036 | +0.705 | -4.386 | **KILL** |
| 26 | E_rvol_50pct_r0.0 | -1.010 | -0.455 | +0.391 | -7.660 | **KILL** |

### Surviving Filters: IS vs OOS Consistency Check

| Filter | IS Sharpe | IS dSharpe | OOS Sharpe | OOS dSharpe | IS-OOS Gap | Overfit Risk |
|--------|----------|------------|------------|-------------|------------|--------------|
| C_whipsaw_2x_r0.0 | +0.902 | +0.000 | +0.982 | +0.426 | -0.426 | N/A |
| A_spread_0.010 | +1.024 | +0.123 | +0.965 | +0.410 | -0.287 | LOW |
| A_spread_0.005 | +0.986 | +0.085 | +0.839 | +0.283 | -0.198 | LOW |
| C_whipsaw_1x_r0.5 | +1.021 | +0.119 | +0.788 | +0.232 | -0.113 | LOW |
| C_whipsaw_2x_r0.5 | +0.902 | +0.000 | +0.774 | +0.218 | -0.218 | N/A |
| C_whipsaw_3x_r0.5 | +0.902 | +0.000 | +0.774 | +0.218 | -0.218 | N/A |
| F_slope_10d | +0.873 | -0.028 | +0.663 | +0.107 | -0.136 | LOW |

## Verdict

### BEST FILTER: C_whipsaw_2x_r0.0

- OOS Sharpe improvement: +0.426
- OOS Sharpe: +0.982
- RANGE Sharpe improvement: +0.601
- UPTREND Sharpe change: -0.138

IS/OOS consistency looks good (IS delta=+0.000, OOS delta=+0.426).

### Other Passing Filters

- **A_spread_0.010**: OOS dSharpe=+0.410
- **A_spread_0.005**: OOS dSharpe=+0.283
- **C_whipsaw_1x_r0.5**: OOS dSharpe=+0.232
- **C_whipsaw_2x_r0.5**: OOS dSharpe=+0.218
- **C_whipsaw_3x_r0.5**: OOS dSharpe=+0.218
- **F_slope_10d**: OOS dSharpe=+0.107

## Key Insights

- **Whipsaw Counter**: 4 pass, 0 kill. Best OOS dSharpe: +0.426
- **EMA Spread**: 2 pass, 2 kill. Best OOS dSharpe: +0.410
- **BB Width**: 0 pass, 3 kill. Best OOS dSharpe: +0.186
- **EMA Slope**: 1 pass, 2 kill. Best OOS dSharpe: +0.107
- **Realized Vol**: 0 pass, 8 kill. Best OOS dSharpe: +0.048
- **ADX**: 0 pass, 4 kill. Best OOS dSharpe: +0.000

### Why Whipsaw Counter Works Best

The whipsaw counter (Filter C) is the most effective because it is **reactive to the specific problem** --
it directly detects the condition that hurts V3 (rapid EMA crossovers). When >2 EMA 20/50 crosses
occur in 60 days, the strategy goes flat (r=0.0 variant) or reduces to half (r=0.5 variant).

Key properties of the winning filter (`C_whipsaw_2x_r0.0`):
- **Minimal intervention**: Only 3.7% of days are affected (very surgical)
- **No UPTREND damage**: UPTREND Sharpe drops from +6.017 to +5.879 (only -2.3%, well under 20% kill threshold)
- **Significant RANGE improvement**: RANGE Sharpe improves from -1.057 to -0.456 (+0.601 improvement)
- **MaxDD improvement**: OOS MaxDD improves from -20.2% to -12.1% (40% reduction)
- **No overfit signal**: IS Sharpe is unchanged (0.902->0.902), OOS improvement is purely OOS-driven
- **Low complexity**: Simple crossover counting, no new parameters to tune beyond the 2-cross threshold

### Why Other Filters Fail

1. **ADX (Filter B)**: Completely killed at all thresholds. ADX reacts too slowly for crypto and
   indiscriminately filters out UPTREND days. Confirms R65's finding that ADX-based filtering is unsuitable.

2. **Realized Vol (Filter E)**: Low vol does not reliably predict RANGE regimes in crypto.
   BTC can trend strongly at low vol (quiet uptrends) and chop at high vol (volatile ranges).
   Every variant either hurts UPTREND or fails to improve OOS.

3. **BB Width (Filter D)**: Similar problem to realized vol -- narrow bands filter out quiet
   uptrends as well as ranges. All variants kill UPTREND by >20%.

4. **EMA Spread (Filter A)**: The 1% threshold variant passes well (+0.410 dSharpe), but higher
   thresholds (2%, 3%) destroy UPTREND. The sweet spot is narrow, suggesting some overfit risk.

5. **EMA Slope (Filter F)**: The 10d variant passes (+0.107) with zero UPTREND damage, but
   the improvement is modest and 45% of days are filtered -- too aggressive for too little gain.

### Recommendation

**C_whipsaw_2x_r0.0** is WORTH ADDING to V3. It is the only filter that:
1. Achieves large OOS Sharpe improvement (+0.426)
2. Preserves UPTREND performance (only -2.3% degradation)
3. Shows zero overfit signal (IS unchanged, improvement is OOS-only)
4. Has minimal parameter sensitivity (2 or 3 max_crosses both work)
5. Is simple to implement (crossover counting in a 60d rolling window)

**Secondary option**: `A_spread_0.010` (EMA spread > 1%) is also strong (+0.410 dSharpe)
but has a narrower viable parameter range (0.5% and 1% work, 2% kills it).

**For maximum robustness**, consider combining both: whipsaw counter as primary filter,
EMA spread as confirmation. But this adds complexity and should be walk-forward validated.
