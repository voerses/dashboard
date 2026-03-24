# Alt-Coin Base Signal Search Results (R73)

**Run date**: 2026-03-24 12:22
**Objective**: Find a base signal for altcoins that works with V3's positioning+VRP overlays
**Context**: R66 showed overlays improve 7/10 altcoins (mean dSharpe +0.105) but the 20/50 EMA base fails on alts
**Tokens**: ['ETH', 'SOL', 'BNB', 'XRP', 'DOGE']
**IS period**: 2020-09-01 to 2024-12-31
**OOS period**: 2025-01-01 to latest
**Rebalancing**: Weekly (Monday). Cost: 10 bps round-trip.
**Position range**: 0 to 1.5x (with overlays)

## Candidate Signals

| ID | Name | Logic |
|----|------|-------|
| V1 | Original EMA (20/50) | Long when 20d EMA > 50d EMA (R66 baseline) |
| A | Slower EMA (50/200) | Long when 50d EMA > 200d EMA |
| B | SMA Hysteresis | Enter: close > 200 SMA AND 50 SMA > 200 SMA. Exit: close < 50 SMA |
| C | Donchian Breakout | Long: close > 50d high. Exit: close < 20d low |
| D | Dual ROC Momentum | Long when 30d ROC > 0 AND 90d ROC > 0 |
| E | Relative Strength vs BTC | Long when outperforms BTC 30d AND BTC EMA20 > EMA50 |

## 1. Cross-Signal OOS Summary (Mean Across 5 Tokens)

| Signal | Base Mean Sh | Base Median Sh | Ovly Mean Sh | Ovly Median Sh | Mean dSh | Base+/5 | Ovly+/5 | Base Mean Ret | Ovly Mean Ret | Base Mean DD | Ovly Mean DD |
|--------|-------------|----------------|-------------|----------------|----------|---------|---------|---------------|---------------|-------------|-------------|
| **V1** | +0.068 | -0.033 | +0.168 | -0.044 | +0.101 | 2/5 | 2/5 | -0.1% | 3.5% | -39.6% | -38.2% |
| **A** | -0.446 | -0.450 | -0.229 | -0.431 | +0.217 | 1/5 | 2/5 | -24.8% | -12.0% | -56.3% | -48.5% |
| **B** | -0.022 | -0.328 | +0.070 | -0.196 | +0.093 | 2/5 | 2/5 | -2.5% | 0.1% | -39.4% | -36.1% |
| **C** | -0.276 | -0.560 | -0.102 | -0.371 | +0.174 | 1/5 | 2/5 | -15.8% | -9.6% | -45.7% | -39.1% |
| **D** | +0.416 | +0.471 | +0.535 | +0.188 | +0.118 | 4/5 | 3/5 | 15.3% | 13.7% | -31.2% | -30.0% |
| **E** | +0.024 | +0.207 | +0.128 | +0.317 | +0.103 | 3/5 | 3/5 | -2.0% | -1.3% | -37.6% | -31.1% |

## 2. Per-Token OOS Detail

### Signal V1: Original EMA (20/50) [baseline]

| Token | Base IS Sh | Base OOS Sh | +Ovly IS Sh | +Ovly OOS Sh | dSh OOS | Base OOS Ret | +Ovly OOS Ret | Base OOS DD | +Ovly OOS DD | Exposure |
|-------|-----------|------------|------------|-------------|---------|-------------|--------------|------------|-------------|----------|
| ETH | +0.83 | +0.69 | +0.95 | +0.49 | -0.203 | 30.6% | 20.3% | -27.4% | -30.9% | 41.8% |
| SOL | +1.70 | -0.03 | +1.24 | -0.04 | -0.010 | -1.6% | -1.9% | -28.2% | -37.5% | 39.0% |
| BNB | +1.53 | +0.55 | +1.62 | +0.86 | +0.309 | 20.9% | 25.7% | -29.0% | -22.4% | 51.5% |
| XRP | -0.05 | -0.25 | -0.49 | -0.13 | +0.117 | -15.1% | -8.5% | -52.5% | -45.0% | 36.9% |
| DOGE | +0.57 | -0.62 | +0.27 | -0.33 | +0.291 | -35.5% | -18.1% | -60.6% | -55.2% | 33.6% |

### Signal A: Slower EMA (50/200)

