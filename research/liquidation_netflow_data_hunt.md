# Free Alternative Crypto Data Source Research

**Date:** 2026-03-24
**Goal:** Find free APIs for liquidation data, exchange netflows, whale transactions, and other predictive alternative data.

---

## Summary of Findings

| Data Type | Best Free Source | Status | Historical Depth | Saved? |
|-----------|-----------------|--------|-----------------|--------|
| Aggregated Liquidations | OKX Public API | WORKS (tick-level) | Days-weeks | Yes (existing) |
| Open Interest (hourly) | Binance Futures API | WORKS | ~21 days (hourly), 30 days (daily) | Yes |
| Long/Short Ratios | Binance Futures API | WORKS | ~21 days (hourly), 30 days (daily) | Yes |
| Taker Buy/Sell Volume | Binance Futures API | WORKS | ~21 days (hourly), 30 days (daily) | Yes |
| Funding Rates | Binance Futures API | WORKS | ~65 days (8h intervals) | Yes |
| Exchange Netflow | CoinMetrics Community | WORKS (existing) | ~6 months | Yes (existing) |
| Exchange Netflow (alt) | Santiment GraphQL | PARTIAL (rate limited) | 1 year (30-day delay) | Partial |
| Whale Transactions | blockchain.info unconfirmed | WORKS (real-time only) | No history | No |
| Whale Transactions | Whale Alert API | REQUIRES KEY (free tier: 10/min, >$500K only) | 30 days free | No |
| Fear & Greed Index | alternative.me | WORKS | 8+ years (since 2018) | Yes |
| BTC On-chain (tx vol, etc) | blockchain.info Charts | WORKS | 1+ year | Yes |
| Market Data (price, vol) | CoinGecko | WORKS | 1 year (daily) | Yes |
| Market Data (OHLCV) | CryptoDataDownload | WORKS | Since 2017 (hourly!) | Yes |
| BTC Mining/Hashrate | mempool.space | WORKS | 1 year | Yes |
| Bybit OI + L/S Ratio | Bybit Public API | WORKS | ~8 days (hourly) | Yes |
| Order Book Depth | Binance Spot API | WORKS (snapshots) | Real-time only | Yes (snapshot) |

---

## Detailed Source Analysis

### 1. Aggregated Liquidation Data

#### OKX Public API -- BEST FREE SOURCE (Already Collected)
- **Endpoint:** `https://www.okx.com/api/v5/public/liquidation-orders`
- **Status:** Already have ~79K BTC and ~79K ETH liquidation records
- **Resolution:** Tick-level (individual liquidation events)
- **Data fields:** timestamp, side, posSide, price, size, bkLoss
- **Rate limit:** Geo-dependent (403 from some IPs via rubik endpoints)
- **File:** `data/alternative/liquidations/okx_btc_liquidations.csv`, `okx_eth_liquidations.csv`

#### Binance Futures `/fapi/v1/allForceOrders` -- DEPRECATED
- **Status:** Returns `{"code": 400, "msg": "The endpoint has been out of maintenance"}`
- Previously provided real-time liquidation orders; no longer available.

#### Coinglass API -- REQUIRES PAID KEY
- **Endpoint:** `https://open-api-v3.coinglass.com/api/futures/liquidation/v2/history`
- **Status:** Returns `{"code":"30001","msg":"API key missing."}`
- Dashboard is free; API requires subscription ($40+/month)

#### Hyblock Capital -- PAID (Free tier has limited dashboard)
- No free API access; liquidation heatmap data requires subscription.

#### Tardis.dev -- FREE REAL-TIME STREAMING (Open Source)
- 57 exchanges available including `bitmex` (has `liquidation` channel), `binance-futures` (has `forceOrder` channel)
- **Open-source client libraries** connect directly to exchange WebSockets (no API key!)
- Excellent for building a live liquidation data collector
- Historical replay requires paid subscription

**Recommendation:** Use existing OKX data + build a Tardis.dev WebSocket collector for ongoing real-time data.

---

### 2. Exchange Netflow Data

