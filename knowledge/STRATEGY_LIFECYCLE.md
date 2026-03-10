# Strategy Lifecycle -- Research to Production Pipeline

> **TL;DR -- Tier system and development pipeline**
> - **V4 (portfolio):** New standard. Run V4 backtest + OOS Jan-Mar. V3-killed strategies can succeed in V4 portfolio context.
> - **V3 (per-token):** Still used for IC validation and per-token robustness. Tier A (>50%), Tier B (20-50%), Tier C (<20%).
> - Pre-dev checklist: 7 knowledge files + sweep results + bias audit + cost verification
> - Fast iteration: idea to validated result in ~25 min (vectorized + parallel workers)
> - Dedup: >80% overlap with Tier A -> propose as filter instead of new strategy
> - **Sideways market gap:** ALL spot-only strategies fail Jan-Mar 2026. Need perp/combined strategies.
> **When to read full file:** Starting a new strategy, understanding tiers, deciding promote/demote/archive
> **Sections:** 1-Tier System, 2-Knowledge Check, 3-Pipeline, 4-Promotion Rules, 5-Sweep Protocol, 6-Numbering, 7-Fast Iteration, 8-V4 Development

---

## 1. TIER SYSTEM

### V4 Portfolio Tier (March 2026 — NEW)

| Strategy | V4 Status | V4 12mo | OOS Jan-Mar | Notes |
|----------|-----------|---------|-------------|-------|
| s58 (s56+s57) | **Production** | +1717% | +66% | Paper trading live Mar 10 |
| s28 momentum_burst_perp | V4 Candidate | +3157% | +157% | Failed V3 gate, thrives in V4 |
| s27 funding_mean_rev | V4 Candidate | — | — | Best March performer (+$16K) |
| s29 funding_carry | V4 Candidate | +269% | +50% | Regime-stable, near-zero corr |

### V3 Per-Token Tier (legacy — still valid for signal validation)

Strategies classified by V3 validation rate (% of 49 tokens passing dual WF+CPCV gate):

| Tier | Rate | Status | Action |
|------|------|--------|--------|
| **A** | > 50% | Production (`strategies/`) | Deploy, monitor, iterate |
| **B** | 20-50% | Experimental (`strategies/`) | Investigate, tune, max 3 cycles |
| **C** | < 20% | Archive (`strategies/archive/`) | Preserved for reference |

### Current Classification (2026-03-01)

**Tier A:**

| Strategy | Rate | Key Edge |
|----------|------|----------|
| s11_momentum_burst | 75.5% | 3% hourly return + ADX + volume |
| s09_optimized_trend | 73.5% | Multi-TF dual momentum |
| s13_vol_weighted_tsmom | 67.3% | Volume-weighted TSMOM |
| s21_skew_momentum | 63.3% | Return skew + momentum acceleration |
| s17_trend_strength_filter | 55.1% | ADX gradient + EMA alignment |
| s18_momentum_accel | 51.0% | Momentum acceleration |

**Tier B:**

| Strategy | Rate | Issue |
|----------|------|-------|
| s20_low_beta_quality | 46.9% | Close to promotion threshold |
| s22_supertrend_adx | 46.9% | Supertrend flip timing noisy |
| s12_quality_breakout | 32.7% | BB squeeze too rare |
| s15_vol_regime_breakout | 30.6% | Vol regime detection needs work |
| s14_microstructure_edge | 26.5% | Spread estimator liquid-only |
| s10_research_dip_buy | 24.5% | Dip-buy timing inconsistent |

**Tier C (Archived):** s07 (10.2%, RSI alone), s08 (6.1%, OBV divergence), s16 (0.0%, composite factor noise), s19 (0.0%, mean reversion loses money).

---

## 2. MANDATORY KNOWLEDGE BASE CHECK

**Before writing ANY strategy, indicator, or engine code:**

```
[ ] knowledge/PERFORMANCE_PATTERNS.md — No for-loops, use rolling_*, target <1ms/call
[ ] knowledge/SIGNAL_DEVELOPMENT.md — 6-layer signal stack, composition patterns
[ ] knowledge/INDICATOR_CATALOG.md — Check existing indicators, IC values, post-ETF stability
[ ] knowledge/STRATEGY_CATALOG.md — Check if type already implemented, academic backing
[ ] knowledge/INDICATOR_CATALOG.md §10 — Signal IC positive post-ETF, decay rate, regime-conditional
[ ] results/sweep_summary_*.json — Current tiers, avoid duplicating, identify gaps
[ ] knowledge/KRAKEN_FEES.md §8,11 — Tier-based costs (T1: 0.30%, T3: 0.60%), not Binance 0.15%
[ ] Bias audit (knowledge/process/BACKTESTING_VALIDATION_BEST_PRACTICES.md §16)
```

Only Tier 0 changes (typo fixes, param tweaks) can skip. New strategy/indicator requires ALL checks.

---

## 3. RESEARCH -> PROTOTYPE PIPELINE

### Phase 1: Research & Ideation

1. **Signal Lab IC check:** `python tools/signal_lab.py --signal <name> --post-etf`
   - IC > +0.02 post-ETF, stable across 2+ horizons, check for post-ETF sign flip
2. **Dedup check (mandatory):** Compare against all Tier A/B entry signals (see `STRATEGY_QUICK_REFERENCE.md` for full table)
   - \>80% overlap -> KILL (propose as filter to existing strategy)
   - 30-80% overlap -> propose as parameter variant
   - <30% overlap -> genuinely new, proceed
3. **Document hypothesis:** What microstructure creates this edge? What regime? Expected profile?

### Phase 2: Prototype

