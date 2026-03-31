# Researcher Best Practices — V4 Engine Parameter Reference & Ground Rules

> **TL;DR -- Every backtest parameter, where it lives, how to change it, and 10 rules that prevent fake edges.**
> The sizing pipeline is documented in `V4_SIZING_PIPELINE.md` -- this file catalogs ALL configurable vs hardcoded
> parameters across the full engine stack and provides ground rules with concrete project examples.
> **When to read this file:** Before writing any new strategy, after any sizing change, when interpreting backtest results.

---

## Part 1: Complete Parameter Catalog

### Layer 1: Engine Defaults (SizingDefaults) -- `v4/config.py`

Immutable dataclass. Strategies override via `sizing_overrides` in config JSON, validated by `resolve_sizing()`.

| Parameter | Default | Overridable | Safety Rails | Where Used | Purpose |
|-----------|---------|-------------|--------------|------------|---------|
| `edge_minimum` | 0.10 | Yes | 0.05--0.50 | `sizing.py:46` | Min edge to allow entry (returns 0 if below) |
| `target_vol` | 0.02 | Yes | 0.005--0.05 | `sizing.py:72` | Vol scaling numerator in kelly formula |
| `spot_max_equity_pct` | 1.0 | Yes | 0.10--1.0 | `sizing.py:86` | Max spot position as fraction of equity |
| `kelly_mult_override` | 0.0 | Yes | 0.05--0.50 | `sizing.py:50` | Fixed kelly mult (0 = use ADV curve) |
| `kelly_mult_scale` | 1.0 | Yes | 0.5--2.0 | `sizing.py:65` | Multiplier on ADV-curve kelly output |
| `cap_pct_override` | 0.0 | Yes | 0.01--0.15 | `sizing.py:52-66` | Fixed cap_pct (0 = use ADV curve) |
| `cap_pct_scale` | 1.0 | Yes | 0.5--2.0 | `sizing.py:59-69` | Multiplier on ADV-curve cap output |
| `min_adv_usd` | 500,000 | Yes | 100K--10M | `universe.py:265` | Minimum ADV to be tradeable |
| `adv_lookback_days` | 30 | Yes | 7--90 | `universe.py:267` | Rolling ADV window in days |
| `kelly_mult_floor` | 0.15 | Yes | 0.05--0.40 | `sizing.py` | ADV curve floor for kelly |
| `kelly_mult_range` | 0.35 | Yes | 0.10--0.80 | `sizing.py` | ADV curve range for kelly |
| `cap_pct_floor` | 0.02 | Yes | 0.005--0.08 | `sizing.py` | ADV curve floor for cap |
| `cap_pct_range` | 0.10 | Yes | 0.02--0.30 | `sizing.py` | ADV curve range for cap |
| `adv_scaling_divisor` | 5.0 | Yes | 1.0--20.0 | `sizing.py` | ADV log-scale denominator |
| `unrealized_pnl_floor` | 0.85 | **NO** | -- | `simulator.py:783` | sizing_eq >= portfolio_eq * 0.85 |
| `funding_buffer_pct` | 0.01 | **NO** | -- | `simulator.py:891,1148` | Reserve for funding costs |
| `vol_floor` | 0.005 | **NO** | -- | `sizing.py:72` | Min volatility (prevents size explosion) |

**Non-overridable parameters (only 3 remain):** `vol_floor`, `unrealized_pnl_floor`, `funding_buffer_pct`. All other sizing params are now overridable via `sizing_overrides` with SAFETY_RAILS bounds. To make a non-overridable parameter configurable: remove from `NON_OVERRIDABLE` set in `config.py`, add a `SAFETY_RAILS` entry with min/max bounds.

### Layer 2: Portfolio Config (PortfolioConfig) -- config JSON files

Set per-portfolio run. NOT strategy-overridable.

