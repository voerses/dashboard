# V4 Strategy Cookbook — Practical Guide for Researchers

> **Purpose:** Step-by-step guide for creating, configuring, and running strategies
> in the v4 portfolio backtest engine. Start here before diving into the detailed
> reference docs (V4_ENGINE.md, V4_EXPERIMENTATION_GUIDE.md, V4_SIZING_PIPELINE.md).
>
> **Last updated:** 2026-04-03

---

## 1. Strategy File Templates

Every strategy lives in `strategies/sNNN_description.py`.

### Research Template (Gate 1 Quick Testing)

For rapid signal hypothesis testing, use `strategies/RESEARCH_TEMPLATE.py`:

```bash
# 1. Copy the research template
cp strategies/RESEARCH_TEMPLATE.py strategies/sNNN_my_signal.py

# 2. Edit PARAMETERS section (top of file) and signal logic

# 3. Run through v4 engine — pure signal quality
python v4/portfolio_backtest.py \
    --strategy sNNN \
    --market perp \
    --months 12 \
    --capital 100000 \
    --raw --skip-wf

# 4. If signal looks good, add walk-forward check
python v4/portfolio_backtest.py \
    --strategy sNNN \
    --market perp \
    --months 12 \
    --capital 100000 \
    --raw

# 5. Full production pipeline
python v4/portfolio_backtest.py \
    --strategy sNNN \
    --market perp \
    --months 12 \
    --capital 100000
```

The research template has a PARAMETERS block at the top for quick iteration:
`LOOKBACK`, `THRESHOLD`, `DIRECTION`, `LEVERAGE`, `MAX_HOLD`, `STOP_MULT`, `EDGE`, etc.
Edit parameters, re-run CLI — no code changes needed for basic sweeps.

### Production Templates

For strategies graduating past Gate 3, use `strategies/TEMPLATE.py` which has the full
6-layer signal stack (regime, trend, entry, volume, exit, sizing).

### Per-Token Strategy (Class A)
```python
"""
sNNN Strategy Description

Hypothesis: ...
Market: SPOT | PERP | COMBINED
Status: EXPERIMENTAL
"""

import numpy as np
from engine import StrategyContext, StrategyResult, MarketType

STRATEGY_TYPE = "per_token"  # Class A: called once per token

# Declare what indicators your strategy needs (see Section 3)
REQUIRED_PLUGINS = []                    # Named plugins from engine.py
REQUIRED_INDICATOR_GROUPS = set()        # Indicator groups (empty = core only)

# Optional: sizing overrides, drawdown scaling, portfolio config
SIZING_OVERRIDES = {}
DD_SCALING = []

def strategy(ctx: StrategyContext) -> StrategyResult:
    """Called once per token with that token's data."""
    close = ctx.ind_1h["close"]
    n = len(close)

    # Your signal logic here (all signals must be shifted by 1 bar!)
    signal = np.zeros(n, dtype=bool)
    # signal[i] = True means "enter at bar i+1" — NOT at bar i

    direction = np.ones(n, dtype=np.int8)  # 1=long, -1=short

    return StrategyResult(
        entry_mask=signal,
        direction=direction,
        market_type=MarketType.PERP,
        leverage=1.25,
        stop_mult=2.0,          # Stop at 2 ATR
        trail_mult=3.0,         # Trail at 3 ATR
        target_mult=999.0,      # No profit target
        no_stop_bars=2,         # No stop for first 2 bars
        min_hold=1,
        max_hold=24,            # Max 24 hours
        edge=0.35,              # Signal edge (affects sizing)
        name="sNNN_description",
    )
```

