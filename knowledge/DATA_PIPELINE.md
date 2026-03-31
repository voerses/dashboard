# Data Pipeline — Architecture & Learnings

> **Last updated:** 2026-03-30

## Directory Structure

```
data/
├── perp/                          # Perpetual futures
│   ├── binance/
│   │   ├── 1h_ohlcv/             # Raw 1h OHLCV CSVs (223 tokens)
│   │   ├── funding/              # 8h funding rates (221 tokens)
│   │   └── oi/                   # Open interest (empty)
│   ├── kraken/
│   │   └── funding/              # 4h funding rates (319 tokens)
│   ├── hyperliquid/
│   │   ├── ohlcv/                # Raw 1h OHLCV CSVs (70 tokens)
│   │   └── funding/              # 8h funding rates (35 tokens)
│   ├── 1h_cache/                 # Historical parquets (199 tokens, promoted from live)
│   ├── 1m_cache/                 # 1-minute perp data (193 tokens, for live entry resolution)
│   └── live/                     # Live buffer (recent 1h bars, merged at read time)
│
├── spot/                          # Spot markets
│   ├── binance/
│   │   ├── 1h_ohlcv/             # Raw 1h OHLCV CSVs (132 tokens)
│   ├── 1h_cache/                 # Historical parquets (135 tokens, promoted from live)
│   └── live/                     # Live buffer (recent 1h bars, merged at read time)
│
├── quality_reports/               # Build quality reports (JSON)
├── .maintenance.lock              # Exclusive lock for data maintenance
└── maintenance.jsonl              # Audit log for all maintenance operations
```

## Live Data Architecture (kdb+ RDB/HDB Pattern)

The data pipeline uses a kdb+-inspired RDB (real-time database) / HDB (historical database) split:

### Two-tier storage
- **Historical (HDB):** `data/{market}/1h_cache/{TOKEN}_1h.parquet` — promoted, quality-checked data
- **Live buffer (RDB):** `data/{market}/live/{TOKEN}.parquet` — recent bars from API fetches

### Read-time merge
`v4/data_loader.py:load_token_data()` merges historical + live at read time. The caller sees a single continuous DataFrame. No manual merge step needed for consumers.

### Promotion (live → historical)
Every 4 hours (`PROMOTE_INTERVAL_S = 14400` in runner), `tools/promote_live.py:run_promotion()` rolls live buffer data into historical cache with QC checks + SHA-256 manifests + atomic writes. Logged to `data/promotions.jsonl`.

### Data maintenance (`v4/data_maintenance.py`)
`ensure_data_fresh()` orchestrates all data flows:

| Trigger | What runs | gap_threshold |
|---------|-----------|---------------|
| Runner startup | Full: backfill 1h gaps + promote + backfill 1m | 1 hour |
| Runner every 4h | Promote only (live → historical for 1h) | N/A |
| `--refresh` on backtest CLI | Full (same as startup) | 1 hour |
| Standalone `python -m v4.data_maintenance` | Full | 1 hour |

**Key parameters:**
- `gap_threshold_hours=1` — any gap > 1 hour triggers backfill (default in both `live_fetcher.py` and `data_maintenance.py`)
- `max_duration_s=300` — timeout after 5 minutes, proceeds to promotion with whatever was fetched
- Exclusive file lock (`data/.maintenance.lock`) prevents concurrent maintenance
- Audit log at `data/maintenance.jsonl`

### 1-minute data
1m data is perp-only, stored directly in `data/perp/1m_cache/{TOKEN}_1m.parquet` (no live/historical split). Used for live entry resolution (BB cross detection between hourly ticks), not for backtests. Backfill fetches only recent bars (gap_threshold_minutes=5).

## Key Design Decisions

### Only store 1h parquets for backtests
The engine computes 4h and daily from 1h on the fly using `pandas.resample()`.
Cost: ~5ms per token for both 4h + daily — negligible vs indicator computation.
Benefit: single source of truth, no stale cache sync issues, less disk usage.

### Multi-exchange merge with priority
OHLCV is merged across exchanges: Binance (primary) > Kraken > Hyperliquid.
The longest/best-quality source wins, gaps filled from others.
Symbol normalization handles Binance `1000PEPE` → `PEPE`, Hyperliquid `kPEPE` → `PEPE`.