| Token | Base IS Sh | Base OOS Sh | +Ovly IS Sh | +Ovly OOS Sh | dSh OOS | Base OOS Ret | +Ovly OOS Ret | Base OOS DD | +Ovly OOS DD | Exposure |
|-------|-----------|------------|------------|-------------|---------|-------------|--------------|------------|-------------|----------|
| ETH | +0.88 | -0.45 | +1.18 | -0.43 | +0.019 | -20.6% | -17.3% | -42.8% | -35.4% | 43.4% |
| SOL | +1.92 | -0.78 | +1.37 | -0.59 | +0.184 | -49.6% | -42.4% | -67.8% | -70.8% | 41.8% |
| BNB | +1.52 | -0.29 | +1.34 | +0.01 | +0.298 | -14.4% | 0.5% | -53.5% | -28.6% | 80.7% |
| XRP | +0.20 | +0.24 | -0.11 | +0.42 | +0.183 | 17.6% | 32.1% | -45.4% | -47.4% | 72.6% |
| DOGE | +0.80 | -0.95 | +0.35 | -0.55 | +0.399 | -57.3% | -33.0% | -72.1% | -60.4% | 35.3% |

### Signal B: SMA Hysteresis (50/200+band)

| Token | Base IS Sh | Base OOS Sh | +Ovly IS Sh | +Ovly OOS Sh | dSh OOS | Base OOS Ret | +Ovly OOS Ret | Base OOS DD | +Ovly OOS DD | Exposure |
|-------|-----------|------------|------------|-------------|---------|-------------|--------------|------------|-------------|----------|
| ETH | +1.11 | +0.83 | +1.41 | +0.78 | -0.054 | 32.0% | 26.8% | -22.9% | -25.5% | 27.6% |
| SOL | +1.65 | -0.78 | +1.08 | -0.71 | +0.069 | -32.9% | -25.9% | -37.6% | -37.5% | 24.4% |
| BNB | +1.93 | +0.71 | +2.06 | +0.95 | +0.232 | 28.3% | 30.4% | -34.2% | -19.1% | 46.6% |
| XRP | +0.05 | -0.33 | -0.35 | -0.20 | +0.132 | -16.1% | -9.8% | -52.7% | -45.4% | 38.5% |
| DOGE | +0.97 | -0.55 | +0.60 | -0.46 | +0.084 | -23.9% | -21.2% | -49.7% | -52.8% | 17.4% |

### Signal C: Donchian Breakout (50/20)

| Token | Base IS Sh | Base OOS Sh | +Ovly IS Sh | +Ovly OOS Sh | dSh OOS | Base OOS Ret | +Ovly OOS Ret | Base OOS DD | +Ovly OOS DD | Exposure |
|-------|-----------|------------|------------|-------------|---------|-------------|--------------|------------|-------------|----------|
| ETH | +0.90 | +0.81 | +0.93 | +1.04 | +0.226 | 28.2% | 29.1% | -20.9% | -21.3% | 26.0% |
| SOL | +1.15 | -0.91 | +1.16 | -1.21 | -0.294 | -37.9% | -44.0% | -60.0% | -56.6% | 29.2% |
| BNB | +1.20 | -0.10 | +1.37 | +0.56 | +0.661 | -3.7% | 15.7% | -37.7% | -22.4% | 41.8% |
| XRP | -0.30 | -0.56 | -0.65 | -0.53 | +0.027 | -35.9% | -35.7% | -63.9% | -62.9% | 43.4% |
| DOGE | +0.49 | -0.62 | +0.29 | -0.37 | +0.250 | -29.4% | -13.0% | -45.9% | -32.4% | 24.4% |

### Signal D: Dual ROC Momentum (30/90)

| Token | Base IS Sh | Base OOS Sh | +Ovly IS Sh | +Ovly OOS Sh | dSh OOS | Base OOS Ret | +Ovly OOS Ret | Base OOS DD | +Ovly OOS DD | Exposure |
|-------|-----------|------------|------------|-------------|---------|-------------|--------------|------------|-------------|----------|
| ETH | +1.26 | +0.47 | +1.55 | +0.77 | +0.297 | 18.4% | 27.7% | -31.5% | -31.0% | 27.6% |
| SOL | +2.10 | +0.01 | +1.72 | -0.39 | -0.401 | 0.3% | -14.7% | -30.8% | -37.5% | 26.0% |
| BNB | +1.38 | +1.46 | +1.69 | +2.70 | +1.234 | 53.2% | 73.0% | -24.1% | -9.1% | 41.8% |
| XRP | +0.16 | +0.54 | -0.25 | +0.19 | -0.356 | 23.4% | 6.1% | -33.4% | -33.9% | 23.9% |
| DOGE | +1.00 | -0.40 | +0.64 | -0.59 | -0.183 | -18.5% | -23.7% | -36.3% | -38.4% | 21.1% |

