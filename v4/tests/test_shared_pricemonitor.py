"""Acceptance tests for Shared WebSocket PriceMonitor feature.

Tests verify:
  - AC1-AC7: Engine-side — external PriceMonitor injection, ownership flag,
    backward compat, cleanup behavior, no-op subscription, separate aggregators
  - AC8-AC16: Runner-side — shared monitor grouping by (exchange, venue),
    _PriceFanOut dispatch, exception isolation, union subscriptions, lifecycle
    management, update_subscriptions (not disconnect/reconnect), dedicated_ws
    opt-out, reconnection fan-out delivery
  - AC17: Regression — covered by running the existing test suite (no dedicated test)
  - E2E: End-to-end test with real CandleAggregators receiving fan-out prices

All tests use synthetic data -- no real market data required.
These tests MUST FAIL until the implementation exists (RED phase).
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from v4.paper_engine import PaperPortfolioEngine
from v4.paper_config import PaperConfig
from v4.config import StrategySpec


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_test_config(**overrides) -> PaperConfig:
    """Build a minimal PaperConfig for shared-monitor tests.

    Uses exit_resolution=5 by default so the engine creates a CandleAggregator
    and (normally) a PriceMonitor.
    """
    defaults = dict(
        strategies=[
            StrategySpec(strategy_id="s56", weight=0.5, market="combined",
                         max_positions=10, exit_resolution=5),
        ],
        capital=200_000.0,
        mode="pool",
        pool_name="test",
        max_portfolio_positions=40,
        concentration_limit=0.10,
        adv_cap_pct=0.10,
        min_position_usd=200.0,
        exchange="binance",
        seed=42,
        train_bars=0,
        recal_bars=99999,
        purge_bars=0,
        lookback_months=3,
        enable_purge_windows=False,
        drawdown_alert_pct=5.0,
        stress_adv_multiplier=0.3,
        max_slip_bps=300,
    )
    defaults.update(overrides)
    return PaperConfig(**defaults)


def _make_mock_price_monitor(venue: str = "perp") -> MagicMock:
    """Build a mock PriceMonitor with the public interface methods."""
    pm = MagicMock()
    pm.connect = MagicMock()
    pm.disconnect = MagicMock()
    pm.update_subscriptions = MagicMock()
    pm.get_latest_prices = MagicMock(return_value={})
    pm._latest_prices = {}
    pm._venue = venue
    pm._ws_thread = None
    return pm


# ===================================================================
# AC1: Engine accepts optional price_monitor param
# ===================================================================

class TestAC1_ExternalPriceMonitor:
    """Engine accepts optional price_monitor param and uses it."""

    def test_engine_uses_injected_price_monitor(self):
        """When price_monitor is passed, engine._price_monitor is the injected one."""
        mock_pm = _make_mock_price_monitor()
        config = _make_test_config()
        engine = PaperPortfolioEngine(config, price_monitor=mock_pm)
        assert engine._price_monitor is mock_pm

    def test_engine_does_not_create_own_when_injected(self):
        """When price_monitor is passed, engine does NOT instantiate a new PriceMonitor."""
        mock_pm = _make_mock_price_monitor()
        config = _make_test_config()
        with patch("v4.price_monitor.PriceMonitor") as MockPM:
            engine = PaperPortfolioEngine(config, price_monitor=mock_pm)
            MockPM.assert_not_called()
            assert engine._price_monitor is mock_pm


# ===================================================================
# AC2: _owns_price_monitor flag
# ===================================================================

class TestAC2_OwnershipFlag:
    """_owns_price_monitor is True when engine creates its own, False when shared."""

    def test_owns_flag_true_when_own_monitor(self):
        """Engine that creates its own PriceMonitor has _owns_price_monitor=True."""
        config = _make_test_config()
        engine = PaperPortfolioEngine(config)
        assert engine._owns_price_monitor is True

    def test_owns_flag_false_when_shared_monitor(self):
        """Engine using injected PriceMonitor has _owns_price_monitor=False."""
        mock_pm = _make_mock_price_monitor()
        config = _make_test_config()
        engine = PaperPortfolioEngine(config, price_monitor=mock_pm)
        assert engine._owns_price_monitor is False


# ===================================================================
# AC3: Backward compatibility — no price_monitor param
# ===================================================================

class TestAC3_BackwardCompat:
    """Engine without price_monitor param creates its own (existing behavior)."""

    def test_engine_creates_own_monitor_without_param(self):
        """Constructing without price_monitor creates engine's own PriceMonitor."""
        config = _make_test_config()
        engine = PaperPortfolioEngine(config)
        # Engine should have created its own monitor (since exit_resolution > 0)
        assert engine._price_monitor is not None
        assert engine._owns_price_monitor is True

    def test_hourly_only_engine_has_no_monitor(self):
        """Engine with exit_resolution=0 has no PriceMonitor (backward compat)."""
        config = _make_test_config(
            strategies=[
                StrategySpec(strategy_id="s56", weight=0.5, market="combined",
                             max_positions=10, exit_resolution=0),
            ],
        )
        engine = PaperPortfolioEngine(config)
        assert engine._price_monitor is None


