# Data Gap Analysis: Available vs. Research-Recommended Data Sources

*Generated: 2026-03-01*
*Cross-references: DATA_MANIFEST.md, CRYPTO_MICROSTRUCTURE_POST_ETF.md, SIGNAL_DISCOVERY_METHODS.md, ONCHAIN_PRACTICAL_GUIDE.md, ONCHAIN_DERIVATIVES_CATALOG.md*

---

## Currently Available Data

From `DATA_MANIFEST.md`, our backtest system contains:

| Dataset | Granularity | Coverage | Period |
|---------|-------------|----------|--------|
| 1H OHLCV + volume fields | 1-hour | 49 tokens | Jan 2024 - Jan 2026 |
| 4H OHLCV (derived from 1H) | 4-hour | 49 tokens | Jan 2024 - Jan 2026 |
| 1M microstructure features (VPIN, realized vol, etc.) | Daily aggregates from 1-min | 49 tokens | Jan 2024 - Jan 2026 |
| Enriched parquet (12 features) | Daily | 57 tokens | Dec 2017 - Feb 2026 |
| Daily OHLCV CSVs | Daily | 57 tokens | Varies by token |

**Available features in enriched data:** vpin, realized_vol, taker_buy_ratio, amihud_1m, vwap_deviation, intraday_skew, intraday_kurtosis, parkinson_vol, volume_herfindahl, max_intraday_dd, trade_count, realized_var.

**What we have:** Price data (multi-timeframe), volume data, and Binance-derived microstructure features. All data is exchange-level (Binance only), spot-market only, and purely technical/microstructural.

**What we lack entirely:** On-chain data, derivatives data, ETF flow data, options data, sentiment data, macro data, cross-exchange data, and alternative data.

---

## High-Priority Missing Data

Data that research explicitly identifies as having strong predictive value for our 1H swing trading system, supported by multiple sources.

### 1. ETF Flow Data (Daily Creation/Redemption)

- **Research evidence:** CRYPTO_MICROSTRUCTURE_POST_ETF.md states: "ETF flow data is now the single most important BTC signal -- more explanatory than traditional crypto variables" (Mazur & Polyzos study). Previous-day ETF inflows show positive price impact (coefficient: 0.027) with strong persistence (0.533). Impulse response peaks at days 3-4.
- **Priority signal rank:** #1 in the Post-ETF Signal Priority Matrix.
- **Specific need:** Daily net flows for IBIT (BlackRock), FBTC (Fidelity), and aggregate BTC/ETH ETF flows.
- **Sources:** Bloomberg Terminal, CoinGlass ETF tracker, ETF.com, SoSoValue.
- **Cost:** CoinGlass free tier has some; Bloomberg is expensive; SoSoValue has free daily data.
- **Expected impact:** Strong -- research shows 1-5 day predictive horizon with very high conviction.

### 2. Perpetual Futures Funding Rate (8h, per token)

- **Research evidence:** Ranked Tier 1 in ONCHAIN_DERIVATIVES_CATALOG.md (DV-01, DV-03). Funding rate Z-score > 2 is an "excellent contrarian entry signal." FR persistence + OI ranked #3 in Post-ETF Signal Priority Matrix.
- **Specific need:** 8-hourly funding rates for all 49 tokens on Binance; aggregated cross-exchange FR from Coinglass.
- **Sources:** Binance API (free, `GET /fapi/v1/fundingRate`), Coinglass (free).
- **Cost:** Free.
- **Expected impact:** Core swing trading signal for contrarian entries at positioning extremes.

### 3. Open Interest (Absolute + Change Rate)

- **Research evidence:** Ranked Tier 1 in ONCHAIN_DERIVATIVES_CATALOG.md (DV-07, DV-08). "Essential context for all other signals." OI-price relationship reveals who is driving price moves. Rapid OI buildup (>5% in 24h) is a cascade risk warning.
- **Specific need:** Hourly OI snapshots per token, aggregated across exchanges.
- **Sources:** Coinglass API (free), Binance API (free).
- **Cost:** Free.
- **Expected impact:** Critical for position sizing and cascade risk detection.

### 4. Liquidation Data (Volume, Long/Short Split)

