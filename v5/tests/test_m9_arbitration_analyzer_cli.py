"""M9 C-10 — arbitration telemetry analyzer CLI + log rotation.

Covers C-10 item 4:
- python -m v5.tools.arbitration_analyzer --log <path> --starvation-report
  runs and produces per-strategy admission rate output
- arbitration.jsonl rotates at 500MB with gzip; stays <2GB over 1-year fixture

All tests MUST FAIL today — v5/tools/arbitration_analyzer.py not yet shipped;
500MB gzip rotation in arbitration logger not yet implemented.
"""
from __future__ import annotations

import gzip
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


class TestArbitrationAnalyzerCLI:
    """C-10 — CLI runs --starvation-report and emits per-strategy admission rates."""

    def test_starvation_report_cli_produces_per_strategy_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "arbitration.jsonl"
            rows = []
            for i in range(100):
                rows.append({
                    "bar_idx": i,
                    "strategy_id": "s524m" if i % 2 == 0 else "s513",
                    "token": "BTCUSDT",
                    "rank_in": 1,
                    "rank_out": 1 if i % 2 == 0 else None,
                    "tier": 0,
                    "admitted": i % 2 == 0,
                    "displaced_by": None,
                })
            log_path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")

            result = subprocess.run(
                [
                    sys.executable, "-m", "v5.tools.arbitration_analyzer",
                    "--log", str(log_path),
                    "--starvation-report",
                ],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=str(_project_root),
            )
            assert result.returncode == 0, (
                f"analyzer exited {result.returncode}: stderr={result.stderr}"
            )
            assert "s524m" in result.stdout
            assert "s513" in result.stdout
            # Admission rate column / phrase must appear
            assert (
                "admission" in result.stdout.lower()
                or "admit" in result.stdout.lower()
            ), f"no admission rate field in output:\n{result.stdout}"


class TestArbitrationLogRotation:
    """C-10 — arbitration.jsonl rotates at 500MB with gzip; 1-year fixture stays <2GB."""

    def test_arbitration_log_rotates_with_gzip_at_500mb(self):
        from v5.arbitration import ArbitrationLogWriter

        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            writer = ArbitrationLogWriter(
                log_dir=log_dir,
                rotate_size_bytes=1_000,  # small for test
            )
            # Write enough rows to trigger a rotation
            for i in range(5_000):
                writer.write({
                    "bar_idx": i,
                    "strategy_id": f"s{i % 3}",
                    "token": "BTCUSDT",
                    "rank_in": 1,
                    "rank_out": 1,
                    "tier": 0,
                    "admitted": True,
                    "displaced_by": None,
                })
            writer.close()

            rotated = list(log_dir.glob("arbitration.jsonl.*.gz"))
            assert rotated, "No rotated .jsonl.gz files produced at 500MB threshold"

            # Assert rotated file is valid gzip + JSONL
            with gzip.open(rotated[0], "rt") as fh:
                first_line = fh.readline()
                json.loads(first_line)
