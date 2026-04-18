"""V5 Trade Archive — parquet writer/reader for ClosedTrade overflow (AC5/13/17).

When PositionManager.closed_trades (bounded deque maxlen=1000) is about to
evict older trades, a batch is flushed to disk as a parquet file via atomic
`.tmp` + rename (AC17). Readers use `pyarrow.dataset` over the archive
directory and skip any `.tmp` files so a half-written flush is invisible.
"""
from __future__ import annotations

import glob
import json
import os
import time
from dataclasses import asdict
from typing import List

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from v5.position import ClosedTrade, ScalingEvent


class TradeArchiveWriter:
    """Per-flush parquet writer with atomic .tmp-then-rename (AC17).

    Preserves M2 FIX identity fields (parent_position_id, exec_seq, exec_type,
    is_terminal, triggered_by, has_scaling, scaling_events). `scaling_events`
    is serialized as a JSON-string column because list-of-struct is not
    uniformly portable across all parquet readers.
    """

    def __init__(self, archive_dir: str):
        self.archive_dir = archive_dir
        os.makedirs(archive_dir, exist_ok=True)

    def flush(self, trades_batch: List[ClosedTrade]) -> None:
        """Write a batch of ClosedTrade to a single parquet file atomically."""
        if not trades_batch:
            return
        rows = []
        for t in trades_batch:
            d = asdict(t)
            # scaling_events is list[ScalingEvent] — serialize as JSON string
            events = getattr(t, "scaling_events", None) or []
            if events:
                serialized = []
                for e in events:
                    if hasattr(e, "__dataclass_fields__"):
                        serialized.append(asdict(e))
                    elif isinstance(e, dict):
                        serialized.append(e)
                    else:
                        # best-effort: fall back to str
                        serialized.append(str(e))
                d["scaling_events"] = json.dumps(serialized)
            else:
                d["scaling_events"] = ""
            # Non-serializable attachments (if any future fields appear)
            d.pop("exit_handlers", None)
            rows.append(d)
        df = pd.DataFrame(rows)
        ts_ms = int(time.time() * 1000)
        # Monotonic-ish suffix — include length + a short random token if
        # multiple flushes land in the same ms.
        final = os.path.join(
            self.archive_dir,
            f"batch_{ts_ms}_{len(trades_batch)}_{os.getpid()}.parquet",
        )
        # Ensure uniqueness if collision
        suffix = 0
        base_final = final
        while os.path.exists(final):
            suffix += 1
            final = base_final.replace(".parquet", f"_{suffix}.parquet")
        tmp = f"{final}.tmp"
        pq.write_table(pa.Table.from_pandas(df), tmp)
        os.rename(tmp, final)


class TradeArchiveReader:
    """Dataset reader over the archive directory. Ignores `.tmp` files (AC17)."""

    def __init__(self, archive_dir: str):
        self.archive_dir = archive_dir

    def all_trades(self) -> pd.DataFrame:
        """Return a DataFrame unioning every committed parquet in the archive."""
        if not os.path.isdir(self.archive_dir):
            return pd.DataFrame()
        # Only accept committed `.parquet` files; skip in-flight `.tmp` writes.
        files = sorted(
            p for p in glob.glob(os.path.join(self.archive_dir, "*.parquet"))
            if not p.endswith(".tmp") and os.path.isfile(p)
        )
        if not files:
            return pd.DataFrame()
        # Prefer pa.dataset for unified reads over multiple files
        try:
            import pyarrow.dataset as ds
            dataset = ds.dataset(files, format="parquet")
            tbl = dataset.to_table()
            return tbl.to_pandas()
        except Exception:
            # Fall back to concatenating per-file reads if dataset fails
            frames = []
            for f in files:
                try:
                    frames.append(pq.read_table(f).to_pandas())
                except Exception:
                    continue
            if not frames:
                return pd.DataFrame()
            return pd.concat(frames, ignore_index=True)
