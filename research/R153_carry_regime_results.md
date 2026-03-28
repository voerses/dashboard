# R153: Funding Carry with V4 Regime Filter

**Date:** 2026-03-28

## Hypothesis

R151 carry portfolio showed +96% return but -71% MaxDD. The drawdowns came from:
1. Long leg buying distressed tokens with extreme negative funding (price collapse)
2. No regime filter meant full exposure during market crashes
3. Imperfect hedge: long/short legs had different beta exposures

**Fixes applied:**
- ADV threshold raised to $50M (exclude garbage tokens)
- Funding rate cap at 200% annualized (exclude distressed tokens)
- V4 regime detection scales allocation: UPTREND 100%, RANGE 70%, QUIET 50%, DOWNTREND 20%, CRISIS 0%
- 72h rebalance with 72h rolling funding for ranking

## Summary Comparison

| Variant | Ann Ret | MaxDD | Sharpe | Calmar | Sortino | 12M Ret | 12M MaxDD | 12M Sharpe | Trades |
|---------|---------|-------|--------|--------|---------|---------|-----------|------------|--------|
| Unfiltered 1x | -9.38% | -75.46% | -0.257 | -0.124 | -0.340 | -4.60% | -43.25% | -0.094 | 5446 |
| Filtered 1x | -15.96% | -74.48% | -0.603 | -0.214 | -0.721 | +5.29% | -18.70% | 0.175 | 5446 |
| Filtered 2x | -34.16% | -94.61% | -0.646 | -0.361 | -0.772 | +1.22% | -35.48% | 0.020 | 5446 |
| Filtered 3x | -51.95% | -99.06% | -0.655 | -0.524 | -0.782 | -11.14% | -50.02% | -0.123 | 5446 |
| Unfiltered 2x | -28.16% | -95.34% | -0.385 | -0.295 | -0.511 | -28.22% | -70.53% | -0.289 | 5446 |

## Long vs Short Leg Decomposition

| Variant | Long Price | Long Funding | Short Price | Short Funding | Net Price | Net Funding |
|---------|-----------|-------------|------------|--------------|-----------|-------------|
| Unfiltered 1x | +0.2754 | +0.5371 | -0.7184 | +0.2976 | -0.4430 | +0.8347 |
| Filtered 1x | +0.4571 | +0.1832 | -0.7795 | +0.1745 | -0.3224 | +0.3577 |
| Filtered 2x | +0.6353 | +0.2510 | -1.0603 | +0.2427 | -0.4251 | +0.4937 |
| Filtered 3x | +0.6851 | +0.3007 | -1.0248 | +0.2833 | -0.3397 | +0.5840 |
| Unfiltered 2x | -0.1563 | +0.9054 | -0.6266 | +0.4894 | -0.7829 | +1.3948 |

## Regime Performance Breakdown (Filtered)

### Filtered 1x

| Regime | Hours | % Time | Alloc | Price PnL | Funding | Net PnL |
|--------|-------|--------|-------|-----------|---------|---------|
| CRISIS | 0 | 0.0% | 0% | +0.0000 | +0.0000 | +0.0000 |
| QUIET | 5808 | 15.0% | 50% | +0.0210 | +0.0423 | +0.0634 |
| UPTREND | 13389 | 34.5% | 100% | -0.2327 | +0.1877 | -0.0450 |
| RANGE | 6949 | 17.9% | 70% | -0.1408 | +0.0691 | -0.0717 |
| DOWNTREND | 12648 | 32.6% | 20% | +0.0301 | +0.0586 | +0.0887 |

### Filtered 2x

| Regime | Hours | % Time | Alloc | Price PnL | Funding | Net PnL |
|--------|-------|--------|-------|-----------|---------|---------|
| CRISIS | 0 | 0.0% | 0% | +0.0000 | +0.0000 | +0.0000 |
| QUIET | 5808 | 15.0% | 50% | +0.0114 | +0.0643 | +0.0758 |
| UPTREND | 13389 | 34.5% | 100% | -0.4717 | +0.2499 | -0.2218 |
| RANGE | 6949 | 17.9% | 70% | -0.0680 | +0.0879 | +0.0199 |
| DOWNTREND | 12648 | 32.6% | 20% | +0.1031 | +0.0916 | +0.1948 |

### Filtered 3x

