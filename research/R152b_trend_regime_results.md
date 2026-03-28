# R152b -- BTC Trend-Following with REGIME-ONLY EXITS

## Motivation
R152 failed (-1.9% annual) because the ATR(14) hourly trailing stop was too tight (~1.3%) for a weekly-timescale EMA entry signal. Every trade got stopped out by intra-day noise within hours.

**Solution**: Remove ATR trailing stop entirely. Exit ONLY when:
1. EMA(168h) crosses below EMA(720h) (bearish cross for longs)
2. Regime changes to a non-matching state (DOWNTREND/CRISIS closes longs)

## Strategy
- **Entry signal**: Continuous (not just crosses). LONG when EMA(168h) > EMA(720h) AND regime allows
- **Exit**: Regime change OR EMA cross reversal. NO ATR trailing stop.
- **Regime filter**: V4 daily regime (EMA 20d/50d + ADX + vol), shifted by 1 day for causality (np.roll)
- **Long sizing**: UPTREND=70%, RANGE/QUIET=30%, DOWNTREND/CRISIS=0%
- **Short sizing** (Variant B only): DOWNTREND=30%
- **Costs**: 7 bps per side on entry/exit only + hourly funding
- **Market**: BTC perp (Binance)
- **Data**: 2020-01-01 to 2026-03-17 (54425 hourly bars)

## Regime Distribution

| Regime | Hours | Pct |
|--------|-------|-----|
| CRISIS | 240 | 0.4% |
| QUIET | 6,648 | 12.2% |
| UPTREND | 21,168 | 38.9% |
| RANGE | 11,544 | 21.2% |
| DOWNTREND | 14,825 | 27.2% |

---

## Variant A: LONG ONLY

Long when EMA(168h) > EMA(720h) AND regime is UPTREND/RANGE/QUIET. Flat otherwise.

### Full Period

| Leverage | Ann.Return | Sharpe | Sortino | MaxDD | Calmar | Trades | WinRate | PF | AvgHold |
|----------|-----------|--------|---------|-------|--------|--------|---------|-----|---------|
| 1x | +26.3% | 0.65 | 0.60 | -49.0% | 0.54 | 42 | 48% | 5.99 | 646h (26.9d) |
| 2x | +38.4% | 0.75 | 0.72 | -56.0% | 0.69 | 42 | 48% | 5.99 | 646h (26.9d) |
| 3x | +46.6% | 0.81 | 0.79 | -58.8% | 0.79 | 42 | 48% | 5.99 | 646h (26.9d) |

### Last 12 Months (2025-03-28 to end)

| Leverage | Ann.Return | Sharpe | Sortino | MaxDD | Calmar | Trades | WinRate | PF | AvgHold |
|----------|-----------|--------|---------|-------|--------|--------|---------|-----|---------|
| 1x | -0.9% | -0.20 | -0.14 | -4.6% | -0.20 | 10 | 60% | 0.69 | 363h (15.1d) |
| 2x | -1.1% | -0.20 | -0.14 | -5.2% | -0.20 | 10 | 60% | 0.69 | 363h (15.1d) |
| 3x | -1.1% | -0.20 | -0.14 | -5.4% | -0.20 | 10 | 60% | 0.69 | 363h (15.1d) |

### Exit Reasons (1x)

| Reason | Count | Pct |
|--------|-------|-----|
| ema_cross | 31 | 74% |
| regime_change | 11 | 26% |

### Monthly Returns (1x Leverage)

