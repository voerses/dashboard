# Cross-Sectional Momentum Strategy -- Results

**Run date:** 2026-03-24 12:49
**Overall verdict:** KILL

## Strategy Design

Cross-sectional momentum ranks crypto tokens by recent performance and
goes long the top performers. This is a fundamentally different approach
from time-series momentum (V3) and may provide diversification.

**Variants tested:**
1. **Simple XSMOM** - Rank by N-day raw return, long top 5
2. **Risk-adjusted XSMOM** - Rank by return/volatility (Sharpe-like)
3. **Dual momentum** - Cross-sectional rank AND absolute momentum (price > SMA)

**Lookback periods:** 14d, 30d, 60d, 90d
**Rebalance:** Weekly (168 hourly bars)
**Cost:** 10 bps round-trip per trade
**Universe:** 43 tokens with >= 3 years data (ex-BTC)

## Universe

| Token | Start Date | Duration (days) | Avg Volume |
|-------|-----------|----------------|-----------|
| SHIB | 2021-05-10 | 1769 | 329,051,975,108 |
| DENT | 2020-01-01 | 2264 | 145,813,551 |
| DOGE | 2020-01-01 | 2264 | 79,115,856 |
| GALA | 2021-09-13 | 1643 | 40,641,220 |
| TRX | 2020-01-01 | 2264 | 40,454,924 |
| XRP | 2020-01-01 | 2264 | 17,115,513 |
| ADA | 2020-01-01 | 2264 | 10,137,010 |
| CHZ | 2020-01-01 | 2264 | 8,799,356 |
| HBAR | 2020-01-01 | 2264 | 7,181,735 |
| XLM | 2020-01-01 | 2264 | 5,957,741 |
| FET | 2020-01-01 | 2264 | 2,087,973 |
| ALGO | 2020-01-01 | 2264 | 2,081,386 |
| SAND | 2020-08-14 | 2037 | 2,049,580 |
| CRV | 2020-08-15 | 2037 | 1,330,671 |
| OP | 2022-06-01 | 1382 | 1,171,306 |
| PHA | 2021-06-25 | 1723 | 958,184 |
| NEAR | 2020-10-14 | 1977 | 545,940 |
| LDO | 2022-05-09 | 1405 | 530,743 |
| DYDX | 2021-09-09 | 1647 | 450,935 |
| DOT | 2020-08-18 | 2033 | 356,147 |
| KAVA | 2020-01-01 | 2264 | 332,440 |
| FIL | 2020-10-15 | 1975 | 282,339 |
| LINK | 2020-01-01 | 2264 | 273,290 |
| APT | 2022-10-19 | 1242 | 246,130 |
| LIT | 2021-02-04 | 1466 | 216,072 |
| UNI | 2020-09-17 | 2004 | 193,398 |
| SOL | 2020-08-11 | 2041 | 183,467 |
| AGLD | 2021-10-05 | 1621 | 181,796 |
| SNX | 2020-07-09 | 2074 | 150,109 |
| ATOM | 2020-01-01 | 2264 | 124,570 |

## Temporal Split

- **In-sample:** 2020-01-01 to 2024-04-29 (70%)
- **Out-of-sample:** 2024-04-29 to 2026-03-14 (30%)

## Kill Criteria

| Metric | Threshold | Rationale |
|--------|-----------|-----------|
| Sharpe ratio | < 0.3 | Not worth the complexity |
| Max drawdown | > 30% | Unacceptable risk |
| BTC correlation | > 0.7 | No diversification benefit |

## OOS Results

