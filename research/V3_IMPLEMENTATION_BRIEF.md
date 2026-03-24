# V3 Momentum Strategy — Implementation Brief

**Date**: 2026-03-24
**Author**: Research Coordinator (sessions 6-7)
**For**: Structured `/dev` task planning

---

## What This Is

A validated BTC momentum strategy that needs to move from research prototype to production.
The research is done. This brief covers what needs to be built.

## Strategy Summary

**V3**: 20/50 EMA crossover + Positioning overlay + VRP overlay. BTC only. Weekly rebalance. No hard stops.

| Metric | OOS (2025-01 to 2026-03) |
|--------|--------------------------|
| Return | +17.52% |
| Sharpe | 0.56 |
| MaxDD  | -20.2% |
| PF     | 1.16 |
| Walk-forward | 5/6 windows positive (mean 0.517) |
| Param sensitivity | 0 KILL flags (max 8.2% degradation) |

**Research artifacts** (read these for full context):
- `research/new_momentum_strategy_results.md` — R65 backtest results
- `research/v3_walkforward_sensitivity_results.md` — R67 robustness validation
- `research/v3_regime_analysis_results.md` — R68 regime breakdown
- `research/v3_prototype_strategy.py` — working prototype (loads data externally)
- `research/v3_prototype_notes.md` — R70 implementation notes

---

## What Needs to Be Built

### Deliverable 1: Custom Indicator Plugins (positioning + VRP)

**Why**: The prototype loads positioning/DVOL data via direct file reads on every strategy call. The v4 engine already has a `@register_indicator` plugin system (`v4/engine.py:406-412`) that runs once during context building. Moving data loading into plugins makes it efficient and available to any future strategy.

**Where**: `v4/engine.py` — add two new registered plugins alongside the existing 9.

**Plugin A: `_compute_positioning_overlay`**

```
Input:  data/alternative/binance_metrics/all_symbols_daily_ls.parquet
Filter: symbol == {ticker}USDT
Fields: sum_toptrader_ls_ratio, count_toptrader_ls_ratio, count_ls_ratio

Computation:
  1. divergence = count_toptrader_ls_ratio - count_ls_ratio
  2. z_toptrader = rolling_zscore(sum_toptrader_ls_ratio, window=30)
  3. z_divergence = rolling_zscore(divergence, window=30)
  4. combined_z = (z_toptrader + z_divergence) / 2.0
  5. multiplier = z_to_multiplier(combined_z)  # see table below
  6. Align daily → 1H via ctx.align_daily_to_1h()

Output into ctx.custom:
  - 'pos_z':   float64 array (1H), combined z-score
  - 'pos_mult': float64 array (1H), sizing multiplier [0.3, 0.5, 1.0, 1.3, 1.5]

Fallback if data missing: pos_mult = 1.0 everywhere (neutral)
```

**Positioning multiplier table:**
| Z-score range | Multiplier |
|---------------|------------|
| z > 1.5       | 0.3        |
| z > 0.5       | 0.5        |
| -0.5 < z < 0.5| 1.0       |
| z < -0.5      | 1.3        |
| z < -1.5      | 1.5        |

**Plugin B: `_compute_vrp_overlay`**

```
Input:  data/alternative/deribit_options/dvol/btc_dvol_daily.json (BTC)
        data/alternative/deribit_options/dvol/eth_dvol_daily.json (ETH)
        For other tokens: use RV proxy (90d RV × 1.2)

Computation:
  1. rv_20d = rolling_std(daily_log_returns, 20) * sqrt(365) * 100
  2. iv = DVOL if available, else 90d_rv * 1.2
  3. vrp = iv - rv_20d
  4. vrp_z = rolling_zscore(vrp, window=60)
  5. multiplier = vrp_z_to_multiplier(vrp_z)  # see table below
  6. Align daily → 1H via ctx.align_daily_to_1h()

Output into ctx.custom:
  - 'vrp_z':    float64 array (1H), VRP z-score
  - 'vrp_mult': float64 array (1H), sizing multiplier [0.3, 0.5, 1.0, 1.3]

Fallback if data missing: vrp_mult = 1.0 everywhere (neutral)
```

**VRP multiplier table:**
| Z-score range | Multiplier |
|---------------|------------|
| z > 1.0       | 1.3        |
| z > -0.5      | 1.0        |
| z > -1.5      | 0.5        |
| z < -1.5      | 0.3        |

