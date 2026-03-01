"""
Strategy S07: RSI Bounce
========================
Simple example strategy to demonstrate the plugin architecture.

Entry: RSI < 25 on 1H + regime is range/quiet + volume spike
Exit: Trail 3x ATR, convex exit toward EMA20

Status: EXPERIMENTAL
"""

import numpy as np
from engine import StrategyContext, StrategyResult, CRISIS, DOWNTREND, RANGE, QUIET


def strategy(ctx: StrategyContext) -> StrategyResult:
    """RSI bounce — buy deep oversold with volume confirmation."""
    n = len(ctx.ind_1h['close'])

    # Conditions
    rsi_1h = ctx.ind_1h['rsi']
    vol_ratio = ctx.ind_1h['vol_ratio']
    regime = ctx.regime_1h

    rsi_oversold = rsi_1h < 25
    vol_ok = vol_ratio > 1.5
    regime_ok = (regime == RANGE) | (regime == QUIET)

    # 4H confirmation: RSI also low
    rsi_4h = ctx.align_4h_to_1h(ctx.ind_4h['rsi'])
    rsi_4h_ok = np.nan_to_num(rsi_4h, 50) < 40

    entry = rsi_oversold & vol_ok & regime_ok & rsi_4h_ok
    entry[:200] = False

    # Target: EMA20 on 4H
    target = ctx.align_4h_to_1h(ctx.ind_4h['ema_20'])

    return StrategyResult(
        entry_mask=entry,
        direction=np.ones(n, dtype=np.int8),
        stop_mult=2.5, trail_mult=2.0, target_mult=5.0,
        no_stop_bars=6, min_hold=6, max_hold=120,
        edge=0.45,
        exit_regimes={CRISIS, DOWNTREND},
        convex_exit=True, mean_target_vals=target,
        name='rsi_bounce',
    )
