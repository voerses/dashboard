# R154 -- BTC Mean-Reversion Strategy Results

**Date**: 2026-03-28
**Asset**: BTC perpetual (1h bars)
**Data range**: 2020-01-01 to 2026-03-17 (54,425 bars)
**Benchmark**: EMA(168) > EMA(720) trend-following on same data

## Strategy Specification

| Parameter | Value |
|-----------|-------|
| RSI Period | 14 |
| RSI Oversold / Overbought | 30 / 70 |
| Bollinger Band Period | 20 |
| Bollinger Band Std | 2.0 |
| ATR Period | 14 |
| EMA Fast (momentum filter) | 168 (7 days) |
| EMA Slow (momentum filter) | 720 (30 days) |
| Take Profit | 1.5x ATR |
| Stop Loss | 2.0x ATR |
| Max Hold Period | 72h (3 days) |
| Position Size | 50% equity per trade |
| Cost per side | 7 bps (taker 4 + slippage 3) |
| Cost round trip | 14 bps |
| Funding | Charged every 8h from perp data |

**Entry Logic**:
- Long: RSI(14) < 30 AND close within 1 ATR of lower BB(20,2.0) AND EMA(168) > EMA(720)
- Short: RSI(14) > 70 AND close within 1 ATR of upper BB(20,2.0) AND EMA(168) < EMA(720)
- Entry at next bar's open (no lookahead)

## Full Period Results

| Metric | 1x Leverage | 2x Leverage | 3x Leverage |
|--------|-------------|-------------|-------------|
| Annual Return | -17.63% | -32.87% | -45.86% |
| Max Drawdown | -70.29% | -91.72% | -97.84% |
| Sharpe | -1.781 | -1.666 | -1.555 |
| Calmar | -0.251 | -0.358 | -0.469 |
| Sortino | -1.043 | -0.979 | -0.918 |
| Trade Count | 694 | 694 | 694 |
| Win Rate | 51.2% | 51.2% | 51.2% |
| Avg Hold (hours) | 8.1 | 8.1 | 8.1 |
| Median Hold (hours) | 4 | 4 | 4 |
| Avg Win | 1.25% | 2.50% | 3.76% |
| Avg Loss | -2.01% | -4.02% | -6.03% |
| Profit Factor | 0.652 | 0.652 | 0.652 |
| Total Return | -70.00% | -91.57% | -97.78% |

### Exit Reason Breakdown

| Exit Reason | 1x | 2x | 3x |
|-------------|-----|-----|-----|
| TP (Take Profit) | 353 | 353 | 353 |
| SL (Stop Loss) | 335 | 335 | 335 |
| MAX_HOLD (72h timeout) | 6 | 6 | 6 |

### Cost Breakdown (Estimated)

| Component | 1x | 2x | 3x |
|-----------|-----|-----|-----|
| Trading fees (est.) | 48.58% | 97.16% | 145.74% |
| Funding paid | 1.60% | 2.40% | 2.89% |
| Total cost drag | 50.18% | 99.56% | 148.63% |

## Last 12 Months Performance

| Metric | 1x Leverage | 2x Leverage | 3x Leverage |
|--------|-------------|-------------|-------------|
| Annual Return | -11.42% | -21.84% | -31.31% |
| Max Drawdown | -11.52% | -21.99% | -31.47% |
| Sharpe | -1.775 | -1.702 | -1.630 |
| Calmar | -0.992 | -0.993 | -0.995 |
| Sortino | -1.136 | -1.092 | -1.050 |
| Trade Count | 114 | 114 | 114 |
| Win Rate | 50.0% | 50.0% | 50.0% |
| Avg Hold (hours) | 7.7 | 7.7 | 7.7 |
| Median Hold (hours) | 4 | 4 | 4 |
| Avg Win | 0.85% | 1.70% | 2.55% |
| Avg Loss | -1.27% | -2.55% | -3.82% |
| Profit Factor | 0.668 | 0.668 | 0.668 |
| Total Return | -11.41% | -21.83% | -31.29% |