**Data loading pattern**: Follow the existing `_load_enriched()` pattern (lazy load, cache, graceful fallback). The positioning parquet covers 19 tokens. DVOL JSON covers BTC and ETH only.

---

### Deliverable 2: Strategy File

**Where**: `strategies/s320_v3_momentum_overlays.py`

**What**: Port `research/v3_prototype_strategy.py` to use `ctx.custom` instead of direct file reads.

**Core logic** (unchanged from prototype):
```
base_signal = (ema_20d > ema_50d) ? 1.0 : 0.0      # Layer 2
final = base_signal * ctx.custom['pos_mult'] * ctx.custom['vrp_mult']  # Layers 3-4
final = clip(final, 0.0, 1.5)
entry_mask = final > 0 at weekly rebalance points
```

**Key parameters** (all validated, do not change):
- Fast EMA: 20, Slow EMA: 50
- Positioning z-window: 30, thresholds: ±1.5/±0.5
- VRP z-window: 60, thresholds: 1.0/-0.5/-1.5
- Rebalance: 168 bars (weekly)
- stop_mult: 99.0 (disabled), trail_mult: 99.0 (disabled)
- max_hold: 168 (weekly)
- BTC only (return empty entry_mask for non-BTC)
- Market type: SPOT

**Pattern to follow**: `strategies/s92_btc_trend_lev.py` (BTC-only, daily EMA, uses `ctx.custom`)

---

### Deliverable 3: Validation Run

After Deliverables 1-2 are built:

```bash
# Gate 4: BTC quick validate
/workspace/venv/bin/python v4/validation.py --strategy s320 --tokens BTC --market spot --workers 1

# Gate 5: Full validation (BTC only, but through the standard pipeline)
/workspace/venv/bin/python v4/validation.py --strategy s320 --workers 1
```

**Expected results** (from research validation):
- Walk-forward: 5/6 positive windows, mean Sharpe ~0.5
- CPCV: should pass (PBO < 40%)
- If validation FAILS: do not tune parameters. The research prototype used a self-contained backtest — any discrepancy is likely an engine integration issue (data alignment, cost model difference, etc.) that should be diagnosed.

---

### Deliverable 4 (Optional): Data Refresh Pipeline

The positioning + DVOL data needs periodic refresh for paper trading and live use.

**Positioning**: `tools/fetch_alternative_data.py` already fetches Binance metrics. Verify it covers the `all_symbols_daily_ls.parquet` file. If not, add a fetch function.

**DVOL**: `research/gamma_exposure_signal.py` has a Deribit DVOL fetcher. Extract into `tools/fetch_dvol.py` for scheduled runs.

**Frequency**: Daily is sufficient (strategy rebalances weekly).

---

## What NOT to Change

- **v3/ directory** — frozen, do not touch
- **V3 parameters** — all validated via walk-forward, do not optimize further
- **Existing strategies** — V3 is a NEW strategy file, not a modification
- **Existing plugins** — add new plugins alongside, don't modify existing 9
- **Cost model** — the research used 10 bps which matches v4's default model

## Risk Assessment

| Risk | Mitigation |
|------|-----------|
| v4 validation gives different results than research backtest | Expected: different cost model, walk-forward masking. Diagnose differences, don't tune to fit. |
| Positioning data has gaps | Plugin gracefully falls back to 1.0 (neutral). Strategy still functions as plain EMA. |
| DVOL data unavailable for non-BTC | RV proxy (90d × 1.2) is already the default for non-BTC/ETH. Tested and works. |
| Strategy performance degrades live | Weekly rebalance = very low execution sensitivity. Monitor monthly Sharpe rolling 90d. |

## File Map

| File | Status | Action |
|------|--------|--------|
| `v4/engine.py` | Exists | ADD two @register_indicator plugins |
| `strategies/s320_v3_momentum_overlays.py` | New | CREATE from prototype |
| `research/v3_prototype_strategy.py` | Exists | Reference only (do not deploy) |
| `data/alternative/binance_metrics/all_symbols_daily_ls.parquet` | Exists | Used by positioning plugin |
| `data/alternative/deribit_options/dvol/btc_dvol_daily.json` | Exists | Used by VRP plugin |

## Estimated Scope

- **Deliverable 1** (plugins): ~100 lines in engine.py, 2 functions
- **Deliverable 2** (strategy): ~150 lines, mostly ported from prototype
- **Deliverable 3** (validation): Run existing scripts, diagnose any gaps
- **Deliverable 4** (data pipeline): Optional, ~50 lines in tools/

Total: touches 2-3 files, straightforward port of validated research code.
