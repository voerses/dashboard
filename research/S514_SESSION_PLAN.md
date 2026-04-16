# S514 L/S Divergence Strategy — Session Plan & Research Summary
> Last updated: 2026-04-04

## CRITICAL BUG: Look-Ahead Bias (MUST FIX FIRST)

**Status: CONFIRMED — all s514 results are invalid until fixed**

The daily L/S metric for date D is the mean of 288 five-minute snapshots spanning
00:00-23:55 UTC on day D. This data is NOT available until D+1 00:00 UTC.

**Bug location:** `strategies/s514_ls_div_leveraged.py` line ~142 in `_get_divergence_aligned()`:
```python
# BIASED (current):
aligned = series.reindex(idx_1h.normalize(), method="ffill")

# FIX (shift by 1 day):
shifted = series.copy()
shifted.index = shifted.index + pd.Timedelta(days=1)
aligned = shifted.reindex(idx_1h.normalize(), method="ffill")
```

**Impact:** Biased +465.8% Sharpe 4.53 → Unbiased +54.6% Sharpe 1.19, MaxDD -29.4% (8.5x inflation)
**Earlier audit result was more severe (+6.3%) because it used different config (TP=3, conc=10%)**
**With no TP + conc=15%: unbiased still shows +54.6% — modest but real edge**
**Shorts lost most edge: biased +$317K → unbiased -$3K. Longs held: +$63K**
**Affects:** s506, s507, s514, s515, s516, s514c, s514w — ALL strategies using this data

### IMMEDIATE PATH: 5-min data for hourly signal updates
- 5-min L/S snapshots available from data.binance.vision (288 per day)
- At hour H, we know all snapshots up to H → compute rolling 24h mean
- Signal delay drops from 24h (daily lag) to ~1h (hourly updated)
- Expected to recover significant alpha, especially on shorts
- Need to download and store 5-min data as hourly parquets

**The same bug applies to the original Gate 5 results for s507 (+38.8%) and the
OOS monthly (+678%). ALL previous s514/s507 results need to be re-evaluated after fix.**

## Strategy Overview

- Signal: Binance top-trader vs retail L/S account ratio divergence
- IC=-0.204 at 14d horizon (strongest positioning signal found)
- Entry: contrarian when 30d z-score > 2.5 (short) or < -2.5 (long)
- Daily data, forward-filled to 1H bars, entry on first bar of new day
- 28 curated tokens (train/test validated)
- 3x leverage, no TP (trail-only at 2 ATR), stop 2.5 ATR, max hold 14d

## Data Infrastructure

### L/S Positioning Data
- Source: data.binance.vision `/data/futures/um/daily/metrics/`
- Fetcher: `scripts/fetch_binance_metrics_fast.py` (original 29 tokens)
- Bulk fetcher: `scripts/fetch_metrics_bulk.py` (229 tokens, parallel)
- Storage: `data/alternative/binance_metrics/all_symbols_daily_ls.parquet`
  - 150,281 rows, 229 symbols, 2020-09-01 to 2026-04-03
- Per-metric parquets: `data/alternative/binance_metrics/by_metric/`
  - toptrader_ls_account.parquet, toptrader_ls_position.parquet
  - global_ls_account.parquet, taker_buy_sell_ratio.parquet, open_interest.parquet
- Currently using DAILY only (aggregated from 5-min). Intraday available but not used.
- Data is Binance-specific (only exchange with top-trader breakdown)

### Columns Available Per Token Per Day
| Column | Description |
|--------|-------------|
| count_toptrader_long_short_ratio | Top trader L/S (account count) — KEY |
| sum_toptrader_long_short_ratio | Top trader L/S (position-weighted) |
| count_long_short_ratio | Global L/S (account count) — KEY |
| sum_taker_long_short_vol_ratio | Taker buy/sell volume ratio |
| sum_open_interest | OI in contracts |
| sum_open_interest_value | OI in USD |

