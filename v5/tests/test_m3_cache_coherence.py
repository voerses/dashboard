"""M3 acceptance tests — RollingCache coherence & cross-sectional ts-alignment.

Covers:
  - AC18: Re-seed after live→historical promotion. When the source parquet
           gains bars mid-run, the RollingCache for that (token, BarType) is
           re-seeded BEFORE the next incremental compute. Post-promotion EMA
           value equals a fresh-rebuild EMA on the same parquet.
  - AC22: Cross-sectional ranking ts-alignment. Before computing cross-sectional
           ranks, tokens lagging > 1 bar-period are excluded and their absence
           counted in `state.rejections.cross_sectional_stale`.

All tests MUST FAIL today — no RollingCacheRegistry, no promotion-event hook,
no ts-alignment guard at cross-sectional sites, no `cross_sectional_stale`
RejectionStats field.

Seed: 42.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


NS_PER_HOUR = 3_600 * 1_000_000_000
BASE_TS = np.int64(1_770_000_000) * np.int64(1_000_000_000)


def _ts_sequence(n: int, start: np.int64 = BASE_TS,
                 step_ns: int = NS_PER_HOUR) -> np.ndarray:
    return start + np.arange(n, dtype=np.int64) * np.int64(step_ns)


def _make_parquet_df(n: int, start_price: float = 100.0) -> pd.DataFrame:
    """Synthetic 1h parquet with a simple price series."""
    np.random.seed(42)
    ts = _ts_sequence(n)
    close = start_price + np.cumsum(np.random.normal(0, 0.5, n)).astype(np.float64)
    return pd.DataFrame({
        "timestamp": ts,
        "close": close,
        "high": close + 1.0,
        "low": close - 1.0,
        "volume": np.full(n, 1000.0),
        "atr": np.full(n, 5.0),
        "funding": np.zeros(n),
    })


# ===================================================================
# AC18 — Re-seed after live->historical promotion
# ===================================================================

class TestAC18ReseedAfterPromotion:
    """On parquet drift detection, cache is re-seeded prior to signal compute."""

    def test_registry_exists(self):
        from v5.rolling_cache import RollingCacheRegistry  # noqa: F401

    def test_reseed_from_parquet_method_exists(self):
        from v5.rolling_cache import RollingCache, BarType
        cache = RollingCache(
            token="BTC", bar_type=BarType.ONE_HOUR, maxlen=500,
        )
        assert hasattr(cache, "reseed_from_parquet") or hasattr(
            cache, "reseed"
        ), "RollingCache must expose reseed_from_parquet() per AC18"

    def test_check_parquet_drift_detects_newer_rows(self, tmp_path):
        """Seed from a parquet, write a newer parquet, then drift check
        must report True (drift detected). The return value is the AC19
        contract — not a consequence of a side effect on the cache state."""
        from v5.rolling_cache import RollingCache, BarType
        parquet_path = tmp_path / "BTC_1h.parquet"
        df1 = _make_parquet_df(100)
        df1.to_parquet(parquet_path)

        cache = RollingCache(
            token="BTC", bar_type=BarType.ONE_HOUR, maxlen=500,
        )
        cache.seed(df1)

        # Advance the parquet on disk (new bars land after seed)
        df2 = _make_parquet_df(110)
        df2.to_parquet(parquet_path)

        # PRIMARY assertion: check_parquet_drift must return a truthy value
        # when the parquet has newer bars than the cache's watermark.
        drift = cache.check_parquet_drift(parquet_path=str(parquet_path))
        assert drift, (
            f"check_parquet_drift returned falsy ({drift!r}) despite parquet "
            f"having newer bars than the cache watermark"
        )

    def test_check_parquet_drift_false_when_parquet_unchanged(self, tmp_path):
        """Inverse: drift must be False when parquet hasn't advanced."""
        from v5.rolling_cache import RollingCache, BarType
        parquet_path = tmp_path / "BTC_1h.parquet"
        df1 = _make_parquet_df(100)
        df1.to_parquet(parquet_path)
        cache = RollingCache(
            token="BTC", bar_type=BarType.ONE_HOUR, maxlen=500,
        )
        cache.seed(df1)
        drift = cache.check_parquet_drift(parquet_path=str(parquet_path))
        assert not drift, (
            f"check_parquet_drift returned truthy ({drift!r}) for unchanged parquet"
        )

    def test_post_promotion_reseed_matches_fresh_rebuild(self, tmp_path):
        """After a promotion rewrites bars in the parquet, the cache's
        incremental-indicator state equals a fresh rebuild on the new parquet.

        We test this via the EMA of close across the seeded window: build an
        EMA from the cache's post-reseed close array, and compare against the
        EMA computed from the freshly-loaded parquet. The two must match.
        """
        from v5.rolling_cache import RollingCache, BarType
        parquet_path = tmp_path / "BTC_1h.parquet"
        df1 = _make_parquet_df(100)
        df1.to_parquet(parquet_path)

        cache = RollingCache(
            token="BTC", bar_type=BarType.ONE_HOUR, maxlen=500,
        )
        cache.seed(df1)

        # Promotion rewrites history: price overwritten for earlier bars
        df2 = df1.copy()
        df2.loc[df2.index[:20], "close"] = df2.loc[df2.index[:20], "close"] + 5.0
        df2.loc[df2.index[:20], "high"] = df2.loc[df2.index[:20], "close"] + 1.0
        df2.loc[df2.index[:20], "low"] = df2.loc[df2.index[:20], "close"] - 1.0
        df2.to_parquet(parquet_path)

        # Force reseed
        def _load(path):
            return pd.read_parquet(path)

        if hasattr(cache, "reseed_from_parquet"):
            cache.reseed_from_parquet(
                token="BTC",
                bar_type=BarType.ONE_HOUR,
                load_fn=lambda t, bt: _load(parquet_path),
            )
        else:
            cache.reseed(df2)

        cache_close = cache.arrays("close")
        df_fresh = pd.read_parquet(parquet_path)

        # Direct data assertion: the cache's post-reseed close array MUST
        # match the post-promotion parquet's close column in the overwritten
        # prefix — proves the reseed actually picked up the new values, not
        # a no-op that happened to produce the same EMA tail by coincidence.
        fresh_close = df_fresh["close"].to_numpy()
        n_prefix = min(20, len(cache_close), len(fresh_close))
        np.testing.assert_allclose(
            cache_close[:n_prefix], fresh_close[:n_prefix],
            rtol=1e-6, atol=1e-6,
            err_msg="Cache close[:20] mismatch post-reseed — indicates no-op reseed",
        )

        # Secondary EMA parity check (retained from original test shape)
        ema_fresh = df_fresh["close"].ewm(span=20, adjust=False).mean().to_numpy()
        ema_cache = pd.Series(cache_close).ewm(span=20, adjust=False).mean().to_numpy()
        n = min(len(ema_fresh), len(ema_cache))
        np.testing.assert_allclose(
            ema_cache[-n:], ema_fresh[-n:], rtol=1e-4, atol=1e-3,
        )


