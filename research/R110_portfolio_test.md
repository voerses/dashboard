# R110: 3-Strategy Portfolio Test

**Date:** 2026-03-24
**Period:** 2021-07-01 to 2026-03-14
**Cost:** 10bps round-trip

## Executive Summary

This research tests a portfolio combining three uncorrelated BTC strategies:
1. **V3 Momentum** (s320): Daily EMA20/EMA50 crossover, weekly rebalance
2. **Intraday Momentum Breakout**: 8h momentum > 2x ATR entry, trailing stop 1.5x ATR, max 48h hold
3. **Macro Regime Rotation**: US10Y and DXY both falling -> long BTC, weekly rebalance

**Key Result:** Best portfolio (Max Sharpe) achieves Sharpe -0.05 vs V3-only 0.60 and BTC B&H 0.25. Max DD: -38.5% vs V3 -45.5%.

## 1. Individual Strategy Performance

| Metric | V3 Momentum | Intraday Momentum | Macro Regime | BTC Buy&Hold |
|--------|------------|-------------------|-------------|-------------|
| Total Return | 126.2% | -98.3% | 0.4% | 102.0% |
| Ann Return | 19.0% | -57.8% | 0.1% | 16.2% |
| Ann Vol | 37.0% | 30.9% | 26.0% | 53.6% |
| Sharpe | 0.51 | -1.87 | 0.00 | 0.30 |
| Sortino | 0.54 | -2.87 | 0.00 | 0.43 |
| Max DD | -45.5% | -98.3% | -29.6% | -76.6% |
| Max DD Duration (days) | 756 | 1660 | 844 | 846 |
| Calmar | 0.42 | -0.59 | 0.00 | 0.21 |
| Win Rate | 25.4% | 33.1% | 14.3% | 49.5% |
| Profit Factor | 1.15 | 0.67 | 1.04 | 1.09 |

## 2. Correlation Analysis

### Pairwise Correlation Matrix (Daily Returns)

| | v3_momentum | intraday_momentum | macro_regime |
|---|---|---|---|
| v3_momentum | 1.0000 | 0.0724 | 0.3293 |
| intraday_momentum | 0.0724 | 1.0000 | 0.0423 |
| macro_regime | 0.3293 | 0.0423 | 1.0000 |

### Rolling 90-Day Correlation Statistics

| Pair | Mean | Min | Max | Std |
|------|------|-----|-----|-----|
| v3_momentum vs intraday_momentum | 0.0599 | -0.4559 | 0.6619 | 0.2374 |
| v3_momentum vs macro_regime | 0.3390 | -0.0216 | 0.9799 | 0.2826 |
| intraday_momentum vs macro_regime | 0.0534 | -0.3560 | 0.4896 | 0.1698 |

**Key Finding:** Low pairwise correlations confirm independent signal sources.

## 3. Portfolio Allocation Results

| Metric | Equal Weight | Risk Parity | Max Sharpe | V3-Only | BTC B&H |
|--------|-------------|------------|-----------|---------|---------|
| Total Return | -60.0% | -51.6% | -5.2% | 136.4% | 72.5% |
| Ann Return | -18.7% | -15.1% | -1.2% | 21.4% | 13.1% |
| Ann Vol | 20.4% | 19.6% | 22.9% | 35.9% | 52.9% |
| Sharpe | -0.92 | -0.77 | -0.05 | 0.60 | 0.25 |
| Sortino | -1.58 | -1.09 | -0.07 | 0.64 | 0.35 |
| Max DD | -64.4% | -55.0% | -38.5% | -45.5% | -76.6% |
| Max DD Duration (days) | 1599 | 1604 | 858 | 756 | 846 |
| Calmar | -0.29 | -0.27 | -0.03 | 0.47 | 0.17 |
| Win Rate | 36.5% | 36.2% | 39.3% | 25.3% | 49.4% |
| Profit Factor | 0.86 | 0.87 | 1.01 | 1.17 | 1.08 |

### Average Weights

**Risk Parity (average weights):**
- v3_momentum: 34.4%
- intraday_momentum: 25.8%
- macro_regime: 39.8%

**Max Sharpe (average weights):**
- v3_momentum: 42.9%
- intraday_momentum: 12.3%
- macro_regime: 44.8%

## 4. Regime Analysis (200d SMA)

