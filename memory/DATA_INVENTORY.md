# Data Inventory — All Available Sources for Strategy Discovery

> **Last updated:** 2026-04-05

## I. PRICE DATA (OHLCV)

| Source | Path | Granularity | Tokens | Date Range | Size |
|--------|------|-------------|--------|------------|------|
| Binance Perp OHLCV | `data/perp/binance/1h_ohlcv/` | 1h | 248 | 2020-01 → 2026-03 | 317 MB |
| Binance Perp 1h Cache | `data/perp/1h_cache/` | 1h | 236 | 2020-01 → 2026-04 | 147 MB |
| Binance Perp 1m Cache | `data/perp/1m_cache/` | 1min | 197 | varies → 2026-04 | 4.0 GB |
| Binance Spot OHLCV | `data/spot/binance/1h_ohlcv/` | 1h | 151 | 2020-01 → 2026-03 | 296 MB |
| Binance Spot 1h Cache | `data/spot/1h_cache/` | 1h | 160 | 2020-01 → 2026-04 | 94 MB |
| Binance Spot 15m Cache | `data/spot/15m_cache/` | 15min | 94 | varies → 2026-03 | 263 MB |
| Hyperliquid Perp OHLCV | `data/perp/hyperliquid/ohlcv/` | 1h | 78 | 2024-01 → 2026-03 | 26 MB |

## II. FUNDING RATES

| Source | Path | Granularity | Tokens | Date Range | Size |
|--------|------|-------------|--------|------------|------|
| Binance Funding | `data/perp/binance/funding/` | 8h | 247 | 2020-01 → 2026-03 | 34 MB |
| Hyperliquid Funding | `data/perp/hyperliquid/funding/` | variable | 78 | 2024-01 → 2026-03 | 28 MB |
| Kraken Funding | `data/perp/kraken/funding/` | 8h | 319 | 2025-03 → 2026-03 | 139 MB |

## III. POSITIONING DATA (L/S, Top Trader, Taker)

| Source | Path | Granularity | Tokens | Date Range | Size | Columns |
|--------|------|-------------|--------|------------|------|---------|
| **Binance 5-min Metrics** | `data/alternative/binance_metrics/5min/` | **5min** | **227** | **2021-12 → 2026-04** | **2.3 GB** | `count_toptrader_long_short_ratio`, `sum_toptrader_long_short_ratio`, `count_long_short_ratio`, `sum_taker_long_short_vol_ratio`, `sum_open_interest`, `sum_open_interest_value` |
| Binance Daily Metrics | `data/alternative/binance_metrics/all_symbols_daily_ls.parquet` | Daily | 229 | 2020-09 → 2026-04 | 17 MB | Same + min/max per day |
| Binance By-Metric | `data/alternative/binance_metrics/by_metric/` | Daily | 229 | 2020-09 → 2026-04 | 9 MB | 5 files: global_ls, toptrader_ls_account, toptrader_ls_position, taker, OI |
| Binance Positioning Extended | `data/alternative/binance_positioning/extended/` | variable | 78 | varies | 9.4 MB | per-symbol funding + LS + taker |
| Bybit L/S Extended | `data/alternative/ls_ratio_extended/bybit/` | hourly | 10 | varies | 330 KB | L/S account ratio |
| OKX L/S Extended | `data/alternative/ls_ratio_extended/okx/` | hourly | 11 | varies | 330 KB | L/S account ratio |
| Funding L/S Proxy | `data/alternative/funding_ls_proxy/` | 1h | 149 | varies | 40 MB | Derived from funding rates |

## IV. OPEN INTEREST

| Source | Path | Granularity | Tokens | Exchanges | Date Range | Size |
|--------|------|-------------|--------|-----------|------------|------|
| **Coinalyze OI (parquet)** | `data/alternative/coinalyze/open_interest_parquet/` | **Daily** | **233** | **7 (Binance, Bybit, OKX, BitMEX, Huobi, Bitfinex, Hyperliquid)** | **2020-01 → 2026-04** | **23.4 MB** |
| Coinalyze OI (CSV raw) | `data/alternative/coinalyze/open_interest/` | Daily | 233 | 7 | 2020-01 → 2026-04 | 51 MB |
| Binance OI (in 5-min metrics) | `data/alternative/binance_metrics/5min/` | 5min | 227 | 1 (Binance) | 2021-12 → 2026-04 | (included in 2.3 GB) |
| Bybit OI | `data/perp/bybit_oi/` | 1h | 14 | 1 (Bybit) | 2024-01 → present | 14 MB |

## V. LIQUIDATIONS

| Source | Path | Granularity | Tokens | Exchanges | Date Range | Size |
|--------|------|-------------|--------|-----------|------------|------|
| **Coinalyze Liquidations (parquet)** | `data/alternative/coinalyze/liquidations_parquet/` | **Daily** | **226** | **6 (Binance, Bybit, OKX, BitMEX, Huobi, Bitfinex)** | **2020-05 → 2026-04** | **8.7 MB** |
| Coinalyze Liquidations (CSV raw) | `data/alternative/coinalyze/liquidations/` | Daily | 226 | 6 | 2020-05 → 2026-04 | 25 MB |
| OKX Liquidations (tick) | `data/alternative/liquidations/` | tick | 3 (BTC/ETH/SOL) | 1 (OKX) | 2026-03 snapshot | 13 MB |

