"""M11 AC-5 — Deterministic native-vs-legacy parity fixture.

Builds a seeded, non-empty fixture both the legacy
``_process_orders(state, all_signals, strategy_specs, bar_maps,
global_bar, config, rng, unified_ts)`` (M2 batch-shape) and the native
``_process_orders_native(sim, signals, ctx, bar_idx, strategy_specs)``
can consume. Both paths MUST produce the same ARMED orders on
``state.open_orders`` / ``sim.open_orders``; the parity test asserts
order count + per-order token/direction match.

Fixture design:
  * 3 tokens (BTCUSDT, ETHUSDT, SOLUSDT) — exercises at least one token
    that passes cleanly plus a second direction (short) so native
    handles both PRICE_ABOVE / PRICE_BELOW trigger derivation.
  * Each token emits a ``TokenSignal`` with ``direction != 0`` and a
    ``SizingRequest(FIXED_NOTIONAL)``. Legacy ``TokenBarArrays`` carries
    matching ``entry_mask[0]=True``, ``armed_levels[0]=trigger_price``
    so Stage 2 arms a price-triggered Order rather than opening a
    Position directly (so the test's ``state.open_orders`` inspection
    surfaces real orders). We route through ``armed_levels`` rather
    than ``entry_delay`` to avoid a latent legacy code-path where
    ``TriggerType.BAR_CLOSE`` is referenced before a local
    ``from v5.orders import TriggerType`` rebind — a pre-M11 bug that
    doesn't manifest in production because no strategy currently sets
    a non-zero ``entry_delay`` without also setting ``armed_levels``.
  * ``MarketDataCache`` populated with one ``BarData`` event per token,
    so the native path's cache-sourced close/high/low/adv read lands
    the same numbers the legacy TokenBarArrays encodes.
  * Both paths share one ``PortfolioConfig`` with
    ``concentration_limit`` and ``min_position_usd`` set so the clamps
    apply; sizing is chosen so no clamp rejects on either side.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Tuple

import numpy as np


__all__ = ["build_parity_fixture", "_build_parity_fixture"]


# The 3 fixture tokens. Direction signs exercise both long + short
# arming paths on the legacy side (entry_mask + direction array).
_TOKENS: Tuple[Tuple[str, int], ...] = (
    ("BTCUSDT", 1),
    ("ETHUSDT", -1),
    ("SOLUSDT", 1),
)


# Seeded deterministic prices — close > 0 so native's "no_price" reject
# does not fire. adv values chosen to exceed any concentration / adv_cap
# clamp threshold so both paths accept.
_PRICES: Dict[str, Tuple[float, float, float]] = {
    # (close, high, low)
    "BTCUSDT": (60_000.0, 60_500.0, 59_500.0),
    "ETHUSDT": (3_000.0, 3_050.0, 2_950.0),
    "SOLUSDT": (150.0, 152.0, 148.0),
}
_ATR: Dict[str, float] = {
    "BTCUSDT": 1_000.0,
    "ETHUSDT": 50.0,
    "SOLUSDT": 5.0,
}
_VOLUME: Dict[str, float] = {
    # Volume × close ≈ ADV(USD) ≈ 10M for all tokens — well above
    # any adv_cap that config.adv_cap_pct × 500 would imply.
    "BTCUSDT": 200.0,
    "ETHUSDT": 4_000.0,
    "SOLUSDT": 80_000.0,
}


@dataclass
class _ParityClock:
    """Minimal clock compatible with ``MarketDataCache`` / ``BarData``."""
    _now_ns: int = 1_700_000_000_000_000_000
    _bar_idx: int = 0

    def now_ns(self) -> int:
        return self._now_ns

    def advance_to(self, ns: int) -> None:
        self._now_ns = int(ns)

    def current_bar_idx(self) -> int:
        return self._bar_idx

    def set_bar_idx(self, i: int) -> None:
        self._bar_idx = int(i)


def _build_token_bar_arrays(
    token: str,
    direction: int,
    n_bars: int,
) -> "TokenBarArrays":  # type: ignore[name-defined]
    """Construct a legacy TokenBarArrays for ``token``.

    All arrays have length ``n_bars`` but only ``bar_idx=0`` carries
    signal payload — that's where the parity test dispatches.
    """
    from v5.signals import TokenBarArrays

    close = np.full(n_bars, _PRICES[token][0], dtype=np.float64)
    high = np.full(n_bars, _PRICES[token][1], dtype=np.float64)
    low = np.full(n_bars, _PRICES[token][2], dtype=np.float64)
    atr = np.full(n_bars, _ATR[token], dtype=np.float64)
    volume_arr = np.full(n_bars, _VOLUME[token], dtype=np.float64)
    # Rolling ADV proxy — volume × close ≈ USD ADV.
    rolling_adv = volume_arr * close
    funding_1h = np.zeros(n_bars, dtype=np.float64)

    entry_mask = np.zeros(n_bars, dtype=np.bool_)
    entry_mask[0] = True

    direction_arr = np.zeros(n_bars, dtype=np.int8)
    direction_arr[0] = int(direction)

    stop_mult = np.full(n_bars, 2.0, dtype=np.float64)
    trail_mult = np.full(n_bars, 0.0, dtype=np.float64)
    leverage = np.full(n_bars, 1.0, dtype=np.float64)

    # armed_levels + entry_delay together force Stage 2 to arm a
    # price-triggered Order rather than open a Position directly. We
    # set armed_levels[0] to the bar close so the trigger is
    # immediate-ish yet the code takes the "has_armed_level" branch
    # (avoiding the latent BAR_CLOSE/TriggerType pre-bind scope bug).
    armed_levels = np.full(n_bars, np.nan, dtype=np.float64)
    armed_levels[0] = _PRICES[token][0]
    armed_direction = np.zeros(n_bars, dtype=np.int8)
    armed_direction[0] = int(direction)
    entry_delay = np.zeros(n_bars, dtype=np.int64)

    ts = np.arange(
        0, n_bars, dtype=np.int64,
    ) * int(3_600 * 1_000_000_000) + 1_700_000_000_000_000_000
    timestamps = ts.astype("datetime64[ns]")

    return TokenBarArrays(
        token=token,
        strategy_id="parity",
        n_bars=n_bars,
        timestamps=timestamps,
        entry_mask=entry_mask,
        direction=direction_arr,
        close=close,
        high=high,
        low=low,
        atr=atr,
        rolling_adv=rolling_adv,
        funding_1h=funding_1h,
        stop_mult=stop_mult,
        trail_mult=trail_mult,
        target_mult=3.0,
        no_stop_bars=0,
        min_hold=0,
        max_hold=240,
        edge=0.50,
        leverage=leverage,
        armed_levels=armed_levels,
        armed_direction=armed_direction,
        entry_delay=entry_delay,
        volume=volume_arr,
    )


def _populate_cache_for_token(cache, clock, token: str) -> None:
    """Push one BarData event into the unified cache so the native path
    can source current-bar OHLC/volume via ``MarketDataCache``.
    """
    from v5.bar_spec import BarSpec
    from v5.data.streams import BarData, InstrumentId, Venue

    ts = clock.now_ns()
    close_v, high_v, low_v = _PRICES[token]
    vol_v = _VOLUME[token]

    inst = InstrumentId(symbol=token, venue=Venue.BINANCE, asset_class="perp")
    bs = BarSpec.from_minutes(60)

    cache.on_data(
        BarData(
            instrument=inst, ts_event=ts, ts_init=ts + 1,
            bar_spec=bs,
            open=close_v, high=high_v, low=low_v,
            close=close_v, volume=vol_v,
        )
    )


def build_parity_fixture(
    *,
    seed: int = 42,
    n_bars: int = 48,
) -> Dict[str, Any]:
    """Build the deterministic M11 AC-5 parity fixture.

    Returns a dict:

      * ``legacy_args`` — positional tuple for ``_process_orders`` that
        mutates a ``SimulationState`` in place. The wrapper installed
        atop the legacy body returns that ``state`` so the test reads
        ``state.open_orders``.
      * ``native_args`` — positional tuple for ``_process_orders_native``
        that mutates a fresh ``SimulationState`` in place and returns
        ``sim``.

    Both paths see the same signal set (3 tokens) + share one
    ``PortfolioConfig``. Expected observable outcome: each path arms
    3 orders on ``open_orders`` with identical token + direction.
    """
    from v5.config import PortfolioConfig
    from v5.config import StrategySpec as ConfigStrategySpec
    from v5.data.cache import MarketDataCache
    from v5.simulator import SimulationState
    from v5.sizing.intents import SizingIntent, SizingRequest
    from v5.strategy_api import TokenSignal, UniverseSignals

    n_bars = int(n_bars)
    strategy_id = "parity"

    # Shared config — concentration / min_size are loose so no clamp
    # rejects on either path (fixture goal is structural + behavioral
    # parity on the accept path). Arbitration policy is PriorityDesc
    # rather than the default RandomShuffle so legacy-path ordering
    # is deterministic by (priority desc, strategy_id, token) — this
    # matches the native path's dict-insertion-order iteration when
    # all priorities tie at 0.0, giving bit-identical order.
    from v5.arbitration import PriorityDesc
    config = PortfolioConfig(
        concentration_limit=0.80,
        adv_cap_pct=0.20,
        min_position_usd=100.0,
        arbitration_policy=PriorityDesc(),
    )

    # Fresh independent states — mutations must not alias across paths.
    legacy_state = SimulationState(initial_capital=1_000_000.0)
    legacy_state.max_equity_watermark = legacy_state.portfolio_equity
    native_state = SimulationState(initial_capital=1_000_000.0)
    native_state.max_equity_watermark = native_state.portfolio_equity
    # Native clamps read the config from sim._config (legacy takes it
    # as a positional arg already).
    native_state._config = config

    # --- Populate the unified cache for the native path --------------
    clock = _ParityClock(_bar_idx=0)
    cache = MarketDataCache(clock=clock)
    for token, _direction in _TOKENS:
        _populate_cache_for_token(cache, clock, token)

    # --- Legacy batch-shape inputs -----------------------------------
    all_signals: Dict[str, Dict[str, Any]] = {strategy_id: {}}
    for token, direction in _TOKENS:
        all_signals[strategy_id][token] = _build_token_bar_arrays(
            token=token, direction=direction, n_bars=n_bars,
        )

    bar_maps: Dict[str, np.ndarray] = {}
    identity_map = np.arange(n_bars, dtype=np.int64)
    for token, _direction in _TOKENS:
        bar_maps[token] = identity_map

    strategy_specs: Dict[str, ConfigStrategySpec] = {
        strategy_id: ConfigStrategySpec(
            strategy_id=strategy_id,
            market="perp",
            max_positions=10,
            max_positions_per_symbol=1,
            weight=1.0,
        ),
    }

    period_ns = 3_600 * 1_000_000_000
    start_ns = 1_700_000_000_000_000_000
    unified_ts = np.arange(
        start_ns, start_ns + n_bars * period_ns, period_ns, dtype=np.int64,
    ).astype("datetime64[ns]")

    rng = np.random.RandomState(int(seed))

    # --- Native UniverseSignals --------------------------------------
    signals_by_token: Dict[str, TokenSignal] = {}
    for token, direction in _TOKENS:
        close_v = _PRICES[token][0]
        # Choose a notional that passes both adv_cap and concentration
        # gates on all tokens: 2% of the equity (=$20k).
        signals_by_token[token] = TokenSignal(
            token=token,
            direction=int(direction),
            sizing=SizingRequest(
                intent=SizingIntent.FIXED_NOTIONAL,
                notional_usd=20_000.0,
                leverage=1.0,
            ),
            stop_mult=2.0,
            trail_mult=0.0,
            target_mult=3.0,
            min_hold=0,
            max_hold=240,
        )
    universe_signals = UniverseSignals(bar_idx=0, signals=signals_by_token)

    # Minimal ctx wrapping cache — the native path routes through
    # ctx.cache per ``_resolve_market_cache``.
    class _NativeCtx:
        def __init__(self, mkt_cache, bar_idx: int):
            self.cache = mkt_cache
            self.data = None
            self._bar_idx = int(bar_idx)

    ctx = _NativeCtx(cache, bar_idx=0)

    legacy_args = (
        legacy_state, all_signals, strategy_specs, bar_maps,
        0, config, rng, unified_ts,
    )
    native_args = (
        native_state, {strategy_id: universe_signals}, ctx, 0, strategy_specs,
    )

    return {
        "legacy_args": legacy_args,
        "native_args": native_args,
        "legacy_state": legacy_state,
        "native_state": native_state,
        "strategy_id": strategy_id,
        "tokens": _TOKENS,
        "n_bars": n_bars,
        "seed": int(seed),
    }


# Backward-compat alias — the Phase-3 parity test imports this symbol
# from ``v5.run_backtest._build_parity_fixture``. The canonical module
# is this one; ``v5.run_backtest`` re-exports for test import stability.
_build_parity_fixture = build_parity_fixture
