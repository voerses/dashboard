# Cost-Adjusted Signal Survival Test

**Run date:** 2026-03-24 10:58

**OOS split:** train < 2025-01-01, test >= 2025-01-01

**Cost assumption:** BTC = 10 bps round-trip (5 bps per side)

## Executive Summary

Of 10 signal-horizon combinations tested:
- **SURVIVES (robust):** 6 (positive net alpha, break-even > 50 bps)
- **SURVIVES (fragile):** 0 (positive net alpha, break-even <= 50 bps)
- **MARGINAL:** 3 (adjusted spread nonzero but net annual alpha negative)
- **KILLED BY COSTS:** 0
- **WEAK SIGNAL:** 1 (|IC| < 0.05)

**Key insight:** Spearman IC is a rank-based metric and invariant to constant cost shifts -- subtracting 10 bps from all returns does not change rank ordering. The real cost impact shows up in:
1. **Quintile L/S spread** after costs (direct P&L impact)
2. **Annual cost drag** from signal turnover (rebalancing frequency)
3. **Net annual alpha** = annualized spread - annual cost drag

## OOS Results Summary

| Signal | Hz | IC | t-stat | Raw Spread | Adj Spread | BE (bps) | Turn/day | Drag/yr | Net Alpha/yr | Verdict |
|--------|----|----|--------|------------|------------|----------|----------|---------|--------------|--------|
| US10Y+DXY Regime | 7d | -0.0724 | -1.51 | +1.21% | +1.01% | 60 | 0.245 | +8.96% | +54.02% | **SURVIVES (robust)** |
| US10Y+DXY Regime | 14d | -0.1350 | -2.81 | +2.81% | +2.61% | 140 | 0.245 | +8.96% | +64.20% | **SURVIVES (robust)** |
| TopTrader L/S Raw | 7d | -0.1683 | -3.55 | +2.45% | +2.25% | 122 | 0.266 | +9.71% | +117.85% | **SURVIVES (robust)** |
| TopTrader L/S Raw | 14d | -0.2042 | -4.30 | +4.62% | +4.42% | 231 | 0.266 | +9.71% | +110.86% | **SURVIVES (robust)** |
| L/S Divergence | 7d | -0.2056 | -4.37 | +4.29% | +4.09% | 215 | 0.505 | +18.42% | +205.46% | **SURVIVES (robust)** |
| L/S Divergence | 14d | -0.2303 | -4.88 | +5.82% | +5.62% | 291 | 0.505 | +18.42% | +133.23% | **SURVIVES (robust)** |
| Skew_30d | 7d | +0.0542 | +1.13 | -1.36% | -1.56% | 68 | 0.384 | +14.02% | -84.79% | **MARGINAL** |
| Skew_30d | 14d | +0.0735 | +1.52 | -1.98% | -2.18% | 99 | 0.384 | +14.02% | -65.72% | **MARGINAL** |
| Net Taker Vol Z | 7d | +0.0891 | +1.39 | -1.24% | -1.44% | 62 | 0.680 | +24.82% | -89.59% | **MARGINAL** |
| Net Taker Vol Z | 14d | +0.0226 | +0.35 | +0.06% | -0.14% | 3 | 0.680 | +24.82% | -23.36% | **WEAK SIGNAL** |

## IS vs OOS Comparison

| Signal | Horizon | IS IC | OOS IC | IS Spread | OOS Spread | IS Net Alpha | OOS Net Alpha |
|--------|---------|-------|--------|-----------|------------|-------------|---------------|
| US10Y+DXY Regime | 7d | +0.0036 | -0.0724 | -1.08% | +1.21% | -63.21% | +54.02% |
| US10Y+DXY Regime | 14d | +0.0697 | -0.1350 | -3.67% | +2.81% | -102.60% | +64.20% |
| TopTrader L/S Raw | 7d | -0.0721 | -0.1683 | +2.27% | +2.45% | +109.32% | +117.85% |
| TopTrader L/S Raw | 14d | -0.0994 | -0.2042 | +4.93% | +4.62% | +119.79% | +110.86% |
| L/S Divergence | 7d | -0.0802 | -0.2056 | +3.14% | +4.29% | +145.91% | +205.46% |
| L/S Divergence | 14d | -0.1187 | -0.2303 | +6.54% | +5.82% | +152.52% | +133.23% |
| Skew_30d | 7d | +0.0645 | +0.0542 | -1.33% | -1.36% | -79.04% | -84.79% |
| Skew_30d | 14d | +0.0422 | +0.0735 | -1.19% | -1.98% | -41.05% | -65.72% |
| Net Taker Vol Z | 7d | N/A | +0.0891 | N/A | -1.24% | N/A | -89.59% |
| Net Taker Vol Z | 14d | N/A | +0.0226 | N/A | +0.06% | N/A | -23.36% |

