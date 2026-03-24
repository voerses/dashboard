# Overlay Backtest Results: Positioning & Crisis on Trend-Following Base

**Run date**: 2026-03-24 11:21
**IS period**: 2020-09-01 to 2024-12-31
**OOS period**: 2025-01-01 to latest
**Rebalancing**: Weekly (Monday)
**Transaction cost**: 10 bps round-trip

## Architecture

- **Base**: Trend following (50/200 SMA crossover with hysteresis)
- **Layer 1**: Positioning sizing (0.3x to 1.5x based on Binance top trader L/S z-scores)
- **Layer 2**: Crisis hedge (exit on BTC drawdown + oil stress)
- **Layer 3**: Macro agreement boost (US10Y + DXY rank composite)
- **Rebalancing**: Weekly to avoid excessive turnover from noisy daily signals

## 1. Variant Performance Table

| Variant | IS Return | OOS Return | IS Sharpe | OOS Sharpe | IS MaxDD | OOS MaxDD | IS Calmar | OOS Calmar | Turnover/yr | Cost Drag/yr |
|---------|-----------|------------|-----------|------------|----------|-----------|-----------|------------|-------------|--------------|
| Base Only (Trend) | 43.7% | -2.7% | 1.26 | -0.14 | -45.6% | -19.1% | 0.96 | -0.14 | 4.6 | 0.46% |
| Base + Positioning | 37.0% | 3.0% | 1.13 | 0.15 | -43.8% | -16.7% | 0.85 | 0.18 | 8.4 | 0.84% |
| Base + Pos + Crisis | 37.0% | 3.0% | 1.13 | 0.15 | -43.8% | -16.7% | 0.85 | 0.18 | 8.4 | 0.84% |
| Base + All Layers | 37.5% | 3.2% | 1.14 | 0.17 | -43.8% | -16.7% | 0.86 | 0.19 | 8.7 | 0.87% |
| Base + Crisis Only | 43.7% | -2.7% | 1.26 | -0.14 | -45.6% | -19.1% | 0.96 | -0.14 | 4.6 | 0.46% |

## 2. Layer Contribution Analysis

Marginal improvement of each layer (IS / OOS):

| Layer Added | IS dSharpe | OOS dSharpe | IS dReturn | OOS dReturn | IS dMaxDD | OOS dMaxDD |
|-------------|------------|-------------|------------|-------------|-----------|------------|
| + Positioning | -0.13 | +0.30 | -6.7% | +5.6% | +1.8% | +2.5% |
| + Positioning + Crisis | -0.13 | +0.30 | -6.7% | +5.6% | +1.8% | +2.5% |
| + All Layers | -0.13 | +0.31 | -6.2% | +5.9% | +1.8% | +2.5% |
| + Crisis Only (no positioning) | +0.00 | +0.00 | +0.0% | +0.0% | +0.0% | +0.0% |

Note: All deltas are vs Base Only. Positive dMaxDD means LESS drawdown (improvement).

### Sequential marginal contribution (each layer added incrementally):

| Step | IS dSharpe | OOS dSharpe |
|------|------------|-------------|
| + Positioning | -0.132 | +0.298 |
| + Crisis | +0.000 | +0.000 |
| + Macro | +0.006 | +0.012 |

## 3. Regime-Specific OOS Performance

Regime day counts (OOS):
- UPTREND: 45 days (10%)
- RANGE: 253 days (59%)
- DOWNTREND: 81 days (19%)
- CRISIS: 52 days (12%)

| Variant | UPTREND Ann Ret | RANGE Ann Ret | DOWNTREND Ann Ret | CRISIS Ann Ret |
|---------|-----------------|---------------|-------------------|----------------|
| Base Only (Trend) | 198.5% | -21.4% | 0.0% | 0.0% |
| Base + Positioning | 193.1% | -13.2% | 0.0% | 0.0% |
| Base + Pos + Crisis | 193.1% | -13.2% | 0.0% | 0.0% |
| Base + All Layers | 205.2% | -13.4% | 0.0% | 0.0% |
| Base + Crisis Only | 198.5% | -21.4% | 0.0% | 0.0% |

