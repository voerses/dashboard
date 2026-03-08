# Strategy Catalog — Compact Reference

> **TL;DR** Trend following is the only consistent edge in crypto at swing timeframes.
> S09 (dual momentum) and S11 (momentum burst) are Tier A production strategies.
> Mean reversion loses money at 18-720hr holds. Simple beats complex (4 conditions > 10).
> **Full details + 67 citations:** `knowledge/archive/STRATEGY_CATALOG_DETAILS.md`

**Context:** Crypto swing trading, $200K capital, 1H timeframe, 18-720hr holds, 6-111 tokens
**Last validated:** March 2026, 111 tokens (spot+perp+combined), Jan 2024 - Jan 2026 data

---

## Production Strategies (Tier A)

### S09 Optimized Trend (Dual Momentum)

| Metric | Value |
|--------|-------|
| Annual PnL | +$162,898/yr (all 49), +$55,408/yr (CPCV 11) |
| Win rate / Payoff | 38-45% / 1.8-2.4x |
| Trade freq | 500-900/yr across token universe |
| Validation | CPCV (PBO < 40%), walk-forward |
| Dual-validated tokens | SUI, TRX, BONK, FLOKI |

**Entry logic:** Daily uptrend (EMA50 + ADX>20 + 12d momentum>0) + 4H pullback to EMA20 + 1H volume burst + RSI 30-55. Multi-timeframe stack (1H:4H:Daily) is critical.

**Based on:** Antonacci (2014) dual momentum + Moskowitz et al. (2012) TSMOM. Crypto-adapted with shorter lookbacks (10-28d vs 12mo) because crypto cycles are faster. Borgards (2021) confirms longer momentum periods in crypto.

### S11 Momentum Burst

| Metric | Value |
|--------|-------|
| Annual PnL | +$170,240/yr (all 49), +$62,705/yr (CPCV 11) |
| Win rate / Payoff | 46-47% / 1.81-1.94x |
| Trade freq | 600-2,400/yr across token universe |
| Validation | CPCV (PBO < 40%), walk-forward |
| Dual-validated tokens | PENGU, SUI, AVAX, BONK, FLOKI, ZRO |

**Entry logic:** Explosive hourly move (>3% in one bar) + trend context (ADX>20, price > EMA20). 24-bar protection window (no stop for 24hrs). Internal development, related to Jegadeesh & Titman (1993) momentum persistence.

### Cross-Sectional Momentum (Diversifier)

| Metric | Value |
|--------|-------|
| Annual PnL | +$189.7%/yr annualized (backtest 2021-2026) |
| Sharpe / Sortino | +1.54 / +1.73 |
| Max Drawdown | -75.2% (805 days) |
| Basket size | ~10 tokens (top 20% of eligible universe) |
| Rebalance | Weekly (7d), 14d trailing return lookback |
| Correlation vs S11 | +0.25 (vs +0.62 among Tier A strategies) |
| Tokens traded | 81 out of 102 eligible |

**Entry logic:** Rank all eligible tokens by 14-day trailing return. Go long the top quintile, equal-weighted, rebalanced weekly. Eligibility: 365-day history + $500K ADV gate. Slippage + fees applied at each rebalance turnover.

**Diversification value:** Structurally different from per-token time-series strategies. Daily return correlation +0.25 vs S11 (rolling 90d median +0.19, range -0.13 to +0.73). A 50/50 blend with S11 cuts max drawdown by 28.7pp (-75% → -46.5%) while maintaining Sharpe 1.74. Supported by Han, Kang & Ryu (2023): cross-sectional outperforms time-series momentum in crypto.

**Implementation:** `v3/cross_sectional.py` — standalone engine, zero blast radius to existing code. Reuses `aggregate_to_timeframe()`, `compute_portfolio_metrics()`, `get_fee_rate()`, and the engine slippage model.

**Parameter sweep (14d best):**
| Lookback | Sharpe | Ann Return | Max DD |
|----------|--------|-----------|--------|
| 7d | +1.47 | +168.4% | -71.2% |
| 14d | +1.54 | +189.7% | -75.2% |
| 30d | +1.48 | +172.1% | -76.2% |
| 60d | +1.10 | +85.3% | -79.1% |

### Signal Agreement Gate: S11+S09 AND (Overlay)

| Metric | S11 Solo | S11+S09 AND Gate |
|--------|---------|-----------------|
| Sharpe | +1.75 | +1.65 (-0.10) |
| Sortino | +2.39 | +2.41 (+0.02) |
| Calmar | +2.32 | +2.78 (+0.46) |
| Max Drawdown | -18.0% | **-11.3%** (+6.7pp) |
| DD Duration | 311 days | 215 days (-96d) |
| Profit Factor | 1.39 | **1.63** (+0.24) |
| Trades | 12,396 | 4,223 (-66%) |
| Avg Trade PnL | $95 | **$160** (+68%) |

**Entry logic:** Enter only when both S11 (momentum burst) AND S09 (optimized trend) trigger on the same 1H bar. Uses S11's trade parameters (stop, trail, hold times). Walk-forward masked identically to solo strategies.

**Value:** Cuts trades by 66% while improving profit factor by 17%, reducing max drawdown by 6.7pp, and boosting avg PnL by 68%. Sharpe only drops 0.10 — excellent risk/return tradeoff. The gate filters out low-conviction entries where only one signal type fires.

