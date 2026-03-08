# Signal Development -- Structured Best Practices

> **TL;DR -- Six-layer signal stack + automated discovery findings**
> - Mandatory layers: Regime Filter -> Trend Alignment -> Entry Signal -> Volume Confirmation -> Exit Logic -> Sizing
> - ADX is #1 manual predictor (IC=0.067); automated discovery found 252 significant features across 55 tokens
> - **Top automated signals:** cross-TF divergence (`ret_1_1h_vs_4h` IC=-0.376, STABLE), regime-conditional EMAs (IC=-0.67), Donchian crisis reversal (IC=-0.82)
> - 92% of signals are sign-consistent across horizons; 168h peak is most common
> - Universal exits: 3x ATR stop, 3x trail, 999 target, 24h no-stop, 18h min hold, 720h max, exit on CRISIS/DOWNTREND
> **When to read full file:** Designing a new strategy, composing signal layers, interpreting discovery results
> **Sections:** 1-Signal Stack, 2-Regime, 3-Trend, 4-Entry, 5-Volume, 6-Exit, 7-Sizing, 8-Composition, 9-Discovery Checklist, 10-Empirical Rules, 11-Automated Discovery Findings, 12-Signal Health, 13-Causality, 14-Horizon Selection

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

---

## 11. AUTOMATED SIGNAL DISCOVERY FINDINGS (55 tokens, 300+ features, March 2026)

Automated discovery via `tools/signal_discovery/` scanned 300+ features across 55 tokens with walk-forward IC evaluation, FDR correction (alpha=0.05), and multi-horizon testing (1, 4, 24, 72, 168h). Results in `outputs/signal_discovery/`.

**252 signals survived FDR filtering** (|IC| >= 0.02, t-stat >= 2.0).

### CRITICAL LESSON: Signals Work as Overlays, NOT Standalone Strategies

Raw signal-based entries (e.g., enter when `ret_1_1h_vs_4h` z-score > threshold) **did not generate positive returns** as standalone strategies. Every standalone signal strategy was killed (s56 max_leverage_momentum: 14% rate, negative mean return).

**Why:** IC (information coefficient) measures statistical correlation with future returns, but:
- IC of -0.376 means the signal explains ~14% of return variance — not enough for standalone edge after costs
- Standalone entries lack the structural edge (basis premium, carry, momentum) that generates P&L
- Signal timing alone can't overcome transaction costs without an underlying profitable mechanism

**What works:** Apply discovered signals as **overlays on existing well-performing strategies**:
- s57/s58 use s44 basis carry as the base (proven profitable mechanism)
- Signal discovery outputs improve **entry timing** (enter when composite signal agrees)
- Signal IC weights **position sizing** (scale size by signal confidence per regime)
- Signal health data enables **regime conditioning** (per-regime IC → regime-adaptive sizing)

**Bottom line: IC != tradeable edge. Signals improve existing strategies, they don't replace them.**

### Top Signals by Regime

| Regime | #1 Signal | IC | #2 Signal | IC | Key Insight |
|--------|-----------|----|-----------|----|-------------|
| CRISIS | `donch_high_in_CRISIS` | -0.818 | `bb_width_mom_48h` | -0.320 | Strong reversal from Donchian highs; expanding vol = more downside |
| QUIET | `ema_50_in_QUIET` | -0.621 | `macd_in_QUIET` | -0.403 | Trend indicators (EMA, MACD) dominate in low-vol environments |
| UPTREND | `ema_50_in_UPTREND` | -0.671 | `atr_in_UPTREND` | -0.298 | EMA position is strongest predictor; ATR adds momentum confirmation |
| RANGE | `ema_10_in_RANGE` | -0.614 | `macd_in_RANGE` | -0.350 | Short EMA + MACD trend for range breakout direction |
| DOWNTREND | `ema_10_in_DOWNTREND` | -0.594 | `macd_in_DOWNTREND` | -0.263 | EMA position predicts continuation; negative IC = below EMA → more down |

### Cross-Timeframe Signals (Strongest New Finding)

Cross-timeframe divergence is the most robust signal class discovered — **STABLE over time, works across all regimes:**

| Signal | IC | Stability | Best Horizon | Mechanism |
|--------|----|-----------|--------------|-----------|
| `ret_1_1h_vs_4h` | -0.376 | STABLE (drift < 0.001/yr) | 4h | 1h return z-score minus 4h z-score; captures mean-reverting micro-momentum |
| `rsi_1h_vs_4h` | -0.291 | STABLE (drift = 0.001/yr) | 1h | RSI divergence between timeframes; when 1h RSI leads 4h, reversal imminent |
| `plus_di_1h_vs_4h` | -0.137 | — | 1h | Directional index divergence |
| `vol_ratio_1h_vs_4h` | varies | — | 1h | Volume divergence between timeframes |

**Why they work:** Cross-TF signals exploit information lag — when short-term (1h) indicators diverge from medium-term (4h), the short-term usually reverts. Negative IC means high cross-TF divergence predicts lower returns (mean-reversion at the micro level works, unlike macro mean-reversion which fails in crypto).

### Universal Signals (Work Across All Regimes)

| Signal | All-Regime IC | Regime Range | Note |
|--------|---------------|--------------|------|
| `ret_1_1h_vs_4h` | -0.376 | -0.268 to -0.381 | Most regime-robust signal found |
| `rsi_1h_vs_4h` | -0.291 | -0.271 to -0.335 | RSI cross-TF nearly as stable |
| `minus_di_in_RANGE` | +0.270 | Range-specific | Rising -DI in range predicts breakdowns |
| `bars_in_regime` (CRISIS) | +0.221 | Crisis-specific | Longer crisis = rebound more likely |

