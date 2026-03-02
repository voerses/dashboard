# System Architecture

## Directory Layout
```
crypto_backtest/
├── strategies/                   # STRATEGY LIBRARY — one file per strategy
│   ├── __init__.py               # Registry of all strategies
│   ├── TEMPLATE.py               # Copy this to create a new strategy
│   ├── README.md                 # How to add strategies
│   ├── s09_optimized_trend.py    # Tier A: EMA stack + ADX > 30
│   ├── s11_momentum_burst.py     # Tier A: ret_1 > 0.03
│   ├── s13_vol_weighted_tsmom.py # Tier A: volume-weighted cumulative returns
│   ├── s17_trend_strength_filter.py # Tier A: ret_1 > 0.02 + ADX > 25
│   ├── s18_momentum_accel.py     # Tier A: momentum acceleration
│   ├── s21_skew_momentum.py      # Tier A: rolling_skew > 0.3 + ret > 0
│   ├── s22_supertrend_adx.py     # Tier B: Supertrend flip + ADX
│   └── archive/                  # Tier C (killed) strategies
│
├── v3/                           # V3 ENGINE — current production engine
│   ├── engine.py                 # StrategyContext, StrategyResult, Engine class
│   ├── validation.py             # Combined Walk-Forward + CPCV validation
│   ├── cpcv.py                   # CPCV split generation + deflated Sharpe
│   ├── universe.py               # Token tiers & position sizing
│   └── metrics.py                # Quant metrics (Sharpe, Sortino, Calmar, etc.)
│
├── tools/                        # STANDALONE TOOLS
│   └── signal_lab.py             # Signal IC evaluation — 37 signals
│
├── data/                         # RAW DATA — NEVER MODIFY
│   ├── 1h_cache/                 # 49 tokens × 18K bars each (PRIMARY)
│   ├── 4h_cache/                 # 49 tokens × 4.5K bars each
│   ├── 1m_cache/                 # 49 tokens daily microstructure features
│   ├── all_tokens_enriched.parquet  # 57 tokens × 20 features
│   └── *_daily.csv               # 57 tokens raw daily OHLCV
│
├── results/                      # VALIDATION RESULTS — auto-generated JSON
│   ├── sweep_summary_*.json      # Tier classifications (A/B/C)
│   └── validation_sNN_*.json     # Per-strategy validation details
│
├── knowledge/                    # KNOWLEDGE BASE — findings, research, guides
│   ├── process/                  # Process docs (gates, validation, risk)
│   ├── ARCHITECTURE.md           # This file
│   ├── STRATEGY_LIFECYCLE.md     # Strategy tiers & development checklist
│   ├── INDICATOR_CATALOG.md      # Available indicators & IC values
│   └── ...                       # Signal development, performance patterns, etc.
│
├── freqtrade_bridge/             # FREQTRADE INTEGRATION
│   ├── parity_check.py           # Engine vs Freqtrade divergence checker
│   └── ...                       # Config generation, strategy shell
│
├── paper_trading/                # PAPER TRADING SYSTEM
│   ├── config_generator.py       # Freqtrade config generation
│   ├── instance_manager.py       # Instance lifecycle management
│   ├── equity_tracker.py         # P&L tracking
│   └── monitor.py                # Live monitoring
│
├── run_paper_trade.py            # CLI LAUNCHER — ties everything together
├── tests/                        # TEST SUITE (pytest)
│
├── v1-deprecated/                # V1 engine (historical reference only)
└── AIPIP/                        # Process improvement proposals
```

## Protected Files — DO NOT OVERWRITE
1. **`v3/engine.py`** — Core engine (StrategyContext, Engine, indicators)
2. **`v3/validation.py`** — Combined WF + CPCV validation pipeline
3. **`v3/universe.py`** — Token universe & position sizing rules
4. **Everything in `data/`** — Raw data, never modify

## How to Write a New Strategy (3 steps)

### Step 1: Copy the template
```bash
cp strategies/TEMPLATE.py strategies/sNN_my_idea.py
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

### Step 3: Validate it
```bash
# Quick check on BTC
python v3/validation.py --strategy sNN --tokens BTC --workers 1

# Full validation (49 tokens)
python v3/validation.py --strategy sNN --workers 4
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

## Validation Pipeline
```bash
# Runs: backtest → Walk-Forward + CPCV (dual gate) → tier assignment
python v3/validation.py --strategy sNN --workers 4
# Results written to results/validation_sNN_*.json
```

## Current Tier Classifications (2026-03-01 sweep)
| Tier | Strategies | Rate |
|------|-----------|------|
| A | s11, s09, s13, s21, s17, s18 | >50% |
| B | s20, s22, s12, s15, s14, s10 | 20-50% |
| C | s07, s08, s16, s19 | <20% (archived) |
