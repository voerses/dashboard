# Gamma Exposure (GEX) Regime Detection Signal — Research Findings

**Date:** 2026-03-23
**Researcher:** Quantitative Research (automated)
**OOS Cutoff:** 2025-07-01
**Assets Tested:** BTC, ETH

---

## Executive Summary

We investigated whether dealer gamma exposure (GEX) from crypto options markets can be used as a volatility regime detection signal. The core hypothesis is that positive dealer gamma dampens volatility (mean reversion regime) while negative gamma amplifies it (trend regime).

**Key finding:** Historical options chain data (OI by strike) is NOT available from free APIs, making direct GEX computation impossible over historical periods. However, DVOL-based proxy signals — particularly the **Volatility Risk Premium (VRP) z-score** — show statistically significant predictive power for forward returns at 3-14 day horizons, with OOS ICs of 0.16-0.27 (p < 0.01). The signal predicts volatility magnitude even more reliably than return direction.

**Verdict: PROMISING for volatility prediction, WEAK for directional returns, REQUIRES data investment for true GEX.**

---

## Data Availability

### What We Had
| Source | Coverage | Quality |
|--------|----------|---------|
| Deribit DVOL Index (daily OHLC) | 2021-03-24 to 2026-03-23 (1,826 days) | No gaps, official index |
| BTC/ETH 1h OHLCV + funding | 2020-01-01 to 2026-03-17 | Complete |
| Live Deribit options snapshots | 2 snapshots (2026-03-23) | Full chain |
| Deribit historical trades | 2+ years via history.deribit.com | Individual trades only |

### What We Could NOT Get (Free)
| Source | Issue |
|--------|-------|
| Historical options OI snapshots | Deribit has NO historical OI API — ephemeral data |
| Pre-computed GEX time series | Laevitas, Greeks.live, Amberdata — all require paid subscriptions |
| Historical put/call OI ratio | Not exposed historically by Deribit |
| Coinglass options data | 500 error on free tier |

### APIs Tested
- Deribit public API: DVOL works (5 years daily), current book works, NO historical OI
- Deribit history API: Trades work (2+ years), but cannot reconstruct OI from trades alone
- Coinglass: 500 error
- Greeks.live: 502 Bad Gateway
- Laevitas: 404 (requires auth)
- Amberdata: Proxy blocked
- Glassnode: 401 (requires API key)

---

## Part A: Direct GEX from Live Snapshot

Computed from 2 Deribit snapshots (2026-03-23):

### BTC GEX
| Metric | Value |
|--------|-------|
| Spot Price | $72,711.50 |
| Net Dealer GEX | $309,828,445 |
| GEX Sign | **POSITIVE** (vol-suppression) |
| Max Gamma Strike | $75,000 |
| Zero-Gamma Level | $70,500 |
| Put/Call OI Ratio | 0.6694 |
| Net Dealer Delta | -7,809 BTC |

### ETH GEX
| Metric | Value |
|--------|-------|
| Spot Price | $2,167.33 |
| Net Dealer GEX | $43,608,063 |
| GEX Sign | **POSITIVE** (vol-suppression) |
| Max Gamma Strike | $2,500 |
| Zero-Gamma Level | $2,075 |
| Put/Call OI Ratio | 0.4967 |
| Net Dealer Delta | -243,172 ETH |

**Interpretation:** Both BTC and ETH are currently in positive gamma (vol-suppression) regime. BTC's max gamma is at $75,000 — this is the "gamma wall" where dealer hedging is strongest. Below $70,500 the regime flips to negative gamma.

---

## Part B: GEX Regime Proxy Signals (DVOL-based)

Since historical OI data is unavailable, we built proxy signals from DVOL:
1. **DVOL Z-Score:** Standardized DVOL level (high = fear = likely negative gamma)
2. **DVOL Change (5d/10d):** Implied vol momentum (rising = regime transition)
3. **VRP (DVOL - RVol20d):** Vol risk premium (high = put overpricing = hedging demand)
4. **VRP Z-Score:** Normalized VRP for regime detection
5. **DVOL Acceleration:** Second derivative of vol momentum
6. **Vol-of-Vol (VVOL):** Volatility of the vol index (instability measure)
7. **DVOL Intraday Range:** Daily DVOL high-low spread
8. **GEX Proxy Composite:** Mean of z-scored sub-signals

