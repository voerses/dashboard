# R156: Intraday Momentum on BTC Perps

**Generated:** 2026-03-28 17:52 UTC
**Data:** BTC perpetual swap 1h OHLCV, 2020-01-01 to 2026-03-17
**Costs:** 7 bps per side
**Funding:** Applied per hour from parquet data based on position direction

## Hypothesis

BTC shows intraday momentum -- when the first N hours of the day are positive, the rest of the day tends to follow. This is a well-documented effect in equity markets. Combined with leveraged execution on perpetual swaps, this could generate high-turnover returns.

## Strategy Variants

### Variant A: Opening Range Breakout
- Compute HIGH and LOW of first 4 hours (00:00-03:00 UTC)
- If price breaks above 4h high: go LONG until 22:00 UTC
- If price breaks below 4h low: go SHORT until 22:00 UTC
- If neither breakout: FLAT for the day
- Force close at end of day

### Variant B: 4-Hour Momentum
- Every 4 hours (00, 04, 08, 12, 16, 20 UTC), compute last 4h return
- If 4h return > 0: LONG for next 4 hours
- If 4h return < 0: SHORT for next 4 hours
- Max 6 trades per day

### Variant C: 8-Hour Session Momentum
- Every 8 hours (00, 08, 16 UTC), look at previous 8h return
- If return > 0: LONG for next 8h session
- If return < 0: SHORT for next 8h session
- Max 3 trades per day

---
## Variant A: Opening Range Breakout -- Full Period Results

| Metric | 1x | 2x | 3x |
|--------|---:|---:|---:|
| Annual Return | -26.7% | -59.5% | -83.1% |
| Gross Annual Return | +21.2% | +11.0% | -23.3% |
| Max Drawdown | -97.0% | -100.0% | -100.0% |
| Sharpe | -0.50 | -0.56 | -0.52 |
| Calmar | -0.28 | -0.59 | -0.83 |
| Sortino | -0.57 | -0.63 | -0.59 |
| Annual Vol | 53.3% | 106.5% | 159.8% |
| Trades/Year | 359 | 359 | 359 |
| Win Rate | 46.1% | 46.1% | 46.1% |
| Avg Trade | -0.049% | -0.098% | -0.147% |
| Avg Winner | +1.926% | +3.852% | +5.778% |
| Avg Loser | -1.737% | -3.474% | -5.211% |
| Profit Factor | 0.95 | 0.95 | 0.95 |
| Total Cost Drag | 312.7% | 625.4% | 938.1% |
| Total Funding PnL | +0.57% | +1.13% | +1.70% |
| Total Return | -85.5% | -99.6% | -100.0% |

### Cost Analysis (Variant A)

| Metric | 1x | 2x | 3x |
|--------|---:|---:|---:|
| Gross Annual Return | +21.2% | +11.0% | -23.3% |
| Net Annual Return | -26.7% | -59.5% | -83.1% |
| Cost Drag (total period) | 312.7% | 625.4% | 938.1% |
| Funding PnL (total period) | +0.57% | +1.13% | +1.70% |

- **1x:** Annualized cost drag = 50.3%/yr, Annualized funding PnL = +0.09%/yr
- **2x:** Annualized cost drag = 100.7%/yr, Annualized funding PnL = +0.18%/yr
- **3x:** Annualized cost drag = 151.0%/yr, Annualized funding PnL = +0.27%/yr

---
## Variant B: 4-Hour Momentum -- Full Period Results

| Metric | 1x | 2x | 3x |
|--------|---:|---:|---:|
| Annual Return | -88.8% | -99.2% | -100.0% |
| Gross Annual Return | -40.4% | -75.9% | -93.5% |
| Max Drawdown | -100.0% | -100.0% | -100.0% |
| Sharpe | -1.41 | -0.79 | -0.53 |
| Calmar | -0.89 | -0.99 | -1.00 |
| Sortino | -1.88 | -1.05 | -0.71 |
| Annual Vol | 63.1% | 126.3% | 189.4% |
| Trades/Year | 1188 | 1188 | 1188 |
| Win Rate | 27.3% | 27.3% | 27.3% |
| Avg Trade | -0.165% | -0.331% | -0.496% |
| Avg Winner | +1.652% | +3.304% | +4.956% |
| Avg Loser | -0.848% | -1.697% | -2.545% |
| Profit Factor | 0.73 | 0.73 | 0.73 |
| Total Cost Drag | 1033.8% | 2067.7% | 3101.5% |
| Total Funding PnL | +1.94% | +3.89% | +5.83% |
| Total Return | -100.0% | -100.0% | -100.0% |