- **Research evidence:** Ranked Tier 1 in ONCHAIN_DERIVATIVES_CATALOG.md (DV-15, DV-16). "Post-liquidation-cascade entries are one of the highest-quality swing signals. After a $200M+ liquidation cascade, price often bounces 3-8% within 24-48 hours." Long Liq Dominance > 80% has historically marked local bottoms.
- **Specific need:** Hourly liquidation volumes (long vs. short) per token.
- **Sources:** Coinglass API (free), exchange WebSocket APIs (free).
- **Cost:** Free.
- **Expected impact:** High -- directly identifies exhaustion points for swing entries.

### 5. Options Data (IV, Skew, Put-Call Ratio, DVOL)

- **Research evidence:** Ranked Tier 1-2 in ONCHAIN_DERIVATIVES_CATALOG.md (DV-24, DV-25, DV-27, DV-32). IV/RV > 1.5 is a fear extreme / contrarian long entry. Options skew ranked #5 in Post-ETF Signal Priority Matrix. GEX (DV-30) determines whether market amplifies or suppresses volatility.
- **Specific need:** BTC/ETH implied volatility (25-delta ATM at 7d, 30d, 90d), put-call ratio, 25-delta risk reversal, DVOL index.
- **Sources:** Deribit API (free).
- **Cost:** Free.
- **Expected impact:** Strong for volatility regime detection and contrarian entry timing.

### 6. Stablecoin Supply Data (USDT/USDC Mint/Burn, Exchange Flows)

- **Research evidence:** Ranked #2 in Post-ETF Signal Priority Matrix. ONCHAIN_PRACTICAL_GUIDE.md ranks it Tier 1 (#3). "300M USDC mint in late 2023 preceded significant BTC rally." Stablecoin supply expanded $1.32B to $268.1B in early 2026, correlating with continued bull trend.
- **Specific need:** Daily stablecoin total supply, supply change, and stablecoin exchange netflows.
- **Sources:** DefiLlama API (free for supply), BGeometrics (partial), CryptoQuant (paid for exchange flows).
- **Cost:** Free for supply; paid for exchange-level flow detail.
- **Expected impact:** Moderate-high as broad market liquidity regime filter.

### 7. MVRV Z-Score / MVRV Ratio (BTC)

- **Research evidence:** Ranked 9/10 in ONCHAIN_PRACTICAL_GUIDE.md priority ranking. "The single highest-value on-chain signal for BTC." Z > 7 has preceded every major top. Z < 0 has preceded every major bottom. Expected impact: +0.05 to +0.15 Sharpe improvement via position sizing.
- **Specific need:** Daily MVRV Z-Score for BTC (and ETH if available).
- **Sources:** BGeometrics API (free), Glassnode (paid for API).
- **Cost:** Free via BGeometrics; $29/mo via Glassnode Tier 2 for reliable API.
- **Expected impact:** High for cycle-level position sizing.

### 8. Exchange Netflows (BTC/ETH)

- **Research evidence:** Ranked 8/10 in ONCHAIN_PRACTICAL_GUIDE.md, Tier 1 in catalog (OC-21, OC-22). "Large exchange inflows can precede sell-offs within hours/days." Single-day netflow of +10,000 BTC has preceded corrections 70%+ of the time.
- **Specific need:** Daily (ideally hourly) BTC and ETH exchange netflows.
- **Sources:** BGeometrics (free, daily), CryptoQuant (paid for hourly).
- **Cost:** Free for daily; $39-199/mo for hourly from CryptoQuant.
- **Expected impact:** Expected -5% to -15% max drawdown improvement as distribution filter.

---

## Medium-Priority Missing Data

Data with demonstrated predictive value but either narrower applicability, slower update frequency, or higher cost relative to signal value.

### 9. Long/Short Ratio & Top Trader Positioning

- **Research evidence:** Tier 2 in catalog (DV-12, DV-13). Top trader ratio is "more reliable than aggregate LS ratio" -- smart money divergence from retail is a strong signal.
- **Sources:** Binance API (free).
- **Cost:** Free.
- **Expected impact:** Moderate contrarian signal at extremes.

### 10. Taker Buy/Sell Ratio (Futures)

- **Research evidence:** Tier 1 in catalog (DV-14). "One of the most directly actionable derivatives signals for swing trading." Available at sub-hourly frequency.
- **Sources:** Binance API (free).
- **Cost:** Free.
- **Expected impact:** Moderate -- direct order flow aggression signal.

