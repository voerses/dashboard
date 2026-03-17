# Strategy Development Quick Reference

> Single-file gate reference. Load this instead of 15 separate files.
> Deep dives linked where needed — read them only when investigating specifics.
>
> **v3/ is FROZEN LEGACY.** All engine, validation, and simulation code lives in v4/.
> Do NOT modify any v3/ files. Strategy modules (`v3/cross_sectional.py`, etc.) still
> live in v3/ but are read-only — new strategies go in `strategies/sNN_*.py` or `v4/`.

---

## Available Capabilities (Read Before Ideating)

Before proposing a strategy, know what tools and strategy types exist.

### Strategy Types

| Type | Module | Example | Validated? |
|------|--------|---------|------------|
| Per-token signal | `strategies/sNN_*.py` | s11 momentum burst (Sharpe 2.58) | Yes — 6 Tier A |
| Per-token perp | `strategies/sNN_*.py` | s29 funding carry (Sharpe 0.81, market-neutral) | Yes — Tier B |
| Combined spot+perp | `strategies/sNN_*.py` | s30 basis carry (Sharpe 2.63, Calmar 12.76) | Yes — 3 Tier A |
| Cross-sectional ranking | `v3/cross_sectional.py` | Long top quintile by 14d return (Sharpe 1.54, corr +0.25 vs S11) | Yes — Tier A diversifier |
| Sector rotation | `v3/sector_rotation.py` | Long top 2 of 10 sectors by category momentum (Sharpe 1.31, corr +0.20) | Yes — Tier A diversifier |
| Pairs / stat arb | `v3/pairs_trading.py` | Cointegrated pairs on perps, z-score entry (Sharpe 0.42, corr -0.06) | Yes — Tier B diversifier |

### Overlays (Improve Existing Strategies)

| Overlay | Module | Effect |
|---------|--------|--------|
| Regime weighting | `v3/regime_analysis.py` | Scale allocation by BTC regime. S11+S09: Sharpe +0.29, DD +4.5pp |
| Signal agreement | `v3/signal_agreement.py` | AND/N-of-M gating. S11+S09 AND: trades -66%, Calmar +0.46, DD +6.7pp |
| Regime sizing (O2) | wrapper strategy | `size_multiplier` per regime. s34 (s11+O2+O3): Sharpe +0.27, PF +0.08 |
| Weekend reduction (O3) | wrapper strategy | Reduce size Fri 20:00–Sun 20:00. Part of s34 wrapper |
| Time-decayed trail | wrapper strategy | Tighten stop by hold time. s69 (s56): Calmar +33%. s72 (s65): Calmar +148% |
| Fixed take-profit | wrapper strategy | Lock in profits at Nx ATR. s75 (s63, TP=3x): Calmar +17.6% |
| Partial profit-taking | engine field | Close fraction at target, trail remainder. s76 (s56): +8.9% solo |
| Dynamic regime weights | `v4/dynamic_weights.py` | Per-tick strategy weighting by regime. super5-dyn: +28% return |
| Conviction entry scoring | `v4/simulator.py` | Rank entries by conviction (from size_multiplier). 0% seed sensitivity |

**Overlay implementation rule (AIPIP-0018):** NEVER modify base strategies. Create a new
wrapper file (`strategies/sNN_name.py`) that imports the base, calls `base_strategy(ctx)`,
applies overlay logic, and returns a modified `StrategyResult`. See Gate 3O in SKILL.md.

**Delta-neutral exemption:** Strategies like s30 basis carry (long spot + short perp) do
NOT benefit from directional overlays (weekend sizing, regime sizing). Both legs hedge
each other. Skip directional overlays at Gate 0 for delta-neutral bases.

### Engine Overlay Support

| Field | Type | Default | Purpose |
|-------|------|---------|---------|
| `size_multiplier` | `float` or `np.ndarray` | `1.0` | Strategy-configured sizing overlay. Applied to Kelly mult in `_simulate` and `_simulate_combined`. |
| `conviction_score` | `Optional[np.ndarray]` | `None` | Per-bar [0,1] signal strength. Auto-derived from size_multiplier if not set. Used for entry ordering. |
| `partial_tp_trail` | `Optional[np.ndarray]` | `None` | Partial profit-taking: close fraction at target, trail remainder with tighter stop. |
| `time_trail_schedule` | `Optional[np.ndarray]` | `None` | Time-decayed trail tightening by hold duration. |
| `funding_exit_threshold` | `float` | `0.0` | Exit if cumulative funding / margin exceeds threshold. Disabled by default (KILLED at 5O). |
| `market_type` (per-bar) | `int` or `np.ndarray` | scalar | Per-bar venue routing: SPOT(0) or PERP(1) per entry. Engine routes fees/funding/prices per position. No current use case (KILLED at 5O — spot fees 2x perp). |

### V4 Per-Strategy Risk Controls (StrategySpec)