### Cost Analysis (Variant B)

| Metric | 1x | 2x | 3x |
|--------|---:|---:|---:|
| Gross Annual Return | -40.4% | -75.9% | -93.5% |
| Net Annual Return | -88.8% | -99.2% | -100.0% |
| Cost Drag (total period) | 1033.8% | 2067.7% | 3101.5% |
| Funding PnL (total period) | +1.94% | +3.89% | +5.83% |

- **1x:** Annualized cost drag = 166.4%/yr, Annualized funding PnL = +0.31%/yr
- **2x:** Annualized cost drag = 332.8%/yr, Annualized funding PnL = +0.63%/yr
- **3x:** Annualized cost drag = 499.2%/yr, Annualized funding PnL = +0.94%/yr

---
## Variant C: 8-Hour Session Momentum -- Full Period Results

| Metric | 1x | 2x | 3x |
|--------|---:|---:|---:|
| Annual Return | -49.7% | -82.9% | -96.1% |
| Gross Annual Return | +12.3% | -14.4% | -55.9% |
| Max Drawdown | -99.3% | -100.0% | -100.0% |
| Sharpe | -0.79 | -0.66 | -0.51 |
| Calmar | -0.50 | -0.83 | -0.96 |
| Sortino | -1.05 | -0.88 | -0.68 |
| Annual Vol | 62.8% | 125.6% | 188.4% |
| Trades/Year | 573 | 573 | 573 |
| Win Rate | 29.9% | 29.9% | 29.9% |
| Avg Trade | -0.081% | -0.161% | -0.242% |
| Avg Winner | +2.433% | +4.866% | +7.299% |
| Avg Loser | -1.152% | -2.304% | -3.456% |
| Profit Factor | 0.90 | 0.90 | 0.90 |
| Total Cost Drag | 498.0% | 996.1% | 1494.1% |
| Total Funding PnL | -0.89% | -1.78% | -2.67% |
| Total Return | -98.6% | -100.0% | -100.0% |

### Cost Analysis (Variant C)

| Metric | 1x | 2x | 3x |
|--------|---:|---:|---:|
| Gross Annual Return | +12.3% | -14.4% | -55.9% |
| Net Annual Return | -49.7% | -82.9% | -96.1% |
| Cost Drag (total period) | 498.0% | 996.1% | 1494.1% |
| Funding PnL (total period) | -0.89% | -1.78% | -2.67% |

- **1x:** Annualized cost drag = 80.2%/yr, Annualized funding PnL = -0.14%/yr
- **2x:** Annualized cost drag = 160.3%/yr, Annualized funding PnL = -0.29%/yr
- **3x:** Annualized cost drag = 240.5%/yr, Annualized funding PnL = -0.43%/yr

---
## Last 12 Months Performance (2025-03-17 to 2026-03-17)

### Variant A: Opening Range Breakout

| Metric | 1x | 2x | 3x |
|--------|---:|---:|---:|
| Annual Return | +19.1% | +23.9% | +12.5% |
| Max Drawdown | -46.5% | -73.0% | -87.2% |
| Sharpe | 0.52 | 0.32 | 0.11 |
| Calmar | 0.41 | 0.33 | 0.14 |
| Sortino | 0.62 | 0.39 | 0.14 |
| Trades | 361 | 361 | 361 |
| Win Rate | 46.5% | 46.5% | 46.5% |
| Profit Factor | 1.11 | 1.11 | 1.11 |

### Variant B: 4-Hour Momentum

| Metric | 1x | 2x | 3x |
|--------|---:|---:|---:|
| Annual Return | -83.6% | -97.8% | -99.8% |
| Max Drawdown | -86.3% | -98.4% | -99.8% |
| Sharpe | -1.93 | -1.13 | -0.77 |
| Calmar | -0.97 | -0.99 | -1.00 |
| Sortino | -2.74 | -1.60 | -1.09 |
| Trades | 1146 | 1146 | 1146 |
| Win Rate | 29.1% | 29.1% | 29.1% |
| Profit Factor | 0.69 | 0.69 | 0.69 |

### Variant C: 8-Hour Session Momentum

| Metric | 1x | 2x | 3x |
|--------|---:|---:|---:|
| Annual Return | -53.9% | -82.4% | -94.4% |
| Max Drawdown | -65.2% | -89.4% | -97.2% |
| Sharpe | -1.25 | -0.96 | -0.73 |
| Calmar | -0.83 | -0.92 | -0.97 |
| Sortino | -1.73 | -1.32 | -1.01 |
| Trades | 570 | 570 | 570 |
| Win Rate | 29.6% | 29.6% | 29.6% |
| Profit Factor | 0.80 | 0.80 | 0.80 |

