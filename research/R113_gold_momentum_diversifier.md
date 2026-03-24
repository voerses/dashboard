# R113: Gold Momentum as BTC Portfolio Diversifier

**Date**: 2026-03-24
**Verdict**: **KILL**
**Reason**: Survivors show degraded recent performance or parameter instability

## Hypothesis

Gold momentum divergence from BTC can predict BTC returns because:
1. Gold rallies during risk-off -> BTC should weaken
2. Gold weakness during risk-on -> BTC should strengthen
3. Gold/BTC correlation regime shifts are informative

## Data Summary

- **BTC**: 2020-01-01 to 2026-03-14, 2258 trading days
- **Gold**: Aligned to BTC daily via forward-fill (weekends/holidays)
- **Overlap period**: 2258 days
- **Signal candidates tested**: 43 variants across 6 categories
- **Fees**: 10bps round-trip per rebalance
- **Walk-forward**: 10 windows, 180d train / 90d test, anchored from end

## 1. Information Coefficient Analysis

### Top 20 Signals by Max Absolute IC

| Signal | IC 1d | IC 3d | IC 7d | IC 14d | Max |IC| | Mean |IC| | Sign Cons. |
|--------|------:|------:|------:|-------:|--------:|----------:|----------:|
| gold_mom_60d_z | -0.0226 | -0.0367 | -0.0695 | -0.1322 | 0.1322 | 0.0653 | 100% |
| gold_btc_ratio_mom_40d_z | -0.0359 | -0.0646 | -0.0989 | -0.1307 | 0.1307 | 0.0825 | 100% |
| rel_mom_40d_z | -0.0372 | -0.0669 | -0.0999 | -0.1277 | 0.1277 | 0.0829 | 100% |
| gold_mom_60d | -0.0224 | -0.0513 | -0.0779 | -0.1201 | 0.1201 | 0.0679 | 100% |
| rel_mom_60d_z | -0.0319 | -0.0475 | -0.0708 | -0.1163 | 0.1163 | 0.0666 | 100% |
| rel_mom_20d_z | -0.0068 | -0.0033 | -0.0383 | -0.1133 | 0.1133 | 0.0405 | 100% |
| gold_btc_ratio_mom_20d_z | -0.0067 | -0.0032 | -0.0376 | -0.1131 | 0.1131 | 0.0402 | 100% |
| rel_mom_20d | -0.0093 | -0.0093 | -0.0436 | -0.1116 | 0.1116 | 0.0434 | 100% |
| gold_mom_40d | -0.0301 | -0.0490 | -0.0680 | -0.1111 | 0.1111 | 0.0646 | 100% |
| gold_btc_ratio_mom_20d | -0.0092 | -0.0092 | -0.0431 | -0.1108 | 0.1108 | 0.0431 | 100% |
| gold_mom_40d_z | -0.0212 | -0.0313 | -0.0527 | -0.1010 | 0.1010 | 0.0516 | 100% |
| gold_vol_20d_z | -0.0353 | -0.0502 | -0.0468 | -0.0839 | 0.0839 | 0.0540 | 100% |
| corr_change_20v60 | -0.0156 | -0.0381 | -0.0685 | -0.0815 | 0.0815 | 0.0509 | 100% |
| rel_mom_40d | -0.0404 | -0.0665 | -0.0761 | -0.0796 | 0.0796 | 0.0657 | 100% |
| gold_btc_ratio_mom_40d | -0.0398 | -0.0658 | -0.0758 | -0.0793 | 0.0793 | 0.0652 | 100% |
| rel_mom_60d | -0.0375 | -0.0459 | -0.0462 | -0.0655 | 0.0655 | 0.0487 | 100% |
| gold_vol_60d | 0.0091 | 0.0254 | 0.0438 | 0.0651 | 0.0651 | 0.0359 | 100% |
| corr_level_60d | 0.0097 | 0.0336 | 0.0480 | 0.0651 | 0.0651 | 0.0391 | 100% |
| gold_btc_vol_ratio_20d | -0.0267 | -0.0470 | -0.0489 | -0.0642 | 0.0642 | 0.0467 | 100% |
| corr_change_30v90 | -0.0169 | -0.0310 | -0.0341 | -0.0633 | 0.0633 | 0.0363 | 100% |

### IC by Signal Category

