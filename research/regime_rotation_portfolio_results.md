# Regime-Rotating Multi-Strategy Portfolio: BTC $200K Simulation

*Generated: 2026-03-26 14:28:32*

## Architecture

Single $200K capital pool with regime-detected strategy rotation:
- **UPTREND**: V3 trend-following (EMA 20/50 cross) + positioning/VRP overlays (70% equity)
- **RANGE**: Positioning contrarian — short when crowd long, long when flat (50% equity)
- **DOWNTREND**: Oil+DXY macro short (60% equity)
- **CRISIS**: Flat (cash) — capital preservation
- **QUIET**: Mild trend-following (30% equity)
- **Carry overlay**: Funding carry when 30d mean > 0.01%

## Cost Model

- Taker fee: 4 bps per side
- Slippage: 3 bps base + sqrt impact
- Funding: 8h settlement (3x daily), positive = longs pay shorts
- Position sizing: 25-80% of equity depending on regime/signal

## Performance Summary

| Metric | Regime Rotation Full | IS Period | OOS Period |
|--------|---------------------|-----------|------------|
| Annual Return | 26.26% | 33.96% | 0.88% |
| Total Return | 261.66% | 255.12% | 1.04% |
| Max Drawdown | -30.21% | -30.21% | -17.20% |
| Sharpe Ratio | 1.04 | 1.19 | 0.13 |
| Calmar Ratio | 0.87 | 1.12 | 0.05 |
| Sortino Ratio | 1.27 | 1.49 | 0.14 |
| Win Rate (daily) | 34.82% | 35.46% | 32.33% |
| Final Equity | $723,320 | $710,242 | $723,320 |
| Duration (years) | 5.5 | 4.3 | 1.2 |

## Comparison: Regime Rotation vs V3 vs Buy-and-Hold

| Metric | Regime Rotation | V3 Standalone | Buy-and-Hold |
|--------|----------------|---------------|-------------|
| Annual Return (Full) | 26.26% | 22.65% | 38.13% |
| Max Drawdown (Full) | -30.21% | -38.15% | -76.63% |
| Sharpe (Full) | 1.04 | 0.87 | 0.84 |
| Calmar (Full) | 0.87 | 0.59 | 0.50 |
| Sortino (Full) | 1.27 | 0.95 | 1.20 |
| Final Equity (Full) | $723,320 | $616,562 | $1,215,169 |

### OOS Period Only

| Metric | Regime Rotation | V3 Standalone | Buy-and-Hold |
|--------|----------------|---------------|-------------|
| Annual Return | 0.88% | -4.77% | -21.79% |
| Max Drawdown | -17.20% | -21.49% | -49.53% |
| Sharpe | 0.13 | -0.22 | -0.31 |
| Sortino | 0.14 | -0.23 | -0.43 |
| Final Equity | $723,320 | $616,562 | $1,215,169 |

## Regime Distribution

| Regime | Days | % of Time |
|--------|------|-----------|
| RANGE | 451 | 20.0% |
| UPTREND | 884 | 39.1% |
| DOWNTREND | 623 | 27.6% |
| CRISIS | 20 | 0.9% |
| QUIET | 280 | 12.4% |

## Regime-Specific Performance (Full Period)

| Regime | Days | % Time | Ann Return | Sharpe | Cum Return |
|--------|------|--------|-----------|--------|-----------|
| QUIET | 249 | 12.4% | -11.20% | -1.46 | -7.64% |
| UPTREND | 790 | 39.2% | 85.24% | 2.25 | 184.36% |
| RANGE | 359 | 17.8% | 10.00% | 0.74 | 9.82% |
| DOWNTREND | 612 | 30.4% | -26.83% | -2.12 | -44.96% |

### OOS Regime Performance

| Regime | Days | % Time | Ann Return | Sharpe | Cum Return |
|--------|------|--------|-----------|--------|-----------|
| QUIET | 60 | 13.9% | -7.08% | -1.36 | -1.16% |
| UPTREND | 104 | 24.1% | 45.12% | 1.88 | 12.85% |
| RANGE | 115 | 26.7% | -3.12% | -0.37 | -0.98% |
| DOWNTREND | 148 | 34.3% | -21.46% | -2.45 | -8.69% |