| Parameter | Default | Where Used | Purpose |
|-----------|---------|------------|---------|
| `capital` | 200,000 | `simulator.py:1327` | Initial equity |
| `max_portfolio_positions` | 40 | `simulator.py:771` | Portfolio-wide position limit |
| `concentration_limit` | 0.10 | `simulator.py:851,1123` | Per-token max as fraction of portfolio equity |
| `adv_cap_pct` | 0.05 | `sizing.py:75` | Hard cap: 5% of rolling ADV |
| `min_position_usd` | 200 | `simulator.py:837,1105` | Entry rejection floor |
| `exchange` | "binance" | `simulator.py:383` | Fee/MMR lookup key |
| `base_spread_bps` | 3.0 | `sizing.py:94` | Slippage base spread |
| `impact_coeff` | 0.03 | `sizing.py:100` | Market impact coefficient |
| `max_slip_bps` | 300 | `sizing.py:101` | Slippage ceiling in basis points |
| `seed` | 42 | `simulator.py:1328` | Random state for entry shuffle |
| `train_bars` | 8,760 (365d) | `signals.py:229` | Walk-forward training window (hours) |
| `recal_bars` | 2,160 (90d) | `signals.py:138` | Recalibration interval (hours) |
| `purge_bars` | 120 (5d) | `signals.py:139` | Purge window after each recal (hours) |
| `stress_adv_multiplier` | 1.0 | `simulator.py:141` | ADV multiplier for stop/margin_call exits |
| `conviction_mode` | "shuffle" | `simulator.py:677-713` | Entry ordering: shuffle/ranked/hybrid |
| `min_conviction_threshold` | 0.0 | `simulator.py:735-741` | Skip entries below this conviction |
| `max_sizing_equity` | None | `simulator.py:786` | Cap portfolio equity for sizing (None = uncapped) |

### Layer 3: Strategy Spec (StrategySpec) -- config JSON per strategy

| Parameter | Default | Where Used | Purpose |
|-----------|---------|------------|---------|
| `strategy_id` | (required) | everywhere | Strategy identifier (e.g. "s56") |
| `weight` | 1.0 | `simulator.py:787` | Fraction of portfolio equity allocated |
| `max_positions` | 15 | `simulator.py:776` | Per-strategy position limit |
| `market` | "combined" | `signals.py:193` | "spot", "perp", or "combined" |
| `strategy_type` | "per_token" | `signals.py:187` | "per_token" (Class A) or "portfolio" (Class B) |
| `circuit_breaker_r` | 0.0 | `simulator.py:506-515` | Emergency exit at Nx initial risk (0=disabled) |
| `pump_filter_funding_zscore` | 0.0 | `simulator.py:753-768` | Block long entries when funding z > threshold |
| `pump_filter_range_threshold` | 0.0 | `simulator.py:744-750` | Block entries when bar_range/ATR > threshold |
| `adv_sizing_enabled` | False | `sizing.py:80-82` | Apply ADV-proportional size reduction |
| `adv_sizing_base` | 100M | `sizing.py:81` | ADV normalization base for sqrt scaling |
| `adv_sizing_floor` | 0.20 | `sizing.py:81` | Minimum ADV sizing multiplier |
| `exit_resolution` | 0 | `signals.py:149` | Sub-hourly exit granularity (0=hourly) |
| `sizing_overrides` | {} | `config.py:70-109` | Dict of SizingDefaults overrides (validated) |
| `max_concurrent_per_token` | 1 | `simulator.py:565` | Max concurrent positions per token+strategy (1=no re-entry) |
| `dd_scaling` | [] | `simulator.py:529-551` | Drawdown scaling: list of (threshold, fraction) tuples |

### Layer 4: Strategy Result (StrategyResult) -- returned per strategy call

These are set by strategy code and flow through `signals.py` -> `simulator.py`.

