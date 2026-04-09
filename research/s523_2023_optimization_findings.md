# s523c 2023 Optimization Findings

**Date**: 2026-04-09
**Goal**: Make s523c profitable in 2023 (BTC +156% recovery year, strategy baseline: −20.3%)
**Method**: Systematic parameter sweeps through the real v4 engine (not post-hoc replay)

---

## Optimization Progress (cumulative)

| Step | Change | 2023 Return | Sharpe | MaxDD | Trades | Delta |
|------|--------|-------------|--------|-------|--------|-------|
| 0 | **Baseline** (both dir, BL=50, RSI on, zw=30) | **−20.3%** | −0.54 | −39.1% | 80 | — |
| 1 | + Long-only (no shorts) | +10.4% | +0.92 | −10.2% | 17 | +31pp |
| 2 | + Remove blacklist (53 tokens → 25 were blocked) | +136.2% | +1.95 | −46.0% | 97 | +126pp |
| 3 | + Remove RSI filter (4H RSI cross gate too restrictive) | +220.5% | +2.04 | −63.0% | 141 | +84pp |
| 4 | + Z-score window 22d (vs 30d — more reactive in trend) | **+313.0%** | +2.75 | −44.1% | 117 | +93pp |
| 5a | + 3mo_red short gate (allow shorts if 3mo BTC avg < 0) | +188.3% | **+3.65** | **−17.5%** | 45 | risk-optimized |
| 5b | + 1mo_red short gate (allow shorts if last month red) | +166.2% | +3.64 | −19.0% | 35 | best for multi-year |

**Best return**: Step 4 at **+313.0%, Sharpe 2.75, Calmar 7.10**
**Best risk-adjusted**: Step 5a at **+188.3%, Sharpe 3.65, MaxDD −17.5%, Calmar 10.76**

---

## What Each Lever Does

### 1. Direction: Long-only in bull markets (+31pp)
- **Why**: 2023 was BTC +156%. The strategy fired 23 shorts into this rally — all lost money.
- **Fix**: Set `DIRECTION = "long"` during bull regimes.
- **Insight**: The positioning composite IS correct for longs in bull markets. "Crowded long positioning" does lead to reversals — the longs catch those reversal bounces. But shorts are toxic in a bull trend.

### 2. Remove blacklist (+126pp — the BIGGEST single lever)
- **Why**: The static 50-token blacklist was calibrated for the 2025 window. It removes 27 of the 53 tokens available in 2023 (51% of the universe!), including BTC, SOL, DOGE, LINK, XRP, ADA, AVAX — the tokens that rallied hardest.
- **Fix**: Set `TOKEN_BLACKLIST = set()` (empty).
- **Insight**: A static blacklist fit to one period destroys performance in other periods. The composite signal itself should decide which tokens to trade, not a hardcoded exclusion list. If a token's composite says "long", let it trade. If the signal is wrong for that token, the IC_signs will eventually be recalibrated (or the ranked conviction mode will deprioritize it naturally).
- **Generalization risk**: MEDIUM. The blacklist was created because those tokens had negative PnL in 2025's L12M window. Removing it means those tokens WILL trade in 2025 too, potentially hurting that window. Needs cross-year validation.

### 3. Remove RSI filter (+84pp)
- **Why**: The RSI timing overlay requires a 4H RSI cross-up through 40 in the last 72 bars. In 2023's strong recovery, RSI stays elevated and the "fresh cross" check is too restrictive — many valid entry signals never get the RSI gate. Only 97 trades fired vs 141 without RSI.
- **Fix**: Set `RSI_WINDOW_1H = 99999` (effectively disabled — every bar has a "recent" cross).
- **Insight**: The RSI filter is tuned for the choppy 2025 regime where timing entries matters. In a trending market, it blocks legitimate trend entries.
- **Generalization risk**: HIGH. RSI filter was specifically added to improve entry timing. Removing it in trending markets is fine, but in choppy markets (2025), it might hurt by entering at suboptimal moments. This should be regime-conditional.

### 4. Z-score window 22d (+93pp)
- **Why**: The default 30-day rolling z-score window is too slow to detect trend changes. A 22-day window is more reactive — it picks up the bear-to-bull transition ~8 days faster.
- **Fix**: `ZSCORE_WINDOW_DAYS = 22`.
- **Results at different windows**:
  - zw=10: +47% (too reactive, only 16 trades — misses sustained signals)
  - zw=15: +77% (41 trades)
  - zw=18: +228% (99 trades, Sharpe 2.61)
  - zw=20: +233% (111 trades, Sharpe 2.51)
  - **zw=22: +313% (117 trades, Sharpe 2.75)** ← sweet spot
  - zw=25: +253% (126 trades)
  - zw=30: +221% (141 trades, baseline)
  - zw=45: +229% (167 trades)
  - zw=60: +94% (186 trades — too lagged)
- **Insight**: The z-score window controls how fast the strategy detects regime changes. 22 days is optimal for 2023's recovery regime. Different regimes may have different optimal windows.
- **Generalization risk**: MEDIUM. 22d is slightly more reactive than 30d. In a choppy market, 22d might whipsaw more. But the sensitivity analysis shows the function is smooth — zw=18 to 25 all give >+228%, so it's not a knife-edge optimization.

### 5. Conditional short gate (risk optimization)
- **Why**: Long-only gets +313% but at −44% MaxDD. The drawdown comes from the early-2023 bear period when BTC was still recovering. Adding a SHORT gate that allows shorts only when a bearish condition is met lets the strategy:
  - Capture short profits during the early-2023 dip (offsetting long losses)
  - Block shorts during the bull recovery (preventing trend-fighting losses)
