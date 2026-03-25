# Strategy Catalog -- Post-MTM Validated (March 2026)

> **POST-MTM VALIDATED.** All performance numbers in this file are from the V4 portfolio
> backtester with mark-to-market equity tracking, $200K starting capital, $2M position
> sizing cap, and hourly slippage. Previous versions of this file contained wildly
> inflated numbers (+3157%, Sharpe 7.29, Calmar 91.74) generated with uncapped equity
> compounding that allowed equity to grow to $33-55M. Those numbers were fantasy.
> This version uses only validated post-MTM data.

> **TL;DR -- The honest picture:**
> Only 2 out of 18 strategies/portfolios made money in the 12-month post-MTM backtest.
> **s62** (conservative funding carry) returned +9.6% and **s65** (funding carry) returned +4.9%.
> Everything else lost money -- some catastrophically (s60: -70%, s63: -79%, s75: -84%).
> The "production portfolio" s58 (s56+s57) lost -32.5%. Adding strategies together mostly
> made things worse, not better. Paper trading (15 days, Mar 11-25 2026) partially confirms:
> s65-based combos are slightly positive, s62 is slightly positive, everything else negative.
> Trend following in crypto at swing timeframes is far harder than pre-MTM backtests suggested.
> **Full details + 67 citations:** `knowledge/archive/STRATEGY_CATALOG_DETAILS.md`

**Context:** Crypto swing trading, $200K capital, $2M sizing cap, 1H timeframe, 18-720hr holds
**Last validated:** March 25, 2026 -- V4 portfolio backtest (12-month, post-MTM)
**V4 engine:** Portfolio-level simulation with shared capital, concentration limits, ADV caps, slippage model, MTM equity. See `knowledge/V4_ENGINE.md`.

---

## V4 Post-MTM Results -- All Strategies Ranked (12-Month, $200K, $2M Cap)

### The Only Profitable Strategies

| Rank | Strategy | 12mo Return | Max Drawdown | Trades | Notes |
|------|----------|-------------|--------------|--------|-------|
| 1 | **s62** conservative_funding_carry | **+9.6%** (+$19.3K) | -29.7% | 2,030 | Best performer. Paper trading: +1.06% in 15 days |
| 2 | **s65** funding_carry_v4 | **+4.9%** (+$9.8K) | -45.8% | 1,796 | Paper trading: +2.25% in 15 days |
| 3 | **s72** s65_time_trail | **+4.9%** (+$9.8K) | -45.8% | 1,796 | Same as s65 (overlay adds no value post-MTM). Paper: +3.21% |

Three strategies barely positive. The rest lose money.

### The Losing Strategies (sorted best to worst)

| Rank | Strategy | 12mo Return | Max Drawdown | Trades | Notes |
|------|----------|-------------|--------------|--------|-------|
| 4 | s58+s62 | **-11.9%** (-$23.8K) | -19.8% | 4,110 | s62 positive solo but dragged down by s58 |
| 5 | s58+s72 | **-20.2%** (-$40.4K) | -44.2% | 3,512 | |
| 6 | s58+s65 | **-20.9%** (-$41.8K) | -36.9% | 3,430 | |
| 7 | 4-edge (s56+s57+s63+s65) | **-21.2%** (-$42.3K) | -38.5% | 4,331 | "Best combo" in paper -- still loses in backtest |
| 8 | s56 momentum | **-27.4%** (-$54.7K) | -38.5% | 1,166 | Former "production" component |
| 9 | s69 (s56+time_trail) | **-27.4%** (-$54.7K) | -38.5% | 1,166 | Time trail adds nothing |
| 10 | s76 (s56+partial_tp) | **-27.9%** (-$55.7K) | -38.4% | 1,446 | Partial TP adds nothing |
| 11 | s57 carry | **-29.3%** (-$58.6K) | -29.2% | 1,438 | Former "production" component |
| 12 | s58 (s56+s57) | **-32.5%** (-$64.9K) | -37.0% | 2,354 | FORMER "production portfolio" -- losing a third of capital |
| 13 | 4-edge+ptp | **-41.9%** (-$83.7K) | -42.0% | 4,662 | |
| 14 | s58+s60 | **-45.3%** (-$90.7K) | -56.7% | 3,368 | |

### Graveyard -- Strategies Losing >50%

