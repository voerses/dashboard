"""V4 Portfolio Backtest — Portfolio (Class B) signal precomputation.

Portfolio strategies receive ALL token contexts simultaneously and return
per-token StrategyResults.  This adapter loads contexts, calls the portfolio
strategy once, converts the results into per-token TokenSignals, and applies
walk-forward masking.  The simulator sees the same dict[str, TokenSignals]
format as Class A strategies.

Portfolio strategy function signature:
    def strategy(contexts: dict[str, tuple]) -> dict[str, StrategyResult]:
        # contexts = {token: (ctx_spot, ctx_perp) | ctx_spot | ctx_perp}
        # Return StrategyResult per token to trade (omit tokens to skip)
"""
from __future__ import annotations

import inspect
import os
import sys
from pathlib import Path
from typing import Optional, Callable

import numpy as np
import pandas as pd

from .config import PortfolioConfig, StrategySpec
from .signals import (
    TokenSignals,
    _to_array,
    _copy_f32,
    _apply_walk_forward_mask,
    DATA_DIR,
)

_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from v4.engine import Engine, MarketType, _load_strategy_fn
from v4.data_loader import load_token_data


def _load_all_contexts(
    tokens: list[str],
    strategy_spec: StrategySpec,
    config: PortfolioConfig,
    months: int,
    end_date: Optional[pd.Timestamp] = None,
) -> tuple[dict, pd.Timestamp, pd.Timestamp]:
    """Load contexts for ALL tokens at once.

    Returns:
        contexts: {token: (ctx_spot, ctx_perp)} for combined,
                  {token: ctx} for single-market
        cutoff: timestamp for trimming arrays
        anchor: data end timestamp
    """
    is_combined = strategy_spec.market == "combined"

    eng_spot = Engine(data_dir=DATA_DIR, market="spot", capital=config.capital, exchange=config.exchange)
    eng_perp = Engine(data_dir=DATA_DIR, market="perp", capital=config.capital, exchange=config.exchange)

    WARMUP_DAYS = 180
    anchor = end_date if end_date is not None else pd.Timestamp.now().tz_localize(None)
    trade_start = anchor - pd.DateOffset(months=months)
    if config.skip_walk_forward:
        cutoff = trade_start
    else:
        cutoff = trade_start - pd.DateOffset(hours=int(config.train_bars))
    load_from = cutoff - pd.DateOffset(days=WARMUP_DAYS)

    contexts = {}

    for token in tokens:
        try:
            # Load data via data_loader (merges historical + live buffer)
            df_spot_full = load_token_data(token, "spot", data_dir=DATA_DIR)
            df_perp_full = load_token_data(token, "perp", data_dir=DATA_DIR)

            if is_combined:
                if df_spot_full is None or df_perp_full is None:
                    continue
            elif strategy_spec.market == "spot":
                if df_spot_full is None:
                    continue
            else:
                if df_perp_full is None:
                    continue

            df_spot = None
            df_perp = None

            if df_spot_full is not None:
                df_spot = df_spot_full[df_spot_full.index >= load_from]
            if df_perp_full is not None:
                df_perp = df_perp_full[df_perp_full.index >= load_from]

            if is_combined:
                if df_spot is None or df_perp is None or len(df_spot) < 500 or len(df_perp) < 500:
                    continue
                common_idx = df_spot.index.intersection(df_perp.index)
                df_spot = df_spot.reindex(common_idx)
                df_perp = df_perp.reindex(common_idx)
                if len(df_spot) < 500 or len(df_perp) < 500:
                    continue
                ctx_spot = eng_spot._build_context(token, df_spot, min_bars=210, market_override="spot")
                ctx_perp = eng_perp._build_context(token, df_perp, min_bars=210, market_override="perp")
                if ctx_spot is None or ctx_perp is None:
                    continue
                contexts[token] = (ctx_spot, ctx_perp)
            elif strategy_spec.market == "spot":
                if df_spot is None or len(df_spot) < 500:
                    continue
                ctx_spot = eng_spot._build_context(token, df_spot, min_bars=210, market_override="spot")
                if ctx_spot is None:
                    continue
                contexts[token] = ctx_spot
            else:
                if df_perp is None or len(df_perp) < 500:
                    continue
                ctx_perp = eng_perp._build_context(token, df_perp, min_bars=210, market_override="perp")
                if ctx_perp is None:
                    continue
                contexts[token] = ctx_perp

        except Exception as e:
            print(f"  {token}: context load error - {e}")
            continue

    return contexts, cutoff, anchor