#### CoinMetrics Community API -- ALREADY COLLECTED
- **Status:** Already have BTC and ETH exchange flow data (FlowInExNtv, FlowOutExNtv, NetFlowNtv, FlowInExUSD, FlowOutExUSD)
- **Depth:** ~568 daily records (Sep 2024 - Feb 2026)
- **Files:** `data/alternative/exchange_netflow/coinmetrics_btc_exchange_flow.csv`, `coinmetrics_eth_exchange_flow.csv`
- **Note:** Free community API has restricted metric access as of 2026; previously working metrics now return `forbidden`

#### Santiment GraphQL API -- PARTIAL
- **Endpoint:** `https://api.santiment.net/graphql`
- **Free tier:** 1-year lookback with 30-day delay, aggressive rate limits (403 after ~2 requests)
- **Metric:** `exchange_balance` worked (325 daily points, Apr 2025 - Feb 2026)
- **Whale metrics:** `whale_transaction_count_100k_usd_to_inf` exists but rate-limited
- **Verdict:** Usable for batch pulls with long waits between requests

#### BGeometrics (bitcoin-data.com) -- RATE LIMITED
- **Status:** API exists at `bitcoin-data.com` with exchange inflow/outflow/netflow/reserves
- **Free tier:** 8 requests/hour -- too restrictive for bulk data collection
- **Paid tier:** Required for reasonable access
- **Verdict:** Not practical for backtesting data at free tier

#### blockchain.info Charts API -- PROXY METRICS
- **Status:** WORKS, no API key needed
- **Available:** estimated-transaction-volume-usd (356 pts), n-transactions (354 pts), output-volume, trade-volume, n-unique-addresses, market-cap, total-bitcoins
- **Depth:** 1 year+ daily
- **Limitation:** No direct exchange flow data; transaction volume is an imperfect proxy
- **File:** `data/alternative/blockchain_info_btc_1year.json`

#### CryptoQuant -- REQUIRES API KEY
- Returns 401 Unauthorized without token
- Free tier exists but API access requires account registration

**Recommendation:** CoinMetrics existing data is the best free source. Supplement with Santiment (careful rate limiting) for exchange_balance. For new data, blockchain.info transaction volume can serve as a proxy.

---

### 3. Whale Transaction Data

#### blockchain.info Unconfirmed Transactions -- REAL-TIME ONLY
- **Endpoint:** `https://blockchain.info/unconfirmed-transactions?format=json`
- **Status:** WORKS, no key needed
- Returns latest 100 unconfirmed transactions, filterable for large amounts
- Found 2 transactions >10 BTC in a single sample
- **Limitation:** No historical data; need to poll continuously to build history
- Useful for building a live whale alert system

#### Blockchair API -- RATE LIMITED
- **Endpoint:** `https://api.blockchair.com/bitcoin/transactions?q=output_total(100000000000..)`
- **Status:** WORKS but IP-based rate limiting (got blacklisted after a few requests)
- Free tier: ~30 requests/day without API key
- Can filter by output_total for whale transactions
- **Verdict:** Too rate-limited for historical data; useful for spot checks

#### Whale Alert API -- REQUIRES KEY (Free Tier Very Limited)
- Requires API key signup
- Free tier: 10 calls/min, only transactions >$500K, max 30 days history
- Sample data available for download (1 day per blockchain)
- **Verdict:** Free tier too limited for backtesting

#### Etherscan v2 -- REQUIRES KEY
- Returns "Missing/Invalid API Key" without key
- Free key available (5 calls/sec) but requires signup

#### Arkham Intelligence -- REQUIRES KEY
- Returns "invalid timestamp format, please sign up for an api key"
- Free tier reportedly generous but requires account

**Recommendation:** Build a continuous blockchain.info unconfirmed-tx poller to accumulate whale data over time. For immediate backtesting, use exchange order book depth snapshots as a whale pressure proxy.

---

### 4. Other Free Alternative Data (Predictive Signals)

#### Fear & Greed Index (alternative.me) -- EXCELLENT
- **Endpoint:** `https://api.alternative.me/fng/?limit=0&format=json`
- **Status:** WORKS perfectly, no key needed
- **Depth:** 2,970 daily records since 2018-02-01
- **Data:** value (0-100), classification (Extreme Fear/Fear/Neutral/Greed/Extreme Greed)
- **File:** `data/alternative/fear_greed_index_full.json`
- **Signal quality:** Contrarian indicator; extreme fear = potential buy, extreme greed = potential sell

