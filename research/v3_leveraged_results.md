# V3 Leveraged Futures Test — Results

**Generated:** 2026-03-24 12:45:10

## Context

V3 is a validated BTC-only momentum strategy: 20/50 EMA crossover + positioning
overlay + VRP overlay. This test evaluates running V3 on perpetual futures with
1x, 2x, and 3x leverage, including actual Binance funding rate costs.

**Spot V3 Baseline (OOS):** Sharpe 0.56, Return +17.52%, MaxDD -20.2%

## Data

- **IS Period:** 2020-03-31 to 2024-06-02
- **OOS Period:** 2024-06-02 to 2026-03-17
- **Funding Rates:** Actual Binance BTC funding (8h settlement)
- **Mean Funding (ann):** 12.46%
- **Trading Cost:** 10 bps per trade
- **Warmup:** 90 days
- **Rebalance:** Weekly (168 bars)

## OOS Performance Metrics

| Metric | 1x | 2x | 3x |
|--------|---:|---:|---:|
| Annualized Return | 10.18% | 11.39% | 3.31% |
| Total Return | 18.93% | 21.26% | 5.99% |
| Sharpe Ratio | 0.472 | 0.470 | 0.471 |
| Sortino Ratio | 0.523 | 0.535 | 0.550 |
| Max Drawdown | -37.39% | -62.41% | -78.36% |
| Calmar Ratio | 0.272 | 0.182 | 0.042 |
| Win Rate | 25.7 | 25.6 | 25.3 |
| Time in Market | 51.7 | 51.7 | 51.7 |
| Trades | 44 | 44 | 44 |
| OOS Days | 653 | 653 | 653 |

## Cost Analysis (OOS)

| Metric | 1x | 2x | 3x |
|--------|---:|---:|---:|
| Funding Drag (ann) | 0.0322 | 0.0645 | 0.0967 |
| Trading Drag (ann) | 0.0169 | 0.0338 | 0.0507 |
| Funding % of Gross | 17.0 | 17.0 | 17.0 |
| Total Gross Return | 0.3388 | 0.6777 | 1.0165 |
| Total Funding Cost | 0.0577 | 0.1153 | 0.1730 |
| Total Trading Cost | 0.0302 | 0.0604 | 0.0907 |

## Liquidation Risk (OOS)

| Metric | 1x | 2x | 3x |
|--------|---:|---:|---:|
| Liquidation Events | 0 | 0 | 63 |
| Near-Liquidation Events | 0 | 9 | 253 |
| Liquidation Threshold | 100% | 50% | 33% |
| Near-Liq Threshold (80%) | 80% | 40% | 27% |

## Leverage Efficiency (OOS)

Risk-adjusted leverage efficiency measures how well Sharpe scales with leverage.
Efficiency = (Sharpe_Lx / Sharpe_1x) / L. Perfect scaling = 100%.

| Leverage | Sharpe | Ratio vs 1x | Expected | Efficiency |
|----------|--------|-------------|----------|------------|
| 1x | 0.472 | 1.000 | 1.0 | 100.0% |
| 2x | 0.470 | 0.997 | 2.0 | 49.9% |
| 3x | 0.471 | 0.999 | 3.0 | 33.3% |

## IS Performance Metrics (Reference)

| Metric | 1x | 2x | 3x |
|--------|---:|---:|---:|
| Annualized Return | 42.18% | 62.26% | 48.42% |
| Sharpe Ratio | 0.982 | 0.979 | 0.978 |
| Max Drawdown | -60.26% | -86.74% | -97.16% |
| Calmar Ratio | 0.700 | 0.718 | 0.498 |
| Liquidation Events | 0 | 0 | 586 |

## Kill Criteria Assessment (OOS)

| Criterion | Threshold | 1x | 2x | 3x |
|-----------|-----------|---:|---:|---:|
| MaxDD > 40% | -40% | PASS (-37.4%) | KILL (-62.4%) | KILL (-78.4%) |
| Multiple Liquidations | >1 | PASS (0) | PASS (0) | KILL (63) |
| Sharpe < 0.3 | 0.3 | PASS (0.47) | PASS (0.47) | PASS (0.47) |
| Funding > 50% Gross | 50% | PASS (17%) | PASS (17%) | PASS (17%) |

## Verdict

### 1x Leverage: PASS

- Sharpe: 0.472
- Annualized Return: 10.18%
- Max Drawdown: -37.39%
- Calmar: 0.272
- Funding Drag: 17.0% of gross

### 2x Leverage: KILL

- MaxDD -62.4% exceeds -40% threshold

### 3x Leverage: KILL

- MaxDD -78.4% exceeds -40% threshold
- 63 liquidation events (threshold: >1)

### Recommendation

**1x leverage** is the recommended level based on risk-adjusted returns.
It achieves Sharpe 0.472 with MaxDD -37.39% and
Calmar 0.272. Funding costs consume 17.0%
of gross returns, which is acceptable.

### Funding Rate Notes

- Actual Binance BTC funding rates were used (mean: 12.46% annualized)
- Funding is charged every 8 hours on the leveraged notional when in a long position
- The 2020-2026 period includes both high-funding bull markets and negative-funding corrections
- Funding costs scale linearly with leverage (2x leverage = 2x funding cost)
