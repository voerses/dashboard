# R102: BB Squeeze Breakout Walk-Forward Validation

**Signal**: Bollinger Band squeeze detection (BB Width < rolling percentile for N+ consecutive hours) with breakout entry
**Protocol**: 10 rolling windows, 180-day train / 90-day test, rolling 90 days
**Date**: 2026-03-24
**Prior result**: PF 4.54 on full sample (R96), short-biased. This study validates OOS.

## Signal Definition

- **Setup**: BB Width (N-period, K std) falls below its rolling 100-bar Xth percentile for at least D consecutive hours = squeeze detected
- **Entry**: When price closes above upper BB during/after squeeze -> LONG. When price closes below lower BB -> SHORT.
- **Exit**: Price crosses back below middle BB (long) / above middle BB (short), OR max hold H hours, OR stop loss S%.
- **Fees**: 10bps round-trip (5bps each way)

## BTC Walk-Forward Results

- Positive OOS windows: **2/10**
- Mean OOS Sharpe: **-17.282**
- Total OOS trades: **54**
- Long: 26 trades, 4 positive windows, mean Sharpe=-19.428
- Short: 28 trades, 2 positive windows, mean Sharpe=-34.529

### Per-Window OOS Metrics

| Window | Test Period | Trades | L/S | Win Rate | PF | PnL | Sharpe | MaxDD | Params |
|--------|------------|--------|-----|----------|-----|-----|--------|-------|--------|
| W1 | 2021-12-28 to 2022-03-27 | 11 | 4/7 | 27.3% | 0.43 | -6.56% | -12.58 | 9.8% | p=18,s=1.75,q=30,d=30,h=48,sl=1.5 |
| W2 | 2022-03-28 to 2022-06-25 | 8 | 4/4 | 37.5% | 0.84 | -1.07% | -1.83 | 4.3% | p=15,s=2.0,q=20,d=24,h=48,sl=2.0 |
| W3 | 2022-06-26 to 2022-09-23 | 0 | 0/0 | 0.0% | 0.00 | 0.00% | 0.00 | 0.0% | p=18,s=2.5,q=20,d=30,h=48,sl=2.5 |
| W4 | 2022-09-24 to 2022-12-22 | 4 | 1/3 | 50.0% | 3.39 | 3.41% | 7.45 | 1.4% | p=20,s=2.25,q=25,d=36,h=48,sl=2.0 |
| W5 | 2022-12-23 to 2023-03-22 | 1 | 0/1 | 0.0% | 0.00 | -0.42% | 0.00 | 0.0% | p=20,s=2.25,q=25,d=36,h=48,sl=2.0 |
| W6 | 2023-03-23 to 2023-06-20 | 2 | 2/0 | 0.0% | 0.00 | -0.94% | -100.00 | 0.4% | p=20,s=2.25,q=25,d=36,h=48,sl=2.0 |
| W7 | 2023-06-21 to 2023-09-18 | 20 | 11/9 | 0.0% | 0.00 | -7.13% | -68.18 | 6.3% | p=20,s=1.5,q=10,d=12,h=24,sl=2.5 |
| W8 | 2023-09-19 to 2023-12-17 | 3 | 2/1 | 66.7% | 1.58 | 0.60% | 4.31 | 0.0% | p=22,s=2.0,q=25,d=36,h=72,sl=3.0 |
| W9 | 2023-12-18 to 2024-03-16 | 5 | 2/3 | 20.0% | 0.84 | -0.76% | -1.98 | 3.1% | p=25,s=2.25,q=10,d=24,h=24,sl=2.0 |
| W10 | 2024-03-17 to 2024-06-14 | 0 | 0/0 | 0.0% | 0.00 | 0.00% | 0.00 | 0.0% | p=22,s=2.0,q=10,d=30,h=24,sl=2.0 |

### In-Sample vs OOS Comparison

| Window | IS Trades | IS Sharpe | IS PnL | OOS Trades | OOS Sharpe | OOS PnL | Degradation |
|--------|-----------|-----------|--------|------------|------------|---------|-------------|
| W1 | 10 | 6.99 | 11.22% | 11 | -12.58 | -6.56% | +280% |
| W2 | 5 | 12.45 | 16.46% | 8 | -1.83 | -1.07% | +115% |
| W3 | 5 | 11.90 | 6.63% | 0 | 0.00 | 0.00% | +100% |
| W4 | 5 | 13.90 | 6.46% | 4 | 7.45 | 3.41% | +46% |
| W5 | 5 | 10.62 | 5.83% | 1 | 0.00 | -0.42% | +100% |
| W6 | 5 | 6.12 | 2.98% | 2 | -100.00 | -0.94% | +1734% |
| W7 | 20 | 2.59 | 3.33% | 20 | -68.18 | -7.13% | +2728% |
| W8 | 7 | 5.75 | 1.21% | 3 | 4.31 | 0.60% | +25% |
| W9 | 7 | 12.10 | 6.38% | 5 | -1.98 | -0.76% | +116% |
| W10 | 5 | 10.40 | 4.16% | 0 | 0.00 | 0.00% | +100% |

