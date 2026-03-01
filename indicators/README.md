# Indicators

Custom indicator modules for strategy development.

## Built-in Indicators (computed by engine)

The engine already computes 23+ indicators on 1H, 4H, and Daily timeframes.
These are available in `ctx.ind_1h`, `ctx.ind_4h`, `ctx.ind_d`:

```
close, high, low, volume, open,
ret_1, atr, atr_pct,
sma_20, sma_50, sma_200, ema_10, ema_20, ema_50,
rsi, bb_upper, bb_lower, bb_width, bb_pct,
adx, plus_di, minus_di,
macd_line, macd_signal, macd_hist,
vol_ratio, donch_high, donch_low, taker
```

## Custom Indicators (computed by engine plugins)

Available in `ctx.custom`:
```
obv, obv_slope, vwap_20, vwap_dev,
ret_6h, ret_12h, ret_24h, ret_48h, ret_120h,
ret_5d, ret_10d, ret_20d, ret_60d
```

## Vectorized Rolling Helpers

Available via `from engine import rolling_*`:
```python
rolling_mean(arr, window)       rolling_max(arr, window)
rolling_std(arr, window)        rolling_min(arr, window)
rolling_median(arr, window)     rolling_zscore(arr, window)
rolling_skew(arr, window)       rolling_corr(arr1, arr2, window)
```

## Adding New Indicators

New indicators can be added in two ways:

1. **Engine plugin** — Add to `compute_indicators_fast()` in the engine.
   Best for indicators used by multiple strategies.

2. **Standalone module** — Create a file here in `indicators/`.
   Best for experimental indicators being tested by a single strategy.

### Standalone Indicator Template

```python
"""
Indicator: My Custom Indicator
IC: +0.0XX post-ETF (from Signal Lab)
Used by: sNN_strategy_name
"""
import numpy as np
from engine import rolling_mean, rolling_std

def compute(close, high, low, volume, **kwargs):
    """Compute indicator values. Returns numpy array."""
    # MUST be vectorized — no Python for-loops over bar arrays
    # Use rolling_* helpers from engine
    result = rolling_mean(close, 20)
    return result
```

## Knowledge Base

See `knowledge/INDICATOR_CATALOG.md` for the exhaustive indicator catalog with:
- Academic citations
- Causal mechanisms
- IC values
- Crypto suitability assessments

See `knowledge/INDICATOR_ANALYSIS.md` for Signal Lab IC results.
