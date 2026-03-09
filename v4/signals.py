"""V4 Portfolio Backtest — Signal precomputation from v3 contexts.

Follows backtest_funds.py pattern: _build_context() -> strategy_fn() -> extract arrays.
Walk-forward masking applied during precomputation (matches v3/portfolio.py:152-169).
"""
from __future__ import annotations

import inspect
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from .config import PortfolioConfig, StrategySpec

# Ensure v3 is importable
_project_root = Path(__file__).resolve().parent.parent
_v3_dir = str(_project_root / "v3")
if _v3_dir not in sys.path:
    sys.path.insert(0, _v3_dir)
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from v3.engine import Engine, MarketType
from v3.paper_engine import _load_strategy_fn
from v3.universe import get_all_tradeable


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
    cap_multiplier: float
    leverage: np.ndarray
    max_trade_pct: float          # additional sizing cap (0 = disabled)
    trail_schedule: Optional[np.ndarray] = None
    max_trail_mult: Optional[np.ndarray] = None
    # Exit mode fields
    convex_exit: bool = False
    rsi: Optional[np.ndarray] = None
    rsi_exit_level: float = 999.0
    mean_target_vals: Optional[np.ndarray] = None
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


def discover_tokens(market: str = "combined") -> list[str]:
    """Find tokens with data for the given market type.

    Combined strategies require both spot and perp parquets.
    Single-market strategies use corresponding market only.
    """
    if market == "combined":
        spot_dir = Path(DATA_DIR) / "spot" / "1h_cache"
        perp_dir = Path(DATA_DIR) / "perp" / "1h_cache"
        spot = {f.replace("_1h.parquet", "") for f in os.listdir(spot_dir)
                if f.endswith(".parquet")} if spot_dir.is_dir() else set()
        perp = {f.replace("_1h.parquet", "") for f in os.listdir(perp_dir)
                if f.endswith(".parquet")} if perp_dir.is_dir() else set()
        return sorted(spot & perp)
    else:
        return get_all_tradeable(market)


