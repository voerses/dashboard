"""
Strategy S203: Extreme Mean Reversion (Bidirectional, Perp)
============================================================
Enters only on extreme deviations from mean — 3+ sigma moves.
These are rare events that have high reversion probability.

Signal: Price > 3 std devs from 50-bar mean + reversal confirmation
Long: Extreme oversold (> 3 sigma down) + RSI turning up
Short: Extreme overbought (> 3 sigma up) + RSI turning down
"""

import numpy as np
import pandas as pd
from engine import StrategyContext, StrategyResult, CRISIS


def strategy(ctx: StrategyContext) -> StrategyResult:
    """Extreme mean reversion at 3+ sigma."""
    close = ctx.ind_1h['close']
    n = len(close)
    rsi = ctx.ind_1h['rsi']
    regime = ctx.regime_1h
    vol_ratio = ctx.ind_1h['vol_ratio']

    rsi_prev = np.roll(rsi, 1); rsi_prev[0] = 50

    # Z-score of close relative to 50-bar rolling mean/std
    rm50 = pd.Series(close).rolling(50, min_periods=50).mean().values
    rs50 = pd.Series(close).rolling(50, min_periods=50).std().values
    zscore = np.where(rs50 > 0, (close - rm50) / np.maximum(rs50, 1e-10), 0)
    zscore = np.nan_to_num(zscore, nan=0)

    # Also check 20-bar z-score for shorter-term extreme
    rm20 = pd.Series(close).rolling(20, min_periods=20).mean().values
    rs20 = pd.Series(close).rolling(20, min_periods=20).std().values
    zscore_20 = np.where(rs20 > 0, (close - rm20) / np.maximum(rs20, 1e-10), 0)
    zscore_20 = np.nan_to_num(zscore_20, nan=0)

    # Long: extreme oversold on both timeframes + turning up
    entry_long = ((zscore < -2.5)
                  & (zscore_20 < -2.0)
                  & (rsi < 30)
                  & (rsi > rsi_prev)
                  & (regime != CRISIS))

    # Short: extreme overbought + turning down
    entry_short = ((zscore > 2.5)
                   & (zscore_20 > 2.0)
                   & (rsi > 70)
                   & (rsi < rsi_prev)
                   & (regime != CRISIS))

    entry_mask = entry_long | entry_short
    direction = np.where(entry_long, 1, np.where(entry_short, -1, 0)).astype(np.int8)
    entry_mask[:200] = False

    if ctx.liquidity_mask is not None:
        entry_mask = entry_mask & ctx.liquidity_mask

    return StrategyResult(
        entry_mask=entry_mask,
        direction=direction,
        stop_mult=4.0,
        trail_mult=2.0,
        target_mult=6.0,  # Take profit at 6 ATR (mean reversion target)
        no_stop_bars=12,
        min_hold=6,
        max_hold=168,  # 1 week max (mean reversion should be fast)
        edge=0.45,
        exit_regimes={CRISIS},
        max_trade_pct=0.12,
        breakeven_atr=1.0,
        exchange='binance',
        name='s203_mean_revert_extreme',
    )
