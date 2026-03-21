"""
Strategy S110: RSI-MACD Optimized (Long-Only, Perp)
====================================================
Sweep-optimized variant of s108 rsi_macd_divergence.
Found via 87-config parameter sweep on 6mo window.

Key change from s108: RSI threshold 30 (vs 35), breakeven_atr=0.8 (vs 0.5)
This tighter RSI filter cuts trades from 125→42 but dramatically improves quality:
  PF: 1.34→2.82, DD: -2.8%→-1.0%, Calmar: ??→3.91

Signal: RSI oversold bounce + MACD bullish crossover (same as s108)
  - RSI < 30 and rising (tighter filter = higher quality entries)
  - MACD crosses above signal line (fresh bullish cross)
  - Breakeven at 0.8 ATR (aggressive stop-to-breakeven)
  - Long only, exit on CRISIS+DOWNTREND

6mo Performance (sweep): Ann=+3.8%, DD=-1.0%, PF=2.82, Cal=3.91, 42 trades

Status: EXPERIMENTAL — needs multi-window validation + leverage testing
"""

import numpy as np
from engine import StrategyContext, StrategyResult, CRISIS, DOWNTREND


def strategy(ctx: StrategyContext) -> StrategyResult:
    """Optimized RSI-MACD — RSI<30 + MACD cross, breakeven 0.8 ATR."""
    n = len(ctx.ind_1h['close'])

    close = ctx.ind_1h['close']
    rsi = ctx.ind_1h['rsi']
    macd = ctx.ind_1h['macd']
    macd_sig = ctx.ind_1h['macd_signal']
    regime = ctx.regime_1h

    # RSI turning up from oversold (tighter threshold = 30)
    rsi_prev = np.roll(rsi, 1)
    rsi_prev[0] = 50

    # MACD bullish cross
    macd_prev = np.roll(macd, 1)
    macd_prev[0] = 0
    macd_sig_prev = np.roll(macd_sig, 1)
    macd_sig_prev[0] = 0

    # Entry: RSI oversold + turning up + fresh MACD bullish cross
    entry = ((rsi < 30)
             & (rsi > rsi_prev)
             & (macd > macd_sig)
             & (macd_prev <= macd_sig_prev)
             & (regime != CRISIS))

    entry[:200] = False  # warmup

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
        max_trade_pct=0.12,
        breakeven_atr=0.8,
        exchange='binance',
        name='s110_rsi_macd_optimized',
    )
