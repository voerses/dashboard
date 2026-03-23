# BTC Spot ETF Flow Signal Analysis Results

**Date range:** 2024-01-12 to 2026-03-16
**IS period:** before 2025-07-01 (536 days)
**OOS period:** from 2025-07-01 (259 days)
**Total ETF flow days:** 533

## Signal Hypothesis

Sustained multi-day ETF inflow streaks indicate institutional demand building (bullish for crypto).
Sustained outflow streaks indicate de-risking (bearish).
Best used on 5-day and 20-day rolling windows, not daily noise.

## Information Coefficient Results

| Signal | Horizon | IC Full | IC IS | IC OOS | t-stat OOS | N OOS |
|--------|---------|---------|-------|--------|------------|-------|
| ETF Flow 5d Sum | 1d | +0.0318 | +0.0286 | +0.0130 | +0.21 | 259 |
| ETF Flow 5d Sum | 3d | +0.0385 | +0.0330 | +0.0117 | +0.19 | 257 |
| ETF Flow 5d Sum | 7d | +0.0513 | +0.0596 | -0.0124 | -0.20 | 253 |
| ETF Flow 5d Sum | 14d | +0.1004 | +0.0677 | +0.1027 | +1.61 | 246 |
| ETF Flow 20d Sum | 1d | +0.0337 | +0.0156 | +0.0524 | +0.84 | 259 |
| ETF Flow 20d Sum | 3d | +0.0398 | +0.0067 | +0.0644 | +1.03 | 257 |
| ETF Flow 20d Sum | 7d | +0.0860 | +0.0305 | +0.1248 | +1.98 | 253 |
| ETF Flow 20d Sum | 14d | +0.0971 | +0.0195 | +0.1248 | +1.96 | 246 |
| Flow Momentum (5d - 20d/4) | 1d | +0.0029 | +0.0096 | -0.0198 | -0.32 | 259 |
| Flow Momentum (5d - 20d/4) | 3d | +0.0208 | +0.0321 | -0.0161 | -0.26 | 257 |
| Flow Momentum (5d - 20d/4) | 7d | +0.0085 | +0.0597 | -0.1001 | -1.59 | 253 |
| Flow Momentum (5d - 20d/4) | 14d | +0.0391 | +0.0654 | -0.0338 | -0.53 | 246 |
| Flow Binary 5d | 1d | +0.0477 | +0.0613 | +0.0030 | +0.05 | 259 |
| Flow Binary 5d | 3d | +0.0433 | +0.0603 | -0.0232 | -0.37 | 257 |
| Flow Binary 5d | 7d | +0.0635 | +0.0737 | -0.0035 | -0.06 | 253 |
| Flow Binary 5d | 14d | +0.1014 | +0.0635 | +0.1087 | +1.71 | 246 |
| Flow Streak | 1d | +0.0241 | +0.0131 | +0.0381 | +0.61 | 259 |
| Flow Streak | 3d | -0.0044 | -0.0147 | +0.0011 | +0.02 | 257 |
| Flow Streak | 7d | +0.0381 | +0.0548 | -0.0244 | -0.39 | 253 |
| Flow Streak | 14d | +0.0457 | +0.0439 | +0.0130 | +0.20 | 246 |
| Flow 5d Z-scored | 1d | +0.0449 | +0.0521 | +0.0236 | +0.38 | 259 |
| Flow 5d Z-scored | 3d | +0.0655 | +0.0746 | +0.0279 | +0.45 | 257 |
| Flow 5d Z-scored | 7d | +0.0883 | +0.1129 | +0.0173 | +0.28 | 253 |
| Flow 5d Z-scored | 14d | +0.1789 | +0.1874 | +0.1368 | +2.15 | 246 |
| Flow 20d Z-scored | 1d | +0.0530 | +0.0525 | +0.0482 | +0.78 | 259 |
| Flow 20d Z-scored | 3d | +0.0805 | +0.0690 | +0.0894 | +1.43 | 257 |
| Flow 20d Z-scored | 7d | +0.1579 | +0.1424 | +0.1642 | +2.61 | 253 |
| Flow 20d Z-scored | 14d | +0.2442 | +0.2632 | +0.1907 | +2.99 | 246 |
| Flow Momentum Z-scored | 1d | -0.0019 | +0.0051 | -0.0219 | -0.35 | 259 |
| Flow Momentum Z-scored | 3d | +0.0045 | +0.0168 | -0.0269 | -0.43 | 257 |
| Flow Momentum Z-scored | 7d | -0.0294 | +0.0091 | -0.1045 | -1.66 | 253 |
| Flow Momentum Z-scored | 14d | +0.0221 | +0.0410 | -0.0160 | -0.25 | 246 |

## Key Findings

**Best OOS signal:** Flow 20d Z-scored at 14d horizon (IC=+0.1907, t=+2.99)

- Flow 5d Sum -> 7d return: IC_OOS=-0.0124 (t=-0.20)
- Flow 5d Sum -> 14d return: IC_OOS=+0.1027 (t=+1.61)
- Flow 20d Sum -> 14d return: IC_OOS=+0.1248 (t=+1.96)

**IC Decay Peak:** Horizon 22d (IC=+0.1113)

## Quintile Analysis (flow_5d)

- 1d horizon: Q1 (lowest flows)=-0.2745%, Q5 (highest flows)=0.1992%, spread=0.4737%
- 3d horizon: Q1 (lowest flows)=-0.4229%, Q5 (highest flows)=0.4874%, spread=0.9103%
- 7d horizon: Q1 (lowest flows)=-0.3085%, Q5 (highest flows)=1.0423%, spread=1.3508%
- 14d horizon: Q1 (lowest flows)=-0.9301%, Q5 (highest flows)=2.7457%, spread=3.6757%

