"""
Strategy Template — Copy this file to create a new strategy.
==============================================================

1. Copy this file: cp TEMPLATE.py s09_my_strategy.py
2. Edit the strategy() function below
3. Test: python -c "
     from engine import Engine, CPCV_ROBUST_TOKENS
     from strategies.s09_my_strategy import strategy
     engine = Engine()
     engine.run(strategy, tokens=CPCV_ROBUST_TOKENS)
   "

Status: EXPERIMENTAL
"""

import numpy as np
from engine import StrategyContext, StrategyResult, CRISIS, DOWNTREND, RANGE, QUIET


def strategy(ctx: StrategyContext) -> StrategyResult:
    """
    Your strategy logic here.

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
    """
    n = len(ctx.ind_1h['close'])

    # ── YOUR ENTRY LOGIC ──────────────────────────────────────
    # Example: RSI oversold on 1H + 4H + uptrend regime
    rsi_1h = ctx.ind_1h['rsi']
    rsi_4h = ctx.align_4h_to_1h(ctx.ind_4h['rsi'])

    entry = (rsi_1h < 30) & (np.nan_to_num(rsi_4h, 50) < 40)
    entry[:200] = False  # skip warmup period
    # ──────────────────────────────────────────────────────────

    return StrategyResult(
        entry_mask=entry,
        direction=np.ones(n, dtype=np.int8),  # +1=long, -1=short

        # ── TRADE MANAGEMENT ──────────────────────────────────
        stop_mult=3.0,        # Initial stop: 3x ATR below entry
        trail_mult=3.0,       # Trailing stop: 3x ATR below highest
        target_mult=999,      # Profit target: 999=disabled (trail only)
        no_stop_bars=12,      # No stop-loss for first 12 bars (protection)
        min_hold=6,           # Minimum hold: 6 hours
        max_hold=720,         # Maximum hold: 30 days
        edge=0.35,            # Kelly edge estimate (affects position size)
        # ──────────────────────────────────────────────────────

        exit_regimes={CRISIS, DOWNTREND},  # Force exit in these regimes
        rsi_exit_level=999,   # Exit when RSI > this (999=disabled)
        convex_exit=False,    # True for mean-reversion style exits
        mean_target_vals=None, # Target price array for convex exits

        name='my_strategy',   # Name for logging
    )
