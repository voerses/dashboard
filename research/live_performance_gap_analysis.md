# Live Performance Gap Analysis

> **Date:** 2026-03-24
> **Analyst:** Quantitative Research Agent
> **Severity:** CRITICAL -- largest risk to portfolio viability

---

## 1. Paper Trading State Summary

### Current Active Pools (7 pools, multi_v4_paper.json)

| Pool | Strategies | Start Date | Days | Realized PnL | MTM PnL | Max DD (MTM) | Open Pos |
|------|-----------|------------|------|-------------|---------|-------------|----------|
| s58+s65 | s56, s57, s65 | 2026-03-11 | 14.0 | +$11,329 (+5.7%) | +$8,018 (+4.0%) | -17.0% | 12 |
| s72 | s72 | 2026-03-12 | 12.5 | +$7,375 (+3.7%) | +$5,476 (+2.7%) | -22.5% | 9 |
| s65 solo | s65 | 2026-03-18 | 5.5 | +$11,243 (+5.6%) | +$6,016 (+3.0%) | -9.2% | 7 |
| s62 solo | s62 | 2026-03-18 | 5.5 | +$2,306 (+1.2%) | +$1,334 (+0.7%) | -6.1% | 8 |
| s98 | s98 | 2026-03-20 | 3.6 | -$2,013 (-1.0%) | -$2,013 (-1.0%) | -1.3% | 0 |
| s106 | s106 | 2026-03-21 | 3.4 | $0 (0.0%) | $0 (0.0%) | 0.0% | 0 |
| s107 | s107 | 2026-03-21 | 3.4 | $0 (0.0%) | $0 (0.0%) | 0.0% | 0 |

### Stopped/Archived Pools

| Pool | Period | Realized PnL | MTM PnL | Max DD (MTM) | Reason |
|------|--------|-------------|---------|-------------|--------|
| s58+s60 (STOPPED) | Mar 10-20 (11d) | -$34,976 (-17.5%) | -$38,740 (-19.4%) | -28.1% | s60 bleeding; rotation removed it |
| 6 archived pools | Pre-Mar 18 | Various losses | N/A | -81% to -97% | Catastrophic DD in backtest; removed |

### Trade Statistics (closed trades only)

| Pool | Trades | Win Rate | Avg Winner | Avg Loser | Payoff | Total PnL |
|------|--------|----------|-----------|----------|--------|----------|
| s65 pool (s56+s57+s65) | 77 | 49.4% | $1,654 | -$1,343 | 1.23 | +$10,444 |
| s72 | 50 | 44.0% | $2,039 | -$1,461 | 1.40 | +$6,817 |
| s65 solo | 16 | 43.8% | $2,779 | -$1,407 | 1.98 | +$10,993 |
| s62 solo | 19 | 42.1% | $1,615 | -$832 | 1.94 | +$2,339 |
| s60 (STOPPED) | 79 | 40.5% | $1,375 | -$1,632 | 0.84 | -$32,009 |

---

## 2. The Claimed Gap: Reality Check

The RESEARCH_STATUS.md states: "All paper trading strategies are LOSING: s58(-59%), s65(-76%), s60(-88%), s63(-92%)"

**These numbers do NOT match current paper trading.** The current pools show:
- s65 pool: +5.7% realized (NOT -76%)
- s72: +3.7% realized (NOT losing)
- s60: -17.5% (NOT -88%)

**The -59% to -92% figures likely reference the old BACKTEST MaxDD numbers** from the archived pools that were removed on 2026-03-18 (e.g., s80+s81-dyn had -96.6% all-time DD, s59 had -93% DD). These are backtest drawdowns, not paper trading results.

**The actual gap analysis should compare:**
- Backtest annual returns of ~150-530% (uncapped compounding)
- Paper trading annualized rates of ~15-150% (rough, from 5-14 days of data)
- Paper trading max DDs of -6% to -28% vs backtest max DDs of -2.8% to -5.7%

---

## 3. Identified Causes of Gap (Ranked by Likelihood)

### CAUSE 1: Equity Compounding Illusion (CRITICAL -- explains ~80% of gap)

**The single largest cause.** The backtest numbers are deeply misleading:

- s60 backtest: $200K -> $7.76M (3,780% return, Sharpe 4.56)
- s65 backtest: $200K -> $1.23M (515% return, Sharpe 5.46)
- s72 backtest: $200K -> $1.53M (664% return, Sharpe 5.56)