### Portfolio Strategy (Class B)
```python
"""
sNNN Portfolio Strategy Description

Hypothesis: ...
Market: PERP
Status: EXPERIMENTAL
"""

import numpy as np
from engine import StrategyContext, StrategyResult, MarketType

STRATEGY_TYPE = "portfolio"  # Class B: called once with ALL tokens

# Portfolio-specific configuration (REQUIRED for Class B!)
PORTFOLIO_CONFIG = {
    "max_positions": 50,              # Per-strategy position limit
    "conviction_mode": "ranked",      # "shuffle" | "ranked" | "hybrid"
    "entry_resolution": 0,            # 0=hourly, 1=1-minute (paper only)
    "max_portfolio_positions": 50,    # Portfolio-wide limit
}

REQUIRED_PLUGINS = []
REQUIRED_INDICATOR_GROUPS = set()
SIZING_OVERRIDES = {}
DD_SCALING = []

def strategy(contexts: dict) -> dict:
    """Called once with {token: ctx} or {token: (ctx_spot, ctx_perp)}.

    Returns: {token: StrategyResult} for tokens with entry signals.
    """
    results = {}
    for token in sorted(contexts.keys()):
        ctx_pair = contexts[token]
        if isinstance(ctx_pair, tuple):
            ctx = ctx_pair[1] if ctx_pair[1] is not None else ctx_pair[0]
        else:
            ctx = ctx_pair

        close = ctx.ind_1h["close"]
        n = len(close)

        # Your cross-sectional signal logic here
        entry_mask = np.zeros(n, dtype=bool)
        direction = np.ones(n, dtype=np.int8)

        if not np.any(entry_mask):
            continue

        results[token] = StrategyResult(
            entry_mask=entry_mask,
            direction=direction,
            market_type=MarketType.PERP,
            leverage=1.25,
            stop_mult=2.0,
            trail_mult=3.0,
            target_mult=999.0,
            min_hold=1,
            max_hold=8,
            edge=0.35,
            name="sNNN_description",
        )

    return results
```

---

## 2. Module-Level Attributes Reference

These are read by `portfolio_backtest.py` at load time:

| Attribute | Type | Required | Default | Purpose |
|-----------|------|----------|---------|---------|
| `STRATEGY_TYPE` | str | Yes | `"per_token"` | `"per_token"` (Class A) or `"portfolio"` (Class B) |
| `REQUIRED_PLUGINS` | list[str] | No | ALL plugins | Which indicator plugins to compute |
| `REQUIRED_INDICATOR_GROUPS` | set[str] | No | ALL groups | Which indicator groups to compute |
| `PORTFOLIO_CONFIG` | dict | No | {} | Portfolio-level settings (see below) |
| `SIZING_OVERRIDES` | dict | No | {} | Override SizingDefaults fields |
| `DD_SCALING` | list[tuple] | No | [] | Drawdown scaling thresholds |
| `MAX_CONCURRENT_PER_TOKEN` | int | No | 1 | Max concurrent positions per token |
| `REGIME_PARAMS` | dict | No | None | Custom regime detection params |

### PORTFOLIO_CONFIG Keys

| Key | Type | Default | Purpose |
|-----|------|---------|---------|
| `max_positions` | int | 40 (or split) | Per-strategy position limit |
| `max_portfolio_positions` | int | 40 | Portfolio-wide position limit |
| `conviction_mode` | str | `"shuffle"` | Entry ordering: shuffle/ranked/hybrid |
| `entry_resolution` | int | 0 | 0=hourly, 1=1-minute (paper trading) |

### SIZING_OVERRIDES Keys (with Safety Rails)

| Key | Default | Range | Purpose |
|-----|---------|-------|---------|
| `kelly_mult_override` | 0.0 (disabled) | [0.05, 0.50] | Fixed Kelly multiplier |
| `cap_pct_override` | 0.0 (disabled) | [0.01, 0.30] | Fixed capital allocation % |
| `target_vol` | 0.02 | [0.005, 0.05] | Vol scaling numerator |
| `min_adv_usd` | 500,000 | [100K, 10M] | Minimum ADV to trade |
| `edge_minimum` | 0.10 | [0.05, 0.50] | Min edge to enter |
| `spot_max_equity_pct` | 1.0 | [0.10, 1.0] | Max spot position % |
| `kelly_mult_floor` | 0.15 | [0.05, 0.40] | ADV curve floor |
| `kelly_mult_range` | 0.35 | [0.10, 0.80] | ADV curve range |
| `cap_pct_floor` | 0.02 | [0.005, 0.08] | Cap curve floor |
| `cap_pct_range` | 0.10 | [0.02, 0.30] | Cap curve range |

---

## 3. Indicator System — What's Available