## Correlation with Trend-Following

| Metric | 1x | 2x | 3x |
|--------|-----|-----|-----|
| Pearson r | 0.2765 | 0.2768 | 0.2771 |
| Spearman r | 0.1788 | 0.1789 | 0.1789 |
| Rolling 90d avg | 0.2681 | 0.2684 | 0.2686 |
| Rolling 90d max | 0.8194 | 0.8187 | 0.8180 |
| Rolling 90d min | -0.2395 | -0.2385 | -0.2376 |
| Overlapping days | 2267 | 2267 | 2267 |

Correlation with trend-following is 0.277 (PASS < 0.3 threshold), confirming the strategies trade different market conditions. However, the rolling 90d correlation swings from -0.24 to +0.82, indicating regime-dependent correlation.

## Monthly Returns (1x Leverage)

| Year | Jan | Feb | Mar | Apr | May | Jun | Jul | Aug | Sep | Oct | Nov | Dec | Annual |
|------|------|------|------|------|------|------|------|------|------|------|------|------|------|
| 2020 | - | -1.0% | -3.4% | -4.2% | -0.3% | -1.3% | -0.0% | -2.0% | -2.9% | 0.5% | -0.5% | 1.6% | - |
| 2021 | 1.7% | -1.2% | -2.0% | 1.2% | -4.6% | -5.0% | -2.4% | 3.2% | -7.9% | -3.4% | -3.0% | -0.9% | - |
| 2022 | 1.1% | -12.7% | -6.4% | -2.7% | 0.8% | -0.4% | -2.5% | -2.9% | -6.1% | -1.0% | -8.3% | -1.8% | - |
| 2023 | 0.3% | -1.5% | -3.9% | -2.0% | -2.0% | -4.6% | -0.6% | -0.6% | -1.8% | 0.4% | 3.4% | -0.4% | - |
| 2024 | -3.0% | 0.0% | 1.1% | 0.1% | -3.4% | -1.3% | -1.6% | -5.1% | -0.8% | 0.4% | 1.1% | 0.7% | - |
| 2025 | -2.1% | 1.9% | -0.1% | 0.4% | -0.6% | 0.1% | 0.4% | -1.6% | -2.7% | -2.2% | -1.3% | -0.5% | - |
| 2026 | -2.0% | -0.5% | -0.4% | - | - | - | - | - | - | - | - | - | - |

## Monthly Returns (2x Leverage)

| Year | Jan | Feb | Mar | Apr | May | Jun | Jul | Aug | Sep | Oct | Nov | Dec | Annual |
|------|------|------|------|------|------|------|------|------|------|------|------|------|------|
| 2020 | - | -2.0% | -7.1% | -8.3% | -0.7% | -2.7% | -0.1% | -4.1% | -5.8% | 0.9% | -1.1% | 3.1% | - |
| 2021 | 2.6% | -2.7% | -4.0% | 2.3% | -9.3% | -9.9% | -4.8% | 6.4% | -15.3% | -6.9% | -6.0% | -1.9% | - |
| 2022 | 2.3% | -24.0% | -12.5% | -5.4% | 1.5% | -0.8% | -5.0% | -5.7% | -11.9% | -2.0% | -16.0% | -3.5% | - |
| 2023 | 0.6% | -3.0% | -7.9% | -4.1% | -4.0% | -9.0% | -1.1% | -1.2% | -3.5% | 0.9% | 7.0% | -0.8% | - |
| 2024 | -6.0% | 0.0% | 2.1% | 0.0% | -6.8% | -2.7% | -3.2% | -10.1% | -1.7% | 0.8% | 2.2% | 1.3% | - |
| 2025 | -4.1% | 3.8% | -0.3% | 0.9% | -1.1% | 0.1% | 0.8% | -3.2% | -5.3% | -4.3% | -2.6% | -1.1% | - |
| 2026 | -4.0% | -1.0% | -0.8% | - | - | - | - | - | - | - | - | - | - |

