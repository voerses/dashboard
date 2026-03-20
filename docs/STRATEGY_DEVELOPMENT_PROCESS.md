# Strategy Development Process: s98 Case Study & Tooling Guide

## Overview

This document captures the systematic process used to develop s98 (Regime-State MACD Squeeze), the tooling created along the way, and identifies gaps and opportunities for future tooling improvements.

## Final Result: s98

| Metric | Value |
|--------|-------|
| Annual Return | +113.8% |
| Calmar | 4.75 |
| Max Drawdown | -24.0% |
| Sharpe | 1.55 |
| Sortino | 1.17 |
| Win Rate | 57.4% |
| Profit Factor | 2.11 |
| Trades | 129 |
| Avg Hold | 74h |
| Leverage | 7x |
| Market | PERP |

---

## Phase 1: Signal Research (Foundational Analysis)

**Tool:** `tools/signal_research.py`

Before choosing any signal, we characterized the raw return series using spectral analysis, autocorrelation, indicator predictive power, and cost breakeven calculations.

**Key findings that shaped all subsequent work:**
- 67% of crypto price variance is noise (1-6h frequencies)
- Only weekly momentum (168h) showed significant autocorrelation (AC=+0.06)
- All individual indicator correlations with future returns < 0.08
- Cost per round-trip: ~0.1% for liquid tokens ($50M+ ADV)
- Minimum edge needed to overcome costs: PF > 1.05 before fees

**Lesson:** Don't build signals on noise. Target weekly+ holding periods and use filters aggressively to trade only high-probability setups.

### How to use:
```bash
/workspace/venv/bin/python tools/signal_research.py
```
Outputs spectral plots, autocorrelation tables, indicator IC rankings, and cost breakeven thresholds.

---

## Phase 2: Broad Signal Screening (Elimination)

**Tool:** `tools/signal_sweep.py`
**Configs tested:** 32 (8 signals x 4 trade management configs)

Tested 8 signal types: EMA cross, EMA state, RSI extreme, Donchian breakout, BB breakout, MACD acceleration, taker ratio, ADX+DI crossover.

**Result:** All 32 configs lost money (Ann -56% to -90%, PF 0.48-0.82). Raw signals without quality filters produce too many low-conviction trades.

**Key insight:** The signal itself matters less than the filters around it. MACD zero-cross showed the least-bad results, so it was selected as the base signal for further filtering.

### How to use:
```bash
/workspace/venv/bin/python tools/signal_sweep.py
```
Writes strategy file, runs backtest, parses results for each signal x config combination. Results saved to `results/v4/signal_sweep.json`.

---

## Phase 3: Parameter Grid Search

**Tool:** `tools/rapid_sweep.py`
**Configs tested:** 864 per signal type (7 signals x ~864 combos = ~6,000 total)

Full parameter grid over leverage, stop, trail, target, grace period, min/max hold, and breakeven for each signal variant.

### How to use:
```bash
/workspace/venv/bin/python tools/rapid_sweep.py --signal macd_zero_cross --max-combos 200
```
Parameters: `--signal` (one of 7 types), `--max-combos` (subsample large grids), `--target-metric` (sort by calmar/annual/sharpe).

Results saved to `results/v4/sweep_{signal_name}.json`.

---

## Phase 4: Focused Iteration (PF > 1.0 Breakthrough)

**Tool:** `tools/focused_sweep.py`
**Configs tested:** 21

Narrowed to MACD zero-cross with explicit liquidity filtering. Tested ADV thresholds ($100M, $500M, $1B), leverage variants, stop/trail configurations.

**Breakthrough:** `combo_1B` — first config to achieve PF > 1.0 (PF=1.04, Ann=+2.1%, DD=-20.4%). The ultra-liquid filter ($1B ADV) was the difference maker.

### How to use:
```bash
/workspace/venv/bin/python tools/focused_sweep.py
```

---

## Phase 5: Amplification (Regime Filter Discovery)

**Tool:** `tools/amplify_sweep.py`
**Configs tested:** 21

