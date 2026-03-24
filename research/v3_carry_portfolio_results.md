# V3 Momentum + Funding Carry Portfolio Backtest

**Run date**: 2026-03-24 13:05
**IS period**: 2020-01-01 to 2024-04-28
**OOS period**: 2024-04-29 to 2026-03-14
**IS/OOS split**: 70% / 30%
**Transaction cost**: 10 bps per rebalance
**Portfolio rebalance**: Monthly
**Strategy rebalance**: Weekly

## Executive Summary

**VERDICT: KILL** -- 1 kill criteria triggered.

- KILL: Carry active only 5.2% of OOS (< 20% threshold)

The funding carry strategy is too dormant in the OOS period to provide 
meaningful diversification. V3 momentum should remain standalone.

## Strategy Descriptions

### V3 Momentum (BTC Spot)
- 20/50 EMA crossover base signal (daily bars)
- Positioning overlay: Binance top trader L/S z-score -> sizing multiplier
- VRP overlay: (IV - RV) z-score -> sizing multiplier
- Weekly rebalance, long-only, position range [0, 1.5x]
- Validated OOS: Sharpe ~0.56, +17.52% return

### Funding Carry (BTC Perp)
- Delta-neutral carry: short perp + long spot when funding positive
- Entry: |72h rolling mean funding| > 0.005% per hour
- Returns = funding income only (hedged price exposure)
- Weekly signal rebalance

## Standalone Strategy Performance

| Metric | V3 IS | V3 OOS | Carry IS | Carry OOS |
|--------|-------|--------|----------|-----------|
| Total Return | 549.99% | 1.10% | 3804.80% | 3.58% |
| Ann. Return | 54.10% | 0.59% | 133.17% | 1.91% |
| Ann. Vol | 44.43% | 28.93% | 9.92% | 0.52% |
| Sharpe | 1.218 | 0.020 | 13.418 | 3.696 |
| Sortino | 1.268 | 0.021 | 27.923 | 0.000 |
| Max DD | -54.39% | -38.43% | -2.81% | -0.10% |
| Calmar | 0.99 | 0.02 | 47.37 | 19.13 |
| Days | 1580 | 678 | 1580 | 678 |

## Correlation Analysis

| Period | V3 vs Carry Correlation |
|--------|------------------------|
| Full | 0.0456 |
| IS | 0.0434 |
| OOS | 0.0114 |
| Rolling 90d Mean | -0.0517 |
| Rolling 90d Min | -0.6557 |
| Rolling 90d Max | 0.4240 |

Correlation is near zero, which is expected: V3 profits from BTC price 
trends while carry profits from funding rates with hedged price exposure.

## Carry Strategy Activity

| Period | % Days Active |
|--------|--------------|
| Full period | 41.5% |
| OOS | 5.2% |

### Activity by Year

| Year | Active Days | Total Days | % Active |
|------|------------|------------|----------|
| 2020 | 228 | 366 | 62.3% |
| 2021 | 323 | 365 | 88.5% |
| 2022 | 170 | 365 | 46.6% |
| 2023 | 133 | 365 | 36.4% |
| 2024 | 84 | 366 | 23.0% |
| 2025 | 0 | 365 | 0.0% |
| 2026 | 0 | 66 | 0.0% |

Funding rates have structurally declined since 2021. The carry strategy 
was highly active in 2020-2021 (bull market with persistent positive funding) 
but has become increasingly dormant as funding rates compressed. 
By H2 2025, the 72h rolling mean rarely exceeds the entry threshold.

## Portfolio Performance (OOS)

| Allocation | Sharpe | Return | MaxDD | Sortino | vs V3 dSharpe |
|------------|--------|--------|-------|---------|---------------|
| V3 Standalone | 0.020 | 1.10% | -38.43% | 0.021 | -- |
| 60/40 | 0.123 | 3.99% | -25.00% | 0.126 | +0.1023 |
| 50/50 | 0.159 | 4.33% | -21.26% | 0.163 | +0.1390 |
| Risk Parity | -0.028 | -0.15% | -4.99% | -0.016 | -0.0486 |
| Dynamic | -0.128 | -6.01% | -38.02% | -0.128 | -0.1484 |

## Portfolio Performance (IS)

