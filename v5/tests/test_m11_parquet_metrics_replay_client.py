"""M11 AC-9 infra — `ParquetMetricsReplayClient` serves MetricData.

All tests RED today — the client does not exist. Tests pin:

  - Yields MetricData events keyed by `metric_id`
  - Resample semantics: 'last' takes the last value in each resample window
  - Resample semantics: 'mean' averages values within the window
  - PIT timestamp alignment: ts_event = end-of-resample-window (not start)
  - Registry registration: (BINANCE, MetricData) → client
  - Unknown metric_id in the stream discriminator raises on subscribe
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from v5.data.streams import InstrumentId, Venue


def _inst(sym: str = "BTCUSDT") -> InstrumentId:
    return InstrumentId(symbol=sym, venue=Venue.BINANCE, asset_class="perp")


def _write_metrics_parquet(
    tmp_path: Path,
    metric_id: str,
    token: str = "BTCUSDT",
    rows: int = 60,
    step_seconds: int = 300,
) -> Path:
    """Write a native-5m metric parquet. Returns project-root-style path."""
    root = tmp_path / "fixture_data"
    metric_dir = root / "binance_metrics" / "5min" / metric_id
    metric_dir.mkdir(parents=True, exist_ok=True)

    start_ns = pd.Timestamp("2026-01-01T00:00:00Z").value
    vals = []
    for i in range(rows):
        ts_ns = start_ns + i * step_seconds * 1_000_000_000
        vals.append({
            "timestamp": pd.Timestamp(ts_ns),
            "value": 1_000_000.0 + i * 10_000.0,
        })
    df = pd.DataFrame(vals).set_index("timestamp")
    df.to_parquet(metric_dir / f"{token}.parquet")
    return root


# ----------------------------------------------------------------------
# Replay emits MetricData
# ----------------------------------------------------------------------


def test_replay_yields_metric_data(tmp_path: Path):
    from v5.data.clients.parquet_metrics import ParquetMetricsReplayClient
    from v5.data.metrics import BUILT_IN_MANIFEST
    from v5.data.streams import DataStream, MetricData

    root = _write_metrics_parquet(tmp_path, "binance.open_interest.5m")
    client = ParquetMetricsReplayClient(manifest=BUILT_IN_MANIFEST, fixture_root=root)

    stream = DataStream(
        instrument=_inst(),
        data_class=MetricData,
        discriminator="binance.open_interest.5m",
    )
    start_ns = pd.Timestamp("2026-01-01T00:00:00Z").value
    end_ns = start_ns + 30 * 60 * 1_000_000_000  # 30 minutes

    events = list(client.replay(stream, start_ns, end_ns))
    assert events, "replay must yield at least one MetricData event"
    assert all(isinstance(e, MetricData) for e in events)
    assert all(e.metric_id == "binance.open_interest.5m" for e in events)


# ----------------------------------------------------------------------
# Resample semantics
# ----------------------------------------------------------------------


def test_resample_semantics_last(tmp_path: Path):
    """resample_agg='last' takes the last value within each resample window."""
    from v5.data.clients.parquet_metrics import ParquetMetricsReplayClient
    from v5.data.metrics import MetricDefinition, MetricsManifest
    from v5.data.streams import DataStream, MetricData

    root = _write_metrics_parquet(
        tmp_path, "custom.metric.5m", rows=24, step_seconds=300,
    )
    mdef = MetricDefinition(
        metric_id="custom.metric.5m",
        venue="BINANCE",
        source_file_pattern="binance_metrics/5min/custom.metric.5m/{token}.parquet",
        timestamp_col="timestamp",
        value_col="value",
        native_resolution="5m",
        resample_to="1h",
        resample_agg="last",
        dtype="float64",
    )
    manifest = MetricsManifest(definitions={mdef.metric_id: mdef})
    client = ParquetMetricsReplayClient(manifest=manifest, fixture_root=root)

    stream = DataStream(
        instrument=_inst(), data_class=MetricData,
        discriminator="custom.metric.5m",
    )
    start_ns = pd.Timestamp("2026-01-01T00:00:00Z").value
    end_ns = start_ns + 2 * 3_600 * 1_000_000_000  # 2 hours

    events = list(client.replay(stream, start_ns, end_ns))
    # After resampling 5m→1h with 'last', expect 2 events (one per hour).
    assert len(events) == 2
    # First hourly bucket closes at +1h; 'last' value is from minute 55
    # (row 11) → 1_000_000 + 11*10_000 = 1_110_000
    assert events[0].value == pytest.approx(1_110_000.0)
    # Second hourly bucket — minute 115 = row 23 → 1_230_000
    assert events[1].value == pytest.approx(1_230_000.0)


def test_resample_semantics_mean(tmp_path: Path):
    """resample_agg='mean' averages values within each resample window."""
    from v5.data.clients.parquet_metrics import ParquetMetricsReplayClient
    from v5.data.metrics import MetricDefinition, MetricsManifest
    from v5.data.streams import DataStream, MetricData

    root = _write_metrics_parquet(
        tmp_path, "custom.mean.5m", rows=24, step_seconds=300,
    )
    mdef = MetricDefinition(
        metric_id="custom.mean.5m",
        venue="BINANCE",
        source_file_pattern="binance_metrics/5min/custom.mean.5m/{token}.parquet",
        timestamp_col="timestamp",
        value_col="value",
        native_resolution="5m",
        resample_to="1h",
        resample_agg="mean",
        dtype="float64",
    )
    manifest = MetricsManifest(definitions={mdef.metric_id: mdef})
    client = ParquetMetricsReplayClient(manifest=manifest, fixture_root=root)

    stream = DataStream(
        instrument=_inst(), data_class=MetricData,
        discriminator="custom.mean.5m",
    )
    start_ns = pd.Timestamp("2026-01-01T00:00:00Z").value
    end_ns = start_ns + 1 * 3_600 * 1_000_000_000  # 1 hour

    events = list(client.replay(stream, start_ns, end_ns))
    assert len(events) == 1
    # Rows 0-11 have values 1_000_000 .. 1_110_000 (step 10_000) — mean = 1_055_000
    expected = np.mean([1_000_000.0 + i * 10_000.0 for i in range(12)])
    assert events[0].value == pytest.approx(expected, rel=1e-9)


def test_pit_timestamp_alignment(tmp_path: Path):
    """ts_event = end-of-resample-window (NOT start-of-window) so PIT
    slicing at bar_close exposes the new value immediately."""
    from v5.data.clients.parquet_metrics import ParquetMetricsReplayClient
    from v5.data.metrics import MetricDefinition, MetricsManifest
    from v5.data.streams import DataStream, MetricData

    root = _write_metrics_parquet(
        tmp_path, "binance.open_interest.5m", rows=24, step_seconds=300,
    )
    mdef = MetricDefinition(
        metric_id="binance.open_interest.5m",
        venue="BINANCE",
        source_file_pattern="binance_metrics/5min/binance.open_interest.5m/{token}.parquet",
        timestamp_col="timestamp",
        value_col="value",
        native_resolution="5m",
        resample_to="1h",
        resample_agg="last",
        dtype="float64",
    )
    manifest = MetricsManifest(definitions={mdef.metric_id: mdef})
    client = ParquetMetricsReplayClient(manifest=manifest, fixture_root=root)

    stream = DataStream(
        instrument=_inst(), data_class=MetricData,
        discriminator="binance.open_interest.5m",
    )
    start_ns = pd.Timestamp("2026-01-01T00:00:00Z").value
    end_ns = start_ns + 2 * 3_600 * 1_000_000_000

    events = list(client.replay(stream, start_ns, end_ns))
    # First event's ts_event MUST be at end of first window (+1h), not start (+0)
    hour_1_close_ns = start_ns + 3_600 * 1_000_000_000
    assert events[0].ts_event == hour_1_close_ns, (
        f"PIT alignment: first MetricData ts_event must be end-of-window "
        f"(t+1h={hour_1_close_ns}), got {events[0].ts_event}"
    )


# ----------------------------------------------------------------------
# Registry integration
# ----------------------------------------------------------------------


def test_registered_serves_metric_data():
    from v5.data.clients.parquet_metrics import ParquetMetricsReplayClient
    from v5.data.metrics import BUILT_IN_MANIFEST
    from v5.data.registry import DataClientRegistry
    from v5.data.streams import MetricData

    reg = DataClientRegistry()
    factory = lambda _cfg: ParquetMetricsReplayClient(manifest=BUILT_IN_MANIFEST)
    reg.register(Venue.BINANCE, MetricData, factory)

    clients = reg.get_clients(_inst(), MetricData)
    assert clients, "ParquetMetricsReplayClient must be registered for MetricData"


def test_unknown_metric_id_rejected(tmp_path: Path):
    """An unknown metric_id on subscribe raises before yielding events."""
    from v5.data.clients.parquet_metrics import ParquetMetricsReplayClient
    from v5.data.metrics import BUILT_IN_MANIFEST
    from v5.data.streams import DataStream, MetricData

    client = ParquetMetricsReplayClient(
        manifest=BUILT_IN_MANIFEST, fixture_root=tmp_path,
    )
    stream = DataStream(
        instrument=_inst(),
        data_class=MetricData,
        discriminator="totally.bogus.metric",
    )
    start_ns = pd.Timestamp("2026-01-01T00:00:00Z").value
    end_ns = start_ns + 3_600 * 1_000_000_000
    with pytest.raises((KeyError, ValueError)):
        # Either the generator is a function that raises immediately, or
        # iterating the first event raises — both acceptable.
        list(client.replay(stream, start_ns, end_ns))