def precompute_strategy_signals(
    strategy_spec: StrategySpec,
    tokens: list[str],
    config: PortfolioConfig,
    months: int,
) -> dict[str, TokenSignals]:
    """Precompute signal arrays for one strategy across all tokens.

    Follows backtest_funds.py pattern:
      _build_context() -> strategy_fn() -> extract arrays -> walk-forward mask
    """
    strategy_fn = _load_strategy_fn(strategy_spec.strategy_id)
    is_single_ctx = len(inspect.signature(strategy_fn).parameters) == 1
    is_combined = strategy_spec.market == "combined"

    eng_spot = Engine(data_dir=DATA_DIR, market="spot", capital=config.capital, exchange=config.exchange)
    eng_perp = Engine(data_dir=DATA_DIR, market="perp", capital=config.capital, exchange=config.exchange)

    results: dict[str, TokenSignals] = {}

    for token in tokens:
        try:
            spot_pq = Path(DATA_DIR) / "spot" / "1h_cache" / f"{token}_1h.parquet"
            perp_pq = Path(DATA_DIR) / "perp" / "1h_cache" / f"{token}_1h.parquet"

            if is_combined:
                if not spot_pq.exists() or not perp_pq.exists():
                    continue
            elif strategy_spec.market == "spot":
                if not spot_pq.exists():
                    continue
            else:  # perp
                if not perp_pq.exists():
                    continue

            # Load and trim data
            cutoff = pd.Timestamp.now().tz_localize(None) - pd.DateOffset(months=months + 2)

            df_spot = None
            df_perp = None
            ctx_spot = None
            ctx_perp = None

            if spot_pq.exists():
                df_spot = pd.read_parquet(spot_pq)
                df_spot = df_spot[df_spot.index >= cutoff]
            if perp_pq.exists():
                df_perp = pd.read_parquet(perp_pq)
                df_perp = df_perp[df_perp.index >= cutoff]

            if is_combined:
                if df_spot is None or df_perp is None or len(df_spot) < 500 or len(df_perp) < 500:
                    continue
                ctx_spot = eng_spot._build_context(token, df_spot, min_bars=210, market_override="spot")
                ctx_perp = eng_perp._build_context(token, df_perp, min_bars=210, market_override="perp")
                if ctx_spot is None or ctx_perp is None:
                    continue
            elif strategy_spec.market == "spot":
                if df_spot is None or len(df_spot) < 500:
                    continue
                ctx_spot = eng_spot._build_context(token, df_spot, min_bars=210, market_override="spot")
                if ctx_spot is None:
                    continue
            else:  # perp
                if df_perp is None or len(df_perp) < 500:
                    continue
                ctx_perp = eng_perp._build_context(token, df_perp, min_bars=210, market_override="perp")
                if ctx_perp is None:
                    continue

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
                p_rsi = _copy_f32(ctx_spot.ind_1h["rsi"], n_safe)
            else:
                p_close = _copy_f32(ctx_perp.ind_1h["close"], n_safe)
                p_high = _copy_f32(ctx_perp.ind_1h["high"], n_safe)
                p_low = _copy_f32(ctx_perp.ind_1h["low"], n_safe)
                p_atr = _copy_f32(ctx_perp.ind_1h["atr"], n_safe)
                p_adv = _copy_f32(ctx_perp.rolling_adv, n_safe) if ctx_perp.rolling_adv is not None else np.full(n_safe, 5_000_000.0, dtype=np.float32)
                p_regime = ctx_perp.regime_1h[:n_safe].copy()
                p_funding = _copy_f32(ctx_perp.funding_1h, n_safe) if ctx_perp.funding_1h is not None else np.zeros(n_safe, dtype=np.float32)
                timestamps = ctx_perp.idx_1h[:n_safe].copy()
                p_rsi = _copy_f32(ctx_perp.ind_1h["rsi"], n_safe)

            # Entry mask with liquidity masking
            entry_mask = sr.entry_mask[:n_safe].copy()
            if ctx_spot is not None and ctx_spot.liquidity_mask is not None:
                entry_mask = entry_mask & ctx_spot.liquidity_mask[:n_safe]
            elif ctx_perp is not None and ctx_perp.liquidity_mask is not None:
                entry_mask = entry_mask & ctx_perp.liquidity_mask[:n_safe]

            # Walk-forward masking
            entry_mask = _apply_walk_forward_mask(
                entry_mask, config.train_bars, config.recal_bars, config.purge_bars,
            )

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

            if is_combined and ctx_perp is not None:
                sec_entry_raw = sr.secondary_entry_mask[:n_safe].copy() if sr.secondary_entry_mask is not None else np.zeros(n_safe, dtype=bool)
                if ctx_perp.liquidity_mask is not None:
                    sec_entry_raw = sec_entry_raw & ctx_perp.liquidity_mask[:n_safe]
                sec_entry = _apply_walk_forward_mask(
                    sec_entry_raw, config.train_bars, config.recal_bars, config.purge_bars,
                )
                sec_dir = sr.secondary_direction[:n_safe].copy() if sr.secondary_direction is not None else np.ones(n_safe, dtype=np.int8)

                perp_close_arr = _copy_f32(ctx_perp.ind_1h["close"], n_safe)
                perp_high_arr = _copy_f32(ctx_perp.ind_1h["high"], n_safe)
                perp_low_arr = _copy_f32(ctx_perp.ind_1h["low"], n_safe)
                perp_atr_arr = _copy_f32(ctx_perp.ind_1h["atr"], n_safe)
                perp_adv_arr = _copy_f32(ctx_perp.rolling_adv, n_safe) if ctx_perp.rolling_adv is not None else np.full(n_safe, 5_000_000.0, dtype=np.float32)
                perp_funding_arr = _copy_f32(ctx_perp.funding_1h, n_safe) if ctx_perp.funding_1h is not None else np.zeros(n_safe, dtype=np.float32)

                # Determine which leg is perp
                is_perp_primary = (sr.market_type == MarketType.PERP)
                is_perp_secondary = (getattr(sr, 'secondary_market_type', MarketType.PERP) == MarketType.PERP)
            elif strategy_spec.market == "perp":
                is_perp_primary = True

            # Mean target values
            mean_target = None
            if sr.mean_target_vals is not None:
                mean_target = _copy_f32(sr.mean_target_vals, n_safe)

            # Trail schedule
            trail_sched = None
            if sr.trail_schedule is not None:
                trail_sched = np.asarray(sr.trail_schedule, dtype=np.float32)

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
            sr_cap_mult = float(sr.cap_multiplier)
            sr_leverage = _to_array(sr.leverage, n_safe)
            sr_max_trade_pct = float(sr.max_trade_pct)
            sr_convex_exit = sr.convex_exit
            sr_rsi_exit_level = float(sr.rsi_exit_level)
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
                trail_schedule=trail_sched,
                max_trail_mult=max_trail,
                convex_exit=sr_convex_exit,
                rsi=p_rsi,
                rsi_exit_level=sr_rsi_exit_level,
                mean_target_vals=mean_target,
                is_combined=is_combined,
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
            )
            results[token] = ts

        except Exception as e:
            print(f"  {token}: signal error - {e}")
            continue

    return results
