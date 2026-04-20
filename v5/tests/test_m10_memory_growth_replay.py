"""M10 G3 — Replay-based memory stability test (AC #8).

Drives ``paper_engine.PaperEngine`` through a deterministic 24h-equivalent
replay fixture (1440 × 1m bars × 10 tokens) built by the shared
``ReplayFixtureBuilder`` (C0) and asserts:

  * ``RSS_end - RSS_start < 100MB`` over the full run
  * ``tracemalloc`` top-10 allocators each bounded (< 50MB/slot)
  * zero state-corruption events — every ``paper_state.persist()``
    round-trips via ``paper_state.load()`` without error

Replaces the previously-planned 48h live soak per user directive
2026-04-20 (AC #8): NO M10 test depends on wall-clock live data
collection.

MUST FAIL TODAY — ``ReplayFixtureBuilder.build()`` is Phase-4
implementation (currently a surface-contract stub); ``PaperEngine``
does not expose the replay driver entry-point the test calls.
"""
from __future__ import annotations

import sys
import tracemalloc
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


# AC #8 requires psutil as a hard dependency. Lazy-imported inside each
# test via ``_require_psutil`` so missing psutil is a test-level failure
# rather than a collection error (keeps sibling tests discoverable).


def _require_psutil():
    import psutil  # noqa: WPS433  (intentional runtime import)
    return psutil


_BYTES_PER_MB = 1024 * 1024
_RSS_GROWTH_CAP = 100 * _BYTES_PER_MB
_ALLOCATOR_CAP = 50 * _BYTES_PER_MB
_BAR_COUNT_24H = 1440  # 24h × 60 min
_TOKEN_COUNT = 10


@pytest.fixture(scope="module")
def replay_ctx():
    """Build a 24h × 10-token deterministic replay fixture."""
    from v5.tests.fixtures._replay_builder import (
        ReplayFixtureBuilder,
        ScenarioSpec,
    )

    tokens = [f"TKN{i:02d}" for i in range(_TOKEN_COUNT)]
    spec = ScenarioSpec(
        random_walk_stddev=0.0008,
        forced_trades=50,
        crash_bar=None,
        funding_snaps=None,
    )
    builder = ReplayFixtureBuilder(
        tokens=tokens,
        bar_count=_BAR_COUNT_24H,
        start_ts="2025-09-01T00:00:00Z",
        seed=42,
        scenario_spec=spec,
    )
    return builder


class TestMemoryGrowthReplay:
    """AC #8 — 24h replay RSS + tracemalloc asserts."""

    def test_rss_growth_under_100mb(self, replay_ctx, tmp_path, monkeypatch):
        """Drive PaperEngine through full 1440-bar replay; bounded RSS."""
        from v5.paper_engine import PaperEngine

        psutil = _require_psutil()
        monkeypatch.setenv("V5_PAPER_STATE_DIR", str(tmp_path))

        proc = psutil.Process()
        rss_start = proc.memory_info().rss

        engine = PaperEngine.from_replay_fixture(replay_ctx)
        engine.run_replay(total_bars=_BAR_COUNT_24H)

        rss_end = proc.memory_info().rss
        growth = rss_end - rss_start

        assert growth < _RSS_GROWTH_CAP, (
            f"RSS grew by {growth / _BYTES_PER_MB:.1f}MB over 24h replay; "
            f"AC #8 caps growth at 100MB."
        )

    def test_tracemalloc_top10_bounded(self, replay_ctx, tmp_path, monkeypatch):
        """Top-10 allocators each < 50MB — rejects unbounded accumulators."""
        from v5.paper_engine import PaperEngine

        monkeypatch.setenv("V5_PAPER_STATE_DIR", str(tmp_path))

        tracemalloc.start()
        try:
            engine = PaperEngine.from_replay_fixture(replay_ctx)
            engine.run_replay(total_bars=_BAR_COUNT_24H)
            snapshot = tracemalloc.take_snapshot()
        finally:
            tracemalloc.stop()

        top = snapshot.statistics("lineno")[:10]
        offenders = [s for s in top if s.size > _ALLOCATOR_CAP]
        assert not offenders, (
            f"Unbounded allocators in paper_engine 24h replay (AC #8 — "
            f"cap 50MB/slot):\n"
            + "\n".join(f"  {s}" for s in offenders)
        )

    def test_state_persist_roundtrip_all_bars(
        self, replay_ctx, tmp_path, monkeypatch
    ):
        """Each paper_state.persist() round-trips via load() cleanly."""
        from v5.paper_engine import PaperEngine
        from v5.paper_state import load as paper_state_load

        monkeypatch.setenv("V5_PAPER_STATE_DIR", str(tmp_path))

        engine = PaperEngine.from_replay_fixture(replay_ctx)
        persist_errors: list[str] = []

        def on_persist(path_str: str):
            try:
                paper_state_load(path_str)
            except Exception as exc:
                persist_errors.append(f"{path_str}: {exc!r}")

        engine.on_persist_callback = on_persist
        engine.run_replay(total_bars=_BAR_COUNT_24H)

        assert persist_errors == [], (
            "State corruption detected during 24h replay persist cycle "
            f"(AC #8):\n" + "\n".join(persist_errors)
        )