### Core Indicators (always computed, no declaration needed)

Available in `ctx.ind_1h`, `ctx.ind_4h`, `ctx.ind_d`:

| Field | Description |
|-------|-------------|
| `close` | Closing price |
| `high` | High price |
| `low` | Low price |
| `volume` | Trading volume |
| `atr` | Average True Range (14-bar) |
| `vol_20` | 20-bar rolling volatility |
| `ret_1h` | 1-hour log return |

### Indicator Groups (opt-in via `REQUIRED_INDICATOR_GROUPS`)

Add group names to the set. Empty set = core only (fastest).

| Group | Fields Added | Use When |
|-------|-------------|----------|
| `ema` | `ema_5`, `ema_10`, `ema_20`, `ema_50`, `ema_200` | Trend following, MA crossovers |
| `macd` | `macd`, `macd_signal`, `macd_histogram` | Momentum/trend signals |
| `rsi` | `rsi` (14-bar) | Mean reversion, overbought/oversold |
| `bb` | `bb_upper`, `bb_lower`, `bb_width`, `bb_middle` (20,2) | Breakout, squeeze detection |
| `adx` | `adx`, `di_plus`, `di_minus` (14-bar) | Trend strength filtering |
| `volume` | `vol_ratio` (current/20-bar SMA) | Volume confirmation |
| `donchian` | `donch_high`, `donch_low` (20-bar) | Channel breakouts |

**Example:** `REQUIRED_INDICATOR_GROUPS = {'bb', 'volume'}` — only compute BB bands and vol ratio.

### Named Plugins (opt-in via `REQUIRED_PLUGINS`)

Plugins add fields to `ctx.custom` (not `ctx.ind_1h`).

| Plugin | Fields | Dependencies | Use When |
|--------|--------|-------------|----------|
| `obv` | `obv`, `obv_slope` | None | Volume-weighted price confirmation |
| `vwap` | `vwap_dev` | None | Mean reversion to VWAP |
| `momentum` | `ret_6h`, `ret_12h`, `ret_24h`, `ret_48h`, `ret_120h` | None | Multi-period momentum |
| `enriched` | `enr_*` (dynamic) | None | Enriched features (if data available) |
| `obv_divergence` | `obv_slope`, `price_slope` | **Requires:** `obv` plugin | Accumulation/distribution divergence |
| `momentum_accel` | `momentum_accel` | **Requires:** `momentum` plugin | Second derivative of price |
| `funding_zscore` | `funding_zscore` | None | Funding rate extremes (perp only) |
| `squeeze` | `squeeze_intensity` | **Requires:** `bb` group | Bollinger squeeze detection |
| `multi_tf` | `multi_tf_score` | **Requires:** `macd` + `ema` groups | Multi-timeframe trend alignment |
| `positioning` | `pos_mult` | None | Top trader positioning (if data available) |
| `vrp` | `vrp_mult` | None | Volatility risk premium |

**Example:** `REQUIRED_PLUGINS = ['funding_zscore', 'momentum']` — compute funding z-score + multi-period returns.

**Dependency Rules:**
- `obv_divergence` requires `obv` in REQUIRED_PLUGINS
- `momentum_accel` requires `momentum` in REQUIRED_PLUGINS
- `squeeze` requires `'bb'` in REQUIRED_INDICATOR_GROUPS
- `multi_tf` requires `'macd'` AND `'ema'` in REQUIRED_INDICATOR_GROUPS

---

## 4. Running Backtests — CLI Reference

```bash
python v4/portfolio_backtest.py --strategy <id> --market <type> [options]
```

### Required Arguments

| Flag | Description |
|------|-------------|
| `--strategy` | Strategy ID(s), comma-separated (e.g. `s400` or `s30,s32`) |
| `--market` | Market type: `spot`, `perp`, or `combined` |

### Common Options

| Flag | Default | Description |
|------|---------|-------------|
| `--months` | 12 | Lookback period in months |
| `--capital` | 200000 | Initial capital (comma-separated for sweep) |
| `--exchange` | binance | Exchange for fees/margin |
| `--raw` | off | Skip portfolio constraints (pure signal quality) |
| `--skip-wf` | off | Skip walk-forward masking (use for fixed-param strategies) |
| `--oos-monthly` | off | Month-by-month OOS with hard data cap |

