"""Strategy S251: Momentum Trend Following (Bidirectional, Perp, 3x)
==================================================================
Rides momentum: enter when short-term momentum aligns with trend.
Key difference from reversal: enter WITH the move, not against it.
Uses breakout detection + trend confirmation + trailing stops.
"""
import numpy as np
from engine import StrategyContext, StrategyResult, MarketType, CRISIS, UPTREND, DOWNTREND

def strategy(ctx: StrategyContext) -> StrategyResult:
    close = ctx.ind_1h['close']
    n = len(close)
    rsi = ctx.ind_1h['rsi']
    ema_10 = ctx.ind_1h['ema_10']
    ema_20 = ctx.ind_1h['ema_20']
    ema_50 = ctx.ind_1h['ema_50']
    adx = ctx.ind_1h['adx']
    atr = ctx.ind_1h['atr']
    regime = ctx.regime_1h
    macd = ctx.ind_1h['macd']
    macd_sig = ctx.ind_1h['macd_signal']
    vol_ratio = ctx.ind_1h['vol_ratio']
    bb_pct = ctx.ind_1h['bb_pct']
    donch_high = ctx.ind_1h['donch_high']
    donch_low = ctx.ind_1h['donch_low']
    ret_1 = ctx.ind_1h['ret_1']

    rsi_prev = np.roll(rsi, 1); rsi_prev[0] = 50
    macd_prev = np.roll(macd, 1); macd_prev[0] = 0
    ms_prev = np.roll(macd_sig, 1); ms_prev[0] = 0
    close_prev = np.roll(close, 1); close_prev[0] = close[0]

    # EMA alignment: all aligned = strong trend
    ema_bull = (ema_10 > ema_20) & (ema_20 > ema_50)
    ema_bear = (ema_10 < ema_20) & (ema_20 < ema_50)

    # Signal 1: Donchian breakout in trend
    # Price breaks above 20-period high in bull alignment
    breakout_long = ((close > donch_high) & ema_bull & (adx > 20)
                     & (regime != CRISIS))
    breakout_short = ((close < donch_low) & ema_bear & (adx > 20)
                      & (regime != CRISIS))

    # Signal 2: MACD cross with trend confirmation
    macd_long = ((macd > macd_sig) & (macd_prev <= ms_prev)
                 & (ema_10 > ema_50)  # basic trend filter
                 & (rsi > 40) & (rsi < 70)  # not extreme
                 & (regime != CRISIS))
    macd_short = ((macd < macd_sig) & (macd_prev >= ms_prev)
                  & (ema_10 < ema_50)
                  & (rsi > 30) & (rsi < 60)
                  & (regime != CRISIS))

    # Signal 3: Volume-confirmed momentum
    # Strong price move on above-average volume
    vol_long = ((ret_1 > 0.02) & (vol_ratio > 2.0) & ema_bull
                & (regime != CRISIS))
    vol_short = ((ret_1 < -0.02) & (vol_ratio > 2.0) & ema_bear
                 & (regime != CRISIS))

    entry_long = breakout_long | macd_long | vol_long
    entry_short = breakout_short | macd_short | vol_short

    entry_mask = entry_long | entry_short
    direction = np.where(entry_long & ~entry_short, 1,
                np.where(entry_short & ~entry_long, -1,
                np.where(rsi > 50, 1, -1))).astype(np.int8)

    entry_mask[:200] = False
    if ctx.liquidity_mask is not None:
        entry_mask = entry_mask & ctx.liquidity_mask

    return StrategyResult(
        entry_mask=entry_mask, direction=direction,
        market_type=MarketType.PERP, leverage=3.0,
        stop_mult=3.0, trail_mult=2.5, target_mult=999,
        no_stop_bars=18, min_hold=12, max_hold=504,
        edge=0.80, exit_regimes={CRISIS},
        breakeven_atr=0.6, max_trade_pct=0.20,
        exchange='binance', name='s251_momentum_trend')