# ===================================================================
# AC4: Shared engine does NOT disconnect on cleanup
# ===================================================================

class TestAC4_SharedNoDisconnect:
    """Engine with shared PriceMonitor does NOT disconnect it on cleanup()."""

    def test_shared_engine_cleanup_does_not_disconnect(self):
        """cleanup() on engine with _owns_price_monitor=False does NOT call disconnect.

        The runner owns the lifecycle of shared monitors. Engines that don't own
        their monitor must not disconnect it — that would kill the feed for all
        other engines sharing the same connection.
        """
        mock_pm = _make_mock_price_monitor()
        config = _make_test_config()
        engine = PaperPortfolioEngine(config, price_monitor=mock_pm)
        assert engine._owns_price_monitor is False

        # cleanup() must exist and be callable
        engine.cleanup()

        mock_pm.disconnect.assert_not_called()


# ===================================================================
# AC5: Owning engine DOES disconnect on cleanup
# ===================================================================

class TestAC5_OwningDisconnects:
    """Engine that owns its PriceMonitor DOES disconnect it on cleanup()."""

    def test_owning_engine_cleanup_disconnects(self):
        """cleanup() on engine with _owns_price_monitor=True calls disconnect()."""
        config = _make_test_config()
        engine = PaperPortfolioEngine(config)
        assert engine._owns_price_monitor is True
        # Replace the real monitor with a mock so we can verify disconnect
        mock_pm = _make_mock_price_monitor()
        engine._price_monitor = mock_pm

        engine.cleanup()

        mock_pm.disconnect.assert_called_once()


# ===================================================================
# AC6: _update_ws_subscriptions() is no-op when not owning monitor
# ===================================================================

class TestAC6_SubscriptionNoOp:
    """_update_ws_subscriptions() is a no-op when engine does not own the monitor."""

    def test_update_ws_subscriptions_noop_when_shared(self):
        """Shared engine's _update_ws_subscriptions does NOT call update_subscriptions.

        This prevents the subscription clobbering bug: if engine A called
        update_subscriptions({BTC}), it would unsubscribe engine B's tokens.
        """
        mock_pm = _make_mock_price_monitor()
        config = _make_test_config()
        engine = PaperPortfolioEngine(config, price_monitor=mock_pm)
        assert engine._owns_price_monitor is False

        engine._update_ws_subscriptions()

        mock_pm.update_subscriptions.assert_not_called()

    def test_update_ws_subscriptions_noop_even_with_positions(self):
        """Shared engine with open positions still does NOT call update_subscriptions."""
        mock_pm = _make_mock_price_monitor()
        config = _make_test_config()
        engine = PaperPortfolioEngine(config, price_monitor=mock_pm)

        # Simulate open positions
        mock_state = MagicMock()
        mock_state.position_manager.open_positions = [MagicMock(token="BTC")]
        engine._get_all_states = MagicMock(return_value=[mock_state])

        engine._update_ws_subscriptions()

        mock_pm.update_subscriptions.assert_not_called()

    def test_update_ws_subscriptions_works_when_owning(self):
        """Owning engine's _update_ws_subscriptions DOES call update_subscriptions."""
        config = _make_test_config()
        engine = PaperPortfolioEngine(config)
        assert engine._owns_price_monitor is True
        # Replace with mock to verify call
        mock_pm = _make_mock_price_monitor()
        engine._price_monitor = mock_pm

        engine._update_ws_subscriptions()

        mock_pm.update_subscriptions.assert_called_once()


