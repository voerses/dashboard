# Cross-Token Positioning Consensus Overlay Results

**Run date:** 2026-03-24 18:06
**IS period:** 2021-06-01 to 2024-12-31
**OOS period:** 2025-01-01 to latest
**Transaction cost:** 10 bps round-trip
**Rebalancing:** Weekly (Monday)

## Question

Does cross-token positioning consensus (mean top trader L/S across 29 tokens)
add information beyond BTC-specific positioning as an overlay on V3 (s320)?

## 1. Signal Correlation (Residual Value Test)

If corr > 0.7, consensus is redundant with BTC-specific positioning.

| Pair | Pearson r | Spearman rho |
|------|-----------|--------------|
| BTC pos z vs Consensus z | 0.336 | 0.340 |
| BTC pos z vs VRP z | 0.005 | 0.031 |
| Consensus z vs VRP z | 0.187 | 0.192 |

**Rolling 90d corr (BTC pos vs Consensus):** mean=0.311, std=0.306, range=[-0.457, 0.854]
**IS corr:** 0.358, **OOS corr:** 0.270

**Verdict:** PARTIALLY INDEPENDENT (r=0.336)

## 2. Backtest Comparison

| Variant | IS Sharpe | OOS Sharpe | IS Return | OOS Return | IS MaxDD | OOS MaxDD | IS Calmar | OOS Calmar |
|---------|-----------|------------|-----------|------------|----------|-----------|-----------|------------|
| V3 Base Only | 0.645 | -0.143 | 17.8% | -2.7% | -45.6% | -19.1% | 0.39 | -0.14 |
| V3 + BTC Pos + VRP (s320) | 0.299 | 0.556 | 6.9% | 10.7% | -45.4% | -11.9% | 0.15 | 0.90 |
| V3 + BTC Pos + VRP + Consensus | 0.019 | 0.207 | 0.3% | 4.2% | -41.2% | -14.5% | 0.01 | 0.29 |
| V3 + Consensus Only | 0.466 | -0.429 | 10.3% | -8.5% | -38.2% | -22.9% | 0.27 | -0.37 |

### Marginal Improvement vs s320 Baseline

| Metric | +Consensus (ADD) | Consensus Only (REPLACE) |
|--------|------------------|--------------------------|
| IS dSharpe | -0.280 | +0.167 |
| IS dReturn | -6.6% | +3.3% |
| IS dMaxDD | +4.2% | +7.2% |
| OOS dSharpe | -0.349 | -0.985 |
| OOS dReturn | -6.6% | -19.3% |
| OOS dMaxDD | -2.6% | -11.0% |

## 3. Walk-Forward Results

**Windows:** 6
**Win rate (consensus improves Sharpe over s320):** 3/6 (50%)
**MaxDD worsened:** 4/6 windows

| Window | Period | s320 Sharpe | +Consensus Sharpe | Delta | s320 MaxDD | +Cons MaxDD | DD Delta |
|--------|--------|-------------|-------------------|-------|------------|-------------|----------|
| W1 | 2021-01-01 to 2021-11-11 | 1.858 | 1.061 | -0.797 | -30.8% | -37.1% | -6.3% |
| W2 | 2021-11-12 to 2022-09-22 | -1.118 | -1.090 | +0.028 | -16.4% | -20.1% | -3.7% |
| W3 | 2022-09-23 to 2023-08-03 | 0.025 | 0.801 | +0.776 | -18.5% | -9.3% | +9.1% |
| W4 | 2023-08-04 to 2024-06-13 | 1.389 | -0.059 | -1.448 | -16.7% | -19.3% | -2.6% |
| W5 | 2024-06-14 to 2025-04-24 | -0.744 | -0.457 | +0.287 | -34.3% | -27.0% | +7.3% |
| W6 | 2025-04-25 to 2026-03-05 | 1.247 | 0.777 | -0.471 | -11.2% | -14.5% | -3.2% |

## 4. Incremental IC Analysis

Residual IC = IC of consensus z-score after partialing out BTC positioning z-score.
If residual IC is near zero, consensus adds no information beyond BTC-specific positioning.

| Horizon | BTC Pos IC | Consensus IC | Residual IC | IS Resid IC | OOS Resid IC |
|---------|-----------|-------------|-------------|-------------|--------------|
| 7d | -0.0311 | 0.0202 | 0.0473 | 0.0207 | 0.0954 |
| 14d | -0.0324 | 0.0036 | 0.0256 | -0.0091 | 0.1080 |

## 5. Multiplier Agreement/Disagreement

- Exact agreement: 43.5%
- Both reduce (<1x): 13.4%
- Both boost (>1x): 13.3%
- Disagree: 9.6%

When BTC pos REDUCES but consensus BOOSTS (96 days): avg 14d return = 2.05%
When BTC pos BOOSTS but consensus REDUCES (119 days): avg 14d return = 1.50%

## 6. Final Verdict

### Decision Criteria

| Criterion | Result |
|-----------|--------|
| Correlation < 0.7 (not redundant) | PASS |
| OOS Sharpe improves | FAIL |
| Walk-forward majority wins (>=4/6) | FAIL |
| MaxDD not worsened in any window | FAIL |
| IS/OOS directional consistency | PASS |
| **Total** | **2/5** |

### Verdict: **KILL**

**Reasoning:** Consensus does not improve s320 OOS (delta=-0.349). Walk-forward: 3/6 wins. Not enough evidence to add complexity.

### Key Numbers

- BTC pos z / Consensus z correlation: 0.336
- s320 OOS Sharpe: 0.556
- +Consensus OOS Sharpe: 0.207
- Consensus-only OOS Sharpe: -0.429
- Walk-forward win rate: 3/6 (50%)
- MaxDD worsened: 4/6 windows
