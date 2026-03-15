# V3 Performance Patterns — Strategy & Engine Best Practices

> **TL;DR — Vectorize everything; <1ms target**
> - 0.04ms (s11 vectorized) vs 1,864ms (s16 loops) — 46,600x difference; total 111-token: 32s vs hours
> - Never `for i in range(n)` on bars; use `rolling_mean/std/max/min` helpers (137x faster)
> - Use pre-computed `ctx.ind_1h` (23+ indicators) and `ctx.custom` — never recompute
> - Boolean vectorization: `(close > sma) & (rsi < 30) & (adx > 25)` not per-bar if-else
> **When to read full file:** Writing a new strategy, profiling slow code, optimizing indicator computation
> **Sections:** 1-2-Performance Model, 3-5-Rules, 6-Checklist + Speed Tiers, 7-8-Engine Internals + Anti-Patterns, 9-Testing

---

## The Performance Model

```
Total time ≈ Σ tokens × (1 WF + 15 CPCV) × (T_build_context + T_strategy + T_simulate)
```

Measured per-component timings (BTC, 54,044 bars — updated 2026-03-03):

| Component | Time | Notes |
|-----------|------|-------|
| `_build_context` | 35-65ms | Indicators + aggregation + plugins + liquidity arrays |
| `strategy()` (s11, vectorized) | 0.04ms | Pure numpy, no loops |
| `strategy()` (s16, loops) | 1,864ms | 10+ Python for-loops |
| `_simulate()` (JIT, warm) | 7-8ms | Numba JIT loop over all bars (scales with bar count) |

Breakdown of `_build_context` (BTC, 54K bars):

| Sub-component | Time | Notes |
|---------------|------|-------|
| `compute_indicators_fast` (1h) | 21ms | Core indicators on 54K bars |
| `aggregate_to_timeframe` (4h + daily) | 9ms | pandas resample |
| `compute_indicators_fast` (4h + daily) | 5ms | Indicators on smaller frames |
| `_compute_obv` plugin | 2ms | Vectorized via `np.cumsum` |
| `_compute_momentum_signals` plugin | 3ms | 5 hourly + 4 daily returns |
| `compute_liquidity_mask` + `compute_rolling_adv` | 4ms | Point-in-time liquidity arrays |
| `detect_daily_regime` + alignment | 2ms | Regime detection |
| `_load_enriched` | 0ms | Cached after first call (~34ms cold) |

**For fast strategies, `_build_context` dominates.** The JIT simulation is the second cost center at 7-8ms.
CPCV multiplies both: 15 folds × (build + strategy + simulate) per token.

---

## Rule 1: Never Use Python For-Loops Over Bar Arrays

**BAD** (106ms per call on 10K bars):
```python
spread_avg = np.full(n, np.nan)
for i in range(20, n):
    spread_avg[i] = np.nanmean(spread[i-20:i])
```

**GOOD** (0.78ms per call — 137x faster):
```python
from engine import rolling_mean
spread_avg = rolling_mean(spread, 20)
```

### Available Vectorized Helpers (from `engine.py`)

```python
from engine import (
    rolling_mean,      # pd.Series.rolling().mean()
    rolling_std,       # pd.Series.rolling().std()
    rolling_median,    # pd.Series.rolling().median()
    rolling_max,       # pd.Series.rolling().max()
    rolling_min,       # pd.Series.rolling().min()
    rolling_zscore,    # (x - rolling_mean) / rolling_std, clipped to [-3, 3]
    rolling_skew,      # pd.Series.rolling().skew()
    rolling_corr,      # pd.Series.rolling().corr()
)
```

All return numpy arrays. All handle NaN correctly.

---

## Rule 2: Use Pre-Computed Indicators from StrategyContext

The engine already computes 23+ indicators on 1H, 4H, and Daily timeframes.
**Do NOT recompute what already exists.**

Available in `ctx.ind_1h` (and `ctx.ind_4h`, `ctx.ind_d`):
```
close, high, low, volume, open,
ret_1, atr, atr_pct,
sma_20, sma_50, sma_200, ema_20,
rsi, bb_upper, bb_lower, bb_width,
adx, macd_line, macd_signal, macd_hist,
plus_di, minus_di, obv, vwap_dev
```

Available in `ctx.custom` (computed by indicator plugins):
```
obv, obv_slope, vwap_20, vwap_dev,
ret_6h, ret_12h, ret_24h, ret_48h, ret_120h,
ret_5d, ret_10d, ret_20d, ret_60d
```

**BAD** (was 30ms on 54K bars — fixed 2026-03-03):
```python
# Python for-loop OBV — O(n) with Python overhead
obv = np.zeros(n)
for i in range(1, n):
    if close[i] > close[i-1]:
        obv[i] = obv[i-1] + volume[i]
    ...
```