# ===================================================================
# AC7: Each engine retains its own CandleAggregator
# ===================================================================

class TestAC7_SeparateAggregators:
    """Each engine has its own CandleAggregator — only PriceMonitor is shared."""

    def test_shared_monitor_separate_aggregators(self):
        """Two engines sharing a PriceMonitor have distinct CandleAggregators."""
        mock_pm = _make_mock_price_monitor()

        config_5m = _make_test_config(
            pool_name="pool_5m",
            strategies=[
                StrategySpec(strategy_id="s_5m", weight=0.5, market="combined",
                             max_positions=10, exit_resolution=5),
            ],
        )
        config_30m = _make_test_config(
            pool_name="pool_30m",
            strategies=[
                StrategySpec(strategy_id="s_30m", weight=0.5, market="combined",
                             max_positions=10, exit_resolution=30),
            ],
        )

        engine_5m = PaperPortfolioEngine(config_5m, price_monitor=mock_pm)
        engine_30m = PaperPortfolioEngine(config_30m, price_monitor=mock_pm)

        # Same PriceMonitor
        assert engine_5m._price_monitor is engine_30m._price_monitor

        # Different CandleAggregators
        assert engine_5m._candle_aggregator is not engine_30m._candle_aggregator
        assert engine_5m._candle_aggregator is not None
        assert engine_30m._candle_aggregator is not None

    def test_aggregators_have_correct_resolutions(self):
        """Each engine's CandleAggregator uses the engine's own resolution."""
        mock_pm = _make_mock_price_monitor()

        config_5m = _make_test_config(
            pool_name="pool_5m",
            strategies=[
                StrategySpec(strategy_id="s_5m", weight=0.5, market="combined",
                             max_positions=10, exit_resolution=5),
            ],
        )
        config_30m = _make_test_config(
            pool_name="pool_30m",
            strategies=[
                StrategySpec(strategy_id="s_30m", weight=0.5, market="combined",
                             max_positions=10, exit_resolution=30),
            ],
        )

        engine_5m = PaperPortfolioEngine(config_5m, price_monitor=mock_pm)
        engine_30m = PaperPortfolioEngine(config_30m, price_monitor=mock_pm)

        assert engine_5m._candle_aggregator._resolution == 5
        assert engine_30m._candle_aggregator._resolution == 30


# ===================================================================
# AC8: Runner groups by (exchange, venue) — 1 PriceMonitor per group
# ===================================================================

class TestAC8_SharedMonitorGrouping:
    """Runner groups engines by (exchange, venue) and creates 1 PriceMonitor per group."""

    def test_four_engines_one_monitor_same_exchange_venue(self):
        """4 sub-hourly engines on same (exchange, venue) share 1 PriceMonitor."""
        from v4.run_paper_multi import _setup_shared_monitors

        configs = []
        for i in range(4):
            configs.append(_make_test_config(
                pool_name=f"pool_{i}",
                exchange="binance",
                strategies=[
                    StrategySpec(strategy_id=f"s{i}", weight=0.5, market="combined",
                                 max_positions=10, exit_resolution=5),
                ],
            ))

        # Runner's setup function groups by (exchange, venue), creates shared monitors
        shared_monitors, engines = _setup_shared_monitors(configs)

        # All 4 engines should share the same PriceMonitor
        monitors = {id(eng._price_monitor) for eng in engines
                    if eng._price_monitor is not None}
        assert len(monitors) == 1

        # Exactly 1 shared monitor created for ("binance", "perp")
        assert len(shared_monitors) == 1
        assert ("binance", "perp") in shared_monitors

    def test_spot_and_perp_get_separate_monitors(self):
        """Engines on different venues get separate shared monitors."""
        from v4.run_paper_multi import _setup_shared_monitors

        config_perp = _make_test_config(
            pool_name="perp_pool",
            exchange="binance",
            strategies=[
                StrategySpec(strategy_id="s_perp", weight=0.5, market="perp",
                             max_positions=10, exit_resolution=5),
            ],
        )
        config_spot = _make_test_config(
            pool_name="spot_pool",
            exchange="binance",
            strategies=[
                StrategySpec(strategy_id="s_spot", weight=0.5, market="spot",
                             max_positions=10, exit_resolution=5),
            ],
        )

        shared_monitors, engines = _setup_shared_monitors([config_perp, config_spot])

        # Two separate shared monitors: one for perp, one for spot
        assert len(shared_monitors) == 2
        assert ("binance", "perp") in shared_monitors
        assert ("binance", "spot") in shared_monitors

        # Engines have different PriceMonitors
        assert engines[0]._price_monitor is not engines[1]._price_monitor

    def test_combined_and_perp_share_same_venue(self):
        """market='combined' and market='perp' both map to venue='perp'.
        They should share the same PriceMonitor."""
        from v4.run_paper_multi import _setup_shared_monitors

        config_combined = _make_test_config(
            pool_name="combined_pool",
            exchange="binance",
            strategies=[
                StrategySpec(strategy_id="s_comb", weight=0.5, market="combined",
                             max_positions=10, exit_resolution=5),
            ],
        )
        config_perp = _make_test_config(
            pool_name="perp_pool",
            exchange="binance",
            strategies=[
                StrategySpec(strategy_id="s_perp", weight=0.5, market="perp",
                             max_positions=10, exit_resolution=5),
            ],
        )

        shared_monitors, engines = _setup_shared_monitors([config_combined, config_perp])

        # Both map to ("binance", "perp") — 1 shared monitor
        assert len(shared_monitors) == 1
        assert ("binance", "perp") in shared_monitors
        assert engines[0]._price_monitor is engines[1]._price_monitor