| Year | Jan | Feb | Mar | Apr | May | Jun | Jul | Aug | Sep | Oct | Nov | Dec | YTD |
|------|------|------|------|------|------|------|------|------|------|------|------|------|------|
| 2020 | - | -3.3% | +0.0% | +9.6% | -1.5% | +4.9% | +12.6% | +0.5% | -8.5% | +15.0% | +27.9% | +34.3% | +91.7% |
| 2021 | +10.0% | +29.0% | -25.4% | +57.0% | -1.4% | +0.0% | +0.0% | +1.0% | -1.5% | +5.0% | -3.7% | +0.0% | +69.9% |
| 2022 | +0.0% | +0.0% | +1.7% | -2.1% | +0.0% | +0.0% | +0.0% | -0.7% | +0.0% | -0.1% | -3.3% | -0.5% | -4.9% |
| 2023 | +6.6% | -3.6% | +2.3% | -1.3% | -0.2% | +0.2% | -0.5% | +0.0% | -0.3% | +5.5% | +1.9% | +2.8% | +13.5% |
| 2024 | -5.5% | +7.6% | -2.5% | +3.1% | +0.0% | -0.2% | -0.4% | -1.1% | +0.2% | +1.2% | +6.9% | -0.7% | +8.8% |
| 2025 | -0.3% | -0.1% | +0.0% | +0.7% | +0.7% | -0.1% | +0.5% | -0.7% | -0.2% | -1.0% | +0.0% | +0.0% | -0.4% |
| 2026 | -0.9% | +0.0% | +0.1% | - | - | - | - | - | - | - | - | - | -0.9% |

### Monthly Returns (2x Leverage)

| Year | Jan | Feb | Mar | Apr | May | Jun | Jul | Aug | Sep | Oct | Nov | Dec | YTD |
|------|------|------|------|------|------|------|------|------|------|------|------|------|------|
| 2020 | - | -6.6% | +0.0% | +20.0% | -2.9% | +9.5% | +23.3% | +0.9% | -14.2% | +26.9% | +45.4% | +49.1% | +151.4% |
| 2021 | +12.9% | +36.4% | -30.1% | +72.3% | -1.7% | +0.0% | +0.0% | +1.1% | -1.8% | +5.8% | -4.2% | +0.0% | +90.7% |
| 2022 | +0.0% | +0.0% | +1.9% | -2.4% | +0.0% | +0.0% | +0.0% | -0.8% | +0.0% | -0.1% | -3.8% | -0.6% | -5.7% |
| 2023 | +7.7% | -4.2% | +2.7% | -1.4% | -0.3% | +0.3% | -0.6% | +0.0% | -0.3% | +6.4% | +2.2% | +3.2% | +15.7% |
| 2024 | -6.3% | +8.8% | -2.9% | +3.6% | +0.0% | -0.2% | -0.4% | -1.2% | +0.2% | +1.4% | +7.9% | -0.8% | +10.2% |
| 2025 | -0.3% | -0.2% | +0.0% | +0.8% | +0.8% | -0.1% | +0.6% | -0.8% | -0.2% | -1.1% | +0.0% | +0.0% | -0.5% |
| 2026 | -1.1% | +0.0% | +0.1% | - | - | - | - | - | - | - | - | - | -1.0% |

### Trade Analysis (1x)

- Total trades: 42
- Avg PnL per trade: +7.76%
- Median PnL per trade: -0.04%
- Avg holding period: 646h (26.9d)
- Median holding period: 297h (12.4d)

#### Top 5 Trades
| Entry | Exit | Dir | PnL | Hold | Exit Reason |
|-------|------|-----|-----|------|-------------|
| 2020-10-09 | 2021-04-21 | LONG | +258.14% | 4649h | ema_cross |
| 2024-10-11 | 2024-12-28 | LONG | +33.13% | 1855h | ema_cross |
| 2024-02-01 | 2024-04-15 | LONG | +30.79% | 1772h | ema_cross |
| 2023-09-29 | 2024-01-18 | LONG | +16.39% | 2658h | ema_cross |
| 2020-04-29 | 2020-06-26 | LONG | +11.41% | 1400h | ema_cross |

#### Bottom 5 Trades
| Entry | Exit | Dir | PnL | Hold | Exit Reason |
|-------|------|-----|-----|------|-------------|
| 2022-08-15 | 2022-08-20 | LONG | -4.25% | 120h | regime_change |
| 2026-01-18 | 2026-01-21 | LONG | -4.68% | 72h | regime_change |
| 2024-07-24 | 2024-08-04 | LONG | -5.67% | 267h | ema_cross |
| 2021-09-16 | 2021-09-20 | LONG | -6.78% | 108h | ema_cross |
| 2022-10-26 | 2022-11-09 | LONG | -12.08% | 340h | ema_cross |

---

## Variant B: LONG/SHORT