### Advanced Options

| Flag | Default | Description |
|------|---------|-------------|
| `--concentration` | 0.10 | Per-token max position (% of equity) |
| `--adv-cap` | 0.05 | Hard cap (% of rolling ADV per position) |
| `--max-positions` | (strategy) | Override per-strategy position limit |
| `--max-portfolio-positions` | 40 | Portfolio-wide position limit |
| `--conviction-mode` | (strategy) | Override: `shuffle`, `ranked`, `hybrid` |
| `--seed` | 42 | Random seed for reproducibility |
| `--output` | results/v4 | Output directory for JSON results |
| `--refresh` | off | Fetch fresh data before running |

### Diagnostic Modes

```bash
# Pure signal quality — no portfolio constraints, no walk-forward
python v4/portfolio_backtest.py --strategy s400 --market perp --raw --skip-wf

# Walk-forward impact test — portfolio constraints ON, WF OFF
python v4/portfolio_backtest.py --strategy s400 --market perp --skip-wf

# Full production pipeline — all guardrails ON
python v4/portfolio_backtest.py --strategy s400 --market perp

# Gold-standard OOS — month-by-month with hard data cap
python v4/portfolio_backtest.py --strategy s400 --market perp --oos-monthly
```

**Flag interaction:**
- `--raw` and `--skip-wf` are **orthogonal** (independent controls)
- `--raw` disables portfolio constraints (position limits, concentration, capital checks)
- `--skip-wf` disables walk-forward training window burn
- Combine both for "pure strategy debug mode"

---

## 5. StrategyResult Fields — Complete Reference

| Field | Type | Default | Purpose |
|-------|------|---------|---------|
| `entry_mask` | ndarray[bool] | **required** | True at bars where entry signal fires |
| `direction` | ndarray[int8] | **required** | 1=long, -1=short per bar |
| `market_type` | MarketType | **required** | SPOT, PERP, or COMBINED |
| `leverage` | float/ndarray | 1.0 | Position leverage (perp only) |
| `edge` | float | 0.35 | Signal edge (affects Kelly sizing) |
| `name` | str | "" | Strategy identifier |
| **Stops/Exits** | | | |
| `stop_mult` | float | 2.0 | Fixed stop at N ATR (999=disabled) |
| `trail_mult` | float | 3.0 | Trailing stop at N ATR (999=disabled) |
| `target_mult` | float | 999.0 | Take-profit at N ATR (999=disabled) |
| `breakeven_atr` | float | 0.0 | Move stop to breakeven after N ATR profit |
| `no_stop_bars` | int | 0 | Skip stops for first N bars after entry |
| `min_hold` | int | 1 | Minimum hold bars before any exit |
| `max_hold` | int | 720 | Maximum hold bars (force exit) |
| `bear_max_hold` | int | 0 | Max hold in bear regime (0=use max_hold) |
| **Partial Profit** | | | |
| `partial_tp_atr` | float | 0.0 | Take partial at N ATR profit (0=disabled) |
| `partial_tp_pct` | float | 0.5 | Close this fraction at partial TP |
| `partial_tp_trail` | float | 0.0 | Trail remainder at N ATR after partial |
| **Sizing** | | | |
| `size_multiplier` | float/ndarray | 1.0 | Scales Kelly position size (per bar) |
| `cap_multiplier` | float/ndarray | 1.0 | Scales capital allocation cap |
| `max_trade_pct` | float | 0.0 | Hard cap: pos <= equity * this (0=disabled) |
| `conviction_score` | ndarray[float] | None | Entry prioritization (for ranked/hybrid modes) |
| **Regimes** | | | |
| `exit_regimes` | set | set() | Exit on these regimes (CRISIS, DOWNTREND, etc.) |
| **Paper Trading** | | | |
| `entry_limit_price` | ndarray | None | NaN=market order, value=limit price |
| `armed_levels` | ndarray | None | Pre-entry monitoring levels |
| `armed_direction` | ndarray[int8] | None | Direction for armed entries |
| `exchange` | str | "binance" | Exchange name |

---

