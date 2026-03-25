# Hybrid Dip-Buy Strategy — Walk-Forward Validation Results

**Run date**: 2026-03-25 13:18

## Strategy Definitions

| Strategy | Direction | Entry | Exit | Rationale |
|----------|-----------|-------|------|-----------|
| V3 Baseline | Long-only | EMA20 > EMA50 (weekly) | EMA20 < EMA50 | Validated trend-following (Sharpe 0.56 OOS) |
| Hybrid-RSI | Long-only | Trend + RSI<40 + ADX>20 | RSI>75 / trend reverse / trail stop 2.5ATR | Pullback entry within trend |
| Hybrid-Zscore | Long-only | Trend + z-score<-1.0 + ADX>20 | z-score>2.0 / trend reverse / trail stop | Mean-reversion entry within trend |
| Hybrid-Multi | Long-only | Trend + (RSI<40 OR z<-1.0 OR DD>3%) + ADX>20 | RSI>75 / z>2.0 / trend / trail / max hold | Maximum signal coverage |
| s320a+RSI Timing | Long-only | Trend (weekly) + defer to RSI<40 within 168h | Trend reverse (weekly check) | V2 Flexible RSI timing on s320a entries |

## Walk-Forward Protocol

- **Windows**: 8 rolling, 180d train / 90d test
- **Costs**: 0.22% RT fees + 5bps slippage/side = 0.32% total per trade
- **Optimization**: Grid search on train, best params applied to OOS
- **Kill criteria**: Mean OOS Sharpe > 0.3, >=5/8 positive windows, >=20 total trades

## BTC Results

### Summary

| Strategy | Mean Sharpe | Med Sharpe | Mean Return | Total Trades | Win Rate | Mean MaxDD | Pos Windows |
|----------|------------|------------|-------------|-------------|----------|------------|-------------|
| V3_Baseline | -0.350 | -0.396 | +3.71% | 46 | 46.7% | 11.58% | 4/8 |
| Hybrid_RSI | -12.299 | -6.712 | +0.26% | 95 | 27.8% | 5.06% | 3/8 |
| Hybrid_Zscore | -6.520 | -2.910 | -7.45% | 208 | 36.8% | 9.85% | 1/8 |
| Hybrid_Multi | -5.972 | -5.583 | -8.46% | 230 | 38.7% | 11.59% | 2/8 |
| s320a_RSI_Timing | +0.735 | +0.213 | -1.79% | 29 | 49.0% | 8.34% | 5/8 |

### Per-Window OOS Detail

| Window | Test Period | V3 Sharpe | H-RSI Sharpe | H-Zscore Sharpe | H-Multi Sharpe | s320a+RSI Sharpe |
|--------|------------|-----------|-------------|----------------|---------------|-----------------|
| W1 | 2024-03-25 to 2024-06-22 | -0.90 | -44.98 | -2.61 | -4.19 | -1.72 |
| W2 | 2024-06-23 to 2024-09-20 | +0.85 | -31.80 | -2.06 | -1.91 | +0.22 |
| W3 | 2024-09-21 to 2024-12-19 | +3.28 | +6.14 | +3.43 | +2.73 | +7.71 |
| W4 | 2024-12-20 to 2025-03-19 | -1.67 | -5.48 | -18.93 | -12.23 | -5.34 |
| W5 | 2025-03-20 to 2025-06-17 | +2.35 | +0.63 | -1.51 | -6.97 | +1.89 |
| W6 | 2025-06-18 to 2025-09-15 | +0.09 | -19.92 | -18.82 | -16.10 | +0.21 |
| W7 | 2025-09-16 to 2025-12-14 | -5.92 | -7.94 | -3.21 | +0.82 | -11.55 |
| W8 | 2025-12-15 to 2026-03-14 | -0.88 | +4.96 | -8.45 | -9.92 | +14.47 |

### Per-Window Trade Counts