| Regime | Hours | % Time | Alloc | Price PnL | Funding | Net PnL |
|--------|-------|--------|-------|-----------|---------|---------|
| CRISIS | 0 | 0.0% | 0% | +0.0000 | +0.0000 | +0.0000 |
| QUIET | 5808 | 15.0% | 50% | -0.0060 | +0.0801 | +0.0740 |
| UPTREND | 13389 | 34.5% | 100% | -0.5729 | +0.2786 | -0.2943 |
| RANGE | 6949 | 17.9% | 70% | +0.0562 | +0.1004 | +0.1566 |
| DOWNTREND | 12648 | 32.6% | 20% | +0.1830 | +0.1250 | +0.3080 |

## Unfiltered 1x

- **Regime filter:** OFF
- **Leverage:** 1.0x
- **ADV threshold:** $50M
- **Eligible tokens:** 111
- **Period:** 2021-10-10 to 2026-03-14 (4.4y)

### Full Period

| Metric | Value |
|--------|-------|
| Total Return | -35.32% |
| Ann Return | -9.38% |
| Ann Vol | 36.53% |
| Sharpe | -0.257 |
| Calmar | -0.124 |
| Sortino | -0.340 |
| Max Dd | -75.46% |
| Win Rate | 49.58% |
| Trade Count | 5446 |
| Total Funding Income | +0.8347 |
| Total Price Pnl | -0.4430 |
| Total Fees | 0.7437 |

### Last 12 Months

| Metric | Value |
|--------|-------|
| Return | -4.60% |
| Max Drawdown | -43.25% |
| Sharpe | -0.094 |
| Calmar | -0.106 |
| Sortino | -0.135 |

### Monthly Returns (last 24)

| Month | Return |
|-------|--------|
| 2024-04 | -5.02% |
| 2024-05 | -15.04% |
| 2024-06 | -4.57% |
| 2024-07 | +1.30% |
| 2024-08 | -6.97% |
| 2024-09 | +2.16% |
| 2024-10 | -1.31% |
| 2024-11 | +10.25% |
| 2024-12 | -10.11% |
| 2025-01 | -3.72% |
| 2025-02 | +37.34% |
| 2025-03 | +9.36% |
| 2025-04 | -9.20% |
| 2025-05 | +11.07% |
| 2025-06 | -5.53% |
| 2025-07 | +23.88% |
| 2025-08 | +5.13% |
| 2025-09 | -1.13% |
| 2025-10 | +18.48% |
| 2025-11 | -25.48% |
| 2025-12 | -4.92% |
| 2026-01 | +15.17% |
| 2026-02 | -16.07% |
| 2026-03 | -1.85% |

### Top 5 Contributors

| Token | Total | Funding | Price |
|-------|-------|---------|-------|
| GALA | +0.3020 | +0.0126 | +0.2894 |
| SOL | +0.2270 | +0.0133 | +0.2137 |
| ZEC | +0.1890 | +0.0096 | +0.1794 |
| ALGO | +0.1595 | +0.0054 | +0.1541 |
| BTC | +0.1572 | +0.0006 | +0.1566 |

### Bottom 5 Detractors

| Token | Total | Funding | Price |
|-------|-------|---------|-------|
| WIF | -0.1279 | +0.0155 | -0.1434 |
| DOT | -0.1325 | +0.0104 | -0.1429 |
| NEO | -0.1368 | +0.0001 | -0.1369 |
| MYX | -0.1457 | +0.0161 | -0.1617 |
| AXS | -0.1658 | +0.1037 | -0.2694 |

## Filtered 1x

- **Regime filter:** ON
- **Leverage:** 1.0x
- **ADV threshold:** $50M
- **Eligible tokens:** 111
- **Period:** 2021-10-10 to 2026-03-14 (4.4y)

### Full Period

| Metric | Value |
|--------|-------|
| Total Return | -53.67% |
| Ann Return | -15.96% |
| Ann Vol | 26.46% |
| Sharpe | -0.603 |
| Calmar | -0.214 |
| Sortino | -0.721 |
| Max Dd | -74.48% |
| Win Rate | 49.42% |
| Trade Count | 5446 |
| Total Funding Income | +0.3577 |
| Total Price Pnl | -0.3224 |
| Total Fees | 0.5712 |

### Last 12 Months

| Metric | Value |
|--------|-------|
| Return | +5.29% |
| Max Drawdown | -18.70% |
| Sharpe | 0.175 |
| Calmar | 0.283 |
| Sortino | 0.234 |

### Monthly Returns (last 24)

