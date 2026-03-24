# R74: Signal D Walk-Forward Validation — Alt Tokens

**Run date**: 2026-03-24 12:28
**Signal**: D = Dual ROC 30/90 momentum (long when both ROC > 0)
**Overlays**: Positioning (Binance Top Trader L/S z-score) + VRP (IV-RV z-score)
**Rebalancing**: Weekly (Monday)
**Transaction cost**: 10 bps round-trip
**Position range**: 0 to 1.5x
**Tokens**: ETH, BNB, XRP, DOGE

## Walk-Forward Windows

| Window | IS Period | OOS Period |
|--------|-----------|------------|
| 1 | 2021-01-01 to 2022-06-30 | 2022-07-01 to 2022-12-31 |
| 2 | 2021-07-01 to 2022-12-31 | 2023-01-01 to 2023-06-30 |
| 3 | 2022-07-01 to 2023-12-31 | 2024-01-01 to 2024-06-30 |
| 4 | 2023-07-01 to 2024-12-31 | 2025-01-01 to 2025-06-30 |

## 1. OOS Sharpe Matrix (Base D)

| Token | W1 OOS | W2 OOS | W3 OOS | W4 OOS | Mean | Win Rate | Verdict |
|-------|---------|---------|---------|---------|------|----------|---------|
| ETH | 0.000 | +0.837 | +2.292 | -0.460 | 0.667 | 50% | **MARGINAL** |
| BNB | -1.118 | -0.540 | +2.536 | +1.058 | 0.484 | 50% | **MARGINAL** |
| XRP | +0.110 | -0.368 | -0.538 | +0.174 | -0.155 | 50% | **KILL** |
| DOGE | -0.849 | -1.485 | +2.053 | -1.751 | -0.508 | 25% | **KILL** |

## 2. OOS Sharpe Matrix (D + Overlays)

| Token | W1 OOS | W2 OOS | W3 OOS | W4 OOS | Mean | Win Rate |
|-------|---------|---------|---------|---------|------|----------|
| ETH | 0.000 | +0.564 | +5.017 | -0.425 | 1.289 | 50% |
| BNB | -1.140 | +0.440 | +1.129 | +1.945 | 0.593 | 75% |
| XRP | -1.243 | -0.911 | -1.247 | +0.054 | -0.837 | 25% |
| DOGE | -0.365 | -1.064 | +1.129 | -1.367 | -0.417 | 25% |

## 3. Overlay Contribution (dSharpe = Overlay - Base)

| Token | W1 | W2 | W3 | W4 | Mean | Hurts? |
|-------|------|------|------|------|------|--------|
| ETH | +0.000 | -0.274 | +2.725 | +0.035 | +0.622 | No (1/4) |
| BNB | -0.022 | +0.980 | -1.407 | +0.887 | +0.109 | No (2/4) |
| XRP | -1.354 | -0.543 | -0.710 | -0.119 | -0.681 | YES (4/4) |
| DOGE | +0.483 | +0.420 | -0.923 | +0.384 | +0.091 | No (1/4) |

## 4. Per-Token Detailed Results

### ETH

- **Positioning data**: Available
- **VRP source**: DVOL
- **Windows tested**: 4

| Window | Period | Base Sharpe | Base Ret | Base MaxDD | Overlay Sharpe | Overlay Ret | Overlay MaxDD | B&H Sharpe | dSharpe |
|--------|--------|-------------|----------|------------|----------------|-------------|---------------|------------|---------|
| W1 | 2022-07-01 to 2022-12-31 | +0.000 | +0.0% | +0.0% | +0.000 | +0.0% | +0.0% | +0.284 | +0.000 |
| W2 | 2023-01-01 to 2023-06-30 | +0.837 | +34.2% | -20.7% | +0.564 | +18.2% | -18.1% | +3.192 | -0.274 |
| W3 | 2024-01-01 to 2024-06-30 | +2.292 | +104.0% | -22.3% | +5.017 | +202.9% | -15.1% | +2.020 | +2.725 |
| W4 | 2025-01-01 to 2025-06-30 | -0.460 | -11.9% | -20.9% | -0.425 | -12.5% | -21.3% | -0.553 | +0.035 |

**Verdict**: MARGINAL: win rate 50%, mean OOS Sharpe 0.667

### BNB

- **Positioning data**: Available
- **VRP source**: proxy (90d RV x 1.2)
- **Windows tested**: 4

