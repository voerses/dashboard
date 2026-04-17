"""Sub-hourly exit simulation for the backtester.

Processes stop/trail/target/CB exits at configurable resolution (1m, 5m,
15m, 30m) within each hourly bar using the 1m parquet cache. Positions
closed here are removed from open_positions before _process_exits runs,
so hourly-only exits (regime, RSI, max_hold, funding) still fire for
surviving positions.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd

from .config import PortfolioConfig, StrategySpec
from .position import Position
from .simulator import (
    SimulationState,
    _close_position,
    _get_bar_data,
)
from .signals import TokenSignals


class MinuteExitCache:
    """Lazy-loads 1m parquet data per token with LRU eviction.

    Uses float32 storage (~7.5MB/token) and evicts least-recently-used
    tokens when cache exceeds max_tokens to bound memory usage.
    """

    def __init__(self, data_dir: str = "data/perp/1m_cache", max_tokens: int = 20,
                 resolution: int = 1):
        self._data_dir = Path(data_dir)
        self._loaded: dict[str, dict] = {}     # token -> {high, low, close, hour_idx}
        self._access_order: list[str] = []     # LRU order (most recent at end)
        self._unavailable: set[str] = set()
        self._max_tokens = max_tokens
        if resolution not in (1, 5, 15, 30):
            raise ValueError(f"resolution must be 1, 5, 15, or 30 (got {resolution})")
        self._resolution = resolution

    def get_minute_bars(
        self, token: str, hour_ts: np.datetime64,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
        """Return (high, low, close) arrays for this hour at configured resolution.

        At 1m resolution returns 60 bars, at 5m returns 12, at 15m returns 4, etc.
        Each bar aggregates: high=max, low=min, close=last of the underlying 1m candles.
        Returns None if no data available for this token/hour.
        """
        if token in self._unavailable:
            return None
        if token not in self._loaded:
            if not self._load_token(token):
                return None
        else:
            # Update LRU order
            self._access_order.remove(token)
            self._access_order.append(token)

        data = self._loaded[token]
        hour_idx = data["hour_idx"]

        # Convert hour_ts to the same dtype for lookup
        key = np.datetime64(hour_ts, "ms")
        loc = hour_idx.get(key)
        if loc is None:
            # Try truncating to hour boundary
            key_h = np.datetime64(hour_ts, "h").astype("datetime64[ms]")
            loc = hour_idx.get(key_h)
            if loc is None:
                return None

        start, end = loc
        h_raw = data["high"][start:end]
        l_raw = data["low"][start:end]
        c_raw = data["close"][start:end]

        if self._resolution == 1:
            return h_raw, l_raw, c_raw

        return _aggregate_bars(h_raw, l_raw, c_raw, self._resolution)

    def _load_token(self, token: str) -> bool:
        """Lazy-load parquet: read only high/low/close columns as float32.
        Build hour_idx dict mapping hour_ts -> (start, end) via np.searchsorted.
        Evicts LRU tokens when cache is full.
        """
        path = self._data_dir / f"{token}_1m.parquet"
        if not path.exists():
            self._unavailable.add(token)
            return False

        try:
            df = pd.read_parquet(path, columns=["high", "low", "close"])
        except Exception:
            self._unavailable.add(token)
            return False

        # Evict LRU tokens if cache is full
        while len(self._loaded) >= self._max_tokens and self._access_order:
            evict = self._access_order.pop(0)
            del self._loaded[evict]

        ts = df.index.values.astype("datetime64[ms]")
        high = df["high"].values.astype(np.float32)
        low = df["low"].values.astype(np.float32)
        close = df["close"].values.astype(np.float32)

        # Build hourly index: map each hour boundary -> (start, end) slice
        hours = ts.astype("datetime64[h]").astype("datetime64[ms]")
        unique_hours = np.unique(hours)
        hour_idx: dict[np.datetime64, tuple[int, int]] = {}
        for h in unique_hours:
            mask_start = np.searchsorted(hours, h, side="left")
            mask_end = np.searchsorted(hours, h, side="right")
            hour_idx[h] = (int(mask_start), int(mask_end))

        self._loaded[token] = {
            "high": high,
            "low": low,
            "close": close,
            "hour_idx": hour_idx,
        }
        self._access_order.append(token)
        return True


def _aggregate_bars(
    high: np.ndarray, low: np.ndarray, close: np.ndarray, resolution: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Aggregate 1m bars into coarser resolution (5m, 15m, 30m).

    Each output bar: high=max, low=min, close=last of the chunk.
    Handles partial trailing chunks (e.g., 60 bars at 7m resolution = 8 full + 4 remainder).
    """
    n = len(high)
    n_bars = (n + resolution - 1) // resolution  # ceiling division
    agg_h = np.empty(n_bars, dtype=high.dtype)
    agg_l = np.empty(n_bars, dtype=low.dtype)
    agg_c = np.empty(n_bars, dtype=close.dtype)
    for i in range(n_bars):
        s = i * resolution
        e = min(s + resolution, n)
        agg_h[i] = np.max(high[s:e])
        agg_l[i] = np.min(low[s:e])
        agg_c[i] = close[e - 1]
    return agg_h, agg_l, agg_c