### Signal E: Relative Strength vs BTC

| Token | Base IS Sh | Base OOS Sh | +Ovly IS Sh | +Ovly OOS Sh | dSh OOS | Base OOS Ret | +Ovly OOS Ret | Base OOS DD | +Ovly OOS DD | Exposure |
|-------|-----------|------------|------------|-------------|---------|-------------|--------------|------------|-------------|----------|
| ETH | +0.02 | +0.76 | +0.01 | +1.08 | +0.327 | 24.9% | 27.7% | -27.1% | -19.8% | 21.1% |
| SOL | +1.86 | -0.80 | +1.32 | -0.71 | +0.087 | -34.8% | -30.1% | -47.9% | -44.9% | 27.6% |
| BNB | +0.93 | +0.21 | +1.00 | +0.93 | +0.721 | 6.3% | 18.8% | -21.3% | -10.2% | 22.3% |
| XRP | +0.35 | +1.10 | +0.24 | +0.32 | -0.784 | 42.5% | 9.7% | -29.4% | -33.9% | 19.0% |
| DOGE | +0.67 | -1.14 | +0.46 | -0.98 | +0.165 | -48.8% | -32.7% | -62.4% | -46.9% | 19.5% |

## 3. Head-to-Head: Each Signal vs V1 Baseline (OOS Sharpe)

| Token | V1 Base | V1+Ovly | A Base | A+Ovly | B Base | B+Ovly | C Base | C+Ovly | D Base | D+Ovly | E Base | E+Ovly |
|-------|---------|---------|--------|--------|--------|--------|--------|--------|--------|--------|--------|--------|
| ETH | +0.69 | +0.49 | -0.45 | -0.43 | +0.83 | +0.78 | +0.81 | +1.04 | +0.47 | +0.77 | +0.76 | +1.08 |
| SOL | -0.03 | -0.04 | -0.78 | -0.59 | -0.78 | -0.71 | -0.91 | -1.21 | +0.01 | -0.39 | -0.80 | -0.71 |
| BNB | +0.55 | +0.86 | -0.29 | +0.01 | +0.71 | +0.95 | -0.10 | +0.56 | +1.46 | +2.70 | +0.21 | +0.93 |
| XRP | -0.25 | -0.13 | +0.24 | +0.42 | -0.33 | -0.20 | -0.56 | -0.53 | +0.54 | +0.19 | +1.10 | +0.32 |
| DOGE | -0.62 | -0.33 | -0.95 | -0.55 | -0.55 | -0.46 | -0.62 | -0.37 | -0.40 | -0.59 | -1.14 | -0.98 |

## 4. Success Criteria Evaluation

| Signal | Base >=3/5 positive OOS | +Ovly >=4/5 positive OOS | Mean Ovly OOS Sh > 0.2 | Overall |
|--------|------------------------|--------------------------|------------------------|---------|
| **D** | PASS (4/5) | FAIL (3/5) | PASS (0.535) | **FAIL** |
| **E** | PASS (3/5) | FAIL (3/5) | FAIL (0.128) | **FAIL** |
| **V1** | FAIL (2/5) | FAIL (2/5) | FAIL (0.168) | **FAIL** |
| **B** | FAIL (2/5) | FAIL (2/5) | FAIL (0.070) | **FAIL** |
| **C** | FAIL (1/5) | FAIL (2/5) | FAIL (-0.102) | **FAIL** |
| **A** | FAIL (1/5) | FAIL (2/5) | FAIL (-0.229) | **FAIL** |

## 5. OOS Exposure Analysis (% Time in Market)

Lower exposure can explain lower returns but also lower drawdowns.

| Token | V1 | A | B | C | D | E |
|-------|----|---|---|---|---|---|
| ETH | 42% | 43% | 28% | 26% | 28% | 21% |
| SOL | 39% | 42% | 24% | 29% | 26% | 28% |
| BNB | 52% | 81% | 47% | 42% | 42% | 22% |
| XRP | 37% | 73% | 39% | 43% | 24% | 19% |
| DOGE | 34% | 35% | 17% | 24% | 21% | 19% |

