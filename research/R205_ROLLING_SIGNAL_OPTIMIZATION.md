# R205: Per-Token Rolling Signal Optimization on Sub-Daily Data

## Core Insight
The edge isn't in one signal applied uniformly. It's in finding the RIGHT
rolling computation per token from high-frequency data. Different tokens
have different microstructures that create different tradeable patterns.

## Data Available
- 5-min L/S positioning: 227 tokens (data/alternative/binance_metrics/5min/)
  - 6 signal fields: toptrader_ls (count+sum), global_ls, taker_ratio, OI, OI_value
- 1-min OHLCV: 197 tokens (data/perp/1m_cache/) — 4GB
- 1H OHLCV + funding: 236 tokens (data/perp/1h_cache/)

## Approach: Exhaustive Per-Token Signal Search

For each token:
1. Load 5-min L/S data + 1-min/1H price data
2. Align timestamps (5-min L/S available 5 min after timestamp)
3. Compute rolling signals at multiple windows:
   - Windows: 1h, 2h, 4h, 8h, 12h, 24h, 48h, 72h, 168h (7d)
   - Fields: toptrader_ls, global_ls, divergence, taker_ratio, OI
   - Computations: z-score, percentile rank, ROC, level
   - That's 9 windows × 5 fields × 4 computations = 180 candidate signals per token
4. For each candidate: compute IC with forward 1d, 3d, 7d returns
5. Walk-forward validate top candidates (4 temporal quarters)
6. Output: per-token optimal signal specification

## Key Design Principles
- ALL signals lagged properly (5-min data at T available at T+5min)
- Entry at next hourly bar open (not intra-bar)
- Walk-forward validation mandatory (3+/4 quarters)
- Transaction costs applied (7bps + slippage + funding)

## Expected Output
Per token:
```json
{
  "token": "BTC",
  "best_signal": {
    "field": "toptrader_ls_count",
    "window": "4h",
    "computation": "zscore",
    "threshold": 2.1,
    "direction": "contrarian",
    "ic_7d": -0.12,
    "wf_quarters": "4/4"
  }
}
```

## Compute Strategy
- This is ~180 signals × 227 tokens = 40,860 IC computations
- Each IC computation is fast (Spearman on ~10K rows)
- Total: ~5 min per token on 5-min data, ~20 hours for all 227
- Parallelize: run 10 tokens at a time
- Or: start with top 30 tokens by OI (most reliable data)

## Phase 1: BTC + top 10 tokens as proof of concept
## Phase 2: Expand to all 227
## Phase 3: Group tokens by optimal signal profile
## Phase 4: Build cluster strategies from groups
