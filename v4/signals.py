"""V4 Portfolio Backtest — Signal precomputation.

Follows backtest_funds.py pattern: _build_context() -> strategy_fn() -> extract arrays.
Walk-forward masking applied during precomputation (matches v3/portfolio.py:152-169).
"""
from __future__ import annotations

import copy
import inspect
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from .config import PortfolioConfig, StrategySpec

_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from v4.engine import Engine, MarketType, _load_strategy_fn, _load_strategy_required_plugins, _load_strategy_required_indicator_groups
from v4.universe import get_all_tradeable
from v4.data_loader import load_token_data, load_token_data_cached, discover_tokens_from_data, infer_data_end_date as _infer_end


DATA_DIR = str(_project_root / "data")


@dataclass
class TokenSignals:
    """Lightweight signal arrays for one token under one strategy."""
    token: str
    strategy_id: str
    n_bars: int
    timestamps: np.ndarray        # datetime64
    entry_mask: np.ndarray        # bool (walk-forward + liquidity masked)
    direction: np.ndarray         # int8
    close: np.ndarray             # spot close (primary market)
    high: np.ndarray
    low: np.ndarray
    atr: np.ndarray
    rolling_adv: np.ndarray
    regime: np.ndarray
    funding_1h: np.ndarray        # zeros for spot
    exit_regimes: set
    # Trade params (per-bar or scalar)
    stop_mult: np.ndarray
    trail_mult: np.ndarray
    target_mult: float
    no_stop_bars: int
    min_hold: int
    max_hold: int
    edge: float
    size_multiplier: np.ndarray
    cap_multiplier: np.ndarray
    leverage: np.ndarray
    max_trade_pct: float          # additional sizing cap (0 = disabled)
    conviction_score: Optional[np.ndarray] = None  # per-bar [0,1] signal strength for entry prioritization
    trail_schedule: Optional[np.ndarray] = None
    time_trail_schedule: Optional[np.ndarray] = None
    max_trail_mult: Optional[np.ndarray] = None
    # Funding-aware exit
    funding_exit_threshold: float = 0.0
    # Partial profit-taking
    partial_tp_atr: float = 0.0
    partial_tp_pct: float = 0.5
    partial_tp_trail: float = 1.5
    # Breakeven ratchet (0 = disabled; strategies must explicitly opt in)
    breakeven_atr: float = 0.0
    # Chandelier stop: trail from highest-high over N-bar lookback window (0 = disabled)
    chandelier_lookback: int = 0
    # Regime-conditional target: tighter TP in bear regimes (0 = disabled, use target_mult)
    bear_target_mult: float = 0.0
    # Regime-conditional max hold: shorter hold in DOWNTREND (0 = use max_hold)
    bear_max_hold: int = 0
    # Configurable exit constants
    regime_exit_min_bars: int = 6
    convex_bar_thresholds: tuple = (48, 12)
    convex_multipliers: tuple = (2.0, 1.5, 0.3)
    # Exit mode fields
    convex_exit: bool = False
    rsi: Optional[np.ndarray] = None
    rsi_exit_level: float = 999.0
    mean_target_vals: Optional[np.ndarray] = None
    # SMA trailing stop: per-bar SMA values (None = disabled)
    sma_trail_vals: Optional[np.ndarray] = None
    # Limit entry price: per-bar limit price (None = market at close)
    entry_limit_price: Optional[np.ndarray] = None
    # Armed entry levels: per-bar watch price for real-time cross detection (None = not armed)
    # Unlike entry_limit_price (set on cross bars), armed_levels are set on PRE-cross bars
    armed_levels: Optional[np.ndarray] = None     # float64, NaN = not armed
    armed_direction: Optional[np.ndarray] = None  # int8, 0 = not armed, 1 = long, -1 = short
    # Combined strategy (spot+perp)
    is_combined: bool = False
    secondary_entry_mask: Optional[np.ndarray] = None
    secondary_direction: Optional[np.ndarray] = None
    secondary_leverage: float = 1.0
    capital_split: float = 0.5
    sec_stop_mult: Optional[float] = None
    sec_trail_mult: Optional[float] = None
    sec_target_mult: Optional[float] = None
    sec_no_stop_bars: Optional[int] = None
    sec_min_hold: Optional[int] = None
    sec_max_hold: Optional[int] = None
    is_perp_primary: bool = False
    is_perp_secondary: bool = False
    # Separate perp arrays (prices differ from spot due to basis)
    perp_close: Optional[np.ndarray] = None
    perp_high: Optional[np.ndarray] = None
    perp_low: Optional[np.ndarray] = None
    perp_atr: Optional[np.ndarray] = None
    perp_rolling_adv: Optional[np.ndarray] = None
    perp_funding_1h: Optional[np.ndarray] = None
    # Precomputed funding z-score (rolling 168h window, for pump filter)
    funding_zscore: Optional[np.ndarray] = None
    # Per-bar venue routing (adaptive spot/perp strategies)
    per_bar_is_perp: Optional[np.ndarray] = None  # bool array: True=perp, False=spot
    # Extended bar-level data for custom exit handlers (Optional, None = unavailable)
    volume: Optional[np.ndarray] = None       # hourly volume (from ctx.ind_1h)
    vol_20: Optional[np.ndarray] = None       # 20-period rolling volatility
    ret_1h: Optional[np.ndarray] = None       # 1-hour log return
    # Signal diagnostic counters (populated during precomputation)
    raw_entry_count: int = 0          # before liquidity mask
    post_liquidity_count: int = 0     # after liquidity mask
    post_walkforward_count: int = 0   # after WF mask


