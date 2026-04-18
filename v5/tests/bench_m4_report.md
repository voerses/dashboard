# M4 bench report — AC23 / AC35 / AC36

- generated: 2026-04-18 18:52:42 UTC
- RSS probe unavailable (psutil not installed)

## AC23 — runtime

| bench | bars | tokens | runtime (s) | target |
|-------|-----:|-------:|------------:|--------|
| hourly (1h base, 1h/1h/1h subs) | 100 | 5 | 0.0014 | <= 3x pre-M4 (relative, user-measured) |
| 1m MTF (1m base, 1h/1h/1m subs) -- projected 1mo: 0.1s | 1440 | 3 | 0.0026 | 1mo 1m MTF <= 600s (10 min) |

## AC35 — memory budget

- projected memory (over-limit config, 3 strats x 1m x 500 tokens): **12804.6 MB** (ceiling 1200 MB)
- assertion trips on over-limit config: **YES**
- projected memory (under-limit config, 3 strats x 1h x 50 tokens): **21.3 MB**
- under-limit config passes: **YES**

## AC36 — demand-driven materialization

- chunked mode activates for 1m x 500 tokens x 365d: **YES**
- eager mode for 1h x 2 tokens x 30d: **YES**
