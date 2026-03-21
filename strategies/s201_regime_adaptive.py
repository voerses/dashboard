"""
Strategy S201: Regime-Adaptive Multi-Signal (Bidirectional, Perp)
================================================================
Uses different signal combinations depending on the market regime.
In uptrend: buy dips (RSI pullback to EMA)
In downtrend: sell rallies (RSI bounce rejection)
In range: mean revert from Bollinger extremes

Key: adaptive behavior per regime should generate more trades while
maintaining quality, since each regime has its own optimized signal.
"""

import numpy as np
from engine import StrategyContext, StrategyResult, CRISIS, DOWNTREND, UPTREND, RANGE, QUIET


def strategy(ctx: StrategyContext) -> StrategyResult:
    """Regime-adaptive multi-signal strategy."""
    close = ctx.ind_1h['close']
    n = len(close)
    rsi = ctx.ind_1h['rsi']
    regime = ctx.regime_1h
    ema_20 = ctx.ind_1h['ema_20']
    ema_50 = ctx.ind_1h['ema_50']
    bb_pct = ctx.ind_1h['bb_pct']
    macd = ctx.ind_1h['macd']
    macd_sig = ctx.ind_1h['macd_signal']
    adx = ctx.ind_1h['adx']
    atr = ctx.ind_1h['atr']
    vol_ratio = ctx.ind_1h['vol_ratio']

    rsi_prev = np.roll(rsi, 1); rsi_prev[0] = 50
    macd_prev = np.roll(macd, 1); macd_prev[0] = 0
    macd_sig_prev = np.roll(macd_sig, 1); macd_sig_prev[0] = 0

    # Price relative to EMAs
    above_ema20 = close > ema_20
    above_ema50 = close > ema_50
    ema_dist = (close - ema_20) / np.maximum(close, 1e-10)

    # === UPTREND SIGNALS (buy the dip) ===
    uptrend = regime == UPTREND
    # RSI pullback in uptrend: RSI dips below 40 then bounces
    up_long = (uptrend
               & (rsi < 40) & (rsi > rsi_prev)
               & above_ema50
               & (vol_ratio > 0.8))

    # === DOWNTREND SIGNALS (sell the rally) ===
    downtrend = regime == DOWNTREND
    # RSI rally rejection in downtrend: RSI rises above 60 then drops
    down_short = (downtrend
                  & (rsi > 60) & (rsi < rsi_prev)
                  & ~above_ema50
                  & (vol_ratio > 0.8))

    # === RANGE SIGNALS (mean reversion from BB extremes) ===
    ranging = (regime == RANGE) | (regime == QUIET)
    # Long: price at lower BB, RSI oversold
    range_long = (ranging
                  & (bb_pct < 0.15)
                  & (rsi < 35)
                  & (rsi > rsi_prev)
                  & (adx < 25))

    # Short: price at upper BB, RSI overbought
    range_short = (ranging
                   & (bb_pct > 0.85)
                   & (rsi > 65)
                   & (rsi < rsi_prev)
                   & (adx < 25))

    # === RSI-MACD CROSS (all regimes except CRISIS) ===
    macd_cross_long = ((rsi < 30) & (rsi > rsi_prev)
                       & (macd > macd_sig) & (macd_prev <= macd_sig_prev)
                       & (regime != CRISIS))

    macd_cross_short = ((rsi > 70) & (rsi < rsi_prev)
                        & (macd < macd_sig) & (macd_prev >= macd_sig_prev)
                        & (regime != CRISIS))

    # Combine all entry signals
    entry_long = up_long | range_long | macd_cross_long
    entry_short = down_short | range_short | macd_cross_short

    entry_mask = entry_long | entry_short
    direction = np.where(entry_long & ~entry_short, 1,
                np.where(entry_short & ~entry_long, -1,
                np.where(entry_long, 1, -1))).astype(np.int8)

    entry_mask[:200] = False

    if ctx.liquidity_mask is not None:
        entry_mask = entry_mask & ctx.liquidity_mask

    return StrategyResult(
        entry_mask=entry_mask,
        direction=direction,
        stop_mult=3.0,
        trail_mult=2.5,
        target_mult=999,
        no_stop_bars=20,
        min_hold=12,
        max_hold=720,
        edge=0.40,
        exit_regimes={CRISIS},
        max_trade_pct=0.12,
        breakeven_atr=0.8,
        exchange='binance',
        name='s201_regime_adaptive',
    )
