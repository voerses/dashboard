"""
s120 — Taker Buy/Sell Volume Confirmation Overlay on s56
=========================================================
Class C Overlay: wraps s56 (signal-enhanced momentum) with taker volume
confirmation for position sizing.

Research basis (taker_volume_results.md):
  - net_taker_vol trend overlay: OOS Sharpe 0.372 -> 1.043 (+180%)
  - cross_token_dispersion: OOS IC=-0.138 (t=-9.35), contrarian
  - net_taker_vol = (buy - sell) / (buy + sell), normalized imbalance

Sizing logic:
  - Compute net_taker_ratio per token at each bar
  - When net taker volume confirms entry direction (positive for longs):
    boost size_multiplier by 1.8x
  - When net taker contradicts entry direction: reduce to 0.4x
  - Neutral zone (|net_taker| < threshold): pass through at 1.0x
  - Cross-token dispersion modulates: high dispersion (crowding) -> reduce

Data: binance_positioning/extended/{TOKEN}USDT_taker_buysell.parquet
  - Columns: timestamp, taker_buy_base_vol, taker_sell_vol
  - Frequency: hourly, 2025-07-01 to 2026-02-28

Base: s56_signal_enhanced_momentum (Tier A, perp, long-only momentum)
Status: EXPERIMENTAL
"""

import os
import numpy as np
import pandas as pd

from strategies.s56_signal_enhanced_momentum import strategy as base_strategy
from engine import StrategyResult

# ── Module-level data cache ──────────────────────────────────────
TAKER_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "alternative", "binance_positioning", "extended",
)

TOKENS = [
    "AAVE", "ADA", "ARB", "AVAX", "BNB", "BTC", "DOGE", "DOT",
    "ETH", "INJ", "LINK", "LTC", "NEAR", "OP", "SOL", "SUI",
    "UNI", "WIF", "XRP",
]

# Cache: {ticker: pd.Series} smoothed net taker ratio with DatetimeIndex
_taker_cache: dict = {}

# Cache: pd.Series with DatetimeIndex, cross-token dispersion of buy/sell ratio
_dispersion_cache: pd.Series = None

# Per-call alignment cache: {(ticker, n_bars): np.ndarray}
_aligned_taker_cache: dict = {}
_aligned_disp_cache: dict = {}

# ── Configuration ────────────────────────────────────────────────
# Net taker thresholds (from research: confirmation at ~60% rate OOS)
NET_TAKER_CONFIRM_THRESH = 0.02   # |net_taker| must exceed this for signal
NET_TAKER_BOOST = 1.8             # When taker confirms trend direction
NET_TAKER_REDUCE = 0.4            # When taker contradicts trend direction

# Cross-token dispersion: negative IC means high dispersion -> lower returns
# Use as a dampener: when dispersion z-score > 1 -> reduce size
DISPERSION_WINDOW = 240           # 10-day rolling window for z-score
DISPERSION_DAMPEN = 0.6           # Multiply size by this when dispersion is high

# Rolling window for smoothing net taker ratio (hours)
NET_TAKER_SMOOTH = 24             # 1-day EMA for noise reduction