## 6. Common Pitfalls (Lessons Learned)

### Pitfall 1: Missing PORTFOLIO_CONFIG (Class B strategies)

**Symptom:** Low trade count, random entry ordering, default position limits.

**Fix:** Always add `PORTFOLIO_CONFIG` to portfolio strategies:
```python
PORTFOLIO_CONFIG = {
    "max_positions": 50,
    "conviction_mode": "ranked",
    "max_portfolio_positions": 50,
}
```

Without it, defaults to `max_positions=40`, `conviction_mode="shuffle"` — fine for
some strategies but wrong for ranked cross-sectional approaches.

### Pitfall 2: Perp Funding Costs Destroy Multi-Day Holds

**Symptom:** Positive gross PnL but massive net losses.

**Root cause:** Altcoin perp funding rates run 30-100%+ annualized. At 2.5x leverage
holding 7 days, funding costs consume 40%+ of capital per year.

**Rules of thumb:**
- Hold < 8h on perps → funding drag ~$2K/yr on $100K
- Hold 24h on perps → funding drag ~$10K/yr on $100K
- Hold 168h on perps → funding drag ~$40K+/yr on $100K

**Fix:** Use `max_hold <= 8` for perp strategies, OR trade spot (no funding).

### Pitfall 3: Walk-Forward Mask Kills Fixed-Parameter Strategies

**Symptom:** 90% of entries killed, very few trades in L12M.

**Root cause:** Walk-forward burns first 365 days + 7-day purge windows. Strategies
with fixed parameters (no optimization) don't need this protection.

**Fix:** Use `--skip-wf` for strategies with no in-sample parameter fitting.

### Pitfall 4: No Stop Loss + High Leverage = Liquidations

**Symptom:** 10-15% of trades end in liquidation.

**Root cause:** `stop_mult=999.0` (effectively no stop) + `leverage=2.5`. Volatile
altcoins can drop 40%+ in a week, triggering margin liquidation.

**Fix:** Always use a real stop loss. `stop_mult=2.0` to `3.0` is typical.
Lower leverage (1.25x) also helps.

### Pitfall 5: Kelly Sizing Produces Smaller Positions Than Expected

**Symptom:** Research targets 27% per position but Kelly gives 9%.

**Root cause:** The Kelly formula multiplies `kelly_mult * edge * size_multiplier * vol_adj`.
All factors < 1.0 compound multiplicatively. At typical 3% vol:
`0.50 * 1.0 * 0.267 * (0.02/0.03) = 0.089` → 8.9% not 26.7%.

**Fix:** Either accept Kelly's conservative sizing (it's designed to prevent ruin)
or increase `cap_pct_override` and `cap_multiplier` to lift caps. Don't fight
Kelly by pushing `kelly_mult_override` to max — it has safety rails for a reason.

### Pitfall 6: Intra-Bar Entries (FATAL — Rule 14)

**Symptom:** Strategy looks amazing in research, dies in production.

**Root cause:** Signal computed on bar T's CLOSE, then entering at a price within
bar T. This is look-ahead bias — at bar T's close, you can't go back in time.

**Rule:** Signal at bar T can ONLY enter at bar T+1 or later. Shift all signals
by 1 bar: `signal_shifted = np.roll(signal, 1); signal_shifted[0] = False`.

### Pitfall 7: Liquidity Mask Kills Altcoin Signals

**Symptom:** 38% of entries killed by liquidity filter.

**Root cause:** Engine applies $500K minimum ADV gate with 90-day burn-in. Strategy
also applies its own volume filter → double filtering.

**Fix:** If your strategy has its own volume filter, consider raising or lowering
`min_adv_usd` in `SIZING_OVERRIDES` to avoid double-gating:
```python
SIZING_OVERRIDES = {"min_adv_usd": 200_000}  # Lower if strategy filters itself
```

---

## 7. Conviction Modes Explained

Controls how entries are ordered when multiple signals fire on the same bar.

### shuffle (default)
Random order. Best for diversification, worst for capital efficiency.
No `conviction_score` needed.

