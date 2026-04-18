"""Deterministic parity fixture generator for AC18/AC19 (T-B10, T-B11).

Produces three fixtures consumed by:
  - `v5/tests/test_m4_parity_hourly.py`
  - `v5/tests/test_m4_parity_paper.py`

Outputs:
  1. `parity_hourly_pre_m4.bin`        — hourly-only trade-archive byte blob
  2. `paper_24h_ticks.jsonl`           — 24h paper tick stream (hourly-only)
  3. `paper_24h_pre_m4_state.json`     — expected post-replay paper state

Determinism:
  - Seed = 42
  - Epoch = 2026-03-01T00:00:00Z (1_770_000_000 * 1e9 ns alignment)
  - Window = 7 days hourly (168 bars) for backtest archive
  - Window = 24 hourly bars for paper replay
  - Token universe = ["BTC"] (matches tests/test_m4_parity_hourly.py call site)
  - All outputs are pure-Python `dict`/`list` of floats/ints; no ML, no clock.

Design note — blocked-by-infrastructure state (2026-04-18):
  The test file invokes `run_backtest_mtf(..., output_path=..., seed=...)`
  and `v5.paper_engine.replay_paper_ticks(...)`. Neither API exists in the
  current v5 codebase — they are deliverables of Tasks 15c (hourly archive
  output) and 16b (paper state output + replay). The *existence* tests
  (`test_pre_m4_fixture_exists`, `test_paper_24h_fixture_exists`,
  `test_pre_m4_paper_state_exists`) only require the fixture files to be
  present. The *comparison* tests will continue to fail with AttributeError
  / TypeError until 15c + 16b land. This is an accepted Phase 4 interim
  state — per Task 22 brief, fixtures here are the capture point; parity
  assertions bind once the implementation ships.

  Once Tasks 15c / 16b ship, the implementer should re-run this script
  (OR capture real engine output via `output_path=...` into these same
  paths) so the comparison tests turn green on the next run.

Run standalone:
    /workspace/venv/bin/python -m v5.tests.fixtures.generate_hourly_parity_fixture

Or invoke `main()` from tests to lazy-regenerate.
"""
from __future__ import annotations

import json
import struct
from pathlib import Path

import numpy as np

SEED = 42
# Matches test file: start_ts_ns = 1_770_000_000 * 1_000_000_000
START_TS_NS = 1_770_000_000 * 1_000_000_000
NS_PER_HOUR = 3_600 * 1_000_000_000
HOURS_BACKTEST = 7 * 24  # 168 bars
HOURS_PAPER = 24
TOKENS = ["BTC"]

# Archive magic header — identifies this as the v5/M4 hourly-only parity blob.
# Format: b"M4HP" + version (u16) + token_count (u16) + hours (u32) + seed (u32)
#          = 16 bytes header, then packed <f8 (ts_ns) + <f8 (close) pairs per bar.
#          This is a deterministic self-describing stub; Task 15c will replace
#          the payload with ClosedTrade records — header survives unchanged.
MAGIC = b"M4HP"
VERSION = 1

FIXTURES_DIR = Path(__file__).resolve().parent
HOURLY_BIN = FIXTURES_DIR / "parity_hourly_pre_m4.bin"
PAPER_JSONL = FIXTURES_DIR / "paper_24h_ticks.jsonl"
PAPER_STATE_JSON = FIXTURES_DIR / "paper_24h_pre_m4_state.json"


def _price_walk(seed: int, n: int, start: float = 60_000.0) -> list[float]:
    """Deterministic geometric-ish random walk. NumPy default_rng with a
    fixed seed guarantees bit-identical output across Python versions and
    platforms (PCG64 is the documented default)."""
    rng = np.random.default_rng(seed)
    drift = rng.normal(0.0, start * 0.002, size=n)
    prices = np.empty(n, dtype=np.float64)
    p = start
    for i in range(n):
        p = max(p + drift[i], 0.0001)
        prices[i] = p
    return [round(float(x), 3) for x in prices]


def generate_hourly_archive(output_path: Path = HOURLY_BIN) -> Path:
    """Write the hourly-only trade-archive byte blob.

    Layout (packed, little-endian):
      magic[4] = "M4HP"
      version  = u16
      n_tokens = u16
      n_bars   = u32
      seed     = u32
      payload  = n_bars * (f8 ts_ns + f8 close)  (stub — replaced by
                 ClosedTrade records when Task 15c lands)
    """
    closes = _price_walk(SEED, HOURS_BACKTEST)
    header = struct.pack(
        "<4sHHII",
        MAGIC,
        VERSION,
        len(TOKENS),
        HOURS_BACKTEST,
        SEED,
    )
    payload = bytearray()
    for i, c in enumerate(closes):
        ts = float(START_TS_NS + i * NS_PER_HOUR)
        payload += struct.pack("<dd", ts, c)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(header + bytes(payload))
    return output_path


def generate_paper_tick_fixture(output_path: Path = PAPER_JSONL) -> Path:
    """Write 24 hourly tick events for BTC as JSONL.

    Shape matches the soak-fixture convention: one JSON object per line,
    with `type`, `ts_ns`, and `bars: {token: [close, high, low]}`.
    """
    closes = _price_walk(SEED + 1, HOURS_PAPER, start=60_000.0)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w") as f:
        for i, close in enumerate(closes):
            ts_ns = START_TS_NS + i * NS_PER_HOUR
            high = round(close * 1.003, 3)
            low = round(close * 0.997, 3)
            event = {
                "type": "tick",
                "ts_ns": ts_ns,
                "bars": {TOKENS[0]: [close, high, low]},
            }
            f.write(json.dumps(event, separators=(",", ":"), ensure_ascii=False))
            f.write("\n")
    return output_path


def generate_paper_state_fixture(output_path: Path = PAPER_STATE_JSON) -> Path:
    """Write the expected post-replay paper state snapshot.

    Minimal hourly-only shape: no open positions, empty trade log, empty
    alert queue, replay cursor at the last tick. Deterministic — no
    wall-clock, no uuid4().

    When Task 16b lands and the engine emits a real paper_state snapshot,
    the implementer MUST update this fixture (or re-derive from the
    engine output) so the comparison test binds to real behavior.
    """
    closes = _price_walk(SEED + 1, HOURS_PAPER, start=60_000.0)
    last_ts_ns = START_TS_NS + (HOURS_PAPER - 1) * NS_PER_HOUR
    last_close = closes[-1]
    state = {
        "schema_version": "m4-paper-parity-v1",
        "seed": SEED,
        "token_universe": TOKENS,
        "hourly_only": True,
        "replay_cursor_ts_ns": last_ts_ns,
        "last_marks": {TOKENS[0]: last_close},
        "positions": [],
        "pending_entries": [],
        "closed_trades": [],
        "alerts": [],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # sort_keys + compact separators => byte-identical across runs.
    output_path.write_text(
        json.dumps(state, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    )
    return output_path


def main() -> tuple[Path, Path, Path]:
    """Generate all three parity fixtures. Idempotent + deterministic."""
    a = generate_hourly_archive()
    b = generate_paper_tick_fixture()
    c = generate_paper_state_fixture()
    return a, b, c


if __name__ == "__main__":
    paths = main()
    for p in paths:
        size = p.stat().st_size
        print(f"Wrote {p} ({size/1024:.2f} KB)")
