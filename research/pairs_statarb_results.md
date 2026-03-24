# Pairs Trading / Statistical Arbitrage Results

*Generated: 2026-03-24*

**Verdict: 0 / 11 variants passed kill criteria. All pairs/stat-arb strategies KILLED.**

## Kill Criteria

| Criterion | Threshold |
|-----------|-----------|
| Sharpe Ratio | >= 0.3 |
| Max Drawdown | <= 20% |
| BTC Correlation | <= 0.3 (absolute) |
| Min Trades | >= 30 |
| Win Rate | >= 45% |

## All Results

| Strategy | Sharpe | Return% | MaxDD% | Trades | WR% | Avg PnL% | PF | BTC Corr | Status |
|----------|--------|---------|--------|--------|-----|----------|-----|----------|--------|
| A: ETH/BTC | -4.513 | -61.38 | -61.80 | 107 | 9.3 | -0.6718 | 0.157 | 0.067 | KILLED |
| A: SOL/ETH | -1.125 | -24.29 | -42.45 | 85 | 24.7 | -0.0988 | 0.843 | 0.039 | KILLED |
| A: BNB/BTC | -1.331 | -25.39 | -31.42 | 95 | 21.1 | -0.1028 | 0.674 | -0.006 | KILLED |
| A: DOGE/XRP | -1.087 | -42.13 | -51.44 | 223 | 22.9 | -0.0165 | 0.965 | -0.004 | KILLED |
| B: BTC basis (10bps) | -1.718 | -1.87 | -2.08 | 7 | 14.3 | -0.098 | 0.027 | -0.040 | KILLED |
| B: BTC basis (20bps) | -0.883 | -0.71 | -0.93 | 4 | 50.0 | -0.0291 | 0.168 | -0.005 | KILLED |
| B: BTC basis (50bps) | 0.000 | 0.00 | 0.00 | 0 | 0.0 | 0 | 0 | N/A | KILLED |
| B: ETH basis (10bps) | -1.399 | -2.27 | -2.59 | 8 | 12.5 | -0.1117 | 0.153 | 0.021 | KILLED |
| B: ETH basis (20bps) | -0.096 | -0.08 | -0.50 | 3 | 66.7 | 0.1062 | 4.117 | 0.022 | KILLED |
| B: ETH basis (50bps) | 0.047 | 0.03 | -0.20 | 2 | 100.0 | 0.2135 | inf | 0.012 | KILLED |
| C: XS Mom L3/S3 | -0.235 | -30.33 | -58.57 | 91 | 46.2 | -0.1769 | 0.927 | -0.063 | KILLED |

## OOS Period

All strategies: 2024-05-26 to 2026-03-14 (~22 months). IS/OOS split: 70/30 with 90-day warmup.

---

## Strategy A: Spot-Spot Pairs (Cointegration)

### Method
- spread = log(price_A) - beta * log(price_B), beta from 60d rolling OLS
- Z-score spread with 30d rolling window
- Entry: |z| > 2, Exit: z crosses 0, Stop: |z| > 4
- Max hold: 168 bars (1 week), costs: 10 bps per leg

### Results
All 4 pairs KILLED. Sharpe range: -4.5 to -1.1. Win rates: 9-23%.

### Root Cause Analysis

**The core problem: crypto pairs do NOT mean-revert at hourly frequency.**

Diagnostic on ETH/BTC (representative):
- OOS z-score: mean=-0.34, std=1.61, range [-5.7, +4.0]
- **Only 17 zero-crossings in 15,595 OOS bars** (1 crossing every ~38 days)
- The spread crosses zero roughly once per month, but the max hold is 1 week
- Trade outcome analysis: of 3,356 potential entry points:
  - 192 (5.7%) reverted to z=0 within 168 bars (win)
  - 127 (3.8%) hit the z=4 stop (loss)
  - 3,037 (90.5%) timed out at max hold (mostly losers due to transaction costs)

**Why this fails in crypto:**
1. Crypto "pairs" exhibit trending co-movement, not mean-reverting spreads. When ETH underperforms BTC, it tends to keep underperforming for months (structural shifts: ETH losing DeFi narrative, BTC ETF inflows).
2. Rolling beta is extremely unstable (range [-1.7, 4.2] for ETH/BTC), meaning the "spread" definition itself shifts constantly.
3. Transaction costs (40 bps round-trip for both legs) are lethal when 90% of trades time out without profit.
4. This is NOT a cointegration failure (no cointegration exists to fail) -- it is a structural feature of crypto markets. These assets are NOT driven by the same cash flows or fundamentals the way equity pairs are.

### Market Neutrality (Silver Lining)
All pairs showed BTC correlation < 0.07. The strategy IS market-neutral, it just loses money in a market-neutral way.

---

## Strategy B: Spot-Perp Basis Trade (Cash & Carry)

### Method
- basis = (perp_price - spot_price) / spot_price
- Entry: |basis| > threshold. Exit: basis reverts to ~0.
- Funding rate accrual included (8h rate / 8 for hourly)
- Max hold: 2016 bars (12 weeks)

### Results
All 6 variants KILLED. Primary kill: insufficient trades (0-8 trades in OOS).

### Root Cause Analysis

**The basis has compressed to near-zero in the OOS period.**

