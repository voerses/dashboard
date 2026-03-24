"""
Strategy S10: Research-Driven Dip Buy
======================================
From indicator analysis findings:
- RSI_low + MACD_pos = best long combo: +1.37% mean, 58.3% WR, +1.01% synergy
- vol_ratio_hi + BB_pct_low = most robust: +0.99%, 58.4% WR, 45/49 tokens
- vol_ratio_hi + ret_neg = most universal: +0.92%, 56.2% WR, all 49 tokens

This strategy combines the top 3 actionable buy signals from the analysis.

Status: EXPERIMENTAL
"""

import numpy as np
from engine import StrategyContext, StrategyResult, CRISIS, DOWNTREND


def strategy(ctx: StrategyContext) -> StrategyResult:
    """Research-driven dip buy — combines top 3 indicator combos."""
    n = len(ctx.ind_1h['close'])

    rsi = ctx.ind_1h['rsi']
    macd = ctx.ind_1h['macd']
    macd_sig = ctx.ind_1h['macd_signal']
    macd_hist = ctx.ind_1h['macd_hist']
    vol_ratio = ctx.ind_1h['vol_ratio']
    bb_pct = ctx.ind_1h['bb_pct']
    ret_1 = ctx.ind_1h['ret_1']

    # Signal A: RSI oversold + MACD turning positive (58.3% WR, +1.37%)
    sig_a = (rsi < 30) & (macd_hist > 0)

    # Signal B: Volume spike at BB bottom (58.4% WR, +0.99%, 45/49 tokens)
    sig_b = (vol_ratio > 2.0) & (bb_pct < 0.1)

    # Signal C: Volume spike on down day (56.2% WR, +0.92%, 49/49 tokens)
    sig_c = (vol_ratio > 2.0) & (ret_1 < 0)

    # Enter on ANY of the 3 signals (OR logic — maximizes opportunity)
    entry = sig_a | sig_b | sig_c

    # Regime filter: not crisis
    regime_ok = ctx.regime_1h != 0
    entry = entry & regime_ok

    # 4H confirmation: RSI not overbought on 4H
    rsi_4h = ctx.align_4h_to_1h(ctx.ind_4h['rsi'])
    rsi_4h_ok = np.nan_to_num(rsi_4h, 50) < 65
    entry = entry & rsi_4h_ok

    entry[:200] = False

    # Use tighter stops for dip-buying (mean reversion profile)
    # but wider protection window since dips need time to recover
    return StrategyResult(
        entry_mask=entry,
        direction=np.ones(n, dtype=np.int8),
        stop_mult=3.0,
        trail_mult=3.0,
        target_mult=999,
        no_stop_bars=24,       # 24-bar protection from sweep findings
        min_hold=12,
        max_hold=480,
        edge=0.40,
        exit_regimes={CRISIS, DOWNTREND},
        name='research_dip_buy',
        breakeven_atr=0.5,
    )
