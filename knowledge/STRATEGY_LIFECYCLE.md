# Strategy Lifecycle -- Research to Production Pipeline

> **TL;DR -- Tier system and development pipeline**
> - **V4 (portfolio):** Current standard. Run V4 backtest with post-MTM equity + $2M sizing cap.
> - **V3 (per-token):** Legacy. V3 validation rates still useful for signal robustness, but V3 return numbers are unreliable.
> - Pre-dev checklist: 7 knowledge files + sweep results + bias audit + cost verification
> - Fast iteration: idea to validated result in ~25 min (vectorized + parallel workers)
> - Dedup: >80% overlap with Tier A -> propose as filter instead of new strategy
> - **Sideways market gap:** ALL spot-only strategies fail Jan-Mar 2026. Only carry (s62, s65) survive.
> **When to read full file:** Starting a new strategy, understanding tiers, deciding promote/demote/archive
> **Sections:** 1-Tier System, 2-Knowledge Check, 3-Pipeline, 4-Promotion Rules, 5-Sweep Protocol, 6-Numbering, 7-Fast Iteration, 8-V4 Development

---

## 1. TIER SYSTEM

### V4 Portfolio Tier (March 2026 — POST-MTM VALIDATED)

> **All numbers below are post-MTM (mark-to-market equity), $200K starting capital, $2M sizing cap, 12-month period.**
> Previous numbers in this section were pre-MTM (realized-only equity) with uncapped compounding — all wildly inflated.

| Strategy | V4 Status | V4 12mo Return | MaxDD | Paper Trading | Notes |
|----------|-----------|---------------|-------|---------------|-------|
| s62 conservative_carry | **Tier A** | +9.6% | -29.7% | +1.06% (7d) | Only profitable solo strategy |
| s65 funding_carry_v4 | **Tier A** | +4.9% | -45.8% | +2.25% (7d) | Paper-trading confirmed |
| s72 s65_time_trail | Tier C | +4.9% | -45.8% | +3.21% (14d) | Same as s65 (overlay neutral in backtest) |
| s56 max_leverage_momentum | Tier C | -27.4% | -38.5% | Losing | Production component, BUT losing |
| s57 signal_timed_turbo_carry | Tier C | -29.3% | -29.2% | Losing | Production component, BUT losing |
| s58 (s56+s57) | Tier C | -32.5% | -37.0% | Losing | Production portfolio, LOSING |
| s60 momentum_burst_perp | Graveyard | -70.5% | -81.3% | -25.8% | Dead |
| s63 vol_spike_reversal | Graveyard | -78.9% | -81.7% | 0% win rate | Dead |
| s75 s63_fixed_tp | Graveyard | -83.9% | -85.7% | | Dead |

### V3 Per-Token Tier (legacy — for signal validation only)

V3 validation rates are still valid for testing per-token signal robustness.
However, V3 RETURN NUMBERS are unreliable (uncapped compounding, pre-MTM equity).

| Tier | Rate | Status | Action |
|------|------|--------|--------|
| **A** | > 50% | Production (`strategies/`) | Deploy, monitor, iterate |
| **B** | 20-50% | Experimental (`strategies/`) | Investigate, tune, max 3 cycles |
| **C** | < 20% | Archive (`strategies/archive/`) | Preserved for reference |

### V3 Legacy Classification (2026-03-01)

> These are V3 per-token validation rates. The corresponding RETURN numbers from V3 are NOT reliable.

**Tier A (V3):**

| Strategy | Rate | Key Edge |
|----------|------|----------|
| s11_momentum_burst | 75.5% | 3% hourly return + ADX + volume |
| s09_optimized_trend | 73.5% | Multi-TF dual momentum |
| s13_vol_weighted_tsmom | 67.3% | Volume-weighted TSMOM |
| s21_skew_momentum | 63.3% | Return skew + momentum acceleration |
| s17_trend_strength_filter | 55.1% | ADX gradient + EMA alignment |
| s18_momentum_accel | 51.0% | Momentum acceleration |

**Tier B (V3):**

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
[ ] knowledge/RESEARCHER_BEST_PRACTICES.md — 9-layer parameter catalog, 12 ground rules
[ ] results/v4/portfolio_rankings.json — Current post-MTM performance truth
[ ] knowledge/KRAKEN_FEES.md §8,11 — Tier-based costs (T1: 0.30%, T3: 0.60%), not Binance 0.15%
[ ] Bias audit (knowledge/process/BACKTESTING_VALIDATION_BEST_PRACTICES.md §16)
```

Only Tier 0 changes (typo fixes, param tweaks) can skip. New strategy/indicator requires ALL checks.

---

## 3. RESEARCH -> PROTOTYPE PIPELINE

### Phase 1: Research & Ideation

1. **Signal Lab IC check:** `python tools/signal_lab.py --signal <name> --post-etf`
   - IC > +0.02 post-ETF, stable across 2+ horizons, check for post-ETF sign flip
2. **Dedup check (mandatory):** Compare against all Tier A/B entry signals (see `STRATEGY_QUICK_REFERENCE.md`)
   - >80% overlap -> KILL (propose as filter to existing strategy)
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
/workspace/venv/bin/python v4/validation.py --strategy sNN --tokens BTC --workers 1
```

