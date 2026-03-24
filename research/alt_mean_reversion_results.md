# Alt-Coin Mean-Reversion Strategy Test Results

Run: 2026-03-24 12:44

Tokens: ETH, SOL, BNB, XRP, DOGE, ADA, LINK, DOT
Cost: 10 bps per trade | Stop Loss: 5% | Max Hold: 168 bars (1 week)
Split: 70% IS / 30% OOS | Warmup: 1440 bars (60d)

## Per-Token OOS Metrics by Variant

### Bollinger Band MR (Long Only)

IS Period: 2020-03-01 to 2024-05-17  |  OOS Period: 2024-05-17 to 2026-03-14

| Token | Sharpe | Win Rate | MaxDD | Trades | Avg Trade | Total Ret | Stop Rate | Verdict |
|-------|--------|----------|-------|--------|-----------|-----------|-----------|---------|
| ETH | -1.445 | 32.4% | -56.2% | 68 | -0.0084 | -50.78% | 63.2% | KILL |
| SOL | -0.127 | 27.9% | -44.1% | 61 | -0.0010 | -24.90% | 70.5% | KILL |
| BNB | -0.250 | 37.8% | -41.1% | 45 | -0.0016 | -16.75% | 62.2% | KILL |
| XRP | +0.934 | 36.4% | -41.4% | 66 | +0.0075 | +25.92% | 62.1% | KILL |
| DOGE | -2.216 | 25.7% | -69.2% | 74 | -0.0138 | -70.32% | 74.3% | KILL |
| ADA | -1.387 | 26.3% | -64.4% | 76 | -0.0100 | -63.85% | 73.7% | KILL |
| LINK | -0.130 | 26.1% | -45.9% | 69 | -0.0010 | -28.37% | 72.5% | KILL |
| DOT | -1.837 | 24.3% | -62.6% | 70 | -0.0119 | -64.37% | 74.3% | KILL |

### Bollinger Band MR (Long+Short)

IS Period: 2020-03-01 to 2024-05-17  |  OOS Period: 2024-05-17 to 2026-03-14

| Token | Sharpe | Win Rate | MaxDD | Trades | Avg Trade | Total Ret | Stop Rate | Verdict |
|-------|--------|----------|-------|--------|-----------|-----------|-----------|---------|
| ETH | -1.821 | 33.0% | -76.6% | 112 | -0.0100 | -73.68% | 62.5% | KILL |
| SOL | -0.826 | 28.2% | -62.3% | 103 | -0.0058 | -59.96% | 69.9% | KILL |
| BNB | +0.364 | 43.0% | -44.7% | 86 | +0.0024 | +0.37% | 55.8% | KILL |
| XRP | -1.359 | 27.1% | -83.3% | 133 | -0.0087 | -79.02% | 70.7% | KILL |
| DOGE | -1.769 | 27.4% | -84.2% | 135 | -0.0107 | -83.43% | 72.6% | KILL |
| ADA | -1.581 | 26.3% | -86.7% | 137 | -0.0104 | -84.32% | 73.0% | KILL |
| LINK | -1.084 | 27.9% | -77.1% | 129 | -0.0073 | -73.31% | 71.3% | KILL |
| DOT | -1.573 | 27.1% | -78.8% | 118 | -0.0097 | -76.99% | 72.0% | KILL |

### RSI MR (Long Only)

IS Period: 2020-03-01 to 2024-05-17  |  OOS Period: 2024-05-17 to 2026-03-14

| Token | Sharpe | Win Rate | MaxDD | Trades | Avg Trade | Total Ret | Stop Rate | Verdict |
|-------|--------|----------|-------|--------|-----------|-----------|-----------|---------|
| ETH | +0.000 | 0.0% | 0.0% | 0 | +0.0000 | +0.00% | 0.0% | KILL |
| SOL | +0.000 | 0.0% | 0.0% | 0 | +0.0000 | +0.00% | 0.0% | KILL |
| BNB | +0.000 | 0.0% | 0.0% | 0 | +0.0000 | +0.00% | 0.0% | KILL |
| XRP | +0.000 | 0.0% | 0.0% | 0 | +0.0000 | +0.00% | 0.0% | KILL |
| DOGE | +0.000 | 0.0% | 0.0% | 0 | +0.0000 | +0.00% | 0.0% | KILL |
| ADA | +0.000 | 0.0% | 0.0% | 0 | +0.0000 | +0.00% | 0.0% | KILL |
| LINK | +0.000 | 0.0% | 0.0% | 0 | +0.0000 | +0.00% | 0.0% | KILL |
| DOT | +0.000 | 0.0% | 0.0% | 0 | +0.0000 | +0.00% | 0.0% | KILL |

