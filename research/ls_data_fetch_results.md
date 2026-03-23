# Long/Short Ratio Data Fetch Results

**Date:** 2026-03-23
**Purpose:** Fetch historical L/S account ratio data beyond Binance's 28-day limit

## Executive Summary

**Bybit is the clear winner** for historical L/S ratio data. It provides FREE daily data going back to August 2020 (5+ years) with no authentication required. OKX is limited to 180 days of daily data. Binance remains capped at 28 days.

## API Findings

### Bybit (`/v5/market/account-ratio`)
- **Max history:** Data from August 2020 to present (5+ years for BTC)
- **Rate limits:** No explicit rate limit headers returned; 500 records per request
- **Pagination:** Uses `startTime`/`endTime` params in millisecond timestamps
- **Authentication:** None required (public endpoint)
- **Data fields:** buyRatio, sellRatio (both sum to 1.0), timestamp
- **Resolution:** Daily (`period=1d`)
- **No server-side history cap** -- unlike Binance, there is no 28-day limit

### OKX (`/api/v5/rubik/stat/contracts/long-short-account-ratio`)
- **Max history:** ~180 days with `period=1D`
- **Rate limits:** Not explicitly documented, but returns `50030 Illegal time range` for requests beyond ~180 days
- **Authentication:** None required
- **Data fields:** timestamp, ratio (long/short ratio as single float)
- **Additional endpoint:** `/long-short-account-ratio-contract-top-trader` -- top trader L/S ratio, but only ~100 recent records
- **Pagination:** `after` param exists but does NOT extend history beyond the 180-day window

### Binance (for reference)
- **Max history:** 28 days (server-enforced hard limit)
- **Not fetched in this run** -- already known limitation

## Data Collected

### Bybit (Primary Source)

| Symbol   | Rows | Start Date | End Date   | Days of History |
|----------|------|------------|------------|-----------------|
| BTCUSDT  | 2056 | 2020-08-05 | 2026-03-23 | 2057            |
| ETHUSDT  | 1978 | 2020-10-22 | 2026-03-23 | 1979            |
| LINKUSDT | 1978 | 2020-10-22 | 2026-03-23 | 1979            |
| ADAUSDT  | 1830 | 2021-03-19 | 2026-03-23 | 1831            |
| XRPUSDT  | 1774 | 2021-05-14 | 2026-03-23 | 1775            |
| DOGEUSDT | 1754 | 2021-06-03 | 2026-03-23 | 1755            |
| SOLUSDT  | 1727 | 2021-06-30 | 2026-03-23 | 1728            |
| BNBUSDT  | 1727 | 2021-06-30 | 2026-03-23 | 1728            |
| AVAXUSDT | 1650 | 2021-09-16 | 2026-03-23 | 1650            |
| SUIUSDT  | 1055 | 2023-05-04 | 2026-03-23 | 1055            |

**Total Bybit rows: 17,529**

### OKX (Secondary Source)

| Symbol   | Rows | Start Date | End Date   | Top Trader Data |
|----------|------|------------|------------|-----------------|
| BTCUSDT  | 180  | 2025-09-25 | 2026-03-23 | Yes (100 days)  |
| ETHUSDT  | 180  | 2025-09-25 | 2026-03-23 | Yes (100 days)  |
| SOLUSDT  | 180  | 2025-09-25 | 2026-03-23 | Yes (100 days)  |
| BNBUSDT  | 180  | 2025-09-25 | 2026-03-23 | Yes (100 days)  |
| XRPUSDT  | 180  | 2025-09-25 | 2026-03-23 | Yes (100 days)  |
| DOGEUSDT | 180  | 2025-09-25 | 2026-03-23 | Yes (100 days)  |
| ADAUSDT  | 180  | 2025-09-25 | 2026-03-23 | Yes (100 days)  |
| AVAXUSDT | 180  | 2025-09-25 | 2026-03-23 | Yes (100 days)  |
| LINKUSDT | 180  | 2025-09-25 | 2026-03-23 | Yes (100 days)  |
| SUIUSDT  | 180  | 2025-09-25 | 2026-03-23 | Yes (100 days)  |

**Total OKX rows: 1,800** (all tokens have identical 180-day window)

## File Locations

All parquet files saved to:
```
/workspace/crypto_backtest/data/alternative/ls_ratio_extended/
```

### Bybit files (primary -- use these for backtesting)
- `bybit_btcusdt_ls_ratio.parquet`
- `bybit_ethusdt_ls_ratio.parquet`
- `bybit_solusdt_ls_ratio.parquet`
- `bybit_bnbusdt_ls_ratio.parquet`
- `bybit_xrpusdt_ls_ratio.parquet`
- `bybit_dogeusdt_ls_ratio.parquet`
- `bybit_adausdt_ls_ratio.parquet`
- `bybit_avaxusdt_ls_ratio.parquet`
- `bybit_linkusdt_ls_ratio.parquet`
- `bybit_suiusdt_ls_ratio.parquet`

### OKX files (supplementary -- cross-exchange comparison)
- `okx_{token}_ls_ratio.parquet` (same 10 tokens)

## Column Schema

### Bybit files
| Column     | Type     | Description                                    |
|------------|----------|------------------------------------------------|
| timestamp  | datetime | UTC date of the data point                     |
| symbol     | string   | Token symbol (e.g., BTCUSDT)                   |
| buy_ratio  | float    | Fraction of accounts that are long (0-1)       |
| sell_ratio | float    | Fraction of accounts that are short (0-1)      |
| ls_ratio   | float    | Long/Short ratio (buy_ratio / sell_ratio)      |
| exchange   | string   | Always "bybit"                                 |

### OKX files
| Column               | Type     | Description                               |
|----------------------|----------|-------------------------------------------|
| timestamp            | datetime | UTC date of the data point                |
| symbol               | string   | Token symbol (e.g., BTCUSDT)              |
| ls_ratio             | float    | Long/Short ratio (direct from API)        |
| buy_ratio            | float    | Derived: ratio / (1 + ratio)              |
| sell_ratio           | float    | Derived: 1 / (1 + ratio)                 |
| exchange             | string   | Always "okx"                              |
| top_trader_ls_ratio  | float    | Top trader L/S ratio (NaN if unavailable) |

## Limitations

1. **Bybit data represents ALL accounts**, not top traders specifically. It measures what fraction of accounts hold long vs short positions (not position size weighted).
2. **OKX daily data is capped at 180 days.** The `after` pagination parameter does not extend beyond this window. The 5-minute resolution endpoint only returns ~3 days.
3. **SUI has the shortest history** (from May 2023) because the token was listed later.
4. **Data availability per token depends on when the perpetual contract was listed on Bybit.** BTC/ETH/LINK go back to late 2020; most altcoins start mid-2021; SUI starts 2023.
5. **Bybit L/S ratio is account-based, not position-weighted.** A whale with a $10M position counts the same as a retail trader with $100.

## Recommendations for Backtesting

1. **Use Bybit as the primary L/S ratio source** -- 5+ years of history vastly exceeds Binance (28 days) and OKX (180 days).
2. **Cross-validate with OKX** for the overlapping 180-day window to check if exchange-specific biases exist.
3. **Consider fetching hourly data** (`period=1h` on Bybit) for higher-resolution signals if needed, though the pagination cost increases proportionally.
4. **Re-fetch periodically** -- run this script monthly to extend the dataset forward.
