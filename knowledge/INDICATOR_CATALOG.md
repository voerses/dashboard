# Indicator Catalog for Crypto Swing Trading

> **TL;DR -- All indicators ranked by usefulness**
> - Tier 1: EMA 12/26/50 (trend), ADX (strength, IC=0.067), RSI (extremes only with regime filter)
> - Volume analysis: vol_ratio for confirmation, taker buy ratio for order flow, OBV for accumulation
> - Redundant pairs to avoid: RSI <-> BB_pct (95% corr), realized_vol <-> parkinson_vol (99% corr)
> - Crypto-specific: funding rate divergence, liquidation cascades, exchange netflows
> **When to read full file:** Building new entry/exit filters, checking if an indicator exists in the engine
> **Formula reference:** `knowledge/archive/INDICATOR_CATALOG_FORMULAS.md` (sections 1-4: trend, oscillators, volume, volatility)

**System context:** 1H timeframe, 18-720 hour holding periods, cryptocurrency markets.

---

## 5. Market Microstructure

### Bid-Ask Spread Estimators

| Indicator | Formula (key) | 1H Suitability | Signal Type | Use |
|-----------|---------------|----------------|-------------|-----|
| Roll Spread | `2 * sqrt(-Cov(r_t, r_{t-1}))` when Cov<0 | LOW-MOD | FILTER, SIZING | Effective spread from trade data; better on 1min |
| Corwin-Schultz | Derived from H-L range ratios (1-bar vs 2-bar) | MOD-HIGH | FILTER, SIZING | OHLC-based spread; good for altcoin liquidity screening |
| Abdi-Ranaldo | `S^2 = 4*Cov(ln(C)-ln(mid), ln(C_next)-ln(mid_next))` | MOD-HIGH | FILTER, SIZING | Modern OHLC spread estimator; cross-universe liquidity filter |

### Price Impact

| Indicator | Formula (key) | 1H Suitability | Signal Type | Use |
|-----------|---------------|----------------|-------------|-----|
| Kyle's Lambda | `delta_price / delta_volume` | MOD | FILTER, SIZING | Permanent price impact per unit flow |
| Amihud Illiquidity | `\|r_t\| / (Vol_t * Price_t)` | HIGH | FILTER, SIZING | Easy from OHLCV; rolling ratio identifies liquidity regimes |
| Sqrt Law Impact | `sigma * sqrt(Q/V)` | MOD | SIZING | Execution/slippage estimation, not signal generation |

### Crypto Microstructure

| Indicator | Thresholds | 1H Suitability | Signal Type | Key Insight |
|-----------|------------|----------------|-------------|-------------|
| Perpetual Funding Rate | Extreme: >0.1%/8h contrarian | HIGH | FILTER, EXIT, ENTRY | Strongest contrarian signal in crypto; monitor predicted funding |
| Liquidation Cascades | `liq_vol / total_vol` | HIGH | ENTRY (post-cascade), FILTER | Post-cascade = mean reversion; pre-cascade = directional risk |
| OI Concentration / Heatmaps | Max-pain levels, liquidation clusters | HIGH | ENTRY, EXIT, FILTER | Price magnets at max-pain; stop zones at liq clusters |
| VPIN | Bulk volume classification | MOD | FILTER, SIZING | Flow toxicity; spikes precede large moves |

---

## 6. Sentiment / Alternative Data

### On-Chain Indicators (BTC/ETH)

| Indicator | Signal Logic | Suitability | Use |
|-----------|-------------|-------------|-----|
| MVRV | >3.5 = top zone; <1 = capitulation | MOD (daily) | Macro regime filter: MVRV>2.5 reduce longs, <1.2 increase |
| SOPR | Bounce off 1.0 from below in bull = dip buy | MOD (daily) | Capitulation/profit-taking detector |
| NVT Ratio | NVT Signal >150 = overvaluation warning | LOW-MOD (daily) | Macro valuation filter only |
| Active Addresses | Declining AA + rising price = speculative divergence | LOW (daily) | Fundamental health check |
| Hash Rate / Ribbons | MA(30) cross above MA(60) = end of miner capitulation | LOW (daily) | BTC-specific bottom detection |

### Social / Sentiment

| Indicator | Signal Logic | Suitability | Use |
|-----------|-------------|-------------|-----|
| Fear & Greed Index | <20 = increase long bias; >80 = reduce exposure | MOD (daily) | Contrarian regime filter |
| Social Volume | Spike + falling price = panic (near bottom) | MOD (hourly from LunarCrush) | Contrarian filter; social + price divergence |
| Weighted Sentiment | Extreme negative = capitulation; extreme positive = euphoria | MOD (daily) | Contrarian at extremes |
| News NLP Sentiment | Rapid sentiment shift = regime change signal | MOD-HIGH (real-time) | Filter or volatility anticipation |

### Derivatives Sentiment