### RSI MR (Long+Short)

IS Period: 2020-03-01 to 2024-05-17  |  OOS Period: 2024-05-17 to 2026-03-14

| Token | Sharpe | Win Rate | MaxDD | Trades | Avg Trade | Total Ret | Stop Rate | Verdict |
|-------|--------|----------|-------|--------|-----------|-----------|-----------|---------|
| ETH | +0.000 | 0.0% | 0.0% | 0 | +0.0000 | +0.00% | 0.0% | KILL |
| SOL | +0.000 | 0.0% | 0.0% | 0 | +0.0000 | +0.00% | 0.0% | KILL |
| BNB | +0.000 | 0.0% | 0.0% | 0 | +0.0000 | +0.00% | 0.0% | KILL |
| XRP | +0.000 | 0.0% | 0.0% | 0 | +0.0000 | +0.00% | 0.0% | KILL |
| DOGE | +0.000 | 100.0% | 0.0% | 1 | +0.1093 | +10.93% | 0.0% | KILL |
| ADA | +0.000 | 0.0% | 0.0% | 1 | -0.0520 | -5.20% | 100.0% | KILL |
| LINK | +0.000 | 0.0% | 0.0% | 0 | +0.0000 | +0.00% | 0.0% | KILL |
| DOT | +0.000 | 0.0% | 0.0% | 0 | +0.0000 | +0.00% | 0.0% | KILL |

### Z-Score MR (Long Only)

IS Period: 2020-03-01 to 2024-05-17  |  OOS Period: 2024-05-17 to 2026-03-14

| Token | Sharpe | Win Rate | MaxDD | Trades | Avg Trade | Total Ret | Stop Rate | Verdict |
|-------|--------|----------|-------|--------|-----------|-----------|-----------|---------|
| ETH | -2.024 | 31.5% | -61.3% | 54 | -0.0115 | -51.95% | 68.5% | KILL |
| SOL | -0.272 | 26.1% | -56.9% | 46 | -0.0024 | -27.12% | 71.7% | KILL |
| BNB | +0.245 | 39.4% | -38.2% | 33 | +0.0017 | -2.85% | 57.6% | KILL |
| XRP | +2.108 | 40.9% | -37.3% | 44 | +0.0187 | +86.00% | 59.1% | KILL |
| DOGE | -1.048 | 30.2% | -47.1% | 53 | -0.0071 | -41.09% | 69.8% | KILL |
| ADA | +0.175 | 34.0% | -49.5% | 53 | +0.0015 | -13.33% | 66.0% | KILL |
| LINK | -0.798 | 27.3% | -49.8% | 55 | -0.0056 | -38.29% | 72.7% | KILL |
| DOT | -2.538 | 24.5% | -61.9% | 53 | -0.0154 | -60.92% | 71.7% | KILL |

### Z-Score MR (Long+Short)

IS Period: 2020-03-01 to 2024-05-17  |  OOS Period: 2024-05-17 to 2026-03-14

| Token | Sharpe | Win Rate | MaxDD | Trades | Avg Trade | Total Ret | Stop Rate | Verdict |
|-------|--------|----------|-------|--------|-----------|-----------|-----------|---------|
| ETH | -2.287 | 30.2% | -70.4% | 86 | -0.0126 | -71.28% | 66.3% | KILL |
| SOL | -0.645 | 29.1% | -68.1% | 86 | -0.0052 | -52.19% | 68.6% | KILL |
| BNB | +0.072 | 40.9% | -49.1% | 66 | +0.0005 | -11.43% | 56.1% | KILL |
| XRP | -1.213 | 26.1% | -84.5% | 111 | -0.0078 | -70.22% | 73.0% | KILL |
| DOGE | -1.873 | 27.4% | -79.7% | 106 | -0.0114 | -77.52% | 72.6% | KILL |
| ADA | -0.462 | 29.4% | -67.4% | 102 | -0.0035 | -52.00% | 70.6% | KILL |
| LINK | -1.757 | 24.0% | -81.3% | 104 | -0.0116 | -77.99% | 76.0% | KILL |
| DOT | -2.119 | 23.3% | -74.4% | 86 | -0.0137 | -75.61% | 74.4% | KILL |

