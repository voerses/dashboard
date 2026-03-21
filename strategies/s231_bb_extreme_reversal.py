"""Strategy S231: Bollinger Band extreme reversal at 3-sigma"""
import numpy as np
from engine import StrategyContext, StrategyResult, MarketType, CRISIS

def strategy(ctx: StrategyContext) -> StrategyResult:
    close = ctx.ind_1h['close']
    n = len(close)
    rsi = ctx.ind_1h['rsi']
    bb_pct = ctx.ind_1h['bb_pct']
    vol_ratio = ctx.ind_1h['vol_ratio']
    regime = ctx.regime_1h
    macd = ctx.ind_1h['macd']
    macd_sig = ctx.ind_1h['macd_signal']
    rsi_prev = np.roll(rsi, 1); rsi_prev[0] = 50

    # Very extreme BB + RSI confirmation + MACD direction
    entry_long = ((bb_pct < 0.03) & (rsi < 25) & (rsi > rsi_prev)
                  & (macd > macd_sig)
                  & (regime != CRISIS))
    entry_short = ((bb_pct > 0.97) & (rsi > 75) & (rsi < rsi_prev)
                   & (macd < macd_sig)
                   & (regime != CRISIS))

    entry_mask = entry_long | entry_short
    direction = np.where(entry_long, 1, np.where(entry_short, -1, 0)).astype(np.int8)
    entry_mask[:200] = False
    if ctx.liquidity_mask is not None:
        entry_mask = entry_mask & ctx.liquidity_mask

    return StrategyResult(
        entry_mask=entry_mask, direction=direction,
        market_type=MarketType.PERP, leverage=2.0,
        stop_mult=3.0, trail_mult=2.5, target_mult=999,
        no_stop_bars=18, min_hold=12, max_hold=504,
        edge=0.40, exit_regimes={CRISIS},
        breakeven_atr=0.8, max_trade_pct=0.12,
        exchange='binance', name='s231_bb_extreme_reversal')
