# Mission M Gate 0 — Diagnostic Report

Generated: 2026-04-08

**Verdict: KILL**

- C1 fail: pearson_r=-0.012, t=-0.29
- C2 fail at +3d: bucket D wr 0.17 vs baseline_uw 0.29 (Δ=12.8pp), pnl ratio=0.02x
- C2 fail at +5d: bucket D wr 0.17 vs baseline_uw 0.22 (Δ=4.6pp), pnl ratio=0.38x
- C2 fail at +7d: bucket D wr 0.13 vs baseline_uw 0.18 (Δ=4.9pp), pnl ratio=1.62x
- C3 fail: flip@+3d → loser rate 47.83% (n_flipped=69)

## Trade log summary

- Source: `results/v4/s523c_growth_12mo_50k_trades.json` (clean 12-month backtest)
- Total trades: 634
- Enriched (with valid signals + OHLCV): 634
- Skipped: 0 {}
- Overall win rate: 43.53%
- Overall mean pnl_pct: +0.118
- Overall median pnl_pct: -0.024

## Primary correlations

| Metric | N | Pearson r | p | t-stat |
|---|---:|---:|---:|---:|
| strength_+3d vs eventual_pnl_pct | 583 | -0.012 | 0.7740 | -0.29 |
| strength_+5d vs eventual_pnl_pct | 551 | -0.026 | 0.5425 | -0.61 |
| strength_+7d vs eventual_pnl_pct | 501 | -0.004 | 0.9290 | -0.09 |
| strength_change_+3d vs eventual_pnl_pct | 583 | -0.037 | 0.3720 | -0.89 |
| strength_change_+5d vs eventual_pnl_pct | 551 | -0.055 | 0.1968 | -1.29 |
| strength_change_+7d vs eventual_pnl_pct | 501 | -0.037 | 0.4142 | -0.82 |

## Flip predictiveness (binary)

| Window | N flipped | Loser rate if flipped | N intact | Loser rate if intact | chi2 p |
|---|---:|---:|---:|---:|---:|
| flip_+3d → eventual_loser | 69 | 47.83% | 514 | 56.23% | 0.2346 |
| flip_+5d → eventual_loser | 97 | 53.61% | 454 | 54.19% | 1.0000 |
| flip_+7d → eventual_loser | 125 | 51.20% | 376 | 52.66% | 0.8574 |

## 4-bucket conditional analysis (Interpretation B core)


### At +3d post-entry
| Bucket | N | Win rate | Mean pnl_pct | Median pnl_pct |
|---|---:|---:|---:|---:|
| A_strong_winning | 193 | 55.96% | +0.350 | +0.025 |
| B_strong_losing | 204 | 29.90% | -0.097 | -0.092 |
| C_degraded_winning | 51 | 64.71% | +0.335 | +0.282 |
| D_degraded_losing | 18 | 16.67% | -0.002 | -0.259 |
| E_neutral | 117 | 47.86% | +0.187 | -0.007 |

*Baseline (all currently-underwater at +3d):* N=278, win rate=29.50%, mean pnl=-0.079

### At +5d post-entry
| Bucket | N | Win rate | Mean pnl_pct | Median pnl_pct |
|---|---:|---:|---:|---:|
| A_strong_winning | 155 | 66.45% | +0.474 | +0.176 |
| B_strong_losing | 165 | 21.21% | -0.187 | -0.171 |
| C_degraded_winning | 62 | 62.90% | +0.513 | +0.369 |
| D_degraded_losing | 35 | 17.14% | -0.063 | -0.278 |
| E_neutral | 134 | 52.24% | +0.134 | +0.004 |

*Baseline (all currently-underwater at +5d):* N=253, win rate=21.74%, mean pnl=-0.165

### At +7d post-entry
| Bucket | N | Win rate | Mean pnl_pct | Median pnl_pct |
|---|---:|---:|---:|---:|
| A_strong_winning | 127 | 70.87% | +0.534 | +0.274 |
| B_strong_losing | 124 | 16.94% | -0.178 | -0.222 |
| C_degraded_winning | 87 | 64.37% | +0.465 | +0.355 |
| D_degraded_losing | 38 | 13.16% | -0.317 | -0.297 |
| E_neutral | 125 | 53.60% | +0.159 | +0.004 |

*Baseline (all currently-underwater at +7d):* N=211, win rate=18.01%, mean pnl=-0.195

## Diagnosis

Signal degradation does NOT systematically predict eventual losers beyond what current P&L alone reveals. The s523c composite either mean-reverts on the same timescale as the strategy hold (so by the time we'd act, the signal has already moved through several states) or the entry-time z-score doesn't carry trade-level forward information. Either way, no reasonable Mission M rule will produce edge.