| Rank | Strategy | 12mo Return | Max Drawdown | Trades | Notes |
|------|----------|-------------|--------------|--------|-------|
| 15 | s59 funding_mean_rev_v4 | **-52.7%** (-$105.5K) | -58.8% | 2,410 | Mean reversion confirmed dead |
| 16 | s60 momentum_burst_perp | **-70.5%** (-$141.1K) | -81.3% | 1,633 | Bidirectional momentum destroyed |
| 17 | s63 vol_spike_reversal | **-78.9%** (-$157.8K) | -81.7% | 2,583 | Counter-trend catastrophic |
| 18 | s75 (s63+fixed_tp) | **-83.9%** (-$167.9K) | -85.7% | 2,709 | Overlay on a broken strategy |

---

## Paper Trading Validation (Mar 11-25, 2026 -- 15 Days Live)

| Portfolio | Return | Peak | DD from Peak | Status |
|-----------|--------|------|-------------|--------|
| s65 combo (s56+s57+s65) | +4.75% | +21.5% | -15.7% | Positive, volatile |
| s72 (s65+time_trail) | +3.21% | +25.8% | -19.5% | Positive, volatile |
| s65 solo | +2.25% | -- | -- | Slightly positive |
| s62 solo | +1.06% | -- | -5.3% | Slightly positive, lowest vol |
| Everything else | Negative | -- | -- | Losing money live |

**Paper trading takeaway:** The two funding carry strategies (s62, s65) show small positive returns
in live execution. Returns are modest and drawdowns are significant. Nothing suggests the kind of
edge that would justify high-conviction deployment.

---

## Strategy Descriptions

### Tier A: Paper-Trading Confirmed Positive

#### s62 -- Conservative Funding Carry

| Metric | Post-MTM Value |
|--------|---------------|
| 12mo Return | +9.6% (+$19.3K) |
| Max Drawdown | -29.7% |
| Trades | 2,030 |
| 3mo Return | -8.9% |
| 1mo Return | +4.0% |
| Paper (15d) | +1.06%, DD -5.3% |

**Entry logic:** Conservative funding carry: higher threshold than s65, tighter stops, lower
position sizes. Regime-gated. Harvests structural funding rate imbalance (retail long bias).

**Why it works:** Higher selectivity means fewer losing trades. Tighter stops limit damage.
Still collects positive funding premium on net-short positions during periods of extreme
retail leverage.

#### s65 -- Funding Carry V4

| Metric | Post-MTM Value |
|--------|---------------|
| 12mo Return | +4.9% (+$9.8K) |
| Max Drawdown | -45.8% |
| Trades | 1,796 |
| 3mo Return | +8.9% |
| 1mo Return | +3.9% |
| Paper (15d) | +2.25% |

**Entry logic:** Harvest structural funding rate imbalance (retail long bias). Go short tokens
with elevated positive funding rates; collect funding payments while holding. Regime-gated.

**Warning:** The 45.8% max drawdown on a 4.9% return is terrible risk-adjusted performance.
The strategy was slightly profitable over 12 months but experienced drawdowns nearly 10x the
total return. The 3mo and 1mo windows look better, but that could be recency bias.

### Tier C: Losing Money

#### s56 -- Signal-Enhanced Momentum (FORMER Production)

| Metric | Post-MTM Value |
|--------|---------------|
| 12mo Return | -27.4% (-$54.7K) |
| Max Drawdown | -38.5% |
| Trades | 1,166 |

**Entry logic:** Signal-enhanced momentum on perps. ADX > 20, multi-TF momentum alignment,
signal discovery timing overlay. 5x aggressive sizing at 1x leverage.

**Post-mortem:** The pre-MTM backtest showed this making thousands of percent because uncapped
compounding let equity snowball to $30M+. With realistic $2M sizing caps, the edge is negative.
The aggressive sizing amplifies losses more than gains under realistic constraints.

#### s57 -- Signal-Timed Turbo Carry (FORMER Production)

| Metric | Post-MTM Value |
|--------|---------------|
| 12mo Return | -29.3% (-$58.6K) |
| Max Drawdown | -29.2% |
| Trades | 1,438 |

**Entry logic:** Carry strategy with signal discovery timing overlay. Aggressive sizing
(size_multiplier=3.0, cap_multiplier=15.0).

**Post-mortem:** Like s56, the aggressive sizing parameters that produced fantasy returns
under uncapped compounding produce losses under realistic constraints. Basis convergence
profits are real but too small to overcome the sizing-amplified losses.

