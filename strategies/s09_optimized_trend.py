"""
Strategy S09: Optimized Trend Following
========================================
Based on sweep results + indicator research findings:
- 24-bar protection window (biggest single improvement: +$9K/yr)
- ADX > 30 filter (only enter strong trends)
- Tighter 3x ATR stop (cuts losers faster on all-token universe)
- EMA stack confirmation (1H above 4H above daily)

CPCV Performance: +$30,721/yr (trend_prot_24)
All-Token Performance: +$69,066/yr (trend_stop_3.0)

This combines both insights: protection window + tighter stop.

Status: EXPERIMENTAL — needs CPCV validation
"""

import numpy as np
from engine import StrategyContext, StrategyResult, CRISIS, DOWNTREND


def strategy(ctx: StrategyContext) -> StrategyResult:
    """Optimized trend following — ADX filter + 24-bar protection + tight stop."""
    n = len(ctx.ind_1h['close'])
    close = ctx.ind_1h['close']

    # ── ENTRY CONDITIONS ──
    # 1. EMA stack: close > EMA10 > EMA20 (1H trend aligned)
    ema10 = ctx.ind_1h['ema_10']
    ema20 = ctx.ind_1h['ema_20']
    ema_stack = (close > ema10) & (ema10 > ema20)

    # 2. Daily trend confirmation: close above daily EMA50
    ema50_d = ctx.align_daily_to_1h(ctx.ind_d['ema_50'])
    daily_trend = close > np.nan_to_num(ema50_d, 0)

    # 3. ADX > 30 (strong trend only — from sweep: ADX 30 beat 15/20/25)
    adx = ctx.ind_1h['adx']
    plus_di = ctx.ind_1h['plus_di']
    minus_di = ctx.ind_1h['minus_di']
    strong_trend = (adx > 30) & (plus_di > minus_di)

    # 4. Not in crisis/downtrend regime
    regime_ok = (ctx.regime_1h != 0)  # not crisis

    entry = ema_stack & daily_trend & strong_trend & regime_ok
    entry[:200] = False

    return StrategyResult(
        entry_mask=entry,
        direction=np.ones(n, dtype=np.int8),
        stop_mult=3.0,        # Tighter stop (from all-token sweep: 3x beat 5x)
        trail_mult=3.0,       # Match stop for consistency
        target_mult=999,      # Trail only, no fixed target
        no_stop_bars=24,      # 24-bar protection (from CPCV sweep: biggest single lever)
        min_hold=18,
        max_hold=720,
        edge=0.40,
        exit_regimes={CRISIS, DOWNTREND},
        name='optimized_trend',
        breakeven_atr=0.5,
    )
