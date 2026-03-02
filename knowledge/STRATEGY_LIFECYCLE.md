# Strategy Lifecycle — Research to Production Pipeline

> **TL;DR — Tier system and development pipeline**
> - Tier A (>50%): production; Tier B (20-50%): experimental, max 3 cycles; Tier C (<20%): archive immediately
> - Pre-dev checklist: 7 knowledge files + sweep results + bias audit + cost verification
> - Fast iteration: idea to validated result in ~25 min (vectorized + parallel workers)
> - Dedup: >80% overlap with Tier A → propose as filter instead of new strategy
> **When to read full file:** Starting a new strategy, understanding tiers, deciding promote/demote/archive
> **Sections:** 1-Tier System (current classification), 2-Mandatory Checklist, 3-Pipeline Phases, 4-Promotion Rules, 5-Sweep Protocol, 6-Numbering, 7-Fast Iteration

## Overview

Every strategy follows a strict lifecycle: **Research → Prototype → Validate → Promote/Archive**.
No strategy ships to production without passing the V3 dual gate. No strategy gets written
without consulting the knowledge base first.

---

## 1. TIER SYSTEM

Strategies are classified by V3 validation rate (% of 49 tokens passing dual WF+CPCV gate):

| Tier | Validation Rate | Status | Location | Action |
|------|----------------|--------|----------|--------|
| **A** | > 50% | Production | `strategies/` | Deploy, monitor, iterate |
| **B** | 20–50% | Experimental | `strategies/` | Investigate, tune, re-validate |
| **C** | < 20% | Archive | `strategies/archive/` | Moved out, preserved for reference |

### Current Classification (2026-03-01)

**Tier A (Production):**
| Strategy | Rate | Key Edge |
|----------|------|----------|
| s11_momentum_burst | 75.5% | 3% hourly return + ADX + volume |
| s09_optimized_trend | 73.5% | Multi-TF dual momentum |
| s13_vol_weighted_tsmom | 67.3% | Volume-weighted time-series momentum |
| s21_skew_momentum | 63.3% | Return skew + momentum acceleration |
| s17_trend_strength_filter | 55.1% | ADX gradient + EMA alignment |
| s18_momentum_accel | 51.0% | Momentum acceleration (change in momentum) |

**Tier B (Experimental):**
| Strategy | Rate | Issue |
|----------|------|-------|
| s20_low_beta_quality | 46.9% | Close to promotion threshold |
| s22_supertrend_adx | 46.9% | Supertrend flip timing noisy |
| s12_quality_breakout | 32.7% | BB squeeze too rare on most tokens |
| s15_vol_regime_breakout | 30.6% | Vol regime detection needs work |
| s14_microstructure_edge | 26.5% | Spread estimator works on liquid only |
| s10_research_dip_buy | 24.5% | Dip-buy timing inconsistent |

**Tier C (Archived):**
| Strategy | Rate | Reason |
|----------|------|--------|
| s07_rsi_bounce | 10.2% | RSI alone insufficient post-ETF |
| s08_obv_divergence | 6.1% | OBV divergence doesn't predict crypto moves |
| s16_composite_factor | 0.0% | Too many weak signals combined = noise |
| s19_mean_reversion_filtered | 0.0% | Mean reversion loses money even with filters |

---

## 2. MANDATORY KNOWLEDGE BASE CHECK

**Before writing ANY strategy, indicator, or engine code, consult these files:**

### Pre-Development Checklist

```
[ ] Read knowledge/PERFORMANCE_PATTERNS.md
    - No Python for-loops over bar arrays
    - Use rolling_* helpers from engine
    - Use pre-computed indicators from ctx.ind_1h/ctx.custom
    - Target < 1ms per call on 40K bars

[ ] Read knowledge/SIGNAL_DEVELOPMENT.md
    - Signal structure: Regime → Trend → Entry → Exit → Sizing
    - Use proper signal composition patterns
    - Include all mandatory components

[ ] Read knowledge/INDICATOR_CATALOG.md
    - Check if indicator already exists in engine
    - Check IC (information coefficient) for relevance
    - Check post-ETF vs pre-ETF signal stability

[ ] Read knowledge/STRATEGY_CATALOG.md
    - Check if strategy type already implemented
    - Check academic backing and expected profile
    - Check our own backtest findings for this approach

[ ] Read knowledge/INDICATOR_CATALOG.md Section 10 (Empirical Results)
    - Verify signal IC is positive and significant post-ETF
    - Check signal decay rate across horizons
    - Check regime-conditional performance

[ ] Check results/sweep_summary_*.json
    - Review current tier classifications
    - Avoid duplicating existing approaches
    - Identify gaps in strategy coverage

[ ] Read knowledge/KRAKEN_FEES.md (Sections 8 & 11)
    - Verify backtest cost assumptions match your target exchange and volume tier
    - Tier 1 tokens: 0.30% per side, Tier 3 tokens: 0.60% per side
    - Do NOT use Binance base-tier costs (0.15%) for Kraken strategies

[ ] Run bias audit checklist (knowledge/process/BACKTESTING_VALIDATION_BEST_PRACTICES.md §16)
    - Look-ahead: no full-array statistics in regime/indicator code
    - CPCV: non-contiguous folds properly masked
    - Costs: tier-based, not flat
```