### 11. Futures Basis (Annualized, Spot-Futures Premium)

- **Research evidence:** Tier 1 in catalog (DV-20). Basis > 25% = overheated. Basis < 5% = underpositioned. "Most direct measure of directional sentiment from sophisticated traders."
- **Sources:** Exchange APIs (free), Coinglass (free).
- **Cost:** Free.
- **Expected impact:** Strong regime signal for institutional sentiment.

### 12. Fear & Greed Index (Crypto)

- **Research evidence:** Tier 1 in catalog (DV-34). "Fear < 20 = aggressive long bias. Greed > 80 = aggressive short bias."
- **Sources:** Alternative.me API (free).
- **Cost:** Free.
- **Expected impact:** Solid daily regime filter.

### 13. SOPR / aSOPR (BTC)

- **Research evidence:** Tier 2 in ONCHAIN_PRACTICAL_GUIDE.md (6/10). SOPR bouncing off 1.0 in bull markets is an actionable entry confirmation. STH-SOPR captures "weak hand" capitulation.
- **Sources:** BGeometrics (free for basic SOPR), Glassnode (paid for aSOPR, STH-SOPR).
- **Cost:** Free for basic; $29-799/mo for variants.
- **Expected impact:** Moderate -- +2-5% win rate as sentiment confirmation filter.

### 14. DeFi TVL Momentum

- **Research evidence:** Tier 2 in catalog (OC-38). "TVL trends lead altcoin prices by 1-3 days because capital deployment precedes price impact."
- **Sources:** DefiLlama API (free).
- **Cost:** Free.
- **Expected impact:** Moderate for altcoin risk appetite.

### 15. Perpetual Volume / Spot Volume Ratio

- **Research evidence:** Tier 2 in catalog (DV-38). Perp/Spot > 5 = leverage-driven regime (mean-reversion works). Perp/Spot < 2 = spot-driven (trend-following works).
- **Sources:** Coinglass (free), exchange APIs.
- **Cost:** Free.
- **Expected impact:** Moderate -- helps select which strategy type to deploy per regime.

### 16. Estimated Liquidation Levels (Heatmap)

- **Research evidence:** Tier 1 in catalog (DV-18). Liquidation clusters "act as magnets." Useful for stop placement and target setting.
- **Sources:** Coinglass (limited free), Hyblock/Kingfisher ($50-200/mo).
- **Cost:** $50-200/mo for systematic data.
- **Expected impact:** Moderate for stop/target optimization.

### 17. Cross-Exchange Order Book Depth

- **Research evidence:** CRYPTO_MICROSTRUCTURE_POST_ETF.md Section 14 lists "real-time order book depth across ALL venues" as a key gap. Order flow imbalance and adverse selection are "key predictive features" per microstructure research.
- **Sources:** Kaiko ($$$), Amberdata ($$$), or direct exchange APIs (free but requires infrastructure).
- **Cost:** High (data vendors) or significant engineering (DIY).
- **Expected impact:** Moderate-high for entry timing at sub-hourly scale; less critical for 1H swing.

### 18. Gamma Exposure (GEX)

- **Research evidence:** Tier 2 in catalog (DV-30). "Negative GEX = expect 2-3x normal move sizes. Positive GEX = expect range-bound/mean-reversion."
- **Sources:** Computable from Deribit API (free).
- **Cost:** Free (requires computation).
- **Expected impact:** Strong for volatility regime identification and position sizing.

---

## Low-Priority / Nice-to-Have

Data with limited predictive value, very slow update frequency, high cost, or applicability outside our 1H swing timeframe.

### 19. Wallet Labeling / Entity Resolution

- **Research evidence:** CRYPTO_MICROSTRUCTURE_POST_ETF.md Section 14: "Wallet labeling and entity resolution is the single biggest data gap."
- **Reality for our system:** Requires Nansen ($$$) or Arkham Intelligence. Even with it, the signal updates daily at best and has low R-squared for short-term trading. Whale transactions tested at R^2 < 0.05 (Presto Research).
- **Cost:** $1,000+/mo for Nansen.
- **Verdict:** Interesting for research but not cost-justified for swing trading signals.

### 20. Hash Rate / Hash Ribbons / Difficulty Ribbons

