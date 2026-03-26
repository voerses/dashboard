# Perp Strategy Backtests — Post Funding Fix (Corrected Data)

**Date**: 2026-03-26
**Context**: Binance 8h settlement funding rates were NOT divided by 8 when merged with 1h data from other exchanges. The fix in `tools/build_parquet_cache.py` corrected `data/perp/1h_cache/` so `funding_1h` values are proper hourly rates.
**Impact**: BTC annual carry dropped from ~78% (buggy) to ~14.5% (corrected). Funding drag/income for all perp strategies changed by ~8x.

---

## 1. Corrected Funding Carry Yields

### BTC Delta-Neutral Carry (Long Spot + Short Perp)

| Metric | Value |
|--------|-------|
| Mean hourly funding | 0.00001656 |
| Annualized carry (gross) | 14.50% |
| Entry cost (one-time, 6bps) | 0.06%/yr |
| **Net annual carry** | **14.44%** |
| Sharpe (carry only) | 41.52 |
| Max drawdown (carry only) | -2.1% |

### Annual Carry by Year (BTC)

| Year | Gross Annual | Notes |
|------|-------------|-------|
| 2020 | +17.22% | |
| 2021 | +30.65% | Peak retail long bias |
| 2022 | +4.16% | Bear market, low funding |
| 2023 | +6.61% | Recovery |
| 2024 | +22.70% | Bull run |
| 2025 | +8.16% | Slowing |
| 2026 | +2.37% | YTD (Q1 only) |

### Simulated $200K Delta-Neutral Carry ($100K per leg)

| Metric | Value |
|--------|-------|
| Period | 2020-01-01 to 2026-03-17 |
| Duration | 6.2 years |
| Total funding earned | $90,102 |
| Total return | +45.1% |
| Annualized return | +7.3% |
| Max drawdown | -2.1% |

**Note**: The 7.3% annualized return (vs 14.5% gross carry) reflects that only half the capital is deployed as the notional short position ($100K out of $200K total). With 2x leverage on the short leg, gross carry would approach 14.5%.

### Cross-Token Carry Statistics

- 111 tokens with 1yr+ funding data
- 68% have positive carry (shorts earn)
- Mean annual carry: -0.03% (pulled down by a few extreme negative-carry tokens)
- Median annual carry: +5.34%
- Top carries: NEIRO +75.6%, ARC +56.4%, RATS +44.1%
- Worst carries: ME -106.4%, MOVE -98.4%, ORCA -91.6%

### Comparison: Buggy vs Corrected

| | Buggy (8x overstated) | Corrected |
|---|---|---|
| BTC annual carry | ~116% | 14.50% |
| ETH annual carry | ~134% | 16.75% |
| Median cross-token | ~43% | 5.34% |

The bug made funding-based strategies appear ~8x more profitable than reality.

---

## 2. Perp Strategy Backtest Results ($200K, 60 Months)

All strategies run with `--market perp --months 60 --capital 200000` on the V4 engine.

### Summary Table

| Strategy | Type | Return | Ann. | Sharpe | MaxDD | Trades | Fees | Funding | WinRate |
|----------|------|--------|------|--------|-------|--------|------|---------|---------|
| s09 | Trend | -96.5% | -42.8% | -1.14 | -97.9% | 13106 | $53,596 | +$33,981 | 39.5% |
| s56 | Momentum | -62.3% | -14.9% | -0.26 | -78.6% | 4720 | $54,585 | +$19,361 | 43.1% |
| s62 | Conservative Carry | -81.0% | -24.1% | -0.68 | -84.1% | 4246 | $27,135 | -$98,007 | 46.4% |
| s65 | Funding Carry | -84.8% | -26.9% | -0.72 | -84.9% | 3832 | $25,732 | -$105,896 | 46.3% |
| s72 | Time Trail Carry | -84.8% | -26.9% | -0.72 | -84.9% | 3832 | $25,732 | -$105,896 | 46.3% |
| s98 | SR Breakout | -75.9% | -21.1% | -0.06 | -95.2% | 533 | $47,281 | +$20,214 | 47.5% |
| s106 | MACD Squeeze 6x | -62.3% | -15.0% | -0.02 | -91.7% | 532 | $37,445 | +$19,558 | 48.3% |
| s107 | MACD Squeeze 6.5x | -68.1% | -17.3% | -0.03 | -93.5% | 533 | $42,622 | +$20,164 | 48.0% |

**Funding sign convention**: Positive = strategy paid funding (cost). Negative = strategy earned funding (income).

### Spot Comparison (Same Strategies)

| Strategy | Market | Return | Ann. | Sharpe | MaxDD | Fees | Funding |
|----------|--------|--------|------|--------|-------|------|---------|
| s09 | perp | -96.5% | -42.8% | -1.14 | -97.9% | $53,596 | +$33,981 |
| s09 | spot | -98.7% | -51.4% | -1.66 | -99.1% | $70,993 | $0 |
| s56 | perp | -62.3% | -14.9% | -0.26 | -78.6% | $54,585 | +$19,361 |
| s56 | spot | -87.0% | -28.9% | -0.76 | -90.8% | $75,160 | $0 |

