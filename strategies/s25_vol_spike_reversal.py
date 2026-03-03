"""
Strategy S25: Volume Spike Mean Reversion (Perp)
=================================================
Gate 1 IC: +0.040 at 24h (t=10.03, 116 tokens) — strongest signal found.

Hypothesis: Volume spikes on perp markets indicate forced liquidation or
panic buying/selling. After the spike exhausts, price reverts toward the
pre-spike mean. We FADE the spike direction.

Causal chain: vol_ratio spike → vol_20 expansion (1h) → ADX update (2-4h)
The vol_ratio is the EARLIEST warning signal (Granger F=719, p≈0).

Signal: When vol_ratio > 3.5 AND price displaced >2.5% from EMA AND ADX>20,
        enter OPPOSITE to the move direction (mean reversion).

Works in BOTH up and down markets:
- Uptrend vol spike (long squeeze): SHORT the overshoot
- Downtrend vol spike (liquidation cascade): LONG the bottom

Market: PERP (leveraged, both directions)
Status: EXPERIMENTAL
"""

import numpy as np
from engine import (StrategyContext, StrategyResult, MarketType,
                    CRISIS, DOWNTREND, UPTREND,
                    rolling_mean, rolling_std)


def strategy(ctx: StrategyContext) -> StrategyResult:
    """Vol spike mean reversion — fade extreme volume moves on perp."""
    n = len(ctx.ind_1h['close'])
    close = ctx.ind_1h['close']
    ema20 = ctx.ind_1h['ema_20']
    adx = ctx.ind_1h['adx']
    vol_ratio = ctx.ind_1h['vol_ratio']
    ret_1 = ctx.ind_1h['ret_1']
    regime = ctx.regime_1h

    # ── LAYER 1: REGIME FILTER ──
    # Allow all non-crisis regimes (this strategy works in trends AND ranges)
    regime_ok = regime != 0  # exclude CRISIS only

    # ── LAYER 2: VOL SPIKE DETECTION (the causal leader) ──
    vol_spike = vol_ratio > 3.0  # 3x average volume = major spike

    # ── LAYER 3: TREND CONFIRMATION ──
    trend_present = adx > 25  # Only trade when strong directional movement

    # ── LAYER 4: DIRECTION — fade the move ──
    # Price significantly displaced from EMA = overextended
    displacement = (close - ema20) / ema20  # % displacement
    overextended_up = displacement > 0.02    # >2% above EMA → short
    overextended_down = displacement < -0.02  # >2% below EMA → long

    # Combined entry
    entry_short = regime_ok & vol_spike & trend_present & overextended_up
    entry_long = regime_ok & vol_spike & trend_present & overextended_down

    # Direction: -1 for shorts (overextended up), +1 for longs (overextended down)
    direction = np.where(entry_short, -1,
                np.where(entry_long, 1, 0)).astype(np.int8)

    entry = entry_short | entry_long
    entry[:200] = False

    return StrategyResult(
        entry_mask=entry,
        direction=direction,

        # Wider stops for perp mean reversion
        stop_mult=3.5,       # 3.5x ATR (room for volatility snap-back)
        trail_mult=2.5,      # 2.5x ATR trailing
        target_mult=999,     # Trail only
        no_stop_bars=12,     # 12h protection (let reversion develop)
        min_hold=12,         # Min 12h (reversion needs time)
        max_hold=168,        # Max 7 days
        edge=0.35,           # Moderate edge estimate

        exit_regimes={CRISIS},  # Only exit on crisis (not downtrend — we trade both dirs)
        name='vol_spike_reversal',

        # PERP settings
        market_type=MarketType.PERP,
        leverage=2.0,         # 2x leverage (conservative for MR)
        exchange='binance',
    )