| Window | Period | Base Sharpe | Base Ret | Base MaxDD | Overlay Sharpe | Overlay Ret | Overlay MaxDD | B&H Sharpe | dSharpe |
|--------|--------|-------------|----------|------------|----------------|-------------|---------------|------------|---------|
| W1 | 2022-07-01 to 2022-12-31 | -1.118 | -44.4% | -29.4% | -1.140 | -26.8% | -16.7% | +0.399 | -0.022 |
| W2 | 2023-01-01 to 2023-06-30 | -0.540 | -13.5% | -13.5% | +0.440 | +9.3% | -6.5% | -0.103 | +0.980 |
| W3 | 2024-01-01 to 2024-06-30 | +2.536 | +150.3% | -20.2% | +1.129 | +39.6% | -17.0% | +4.069 | -1.407 |
| W4 | 2025-01-01 to 2025-06-30 | +1.058 | +21.9% | -10.4% | +1.945 | +46.5% | -7.3% | -0.278 | +0.887 |

**Verdict**: MARGINAL: win rate 50%, mean OOS Sharpe 0.484

### XRP

- **Positioning data**: Available
- **VRP source**: proxy (90d RV x 1.2)
- **Windows tested**: 4

| Window | Period | Base Sharpe | Base Ret | Base MaxDD | Overlay Sharpe | Overlay Ret | Overlay MaxDD | B&H Sharpe | dSharpe |
|--------|--------|-------------|----------|------------|----------------|-------------|---------------|------------|---------|
| W1 | 2022-07-01 to 2022-12-31 | +0.110 | +5.1% | -25.8% | -1.243 | -28.5% | -24.0% | +0.050 | -1.354 |
| W2 | 2023-01-01 to 2023-06-30 | -0.368 | -12.5% | -17.7% | -0.911 | -27.9% | -22.9% | +1.491 | -0.543 |
| W3 | 2024-01-01 to 2024-06-30 | -0.538 | -22.2% | -19.2% | -1.247 | -26.5% | -18.2% | -0.664 | -0.710 |
| W4 | 2025-01-01 to 2025-06-30 | +0.174 | +7.7% | -32.0% | +0.054 | +2.5% | -33.9% | +0.159 | -0.119 |

**Verdict**: KILL: mean OOS Sharpe -0.155 < 0

### DOGE

- **Positioning data**: Available
- **VRP source**: proxy (90d RV x 1.2)
- **Windows tested**: 4

| Window | Period | Base Sharpe | Base Ret | Base MaxDD | Overlay Sharpe | Overlay Ret | Overlay MaxDD | B&H Sharpe | dSharpe |
|--------|--------|-------------|----------|------------|----------------|-------------|---------------|------------|---------|
| W1 | 2022-07-01 to 2022-12-31 | -0.849 | -53.8% | -47.6% | -0.365 | -16.3% | -30.5% | +0.104 | +0.483 |
| W2 | 2023-01-01 to 2023-06-30 | -1.485 | -47.6% | -33.5% | -1.064 | -15.8% | -12.7% | -0.157 | +0.420 |
| W3 | 2024-01-01 to 2024-06-30 | +2.053 | +187.2% | -32.9% | +1.129 | +69.4% | -34.2% | +0.897 | -0.923 |
| W4 | 2025-01-01 to 2025-06-30 | -1.751 | -52.6% | -34.8% | -1.367 | -49.6% | -32.6% | -0.740 | +0.384 |

**Verdict**: KILL: win rate 25% < 50%; mean OOS Sharpe -0.508 < 0

## 5. KILL Analysis

### Criteria
- Walk-forward win rate < 50%: KILL that token
- Mean OOS Sharpe < 0: KILL that token
- Overlay consistently hurts (dSharpe < 0 in >2 windows): drop overlays

### Results

| Token | Win Rate | Mean OOS Sh | Overlay Hurts | Base Verdict | Overlay Verdict |
|-------|----------|-------------|---------------|--------------|-----------------|
| ETH | 50% (PASS) | 0.667 (PASS) | 1/4 | PASS | KEEP |
| BNB | 50% (PASS) | 0.484 (PASS) | 2/4 | PASS | KEEP |
| XRP | 50% (PASS) | -0.155 (FAIL) | 4/4 | KILL | DROP |
| DOGE | 25% (FAIL) | -0.508 (FAIL) | 1/4 | KILL | KEEP |

## 6. Final Recommendations

### MARGINAL: ETH, BNB
Not clearly profitable or not consistently winning across windows. Needs more evidence before deployment.

### KILL: XRP, DOGE
Failed walk-forward validation. Do NOT deploy Signal D on these tokens.

### Production Signal D Candidates

**No tokens pass walk-forward validation for Signal D deployment.**