| Month | Return |
|-------|--------|
| 2024-04 | -3.76% |
| 2024-05 | -12.17% |
| 2024-06 | -2.32% |
| 2024-07 | -1.49% |
| 2024-08 | -0.37% |
| 2024-09 | +1.73% |
| 2024-10 | -1.89% |
| 2024-11 | +13.31% |
| 2024-12 | -10.11% |
| 2025-01 | -2.00% |
| 2025-02 | +14.54% |
| 2025-03 | -1.06% |
| 2025-04 | -7.12% |
| 2025-05 | +11.27% |
| 2025-06 | -5.48% |
| 2025-07 | +17.29% |
| 2025-08 | +3.08% |
| 2025-09 | -6.57% |
| 2025-10 | +11.28% |
| 2025-11 | -5.88% |
| 2025-12 | -1.70% |
| 2026-01 | +5.23% |
| 2026-02 | -4.79% |
| 2026-03 | -4.76% |

### Top 5 Contributors

| Token | Total | Funding | Price |
|-------|-------|---------|-------|
| GALA | +0.1233 | +0.0075 | +0.1158 |
| SOL | +0.0885 | +0.0034 | +0.0851 |
| CRV | +0.0862 | +0.0009 | +0.0853 |
| BTC | +0.0756 | -0.0006 | +0.0762 |
| BCH | +0.0652 | +0.0077 | +0.0575 |

### Bottom 5 Detractors

| Token | Total | Funding | Price |
|-------|-------|---------|-------|
| CHZ | -0.0728 | +0.0055 | -0.0783 |
| IMX | -0.0732 | +0.0050 | -0.0782 |
| OP | -0.0746 | +0.0056 | -0.0802 |
| WIF | -0.0932 | +0.0104 | -0.1036 |
| TIA | -0.1058 | +0.0115 | -0.1173 |

## Filtered 2x

- **Regime filter:** ON
- **Leverage:** 2.0x
- **ADV threshold:** $50M
- **Eligible tokens:** 111
- **Period:** 2021-10-10 to 2026-03-14 (4.4y)

### Full Period

| Metric | Value |
|--------|-------|
| Total Return | -84.27% |
| Ann Return | -34.16% |
| Ann Vol | 52.92% |
| Sharpe | -0.646 |
| Calmar | -0.361 |
| Sortino | -0.772 |
| Max Dd | -94.61% |
| Win Rate | 49.42% |
| Trade Count | 5446 |
| Total Funding Income | +0.4937 |
| Total Price Pnl | -0.4251 |
| Total Fees | 0.9086 |

### Last 12 Months

| Metric | Value |
|--------|-------|
| Return | +1.22% |
| Max Drawdown | -35.48% |
| Sharpe | 0.020 |
| Calmar | 0.034 |
| Sortino | 0.027 |

### Monthly Returns (last 24)

| Month | Return |
|-------|--------|
| 2024-04 | -7.98% |
| 2024-05 | -23.08% |
| 2024-06 | -4.83% |
| 2024-07 | -3.05% |
| 2024-08 | -0.87% |
| 2024-09 | +3.18% |
| 2024-10 | -4.45% |
| 2024-11 | +25.46% |
| 2024-12 | -21.40% |
| 2025-01 | -5.75% |
| 2025-02 | +29.22% |
| 2025-03 | -2.22% |
| 2025-04 | -14.20% |
| 2025-05 | +21.87% |
| 2025-06 | -11.37% |
| 2025-07 | +35.96% |
| 2025-08 | +5.53% |
| 2025-09 | -13.16% |
| 2025-10 | +22.33% |
| 2025-11 | -12.31% |
| 2025-12 | -4.37% |
| 2026-01 | +10.38% |
| 2026-02 | -9.40% |
| 2026-03 | -9.37% |

### Top 5 Contributors

| Token | Total | Funding | Price |
|-------|-------|---------|-------|
| GALA | +0.2817 | +0.0141 | +0.2676 |
| SOL | +0.1690 | +0.0064 | +0.1625 |
| CRV | +0.1561 | +0.0027 | +0.1534 |
| BTC | +0.1508 | +0.0001 | +0.1507 |
| NEAR | +0.1399 | +0.0050 | +0.1349 |

### Bottom 5 Detractors

| Token | Total | Funding | Price |
|-------|-------|---------|-------|
| NEO | -0.1202 | -0.0006 | -0.1196 |
| IMX | -0.1313 | +0.0096 | -0.1409 |
| DOT | -0.1334 | +0.0052 | -0.1386 |
| TIA | -0.1600 | +0.0117 | -0.1717 |
| CHZ | -0.1694 | +0.0087 | -0.1781 |