#### Binance Futures Derivatives Data -- EXCELLENT
All free, no API key, hourly and daily resolution:

| Metric | Endpoint | Records Saved | Depth |
|--------|----------|--------------|-------|
| Open Interest | `/futures/data/openInterestHist` | 1000 (hourly) | ~21 days |
| Global L/S Ratio | `/futures/data/globalLongShortAccountRatio` | 1000 (hourly) | ~21 days |
| Top Trader L/S Position | `/futures/data/topLongShortPositionRatio` | 1000 (hourly) | ~21 days |
| Top Trader L/S Account | `/futures/data/topLongShortAccountRatio` | 1000 (hourly) | ~21 days |
| Taker Buy/Sell Volume | `/futures/data/takerlongshortRatio` | 1000 (hourly) | ~21 days |
| Funding Rate | `/fapi/v1/fundingRate` | 2400 (8h) | 2019-2026! |

- **File prefix:** `data/alternative/binance_*.json`
- **Signal quality:** Funding rate and L/S ratio divergences are well-documented predictive signals

#### Bybit Public API -- GOOD
- OI and L/S ratio, 200 records hourly (~8 days)
- **File:** `data/alternative/bybit_derivatives_data.json`
- Shorter history than Binance but useful for multi-exchange confirmation

#### mempool.space -- GOOD
- Bitcoin hashrate (365 daily records), difficulty adjustments, mempool fee data
- **File:** `data/alternative/mempool_space_btc_data.json`
- **Signal quality:** Hashrate trends and difficulty adjustments can predict miner selling pressure

#### CoinGecko -- GOOD
- BTC and ETH price, market cap, and total volumes (366 daily points, 1 year)
- **File:** `data/alternative/coingecko_btc_eth_365d.json`
- Free tier: 10-30 calls/min, max 365 days

#### CryptoDataDownload -- EXCELLENT
- Free CSV downloads of historical OHLCV data
- BTC/USDT hourly data: 74,877 records from 2017-08-17 to 2026-03-23!
- **File:** `data/alternative/cryptodatadownload_btc_1h.csv` (8 MB)
- **Signal quality:** Long history enables volume-based signal backtesting

#### Binance Order Book Depth -- REAL-TIME ONLY
- 5000 levels of bid/ask depth, free, no key
- Bid/ask volume imbalance = whale pressure proxy
- Current snapshot: bid/ask ratio 0.89 (slight sell pressure), deep ratio 0.72
- **File:** `data/alternative/binance_btc_orderbook_snapshot.json`
- Need continuous polling to build time series

---

## What Did NOT Work

| Source | Issue |
|--------|-------|
| Coinglass API | Requires paid API key ($40+/month) |
| CryptoQuant API | Requires API key (free tier exists but needs signup) |
| Glassnode API | Free tier (Tier 1) exists but full API requires Professional plan |
| BGeometrics | 8 requests/hour free limit -- too restrictive |
| Blockchair | IP-based blacklisting after a few requests |
| Etherscan v2 | Requires API key signup |
| Arkham Intelligence | Requires API key signup |
| Dune Analytics | Requires API key |
| CoinMetrics Community (2026) | Many metrics now return `forbidden` on free tier |
| Binance Forced Liquidation Orders | Endpoint deprecated/removed |
| OKX Rubik Analytics | Returns 403 Forbidden (geo-restricted from some IPs) |
| dYdX v4 Funding | Returns 403 after first successful call |
| CryptoCompare Blockchain | Returns errors without API key for blockchain metrics |

---

## Recommendations for Building Trading Signals

### Immediate (Data Available Now)
1. **Funding Rate Divergence Signal** -- Binance funding rates since 2019 (2,400 records). When funding is extremely positive (crowded longs), expect mean reversion.
2. **Fear & Greed Contrarian Signal** -- 8 years of daily data. Buy when value <20 (Extreme Fear), sell when >80 (Extreme Greed).
3. **Exchange Netflow Signal** -- CoinMetrics data (568 daily records). Large net inflows = bearish (coins moving to exchanges to sell).
4. **OI + L/S Ratio Divergence** -- When OI is rising but L/S ratio is extremely skewed, expect liquidation cascade.
5. **Taker Buy/Sell Pressure** -- Binance taker volume ratio. Sustained sell pressure (ratio <0.9) = bearish signal.