### Price Data
- Perp 1H: `data/perp/1h_cache/` (236 tokens, UTC timestamps)
- All timestamps naive UTC, aligned with L/S data

## Token Selection

### Current: Static 28-Token Allowlist (train/test validated)
```
EIGEN, PENGU, INJ, LTC, ARB, SAND, AAVE, GUN, RAYSOL, LINK,
HBAR, RENDER, ETH, PIXEL, DOGE, WLD, VANRY, ADA, AIOT, UNI,
BTC, IP, NEAR, ETC, MYX, PIPPIN, ZEN, RESOLV
```

Selected via: train (Apr-Oct 2025) / test (Oct-Apr 2026) split — tokens positive in both.

### Ultra-Robust 11 (profitable in ALL 3 cross-validation windows)
```
ETH, BTC, ZEN, RENDER, EIGEN, AAVE, PIXEL, LTC, LINK, VANRY, ADA
```

### Token Quality Predictors (from analysis)
- OI value: winners median $20M vs losers $7M (2.83x ratio, r=+0.301)
- Data history: winners 655 days vs losers 472 days
- Divergence volatility: winners 0.248 vs losers 0.182

## Optimization Research Results (ALL PRE-FIX — NEED RE-VALIDATION)

### What Helped (on biased backtest)
| Change | Impact |
|--------|--------|
| Remove TP (999 instead of 3 ATR) | +182pp return |
| Concentration 15% | +14pp return |
| Aggressive conviction sizing (z/2.5)^2 | +18pp return |

### What Did NOT Help
| Dimension | Finding |
|-----------|---------|
| Z-score window | 30d optimal, no improvement from 10d-90d |
| Regime conditioning | Signal is regime-agnostic, all filters hurt |
| S/R proximity | Irrelevant to positioning signal (all variants hurt) |
| VRP/trend overlays | All hurt — MR signal is self-contained |
| Asymmetric long/short params | Symmetric is already near-optimal |
| Monthly token rotation | Static 28 beats all dynamic rules by 5-10x |
| Dynamic universe width | 28 is sweet spot (11 too narrow, 38 too wide) |

### Long/Short Split
- Shorts dominate: 2x trades, 2x P&L
- Split s515 (short) + s516 (long) improved Sharpe but reduced return
- Unified strategy better due to capital utilization

## Next Steps (Priority Order)

### 1. FIX THE LOOK-AHEAD BIAS
- Apply 1-day shift to `_get_divergence_aligned()` in s514 (and all variants)
- Re-run L12M backtest and OOS monthly with the fix
- If unbiased results are still positive (even modestly), proceed
- If unbiased results are negative, KILL the strategy

### 2. Investigate Sub-Daily L/S Data
- data.binance.vision has 5-minute granularity (288 per day)
- Instead of daily mean, could use:
  - End-of-day snapshot (23:55 UTC value) — available at day close
  - Rolling 24h mean updated hourly — more responsive, less look-ahead
  - 4H or 8H aggregates — faster signal updates
- This could recover some edge lost by the 1-day shift

### 3. Optimize Entry Price
- Currently enters at close of signal bar (00:00 UTC of new day)
- Could use limit orders: enter at VWAP or near intraday support
- Could use sub-hourly monitoring: enter on first 1m/5m bar that confirms direction
- V4 engine supports `entry_limit_price` field

### 4. ML Token Selection (LambdaMART)
**Research summary (from web research):**
- Frame as learning-to-rank, NOT classification/regression
- Use LambdaMART (XGBoost `rank:ndcg` or LightGBM `lambdarank`)
- max_depth=3, 10-15 features, heavy regularization (small sample)
- CPCV for overfitting detection

**Feature engineering (per token per month):**
- Signal quality: rolling IC, hit rate, signal frequency, MR speed
- Liquidity: OI level + change, ADV, funding rate volatility
- Positioning: divergence persistence, top-trader agreement, L/S ratio momentum
- Structural: data history length, volatility, BTC correlation

