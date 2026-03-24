# V3 Conditional Seasonal Overlay Test Results

**Generated:** 2026-03-24 13:05
**Data range:** 2020-01-01 to 2026-03-14
**IS period:** 2020-09-01 to 2024-12-31
**OOS period:** 2025-01-01 to 2026-03-14
**Transaction cost:** 10 bps round-trip
**Rebalance:** Weekly

## Context

Previous research (R80) found that **blanket calendar rules** (reduce in Apr-Sep)
fail PIT OOS but pass 5/6 walk-forward windows. The insight: seasonality is REAL
but needs to be **CONDITIONAL** -- reduce in weak months ONLY when other signals
also suggest caution.

This test applies conditional seasonal overlays that combine calendar weakness
with signal confirmation from positioning z-score and/or VRP z-score.

## Executive Summary

**1 PASS / 3 KILL** out of 4 conditional seasonal variants tested.

- **V1_pos_conditioning**: **KILL** -- WF PASS: 4/6 windows improve
- **V2_vrp_conditioning**: **PASS** -- WF PASS: 4/6 windows improve
- **V3c_both_conditioning**: **KILL** -- WF PASS: 5/6 windows improve
- **V4_gradient**: **KILL** -- WF PASS: 5/6 windows improve

## Variant Descriptions

All variants apply a seasonal multiplier ONLY during weak months (Apr-Sep).
Strong months (Oct-Mar) are always 1.0x (no adjustment).

| Variant | Conditioning Signal | Weak-Month Logic |
|---------|--------------------|--------------------|
| V1 | Positioning z-score | pos_z>0.5: 0.3x, neutral: 0.7x, pos_z<-0.5: 1.0x |
| V2 | VRP z-score | vrp_z<-0.5: 0.3x, neutral: 0.7x, vrp_z>0.5: 1.0x |
| V3c | Both signals | Both cautious: 0.0x, either: 0.5x, neither: 0.8x |
| V4 | Gradient + Positioning | Month-strength * (0.5 if pos_z>0.5 in weak month) |

## Conditional Activation Statistics

How often does each overlay actually reduce position in weak months?

### V1_pos_conditioning

- weak_days: 1098
- cautious_days: 186
- neutral_days: 403
- contrarian_days: 273
- pct_full_reduction: 16.9%
- pct_mild_reduction: 36.7%
- pct_no_reduction: 24.9%

### V2_vrp_conditioning

- weak_days: 1098
- cautious_days: 271
- neutral_days: 265
- complacent_days: 358
- pct_full_reduction: 24.7%
- pct_mild_reduction: 24.1%
- pct_no_reduction: 32.6%

### V3c_both_conditioning

- weak_days: 1098
- both_cautious_days: 55
- either_cautious_days: 347
- neither_cautious_days: 696
- pct_go_flat: 5.0%
- pct_half_reduction: 31.6%
- pct_mild_reduction: 63.4%

### V4_gradient

- avg_mult_strong: 1.184
- avg_mult_weak: 0.717
- avg_mult_weak_crowd: 0.391
- days_crowd_reduced: 186

## Point-in-Time Results (IS + OOS)

| Variant | IS Sharpe | IS Return | IS MaxDD | OOS Sharpe | OOS Return | OOS MaxDD | dSharpe OOS |
|---------|-----------|-----------|----------|------------|------------|-----------|-------------|
| V3_base | +0.902 | +39.4% | -54.2% | +0.556 | +14.7% | -20.2% | +0.000 |
| V1_pos_conditioning | +1.299 | +54.1% | -48.8% | +0.531 | +13.1% | -18.3% | -0.025 |
| V2_vrp_conditioning | +1.080 | +45.3% | -54.5% | +0.675 | +17.3% | -18.6% | +0.119 |
| V3c_both_conditioning | +1.247 | +51.1% | -50.3% | +0.468 | +11.1% | -18.2% | -0.088 |
| V4_gradient | +1.315 | +61.3% | -56.3% | +0.165 | +4.3% | -22.5% | -0.391 |

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
| **Summary** | | | | | | 0/6 improved, 0/6 DD worse |

