# Taker Buy/Sell Volume Signal Research Results

**Generated**: 2026-03-23 22:54
**Data**: 19 tokens, hourly, 2025-07-01 to 2026-02-28
**IS/OOS Split**: 2025-11-01 (first 4 months IS, last 4 months OOS)
**Methodology**: Spearman rank IC, daily resampled, rolling 20-day IC windows for t-stat

## Signal Variants

| # | Signal | Description |
|---|--------|-------------|
| 1 | `buy_sell_ratio` | Taker buy volume / sell volume |
| 2 | `net_taker_vol` | (buy - sell) / (buy + sell), normalized imbalance |
| 3a | `net_taker_zscore_10d` | 10-day rolling z-score of net taker volume |
| 3b | `net_taker_zscore_20d` | 20-day rolling z-score of net taker volume |
| 4a | `ratio_momentum_1d` | 1-day change in buy/sell ratio |
| 4b | `ratio_momentum_3d` | 3-day change in buy/sell ratio |
| 4c | `ratio_momentum_7d` | 7-day change in buy/sell ratio |
| 5 | `vol_weighted_pressure_norm` | Net taker vol * total volume, normalized |
| 6 | `cross_token_dispersion` | Std dev of buy/sell ratios across tokens |

## Pooled Time-Series IC

### IS

**IC Mean:**

| Signal | fwd_1d | fwd_3d | fwd_7d | fwd_14d |
|--------|--------|--------|--------|--------|
| `buy_sell_ratio` | 0.0003 | -0.0428 | 0.0162 | -0.0552 |
| `cross_token_dispersion` | -0.0875 | -0.0934 | -0.1513 | -0.0663 |
| `net_taker_vol` | 0.0026 | -0.0493 | 0.0068 | -0.0623 |
| `net_taker_zscore_10d` | -0.0068 | 0.0283 | 0.0159 | -0.0524 |
| `net_taker_zscore_20d` | -0.0038 | 0.0342 | 0.0229 | -0.0534 |
| `ratio_momentum_1d` | -0.0346 | 0.0001 | -0.0187 | -0.0151 |
| `ratio_momentum_3d` | -0.0267 | 0.0038 | 0.0063 | -0.0038 |
| `ratio_momentum_7d` | -0.0094 | 0.0110 | 0.0813 | -0.0129 |
| `vol_weighted_pressure_norm` | 0.0819 | -0.0244 | 0.1017 | 0.0453 |

**IC t-stat:**

| Signal | fwd_1d | fwd_3d | fwd_7d | fwd_14d |
|--------|--------|--------|--------|--------|
| `buy_sell_ratio` | 0.01 | -1.79 | 0.70 | -2.31 |
| `cross_token_dispersion` | -4.19 | -3.68 | -6.30 | -3.08 |
| `net_taker_vol` | 0.13 | -2.11 | 0.29 | -2.72 |
| `net_taker_zscore_10d` | -0.34 | 1.21 | 0.71 | -2.58 |
| `net_taker_zscore_20d` | -0.19 | 1.40 | 0.91 | -2.43 |
| `ratio_momentum_1d` | -1.68 | 0.00 | -1.36 | -1.22 |
| `ratio_momentum_3d` | -1.22 | 0.15 | 0.28 | -0.19 |
| `ratio_momentum_7d` | -0.47 | 0.43 | 3.08 | -0.48 |
| `vol_weighted_pressure_norm` | 4.08 | -1.29 | 4.63 | 1.90 |

**Hit Rate:**

| Signal | fwd_1d | fwd_3d | fwd_7d | fwd_14d |
|--------|--------|--------|--------|--------|
| `buy_sell_ratio` | 53.7% | 51.7% | 49.7% | 44.2% |
| `cross_token_dispersion` | 53.7% | 51.7% | 49.7% | 44.2% |
| `net_taker_vol` | 48.8% | 48.7% | 52.2% | 54.2% |
| `net_taker_zscore_10d` | 50.0% | 50.6% | 51.2% | 50.0% |
| `net_taker_zscore_20d` | 50.1% | 51.1% | 51.6% | 50.0% |
| `ratio_momentum_1d` | 48.0% | 50.6% | 50.0% | 48.4% |
| `ratio_momentum_3d` | 48.5% | 50.3% | 50.9% | 50.0% |
| `ratio_momentum_7d` | 48.6% | 50.4% | 52.0% | 50.8% |
| `vol_weighted_pressure_norm` | 51.7% | 50.9% | 54.6% | 55.2% |

### OOS

**IC Mean:**