def _sr_to_token_signals(
    token: str,
    sr,  # StrategyResult
    ctx_spot,
    ctx_perp,
    strategy_id: str,
    is_combined: bool,
    market: str,
    cutoff: pd.Timestamp,
    config: PortfolioConfig,
) -> Optional[TokenSignals]:
    """Convert a StrategyResult + contexts into a TokenSignals object.

    This mirrors the extraction logic in signals.py:precompute_strategy_signals
    but operates on already-loaded contexts (no file I/O).
    """
    primary_ctx = ctx_spot if ctx_spot is not None else ctx_perp
    n = len(primary_ctx.ind_1h["close"])

    n_safe = n
    if ctx_spot is not None:
        n_safe = min(n_safe, len(ctx_spot.idx_1h), len(ctx_spot.regime_1h))
    if ctx_perp is not None:
        n_safe = min(n_safe, len(ctx_perp.ind_1h["close"]))
    n_safe = min(n_safe, len(sr.entry_mask))

    # Extract primary market arrays
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
        _ind = ctx_spot.ind_1h
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
        p_rsi = _copy_f32(ctx_perp.ind_1h["rsi"], n_safe)
        _ind = ctx_perp.ind_1h
        p_volume = _copy_f32(_ind["volume"], n_safe) if "volume" in _ind else None
        p_vol_20 = _copy_f32(_ind["vol_20"], n_safe) if "vol_20" in _ind else None
        p_ret_1h = _copy_f32(_ind["ret_1"], n_safe) if "ret_1" in _ind else None

    # 3-stage signal counting: raw → post-liquidity → post-WF
    raw_count = int(sr.entry_mask[:n_safe].sum())

    # Entry mask with liquidity masking
    entry_mask = sr.entry_mask[:n_safe].copy()
    if ctx_spot is not None and ctx_spot.liquidity_mask is not None:
        entry_mask = entry_mask & ctx_spot.liquidity_mask[:n_safe]
    elif ctx_perp is not None and ctx_perp.liquidity_mask is not None:
        entry_mask = entry_mask & ctx_perp.liquidity_mask[:n_safe]

    post_liq_count = int(entry_mask.sum())

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
        sec_entry = sr.secondary_entry_mask[:n_safe].copy() if sr.secondary_entry_mask is not None else np.zeros(n_safe, dtype=bool)
        if ctx_perp.liquidity_mask is not None:
            sec_entry = sec_entry & ctx_perp.liquidity_mask[:n_safe]
        sec_dir = sr.secondary_direction[:n_safe].copy() if sr.secondary_direction is not None else np.ones(n_safe, dtype=np.int8)

        perp_close_arr = _copy_f32(ctx_perp.ind_1h["close"], n_safe)
        perp_high_arr = _copy_f32(ctx_perp.ind_1h["high"], n_safe)
        perp_low_arr = _copy_f32(ctx_perp.ind_1h["low"], n_safe)
        perp_atr_arr = _copy_f32(ctx_perp.ind_1h["atr"], n_safe)
        perp_adv_arr = _copy_f32(ctx_perp.rolling_adv, n_safe) if ctx_perp.rolling_adv is not None else np.full(n_safe, 5_000_000.0, dtype=np.float32)
        perp_funding_arr = _copy_f32(ctx_perp.funding_1h, n_safe) if ctx_perp.funding_1h is not None else np.zeros(n_safe, dtype=np.float32)

        is_perp_primary = (sr.market_type == MarketType.PERP)
        is_perp_secondary = (getattr(sr, 'secondary_market_type', MarketType.PERP) == MarketType.PERP)
    elif market == "perp":
        is_perp_primary = True

    # Schedules and optional arrays
    mean_target = _copy_f32(sr.mean_target_vals, n_safe) if sr.mean_target_vals is not None else None
    sma_trail = _copy_f32(sr.sma_trail_vals, n_safe) if getattr(sr, 'sma_trail_vals', None) is not None else None
    entry_limit = _copy_f32(sr.entry_limit_price, n_safe) if getattr(sr, 'entry_limit_price', None) is not None else None
    trail_sched = np.asarray(sr.trail_schedule, dtype=np.float32) if sr.trail_schedule is not None else None
    time_trail_sched = np.asarray(sr.time_trail_schedule, dtype=np.float32) if getattr(sr, 'time_trail_schedule', None) is not None else None
    max_trail = np.asarray(sr.max_trail_mult, dtype=np.float32)[:n_safe].copy() if sr.max_trail_mult is not None else None

    # Extract scalar/array fields
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
    sr_bear_target_mult = float(getattr(sr, 'bear_target_mult', 0.0))
    sr_bear_max_hold = int(getattr(sr, 'bear_max_hold', 0))
    # Conviction score: use explicit if provided, else derive from size_multiplier
    sr_conviction = None
    if getattr(sr, 'conviction_score', None) is not None:
        sr_conviction = _to_array(sr.conviction_score, n_safe)
    elif sr.size_multiplier is not None:
        sm = _to_array(sr.size_multiplier, n_safe)
        sm_max = float(np.nanmax(sm)) if len(sm) > 0 else 1.0
        sr_conviction = sm / max(sm_max, 1e-10)
    sr_sec_leverage = float(getattr(sr, 'secondary_leverage', 1.0))
    sr_capital_split = float(getattr(sr, 'capital_split', 0.5))
    sr_sec_stop = float(sr.secondary_stop_mult) if sr.secondary_stop_mult is not None else None
    sr_sec_trail = float(sr.secondary_trail_mult) if sr.secondary_trail_mult is not None else None
    sr_sec_target = float(sr.secondary_target_mult) if sr.secondary_target_mult is not None else None
    sr_sec_no_stop = int(sr.secondary_no_stop_bars) if sr.secondary_no_stop_bars is not None else None
    sr_sec_min_hold = int(sr.secondary_min_hold) if sr.secondary_min_hold is not None else None
    sr_sec_max_hold = int(sr.secondary_max_hold) if sr.secondary_max_hold is not None else None

    # Trim to simulation window
    ts_arr = timestamps.values if hasattr(timestamps, 'values') else np.asarray(timestamps)
    cutoff_ns = np.datetime64(cutoff)
    trim_start = int(np.searchsorted(ts_arr, cutoff_ns))

    if trim_start > 0:
        s = trim_start
        timestamps = timestamps[s:]
        p_close = p_close[s:]
        p_high = p_high[s:]
        p_low = p_low[s:]
        p_atr = p_atr[s:]
        p_adv = p_adv[s:]
        p_regime = p_regime[s:]
        p_funding = p_funding[s:]
        p_rsi = p_rsi[s:]
        entry_mask = entry_mask[s:]
        sr_direction = sr_direction[s:]
        sr_stop_mult = sr_stop_mult[s:]
        sr_trail_mult = sr_trail_mult[s:]
        sr_size_mult = sr_size_mult[s:]
        sr_cap_mult = sr_cap_mult[s:]
        sr_leverage = sr_leverage[s:]
        if sr_conviction is not None:
            sr_conviction = sr_conviction[s:]
        if entry_limit is not None:
            entry_limit = entry_limit[s:]
        if mean_target is not None:
            mean_target = mean_target[s:]
        if sma_trail is not None:
            sma_trail = sma_trail[s:]
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
        if p_volume is not None:
            p_volume = p_volume[s:]
        if p_vol_20 is not None:
            p_vol_20 = p_vol_20[s:]
        if p_ret_1h is not None:
            p_ret_1h = p_ret_1h[s:]
        n_safe = n_safe - s

    # Walk-forward masking (conditional on skip_walk_forward)
    if not config.skip_walk_forward:
        if n_safe < config.train_bars + config.purge_bars + 100:
            return None
        entry_mask = _apply_walk_forward_mask(
            entry_mask, config.train_bars, config.recal_bars, config.purge_bars,
        )
        if sec_entry is not None:
            sec_entry = _apply_walk_forward_mask(
                sec_entry, config.train_bars, config.recal_bars, config.purge_bars,
            )
    else:
        if n_safe < 200:
            return None

    post_wf_count = int(entry_mask.sum())

    return TokenSignals(
        token=token,
        strategy_id=strategy_id,
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
        convex_exit=sr_convex_exit,
        rsi=p_rsi,
        rsi_exit_level=sr_rsi_exit_level,
        mean_target_vals=mean_target,
        sma_trail_vals=sma_trail,
        entry_limit_price=entry_limit,
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
        volume=p_volume,
        vol_20=p_vol_20,
        ret_1h=p_ret_1h,
        # Diagnostic counters
        raw_entry_count=raw_count,
        post_liquidity_count=post_liq_count,
        post_walkforward_count=post_wf_count,
        # Exit constants from StrategyResult
        regime_exit_min_bars=getattr(sr, 'regime_exit_min_bars', 6),
        convex_bar_thresholds=getattr(sr, 'convex_bar_thresholds', (48, 12)),
        convex_multipliers=getattr(sr, 'convex_multipliers', (2.0, 1.5, 0.3)),
    )


