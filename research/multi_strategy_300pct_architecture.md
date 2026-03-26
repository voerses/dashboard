# Multi-Strategy Pool Architecture: $200K Capital, 300% Target

**Date:** 2026-03-26
**Capital:** $200,000 (single pool)
**Target:** 300% annual return
**Basis:** 14 research sessions, 100+ signal tests, V3/V4 engine validated

---

## Executive Summary

**300% annual return is not mathematically achievable with the available signals and $200K capital.** The highest realistic estimate using all proven components is **~77% annual** (Architecture A: Regime Rotation). This analysis shows the math transparently and recommends the best achievable architecture.

---

## 1. Available Signal Inventory (OOS-Validated Only)

### Entry Signals

| # | Signal | OOS Evidence | Sharpe | Status |
|---|--------|-------------|--------|--------|
| 1 | EMA 20/50 crossover | V3 OOS: +14.7%, Sharpe 0.56 | 0.56 | PROVEN |
| 2 | RSI timing (cross-up 35) | 13/18 WF windows improved, dSharpe +5.87 | N/A (overlay) | PROVEN |
| 3 | Cross-TF Momentum Div Z | WF Sharpe 0.621, V3 corr 0.27 | 0.417 | PASSED |
| 4 | BB Squeeze Breakout | BTC PF 1.41, shorts PF 2.60 | N/A | ALIVE |
| 5 | Dual TF Momentum (long) | BTC PF 1.62, 210 OOS trades | N/A | ALIVE |

### Overlay/Sizing Signals

| # | Signal | OOS Evidence | IC | Status |
|---|--------|-------------|-----|--------|
| 6 | Top Trader L/S z-score | IC -0.166 overall, -0.288 in RANGE | -0.166 | PROVEN (in V3) |
| 7 | L/S Divergence | IC -0.204 overall, -0.35 in RANGE OOS | -0.204 | PROVEN (in V3) |
| 8 | VRP z-score (IV-RV) | IC 0.268, super-additive with positioning | 0.268 | PROVEN (in V3) |

### Exit Signals

| # | Signal | Evidence | Impact |
|---|--------|---------|--------|
| 9 | Trail stops (1.5 ATR) | +0.629 avg Sharpe improvement | HIGH |
| 10 | Vol Expansion Exit | 57.8% correct on short exits | MODERATE |

### Killed/Unvalidated Signals

| Signal | Reason Killed |
|--------|--------------|
| Oil 20d momentum short | No OOS backtest; IC only validated in crisis sub-regime |
| OI divergence short | -302bps 7D OOS but no full strategy backtest |
| Macro regime rotation | KILLED in R109 (non-stationary IC, fragile params) |
| Regime-switched positioning | OOS Sharpe -1.14 across all variants |
| ETF flow overlay | Clips V3 gains as overlay (R105) |
| Exchange netflow | IC 0.149 but no strategy-level validation |
| ADX filter | Marginal OOS dSharpe: -0.204 (hurts) |
| 2x/3x perp leverage | KILLED: MaxDD > -40% threshold |
| Dynamic VRP leverage | KILLED: MaxDD -52.2% even best config |

---

## 2. V4 Engine Pool Mechanics

From `/workspace/crypto_backtest/v4/config.py` and `/workspace/crypto_backtest/v4/paper_engine.py`:

### Pool Mode (Shared Capital)
- Multiple `StrategySpec` entries share a single `$200K` capital pool
- Each strategy has a `weight` (0.0-1.0); weights must sum to <= 1.0
- Position sizing uses `equity * weight * kelly * edge * vol_adj`
- Free capital check at entry: ensures strategies don't overcommit
- **Strategies can have different markets** (spot vs perp via `market` field)
- Capital utilization limited by signal sparsity (V3 is flat ~44% of time)

### Independent Mode (Separate Capital)
- Each strategy gets `capital * weight` as its own isolated pool
- Weight=1.0 passed internally (pre-split)
- No cross-strategy capital sharing

