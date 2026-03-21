# Sub-Hourly Exit System — Architecture & E2E Testing Guide

> **Created:** 2026-03-21
> **Applies to:** `/dev` and `/strategy` pipelines
> **Key files:** `v4/candle_aggregator.py`, `v4/paper_engine.py`, `v4/minute_exits.py`, `v4/tests/test_e2e_sub_hourly.py`

---

## 1. Data Flow Architecture

```
PriceMonitor (WS thread)          Main Thread (run_paper_multi.py)
   |                                   |
   | on_price(token, price, ts_ms)     | Every 2s in sleep loop:
   |        |                          |   candles = aggregator.flush_completed()
   |        v                          |   if candles:
   |  CandleAggregator                 |     engine.process_sub_hourly_exits(candles)
   |  (thread-safe buffer)             |
   |                                   | Every 60min:
   |                                   |   engine.tick()  (full hourly pipeline)
```

### Step-by-step

1. **PriceMonitor** (WebSocket) receives real-time prices, calls `CandleAggregator.on_price(token, price, timestamp_ms)`
2. **CandleAggregator** buckets ticks into N-minute candles (N = `exit_resolution`). When a new interval starts, the previous candle is "completed" and moved to an internal buffer
3. **Main thread** calls `aggregator.flush_completed()` every 2 seconds. Returns `{token: (high, low, close)}` for all completed candles since last flush
4. If candles exist, main thread calls `engine.process_sub_hourly_exits(candles)` which:
   - Looks up each open position's cached ATR/ADV from the last hourly tick
   - Runs `check_candle_exits()` (shared with backtest in `v4/minute_exits.py`)
   - Closes positions via `_sim._close_position()`
   - Handles linked legs (hedge pairs)
   - Updates `_last_known_prices` for MTM equity
   - Persists state (trades.jsonl, state.json, equity.csv)

### Configuration

```python
# Per-strategy exit resolution (in StrategySpec, NOT PaperConfig)
StrategySpec(strategy_id="s98", weight=1.0, market="perp", max_positions=15, exit_resolution=30)
StrategySpec(strategy_id="s56", weight=1.0, market="perp", max_positions=15, exit_resolution=5)
StrategySpec(strategy_id="s57", weight=1.0, market="combined", max_positions=15, exit_resolution=0)  # hourly

# Engine computes effective resolution = min(non-zero strategy resolutions)
# Positions whose strategy has exit_resolution=0 are skipped during sub-hourly checks
```

**IMPORTANT:** `exit_resolution` is on `StrategySpec`, not `PaperConfig`. The engine derives `_effective_exit_resolution` from the strategies. Use `getattr(self, '_effective_exit_resolution', 0)` in engine code because many tests create engines via `__new__` bypassing `__init__`.

---

## 2. Critical Implementation Details

### Exit price is ALWAYS candle close

`check_candle_exits()` returns the candle close `c` as exit price for ALL exit types (stop, target, circuit breaker). This is because sub-hourly candles don't have bar-level resolution to determine the exact intra-candle exit price. The close is used as a conservative approximation.

```python
# From minute_exits.py:check_candle_exits
# Stop breached:  returns ("stop", c)    NOT ("stop", stop_price)
# Target hit:     returns ("target", c)  NOT ("target", target_price)
# CB triggered:   returns ("cb", c)      NOT ("cb", cb_level)
```

### Exit priority: CB > stop > target

Circuit breaker is checked first, then stop, then target. This is intentional — CB is an emergency exit that takes priority.

### Trail tightening runs BEFORE stop check

Order within `check_candle_exits`:
1. Update `pos.highest` / `pos.lowest` from candle H/L
2. Breakeven ratchet (if `breakeven_atr > 0` and profit exceeds threshold)
3. Trail tightening via `_update_trail_minute()` — moves `pos.stop_price`
4. CB check → stop check → target check

### Position.breakeven_atr defaults to 0.5