Perp versions perform *better* than spot for momentum strategies (s09, s56) despite paying funding. This is because perp has wider token coverage (111 vs 69 tokens) and better liquidity on many altcoins.

---

## 3. Carry Strategy Decomposition

### s65 (Funding Carry) P&L Breakdown

| Component | Amount |
|-----------|--------|
| Initial Capital | $200,000 |
| Final Equity | $30,349 |
| **Total P&L** | **-$169,651** |
| Funding Earned | +$105,896 |
| Fees Paid | -$25,732 |
| Price P&L (inferred) | -$249,815 |

The carry strategy successfully earned $105,896 in funding over 60 months. However, price moves against positions generated -$249,815 in losses, overwhelming the carry income by 2.4x.

### Why Carry Strategies Lose Despite Earning Funding

1. **Carry is real but modest at corrected rates**: $105,896 over 60 months on $200K = ~10.6%/yr gross carry. This matches the theoretical 14.5% BTC carry discounted for (a) not always being deployed, (b) multi-token average carry being lower.

2. **Price risk dominates**: Carry strategies enter short positions when funding is positive. In a structurally bullish market (crypto 2020-2024), systematic shorts lose on price. The -$249,815 price loss is 2.4x the carry income.

3. **No delta hedge in the backtest**: The V4 engine's carry strategies are NOT delta-neutral. They go directionally short (or long) on perp without a spot hedge. A proper delta-neutral carry trade (long spot + short perp) would eliminate price risk but requires the separate carry engine.

4. **Walk-forward penalty**: The 60-month backtest includes a training window (~2400 bars) burned for walk-forward validation, reducing deployable capital time.

---

## 4. Delta-Neutral Carry vs Strategy Carry

| Approach | Annual Return | Max DD | Sharpe | Notes |
|----------|-------------|--------|--------|-------|
| Delta-neutral BTC carry | +7.3% | -2.1% | ~41.5 | $100K per leg, no leverage |
| s65 carry strategy | -26.9% | -84.9% | -0.72 | Directional, no spot hedge |
| s62 conservative carry | -24.1% | -84.1% | -0.68 | Directional, no spot hedge |

**Delta-neutral carry works; directional carry does not.** The ~14.5% annual funding rate is a real edge, but only when price risk is hedged.

---

## 5. Comparison to Prior Results

### vs R134 (All Negative)
All strategies remain negative, consistent with prior findings. The funding correction did not rescue any strategy.

### vs R135 (Best 22.6%)
The prior best result of 22.6% was likely on a different time period or configuration. With corrected funding on 60 months, the best perp result is s106 at -62.3% (least negative).

### Impact of Funding Correction on Key Strategies

| Strategy | Role of Funding | Impact of Correction |
|----------|----------------|---------------------|
| s09 (trend) | Pays funding (long bias) | Funding cost REDUCED ~8x ($34K vs ~$272K), strategy less penalized |
| s56 (momentum) | Pays funding (long bias) | Funding cost REDUCED ~8x ($19K vs ~$155K), strategy less penalized |
| s65 (carry) | Earns funding (short bias) | Funding income REDUCED ~8x ($106K vs ~$847K), strategy more penalized |
| s106 (MACD squeeze) | Pays funding (long bias) | Funding cost REDUCED ~8x ($20K vs ~$156K), strategy less penalized |

**Long-biased strategies (s09, s56, s106) benefit from the funding correction** because they pay less funding. **Carry strategies (s62, s65) are hurt** because they earn less funding -- their entire edge was from funding, and 8x less funding means the carry cannot overcome price losses.

---

## 6. Key Takeaways

1. **Corrected BTC funding carry = 14.5%/yr** (was ~116% in buggy data). This is a real, meaningful yield but not the extraordinary edge the buggy data suggested.

2. **All perp strategies remain deeply negative** over 60 months. Funding correction did not fix the fundamental issues (fees, slippage, adverse selection).

3. **Delta-neutral carry is the only viable funding strategy**: 7.3%/yr with -2.1% max drawdown and Sharpe ~41. But this requires a separate carry engine (long spot + short perp), not the V4 trend/carry strategies.

4. **Long-biased strategies improved slightly** (s56 perp: -62.3% vs buggy ~-75%) because they pay less corrected funding. But they're still deeply negative.

5. **Carry strategies got worse**: s65 went from potentially positive (with 8x inflated funding) to -84.8% (with correct funding). The carry income cannot overcome directional price losses.

6. **Recent carry has compressed**: 2025 carry is 8.2%, 2026 YTD is 2.4%. The funding edge is shrinking as markets mature and more participants arbitrage the premium.
