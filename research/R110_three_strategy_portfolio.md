# R110 -- Three-Strategy Portfolio: V3 Momentum + Intraday Breakout + Macro Regime

**Date**: 2026-03-24
**Period**: 2021-01-01 to 2026-03-31
**Asset**: BTC spot
**Cost assumption**: 10 bps round-trip

## Strategy Definitions

| # | Strategy | Signal | Rebalance | Position |
|---|----------|--------|-----------|----------|
| 1 | V3 Momentum | EMA(20) > EMA(50) | Weekly (Mon) | Long/Flat |
| 2 | Intraday Breakout | 1h ret > 2% + vol > 2x avg | Per-bar | Long/Flat (8h max) |
| 3 | Macro Regime | US10Y chg < 0 + DXY chg < 0 | Daily | Long/Flat |

## Individual Strategy Metrics

| Metric | Buy&Hold | V3 Momentum | Intraday Breakout | Macro Regime |
|--------|----------|-------------|-------------------|--------------|
| Ann. Return | 34.23% | 29.40% | 6.13% | 20.37% |
| Ann. Volatility | 58.75% | 43.05% | 13.81% | 28.31% |
| Sharpe | 0.583 | 0.683 | 0.444 | 0.720 |
| Sortino | 0.832 | 0.756 | 0.260 | 0.630 |
| Calmar | 0.447 | 0.499 | 0.382 | 0.637 |
| Max Drawdown | -76.63% | -58.95% | -16.03% | -31.96% |
| Worst Month | -37.29% | -22.04% | -8.52% | -17.97% |
| Total Return | 141.31% | 184.74% | 30.86% | 133.97% |

## Correlation Structure

### 3x3 Correlation Matrix (Daily Returns)

| | V3 Momentum | Intraday Breakout | Macro Regime |
|---|---|---|---|
| **V3 Momentum** | 1.0000 | 0.1876 | 0.3573 |
| **Intraday Breakout** | 0.1876 | 1.0000 | 0.1268 |
| **Macro Regime** | 0.3573 | 0.1268 | 1.0000 |

### Crash vs Normal Correlation

Crash days (bottom 5th pctile BTC returns): **95** days

**Normal-day correlations:**

| | V3 Momentum | Intraday Breakout | Macro Regime |
|---|---|---|---|
| **V3 Momentum** | 1.0000 | 0.1797 | 0.3746 |
| **Intraday Breakout** | 0.1797 | 1.0000 | 0.1426 |
| **Macro Regime** | 0.3746 | 0.1426 | 1.0000 |

**Crash-day correlations:**

| | V3 Momentum | Intraday Breakout | Macro Regime |
|---|---|---|---|
| **V3 Momentum** | 1.0000 | 0.2131 | 0.0555 |
| **Intraday Breakout** | 0.2131 | 1.0000 | -0.0808 |
| **Macro Regime** | 0.0555 | -0.0808 | 1.0000 |

### Rolling 90-Day Correlations (Summary)

| Pair | Mean | Std | Min | Max |
|------|------|-----|-----|-----|
| V3 vs Intraday | 0.1655 | 0.1481 | -0.2684 | 0.4993 |
| V3 vs Macro | 0.3661 | 0.2475 | -0.0981 | 0.8655 |
| Intraday vs Macro | 0.1031 | 0.1473 | -0.3391 | 0.5328 |

## Portfolio Allocation Comparison

| Portfolio | Ann. Return | Sharpe | Sortino | Calmar | Max DD | Worst Month |
|-----------|-------------|--------|---------|--------|--------|-------------|
| Buy & Hold | 34.23% | **0.583** | 0.832 | 0.447 | -76.63% | -37.29% |
| V3 Only | 29.40% | **0.683** | 0.756 | 0.499 | -58.95% | -22.04% |
| V3 + Intraday (50/50) | 17.77% | **0.746** | 0.883 | 0.486 | -36.59% | -14.56% |
| V3 + Macro (50/50) | 24.88% | **0.838** | 1.029 | 0.596 | -41.73% | -14.93% |
| Equal Weight (33/33/33) | 18.63% | **0.880** | 1.142 | 0.609 | -30.62% | -12.08% |
| Risk Parity | 12.30% | **0.820** | 1.040 | 0.608 | -20.25% | -5.33% |
| V3 Anchor (50/25/25) | 21.33% | **0.825** | 1.034 | 0.550 | -38.75% | -14.62% |

### Risk Parity Average Weights

- V3 Momentum: **23.1%** (range: 0.0% to 100.0%)
- Intraday Breakout: **49.3%** (range: 0.0% to 100.0%)
- Macro Regime: **27.6%** (range: 0.0% to 100.0%)

## Regime Analysis

### Sharpe Ratio by Regime

| Regime | V3 Momentum | Intraday Breakout | Macro Regime | Equal Weight | V3 Anchor | Buy & Hold |
|---|---|---|---|---|---|---|
| UPTREND | **3.444** | **1.148** | **3.049** | **3.810** | **3.735** | **4.523** |
| DOWNTREND | -3.838 | -0.632 | -1.964 | -3.459 | -3.755 | -5.102 |
| RANGE | 0.189 | **0.635** | **0.763** | **0.580** | 0.427 | **2.172** |
| CRISIS | **1.384** | -0.388 | **1.098** | **1.423** | **1.504** | -1.683 |

