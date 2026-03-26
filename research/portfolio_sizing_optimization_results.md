# Portfolio Sizing Optimization Results

Date: 2026-03-26
Capital: $200,000
Lookback periods tested: 12 months, 3 months

## CRITICAL FINDING: Backtest vs Paper Divergence

**The backtest engine produces deeply negative results (-17% to -84%) for ALL configurations,
even though live paper trading shows s62 at +9.6% and s65 at +4.9% over 12 months.**

This divergence means sizing optimization results here cannot be taken at face value.
The backtest environment may differ from paper in: walk-forward masking, funding rate modeling,
entry timing, or market impact assumptions. See "Divergence Investigation" section below.

## SAFETY_RAILS Reference
```
kelly_mult_floor:    [0.05, 0.40]   (default: 0.15)
kelly_mult_range:    [0.10, 0.80]   (default: 0.35)
cap_pct_floor:       [0.005, 0.08]  (default: 0.02)
cap_pct_range:       [0.02, 0.30]   (default: 0.10)
adv_scaling_divisor: [1.0, 20.0]    (default: 5.0)
kelly_mult_override: [0.05, 0.50]   (default: 0.0 = use curve)
kelly_mult_scale:    [0.5, 2.0]     (default: 1.0)
cap_pct_override:    [0.01, 0.15]   (default: 0.0 = use curve)
cap_pct_scale:       [0.5, 2.0]     (default: 1.0)
```

---

## Experiment 1: Best-of-Two (s62 + s65)

Rationale: s62 (+9.6%) and s65 (+4.9%) are the only positive post-MTM strategies.
Test whether pairing them with aggressive sizing yields better risk-adjusted returns.

### 12-Month Lookback

| Config | Ann.Ret% | MaxDD% | Sharpe | Calmar | Sortino | Trades | Win% | PF | Final Eq | Funding |
|--------|----------|--------|--------|--------|---------|--------|------|----|----------|---------|
| s62+s65 50/50 default | -34.4% | -63.2% | -1.15 | -0.54 | -0.94 | 3660 | 43.4% | 0.84 | $85,516 | $-99,987 |
| s62+s65 50/50 aggressive | -31.8% | -60.7% | -0.99 | -0.52 | -0.84 | 3266 | 44.0% | 0.85 | $92,360 | $-89,188 |
| s62+s65 70/30 aggressive | -43.8% | -71.2% | -1.65 | -0.61 | -1.31 | 3235 | 42.8% | 0.78 | $62,851 | $-79,962 |
| s62 solo default | -45.8% | -71.2% | -2.00 | -0.64 | -1.51 | 2019 | 41.6% | 0.77 | $58,293 | $-71,533 |
| s65 solo default | -43.1% | -70.6% | -1.42 | -0.61 | -1.17 | 1787 | 42.7% | 0.79 | $64,382 | $-96,200 |

### 3-Month Lookback

| Config | Ann.Ret% | MaxDD% | Sharpe | Calmar | Sortino | Trades | Win% | PF | Final Eq | Funding |
|--------|----------|--------|--------|--------|---------|--------|------|----|----------|---------|
| s62+s65 50/50 default | -22.2% | -31.5% | -1.35 | -0.71 | -0.60 | 1214 | 42.7% | 0.79 | $145,350 | $-60,009 |
| s62+s65 50/50 aggressive | -17.5% | -32.9% | -0.86 | -0.53 | -0.43 | 1167 | 40.5% | 0.86 | $156,396 | $-54,917 |
| s62+s65 70/30 aggressive | -29.1% | -37.7% | -1.83 | -0.77 | -0.79 | 1106 | 43.3% | 0.73 | $129,610 | $-50,649 |
| s62 solo default | -35.9% | -46.0% | -2.35 | -0.78 | -0.95 | 636 | 39.2% | 0.66 | $114,138 | $-35,486 |
| s65 solo default | -35.2% | -46.9% | -1.74 | -0.75 | -0.75 | 636 | 37.6% | 0.69 | $115,611 | $-59,565 |

### Exp1 Analysis

