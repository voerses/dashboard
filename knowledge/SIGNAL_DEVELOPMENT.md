# Signal Development — Structured Best Practices

## Overview

Every strategy is a composition of signal layers. This document defines the standard
structure, proven patterns, and composition rules based on our validated results.

The core principle: **Tier A strategies all share the same structure.** They differ in
which specific signals they use at each layer, but the layering is consistent.

---

## 1. THE SIGNAL STACK

Every strategy MUST include layers 1-4. Layers 5-6 are optional enhancements.

```
┌─────────────────────────────────────────────┐
│ Layer 6: Position Sizing (edge estimate)    │  Optional
├─────────────────────────────────────────────┤
│ Layer 5: Exit Logic (regime + trail + RSI)  │  Optional overrides
├─────────────────────────────────────────────┤
│ Layer 4: Volume Confirmation                │  MANDATORY
├─────────────────────────────────────────────┤
│ Layer 3: Entry Signal (core hypothesis)     │  MANDATORY
├─────────────────────────────────────────────┤
│ Layer 2: Trend Alignment                    │  MANDATORY
├─────────────────────────────────────────────┤
│ Layer 1: Regime Filter                      │  MANDATORY
└─────────────────────────────────────────────┘
```

Each layer is a boolean mask. The final entry signal is their intersection:
```python
entry = regime_ok & trend_ok & core_signal & volume_ok
entry[:warmup] = False
```

---

## 2. LAYER 1: REGIME FILTER

**Purpose:** Avoid trading in hostile market conditions. This is the single most
important risk control — every Tier A strategy uses it.

### Standard Implementation

```python
# Engine provides 5 regimes via detect_daily_regime():
# 0 = CRISIS, 1 = QUIET, 2 = UPTREND, 3 = RANGE, 4 = DOWNTREND
regime_ok = ctx.regime_1h != 0  # exclude crisis (minimum)
```

### Regime Constants (from engine.py)

```python
from engine import CRISIS, DOWNTREND, RANGE, QUIET, UPTREND

# In StrategyResult:
exit_regimes={CRISIS, DOWNTREND}  # force exit when regime shifts to these
```

### Regime Detection Internals

The engine's `detect_daily_regime()` uses:
- **SMA 200 slope** for long-term trend direction
- **Daily volatility percentile** (20d realized vol vs 120d history)
- **Drawdown from 60d high** for crisis detection
- 5-state classification broadcast to 1H bars

### Best Practices

| What | Do | Don't |
|------|-----|-------|
| Crisis filter | Always exclude regime 0 | Skip regime filtering |
| Exit regimes | Set `exit_regimes={CRISIS, DOWNTREND}` | Use empty set |
| Long strategies | Exclude CRISIS + DOWNTREND entries | Trade all regimes |
| Mean reversion | Exclude CRISIS only | Exclude UPTREND (MR works in trends) |
| Regime transitions | Let trailing stop handle exit | Add complex regime-change logic |

### Proven Patterns from Tier A Strategies

- **s11 (75.5%):** `regime_ok = ctx.regime_1h != 0` — simple crisis exclusion
- **s09 (73.5%):** Same + daily trend confirmation via EMA50
- **s17 (55.1%):** Same — regime filter is the baseline, not the edge

---

## 3. LAYER 2: TREND ALIGNMENT

**Purpose:** Only enter in the direction of the prevailing trend. This is where
most of the alpha comes from — our top 6 strategies are ALL trend-following.

### Standard Implementations (ranked by validation rate)

**EMA Stack (s09: 73.5%):**
```python
ema10 = ctx.ind_1h['ema_10']
ema20 = ctx.ind_1h['ema_20']
ema_stack = (close > ema10) & (ema10 > ema20)

# Optional: daily confirmation
ema50_d = ctx.align_daily_to_1h(ctx.ind_d['ema_50'])
daily_trend = close > np.nan_to_num(ema50_d, 0)
```

**Simple EMA (s11: 75.5%, s17: 55.1%):**
```python
trend_ok = close > ctx.ind_1h['ema_20']
```

**ADX Trend Strength (s09: 73.5%, s17: 55.1%):**
```python
adx = ctx.ind_1h['adx']
plus_di = ctx.ind_1h['plus_di']
minus_di = ctx.ind_1h['minus_di']
strong_trend = (adx > 25) & (plus_di > minus_di)
```