| Signal | fwd_1d | fwd_3d | fwd_7d | fwd_14d |
|--------|--------|--------|--------|--------|
| `buy_sell_ratio` | 0.0166 | -0.0422 | -0.0504 | -0.0119 |
| `cross_token_dispersion` | -0.1382 | -0.0536 | -0.0748 | -0.1247 |
| `net_taker_vol` | 0.0250 | -0.0382 | -0.0473 | 0.0010 |
| `net_taker_zscore_10d` | -0.0009 | -0.0527 | 0.0483 | 0.0040 |
| `net_taker_zscore_20d` | -0.0059 | -0.0587 | 0.0441 | 0.0047 |
| `ratio_momentum_1d` | 0.0187 | -0.0039 | 0.0199 | 0.0266 |
| `ratio_momentum_3d` | 0.0288 | -0.0623 | 0.0018 | -0.0061 |
| `ratio_momentum_7d` | 0.0084 | -0.0140 | 0.0458 | 0.0468 |
| `vol_weighted_pressure_norm` | -0.0160 | -0.0664 | -0.1149 | -0.0752 |

**IC t-stat:**

| Signal | fwd_1d | fwd_3d | fwd_7d | fwd_14d |
|--------|--------|--------|--------|--------|
| `buy_sell_ratio` | 0.74 | -2.12 | -2.40 | -0.61 |
| `cross_token_dispersion` | -9.35 | -2.50 | -2.71 | -3.53 |
| `net_taker_vol` | 1.09 | -1.94 | -2.21 | 0.05 |
| `net_taker_zscore_10d` | -0.04 | -2.49 | 2.01 | 0.18 |
| `net_taker_zscore_20d` | -0.28 | -2.80 | 1.81 | 0.21 |
| `ratio_momentum_1d` | 0.93 | -0.23 | 1.47 | 2.21 |
| `ratio_momentum_3d` | 1.26 | -2.42 | 0.10 | -0.31 |
| `ratio_momentum_7d` | 0.40 | -0.68 | 1.79 | 2.07 |
| `vol_weighted_pressure_norm` | -0.75 | -3.62 | -5.95 | -3.19 |

**Hit Rate:**

| Signal | fwd_1d | fwd_3d | fwd_7d | fwd_14d |
|--------|--------|--------|--------|--------|
| `buy_sell_ratio` | 38.5% | 37.2% | 35.0% | 28.0% |
| `cross_token_dispersion` | 38.5% | 37.2% | 35.0% | 28.0% |
| `net_taker_vol` | 54.2% | 54.8% | 56.1% | 62.1% |
| `net_taker_zscore_10d` | 51.7% | 48.8% | 53.6% | 52.3% |
| `net_taker_zscore_20d` | 51.3% | 48.3% | 53.1% | 52.2% |
| `ratio_momentum_1d` | 51.3% | 50.2% | 51.5% | 52.1% |
| `ratio_momentum_3d` | 51.8% | 48.0% | 50.4% | 49.8% |
| `ratio_momentum_7d` | 52.1% | 50.6% | 54.1% | 50.4% |
| `vol_weighted_pressure_norm` | 54.2% | 54.3% | 56.7% | 60.6% |

## Sign Consistency (IS vs OOS)

| Signal | fwd_1d | fwd_3d | fwd_7d | fwd_14d |
|--------|--------|--------|--------|--------|
| `buy_sell_ratio` | YES | YES | NO | YES |
| `cross_token_dispersion` | YES | YES | YES | YES |
| `net_taker_vol` | YES | YES | NO | NO |
| `net_taker_zscore_10d` | YES | NO | YES | NO |
| `net_taker_zscore_20d` | YES | NO | YES | NO |
| `ratio_momentum_1d` | NO | NO | NO | NO |
| `ratio_momentum_3d` | NO | NO | YES | YES |
| `ratio_momentum_7d` | NO | NO | YES | NO |
| `vol_weighted_pressure_norm` | NO | YES | NO | NO |

## Cross-Sectional IC

### IS

**CS IC Mean:**

| Signal | fwd_1d | fwd_3d | fwd_7d | fwd_14d |
|--------|--------|--------|--------|--------|
| `buy_sell_ratio` | 0.0012 | 0.0042 | -0.0258 | -0.0029 |
| `cross_token_dispersion` | N/A | N/A | N/A | N/A |
| `net_taker_vol` | -0.0003 | -0.0023 | -0.0259 | -0.0019 |
| `net_taker_zscore_10d` | -0.0105 | 0.0058 | 0.0111 | 0.0158 |
| `net_taker_zscore_20d` | -0.0117 | 0.0124 | 0.0132 | 0.0154 |
| `ratio_momentum_1d` | -0.0088 | -0.0212 | 0.0173 | -0.0012 |
| `ratio_momentum_3d` | -0.0147 | -0.0045 | 0.0066 | -0.0038 |
| `ratio_momentum_7d` | -0.0201 | -0.0172 | 0.0103 | 0.0278 |
| `vol_weighted_pressure_norm` | 0.0226 | -0.0267 | 0.0054 | 0.0565 |