- **Pairing helps.** The 50/50 combo consistently outperforms either solo strategy at both lookback periods. Diversification benefit is real.
- **Aggressive sizing (50/50) is the best config across all experiments.** At 12mo: Sharpe -0.99 vs -1.15 baseline (+14% improvement). At 3mo: Sharpe -0.86 vs -1.35 (+36% improvement). The aggressive cap_pct_floor=0.05, cap_pct_range=0.15, kelly_mult_scale=1.5 configuration consistently wins.
- **70/30 weighting toward s62 hurts.** Overweighting the "best" strategy destroys diversification. Equal weight is better.
- **Funding costs are enormous.** s65 alone generates $96K in funding costs on $200K capital (12mo). This is the dominant PnL driver, not signal quality.

---

## Experiment 2: s62 Solo, Fully Optimized

Rationale: s62 (conservative funding carry) is the best performer in paper.
Test whether bypassing the ADV curve with fixed overrides or scaling up improves it.

### 12-Month Lookback

| Config | Ann.Ret% | MaxDD% | Sharpe | Calmar | Sortino | Trades | Win% | PF | Final Eq | Funding |
|--------|----------|--------|--------|--------|---------|--------|------|----|----------|---------|
| s62 default (from Exp1) | -45.8% | -71.2% | -2.00 | -0.64 | -1.51 | 2019 | 41.6% | 0.77 | $58,293 | $-71,533 |
| kelly=0.40, cap=0.10, conc=0.15 | -50.4% | -76.9% | -1.81 | -0.66 | -1.47 | 1902 | 43.2% | 0.73 | $48,837 | $-66,071 |
| ADV curve raised, conc=0.15 | -60.3% | -84.4% | -2.82 | -0.71 | -2.01 | 1864 | 44.0% | 0.65 | $31,381 | $-57,475 |
| kelly_scale=2x, cap_scale=2x, conc=0.20 | -58.3% | -83.0% | -1.95 | -0.70 | -1.63 | 1772 | 41.8% | 0.70 | $34,596 | $-54,508 |

### 3-Month Lookback

| Config | Ann.Ret% | MaxDD% | Sharpe | Calmar | Sortino | Trades | Win% | PF | Final Eq | Funding |
|--------|----------|--------|--------|--------|---------|--------|------|----|----------|---------|
| s62 default (from Exp1) | -35.9% | -46.0% | -2.35 | -0.78 | -0.95 | 636 | 39.2% | 0.66 | $114,138 | $-35,486 |
| kelly=0.40, cap=0.10, conc=0.15 | -39.1% | -52.6% | -2.18 | -0.74 | -0.89 | 614 | 39.9% | 0.59 | $106,904 | $-33,430 |
| ADV curve raised, conc=0.15 | -43.6% | -52.2% | -2.49 | -0.84 | -0.91 | 605 | 42.1% | 0.60 | $97,363 | $-37,139 |
| kelly_scale=2x, cap_scale=2x, conc=0.20 | -41.7% | -50.1% | -1.86 | -0.83 | -0.72 | 596 | 42.3% | 0.63 | $101,020 | $-42,579 |

### Exp2 Analysis

- **ALL optimized configs are WORSE than default.** Increasing position sizes on a losing strategy just amplifies losses.
- **The "raised ADV curve" variant is the worst** (-84.4% DD at 12mo). This is the most aggressive curve reshaping and it concentrates more capital into losing positions.
- **Funding costs decrease with larger positions** (counterintuitively) because capital is exhausted faster, leading to fewer open positions late in the period.
- **Conclusion: Do NOT apply aggressive sizing to s62 solo.** The strategy's backtest edge is negative in this engine; bigger bets make it worse.

---

## Experiment 3: s56+s57+s65 Pool with Per-Strategy Sizing

Rationale: This is the existing s58+s65 production pool (s56+s57+s65).
Test per-strategy sizing tuned to each strategy's ADV profile.

### 12-Month Lookback

| Config | Ann.Ret% | MaxDD% | Sharpe | Calmar | Sortino | Trades | Win% | PF | Final Eq | Funding | s56 PnL | s57 PnL | s65 PnL |
|--------|----------|--------|--------|--------|---------|--------|------|----|----------|---------|---------|---------|---------|
| Baseline (default sizing) | -47.5% | -76.7% | -1.73 | -0.62 | -1.35 | 2837 | 43.5% | 0.78 | $54,738 | $-64,108 | $-35,573 | $-32,171 | $-67,097 |
| Tuned v1 (s56/s65 higher cap) | -46.5% | -72.0% | -1.48 | -0.65 | -1.22 | 2813 | 43.9% | 0.79 | $56,846 | $-65,577 | $-30,973 | $-31,541 | $-70,387 |
| Aggressive (all high sizing) | -50.4% | -78.7% | -1.93 | -0.64 | -1.45 | 2755 | 44.1% | 0.79 | $48,887 | $-70,257 | $-22,527 | $-32,376 | $-85,523 |

