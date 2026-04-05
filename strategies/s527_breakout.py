"""
S527 — Breakout from Consolidation Regime
==========================================

Trades when HMM transitions from consolidation (state 0) to trending
(state 1 = bull, state 2 = bear).

Entry logic:
- Track HMM state transitions on daily bars (forward-filled to 1h with 1-day lag).
- On transition day AND next 3 days (72 1h bars entry window):
    Confirm: volume > 1.5x 20-bar avg AND close outside BB(20, 2)
    LONG  if transitioning to bull (state 1)
    SHORT if transitioning to bear (state 2)
- After window closes, no entries until next transition.

Parameters optimized for breakout momentum capture.
"""

import os
import numpy as np
import pandas as pd
from engine import (StrategyContext, StrategyResult, MarketType,
                    rolling_mean, rolling_std)


# ======================================================================
#  Parameters
# ======================================================================

LEVERAGE = 2.0
STOP_MULT = 3.0
TRAIL_MULT = 2.0
TARGET_MULT = 999.0  # let breakouts run
MAX_HOLD = 336       # 14 days
MIN_HOLD = 24
NO_STOP_BARS = 24
WARMUP = 200

ENTRY_WINDOW_BARS = 72   # 3 days in 1h bars
VOL_CONFIRM_MULT = 1.5   # volume > 1.5x 20-bar avg
BB_PERIOD = 20
BB_STD = 2.0

MARKET = MarketType.PERP

PORTFOLIO_CONFIG = {
    "conviction_mode": "ranked",
    "max_positions": 30,
}


# ======================================================================
#  HMM Regime Cache
# ======================================================================

_REGIME_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "alternative", "hmm_regimes",
)

_regime_cache: dict = {}
_aligned_cache: dict = {}


def _load_regime(token: str) -> pd.DataFrame | None:
    if token in _regime_cache:
        return _regime_cache[token]
    path = os.path.join(_REGIME_DIR, f"{token}_daily_regime.parquet")
    if not os.path.exists(path):
        _regime_cache[token] = None
        return None
    df = pd.read_parquet(path)
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    _regime_cache[token] = df
    return df


def _get_regime_aligned(token: str, idx_1h: pd.DatetimeIndex) -> np.ndarray:
    """Daily regime forward-filled to 1h with 1-day lag (no lookahead)."""
    cache_key = (token, len(idx_1h), idx_1h[0], idx_1h[-1])
    if cache_key in _aligned_cache:
        return _aligned_cache[cache_key]

    n = len(idx_1h)
    regime_df = _load_regime(token)
    if regime_df is None:
        result = np.full(n, -1, dtype=np.int8)
        _aligned_cache[cache_key] = result
        return result

    shifted = regime_df["state"].shift(1)
    aligned = shifted.reindex(idx_1h.normalize(), method="ffill")
    aligned.index = idx_1h
    result = aligned.fillna(-1).values.astype(np.int8)
    _aligned_cache[cache_key] = result
    return result


def _get_regime_prev_aligned(token: str, idx_1h: pd.DatetimeIndex) -> np.ndarray:
    """Previous day's regime (2-day lag) for detecting transitions."""
    cache_key = ("prev", token, len(idx_1h), idx_1h[0], idx_1h[-1])
    if cache_key in _aligned_cache:
        return _aligned_cache[cache_key]

    n = len(idx_1h)
    regime_df = _load_regime(token)
    if regime_df is None:
        result = np.full(n, -1, dtype=np.int8)
        _aligned_cache[cache_key] = result
        return result

    shifted = regime_df["state"].shift(2)
    aligned = shifted.reindex(idx_1h.normalize(), method="ffill")
    aligned.index = idx_1h
    result = aligned.fillna(-1).values.astype(np.int8)
    _aligned_cache[cache_key] = result
    return result


# ======================================================================
#  Strategy Function
# ======================================================================

def strategy(ctx: StrategyContext) -> StrategyResult:
    """Breakout — entries on consolidation-to-trend transitions."""
    close = ctx.ind_1h['close']
    volume = ctx.ind_1h['volume']
    n = len(close)
    ticker = ctx.ticker

    # Load regimes
    regime = _get_regime_aligned(ticker, ctx.idx_1h)
    regime_prev = _get_regime_prev_aligned(ticker, ctx.idx_1h)

    # Detect transitions: consolidation (0) -> bull (1) or bear (2)
    transition_to_bull = (regime_prev == 0) & (regime == 1)
    transition_to_bear = (regime_prev == 0) & (regime == 2)

    # Expand transition signal to entry window (72 bars = 3 days)
    bull_window = pd.Series(transition_to_bull.astype(np.float64)).rolling(
        ENTRY_WINDOW_BARS, min_periods=1).max().values > 0
    bear_window = pd.Series(transition_to_bear.astype(np.float64)).rolling(
        ENTRY_WINDOW_BARS, min_periods=1).max().values > 0

    # Confirmation filters on 1h bars
    vol_ma20 = rolling_mean(volume, 20)
    vol_confirm = volume > (VOL_CONFIRM_MULT * vol_ma20)

    sma = rolling_mean(close, BB_PERIOD)
    std = rolling_std(close, BB_PERIOD)
    bb_upper = sma + BB_STD * std
    bb_lower = sma - BB_STD * std

    # Long: in bull transition window + volume confirm + close > BB upper
    long_signal = bull_window & vol_confirm & (close > bb_upper)
    # Short: in bear transition window + volume confirm + close < BB lower
    short_signal = bear_window & vol_confirm & (close < bb_lower)

    # Warmup
    long_signal[:WARMUP] = False
    short_signal[:WARMUP] = False

    entry = long_signal | short_signal
    direction = np.where(long_signal, 1, np.where(short_signal, -1, 0)).astype(np.int8)

    # Conviction: higher volume = higher conviction
    conviction = np.zeros(n, dtype=np.float64)
    vol_ratio = volume / np.maximum(vol_ma20, 1e-10)
    conviction[entry] = np.clip(vol_ratio[entry] / 3.0, 0.3, 1.0)

    return StrategyResult(
        entry_mask=entry,
        direction=direction,
        market_type=MARKET,
        leverage=LEVERAGE,
        stop_mult=STOP_MULT,
        trail_mult=TRAIL_MULT,
        target_mult=TARGET_MULT,
        no_stop_bars=NO_STOP_BARS,
        min_hold=MIN_HOLD,
        max_hold=MAX_HOLD,
        edge=0.35,
        name='s527_breakout',
        conviction_score=conviction,
    )
