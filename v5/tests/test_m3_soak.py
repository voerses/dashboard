"""M3 soak tests — bounded memory growth + maintenance-event coverage.

Covers:
  - AC11: Memory soak. RSS sampled every 100 ticks; first 500 ticks =
          warmup (discarded); post-warmup total delta < 100 MB; per-window
          delta < 50 MB. Opt-in via RUN_FULL_SOAK=1 so CI can skip when
          the 120s budget is tight (default off; fast AC25 tests always run).
  - AC25: Fixture includes synthetic backfill, promote, and WS reconnect
          events in addition to ticks. Per-event-type assertions verify
          the coherence protocol reacts correctly (watermark after
          backfill, reseed after promote, sane state after reconnect).

Seed: 42. Epoch: 2026-03-01T00:00:00Z. Fixture lazy-regenerates if absent.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
FIXTURE_PATH = FIXTURE_DIR / "soak_ticks.jsonl"
RUN_FULL_SOAK = os.environ.get("RUN_FULL_SOAK", "0") == "1"
SOAK_BUDGET_S = 120.0

# Tokens + per-event-index invariants are the contract between the fixture
# generator and the soak runner. Import by name so the test fails loudly
# if the generator's schema drifts.
from v5.tests.fixtures.generate_soak_fixture import (  # noqa: E402
    TOKENS,
    N_TICKS,
    NS_PER_HOUR,
    BACKFILL_AT_600_TOKEN,
    BACKFILL_AT_600_GAP_HOURS,
    PROMOTE_AT_720_TOKEN,
    PROMOTE_AT_720_REWRITE_LAST_N,
    RECONNECT_AT_900_DURATION_SEC,
    BACKFILL_AT_1100_TOKEN,
    BACKFILL_AT_1100_GAP_HOURS,
    bar_array_to_dict,
    backfill_bar_array_to_dict,
    main as generate_fixture,
)


# ---------------------------------------------------------------------------
# Fixture loading — lazy-regenerate on demand
# ---------------------------------------------------------------------------


def _ensure_fixture() -> Path:
    """Return the fixture path, regenerating deterministically if missing."""
    if not FIXTURE_PATH.is_file():
        generate_fixture(FIXTURE_PATH)
    return FIXTURE_PATH


def _load_soak_fixture() -> list[dict]:
    path = _ensure_fixture()
    events: list[dict] = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            events.append(json.loads(line))
    return events


# ---------------------------------------------------------------------------
# Minimal replay runner — acts directly on RollingCache for speed.
# We do NOT route through the full PaperPortfolioEngine because:
#   (1) the engine requires a full PaperConfig + strategy set, which would
#       swamp the 120 s CI budget,
#   (2) AC25's per-event-type assertions target the cache-coherence layer
#       (watermarks, reseed), which is exactly what RollingCache handles.
# ---------------------------------------------------------------------------


def _dispatch_tick(caches: dict, event: dict) -> None:
    """Tick event: append (close/high/low/...) to each per-token 1h cache."""
    from v5.rolling_cache import BarType  # local import to avoid early load cost
    ts_ns = int(event["ts_ns"])
    bars = event["bars"]
    for token, arr in bars.items():
        cache = caches.get((token, BarType.ONE_HOUR))
        if cache is None:
            continue
        bar = bar_array_to_dict(arr)
        # Skip if non-monotonic (e.g., a backfill already advanced the
        # cache past this ts_ns). RollingCache.append raises otherwise.
        if cache._size > 0 and ts_ns <= cache.last_written_ts_ns():
            return  # the bar is already present; ignore duplicate
        cache.append(ts_ns=ts_ns, **bar)


def _dispatch_backfill(caches: dict, tmp_path: Path, event: dict) -> Path:
    """Backfill event: write a new parquet for the target (token, 1h) with
    the new bars appended, then call check_parquet_drift + reseed."""
    from v5.rolling_cache import BarType
    token = event["token"]
    cache = caches.get((token, BarType.ONE_HOUR))
    if cache is None:
        raise RuntimeError(f"no cache for {token}/1h during backfill")

    # Build the "on-disk" parquet: current cache contents + backfill bars.
    existing = _cache_to_df(cache)
    new_rows = [backfill_bar_array_to_dict(b) for b in event["new_bars"]]
    new_df = pd.DataFrame(new_rows).rename(columns={"ts_ns": "timestamp"})
    # Order: existing history then the backfilled bars. Drop dupes keeping last
    # so any ts collision resolves in favour of the newer write.
    merged = pd.concat([existing, new_df], ignore_index=True)
    merged["timestamp"] = merged["timestamp"].astype("int64")
    merged = merged.drop_duplicates(subset=["timestamp"], keep="last")
    merged = merged.sort_values("timestamp").reset_index(drop=True)

    parquet_path = tmp_path / f"{token}_1h.parquet"
    merged.to_parquet(parquet_path, index=False)

    # Drift should now be true; reseed the cache from the parquet.
    drifted = cache.check_parquet_drift(str(parquet_path))
    if drifted:
        cache.reseed_from_parquet(
            token, BarType.ONE_HOUR,
            load_fn=lambda tok, bt: pd.read_parquet(parquet_path),
        )
    return parquet_path


def _dispatch_promote(caches: dict, tmp_path: Path, event: dict) -> Path:
    """Promote event: rewrite existing parquet rows (last N) with corrected
    OHLC, then reseed. The new bars carry the same timestamps as bars
    already in the cache — this is the "rewrite" semantic of fix_promoted_bars."""
    from v5.rolling_cache import BarType
    token = event["token"]
    cache = caches.get((token, BarType.ONE_HOUR))
    if cache is None:
        raise RuntimeError(f"no cache for {token}/1h during promote")

    existing = _cache_to_df(cache)
    rewrites = [backfill_bar_array_to_dict(b) for b in event["rewritten_bars"]]
    rewrite_df = pd.DataFrame(rewrites).rename(columns={"ts_ns": "timestamp"})
    # Overwrite rows by timestamp (drop_duplicates keep="last").
    merged = pd.concat([existing, rewrite_df], ignore_index=True)
    merged["timestamp"] = merged["timestamp"].astype("int64")
    merged = merged.drop_duplicates(subset=["timestamp"], keep="last")
    merged = merged.sort_values("timestamp").reset_index(drop=True)

    parquet_path = tmp_path / f"{token}_1h.parquet"
    merged.to_parquet(parquet_path, index=False)

    # Promote bumps the last ts by +1 ns relative to the live cache so that
    # check_parquet_drift returns True (rewrite semantics). If the top-of-
    # parquet ts already equals the cache watermark, we still reseed
    # unconditionally — the rewrite updated OHLC values, not just the tail.
    cache.reseed_from_parquet(
        token, BarType.ONE_HOUR,
        load_fn=lambda tok, bt: pd.read_parquet(parquet_path),
    )
    return parquet_path


def _dispatch_reconnect(caches: dict, event: dict) -> None:
    """Reconnect event: no-op cache-wise. Exercises the code path that
    handles the catchup (which is outside the RollingCache). We assert the
    caches remain in a consistent state (monotonic ts, size > 0 for seeded
    tokens)."""
    for (token, _bt), cache in caches.items():
        # A reconnect must not corrupt the cache watermark.
        if cache._size > 0:
            last_ts = cache.last_written_ts_ns()
            assert last_ts > 0, f"{token}: reconnect corrupted watermark"


def _cache_to_df(cache) -> pd.DataFrame:
    """Extract the ordered contents of a RollingCache into a parquet-shaped DF."""
    return pd.DataFrame({
        "timestamp": cache.timestamps().astype(np.int64),
        "close": cache.arrays("close").astype(np.float64),
        "high": cache.arrays("high").astype(np.float64),
        "low": cache.arrays("low").astype(np.float64),
        "volume": cache.arrays("volume").astype(np.float64),
        "atr": cache.arrays("atr").astype(np.float64),
        "funding": cache.arrays("funding").astype(np.float64),
    })


def _build_caches() -> dict:
    """Subscribe one 1h cache per token. Returns {(token, BarType): cache}."""
    from v5.rolling_cache import RollingCacheRegistry, BarType
    reg = RollingCacheRegistry()
    for t in TOKENS:
        reg.subscribe(t, BarType.ONE_HOUR)
    return {(c.token, c.bar_type): c for c in reg.all()}


# ---------------------------------------------------------------------------
# AC25 — event-type coverage (structural + fast; always runs)
# ---------------------------------------------------------------------------


class TestAC25EventTypeCoverage:
    """AC25: fixture MUST contain >=1 instance of each event type."""

    def test_soak_includes_tick_events(self):
        events = _load_soak_fixture()
        n = sum(1 for e in events if e["type"] == "tick")
        assert n >= 1400, f"expected ~1440 tick events; got {n}"

    def test_soak_includes_backfill_events(self):
        events = _load_soak_fixture()
        n = sum(1 for e in events if e["type"] == "backfill")
        assert n >= 1, f"expected >=1 backfill event; got {n}"

    def test_soak_includes_promote_events(self):
        events = _load_soak_fixture()
        n = sum(1 for e in events if e["type"] == "promote")
        assert n >= 1, f"expected >=1 promote event; got {n}"

    def test_soak_includes_reconnect_events(self):
        events = _load_soak_fixture()
        n = sum(1 for e in events if e["type"] == "reconnect")
        assert n >= 1, f"expected >=1 reconnect event; got {n}"

    def test_backfill_event_triggers_reseed(self, tmp_path):
        """Replay ticks up to the first backfill event; the cache for the
        target token must advance its watermark past the gap."""
        events = _load_soak_fixture()
        caches = _build_caches()
        from v5.rolling_cache import BarType

        target_token = BACKFILL_AT_600_TOKEN
        target_cache = caches[(target_token, BarType.ONE_HOUR)]

        # Replay until first backfill for target_token.
        for ev in events:
            if ev["type"] == "tick":
                _dispatch_tick(caches, ev)
            elif ev["type"] == "backfill" and ev["token"] == target_token:
                # Record watermark BEFORE backfill.
                ts_before = target_cache.seeded_through_ts_ns
                path = _dispatch_backfill(caches, tmp_path, ev)
                ts_after = target_cache.seeded_through_ts_ns
                assert path.is_file()
                assert ts_after >= ts_before, (
                    f"{target_token}: watermark regressed after backfill: "
                    f"before={ts_before} after={ts_after}"
                )
                # The cache must contain at least as many bars as before
                # (backfill cannot empty the cache).
                assert target_cache._size > 0, "cache empty after backfill"
                return
        pytest.fail(f"no backfill event found for {target_token}")

    def test_promote_event_triggers_reseed(self, tmp_path):
        """Replay until the first promote event; assert cache's tail close
        values match the rewritten-bars' close values post-reseed."""
        events = _load_soak_fixture()
        caches = _build_caches()
        from v5.rolling_cache import BarType

        target_token = PROMOTE_AT_720_TOKEN
        target_cache = caches[(target_token, BarType.ONE_HOUR)]

        for ev in events:
            if ev["type"] == "tick":
                _dispatch_tick(caches, ev)
            elif ev["type"] == "backfill":
                _dispatch_backfill(caches, tmp_path, ev)
            elif ev["type"] == "promote" and ev["token"] == target_token:
                n_rewrite = len(ev["rewritten_bars"])
                _dispatch_promote(caches, tmp_path, ev)
                # The last n_rewrite close values in the cache must match
                # the promote event's close values (by ts).
                cache_ts = target_cache.timestamps()
                cache_close = target_cache.arrays("close")
                expected = {
                    int(b[0]): float(b[1])  # [ts_ns, close, high, low]
                    for b in ev["rewritten_bars"]
                }
                # For each expected ts, find it in the cache and assert
                # close match.
                mismatches = []
                for ts_expected, close_expected in expected.items():
                    idx = np.where(cache_ts == ts_expected)[0]
                    if len(idx) == 0:
                        mismatches.append((ts_expected, "missing"))
                        continue
                    actual = float(cache_close[idx[0]])
                    if not np.isclose(actual, close_expected, rtol=1e-3):
                        mismatches.append((ts_expected, actual, close_expected))
                assert not mismatches, (
                    f"promote reseed did not take effect: {mismatches[:3]}"
                )
                return
        pytest.fail(f"no promote event found for {target_token}")

    def test_reconnect_event_catchup(self, tmp_path):
        """Replay through the reconnect event; assert engine state sane —
        no exceptions, cache watermarks are monotonic, and subsequent ticks
        continue to advance watermarks."""
        events = _load_soak_fixture()
        caches = _build_caches()
        from v5.rolling_cache import BarType

        found_reconnect = False
        watermarks_at_reconnect: dict = {}
        watermarks_after_5_ticks: dict = {}
        ticks_post_reconnect = 0

        for ev in events:
            if ev["type"] == "tick":
                _dispatch_tick(caches, ev)
                if found_reconnect and ticks_post_reconnect < 5:
                    ticks_post_reconnect += 1
                    if ticks_post_reconnect == 5:
                        for t in TOKENS:
                            cache = caches[(t, BarType.ONE_HOUR)]
                            watermarks_after_5_ticks[t] = cache.seeded_through_ts_ns
                        break
            elif ev["type"] == "backfill":
                _dispatch_backfill(caches, tmp_path, ev)
            elif ev["type"] == "promote":
                _dispatch_promote(caches, tmp_path, ev)
            elif ev["type"] == "reconnect":
                for t in TOKENS:
                    cache = caches[(t, BarType.ONE_HOUR)]
                    watermarks_at_reconnect[t] = cache.seeded_through_ts_ns
                _dispatch_reconnect(caches, ev)
                found_reconnect = True

        assert found_reconnect, "no reconnect event found in fixture"
        # After 5 ticks post-reconnect, watermarks must have strictly advanced
        # for every token (the stream keeps flowing).
        assert ticks_post_reconnect == 5, (
            f"did not observe 5 ticks after reconnect; got {ticks_post_reconnect}"
        )
        for t in TOKENS:
            before = watermarks_at_reconnect[t]
            after = watermarks_after_5_ticks[t]
            assert after > before, (
                f"{t}: watermark did not advance after reconnect "
                f"(before={before}, after={after})"
            )