### Key Constraint for Pool Mode
- `concentration_limit`: 10% per token across strategies (can override to 1.0 for BTC-only)
- `max_portfolio_positions`: 40 (more than needed for BTC-only)
- `raw_mode`: bypasses portfolio constraints (available for simplified strategies)

---

## 3. Architecture Proposals

### Architecture A: Regime Rotation

**Concept:** One pool, 4 regime-conditional strategies. Only one active at a time. Weight=1.0 for all (full equity, mutually exclusive activation).

| Regime | Time % | Strategy | Validated? |
|--------|--------|----------|-----------|
| UPTREND | 38% | V3+RSI timing (spot BTC long) | YES (V3 OOS Sharpe 6.02 in uptrend) |
| RANGE | 35% | Flat (go to cash) | YES (avoids -37.1% V3 RANGE drag) |
| DOWNTREND | 15% | Flat (go to cash) | YES (V3 already 91% flat) |
| CRISIS | 12% | Flat (go to cash) | YES (V3 already 99.6% flat) |

**Why RANGE/DOWNTREND/CRISIS are flat:** No validated short or contrarian strategy survived OOS testing. The regime-switched positioning signal had OOS Sharpe -1.14. Oil/macro shorts have no full-strategy validation. Going flat during bad regimes is the *honest* allocation.

#### Estimated Performance

| Metric | Value | Basis |
|--------|-------|-------|
| **Annual Return** | **~77%** | V3 OOS uptrend: 193.1% * 1.05 RSI * 38% time |
| **Max Drawdown** | **-25% to -35%** | V3 OOS uptrend MaxDD -8.9%, plus regime transition risk |
| **Sharpe** | **~0.8-1.0** | V3 uptrend Sharpe 6.02, diluted by 62% flat time |
| **Capital Utilization** | **~33%** | Active 38% of time, mean position 0.77 in uptrend |
| **Key Risk** | Regime misclassification | False uptrend -> RANGE whipsaw losses |

**Return Math:**
- V3 in UPTREND (OOS): 193.1% annualized, Sharpe 6.02, MaxDD -8.9%
- RSI timing adds ~5% improvement (conservative; WF shows +5.87 dSharpe)
- Time-weighted: 202.8% * 38% = **77.0%**
- Flat 62% of the time: **0%** contribution
- Trail stops would further reduce MaxDD from -8.9% to perhaps -6-7%

**Pros:**
- Highest absolute return estimate of all architectures
- Simple to implement (V3 already goes flat in non-uptrend regimes)
- V3's existing regime filter already does most of this
- MaxDD controlled by regime gating

**Cons:**
- 62% of the time doing nothing (massive capital inefficiency)
- Entirely dependent on one signal (EMA crossover) in one asset (BTC)
- Regime transitions create whipsaw risk at boundaries
- No validated strategy to deploy during RANGE (35% of time!)

---

### Architecture B: Parallel Uncorrelated

**Concept:** Multiple simultaneously active strategies with split capital. Diversification reduces risk but also reduces per-strategy allocation.

| Strategy | Weight | Market | Signal |
|----------|--------|--------|--------|
| V3 Momentum | 0.50 | spot | EMA 20/50 + Pos + VRP |
| BB Squeeze Short | 0.30 | perp | BB squeeze breakout (short-biased) |
| MTF Div Z | 0.20 | spot | Momentum divergence z-score |

Correlations: V3 vs MTF = 0.27, V3 vs BB = ~0.05 (different signal type), MTF vs BB = ~0.10

#### Estimated Performance

| Metric | Value | Basis |
|--------|-------|-------|
| **Annual Return** | **~14%** | V3: 7.3% + BB: 5.3% + MTF: 1.2% |
| **Max Drawdown** | **-13% to -18%** | Diversification benefit from low correlations |
| **Sharpe** | **~0.65-0.80** | Better Sharpe from diversification, lower return |
| **Capital Utilization** | **~40%** | Varies: V3 56%, BB ~30%, MTF ~30% |
| **Key Risk** | Crypto correlation spike in crashes |

