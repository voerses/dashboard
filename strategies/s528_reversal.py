"""
S528 — Trend Exhaustion / Reversal
====================================

Trades when HMM signals a trend is weakening — declining state probability
plus confirming technical signals.

Entry logic:
- HMM state is trending (1=bull or 2=bear)
- state_prob is DECLINING (today < 3-day avg)
- Plus one of:
    a) RSI divergence (price new high but RSI isn't, or vice versa)
    b) Volume declining (3-day avg < 10-day avg)
    c) BB squeeze starting (BB width decreasing 3+ consecutive days)
- Direction: opposite of current trend

Parameters tuned for reversal timing.
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
STOP_MULT = 5.0       # wider stop for reversals
TRAIL_MULT = 2.5
TARGET_MULT = 999.0
MAX_HOLD = 168       # 7 days
MIN_HOLD = 24        # min 1 day hold
NO_STOP_BARS = 24
WARMUP = 200

RSI_PERIOD = 14
RSI_DIVERGENCE_LOOKBACK = 48  # 2 days for divergence check
BB_PERIOD = 20
BB_STD = 2.0
MIN_CONFIRM_COUNT = 2  # require at least 2 confirming signals

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


def _get_regime_aligned(token: str, idx_1h: pd.DatetimeIndex, col: str = "state") -> np.ndarray:
    """Daily regime column forward-filled to 1h with 1-day lag."""
    cache_key = (col, token, len(idx_1h), idx_1h[0], idx_1h[-1])
    if cache_key in _aligned_cache:
        return _aligned_cache[cache_key]

    n = len(idx_1h)
    regime_df = _load_regime(token)
    if regime_df is None or col not in regime_df.columns:
        fill = -1 if col == "state" else 0.0
        result = np.full(n, fill, dtype=np.float64)
        _aligned_cache[cache_key] = result
        return result

    shifted = regime_df[col].shift(1)
    aligned = shifted.reindex(idx_1h.normalize(), method="ffill")
    aligned.index = idx_1h
    result = aligned.fillna(-1 if col == "state" else 0.0).values.astype(np.float64)
    _aligned_cache[cache_key] = result
    return result


# ======================================================================
#  RSI Computation
# ======================================================================

def _compute_rsi(close: np.ndarray, period: int = 14) -> np.ndarray:
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
    """Trend exhaustion reversal — fade weakening trends."""
    close = ctx.ind_1h['close']
    volume = ctx.ind_1h['volume']
    n = len(close)
    ticker = ctx.ticker

    # Load regime state and probability
    regime = _get_regime_aligned(ticker, ctx.idx_1h, "state").astype(np.int8)
    state_prob = _get_regime_aligned(ticker, ctx.idx_1h, "state_prob")

    # Trend masks
    is_bull = regime == 1
    is_bear = regime == 2
    is_trending = is_bull | is_bear

    # State probability declining: current < 3-day rolling avg
    prob_ma3 = pd.Series(state_prob).rolling(72, min_periods=24).mean().values  # 3 days in 1h bars
    prob_declining = state_prob < prob_ma3

    # Base condition: trending AND prob declining
    base_cond = is_trending & prob_declining

    # ----- Confirming signals -----

    # (a) RSI divergence
    rsi = _compute_rsi(close, RSI_PERIOD)
    # Price making new 24-bar high but RSI isn't (bearish divergence in bull)
    price_high_24 = pd.Series(close).rolling(RSI_DIVERGENCE_LOOKBACK, min_periods=6).max().values
    rsi_high_24 = pd.Series(rsi).rolling(RSI_DIVERGENCE_LOOKBACK, min_periods=6).max().values
    # Price at new high but RSI below its recent max
    bearish_div = (close >= price_high_24 * 0.998) & (rsi < rsi_high_24 - 3)
    # Price making new 24-bar low but RSI isn't (bullish divergence in bear)
    price_low_24 = pd.Series(close).rolling(RSI_DIVERGENCE_LOOKBACK, min_periods=6).min().values
    rsi_low_24 = pd.Series(rsi).rolling(RSI_DIVERGENCE_LOOKBACK, min_periods=6).min().values
    bullish_div = (close <= price_low_24 * 1.002) & (rsi > rsi_low_24 + 3)

    rsi_divergence = (is_bull & bearish_div) | (is_bear & bullish_div)

    # (b) Volume declining: 3-day avg < 10-day avg
    vol_ma3 = rolling_mean(volume, 72)   # 3 days in 1h
    vol_ma10 = rolling_mean(volume, 240)  # 10 days in 1h
    vol_declining = vol_ma3 < vol_ma10

    # (c) BB squeeze starting: BB width decreasing for 3+ consecutive 1h bars (72h)
    sma = rolling_mean(close, BB_PERIOD)
    std = rolling_std(close, BB_PERIOD)
    bb_width = 4 * std / np.maximum(sma, 1e-10)
    # Check if bb_width is declining over 72 bars
    bb_width_ma = pd.Series(bb_width).rolling(72, min_periods=24).mean().values
    bb_width_prev_ma = pd.Series(bb_width).shift(24).rolling(72, min_periods=24).mean().values
    bb_squeeze = bb_width_ma < bb_width_prev_ma

    # Count confirming signals — require at least MIN_CONFIRM_COUNT
    confirm_count = (rsi_divergence.astype(np.int8)
                     + vol_declining.astype(np.int8)
                     + bb_squeeze.astype(np.int8))
    confirmed = confirm_count >= MIN_CONFIRM_COUNT

    # Entry: base condition AND confirmed
    entry_raw = base_cond & confirmed

    # Direction: opposite of current trend
    # Short if bull weakening (is_bull), long if bear weakening (is_bear)
    long_raw = entry_raw & is_bear
    short_raw = entry_raw & is_bull

    # Rate-limit: first bar of day only
    dates = ctx.idx_1h.normalize()
    day_change = np.zeros(n, dtype=bool)
    day_change[0] = True
    day_change[1:] = dates[1:] != dates[:-1]
    long_signal = long_raw & day_change
    short_signal = short_raw & day_change

    # Warmup
    long_signal[:WARMUP] = False
    short_signal[:WARMUP] = False

    entry = long_signal | short_signal
    direction = np.where(long_signal, 1, np.where(short_signal, -1, 0)).astype(np.int8)

    # Conviction: based on how much prob has declined
    conviction = np.zeros(n, dtype=np.float64)
    prob_delta = prob_ma3 - state_prob  # positive when declining
    conviction[entry] = np.clip(prob_delta[entry] * 5.0, 0.2, 1.0)

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
        edge=0.25,
        name='s528_reversal',
        conviction_score=conviction,
    )