- **Research evidence:** ONCHAIN_PRACTICAL_GUIDE.md rates Hash Ribbons at 4/10 priority. "Signal fires 2-3 times per year." Average trade lasted 253 days -- mismatched with our 1H swing system.
- **Sources:** Blockchain.com (free).
- **Cost:** Free.
- **Verdict:** Include as a rare regime override if trivial to implement, but do not prioritize.

### 21. NVT Ratio

- **Research evidence:** ONCHAIN_PRACTICAL_GUIDE.md explicitly says "skip for our system." Too slow for swing trading.
- **Verdict:** Skip.

### 22. Whale Transaction Tracking

- **Research evidence:** ONCHAIN_PRACTICAL_GUIDE.md Section 3.7: "R-squared ranged from 0.0017 to 0.0537 across all 12 scenarios tested. This is statistically insignificant."
- **Verdict:** Skip. The signal-to-noise ratio is terrible.

### 23. Social Sentiment Data (Telegram, Discord, Twitter)

- **Research evidence:** Ranked #9 in Post-ETF Signal Priority Matrix (low conviction). CRYPTO_MICROSTRUCTURE_POST_ETF.md Section 14 mentions Telegram/Discord analytics as undertracked.
- **Sources:** LunarCrush, Santiment ($49+/mo), custom scrapers.
- **Cost:** $49+/mo or significant engineering.
- **Verdict:** Low priority. Non-linear relationship with returns, short half-life, difficult to backtest.

### 24. GitHub / Developer Activity

- **Research evidence:** Ranked #10 in Post-ETF Signal Priority Matrix (30-90 day horizon). "Underused leading indicator for protocol quality."
- **Sources:** Santiment (free tier for DAA + dev activity).
- **Cost:** Free for basic.
- **Verdict:** Too slow for 1H swing. Useful for token selection, not trade timing.

### 25. Macro Data (M2 Money Supply, DXY, Real Yields)

- **Research evidence:** CRYPTO_MICROSTRUCTURE_POST_ETF.md notes "macro sensitivity increased" post-ETF. Section 14 lists "real-time global liquidity tracking" as a gap.
- **Sources:** FRED API (free), TradingView.
- **Cost:** Free.
- **Verdict:** Low priority for 1H swing. Monthly update frequency mismatches our timeframe. Consider for daily regime overlay eventually.

### 26. Cross-Chain Bridge Analytics

- **Research evidence:** CRYPTO_MICROSTRUCTURE_POST_ETF.md Section 14: "urgently needed as multi-chain activity grows."
- **Verdict:** Relevant for DeFi-native strategies, not for our centralized exchange swing system.

### 27. MEV Data

- **Research evidence:** Listed in SIGNAL_DISCOVERY_METHODS.md Section 12.4 as unique to crypto. CRYPTO_MICROSTRUCTURE_POST_ETF.md Section 14 lists it as undertracked.
- **Verdict:** Research interest only. Not applicable to our Binance spot/perp strategy.

### 28. HODL Waves / Realized Cap HODL Waves

- **Research evidence:** Tier 4 in catalog. "Weekly. Very slow. Only useful as multi-month regime context."
- **Cost:** $799/mo (Glassnode Tier 3).
- **Verdict:** Skip. Too slow, too expensive.

---

## Recommended Data Acquisition Plan

Prioritized by (expected signal value) * (data quality) / (cost + engineering effort).

### Phase 1: Free Derivatives Data (Week 1-2, $0/mo)

**Build a derivatives data pipeline using free exchange APIs.**

| Data | Source | Effort | Expected Impact |
|------|--------|--------|-----------------|
| Funding Rate (8h, all tokens) | Binance API | Low | High -- contrarian entries |
| Open Interest (hourly) | Coinglass / Binance API | Low | High -- cascade risk sizing |
| Liquidation Volume (long/short) | Coinglass API | Low | High -- exhaustion entries |
| Long/Short Ratio + Top Trader Ratio | Binance API | Low | Moderate -- crowd positioning |
| Taker Buy/Sell Ratio (futures) | Binance API | Low | Moderate -- order flow |
| Fear & Greed Index | Alternative.me API | Trivial | Moderate -- regime filter |

**Estimated total effort:** 2-3 days of engineering. All free APIs, no keys required.
**Expected Sharpe improvement:** +0.1 to +0.2 from derivatives signals alone.