| Field | Type | Default | Where Used | Purpose |
|-------|------|---------|------------|---------|
| `entry_mask` | ndarray(bool) | (required) | `simulator.py:659` | Bar-by-bar entry signal |
| `direction` | ndarray(int8) | (required) | `simulator.py:827` | +1 long, -1 short |
| `stop_mult` | float/ndarray | 3.0 | `simulator.py:1178` | Stop distance in ATR multiples |
| `trail_mult` | float/ndarray | 1.5 | `simulator.py:447` | Trail distance in ATR multiples |
| `target_mult` | float | 999.0 | `simulator.py:529` | Take-profit in ATR multiples |
| `no_stop_bars` | int | 0 | `simulator.py:436` | Bars before stop becomes active |
| `min_hold` | int | 6 | `simulator.py:557` | Minimum hold before RSI/mean exit |
| `max_hold` | int | 720 | `simulator.py:580` | Maximum hold (30 days at 1h) |
| `edge` | float | 0.35 | `sizing.py:46,71` | Gate + kelly numerator |
| `size_multiplier` | float/ndarray | 1.0 | `sizing.py:71` | Scales kelly_frac (conviction) |
| `cap_multiplier` | float/ndarray | 1.0 | `sizing.py:74` | Scales capital_cap |
| `leverage` | float/ndarray | 1.0 | `sizing.py:85` | Notional = margin x leverage |
| `max_trade_pct` | float | 0.0 | `sizing.py:78-79` | Absolute cap: pos <= equity x this |
| `exit_regimes` | set | {CRISIS} | `simulator.py:548-552` | Force exit in these regimes |
| `rsi_exit_level` | float | 999.0 | `simulator.py:555-563` | RSI threshold for exit |
| `convex_exit` | bool | False | `simulator.py:427-434` | Use convex exit mode |
| `mean_target_vals` | ndarray/None | None | `simulator.py:566-571` | Per-bar mean-reversion target |
| `trail_schedule` | ndarray/None | None | `simulator.py:438-445` | Progressive trail: [[atr_thresh, mult], ...] |
| `time_trail_schedule` | ndarray/None | None | `simulator.py:450-458` | Time-based trail: [[bars, mult], ...] |
| `max_trail_mult` | ndarray/None | None | `simulator.py:461-463` | Per-bar ceiling on trail multiplier |
| `funding_exit_threshold` | float | 0.0 | `simulator.py:585-588` | Exit if cum_funding/margin > this |
| `partial_tp_atr` | float | 0.0 | `simulator.py:490-498` | Partial TP trigger in ATR units |
| `partial_tp_pct` | float | 0.5 | `simulator.py:231` | Fraction closed at partial TP |
| `partial_tp_trail` | float | 1.5 | `simulator.py:295` | Trail on remainder after partial TP |
| `breakeven_atr` | float | 0.0 | `simulator.py:413-424` | Move stop to BE after this ATR profit |
| `chandelier_lookback` | int | 0 | `simulator.py:467-479` | Trail from N-bar high/low (0=all-time) |
| `bear_target_mult` | float | 0.0 | `simulator.py:530-533` | Tighter TP in DOWNTREND regime |
| `bear_max_hold` | int | 0 | `simulator.py:576-579` | Shorter hold in DOWNTREND (0=use max_hold) |
| `conviction_score` | ndarray/None | None | `simulator.py:683-684` | Per-bar [0,1] for entry prioritization |
| `market_type` | int | SPOT (0) | `signals.py:349-362` | SPOT=0, PERP=1, or per-bar ndarray |
| `secondary_*` | various | None | `simulator.py:976-1005` | Combined strategy secondary leg params |
| `capital_split` | float | 0.5 | `simulator.py:833` | Primary/secondary capital allocation |

### Layer 5: Hardcoded Constants (Magic Numbers)

These are embedded in source code with no config pathway.

| Constant | Value | File:Line | Purpose | To Make Configurable |
|----------|-------|-----------|---------|---------------------|
| WARMUP_DAYS | 180 | `signals.py:224` | Extra history for indicator burn-in | Add to PortfolioConfig |
| min_bars (context) | 500 (prod), 210 (signals) | `engine.py:831`, `signals.py:256` | Minimum 1h bars to build context | Already a parameter on `_build_context()` |
| Conviction tiers | 0.66/0.33 | `simulator.py:700-704` | Hybrid mode tier boundaries | Add to PortfolioConfig or StrategySpec |
| Margin call threshold | -5% of equity | `simulator.py:1241` | Free capital floor before force-close | Add to PortfolioConfig |
| Margin deficiency warning | -10% of equity | `simulator.py:1299` | Warn on deep negative free_capital | Add to PortfolioConfig |
| NaN ATR fallback | 2% of price | `simulator.py:410,792,1008,1072` | Fallback when ATR is NaN | Hardcoded; could add to SizingDefaults |
| Regime exit min bars | 6 | `simulator.py:550` | Minimum bars held before regime exit | Could add to StrategyResult |
| Fallback ADV | $5M | `universe.py:40` | Used when ADV data unavailable | Already a constant; could add to config |
| Burn-in days | 90 | `universe.py:266` | New listings excluded for 90 days | Parameterized in `compute_liquidity_mask()` |
| ADV tier thresholds | $50M/$10M | `universe.py:30-31` | Tier classification (reporting only) | Cosmetic; low priority |
| MAX_ADV_PCT | 0.02 | `universe.py:33` | Legacy: 2% of daily volume cap | Superseded by `adv_cap_pct` in PortfolioConfig |
| Quality filter min_bars | 2000 | `universe.py:523` | Token quality gate bar count | Parameterized in `get_filtered_universe()` |
| Quality filter min_grade | "B" | `universe.py:523` | Token quality gate grade | Parameterized in `get_filtered_universe()` |
| Funding z-score lookback | 168h (7d) | `simulator.py:757` | Pump filter funding window | Could add to StrategySpec |
| Funding z-score min data | 24h (1d) | `simulator.py:760` | Min funding data for z-score | Hardcoded; unlikely to need change |
| Composite check limit | 8.0x equity | `config.py:121` | Max worst-case position fraction | Hardcoded safety; should not be configurable |
| NO_COMBINED exclusions | {PAXG} | `universe.py:230` | Tokens excluded from combined market | Static set; edit source to change. LIT and XMR removed (delisted from Binance, spot data deleted 2026-03-30). |

