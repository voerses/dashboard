# V3 Performance Patterns — Strategy & Engine Best Practices

## TL;DR

The V3 validation engine calls each strategy function ~784 times per token
(1 WF + 15 CPCV folds × 49 tokens). A strategy that takes 1ms/call finishes
in 38 seconds. One that takes 100ms/call takes 64 MINUTES. **Vectorize everything.**

---

## The Performance Model

```
Total time ≈ Σ tokens × (1 WF + 15 CPCV) × (T_build_context + T_strategy + T_simulate)
```

Measured per-component timings (BTC, 44,554 bars):

| Component | Time | Notes |
|-----------|------|-------|
| `_build_context` | 26-54ms | Indicators + timeframe aggregation |
| `strategy()` (s11, vectorized) | 0.04ms | Pure numpy, no loops |
| `strategy()` (s16, loops) | 1,864ms | 10+ Python for-loops |
| `_simulate()` (JIT, cached) | 0.3ms | Numba JIT simulation |

**The strategy function dominates.** Everything else is sub-millisecond after warmup.

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

**BAD:**
```python
# Recomputing OBV from scratch — 44K iterations
obv = np.zeros(n)
for i in range(1, n):
    if close[i] > close[i-1]:
        obv[i] = obv[i-1] + volume[i]
    ...
```

**GOOD:**
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
df = pd.read_parquet('data/1h_cache/BTC_1h.parquet')

# First call computes indicators (~54ms)
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

## Performance Checklist for New Strategies

Before committing a new strategy, verify:

1. [ ] **No `for i in range(n)` loops** over bar arrays (where n > 100)
2. [ ] **No manual rolling calculations** — use `rolling_*` helpers
3. [ ] **Uses pre-computed indicators** from `ctx.ind_1h` where available
4. [ ] **Vectorized boolean logic** — no per-bar if/else
5. [ ] **Profile it**: should be < 1ms per call on 40K bars

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

| Tier | Time/call | 49-token validation | Example |
|------|-----------|---------------------|---------|
| Fast | < 1ms | ~18s | s11, s09 (vectorized) |
| Medium | 1-10ms | ~2 min | s12, s18 |
| Slow | 10-100ms | ~20 min | s14, s15 (some loops) |
| Broken | > 100ms | > 1 hour | s16 (all loops) |

---

## Engine Internals: What's Already Optimized

- **JIT simulation**: `_simulate_core_jit` is Numba-compiled, cached, ~0.1ms
- **Exit regime mask**: Vectorized with `np.isin` (~0.1ms vs 7.5ms for Python loop)
- **Indicator computation**: Pure numpy, ~9ms for 44K bars
- **Context caching**: Available via `use_cache=True` parameter

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
# Single strategy, fast
python v3/validation.py --strategy s11 --workers 4 --data-dir data
# Expected: ~17s for 49 tokens

# Multi-strategy sweep (runs sequentially, indicators cached per-worker)
python v3/validation.py --strategy s11 s09 s17 --workers 4 --data-dir data

# Profile a specific strategy
python -c "
from engine import Engine
import pandas as pd, time
eng = Engine(data_dir='data')
df = pd.read_parquet('data/1h_cache/BTC_1h.parquet')
ctx = eng._build_context('BTC', df)
from strategies.s11_momentum_burst import strategy
t0 = time.perf_counter()
for _ in range(1000):
    strategy(ctx)
print(f'{(time.perf_counter()-t0)/1000*1000:.3f}ms/call')
"
```