| Indicator | Signal Logic | Suitability | Use |
|-----------|-------------|-------------|-----|
| Put-Call Ratio | High PCR = contrarian bullish; low = contrarian bearish | MOD (daily) | Contrarian filter (growing relevance as crypto options mature) |
| Perpetual Basis | Extreme positive basis = crowded longs; normalization drives moves | HIGH (real-time) | Mean-reversion signal; monitor across BTC/ETH/alts |

---

## 7. Statistical / Econometric Indicators

### Returns-Based Features

| Indicator | Formula | 1H Suitability | Use |
|-----------|---------|----------------|-----|
| Multi-Horizon Returns | `r(k)` for k=1,4,12,24,48,168,336,720h | HIGH | Foundational ML features; multi-scale market state |
| Log Returns | `ln(C_t / C_{t-1})` | HIGH | All statistical computations; convert to simple for P&L |
| Excess Returns vs BTC | `r_asset - r_BTC` over period k | HIGH | Cross-sectional ranking; relative momentum |

### Distribution Measures

| Indicator | Threshold / Window | 1H Suitability | Use |
|-----------|-------------------|----------------|-----|
| Rolling Skewness | 168-720h window; negative in uptrend = crash warning | HIGH | Regime filter, position sizing, exit signal |
| Rolling Kurtosis | 168-720h window; high = reduce size, widen stops | HIGH | Tail risk; kurtosis expansion precedes major moves |
| CVaR (5%) | 720h window; avg of returns below 5th percentile | HIGH | Max position sizing; drawdown budget allocation |
| EVT / GPD | xi>0 = fat tails (crypto always); 720h+ window | MOD | Extreme stop-loss levels; stress-test position sizes |

### Correlation / Dependence

| Indicator | Window / Use | 1H Suitability | Key Insight |
|-----------|-------------|----------------|-------------|
| Rolling Correlation | 168-720h; intra-crypto (BTC vs alts) | HIGH | High corr = risk-off, low = alt-season; portfolio construction |
| DCC-GARCH | Daily estimate, interpolate to hourly | MOD-HIGH | Time-varying correlation for multi-asset portfolios |
| Copula Tail Dependence | Daily/4H; lower tail coefficient | MOD | Crypto has stronger lower tail dependence (contagion risk) |

### Regime Detection

| Indicator | States / Method | 1H Suitability | Use |
|-----------|----------------|----------------|-----|
| HMM | 2-3 states (bull/bear/sideways) via Baum-Welch | HIGH | Strategy switching: momentum in bull, mean-revert in range |
| BOCPD | Online change-point detection via Bayesian message-passing | HIGH | Real-time regime change detection; triggers risk reduction |
| Markov Switching | 2-state (high-vol/low-vol) with parametric returns | HIGH | Smoothed state probabilities for regime identification |

---

## 8. Causal / Lead-Lag Indicators

### Statistical Causality

| Indicator | Method | 1H Suitability | Use |
|-----------|--------|----------------|-----|
| Granger Causality | F-test on VAR; rolling window re-estimation | HIGH | BTC->alt at 1-24h lags; leading signal for alt entries |
| Transfer Entropy | Non-parametric info flow; 100-500 bar windows | MOD-HIGH | Nonlinear lead-lag; directional info flow graph |
| Cross-Correlation | `CCF(X,Y,k)` at lags -24..+24h | HIGH | Peak lag = positioning delay; some alts lag BTC 1-4h |

### Cross-Asset Leading Indicators

| Indicator | Signal Logic | 1H Suitability | Key Insight |
|-----------|-------------|----------------|-------------|
| ETH/BTC Ratio | Rising = risk-on/alt-season; falling = flight to BTC | HIGH | First stop in capital rotation; leads broader alt moves |
| BTC Dominance | Falling below 40% = alt-season signal | MOD (daily) | Market cycle position indicator |
| DXY | Negative corr with BTC (when regime active) | HIGH (hourly) | Macro context; sharp DXY strengthening = reduce crypto |
| US10Y Yield | Rising real yields = negative for crypto | MOD-HIGH (hourly during trading hours) | Rate shock events create immediate crypto vol |
| SPX Correlation | 720h rolling BTC-SPX corr | HIGH | High corr = macro-driven; low corr = crypto-specific factors |

---

## 10. Empirical Results (49-Token Signal Lab Analysis)

*Source: Signal Lab IC analysis across 49 tokens, 2024-01-01 to 2026-01-31 (762 days)*

### IC Rankings (Forward Return Predictability)

| Rank | Indicator | Avg |IC| | Verdict | Notes |
|------|-----------|---------|---------|-------|
| 1 | **ADX** | 0.067 | PREDICTIVE | IC increases with horizon (0.05->0.08) |
| 2 | realized_vol | 0.053 | PREDICTIVE | Leading indicator of forward returns |
| 3 | BB_width | 0.039 | WEAK | Consistent across horizons |
| 4 | vol_ratio | 0.027 | WEAK | Decays with horizon |
| 5 | taker | 0.021 | WEAK | Useful at 1d only (+0.035), gone by 5d |
| 16 | RSI | 0.004 | NOISE | Useless standalone despite popularity |