1. Copy `strategies/TEMPLATE.py` -> `strategies/sNN_name.py`
2. Follow signal structure from `SIGNAL_DEVELOPMENT.md` (regime -> trend -> entry -> exit)
3. All code vectorized (see `PERFORMANCE_PATTERNS.md`)
4. Performance gate: < 1ms per call on 40K bars

### Phase 3: Quick Validation (BTC only)

```bash
/workspace/venv/bin/python v3/validation.py --strategy sNN --tokens BTC --workers 1
```

BTC must pass dual gate (WF + CPCV). Max 3 tuning attempts on parameters -- if core logic fails, archive.

### Phase 4: Full Validation (49 tokens)

```bash
/workspace/venv/bin/python v3/validation.py --strategy sNN --workers 4
```

Classify: >50% = Tier A, 20-50% = Tier B, <20% = Tier C (archive immediately).

### Phase 5: Iterate or Archive

**Tier B:** Up to 3 iteration cycles. Analyze failing tokens (regime? liquidity? cap?). Adjust parameters, not logic. Re-validate each change. No improvement after 3 cycles -> archive.

**Tier C:** Archive immediately to `strategies/archive/`. Add entry to archive log. Excluded from sweeps.

---

## 4. PROMOTION RULES

| Transition | Criteria |
|------------|----------|
| B -> A | Rate > 50% on latest sweep, stable across 2+ sweeps, no >80% overlap with Tier A |
| A -> B | Rate < 40% persists across 2 sweeps, OR single sweep < 20% |
| Archive -> Experimental | New Signal Lab data shows signal strengthened, or market structure changed |

---

## 5. SWEEP PROTOCOL

### When to Run

After any: strategy code change, monthly data update, engine/indicator changes.

```bash
/workspace/venv/bin/python v3/validation.py \
  --strategy s07 s08 s09 s10 s11 s12 s13 s14 s15 s16 s17 s18 s19 s20 s21 s22 \
  --workers 4 --data-dir data
```

Results: `results/sweep_summary_YYYYMMDD.json`. Compare across dates.

### Post-Sweep Actions

1. Update tier classifications in this document
2. Archive new Tier C strategies
3. Promote B -> A if criteria met
4. Investigate A -> B demotions
5. Commit updated results

---

## 6. STRATEGY NUMBERING

| Range | Category |
|-------|----------|
| s01-s06 | Legacy V1 (not in current system) |
| s07-s10 | V2 initial batch |
| s11-s16 | Signal Lab batch 1 (IC analysis) |
| s17-s22 | Signal Lab batch 2 (post-ETF) |
| s23-s28 | Perp/combined batch (funding, carry, momentum) |
| s29-s32 | Combined spot+perp strategies |
| s33-s44 | Overlay wrappers (regime, trail, sizing) |
| s49-s55 | Leverage/sizing experiments |
| s56-s58 | Signal-enhanced V4 portfolio strategies |
| s59+ | Future (next available: s59) |

Numbers are permanent -- never reuse an archived number.

---

## 7. FAST ITERATION WORKFLOW

```
1. Signal Lab IC check          (~2 min)
2. Knowledge base review        (~5 min)
3. Write vectorized strategy    (~15 min)
4. Performance check (< 1ms)    (~1 min)
5. BTC-only quick validation    (~20 sec)
6. Full 49-token validation     (~20 sec)
7. Classify and decide          (~1 min)
Total: ~25 min from idea to validated result
```

**Enablers:** Vectorized strategies (<30s validation), context caching, parallel workers, rolling helpers.

### Iteration Anti-Patterns

- Don't add more signals to a losing strategy -- archive and try a different approach
- Don't over-fit to specific tokens (works on 5 = likely curve-fitted)
- Don't tune parameters without re-validating (every change needs fresh V3 run)
- Don't skip the knowledge base check (#1 time waste = reimplementing known failures)

---

## 8. V4 PORTFOLIO DEVELOPMENT WORKFLOW

### When to Use V4 (vs V3)

Use V4 when building **portfolio components** — strategies that may be individually weak but contribute to portfolio-level edge through diversification, regime coverage, or low correlation.

Use V3 when testing **new hypotheses** — strategies that need to prove per-token robustness before being considered.

**Key insight (March 2026):** Several V3-killed strategies (s27, s28, s25) are profitable in V4 portfolio context. V4's shared capital pool + multi-token diversification compensates for per-token weakness.

### V4 Fast Path (Idea → Portfolio Component)

```
1. Gate 0: Idea screening (same as V3)           (~5 min)
2. V4 12-month backtest                           (~5 min)
   python v4/portfolio_backtest.py --strategy sNN --months 12
3. V4 OOS test (train→Dec, trade Jan-Mar)         (~5 min)
   Adjust config.train_bars to push mask to Jan 1
4. Portfolio complement test                       (~10 min)
   Add to s58 portfolio, check return/Sharpe/MaxDD improvement
5. Paper trading (if passes gates 1-3)             (~1-4 weeks)
Total: ~25 min from idea to portfolio-validated result
```

### Sideways/Choppy Market Strategy Design

**Goal:** Complement s58 in regimes where carry goes flat and long-only momentum fails.

**Requirements:**
1. Must use perp or combined market (bidirectional)
2. Must be profitable in RANGE and QUIET regimes
3. Must show positive March 2026 PnL (stress test)
4. Must have low correlation with s56 (momentum) and s57 (carry)

**Proven approaches from V4 sweep:**
- Funding mean reversion (s27): exploits funding rate extremes
- Bidirectional momentum (s28): catches both upside and downside moves
- Pure funding carry (s29): harvests funding payments, regime-stable
- Vol spike reversal (s25): catches reversals after vol expansion

See `knowledge/process/STRATEGY_PIPELINE_GATES.md` § "V4 Portfolio Strategy Development" for full gate details and code templates.