| Field | Default | Purpose |
|-------|---------|---------|
| `circuit_breaker_r` | `0.0` | Emergency exit at Nx initial risk (e.g. 4.0). 0=disabled. Essential for s59/s80. |
| `pump_filter_funding_zscore` | `0.0` | Block long entries when funding z-score > threshold (e.g. 3.0). 0=disabled. |
| `pump_filter_range_threshold` | `0.0` | Block entries when bar range/ATR > threshold (e.g. 4.0). 0=disabled. Only helps s60. |

**Current live settings (0d59ff4):** s59/s80: CB=4.0. s56/s57/s65/s69/s72/s76/s81: funding=3.0. s60: funding=3.0 + range=4.0. s62/s63/s75: no filters.

**JSON loading:** Always use `StrategySpec.from_dict(d)` when parsing dicts. Never construct StrategySpec manually from JSON — fields will be silently dropped.

### V4 Portfolio Config Options

| Field | Default | Purpose |
|-------|---------|---------|
| `conviction_mode` | `"shuffle"` | Entry ordering: `shuffle` (random), `ranked` (conviction descending), `hybrid` (3 tiers) |
| `min_conviction_threshold` | `0.0` | Skip entries below this conviction score |

### Paper Trader Operations

| Command | What it does |
|---------|-------------|
| `python -m v4.run_paper_multi --config <cfg>` | Start continuous paper trading (hourly ticks) |
| `python -m v4.run_paper_multi --config <cfg> --once` | Single tick then exit |
| `python -m v4.run_paper_multi --config <cfg> --refresh` | Live price refresh + dashboard push (no tick/trading, ~22s) |
| `python -m v4.run_paper_multi --config <cfg> --status` | Print equity/positions for all pools |
| `python -m v4.run_paper_multi --config <cfg> --fetch-only` | Fetch data only (no tick) |
| `python -m v4.run_paper_multi --config <cfg> --prices` | Print current token prices |

### Portfolio Tools

| Tool | Module | Purpose |
|------|--------|---------|
| Portfolio simulation | `v3/portfolio.py` | Shared cash pool, realistic capital, concentration caps |
| Correlation analysis | `v3/correlation.py` | Pairwise strategy corr, marginal Sharpe, greedy portfolio selection |
| Dynamic universe | `v3/dynamic_universe.py` | Point-in-time token eligibility at each WF window |

### Market Capabilities

| Capability | Details |
|-----------|---------|
| Spot trading | Long only, Binance 116 tokens |
| Perpetual futures | Long AND short, leverage, funding rates |
| Combined spot+perp | Dual-leg strategies: simultaneous, conditional, alternating. 111 tokens with both spot+perp data |
| Exchanges | Binance, Kraken, Hyperliquid (different fee tiers) |
| Data | 1H candles, 2020-2026. Spot: `data/spot/1h_cache/`. Perp: `data/perp/1h_cache/` |

### Signal Discovery Engine

| Capability | Module | Details |
|-----------|--------|---------|
| Automated IC testing | `tools/signal_discovery/` | 300+ features × 55 tokens × 5 horizons, FDR correction |
| Per-token signal catalogs | `outputs/signal_discovery/` | 252 aggregate + 55 per-token signal catalogs |
| Rolling IC health | `outputs/signal_discovery/rolling_ic_summary*.csv` | STABLE/DECAYING/STRENGTHENING/DEAD classification |
| Lead/lag causality | `outputs/signal_discovery/lead_lag*.json` | Forward vs reverse IC asymmetry (57 LEADING, 16 HIGH conf) |
| IC decay curves | `outputs/signal_discovery/ic_decay_curves*.json` | Peak horizon, half-life, sign consistency |
| Signal portfolio | `tools/signal_portfolio/` | Token clustering, IC-weighted composites, strategy generation |

### Key Constraints

- **IC != tradeable edge** — discovered signals only work as overlays on proven strategies, never standalone
- **All 6 Tier A per-token strategies are momentum variants** (median pairwise r=+0.62, effective N=2.02)
- To improve portfolio, need **different strategy families**, not more momentum
- Cross-sectional (+0.25), sector rotation (+0.20), pairs (-0.06) provide genuine diversification
- **Combined strategies unlocked diversification:** s30 basis carry (corr +0.21 vs s11), s29 funding carry (corr -0.16 vs s11) are different families
- Mean reversion consistently fails at 18-720hr holds in crypto — don't retry
- **s31 hedged momentum is redundant with s11** (corr +0.71) — don't run both
- **5x leverage kills all strategies** — use 1x with aggressive sizing (size_mult=3, cap_mult=15) instead
- **Regime-conditional EMAs decay fast** — prefer cross-TF signals (STABLE over time)
- **Four edge families validated in V4**: momentum (s56), basis carry (s57), counter-trend (s63), funding carry (s65). Next families to explore: cross-sectional, volatility harvesting, pairs/stat arb.
- **Spot venue routing is a dead end** — tested 5 strategies across carry + momentum classes, ALL killed. Carry longs RECEIVE funding (removing it costs 34% return). Spot fees are 2x perp fees on Binance (0.1% vs 0.05%), overwhelming funding savings for short-hold strategies. Don't retry unless fee structure changes or strategy holds >7 days.

