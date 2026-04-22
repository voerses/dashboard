"""M11 AC-9 infra — `MetricsManifest` + `MetricDefinition` + builtin seed.

All tests RED today — `v5.data.metrics` does not exist. Pins:

  - `MetricDefinition` frozen dataclass with required fields
  - `BUILT_IN_MANIFEST` contains 3 seed entries
  - `manifest.get(metric_id)` returns a `MetricDefinition`
  - YAML load + dump roundtrips preserve the manifest
"""
from __future__ import annotations

from pathlib import Path

import pytest


# ----------------------------------------------------------------------
# MetricDefinition
# ----------------------------------------------------------------------


def test_metric_definition_fields():
    """MetricDefinition has required fields; frozen dataclass."""
    from v5.data.metrics import MetricDefinition

    mdef = MetricDefinition(
        metric_id="binance.open_interest.5m",
        venue="BINANCE",
        source_file_pattern="binance_metrics/5min/open_interest/{token}.parquet",
        timestamp_col="timestamp",
        value_col="value",
        native_resolution="5m",
        resample_to="1h",
        resample_agg="last",
        dtype="float64",
    )
    # All fields populated
    assert mdef.metric_id == "binance.open_interest.5m"
    assert mdef.venue == "BINANCE"
    assert mdef.native_resolution == "5m"
    assert mdef.resample_to == "1h"
    assert mdef.resample_agg == "last"

    # Must be frozen — attempting to mutate raises
    with pytest.raises((AttributeError, TypeError)):
        mdef.metric_id = "other.id"


# ----------------------------------------------------------------------
# Builtin manifest + get
# ----------------------------------------------------------------------


def test_builtin_manifest_has_seed_entries():
    """BUILT_IN_MANIFEST has the 3 required binance seeds."""
    from v5.data.metrics import BUILT_IN_MANIFEST, MetricsManifest

    assert isinstance(BUILT_IN_MANIFEST, MetricsManifest)

    expected_ids = {
        "binance.open_interest.5m",
        "binance.top_trader_ls.5m",
        "binance.taker_ls_vol.5m",
    }
    for mid in expected_ids:
        mdef = BUILT_IN_MANIFEST.get(mid)
        assert mdef is not None, f"BUILT_IN_MANIFEST missing seed entry {mid!r}"
        assert mdef.metric_id == mid


def test_manifest_get_by_id():
    """MetricsManifest.get(metric_id) returns a MetricDefinition or None."""
    from v5.data.metrics import MetricDefinition, MetricsManifest

    mdef = MetricDefinition(
        metric_id="foo.bar.5m",
        venue="BINANCE",
        source_file_pattern="some/{token}.parquet",
        timestamp_col="timestamp",
        value_col="value",
        native_resolution="5m",
        resample_to="1h",
        resample_agg="last",
        dtype="float64",
    )
    manifest = MetricsManifest(definitions={mdef.metric_id: mdef})
    assert manifest.get("foo.bar.5m") is mdef
    assert manifest.get("not.present") is None


# ----------------------------------------------------------------------
# YAML roundtrip
# ----------------------------------------------------------------------


def test_manifest_yaml_roundtrip(tmp_path: Path):
    """MetricsManifest.dump_yaml / load_yaml roundtrips."""
    from v5.data.metrics import MetricDefinition, MetricsManifest

    entries = [
        MetricDefinition(
            metric_id="a.metric.5m",
            venue="BINANCE",
            source_file_pattern="metrics/a/{token}.parquet",
            timestamp_col="timestamp",
            value_col="value",
            native_resolution="5m",
            resample_to="1h",
            resample_agg="last",
            dtype="float64",
        ),
        MetricDefinition(
            metric_id="b.metric.5m",
            venue="BINANCE",
            source_file_pattern="metrics/b/{token}.parquet",
            timestamp_col="ts",
            value_col="val",
            native_resolution="5m",
            resample_to="1h",
            resample_agg="mean",
            dtype="float32",
        ),
    ]
    m = MetricsManifest(definitions={e.metric_id: e for e in entries})
    out_path = tmp_path / "manifest.yaml"
    m.dump_yaml(out_path)
    assert out_path.exists() and out_path.stat().st_size > 0

    loaded = MetricsManifest.load_yaml(out_path)
    for e in entries:
        got = loaded.get(e.metric_id)
        assert got is not None
        assert got.metric_id == e.metric_id
        assert got.venue == e.venue
        assert got.native_resolution == e.native_resolution
        assert got.resample_to == e.resample_to
        assert got.resample_agg == e.resample_agg
        assert got.value_col == e.value_col
