# R109: Deep Walk-Forward Validation of Macro Regime Rotation on BTC

**Date**: 2026-03-24
**Status**: FAIL
**Parent**: R107 (Uncorrelated Signal Discovery)
**Signal**: US10Y + DXY 20d rate-of-change regime rotation

## Signal Description

Macro regime rotation uses the 20-day rate-of-change of the US 10-Year Treasury Yield and the US Dollar Index (DXY). When both are falling simultaneously, this indicates a "risk-on" macro environment favorable for BTC (long). When both are rising, it signals "risk-off" (flat or short). Mixed signals (one up, one down) result in a flat/neutral position.

- **Rebalance**: Weekly (every 7 days)
- **Cost assumption**: 10 bps per trade
- **Correlation with V3 momentum**: ~0.010 (uncorrelated, per R107)

---

## 1. Walk-Forward Validation: Long-Only (10 Windows)

Train: 12 months | Test: 6 months | Start: 2021-07-01
Parameters optimized: ROC lookback (10/15/20/30/40d), signal smoothing (none/5d/10d SMA)

| Window | Test Period | Return | Sharpe | MaxDD | Trades | Win Rate | Best ROC | Best Smooth |
|--------|------------|--------|--------|-------|--------|----------|----------|-------------|
| 1 | 2022-07-01 to 2023-01-01 | -11.4% | -1.11 | -17.3% | 4 | 25% | 30d | 0d |
| 2 | 2023-01-01 to 2023-07-01 | 25.2% | 2.33 | -12.1% | 4 | 50% | 30d | 0d |
| 3 | 2023-07-01 to 2024-01-01 | 7.8% | 0.80 | -10.2% | 5 | 20% | 10d | 5d |
| 4 | 2024-01-01 to 2024-07-01 | -14.2% | -0.96 | -17.9% | 5 | 0% | 15d | 0d |
| 5 | 2024-07-01 to 2025-01-01 | 10.9% | 0.93 | -15.8% | 6 | 50% | 10d | 0d |
| 6 | 2025-01-01 to 2025-07-01 | -14.4% | -0.96 | -21.0% | 9 | 33% | 10d | 0d |
| 7 | 2025-07-01 to 2026-01-01 | -2.0% | -0.22 | -16.1% | 4 | 25% | 40d | 5d |

**Summary (Long-Only)**:
- Positive windows: **3/7**
- Mean OOS Sharpe: **0.118**
- Median OOS Sharpe: **-0.216**
- Mean OOS Return: **0.3%**
- Mean OOS MaxDD: **-15.8%**
- Kill criteria (<5/10 positive OR mean Sharpe <0.2): **TRIGGERED**

---

## 2. Walk-Forward Validation: Bidirectional (10 Windows)

| Window | Test Period | Return | Sharpe | MaxDD | Trades | Win Rate | Best ROC | Best Smooth |
|--------|------------|--------|--------|-------|--------|----------|----------|-------------|
| 1 | 2022-07-01 to 2023-01-01 | -8.7% | -0.40 | -27.9% | 4 | 75% | 30d | 10d |
| 2 | 2023-01-01 to 2023-07-01 | -1.1% | -0.05 | -34.1% | 6 | 50% | 30d | 0d |
| 3 | 2023-07-01 to 2024-01-01 | -4.5% | -0.31 | -21.9% | 10 | 30% | 10d | 5d |
| 4 | 2024-01-01 to 2024-07-01 | -27.8% | -0.98 | -47.1% | 10 | 40% | 10d | 5d |
| 5 | 2024-07-01 to 2025-01-01 | -20.1% | -0.81 | -45.6% | 7 | 57% | 10d | 0d |
| 6 | 2025-01-01 to 2025-07-01 | -23.6% | -1.23 | -28.1% | 11 | 27% | 10d | 0d |
| 7 | 2025-07-01 to 2026-01-01 | -10.9% | -0.87 | -19.3% | 6 | 33% | 40d | 10d |

**Summary (Bidirectional)**:
- Positive windows: **0/7**
- Mean OOS Sharpe: **-0.665**
- Median OOS Sharpe: **-0.812**
- Mean OOS Return: **-13.8%**
- Mean OOS MaxDD: **-32.0%**

---

## 3. Long-Only vs Bidirectional Comparison

| Metric | Long-Only | Bidirectional |
|--------|-----------|---------------|
| Mean OOS Sharpe | 0.118 | -0.665 |
| Median OOS Sharpe | -0.216 | -0.812 |
| Mean OOS Return | 0.3% | -13.8% |
| Mean OOS MaxDD | -15.8% | -32.0% |
| Positive Windows | 3/7 | 0/7 |