| Allocation | Sharpe | Return | MaxDD | Sortino | vs V3 dSharpe |
|------------|--------|--------|-------|---------|---------------|
| V3 Standalone | 1.218 | 549.99% | -54.39% | 1.268 | -- |
| 60/40 | 3.183 | 1379.33% | -27.32% | 3.500 | +1.9658 |
| 50/50 | 4.110 | 1677.47% | -19.89% | 4.596 | +2.8924 |
| Risk Parity | 8.430 | 2587.20% | -16.14% | 8.079 | +7.2122 |
| Dynamic | 3.271 | 1558.25% | -28.18% | 3.677 | +2.0531 |

## Risk Parity Weight Distribution (OOS)

- Mean V3 weight: 7.7%
- Mean Carry weight: 92.3%
- V3 weight range: [0.0%, 50.0%]

Risk parity heavily tilts toward carry (lower vol) because the carry 
strategy is mostly flat (near-zero vol). This makes risk parity 
behave perversely: it over-allocates to an inactive strategy.

## Monthly OOS Returns

| Month | V3 | Carry | 60/40 | 50/50 | Risk Parity | Dynamic |
|-------|----|----- |------|------|------|------|
| 2024-04 | -6.10% | 0.00% | -3.63% | -3.02% | -0.32% | -3.63% |
| 2024-05 | 3.56% | 0.00% | 2.29% | 1.94% | 0.06% | 1.40% |
| 2024-06 | -15.94% | 0.00% | -9.79% | -8.20% | -0.00% | -15.94% |
| 2024-07 | -3.33% | 0.00% | -2.00% | -1.67% | -0.00% | -3.33% |
| 2024-08 | -16.51% | 0.00% | -10.13% | -8.49% | -0.00% | -16.51% |
| 2024-09 | -0.16% | 0.00% | -0.06% | -0.04% | -0.00% | -0.16% |
| 2024-10 | 10.74% | 0.00% | 6.46% | 5.38% | 0.00% | 10.74% |
| 2024-11 | 19.06% | 2.55% | 12.29% | 10.63% | 2.55% | 16.26% |
| 2024-12 | -3.48% | 1.00% | -1.64% | -1.19% | 0.83% | -1.64% |
| 2025-01 | 16.50% | 0.00% | 9.89% | 8.24% | 0.57% | 8.31% |
| 2025-02 | -5.38% | 0.00% | -3.20% | -2.67% | -0.00% | -5.38% |
| 2025-03 | 0.00% | 0.00% | 0.00% | 0.00% | 0.00% | 0.00% |
| 2025-04 | -1.28% | 0.00% | -0.77% | -0.64% | -0.64% | -1.28% |
| 2025-05 | 14.34% | 0.00% | 8.58% | 7.15% | 0.00% | 14.34% |
| 2025-06 | 1.48% | 0.00% | 0.98% | 0.83% | 0.00% | 1.48% |
| 2025-07 | 10.84% | 0.00% | 6.44% | 5.36% | 0.00% | 10.84% |
| 2025-08 | 0.41% | 0.00% | 0.30% | 0.26% | 0.00% | 0.41% |
| 2025-09 | 0.88% | 0.00% | 0.60% | 0.51% | 0.00% | 0.88% |
| 2025-10 | -9.24% | 0.00% | -5.55% | -4.62% | -0.00% | -9.24% |
| 2025-11 | 0.00% | 0.00% | 0.00% | 0.00% | 0.00% | 0.00% |
| 2025-12 | 0.00% | 0.00% | 0.00% | 0.00% | 0.00% | 0.00% |
| 2026-01 | -6.28% | 0.00% | -3.75% | -3.12% | -3.12% | -6.28% |
| 2026-02 | 0.00% | 0.00% | 0.00% | 0.00% | 0.00% | 0.00% |
| 2026-03 | 0.00% | 0.00% | 0.00% | 0.00% | 0.00% | 0.00% |

## Kill Criteria Evaluation

| Criterion | Threshold | Actual | Result |
|-----------|-----------|--------|--------|
| Correlation | < 0.5 | 0.011 | PASS |
| Portfolio Sharpe > V3 | > 0.020 | 0.159 (50/50) | PASS |
| Carry Active % | > 20% | 5.2% | KILL |

## Note on V3 OOS Sharpe