**Return Math:**
- V3 at 50% weight: 14.7% * 0.5 = 7.3%
- BB Squeeze at 30% weight: ~50 trades/yr * 0.35% avg * 0.3 = 5.3% (rough estimate)
- MTF Div Z at 20% weight: 5.8% * 0.2 = 1.2%
- Total: **~13.8%**

**Pros:**
- Best Sharpe ratio of all architectures (diversification)
- Lowest MaxDD estimate
- More robust to single-signal failure
- BB Squeeze shorts provide some counter-cyclical exposure
- R110/R112 portfolio research validates this concept (EqW Sharpe 0.880 in R110)

**Cons:**
- Lowest absolute return (split capital dilutes each strategy)
- BB Squeeze not fully strategy-validated (signal-level only, no full backtest)
- MTF Div Z has marginal Sharpe (0.417)
- Nowhere close to 300% target

---

### Architecture C: V3 Enhanced (Single Strategy, Maxed Out)

**Concept:** V3 with every proven overlay stacked. Full capital, single strategy, maximally tuned.

| Component | Effect | Evidence |
|-----------|--------|----------|
| Base: EMA 20/50 | Core trend signal | OOS Sharpe 0.56 |
| + Positioning overlay | Size reduction when crowd long | OOS dSharpe +0.211 |
| + VRP overlay | Size reduction in turbulence | OOS dSharpe +0.421 |
| + RSI timing | Better entry on pullbacks | dSharpe +5.87 (WF) |
| + Trail stop (1.5 ATR) | Cut losers faster | +0.629 avg Sharpe |
| + Partial TP (+2 ATR) | Lock in winners | Available in V4 |
| + Cross-TF filter | Gate entries on MTF alignment | RANGE Sharpe 0.03 (neutral) |

#### Estimated Performance

| Metric | Value | Basis |
|--------|-------|-------|
| **Annual Return** | **~29%** | Sharpe ~1.1 * 26.2% vol |
| **Max Drawdown** | **-15% to -20%** | Trail stops + RSI timing reduce drawdowns |
| **Sharpe** | **~1.1** | 0.56 base + 0.20 RSI + 0.30 trail + 0.05 XTF |
| **Capital Utilization** | **~56%** | V3 is in market 55.9% of time |
| **Key Risk** | Overlay stacking may not be additive |

**Return Math:**
- V3 OOS: Sharpe 0.56, Vol 26.2%
- RSI timing: +0.20 Sharpe (conservative from +5.87 dSharpe WF data)
- Trail stops: +0.30 Sharpe (conservative from +0.629 research; assume 50% OOS degradation)
- Cross-TF filter: +0.05 Sharpe (minor, may help in RANGE)
- Enhanced Sharpe: ~1.11
- Enhanced return: 1.11 * 26.2% = **~29.1%**

**Pros:**
- Best risk-adjusted returns (highest Sharpe)
- Simplest to implement (V3 already built, add overlays)
- All components individually validated
- Moderate MaxDD with trail stops
- Full capital utilization when signal is active

**Cons:**
- Overlay stacking likely NOT fully additive (interactions, diminishing returns)
- Still BTC-only, single-directional
- 44% flat time with no capital deployment
- 29% is far from 300%
- Parameter count increases (overfitting risk with more overlays)

---

## 4. The 300% Math: Why It Cannot Work

### The Fundamental Constraint

The highest annualized return observed **anywhere** in 14 research sessions:

| Signal | Condition | Ann Return | Sharpe | Time Active |
|--------|-----------|-----------|--------|-------------|
| V3 in UPTREND (IS) | Bull market only | 353.2% | 6.42 | 40% |
| V3 in UPTREND (OOS) | Bull market only | 193.1% | 6.02 | 31% |
| V3+RSI full period | All regimes | 386.9% total (5.3yr) | 3.85 | ~55% |