### Layer 6: Indicator Constants -- `v4/engine.py`

All indicator periods are hardcoded in `compute_indicators_fast()`. Strategies cannot change them.

| Indicator | Period(s) | Notes |
|-----------|-----------|-------|
| EMA | 10, 20, 50 | Standard trend EMAs |
| MACD | 12, 26, 9 | Standard MACD |
| RSI | 14 | Standard RSI |
| Bollinger Bands | 20, 2.0 std | Standard BBands |
| ATR | 14 | EMA-smoothed true range |
| ADX / DI | 14 | Directional movement |
| Volume SMA | 20 | Volume ratio baseline |
| Volatility (ret_1) | 20 | 20-bar rolling std of log returns |
| Donchian Channel | 20 | High/low breakout |
| Regime detection | ADX>25, expanding quantiles (min 60d) | Daily regime labels |
| OBV slope | 10 bars | Custom indicator plugin |
| VWAP | 20 bars | Custom indicator plugin |
| Momentum returns | 6h, 12h, 24h, 48h, 120h; 5d, 10d, 20d, 60d | Custom indicator plugin |
| Positioning z-score | 30d rolling | Custom indicator plugin |
| VRP z-score | 60d rolling | Custom indicator plugin |
| Funding z-score | 168h (7d) rolling | Custom indicator plugin |
| Squeeze intensity | 240-bar (10d) BB width avg | Custom indicator plugin |

**To change an indicator period:** Edit `compute_indicators_fast()` or the relevant `@register_indicator` plugin in `engine.py`. This affects ALL strategies.

### Layer 7: Validation Parameters -- `v4/validation.py`

| Parameter | Default | CLI Flag | Purpose |
|-----------|---------|----------|---------|
| `train_days` | 365 | `--train-days` | Walk-forward training window |
| `recalibrate_every` | 90 | `--recal-days` | Recalibration interval |
| `purge_days` | 5 | `--purge-days` | Purge window between train/test |
| `n_groups` | 6 | `--cpcv-groups` | CPCV fold count |
| `n_test_groups` | 2 | (hardcoded) | CPCV test groups per split |
| `purge_pct` | 0.01 | (hardcoded) | CPCV purge fraction |
| `warmup_bars` | 200 | (hardcoded) | CPCV context warm-up |
| `pbo_threshold` | 0.40 | `--pbo-threshold` | PBO pass/fail cutoff |
| `capital` | 200,000 | `--capital` | Validation capital |
| `workers` | 4 | `--workers` | Parallel execution |

### Layer 8: Exchange Fee Tables -- `v4/universe.py`

Hardcoded. Edit source to change.

| Exchange | Spot (maker/taker) | Perp (maker/taker) | MMR | Liquidation Fee |
|----------|-------------------|-------------------|-----|-----------------|
| Binance | 10/10 bps | 2/5 bps | 0.40% | 1.5% |
| Kraken | 16/26 bps | 2/5 bps | 1.00% | 1.5% |
| Hyperliquid | 4/7 bps | 1.5/4.5 bps | 5.00% | 1.5% |

### Layer 9: Report Sanity Bounds -- `v4/report.py`

Hardcoded warning thresholds (do not block, only warn):

| Metric | Warning Range | Meaning |
|--------|--------------|---------|
| Total return | Outside [-50%, +500%] | Likely unrealistic |
| Sharpe ratio | Outside [-1, 5] | Likely inflated or broken |
| Max drawdown | Outside [5%, 80%] | Too smooth or catastrophic |

---

## Part 2: Researcher Ground Rules

