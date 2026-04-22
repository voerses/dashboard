"""M11 AC-9 — `v5.run_backtest.run_backtest(...)` orchestrator.

All tests RED today — the orchestrator module does not exist. Pins:

  - End-to-end smoke: minimal `run_backtest(strategies, instruments, start,
    end)` returns SimulationState
  - Honors each strategy's `required_data()` subscriptions
  - Registers both ParquetReplayClient (for BarData + FundingRateData)
    and ParquetMetricsReplayClient (for MetricData)
  - DataEngine runs in REPLAY transport mode
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from v5.bar_spec import BarSpec
from v5.data.streams import InstrumentId, Venue


def _inst(sym: str = "BTCUSDT") -> InstrumentId:
    return InstrumentId(symbol=sym, venue=Venue.BINANCE, asset_class="perp")


@pytest.fixture
def tiny_bar_fixture(tmp_path: Path):
    """Write one 1h parquet for BTCUSDT and ETHUSDT — 48 bars = 2 days."""
    root = tmp_path / "fixture_data"
    cache_dir = root / "perp" / "1h_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    start_ns = pd.Timestamp("2026-01-01T00:00:00Z").value
    for token, base_price in [("BTCUSDT", 100.0), ("ETHUSDT", 50.0)]:
        rows = []
        for i in range(48):
            ts_ns = start_ns + i * 3_600 * 1_000_000_000
            rows.append({
                "timestamp": pd.Timestamp(ts_ns),
                "open": base_price + i,
                "high": base_price + i + 1,
                "low": base_price + i - 1,
                "close": base_price + i + 0.5,
                "volume": 1_000.0,
            })
        df = pd.DataFrame(rows).set_index("timestamp")
        df.to_parquet(cache_dir / f"{token}_1h.parquet")
    return root, start_ns


class _TinyStrategy:
    """Minimal Protocol-conformant strategy. Emits a buy every 6 bars."""
    id = "tiny"
    strategy_id = "tiny"

    def __init__(self, instrument: InstrumentId):
        self._instrument = instrument

    def required_data(self):
        from v5.data.streams import BarData, DataStream, Subscription

        stream = DataStream(
            instrument=self._instrument, data_class=BarData,
            bar_spec=BarSpec.from_minutes(60),
        )
        return [Subscription(stream=stream, handler=lambda _: None)]

    def generate(self, ctx, bar_idx):
        from v5.strategy_api import UniverseSignals
        return UniverseSignals(bar_idx=bar_idx, signals={})


# ----------------------------------------------------------------------
# end-to-end smoke
# ----------------------------------------------------------------------


def test_end_to_end_smoke(tiny_bar_fixture):
    """run_backtest returns a SimulationState for the fixture window."""
    from v5.config import PortfolioConfig
    from v5.data.metrics import BUILT_IN_MANIFEST
    from v5.run_backtest import run_backtest

    root, start_ns = tiny_bar_fixture
    end_ns = start_ns + 48 * 3_600 * 1_000_000_000

    btc = _inst("BTCUSDT")
    strategy = _TinyStrategy(btc)

    state = run_backtest(
        strategies=[strategy],
        instruments=[btc],
        start_ns=start_ns,
        end_ns=end_ns,
        manifest=BUILT_IN_MANIFEST,
        config=PortfolioConfig(strategies=[], capital=100_000.0, seed=42),
        fixture_root=root,
    )
    assert state is not None
    # It's okay for closed_trades to be empty; what matters is the call
    # produced a SimulationState-shaped object
    assert hasattr(state, "closed_trades") or hasattr(state, "position_manager")


def test_subscribes_via_required_data(tiny_bar_fixture):
    """Orchestrator consults each strategy's `required_data()` to subscribe."""
    from v5.config import PortfolioConfig
    from v5.data.metrics import BUILT_IN_MANIFEST
    from v5.run_backtest import run_backtest

    root, start_ns = tiny_bar_fixture
    end_ns = start_ns + 12 * 3_600 * 1_000_000_000
    btc = _inst("BTCUSDT")

    calls = {"n_required_data": 0}

    class _Observing(_TinyStrategy):
        def required_data(self):
            calls["n_required_data"] += 1
            return super().required_data()

    strategy = _Observing(btc)
    run_backtest(
        strategies=[strategy],
        instruments=[btc],
        start_ns=start_ns,
        end_ns=end_ns,
        manifest=BUILT_IN_MANIFEST,
        config=PortfolioConfig(strategies=[], capital=100_000.0, seed=42),
        fixture_root=root,
    )
    assert calls["n_required_data"] >= 1


# ----------------------------------------------------------------------
# client registration + transport mode
# ----------------------------------------------------------------------


def test_registers_both_parquet_clients(tiny_bar_fixture):
    """Orchestrator registers both BarData client + MetricData client."""
    from v5.data.metrics import BUILT_IN_MANIFEST
    from v5.run_backtest import _build_default_registry
    from v5.data.streams import BarData, FundingRateData, MetricData

    root, _ = tiny_bar_fixture
    reg = _build_default_registry(manifest=BUILT_IN_MANIFEST, fixture_root=root)
    assert reg.get_clients(_inst(), BarData), (
        "run_backtest orchestrator must register a client for BarData."
    )
    assert reg.get_clients(_inst(), MetricData), (
        "run_backtest orchestrator must register a client for MetricData."
    )
    # FundingRateData also served by the same ParquetReplayClient
    assert reg.get_clients(_inst(), FundingRateData), (
        "run_backtest orchestrator must register a client for FundingRateData."
    )


def test_uses_data_engine_replay_mode(tiny_bar_fixture):
    """The DataEngine instance is configured for replay (no live clients)."""
    from v5.data.metrics import BUILT_IN_MANIFEST
    from v5.data.streams import TransportMode
    from v5.run_backtest import _build_default_registry, _pick_mode_for_registry

    root, _ = tiny_bar_fixture
    reg = _build_default_registry(manifest=BUILT_IN_MANIFEST, fixture_root=root)
    mode = _pick_mode_for_registry(reg)
    assert mode == TransportMode.REPLAY
