# Parameter Sensitivity & Walk-Forward Robustness Results

**Generated**: 2026-03-24 11:13
**System**: Regime-Switched Positioning Signal for BTC
**Cost assumption**: 10 bps round-trip per position change

## 1. Parameter Sensitivity Grid

Each parameter tested independently at +/-20% from base, all others at base values.

| Parameter | Low (-20%) | Base | High (+20%) | Sharpe Delta | Calmar Delta | Robust? |
|-----------|-----------|------|-------------|-------------|-------------|---------|
| Positioning Z-Score Lookback | 24: Sharpe=-0.071 | 30: Sharpe=+0.036 | 36: Sharpe=-0.004 | Low: -0.106, High: -0.040 | Low: -0.057, High: -0.017 | **YES** |
| Regime Lookback | 40: Sharpe=+0.080 | 50: Sharpe=+0.036 | 60: Sharpe=-0.214 | Low: +0.045, High: -0.250 | Low: +0.018, High: -0.100 | **NO** |
| Regime Thresholds | +/-8%: Sharpe=-0.067 | +/-10%: Sharpe=+0.036 | +/-12%: Sharpe=+0.096 | Low: -0.102, High: +0.060 | Low: -0.055, High: +0.029 | **YES** |
| Entry Z-Threshold | 0.8: Sharpe=+0.053 | 1.0: Sharpe=+0.036 | 1.2: Sharpe=+0.094 | Low: +0.017, High: +0.058 | Low: +0.002, High: +0.036 | **YES** |
| Macro Agreement Boost | 1.25: Sharpe=+0.049 | 1.5: Sharpe=+0.036 | 1.75: Sharpe=+0.026 | Low: +0.013, High: -0.010 | Low: +0.008, High: -0.005 | **YES** |
| Macro Tercile Thresholds | 25/75: Sharpe=+0.038 | 33/66: Sharpe=+0.036 | 40/60: Sharpe=+0.088 | Low: +0.003, High: +0.053 | Low: +0.003, High: +0.023 | **YES** |

### Detailed Parameter Values

| Parameter | Variant | Value | Sharpe | Calmar | Return | MaxDD | Vol |
|-----------|---------|-------|--------|--------|--------|-------|-----|
| Positioning Z-Score Lookback | Low (-20%) | 24 | -0.071 | -0.128 | -38.71% | -66.32% | 35.56% |
| Positioning Z-Score Lookback | Base | 30 | +0.036 | -0.071 | -23.98% | -67.91% | 35.24% |
| Positioning Z-Score Lookback | High (+20%) | 36 | -0.004 | -0.089 | -29.88% | -70.43% | 35.38% |
| Regime Lookback | Low (-20%) | 40 | +0.080 | -0.053 | -17.28% | -63.40% | 35.37% |
| Regime Lookback | Base | 50 | +0.036 | -0.071 | -23.98% | -67.91% | 35.24% |
| Regime Lookback | High (+20%) | 60 | -0.214 | -0.171 | -53.79% | -76.32% | 35.60% |
| Regime Thresholds | Low (-20%) | +/-8% | -0.067 | -0.127 | -38.24% | -65.99% | 35.61% |
| Regime Thresholds | Base | +/-10% | +0.036 | -0.071 | -23.98% | -67.91% | 35.24% |
| Regime Thresholds | High (+20%) | +/-12% | +0.096 | -0.042 | -14.39% | -65.42% | 35.12% |
| Entry Z-Threshold | Low (-20%) | 0.8 | +0.053 | -0.070 | -24.06% | -69.66% | 37.20% |
| Entry Z-Threshold | Base | 1.0 | +0.036 | -0.071 | -23.98% | -67.91% | 35.24% |
| Entry Z-Threshold | High (+20%) | 1.2 | +0.094 | -0.036 | -12.17% | -65.47% | 33.02% |
| Macro Agreement Boost | Low (-20%) | 1.25 | +0.049 | -0.064 | -21.65% | -67.80% | 35.00% |
| Macro Agreement Boost | Base | 1.5 | +0.036 | -0.071 | -23.98% | -67.91% | 35.24% |
| Macro Agreement Boost | High (+20%) | 1.75 | +0.026 | -0.077 | -25.66% | -68.10% | 35.43% |
| Macro Tercile Thresholds | Low (-20%) | 25/75 | +0.038 | -0.068 | -23.02% | -68.20% | 34.80% |
| Macro Tercile Thresholds | Base | 33/66 | +0.036 | -0.071 | -23.98% | -67.91% | 35.24% |
| Macro Tercile Thresholds | High (+20%) | 40/60 | +0.088 | -0.048 | -16.65% | -67.28% | 35.97% |

## 2. Walk-Forward Results

All windows use base parameters. Train period is expanding from 2020-09-01.

| Window | Train Period | Test Period | Test Sharpe | Test Calmar | Test Return | Test MaxDD |
|--------|-------------|-------------|-------------|-------------|-------------|-----------|
| Window 1 | 2020-09-01 to 2023-06-30 | 2023-07-01 to 2024-06-30 | -1.102 | -0.689 | -31.08% | -45.00% |
| Window 2 | 2020-09-01 to 2024-06-30 | 2024-07-01 to 2025-06-30 | -0.186 | -0.224 | -8.21% | -36.67% |
| Window 3 | 2020-09-01 to 2025-06-30 | 2025-07-01 to 2026-12-31 | +0.448 | +0.697 | +5.75% | -12.22% |

### Train vs Test Comparison

| Window | Train Sharpe | Test Sharpe | Delta | Train Return | Test Return |
|--------|-------------|-------------|-------|-------------|-------------|
| Window 1 | +0.318 | -1.102 | -1.420 | +13.91% | -31.08% |
| Window 2 | +0.028 | -0.186 | -0.214 | -21.71% | -8.21% |
| Window 3 | -0.006 | +0.448 | +0.454 | -28.22% | +5.75% |

## 3. Worst-Case Analysis

Tested all 64 extreme parameter combinations on OOS window (2024-07-01 to 2025-06-30).

| Metric | Value |
|--------|-------|
| Worst Sharpe | -0.419 |
| Worst Calmar | -0.431 |
| Worst Return | -15.22% |
| Worst MaxDD | -35.30% |
| Worst Params | zL=36 rL=60 rT=[-12%,+12%] eZ=0.8 mB=1.75 mP=[25%/75%] |
| Best Sharpe | +1.575 |
| Best Params | zL=36 rL=40 rT=[-8%,+8%] eZ=1.2 mB=1.75 mP=[25%/75%] |
| Median Sharpe (all combos) | -0.014 |
| Sharpe Std Dev | 0.577 |
| Sharpe Range | [-0.419, +1.575] |
| Combos with Positive Sharpe | 31/64 (48%) |

## 4. Summary Verdict

### Parameter Robustness: 5/6 parameters robust

PASS: 5 of 6 parameters maintain Sharpe within 0.15 of base at +/-20% perturbation.

### Walk-Forward Consistency: 1/3 windows positive

FAIL: 2 of 3 walk-forward windows have non-positive Sharpe.

### Worst-Case OOS: Sharpe = -0.419

WARNING: Only 48% of extreme combinations maintain positive OOS Sharpe.

### Final Recommendation

**KILL.** The signal system fails robustness checks:
- Only 1/3 walk-forward windows positive (need all 3)
- Only 48% of extreme combos maintain positive Sharpe

The edge is likely an artifact of parameter fitting. Do not deploy.
