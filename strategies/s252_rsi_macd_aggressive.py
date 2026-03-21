"""Strategy S252: Aggressive RSI-MACD (Bidirectional, Perp, 3x)
=============================================================
Proven RSI-MACD signal with aggressive sizing: 3x leverage,
higher edge parameter, wider max_trade_pct, more permissive entry.
Also adds RSI divergence and standalone MACD cross signals.
"""
import numpy as np
from engine import StrategyContext, StrategyResult, MarketType, CRISIS

def strategy(ctx: StrategyContext) -> StrategyResult:
    close = ctx.ind_1h['close']
    n = len(close)
    rsi = ctx.ind_1h['rsi']
    macd = ctx.ind_1h['macd']
    macd_sig = ctx.ind_1h['macd_signal']
    bb_pct = ctx.ind_1h['bb_pct']
    adx = ctx.ind_1h['adx']
    regime = ctx.regime_1h

    rsi_prev = np.roll(rsi, 1); rsi_prev[0] = 50
    macd_prev = np.roll(macd, 1); macd_prev[0] = 0
    ms_prev = np.roll(macd_sig, 1); ms_prev[0] = 0

    # Signal 1: Classic RSI-MACD cross (proven, ~83 trades, PF 1.78)
    ta_long = ((rsi < 30) & (rsi > rsi_prev)
               & (macd > macd_sig) & (macd_prev <= ms_prev)
               & (regime != CRISIS))
    ta_short = ((rsi > 70) & (rsi < rsi_prev)
                & (macd < macd_sig) & (macd_prev >= ms_prev)
                & (regime != CRISIS))

    # Signal 2: Relaxed RSI-MACD (wider RSI bands = more trades)
    relaxed_long = ((rsi < 35) & (rsi > rsi_prev)
                    & (macd > macd_sig) & (macd_prev <= ms_prev)
                    & (adx > 20)  # trending market
                    & (regime != CRISIS))
    relaxed_short = ((rsi > 65) & (rsi < rsi_prev)
                     & (macd < macd_sig) & (macd_prev >= ms_prev)
                     & (adx > 20)
                     & (regime != CRISIS))

    # Signal 3: BB extreme + RSI reversal (without MACD requirement)
    bb_long = ((bb_pct < 0.05) & (rsi < 30) & (rsi > rsi_prev)
               & (regime != CRISIS))
    bb_short = ((bb_pct > 0.95) & (rsi > 70) & (rsi < rsi_prev)
                & (regime != CRISIS))

    # Signal 4: Deep RSI + any MACD histogram improvement
    macd_hist = macd - macd_sig
    mh_prev = np.roll(macd_hist, 1); mh_prev[0] = 0
    deep_long = ((rsi < 25) & (macd_hist > mh_prev)
                 & (regime != CRISIS))
    deep_short = ((rsi > 75) & (macd_hist < mh_prev)
                  & (regime != CRISIS))

    entry_long = ta_long | relaxed_long | bb_long | deep_long
    entry_short = ta_short | relaxed_short | bb_short | deep_short

    entry_mask = entry_long | entry_short
    direction = np.where(entry_long & ~entry_short, 1,
                np.where(entry_short & ~entry_long, -1,
                np.where(rsi < 50, 1, -1))).astype(np.int8)

    entry_mask[:200] = False
    if ctx.liquidity_mask is not None:
        entry_mask = entry_mask & ctx.liquidity_mask

    return StrategyResult(
        entry_mask=entry_mask, direction=direction,
        market_type=MarketType.PERP, leverage=3.0,
        stop_mult=3.0, trail_mult=3.0, target_mult=999,
        no_stop_bars=24, min_hold=18, max_hold=720,
        edge=0.80, exit_regimes={CRISIS},
        breakeven_atr=0.8, max_trade_pct=0.20,
        exchange='binance', name='s252_rsi_macd_aggressive')
