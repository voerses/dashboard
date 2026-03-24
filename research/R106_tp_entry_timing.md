# R106 -- Trend+Pullback RSI as Entry Timing Overlay for V3 Momentum

**Date**: 2026-03-24
**Asset**: BTC spot (1h bars)
**Period**: 2021-01-01 to 2026-03-14
**OOS Focus**: 2025-09-01 to 2026-03-31

## Hypothesis

V3 Momentum (s320) rebalances weekly (168 bars), entering blindly when EMA20>EMA50.
R105 showed that Trend+Pullback (4h RSI cross through 40 during uptrend) is 98%
overlapping with V3 as a separate strategy (both need EMA20>EMA50).

Instead of running T+P as a separate strategy, we test whether RSI pullback signals
can IMPROVE V3's entry timing -- buying at pullback dips instead of random weekly points.
Target improvement: reduced drawdown during range markets.

## Variant Definitions

| Variant | Modification | Expected Benefit |
|---------|-------------|-----------------|
| Baseline | Standard V3 weekly rebalance | Reference |
| V1 Strict | Only enter if 4h RSI < 45 near rebalance | Fewer but better entries |
| V2 Flexible | Enter on first RSI cross-up through 40 in window; fallback if none | Deferred entry to dips |
| V3 Conviction | Normal timing, scale size by RSI at entry | Better sizing at pullbacks |

## OOS Results (2025-09 to 2026-03)

| Variant | Sharpe | dSharpe | Total Return | MaxDD | dDD | Trades | WR | Avg PnL | Avg Entry RSI |
|---------|--------|---------|-------------|-------|-----|--------|-----|---------|--------------|
| baseline | **-19.804** | +0.000 | -21.41% | -18.20% | +0.00% | 4 | 0.0% | -5.352% | 45.5 |
| v1_strict | **-14.846** | +4.958 | -11.41% | -8.85% | +9.35% | 3 | 0.0% | -3.803% | 27.0 |
| v2_flexible | **-6.862** | +12.942 | -4.21% | -5.09% | +13.11% | 4 | 25.0% | -1.052% | 42.2 |
| v3_conviction | **-15.555** | +4.249 | -19.46% | -16.53% | +1.67% | 4 | 0.0% | -4.864% | 45.5 |

### Range-Regime Performance (Target Improvement Area)

| Variant | Range Trades Return | Range Sharpe |
|---------|-------------------|-------------|
| baseline | 0.00% | 0.000 |
| v1_strict | 0.00% | 0.000 |
| v2_flexible | 0.00% | 0.000 |
| v3_conviction | 0.00% | 0.000 |

## Full Period Results (2021-01 to 2026-03, default params)

Context: full-period run with default parameters (no optimization). Shows how each
variant performs over all market regimes with a larger trade sample.

| Variant | Sharpe | dSharpe | Total Return | MaxDD | Trades | WR | Avg Entry RSI |
|---------|--------|---------|-------------|-------|--------|-----|--------------|
| baseline | **0.391** | +0.000 | 52.77% | -58.51% | 135 | 53.3% | 53.2 |
| v1_strict | **1.563** | +1.172 | 145.66% | -35.70% | 84 | 63.1% | 34.8 |
| v2_flexible | **3.287** | +2.896 | 336.87% | -28.29% | 135 | 68.9% | 47.8 |
| v3_conviction | **0.304** | -0.087 | 34.92% | -57.09% | 135 | 53.3% | 53.2 |

### Full Period Range-Regime Performance

| Variant | Range Trades Return | Range Sharpe |
|---------|-------------------|-------------|
| baseline | -44.70% | -1.757 |
| v1_strict | 12.78% | 0.505 |
| v2_flexible | 2.68% | 0.112 |
| v3_conviction | -45.17% | -1.768 |

## Walk-Forward Results (6 windows, 12mo train / 6mo test)

### baseline

| Window | Test Period | Trades | PnL | Sharpe | WR | MaxDD |
|--------|-----------|--------|-----|--------|-----|-------|
| W1 | 2023-03-30 to 2023-09-25 | 16 | -23.52% | -3.07 | 18.8% | -25.91% |
| W2 | 2023-09-26 to 2024-03-23 | 24 | 92.32% | 3.49 | 66.7% | -15.56% |
| W3 | 2024-03-24 to 2024-09-19 | 13 | -18.34% | -2.35 | 38.5% | -23.49% |
| W4 | 2024-09-20 to 2025-03-18 | 21 | 42.61% | 2.49 | 57.1% | -8.69% |
| W5 | 2025-03-19 to 2025-09-14 | 18 | 15.72% | 1.64 | 55.6% | -7.90% |
| W6 | 2025-09-15 to 2026-03-13 | 4 | -17.96% | -11.57 | 0.0% | -16.32% |

### v1_strict

