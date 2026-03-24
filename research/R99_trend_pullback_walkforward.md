# R99 -- Trend+Pullback Walk-Forward Validation

## Signal Definition (R98)
- **Daily trend**: EMA(20) vs EMA(50) on daily bars. Up = long bias, Down = short bias.
- **4h pullback entry**: RSI(14) on 4h bars. Long when RSI < threshold in uptrend. Short when RSI > threshold in downtrend.
- **Exit**: RSI crosses back above 50 (longs) or below 50 (shorts), OR max hold days reached.
- **Fees**: 10 bps round-trip (5 bps each way).

## Walk-Forward Protocol
- 10 rolling windows: 180-day train / 90-day test, rolling forward 90 days
- Train: grid search RSI thresholds (long: 30-40 step 2, short: 60-70 step 2) and max hold (5-10 days)
- Optimization target: Sharpe ratio
- Test: apply best train params to OOS window
- Date: 2026-03-24

## Summary Verdicts

| Token | Direction | Verdict | Pos. Windows | Mean Sharpe | Total Trades | Mean OOS Return | Mean WR | Mean DD | Fragile? |
|-------|-----------|---------|-------------|-------------|------------|-----------------|---------|---------|----------|
| BTC | long | **PASS** | 6/10 | 8.64 | 30 | 2.04% | 61.9% | 0.98% | no |
| BTC | short | **KILL** | 4/10 | -0.06 | 31 | -0.10% | 65.5% | 3.73% | YES |
| BTC | combined | **CONDITIONAL PASS** | 7/10 | 3.24 | 63 | 2.60% | 62.6% | 5.63% | YES |
| ETH | long | **KILL** | 6/10 | 37.17 | 22 | 0.11% | 54.0% | 0.91% | YES |
| ETH | short | **KILL** | 3/10 | 2.73 | 47 | -1.67% | 62.1% | 11.56% | YES |
| ETH | combined | **KILL** | 4/10 | 1.22 | 75 | -2.17% | 60.5% | 12.14% | YES |

## Kill Criteria

| Criterion | Threshold |
|-----------|-----------|
| Positive OOS windows | >= 5 / 10 |
| Mean OOS Sharpe | >= 0.3 |
| Total OOS trades | >= 30 |
| Parameter sensitivity | mean Sharpe degradation < 30% with params +/-20% |

### BTC_short: **KILL**
- KILL: <5/10 positive OOS windows (4/10)
- KILL: Mean OOS Sharpe < 0.3 (-0.06)
- CONDITIONAL: Param sensitivity: mean degradation 64.6%

### BTC_combined: **CONDITIONAL PASS**
- CONDITIONAL: Param sensitivity: mean degradation 41.0%

### ETH_long: **KILL**
- KILL: <30 total OOS trades (22)
- CONDITIONAL: Param sensitivity: mean degradation 158.3%

### ETH_short: **KILL**
- KILL: <5/10 positive OOS windows (3/10)
- CONDITIONAL: Param sensitivity: mean degradation 221.2%

### ETH_combined: **KILL**
- KILL: <5/10 positive OOS windows (4/10)
- CONDITIONAL: Param sensitivity: mean degradation 357.6%

## BTC LONG -- Per-Window OOS Results

| Window | Test Period | Optimized Params | Trades | PnL | Sharpe | WR | MaxDD | PF |
|--------|------------|------------------|--------|-----|--------|-----|-------|-----|
| W1 | 2023-09-27 to 2023-12-25 | L<34 S>60 H7d | 4 | 8.20% | 16.34 | 75.0% | 0.00% | 85.16 |
| W2 | 2023-12-26 to 2024-03-24 | L<36 S>60 H8d | 4 | -4.32% | -4.30 | 50.0% | 5.86% | 0.32 |
| W3 | 2024-03-25 to 2024-06-22 | L<30 S>60 H5d | 4 | 7.51% | 8.89 | 75.0% | 2.22% | 4.38 |
| W4 | 2024-06-23 to 2024-09-20 | L<30 S>60 H5d | 1 | -13.14% | 0.00 | 0.0% | 0.00% | 0.00 |
| W5 | 2024-09-21 to 2024-12-19 | L<30 S>60 H8d | 1 | 0.79% | 0.00 | 100.0% | 0.00% | 999.00 |
| W6 | 2024-12-20 to 2025-03-19 | L<36 S>60 H5d | 7 | 14.53% | 15.18 | 85.7% | 0.81% | 18.90 |
| W7 | 2025-03-20 to 2025-06-17 | L<30 S>60 H5d | 2 | 4.71% | 34.01 | 100.0% | 0.00% | 999.00 |
| W8 | 2025-06-18 to 2025-09-15 | L<30 S>60 H5d | 3 | 6.46% | 17.70 | 100.0% | 0.00% | 999.00 |
| W9 | 2025-09-16 to 2025-12-14 | L<30 S>60 H5d | 3 | -1.01% | -1.41 | 33.3% | 0.94% | 0.74 |
| W10 | 2025-12-15 to 2026-03-14 | L<32 S>60 H7d | 1 | -3.33% | 0.00 | 0.0% | 0.00% | 0.00 |