#### s58 -- Multi-Strategy Portfolio: s56+s57 (FORMER "Production Portfolio")

| Metric | Post-MTM Value |
|--------|---------------|
| 12mo Return | -32.5% (-$64.9K) |
| Max Drawdown | -37.0% |
| Trades | 2,354 |
| 3mo Return | -1.2% |
| 1mo Return | -7.8% |

**Previously reported as:** +1717%, Sharpe 7.29, Calmar 91.74. All of that was fiction
produced by uncapped equity compounding.

**Post-mortem:** Both components (s56 and s57) lose money individually. Combining two losing
strategies produces a losing portfolio. The "diversification benefit" claimed in previous
versions was an artifact of the compounding bug.

#### s59 -- Funding Mean Reversion V4

| Metric | Post-MTM Value |
|--------|---------------|
| 12mo Return | -52.7% (-$105.5K) |
| Max Drawdown | -58.8% |
| Trades | 2,410 |

**Entry logic:** Fade extreme funding rates (z-score > 2), expect mean reversion. 72h lookback.

**Post-mortem:** Mean reversion at swing timeframes confirmed dead. Funding rates can stay
extreme far longer than expected.

#### s60 -- Momentum Burst Perp V4

| Metric | Post-MTM Value |
|--------|---------------|
| 12mo Return | -70.5% (-$141.1K) |
| Max Drawdown | -81.3% |
| Trades | 1,633 |

**Entry logic:** Bidirectional perp momentum burst: ADX>20, vol_ratio>1.0, regime-scaled.

**Post-mortem:** Catastrophic failure. Bidirectional momentum on perps generates massive
fees and funding costs that eat through any edge. Lost over 70% of capital.

#### s63 -- Vol Spike Reversal V4

| Metric | Post-MTM Value |
|--------|---------------|
| 12mo Return | -78.9% (-$157.8K) |
| Max Drawdown | -81.7% |
| Trades | 2,583 |

**Entry logic:** Fade extreme vol spikes (vol_ratio > 3x). Bidirectional: short after
up-spikes, long after down-spikes.

**Post-mortem:** Counter-trend strategy in a momentum-driven market. Almost total loss.
Paper trading confirmed: 0% win rate on initial trades.

#### s80 -- Cross-Sectional Momentum V4 / s81 -- Sector Rotation V4

**Status:** No post-MTM backtest data available. Deployed to paper trading but no results
reported yet. Given the failure rate of other V4 strategies, treat with extreme caution.

### Overlay Wrappers (Post-MTM Reality)

| Strategy | Base | Overlay | Pre-MTM Claim | Post-MTM Reality |
|----------|------|---------|---------------|-----------------|
| s69 (s56+time_trail) | s56 | Time-decayed trail | "Calmar +33%" | -27.4%, same as s56. Overlay adds nothing. |
| s72 (s65+time_trail) | s65 | Time-decayed trail | "Calmar +148%" | +4.9%, identical to s65. Overlay adds nothing. |
| s75 (s63+fixed_tp) | s63 | Fixed TP at 3x ATR | "Calmar +17.6%" | -83.9%, worse than s63. Overlay amplifies losses. |
| s76 (s56+partial_tp) | s56 | Partial TP at 2x ATR | "Solo +8.9%" | -27.9%, same as s56. Overlay adds nothing. |

**Conclusion on overlays:** Every overlay wrapper tested adds zero or negative value post-MTM.
The pre-MTM "improvements" were artifacts of uncapped compounding dynamics. Time trails,
partial take-profits, and fixed TPs do not fix losing base strategies.

### Engine-Level Overlays

| Overlay | Pre-MTM Claim | Post-MTM Status |
|---------|---------------|-----------------|
| Dynamic regime weights | "super5-dyn: +28% return" | No post-MTM validation. Likely inflated. |
| Conviction entry scoring | "Calmar +59-281%" | No post-MTM validation. Likely inflated. |

---

## V3 Legacy Strategies -- NO POST-MTM VALIDATION

The following strategies were validated only under V3 (per-token, uncapped equity, no MTM fix).
Their performance numbers are from the old system and **cannot be trusted** until re-tested
with post-MTM V4 constraints.

### V3 LEGACY -- Unvalidated (DO NOT USE WITHOUT RE-TESTING)