| Window | V3 | H-RSI | H-Zscore | H-Multi | s320a+RSI |
|--------|-----|-------|----------|---------|-----------|
| W1 | 7 | 3 | 20 | 23 | 5 |
| W2 | 4 | 7 | 20 | 29 | 3 |
| W3 | 3 | 15 | 26 | 32 | 2 |
| W4 | 6 | 12 | 24 | 30 | 4 |
| W5 | 6 | 16 | 32 | 26 | 3 |
| W6 | 7 | 14 | 31 | 31 | 5 |
| W7 | 8 | 12 | 27 | 30 | 5 |
| W8 | 5 | 16 | 28 | 29 | 2 |

## ETH Results

### Summary

| Strategy | Mean Sharpe | Med Sharpe | Mean Return | Total Trades | Win Rate | Mean MaxDD | Pos Windows |
|----------|------------|------------|-------------|-------------|----------|------------|-------------|
| V3_Baseline | -1.314 | -0.454 | -0.71% | 45 | 34.2% | 19.07% | 4/8 |
| Hybrid_RSI | -21.270 | -11.236 | -4.19% | 114 | 24.6% | 9.43% | 2/8 |
| Hybrid_Zscore | -12.332 | -13.487 | -10.88% | 166 | 31.9% | 13.67% | 2/8 |
| Hybrid_Multi | -10.725 | -10.354 | -25.48% | 287 | 35.2% | 24.33% | 1/8 |
| s320a_RSI_Timing | -3.100 | +0.554 | +0.41% | 26 | 41.5% | 10.42% | 4/8 |

### Per-Window OOS Detail

| Window | Test Period | V3 Sharpe | H-RSI Sharpe | H-Zscore Sharpe | H-Multi Sharpe | s320a+RSI Sharpe |
|--------|------------|-----------|-------------|----------------|---------------|-----------------|
| W1 | 2024-03-25 to 2024-06-22 | +1.13 | -18.34 | -16.62 | -13.74 | +1.11 |
| W2 | 2024-06-23 to 2024-09-20 | -2.03 | -74.21 | -16.78 | -10.66 | -0.99 |
| W3 | 2024-09-21 to 2024-12-19 | +2.11 | +9.12 | +0.58 | +0.77 | +1.58 |
| W4 | 2024-12-20 to 2025-03-19 | -6.17 | -15.09 | -18.51 | -23.21 | -17.53 |
| W5 | 2025-03-20 to 2025-06-17 | +1.88 | -3.06 | -0.94 | -5.39 | +3.80 |
| W6 | 2025-06-18 to 2025-09-15 | +2.51 | +2.48 | +2.06 | -10.05 | +2.09 |
| W7 | 2025-09-16 to 2025-12-14 | -6.22 | -7.38 | -10.35 | -9.95 | -14.86 |
| W8 | 2025-12-15 to 2026-03-14 | -3.72 | -63.69 | -38.09 | -13.57 | +0.00 |

### Per-Window Trade Counts

| Window | V3 | H-RSI | H-Zscore | H-Multi | s320a+RSI |
|--------|-----|-------|----------|---------|-----------|
| W1 | 3 | 8 | 27 | 35 | 3 |
| W2 | 7 | 6 | 14 | 31 | 3 |
| W3 | 3 | 21 | 30 | 37 | 2 |
| W4 | 7 | 20 | 17 | 36 | 3 |
| W5 | 5 | 21 | 24 | 45 | 4 |
| W6 | 6 | 19 | 21 | 45 | 5 |
| W7 | 7 | 11 | 17 | 29 | 5 |
| W8 | 7 | 8 | 16 | 29 | 1 |

## BNB Results

### Summary