## Root Cause Analysis

### Why the Strategy Loses Money

The strategy has a **fundamental negative expectation** at the specified parameters:

| Factor | Value | Impact |
|--------|-------|--------|
| TP:SL ratio | 1.5 : 2.0 (= 0.75) | Reward < Risk per trade |
| Break-even win rate | 57.1% | Need 57%+ to overcome asymmetry |
| Actual win rate | 51.2% | Below break-even |
| Empirical TP hit rate | ~48% | Slightly below 50/50 |
| Cost per trade | 14 bps RT on 50% equity | 694 trades = ~49% cumulative drag |

**The asymmetric TP/SL (1.5x vs 2.0x ATR) requires a win rate of 57.1% to break even (before costs), but the empirical hit rate is only ~48-51%.** Mean-reversion on BTC does not produce a high enough win rate with these wide bands to overcome the unfavorable risk-reward ratio.

### Signal Quality

| Condition | Frequency |
|-----------|-----------|
| RSI < 30 | 4.7% of bars |
| RSI > 70 | 6.3% of bars |
| Within 1 ATR of lower BB | 29.7% of bars |
| Within 1 ATR of upper BB | 31.9% of bars |
| EMA(168) > EMA(720) uptrend | 54.6% of bars |
| Combined long signal | 891 bars |
| Combined short signal | 885 bars |
| Trades executed | 694 (limited by one-at-a-time) |

The "within 1 ATR of BB" condition is quite permissive (30% of bars qualify), so the effective filter is primarily RSI + momentum direction. The momentum filter (EMA cross) successfully reduces the number of counter-trend trades but does not improve the win rate enough.

### Why Mean-Reversion is Difficult on BTC

1. **BTC is strongly trending**: EMA(168) > EMA(720) for 55% of the period. In trending markets, "oversold" bounces are shallow and "overbought" corrections are also shallow. The 1.5x ATR TP target is too ambitious for a pullback bounce.
2. **ATR-based targets are volatile**: BTC's ATR varies wildly (from ~$200 in 2020 to ~$5000+ in 2024). The absolute dollar TP/SL adjusts, but the proportional moves required (1.5-2.0 ATR) remain challenging.
3. **High-frequency cost drag**: 694 trades at 14bps RT on 50% equity creates ~49% cumulative cost drag. Mean-reversion strategies with short hold times are especially sensitive to transaction costs.

### Potential Improvements (Not Tested)

If the specification allowed modifications:
- **Reverse the TP/SL**: TP at 2.0x ATR, SL at 1.5x ATR (favorable risk-reward, lower win rate acceptable)
- **Tighter entry**: RSI < 20 / > 80 (stronger oversold/overbought, higher signal quality)
- **Reduce position size**: 25% equity to lower cost drag
- **BB proximity**: Price below lower BB (not "within 1 ATR") for stricter entry

## Summary & Verdict

- **Trade count**: 694 trades over full period (PASS: need >50)
- **Correlation with trend**: Pearson r = 0.277 (PASS: need < 0.3)
- **Sharpe (1x, full)**: -1.781 (FAIL)
- **Sharpe (1x, 12m)**: -1.775 (FAIL)
- **Annual Return (1x, full)**: -17.63% (FAIL)
- **Annual Return (1x, 12m)**: -11.42% (FAIL)
- **Win Rate**: 51.2% (below 57.1% break-even for 1.5:2.0 TP/SL ratio)
- **Best leverage**: 1x (all leverage levels are negative; higher leverage amplifies losses)

**VERDICT: KILLED** -- The strategy has negative expected value per trade due to the asymmetric TP:SL ratio (1.5:2.0) combined with insufficient win rate (~51% vs 57% required). The correlation metric passes (0.277 < 0.3), confirming mean-reversion trades different conditions than trend-following, but the strategy itself is unprofitable at these parameters. Not suitable as a portfolio component in its current form.
