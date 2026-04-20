"""M8 — MarketState adapter parity.

Clamps read market state via an adapter that wraps BOTH:
  - PriceMonitor (legacy, config.use_data_engine=False)
  - DataEngine (M6, config.use_data_engine=True)
  - Simulator (backtest path)

Byte-identity between the three paths required.

Canonical Protocol surface:
  class MarketState(Protocol):
      def adv(self, token) -> float: ...
      def rolling_adv(self, token, window_hours=24) -> float: ...
      def mark_price(self, token) -> float: ...
      def free_margin(self, strategy_id, policy) -> float: ...
      def liquidation_distance(self, position, leverage) -> float: ...
      def equity(self, strategy_id) -> float: ...

All tests MUST FAIL today — MarketState adapter protocol + implementations
do not exist.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


@pytest.fixture
def adapter_inputs():
    """Synthetic identical inputs fed to all three adapters."""
    return {
        "token": "BTC",
        "bar_idx": 42,
        "mark_price": 50_000.0,
        "adv": 1_000_000.0,
        "available_margin": 150_000.0,
        "equity": 150_000.0,
        "liquidation_distance_bps": 500.0,
    }


class TestMarketStateProtocol:
    """MarketState adapter protocol shape."""

    def test_protocol_importable(self):
        from v5.sizing.market_state import MarketState  # noqa: F401

    def test_protocol_declares_required_methods(self):
        from v5.sizing.market_state import MarketState
        for method in (
            "mark_price", "adv", "rolling_adv",
            "free_margin", "liquidation_distance", "equity",
        ):
            assert hasattr(MarketState, method), (
                f"MarketState Protocol must declare {method!r}"
            )


class TestAdapterByteIdentity:
    """All three adapters produce identical scalars for identical inputs."""

    def test_price_monitor_vs_data_engine_adv(self, adapter_inputs):
        from v5.sizing.market_state import (
            DataEngineMarketState, PriceMonitorMarketState,
        )
        pm = PriceMonitorMarketState.from_synthetic(adapter_inputs)
        de = DataEngineMarketState.from_synthetic(adapter_inputs)
        assert pm.adv(adapter_inputs["token"]) == de.adv(adapter_inputs["token"])

    def test_price_monitor_vs_data_engine_mark(self, adapter_inputs):
        from v5.sizing.market_state import (
            DataEngineMarketState, PriceMonitorMarketState,
        )
        pm = PriceMonitorMarketState.from_synthetic(adapter_inputs)
        de = DataEngineMarketState.from_synthetic(adapter_inputs)
        assert pm.mark_price(adapter_inputs["token"]) == de.mark_price(adapter_inputs["token"])

    def test_price_monitor_vs_data_engine_liquidation_distance(self, adapter_inputs):
        from v5.sizing.market_state import (
            DataEngineMarketState, PriceMonitorMarketState,
        )
        pm = PriceMonitorMarketState.from_synthetic(adapter_inputs)
        de = DataEngineMarketState.from_synthetic(adapter_inputs)
        assert pm.liquidation_distance(None, 10.0) == de.liquidation_distance(None, 10.0)

    def test_simulator_adapter_matches_both(self, adapter_inputs):
        from v5.sizing.market_state import (
            DataEngineMarketState, PriceMonitorMarketState,
            SimulatorMarketState,
        )
        pm = PriceMonitorMarketState.from_synthetic(adapter_inputs)
        de = DataEngineMarketState.from_synthetic(adapter_inputs)
        sim = SimulatorMarketState.from_synthetic(adapter_inputs)
        tok = adapter_inputs["token"]
        assert sim.mark_price(tok) == pm.mark_price(tok) == de.mark_price(tok)
        assert sim.adv(tok) == pm.adv(tok) == de.adv(tok)