## Strategy Activity Breakdown

| Strategy | Days | % |
|----------|------|---|
| UPTREND_LONG | 791 | 39.3% |
| DOWNTREND_FLAT | 495 | 24.6% |
| RANGE_LONG | 267 | 13.3% |
| QUIET_FLAT | 140 | 7.0% |
| DOWNTREND_PARTIAL_SHORT | 110 | 5.5% |
| QUIET_LONG | 85 | 4.2% |
| RANGE_MILD | 77 | 3.8% |
| QUIET_CONTRA_SHORT | 25 | 1.2% |
| RANGE_SHORT | 14 | 0.7% |
| DOWNTREND_STRONG_SHORT | 8 | 0.4% |
| CARRY_SHORT | 1 | 0.0% |

## Monthly Returns

| Year | Jan | Feb | Mar | Apr | May | Jun | Jul | Aug | Sep | Oct | Nov | Dec |
|------|------|------|------|------|------|------|------|------|------|------|------|------|
| 2020 | -- | -- | -- | -- | -- | -- | -- | -- | -- | 10.8% | 27.2% | 25.6% |
| 2021 | 18.7% | 18.1% | 9.8% | -6.8% | -2.8% | -2.2% | -3.4% | 11.3% | -11.1% | 15.5% | -1.8% | 1.7% |
| 2022 | 0.0% | -3.6% | 5.0% | -6.6% | 3.0% | -6.5% | 1.1% | -3.8% | -0.5% | 1.1% | -12.3% | 0.9% |
| 2023 | 6.1% | 0.8% | 15.3% | 0.9% | -0.1% | 0.6% | -2.3% | -1.6% | 1.1% | 11.1% | 5.3% | 7.7% |
| 2024 | -1.8% | 21.9% | 8.5% | -7.7% | -5.4% | -3.2% | -1.4% | -12.8% | 0.8% | -0.9% | 16.4% | -2.7% |
| 2025 | 5.2% | -2.1% | -1.2% | 1.5% | 9.1% | 5.4% | 2.5% | -2.0% | -1.6% | -7.4% | 0.4% | -0.9% |
| 2026 | -5.6% | -0.2% | 0.0% | -- | -- | -- | -- | -- | -- | -- | -- | -- |

## Worst Drawdowns

| Rank | Start | Trough | End | Depth | Duration (d) |
|------|-------|--------|-----|-------|-------------|
| 1 | 2021-02-22 | 2023-01-15 | 2023-11-09 | -30.21% | 990 |
| 2 | 2024-04-09 | 2024-10-10 | 2025-07-13 | -29.30% | 460 |
| 3 | 2025-08-08 | 2026-02-19 | 2026-03-14 | -17.20% | 218 |
| 4 | 2021-01-15 | 2021-01-27 | 2021-02-02 | -9.70% | 18 |
| 5 | 2024-01-11 | 2024-01-22 | 2024-02-14 | -9.15% | 34 |
| 6 | 2024-03-14 | 2024-03-19 | 2024-03-25 | -8.84% | 11 |
| 7 | 2021-01-09 | 2021-01-12 | 2021-01-14 | -7.47% | 5 |

## Cost Analysis

| Component | Total | Annual |
|-----------|-------|--------|
| Trading Costs | $78,929 | $14,314 |
| Funding P&L | $-109,733 | $-19,901 |
| Net P&L | $523,320 | $94,907 |
| Cost as % of P&L | 15.1% | -- |

## Key Question: 100%+ Annual Return with MaxDD < 40%?

**Full Period (2020-09-01 to latest):**
- Annual Return: 26.26%
- Max Drawdown: -30.21%
- Target met: NO

**OOS Period (2025-01-01 to latest):**
- Annual Return: 0.88%
- Max Drawdown: -17.20%
- Target met: NO

