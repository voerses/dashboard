"""
Research Template — Quick Signal Hypothesis Testing
====================================================

Copy this file to test a new signal through the v4 engine:

    cp strategies/RESEARCH_TEMPLATE.py strategies/sNNN_my_signal.py

Then edit the PARAMETERS section and the signal logic in strategy().

Run through v4 portfolio backtest (Gate 1 quick test):

    # Pure signal quality — no portfolio constraints, no walk-forward burn
    python v4/portfolio_backtest.py \
        --strategy sNNN \
        --market perp \
        --months 12 \
        --capital 100000 \
        --raw --skip-wf

    # With walk-forward mask (check for overfitting)
    python v4/portfolio_backtest.py \
        --strategy sNNN \
        --market perp \
        --months 12 \
        --capital 100000 \
        --raw

    # Full production pipeline (portfolio constraints ON)
    python v4/portfolio_backtest.py \
        --strategy sNNN \
        --market perp \
        --months 12 \
        --capital 100000

Interpret results across the 3 modes:
    - Mode 1 bad         → signal has no edge, KILL
    - Mode 1 good, 2 bad → signal is overfitted, reduce params
    - 1-2 good, 3 bad    → portfolio constraints kill it, tune sizing

Status: RESEARCH
"""

import numpy as np
from engine import (StrategyContext, StrategyResult, MarketType,
                    CRISIS, DOWNTREND,
                    rolling_mean, rolling_std, rolling_max, rolling_min,
                    rolling_median, rolling_zscore, rolling_skew, rolling_corr)


# ╔══════════════════════════════════════════════════════════════════╗
# ║  PARAMETERS — Edit these to experiment                         ║
# ╚══════════════════════════════════════════════════════════════════╝

# -- Signal parameters (your hypothesis) --
LOOKBACK = 168          # rolling window for signal computation (hours)
THRESHOLD = 2.0         # z-score threshold for entry
DIRECTION = "both"      # "long", "short", or "both"

# -- Trade management --
LEVERAGE = 1.0          # notional multiplier (1.0 = no leverage)
MAX_HOLD = 24           # max hours to hold a position
STOP_MULT = 3.0         # stop loss in ATR multiples (999 = no stop)
TRAIL_MULT = 3.0        # trailing stop in ATR multiples
EDGE = 0.35             # Kelly edge estimate (affects position sizing)
MIN_HOLD = 1            # minimum hours before exit allowed
NO_STOP_BARS = 0        # hours of stop protection after entry

# -- Market --
MARKET = MarketType.PERP   # MarketType.SPOT or MarketType.PERP


# ╔══════════════════════════════════════════════════════════════════╗
# ║  STRATEGY FUNCTION — Edit signal logic below                   ║
# ╚══════════════════════════════════════════════════════════════════╝

def strategy(ctx: StrategyContext) -> StrategyResult:
    """
    Signal hypothesis under test.

    Available data (see TEMPLATE.py for full reference):
        ctx.ind_1h['close'], ['high'], ['low'], ['volume']
        ctx.ind_1h['ema_10'], ['ema_20'], ['ema_50']
        ctx.ind_1h['rsi'], ['atr'], ['adx'], ['ret_1'], ['vol_20']
        ctx.ind_1h['bb_upper'], ['bb_lower'], ['bb_width'], ['bb_pct']
        ctx.ind_1h['macd'], ['macd_signal'], ['macd_hist']
        ctx.ind_1h['vol_ratio'], ['taker']
        ctx.ind_4h, ctx.ind_d  — same indicators on 4H/daily
        ctx.regime_1h          — 0=crisis, 1=quiet, 2=uptrend, 3=range, 4=downtrend
        ctx.funding_1h         — per-hour funding rate (perp only, None for spot)
        ctx.funding_raw        — raw settlement-interval rate
        ctx.custom             — dict of plugin indicators (obv, vwap, momentum, etc.)
        ctx.ticker             — 'BTC', 'ETH', etc.
        ctx.liquidity_mask     — bool array, True = liquid enough to trade

        Rolling helpers (vectorized):
        rolling_mean, rolling_std, rolling_max, rolling_min,
        rolling_median, rolling_zscore, rolling_skew, rolling_corr
    """
    n = len(ctx.ind_1h['close'])
    close = ctx.ind_1h['close']

    # ── YOUR SIGNAL LOGIC HERE ─────────────────────────────────────
    #
    # Example: funding rate mean-reversion
    #   funding = ctx.funding_1h if ctx.funding_1h is not None else np.zeros(n)
    #   funding_zscore = rolling_zscore(funding, LOOKBACK)
    #   long_signal = funding_zscore < -THRESHOLD
    #   short_signal = funding_zscore > THRESHOLD
    #
    # Example: momentum burst
    #   ret = ctx.ind_1h['ret_1']
    #   long_signal = ret > 0.03
    #   short_signal = ret < -0.03
    #
    # Example: BB squeeze breakout
    #   bb_width = ctx.ind_1h['bb_width']
    #   squeeze = rolling_zscore(bb_width, LOOKBACK) < -THRESHOLD
    #   long_signal = squeeze & (close > ctx.ind_1h['bb_upper'])
    #   short_signal = squeeze & (close < ctx.ind_1h['bb_lower'])

    # Replace with your actual signal:
    long_signal = np.zeros(n, dtype=bool)
    short_signal = np.zeros(n, dtype=bool)
    # ───────────────────────────────────────────────────────────────

    # Compose entry mask and direction from signals
    if DIRECTION == "long":
        entry = long_signal
        direction = np.ones(n, dtype=np.int8)
    elif DIRECTION == "short":
        entry = short_signal
        direction = -np.ones(n, dtype=np.int8)
    else:  # "both"
        entry = long_signal | short_signal
        direction = np.where(long_signal, 1, np.where(short_signal, -1, 0)).astype(np.int8)

    # Warmup guard — don't trade in first LOOKBACK bars
    entry[:max(LOOKBACK, 200)] = False

    return StrategyResult(
        entry_mask=entry,
        direction=direction,
        market_type=MARKET,
        leverage=LEVERAGE,
        stop_mult=STOP_MULT,
        trail_mult=TRAIL_MULT,
        target_mult=999,
        no_stop_bars=NO_STOP_BARS,
        min_hold=MIN_HOLD,
        max_hold=MAX_HOLD,
        edge=EDGE,
        name='research_signal',
        breakeven_atr=0.5,
    )
