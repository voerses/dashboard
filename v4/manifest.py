"""V4 Data Manifest — versioned promotion log for reproducible backtests.

Each promotion creates a manifest entry recording:
- Which token/market was promoted
- Time range of promoted bars
- SHA-256 hash of the resulting historical parquet
- Total bar count after promotion
- Timestamp of the promotion

This enables:
- Point-in-time data loading (what data was available at time T?)
- Integrity verification (has historical data been tampered with?)
- Audit trail (when was each bar range added?)

Manifests are stored per-market in data/manifests/{market}.jsonl.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

from .data_loader import historical_path, _ensure_datetime_index

DATA_DIR = os.environ.get("DATA_DIR", "data")


def manifest_path(market: str, data_dir: str = DATA_DIR) -> Path:
    """Path to the manifest file for a market."""
    return Path(data_dir) / "manifests" / f"{market}.jsonl"


def _hash_file(path: Path) -> Optional[str]:
    """Compute SHA-256 hash of a file. Returns None if file is missing."""
    if not path.exists():
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def write_manifest_entry(
    token: str,
    market: str,
    bars_promoted: int,
    ts_start: pd.Timestamp | str,
    ts_end: pd.Timestamp | str,
    hist_total_bars: int,
    data_dir: str = DATA_DIR,
) -> dict:
    """Write a manifest entry after a successful promotion.

    Computes SHA-256 of the historical parquet and appends a JSONL record.
    Returns the manifest entry dict.
    """
    hist_pq = historical_path(token, market, data_dir)
    hist_hash = _hash_file(hist_pq) if hist_pq.exists() else None

    entry = {
        "token": token,
        "market": market,
        "bars_promoted": bars_promoted,
        "ts_start": str(ts_start),
        "ts_end": str(ts_end),
        "hist_total_bars": hist_total_bars,
        "hist_sha256": hist_hash,
        "promoted_at": datetime.now(timezone.utc).isoformat(),
    }

    mf_path = manifest_path(market, data_dir)
    mf_path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(entry) + "\n"
    # Atomic append: write full line then flush.  On POSIX, writes
    # smaller than PIPE_BUF (4096 bytes) to a file opened with O_APPEND
    # are atomic.  Our lines are ~300 bytes, well under the limit.
    with open(mf_path, "a") as f:
        f.write(line)
        f.flush()
        os.fsync(f.fileno())

    return entry


def read_manifest(market: str, data_dir: str = DATA_DIR) -> list[dict]:
    """Read all manifest entries for a market.

    Returns list of dicts sorted by promoted_at (oldest first).
    """
    mf_path = manifest_path(market, data_dir)
    if not mf_path.exists():
        return []

    entries = []
    with open(mf_path) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue  # skip corrupt lines
    return entries


def verify_integrity(
    token: str,
    market: str,
    data_dir: str = DATA_DIR,
    allow_rebuild: bool = False,
) -> tuple[bool, str]:
    """Verify historical parquet matches the latest manifest hash.

    Returns (ok, message).
    """
    entries = read_manifest(market, data_dir)
    token_entries = [e for e in entries if e["token"] == token]

    if not token_entries:
        return True, "no manifest entries (untracked)"

    latest = token_entries[-1]
    expected_hash = latest.get("hist_sha256")
    if expected_hash is None:
        return True, "no hash recorded"

    hist_pq = historical_path(token, market, data_dir)
    if not hist_pq.exists():
        return False, f"historical file missing (expected hash {expected_hash[:12]}...)"

    actual_hash = _hash_file(hist_pq)
    if actual_hash != expected_hash:
        msg = (
            f"hash mismatch: expected {expected_hash[:12]}... "
            f"got {actual_hash[:12]}..."
        )
        if allow_rebuild:
            return True, f"WARNING: {msg} (rebuild expected, not treated as failure)"
        return False, msg

    return True, "OK"


def get_data_boundary_at(
    token: str,
    market: str,
    as_of: datetime | pd.Timestamp,
    data_dir: str = DATA_DIR,
) -> Optional[pd.Timestamp]:
    """Find the latest data timestamp available for a token at a given point in time.

    Scans manifest entries promoted before as_of and returns the ts_end
    of the latest one. This tells you: "at time as_of, historical data
    for this token extended up to this timestamp."

    Returns None if no manifest entries exist before as_of.
    """
    entries = read_manifest(market, data_dir)
    token_entries = [e for e in entries if e["token"] == token]

    if not token_entries:
        return None

    # Convert as_of to a tz-aware UTC datetime for proper comparison
    if isinstance(as_of, pd.Timestamp):
        as_of_dt = as_of.to_pydatetime()
    elif isinstance(as_of, datetime):
        as_of_dt = as_of
    else:
        as_of_dt = datetime.fromisoformat(str(as_of))

    # Ensure as_of is tz-aware UTC
    if as_of_dt.tzinfo is None:
        as_of_dt = as_of_dt.replace(tzinfo=timezone.utc)

    # Find latest entry promoted before as_of using datetime comparison
    best_end = None
    for entry in token_entries:
        promoted_str = entry.get("promoted_at", "")
        if not promoted_str:
            continue
        promoted_dt = datetime.fromisoformat(promoted_str)
        # If promoted_at was stored without timezone, assume UTC
        if promoted_dt.tzinfo is None:
            promoted_dt = promoted_dt.replace(tzinfo=timezone.utc)
        if promoted_dt <= as_of_dt:
            ts_end = pd.Timestamp(entry["ts_end"])
            if best_end is None or ts_end > best_end:
                best_end = ts_end

    return best_end