### Strategy Classes (Gate 0 Routing)

| Class | Gate Path | When to Use |
|-------|-----------|-------------|
| A. Per-Token Signal | 0→1→2→3→4→5→6→7 | Single signal on individual tokens (spot or perp) |
| A2. Combined Spot+Perp | 0→1→2→3→4→5→5.5→6→7 | Dual-leg: long spot + short perp, or regime-adaptive instrument selection |
| B. Portfolio Strategy | 0→2→3P→5P→6→7 | Cross-token ranking, sector rotation, pairs (V3 modules) |
| C. Overlay | 0→2→3O→5O→6→7 | Regime weighting, signal agreement, risk scaling |
| D. V4 Portfolio Strategy | 0→2→V4-3→V4-4→V4-5→6→7 | V4 portfolio components, sideways complements, perp strategies |

**V4 vs V3 routing:** Use V4 (Class D) for portfolio components targeting s58,
perp/combined bidirectional strategies, and sideways/choppy complements. Use V3 (Class B)
for independent portfolio strategies and per-token robustness testing.

**Combined strategy patterns (Class A2):**
- **Simultaneous:** Both legs enter/exit together (e.g., s30 basis carry)
- **Conditional secondary:** Primary always, secondary only when condition met (e.g., s31 hedged momentum)
- **Alternating:** One or the other based on regime (e.g., s32 regime spot/perp)

**Combined validation:** `python v4/validation.py --strategy sNN --market combined --workers 4`

> Deep dive: `memory/PROJECT_STATUS.md` (full capability inventory + open tasks)

---

## Existing Tier A/B Strategies (Dedup Check)

### Per-Token (Spot)

| Strategy | Tier | Rate | Core Entry Signal | Hold |
|----------|------|------|-------------------|------|
| s11 Momentum Burst | A | 75.5% | `ret_1 > 0.03` (momentum burst) | 18-720h |
| s09 Optimized Trend | A | 73.5% | EMA stack + daily EMA50 + ADX > 30 | trend-follow |
| s13 Vol-Weighted TSMOM | A | 67.3% | Volume-weighted cumulative returns | breakout |
| s21 Skew Momentum | A | 63.3% | `rolling_skew > 0.3` + ret > 0 | momentum |
| s17 Trend Strength | A | 55.1% | `ret_1 > 0.02` + ADX > 25 + +DI > -DI | trend |
| s18 Momentum Accel | A | 51.0% | `ret_24h > ret_72h/3` (acceleration) | momentum |

### Combined Spot+Perp

| Strategy | Tier | Rate | Core Entry Signal | Market | Hold |
|----------|------|------|-------------------|--------|------|
| s32 Regime Spot/Perp | A | 78.9% | Regime-gated: spot long uptrend, perp short downtrend | combined | 18-720h |
| s30 Basis Carry | A | 77.1% | Basis z-score > 1.5 (long spot + short perp) | combined | 504h |
| s31 Hedged Momentum | A | 55.0% | Momentum burst + funding hedge (perp short when funding extreme) | combined | 120-720h |

### Per-Token (Perp)

| Strategy | Tier | Rate | Core Entry Signal | Market | Hold |
|----------|------|------|-------------------|--------|------|
| s29 Funding Carry | B | 20.1% | Funding rate z-score (short when positive, long when negative) | perp | 168h |
| s63 Vol Spike Reversal V4 | V4 | — | vol_ratio > 3x + displacement > 2.5% + ADX > 20, fade the spike | perp | 12-168h |
| s65 Funding Carry V4 | V4 | — | funding_signed rolling mean, carry = opposite to funding | perp | 24-336h |

### Per-Token (Spot, Tier B)

| Strategy | Tier | Rate | Core Entry Signal | Hold |
|----------|------|------|-------------------|------|
| s22 Supertrend ADX | B | 46.9% | Supertrend + ADX filter | trend |
| s20 Low Beta Quality | B | 46.9% | Low-beta quality factor | long-term |
| s12 Quality Breakout | B | 32.7% | Quality + breakout combo | swing |

### Overlay Wrappers (Gate 5O validated)

| Strategy | Base | Overlay | Key Result |
|----------|------|---------|------------|
| s37 momentum_trail | s11 | O5 trail | Sharpe +0.477, 65/69 token wins |
| s39 trend_trail | s09 | O5 trail | Sharpe +0.819, rate 36→59% |
| s44 basis_carry_trail | s30 | O5 trail | Sharpe +1.448, MaxDD halved |
| s54 turbo_carry | s44 | 2x sizing + cap_mult=15 | +160%/yr, paper trading |
| s57 signal_timed_carry | s44 | Signal discovery timing | Paper trading |
| s58 multi_strategy | s44+ | Multi-signal composite | Paper trading |

