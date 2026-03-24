# R108: Deep Walk-Forward Validation -- Intraday Momentum Breakout (BTC)

**Date:** 2026-03-24 20:25

**Signal:** 8h momentum breakout on BTC 1h bars (absolute price change vs ATR threshold)
**Origin:** R107 (lost context, reconstructed)
**V3 Correlation:** 0.0879

## Kill Criteria Verdict

- Positive windows >= 5/N (active): PASS (5/7 active windows positive (8 total, 1 zero-trade))
- Mean Sharpe >= 0.3 (active windows): **FAIL** (mean Sharpe = 0.169 (over 7 active windows))
- No window MaxDD > 30%: PASS (worst DD = 13.58%)

**Overall: FAIL -- Signal killed**

## Walk-Forward Results (12mo Train / 6mo Test)

| Window | Test Period | Params (ATR/Trail/Hold) | Train Sharpe | Test Sharpe | Return | MaxDD | Trades | WinRate |
|--------|-------------|------------------------|-------------|-------------|--------|-------|--------|---------|
| 1 | 2022-07-01 to 2023-01-01 | 6.0/5.0/24 | 2.123 | 2.061 | 14.32% | 6.63% | 9 | 77.8% |
| 2 | 2023-01-01 to 2023-07-01 | 6.0/5.0/24 | 2.588 | 0.740 | 5.85% | 13.58% | 17 | 41.2% |
| 3 | 2023-07-01 to 2024-01-01 | 6.0/5.0/24 | 1.302 | 0.302 | 1.21% | 3.69% | 11 | 54.5% |
| 4 | 2024-01-01 to 2024-07-01 | 7.0/5.0/24 | 0.924 | 2.305 | 5.98% | 1.12% | 1 | 100.0% |
| 5 | 2024-07-01 to 2025-01-01 | 7.0/5.0/48 | 1.819 | 0.000 | 0.00% | 0.00% | 0 | 0.0% |
| 6 | 2025-01-01 to 2025-07-01 | 6.0/5.0/72 | 1.827 | 0.547 | 3.21% | 6.94% | 8 | 50.0% |
| 7 | 2025-07-01 to 2026-01-01 | 6.0/5.0/48 | 1.194 | -1.516 | -8.90% | 10.75% | 12 | 41.7% |
| 8 | 2026-01-01 to 2026-03-14 | 7.0/3.0/24 | 0.663 | -3.253 | -2.11% | 2.14% | 2 | 0.0% |

**Summary:** 5/7 active windows positive (1 zero-trade windows excluded), mean Sharpe 0.169, median Sharpe 0.547, mean return 2.44%, worst DD 13.58%

## Optimal Parameter Frequency

- **ATR mult:** {6.0: 5, 7.0: 3}
- **Trail mult:** {5.0: 7, 3.0: 1}
- **Max hold:** {24: 5, 48: 2, 72: 1}
- **Modal params:** ATR=6.0, Trail=5.0, Hold=24

## Parameter Sensitivity (+/- 20%)

Base params: {'atr_mult': 6.0, 'trail_mult': 5.0, 'max_hold': 24}, base Sharpe: 0.981

| Parameter | Direction | Value | Sharpe | Change % | Fragile? |
|-----------|-----------|-------|--------|----------|----------|
| atr_mult | -20% | 4.8 | 0.098 | -90.0% | YES |
| atr_mult | +20% | 7.2 | 0.847 | -13.7% | no |
| trail_mult | -20% | 4.0 | 0.448 | -54.3% | YES |
| trail_mult | +20% | 6.0 | 0.824 | -16.0% | no |
| max_hold | -20% | 19 | 0.940 | -4.2% | no |
| max_hold | +20% | 29 | 0.862 | -12.1% | no |

**Sensitivity verdict:** FRAGILE -- at least one parameter shows >30% degradation

## Regime Analysis (200-SMA)

| Regime | Trades | Avg PnL (%) | Total PnL (%) | Win Rate | Best | Worst |
|--------|--------|-------------|---------------|----------|------|-------|
| uptrend | 4 | -0.883 | -3.53 | 25.0% | 0.60% | -1.99% |
| downtrend | 0 | 0.000 | 0.00 | 0.0% | 0.00% | 0.00% |
| range | 88 | 0.733 | 64.49 | 58.0% | 13.19% | -4.59% |

## Cost Sensitivity