### Rule 1: NO Spot Overleverage

**Spot positions MUST NEVER exceed 100% of equity.**

- `size_multiplier` on spot strategies must be capped at 1.0
- `spot_max_equity_pct` safety rail max is 1.0 -- the engine enforces this
- If your research shows >100% returns on spot without leverage, your backtest has a bug
- The `leverage` field has no effect on spot (`leverage <= 1.0` triggers spot equity cap in `sizing.py:85-86`)

**Project example:** s320's original V3 backtest showed +387% returns. The root cause was `size_multiplier` clipped to [0, 1.5] instead of [0, 1.0], creating $300K BTC exposure on $200K cash -- impossible on spot without margin. After fixing to [0, 1.0]: +2.9% annualized over 60 months. The strategy was essentially dead. (Finding #130 in RESEARCH_STATUS)

**Checklist:**
- [ ] `size_multiplier` never exceeds 1.0 for spot strategies
- [ ] `spot_max_equity_pct` is not set above 1.0 (safety rail blocks it anyway)
- [ ] If strategy returns > capital on spot, investigate before celebrating

### Rule 2: NO Look-Ahead Bias

**All signals must use only data available at the time of the decision.**

- `np.roll(signal, 1)` or equivalent shift is MANDATORY for any indicator used in entry/exit logic
- Daily signals aligned to hourly bars must use the PREVIOUS day's close, not the current day
- The engine's `compute_rolling_adv()` already lags by 1 day (`signals.py:305-314`), but strategy-level overlays must also lag
- DVOL, positioning, funding data must be lagged by at least 1 bar
- Walk-forward validation (not full-period backtest) is the ONLY valid performance measure
- If your IS and OOS results have the same sign but wildly different magnitudes, you likely have leakage

**Project example:** The engine's per-bar liquidity mask already handles most look-ahead bias for ADV-based filtering. Residual static-universe look-ahead inflates Sharpe by +0.2% to +2.7% depending on universe breadth. (STRATEGY_CATALOG finding)

**How the engine prevents it:**
- `compute_rolling_adv()` lags ADV by 1 day (day d's ADV applies to day d+1's bars)
- `detect_daily_regime()` uses expanding (causal) percentiles with 60-day minimum
- Walk-forward masking in `signals.py:121-143` blanks entry_mask during training windows

**Checklist:**
- [ ] Every external data source (funding, positioning, DVOL) is lagged >= 1 bar
- [ ] No full-array statistics used in signal generation (use expanding/rolling only)
- [ ] Walk-forward results are the reported metrics, not full-period backtest

### Rule 3: Non-Overlapping Returns for IC Measurement

**NEVER compute IC using overlapping forward returns.**

Using weekly IC with daily observations inflates t-statistics by 3--5x due to return autocorrelation. Use non-overlapping return windows only: for 7-day IC, sample every 7th observation.

**Project example:** Funding Dispersion signal (BTC vs alt basket) showed IC=+0.13 with t=4.6 using overlapping weekly returns. With non-overlapping observations: all time horizons dead, and the signal died structurally post-2024. This was finding #54 in RESEARCH_STATUS, and finding #129: "Weekly-windowed IC with overlapping returns is dangerously inflating."

**Correct implementation:**
```python
# BAD: overlapping (inflates t-stat 3-5x)
ic_bad = np.corrcoef(signal[:-7], forward_return_7d[:-7])[0, 1]

# GOOD: non-overlapping (honest t-stat)
idx = np.arange(0, len(signal) - 7, 7)  # sample every 7th bar
ic_good = np.corrcoef(signal[idx], forward_return_7d[idx])[0, 1]
```

### Rule 4: IS/OOS Split is Mandatory

**All signal testing must use temporal IS/OOS split (first 60% / last 40%).**

- Never report full-period metrics as if they are OOS
- If IS and OOS IC have DIFFERENT SIGNS, the signal is dead -- kill it immediately
- Post-hoc parameter optimization on the full sample = overfitting

**Project example:** Funding Flip signal (sign reversal, 72h decay) had IS IC=-0.151 (t=-2.11), but OOS IC=+0.022 (t=+0.25) -- sign flip. Killed immediately. Finding #52 in RESEARCH_STATUS. Funding RoC Momentum likewise: IS IC=-0.172 (t=-2.43), OOS IC=-0.062 (t=-0.70) -- insignificant. Killed as finding #53.