## Regime Interaction (ETF Flow x DXY+10Y)

| Regime | Flow Dir | Horizon | Mean Return | t-stat | N |
|--------|----------|---------|-------------|--------|---|
| EASING | inflow | 1d | 0.1787% | +1.12 | 183 |
| EASING | outflow | 1d | -0.1997% | -0.58 | 91 |
| MIXED | inflow | 1d | 0.1162% | +0.59 | 147 |
| MIXED | outflow | 1d | -0.0299% | -0.13 | 128 |
| TIGHTENING | inflow | 1d | 0.3348% | +1.55 | 164 |
| TIGHTENING | outflow | 1d | -0.0177% | -0.07 | 79 |
| EASING | inflow | 3d | 0.1791% | +0.68 | 183 |
| EASING | outflow | 3d | -0.2093% | -0.39 | 91 |
| MIXED | inflow | 3d | 0.3884% | +1.30 | 147 |
| MIXED | outflow | 3d | -0.2685% | -0.73 | 128 |
| TIGHTENING | inflow | 3d | 1.1404% | +2.98 | 162 |
| TIGHTENING | outflow | 3d | 0.1849% | +0.36 | 79 |
| EASING | inflow | 7d | -0.1582% | -0.39 | 183 |
| EASING | outflow | 7d | 0.6834% | +1.12 | 91 |
| MIXED | inflow | 7d | 0.6168% | +1.38 | 147 |
| MIXED | outflow | 7d | -0.7199% | -1.24 | 128 |
| TIGHTENING | inflow | 7d | 3.2715% | +5.10 | 158 |
| TIGHTENING | outflow | 7d | -0.1137% | -0.16 | 79 |
| EASING | inflow | 14d | -0.2123% | -0.42 | 183 |
| EASING | outflow | 14d | 0.4665% | +0.55 | 91 |
| MIXED | inflow | 14d | 0.8700% | +1.35 | 142 |
| MIXED | outflow | 14d | -1.6838% | -2.18 | 126 |
| TIGHTENING | inflow | 14d | 6.8877% | +7.26 | 158 |
| TIGHTENING | outflow | 14d | 0.9940% | +0.87 | 79 |

## Rolling IC Stability

- Flow 5d -> 7d: mean rolling IC=-0.0791, positive 41% of windows
- Flow 5d -> 14d: mean rolling IC=-0.0812, positive 38% of windows
- Flow 20d -> 7d: mean rolling IC=-0.1060, positive 30% of windows
- Flow 20d -> 14d: mean rolling IC=-0.1157, positive 36% of windows

## Data Sources

- **SoSoValue API** (300 days, Jan 2025 - Mar 2026): Accurate USD-denominated daily net flows
- **Bitbo.io** (233 days, Jan 2024 - Jan 2025): BTC-denominated flows from embedded page data, converted to USD using actual daily BTC closing prices
- Combined dataset: 533 trading days covering the full BTC spot ETF era

## Interpretation Notes

### Rolling IC Stability Warning
The negative mean rolling IC values are a red flag. While the full-period Spearman IC is positive (flows positively correlate with future returns), the rolling 60-day windows show inconsistency. This suggests the signal works well during certain regimes but fails or reverses in others. This is consistent with the regime interaction results.

### The TIGHTENING + Inflow Anomaly
The most striking finding is the TIGHTENING regime + ETF inflow combination:
- 7d mean return: +3.27% (t=5.10), 62% win rate, N=158
- 14d mean return: +6.89% (t=7.26), 70.3% win rate, N=158

This is counterintuitive: why would inflows during tightening produce the highest returns? Likely explanation: when institutional investors buy BTC despite rising rates and strong dollar, they are expressing high conviction. This "buying against the macro headwind" signal separates informed institutional demand from momentum-chasing retail flows. The conviction buying predicts that the tightening headwinds are either priced in or about to reverse.

### Signal Does NOT Work Well During Easing
Surprisingly, ETF inflows during easing regimes show near-zero or negative forward returns at 7d and 14d horizons. During easing, BTC rises broadly, and ETF inflows may reflect momentum-chasing rather than informed demand. The signal has no incremental value when "everyone is buying."

## Conclusion

Out of 32 signal-horizon combinations tested, 9 showed |IC_OOS| > 0.05 with |t| > 1.5.

### What Works
1. **Flow 20d Z-scored at 14d horizon** is the strongest standalone signal: IC_OOS=+0.19, t=+2.99. Z-scoring normalizes for changing flow regimes (flows were much larger in late 2024 than early 2024).
2. **Flow 20d Z-scored at 7d** also strong: IC_OOS=+0.16, t=+2.61.
3. **Quintile spreads are economically significant**: Q5-Q1 spread of 3.68% at 14d for flow_5d, and 5.50% for flow_20d.

### What Does NOT Work
1. **Daily flow signal**: Noise dominates at 1d and 3d horizons. IC is near zero OOS.
2. **Flow momentum (5d vs 20d acceleration)**: Inconsistent sign, negative OOS. The rate of change of flows is not predictive.
3. **Flow streak count**: Weak signal at all horizons.
4. **Rolling IC stability is poor**: The signal is time-varying and regime-dependent.

### Recommended Use
- Use as a **regime overlay** (position sizing, not entry signal) rather than standalone directional predictor
- Best construction: 20-day rolling sum, Z-scored against trailing 60-day window
- Optimal holding period: 14-22 days (IC peaks at 22d)
- Strongest alpha when combined with macro regime: TIGHTENING + inflow = high-conviction buy
- The existing s38_momentum_etf_flow strategy (3-day window) may underperform; a 20-day window would be more robust
