"""V5 — Pre-allocated numpy ring buffer cache (AC1, AC18-21, AC20, AC35).

M4 extensions:
  - AC20: RollingCache parameterized by BarSpec (sidecar key preserved).
    BarType.ONE_MIN / ONE_HOUR / DAILY are migration aliases pointing at
    the interned canonical BarSpec instances (identity-equal).
  - AC35: maxlen_for_bar_spec(spec, role) returns role-aware lookback
    (signal=250d, entry=7d, exit=2d).
"""
from __future__ import annotations

import logging
import math
from datetime import timedelta
from pathlib import Path
from typing import Callable, Literal, Optional, Union

import numpy as np
import pandas as pd

from v5.bar_spec import BarSpec

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# BarType — module-level migration alias. BarType.ONE_MIN / ONE_HOUR / DAILY
# are identity-equal to the interned BarSpec.from_minutes(1/60/1440). This
# preserves legacy `BarType.ONE_HOUR`-style imports during the migration.
# ---------------------------------------------------------------------------


class BarType:
    """Legacy-compat alias container. Attributes ARE BarSpec instances
    (identity-equal to BarSpec.from_minutes(1/60/1440))."""

    ONE_MIN: BarSpec = BarSpec.from_minutes(1)
    ONE_HOUR: BarSpec = BarSpec.from_minutes(60)
    DAILY: BarSpec = BarSpec.from_minutes(1440)

    def __init__(self, *_, **__):
        raise TypeError("BarType is a namespace of BarSpec aliases; do not instantiate.")


# Legacy default maxlens (M3 parity — keyed by the interned BarSpec instances)
_MAXLEN_BY_BAR: dict[BarSpec, int] = {
    BarType.ONE_MIN: 1440,    # 24h
    BarType.ONE_HOUR: 6000,   # ~250d, covers 200-day lookback + warmup
    BarType.DAILY: 500,
}

_NS_PER_MIN = 60 * 1_000_000_000
_NS_PER_HOUR = 3_600 * 1_000_000_000
_NS_PER_DAY = 24 * _NS_PER_HOUR

_FIELDS = ("close", "high", "low", "volume", "atr", "funding")

# Role-aware lookback defaults for maxlen_for_bar_spec (AC35)
_LOOKBACK_DAYS_BY_ROLE: dict[str, int] = {
    "signal": 250,
    "entry": 7,
    "exit": 2,
}


def maxlen_for_bar_spec(spec: BarSpec, role: str) -> int:
    """Role-aware maxlen: ceil(lookback_days * 1440 / resolution_minutes).

    Canonical roles (signal=250d, entry=7d, exit=2d) keep their bespoke
    defaults. Unknown roles default to the `signal` lookback (250d) — the
    most conservative choice, matches brief §G1 N-role policy where
    role is a free string and the engine must not crash on unrecognized
    domain-specific roles (e.g. "regime", "alpha", "risk").
    """
    lookback_days = _LOOKBACK_DAYS_BY_ROLE.get(role, _LOOKBACK_DAYS_BY_ROLE["signal"])
    minutes = lookback_days * 1440
    return int(math.ceil(minutes / spec.resolution_minutes))


def _coerce_ts_to_ns(ts) -> np.ndarray:
    """Accepts int64 ns array OR pd.Timestamp series; returns int64 ns array."""
    if isinstance(ts, pd.Series):
        if pd.api.types.is_datetime64_any_dtype(ts):
            # pd.Timestamp series -> int64 ns
            return ts.astype("int64").to_numpy()
        return ts.astype(np.int64).to_numpy()
    arr = np.asarray(ts)
    if np.issubdtype(arr.dtype, np.datetime64):
        return arr.astype("datetime64[ns]").astype(np.int64)
    return arr.astype(np.int64)


