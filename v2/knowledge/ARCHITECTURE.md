# System Architecture

## Directory Layout
```
v2/
├── knowledge/                    # KNOWLEDGE BASE — findings, results, data docs
│   ├── STRATEGY_RESULTS.md       # Master strategy comparison & findings
│   ├── DATA_MANIFEST.md          # Data file inventory (DO NOT DELETE DATA)
│   └── ARCHITECTURE.md           # This file
│
├── strategies/                   # STRATEGY LIBRARY — one file per strategy
│   ├── __init__.py               # Registry of all strategies
│   ├── TEMPLATE.py               # Copy this to create a new strategy
│   ├── README.md                 # How to add strategies
│   ├── s07_rsi_bounce.py         # Example: RSI bounce (EXPERIMENTAL)
│   └── s08_obv_divergence.py     # Example: OBV divergence (EXPERIMENTAL)
│
├── engine.py            # PLUGIN ENGINE — generic, extensible framework
│   │                             # StrategyContext, StrategyResult, Engine class
│   │                             # Indicator plugin system, auto-comparison
│
├── results/                      # LOGGED RESULTS — auto-generated JSON per test run
│
├── real_data/                    # RAW DATA — NEVER MODIFY
│   ├── 1h_cache/                 # 49 tokens × 18K bars each (PRIMARY)
│   ├── 4h_cache/                 # 49 tokens × 4.5K bars each
│   ├── 1m_cache/                 # 49 tokens daily microstructure features
│   ├── all_tokens_enriched.parquet  # 57 tokens × 20 features
│   └── *_daily.csv               # 57 tokens raw daily OHLCV
│
├── mtf_strategy_v2.py            # CORE ENGINE — DO NOT OVERWRITE
│   │                             # 3-tier MTF backtest (1H:4H:Daily)
│   │                             # Numba-JIT simulation, indicators, regime detection
│   │                             # Built-in strategies: dual_momentum, vol_breakout, mean_reversion
│
├── cpcv.py                       # CPCV validation framework
├── liquid_universe.py            # Token tiers & position sizing
│
├── run_test_suite.py             # Legacy test runner (uses mtf_strategy_v2 directly)
├── strategy_comparison.py        # Legacy comparison (V2/V3/VPIN/fat-tail)
│
├── strategies.py                 # Prior V2 strategies (reference only)
├── strategies_v3.py              # Prior V3 strategies (reference only)
├── signal_lab.py                 # Signal IC evaluation — 37 signals
├── walk_forward_fast.py          # Walk-forward optimization engine
├── regime_detector.py            # HMM + BOCPD + VPIN regime detection
├── mtf_swing_strategy.py         # V1 MTF swing (reference only)
│
├── fetch_1m_data.py              # Data download from Binance Vision
├── real_data_fetcher.py          # Alternative data fetcher
├── live_scanner.py               # Live Binance market scanner
│
├── outputs_v2/                   # Prior run outputs (CSVs, PNGs, JSONs)
└── dashboard.html                # Visualization dashboard
```

## Protected Files — DO NOT OVERWRITE
1. **`mtf_strategy_v2.py`** — Core backtesting engine with Numba JIT simulation
2. **`engine.py`** — Plugin architecture (StrategyContext, Engine)
3. **`cpcv.py`** — CPCV validation framework
4. **`liquid_universe.py`** — Token universe & position sizing rules
5. **Everything in `real_data/`** — Raw data, never modify

## How to Write a New Strategy (3 steps)

### Step 1: Copy the template
```bash
cp strategies/TEMPLATE.py strategies/s09_my_idea.py
```

### Step 2: Edit the strategy() function
```python
def strategy(ctx: StrategyContext) -> StrategyResult:
    n = len(ctx.ind_1h['close'])

    # Your entry logic — use any indicators available in ctx
    entry = ctx.ind_1h['rsi'] < 30  # example

    return StrategyResult(
        entry_mask=entry,
        direction=np.ones(n, dtype=np.int8),
        stop_mult=3.0, trail_mult=3.0, ...
        name='my_idea',
    )
```