**CS IC t-stat:**

| Signal | fwd_1d | fwd_3d | fwd_7d | fwd_14d |
|--------|--------|--------|--------|--------|
| `buy_sell_ratio` | 0.05 | 0.17 | -1.07 | -0.11 |
| `cross_token_dispersion` | 0.00 | 0.00 | 0.00 | 0.00 |
| `net_taker_vol` | -0.01 | -0.09 | -1.05 | -0.08 |
| `net_taker_zscore_10d` | -0.44 | 0.26 | 0.53 | 0.76 |
| `net_taker_zscore_20d` | -0.48 | 0.55 | 0.60 | 0.72 |
| `ratio_momentum_1d` | -0.36 | -0.95 | 0.75 | -0.06 |
| `ratio_momentum_3d` | -0.77 | -0.22 | 0.33 | -0.18 |
| `ratio_momentum_7d` | -0.88 | -0.79 | 0.49 | 1.27 |
| `vol_weighted_pressure_norm` | 0.95 | -1.10 | 0.22 | 2.18 |

### OOS

**CS IC Mean:**

| Signal | fwd_1d | fwd_3d | fwd_7d | fwd_14d |
|--------|--------|--------|--------|--------|
| `buy_sell_ratio` | -0.0263 | -0.0788 | -0.0518 | -0.0506 |
| `cross_token_dispersion` | N/A | N/A | N/A | N/A |
| `net_taker_vol` | -0.0317 | -0.0765 | -0.0449 | -0.0368 |
| `net_taker_zscore_10d` | 0.0071 | 0.0096 | 0.0373 | 0.0309 |
| `net_taker_zscore_20d` | 0.0015 | 0.0083 | 0.0336 | 0.0328 |
| `ratio_momentum_1d` | 0.0023 | -0.0167 | -0.0091 | -0.0038 |
| `ratio_momentum_3d` | -0.0004 | -0.0286 | -0.0088 | 0.0183 |
| `ratio_momentum_7d` | 0.0084 | -0.0002 | 0.0175 | 0.0243 |
| `vol_weighted_pressure_norm` | -0.0258 | -0.0355 | 0.0226 | 0.0243 |

**CS IC t-stat:**

| Signal | fwd_1d | fwd_3d | fwd_7d | fwd_14d |
|--------|--------|--------|--------|--------|
| `buy_sell_ratio` | -1.11 | -3.48 | -2.07 | -1.98 |
| `cross_token_dispersion` | 0.00 | 0.00 | 0.00 | 0.00 |
| `net_taker_vol` | -1.34 | -3.39 | -1.74 | -1.43 |
| `net_taker_zscore_10d` | 0.31 | 0.39 | 1.48 | 1.31 |
| `net_taker_zscore_20d` | 0.07 | 0.33 | 1.34 | 1.40 |
| `ratio_momentum_1d` | 0.11 | -0.69 | -0.38 | -0.17 |
| `ratio_momentum_3d` | -0.02 | -1.23 | -0.38 | 0.85 |
| `ratio_momentum_7d` | 0.38 | -0.01 | 0.73 | 1.01 |
| `vol_weighted_pressure_norm` | -1.16 | -1.34 | 0.95 | 0.97 |

## Per-Token IC Breakdown (net_taker_vol, fwd_3d)

| Token | IC (full) | IC (mean) | t-stat | Hit Rate | N obs |
|-------|-----------|-----------|--------|----------|-------|
| XRPUSDT | 0.0431 | 0.0346 | 0.45 | 51.2% | 240 |
| LTCUSDT | 0.0401 | 0.0090 | 0.14 | 58.3% | 240 |
| BTCUSDT | 0.0245 | -0.0124 | -0.14 | 48.8% | 240 |
| AVAXUSDT | 0.0130 | 0.0380 | 0.55 | 50.4% | 240 |
| SOLUSDT | 0.0026 | -0.0618 | -0.94 | 50.0% | 240 |
| NEARUSDT | -0.0005 | 0.0134 | 0.24 | 55.0% | 240 |
| ADAUSDT | -0.0101 | -0.0030 | -0.04 | 52.9% | 240 |
| SUIUSDT | -0.0154 | 0.0234 | 0.26 | 57.1% | 240 |
| ARBUSDT | -0.0172 | -0.0103 | -0.15 | 52.9% | 240 |
| DOGEUSDT | -0.0193 | -0.0540 | -0.98 | 55.0% | 240 |
| ETHUSDT | -0.0283 | 0.0182 | 0.21 | 52.1% | 240 |
| AAVEUSDT | -0.0350 | -0.0096 | -0.13 | 52.1% | 240 |
| BNBUSDT | -0.0459 | -0.1521 | -2.25 | 48.3% | 240 |
| WIFUSDT | -0.0722 | -0.1435 | -1.96 | 50.4% | 240 |
| UNIUSDT | -0.0752 | -0.1164 | -1.15 | 53.3% | 240 |
| INJUSDT | -0.0791 | -0.0475 | -0.59 | 51.7% | 240 |
| LINKUSDT | -0.0871 | -0.0485 | -0.83 | 50.4% | 240 |
| DOTUSDT | -0.1329 | -0.1426 | -1.83 | 48.3% | 240 |
| OPUSDT | -0.1578 | -0.0817 | -1.03 | 43.3% | 240 |