def _resolve_minute_entries(
    strategy_results: dict,
    contexts: dict,
    strategy_spec: StrategySpec,
    live_bar: int = -1,
) -> None:
    """Resolve entry_limit_price to 1m-level cross prices in-place.

    For each entry bar where entry_limit_price is set (the BB threshold),
    loads 1m data and finds the first 1m close that crosses the threshold.
    Replaces entry_limit_price with the actual 1m cross price.
    Clears entry_mask for bars where no 1m cross is found.

    Args:
        live_bar: If >= 0, bars at this index or later are preserved when
                  1m data is missing (paper trading: WebSocket handles live
                  bars). For backtest, leave as -1 to clear all unresolvable.
    """
    from v4.minute_exits import MinuteExitCache
    cache = MinuteExitCache(resolution=1, max_tokens=20)
    resolved = 0
    skipped = 0
    no_1m_data = 0

    for token, sr in strategy_results.items():
        if sr.entry_limit_price is None:
            continue

        ctx = contexts.get(token)
        if ctx is None:
            continue
        if isinstance(ctx, tuple):
            ctx = ctx[1] if ctx[1] is not None else ctx[0]

        ts_1h = ctx.idx_1h  # DatetimeIndex
        entry_bars = np.where(sr.entry_mask)[0]

        for bar in entry_bars:
            lp = sr.entry_limit_price[bar]
            if np.isnan(lp):
                continue

            direction = int(sr.direction[bar])
            hour_ts = np.datetime64(ts_1h[bar], 'ms')

            minute_data = cache.get_minute_bars(token, hour_ts)
            if minute_data is None:
                if live_bar >= 0 and bar >= live_bar:
                    # Paper mode: preserve entry_mask for WebSocket resolution
                    continue
                sr.entry_mask[bar] = False  # No 1m data → skip entry
                no_1m_data += 1
                continue

            _, _, closes_1m = minute_data

            # Find first 1m close crossing the BB level
            if direction == 1:
                cross_mask = closes_1m > lp
            else:
                cross_mask = closes_1m < lp

            cross_idx = np.where(cross_mask)[0]
            if len(cross_idx) > 0:
                sr.entry_limit_price[bar] = float(closes_1m[cross_idx[0]])
                resolved += 1
            else:
                sr.entry_mask[bar] = False  # No cross → skip entry
                skipped += 1

    print(f"  1m entry resolution: {resolved} resolved, {skipped} skipped (no cross), {no_1m_data} skipped (no 1m data)")


