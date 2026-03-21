"""
Strategy S129: RSI-MACD Bidirectional (Long+Short, Perp)
=========================================================
Combines s110 (long) + s128 (short) into one strategy.
Long: RSI < 30 + rising + MACD bullish cross
Short: RSI > 70 + declining + MACD bearish cross

This should increase total trades (longs + shorts) while maintaining
the same selectivity per direction.
"""

import numpy as np
from engine import StrategyContext, StrategyResult, CRISIS, DOWNTREND


def strategy(ctx: StrategyContext) -> StrategyResult:
    """RSI-MACD bidirectional — long oversold, short overbought."""
    n = len(ctx.ind_1h['close'])

    rsi = ctx.ind_1h['rsi']
    macd = ctx.ind_1h['macd']
    macd_sig = ctx.ind_1h['macd_signal']
    regime = ctx.regime_1h

    rsi_prev = np.roll(rsi, 1); rsi_prev[0] = 50
    macd_prev = np.roll(macd, 1); macd_prev[0] = 0
    macd_sig_prev = np.roll(macd_sig, 1); macd_sig_prev[0] = 0

    # Long: RSI oversold bounce + MACD bullish cross
    entry_long = ((rsi < 30)
                  & (rsi > rsi_prev)
                  & (macd > macd_sig)
                  & (macd_prev <= macd_sig_prev)
                  & (regime != CRISIS))

    # Short: RSI overbought rejection + MACD bearish cross
    entry_short = ((rsi > 70)
                   & (rsi < rsi_prev)
                   & (macd < macd_sig)
                   & (macd_prev >= macd_sig_prev)
                   & (regime != CRISIS))

    entry_mask = entry_long | entry_short
    direction = np.where(entry_long, 1, np.where(entry_short, -1, 0)).astype(np.int8)
    entry_mask[:200] = False

    if ctx.liquidity_mask is not None:
        entry_mask = entry_mask & ctx.liquidity_mask

    return StrategyResult(
        entry_mask=entry_mask,
        direction=direction,
        stop_mult=3.0,
        trail_mult=3.0,
        target_mult=999,
        no_stop_bars=24,
        min_hold=18,
        max_hold=720,
        edge=0.40,
        exit_regimes={CRISIS},  # Only exit on CRISIS for both directions
        max_trade_pct=0.12,
        breakeven_atr=0.8,
        exchange='binance',
        name='s129_rsi_macd_bidir',
    )