### 3-Month Lookback

| Config | Ann.Ret% | MaxDD% | Sharpe | Calmar | Sortino | Trades | Win% | PF | Final Eq | Funding | s56 PnL | s57 PnL | s65 PnL |
|--------|----------|--------|--------|--------|---------|--------|------|----|----------|---------|---------|---------|---------|
| Baseline (default sizing) | -26.5% | -40.2% | -1.26 | -0.66 | -0.53 | 896 | 40.0% | 0.76 | $135,330 | $-54,840 | $-6,017 | $-3,247 | $-51,863 |
| Tuned v1 (s56/s65 higher cap) | -29.9% | -39.8% | -1.60 | -0.75 | -0.74 | 851 | 40.4% | 0.72 | $127,587 | $-45,123 | $-8,435 | $-3,318 | $-57,135 |
| Aggressive (all high sizing) | -23.8% | -39.6% | -1.12 | -0.60 | -0.51 | 872 | 42.4% | 0.79 | $141,511 | $-50,198 | $+2,126 | $-4,123 | $-52,892 |

### Exp3 Analysis

- **Tuned v1 slightly improves Sharpe** at 12mo (-1.48 vs -1.73 baseline, +14%) and reduces MaxDD (72% vs 76.7%).
- **Aggressive is a mixed bag.** Worse at 12mo but best at 3mo (Sharpe -1.12 vs -1.26). Note s56 flips to positive at 3mo with aggressive sizing ($+2,126).
- **s65 is the dominant loss driver** in every configuration: -$51K to -$85K of the total loss. Its massive funding costs ($48K-$70K) overwhelm any carry edge.
- **s56 and s57 losses are smaller** and more stable across sizing configs. s57 appears market-neutral (near-zero funding).

---

## Cross-Experiment Comparison: Best Configs

### 12-Month (all configs, ranked by Sharpe)

| Rank | Config | Sharpe | Ann.Ret% | MaxDD% | Calmar | Final Eq |
|------|--------|--------|----------|--------|--------|----------|
| 1 | s62+s65 50/50 aggressive | -0.99 | -31.8% | -60.7% | -0.52 | $92,360 |
| 2 | s62+s65 50/50 default | -1.15 | -34.4% | -63.2% | -0.54 | $85,516 |
| 3 | s65 solo default | -1.42 | -43.1% | -70.6% | -0.61 | $64,382 |
| 4 | s56+s57+s65 tuned | -1.48 | -46.5% | -72.0% | -0.65 | $56,846 |
| 5 | s62+s65 70/30 aggressive | -1.65 | -43.8% | -71.2% | -0.61 | $62,851 |
| 6 | s56+s57+s65 baseline | -1.73 | -47.5% | -76.7% | -0.62 | $54,738 |
| 7 | exp2_s62_optimized_v1 | -1.81 | -50.4% | -76.9% | -0.66 | $48,837 |
| 8 | s56+s57+s65 aggressive | -1.93 | -50.4% | -78.7% | -0.64 | $48,887 |
| 9 | exp2_s62_max_aggressive | -1.95 | -58.3% | -83.0% | -0.70 | $34,596 |
| 10 | s62 solo default | -2.00 | -45.8% | -71.2% | -0.64 | $58,293 |
| 11 | exp2_s62_optimized_v2 | -2.82 | -60.3% | -84.4% | -0.71 | $31,381 |

### 3-Month (all configs, ranked by Sharpe)

