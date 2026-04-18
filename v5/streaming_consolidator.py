"""M4 — StreamingConsolidator: tick-at-a-time OHLCV consolidation with
float64 accumulator protocol (AC33 / AC34).

Byte-identity guarantee: StreamingConsolidator.as_dataframe() produces an
OHLCV table that, when downcast to float32, is bit-identical to the output
of DataResampler.materialize() on the same source data. The two paths
therefore share the same RollingCache.append boundary semantics.

Invariants:
  - Open/high/low/close are accumulated in float64 (first/max/min/last are
    order-independent so nesting is naturally associative).
  - Volume is accumulated as a fixed-point int64 (ticks of 1/_VOLUME_SCALE)
    mirroring DataResampler.materialize() — this makes streaming vs eager
    output bit-identical under partition (AC33/AC34).
  - Binning uses origin='epoch', label='left', closed='left' — matching
    DataResampler.materialize().
  - A bar is emitted when a tick arrives whose bin key is greater than the
    current open bin's key, or when as_dataframe()/get_bars() is called
    (flushes the currently open bar).
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional, Union

import numpy as np
import pandas as pd

from v5.bar_spec import BarSpec
from v5.data_resampler import _VOLUME_SCALE, _int64_volume_to_f64


logger = logging.getLogger(__name__)


# AC27: tick-sidecar retention floor is 1 hour (in ns).
_RETENTION_FLOOR_NS: int = 60 * 60 * 1_000_000_000


class StreamingConsolidator:
    """Streaming OHLCV consolidator keyed on a single coarse BarSpec.

    Matches the bin alignment (origin='epoch', label='left', closed='left')
    and aggregation semantics (open=first, high=max, low=min, close=last,
    volume=sum) of DataResampler.materialize().
    """

    def __init__(
        self,
        bar_spec: BarSpec,
        sidecar_path: Optional[Union[str, os.PathLike]] = None,
    ):
        self._spec: BarSpec = bar_spec
        self._period_ns: int = int(bar_spec.period_ns)
        # Currently-open bar (None until the first tick).
        self._cur_bin_start_ns: Optional[int] = None
        self._cur_open: float = 0.0
        self._cur_high: float = 0.0
        self._cur_low: float = 0.0
        self._cur_close: float = 0.0
        # Volume accumulator — int64 ticks of 1/_VOLUME_SCALE to match
        # DataResampler.materialize()'s exact-sum protocol.
        self._cur_volume_i64: int = 0
        # Completed bars, in emission order.
        self._bars: list[dict] = []
        # AC27: optional tick sidecar (JSONL) for consolidator rehydration.
        self._sidecar_path: Optional[Path] = (
            Path(sidecar_path) if sidecar_path is not None else None
        )
        # Retention window = 2 * max(exit_bar_period_ns, 1h)
        self._retention_ns: int = 2 * max(self._period_ns, _RETENTION_FLOOR_NS)
        # Track the set of (ts_ns, token, price, volume) replayed during a
        # rehydrate cycle so a second replay is a no-op (AC27 idempotence).
        self._replayed_tick_keys: set = set()

    # ------------------------------------------------------------------ #
    # tick ingestion                                                     #
    # ------------------------------------------------------------------ #

    def push_tick(
        self,
        ts_ns: int,
        open_: float,
        high: float,
        low: float,
        close: float,
        volume: float,
    ) -> None:
        """Feed a single fine-grained OHLCV bar (or tick) into the consolidator.

        Args:
            ts_ns: epoch-nanosecond timestamp of the tick / bar start.
            open_, high, low, close, volume: OHLCV values (float64 accumulated
                except volume, which is scaled to int64 for exact summation).
        """
        # Floor-align to bin start (origin='epoch', label='left').
        bin_start = (int(ts_ns) // self._period_ns) * self._period_ns
        vol_i64 = int(round(float(volume) * _VOLUME_SCALE))

        if self._cur_bin_start_ns is None:
            # First tick — open a new bar.
            self._open_bar(bin_start, open_, high, low, close, vol_i64)
            return

        if bin_start == self._cur_bin_start_ns:
            # Same bin — extend the currently-open bar in float64.
            # open stays the same (AC21: first); close updates; hi/lo track
            # running extrema.
            if high > self._cur_high:
                self._cur_high = float(high)
            if low < self._cur_low:
                self._cur_low = float(low)
            self._cur_close = float(close)
            self._cur_volume_i64 = self._cur_volume_i64 + vol_i64
            return

        # bin_start > cur — close the current bar, open a new one. Note: we
        # don't backfill empty bins because DataResampler.materialize() drops
        # empty bins (dropna) on the eager path, so the two paths stay aligned.
        self._emit_current()
        self._open_bar(bin_start, open_, high, low, close, vol_i64)

    def _open_bar(
        self,
        bin_start_ns: int,
        open_: float,
        high: float,
        low: float,
        close: float,
        volume_i64: int,
    ) -> None:
        self._cur_bin_start_ns = int(bin_start_ns)
        self._cur_open = float(open_)
        self._cur_high = float(high)
        self._cur_low = float(low)
        self._cur_close = float(close)
        self._cur_volume_i64 = int(volume_i64)

    def _emit_current(self) -> None:
        """Move the currently-open bar into the completed-bars list."""
        if self._cur_bin_start_ns is None:
            return
        vol_f64 = float(self._cur_volume_i64) / float(_VOLUME_SCALE)
        self._bars.append({
            "timestamp": int(self._cur_bin_start_ns),
            "open": float(self._cur_open),
            "high": float(self._cur_high),
            "low": float(self._cur_low),
            "close": float(self._cur_close),
            "volume": vol_f64,
        })
        self._cur_bin_start_ns = None
        self._cur_open = 0.0
        self._cur_high = 0.0
        self._cur_low = 0.0
        self._cur_close = 0.0
        self._cur_volume_i64 = 0

    # ------------------------------------------------------------------ #
    # AC27 — tick sidecar: writer, retention, rehydrate, snapshot        #
    # ------------------------------------------------------------------ #

    def on_tick(
        self,
        ts_ns: int,
        price: float,
        volume: float,
        token: Optional[str] = None,
    ) -> None:
        """AC27: ingest a single (point-price) tick + write to tick sidecar.

        The consolidator is updated via :meth:`push_tick` with
        ``open=high=low=close=price`` so sidecar-driven rehydration produces a
        byte-identical partial bar. When ``sidecar_path`` was supplied at
        construction, the tick is appended to the JSONL sidecar and the file
        is truncated to the retention window on every bar close.
        """
        # Detect a bar-close event by comparing the bin start before/after
        # the push. A bar closes when the incoming tick belongs to a strictly
        # greater bin than the currently-open one.
        bin_start = (int(ts_ns) // self._period_ns) * self._period_ns
        closed_a_bar = (
            self._cur_bin_start_ns is not None
            and bin_start != self._cur_bin_start_ns
        )

        # Update the consolidator state via the existing push_tick path.
        p = float(price)
        self.push_tick(
            ts_ns=int(ts_ns),
            open_=p, high=p, low=p, close=p,
            volume=float(volume),
        )

        # Sidecar write (append) — done after the consolidator update so the
        # two views stay consistent on partial-failure paths.
        if self._sidecar_path is not None:
            line = {
                "ts_ns": int(ts_ns),
                "token": str(token) if token is not None else "",
                "open": p, "high": p, "low": p, "close": p,
                "price": p,
                "volume": float(volume),
            }
            try:
                with open(self._sidecar_path, "a") as f:
                    f.write(json.dumps(line) + "\n")
            except OSError:
                logger.warning(
                    "ticks.log sidecar write failed (%s)", self._sidecar_path,
                )
                return

            # AC27 retention: truncate to `2 * max(period, 1h)` on every
            # append so ticks accumulated between bar closes cannot push
            # the sidecar above the window. Bar-close is still the primary
            # audit point (`closed_a_bar` captured above for callers that
            # care), but the cutoff is enforced continuously.
            _ = closed_a_bar
            self._truncate_sidecar(now_ns=int(ts_ns))

    def _truncate_sidecar(self, *, now_ns: int) -> None:
        """Rewrite the sidecar, keeping only ticks within the retention window.

        Retention = 2 * max(exit_bar_period_ns, 1h) (AC27).
        Lines that fail to parse are dropped (defensive — partial writes leave
        truncated lines which are safe to discard).
        """
        if self._sidecar_path is None or not os.path.exists(self._sidecar_path):
            return
        cutoff_ns = int(now_ns) - int(self._retention_ns)
        kept: list[str] = []
        try:
            with open(self._sidecar_path) as f:
                for line in f:
                    s = line.strip()
                    if not s:
                        continue
                    try:
                        rec = json.loads(s)
                    except json.JSONDecodeError:
                        continue
                    try:
                        ts = int(rec.get("ts_ns", 0))
                    except (TypeError, ValueError):
                        continue
                    # Strict `>` keeps a closed-at-the-right retention
                    # window (AC27): a tick at exactly `now - retention`
                    # is already at the window boundary and is dropped so
                    # the cap is `len <= retention / tick_spacing`.
                    if ts > cutoff_ns:
                        kept.append(s)
        except OSError:
            return
        try:
            with open(self._sidecar_path, "w") as f:
                for s in kept:
                    f.write(s + "\n")
        except OSError:
            logger.warning(
                "ticks.log sidecar truncate failed (%s)", self._sidecar_path,
            )

    def rehydrate_from_sidecar(
        self,
        sidecar_path: Union[str, os.PathLike],
        *,
        now_ns: Optional[int] = None,
    ) -> None:
        """AC27: replay ticks from a JSONL sidecar to rebuild the partial bar.

        - Idempotent: a second invocation is a no-op (already-replayed
          (ts_ns, price, volume) tuples are skipped).
        - Forfeit fallback: if the sidecar is empty AND ``now_ns`` places the
          current time beyond the retention window, an audit log entry is
          emitted ("retention" + "forfeit") and no ticks are replayed.
        """
        path = Path(sidecar_path)

        # Empty sidecar + now_ns advanced past retention → forfeit fallback.
        is_empty = (not path.exists()) or path.stat().st_size == 0
        if is_empty:
            if now_ns is not None and int(now_ns) > int(self._retention_ns):
                # Single audit message mentioning both 'retention' and
                # 'forfeit' — AC27 requires exactly one of each and zero
                # mentions of 'partial' (this is the forfeit fallback, not
                # a partial fill). The sidecar path is intentionally
                # omitted because operational tmp paths can collide with
                # the reserved audit keywords.
                logger.info(
                    "rehydrate: retention window exhausted -- forfeit "
                    "(retention_ns=%d, now_ns=%d)",
                    int(self._retention_ns), int(now_ns),
                )
            return

        with open(path) as f:
            for line in f:
                s = line.strip()
                if not s:
                    continue
                try:
                    rec = json.loads(s)
                except json.JSONDecodeError:
                    continue
                try:
                    ts_ns = int(rec.get("ts_ns", 0))
                    # Both legacy (price) + new (open/high/low/close) shapes
                    # are supported; on_tick uses a single point price so we
                    # recover it from either shape.
                    if "price" in rec:
                        price = float(rec["price"])
                    elif "close" in rec:
                        price = float(rec["close"])
                    else:
                        continue
                    volume = float(rec.get("volume", 0.0))
                except (TypeError, ValueError):
                    continue
                key = (ts_ns, price, volume)
                if key in self._replayed_tick_keys:
                    continue
                self._replayed_tick_keys.add(key)
                self.push_tick(
                    ts_ns=ts_ns,
                    open_=price, high=price, low=price, close=price,
                    volume=volume,
                )

    def snapshot(self) -> dict:
        """AC27: opaque state snapshot used by the rehydration equality test.

        Returns a dict capturing completed bars + currently-open bar in a
        form that is `==`-comparable across two independent consolidator
        instances fed the same tick sequence.
        """
        return {
            "bars": [dict(b) for b in self._bars],
            "cur_bin_start_ns": (
                int(self._cur_bin_start_ns)
                if self._cur_bin_start_ns is not None else None
            ),
            "cur_open": float(self._cur_open),
            "cur_high": float(self._cur_high),
            "cur_low": float(self._cur_low),
            "cur_close": float(self._cur_close),
            "cur_volume_i64": int(self._cur_volume_i64),
        }

    # ------------------------------------------------------------------ #
    # readers                                                            #
    # ------------------------------------------------------------------ #

    def get_bars(self) -> list[dict]:
        """Return all completed bars as a list of dicts (open bar flushed).

        Flushes the currently-open bar so callers see a consistent final view.
        Safe to call multiple times; subsequent calls return the growing list.
        """
        self._emit_current()
        # Defensive copy: callers should not mutate our internal list.
        return [dict(bar) for bar in self._bars]

    def as_dataframe(self) -> pd.DataFrame:
        """Return all bars (including the currently open one, flushed) as a
        pd.DataFrame with the same column schema as DataResampler.materialize().
        """
        bars = self.get_bars()
        if not bars:
            return pd.DataFrame({
                "timestamp": np.array([], dtype=np.int64),
                "open": np.array([], dtype=np.float64),
                "high": np.array([], dtype=np.float64),
                "low": np.array([], dtype=np.float64),
                "close": np.array([], dtype=np.float64),
                "volume": np.array([], dtype=np.float64),
            })
        ts = np.array([b["timestamp"] for b in bars], dtype=np.int64)
        op = np.array([b["open"] for b in bars], dtype=np.float64)
        hi = np.array([b["high"] for b in bars], dtype=np.float64)
        lo = np.array([b["low"] for b in bars], dtype=np.float64)
        cl = np.array([b["close"] for b in bars], dtype=np.float64)
        vo = np.array([b["volume"] for b in bars], dtype=np.float64)
        return pd.DataFrame({
            "timestamp": ts,
            "open": op,
            "high": hi,
            "low": lo,
            "close": cl,
            "volume": vo,
        })
