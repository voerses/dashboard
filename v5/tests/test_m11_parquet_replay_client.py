"""M11 AC-9 infra — `ParquetReplayClient` serves BarData + FundingRateData.

All tests RED today — the current `ParquetReplayClient.replay()` returns an
empty iterator (stub). Tests specify the post-M11 surface:

  - Yields BarData / FundingRateData subclass instances (not `_ReplayBar`)
  - Monotonic ts_event ordering
  - Time-window filtering [start_ns, end_ns)
  - De-duplication on (ts_event, instrument)
  - Registry registration for both data classes at BINANCE venue
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from v5.bar_spec import BarSpec
from v5.data.streams import InstrumentId, Venue


def _inst(sym: str = "BTCUSDT") -> InstrumentId:
    return InstrumentId(symbol=sym, venue=Venue.BINANCE, asset_class="perp")


@pytest.fixture
def bar_parquet_fixture(tmp_path: Path):
    """Write a tiny 1h bar parquet to tmp_path and return (root, inst, bar_spec)."""
    root = tmp_path / "fixture_data"
    cache_dir = root / "perp" / "1h_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    # 6 hourly bars from 2026-01-01T00:00
    start_ns = pd.Timestamp("2026-01-01T00:00:00Z").value
    rows = []
    for i in range(6):
        ts_ns = start_ns + i * 3_600 * 1_000_000_000
        rows.append({
            "timestamp": pd.Timestamp(ts_ns),
            "open": 100.0 + i,
            "high": 101.0 + i,
            "low": 99.0 + i,
            "close": 100.5 + i,
            "volume": 1_000.0,
        })
    df = pd.DataFrame(rows).set_index("timestamp")
    df.to_parquet(cache_dir / "BTCUSDT_1h.parquet")

    return root, _inst("BTCUSDT"), BarSpec.from_minutes(60)


@pytest.fixture
def funding_parquet_fixture(tmp_path: Path):
    """Write a funding-rate parquet so the replay client can serve
    FundingRateData events."""
    root = tmp_path / "fixture_data"
    funding_dir = root / "perp" / "funding"
    funding_dir.mkdir(parents=True, exist_ok=True)

    start_ns = pd.Timestamp("2026-01-01T00:00:00Z").value
    rows = []
    for i in range(4):
        ts_ns = start_ns + i * 8 * 3_600 * 1_000_000_000
        rows.append({
            "timestamp": pd.Timestamp(ts_ns),
            "funding_rate": 0.0001 * (i + 1),
            "next_funding_ts": ts_ns + 8 * 3_600 * 1_000_000_000,
        })
    df = pd.DataFrame(rows).set_index("timestamp")
    df.to_parquet(funding_dir / "BTCUSDT_funding.parquet")

    return root, _inst("BTCUSDT")


# ----------------------------------------------------------------------
# BarData replay
# ----------------------------------------------------------------------


def test_replay_yields_bar_data_in_order(bar_parquet_fixture):
    """replay() emits BarData events in monotonic ts_event order."""
    from v5.data.clients.parquet_replay import ParquetReplayClient
    from v5.data.streams import BarData, DataStream

    root, inst, bs = bar_parquet_fixture
    client = ParquetReplayClient(fixture_root=root)
    stream = DataStream(instrument=inst, data_class=BarData, bar_spec=bs)

    start_ns = pd.Timestamp("2026-01-01T00:00:00Z").value
    end_ns = start_ns + 6 * 3_600 * 1_000_000_000

    events = list(client.replay(stream, start_ns, end_ns))
    assert len(events) == 6
    ts_list = [e.ts_event for e in events]
    assert ts_list == sorted(ts_list)
    assert all(isinstance(e, BarData) for e in events)


def test_replay_filters_time_window(bar_parquet_fixture):
    """replay yields only events within [start_ns, end_ns)."""
    from v5.data.clients.parquet_replay import ParquetReplayClient
    from v5.data.streams import BarData, DataStream

    root, inst, bs = bar_parquet_fixture
    client = ParquetReplayClient(fixture_root=root)
    stream = DataStream(instrument=inst, data_class=BarData, bar_spec=bs)

    # Narrow window — only bars 1..3 (ts offsets 1h, 2h, 3h)
    base = pd.Timestamp("2026-01-01T00:00:00Z").value
    start = base + 1 * 3_600 * 1_000_000_000
    end = base + 4 * 3_600 * 1_000_000_000
    events = list(client.replay(stream, start, end))
    # Exactly 3 events (hours 1, 2, 3) — end is exclusive
    assert len(events) == 3
    for e in events:
        assert start <= e.ts_event < end


def test_replay_yields_bar_data_subclass_instance(bar_parquet_fixture):
    """Events are `BarData` instances — NOT legacy `_ReplayBar`."""
    from v5.data.clients.parquet_replay import ParquetReplayClient
    from v5.data.streams import BarData, DataStream

    root, inst, bs = bar_parquet_fixture
    client = ParquetReplayClient(fixture_root=root)
    stream = DataStream(instrument=inst, data_class=BarData, bar_spec=bs)
    start_ns = pd.Timestamp("2026-01-01T00:00:00Z").value
    end_ns = start_ns + 6 * 3_600 * 1_000_000_000

    events = list(client.replay(stream, start_ns, end_ns))
    assert events, "replay must yield at least one event"
    for e in events:
        assert isinstance(e, BarData), (
            f"expected BarData, got {type(e).__name__} — _ReplayBar must be "
            f"deleted per M11 commit 3.3"
        )
    # Also assert _ReplayBar is gone from the module
    import v5.data.clients.parquet_replay as mod
    assert not hasattr(mod, "_ReplayBar"), (
        "`_ReplayBar` dataclass must be deleted — ParquetReplayClient yields "
        "BarData subclass instances directly."
    )


def test_replay_deduplicates_on_ts_instrument(tmp_path, bar_parquet_fixture):
    """Duplicate (ts_event, instrument) events are yielded at most once."""
    from v5.data.clients.parquet_replay import ParquetReplayClient
    from v5.data.streams import BarData, DataStream

    root, inst, bs = bar_parquet_fixture
    # Duplicate the existing parquet under `live/` to exercise dedup across sources
    cache_path = root / "perp" / "1h_cache" / "BTCUSDT_1h.parquet"
    dup_dir = root / "perp" / "live"
    dup_dir.mkdir(parents=True, exist_ok=True)
    # Copy verbatim — every (ts, inst) now appears twice across sources
    import shutil
    shutil.copyfile(cache_path, dup_dir / "BTCUSDT.parquet")

    client = ParquetReplayClient(fixture_root=root)
    stream = DataStream(instrument=inst, data_class=BarData, bar_spec=bs)
    start_ns = pd.Timestamp("2026-01-01T00:00:00Z").value
    end_ns = start_ns + 6 * 3_600 * 1_000_000_000

    events = list(client.replay(stream, start_ns, end_ns))
    ts_pairs = [(e.ts_event, e.instrument) for e in events]
    assert len(ts_pairs) == len(set(ts_pairs)), (
        "replay must de-dup on (ts_event, instrument)"
    )


# ----------------------------------------------------------------------
# FundingRateData replay
# ----------------------------------------------------------------------


def test_replay_yields_funding_rate_data(funding_parquet_fixture):
    """Subscribing to FundingRateData emits FundingRateData events."""
    from v5.data.clients.parquet_replay import ParquetReplayClient
    from v5.data.streams import DataStream, FundingRateData

    root, inst = funding_parquet_fixture
    client = ParquetReplayClient(fixture_root=root)
    stream = DataStream(instrument=inst, data_class=FundingRateData)

    start_ns = pd.Timestamp("2026-01-01T00:00:00Z").value
    end_ns = start_ns + 4 * 8 * 3_600 * 1_000_000_000

    events = list(client.replay(stream, start_ns, end_ns))
    assert len(events) >= 1
    assert all(isinstance(e, FundingRateData) for e in events)


# ----------------------------------------------------------------------
# Registry registration for both kinds
# ----------------------------------------------------------------------


def test_registered_serves_both_kinds():
    """One `ParquetReplayClient` registered for (BINANCE, BarData) AND
    (BINANCE, FundingRateData) — registry lookup returns it for both."""
    from v5.data.clients.parquet_replay import ParquetReplayClient
    from v5.data.registry import DataClientRegistry
    from v5.data.streams import BarData, FundingRateData

    reg = DataClientRegistry()
    factory = lambda _cfg: ParquetReplayClient()
    reg.register(Venue.BINANCE, BarData, factory)
    reg.register(Venue.BINANCE, FundingRateData, factory)

    inst = _inst()
    bar_clients = reg.get_clients(inst, BarData)
    fund_clients = reg.get_clients(inst, FundingRateData)
    assert bar_clients, "ParquetReplayClient must be registered for BarData"
    assert fund_clients, "ParquetReplayClient must be registered for FundingRateData"