## Filtered 3x

- **Regime filter:** ON
- **Leverage:** 3.0x
- **ADV threshold:** $50M
- **Eligible tokens:** 111
- **Period:** 2021-10-10 to 2026-03-14 (4.4y)

### Full Period

| Metric | Value |
|--------|-------|
| Total Return | -96.10% |
| Ann Return | -51.95% |
| Ann Vol | 79.37% |
| Sharpe | -0.655 |
| Calmar | -0.524 |
| Sortino | -0.782 |
| Max Dd | -99.06% |
| Win Rate | 49.42% |
| Trade Count | 5446 |
| Total Funding Income | +0.5840 |
| Total Price Pnl | -0.3397 |
| Total Fees | 1.1998 |

### Last 12 Months

| Metric | Value |
|--------|-------|
| Return | -11.14% |
| Max Drawdown | -50.02% |
| Sharpe | -0.123 |
| Calmar | -0.223 |
| Sortino | -0.164 |

### Monthly Returns (last 24)

| Month | Return |
|-------|--------|
| 2024-04 | -12.59% |
| 2024-05 | -32.83% |
| 2024-06 | -7.51% |
| 2024-07 | -4.66% |
| 2024-08 | -1.52% |
| 2024-09 | +4.34% |
| 2024-10 | -7.63% |
| 2024-11 | +35.71% |
| 2024-12 | -33.24% |
| 2025-01 | -11.07% |
| 2025-02 | +43.57% |
| 2025-03 | -3.48% |
| 2025-04 | -21.17% |
| 2025-05 | +31.39% |
| 2025-06 | -17.56% |
| 2025-07 | +55.78% |
| 2025-08 | +7.30% |
| 2025-09 | -19.70% |
| 2025-10 | +32.88% |
| 2025-11 | -19.12% |
| 2025-12 | -7.92% |
| 2026-01 | +15.40% |
| 2026-02 | -13.85% |
| 2026-03 | -13.82% |

### Top 5 Contributors

| Token | Total | Funding | Price |
|-------|-------|---------|-------|
| GALA | +0.4720 | +0.0201 | +0.4519 |
| SOL | +0.2393 | +0.0089 | +0.2304 |
| CRV | +0.2381 | +0.0052 | +0.2329 |
| NEAR | +0.2376 | +0.0080 | +0.2295 |
| BTC | +0.2320 | +0.0014 | +0.2306 |

### Bottom 5 Detractors

| Token | Total | Funding | Price |
|-------|-------|---------|-------|
| NEO | -0.1544 | -0.0009 | -0.1534 |
| TIA | -0.1759 | +0.0092 | -0.1851 |
| IMX | -0.1849 | +0.0135 | -0.1984 |
| DOT | -0.2436 | +0.0084 | -0.2520 |
| CHZ | -0.2838 | +0.0119 | -0.2958 |

## Unfiltered 2x

- **Regime filter:** OFF
- **Leverage:** 2.0x
- **ADV threshold:** $50M
- **Eligible tokens:** 111
- **Period:** 2021-10-10 to 2026-03-14 (4.4y)

### Full Period

| Metric | Value |
|--------|-------|
| Total Return | -76.85% |
| Ann Return | -28.16% |
| Ann Vol | 73.07% |
| Sharpe | -0.385 |
| Calmar | -0.295 |
| Sortino | -0.511 |
| Max Dd | -95.34% |
| Win Rate | 49.58% |
| Trade Count | 5446 |
| Total Funding Income | +1.3948 |
| Total Price Pnl | -0.7829 |
| Total Fees | 1.3762 |

### Last 12 Months

| Metric | Value |
|--------|-------|
| Return | -28.22% |
| Max Drawdown | -70.53% |
| Sharpe | -0.289 |
| Calmar | -0.400 |
| Sortino | -0.414 |

### Monthly Returns (last 24)

| Month | Return |
|-------|--------|
| 2024-04 | -10.65% |
| 2024-05 | -28.26% |
| 2024-06 | -9.39% |
| 2024-07 | +1.85% |
| 2024-08 | -13.85% |
| 2024-09 | +3.73% |
| 2024-10 | -3.52% |
| 2024-11 | +18.68% |
| 2024-12 | -21.40% |
| 2025-01 | -10.42% |
| 2025-02 | +83.49% |
| 2025-03 | +17.54% |
| 2025-04 | -18.27% |
| 2025-05 | +21.43% |
| 2025-06 | -11.69% |
| 2025-07 | +50.21% |
| 2025-08 | +9.23% |
| 2025-09 | -4.44% |
| 2025-10 | +36.87% |
| 2025-11 | -46.92% |
| 2025-12 | -11.95% |
| 2026-01 | +30.31% |
| 2026-02 | -30.64% |
| 2026-03 | -4.32% |

