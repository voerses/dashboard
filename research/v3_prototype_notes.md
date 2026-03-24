# V3 Momentum Prototype Strategy -- Implementation Notes

**Date**: 2026-03-24
**File**: `research/v3_prototype_strategy.py`
**Strategy name**: `v3_momentum_overlays`

## Pattern Followed

The strategy follows the existing v4 strategy pattern established by:
- `strategies/TEMPLATE.py` -- canonical 4-layer signal stack, StrategyResult interface
- `strategies/s92_btc_trend_lev.py` -- BTC-only pattern with token filter, daily-bar computation
- `strategies/s95_weekly_ema_trend.py` -- daily EMA aligned to hourly via `align_daily_to_1h`
- `strategies/s29_funding_carry.py` -- overlay sizing via `size_multiplier` field

Key interface elements:
- `strategy(ctx: StrategyContext) -> StrategyResult` function signature
- Import from `engine` (resolves to `v4/engine.py`)
- Vectorized numpy/pandas operations (no Python for-loops over bars)
- Return `StrategyResult` with all required fields

## Strategy Architecture

```
Layer 1: Regime Filter    -- exclude CRISIS (ctx.regime_1h != 0)
Layer 2: EMA Base Signal  -- long when 20d EMA > 50d EMA (daily bars)
Layer 3: Positioning Overlay -- 30d z-score of Top Trader L/S + Divergence
Layer 4: VRP Overlay      -- 60d z-score of (IV - RV)

Compose: base * pos_mult * vrp_mult, clipped to [0, 1.5]
Align daily -> hourly via ctx.align_daily_to_1h()
Weekly rebalance (168 bars), NO stop losses
```

### Position Sizing Multipliers

**Positioning (contrarian):**
| Combined Z-score | Multiplier | Interpretation |
|-----------------|------------|----------------|
| z > 1.5         | 0.3x       | Extreme crowding, reduce |
| z > 0.5         | 0.5x       | Moderate crowding |
| -0.5 < z < 0.5  | 1.0x       | Neutral |
| z < -0.5        | 1.3x       | Contrarian, increase |
| z < -1.5        | 1.5x       | Extreme contrarian, max |

**VRP (volatility risk premium):**
| VRP Z-score | Multiplier | Interpretation |
|------------|------------|----------------|
| z > 1.0    | 1.3x       | Vol overpriced, size up |
| z > -0.5   | 1.0x       | Normal |
| z > -1.5   | 0.5x       | Vol cheap, reduce |
| z < -1.5   | 0.3x       | Extreme stress, minimum |

## Signal Validation Results

```
BTC 1H data: 54,141 bars (2020-01-01 to 2026-03-14)
Entry bars: 30,072 / 54,141 (55.5%)
Position range: [0.00, 1.50]
Mean position when in market: 0.809

EMA Base: 56.0% of days long, 44.0% flat
Positioning: 3.9% extreme crowded, 50.7% neutral, 3.1% extreme contrarian
VRP: 11.6% extreme stress, 58.9% normal, 14.3% complacent

Yearly behavior:
  2022 (bear): only 10% in market (EMA correctly flat)
  2021-2024: 69-74% in market
  2025 (sideways): 55% in market
```

## V4 Compatibility Checks -- ALL PASSED

- entry_mask: bool array, correct length
- direction: int8 array, correct length
- Scalar fields (stop_mult, trail_mult, etc.): correct types
- size_multiplier: float array, range [0, 1.5]
- conviction_score: float array, range [0, 1]
- exit_regimes: set containing CRISIS
- market_type: SPOT (0)
- Non-BTC tokens: returns empty entry mask

## Performance

- **3.63 ms/call** (exceeds 1ms target)
- Primary cost: positioning and DVOL data loading (cached after first call), daily-bar z-score computation via rolling_zscore
- Data loading is module-level cached, so second call onwards is faster
- The 1ms target is for strategies that use only ctx-provided indicators; this strategy loads external data

## Issues Discovered