### Ann. Return by Regime

| Regime | V3 Momentum | Intraday Breakout | Macro Regime | Equal Weight | V3 Anchor | Buy & Hold |
|---|---|---|---|---|---|---|
| UPTREND | 170.05% | 17.61% | 80.35% | 89.34% | 109.52% | 242.30% |
| DOWNTREND | -134.79% | -9.13% | -63.42% | -69.11% | -85.53% | -315.41% |
| RANGE | 7.90% | 7.65% | 17.46% | 11.01% | 10.23% | 111.97% |
| CRISIS | 46.50% | -4.40% | 48.66% | 30.25% | 34.32% | -149.56% |

### Best Strategy Contributor per Regime

- **UPTREND**: V3 Momentum (Sharpe: 3.444)
- **DOWNTREND**: Intraday Breakout (Sharpe: -0.632)
- **RANGE**: Macro Regime (Sharpe: 0.763)
- **CRISIS**: V3 Momentum (Sharpe: 1.384)

### Does Portfolio Fix V3's RANGE Weakness?

- V3 alone in RANGE: Sharpe = 0.189
- Equal Weight in RANGE: Sharpe = 0.580
- V3 Anchor in RANGE: Sharpe = 0.427
- **YES**: Portfolio improves RANGE regime performance

## Walk-Forward Portfolio Test

Configuration: 12mo train / 6mo test, 6 windows

| Window | Test Period | Opt Weights | Port Sharpe | V3 Sharpe | dSharpe | EqW Sharpe | Anchor Sharpe | Port > V3? |
|--------|-------------|-------------|-------------|-----------|---------|------------|---------------|------------|
| W1 | 2022-01-01 to 2022-07-01 | 0.2/0.5/0.3 | -0.548 | -2.068 | +1.519 | -1.235 | -1.707 | YES |
| W2 | 2022-07-01 to 2023-01-01 | 0.1/0.4/0.5 | -0.599 | -2.072 | +1.474 | -1.265 | -1.578 | YES |
| W3 | 2023-01-01 to 2023-07-01 | 0.1/0.7/0.2 | 1.186 | 1.378 | -0.192 | 2.161 | 1.903 | NO |
| W4 | 2023-07-01 to 2024-01-01 | 0.1/0.1/0.8 | 2.420 | 1.896 | +0.524 | 2.298 | 2.172 | YES |
| W5 | 2024-01-01 to 2024-07-01 | 0.1/0.1/0.8 | 1.309 | 1.663 | -0.354 | 1.747 | 1.749 | NO |
| W6 | 2024-07-01 to 2025-01-01 | 0.3/0.3/0.4 | 1.180 | 1.327 | -0.147 | 1.179 | 1.271 | NO |

**Summary**: Portfolio beats V3 in **3/6** windows, avg dSharpe = **+0.471**

## Drawdown Recovery Analysis

| Strategy | Max DD | Peak Date | Trough Date | Recovery Date | Recovery Days | Recovered? |
|----------|--------|-----------|-------------|---------------|---------------|------------|
| V3 Only | -58.95% | 2021-11-08 | 2023-03-10 | 2024-02-28 | 842 | YES |
| Equal Weight | -30.62% | 2021-11-08 | 2022-11-09 | 2023-12-02 | 754 | YES |
| Risk Parity | -20.25% | 2021-11-08 | 2022-11-09 | 2023-12-04 | 756 | YES |
| V3 Anchor | -38.75% | 2021-11-08 | 2022-11-09 | 2023-12-21 | 773 | YES |
| Buy & Hold | -76.63% | 2021-11-08 | 2022-11-21 | 2024-03-04 | 847 | YES |

## Kill Criteria Evaluation

| Criterion | Threshold | Result |
|-----------|-----------|--------|
| Portfolio Sharpe >= V3 | 0.683 | PASS (Equal Weight Sharpe 0.880 >= V3 0.683) |
| Portfolio MaxDD <= V3 | -58.95% | PASS (-30.62% vs V3 -58.95%) |
| Walk-forward >= 3/6 | 3/6 | PASS (3/6 windows) |
| Crash corr <= 0.5 | 0.5 | OK (max crash corr = 0.213 <= 0.5) |

**Overall Verdict**: **PASS**

### Per-Allocation Kill Verdicts

| Allocation | Sharpe | MaxDD | Verdict | Reason |
|------------|--------|-------|---------|--------|
| Equal Weight | 0.880 | -30.62% | **PASSED** | All criteria passed |
| Risk Parity | 0.820 | -20.25% | **PASSED** | All criteria passed |
| V3 Anchor | 0.825 | -38.75% | **PASSED** | All criteria passed |

## Conclusion

The three-strategy portfolio **PASSES** all kill criteria.
**Best allocation method**: Equal Weight (Sharpe: 0.880)

### Recommendation

- Use **Equal Weight** allocation
- Portfolio Sharpe: 0.880 vs V3-only: 0.683 (+0.197)
- Portfolio MaxDD: -30.62% vs V3-only: -58.95%