### Signal Lab IC Values (post-ETF)

| Signal | 1d IC | 5d IC | 10d IC | Trend |
|--------|-------|-------|--------|-------|
| adx | +0.017 | +0.033 | +0.067 | Strengthens with horizon |
| ema_stack | +0.008 | +0.012 | +0.015 | Weak but persistent |
| supertrend_dir | +0.023 | +0.018 | +0.011 | Flipped from negative pre-ETF |

### Best Practices

- **Use ADX > 20-30 as minimum.** ADX is the #1 predictive indicator.
- **Multi-timeframe stacking works.** s09 uses 1H + Daily EMA alignment → 73.5%.
- **Don't over-filter.** Two trend conditions is enough. Three starts cutting signal.
- **ADX threshold matters:** 20 = any trend, 25 = moderate, 30 = strong only.
  Higher threshold → fewer trades but higher quality.

---

## 4. LAYER 3: ENTRY SIGNAL

**Purpose:** The core hypothesis — the specific market condition that creates an edge.

### Proven Entry Types (from Tier A)

**Momentum Burst (s11: 75.5%, s17: 55.1%):**
```python
ret_1 = ctx.ind_1h['ret_1']
burst = ret_1 > 0.03  # 3% hourly move (s11)
burst = ret_1 > 0.02  # 2% hourly move (s17, lower threshold)
```
- IC: ret_1 has IC=+0.074 in quiet regimes
- Works because: large hourly moves indicate institutional order flow
- Decay: strong at 1d, fades by 5d → suitable for swing entry

**Momentum Acceleration (s18: 51.0%):**
```python
ret_24h = np.zeros(n); ret_24h[24:] = close[24:] / close[:-24] - 1
ret_72h = np.zeros(n); ret_72h[72:] = close[72:] / close[:-72] - 1
accel = ret_24h > ret_72h / 3  # short-term outpacing medium-term
```
- Works because: accelerating momentum predicts continuation (Frog-in-the-Pan)
- Key: it's the CHANGE in momentum, not the level

**Volume-Weighted TSMOM (s13: 67.3%):**
```python
vw_ret = log_ret * (volume / vol_avg)  # volume-weighted returns
vw_cum = pd.Series(vw_ret).rolling(lookback).sum().values
signal = vw_cum > 0  # positive volume-weighted momentum
```
- Works because: volume confirms conviction behind price moves
- Weighting by relative volume filters noise from low-activity periods

**Skew Momentum (s21: 63.3%):**
```python
from engine import rolling_skew
skew_20d = rolling_skew(log_ret, 480)  # 20-day rolling skew
positive_skew = skew_20d > 0.3  # right-tail dominant
```
- Works because: positive skew = asymmetric upside, continuation likely

### Entry Types That DON'T Work

| Entry Type | Best Rate | Why It Fails |
|------------|-----------|--------------|
| RSI oversold alone | 10.2% | Too many false signals in crypto |
| OBV divergence | 6.1% | Volume divergence doesn't predict crypto moves |
| Composite factors | 0.0% | Averaging weak signals = diluted noise |
| Mean reversion | 0.0% | Crypto trends; reversion is buying falling knives |
| BB squeeze alone | 32.7% | Too rare, timing uncertain |

### Design Rules

1. **One core signal, not five.** s11 uses ONE entry signal (ret_1 > 0.03) plus
   filters. s16 uses TEN signals → 0% validation. Complexity kills.
2. **The entry signal must have IC > +0.02 post-ETF.** Check Signal Lab.
3. **Match signal horizon to holding period.** ret_1 (hourly) for swing entry,
   ret_24h (daily) for medium-term momentum.
4. **Asymmetric signals outperform.** Skew, acceleration, bursts — anything that
   captures tail events works better than level-based signals.

---

## 5. LAYER 4: VOLUME CONFIRMATION

**Purpose:** Confirm that price moves have participation. Volume-less moves are noise.

### Standard Implementation

```python
vol_ratio = ctx.ind_1h['vol_ratio']  # current vol / 20-bar average
vol_ok = vol_ratio > 1.0  # at least average volume (s11, s17)
vol_ok = vol_ratio > 0.8  # slightly relaxed (s18, s22)
vol_ok = vol_ratio > 1.5  # strong expansion (s12, breakout strategies)
```