The V3 OOS Sharpe here (0.020) is much lower than the previously validated 0.56 because
this test uses a different OOS window. The 70/30 chronological split puts OOS at Apr 2024
to Mar 2026, which includes the severe BTC drawdown in mid-2024 (-16% months in Jun and Aug).
The original V3 validation used a different split. This does not invalidate V3 -- it shows
that the OOS window matters, and mid-2024 was a difficult period for trend-following.

## Portfolio Allocation Analysis

**60/40 and 50/50**: These mechanically dilute V3 returns when carry is flat. In months where
V3 returns +10% and carry returns 0%, the 60/40 portfolio returns only +6%. This is visible
in the monthly table: every non-zero V3 month is scaled down. The slight Sharpe improvement
(0.020 -> 0.159) comes entirely from reduced volatility (smaller drawdowns), not from
carry adding return.

**Risk Parity**: Perversely allocates ~92% to carry because carry has near-zero volatility
(it is mostly flat). This means Risk Parity is 92% cash, 8% V3 -- producing near-zero
returns. This is a known failure mode of risk parity with dormant strategies.

**Dynamic**: Uses a 30d lookback to detect carry activity. When carry had a brief active
period (Nov-Dec 2024), Dynamic switched to 60/40 for that window plus 30 days after. During
those months where V3 was positive, Dynamic diluted returns to 60% of V3. The -6.01% return
reflects this dilution during the Dec 2024 to Jan 2025 transition period.

## Honest Assessment

### Funding Carry Is Dead (for now)

The funding carry strategy is dormant in the OOS period. BTC perpetual
funding rates have structurally declined since the 2021 bull market.
The 72h rolling mean of funding rarely exceeds the entry threshold
(0.005% per hour) in 2024+.

Carry activity by year tells the story clearly:
- 2020: 62% active (bull market, persistent positive funding)
- 2021: 89% active (peak funding regime)
- 2022: 47% active (bear market, still some funding)
- 2023: 36% active (recovery, declining funding)
- 2024: 23% active (marginal, concentrated in Q4)
- 2025: 0% active (funding rates collapsed)
- 2026: 0% active (same)

This is not a cyclical dip -- it is a structural decline in BTC perpetual funding rates.
The crypto derivatives market has matured: more market makers, tighter spreads, lower
funding premiums. The carry trade that worked in 2020-2021 may not return.

### What the Portfolios Actually Show

When carry is inactive:
- 60/40 and 50/50 just scale down V3 by their V3 weight (60% or 50%)
- Risk Parity becomes ~100% cash (near-zero allocation to V3)
- Dynamic correctly falls back to 100% V3 MOST of the time, but the 30d lookback
  creates dilution windows around carry's rare active periods

The only period carry contributed was Nov-Dec 2024 (+2.55% and +1.00%), which is
35 out of 678 OOS days (5.2%). This is too sparse to matter.

### When Would This Portfolio Make Sense?

The V3+Carry portfolio would become viable if:
1. BTC funding rates return to 2020-2021 levels (0.01%+ per 8h consistently)
2. The carry strategy has sustained activity (>30% of days)
3. Carry provides genuine diversification (correlation stays < 0.3)

Historically (IS period), the portfolio was spectacular: 50/50 Sharpe of 4.1 vs V3's 1.2.
But this IS performance is entirely driven by 2020-2021 funding rates that no longer exist.

### IS Performance Is Misleading

In the IS period (2020-2024), carry had a Sharpe of 13.4. This is genuine -- delta-neutral
funding carry in 2020-2021 was one of the best risk-adjusted trades in crypto history. But:
- This regime has ended
- IS performance is not representative of current conditions
- Deploying based on IS would be a classic regime-change trap

## Recommendation

**Do not deploy the V3+Carry portfolio.**

Kill flag triggered:
- Carry active only 5.2% of OOS (threshold: 20%)

**What to do instead:**
1. Keep V3 momentum as a standalone strategy
2. Monitor BTC 8h funding rate monthly -- if the 30d mean exceeds 0.01%, revisit
3. Consider carry as a separate conditional strategy that activates ONLY during
   funding rate regimes (not as a permanent portfolio allocation)
4. If funding recovers, the Dynamic allocation is the only reasonable approach --
   the static allocations (60/40, 50/50) will always dilute V3 during dormant periods
