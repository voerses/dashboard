# Long/Short Ratio Data Acquisition Report
**Date:** 2026-03-23 | **Goal:** 6+ months of hourly L/S ratio data for signal validation

## Executive Summary

Binance hard-limits L/S ratio history to **28 days** (672 hourly records). This is an absolute
server-side cap that cannot be bypassed by pagination, API keys, or any other technique.
Getting 6+ months requires either (a) accumulating Binance data daily via cron, or (b) using
a third-party data provider.

---

## 1. Binance API Findings

### What We Tested

- `globalLongShortAccountRatio` (global L/S account ratio)
- `topLongShortAccountRatio` (top 20% traders by margin, account ratio)
- `topLongShortPositionRatio` (top 20% traders by margin, position ratio)
- `takerlongshortRatio` (taker buy/sell volume ratio)

### Pagination Discovery

The default API call returns 500 records (~20.8 days at 1h). By using `endTime` parameter
set to the earliest timestamp from the previous page, we can page backwards to get an
additional ~172 records, reaching the full **28-day / 672-hour hard limit**.

Tested boundary:
- 670 hours (27.9 days) ago: OK
- 672 hours (28.0 days) ago: FAIL (HTTP 400)
- `startTime` parameter fails for anything > ~27 days ago regardless of value

### Current Data Collected

| File | Rows | Symbols | Date Range |
|------|------|---------|------------|
| `global_ls_ratio.parquet` | 14,268 | 22 | Feb 23 - Mar 23, 2026 |
| `top_ls_account_ratio.parquet` | 14,268 | 22 | Feb 23 - Mar 23, 2026 |
| `top_ls_position_ratio.parquet` | 14,268 | 22 | Feb 23 - Mar 23, 2026 |
| `taker_buy_sell_vol.parquet` | 14,265 | 22 | Feb 23 - Mar 23, 2026 |

**Tokens covered (19 active + 3 legacy):**
BTCUSDT, ETHUSDT, SOLUSDT, DOGEUSDT, XRPUSDT, ADAUSDT, AVAXUSDT, LINKUSDT,
DOTUSDT, UNIUSDT, AAVEUSDT, NEARUSDT, APTUSDT, ARBUSDT, OPUSDT, SUIUSDT,
INJUSDT, SEIUSDT, WLDUSDT + BNBUSDT, LTCUSDT, WIFUSDT (legacy)

**MATICUSDT** - Delisted from Binance futures (Polygon rebranded to POL).

### Accumulation Strategy

The fetch script (`tools/fetch_binance_ls_history.py`) supports incremental merge.
Running daily via cron will accumulate data over time:
- After 1 month: ~56 days of data
- After 3 months: ~112 days of data
- After 6 months: ~196 days of data (goal met)

This is slow but free and requires no third-party accounts.

---

## 2. Third-Party Data Sources

### Coinalyze (FREE - Best Option)

- **URL:** https://api.coinalyze.net/v1/doc/
- **Pricing:** FREE (requires free account for API key)
- **Rate limit:** 40 calls/minute
- **L/S ratio endpoint:** `/v1/long-short-ratio-history`
- **Intraday retention:** 1,500-2,000 data points (deleted daily)
- **Daily retention:** UNLIMITED (old data not deleted)
- **Coverage:** 300+ coins, data from Binance and other exchanges
- **Data source:** Exchange-provided (same underlying data as Binance API)

**Assessment:** Best free option for historical L/S ratio. Daily granularity data goes back
to exchange listing date. Intraday (hourly) is limited to ~83 days (2000 / 24). Could
provide 1-2 years of daily L/S data immediately, or ~83 days of hourly data.

**Action needed:** Sign up at coinalyze.net, generate API key, build fetcher.

### Tardis.dev (PAID - Best Quality)

- **URL:** https://tardis.dev/
- **L/S ratio data since:** 2020-10-28 (5+ years!)
- **Channels:** `globalLongShortAccountRatio`, `topLongShortAccountRatio`,
  `topLongShortPositionRatio`, `takerlongshortRatio`
- **Granularity:** Minute-level (queried REST endpoint every minute)
- **Format:** CSV downloads + replay API
- **Free tier:** Only first day of each month (not useful for backtesting)
- **Minimum order:** $300
- **Monthly plan:** ~$300+ (gives 4 months of historical access)
- **Quarterly plan:** ~$300+ (gives 12 months of historical access)

**Assessment:** Gold standard for L/S ratio data -- 5+ years at minute granularity.
Too expensive for exploration, but worth it if the signal validates.

### CoinGlass (PAID)

- **URL:** https://www.coinglass.com/pricing
- **Hobbyist:** $29/mo (80+ endpoints, 30 req/min)
- **Startup:** $79/mo (130+ endpoints, 80 req/min, 360 days hourly data)
- **Standard:** $299/mo (150+ endpoints, 300 req/min, 720 days hourly data)
- **L/S ratio endpoints:** Global, Top Trader Account, Top Trader Position
- **All-time daily data** on all tiers

**Assessment:** $79/mo Startup tier gives 360 days of hourly L/S data -- sufficient
for validation. More expensive than Coinalyze but better documented API.

### Kaggle / binance-futures-dataset-builder

- **URL:** https://kaggle.com/datasets/jesusgraterol/bitcoin-longshort-ratio-binance-futures
- **Coverage:** Bitcoin only (no multi-token)
- **Last updated:** October 2023 (stale)
- **Format:** CSV
- **License:** CC0

**Assessment:** Only covers BTC, last updated 2023. Not useful for our multi-token needs.

### CryptoDataDownload

- **URL:** https://www.cryptodatadownload.com/
- **Pricing:** $49.99/mo for API; free CSV downloads for basic OHLCV
- **L/S ratio:** Available in API (Count L/S Ratio, Sum Taker L/S Ratio)

**Assessment:** Moderate option. API-gated L/S data at $50/mo.

---

## 3. Recommended Plan

### Immediate (Free, Start Today)

1. **Run `fetch_binance_ls_history.py` daily via cron** to accumulate Binance L/S data.
   The script merges incrementally -- each run adds new data while keeping old records.
   Added to `cron_altdata.sh`.

2. **Sign up for Coinalyze free account** and build a daily-granularity L/S fetcher.
   This should give us 1-2 years of daily L/S data immediately (for all 20+ tokens).
   Daily data is less granular but sufficient for a first validation pass.

### Short-term (If Signal Shows Promise with Daily Data)

3. **Subscribe to CoinGlass Startup ($79/mo)** for 360 days of hourly L/S data.
   This gives us the hourly granularity we need for proper backtesting.
   Cancel after 1 month once data is downloaded.

### If Signal Validates

4. **Subscribe to Tardis.dev ($300/quarter)** for 5+ years of minute-level data.
   This is the definitive dataset for a production-ready backtest.

### Timeline to 6 Months of Hourly Data

| Approach | Cost | Time to 6 Months | Quality |
|----------|------|-------------------|---------|
| Binance cron only | $0 | ~6 months real-time | High (source data) |
| Coinalyze daily | $0 | Immediate | Medium (daily only) |
| CoinGlass Startup | $79 | Immediate | High (hourly, 12mo) |
| Tardis.dev | $300 | Immediate | Highest (minute, 5yr) |

---

## 4. Files Created/Modified

| File | Purpose |
|------|---------|
| `tools/fetch_binance_ls_history.py` | Paginated Binance L/S fetcher with incremental merge |
| `tools/cron_altdata.sh` | Updated to include L/S history in hourly cron |
| `data/alternative/binance_positioning/*.parquet` | 28 days of data for 22 symbols, 4 endpoints |