## VI. OPTIONS / VOLATILITY

| Source | Path | Granularity | Tokens | Date Range | Size |
|--------|------|-------------|--------|------------|------|
| Deribit DVOL | `data/alternative/deribit_options/dvol/` | Daily | 2 (BTC/ETH) | varies | 988 KB |
| Deribit Options Snapshots | `data/alternative/deribit_options/raw/` | snapshot | 2 | 2026-03 | 236 KB |

## VII. MACRO & TRADITIONAL FINANCE

| Source | Path | Granularity | Assets | Date Range | Size |
|--------|------|-------------|--------|------------|------|
| Macro Indicators | `data/alternative/macro/` | Daily | 7 (Gold, Nasdaq, Oil, SP500, US10Y, DXY, VIX) | varies → 2026-03 | 2.7 MB |
| ETF Flows | `data/alternative/etf_flows/` | Daily | 1 (BTC ETF) | varies | 8 KB |

## VIII. ON-CHAIN

| Source | Path | Granularity | Metrics | Date Range | Size |
|--------|------|-------------|---------|------------|------|
| On-Chain Extended | `data/alternative/onchain_extended/` | Daily | 46 metrics (active addr, hash rate, MVRV, exchange flows, etc.) | varies | 2.4 MB |
| Exchange Netflow | `data/alternative/exchange_netflow/` | Daily | BTC+ETH flows (CoinMetrics, Santiment) | 2024-09 → 2026-03 | 460 KB |

## IX. SENTIMENT

| Source | Path | Granularity | Date Range | Size |
|--------|------|-------------|------------|------|
| Fear & Greed Index | `data/alternative/fear_greed/` | Daily | full history | 268 KB |
| Trump Social Sentiment | `data/alternative/trump_social/` | Event | 2024-2026 | 40 MB |
| Stablecoin Supply | `data/alternative/stablecoin_supply/` | Daily | full history | 55 MB |

## X. ML FEATURES (Pre-computed)

| Source | Path | Size | Notes |
|--------|------|------|-------|
| Feature Matrix | `data/ml_features/feature_matrix.parquet` | ~26 MB | 122 features, 10+ data sources |
| R204 IC Matrix | `data/ml_features/R204_ic_matrix.parquet` | small | Per-token IC results |
| R205 Rolling Signal Scan | `data/ml_features/R205_rolling_signal_scan.parquet` | small | 168h window dominates, per-token signs |

---

## TOTAL: ~7.8 GB across 45+ data sources

## KEY SIGNAL FIELDS (5-min Binance Metrics — Primary Signal Source)

| Column | Description | Signal Use |
|--------|-------------|------------|
| `count_long_short_ratio` | Global L/S account ratio | Retail sentiment (contrarian) |
| `count_toptrader_long_short_ratio` | Top 20% traders L/S by account | Smart money positioning |
| `sum_toptrader_long_short_ratio` | Top 20% traders L/S by position size | Smart money conviction |
| `sum_taker_long_short_vol_ratio` | Taker buy/sell volume ratio | Aggressive flow direction |
| `sum_open_interest` | Total OI in base currency | Position buildup/unwind |
| `sum_open_interest_value` | Total OI in USD | Size-normalized OI |

## GAPS — Data We Could Add

| Data Type | Source | Why | Priority |
|-----------|--------|-----|----------|
| Coinalyze 12h OI/Liq | Coinalyze API | 2.7 years of sub-daily cross-exchange data (vs daily only now) | MEDIUM |
| Coinalyze L/S ratio | Coinalyze API | Only Binance+Bybit have it, but cross-exchange divergence possible | LOW (we have 5-min Binance already) |
| Coinalyze OHLCV buy/sell volume | Coinalyze API | Taker flow decomposition per exchange | MEDIUM |
| Coinalyze funding rates | Coinalyze API | Cross-exchange funding divergence | LOW (Binance funding sufficient) |
| CoinGlass aggregate OI | CoinGlass API | Pre-aggregated cross-exchange OI | LOW (paid API, we can aggregate ourselves) |
| Binance 1-min L/S metrics | data.binance.vision | Even finer granularity than 5-min | LOW (5-min sufficient for hourly strategies) |
| More on-chain (Glassnode/Nansen) | Paid APIs | SOPR, NUPL, whale movements | HIGH cost, uncertain edge |
| Order book depth/imbalance | Exchange APIs | Real-time L2 data | HIGH effort, HF-only signal |
| Social sentiment (LunarCrush) | LunarCrush API | Social volume, sentiment scores | MEDIUM |
| DEX volume/TVL | DefiLlama API | DeFi activity as leading indicator | MEDIUM |
