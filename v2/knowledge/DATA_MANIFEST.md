# Data Manifest — DO NOT DELETE OR MODIFY DATA FILES

Last verified: 2026-02-28

## Critical Data Files

### 1H Cache (49 tokens) — PRIMARY BACKTEST DATA
```
real_data/1h_cache/*.parquet
Total: 49 files, 855,223 bars
Period: Jan 2024 - Jan 2026
Source: Binance Vision 1-minute data, aggregated to 1H
Columns: open_time, open, high, low, close, volume, quote_volume, trades, taker_buy_base, taker_buy_quote
```

### 4H Cache (49 tokens) — DERIVED FROM 1H
```
real_data/4h_cache/*.parquet
Total: 49 files
Source: Aggregated from 1H data
```

### 1M Cache (49 tokens) — DAILY MICROSTRUCTURE FEATURES
```
real_data/1m_cache/*.parquet
Total: 49 files
Source: Aggregated daily features from 1-minute data (VPIN, realized vol, etc.)
```

### Enriched Parquet — FULL FEATURE SET
```
real_data/all_tokens_enriched.parquet
Shape: 88,785 rows × 20 columns
Tokens: 57 (includes 8 without 1H data)
Period: 2017-12-13 to 2026-02-28
Features: vpin, realized_vol, taker_buy_ratio, amihud_1m, vwap_deviation,
          intraday_skew, intraday_kurtosis, parkinson_vol, volume_herfindahl,
          max_intraday_dd, trade_count, realized_var
```

### Daily CSVs (57 tokens) — RAW DAILY OHLCV
```
real_data/*_daily.csv
Total: 57 files
Source: Binance Vision daily data
```

## Token Coverage

### Tokens WITH 1H data (49):
AAVE, ADA, ALICE, APT, ARB, AVAX, BCH, BNB, BONK, BTC, CAKE, CHZ,
DASH, DENT, DOGE, DOT, ENA, ETH, FET, FIL, FLOKI, HBAR, ICP, INJ,
LINK, LTC, NEAR, OM, OP, PAXG, PENDLE, PENGU, PEPE, POL, SEI, SHIB,
SOL, SUI, TAO, TON, TRUMP, TRX, UNI, WIF, WLD, XLM, XRP, ZEC, ZRO

### Tokens WITHOUT 1H data (8):
KITE, ASTER, BARD, VIRTUAL, PUMP, ENSO, XPL, WLFI
(Too new for Binance Vision historical data)

## Data Size
Total: ~112MB in real_data/
