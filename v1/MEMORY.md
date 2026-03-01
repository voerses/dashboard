# Crypto Trading Strategy Memory

## V3 Post-ETF Analysis — LATEST (Feb 28, 2026)

### What Changed Post-ETF (Jan 10, 2024)

**Signal Lab Results (29 signals, 116 tokens, 9.5 seconds):**
- **KILLED post-ETF**: EMA cross (IC: +0.039 → -0.001), Donchian position, RSI, Z-scores, BB%
- **FLIPPED sign**: btc_lead_5d (+0.014 → -0.072), vol_adjusted_momentum (+0.011 → -0.038)
- **IMPROVED post-ETF**: mean_reversion_5d (-0.002 → +0.033), btc_corr_20 (-0.016 → +0.042), volume_momentum (+0.024 → +0.040)
- **STRONGEST post-ETF**: vol_of_vol (IC=-0.098), btc_lead_5d (IC=-0.072), garman_klass (IC=-0.065)
- **Composite signal IC**: 0.049 (5d fwd), 0.079 (20d fwd) — usable

**Causal Analysis:**
- Autocorrelation: pre-ETF -0.042, post-ETF +0.008 → returns LESS predictable
- BTC Hurst proxy: 0.105 (weakly trending), cross-scale coherence: 0.64 (conflicting)
- Transfer entropy: BTC→alt influence roughly stable, slight decrease

**Correlation Topology:**
- Market factor dominance shifted post-ETF
- Crypto is becoming more efficient (institutional arb)

### V3 Quick Evaluation — 10 Liquid Tokens

**BEST POST-ETF RESULTS (Post-ETF Sharpe):**

| Rank | Token | Strategy | Post-ETF Sharpe | Post-ETF Return | Post-ETF DD | Version |
|------|-------|----------|----------------|-----------------|-------------|---------|
| 1 | DOGE | Adaptive Multi-Scale | 1.59 | +81.9% | -10.3% | V3 |
| 2 | DOGE | Liquidity Contrarian | 1.46 | +12.9% | -2.4% | V3 |
| 3 | XRP | HMM Regime Adaptive | 1.42 | +47.3% | -9.2% | V2 |
| 4 | BNB | Funding+OI Divergence | 1.29 | +16.8% | -3.9% | V2 |
| 5 | BNB | Meta-Adaptive Ensemble | 1.23 | +12.7% | -3.0% | V3 |
| 6 | BNB | Volatility Harvesting | 1.17 | +15.4% | -5.3% | V3 |
| 7 | ADA | Liquidity Contrarian | 1.02 | +11.2% | -3.3% | V3 |
| 8 | ADA | Adaptive Multi-Scale | 1.00 | +42.0% | -16.6% | V3 |

**STRATEGY COMPARISON (Post-ETF):**

| Strategy | Version | Avg Post-ETF Sharpe | Beat B&H Post-ETF | Avg DD |
|----------|---------|-------------------|-------------------|--------|
| Adaptive Multi-Scale | V3 | **0.61** | **6/10** | -12.9% |
| Liquidity Contrarian | V3 | 0.34 | 4/10 | -4.8% |
| HMM Regime Adaptive | V2 | 0.30 | 6/10 | -21.7% |
| Cross-Sectional Momentum | V3 | 0.25 | 5/10 | -9.2% |
| Meta-Adaptive Ensemble | V3 | 0.24 | 6/10 | -8.2% |
| Momentum Trend | V2 | 0.17 | 2/10 | -14.6% |
| Funding+OI Divergence | V2 | 0.15 | 5/10 | -8.8% |
| Volatility Harvesting | V3 | -0.01 | 3/10 | -11.1% |
| Buy & Hold | - | 0.31 | - | -23.5% |

**KEY FINDINGS:**
1. **V3 BEATS V2** post-ETF: avg Sharpe 0.284 vs 0.210
2. **Adaptive Multi-Scale is the winner** — Sharpe 0.61, beats B&H 6/10 tokens
3. **Liquidity Contrarian** — lowest drawdown (4.8%), Sharpe 0.34
4. **B&H avg is 0.31** — Adaptive Multi-Scale (0.61) DOUBLES it
5. **10/10 tokens have at least one strategy beating B&H post-ETF**

### Token Selection Recommendations

| Token | Verdict | Best Strategy | Post-ETF Sharpe | B&H Sharpe |
|-------|---------|--------------|----------------|------------|
| DOGE | **INVEST** (8/8 beat B&H) | Adaptive Multi-Scale | 1.59 | 0.52 |
| BNB | **INVEST** (4/8 beat B&H) | Funding+OI Divergence | 1.29 | 0.84 |
| XRP | **INVEST** (2/8 beat B&H) | HMM Regime Adaptive | 1.42 | 0.83 |
| ADA | **INVEST** (4/8 beat B&H) | Liquidity Contrarian | 1.02 | 0.09 |
| NEAR | **INVEST** (5/8 beat B&H) | Liquidity Contrarian | 0.76 | -0.02 |
| BTC | **INVEST** (3/8 beat B&H) | Funding+OI Divergence | 0.85 | 0.55 |
| SOL | **MONITOR** | Adaptive Multi-Scale | 0.72 | 0.28 |
| AVAX | **MONITOR** (7/8 beat B&H) | Adaptive Multi-Scale | 0.43 | -0.30 |
| ETH | **MONITOR** | Cross-Sectional Momentum | 0.33 | 0.14 |
| LINK | **AVOID** | Adaptive Multi-Scale | 0.19 | 0.14 |