### VWAP MR (Long Only)

IS Period: 2020-03-01 to 2024-05-17  |  OOS Period: 2024-05-17 to 2026-03-14

| Token | Sharpe | Win Rate | MaxDD | Trades | Avg Trade | Total Ret | Stop Rate | Verdict |
|-------|--------|----------|-------|--------|-----------|-----------|-----------|---------|
| ETH | -1.514 | 43.9% | -72.5% | 139 | -0.0057 | -63.40% | 55.4% | KILL |
| SOL | -0.623 | 44.7% | -62.7% | 170 | -0.0023 | -49.86% | 55.3% | KILL |
| BNB | -0.919 | 48.5% | -49.9% | 97 | -0.0035 | -38.08% | 49.5% | KILL |
| XRP | -1.396 | 39.9% | -74.5% | 153 | -0.0057 | -68.56% | 57.5% | KILL |
| DOGE | -1.199 | 41.0% | -78.3% | 205 | -0.0048 | -74.94% | 58.5% | KILL |
| ADA | -0.703 | 42.5% | -72.9% | 207 | -0.0027 | -61.30% | 57.0% | KILL |
| LINK | -1.177 | 39.9% | -79.5% | 188 | -0.0048 | -72.56% | 60.1% | KILL |
| DOT | -2.900 | 38.4% | -89.4% | 177 | -0.0109 | -89.17% | 61.6% | KILL |

### VWAP MR (Long+Short)

IS Period: 2020-03-01 to 2024-05-17  |  OOS Period: 2024-05-17 to 2026-03-14

| Token | Sharpe | Win Rate | MaxDD | Trades | Avg Trade | Total Ret | Stop Rate | Verdict |
|-------|--------|----------|-------|--------|-----------|-----------|-----------|---------|
| ETH | -1.189 | 48.8% | -82.1% | 252 | -0.0043 | -76.00% | 50.4% | KILL |
| SOL | -0.628 | 47.6% | -80.5% | 313 | -0.0022 | -69.66% | 52.4% | KILL |
| BNB | -1.456 | 50.0% | -71.9% | 182 | -0.0055 | -70.48% | 47.8% | KILL |
| XRP | -1.735 | 42.0% | -95.6% | 317 | -0.0064 | -92.31% | 56.8% | KILL |
| DOGE | -1.161 | 42.5% | -93.0% | 386 | -0.0042 | -90.29% | 57.0% | KILL |
| ADA | -0.969 | 44.3% | -90.7% | 377 | -0.0034 | -85.80% | 55.4% | KILL |
| LINK | -0.913 | 44.8% | -89.2% | 355 | -0.0034 | -84.03% | 55.2% | KILL |
| DOT | -2.281 | 41.6% | -95.5% | 308 | -0.0083 | -95.17% | 58.1% | KILL |

## Variant Summary (OOS Aggregates)

| Variant | Mean Sharpe | Pos. Sharpe Tokens | Mean Win Rate | Mean MaxDD | Min Trades | BTC Corr | Verdict |
|---------|-------------|-------------------|---------------|------------|------------|----------|---------|
| Bollinger Band MR (Long Only) | -0.807 | 1/8 | 29.6% | -53.1% | 45 | +0.375 | **KILL** |
| Bollinger Band MR (Long+Short) | -1.206 | 1/8 | 30.0% | -74.2% | 86 | -0.134 | **KILL** |
| RSI MR (Long Only) | +0.000 | 0/8 | 0.0% | 0.0% | 0 | N/A | **KILL** |
| RSI MR (Long+Short) | +0.000 | 0/8 | 12.5% | 0.0% | 0 | N/A | **KILL** |
| Z-Score MR (Long Only) | -0.519 | 3/8 | 31.7% | -50.3% | 33 | +0.428 | **KILL** |
| Z-Score MR (Long+Short) | -1.286 | 1/8 | 28.8% | -71.8% | 66 | -0.108 | **KILL** |
| VWAP MR (Long Only) | -1.304 | 0/8 | 42.3% | -72.5% | 97 | +0.545 | **KILL** |
| VWAP MR (Long+Short) | -1.291 | 0/8 | 45.2% | -87.3% | 182 | -0.062 | **KILL** |