# ===================================================================
# AC9: _PriceFanOut dispatches to all CandleAggregators
# ===================================================================

class TestAC9_FanOutDispatch:
    """_PriceFanOut dispatches price updates to each engine's CandleAggregator."""

    def test_fanout_dispatches_to_all_callbacks(self):
        """Fan-out calls each registered callback with (token, price, timestamp)."""
        from v4.run_paper_multi import _PriceFanOut

        received_a = []
        received_b = []
        received_c = []

        fanout = _PriceFanOut([
            lambda t, p, ts: received_a.append((t, p, ts)),
            lambda t, p, ts: received_b.append((t, p, ts)),
            lambda t, p, ts: received_c.append((t, p, ts)),
        ])
        fanout("BTC", 65000.0, 1697380200000)

        assert received_a == [("BTC", 65000.0, 1697380200000)]
        assert received_b == [("BTC", 65000.0, 1697380200000)]
        assert received_c == [("BTC", 65000.0, 1697380200000)]

    def test_fanout_multiple_updates(self):
        """Fan-out dispatches multiple price updates to all callbacks."""
        from v4.run_paper_multi import _PriceFanOut

        received = []
        fanout = _PriceFanOut([lambda t, p, ts: received.append(t)])
        fanout("BTC", 65000.0, 1)
        fanout("ETH", 3400.0, 2)

        assert received == ["BTC", "ETH"]

    def test_fanout_empty_callbacks(self):
        """Fan-out with no callbacks does not raise."""
        from v4.run_paper_multi import _PriceFanOut

        fanout = _PriceFanOut([])
        fanout("BTC", 65000.0, 1)  # Should not raise


# ===================================================================
# AC10: _PriceFanOut exception isolation
# ===================================================================

class TestAC10_FanOutExceptionIsolation:
    """_PriceFanOut catches exceptions per-callback — one error does not block others."""

    def test_exception_in_one_callback_does_not_block_others(self):
        """If callback A raises, callbacks B and C still receive the update."""
        from v4.run_paper_multi import _PriceFanOut

        received_b = []
        received_c = []

        def cb_bad(token, price, ts):
            raise RuntimeError("Engine A exploded")

        fanout = _PriceFanOut([
            cb_bad,
            lambda t, p, ts: received_b.append((t, p, ts)),
            lambda t, p, ts: received_c.append((t, p, ts)),
        ])
        # Should not raise — exception is caught internally
        fanout("BTC", 65000.0, 1697380200000)

        assert received_b == [("BTC", 65000.0, 1697380200000)]
        assert received_c == [("BTC", 65000.0, 1697380200000)]

    def test_multiple_exceptions_dont_propagate(self):
        """Multiple failing callbacks don't prevent the rest from receiving."""
        from v4.run_paper_multi import _PriceFanOut

        received = []

        fanout = _PriceFanOut([
            lambda t, p, ts: (_ for _ in ()).throw(ValueError("fail 1")),
            lambda t, p, ts: (_ for _ in ()).throw(ValueError("fail 2")),
            lambda t, p, ts: received.append((t, p, ts)),
        ])
        fanout("ETH", 3400.0, 123)

        assert received == [("ETH", 3400.0, 123)]