class RollingCache:
    """Pre-allocated numpy ring buffer for one (token, BarSpec).

    Accepts either `bar_spec=BarSpec(...)` (M4) or `bar_type=BarType.XXX` (M3
    legacy). Both paths normalize to a BarSpec stored on ``self.bar_spec`` and
    ``self.bar_type`` (aliased for M3 compatibility).
    """

    def __init__(
        self,
        token: str,
        bar_type: Optional[BarSpec] = None,
        maxlen: Optional[int] = None,
        *,
        bar_spec: Optional[BarSpec] = None,
        lookback: Optional[timedelta] = None,
    ):
        # Normalize bar_spec/bar_type; exactly one must be provided (and they
        # may be the same instance via the BarType migration alias).
        spec = bar_spec if bar_spec is not None else bar_type
        if spec is None:
            raise ValueError(
                "RollingCache requires either bar_spec=... or bar_type=..."
            )
        if not isinstance(spec, BarSpec):
            raise TypeError(
                f"bar_spec/bar_type must be a BarSpec; got {type(spec).__name__}"
            )

        self.token = token
        # Canonical attribute is `bar_spec`; `bar_type` kept for M3 parity.
        self.bar_spec: BarSpec = spec
        self.bar_type: BarSpec = spec

        # Resolve maxlen: explicit > lookback -> derived > legacy default.
        if maxlen is not None:
            resolved_maxlen = int(maxlen)
        elif lookback is not None:
            # Derive from lookback timedelta and BarSpec resolution.
            total_minutes = lookback.total_seconds() / 60.0
            resolved_maxlen = max(1, int(math.ceil(
                total_minutes / spec.resolution_minutes
            )))
        elif spec in _MAXLEN_BY_BAR:
            resolved_maxlen = _MAXLEN_BY_BAR[spec]
        else:
            # Conservative fallback for non-legacy BarSpecs without lookback:
            # ~250 days at this resolution.
            resolved_maxlen = maxlen_for_bar_spec(spec, "signal")

        self.maxlen = resolved_maxlen
        self._buf = {
            f: np.zeros(self.maxlen, dtype=np.float32) for f in _FIELDS
        }
        self._timestamps = np.zeros(self.maxlen, dtype=np.int64)
        self._head = 0  # next write slot
        self._size = 0  # bars written, capped at maxlen
        self.seeded_through_ts_ns: int = 0

    # -----------------------------------------------------------------
    # Sidecar key — AC20: f"{token}__{bar_spec.label}" preserves legacy
    # "1m"/"1h"/"1d" sidecar filenames on disk.
    # -----------------------------------------------------------------

    @property
    def sidecar_key(self) -> str:
        return f"{self.token}__{self.bar_spec.label}"

    # -----------------------------------------------------------------
    # Writes
    # -----------------------------------------------------------------

    def append(self, ts_ns: int, **fields) -> None:
        """O(1) write. Enforces monotonicity (AC21): raises ValueError if
        ts_ns <= last written ts."""
        ts_ns = int(ts_ns)
        if self._size > 0:
            last_ts = int(self.last_written_ts_ns())
            if ts_ns <= last_ts:
                raise ValueError(
                    f"non-monotonic bar: got {ts_ns}, last={last_ts}"
                )
            # AC20 gap detect on append
            period = self.bar_spec.period_ns
            if period > 0:
                gap = ts_ns - last_ts
                if gap > 2 * period:
                    logger.warning(
                        "RollingCache: bar gap/jump detected for %s/%s: "
                        "delta_ns=%d (expected=%d)",
                        self.token, self.bar_spec.label, gap, period,
                    )

        slot = self._head
        self._timestamps[slot] = ts_ns
        for f in _FIELDS:
            if f in fields:
                self._buf[f][slot] = np.float32(fields[f])
            else:
                self._buf[f][slot] = np.float32(0.0)

        self._head = (self._head + 1) % self.maxlen
        if self._size < self.maxlen:
            self._size += 1

        if ts_ns > self.seeded_through_ts_ns:
            self.seeded_through_ts_ns = ts_ns

    def last_written_ts_ns(self) -> int:
        if self._size == 0:
            return 0
        last_slot = (self._head - 1) % self.maxlen
        return int(self._timestamps[last_slot])

    # -----------------------------------------------------------------
    # Reads
    # -----------------------------------------------------------------

    def _ordered_view(self, arr: np.ndarray) -> np.ndarray:
        """Return size-length ordered array.

        Zero-copy view when not wrapped; concatenate on wrap.
        """
        if self._size == 0:
            return arr[:0]  # view, length 0
        if self._size < self.maxlen:
            # Linear segment 0.._size; zero-copy view
            return arr[: self._size]
        # Wrapped: concatenate tail + head
        head = self._head
        return np.concatenate((arr[head:], arr[:head]))

    def arrays(self, field: str) -> np.ndarray:
        """Zero-copy view when not wrapped; np.concatenate on wrap."""
        if field not in self._buf:
            raise KeyError(f"unknown field: {field}")
        return self._ordered_view(self._buf[field])

    def timestamps(self) -> np.ndarray:
        return self._ordered_view(self._timestamps)

    # -----------------------------------------------------------------
    # Seeding
    # -----------------------------------------------------------------

    def seed(self, df: pd.DataFrame) -> None:
        """Bulk cold-start from historical parquet.

        Expects columns: timestamp (int64 ns or pd.Timestamp),
        close, high, low, volume, atr, funding.
        Missing field columns default to 0.
        """
        if df is None or len(df) == 0:
            return

        ts_ns = _coerce_ts_to_ns(df["timestamp"])
        n = len(ts_ns)

        # AC20: gap detection on seed — any consecutive diff > 1.5x bar_period
        if n >= 2:
            period = self.bar_spec.period_ns
            if period > 0:
                diffs = np.diff(ts_ns)
                gap_mask = diffs > int(1.5 * period)
                n_gaps = int(gap_mask.sum())
                if n_gaps > 0:
                    logger.warning(
                        "RollingCache.seed: %d gaps detected for %s/%s",
                        n_gaps, self.token, self.bar_spec.label,
                    )

        # Keep only last maxlen rows
        if n > self.maxlen:
            df = df.iloc[-self.maxlen:].reset_index(drop=True)
            ts_ns = ts_ns[-self.maxlen:]
            n = self.maxlen

        # Reset state & bulk-copy into buffer positions 0..n
        self._head = n % self.maxlen
        self._size = n
        self._timestamps[:n] = ts_ns

        for f in _FIELDS:
            if f in df.columns:
                vals = np.asarray(df[f].to_numpy(), dtype=np.float32)
            else:
                vals = np.zeros(n, dtype=np.float32)
            self._buf[f][:n] = vals

        # Zero any tail region beyond size (defensive; buffer constructed zeroed)
        if n < self.maxlen:
            self._timestamps[n:] = 0
            for f in _FIELDS:
                self._buf[f][n:] = 0.0

        if n > 0:
            last_ts = int(ts_ns[-1])
            if last_ts > self.seeded_through_ts_ns:
                self.seeded_through_ts_ns = last_ts

    # -----------------------------------------------------------------
    # Parquet drift + reseed
    # -----------------------------------------------------------------

    def check_parquet_drift(self, parquet_path: str) -> bool:
        """Peek the last row's timestamp in the parquet at `parquet_path`;
        return True if latest_ts > self.seeded_through_ts_ns, else False.
        Does NOT reseed — caller decides."""
        try:
            df = pd.read_parquet(parquet_path, columns=["timestamp"])
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "RollingCache.check_parquet_drift: failed to read %s: %s",
                parquet_path, exc,
            )
            return False
        if len(df) == 0:
            return False
        last_ts = int(_coerce_ts_to_ns(df["timestamp"])[-1])
        return last_ts > int(self.seeded_through_ts_ns)

    def reseed_from_parquet(
        self,
        token: str,
        bar_type: BarSpec,
        load_fn: Callable[[str, BarSpec], pd.DataFrame],
    ) -> None:
        """Atomic reseed: clear ring, call load_fn(token, bar_type) -> pd.DataFrame,
        seed() from it. load_fn is a callable the caller provides so this
        function stays data-layer-agnostic."""
        df = load_fn(token, bar_type)
        # Clear state
        self._head = 0
        self._size = 0
        self._timestamps[:] = 0
        for f in _FIELDS:
            self._buf[f][:] = 0.0
        # Do not reset watermark; seed() will advance it if new data is newer
        self.seed(df)


