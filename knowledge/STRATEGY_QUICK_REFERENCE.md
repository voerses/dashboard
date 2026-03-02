# Strategy Development Quick Reference

> Single-file gate reference. Load this instead of 15 separate files.
> Deep dives linked where needed — read them only when investigating specifics.

---

## Existing Tier A/B Strategies (Dedup Check)

| Strategy | Tier | Rate | Core Entry Signal | Hold |
|----------|------|------|-------------------|------|
| s11 Momentum Burst | A | 75.5% | `ret_1 > 0.03` (momentum burst) | 18-720h |
| s09 Optimized Trend | A | 73.5% | EMA stack + daily EMA50 + ADX > 30 | trend-follow |
| s13 Volume Breakout | A | 67.3% | Volume-weighted cumulative returns | breakout |
| s21 Skew Momentum | A | 63.3% | `rolling_skew > 0.3` + ret > 0 | momentum |
| s17 ADX Directional | B | 55.1% | `ret_1 > 0.02` + ADX > 25 + +DI > -DI | trend |
| s18 Acceleration | B | 51.0% | `ret_24h > ret_72h/3` (acceleration) | momentum |
| s22 Supertrend ADX | B | 46.9% | Supertrend + ADX filter | trend |

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

**Tier C (Archived — Don't Repeat):** s02, s03, s04, s08, s14, s15, s16, s20

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

**Kill criteria:**
- Entry signal overlaps > 80% with existing Tier A/B → KILL
- Already tried and failed (Tier C) with no new evidence → KILL
- Signal type has 2+ strategies in Tier A → saturated, KILL

**Recycle:** Overlap 30-80% → propose as FILTER to existing strategy, not new strategy

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
Layer 6: Position sizing → Tier-based Kelly (engine handles this)
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

> Deep dive: `knowledge/SIGNAL_DEVELOPMENT.md`, `knowledge/PERFORMANCE_PATTERNS.md`

---

## Gate 4: Quick Validate (BTC) — Dual Gate

**Run:** `python v3/validation.py --strategy sNN --tokens BTC --workers 1`

**BTC must pass BOTH:**
1. Walk-Forward: positive OOS PnL
2. CPCV: PBO < 40%, majority of folds profitable

**3-attempt rule:** Max 3 parameter tuning attempts. Core logic changes = KILL.

---

## Gate 5: Full Validate (49 Tokens) — Tier Assignment

**Run:** `python v3/validation.py --strategy sNN --workers 4`

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

**Transaction Costs (applied automatically by engine):**

| Tier | Tokens | Fee | Slippage | Per-side |
|------|--------|-----|----------|----------|
| 1 (>$50M ADV) | BTC, ETH, SOL, SUI | 0.22% | 8 bps | 0.30% |
| 2 ($10-50M) | ADA, AVAX, DOT, LINK | 0.22% | 15 bps | 0.37% |
| 3 ($5-10M) | BONK, FLOKI, PENGU | 0.25% | 35 bps | 0.60% |

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
[ ] Fee rate uses tier-based costs (not flat 0.10%)
[ ] Slippage varies by token liquidity tier
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
```

> Deep dive: `knowledge/process/BACKTESTING_VALIDATION_BEST_PRACTICES.md` Section 16
