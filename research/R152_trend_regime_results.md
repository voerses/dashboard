# R152 -- BTC Trend-Following with V4 Regime Filter

## Strategy
- **Entry signal**: Hourly EMA(168)/EMA(720) cross (bullish cross = long, bearish cross = short)
- **Regime filter**: V4 daily regime (EMA 20d/50d + ADX + vol), shifted by 1 day for causality (np.roll)
- **Position sizing**: UPTREND=70%, RANGE/QUIET=30%, DOWNTREND/CRISIS=0%
- **Exit**: 1.5x ATR(14) trailing stop, 0.5x ATR breakeven ratchet
- **Costs**: 7 bps per side (4 taker + 3 slippage) + hourly funding
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

## Results by Leverage

Last 12 months cutoff: 2025-03-28

### Full Period

| Leverage | Ann.Return | Sharpe | Sortino | MaxDD | Calmar | Trades | WinRate | PF |
|----------|-----------|--------|---------|-------|--------|--------|---------|-----|
| 1x | -1.9% | -1.56 | -0.06 | -11.0% | -0.17 | 59 | 5% | 0.05 |
| 2x | -3.9% | -1.57 | -0.06 | -22.0% | -0.18 | 59 | 5% | 0.05 |
| 3x | -6.2% | -1.59 | -0.06 | -33.0% | -0.19 | 59 | 5% | 0.05 |

### Last 12 Months (2025-03-28 to 2026-03-28)

| Leverage | Ann.Return | Sharpe | Sortino | MaxDD | Calmar | Trades | WinRate | PF |
|----------|-----------|--------|---------|-------|--------|--------|---------|-----|
| 1x | -0.9% | -2.66 | -0.15 | -0.9% | -1.01 | 12 | 8% | 0.02 |
| 2x | -2.1% | -2.67 | -0.16 | -2.0% | -1.01 | 12 | 8% | 0.02 |
| 3x | -3.6% | -2.67 | -0.16 | -3.5% | -1.01 | 12 | 8% | 0.02 |

## Monthly Returns (1x Leverage)

| Year | Jan | Feb | Mar | Apr | May | Jun | Jul | Aug | Sep | Oct | Nov | Dec | YTD |
|------|------|------|------|------|------|------|------|------|------|------|------|------|------|
| 2020 | - | -0.5% | +0.0% | -0.6% | +0.0% | -0.1% | +0.4% | +0.0% | -0.1% | -0.0% | +0.0% | +0.0% | -0.9% |
| 2021 | +0.0% | +0.0% | +0.0% | -1.2% | -0.2% | +0.0% | +0.0% | +0.0% | -1.2% | +0.0% | -0.0% | +0.0% | -2.6% |
| 2022 | +0.0% | +0.0% | -0.0% | -0.1% | +0.0% | +0.0% | +0.0% | +0.0% | +0.0% | -0.4% | -0.1% | -0.1% | -0.7% |
| 2023 | -0.2% | +0.0% | -1.8% | -1.1% | -0.0% | +0.0% | -0.2% | +0.0% | -0.1% | +0.0% | +0.0% | +0.0% | -3.4% |
| 2024 | -0.0% | -0.1% | +0.0% | -0.1% | +0.0% | -0.0% | +0.0% | -0.1% | -0.4% | -1.0% | +0.0% | -0.6% | -2.5% |
| 2025 | -0.6% | +0.2% | +0.0% | +0.0% | +0.0% | -0.1% | +0.0% | -0.4% | -0.1% | -0.2% | +0.0% | +0.0% | -1.3% |
| 2026 | -0.0% | +0.0% | -0.0% | - | - | - | - | - | - | - | - | - | -0.1% |

## Monthly Returns (2x Leverage)

| Year | Jan | Feb | Mar | Apr | May | Jun | Jul | Aug | Sep | Oct | Nov | Dec | YTD |
|------|------|------|------|------|------|------|------|------|------|------|------|------|------|
| 2020 | - | -1.0% | +0.0% | -1.1% | +0.0% | -0.2% | +0.8% | +0.0% | -0.2% | -0.1% | +0.0% | +0.0% | -1.8% |
| 2021 | +0.0% | +0.0% | +0.0% | -2.4% | -0.5% | +0.0% | +0.0% | +0.0% | -2.4% | +0.0% | -0.1% | +0.0% | -5.3% |
| 2022 | +0.0% | +0.0% | -0.1% | -0.2% | +0.0% | +0.0% | +0.0% | +0.0% | +0.0% | -0.8% | -0.2% | -0.2% | -1.5% |
| 2023 | -0.4% | +0.0% | -3.7% | -2.4% | -0.1% | +0.0% | -0.5% | +0.0% | -0.3% | +0.0% | +0.0% | +0.0% | -7.3% |
| 2024 | -0.1% | -0.2% | +0.0% | -0.2% | +0.0% | -0.1% | +0.0% | -0.3% | -0.9% | -2.2% | +0.0% | -1.4% | -5.5% |
| 2025 | -1.4% | +0.4% | +0.0% | +0.0% | +0.0% | -0.2% | +0.0% | -0.9% | -0.2% | -0.5% | +0.0% | +0.0% | -2.9% |
| 2026 | -0.1% | +0.0% | -0.1% | - | - | - | - | - | - | - | - | - | -0.2% |

## Trade Analysis (1x Leverage)

- Total trades: 59
- Long trades: 28
- Short trades: 31
- Long avg PnL: -0.17%
- Long win rate: 7%
- Short avg PnL: -0.20%
- Short win rate: 3%
- Avg holding period: 4 hours (0.2 days)

### Top 5 Trades
| Entry | Exit | Dir | PnL | Bars |
|-------|------|-----|-----|------|
| 2020-07-22 | 2020-07-22 | LONG | +0.38% | 2 |
| 2025-02-04 | 2025-02-04 | SHORT | +0.15% | 1 |
| 2025-04-18 | 2025-04-19 | LONG | +0.01% | 37 |
| 2024-06-15 | 2024-06-15 | SHORT | -0.04% | 13 |
| 2021-05-04 | 2021-05-04 | SHORT | -0.04% | 1 |

### Bottom 5 Trades
| Entry | Exit | Dir | PnL | Bars |
|-------|------|-----|-----|------|
| 2024-10-11 | 2024-10-11 | SHORT | -0.83% | 5 |
| 2021-09-12 | 2021-09-12 | SHORT | -0.94% | 2 |
| 2023-04-26 | 2023-04-26 | LONG | -0.96% | 2 |
| 2021-04-21 | 2021-04-21 | SHORT | -1.18% | 1 |
| 2023-03-15 | 2023-03-15 | LONG | -1.55% | 7 |

## Notes
- V4 regime detection is CAUSAL: np.roll(regimes, 1) shifts daily regime by 1 day
- Regime uses expanding (causal) percentiles for volatility thresholds
- Hourly EMA(168) ~ 1 week, EMA(720) ~ 1 month
- First 720 bars skipped for EMA warm-up
- Funding applied per-hour: longs pay positive funding, shorts collect
- Single position at a time (no pyramiding)
- Position is force-closed when regime transitions to DOWNTREND/CRISIS
- Comparison: R150 (no regime filter) got -6.3% annual at 1x