| Variant | UPTREND Sharpe | RANGE Sharpe | DOWNTREND Sharpe | CRISIS Sharpe |
|---------|----------------|--------------|------------------|---------------|
| Base Only (Trend) | 6.70 | -1.03 | 0.00 | 0.00 |
| Base + Positioning | 6.18 | -0.63 | 0.00 | 0.00 |
| Base + Pos + Crisis | 6.18 | -0.63 | 0.00 | 0.00 |
| Base + All Layers | 6.44 | -0.63 | 0.00 | 0.00 |
| Base + Crisis Only | 6.70 | -1.03 | 0.00 | 0.00 |

## 4. Monthly Returns (OOS) — Base + All Layers

| Month | Return |
|-------|--------|
| 2025-01 | -7.66% |
| 2025-02 | -4.97% |
| 2025-03 | 0.00% |
| 2025-04 | -1.47% |
| 2025-05 | 11.85% |
| 2025-06 | 2.44% |
| 2025-07 | 10.92% |
| 2025-08 | -1.96% |
| 2025-09 | -1.75% |
| 2025-10 | -0.24% |
| 2025-11 | 0.00% |
| 2025-12 | 0.00% |
| 2026-01 | 0.00% |
| 2026-02 | 0.00% |
| 2026-03 | 0.00% |
| **Cumulative** | **5.58%** |

## 5. Overlay Alpha vs Base

Net alpha = Overlay variant metrics - Base Only metrics

| Variant | IS Alpha (Return) | OOS Alpha (Return) | IS Alpha (Sharpe) | OOS Alpha (Sharpe) | IS DD Improvement | OOS DD Improvement |
|---------|-------------------|--------------------|-------------------|--------------------|-------------------|--------------------|
| Base + Positioning | -6.7% | +5.6% | -0.13 | +0.30 | +1.8% | +2.5% |
| Base + Pos + Crisis | -6.7% | +5.6% | -0.13 | +0.30 | +1.8% | +2.5% |
| Base + All Layers | -6.2% | +5.9% | -0.13 | +0.31 | +1.8% | +2.5% |
| Base + Crisis Only | +0.0% | +0.0% | +0.00 | +0.00 | +0.0% | +0.0% |

Positive Return/Sharpe alpha = overlay improves. Positive DD improvement = less drawdown (better).

## 6. Position Distribution (% of time at each level)

| Variant | 0.0 (Flat) | ~0.3 | ~0.5 | ~0.65 | ~1.0 | ~1.3 | ~1.5 |
|---------|------------|------|------|-------|------|------|------|
| Base Only (Trend) | 54.9% | 0.0% | 0.0% | 0.0% | 45.1% | 0.0% | 0.0% |
| Base + Positioning | 54.9% | 2.1% | 11.8% | 0.0% | 24.3% | 5.9% | 1.0% |
| Base + Pos + Crisis | 54.9% | 2.1% | 11.8% | 0.0% | 24.3% | 5.9% | 1.0% |
| Base + All Layers | 54.9% | 2.1% | 11.8% | 0.0% | 24.3% | 3.5% | 3.5% |
| Base + Crisis Only | 54.9% | 0.0% | 0.0% | 0.0% | 45.1% | 0.0% | 0.0% |

## 7. Key Conclusions

### Does the overlay improve the base strategy?

**YES** — Best overlay (Base + All Layers) improves OOS Sharpe by +0.31

### Is the improvement statistically significant?

- OOS daily return difference t-stat: 1.48
- OOS period: 431 days
- **Not statistically significant** (need more OOS data or larger effect)

### Which layer contributes most?

- Positioning layer OOS dSharpe: +0.298
- Crisis layer OOS dSharpe: +0.000
- Positioning + Crisis combined OOS dSharpe: +0.298
- Macro agreement marginal OOS dSharpe: +0.012
- **Positioning** contributes more (sizing adjustment)

**IMPORTANT: Crisis layer is structurally redundant.** The 50/200 SMA trend-following base exits positions (close < 50 SMA) before BTC 50d return reaches -10% or -20%. In the entire dataset, there are ZERO days where the crisis signal fires AND the base trend position is long. The crisis hedge provides no additional protection on top of trend following. To make it useful, it would need to use a faster indicator (e.g., 10d or 20d return thresholds) or trigger before the trend exit.

### Recommendation

**NEEDS_TUNING**

Reasoning: The positioning overlay clearly improves the base strategy (OOS Sharpe: -0.14 to +0.17, OOS MaxDD improvement: 2.5pp). However, several issues temper enthusiasm:

