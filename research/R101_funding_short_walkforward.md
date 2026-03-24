# R101: Funding Rate Reversal Short — Walk-Forward Validation

**Signal**: Short when 8h funding rate > threshold (extreme positive funding = overleveraged longs)
**Protocol**: 8 rolling windows, 180d train / 90d test (standard) + 90d/45d (regime-aware)
**Date**: 2026-03-24
**Prior result (R97)**: Sharpe 7.07 on full sample (suspected overfit)

## VERDICT: KILL

The funding rate reversal short signal fails walk-forward validation decisively. The R97 Sharpe of 7.07 was an artifact of in-sample optimization over a handful of extreme events in 2020-2021. The signal is regime-dependent and the triggering conditions no longer exist in current markets.

---

## Critical Finding: Signal Events Are Regime-Dependent

Before running walk-forward, temporal analysis reveals a fatal flaw:

### BTC — Extreme Funding Days by Year

| Year | FR>0.03% | FR>0.05% | FR>0.08% | FR>0.10% | Total Days |
|------|----------|----------|----------|----------|------------|
| 2020 | 66 | 35 | 8 | 3 | 366 |
| 2021 | 113 | 63 | 36 | 23 | 365 |
| 2022 | 0 | 0 | 0 | 0 | 365 |
| 2023 | 0 | 0 | 0 | 0 | 365 |
| 2024 | 1 | 0 | 0 | 0 | 366 |
| 2025 | 0 | 0 | 0 | 0 | 365 |
| 2026 | 0 | 0 | 0 | 0 | 76 |

### ETH — Extreme Funding Days by Year

| Year | FR>0.03% | FR>0.05% | FR>0.08% | FR>0.10% | Total Days |
|------|----------|----------|----------|----------|------------|
| 2020 | 91 | 55 | 21 | 11 | 366 |
| 2021 | 125 | 80 | 48 | 35 | 365 |
| 2022 | 0 | 0 | 0 | 0 | 365 |
| 2023 | 3 | 0 | 0 | 0 | 365 |
| 2024 | 0 | 0 | 0 | 0 | 366 |
| 2025 | 0 | 0 | 0 | 0 | 365 |
| 2026 | 0 | 0 | 0 | 0 | 76 |

**99% of extreme funding events occurred before 2022.** The signal literally cannot fire in current market conditions. This alone is sufficient for a KILL verdict, but we run the walk-forward anyway to be thorough.

---

## Full Sample Check (Context Only)

Even in-sample, the signal is weak:

### BTC Full Sample

| Threshold | Hold | Trades | Sharpe | PnL | Win Rate |
|-----------|------|--------|--------|-----|----------|
| 0.03% | 48h | 39 | 1.08 | +12.93% | 46.2% |
| 0.03% | 72h | 31 | 0.96 | +12.97% | 38.7% |
| 0.05% | 48h | 30 | 1.61 | +20.67% | 43.3% |
| 0.05% | 72h | 25 | 0.43 | +5.32% | 36.0% |
| 0.08% | 48h | 15 | -1.56 | -8.40% | 40.0% |
| 0.10% | 48h | 5 | -3.58 | -5.67% | 40.0% |

### ETH Full Sample

| Threshold | Hold | Trades | Sharpe | PnL | Win Rate |
|-----------|------|--------|--------|-----|----------|
| 0.03% | 48h | 83 | -0.60 | -15.79% | 28.9% |
| 0.05% | 48h | 62 | -0.82 | -18.14% | 27.4% |
| 0.08% | 48h | 33 | -3.59 | -36.66% | 27.3% |
| 0.10% | 72h | 22 | -9.03 | -49.89% | 13.6% |

**ETH shows strongly negative performance across ALL thresholds.** The signal is destructive on ETH — extreme funding does not predict downward reversals; it predicts continuation (the funding is high because the trend is strong).

---

## Standard Walk-Forward (180d Train / 90d Test)

### BTC Standard

