# Binance Vision Orderbook Data — Perp Futures

Daily backfill of Binance **perp futures** bookDepth and aggTrades from the
[Binance Vision](https://data.binance.vision) public archive.

## What was fetched

| Dataset | Symbols | Range | Files | Size |
|---|---|---|---|---|
| `bookdepth` (raw snapshots) | BTCUSDT, ETHUSDT, SOLUSDT | 2025-04-07 .. 2026-04-07 | 1094 / 1098 | ~470 MB |
| `trades_1s` (1-second bars) | BTCUSDT, ETHUSDT, SOLUSDT | 2025-04-07 .. 2026-04-07 | 1095 / 1098 | ~2.6 GB |

Missing days are the current day (2026-04-07, T+1 latency) and a single SOL
bookDepth gap on 2026-01-14. Everything else is present.

## Layout

```
data/perp/binance/{SYMBOL}/bookdepth/{YYYY-MM-DD}.parquet
data/perp/binance/{SYMBOL}/trades_1s/{YYYY-MM-DD}.parquet
```

## Schemas

### `bookdepth/*.parquet` — raw snapshots (as-published)
| Column | Type | Notes |
|---|---|---|
| `ts` | datetime64[us] | snapshot timestamp (UTC, tz-naive) |
| `percentage` | int16 | one of {-5,-4,-3,-2,-1,1,2,3,4,5} — price offset from mid |
| `depth` | float64 | base-asset size on the book at that level |
| `notional` | float64 | quote (USD) notional at that level |

One snapshot ~ every 33 seconds, 10 rows per snapshot (5 bid levels + 5 ask
levels). ~27K rows/day BTC. No resampling — this is the depth curve as
published by Binance.

### `trades_1s/*.parquet` — 1-second bars (resampled from aggTrades)
| Column | Type | Notes |
|---|---|---|
| `ts` | datetime64[s] | 1-second bar timestamp (UTC, tz-naive) |
| `open, high, low, close` | float32 | price OHLC within the second |
| `buy_volume, sell_volume` | float32 | base-asset volume by aggressor side |
| `buy_notional, sell_notional` | float32 | quote (USD) volume by aggressor side |
| `num_trades` | int32 | count of aggTrades in the second |
| `vwap` | float32 | notional-weighted average price |

Empty seconds (no trades) are dropped. Taker-side convention:
`is_buyer_maker == False` -> aggressor is buyer -> `buy_volume`. Raw aggTrades
tick data is discarded after resampling.

## Usage

Loader one-liner (1s bars, single day):
```python
import pandas as pd
df = pd.read_parquet("data/perp/binance/BTCUSDT/trades_1s/2025-06-01.parquet")
```

Load a date range (concat daily parquets):
```python
from pathlib import Path
import pandas as pd
paths = sorted(Path("data/perp/binance/BTCUSDT/trades_1s").glob("2025-*.parquet"))
df = pd.concat([pd.read_parquet(p) for p in paths], ignore_index=True)
```

## Fetcher scripts

```bash
/workspace/venv/bin/python tools/fetch_binance_vision_bookdepth.py \
    --symbols BTCUSDT,ETHUSDT,SOLUSDT --start 2025-04-07 --end 2026-04-07

/workspace/venv/bin/python tools/fetch_binance_vision_aggtrades.py \
    --symbols BTCUSDT,ETHUSDT,SOLUSDT --start 2025-04-07 --end 2026-04-07
```

Both accept `--force` (re-download + overwrite), `--workers N`, and skip dates
that already have a parquet. 404s and parse errors are logged and the script
continues. To extend to more symbols, just add them to `--symbols` (comma
separated perp pair names, e.g. `BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT`).

## Known limitations

- **T+1 latency.** Daily archives land the day after the session — there is no
  intraday data via this path.
- **Pre-aggregated depth curve, not raw L2.** The `bookDepth` archive is a
  10-level snapshot at ±{1,2,3,4,5}% offsets from mid — not the full orderbook.
  It is published roughly every 33s. Good for liquidity/market-impact features;
  not suitable for top-of-book microstructure work.
- **1s bar resampling is lossy** by design. Tick-level aggTrades are discarded
  after resampling to keep disk footprint manageable. If you need raw ticks,
  re-fetch from Binance Vision directly.
- **float32 prices.** Bar prices are stored as float32 (~1e-7 relative
  precision, far below one tick for BTC/ETH/SOL). If you need float64, cast on
  load.
- **Disk footprint.** 365d × 3 symbols is ~3 GB total, dominated by the 1s
  bars for BTC/ETH/SOL (each ~900 MB). Budget accordingly before widening.

## Phase 2 fetch report (2026-04-08)

Extended Mission F coverage from BTC/ETH/SOL to the top-10 alt perps using the
existing Phase 1 tools unchanged (`fetch_binance_vision_bookdepth.py`,
`fetch_binance_vision_aggtrades.py`, `build_microstructure_signals.py`).

**Symbols fetched (10):** BNBUSDT, XRPUSDT, DOGEUSDT, ADAUSDT, AVAXUSDT,
LINKUSDT, DOTUSDT, NEARUSDT, ATOMUSDT, LTCUSDT

**Date range:** 2025-04-10 to 2026-04-05 inclusive (361 calendar days)

**Total disk used (all 10 symbols):** ~7.0 GB
(workspace `data/perp/binance` grew from 3.7 GB to 11 GB during Phase 2)

**File counts (per symbol):**

| Symbol   | bookdepth | trades_1s | microstructure_1min.parquet |
|----------|-----------|-----------|------------------------------|
| BNBUSDT  | 361       | 361       | 71 MB                        |
| XRPUSDT  | 360*      | 361       | 70 MB                        |
| DOGEUSDT | 361       | 361       | 71 MB                        |
| ADAUSDT  | 361       | 361       | 70 MB                        |
| AVAXUSDT | 361       | 361       | 71 MB                        |
| LINKUSDT | 361       | 361       | 71 MB                        |
| DOTUSDT  | 361       | 361       | 70 MB                        |
| NEARUSDT | 361       | 361       | 70 MB                        |
| ATOMUSDT | 361       | 361       | 70 MB                        |
| LTCUSDT  | 361       | 361       | 70 MB                        |

\*XRPUSDT is missing 2026-01-14 bookDepth (upstream Binance Vision 404). All
other day-symbol pairs downloaded cleanly.

**Errors / 404s:**
- bookdepth: 3609/3610 ok, 1 404 (XRPUSDT 2026-01-14), 0 errors — 802s wall
- aggTrades: 3610/3610 ok, 0 404s, 0 errors, `total_bars=208,503,078` — 1531s wall
- microstructure build: 10/10 ok, phase `all` (DtM/OFI-T/VDV + OFI-M merge)

**Microstructure sanity check (BNBUSDT):**
- rows: 519,840 (361 days × 1440 min)
- date range: 2025-04-10 00:00 → 2026-04-05 23:59
- 32 columns: `ts, mid_price, dtm_bid_{1,5}pct, dtm_ask_{1,5}pct,
  dtm_asymmetry_5pct, dtm_total_5pct, dtm_bid_5pct_z168h, dtm_total_5pct_z168h,
  dtm_bid_velocity_1h, ofi_t_{1min,5min,30min,4h}, ofi_t_notional_5min,
  vwap_{5min,30min,4h}, dev_{5min,30min,4h}, vdv_{5min,30min,4h},
  ofi_m_{1pct,5pct,total}, ofi_m_{1pct,total}_5min, ofi_m_total_30min,
  ofi_m_total_z168h`
- non-null coverage: Phase 1 signals 99.9%+; OFI-M rolling z-score 90.1%
  (reduced by 168h warm-up window, expected)

All 10 symbol-level `microstructure_1min.parquet` files are now ready for
strategy research alongside the existing BTC/ETH/SOL data. BTC/ETH/SOL data was
not touched.