## BTC SHORT -- Per-Window OOS Results

| Window | Test Period | Optimized Params | Trades | PnL | Sharpe | WR | MaxDD | PF |
|--------|------------|------------------|--------|-----|--------|-----|-------|-----|
| W1 | 2023-09-27 to 2023-12-25 | L<30 S>60 H6d | 2 | 0.13% | 0.43 | 50.0% | 0.00% | 1.09 |
| W2 | 2023-12-26 to 2024-03-24 | L<30 S>70 H5d | 0 | - | - | - | - | - |
| W3 | 2024-03-25 to 2024-06-22 | L<35 S>65 H7d | 2 | -6.03% | -4.72 | 50.0% | 8.33% | 0.28 |
| W4 | 2024-06-23 to 2024-09-20 | L<30 S>64 H5d | 7 | -7.00% | -3.35 | 57.1% | 8.14% | 0.45 |
| W5 | 2024-09-21 to 2024-12-19 | L<30 S>70 H5d | 0 | - | - | - | - | - |
| W6 | 2024-12-20 to 2025-03-19 | L<30 S>70 H5d | 1 | 9.05% | 0.00 | 100.0% | 0.00% | 999.00 |
| W7 | 2025-03-20 to 2025-06-17 | L<30 S>64 H5d | 4 | -0.64% | -0.35 | 75.0% | 8.15% | 0.92 |
| W8 | 2025-06-18 to 2025-09-15 | L<30 S>64 H7d | 3 | 2.15% | 7.63 | 66.7% | 1.06% | 3.03 |
| W9 | 2025-09-16 to 2025-12-14 | L<30 S>62 H7d | 8 | 2.04% | 0.77 | 75.0% | 0.65% | 1.20 |
| W10 | 2025-12-15 to 2026-03-14 | L<30 S>66 H5d | 4 | -0.53% | -0.85 | 50.0% | 3.49% | 0.85 |

## BTC COMBINED -- Per-Window OOS Results

| Window | Test Period | Optimized Params | Trades | PnL | Sharpe | WR | MaxDD | PF |
|--------|------------|------------------|--------|-----|--------|-----|-------|-----|
| W1 | 2023-09-27 to 2023-12-25 | L<34 S>60 H10d | 6 | 8.33% | 8.74 | 66.7% | 0.10% | 6.61 |
| W2 | 2023-12-26 to 2024-03-24 | L<36 S>70 H8d | 4 | -4.32% | -4.30 | 50.0% | 5.86% | 0.32 |
| W3 | 2024-03-25 to 2024-06-22 | L<30 S>68 H5d | 5 | 3.31% | 2.16 | 60.0% | 4.20% | 1.52 |
| W4 | 2024-06-23 to 2024-09-20 | L<30 S>64 H5d | 8 | -20.13% | -5.07 | 50.0% | 20.20% | 0.22 |
| W5 | 2024-09-21 to 2024-12-19 | L<30 S>60 H8d | 2 | 0.57% | 5.68 | 50.0% | 0.00% | 3.57 |
| W6 | 2024-12-20 to 2025-03-19 | L<36 S>70 H5d | 8 | 23.58% | 14.73 | 87.5% | 0.81% | 30.05 |
| W7 | 2025-03-20 to 2025-06-17 | L<30 S>66 H5d | 5 | 2.32% | 1.19 | 80.0% | 8.15% | 1.28 |
| W8 | 2025-06-18 to 2025-09-15 | L<34 S>64 H7d | 9 | 11.68% | 11.35 | 77.8% | 1.06% | 10.09 |
| W9 | 2025-09-16 to 2025-12-14 | L<32 S>62 H7d | 11 | 3.47% | 1.00 | 63.6% | 10.22% | 1.30 |
| W10 | 2025-12-15 to 2026-03-14 | L<32 S>66 H8d | 5 | -2.85% | -3.05 | 40.0% | 5.72% | 0.51 |