Added advanced features on top of the PF>1.0 base: signal quality filters (RSI, volume, EMA alignment), leverage amplification, BB squeeze, and critically — **regime filtering** (UPTREND/DOWNTREND only).

**Major breakthrough:** Regime filter alone took the strategy from Ann=-24% (unfiltered) to Ann=+104% (regime-filtered). This was the single biggest alpha driver discovered in the entire process.

### How to use:
```bash
/workspace/venv/bin/python tools/amplify_sweep.py
```

---

## Phase 6: Drawdown Reduction

**Tool:** `tools/regime_drill.py`
**Configs tested:** 33

The regime-filtered strategy had Ann=+104% but DD=-51.5%. This tool systematically explored drawdown reduction: lower leverage, stop losses, tighter trails, shorter holds, higher quality entries.

**Key finding:** Adding BB squeeze filter (width < 80% of rolling average) improved PF from 1.52 to 1.92 while reducing DD from -51.5% to -31.7%.

### How to use:
```bash
/workspace/venv/bin/python tools/regime_drill.py
```

---

## Phase 7: Squeeze Depth Optimization

**Tool:** `tools/final_push.py`
**Configs tested:** 36

Explored squeeze tightness (0.5x to 0.8x), BB lookback period (120h to 240h), and leverage ladders on each combination.

**Key finding:** Tight squeeze (0.6x) achieved DD<20% for the first time (Cal=3.19, DD=-19.8%). The 240h BB lookback provided more stable squeeze detection.

### How to use:
```bash
/workspace/venv/bin/python tools/final_push.py
```

---

## Phase 8: Leverage Optimization

**Tool:** `tools/leverage_push.py`
**Configs tested:** 27

Systematic leverage ladder (3x to 20x) on best squeeze configs. Mapped the return-vs-drawdown tradeoff curve.

**Key finding:** Leverage beyond 12x destroys edge via fee drag. 7x was the sweet spot for Calmar optimization. Fee drag at 20x leverage consumed >$200k (more than initial capital).

### How to use:
```bash
/workspace/venv/bin/python tools/leverage_push.py
```

---

## Phase 9: Signal Architecture (Regime-State Discovery)

**Tool:** `tools/multi_signal_push.py`
**Configs tested:** 22

Tested three signal architectures:
1. **Union of 3 signals** (MACD + EMA cross + BB breakout) — more trades, lower quality
2. **2-of-3 agreement** — fewer trades, better quality but too few
3. **Regime-state dual entry** — MACD cross within existing trend OR regime change with EMA/MACD confirmation

**Key finding:** Regime-state architecture produced Calmar=4.50, the best risk-adjusted result. The dual-entry approach captures both trend continuation (MACD cross in regime) and early trend entry (regime change with confirmation).

### How to use:
```bash
/workspace/venv/bin/python tools/multi_signal_push.py
```

---

## Phase 10: Capital Scaling Validation

**Tool:** `tools/rstate_leverage.py`
**Configs tested:** 37

Validated the regime-state signal across capital sizes ($20k, $50k, $200k) and leverage levels. Tested stop + breakeven combos, tighter trails, and lower ADV thresholds.

**Key finding:** The strategy scales well from $20k to $200k. At $50k with 10x leverage, Ann=+128%, Cal=4.01. No config achieved 300%+ annual with DD<20% — a fundamental tradeoff given 129 trades/year.

### How to use:
```bash
/workspace/venv/bin/python tools/rstate_leverage.py
```

---

## Phase 11: Position Sizing (Final Config)

**Tool:** `tools/concentration_push.py`
**Configs tested:** 30

Tested concentration limits (10% to 100% of capital per position), max position counts (3 to 15), ADV caps, and leverage x concentration interactions.

**Key finding:** Concentration=0.20 with BB240 at 7x leverage produced the final config: Ann=+113.8%, Cal=4.75, DD=-24.0%, PF=2.11. Higher concentration didn't help due to ADV-capping on $1B+ liquid tokens.

### How to use:
```bash
/workspace/venv/bin/python tools/concentration_push.py
```

---