BTC must pass dual gate (WF + CPCV). Max 3 tuning attempts on parameters -- if core logic fails, archive.

### Phase 4: Full Validation (49 tokens)

```bash
/workspace/venv/bin/python v4/validation.py --strategy sNN --workers 4
```

Classify: >50% = Tier A, 20-50% = Tier B, <20% = Tier C (archive immediately).

### Phase 5: V4 Portfolio Backtest

```bash
/workspace/venv/bin/python v4/rank_all_portfolios.py
```

Run with post-MTM equity and $2M sizing cap. Compare against portfolio_rankings.json.

### Phase 6: Paper Trading (50+ trades)

Deploy to paper trading for 50+ closed trades before production.

---

## 4. PROMOTION RULES

| Transition | Criteria |
|------------|----------|
| B -> A | Rate > 50% on latest sweep, stable across 2+ sweeps, no >80% overlap with Tier A |
| A -> B | Rate < 40% persists across 2 sweeps, OR single sweep < 20% |
| Archive -> Experimental | New Signal Lab data shows signal strengthened, or market structure changed |
| Paper -> Production | 50+ trades, returns >60% of backtest, MaxDD <1.5x backtest |

---

## 5. SWEEP PROTOCOL

### When to Run

After any: strategy code change, monthly data update, engine/indicator changes.

```bash
/workspace/venv/bin/python v4/validation.py \
  --strategy sNN --workers 4 --data-dir data
```

Results: `results/v4/portfolio_rankings.json`. Compare across dates.

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
| s07-s22 | V2/V3 signal lab batches |
| s25-s54 | V3 perp/carry strategies |
| s56-s81 | V4 production strategies |
| s85-s98 | V4 experimental (overlays, MACD, etc.) |
| s100-s120 | Auto-research batch (all killed) |
| s300+ | Research strategies (s316-s320) |

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
7. V4 portfolio backtest        (~5 min)
8. Classify and decide          (~1 min)
Total: ~30 min from idea to validated result
```

**Enablers:** Vectorized strategies (<30s validation), context caching, parallel workers, rolling helpers.

### Iteration Anti-Patterns

- Don't add more signals to a losing strategy -- archive and try a different approach
- Don't over-fit to specific tokens (works on 5 = likely curve-fitted)
- Don't tune parameters without re-validating (every change needs fresh validation run)
- Don't skip the knowledge base check (#1 time waste = reimplementing known failures)
- Don't trust pre-MTM return numbers -- always verify with post-MTM equity
- Don't cite compounded returns as realistic performance

---

## 8. V4 PORTFOLIO DEVELOPMENT WORKFLOW

### When to Use V4 (vs V3)

Use V4 when building **portfolio components** — strategies that may be individually weak but contribute to portfolio-level edge through diversification, regime coverage, or low correlation.

Use V3 when testing **new hypotheses** — strategies that need to prove per-token robustness before being considered.

### V4 Fast Path (Idea -> Portfolio Component)

```
1. Gate 0: Idea screening (same as V3)           (~5 min)
2. V4 12-month backtest (post-MTM, $2M cap)       (~5 min)
   python v4/rank_all_portfolios.py
3. V4 OOS test (train->Dec, trade Jan-Mar)         (~5 min)
4. Portfolio complement test                       (~10 min)
   Add to portfolio, check return/Sharpe/MaxDD improvement
5. Paper trading (if passes gates 1-3)             (~1-4 weeks)
Total: ~25 min from idea to portfolio-validated result
```

### Current Reality (March 2026)

**Only funding carry strategies are profitable:**
- s62 (conservative funding carry): +9.6% post-MTM
- s65 (funding carry): +4.9% post-MTM

**All momentum strategies are losing:** s56 (-27.4%), s60 (-70.5%), s69 (-27.4%)

**Carry + momentum combos also lose:** s58 (-32.5%), s58+s65 (-20.9%), 4-edge (-21.2%)

**Root cause:** Sideways market Jan-Mar 2026 kills momentum. Carry survives but doesn't compensate for momentum losses in combined portfolios.

See `knowledge/process/STRATEGY_PIPELINE_GATES.md` for full gate details and code templates.
