"""M9 AC #22 — Engine/strategy isolation audit (grep-verified).

Covers AC #22:
- Strategies under v5/strategies/ must NOT import engine modules
  (v5.simulator, v5.engine, v5.bar_processor)
- Engine files MUST NOT touch strategy private attributes (strategy._xxx)

Makes §11.1 future-rewrite auditable — no hidden cross-layer coupling.

All tests MUST FAIL today — leftover imports and private-attr touches still
exist pre-M9.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


class TestStrategiesDoNotImportEngine:
    """AC #22 — v5/strategies/ contains zero imports from engine modules."""

    def test_zero_engine_imports_in_strategies(self):
        strategies_dir = _project_root / "v5" / "strategies"
        if not strategies_dir.exists():
            pytest.fail(f"Expected v5/strategies/ at {strategies_dir}")

        result = subprocess.run(
            [
                "grep", "-rn",
                "-E", r"from v5\.simulator|from v5\.engine|from v5\.bar_processor",
                str(strategies_dir),
                "--exclude-dir=tests",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        # grep returns 1 when no matches (that is the success case)
        matches = [
            line for line in result.stdout.splitlines() if line.strip()
        ]
        assert matches == [], (
            "v5/strategies/ must not import engine modules; found:\n"
            + "\n".join(matches)
        )


class TestEngineDoesNotTouchStrategyPrivates:
    """AC #22 — engine files do not read/write strategy._xxx private attributes."""

    def test_zero_private_attr_touches_in_engine(self):
        engine_files = [
            _project_root / "v5" / "simulator.py",
            _project_root / "v5" / "engine.py",
            _project_root / "v5" / "bar_processor.py",
        ]
        existing = [f for f in engine_files if f.exists()]
        if not existing:
            pytest.fail("No engine files found among simulator/engine/bar_processor")

        result = subprocess.run(
            [
                "grep", "-rn",
                "-E", r"strategy\._[a-z]",
                *[str(f) for f in existing],
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        matches = [
            line for line in result.stdout.splitlines() if line.strip()
        ]
        assert matches == [], (
            "Engine files must not touch strategy._<private>; found:\n"
            + "\n".join(matches)
        )