**Full-Sample Backtest (2021-07-01 onward, default params ROC=20, smooth=none)**:

| Metric | Long-Only | Bidirectional |
|--------|-----------|---------------|
| Total Return | 19.0% | -78.0% |
| Annualized Return | 5.6% | -37.6% |
| Sharpe | 0.177 | -0.718 |
| MaxDD | -38.7% | -90.6% |
| Volatility | 31.5% | 52.3% |
| Trades | 38 | 66 |
| Win Rate | 42% | 45% |

---

## 4. Signal Lag Analysis

Tests robustness of the signal when macro data is delayed by 0-3 days (realistic: macro data often available with a 1-day lag).

| Lag (days) | Mean Sharpe | Mean Return | Mean MaxDD | Positive Windows |
|------------|-------------|-------------|------------|------------------|
| 0 | 0.118 | 0.3% | -15.8% | 3/7 |
| 1 | 0.082 | -0.8% | -15.6% | 2/7 |
| 2 | 0.507 | 3.7% | -17.4% | 4/7 |
| 3 | 0.576 | 4.4% | -16.3% | 3/7 |

**Degradation 0 to 1 day lag**: 30.0%
**Tradeable with realistic lag?** YES

---

## 5. Data Snooping Check

- Parameter combinations per window: **15** (5 ROC lookbacks x 3 smoothing options)
- Bonferroni-adjusted significance level: **alpha = 0.0033**
- OOS Sharpe t-test: t-stat = 0.241, raw p-value = 0.8174
- **Bonferroni-adjusted p-value: 1.0000**
- Survives Bonferroni correction: **NO**

Note: Walk-forward inherently mitigates snooping (parameters chosen on train, evaluated on test). The Bonferroni check is conservative since WF already controls for overfitting. The real question is whether OOS performance is significantly different from zero.

---

## 6. Stationarity / Rolling IC Analysis

Rolling 90-day rank correlation between macro regime signal and BTC 7-day forward returns.

| Year | Mean IC | Std IC | % Positive | Min | Max |
|------|---------|--------|------------|-----|-----|
| 2020 | 0.1527 | 0.2278 | 66% | -0.2039 | 0.5235 |
| 2021 | -0.0773 | 0.1744 | 35% | -0.4177 | 0.2702 |
| 2022 | -0.1655 | 0.1474 | 10% | -0.4865 | 0.1558 |
| 2023 | -0.0689 | 0.1081 | 26% | -0.2732 | 0.1954 |
| 2024 | -0.2264 | 0.2180 | 21% | -0.5131 | 0.2253 |
| 2025 | -0.0578 | 0.3109 | 49% | -0.5735 | 0.3811 |
| 2026 | 0.2983 | 0.0637 | 100% | 0.2132 | 0.3775 |

- **Overall IC**: -0.0780 (std: 0.2376)
- **% time positive**: 34%
- **Sign changes**: 31
- **Stability assessment**: MODERATELY STABLE

---

## 7. Regime Distribution

Period: 2021-07-01 onward (with ROC=20, no smoothing)

| Regime | Days | % of Total | Description |
|--------|------|------------|-------------|
| RISK-ON | 354 | 30% | US10Y falling AND DXY falling -> Long BTC |
| RISK-OFF | 439 | 37% | US10Y rising AND DXY rising -> Short/Flat BTC |
| MIXED | 382 | 33% | Divergent signals -> Flat |
| **Total** | **1175** | **100%** | |

---

## 8. Verdict and Recommendations

### FAIL

The macro regime rotation signal **fails the kill criteria** in walk-forward validation:
- Only 3/7 positive OOS windows (need >=5)
- Mean OOS Sharpe of 0.118 is below 0.2 threshold

**Recommendation**: Do NOT proceed to implementation. The macro regime signal does not demonstrate consistent OOS profitability under walk-forward validation. While the in-sample fit from R107 looked promising, the signal does not generalize reliably across market regimes.

**Why it fails**: The relationship between macro variables (US10Y, DXY) and BTC returns is not stationary. The rolling IC analysis shows the signal flips predictive direction across time, making it unreliable as a standalone signal.

**Salvage options**:
1. Use as a regime filter (reduce position size in risk-off) rather than a standalone signal
2. Combine with V3 momentum as a conditional overlay (only trade V3 signals in risk-on regimes)
3. Test with different macro variables (e.g., real rates, credit spreads)

---

*Generated by R109_macro_regime_wf.py*
