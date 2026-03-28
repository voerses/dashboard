"""
Strategy Template — Copy this file to create a new strategy.
==============================================================

PRE-DEVELOPMENT CHECKLIST (mandatory before writing code):
──────────────────────────────────────────────────────────
[ ] 1. DEDUPLICATION CHECK — verify this strategy doesn't already exist:
       - Read knowledge/STRATEGY_LIFECYCLE.md → current tier classifications
       - Read strategies/README.md → existing strategy summaries
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
       - Run: python tools/signal_lab.py --signal <name> --post-etf
       - IC must be > +0.02 post-ETF (Jan 2024+) at target horizon
       - Signal must be stable across 2+ horizons

[ ] 3. KNOWLEDGE BASE CHECK:
       - Read knowledge/PERFORMANCE_PATTERNS.md (vectorization rules)
       - Read knowledge/SIGNAL_DEVELOPMENT.md (signal stack structure)
       - Read knowledge/INDICATOR_CATALOG.md (available indicators)
       - Read knowledge/STRATEGY_CATALOG.md (academic backing)

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
     import sys; sys.path.insert(0, 'v4')
     from engine import Engine
     import pandas as pd, time
     eng = Engine(data_dir='data', market='spot')
     df = pd.read_parquet('data/spot/1h_cache/BTC_1h.parquet')
     ctx = eng._build_context('BTC', df)
     from strategies.sNN_my_strategy import strategy
     t0 = time.perf_counter()
     for _ in range(1000): strategy(ctx)
     print(f'{(time.perf_counter()-t0)/1000*1000:.3f}ms/call')
   "
5. Quick validate: python v4/validation.py --strategy sNN --tokens BTC --workers 1
6. Portfolio backtest: python v4/portfolio_backtest.py --strategy sNN --months 12 --capital 200000
7. Full validate: python v4/validation.py --strategy sNN --workers 4

SIDEWAYS MARKET STRATEGIES (complementing s58):
- Must use perp or combined market (bidirectional for short capability)
- Must be profitable in RANGE/QUIET regimes (not just UPTREND)
- Must show positive March 2026 PnL (sideways stress test)
- See V4 candidates: s27, s28, s25, s29 in STRATEGY_CATALOG.md

Status: EXPERIMENTAL
"""

import numpy as np
from engine import (StrategyContext, StrategyResult, MarketType,
                    CRISIS, DOWNTREND,
                    rolling_mean, rolling_std, rolling_max, rolling_min,
                    rolling_median, rolling_zscore, rolling_skew, rolling_corr)


# ── Sizing overrides (read by portfolio_backtest.py) ─────────────
# Uncomment and fill in if your strategy needs non-default Kelly params.
# Run `python tools/verify_sizing.py sNN` after setting these.
# SIZING_OVERRIDES = {
#     "kelly_mult_override": 0.50,
#     "target_vol": 0.02,
#     "cap_pct_override": 0.15,
# }

# ── Research target sizes (verification target, not used by engine) ──
# What your research/notebook intended. verify_sizing.py checks these.
# RESEARCH_TARGET_SIZES = {
#     "per_position_pct": 0.20,        # target fraction of equity per position
#     "max_concurrent": 5,              # max simultaneous positions
#     "max_gross_exposure": 1.0,        # max total exposure as fraction of equity
#     "hold_duration_hours": 168,       # avg or expected hold time
# }

# ── Engine feature overrides ────────────────────────────────────────
# DD scaling disabled by default. Enable with strategy-specific thresholds
# only after baseline validation confirms the strategy's natural drawdown profile.
# Example (aggressive):
#   DD_SCALING = [(0.10, 0.75), (0.20, 0.50), (0.30, 0.25), (0.40, 0.0)]
DD_SCALING = []


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
    ctx.rolling_adv     # per-bar ADV array (point-in-time, no look-ahead)
    ctx.liquidity_mask  # bool array: True = liquid enough to trade at this bar

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

    # Futures-only (None for spot strategies):
    ctx.funding_1h      # per-hour funding rate array (for perp signal use)
    ctx.funding_raw     # raw settlement-interval rate
    ctx.market_type     # 'spot' or 'perp' — for strategy introspection

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

        # ── FUTURES FIELDS (defaults = pure spot, no changes needed) ──
        # market_type=MarketType.SPOT,  # 0=spot (default), 1=perp, 2=combined
        # leverage=1.0,                 # notional multiplier (no hard cap)
        # exchange='binance',           # for fee/funding lookup,
        breakeven_atr=0.5,
    )