def check_candle_exits(
    pos: Position,
    h: float,
    l: float,
    c: float,
    cur_atr: float,
    bars_held: int,
    cb_r: float,
    eff_target: float,
    convex_initial_risk: float,
) -> tuple[str | None, float | None]:
    """Check a single candle for price-based exits.

    Returns (exit_reason, exit_price) or (None, None).
    Used by both process_minute_exits (backtest) and process_sub_hourly_exits (paper).

    Mutates pos in-place: updates highest/lowest, breakeven_triggered, stop_price.
    Does NOT handle partial TP (caller handles that separately).

    Parameters
    ----------
    pos : Position
        Open position (mutated in-place for highest/lowest/stop tracking).
    h, l, c : float
        High, low, close of the candle.
    cur_atr : float
        Current ATR value.
    bars_held : int
        Number of bars the position has been held.
    cb_r : float
        Circuit breaker multiplier (0 = disabled).
    eff_target : float
        Effective target multiplier (may be adjusted for bear regime).
    convex_initial_risk : float
        Initial risk for convex exits (pos.initial_risk).
    """
    if math.isnan(h) or math.isnan(l) or math.isnan(c):
        return None, None

    d = pos.direction

    # Step 1: Update highest/lowest
    if d == 1:
        pos.highest = max(pos.highest, h)
    else:
        pos.lowest = min(pos.lowest, l)

    # Step 2: Breakeven ratchet
    if pos.breakeven_atr > 0.0 and not pos.breakeven_triggered:
        if d == 1:
            be_profit_atr = (pos.highest - pos.entry_price) / max(cur_atr, 1e-10)
        else:
            be_profit_atr = (pos.entry_price - pos.lowest) / max(cur_atr, 1e-10)
        if be_profit_atr >= pos.breakeven_atr:
            if d == 1:
                pos.stop_price = max(pos.stop_price, pos.entry_price)
            else:
                pos.stop_price = min(pos.stop_price, pos.entry_price)
            pos.breakeven_triggered = True

    # Step 3: Trail tightening
    _update_trail_minute(pos, cur_atr, bars_held)

    # Step 4: Check exit conditions (priority: CB > stop > target)
    stop_active = bars_held >= pos.no_stop_bars or pos.convex_exit

    # Circuit breaker
    if cb_r > 0 and convex_initial_risk > 0:
        cb_dist = cb_r * convex_initial_risk
        if d == 1 and l <= pos.entry_price - cb_dist:
            return "circuit_breaker", c
        elif d == -1 and h >= pos.entry_price + cb_dist:
            return "circuit_breaker", c

    # Stop-loss
    if stop_active and d == 1 and l <= pos.stop_price:
        return "stop", c
    elif stop_active and d == -1 and h >= pos.stop_price:
        return "stop", c

    # Target
    if pos.convex_exit:
        if d == 1 and c > pos.entry_price + eff_target * convex_initial_risk:
            return "target", c
        elif d == -1 and c < pos.entry_price - eff_target * convex_initial_risk:
            return "target", c
    else:
        if d == 1 and h >= pos.entry_price + eff_target * cur_atr:
            return "target", c
        elif d == -1 and l <= pos.entry_price - eff_target * cur_atr:
            return "target", c

    return None, None


