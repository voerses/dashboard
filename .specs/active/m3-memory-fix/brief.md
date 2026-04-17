# M3: Memory Fix — Bound all v5 paper trader memory to < 1.2GB steady-state

## Problem

The v4 paper trader grows to 2GB+ RSS within 24-48 hours of continuous
operation, requiring manual restarts. Production paper is running at $450K
notional — restarts cause missed signals and stale state. The memory growth
is deterministic: every tick adds data to unbounded structures that are never
pruned.

## Root Causes

Three structures account for ~95% of the leak:

1. **`_context_cache`** (`paper_engine.py:2321`): Unbounded dict of
   `StrategyContext` objects. At 236 tokens x 2 markets x 30 indicator arrays
   x full history, this reaches **2.3GB** within hours. Each context holds the
   entire bar history for its token — and is never evicted.

2. **`TokenSignals` precompute**: Every tick rebuilds full-length indicator
   arrays (30 arrays x 236 tokens x all strategies). This reallocates
   **~2.4GB per tick** — the old arrays become garbage but GC pressure causes
   RSS to ratchet upward because Python rarely returns pages to the OS.

3. **Append-only structures**: `closed_trades` (list), `_hist_cache` (dict),
   `_alerts` (list), `_pending_alerts` (list), `_filled_4h_windows` (set) —
   all grow without bounds over the lifetime of the process.
   `_filled_4h_windows` is a set of `(token, window_start)` tuples that grows
   without bound as new 4h windows are encountered; needs TTL eviction or maxlen.

### Source file:line references for all memory hogs

| Structure | File:line | Growth pattern |
|-----------|-----------|----------------|
| `_context_cache` | `paper_engine.py:2321` | Unbounded dict keyed by (token, market); never evicted |
| `_hist_cache` | `paper_engine.py:162` | Unbounded dict of historical bar data |
| `TokenSignals` rebuild | `signals.py:33-200+` | Full-length indicator arrays rebuilt every tick (~2.4GB allocated per tick) |
| `_alerts` | `paper_engine.py:101` (appended at lines 1800, 1866) | List appended per alert, never pruned |
| `_pending_alerts` | `paper_engine.py:3365` | List of pending alerts, never pruned |
| `_filled_4h_windows` | `paper_engine.py:176` | Set of `(token, window_start)` tuples, never evicted |
| `closed_trades` | `position.py:106,149` / `simulator.py:363` | List appended on every trade close, never pruned |

### Additional memory fix: PaperTickResult shallow-copy

Replace `dict(self._last_known_prices)` shallow-copy pattern in `PaperTickResult`
with `MappingProxyType` (immutable view, zero-copy). The current pattern creates a
full dict copy on every tick result, wasting allocation bandwidth.

## Scope

### In scope

- Bounding all unbounded data structures in the paper engine
- Slotted dataclasses for high-instance-count objects
- float32 downcast for indicator arrays where precision is not needed
- Incremental signal computation (replace full-rebuild pattern)
- Closed trade archival to parquet
- RSS monitoring and alerting in the paper runner
- Memory soak test (replays recorded tick data at accelerated speed with mock TestClock; CI-compatible, not a real 24h run)

### Out of scope

- Backtest engine memory (runs to completion, not long-lived)
- Dashboard backend changes beyond reading from parquet archive
- Multi-process or shared-memory architectures
- Changes to signal logic, position management, or trade execution
- v4 changes (v4 is upstream reference only for this milestone)

## Deliverables

### 1. RollingCache pattern (pre-allocated numpy ring buffer)

A `RollingCache` utility using pre-allocated numpy ring buffers for all
historically unbounded structures. **Not** `collections.deque(maxlen=N)` —
deque has per-element Python object overhead and forces GC churn. Instead:

- `RollingCache` allocates `np.zeros((maxlen, N_FIELDS), dtype=np.float32)` per (token, BarType)
- Column-oriented storage: separate 1D arrays per field (`_close`, `_high`, `_low`, `_volume`, `_atr`, etc.)
- `append()` = indexed write at `head % maxlen`, O(1), ~50ns, zero allocation
- `arrays()` = zero-copy view via slice `buf[tail:head]` when not wrapped (>99% of the time after warmup); `np.concatenate` on wrap (~5us worst case)
- Memory budget: 236 tokens x 3 BarTypes x 2000 bars x 6 fields x 4 bytes = ~34MB total
- Prior art: kdb+/NautilusTrader/TA-Lib all use pre-allocated ring buffers for this pattern

