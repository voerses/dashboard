
import numpy as np
from engine import StrategyContext, StrategyResult, MarketType, CRISIS, DOWNTREND

def strategy(ctx: StrategyContext) -> StrategyResult:
    close = ctx.ind_1h["close"]
    n = len(close)
    rsi = ctx.ind_1h["rsi"]
    macd = ctx.ind_1h["macd"]
    macd_sig = ctx.ind_1h["macd_signal"]
    regime = ctx.regime_1h
    rsi_prev = np.roll(rsi, 1); rsi_prev[0] = 50
    macd_prev = np.roll(macd, 1); macd_prev[0] = 0
    macd_sig_prev = np.roll(macd_sig, 1); macd_sig_prev[0] = 0
    entry_long = ((rsi < 30) & (rsi > rsi_prev)
                  & (macd > macd_sig) & (macd_prev <= macd_sig_prev)
                  & (regime != CRISIS))
    entry_short = ((rsi > 70) & (rsi < rsi_prev)
                   & (macd < macd_sig) & (macd_prev >= macd_sig_prev)
                   & (regime != CRISIS))
    entry_mask = entry_long | entry_short
    direction = np.where(entry_long, 1, np.where(entry_short, -1, 0)).astype(np.int8)
    entry_mask[:200] = False
    if ctx.liquidity_mask is not None:
        entry_mask = entry_mask & ctx.liquidity_mask
    return StrategyResult(
        entry_mask=entry_mask, direction=direction,
        market_type=MarketType.PERP,
        leverage=3.0, stop_mult=3.0, trail_mult=3.0, target_mult=999,
        no_stop_bars=24, min_hold=18, max_hold=720,
        edge=0.8, exit_regimes={CRISIS},
        breakeven_atr=0.8, max_trade_pct=0.3,
        exchange="binance", name="s226_lev_test")