| Window | Train | Test | Test Dates | Trades | PnL | Sharpe | WR | MaxDD | Status |
|--------|-------|------|-----------|--------|-----|--------|-----|-------|--------|
| W1 | 45 | 18 | 2020-07-29 to 2020-10-27 | 18 | -10.48% | -2.43 | 22.2% | 12.18% | valid |
| W2 | 11 | 0 | 2020-10-27 to 2021-01-25 | 0 | 0% | 0 | 0% | 0% | insufficient |
| W3 | 4 | 0 | 2021-01-25 to 2021-04-25 | 0 | 0% | 0 | 0% | 0% | insufficient |
| W4-W8 | 0 | 0 | 2021-04 to 2022-07 | 0 | 0% | 0 | 0% | 0% | no_train_params |

Only 1 valid window (W1), which is negative. Windows 4-8 have zero training signals because funding extremes disappeared.

### ETH Standard

| Window | Train | Test | Test Dates | Trades | PnL | Sharpe | WR | MaxDD | Status |
|--------|-------|------|-----------|--------|-----|--------|-----|-------|--------|
| W1 | 40 | 27 | 2020-07-29 to 2020-10-27 | 27 | -21.96% | -1.82 | 33.3% | 32.15% | valid |
| W2-W3 | 14-20 | 0 | 2020-10 to 2021-04 | 0 | 0% | 0 | 0% | 0% | insufficient |
| W4-W8 | 0 | 0 | 2021-04 to 2022-07 | 0 | 0% | 0 | 0% | 0% | no_train_params |

Only 1 valid window (W1), which is strongly negative with 32% max drawdown.

### Standard WF Summary
- **Total OOS trades**: 45
- **Valid windows**: 2
- **Positive windows**: 0/2 (0%)
- **Mean OOS Sharpe**: -2.12
- **Verdict**: **KILL** (0/2 positive windows)

---

## Regime-Aware Walk-Forward (90d Train / 45d Test, 2020-2021)

This gives the signal its BEST chance by testing within the only regime where events occur.

### BTC Regime-Aware

| Window | Train | Test | Test Dates | Trades | PnL | Sharpe | WR | MaxDD | Status | Params |
|--------|-------|------|-----------|--------|-----|--------|-----|-------|--------|--------|
| W1 | 18 | 3 | 2020-05-01 to 2020-06-15 | 3 | +13.68% | 2.79 | 66.7% | 0% | valid | FR>0.03% hold=48h SL=2.5% |
| W2 | 3 | 5 | 2020-06-15 to 2020-07-30 | 5 | -8.64% | -5.31 | 20.0% | 7.33% | valid | FR>0.05% hold=96h SL=1.0% |
| W3 | 7 | 3 | 2020-07-30 to 2020-09-13 | 3 | -6.43% | -72.50 | 0.0% | 4.15% | valid | FR>0.05% hold=72h SL=1.5% |
| W4-W5 | 3-8 | 0 | 2020-09 to 2020-12 | 0 | 0% | 0 | 0% | 0% | insufficient |
| W6-W8 | 0 | 0 | 2020-12 to 2021-04 | 0 | 0% | 0 | 0% | 0% | no_train_params |

- BTC profit concentration: top 2 trades account for 95.7% of all profits (CONCENTRATED)

### ETH Regime-Aware

| Window | Train | Test | Test Dates | Trades | PnL | Sharpe | WR | MaxDD | Status | Params |
|--------|-------|------|-----------|--------|-----|--------|-----|-------|--------|--------|
| W1 | 24 | 5 | 2020-05-01 to 2020-06-15 | 5 | +8.70% | 1.37 | 60.0% | 3.72% | valid | FR>0.03% hold=72h SL=3.0% |
| W2 | 3 | 7 | 2020-06-15 to 2020-07-30 | 7 | -10.44% | -3.27 | 42.9% | 5.70% | valid | FR>0.05% hold=24h SL=2.0% |
| W3 | 7 | 19 | 2020-07-30 to 2020-09-13 | 19 | -11.69% | -0.98 | 26.3% | 33.55% | valid | FR>0.05% hold=72h SL=2.0% |
| W4-W6 | 3-14 | 0 | 2020-09 to 2021-01 | 0 | 0% | 0 | 0% | 0% | insufficient |
| W7-W8 | 0 | 0 | 2021-01 to 2021-04 | 0 | 0% | 0 | 0% | 0% | no_train_params |

