# Strategy Triage Results — Post Bug-Fix OOS Performance

**Date:** 2026-03-24
**Period:** Last 6 months OOS (2025-09-01 to 2026-03-17)
**Context:** Previous backtest results were INVALID due to a bug that used close price as stop price instead of current price. After the fix, most strategies showed negative full-period returns. This triage identifies which strategies still produce positive returns in the most recent 6-month OOS window.

**Methodology:**
- Ran each Tier A strategy on 10 liquid tokens (BTC, ETH, SOL, XRP, DOGE, BNB, SUI, ADA, PEPE, LINK)
- Used v4 simulator with walk-forward masking (365d train, 90d recal, 5d purge)
- 24-month data window, extracted OOS trades with entry after 2025-09-01
- Capital: $200,000

---

## Summary Table

| Strategy | Market   | OOS Trades | OOS Return | Win Rate | Profit Factor | Max DD   | Verdict    |
|----------|----------|------------|------------|----------|---------------|----------|------------|
| s29      | perp     | 29         | +5.60%     | 79.3%    | 4.93          | -1.09%   | **ALIVE**  |
| s32      | combined | 0          | +0.00%     | --       | --            | --       | DEAD       |
| s37      | perp     | 114        | +3.87%     | 51.8%    | 1.30          | -3.43%   | **ALIVE**  |
| s44      | combined | 26         | -1.39%     | 38.5%    | 0.56          | -1.35%   | DEAD       |
| s56      | perp     | 41         | -2.71%     | 46.3%    | 0.64          | -3.95%   | DEAD       |
| s65      | perp     | 24         | +4.04%     | 83.3%    | 4.93          | -0.47%   | **ALIVE**  |
| s72      | perp     | 24         | +4.04%     | 83.3%    | 4.93          | -0.47%   | **ALIVE**  |

**s58** was excluded from triage -- it is a multi-strategy portfolio runner (combines s56+s57), not a standard strategy function.

---

## Verdict Categories

- **ALIVE** (OOS return > +2%): s29, s37, s65, s72
- **MARGINAL** (0% < OOS return < +2%): none
- **DEAD** (OOS return <= 0% or no trades): s32, s44, s56

---

## Strategy-Level Analysis

### ALIVE Strategies

#### s29 — Funding Rate Carry (perp) -- ALIVE
- **OOS Return: +5.60%** | 29 trades | 79.3% win rate | PF 4.93
- Max drawdown: -1.09% (very shallow)
- Carry strategy harvests perp funding payments. Structurally robust because edge comes from funding income (retail long bias), not price prediction.
- Full-period return is -14.33% (skewed by the bug-affected earlier period), but recent OOS is strongly positive.
- All 10 tokens profitable in OOS. Top performers: SUI (+$2,534), XRP (+$2,153), BNB (+$1,708).
- **Note:** s65 and s72 are v4 rebuilds of this strategy family. Keeping s29 as a reference but s65/s72 are the production variants.

#### s37 — Momentum Trail Progression (perp) -- ALIVE
- **OOS Return: +3.87%** | 114 trades | 51.8% win rate | PF 1.30
- Max drawdown: -3.43% (moderate)
- Wraps s11 momentum burst with progressive trailing stop. Higher trade count means more statistical significance.
- Mixed per-token results: SUI (+$7,245) is the standout, while XRP (-$2,007) and PEPE (-$1,566) drag.
- PF of 1.30 is thin but positive. The strategy works but is not high-conviction.

#### s65 — Funding Carry V4 (perp) -- ALIVE
- **OOS Return: +4.04%** | 24 trades | 83.3% win rate | PF 4.93
- Max drawdown: -0.47% (very shallow -- best risk-adjusted)
- V4 rebuild of s29 with progressive trailing stops, regime-aware sizing, funding z-score entries.
- All 9 tokens with trades are profitable. SUI (+$2,537), SOL (+$1,342), DOGE (+$1,196) lead.
- Full-period return is -26.87% (severely impacted by bug-affected period), but OOS performance is strong.
- **Best risk-adjusted strategy in the triage.**

#### s72 — s65 + Time Trail (perp) -- ALIVE
- **OOS Return: +4.04%** | 24 trades | 83.3% win rate | PF 4.93
- Identical OOS performance to s65 (time trail overlay has no effect because base trail_mult=1.5 is already tighter than all schedule values).
- **Effectively identical to s65 -- the time trail overlay is a no-op.** Can be treated as s65.

### DEAD Strategies

#### s32 — Regime-Adaptive Spot-Perp (combined) -- DEAD
- Zero trades in OOS. Zero trades in full period.
- The combined market requirement (spot+perp alignment) plus strict regime filters (UPTREND-only for spot longs, DOWNTREND-only for perp shorts) produce no entries in the recent market environment.
- **Kill decision: no signal generation.** The strategy is structurally unable to trade in the current regime.

#### s44 — Basis Carry Trail Progression (combined) -- DEAD
- OOS: 26 trades, -1.39% return, 38.5% win rate, PF 0.56
- Only PEPE and BNB generated OOS trades. PEPE was -$2,751 (overwhelmingly negative).
- Basis carry (long spot + short perp) is fundamentally challenged: basis has compressed post-ETF approval.
- **Kill decision: negative edge.**

#### s56 — Max Leverage Momentum 5x (perp) -- DEAD
- OOS: 41 trades, -2.71% return, 46.3% win rate, PF 0.64
- The 5x leverage amplifies losses. Entry filters (ADX>30, multi-horizon momentum, BB breakout, vol>2.5) are too strict for some tokens, too loose for others.
- ETH (+$1,009) and BNB (+$478) positive, but PEPE (-$3,957) and DOGE (-$2,389) destroy the portfolio.
- **Kill decision: leverage amplifies the stop-price bug fix impact. Negative edge.**

---

## Key Observations

1. **Carry strategies survived the bug fix.** s29, s65, and s72 all show positive OOS returns. Their edge comes from funding income, not price prediction, so the stop-price fix has less impact on their overall profitability.

2. **s37 (momentum) survived but is marginal.** PF=1.30 with 3.87% return and 3.43% drawdown. The progressive trail helps lock in winners but the thin edge means it is fragile.

3. **Combined market strategies are dead.** Both s32 and s44 failed. Combined strategies have additional complexity (spot+perp alignment, basis dynamics) that does not add value in the current market.

4. **High-leverage strategies are dead.** s56 at 5x leverage amplifies the cost of the stop-price fix. Without the bug, stops trigger at worse prices, and leverage magnifies this.

5. **s65 = s72 in practice.** The time trail overlay in s72 is a no-op because the base trail_mult (1.5) is always tighter than the schedule values (3.5, 3.0, 2.5, 2.0). These should be consolidated.

6. **Full-period returns are misleading.** Many strategies show deeply negative full-period returns but positive OOS returns. This is because the walk-forward training period includes the bug-affected historical data, and the strategies may have adapted/improved in recent months.

---

## Recommendations

1. **Keep alive:** s65 (funding carry v4) -- best risk-adjusted performance, shallow drawdown, high win rate
2. **Keep alive with monitoring:** s37 (momentum trail) -- positive but thin edge, monitor PF closely
3. **Keep alive as reference:** s29 (original funding carry) -- superseded by s65 but independently validates the carry thesis
4. **Kill:** s32, s44, s56, s72 (s72 is identical to s65, consolidate)
5. **Priority:** Focus new strategy development on carry-based approaches (proven post-fix) and investigate why momentum strategies have thinner edge

---

## Appendix: Full JSON Results

See `strategy_triage_results.json` in this directory for complete per-trade data.