| Category | # Signals | Best Max |IC| | Best Signal |
|----------|----------:|----------:|------------|
| Gold Momentum | 8 | 0.1322 | gold_mom_60d_z |
| Corr Change | 6 | 0.0815 | corr_change_20v60 |
| Relative Momentum | 8 | 0.1277 | rel_mom_40d_z |
| Convergence/Divergence | 6 | 0.0499 | convergence_10d |
| Gold Volatility | 9 | 0.0839 | gold_vol_20d_z |
| Gold/BTC Ratio | 6 | 0.1307 | gold_btc_ratio_mom_40d_z |

### IC Kill Check (threshold: 0.02)
- Signals tested: 43
- Signals surviving IC >= 0.02: 36

### IC Stability (First Half vs Second Half)

Critical test: does the IC persist or decay in the second half of the sample?

| Signal | H1 IC(7d) | H2 IC(7d) | Same Sign | IC Decay |
|--------|----------:|----------:|:---------:|---------:|
| gold_mom_60d_z | -0.1207 | -0.0232 | Yes | -0.0974 |
| gold_btc_ratio_mom_40d_z | -0.1294 | -0.0686 | Yes | -0.0608 |
| rel_mom_40d_z | -0.1280 | -0.0729 | Yes | -0.0551 |
| gold_mom_60d | -0.0650 | -0.0878 | Yes | +0.0228 |
| rel_mom_60d_z | -0.1280 | -0.0190 | Yes | -0.1090 |
| rel_mom_20d_z | -0.0528 | -0.0271 | Yes | -0.0256 |
| gold_btc_ratio_mom_20d_z | -0.0504 | -0.0273 | Yes | -0.0231 |
| rel_mom_20d | -0.0762 | 0.0027 | NO | -0.0735 |
| gold_mom_40d | -0.0872 | -0.0511 | Yes | -0.0362 |
| gold_btc_ratio_mom_20d | -0.0756 | 0.0041 | NO | -0.0715 |

## 2. Walk-Forward Validation (Anchored from End)

Configuration: up to 10 windows, 180d train / 90d test

### Summary

| Signal | Win | Pos | Mean Sharpe | Med Sharpe | Min | Max | Mean Ret | Recent 3w | Status |
|--------|----:|----:|------------:|-----------:|----:|----:|---------:|----------:|--------|
| rel_mom_60d_z | 10 | 7/10 | 0.784 | 0.474 | -1.045 | 2.662 | 5.3% | 0.025 | PASS |
| gold_btc_ratio_mom_40d_z | 10 | 5/10 | 0.713 | 0.475 | -1.299 | 3.908 | 5.3% | -0.282 | PASS |
| rel_mom_40d_z | 10 | 6/10 | 0.692 | 0.395 | -0.953 | 3.327 | 3.5% | 0.111 | PASS |
| gold_mom_60d_z | 10 | 5/10 | -0.234 | -0.061 | -3.130 | 2.095 | -2.8% | -0.472 | KILL |
| gold_mom_60d | 10 | 5/10 | -0.387 | -0.255 | -4.656 | 2.146 | -1.1% | -0.582 | KILL |
| rel_mom_20d | 10 | 5/10 | -0.408 | -0.231 | -3.856 | 2.882 | -3.0% | 0.360 | KILL |
| gold_btc_ratio_mom_20d | 10 | 5/10 | -0.478 | -0.267 | -3.856 | 2.882 | -3.2% | 0.127 | KILL |
| rel_mom_20d_z | 10 | 3/10 | -0.699 | -0.914 | -2.404 | 2.143 | -7.4% | -0.850 | KILL |
| gold_mom_40d | 10 | 3/10 | -0.722 | -0.881 | -2.621 | 1.503 | -4.7% | -0.615 | KILL |
| gold_btc_ratio_mom_20d_z | 10 | 2/10 | -1.004 | -0.770 | -3.030 | 0.831 | -9.4% | -0.726 | KILL |

### Window Details: rel_mom_60d_z