**Implementation:** `v3/signal_agreement.py` — standalone tool supporting AND, N-of-M, and ANY gating modes. Zero blast radius to existing code.

### Regime-Conditional Weighting (Overlay)

| Metric | Baseline (S11+S09) | Regime-Weighted | Delta |
|--------|-------------------|-----------------|-------|
| Sharpe | +1.67 | **+1.96** | **+0.29** |
| Sortino | +2.41 | **+2.79** | **+0.38** |
| Calmar | +2.51 | **+3.47** | **+0.96** |
| Max Drawdown | -22.2% | **-17.7%** | **+4.5pp** |
| DD Duration | 417 days | **229 days** | **-188 days** |
| Profit Factor | 1.32 | **1.58** | **+0.26** |
| Trades | 33,498 | 25,528 | -24% |
| Ann. Return | +55.8% | **+61.5%** | **+5.7pp** |

**Mechanism:** Scales position sizes by market regime (detected from BTC daily bars). Full allocation in UPTREND, reduced in RANGE (68-92%), half in QUIET, minimal/zero in DOWNTREND/CRISIS. Weights derived empirically from per-regime profit factor analysis.

**Key regime findings (BTC 2020-2026):**
| Regime | Frequency | S11 PF | S09 PF | Weight |
|--------|-----------|--------|--------|--------|
| UPTREND | 39% | 1.94 | 1.46 | 1.00 |
| RANGE | 21% | 1.31 | 1.35 | 0.68-0.92 |
| QUIET | 12% | 1.15 | 1.17 | 0.50 |
| DOWNTREND | 27% | 0.93 | 0.78 | 0.00-0.25 |
| CRISIS | 0.4% | -- | -- | 0.00 |

**Value:** Improves every risk metric while also boosting returns. The biggest win: avoiding DOWNTREND trades where both strategies lose money (PF < 1.0). Eliminates ~8K losing trades while keeping all winning regimes at full allocation.

**Implementation:** `v3/regime_analysis.py` — standalone tool with empirical heatmap analysis + regime-weighted portfolio simulation. Zero blast radius.

### V3 Liquidity Contrarian (Complement)

| Metric | Value |
|--------|-------|
| Annual PnL | +$8,000/yr |
| Win rate | 63% (but only 108 trades in 2 years) |
| Role | Low-correlation complement to S09/S11 |

### HMM Regime Detection (Overlay)

| Metric | Value |
|--------|-------|
| Regime split | 52% range, 19% uptrend, 18% quiet, 10% downtrend, 1% crisis |
| Implementation | `regime_detector.py`, 2-3 state HMM |
| Effect | Drives allocation: 80-90% deployed bull, 10-20% bear |

Based on Hamilton (1989). Crypto-validated by Castellano & D'Ecclesia (2025).

### Sector / Narrative Rotation (Diversifier)