# ===================================================================
# AC11: Runner computes union of open tokens for subscription
# ===================================================================

class TestAC11_UnionSubscription:
    """Runner computes union of all open tokens and subscribes once."""

    def test_union_of_tokens_across_engines(self):
        """Runner calls update_shared_subscriptions() with the union of all
        engines' open tokens — not per-engine.

        Engine A holds {BTC, ETH}, engine B holds {ETH, SOL}.
        Shared monitor must receive {BTC, ETH, SOL}.
        """
        from v4.run_paper_multi import _update_shared_subscriptions

        mock_pm = _make_mock_price_monitor()

        config_a = _make_test_config(pool_name="pool_a")
        config_b = _make_test_config(pool_name="pool_b")

        engine_a = PaperPortfolioEngine(config_a, price_monitor=mock_pm)
        engine_b = PaperPortfolioEngine(config_b, price_monitor=mock_pm)

        # Simulate open positions
        mock_state_a = MagicMock()
        mock_state_a.position_manager.open_positions = [
            MagicMock(token="BTC"), MagicMock(token="ETH"),
        ]
        mock_state_b = MagicMock()
        mock_state_b.position_manager.open_positions = [
            MagicMock(token="ETH"), MagicMock(token="SOL"),
        ]

        engine_a._get_all_states = MagicMock(return_value=[mock_state_a])
        engine_b._get_all_states = MagicMock(return_value=[mock_state_b])

        shared_monitors = {("binance", "perp"): mock_pm}
        engine_groups = {("binance", "perp"): [engine_a, engine_b]}

        _update_shared_subscriptions(shared_monitors, engine_groups)

        mock_pm.update_subscriptions.assert_called_once_with({"BTC", "ETH", "SOL"})

    def test_empty_token_set_when_no_positions(self):
        """When all engines have zero open positions, shared monitor gets
        update_subscriptions with empty set (or disconnect). Either is valid."""
        from v4.run_paper_multi import _update_shared_subscriptions

        mock_pm = _make_mock_price_monitor()

        config = _make_test_config(pool_name="pool_a")
        engine = PaperPortfolioEngine(config, price_monitor=mock_pm)

        # No open positions
        mock_state = MagicMock()
        mock_state.position_manager.open_positions = []
        engine._get_all_states = MagicMock(return_value=[mock_state])

        shared_monitors = {("binance", "perp"): mock_pm}
        engine_groups = {("binance", "perp"): [engine]}

        _update_shared_subscriptions(shared_monitors, engine_groups)

        # Should either update with empty set or disconnect — not leave stale subs
        if mock_pm.update_subscriptions.called:
            mock_pm.update_subscriptions.assert_called_once_with(set())
        else:
            mock_pm.disconnect.assert_called_once()


# ===================================================================
# AC12: Runner startup connects shared monitors
# ===================================================================

class TestAC12_StartupConnect:
    """Runner startup connects shared monitors with all open tokens."""

    def test_shared_monitor_connected_at_startup(self):
        """Runner calls _connect_shared_monitors() at startup with all open tokens."""
        from v4.run_paper_multi import _connect_shared_monitors

        mock_pm = _make_mock_price_monitor()

        config = _make_test_config()
        engine = PaperPortfolioEngine(config, price_monitor=mock_pm)

        # Simulate restored state with open positions
        mock_state = MagicMock()
        mock_state.position_manager.open_positions = [MagicMock(token="BTC")]
        engine._get_all_states = MagicMock(return_value=[mock_state])

        shared_monitors = {("binance", "perp"): mock_pm}
        engine_groups = {("binance", "perp"): [engine]}

        _connect_shared_monitors(shared_monitors, engine_groups)

        mock_pm.connect.assert_called_once()
        connect_args = mock_pm.connect.call_args[0][0]
        assert "BTC" in connect_args


# ===================================================================
# AC13: Per-tick uses update_subscriptions() not disconnect/reconnect
# ===================================================================

