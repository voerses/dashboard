"""
Strategy Template — Copy this file to create a new strategy.
==============================================================

PRE-DEVELOPMENT CHECKLIST (mandatory before writing code):
──────────────────────────────────────────────────────────
[ ] 1. DEDUPLICATION CHECK — verify this strategy doesn't already exist:
       - Read v3/STRATEGY_LIFECYCLE.md → current tier classifications
       - Read v2/strategies/README.md → existing strategy summaries
       - Check entry signal correlation with Tier A strategies:
           s11: ret_1 > 0.03 (momentum burst)
           s09: EMA stack + daily EMA50 (dual momentum)
           s13: volume-weighted TSMOM
           s21: rolling skew + momentum
           s17: ADX + momentum burst (2%)
           s18: momentum acceleration (ret change)
       - If your core entry signal overlaps >80% with an existing one, STOP.
         Consider adding a filter to the existing strategy instead.

[ ] 2. SIGNAL LAB IC CHECK — verify the signal has predictive power:
       - Run: python v2/signal_lab.py --signal <name> --post-etf
       - IC must be > +0.02 post-ETF (Jan 2024+) at target horizon
       - Signal must be stable across 2+ horizons

[ ] 3. KNOWLEDGE BASE CHECK:
       - Read v3/PERFORMANCE_PATTERNS.md (vectorization rules)
       - Read v3/SIGNAL_DEVELOPMENT.md (signal stack structure)
       - Read v2/knowledge/INDICATOR_CATALOG.md (available indicators)
       - Read v2/knowledge/STRATEGY_CATALOG.md (academic backing)

[ ] 4. PERFORMANCE CHECK after writing:
       - Profile: must be < 1ms per call on 40K bars
       - No Python for-loops over bar arrays
       - Use rolling_* helpers from engine

Steps:
1. Complete the checklist above
2. Copy this file: cp TEMPLATE.py sNN_my_strategy.py
3. Follow the signal stack: Regime → Trend → Entry → Volume
4. Performance check:
   python -c "
     import sys; sys.path.insert(0, 'v3'); sys.path.insert(0, 'v2')
     from engine import Engine
     import pandas as pd, time
     eng = Engine(data_dir='v2/real_data')
     df = pd.read_parquet('v2/real_data/1h_cache/BTC_1h.parquet')
     ctx = eng._build_context('BTC', df)
     from strategies.sNN_my_strategy import strategy
     t0 = time.perf_counter()
     for _ in range(1000): strategy(ctx)
     print(f'{(time.perf_counter()-t0)/1000*1000:.3f}ms/call')
   "
5. Quick validate: python v3/validation.py --strategy sNN --tokens BTC --workers 1
6. Full validate: python v3/validation.py --strategy sNN --workers 4

Status: EXPERIMENTAL
"""

import numpy as np
from engine import (StrategyContext, StrategyResult, CRISIS, DOWNTREND,
                    rolling_mean, rolling_std, rolling_max, rolling_min,
                    rolling_median, rolling_zscore, rolling_skew, rolling_corr)


def strategy(ctx: StrategyContext) -> StrategyResult:
    """
    Your strategy logic here.

    SIGNAL STACK (all 4 layers mandatory):
    ──────────────────────────────────────
    Layer 1: Regime Filter     — ctx.regime_1h != 0 (minimum)
    Layer 2: Trend Alignment   — close > ema20, adx > 25, etc.
    Layer 3: Entry Signal      — your core hypothesis (ONE signal)
    Layer 4: Volume Confirm    — vol_ratio > 1.0

    Available in ctx:
    ─────────────────
    ctx.ticker          # 'BTC', 'ETH', etc.
    ctx.tier            # 1, 2, or 3 (liquidity tier)

    ctx.ind_1h          # dict of 1H numpy arrays:
                        #   close, high, low, volume, ema_10, ema_20, ema_50,
                        #   macd, macd_signal, macd_hist, rsi, bb_upper, bb_lower,
                        #   bb_width, bb_pct, atr, adx, plus_di, minus_di,
                        #   vol_ratio, ret_1, vol_20, donch_high, donch_low, taker

    ctx.ind_4h          # same indicators on 4H bars
    ctx.ind_d           # same indicators on daily bars

    ctx.regime_1h       # int8 array: 0=crisis, 1=quiet, 2=uptrend, 3=range, 4=downtrend

    ctx.align_daily_to_1h(arr)  # forward-fill daily array to 1H index
    ctx.align_4h_to_1h(arr)     # forward-fill 4H array to 1H index

    ctx.custom          # dict of custom indicators (auto-computed):
                        #   obv, obv_slope, vwap_20, vwap_dev,
                        #   ret_6h, ret_12h, ret_24h, ret_48h, ret_120h,
                        #   ret_5d, ret_10d, ret_20d, ret_60d,
                        #   enr_vpin, enr_realized_vol, enr_taker_buy_ratio, ...

    ctx.enriched        # pandas DataFrame of daily enriched features (or None)
    ctx.df_1h           # raw 1H DataFrame (for custom aggregation)
    ctx.df_4h           # raw 4H DataFrame
    ctx.df_daily        # raw daily DataFrame

    Rolling helpers (vectorized, use INSTEAD of for-loops):
    ──────────────────────────────────────────────────────
    rolling_mean(arr, window)       rolling_max(arr, window)
    rolling_std(arr, window)        rolling_min(arr, window)
    rolling_median(arr, window)     rolling_zscore(arr, window)
    rolling_skew(arr, window)       rolling_corr(arr1, arr2, window)
    """
    n = len(ctx.ind_1h['close'])
    close = ctx.ind_1h['close']

    # ── LAYER 1: REGIME FILTER ──────────────────────────────────
    regime_ok = ctx.regime_1h != 0  # exclude crisis

    # ── LAYER 2: TREND ALIGNMENT ────────────────────────────────
    ema20 = ctx.ind_1h['ema_20']
    adx = ctx.ind_1h['adx']
    trend_ok = (close > ema20) & (adx > 25)

    # ── LAYER 3: ENTRY SIGNAL (your core hypothesis) ────────────
    # Replace this with your actual signal.
    # Keep it to ONE condition. Complexity kills.
    ret_1 = ctx.ind_1h['ret_1']
    core_signal = ret_1 > 0.03  # example: momentum burst

    # ── LAYER 4: VOLUME CONFIRMATION ────────────────────────────
    vol_ratio = ctx.ind_1h['vol_ratio']
    vol_ok = vol_ratio > 1.0

    # ── COMPOSE ─────────────────────────────────────────────────
    entry = regime_ok & trend_ok & core_signal & vol_ok
    entry[:200] = False  # warmup guard
    # ────────────────────────────────────────────────────────────

    return StrategyResult(
        entry_mask=entry,
        direction=np.ones(n, dtype=np.int8),  # +1=long, -1=short

        # ── TRADE MANAGEMENT (Tier A defaults) ───────────────────
        stop_mult=3.0,        # 3x ATR initial stop (proven optimal)
        trail_mult=3.0,       # 3x ATR trailing stop
        target_mult=999,      # Trail only, no fixed target
        no_stop_bars=24,      # 24h protection (biggest single lever)
        min_hold=18,          # Minimum 18 hours
        max_hold=720,         # Maximum 30 days
        edge=0.40,            # Kelly edge estimate
        # ──────────────────────────────────────────────────────────

        exit_regimes={CRISIS, DOWNTREND},  # Force exit on regime shift
        name='my_strategy',
    )