**Rule of thumb:** Expect 40--60% IS-to-OOS degradation on genuine signals. If OOS retains >80% of IS performance, check for leakage. If OOS is <20% of IS, the signal is overfit.

### Rule 5: Cost-Adjusted Edge

**Raw IC does not matter if costs eat the edge.**

Transaction costs in this system:
- Perp taker (Binance): 5 bps per side (entry + exit = 10 bps round-trip)
- Spot taker (Binance): 10 bps per side (20 bps round-trip)
- Slippage: `base_spread_bps + impact_coeff * sqrt(participation) * 10000` (see `sizing.py:91-101`)
- Funding costs on perps: 8h settlement, can be positive or negative
- Perp carry drag: structural long bias means shorts collect funding, longs pay

**Cost math:** A strategy trading weekly with 15 bps total cost per round-trip accumulates 52 * 0.15% = 7.8% annual fee drag. A monthly strategy: 12 * 0.15% = 1.8%.

**Project example:** ALL spot-only strategies lose money in Jan--Mar 2026 sideways market despite having positive signals. Only perp and combined strategies survive because perp fees are lower (5 bps vs 10 bps) and short entries collect funding. (STRATEGY_CATALOG critical finding)

**Checklist:**
- [ ] Compute net-of-cost return before declaring an edge
- [ ] For perp strategies, account for funding drag (positive for longs, negative for shorts)
- [ ] Check total_fees and total_funding in the simulation report -- if fees exceed PnL, the strategy has no real edge

### Rule 6: Regime Robustness

**Test in UPTREND, DOWNTREND, RANGE, and CRISIS regimes separately.**

The engine detects 5 regimes (constants in `engine.py:69`):
- CRISIS (0): vol > 2x expanding p75
- QUIET (1): vol < 0.7x expanding p25
- UPTREND (2): ADX > 25, EMA20 > EMA50
- RANGE (3): default (no strong trend or extreme vol)
- DOWNTREND (4): ADX > 25, EMA20 < EMA50

A signal that only works in one regime is fragile and will fail when the regime changes.

**Project example:** s16 (complex mean-reversion) had 0% survival rate across CPCV folds. It worked beautifully in-sample during the 2024 bull run but collapsed in all other regimes. Simple trend-following (s11) with 4 conditions survives across all regimes because it does not overfit to one market state. More conditions = more overfit paths = worse OOS. (STRATEGY_CATALOG: "S16 at 0% survival")

**Practical check:** Run a single-strategy backtest with `bear_target_mult` and `bear_max_hold` to see behavior differences across regimes. If the strategy loses money in DOWNTREND but your backtest period was 80% UPTREND, the aggregate metrics are misleading.

### Rule 7: Sufficient Trade Count

**Minimum 30 trades for any statistical claim.**

- Walk-forward: at least 6 windows, majority must be positive
- PBO (Probability of Backtest Overfitting) must be < 40%
- CPCV: at least 6 groups with 2 test groups (15 fold combinations)