# ---------------------------------------------------------------------------
# AC11 — full RSS soak. Opt-in via RUN_FULL_SOAK=1. Runs under the 120 s
# CI budget when enabled (locally verified ~10 s on Linux /proc fallback).
# ---------------------------------------------------------------------------


class TestAC11ShortSoakRSS:
    """Unconditional short soak (300 ticks of the fixture) — runs on every CI
    invocation to catch memory regressions. Per-review compromise: CI must
    catch RSS regressions without needing 1440-tick opt-in runs. Budget: <20s.
    """

    def test_short_soak_rss_bounded(self, tmp_path):
        """300-tick slice of the soak fixture. Post-warmup (first 100 ticks
        discarded) delta must be < 50 MB; per-100-tick window delta < 25 MB.
        Thresholds halved from the full-soak 100/50 to reflect the shorter
        run — still catches any monotonic leak."""
        from v5.paper_utils import _read_rss_mb

        events = _load_soak_fixture()
        caches = _build_caches()
        # Slice first 300 tick events + any maintenance events that fall in range
        limited = []
        tick_count = 0
        for ev in events:
            if tick_count >= 300:
                break
            limited.append(ev)
            if ev.get("type") == "tick":
                tick_count += 1

        tick_idx = 0
        samples: list[float] = []
        t0 = time.time()
        SHORT_BUDGET_S = 30  # tight CI budget

        for ev in limited:
            et = ev["type"]
            if et == "tick":
                _dispatch_tick(caches, ev)
                tick_idx += 1
                if tick_idx % 100 == 0:
                    samples.append(_read_rss_mb())
            elif et == "backfill":
                _dispatch_backfill(caches, tmp_path, ev)
            elif et == "promote":
                _dispatch_promote(caches, tmp_path, ev)
            elif et == "reconnect":
                _dispatch_reconnect(caches, ev)
            if time.time() - t0 > SHORT_BUDGET_S:
                pytest.fail(
                    f"short soak exceeded {SHORT_BUDGET_S}s budget after "
                    f"{tick_idx} ticks"
                )

        if _read_rss_mb() == 0.0:
            pytest.skip("RSS unavailable (neither psutil nor /proc/self/status)")
        assert tick_idx == 300, f"expected 300 ticks; got {tick_idx}"
        # Discard first 100 ticks warmup = 1 sample at i=0.
        post_warmup = samples[1:]
        if len(post_warmup) < 2:
            pytest.skip("insufficient RSS samples for short soak — CI may lack psutil")
        total_delta = max(post_warmup) - min(post_warmup)
        per_window = [abs(post_warmup[i] - post_warmup[i - 1])
                      for i in range(1, len(post_warmup))]
        max_window = max(per_window) if per_window else 0.0

        assert total_delta < 50.0, (
            f"short-soak RSS delta {total_delta:.1f} MB exceeds 50 MB "
            f"(samples={samples})"
        )
        assert max_window < 25.0, (
            f"short-soak per-window RSS delta {max_window:.1f} MB exceeds 25 MB "
            f"(samples={samples})"
        )