This is the most common testing pitfall. `Position` has `breakeven_atr: float = 0.5`, meaning any position with >0.5 ATR unrealized profit triggers breakeven, moving the stop to entry price. **In e2e tests, always set `breakeven_atr=0.0` unless explicitly testing breakeven behavior.**

### Per-strategy ATR cache

`_cached_bar_data` is keyed by `(strategy_id, token)` tuple, not just `token`. This ensures multi-strategy portfolios get independent ATR values for the same token. The cache is populated after each hourly tick via `_cache_bar_data()`.

### Linked exit pricing

For hedge pairs (primary + linked legs), the linked position's exit runs `check_candle_exits` independently if there's a candle for the linked token. If not, falls back to the primary candle's close price.

### _last_known_prices update

Candle closes update `_last_known_prices` on EVERY flush (not just when exits occur). This keeps MTM equity fresh.

---

## 3. How to Write E2E Tests (Simulation-Based)

### Golden rule: NO network, NO real state

E2E tests simulate the full pipeline with synthetic data. They NEVER:
- Connect to WebSocket or any network
- Touch real paper trader state files
- Import or instantiate `PriceMonitor`

They ALWAYS:
- Use `tempfile.mkdtemp()` for state directories
- Create synthetic `PaperConfig` with `state_dir=tmpdir`
- Feed ticks directly to `CandleAggregator.on_price()`

### Test helper pattern

```python
from v4.candle_aggregator import CandleAggregator
from v4.paper_engine import PaperPortfolioEngine
from v4.paper_config import PaperConfig
from v4.config import StrategySpec
from v4.position import Position

def _ts_ms(minutes: float) -> int:
    """Convert minutes to timestamp in milliseconds."""
    return int(minutes * 60 * 1000)

def _make_config(state_dir: str, exit_resolution: int = 5) -> PaperConfig:
    return PaperConfig(
        strategies=[StrategySpec(
            strategy_id="s56", weight=1.0, market="combined", max_positions=10,
            exit_resolution=exit_resolution,  # Per-strategy, NOT on PaperConfig
        )],
        capital=200_000.0,
        mode="pool",
        pool_name="e2e_sim",
        max_portfolio_positions=40,
        concentration_limit=0.10,
        adv_cap_pct=0.10,
        min_position_usd=200.0,
        exchange="binance",
        seed=42,
        train_bars=0,
        recal_bars=99999,
        purge_bars=0,
        lookback_months=3,
        enable_purge_windows=False,
        state_dir=state_dir,
    )

def _make_position(
    token="BTC", strategy_id="s56", entry_price=100.0, direction=1,
    margin_usd=10_000.0, stop_price=90.0, trail_mult=3.0, target_mult=5.0,
    initial_risk=2.0, entry_bar=0, highest=0.0, lowest=0.0,
) -> Position:
    quantity = direction * margin_usd / entry_price
    return Position(
        position_id=f"{token}:{strategy_id}:0:primary",
        token=token, strategy_id=strategy_id, leg="primary",
        entry_bar=entry_bar, entry_price=entry_price,
        direction=direction, quantity=quantity, margin_usd=margin_usd,
        leverage=1.0, is_perp=True, fee_rate=0.0005,
        stop_mult=2.0, trail_mult=trail_mult, target_mult=target_mult,
        no_stop_bars=0, min_hold=0, max_hold=720, exit_regimes=set(),
        convex_exit=False, stop_price=stop_price,
        highest=highest if highest != 0.0 else entry_price,
        lowest=lowest if lowest != 0.0 else entry_price,
        initial_risk=initial_risk,
        breakeven_atr=0.0,  # CRITICAL: disable unless testing breakeven
    )
```

### Setting up an engine with a pre-existing position

```python
import tempfile

tmpdir = tempfile.mkdtemp()
config = _make_config(tmpdir, exit_resolution=5)
engine = PaperPortfolioEngine(config)

pos = _make_position(token="BTC", stop_price=90.0, target_mult=5.0)
engine.state.position_manager.open_positions.append(pos)

# Populate per-strategy ATR cache (required for sub-hourly exits)
engine._cached_bar_data[("s56", "BTC")] = {
    "atr": 2.0,
    "adv": 1e8,
    "regime": "normal",
    "bear_target_mult": 1.0,
}
```