### Long vs Short OOS Breakdown

| Window | Long Trades | Long WR | Long PnL | Long Sharpe | Short Trades | Short WR | Short PnL | Short Sharpe |
|--------|-------------|---------|----------|-------------|--------------|----------|-----------|--------------|
| W1 | 4 | 50.0% | 1.51% | 5.87 | 7 | 14.3% | -8.07% | -55.59 |
| W2 | 4 | 50.0% | 1.94% | 4.66 | 4 | 25.0% | -3.01% | -22.27 |
| W3 | 0 | 0.0% | 0.00% | 0.00 | 0 | 0.0% | 0.00% | 0.00 |
| W4 | 1 | 0.0% | -0.60% | 0.00 | 3 | 66.7% | 4.01% | 10.21 |
| W5 | 0 | 0.0% | 0.00% | 0.00 | 1 | 0.0% | -0.42% | 0.00 |
| W6 | 2 | 0.0% | -0.94% | -100.00 | 0 | 0.0% | 0.00% | 0.00 |
| W7 | 11 | 0.0% | -3.94% | -61.54 | 9 | 0.0% | -3.19% | -83.49 |
| W8 | 2 | 50.0% | 0.17% | 1.66 | 1 | 100.0% | 0.43% | 0.00 |
| W9 | 2 | 50.0% | 2.86% | 13.35 | 3 | 0.0% | -3.62% | -90.56 |
| W10 | 0 | 0.0% | 0.00% | 0.00 | 0 | 0.0% | 0.00% | 0.00 |

## ETH Walk-Forward Results

- Positive OOS windows: **2/10**
- Mean OOS Sharpe: **-27.771**
- Total OOS trades: **64**
- Long: 27 trades, 5 positive windows, mean Sharpe=-28.328
- Short: 37 trades, 1 positive windows, mean Sharpe=-56.578

### Per-Window OOS Metrics

| Window | Test Period | Trades | L/S | Win Rate | PF | PnL | Sharpe | MaxDD | Params |
|--------|------------|--------|-----|----------|-----|-----|--------|-------|--------|
| W1 | 2021-12-28 to 2022-03-27 | 6 | 1/5 | 16.7% | 0.63 | -2.20% | -7.42 | 4.7% | p=20,s=1.5,q=25,d=30,h=36,sl=1.0 |
| W2 | 2022-03-28 to 2022-06-25 | 12 | 6/6 | 41.7% | 2.59 | 15.51% | 7.05 | 4.6% | p=20,s=1.75,q=30,d=24,h=36,sl=3.0 |
| W3 | 2022-06-26 to 2022-09-23 | 4 | 2/2 | 0.0% | 0.00 | -6.76% | -100.00 | 5.0% | p=25,s=1.5,q=15,d=30,h=48,sl=1.5 |
| W4 | 2022-09-24 to 2022-12-22 | 4 | 1/3 | 50.0% | 0.43 | -2.65% | -9.37 | 3.4% | p=15,s=2.25,q=30,d=30,h=36,sl=3.0 |
| W5 | 2022-12-23 to 2023-03-22 | 5 | 3/2 | 20.0% | 1.03 | 0.12% | 0.37 | 2.6% | p=20,s=2.25,q=20,d=24,h=72,sl=3.0 |
| W6 | 2023-03-23 to 2023-06-20 | 2 | 2/0 | 0.0% | 0.00 | -2.94% | -64.48 | 0.7% | p=20,s=2.5,q=15,d=18,h=60,sl=2.0 |
| W7 | 2023-06-21 to 2023-09-18 | 8 | 3/5 | 0.0% | 0.00 | -4.26% | -92.12 | 3.7% | p=15,s=2.5,q=20,d=18,h=72,sl=2.5 |
| W8 | 2023-09-19 to 2023-12-17 | 18 | 8/10 | 16.7% | 0.63 | -3.55% | -5.83 | 6.0% | p=20,s=1.5,q=10,d=12,h=24,sl=2.5 |
| W9 | 2023-12-18 to 2024-03-16 | 4 | 1/3 | 25.0% | 0.63 | -1.03% | -5.90 | 1.5% | p=25,s=2.0,q=15,d=36,h=24,sl=2.0 |
| W10 | 2024-03-17 to 2024-06-14 | 1 | 0/1 | 0.0% | 0.00 | -2.64% | 0.00 | 0.0% | p=18,s=2.25,q=25,d=36,h=36,sl=2.5 |

