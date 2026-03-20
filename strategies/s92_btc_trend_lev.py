"""
s92 BTC Trend Following with Leverage — Per-Token Strategy

Follows BTC's medium-term trend with controlled leverage.
Long when uptrending (close > EMA20 > EMA50), short when downtrending.
No mechanical stops — hold to weekly rebalance (168 bars).

Key design:
- Only intended for BTC (most liquid, clearest trends)
- Leverage 5x, 10% margin cap = 50% notional exposure
- NO stops/trails — hold to rebalance, let trend play out
- Weekly rebalance: re-evaluate direction, exit if trend unclear
- Regime sizing: larger in confirmed trends

Market: PERP (bidirectional)
Status: EXPERIMENTAL
"""

import numpy as np
from engine import (StrategyContext, StrategyResult, MarketType,
                    CRISIS, DOWNTREND, UPTREND, RANGE, QUIET)


LEVERAGE = 5.0
REBALANCE_BARS = 168       # 7-day rebalance
LOOKBACK_TREND = 14 * 24   # 14-day trailing return for confirmation
MIN_TREND_RET = 0.02       # 2% minimum trailing return for entry


def strategy(ctx: StrategyContext) -> StrategyResult:
    """BTC trend following — no stops, rebalance exit."""
    close = ctx.ind_1h['close']
    n = len(close)
    regime = ctx.regime_1h

    # BTC-only: return empty result for all other tokens
    if ctx.ticker != 'BTC':
        return StrategyResult(
            entry_mask=np.zeros(n, dtype=bool),
            direction=np.ones(n, dtype=np.int8),
            market_type=MarketType.PERP,
            leverage=1.0,
            stop_mult=99.0,
            trail_mult=99.0,
            target_mult=999,
            no_stop_bars=REBALANCE_BARS,
            min_hold=24,
            max_hold=REBALANCE_BARS,
            edge=0.35,
            exit_regimes=set(),
            exchange='binance',
            name='s92_btc_trend_lev',
            breakeven_atr=0.0,
        )

    ema_20 = ctx.ind_1h['ema_20']
    ema_50 = ctx.ind_1h['ema_50']

    # Trailing 14d return
    trail_ret = np.zeros(n, dtype=np.float64)
    lb = LOOKBACK_TREND
    if n > lb:
        trail_ret[lb:] = (close[lb:] - close[:-lb]) / np.maximum(close[:-lb], 1e-10)

    # Long signal: uptrend structure
    long_signal = (close > ema_20) & (ema_20 > ema_50) & (trail_ret > MIN_TREND_RET)

    # Short signal: downtrend structure
    short_signal = (close < ema_20) & (ema_20 < ema_50) & (trail_ret < -MIN_TREND_RET)

    # Entry at rebalance points only (thin the signal to weekly)
    entry_mask = np.zeros(n, dtype=bool)
    direction = np.ones(n, dtype=np.int8)

    for rb in range(lb, n, REBALANCE_BARS):
        if long_signal[rb]:
            # Mark entry at rebalance bar and keep direction for holding period
            entry_mask[rb:rb + REBALANCE_BARS] = True
            direction[rb:rb + REBALANCE_BARS] = 1
        elif short_signal[rb]:
            entry_mask[rb:rb + REBALANCE_BARS] = True
            direction[rb:rb + REBALANCE_BARS] = -1
        # else: no position this week

    # Burn-in
    entry_mask[:max(300, lb)] = False

    # Liquidity
    if ctx.liquidity_mask is not None:
        entry_mask = entry_mask & ctx.liquidity_mask

    # Regime sizing
    size_mult = np.where(regime == UPTREND, 1.5,
                np.where(regime == DOWNTREND, 1.5,
                np.where(regime == RANGE, 1.0,
                np.where(regime == QUIET, 0.5, 0.0))))

    return StrategyResult(
        entry_mask=entry_mask,
        direction=direction,
        market_type=MarketType.PERP,
        leverage=LEVERAGE,
        stop_mult=99.0,
        trail_mult=99.0,
        target_mult=999,
        no_stop_bars=REBALANCE_BARS,
        min_hold=24,
        max_hold=REBALANCE_BARS,
        edge=0.35,
        exit_regimes=set(),
        exchange='binance',
        name='s92_btc_trend_lev',
        breakeven_atr=0.0,
        size_multiplier=size_mult,
        cap_multiplier=2.0,
        max_trade_pct=0.10,
    )