**Project example:** Funding Flip signal had only 320 events across 6 years of data. Even with a seemingly decent IS t-stat of -2.11, the OOS flipped sign. With so few events, any correlation is noise. (Finding #52)

**The validation engine enforces this:** `v4/validation.py` requires PBO < 0.40 (configurable via `--pbo-threshold`). A strategy must pass BOTH walk-forward (OOS PnL > 0) AND CPCV (PBO < threshold) to be "validated."

### Rule 8: Research vs Production Fidelity

**The production strategy MUST faithfully implement the validated research.**

- If research validated on 50/200 SMA, production must use 50/200 SMA (not 20/50 EMA)
- All overlay threshold values must match the research exactly
- Run a "research reproduction test" before deploying: backtest the production code on the same data window and compare metrics

**Project example:** The positioning overlay uses specific z-score thresholds (>1.5 -> 0.3x, >0.5 -> 0.5x, <-0.5 -> 1.3x, <-1.5 -> 1.5x). These were validated in research and are hardcoded in `engine.py:641-647`. Any modification to these thresholds requires re-validation.

**What to watch for:**
- Indicator periods changed between research and production
- Different data alignment (daily vs hourly) than what was researched
- Additional filters added "for safety" that change the signal population
- Edge case: strategy researched on Binance data but deployed on Hyperliquid (different fee structure)

### Rule 9: Position Sizing Sanity Checks

**After ANY sizing change, run a diagnostic backtest and check:**

1. No `min_size` rejections where you expect trades (check rejection stats in report)
2. Binding constraint is the one you intended (kelly? cap? ADV? spot equity?)
3. Position sizes are reasonable (not $0, not >equity)
4. Document which constraint is binding and why

See `V4_SIZING_PIPELINE.md` for the full formula reference.

**Project example:** s320 had 88% of capital idle when running as BTC-only because `cap_pct=0.12` and `concentration_limit=0.10` capped BTC position at $24K on $200K portfolio. For a multi-token engine running a 1-token strategy, this is the expected behavior but wrong config. Fix required: `cap_multiplier=8.0`, `concentration_limit=1.0`, `max_trade_pct=0.95`. (Finding #45 in PROJECT_STATUS)

**Diagnostic checklist:**
- [ ] Run backtest, check `extra_info["rejections"]` -- which rejection type dominates?
- [ ] If `min_size` rejections are high: strategy is entering with tiny conviction; check `size_multiplier` at entry bars
- [ ] If `adv_cap` rejections are high: positions too large for token liquidity; reduce `cap_multiplier` or check ADV data
- [ ] If `capital` rejections are high: too many concurrent positions; reduce `max_positions` or increase capital
- [ ] Verify `total_fees` and `total_funding` are plausible fractions of total PnL

### Rule 10: Compounding Awareness

**V4 compounds realized PnL by default.**

The portfolio equity is `initial_capital + realized_pnl - total_fees - total_funding` (`simulator.py:74-76`). This means:
- Drawdowns reduce future position sizes (self-reinforcing losses)
- A -20% drawdown means positions are 20% smaller, requiring +25% gain to recover
- Winners compound: a $200K account that grows to $2M takes $2M-sized positions
- Uncapped compounding inflates backtested returns -- always report CAGR, not total return

**Project example:** The STRATEGY_CATALOG warns: "All return figures below were generated with uncapped equity compounding on $200K starting capital. The backtester allows equity to grow to $33-55M and take multi-million dollar altcoin perp positions that CANNOT be executed in practice. Realistic returns after $2M sizing cap and hourly slippage are estimated at 60-80% lower." The s28 strategy showed +3157% return (PRE-MTM -- likely inflated) with uncapped compounding, but this is unachievable at scale.

**Mitigation:**
- Use `max_sizing_equity` in PortfolioConfig to cap equity used for sizing (e.g., $2M)
- Report CAGR, Sharpe, max drawdown -- not raw total return
- Compare with `max_sizing_equity` set to initial capital (no compounding) for a conservative estimate
- Calmar ratios are inflated ~100-1000x by uncapped compounding (realistic Calmar likely 1-5)

### Rule 11: Walk-Forward is the Only Valid Performance Measure

**Never report full-period backtest results as strategy performance.**

The V4 engine applies walk-forward masking automatically (`signals.py:121-143`):
- First `train_bars` (default 365 days): all entries masked
- Every `recal_bars` (default 90 days): `purge_bars` (default 5 days) masked
- Only bars AFTER training + purge are eligible for entry

**What this means:**
- The first year of data is training-only -- no trades
- Every 90 days, 5 days are purged to prevent leakage
- Reported metrics are OOS by construction

**If you run a quick "is this signal alive?" test outside the portfolio engine** (e.g., in a Jupyter notebook), you MUST manually implement IS/OOS splitting. The engine does this for you, but raw signal analysis does not.

### Rule 12: ADV Data Quality

**Rolling ADV determines position sizing, liquidity gating, and slippage.**

If ADV data is wrong, everything downstream is wrong:
- Stale ADV (from a delisted token) inflates position sizes
- Missing volume data triggers `FALLBACK_ADV` of $5M -- may be too generous for micro-caps
- The 90-day burn-in prevents trading new listings with unreliable volume data

**Checks before using a new token:**
- [ ] Verify the token has > 90 days of history (burn-in)
- [ ] Check that volume data is non-zero and not flat (data quality issue)
- [ ] For combined strategies, confirm both spot AND perp data exist with sufficient overlap
- [ ] Tokens in `NO_COMBINED` set (PAXG) are excluded from combined strategies. LIT and XMR were delisted from Binance and their spot data deleted (2026-03-30).

### Rule 13: Recent Performance is the Only Metric That Matters

**Full-period backtests are dominated by 2020-2021 bull market returns that will not repeat. The ONLY performance that matters for deployment decisions is the most recent 12 months.**

Crypto markets have undergone structural regime changes:
- **2020-2021:** Retail-driven mania, 10x BTC run, funding rates 30%+ annualized. Anything long made money.
- **2022:** Bear market, -76% BTC drawdown. Regime completely different from 2020-2021.
- **2023-2024:** Institutional era (ETF approvals), moderate trends, lower volatility, compressed funding.
- **2025-2026:** Post-ETF, low volatility, tight ranges, funding 2-8% annualized.

A strategy showing +40%/yr over 6 years but +2%/yr in the last 12 months tells you it captured 2020-2021 alpha that no longer exists. **You should expect the last 12 months to predict forward performance, not the full period.**

**Mandatory reporting format for all strategy evaluations:**

```
LAST 12 MONTHS (primary decision metric):
  Annual Return: XX%, Sharpe: X.XX, MaxDD: XX%

Full Period (context only — DO NOT use for deployment decisions):
  Annual Return: XX%, Sharpe: X.XX, MaxDD: XX%
```

**Project example (critical):** BTC Trend+Carry regime rotation showed **+32.9%/yr** over 6 years but only **+4.2%/yr** in the last 14 months (Jan 2025-Mar 2026). The 32.9% was inflated by 2020 (+47%) and 2023 (+76%) — exceptional years that skew the average. The "Heavy Carry" variant (best risk-adjusted) earned +4.2% OOS vs +32.9% full-period — an **8x overstatement** if you used full-period numbers. (Finding #140 in RESEARCH_STATUS)

**Another example:** Altcoin L/S carry (s85+s90) showed +16.1%/yr over 36 months but the 60-month number is +7.5%/yr because the earliest data includes 2021 funding compression post-bull. The 36mo number cherry-picked the best window.

**Rules:**
1. ALWAYS lead with last-12-month metrics in any report or assessment
2. NEVER use full-period annual return for deployment decisions
3. If last-12-month Sharpe < 0.3, the strategy is not production-ready regardless of full-period metrics
4. Funding carry rates are structurally declining (30% 2021 → 8% 2025 → 2% 2026 YTD) — do NOT use historical averages to project future carry income
5. When comparing strategies, compare their recent-12-month windows, not their full-period CAGRs

**BTC Funding Rate Trend (annualized, corrected):**

| Year | Annual Rate | Status |
|------|-----------|--------|
| 2020 | 17.2% | Bull ramp |
| 2021 | 30.7% | Peak mania |
| 2022 | 4.2% | Bear |
| 2023 | 6.6% | Recovery |
| 2024 | 22.7% | ETF + bull |
| 2025 | 8.2% | Cooling |
| 2026 YTD | 2.4% | Compressed |

Any carry strategy projecting >10%/yr should justify why future funding will exceed the 2025-2026 baseline.

---

## Quick Reference: Where to Change Things

| I want to... | Change this | In this file |
|--------------|-------------|-------------|
| Adjust Kelly multiplier for one strategy | `sizing_overrides: {"kelly_mult_override": 0.30}` | Config JSON |
| Cap spot position at 80% equity | `sizing_overrides: {"spot_max_equity_pct": 0.80}` | Config JSON |
| Limit portfolio to 20 positions | `max_portfolio_positions: 20` | Config JSON (PortfolioConfig) |
| Change walk-forward window | `train_bars: 4380` (182.5 days) | Config JSON (PortfolioConfig) |
| Use Hyperliquid fees | `exchange: "hyperliquid"` | Config JSON (PortfolioConfig) |
| Add a conviction-based exit | Return `conviction_score` array from strategy | Strategy code |
| Change indicator period (e.g., RSI 21) | Edit `compute_indicators_fast()` | `v4/engine.py` (affects ALL strategies) |
| Exclude a token from combined | Add to `NO_COMBINED` set | `v4/universe.py` |
| Change ADV curve shape | Override via `sizing_overrides`: `kelly_mult_floor` (0.05-0.40), `kelly_mult_range` (0.10-0.80), `cap_pct_floor` (0.005-0.08), `cap_pct_range` (0.02-0.30), `adv_scaling_divisor` (1.0-20.0) | `v4/config.py` (SAFETY_RAILS) |
| Cap compounding | Set `max_sizing_equity` | Config JSON (PortfolioConfig) |
| Change PBO threshold | `--pbo-threshold 0.30` | CLI flag for `v4/validation.py` |