## Trend Overlay Analysis

Strategy: 7-day trailing momentum (long/short). Overlay: only trade when taker signal confirms direction.

| Signal | Sample | Trend Sharpe | Overlay Sharpe | Confirm % | Trend HR | Overlay HR |
|--------|--------|-------------|----------------|-----------|----------|------------|
| `net_taker_vol` | IS | 0.427 | 0.599 | 52.7% | 51.7% | 50.7% |
| `net_taker_vol` | OOS | 0.372 | 1.043 | 60.1% | 52.0% | 55.3% |
| `net_taker_zscore_10d` | IS | 0.728 | 0.424 | 52.8% | 51.9% | 52.0% |
| `net_taker_zscore_10d` | OOS | 0.372 | 0.067 | 52.0% | 52.0% | 53.7% |
| `vol_weighted_pressure_norm` | IS | 0.728 | 1.458 | 56.2% | 51.9% | 53.3% |
| `vol_weighted_pressure_norm` | OOS | 0.372 | 0.755 | 62.4% | 52.0% | 55.1% |

## Top OOS Signals (by |IC|)

| Signal | Horizon | IC Mean | t-stat | Hit Rate |
|--------|---------|---------|--------|----------|
| `cross_token_dispersion` | fwd_1d | -0.1382 | -9.35 | 38.5% |
| `cross_token_dispersion` | fwd_14d | -0.1247 | -3.53 | 28.0% |
| `vol_weighted_pressure_norm` | fwd_7d | -0.1149 | -5.95 | 56.7% |
| `vol_weighted_pressure_norm` | fwd_14d | -0.0752 | -3.19 | 60.6% |
| `cross_token_dispersion` | fwd_7d | -0.0748 | -2.71 | 35.0% |
| `vol_weighted_pressure_norm` | fwd_3d | -0.0664 | -3.62 | 54.3% |
| `ratio_momentum_3d` | fwd_3d | -0.0623 | -2.42 | 48.0% |
| `net_taker_zscore_20d` | fwd_3d | -0.0587 | -2.80 | 48.3% |
| `cross_token_dispersion` | fwd_3d | -0.0536 | -2.50 | 37.2% |
| `net_taker_zscore_10d` | fwd_3d | -0.0527 | -2.49 | 48.8% |

## Robust Signals (|t-stat| > 1.5 in both IS and OOS)

| Signal | Horizon | IS IC | IS t | OOS IC | OOS t |
|--------|---------|-------|------|--------|-------|
| `cross_token_dispersion` | fwd_1d | -0.0875 | -4.19 | -0.1382 | -9.35 |
| `cross_token_dispersion` | fwd_14d | -0.0663 | -3.08 | -0.1247 | -3.53 |
| `vol_weighted_pressure_norm` | fwd_7d | 0.1017 | 4.63 | -0.1149 | -5.95 |
| `vol_weighted_pressure_norm` | fwd_14d | 0.0453 | 1.90 | -0.0752 | -3.19 |
| `cross_token_dispersion` | fwd_7d | -0.1513 | -6.30 | -0.0748 | -2.71 |
| `cross_token_dispersion` | fwd_3d | -0.0934 | -3.68 | -0.0536 | -2.50 |
| `ratio_momentum_7d` | fwd_7d | 0.0813 | 3.08 | 0.0458 | 1.79 |
| `buy_sell_ratio` | fwd_3d | -0.0428 | -1.79 | -0.0422 | -2.12 |
| `net_taker_vol` | fwd_3d | -0.0493 | -2.11 | -0.0382 | -1.94 |

## Interpretation Notes

- **Positive IC**: Higher taker buy pressure predicts higher future returns (momentum/continuation)
- **Negative IC**: Higher taker buy pressure predicts lower future returns (contrarian/reversal)
- **Sign consistency**: If IS and OOS have the same IC sign, the signal direction is stable
- **t-stat > 2**: Statistically significant at ~95% confidence
- **Hit rate**: Fraction of observations where signal direction matches return direction (50% = random)
- **Cross-sectional IC**: Measures whether ranking tokens by signal predicts relative performance
- **Trend overlay**: Tests whether filtering trend signals by taker confirmation improves Sharpe ratio
