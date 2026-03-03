# Strategy Correlation Analysis — Tier A Strategies

> **Date:** 2026-03-03
> **Method:** Equal-weight daily returns across all tokens with trades, pairwise correlation
> **Universe:** 116 spot tokens, 2252 trading days (2020-01-02 to 2026-03-02)

## Headline: Effective N = 2.02 out of 6

Our 6 Tier A strategies behave like ~2 independent bets. Adding more momentum strategies
will not improve portfolio diversification.

## Correlation Matrix

|     | S09  | S11  | S13  | S17  | S18  | S21  |
|-----|------|------|------|------|------|------|
| S09 | 1.00 | 0.66 | 0.72 | 0.61 | 0.59 | 0.74 |
| S11 | 0.66 | 1.00 | 0.62 | **0.86** | 0.55 | 0.61 |
| S13 | 0.72 | 0.62 | 1.00 | 0.62 | 0.52 | 0.68 |
| S17 | 0.61 | **0.86** | 0.62 | 1.00 | 0.47 | 0.58 |
| S18 | 0.59 | 0.55 | 0.52 | 0.47 | 1.00 | 0.47 |
| S21 | 0.74 | 0.61 | 0.68 | 0.58 | 0.47 | 1.00 |

- Mean pairwise correlation: ~0.62
- Range: 0.47 (S18-S21) to 0.86 (S11-S17)
- All correlations positive — no natural hedges

## Per-Strategy Performance (equal-weight across all tokens)

| Strategy | AnnRet | Vol | Sharpe | Notes |
|----------|--------|-----|--------|-------|
| S11 Momentum Burst | +1.82% | 0.70% | **2.58** | Best overall |
| S09 Optimized Trend | +1.39% | 0.70% | 1.98 | Strong #2 |
| S17 Trend Strength | +1.18% | 0.65% | 1.81 | 0.86 corr with S11 — redundant |
| S21 Skew Momentum | +0.60% | 0.45% | 1.33 | Lower vol, decent Sharpe |
| S13 Vol-Weighted TSMOM | +0.52% | 0.71% | 0.73 | Mediocre |
| S18 Momentum Accel | +0.31% | 0.49% | 0.63 | Weakest, but most independent |

## Key Findings

### 1. S11 and S17 are the same bet (corr=0.86)
S11: `ret_1 > 0.03` + ADX > 20 + close > EMA20.
S17: `ret_1 > 0.02` + ADX > 25 + +DI > -DI.
Nearly identical entry signals. Running both doubles exposure without diversification.

### 2. S09 and S21 cluster together (corr=0.74)
Both are trend-following with EMA/regime filters. Different signals but capture the same market moves.

### 3. S18 is most independent (lowest avg correlation ~0.52)
Momentum acceleration (acceleration > threshold) fires at different times than simple momentum burst.
But it's also the weakest performer (Sharpe=0.63).

### 4. No strategy pair is below 0.47 correlation
The "diversified" target of -0.2 to +0.2 (from quant literature) is nowhere close.
All strategies are long-only momentum on the same asset class — structural correlation floor.

### 5. Rolling correlation varies significantly
S11 vs S17: min=0.36, max=0.96 (90-day rolling). In stress periods, convergence is near-total.
S18 vs S21: min=-0.07, max=0.90. Occasionally decorrelates but unreliable.

## Implications

1. **Running all 6 strategies ≈ running 2 strategies with 3x leverage.** Capital allocation
   across strategies must account for this.
2. **S17 should not run alongside S11** — it's the same signal with worse performance.
3. **For genuine diversification, we need fundamentally different strategy types:**
   - Mean reversion (different return profile from momentum)
   - Cross-sectional (relative ranking, not absolute signal)
   - Market-neutral (long/short pairs, hedged)
   - Different asset class exposure (perp funding rate, basis trade)
4. **Portfolio-level Sharpe will be much lower than per-strategy Sharpe** suggests.
   With avg correlation 0.62 and 6 strategies: Sharpe scaling factor ≈ sqrt(6) / sqrt(1 + 5*0.62) = 2.45 / 2.01 = 1.22x, not 2.45x.
