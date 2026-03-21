"""Strategy S232: Volume capitulation reversal — extreme volume + oversold"""
import numpy as np
from engine import StrategyContext, StrategyResult, MarketType, CRISIS

def strategy(ctx: StrategyContext) -> StrategyResult:
    close = ctx.ind_1h['close']
    n = len(close)
    rsi = ctx.ind_1h['rsi']
    vol_ratio = ctx.ind_1h['vol_ratio']
    regime = ctx.regime_1h
    rsi_prev = np.roll(rsi, 1); rsi_prev[0] = 50

    # Capitulation: extreme volume + extreme RSI + reversal
    entry_long = ((vol_ratio > 3.0) & (rsi < 25) & (rsi > rsi_prev)
                  & (regime != CRISIS))
    entry_short = ((vol_ratio > 3.0) & (rsi > 75) & (rsi < rsi_prev)
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
        edge=0.45, exit_regimes={CRISIS},
        breakeven_atr=0.8, max_trade_pct=0.12,
        exchange='binance', name='s232_volume_capitulation')