Default maxlen by data type:

| Data type           | maxlen | Retention    |
|---------------------|--------|--------------|
| 1-min bars          | 1440   | 24 hours     |
| 1-hour bars         | 2000   | ~83 days     |
| Daily bars          | 500    | ~1.4 years   |
| closed_trades       | 1000   | archive rest |
| _alerts             | 1000   | —            |
| _pending_alerts     | 500    | —            |
| _filled_4h_windows  | 500    | —            |

**`_context_cache` replacement specifics:**
The unbounded `dict[tuple, StrategyContext]` at `paper_engine.py:2321` is replaced
by a `RollingCache` with a pre-allocated ring buffer per `BarType`. Each strategy
declares its data subscriptions (which BarTypes it needs); only subscribed BarTypes
are cached. StrategyContext objects are NOT cached long-term — they are constructed
on-the-fly from the rolling bar data in the cache. This eliminates the leak
because bar data is bounded by the ring buffer maxlen, and StrategyContext objects
are transient (created per-tick, GC'd after the tick completes). LRU eviction of
StrategyContext objects is NOT used — the fix is structural (bounded input data),
not a cache policy on the output objects.

### 2. Slotted dataclasses

`@dataclass(slots=True)` applied to: `Position`, `ClosedTrade`, `BarContext`,
`TokenSignals`, `ScalingEvent`. Halves per-instance memory footprint by
eliminating `__dict__`.

### 3. float32 downcast

Indicator arrays that do not require float64 precision are stored as float32.

**Downcast to float32:**
- Trailing multipliers: `trail_schedule`, `time_trail_schedule`, `max_trail_mult_arr`
- Scores: `conviction` (v4) / `priority` (v5) score arrays
- Volume: `volume`, `vol_20` arrays
- General indicators that don't need sub-penny precision

**Keep at float64** (precision-critical):
- Prices, quantities, margin, PnL, fees, funding
- ATR arrays (used in stop/entry calculations where precision matters)

### 4. Incremental signal computation

Replace the full-rebuild pattern in TokenSignals precompute:

- Keep indicator arrays alive across ticks (append new bar data in-place)
- Only recompute the trailing lookback window, not the full history
- Eliminates the _context_cache leak — contexts hold bounded rolling data

**Incremental indicator classification:**
- **Fully incremental** (append one value per new bar): EMA, SMA, ATR — these
  maintain running state and update O(1) per bar
- **Incremental via chain**: MACD — incremental because it's composed of EMA
  chains (fast EMA, slow EMA, signal EMA)
- **Requires full recompute**: Cross-sectional ranking — needs all tokens' values
  at the current bar to produce percentile ranks; cannot be incrementalized.
  Recompute is bounded by token count (236), not history length.

**Parity requirement**: Incremental vs full-rebuild computation must produce
identical results within float tolerance (1e-10 for float64 fields, 1e-4 for
float32 downcast fields). This is a dedicated acceptance test (see AC-parity below).

### 5. Bounded closed_trades with parquet archive

- In-memory: ring buffer (maxlen=1000) — most recent 1000 trades
- Archive: periodic flush to `trades_archive.parquet` in the state directory
- Dashboard reads from both sources (in-memory for live, parquet for history)
- State roundtrip (save/load) preserves the bounded structure correctly

**Archival mechanics:**
- **Trigger**: Flush when buffer is full and a new trade would evict the oldest.
  Alternatively, flush on a timer (every 100 trades or every N minutes) —
  implementation should prefer the eviction trigger to avoid data loss.
- **Parquet schema**: All `ClosedTrade` fields including identity fields from M2
  (`parent_position_id`, `exec_seq`, `exec_type`, `is_terminal`, `triggered_by`,
  `has_scaling`). Schema must be forward-compatible with M2 additions.
- **Write mode**: Use `pq.ParquetWriter` with `append=True` to add new row
  groups to the existing parquet file. Do NOT use
  `pd.DataFrame.to_parquet(mode='append')` — that API does not exist. The
  pattern is: open a `pq.ParquetWriter` in append mode, call
  `writer.write_table(pa.Table.from_pandas(df))`, then `writer.close()`.
- **File rotation**: Single file `trades_archive.parquet` per state directory.
  If file exceeds 100MB, rotate to `trades_archive_{timestamp}.parquet` and
  start a new file. Dashboard scans all `trades_archive*.parquet` files.

### 6. Memory monitoring

- RSS logged every 60s in the paper runner
- Alert threshold at 1.2GB (log warning)
- Optional `tracemalloc` snapshot every 6h for first week post-deploy
  (toggled by config flag, off by default)

## Acceptance Criteria

| #    | Criterion | Verification |
|------|-----------|--------------|
| AC1  | `RollingCache` class exists using pre-allocated numpy ring buffer with configurable maxlen | Unit test |
| AC2  | `_context_cache` replaced with bounded pattern; no unbounded dict of StrategyContext | Code review + soak test |
| AC3  | `_hist_cache` replaced with bounded pattern | Code review + soak test |
| AC4  | `TokenSignals` computed incrementally (append + recompute tail), not full rebuild per tick | Unit test: verify only last N bars recomputed |
| AC5  | `closed_trades` bounded at 1000 in-memory; overflow archived to parquet | Unit test: insert 1500 trades, verify 1000 in memory + 500 in parquet |
| AC6  | `_alerts` and `_pending_alerts` bounded | Unit test |
| AC7  | `_filled_4h_windows` bounded | Unit test |
| AC8  | `Position`, `ClosedTrade`, `BarContext`, `TokenSignals`, `ScalingEvent` use `@dataclass(slots=True)` | Unit test: verify `__slots__` exists, `__dict__` raises AttributeError |
| AC9  | Indicator arrays use float32 where precision is not needed; prices/PnL/fees remain float64 | Unit test: check dtype of specific fields |
| AC10 | RSS logged every 60s in paper runner; warning at 1.2GB threshold | Integration test: verify log output |
| AC11 | Memory soak test: 24h simulated paper run via recorded tick replay at accelerated speed with mock clock (TestClock). Not a real 24h wall-clock run — must complete in CI within minutes. RSS growth < 100MB after initial warmup | Soak test (CI-compatible, accelerated replay) |
| AC12 | All existing v5 tests pass — no behavior change | `pytest` full suite |
| AC13 | Dashboard closed_trades endpoint reads from both in-memory ring buffer and parquet archive | Integration test |
| AC14 | Paper state save/load roundtrip preserves bounded structures correctly | Unit test: save state, reload, verify structure types and contents |
| AC15 | Incremental signal parity: incremental computation produces identical results to full-rebuild within float tolerance (1e-10 for float64, 1e-4 for float32 downcast) | Parity test: run both paths on same 500-bar window, assert max abs diff within tolerance per field |
| AC16 | `PaperTickResult` uses `MappingProxyType` instead of `dict()` shallow-copy for `_last_known_prices` | Code review: no `dict(self._last_known_prices)` pattern; unit test: returned object is `MappingProxyType` |

## Estimates

**Total: 30-50 hours**

| Component                    | Estimate |
|------------------------------|----------|
| RollingCache + bounded swaps | 6-8h     |
| Slotted dataclasses          | 3-4h     |
| float32 downcast             | 3-4h     |
| Incremental signal compute   | 10-15h   |
| Closed trades archive        | 4-6h     |
| Memory monitoring            | 2-3h     |
| Soak test + CI integration   | 4-6h     |
| Integration testing          | 3-5h     |

## Dependencies

- **M1 (v5 fork)**: Must exist — all changes target v5 modules
- **M2 (position scaling)**: Mostly independent, but M3 needs to know about M2's
  `ScalingEvent` dataclass if it wants to apply `@dataclass(slots=True)` to it.
  M2 should deliver `ScalingEvent` first, or M3 conditionally applies slots only
  if the class already exists. Recommended: M2 delivers first for the `ScalingEvent`
  class; M3 then adds `slots=True` to it along with the other dataclasses.

## Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| Incremental signal computation introduces subtle numerical drift vs full rebuild | HIGH | Parity test: run both paths on same data, assert max abs diff < 1e-6 for float64 fields |
| Slotted dataclasses break pickle/deepcopy in state save/load | MEDIUM | Test roundtrip explicitly (AC14) |
| float32 downcast causes precision loss in edge cases | LOW | Only downcast fields with values < 1e6 and no currency semantics |
| Ring buffer eviction drops data needed by downstream analytics | MEDIUM | Archive to parquet before eviction; dashboard reads both sources |

## Parity Gate

This milestone passes when:

1. All existing v5 tests pass (zero behavior change)
2. Memory soak test passes (RSS growth < 100MB over 24h simulated)
3. Parity test: incremental signal output matches full-rebuild output within tolerance
