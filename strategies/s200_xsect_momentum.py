"""
Strategy S200: Cross-Sectional Momentum (Bidirectional, Perp)
==============================================================
Ranks all tokens by recent momentum. Longs top performers, shorts worst.
Key insight: instead of predicting individual token direction, exploit
relative strength across the universe.

Signal: Weekly momentum rank (168h return) with RSI filter
Long: Top momentum quartile + RSI not overbought
Short: Bottom momentum quartile + RSI not oversold
"""

import numpy as np
from engine import StrategyContext, StrategyResult, CRISIS, DOWNTREND


def strategy(ctx: StrategyContext) -> StrategyResult:
    """Cross-sectional momentum — rank-based entries."""
    close = ctx.ind_1h['close']
    n = len(close)
    rsi = ctx.ind_1h['rsi']
    regime = ctx.regime_1h
    adx = ctx.ind_1h['adx']
    vol_20 = ctx.ind_1h['vol_20']

    # Momentum: 168h (7d) log return
    mom_168 = np.zeros(n)
    mom_168[168:] = np.log(close[168:] / close[:-168])

    # Also 48h momentum for shorter-term signal
    mom_48 = np.zeros(n)
    mom_48[48:] = np.log(close[48:] / close[:-48])

    # Volatility-adjusted momentum
    vol_safe = np.maximum(vol_20, 0.005)
    mom_adj = mom_168 / vol_safe

    # Percentile rank of momentum (within this token's own history)
    # Use expanding window to avoid look-ahead
    mom_rank = np.zeros(n)
    for i in range(200, n):
        window = mom_adj[200:i + 1]
        mom_rank[i] = (window < mom_adj[i]).sum() / len(window)

    # Entry conditions:
    # Long: strong momentum (top 20%) + trend confirmation + RSI not extreme
    entry_long = ((mom_rank > 0.80)
                  & (mom_48 > 0)
                  & (rsi < 70)
                  & (rsi > 40)
                  & (adx > 20)
                  & (regime != CRISIS))

    # Short: weak momentum (bottom 20%) + trend confirmation + RSI not extreme
    entry_short = ((mom_rank < 0.20)
                   & (mom_48 < 0)
                   & (rsi > 30)
                   & (rsi < 60)
                   & (adx > 20)
                   & (regime != CRISIS))

    entry_mask = entry_long | entry_short
    direction = np.where(entry_long, 1, np.where(entry_short, -1, 0)).astype(np.int8)
    entry_mask[:250] = False

    if ctx.liquidity_mask is not None:
        entry_mask = entry_mask & ctx.liquidity_mask

    # Conviction based on momentum strength
    conviction = np.abs(mom_adj) / np.maximum(np.abs(mom_adj).max(), 1e-10)
    conviction = np.clip(conviction, 0, 1)

    return StrategyResult(
        entry_mask=entry_mask,
        direction=direction,
        stop_mult=3.0,
        trail_mult=2.5,
        target_mult=999,
        no_stop_bars=24,
        min_hold=12,
        max_hold=504,  # 3 weeks
        edge=0.40,
        exit_regimes={CRISIS},
        max_trade_pct=0.12,
        breakeven_atr=0.8,
        conviction_score=conviction,
        exchange='binance',
        name='s200_xsect_momentum',
    )
