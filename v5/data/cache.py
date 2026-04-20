"""M6 — MultiInstrumentCache (AC-D1 / AC-D1a / AC-D1b).

Thin wrapper that composes M4's `v5.rolling_cache.RollingCache` by 3-tuple key
`(InstrumentId, BarSpec, role)` per design D3. Memory bounds, ring-buffer
storage, and role-aware lookback (signal=250d / entry=7d / exit=2d) are
inherited from M4 — M6 does not redefine them.

Memory projection reuses M4's shipped constants (`_FIELDS_PER_BAR`,
`_BYTES_PER_CELL`, `maxlen_for_bar_spec`) — no math duplication.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

import numpy as np

from v5.bar_spec import BarSpec
from v5.data.streams import DataKind, DataStream, InstrumentId, Subscription
from v5.rolling_cache import (
    RollingCache,
    _BYTES_PER_CELL,
    _FIELDS_PER_BAR,
    maxlen_for_bar_spec,
)


@dataclass(frozen=True, slots=True)
class BarArrays:
    """Zero-copy views of the underlying RollingCache field arrays."""

    close: np.ndarray
    high: np.ndarray
    low: np.ndarray
    volume: np.ndarray
    atr: np.ndarray
    funding: np.ndarray


class MultiInstrumentCache:
    """Routes (instrument, bar_spec, role) → M4 RollingCache.

    Why 3-tuple: M4's RollingCache lookback depends on role (signal=250d,
    entry=7d, exit=2d). A 2-tuple key with a single default_role forces
    every cache to the signal=250d lookback, which blows M4's 1.2 GB
    aggregate budget for a 236-token fleet. Role belongs in the key.
    """

    def __init__(self):
        self._caches: Dict[Tuple[InstrumentId, BarSpec, str], RollingCache] = {}

    def _get_or_create(
        self,
        inst: InstrumentId,
        spec: BarSpec,
        role: str,
    ) -> RollingCache:
        key = (inst, spec, role)
        rc = self._caches.get(key)
        if rc is None:
            maxlen = maxlen_for_bar_spec(spec, role)
            rc = RollingCache(token=inst.symbol, bar_spec=spec, maxlen=maxlen)
            self._caches[key] = rc
        return rc

    def on_bar(
        self,
        bar,
        role: str,
    ) -> None:
        """Append a bar to the (inst, spec, role) cache. Raises ValueError if
        ts_event violates monotonicity (delegated to RollingCache.append)."""
        rc = self._get_or_create(bar.instrument_id, bar.bar_spec, role)
        rc.append(
            ts_ns=int(bar.ts_event),
            close=float(bar.close),
            high=float(bar.high),
            low=float(bar.low),
            volume=float(bar.volume),
        )

    def arrays(
        self,
        stream: DataStream,
        role: str,
    ) -> BarArrays:
        """Return zero-copy views for the (stream.instrument, stream.bar_spec, role)
        RollingCache. Raises KeyError if no such cache exists."""
        key = (stream.instrument, stream.bar_spec, role)
        rc = self._caches.get(key)
        if rc is None:
            raise KeyError(f"no cache for {key}")
        return BarArrays(
            close=rc.arrays("close"),
            high=rc.arrays("high"),
            low=rc.arrays("low"),
            volume=rc.arrays("volume"),
            atr=rc.arrays("atr"),
            funding=rc.arrays("funding"),
        )

    def compute_projected_memory_mb(self) -> float:
        """Aggregate projected memory (MB) across all live (inst, spec, role) caches.

        Uses M4's shipped constants directly — no math duplication.
        """
        total_bytes = sum(
            rc.maxlen * _FIELDS_PER_BAR * _BYTES_PER_CELL
            for rc in self._caches.values()
        )
        return total_bytes / (1024 * 1024)

    def project_from_subscriptions(self, subs: List[Subscription]) -> float:
        """Aggregate projected memory (MB) across the unique (inst, spec, role)
        tuples that WOULD be created for this subscription set.

        Used by DataEngine.start() to enforce AC-D1b BEFORE any data flows.
        Shares constants with M4's arithmetic so `compute_projected_memory_mb()`
        agrees with this method once all subscriptions have received ≥1 bar.
        """
        unique_tuples = {
            (s.stream.instrument, s.stream.bar_spec, s.role)
            for s in subs if s.stream.data_kind == DataKind.BAR
        }
        total_bytes = sum(
            maxlen_for_bar_spec(spec, role) * _FIELDS_PER_BAR * _BYTES_PER_CELL
            for (_inst, spec, role) in unique_tuples
        )
        return total_bytes / (1024 * 1024)
