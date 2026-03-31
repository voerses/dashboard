# Data Manifest — DO NOT DELETE OR MODIFY DATA FILES

Last verified: 2026-03-30 (full backfill completed)

## Critical Data Files

### Perp 1H Cache (199 tokens) — PRIMARY BACKTEST DATA
```
data/perp/1h_cache/*.parquet
Total: 199 files
Period: 2020-2026 (varies by token listing date)
Source: Multi-exchange merge (Binance > Kraken > Hyperliquid) via build_parquet_cache.py
Columns: open_time, open, high, low, close, volume, funding_rate
Status: NO stale tokens, NO gaps (Mar 5-13 and Mar 14-17 gaps FILLED 2026-03-30)
Only remaining gaps: historical Binance maintenance windows (2020-2021, ~3-6h each, unfillable)
```

### Spot 1H Cache (135 tokens)
```
data/spot/1h_cache/*.parquet
Total: 135 files
Period: 2020-2026 (varies by token listing date)
Source: Binance spot via build_parquet_cache.py + live promotion
Status: Mar 5-13 gap FILLED. 42 tokens extended from ~309 bars to ~17,800 bars (2026-03-30).
LIT and XMR DELETED (delisted from Binance 2026-03-30).
```

### Perp 1M Cache (193 tokens) — LIVE ENTRY RESOLUTION
```
data/perp/1m_cache/*.parquet
Total: 193 files
Source: Binance Futures 1-minute candles via live_fetcher.py + backfill scripts
Status: CVC/PUMP/LIT gaps FILLED (2026-03-30). ICP and 2022 historical gaps being filled.
Used for live BB cross detection between hourly ticks, not for backtests.
```

### Raw CSVs (multi-exchange)
```
data/perp/binance/1h_ohlcv/     — 223 tokens
data/perp/binance/funding/       — 221 tokens (8h rates)
data/perp/kraken/funding/         — 319 tokens (4h rates)
data/perp/hyperliquid/ohlcv/     — 70 tokens
data/perp/hyperliquid/funding/    — 35 tokens (8h rates)
data/spot/binance/1h_ohlcv/      — 132 tokens
```

## Delisted Tokens

| Token | Market | Delisted From | Action | Date |
|-------|--------|---------------|--------|------|
| LIT | Spot | Binance | Spot data deleted | 2026-03-30 |
| XMR | Spot | Binance | Spot data deleted | 2026-03-30 |

## Data Integrity Notes

- **Gap threshold:** 1 hour (changed from 24h on 2026-03-30 in both `live_fetcher.py` and `data_maintenance.py`)
- **Maintenance windows:** ~3-6h gaps from Binance scheduled maintenance in 2020-2021 cannot be filled (exchange was offline)
- **4H/daily data:** Computed on-the-fly from 1H via `pandas.resample()` — not stored separately
- **Funding rates:** Merged into 1H parquets as `funding_rate` column, normalized to hourly (Binance 8h÷8, Kraken 4h÷4, Hyperliquid 8h÷8)
- **1000-prefix tokens:** Prices stored at raw exchange level (no /1000 division). 12 tokens: PEPE, SHIB, FLOKI, BONK, LUNC, SATS, RATS, CAT, CHEEMS, WHY, X, XEC

## Data Size
Total: ~500MB+ in data/ (gitignored, re-fetchable but takes 60+ minutes)