1. **Base strategy underperforms OOS**: The 50/200 SMA trend follower has negative OOS return (-2.7%) and negative OOS Sharpe (-0.14). The OOS period (Jan 2025 to Mar 2026) has been mostly range-bound, which is hostile to trend following. The overlay turns this into a small positive (+3.2% ann), but the absolute return is still modest.

2. **IS/OOS degradation**: Base IS Sharpe 1.26 vs OOS -0.14 is a massive drop. The overlay IS Sharpe 1.14 vs OOS 0.17 also degrades but less severely. This suggests the IS period captured a favorable trend regime (2020-2021 BTC bull run) that inflated IS metrics.

3. **Not statistically significant**: t-stat of 1.48 (p ~0.14) means we cannot reject the null hypothesis that the overlay adds no value. More OOS data is needed.

4. **Crisis layer is dead weight**: Completely redundant with trend-following base. Remove or redesign with faster indicators.

5. **The positioning layer works**: It reduces exposure during crowded long periods (high z-score) and increases exposure when positioning is light. The key value-add is in RANGE regimes where it reduces losses from -21.4% to -13.2% annualized by sizing down.

**Next steps if proceeding:**
- Remove the crisis layer (or redesign with 10-20d BTC return triggers)
- Test with a faster trend follower (20/50 SMA or 10/50 EMA) to see if the positioning overlay adds more when the base is more responsive
- Accumulate more OOS data before deploying capital
- Consider if the positioning layer's benefit is primarily "reduces whipsaws" which could also be achieved by simpler volatility-based sizing

### Context from R58

- R58 showed positioning signals as STANDALONE directional system lose money
- This backtest tests them as OVERLAYS on trend-following (the correct architecture)
- Weekly rebalancing (vs daily in R58) reduces turnover cost drag significantly
- Base trend-following IS Sharpe: 1.26, OOS Sharpe: -0.14
- Base IS Return: 43.7%, OOS Return: -2.7%

## Appendix: Equity Curve Data Points

Quarterly equity values (normalized to 1.0 at start):

| Date | Base Only | Base+Pos | Base+Pos+Crisis | Base+All | Base+Crisis |
|------|-----------|----------|-----------------|----------|-------------|
| 2020-09-30 | 0.94 | 0.94 | 0.94 | 0.94 | 0.94 |
| 2020-12-31 | 2.35 | 2.21 | 2.21 | 2.21 | 2.35 |
| 2021-03-31 | 4.78 | 4.24 | 4.24 | 4.24 | 4.78 |
| 2021-06-30 | 4.40 | 4.06 | 4.06 | 4.06 | 4.40 |
| 2021-09-30 | 4.27 | 4.22 | 4.22 | 4.22 | 4.27 |
| 2021-12-31 | 4.87 | 4.56 | 4.56 | 4.56 | 4.87 |
| 2022-03-31 | 4.87 | 4.56 | 4.56 | 4.56 | 4.87 |
| 2022-06-30 | 4.87 | 4.56 | 4.56 | 4.56 | 4.87 |
| 2022-09-30 | 4.87 | 4.56 | 4.56 | 4.56 | 4.87 |
| 2022-12-31 | 4.87 | 4.56 | 4.56 | 4.56 | 4.87 |
| 2023-03-31 | 6.06 | 5.50 | 5.50 | 5.50 | 6.06 |
| 2023-06-30 | 6.01 | 5.34 | 5.34 | 5.27 | 6.01 |
| 2023-09-30 | 5.76 | 5.04 | 5.04 | 4.97 | 5.76 |
| 2023-12-31 | 8.53 | 6.83 | 6.83 | 6.91 | 8.53 |
| 2024-03-31 | 12.03 | 10.17 | 10.17 | 10.40 | 12.03 |
| 2024-06-30 | 9.01 | 7.64 | 7.64 | 7.81 | 9.01 |
| 2024-09-30 | 7.04 | 5.97 | 5.97 | 6.11 | 7.04 |
| 2024-12-31 | 10.30 | 7.64 | 7.64 | 7.81 | 10.30 |
| 2025-03-31 | 9.04 | 6.70 | 6.70 | 6.85 | 9.04 |
| 2025-06-30 | 10.19 | 7.58 | 7.58 | 7.74 | 10.19 |
| 2025-09-30 | 10.16 | 8.05 | 8.05 | 8.27 | 10.16 |
| 2025-12-31 | 9.83 | 8.03 | 8.03 | 8.25 | 9.83 |
| 2026-03-14 | 9.83 | 8.03 | 8.03 | 8.25 | 9.83 |