### ranked
Highest conviction first. Requires `conviction_score` array in StrategyResult.
Best when you have a clear signal strength metric.
```python
conviction = np.zeros(n, dtype=np.float64)
conviction[strong_signal] = 0.8
conviction[weak_signal] = 0.3
# Pass in StrategyResult:
StrategyResult(..., conviction_score=conviction)
```

### hybrid
Buckets entries into tiers (high >=0.66, medium >=0.33, low), shuffles within
each tier. Compromise between ranked and shuffle.

---

## 8. Sizing Pipeline Quick Reference

```
Position Size = min(raw_kelly, capital_cap, adv_cap, max_trade_cap)

Where:
  raw_kelly   = strategy_equity * kelly_frac * vol_adj
  kelly_frac  = kelly_mult * edge * size_multiplier
  vol_adj     = target_vol / max(volatility, 0.005)

  capital_cap = strategy_equity * cap_pct * cap_multiplier
  adv_cap     = rolling_adv * 0.05
  max_trade   = strategy_equity * max_trade_pct  (if > 0)
```

**Sizing Overrides cheat sheet:**
- Want bigger positions? → Increase `cap_pct_override` or `cap_multiplier`
- Want positions to ignore ADV? → Increase `cap_multiplier` (but ADV hard cap still applies)
- Want fixed Kelly? → Set `kelly_mult_override` (0.05-0.50)
- Want more vol-sensitive sizing? → Increase `target_vol` (0.005-0.05)

---

## 9. Exit System Quick Reference

Exits are checked BEFORE entries each bar, in this order:

1. **Data end** — token data stopped → force close
2. **Funding accrual** — perp positions charged funding each bar
3. **Liquidation** — perp margin check, force close if underwater
4. **Stop loss** — if `close < entry - stop_mult * entry_atr`
5. **Trailing stop** — if `close < high_water - trail_mult * entry_atr`
6. **Target** — if `close > entry + target_mult * entry_atr`
7. **Partial TP** — close fraction at `partial_tp_atr * entry_atr` profit
8. **Max hold** — force exit after `max_hold` bars
9. **Regime exit** — if current regime in `exit_regimes`

Set any mult to 999.0 to disable. Use `no_stop_bars` to skip stops early in trade.

---

## 10. Diagnostic Workflow

When a strategy underperforms, run this diagnostic sequence:

```bash
# Step 1: Pure signal quality (no engine interference)
python v4/portfolio_backtest.py --strategy sNNN --market perp --raw --skip-wf

# Step 2: Add walk-forward (is signal overfitted?)
python v4/portfolio_backtest.py --strategy sNNN --market perp --raw

# Step 3: Add portfolio constraints (do constraints kill it?)
python v4/portfolio_backtest.py --strategy sNNN --market perp --skip-wf

# Step 4: Full pipeline (production reality)
python v4/portfolio_backtest.py --strategy sNNN --market perp
```

**Interpretation:**
- Step 1 bad → signal has no edge. Kill it.
- Step 1 good, Step 2 bad → signal is overfitted. Reduce parameters.
- Steps 1-2 good, Step 3 bad → portfolio constraints kill it. Tune PORTFOLIO_CONFIG.
- Steps 1-3 good, Step 4 bad → walk-forward is too aggressive. Use `--skip-wf` if fixed params.

**Check the output diagnostics** for rejection reasons:
```json
"rejections": {
  "portfolio_limit": 42,   // Too many positions? Increase max_portfolio_positions
  "strategy_limit": 15,    // Per-strategy cap? Increase max_positions in PORTFOLIO_CONFIG
  "min_size": 0,           // Positions too small? Increase edge or kelly_mult
  "adv_cap": 8,            // ADV cap binding? Lower min_adv_usd or accept fewer positions
}
```

---

## Related Documentation

- **Architecture deep dive:** `knowledge/V4_ENGINE.md`
- **Experimentation recipes:** `knowledge/V4_EXPERIMENTATION_GUIDE.md` (58KB, comprehensive)
- **Sizing pipeline:** `knowledge/V4_SIZING_PIPELINE.md`
- **Bias audit:** `knowledge/RESEARCHER_BEST_PRACTICES.md` (Rule 14: no intra-bar entries)
- **Gate pipeline:** `knowledge/STRATEGY_QUICK_REFERENCE.md`
