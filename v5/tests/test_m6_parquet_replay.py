"""M6 — ParquetReplayClient (T-D3, T-D15 / AC-D3, AC-D15).

Fixture expectation (Wave-F, not Phase 3 scope):
v5/tests/fixtures/m6_parquet_replay/ — RED today via import error on
v5.data.clients.parquet_replay.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

FIXTURE = _project_root / "v5" / "tests" / "fixtures" / "m6_parquet_replay"


def _stream():
    from v5.bar_spec import BarSpec
    from v5.data.streams import BarData, DataStream, InstrumentId, Venue
    inst = InstrumentId(symbol="BTCUSDT", venue=Venue.BINANCE, asset_class="perp")
    return DataStream(instrument=inst, data_class=BarData,
                      bar_spec=BarSpec.from_minutes(60))


class TestParquetReplayClientProtocol:
    """T-D3 / AC-D3 — implements DataClient only (no streaming)."""

    def test_connect_disconnect(self):
        from v5.data.clients.parquet_replay import ParquetReplayClient
        c = ParquetReplayClient(fixture_root=FIXTURE)
        c.connect(); c.disconnect()

    def test_supports_replay_mode(self):
        from v5.data.clients.parquet_replay import ParquetReplayClient
        from v5.data.streams import TransportMode
        assert ParquetReplayClient(fixture_root=FIXTURE).supports(_stream(), TransportMode.REPLAY) is True

    def test_replay_returns_iterator(self):
        from v5.data.clients.parquet_replay import ParquetReplayClient
        c = ParquetReplayClient(fixture_root=FIXTURE)
        c.connect()
        it = c.replay(_stream(), start_ns=0, end_ns=10**19)
        assert hasattr(it, "__iter__")


class TestParquetReplayClientUnsupportedOps:
    """T-D15 / AC-D15 — PUSH/PULL_SCHEDULED raise."""

    def test_supported_modes_exact_set(self):
        from v5.data.clients.parquet_replay import ParquetReplayClient
        from v5.data.streams import TransportMode
        c = ParquetReplayClient(fixture_root=FIXTURE)
        assert c.supported_modes == frozenset({TransportMode.REPLAY, TransportMode.PULL_ONCE})

    def test_subscribe_raises_not_implemented(self):
        from v5.data.clients.parquet_replay import ParquetReplayClient
        c = ParquetReplayClient(fixture_root=FIXTURE)
        with pytest.raises(NotImplementedError):
            c.subscribe(_stream())

    def test_subscribe_scheduled_raises_not_implemented(self):
        from v5.data.clients.parquet_replay import ParquetReplayClient
        c = ParquetReplayClient(fixture_root=FIXTURE)
        with pytest.raises(NotImplementedError):
            c.subscribe_scheduled(_stream(), interval_s=60)

    def test_supports_push_is_false(self):
        from v5.data.clients.parquet_replay import ParquetReplayClient
        from v5.data.streams import TransportMode
        assert ParquetReplayClient(fixture_root=FIXTURE).supports(_stream(), TransportMode.PUSH) is False