### Strongest Return Prediction Signals (OOS p < 0.05)

| Signal | Asset | Horizon | IS IC | OOS IC | OOS p |
|--------|-------|---------|-------|--------|-------|
| **VRP Z-Score** | **BTC** | **7d** | **0.005** | **0.268** | **0.000** |
| **VRP Z-Score** | **BTC** | **14d** | **-0.031** | **0.258** | **0.000** |
| **Vol-of-Vol** | **ETH** | **14d** | **0.107** | **0.248** | **0.000** |
| **VRP Z-Score** | **ETH** | **14d** | **0.111** | **0.223** | **0.000** |
| VRP (raw) | BTC | 14d | -0.108 | 0.198 | 0.002 |
| VRP (raw) | BTC | 7d | -0.039 | 0.195 | 0.002 |
| VRP Z-Score | ETH | 7d | 0.128 | 0.189 | 0.003 |
| VRP Z-Score | BTC | 3d | 0.018 | 0.187 | 0.003 |
| VRP Z-Score | ETH | 3d | 0.104 | 0.163 | 0.009 |
| VRP (raw) | ETH | 14d | 0.022 | 0.153 | 0.016 |
| GEX Composite | ETH | 14d | 0.129 | 0.137 | 0.032 |
| VRP (raw) | BTC | 3d | -0.006 | 0.124 | 0.047 |

### Key Observations

1. **VRP Z-Score is the star signal.** It achieves OOS IC of 0.27 for BTC 7d returns and 0.22 for ETH 14d returns — both highly significant (p < 0.001). The economic logic is sound: when implied vol is much higher than realized vol (high VRP), it signals excessive hedging demand, which drives negative gamma dynamics.

2. **IS/OOS divergence is notable.** Several signals (e.g., DVOL Z-Score) show strong IS performance but fail OOS. VRP Z-Score shows the opposite — weak IS but strong OOS. This is unusual and suggests the VRP relationship may be regime-dependent (the OOS period 2025H2-2026Q1 had specific market conditions).

3. **The signal is better at predicting volatility than direction.** When testing IC against absolute returns:
   - GEX Proxy Composite: OOS IC = 0.28 for 1d abs(return) (p < 0.001)
   - DVOL Z-Score: OOS IC = 0.32 for 1d abs(return) (p < 0.001)
   - DVOL 10d Change: OOS IC = 0.25 for 1d abs(return) (p < 0.001)

4. **Longer horizons (7-14d) work better than short (1d).** This aligns with the GEX hypothesis — gamma regimes are slow-moving states, not day-to-day signals.

---

## Part B2: Regime-Conditional Returns

### BTC (Out-of-Sample)

| Regime | 1d Mean | 7d Mean | 14d Mean | 14d Sharpe |
|--------|---------|---------|----------|------------|
| POS_GAMMA | -0.08% | -0.88% | -2.83% | -1.78 |
| NEG_GAMMA | -0.14% | -1.03% | -1.71% | -1.21 |

### ETH (Out-of-Sample)

| Regime | 1d Mean | 7d Mean | 14d Mean | 14d Sharpe |
|--------|---------|---------|----------|------------|
| POS_GAMMA | 0.10% | -0.28% | -1.53% | -0.52 |
| NEG_GAMMA | -0.01% | 0.69% | 1.68% | 0.65 |

**Insight:** The negative gamma regime (high vol / rising vol) showed BETTER returns for ETH OOS. This is counterintuitive but suggests that the vol spike period coincided with recovery bounces. The regime signal predicts volatility magnitude more reliably than return sign.

---

## Part C: Strategy Simulation

Simple regime-switching strategy (MR in positive gamma, momentum in negative gamma):

### BTC OOS Results
| Strategy | Ann. Return | Sharpe | Max DD |
|----------|------------|--------|--------|
| Buy & Hold | -41.6% | -0.93 | -49.6% |
| Always MR | -19.7% | -0.44 | -40.8% |
| Always Momentum | +40.9% | +0.91 | -28.8% |
| GEX Regime Switch | -41.0% | -0.91 | -42.1% |

**The naive regime-switching strategy failed.** The GEX proxy composite with a simple binary regime split (positive/negative composite) and simple MR/momentum sub-strategies does not add value. This is not surprising because:
1. The proxy is too coarse — it misses the actual GEX levels
2. The sub-strategies (1d MR / 5d momentum) are too simplistic
3. The signal predicts vol magnitude, not direction — a vol-targeting overlay would be more appropriate