## ETH LONG -- Per-Window OOS Results

| Window | Test Period | Optimized Params | Trades | PnL | Sharpe | WR | MaxDD | PF |
|--------|------------|------------------|--------|-----|--------|-----|-------|-----|
| W1 | 2023-09-27 to 2023-12-25 | L<32 S>60 H7d | 2 | 5.81% | 396.75 | 100.0% | 0.00% | 999.00 |
| W2 | 2023-12-26 to 2024-03-24 | L<30 S>60 H5d | 2 | -7.24% | -30.36 | 0.0% | 2.53% | 0.00 |
| W3 | 2024-03-25 to 2024-06-22 | L<36 S>60 H7d | 5 | -0.17% | -0.11 | 40.0% | 5.99% | 0.97 |
| W4 | 2024-06-23 to 2024-09-20 | L<40 S>60 H5d | 1 | 0.73% | 0.00 | 100.0% | 0.00% | 999.00 |
| W5 | 2024-09-21 to 2024-12-19 | L<30 S>60 H7d | 3 | 0.77% | 1.90 | 33.3% | 0.56% | 1.41 |
| W6 | 2024-12-20 to 2025-03-19 | L<30 S>60 H5d | 2 | 1.99% | 2.45 | 50.0% | 0.00% | 1.60 |
| W7 | 2025-03-20 to 2025-06-17 | L<30 S>60 H5d | 1 | 4.71% | 0.00 | 100.0% | 0.00% | 999.00 |
| W8 | 2025-06-18 to 2025-09-15 | L<34 S>60 H5d | 3 | 6.83% | 9.54 | 66.7% | 0.00% | 21.55 |
| W9 | 2025-09-16 to 2025-12-14 | L<30 S>60 H5d | 2 | -6.26% | -8.48 | 50.0% | 0.00% | 0.09 |
| W10 | 2025-12-15 to 2026-03-14 | L<32 S>60 H7d | 1 | -6.03% | 0.00 | 0.0% | 0.00% | 0.00 |

## ETH SHORT -- Per-Window OOS Results

| Window | Test Period | Optimized Params | Trades | PnL | Sharpe | WR | MaxDD | PF |
|--------|------------|------------------|--------|-----|--------|-----|-------|-----|
| W1 | 2023-09-27 to 2023-12-25 | L<30 S>66 H6d | 3 | -11.25% | -6.88 | 33.3% | 11.19% | 0.11 |
| W2 | 2023-12-26 to 2024-03-24 | L<30 S>60 H5d | 0 | - | - | - | - | - |
| W3 | 2024-03-25 to 2024-06-22 | L<30 S>70 H10d | 2 | -17.76% | -5.65 | 50.0% | 21.69% | 0.18 |
| W4 | 2024-06-23 to 2024-09-20 | L<30 S>62 H7d | 8 | -0.86% | -0.30 | 75.0% | 9.91% | 0.94 |
| W5 | 2024-09-21 to 2024-12-19 | L<30 S>62 H7d | 7 | -21.55% | -4.16 | 57.1% | 22.67% | 0.27 |
| W6 | 2024-12-20 to 2025-03-19 | L<30 S>62 H5d | 6 | 24.85% | 27.50 | 83.3% | 1.22% | 21.29 |
| W7 | 2025-03-20 to 2025-06-17 | L<30 S>60 H5d | 10 | -2.23% | -0.38 | 80.0% | 21.80% | 0.92 |
| W8 | 2025-06-18 to 2025-09-15 | L<30 S>60 H5d | 0 | - | - | - | - | - |
| W9 | 2025-09-16 to 2025-12-14 | L<30 S>60 H5d | 7 | 6.93% | 4.95 | 42.9% | 3.96% | 2.70 |
| W10 | 2025-12-15 to 2026-03-14 | L<30 S>70 H5d | 4 | 8.48% | 6.74 | 75.0% | 0.00% | 4.18 |