### 1. External Data Dependency
The strategy loads positioning and DVOL data from parquet/JSON files directly. This is outside the standard `StrategyContext` pipeline. The v4 engine's enriched data system (`ctx.enriched`) could be used, but:
- The `all_tokens_enriched.parquet` file does not exist in this workspace
- Even if it did, it may not contain Top Trader L/S or DVOL columns
- The positioning data path (`data/alternative/binance_metrics/all_symbols_daily_ls.parquet`) is BTC-specific

**Production fix**: Register a custom indicator plugin in `v4/engine.py` that loads positioning + DVOL data and exposes them through `ctx.custom`. This would:
- Move data loading to context build time (one-time cost)
- Make the data available to all strategies via `ctx.custom['pos_multiplier']` etc.
- Eliminate the need for module-level caching

### 2. Performance Above 1ms Target
At 3.63ms/call, the strategy is ~3.6x above the 1ms performance target. Optimization paths:
1. Pre-compute daily overlays during context building (custom indicator plugin)
2. Cache the daily-to-hourly alignment result
3. Pre-compute z-scores as part of the indicator pipeline

### 3. Strategy Loader Path
The v4 strategy loader (`_load_strategy_fn`) looks for files matching `{strategy_id}_*.py` in `strategies/`. The research prototype at `research/v3_prototype_strategy.py` cannot be loaded by this mechanism. For production, the file must be:
- Copied to `strategies/s320_v3_momentum_overlays.py` (next available number is s320)
- Or registered via an alternative loading mechanism

### 4. Rebalance Implementation
The current implementation uses a for-loop to mark entry_mask at weekly rebalance points. This is O(n/168) iterations which is trivial, but it deviates from fully vectorized pattern. For production, this could use `np.arange(WARMUP_BARS, n, REBALANCE_BARS)` with fancy indexing.

### 5. Walk-Forward Mask Interaction
The v4 signal pipeline applies a walk-forward mask (`_apply_walk_forward_mask`) on top of the strategy's entry_mask. This should work correctly with the V3 strategy since the walk-forward mask simply zeros out entries during training/purge windows.

## Production Deployment Checklist

1. **Register custom indicator plugin** in `v4/engine.py`:
   - `_compute_positioning_overlay(ctx)` -> `ctx.custom['pos_z']`, `ctx.custom['pos_mult']`
   - `_compute_vrp_overlay(ctx)` -> `ctx.custom['vrp_z']`, `ctx.custom['vrp_mult']`
   - Only compute for BTC (or make configurable)

2. **Copy to strategies directory**:
   - `strategies/s320_v3_momentum_overlays.py`
   - Refactor to use `ctx.custom` instead of direct file loading
   - Performance should drop to <1ms/call with pre-computed overlays

3. **Run full validation**:
   ```bash
   python v4/validation.py --strategy s320 --tokens BTC --market spot --workers 1
   ```

4. **Portfolio backtest**:
   ```bash
   python v4/portfolio_backtest.py --strategy s320 --months 12 --capital 200000
   ```

5. **Gate checks**:
   - Gate 4: OOS Sharpe, MaxDD, Beta (validated at research level)
   - Gate 5: Walk-forward + CPCV (run via v4/validation.py)
   - Gate 6: Paper trading via v4/paper_engine.py

## Data Sources

| Data | Path | Format |
|------|------|--------|
| BTC Spot 1H | `data/spot/1h_cache/BTC_1h.parquet` | Parquet, OHLCV |
| Positioning | `data/alternative/binance_metrics/all_symbols_daily_ls.parquet` | Parquet, daily |
| BTC DVOL | `data/alternative/deribit_options/dvol/btc_dvol_daily.json` | JSON, daily OHLC |

## Reference Research

- `research/v3_walkforward_sensitivity.py` -- Original V3 validation (walk-forward + param sensitivity)
- `research/vrp_sizing_overlay_test.py` -- VRP overlay validation
- `research/v3_regime_analysis.py` -- Regime conditioning analysis