### V1_pos_conditioning

| Window | OOS Period | IS Sharpe | OOS Sharpe | OOS Return | OOS MaxDD | dSharpe vs Base |
|--------|-----------|-----------|------------|------------|-----------|-----------------|
| W1 | 2022-07-01..2022-12-31 | +1.161 | -1.440 | -44.8% | -28.9% | +0.042 |
| W2 | 2023-01-01..2023-06-30 | -0.410 | +0.524 | +15.8% | -15.9% | -0.044 |
| W3 | 2023-07-01..2023-12-31 | -0.808 | +1.354 | +36.7% | -10.2% | +0.582 |
| W4 | 2024-01-01..2024-06-30 | -0.151 | +2.032 | +95.1% | -20.6% | +0.767 |
| W5 | 2024-07-01..2024-12-31 | +1.279 | +1.067 | +28.1% | -17.7% | +0.702 |
| W6 | 2025-01-01..2025-06-30 | +1.457 | +1.588 | +48.0% | -9.9% | -0.025 |
| **Mean** | | | **+0.854** | | | **+0.337** |
| **Summary** | | | | | | 4/6 improved, 0/6 DD worse |

### V2_vrp_conditioning

| Window | OOS Period | IS Sharpe | OOS Sharpe | OOS Return | OOS MaxDD | dSharpe vs Base |
|--------|-----------|-----------|------------|------------|-----------|-----------------|
| W1 | 2022-07-01..2022-12-31 | +0.951 | -1.482 | -48.7% | -31.5% | 0.000 |
| W2 | 2023-01-01..2023-06-30 | -0.596 | +0.502 | +15.4% | -15.6% | -0.067 |
| W3 | 2023-07-01..2023-12-31 | -0.916 | +0.805 | +22.7% | -15.0% | +0.034 |
| W4 | 2024-01-01..2024-06-30 | -0.334 | +1.652 | +75.9% | -24.6% | +0.387 |
| W5 | 2024-07-01..2024-12-31 | +0.994 | +0.958 | +26.0% | -18.3% | +0.593 |
| W6 | 2025-01-01..2025-06-30 | +1.136 | +1.755 | +54.8% | -9.5% | +0.141 |
| **Mean** | | | **+0.698** | | | **+0.181** |
| **Summary** | | | | | | 4/6 improved, 0/6 DD worse |

### V3c_both_conditioning

| Window | OOS Period | IS Sharpe | OOS Sharpe | OOS Return | OOS MaxDD | dSharpe vs Base |
|--------|-----------|-----------|------------|------------|-----------|-----------------|
| W1 | 2022-07-01..2022-12-31 | +1.119 | -1.458 | -46.1% | -29.8% | +0.024 |
| W2 | 2023-01-01..2023-06-30 | -0.522 | +0.794 | +22.2% | -13.0% | +0.225 |
| W3 | 2023-07-01..2023-12-31 | -0.823 | +1.285 | +34.7% | -11.2% | +0.513 |
| W4 | 2024-01-01..2024-06-30 | -0.140 | +1.876 | +85.1% | -22.6% | +0.612 |
| W5 | 2024-07-01..2024-12-31 | +1.306 | +0.814 | +22.1% | -19.6% | +0.449 |
| W6 | 2025-01-01..2025-06-30 | +1.311 | +1.438 | +41.9% | -9.6% | -0.175 |
| **Mean** | | | **+0.792** | | | **+0.275** |
| **Summary** | | | | | | 5/6 improved, 0/6 DD worse |

### V4_gradient