| Strategy | V3 Claim | Post-MTM Status |
|----------|----------|-----------------|
| s28 momentum_burst_perp | "+3157%, Sharpe 5.2" | **V3 LEGACY -- NO POST-MTM VALIDATION.** V3 per-token gate: 6.7% rate (22/329). These numbers were from uncapped equity. |
| s29 funding_carry | "+269%, Sharpe 3.1" | **V3 LEGACY -- NO POST-MTM VALIDATION.** Per-token mean Sharpe +0.81. Needs V4 re-test. |
| s44 basis_carry_trail | "+139%, Sharpe 2.8" | **V3 LEGACY -- NO POST-MTM VALIDATION.** |
| s49 perp_momentum | "+148%, Sharpe 2.4" | **V3 LEGACY -- NO POST-MTM VALIDATION.** |
| s51 regime_momentum | "+238%, Sharpe 2.9" | **V3 LEGACY -- NO POST-MTM VALIDATION.** |
| s54 turbo_carry | "+746%, Sharpe 4.8" | **V3 LEGACY -- NO POST-MTM VALIDATION.** |
| s25 vol_spike_reversal | "+67% OOS" | **V3 LEGACY -- NO POST-MTM VALIDATION.** V4 version (s63) lost -78.9%. |
| s30 basis_carry | "Sharpe 2.63, Calmar 12.76" | **V3 LEGACY -- NO POST-MTM VALIDATION.** All V3 Calmar/Sharpe numbers inflated by uncapped compounding. |

### V3 Per-Token Strategies (Historical Reference Only)

The following V3 strategies (S09, S11, cross-sectional momentum, sector rotation, pairs trading,
regime overlays, etc.) were validated at the per-token level without portfolio constraints.
Their metrics (Sharpe, Calmar, PnL) are from uncapped equity backtests and are **not reliable
indicators of live performance**.

Key V3 strategies for historical reference:
- **S09** Optimized Trend (Dual Momentum) -- per-token CPCV validated
- **S11** Momentum Burst -- per-token CPCV validated
- **S29** Funding Carry -- per-token, market-neutral carry
- **S30** Basis Carry -- per-token delta-neutral arbitrage
- **S31** Funding-Hedged Momentum -- redundant with S11 (corr +0.71)
- **S32** Regime-Adaptive Spot-Perp -- alternating regime strategy
- **Cross-sectional momentum** -- portfolio ranking strategy
- **Sector rotation** -- category momentum

These are documented in `knowledge/archive/STRATEGY_CATALOG_DETAILS.md`. Do not use V3
performance numbers for any deployment decisions.

---

## Tier Assignment (Post-MTM, March 2026)

### Tier A: Paper-Trading Confirmed Positive

| Strategy | 12mo Return | Max DD | Paper (15d) | Edge |
|----------|-------------|--------|-------------|------|
| s62 | +9.6% | -29.7% | +1.06% | Conservative funding carry |
| s65 | +4.9% | -45.8% | +2.25% | Funding carry |

**Honest assessment:** These strategies are slightly profitable but the risk-adjusted returns
are poor. s62 returned +9.6% with a -29.7% drawdown. s65 returned +4.9% with a -45.8% drawdown.
A simple bank deposit would have been comparable with zero risk. The edge is real but thin.

### Tier B: Positive in Backtest, Untested Live

*None currently qualify.* s72 shows +4.9% in backtest but is identical to s65 and has been
paper-traded (so it is effectively part of Tier A). No other strategy shows positive post-MTM
12-month returns.

### Tier C: Losing Money (Do Not Deploy)

| Strategy | 12mo Return | Max DD | Status |
|----------|-------------|--------|--------|
| s56 | -27.4% | -38.5% | Former production component |
| s57 | -29.3% | -29.2% | Former production component |
| s58 (s56+s57) | -32.5% | -37.0% | Former "production portfolio" |
| s69 (s56+time_trail) | -27.4% | -38.5% | Overlay on losing strategy |
| s72 (s65+time_trail) | +4.9% | -45.8% | Technically positive but identical to s65 |
| s76 (s56+partial_tp) | -27.9% | -38.4% | Overlay on losing strategy |
| s80, s81 | No data | No data | Unvalidated |
| All portfolios containing s56/s57 | -12% to -46% | -20% to -57% | Adding losers makes more losers |

### Graveyard: Total Loss (>50% Drawdown, Do Not Touch)

