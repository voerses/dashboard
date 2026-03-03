"""
Strategy S28: Momentum Burst Perp (Both Directions)
=====================================================
Adaptation of S11 (best spot strategy, 36/116 validated) for perp markets.
S11 is long-only spot. S28 trades BOTH directions on perp:
- Strong up burst → LONG (same as S11)
- Strong down burst → SHORT (new — exploits leverage)

This tests whether S11's proven momentum burst signal also works short.

Causal chain: ret_1 > 0.03 burst → ADX expansion (2-4h) → trend continuation.
From Granger analysis: RSI and vol_ratio are leading indicators for ret_1.

Market: PERP (leveraged, both directions)
Status: EXPERIMENTAL
"""

import numpy as np
from engine import (StrategyContext, StrategyResult, MarketType,
                    CRISIS, DOWNTREND, UPTREND,
                    rolling_mean, rolling_std)


def strategy(ctx: StrategyContext) -> StrategyResult:
    """Bidirectional momentum burst on perp — long bursts up, short bursts down."""
    n = len(ctx.ind_1h['close'])
    close = ctx.ind_1h['close']
    ret_1 = ctx.ind_1h['ret_1']
    adx = ctx.ind_1h['adx']
    ema20 = ctx.ind_1h['ema_20']
    vol_ratio = ctx.ind_1h['vol_ratio']
    regime = ctx.regime_1h

    # ── LAYER 1: REGIME FILTER ──
    regime_ok = regime != 0  # exclude CRISIS

    # ── LAYER 2: ADX TREND CONFIRMATION ──
    adx_ok = adx > 25  # strong directional movement

    # ── LAYER 3: MOMENTUM BURST (both directions) ──
    burst_up = ret_1 > 0.03     # 3% up burst → LONG
    burst_down = ret_1 < -0.03  # 3% down burst → SHORT

    # ── LAYER 4: TREND ALIGNMENT ──
    # Long: price above EMA (uptrend context)
    # Short: price below EMA (downtrend context)
    trend_long = close > ema20
    trend_short = close < ema20

    # ── LAYER 5: VOLUME CONFIRMATION ──
    vol_ok = vol_ratio > 1.5  # elevated volume for both directions

    # Combined entry
    entry_long = regime_ok & adx_ok & burst_up & trend_long & vol_ok
    entry_short = regime_ok & adx_ok & burst_down & trend_short & vol_ok

    direction = np.where(entry_long, 1,
                np.where(entry_short, -1, 0)).astype(np.int8)

    entry = entry_long | entry_short
    entry[:200] = False

    return StrategyResult(
        entry_mask=entry,
        direction=direction,

        # S11-proven parameters adapted for perp
        stop_mult=3.0,       # 3x ATR (same as S11)
        trail_mult=3.0,      # 3x ATR trailing (same as S11)
        target_mult=999,     # Trail only
        no_stop_bars=24,     # 24h protection (same as S11)
        min_hold=18,         # Min 18h (same as S11)
        max_hold=720,        # Max 30 days
        edge=0.35,           # Slightly lower edge (short side unproven)

        exit_regimes={CRISIS},  # Only crisis exit (we trade both dirs)
        name='momentum_burst_perp',

        # PERP settings
        market_type=MarketType.PERP,
        leverage=2.0,
        exchange='binance',
    )