| Window | OOS Period | IS Sharpe | OOS Sharpe | OOS Return | OOS MaxDD | dSharpe vs Base |
|--------|-----------|-----------|------------|------------|-----------|-----------------|
| W1 | 2022-07-01..2022-12-31 | +1.095 | -1.341 | -53.5% | -35.5% | +0.141 |
| W2 | 2023-01-01..2023-06-30 | -0.527 | +0.853 | +24.8% | -12.5% | +0.284 |
| W3 | 2023-07-01..2023-12-31 | -0.825 | +1.612 | +50.9% | -11.8% | +0.840 |
| W4 | 2024-01-01..2024-06-30 | -0.132 | +1.908 | +92.7% | -22.6% | +0.644 |
| W5 | 2024-07-01..2024-12-31 | +1.437 | +1.109 | +35.5% | -21.7% | +0.744 |
| W6 | 2025-01-01..2025-06-30 | +1.517 | +1.029 | +32.1% | -12.2% | -0.584 |
| **Mean** | | | **+0.862** | | | **+0.345** |
| **Summary** | | | | | | 5/6 improved, 2/6 DD worse |

## Kill Criteria Evaluation

Criteria (all must PASS for variant to survive):
1. **WF Windows**: >= 4/6 windows show Sharpe improvement over base V3
2. **MaxDD Windows**: MaxDD worsens in <= 2/6 windows
3. **Mean WF dSharpe**: >= +0.05 (worth the complexity)
4. **PIT OOS**: dSharpe >= 0 (seasonal overlay must pass BOTH PIT and WF)

### V1_pos_conditioning: **KILL**

- WF PASS: 4/6 windows improve
- MaxDD PASS: worsens in 0/6 windows
- Mean dSharpe PASS: +0.337
- PIT OOS KILL: dSharpe=-0.025 (must be >=0)

### V2_vrp_conditioning: **PASS**

- WF PASS: 4/6 windows improve
- MaxDD PASS: worsens in 0/6 windows
- Mean dSharpe PASS: +0.181
- PIT OOS PASS: dSharpe=+0.119

### V3c_both_conditioning: **KILL**

- WF PASS: 5/6 windows improve
- MaxDD PASS: worsens in 0/6 windows
- Mean dSharpe PASS: +0.275
- PIT OOS KILL: dSharpe=-0.088 (must be >=0)

### V4_gradient: **KILL**

- WF PASS: 5/6 windows improve
- MaxDD PASS: worsens in 2/6 windows
- Mean dSharpe PASS: +0.345
- PIT OOS KILL: dSharpe=-0.391 (must be >=0)

## Statistical Significance (OOS period)

| Variant | t-stat | p-value | N days | Significant? |
|---------|--------|---------|--------|-------------|
| V1_pos_conditioning | -0.448 | 0.6546 | 431 | No |
| V2_vrp_conditioning | +0.667 | 0.5051 | 431 | No |
| V3c_both_conditioning | -0.960 | 0.3378 | 431 | No |
| V4_gradient | -1.599 | 0.1106 | 431 | No |

## Comparison: Conditional vs Blanket Seasonal Overlays

From R80 (blanket seasonal), all 4 variants were KILLED by PIT OOS but
showed 5/6 WF windows improved. How do conditional variants compare?

| Metric | Blanket V1 (binary) | Blanket V4 (sell-in-may) | Conditional V1 (pos) | Conditional V3c (both) |
|--------|--------------------|-----------------------|---------------------|-----------------------|
| WF Mean dSharpe | +1.469 | +0.427 | +0.337 | +0.275 |
| PIT OOS dSharpe | -0.951 | -0.297 | -0.025 | -0.088 |
| WF Windows Improved | 5/6 | 5/6 | 4/6 | 5/6 |

## Conclusion

**1 variant(s) pass all kill criteria: V2_vrp_conditioning**

### V2_vrp_conditioning
- WF: 4/6 windows improved, mean dSharpe=+0.181
- PIT OOS: dSharpe=+0.119
- MaxDD worsened in 0/6 windows
- Statistical significance: t=+0.667, p=0.5051

**Recommendation:** Consider adopting the passing variant(s) as a
production overlay on V3. The conditional approach successfully filters
out periods where blanket seasonal reduction would have hurt.