## ETH COMBINED -- Per-Window OOS Results

| Window | Test Period | Optimized Params | Trades | PnL | Sharpe | WR | MaxDD | PF |
|--------|------------|------------------|--------|-----|--------|-----|-------|-----|
| W1 | 2023-09-27 to 2023-12-25 | L<32 S>66 H7d | 5 | -3.89% | -1.81 | 60.0% | 9.64% | 0.65 |
| W2 | 2023-12-26 to 2024-03-24 | L<36 S>70 H5d | 6 | -9.36% | -2.30 | 66.7% | 18.45% | 0.59 |
| W3 | 2024-03-25 to 2024-06-22 | L<36 S>70 H5d | 8 | -21.59% | -4.10 | 37.5% | 25.69% | 0.33 |
| W4 | 2024-06-23 to 2024-09-20 | L<36 S>62 H7d | 9 | -0.12% | -0.04 | 77.8% | 9.91% | 0.99 |
| W5 | 2024-09-21 to 2024-12-19 | L<30 S>62 H7d | 10 | -20.78% | -3.41 | 50.0% | 23.69% | 0.34 |
| W6 | 2024-12-20 to 2025-03-19 | L<30 S>62 H5d | 8 | 26.84% | 14.38 | 75.0% | 1.22% | 6.91 |
| W7 | 2025-03-20 to 2025-06-17 | L<30 S>60 H5d | 11 | 2.48% | 0.39 | 81.8% | 21.80% | 1.09 |
| W8 | 2025-06-18 to 2025-09-15 | L<34 S>60 H5d | 3 | 6.83% | 9.54 | 66.7% | 0.00% | 21.55 |
| W9 | 2025-09-16 to 2025-12-14 | L<38 S>60 H5d | 10 | -4.57% | -1.55 | 30.0% | 4.99% | 0.71 |
| W10 | 2025-12-15 to 2026-03-14 | L<32 S>70 H7d | 5 | 2.45% | 1.05 | 60.0% | 6.03% | 1.28 |

## Parameter Sensitivity

Base train-optimal params adjusted +/-20% and re-run on each OOS window.
>30% mean Sharpe degradation = FRAGILE.

### BTC LONG (mean degradation: -37.3%)

| Window | Base Sharpe | -20% Sharpe | +20% Sharpe | -20% Degrade | +20% Degrade |
|--------|------------|------------|------------|-------------|-------------|
| W1 | 16.34 | 73.23 | 9.59 | -348.2% | 41.3% |
| W2 | -4.30 | -0.14 | 2.82 | -96.8% | -165.5% |
| W3 | 8.89 | 0.00 | 1.61 | 100.0% | 81.9% |
| W4 | 0.00 | 0.00 | -7.09 | n/a | n/a |
| W5 | 0.00 | 0.00 | 9.27 | n/a | n/a |
| W6 | 15.18 | 228.94 | 9.12 | -1408.5% | 39.9% |
| W7 | 34.01 | 0.00 | 8.92 | 100.0% | 73.8% |
| W8 | 17.70 | 0.00 | 13.50 | 100.0% | 23.7% |
| W9 | -1.41 | -1.06 | -14.95 | -24.7% | 960.2% |
| W10 | 0.00 | 0.00 | 0.00 | n/a | n/a |

### BTC SHORT (mean degradation: 64.6%)

| Window | Base Sharpe | -20% Sharpe | +20% Sharpe | -20% Degrade | +20% Degrade |
|--------|------------|------------|------------|-------------|-------------|
| W1 | 0.43 | -2.94 | 0.00 | 777.6% | 100.0% |
| W2 | 0.00 | 0.00 | 0.00 | n/a | n/a |
| W3 | -4.72 | -3.23 | 0.00 | -31.7% | -100.0% |
| W4 | -3.35 | -2.33 | 0.00 | -30.4% | -100.0% |
| W5 | 0.00 | 0.00 | 0.00 | n/a | n/a |
| W6 | 0.00 | 16.59 | 0.00 | n/a | n/a |
| W7 | -0.35 | -1.84 | 0.25 | 421.1% | -169.7% |
| W8 | 7.63 | -1.81 | 0.00 | 123.8% | 100.0% |
| W9 | 0.77 | 2.73 | -4.36 | -256.3% | 668.7% |
| W10 | -0.85 | 3.39 | 0.00 | -498.5% | -100.0% |