---
## Monthly Returns (Last 24 Months, 1x Leverage)

### Variant A: Opening Range Breakout

| Year | Jan | Feb | Mar | Apr | May | Jun | Jul | Aug | Sep | Oct | Nov | Dec | YTD |
|------|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|
| 2024 |   -   |   -   |   -   | -24.3% | -1.0% | -9.8% | -8.7% | -1.7% | +2.6% | -14.9% | -10.8% | +11.0% | -57.7% |
| 2025 | -1.7% | +2.2% | +0.5% | +23.9% | +2.5% | +10.7% | -12.8% | -6.6% | -6.8% | +7.3% | -21.4% | -9.5% | -11.6% |
| 2026 | +13.7% | +13.5% | +6.4% |   -   |   -   |   -   |   -   |   -   |   -   |   -   |   -   |   -   | +33.5% |

### Variant B: 4-Hour Momentum

| Year | Jan | Feb | Mar | Apr | May | Jun | Jul | Aug | Sep | Oct | Nov | Dec | YTD |
|------|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|
| 2024 |   -   |   -   |   -   | -4.2% | -17.9% | -8.6% | -12.2% | -30.7% | -20.8% | -16.1% | -16.8% | -33.0% | -160.3% |
| 2025 | -15.4% | -14.4% | -7.1% | +2.0% | -30.6% | -27.0% | -2.6% | -19.7% | -19.0% | -13.1% | -3.0% | +0.8% | -149.1% |
| 2026 | -10.9% | -23.8% | -16.8% |   -   |   -   |   -   |   -   |   -   |   -   |   -   |   -   |   -   | -51.5% |

### Variant C: 8-Hour Session Momentum

| Year | Jan | Feb | Mar | Apr | May | Jun | Jul | Aug | Sep | Oct | Nov | Dec | YTD |
|------|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|
| 2024 |   -   |   -   |   -   | -27.8% | +15.0% | +6.7% | -7.6% | -3.0% | -13.0% | -2.7% | +9.0% | -10.7% | -34.2% |
| 2025 | -9.2% | -3.7% | -7.4% | -6.0% | -9.9% | -17.6% | +2.0% | +3.8% | -3.1% | -10.7% | +3.9% | -25.5% | -83.3% |
| 2026 | -13.8% | +14.9% | -1.2% |   -   |   -   |   -   |   -   |   -   |   -   |   -   |   -   |   -   | -0.1% |

---
## Cross-Variant Comparison (1x Leverage)

| Metric | Variant A | Variant B | Variant C |
|--------|----------:|----------:|----------:|
| Annual Return | -26.7% | -88.8% | -49.7% |
| Gross Annual Return | +21.2% | -40.4% | +12.3% |
| Max Drawdown | -97.0% | -100.0% | -99.3% |
| Sharpe | -0.50 | -1.41 | -0.79 |
| Calmar | -0.28 | -0.89 | -0.50 |
| Sortino | -0.57 | -1.88 | -1.05 |
| Trades/Year | 359 | 1188 | 573 |
| Win Rate | 46.1% | 27.3% | 29.9% |
| Profit Factor | 0.95 | 0.73 | 0.90 |
| Avg Trade | -0.0489% | -0.1655% | -0.0807% |

---
## Verdict & Analysis

**Best variant by Sharpe (1x):** Variant A (Sharpe = -0.50)

### Cost Impact

- **Variant A:** Gross +21.2% -> Net -26.7% (cost drag: 50.3%/yr, trades/yr: 359)
- **Variant B:** Gross -40.4% -> Net -88.8% (cost drag: 166.4%/yr, trades/yr: 1188)
- **Variant C:** Gross +12.3% -> Net -49.7% (cost drag: 80.2%/yr, trades/yr: 573)

### Leverage Analysis

**Variant A:**
  - 1x: Return -26.7%, MaxDD -97.0%, Sharpe -0.50
  - 2x: Return -59.5%, MaxDD -100.0%, Sharpe -0.56
  - 3x: Return -83.1%, MaxDD -100.0%, Sharpe -0.52

**Variant B:**
  - 1x: Return -88.8%, MaxDD -100.0%, Sharpe -1.41
  - 2x: Return -99.2%, MaxDD -100.0%, Sharpe -0.79
  - 3x: Return -100.0%, MaxDD -100.0%, Sharpe -0.53

**Variant C:**
  - 1x: Return -49.7%, MaxDD -99.3%, Sharpe -0.79
  - 2x: Return -82.9%, MaxDD -100.0%, Sharpe -0.66
  - 3x: Return -96.1%, MaxDD -100.0%, Sharpe -0.51

