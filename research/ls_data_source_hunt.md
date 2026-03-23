# Long/Short Account Ratio Data Source Hunt

**Date**: 2026-03-23
**Objective**: Find free historical Long/Short Account Ratio data for crypto perpetual futures
**Required tokens**: BTC, ETH, SOL (minimum); ideally 20+ tokens
**Required history**: 6+ months, preferably years

---

## EXECUTIVE SUMMARY

**WINNER: data.binance.vision "metrics" files** -- This is the definitive free source.

| Source | Status | History | Tokens | Granularity | Free? |
|--------|--------|---------|--------|-------------|-------|
| **data.binance.vision metrics** | **WORKING** | **Sep 2020 - present** | **793 symbols** | **5-minute** | **Yes** |
| **Bybit API** | WORKING | Aug 2020 - present | 20+ major pairs | 5min to daily | Yes |
| **OKX API** | WORKING | ~Dec 2023 - present (contract-level) | 20+ pairs | 5min to daily | Yes |
| Binance live API | Working but limited | Last 28 days only | All pairs | 5min to daily | Yes |
| Coinalyze API | Requires free API key | Daily: unlimited; Intraday: 1500-2000 pts | Many | 1min to daily | Free with key |
| Kaggle (jesusgraterol) | Static dataset | BTC only, through Oct 2023 | BTC only | From Binance API | Yes (CC0) |
| Kaggle (maxisoft) | Static dataset | Multi-coin, updated Mar 2025 | Multiple | From Binance API | Yes |
| CryptoQuant | Paid only | Unknown | Many | Up to daily | No (min $109/mo for API) |
| CoinGlass | Requires API key | Unknown | Many | Unknown | Paid API |
| Glassnode | No L/S ratio endpoint | N/A | N/A | N/A | No |
| Santiment | Different metric (MVRV) | N/A | N/A | N/A | No |
| TradingView | Display only, no API | N/A | N/A | N/A | N/A |

---

## SOURCE #1: data.binance.vision Metrics Files (THE WINNER)

### Discovery

The `data.binance.vision` S3 bucket contains a **`metrics`** folder under futures data that was previously overlooked. This is NOT the same as klines or funding rate data.

### URL Pattern

```
https://data.binance.vision/data/futures/um/daily/metrics/{SYMBOL}/{SYMBOL}-metrics-{YYYY-MM-DD}.zip
```

### S3 Listing URL

```
https://s3-ap-northeast-1.amazonaws.com/data.binance.vision?prefix=data/futures/um/daily/metrics/&delimiter=/
```

### What's Inside

Each zip contains a CSV with these columns:

| Column | Description |
|--------|-------------|
| `create_time` | Timestamp (5-minute intervals, e.g., `2020-09-01 00:05:00`) |
| `symbol` | Trading pair (e.g., `BTCUSDT`) |
| `sum_open_interest` | Total open interest in base currency |
| `sum_open_interest_value` | Total open interest in USD |
| `count_toptrader_long_short_ratio` | Top 20% traders L/S ratio BY ACCOUNT COUNT |
| `sum_toptrader_long_short_ratio` | Top 20% traders L/S ratio BY POSITION SIZE |
| `count_long_short_ratio` | **GLOBAL L/S ACCOUNT RATIO (this is what we want)** |
| `sum_taker_long_short_vol_ratio` | Taker buy/sell volume ratio |

### Data Range

- **BTCUSDT**: From **2020-09-01** to **2026-03-22** (yesterday) -- 5.5+ YEARS
- **Most major tokens** (ETH, SOL, DOGE, XRP, LINK, etc.): From **2021-12-01** -- 4+ YEARS
- **Newer tokens**: From their Binance Futures listing date
- **Total symbols**: **793** (including all Binance USDT-M perpetual futures)

### Start Dates for Key Symbols