def _rolling_funding_zscore(funding: np.ndarray, lookback: int = 168) -> np.ndarray:
    """Precompute rolling funding z-score (vectorized, replaces per-bar inline).

    Uses ddof=0 (population std) to match the original inline code which used
    np.std() (ddof=0 by default). Returns NaN for bars with insufficient data.
    """
    n = len(funding)
    zscore = np.full(n, np.nan, dtype=np.float64)
    if n < 24:
        return zscore
    # Use pandas for efficient rolling computation with ddof=0 (population std)
    # to match the original inline np.std() which defaults to ddof=0
    s = pd.Series(funding.astype(np.float64))
    rmean = s.rolling(window=lookback, min_periods=24).mean()
    rstd = s.rolling(window=lookback, min_periods=24).std(ddof=0)
    valid = rstd > 0
    zscore[valid] = ((s[valid] - rmean[valid]) / rstd[valid]).values
    return zscore


def _to_array(val, n: int) -> np.ndarray:
    """Convert scalar or array to numpy array of length n (float32 to save memory)."""
    if isinstance(val, np.ndarray):
        return val[:n].astype(np.float32)
    return np.full(n, float(val) if val is not None else 0.0, dtype=np.float32)


def _copy_f32(arr: np.ndarray, n: int) -> np.ndarray:
    """Slice and copy to float32 — breaks numpy view references to free source data."""
    return arr[:n].astype(np.float32, copy=True)


def _apply_walk_forward_mask(
    entry_mask: np.ndarray,
    train_bars: int,
    recal_bars: int,
    purge_bars: int,
) -> np.ndarray:
    """Apply walk-forward masking (matches v3/portfolio.py:152-169).

    Per-token relative to token data start:
    - First train_bars: all entries masked
    - Every recal_bars after: purge_bars window masked
    """
    masked = entry_mask.copy()
    n = len(masked)
    masked[:train_bars] = False

    recal_point = train_bars
    while recal_point < n:
        purge_end = min(recal_point + purge_bars, n)
        masked[recal_point:purge_end] = False
        recal_point += recal_bars

    return masked


def infer_data_end_date(market: str = "combined") -> pd.Timestamp:
    """Get the latest timestamp across historical + live data.

    Delegates to v4.data_loader which checks both 1h_cache and live directories.
    """
    return _infer_end(market=market, data_dir=DATA_DIR)


def discover_tokens(market: str = "combined") -> list[str]:
    """Find tokens with data for the given market type.

    Scans both historical (1h_cache) and live buffer directories.
    Combined strategies require both spot and perp data.
    Single-market strategies use corresponding market only.
    """
    if market == "combined":
        spot = discover_tokens_from_data("spot", data_dir=DATA_DIR)
        perp = discover_tokens_from_data("perp", data_dir=DATA_DIR)
        return sorted(spot & perp)
    else:
        return sorted(discover_tokens_from_data(market, data_dir=DATA_DIR))