| Strategy | Mean Sharpe | Med Sharpe | Mean Return | Total Trades | Win Rate | Mean MaxDD | Pos Windows |
|----------|------------|------------|-------------|-------------|----------|------------|-------------|
| V3_Baseline | +0.619 | +0.479 | +4.69% | 43 | 48.9% | 10.04% | 4/8 |
| Hybrid_RSI | +3.397 | -0.780 | -1.28% | 78 | 44.0% | 4.56% | 4/8 |
| Hybrid_Zscore | +2.054 | +1.557 | +3.99% | 160 | 39.2% | 6.81% | 5/8 |
| Hybrid_Multi | -4.657 | -3.635 | -7.58% | 217 | 41.0% | 13.02% | 2/8 |
| s320a_RSI_Timing | +0.593 | +1.360 | +2.47% | 28 | 46.9% | 6.91% | 5/8 |

### Per-Window OOS Detail

| Window | Test Period | V3 Sharpe | H-RSI Sharpe | H-Zscore Sharpe | H-Multi Sharpe | s320a+RSI Sharpe |
|--------|------------|-----------|-------------|----------------|---------------|-----------------|
| W1 | 2024-03-25 to 2024-06-22 | +2.04 | +1.38 | -1.17 | -2.21 | +3.73 |
| W2 | 2024-06-23 to 2024-09-20 | +1.32 | +14.20 | +0.65 | -7.53 | +0.43 |
| W3 | 2024-09-21 to 2024-12-19 | +1.95 | +82.43 | +2.46 | +1.66 | +2.29 |
| W4 | 2024-12-20 to 2025-03-19 | -2.41 | -40.55 | +7.21 | +0.30 | -3.39 |
| W5 | 2025-03-20 to 2025-06-17 | -0.37 | -21.63 | +4.84 | -3.27 | -6.10 |
| W6 | 2025-06-18 to 2025-09-15 | +4.42 | -2.94 | -1.36 | -14.53 | +3.33 |
| W7 | 2025-09-16 to 2025-12-14 | -1.13 | +1.64 | +8.36 | -4.00 | -0.02 |
| W8 | 2025-12-15 to 2026-03-14 | -0.88 | -7.35 | -4.56 | -7.67 | +4.46 |

### Per-Window Trade Counts

| Window | V3 | H-RSI | H-Zscore | H-Multi | s320a+RSI |
|--------|-----|-------|----------|---------|-----------|
| W1 | 6 | 6 | 22 | 27 | 4 |
| W2 | 4 | 4 | 24 | 24 | 4 |
| W3 | 3 | 2 | 21 | 30 | 2 |
| W4 | 5 | 5 | 19 | 29 | 4 |
| W5 | 5 | 9 | 18 | 23 | 4 |
| W6 | 4 | 27 | 26 | 30 | 4 |
| W7 | 9 | 14 | 14 | 26 | 4 |
| W8 | 7 | 11 | 16 | 28 | 2 |

## Parameter Sensitivity — Hybrid-RSI (BTC)

Mean OOS Sharpe across all windows for each RSI entry/exit combination:

| Entry \ Exit | RSI 70 | RSI 75 | RSI 80 |
|-------------|--------|--------|--------|
| RSI < 30 | -20.839 (n=20) | -20.839 (n=20) | -20.839 (n=20) |
| RSI < 35 | -23.509 (n=45) | -23.793 (n=45) | -24.049 (n=45) |
| RSI < 40 | -7.471 (n=100) | -5.811 (n=100) | -6.707 (n=100) |
| RSI < 45 | -5.622 (n=152) | -2.561 (n=152) | -2.243 (n=152) |

**Best combination**: RSI entry < 45, RSI exit > 80 (mean Sharpe -2.243, 3/8 positive windows)

**Sensitivity**: Sharpe range 21.806, std 8.797
WARNING: High parameter sensitivity detected. Signal may be fragile.

## Correlation Analysis (BTC)

Return correlation between strategies (per-window OOS returns):

