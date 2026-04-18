"""Deterministic 48h MTF tick fixture generator (T-B23 / AC31).

Emits `v5/tests/fixtures/mtf_48h_ticks.jsonl` — a recorded 1-minute tick
stream covering 48 hours for 3 tokens. Drives the AC31 code-path identity
test: the same fixture must produce byte-identical output through the
backtest path (``v5.simulator.run_backtest_mtf``) and the paper path
(``v5.paper_engine.replay_paper_ticks``).

AC31 framing (brief.md §31):
  "Every MTF feature (triple subscriptions, PendingEntry state machine,
   intra-bar fill realism) uses identical code path in backtest and paper.
   48-hour MTF tick fixture replays through both paths; diff of
   ClosedTrade archive + PendingEntry state-transition log = 0 bytes."

The fixture represents a **post-M4 record**: the test contract is
paper-vs-backtest code-path identity on the *same* M4 build, not
cross-version parity. A single deterministic 1m tick stream is sufficient
input for both paths; any divergence reflects code-path drift, not input
drift.

Determinism:
  - Seed = 42
  - Start = 2026-02-02T00:00:00Z (ts_ns 1_770_000_000 * 1e9 — matches the
    AC31 test window anchor in ``test_m4_parity_mtf.py``)
  - Cadence = 1 minute; N_BARS = 48 * 60 = 2880
  - Tokens = ["BTC", "ETH", "SOL"]  (three — sufficient to exercise per-
    token dispatch without inflating fixture size)

Schema (one JSON object per line):
    {"type":"tick","ts_ns":<int>,"bars":{"<TOKEN>":[close,high,low],...}}

The sparse [c,h,l] array form mirrors ``generate_soak_fixture.py`` so
loaders can share bar-coercion helpers (see ``bar_array_to_dict``).

Run standalone:
    python -m v5.tests.fixtures.generate_mtf_48h_fixture

Test status (T23 green phase):
  * ``test_mtf_48h_fixture_exists``                     — GREEN (this file)
  * ``test_backtest_vs_paper_archive_match``            — RED, blocked:
        needs ``v5.paper_engine.replay_paper_ticks``     (Task 16b) and
        ``run_backtest_mtf(output_path=, seed=)``        (Task 15c/17).
  * ``test_pending_entry_log_diff_zero``                — RED, blocked:
        needs ``replay_paper_ticks`` + ``pe_log_path=``  kwargs on both
        entrypoints — same ownership as above.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

import numpy as np

SEED = 42
# Matches the test's start_ts_ns anchor: 1_770_000_000 * 1e9 ns.
START_TS_NS = 1_770_000_000 * 1_000_000_000
NS_PER_MIN = 60 * 1_000_000_000
N_BARS = 48 * 60  # 2880 one-minute bars

TOKENS = ["BTC", "ETH", "SOL"]

_START_PRICES = {
    "BTC": 60_000.0,
    "ETH": 3_000.0,
    "SOL": 150.0,
}


def _synthetic_bar(close: float, rng: np.random.Generator) -> list:
    """Sparse [c, h, l] bar. Volume/ATR are re-derived by the replay
    harness; keeping the fixture array-only holds the file under 500 KB."""
    high = close * (1.0 + abs(rng.normal(0.0, 0.0005)))
    low = close * (1.0 - abs(rng.normal(0.0, 0.0005)))
    return [round(close, 4), round(high, 4), round(low, 4)]


def _generate_events() -> Iterable[dict]:
    rng = np.random.default_rng(SEED)
    prices = {t: _START_PRICES[t] for t in TOKENS}
    for i in range(N_BARS):
        ts_ns = START_TS_NS + i * NS_PER_MIN
        bars: dict[str, list] = {}
        for t in TOKENS:
            # Small per-minute drift keeps prices coherent across 48h.
            drift = rng.normal(0.0, prices[t] * 0.0005)
            prices[t] = max(prices[t] + drift, 0.0001)
            bars[t] = _synthetic_bar(prices[t], rng)
        yield {"type": "tick", "ts_ns": ts_ns, "bars": bars}


def main(output_path: str | Path | None = None) -> Path:
    if output_path is None:
        output_path = Path(__file__).resolve().parent / "mtf_48h_ticks.jsonl"
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w") as f:
        for ev in _generate_events():
            f.write(json.dumps(ev, separators=(",", ":"), ensure_ascii=False))
            f.write("\n")
    return output_path


if __name__ == "__main__":
    path = main()
    size = path.stat().st_size
    print(f"Wrote {path} ({size/1024:.1f} KB)")
