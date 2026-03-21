"""
Strategy S202: Volatility Contraction Breakout (Bidirectional, Perp)
====================================================================
Enters when volatility contracts to historically low levels, then breaks out.
This exploits the mean-reverting nature of volatility — low vol precedes big moves.

Signal: BB squeeze (narrowest in 50 bars) + directional breakout
Long: Squeeze + breakout above upper BB + volume surge
Short: Squeeze + breakdown below lower BB + volume surge
"""

import numpy as np
from engine import StrategyContext, StrategyResult, CRISIS


def strategy(ctx: StrategyContext) -> StrategyResult:
    """Volatility squeeze breakout."""
    close = ctx.ind_1h['close']
    n = len(close)
    bb_width = ctx.ind_1h['bb_width']
    bb_pct = ctx.ind_1h['bb_pct']
    vol_ratio = ctx.ind_1h['vol_ratio']
    regime = ctx.regime_1h
    atr = ctx.ind_1h['atr']
    adx = ctx.ind_1h['adx']
    rsi = ctx.ind_1h['rsi']

    # Detect BB squeeze: width is in bottom 10th percentile of last 100 bars
    bb_rank = np.zeros(n)
    for i in range(100, n):
        window = bb_width[i - 100:i + 1]
        bb_rank[i] = (window < bb_width[i]).sum() / len(window)

    squeeze = bb_rank < 0.10  # Bottom 10% = squeeze

    # Previous bar was squeeze, current bar breaks out
    squeeze_prev = np.roll(squeeze, 1)
    squeeze_prev[0] = False

    # Breakout direction
    breakout_up = (squeeze_prev
                   & (bb_pct > 0.85)
                   & (vol_ratio > 1.5)
                   & (rsi > 50)
                   & (regime != CRISIS))

    breakout_down = (squeeze_prev
                     & (bb_pct < 0.15)
                     & (vol_ratio > 1.5)
                     & (rsi < 50)
                     & (regime != CRISIS))

    entry_mask = breakout_up | breakout_down
    direction = np.where(breakout_up, 1, np.where(breakout_down, -1, 0)).astype(np.int8)
    entry_mask[:200] = False

    if ctx.liquidity_mask is not None:
        entry_mask = entry_mask & ctx.liquidity_mask

    return StrategyResult(
        entry_mask=entry_mask,
        direction=direction,
        stop_mult=2.5,
        trail_mult=2.0,
        target_mult=999,
        no_stop_bars=12,
        min_hold=6,
        max_hold=336,  # 2 weeks
        edge=0.40,
        exit_regimes={CRISIS},
        max_trade_pct=0.12,
        breakeven_atr=0.5,
        exchange='binance',
        name='s202_volatility_breakout',
    )