### BTC COMBINED (mean degradation: 41.0%)

| Window | Base Sharpe | -20% Sharpe | +20% Sharpe | -20% Degrade | +20% Degrade |
|--------|------------|------------|------------|-------------|-------------|
| W1 | 8.74 | 3.25 | 11.14 | 62.9% | -27.4% |
| W2 | -4.30 | -0.14 | 2.82 | -96.8% | -165.5% |
| W3 | 2.16 | 0.17 | 1.61 | 92.1% | 25.7% |
| W4 | -5.07 | -1.08 | -8.45 | -78.6% | 66.8% |
| W5 | 5.68 | 8.47 | 9.27 | -49.1% | -63.2% |
| W6 | 14.73 | 19.95 | 9.12 | -35.5% | 38.0% |
| W7 | 1.19 | -2.28 | 0.19 | 291.7% | 84.0% |
| W8 | 11.35 | 4.09 | 4.88 | 63.9% | 57.0% |
| W9 | 1.00 | 2.16 | -7.68 | -115.8% | 866.2% |
| W10 | -3.05 | 2.21 | -2.32 | -172.5% | -23.9% |

### ETH LONG (mean degradation: 158.3%)

| Window | Base Sharpe | -20% Sharpe | +20% Sharpe | -20% Degrade | +20% Degrade |
|--------|------------|------------|------------|-------------|-------------|
| W1 | 396.75 | 0.00 | 23.76 | 100.0% | 94.0% |
| W2 | -30.36 | 0.00 | -4.21 | -100.0% | -86.1% |
| W3 | -0.11 | -0.97 | -2.85 | 747.4% | 2394.7% |
| W4 | 0.00 | 0.00 | 2.20 | n/a | n/a |
| W5 | 1.90 | 0.00 | 1.53 | 100.0% | 19.7% |
| W6 | 2.45 | 10.83 | -0.62 | -342.2% | 125.2% |
| W7 | 0.00 | 0.00 | 93.34 | n/a | n/a |
| W8 | 9.54 | 76.60 | 12.39 | -703.0% | -29.9% |
| W9 | -8.48 | -3.51 | -4.64 | -58.7% | -45.3% |
| W10 | 0.00 | 0.00 | 0.00 | n/a | n/a |

### ETH SHORT (mean degradation: 221.2%)

| Window | Base Sharpe | -20% Sharpe | +20% Sharpe | -20% Degrade | +20% Degrade |
|--------|------------|------------|------------|-------------|-------------|
| W1 | -6.88 | -6.80 | 0.00 | -1.2% | -100.0% |
| W2 | 0.00 | 0.00 | 0.00 | n/a | n/a |
| W3 | -5.65 | -4.74 | 0.00 | -16.1% | -100.0% |
| W4 | -0.30 | 1.48 | -10.54 | -593.6% | 3404.2% |
| W5 | -4.16 | -5.22 | -5.65 | 25.4% | 35.6% |
| W6 | 27.50 | 11.45 | 0.00 | 58.4% | 100.0% |
| W7 | -0.38 | -2.08 | -5.13 | 447.7% | 1250.8% |
| W8 | 0.00 | 0.00 | 0.00 | n/a | n/a |
| W9 | 4.95 | 4.99 | 61.09 | -0.8% | -1133.5% |
| W10 | 6.74 | 2.53 | 0.00 | 62.4% | 100.0% |

### ETH COMBINED (mean degradation: 357.6%)

| Window | Base Sharpe | -20% Sharpe | +20% Sharpe | -20% Degrade | +20% Degrade |
|--------|------------|------------|------------|-------------|-------------|
| W1 | -1.81 | -6.73 | 3.37 | 271.9% | -286.0% |
| W2 | -2.30 | -10.98 | 0.29 | 377.6% | -112.7% |
| W3 | -4.10 | -3.92 | -4.72 | -4.5% | 14.9% |
| W4 | -0.04 | 2.15 | -4.71 | -5492.5% | 11687.9% |
| W5 | -3.41 | -4.86 | -3.32 | 42.7% | -2.7% |
| W6 | 14.38 | 11.16 | -0.62 | 22.4% | 104.3% |
| W7 | 0.39 | -2.08 | -1.48 | 633.1% | 480.3% |
| W8 | 9.54 | 76.60 | 12.39 | -703.0% | -29.9% |
| W9 | -1.55 | 2.86 | 0.87 | -284.4% | -155.7% |
| W10 | 1.05 | 0.76 | -4.86 | 28.1% | 561.0% |

