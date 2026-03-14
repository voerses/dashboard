"""V4 Data Loader — unified read path for historical + live data.

Implements the kdb+-inspired RDB/HDB separation pattern:
- Historical data (HDB): data/{market}/1h_cache/{TOKEN}_1h.parquet
  Written by build_parquet_cache.py only. Immutable between rebuilds.
- Live buffer (RDB): data/{market}/live/{TOKEN}.parquet
  Written by live_fetcher.py only. Small, recent bars.

load_token_data() merges both at read time, producing a single DataFrame
that consumers (signals.py, portfolio_signals.py) use transparently.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

import pandas as pd

# Default data directory — matches v4/signals.py convention.
DATA_DIR = os.environ.get("DATA_DIR", "data")


def _ensure_datetime_index(df: pd.DataFrame) -> pd.DataFrame:
    """Convert integer index to DatetimeIndex if needed."""
    if not isinstance(df.index, pd.DatetimeIndex):
        df = df.copy()
        # Detect unit from magnitude of first value.
        # Boundaries chosen for crypto data (2010-2040):
        #   seconds:      ~1.3e9 to ~2.2e9
        #   milliseconds: ~1.3e12 to ~2.2e12
        #   microseconds: ~1.3e15 to ~2.2e15
        #   nanoseconds:  ~1.3e18 to ~2.2e18
        if len(df) > 0:
            sample = abs(int(df.index[0]))
            if sample > 1e18:
                unit = "ns"
            elif sample > 1e14:
                unit = "us"
            elif sample > 1e11:
                unit = "ms"
            else:
                unit = "s"
        else:
            unit = "ms"
        df.index = pd.to_datetime(df.index, unit=unit)
    # Strip timezone if present
    if hasattr(df.index, "tz") and df.index.tz is not None:
        df.index = df.index.tz_convert("UTC").tz_localize(None)
    return df


def historical_path(token: str, market: str, data_dir: str = DATA_DIR) -> Path:
    """Path to the historical (immutable) parquet file."""
    return Path(data_dir) / market / "1h_cache" / f"{token}_1h.parquet"


def live_path(token: str, market: str, data_dir: str = DATA_DIR) -> Path:
    """Path to the live buffer parquet file."""
    return Path(data_dir) / market / "live" / f"{token}.parquet"


def load_token_data(
    token: str,
    market: str,
    data_dir: str = DATA_DIR,
) -> pd.DataFrame | None:
    """Load token data by merging historical + live buffer.

    Returns a single DataFrame with:
    - DatetimeIndex (tz-naive)
    - Deduplicated on index (live wins on overlap)
    - Sorted chronologically

    Returns None if no data exists for this token/market.
    """
    hist_pq = historical_path(token, market, data_dir)
    live_pq = live_path(token, market, data_dir)

    df_hist = None
    df_live = None

    if hist_pq.exists():
        try:
            df_hist = pd.read_parquet(hist_pq)
            df_hist = _ensure_datetime_index(df_hist)
        except Exception as e:
            logging.getLogger(__name__).warning(
                "Corrupt historical parquet for %s/%s: %s", token, market, e
            )

    if live_pq.exists():
        try:
            df_live = pd.read_parquet(live_pq)
            df_live = _ensure_datetime_index(df_live)
        except Exception as e:
            logging.getLogger(__name__).warning(
                "Corrupt live parquet for %s/%s: %s", token, market, e
            )

    if df_hist is None and df_live is None:
        return None

    if df_hist is not None and df_live is not None:
        # Guard: empty historical — just return live
        if len(df_hist) == 0:
            return df_live
        if len(df_live) == 0:
            return df_hist

        hist_max = df_hist.index.max()
        live_new = df_live[df_live.index > hist_max]
        live_overlap = df_live[df_live.index <= hist_max]

        if len(live_overlap) > 0:
            df_hist = df_hist.copy()

            # Split overlap into: bars that exist in hist (update) vs
            # bars that fill gaps in hist (append via concat).
            in_hist = live_overlap.index.isin(df_hist.index)
            live_update = live_overlap[in_hist]
            live_gap_fill = live_overlap[~in_hist]

            if len(live_update) > 0:
                # For overlapping bars, live wins — but only for columns
                # present in live.  Preserves historical funding data
                # when the live buffer lacks funding_1h/funding_rate.
                shared_cols = df_hist.columns.intersection(live_update.columns)
                df_hist.loc[live_update.index, shared_cols] = live_update[shared_cols]

                # Add live-only columns for overlap bars (e.g., live has
                # funding but historical doesn't)
                live_only_cols = live_update.columns.difference(df_hist.columns)
                if len(live_only_cols) > 0:
                    for col in live_only_cols:
                        df_hist[col] = float("nan")
                    df_hist.loc[live_update.index, live_only_cols] = live_update[live_only_cols]

            # Gap-fill bars go into the concat pool with live_new
            if len(live_gap_fill) > 0:
                live_new = pd.concat([live_new, live_gap_fill])

        if len(live_new) > 0:
            combined = pd.concat([df_hist, live_new])
        else:
            combined = df_hist

        combined = combined.sort_index()
        # Fill NaN funding columns that arise from schema mismatch
        for col in ("funding_1h", "funding_rate"):
            if col in combined.columns:
                combined[col] = combined[col].fillna(0.0)
        return combined

    if df_hist is not None:
        return df_hist

    return df_live


def discover_tokens_from_data(
    market: str,
    data_dir: str = DATA_DIR,
) -> set[str]:
    """Find tokens with data in historical or live directories for a single market.

    Returns a set of token names (e.g. {'BTC', 'ETH', ...}).
    """
    tokens = set()

    # Historical: data/{market}/1h_cache/{TOKEN}_1h.parquet
    hist_dir = Path(data_dir) / market / "1h_cache"
    if hist_dir.is_dir():
        for f in os.listdir(hist_dir):
            if f.endswith("_1h.parquet") and not f.startswith(".") and ".tmp" not in f:
                tokens.add(f.replace("_1h.parquet", ""))

    # Live: data/{market}/live/{TOKEN}.parquet
    live_dir = Path(data_dir) / market / "live"
    if live_dir.is_dir():
        for f in os.listdir(live_dir):
            if f.endswith(".parquet") and not f.startswith(".") and ".tmp" not in f:
                tokens.add(f.replace(".parquet", ""))

    return tokens


def infer_data_end_date(
    market: str = "combined",
    data_dir: str = DATA_DIR,
) -> pd.Timestamp:
    """Get the latest timestamp across historical + live data.

    Checks both 1h_cache and live directories for reference tokens.
    Falls back to scanning all available tokens if reference tokens have no data.
    """
    ref_tokens = ["BTC", "ETH", "SOL"]
    latest = pd.Timestamp.min

    markets = ["spot", "perp"] if market == "combined" else [market]

    for mkt in markets:
        for token in ref_tokens:
            for pq in [historical_path(token, mkt, data_dir),
                       live_path(token, mkt, data_dir)]:
                if pq.exists():
                    try:
                        idx = pd.read_parquet(pq, columns=[]).index
                        if len(idx) > 0:
                            ts = idx.max()
                            if pd.isna(ts):
                                continue
                            if not isinstance(ts, pd.Timestamp):
                                sample = abs(int(ts))
                                if sample > 1e18: unit = "ns"
                                elif sample > 1e14: unit = "us"
                                elif sample > 1e11: unit = "ms"
                                else: unit = "s"
                                ts = pd.Timestamp(ts, unit=unit)
                            if hasattr(ts, "tz") and ts.tz is not None:
                                ts = ts.tz_convert("UTC").tz_localize(None)
                            if ts > latest:
                                latest = ts
                    except Exception:
                        continue

    # Fallback: if reference tokens had no data, scan all available tokens
    if latest == pd.Timestamp.min:
        for mkt in markets:
            all_tokens = discover_tokens_from_data(mkt, data_dir)
            for token in sorted(all_tokens)[:10]:  # cap at 10 to avoid scanning entire universe
                for pq in [historical_path(token, mkt, data_dir),
                           live_path(token, mkt, data_dir)]:
                    if pq.exists():
                        try:
                            idx = pd.read_parquet(pq, columns=[]).index
                            if len(idx) > 0:
                                ts = idx.max()
                                if pd.isna(ts):
                                    continue
                                if not isinstance(ts, pd.Timestamp):
                                    sample = abs(int(ts))
                                    if sample > 1e18: unit = "ns"
                                    elif sample > 1e14: unit = "us"
                                    elif sample > 1e11: unit = "ms"
                                    else: unit = "s"
                                    ts = pd.Timestamp(ts, unit=unit)
                                if hasattr(ts, "tz") and ts.tz is not None:
                                    ts = ts.tz_convert("UTC").tz_localize(None)
                                if ts > latest:
                                    latest = ts
                        except Exception:
                            continue

    if latest == pd.Timestamp.min:
        return pd.Timestamp.now().tz_localize(None)
    return latest


def load_token_data_at(
    token: str,
    market: str,
    as_of: pd.Timestamp,
    data_dir: str = DATA_DIR,
    use_manifest: bool = True,
) -> Optional[pd.DataFrame]:
    """Load token data as it existed at a specific point in time.

    Args:
        token: Token symbol (e.g., "BTC")
        market: Market type ("perp" or "spot")
        as_of: Point-in-time cutoff. When use_manifest=True, this is
            interpreted as a wall-clock time — the manifest determines
            which bars had been promoted by that time. When
            use_manifest=False, this is a market timestamp — data is
            simply trimmed to bars with index <= as_of.
        data_dir: Data directory path.
        use_manifest: If True (default), use the promotion manifest to
            determine the data boundary at as_of. If False, ignore the
            manifest and trim data to bars <= as_of.

    Returns None if no data exists or if as_of is before all data.

    Note: if historical data is rebuilt from CSVs (via
    build_parquet_cache.py), the file hash will change and
    verify_integrity() will report a mismatch. Use
    verify_integrity(allow_rebuild=True) to suppress this.
    """
    from .manifest import get_data_boundary_at

    boundary = None
    if use_manifest:
        boundary = get_data_boundary_at(token, market, as_of, data_dir)

    # Load historical only (no live buffer for point-in-time queries)
    hist_pq = historical_path(token, market, data_dir)
    if not hist_pq.exists():
        return None

    try:
        df = pd.read_parquet(hist_pq)
        df = _ensure_datetime_index(df)
    except Exception as e:
        logging.getLogger(__name__).warning(
            "Corrupt historical parquet for %s/%s: %s", token, market, e
        )
        return None

    if len(df) == 0:
        return None

    if boundary is not None:
        # Trim to manifest boundary
        df = df[df.index <= boundary]
    else:
        # No manifest — best effort: trim to as_of
        if hasattr(as_of, "tz") and as_of.tz is not None:
            as_of = as_of.tz_convert("UTC").tz_localize(None)
        df = df[df.index <= as_of]

    if len(df) == 0:
        return None

    return df