## Quintile Spread After Various Cost Levels (OOS, 14d)

Adjusted L/S quintile spread = raw spread - 2 * round_trip_cost:

| Signal | 0bps | 5bps | 10bps | 15bps | 20bps | 30bps | 50bps | 75bps | 100bps |
|--------|--------|--------|--------|--------|--------|--------|--------|--------|--------|
| US10Y+DXY Regime | +2.81% | +2.71% | +2.61% | +2.51% | +2.41% | +2.21% | +1.81% | +1.31% | +0.81% |
| TopTrader L/S Raw | +4.62% | +4.52% | +4.42% | +4.32% | +4.22% | +4.02% | +3.62% | +3.12% | +2.62% |
| L/S Divergence | +5.82% | +5.72% | +5.62% | +5.52% | +5.42% | +5.22% | +4.82% | +4.32% | +3.82% |
| Skew_30d | -1.98% | -2.08% | -2.18% | -2.28% | -2.38% | -2.58% | -2.98% | -3.48% | -3.98% |
| Net Taker Vol Z | +0.06% | -0.04% | -0.14% | -0.24% | -0.34% | -0.54% | -0.94% | -1.44% | -1.94% |

## Signal Turnover & Annual Cost Drag (OOS)

| Signal | Turnover/day | Trades/yr | Avg Holding (days) | Annual Drag | 14d Raw Spread | Net Alpha/yr |
|--------|-------------|-----------|--------------------|-------------|----------------|-------------|
| US10Y+DXY Regime | 0.245 | 90 | 4.1 | 8.96% | +2.81% | +64.20% |
| TopTrader L/S Raw | 0.266 | 97 | 3.8 | 9.71% | +4.62% | +110.86% |
| L/S Divergence | 0.505 | 184 | 2.0 | 18.42% | +5.82% | +133.23% |
| Skew_30d | 0.384 | 140 | 2.6 | 14.02% | -1.98% | -65.72% |
| Net Taker Vol Z | 0.680 | 248 | 1.5 | 24.82% | +0.06% | -23.36% |

## Detailed Signal Analysis

### US10Y+DXY Regime

**IS Period:**
- Turnover: 0.189/day (avg holding: 5.3 days, annual cost drag: 6.90%)
- **7d** (n=1767):
  - IC: +0.0036 (t=+0.15)
  - Raw L/S spread: -1.08%, after costs: -1.28%
  - Break-even cost: 54 bps round-trip
  - Net annual alpha: -63.21%
- **14d** (n=1767):
  - IC: +0.0697 (t=+2.94)
  - Raw L/S spread: -3.67%, after costs: -3.87%
  - Break-even cost: 184 bps round-trip
  - Net annual alpha: -102.60%

**OOS Period:**
- Turnover: 0.245/day (avg holding: 4.1 days, annual cost drag: 8.96%)
- **7d** (n=434):
  - IC: -0.0724 (t=-1.51)
  - Raw L/S spread: +1.21%, after costs: +1.01%
  - Break-even cost: 60 bps round-trip
  - Net annual alpha: +54.02%
- **14d** (n=427):
  - IC: -0.1350 (t=-2.81)
  - Raw L/S spread: +2.81%, after costs: +2.61%
  - Break-even cost: 140 bps round-trip
  - Net annual alpha: +64.20%

### TopTrader L/S Raw

**IS Period:**
- Turnover: 0.242/day (avg holding: 4.1 days, annual cost drag: 8.84%)
- **7d** (n=1268):
  - IC: -0.0721 (t=-2.57)
  - Raw L/S spread: +2.27%, after costs: +2.07%
  - Break-even cost: 113 bps round-trip
  - Net annual alpha: +109.32%
- **14d** (n=1268):
  - IC: -0.0994 (t=-3.55)
  - Raw L/S spread: +4.93%, after costs: +4.73%
  - Break-even cost: 247 bps round-trip
  - Net annual alpha: +119.79%

**OOS Period:**
- Turnover: 0.266/day (avg holding: 3.8 days, annual cost drag: 9.71%)
- **7d** (n=434):
  - IC: -0.1683 (t=-3.55)
  - Raw L/S spread: +2.45%, after costs: +2.25%
  - Break-even cost: 122 bps round-trip
  - Net annual alpha: +117.85%
- **14d** (n=427):
  - IC: -0.2042 (t=-4.30)
  - Raw L/S spread: +4.62%, after costs: +4.42%
  - Break-even cost: 231 bps round-trip
  - Net annual alpha: +110.86%

### L/S Divergence

**IS Period:**
- Turnover: 0.490/day (avg holding: 2.0 days, annual cost drag: 17.89%)
- **7d** (n=1268):
  - IC: -0.0802 (t=-2.86)
  - Raw L/S spread: +3.14%, after costs: +2.94%
  - Break-even cost: 157 bps round-trip
  - Net annual alpha: +145.91%