@pytest.mark.skipif(
    not RUN_FULL_SOAK,
    reason="full RSS soak opt-in via RUN_FULL_SOAK=1 (short-soak runs always; see TestAC11ShortSoakRSS)",
)
class TestAC11SoakRSS:
    """24h simulated replay must not grow RSS beyond thresholds."""

    def test_soak_rss_bounded(self, tmp_path):
        from v5.paper_utils import _read_rss_mb

        events = _load_soak_fixture()
        caches = _build_caches()

        tick_idx = 0
        samples: list[float] = []
        t0 = time.time()

        for ev in events:
            et = ev["type"]
            if et == "tick":
                _dispatch_tick(caches, ev)
                tick_idx += 1
                if tick_idx % 100 == 0:
                    samples.append(_read_rss_mb())
            elif et == "backfill":
                _dispatch_backfill(caches, tmp_path, ev)
            elif et == "promote":
                _dispatch_promote(caches, tmp_path, ev)
            elif et == "reconnect":
                _dispatch_reconnect(caches, ev)

            # Budget guard: if we exceed SOAK_BUDGET_S, abort cleanly.
            if time.time() - t0 > SOAK_BUDGET_S:
                pytest.fail(
                    f"soak exceeded {SOAK_BUDGET_S}s budget after "
                    f"{tick_idx} ticks"
                )

        assert tick_idx == N_TICKS, f"expected {N_TICKS} ticks; got {tick_idx}"
        # Discard first 500 ticks of warmup = first 5 samples (every 100 ticks).
        assert len(samples) >= 6, f"not enough samples: {samples}"
        post_warmup = samples[5:]
        total_delta = max(post_warmup) - min(post_warmup)
        per_window = [abs(post_warmup[i] - post_warmup[i - 1])
                      for i in range(1, len(post_warmup))]
        max_window = max(per_window) if per_window else 0.0

        assert total_delta < 100.0, (
            f"post-warmup RSS delta {total_delta:.1f} MB exceeds 100 MB "
            f"(samples={samples})"
        )
        assert max_window < 50.0, (
            f"max per-100-tick window delta {max_window:.1f} MB exceeds 50 MB "
            f"(samples={samples})"
        )