| Window | Test Period | Params | Trades | PnL | Sharpe | dSharpe | WR | MaxDD |
|--------|-----------|--------|--------|-----|--------|---------|-----|-------|
| W1 | 2023-03-30 to 2023-09-25 | RSI<40.0 LB=36h | 7 | 5.19% | 1.49 | +4.56 | 57.1% | -6.01% |
| W2 | 2023-09-26 to 2024-03-23 | RSI<35.0 LB=12h | 7 | 30.22% | 11.45 | +7.96 | 100.0% | 0.00% |
| W3 | 2024-03-24 to 2024-09-19 | RSI<35.0 LB=48h | 6 | 18.64% | 4.51 | +6.86 | 66.7% | -5.30% |
| W4 | 2024-09-20 to 2025-03-18 | RSI<35.0 LB=12h | 1 | 0.16% | 0.00 | -2.49 | 100.0% | 0.00% |
| W5 | 2025-03-19 to 2025-09-14 | RSI<35.0 LB=48h | 8 | 3.13% | 1.15 | -0.49 | 50.0% | -2.23% |
| W6 | 2025-09-15 to 2026-03-13 | RSI<35.0 LB=24h | 2 | -6.10% | -33.44 | -21.87 | 0.0% | -3.71% |

### v2_flexible

| Window | Test Period | Params | Trades | PnL | Sharpe | dSharpe | WR | MaxDD |
|--------|-----------|--------|--------|-----|--------|---------|-----|-------|
| W1 | 2023-03-30 to 2023-09-25 | cross@30.0 | 16 | 3.03% | 0.46 | +3.54 | 43.8% | -7.23% |
| W2 | 2023-09-26 to 2024-03-23 | cross@35.0 | 24 | 122.23% | 5.88 | +2.39 | 75.0% | -5.05% |
| W3 | 2024-03-24 to 2024-09-19 | cross@40.0 | 13 | 9.36% | 1.41 | +3.76 | 61.5% | -11.21% |
| W4 | 2024-09-20 to 2025-03-18 | cross@45.0 | 21 | 70.67% | 5.24 | +2.75 | 81.0% | -8.62% |
| W5 | 2025-03-19 to 2025-09-14 | cross@35.0 | 18 | 42.89% | 6.48 | +4.84 | 77.8% | -3.96% |
| W6 | 2025-09-15 to 2026-03-13 | cross@35.0 | 4 | 2.40% | 3.98 | +15.55 | 50.0% | -1.16% |

### v3_conviction

| Window | Test Period | Params | Trades | PnL | Sharpe | dSharpe | WR | MaxDD |
|--------|-----------|--------|--------|-----|--------|---------|-----|-------|
| W1 | 2023-03-30 to 2023-09-25 | d<35.0 s<40.0 m=1.5/0.5 | 16 | -20.35% | -3.08 | -0.00 | 18.8% | -21.26% |
| W2 | 2023-09-26 to 2024-03-23 | d<35.0 s<50.0 m=1.5/0.5 | 24 | 64.29% | 3.59 | +0.09 | 66.7% | -12.62% |
| W3 | 2024-03-24 to 2024-09-19 | d<30.0 s<50.0 m=1.2/0.8 | 13 | -16.68% | -2.60 | -0.25 | 38.5% | -20.77% |
| W4 | 2024-09-20 to 2025-03-18 | d<35.0 s<40.0 m=1.5/0.5 | 21 | 21.72% | 2.51 | +0.03 | 57.1% | -4.53% |
| W5 | 2025-03-19 to 2025-09-14 | d<35.0 s<45.0 m=1.5/0.5 | 18 | 4.46% | 0.79 | -0.85 | 50.0% | -6.11% |
| W6 | 2025-09-15 to 2026-03-13 | d<35.0 s<45.0 m=1.5/0.7 | 4 | -15.84% | -14.46 | -2.89 | 0.0% | -14.09% |

## Walk-Forward dSharpe Summary

| Variant | W1 | W2 | W3 | W4 | W5 | W6 | Mean | Windows>0 |
|---------|----|----|----|----|----|----|------|-----------|
| v1_strict | +4.56 | +7.96 | +6.86 | -2.49 | -0.49 | -21.87 | -0.91 | 3/6 |
| v2_flexible | +3.54 | +2.39 | +3.76 | +2.75 | +4.84 | +15.55 | +5.47 | 6/6 |
| v3_conviction | -0.00 | +0.09 | -0.25 | +0.03 | -0.85 | -2.89 | -0.65 | 2/6 |

## Trade Count Comparison (per window)

| Window | Baseline | V1 Strict | V2 Flexible | V3 Conviction |
|--------|----------|-----------|-------------|---------------|
| W1 | 16 | 7 | 16 | 16 |
| W2 | 24 | 7 | 24 | 24 |
| W3 | 13 | 6 | 13 | 13 |
| W4 | 21 | 1 | 21 | 21 |
| W5 | 18 | 8 | 18 | 18 |
| W6 | 4 | 2 | 4 | 4 |