| Strategy | 12mo Return | Max DD | Cause of Death |
|----------|-------------|--------|----------------|
| s59 | -52.7% | -58.8% | Mean reversion is dead at swing TF |
| s60 | -70.5% | -81.3% | Perp momentum fees eat edge |
| s63 | -78.9% | -81.7% | Counter-trend in momentum market |
| s75 | -83.9% | -85.7% | Overlay on dead s63 |

---

## Key Research Findings (Updated Post-MTM)

### What Actually Works (Post-MTM Evidence)

| Finding | Evidence | Implication |
|---------|----------|-------------|
| Funding carry is the only surviving edge | s62 +9.6%, s65 +4.9% -- only profitable strategies | Focus research on funding carry variants |
| Conservative beats aggressive | s62 (tighter stops, lower size) > s65 (standard) | Reduce position sizes, tighten risk |
| The edge is thin | Best strategy returns +9.6% with -29.7% DD | Do not over-allocate; this is not a get-rich strategy |
| Combining strategies mostly hurts | s58+s62: -11.9% vs s62 solo: +9.6% | Adding losing strategies to winning ones makes losers |
| Overlays add zero value post-MTM | s69=s56, s72=s65, s76=s56 (identical results) | Time trails, partial TP do not help with realistic sizing |
| Paper trading partially confirms backtest | s62 +1.06%, s65 +2.25% over 15 days | Small edge appears real, but sample too small to be conclusive |

### What Definitively Fails (Post-MTM Evidence)

| Finding | Evidence | Implication |
|---------|----------|-------------|
| Momentum at swing TF (with realistic sizing) | s56: -27.4%, s60: -70.5% | The "momentum works in crypto" thesis is overstated under constraints |
| Mean reversion at swing TF | s59: -52.7% | Confirmed dead, again |
| Counter-trend / vol spike reversal | s63: -78.9% | Near-total loss |
| Aggressive sizing (size_mult=3, cap_mult=15) | s56, s57 both lose ~28-29% | Amplifies losses more than gains under sizing caps |
| Multi-strategy diversification (with losers) | All combos worse than best solo | Diversification only helps if components are positive |
| All pre-MTM performance claims | Sharpe 7.29, +1717%, Calmar 91.74 | Were artifacts of uncapped compounding, not real edge |

### What Remains Unknown

| Question | Status | Next Step |
|----------|--------|-----------|
| Can V3 strategies (s28, s29, s30, etc.) survive post-MTM? | Untested | Run V4 post-MTM backtests before any deployment |
| Is the funding carry edge durable or a 2025 artifact? | 15 days of paper data | Need 3-6 months minimum |
| Would reduced sizing improve s56/s57? | Untested | Try size_mult=1.0 with MTM constraints |
| Can cross-sectional/sector rotation survive post-MTM? | s80/s81 untested | Priority backtest targets |

---

## Strategy Graveyard (Tier D: Do Not Implement)

| Strategy | Category | Reason for Rejection | Post-MTM Result |
|----------|----------|---------------------|-----------------|
| BB mean reversion (any variant) | Mean reversion | Loses money at swing TF | N/A (never V4 tested) |
| RSI extremes (standalone) | Mean reversion | IC=0.004; MR dead at swing TF | N/A |
| Vol breakout (BB squeeze) | Volatility | 55% fakeout rate | N/A |
| s59 funding_mean_rev_v4 | Mean reversion (perp) | -52.7% in 12 months | **CONFIRMED DEAD** |
| s60 momentum_burst_perp_v4 | Momentum (perp) | -70.5% in 12 months | **CONFIRMED DEAD** |
| s63 vol_spike_reversal_v4 | Counter-trend | -78.9% in 12 months, 0% win rate in paper | **CONFIRMED DEAD** |
| s75 s63_fixed_tp | Counter-trend + TP | -83.9% in 12 months | **CONFIRMED DEAD** |
| s56 signal_enhanced_momentum | Momentum | -27.4% in 12 months | **CONFIRMED LOSING** |
| s57 signal_timed_turbo_carry | Carry (aggressive) | -29.3% in 12 months | **CONFIRMED LOSING** |
| s58 multi_strategy_portfolio | Portfolio (s56+s57) | -32.5% in 12 months | **CONFIRMED LOSING** |
| s61 funding_carry variant | Funding carry | Overlap >80% with s65; deduped | DEDUP |
| s66 adx_breakout | ADX breakout | Too few trades (38 on BTC) | KILLED Gate 0 |
| s67 funding_momentum_v4 | Funding momentum | Sharpe too low for portfolio | KILLED V4-Gate 5 |
| s68 band_walk | BB band walk | 80% overlap with s56 | KILLED Gate 2 |
| s70 s60_time_trail | s60 + time trail | Base strategy dead (-70.5%) | NOT DEPLOYED |
| s71 s63_time_trail | s63 + time trail | Base strategy dead (-78.9%) | NOT DEPLOYED |
| s73 s56_funding_exit | s56 + funding exit | Calmar degrades at every threshold | KILLED |
| s74 s60_funding_exit | s60 + funding exit | Base strategy dead | KILLED |
| All 5x leveraged strategies (s50/s52/s55) | Leverage | Fees amplified 5x eat the edge | KILLED |
| Standalone signal strategies | Signal entries | IC does not equal tradeable edge | KILLED |