## Kill Criteria Detail

| Criterion | Threshold | Purpose |
|-----------|-----------|---------|
| Mean Sharpe | >= 0.2 | Minimum risk-adjusted return |
| Win Rate | >= 45% | Minimum win frequency |
| Min Trades/Token | >= 50 | Statistical significance |
| Mean MaxDD | > -25% | Risk management |

**Bollinger Band MR (Long Only)** -- KILL reasons:
  - Mean Sharpe -0.807 < 0.2
  - Mean Win Rate 29.6% < 45%
  - Min trades 45 < 50
  - Mean MaxDD -53.1% > 25%

**Bollinger Band MR (Long+Short)** -- KILL reasons:
  - Mean Sharpe -1.206 < 0.2
  - Mean Win Rate 30.0% < 45%
  - Mean MaxDD -74.2% > 25%

**RSI MR (Long Only)** -- KILL reasons:
  - Mean Sharpe 0.000 < 0.2
  - Mean Win Rate 0.0% < 45%
  - Min trades 0 < 50

**RSI MR (Long+Short)** -- KILL reasons:
  - Mean Sharpe 0.000 < 0.2
  - Mean Win Rate 12.5% < 45%
  - Min trades 0 < 50

**Z-Score MR (Long Only)** -- KILL reasons:
  - Mean Sharpe -0.519 < 0.2
  - Mean Win Rate 31.7% < 45%
  - Min trades 33 < 50
  - Mean MaxDD -50.3% > 25%

**Z-Score MR (Long+Short)** -- KILL reasons:
  - Mean Sharpe -1.286 < 0.2
  - Mean Win Rate 28.8% < 45%
  - Mean MaxDD -71.8% > 25%

**VWAP MR (Long Only)** -- KILL reasons:
  - Mean Sharpe -1.304 < 0.2
  - Mean Win Rate 42.3% < 45%
  - Mean MaxDD -72.5% > 25%

**VWAP MR (Long+Short)** -- KILL reasons:
  - Mean Sharpe -1.291 < 0.2
  - Mean MaxDD -87.3% > 25%

## Best Variant

**Z-Score MR (Long Only)** with Mean OOS Sharpe -0.519
- 3/8 tokens with positive Sharpe
- Mean Win Rate: 31.7%
- Mean MaxDD: -50.3%
- BTC Correlation: +0.428
- Verdict: **KILL**

## IS vs OOS Comparison (Sharpe)

| Variant | IS Mean Sharpe | OOS Mean Sharpe | Degradation |
|---------|---------------|-----------------|-------------|
| Bollinger Band MR (Long Only) | +0.218 | -0.807 | -1.025 |
| Bollinger Band MR (Long+Short) | -1.490 | -1.206 | +0.284 |
| RSI MR (Long Only) | +0.000 | +0.000 | +0.000 |
| RSI MR (Long+Short) | +0.310 | +0.000 | -0.310 |
| Z-Score MR (Long Only) | +0.222 | -0.519 | -0.741 |
| Z-Score MR (Long+Short) | -1.925 | -1.286 | +0.640 |
| VWAP MR (Long Only) | -0.182 | -1.304 | -1.122 |
| VWAP MR (Long+Short) | -1.128 | -1.291 | -0.163 |

## Final Verdict

**ALL variants KILLED.** Mean-reversion on altcoins does not meet minimum thresholds.

Recommendation: Investigate alternative approaches:
- Regime-conditioned MR (only trade in ranging regimes)
- Pairs/spread MR (relative value rather than absolute)
- Shorter timeframe MR (5m/15m bars)
- Adaptive parameters (vary lookback with realized vol)