- **Best gate for 2023**: `short_if_3mo_red` (allow shorts when average of last 3 monthly BTC returns < 0)
  - +188.3%, **Sharpe 3.65**, **MaxDD −17.5%**, Calmar 10.76
  - The shorts in Jan-Feb 2023 (when 3-month average was still negative) PROFITED during the March dip
  - From March onward, 3-month average turned positive → shorts blocked → pure long ride
- **Best gate for multi-year robustness**: `short_if_1mo_red` (shorts when last month was red)
  - Across all years: 2022 −0.5%, 2023 +166%, 2024 +243%, 2025 −1.7%. Sum +407%
  - Nearly all-years-positive (misses by −0.5% in 2022 and −1.7% in 2025)

---

## What DOESN'T Help in 2023

### Threshold (0.0 to 2.0): ZERO effect
- Every threshold gives identical results. The ranked conviction mode with 53 tokens selects the same trades regardless of threshold. The composite z-scores are either clearly above all thresholds or clearly below — no tokens sit in the 0.5-2.0 discrimination band.

### Max positions (15 to 50): ZERO effect
- The strategy never fills more than ~15 positions simultaneously with 53 tokens. The 30-position cap is never binding.

### Concentration (0.15 to 1.0): ZERO effect
- Same reason as max positions — with the small universe, portfolio allocation doesn't change.

### Stop distance (2.0 to 999 ATR): ZERO effect
- In 2023's bull run, stops NEVER trigger for long positions (prices keep going up). Stop parameters are irrelevant.

### Breakeven ratchet (0.3 to 1.5 ATR): ZERO effect
- Same reason — positions go into profit early and stay there. Breakeven never activates.

### Min hold (12 to 48 hours): ZERO effect
- Positions hold for ~420 hours on average. Min hold doesn't bind.

### Trailing stop: HURTS
- trail=4.0: −101pp return (locks in gains too early, cuts winners)
- trail=3.0: −139pp
- trail=2.0: −159pp
- In a trending market, trailing stops truncate the right tail of returns.

### Sub-daily entry cadence (1h to 24h): MARGINAL NEGATIVE
- More frequent entries don't help because the same tokens re-enter with the same composite. 8 more trades at sub-daily cadence, but worse quality on average.

### Funding boost adjustment: ZERO effect
- Changing FUNDING_BOOST from 0.10 to 0.25 changes nothing. Funding is a tiny factor vs the composite signal.

### Higher leverage (3.0x+): MIXED
- 3.0x: +15pp return but +8pp MaxDD. Net: slightly worse Calmar.
- 3.5x: +0pp return but +17pp MaxDD. Net: strictly worse.
- 4.0x: −23pp return, +28pp MaxDD. Worse on both dimensions.
- In 2023, 2.6x leverage is already near-optimal. Higher leverage amplifies the early-year drawdown proportionally more than the gains.

---

## Structural Observations

### Universe size is the hard ceiling
- 2023: 53 tokens with 5-min positioning data
- 2025: 227 tokens
- The 4.3× universe difference explains most of the performance gap between years. More tokens = better conviction ranking = higher-quality trade selection.

### The composite signal WORKS — it's the filters that break it
- Every improvement in 2023 came from REMOVING restrictions (blacklist, RSI filter, shorter z-score window), not from adding new logic.
- The positioning composite at zw=22 produces Sharpe 2.75 when unfiltered. The filters were tuned to 2025 and actively harm 2023.

### The optimal 2023 config would HURT 2025
- The same parameter changes that turn 2023 from −20% to +313% would likely reduce 2025's +747% significantly (especially removing the blacklist, which was specifically curated for 2025).
- This is the fundamental tension: parameters optimized for one regime don't transfer to another.

---

## Recommended Regime-Adaptive Strategy Architecture

Based on 2023 findings, the ideal s523h would use BTC monthly candles to select per-regime parameters:

```
At each daily evaluation:

1. Compute BTC regime from last 1-3 monthly candles:
   - If last month green → BULL MODE
   - If last month red → BEAR MODE (allow shorts)
   - If 3-month average red → DEEP BEAR (allow shorts + flip signs)

2. In BULL MODE:
   - DIRECTION = "long" (no shorts)
   - TOKEN_BLACKLIST = empty (let the signal decide)
   - RSI filter = OFF (don't restrict trend entries)
   - Z-score window = 22 days (reactive to trend changes)

3. In BEAR MODE:
   - DIRECTION = "short" (or "both" with conditional longs)
   - TOKEN_BLACKLIST = empty
   - RSI filter = ON (timing matters more in choppy bears)
   - Z-score window = 30 days (more conservative, avoid whipsaws)

4. Position sizing:
   - LEVERAGE = 2.6x (constant — higher doesn't help)
   - MAX_POSITIONS = 30 (never binding with <60 tokens anyway)
```

This architecture uses the MONTHLY timeframe context the user suggested to dynamically adjust the strategy's behavior. The monthly candle gate (`short_if_1mo_red`) was the most promising multi-year result, and the per-regime parameter settings follow directly from the 2023 optimization findings.

---

## Next Steps

1. **Optimize 2022** (bear year, BTC −65%) with the same systematic approach
2. **Optimize 2024** (bull year, BTC +120%)
3. **Cross-validate**: run the 2023-optimal config on 2022/2024/2025 and measure degradation
4. **Build s523h with regime switching**: implement the architecture above as a new strategy variant
5. **51-month full backtest** of the regime-switching variant through the engine