BTC basis by period:
| Year | Mean (bps) | Std (bps) | Max (bps) |
|------|-----------|-----------|-----------|
| 2020 | +1.3 | 8.4 | 63 |
| 2021 | +4.5 | 8.0 | 210 |
| 2022 | -4.3 | 1.9 | 32 |
| 2023 | -2.4 | 4.4 | 25 |
| 2024 | -0.3 | 5.2 | 29 |
| 2025 | -4.3 | 1.2 | 6 |
| 2026 | -4.8 | 1.4 | -2 |

Key findings:
- In 2021, basis regularly exceeded 20bps (bull market premium on perps). This is when the strategy WOULD have worked.
- By 2024-2026 (our OOS period), the basis compresses to mean -3.5bps with std of only 3bps. The 10bps threshold is almost never reached.
- At 50bps threshold: zero trades in OOS. The basis literally never gets that wide anymore.
- **Structural explanation**: the basis arbitrage has been arbed away. Market makers and automated basis traders now keep the perp-spot basis extremely tight on BTC and ETH. This is a crowded trade.

**The strategy worked in 2020-2021 but is now dead alpha.**

Even the loosest threshold (10bps) generated only 7-8 trades, and with transaction costs eating into the tiny basis convergence profit, net returns were negative.

---

## Strategy C: Cross-Token Momentum Spread (Long-Short)

### Method
- Universe: 54 tokens with 2+ years of spot+perp data (excluding BTC as benchmark)
- Weekly rebalance: rank by 14-day return, long top 3, short bottom 3
- Equal dollar weight per leg, dollar-neutral

### Results
KILLED: Sharpe -0.235, MaxDD -58.57%, 91 trades, Win rate 46.2%.

### Root Cause Analysis

**The short leg systematically underperforms expectations.**

The strategy achieves near-zero BTC correlation (-0.063) -- it IS market-neutral. But the returns are deeply negative.

This happens because:
1. **Momentum reversal on short leg**: the worst-performing tokens over 14 days often bounce back violently. Shorting losers in crypto is dangerous because:
   - "Dead cat bounce" patterns are common
   - Protocol announcements / narrative shifts can reverse 14-day trends instantly
   - Small-cap tokens have explosive mean-reversion at short horizons
2. **Winner concentration**: the top 3 tokens may share correlated momentum (e.g., all meme coins pumping together), then reverse simultaneously.
3. **Asymmetric losses**: short positions in tokens that 10x produce unlimited losses, while long positions in tokens that drop can only lose 100%. The expected PnL of the short leg is worse than the long leg.
4. **The win rate (46.2%) is tantalizingly close to the 45% threshold** but the average losing trade is much larger than the average winning trade, giving a profit factor of only 0.927.

---

## Market Neutrality Analysis

The one consistent positive finding: nearly all variants achieved BTC correlation below 0.1. The long-short construction successfully hedges out market beta.

| Strategy | BTC Corr | Neutral? |
|----------|----------|----------|
| A: ETH/BTC | 0.067 | YES |
| A: SOL/ETH | 0.039 | YES |
| A: BNB/BTC | -0.006 | YES |
| A: DOGE/XRP | -0.004 | YES |
| B: BTC basis (10bps) | -0.040 | YES |
| B: BTC basis (20bps) | -0.005 | YES |
| B: ETH basis (10bps) | 0.021 | YES |
| B: ETH basis (20bps) | 0.022 | YES |
| B: ETH basis (50bps) | 0.012 | YES |
| C: XS Mom L3/S3 | -0.063 | YES |

**These strategies are market-neutral -- they just have negative alpha.**

---

## Conclusions

### All 11 variants failed. Pairs/stat-arb is NOT viable for our crypto universe in the current regime.

**Why pairs trading fails in crypto (structural reasons):**

1. **No true cointegration**: Unlike equity pairs (e.g., Coca-Cola / Pepsi share customers, costs, macro exposure), crypto tokens are NOT driven by common economic fundamentals. "ETH/BTC" is not a pair with an economic anchor -- it is two independent speculative assets with loosely correlated narratives.

2. **Basis arbed away**: The spot-perp basis was a viable trade in 2020-2021 but has been compressed to near-zero by automated market makers. The remaining basis is smaller than transaction costs.

3. **Crypto momentum reverses at short horizons**: Cross-sectional momentum (rank and hold) produces negative returns with a short leg in crypto because of violent reversals in beaten-down tokens.

4. **Transaction costs dominate**: With 10bps per leg and frequent signal-driven rebalancing, the strategy needs significantly positive gross alpha to survive, and none of these variants generate positive gross alpha.

### Recommendations for Future Research

1. **Do NOT pursue pairs trading further** -- the structural conditions do not support it in crypto.

2. **If exploring market-neutral approaches**, consider:
   - **Funding rate harvesting** without the basis component -- pure carry from perp funding rates (long perps in negative funding, short in positive), avoiding the basis convergence bet entirely
   - **Statistical arbitrage across exchanges** (cross-exchange arb) rather than cross-asset arb -- though this requires multi-exchange data and sub-second execution
   - **Options-based relative value** if options data becomes available

3. **Accept beta exposure and manage it**: Rather than hedging to zero beta, overlay directional strategies with position sizing that reduces exposure during high-risk regimes (which we already test elsewhere).
