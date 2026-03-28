# Quantitative Trading Methodology Reference

> **Audience:** Both `/strategy` (research/simulation) and `/dev` (implementation).
> **When to read:** Before evaluating any signal, building any backtest, or sizing any position.
> **Complements:** `RESEARCHER_BEST_PRACTICES.md` (engine parameters), `KRAKEN_FEES.md` (fee specifics).

---

## 1. Backtesting Methodology

### Principles
- Walk-forward validation is mandatory. Single train/test splits are storytelling, not backtesting.
- Compute Probability of Backtest Overfitting (PBO) for every strategy. PBO >0.50 = more likely overfit than not.
- CPCV (Combinatorial Purged Cross-Validation) produces a distribution of OOS results, not a single number.
- Eliminate look-ahead bias with strict information-set discipline: never use data unavailable at decision time.
- Guard against survivorship bias — test on delisted/dead coins, not just current survivors.

### Thresholds

| Metric | Minimum | Target |
|--------|---------|--------|
| Backtest trades | 200 | 500+ across multiple regimes |
| Walk-Forward Efficiency (WFE) | 50% | >70% |
| PBO | <0.50 | <0.30 |
| Data span | 1 year | 2-3 complete market cycles |
| Max strategy variations (5yr daily) | ~45 before false discovery dominates | — |

### Common Mistakes
- **Meta-overfitting:** Tuning walk-forward window sizes until WF results look good defeats the purpose.
- **Parameter cliff blindness:** Vary parameters +/-20%. Robust strategies show a plateau; overfit ones show cliffs.
- **Trial inflation:** Running hundreds of backtest variations without tracking count. Apply DSR or FDR correction.

---

## 2. Signal Evaluation

### Metrics

| Metric | What It Measures | Good | Excellent | Suspect |
|--------|-----------------|------|-----------|---------|
| Rank IC (Spearman) | Predictive accuracy | >0.03 | >0.05 | — |
| ICIR (mean IC / std IC) | Signal stability | >0.30 | 0.40-0.60 | — |
| Profit Factor | Win dollars / loss dollars | >1.3 | 1.5-2.0 | >4.0 |
| Hit Rate | Win percentage | context-dependent | — | >80% with low PF |
| Sharpe Ratio | Risk-adjusted return | >0.5 | 1.0-2.0 | >3.0 (likely overfit) |
| Deflated Sharpe (DSR) | Sharpe corrected for trials | 95% confidence | — | — |

### Multiple Testing Correction
- Use **Benjamini-Hochberg (FDR)** when screening many signals — controls false discovery proportion.
- Bonferroni is overly conservative for exploratory research; use only for final confirmation.
- Track total number of strategies/signals tested across ALL sessions. This number only goes up.

### Hit Rate vs Profit Factor (coupled system)
- 40% win rate with 3:1 payoff (PF=2.0) beats 70% win rate with 0.5:1 payoff (PF=1.17).
- Always evaluate expectancy: `(win_rate × avg_win) - (loss_rate × avg_loss)`.
- Break-even win rates: 33% at 2:1 R:R, 25% at 3:1 R:R, 50% at 1:1 R:R.

### Signal Decay
- IC measured over the full backtest may mask a signal that only worked in 2020-2021.
- Always test post-ETF (Jan 2024+) as the primary period. Full-period is supplementary.
- Rule 13 from our codebase: last 12 months is the primary metric.

---

## 3. Position Sizing

### Principles
- **Fractional Kelly (25-50%):** Full Kelly maximizes growth but produces extreme drawdowns. Half-Kelly captures ~75% of growth with ~50% less drawdown.
- **Volatility-targeting:** Size inversely proportional to realized vol. Maintains constant dollar-risk per position.
- **ADV cap (1-5%):** Orders >5% of ADV move the market. Square-root impact law: 2x volume = ~1.4x slippage.
- **Risk parity across strategies:** Equalize each strategy's contribution to portfolio variance.
- **Recalculate monthly:** Edge estimates, win rates, and vol regimes shift.

### Thresholds

| Parameter | Guideline |
|-----------|-----------|
| Fractional Kelly | 25-50% of full Kelly |
| Max single position vs ADV | 1-5% daily |
| Max portfolio drawdown tolerance | 25% |
| Max consecutive losers to budget (40% WR) | 9-12 |

### Our Implementation
- Kelly formula: `edge / (vol² + edge²) × vol_scale` (see `v4/sizing.py`)
- ADV curve: logarithmic scaling with floor/range (see `RESEARCHER_BEST_PRACTICES.md` Layer 1)
- Concentration limit: 10% of portfolio equity per token (see `v4/simulator.py`)

---

## 4. Fee and Cost Modeling

### Three Layers (all must be modeled)

| Layer | Description | Typical Range (Crypto) |
|-------|-------------|----------------------|
| **Exchange fees** | Maker/taker per trade | Maker: 0.01-0.02%, Taker: 0.04-0.06% |
| **Funding rates** | Perpetual futures holding cost | Neutral: 0.01%/8h, Bullish: 0.03-0.10%/8h |
| **Slippage/impact** | Market impact of order execution | Liquid: 0.01-0.05%, Stress: 0.5-3.0% |