### Step 3: Test it
```python
from engine import Engine, strategy_dual_momentum, CPCV_ROBUST_TOKENS
from strategies.s09_my_idea import strategy as my_idea

engine = Engine()

# Quick test on CPCV tokens
engine.run(my_idea, tokens=CPCV_ROBUST_TOKENS)

# Compare against the proven baseline
engine.compare(
    strategies=[strategy_dual_momentum, my_idea],
    labels=['Dual Momentum (baseline)', 'My Idea'],
    tokens=CPCV_ROBUST_TOKENS,
)
```

## What's Available in StrategyContext

### Base Indicators (all timeframes: ind_1h, ind_4h, ind_d)
```
close, high, low, volume, ema_10, ema_20, ema_50,
macd, macd_signal, macd_hist, rsi, bb_upper, bb_lower,
bb_width, bb_pct, atr, adx, plus_di, minus_di,
vol_ratio, ret_1, vol_20, donch_high, donch_low, taker
```

### Custom Indicators (auto-computed, in ctx.custom)
```
obv, obv_slope                              # On-Balance Volume
vwap_20, vwap_dev                           # Rolling VWAP
ret_6h, ret_12h, ret_24h, ret_48h, ret_120h  # Multi-period returns (1H)
ret_5d, ret_10d, ret_20d, ret_60d           # Multi-period returns (daily→1H)
enr_vpin, enr_realized_vol, enr_taker_buy_ratio,  # Enriched features (daily→1H)
enr_amihud_1m, enr_vwap_deviation, enr_intraday_skew, enr_parkinson_vol
```

### Helpers
```python
ctx.align_daily_to_1h(daily_array)   # Forward-fill daily → 1H
ctx.align_4h_to_1h(h4_array)        # Forward-fill 4H → 1H
ctx.enriched                         # Raw enriched DataFrame (or None)
ctx.df_1h / ctx.df_4h / ctx.df_daily # Raw DataFrames for custom work
ctx.regime_1h                        # 0=crisis, 1=quiet, 2=uptrend, 3=range, 4=downtrend
ctx.tier                             # 1, 2, or 3 (liquidity tier)
```

### Adding New Custom Indicators
```python
from engine import register_indicator, StrategyContext

@register_indicator
def my_custom_indicator(ctx: StrategyContext):
    close = ctx.ind_1h['close']
    ctx.custom['my_signal'] = ...  # your computation
```

## Key Parameters (Current Best: S11 Momentum Burst +$62,705/yr on CPCV tokens)
```python
# S11 Momentum Burst (best validated strategy)
stop_mult = 3.0        # 3x ATR initial stop (tighter = better, from sweep)
trail_mult = 3.0       # 3x ATR trailing stop
no_stop_bars = 24      # 24-bar protection window (biggest single lever: +$9K/yr)
edge = 0.40            # Kelly edge assumption
min_hold = 18          # 18 hours minimum hold
max_hold = 720         # 30 days maximum hold

# Dual-validated tokens (pass BOTH CPCV + Walk-Forward):
# S11: PENGU, SUI, AVAX, BONK, FLOKI, ZRO
# S09: SUI, TRX, BONK, FLOKI
# Triple-validated core: SUI, BONK, FLOKI
```

## Validation Pipeline
```python
engine = Engine()
# Runs: backtest → CPCV (per token PBO) → Walk-Forward (60/40 split)
# Only deploys tokens passing BOTH gates (PBO < 40% AND OOS profitable)
result = engine.validate(strategy_fn, tokens=CPCV_ROBUST_TOKENS)
print(result['validated_tokens'])  # Only the robust ones
```

## Performance Benchmarks (with Numba JIT)
- Single token (BTC): **25ms**
- 49 tokens × 3 strategies: **1.38s**
- 49 tokens × DM only: **1.07s**
- 11 CPCV × DM only: **0.23s**
- CPCV (49 tokens × 15 folds): ~8s with 4 workers
- First run includes ~0.9s JIT warmup (cached after)
