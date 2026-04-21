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
├── data/                         # RAW DATA — NEVER MODIFY (~500MB+, gitignored)
│   ├── perp/1h_cache/            # 199 tokens perp 1H parquets (PRIMARY)
│   ├── perp/1m_cache/            # 193 tokens perp 1M parquets (live entry resolution)
│   ├── spot/1h_cache/            # 135 tokens spot 1H parquets
│   ├── perp/live/, spot/live/    # Live buffer (merged at read time)
│   └── perp/binance/, kraken/, hyperliquid/  # Raw CSVs per exchange
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

## Multi-Portfolio Paper Runner (`run_paper_multi.py`)

Runs N independent portfolios in a single process. Config: `configs/multi_v4_paper.json`.

```bash
python -m v4.run_paper_multi --config configs/multi_v4_paper.json
python -m v4.run_paper_multi --config configs/multi_v4_paper.json --once   # single tick
python -m v4.run_paper_multi --config configs/multi_v4_paper.json --status # show status
```

### Shared WebSocket Architecture

One `PriceMonitor` per unique `(exchange, venue)` pair serves all sub-hourly engines:

- **`_PriceFanOut`** routes each price update from the shared monitor to N `CandleAggregator` callbacks with per-callback exception isolation
- **Venue mapping**: `"perp"` and `"combined"` strategies → venue `"perp"`, `"spot"` strategies → venue `"spot"`
- **Engine grouping**: Only engines with `exit_resolution` (sub-hourly exits) join shared WebSocket groups. Hourly-only engines skip WebSocket entirely.
- **Lazy connect**: Shared monitors connect when first tokens appear (handles cold start with no positions)
- **Per-pool opt-out**: Set `"dedicated_ws": true` on a portfolio for its own isolated PriceMonitor

### State Restoration on Startup (`paper_utils.py`)

`restore_state()` runs on every startup:
1. Restores tick_counter, positions, equity, last_timestamp from `state.json`
2. **Recovery truncation**: Removes trades.jsonl / equity.csv entries after restored tick
3. **Grace period adjustment**: If downtime > 1.5 hours, bumps `tick_counter` by missed hours so `bars_held` (used by `no_stop_bars`, trail tightening, `max_hold`) reflects real elapsed time. The bumped state is persisted immediately to prevent double-bumping on repeated crash-restarts.
4. Seeds `_flushed_position_ids` from trades.jsonl to prevent duplicate writes
5. **PID lock**: `acquire_pid_lock()` prevents duplicate instances via `fcntl.flock`

### Operational Notes

- **Memory**: ~850-900MB RSS for 8 portfolios with ~325 tokens
- **Tick duration**: ~55-65s total across 8 portfolios (data fetch shared, ticks sequential)
- **Known symbol warnings**: BONK, FLOKI, RATS, PEPE, SHIB perpetual symbols not available on Binance — these are expected warnings, not errors

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

---

# v5 Architecture Addendum (M10 — Final Milestone)

## Trade Identity Model (FIX-aligned)

v5 tracks every fill, scale, and exit as a **lineage tree** rooted at the
original entry's `parent_position_id`. Each child trade (scale, partial
fill, linked-leg propagation, exit) carries its position in the tree
via a small set of immutable identity fields that map one-to-one onto
FIX protocol fields.

### Core identity fields (on `Position`, `ClosedTrade`, `ScalingEvent`)

| v5 field | Type | FIX analog | Semantics |
|---|---|---|---|
| `parent_position_id` | `str` | `OrderID(37)` | Immutable join key. All rows of a lineage share this value. |
| `exec_seq` | `int` | `ExecID(17)` | Monotonic per `parent_position_id`. 0 = open, increments on scale/reduce/exit. |
| `exec_type` | `str` | `ExecType(150)` | One of: `"reduce"`, `"exit"`, `"linked_reduce"`. RESERVED values: `"open"`, `"increase"`. |
| `is_terminal` | `bool` | `OrdStatus(39)=Filled` | Exactly one `ClosedTrade` per `parent_position_id` has `True`. |
| `triggered_by` | `str` | `ExecRestatementReason(378)` | Set ONLY by linked-leg auto-propagation. Empty string for operator-initiated. |
| `has_scaling` | `bool` | (v5-specific) | True on any `ClosedTrade` whose position recorded prior `ScalingEvent`s. |

### Lineage diagram

```
Open: parent_position_id=BTC:s524m:42:primary  exec_seq=0  exec_type=(reserved "open")
 │
 ├── Scale-in: exec_seq=1  exec_type="increase"  (RESERVED — M11+)
 ├── Partial reduce: exec_seq=2  exec_type="reduce"
 ├── Linked propagation to BTC:s524m:42:secondary: exec_seq=3  exec_type="linked_reduce"  triggered_by=BTC:s524m:42:primary
 └── Final exit: exec_seq=N  exec_type="exit"  is_terminal=True
```