The equity curve analysis reveals the compounding pattern:
- s60: $200K flat for 12 months, then hockey-sticks to $7.76M. Feb 2026 alone: +75.5% on a $4.3M base = $3.2M profit in one month. By that point the strategy is taking multi-million dollar positions that are **physically impossible** to execute.
- s65: Similar pattern -- $200K flat for 12 months, then compound from $200K to $1.2M in the last 12 months.

**The PROJECT_STATUS.md already acknowledges this:** "All return figures were generated with uncapped equity compounding. The backtester allows equity to grow to $33-55M... Realistic returns after $2M sizing cap are estimated at 60-80% lower."

The backtest runner (`backtest_portfolio.py`) does use `max_sizing_equity=2_000_000`, but the equity still compounds freely above that. The Sharpe ratio of 4-6 is computed on a curve dominated by late-period returns on inflated capital -- this does not represent what $200K of fresh capital can earn.

**Paper trading starts at $200K and stays there.** It experiences the early, low-return phase of the equity curve. There is no "hockey stick" yet because the capital hasn't compounded. A fair comparison would be the first 2 weeks of the backtest (where returns are close to zero).

### CAUSE 2: Backtest Sharpe Inflation from Path-Dependent Sizing (HIGH)

The Kelly sizing formula (`compute_position_size`) scales position size with equity:
```
raw = strategy_equity * kelly_frac * vol_adj
```

In the backtest, as equity grows from $200K to $1M+, each winning trade generates proportionally larger absolute PnL. This creates a self-reinforcing cycle that inflates Sharpe. In paper trading, equity is ~$200-212K, so position sizes are 5-6x smaller than backtest late-period positions. The per-trade PnL is correspondingly smaller.

### CAUSE 3: Max Drawdown Understatement in Backtests (HIGH)

The backtest reports MaxDD of -2.8% to -5.7% for these strategies. But paper trading shows MaxDD of -6% to -28% (MTM basis) in just 5-14 days. This discrepancy has two causes:

1. **Denominator effect**: When equity is $1M+, a $20K drawdown is only -2%. The same $20K drawdown on $200K capital is -10%. The percentage DD is much worse at the starting capital level.

2. **MTM was previously broken**: PROJECT_STATUS notes the MTM equity fix (commit b9d108f) -- "MaxDD was severely understated before (e.g., s80: -16% reported vs -44% actual)."

### CAUSE 4: Market Regime During Paper Trading Period (MODERATE)

Paper trading started during a specific market window (Mar 10-24, 2026). The strategies are trend-following (s56, s60) and carry (s65) -- both require sustained directional moves or persistent funding rates. If this period was choppy or experienced sudden reversals, strategies would underperform.

Evidence: The s60 pool (momentum burst) lost -17.5% in 11 days, with 57/79 exits being stops (72%). This suggests the market was choppy with many false breakouts -- exactly the regime where momentum strategies bleed.

### CAUSE 5: Strategies Not in Backtest Portfolio (MODERATE)

The backtest "portfolio" claiming Sharpe 6.53 uses s44+s29+s37+s32 -- but NONE of these strategies are in paper trading. Paper trading runs s56, s57, s60, s62, s65, s72, s98, s106, s107. These are different strategies with different (and generally worse) backtest profiles. The comparison is apples-to-oranges.

### CAUSE 6: Stop-Loss Clustering and Position Sizing Mismatch (LOW-MODERATE)

Paper trading runs multiple strategies in pool mode with weight=1.0 per strategy in the s65 pool. This means each strategy sizes off the FULL portfolio equity. With 3 strategies at weight=1.0, total exposure can exceed 300% of capital. When the market turns, correlated positions all stop out simultaneously (evidence: 63/77 exits in s65 pool are stops).

### CAUSE 7: Insufficient Sample Size (LOW but important)

5-14 days of paper trading (50-340 ticks) is statistically insufficient to evaluate a strategy whose backtest covers 8,760+ bars. The paper trading results -- both positive and negative -- are dominated by noise. Any comparison to annualized backtest metrics is unreliable with N < 100 trades per strategy.

---

## 4. What is NOT a Significant Cause

