# Performant Backtesting Infrastructure — Comprehensive Research

> Research compiled March 2026. Covers architecture, optimization, data engineering,
> and tooling for building high-performance crypto/quant backtesting systems in Python.

---

## Table of Contents

1. [Vectorized Backtesting Patterns](#1-vectorized-backtesting-patterns)
2. [Event-Driven vs Vectorized — Tradeoffs](#2-event-driven-vs-vectorized--tradeoffs)
3. [Numba JIT Compilation](#3-numba-jit-compilation)
4. [Parallel Backtesting](#4-parallel-backtesting)
5. [Data Pipeline Optimization](#5-data-pipeline-optimization)
6. [Feature Store Design](#6-feature-store-design)
7. [GPU-Accelerated Backtesting](#7-gpu-accelerated-backtesting)
8. [Backtesting Framework Comparison](#8-backtesting-framework-comparison)
9. [Database Design for Tick/OHLCV Data](#9-database-design-for-tickohlcv-data)
10. [Real-Time Data Ingestion](#10-real-time-data-ingestion)
11. [Optimization Techniques](#11-optimization-techniques)
12. [Cloud vs Local Infrastructure](#12-cloud-vs-local-infrastructure)
13. [Data Quality](#13-data-quality)
14. [Reproducibility](#14-reproducibility)
15. [Python Libraries for Quant Finance](#15-python-libraries-for-quant-finance)
16. [Codebase Structure for Maintainability and Speed](#16-codebase-structure-for-maintainability-and-speed)
17. [Zipline Pipeline Architecture](#17-zipline-pipeline-architecture)
18. [VectorBT Architecture Internals](#18-vectorbt-architecture-internals)
19. [Modern Crypto Backtesting Alternatives (2024-2025)](#19-modern-crypto-backtesting-alternatives-2024-2025)

---

## 1. Vectorized Backtesting Patterns

### Core Concept

Vectorization performs operations on entire arrays at once rather than iterating through individual data points. Thanks to the time-series nature of trading data, most backtesting computations translate naturally into vector operations. NumPy's SIMD/BLAS optimizations make these operations orders of magnitude faster than Python loops.

### Key Patterns

**1. Element-wise DataFrame Operations**
```python
# Instead of looping:
# for i in range(len(df)):
#     df['signal'][i] = df['sma_fast'][i] > df['sma_slow'][i]

# Vectorized:
df['signal'] = (df['sma_fast'] > df['sma_slow']).astype(int)
```

**2. Boolean Comparisons for Signals**
Precompute moving averages, then generate crossover signals via boolean comparison — no loops required. Compute account value, PnL, and the full backtest result all vectorized.

**3. Multi-dimensional Array Packing (VectorBT approach)**
Each strategy instance is represented in vectorized form. Multiple instances are packed into a single multi-dimensional array, processed simultaneously, and compared easily. This enables testing thousands of parameter combinations in seconds.

**4. Rolling Window Operations**
```python
df['sma_50'] = df['close'].rolling(50).mean()
df['sma_200'] = df['close'].rolling(200).mean()
df['rsi'] = ta.RSI(df['close'].values, timeperiod=14)
```

**5. Shift-based P&L Computation**
```python
df['returns'] = df['close'].pct_change()
df['strategy_returns'] = df['signal'].shift(1) * df['returns']
df['equity'] = (1 + df['strategy_returns']).cumprod()
```

### Performance Benchmarks

- A multi-asset, daily-rebalancing vectorized backtest (500 tickers, 10 years daily data) completes in **under 1 second**
- Equivalent event-based simulation of minute-level data: **15-30 minutes**
- Tick-level event simulations: **hours** or distributed computing required

### Limitations

- **Look-ahead bias risk**: Off-by-one indexing errors (e.g., `df.shift(-1)`) can silently inflate results
- **No execution realism**: Cannot model slippage, partial fills, bid-ask spreads
- **Path dependency**: Strategies with dynamic position sizing based on past P&L are difficult to vectorize
- **Complex conditionals**: Logic with early termination or branching is better handled iteratively

### When NOT to Vectorize

- Complex conditionals or early termination logic
- Operations that depend heavily on previous results (path-dependent)
- Very small datasets (vectorization overhead exceeds benefit)
- When you need realistic execution modeling (slippage, partial fills)

---

## 2. Event-Driven vs Vectorized — Tradeoffs

### Side-by-Side Comparison

| Dimension | Vectorized | Event-Driven |
|-----------|-----------|--------------|
| **Speed** | Ultra-fast (SIMD/BLAS parallel ops) | Slow (per-bar Python execution) |
| **Prototyping** | Minutes to implement | Hours to implement |
| **Look-ahead bias** | High risk (easy to introduce) | Minimal (data drip-fed as events) |
| **Execution realism** | Low (assumes fill at bar close/open) | High (slippage, partial fills, spreads) |
| **Code reuse (backtest to live)** | None — fundamentally different architecture | Direct — same code for both |
| **Path-dependent logic** | Difficult, cumbersome | Natural, first-class |
| **Dynamic risk mgmt** | Cannot enforce intraday | Intraday margin/VaR limits possible |
| **Best frequency** | Daily/weekly | Intraday/HFT |
| **Language fit** | Python/NumPy/pandas (built for vectors) | C++/Java/C#/Rust (compiled, loop-friendly) |

### The Hybrid Approach (Industry Standard)

Most quant teams adopt a two-stage pipeline:

1. **Stage 1 — Vectorized screening**: Use vectorized backtests to rapidly screen thousands of strategy candidates. Filter the universe down to promising designs.
2. **Stage 2 — Event-driven validation**: Migrate top candidates into an event-driven framework for realistic execution modeling before live deployment.

This captures the speed of vectorized testing during research while ensuring production-grade realism before going live.

### Decision Framework

- **Use vectorized** for: factor research, cross-sectional studies, daily/weekly strategies, parameter sweeps, initial screening
- **Use event-driven** for: final validation, intraday/HFT, strategies needing realistic execution, live trading code reuse
- **Use hybrid** for: any production workflow

---

## 3. Numba JIT Compilation

### What Numba Does

Numba translates Python functions to optimized machine code at runtime using the LLVM compiler. Decorated functions compile on first call and run at near-C/Fortran speed on subsequent calls.

### Typical Speedups

- **50x or more** for loop-heavy, numerically-intensive backtesting logic
- VectorBT uses Numba throughout its core — this is how it achieves orders-of-magnitude speedup over traditional Python backtesting libraries

### Compilation Modes

| Mode | Decorator | Description |
|------|-----------|-------------|
| **nopython** (recommended) | `@njit` or `@jit(nopython=True)` | Full machine code, no Python interpreter. Maximum performance. |
| **object** | `@jit` | Falls back to Python runtime. Lower performance but more flexible. |
| **parallel** | `@njit(parallel=True)` | Automatic parallelization across CPU cores. ~5-6x over standard `@njit`. |

### Code Structure for Maximum Speedup

```python
import numpy as np
from numba import njit

@njit
def simulate_strategy(prices, sma_fast, sma_slow, initial_capital):
    """Numba-compiled simulation loop for path-dependent logic."""
    n = len(prices)
    positions = np.zeros(n)
    cash = initial_capital
    equity = np.zeros(n)

    for i in range(1, n):
        # Signal logic
        if sma_fast[i] > sma_slow[i] and sma_fast[i-1] <= sma_slow[i-1]:
            positions[i] = cash / prices[i]  # Buy
            cash = 0.0
        elif sma_fast[i] < sma_slow[i] and positions[i-1] > 0:
            cash = positions[i-1] * prices[i]  # Sell
            positions[i] = 0.0
        else:
            positions[i] = positions[i-1]

        equity[i] = cash + positions[i] * prices[i]

    return equity, positions
```

### Critical Best Practices

1. **Use `@njit` not `@jit`** — forces nopython mode, raises error if compilation fails instead of silently falling back
2. **Use NumPy arrays, not pandas** — Numba cannot compile pandas objects
3. **Extract data to arrays before calling Numba functions**:
   ```python
   prices = df['close'].values  # numpy array
   result = simulate_strategy(prices, sma_fast, sma_slow, capital)
   ```
4. **Accept first-call overhead** — first invocation compiles (slow), subsequent calls are fast. Warm up with a small dataset.
5. **Avoid Python objects inside Numba functions** — no dicts, lists of mixed types, or class instances
6. **Use `@njit(cache=True)`** — caches compiled code to disk, avoiding recompilation across sessions
7. **Use `@njit(parallel=True)` with `prange`** — for embarrassingly parallel inner loops

### Parallelization with Numba

```python
from numba import njit, prange

@njit(parallel=True)
def run_parameter_sweep(prices, fast_windows, slow_windows, capital):
    n_combos = len(fast_windows)
    results = np.zeros(n_combos)
    for i in prange(n_combos):
        equity = simulate_single(prices, fast_windows[i], slow_windows[i], capital)
        results[i] = equity[-1]
    return results
```

---

## 4. Parallel Backtesting

### Fundamental Principle

Backtesting is path-dependent — it is hard to parallelize within a single backtest. Parallelism is best applied **across** independent runs:
- Different parameter combinations
- Different instruments/symbols
- Different time periods
- Different strategy variants

### Approach Comparison

| Approach | Best For | Key Advantage | Key Limitation |
|----------|----------|---------------|----------------|
| **Ray** | Distributed backtesting, shared memory | Pandas-compatible, zero-copy NumPy sharing | Requires Ray cluster setup |
| **Dask** | Large-scale distributed data | Scales to clusters, lazy evaluation | Not 100% Pandas-compatible |
| **`multiprocessing`** | Simple local parallelism | No setup, stdlib | Pickling/serialization overhead |
| **Numba `parallel=True`** | Inner-loop parallelism | Minimal overhead, auto-parallelization | Limited to Numba-compatible code |
| **Backtrader built-in** | Quick optimization | Integrated with framework | Memory issues on large runs |

### Ray (Recommended for Production)

```python
import ray
import pandas as pd

ray.init()

# Store shared data in object store (zero-copy for NumPy)
price_data_ref = ray.put(price_df.values)  # numpy array in shared memory

@ray.remote
def run_backtest(data_ref, fast_period, slow_period):
    prices = ray.get(data_ref)  # zero-copy access
    return simulate_strategy(prices, fast_period, slow_period)

# Launch all backtests in parallel
futures = [
    run_backtest.remote(price_data_ref, fast, slow)
    for fast in range(5, 50)
    for slow in range(20, 200)
]

# Collect results (ONE call to ray.get — never inside a loop)
results = ray.get(futures)
```

**Key Ray Tips:**
- NEVER call `ray.get()` inside a loop — collect all futures first, then `ray.get()` once
- Use `ray.put()` for shared data (OHLCV DataFrames, indicator arrays) — enables zero-copy access
- Store daily OHLCV in object store during prep; load tick data on-demand per worker
- Ray's Plasma object store avoids data duplication across workers

### Multiprocessing (Simple Local)

```python
from multiprocessing import Pool
import functools

def run_single_backtest(params, prices):
    fast, slow = params
    return simulate_strategy(prices, fast, slow)

params_list = [(f, s) for f in range(5, 50) for s in range(20, 200)]
with Pool(processes=os.cpu_count()) as pool:
    results = pool.map(functools.partial(run_single_backtest, prices=prices), params_list)
```

### Architecture Pattern: I/O vs CPU Separation

- **Threading** for I/O-bound work: data loading, API calls, writing results
- **Multiprocessing** for CPU-bound work: strategy simulation, indicator computation, optimization
- Python's GIL blocks true parallel threading for CPU work — multiprocessing (separate processes) is required

### Shared Memory for Workers

```python
from multiprocessing import shared_memory
import numpy as np

# Create shared memory block
shm = shared_memory.SharedMemory(create=True, size=prices.nbytes)
shared_prices = np.ndarray(prices.shape, dtype=prices.dtype, buffer=shm.buf)
shared_prices[:] = prices[:]  # Copy data once

# Workers access via shared memory name
# shm_name = shm.name  # pass to workers
# In worker: existing_shm = shared_memory.SharedMemory(name=shm_name)
```

---

## 5. Data Pipeline Optimization

### Format Comparison

| Factor | CSV | HDF5 | Parquet | Feather/Arrow |
|--------|-----|------|---------|---------------|
| **Read speed** | Slowest (~100x slower) | Fastest (single-thread) | Fast (with parallelism) | Very fast |
| **File size** | Largest | Medium | Smallest (5x compression) | Medium |
| **Memory efficiency** | Worst | Good | Best | Good |
| **Memory-mappable** | No | Yes (native) | No (requires decompression) | Yes |
| **Distributed processing** | No | No (not splittable) | Yes (Spark/Dask) | Limited |
| **Schema support** | No | Yes | Yes (rich) | Yes |
| **Compression** | None | Supports gzip | Multiple codecs built-in | LZ4 |
| **Write speed** | Slow | Fast | Moderate (encoding overhead) | Fastest |

### Recommendations by Use Case

**For real-time tick data processing:**
- **HDF5** — Ultra-fast single-thread reads, native memory mapping, same structure on disk and in memory

**For historical analytics and storage:**
- **Parquet** — Best compression (5x smaller than CSV), columnar layout, parallel reads, Spark/Dask compatible

**For inter-process data exchange:**
- **Arrow/Feather** — Fastest read/write, zero-copy memory mapping, ideal for short-lived intermediate data

**For data ingestion:**
- **CSV** — Accept as interchange format only. Convert to binary format immediately after ingestion.

### Partitioning Strategy

```
data/
  parquet/
    symbol=BTCUSDT/
      year=2024/
        month=01/
          data.parquet
        month=02/
          data.parquet
    symbol=ETHUSDT/
      ...
```

- Partition by symbol and date for efficient predicate pushdown
- Enables reading only the data you need without scanning entire dataset
- Dask/Spark can parallelize reads across partitions automatically

### Memory-Mapped Files

```python
import numpy as np

# Create memory-mapped file for tick data
fp = np.memmap('tick_data.dat', dtype='float64', mode='w+', shape=(1_000_000, 6))
# Columns: timestamp, open, high, low, close, volume

# Read only what you need — OS handles paging
data = np.memmap('tick_data.dat', dtype='float64', mode='r', shape=(1_000_000, 6))
chunk = data[500_000:600_000]  # Only this slice is loaded into RAM
```

- HDF5 and Arrow are natively memory-mappable
- Parquet requires decompression — lazy reading possible but with a performance penalty
- For files larger than RAM, HDF5 gives faster performance than Arrow for memory-mapped access

### Chunked Processing

```python
import pandas as pd

# Process large Parquet files in chunks
for chunk in pd.read_parquet('large_file.parquet', columns=['close', 'volume'],
                              filters=[('symbol', '==', 'BTCUSDT')]):
    process_chunk(chunk)
```

### Polars as a Modern Alternative

Polars (Rust-based) is 10-50x faster than pandas on large datasets:
```python
import polars as pl

df = pl.scan_parquet('data/*.parquet')  # Lazy evaluation
result = (
    df.filter(pl.col('symbol') == 'BTCUSDT')
    .with_columns(pl.col('close').rolling_mean(20).alias('sma_20'))
    .collect()  # Execute only when needed
)
```

---

## 6. Feature Store Design

### Architecture Overview

A feature store is a dedicated repository for precomputed features, serving two modes:

1. **Offline store**: Columnar storage (Delta Lake, Parquet) for historical data — supports backtesting and training with point-in-time correct values
2. **Online store**: Low-latency key-value store for real-time inference — sub-second retrieval of latest feature values

### Core Design Principles

**1. Point-in-Time Correctness (Critical)**
```
Features are time-series data. When joining features to training examples,
you MUST use "as-of" joins — retrieve the latest feature values that existed
AT OR BEFORE the example's timestamp. Using current values for historical
examples introduces look-ahead bias.
```

**2. Precomputed vs Runtime**
- **Precomputed (feature store)**: Indicators that can be computed ahead of time — SMA, RSI, VWAP, volume profiles, on-chain metrics
- **Runtime (model input)**: Information only known at prediction time — current price, order book state, live spread

**3. Feature Pipeline Architecture**
```
Raw Data (OHLCV, tick, on-chain)
    |
    v
Feature Pipelines (batch/streaming)
    |
    v
Feature Store (offline + online)
    |
    +--> Backtesting Engine (offline reads with as-of joins)
    +--> Live Trading (online reads, sub-ms latency)
```

### Implementation for Backtesting

```python
class FeatureStore:
    """Simple feature store for backtesting with precomputed indicators."""

    def __init__(self, base_path: str):
        self.base_path = base_path

    def compute_and_store(self, symbol: str, ohlcv: pd.DataFrame):
        """Compute all indicators once, store as Parquet."""
        features = pd.DataFrame(index=ohlcv.index)
        features['sma_20'] = ohlcv['close'].rolling(20).mean()
        features['sma_50'] = ohlcv['close'].rolling(50).mean()
        features['rsi_14'] = ta.RSI(ohlcv['close'].values, 14)
        features['atr_14'] = ta.ATR(ohlcv['high'].values,
                                     ohlcv['low'].values,
                                     ohlcv['close'].values, 14)
        features['volume_sma_20'] = ohlcv['volume'].rolling(20).mean()

        path = f"{self.base_path}/{symbol}/features.parquet"
        features.to_parquet(path)

    def load(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        """Load precomputed features for a date range."""
        path = f"{self.base_path}/{symbol}/features.parquet"
        df = pd.read_parquet(path)
        return df.loc[start:end]
```

### Caching Layers

- **L1 — In-memory**: LRU cache for frequently accessed features (current strategy's indicators)
- **L2 — Local disk**: Parquet files for computed features per symbol
- **L3 — Remote storage**: S3/GCS for full historical feature sets

### Preventing Look-Ahead Bias

Use a generator pattern to enforce temporal discipline:
```python
def yield_features(feature_store, symbol, dates):
    """Yield one timestamp at a time — physically prevents look-ahead bias."""
    for date in dates:
        features_up_to_date = feature_store.load(symbol, end=date)
        yield date, features_up_to_date.iloc[-1]  # Only latest available
```

---

## 7. GPU-Accelerated Backtesting

### RAPIDS Ecosystem (NVIDIA)

| Library | Purpose | CPU Equivalent |
|---------|---------|----------------|
| **cuDF** | GPU-accelerated DataFrames | pandas |
| **cuML** | GPU-accelerated ML | scikit-learn |
| **cuPy** | GPU-accelerated arrays | NumPy |
| **Dask-cuDF** | Multi-GPU distributed DataFrames | Dask |
| **Numba CUDA** | Custom GPU kernels | N/A |

### Zero-Code-Change Acceleration (2024-2025)

As of RAPIDS v24.02+ and v25.02+, cuDF can accelerate existing pandas code with **no code changes**:
```python
# Just add this import — existing pandas code runs on GPU
%load_ext cudf.pandas
import pandas as pd  # Now GPU-accelerated transparently

df = pd.read_parquet('ohlcv.parquet')  # GPU-accelerated read
df['sma_50'] = df['close'].rolling(50).mean()  # GPU-accelerated rolling
```

Performance: **up to 150x faster** for a 5GB dataset with zero code changes.

### When GPU Makes Sense

**Good GPU candidates:**
- Large-scale indicator computation across many symbols simultaneously
- Portfolio optimization (matrix operations)
- ML model training/inference for signal generation
- Monte Carlo simulations
- Processing 2-10GB+ datasets

**Poor GPU candidates:**
- Small datasets (GPU transfer overhead exceeds compute benefit)
- Sequential, path-dependent logic (single-thread bottleneck)
- I/O-bound workflows

### gQuant Workflow Architecture

NVIDIA's gQuant project organizes quant workflows as directed acyclic graphs (DAGs):
```
DataLoader → Transformation → Strategy → Backtest → Analysis
```
Each node receives DataFrames as inputs, validates them, computes on GPU, and passes results to child nodes.

### Custom CUDA Kernels with Numba

For maximum GPU control, write custom CUDA kernels:
```python
from numba import cuda
import numpy as np

@cuda.jit
def compute_returns_gpu(prices, returns, n):
    i = cuda.grid(1)
    if i > 0 and i < n:
        returns[i] = (prices[i] - prices[i-1]) / prices[i-1]
```

NVIDIA benchmarks show **100x+ speedup** for algorithmic trading simulations using Numba CUDA kernels.

### 2025 Updates

- RAPIDS 25.02/25.04: cuML accelerates scikit-learn, Polars GPU Engine, cuDF pre-installed in Google Colab
- NVIDIA Blackwell architecture: Hardware decompression engine for IO-heavy workloads
- Dask-cuDF: Distributes work across multiple GPUs, handles lazy execution with dependency graph pruning

---

## 8. Backtesting Framework Comparison

### Summary Matrix (2024-2025)

| Framework | Speed | Ease of Use | Live Trading | Active Dev | Best For |
|-----------|-------|-------------|-------------|-----------|----------|
| **VectorBT** | Fastest (Numba + vectorized) | Steep curve | Via StrateQueue/PRO | PRO only | Quant research, optimization |
| **Backtrader** | Moderate | Beginner-friendly | IB, OANDA, Alpaca | Stalled (~2018) | Retail algo trading |
| **Zipline-Reloaded** | Slow (event-driven) | Clean API, hard install | Limited | Forks only | Academic, factor research |
| **NautilusTrader** | Very fast (Rust core) | Steep curve | Multi-exchange | Active | Institutional production |
| **Freqtrade** | Good | Moderate | Built-in (CCXT) | Active | Crypto-specific, ML (FreqAI) |
| **Jesse** | Good | Intuitive API | Built-in | Active | Crypto-focused |
| **bt** | Good | Composable algos | No | Moderate | Portfolio-level strategies |
| **QSTrader** | Moderate | Modular OOP | Extensible | Active | Learning event-driven design |
| **Custom engine** | Varies | Full control | Full control | You maintain | Institutional/proprietary |

### VectorBT (Speed Champion)

- Tests 1,000+ parameter combinations in the time other libraries process a single backtest
- VectorBT 1.2.0 (Oct 2025): Native tick-level resolution, slippage models matching Binance fills within 0.3%
- Free version maintained only; PRO for new features
- Core: NumPy + pandas + Numba compilation

### NautilusTrader (Production Champion)

- Python API + Rust performance core
- Streams 5M+ rows/sec, handles more data than available RAM
- Same code for backtest and live — no code changes needed
- Multi-asset: spot, futures, derivatives, options
- AI-ready: designed for ML model integration

### Freqtrade (Crypto Champion)

- FreqAI module: adaptive ML prediction, continuous retraining
- CCXT integration: all major crypto exchanges
- Hyperopt module for systematic parameter optimization
- Crypto-only — not for stocks/forex

### Decision Framework

```
Need raw speed + research?              → VectorBT
Need backtest-to-live parity?           → NautilusTrader
Need crypto-specific + ML integration?  → Freqtrade
Need quick prototyping?                 → Backtrader or Backtesting.py
Need portfolio-level strategies?        → bt
Building institutional infra?           → NautilusTrader or custom
Learning event-driven design?           → QSTrader or Zipline
```

---

## 9. Database Design for Tick/OHLCV Data

### Database Comparison

| Database | Architecture | Ingestion (rows/sec) | Query Speed | SQL | Best For |
|----------|-------------|---------------------|-------------|-----|----------|
| **QuestDB** | Columnar, SIMD, zero-GC Java/C++/Rust | 2.94M | Fastest (25ms OHLCV) | Yes | Raw tick analytics |
| **TimescaleDB** | PostgreSQL extension, hybrid row-columnar | ~250K | Good (1,021ms OHLCV) | Full PostgreSQL | Mixed workloads, existing PG ecosystem |
| **InfluxDB 3.0** | Rust engine, columnar | ~581K | Moderate | Yes (new in v3) | IoT/monitoring crossover |
| **ClickHouse** | Columnar, distributed | Very high | Very fast (547ms OHLCV) | Yes | General-purpose analytics |
| **KDB+/KDB-X** | Vector/columnar, proprietary | Ultra-high | Ultra-fast (109ms OHLCV) | q language | HFT, institutional |
| **Arctic** | MongoDB-backed, Python-native | Moderate | Good | No (Python API) | pandas DataFrame storage |

### OHLCV Bar Computation Benchmark (5-min bars from tick data)

| Database | Average Query Time |
|----------|-------------------|
| PostgreSQL | 3,493 ms |
| TimescaleDB | 1,021 ms |
| ClickHouse | 547 ms |
| KDB+ | 109 ms |
| **QuestDB** | **25 ms** |

QuestDB is 4.4x faster than KDB+ on this specific OHLCV computation workload.

### Ingestion Benchmarks

- QuestDB: **2.94M rows/sec** vs InfluxDB: 581K rows/sec (5x faster)
- For 10M unique series: QuestDB 2.3M rows/sec vs InfluxDB 55K rows/sec (42x faster — InfluxDB degrades with high cardinality)
- QuestDB vs TimescaleDB: 6-13x faster ingestion, 16-20x faster complex queries

### Recommended Architecture

```
Tick Data (WebSocket) → QuestDB (real-time ingest + queries)
                      → Parquet (long-term cold storage, analytics)

OHLCV Bars           → TimescaleDB (if in PostgreSQL ecosystem)
                      → QuestDB (if prioritizing query speed)
                      → ClickHouse (if general analytics workloads)

Feature Store         → Parquet + metadata in SQLite/PostgreSQL
Research Data         → Arctic (pandas-native, simple)
```

### Schema Design for Tick Data

```sql
-- QuestDB example
CREATE TABLE ticks (
    symbol SYMBOL,
    timestamp TIMESTAMP,
    price DOUBLE,
    quantity DOUBLE,
    side SYMBOL,      -- 'buy' or 'sell'
    trade_id LONG
) TIMESTAMP(timestamp) PARTITION BY DAY
WAL
DEDUP UPSERT KEYS(symbol, trade_id);
```

### Schema Design for OHLCV

```sql
CREATE TABLE ohlcv (
    symbol SYMBOL,
    timestamp TIMESTAMP,
    timeframe SYMBOL,  -- '1m', '5m', '1h', '1d'
    open DOUBLE,
    high DOUBLE,
    low DOUBLE,
    close DOUBLE,
    volume DOUBLE,
    trades INT
) TIMESTAMP(timestamp) PARTITION BY MONTH;
```

### Arctic (Man AHL)

- Python library built on MongoDB for storing pandas DataFrames and NumPy arrays
- Designed specifically for quant finance tick and OHLCV data
- Simple Python API — no SQL
- Good for research environments, less suited for production real-time workloads

---

## 10. Real-Time Data Ingestion

### The Normalization Challenge

Exchanges follow no unified standard:
- Different field names and array structures
- Different symbol formats: `BTCUSDT` vs `BTC-USD` vs `BTC/USDT`
- Timestamp drift across venues
- Order book deltas are event-driven, not time-based
- REST vs WebSocket behavior varies across Binance, Coinbase, OKX, Bybit

### Cryptofeed (Primary Open-Source Solution)

```python
from cryptofeed import FeedHandler
from cryptofeed.defines import TRADES, L2_BOOK
from cryptofeed.exchanges import Binance, Coinbase

async def trade_handler(trade, receipt_timestamp):
    # Normalized trade data regardless of exchange
    print(f"{trade.exchange} {trade.symbol} {trade.side} "
          f"{trade.amount}@{trade.price} {trade.timestamp}")

fh = FeedHandler()
fh.add_feed(Binance(symbols=['BTC-USDT'], channels=[TRADES, L2_BOOK],
                     callbacks={TRADES: trade_handler}))
fh.add_feed(Coinbase(symbols=['BTC-USD'], channels=[TRADES],
                      callbacks={TRADES: trade_handler}))
fh.run()
```

**Key features:**
- Handles multiple exchange WebSocket feeds
- Returns normalized, standardized results
- Backend callbacks for direct-to-storage writing
- Synthetic NBBO (National Best Bid/Offer) aggregation
- REST interface fallback when WebSocket unavailable

### Production Pipeline Architecture

```
Exchanges (WebSocket)
    |
    v
Python Normalizer (Cryptofeed / custom)
    |  - Symbol resolution
    |  - Timestamp normalization
    |  - Schema standardization
    v
Message Queue (Kafka / Redis Streams)
    |
    v
Storage (QuestDB / ClickHouse)
    |
    v
Analytics / Backtesting (Grafana / Custom)
```

### Key Challenges and Solutions

| Challenge | Solution |
|-----------|----------|
| Timestamp drift across venues | NTP sync + exchange-provided timestamps + local receipt time |
| Symbol mismatches | Unified symbol mapping table (maintained per exchange) |
| Silent data gaps | Heartbeat monitoring, gap detection on expected intervals |
| Out-of-order updates | Write-ahead log with reordering (QuestDB WAL) |
| Reconnection handling | Exponential backoff, automatic resubscription |
| Rate limiting | Connection pooling, request throttling per exchange |

### Data Normalization Schema

```python
@dataclass
class NormalizedTrade:
    exchange: str           # 'binance', 'coinbase', 'okx'
    symbol: str             # Unified: 'BTC/USDT'
    timestamp: float        # Unix epoch (exchange time)
    receipt_timestamp: float # Unix epoch (local receipt)
    price: float
    quantity: float
    side: str               # 'buy' or 'sell'
    trade_id: str           # Exchange-specific ID
```

---

## 11. Optimization Techniques

### 11.1 Avoiding Indicator Recomputation Across Parameter Sweeps

**Problem**: When sweeping over strategy parameters (e.g., SMA periods 10-200), naive implementations recompute all indicators for every parameter combination.

**Solution 1 — Precompute all indicator variants once:**
```python
# Compute ALL SMA variants in one pass
sma_variants = {}
for period in range(5, 201):
    sma_variants[period] = df['close'].rolling(period).mean()

# Strategy sweep only references precomputed values
for fast in range(5, 50):
    for slow in range(50, 201):
        signals = (sma_variants[fast] > sma_variants[slow]).astype(int)
        # ... simulate
```

**Solution 2 — Separate indicator parameters from strategy parameters:**
```
Indicator Layer:  Compute RSI(14), SMA(20), SMA(50), SMA(200) ONCE
Strategy Layer:   Sweep over entry/exit thresholds, position sizing rules
```
If only strategy parameters change, indicator computation is reused automatically.

**Solution 3 — Dynamic programming for overlapping computations:**
Rolling computations share data — SMA(50) and SMA(100) both need the same price data. Compute the rolling sum incrementally:
```python
@njit
def incremental_sma(prices, period):
    n = len(prices)
    sma = np.empty(n)
    sma[:period-1] = np.nan
    window_sum = np.sum(prices[:period])
    sma[period-1] = window_sum / period
    for i in range(period, n):
        window_sum += prices[i] - prices[i - period]
        sma[i] = window_sum / period
    return sma
```

### 11.2 Incremental Indicator Computation

For streaming/live scenarios where new data arrives one bar at a time:

```python
class IncrementalSMA:
    def __init__(self, period):
        self.period = period
        self.buffer = deque(maxlen=period)
        self.sum = 0.0

    def update(self, value):
        if len(self.buffer) == self.period:
            self.sum -= self.buffer[0]
        self.buffer.append(value)
        self.sum += value
        return self.sum / len(self.buffer) if len(self.buffer) == self.period else None
```

**Key incremental indicators:**
- SMA: Maintain running sum, subtract oldest, add newest — O(1) per update
- EMA: `ema = alpha * price + (1 - alpha) * prev_ema` — O(1) per update
- RSI: Track running average gain/loss — O(1) per update
- Bollinger Bands: Maintain running mean and variance — O(1) per update
- VWAP: Running cumulative (price * volume) / cumulative volume — O(1) per update

### 11.3 Shared Memory for Parallel Workers

```python
import multiprocessing as mp
from multiprocessing import shared_memory
import numpy as np

def create_shared_data(prices_array):
    """Create shared memory block accessible by all workers."""
    shm = shared_memory.SharedMemory(create=True, size=prices_array.nbytes)
    shared_arr = np.ndarray(prices_array.shape, dtype=prices_array.dtype, buffer=shm.buf)
    shared_arr[:] = prices_array[:]
    return shm

def worker(shm_name, shape, dtype, params):
    """Worker reads from shared memory — no data copying."""
    existing_shm = shared_memory.SharedMemory(name=shm_name)
    prices = np.ndarray(shape, dtype=dtype, buffer=existing_shm.buf)
    result = run_strategy(prices, params)
    existing_shm.close()
    return result
```

**With Ray (simpler):**
```python
# Ray handles shared memory automatically
data_ref = ray.put(prices_array)  # Stored once in shared Plasma store

@ray.remote
def worker(data_ref, params):
    prices = ray.get(data_ref)  # Zero-copy access to shared data
    return run_strategy(prices, params)
```

### 11.4 General Optimization Checklist

1. **Profile first** — use `cProfile`, `line_profiler`, or `py-spy` to find actual bottlenecks
2. **Vectorize pandas operations** — replace `.apply()` and `.iterrows()` with vectorized operations
3. **Use appropriate dtypes** — `float32` instead of `float64` halves memory, speeds cache access
4. **Precompute indicators** — compute once, reuse across parameter sweeps
5. **Use Numba for loops** — when vectorization is impossible (path-dependent logic)
6. **Parallelize across runs** — multiprocessing/Ray for independent backtests
7. **Optimize I/O** — Parquet with predicate pushdown, read only needed columns
8. **Reduce memory allocations** — reuse arrays, avoid creating intermediate DataFrames
9. **Use Polars over pandas** — 10-50x faster for large datasets
10. **Cache results** — memoize expensive computations, store intermediate results

---

## 12. Cloud vs Local Infrastructure

### Decision Framework

| Factor | Cloud (AWS/GCP) | Local/On-Premise |
|--------|----------------|------------------|
| **Workload type** | Bursty, variable (parameter sweeps, research) | Steady, predictable (daily production) |
| **Upfront cost** | Low (OpEx, pay-as-you-go) | High (CapEx, hardware) |
| **Long-term cost** | Can spiral without governance | Lower after break-even (~15 months) |
| **Scalability** | Instant, elastic | Requires hardware procurement |
| **Data egress** | Expensive ($0.09/GB on AWS) | Free internal transfers |
| **Best for** | Parallel backtests, research sprints | Continuous production workloads |
| **GPU access** | On-demand (P4d, A100, H100) | Large upfront investment |

### When to Scale to Cloud

- **Go cloud** when: Running large parameter sweeps (1000+ combinations), testing across many instruments simultaneously, GPU-heavy ML training, bursty research sprints, or team needs access from multiple locations
- **Stay local** when: Running steady daily backtests, data egress costs would be high, workload is predictable, or you've reached the 15-month break-even point

### Cost Optimization Strategies

**Spot/Preemptible Instances (up to 90% discount):**
- Backtesting is fault-tolerant — checkpoint progress and resume on new spot instance
- Spread across 6-8 instance families to reduce simultaneous reclaims

**Architectural Principles:**
- Separate research (bursty, CPU/GPU heavy) from live trading (latency-sensitive, steady)
- Put data next to compute: S3/GCS with columnar Parquet, partitioned by symbol/date
- Docker-package strategy code for reproducible backtests

**AWS-Specific:**
- Graviton (ARM) instances: 20-40% cost savings for compatible workloads
- Savings Plans / Reserved Instances: 30-75% discount for predictable usage
- S3 Intelligent-Tiering for historical data

**GCP-Specific:**
- Sustained Use Discounts: Automatic discounts for consistent usage (no commitment needed)
- Preemptible VMs: Similar to spot, 80% discount
- Per-second billing: Fine-grained cost control

### Hybrid Approach (Recommended)

```
Local Infrastructure:
  - Daily production backtests
  - Live trading execution
  - Data storage (avoid egress fees)

Cloud (on-demand):
  - Large parameter sweeps (spin up 100+ instances, tear down when done)
  - GPU-heavy ML training
  - Cross-team collaboration
  - Disaster recovery
```

### AWS + Coiled for Backtesting at Scale

AWS with Coiled (Dask cloud deployment) enables:
- Terabyte-scale dataset processing
- Training times from days to minutes
- Automated cloud scaling — focus on strategies, not infrastructure
- XGBoost + Dask + Coiled integration for distributed ML backtesting

---

## 13. Data Quality

### Common Data Quality Issues

| Issue | Description | Impact on Backtesting |
|-------|-------------|-----------------------|
| **Missing data / gaps** | Expected bars absent | Phantom signals at gap boundaries |
| **Outliers / spikes** | Flash crashes, fat-finger trades | Inflated/deflated returns |
| **Stale rates** | Consecutive unchanged values | Distorted volatility, false low-risk |
| **Timestamp drift** | Clock skew across exchanges | Incorrect cross-exchange analysis |
| **Corporate actions** | Splits, dividends (less relevant for crypto) | Wrong price levels |
| **Exchange maintenance** | Scheduled/unscheduled downtime | Data gaps misinterpreted as signals |
| **Delisting events** | Token removals, chain migrations | Survivorship bias |

### Gap Detection

```python
def detect_gaps(df, expected_freq='1min'):
    """Detect missing bars in OHLCV data."""
    expected_index = pd.date_range(start=df.index[0], end=df.index[-1], freq=expected_freq)
    missing = expected_index.difference(df.index)

    # Filter out known non-trading periods (e.g., exchange maintenance)
    # Crypto trades 24/7 but exchanges have maintenance windows
    return missing

def fill_gaps(df, method='ffill', max_gap=5):
    """Fill small gaps, flag large gaps."""
    df_reindexed = df.reindex(pd.date_range(df.index[0], df.index[-1], freq='1min'))
    small_gaps = df_reindexed.isna().sum(axis=1).rolling(max_gap+1).sum() <= max_gap
    df_filled = df_reindexed.where(small_gaps).ffill(limit=max_gap)
    return df_filled
```

### Outlier Handling for Crypto

```python
def detect_outliers(df, column='close', window=100, threshold=5.0):
    """Detect price outliers using rolling z-score."""
    rolling_mean = df[column].rolling(window).mean()
    rolling_std = df[column].rolling(window).std()
    z_score = (df[column] - rolling_mean) / rolling_std
    outliers = z_score.abs() > threshold
    return outliers

def handle_outliers(df, outliers, method='clip'):
    """Handle detected outliers."""
    if method == 'clip':
        # Clip to rolling bounds
        upper = df['close'].rolling(100).mean() + 5 * df['close'].rolling(100).std()
        lower = df['close'].rolling(100).mean() - 5 * df['close'].rolling(100).std()
        df.loc[outliers, 'close'] = df['close'].clip(lower=lower, upper=upper)
    elif method == 'interpolate':
        df.loc[outliers, 'close'] = np.nan
        df['close'] = df['close'].interpolate(method='linear')
    return df
```

### Crypto-Specific Data Quality

- **No corporate actions** in the traditional sense, but watch for:
  - Token swaps / chain migrations (e.g., old token to new token)
  - Hard forks creating new assets
  - Exchange-specific delisting and relisting
  - Stablecoin depegs (OHLCV looks normal but USD value is wrong)
- **Cross-exchange consistency**: Same pair can have different prices across exchanges. Use VWAP or median across exchanges for reference pricing.
- **Volume manipulation**: Wash trading is common on some exchanges. Filter by trusted exchange list or use reported vs actual volume metrics.

### Data Quality Pipeline

```
Raw Data → Gap Detection → Outlier Detection → Normalization → Validation → Clean Data
                |                |                                    |
                v                v                                    v
           Gap Report      Outlier Report                     Quality Score
```

---

## 14. Reproducibility

### The Reproducibility Stack

```
1. Code versioning (Git)
2. Data versioning (DVC, lakeFS, or hash-based snapshots)
3. Environment versioning (Docker, Conda lock files)
4. Configuration versioning (YAML/JSON config tracked in Git)
5. Random seed control (centralized seed management)
6. Experiment tracking (MLflow, W&B, or custom)
```

### Deterministic Backtests

**Centralized Seed Management:**
```python
import random
import numpy as np

def set_global_seed(seed: int = 42):
    """Set all random seeds for full reproducibility."""
    random.seed(seed)
    np.random.seed(seed)
    # If using PyTorch:
    # torch.manual_seed(seed)
    # torch.cuda.manual_seed_all(seed)
    # torch.backends.cudnn.deterministic = True
```

**Deterministic Data Splits:**
```python
# Always use a fixed seed for train/test splits
from sklearn.model_selection import TimeSeriesSplit
# TimeSeriesSplit is inherently deterministic (no randomness)
tscv = TimeSeriesSplit(n_splits=5)
```

### Data Version Control

```python
import hashlib

def hash_dataframe(df: pd.DataFrame) -> str:
    """Generate deterministic hash of a DataFrame for version tracking."""
    return hashlib.sha256(
        pd.util.hash_pandas_object(df).values.tobytes()
    ).hexdigest()

# Store hash alongside backtest results
data_hash = hash_dataframe(ohlcv_data)
results_metadata = {
    'data_hash': data_hash,
    'data_source': 'binance_spot',
    'date_range': '2020-01-01 to 2024-12-31',
    'strategy_version': 'v2.3.1',
    'seed': 42,
    'timestamp': datetime.utcnow().isoformat()
}
```

### Environment Reproducibility

```dockerfile
# Dockerfile for reproducible backtesting
FROM python:3.11-slim
COPY requirements.lock.txt .
RUN pip install --no-cache-dir -r requirements.lock.txt
COPY src/ /app/src/
COPY config/ /app/config/
WORKDIR /app
ENTRYPOINT ["python", "-m", "backtest.run"]
```

### Guarding Against Overfitting

- **Probability of Backtest Overfitting (PBO)**: Quantifies the chance that an optimized strategy's out-of-sample performance will be negative
- **Deflated Sharpe Ratio (DSR)**: Adjusts Sharpe ratio for the number of trials run
- **Walk-forward analysis**: Train on expanding window, test on subsequent out-of-sample period
- **Regime awareness**: Financial markets are non-stationary — a strategy optimized on one regime may fail in another

### Experiment Tracking

```python
import mlflow

mlflow.set_experiment("sma_crossover_btcusdt")
with mlflow.start_run():
    mlflow.log_param("fast_period", 20)
    mlflow.log_param("slow_period", 50)
    mlflow.log_param("data_hash", data_hash)
    mlflow.log_param("seed", 42)

    results = run_backtest(...)

    mlflow.log_metric("sharpe_ratio", results.sharpe)
    mlflow.log_metric("max_drawdown", results.max_drawdown)
    mlflow.log_metric("total_return", results.total_return)
    mlflow.log_artifact("config.yaml")
```

---

## 15. Python Libraries for Quant Finance

### Tier 1 — Foundational (Must Have)

| Library | Purpose | Notes |
|---------|---------|-------|
| **NumPy** | Matrix math, linear algebra | Foundation for everything. Used by Goldman Sachs, JPMorgan. |
| **pandas** | Time-series data manipulation (OHLCV) | Industry standard. BlackRock built InGen on it. |
| **SciPy** | Optimization, signal processing, curve fitting | Essential for portfolio optimization, statistical tests. |
| **Statsmodels** | Statistical modeling, econometrics | Time-series analysis (ARIMA, GARCH), regression. JPMorgan job listings mention it. |

### Tier 2 — Technical Analysis

| Library | Purpose | Notes |
|---------|---------|-------|
| **TA-Lib** | Technical indicators (SMA, RSI, MACD, Bollinger) | C library with Python wrapper. May need binary install. |
| **pandas-ta** | Technical indicators as pandas extension | Pure Python, applies directly to DataFrames. |
| **tulipy** | Technical analysis (tulipindicators wrapper) | Fast C-based indicators. |

### Tier 3 — Backtesting & Trading

| Library | Purpose | Notes |
|---------|---------|-------|
| **VectorBT** | Fast vectorized backtesting | Numba-accelerated. Free version maintained only. |
| **Backtrader** | Event-driven backtesting | Beginner-friendly. Stalled development. |
| **Zipline-Reloaded** | Event-driven backtesting (Quantopian) | Pipeline architecture. Installation challenges. |
| **NautilusTrader** | Production-grade backtesting + live | Rust core + Python API. |
| **Freqtrade** | Crypto trading bot + backtesting | FreqAI for ML integration. |

### Tier 4 — Performance & Risk

| Library | Purpose | Notes |
|---------|---------|-------|
| **QuantStats** | Performance analytics, HTML tear sheets | Sharpe, drawdown, win rate, benchmark comparison. |
| **PyFolio** | Deep performance/risk analysis | Bayesian statistics for return analysis. |
| **Riskfolio-Lib** | Portfolio optimization | Mean-variance, risk parity, Black-Litterman. |
| **skfolio** | Portfolio optimization (sklearn-compatible) | Unified interface for portfolio models. |
| **QuantLib** | Derivatives pricing, risk management | Options, bonds, swaps pricing. |

### Tier 5 — Acceleration & Scale

| Library | Purpose | Notes |
|---------|---------|-------|
| **Numba** | JIT compilation for Python | 50x+ speedup for numerical loops. |
| **Polars** | Fast DataFrame library (Rust-based) | 10-50x faster than pandas. Rising star. |
| **cuDF (RAPIDS)** | GPU-accelerated DataFrames | Up to 150x faster. Zero-code-change in 2024+. |
| **Dask** | Distributed computing | Scale pandas/NumPy to clusters. |
| **Ray** | Distributed computing + shared memory | Best for parallel backtesting. |

### Tier 6 — Data & ML

| Library | Purpose | Notes |
|---------|---------|-------|
| **CCXT** | Unified crypto exchange API | 100+ exchanges, REST + WebSocket. |
| **Cryptofeed** | Normalized exchange WebSocket feeds | Multi-exchange, real-time data normalization. |
| **scikit-learn** | Machine learning | Classification, regression, clustering. |
| **XGBoost / LightGBM** | Gradient boosting | Top performers for tabular financial data. |
| **OpenBB** | Financial data terminal | Open-source Bloomberg alternative. |

---

## 16. Codebase Structure for Maintainability and Speed

### Recommended Project Layout

```
crypto_backtest/
├── config/
│   ├── settings.yaml          # Global settings
│   ├── strategies/            # Per-strategy configs
│   │   ├── sma_crossover.yaml
│   │   └── rsi_mean_reversion.yaml
│   └── exchanges/             # Per-exchange configs
│       ├── binance.yaml
│       └── coinbase.yaml
├── src/
│   ├── data/
│   │   ├── __init__.py
│   │   ├── loaders.py         # Data loading (Parquet, DB, API)
│   │   ├── normalizers.py     # Cross-exchange normalization
│   │   ├── quality.py         # Gap detection, outlier handling
│   │   └── storage.py         # Write to Parquet/DB
│   ├── features/
│   │   ├── __init__.py
│   │   ├── indicators.py      # Vectorized indicator computation
│   │   ├── incremental.py     # Incremental (streaming) indicators
│   │   └── store.py           # Feature store (precomputed + cache)
│   ├── strategy/
│   │   ├── __init__.py
│   │   ├── base.py            # Abstract strategy interface
│   │   ├── signals.py         # Signal generation
│   │   └── implementations/   # Concrete strategies
│   │       ├── sma_crossover.py
│   │       └── rsi_mean_reversion.py
│   ├── execution/
│   │   ├── __init__.py
│   │   ├── simulator.py       # Backtest execution engine
│   │   ├── costs.py           # Transaction costs, slippage
│   │   └── portfolio.py       # Position tracking, P&L
│   ├── optimization/
│   │   ├── __init__.py
│   │   ├── sweep.py           # Parameter sweep orchestration
│   │   ├── parallel.py        # Ray/multiprocessing workers
│   │   └── objectives.py      # Sharpe, Sortino, custom metrics
│   ├── analytics/
│   │   ├── __init__.py
│   │   ├── metrics.py         # Performance metrics
│   │   ├── reports.py         # Report generation
│   │   └── visualization.py   # Charts, tear sheets
│   └── live/
│       ├── __init__.py
│       ├── feed.py            # Real-time data feed (WebSocket)
│       ├── executor.py        # Live order execution
│       └── monitor.py         # Position monitoring
├── tests/
│   ├── test_indicators.py
│   ├── test_strategy.py
│   ├── test_execution.py
│   └── test_data_quality.py
├── notebooks/
│   └── research/              # Jupyter notebooks for exploration
├── data/
│   ├── raw/                   # Raw downloaded data
│   ├── processed/             # Cleaned Parquet files
│   └── features/              # Precomputed feature store
└── scripts/
    ├── download_data.py
    ├── run_backtest.py
    └── optimize.py
```

### Design Principles

**1. Separation of Concerns**
- Data handling, signal generation, execution, risk management, and reporting in distinct modules
- Each module should be independently testable and replaceable

**2. Strategy as Interface**
```python
from abc import ABC, abstractmethod
import numpy as np

class Strategy(ABC):
    @abstractmethod
    def generate_signals(self, features: dict[str, np.ndarray]) -> np.ndarray:
        """Return array of signals: 1=long, -1=short, 0=flat."""
        pass

    @abstractmethod
    def get_required_features(self) -> list[str]:
        """Return list of feature names this strategy needs."""
        pass
```

**3. Execution Engine Swap (OOP Inheritance)**
```python
class ExecutionEngine(ABC):
    @abstractmethod
    def execute(self, signal, price, portfolio_state): ...

class BacktestExecutor(ExecutionEngine):
    """Simulated execution with configurable slippage/costs."""
    ...

class LiveExecutor(ExecutionEngine):
    """Real exchange execution via CCXT."""
    ...
```

**4. Hot Path Optimization**
- Write inner simulation loops in Numba (`@njit`)
- Keep pandas for data loading and reporting only
- Use NumPy arrays for all computation
- Profile with `py-spy` or `line_profiler` to find actual bottlenecks

**5. Configuration-Driven**
```yaml
# config/strategies/sma_crossover.yaml
strategy:
  name: sma_crossover
  version: "2.1.0"
  parameters:
    fast_period: 20
    slow_period: 50
  features:
    - sma_20
    - sma_50
  execution:
    slippage_bps: 5
    commission_bps: 10
  risk:
    max_position_pct: 0.1
    stop_loss_pct: 0.02
```

**6. Composable Algorithm Blocks (bt-style)**
```python
# Build strategies from reusable blocks
pipeline = Pipeline([
    LoadData('BTC/USDT', '1h'),
    ComputeFeatures(['sma_20', 'sma_50', 'rsi_14']),
    GenerateSignals(SMACrossover(fast=20, slow=50)),
    SizePositions(FixedFraction(0.1)),
    SimulateExecution(slippage_bps=5),
    ComputeMetrics(['sharpe', 'max_drawdown', 'total_return']),
])
results = pipeline.run(start='2020-01-01', end='2024-12-31')
```

---

## 17. Zipline Pipeline Architecture

### Core Design

Zipline (Quantopian's engine) uses a **Pipeline API** that computes cross-sectional factors across all assets simultaneously.

**Key components:**
- **Pipeline**: Collection of named `Term` instances (columns) and a `Filter` (screen)
- **Factors**: Take historical bar arrays, produce output per security (e.g., RSI, moving averages)
- **Filters**: Boolean-valued terms for screening (e.g., "top 100 by volume")
- **Classifiers**: Categorical outputs (e.g., sector classification)
- **SimplePipelineEngine**: Loads data via dispatching loaders, passes to compute functions

### Execution Model

```
Term definitions → TermGraph (DAG) → ExecutionPlan → Optimized execution
```

- Internally builds a dependency graph (TermGraph)
- Creates an ExecutionPlan that optimizes execution order
- Vectorizes factor computation where possible (e.g., 30x speedup for RollingPearson)

### Data Architecture

- **bcolz**: Compressed, columnar format on disk for efficient retrieval
- **SQLite**: Metadata storage (asset info, calendar)
- **Point-in-time adjustments**: Automatically computes split/dividend adjustments based on backtest date range
- **DataFrameLoader**: Simple in-memory loader for research

### Domain System

Added for international market support:
- `Domain` determines asset universe and trading calendar
- Supports GENERIC, US_EQUITIES, CA_EQUITIES, GB_EQUITIES, etc.
- Generic vs specialized datasets (e.g., `EquityPricing` base, `USEquityPricing` specialized)

### Lessons for Custom Engines

1. **Separate data loading from computation** — loaders are pluggable, computation is generic
2. **Build a dependency graph** — enables optimization and prevents redundant computation
3. **Use columnar storage** — bcolz/Parquet for efficient column reads
4. **Point-in-time correctness** — adjustments must be computed relative to the simulation date
5. **Factor computation should be vectorized** — operate on 2D arrays (time x assets)

---

## 18. VectorBT Architecture Internals

### Core Design Philosophy

VectorBT replaces traditional OOP backtesting (strategies as classes) with **vectorization over iteration**. Each strategy instance is represented as a vector; multiple instances are packed into multi-dimensional arrays and processed simultaneously.

### Layered Architecture

```
Data Sources (Yahoo, Binance, CCXT)
    |
    v
Indicator Factory (parameter broadcasting, Numba optimization)
    |
    v
Portfolio Engine (Numba-compiled simulation)
    |
    v
Records & Analytics (MappedArray, sparse representations)
```

### Key Technical Innovations

**1. Broadcasting System**
- Inspired by NumPy broadcasting, but one-dimensional arrays are always per-row (time-series convention)
- `Portfolio.from_signals` broadcasts 50+ arguments with zero additional memory overhead
- If arrays differ in size, VBT "stretches" smaller arrays to match

**2. Argument Preparation Pipeline**
When calling `Portfolio.from_signals`:
```
Raw arguments → Enum mapping → Broadcasting → Dtype checks → Template substitution → Numba functions
```
This pipeline is exposed as a separate class for customization.

**3. Feature-Separate Array Design**
- Multiple backtests could form a 3D cube, but pandas cannot handle this
- Non-changing features would waste memory if replicated across all backtests
- VBT manages variability by processing features as separate arrays

**4. Numba-Compiled Core**
- All hot-path portfolio simulation runs through `vectorbt.portfolio.nb`
- Record classes (orders, trades, positions, drawdowns) for efficient event tracking
- `flex_simulate_nb` for maximum flexibility (multiple orders per symbol per bar)

**5. Callback System**
Pre/post-processing callbacks at every simulation step:
- Simulation-level, group-level, segment-level callbacks
- Order modification callbacks
- Enables custom logic without forking the engine

### Performance

- Can process **2 million individual backtests** in a single run
- Tests 1,000 parameter combinations in seconds
- Memory-efficient: Broadcasting adds almost no overhead regardless of shape size

---

## 19. Modern Crypto Backtesting Alternatives (2024-2025)

### Framework Comparison for Crypto

| Framework | Language | AI/ML Integration | Live Trading | Exchange Support | Key Strength |
|-----------|---------|-------------------|-------------|-----------------|--------------|
| **Freqtrade** | Python | FreqAI (built-in) | Yes (CCXT) | 20+ via CCXT | ML integration, Hyperopt |
| **NautilusTrader** | Python + Rust | AI-ready (pluggable) | Yes | Multi-asset | Performance, backtest-live parity |
| **Jesse** | Python | Pluggable | Yes | Crypto exchanges | Clean API, intuitive |
| **OctoBot** | Python | GPT-based strategy assist | Yes | Major exchanges | Modular "tentacles" architecture |
| **FinRL** | Python | DRL (built-in) | Via gym env | Crypto + stocks | Deep RL for trading |
| **VectorBT** | Python | Via custom factors | Via StrateQueue | Data-agnostic | Raw speed, research |
| **QuantConnect (LEAN)** | C#/Python | ML libraries | Yes | Multi-asset | Institutional-grade cloud infra |

### Key Trends (2025)

1. **AI integration is default** — FreqAI, NautilusTrader AI-ready, OctoBot GPT integration
2. **Backtest-to-live parity** — Same code for both (NautilusTrader, Freqtrade, Jesse)
3. **Rust for performance** — NautilusTrader's Rust core sets the performance bar
4. **Community-driven** — Open-source frameworks with active Discord/Telegram communities
5. **Multi-asset convergence** — Crypto-first frameworks adding traditional asset support

### Recommended Stack for Crypto Backtesting (2025)

```
Research/Prototyping:  VectorBT (fast iteration) or Freqtrade (crypto-native)
Production Backtest:   NautilusTrader (performance + live parity)
Live Trading:          NautilusTrader or Freqtrade (depending on complexity)
Data Pipeline:         Cryptofeed → QuestDB → Parquet
Feature Store:         Custom (Parquet-based with LRU cache)
ML Integration:        FreqAI or custom (XGBoost/LightGBM)
Monitoring:            Grafana + custom dashboards
```

---

## Quick Reference: Architecture Decision Checklist

### Starting a New Backtesting Project

- [ ] **Data format**: Parquet for storage, Arrow/Feather for inter-process exchange
- [ ] **Database**: QuestDB for tick data, TimescaleDB if in Postgres ecosystem
- [ ] **Indicators**: Precompute with vectorized pandas/NumPy, cache in feature store
- [ ] **Simulation engine**: Numba-compiled loops for path-dependent logic
- [ ] **Parameter sweeps**: Ray for distributed, multiprocessing for local
- [ ] **Framework**: VectorBT for research speed, NautilusTrader for production
- [ ] **GPU**: cuDF for large-scale data processing (if NVIDIA GPU available)
- [ ] **Reproducibility**: Seed management, Docker, data hashing, experiment tracking
- [ ] **Data quality**: Gap detection, outlier handling, exchange normalization
- [ ] **Infrastructure**: Local for steady workloads, cloud burst for large sweeps

### Performance Optimization Priority

1. **Vectorize** first (pandas/NumPy operations)
2. **Numba** for unavoidable loops
3. **Parallelize** across independent runs (Ray/multiprocessing)
4. **GPU** for massive datasets (cuDF/RAPIDS)
5. **Distribute** for cluster-scale (Dask/Ray clusters)

---

*This document represents a synthesis of current best practices and tooling as of early 2026.
Technologies and benchmarks evolve rapidly — validate specific claims against current documentation.*