| Symbol | Earliest Date |
|--------|--------------|
| BTCUSDT | 2020-09-01 |
| ETHUSDT | 2021-12-01 |
| SOLUSDT | 2021-12-01 |
| DOGEUSDT | 2021-12-01 |
| XRPUSDT | 2021-12-01 |
| BNBUSDT | 2021-12-01 |
| LINKUSDT | 2021-12-01 |
| ADAUSDT | 2021-12-01 |
| DOTUSDT | 2021-12-01 |
| AVAXUSDT | 2021-12-01 |
| LTCUSDT | 2021-12-01 |
| NEARUSDT | 2021-12-01 |
| AAVEUSDT | 2021-12-01 |
| UNIUSDT | 2021-12-01 |
| TRXUSDT | 2021-12-01 |
| FILUSDT | 2021-12-01 |
| RUNEUSDT | 2021-12-01 |
| 1000SHIBUSDT | 2021-12-01 |
| OPUSDT | 2022-06-01 |
| INJUSDT | 2022-08-17 |
| APTUSDT | 2022-10-19 |
| ARBUSDT | 2023-03-23 |
| SUIUSDT | 2023-05-03 |
| TONUSDT | 2024-03-01 |

### Granularity

- **288 data points per day** (one every 5 minutes)
- Can be aggregated to any desired interval (15min, 1h, 4h, daily)

### Data Quality Notes

- 2020 files may contain duplicate rows (same timestamp appearing twice); deduplicate by timestamp
- 2026 files have clean data with exactly 288 unique timestamps per day
- The `count_long_short_ratio` is a ratio (e.g., 1.54 means 60.6% long, 39.4% short)
- To convert: `long_pct = ratio / (1 + ratio)`, `short_pct = 1 / (1 + ratio)`
- Cross-verified against Binance live API: values match

### How to Download

```bash
# Single file
curl -O "https://data.binance.vision/data/futures/um/daily/metrics/BTCUSDT/BTCUSDT-metrics-2024-01-15.zip"

# Date range (see fetch_ls_data.py script)
# No authentication required
# No rate limits observed (but be respectful)
```

---

## SOURCE #2: Bybit API (Free, No Auth Required)

### Endpoint

```
GET https://api.bybit.com/v5/market/account-ratio
```

### Parameters

| Parameter | Required | Type | Description |
|-----------|----------|------|-------------|
| category | Yes | string | `linear` or `inverse` |
| symbol | Yes | string | e.g., `BTCUSDT` |
| period | Yes | string | `5min`, `15min`, `30min`, `1h`, `4h`, `1d` |
| startTime | No | int (ms) | Start timestamp in milliseconds |
| endTime | No | int (ms) | End timestamp in milliseconds |
| limit | No | int | Max 500, default 50 |
| cursor | No | string | Pagination cursor |

### Response Format

```json
{
    "retCode": 0,
    "retMsg": "OK",
    "result": {
        "list": [
            {
                "symbol": "BTCUSDT",
                "buyRatio": "0.5957",
                "sellRatio": "0.4043",
                "timestamp": "1774224000000"
            }
        ],
        "nextPageCursor": "lastid=0&lasttime=1773878400"
    }
}
```

### History Depth (Verified by Testing)

| Symbol | Earliest Data |
|--------|--------------|
| BTCUSDT | ~2020-08-05 |
| ETHUSDT | ~2021-01-01 (or earlier) |
| LTCUSDT | ~2021-01-01 (or earlier) |
| LINKUSDT | ~2021-01-01 (or earlier) |
| SOLUSDT | ~2021-08-01 |
| DOGEUSDT | ~2021-08-01 |
| XRPUSDT | ~2021-08-01 |
| ADAUSDT | ~2021-08-01 |
| AVAXUSDT | ~2022-02-01 |

### Rate Limits

- No API key required
- Rate limit: 10 requests per second (public endpoints)
- Use `nextPageCursor` for pagination when fetching large date ranges

### Pros

- No authentication required
- Long history (4-5 years for major pairs)
- Support for hourly and daily granularity
- Pagination support via cursor

### Cons

- Need to paginate through results (500 records per page max)
- Returns data in descending order (newest first)
- Only covers Bybit exchange data (not Binance)

---

## SOURCE #3: OKX API (Free, No Auth Required)

### Endpoints

**Currency-level (all futures + swaps combined):**
```
GET https://www.okx.com/api/v5/rubik/stat/contracts/long-short-account-ratio
```

**Contract-level (specific perpetual):**
```
GET https://www.okx.com/api/v5/rubik/stat/contracts/long-short-account-ratio-contract
```