Long when EMA(168h) > EMA(720h) AND regime is UPTREND/RANGE/QUIET. Short when EMA(168h) < EMA(720h) AND regime is DOWNTREND. Flat in CRISIS.

### Full Period

| Leverage | Ann.Return | Sharpe | Sortino | MaxDD | Calmar | Trades | WinRate | PF | AvgHold |
|----------|-----------|--------|---------|-------|--------|--------|---------|-----|---------|
| 1x | +26.7% | 0.66 | 0.73 | -49.1% | 0.54 | 75 | 45% | 4.42 | 534h (22.3d) |
| 2x | +38.9% | 0.76 | 0.88 | -56.2% | 0.69 | 75 | 45% | 4.42 | 534h (22.3d) |
| 3x | +47.2% | 0.82 | 0.96 | -59.0% | 0.80 | 75 | 45% | 4.42 | 534h (22.3d) |

### Last 12 Months (2025-03-28 to end)

| Leverage | Ann.Return | Sharpe | Sortino | MaxDD | Calmar | Trades | WinRate | PF | AvgHold |
|----------|-----------|--------|---------|-------|--------|--------|---------|-----|---------|
| 1x | +1.8% | 0.42 | 0.38 | -4.4% | 0.42 | 17 | 59% | 1.54 | 362h (15.1d) |
| 2x | +2.1% | 0.42 | 0.38 | -4.9% | 0.42 | 17 | 59% | 1.54 | 362h (15.1d) |
| 3x | +2.2% | 0.42 | 0.38 | -5.1% | 0.42 | 17 | 59% | 1.54 | 362h (15.1d) |

### Exit Reasons (1x)

| Reason | Count | Pct |
|--------|-------|-----|
| ema_cross | 41 | 55% |
| regime_change | 34 | 45% |

### Monthly Returns (1x Leverage)

| Year | Jan | Feb | Mar | Apr | May | Jun | Jul | Aug | Sep | Oct | Nov | Dec | YTD |
|------|------|------|------|------|------|------|------|------|------|------|------|------|------|
| 2020 | - | -3.3% | +0.0% | +9.6% | -1.5% | +4.9% | +12.6% | +0.5% | -9.5% | +15.2% | +28.2% | +34.6% | +91.3% |
| 2021 | +10.1% | +29.1% | -25.5% | +57.3% | +0.6% | +0.1% | -1.7% | +1.0% | -1.8% | +4.2% | -3.7% | +0.5% | +70.1% |
| 2022 | +1.4% | -1.9% | +1.3% | -1.5% | +1.2% | +1.1% | -0.8% | -0.3% | -0.5% | +0.4% | -3.7% | -0.5% | -3.7% |
| 2023 | +6.6% | -3.6% | +2.3% | -1.2% | -0.2% | -0.3% | -0.5% | +0.2% | -0.3% | +5.5% | +1.9% | +2.8% | +13.2% |
| 2024 | -5.5% | +7.6% | -2.5% | +3.1% | -0.4% | -0.3% | -0.6% | -1.6% | +0.1% | +1.2% | +7.0% | -0.7% | +7.4% |
| 2025 | -0.3% | +0.4% | +0.1% | +0.7% | +0.7% | -0.1% | +0.5% | -0.7% | -0.3% | -1.2% | +1.1% | +0.0% | +0.9% |
| 2026 | -0.1% | +0.9% | +0.1% | - | - | - | - | - | - | - | - | - | +0.9% |

### Monthly Returns (2x Leverage)