**GOOD** (vectorized — 1.7ms, 18x faster):
```python
# Vectorized OBV via sign + cumsum (now used in engine.py)
sign = np.sign(np.diff(close, prepend=close[0]))
obv = np.cumsum(sign * volume)
```

**BEST** (use pre-computed):
```python
obv = ctx.custom['obv']
obv_slope = ctx.custom['obv_slope']
```

---

## Rule 3: Vectorize Conditional Logic

**BAD:**
```python
signal = np.zeros(n, dtype=bool)
for i in range(200, n):
    if close[i] > sma_200[i] and rsi[i] < 30 and adx[i] > 25:
        signal[i] = True
```

**GOOD:**
```python
signal = (close > sma_200) & (rsi < 30) & (adx > 25)
signal[:200] = False  # warmup guard
```

---

## Rule 4: Context Caching for Multi-Strategy Runs

When testing multiple strategies on the same tokens, use `use_cache=True`:

```python
engine = Engine(data_dir='data')
df = pd.read_parquet('data/spot/1h_cache/BTC_1h.parquet')

# First call computes indicators (~65ms)
ctx = engine._build_context('BTC', df, use_cache=True)

# Second call returns cached context (~0.01ms)
ctx = engine._build_context('BTC', df, use_cache=True)
```

The cache keys on `(ticker, len(df))`, so different data slices get different contexts.

---

## Rule 5: Minimize Allocations in Hot Paths

**BAD:** Creating new arrays inside loops
```python
for i in range(n):
    chunk = data[i-window:i]  # creates a new array each iteration
    result[i] = np.mean(chunk)
```

**GOOD:** Use rolling helpers (single allocation)
```python
result = rolling_mean(data, window)
```

---

## Rule 6: Never Introduce Look-Ahead Bias

Every optimization, refactor, or new feature in the engine/strategy code must preserve causal ordering.
**Speed is worthless if it leaks future data into past decisions.**

### What constitutes look-ahead bias

- Using bar `i+1` (or later) data to compute a value at bar `i`
- Rolling windows that include the current or future bar in their output (off-by-one)
- Daily aggregations applied to the same day's hourly bars instead of the next day's
- Sorting/ranking across the full array then using the rank at bar `i` (survivorship/full-sample bias)
- Filling NaN backward (`bfill`) instead of forward (`ffill`)

### Mandatory bias checks when modifying engine code

1. **Causal trace**: For every array operation, verify `output[i]` depends only on inputs at indices `<= i`
   (or `< i` for lagged quantities like daily ADV applied to next-day bars).
2. **NaN boundary check**: Confirm that the first non-NaN output bar is no earlier than expected.
   Example: a 30-day rolling median should have NaN for the first 30 days.
3. **Before/after diff**: Run validation on at least one strategy before and after the change.
   Same tokens, same results = no behavioral change. Any difference requires investigation.

### Common safe patterns

```python
# SAFE: np.diff looks backward
np.diff(close, prepend=close[0])  # result[i] = close[i] - close[i-1]

# SAFE: cumsum is causal
np.cumsum(arr)  # output[i] = sum(arr[0:i+1])

# SAFE: rolling with min_periods forward-fills NaN at the start
pd.Series(arr).rolling(window, min_periods=3).mean()

# SAFE: np.repeat for expanding daily to hourly (with lag offset)
adv_hourly[bars_per_day:] = np.repeat(daily_adv, bars_per_day)  # 1-day lag
```

### Dangerous patterns (require extra scrutiny)

```python
# DANGEROUS: scipy/sklearn operations that see the whole array
from sklearn.preprocessing import StandardScaler
scaled = scaler.fit_transform(arr)  # fit() sees ALL data including future

# DANGEROUS: pandas operations that can fill backward
df.fillna(method='bfill')  # leaks future into past

# DANGEROUS: sorting + ranking on full series
ranks = arr.argsort().argsort()  # rank uses future values
```

---

## Performance Checklist for New Strategies

Before committing a new strategy, verify:

1. [ ] **No `for i in range(n)` loops** over bar arrays (where n > 100)
2. [ ] **No manual rolling calculations** — use `rolling_*` helpers
3. [ ] **Uses pre-computed indicators** from `ctx.ind_1h` where available
4. [ ] **Vectorized boolean logic** — no per-bar if/else
5. [ ] **Profile it**: should be < 1ms per call on 40K bars
6. [ ] **No look-ahead bias**: every output[i] depends only on inputs at index <= i (see Rule 6)

Quick timing test:
```python
import time
ctx = engine._build_context('BTC', df)
t0 = time.perf_counter()
for _ in range(100):
    result = strategy(ctx)
t1 = time.perf_counter()
print(f"{(t1-t0)/100*1000:.2f}ms per call")
# Target: < 1ms. Acceptable: < 10ms. Red flag: > 100ms.
```