**Top trader by account:**
```
GET https://www.okx.com/api/v5/rubik/stat/contracts/long-short-account-ratio-contract-top-trader
```

### Parameters

| Parameter | Required | Description |
|-----------|----------|-------------|
| ccy | Yes (currency-level) | Currency, e.g., `BTC` |
| instId | Yes (contract-level) | Instrument ID, e.g., `BTC-USDT-SWAP` |
| period | No | `5m`, `1H`, `1D` (default `5m`) |
| end | No | Timestamp (ms) for pagination (get data BEFORE this time) |

### History Depth (Verified by Testing)

- **Currency-level**: Hard-capped at **180 data points** (180 days at daily). No pagination possible.
- **Contract-level**: **100 records per page**, but paginable via `end` parameter. Goes back to approximately **Dec 31, 2023** (~15 months).

### Rate Limits

- No API key required for these public endpoints
- OKX public API rate limit: 20 requests per 2 seconds

### Response Format

```json
{
    "code": "0",
    "data": [
        ["1774281600000", "1.18"]   // [timestamp_ms, ratio]
    ]
}
```

### Pros

- No authentication required
- Provides OKX-specific positioning data
- Multiple metric types available

### Cons

- Currency-level capped at 180 days, no pagination
- Contract-level only goes back ~15 months
- Less history than Bybit or data.binance.vision

---

## SOURCE #4: Binance Live API (Limited)

### Endpoint

```
GET https://fapi.binance.com/futures/data/globalLongShortAccountRatio
```

### Verified Limitation

- **Maximum 28 days of history** - confirmed by testing
- API rejects `startTime` parameters older than ~28 days with error code -1130
- This is the same data that appears in data.binance.vision metrics, just limited to recent history

### Use Case

Only useful for getting the latest few weeks of data or for real-time monitoring.

---

## SOURCE #5: Coinalyze API (Free with Registration)

### Endpoint

```
GET https://api.coinalyze.net/v1/long-short-ratio-history
```

### Parameters

- `symbols`: e.g., `BTCUSDT_PERP.A` (symbol + exchange code)
- `interval`: `1min` to `daily`
- `from`: Unix timestamp (seconds)
- `to`: Unix timestamp (seconds)
- `api_key`: Required (free registration)

### Authentication

- Requires free account registration at coinalyze.net
- API key provided in header or query parameter
- Rate limit: 40 calls per minute

### Data Retention

- **Daily granularity**: Unlimited history (old data NOT deleted)
- **Intraday granularity**: 1,500-2,000 data points retained; old data deleted daily

### Response Format

```json
[
    {
        "symbol": "BTCUSDT_PERP.A",
        "history": [
            {"t": 1672531200, "r": 1.35, "l": 57.4, "s": 42.6}
        ]
    }
]
```

### Pros

- Free with registration
- Daily data has unlimited history
- Covers multiple exchanges (Binance, Bybit, OKX, etc.)
- Rich response with ratio + long% + short%

### Cons

- Requires account creation and API key
- Previous agents reported 401 errors (likely missing/invalid key)
- Intraday data is ephemeral (1500-2000 data points only)
- Cannot test without registration in this environment

---

## SOURCE #6: Kaggle Datasets

### jesusgraterol/bitcoin-longshort-ratio-binance-futures

- **URL**: https://www.kaggle.com/datasets/jesusgraterol/bitcoin-longshort-ratio-binance-futures
- **Coverage**: BTC only
- **Last updated**: October 2023
- **License**: CC0 (Public Domain)
- **Size**: ~147 KB compressed
- **Built from**: Binance `globalLongShortAccountRatio` API
- **Builder repo**: https://github.com/jesusgraterol/binance-futures-dataset-builder

### maxisoft/binance-futures-stats

- **URL**: https://www.kaggle.com/datasets/maxisoft/binance-futures-stats
- **Coverage**: Multiple coins
- **Last updated**: March 2025
- **Size**: 447 MB compressed
- **Version**: 253 (frequently updated)
- **Builder repo**: https://github.com/maxisoft/binance-dumper
- **Data collected**: Top trader L/S account ratio, top trader L/S position ratio, global L/S account ratio, taker buy/sell volume, open interest

---

## SOURCES TESTED AND REJECTED

### CryptoQuant