class TestAC13_UpdateNotReconnect:
    """Runner per-tick subscription update uses update_subscriptions() — NOT disconnect/reconnect.

    Bug fix: the current code (lines 520-523) does disconnect() then connect()
    every hour. With a shared monitor, this kills the feed for all engines.
    """

    def test_per_tick_uses_update_subscriptions(self):
        """After a tick, runner calls update_subscriptions() on shared monitor."""
        from v4.run_paper_multi import _update_shared_subscriptions

        mock_pm = _make_mock_price_monitor()
        mock_pm._ws_thread = MagicMock()
        mock_pm._ws_thread.is_alive.return_value = True

        config = _make_test_config()
        engine = PaperPortfolioEngine(config, price_monitor=mock_pm)

        # Simulate open positions after tick
        mock_state = MagicMock()
        mock_state.position_manager.open_positions = [
            MagicMock(token="BTC"), MagicMock(token="ETH"),
        ]
        engine._get_all_states = MagicMock(return_value=[mock_state])

        shared_monitors = {("binance", "perp"): mock_pm}
        engine_groups = {("binance", "perp"): [engine]}

        _update_shared_subscriptions(shared_monitors, engine_groups)

        mock_pm.update_subscriptions.assert_called_once_with({"BTC", "ETH"})
        mock_pm.disconnect.assert_not_called()


# ===================================================================
# AC14: Runner shutdown disconnects shared monitors
# ===================================================================

class TestAC14_ShutdownDisconnect:
    """Runner shutdown disconnects all shared monitors (deduplicated)."""

    def test_shared_monitors_disconnected_on_shutdown(self):
        """Each shared PriceMonitor is disconnected exactly once on shutdown."""
        from v4.run_paper_multi import _disconnect_shared_monitors

        mock_pm = _make_mock_price_monitor()
        shared_monitors = {("binance", "perp"): mock_pm}

        _disconnect_shared_monitors(shared_monitors)

        mock_pm.disconnect.assert_called_once()

    def test_multiple_venue_monitors_all_disconnected(self):
        """Shutdown disconnects each venue's monitor exactly once."""
        from v4.run_paper_multi import _disconnect_shared_monitors

        mock_perp = _make_mock_price_monitor(venue="perp")
        mock_spot = _make_mock_price_monitor(venue="spot")
        shared_monitors = {
            ("binance", "perp"): mock_perp,
            ("binance", "spot"): mock_spot,
        }

        _disconnect_shared_monitors(shared_monitors)

        mock_perp.disconnect.assert_called_once()
        mock_spot.disconnect.assert_called_once()


# ===================================================================
# AC15: dedicated_ws opts out of sharing
# ===================================================================

