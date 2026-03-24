# V3 Momentum Strategy: Walk-Forward & Parameter Sensitivity Results

**Run date**: 2026-03-24 11:59

## Strategy Definition (V3)

- **Base**: Long when 20d EMA > 50d EMA, flat otherwise. No stop losses.
- **Positioning overlay**: Binance Top Trader L/S + L/S Divergence combined z-score (30d) -> 0.3x to 1.5x
- **VRP overlay**: (IV - RV) z-score (60d) -> 0.3x to 1.3x
- **Rebalancing**: Weekly (Monday). Cost: 10 bps round-trip. Position range: [0, 1.5x].

## Part 1: Walk-Forward Test

6 rolling windows, each 18-month IS + 6-month OOS.

| Window | IS Period | OOS Period | IS Sharpe | OOS Sharpe | IS Return | OOS Return | IS MaxDD | OOS MaxDD | OOS BH Sharpe |
|--------|-----------|------------|-----------|------------|-----------|------------|----------|----------|---------------|
| 1 | 2021-01-01 to 2022-06-30 | 2022-07-01 to 2022-12-31 | +0.817 | -1.482 | +43.1% | -48.7% | -34.4% | -31.5% | -0.554 |
| 2 | 2021-07-01 to 2022-12-31 | 2023-01-01 to 2023-06-30 | -0.570 | +0.569 | -20.0% | +17.9% | -53.3% | -16.3% | +4.912 |
| 3 | 2022-01-01 to 2023-06-30 | 2023-07-01 to 2023-12-31 | -0.889 | +0.771 | -25.7% | +22.2% | -43.6% | -16.0% | +2.400 |
| 4 | 2022-07-01 to 2023-12-31 | 2024-01-01 to 2024-06-30 | -0.313 | +1.264 | -9.7% | +61.8% | -32.8% | -27.9% | +2.208 |
| 5 | 2023-01-01 to 2024-06-30 | 2024-07-01 to 2024-12-31 | +0.872 | +0.365 | +32.6% | +10.7% | -27.9% | -23.5% | +2.344 |
| 6 | 2023-07-01 to 2024-12-31 | 2025-01-01 to 2025-06-30 | +0.809 | +1.613 | +29.7% | +52.0% | -44.8% | -9.9% | +0.671 |

### Walk-Forward Summary

- Windows with positive OOS Sharpe: **5/6**
- Windows with OOS Sharpe > 0.3: **5/6**
- OOS Sharpe range: -1.482 to 1.613
- OOS Sharpe mean: 0.517
- OOS Sharpe median: 0.670
- Trend: **IMPROVING**

- IS/OOS efficiency ratio: 4.27 (1.0 = no degradation, <0.5 = suspect overfit)

## Part 2: Parameter Sensitivity

Each parameter tested at +-20% of default, others held constant.
OOS period: 2025-01-01 to latest.

| Parameter | Low Value | Low Sharpe | Default Value | Default Sharpe | High Value | High Sharpe | Max Degradation |
|-----------|----------|------------|---------------|----------------|------------|-------------|-----------------|
| fast_ema | 16 | +0.556 | 20 | +0.556 | 24 | +0.620 | 0.0% |
| slow_ema | 40 | +0.600 | 50 | +0.556 | 60 | +0.576 | 0.0% |
| pos_z_window | 24 | +0.557 | 30 | +0.556 | 36 | +0.630 | 0.0% |
| vrp_z_window | 48 | +0.564 | 60 | +0.556 | 72 | +0.515 | 7.3% |
| pos_high_thresh | 1.2 | +0.708 | 1.5 | +0.556 | 1.8 | +0.577 | 0.0% |
| vrp_high_thresh | 0.8 | +0.601 | 1.0 | +0.556 | 1.2 | +0.510 | 8.2% |

### No KILL flags. All +-20% perturbations maintain Sharpe within 30%.

### Detailed Sensitivity (Return & MaxDD)

| Parameter | Value | OOS Sharpe | OOS Return | OOS MaxDD |
|-----------|-------|------------|------------|-----------|
| fast_ema | 16 | +0.556 | +14.7% | -20.2% |
| fast_ema | 20 (default) | +0.556 | +14.7% | -20.2% |
| fast_ema | 24 | +0.620 | +16.3% | -15.1% |
| slow_ema | 40 | +0.600 | +15.8% | -19.3% |
| slow_ema | 50 (default) | +0.556 | +14.7% | -20.2% |
| slow_ema | 60 | +0.576 | +15.2% | -15.1% |
| pos_z_window | 24 | +0.557 | +14.4% | -18.5% |
| pos_z_window | 30 (default) | +0.556 | +14.7% | -20.2% |
| pos_z_window | 36 | +0.630 | +16.2% | -18.5% |
| vrp_z_window | 48 | +0.564 | +15.0% | -19.3% |
| vrp_z_window | 60 (default) | +0.556 | +14.7% | -20.2% |
| vrp_z_window | 72 | +0.515 | +13.7% | -20.2% |
| pos_high_thresh | 1.2 | +0.708 | +18.2% | -20.2% |
| pos_high_thresh | 1.5 (default) | +0.556 | +14.7% | -20.2% |
| pos_high_thresh | 1.8 | +0.577 | +14.8% | -18.5% |
| vrp_high_thresh | 0.8 | +0.601 | +16.3% | -19.8% |
| vrp_high_thresh | 1.0 (default) | +0.556 | +14.7% | -20.2% |
| vrp_high_thresh | 1.2 | +0.510 | +13.2% | -20.2% |

## Part 3: Combinatorial Stability

4 extreme corner cases tested on OOS period (2025-01-01 to latest).

| Configuration | Fast EMA | Slow EMA | Pos Z-Thresh | OOS Sharpe | OOS Return | OOS MaxDD |
|---------------|----------|----------|-------------|------------|------------|-----------|
| Default (20/50, thresh=1.5) | 20 | 50 | 1.5 | +0.556 | +14.7% | -20.2% |
| Fast base + tight overlays | - | - | - | +0.824 | +20.9% | -20.1% |
| Slow base + wide overlays | - | - | - | +0.531 | +13.8% | -15.1% |
| Fast base + wide overlays | - | - | - | +0.689 | +17.3% | -18.4% |
| Slow base + tight overlays | - | - | - | +0.729 | +18.8% | -14.9% |

- Sharpe range across all configurations: 0.293
- Sharpe std across all configurations: 0.109
- Configurations with positive Sharpe: 5/5

## Verdict

### **ROBUST**

Walk-forward: 5/6 windows positive OOS (mean Sharpe 0.517). Parameter sensitivity: no KILL flags (all +-20% perturbations within tolerance). Corner cases: 5/5 configurations positive.

### Scoring Breakdown

| Dimension | Score | Max | Assessment |
|-----------|-------|-----|------------|
| Walk-Forward | 2 | 2 | 5/6 positive OOS windows |
| Parameter Sensitivity | 2 | 2 | 0 KILL flags |
| Corner Cases | 2 | 2 | 5/5 positive configs |
| **Total** | **6** | **6** | ROBUST |

### Interpretation

- Score >= 5: ROBUST -- safe for production
- Score 3-4: FRAGILE -- needs further tuning or conditional deployment
- Score < 3: KILL -- do not deploy
