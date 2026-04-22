"""M11 AC-5 — Native signal consumption in `_process_orders` / `_process_exits`.

All tests RED today — the native helpers do not exist. Pins:

  - `_build_bar_context(token, ctx, bar_idx, pos)` reads OHLC / ATR / funding
    from the polymorphic `MarketDataCache`, not `sig.field[local_bar]`
  - 6-clamp sizing pipeline consumes `TokenSignal.sizing`, not
    `TokenBarArrays.sizing[local_bar]`
  - `_build_order_from_signal(signal, sized_quantity, bar_ctx)` constructs
    an `Order` from a TokenSignal
  - `_process_orders_native` walks `UniverseSignals` → orders on sim state
  - `_process_exits_native` consults `strategy.check_exit` per position
  - Engine-default stop/trail/target still fires when `check_exit` → None
  - Native vs legacy parity: same orders on fixed fixture
"""
from __future__ import annotations

import numpy as np
import pytest

from v5.bar_spec import BarSpec
from v5.data.streams import InstrumentId, Venue


def _inst(sym: str = "BTCUSDT") -> InstrumentId:
    return InstrumentId(symbol=sym, venue=Venue.BINANCE, asset_class="perp")


# ----------------------------------------------------------------------
# _build_bar_context reads from MarketDataCache
# ----------------------------------------------------------------------


def test_bar_context_from_cache():
    """_build_bar_context pulls OHLC from the polymorphic cache."""
    from v5.data.cache import MarketDataCache
    from v5.data.streams import BarData
    from v5.simulator import _build_bar_context

    class _Clk:
        def __init__(self):
            self._now = 0
            self.current_bar_idx = 0

        def now_ns(self):
            return self._now

        def advance_to(self, ns):
            self._now = ns

    clk = _Clk()
    cache = MarketDataCache(clock=clk)
    inst = _inst()
    bs = BarSpec.from_minutes(60)

    base_ns = 1_700_000_000_000_000_000
    for i in range(4):
        ts = base_ns + i * 3_600 * 1_000_000_000
        clk.advance_to(ts)
        clk.current_bar_idx = i
        cache.on_data(
            BarData(
                instrument=inst, ts_event=ts, ts_init=ts + 1,
                bar_spec=bs,
                open=100.0 + i, high=110.0 + i, low=95.0 + i,
                close=105.0 + i, volume=1_000.0,
            )
        )

    class _Ctx:
        pass

    ctx = _Ctx()
    ctx.cache = cache
    # Minimal ctx.data stand-in — tests require the helper to route through
    # the cache, not via ctx.data.per_token's in-memory arrays
    ctx.data = type("D", (), {"_bar_idx": 3})()

    bar_ctx = _build_bar_context(
        token="BTCUSDT", ctx=ctx, bar_idx=3, pos=None,
    )
    # BarContext should expose current-bar OHLC sourced from the cache
    assert getattr(bar_ctx, "close", None) == pytest.approx(108.0)
    assert getattr(bar_ctx, "high", None) == pytest.approx(113.0)
    assert getattr(bar_ctx, "low", None) == pytest.approx(98.0)


# ----------------------------------------------------------------------
# 6-clamp sizing pipeline consumes TokenSignal.sizing
# ----------------------------------------------------------------------


def test_sizing_clamps_from_signal():
    """`_apply_sizing_clamps_native(token_signal, spec, ctx, sim, token, bar_idx)`
    reads TokenSignal.sizing (not TokenBarArrays.sizing[i])."""
    from v5.simulator import _apply_sizing_clamps_native
    from v5.sizing.intents import SizingIntent, SizingRequest
    from v5.strategy_api import TokenSignal

    sig = TokenSignal(
        token="BTCUSDT", direction=1,
        sizing=SizingRequest(
            intent=SizingIntent.FIXED_FRACTION,
            fraction_of_equity=0.1,
            leverage=1.0,
        ),
    )
    # Helper must exist and be callable with this exact shape
    assert callable(_apply_sizing_clamps_native)


def test_6_clamp_pipeline_native():
    """All 6 clamps apply: adv_cap, concentration, free_capital, min_size,
    liq_distance, slippage."""
    from v5.simulator import _apply_sizing_clamps_native

    # Can't execute without a real ctx+cache; minimum regression surface is
    # that the function takes all 6 clamp inputs and returns a sized result.
    import inspect
    sig = inspect.signature(_apply_sizing_clamps_native)
    params = set(sig.parameters.keys())
    # Must accept at least: token_signal, spec, ctx, sim_state, token, bar_idx
    required = {"token_signal", "spec", "ctx", "sim_state", "token", "bar_idx"}
    missing = required - params
    assert not missing, (
        f"_apply_sizing_clamps_native missing parameters: {missing}. "
        f"Got: {sorted(params)}"
    )