| Variant | Sharpe | Return | MaxDD | Profit Factor | BTC Corr | Avg Turnover | Trades | Verdict |
|---------|--------|--------|-------|--------------|----------|-------------|--------|---------|
| simple_xsmom_lb14d | 0.910 | 114.8% | -64.1% | 1.03 | 0.659 | 1.079 | 502 | **KILL (MaxDD)** |
| dual_momentum_lb14d | 0.876 | 103.2% | -61.5% | 1.03 | 0.582 | 1.253 | 552 | **KILL (MaxDD)** |
| risk_adjusted_xsmom_lb14d | 0.687 | 49.0% | -63.2% | 1.02 | 0.659 | 1.119 | 518 | **KILL (MaxDD)** |
| risk_adjusted_xsmom_lb60d | 0.670 | 46.8% | -64.4% | 1.02 | 0.669 | 0.552 | 258 | **KILL (MaxDD)** |
| simple_xsmom_lb60d | 0.592 | 28.3% | -63.3% | 1.02 | 0.649 | 0.536 | 248 | **KILL (MaxDD)** |
| risk_adjusted_xsmom_lb90d | 0.452 | 4.8% | -65.5% | 1.01 | 0.680 | 0.425 | 198 | **KILL (MaxDD)** |
| simple_xsmom_lb90d | 0.398 | -5.7% | -66.4% | 1.01 | 0.657 | 0.429 | 204 | **KILL (MaxDD)** |
| dual_momentum_lb60d | 0.315 | -28.3% | -79.1% | 1.01 | 0.565 | 0.736 | 329 | **KILL (MaxDD)** |
| simple_xsmom_lb30d | 0.314 | -18.8% | -69.1% | 1.01 | 0.665 | 0.731 | 346 | **KILL (MaxDD)** |
| risk_adjusted_xsmom_lb30d | 0.311 | -17.7% | -70.1% | 1.01 | 0.662 | 0.770 | 350 | **KILL (MaxDD)** |
| dual_momentum_lb30d | 0.089 | -48.3% | -75.9% | 1.00 | 0.597 | 0.946 | 419 | **KILL (Sharpe, MaxDD)** |
| dual_momentum_lb90d | -0.048 | -60.9% | -85.9% | 1.00 | 0.581 | 0.602 | 267 | **KILL (Sharpe, MaxDD)** |

## IS Results (for comparison)

| Variant | Sharpe | Return | MaxDD | BTC Corr |
|---------|--------|--------|-------|----------|
| simple_xsmom_lb14d | 1.644 | 13657.5% | -90.1% | 0.682 |
| simple_xsmom_lb30d | 1.824 | 30149.6% | -82.3% | 0.692 |
| simple_xsmom_lb60d | 1.332 | 3141.7% | -87.3% | 0.698 |
| simple_xsmom_lb90d | 1.152 | 1363.3% | -89.7% | 0.708 |
| risk_adjusted_xsmom_lb14d | 1.492 | 6587.1% | -91.5% | 0.697 |
| risk_adjusted_xsmom_lb30d | 1.639 | 12275.2% | -82.5% | 0.707 |
| risk_adjusted_xsmom_lb60d | 1.386 | 3944.8% | -87.5% | 0.707 |
| risk_adjusted_xsmom_lb90d | 1.285 | 2479.4% | -86.2% | 0.720 |
| dual_momentum_lb14d | 1.357 | 3689.8% | -92.2% | 0.635 |
| dual_momentum_lb30d | 1.619 | 12703.1% | -88.6% | 0.635 |
| dual_momentum_lb60d | 1.292 | 2700.7% | -89.1% | 0.635 |
| dual_momentum_lb90d | 1.229 | 1994.4% | -89.5% | 0.635 |

## Best Variant

**No variant passes all kill criteria.** Cross-sectional momentum is KILLED.

Possible reasons:
- Crypto tokens are too correlated (beta-driven), reducing cross-sectional signal quality
- Weekly rebalance too slow for rapidly rotating leadership
- Transaction costs eat into thin momentum spreads
- The momentum effect may not exist strongly enough in crypto cross-sections

## Correlation Analysis

All XSMOM variants are fundamentally long-only crypto portfolios.
Even with momentum-based selection, the dominant factor is likely
crypto beta (market direction). This limits diversification potential
relative to the BTC-only V3 strategy.

Average BTC correlation across variants: 0.636

High correlation with BTC confirms that cross-sectional token selection
does not meaningfully reduce crypto beta exposure.

## Conclusion

Cross-sectional momentum does not meet the required bar for
deployment alongside V3. The strategy is KILLED.

**Recommended next steps:**
- Investigate mean-reversion (contrarian) strategies instead
- Consider non-directional strategies (funding rate arb, basis trade)
- Explore cross-asset strategies (crypto vs. macro) for true decorrelation
