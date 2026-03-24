"""
Strategy S11: Momentum Burst
=============================
From sweep results: momentum burst strategies ranked #3-5 on all tokens.
Simple but effective: enter when hourly return exceeds threshold + trend filter.

All-Token Performance: mom_burst_0.03 = +$50,296/yr (31/49 profitable)
                       mom_burst_0.05 = +$43,979/yr (31/49 profitable)

Key insight from research:
- ADX is the #1 predictor (IC=0.067), increases with horizon
- Taker buy ratio useful at 1d but decays by 5d
- In quiet markets, ret_1 momentum (IC=+0.074) is the best signal

Status: EXPERIMENTAL
"""

import numpy as np
from engine import StrategyContext, StrategyResult, CRISIS, DOWNTREND


def strategy(ctx: StrategyContext) -> StrategyResult:
    """Momentum burst — enter on strong hourly move with ADX confirmation."""
    n = len(ctx.ind_1h['close'])

    ret_1 = ctx.ind_1h['ret_1']
    adx = ctx.ind_1h['adx']
    ema20 = ctx.ind_1h['ema_20']
    ema50 = ctx.ind_1h['ema_50']
    close = ctx.ind_1h['close']

    # Core: strong hourly return (momentum burst)
    burst = ret_1 > 0.03  # 3% hourly move

    # ADX filter: trend must be present (from research: ADX #1 predictor)
    adx_ok = adx > 20

    # Trend alignment: above EMA20 (simple but effective)
    trend_ok = close > ema20

    # Volume confirmation
    vol_ratio = ctx.ind_1h['vol_ratio']
    vol_ok = vol_ratio > 1.0  # at least average volume

    entry = burst & adx_ok & trend_ok & vol_ok
    entry[:200] = False

    return StrategyResult(
        entry_mask=entry,
        direction=np.ones(n, dtype=np.int8),
        stop_mult=3.0,
        trail_mult=3.0,
        target_mult=999,
        no_stop_bars=24,
        min_hold=18,
        max_hold=720,
        edge=0.40,
        exit_regimes={CRISIS, DOWNTREND},
        name='momentum_burst',
        max_trade_pct=0.12,
        breakeven_atr=0.5,
    )