### In-Sample vs OOS Comparison

| Window | IS Trades | IS Sharpe | IS PnL | OOS Trades | OOS Sharpe | OOS PnL | Degradation |
|--------|-----------|-----------|--------|------------|------------|---------|-------------|
| W1 | 7 | 9.47 | 13.80% | 6 | -7.42 | -2.20% | +178% |
| W2 | 22 | 1.41 | 3.67% | 12 | 7.05 | 15.51% | -401% |
| W3 | 5 | 8.09 | 8.97% | 4 | -100.00 | -6.76% | +1335% |
| W4 | 6 | 10.12 | 13.91% | 4 | -9.37 | -2.65% | +193% |
| W5 | 5 | 6.80 | 2.69% | 5 | 0.37 | 0.12% | +95% |
| W6 | 10 | 6.54 | 6.34% | 2 | -64.48 | -2.94% | +1086% |
| W7 | 5 | 13.48 | 7.95% | 8 | -92.12 | -4.26% | +783% |
| W8 | 18 | 0.10 | 0.10% | 18 | -5.83 | -3.55% | +5934% |
| W9 | 5 | 2.79 | 0.93% | 4 | -5.90 | -1.03% | +311% |
| W10 | 8 | 8.61 | 11.22% | 1 | 0.00 | -2.64% | +100% |

### Long vs Short OOS Breakdown

| Window | Long Trades | Long WR | Long PnL | Long Sharpe | Short Trades | Short WR | Short PnL | Short Sharpe |
|--------|-------------|---------|----------|-------------|--------------|----------|-----------|--------------|
| W1 | 1 | 100.0% | 3.75% | 0.00 | 5 | 0.0% | -5.95% | -100.00 |
| W2 | 6 | 33.3% | 0.95% | 1.25 | 6 | 50.0% | 14.56% | 11.68 |
| W3 | 2 | 0.0% | -3.40% | -100.00 | 2 | 0.0% | -3.36% | -100.00 |
| W4 | 1 | 0.0% | -1.51% | 0.00 | 3 | 66.7% | -1.14% | -4.62 |
| W5 | 3 | 33.3% | 1.47% | 5.38 | 2 | 0.0% | -1.35% | -100.00 |
| W6 | 2 | 0.0% | -2.94% | -64.48 | 0 | 0.0% | 0.00% | 0.00 |
| W7 | 3 | 0.0% | -1.64% | -100.00 | 5 | 0.0% | -2.61% | -66.04 |
| W8 | 8 | 25.0% | 1.19% | 2.89 | 10 | 10.0% | -4.74% | -50.22 |
| W9 | 1 | 100.0% | 1.79% | 0.00 | 3 | 0.0% | -2.82% | -100.00 |
| W10 | 0 | 0.0% | 0.00% | 0.00 | 1 | 0.0% | -2.64% | 0.00 |

## Parameter Sensitivity Analysis

Vary each parameter by +/-20% from modal values across windows. >30% Sharpe degradation = fragile.

### BTC Sensitivity

| Variation | Sharpe | PnL | Trades | Degradation |
|-----------|--------|-----|--------|-------------|
| base | 0.820 | 1.79% | 27 | +0.0% |
| bb_period_minus20 | 3.441 | 4.73% | 16 | -319.7% **FRAGILE** |
| bb_period_plus20 | -1.059 | -3.02% | 35 | +229.1% **FRAGILE** |
| bb_std_minus20 | -0.433 | -1.01% | 33 | +152.8% **FRAGILE** |
| bb_std_plus20 | -1.551 | -2.23% | 17 | +289.2% **FRAGILE** |
| max_hold_bars_minus20 | 1.791 | 4.55% | 27 | -118.4% **FRAGILE** |
| max_hold_bars_plus20 | 0.565 | 1.21% | 27 | +31.1% **FRAGILE** |
| min_squeeze_bars_minus20 | 0.128 | 0.58% | 57 | +84.4% **FRAGILE** |
| min_squeeze_bars_plus20 | -0.917 | -1.07% | 17 | +211.9% **FRAGILE** |
| squeeze_pctile_minus20 | -2.557 | -2.86% | 18 | +411.9% **FRAGILE** |
| squeeze_pctile_plus20 | 2.625 | 8.71% | 42 | -220.1% **FRAGILE** |
| stop_loss_pct_minus20 | -0.723 | -1.61% | 28 | +188.2% **FRAGILE** |
| stop_loss_pct_plus20 | 0.849 | 1.86% | 27 | -3.6% |

