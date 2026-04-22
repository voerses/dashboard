"""M11 AC-2 parity fixture builder (Commit 8 Stage 2a — real-data 60-day window).

Replaces the frozen fixture's 14-day synthetic random-walk bars with a
60-day window of real BTC+ETH 1h bars + funding + 3 metric parquets in
the layout the ``BUILT_IN_MANIFEST`` seed entries expect. The 22-day
rolling z-score window used by ``s524m`` needs > 22 trading days of
daily metric samples to produce a non-NaN composite; the frozen
fixture's 14 bars/day × 14 days = 336 1h bars ≡ 14 daily samples is
below the warmup threshold and produces composite==NaN on every bar.

This builder slices real on-disk parquets (no network fetch, no
synthetic interpolation) so the resulting fixture is:

  * deterministic (fixed slice window),
  * actually capable of triggering s524m entries (composite has real
    z-score dispersion), and
  * compliant with the ``BUILT_IN_MANIFEST`` path layout
    (``binance_metrics/5min/binance.<metric>.5m/{token}.parquet``) the
    ``ParquetMetricsReplayClient`` reads.

Source data:
  * 1h bars + funding: ``data/perp/1h_cache/{BTC|ETH}_1h.parquet`` →
    rewritten under ``{fixture_root}/perp/1h_cache/{SYMBOL}_1h.parquet``
    and ``{fixture_root}/perp/funding/{SYMBOL}_funding.parquet`` using
    the ``BTCUSDT`` / ``ETHUSDT`` symbol convention the frozen test's
    ``_inst`` helper and ``ParquetReplayClient._bar_source_paths`` both
    assume.
  * metrics: ``data/alternative/binance_metrics/5min/{TOKEN}USDT_5min.parquet``
    (columns ``create_time``, ``sum_open_interest_value``,
    ``sum_toptrader_long_short_ratio``, ``sum_taker_long_short_vol_ratio``)
    → split into per-metric parquets with the schema
    ``timestamp`` / ``value`` the manifest's seed entries declare.

Guided by:
  * ``knowledge/adr/ADR-0001`` + ``ADR-0002`` (event-driven dispatch,
    polymorphic Data),
  * ``.specs/active/m11-unified-event-loop/design.md`` §2/§5 (scope
    map + cache contract),
  * ``v5/data/metrics.py::BUILT_IN_MANIFEST`` (seed entry layout), and
  * the authoritative ``_load_daily_signals`` producer from
    ``git show 1d6a3b6:strategies/s524m_portfolio_rank.py:483-608``.

Window: 2024-10-01 → 2024-11-30 UTC (61 calendar days; covers the
Q4-2024 BTC breakout so price series has enough dispersion to fire both
long and short composite signals across the universe).
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd

from v5.bar_spec import BarSpec
from v5.data.metrics import BUILT_IN_MANIFEST, MetricsManifest
from v5.data.streams import InstrumentId, Venue


__all__ = ["build_ac2_fixture"]


# ----------------------------------------------------------------------
# Window + universe
# ----------------------------------------------------------------------


# 60-day slice. Start / end are UTC-naive pandas Timestamps; converted
# to ns-since-epoch via ``pd.Timestamp(...).value`` below.
_WINDOW_START = pd.Timestamp("2024-10-01T00:00:00Z")
_WINDOW_END = pd.Timestamp("2024-11-30T00:00:00Z")


# (canonical_symbol, source_token) — canonical matches the frozen test's
# ``_inst`` helper (``BTCUSDT`` / ``ETHUSDT``); source_token is the file
# prefix used under ``data/perp/1h_cache/`` + ``data/alternative/...``.
_UNIVERSE: List[tuple[str, str, str]] = [
    # (canonical_symbol, bar_file_stem, metric_file_stem)
    ("BTCUSDT", "BTC", "BTCUSDT"),
    ("ETHUSDT", "ETH", "ETHUSDT"),
]


_METRIC_COL_MAP = {
    # metric_id in BUILT_IN_MANIFEST → source column in the bundled
    # 5-min parquet. These mappings mirror the v4 _load_daily_signals
    # call shape exactly (one 5-min parquet carries all three metrics).
    "binance.open_interest.5m": "sum_open_interest_value",
    "binance.top_trader_ls.5m": "sum_toptrader_long_short_ratio",
    "binance.taker_ls_vol.5m": "sum_taker_long_short_vol_ratio",
}


# ----------------------------------------------------------------------
# Slice helpers
# ----------------------------------------------------------------------


def _project_root() -> Path:
    """Resolve the project root from this file's location.

    ``v5/tests/fixtures/_m11_parity_builder_v2.py`` → repo root is
    three parents up (``v5/tests/fixtures/`` → ``v5/tests/`` → ``v5/``
    → repo root).
    """
    return Path(__file__).resolve().parents[3]


def _slice_bars(source: Path, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """Read the source 1h parquet and slice to [start, end)."""
    df = pd.read_parquet(source)
    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError(
            f"expected DatetimeIndex on {source!s}, got "
            f"{type(df.index).__name__}"
        )
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    else:
        df.index = df.index.tz_convert("UTC")
    mask = (df.index >= start) & (df.index < end)
    return df.loc[mask].copy()


def _write_bar_parquet(
    df: pd.DataFrame, dest_dir: Path, symbol: str,
) -> None:
    """Emit the ``{SYMBOL}_1h.parquet`` file with the columns the
    ``ParquetReplayClient`` reads.

    Schema: DatetimeIndex + columns open/high/low/close/volume. The
    underlying M11 replay client tolerates UTC-aware or UTC-naive
    indexes; for deterministic cross-platform reads we write a
    ``timestamp`` column AND set it as the index (matches the frozen
    test's own fixture shape).
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    out = pd.DataFrame(
        {
            "open": df["open"].astype("float64"),
            "high": df["high"].astype("float64"),
            "low": df["low"].astype("float64"),
            "close": df["close"].astype("float64"),
            "volume": df["volume"].astype("float64"),
        },
        index=df.index.rename("timestamp"),
    )
    out.to_parquet(dest_dir / f"{symbol}_1h.parquet")


def _write_funding_parquet(
    df: pd.DataFrame, dest_dir: Path, symbol: str,
) -> None:
    """Emit the ``{SYMBOL}_funding.parquet`` file keyed on 8h settlement
    boundaries.

    The source 1h parquet has a ``funding_rate`` column aligned per
    hour; real Binance funding settles every 8 hours at 00/08/16 UTC.
    We resample by taking the funding_rate value at those boundaries.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    # Resample to 8h boundaries, taking the first non-null rate in each
    # window as a proxy for the settlement value.
    eight_h = df["funding_rate"].resample(
        "8h", label="left", closed="left",
    ).first().dropna()
    if eight_h.empty:
        # Fall back to a zero-rate series anchored to 8h boundaries
        # within the window so the replay client has something to iterate.
        idx = pd.date_range(
            start=df.index.min().floor("8h"),
            end=df.index.max(),
            freq="8h",
            tz="UTC",
        )
        eight_h = pd.Series(0.0, index=idx, dtype="float64")

    # next_funding_ts = next 8h boundary in ns.
    next_ts_ns = (
        eight_h.index + pd.Timedelta(hours=8)
    ).astype("datetime64[ns, UTC]").astype("int64")
    out = pd.DataFrame(
        {
            "funding_rate": eight_h.astype("float64").values,
            "next_funding_ts": next_ts_ns,
        },
        index=eight_h.index.rename("timestamp"),
    )
    out.to_parquet(dest_dir / f"{symbol}_funding.parquet")


def _write_metric_parquets(
    source_5min: Path,
    dest_root: Path,
    token_symbol: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> None:
    """Slice the bundled 5-min metrics parquet + emit one per-metric
    parquet per ``BUILT_IN_MANIFEST`` seed entry.

    Emits three files under
    ``{dest_root}/binance_metrics/5min/binance.<metric>.5m/{token_symbol}.parquet``
    each with columns ``timestamp`` (ns ts) + ``value`` (float64).
    """
    df = pd.read_parquet(
        source_5min,
        columns=[
            "create_time",
            "sum_open_interest_value",
            "sum_toptrader_long_short_ratio",
            "sum_taker_long_short_vol_ratio",
        ],
    )
    df["create_time"] = pd.to_datetime(df["create_time"], utc=True)
    df = df.set_index("create_time").sort_index()
    df = df.loc[(df.index >= start) & (df.index < end)].copy()

    for metric_id, src_col in _METRIC_COL_MAP.items():
        if src_col not in df.columns:
            raise KeyError(
                f"column {src_col!r} missing from {source_5min!s}"
            )
        sub = df[[src_col]].dropna()
        if sub.empty:
            # Emit an empty (but schema-correct) parquet so the replay
            # client yields zero events rather than raising.
            out = pd.DataFrame(
                {"value": pd.Series([], dtype="float64")},
                index=pd.DatetimeIndex([], tz="UTC", name="timestamp"),
            )
        else:
            out = pd.DataFrame(
                {"value": sub[src_col].astype("float64")},
                index=sub.index.rename("timestamp"),
            )
        # Match the manifest's source_file_pattern:
        #   binance_metrics/5min/binance.<metric>.5m/{token}.parquet
        metric_dir = dest_root / "binance_metrics" / "5min" / metric_id
        metric_dir.mkdir(parents=True, exist_ok=True)
        out.to_parquet(metric_dir / f"{token_symbol}.parquet")


# ----------------------------------------------------------------------
# Public entry point
# ----------------------------------------------------------------------


def build_ac2_fixture(tmp_path: Path) -> Dict[str, object]:
    """Build the M11 AC-2 parity fixture under ``tmp_path``.

    Returns a dict with keys ``root`` / ``start_ns`` / ``end_ns`` /
    ``instruments`` / ``manifest`` — the same shape the frozen
    ``parity_fixture`` returns, plus ``manifest`` so callers can pass
    the seed manifest through to ``run_backtest``.

    The fixture is materialized on disk under
    ``tmp_path/fixture_data/`` with:

      * ``perp/1h_cache/{SYMBOL}_1h.parquet`` — real BTC/ETH 1h bars.
      * ``perp/funding/{SYMBOL}_funding.parquet`` — real funding rates
        at 8h boundaries.
      * ``binance_metrics/5min/binance.<metric>.5m/{TOKEN}.parquet`` —
        one file per metric per token per ``BUILT_IN_MANIFEST`` seed.
    """
    tmp_path = Path(tmp_path)
    root = tmp_path / "fixture_data"
    root.mkdir(parents=True, exist_ok=True)

    project_root = _project_root()
    bar_cache_root = project_root / "data" / "perp" / "1h_cache"
    metric_cache_root = project_root / "data" / "alternative" / "binance_metrics" / "5min"

    for canonical_symbol, bar_stem, metric_stem in _UNIVERSE:
        # -------- 1h bars + funding --------
        bar_src = bar_cache_root / f"{bar_stem}_1h.parquet"
        if not bar_src.exists():
            raise FileNotFoundError(
                f"build_ac2_fixture requires {bar_src!s}; "
                f"regenerate via tools/build_parquet_cache.py"
            )
        bars = _slice_bars(bar_src, _WINDOW_START, _WINDOW_END)
        if bars.empty:
            raise ValueError(
                f"{bar_src!s} has no rows in "
                f"[{_WINDOW_START}, {_WINDOW_END})"
            )
        _write_bar_parquet(
            bars, root / "perp" / "1h_cache", canonical_symbol,
        )
        _write_funding_parquet(
            bars, root / "perp" / "funding", canonical_symbol,
        )

        # -------- Metrics (one file per metric_id) --------
        metric_src = metric_cache_root / f"{metric_stem}_5min.parquet"
        if not metric_src.exists():
            raise FileNotFoundError(
                f"build_ac2_fixture requires {metric_src!s}; "
                f"see data/alternative/binance_metrics/5min/ "
                f"for expected sources."
            )
        _write_metric_parquets(
            metric_src, root,
            token_symbol=canonical_symbol,
            start=_WINDOW_START, end=_WINDOW_END,
        )

    start_ns = int(_WINDOW_START.value)
    end_ns = int(_WINDOW_END.value)
    instruments = [
        InstrumentId(symbol=sym, venue=Venue.BINANCE, asset_class="perp")
        for sym, _, _ in _UNIVERSE
    ]

    return {
        "root": root,
        "start_ns": start_ns,
        "end_ns": end_ns,
        "instruments": instruments,
        "manifest": BUILT_IN_MANIFEST,
    }
