"""
Strategy S26: RSI Extreme Mean Reversion (Perp)
================================================
Gate 1 IC: +0.032 at 6h (t=6.46, 116 tokens) — strong signal.

Hypothesis: Extreme RSI readings on perp markets indicate exhaustion.
RSI < 25 = panic selling exhaustion → LONG the bounce.
RSI > 75 = euphoria exhaustion → SHORT the fade.

Causal chain: RSI is #1 Granger leader (net +17 score across BTC/ETH/SOL).
RSI leads ema20_slope by 2-5h, which leads ret_1.

Signal: When RSI hits extreme (<25 or >75) AND ADX confirms directional
        move AND volume spike confirms participation → FADE the extreme.

Works in BOTH up and down markets (perp, both directions).

Market: PERP (leveraged, both directions)
Status: EXPERIMENTAL
"""

import numpy as np
from engine import (StrategyContext, StrategyResult, MarketType,
                    CRISIS, DOWNTREND, UPTREND,
                    rolling_mean, rolling_std)


def strategy(ctx: StrategyContext) -> StrategyResult:
    """RSI extreme mean reversion — fade exhaustion on perp."""
    n = len(ctx.ind_1h['close'])
    close = ctx.ind_1h['close']
    rsi = ctx.ind_1h['rsi']
    adx = ctx.ind_1h['adx']
    vol_ratio = ctx.ind_1h['vol_ratio']
    regime = ctx.regime_1h

    # ── LAYER 1: REGIME FILTER ──
    regime_ok = regime != 0  # exclude CRISIS only

    # ── LAYER 2: RSI EXTREME DETECTION (the causal leader) ──
    rsi_oversold = rsi < 25      # extreme oversold → long
    rsi_overbought = rsi > 75    # extreme overbought → short

    # ── LAYER 3: TREND CONFIRMATION ──
    trend_present = adx > 20  # directional move in progress

    # ── LAYER 4: VOLUME CONFIRMATION ──
    vol_elevated = vol_ratio > 1.5  # above-average volume = real move

    # Combined entry
    entry_long = regime_ok & rsi_oversold & trend_present & vol_elevated
    entry_short = regime_ok & rsi_overbought & trend_present & vol_elevated

    # Direction: +1 for longs (oversold), -1 for shorts (overbought)
    direction = np.where(entry_long, 1,
                np.where(entry_short, -1, 0)).astype(np.int8)

    entry = entry_long | entry_short
    entry[:200] = False

    return StrategyResult(
        entry_mask=entry,
        direction=direction,

        # Mean reversion stops — tighter than trend following
        stop_mult=3.0,       # 3x ATR stop
        trail_mult=2.5,      # 2.5x ATR trailing
        target_mult=999,     # Trail only
        no_stop_bars=8,      # 8h protection
        min_hold=6,          # Min 6h (RSI reverts fast)
        max_hold=120,        # Max 5 days
        edge=0.30,           # Conservative edge estimate

        exit_regimes={CRISIS},  # Only exit on crisis
        name='rsi_extreme_reversal',

        # PERP settings
        market_type=MarketType.PERP,
        leverage=2.0,         # 2x leverage
        exchange='binance',
        breakeven_atr=0.5,
    )