### Paper Trading (Live, Gate 6)

| Pool | Capital | Status | Strategies |
|------|---------|--------|------------|
| s58 | $200K | Running | s56+s57 |
| s60 | $200K | Running | s60 solo |
| s58+s60 | $200K | Running | s56+s57+s60 |
| s58+s62 | $200K | Running | s56+s57+s62 |
| s58+s63 | $200K | Running | s56+s57+s63 |
| s58+s65 | $200K | Running | s56+s57+s65 |

### Portfolio Assembly (Gate 5.5 Result)

**Recommended 4-strategy allocation (Sharpe ~4.7, MaxDD ~-4%):**

| Strategy | Weight | Solo Sharpe | Marginal Sharpe | Corr vs S11 | Role |
|----------|--------|-------------|-----------------|-------------|------|
| s30 Basis Carry | 40% | 5.47 | +0.95 | +0.21 | Core — delta-neutral arb, regime-stable |
| s32 Regime Spot/Perp | 25% | 3.01 | +0.41 | +0.25 | Complementary — negative beta hedge |
| s29 Funding Carry | 20% | 1.70 | +0.61 | -0.16 | Diversifier — market-neutral, negative corr |
| s11 Momentum Burst | 15% | 1.74 | -0.49 | 1.00 | Directional momentum exposure |

**Do NOT run s31 + s11 together** (corr +0.71, both negative marginal Sharpe when combined)

> Deep dive: `knowledge/STRATEGY_CATALOG.md`

---

## Gate 0: Idea Screen — Thresholds

| Criterion | Threshold |
|-----------|-----------|
| Economic mechanism | Must be explainable |
| Expected trade count | > 30 in available data |
| Idea score | >= 5/10 on rubric |
| Already tried & failed? | Check Tier C list below |
| Look-ahead bias risk | Signal must use only past data |

