"""
S526 — Channel Mean-Reversion in Consolidation Regime
=====================================================

Trades only when HMM regime = consolidation (state 0).

Entry logic (1h bars):
- Load daily HMM regime, forward-fill to 1h (regime known at daily close,
  applied from next day's first bar — no lookahead).
- When regime = consolidation:
    LONG:  close < BB_lower(20, 2.0) AND RSI(14) < 35
    SHORT: close > BB_upper(20, 2.0) AND RSI(14) > 65
- When regime != consolidation: NO TRADES

Parameters optimized for mean-reversion in low-volatility consolidation.
"""

import os
import numpy as np
import pandas as pd
from engine import (StrategyContext, StrategyResult, MarketType,
                    rolling_mean, rolling_std)


# ======================================================================
#  Parameters
# ======================================================================

LEVERAGE = 1.5
STOP_MULT = 4.0       # wider stop — 1h bars are noisy
TRAIL_MULT = 2.0
TARGET_MULT = 3.0     # target ~3 ATR
MAX_HOLD = 72         # 3 days — MR resolves quickly in consolidation
MIN_HOLD = 12         # minimum 12h hold
NO_STOP_BARS = 12
WARMUP = 200

BB_PERIOD = 20
BB_STD = 2.5          # wider bands = more extreme entries
RSI_PERIOD = 14
RSI_LONG_LEVEL = 28   # more extreme RSI required
RSI_SHORT_LEVEL = 72
MIN_BARS_BETWEEN = 24 # rate limit: 1 entry per day per direction

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

# {token: pd.DataFrame with date, state, state_prob}
_regime_cache: dict = {}
# {(token, n_bars, first_ts, last_ts): np.ndarray}
_aligned_cache: dict = {}


def _load_regime(token: str) -> pd.DataFrame | None:
    """Load HMM regime data for a token. Cached."""
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
    """Get daily regime forward-filled to 1h bars.

    Regime from day D is applied starting at day D+1 first bar (no lookahead).
    Returns array of states (0=consol, 1=bull, 2=bear), -1 where unavailable.
    """
    cache_key = (token, len(idx_1h), idx_1h[0], idx_1h[-1])
    if cache_key in _aligned_cache:
        return _aligned_cache[cache_key]

    n = len(idx_1h)
    regime_df = _load_regime(token)
    if regime_df is None:
        result = np.full(n, -1, dtype=np.int8)
        _aligned_cache[cache_key] = result
        return result

    # Shift regime by 1 day to avoid lookahead
    shifted = regime_df["state"].shift(1)

    # Forward-fill to 1h index
    aligned = shifted.reindex(idx_1h.normalize(), method="ffill")
    aligned.index = idx_1h
    result = aligned.fillna(-1).values.astype(np.int8)

    _aligned_cache[cache_key] = result
    return result


# ======================================================================
#  RSI Computation
# ======================================================================

def _compute_rsi(close: np.ndarray, period: int = 14) -> np.ndarray:
    """Standard RSI."""
    delta = np.diff(close, prepend=close[0])
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    avg_gain = pd.Series(gain).ewm(span=period, adjust=False).mean().values
    avg_loss = pd.Series(loss).ewm(span=period, adjust=False).mean().values
    rs = avg_gain / (avg_loss + 1e-10)
    return 100.0 - (100.0 / (1.0 + rs))


# ======================================================================
#  Strategy Function
# ======================================================================

def strategy(ctx: StrategyContext) -> StrategyResult:
    """Channel MR — only in consolidation regime."""
    close = ctx.ind_1h['close']
    n = len(close)
    ticker = ctx.ticker

    # Load HMM regime aligned to 1h
    regime = _get_regime_aligned(ticker, ctx.idx_1h)

    # Consolidation mask
    is_consolidation = regime == 0

    # Bollinger Bands on 1h
    sma = rolling_mean(close, BB_PERIOD)
    std = rolling_std(close, BB_PERIOD)
    bb_upper = sma + BB_STD * std
    bb_lower = sma - BB_STD * std

    # RSI on 1h
    rsi = _compute_rsi(close, RSI_PERIOD)

    # Entry signals — only in consolidation
    long_raw = is_consolidation & (close < bb_lower) & (rsi < RSI_LONG_LEVEL)
    short_raw = is_consolidation & (close > bb_upper) & (rsi > RSI_SHORT_LEVEL)

    # Rate-limit: only fire on first bar of each new day
    dates = ctx.idx_1h.normalize()
    day_change = np.zeros(n, dtype=bool)
    day_change[0] = True
    day_change[1:] = dates[1:] != dates[:-1]

    # Only allow entry on day boundaries when signal is present
    long_signal = long_raw & day_change
    short_signal = short_raw & day_change

    # Warmup guard
    long_signal[:WARMUP] = False
    short_signal[:WARMUP] = False

    entry = long_signal | short_signal
    direction = np.where(long_signal, 1, np.where(short_signal, -1, 0)).astype(np.int8)

    # Conviction: higher when RSI is more extreme
    conviction = np.zeros(n, dtype=np.float64)
    conviction[long_signal] = np.clip((RSI_LONG_LEVEL - rsi[long_signal]) / RSI_LONG_LEVEL, 0.3, 1.0)
    conviction[short_signal] = np.clip((rsi[short_signal] - RSI_SHORT_LEVEL) / (100 - RSI_SHORT_LEVEL), 0.3, 1.0)

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
        edge=0.30,
        name='s526_channel_mr',
        conviction_score=conviction,
    )
