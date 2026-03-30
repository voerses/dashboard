# V4 Portfolio Backtest Engine

> **TL;DR** V4 is a portfolio-level simulator that runs multiple strategies in a shared capital pool with realistic constraints (concentration limits, ADV caps, partial fills, slippage). It replaces V3's per-token validation with end-to-end portfolio simulation. Paper trading uses the same simulator code with live data fetching.

**Last updated:** 2026-03-30

---

## Architecture

```
v4/
├── config.py                # PortfolioConfig, StrategySpec dataclasses
├── signals.py               # precompute_strategy_signals(), discover_tokens()
├── portfolio_signals.py     # precompute_portfolio_signals(), Class B dispatch
├── walk_forward.py          # WalkForwardWindow, compute_wf_windows(), BacktestManifest
├── simulator.py             # simulate_portfolio(), build_unified_index()
├── metrics.py               # PerformanceMetrics, deflated_sharpe_ratio(), walk_forward_efficiency()
├── paper_engine.py          # PaperPortfolioEngine (live tick-by-tick)
├── live_fetcher.py          # LiveFetcher (ccxt → parquet append)
├── run_paper.py             # Continuous hourly loop with dashboard push
├── report.py                # compute_portfolio_metrics(), print_report()
├── portfolio_backtest.py    # CLI entry point
└── tests/                   # test_paper_engine.py, test_paper_determinism.py, etc.
```

### Data Flow

```
Parquet cache (data/spot/1h_cache/, data/perp/1h_cache/)
  ↓
precompute_strategy_signals()    # loads data, builds context, calls strategy fn
  ↓                              # applies walk-forward mask, extracts arrays
TokenSignals per token           # close, high, low, atr, entry_mask, direction, etc.
  ↓
build_unified_index()            # merges timestamps across all strategies/tokens
  ↓
simulate_portfolio()             # processes exits then entries each bar
  ↓                              # shared capital pool, position manager, constraints
SimulationState                  # closed_trades, open_positions, equity_curve
  ↓
compute_portfolio_metrics()      # Sharpe, Sortino, Calmar, drawdown, etc.
```

### Signal Pipeline (`signals.py`)

`precompute_strategy_signals(spec, tokens, config, months, end_date)`:

1. **Data loading**: Reads parquet, trims to `[load_from, end_date]` with 180-day warmup
2. **Hard data cap** (OOS integrity): `df = df[df.index <= end_date]` applied BEFORE `_build_context()` — prevents any future data from leaking into indicator computation
3. **Alignment** (combined strategies): `index.intersection()` for bar-for-bar spot/perp alignment
4. **Context building**: `Engine._build_context()` computes indicators (EMA, RSI, ADX, ATR, etc.)
5. **Strategy call**: 1-arg (single market) or 2-arg (spot+perp combined)
6. **Walk-forward mask**: `entry_mask[0:train_bars] = False` — training data cannot generate entries
7. **Array extraction**: Close, high, low, ATR, entry_mask, direction, stop/trail params → `TokenSignals`

The hard data cap is applied in both `_load_all_contexts()` (portfolio path) and `precompute_strategy_signals()` (per-token path). This is the primary defense against OOS data bleed-forward.

### True Walk-Forward (`portfolio_signals.py`, opt-in)

When `PortfolioConfig.true_walk_forward=True`, signals are recomputed per OOS window instead of using a single-pass walk-forward mask. This is for non-causal strategies where indicators might leak future information.

`_precompute_true_walk_forward(strategy_spec, tokens, config, months, end_date, strategy_fn)`:

1. **Phase A**: Load raw DataFrames once per token (amortized I/O)
2. **Phase B**: Compute window schedule via `compute_wf_windows()`, resolve consistent universe (tokens with sufficient data at every window's `data_cap`)
3. **Phase C**: Per-window loop — slice data at `data_cap`, build context, call strategy, extract OOS segment
4. **Phase D**: Concatenate OOS segments into `TokenSignals` per token

Memory management: `gc.collect()` between windows. Performance ceiling: warns if total time exceeds 3x single-pass.

### Walk-Forward Window Module (`walk_forward.py`)

Pure functions for computing walk-forward schedules:

```python
from v4.walk_forward import compute_wf_windows, WalkForwardWindow

windows = compute_wf_windows(
    n_bars=17520,      # 2 years of hourly data
    train_bars=8760,   # 1 year training
    recal_bars=2160,   # 90 days OOS
    purge_bars=168,    # 7-day purge gap
    scheme="rolling",  # or "expanding"
)
# Returns: [WalkForwardWindow(window_idx, data_cap, oos_start, oos_end), ...]
```

Also provides `BacktestManifest` for reproducibility logging and `compute_config_hash()` for deterministic result verification.

### True OOS Monthly Runner (`run_oos_monthly.py`)

Runs a proper month-by-month OOS backtest with zero forward-looking bias:

```bash
python run_oos_monthly.py
```

For each month, signals are recomputed from scratch with data capped at that month's end. Each month's signal computation cannot see any data beyond its own end date. This is the gold standard for evaluating strategy performance without look-ahead bias.

### Simulator (`simulator.py`)

Each bar processes in order:
1. **Funding** (perp positions): deducted from equity bar-by-bar
2. **Exits**: stop, trail, target, regime, RSI, mean-target, max-hold, liquidation
3. **Entries**: size calculation → constraint checks → slippage → position creation

**Constraint chain** (entries checked in order):
1. Strategy position limit (max_positions per strategy)
2. Portfolio position limit (max_portfolio_positions)
3. Minimum position size (min_position_usd)
4. ADV cap (adv_cap_pct of rolling ADV)
5. Concentration limit (max % of equity per token)
6. Free capital (scale down if insufficient, with fee budget)

**Slippage model**: `compute_slippage_bps(notional, adv, base_spread_bps, impact_coeff, max_slip_bps)`
- Base spread + square-root market impact + cap

### Paper Trading (`paper_engine.py`, `run_paper.py`, `run_paper_multi.py`)

Same simulator code, but:
- **Per-tick execution**: `PaperPortfolioEngine.tick()` runs one bar per hour
- **Live data**: `LiveFetcher` fetches OHLCV via ccxt, appends to parquet cache
- **State persistence**: JSON state file with open positions, equity, tick counter
- **Per-bar RNG**: `RandomState(seed + tick_counter)` for deterministic restartability
- **Live dashboard**: Writes `/srv/data/state.json` every 1s with live WebSocket prices. Dashboard at `/srv/dashboard/current/` polls via fetch. Loads full trade history from `trades.jsonl` and equity from `equity.csv`.

#### Multi-Portfolio Runner (`run_paper_multi.py`)

Runs N independent portfolios in a single process with shared infrastructure:

- **Shared WebSocket**: One `PriceMonitor` per unique `(exchange, venue)` pair, with `_PriceFanOut` routing price updates to all engines' `CandleAggregator` callbacks. Reduces N WebSocket connections to 1 per venue.
- **Per-pool opt-out**: Set `"dedicated_ws": true` in a portfolio's config to give it its own `PriceMonitor` (useful for isolation/debugging).
- **Engine grouping**: Only engines with sub-hourly `exit_resolution` are grouped for shared WebSocket. Hourly-only engines skip WebSocket entirely.
- **Lazy connect**: Shared monitors connect on first subscription update when tokens exist, not at boot (handles cold start with no positions).

#### State Restoration (`paper_utils.py`)

On startup, `restore_state()` restores engine state from `state.json`:

- **tick_counter, positions, equity, last_timestamp** all restored
- **Recovery truncation**: trades.jsonl and equity.csv entries after the restored tick are removed
- **Flushed position IDs**: Seeded from existing trades.jsonl to prevent duplicate writes
- **Grace period adjustment**: If `last_timestamp` indicates a gap > 1.5 hours, `tick_counter` is advanced by the number of missed hours so that time-based exit logic (`no_stop_bars`, trail tightening, `max_hold`) reflects real elapsed wall-clock time. The bumped tick_counter and updated last_timestamp are persisted to state.json immediately to prevent double-bumping on repeated restarts.
- **PID lock**: `acquire_pid_lock()` prevents duplicate instances via `fcntl.flock`
- **Strategy reconciliation** (independent mode): Orphaned strategies are force-closed, new strategies initialized, equity redistributed

---

## How to Run Backtests

### CLI
```bash
python v4/portfolio_backtest.py --strategy s56,s57 --months 12 --exchange binance --capital 200000
```

### Python (per-strategy market control)
```python
from v4.config import PortfolioConfig, StrategySpec
from v4.signals import precompute_strategy_signals, discover_tokens, infer_data_end_date
from v4.simulator import simulate_portfolio
from v4.report import compute_portfolio_metrics

strategy_specs = {
    's56': StrategySpec(strategy_id='s56', weight=1.0, max_positions=15, market='perp'),
    's57': StrategySpec(strategy_id='s57', weight=1.0, max_positions=15, market='combined'),
}

config = PortfolioConfig(
    strategies=list(strategy_specs.values()),
    capital=200_000, exchange='binance',
    concentration_limit=1.0, adv_cap_pct=0.05,
)

data_end = infer_data_end_date('combined')
all_signals = {}
for sid, spec in strategy_specs.items():
    tokens = discover_tokens(spec.market)
    all_signals[sid] = precompute_strategy_signals(spec, tokens, config, 12, end_date=data_end)

state = simulate_portfolio(all_signals, strategy_specs, config)
metrics, extra_info, eq_daily = compute_portfolio_metrics(state, 200_000)
```

### Out-of-Sample Test (hard data cap — recommended)
```python
# The end_date parameter caps ALL data before indicator computation.
# No future data leaks into rolling indicators (EMA, RSI, ATR, etc.)
data_end = pd.Timestamp('2026-01-01')  # cap at this date
all_signals = precompute_strategy_signals(spec, tokens, config, months=12, end_date=data_end)
```

### True OOS Monthly Backtest (gold standard)
```bash
# Recomputes signals for each month with data capped at that month's end.
# Each month sees ONLY data available up to its cap date. ~90s for 12 months.
python run_oos_monthly.py
```

### Legacy OOS Test (train_bars manipulation — deprecated)
```python
# Old approach: adjust train_bars to push the walk-forward mask.
# This does NOT prevent indicator leakage — use end_date cap instead.
trade_start_raw = data_end - pd.DateOffset(months=3)
extra = int((pd.Timestamp('2026-01-01') - trade_start_raw).total_seconds() / 3600)
config.train_bars = 8760 + extra  # 365 days + gap to Jan 1
```

---

## Key Config Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `train_bars` | 8760 (365d) | Walk-forward training window (masked, no trades) |
| `purge_bars` | 168 (7d) | Purge gap between training and OOS (prevents data leakage) |
| `true_walk_forward` | False | Opt-in per-window signal recomputation (Class B only) |
| `concentration_limit` | 0.10 | Max fraction of equity per token |
| `adv_cap_pct` | 0.05 | Max 5% of rolling ADV per position |
| `min_position_usd` | 200 | Minimum position size |
| `base_spread_bps` | 3.0 | Base spread for slippage model |
| `impact_coeff` | 0.03 | Square-root impact coefficient |
| `max_slip_bps` | 300 | Maximum slippage cap |
| `seed` | 42 | RNG seed for entry ordering |

---

## V4 vs V3 Differences

| Aspect | V3 | V4 |
|--------|----|----|
| **Scope** | Per-token WF+CPCV validation | Portfolio-level simulation |
| **Capital** | Unlimited per token | Shared pool with constraints |
| **Strategies** | One at a time | Multiple simultaneous |
| **Sizing** | Fixed % or ADV-based | ADV + concentration + Kelly + partial fills |
| **Slippage** | Tier-based flat cost | Square-root market impact model |
| **Walk-forward** | Per-token mask in validation | Global mask in signal precomputation |
| **Validation metric** | Token pass rate (% passing dual gate) | Portfolio return, Sharpe, drawdown |
| **Paper trading** | Separate engine (`run_paper_live.py`) | Same simulator code via `PaperPortfolioEngine` |

---

## Exchange Fee Models

Fees are looked up by exchange name in the simulator:

| Exchange | Spot Taker | Perp Taker | Funding | Liquidation Fee |
|----------|-----------|-----------|---------|----------------|
| binance | 0.10% | 0.05% | Per-bar from data | 0.50% |
| hyperliquid | 0.01% | 0.035% | Per-bar from data | 0.50% |
| kraken | 0.22% | 0.05% | Per-bar from data | 0.50% |

---

## Related Documentation

- **Sizing pipeline:** `knowledge/V4_SIZING_PIPELINE.md` — Kelly formula, ADV curve, 3 config layers, safety rails
- **Experimentation guide:** `knowledge/V4_EXPERIMENTATION_GUIDE.md` — exit handlers, custom sizing models, regime customization, raw mode, extension points, experimentation recipes