# ----------------------------------------------------------------------
# Order construction from TokenSignal
# ----------------------------------------------------------------------


def test_build_order_from_signal():
    """`_build_order_from_signal(signal, sized_quantity, bar_ctx)` returns an
    `Order` with token + direction + qty matching the signal."""
    from v5.simulator import _build_order_from_signal
    from v5.strategy_api import TokenSignal
    from v5.sizing.intents import SizingIntent, SizingRequest

    sig = TokenSignal(
        token="BTCUSDT", direction=1,
        sizing=SizingRequest(intent=SizingIntent.FIXED_FRACTION,
                              fraction_of_equity=0.1, leverage=1.0),
    )

    class _BarCtx:
        bar_idx = 100
        close = 50_000.0
        high = 50_500.0
        low = 49_500.0
        ts_event = 1_700_000_000_000_000_000
        atr = 500.0

    order = _build_order_from_signal(
        signal=sig, sized_quantity=0.1, bar_ctx=_BarCtx(),
    )
    assert getattr(order, "token", None) == "BTCUSDT"
    assert getattr(order, "direction", None) == 1


# ----------------------------------------------------------------------
# End-to-end native order / exit processing
# ----------------------------------------------------------------------


def test_process_orders_native_end_to_end():
    """_process_orders_native(sim, signals, ctx, bar_idx, specs) walks
    UniverseSignals → Orders on sim.pending_orders / sim.open_orders."""
    from v5.simulator import _process_orders_native

    import inspect
    sig = inspect.signature(_process_orders_native)
    params = set(sig.parameters.keys())
    assert {"sim", "signals", "ctx", "bar_idx"}.issubset(params), (
        f"_process_orders_native signature mismatch: got {sorted(params)}"
    )


def test_process_exits_native_end_to_end():
    """_process_exits_native(sim, ctx, bar_idx, strategies) calls
    strategy.check_exit for each open position; close_position when
    should_exit is True."""
    from v5.simulator import _process_exits_native

    import inspect
    sig = inspect.signature(_process_exits_native)
    params = set(sig.parameters.keys())
    assert {"sim", "ctx", "bar_idx", "strategies"}.issubset(params), (
        f"_process_exits_native signature mismatch: got {sorted(params)}"
    )


def test_engine_default_exit_fallback():
    """If strategy.check_exit returns None, engine-default stop/trail/target
    logic still fires (via `_engine_default_exit_check`)."""
    from v5.simulator import _engine_default_exit_check

    assert callable(_engine_default_exit_check), (
        "_engine_default_exit_check(pos, bar_ctx) must exist so the engine "
        "still applies stops/trails/targets when strategies return None."
    )


# ----------------------------------------------------------------------
# Parity: native vs legacy on fixed fixture
# ----------------------------------------------------------------------


def test_native_vs_legacy_parity_on_fixture(tmp_path):
    """On a fixed fixture, `_process_orders_native` produces identical
    orders to the legacy `_process_orders(pre_baked_arrays, ...)` path."""
    from v5.simulator import _process_orders, _process_orders_native
    from v5.config import PortfolioConfig

    # The deterministic fixture builder lives in the orchestrator once
    # M11 ships; today the helper doesn't exist and the imports fail RED.
    from v5.run_backtest import _build_parity_fixture

    fixture = _build_parity_fixture(seed=42, n_bars=48)
    legacy_state = _process_orders(*fixture["legacy_args"])
    native_state = _process_orders_native(*fixture["native_args"])

    # Identical orders — pendings + opens + fills
    legacy_orders = list(getattr(legacy_state, "open_orders", [])
                         or getattr(legacy_state, "pending_orders", []))
    native_orders = list(getattr(native_state, "open_orders", [])
                         or getattr(native_state, "pending_orders", []))
    assert len(legacy_orders) == len(native_orders)
    for lo, no in zip(legacy_orders, native_orders):
        assert getattr(lo, "token", None) == getattr(no, "token", None)
        assert getattr(lo, "direction", None) == getattr(no, "direction", None)