# ===================================================================
# AC22 — Cross-sectional ts-alignment
# ===================================================================

class TestAC22CrossSectionalTSAlignment:
    """Tokens lagging >1 bar-period are excluded from cross-sectional ranking."""

    def test_rejection_stats_has_cross_sectional_stale_field(self):
        from v5.simulator import RejectionStats
        stats = RejectionStats()
        assert hasattr(stats, "cross_sectional_stale")
        assert stats.cross_sectional_stale == 0

    def test_rejection_stats_to_dict_includes_field(self):
        from v5.simulator import RejectionStats
        stats = RejectionStats()
        d = stats.to_dict()
        assert "cross_sectional_stale" in d
        assert d["cross_sectional_stale"] == 0

    def test_all_aligned_tokens_rank_uses_all(self):
        """5 tokens at identical ts_ns => all participate in the rank."""
        from v5.engine import compute_cross_sectional_rank_aligned
        ts = int(BASE_TS)
        participants = [
            ("BTC", ts, 1.0),
            ("ETH", ts, 2.0),
            ("SOL", ts, 3.0),
            ("XRP", ts, 4.0),
            ("DOGE", ts, 5.0),
        ]
        bar_period_ns = NS_PER_HOUR
        from v5.simulator import RejectionStats
        stats = RejectionStats()
        ranks = compute_cross_sectional_rank_aligned(
            participants, bar_period_ns=bar_period_ns,
            rejection_stats=stats,
        )
        assert len(ranks) == 5
        assert stats.cross_sectional_stale == 0

    def test_lagging_token_excluded(self):
        """Token lagging by 2 bars is excluded; counter increments."""
        from v5.engine import compute_cross_sectional_rank_aligned
        from v5.simulator import RejectionStats
        ts = int(BASE_TS)
        lagged_ts = ts - 2 * NS_PER_HOUR
        participants = [
            ("BTC", ts, 1.0),
            ("ETH", ts, 2.0),
            ("SOL", ts, 3.0),
            ("XRP", ts, 4.0),
            ("DOGE", lagged_ts, 5.0),  # lagging 2 bars
        ]
        bar_period_ns = NS_PER_HOUR
        stats = RejectionStats()
        ranks = compute_cross_sectional_rank_aligned(
            participants, bar_period_ns=bar_period_ns,
            rejection_stats=stats,
        )
        assert len(ranks) == 4
        assert stats.cross_sectional_stale == 1

    def test_within_tolerance_not_excluded(self):
        """Token lagging by < 1 bar period (sub-hourly skew) is NOT excluded."""
        from v5.engine import compute_cross_sectional_rank_aligned
        from v5.simulator import RejectionStats
        ts = int(BASE_TS)
        close_ts = ts - int(0.5 * NS_PER_HOUR)
        participants = [
            ("BTC", ts, 1.0),
            ("ETH", close_ts, 2.0),  # 30-min lag, within tolerance
        ]
        bar_period_ns = NS_PER_HOUR
        stats = RejectionStats()
        ranks = compute_cross_sectional_rank_aligned(
            participants, bar_period_ns=bar_period_ns,
            rejection_stats=stats,
        )
        assert len(ranks) == 2
        assert stats.cross_sectional_stale == 0


