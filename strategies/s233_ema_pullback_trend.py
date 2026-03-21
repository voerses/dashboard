"""Strategy S233: EMA pullback in strong trend — buy the dip / sell the rip"""
import numpy as np
from engine import StrategyContext, StrategyResult, MarketType, CRISIS, UPTREND, DOWNTREND

def strategy(ctx: StrategyContext) -> StrategyResult:
    close = ctx.ind_1h['close']
    n = len(close)
    rsi = ctx.ind_1h['rsi']
    ema_20 = ctx.ind_1h['ema_20']
    ema_50 = ctx.ind_1h['ema_50']
    adx = ctx.ind_1h['adx']
    regime = ctx.regime_1h
    macd = ctx.ind_1h['macd']
    macd_sig = ctx.ind_1h['macd_signal']
    rsi_prev = np.roll(rsi, 1); rsi_prev[0] = 50

    # Strong trend pullback: price touches EMA20 in strong trend, RSI confirms
    # Long: in uptrend, price pulls back to EMA20, RSI oversold and turning
    entry_long = ((regime == UPTREND) & (adx > 30)
                  & (close <= ema_20 * 1.005)  # within 0.5% of EMA20
                  & (close > ema_50)
                  & (rsi < 40) & (rsi > rsi_prev)
                  & (macd > macd_sig))

    # Short: in downtrend, price rallies to EMA20, RSI overbought and turning
    entry_short = ((regime == DOWNTREND) & (adx > 30)
                   & (close >= ema_20 * 0.995)
                   & (close < ema_50)
                   & (rsi > 60) & (rsi < rsi_prev)
                   & (macd < macd_sig))

    entry_mask = entry_long | entry_short
    direction = np.where(entry_long, 1, np.where(entry_short, -1, 0)).astype(np.int8)
    entry_mask[:200] = False
    if ctx.liquidity_mask is not None:
        entry_mask = entry_mask & ctx.liquidity_mask

    return StrategyResult(
        entry_mask=entry_mask, direction=direction,
        market_type=MarketType.PERP, leverage=2.0,
        stop_mult=2.5, trail_mult=2.0, target_mult=999,
        no_stop_bars=18, min_hold=12, max_hold=504,
        edge=0.40, exit_regimes={CRISIS},
        breakeven_atr=0.6, max_trade_pct=0.12,
        exchange='binance', name='s233_ema_pullback_trend')
