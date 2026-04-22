"""M6/M11 — ParquetReplayClient (AC-D3, AC-D15, AC-D20).

DataClient-only (no LiveDataClient interface) — supports REPLAY + PULL_ONCE.
Calling `subscribe` / `subscribe_scheduled` raises NotImplementedError.

Infrastructure wall-clock reads:
  (none — replay is timestamp-driven from parquet file contents; no wall-clock
  reads are performed by this module. Backtest determinism invariant AC24 is
  upheld by construction.)

M11 Commit 3 (ADR-0002): yields ``BarData`` / ``FundingRateData`` subclass
instances directly — the pre-M11 ``_ReplayBar`` duck-typed dataclass has been
deleted.

Bar parquets live at::

    {fixture_root}/{market}/1h_cache/{SYMBOL}_{label}.parquet   (primary)
    {fixture_root}/{market}/live/{SYMBOL}.parquet               (secondary)

Funding parquets live at::

    {fixture_root}/{market}/funding/{SYMBOL}_funding.parquet

where ``market`` is derived from ``instrument.asset_class`` ("perp" or "spot")
and ``label`` is ``BarSpec.label`` (e.g., "1h", "1d"). Rows are filtered to
``[start_ns, end_ns)`` on the parquet's timestamp column/index; events are
de-duplicated on ``(ts_event, instrument)`` across sources; resulting stream
is monotonic in ``ts_event``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, FrozenSet, Iterator, List, Optional

import pandas as pd

from v5.data.streams import (
    BarData,
    DataStream,
    FundingRateData,
    TransportMode,
    Venue,
)


# --- path resolution --------------------------------------------------


def _market_dir(asset_class: str) -> str:
    """Map instrument.asset_class → top-level market directory name.

    Only ``perp`` / ``spot`` have parquet caches today; ``future`` and
    ``option`` raise (no cached sources available).
    """
    if asset_class == "perp":
        return "perp"
    if asset_class == "spot":
        return "spot"
    raise ValueError(
        f"ParquetReplayClient does not support asset_class={asset_class!r}; "
        f"only 'perp' and 'spot' have parquet caches."
    )


def _bar_source_paths(
    fixture_root: Path, market: str, symbol: str, label: str,
) -> List[Path]:
    """Return candidate source paths for a (market, symbol, label) bar stream.

    Primary = ``{market}/1h_cache/{SYMBOL}_{label}.parquet`` (canonical
    cache layout used by ``tools/build_parquet_cache.py`` and
    ``v5/data_loader.py``).

    Secondary = ``{market}/live/{SYMBOL}.parquet`` (live-capture append
    file). Duplicate ``(ts_event, instrument)`` rows across the two
    sources are de-duplicated at replay time.
    """
    return [
        fixture_root / market / f"{label}_cache" / f"{symbol}_{label}.parquet",
        fixture_root / market / "live" / f"{symbol}.parquet",
    ]


def _funding_source_paths(
    fixture_root: Path, market: str, symbol: str,
) -> List[Path]:
    """Return candidate source paths for a funding-rate stream."""
    return [
        fixture_root / market / "funding" / f"{symbol}_funding.parquet",
        fixture_root / market / "funding" / f"{symbol}.parquet",
    ]


# --- timestamp column resolution --------------------------------------


def _extract_ts_ns_column(df: pd.DataFrame) -> pd.Series:
    """Return an int64 nanosecond-since-epoch series aligned with ``df``.

    Handles three shapes seen in the on-disk parquets:
      - ``DatetimeIndex`` as the parquet index (M4 cache)
      - a ``timestamp`` column (M11 test fixtures)
      - an ``open_time`` column (pre-M4 cache format)
    """
    # Prefer a named column if present — fixtures write `timestamp` explicitly.
    # Normalize to ns-resolution int64 regardless of the parquet's native
    # timeUnit (real on-disk parquets are often datetime64[ms]; test fixtures
    # write datetime64[ns]). Forcing .astype('datetime64[ns]') before .astype('int64')
    # makes the unit explicit and deterministic.
    for col in ("timestamp", "open_time", "ts_event", "time"):
        if col in df.columns:
            return (
                pd.to_datetime(df[col], utc=True)
                .astype("datetime64[ns, UTC]")
                .astype("int64")
            )
    idx = df.index
    if isinstance(idx, pd.DatetimeIndex):
        # `idx.asi8` returns the int at the index's NATIVE resolution (often ms
        # for on-disk parquets). Convert to ns first to avoid silent ms-valued
        # replay outputs.
        idx_ns = idx.tz_convert("UTC") if idx.tz is not None else idx.tz_localize("UTC")
        return pd.Series(idx_ns.astype("datetime64[ns, UTC]").astype("int64"), index=df.index)
    if pd.api.types.is_integer_dtype(idx):
        # Assume already ns-since-epoch
        return pd.Series(idx.astype("int64"), index=df.index)
    raise ValueError(
        f"parquet has no recognizable timestamp: columns={list(df.columns)}, "
        f"index_type={type(idx).__name__}"
    )


# --- ParquetReplayClient ----------------------------------------------


class ParquetReplayClient:
    """Deterministic parquet playback. Backtest-only.

    ``supported_modes == {REPLAY, PULL_ONCE}`` — no streaming.

    Serves ``BarData`` and ``FundingRateData`` streams from on-disk parquet
    files. Other ``Data`` subclasses (``TradeData``, ``OrderBookData``,
    ``MetricData``, etc.) fall through ``supports()``: ``MetricData`` is
    handled by the separate ``ParquetMetricsReplayClient`` (Commit 4).
    """

    venue: Venue = Venue.BINANCE
    supported_modes: FrozenSet[TransportMode] = frozenset({
        TransportMode.REPLAY, TransportMode.PULL_ONCE,
    })

    # Default pyarrow batch size — streaming iteration without loading the
    # whole parquet into memory. 10_000 rows ≈ 1.2y of hourly bars per
    # batch, enough to amortize file-open overhead while capping footprint.
    _DEFAULT_BATCH_SIZE: int = 10_000

    def __init__(self, fixture_root: Path | None = None):
        # fixture_root lets tests point at a custom parquet tree; defaults
        # to the project-level `data/` directory used by v5/data_loader.py.
        self._fixture_root = (
            Path(fixture_root)
            if fixture_root is not None
            else Path(__file__).resolve().parents[3] / "data"
        )

    # ------------------------------------------------------------
    # Protocol conformance
    # ------------------------------------------------------------

    def supports(self, stream: DataStream, mode: TransportMode) -> bool:
        if mode not in self.supported_modes:
            return False
        return stream.data_class in (BarData, FundingRateData)

    def connect(self) -> None:
        # No network or file open — parquet reads happen lazily per-replay call.
        pass

    def disconnect(self) -> None:
        pass

    def request(
        self, stream: DataStream, start_ns: int, end_ns: int,
    ) -> List[Any]:
        """PULL_ONCE snapshot — materialize the requested window into a list."""
        return list(self.replay(stream, start_ns, end_ns))

    def replay(
        self, stream: DataStream, start_ns: int, end_ns: int,
    ) -> Iterator[Any]:
        """REPLAY — yield events from parquet in chronological order within
        ``[start_ns, end_ns)``.

        Streams the parquet file row-group-by-row-group (pyarrow
        ``ParquetFile.iter_batches``) rather than materializing the whole
        file into a DataFrame, so large bar histories do not blow out
        memory. Rows outside the time window are skipped; duplicates on
        ``(ts_event, instrument)`` across multiple source paths are
        de-duplicated before yielding.

        Raises ``ValueError`` (not propagated as a raise here, but noted)
        when the resulting stream would be non-monotonic — downstream
        ``GapDetector`` will reject non-monotonic bars. This iterator
        emits strictly-increasing ``ts_event`` within a single stream.
        """
        data_class = stream.data_class
        if data_class is BarData:
            yield from self._replay_bars(stream, int(start_ns), int(end_ns))
            return
        if data_class is FundingRateData:
            yield from self._replay_funding(stream, int(start_ns), int(end_ns))
            return
        # Other Data subclasses: empty — let the engine's unsupported-stream
        # branch surface the error rather than raising here (supports()
        # already declares the actual set).
        return

    # ------------------------------------------------------------
    # Subclass-specific replay
    # ------------------------------------------------------------

    def _replay_bars(
        self, stream: DataStream, start_ns: int, end_ns: int,
    ) -> Iterator[BarData]:
        market = _market_dir(stream.instrument.asset_class)
        symbol = stream.instrument.symbol
        label = stream.bar_spec.label
        paths = _bar_source_paths(self._fixture_root, market, symbol, label)

        seen_ts: set = set()
        # Collect candidate rows from every source path; sort + dedup
        # across sources before yielding. For a single-source path the
        # iterator is effectively streaming; multi-source cases
        # (cache + live append) still respect the time window but buffer
        # the union to guarantee global monotonicity.
        candidate_rows: List[tuple] = []
        for path in paths:
            if not path.exists():
                continue
            for ts_ns, o, h, l, c, v in self._iter_bar_rows(
                path, start_ns, end_ns,
            ):
                candidate_rows.append((ts_ns, o, h, l, c, v))

        # Global chronological sort + dedup on ts_event (instrument is
        # fixed per stream).
        candidate_rows.sort(key=lambda r: r[0])
        for ts_ns, o, h, l, c, v in candidate_rows:
            if ts_ns in seen_ts:
                continue
            seen_ts.add(ts_ns)
            yield BarData(
                instrument=stream.instrument,
                ts_event=ts_ns,
                ts_init=ts_ns,
                bar_spec=stream.bar_spec,
                open=float(o),
                high=float(h),
                low=float(l),
                close=float(c),
                volume=float(v),
            )

    def _iter_bar_rows(
        self, path: Path, start_ns: int, end_ns: int,
    ) -> Iterator[tuple]:
        """Stream (ts_ns, open, high, low, close, volume) tuples from a
        bar parquet file within ``[start_ns, end_ns)``.

        Uses pyarrow's row-group iterator to keep memory bounded. Falls
        back to a single ``pandas.read_parquet`` when pyarrow is not
        available or the file is laid out in a way that can't be
        row-group-streamed cleanly.
        """
        try:
            import pyarrow.parquet as pq
        except ImportError:  # pragma: no cover — pyarrow is a hard dep
            pq = None

        if pq is not None:
            try:
                pf = pq.ParquetFile(path)
                # Use the recorded schema to decide whether the index is
                # stored as a column ("timestamp") or as a pandas-index.
                for batch in pf.iter_batches(
                    batch_size=self._DEFAULT_BATCH_SIZE,
                ):
                    df = batch.to_pandas()
                    ts = _extract_ts_ns_column(df)
                    mask = (ts >= start_ns) & (ts < end_ns)
                    if not mask.any():
                        continue
                    sub = df[mask]
                    ts_sub = ts[mask]
                    opens = sub["open"].to_numpy()
                    highs = sub["high"].to_numpy()
                    lows = sub["low"].to_numpy()
                    closes = sub["close"].to_numpy()
                    volumes = sub["volume"].to_numpy()
                    ts_arr = ts_sub.to_numpy()
                    for i in range(len(sub)):
                        yield (
                            int(ts_arr[i]),
                            float(opens[i]),
                            float(highs[i]),
                            float(lows[i]),
                            float(closes[i]),
                            float(volumes[i]),
                        )
                return
            except Exception:
                # Fall through to pandas.read_parquet fallback
                pass

        df = pd.read_parquet(path, engine="pyarrow")
        ts = _extract_ts_ns_column(df)
        mask = (ts >= start_ns) & (ts < end_ns)
        if not mask.any():
            return
        sub = df[mask]
        ts_sub = ts[mask]
        opens = sub["open"].to_numpy()
        highs = sub["high"].to_numpy()
        lows = sub["low"].to_numpy()
        closes = sub["close"].to_numpy()
        volumes = sub["volume"].to_numpy()
        ts_arr = ts_sub.to_numpy()
        for i in range(len(sub)):
            yield (
                int(ts_arr[i]),
                float(opens[i]),
                float(highs[i]),
                float(lows[i]),
                float(closes[i]),
                float(volumes[i]),
            )

    def _replay_funding(
        self, stream: DataStream, start_ns: int, end_ns: int,
    ) -> Iterator[FundingRateData]:
        market = _market_dir(stream.instrument.asset_class)
        symbol = stream.instrument.symbol
        paths = _funding_source_paths(self._fixture_root, market, symbol)

        seen_ts: set = set()
        candidate_rows: List[tuple] = []
        for path in paths:
            if not path.exists():
                continue
            for ts_ns, rate, next_ts in self._iter_funding_rows(
                path, start_ns, end_ns,
            ):
                candidate_rows.append((ts_ns, rate, next_ts))

        candidate_rows.sort(key=lambda r: r[0])
        for ts_ns, rate, next_ts in candidate_rows:
            if ts_ns in seen_ts:
                continue
            seen_ts.add(ts_ns)
            # FundingRateData invariant requires next_funding_ts > ts_event.
            # If the source row doesn't supply one, fall back to
            # ts_event + 8h (Binance default funding interval).
            nf = int(next_ts) if next_ts is not None else ts_ns + 8 * 3_600 * 1_000_000_000
            if nf <= ts_ns:
                nf = ts_ns + 8 * 3_600 * 1_000_000_000
            yield FundingRateData(
                instrument=stream.instrument,
                ts_event=ts_ns,
                ts_init=ts_ns,
                rate=float(rate),
                next_funding_ts=nf,
            )

    def _iter_funding_rows(
        self, path: Path, start_ns: int, end_ns: int,
    ) -> Iterator[tuple]:
        """Stream (ts_ns, funding_rate, next_funding_ts) tuples from a
        funding parquet within ``[start_ns, end_ns)``.

        Accepts several column-naming conventions seen in on-disk files:
          - ``funding_rate`` / ``rate``        — funding value
          - ``next_funding_ts`` / ``next_ts``  — next settlement ts (ns) — optional
        """
        df = pd.read_parquet(path, engine="pyarrow")
        ts = _extract_ts_ns_column(df)
        mask = (ts >= start_ns) & (ts < end_ns)
        if not mask.any():
            return
        sub = df[mask]
        ts_sub = ts[mask]

        rate_col: Optional[str] = None
        for c in ("funding_rate", "rate", "value"):
            if c in sub.columns:
                rate_col = c
                break
        if rate_col is None:
            raise ValueError(
                f"funding parquet {path!s} missing rate column; "
                f"columns={list(sub.columns)}"
            )
        rates = sub[rate_col].to_numpy()

        next_ts_arr = None
        for c in ("next_funding_ts", "next_ts", "next_funding_timestamp"):
            if c in sub.columns:
                raw = sub[c]
                # Allow both integer-ns and Timestamp columns.
                if pd.api.types.is_datetime64_any_dtype(raw):
                    next_ts_arr = raw.astype("int64").to_numpy()
                else:
                    next_ts_arr = raw.astype("int64").to_numpy()
                break

        ts_arr = ts_sub.to_numpy()
        for i in range(len(sub)):
            nt = int(next_ts_arr[i]) if next_ts_arr is not None else None
            yield (
                int(ts_arr[i]),
                float(rates[i]),
                nt,
            )

    # ------------------------------------------------------------
    # LiveDataClient ops — ParquetReplayClient does NOT implement
    # LiveDataClient, but paper/live callers may probe via duck typing.
    # Raise clearly.
    # ------------------------------------------------------------

    def subscribe(self, stream: DataStream) -> None:
        raise NotImplementedError(
            "ParquetReplayClient does not support PUSH (WS streaming). "
            "Use BinanceWSClient for live subscriptions."
        )

    def unsubscribe(self, stream: DataStream) -> None:
        raise NotImplementedError(
            "ParquetReplayClient does not support PUSH (WS streaming)."
        )

    def subscribe_scheduled(self, stream: DataStream, interval_s: int) -> None:
        raise NotImplementedError(
            "ParquetReplayClient does not support PULL_SCHEDULED (cron REST)."
        )
