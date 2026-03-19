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

## Real-Time Exit Sentinel

The Exit Sentinel is a standalone process that monitors real-time prices via Binance WebSocket and detects stop-loss breaches between hourly ticks. It runs alongside the paper trading engine.

### Process Model

```
Paper Engine (hourly tick)           Sentinel (continuous)
─────────────────────────           ─────────────────────
1. Fetch 1H candle close            1. Read stops.json (per portfolio)
2. Run strategy signals              2. Connect Binance WS (perp + spot)
3. Process entries/exits             3. Monitor mark prices every ~1s
4. Write stops.json  ───────────▶   4. Detect breaches + confirm
5. Update state.json                 5. Write sentinel_recent.json
6. Push dashboard                    6. Write sentinel_shadow.jsonl
                                     7. (Live mode) Write exit_events.jsonl
                                          │
                          ◀───────────────┘
                     (next tick: paper engine reads exit_events.jsonl)
```

- **Paper engine** writes `stops.json` per portfolio every hourly tick (when `sentinel_mode != "off"`). Contains StopLevel objects for each open position: stop_price, entry_price, direction, cb_price, target_price, estimated_liq_price, stop_active flag, liquidity tier.
- **Sentinel** reads stops.json, subscribes to Binance WebSocket streams for all tokens across all portfolios, and checks prices against stop levels continuously.
- **Order matters:** Start paper engine first (it creates stops.json), then sentinel (reads them).

### Dual-Venue Monitoring

The sentinel runs two independent PriceMonitor instances:

| Venue | WebSocket Stream | Use Case |
|-------|-----------------|----------|
| **Perp** | `@markPrice@1s` (Binance Futures) | Perpetual contract positions (majority of strategies) |
| **Spot** | `@miniTicker` (Binance Spot) | Spot positions (s57 combined strategy) |

Each PriceMonitor maintains its own WebSocket connection with automatic reconnection (exponential backoff) and REST API fallback when WS is unavailable. The `is_perp` flag on each StopLevel determines which venue's price feed is used for breach detection.

### Breach Detection Flow

```
Price tick arrives
  │
  ▼
For each portfolio's stop levels:
  │
  ├─ Filter: skip if stop_active == False (bars_held < no_stop_bars and not convex_exit)
  │
  ├─ Trail tightening: max(stop_price, highest - trail_mult * cur_atr) for simple trail
  │
  ├─ Stop check: price <= stop_price (long) or price >= stop_price (short)
  │   Also checks: CB breach (price vs cb_price), target hit, liquidation proximity
  │
  ├─ If breached → start confirmation timer (per liquidity tier):
  │     BTC/ETH: 30s, Top-20: 60s, Others: 90s
  │
  ├─ During confirmation: wick filtering
  │     If price recovers above stop → reject (wick, not real breach)
  │
  └─ After timer expires with sustained breach → CONFIRMED
       │
       ├─ Write to sentinel_recent.json (ring buffer, last 50)
       ├─ Write to sentinel_shadow.jsonl (append-only full log)
       └─ (Live mode only) Write ExitEvent to exit_events.jsonl
```

### Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| **stop_active gating** | Respects the strategy's `no_stop_bars` grace period. Only checks stops when `bars_held >= no_stop_bars` or `convex_exit` is triggered. ~70% of positions have stop==entry during grace period due to breakeven ratchet. |
| **breakeven_atr=0.5 default** | After trade reaches +0.5 ATR profit, stop moves to entry price. Most positions in grace period show stop==entry. This is expected behavior, not a bug. |
| **Per-tier confirmation timers** | Higher-liquidity tokens (BTC/ETH) confirm faster because their prices are less noisy. Lower-liquidity tokens need longer confirmation to avoid false triggers from spread noise. |
| **Wick filtering** | Rejects breaches where price recovers above stop during the confirmation window. Prevents acting on temporary wicks that the hourly engine would have ignored. |
| **Shadow mode first** | Logs all breach detections without acting on them. Allows weeks of observation to compare sentinel exits vs hourly exits before enabling automatic exits. |
| **PID lock file** | `state/sentinel.pid` prevents duplicate sentinel processes. SIGINT/SIGTERM handled for clean shutdown. |

### File Layout (per portfolio state directory)

```
state/v4_paper/{portfolio_name}/
├── state.json              # Paper engine state (positions, equity, trades)
├── stops.json              # Written by paper engine: current stop levels
├── sentinel_recent.json    # Written by sentinel: last 50 breach events
├── sentinel_shadow.jsonl   # Written by sentinel: full breach log
├── sentinel_heartbeat.json # Written by sentinel: connection status (every 60s)
├── sentinel_metrics.json   # Written by sentinel: performance metrics (every 5 min)
└── exit_events.jsonl       # Written by sentinel (live mode): exits for paper engine
```

### Source Files

| File | Purpose |
|------|---------|
| `v4/stop_store.py` | StopLevel/ExitEvent dataclasses, atomic JSON read/write |
| `v4/price_monitor.py` | Binance WS connection (`@markPrice@1s` for perp, `@miniTicker` for spot), REST fallback, auto-reconnect, venue parameter |
| `v4/breach_detector.py` | Per-portfolio breach detection with confirmation timers, wick filtering, trail tightening, shadow logging |
| `v4/sentinel_metrics.py` | Metrics collection, heartbeat, state consistency checks |
| `v4/run_sentinel.py` | CLI entry point with PID lock, SIGINT/SIGTERM, dual-venue monitoring |
| `v4/paper_engine.py` | `_extract_stop_levels()` and `_process_sentinel_exits()` integration points |
| `tools/measure_stop_breach.py` | Historical analysis of delayed-stop cost (Phase 0 measurement) |
| `tools/sentinel_shadow_report.py` | Joins sentinel events with trades for TP/FT/PRE classification |
