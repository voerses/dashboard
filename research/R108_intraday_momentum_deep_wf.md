# R108 -- Intraday Momentum Breakout: Deep Walk-Forward Validation

**Date**: 2026-03-24
**Asset**: BTC spot 1h
**Base cost**: 10 bps round-trip
**R107 reference**: Standalone Sharpe 0.594, corr 0.007 with V3, 5/6 WF positive

## VERDICT: **KILL**

### Kill Reasons
- Only 4/10 positive OOS windows (need >=5/10)
- Mean OOS Sharpe -0.982 < 0.3
- Top 5 trades = 100.0% of profit (>50%)
- Edge disappears at 15bps (Sharpe=-1.271)

### Conditional Flags
- Parameter sensitivity: >30% Sharpe degradation on some params
- Works in only 1/3 regimes

## Walk-Forward Summary

| Metric | Value |
|--------|-------|
| Windows | 10 (of 10 configured) |
| Positive OOS windows | 4/10 |
| Mean OOS Sharpe | -0.982 |
| Total OOS trades | 57 |
| Total OOS return | -24.26% |

## Walk-Forward Window Detail

| Window | Train Period | Test Period | Best Params (ret/vol/hold/trail) | Train Sharpe | OOS Sharpe | OOS Trades | OOS Return | OOS WR | Positive? |
|--------|-------------|-------------|----------------------------------|-------------|-----------|-----------|-----------|--------|-----------|
| W1 | 2023-03-30 to 2023-09-26 | 2023-09-26 to 2023-12-25 | 1.5%/3.0x/4h/1.5% | 0.025 | 1.196 | 8 | 2.63% | 50% | YES |
| W2 | 2023-06-28 to 2023-12-25 | 2023-12-25 to 2024-03-24 | 2.5%/1.5x/16h/1.5% | 3.879 | -5.577 | 2 | -5.00% | 0% | NO |
| W3 | 2023-09-26 to 2024-03-24 | 2024-03-24 to 2024-06-22 | 1.5%/2.0x/16h/2.0% | 3.257 | -0.496 | 11 | -2.20% | 45% | NO |
| W4 | 2023-12-25 to 2024-06-22 | 2024-06-22 to 2024-09-20 | 1.5%/2.0x/8h/2.0% | 2.725 | -3.655 | 20 | -15.67% | 50% | NO |
| W5 | 2024-03-24 to 2024-09-20 | 2024-09-20 to 2024-12-19 | 2.0%/3.0x/8h/1.0% | 0.932 | -2.390 | 7 | -4.94% | 57% | NO |
| W6 | 2024-06-22 to 2024-12-19 | 2024-12-19 to 2025-03-19 | 3.0%/1.5x/4h/1.0% | 0.703 | 0.709 | 3 | 0.73% | 67% | YES |
| W7 | 2024-09-20 to 2025-03-19 | 2025-03-19 to 2025-06-17 | 3.0%/1.5x/8h/1.5% | 3.921 | 0.000 | 1 | -0.04% | 0% | NO |
| W8 | 2024-12-19 to 2025-06-17 | 2025-06-17 to 2025-09-15 | 3.0%/3.0x/4h/1.0% | 4.867 | 0.000 | 0 | 0.00% | 0% | NO |
| W9 | 2025-03-19 to 2025-09-15 | 2025-09-15 to 2025-12-14 | 2.5%/1.5x/6h/1.0% | 4.191 | 0.303 | 2 | 0.07% | 50% | YES |
| W10 | 2025-06-17 to 2025-12-14 | 2025-12-14 to 2026-03-14 | 2.5%/1.5x/8h/1.0% | 2.648 | 0.088 | 3 | 0.17% | 33% | YES |

## Full-Period Best Parameters

| Parameter | Value |
|-----------|-------|
| Return threshold | 1.5% |
| Volume multiplier | 2.0x |
| Max hold | 4h |
| Trailing stop | 1.5% |
| Sharpe (full period) | 0.676 |
| Trades | 380 |
| Total return | 65.25% |
| Win rate | 53.4% |
| Avg hold (bars) | 3.7 |