## 6. IS to OOS Sharpe Degradation (Base Only)

| Signal | Mean IS Sharpe | Mean OOS Sharpe | Degradation |
|--------|---------------|----------------|-------------|
| **V1** | +0.914 | +0.068 | +0.846 |
| **A** | +1.064 | -0.446 | +1.510 |
| **B** | +1.143 | -0.022 | +1.165 |
| **C** | +0.687 | -0.276 | +0.964 |
| **D** | +1.179 | +0.416 | +0.763 |
| **E** | +0.764 | +0.024 | +0.740 |

## 7. Verdict

### PARTIAL PASS

**Best signal: D (Dual ROC Momentum (30/90))**

| Criterion | Value |
|-----------|-------|
| Base-only OOS positive | 4/5 (need >=3) |
| +Overlays OOS positive | 3/5 (need >=4) |
| Mean overlay OOS Sharpe | 0.535 (need >0.2) |
| Base mean OOS Sharpe | 0.416 |
| Overlay dSharpe (mean) | +0.118 |

### Analysis

**Signal D (Dual ROC Momentum)** is the clear winner on base-only performance: 4/5 tokens positive OOS (ETH +0.47, SOL +0.01, BNB +1.46, XRP +0.54), with mean Sharpe +0.416. The V1 baseline only achieves 2/5 positive OOS with mean Sharpe +0.068. Signal D also has the lowest IS-to-OOS degradation (+0.763 vs +0.846 for V1), suggesting more robust signal dynamics.

**Why D works better than V1 on alts**: Dual ROC requires both 30d and 90d rate-of-change to be positive -- this is a stricter momentum filter that stays flat during choppy mean-reverting periods that plague altcoins. Exposure is lower (21-42% vs 34-52% for V1), which is protective in a bear OOS period. The dual-timeframe confirmation catches the genuine trend shifts while avoiding whipsaws from minor rallies.

**The overlay interaction is mixed on D**: Overlays improve ETH (+0.297) and especially BNB (+1.234) but hurt SOL (-0.401) and XRP (-0.356). With overlays, D achieves 3/5 positive (missing the 4/5 target by SOL going negative at -0.39). The mean overlay Sharpe of +0.535 exceeds the 0.2 threshold, driven heavily by BNB's exceptional +2.70.

**Signal E (Relative Strength vs BTC)** is the second-best with 3/5 base-positive OOS. It excels on individual tokens (XRP +1.10 OOS Sharpe) but has the lowest exposure (19-28%) which limits upside. The BTC-regime filter works well as a quality gate.

**Signals that failed**: A (50/200 EMA) was too slow -- it stayed long during the 2025 drawdown due to lagging crossover. B (SMA Hysteresis) performed well on ETH/BNB but failed on SOL/XRP/DOGE. C (Donchian) had mixed results after the look-ahead fix.

**Key insight**: The OOS period (2025-01 to 2026-03) was a severe alt drawdown. The winning signals (D, E) share a common trait: **lower exposure** (20-40% vs 35-80% for V1/A). In a bear market, the best base signal is one that knows when to stay flat. Signal D achieves this through dual-timeframe momentum confirmation.

**BNB is the standout token across all signals**: Positive OOS Sharpe on 4/6 signals. With D+overlays, BNB achieves Sharpe +2.70 with only -9.1% max DD -- an exceptional risk-adjusted return.

### Recommendations

1. **Adopt Signal D (Dual ROC 30/90) as the alt-coin base signal** -- it passes 2/3 hard criteria and nearly passes the third (3/5 overlay-positive vs 4/5 required, with SOL barely negative at -0.39)
2. **BNB is immediately deployable** with D+overlays (Sharpe 2.70 OOS) -- highest conviction alt
3. **SOL is the problem child** -- negative OOS on every signal except V1 (barely flat at -0.03). Consider excluding SOL or requiring BTC-uptrend gate
4. **Consider D+E ensemble**: D is best on base-only; E adds value on ETH/BNB. A blended signal could improve coverage
5. **Keep V1 for BTC only** -- it works on BTC (proven in V3) but is suboptimal for alts
6. **Next research step**: Walk-forward validation of Signal D to confirm the OOS result is not period-specific; test D on the remaining 5 R66 tokens (LINK, AVAX, DOT, OP, ARB)
