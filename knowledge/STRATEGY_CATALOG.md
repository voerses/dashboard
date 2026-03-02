# Strategy Catalog — Compact Reference

> **TL;DR** Trend following is the only consistent edge in crypto at swing timeframes.
> S09 (dual momentum) and S11 (momentum burst) are Tier A production strategies.
> Mean reversion loses money at 18-720hr holds. Simple beats complex (4 conditions > 10).
> **Full details + 67 citations:** `knowledge/archive/STRATEGY_CATALOG_DETAILS.md`

**Context:** Crypto swing trading, $200K capital, 1H timeframe, 18-720hr holds, 6-49 tokens
**Last validated:** February 2026, 49 tokens, Jan 2024 - Jan 2026 data

---

## Production Strategies (Tier A)

### S09 Optimized Trend (Dual Momentum)

| Metric | Value |
|--------|-------|
| Annual PnL | +$162,898/yr (all 49), +$55,408/yr (CPCV 11) |
| Win rate / Payoff | 38-45% / 1.8-2.4x |
| Trade freq | 500-900/yr across token universe |
| Validation | CPCV (PBO < 40%), walk-forward |
| Dual-validated tokens | SUI, TRX, BONK, FLOKI |

**Entry logic:** Daily uptrend (EMA50 + ADX>20 + 12d momentum>0) + 4H pullback to EMA20 + 1H volume burst + RSI 30-55. Multi-timeframe stack (1H:4H:Daily) is critical.

**Based on:** Antonacci (2014) dual momentum + Moskowitz et al. (2012) TSMOM. Crypto-adapted with shorter lookbacks (10-28d vs 12mo) because crypto cycles are faster. Borgards (2021) confirms longer momentum periods in crypto.

### S11 Momentum Burst

| Metric | Value |
|--------|-------|
| Annual PnL | +$170,240/yr (all 49), +$62,705/yr (CPCV 11) |
| Win rate / Payoff | 46-47% / 1.81-1.94x |
| Trade freq | 600-2,400/yr across token universe |
| Validation | CPCV (PBO < 40%), walk-forward |
| Dual-validated tokens | PENGU, SUI, AVAX, BONK, FLOKI, ZRO |

**Entry logic:** Explosive hourly move (>3% in one bar) + trend context (ADX>20, price > EMA20). 24-bar protection window (no stop for 24hrs). Internal development, related to Jegadeesh & Titman (1993) momentum persistence.

### V3 Liquidity Contrarian (Complement)

| Metric | Value |
|--------|-------|
| Annual PnL | +$8,000/yr |
| Win rate | 63% (but only 108 trades in 2 years) |
| Role | Low-correlation complement to S09/S11 |

### HMM Regime Detection (Overlay)

| Metric | Value |
|--------|-------|
| Regime split | 52% range, 19% uptrend, 18% quiet, 10% downtrend, 1% crisis |
| Implementation | `regime_detector.py`, 2-3 state HMM |
| Effect | Drives allocation: 80-90% deployed bull, 10-20% bear |

Based on Hamilton (1989). Crypto-validated by Castellano & D'Ecclesia (2025).

---

## Priority Ranking — All Strategies

### Tier A: Production (Validated)