### When to Skip

Only Tier 0 changes (typo fixes, parameter tweaks within existing strategies) can skip the
full checklist. Any new strategy or new indicator requires ALL checks.

---

## 3. RESEARCH → PROTOTYPE PIPELINE

### Phase 1: Research & Ideation

**Input:** Academic papers, Signal Lab IC analysis, market structure observations.

**Process:**
1. Identify a candidate signal from Signal Lab (`tools/signal_lab.py`)
   - IC must be > +0.02 post-ETF (Jan 2024+) to proceed
   - Signal must be stable across at least 2 horizons (1d, 5d, 10d)
   - Check if signal flipped sign post-ETF (some did — see s22_supertrend_adx)

2. **DEDUPLICATION CHECK (mandatory, before ANY code is written)**
   - Read `strategies/README.md` for existing strategy summaries
   - Read `knowledge/STRATEGY_LIFECYCLE.md` tier classifications for current status
   - Compare your proposed entry signal against ALL existing Tier A/B entry signals:

   | Strategy | Core Entry Signal | Signal Type |
   |----------|-------------------|-------------|
   | s11 (75.5%) | `ret_1 > 0.03` | Momentum burst |
   | s09 (73.5%) | EMA stack + daily EMA50 + ADX > 30 | Dual momentum |
   | s13 (67.3%) | Volume-weighted cumulative returns | Vol-weighted TSMOM |
   | s21 (63.3%) | `rolling_skew > 0.3` + ret > 0 | Skew momentum |
   | s17 (55.1%) | `ret_1 > 0.02` + ADX > 25 + +DI > -DI | Trend strength burst |
   | s18 (51.0%) | `ret_24h > ret_72h/3` (acceleration) | Momentum acceleration |
   | s20 (46.9%) | Low beta + low vol-of-vol + trend | Quality factor |
   | s22 (46.9%) | Supertrend flip + ADX > 20 | Trend reversal |
   | s12 (32.7%) | BB squeeze + volume expansion | Breakout |
   | s15 (30.6%) | Low vol regime → expansion + breakout | Vol regime |
   | s14 (26.5%) | Corwin-Schultz spread + microstructure | Microstructure |
   | s10 (24.5%) | Sharp drop + RSI oversold + support | Dip buy |

   **If your entry signal overlaps > 80% with an existing strategy:**
   - Do NOT create a new strategy
   - Instead: propose adding your signal as an additional FILTER to the existing strategy
   - Or: propose a parameter variant that can be tested as a config change

   **If your entry signal is genuinely different (< 30% overlap):**
   - Proceed to prototype
   - Document what makes it distinct from existing approaches

3. Check existing strategies
   - Does any Tier A/B strategy already exploit this signal?
   - If yes: consider adding it as a FILTER to existing strategy instead of new one
   - If no: proceed to prototype

3. Document the hypothesis
   - What market microstructure creates this edge?
   - Why hasn't it been arbitraged away?
   - What regime does it work in? (trending, ranging, crisis?)
   - Expected profile: win rate, payoff ratio, trade frequency

**Output:** Entry in a research log with signal IC, hypothesis, and go/no-go decision.

### Phase 2: Prototype

**Input:** Validated research hypothesis + knowledge base check.

**Process:**
1. Copy `strategies/TEMPLATE.py` → `strategies/sNN_name.py`
2. Follow the signal structure from `knowledge/SIGNAL_DEVELOPMENT.md`:
   - Regime filter (mandatory)
   - Trend alignment (mandatory for trend strategies)
   - Entry signal (the core hypothesis)
   - Exit logic (regime-based + trailing stop)
3. All code MUST be vectorized (see `knowledge/PERFORMANCE_PATTERNS.md`)
4. Run performance check:
   ```python
   # Must complete in < 1ms per call
   python -c "
   import sys; sys.path.insert(0, 'v3')
   from engine import Engine
   import pandas as pd, time
   eng = Engine(data_dir='data')
   df = pd.read_parquet('data/1h_cache/BTC_1h.parquet')
   ctx = eng._build_context('BTC', df)
   from strategies.sNN_name import strategy
   t0 = time.perf_counter()
   for _ in range(1000): strategy(ctx)
   print(f'{(time.perf_counter()-t0)/1000*1000:.3f}ms/call')
   "
   ```

**Output:** Strategy file that passes performance check.

### Phase 3: Quick Validation (Single Token)