### Key Facts
- Funding dominates for hold periods >1 day. One day of funding (0.09%) > two taker fills (0.08%).
- Square-root impact model: `impact ∝ sqrt(order_size / ADV)`. Validated on 1M+ BTC metaorders.
- A strategy with 0.03% edge/trade is profitable on maker but underwater on taker.
- Annualized funding carry: ~10-20% APY in bull markets, ~2-5% in neutral.

### Our Fee Model
- See `KRAKEN_FEES.md` for tier-specific rates (Kraken $200K AUM: 0.08-0.12% maker, 0.18-0.22% taker).
- Slippage: `base_spread_bps + sqrt(impact) + cap` (see `v4/sizing.py:94`).
- Funding: charged per-bar in simulator but NOT in sizing cost estimate (known gap).

---

## 5. Regime Detection

### Principles
- A trend-following strategy in a range-bound regime will get chopped. Regime awareness is mandatory.
- 2-3 states is the sweet spot. More than 4 states risks overfitting.
- Threshold-based methods (MA cross, vol percentile) often competitive with HMMs. Use as baselines.
- Regime labels work best as features/filters, not as direct trading signals.
- Use regime probabilities to scale exposure gradually — binary switching causes whipsaw.

### Our Implementation
- 5 regimes: CRISIS, QUIET, UPTREND, RANGE, DOWNTREND (see `v4/regime_analysis.py`)
- Expanding percentile thresholds with 60-day minimum
- Regime used for: strategy selection, position sizing multiplier, exit handler selection

### Thresholds

| Parameter | Guideline |
|-----------|-----------|
| Number of states | 2-3 (max 4 for HMM) |
| Detection lag (daily data) | 5-20 days — budget for this in risk management |
| Retrain frequency (if using HMM) | Monthly to quarterly |
| Walk-forward regime training | Mandatory — full-sample HMM = look-ahead bias |

---

## 6. Crypto-Specific Considerations

### Structural Facts
- Funding rates are positive >92% of the time (structural +0.01% interest component).
- Post-ETF: institutional capital creates a ceiling on funding rates AND a new systemic risk (ETF outflows).
- Perpetual-spot basis averages 5-10% annualized — exploitable but requires careful management.
- All 6 current Tier A strategies are momentum variants (median correlation +0.62).

### Tail Risks
- **Liquidation cascades:** Sept 2025 saw $16.7B liquidated in 24h. Order book depth shrank >90%.
- **Exchange failures:** Frozen interfaces, ADL, oracle manipulation all caused losses in 2025.
- **Stablecoin depeg:** USDe traded at $0.65 on Binance during Nov 2025 cascade.
- **Funding rate flips:** Strategies relying on positive funding can suddenly start paying.

### Rules
- Stress-test with 10x normal slippage for cascade scenarios.
- Diversify across 2-3+ venues. Maintain withdrawal capability.
- Max leverage: 2-5x for systematic strategies (125x available but suicidal).
- Exchange-reported volume is unreliable — use normalized volume for ADV calculations.
- Funding payment frequency differs by exchange: Hyperliquid hourly, Binance 8h.

---

## 7. Paper Trading Validation

### Principles
- Paper trading always overstates real performance. Model slippage dynamically, not as a flat constant.
- Require 200+ trades spanning 2-3 market regimes before trusting results.
- Validate with t-test on per-trade returns. 65% WR on 20 trades = noise; on 200 trades = real.
- Monte Carlo resampling: 95% of resampled equity curves profitable = robust edge.

### Thresholds

| Metric | Threshold |
|--------|-----------|
| Minimum paper trades | 200 (across multiple regimes) |
| Max order size vs ADV | 5% (low-touch), 1-3% (institutional) |
| Monte Carlo confidence | 95% of resampled curves profitable |
| Backtest-to-live decay | Expect 40-60% of backtest performance |
| Kill threshold | Returns <60% of backtest |

### Our Implementation
- Paper engine: `v4/paper_engine.py` with real-time WebSocket prices
- Sub-hourly exit resolution: 5m/15m/30m via per-strategy `exit_resolution`
- State restoration: `state.json` polling every 1s
- Circuit breakers: -3% daily halt, -15% pull

---

## Quick Decision Tree

```
New signal idea?
  → Check IC >0.03, t-stat >2.0, 200+ trades
  → Apply DSR correction for total trials tested
  → Test post-ETF (Jan 2024+) as primary window
  → Cost-adjust: subtract fees + funding + impact

Sizing a position?
  → Fractional Kelly (25-50%)
  → Cap at 5% of ADV
  → Vol-target to normalize risk
  → Concentration limit: 10% of equity per token

Evaluating a backtest?
  → Walk-forward + CPCV dual-gate
  → PBO <0.50
  → Parameter sensitivity +/-20%
  → Sharpe >3.0 = suspect, investigate before deploying

Going to paper?
  → Need 200+ trades minimum
  → Budget for 40-60% backtest decay
  → Kill if returns <60% of backtest
  → Stress-test with 10x slippage
```
