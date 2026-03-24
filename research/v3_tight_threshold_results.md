# R72: V3 Positioning Threshold Tightening -- Walk-Forward Validation

**Run date**: 2026-03-24 12:22

## Context

R67 parameter sensitivity showed that tightening `pos_high_thresh` from 1.5 to 1.2
improved OOS Sharpe from 0.556 to 0.708 on the 2025 window. This was a single-window
observation. This study validates the finding via walk-forward analysis across 6 windows.

## Variants Tested

| Variant | pos_high_thresh | mid_thresh | Thresholds |
|---------|----------------|------------|------------|
| V3-default | 1.50 | 0.50 | z > 1.5 -> 0.3x, z > 0.5 -> 0.5x, neutral -> 1.0x, z < -0.5 -> 1.3x, z < -1.5 -> 1.5x |
| V3-mid | 1.35 | 0.45 | z > 1.35 -> 0.3x, z > 0.45 -> 0.5x, neutral -> 1.0x, z < -0.45 -> 1.3x, z < -1.35 -> 1.5x |
| V3-tight | 1.20 | 0.40 | z > 1.2 -> 0.3x, z > 0.4 -> 0.5x, neutral -> 1.0x, z < -0.4 -> 1.3x, z < -1.2 -> 1.5x |

All other parameters held constant: fast_ema=20, slow_ema=50, pos_z_window=30, vrp_z_window=60, vrp_high_thresh=1.0.

## Part 1: Full-Period IS/OOS Test

| Variant | IS Sharpe | IS Return | IS MaxDD | OOS Sharpe | OOS Return | OOS MaxDD |
|---------|-----------|-----------|----------|------------|------------|-----------|
| V3-default | +0.902 | +39.4% | -54.2% | +0.556 | +14.7% | -20.2% |
| V3-mid | +0.838 | +36.5% | -54.4% | +0.812 | +21.0% | -20.2% |
| V3-tight | +0.875 | +38.1% | -53.9% | +0.708 | +18.2% | -20.2% |

### Positioning Overlay Activation (OOS Period)

| Variant | 0.3x (extreme crowd long) | 0.5x (crowd long) | 1.0x (neutral) | 1.3x (crowd short) | 1.5x (extreme crowd short) | Mean Mult |
|---------|---------------------------|-------------------|----------------|--------------------|-----------------------------|-----------|
| V3-default | 7.2% | 20.4% | 40.1% | 27.8% | 4.4% | 0.953 |
| V3-mid | 8.8% | 20.2% | 35.7% | 28.3% | 7.0% | 0.957 |
| V3-tight | 9.3% | 20.2% | 33.2% | 27.4% | 10.0% | 0.966 |

Tighter thresholds mean the overlay activates more often (fewer days at neutral 1.0x).

## Part 2: Walk-Forward Test (6 windows)

### Per-Window OOS Results

| Window | OOS Period | V3-default Sharpe | V3-mid Sharpe | V3-tight Sharpe | dSharpe (tight-default) |
|--------|------------|-------------------|---------------|-----------------|-------------------------|
| 1 | 2022-07-01..2022-12-31 | -1.482 | -1.482 | -1.482 | +0.000 |
| 2 | 2023-01-01..2023-06-30 | +0.569 | +0.563 | +0.598 | +0.029 |
| 3 | 2023-07-01..2023-12-31 | +0.771 | +0.650 | +0.714 | -0.057 |
| 4 | 2024-01-01..2024-06-30 | +1.264 | +1.212 | +1.212 | -0.052 |
| 5 | 2024-07-01..2024-12-31 | +0.365 | +0.178 | +0.178 | -0.187 |
| 6 | 2025-01-01..2025-06-30 | +1.613 | +2.334 | +2.053 | +0.440 |

### Per-Window OOS Returns & MaxDD

| Window | V3-default Return | V3-tight Return | V3-default MaxDD | V3-tight MaxDD |
|--------|-------------------|-----------------|------------------|----------------|
| 1 | -48.7% | -48.7% | -31.5% | -31.5% |
| 2 | +17.9% | +19.4% | -16.3% | -16.3% |
| 3 | +22.2% | +20.1% | -16.0% | -16.0% |
| 4 | +61.8% | +60.5% | -27.9% | -28.2% |
| 5 | +10.7% | +5.1% | -23.5% | -23.5% |
| 6 | +52.0% | +63.5% | -9.9% | -9.7% |

### Walk-Forward Summary

| Metric | V3-default | V3-mid | V3-tight |
|--------|------------|--------|----------|
| Mean OOS Sharpe | +0.517 | +0.576 | +0.546 |
| Median OOS Sharpe | +0.670 | +0.607 | +0.656 |
| OOS Sharpe Std | 0.987 | 1.147 | 1.080 |
| Min OOS Sharpe | -1.482 | -1.482 | -1.482 |
| Max OOS Sharpe | +1.613 | +2.334 | +2.053 |
| Positive Windows | 5/6 | 5/6 | 5/6 |
| Mean OOS Return | +19.3% | +21.0% | +20.0% |
| Worst MaxDD | -31.5% | -31.5% | -31.5% |

### V3-tight vs V3-default: Per-Window Delta

- V3-tight wins (dSharpe > +0.01): **2/6**
- V3-tight hurts (dSharpe < -0.01): **3/6**
- Approximately same: **1/6**
- Mean dSharpe: **+0.029**
- Median dSharpe: **-0.026**
- dSharpe range: -0.187 to +0.440

## Part 3: Monotonicity / Smoothness Check

Is the performance curve smooth as threshold tightens (1.5 -> 1.35 -> 1.2)?

- Mean OOS Sharpe monotonically increasing? **False**
- V3-mid between V3-default and V3-tight? **False**
- Per-window monotonic: **3/6**
- Per-window V3-mid between: **3/6**

- V3-default: mean OOS Sharpe = +0.517
- V3-mid: mean OOS Sharpe = +0.576
- V3-tight: mean OOS Sharpe = +0.546

The response is non-monotonic: V3-mid does not sit between the other two. This suggests the improvement may be noise or regime-specific.

## KILL Criteria Evaluation

| Criterion | Result | Threshold | Verdict |
|-----------|--------|-----------|---------|
| V3-tight WF mean Sharpe < V3-default? | tight=+0.546 vs default=+0.517 | tight < default | PASS |
| V3-tight hurts >2/6 windows? | 3/6 hurt | >2 | **KILL** |

## Verdict

### **KEEP** (stay at pos_high_thresh=1.5)

V3-tight fails KILL criteria: hurts 3/6 windows (threshold: >2).

The R67 finding (Sharpe 0.556 -> 0.708) was a single-window observation on 2025 OOS data. Walk-forward validation shows it does not generalize robustly across market regimes.

## Appendix: IS Results (overfit detection)

| Window | V3-default IS Sharpe | V3-tight IS Sharpe | IS dSharpe |
|--------|---------------------|--------------------|-----------|
| 1 | +0.817 | +0.764 | -0.054 |
| 2 | -0.570 | -0.676 | -0.106 |
| 3 | -0.889 | -0.868 | +0.021 |
| 4 | -0.313 | -0.316 | -0.003 |
| 5 | +0.872 | +0.844 | -0.028 |
| 6 | +0.809 | +0.716 | -0.093 |

IS vs OOS dSharpe correlation: -0.601
(Moderate correlation: IS improvements somewhat predict OOS)