**Process:**
1. Run V3 validation on BTC only (fastest, most liquid):
   ```bash
   cd /workspace/crypto_backtest
   /workspace/venv/bin/python v3/validation.py --strategy sNN --tokens BTC --workers 1
   ```
2. Check: Does BTC pass dual gate?
3. If NO: review entry logic, check if signal timing is off, tune parameters
4. If YES: proceed to full validation

**Gate:** BTC must pass dual gate to continue. If BTC fails after 3 tuning attempts,
the hypothesis is likely wrong — archive the prototype.

### Phase 4: Full Validation

**Process:**
1. Run V3 validation on all 49 tokens:
   ```bash
   /workspace/venv/bin/python v3/validation.py --strategy sNN --workers 4
   ```
2. Record validation rate and classify into tier

**Gate:**
- Rate > 50% → Tier A (production)
- Rate 20–50% → Tier B (experimental, iterate)
- Rate < 20% → Tier C (archive immediately)

### Phase 5: Iterate or Archive

**Tier B strategies** get up to 3 iteration cycles:
1. Analyze which tokens fail — is it a specific regime? Liquidity? Market cap?
2. Adjust parameters (NOT logic) — wider/tighter filters, different thresholds
3. Re-validate after each change
4. If rate doesn't improve past 50% after 3 cycles → archive

**Tier C strategies** are archived immediately:
1. Move to `strategies/archive/`
2. Add entry to archive log with reason and validation date
3. Strategy preserved for reference but excluded from sweeps

---

## 4. PROMOTION RULES

### Tier B → Tier A Promotion

A Tier B strategy is promoted when:
- Validation rate exceeds 50% on the latest sweep
- Rate has been stable or improving across 2+ sweep dates
- No overlap > 80% with existing Tier A entry signals (checked via correlation)

### Tier A → Tier B Demotion

A Tier A strategy is demoted when:
- Validation rate drops below 40% (10% buffer below threshold)
- Decline persists across 2 consecutive sweeps
- Or: single catastrophic sweep where rate drops below 20%

### Archive → Experimental Resurrection

An archived strategy can be resurrected if:
- New Signal Lab data shows the underlying signal has strengthened
- Market structure has changed (e.g., new ETF approval, regime shift)
- A new composition approach is identified

---

## 5. SWEEP PROTOCOL

### Regular Sweeps

Run the full 16-strategy sweep after:
- Any strategy code change
- New data ingestion (monthly market data update)
- Engine/indicator changes that affect strategy inputs

```bash
# Full sweep — all strategies, all tokens
/workspace/venv/bin/python v3/validation.py \
  --strategy s07 s08 s09 s10 s11 s12 s13 s14 s15 s16 s17 s18 s19 s20 s21 s22 \
  --workers 4 --data-dir data
```

### Sweep Output

Results saved to `results/sweep_summary_YYYYMMDD.json`.
Compare across dates to track strategy evolution.

### Post-Sweep Actions

1. Update tier classifications in this document
2. Archive any new Tier C strategies
3. Promote any Tier B → Tier A strategies
4. Investigate any Tier A → Tier B demotions
5. Commit updated sweep results

---

## 6. STRATEGY NUMBERING

| Range | Category | Notes |
|-------|----------|-------|
| s01–s06 | Legacy V1 | Not in current system |
| s07–s10 | V2 initial batch | Mixed results |
| s11–s16 | Signal Lab batch 1 | Based on IC analysis |
| s17–s22 | Signal Lab batch 2 | Post-ETF adapted |
| s23+ | Future | Next available: s23 |

When archiving, the strategy number is preserved. A new strategy always gets the next
available number — never reuse an archived number.

---

## 7. FAST ITERATION WORKFLOW

For rapid hypothesis testing:

```
1. Signal Lab IC check          (~2 min)
2. Knowledge base review        (~5 min)
3. Write vectorized strategy    (~15 min)
4. Performance check (< 1ms)    (~1 min)
5. BTC-only quick validation    (~20 sec)
6. Full 49-token validation     (~20 sec per strategy)
7. Classify and decide          (~1 min)
─────────────────────────────────────────
Total: ~25 min from idea to validated result
```

The key enablers:
- **Vectorized strategies** keep validation under 30s per strategy
- **Context caching** avoids recomputing indicators across strategies
- **Parallel workers** distribute token validation across CPU cores
- **Rolling helpers** eliminate the need to write custom rolling code

### Iteration Anti-Patterns

- **Don't add more signals to a losing strategy.** If the core hypothesis is wrong,
  more filters won't fix it. Archive and try a different approach.
- **Don't over-fit to specific tokens.** If a strategy only works on 5 tokens,
  it's likely curve-fitted. A real edge works broadly.
- **Don't tune parameters without re-validating.** Every parameter change requires
  a fresh V3 validation run.
- **Don't skip the knowledge base check.** The #1 waste of time is reimplementing
  something that already exists or repeating a known failure.
