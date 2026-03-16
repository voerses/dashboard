"""
s56 Max Leverage Momentum — 5x perp with maximum conviction filters

Highest leverage strategy in the portfolio. Uses 5x leverage on perp
with the tightest possible entry filters to ensure only the most
high-conviction setups get through.

Entry (ALL must be true):
- Token in strong uptrend: close > EMA20 > EMA50, ADX > 30 (stricter)
- Multi-horizon momentum: ret_12h > 3%, ret_24h > 5%, ret_48h > 8%
- Breakout: close > Bollinger upper band
- Volume explosion: vol_ratio > 2.5
- RSI 60-80: strong momentum zone
- Not crisis or downtrend
- EMA10 > EMA20 (short-term trend acceleration)

5x leverage means even 2-3% moves generate 10-15% returns.
With max_hold=360h (15 days) and progressive trail, captures
multi-day momentum surges.

Edge = 0.65 (highest conviction, tightest filter)

Status: EXPERIMENTAL
"""

import numpy as np
from engine import (StrategyContext, StrategyResult, MarketType,
                    CRISIS, DOWNTREND)


TRAIL_SCHEDULE = None  # exit ablation: flat 1.5 ATR trail


def strategy(ctx: StrategyContext) -> StrategyResult:
    """5x leveraged max conviction momentum on perp."""
    n = len(ctx.ind_1h['close'])
    close = ctx.ind_1h['close']
    ema_10 = ctx.ind_1h['ema_10']
    ema_20 = ctx.ind_1h['ema_20']
    ema_50 = ctx.ind_1h['ema_50']
    adx = ctx.ind_1h['adx']
    rsi = ctx.ind_1h['rsi']
    vol_ratio = ctx.ind_1h['vol_ratio']
    bb_upper = ctx.ind_1h['bb_upper']
    regime = ctx.regime_1h

    # ── LAYER 1: VERY STRONG UPTREND ────────────────────────────
    # EMA10 > EMA20 > EMA50 = short-term accelerating into medium-term trend
    strong_trend = (ema_10 > ema_20) & (ema_20 > ema_50) & (close > ema_10) & (adx > 30)

    # ── LAYER 2: EXTREME MOMENTUM CASCADE ───────────────────────
    ret_12h = ctx.custom.get('ret_12h')
    ret_24h = ctx.custom.get('ret_24h')
    ret_48h = ctx.custom.get('ret_48h')

    if ret_12h is None:
        ret_12h = np.zeros(n)
        ret_12h[12:] = (close[12:] - close[:-12]) / np.maximum(close[:-12], 1e-10)
    if ret_24h is None:
        ret_24h = np.zeros(n)
        ret_24h[24:] = (close[24:] - close[:-24]) / np.maximum(close[:-24], 1e-10)
    if ret_48h is None:
        ret_48h = np.zeros(n)
        ret_48h[48:] = (close[48:] - close[:-48]) / np.maximum(close[:-48], 1e-10)

    # Extreme momentum: stricter than s53
    extreme_mom = (ret_12h > 0.03) & (ret_24h > 0.05) & (ret_48h > 0.08)

    # ── LAYER 3: BOLLINGER BREAKOUT ─────────────────────────────
    breakout = close > bb_upper

    # ── LAYER 4: EXTREME VOLUME ────────────────────────────────
    vol_extreme = vol_ratio > 2.5

    # ── LAYER 5: RSI MOMENTUM SWEET SPOT ───────────────────────
    rsi_sweet = (rsi > 60) & (rsi < 80)

    # ── LAYER 6: NOT CRISIS/DOWNTREND ──────────────────────────
    regime_ok = (regime != CRISIS) & (regime != DOWNTREND)

    # ── COMPOSE ─────────────────────────────────────────────────
    entry = strong_trend & extreme_mom & breakout & vol_extreme & rsi_sweet & regime_ok
    entry[:200] = False

    if ctx.liquidity_mask is not None:
        entry = entry & ctx.liquidity_mask

    direction = np.ones(n, dtype=np.int8)

    # Max sizing in uptrend only
    regime_size = np.where(regime == 2, 3.0,       # UPTREND
                 np.where(regime == 3, 1.5, 0.5))  # RANGE / other

    return StrategyResult(
        entry_mask=entry,
        direction=direction,
        market_type=MarketType.PERP,
        leverage=5.0,
        stop_mult=2.5,       # Tighter stop at 5x leverage
        trail_mult=1.5,       # exit ablation: flat 1.5 ATR trail
        target_mult=999,
        no_stop_bars=12,
        min_hold=8,
        max_hold=360,
        edge=0.65,
        exit_regimes={CRISIS, DOWNTREND},
        exchange='binance',
        name='s56_max_leverage_momentum',
        trail_schedule=TRAIL_SCHEDULE,
        size_multiplier=regime_size,
    )