### Liquidity Tiers (for $200K portfolio)
- **Tier 1** (full position): BTC, ETH, SOL, BNB, XRP, DOGE, ADA, AVAX, DOT, LINK
- **Tier 2** (max 50%): Most mid-caps (NEAR, UNI, ATOM, etc.)
- **Untradeable**: Tokens with <$3M average daily volume (DCR, STEEM, JUV, ADX, D)
- Rule: Never trade >2% of average daily volume
- 15 tokens had >20% ADV decline post-ETF (liquidity drying up)

### Fast Iteration Framework
- `signal_lab.py` — Tests 29 signals in 9.5 seconds (vs hours for full backtest)
- IC (Information Coefficient) = rank corr between signal and forward returns
- |IC| > 0.03 is useful, > 0.05 is strong, > 0.10 is exceptional
- Use signal_lab to screen ideas, then backtest winners only

---

## V2 Walk-Forward Results — REAL DATA (Feb 28, 2026)

### Data
- **REAL historical data** from Binance API (no synthetic data)
- BTC: 3000 days (Dec 2017 — Feb 2026), ETH: 3000 days, SOL: 2028 days
- $200K initial capital, 0.1% fees, 5bps slippage
- **Walk-forward backtesting**: 365-day training, 90-day regime recalibration
- **No look-ahead bias**: regime detection only on past data, signals only on test period

### Full Walk-Forward Results Table

| Asset | Strategy | Return | Sharpe | Max DD | Win Rate | Trades |
|---|---|---|---|---|---|---|
| BTC | Buy & Hold | +299% | 0.59 | -83% | - | 1 |
| BTC | HMM Regime | +160% | 0.94 | -18% | 56% | 54 |
| BTC | Momentum | +89% | 1.00 | -11% | 41% | 76 |
| BTC | Funding+OI | +43% | 0.91 | -7% | 50% | 64 |
| ETH | Buy & Hold | +173% | 0.58 | -94% | - | 1 |
| ETH | HMM Regime | +199% | 0.73 | -26% | 58% | 55 |
| ETH | Momentum | +85% | 0.65 | -21% | 37% | 78 |
| SOL | Buy & Hold | +2298% | 1.07 | -96% | - | 1 |
| SOL | HMM Regime | +220% | 0.95 | -31% | 56% | 36 |
| SOL | Momentum | +191% | 1.05 | -26% | 36% | 53 |

### Architecture
- `real_data_fetcher.py` — Pulls OHLCV from Binance API, caches locally (149 tokens)
- `regime_detector.py` — HMM + BOCPD + VPIN + vol clustering, composite
- `position_sizer.py` — Kelly + vol scaling + regime + drawdown + anti-martingale
- `strategies.py` — V2: 5 strategies (HMM, Momentum, MeanRev, Funding+OI, Ensemble)
- `strategies_v3.py` — V3: 5 new strategies (Vol Harvesting, Cross-Sectional, Liquidity, Multi-Scale, Meta-Adaptive)
- `walk_forward.py` — Walk-forward engine with expanding window regime detection
- `backtest_engine.py` — Fee/slippage-aware backtester
- `signal_lab.py` — Fast signal IC evaluation (29 signals, <10 seconds)
- `causal_analysis.py` — Transfer entropy, topology, structural breaks
- `liquidity_analysis.py` — Amihud, Kyle's Lambda, market impact, liquidity tiers
- `research_post_etf.py` — Comprehensive pre vs post ETF analysis
- `run_v3_quick.py` — Quick 10-token evaluation
- `run_v2_full.py` — Full 149-token analysis
- `dashboard.html` — Interactive HTML/JS dashboard

### User Preferences
- $200K capital
- NOT doing arbitrage, NOT doing HFT
- Wants medium-term strategies with some price movement needed
- Wants visual dashboard to see buy/sell signals and trigger manually
- Wants automatic market condition detection
- Targeting 60%+ win rate with high returns
- Wants the BEST strategies from top quant teams worldwide
- **Wants real data, not synthetic** — all backtests now use Binance historical data
- **Wants proper backtesting** — walk-forward, no look-ahead bias
- **Thinks contrarian** — swim against the market, not mainstream approaches
- **Considers liquidity** — don't move the market when trading
- **Focus on post-ETF era** — crypto market structure has shifted