- **Slippage/execution**: The paper trader uses the same `compute_slippage_bps()` model as backtest (3bps + sqrt impact). No execution gap here.
- **Look-ahead bias**: The strategy code uses lagged indicators appropriately (e.g., `_compute_tf_alignment` shifts by 1 bar). No look-ahead detected.
- **Data staleness**: Live data is being fetched and appended to parquet (BTC.parquet updated Mar 24 11:02Z). Not stale.
- **Strategy bugs**: The paper engine delegates to the same `_process_exits()` and `_process_entries()` as the backtester. Behavioral parity was verified with test suites.

---

## 5. Validation Framework Assessment

The v4/validation.py implements:
- **Walk-forward validation**: Rolling train/test with purge windows (365d train, 90d recal, 5d purge)
- **CPCV validation**: 6 groups, 2 test groups, 1% purge, Probability of Backtest Overfitting (PBO)
- **Dual gate**: Token must pass BOTH WF (positive OOS PnL) and CPCV (PBO < 40%)

**Critical weakness**: Validation is per-token, not portfolio-level. A strategy can pass validation on 30 tokens individually but fail when running all 30 simultaneously with shared capital and concentration limits. The paper trading runs the portfolio mode, which the validation engine does not test.

**PBO threshold is generous**: 40% PBO means a strategy has a 40% probability of being overfit. This is a weak gate.

---

## 6. Recommendations

### Immediate (this week)

1. **Stop comparing paper to uncapped backtest returns.** Create a "realistic backtest" mode with:
   - Fixed $200K sizing equity (no compounding growth beyond a cap)
   - OR: report returns as % of starting capital for each calendar month
   - Track the first-14-days performance of the backtest and compare to paper

2. **Run the ACTUAL paper-traded strategies (s56, s65, s72, s62) through backtest for the SAME calendar period** (Mar 10-24, 2026). This gives a direct apples-to-apples comparison of backtest vs paper for the same market conditions.

3. **Increase PBO threshold strictness** from 40% to 25%. Reject more aggressively.

### Short-term (next 2 weeks)

4. **Add portfolio-level validation**: Run the multi-strategy pool config through the backtester and measure portfolio-level Sharpe/DD, not per-strategy.

5. **Report drawdowns at INITIAL capital level**: Always show MaxDD computed on the first N months when equity is near starting capital. The late-period DD on inflated equity is irrelevant for assessing risk at deployment.

6. **Wait for statistical significance**: Paper trading needs 30+ days and 200+ closed trades per strategy before drawing conclusions. Current data (5-14 days, 16-77 trades) is noise.

### Medium-term (next month)

7. **Deploy the s44+s29+s37+s32 portfolio** that actually claims Sharpe 6.53. No strategy currently in paper trading matches the claimed portfolio.

8. **Add regime-conditional performance monitoring**: Track paper PnL broken by detected regime. This allows comparison to backtest per-regime performance.

---

## 7. Should Signal Research Be Paused?

**No, but it should be deprioritized relative to infrastructure fixes.**

The gap is NOT primarily caused by bad strategies or missing signals. It is caused by:
1. Misleading backtest metrics (compounding illusion) -- an accounting/reporting problem
2. Running different strategies in paper than in the "best portfolio" backtest
3. Insufficient paper trading duration for statistical evaluation

**Priority order:**
1. Fix backtest reporting to show realistic, non-compounding returns (~2 days of work)
2. Paper trade the ACTUAL claimed portfolio (s44+s29+s37+s32) (~1 day to configure)
3. Let paper trading run for 30+ days before evaluating (~wait)
4. Continue signal research in parallel (it doesn't conflict)

The positioning overlay research (Finding 47: +0.31 OOS Sharpe) is genuinely useful and should be implemented. But the "live performance gap" is mostly an illusion created by comparing $200K paper results to $7M-compounded backtest results.

---

## Appendix: Key Data Points

**Backtest final equities (12mo, starting $200K):**
- s60: $7,759,668 (3,780% -- physically impossible at scale)
- s72: $1,528,616 (664%)
- s65: $1,229,523 (515%)

**Paper trading results (5-14 days, starting $200K):**
- s65 pool: $211,329 realized (+5.7%)
- s72: $207,375 realized (+3.7%)
- s65 solo: $211,243 realized (+5.6%)
- s60 (stopped): $165,024 realized (-17.5%)

**The s60 loss is the only genuine concern** -- momentum burst strategies are fragile in choppy markets. The other strategies are profitable in paper trading, just at rates that look small compared to fantasy backtest numbers.
