# R172 → v4 Engine: Configuration & Gaps Analysis

**Date:** 2026-03-28
**Strategies:** s400 (R162 cross-sectional momentum), s401 (R160 vol breakout)
**Target:** R172 standalone achieved +446.9% OOS, -18.0% MaxDD, Calmar 24.68

## Current v4 Results (12M, $200K, skip-wf, non-raw, proper sizing)

| Metric | s400 Alone | s401 Alone | R172 Standalone |
|--------|-----------|-----------|-----------------|
| Return | -4.3% | -83.5% | +446.9% |
| MaxDD | -58.1% | -94.2% | -18.0% |
| Trades | 140 | 900 | ~6,000+ |
| Avg Margin | $13,477 | $5,987* | $40-53K |
| Win Rate | 44.3% | 28.8% | ~50% |
| SMA Trail Exits | N/A | 889/900 (99%) | Primary exit |

*s401 avg margin is low because equity dropped to $32K (loss spiral)

---

## NOT Gaps — Properly Configurable at Strategy Level

### 1. Position Sizing ✅ SOLVED

**Problem we had:** Positions were 10-15x too small ($3-6K vs $40-53K target).

**Solution:** Module-level `SIZING_OVERRIDES` dict + StrategyResult fields:
```python
SIZING_OVERRIDES = {
    "kelly_mult_override": 0.50,   # Max Kelly multiplier (fixed, skip ADV curve)
    "target_vol": 0.05,            # Higher = larger positions
    "cap_pct_override": 0.15,      # Lift capital cap
}
# In StrategyResult:
cap_multiplier=3.0,       # Lift capital_cap so max_trade_pct binds
max_trade_pct=0.20,       # Hard cap: 20% of equity per position
```

**Reference docs:** `knowledge/V4_SIZING_PIPELINE.md`, `knowledge/V4_EXPERIMENTATION_GUIDE.md` §4

### 2. Leverage ✅ SOLVED

**Problem we had:** leverage=1.0 vs R172's 2.5x.

**Solution:** Just set `leverage=2.5` in StrategyResult. Engine fully supports per-bar
leverage arrays. This was never an engine gap — just a strategy config error.

### 3. Token Universe ✅ Configurable

**Problem:** 195 tokens including meme coins vs R172's curated 49.

**Solution:** Strategy-level filtering. Options:
- Filter by ADV in the strategy function (already have dvol filter in s400)
- `SIZING_OVERRIDES = {"min_adv_usd": 5_000_000}` to reject low-ADV tokens
- Filter by data history length (already in s400: `min_bars = LOOKBACK_BARS + WARMUP + 30*24`)

### 4. Custom Sizing Model ✅ Pluggable

The engine supports **custom sizing models** via the `SizingModel` protocol and
`_SIZING_MODELS` registry. If Kelly + overrides can't match R172's direct-weight
allocation, we can register a `FixedFractionSizing` or `DirectWeightSizing` model.

See `knowledge/V4_EXPERIMENTATION_GUIDE.md` §4 for implementation pattern.

---

## TRUE Engine Limitations (require code changes)

### Gap 1: Re-entry Prevention [SIGNIFICANT — s401 only]

**What:** `simulator.py:563` — `find_open_for_token_strategy()` prevents opening a
second position on the same token+strategy while one is open. Hardcoded, no config flag.

**Impact on s401:** R168 standalone R160 allowed concurrent positions per token (e.g.,
3 BTC breakout positions open simultaneously). The v4 engine blocks this. Combined with
max_positions=5, this reduces s401's trade count from ~5K potential to ~900 actual.

**Impact on s400:** Minimal. Portfolio strategy opens one position per token at each
weekly rebalance. Re-entry within the same week isn't part of the design.

**Fix required:** Add `allow_reentry: bool = False` to StrategyResult. When True, skip
the `find_open_for_token_strategy` check. Existing max_positions limit handles concurrency.

**Engine location:** `v4/simulator.py:563`