## Phase 12: Portfolio Comparison

**Tool:** `tools/run_all_portfolios.py`

Runs 12-month backtests for all portfolios in `configs/multi_v4_paper.json` and ranks them.

**Result:** s98 was the only profitable strategy out of 21 portfolios tested. All legacy strategies (s56-s92) showed negative returns under the realistic V4 backtester.

### How to use:
```bash
/workspace/venv/bin/python tools/run_all_portfolios.py
```
Results saved to `results/v4/all_21_portfolios_12mo.json`.

---

## Development Arc: The Funnel

```
signal_research.py     → Characterized return series, identified cost breakeven
       ↓
signal_sweep.py        → 32 configs, all losers → MACD selected as base signal
       ↓
rapid_sweep.py         → 6,000+ parameter combos → best trade mgmt params
       ↓
focused_sweep.py       → 21 configs → first PF>1.0 (ultra-liquid $1B filter)
       ↓
amplify_sweep.py       → 21 configs → REGIME FILTER breakthrough (+104% annual)
       ↓
regime_drill.py        → 33 configs → BB SQUEEZE filter (PF 1.52→1.92)
       ↓
final_push.py          → 36 configs → tight squeeze achieves DD<20%
       ↓
leverage_push.py       → 27 configs → 7x optimal, >12x destroys edge
       ↓
multi_signal_push.py   → 22 configs → REGIME-STATE dual entry (Cal=4.50)
       ↓
rstate_leverage.py     → 37 configs → validated across capital sizes
       ↓
concentration_push.py  → 30 configs → concentration=0.20 final (Cal=4.75)
       ↓
run_all_portfolios.py  → confirmed s98 is only profitable strategy
```

**Total: ~7,000+ backtests across 11 tools over 11 iterative phases.**

---

## Three Key Alpha Drivers Discovered

| Driver | Impact | Discovery Phase |
|--------|--------|----------------|
| **Regime filter** (UPTREND/DOWNTREND only) | Ann: -24% → +104% | Phase 5 (amplify_sweep) |
| **BB squeeze** (volatility compression) | PF: 1.52 → 1.92, DD: -51% → -32% | Phase 6 (regime_drill) |
| **Ultra-liquid filter** ($1B+ ADV) | PF: 0.86 → 1.04 | Phase 4 (focused_sweep) |

---

## Gaps & Opportunities for Better Tooling

### Gap 1: No Automated Walk-Forward Validation in Sweep Tools
**Problem:** All sweep tools run a single 12-month in-sample backtest. There's no out-of-sample validation built into the sweep loop. We could be overfitting to the 12-month window.

**Opportunity:** Build a `tools/robust_sweep.py` that runs each config through walk-forward validation (e.g., 3-month train, 1-month test, rolling) and reports both in-sample and out-of-sample metrics. Kill configs where OOS Calmar < 50% of IS Calmar.

### Gap 2: No Cross-Correlation Between Configs
**Problem:** When testing 30+ configs, many produce similar trade sets. We can't see which configs are truly independent vs. just parameter variations of the same trades.

**Opportunity:** Build a `tools/config_correlation.py` that takes sweep results and computes pairwise trade overlap (Jaccard similarity of entry dates/tokens). Cluster configs by similarity to identify truly distinct strategies vs. parameter noise.

### Gap 3: No Regime-Aware Backtest Decomposition
**Problem:** A strategy might show +113% annual overall but could be +300% in uptrends and -50% in downtrends. We don't decompose returns by regime automatically.

**Opportunity:** Build a `tools/regime_decompose.py` that splits equity curves by regime period and reports per-regime metrics (return, DD, PF, trade count). This would identify strategies that only work in one regime type.

### Gap 4: No Automated Signal Discovery Pipeline
**Problem:** Each signal type (MACD, EMA, RSI, etc.) was tested manually. Discovering that regime filtering was the key took 5 phases of iteration.