# ===================================================================
# AC14 — sha256 sidecar fallback (Task 11b)
# ===================================================================

class TestAC14SideCarFallback:
    """Sidecar `.npz` integrity: sha256 mismatch or missing file => cold-start
    fallback. No exception raised, WARNING logged, and an audit event line is
    appended to a telemetry/audit log containing
    `{"event": "rolling_cache_fallback", ...}`.

    Happy path: sidecar verifies + loads, cache contents match what was saved,
    and NO fallback event is recorded.
    """

    # ---- helpers ------------------------------------------------------

    @staticmethod
    def _seed_registry(engine, n_bars: int = 150):
        """Seed the engine's rolling-cache registry with one BTC 1h cache."""
        from v5.rolling_cache import BarType
        registry = engine._rolling_cache_registry
        cache = registry.subscribe("BTC", BarType.ONE_HOUR)
        df = _make_parquet_df(n_bars)
        cache.seed(df)
        return cache

    @staticmethod
    def _find_sidecar(state_dir: Path) -> Path | None:
        """Return the .npz sidecar path inside a state directory."""
        hits = list(state_dir.glob("*.npz"))
        if hits:
            return hits[0]
        return None

    @staticmethod
    def _read_audit_events(state_dir: Path) -> list[dict]:
        """Search common audit-log locations for 'rolling_cache_fallback' events.

        The implementation may write to either `<state_dir>/audit.jsonl` or
        `.specs/telemetry.jsonl` (via walk-up); accept any found location.
        Returns the list of matched event dicts.
        """
        import json
        candidates: list[Path] = []
        # In-state-dir locations
        candidates.extend(state_dir.glob("*.jsonl"))
        candidates.extend(state_dir.rglob("audit.jsonl"))
        # Walk-up for .specs/telemetry.jsonl
        cur = state_dir
        for _ in range(8):
            cand = cur / ".specs" / "telemetry.jsonl"
            if cand.exists():
                candidates.append(cand)
            if cur.parent == cur:
                break
            cur = cur.parent
        # Also check project root's telemetry
        project_root = Path(__file__).resolve().parent.parent.parent
        proj_tel = project_root / ".specs" / "telemetry.jsonl"
        if proj_tel.exists():
            candidates.append(proj_tel)

        events: list[dict] = []
        seen_paths: set[Path] = set()
        for p in candidates:
            rp = p.resolve()
            if rp in seen_paths or not rp.exists():
                continue
            seen_paths.add(rp)
            try:
                for line in rp.read_text().splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    if isinstance(obj, dict) and obj.get("event") == "rolling_cache_fallback":
                        events.append(obj)
            except Exception:
                continue
        return events

    @staticmethod
    def _audit_snapshot(state_dir: Path) -> int:
        """Return current count of rolling_cache_fallback events before an action."""
        return len(TestAC14SideCarFallback._read_audit_events(state_dir))

    # ---- tests --------------------------------------------------------

    def test_corrupt_npz_falls_back(self, tmp_path, caplog):
        """Corrupting the sidecar .npz triggers cold-start fallback, not a
        crash, and records an audit event with a sha256-mismatch reason."""
        import logging
        from v5.paper_engine import PaperPortfolioEngine
        from v5.paper_config import PaperConfig
        from v5.paper_state import serialize_state, deserialize_state
        from v5.rolling_cache import BarType

        config = PaperConfig(strategies=[])
        engine = PaperPortfolioEngine(config)
        self._seed_registry(engine, n_bars=150)

        state_dir = tmp_path / "state"
        state_dir.mkdir()
        serialize_state(engine, str(state_dir))

        sidecar = self._find_sidecar(state_dir)
        assert sidecar is not None, (
            "Task 11a save must produce a .npz sidecar in state_dir"
        )

        # Corrupt the .npz bytes (garbage that still claims to be a file)
        sidecar.write_bytes(b"this is not a valid npz archive, just garbage")

        pre_count = self._audit_snapshot(state_dir)

        engine2 = PaperPortfolioEngine(config)
        # Fresh engine must be re-seedable in lieu of the corrupted sidecar;
        # we don't actually need strategy-driven cold-start here — the
        # sha256-mismatch path is expected to NOT raise regardless.
        with caplog.at_level(logging.WARNING):
            deserialize_state(engine2, str(state_dir))

        # 1. No crash — we reached this line.
        # 2. Warning logged somewhere during load.
        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert len(warnings) >= 1, (
            "Expected at least one WARNING log on corrupted sidecar fallback"
        )

        # 3. Audit event recorded with a sha256-mismatch-style reason.
        events_after = self._read_audit_events(state_dir)
        new_events = events_after[pre_count:]
        assert new_events, (
            "Task 11b requires an audit event line with "
            "'event': 'rolling_cache_fallback' on sha256 mismatch"
        )
        reasons = {e.get("reason") for e in new_events}
        assert any(
            r in ("sha256_mismatch", "checksum_mismatch", "hash_mismatch",
                  "corrupt", "corrupted")
            for r in reasons
        ), (
            f"Expected a sha256-mismatch-style reason in audit event, got {reasons!r}"
        )

    def test_missing_npz_falls_back(self, tmp_path, caplog):
        """Deleting the sidecar .npz triggers cold-start fallback with a
        'missing'-style reason."""
        import logging
        from v5.paper_engine import PaperPortfolioEngine
        from v5.paper_config import PaperConfig
        from v5.paper_state import serialize_state, deserialize_state

        config = PaperConfig(strategies=[])
        engine = PaperPortfolioEngine(config)
        self._seed_registry(engine, n_bars=150)

        state_dir = tmp_path / "state"
        state_dir.mkdir()
        serialize_state(engine, str(state_dir))

        sidecar = self._find_sidecar(state_dir)
        assert sidecar is not None, "save must produce a .npz sidecar"
        sidecar.unlink()  # delete, not corrupt

        pre_count = self._audit_snapshot(state_dir)

        engine2 = PaperPortfolioEngine(config)
        with caplog.at_level(logging.WARNING):
            deserialize_state(engine2, str(state_dir))

        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert len(warnings) >= 1, (
            "Expected at least one WARNING log on missing sidecar fallback"
        )

        events_after = self._read_audit_events(state_dir)
        new_events = events_after[pre_count:]
        assert new_events, (
            "Task 11b requires an audit event with "
            "'event': 'rolling_cache_fallback' when sidecar is missing"
        )
        reasons = {e.get("reason") for e in new_events}
        # Accept tolerant set — impl may use any of these keys
        assert any(
            r in ("missing", "npz_missing", "sidecar_missing", "not_found",
                  "file_missing", "absent")
            for r in reasons
        ), (
            f"Expected a 'missing'-style reason in audit event, got {reasons!r}"
        )

    def test_happy_path_sidecar_loads(self, tmp_path, caplog):
        """Clean save/load: sidecar verifies, cache contents match what was
        saved (close[:N] + watermark), and NO fallback event is recorded."""
        import logging
        from v5.paper_engine import PaperPortfolioEngine
        from v5.paper_config import PaperConfig
        from v5.paper_state import serialize_state, deserialize_state
        from v5.rolling_cache import BarType

        config = PaperConfig(strategies=[])
        engine = PaperPortfolioEngine(config)
        saved_cache = self._seed_registry(engine, n_bars=150)
        saved_close = np.asarray(saved_cache.arrays("close")).copy()
        saved_watermark = int(saved_cache.seeded_through_ts_ns)

        state_dir = tmp_path / "state"
        state_dir.mkdir()
        serialize_state(engine, str(state_dir))

        pre_count = self._audit_snapshot(state_dir)

        engine2 = PaperPortfolioEngine(config)
        with caplog.at_level(logging.WARNING):
            deserialize_state(engine2, str(state_dir))

        # Cache is populated and matches what we saved
        loaded_cache = engine2._rolling_cache_registry.get("BTC", BarType.ONE_HOUR)
        assert loaded_cache is not None, (
            "Happy-path load must restore the BTC/ONE_HOUR cache"
        )
        loaded_close = np.asarray(loaded_cache.arrays("close"))
        n = min(len(saved_close), len(loaded_close))
        assert n > 0, "Loaded cache must have content"
        np.testing.assert_allclose(
            loaded_close[:n], saved_close[:n], rtol=1e-6, atol=1e-6,
            err_msg="Happy-path close[] differs between save and load",
        )
        assert int(loaded_cache.seeded_through_ts_ns) == saved_watermark, (
            "Watermark must roundtrip exactly on happy path"
        )

        # NO fallback event appended for a clean load
        events_after = self._read_audit_events(state_dir)
        new_events = events_after[pre_count:]
        assert not new_events, (
            f"Happy path must NOT record a rolling_cache_fallback event; "
            f"got {new_events!r}"
        )
