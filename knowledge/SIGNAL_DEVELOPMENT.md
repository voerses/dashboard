# Signal Development -- Structured Best Practices

> **TL;DR -- Six-layer signal stack**
> - Mandatory layers: Regime Filter -> Trend Alignment -> Entry Signal -> Volume Confirmation -> Exit Logic -> Sizing
> - ADX is #1 predictor (IC=0.067); RSI standalone is useless; simple beats complex (4 conditions > 10)
> - Universal exits: 3x ATR stop, 3x trail, 999 target, 24h no-stop, 18h min hold, 720h max, exit on CRISIS/DOWNTREND
> - Regime detection uses expanding quantiles (causal) -- never full-array statistics
> **When to read full file:** Designing a new strategy, composing signal layers, choosing entry signal type
> **Sections:** 1-Signal Stack, 2-Regime, 3-Trend, 4-Entry, 5-Volume, 6-Exit, 7-Sizing, 8-Composition, 9-Discovery Checklist, 10-Empirical Rules

## 1. THE SIGNAL STACK

Every strategy MUST include layers 1-4. Layers 5-6 are optional enhancements.

```
Layer 6: Position Sizing (edge estimate)     Optional
Layer 5: Exit Logic (regime + trail + RSI)   Optional overrides
Layer 4: Volume Confirmation                 MANDATORY
Layer 3: Entry Signal (core hypothesis)      MANDATORY
Layer 2: Trend Alignment                     MANDATORY
Layer 1: Regime Filter                       MANDATORY
```

Final entry = intersection of boolean masks:
```python
entry = regime_ok & trend_ok & core_signal & vol_ok
entry[:warmup] = False
```

---

## 2. LAYER 1: REGIME FILTER

Avoid trading in hostile conditions. Every Tier A strategy uses this.

```python
# Engine provides 5 regimes via detect_daily_regime():
# 0=CRISIS, 1=QUIET, 2=UPTREND, 3=RANGE, 4=DOWNTREND
regime_ok = ctx.regime_1h != 0  # exclude crisis (minimum)

# In StrategyResult:
exit_regimes={CRISIS, DOWNTREND}  # force exit on regime shift
```

**Detection internals:** ADX for trend strength (>25), EMA 20/50 crossover for direction, expanding volatility percentiles (20d vol vs causal p75/p25), crisis = vol > 2x expanding p75. 5-state classification broadcast to 1H.

### Look-Ahead Safety (CRITICAL)

Regime at bar `i` uses **only bars 0..i** (causal):
- Volatility thresholds: `pd.Series.expanding(min_periods=60).quantile()` -- each bar sees only its own history
- Before `min_periods` (60 daily bars ~ 3 months): defaults to regime 3 (RANGE)
- **Bug fixed March 2026:** Original used `np.percentile()` on full array (future leak). Now uses expanding windows.

**Rules:** Never `np.percentile/mean/std` on full array. Always `.expanding()/.rolling()` with `min_periods`. See `knowledge/process/BACKTESTING_VALIDATION_BEST_PRACTICES.md` Section 10.

| What | Do | Don't |
|------|-----|-------|
| Crisis filter | Always exclude regime 0 | Skip regime filtering |
| Exit regimes | `{CRISIS, DOWNTREND}` | Empty set |
| Long strategies | Exclude CRISIS + DOWNTREND entries | Trade all regimes |
| Mean reversion | Exclude CRISIS only | Exclude UPTREND |

**Tier A validation:** s11 (75.5%), s09 (73.5%), s17 (55.1%) all use `regime_ok = ctx.regime_1h != 0`.

---

## 3. LAYER 2: TREND ALIGNMENT

Enter in direction of prevailing trend. Top 6 strategies are ALL trend-following.

**Implementations (ranked by validation):**

| Pattern | Strategy | Code |
|---------|----------|------|
| Simple EMA | s11 (75.5%) | `close > ema_20` |
| EMA Stack + Daily | s09 (73.5%) | `(close > ema10) & (ema10 > ema20)` + daily EMA50 |
| ADX Strength | s09, s17 | `(adx > 25) & (plus_di > minus_di)` |

