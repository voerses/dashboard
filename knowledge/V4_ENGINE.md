# V4 Portfolio Backtest Engine

> **TL;DR** V4 is a portfolio-level simulator that runs multiple strategies in a shared capital pool with realistic constraints (concentration limits, ADV caps, partial fills, slippage). It replaces V3's per-token validation with end-to-end portfolio simulation. Paper trading uses the same simulator code with live data fetching.

**Last updated:** 2026-03-10

---

## Architecture

```
v4/
├── config.py                # PortfolioConfig, StrategySpec dataclasses
├── signals.py               # precompute_strategy_signals(), discover_tokens()
├── simulator.py             # simulate_portfolio(), build_unified_index()
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
2. **Alignment** (combined strategies): `index.intersection()` for bar-for-bar spot/perp alignment
3. **Context building**: `Engine._build_context()` computes indicators (EMA, RSI, ADX, ATR, etc.)
4. **Strategy call**: 1-arg (single market) or 2-arg (spot+perp combined)
5. **Walk-forward mask**: `entry_mask[0:train_bars] = False` — training data cannot generate entries
6. **Array extraction**: Close, high, low, ATR, entry_mask, direction, stop/trail params → `TokenSignals`

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

### Paper Trading (`paper_engine.py`, `run_paper.py`)

Same simulator code, but:
- **Per-tick execution**: `PaperPortfolioEngine.tick()` runs one bar per hour
- **Live data**: `LiveFetcher` fetches OHLCV via ccxt, appends to parquet cache
- **State persistence**: JSON state file with open positions, equity, tick counter
- **Per-bar RNG**: `RandomState(seed + tick_counter)` for deterministic restartability
- **Live dashboard**: Writes `/srv/data/state.json` every 1s with live WebSocket prices. Dashboard at `/srv/dashboard/current/` polls via fetch. Loads full trade history from `trades.jsonl` and equity from `equity.csv`.

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

### Out-of-Sample Test (train→Dec, trade Jan-Mar)
```python
# Adjust train_bars to push the walk-forward mask to Jan 1
trade_start_raw = data_end - pd.DateOffset(months=3)
extra = int((pd.Timestamp('2026-01-01') - trade_start_raw).total_seconds() / 3600)
config.train_bars = 8760 + extra  # 365 days + gap to Jan 1
# Then run with months=3
```

---

## Key Config Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `train_bars` | 8760 (365d) | Walk-forward training window (masked, no trades) |
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