### Build Over Time (Need Continuous Collection)
6. **Whale Transaction Monitor** -- Poll blockchain.info unconfirmed transactions every 5 minutes, filter >100 BTC.
7. **Order Book Imbalance** -- Snapshot Binance depth every 15 minutes, track bid/ask ratio changes.
8. **Multi-Exchange OI Divergence** -- Compare Binance vs Bybit OI for confirmation signals.

### Requires API Key Signup (Free Tiers)
9. **Whale Alert** -- Free key gets you >$500K transactions with 30-day history.
10. **Etherscan** -- Free key gets large ETH transfers.
11. **CryptoQuant** -- Free tier gets basic exchange flow data.

---

## Data Files Saved

All files saved to `/workspace/crypto_backtest/data/alternative/`:

| File | Size | Contents |
|------|------|----------|
| `binance_funding_rates_full.json` | 292K | BTC+ETH funding rates, 2019-2026 |
| `binance_open_interest_hourly.json` | 204K | BTC+ETH OI, ~21 days hourly |
| `binance_global_ls_ratio_hourly.json` | 156K | Global L/S ratio, ~21 days hourly |
| `binance_top_trader_ls_position_hourly.json` | 156K | Top trader positions, ~21 days hourly |
| `binance_top_trader_ls_account_hourly.json` | 156K | Top trader accounts, ~21 days hourly |
| `binance_taker_buy_sell_hourly.json` | 148K | Taker buy/sell volume, ~21 days hourly |
| `binance_daily_open_interest.json` | 12K | Daily OI, ~30 days |
| `binance_global_ls_ratio_daily.json` | 12K | Daily L/S ratio, ~30 days |
| `binance_taker_buy_sell_daily.json` | 12K | Daily taker volume, ~30 days |
| `binance_top_trader_ls_position_daily.json` | 12K | Daily top trader, ~30 days |
| `binance_funding_rate_btc.json` | 28K | BTC funding, ~65 days |
| `binance_btc_orderbook_snapshot.json` | 336K | 5000-level depth snapshot |
| `blockchain_info_btc_1year.json` | 392K | 9 BTC on-chain charts, 1 year |
| `coingecko_btc_eth_365d.json` | 144K | Price/mcap/volume, 1 year daily |
| `cryptodatadownload_btc_1h.csv` | 7.9M | BTCUSDT hourly OHLCV since 2017 |
| `fear_greed_index_full.json` | 284K | Fear & Greed, 2018-2026 (2970 days) |
| `bybit_derivatives_data.json` | 88K | Bybit OI + L/S, ~8 days |
| `mempool_space_btc_data.json` | 44K | Hashrate + difficulty, 1 year |

**Previously collected (existing):**
| Directory | Contents |
|-----------|----------|
| `liquidations/` | OKX BTC/ETH/SOL tick liquidations + Binance funding rates |
| `exchange_netflow/` | CoinMetrics + Santiment BTC/ETH exchange flows |
| `funding_ls_proxy/` | Funding/LS ratio data |
| `binance_oi/` | Binance open interest |
| `fear_greed/` | Fear & Greed Index |
| `stablecoin_supply/` | Stablecoin supply data |
| `macro/` | Macro economic indicators |
| `deribit_options/` | Options market data |
| `etf_flows/` | Bitcoin ETF flow data |

---

## API Rate Limit Reference

| API | Rate Limit | Auth Required |
|-----|-----------|---------------|
| Binance Futures Data | 2400 req/min (weight-based) | No |
| Binance Spot | 1200 req/min | No |
| blockchain.info | ~100 req/min (undocumented) | No |
| CoinGecko | 10-30 req/min | No (free tier) |
| alternative.me F&G | Generous (no documented limit) | No |
| mempool.space | Generous | No |
| Bybit | 120 req/min | No |
| Santiment | ~2 req/min (aggressive 403) | No (free tier) |
| CryptoDataDownload | Generous (CSV download) | No |
| BGeometrics | 8 req/hour | No |
| Blockchair | ~30 req/day (IP-based) | No |