**Signal Lab IC (post-ETF):**

| Signal | 1d IC | 5d IC | 10d IC |
|--------|-------|-------|--------|
| adx | +0.017 | +0.033 | +0.067 |
| ema_stack | +0.008 | +0.012 | +0.015 |
| supertrend_dir | +0.023 | +0.018 | +0.011 |

**Rules:** ADX > 20-30 minimum (#1 predictor). Multi-TF stacking works (s09 = 73.5%). Two trend conditions is enough -- three cuts signal.

---

## 4. LAYER 3: ENTRY SIGNAL

The core hypothesis -- the specific condition creating an edge.

### Proven Entry Types

| Type | Strategy | Code | Why It Works |
|------|----------|------|--------------|
| Momentum Burst | s11 (75.5%) | `ret_1 > 0.03` | IC=+0.074 in quiet; institutional order flow |
| Momentum Burst (lower) | s17 (55.1%) | `ret_1 > 0.02` | Same mechanism, wider net |
| Momentum Accel | s18 (51.0%) | `ret_24h > ret_72h/3` | Change in momentum, not level |
| Vol-Weighted TSMOM | s13 (67.3%) | `vw_cum > 0` | Volume confirms conviction |
| Skew Momentum | s21 (63.3%) | `rolling_skew > 0.3` | Positive skew = asymmetric upside |

### Entry Types That DON'T Work

| Entry Type | Best Rate | Why |
|------------|-----------|-----|
| RSI oversold alone | 10.2% | Too many false signals in crypto |
| OBV divergence | 6.1% | Volume divergence doesn't predict crypto |
| Composite factors | 0.0% | Averaging weak signals = diluted noise |
| Mean reversion | 0.0% | Crypto trends; reversion = buying falling knives |
| BB squeeze alone | 32.7% | Too rare, timing uncertain |

**Design rules:**
1. ONE core signal, not five. s11 uses 1 entry + filters (75.5%). s16 uses 10 -> 0%.
2. IC > +0.02 post-ETF required. Check Signal Lab.
3. Match signal horizon to holding period (ret_1 for swing, ret_24h for medium-term).
4. Asymmetric signals outperform (skew, acceleration, bursts > level-based).

---

## 5. LAYER 4: VOLUME CONFIRMATION

Confirm price moves have participation. Volume-less moves are noise.

```python
vol_ratio = ctx.ind_1h['vol_ratio']  # current vol / 20-bar average
vol_ok = vol_ratio > 1.0   # standard (s11, s17)
vol_ok = vol_ratio > 0.8   # relaxed (s18, s22)
vol_ok = vol_ratio > 1.5   # breakout strategies (s12)
```

| Signal | IC | Notes |
|--------|-----|-------|
| vol_ratio | +0.039 | Solid across horizons |
| taker_buy_ratio | +0.014 | 1d only, decays fast |
| volume_momentum | +0.039 | Rising volume precedes moves |

**Rules:** Min threshold 0.8x. Breakouts need 1.5x. Don't over-filter (>2.0 cuts valid entries). Use `ctx.ind_1h['vol_ratio']` -- don't recompute.

---

## 6. LAYER 5: EXIT LOGIC

Engine handles most exits via StrategyResult -- strategies rarely need custom exit code.

```python
return StrategyResult(
    stop_mult=3.0,        # 3x ATR initial stop (universal Tier A)
    trail_mult=3.0,       # 3x ATR trailing (matches initial)
    target_mult=999,      # No fixed target -- trail handles upside
    no_stop_bars=24,      # 24h protection window (biggest single lever)
    min_hold=18,          # Minimum 18h (avoid noise exits)
    max_hold=720,         # Maximum 30 days
    exit_regimes={CRISIS, DOWNTREND},
)
```

| Parameter | Optimal | Evidence |
|-----------|---------|----------|
| stop_mult | 3.0 | All-token sweep: 3x beats 5x by +$9K/yr |
| no_stop_bars | 24 | CPCV sweep: biggest single improvement |
| trail_mult | 3.0 | Match stop_mult for consistency |
| target_mult | 999 | Trail-only beats fixed targets in trending crypto |
| min_hold | 18 | Below 18h, noise exits dominate |
| max_hold | 720 | Beyond 30d, edge decays to zero |

**Rules:** Don't override engine exits. 3x ATR universal. 24-bar protection mandatory. Regime exits handle macro risk.

---

## 7. LAYER 6: POSITION SIZING

Engine handles via `edge` parameter and modified Kelly:
```python
edge=0.40  # Tier A: 0.35-0.45
# position_size = min(edge * capital / n_trades, max_position_usd(ticker, capital, adv))
```

| Liquidity Tier | Tokens | Max Position % |
|----------------|--------|---------------|
| 1 (BTC, ETH) | 2 | 15% |
| 2 (SOL, XRP, etc.) | ~10 | 8% |
| 3 (FLOKI, BONK, etc.) | ~37 | 3% |

**Rules:** Conservative edge estimate (0.40 = 40% P(profit)). Tier 3 gets small positions regardless of signal strength. Don't dynamically adjust edge in strategy code.

---

## 8. COMPOSITION PATTERNS

### Pattern A: Proven Stack (Tier A template) -- 4 conditions

```python
entry = (ctx.regime_1h != 0) & (close > ema20) & (adx > 25) & (ret_1 > 0.03) & (vol_ratio > 1.0)
entry[:200] = False
```

### Pattern B: Multi-Timeframe (s09, 73.5%)

```python
signal_1h = (close > ema10) & (ema10 > ema20)
ema50_d = ctx.align_daily_to_1h(ctx.ind_d['ema_50'])
entry = signal_1h & (close > np.nan_to_num(ema50_d, 0)) & volume_ok
```

### Pattern C: Acceleration / Derivative (s18, s21)

```python
acceleration = ret_24h - ret_72h / 3
entry = (acceleration > 0) & (momentum > 0.005)
```

### Anti-Pattern: Kitchen Sink (s16: 0.0%)

10 conditions = virtually no entries. **More signals != more alpha. Fewer, stronger signals win.**

---

## 9. SIGNAL DISCOVERY CHECKLIST

```
1. [ ] Signal Lab IC > +0.02 post-ETF: python tools/signal_lab.py --signal <name> --post-etf
2. [ ] IC stable across 2+ horizons (1d, 5d, 10d)
3. [ ] Works in UPTREND + QUIET; check if sign flips in CRISIS
4. [ ] Correlation with Tier A entries: >0.8 = redundant, <0.3 = new alpha
5. [ ] Signal type: Level (regime-dependent) vs Change (robust) vs Event (needs volume)
6. [ ] IC survives after tier-based transaction costs (v3/universe.py TIER_COSTS)
7. [ ] No look-ahead bias: no full-array stats, expanding() with min_periods only
8. [ ] Compose using Proven Stack (4 conditions)
9. [ ] Validate with V3 dual gate
```

---

## 10. EMPIRICAL RULES (from 16 strategies across 49 tokens)

1. **Trend-following is the only consistent crypto edge.** Every Tier A is trend-following. Mean reversion loses money.
2. **ADX is #1 predictor.** IC=+0.067, increases with horizon.
3. **Momentum bursts work.** >2-3% hourly returns = institutional flow revealing itself.
4. **Volume confirms everything.** No volume confirmation = no edge.
5. **Simple beats complex.** 4 conditions (s11, 75.5%) crushes 10 conditions (s16, 0.0%).
6. **3x ATR stop is universal.** Calibrated to crypto volatility.
7. **24h protection mandatory.** Prevents stop-hunting (#1 PnL drag).
8. **Post-ETF is a different market.** Always check post-ETF IC values.
9. **Mean reversion is a trap.** IC=+0.022 post-ETF but loses after costs. s19 (0.0%) proves it.
10. **Vectorize or die.** 100ms/call makes validation impractical. Fast iteration is the real edge.
