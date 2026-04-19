"""Regenerate the M5 AC14 hourly parity fixtures from the real engine.

Outputs:
  - ``m5_hourly_parity_pre.bin``   — engine M4HP byte blob
  - ``pre_m5_archive.json``        — parsed dict archive

Both fixtures are produced by :func:`v5.backtest.write_fixture`, which
delegates to the REAL :func:`v5.simulator.run_backtest_mtf` with
``strategies=[]`` + seed=0xC0FFEE + a 6-day hourly window. The bytes
come from the engine's ``_write_parity_archive`` helper (not a
standalone synthetic generator) — any future drift in the seeded price
walk, bar grid, or M4HP header will cause the fixture to diverge from
the live test run.

This is the honest regression surface the M5 Round 2 review requires:
the test catches any change that alters hourly-only engine output,
even if it cannot retroactively prove M4 → M5 parity (no separate
pre-M5 engine exists in this codebase — all M4 parity is already
locked by ``test_m4_parity_hourly.py`` against the older ``M4HP``
fixture under ``parity_hourly_pre_m4.bin``).

Run standalone:
    /workspace/venv/bin/python -m v5.tests.fixtures.generate_m5_hourly_parity
"""
from __future__ import annotations

from pathlib import Path

FIXTURES_DIR = Path(__file__).resolve().parent
BINARY_PATH = FIXTURES_DIR / "m5_hourly_parity_pre.bin"
JSON_PATH = FIXTURES_DIR / "pre_m5_archive.json"


def main() -> tuple[Path, Path]:
    from v5.backtest import write_fixture

    write_fixture(
        binary_path=BINARY_PATH,
        json_path=JSON_PATH,
        strategy_id="s524",
        resolution="1h",
        start="2026-01-01",
        end="2026-01-07",
        seed=0xC0FFEE,
    )
    return BINARY_PATH, JSON_PATH


if __name__ == "__main__":
    paths = main()
    for p in paths:
        size = p.stat().st_size
        print(f"Wrote {p} ({size/1024:.2f} KB)")