---

## Strategy Speed Tiers

Validation times measured with `--universe filtered` (111 tokens), 4 workers.
Per-token cost = 16 calls × (build_context + strategy + simulate).

| Tier | Strategy time/call | Validation (111 tok, 4w) | Example |
|------|-------------------|--------------------------|---------|
| Fast | < 1ms | ~32s | s11, s09 (vectorized) |
| Medium | 1-10ms | ~3 min | s12, s18 |
| Slow | 10-100ms | ~25 min | s14, s15 (some loops) |
| Broken | > 100ms | > 2 hours | s16 (all loops) |

Note: `_build_context` (~50ms avg) and `_simulate` (~8ms) are fixed costs per call.
A fast strategy adds negligible time; a slow strategy multiplies by 16 calls/token.

---

## Engine Internals: What's Already Optimized

- **JIT simulation**: `_simulate_core_jit` is Numba-compiled, cached, ~7ms on 54K bars (scales linearly with bar count)
- **Exit regime mask**: Vectorized with `np.isin` (~0.3ms)
- **Indicator computation**: Pure numpy, ~21ms for 54K bars (1h), ~5ms for 4h+daily combined
- **OBV plugin**: Vectorized via `np.sign` + `np.cumsum` (~2ms for 54K bars)
- **Context caching**: Available via `use_cache=True` parameter
- **Liquidity arrays**: `compute_rolling_adv` vectorized via `np.repeat` (~1ms)

---

## Anti-Patterns Found in Existing Strategies

### S16 Composite Factor (1,864ms/call → should be <1ms)
- 10+ manual `for i in range(...)` loops for rolling z-scores
- Fix: Replace with `rolling_zscore()` from engine

### S14 Microstructure Edge (600ms/call → should be <1ms)
- Manual rolling mean, rolling median, rolling std loops
- Nested loops for Corwin-Schultz spread estimator
- Fix: Replace with `rolling_mean()`, `rolling_median()`, `rolling_std()`

### S15 Vol Regime Breakout (similar to S14)
- Same manual rolling patterns
- Fix: Same vectorization approach

---

## Testing Performance Patterns

When running the V3 validation sweep:

```bash
# Single strategy, filtered universe (default)
python v3/validation.py --strategy s11_momentum_burst --workers 4
# Expected: ~32s for 111 filtered tokens

# Full universe (all 116 tokens including noise)
python v3/validation.py --strategy s11_momentum_burst --universe all --workers 4

# Multi-strategy sweep (runs sequentially, indicators cached per-worker)
python v3/validation.py --strategy s11 s09 s17 --workers 4

# Profile a specific strategy
python -c "
from engine import Engine
import pandas as pd, time
eng = Engine(data_dir='data')
df = pd.read_parquet('data/spot/1h_cache/BTC_1h.parquet')
ctx = eng._build_context('BTC', df)
from strategies.s11_momentum_burst import strategy
t0 = time.perf_counter()
for _ in range(1000):
    strategy(ctx)
print(f'{(time.perf_counter()-t0)/1000*1000:.3f}ms/call')
"
```

---

## Backtesting Realism Caveats (Added 2026-03-15)

Backtested Calmar ratios are inflated ~100-1000x by several compounding factors:

| Factor | Impact | Status |
|--------|--------|--------|
| Full equity compounding | 10-50x inflation | **Accepted** — intended behavior |
| Daily DD resampling | 3-5x DD underestimate | **Accepted** — measured, modest |
| `cap_multiplier=15` disabling ADV caps | Unbounded position sizes | **Needs fix** — mission active |
| No market impact at scale | Assumes zero slippage on $1M+ positions | **Needs fix** — mission active |
| Survivorship bias | Only current Binance listings | **Accepted** |
| Parameter overfitting | Not quantified | **Needs quantification** — mission active |

**Realistic Calmar:** Likely 1-5 for the best strategies, not 100-1000.

**Key insight:** The strategies have genuine edge (profitable every month across 21 portfolios over 12 months), but the magnitude of backtested returns at large equity levels is unrealistic because positions would exceed market capacity.

### Funding Rate Gotchas

- **PIPPIN example:** 29.6% annualized funding rate (30d). s65 goes long when 72h rolling mean briefly dips negative. Despite paying heavy funding, PIPPIN longs are net profitable because price gains exceed funding costs.
- **Attempted fix (8h funding confirmation) KILLED:** Reduced 12mo return by 60%. Filter blocks profitable entries across all tokens, not just edge cases. Lesson: don't over-filter based on single-token pathology.
- **`funding_exit_threshold`:** Engine supports exiting when cumulative funding / margin exceeds threshold (v4/simulator.py). Currently disabled (0.0) on all strategies. Available as safety valve if needed.