### Key Observations

1. **Variant B cost sensitivity:** With 6 trades/day, annualized cost drag is 166.4%/yr. Gross return -40.4% becomes -88.8% net. Costs destroy the edge.

2. **All variants have negative Sharpe at 1x.** Intraday momentum does not appear to be a reliable signal on BTC perps. The hypothesis is rejected.

3. **Recent vs Full Period:**
   - Variant A: Full Sharpe -0.50 vs L12M Sharpe 0.52
   - Variant B: Full Sharpe -1.41 vs L12M Sharpe -1.93
   - Variant C: Full Sharpe -0.79 vs L12M Sharpe -1.25

---
## Deep Edge Decomposition

### Benchmark
- **Buy-and-Hold BTC:** +45.6%/yr annualized (2020-01-01 to 2026-03-17, +932.7% total)
- BTC went from ~$7,172 to ~$74,058 over the period

### Gross Edge Analysis (Before Costs)

| Metric | Variant A | Variant B | Variant C |
|--------|----------:|----------:|----------:|
| Annualized gross edge | +33.3% | -32.5% | +31.2% |
| Avg hourly return (long bars) | +0.0146% | +0.0028% | +0.0099% |
| Avg hourly return (short bars) | -0.0035% | -0.0104% | -0.0030% |
| % time in position | 67.5% | 100.0% | 100.0% |
| % time long | 34.0% | 51.0% | 50.9% |
| % time short | 33.5% | 49.0% | 49.0% |
| Annual turnover (x equity) | 719x | 2,377x | 1,145x |
| Annualized cost drag (7bps/side) | 50.3%/yr | 166.4%/yr | 80.2%/yr |

### Root Cause Analysis

**Variant A (Opening Range Breakout):**
- Has a genuine gross edge of +33.3%/yr, driven entirely by the long side (+0.0146%/hr when long)
- The short side is a drag (-0.0035%/hr when short, meaning being short loses money in a BTC uptrend)
- HOWEVER, the 50.3%/yr cost drag overwhelms the 33.3% gross edge
- Only 67.5% of the time in position (32.5% flat when no breakout) -- this is actually good for capital efficiency
- **Verdict:** Weak but real directional edge exists. Cost-sensitive. Long-only variant might survive at lower costs.

**Variant B (4-Hour Momentum):**
- NEGATIVE gross edge of -32.5%/yr -- the 4h momentum signal is actually mean-reverting on BTC
- Being short 49% of the time in a secular bull market destroys returns
- 27.3% win rate confirms the signal is systematically wrong
- On top of the negative gross edge, costs add another 166%/yr
- **Verdict:** DEAD. Signal is anti-momentum at 4h frequency. BTC 4h returns mean-revert.

**Variant C (8-Hour Session Momentum):**
- Positive gross edge of +31.2%/yr, but weaker than Variant A despite being always in position
- Like Variant B, nearly 50/50 long/short split is heavily penalized in a bull market
- 80.2%/yr cost drag from constant flipping (every 8h) kills the edge
- 29.9% win rate indicates most individual trades lose (large winners offset many small losers)
- **Verdict:** DEAD. Edge too thin vs. cost structure at this frequency.

### Why the Hypothesis Fails on BTC

1. **BTC is not equities.** The intraday momentum effect documented in equity markets does not translate to crypto. BTC at sub-daily frequencies shows **mean-reversion**, not momentum (especially at 4h frequency).

2. **Secular bull market penalty.** All three variants spend roughly half the time short. In a market that went from $7k to $74k, being short 33-49% of the time is extremely costly. Even Buy-and-Hold returned +45.6%/yr.

3. **Cost structure is prohibitive.** At 7bps/side, any strategy making more than ~200 trades/year needs an enormous per-trade edge to survive. Variant B with 1,188 trades/year needs ~14bps edge per trade just to break even on costs. The actual signal delivers negative edge.

4. **Variant A has a nugget of alpha** (+33.3% gross, driven by breakout-long), but it is entirely consumed by 50.3%/yr in costs. A long-only variant with 2-3bps execution costs might be viable, but that requires market-making or sub-penny execution.

### Recommendation

**KILL all three variants in current form.** No variant produces positive net returns after realistic costs.

**Potential follow-up research:**
- **Long-only Variant A** at reduced cost assumptions (maker rebates, smart execution)
- **Daily momentum** (instead of intraday) to reduce trade frequency
- **Regime filter** -- only trade Variant A during high-volatility regimes where breakouts are larger
- **Asymmetric sizing** -- full size long, reduced short (or skip shorts entirely)