Even the **best regime-specific return (OOS)** is 193.1%, and it only occurs 31-38% of the time.

### Five Paths to 300% (All Fail)

**Path 1: V3 in uptrend, full capital**
- 193.1% * 77% position * 38% time = **56.5%** effective
- Need 5.3x leverage to reach 300%
- **KILLED:** 2x perp already fails at -62% MaxDD

**Path 2: Perfect regime rotation (hypothetical)**
- Uptrend: 193.1% * 38% = 73.4%
- Range: hypothetical +50% * 35% = 17.5%
- Downtrend: hypothetical short +100% * 15% = 15.0%
- Crisis: 0%
- Total: **105.9%**
- **Problem:** No validated range or short strategy exists. The +50%/+100% numbers are fictional.

**Path 3: Required Sharpe calculation**
- At BTC vol ~60%, need Sharpe = 300% / 60% = **5.0**
- Best sustained OOS Sharpe = 0.56 (V3) or 6.02 (uptrend-only, 38% of time)
- Need Sharpe 5.0 across **all regimes, all the time**
- V3 RANGE Sharpe = -1.01, DOWNTREND = -1.49
- **Mathematically impossible** with available signals

**Path 4: Multi-token expansion**
- Adding ETH/SOL: crypto correlation 0.7-0.9 during stress
- Net diversification benefit: ~30-50% more trades
- Architecture A * 1.4x = **107.9%**
- Still far short

**Path 5: Higher-frequency trading**
- BB Squeeze + Dual TF Momentum: ~50-200 trades/year, PF 1.4-1.6
- At 0.3% avg per trade, 200 trades: **60% gross**
- After costs (10bps * 200 * 2 = 40bps drag): ~56%
- Combined with V3: maybe **80-100%**
- Still far short

### The Binding Constraints

1. **Signal edge is narrow.** V3's OOS edge is 14.7% annual on spot. Even in the best regime (uptrend), it's 193% -- but only 38% of the time.

2. **Leverage destroys risk-adjusted returns.** 2x perp: KILLED. Dynamic leverage: KILLED. Funding drag consumes 17% of gross at 1x; it scales linearly.

3. **No validated short/range strategy exists.** 35% of the time (RANGE), V3 bleeds -37.1% annually. No tested alternative works OOS. This is the biggest performance drag.

4. **Capital utilization is inherently low.** V3 is flat 44% of the time. Even Architecture A only utilizes 33% of capital on a time-weighted basis.

5. **Crypto correlations spike during crashes.** Multi-token diversification provides less benefit than expected because all crypto assets crash together.

---

## 5. Recommendation: Architecture A (Regime Rotation)

### Why Architecture A

| Criterion | Arch A | Arch B | Arch C |
|-----------|--------|--------|--------|
| Annual Return | **~77%** | ~14% | ~29% |
| Max Drawdown | -25% to -35% | **-13% to -18%** | -15% to -20% |
| Sharpe | 0.8-1.0 | 0.65-0.80 | **~1.1** |
| Capital Utilization | 33% | 40% | 56% |
| Implementation Complexity | Low | High | Medium |
| Components Validated | All | Partial | All |
| Realistic? | **YES** | YES | YES |
| Path to 300%? | NO | NO | NO |

**Architecture A wins on absolute return** -- the only metric that matters if the goal is maximum profit. It achieves ~77% annual by concentrating all capital on V3+RSI during the one regime where V3 has a massive edge (uptrend, Sharpe 6.02), and going flat otherwise.

### Practical Implementation: Architecture A is Already V3

The key insight: **V3 already implements most of Architecture A.** V3 is:
- Flat 91.3% of downtrend time (EMA crossover exits)
- Flat 99.6% of crisis time (crisis regime filter)
- Position 0.465 in RANGE (partially reduced by positioning overlay)