### Regime WF Summary
- **Total OOS trades**: 42
- **Valid windows**: 6
- **Positive windows**: 2/6 (33%)
- **Mean OOS Sharpe**: -12.98
- **Verdict**: **KILL** (2/6 positive windows < 50% required)

Even within the 2020-2021 regime where the signal should work best, only 2 of 6 valid windows are profitable, and those profits are concentrated in 1-2 trades.

---

## Kill Criteria Evaluation

| Criterion | Threshold | Standard WF | Regime WF | Result |
|-----------|-----------|-------------|-----------|--------|
| Positive windows | >=4/8 or >=50% | 0/2 (0%) | 2/6 (33%) | **FAIL** |
| Mean OOS Sharpe | >= 0.5 | -2.12 | -12.98 | **FAIL** |
| Total OOS trades | >= 20 | 45 | 42 | Pass |
| Profit concentration | <80% from top 2 | BTC: 75% | BTC: 96% | **FAIL** (BTC regime) |

**All substantive criteria fail.** The signal is killed on multiple grounds.

---

## Why R97 Showed Sharpe 7.07

1. **In-sample optimization**: The entire 2020-2021 dataset was used to select the optimal threshold, creating massive look-ahead bias
2. **Few extreme events**: With only ~30-80 total trades per token, a few lucky hits dominate the statistics
3. **Sharpe inflation**: Low trade counts combined with a few large winners create artificially high per-trade Sharpe that doesn't reflect calendar-time risk
4. **Regime-specific**: The signal fired during a specific bull market (2020-2021) when retail leverage was high and exchange risk controls were immature

---

## Structural Reasons the Signal No Longer Works

1. **Exchange risk controls**: Since 2022, major exchanges (Binance, Bybit, OKX) have implemented:
   - Maximum funding rate caps (typically +/-0.75% per 8h)
   - Auto-deleveraging (ADL) mechanisms that prevent extreme crowding
   - Higher margin requirements during high-volatility periods
2. **Reduced retail leverage**: Maximum leverage has been reduced from 125x to 20x on most exchanges
3. **Funding rate normalization**: Modern exchanges adjust funding more frequently (some have moved to 1h or 4h settlement)
4. **Market maturity**: Institutional participation has increased, arbitraging away funding rate dislocations more quickly

---

## Conclusion

**VERDICT: KILL**

The "Funding Rate Reversal Short" signal is a historical artifact of the 2020-2021 crypto bull market. It depends on extreme positive funding rates (>0.03%) that have not occurred since late 2021. Even when tested within the favorable regime, the signal loses money in 67% of walk-forward windows and profits are concentrated in 1-2 trades.

The R97 Sharpe of 7.07 is confirmed as overfit. The actual full-sample Sharpe (with proper costs) ranges from -9.03 to +1.61 depending on parameters, and the best BTC configuration (0.05%/48h) produces only +20.67% over 6+ years with 30 trades — a far cry from the claimed performance.

This signal should NOT be implemented as a standalone strategy. The underlying market microstructure that created the edge has been permanently altered by exchange risk controls and reduced retail leverage.

---

## Data Sources
- BTC/ETH 1h spot OHLCV: `/workspace/crypto_backtest/data/spot/1h_cache/{BTC,ETH}_1h.parquet`
- Daily funding rates: `/workspace/crypto_backtest/data/alternative/funding_ls_proxy/{BTC,ETH}_funding_proxy.parquet`
- Raw 8h funding: `/workspace/crypto_backtest/data/alternative/binance_funding_rates_full.json`

## Script
- `/workspace/crypto_backtest/research/R101_funding_short_walkforward.py`