---

## Research Principles (Revised)

1. **Post-MTM validation is mandatory.** No strategy should be deployed or recommended based on
   uncapped equity backtest results. The MTM fix revealed that most "profitable" strategies were
   artifacts of unrealistic compounding.

2. **The edge is thin.** The best post-MTM strategy (s62) returned +9.6% on $200K with -29.7%
   drawdown. Set expectations accordingly. This is not a path to rapid wealth.

3. **Simple still beats complex.** The two surviving strategies (s62, s65) are straightforward
   funding carry plays. Every attempt at complex overlays, multi-strategy portfolios, or
   sophisticated entry logic produced worse results.

4. **Diversification only works with positive components.** Combining losing strategies produces
   a losing portfolio. The pre-MTM claim that "multi-strategy diversification always helps"
   was wrong.

5. **Paper trading is the minimum bar.** 15 days of paper trading showed s62 and s65 slightly
   positive. This is encouraging but far from conclusive. Need 3-6 months minimum.

6. **Be honest about what we do not know.** Many V3 strategies have never been re-tested
   post-MTM. They may be profitable or they may be dead. Do not assume either.

7. **Position sizing is the dominant factor.** The difference between "profitable" and "losing"
   for many strategies appears to be the sizing parameters. Aggressive sizing (3x multiplier)
   that produced fantasy returns under uncapped compounding produces real losses under constraints.

8. **CPCV remains the gold standard** for per-token validation. But per-token validation does
   not guarantee portfolio-level profitability under realistic constraints.

9. **Harvey t-stat > 3.0** threshold for any new signal (multiple testing correction).

10. **Admit failure.** 16 out of 18 strategies/portfolios tested post-MTM are losing money.
    The pre-MTM catalog painted a picture of robust, highly profitable strategies. That picture
    was wrong. The honest assessment is that we have found a thin, fragile edge in funding carry
    and nothing else has survived realistic testing.

---

## Regime Model Overview

Our HMM detects 5 regimes that drive allocation decisions:

| Regime | Frequency | Action | Note |
|--------|-----------|--------|------|
| Uptrend | 19% | Full allocation to carry strategies | Only surviving edge is carry |
| Range | 52% | Reduced exposure | Dead time -- thin edge even thinner |
| Quiet | 18% | Selective | Wait for funding rate extremes |
| Downtrend | 10% | Cash/minimal | s62/s65 may still collect funding |
| Crisis | 1% | Circuit breaker | Exit all positions |

---

## What Next

1. **Re-test V3 strategies under post-MTM constraints.** s29, s30, s44 were promising in V3
   but have never been validated post-MTM. Priority targets.

2. **Reduce sizing on s56/s57.** Try size_multiplier=1.0 to see if the edge is real at
   moderate sizing.

3. **Extend paper trading.** 15 days is not enough. Need 3-6 months of s62 and s65 paper
   results before considering real capital.

4. **Investigate why funding carry survives.** The structural explanation (retail long bias
   creates persistent positive funding) is plausible. Need to test whether this edge is
   durable or a 2024-2025 artifact.

5. **Run s80/s81 through post-MTM backtest.** Cross-sectional and sector rotation are
   structurally different from funding carry and could provide genuine diversification --
   but only if they are actually profitable.

*Full strategy descriptions, parameter tables, and 67 academic citations archived in `knowledge/archive/STRATEGY_CATALOG_DETAILS.md`.*
