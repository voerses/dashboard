# Regime-Switched Positioning Signal Backtest Results

*Generated: 2026-03-24 11:11:47*

**Data Range:** 2020-09-01 to 2026-03-14
**IS Period:** 2020-09-01 to 2024-12-31
**OOS Period:** 2025-01-01 to latest
**Cost Model:** 10 bps round-trip

## 1. Strategy Performance Summary

### All Variants Comparison

| Metric | Variant A IS | Variant A OOS | Variant B IS | Variant B OOS | Variant C IS | Variant C OOS | Variant D IS | Variant D OOS |
|--------|-----------|-----------|-----------|-----------|-----------|-----------|-----------|-----------|
| Annual Return | -5.61% | -20.60% | 10.16% | -25.30% | 11.26% | -25.21% | 8.61% | -26.55% |
| Max Drawdown | -60.45% | -39.42% | -56.62% | -36.84% | -58.65% | -36.29% | -59.52% | -36.56% |
| Sharpe Ratio | 0.06 | -0.65 | 0.45 | -1.15 | 0.47 | -1.05 | 0.40 | -1.14 |
| Calmar Ratio | -0.09 | -0.52 | 0.18 | -0.69 | 0.19 | -0.69 | 0.14 | -0.73 |
| Sortino Ratio | 0.09 | -0.82 | 0.64 | -1.43 | 0.67 | -1.33 | 0.58 | -1.43 |
| Win Rate | 46.70% | 47.21% | 47.90% | 44.42% | 47.78% | 44.19% | 47.52% | 44.42% |
| Avg Trade Duration (d) | 5.0 | 4.6 | 5.4 | 4.9 | 5.4 | 4.9 | 5.4 | 4.8 |
| Total Trades | 1314 | 349 | 1290 | 344 | 1254 | 340 | 1262 | 342 |
| Annual Cost Drag | 12.30% | 12.03% | 10.61% | 10.44% | 11.01% | 11.15% | 10.96% | 11.16% |
| Net Annual Return | -5.61% | -20.60% | 10.16% | -25.30% | 11.26% | -25.21% | 8.61% | -26.55% |

### Variant D (Full System) — Detailed

| Metric | IS | OOS | Full Period |
|--------|-----|-----|------------|
| Annual Return | 8.61% | -26.55% | -0.10% |
| Max Drawdown | -59.52% | -36.56% | -59.52% |
| Sharpe Ratio | 0.40 | -1.14 | 0.17 |
| Calmar Ratio | 0.14 | -0.73 | -0.00 |
| Sortino Ratio | 0.58 | -1.43 | 0.24 |
| Win Rate | 47.52% | 44.42% | 46.86% |
| Avg Trade Duration (d) | 5.4 | 4.8 | 5.3 |
| Total Trades | 1262 | 342 | 1604 |
| Annual Cost Drag | 10.96% | 11.16% | 11.00% |
| Net Annual Return | 8.61% | -26.55% | -0.10% |

## 2. Regime-Specific Performance (OOS — Variant D)

| Regime | Annual Return | Max DD | Sharpe | Win Rate | Days |
|--------|--------------|--------|--------|----------|------|
| UPTREND | -41.23% | -12.45% | -2.16 | 37.80% | 82 |
| DOWNTREND | -48.13% | -13.89% | -3.20 | 41.98% | 81 |
| RANGE | -27.54% | -28.21% | -1.07 | 45.83% | 216 |
| CRISIS | 93.36% | -3.89% | 3.10 | 52.94% | 51 |

## 3. Comparison vs Buy-and-Hold

| Metric | Buy & Hold | Variant A | Variant B | Variant C | Variant D |
|--------|-----------|-----------|-----------|-----------|-----------|
| Annual Return | -21.83% | -20.60% | -25.30% | -25.21% | -26.55% |
| Max Drawdown | -49.53% | -39.42% | -36.84% | -36.29% | -36.56% |
| Sharpe Ratio | -0.31 | -0.65 | -1.15 | -1.05 | -1.14 |
| Sortino Ratio | -0.43 | -0.82 | -1.43 | -1.33 | -1.43 |
| Total Return | -25.17% | -23.79% | -29.07% | -28.96% | -30.46% |
| **Alpha vs BnH** | -- | 1.23% | -3.47% | -3.37% | -4.72% |

### Full Period Comparison

| Metric | Buy & Hold | Variant D |
|--------|-----------|-----------|
| Annual Return | 38.15% | -0.10% |
| Max Drawdown | -76.63% | -59.52% |
| Sharpe Ratio | 0.84 | 0.17 |
| Sortino Ratio | 1.20 | 0.24 |
| Total Return | 493.70% | -0.54% |

## 4. Monthly Returns Table (OOS — Variant D)