**Architecture:**
```
Hard filter (OI>$10M, history>180d, ADV>$5M)
  → ~80-100 tokens pass
  → LambdaMART ranks by predicted signal quality
  → top-28 traded
  → confidence-weighted blend with static list
```

**Train/test:** Train on months 1-12, walk-forward test months 13-24
**Heuristic baseline to beat:** weighted rank of (OI + history + div_vol + hit_rate)

**Key references:**
- Poh et al. 2021 "Building Cross-Sectional Systematic Strategies By Learning to Rank"
- Koshiyama et al. 2021 "QuantNet: Transferring Learning Across Trading Strategies"
- Cakici et al. 2024 "ML and Cross-Section of Cryptocurrency Returns" (XGBoost R²=4.85%)
- Lopez de Prado — CPCV for overfitting detection

### 5. Paper Trading Deployment (after bias fix + re-validation)
- Daily cron for L/S data: `scripts/fetch_binance_metrics_fast.py --extend`
- Paper trade via V4 paper engine
- Monitor: <10 trades/30d = signal decay warning
- Gate 6 needs 50+ trades before go-live

## Research Scripts Created This Session
- `research/gate5_s507_full_validate.py` — Gate 5 V4 backtest
- `research/gate5_s507_param_sensitivity.py` — Parameter sensitivity
- `research/gate5_s507_per_token.py` — Per-token concentration analysis
- `research/gate5_s507_continuous_sizing.py` — Continuous sizing test
- `research/s98_filter_diagnosis.py` — s98 filter ablation
- `research/s98_trigger_ema_pullback.py` — EMA pullback trigger
- `research/s98_trigger_rsi_pullback.py` — RSI pullback trigger
- `research/s98_trigger_donchian.py` — Donchian breakout trigger
- `research/s98_multi_trigger_validate.py` — Triple trigger validation
- `research/s98_triple_sensitivity.py` — Leverage + param sweep
- `research/s513_gate3_validate.py` — s513 Gate 3
- `research/s513_gate4_validate.py` — s513 Gate 4 walk-forward
- `research/s507_oos_monthly.py` — s507 OOS monthly
- `research/s507_leverage_sweep.py` — s507 leverage optimization
- `research/s514_expanded_universe.py` — 229-token test
- `research/s514_token_quality_analysis.py` — Per-token P&L analysis
- `research/s514_optimal_tokens.py` — 3-window cross-validation
- `research/s514_train_test_tokens.py` — Train/test token selection
- `research/s514_filtered_v4_backtest.py` — V4 filtered backtests
- `research/s514_compare_lists_oos.py` — ROBUST vs MAX_PNL comparison
- `research/s514_window_sweep.py` — Z-score window sweep
- `research/s514_conviction_sizing.py` — Dynamic conviction sizing
- `research/s514_regime_sweep.py` — Regime conditioning
- `research/s514_overlay_sweep.py` — VRP + trend overlays
- `research/s514_asymmetric_sweep.py` — Long/short split optimization
- `research/s514_token_rotation.py` — Monthly token rotation
- `research/s514_walkforward_tokens.py` — Walk-forward dynamic selection
- `research/s514_bias_audit.py` — **CRITICAL: look-ahead bias audit**

## Strategy Files Created/Modified
- `strategies/s513_triple_trigger_swing.py` — Triple-trigger s98 variant (Gate 4 FAIL)
- `strategies/s514_ls_div_leveraged.py` — Main L/S divergence 3x (**HAS BIAS BUG**)
- `strategies/s515_ls_div_short.py` — Short-only split
- `strategies/s516_ls_div_long.py` — Long-only split
- `strategies/s514c_ls_div_core11.py` — Core 11 tokens
- `strategies/s514w_ls_div_wide38.py` — Wide 38 tokens

## Data Infrastructure Built
- `scripts/fetch_metrics_bulk.py` — Parallel bulk fetcher for 229 tokens
- 229 tokens of L/S data downloaded (was 29)
- Per-metric parquets in `data/alternative/binance_metrics/by_metric/`