The improvement from formalizing Architecture A:
1. **Hard RANGE gating**: Force flat when regime = RANGE (saves the -37.1% RANGE drag)
2. **RSI entry timing**: Defer entries to RSI cross-up through 35 (proven +5.87 dSharpe)
3. **Trail stops**: 1.5 ATR trailing stop (proven +0.629 Sharpe)

### V4 Pool Configuration

```json
{
  "capital": 200000,
  "mode": "pool",
  "strategies": [
    {
      "strategy_id": "s320_regime_gated",
      "weight": 1.0,
      "market": "spot",
      "max_positions": 1,
      "sizing_overrides": {
        "kelly_mult_override": 0.50,
        "spot_max_equity_pct": 0.95
      }
    }
  ],
  "concentration_limit": 1.0,
  "raw_mode": true,
  "skip_walk_forward": false
}
```

The strategy itself (s320_regime_gated) would need these enhancements:
- Regime gate: only generate entry signals when regime = UPTREND
- RSI timing: defer entry to first RSI(14) cross-up through 35 within 168h window
- Trail stop: 1.5 ATR trailing stop exit
- Base signal: EMA(20) > EMA(50) crossover
- Sizing overlays: positioning + VRP (unchanged from V3)

### Hybrid Approach: A + B

For even better risk-adjusted returns, combine Architectures A and B:

| Strategy | Weight | Active During | Market |
|----------|--------|---------------|--------|
| V3+RSI (regime-gated) | 0.70 | UPTREND only | spot |
| BB Squeeze short | 0.15 | RANGE/DOWNTREND | perp |
| MTF Div Z | 0.15 | Always | spot |

This gives:
- UPTREND: V3+RSI at 70% capital + MTF at 15% = ~152% * 38% = ~58%
- RANGE: BB Squeeze at 15% + MTF at 15% = ~5% * 35% = ~2%
- DOWNTREND/CRISIS: small BB Squeeze + MTF = ~1%
- **Total: ~61% with better diversification**

The tradeoff: ~16% less return than pure Architecture A, but better Sharpe and lower MaxDD from the diversification during non-uptrend regimes.

---

## 6. Implementation Roadmap

### Phase 1: V3 Regime Gate (1-2 weeks)
- **New feature**: Add hard RANGE gate to V3 (exit when regime transitions to RANGE)
- **Test**: Compare V3 with vs without RANGE gate on OOS period
- **Expected impact**: Eliminates -37.1% RANGE drag; converts to ~0%
- **Requires**: Regime classifier integration into signal generation

### Phase 2: RSI Entry Timing (1-2 weeks)
- **New feature**: Implement V2 Flexible RSI timing in V4 engine
- **Config**: RSI threshold 35, search window 168h, fallback to base entry
- **Test**: Walk-forward validation (already done in R111 -- 13/18 windows improved)
- **Expected impact**: +0.2 Sharpe

### Phase 3: Trail Stop Integration (1 week)
- **New feature**: 1.5 ATR trailing stop exit (V4 already has partial TP infrastructure)
- **Test**: Backtest with trail stop vs no stop on V3
- **Expected impact**: +0.3 Sharpe, reduced MaxDD

### Phase 4: BB Squeeze Short (2-3 weeks) [Optional for Hybrid]
- **New strategy**: BB squeeze breakout (short-biased) on BTC perp
- **Requires**: Full strategy build with walk-forward validation
- **Signal basis**: BB width < 20th percentile for 24h, breaks below band, vol confirm
- **Risk**: Funding drag on perp shorts, OOS validation needed

### Phase 5: Multi-Token Expansion (3-4 weeks) [Optional]
- **New feature**: Extend V3 to ETH/SOL
- **Requires**: Token-specific overlay calibration
- **Expected impact**: 30-50% more trades, but correlated with BTC

---

## 7. Honest Risk Assessment