### Volume Signal IC (post-ETF)

| Signal | IC | Notes |
|--------|-----|-------|
| vol_ratio | +0.039 | Solid, works across horizons |
| taker_buy_ratio | +0.014 | 1d only, decays fast |
| volume_momentum | +0.039 | Rising volume precedes moves |

### Best Practices

- **Minimum threshold: 0.8x average.** Below this, price moves are unreliable.
- **For breakout strategies: 1.5x minimum.** Breakouts without volume are fakeouts.
- **Don't over-filter volume.** vol_ratio > 2.0 cuts too many valid entries.
- **vol_ratio is pre-computed.** Use `ctx.ind_1h['vol_ratio']`, don't recompute.

---

## 6. LAYER 5: EXIT LOGIC

**Purpose:** Control how and when trades close. The engine handles most exit logic
via StrategyResult parameters — strategies rarely need custom exit code.

### Standard Parameters (from Tier A)

```python
return StrategyResult(
    # ... entry logic ...
    stop_mult=3.0,        # 3x ATR initial stop (universal across Tier A)
    trail_mult=3.0,       # 3x ATR trailing stop (matches initial)
    target_mult=999,      # No fixed target — trail handles upside
    no_stop_bars=24,      # 24h protection window (biggest single lever)
    min_hold=18,          # Minimum 18 hours (avoid noise exits)
    max_hold=720,         # Maximum 30 days
    exit_regimes={CRISIS, DOWNTREND},  # Force exit on regime shift
)
```

### Parameter Insights (from validation data)

| Parameter | Optimal | Evidence |
|-----------|---------|----------|
| stop_mult | 3.0 | All-token sweep: 3x beats 5x by +$9K/yr |
| no_stop_bars | 24 | CPCV sweep: biggest single improvement |
| trail_mult | 3.0 | Match stop_mult for consistency |
| target_mult | 999 | Trail-only beats fixed targets in trending crypto |
| min_hold | 18 | Below 18h, noise exits dominate |
| max_hold | 720 | Beyond 30d, edge decays to zero |

### Special Exit Logic

**RSI Exit (mean reversion strategies only):**
```python
rsi_exit_level=70  # exit when RSI > 70 (overbought = reversion complete)
```

**Convex Exit (experimental):**
```python
convex_exit=True,
mean_target_vals=target_price_array  # price targets for convex scaling
```

### Rules

- **Don't override engine exits.** The stop/trail/regime system is well-calibrated.
- **3x ATR is universal.** Every Tier A strategy uses it. Don't experiment with 2x or 5x.
- **24-bar protection is mandatory.** It prevents stop-hunting in the first day.
- **Regime exits handle macro risk.** No need for custom drawdown logic.

---

## 7. LAYER 6: POSITION SIZING

**Purpose:** Scale position size based on edge estimate and liquidity.

### Standard Implementation

The engine handles sizing via the `edge` parameter in StrategyResult:

```python
edge=0.40  # Tier A strategies: 0.35-0.45
edge=0.30  # Tier B strategies: lower confidence
```

The engine uses a modified Kelly criterion:
```
position_size = min(
    edge * capital / n_concurrent_trades,
    max_position_usd(ticker, capital, adv)  # liquidity limit
)
```

### Liquidity Tiers (from universe.py)

| Tier | Tokens | Max Position % |
|------|--------|---------------|
| 1 (BTC, ETH) | 2 | 15% of capital |
| 2 (SOL, XRP, etc.) | ~10 | 8% of capital |
| 3 (FLOKI, BONK, etc.) | ~37 | 3% of capital |

### Rules

- **Edge estimate must be conservative.** 0.40 means "40% probability of profit after costs."
- **Tier 3 tokens get small positions regardless of signal strength.** Liquidity is the binding constraint.
- **Don't dynamically adjust edge in strategy code.** The engine handles this.

---

## 8. COMPOSITION PATTERNS

### Pattern A: The Proven Stack (Tier A template)

