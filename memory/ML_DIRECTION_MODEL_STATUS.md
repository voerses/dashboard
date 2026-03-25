# ML Direction Model — Development Status

> **Last updated:** 2026-03-23
> **Status:** ALL ML direction models DEAD. Microstructure (Exp D) had inflated OOS precision — real edge ~8pp, eaten by costs. Pivot to ML-as-overlay.
> **Active experiments:** NONE — all killed. See "Next Steps" for pivot plan.

## Definitive Conclusion (2026-03-23)

**ML direction prediction CANNOT generate tradeable alpha in crypto, regardless of feature set.**

Tested exhaustively across:
- **Feature sets:** TA (41 features), enhanced TA (45 features), microstructure from 1m data (16 features), cross-sectional ranks (13 features)
- **Training approaches:** Full history, recent-only (18mo), walk-forward (12mo rolling), regime-specialist
- **Horizons:** 4h, 8h, 12h, 24h
- **Targets:** 1.5%, 2%, 3% thresholds

**Result: ZERO approaches produce tradeable OOS alpha after costs.**

---

## V1-V4: TA Feature Models (ALL DEAD)

**Root cause:** TA features are lagged transforms of price containing no future information for efficient markets.

| Model | In-Sample | OOS Precision | Issue |
|-------|-----------|---------------|-------|
| V2 (41 features) | ~80% | 36.7% long, 48.4% short | Row-index split = 100% temporal leakage |
| V3 (3-fold temporal, 48h embargo) | ~60% | Similar to random | Autocorrelation leaks through short embargo |
| V4 (+ LOTO cross-token) | ~60-62% | Similar to random | Same embargo issue |
| V5 (45 features, +dispersion) | N/A | 33.9% long, 41.9% short | Enhanced features made it WORSE |

### Experiment A: Recent-Only Training
- **Script:** `tools/ml_exp_a_recent.py`
- **Train on Jan 2024 - June 2025 only** (post-ETF)
- **4 horizons:** 4h/1.5%, 8h/1.5%, 12h/2%, 24h/3%
- **Result:** All ~50% = random. Old data isn't the problem.

### Experiment B: Walk-Forward Retraining
- **Script:** `tools/ml_exp_b_walkforward.py`
- **12-month rolling, retrained every 3 months**
- **Result:** Long random (44-51%), short marginal (50-58%). Even monthly retraining can't fix it.

### Experiment C: Regime-Specialist Models
- **Script:** `tools/ml_exp_c_regime_specialist.py`
- **4 dispersion-breadth regimes** (DIVERGENT_BULL/BEAR, CORRELATED_BULL/BEAR)
- **Result:** All specialists below regime base rates.

---

## Experiment D: Microstructure ML (DEAD — Inflated Precision)

- **Script:** `tools/ml_exp_d_microstructure.py`
- **Model saved:** `results/v5/ml_exp_d_best_model.joblib`
- **Features (16):** 10 micro (VPIN, realized_vol_ratio, kyle_lambda, amihud, etc.) + 5 TA (ret_24h, vol_20, rsi_14, funding, btc_ret_24h) + vpin_4h

### Claimed vs Real Precision

| Threshold | Experiment Claim | Real (All Bars) | Backtest WR |
|-----------|-----------------|-----------------|-------------|
| Short p>=0.65 | **60.6%** (388 trades) | **53.4%** | **~43%** |
| Short p>=0.70 | **68.7%** (99 trades) | **57.9%** | **~43%** |

### Root Cause: Neutral Label Filtering Artifact
The experiment **dropped ~40% of bars** where |fwd_ret| < 1.5% before computing precision. This inflated results by evaluating only on big-move bars (where distinguishing direction is easier). Real precision on ALL bars: ~53-58%. After trading costs: ~43%.

### Strategy Backtest Results (ALL LOSING)