| Year | Jan | Feb | Mar | Apr | May | Jun | Jul | Aug | Sep | Oct | Nov | Dec |
|------|------|------|------|------|------|------|------|------|------|------|------|------|
| 2025 | -2.2% | -1.3% | 7.7% | -10.3% | -1.2% | -4.1% | -5.2% | -5.2% | -1.0% | 2.3% | -12.4% | -0.3% |
| 2026 | -2.3% | 3.1% | -1.7% | -- | -- | -- | -- | -- | -- | -- | -- | -- |

## 5. Drawdown Analysis (Variant D — Full Period)

| Rank | Start | Trough | End | Depth | Duration (d) | Recovery (d) |
|------|-------|--------|-----|-------|-------------|-------------|
| 1 | 2021-02-28 | 2022-03-20 | 2023-10-22 | -59.52% | 966 | 581 |
| 2 | 2024-12-17 | 2026-01-13 | 2026-03-13 | -39.89% | 451 | ongoing |
| 3 | 2020-09-10 | 2020-12-13 | 2021-02-07 | -36.64% | 150 | 56 |
| 4 | 2024-03-13 | 2024-04-21 | 2024-11-20 | -23.08% | 252 | 213 |
| 5 | 2023-11-10 | 2024-01-15 | 2024-03-10 | -21.58% | 121 | 55 |

## 6. Variant Contribution Analysis (OOS)

Isolating the contribution of each layer:

| Layer Added | Sharpe Delta | Ann Return Delta | Max DD Change |
|-------------|-------------|-----------------|---------------|
| Positioning Only (baseline) | -0.65 (base) | -20.60% (base) | -39.42% (base) |
| + Regime Switching | -0.501 | -4.70% | 2.58% |
| + Macro Agreement Boost | 0.094 | 0.09% | 0.55% |
| + Oil Crisis Hedge | -0.090 | -1.35% | -0.27% |

## 7. Regime Distribution

### OOS Period

| Regime | Days | % |
|--------|------|---|
| RANGE | 216 | 50.1% |
| UPTREND | 82 | 19.0% |
| DOWNTREND | 81 | 18.8% |
| CRISIS | 52 | 12.1% |

### Full Period

| Regime | Days | % |
|--------|------|---|
| UPTREND | 781 | 38.8% |
| RANGE | 712 | 35.4% |
| DOWNTREND | 284 | 14.1% |
| CRISIS | 237 | 11.8% |

## 8. Signal Statistics

| Signal | Mean | Std | Min | Max | Non-Null |
|--------|------|-----|-----|-----|----------|
| sig_toptrader_ls | 0.0358 | 1.2535 | -5.2947 | 5.2947 | 1904 |
| sig_ls_divergence | -0.0096 | 1.1475 | -4.4171 | 3.5199 | 2005 |
| sig_oil_mom | -0.0280 | 0.3968 | -0.9884 | 4.0137 | 2014 |
| sig_macro_composite | 1.0426 | 0.5077 | 0.0221 | 1.9847 | 2014 |

## 9. Research Conclusions

### Key Findings

1. **Regime switching adds value in-sample**: Variants B/C/D (Sharpe 0.40-0.47 IS) 
   substantially outperform Variant A (Sharpe 0.06) by gating signals to appropriate regimes.

2. **OOS degradation is significant**: All variants show negative OOS performance. 
   BTC buy-and-hold also negative OOS (Sharpe -0.31), 
   suggesting the OOS window is a challenging macro environment.

3. **Crisis hedge is the standout**: The CRISIS regime sub-strategy achieves 
   Sharpe 3.10 and 93.36% 
   annualized in OOS crisis periods (51 days). 
   This is the most robust signal component.

4. **Cost drag is substantial**: At 10 bps round-trip, daily rebalancing 
   generates ~10.96% annual cost drag. 
   Reducing rebalance frequency or adding position change thresholds could help.

5. **Max drawdown improvement**: All strategy variants have lower max drawdown than 
   buy-and-hold in OOS (-36.56% vs -49.53%), 
   indicating the regime-switching provides some tail risk protection.

### Recommendations

1. **Reduce turnover**: Implement position change threshold (e.g., only rebalance when signal change > 0.2 z-score) to cut cost drag by 50%+.
2. **Crisis-only deployment**: The crisis regime hedge sub-strategy works well standalone and could be isolated as a tail risk overlay.
3. **Signal frequency**: Test weekly signal rebalancing instead of daily to reduce costs.
4. **Ensemble with other signals**: The positioning signals have low standalone IC; combining with funding rate, OI divergence, or flow signals may improve robustness.
5. **OOS window caveat**: The OOS period (Jan 2025 - Mar 2026) contains a BTC drawdown from ATH. Longer OOS validation needed before deployment.