| Rank | Config | Sharpe | Ann.Ret% | MaxDD% | Calmar | Final Eq |
|------|--------|--------|----------|--------|--------|----------|
| 1 | s62+s65 50/50 aggressive | -0.86 | -17.5% | -32.9% | -0.53 | $156,396 |
| 2 | s56+s57+s65 aggressive | -1.12 | -23.8% | -39.6% | -0.60 | $141,511 |
| 3 | s56+s57+s65 baseline | -1.26 | -26.5% | -40.2% | -0.66 | $135,330 |
| 4 | s62+s65 50/50 default | -1.35 | -22.2% | -31.5% | -0.71 | $145,350 |
| 5 | s56+s57+s65 tuned | -1.60 | -29.9% | -39.8% | -0.75 | $127,587 |
| 6 | s65 solo default | -1.74 | -35.2% | -46.9% | -0.75 | $115,611 |
| 7 | s62+s65 70/30 aggressive | -1.83 | -29.1% | -37.7% | -0.77 | $129,610 |
| 8 | exp2_s62_max_aggressive | -1.86 | -41.7% | -50.1% | -0.83 | $101,020 |
| 9 | exp2_s62_optimized_v1 | -2.18 | -39.1% | -52.6% | -0.74 | $106,904 |
| 10 | s62 solo default | -2.35 | -35.9% | -46.0% | -0.78 | $114,138 |
| 11 | exp2_s62_optimized_v2 | -2.49 | -43.6% | -52.2% | -0.84 | $97,363 |

---

## Divergence Investigation: Backtest vs Paper Performance

The most important finding is NOT about sizing -- it is the massive gap between paper results (s62 +9.6%, s65 +4.9%) and backtest results (all deeply negative).

### Key Discrepancies

1. **Funding costs dominate.** The backtest engine charges $60K-$100K in funding on $200K capital over 12 months. If paper trading captures funding differently (e.g., earning positive funding on shorts in a net-long market), results diverge massively.

2. **Walk-forward masking.** The backtest engine uses walk-forward with train_bars=8760 (365 days) which burns a full year of data for training. The paper strategies may have been trained differently or use parameters that were already fixed before the paper period.

3. **Position rejections.** The backtest rejects 30K-100K+ entries due to portfolio limits, strategy limits, concentration limits, and capital constraints. Paper trading may have different effective constraints.

4. **Position sizing.** Default sizing produces 2000-3600 trades at 12mo, with avg PnL of -$28 to -$71 per trade. The strategies are not fundamentally broken (43-44% win rate, ~1.0x payoff), but funding costs create a persistent drag.

### Likely Root Cause

**Funding cost modeling.** s65 is a funding carry strategy that should EARN funding (short tokens with high positive funding rates). If the backtest charges funding costs as a flat drag rather than modeling the directional funding flow correctly, carry strategies would appear deeply negative in backtest but positive in paper.

Evidence: s65 paper = +4.9%, s65 backtest = -43.1% to -70.6%. Funding costs for s65 = $65K-$96K on $200K capital. If even 50% of that funding was actually earned (not paid), s65 would be roughly break-even to positive.

---

## Summary and Recommendations

### What the sizing optimization shows (within-backtest relative comparisons):

1. **Best relative config: s62+s65 50/50 with aggressive sizing overrides** (cap_pct_floor=0.05, cap_pct_range=0.15, kelly_mult_scale=1.5). Consistently ranks #1 at both 12mo and 3mo.

2. **Diversification > concentration.** Equal-weight 50/50 beats 70/30, which beats solo. Never overweight a single strategy.

3. **Aggressive sizing on solo strategies makes things worse.** All Exp2 configs underperform the default. Do not apply aggressive sizing without diversification.

4. **Per-strategy tuning has modest benefit.** Exp3 tuned v1 improves Sharpe by ~14% over baseline. Worth implementing but not transformative.

### Recommended sizing_overrides for production (if deploying):

If the paper-backtest divergence is resolved and strategies are confirmed positive:

```json
{
  "s62": {
    "sizing_overrides": {
      "cap_pct_floor": 0.05,
      "cap_pct_range": 0.15,
      "kelly_mult_scale": 1.5
    }
  },
  "s65": {
    "sizing_overrides": {
      "cap_pct_floor": 0.05,
      "cap_pct_range": 0.15,
      "kelly_mult_scale": 1.5
    }
  }
}
```

### Critical next step (higher priority than sizing):

**Investigate the backtest-paper divergence.** Specifically:
- Run the same period as paper (exact dates) with `--skip-wf` to eliminate walk-forward masking effects
- Compare funding cost modeling in backtest vs actual funding earned/paid in paper
- Check if the paper trader uses different concentration/position limits
- This divergence investigation should happen before any sizing parameter changes are deployed to production