| Win | Train Period | Test Period | Best Params | Train Sharpe | OOS Sharpe | OOS Return | OOS MaxDD |
|----:|-------------|-------------|-------------|-------------:|-----------:|-----------:|----------:|
| 1 | 2023-03-24 to 2023-09-19 | 2023-09-20 to 2023-12-18 | {'mode': 'tercile', 'threshold': 0. | 0.904 | 1.835 | 6.7% | -3.9% |
| 2 | 2023-06-22 to 2023-12-18 | 2023-12-19 to 2024-03-17 | {'mode': 'tercile', 'threshold': 0. | 1.916 | 2.662 | 25.8% | -10.6% |
| 3 | 2023-09-20 to 2024-03-17 | 2024-03-18 to 2024-06-15 | {'mode': 'long_flat', 'threshold':  | 3.057 | 0.428 | 3.4% | -8.4% |
| 4 | 2023-12-19 to 2024-06-15 | 2024-06-16 to 2024-09-13 | {'mode': 'long_flat', 'threshold':  | 1.303 | 0.761 | 6.0% | -20.9% |
| 5 | 2024-03-18 to 2024-09-13 | 2024-09-14 to 2024-12-12 | {'mode': 'tercile', 'threshold': 0. | 0.965 | -1.045 | -5.4% | -12.1% |
| 6 | 2024-06-16 to 2024-12-12 | 2024-12-13 to 2025-03-12 | {'mode': 'tercile', 'threshold': 0. | 1.905 | 0.521 | 3.0% | -16.1% |
| 7 | 2024-09-14 to 2025-03-12 | 2025-03-13 to 2025-06-10 | {'mode': 'long_flat', 'threshold':  | 2.271 | 2.600 | 16.4% | -9.1% |
| 8 | 2024-12-13 to 2025-06-10 | 2025-06-11 to 2025-09-08 | {'mode': 'long_flat', 'threshold':  | 0.717 | 0.137 | 1.6% | -11.3% |
| 9 | 2025-03-13 to 2025-09-08 | 2025-09-09 to 2025-12-07 | {'mode': 'long_flat', 'threshold':  | 1.340 | 0.000 | 0.0% | 0.0% |
| 10 | 2025-06-11 to 2025-12-07 | 2025-12-08 to 2026-03-14 | {'mode': 'long_short', 'threshold': | 1.021 | -0.062 | -4.3% | -16.2% |

### Window Details: gold_btc_ratio_mom_40d_z

| Win | Train Period | Test Period | Best Params | Train Sharpe | OOS Sharpe | OOS Return | OOS MaxDD |
|----:|-------------|-------------|-------------|-------------:|-----------:|-----------:|----------:|
| 1 | 2023-03-24 to 2023-09-19 | 2023-09-20 to 2023-12-18 | {'mode': 'tercile', 'threshold': 0. | 0.789 | 2.665 | 12.2% | -4.0% |
| 2 | 2023-06-22 to 2023-12-18 | 2023-12-19 to 2024-03-17 | {'mode': 'long_short', 'threshold': | 3.089 | 1.495 | 19.0% | -23.6% |
| 3 | 2023-09-20 to 2024-03-17 | 2024-03-18 to 2024-06-15 | {'mode': 'long_flat', 'threshold':  | 3.795 | -1.119 | -9.6% | -15.4% |
| 4 | 2023-12-19 to 2024-06-15 | 2024-06-16 to 2024-09-13 | {'mode': 'tercile', 'threshold': 0. | 1.414 | 0.950 | 5.3% | -6.5% |
| 5 | 2024-03-18 to 2024-09-13 | 2024-09-14 to 2024-12-12 | {'mode': 'tercile', 'threshold': 0. | 1.996 | -1.299 | -7.5% | -12.1% |
| 6 | 2024-06-16 to 2024-12-12 | 2024-12-13 to 2025-03-12 | {'mode': 'tercile', 'threshold': 0. | 2.815 | 1.375 | 11.7% | -11.5% |
| 7 | 2024-09-14 to 2025-03-12 | 2025-03-13 to 2025-06-10 | {'mode': 'long_flat', 'threshold':  | 2.654 | 3.908 | 30.4% | -9.1% |
| 8 | 2024-12-13 to 2025-06-10 | 2025-06-11 to 2025-09-08 | {'mode': 'long_flat', 'threshold':  | 2.263 | -0.698 | -2.6% | -6.8% |
| 9 | 2025-03-13 to 2025-09-08 | 2025-09-09 to 2025-12-07 | {'mode': 'long_flat', 'threshold':  | 1.923 | 0.000 | 0.0% | 0.0% |
| 10 | 2025-06-11 to 2025-12-07 | 2025-12-08 to 2026-03-14 | {'mode': 'long_short', 'threshold': | 0.521 | -0.148 | -5.4% | -18.0% |

### Window Details: rel_mom_40d_z

| Win | Train Period | Test Period | Best Params | Train Sharpe | OOS Sharpe | OOS Return | OOS MaxDD |
|----:|-------------|-------------|-------------|-------------:|-----------:|-----------:|----------:|
| 1 | 2023-03-24 to 2023-09-19 | 2023-09-20 to 2023-12-18 | {'mode': 'tercile', 'threshold': 0. | 0.758 | 3.327 | 17.8% | -4.0% |
| 2 | 2023-06-22 to 2023-12-18 | 2023-12-19 to 2024-03-17 | {'mode': 'long_short', 'threshold': | 3.089 | 0.528 | 4.5% | -30.9% |
| 3 | 2023-09-20 to 2024-03-17 | 2024-03-18 to 2024-06-15 | {'mode': 'long_flat', 'threshold':  | 3.546 | -0.953 | -8.1% | -13.9% |
| 4 | 2023-12-19 to 2024-06-15 | 2024-06-16 to 2024-09-13 | {'mode': 'long_flat', 'threshold':  | 1.527 | 0.263 | -0.4% | -16.2% |
| 5 | 2024-03-18 to 2024-09-13 | 2024-09-14 to 2024-12-12 | {'mode': 'tercile', 'threshold': 0. | 2.152 | -0.864 | -4.8% | -12.1% |
| 6 | 2024-06-16 to 2024-12-12 | 2024-12-13 to 2025-03-12 | {'mode': 'tercile', 'threshold': 0. | 2.692 | 1.375 | 11.7% | -11.5% |
| 7 | 2024-09-14 to 2025-03-12 | 2025-03-13 to 2025-06-10 | {'mode': 'long_flat', 'threshold':  | 2.552 | 2.909 | 18.6% | -9.1% |
| 8 | 2024-12-13 to 2025-06-10 | 2025-06-11 to 2025-09-08 | {'mode': 'tercile', 'threshold': 0. | 1.751 | 0.589 | 2.2% | -3.9% |
| 9 | 2025-03-13 to 2025-09-08 | 2025-09-09 to 2025-12-07 | {'mode': 'long_flat', 'threshold':  | 1.220 | 0.000 | 0.0% | 0.0% |
| 10 | 2025-06-11 to 2025-12-07 | 2025-12-08 to 2026-03-14 | {'mode': 'long_short', 'threshold': | 0.470 | -0.257 | -6.8% | -20.2% |

## 3. V3 Correlation Check

| Signal | Corr with V3 | Status |
|--------|-------------:|--------|
| rel_mom_40d_z_inv | 0.1250 | PASS |
| rel_mom_40d_z | -0.1253 | PASS |
| gold_btc_ratio_mom_40d_z_inv | 0.1294 | PASS |
| gold_btc_ratio_mom_40d_z | -0.1295 | PASS |
| rel_mom_60d_z | -0.1806 | PASS |
| rel_mom_60d_z_inv | 0.1811 | PASS |

Kill threshold: |corr| > 0.5

## 4. Stationarity Check (Rolling IC, 120d window, 7d horizon)

| Signal | Full IC Mean | Full IC Std | Last 2y IC | Last 2y Pos% | Sign Changed |
|--------|------------:|-----------:|----------:|-----------:|:------------:|
| gold_btc_ratio_mom_40d_z | 0.0127 | 0.1925 | 0.0429 | 50.5% | Yes |
| rel_mom_40d_z | 0.0188 | 0.1941 | 0.0435 | 49.8% | Yes |
| rel_mom_60d_z | 0.0694 | 0.2352 | 0.0778 | 66.5% | Yes |

## 5. Parameter Stability

| Signal | Dominant Mode | Mode Consistency | Stable |
|--------|:-------------|----------------:|:------:|
| gold_btc_ratio_mom_40d_z | tercile | 40% | No |
| rel_mom_40d_z | tercile | 40% | No |
| rel_mom_60d_z | long_flat | 50% | No |

## 6. Regime Analysis

How does each signal perform during BTC UPTREND, DOWNTREND, and RANGE?
(RANGE is where V3 struggles, so diversifiers should add value there.)

### gold_btc_ratio_mom_40d_z

| Regime | Days | % Total | Sharpe | Return |
|--------|-----:|--------:|-------:|-------:|
| UPTREND | 1077 | 48% | -0.648 | -69.8% |
| DOWNTREND | 691 | 31% | -0.540 | -47.2% |
| RANGE | 490 | 22% | 1.551 | 96.5% |

### rel_mom_40d_z

| Regime | Days | % Total | Sharpe | Return |
|--------|-----:|--------:|-------:|-------:|
| UPTREND | 1077 | 48% | -0.661 | -70.4% |
| DOWNTREND | 691 | 31% | -1.042 | -67.2% |
| RANGE | 490 | 22% | 1.457 | 87.4% |

### rel_mom_60d_z

| Regime | Days | % Total | Sharpe | Return |
|--------|-----:|--------:|-------:|-------:|
| UPTREND | 1077 | 48% | -0.892 | -74.2% |
| DOWNTREND | 691 | 31% | -0.095 | -26.9% |
| RANGE | 490 | 22% | 0.940 | 41.8% |

## Kill Criteria Summary

| Criterion | Threshold | Result | Status |
|-----------|-----------|--------|--------|
| K1: IC >= 0.02 | 0.02 | Best max IC = 0.1322 | PASS |
| K2: V3 corr < 0.5 | 0.5 | Min |corr| = 0.1250 | PASS |
| K3: WF mean Sharpe > 0.3 | 0.3 | Best = 0.784 | PASS |
| K4: >= 5/10 WF positive | 5/10 | Best = 7/10 | PASS |

## Final Verdict

**KILL**: Survivors show degraded recent performance or parameter instability

### Surviving Signals

- **gold_btc_ratio_mom_40d_z**: Mean OOS Sharpe=0.713, Positive=5/10, V3 corr=-0.1295, Recent 3w mean=-0.282
- **rel_mom_40d_z**: Mean OOS Sharpe=0.692, Positive=6/10, V3 corr=-0.1253, Recent 3w mean=0.111
- **rel_mom_60d_z**: Mean OOS Sharpe=0.784, Positive=7/10, V3 corr=-0.1806, Recent 3w mean=0.025

## Detailed Analysis

### Key Observations

1. **Direction**: 15/15 top signals have negative IC (gold strength -> BTC weakness). This confirms the risk-off thesis: when gold is rallying relative to BTC, BTC forward returns tend to be negative.

2. **Horizon Effect**: IC magnitude increases monotonically from 1d to 14d across all top signals. The gold-BTC relationship is slow-moving -- it predicts multi-week returns better than daily returns. This is consistent with a macro regime signal.

3. **Walk-Forward**: 3/10 signals pass both WF criteria. The anchored-from-end design ensures the most recent market regime (2024-2026) is tested in every signal's last 3 windows.

4. **Stationarity WARNING**: 3/3 signals show IC sign changes in the last 2 years. Rolling IC hovers around zero with ~50% positive -- this means the signal is noisy and the predictive relationship is weak/intermittent.

5. **Regime Performance**: 
   - gold_btc_ratio_mom_40d_z: RANGE Sharpe = 1.551 (positive -- diversifies V3)
   - rel_mom_40d_z: RANGE Sharpe = 1.457 (positive -- diversifies V3)
   - rel_mom_60d_z: RANGE Sharpe = 0.940 (positive -- diversifies V3)

## Recommendations

### Caution: Surface-Level Pass Masks Fragility

While some signals technically pass all kill criteria, several red flags warrant caution:

- **All signals show IC sign changes in the last 2 years.** The gold->BTC predictive relationship is intermittent, not persistent.
- **Parameter instability** in 3 signal(s): optimal mode (long/short vs tercile) shifts across windows.
- **High OOS Sharpe variance**: Min/Max OOS Sharpe ranges are wide, indicating inconsistent performance across market regimes.

### Next Steps (if proceeding)

- Paper trade the best signal for 90 days before any live allocation
- Cap allocation at 10% of portfolio until 6+ months of live data confirm edge
- Set kill switch: if rolling 90d Sharpe < -0.5, halt the signal
- Recheck IC monthly for sign stability