## Kill Criteria Evaluation

| Criterion | Threshold | V1 Strict | V2 Flexible | V3 Conviction |
|-----------|-----------|-----------|-------------|---------------|
| OOS dSharpe | > 0 | +4.958 PASS | +12.942 PASS | +4.249 PASS |
| WF windows improved | >= 3/6 | 3/6 PASS | 6/6 PASS | 2/6 FAIL |
| Min trades/window | >= 15 | 1 FAIL | 4 FAIL | 4 FAIL |

## Verdicts

### v1_strict: **KILL**

- OOS Sharpe: -14.846 (baseline: -19.804, dSharpe: +4.958)
- OOS MaxDD: -8.85% (baseline: -18.20%, improvement: +9.35%)
- Walk-forward: 3/6 windows improved (mean dSharpe: -0.912)
- Range-regime improvement: +0.00%
- **Kill reasons:**
  - Trade count <15 in 6/6 windows (min=1)
- Conditionals:
  - OOS period has only 4 baseline trades -- insufficient for statistical significance
  - Walk-forward borderline: 3/6 improved

### v2_flexible: **CONDITIONAL**

- OOS Sharpe: -6.862 (baseline: -19.804, dSharpe: +12.942)
- OOS MaxDD: -5.09% (baseline: -18.20%, improvement: +13.11%)
- Walk-forward: 6/6 windows improved (mean dSharpe: +5.471)
- Range-regime improvement: +0.00%
- Conditionals:
  - Baseline itself sparse (<15 trades) in 2/6 windows
  - OOS period has only 4 baseline trades -- insufficient for statistical significance

### v3_conviction: **KILL**

- OOS Sharpe: -15.555 (baseline: -19.804, dSharpe: +4.249)
- OOS MaxDD: -16.53% (baseline: -18.20%, improvement: +1.67%)
- Walk-forward: 2/6 windows improved (mean dSharpe: -0.645)
- Range-regime improvement: +0.00%
- **Kill reasons:**
  - Walk-forward <3/6 windows improved (2/6)
- Conditionals:
  - Baseline itself sparse (<15 trades) in 2/6 windows
  - OOS period has only 4 baseline trades -- insufficient for statistical significance

## Final Recommendation

**KILLED variants:**
- v1_strict: Trade count <15 in 6/6 windows (min=1)
- v3_conviction: Walk-forward <3/6 windows improved (2/6)

**CONDITIONAL variants:**
- v2_flexible: Baseline itself sparse (<15 trades) in 2/6 windows; OOS period has only 4 baseline trades -- insufficient for statistical significance

**Best variant: v2_flexible** (verdict: CONDITIONAL)

**CAVEAT: OOS period has only 4 baseline trades.**
The OOS dSharpe and DD improvement numbers are based on too few observations
to be statistically significant. The walk-forward results are more reliable
since they cover multiple market regimes with more trades per window.

### V2 Flexible Window -- Evidence Summary

- **Walk-forward: 6/6 windows improved** (mean dSharpe: +5.471)
- OOS dSharpe: +12.942 (limited by 4-trade sample)
- OOS DD improvement: +13.11%

### Full Period Context (strongest evidence)

- Baseline full-period: Sharpe 0.391, Return 52.77%, MaxDD -58.51%
- V2 Flexible full-period: Sharpe 3.287, Return 336.87%, MaxDD -28.29%
- dSharpe: +2.896
- DD improvement: +30.22% (from -58.51% to -28.29%)
- Win rate improvement: 53.3% -> 68.9%
- Average entry RSI: 53.2 -> 47.8
- Same trade count: 135 (fallback ensures no missed entries)

### Range-Regime Target

- Baseline range-regime: -44.70% return, Sharpe -1.757
- V2 range-regime: 2.68% return, Sharpe 0.112
- V1 range-regime: 12.78% return, Sharpe 0.505

### Mechanism

V2 Flexible defers V3's entry within the 168-bar rebalance window to the first
4h RSI cross-up through 40. If no cross occurs in the window, it falls back to
the original rebalance point (ensuring no missed entries). This means:
- When a pullback occurs early in the window, entry is delayed to the dip
- When no pullback occurs (strong trend), entry happens at the normal rebalance
- Trade count remains identical to baseline
- Average entry RSI drops from 53.2 to 47.8 (lower = cheaper entry)

**Recommendation: CONDITIONAL -- strong walk-forward evidence (6/6 windows), but
OOS period has insufficient sample size for confident statistical validation.**

Next steps:
1. Monitor V2 Flexible entry timing alongside V3 baseline in paper trading
2. After 3+ months with 15+ trades, re-evaluate OOS dSharpe with adequate sample
3. If confirmed positive, integrate into s320 as optional entry timing mode