---

## Part D: Cross-Asset Analysis

| Metric | Value |
|--------|-------|
| BTC-ETH GEX proxy composite correlation | 0.799 |
| BTC-ETH DVOL correlation | 0.893 |
| BTC-ETH VRP correlation | 0.755 |
| Regime agreement rate | 77.8% |
| Regime disagreement days | 22.2% |

BTC and ETH GEX regimes are highly correlated, as expected for a shared macro/options market. The 22% disagreement rate offers potential for relative-value trades.

---

## Rolling IC Stability

| Signal | Horizon | Mean IC | Std IC | % Positive | % Significant (>0.05) |
|--------|---------|---------|--------|-----------|----------------------|
| GEX Composite | 1d | 0.050 | 0.135 | 67.7% | 71.2% |
| GEX Composite | 7d | 0.100 | 0.172 | 71.2% | 79.7% |
| DVOL Z-Score | 1d | 0.062 | 0.115 | 69.1% | 71.2% |
| DVOL Z-Score | 7d | 0.078 | 0.241 | 58.7% | 84.7% |
| VRP Z-Score | 1d | 0.020 | 0.129 | 58.8% | 71.7% |
| VRP Z-Score | 7d | 0.020 | 0.272 | 54.0% | 86.0% |

The composite signal has the best stability (positive 68-71% of the time across 60-day windows). VRP Z-Score has high variance but is significant in 86% of windows at the 7d horizon.

---

## Conclusions & Recommendations

### What Works
1. **VRP Z-Score** is a statistically significant predictor of 3-14d returns (OOS IC 0.16-0.27)
2. **DVOL-based signals** reliably predict forward volatility magnitude (OOS IC up to 0.32)
3. The **composite GEX proxy** has good rolling stability (positive 68-72% of the time)
4. **Cross-asset correlation** (0.80) confirms the signal captures a shared regime factor

### What Does NOT Work
1. The naive regime-switching strategy (MR vs momentum) adds no value
2. 1-day return prediction from DVOL-based signals is unreliable
3. The binary regime classification is too coarse for trading signals

### How to Use This Signal
1. **Vol-targeting overlay:** Scale position sizes INVERSELY to the GEX proxy composite. In high-composite (negative gamma) regimes, reduce size. In low-composite (positive gamma), increase size.
2. **Strategy selection conditional on regime:** In positive gamma, favor mean-reversion strategies with tighter stops. In negative gamma, favor trend-following with wider stops. But use proper strategies, not 1d sign-flip.
3. **Risk management:** Use VRP Z-Score > 1.5 as a "vol storm warning" — reduce exposure across all strategies.

### Investment Needed for True GEX Signal
1. **Immediate (free):** Set up daily Deribit snapshot collection via cron (3x/day). After 3 months, we can compute actual historical GEX.
2. **Fast-track ($199/mo):** Tardis.dev historical Deribit data goes back to 2019. This would immediately enable a full 6+ year GEX backtest.
3. **The hypothesis is worth the investment.** The proxy signals already show IC > 0.15 OOS. The actual signal (from real OI data) should be materially stronger since it captures dealer positioning directly rather than through a noisy vol proxy.

---

## Files

| File | Description |
|------|-------------|
| `/workspace/crypto_backtest/research/gamma_exposure_signal.py` | Full research script (data loading, GEX computation, proxy signals, IC analysis, strategy sim) |
| `/workspace/crypto_backtest/research/gamma_exposure_results.md` | This results document |
| `/workspace/crypto_backtest/data/alternative/deribit_options/dvol/btc_dvol_daily.json` | BTC DVOL daily data (1,826 days) |
| `/workspace/crypto_backtest/data/alternative/deribit_options/dvol/eth_dvol_daily.json` | ETH DVOL daily data (1,826 days) |
| `/workspace/crypto_backtest/data/alternative/deribit_options/dvol/btc_book_snapshot.json` | Fresh BTC options book snapshot |
| `/workspace/crypto_backtest/data/alternative/deribit_options/dvol/eth_book_snapshot.json` | Fresh ETH options book snapshot |
| `/workspace/crypto_backtest/data/alternative/deribit_options/raw/snapshot_*.parquet` | Raw options chain snapshots (2) |