| Variant | Config | Return | Trades | WR | MaxDD | PF |
|---------|--------|--------|--------|----|-------|-----|
| s316 v1 | Bidir, 4x lev, no stops | **-99.0%** | 3210 | 43.6% | -99.0% | 0.60 |
| s316 v2 | Short, p>=0.70, stops, 2x | **-2.2%** | 95 | 43.2% | -8.2% | 0.91 |
| s317 | Short, p>=0.65, no stops, 2x | **-17.9%** | 494 | 43.3% | -28.9% | 0.84 |
| s318 | Bidir, no stops, 1x | **-86.8%** | 7925 | 42.7% | -87.1% | 0.75 |
| s319 | Short, p>=0.65, no stops, 3x | **-37.3%** | 494 | 42.3% | -48.6% | 0.74 |

**The model has a genuine but tiny ~8pp edge above 50% — completely eaten by trading costs (fees, slippage, adverse selection).**

---

## Experiment E: Cross-Sectional Relative Strength (DEAD)

- **Script:** `tools/ml_exp_e_cross_sectional.py`
- **Features (13):** rank-based (ret, vol, volume, rsi, funding, drawdown ranks) + market context
- **Result:** Top quintile 22.2% vs 23.2% base rate = no edge for winners. Bottom quintile 35.2% vs 19.9% = can identify losers, but not tradeable (would need shorting).

---

## Strategy Graveyard (ML Direction)

| Strategy | Status | Notes |
|----------|--------|-------|
| s312_ml_v2_long | **DEAD** | In-sample +5004%, OOS = negative alpha |
| s313_ml_v2_short | **DEAD** | In-sample +1513%, OOS = negative alpha |
| s314_ml_adaptive | **DEAD** | Portfolio strategy, -93.4%, feature mismatch |
| s315_ml_v4_bidir | **DEAD** | V4 model, -96.8%, cross-sectional feature mismatch |
| s316_micro_short | **DEAD** | Microstructure model, -2.2% to -99% across 5 variants |
| s317_micro_short_v2 | **DEAD** | Variant: short p>=0.65, no stops, 2x. -17.9% |
| s318_micro_bidir | **DEAD** | Variant: bidir, no stops, 1x. -86.8% |
| s319_micro_aggressive | **DEAD** | Variant: short p>=0.65, 3x. -37.3% |

---

## Key Lessons Learned

1. **TA features are non-predictive OOS** for liquid crypto at any horizon (4h-24h).
2. **Microstructure features have tiny real edge (~8pp)** but insufficient to overcome trading costs.
3. **Neutral label filtering inflates precision** — ALWAYS evaluate on ALL bars, not just big-move bars.
4. **Model precision ≠ trade win rate.** Precision drops ~10-15pp from frictionless evaluation to real trading.
5. **Direction prediction is the WRONG use of ML in crypto.** Practitioners use ML for regime detection, position sizing, timing, and execution — not price direction.
6. **Autocorrelation breaks short embargoes.** Need months of separation, not hours.
7. **In-sample metrics are meaningless.** Only strict temporal OOS with full-bar evaluation matters.
8. **Cross-asset transfer doesn't help** when the base features are non-predictive.

---

## File Index

| File | Purpose | Status |
|------|---------|--------|
| `tools/ml_v5_oos_train.py` | V5 strict OOS training | OVERFIT |
| `tools/ml_exp_a_recent.py` | Experiment A: recent-only | NO EDGE |
| `tools/ml_exp_b_walkforward.py` | Experiment B: walk-forward | NO EDGE |
| `tools/ml_exp_c_regime_specialist.py` | Experiment C: regime specialist | NO EDGE |
| `tools/ml_exp_d_microstructure.py` | Experiment D: microstructure | INFLATED PRECISION |
| `tools/ml_exp_e_cross_sectional.py` | Experiment E: cross-sectional | NO EDGE |
| `results/v5/ml_exp_d_best_model.joblib` | Exp D model artifact | DO NOT USE for direction trading |
| `strategies/s316_micro_short.py` | Microstructure short strategy | DEAD |
| `strategies/s317_micro_short_v2.py` | Variant: no stops | DEAD |
| `strategies/s318_micro_bidir.py` | Variant: bidirectional | DEAD |
| `strategies/s319_micro_aggressive.py` | Variant: 3x aggressive | DEAD |

