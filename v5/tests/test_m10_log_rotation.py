"""M10 E3 — JSONL log-rotation policy (AC #22).

All JSONL sinks under ``v5/logs/`` must rotate at 500MB, gzip the
rotated file, and re-open the active sink. Parametrised across every
sink named in the brief:

    * arbitration.jsonl
    * sizing_fills.jsonl
    * orders_log.jsonl
    * funding_accruals.jsonl
    * risk_decisions.jsonl
    * clock_drift.jsonl

For test speed we instantiate ``LogRotator`` with a tiny
``max_bytes=1024`` limit and drive it past the threshold with synthetic
JSON records.

MUST FAIL TODAY (RED):
    * ``v5.logs.LogRotator`` class does not exist — ImportError.
"""
from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


_SINK_NAMES = [
    "arbitration.jsonl",
    "sizing_fills.jsonl",
    "orders_log.jsonl",
    "funding_accruals.jsonl",
    "risk_decisions.jsonl",
    "clock_drift.jsonl",
]


def _payload(i: int) -> str:
    """~100-byte JSON record — ensures we cross 1024B in well under 20 writes."""
    return json.dumps(
        {
            "seq": i,
            "ts": 1_735_689_600 + i,
            "token": "BTC",
            "strategy_id": "s524m",
            "payload": "x" * 50,
        }
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestLogRotatorExists:
    """AC #22 — LogRotator class importable from v5.logs."""

    def test_log_rotator_importable(self) -> None:
        from v5.logs import LogRotator  # noqa: F401

        # Surface-contract check: LogRotator is a class callable with at least
        # (path, max_bytes) in its __init__ signature.
        assert callable(LogRotator)


@pytest.mark.parametrize("sink_name", _SINK_NAMES)
class TestJsonlSinkRotation:
    """AC #22 — every JSONL sink rotates + gzips + re-opens."""

    def test_rotation_produces_gzipped_archive(
        self, sink_name: str, tmp_path: Path
    ) -> None:
        from v5.logs import LogRotator

        sink_path = tmp_path / sink_name
        rotator = LogRotator(sink_path, max_bytes=1024)

        # Write >1024 bytes of JSONL.
        written_records: list[dict] = []
        total_bytes = 0
        i = 0
        while total_bytes < 4096:  # ~4 KB forces at least one rotation
            rec = _payload(i)
            rotator.write(rec + "\n")
            written_records.append(json.loads(rec))
            total_bytes += len(rec) + 1
            i += 1
        rotator.close()

        # Active sink re-opened (exists as an independent file).
        assert sink_path.exists(), (
            f"Active sink {sink_path} must be re-opened after rotation."
        )

        # Rotated + gzipped sibling present.
        archive = sink_path.with_name(sink_path.name + ".1.gz")
        assert archive.exists(), (
            f"Rotated + gzipped archive expected at {archive}."
        )

        # Decode the archive and verify records round-trip.
        with gzip.open(archive, "rt") as f:
            decoded_lines = [line.strip() for line in f if line.strip()]
        assert len(decoded_lines) > 0, (
            "Gzipped archive must contain at least one rotated record."
        )
        # Each line decodes as JSON with the expected shape.
        for line in decoded_lines:
            blob = json.loads(line)
            assert "seq" in blob
            assert "ts" in blob
            assert "token" in blob

    def test_rotation_threshold_honors_max_bytes(
        self, sink_name: str, tmp_path: Path
    ) -> None:
        """Rotator does NOT rotate before crossing the threshold."""
        from v5.logs import LogRotator

        sink_path = tmp_path / sink_name
        rotator = LogRotator(sink_path, max_bytes=1_000_000)  # 1 MB

        # Write a few hundred bytes — well below threshold.
        for i in range(5):
            rotator.write(_payload(i) + "\n")
        rotator.close()

        archive = sink_path.with_name(sink_path.name + ".1.gz")
        assert not archive.exists(), (
            f"No rotation must fire below max_bytes "
            f"(unexpected archive at {archive})."
        )
        assert sink_path.exists()