def process_minute_exits(
    state: SimulationState,
    all_signals: dict[str, dict[str, TokenSignals]],
    bar_maps: dict[str, np.ndarray],
    global_bar: int,
    unified_ts: np.ndarray,
    config: PortfolioConfig,
    strategy_specs: dict[str, StrategySpec],
    minute_cache: MinuteExitCache,
) -> None:
    """Process price-based exits at sub-hourly resolution for this hourly bar.

    Resolution is determined by the MinuteExitCache's resolution setting.
    Runs BEFORE _process_exits. Positions closed here are removed from
    open_positions, so _process_exits naturally skips them.
    """
    hour_ts = unified_ts[global_bar]
    positions_to_close: list[tuple[Position, float, str, float]] = []

    for pos in list(state.position_manager.open_positions):
        # Skip if already marked for closure
        if any(p is pos for p, _, _, _ in positions_to_close):
            continue

        sig = all_signals.get(pos.strategy_id, {}).get(pos.token)
        if sig is None:
            continue

        bm = bar_maps.get(pos.token)
        if bm is None:
            continue

        local_bar = int(bm[global_bar])
        if local_bar == -1 or local_bar >= sig.n_bars:
            continue

        # Get hourly bar data for ATR, ADV
        is_secondary = pos.leg == "secondary"
        _use_perp = pos.is_perp if sig.per_bar_is_perp is not None else None
        _, _, _, atr_val, adv_val, _ = _get_bar_data(sig, local_bar, not is_secondary, use_perp=_use_perp)

        cur_atr = atr_val
        if np.isnan(cur_atr):
            cur_atr = abs(pos.entry_price) * 0.02

        # Get 60 minute bars from cache
        minute_data = minute_cache.get_minute_bars(pos.token, hour_ts)
        if minute_data is None:
            continue

        m_high, m_low, m_close = minute_data
        bars_held = global_bar - pos.entry_bar
        d = pos.direction

        # Get circuit breaker params
        spec_cb = strategy_specs.get(pos.strategy_id)
        cb_r = spec_cb.circuit_breaker_r if spec_cb else 0.0

        # Compute effective target (may be adjusted for bear regime)
        eff_target = pos.target_mult
        if sig.bear_target_mult > 0.0:
            regime_val_tp = int(sig.regime[local_bar])
            if regime_val_tp == 4:
                eff_target = sig.bear_target_mult

        exit_price = None
        exit_reason = ""

        for i in range(len(m_high)):
            h_i = float(m_high[i])
            l_i = float(m_low[i])
            c_i = float(m_close[i])

            if np.isnan(h_i) or np.isnan(l_i) or np.isnan(c_i):
                continue

            # Use shared helper for exit checks (updates pos state in-place)
            reason, price = check_candle_exits(
                pos, h_i, l_i, c_i, cur_atr, bars_held,
                cb_r, eff_target, pos.initial_risk,
            )
            if reason is not None:
                exit_price = price
                exit_reason = reason
                break

        if exit_price is not None:
            positions_to_close.append((pos, exit_price, exit_reason, adv_val))
            # Close linked position too
            if pos.linked_position_id:
                linked = state.position_manager.get_linked(pos.linked_position_id)
                if linked and not any(p is linked for p, _, _, _ in positions_to_close):
                    is_lk_sec = linked.leg == "secondary"
                    _lkup = linked.is_perp if sig.per_bar_is_perp is not None else None
                    lc, _, _, _, la, _ = _get_bar_data(sig, local_bar, not is_lk_sec, use_perp=_lkup)
                    positions_to_close.append((linked, lc, "linked_exit", la))

    # Execute all closures
    for pos, exit_price, reason, exit_adv in positions_to_close:
        if pos in state.position_manager.open_positions:
            _close_position(state, pos, global_bar, exit_price, reason, exit_adv, config)


def _update_trail_minute(pos: Position, cur_atr: float, bars_held: int) -> None:
    """Update trailing stop at minute granularity.

    Mirrors hourly _update_trail guard conditions:
    - Skip if convex_exit (has its own bar-level logic)
    - Skip if trail_schedule is not None (profit-dependent, needs hourly close)
    - Skip if chandelier_lookback > 0 (needs hourly high/low arrays)
    - Skip if ATR or trail_mult invalid
    """
    # Guard: convex positions use different exit mechanics
    if pos.convex_exit:
        return

    # Guard: profit-dependent trail schedule needs hourly close for profit calc
    if pos.trail_schedule is not None:
        return

    # Guard: chandelier uses lookback-window high/low arrays
    if pos.chandelier_lookback > 0:
        return

    # Guard: invalid trail params
    if cur_atr <= 0 or pos.trail_mult <= 0:
        return

    # Guard: not yet past no_stop_bars
    if bars_held < pos.no_stop_bars:
        return

    d = pos.direction
    eff_tm = pos.trail_mult

    # Time-based trail tightening
    if pos.time_trail_schedule is not None:
        time_tm = pos.trail_mult
        for si in range(pos.time_trail_schedule.shape[0]):
            if bars_held >= pos.time_trail_schedule[si, 0]:
                time_tm = pos.time_trail_schedule[si, 1]
            else:
                break
        if time_tm < eff_tm:
            eff_tm = time_tm

    # Per-bar ceiling (max_trail_mult_arr is bar-indexed, not minute-indexed)
    # Skip here — applied at hourly level in _process_exits

    if d == 1:
        trail = pos.highest - eff_tm * cur_atr
        pos.stop_price = max(pos.stop_price, trail)
    else:
        trail = pos.lowest + eff_tm * cur_atr
        pos.stop_price = min(pos.stop_price, trail)