| # | Strategy | Status | Annual PnL |
|---|----------|--------|------------|
| 1 | S11 Momentum Burst | LIVE (#1) | +$170K |
| 2 | S09 Dual Momentum Trend | LIVE (#2) | +$163K |
| 3 | V3 Liquidity Contrarian | LIVE (complement) | +$8K |
| 4 | HMM Regime Detection | LIVE (overlay) | Integrated |

### Tier B: High Priority — Next to Implement

| # | Strategy | Signal type | Expected lift | Complexity |
|---|----------|-------------|---------------|------------|
| 1 | Vol scaling (Moreira & Muir 2017) | Sizing overlay | +0.3-0.5 Sharpe | Low |
| 2 | Momentum crash protection (Barroso 2015) | Sizing overlay | Better drawdowns | Low |
| 3 | DI crossover direction (Wilder 1978) | Entry filter | Higher win rate | Low |
| 4 | Volume-weighted TSMOM (Huang 2024) | Signal variant | Sharpe 2.17 (lit.) | Medium |
| 5 | KAMA adaptive MA (Kaufman 1995) | Signal variant | Fewer whipsaws | Medium |
| 6 | Meta-labeling (Lopez de Prado 2018) | Entry filter | +10-20% hit rate | Medium |

### Tier C: Worth Testing

| # | Strategy | Signal type | Expected lift | Complexity |
|---|----------|-------------|---------------|------------|
| 1 | Dynamic strategy allocation by regime | Allocation | +15-30% Sharpe | Medium |
| 2 | RS-based token selection | Token filter | Better concentration | Low |
| 3 | Sector rotation overlay | Allocation | Capture altseason | Medium |
| 4 | Pyramiding (Turtle-style adds) | Sizing | Larger trend capture | Medium |
| 5 | Channel breakout (ATR-based) | Signal | Replace failed vol_breakout | Medium |
| 6 | CUSUM structural break filter | Entry filter | Fewer noise trades | Medium |
| 7 | Fractional differentiation | Feature eng. | Better ML inputs | Medium |

---

## Strategy Graveyard (Tier D: Do Not Implement)

| Strategy | Category | Reason for rejection | Our result |
|----------|----------|---------------------|------------|
| BB mean reversion (any variant) | Mean reversion | Loses -$25K to -$185K/yr at swing TF | FAILED |
| RSI extremes (standalone) | Mean reversion | IC=0.004 standalone; MR loses at swing TF | FAILED |
| Vol breakout (BB squeeze) | Volatility | -$4,220/yr; squeeze too frequent, 55% fakeout | FAILED |
| TTM Squeeze | Volatility | Same problems as vol breakout; weak academic support | NOT TESTED |
| Pairs trading | Cross-sectional | Requires shorting; crypto correlations unstable | N/A |
| OU-based strategies | Mean reversion | Crypto is momentum-driven, fails OU model test | N/A |
| Cross-sectional momentum | Cross-sectional | "Weak and unreliable" in crypto (Han 2023) | N/A |
| Order book imbalance | Microstructure | Signal horizon minutes, too short for swing | N/A |
| RSI-2 / Connors strategies | Mean reversion | Too short-term; RSI IC=0.004 in crypto | N/A |
| Straddle-like (spot) | Volatility | Requires long+short; spot-only limitation | N/A |
| Vol risk premium harvesting | Volatility | Requires options/perp infrastructure | N/A |
| Deep RL for signals | ML | High complexity, low OOS reliability, catastrophic forgetting | N/A |
| Transformer prediction | ML | Insufficient data, non-stationarity, low interpretability | N/A |
| GNN token relationships | ML | Unstable graph structure, emerging/unproven | N/A |
| V2 Daily Momentum | Trend | Too slow; -$13,806/yr (golden cross lag) | FAILED |
| VPIN filter (current data) | Microstructure | Hurt performance; data quality issue (0.5 fills) | FAILED |

---

## Key Research Findings

### What Works in Crypto (Swing TF)

| Finding | Evidence | Implication |
|---------|----------|-------------|
| ADX is #1 predictor | IC=0.067, increases with horizon | Keep ADX>20 as core filter |
| Multi-TF stack beats single-TF | S09/S11 use 1H:4H:Daily | Never reduce to single timeframe |
| Simple beats complex | S11 (4 conditions) 75.5% vs S16 (10 conditions) 0% | Cap entry conditions at 4-5 |
| Momentum > everything else | Only profitable strategy type at swing TF | All production strategies are trend-based |
| Regime filtering is essential | Momentum works bull/neutral, fails bear | Always use regime overlay |
| 3x ATR stop is optimal | Parameter sweep finding | Don't change stop multiplier |
| TSMOM lookback 10-28d | Han (2023): 28d lookback, Sharpe 1.51 | Crypto cycles faster than equities |
| Vol-weighted TSMOM promising | Huang (2024): Sharpe 2.17 | Untested in our system; Tier B priority |

### What Fails in Crypto (Swing TF)

| Finding | Evidence | Why |
|---------|----------|-----|
| Mean reversion | All 70-sweep MR strategies lose | Hold period mismatch; MR needs 1-day holds or no stops |
| RSI as standalone | IC=0.004 | Near-zero predictive power alone |
| BB squeeze breakout | -$4,220/yr | Too frequent (160/760 days), 55% false breakout rate |
| Complex entry logic | S16 at 0% survival | More conditions = more overfit paths = worse OOS |
| Stop-losses on MR | Beluska & Vojtko (2024) | Stops destroy MR performance; conflicts with risk mgmt |

### RSI Regime Dependency

RSI behaves opposite to textbook expectations depending on regime:

| Regime | High RSI predicts | Low RSI predicts | Implication |
|--------|-------------------|------------------|-------------|
| Uptrend | MORE upside | Dip-buy opportunity | Use RSI as momentum confirmation |
| Downtrend | MORE downside | Nothing useful | Fade bounces, not buy dips |
| Range | Mean reversion (weak) | Mean reversion (weak) | Only regime where textbook RSI works |

### Top 2-Indicator Combinations (1-day horizon)

| Combination | Mean return | Win rate | Token count |
|-------------|-----------|----------|-------------|
| RSI_low + MACD_pos | +1.37% | 58.3% | -- |
| vol_ratio_hi + BB_pct_low | +0.99% | 58.4% | 45 tokens |
| Taker > 0.55 + positive daily return | +1.18% | 52.1% | 19 tokens |

---

## Regime Model Overview

Our HMM detects 5 regimes that drive all allocation decisions:

| Regime | Frequency | Action | Deployment |
|--------|-----------|--------|------------|
| Uptrend | 19% | Full momentum (S09+S11) | 80-90% |
| Range | 52% | Reduced exposure, trend-only | 40-60% |
| Quiet | 18% | Selective, wait for breakout | 30-50% |
| Downtrend | 10% | Cash/minimal | 10-20% |
| Crisis | 1% | Circuit breaker: -15% half, -25% cash | 0% |

**Key insight:** 52% of time is range regime where momentum signals are weak. Capturing this dead time is the biggest improvement opportunity (Tier C #1: dynamic strategy allocation).

---

## Unexplored High-Value Techniques (from Lopez de Prado 2018)

| Technique | Chapter | What it does | Priority |
|-----------|---------|-------------|----------|
| Meta-labeling | Ch. 3 | Secondary model predicts if S09/S11 signal will win | Tier B |
| Fractional differentiation | Ch. 5 | Stationary features that preserve long-term memory | Tier C |
| CUSUM filter | Ch. 17 | Trade only after structural breaks (regime changes) | Tier C |
| Triple barrier labeling | Ch. 3 | Already implemented (stop/trail/max hold) | DONE |
| CPCV | Ch. 12 | Already implemented (primary validation) | DONE |

---

## Research Principles

1. **CPCV is the gold standard** for strategy validation. PBO < 40% required.
2. **Harvey t-stat > 3.0** threshold for any new signal (multiple testing correction for 70+ strategies tested).
3. **Simple dominates complex** at every validation level we have tested.
4. **Features that survive costs** are always the same: momentum, volatility, volume, trend strength (ADX). Exotic features (sentiment, NLP, alternative data) rarely survive. (Gu et al. 2020)
5. **Ensemble > single strategy.** S09 + S11 together diversify better than either alone. Target: 60% S11 + 40% S09 allocation.

*Full strategy descriptions, parameter tables, and 67 academic citations archived in `knowledge/archive/STRATEGY_CATALOG_DETAILS.md`.*
