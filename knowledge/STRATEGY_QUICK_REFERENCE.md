# Strategy Development Quick Reference

> Single-file gate reference. Load this instead of 15 separate files.
> Deep dives linked where needed — read them only when investigating specifics.

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

### Strategy Classes (Gate 0 Routing)

| Class | Gate Path | When to Use |
|-------|-----------|-------------|
| A. Per-Token Signal | 0→1→2→3→4→5→6→7 | Single signal on individual tokens (spot or perp) |
| A2. Combined Spot+Perp | 0→1→2→3→4→5→5.5→6→7 | Dual-leg: long spot + short perp, or regime-adaptive instrument selection |
| B. Portfolio Strategy | 0→2→3P→5P→6→7 | Cross-token ranking, sector rotation, pairs |
| C. Overlay | 0→2→3O→5O→6→7 | Regime weighting, signal agreement, risk scaling |

**Combined strategy patterns (Class A2):**
- **Simultaneous:** Both legs enter/exit together (e.g., s30 basis carry)
- **Conditional secondary:** Primary always, secondary only when condition met (e.g., s31 hedged momentum)
- **Alternating:** One or the other based on regime (e.g., s32 regime spot/perp)

**Combined validation:** `python v3/validation.py --strategy sNN --market combined --workers 4`

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

| Strategy | Capital | Status | Tokens |
|----------|---------|--------|--------|
| s30 basis_carry | $200K | Running | 90 |
| s32 regime_spot_perp | $200K | Running | 90 |
| s54 turbo_carry | $200K | Running | 22 |
| s58 multi_strategy_portfolio | $200K | Running | 90 |

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

## Gate 1: Signal Lab — IC Thresholds

**Kill if ANY fail:**

| Metric | Kill Threshold |
|--------|---------------|
| Mean IC (post-ETF) | < +0.02 |
| IC t-statistic | < 2.0 |
| IC hit rate | < 55% |
| ICIR (IC / std(IC)) | < 0.5 |
| Gross profit factor | < 1.3 |
| Trade count | < 50 |

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

**Standard Exit Parameters (from Tier A):**

| Parameter | Value | Notes |
|-----------|-------|-------|
| stop_mult | 3.0 | Universal across Tier A |
| trail_mult | 2.5 | Trailing stop |
| target_mult | 5.0 | Profit target |
| min_hold | 6 | Hours minimum |
| max_hold | 720 | 30 days maximum |
| exit_regimes | {CRISIS, DOWNTREND} | Force exit |

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

**Run:** `python v3/validation.py --strategy sNN --tokens BTC --workers 1`
**Combined:** `python v3/validation.py --strategy sNN --tokens BTC --market combined --workers 1`

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

**Run:** `python v3/validation.py --strategy sNN --workers 4`
**Combined:** `python v3/validation.py --strategy sNN --market combined --workers 4` (111 tokens with both spot+perp)

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

## Gate 6: Paper Trading — Degradation Thresholds

**Minimum:** 50 trades before any go-live decision. Duration: 1-4 weeks.

**Acceptable Degradation:**

| Metric | Max Degradation |
|--------|-----------------|
| Paper Sortino / Backtest Sortino | > 0.6x |
| Slippage vs modeled | < 2x backtest assumption |
| Max drawdown | < 1.5x backtest |
| Fill rate | > 95% |

**Kill criteria:**
- Paper returns < 60% of backtest returns
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

**Decay Detection (monthly):**

| Flag | Trigger |
|------|---------|
| Rolling 60d Sortino < 0.0 | Flag |
| Rolling 90d profit factor < 0.8 | Flag |
| No equity high in 6 months | Flag |
| Corr with another Tier A > 0.7 | Flag |
| Validation rate dropped > 10pp | Flag |

**Escalation:** 1 flag → -25%. 2 flags → -50% + review. 3+ flags → pull, post-mortem.

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