### Phase 2: Free Options + DeFi Data (Week 3-4, $0/mo)

| Data | Source | Effort | Expected Impact |
|------|--------|--------|-----------------|
| Options IV, Skew, Put-Call Ratio | Deribit API | Medium | High -- vol regime |
| DVOL (BTC Volatility Index) | Deribit API | Low | Moderate -- fear gauge |
| Futures Basis (annualized) | Exchange APIs | Low | Moderate -- carry signal |
| Stablecoin Supply | DefiLlama API | Low | Moderate -- liquidity proxy |
| DeFi TVL | DefiLlama API | Low | Moderate -- risk appetite |
| GEX (computed from Deribit) | Deribit API + computation | Medium | High -- vol amplifier/suppressor |

**Estimated total effort:** 3-5 days of engineering.

### Phase 3: Free On-Chain Data (Week 5-6, $0/mo)

| Data | Source | Effort | Expected Impact |
|------|--------|--------|-----------------|
| MVRV Z-Score (BTC) | BGeometrics API | Low | High -- cycle sizing |
| Exchange Netflows (BTC, daily) | BGeometrics API | Low | Moderate -- distribution filter |
| SOPR (BTC) | BGeometrics API | Low | Moderate -- sentiment |
| Active Addresses (BTC) | Blockchain.com API | Low | Low-Moderate -- confirmation |
| Hash Rate (BTC) | Blockchain.com API | Trivial | Low -- miner regime |

**Estimated total effort:** 2-3 days. Validate BGeometrics API reliability first.
**Expected max drawdown improvement:** -5% to -15% from on-chain regime filters.

### Phase 4: ETF Flow Data (Week 7-8, $0-50/mo)

| Data | Source | Effort | Expected Impact |
|------|--------|--------|-----------------|
| Daily BTC ETF net flows (IBIT, FBTC, aggregate) | SoSoValue (free), CoinGlass | Medium | Very High -- #1 signal per research |
| Daily ETH ETF net flows | SoSoValue, CoinGlass | Low | Moderate |
| ETF premium/discount | Derived from NAV vs market price | Medium | Moderate -- arb signal |

**Estimated total effort:** 3-4 days. May require scraping if no clean API.
**This is the single highest-value addition per the research -- deprioritized only because data acquisition is less straightforward than exchange APIs.**

### Phase 5: Paid On-Chain Upgrade (Month 3+, $29-70/mo)

Only pursue after validating Phase 3 free on-chain signals in backtests.

| Data | Source | Cost | Expected Impact |
|------|--------|------|-----------------|
| MVRV, SOPR, NUPL (reliable API) | Glassnode Tier 2 | $29/mo | Upgrade from BGeometrics |
| Hourly Exchange Netflows | CryptoQuant Pro | $39/mo | Upgrade daily to hourly |
| CDD, Realized Price, NVT Signal | Glassnode Tier 2 | Included | Additional on-chain signals |

**Decision gate:** Only spend if Phase 3 backtest shows >0.05 Sharpe improvement from on-chain signals.

### Phase 6: Research / Exploration (Ongoing, $0)

- Cross-sectional factors (2-week momentum, residual momentum) -- computable from existing price data
- Regime detection via HMM -- computable from existing features
- Cross-exchange basis divergence -- free from exchange APIs
- Margin lending rates -- free from Bitfinex/Aave

---

## Summary

**Our current data is 100% Binance spot price/volume data with derived microstructure features.** The research identifies six categories of missing data that would meaningfully improve strategy performance:

1. **Derivatives data** (funding rate, OI, liquidations, options) -- free, high impact, should be first priority
2. **ETF flow data** -- the single most predictive signal per research, but harder to acquire programmatically
3. **On-chain data** (MVRV, exchange flows, stablecoins) -- free-to-cheap, proven regime filters
4. **Sentiment data** (Fear & Greed) -- free, easy, moderate value
5. **DeFi data** (TVL, stablecoin supply) -- free, moderate value
6. **Options data** (IV, skew, GEX) -- free from Deribit, high value for volatility regime

The total cost to achieve a substantially improved data foundation is $0/mo for Phase 1-4 (covers ~80% of signal value) and $29-70/mo for Phase 5 (remaining ~20%). Engineering effort is approximately 2-4 weeks for the full pipeline.