```python
# Layer 1: Regime
regime_ok = ctx.regime_1h != 0

# Layer 2: Trend
trend_ok = (close > ema20) & (adx > 25)

# Layer 3: Entry (ONE core signal)
core_signal = ret_1 > 0.03

# Layer 4: Volume
vol_ok = vol_ratio > 1.0

# Compose
entry = regime_ok & trend_ok & core_signal & vol_ok
entry[:200] = False
```

**4 conditions.** Not 3, not 7. Four boolean masks intersected.

### Pattern B: Multi-Timeframe Confirmation

```python
# 1H signal
signal_1h = (close > ema10) & (ema10 > ema20)

# Daily confirmation (aligned to 1H)
ema50_d = ctx.align_daily_to_1h(ctx.ind_d['ema_50'])
signal_d = close > np.nan_to_num(ema50_d, 0)

# Combine
entry = signal_1h & signal_d & volume_ok
```

Used by s09 (73.5%). The daily filter adds significant alpha.

### Pattern C: Acceleration / Derivative Signals

```python
# Not the level, but the CHANGE in the signal
momentum = ret_24h
acceleration = ret_24h - ret_72h / 3  # short-term outpacing medium
entry = (acceleration > 0) & (momentum > 0.005)
```

Used by s18 (51.0%), s21 (63.3%). Second-derivative signals catch the
"second wave" of momentum.

### Anti-Pattern: The Kitchen Sink

```python
# DON'T do this — s16 (0.0% validation)
entry = (z_mom > 1.5) & (z_vol < -1.0) & (z_vov < -0.5) & \
        (z_skew > 0) & (z_near_high > 0) & (z_bb_squeeze > 1.0) & \
        (z_vol_expand > 0) & (z_corr > 0) & (z_regime > 0) & trend_ok
```

10 conditions = virtually no entries. Each weak signal adds noise.
**More signals ≠ more alpha. Fewer, stronger signals win.**

---

## 9. SIGNAL DISCOVERY CHECKLIST

When developing a new strategy:

```
1. [ ] Run Signal Lab to get IC values for candidate signals
       python v2/signal_lab.py --signal <name> --post-etf

2. [ ] Verify IC > +0.02 post-ETF at target horizon

3. [ ] Check signal stability across regimes
       - Does it work in both UPTREND and QUIET?
       - Does it flip sign in CRISIS? (if so, use as regime filter instead)

4. [ ] Check correlation with existing Tier A entry signals
       - If corr > 0.8 with s11 entry, it's redundant
       - If corr < 0.3, it's genuinely new alpha

5. [ ] Determine signal type:
       - Level (RSI < 30) → prone to regime dependence
       - Change (acceleration > 0) → more robust across regimes
       - Event (BB squeeze break) → low frequency, needs volume confirmation

6. [ ] Compose using the Proven Stack pattern (4 conditions)

7. [ ] Validate with V3 dual gate
```

---

## 10. WHAT WE'VE LEARNED (EMPIRICAL RULES)

From validating 16 strategies across 49 tokens:

1. **Trend-following is the only consistent edge in crypto.** Every Tier A strategy
   is fundamentally trend-following. Mean reversion loses money.

2. **ADX is the #1 predictor.** IC=+0.067, increases with horizon. Use it.

3. **Momentum bursts work.** Large hourly returns (>2-3%) predict continuation.
   This is institutional order flow revealing itself.

4. **Volume confirms everything.** No volume confirmation → no edge.

5. **Simple beats complex.** 4 conditions > 10 conditions. s11 (4 conditions, 75.5%)
   crushes s16 (10 conditions, 0.0%).

6. **3x ATR stop is universal.** Every Tier A strategy uses 3.0. It's calibrated
   to crypto's volatility structure.

7. **24-hour protection window is mandatory.** Prevents stop-hunting, the #1 PnL
   drag in crypto.

8. **Post-ETF is a different market.** Signals that worked pre-2024 may not work now.
   Always check post-ETF IC values.

9. **Mean reversion is a trap.** It looks like it should work (IC=+0.022 post-ETF)
   but loses money after costs. The only safe contrarian play is waiting for
   extreme conditions — and even then, s19 (0.0%) proves it's unreliable.

10. **Vectorize or die.** A strategy that takes 100ms/call makes validation
    impractical. Vectorized strategies enable fast iteration, which is the real edge.