def precompute_strategy_signals(
    strategy_spec: StrategySpec,
    tokens: list[str],
    config: PortfolioConfig,
    months: int,
    end_date: Optional[pd.Timestamp] = None,
    live_bar: int = -1,
    hist_cache: dict | None = None,
    eng_spot: Optional[Engine] = None,
    eng_perp: Optional[Engine] = None,
) -> dict[str, TokenSignals]:
    """Precompute signal arrays for one strategy across all tokens.

    Args:
        end_date: Explicit end date for the data window. If None, uses
                  pd.Timestamp.now() (appropriate for live trading only).
                  For backtesting, pass infer_data_end_date() for reproducibility.
        live_bar: If >= 0, bars at this index or later are preserved during
                  1m entry resolution when 1m data is missing (paper mode).
        eng_spot: Optional shared Engine instance for spot market (enables
                  context caching across strategies). Creates fresh if None.
        eng_perp: Optional shared Engine instance for perp market. Creates fresh if None.

    Follows backtest_funds.py pattern:
      _build_context() -> strategy_fn() -> extract arrays -> walk-forward mask
    """
    # Dispatch to portfolio adapter for Class B strategies
    if strategy_spec.strategy_type == "portfolio":
        from .portfolio_signals import precompute_portfolio_signals
        return precompute_portfolio_signals(strategy_spec, tokens, config, months, end_date, live_bar=live_bar, hist_cache=hist_cache)

    strategy_fn = _load_strategy_fn(strategy_spec.strategy_id)
    is_single_ctx = len(inspect.signature(strategy_fn).parameters) == 1
    is_combined = strategy_spec.market == "combined"

    # Use shared engines if provided, otherwise create fresh ones
    _use_ctx_cache = eng_spot is not None or eng_perp is not None
    if eng_spot is None:
        eng_spot = Engine(data_dir=DATA_DIR, market="spot", capital=config.capital, exchange=config.exchange)
    if eng_perp is None:
        eng_perp = Engine(data_dir=DATA_DIR, market="perp", capital=config.capital, exchange=config.exchange)

    if not _use_ctx_cache:
        # Wire plugin opt-in from strategy module (only when creating fresh engines)
        req_plugins = _load_strategy_required_plugins(strategy_spec.strategy_id)
        eng_spot._required_plugins = req_plugins
        eng_perp._required_plugins = req_plugins
        # Wire selective indicator groups
        req_groups = _load_strategy_required_indicator_groups(strategy_spec.strategy_id)
        eng_spot._required_indicator_groups = req_groups
        eng_perp._required_indicator_groups = req_groups

    def _build_ctx(eng, token, df, **kwargs):
        """Build context, using external cache for shared engines.

        Returns a shallow copy on cache hit to prevent cross-strategy
        contamination (e.g. regime_params mutating regime_1h in-place).
        """
        if _use_ctx_cache:
            # Include last timestamp as cheap integrity check — catches
            # same-length DataFrames with different content.
            last_ts = df.index[-1] if len(df) > 0 else None
            key = (token, len(df), last_ts)
            cached = eng._context_cache.get(key)
            if cached is not None:
                ctx_copy = copy.copy(cached)
                # Shallow-copy mutable dicts so strategy mutations don't
                # contaminate the cached instance (e.g. ctx.custom writes).
                ctx_copy.custom = dict(cached.custom)
                ctx_copy.ind_1h = dict(cached.ind_1h)
                ctx_copy.ind_4h = dict(cached.ind_4h)
                ctx_copy.ind_d = dict(cached.ind_d)
                return ctx_copy
            ctx = eng._build_context(token, df, **kwargs)
            if ctx is not None:
                eng._context_cache[key] = ctx
            return ctx
        return eng._build_context(token, df, **kwargs)

    results: dict[str, TokenSignals] = {}

    for token in tokens:
        try:
            # Load data via data_loader (merges historical + live buffer)
            df_spot_full = load_token_data_cached(token, "spot", hist_cache=hist_cache, data_dir=DATA_DIR, max_rows=config.cache_max_rows)
            df_perp_full = load_token_data_cached(token, "perp", hist_cache=hist_cache, data_dir=DATA_DIR, max_rows=config.cache_max_rows)

            if is_combined:
                if df_spot_full is None or df_perp_full is None:
                    continue
            elif strategy_spec.market == "spot":
                if df_spot_full is None:
                    continue
            else:  # perp
                if df_perp_full is None:
                    continue

            # Compute cutoff for the simulation window.  We load extra
            # warm-up history before the cutoff so indicators (liquidity
            # burn-in 90d, regime expanding percentiles 60d, rolling ADV 30d,
            # EMAs) are properly initialized.  Arrays are trimmed to the
            # cutoff AFTER context building.
            #
            # Warm-up budget: 180 days covers all burn-in periods with margin.
            # This avoids loading full 4-5yr parquets which cause OOM.
            WARMUP_DAYS = 180
            anchor = end_date if end_date is not None else pd.Timestamp.now("UTC").tz_localize(None)
            # Trading should start at anchor - months. Cutoff must be
            # train_bars hours earlier so walk-forward mask aligns exactly.
            trade_start = anchor - pd.DateOffset(months=months)
            if config.skip_walk_forward:
                cutoff = trade_start  # no training window needed
            else:
                cutoff = trade_start - pd.DateOffset(hours=int(config.train_bars))
            load_from = cutoff - pd.DateOffset(days=WARMUP_DAYS)

            df_spot = None
            df_perp = None
            ctx_spot = None
            ctx_perp = None

            if df_spot_full is not None:
                df_spot = df_spot_full[df_spot_full.index >= load_from]
                if end_date is not None:
                    df_spot = df_spot[df_spot.index <= end_date]
            if df_perp_full is not None:
                df_perp = df_perp_full[df_perp_full.index >= load_from]
                if end_date is not None:
                    df_perp = df_perp[df_perp.index <= end_date]

            if is_combined:
                if df_spot is None or df_perp is None or len(df_spot) < 500 or len(df_perp) < 500:
                    continue
                # Align spot and perp to common date range (matches v3/engine.py:1685-1688).
                # Without this, bar 0 of spot and bar 0 of perp can be years apart,
                # producing phantom basis values (e.g., 2020 spot vs 2022 perp).
                # Use index intersection to ensure bar-for-bar timestamp alignment;
                # simple date-range slicing leaves different bar counts when one
                # series has gaps the other doesn't (affected 35/95 tokens).
                common_idx = df_spot.index.intersection(df_perp.index)
                df_spot = df_spot.reindex(common_idx)
                df_perp = df_perp.reindex(common_idx)
                if len(df_spot) < 500 or len(df_perp) < 500:
                    continue
                ctx_spot = _build_ctx(eng_spot, token, df_spot, min_bars=210, market_override="spot")
                ctx_perp = _build_ctx(eng_perp, token, df_perp, min_bars=210, market_override="perp")
                if ctx_spot is None or ctx_perp is None:
                    continue
            elif strategy_spec.market == "spot":
                if df_spot is None or len(df_spot) < 500:
                    continue
                ctx_spot = _build_ctx(eng_spot, token, df_spot, min_bars=210, market_override="spot")
                if ctx_spot is None:
                    continue
            else:  # perp
                if df_perp is None or len(df_perp) < 500:
                    continue
                ctx_perp = _build_ctx(eng_perp, token, df_perp, min_bars=210, market_override="perp")
                if ctx_perp is None:
                    continue

            # Regime injection: patch ctx.regime_1h BEFORE strategy call
            if strategy_spec.regime_params is not None:
                from v4.engine import detect_daily_regime, _ema, _align_higher_to_lower
                from v4.config import RegimeConfig
                rc = RegimeConfig(**strategy_spec.regime_params)
                primary_ctx = ctx_spot if ctx_spot is not None else ctx_perp
                custom_regime_d = detect_daily_regime(
                    primary_ctx.ind_d,
                    adx_threshold=rc.adx_threshold,
                    crisis_mult=rc.crisis_mult,
                    quiet_mult=rc.quiet_mult,
                    ema_pair=rc.ema_pair,
                    min_periods=rc.min_periods,
                )
                # Shift by 1 day (avoid look-ahead) and align to 1h
                custom_regime_d_shifted = np.roll(custom_regime_d, 1)
                custom_regime_d_shifted[0] = 3  # RANGE default for first bar
                custom_regime_1h = _align_higher_to_lower(
                    primary_ctx.idx_d, custom_regime_d_shifted.astype(float), primary_ctx.idx_1h
                )
                custom_regime_1h = np.nan_to_num(custom_regime_1h, nan=3).astype(np.int8)
                if ctx_spot is not None:
                    ctx_spot.regime_1h = custom_regime_1h
                if ctx_perp is not None:
                    ctx_perp.regime_1h = custom_regime_1h

            # Call strategy
            if is_single_ctx:
                if strategy_spec.market == "perp":
                    sr = strategy_fn(ctx_perp)
                else:
                    sr = strategy_fn(ctx_spot)
            else:
                sr = strategy_fn(ctx_spot, ctx_perp)

            # Primary context for array lengths
            primary_ctx = ctx_spot if ctx_spot is not None else ctx_perp
            n = len(primary_ctx.ind_1h["close"])

            # Determine safe length across all arrays
            n_safe = n
            if ctx_spot is not None:
                n_safe = min(n_safe, len(ctx_spot.idx_1h), len(ctx_spot.regime_1h))
            if ctx_perp is not None:
                n_safe = min(n_safe, len(ctx_perp.ind_1h["close"]))
            n_safe = min(n_safe, len(sr.entry_mask))

            # Extract primary market arrays (copy to float32 to break view refs + save memory)
            if ctx_spot is not None:
                p_close = _copy_f32(ctx_spot.ind_1h["close"], n_safe)
                p_high = _copy_f32(ctx_spot.ind_1h["high"], n_safe)
                p_low = _copy_f32(ctx_spot.ind_1h["low"], n_safe)
                p_atr = _copy_f32(ctx_spot.ind_1h["atr"], n_safe)
                p_adv = _copy_f32(ctx_spot.rolling_adv, n_safe) if ctx_spot.rolling_adv is not None else np.full(n_safe, 5_000_000.0, dtype=np.float32)
                p_regime = ctx_spot.regime_1h[:n_safe].copy()
                p_funding = np.zeros(n_safe, dtype=np.float32)
                timestamps = ctx_spot.idx_1h[:n_safe].copy()
                _ind = ctx_spot.ind_1h
                p_rsi = _copy_f32(_ind["rsi"], n_safe) if "rsi" in _ind else None
                p_volume = _copy_f32(_ind["volume"], n_safe) if "volume" in _ind else None
                p_vol_20 = _copy_f32(_ind["vol_20"], n_safe) if "vol_20" in _ind else None
                p_ret_1h = _copy_f32(_ind["ret_1"], n_safe) if "ret_1" in _ind else None
            else:
                p_close = _copy_f32(ctx_perp.ind_1h["close"], n_safe)
                p_high = _copy_f32(ctx_perp.ind_1h["high"], n_safe)
                p_low = _copy_f32(ctx_perp.ind_1h["low"], n_safe)
                p_atr = _copy_f32(ctx_perp.ind_1h["atr"], n_safe)
                p_adv = _copy_f32(ctx_perp.rolling_adv, n_safe) if ctx_perp.rolling_adv is not None else np.full(n_safe, 5_000_000.0, dtype=np.float32)
                p_regime = ctx_perp.regime_1h[:n_safe].copy()
                p_funding = _copy_f32(ctx_perp.funding_1h, n_safe) if ctx_perp.funding_1h is not None else np.zeros(n_safe, dtype=np.float32)
                timestamps = ctx_perp.idx_1h[:n_safe].copy()
                _ind = ctx_perp.ind_1h
                p_rsi = _copy_f32(_ind["rsi"], n_safe) if "rsi" in _ind else None
                p_volume = _copy_f32(_ind["volume"], n_safe) if "volume" in _ind else None
                p_vol_20 = _copy_f32(_ind["vol_20"], n_safe) if "vol_20" in _ind else None
                p_ret_1h = _copy_f32(_ind["ret_1"], n_safe) if "ret_1" in _ind else None

            # Entry mask with liquidity masking (walk-forward applied after trim)
            raw_count = int(sr.entry_mask[:n_safe].sum())  # before liquidity AND
            entry_mask = sr.entry_mask[:n_safe].copy()
            if ctx_spot is not None and ctx_spot.liquidity_mask is not None:
                entry_mask = entry_mask & ctx_spot.liquidity_mask[:n_safe]
            elif ctx_perp is not None and ctx_perp.liquidity_mask is not None:
                entry_mask = entry_mask & ctx_perp.liquidity_mask[:n_safe]
            post_liq_count = int(entry_mask.sum())  # after liquidity mask

            # Combined fields
            sec_entry = None
            sec_dir = None
            perp_close_arr = None
            perp_high_arr = None
            perp_low_arr = None
            perp_atr_arr = None
            perp_adv_arr = None
            perp_funding_arr = None
            is_perp_primary = False
            is_perp_secondary = False

            token_is_combined = is_combined  # per-token copy; never mutate is_combined
            if token_is_combined and ctx_perp is not None:
                if sr.secondary_entry_mask is None or len(sr.secondary_entry_mask) == 0:
                    import warnings
                    warnings.warn(
                        f"Strategy '{strategy_spec.strategy_id}' running in combined mode for {token} "
                        f"but produced no secondary_entry_mask. Falling back to single-leg mode. "
                        f"Use --market spot or --market perp if this strategy doesn't support combined.",
                        stacklevel=2,
                    )
                    token_is_combined = False
            if token_is_combined and ctx_perp is not None:
                sec_entry = sr.secondary_entry_mask[:n_safe].copy()
                if ctx_perp.liquidity_mask is not None:
                    sec_entry = sec_entry & ctx_perp.liquidity_mask[:n_safe]
                sec_dir = sr.secondary_direction[:n_safe].copy() if sr.secondary_direction is not None else np.ones(n_safe, dtype=np.int8)

                perp_close_arr = _copy_f32(ctx_perp.ind_1h["close"], n_safe)
                perp_high_arr = _copy_f32(ctx_perp.ind_1h["high"], n_safe)
                perp_low_arr = _copy_f32(ctx_perp.ind_1h["low"], n_safe)
                perp_atr_arr = _copy_f32(ctx_perp.ind_1h["atr"], n_safe)
                perp_adv_arr = _copy_f32(ctx_perp.rolling_adv, n_safe) if ctx_perp.rolling_adv is not None else np.full(n_safe, 5_000_000.0, dtype=np.float32)
                perp_funding_arr = _copy_f32(ctx_perp.funding_1h, n_safe) if ctx_perp.funding_1h is not None else np.zeros(n_safe, dtype=np.float32)

                # Determine which leg is perp
                is_perp_primary = (sr.market_type == MarketType.PERP) if not isinstance(sr.market_type, np.ndarray) else False
                is_perp_secondary = (getattr(sr, 'secondary_market_type', MarketType.PERP) == MarketType.PERP)
            elif strategy_spec.market == "perp":
                is_perp_primary = True

            # Per-bar venue routing: detect per-bar market_type (adaptive spot/perp)
            per_bar_is_perp_arr = None
            ts_is_combined = token_is_combined
            if token_is_combined and isinstance(sr.market_type, np.ndarray):
                # Strategy returns per-bar market routing (SPOT for longs, PERP for shorts)
                # Use single-leg path with per-bar fee/funding/price routing
                per_bar_is_perp_arr = (sr.market_type[:n_safe] == MarketType.PERP).astype(bool)
                ts_is_combined = False  # single-leg path, NOT atomic two-leg
                is_perp_primary = False  # per_bar_is_perp overrides this

            # Mean target values
            mean_target = None
            if sr.mean_target_vals is not None:
                mean_target = _copy_f32(sr.mean_target_vals, n_safe)

            # SMA trail values
            sma_trail = None
            if getattr(sr, 'sma_trail_vals', None) is not None:
                sma_trail = _copy_f32(sr.sma_trail_vals, n_safe)

            # Entry limit price
            entry_limit = _copy_f32(sr.entry_limit_price, n_safe) if getattr(sr, 'entry_limit_price', None) is not None else None

            # Armed entry levels (pre-cross BB watch prices for real-time detection)
            armed_lvl = sr.armed_levels[:n_safe].copy() if getattr(sr, 'armed_levels', None) is not None else None
            armed_dir = sr.armed_direction[:n_safe].copy() if getattr(sr, 'armed_direction', None) is not None else None

            # Trail schedule
            trail_sched = None
            if sr.trail_schedule is not None:
                trail_sched = np.asarray(sr.trail_schedule, dtype=np.float32)

            # Time-based trail schedule
            time_trail_sched = None
            if getattr(sr, 'time_trail_schedule', None) is not None:
                time_trail_sched = np.asarray(sr.time_trail_schedule, dtype=np.float32)

            # Max trail mult
            max_trail = None
            if sr.max_trail_mult is not None:
                max_trail = np.asarray(sr.max_trail_mult, dtype=np.float32)[:n_safe].copy()

            # Extract all sr fields into locals before freeing sr/context memory
            sr_direction = np.asarray(sr.direction[:n_safe], dtype=np.int8)
            sr_exit_regimes = sr.exit_regimes
            sr_stop_mult = _to_array(sr.stop_mult, n_safe)
            sr_trail_mult = _to_array(sr.trail_mult, n_safe)
            sr_target_mult = float(sr.target_mult)
            sr_no_stop_bars = int(sr.no_stop_bars)
            sr_min_hold = int(sr.min_hold)
            sr_max_hold = int(sr.max_hold)
            sr_edge = float(sr.edge)
            sr_size_mult = _to_array(sr.size_multiplier, n_safe)
            sr_cap_mult = _to_array(sr.cap_multiplier, n_safe)
            sr_leverage = _to_array(sr.leverage, n_safe)
            sr_max_trade_pct = float(sr.max_trade_pct)
            sr_convex_exit = sr.convex_exit
            sr_rsi_exit_level = float(sr.rsi_exit_level)
            sr_funding_exit_threshold = float(getattr(sr, 'funding_exit_threshold', 0.0))
            sr_partial_tp_atr = float(getattr(sr, 'partial_tp_atr', 0.0))
            sr_partial_tp_pct = float(getattr(sr, 'partial_tp_pct', 0.5))
            sr_partial_tp_trail = float(getattr(sr, 'partial_tp_trail', 1.5))
            sr_breakeven_atr = float(getattr(sr, 'breakeven_atr', 0.0))
            sr_chandelier_lookback = int(getattr(sr, 'chandelier_lookback', 0))
            sr_regime_exit_min_bars = int(getattr(sr, 'regime_exit_min_bars', 6))
            sr_convex_bar_thresholds = tuple(getattr(sr, 'convex_bar_thresholds', (48, 12)))
            sr_convex_multipliers = tuple(getattr(sr, 'convex_multipliers', (2.0, 1.5, 0.3)))
            sr_bear_target_mult = float(getattr(sr, 'bear_target_mult', 0.0))
            sr_bear_max_hold = int(getattr(sr, 'bear_max_hold', 0))
            # Conviction score: use explicit if provided, else derive from size_multiplier.
            # This auto-derivation eliminates seed sensitivity in ranked conviction mode
            # by giving each entry a differentiating conviction value based on its sizing signal.
            sr_conviction = None
            if getattr(sr, 'conviction_score', None) is not None:
                sr_conviction = _to_array(sr.conviction_score, n_safe)
            elif sr.size_multiplier is not None:
                sm = _to_array(sr.size_multiplier, n_safe)
                sm_max = float(np.nanmax(sm)) if len(sm) > 0 else 1.0
                sr_conviction = sm / max(sm_max, 1e-10)  # normalize to [0, 1]
            sr_sec_leverage = float(getattr(sr, 'secondary_leverage', 1.0))
            sr_capital_split = float(getattr(sr, 'capital_split', 0.5))
            sr_sec_stop = float(sr.secondary_stop_mult) if sr.secondary_stop_mult is not None else None
            sr_sec_trail = float(sr.secondary_trail_mult) if sr.secondary_trail_mult is not None else None
            sr_sec_target = float(sr.secondary_target_mult) if sr.secondary_target_mult is not None else None
            sr_sec_no_stop = int(sr.secondary_no_stop_bars) if sr.secondary_no_stop_bars is not None else None
            sr_sec_min_hold = int(sr.secondary_min_hold) if sr.secondary_min_hold is not None else None
            sr_sec_max_hold = int(sr.secondary_max_hold) if sr.secondary_max_hold is not None else None

            # Free context and dataframe memory (break refs so GC can reclaim)
            ctx_spot = ctx_perp = df_spot = df_perp = sr = None

            # Trim arrays to the simulation window.  Indicators were computed
            # on full history so burn-in / expanding stats are correct; we now
            # discard the pre-cutoff portion that was only needed for warm-up.
            ts_arr = timestamps.values if hasattr(timestamps, 'values') else np.asarray(timestamps)
            cutoff_ns = np.datetime64(cutoff)
            trim_start = int(np.searchsorted(ts_arr, cutoff_ns))

            if trim_start > 0:
                s = trim_start  # shorthand
                timestamps = timestamps[s:]
                p_close = p_close[s:]
                p_high = p_high[s:]
                p_low = p_low[s:]
                p_atr = p_atr[s:]
                p_adv = p_adv[s:]
                p_regime = p_regime[s:]
                p_funding = p_funding[s:]
                if p_rsi is not None:
                    p_rsi = p_rsi[s:]
                if p_volume is not None:
                    p_volume = p_volume[s:]
                if p_vol_20 is not None:
                    p_vol_20 = p_vol_20[s:]
                if p_ret_1h is not None:
                    p_ret_1h = p_ret_1h[s:]
                entry_mask = entry_mask[s:]
                sr_direction = sr_direction[s:]
                sr_stop_mult = sr_stop_mult[s:]
                sr_trail_mult = sr_trail_mult[s:]
                sr_size_mult = sr_size_mult[s:]
                sr_cap_mult = sr_cap_mult[s:]
                sr_leverage = sr_leverage[s:]
                if sr_conviction is not None:
                    sr_conviction = sr_conviction[s:]
                if mean_target is not None:
                    mean_target = mean_target[s:]
                if sma_trail is not None:
                    sma_trail = sma_trail[s:]
                if entry_limit is not None:
                    entry_limit = entry_limit[s:]
                if armed_lvl is not None:
                    armed_lvl = armed_lvl[s:]
                if armed_dir is not None:
                    armed_dir = armed_dir[s:]
                if max_trail is not None:
                    max_trail = max_trail[s:]
                if sec_entry is not None:
                    sec_entry = sec_entry[s:]
                if sec_dir is not None:
                    sec_dir = sec_dir[s:]
                if perp_close_arr is not None:
                    perp_close_arr = perp_close_arr[s:]
                    perp_high_arr = perp_high_arr[s:]
                    perp_low_arr = perp_low_arr[s:]
                    perp_atr_arr = perp_atr_arr[s:]
                    perp_adv_arr = perp_adv_arr[s:]
                    perp_funding_arr = perp_funding_arr[s:]
                if per_bar_is_perp_arr is not None:
                    per_bar_is_perp_arr = per_bar_is_perp_arr[s:]
                n_safe = n_safe - s

            # Skip token if trimmed window is too short
            if not config.skip_walk_forward:
                if n_safe < config.train_bars + config.purge_bars + 100:
                    continue
            else:
                if n_safe < 200:
                    continue

            # Walk-forward masking (applied on trimmed arrays so training
            # window starts from the cutoff, not from the token's first bar)
            if not config.skip_walk_forward:
                entry_mask = _apply_walk_forward_mask(
                    entry_mask, config.train_bars, config.recal_bars, config.purge_bars,
                )
                if sec_entry is not None:
                    sec_entry = _apply_walk_forward_mask(
                        sec_entry, config.train_bars, config.recal_bars, config.purge_bars,
                    )
            post_wf_count = int(entry_mask.sum())  # after WF mask (= post_liq if WF skipped)

            # Precompute funding z-score for pump filter (avoids per-bar inline computation)
            _funding_zscore = None
            if p_funding is not None and len(p_funding) > 0:
                _funding_zscore = _rolling_funding_zscore(p_funding, lookback=168)

            ts = TokenSignals(
                token=token,
                strategy_id=strategy_spec.strategy_id,
                n_bars=n_safe,
                timestamps=timestamps.values if hasattr(timestamps, 'values') else np.asarray(timestamps),
                entry_mask=entry_mask,
                direction=sr_direction,
                close=p_close,
                high=p_high,
                low=p_low,
                atr=p_atr,
                rolling_adv=p_adv,
                regime=p_regime,
                funding_1h=p_funding,
                exit_regimes=sr_exit_regimes,
                stop_mult=sr_stop_mult,
                trail_mult=sr_trail_mult,
                target_mult=sr_target_mult,
                no_stop_bars=sr_no_stop_bars,
                min_hold=sr_min_hold,
                max_hold=sr_max_hold,
                edge=sr_edge,
                size_multiplier=sr_size_mult,
                cap_multiplier=sr_cap_mult,
                leverage=sr_leverage,
                max_trade_pct=sr_max_trade_pct,
                conviction_score=sr_conviction,
                trail_schedule=trail_sched,
                time_trail_schedule=time_trail_sched,
                max_trail_mult=max_trail,
                funding_exit_threshold=sr_funding_exit_threshold,
                partial_tp_atr=sr_partial_tp_atr,
                partial_tp_pct=sr_partial_tp_pct,
                partial_tp_trail=sr_partial_tp_trail,
                breakeven_atr=sr_breakeven_atr,
                chandelier_lookback=sr_chandelier_lookback,
                bear_target_mult=sr_bear_target_mult,
                bear_max_hold=sr_bear_max_hold,
                regime_exit_min_bars=sr_regime_exit_min_bars,
                convex_bar_thresholds=sr_convex_bar_thresholds,
                convex_multipliers=sr_convex_multipliers,
                convex_exit=sr_convex_exit,
                rsi=p_rsi,
                rsi_exit_level=sr_rsi_exit_level,
                mean_target_vals=mean_target,
                sma_trail_vals=sma_trail,
                entry_limit_price=entry_limit,
                armed_levels=armed_lvl,
                armed_direction=armed_dir,
                is_combined=ts_is_combined,
                secondary_entry_mask=sec_entry,
                secondary_direction=sec_dir,
                secondary_leverage=sr_sec_leverage,
                capital_split=sr_capital_split,
                sec_stop_mult=sr_sec_stop,
                sec_trail_mult=sr_sec_trail,
                sec_target_mult=sr_sec_target,
                sec_no_stop_bars=sr_sec_no_stop,
                sec_min_hold=sr_sec_min_hold,
                sec_max_hold=sr_sec_max_hold,
                is_perp_primary=is_perp_primary,
                is_perp_secondary=is_perp_secondary,
                perp_close=perp_close_arr,
                perp_high=perp_high_arr,
                perp_low=perp_low_arr,
                perp_atr=perp_atr_arr,
                perp_rolling_adv=perp_adv_arr,
                perp_funding_1h=perp_funding_arr,
                funding_zscore=_funding_zscore,
                per_bar_is_perp=per_bar_is_perp_arr,
                volume=p_volume,
                vol_20=p_vol_20,
                ret_1h=p_ret_1h,
                raw_entry_count=raw_count,
                post_liquidity_count=post_liq_count,
                post_walkforward_count=post_wf_count,
            )
            results[token] = ts

        except Exception as e:
            print(f"  {token}: signal error - {e}")
            continue

    # Clear context caches to prevent memory leak — but only for locally-created
    # engines. Shared engines (passed via eng_spot/eng_perp params) are managed
    # by the caller (e.g., paper_engine clears after all strategies complete).
    if not _use_ctx_cache:
        eng_spot._context_cache.clear()
        eng_perp._context_cache.clear()
        del eng_spot, eng_perp

    # Paper mode: clear hist_cache between ticks to reclaim memory.
    # Mirrors portfolio_signals._load_all_contexts behavior for Class B strategies.
    if config.cache_max_rows > 0 and hist_cache is not None:
        hist_cache.clear()
        import gc
        import ctypes
        gc.collect()
        try:
            ctypes.CDLL("libc.so.6").malloc_trim(0)
        except (OSError, AttributeError):
            pass

    return results
