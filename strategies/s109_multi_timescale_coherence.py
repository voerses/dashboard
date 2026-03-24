"""
Strategy S109: Multi-Timescale Coherence (Bidirectional)
========================================================
Auto-research pipeline marginal survivor (2026-03-21, v2 run).
Passed 12mo gate (PF=1.02, -0.5% ann) and positive in 2026 (+3.0% ann).
Higher DD than s108 (-30% vs -7%) so treat as secondary candidate.

Signal: Triple-timeframe EMA alignment (1h + 4h + daily) with ADX filter
  - All three timeframes must agree on trend direction
  - ADX > 20 confirms trend strength
  - Bidirectional (long + short)

12mo Performance: Ann=-0.5%, DD=-30.3%, PF=1.02, 4394 trades
3mo (2026) Performance: Ann=+3.0%, Cal=0.15, DD=-19.5%, PF=1.05

Config: wide_bidir (1x leverage, trail=3.0, exit on CRISIS only)

Status: EXPERIMENTAL — needs Gate 4-5 validation
"""

import numpy as np
from engine import (StrategyContext, StrategyResult, MarketType,
                    CRISIS, DOWNTREND)


def strategy(ctx: StrategyContext) -> StrategyResult:
    """Multi-timescale coherence — all 3 timeframes must agree."""
    n = len(ctx.ind_1h['close'])

    close = ctx.ind_1h['close']
    ema10 = ctx.ind_1h['ema_10']
    ema20 = ctx.ind_1h['ema_20']
    ema50 = ctx.ind_1h['ema_50']
    adx = ctx.ind_1h['adx']
    regime = ctx.regime_1h

    # 4h alignment
    close_4h = ctx.ind_4h['close']
    ema20_4h = ctx.ind_4h['ema_20']
    align_4h = ctx.align_4h_to_1h((close_4h > ema20_4h).astype(np.float64))

    # Daily alignment
    close_d = ctx.ind_d['close']
    ema20_d = ctx.ind_d['ema_20']
    align_d = ctx.align_daily_to_1h((close_d > ema20_d).astype(np.float64))

    # Coherence: all 3 TFs must agree
    bull_1h = (ema10 > ema20) & (ema20 > ema50)
    bear_1h = (ema10 < ema20) & (ema20 < ema50)

    entry_long = bull_1h & (align_4h > 0.5) & (align_d > 0.5) & (adx > 20) & (regime != CRISIS)
    entry_short = bear_1h & (align_4h < 0.5) & (align_d < 0.5) & (adx > 20) & (regime != CRISIS)

    entry_mask = entry_long | entry_short
    direction = np.where(entry_long, 1, np.where(entry_short, -1, 0)).astype(np.int8)
    entry_mask[:200] = False

    if ctx.liquidity_mask is not None:
        entry_mask = entry_mask & ctx.liquidity_mask

    return StrategyResult(
        entry_mask=entry_mask,
        direction=direction,
        market_type=MarketType.PERP,
        stop_mult=3.0,
        trail_mult=3.0,
        target_mult=999,
        no_stop_bars=24,
        min_hold=12,
        max_hold=720,
        edge=0.40,
        exit_regimes={CRISIS},
        max_trade_pct=0.12,
        exchange='binance',
        name='s109_multi_timescale_coherence',
        breakeven_atr=0.5,
    )
