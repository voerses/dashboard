# R112 -- 2-Strategy Portfolio: V3 Momentum + Intraday Momentum

**Date**: 2026-03-24
**Period**: 2021-01-01 to 2026-03-31
**Asset**: BTC spot
**Cost assumption**: 10 bps round-trip

**Context**: R109 KILLED the Macro Regime Rotation signal (non-stationary IC, fragile params). R110 tested a 3-strategy portfolio but one leg is now dead. This research evaluates the simpler V3 + Intraday Momentum combination.

## Strategy Definitions

| # | Strategy | Signal | Rebalance | Position |
|---|----------|--------|-----------|----------|
| 1 | V3 Momentum | EMA(20) > EMA(50) | Weekly (Mon) | Long/Flat |
| 2 | Intraday Breakout | 1h ret > 2% + vol > 2x avg | Per-bar | Long/Flat (8h max) |

## Individual Strategy Metrics

| Metric | Buy&Hold | V3 Momentum | Intraday Breakout |
|--------|----------|-------------|-------------------|
| Ann. Return | 34.23% | 29.40% | 6.13% |
| Ann. Volatility | 58.75% | 43.05% | 13.81% |
| Sharpe | 0.583 | 0.683 | 0.444 |
| Sortino | 0.832 | 0.756 | 0.260 |
| Calmar | 0.447 | 0.499 | 0.382 |
| Max Drawdown | -76.63% | -58.95% | -16.03% |
| Worst Month | -37.29% | -22.04% | -8.52% |
| Total Return | 141.31% | 184.74% | 30.86% |

## Correlation Structure

### V3 vs Intraday Correlation (Daily Returns)

| | V3 Momentum | Intraday Breakout |
|---|---|---|
| **V3 Momentum** | 1.0000 | 0.1876 |
| **Intraday Breakout** | 0.1876 | 1.0000 |

### Crash vs Normal Correlation

Crash days (bottom 5th pctile BTC returns): **95** days

- Normal-day correlation: **0.1797**
- Crash-day correlation: **0.2131**
- Correlation change under stress: **+0.0334**
- LOW crash correlation: good diversification holds under stress

### Rolling 90-Day Correlation (Summary)

| Stat | Value |
|------|-------|
| Mean | 0.1655 |
| Std | 0.1481 |
| Min | -0.2684 |
| Max | 0.4993 |
| Pct > 0.5 | 0.0% |
| Pct < 0 | 14.7% |

## Portfolio Allocation Results

| Portfolio | Ann. Return | Ann. Vol | Sharpe | Sortino | Calmar | Max DD | Worst Month |
|-----------|-------------|----------|--------|---------|--------|--------|-------------|
| Buy & Hold | 34.23% | 58.75% | **0.583** | 0.832 | 0.447 | -76.63% | -37.29% |
| V3 Only | 29.40% | 43.05% | **0.683** | 0.756 | 0.499 | -58.95% | -22.04% |
| Intraday Only | 6.13% | 13.81% | **0.444** | 0.260 | 0.382 | -16.03% | -8.52% |
| 50/50 (Equal Wt) | 17.77% | 23.81% | **0.746** | 0.883 | 0.486 | -36.59% | -14.56% |
| 60/40 (V3 Heavy) | 20.09% | 27.41% | **0.733** | 0.854 | 0.482 | -41.69% | -16.05% |
| 70/30 (V3 Anchor) | 22.42% | 31.18% | **0.719** | 0.826 | 0.483 | -46.46% | -17.54% |
| Risk Parity | 3.42% | 14.92% | **0.229** | 0.246 | 0.095 | -36.13% | -11.10% |

### Risk Parity Average Weights

- V3 Momentum: **30.4%** (range: 0.0% to 100.0%)
- Intraday Breakout: **69.6%** (range: 0.0% to 100.0%)

### Improvement vs V3-Only

| Portfolio | dSharpe | dMaxDD | dSortino |
|-----------|---------|--------|----------|
| 50/50 | +0.063 | +22.36% | +0.128 |
| 60/40 | +0.050 | +17.26% | +0.098 |
| 70/30 | +0.036 | +12.49% | +0.070 |
| Risk Parity | -0.454 | +22.82% | -0.510 |