Fragile parameters: 11/12
**WARNING: >30% of parameter variations show fragility -- signal is parameter-sensitive**

### ETH Sensitivity

| Variation | Sharpe | PnL | Trades | Degradation |
|-----------|--------|-----|--------|-------------|
| base | -15.485 | -11.65% | 25 | +0.0% |
| bb_period_minus20 | -6.040 | -2.93% | 13 | -61.0% **FRAGILE** |
| bb_period_plus20 | -3.499 | -10.66% | 41 | -77.4% **FRAGILE** |
| bb_std_minus20 | -15.798 | -12.97% | 28 | +2.0% |
| bb_std_plus20 | -14.341 | -9.70% | 19 | -7.4% |
| max_hold_bars_minus20 | -12.642 | -10.63% | 25 | -18.4% |
| max_hold_bars_plus20 | -15.485 | -11.65% | 25 | +0.0% |
| min_squeeze_bars_minus20 | -3.628 | -10.20% | 50 | -76.6% **FRAGILE** |
| min_squeeze_bars_plus20 | -24.001 | -3.00% | 8 | +55.0% **FRAGILE** |
| squeeze_pctile_minus20 | -34.298 | -5.89% | 13 | +121.5% **FRAGILE** |
| squeeze_pctile_plus20 | -2.781 | -5.39% | 37 | -82.0% **FRAGILE** |
| stop_loss_pct_minus20 | -15.631 | -12.22% | 25 | +0.9% |
| stop_loss_pct_plus20 | -15.485 | -11.65% | 25 | +0.0% |

Fragile parameters: 6/12
**WARNING: >30% of parameter variations show fragility -- signal is parameter-sensitive**

## Kill Criteria Assessment

| Token | Direction | Criterion | Threshold | Result | Status |
|-------|-----------|-----------|-----------|--------|--------|
| BTC | Combined | Positive OOS windows | >=5/10 | 2/10 | KILL |
| BTC | Combined | Mean OOS Sharpe | >=0.3 | -17.282 | KILL |
| BTC | Combined | Total OOS trades | >=30 | 54 | PASS |
| BTC | long | long mean OOS Sharpe=-19.428 < 0.3 | - | - | KILL |
| BTC | short | Only 2/7 positive short OOS windows | - | - | KILL |
| BTC | short | short mean OOS Sharpe=-34.529 < 0.3 | - | - | KILL |
| ETH | Combined | Positive OOS windows | >=5/10 | 2/10 | KILL |
| ETH | Combined | Mean OOS Sharpe | >=0.3 | -27.771 | KILL |
| ETH | Combined | Total OOS trades | >=30 | 64 | PASS |
| ETH | long | long mean OOS Sharpe=-28.328 < 0.3 | - | - | KILL |
| ETH | short | Only 1/9 positive short OOS windows | - | - | KILL |
| ETH | short | short mean OOS Sharpe=-56.578 < 0.3 | - | - | KILL |

## Exit Reason Breakdown (All OOS Trades)

### BTC

| Exit Reason | Count | Avg PnL | Total PnL |
|-------------|-------|---------|-----------|
| stop_loss | 6 | -1.849% | -11.09% |
| max_hold | 2 | 4.446% | 8.89% |
| cross_inside | 45 | -0.330% | -14.87% |
| end_of_period | 1 | 4.192% | 4.19% |

### ETH

| Exit Reason | Count | Avg PnL | Total PnL |
|-------------|-------|---------|-----------|
| stop_loss | 12 | -1.721% | -20.66% |
| max_hold | 3 | 6.490% | 19.47% |
| cross_inside | 48 | -0.270% | -12.95% |
| end_of_period | 1 | 3.747% | 3.75% |

## Final Verdicts

### BTC: **KILL**

- KILL: Only 2/10 positive OOS windows (need >=5)
- KILL: Mean OOS Sharpe=-17.282 < 0.3

#### BTC LONG: **KILL**

- KILL: long mean OOS Sharpe=-19.428 < 0.3

#### BTC SHORT: **KILL**

- KILL: Only 2/7 positive short OOS windows
- KILL: short mean OOS Sharpe=-34.529 < 0.3

### ETH: **KILL**

- KILL: Only 2/10 positive OOS windows (need >=5)
- KILL: Mean OOS Sharpe=-27.771 < 0.3

#### ETH LONG: **KILL**

- KILL: long mean OOS Sharpe=-28.328 < 0.3

#### ETH SHORT: **KILL**

- KILL: Only 1/9 positive short OOS windows
- KILL: short mean OOS Sharpe=-56.578 < 0.3