- **14d** (n=1268):
  - IC: -0.1187 (t=-4.25)
  - Raw L/S spread: +6.54%, after costs: +6.34%
  - Break-even cost: 327 bps round-trip
  - Net annual alpha: +152.52%

**OOS Period:**
- Turnover: 0.505/day (avg holding: 2.0 days, annual cost drag: 18.42%)
- **7d** (n=434):
  - IC: -0.2056 (t=-4.37)
  - Raw L/S spread: +4.29%, after costs: +4.09%
  - Break-even cost: 215 bps round-trip
  - Net annual alpha: +205.46%
- **14d** (n=427):
  - IC: -0.2303 (t=-4.88)
  - Raw L/S spread: +5.82%, after costs: +5.62%
  - Break-even cost: 291 bps round-trip
  - Net annual alpha: +133.23%

### Skew_30d

**IS Period:**
- Turnover: 0.272/day (avg holding: 3.7 days, annual cost drag: 9.92%)
- **7d** (n=1797):
  - IC: +0.0645 (t=+2.74)
  - Raw L/S spread: -1.33%, after costs: -1.53%
  - Break-even cost: 66 bps round-trip
  - Net annual alpha: -79.04%
- **14d** (n=1797):
  - IC: +0.0422 (t=+1.79)
  - Raw L/S spread: -1.19%, after costs: -1.39%
  - Break-even cost: 60 bps round-trip
  - Net annual alpha: -41.05%

**OOS Period:**
- Turnover: 0.384/day (avg holding: 2.6 days, annual cost drag: 14.02%)
- **7d** (n=434):
  - IC: +0.0542 (t=+1.13)
  - Raw L/S spread: -1.36%, after costs: -1.56%
  - Break-even cost: 68 bps round-trip
  - Net annual alpha: -84.79%
- **14d** (n=427):
  - IC: +0.0735 (t=+1.52)
  - Raw L/S spread: -1.98%, after costs: -2.18%
  - Break-even cost: 99 bps round-trip
  - Net annual alpha: -65.72%

### Net Taker Vol Z

**IS Period:**
- Turnover: N/A
- **7d** (n=0):
  - INSUFFICIENT DATA
- **14d** (n=0):
  - INSUFFICIENT DATA

**OOS Period:**
- Turnover: 0.680/day (avg holding: 1.5 days, annual cost drag: 24.82%)
- **7d** (n=244):
  - IC: +0.0891 (t=+1.39)
  - Raw L/S spread: -1.24%, after costs: -1.44%
  - Break-even cost: 62 bps round-trip
  - Net annual alpha: -89.59%
- **14d** (n=237):
  - IC: +0.0226 (t=+0.35)
  - Raw L/S spread: +0.06%, after costs: -0.14%
  - Break-even cost: 3 bps round-trip
  - Net annual alpha: -23.36%

## Methodology

### Cost Model
- **Round-trip cost:** Entry + exit fees
  - BTC/ETH: 5 bps per side = 10 bps round-trip
  - Alts: 15 bps per side = 30 bps round-trip

### Key Metrics

| Metric | Definition | Why it matters |
|--------|------------|----------------|
| Spearman IC | Rank correlation of signal vs forward return | Measures predictive power; invariant to constant cost shifts |
| Raw Q1-Q5 Spread | Mean return of bottom quintile minus top quintile | The gross edge before costs |
| Adjusted Spread | Raw spread - 2 * round_trip_cost | Net edge per trade after paying entry+exit on both legs |
| Break-even Cost | Round-trip cost at which spread = 0 | Robustness measure: higher = more room for slippage |
| Signal Turnover | Fraction of days with quintile change | Frequency of trading; directly drives cost drag |
| Annual Cost Drag | turnover_rate * 365 * round_trip_cost | Total annual cost from rebalancing |
| Net Annual Alpha | Annualized spread - annual cost drag | Bottom line: does the signal make money after costs? |

### Why IC Does Not Change With Costs

Spearman IC measures rank correlation. Subtracting a constant (trading cost) from all returns shifts every observation equally and does **not** change the rank ordering. Therefore, Spearman IC is identical whether computed on raw or cost-adjusted returns.

The real cost impact is on **P&L**, not **predictability**. A signal can perfectly predict return ranks (IC=1.0) but still lose money if the spread between quintiles is smaller than trading costs.

### Survival Criteria

- **SURVIVES (robust):** |IC| >= 0.05, adjusted spread > 0, net annual alpha > 0, break-even > 50 bps
- **SURVIVES (fragile):** Same but break-even <= 50 bps
- **MARGINAL:** Adjusted spread nonzero but net annual alpha <= 0
- **KILLED BY COSTS:** Adjusted spread <= 0
- **WEAK SIGNAL:** |IC| < 0.05 regardless of costs