def _load_taker_data():
    """Load all taker buy/sell data into cache at module level.

    Called once on first strategy invocation. Subsequent calls are no-ops.
    """
    global _taker_cache, _dispersion_cache

    if _taker_cache:
        return

    ratio_frames = {}

    for ticker in TOKENS:
        symbol = f"{ticker}USDT"
        fpath = os.path.join(TAKER_DIR, f"{symbol}_taker_buysell.parquet")
        if not os.path.exists(fpath):
            continue

        df = pd.read_parquet(fpath, columns=["timestamp", "taker_buy_base_vol", "taker_sell_vol"])
        ts = pd.to_datetime(df["timestamp"]).dt.tz_localize(None)
        df = df.set_index(ts).sort_index()

        # Pre-compute net taker ratio: (buy - sell) / (buy + sell)
        buy = df["taker_buy_base_vol"].values.astype(np.float64)
        sell = df["taker_sell_vol"].values.astype(np.float64)
        total = buy + sell
        net_ratio = np.where(total > 0, (buy - sell) / total, 0.0)

        # EMA smoothing
        s = pd.Series(net_ratio, index=df.index)
        smoothed = s.ewm(span=NET_TAKER_SMOOTH, min_periods=NET_TAKER_SMOOTH // 2).mean()

        # Buy/sell ratio for dispersion
        bsr = np.where(sell > 0, buy / sell, 1.0)
        ratio_frames[ticker] = pd.Series(bsr, index=df.index)

        _taker_cache[ticker] = smoothed

    # Cross-token dispersion: std of buy/sell ratio across tokens at each timestamp
    if ratio_frames:
        ratio_df = pd.DataFrame(ratio_frames)
        _dispersion_cache = ratio_df.std(axis=1)
        _dispersion_cache.name = "cross_token_dispersion"


def _get_net_taker_aligned(ticker: str, idx_1h: pd.DatetimeIndex) -> np.ndarray:
    """Get net taker ratio aligned to the strategy's 1h index.

    Returns array of shape (n,) with 0.0 where data is unavailable.
    Uses alignment cache keyed by (ticker, len(idx_1h)) for < 1ms repeat calls.
    """
    cache_key = (ticker, len(idx_1h))
    if cache_key in _aligned_taker_cache:
        return _aligned_taker_cache[cache_key]

    if ticker not in _taker_cache:
        result = np.zeros(len(idx_1h), dtype=np.float64)
    else:
        smoothed = _taker_cache[ticker]
        aligned = smoothed.reindex(idx_1h, method="ffill")
        result = np.nan_to_num(aligned.values.astype(np.float64), nan=0.0)

    _aligned_taker_cache[cache_key] = result
    return result


def _get_dispersion_zscore_aligned(idx_1h: pd.DatetimeIndex) -> np.ndarray:
    """Get cross-token dispersion z-score aligned to strategy's 1h index.

    Returns array of shape (n,) with 0.0 where data is unavailable.
    Uses alignment cache keyed by len(idx_1h) for < 1ms repeat calls.
    """
    cache_key = len(idx_1h)
    if cache_key in _aligned_disp_cache:
        return _aligned_disp_cache[cache_key]

    global _dispersion_cache
    if _dispersion_cache is None or _dispersion_cache.empty:
        result = np.zeros(len(idx_1h), dtype=np.float64)
    else:
        aligned = _dispersion_cache.reindex(idx_1h, method="ffill")
        vals = aligned.values.astype(np.float64)

        # Rolling z-score
        s = pd.Series(vals)
        mu = s.rolling(DISPERSION_WINDOW, min_periods=DISPERSION_WINDOW // 2).mean()
        sigma = s.rolling(DISPERSION_WINDOW, min_periods=DISPERSION_WINDOW // 2).std()
        z = ((s - mu) / sigma.clip(lower=1e-10)).values
        result = np.clip(np.nan_to_num(z, nan=0.0), -3.0, 3.0)

    _aligned_disp_cache[cache_key] = result
    return result


def strategy(ctx):
    """s56 + taker volume confirmation overlay."""
    # Ensure data is loaded (no-op after first call)
    _load_taker_data()

    # Run base strategy
    result = base_strategy(ctx)

    n = len(ctx.ind_1h["close"])
    ticker = ctx.ticker

    # ── NET TAKER CONFIRMATION ───────────────────────────────────
    net_taker = _get_net_taker_aligned(ticker, ctx.idx_1h)

    # Direction from base strategy (s56 is long-only: direction is +1)
    # For long entries: positive net_taker confirms, negative contradicts
    # For short entries: negative net_taker confirms, positive contradicts
    direction = result.direction  # per-bar array, +1 or -1

    # Compute confirmation: direction * net_taker > 0 means aligned
    # direction * net_taker > threshold -> confirm (boost)
    # direction * net_taker < -threshold -> contradict (reduce)
    signed_taker = direction * net_taker

    taker_scale = np.where(
        signed_taker > NET_TAKER_CONFIRM_THRESH,
        NET_TAKER_BOOST,
        np.where(
            signed_taker < -NET_TAKER_CONFIRM_THRESH,
            NET_TAKER_REDUCE,
            1.0,
        ),
    )

    # ── CROSS-TOKEN DISPERSION DAMPENER ──────────────────────────
    # High dispersion (z > 1) means crowding in buy/sell ratios
    # Research: IC=-0.138, contrarian -- high dispersion predicts lower returns
    disp_z = _get_dispersion_zscore_aligned(ctx.idx_1h)
    disp_scale = np.where(disp_z > 1.0, DISPERSION_DAMPEN, 1.0)

    # ── COMBINE WITH BASE SIZE MULTIPLIER ────────────────────────
    base_size = result.size_multiplier
    if isinstance(base_size, (int, float)):
        base_size = np.full(n, float(base_size), dtype=np.float64)
    else:
        base_size = np.asarray(base_size, dtype=np.float64)

    combined_size = base_size * taker_scale * disp_scale

    # Cap at same level as s56 (6.0)
    combined_size = np.minimum(combined_size, 6.0)

    return StrategyResult(
        entry_mask=result.entry_mask,
        direction=result.direction,
        stop_mult=result.stop_mult,
        trail_mult=result.trail_mult,
        target_mult=result.target_mult,
        no_stop_bars=result.no_stop_bars,
        min_hold=result.min_hold,
        max_hold=result.max_hold,
        edge=result.edge,
        exit_regimes=result.exit_regimes,
        name="s120_taker_confirm_overlay",
        max_trade_pct=result.max_trade_pct,
        trail_schedule=result.trail_schedule,
        size_multiplier=combined_size,
        cap_multiplier=result.cap_multiplier,
        breakeven_atr=0.5,
    )