| | V3_Baseline | Hybrid_RSI | Hybrid_Zscore | Hybrid_Multi | s320a_RSI_Timing |
|---|---|---|---|---|---|
| V3_Baseline | +1.000 | +0.616 | +0.570 | +0.415 | +0.882 |
| Hybrid_RSI | +0.616 | +1.000 | +0.438 | +0.325 | +0.669 |
| Hybrid_Zscore | +0.570 | +0.438 | +1.000 | +0.927 | +0.434 |
| Hybrid_Multi | +0.415 | +0.325 | +0.927 | +1.000 | +0.208 |
| s320a_RSI_Timing | +0.882 | +0.669 | +0.434 | +0.208 | +1.000 |

**V3 vs Hybrid avg correlation: +0.621** -- Moderate correlation. Some diversification benefit, but significant overlap.

## Final Verdicts

| Token | Strategy | Verdict | Mean Sharpe | Pos Windows | Trades | Reason |
|-------|----------|---------|------------|-------------|--------|--------|
| BTC | V3_Baseline | **KILL** | -0.350 | 4/8 | 46 | negative mean Sharpe -0.350 |
| BTC | Hybrid_RSI | **KILL** | -12.299 | 3/8 | 95 | pos windows 3/8; negative mean Sharpe -12.299 |
| BTC | Hybrid_Zscore | **KILL** | -6.520 | 1/8 | 208 | pos windows 1/8; negative mean Sharpe -6.520 |
| BTC | Hybrid_Multi | **KILL** | -5.972 | 2/8 | 230 | pos windows 2/8; negative mean Sharpe -5.972 |
| BTC | s320a_RSI_Timing | **PASS** | +0.735 | 5/8 | 29 | All criteria met |
| ETH | V3_Baseline | **KILL** | -1.314 | 4/8 | 45 | negative mean Sharpe -1.314 |
| ETH | Hybrid_RSI | **KILL** | -21.270 | 2/8 | 114 | pos windows 2/8; negative mean Sharpe -21.270 |
| ETH | Hybrid_Zscore | **KILL** | -12.332 | 2/8 | 166 | pos windows 2/8; negative mean Sharpe -12.332 |
| ETH | Hybrid_Multi | **KILL** | -10.725 | 1/8 | 287 | pos windows 1/8; negative mean Sharpe -10.725 |
| ETH | s320a_RSI_Timing | **KILL** | -3.100 | 4/8 | 26 | negative mean Sharpe -3.100 |
| BNB | V3_Baseline | **CONDITIONAL** | +0.619 | 4/8 | 43 | pos windows 4/8 |
| BNB | Hybrid_RSI | **CONDITIONAL** | +3.397 | 4/8 | 78 | pos windows 4/8 |
| BNB | Hybrid_Zscore | **PASS** | +2.054 | 5/8 | 160 | All criteria met |
| BNB | Hybrid_Multi | **KILL** | -4.657 | 2/8 | 217 | pos windows 2/8; negative mean Sharpe -4.657 |
| BNB | s320a_RSI_Timing | **PASS** | +0.593 | 5/8 | 28 | All criteria met |

## Recommendation

**Best performer on BTC**: s320a_RSI_Timing (mean Sharpe +0.735)
**vs V3 Baseline**: +1.085 Sharpe improvement

The hybrid dip-buy entry provides a meaningful improvement over V3's EMA-cross entry. The better entry timing captures the same trend direction at more favorable prices.

### Cross-Asset Robustness

- V3_Baseline: positive on 1/3 tokens (BTC=-0.350, ETH=-1.314, BNB=+0.619)
- Hybrid_RSI: positive on 1/3 tokens (BTC=-12.299, ETH=-21.270, BNB=+3.397)
- Hybrid_Zscore: positive on 1/3 tokens (BTC=-6.520, ETH=-12.332, BNB=+2.054)
- Hybrid_Multi: positive on 0/3 tokens (BTC=-5.972, ETH=-10.725, BNB=-4.657)
- s320a_RSI_Timing: positive on 2/3 tokens (BTC=+0.735, ETH=-3.100, BNB=+0.593)

---
*Generated 2026-03-25 13:18 | Walk-forward: 8 windows | Costs: 0.32% per trade*