| Metric | Value |
|--------|-------|
| Annual Return | +100.5% annualized (best config, 2021-2026) |
| Sharpe / Sortino | +1.31 / +1.07 |
| Calmar | +1.73 |
| Max Drawdown | -57.9% (616 days) |
| Basket size | ~5.8 tokens (top 2 sectors, hybrid mode) |
| Rebalance | Weekly (7d), 7d trailing return lookback |
| Regime filter | ON — BTC EMA20/50 crossover + vol (33% days risk-off) |
| Correlation vs S11 | +0.20 (even lower than cross-sectional's +0.25) |
| Tokens traded | 79 out of 102 eligible |

**Entry logic:** Classify all tokens into 10 sectors (L1, DeFi, Meme, AI, Gaming, L2, Infra, Privacy, Payments, Emerging). Compute sector-level trailing returns (mean of eligible token returns). Rank sectors, go long top 2. In hybrid mode: within each selected sector, rank tokens by individual trailing return, pick top 5. BTC regime filter goes to cash during downtrends/crises.

**Sector analysis (OOS 2021-2026):**
| Sector | Ann Return | Sharpe | Max DD |
|--------|-----------|--------|--------|
| Meme | +101.3% | +0.91 | -90.8% |
| AI | +29.7% | +0.84 | -94.2% |
| Payments | +27.6% | +0.70 | -78.5% |
| Privacy | +25.6% | +0.72 | -82.3% |
| Gaming | +26.7% | +0.78 | -98.3% |
| L1 | +18.3% | +0.62 | -83.8% |
| DeFi | +4.0% | +0.53 | -87.7% |
| L2 | -51.5% | -0.14 | -96.0% |

**Diversification value:** Structurally different from both per-token time-series strategies AND individual cross-sectional momentum. Captures "narrative rotation" (AI season, meme season) that individual token strategies miss. Daily return correlation +0.20 vs S11. A 50/50 blend: Sharpe +1.65, DD -33.6%, Ann +77.0%.

**Key insight:** Regime filter is essential — without it, DD is -78% to -83% (always invested through bear markets). With regime filter, 33% of days are risk-off (BTC downtrend), cutting DD from -78% to -58%.

**Parameter sweep (regime filter ON):**
| Config | Sharpe | Ann Return | Max DD |
|--------|--------|-----------|--------|
| lb=7 top=2 hybrid (best) | +1.31 | +100.5% | -57.9% |
| lb=7 top=2 sector | +1.10 | +69.0% | -66.5% |
| lb=14 top=2 hybrid | +1.12 | +73.0% | -59.2% |
| lb=30 top=2 hybrid | +1.11 | +70.2% | -59.5% |

**Implementation:** `v3/sector_rotation.py` — standalone engine with sector analysis, parameter sweep, and regime filter. Zero blast radius.

### Pairs Trading — Statistical Arbitrage (Diversifier)

| Metric | Value |
|--------|-------|
| Annual Return | +4.2% annualized (best config, 2021-2026) |
| Sharpe / Sortino | +0.42 / +0.24 |
| Calmar | +0.31 |
| Max Drawdown | -13.6% (486 days) |
| Trade count | 126 trades (5yr backtest) |
| Win rate / PF | 57.9% / 1.31 |
| Rebalance | Weekly (7d), 90d lookback |
| Regime filter | ON — BTC EMA20/50 (33% days risk-off) |
| Correlation vs S11 | **-0.06** (near-zero, essentially uncorrelated) |
| Market | Perpetual futures (both legs) |

**Entry logic:** Identify correlated token pairs via 90-day trailing correlation (>0.60) + half-life of mean reversion (3-60d). Compute rolling z-score of log price spread. Enter when |z| > 3.0 (long underperformer / short outperformer on perps). Exit on mean reversion (|z| < 0.5), stop loss (|z| > 4.0), or max hold (10 days). Max 5 concurrent pairs. BTC regime filter closes all pairs during downtrends.

**Diversification value:** The key selling point. Correlation with S11 is **-0.06** — effectively uncorrelated. This is rare in crypto where everything typically correlates +0.5 to +0.8. Blend analysis:

| Allocation (S11/Pairs) | Sharpe | Sortino | Calmar | Max DD | Ann% |
|------------------------|--------|---------|--------|--------|------|
| 100/0 (S11 only) | +1.89 | +3.11 | +2.21 | -18.2% | +40.2% |
| 90/10 | +1.90 | +3.17 | +2.25 | -16.2% | +36.4% |
| 85/15 | +1.91 | +3.20 | +2.27 | -15.2% | +34.5% |
| 70/30 | +1.91 | +3.23 | +2.38 | -12.2% | +28.9% |
| 50/50 | +1.83 | +2.96 | +2.70 | -8.0% | +21.6% |

**Standalone is modest** (Sharpe +0.42, Calmar +0.31) — does not meet Tier A thresholds. But a 15-30% allocation to pairs improves the combined portfolio's Calmar by 0.06-0.17 while cutting drawdown by 3-6pp. This is pure diversification benefit from near-zero correlation.

**Parameter sweep (regime filter ON):**
| Config | Sharpe | Ann% | Max DD | Trades |
|--------|--------|------|--------|--------|
| lb=90 rb=7 ez=3.0 mh=10 mp=5 (best) | +0.42 | +4.2% | -13.6% | 126 |
| lb=90 rb=7 ez=3.5 mh=10 mp=10 | +0.22 | +1.0% | -8.7% | 89 |
| lb=90 rb=7 ez=3.0 mh=10 mp=10 | +0.25 | +1.7% | -11.3% | 232 |
| lb=75 rb=7 ez=3.0 mh=10 mp=10 | +0.15 | +0.8% | -15.9% | 218 |
| lb=60 rb=14 ez=3.0 mh=15 SS mp=10 | +0.05 | +0.1% | -11.4% | 206 |

**Key finding:** Higher entry threshold (z=3.0+) is critical — lower thresholds (z=2.0) lose money. Shorter holding periods (10d) beat longer. Fewer max pairs (5) concentrates into highest-quality pairs.

**Implementation:** `v3/pairs_trading.py` — standalone engine using perp futures data. Pair selection via correlation + half-life scoring. Funding rate costs loaded from parquet columns. Zero blast radius.

### S29 Funding Carry (Per-Token Perp, Market-Neutral)

| Metric | Value |
|--------|-------|
| Validation Rate | 66/328 (20.1%) — 91.2% among tokens with sufficient funding data |
| Mean Sharpe | +0.81 |
| Mean Calmar | +0.76 |
| Mean Max Drawdown | -1.26% |
| Trade count | 52 (BTC), varies by token |
| Correlation vs S11 | +0.002 (effectively zero) |
| Market | Perpetual futures only |

**Entry logic:** Harvest structural funding rate premium. When rolling 72h mean |funding| > 0.00005/hr (~48% annualized), go opposite direction to funding: long when funding < 0, short when funding > 0. No trend filter — carry works in all regimes except crisis. Wider stops (4.0x ATR initial, 3.5x ATR trail) because carry income compensates for price moves.

**Key parameters:** 72h lookback, 0.00005/hr threshold, Kelly 0.30, max hold 14 days, 48h no-stop protection.

**Value:** First genuinely market-neutral strategy (beta=0.0000). Negative correlation with momentum strategies (-0.155 vs s11, -0.138 vs s31). Best diversifier by marginal Sharpe in 5-strategy portfolio. BTC validation: Sharpe 0.79, PF 2.31, 53.9% win rate, funding drag -18.2% per trade.

**Implementation:** `strategies/s29_funding_carry.py` — per-token perp strategy using standard `strategy(ctx)` signature.

---

### S30 Basis Carry (Combined Spot+Perp, Delta-Neutral)

| Metric | Value |
|--------|-------|
| Validation Rate | 84/109 (77.1%) |
| Mean Sharpe | +2.63 |
| Mean Calmar | +12.76 (highest ever) |
| Mean Max Drawdown | -0.43% |
| Trade count | 36,122 across 84 tokens |
| Marginal Sharpe | +1.53 (highest contributor) |
| Market | Combined: long spot + short perp (simultaneous) |

**Entry logic:** Delta-neutral cash-and-carry arbitrage. When basis (spot-perp price spread) > 0.1% AND rolling 72h z-score > 1.5, simultaneously go long spot + short perp. Captures the basis premium while eliminating directional risk. Both legs enter on the same bar.

**Key parameters:** 72h basis z-score > 1.5, basis minimum 0.1%, vol_ratio > 0.5 (relaxed), capital split 50/50, Kelly 0.25, max hold 21 days. Spot: 3.5x ATR trail / 4.0x stop. Perp: 3.0x ATR trail / 3.5x stop (tighter).

**Regime stability:** Remarkably consistent across all regimes — PF 2.50 (RANGE) to 2.68 (UPTREND). No regime weighting needed.

| Regime | Trades | Win Rate | Profit Factor | Avg Return |
|--------|--------|----------|---------------|------------|
| UPTREND | 13,771 | 54.9% | 2.68 | +2.08% |
| RANGE | 6,451 | 52.9% | 2.50 | +1.77% |
| QUIET | 5,026 | 53.3% | 2.62 | +1.47% |
| DOWNTREND | 10,876 | 54.7% | 2.59 | +1.85% |

**BTC validation:** Sharpe 4.33, Calmar 24.52, MaxDD -0.34%, PF 2.78, 836 trades.

**Value:** Anchor strategy for combined portfolio. Highest Calmar of any strategy tested (12.76 mean). Regime-stable. Moderate correlation with other combined strategies (0.22-0.24). Pattern: simultaneous legs.

**Implementation:** `strategies/s30_basis_carry.py` — combined `strategy(ctx_spot, ctx_perp)` signature. Uses `_simulate_combined` engine method.

---

### S31 Funding-Hedged Momentum (Combined Spot+Perp, Conditional)

| Metric | Value |
|--------|-------|
| Validation Rate | 60/109 (55.0%) |
| Mean Sharpe | +0.64 |
| Mean Calmar | +0.94 |
| Mean Max Drawdown | -1.68% |
| Trade count | 10,712 across 60 tokens |
| Correlation vs S11 | +0.71 (WARNING: redundant) |
| Market | Combined: spot long momentum + conditional perp short hedge |

**Entry logic:** Primary leg: standard momentum burst on spot (close > EMA20, ADX > 20, ret_1 > 2%, vol_ratio > 1.0, uptrend/range regimes). Secondary leg (conditional): perp short hedge only when funding z-score > 2.5 AND RSI > 70 (overbought). The hedge activates independently — not every momentum entry gets hedged.

**Key parameters:** Primary: ret_1 > 2%, ADX > 20, 80% capital. Secondary: funding z > 2.5, RSI > 70, 20% capital. Primary stops: 3.0x ATR trail, max 720h. Secondary stops: 2.5x ATR trail, 4.0x target (take profit), max 120h.

**WARNING — Redundancy with S11:** Correlation +0.71 with s11_momentum_burst. The primary leg IS essentially momentum burst. Both have negative marginal Sharpe when combined in portfolio. **Recommended: exclude from portfolio when s11 is included.**

**Regime performance:**
| Regime | Trades | Win Rate | Profit Factor |
|--------|--------|----------|---------------|
| UPTREND | 6,540 | 44.6% | 1.81 |
| RANGE | 2,310 | 42.5% | 1.53 |
| QUIET | 1,235 | 43.5% | 1.46 |
| DOWNTREND | 1,836 | 39.1% | 1.16 (weak) |

**Implementation:** `strategies/s31_funding_hedged_momentum.py` — combined `strategy(ctx_spot, ctx_perp)` signature. Pattern: conditional secondary leg.

---

### S32 Regime-Adaptive Spot-Perp (Combined, Alternating)

| Metric | Value |
|--------|-------|
| Validation Rate | 86/109 (78.9%) — highest of combined strategies |
| Mean Sharpe | +1.37 |
| Mean Calmar | +2.86 |
| Mean Max Drawdown | -0.78% |
| Trade count | 27,469 across 86 tokens |
| Marginal Sharpe | +0.25 |
| Market | Combined: spot long (uptrends) / perp short (downtrends), alternating |

**Entry logic:** Alternating regime-gated legs. In UPTREND/QUIET: spot long when close > EMA20, ADX > 25, 24h return > 5%, vol_ratio > 0.8. In DOWNTREND: perp short when close < EMA20, ADX > 20, 24h return < -3%, vol_ratio > 0.8. Never both legs simultaneously — uses spot for longs (no funding drag) and perps for shorts (only instrument that can short).

**Key parameters:** Long: ADX > 25, ret_24h > 5%, 60% capital. Short: ADX > 20, ret_24h < -3%, 40% capital. Long stops: 2.5x ATR trail, 5.0x target, max 720h. Short stops: 2.0x ATR trail, 4.0x target, max 336h.

**Regime performance:**
| Regime | Trades | Win Rate | Profit Factor |
|--------|--------|----------|---------------|
| UPTREND | 8,970 | 46.5% | 2.28 |
| RANGE | 4,167 | 45.6% | 2.13 |
| QUIET | 2,501 | 45.4% | 1.91 |
| DOWNTREND | 12,181 | 42.6% | 1.40 |

**BTC validation:** Sharpe 1.41, Calmar 3.21, MaxDD -0.46%, PF 1.90, 326 trades.

**Value:** Highest validation rate (78.9%) of any combined strategy. Uses the right instrument for each regime — spot for longs avoids funding costs, perps for shorts earn funding in downtrends. Moderate correlation with s30 (0.24), good portfolio complement.

**Implementation:** `strategies/s32_regime_spot_perp.py` — combined `strategy(ctx_spot, ctx_perp)` signature. Pattern: alternating legs.

---

### Portfolio Assembly — Combined Strategy Portfolio (Gate 5.5)

**Recommended 4-strategy allocation** (s31 excluded due to s11 redundancy):

| Strategy | Allocation | Marginal Sharpe | Role |
|----------|-----------|-----------------|------|
| s30 basis_carry | 40% | +0.95 | Anchor — regime-stable arb |
| s32 regime_spot_perp | 25% | +0.25 | Regime-adaptive directional |
| s29 funding_carry | 20% | — | Market-neutral diversifier |
| s11 momentum_burst | 15% | — | Per-token momentum |

**3-strategy combined portfolio metrics (s30+s31+s32):**

| Metric | Value |
|--------|-------|
| Sharpe | 4.41 |
| Sortino | 6.37 |
| Calmar | 14.29 |
| Max Drawdown | -7.1% |
| DD Duration | 36 days |
| Total Return | 3,588% |
| Annualized Return | 101.2% |
| Total Trades | 75,864 |
| Tokens Traded | 91 |

**5-strategy cross-family correlation:**

| | s30 | s31 | s32 | s11 | s29 |
|---|-----|-----|-----|-----|-----|
| s30 | 1.00 | 0.22 | 0.24 | 0.07 | 0.08 |
| s31 | 0.22 | 1.00 | 0.32 | **0.71** | -0.14 |
| s32 | 0.24 | 0.32 | 1.00 | 0.21 | -0.02 |
| s11 | 0.07 | **0.71** | 0.21 | 1.00 | -0.16 |
| s29 | 0.08 | -0.14 | -0.02 | -0.16 | 1.00 |

**Effective N:** 3.03 with 5 strategies (median corr 0.21).

**Regime weighting verdict:** Not needed. s30 already regime-stable (PF 2.50-2.68 across all regimes). Weighted portfolio only adds +0.03 Sharpe vs baseline — not worth the complexity.

---

### Dynamic Universe — Point-in-Time Token Eligibility (Infrastructure)

| Metric | Static Universe | Dynamic Universe | Bias |
|--------|----------------|-----------------|------|
| Sharpe (liquid, 102 tokens) | +1.75 | +1.74 | +0.2% (minimal) |
| Sharpe (all, 116 tokens) | +1.75 | +1.70 | +2.7% (modest) |
| Ghost trades (liquid) | — | 36 trades, $7.8K PnL | |
| Ghost trades (all) | — | 112 trades, $29.3K PnL | |

**What it fixes:** Static universe locks the token list at backtest start using current ADV/quality. Dynamic universe re-evaluates eligibility every 90 days (matching walk-forward windows) using only data available at each point. Tokens that hadn't listed yet or lost liquidity are excluded from that window.

**Key finding:** The existing per-bar liquidity mask in `engine.py` already handles most of the bias. The residual static-universe look-ahead is **+0.2% to +2.7%** Sharpe inflation depending on universe breadth. Not catastrophic but worth correcting for rigorous backtests.

**Timeline insights (BTC-era 2020-2026):** Universe grew from 21 tokens (2020) to 101 tokens (2026). XMR lost eligibility in 2024 (Binance delisting). OM had intermittent eligibility (3 gaps).

**Implementation:** `v3/dynamic_universe.py` — standalone tool. Zero blast radius.

---

## Priority Ranking — All Strategies

### Tier A: Production (Validated)

| # | Strategy | Status | Sharpe | Type |
|---|----------|--------|--------|------|
| 1 | **S30 Basis Carry** | **VALIDATED (combined)** | **+2.63** | **Delta-neutral arb** |
| 2 | S11 Momentum Burst | VALIDATED (spot) | +2.58 | Per-token momentum |
| 3 | S09 Dual Momentum Trend | VALIDATED (spot) | +1.98 | Per-token dual momentum |
| 4 | **S32 Regime Spot-Perp** | **VALIDATED (combined)** | **+1.37** | **Alternating regime** |
| 5 | Cross-Sectional Momentum | VALIDATED (diversifier) | +1.54 | Portfolio ranking |
| 6 | Sector/Narrative Rotation | VALIDATED (diversifier) | +1.31 | Category momentum |
| 7 | S29 Funding Carry | VALIDATED (perp) | +0.81 | Market-neutral carry |
| 8 | Regime-Conditional Weighting | VALIDATED (overlay) | +0.29 | Overlay |
| 9 | **S31 Funding-Hedged Momentum** | **VALIDATED (combined)** | **+0.64** | **Conditional hedge** |
| 10 | V3 Liquidity Contrarian | VALIDATED (complement) | — | Low-corr complement |
| 11 | HMM Regime Detection | VALIDATED (overlay) | — | Overlay |

### Recommended Portfolio (Gate 5.5)

| Strategy | Weight | Sharpe | MaxDD | Role |
|----------|--------|--------|-------|------|
| s30 basis_carry | 40% | 2.63 | -0.4% | Anchor (regime-stable) |
| s32 regime_spot_perp | 25% | 1.37 | -0.8% | Directional (regime-gated) |
| s29 funding_carry | 20% | 0.81 | -1.3% | Diversifier (market-neutral) |
| s11 momentum_burst | 15% | 2.58 | -18.2% | Momentum alpha |
| **Portfolio** | **100%** | **4.41** | **-7.1%** | **Calmar 14.29** |

Note: s31 excluded — redundant with s11 (corr +0.71).

### Tier B: Validated / High Priority

| # | Strategy | Signal type | Expected lift | Complexity |
|---|----------|-------------|---------------|------------|
| 0 | **Pairs Trading (Stat Arb)** | **VALIDATED (diversifier)** | **corr -0.06 vs S11, cuts DD -6pp** | **Medium** |
| 1 | Vol scaling (Moreira & Muir 2017) | Sizing overlay | +0.3-0.5 Sharpe | Low |
| 2 | Momentum crash protection (Barroso 2015) | Sizing overlay | Better drawdowns | Low |
| 3 | DI crossover direction (Wilder 1978) | Entry filter | Higher win rate | Low |
| 4 | Volume-weighted TSMOM (Huang 2024) | Signal variant | Sharpe 2.17 (lit.) | Medium |
| 5 | KAMA adaptive MA (Kaufman 1995) | Signal variant | Fewer whipsaws | Medium |
| 6 | Meta-labeling (Lopez de Prado 2018) | Entry filter | +10-20% hit rate | Medium |

### Tier C: Worth Testing

| # | Strategy | Signal type | Expected lift | Complexity |
|---|----------|-------------|---------------|------------|
| 1 | Dynamic strategy allocation by regime | Allocation | +15-30% Sharpe | Medium |
| 2 | RS-based token selection | Token filter | Better concentration | Low |
| ~~3~~ | ~~Sector rotation overlay~~ | ~~Allocation~~ | **PROMOTED to Tier A** — Sharpe +1.31, corr +0.20 vs S11 | ~~Medium~~ |
| 4 | Pyramiding (Turtle-style adds) | Sizing | Larger trend capture | Medium |
| 5 | Channel breakout (ATR-based) | Signal | Replace failed vol_breakout | Medium |
| 6 | CUSUM structural break filter | Entry filter | Fewer noise trades | Medium |
| 7 | Fractional differentiation | Feature eng. | Better ML inputs | Medium |

---

## Strategy Graveyard (Tier D: Do Not Implement)

| Strategy | Category | Reason for rejection | Our result |
|----------|----------|---------------------|------------|
| BB mean reversion (any variant) | Mean reversion | Loses -$25K to -$185K/yr at swing TF | FAILED |
| RSI extremes (standalone) | Mean reversion | IC=0.004 standalone; MR loses at swing TF | FAILED |
| Vol breakout (BB squeeze) | Volatility | -$4,220/yr; squeeze too frequent, 55% fakeout | FAILED |
| TTM Squeeze | Volatility | Same problems as vol breakout; weak academic support | NOT TESTED |
| ~~Pairs trading~~ | ~~Cross-sectional~~ | **PROMOTED to Tier B** — Sharpe +0.42, corr -0.06 vs S11. Earlier dismissal was "requires shorting" but perp infrastructure now exists. Standalone is modest but diversification value is exceptional: 70/30 S11/Pairs blend cuts DD from -18.2% to -12.2% while maintaining Sharpe 1.91. | VALIDATED |
| OU-based strategies | Mean reversion | Crypto is momentum-driven, fails OU model test | N/A |
| ~~Cross-sectional momentum~~ | ~~Cross-sectional~~ | **PROMOTED to Tier A** — Sharpe +1.54, corr +0.25 vs S11. Earlier dismissal based on incomplete reading of Han 2023 (which actually favors XS over TS momentum). | VALIDATED |
| Order book imbalance | Microstructure | Signal horizon minutes, too short for swing | N/A |
| RSI-2 / Connors strategies | Mean reversion | Too short-term; RSI IC=0.004 in crypto | N/A |
| Straddle-like (spot) | Volatility | Requires long+short; spot-only limitation | N/A |
| Vol risk premium harvesting | Volatility | Requires options/perp infrastructure | N/A |
| Deep RL for signals | ML | High complexity, low OOS reliability, catastrophic forgetting | N/A |
| Transformer prediction | ML | Insufficient data, non-stationarity, low interpretability | N/A |
| GNN token relationships | ML | Unstable graph structure, emerging/unproven | N/A |
| V2 Daily Momentum | Trend | Too slow; -$13,806/yr (golden cross lag) | FAILED |
| VPIN filter (current data) | Microstructure | Hurt performance; data quality issue (0.5 fills) | FAILED |
| s25 Vol Spike Reversal | Volatility | BTC failed 3x; proceeded to G5 on altcoin promise but didn't pass | FAILED |
| s26 RSI Extreme Reversal | Mean reversion | 14.3% rate (47/329), below 20% threshold | FAILED |
| s27 Funding Mean Reversion | Mean reversion (perp) | 11.6% rate (38/329), funding data sparse for many tokens | FAILED |
| s28 Momentum Burst Perp | Momentum (perp) | 6.7% rate (22/329), bidirectional too selective on perp | FAILED |
| s35 regime_spot_perp_volsized | Vol-managed sizing | Calmar -29%, return halved vs s32. Vol-managed sizing too aggressive on regime-gated strategy | FAILED |
| s36 basis carry funding-scaled | Funding overlay | Neutral — Calmar -0.29, Sharpe -0.01. Funding rate colinear with basis premium | FAILED |
| s38 momentum ETF flow | ETF flow overlay | ETF data covers only 15mo; overlay=1.0 for 85% of backtest | FAILED |
| s42 momentum defensive trail | Per-bar vol ceiling | Zero marginal improvement on top of O5 trail progression | FAILED |
| s43 regime spot/perp trail | Trail on s32 | Rate -2.2pp; trail tightens short leg prematurely | FAILED |
| s50 momentum_extreme_leverage | 5x leverage momentum | 65% rate but only +2.7% mean return. Fees amplified more than edge | FAILED |
| s52 funding_extremes_leveraged | 5x leverage funding | 40% rate, +2.4% mean return. Funding extremes too rare | FAILED |
| s53 alt_momentum_breakout | Breakout filters | 37% rate, +1.0% mean return. Filters too strict | FAILED |
| s55 leveraged_carry_momentum | 2-5x leverage carry | -7pp MaxDD degradation exposed by intra-bar liquidation fix | FAILED |
| s56 max_leverage_momentum | 5x signal-enhanced | 14% rate, negative mean return. Leverage amplifies losses | FAILED |
| **Standalone signal strategies** | Signal discovery entries | IC != tradeable edge. Signals only work as overlays on proven strategies | FAILED |

---

### Overlay Wrappers (s34-s44, s54, s57-s58)

| Strategy | Base | Overlay | Result | Status |
|----------|------|---------|--------|--------|
| s34 momentum_regime_sized | s11 | O2 regime sizing + O3 weekend | Sharpe +0.27, PF +0.08 | Validated |
| s37 momentum_trail_progression | s11 | O5 progressive trail | Sharpe +0.477, 65/69 token wins | Validated |
| s39 trend_trail_progression | s09 | O5 progressive trail | Sharpe +0.819, rate 36→59% | Validated |
| s40 tsmom_trail_progression | s13 | O5 progressive trail | Sharpe +0.752, rate 24→52% | Validated |
| s41 skew_trail_progression | s21 | O5 progressive trail | Sharpe +0.468, rate 24→47% | Validated |
| s44 basis_carry_trail_progression | s30 | O5 progressive trail | Sharpe +1.448, MaxDD halved | Validated |
| s54 turbo_carry | s44 | 2x regime sizing + cap_mult=15 | +160%/yr, +66.5%/yr last 12mo | Paper trading |
| s57 signal_timed_turbo_carry | s44 | Signal discovery timing | Signal-enhanced carry | Paper trading |
| s58 multi_strategy_portfolio | s44+ | Multi-signal composite | Portfolio with signal overlays | Paper trading |

---

## Key Research Findings

### What Works in Crypto (Swing TF)

| Finding | Evidence | Implication |
|---------|----------|-------------|
| ADX is #1 predictor | IC=0.067, increases with horizon | Keep ADX>20 as core filter |
| Multi-TF stack beats single-TF | S09/S11 use 1H:4H:Daily | Never reduce to single timeframe |
| Simple beats complex | S11 (4 conditions) 75.5% vs S16 (10 conditions) 0% | Cap entry conditions at 4-5 |
| Momentum > everything else | Only profitable strategy type at swing TF | All production strategies are trend-based |
| Regime filtering is essential | Momentum works bull/neutral, fails bear | Always use regime overlay |
| Delta-neutral arb is regime-stable | s30 PF 2.50-2.68 across all regimes | Best anchor for portfolio |
| Combined spot+perp unlocks new edge | 3 patterns: simultaneous, conditional, alternating | Each captures different premium |
| Funding carry is market-neutral | s29 beta=0.000, corr=+0.002 vs S11 | Best diversifier available |
| Basis carry has highest Calmar | s30: Calmar 12.76, MaxDD -0.4% | Risk-adjusted king |
| 3x ATR stop is optimal | Parameter sweep finding | Don't change stop multiplier |
| TSMOM lookback 10-28d | Han (2023): 28d lookback, Sharpe 1.51 | Crypto cycles faster than equities |
| Vol-weighted TSMOM promising | Huang (2024): Sharpe 2.17 | Untested in our system; Tier B priority |
| Cross-TF divergence is most stable signal | `ret_1_1h_vs_4h` IC=-0.376, STABLE | Use as overlay timing, not standalone |
| Signals only work as overlays | s56 killed, s57/s58 survive | Layer on proven strategy (carry, momentum), not as entries |
| 92% of signals are sign-consistent | Discovery across 5 horizons | Same signal direction works at all timeframes |
| Only 23% of signals are genuinely causal | Lead/lag analysis, 57 of 252 | Prioritize HIGH-confidence LEADING signals |
| Regime-conditional EMAs decay fast | `ema_50_in_QUIET` lost 77% IC | Cross-TF signals more temporally robust |
| Progressive trailing stops are universal | s37-s44 all improved | Strongest single overlay: +21.5pp avg, 93% win rate |

### What Fails in Crypto (Swing TF)

| Finding | Evidence | Why |
|---------|----------|-----|
| Mean reversion | All 70-sweep MR strategies lose | Hold period mismatch; MR needs 1-day holds or no stops |
| RSI as standalone | IC=0.004 | Near-zero predictive power alone |
| BB squeeze breakout | -$4,220/yr | Too frequent (160/760 days), 55% false breakout rate |
| Complex entry logic | S16 at 0% survival | More conditions = more overfit paths = worse OOS |
| Stop-losses on MR | Beluska & Vojtko (2024) | Stops destroy MR performance; conflicts with risk mgmt |
| Momentum burst on perps | s28: 6.7% rate | Bidirectional momentum too selective; perp fees/funding eat edge |
| Funding mean reversion | s27: 11.6% rate | Funding data sparse; signal decays fast once widely known |
| Hedged momentum + s11 redundancy | s31↔s11 corr +0.71 | Momentum primary leg IS momentum burst; hedge adds little |
| Standalone signal entries | s56: 14% rate, negative mean | IC predicts returns but not enough edge after costs |
| 5x leverage on any strategy | s50/s52/s56: all killed | Fees amplified 5x eat the edge; use 1x with aggressive sizing instead |
| Cross-TF signals as standalone | Discovery showed IC=-0.376 | Genuine IC but only ~14% of return variance; needs base strategy |

### RSI Regime Dependency

RSI behaves opposite to textbook expectations depending on regime:

| Regime | High RSI predicts | Low RSI predicts | Implication |
|--------|-------------------|------------------|-------------|
| Uptrend | MORE upside | Dip-buy opportunity | Use RSI as momentum confirmation |
| Downtrend | MORE downside | Nothing useful | Fade bounces, not buy dips |
| Range | Mean reversion (weak) | Mean reversion (weak) | Only regime where textbook RSI works |

### Top 2-Indicator Combinations (1-day horizon)

| Combination | Mean return | Win rate | Token count |
|-------------|-----------|----------|-------------|
| RSI_low + MACD_pos | +1.37% | 58.3% | -- |
| vol_ratio_hi + BB_pct_low | +0.99% | 58.4% | 45 tokens |
| Taker > 0.55 + positive daily return | +1.18% | 52.1% | 19 tokens |

---

## Regime Model Overview

Our HMM detects 5 regimes that drive all allocation decisions:

| Regime | Frequency | Action | Deployment |
|--------|-----------|--------|------------|
| Uptrend | 19% | Full momentum (S09+S11) | 80-90% |
| Range | 52% | Reduced exposure, trend-only | 40-60% |
| Quiet | 18% | Selective, wait for breakout | 30-50% |
| Downtrend | 10% | Cash/minimal | 10-20% |
| Crisis | 1% | Circuit breaker: -15% half, -25% cash | 0% |

**Key insight:** 52% of time is range regime where momentum signals are weak. Capturing this dead time is the biggest improvement opportunity (Tier C #1: dynamic strategy allocation).

---

## Unexplored High-Value Techniques (from Lopez de Prado 2018)

| Technique | Chapter | What it does | Priority |
|-----------|---------|-------------|----------|
| Meta-labeling | Ch. 3 | Secondary model predicts if S09/S11 signal will win | Tier B |
| Fractional differentiation | Ch. 5 | Stationary features that preserve long-term memory | Tier C |
| CUSUM filter | Ch. 17 | Trade only after structural breaks (regime changes) | Tier C |
| Triple barrier labeling | Ch. 3 | Already implemented (stop/trail/max hold) | DONE |
| CPCV | Ch. 12 | Already implemented (primary validation) | DONE |

---

## Research Principles

1. **CPCV is the gold standard** for strategy validation. PBO < 40% required.
2. **Harvey t-stat > 3.0** threshold for any new signal (multiple testing correction for 70+ strategies tested).
3. **Simple dominates complex** at every validation level we have tested.
4. **Features that survive costs** are always the same: momentum, volatility, volume, trend strength (ADX). Exotic features (sentiment, NLP, alternative data) rarely survive. (Gu et al. 2020)
5. **Ensemble > single strategy.** S09 + S11 together diversify better than either alone. Cross-sectional momentum (corr +0.25 vs S11) adds genuine diversification vs the +0.62 pairwise correlation among time-series strategies.
6. **Combined spot+perp unlocks new strategy classes.** Three distinct patterns (simultaneous, conditional, alternating) each capture different market premiums. s30 basis carry (Calmar 12.76) is the best risk-adjusted strategy ever validated.
7. **Diversification across market types beats within-type.** s30(combined)+s29(perp)+s11(spot) have median pairwise corr 0.08 vs 0.62 among spot-only strategies. Effective N=3.03 with 5 strategies vs N=2.02 with 6 spot strategies.
8. **IC != tradeable edge.** Signal discovery found 252 FDR-passing signals with genuine IC, but standalone signal strategies generated zero positive returns. Signals only work as overlays on existing profitable strategies (carry, momentum). The base strategy provides the structural edge; the signal improves timing and sizing.
9. **Cross-TF divergence is the most robust alpha source.** `ret_1_1h_vs_4h` (IC=-0.376) and `rsi_1h_vs_4h` (IC=-0.291) are STABLE, LEADING, and work across ALL regimes. They exploit information lag between timeframes — a genuine market microstructure effect, not curve-fitting.
10. **Aggressive sizing beats leverage.** s54/s57/s58 use `size_multiplier=3.0` + `cap_multiplier=15.0` at 1x leverage instead of 5x leverage. Same position sizes, but fees are on 1x notional not 5x. Every 5x leveraged strategy was killed.

*Full strategy descriptions, parameter tables, and 67 academic citations archived in `knowledge/archive/STRATEGY_CATALOG_DETAILS.md`.*