**Opportunity:** Build a `tools/auto_signal_discovery.py` that:
1. Takes a base signal template
2. Automatically adds/removes each available filter (regime, squeeze, liquidity, ADX, DI, volume, EMA alignment)
3. Runs a factorial experiment (2^N combinations for N filters)
4. Ranks by marginal contribution of each filter to PF/Calmar
5. Outputs: "Filter X improves PF by +0.3, Filter Y improves Calmar by +1.2"

This would have found the regime filter in Phase 2 instead of Phase 5.

### Gap 5: No Fee Impact Analyzer
**Problem:** High leverage destroys edge through fee drag, but this wasn't obvious until we tested leverage ladders manually. The breakeven analysis in signal_research.py doesn't account for leverage-amplified fees.

**Opportunity:** Build a `tools/fee_impact.py` that takes a strategy's trade log and models: (1) fees at each leverage level, (2) the leverage level where fees consume >50% of gross PnL, (3) optimal leverage given the strategy's gross edge and trade frequency.

### Gap 6: No Multi-Timeframe Signal Testing
**Problem:** All signals use 1h indicators. Some patterns might be stronger on 4h or daily timeframes, but there's no easy way to test.

**Opportunity:** Extend the engine to support multi-timeframe indicators (4h, 1D candles derived from 1h data) and build `tools/timeframe_sweep.py` to test the same signal logic across timeframes.

### Gap 7: No Strategy Combination Optimizer
**Problem:** When combining strategies in a portfolio (like s58+s65), weights are set manually. There's no optimization of portfolio weights based on correlation and return profiles.

**Opportunity:** Build a `tools/portfolio_optimizer.py` that:
1. Runs each strategy individually to get equity curves
2. Computes pairwise correlations
3. Uses mean-variance or risk-parity optimization to find optimal weights
4. Reports: optimal allocation, expected portfolio Calmar, diversification benefit

### Gap 8: No Continuous Monitoring of Strategy Decay
**Problem:** Strategies are validated once and then paper traded. There's no automated detection of when a strategy's edge is decaying (rolling Calmar dropping, PF declining).

**Opportunity:** Build a `tools/decay_monitor.py` that:
1. Reads paper trading state files
2. Computes rolling 30/60/90-day metrics
3. Alerts when rolling Calmar < 0 or rolling PF < 1.0 for 30+ days
4. Compares live performance vs. backtest expectations

### Gap 9: No Parallelized Sweep Execution
**Problem:** Sweep tools run configs sequentially. A 30-config sweep takes ~3 minutes (6s per config). A 1000-config grid takes ~100 minutes.

**Opportunity:** Add `--workers N` flag to all sweep tools for parallel execution using `multiprocessing.Pool`. This would speed up large grids by 4-8x.

### Gap 10: No Standardized Sweep Template
**Problem:** Each sweep tool (11 total) has its own copy of `parse_result()`, its own strategy template, and its own config format. Adding a new sweep requires copying ~200 lines of boilerplate.

**Opportunity:** Create a `tools/sweep_framework.py` base module that provides:
1. Standard `parse_result()` function
2. Strategy template writer
3. Config runner with progress bar
4. Results ranker and JSON output
5. CLI with `--workers`, `--target-metric`, `--max-combos` flags

New sweeps would only need to define: (1) the strategy template, (2) the config list.

---

## Quick Start: Creating a New Strategy

1. **Research the signal:** Run `signal_research.py` to understand return characteristics
2. **Screen signals:** Modify `signal_sweep.py` with your candidate signals, run 32-config screen
3. **Grid search:** Use `rapid_sweep.py` on the best signal to find optimal trade management
4. **Add filters:** Use `amplify_sweep.py` pattern — test regime, squeeze, liquidity, quality filters
5. **Optimize drawdown:** Use `regime_drill.py` pattern — systematically reduce DD while preserving return
6. **Find optimal leverage:** Use `leverage_push.py` — map the return-vs-DD tradeoff curve
7. **Size positions:** Use `concentration_push.py` — find optimal concentration given liquidity constraints
8. **Compare to existing:** Use `run_all_portfolios.py` — verify new strategy adds value

Each tool writes results to `results/v4/` as JSON for post-analysis.