## Regime Analysis

### Sharpe Ratio by Regime

| Regime | V3 Momentum | Intraday Breakout | 50/50 | 60/40 | 70/30 | Buy & Hold |
|---|---|---|---|---|---|---|
| UPTREND | **3.444** | **1.148** | **3.399** | **3.440** | **3.455** | **4.523** |
| DOWNTREND | -3.838 | -0.632 | -3.500 | -3.647 | -3.736 | -5.102 |
| RANGE | 0.189 | **0.635** | 0.350 | 0.301 | 0.263 | **2.172** |
| CRISIS | **1.384** | -0.388 | **1.179** | **1.259** | **1.310** | -1.683 |

### Ann. Return by Regime

| Regime | V3 Momentum | Intraday Breakout | 50/50 | 60/40 | 70/30 | Buy & Hold |
|---|---|---|---|---|---|---|
| UPTREND | 170.05% | 17.61% | 93.83% | 109.08% | 124.32% | 242.30% |
| DOWNTREND | -134.79% | -9.13% | -71.96% | -84.53% | -97.09% | -315.41% |
| RANGE | 7.90% | 7.65% | 7.78% | 7.80% | 7.83% | 111.97% |
| CRISIS | 46.50% | -4.40% | 21.05% | 26.14% | 31.23% | -149.56% |

### Best Strategy Contributor per Regime

- **UPTREND**: V3 Momentum (Sharpe: 3.444)
- **DOWNTREND**: Intraday Breakout (Sharpe: -0.632)
- **RANGE**: Intraday Breakout (Sharpe: 0.635)
- **CRISIS**: V3 Momentum (Sharpe: 1.384)

### Does Portfolio Fix V3's RANGE Weakness?

- V3 alone in RANGE: Sharpe = 0.189
- 50/50 in RANGE: Sharpe = 0.350
- 60/40 in RANGE: Sharpe = 0.301
- 70/30 in RANGE: Sharpe = 0.263
- **YES**: Portfolio improves RANGE regime performance

## Walk-Forward Portfolio Test

Configuration: 12mo train / 6mo test, 6 windows
Optimization: Grid search V3 weight [0.10, 0.90] step 0.05

| Window | Test Period | Opt V3/Intra | Port Sharpe | V3 Sharpe | dSharpe | 50/50 | 60/40 | 70/30 | Port > V3? |
|--------|-------------|--------------|-------------|-----------|---------|-------|-------|-------|------------|
| W1 | 2022-01-01 to 2022-07-01 | 0.25/0.75 | -0.063 | -2.068 | +2.004 | -0.914 | -1.272 | -1.582 | YES |
| W2 | 2022-07-01 to 2023-01-01 | 0.10/0.90 | -0.708 | -2.072 | +1.364 | -1.854 | -1.940 | -1.996 | YES |
| W3 | 2023-01-01 to 2023-07-01 | 0.10/0.90 | -0.332 | 1.378 | -1.710 | 0.966 | 1.103 | 1.203 | NO |
| W4 | 2023-07-01 to 2024-01-01 | 0.90/0.10 | 1.903 | 1.896 | +0.007 | 1.942 | 1.931 | 1.920 | YES |
| W5 | 2024-01-01 to 2024-07-01 | 0.90/0.10 | 1.672 | 1.663 | +0.009 | 1.699 | 1.697 | 1.690 | YES |
| W6 | 2024-07-01 to 2025-01-01 | 0.50/0.50 | 1.019 | 1.327 | -0.308 | 1.019 | 1.124 | 1.198 | NO |

**Summary**: Portfolio beats V3 in **4/6** windows, avg dSharpe = **+0.228**, avg optimal V3 weight = **0.46**

## Drawdown Recovery Analysis