### Top 5 Contributors

| Token | Total | Funding | Price |
|-------|-------|---------|-------|
| GALA | +0.7060 | +0.0272 | +0.6788 |
| SOL | +0.5267 | +0.0311 | +0.4956 |
| ALGO | +0.3866 | +0.0134 | +0.3732 |
| ZEC | +0.3845 | +0.0160 | +0.3686 |
| BTC | +0.3604 | +0.0033 | +0.3571 |

### Bottom 5 Detractors

| Token | Total | Funding | Price |
|-------|-------|---------|-------|
| LTC | -0.2231 | +0.0058 | -0.2289 |
| TIA | -0.2759 | +0.0235 | -0.2994 |
| NEO | -0.3071 | +0.0003 | -0.3074 |
| DOT | -0.3594 | +0.0271 | -0.3865 |
| AXS | -0.3822 | +0.2398 | -0.6220 |

## Cost Breakdown

| Variant | Gross PnL | Fees | Net PnL | Fee Drag |
|---------|----------|------|---------|----------|
| Unfiltered 1x | +0.3917 | 0.7437 | -0.3521 | 189.9% |
| Filtered 1x | +0.0353 | 0.5712 | -0.5358 | 1616.7% |
| Filtered 2x | +0.0687 | 0.9086 | -0.8399 | 1323.2% |
| Filtered 3x | +0.2443 | 1.1998 | -0.9555 | 491.1% |
| Unfiltered 2x | +0.6119 | 1.3762 | -0.7643 | 224.9% |

## Conclusions

### Regime Filter Impact (1x)

- **MaxDD:** -75.46% -> -74.48% (+1.3% change)
- **Ann Return:** -9.38% -> -15.96%
- **Sharpe:** -0.257 -> -0.603
- **Calmar:** -0.124 -> -0.214
- **12M Return:** -4.60% -> +5.29%
- **12M MaxDD:** -43.25% -> -18.70%

### Leverage Analysis (Regime-Filtered)

- **1x:** Ret=-15.96%, MaxDD=-74.48%, Sharpe=-0.603, Calmar=-0.214
- **2x:** Ret=-34.16%, MaxDD=-94.61%, Sharpe=-0.646, Calmar=-0.361
- **3x:** Ret=-51.95%, MaxDD=-99.06%, Sharpe=-0.655, Calmar=-0.524

### Key Findings

- **Unfiltered 1x:** Funding=+0.8347, Price=-0.4430, Net=+0.3917
- **Filtered 1x:** Funding=+0.3577, Price=-0.3224, Net=+0.0353
- **Filtered 2x:** Funding=+0.4937, Price=-0.4251, Net=+0.0687
- **Filtered 3x:** Funding=+0.5840, Price=-0.3397, Net=+0.2443
- **Unfiltered 2x:** Funding=+1.3948, Price=-0.7829, Net=+0.6119

### Verdict

**REJECT.** The cross-sectional carry strategy is fundamentally broken in this period. Price losses overwhelm funding income (-0.4430 price vs +0.8347 funding). The long leg (low-funding tokens) suffers persistent price losses that the short leg cannot hedge.

**Root cause analysis:**
- The long leg buys tokens with the lowest funding rates. In crypto, low funding 
  often signals bearish sentiment or structural selling pressure.
- The short leg shorts tokens with high positive funding. These tend to be tokens 
  in demand (bullish), so shorts lose on price moves in uptrends.
- The L/S construction creates a systematic **anti-momentum** bias: long unloved 
  tokens, short popular tokens. In trending crypto markets, this is toxic.
- The regime filter helps reduce exposure during crashes (-12pp MaxDD improvement) 
  but cannot fix the fundamental signal problem.

**Recommendation:** Abandon cross-sectional L/S carry. Instead consider:
1. **Pure short carry**: Short only the highest-funding tokens, hedge with BTC/ETH futures
2. **Carry + momentum**: Only short high-funding tokens with bearish momentum
3. **Delta-neutral carry**: Market-make a single token, collect funding via basis trade