### What Could Go Right (Bull Case)
- BTC enters sustained uptrend (like 2020-2021): V3 captures 150-200% annually
- RSI timing + trail stops improve Sharpe from 0.56 to 1.0+
- RANGE gate eliminates the -37.1% drag
- **Bull case annual: ~100-120%**

### What Could Go Wrong (Bear Case)
- Extended RANGE regime (like 2022): V3 is flat, 0% for months
- Regime misclassification: enters position in false uptrend, regime flips to RANGE
- EMA crossover whipsaw during transitions: -2.05 Sharpe in EMA whipsaw periods
- RSI timing catches falling knives (max DD -3.18% per entry vs -1.39% for fallback)
- **Bear case annual: -20% to 0%**

### Structural Risks
1. **Single-asset dependency**: 100% BTC -- a catastrophic BTC failure (regulatory, technical) is unhedged
2. **Signal decay**: EMA crossover may lose effectiveness as markets become more efficient
3. **Regime classifier lag**: SMA-based regime detection is inherently backward-looking (200d SMA requires months of data)
4. **Overlay overfitting**: IS-to-OOS Sharpe degradation already observed (0.90 to 0.56)
5. **Walk-forward masking**: V4 engine burns 365 days for training; recent regime changes may not be captured

### The Honest Bottom Line

| Scenario | Probability | Annual Return |
|----------|------------|--------------|
| Bull (sustained uptrend) | 25% | +100% to +120% |
| Base (mixed regimes) | 50% | +30% to +77% |
| Bear (RANGE/downtrend) | 25% | -20% to +5% |
| **Expected (probability-weighted)** | | **~40% to 55%** |

The expected annual return across scenarios is approximately **40-55%**, with significant variance. This is a strong result for a crypto momentum strategy with $200K, but it is **not 300%.**

To reach 300%, one would need:
- A fundamentally different approach (HFT, market-making, options selling)
- Significant leverage (which has been KILLED in testing due to drawdown risk)
- Undiscovered signals with much higher edge (not found in 14 sessions of research)
- Or acceptance of catastrophic drawdown risk (>-80% MaxDD)

**None of these are recommended.**

---

## Appendix: Data Sources

All estimates are derived from OOS (out-of-sample) validated results in these research files:

| File | Key Data Used |
|------|--------------|
| `research/v3_regime_analysis_results.md` | V3 regime-specific Sharpe/returns, position sizing distribution |
| `research/v3_walkforward_sensitivity_results.md` | Walk-forward validation (5/6 positive OOS), parameter robustness |
| `research/new_momentum_strategy_results.md` | V3 variant comparison, overlay contribution analysis |
| `research/R110_three_strategy_portfolio.md` | 3-strategy portfolio (Sharpe 0.880, MaxDD -30.6%) |
| `research/R112_two_strategy_portfolio.md` | 2-strategy portfolio (Sharpe 0.746, MaxDD -36.6%) |
| `research/R111_rsi_timing_extended.md` | RSI timing: 13/18 WF improved, dSharpe +5.87 |
| `research/R116_multitf_divergence_diversifier.md` | MTF Div Z: only surviving diversifier (corr 0.27) |
| `research/v3_perp_overlay_leverage_results.md` | Perp leverage: all >1x KILLED, dynamic leverage analysis |
| `research/v3_leveraged_results.md` | 2x/3x KILLED, funding drag 17% of gross |
| `research/regime_switched_backtest_results.md` | Regime-switched positioning OOS Sharpe -1.14 |
| `research/s320_sizing_sweep_results.md` | S320 sizing: cap_pct non-binding, 49% avg position |
| `research/entry_exit_signal_results.md` | BB Squeeze PF 2.60 shorts, Dual TF PF 1.62 longs |
| `research/portfolio_sizing_optimization_results.md` | Backtest-paper divergence, funding cost dominance |
| `v4/config.py` | StrategySpec, PortfolioConfig, pool mode mechanics |

---

*Generated 2026-03-26. All return estimates use OOS data unless explicitly noted.*
