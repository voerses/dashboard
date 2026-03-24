"""
Strategy S108: RSI-MACD Divergence (Long-Only)
==============================================
Auto-research pipeline survivor (2026-03-21, v2 run).
Only signal to pass dual-window gate from 270 candidates.

Signal: RSI oversold bounce + MACD bullish crossover
  - RSI < 35 and rising (momentum turning up from oversold)
  - MACD crosses above signal line (fresh bullish cross)
  - Long only, exit on CRISIS+DOWNTREND

12mo Performance: Ann=-0.2%, DD=-6.7%, PF=1.02, 208 trades
  (BTC was -11.2% over same period — strategy outperformed by +11%)
3mo (2026) Performance: Ann=+1.2%, Cal=0.45, DD=-2.7%, PF=1.22

Config: s11_long (1x leverage, trail=3.0, no_stop=24, min_hold=18, max_hold=720)

Status: EXPERIMENTAL — needs Gate 4-5 validation
"""

import numpy as np
from engine import StrategyContext, StrategyResult, CRISIS, DOWNTREND


def strategy(ctx: StrategyContext) -> StrategyResult:
    """RSI-MACD divergence — long only on oversold bounce with MACD cross."""
    n = len(ctx.ind_1h['close'])

    close = ctx.ind_1h['close']
    rsi = ctx.ind_1h['rsi']
    macd = ctx.ind_1h['macd']
    macd_sig = ctx.ind_1h['macd_signal']
    regime = ctx.regime_1h

    # RSI turning up from oversold
    rsi_prev = np.roll(rsi, 1)
    rsi_prev[0] = 50

    # MACD bullish cross (current bar: macd > signal, previous bar: macd <= signal)
    macd_prev = np.roll(macd, 1)
    macd_prev[0] = 0
    macd_sig_prev = np.roll(macd_sig, 1)
    macd_sig_prev[0] = 0

    # Entry: RSI oversold + turning up + fresh MACD bullish cross
    entry = ((rsi < 35)
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
        exchange='binance',
        name='s108_rsi_macd_divergence',
        breakeven_atr=0.5,
    )