### 1000-prefix tokens (Binance)
Binance lists some tokens as `1000TOKENUSDT` on futures (price = 1000x per-token price).
**Prices are stored at raw exchange level — NO division by 1000.** This is critical for:
- Order placement accuracy when live trading
- Position sizing correctness
- Consistency between backtest and live execution

Symbol resolution (`_resolve_symbol()`) maps `PEPE` → `1000PEPE/USDT:USDT` for API calls.
Filenames use the short form: `PEPE_1h.parquet`, `PEPE_1m.parquet`.

Current 1000-prefix tokens (as of 2026-03-30):
`PEPE, SHIB, FLOKI, BONK, LUNC, SATS, RATS, CAT, CHEEMS, WHY, X, XEC`

Defined in:
- `v4/live_fetcher.py:_1000_PREFIX_TOKENS`
- `v4/price_monitor.py:_1000_PREFIX_TOKENS`
- `tools/fetch_today_data.py:_1000_TOKENS`
- `tools/backfill_vision_1m.py:_1000_TOKENS`
- `tools/backfill_vision_1h.py:_1000_TOKENS`
- `tools/fetch_binance_futures_1m.py:_1000_TOKENS`
- `tools/build_parquet_cache.py:BINANCE_SYMBOL_MAP`

**Note:** NEIRO and APU are NOT 1000-prefix — they trade as plain symbols on Binance.

### Dynamic token discovery
Never hardcode token lists. `build_parquet_cache.py` discovers tokens from CSV files.
`v4/data_loader.py:discover_tokens_from_data()` discovers tokens from parquet files at runtime.
Adding a new token = just add its parquet file to the appropriate cache directory.

### Quality grading
15 automated checks per token. Grade A/B/C assignment.
Auto-fixes: dedup, OHLC clamping, gap interpolation (< 6h gaps only).

## Funding Data

### Status
- Funding is merged into 1h parquets as `funding_rate` column
- 8h settlement rates normalized to hourly (`_normalize_funding_to_hourly()` divides by interval)
- **8x overcharge bug fixed 2026-03-26:** Binance 8h rates were not divided by 8 before merge. All 195 perp parquets rebuilt.

### Intervals
| Exchange | Interval | Normalization |
|----------|----------|---------------|
| Binance | 8h (3x/day) | ÷ 8 to get hourly rate |
| Kraken | 4h (6x/day) | ÷ 4 to get hourly rate |
| Hyperliquid | 8h (3x/day) | ÷ 8 to get hourly rate |

## Fetch Scripts

### Batch (historical backfill)
| Script | Exchange | Market | Data Types |
|--------|----------|--------|-----------|
| `fetch_binance_perp.py` | Binance | Perp | OHLCV, funding, OI |
| `fetch_binance_spot.py` | Binance | Spot | OHLCV |
| `fetch_kraken_perp.py` | Kraken | Perp | OHLCV, funding |
| `fetch_hyperliquid_perp.py` | Hyperliquid | Perp | OHLCV, funding |
| `build_parquet_cache.py` | All | Both | Merge CSVs + QC → parquet |

### Live (runner data flows)
| Module | What it does |
|--------|-------------|
| `v4/live_fetcher.py` | `fetch_all_data()` — hourly 1h bar fetch for all tokens. `backfill_gaps()` — fill gaps > threshold. `append_to_parquet()` — atomic writes to live buffer. |
| `v4/data_maintenance.py` | `ensure_data_fresh()` — orchestrates backfill + promote + 1m fetch. CLI: `python -m v4.data_maintenance`. |
| `tools/promote_live.py` | `run_promotion()` — rolls live → historical with QC + manifests. |

All scripts use `ccxt` with `enableRateLimit: True`, retry with exponential backoff.

## Engine Data Flow

Primary market is **perp** for active strategies (s501).

```
data/perp/1h_cache/{TOKEN}_1h.parquet  +  data/perp/live/{TOKEN}.parquet
  → merged at read time by load_token_data()
  → Engine._build_context()
    → aggregate_to_timeframe(df_1h, hours=4)  → df_4h   (computed, not stored)
    → aggregate_to_timeframe(df_1h, hours=24) → df_daily (computed, not stored)
    → compute_indicators_fast() for each timeframe
    → compute_adv() from close * volume → ADV for position sizing
    → StrategyContext with all timeframes + indicators
```
