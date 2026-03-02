# Short Strategy Research -- Perpetual Futures for Downtrend Alpha

> **TL;DR -- Exchange & data summary**
> - **Execute on Hyperliquid** (0.015% maker, no KYC, hourly funding, Python SDK)
> - **Kraken as backup** (0.0125% maker, hourly funding, already our spot exchange)
> - **Binance for data** (`data.binance.vision` bulk downloads -- best historical coverage)
> - Funding rate paradox: shorts PAY in bear markets (~1-5%/mo drag), GET PAID in bull markets
> - Academic evidence: short-side momentum is where crypto alpha lives (Fieberg et al. 2024)
> **When to read full file:** Building a short strategy, setting up perp data pipeline, choosing execution venue
> **Sections:** 1-Exchange Comparison, 2-Data Availability, 3-Pipeline Plan, 4-Strategy Design, 5-Cost Model, 6-Risk Framework

---

## 1. EXCHANGE COMPARISON

| Feature | Kraken | Hyperliquid | Binance |
|---------|--------|-------------|---------|
| **Perp count** | 133+ | 150+ | 350+ |
| **Maker / Taker fee** | 0.0125% / 0.0225% | 0.015% / 0.035% | 0.02% / 0.05% |
| **Funding freq** | Hourly | Hourly | 8-hourly |
| **Max leverage** | 50x | 50x | 125x |
| **Collateral** | Multi (USDC/BTC/ETH) | USDC | USDT |
| **KYC** | Yes | No | Yes |
| **US access** | Spot yes, perps no | Yes (no KYC) | Banned |
| **Historical data** | Good (funding + OHLCV) | Limited (5K candle cap) | Excellent (free bulk) |
| **Oracle risk** | Low | HIGH (JELLYJELLY, POPCAT) | Low |
| **Symbol format** | `PF_XBTUSD` / CCXT: `BTC/USD:USD` | `BTC` / CCXT: `BTC/USDC:USDC` | `BTCUSDT` / CCXT: `BTC/USDT:USDT` |
| **CCXT class** | `ccxt.krakenfutures()` | `ccxt.hyperliquid()` | `ccxt.binanceusdm()` |

**Recommendation:** Execute on Hyperliquid (primary) or Kraken (backup). Data from Binance. Cross-validate funding patterns across exchanges before live.

---

## 2. DATA AVAILABILITY

### Data Quality Scorecard

| Data Type | Binance | Kraken | Hyperliquid |
|-----------|---------|--------|-------------|
| Funding rates | A+ (since ~2019, bulk CSV) | A (since ~2018, free API) | B+ (since Feb 2023, paginated) |
| OHLCV | A+ (full history, bulk) | A- (full, paginated) | C (5K candle cap = ~208d at 1h) |
| Open interest | A+ (historical API + bulk) | D (snapshot only) | D (snapshot only) |
| Liquidations | A (bulk CSV) | C (partial) | C- (derive from S3 fills) |

### API Details

| Data | Binance | Kraken | Hyperliquid |
|------|---------|--------|-------------|
| **Funding endpoint** | `GET /fapi/v1/fundingRate` (1000/page) | `GET /derivatives/api/v3/historicalfundingrates` (full) | `POST /info` fundingHistory (500/page) |
| **Funding frequency** | 8-hourly | Hourly | Hourly |
| **OHLCV depth (1h)** | Years (bulk) | Years (paginated) | ~208 days (5K cap) |
| **Bulk download** | `data.binance.vision` (free) | No futures bulk | No (S3 monthly, unreliable) |
| **Rate limits** | Standard | Free (public) | 20 + 1 per 20 items |

### SDKs

| Exchange | SDK | CCXT Symbol |
|----------|-----|-------------|
| Binance | `ccxt` or `python-binance` | `BTC/USDT:USDT` |
| Kraken | `ccxt` (`krakenfutures`) or `python-kraken-sdk` | `BTC/USD:USD` |
| Hyperliquid | `hyperliquid-python-sdk` (requires Python 3.10) | `BTC/USDC:USDC` |

### Third-Party Providers

| Provider | Data | Pricing |
|----------|------|---------|
| Tardis.dev | Tick data, OI, liquidations (Kraken since 2019) | Paid (free samples) |
| CoinGlass | OI, funding, liquidations (all three) | From $35/mo |
| Artemis | Hyperliquid tables on S3 | Free |

---

## 3. DATA PIPELINE PLAN

### Phase 1 -- Core (enough to backtest shorts)

| Data | Source | Method | Tokens |
|------|--------|--------|--------|
| Funding rates | Binance | `data.binance.vision` bulk CSV | 49 |
| Perp OHLCV (1h) | Binance | `data.binance.vision` bulk CSV | 49 |
| Funding rates | Kraken | Public API (free) | 49 |
| Funding rates | Hyperliquid | `fundingHistory` (paginated) | 49 |

### Phase 2 -- Enhanced

| Data | Source | Method |
|------|--------|--------|
| Perp OHLCV (1h) | Kraken | Charts API (paginated) |
| Perp OHLCV (4h) | Hyperliquid | `candleSnapshot` (full at 4h) |
| Open interest | Binance | `data.binance.vision` bulk |

### Phase 3 -- Nice to have

Cross-exchange OI (CoinGlass), liquidation cascades, long/short ratio, Hyperliquid fills (S3 archive).

### Storage