class RollingCacheRegistry:
    """Per-engine registry of RollingCache instances keyed by (token, BarSpec)."""

    def __init__(self):
        self._caches: dict[tuple[str, BarSpec], RollingCache] = {}

    def subscribe(self, token: str, bar_type: BarSpec) -> RollingCache:
        """Explicit subscription gateway — prevents subscribe-after-publish races.
        Idempotent: returns existing cache if already subscribed."""
        key = (token, bar_type)
        if key not in self._caches:
            self._caches[key] = RollingCache(token=token, bar_type=bar_type)
        return self._caches[key]

    def get(self, token: str, bar_type: BarSpec) -> Optional[RollingCache]:
        """Read-only accessor; returns None if not subscribed."""
        return self._caches.get((token, bar_type))

    def seed_all(
        self,
        strategy_specs,
        data_dir: str,
        max_lookback_bars: int,
        load_fn: Optional[Callable[[str, BarSpec], pd.DataFrame]] = None,
    ) -> None:
        """One-shot cold-start: for every (token, bar_type) strategies need,
        subscribe + seed."""
        needs: set[tuple[str, BarSpec]] = set()
        for spec in strategy_specs or []:
            tokens = getattr(spec, "tokens", None) or []
            bar_types = getattr(spec, "bar_types", None) or []
            for t in tokens:
                for bt in bar_types:
                    needs.add((t, bt))

        for token, bar_type in needs:
            cache = self.subscribe(token, bar_type)
            if load_fn is None:
                continue
            try:
                df = load_fn(token, bar_type)
            except Exception as exc:
                logger.warning(
                    "RollingCacheRegistry.seed_all: load_fn failed for "
                    "%s/%s: %s", token, bar_type.label, exc,
                )
                continue
            cache.seed(df)

    def check_parquet_drift(
        self,
        load_fn: Optional[Callable[[str, BarSpec], pd.DataFrame]] = None,
    ) -> list[tuple[str, BarSpec]]:
        """Return the list of (token, bar_type) keys whose caches lag the
        parquet."""
        drifted: list[tuple[str, BarSpec]] = []
        if load_fn is None:
            return drifted
        for key, cache in self._caches.items():
            token, bar_type = key
            try:
                df = load_fn(token, bar_type)
            except Exception:
                continue
            if df is None or len(df) == 0:
                continue
            try:
                last_ts = int(_coerce_ts_to_ns(df["timestamp"])[-1])
            except Exception:
                continue
            if last_ts > int(cache.seeded_through_ts_ns):
                drifted.append(key)
        return drifted

    def all(self) -> list[RollingCache]:
        return list(self._caches.values())