- Has L/S ratio data and supports CSV export via API
- **API requires Professional plan ($109/month minimum)**
- Free tier does NOT include API access
- https://cryptoquant.com/pricing

### CoinGlass

- Has comprehensive L/S ratio data
- API returns `{"code":"30001","msg":"API key missing.","success":false}`
- Public v2 endpoint returns HTTP 500
- Requires paid API subscription
- https://www.coinglass.com/pricing

### Glassnode

- Does NOT have a traditional L/S account ratio endpoint
- Has "Long/Short Bias" as a newer dashboard feature
- Full API requires Professional plan
- https://glassnode.com

### Santiment

- Has "MVRV Long/Short Divergence" which is a completely different metric (on-chain, not exchange-based)
- API requires paid plan for real-time/historical data
- https://api.santiment.net/

### TradingView

- Displays Binance L/S data in charts
- No API for extracting this data
- Charts use Binance's live API as backend

### CryptoDataDownload

- Focuses on OHLCV and funding rate data
- Does NOT have L/S ratio data
- https://www.cryptodatadownload.com/

### data.binance.vision (klines/funding)

- The `klines` and `funding` folders do NOT contain L/S ratio data
- But the `metrics` folder DOES (documented above as Source #1)

---

## RECOMMENDED STRATEGY

### Primary: data.binance.vision Metrics (for backtesting)

Use the `/workspace/crypto_backtest/tools/fetch_ls_data.py` script to bulk download historical metrics.

**Advantages:**
- 793 symbols, 5-minute granularity, back to Sep 2020 (BTC) / Dec 2021 (most tokens)
- Free, no auth, no rate limits
- Contains ALL 4 L/S ratio variants plus open interest
- Binance is the largest exchange by volume

### Supplementary: Bybit API (for cross-exchange validation)

Add Bybit data to compare Binance vs Bybit positioning. Bybit goes back to ~Aug 2020 for BTC.

### Supplementary: OKX API (for additional exchange coverage)

OKX data only goes back ~15 months at contract level, but useful for recent cross-exchange analysis.

### For Real-Time: Coinalyze (if you register for free API key)

Coinalyze aggregates L/S data across exchanges with unlimited daily history. Good for ongoing monitoring.

### Running the Fetcher

```bash
# Download BTCUSDT metrics for 2024
python3 /workspace/crypto_backtest/tools/fetch_ls_data.py \
    --symbols BTCUSDT ETHUSDT SOLUSDT \
    --start-date 2024-01-01 \
    --end-date 2026-03-22 \
    --output-dir /workspace/crypto_backtest/data/ls_ratio \
    --source binance_vision

# Download from Bybit API
python3 /workspace/crypto_backtest/tools/fetch_ls_data.py \
    --symbols BTCUSDT ETHUSDT SOLUSDT \
    --start-date 2022-01-01 \
    --end-date 2026-03-22 \
    --output-dir /workspace/crypto_backtest/data/ls_ratio \
    --source bybit
```

---

## APPENDIX: Column Mapping Between Sources

| Metric | Binance Vision (`metrics`) | Binance API | Bybit API | OKX API |
|--------|---------------------------|-------------|-----------|---------|
| Global L/S Account Ratio | `count_long_short_ratio` | `longShortRatio` | `buyRatio/sellRatio` | `data[1]` (ratio) |
| Top Trader L/S Account | `count_toptrader_long_short_ratio` | `topLongShortAccountRatio` | N/A | Top trader endpoint |
| Top Trader L/S Position | `sum_toptrader_long_short_ratio` | `topLongShortPositionRatio` | N/A | Top trader position endpoint |
| Taker Buy/Sell Ratio | `sum_taker_long_short_vol_ratio` | `takerLongShortRatio` | N/A | N/A |
| Open Interest | `sum_open_interest` | `openInterestHist` | Separate endpoint | Separate endpoint |

### Ratio Interpretation

- **Binance/data.binance.vision/OKX**: Ratio = long/short (e.g., 1.5 means 60% long, 40% short)
- **Bybit**: Returns `buyRatio` and `sellRatio` directly as decimals (e.g., 0.60, 0.40)
- To convert Binance ratio to percentages: `long% = ratio / (1 + ratio)`, `short% = 1 / (1 + ratio)`