### Redundant Pairs (|corr| > 0.7) -- Drop One

| Pair | Corr | Keep |
|------|------|------|
| RSI <-> BB_pct | 0.952 | RSI |
| realized_vol <-> parkinson_vol | 0.992 | realized_vol |
| taker <-> taker_buy_ratio | 1.000 | taker |
| ret_1 <-> vwap_deviation | 0.827 | ret_1 |
| ATR_pct <-> vol_20 | 0.805 | ATR_pct |

### Truly Independent Signals (max |corr| < 0.5 with all others)

VPIN (0.310), amihud_1m (0.337), intraday_skew (0.323), intraday_kurtosis (0.310)

### Best Indicator by Regime

| Regime | Best Indicator | IC | Key Insight |
|--------|---------------|-----|-------------|
| Uptrend | ATR_pct | -0.072 | Low vol predicts continuation; momentum works WITH trend |
| Downtrend | **RSI** | **-0.145** | High RSI bounces = sell signals (opposite of textbook!) |
| Range | intraday_kurtosis | -0.072 | Microstructure beats traditional indicators |
| Quiet | ret_1 | +0.074 | Short-term momentum + order flow dominant |

### Top Actionable Combinations (2-indicator)

| Combo | Mean Ret | WR | Tokens | Use |
|-------|----------|-----|--------|-----|
| RSI_low + MACD_pos | +1.37% | 58.3% | 27 | LONG: dip buy + momentum confirm |
| vol_ratio_hi + BB_pct_low | +0.99% | 58.4% | 45 | LONG: capitulation buy (most robust) |
| vol_ratio_hi + ret_neg | +0.92% | 56.2% | 49 | LONG: volume spike reversal (universal) |
| ADX_weak + MACD_neg | -1.58% | 37.0% | 29 | AVOID: no trend + negative momentum |

---

## 11. Implementation Priority Matrix

For a 1H crypto swing trading system (18-720 hour holds), prioritize by impact and feasibility.

### Tier 1: Essential (Implement First)

| Indicator | Category | Rationale |
|-----------|----------|-----------|
| Multi-horizon returns | Statistical | Foundational feature for any system |
| ATR | Volatility | Position sizing and stop placement |
| EMA crossover / HMA | Trend | Primary trend identification |
| RSI | Oscillator | Mean-reversion entry/exit |
| Supertrend | Trend | Clean trend signals for crypto |
| Bollinger Squeeze | Volatility | High-probability breakout entries |
| OBV / Delta Volume | Volume | Volume confirmation of moves |
| Hurst Exponent | Statistical | Regime classification (trend vs mean-revert) |
| Funding Rate | Crypto-specific | Crowded positioning detection |
| Rolling Volatility (GK) | Volatility | Accurate vol for sizing |

### Tier 2: High Value (Implement Second)

| Indicator | Category | Rationale |
|-----------|----------|-----------|
| TSMOM (multi-horizon) | Momentum | Research-backed momentum signal |
| KAMA | Trend | Adaptive trend following |
| MFI | Volume | Volume-weighted momentum |
| HMM Regime | Statistical | Strategy selection framework |
| GARCH(1,1) | Volatility | Conditional vol forecast |
| Amihud Illiquidity | Microstructure | Liquidity filter |
| Open Interest momentum | Crypto-specific | Derivatives positioning |
| ETH/BTC ratio | Lead-lag | Alt-season detection |
| Cross-correlation lags | Lead-lag | BTC-alt lead-lag exploitation |
| Skewness / Kurtosis | Statistical | Tail risk management |

### Tier 3: Alpha Enhancement (Implement Third)

| Indicator | Category | Rationale |
|-----------|----------|-----------|
| Cross-sectional momentum | Momentum | Universe ranking |
| VPIN | Microstructure | Flow toxicity detection |
| BOCPD | Statistical | Real-time regime change detection |
| Taker Buy Ratio | Crypto-specific | Order flow aggression |
| Liquidation cascades | Crypto-specific | Forced-flow opportunities |
| DXY / US10Y | Macro | Macro regime filter |
| CVaR / EVT | Statistical | Tail-aware risk management |
| Transfer Entropy | Lead-lag | Nonlinear information flow |
| HAR-RV | Volatility | Multi-scale vol forecast |
| Corwin-Schultz spread | Microstructure | Liquidity screening |

### Tier 4: Supplementary (Optional)

| Indicator | Category | Rationale |
|-----------|----------|-----------|
| MVRV / SOPR / NVT | On-chain | Slow-moving macro filters |
| Social sentiment | Alternative | Contrarian filter |
| Fear & Greed Index | Alternative | Regime context |
| DCC-GARCH | Correlation | Dynamic correlation modeling |
| Copula dependence | Correlation | Tail dependence |
| MESA / Ehlers filters | Trend | Advanced cycle analysis |
| News NLP sentiment | Alternative | Event-driven filter |
| Hash Rate / Hash Ribbons | On-chain | BTC-specific bottom detection |