The invariant: every `parent_position_id` has exactly ONE row with
`is_terminal=True`. Partial reductions are non-terminal. Linked-leg
auto-propagation produces rows whose `triggered_by` points to the
primary leg's `position_id`.

### ScalingEvent schema (pre-terminal events)

```python
@dataclass(slots=True)
class ScalingEvent:
    position_id: str           # which position scaled
    parent_position_id: str    # lineage root
    exec_seq: int              # monotonic sequence
    bar: int                   # when
    delta_size: float          # signed; negative = reduce
    delta_margin_usd: float
    exec_type: str             # "reduce" | "linked_reduce"
    triggered_by: str          # position_id that caused the auto-propagation, else ""
```

### ClosedTrade lineage rules

- `exec_seq == 0` implies the trade is a one-shot entry-to-exit with no scales.
- `has_scaling == True` implies at least one `ScalingEvent` was recorded
  against this position before closure.
- `is_terminal == False` implies a partial-reduce row (closed portion of
  a still-open position). Partial-reduce rows persist in
  `ClosedTrade`-log form for reporting; the parent `Position` remains open.
- `triggered_by != ""` implies a linked-leg propagation. Primary leg
  closure triggers secondary leg `linked_reduce` with
  `triggered_by = <primary.position_id>`.

### FIX field mapping table

| FIX tag | FIX field | v5 equivalent |
|---|---|---|
| 37 | OrderID | `parent_position_id` |
| 17 | ExecID | `f"{parent_position_id}#{exec_seq}"` |
| 150 | ExecType | `exec_type` |
| 39 | OrdStatus | `"Filled"` if `is_terminal` else `"PartiallyFilled"` |
| 378 | ExecRestatementReason | `triggered_by` (non-empty → auto-propagated) |
| 654 | LegRefID | `Leg.leg_ref_id` (for multi-leg orders) |
| 587 | LegSettlType | `Leg.settlement_type` ∈ {"spot","perp","futures"} |
| 1098 | StrategyID | `ClosedTrade.strategy_id` |

---

## TickCadencePolicy Wiring (M9 deliverable)

**Protocol** (in `v5/sizing/tick_cadence.py`): `TickCadencePolicy.sample()`
returns `"bar_close" | "tick" | "release"` per policy. Injected at
`PortfolioConfig.tick_cadence_policy`. Consumed in
`v5/simulator.py::_stage2_process_new_signals` via `sampling_cadence`
discipline — ensures AC-Sz9 parity (the same tick policy is honored on
both backtest and paper paths).

Default policy: `BarCloseCadence` (equivalent to M7 behavior — decide at
bar_close only). Alternative policies: `TickCadence` (decide on every
tick, used by `paper_engine` under TestClock-accelerated tests),
`ReleaseCadence` (decide on trigger release, used by stop-armed entries).

Bridge wiring path: `config.tick_cadence_policy.sample(ctx, bar)` is
called before `apply_arbitration`. If the policy returns `"tick"`
semantics during a bar_close context, the bar is deferred. See
`.specs/done/m9-cleanups/brief.md` C-10 for the M9 spec.

---

## M8→M9 Test-Dispute Tally

During M8 + M9 implementation, 7 tests were revised via the
test-dispute protocol. Telemetry at `.specs/telemetry.jsonl`.

| # | Test | Root cause | Resolution |
|---|---|---|---|
| 1 | `test_allocation_state_fields` (M9) | M8 asserted exactly 4 fields; M9 added `market_snapshot` | Relaxed to "at least these 4 core fields" |
| 2 | `test_m8_site5_slippage` (M8) | Slippage bound on `adv_cap=0.005` differed from 0.010 baseline | Parameterized by adv_cap; added per-cap tolerance |
| 3 | `test_paper_state_v2_round_trip` (M7) | Removed `regime` field broke deserialization | Migration path: strip on load + emit on persist with None default |
| 4 | `test_backoff_constant` (M7) | Anchor-test required `BACKOFF == [30, 60, 120]` | Kept as regression lock (legitimate constant anchor) |
| 5 | `test_ac_s10_xfail_flip` (M9) | xfail strict passes on any non-error return | M10 tightens with positive-assertion guards (AC #10) |
| 6 | `test_name_not_importable[get_sizing_model]` (M8) | M8 banned symbol; M10 B11 re-admits via real import | Removed from M8 banned list (M10 2026-04-21) |
| 7 | `test_name_not_importable[KellySizing]` (M8) | Substring regex false-matched `_LegacyKellySizing` | Tightened to word-boundary regex (M10 2026-04-21) |

Policy: future test-disputes are logged to `.specs/telemetry.jsonl`
with `event: "test_dispute_resolved"` and must name the resolver +
resolution reason.