| Year | Jan | Feb | Mar | Apr | May | Jun | Jul | Aug | Sep | Oct | Nov | Dec | YTD |
|------|------|------|------|------|------|------|------|------|------|------|------|------|------|
| 2020 | - | -6.6% | +0.0% | +20.0% | -2.9% | +9.5% | +23.3% | +0.9% | -16.0% | +27.4% | +46.1% | +49.6% | +151.5% |
| 2021 | +13.0% | +36.6% | -30.3% | +72.8% | +0.7% | +0.1% | -2.0% | +1.1% | -2.1% | +4.9% | -4.3% | +0.6% | +91.1% |
| 2022 | +1.6% | -2.2% | +1.5% | -1.7% | +1.4% | +1.3% | -0.9% | -0.3% | -0.6% | +0.5% | -4.3% | -0.6% | -4.3% |
| 2023 | +7.7% | -4.1% | +2.7% | -1.4% | -0.3% | -0.3% | -0.6% | +0.3% | -0.3% | +6.4% | +2.2% | +3.2% | +15.3% |
| 2024 | -6.2% | +8.7% | -2.9% | +3.6% | -0.5% | -0.4% | -0.6% | -1.9% | +0.1% | +1.4% | +8.0% | -0.8% | +8.5% |
| 2025 | -0.3% | +0.4% | +0.1% | +0.8% | +0.8% | -0.1% | +0.6% | -0.8% | -0.3% | -1.4% | +1.2% | +0.0% | +1.0% |
| 2026 | -0.1% | +1.0% | +0.1% | - | - | - | - | - | - | - | - | - | +1.0% |

### Trade Analysis (1x)

- Total trades: 75 (L=42, S=33)
- Long avg PnL: +7.76%, win rate: 48%, avg hold: 646h
- Short avg PnL: +0.25%, win rate: 42%, avg hold: 392h

#### Top 5 Trades
| Entry | Exit | Dir | PnL | Hold | Exit Reason |
|-------|------|-----|-----|------|-------------|
| 2020-10-09 | 2021-04-21 | LONG | +258.14% | 4649h | ema_cross |
| 2024-10-11 | 2024-12-28 | LONG | +33.13% | 1855h | ema_cross |
| 2024-02-01 | 2024-04-15 | LONG | +30.79% | 1772h | ema_cross |
| 2023-09-29 | 2024-01-18 | LONG | +16.39% | 2658h | ema_cross |
| 2020-04-29 | 2020-06-26 | LONG | +11.41% | 1400h | ema_cross |

#### Bottom 5 Trades
| Entry | Exit | Dir | PnL | Hold | Exit Reason |
|-------|------|-----|-----|------|-------------|
| 2026-01-18 | 2026-01-21 | LONG | -4.68% | 72h | regime_change |
| 2024-07-24 | 2024-08-04 | LONG | -5.67% | 267h | ema_cross |
| 2021-07-16 | 2021-07-26 | SHORT | -6.62% | 251h | ema_cross |
| 2021-09-16 | 2021-09-20 | LONG | -6.78% | 108h | ema_cross |
| 2022-10-26 | 2022-11-09 | LONG | -12.08% | 340h | ema_cross |

---

## Comparison with R152 (ATR trailing stop)

| Metric | R152 (ATR stop) | R152b-A (Long only) | R152b-B (L/S) |
|--------|----------------|--------------------|--------------| 
| Exit mechanism | ATR(14) trailing stop | Regime change + EMA cross | Regime change + EMA cross |
| Annual Return (1x) | -1.9% | +26.3% | +26.7% |
| Sharpe | ~0.0 | 0.65 | 0.66 |
| Max DD | ~-5% | -49.0% | -49.1% |
| Calmar | ~-0.4 | 0.54 | 0.54 |
| Trades | ~200+ | 42 | 75 |
| Avg Hold | ~12h | 646h (27d) | 534h (22d) |
| Win Rate | ~35% | 48% | 45% |
| Problem | Stop too tight for weekly EMA signal | -49% DD from riding 2021 bull/crash | Short side marginal |

## Notes
- **Key insight**: R152's ATR(14) trailing stop on hourly bars was ~1.3%, causing immediate stop-outs on a signal designed to capture weekly trends.
- **This fix**: By removing the trailing stop entirely and exiting ONLY on regime change or EMA cross reversal, the strategy can ride trends for days/weeks without being shaken out by hourly noise.
- V4 regime detection is CAUSAL: np.roll(regimes, 1) shifts daily regime by 1 day
- Regime uses expanding (causal) percentiles for volatility thresholds
- Hourly EMA(168) ~ 1 week, EMA(720) ~ 1 month
- First 720 bars skipped for EMA warm-up
- Funding applied per-hour: longs pay positive funding, shorts collect
- Single position at a time (no pyramiding)
- Position size dynamically adjusts when regime changes (e.g., UPTREND 70% -> RANGE 30%)
- Costs: 7 bps per side applied ONLY on entry and exit (not every hour)