def precompute_portfolio_signals(
    strategy_spec: StrategySpec,
    tokens: list[str],
    config: PortfolioConfig,
    months: int,
    end_date: Optional[pd.Timestamp] = None,
    live_bar: int = -1,
) -> dict[str, TokenSignals]:
    """Precompute signals for a portfolio (Class B) strategy.

    1. Load ALL token contexts
    2. Call portfolio_strategy_fn(contexts) once
    3. Convert per-token StrategyResults to TokenSignals
    4. Apply walk-forward masking

    The portfolio strategy function receives a dict of contexts and returns
    a dict of StrategyResults for tokens it wants to trade.
    """
    strategy_fn = _load_strategy_fn(strategy_spec.strategy_id)
    is_combined = strategy_spec.market == "combined"

    print(f"  Loading contexts for {len(tokens)} tokens ({strategy_spec.market})...")
    contexts, cutoff, anchor = _load_all_contexts(
        tokens, strategy_spec, config, months, end_date,
    )
    print(f"  Loaded {len(contexts)} token contexts")

    # Call portfolio strategy with all contexts
    print(f"  Calling portfolio strategy {strategy_spec.strategy_id}...")
    strategy_results = strategy_fn(contexts)
    print(f"  Strategy returned signals for {len(strategy_results)} tokens")

    # Resolve 1m entry prices if entry_resolution > 0
    if strategy_spec.entry_resolution > 0:
        _resolve_minute_entries(strategy_results, contexts, strategy_spec, live_bar=live_bar)

    # Convert each StrategyResult → TokenSignals
    results: dict[str, TokenSignals] = {}
    for token, sr in strategy_results.items():
        if token not in contexts:
            continue

        ctx = contexts[token]
        if is_combined:
            ctx_spot, ctx_perp = ctx
        elif strategy_spec.market == "spot":
            ctx_spot, ctx_perp = ctx, None
        else:
            ctx_spot, ctx_perp = None, ctx

        try:
            ts = _sr_to_token_signals(
                token=token,
                sr=sr,
                ctx_spot=ctx_spot,
                ctx_perp=ctx_perp,
                strategy_id=strategy_spec.strategy_id,
                is_combined=is_combined,
                market=strategy_spec.market,
                cutoff=cutoff,
                config=config,
            )
            if ts is not None:
                results[token] = ts
        except Exception as e:
            print(f"  {token}: signal conversion error - {e}")
            continue

    print(f"  Portfolio signals: {len(results)} tokens ready for simulation")
    return results