### Feeding ticks and triggering exits

```python
agg = CandleAggregator(resolution_minutes=5)

# Feed ticks within interval 0 (minutes 0-4.99)
agg.on_price("BTC", 105.0, _ts_ms(0))
agg.on_price("BTC", 108.0, _ts_ms(2))

# Cross into interval 1 (minute 5+) — this completes candle 0
agg.on_price("BTC", 109.0, _ts_ms(5))

candles = agg.flush_completed()
# candles == {"BTC": (108.0, 105.0, 108.0)}  # (high, low, close)

closed = engine.process_sub_hourly_exits(candles)
```

### Testing different timeframes (parametrized)

```python
@pytest.mark.parametrize("resolution", [1, 5, 15, 30])
def test_boundary_fires_correctly(resolution):
    agg = CandleAggregator(resolution_minutes=resolution)
    # Feed one tick at minute 0, one tick at minute=resolution (crosses boundary)
    agg.on_price("BTC", 100.0, _ts_ms(0))
    agg.on_price("BTC", 101.0, _ts_ms(resolution))
    candles = agg.flush_completed()
    assert "BTC" in candles
```

---

## 4. Common Test Scenarios

### Stop breach
```python
# entry=100, stop=90, ATR=2. Feed low=89 to breach stop.
# Expected: exit_reason="stop", exit_price=candle_close (not 90)
```

### Target hit
```python
# entry=100, target_mult=5.0, ATR=2. Target = 100 + 5*2 = 110.
# Feed high=111 to hit target.
# Expected: exit_reason="target", exit_price=candle_close (not 110)
```

### Circuit breaker
```python
# entry=100, initial_risk=2.0, circuit_breaker_r=3.0. CB level = 100 - 3*2 = 94.
# Feed low=93 to trigger CB.
# IMPORTANT: Must set engine.config.strategies[i].circuit_breaker_r = 3.0
# (defaults to 0.0 which disables CB)
```

### Breakeven ratchet then stop
```python
# Set breakeven_atr=0.5 explicitly. Feed candle with profit > 0.5*ATR.
# Breakeven triggers, stop moves to entry. Then feed candle below entry.
# Expected: exit_reason="stop", stop moved to entry_price
```

### Multi-candle trail tightening (state continuity)
```python
# Feed candle 1: high=106, price rises, trail tightens from below
# Feed candle 2: high=108, trail tightens further
# Verify pos.stop_price increases between candles (trail ratchets up)
# Feed candle 3: low drops below tightened stop → exit
```

### Short positions
```python
# direction=-1, entry=100, stop=110, target_mult=5.0
# Short stop: H >= stop_price (110)
# Short target: L <= entry - target_mult * ATR
```

---

## 5. Persistence Verification

After exits, verify all three persistence artifacts:

```python
# trades.jsonl — one line per closed trade
trades_path = Path(tmpdir) / "e2e_sim" / "trades.jsonl"
with open(trades_path) as f:
    trade = json.loads(f.readline())
assert trade["exit_reason"] == "stop"
assert trade["token"] == "BTC"

# state.json — open positions reduced
state_path = Path(tmpdir) / "e2e_sim" / "state.json"
with open(state_path) as f:
    state = json.load(f)
assert len(state["open_positions"]) == expected_count

# equity.csv — row appended with MTM equity
equity_path = Path(tmpdir) / "e2e_sim" / "equity.csv"
with open(equity_path) as f:
    lines = f.readlines()
assert len(lines) >= 2  # header + at least 1 row
```

---

## 6. Gotchas & Pitfalls