---

## Next Steps: ML Pivot (for next session)

**ML direction prediction is conclusively dead. The right use of ML in crypto is NOT direction prediction.**

Based on comprehensive quant research (2025-2026), the actionable ML approaches are:

### Approach 1: ML-Optimized Funding Rate Carry (HIGHEST PRIORITY)
- **We already have s65 (funding carry) earning +4.9% post-MTM (12mo), actively paper trading (+2.25% in 7d)**
- ML optimization: predict funding rate persistence, optimize entry/exit timing, venue selection
- Expected improvement: improve returns on the only profitable strategy family we have
- Data: multi-exchange funding rates, OI, liquidation data
- Implementation: overlay on s65/s62, NOT a new direction model

### Approach 2: Liquidation Cascade Risk Model (RISK MANAGEMENT)
- Build HMM/GMM regime model using OI delta, funding z-score, exchange inflows, SOPR
- Classify: calm → heated → pre-cascade → cascade
- Use to REDUCE exposure before cascades (know when NOT to trade)
- Improves drawdown of ALL existing strategies
- Data: CoinGlass (OI, funding, liquidations), Glassnode (on-chain)

### Approach 3: LOB Microstructure at High Frequency
- Our 1m OHLCV microstructure features don't work because 1m is too coarse
- Real microstructure edge needs 100ms-1s LOB snapshots (order book imbalance, depth)
- Requires new data infrastructure (Tardis.dev, Binance L2 API)
- Horizon: seconds to minutes, not hours
- **Requires significant infrastructure investment — not quick win**

### Approach 4: CTREND Cross-Sectional Momentum (MOST RESEARCHED)
- ML-weighted aggregation of 28 technical signals for cross-token ranking
- LONG ONLY top decile (do NOT short — losers rebound in crypto)
- Weekly rebalance, walk-forward monthly retrain
- Academic evidence: Sharpe 3.12 after costs (LSTM/GRU ensemble)
- **Key insight: ML aggregates signals, doesn't predict direction directly**

### Approach 5: Options IV Skew for Regime Detection
- BTC options skew at D9 (extreme put demand) → +13% forward 90d, +133% forward 360d
- Use as regime classifier for strategy switching (momentum vs carry vs neutral)
- Data: Deribit options API, Glassnode interpolated IV
- Meta-strategy: select WHICH existing strategy to run based on vol regime

### Recommendation Priority Order
1. **ML overlay on s65 funding carry** (fastest path — improve proven strategy)
2. **Liquidation cascade risk model** (protect existing profits)
3. **CTREND cross-sectional** (new strategy class with strong evidence)
4. **Options IV regime switching** (meta-strategy)
5. **LOB microstructure** (infrastructure-heavy, long-term)

---

## Existing Profitable Strategies (Context for ML Overlay)

These strategies are ALREADY working and should be the BASE for ML improvements:

| Strategy | Type | Post-MTM 12mo | MaxDD (MTM) | Status |
|----------|------|---------------|-------------|--------|
| s56 | Momentum burst perp | -27.4% | -38.5% | LOSING |
| s57 | Signal-timed carry | -29.3% | -29.2% | LOSING |
| s58 | Multi-strategy portfolio | -32.5% | -37.0% | LOSING |
| s65 | Funding carry V4 | +4.9% | -45.8% | PAPER TRADING (positive) |
| s62 | Conservative carry | +9.6% | -29.7% | PAPER TRADING (positive) |
| s60 | Momentum burst perp V4 | -70.5% | -81.3% | DEAD |
| s80 | Cross-sectional momentum | N/A | N/A | Paper only, losing |
| s81 | Sector rotation | N/A | N/A | Paper only, losing |