---

## 12. SIGNAL HEALTH (Rolling IC Stability)

Of the top 30 signals tested over rolling 1-year windows:

| Status | Count | Meaning | Implication |
|--------|-------|---------|-------------|
| STABLE | 11 (37%) | IC drift < 0.005/yr | Safe for live trading |
| DECAYING | 13 (43%) | IC shrinking over time | Use with caution; may need refit |
| STRENGTHENING | 5 (17%) | IC growing over time | Emerging edge; monitor |
| DEAD | 1 (3%) | IC crossed zero | Discard |

### Most Reliable Signals (STABLE, high IC)

| Signal | Horizon | Early IC | Late IC | Annual Drift |
|--------|---------|----------|---------|--------------|
| `donch_high_in_CRISIS` | 168h | -0.440 | -0.415 | +0.003 |
| `ret_1_1h_vs_4h` | 1h | -0.337 | -0.339 | -0.001 |
| `ret_1_1h_vs_4h` | 4h | -0.336 | -0.338 | -0.001 |
| `rsi_1h_vs_4h` | 1h | -0.272 | -0.270 | +0.001 |
| `rsi_1h_vs_4h` | 4h | -0.256 | -0.255 | +0.000 |

**Key lesson:** Cross-timeframe signals are the most temporally stable. Regime-conditional EMAs (e.g., `ema_50_in_QUIET`) decay fast (IC shift > 0.4 over period) — they overfit to specific market epochs.

### DECAYING Signals (Use With Caution)

| Signal | Horizon | Early IC | Late IC | Annual Drift |
|--------|---------|----------|---------|--------------|
| `ema_50_in_QUIET` | 168h | -0.528 | -0.120 | +0.142 |
| `ema_50_in_QUIET` | 72h | -0.503 | -0.159 | +0.126 |
| `ema_10_in_DOWNTREND` | 168h | -0.180 | -0.154 | +0.023 |
| `donch_high_in_CRISIS` | 72h | -0.378 | -0.255 | +0.044 |

---

## 13. CAUSALITY (Lead/Lag Analysis)

Of 252 signals tested for forward vs reverse IC asymmetry:
- **57 LEADING** (forward IC >> reverse IC — genuine predictors)
- **111 LAGGING** (reverse IC ≥ forward IC — following price, not predicting)
- **84 SYMMETRIC** (unclear direction)
- **16 HIGH confidence** LEADING signals

### Top Leading Signals (Genuine Predictors)

| Signal | Horizon | Asymmetry | Confidence | Forward IC |
|--------|---------|-----------|------------|------------|
| `ema_10_in_DOWNTREND` | 4h | 48.3x | MEDIUM | -0.028 |
| `adx_in_CRISIS` | 72h | 23.7x | HIGH | +0.187 |
| `ema_50_in_QUIET` | 24h | 21.3x | MEDIUM | -0.049 |
| `ema_10_in_DOWNTREND` | 168h | 17.3x | HIGH | -0.155 |
| `ret_1_1h_vs_4h` | 4h | 13.3x | HIGH | -0.341 |
| `donch_high_in_CRISIS` | 168h | 11.7x | HIGH | -0.404 |

**Actionable insight:** Prioritize HIGH-confidence LEADING signals for strategies. LAGGING signals describe past returns (useful for regime detection but not entry timing). Cross-TF signals (`ret_1_1h_vs_4h`, `rsi_1h_vs_4h`) are consistently LEADING with HIGH confidence.

---

## 14. HORIZON SELECTION (IC Decay Curves)

Each signal was tested at 1h, 4h, 24h, 72h, 168h horizons. Peak horizon distribution:

| Peak Horizon | # Features | Top Signal | Peak IC |
|-------------|------------|------------|---------|
| 1h | 10 | `rsi_1h_vs_4h` | -0.291 |
| 4h | 7 | `ret_1_1h_vs_4h` | -0.376 |
| 24h | 16 | `bb_pct_in_CRISIS` | -0.236 |
| 72h | 12 | `bb_width_in_QUIET` | -0.208 |
| 168h | 28 | `donch_high_in_CRISIS` | -0.818 |

**92% of signals are sign-consistent** across all tested horizons — the signal direction is stable, only magnitude changes. This means a signal that works at 1h also works at 168h (just weaker/stronger).

### Horizon Selection Rules

1. **Short-horizon signals (1-4h):** Cross-TF divergence. Match with short holds (4-48h bars). Best for mean-reversion micro-entries.
2. **Medium-horizon signals (24-72h):** BB/MACD regime-conditional. Match with swing holds (24-168h bars). Best for momentum entries.
3. **Long-horizon signals (168h):** EMA position, Donchian channels. Match with position holds (72-720h bars). Best for trend-following.
4. **Rule of thumb:** Set `min_hold` to ~1/4 of peak horizon, `max_hold` to ~4x peak horizon.

### How to Use Discovery Results for Strategy Building

```python
# 1. Load per-token catalog
import json
with open('outputs/signal_discovery/signal_catalog_BTC.json') as f:
    catalog = json.load(f)

# 2. Filter: STABLE + LEADING + high IC
# Use rolling_ic_summary_{TOKEN}.csv for stability
# Use lead_lag_{TOKEN}.csv for causality

# 3. Pick 2-5 non-correlated signals (|pairwise_corr| < 0.7)
# 4. IC-weight into composite: sum(sign(ic) * |ic| * zscore(feature)) / sum(|ic|)
# 5. Entry when |composite z-score| > threshold
# 6. Set hold period from peak horizon of dominant signal
```

> Deep dive: `knowledge/process/SIGNAL_DISCOVERY_METHODS.md`, `outputs/signal_discovery/`