### Gap 2: Drawdown Control Overlay [MODERATE]

**What:** R172 had dynamic position sizing based on portfolio drawdown:
- DD < -5%: 75% size | DD < -10%: 50% | DD < -15%: 25% | DD < -20%: 0%

**Why it can't be done at strategy level:** Strategies precompute signals BEFORE
simulation. The `size_multiplier` array is set during signal generation, when portfolio
equity is unknown. DD control requires simulation-time access to evolving equity.

**Fix required:** Add a `dd_scaling` config to PortfolioConfig/SimConfig that the
simulator reads during the sizing step. Example:
```python
dd_scaling = [(0.05, 0.75), (0.10, 0.50), (0.15, 0.25), (0.20, 0.0)]
```

**Engine location:** `v4/simulator.py` sizing section (lines 840-880)

---

## Strategy-Level Issues (not engine gaps)

### Issue A: BB Breakout Unprofitable on 195 Tokens

s401 has 28.8% win rate and -83.5% return. The SMA trail exit works correctly (99% of
exits), but most breakouts reverse before producing a profit. This is likely because:
1. Many of the 195 tokens produce false breakouts (meme coins, low-liquidity)
2. The most recent 12 months may be unfavorable for breakout strategies
3. The curated 49-token universe in R172 excluded noisy tokens

**Fix:** Add token quality filters (min ADV, min history, exclude known meme tokens).

### Issue B: s400 Funding Drag

s400 has $57K in funding costs on $200K capital (28.6%!). This nearly eliminates the
strategy's gross alpha. The leverage amplifies funding: 2.5x leverage on a $13K short
position means $32.5K notional paying funding rates.

**Fix:** Consider reducing leverage for the short side, or implementing on spot+perp
combined to use spot for longs (no funding) and perp only for shorts.

### Issue C: s400 Selects Volatile New Tokens

s400's top tokens are PIPPIN, H, MYX, BAN — volatile new coins with extreme returns
that dominate the cross-sectional ranking. In R172 standalone, these were excluded by
the smaller universe.

**Fix:** Require minimum data history (e.g., 6 months) or use a pre-defined token list.

---

## Configuration Reference

### StrategyResult Fields for Sizing

| Field | What it controls | Our setting |
|-------|-----------------|-------------|
| `edge` | Kelly numerator | 0.40 (s401), 1.0 (s400) |
| `size_multiplier` | Scales kelly_frac | 1.0 (s401), per-token weight (s400) |
| `cap_multiplier` | Lifts capital_cap | 3.0 (s401), 5.0 (s400) |
| `max_trade_pct` | Hard cap on position | 0.20 (s401), 0.30 (s400) |
| `leverage` | Notional = margin × leverage | 2.5 (both) |

### SIZING_OVERRIDES (Module-Level Dict)

| Key | Default | Our setting | Safety rails |
|-----|---------|-------------|--------------|
| `kelly_mult_override` | 0 (use ADV curve) | 0.50 | 0.05-0.50 |
| `target_vol` | 0.02 | 0.05 | 0.005-0.05 |
| `cap_pct_override` | 0 (use ADV curve) | 0.15 | 0.01-0.15 |

### Sizing Formula

```
raw_kelly   = strategy_equity × (kelly_mult × edge × size_multiplier) × (target_vol / volatility)
capital_cap = strategy_equity × cap_pct × cap_multiplier
pos_usd     = min(raw_kelly, capital_cap, adv_cap, max_trade_pct × equity)
notional    = pos_usd × leverage
```

---

## Summary: What Needs Engine Changes

| Item | Type | Priority | Effort |
|------|------|----------|--------|
| Re-entry prevention flag | New StrategyResult field + simulator check | HIGH | Small (10 lines) |
| DD control scaling | New PortfolioConfig field + simulator sizing | MEDIUM | Medium (50 lines) |

Everything else is configurable. The current poor performance is a STRATEGY issue
(unprofitable signal on broad universe), not an engine limitation.
