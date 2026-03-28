# R151: Honest Carry Portfolio — Funding Rate Carry Results

**Date:** 2026-03-28

## Hypothesis

Short high-funding tokens to collect carry. This is a PORTFOLIO strategy
that ranks ALL tokens by funding rate and shorts the top N while longing
the bottom N as a hedge. The carry should provide steady income regardless
of market direction.

## Summary Across Variants

| Variant | Ann Ret | Ann Ret (12M) | Sharpe | Sharpe (12M) | Calmar | MaxDD | Trades | Funding Inc | Price PnL |
|---------|---------|---------------|--------|-------------|--------|-------|--------|------------|----------|
| A. Base (5+5, 168h) | -0.35% | 15.91% | -0.009 | 0.315 | -0.005 | -66.45% | 2792 | 1.1734 | -0.9924 |
| B. Wider (10+10, 168h) | -5.79% | 24.85% | -0.125 | 0.424 | -0.074 | -78.79% | 4760 | 1.1457 | -1.0903 |
| C. Fast (5+5, 72h) | 17.94% | 96.31% | 0.451 | 1.699 | 0.252 | -71.19% | 5488 | 2.4839 | -0.8365 |
| D. Quality (5+5, 168h, ADV>200M) | 13.01% | 2.12% | 0.421 | 0.064 | 0.215 | -60.45% | 2874 | 1.2752 | 0.1025 |

## Variant A. Base (5+5, 168h)

- **Positions per side:** 5
- **Rebalance:** every 168h
- **ADV threshold:** $50M
- **Eligible tokens:** 111
- **Period:** 2021-10-10 to 2026-03-14 (4.4 years)

| Metric | Value |
|--------|-------|
| Total Return | -1.53% |
| Annual Return (full) | -0.35% |
| Annual Return (last 12M) | 15.91% |
| Annual Volatility | 37.08% |
| Sharpe (full) | -0.009 |
| Sharpe (last 12M) | 0.315 |
| Calmar | -0.005 |
| Max Drawdown | -66.45% |
| Trade Count | 2792 |
| Funding Income | 1.1734 |
| Price PnL | -0.9924 |
| Total Fees | 0.1962 |

### Top 5 Contributors

| Token | Total PnL | Funding | Price |
|-------|-----------|---------|-------|
| GALA | +0.3279 | +0.0101 | +0.3177 |
| SOL | +0.2780 | +0.0139 | +0.2641 |
| IMX | +0.2035 | +0.0095 | +0.1940 |
| XPL | +0.1845 | +0.0013 | +0.1832 |
| TRX | +0.1714 | +0.0113 | +0.1600 |

### Bottom 5 Detractors

| Token | Total PnL | Funding | Price |
|-------|-----------|---------|-------|
| WIF | -0.1461 | +0.0194 | -0.1655 |
| ETC | -0.1466 | +0.0092 | -0.1558 |
| AVNT | -0.1849 | +0.0156 | -0.2005 |
| TIA | -0.2262 | +0.0221 | -0.2483 |
| CFX | -0.2700 | +0.0054 | -0.2754 |

## Variant B. Wider (10+10, 168h)

- **Positions per side:** 10
- **Rebalance:** every 168h
- **ADV threshold:** $50M
- **Eligible tokens:** 111
- **Period:** 2021-10-10 to 2026-03-14 (4.4 years)

| Metric | Value |
|--------|-------|
| Total Return | -23.22% |
| Annual Return (full) | -5.79% |
| Annual Return (last 12M) | 24.85% |
| Annual Volatility | 46.20% |
| Sharpe (full) | -0.125 |
| Sharpe (last 12M) | 0.424 |
| Calmar | -0.074 |
| Max Drawdown | -78.79% |
| Trade Count | 4760 |
| Funding Income | 1.1457 |
| Price PnL | -1.0903 |
| Total Fees | 0.2871 |

### Top 5 Contributors

| Token | Total PnL | Funding | Price |
|-------|-----------|---------|-------|
| GALA | +0.5298 | +0.0122 | +0.5176 |
| SOL | +0.2770 | +0.0106 | +0.2664 |
| CRV | +0.2340 | +0.0099 | +0.2241 |
| IMX | +0.1863 | +0.0063 | +0.1800 |
| BCH | +0.1475 | +0.0158 | +0.1317 |

### Bottom 5 Detractors

