# V3 Perp + Overlay + Dynamic Leverage -- Results

**Generated:** 2026-03-26 14:07:19

## Context

V3 is a BTC-only momentum strategy: 20/50 EMA crossover + positioning overlay
+ VRP overlay. This test evaluates whether **dynamic leverage** (VRP-regime-scaled)
on perpetual futures can achieve high returns with controlled drawdowns.

**Key Innovation:** VRP regime controls BOTH position sizing AND leverage,
creating multiplicative risk reduction during turbulence.

**Prior Results (Reference):**
- V3 on BTC SPOT (OOS): +17.52%, Sharpe 0.56, MaxDD -20.2%
- V3 on 1x BTC perp (finding #79): Sharpe 0.47, Return +10.2%, MaxDD -37.4%
- 2x perp: KILL (-62% DD), 3x perp: KILL (-78% DD)

## Data

- **Perp Data:** 2020-01-01 to 2026-03-17 (54425 hourly bars)
- **IS Period:** 2020-03-31 to 2024-06-02
- **OOS Period:** 2024-06-02 to 2026-03-17
- **Funding:** Actual Binance BTC rates (8h settlement at 00/08/16 UTC)
- **Mean Funding (ann):** 9.83%
- **Trading Cost:** 10 bps per trade
- **Warmup:** 90 days, **Rebalance:** Weekly (168 bars)

## VRP Regime Distribution (Full Period)

| Regime | VRP z Threshold | Time % |
|--------|-----------------|--------|
| Complacent | z > 1.0 | 17.7% |
| Normal | -0.5 < z < 1.0 | 49.2% |
| Turbulent | -1.5 < z < -0.5 | 18.7% |
| Extreme | z < -1.5 | 14.4% |

## Experiment Configurations

| Experiment | Base Lev | Dynamic | Complacent | Normal | Turbulent | Extreme |
|------------|----------|---------|------------|--------|-----------|---------|
| Exp 1: V3+Overlay 1x Perp (Baseline) | 1.0x | No | 1.0x | 1.0x | 1.0x | 1.0x |
| Exp 2: Dynamic VRP-Scaled (0.5x-2.0x) | 1.0x | Yes | 2.0x | 1.0x | 0.5x | 0.3x |
| Exp 3: Fixed 1.5x Leverage + Overlay | 1.5x | No | 1.5x | 1.5x | 1.5x | 1.5x |
| Exp 4: Conservative Dynamic (0.7x-1.3x) | 1.0x | Yes | 1.3x | 1.0x | 0.7x | 0.5x |
| Exp 5: Dynamic 1.5x Base (0.5x-2.0x) | 1.5x | Yes | 2.0x | 1.5x | 0.75x | 0.5x |
| Exp 6: Ultra-Conservative (0.8x-1.2x) | 1.0x | Yes | 1.2x | 1.0x | 0.8x | 0.6x |
| Exp 7: Dynamic Moderate (0.5x-1.5x) | 1.0x | Yes | 1.5x | 1.0x | 0.5x | 0.3x |

## OOS Performance Comparison

| Metric | 1x Base | Dyn Aggr | 1.5x Fix | Cons Dyn | Dyn 1.5x | Ultra Cons | Dyn Mod |
|--------|---:|---:|---:|---:|---:|---:|---:|
| Ann Return | 10.9% | 22.6% | 13.1% | 15.0% | 19.1% | 13.7% | 17.6% |
| Total Return | 20.3% | 44.0% | 24.6% | 28.4% | 36.7% | 25.7% | 33.6% |
| Sharpe | 0.494 | 0.642 | 0.493 | 0.566 | 0.581 | 0.545 | 0.599 |
| Sortino | 0.548 | 0.739 | 0.553 | 0.638 | 0.664 | 0.612 | 0.679 |
| Max DD | -37.3% | -52.2% | -51.2% | -42.1% | -57.5% | -40.5% | -45.1% |
| Calmar | 0.292 | 0.433 | 0.256 | 0.357 | 0.333 | 0.337 | 0.390 |
| DD Duration (d) | 216 | 216 | 216 | 216 | 216 | 216 | 216 |
| Win Rate % | 25.9 | 25.7 | 25.9 | 25.7 | 25.7 | 25.7 | 25.7 |
| Time in Mkt % | 51.7 | 51.7 | 51.7 | 51.7 | 51.7 | 51.7 | 51.7 |
| Mean Eff Lev | 0.829 | 1.193 | 1.244 | 0.927 | 1.397 | 0.893 | 0.996 |
| Max Eff Lev | 1.500 | 3.000 | 2.250 | 1.950 | 3.000 | 1.800 | 2.250 |
| Fund Drag %/yr | 2.56 | 3.61 | 3.84 | 2.84 | 4.27 | 2.74 | 3.04 |
| Fund % of Gross | 13.5 | 9.6 | 13.5 | 11.5 | 11.2 | 12.1 | 10.7 |
| Liquidations | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| Near-Liquidations | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

## IS Performance (Reference)

| Metric | 1x Base | Dyn Aggr | 1.5x Fix | Cons Dyn | Dyn 1.5x | Ultra Cons | Dyn Mod |
|--------|---:|---:|---:|---:|---:|---:|---:|
| Ann Return | 42.5% | 20.2% | 56.6% | 35.5% | 42.7% | 37.8% | 30.8% |
| Sharpe | 0.987 | 0.611 | 0.985 | 0.864 | 0.849 | 0.905 | 0.783 |
| Max DD | -60.3% | -82.1% | -76.2% | -68.2% | -83.9% | -65.7% | -72.9% |
| Calmar | 0.705 | 0.246 | 0.743 | 0.520 | 0.508 | 0.576 | 0.422 |
| Fund Drag %/yr | 10.48 | 11.11 | 15.72 | 10.59 | 15.78 | 10.55 | 10.67 |

## Kill Criteria Assessment (OOS)

| Criterion | Threshold |
|-----------|-----------|
| MaxDD | > -40% |
| Liquidations | > 1 |
| Sharpe | < 0.3 |
| Funding % Gross | > 50% |

**Exp 1: V3+Overlay 1x Perp (Baseline):** PASS
- Sharpe 0.494, Return 10.9%, DD -37.3%

**Exp 2: Dynamic VRP-Scaled (0.5x-2.0x):** KILL
- MaxDD -52.2% exceeds -40% threshold

**Exp 3: Fixed 1.5x Leverage + Overlay:** KILL
- MaxDD -51.2% exceeds -40% threshold

**Exp 4: Conservative Dynamic (0.7x-1.3x):** KILL
- MaxDD -42.1% exceeds -40% threshold

**Exp 5: Dynamic 1.5x Base (0.5x-2.0x):** KILL
- MaxDD -57.5% exceeds -40% threshold

**Exp 6: Ultra-Conservative (0.8x-1.2x):** KILL
- MaxDD -40.5% exceeds -40% threshold

**Exp 7: Dynamic Moderate (0.5x-1.5x):** KILL
- MaxDD -45.1% exceeds -40% threshold

## Dynamic Leverage Value Assessment

Does dynamic leverage add value over fixed leverage?

**Dynamic Aggressive vs Fixed 1x:**
- Return: 10.9% -> 22.6% (+11.7%)
- Sharpe: 0.494 -> 0.642 (+0.148)
- MaxDD: -37.3% -> -52.2%
- Calmar: 0.292 -> 0.433

**Dynamic Aggressive vs Fixed 1.5x:**
- Return: 13.1% -> 22.6% (+9.5%)
- Sharpe: 0.493 -> 0.642 (+0.149)
- MaxDD: -51.2% -> -52.2%
- Key insight: dynamic leverage targets SAME average exposure but with regime-appropriate scaling

**Conservative Dynamic vs Fixed 1x:**
- Return: 10.9% -> 15.0% (+4.1%)
- Sharpe: 0.494 -> 0.566 (+0.072)
- MaxDD: -37.3% -> -42.1%
- This is the safest dynamic approach

## Verdict

### Recommendation

**Best risk-adjusted experiment:** Exp 1: V3+Overlay 1x Perp (Baseline)
- Annual Return: 10.9%
- Sharpe: 0.494
- Max DD: -37.3%
- Calmar: 0.292
- Sortino: 0.548
- Funding Drag: 2.56%/yr (13.5% of gross)
- Mean Effective Leverage: 0.829x

## Funding Rate Impact

Funding rates on BTC perps average ~10%/yr annualized. For a strategy
that generates 11% gross on 1x, this is a significant drag.

Key observations:
- Funding is charged on the leveraged notional (2x leverage = 2x funding)
- Dynamic leverage REDUCES funding cost during turbulence (when funding tends to be higher)
- The overlay's position sizing ALSO reduces funding exposure when cautious
- Net effect: overlay+dynamic leverage pays less funding than fixed leverage at same average return

## Critical Analysis

### Why 100%+ Annual Returns Are Not Achievable

The hypothesis was that overlays + dynamic leverage could push returns to 50-100%+ with
controlled drawdowns. This is **conclusively falsified** by the data:

1. **The base edge is too narrow.** V3+overlay on 1x perp produces ~11% OOS return, and
   ~10% of that is consumed by funding drag. The net edge after funding is ~8-9% on 1x.
   Even 2x leverage only doubles this to ~16-18%, not enough for 50%+.

2. **BTC crashed 50% in Jan-Feb 2026** ($126K -> $63K). The 20/50 EMA is too slow to
   exit before absorbing a significant portion of this crash. The strategy went FLAT on
   Jan 20 at $88K after entering at $95K on Jan 17 (the EMA briefly crossed), but the
   prior drawdown from the Oct 2025 peak ($118K) was already accumulating.

3. **Funding drag scales linearly with leverage.** At ~10%/yr annualized funding on BTC
   perps, a 2x position pays ~20%/yr in funding. This eats most of the leveraged return.

4. **Overlay reduces DD but also reduces upside.** The positioning overlay cuts exposure
   during crowded longs (common in bull runs), which protects on reversals but caps the
   upside that leverage needs to amplify.

### What Dynamic Leverage DOES Achieve

Despite not reaching the 50%+ threshold, dynamic leverage shows clear value:

| Comparison | Sharpe Delta | Return Delta | DD Change |
|-----------|-------------|-------------|-----------|
| Exp 2 (dyn 0.5-2x) vs Exp 1 (fixed 1x) | +0.148 | +11.7% | -14.9% worse |
| Exp 2 (dyn 0.5-2x) vs Exp 3 (fixed 1.5x) | +0.149 | +9.5% | -1.1% similar |
| Exp 4 (dyn 0.7-1.3x) vs Exp 1 (fixed 1x) | +0.072 | +4.1% | -4.8% worse |
| Exp 7 (dyn 0.5-1.5x) vs Exp 1 (fixed 1x) | +0.105 | +6.7% | -7.8% worse |

**Key insight:** Dynamic leverage generates HIGHER Sharpe than any fixed leverage level.
Exp 2 achieves Sharpe 0.642 -- the highest across ALL experiments, including fixed 1x
(0.494). This means the VRP-regime leverage scaling genuinely improves risk-adjusted
returns. The issue is that the absolute drawdown still exceeds the -40% kill threshold.

### Funding Drag Efficiency

Dynamic leverage also improves funding efficiency:
- Fixed 1.5x: 13.5% of gross consumed by funding
- Dynamic 0.5-2x (same avg exposure): only 9.6% consumed
- This is because dynamic leverage de-levers during turbulence, which often coincides
  with elevated funding rates

### Bottom Line

- **V3 on BTC perps is NOT the path to 100% returns.** The strategy edge (~17% gross
  on spot) is too narrow for leverage to amplify past funding drag and crash risk.
- **Dynamic leverage IS a valid technique** that improves Sharpe by ~0.1-0.15 over fixed.
- **The best perp configuration is 1x with full overlays** (Sharpe 0.494, -37% DD).
- **For 50%+ returns, need a fundamentally different approach:**
  - Multi-asset portfolio with uncorrelated signals
  - Higher-frequency strategy with smaller per-trade risk
  - Altcoin perps with funding arbitrage opportunities
  - Options strategies rather than linear perps

## Next Steps

1. Walk-forward validation on the best configuration
2. Monte Carlo simulation for tail risk assessment
3. Test with realistic slippage model (not just fixed bps)
4. Consider funding rate prediction model to further optimize entry timing
5. Evaluate if dynamic leverage adds value over simply using fixed 1x with larger allocation
6. Investigate multi-asset portfolio approach for return target
7. Test dynamic leverage concept on higher-frequency signals where the edge/funding ratio is better