| Strategy | Max DD | Peak Date | Trough Date | Recovery Date | Recovery Days | Recovered? |
|----------|--------|-----------|-------------|---------------|---------------|------------|
| V3 Only | -58.95% | 2021-11-08 | 2023-03-10 | 2024-02-28 | 842 | YES |
| 50/50 | -36.59% | 2021-11-08 | 2023-03-10 | 2024-02-28 | 842 | YES |
| 60/40 | -41.69% | 2021-11-08 | 2023-03-10 | 2024-02-28 | 842 | YES |
| 70/30 | -46.46% | 2021-11-08 | 2023-03-10 | 2024-02-28 | 842 | YES |
| Risk Parity | -36.13% | 2021-11-08 | 2023-08-18 | N/A | 1587 | NO (still in DD) |
| Buy & Hold | -76.63% | 2021-11-08 | 2022-11-21 | 2024-03-04 | 847 | YES |

## Comparison: 2-Strategy vs R110 3-Strategy Portfolio

Key question: Does removing Macro Regime hurt significantly, or was Intraday the main diversifier?

| Metric | V3 Only | 2-Strat 50/50 | 2-Strat 60/40 | 2-Strat RiskPar | R110 EqWt (33/33/33) | R110 RiskPar | R110 V3Anchor |
|--------|---------|---------------|---------------|-----------------|----------------------|--------------|---------------|
| Sharpe | 0.683 | 0.746 | 0.733 | 0.229 | 0.880 | 0.820 | 0.825 |
| Sortino | 0.756 | 0.883 | 0.854 | 0.246 | 1.142 | 1.040 | 1.034 |
| Calmar | 0.499 | 0.486 | 0.482 | 0.095 | 0.609 | 0.608 | 0.550 |
| Max DD | -58.95% | -36.59% | -41.69% | -36.13% | -30.62% | -20.25% | -38.75% |
| Ann. Return | 29.40% | 17.77% | 20.09% | 3.42% | 18.63% | 12.30% | 21.33% |
| Worst Month | -22.04% | -14.56% | -16.05% | -11.10% | -12.08% | -5.33% | -14.62% |

### Impact of Removing Macro Regime

- Sharpe change (50/50 vs R110 EqWt): **-0.134**
- Sharpe change (RiskPar 2s vs R110 RiskPar): **-0.591**
- MaxDD change (50/50 vs R110 EqWt): **-5.97%** (more negative = worse)

**Verdict**: Macro Regime removal **HURTS** meaningfully. The 3-strategy portfolio was genuinely better, but since Macro is KILLED, we must accept the 2-strategy version.

## Kill Criteria Evaluation

| Criterion | Threshold | Result |
|-----------|-----------|--------|
| Portfolio Sharpe >= V3 | 0.683 | PASS (50/50 Sharpe 0.746 >= V3 0.683) |
| Portfolio MaxDD <= V3 | -58.95% | PASS (-36.59% vs V3 -58.95%) |
| Walk-forward >= 3/6 | 3/6 | PASS (4/6 windows) |
| Crash corr <= 0.5 | 0.5 | OK (crash corr = 0.213 <= 0.5) |

**Overall Verdict**: **PASS**

### Per-Allocation Kill Verdicts

| Allocation | Sharpe | MaxDD | Sharpe >= V3? | MaxDD <= V3? | Verdict |
|------------|--------|-------|---------------|--------------|---------|
| 50/50 | 0.746 | -36.59% | YES | YES | **PASS** |
| 60/40 | 0.733 | -41.69% | YES | YES | **PASS** |
| 70/30 | 0.719 | -46.46% | YES | YES | **PASS** |
| Risk Parity | 0.229 | -36.13% | NO | YES | **FAIL** |

## Conclusion

The 2-strategy portfolio (V3 + Intraday) **PASSES** all kill criteria.
**Best allocation method**: 50/50 (Sharpe: 0.746)

### Recommendation

- Use **50/50** allocation for the V3 + Intraday portfolio
- Portfolio Sharpe: 0.746 vs V3-only: 0.683 (+0.063)
- Portfolio MaxDD: -36.59% vs V3-only: -58.95%
- Removing Macro Regime: costs 0.134 Sharpe vs 3-strategy, but Macro is KILLED so this is the best available option

## Next Steps

1. Implement the 2-strategy portfolio in the live signal pipeline
2. Monitor V3 vs Intraday rolling correlation -- if it rises above 0.5, revert to V3-only
3. Periodic rebalance check: re-run walk-forward quarterly
4. Search for a 3rd uncorrelated signal to replace Macro Regime