| Pitfall | What goes wrong | Fix |
|---------|----------------|-----|
| Forget `breakeven_atr=0.0` | Positions exit at breakeven instead of expected stop/target | Always set `breakeven_atr=0.0` in `_make_position` unless testing breakeven |
| Expect stop_price as exit price | Assertions fail — exit price is candle close | Assert against the candle `c` value, not `stop_price` |
| Forget ATR cache | `process_sub_hourly_exits` silently skips uncached positions | Always populate `engine._cached_bar_data[(strategy_id, token)]` |
| Use token-only cache key | Multi-strategy tests get wrong ATR | Key is `(strategy_id, token)` tuple |
| CB test with default config | CB never fires — `circuit_breaker_r` defaults to 0.0 | Explicitly set `engine.config.strategies[i].circuit_breaker_r = 3.0` |
| Feed ticks without crossing boundary | No candles emitted from `flush_completed()` | Must send at least one tick in the NEXT interval to complete the current candle |
| Forget `_last_known_prices` | MTM equity uses stale prices | Happens automatically now but verify prices update even without exits |
| Touch real state files | Corrupts live paper trader | ALWAYS use `tempfile.mkdtemp()` for `state_dir` |
| Stop=entry edge case | `L <= stop_price` is `100 <= 100` = True | When testing "no exit", ensure L is strictly above stop for longs |
| exit_resolution on PaperConfig | Engine ignores it — only reads from StrategySpec | Put `exit_resolution` on each StrategySpec, not on PaperConfig |
| Tests using `__new__` bypass | `_effective_exit_resolution` AttributeError | Engine uses `getattr(self, '_effective_exit_resolution', 0)` defensively |
| Per-strategy skip | Positions with strategy exit_resolution=0 are silently skipped | Must set exit_resolution on the StrategySpec used by the test position |

---

## 7. Running the Tests

```bash
# All sub-hourly tests (unit + e2e)
pytest v4/tests/test_candle_aggregator.py v4/tests/test_paper_sub_hourly.py v4/tests/test_e2e_sub_hourly.py -v

# Just e2e simulation tests
pytest v4/tests/test_e2e_sub_hourly.py -v

# Quick sanity (all v4 tests)
pytest v4/tests/ -x -q
```

---

## 8. Exit Resolution Sweep Results (2026-03-21)

3-month lookback, $200K capital. Sweep tool: `sweep_exit_resolution.py`.

| Strategy | Hourly Sharpe | 5m | 15m | 30m | Best | Trades |
|----------|:---:|:---:|:---:|:---:|:---:|:---:|
| s56 | -1.24 | **-1.10** | -1.16 | -1.25 | 5m (+0.14) | 306 |
| s57 | **-6.46** | -6.47 | -6.53 | -6.50 | hourly | 324 |
| s62 | **-2.98** | — | — | — | hourly | 625 |
| s65 | **-2.18** | — | — | — | hourly | 598 |
| s72 | **-2.18** | — | — | — | hourly | 598 |
| s98 | 1.51 | 1.55 | 1.53 | **1.56** | 30m (+0.05) | 26 |
| s106 | 1.51 | **1.55** | 1.53 | — | 5m (+0.04) | 26 |
| s107 | — | — | — | — | 5m (mirrors s106) | — |

**Key findings:**
- Sub-hourly exits consistently improve profitable strategies by +0.04-0.05 Sharpe
- For losing strategies (Sharpe < -1), sub-hourly is noise at best
- 5m and 30m both work well; 30m has lowest operational overhead
- High-trade strategies (600+ trades) are extremely slow to backtest at sub-hourly resolution due to per-bar parquet I/O

---

## 9. Adding New Exit Types

If adding a new exit type to `check_candle_exits()`:

1. Add the logic in `v4/minute_exits.py:check_candle_exits()` — this is the single source of truth for both backtest and paper trader
2. Add a backtest test in `v4/tests/test_minute_exits.py`
3. Add a paper unit test in `v4/tests/test_paper_sub_hourly.py`
4. Add an e2e simulation test in `v4/tests/test_e2e_sub_hourly.py` following the patterns above
5. The exit price should be candle close `c` (consistent with all other exit types)
6. Remember exit priority: CB > stop > target > new_type (or adjust as needed)