class TestAC15_DedicatedWsOptOut:
    """dedicated_ws in per-portfolio config opts a pool out of sharing.

    dedicated_ws is on PaperConfig (default False). The runner reads it
    to decide whether to inject a shared monitor. The engine itself never
    reads it — it only sees the price_monitor param (or lack thereof).
    """

    def test_dedicated_pool_gets_own_monitor(self):
        """When runner sees dedicated_ws=True, it does NOT pass a shared monitor.
        The engine creates its own (default behavior)."""
        config = _make_test_config(pool_name="dedicated_pool", dedicated_ws=True)
        # Runner does NOT pass price_monitor for dedicated pools
        engine = PaperPortfolioEngine(config)
        assert engine._price_monitor is not None
        assert engine._owns_price_monitor is True

    def test_non_dedicated_pool_uses_shared_monitor(self):
        """When runner sees dedicated_ws=False (default), it injects a shared monitor."""
        mock_pm = _make_mock_price_monitor()
        config = _make_test_config(pool_name="shared_pool")
        assert config.dedicated_ws is False
        # Runner passes shared price_monitor for non-dedicated pools
        engine = PaperPortfolioEngine(config, price_monitor=mock_pm)
        assert engine._price_monitor is mock_pm
        assert engine._owns_price_monitor is False

    def test_mixed_dedicated_and_shared(self):
        """Mix of dedicated and shared engines: dedicated gets own, shared uses injected."""
        mock_pm = _make_mock_price_monitor()

        config_shared = _make_test_config(pool_name="shared_pool")
        config_dedicated = _make_test_config(pool_name="dedicated_pool", dedicated_ws=True)

        engine_shared = PaperPortfolioEngine(config_shared, price_monitor=mock_pm)
        engine_dedicated = PaperPortfolioEngine(config_dedicated)

        assert engine_shared._price_monitor is mock_pm
        assert engine_shared._owns_price_monitor is False

        assert engine_dedicated._price_monitor is not mock_pm
        assert engine_dedicated._owns_price_monitor is True

    def test_dedicated_ws_parsed_from_json_config(self):
        """load_multi_config parses dedicated_ws from portfolio JSON."""
        from v4.run_paper_multi import load_multi_config

        config_data = {
            "shared": {
                "capital": 200000.0,
                "max_portfolio_positions": 40,
                "concentration_limit": 0.10,
                "adv_cap_pct": 0.10,
                "min_position_usd": 200.0,
                "exchange": "binance",
                "seed": 42,
                "train_bars": 0,
                "recal_bars": 99999,
                "purge_bars": 0,
                "lookback_months": 3,
                "stress_adv_multiplier": 0.3,
                "max_slip_bps": 300,
            },
            "portfolios": [
                {
                    "pool_name": "shared_pool",
                    "strategies": [{"strategy_id": "s56", "weight": 0.5, "market": "combined",
                                    "max_positions": 10, "exit_resolution": 5}],
                },
                {
                    "pool_name": "dedicated_pool",
                    "dedicated_ws": True,
                    "strategies": [{"strategy_id": "s98", "weight": 0.5, "market": "combined",
                                    "max_positions": 10, "exit_resolution": 30}],
                },
            ],
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(config_data, f)
            tmp_path = f.name

        try:
            configs = load_multi_config(tmp_path)
            assert configs[0].dedicated_ws is False
            assert configs[1].dedicated_ws is True
        finally:
            os.unlink(tmp_path)

    def test_setup_shared_monitors_skips_dedicated(self):
        """_setup_shared_monitors excludes dedicated_ws pools from sharing."""
        from v4.run_paper_multi import _setup_shared_monitors

        configs = [
            _make_test_config(pool_name="shared_1", dedicated_ws=False),
            _make_test_config(pool_name="shared_2", dedicated_ws=False),
            _make_test_config(pool_name="dedicated", dedicated_ws=True),
        ]

        shared_monitors, engines = _setup_shared_monitors(configs)

        # Shared engines use the same monitor
        assert engines[0]._price_monitor is engines[1]._price_monitor
        assert engines[0]._owns_price_monitor is False

        # Dedicated engine has its own monitor
        assert engines[2]._price_monitor is not engines[0]._price_monitor
        assert engines[2]._owns_price_monitor is True


# ===================================================================
# AC16: Reconnection delivers to all engines via fan-out
# ===================================================================

class TestAC16_ReconnectionFanOut:
    """Shared monitor reconnection delivers gap-fill prices to all engines."""

    def test_reconnection_callback_reaches_all_aggregators(self):
        """After simulated reconnect, calling the fan-out callback delivers
        to all registered CandleAggregators."""
        from v4.run_paper_multi import _PriceFanOut

        received_a = []
        received_b = []

        fanout = _PriceFanOut([
            lambda t, p, ts: received_a.append((t, p, ts)),
            lambda t, p, ts: received_b.append((t, p, ts)),
        ])

        # Simulate reconnection: gap-fill prices from REST + live price
        fanout("BTC", 64500.0, 1697380100000)
        fanout("BTC", 64800.0, 1697380150000)
        fanout("BTC", 65000.0, 1697380200000)

        assert len(received_a) == 3
        assert len(received_b) == 3
        assert received_a[0] == ("BTC", 64500.0, 1697380100000)
        assert received_a[2] == ("BTC", 65000.0, 1697380200000)
        assert received_b[0] == ("BTC", 64500.0, 1697380100000)
        assert received_b[2] == ("BTC", 65000.0, 1697380200000)

    def test_reconnection_with_failing_aggregator(self):
        """Gap-fill prices still reach healthy engines even if one is broken."""
        from v4.run_paper_multi import _PriceFanOut

        received = []

        def cb_broken(token, price, ts):
            raise RuntimeError("Aggregator broken")

        fanout = _PriceFanOut([cb_broken, lambda t, p, ts: received.append((t, p, ts))])
        fanout("BTC", 64500.0, 1697380100000)

        assert received == [("BTC", 64500.0, 1697380100000)]


# ===================================================================
# E2E: End-to-end test with real CandleAggregators
# ===================================================================

class TestE2E_SharedMonitorFanOut:
    """End-to-end test: shared PriceMonitor fan-out through real CandleAggregators.

    Uses real CandleAggregator instances (not mocks) to verify that price
    updates flow correctly from a single fan-out through to candle completion.
    """

    def test_fanout_to_real_candle_aggregators_different_resolutions(self):
        """Price ticks fan out through _PriceFanOut to real CandleAggregators
        with different resolutions (5m and 30m). Both receive all ticks,
        but complete candles at different intervals."""
        from v4.run_paper_multi import _PriceFanOut
        from v4.candle_aggregator import CandleAggregator

        agg_5m = CandleAggregator(5)
        agg_30m = CandleAggregator(30)

        fanout = _PriceFanOut([agg_5m.on_price, agg_30m.on_price])

        # 5m interval 0: timestamps in [0, 300)s → interval_key=0
        # 5m interval 1: timestamps in [300, 600)s → interval_key=1
        # 30m interval 0: timestamps in [0, 1800)s → interval_key=0
        fanout("BTC", 65000.0, 60_000)    # t=60s, 5m key=0, 30m key=0
        fanout("BTC", 65500.0, 120_000)   # t=120s
        fanout("BTC", 64800.0, 180_000)   # t=180s

        # Cross 5m boundary — triggers candle completion for 5m aggregator
        fanout("BTC", 65200.0, 300_000)   # t=300s, 5m key=1, 30m key=0

        # 5m aggregator: completed candle from first 5m interval
        completed_5m = agg_5m.flush_completed()
        assert "BTC" in completed_5m
        h, l, c = completed_5m["BTC"]
        assert h == 65500.0
        assert l == 64800.0
        assert c == 64800.0

        # 30m aggregator: still in first interval, no completed candle
        completed_30m = agg_30m.flush_completed()
        assert "BTC" not in completed_30m

    def test_fanout_to_engines_with_real_aggregators(self):
        """Two engines with different exit resolutions share one PriceMonitor.
        Fan-out delivers ticks to both engines' real CandleAggregators."""
        from v4.run_paper_multi import _PriceFanOut

        mock_pm = _make_mock_price_monitor()

        config_5m = _make_test_config(
            pool_name="pool_5m",
            strategies=[
                StrategySpec(strategy_id="s_5m", weight=0.5, market="combined",
                             max_positions=10, exit_resolution=5),
            ],
        )
        config_30m = _make_test_config(
            pool_name="pool_30m",
            strategies=[
                StrategySpec(strategy_id="s_30m", weight=0.5, market="combined",
                             max_positions=10, exit_resolution=30),
            ],
        )

        engine_5m = PaperPortfolioEngine(config_5m, price_monitor=mock_pm)
        engine_30m = PaperPortfolioEngine(config_30m, price_monitor=mock_pm)

        assert engine_5m._candle_aggregator is not None
        assert engine_30m._candle_aggregator is not None

        fanout = _PriceFanOut([
            engine_5m._candle_aggregator.on_price,
            engine_30m._candle_aggregator.on_price,
        ])

        fanout("ETH", 3400.0, 60_000)
        fanout("ETH", 3450.0, 120_000)
        fanout("ETH", 3380.0, 180_000)
        fanout("ETH", 3420.0, 300_000)  # crosses 5m boundary

        completed_5m = engine_5m._candle_aggregator.flush_completed()
        assert "ETH" in completed_5m
        h, l, c = completed_5m["ETH"]
        assert h == 3450.0
        assert l == 3380.0
        assert c == 3380.0

        completed_30m = engine_30m._candle_aggregator.flush_completed()
        assert "ETH" not in completed_30m

    def test_exception_isolation_with_real_aggregators(self):
        """One broken callback does not prevent a real CandleAggregator from receiving."""
        from v4.run_paper_multi import _PriceFanOut
        from v4.candle_aggregator import CandleAggregator

        agg = CandleAggregator(5)

        def broken_callback(token, price, ts):
            raise RuntimeError("This engine is broken")

        fanout = _PriceFanOut([broken_callback, agg.on_price])

        fanout("BTC", 65000.0, 60_000)
        fanout("BTC", 65500.0, 120_000)
        fanout("BTC", 65200.0, 300_000)  # crosses 5m boundary

        completed = agg.flush_completed()
        assert "BTC" in completed
        h, l, c = completed["BTC"]
        assert h == 65500.0
        assert l == 65000.0
