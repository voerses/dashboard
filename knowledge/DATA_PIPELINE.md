# Data Pipeline — Architecture & Learnings

## Directory Structure

```
data/
├── perp/                          # Perpetual futures
│   ├── binance/
│   │   ├── 1h_ohlcv/             # Raw 1h OHLCV CSVs (165 tokens)
│   │   ├── funding/              # 8h funding rates (165 tokens)
│   │   └── oi/                   # Open interest (empty)
│   ├── kraken/
│   │   ├── 1h_ohlcv/             # Raw 1h OHLCV CSVs (314 tokens)
│   │   └── funding/              # 4h funding rates (476 tokens)
│   ├── hyperliquid/
│   │   ├── ohlcv/                # Raw 1h OHLCV CSVs (52 tokens)
│   │   └── funding/              # 8h funding rates (26 tokens)
│   └── 1h_cache/                 # Merged, quality-checked parquets (360 tokens)
│
├── spot/                          # Spot markets
│   ├── binance/
│   │   ├── 1h_ohlcv/             # Raw 1h OHLCV CSVs (117 tokens)
│   │   └── daily_ohlcv/          # Legacy daily CSVs (57 tokens, Binance Vision)
│   └── 1h_cache/                 # Merged, quality-checked parquets (116 tokens)
│
└── quality_reports/               # Build quality reports (JSON)
```

## Key Design Decisions

### Only store 1h parquets
The engine computes 4h and daily from 1h on the fly using `pandas.resample()`.
Cost: ~5ms per token for both 4h + daily — negligible vs indicator computation.
Benefit: single source of truth, no stale cache sync issues, less disk usage.

### Multi-exchange merge with priority
OHLCV is merged across exchanges: Binance (primary) > Kraken > Hyperliquid.
The longest/best-quality source wins, gaps filled from others.
Symbol normalization handles Binance `1000PEPE` → `PEPE`, Hyperliquid `kPEPE` → `PEPE`.

### Dynamic token discovery
Never hardcode token lists. `build_parquet_cache.py` discovers tokens from CSV files.
`get_all_tradeable()` in `universe.py` discovers tokens from parquet files.

### Quality grading
15 automated checks per token. Grade A/B/C assignment.
Auto-fixes: dedup, OHLC clamping, gap interpolation (< 6h gaps only).

## Funding Data

### Status
- Downloaded for all 3 exchanges but NOT merged into 1h parquets
- Only 1 of 360 cached tokens missing funding (LUNC)
- `build_parquet_cache.py` has `merge_funding()` but output isn't in the cache

### Intervals
| Exchange | Interval | Rows/token (BTC) |
|----------|----------|------------------|
| Binance | 8h (3x/day) | ~6,760 |
| Kraken | 4h (6x/day) | ~39,000 |
| Hyperliquid | 8h (3x/day) | ~24,000 |

### Integration TODO
Funding needs to be:
1. Merged across exchanges (longest history wins)
2. Forward-filled to 1h alignment
3. Included in 1h parquets as `funding_rate` column
4. Used in engine simulation as a holding cost/income

Impact: a token at -0.01% funding every 8h costs ~1.1%/day to hold long.
This is a material cost currently missing from backtests.

## Fetch Scripts

| Script | Exchange | Market | Data Types |
|--------|----------|--------|-----------|
| `fetch_binance_perp.py` | Binance | Perp | OHLCV, funding, OI |
| `fetch_binance_spot.py` | Binance | Spot | OHLCV |
| `fetch_kraken_perp.py` | Kraken | Perp | OHLCV, funding |
| `fetch_hyperliquid_perp.py` | Hyperliquid | Perp | OHLCV, funding |
| `build_parquet_cache.py` | All | Both | Merge + QC → parquet |

All scripts use `ccxt` with rate limiting, retry with exponential backoff,
and parallel workers (default 4).

## Engine Data Flow

Default market is **spot** — strategies trade spot markets, not perps.
Perp data is kept for future perp-specific strategies (funding arb, basis trade, etc.)

```
data/spot/1h_cache/{TOKEN}_1h.parquet     ← default (spot trading)
data/perp/1h_cache/{TOKEN}_1h.parquet     ← use Engine(market='perp') when needed

  → Engine._build_context()
    → aggregate_to_timeframe(df_1h, hours=4)  → df_4h   (computed, not stored)
    → aggregate_to_timeframe(df_1h, hours=24) → df_daily (computed, not stored)
    → compute_indicators_fast() for each timeframe
    → compute_adv() from close * volume → ADV for position sizing
    → StrategyContext with all timeframes + indicators
```