BTC price above 200d SMA = Bull, below = Bear.

| Strategy | Bull Days | Bear Days | Bull Ann Ret | Bear Ann Ret | Bull Sharpe | Bear Sharpe | Bull MaxDD | Bear MaxDD |
|----------|-----------|-----------|-------------|-------------|------------|------------|-----------|-----------|
| V3-Only | 973 | 738 | 74.2% | -28.0% | 1.48 | -1.22 | -31.8% | -53.2% |
| Equal Weight | 973 | 738 | 1.8% | -38.7% | 0.19 | -2.89 | -28.7% | -63.9% |
| Risk Parity | 955 | 696 | -0.7% | -33.5% | 0.07 | -2.10 | -25.3% | -53.5% |
| Max Sharpe | 937 | 684 | 21.5% | -25.6% | 0.87 | -1.73 | -20.7% | -44.1% |
| BTC B&H | 973 | 738 | 163.1% | -60.4% | 2.21 | -1.29 | -30.2% | -89.3% |

**V3 Range/Bear Fix:** Equal Weight bear Sharpe = -2.89 vs V3-only = -1.22. Both struggle in bear markets.

## 5. Walk-Forward Portfolio Test (4 Windows)

18mo train / 6mo test. Optimal weights in-sample, applied OOS.

| Window | Train Period | Test Period | OOS Sharpe | OOS Ann Ret | OOS Max DD | Weights |
|--------|-------------|------------|-----------|-----------|----------|---------|
| Window 1 | 2021-07-01 to 2022-12-30 | 2022-12-30 to 2023-06-30 | 0.80 | 24.8% | -13.3% | v3_momentum=60.0%, intraday_momentum=10.0%, macro_regime=30.0% |
| Window 2 | 2022-05-26 to 2023-11-24 | 2023-11-24 to 2024-05-24 | 2.84 | 106.6% | -14.6% | v3_momentum=60.0%, intraday_momentum=10.0%, macro_regime=30.0% |
| Window 3 | 2023-04-20 to 2024-10-18 | 2024-10-18 to 2025-04-18 | 0.34 | 8.9% | -18.3% | v3_momentum=48.8%, intraday_momentum=10.0%, macro_regime=41.2% |
| Window 4 | 2024-03-14 to 2025-09-12 | 2025-09-12 to 2026-03-13 | -2.96 | -35.8% | -19.3% | v3_momentum=60.0%, intraday_momentum=10.0%, macro_regime=30.0% |
| **Average** | | | **0.26** | **26.1%** | **-16.4%** | |

Walk-forward stability: 3/4 windows with positive OOS Sharpe.

## 6. Drawdown Recovery Analysis

Episodes where drawdown exceeded 5%.

| Strategy | Num Episodes | Avg Recovery Days | Max Recovery Days | Avg Max DD |
|----------|-------------|-------------------|-------------------|-----------|
| V3-Only | 30 | 48 | 748 | -11.6% |
| Equal Weight | 10 | 160 | 1574 | -12.4% |
| Risk Parity | 7 | 223 | 1550 | -12.6% |
| Max Sharpe | 11 | 139 | 769 | -12.9% |
| BTC B&H | 35 | 42 | 840 | -13.2% |

## 7. Conclusions

1. **Best portfolio allocation:** Max Sharpe (Sharpe: -0.05)
2. **vs V3-only Sharpe (0.60):** Underperformance of 0.65
3. **vs BTC B&H Sharpe (0.25):** Underperformance of 0.30
4. **Max DD reduction:** V3=-45.5% -> Max Sharpe=-38.5% (improved)
5. **Walk-forward stability:** 3/4 windows positive OOS Sharpe (avg=0.26)
6. **Correlation verdict:** Strategies show low pairwise correlations, validating portfolio diversification.

### Individual Strategy Assessment

- **V3 Momentum**: Sharpe=0.51, Total=126.2% -> STRONG
- **Intraday Momentum**: Sharpe=-1.87, Total=-98.3% -> WEAK
- **Macro Regime**: Sharpe=0.00, Total=0.4% -> PASS

## 8. Recommendation

**HOLD.** Portfolio diversification does not conclusively improve on V3-only. Individual strategies need refinement before combining.

**Action items:** Refine Intraday Momentum, Macro Regime before retesting portfolio.