## Parameter Sensitivity (+/-20%)

Base Sharpe: 0.676

| Parameter | Direction | Value | Sharpe | Change | Degraded >30%? |
|-----------|----------|-------|--------|--------|----------------|
| ret_thresh | -20% | 0.012 | 0.394 | -41.8% | YES |
| ret_thresh | +20% | 0.018 | 0.465 | -31.2% | YES |
| vol_mult | -20% | 1.60 | 0.477 | -29.5% | no |
| vol_mult | +20% | 2.40 | 0.574 | -15.1% | no |
| max_hold | -20% | 3 | 0.530 | -21.6% | no |
| max_hold | +20% | 5 | 0.572 | -15.4% | no |
| trail_stop | -20% | 0.012 | 0.547 | -19.1% | no |
| trail_stop | +20% | 0.018 | 0.628 | -7.1% | no |

**WARNING**: Some parameters show >30% Sharpe degradation at +/-20% perturbation. Signal is FRAGILE on those axes.

## Regime Analysis (50-day SMA)

| Regime | Trades | Avg Return | Total Return | Win Rate | Profitable? |
|--------|--------|-----------|-------------|---------|-------------|
| UPTREND | 25 | -0.42% | -10.56% | 48% | NO |
| DOWNTREND | 25 | -0.62% | -15.47% | 44% | NO |
| RANGE | 7 | 0.25% | 1.77% | 57% | YES |

**Key finding**: Signal IS profitable in RANGE regime (V3's weakness). This provides diversification value.

## Short Side Test

| Metric | Long-Only | Short-Only |
|--------|-----------|-----------|
| Positive windows | 4/10 | 2/10 |
| Mean OOS Sharpe | -0.982 | -1.298 |
| Total OOS trades | 57 | 59 |
| Total OOS return | -24.26% | -11.55% |

Short side does NOT add meaningful value. Keep long-only.

## Cost Sensitivity

| Cost (bps) | Positive Windows | Mean OOS Sharpe | Total OOS Trades | Total OOS Return |
|-----------|-----------------|----------------|-----------------|-----------------|
| 5 | 4/10 | -0.801 | 70 | -16.87% |
| 10 | 4/10 | -0.982 | 57 | -24.26% |
| 15 | 2/10 | -1.271 | 68 | -31.65% |
| 20 | 2/10 | -1.270 | 62 | -33.56% |

**No edge at any cost level**: Signal has negative mean OOS Sharpe even at lowest tested cost (5 bps). No tradeable edge exists.

## Trade Clustering

| Metric | Value |
|--------|-------|
| Total OOS trades | 57 |
| Total PnL | -0.2426 |
| Top 5 trades % | 100.0% |
| Top 10 trades % | 100.0% |
| Profit factor | 0.00 |
| Concentrated (>50% top5)? | YES - FRAGILE |

## Kill Criteria Checklist

| Criterion | Threshold | Actual | Pass? |
|-----------|----------|--------|-------|
| Positive OOS windows | >=5/10 | 4/10 | FAIL |
| Mean OOS Sharpe | >=0.3 | -0.982 | FAIL |
| Total OOS trades | >=50 | 57 | PASS |
| Top 5 trades < 50% | <50% | 100.0% | FAIL |
| Edge at 15bps | Sharpe>=0.1 | -1.271 | FAIL |
| Param sensitivity <30% | all <30% | some >30% | CONDITIONAL |
| Multi-regime | >=2 regimes | 1/3 | CONDITIONAL |

## Final Verdict: **KILL**

The intraday momentum breakout signal **fails** deep walk-forward validation:
- Only 4/10 positive OOS windows (need >=5/10)
- Mean OOS Sharpe -0.982 < 0.3
- Top 5 trades = 100.0% of profit (>50%)
- Edge disappears at 15bps (Sharpe=-1.271)

**Recommendation**: Do NOT allocate capital to this signal. The R107 results were likely due to limited WF windows (6 vs 10) or overfitting in the initial evaluation.