## Regime Robustness (Up vs Down Markets)

OOS trades split by daily close vs 50-day SMA at entry.

### BTC LONG

| Regime | Trades | PnL | Sharpe | WR | MaxDD | PF |
|--------|--------|-----|--------|-----|-------|-----|
| Up (>SMA50) | 22 | 2.03% | 0.26 | 63.6% | 15.60% | 1.07 |
| Down (<SMA50) | 8 | 18.38% | 16.39 | 87.5% | 0.81% | 23.65 |

### BTC SHORT

| Regime | Trades | PnL | Sharpe | WR | MaxDD | PF |
|--------|--------|-----|--------|-----|-------|-----|
| Up (>SMA50) | 5 | -9.89% | -5.76 | 40.0% | 11.36% | 0.15 |
| Down (<SMA50) | 26 | 9.06% | 1.01 | 69.2% | 15.06% | 1.27 |

### BTC COMBINED

| Regime | Trades | PnL | Sharpe | WR | MaxDD | PF |
|--------|--------|-----|--------|-----|-------|-----|
| Up (>SMA50) | 29 | -4.65% | -0.46 | 55.2% | 16.30% | 0.88 |
| Down (<SMA50) | 34 | 30.62% | 3.15 | 73.5% | 11.23% | 2.01 |

### ETH LONG

| Regime | Trades | PnL | Sharpe | WR | MaxDD | PF |
|--------|--------|-----|--------|-----|-------|-----|
| Up (>SMA50) | 19 | -0.76% | -0.12 | 47.4% | 12.47% | 0.97 |
| Down (<SMA50) | 3 | 1.91% | 1.74 | 66.7% | 0.00% | 1.48 |

### ETH SHORT

| Regime | Trades | PnL | Sharpe | WR | MaxDD | PF |
|--------|--------|-----|--------|-----|-------|-----|
| Up (>SMA50) | 11 | -15.12% | -2.11 | 72.7% | 21.80% | 0.52 |
| Down (<SMA50) | 36 | 1.73% | 0.09 | 63.9% | 42.84% | 1.02 |

### ETH COMBINED

| Regime | Trades | PnL | Sharpe | WR | MaxDD | PF |
|--------|--------|-----|--------|-----|-------|-----|
| Up (>SMA50) | 35 | -31.09% | -1.70 | 57.1% | 37.65% | 0.63 |
| Down (<SMA50) | 40 | 9.38% | 0.48 | 62.5% | 40.56% | 1.11 |

## Final Verdict

| Token | Direction | Verdict | Reason |
|-------|-----------|---------|--------|
| BTC | long | **PASS** | All criteria passed |
| BTC | short | **KILL** | <5/10 positive OOS windows (4/10); Mean OOS Sharpe < 0.3 (-0.06); Param sensitivity: mean degradation 64.6% |
| BTC | combined | **CONDITIONAL PASS** | Param sensitivity: mean degradation 41.0% |
| ETH | long | **KILL** | <30 total OOS trades (22); Param sensitivity: mean degradation 158.3% |
| ETH | short | **KILL** | <5/10 positive OOS windows (3/10); Param sensitivity: mean degradation 221.2% |
| ETH | combined | **KILL** | <5/10 positive OOS windows (4/10); Param sensitivity: mean degradation 357.6% |

### Overall Recommendation

**Some token/direction combinations FAIL walk-forward validation:**

- **BTC_short**: KILL -- <5/10 positive OOS windows (4/10); Mean OOS Sharpe < 0.3 (-0.06)
- **ETH_long**: KILL -- <30 total OOS trades (22)
- **ETH_short**: KILL -- <5/10 positive OOS windows (3/10)
- **ETH_combined**: KILL -- <5/10 positive OOS windows (4/10)

**Conditional passes (parameter fragility detected):**

- **BTC_combined**: CONDITIONAL -- Param sensitivity: mean degradation 41.0%

**Clean passes:**

- **BTC_long**: PASS

Viable for deployment: BTC_combined, BTC_long
Consider restricting to passing directions only.