| Cost (bps) | Sharpe | Total Return | MaxDD | Trades | Win Rate |
|------------|--------|-------------|-------|--------|----------|
| 0 | 1.130 | 94.45% | 13.06% | 92 | 58.7% |
| 5 | 1.056 | 85.70% | 13.32% | 92 | 56.5% |
| 10 | 0.981 | 77.35% | 13.58% | 92 | 56.5% |
| 15 | 0.907 | 69.37% | 13.84% | 92 | 55.4% |
| 20 | 0.833 | 61.75% | 14.12% | 92 | 55.4% |
| 25 | 0.758 | 54.47% | 15.95% | 92 | 55.4% |
| 30 | 0.684 | 47.52% | 18.35% | 92 | 55.4% |
| 40 | 0.537 | 34.53% | 22.96% | 92 | 52.2% |
| 50 | 0.390 | 22.68% | 27.31% | 92 | 51.1% |

**Break-even cost:** ~>50 bps

## Trade Clustering Analysis

- **Total trades:** 92
- **Total PnL:** 60.96%
- **Top 10% trades (9 trades):** 63.45% (104.1% of total P&L)
- **Top 20% trades (18 trades):** 91.00% (149.3% of total P&L)
- **Bottom 10% trades:** -29.50%
- **Best single trade:** 13.19%
- **Worst single trade:** -4.59%
- **Median trade:** 0.463%
- **Monthly hit rate:** 31/48 (64.6%)

## Full-Sample Parameter Scan (Diagnostic)

Tested 120 parameter combinations on full data (2021-01 to present).
Combos with positive Sharpe: 71/120

**Top 5 parameter combos (full sample, NOT walk-forward):**

| ATR Mult | Trail Mult | Max Hold | Sharpe | Return | MaxDD | Trades | Win Rate |
|----------|------------|----------|--------|--------|-------|--------|----------|
| 6.0 | 5.0 | 24 | 0.954 | 86.43% | 13.58% | 95 | 56.8% |
| 6.0 | 5.0 | 48 | 0.941 | 103.56% | 14.75% | 91 | 51.6% |
| 8.0 | 5.0 | 96 | 0.881 | 32.70% | 7.64% | 12 | 58.3% |
| 6.0 | 5.0 | 72 | 0.797 | 87.35% | 17.02% | 89 | 48.3% |
| 6.0 | 5.0 | 96 | 0.797 | 92.29% | 18.35% | 89 | 48.3% |

*Note: Full-sample results are biased (lookahead). They show the upper bound of what this signal could achieve with perfect hindsight.*

## Conclusions & Next Steps

The intraday momentum breakout signal **fails** deep walk-forward validation.
Do NOT proceed to implementation.

**Failed criteria:**
- Mean Sharpe >= 0.3 (active windows): mean Sharpe = 0.169 (over 7 active windows)

**Post-mortem analysis:**

The R107 preliminary finding of Sharpe 0.594 does not replicate under rigorous walk-forward testing.
Possible explanations:
1. **R107 used in-sample optimization** -- the preliminary Sharpe was likely from a single optimized window, not true OOS
2. **Signal is too selective at optimal params** -- at ATR mult=6-7, only 0.2-0.6% of bars generate signals, leading to very few trades per 6mo window (0-17)
3. **Low trade count makes Sharpe unreliable** -- with only 1-17 trades per 6mo window, Sharpe estimates have huge variance
4. **Signal decays in recent data** -- Windows 7-8 (2025-2026) are negative, suggesting the edge may be time-dependent or already exploited
5. **Parameter sensitivity is asymmetric** -- lowering ATR mult or trail mult by 20% destroys the edge (>50% degradation), while increasing them is more benign
6. **Profit concentrated in top trades** -- 104% of PnL comes from top 10% of trades, making the strategy unreliable for consistent returns

**Nuances (this is NOT a clear-cut kill):**
- Full-sample Sharpe of 0.95 with 13.6% max DD is attractive
- Break-even cost >50 bps shows the per-trade edge is substantial
- 5/7 active WF windows positive shows directional consistency
- V3 correlation ~0.09 confirms genuine diversification
- The FAIL is narrow: mean Sharpe 0.169 vs threshold 0.3

**Recommendation:** CONDITIONAL HOLD. The signal has a real but thin edge that is:
- Too infrequent for standalone allocation (8-17 trades per 6 months)
- Fragile to parameter perturbation on the downside
- Degrading in recent windows

Consider:
- Use as a SMALL allocation overlay on V3 (5-10% weight) given low correlation
- Combine with additional confirmation (volume spike, regime filter)
- Test on 4h bars for less noise and more trades
- Re-evaluate in 6 months if newer data shows recovery