# =============================================================================
# PERP STRATEGY EXAMPLE — BIDIRECTIONAL (for V4 sideways/choppy markets)
# =============================================================================
#
# def strategy(ctx: StrategyContext) -> StrategyResult:
#     """Bidirectional perp strategy — long in uptrends, short in downtrends.
#
#     V4 NOTE: Bidirectional strategies are critical for sideways markets.
#     ALL spot-only strategies lost money Jan-Mar 2026. Only perp/combined
#     strategies with short capability survived.
#
#     For V4 portfolio use: set market='perp' in StrategySpec.
#     """
#     n = len(ctx.ind_1h['close'])
#     close = ctx.ind_1h['close']
#     ema20 = ctx.ind_1h['ema_20']
#     adx = ctx.ind_1h['adx']
#     ret_1 = ctx.ind_1h['ret_1']
#     vol_ratio = ctx.ind_1h['vol_ratio']
#
#     # Use funding rate as a signal — negative funding = shorts pay longs
#     funding = ctx.funding_1h  # per-hour funding rate (None if spot)
#     if funding is None:
#         funding = np.zeros(n)
#
#     regime_ok = ctx.regime_1h != 0  # exclude crisis
#
#     # LONG entries: momentum burst in uptrends
#     long_entry = regime_ok & (close > ema20) & (adx > 25) & (ret_1 > 0.03) & (vol_ratio > 1.0)
#
#     # SHORT entries: downtrend momentum (key for sideways survival)
#     short_entry = regime_ok & (close < ema20) & (adx > 20) & (ret_1 < -0.03) & (vol_ratio > 0.8)
#
#     entry = long_entry | short_entry
#     entry[:200] = False
#
#     direction = np.where(long_entry, 1, np.where(short_entry, -1, 0)).astype(np.int8)
#
#     return StrategyResult(
#         entry_mask=entry,
#         direction=direction,
#         market_type=MarketType.PERP,
#         leverage=1.0,            # 1x leverage — aggressive sizing beats leverage
#         exchange='binance',
#         name='perp_bidirectional',
#         stop_mult=3.0, trail_mult=3.0, target_mult=999,
#         no_stop_bars=24, min_hold=18, max_hold=720, edge=0.40,
#         exit_regimes={CRISIS},   # Don't exit on DOWNTREND — shorts need it
#         size_multiplier=3.0,     # Aggressive sizing at 1x leverage
#         cap_multiplier=15.0,     # Relax ADV cap for more capital deployment,
    breakeven_atr=0.5,
#     )
#
#
# =============================================================================
# COMBINED (HEDGING) STRATEGY EXAMPLE
# =============================================================================
#
# def strategy(ctx_spot: StrategyContext, ctx_perp: StrategyContext) -> StrategyResult:
#     """Combined strategy: spot long + perp short hedge. Shared equity pool."""
#     n = len(ctx_spot.ind_1h['close'])
#
#     # Spot leg: long momentum
#     spot_entry = (ctx_spot.ind_1h['ret_1'] > 0.03) & (ctx_spot.regime_1h != 0)
#     spot_entry[:200] = False
#
#     # Perp leg: short hedge in downtrends
#     perp_entry = (ctx_perp.ind_1h['rsi'] > 60) & (ctx_perp.regime_1h == 4)
#     perp_entry[:200] = False
#
#     return StrategyResult(
#         entry_mask=spot_entry,
#         direction=np.ones(n, dtype=np.int8),   # spot: long
#         market_type=MarketType.COMBINED,
#         # Secondary leg
#         secondary_entry_mask=perp_entry,
#         secondary_direction=-np.ones(n, dtype=np.int8),  # perp: short
#         secondary_market_type=MarketType.PERP,
#         secondary_leverage=2.0,
#         capital_split=0.6,  # 60% spot, 40% perp
#         exchange='hyperliquid',
#         name='hedge_momentum',
#         stop_mult=3.0, trail_mult=3.0, target_mult=999,
#         no_stop_bars=24, min_hold=18, max_hold=720, edge=0.40,
#         exit_regimes={CRISIS, DOWNTREND},
    breakeven_atr=0.5,
#     )