| Token | Total PnL | Funding | Price |
|-------|-----------|---------|-------|
| TIA | -0.1685 | +0.0158 | -0.1844 |
| AVNT | -0.1703 | +0.0165 | -0.1867 |
| AVAX | -0.1815 | +0.0101 | -0.1916 |
| OP | -0.2438 | +0.0090 | -0.2527 |
| SAND | -0.3710 | +0.0106 | -0.3816 |

## Variant C. Fast (5+5, 72h)

- **Positions per side:** 5
- **Rebalance:** every 72h
- **ADV threshold:** $50M
- **Eligible tokens:** 111
- **Period:** 2021-10-10 to 2026-03-14 (4.4 years)

| Metric | Value |
|--------|-------|
| Total Return | 107.57% |
| Annual Return (full) | 17.94% |
| Annual Return (last 12M) | 96.31% |
| Annual Volatility | 39.76% |
| Sharpe (full) | 0.451 |
| Sharpe (last 12M) | 1.699 |
| Calmar | 0.252 |
| Max Drawdown | -71.19% |
| Trade Count | 5488 |
| Funding Income | 2.4839 |
| Price PnL | -0.8365 |
| Total Fees | 0.5713 |

### Top 5 Contributors

| Token | Total PnL | Funding | Price |
|-------|-----------|---------|-------|
| M | +0.6754 | +0.2294 | +0.4459 |
| AVNT | +0.3246 | +0.0762 | +0.2484 |
| FARTCOIN | +0.3228 | +0.0213 | +0.3016 |
| GALA | +0.3041 | +0.0148 | +0.2893 |
| SOL | +0.2980 | +0.0246 | +0.2734 |

### Bottom 5 Detractors

| Token | Total PnL | Funding | Price |
|-------|-----------|---------|-------|
| TIA | -0.2010 | +0.0257 | -0.2267 |
| ARC | -0.2278 | +0.0276 | -0.2553 |
| WIF | -0.2437 | +0.0283 | -0.2720 |
| MYX | -0.3339 | +0.4135 | -0.7474 |
| PIPPIN | -0.3395 | +0.0756 | -0.4151 |

## Variant D. Quality (5+5, 168h, ADV>200M)

- **Positions per side:** 5
- **Rebalance:** every 168h
- **ADV threshold:** $200M
- **Eligible tokens:** 68
- **Period:** 2020-11-14 to 2026-03-14 (5.3 years)

| Metric | Value |
|--------|-------|
| Total Return | 91.89% |
| Annual Return (full) | 13.01% |
| Annual Return (last 12M) | 2.12% |
| Annual Volatility | 30.90% |
| Sharpe (full) | 0.421 |
| Sharpe (last 12M) | 0.064 |
| Calmar | 0.215 |
| Max Drawdown | -60.45% |
| Trade Count | 2874 |
| Funding Income | 1.2752 |
| Price PnL | 0.1025 |
| Total Fees | 0.4584 |

### Top 5 Contributors

| Token | Total PnL | Funding | Price |
|-------|-----------|---------|-------|
| SOL | +0.8628 | +0.0037 | +0.8591 |
| GALA | +0.7394 | +0.0185 | +0.7209 |
| ZEC | +0.6394 | +0.0154 | +0.6240 |
| DYDX | +0.4137 | +0.0240 | +0.3897 |
| ETC | +0.3785 | +0.0273 | +0.3512 |

### Bottom 5 Detractors

| Token | Total PnL | Funding | Price |
|-------|-----------|---------|-------|
| FET | -0.2689 | +0.0151 | -0.2840 |
| AVAX | -0.2706 | +0.0259 | -0.2965 |
| OP | -0.4503 | +0.0189 | -0.4692 |
| SAND | -0.5320 | +0.0261 | -0.5581 |
| TIA | -0.5902 | +0.0448 | -0.6350 |

## Conclusions

1. **Best variant:** C. Fast (5+5, 72h) with Sharpe 0.451, Ann Return 17.94%
2. **A. Base (5+5, 168h)** is funding-driven: funding=1.1734, price=-0.9924
2. **B. Wider (10+10, 168h)** is funding-driven: funding=1.1457, price=-1.0903
2. **C. Fast (5+5, 72h)** is funding-driven: funding=2.4839, price=-0.8365
2. **D. Quality (5+5, 168h, ADV>200M)** is funding-driven: funding=1.2752, price=0.1025

**VERDICT: MARGINAL.** Best Sharpe is 0.451 — positive but not compelling after fees. Needs refinement (signal quality, timing).