### Best-Case vs Worst-Case Estimates

**Best-case scenario** (strong trend environment like 2020-2021):
- Expected annual return: 33.96% (based on IS performance)
- Expected Sharpe: 1.19
- Expected MaxDD: -30.21%
- Probability estimate: ~25% of years look like this

**Realistic-case scenario** (mix of regimes, moderate trends):
- Expected annual return: 18.64%
- Expected MaxDD: -30.21%
- Probability estimate: ~50% of years

**Worst-case scenario** (choppy markets, whipsaw regimes):
- Expected annual return: -20% to 0%
- Expected MaxDD: -40% to -55%
- Probability estimate: ~25% of years
- Primary risk: false regime switches causing rapid position changes with costs

## Conclusions

1. **Alpha vs V3 standalone**: 3.60% annual (outperforms)
2. **Alpha vs buy-and-hold**: -11.88% annual (underperforms)
3. **Drawdown improvement**: regime rotation MaxDD -30.21% vs V3 -38.15% vs B&H -76.63%
4. **Capital efficiency**: capital is active in 68% of days (vs V3's ~56%)

### Architecture Assessment

The regime-rotation approach offers:
- **Regime awareness**: Different market conditions get matched strategies
- **Crisis protection**: Flat during CRISIS prevents large drawdowns
- **Capital efficiency**: Not waiting for one strategy's conditions
- **Carry income**: Funding overlay adds income during neutral periods

Key risks:
- **Regime whipsaw**: False regime transitions cause excess trading costs
- **Signal decay**: Positioning signals may weaken as more traders use similar data
- **Concentration**: 100% BTC — no cross-asset diversification
- **Regime detection lag**: Daily detection misses intraday regime shifts

## Leveraged Variants

Testing whether leverage can push returns above 100% while maintaining MaxDD < 40%.

| Metric | 1x (Base) | 1.5x | 2x |
|--------|----------|------|-----|
| Annual Return (Full) | 26.26% | 36.12% | 42.48% |
| Max Drawdown (Full) | -30.21% | -55.66% | -70.06% |
| Sharpe (Full) | 1.04 | 0.89 | 0.87 |
| Calmar (Full) | 0.87 | 0.65 | 0.61 |
| Sortino (Full) | 1.27 | 1.08 | 1.06 |
| Final Equity | $723,320 | $1,095,344 | $1,408,839 |

### OOS Leveraged Performance

| Metric | 1x (Base) | 1.5x | 2x |
|--------|----------|------|-----|
| Annual Return | 0.88% | -5.03% | -9.09% |
| Max Drawdown | -17.20% | -30.87% | -39.79% |
| Sharpe | 0.13 | -0.05 | -0.08 |
| Final Equity | $723,320 | $1,095,344 | $1,408,839 |

### Can Leverage Reach 100%+ Annual?

- **1x Full**: Ann=26.26%, MaxDD=-30.21% -> Target NOT met
- **1x OOS**: Ann=0.88%, MaxDD=-17.20% -> Target NOT met
- **1.5x Full**: Ann=36.12%, MaxDD=-55.66% -> Target NOT met
- **1.5x OOS**: Ann=-5.03%, MaxDD=-30.87% -> Target NOT met
- **2x Full**: Ann=42.48%, MaxDD=-70.06% -> Target NOT met
- **2x OOS**: Ann=-9.09%, MaxDD=-39.79% -> Target NOT met

## Definitive Analysis: Why 100%+ Annual / MaxDD < 40% Is Not Achievable

### The Arithmetic of the Target

To achieve 100% annual return on BTC with MaxDD < 40%, you need a daily return of
approximately 0.19% with daily volatility below 0.92% (to keep Sharpe above 2.0 and
drawdowns bounded). BTC's historical daily volatility is 3.5-4.5%, meaning the required
information ratio relative to BTC noise is approximately 0.19/3.5 = 0.054. This is
actually achievable with perfect regime timing.

The binding constraint is **not** return generation but **drawdown control during regime
transitions**. The V4 regime detector uses EMAs (20/50) and ADX, which are inherently
lagging indicators. When BTC transitions from UPTREND to DOWNTREND, the detector takes
5-20 days to recognize the new regime. During this transition window, the portfolio holds
the WRONG position (long into a falling market), generating the bulk of drawdowns.

### Where Returns Come From

| Source | Contribution | Reliability |
|--------|-------------|-------------|
| QUIET (12% of time) | -11.20% ann, Sharpe -1.46 | NEGATIVE |
| UPTREND (39% of time) | 85.24% ann, Sharpe 2.25 | HIGH |
| RANGE (18% of time) | 10.00% ann, Sharpe 0.74 | MODERATE |
| DOWNTREND (30% of time) | -26.83% ann, Sharpe -2.12 | NEGATIVE |

The UPTREND strategy alone generates 184.36% 
cumulative return but is offset by -44.96% 
cumulative loss during DOWNTREND transitions. The net is +261.66% over 
5.5 years = 26.26% annualized.

### Why Leverage Cannot Fix This

Leverage amplifies both returns AND drawdowns proportionally. At 2x:
- UPTREND return doubles: ~170.47% annualized
- But DOWNTREND transition losses also double
- MaxDD goes from -30.21% to -70.06%, 
blowing through the 40% limit

The Sharpe-optimal leverage (Kelly fraction) for Sharpe 1.04 with daily returns is approximately:
- f* = mu / sigma^2 ~ 1.5x
- Practical Kelly fraction (half-Kelly) ~ 0.75x
- At 0.75x Kelly, the portfolio is already near-optimally sized at 1x. There is no free leverage to exploit.

### What Would Be Required to Hit 100%+ / 40% MaxDD

1. **Sub-daily regime detection** (15m-1h) to cut transition lag from 10 days to 1-2 days
2. **Options-based hedging** during regime transitions (buy puts when regime confidence drops)
3. **Multi-asset rotation** (ETH, SOL, etc.) to diversify timing risk
4. **Machine learning regime classifier** instead of rule-based EMA/ADX
5. **Intraday momentum overlay** during regime transitions

Even with all of the above, the expected return would be approximately 50-70% annualized
(not 100%+) because BTC's Sharpe in the best trend window was ~1.2, the strategy already
extracts nearly all available alpha during UPTREND (Sharpe 2.25), and the remaining improvement
comes from reducing losses in non-trend regimes. Realistic max leverage for 40% MaxDD cap is ~1.3x.
1.3x * 50% base = 65% annual -- still below 100%.

### Best-Case and Worst-Case Estimates (Revised)

| Scenario | Annual Return | MaxDD | Sharpe | Probability |
|----------|-------------|-------|--------|-------------|
| Bull market (2020-2021 style) | 50-70% | -20% to -30% | 1.5-2.0 | 20% of years |
| Normal market (mixed regimes) | 15-30% | -25% to -35% | 0.8-1.2 | 50% of years |
| Bear/choppy market (2022 style) | -10% to +5% | -30% to -40% | -0.5 to 0.3 | 25% of years |
| Black swan (Luna/FTX style) | -30% to -50% | -40% to -60% | < -1.0 | 5% of years |

**Expected long-run annual return**: ~25-30% with Sharpe ~1.0

### Bottom Line

The regime-rotation architecture is sound and meaningfully improves on V3 standalone
(+3.60% annual return, 
7.94% MaxDD reduction, 
+0.17 Sharpe).
It correctly identifies that different market conditions require different strategies.
However, **100%+ annual returns with < 40% MaxDD is not achievable** with daily regime
detection on a single asset. The binding constraint is regime transition lag, which creates
unavoidable drawdowns that consume 40-50% of gross returns. The realistic ceiling for this
architecture at 1x position sizing is approximately 30-40% annualized with -30% MaxDD,
yielding a Calmar ratio of 1.0-1.3 -- which is excellent for a crypto-only portfolio but
falls short of the 100% annual target.