**Tier C (Archived — Don't Repeat):** s02, s03, s04, s07, s08, s10, s12, s14, s15, s16, s19, s20, s25, s26, s27, s28, s33, s35, s36, s38, s42, s43, s50, s52, s53, s55, s56

**Key Kill Reasons:** Standalone signal entries (IC != edge), 5x leverage (fee amplification), directional overlays on delta-neutral (both legs hedge), vol-managed sizing on regime-gated strategies (double-dipping risk reduction)

> Deep dive: `knowledge/STRATEGY_LIFECYCLE.md`, `knowledge/process/SCOPE_AND_CONTEXT.md`

---

## Gate 1: Signal Lab — IC Thresholds (AIPIP-0016 Updated)

**Kill if ANY fail:**

| Metric | Kill Threshold | Source |
|--------|---------------|--------|
| Mean IC (post-ETF) | < +0.02 | — |
| IC t-statistic | < 2.0 (or < 3.4 if N > 37 strategies tested) | Harvey & Liu 2020 |
| IC hit rate | < 55% | — |
| **ICIR (IC / std(IC))** | **< 0.3** | Qian/Hua/Sorensen 2007 |
| Gross profit factor | < 1.3 | — |
| Trade count | < 50 | — |

**Harvey-Liu multiple testing:** With N=37+ strategies/signals tested, required
t-stat = `sqrt(2 * ln(N))` ≈ 3.4. Apply to NEW signals only — existing Tier A grandfathered.

**New informational metrics (check, don't kill):**

| Metric | Purpose | Source |
|--------|---------|--------|
| ICIR post-ETF | Stability in current regime | `outputs/signal_discovery/rolling_ic_summary*.csv` |
| Transfer entropy | Non-linear causality | `outputs/signal_discovery/lead_lag*.json` |
| IC decay class | STABLE/DECAYING/DEAD | `outputs/signal_discovery/rolling_ic_summary*.csv` |
| Lead/lag asymmetry | Confirm signal is LEADING | `outputs/signal_discovery/lead_lag*.json` |

**Signal stability hierarchy:**

| Class | Drift Rate | Examples | Use As |
|-------|-----------|---------|--------|
| STABLE | < 0.001/yr | Cross-TF divergence, vol clustering | Primary entry signals |
| DECAYING | 0.001-0.01/yr | Regime-conditional EMAs, RSI | Use with caution, monitor |
| DEAD | > 0.01/yr | Microstructure, order flow proxies | Avoid (post-ETF killed) |

**Top Predictors (post-ETF IC):**

| Indicator | IC | Notes |
|-----------|-----|-------|
| ADX | +0.067 | Best predictor, increases with horizon |
| realized_vol | +0.053 | 2nd best |
| vol_ratio | +0.027 | Volume confirmation |
| taker | +0.021 | Decays fast (1d only) |
| intraday_kurtosis | +0.018 | Independent, range-market signal |
| RSI | +0.004 | Useless standalone; 95% corr with BB_pct |

**Redundant Pairs (drop one):**
- RSI ↔ BB_pct (r=0.95) — keep RSI
- realized_vol ↔ parkinson_vol (r=0.99) — keep realized_vol
- taker ↔ taker_buy_ratio (r=1.00) — keep taker

**Regime-Conditional IC:**

| Regime | Best Signals | Worst Signals |
|--------|-------------|---------------|
| Uptrend | vol_ratio, BB_pct, ATR_pct | Mean-reversion |
| Downtrend | RSI (contrarian, IC=-0.145) | MACD (lagging) |
| Range | intraday_kurtosis, intraday_skew | Most traditional |
| Quiet | ret_1 (+0.074), taker (+0.051) | Vol indicators |

> Deep dive: `knowledge/INDICATOR_CATALOG.md` (Section 10: Empirical Results), `knowledge/process/SIGNAL_DISCOVERY_METHODS.md`

---

## Gate 2: Knowledge + Dedup — Overlap Rules

**Per-token strategies:**
- Entry signal overlaps > 80% with existing Tier A/B → KILL
- Already tried and failed (Tier C) with no new evidence → KILL
- Signal type has 2+ strategies in Tier A → saturated, KILL
- **Recycle:** Overlap 30-80% → propose as FILTER to existing strategy, not new strategy

**Portfolio strategies:**
- Compare strategy CLASS, not entry signal (cross-sectional vs sector vs pairs)
- Check correlation vs existing portfolio strategies: >0.7 → KILL
- If same class exists: must show improvement on Calmar or DD, not just different parameters

**Combined spot+perp strategies:**
- Check if the spot leg signal overlaps with existing per-token strategies (s31↔s11 = +0.71, redundant)
- Basis arb (s30) is unique — no overlap with momentum/trend family
- New combined strategies must show corr <0.5 vs s30 to add value
- Funding-based strategies: s27 (mean reversion) and s28 (perp momentum) both killed. s29 (carry) works because it harvests the payment, not predicting direction

**Overlays:**
- Check if overlay already applied to base strategy
- Multiple overlays on same base OK if targeting different aspects (regime=allocation, agreement=entry, risk=sizing)
- Regime weighting not needed for combined portfolio (s30 already regime-stable, PF 2.50-2.68 across all regimes)

**Post-ETF Regime Shifts (Jan 2024+):**
- BTC price driven by ETF flows, not on-chain metrics
- Vol compressed to 20-30% (was 50-100%)
- IBIT (BlackRock) controls ~96% of net ETF volume
- Basis trade collapsed Oct 2024 (futures premium evaporated)

> Deep dive: `knowledge/STRATEGY_CATALOG.md`, `knowledge/process/CRYPTO_MICROSTRUCTURE_POST_ETF.md`

---

## Gate 3: Prototype — Signal Structure & Performance

**Mandatory Signal Stack (all Tier A strategies use this):**
```
Layer 1: Regime filter   → ctx.regime_1h != 0 (minimum: exclude CRISIS)
Layer 2: Trend alignment → ADX/EMA direction confirmation
Layer 3: Entry signal    → The actual alpha signal
Layer 4: Volume confirm  → vol_ratio > 1.0 or similar
Layer 5: Exit logic      → ATR trail + regime exit + max hold
Layer 6: Position sizing → ADV-based Kelly (engine computes from volume data)
```

**Regime Constants:** 0=CRISIS, 1=QUIET, 2=UPTREND, 3=RANGE, 4=DOWNTREND

**Standard Exit Parameters (updated 2026-03-15, exit ablation winner):**

| Parameter | Value | Notes |
|-----------|-------|-------|
| stop_mult | 3.0 | Universal across all strategies |
| trail_mult | 1.5 | Flat 1.5 ATR trail (replaces progressive schedule) |
| trail_schedule | None | Progressive schedule RETIRED — flat trail beats it |
| target_mult | 999 | Trail only, no fixed target |
| breakeven_atr | 0.5 | Move stop to entry after +0.5 ATR profit |
| min_hold | 6-12 | Hours minimum |
| max_hold | 504-720 | 21-30 days maximum |
| exit_regimes | {CRISIS} | Force exit (most V4 strategies) |
| bear_max_hold | 12 | s80/s81 only: force exit after 12h in DOWNTREND |

**s65 carry-specific:** trail(1.5) separately confirmed via 144-run sweep (9 configs × 4 periods × 4 portfolios). Harmonic rank score 2.38 vs next-best 1.68. Progressive schedules add no value for carry.

**Calmar caveat:** Backtested Calmar inflated ~100-1000x. See `memory/PROJECT_STATUS.md` finding #30. Realistic Calmar likely 1-5.

**Performance Requirements:**

| Metric | Target | Kill |
|--------|--------|------|
| strategy() call time | < 1ms on 40K bars | > 1ms |
| Vectorized? | Yes — NumPy only | Python for-loops |
| All 6 layers present? | Yes | Missing regime/exit |

**Vectorization Rules:**
- Use `ctx.ind_1h['rsi']` — never recompute indicators
- Use `rolling_mean(arr, window)` — never `for i in range()` over bars
- Vectorized: 0.04ms/call. Looped: 1,864ms/call (46,600x slower)
- **Combined strategies:** Use `_fast_rolling_zscore` (numpy cumsum) instead of `rolling_zscore` (pandas) — saves ~1ms on 54K bars

**Combined Strategy Template (Class A2):**
```python
def strategy(ctx_spot: StrategyContext, ctx_perp: StrategyContext) -> StrategyResult:
    # Primary leg (spot or perp)
    entry_mask = ...      # When to enter primary leg
    direction = ...       # 1=long, -1=short
    market_type = MarketType.SPOT  # or PERP

    # Secondary leg (optional, different from primary)
    secondary_entry_mask = ...
    secondary_direction = ...
    secondary_market_type = MarketType.PERP  # must differ from primary

    return StrategyResult(
        entry_mask=entry_mask, direction=direction, market_type=MarketType.COMBINED,
        capital_split=0.5,  # fraction of capital for primary leg
        secondary_entry_mask=secondary_entry_mask, secondary_direction=secondary_direction,
        secondary_market_type=secondary_market_type,
        # separate trade management params for each leg
        stop_mult=3.0, secondary_stop_mult=3.0,
        max_hold=720, secondary_max_hold=504,
    )
```

> Deep dive: `knowledge/SIGNAL_DEVELOPMENT.md`, `knowledge/PERFORMANCE_PATTERNS.md`

---

## Gate 4: Quick Validate (BTC) — Dual Gate

**Run:** `python v4/validation.py --strategy sNN --tokens BTC --workers 1`
**Combined:** `python v4/validation.py --strategy sNN --tokens BTC --market combined --workers 1`

**BTC must pass BOTH:**
1. Walk-Forward: positive OOS PnL
2. CPCV: PBO < 40%, majority of folds profitable

**3-attempt rule:** Max 3 parameter tuning attempts. Core logic changes = KILL.

**Combined strategy notes:**
- Validation automatically detects 2-arg signature and routes to `_run_walk_forward_combined` / `_run_cpcv_combined`
- Both primary AND secondary entry masks are walk-forward masked (OOS only)
- Engine `_simulate_combined` handles both legs with per-leg fees, funding, slippage

---

## Gate 5: Full Validate (111 Tokens) — Tier Assignment

**Run:** `python v4/validation.py --strategy sNN --workers 4`
**Combined:** `python v4/validation.py --strategy sNN --market combined --workers 4` (111 tokens with both spot+perp)

**Return-First Metrics:**

| Metric | Threshold | Priority |
|--------|-----------|----------|
| Calmar ratio | > 0.5 | PRIMARY |
| Sortino ratio | > 1.0 | PRIMARY |
| Profit factor | > 1.5 | PRIMARY |
| Max drawdown | < 25% | CONSTRAINT |
| Trade count | > 100 | VALIDITY |
| WFE (OOS/IS return) | > 50% | VALIDITY |

**Tier Assignment:**

| Rate | Tier | Action |
|------|------|--------|
| > 50% | A | Proceed to Gate 6 |
| 20-50% | B | Iterate (max 3 cycles, params only) |
| < 20% | C | ARCHIVE immediately |

**Transaction Costs (dynamic ADV-based, applied automatically by engine):**

Costs are now continuous functions of actual ADV (computed from volume data):

| ADV | Tier (label) | Fee | Slippage | Kelly | Cap% |
|-----|-------------|-----|----------|-------|------|
| $10B+ | 1 | 0.22% | 5 bps | 0.50 | 12% |
| $100M | 1 | 0.22% | 5 bps | 0.36 | 8% |
| $50M | 1 | 0.22% | 7 bps | 0.34 | 7.4% |
| $10M | 2 | 0.22% | 16 bps | 0.29 | 6% |
| $5M | 3 | 0.22% | 22 bps | 0.27 | 5.4% |
| $1M | 3 | 0.22% | 50 bps | 0.22 | 4% |
| <$0.1M | 3 | 0.22% | 50 bps | 0.15 | 2% |

**Kill criteria:**
- Validation rate < 20%
- Calmar < 0.5 on passing tokens
- Max DD > 25% on BTC
- WFE < 50%
- Parameter sensitivity > 30% Calmar degradation at +/-20%
- Tier B after 3 cycles without reaching 50%

> Deep dive: `knowledge/process/BACKTESTING_VALIDATION_BEST_PRACTICES.md`,
> `knowledge/process/RISK_PORTFOLIO_CONSTRUCTION.md`

---

## V4 Gates: V4 Portfolio Strategy Validation (Class D)

### V4-Gate 3: Full-History Backtest (72 months)

**Run:** `/workspace/venv/bin/python v4/portfolio_backtest.py --strategy sNN --months 72 --capital 200000`

Uses max available data (currently 74 months, Jan 2020 – Mar 2026) to cover all market regimes:
COVID crash, 2021 bull, 2022 bear, 2023 recovery, 2024 ETF rally, 2025-2026 consolidation.

| Criterion | Threshold | Kill |
|-----------|-----------|------|
| Full-history total return | > +50% | < +50% |
| Portfolio Calmar | > 0.5 | < 0.5 |
| Max drawdown | < 25% | > 25% |
| Trade count | > 100 | < 100 |
| Vectorized (< 1ms/call) | Yes | No |

**Check mission brief:** If `.claude/.strategy-mission` exists, apply mission-specific criteria.

**Report per-regime metrics** (UPTREND, DOWNTREND, RANGE, QUIET, CRISIS) — not kill criteria,
but required for portfolio complement test at V4-Gate 5.

### V4-Gate 4: OOS Validation (Train→Dec, Trade Jan-Mar)

**Method:** Adjust `config.train_bars` to end on Dec 31 2025. Trade OOS Jan 1 to present.

| Criterion | Threshold | Kill |
|-----------|-----------|------|
| OOS total return | > 0% (positive) | Negative |
| OOS months positive | >= 2 of 3 | < 2 of 3 |
| OOS max drawdown | < 30% | > 30% |
| March 2026 PnL | > -5% | < -5% |

**Sideways market checklist:**
- [ ] Uses perp or combined market (bidirectional)
- [ ] Profitable in RANGE and QUIET regimes
- [ ] Shows positive March 2026 PnL
- [ ] Low correlation with s56 (momentum) and s57 (carry)

### V4-Gate 5: Portfolio Complement Test

**Method:** Run V4 backtest with s58 + new strategy. Compare portfolio metrics.

| Criterion | Threshold | Kill |
|-----------|-----------|------|
| Portfolio Sharpe delta | > 0 (improves) | Decreases |
| Portfolio MaxDD delta | <= +2pp | Worsens > 2pp |
| Correlation vs s56 | < 0.5 | > 0.7 |
| Correlation vs s57 | < 0.5 | > 0.7 |
| Marginal Sharpe contribution | > 0 | Negative |
| Regime coverage | 2+ regimes beyond s58 | Same profile |

**V4 reference strategies:**

| Strategy | V4 12mo | OOS Jan-Mar | March | Role |
|----------|---------|-------------|-------|------|
| s58 (s56+s57) | +1717% | +66% | +66% | Production baseline |
| s28 momentum_burst_perp | +3157% | +157% | — | V4 candidate (failed V3) |
| s27 funding_mean_rev | — | — | +$16K | Best March performer |
| s29 funding_carry | +269% | +50% | — | Regime-stable, low corr |

> Deep dive: `knowledge/process/STRATEGY_PIPELINE_GATES.md` (V4 OOS test template)

### V4-Gate 5.5: Per-Portfolio Concentration Tuning

**Tool:** `python tools/concentration_sweep.py`

**Method:** Sweep `concentration_limit` per portfolio across [0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.50, 1.0],
test across 3 periods (12mo, 3mo, 1mo). Select by **maximum Calmar ratio** (return / |max_dd|).

**Selection criterion:** Calmar ratio (NOT "max return where DD < Xpp worse").
Rationale: absolute DD thresholds treat 1% and 30% DD portfolios equally. Calmar naturally
balances return vs risk and is the primary metric at Gates 3-5.

**Cross-period consistency:** Require the recommended value does not LOSE on return in more
than 1 of 3 periods. Flag extreme path sensitivity (small value change causing >5x DD swing).

**Current optimal values (March 2026):**

| Portfolio | concentration_limit | Rationale |
|-----------|-------------------|-----------|
| s58+s62 | 0.20 | STRONG — consistent all periods |
| s58+s63 | 0.15 | MODERATE — 3mo/1mo win, 12mo return lower but Calmar better |
| s58+s59 | 0.15 | STRONG — 4x Calmar improvement at 12mo |
| s58+s72 | 0.20 | STRONG — consistent all periods |
| s58+s75 | 0.25 | MODERATE — 12mo/3mo win, 1mo mixed |
| s58+s76 | 0.15 | MODERATE — 12mo Calmar +70%, return -13% |
| 4-edge+ptp | 0.15 | STRONG — consistent all 3 periods |
| s80+s81 | 0.50 | STRONG — 0.10 was catastrophically restrictive (3592 rejections) |
| s80+s81-dyn | 0.50 | MODERATE — 0.50 more consistent than 1.0 across periods |
| super5-dyn | 0.30 | STRONG — conservative loosening, Calmar improves |
| super5-conv | 0.50 | MODERATE — massive return uplift, DD +4pp acceptable |

**Unchanged:** s58 (1.0), s58+s60 (1.0), s58+s65 (1.0), s58+s69 (1.0), 4-edge (1.0), 4-edge-conv (1.0)

---

## Gate 6: Paper Trading — Degradation Thresholds (AIPIP-0016 + AIPIP-0024)

**Single deployment path:** `v4/run_paper_multi.py` + `configs/multi_v4_paper.json`.
NEVER create new runner scripts or alternative dashboard paths. See SKILL.md Gate 6 for
the full deployment checklist. Do NOT set `gate6` until the strategy is confirmed running.

**Minimum:** 50 trades before any go-live decision. Duration: 1-4 weeks.

**Expected degradation (Suhonen et al. 2017):**
- 215 strategies across 17 banks: **median 73% Sharpe deterioration**
- Budget **50-60% Sharpe degradation** backtest → live
- s58 example: backtest Sharpe 7.29 → expected live **2.9-4.4**

**Acceptable Degradation:**

| Metric | First 2 Weeks | Steady State |
|--------|---------------|-------------|
| Paper Sortino / Backtest Sortino | > 0.4x (settling) | > 0.6x |
| Slippage vs modeled | < 2x backtest assumption | < 2x |
| Max drawdown | < 1.5x backtest | < 1.5x |
| Fill rate | > 90% | > 95% |

**Kill criteria:**
- Paper Sharpe < 0.4x backtest Sharpe (after settling)
- Slippage > 50% of expected edge per trade
- Max DD > 1.5x worst backtest drawdown
- < 10 trades generated

> Deep dive: `knowledge/PAPER_TRADING_PRO_FRAMEWORK.md`

---

## Gate 7: Production — Risk Limits & Decay

**Position Sizing:**
- Initial allocation: 1-5% of target for first 3-6 months
- Per-trade risk: 1-2% of strategy allocation (use 1/4 Kelly)
- Volume participation: < 5% of average daily volume

**Circuit Breakers:**

| Trigger | Action |
|---------|--------|
| Daily loss > 3% | Halt for day |
| Rolling 5d DD > Y% | Reduce 50% |
| Max DD from peak > 15% | Pull from production |
| Portfolio daily loss > 5% | Reduce ALL 50% |

**Decay Detection — Threshold Flags (monthly):**

| Flag | Trigger |
|------|---------|
| Rolling 60d Sortino < 0.0 | Flag |
| Rolling 90d profit factor < 0.8 | Flag |
| No equity high in 6 months | Flag |
| Corr with another Tier A > 0.7 | Flag |
| Validation rate dropped > 10pp | Flag |

**Decay Detection — BOCPD (Adams & MacKay 2007):**

| Metric | BOCPD Config | Trigger |
|--------|-------------|---------|
| Rolling 30d Sharpe | Hazard rate 1/90 | Changepoint prob > 0.8 |
| Rolling 30d return | Hazard rate 1/90 | Downward shift detected |
| Rolling 60d realized vol | Hazard rate 1/180 | Vol regime change |

**Combined interpretation:**
- Threshold + BOCPD fire → **Structural decay.** Pull from production.
- Threshold fires, BOCPD silent → **Normal noise.** Reduce 25%, continue.
- BOCPD fires, threshold OK → **Early warning.** Investigate regime change.

**Escalation:** 1 threshold flag → -25%. 2 flags → -50% + review. 3+ flags or BOCPD confirmed → pull.

> Deep dive: `knowledge/process/RISK_PORTFOLIO_CONSTRUCTION.md`, `knowledge/KRAKEN_FEES.md`

---

## Bias Audit Checklist (Run at Every Gate)

```
COSTS:
[ ] Fee rate and slippage computed from actual ADV (not flat/static)
[ ] ADV derived from volume data (30-day median of close*volume)
[ ] Costs applied on BOTH entry and exit

LOOK-AHEAD:
[ ] No np.percentile/mean/std on full arrays — use .expanding()/.rolling()
[ ] Regime detection uses only past data (expanding quantiles, min_periods=60)
[ ] Early bars default to neutral state (RANGE)

CPCV:
[ ] Non-contiguous fold entries restricted to test_idx bars only
[ ] Purge/embargo fraction >= 0.01
[ ] Entry mask applied AFTER fold slicing

DATA:
[ ] Point-in-time universe (no survivorship bias)
[ ] No forward-fill with current bar's data

COMBINED (if spot+perp strategy):
[ ] Both spot and perp data aligned to common time range
[ ] Both entry_mask AND secondary_entry_mask walk-forward masked
[ ] Funding costs applied to perp leg (engine handles automatically)
[ ] capital_split correctly divides capital between legs
```

> Deep dive: `knowledge/process/BACKTESTING_VALIDATION_BEST_PRACTICES.md` Section 16