```
data/perp/{exchange}/funding/    # {TOKEN}_funding.parquet
data/perp/{exchange}/1h_cache/   # {TOKEN}_perp_1h.parquet
data/perp/{exchange}/oi/         # {TOKEN}_oi.parquet
```

---

## 4. SHORT STRATEGY DESIGN

### Academic Evidence

- **Fieberg et al. (2024):** Momentum profits concentrated in short leg -- shorting losers > going long winners
- **Man AHL:** Runs systematic crypto trend-following with both long and short
- **RSI in downtrends:** IC = -0.145 (overbought in downtrend predicts negative returns -- strongest single-signal IC for shorts)

### Proposed 6-Layer Signal Stack

| Layer | Signal | Rationale |
|-------|--------|-----------|
| Regime | DOWNTREND or CRISIS | Only short in confirmed bear regimes |
| Trend | Close < EMA20 AND EMA20 < EMA50 | Bear EMA alignment |
| Entry | RSI > 60 in downtrend (IC=-0.145) OR `ret_1 < -0.02` after rally | Overbought bounces fail in downtrends |
| Volume | Declining on rallies, expanding on drops | Distribution pattern |
| Exit | Regime flip to UPTREND + 2x ATR trail | Regime filter + tighter risk |
| Sizing | 50-70% of long sizing, quarter-Kelly | Unbounded theoretical short risk |

### Funding Rate Paradox

| Market Regime | Funding Rate | Impact on Shorts |
|---------------|-------------|------------------|
| Bull market | +0.01% to +0.05% per 8h | Shorts GET PAID |
| Neutral | Near zero | Minimal |
| Bear market | -0.01% to -0.05% per 8h | Shorts PAY (~1-5%/mo drag) |

Shorts face funding headwind in exactly the regimes we want to be short. Must model as real cost. **Funding capture** (delta-neutral arb in bull) is a separate strategy type.

### Signal Candidates

| Signal | IC in Downtrend | Type |
|--------|----------------|------|
| RSI (high values) | -0.145 | Mean reversion (overbought short entry) |
| ADX + (-DI > +DI) | ~0.08 | Trend strength |
| Volume decline on rallies | ~0.05 | Distribution |
| Funding rate (high positive) | ~0.04 | Overleveraged longs |
| OI increase + price decline | ~0.06 | New shorts piling in |

---

## 5. COST MODEL FOR SHORT BACKTESTING

### Per-Trade Costs

| Component | Hyperliquid | Kraken |
|-----------|-------------|--------|
| Entry (taker) | 0.035% | 0.0225% |
| Exit (taker) | 0.035% | 0.0225% |
| Slippage | 0.05% | 0.05% |
| **Round-trip** | **0.12%** | **0.095%** |
| + Bear funding (48h hold) | ~0.03-0.13% | ~0.025-0.105% |
| **Total per trade** | **0.15-0.25%** | **0.12-0.20%** |

### Longs vs Shorts Cost Comparison

| Component | Long (Spot/Kraken) | Short (Perp/Hyperliquid) |
|-----------|-------------------|--------------------------|
| Round-trip fees | 0.60% (Kraken Tier 1) | 0.07% |
| Funding | N/A | Variable |
| Slippage | 0.05% | 0.05% |
| **Round-trip total** | **0.65%** | **0.12% + funding** |

**Perps are cheaper per-trade than spot**, but funding accumulates. For swing trades (hours-days), perps win. For multi-week bear holds, funding drag becomes material.

### Engine Implementation

Costs must include: entry/exit taker fees, funding accrual per period, slippage (same model as longs). No separate borrow rate for perps (included in funding).

---

## 6. RISK FRAMEWORK FOR SHORTS

### Position Sizing (Asymmetric vs Longs)

| Parameter | Long | Short | Rationale |
|-----------|------|-------|-----------|
| Max position | 100% | 50-70% | Unbounded upside risk |
| Kelly fraction | 1/4 | 1/4 | Same conservative fraction |
| Max leverage | 1x (spot) | 2-3x (perp) | Keep effective exposure similar |
| Stop loss | 3x ATR | 2x ATR | Short squeezes more violent |
| Max hold | 720h | 360h | Mean reversion works against shorts |

### Circuit Breakers

| Trigger | Action |
|---------|--------|
| -5% single-trade loss | Halt 24h |
| -3% daily portfolio loss | Close all shorts |
| -15% drawdown from peak | Pull from production |
| Regime flip to UPTREND | Immediate exit |
| Funding > +0.1%/8h | Reduce position 50% |

### Short-Specific Risks

| Risk | Mitigation |
|------|------------|
| Short squeeze (forced buying cascade) | 2x ATR stops, lower leverage |
| Unlimited loss potential | Stop losses + position sizing |
| Funding drag in bear markets | Model in backtesting |
| Exchange oracle manipulation (Hyperliquid) | Position limits, monitoring |
| Correlation spike (all tokens -> 1.0 in crashes) | Size for correlated gains |

---

## 7. NEXT STEPS

1. Build `tools/fetch_perp_data.py` using CCXT for unified access
2. Extend engine: short-side support (negative positions, funding costs, 2x ATR stops)
3. Prototype s23_short_momentum using signal stack above
4. Same 8-gate pipeline with short-specific kill criteria
5. Cross-validate funding patterns across Binance/Kraken/Hyperliquid

---

*Research compiled: 2026-03-02. Sources: Kraken Futures API docs, Hyperliquid GitBook, Binance Futures API docs, Fieberg et al. (2024), Man AHL systematic crypto research.*