# ---------------------------------------------------------------------------
# Projected memory budget (AC35). Exported via v5.bar_processor as well for
# the "pre-run assertion at sim init" hook.
# ---------------------------------------------------------------------------

_BYTES_PER_CELL = 4       # float32
_FIELDS_PER_BAR = len(_FIELDS)  # 6
_MEMORY_BUDGET_MB = 1200  # 1.2 GB ceiling (AC35)


def compute_projected_memory_mb(strategies, tokens) -> float:
    """Project RSS (MB) required to hold rolling caches for every
    (strategy, role, token) subscription.

    Sums ``maxlen * fields * bytes_per_cell * tokens`` across each strategy's
    declared ``bar_subscriptions`` (keys are role names, values are BarSpecs).
    ``tokens`` may be a list (or other sized iterable) — its length is used.
    """
    try:
        n_tokens = len(tokens)
    except TypeError:
        n_tokens = int(tokens)

    total_bytes = 0
    for strategy in strategies or []:
        subs = getattr(strategy, "bar_subscriptions", None) or {}
        for role, spec in subs.items():
            if spec is None:
                continue
            maxlen = maxlen_for_bar_spec(spec, role)
            total_bytes += (
                maxlen * _FIELDS_PER_BAR * _BYTES_PER_CELL * n_tokens
            )
    return total_bytes / (1024 * 1024)


def assert_memory_budget(strategies, tokens, ceiling_mb: int = _MEMORY_BUDGET_MB) -> float:
    """Assertion helper for sim init — raises MemoryError when projection
    exceeds the 1.2 GB ceiling. Returns the projection for telemetry."""
    projected = compute_projected_memory_mb(strategies, tokens)
    if projected > ceiling_mb:
        raise MemoryError(
            f"Projected rolling-cache memory {projected:.1f} MB exceeds "
            f"ceiling {ceiling_mb} MB"
        )
    return projected
