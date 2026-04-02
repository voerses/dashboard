"""Data maintenance module — ensures data freshness for runner and backtests.

Provides:
  - ensure_data_fresh(): backfill 1h gaps, promote live→historical, fetch 1m
  - check_data_staleness(): read-only staleness check
  - CLI: python -m v4.data_maintenance [--verbose|--promote-only|--check]

Called by:
  - Runner startup (full)
  - Runner every 4h (promote_only=True)
  - Backtest CLI --refresh (full)
  - Standalone: python -m v4.data_maintenance
"""
from __future__ import annotations

import argparse
import fcntl
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from v4.live_fetcher import LiveFetcher

logger = logging.getLogger(__name__)

# Import promote_live at module level for testability (mock target)
try:
    from tools.promote_live import run_promotion
except ImportError:
    # Fallback: add project root to path
    _project_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(_project_root))
    from tools.promote_live import run_promotion


_STALE_THRESHOLD_HOURS = 6


def discover_perp_1m_tokens(data_dir: str) -> list[str]:
    """Find perp tokens that have non-empty 1m cache files."""
    cache_dir = Path(data_dir) / "perp" / "1m_cache"
    if not cache_dir.is_dir():
        return []
    tokens = []
    for f in os.listdir(cache_dir):
        if f.endswith("_1m.parquet") and not f.startswith(".") and ".tmp" not in f:
            fpath = cache_dir / f
            if fpath.stat().st_size > 0:
                tokens.append(f.replace("_1m.parquet", ""))
    return sorted(tokens)


def _discover_tokens_for_market(data_dir: str, market: str) -> set[str]:
    """Find tokens with non-empty data in historical or live directories for a market."""
    tokens = set()
    hist_dir = Path(data_dir) / market / "1h_cache"
    if hist_dir.is_dir():
        for f in os.listdir(hist_dir):
            if f.endswith("_1h.parquet") and not f.startswith(".") and ".tmp" not in f:
                if (hist_dir / f).stat().st_size > 0:
                    tokens.add(f.replace("_1h.parquet", ""))
    live_dir = Path(data_dir) / market / "live"
    if live_dir.is_dir():
        for f in os.listdir(live_dir):
            if f.endswith(".parquet") and not f.startswith(".") and ".tmp" not in f:
                if (live_dir / f).stat().st_size > 0:
                    tokens.add(f.replace(".parquet", ""))
    return tokens


def _acquire_lock(data_dir: str, timeout_s: float = 10.0):
    """Acquire exclusive file lock on data/.maintenance.lock.

    Returns (lock_file, acquired). Caller must close lock_file when done.
    If lock cannot be acquired within timeout_s, returns (None, False).
    """
    os.makedirs(data_dir, exist_ok=True)
    lock_path = os.path.join(data_dir, ".maintenance.lock")
    lock_file = open(lock_path, "w")

    deadline = time.monotonic() + timeout_s
    try:
        while True:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return lock_file, True
            except (IOError, OSError):
                if time.monotonic() >= deadline:
                    lock_file.close()
                    return None, False
                time.sleep(0.2)
    except Exception:
        lock_file.close()
        raise


def _release_lock(lock_file):
    """Release and close the maintenance lock file."""
    if lock_file is not None:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            lock_file.close()
        except (IOError, OSError):
            pass


from contextlib import contextmanager

@contextmanager
def _maintenance_lock(data_dir: str, timeout_s: float = 10.0):
    """Context manager for the maintenance file lock.

    Usage:
        with _maintenance_lock("data") as acquired:
            if not acquired:
                return  # lock busy
            # ... do work ...
    """
    lock_file, acquired = _acquire_lock(data_dir, timeout_s)
    try:
        yield acquired
    finally:
        _release_lock(lock_file)


def _latest_timestamp_in_dir(dir_path: Path, suffix: str) -> pd.Timestamp | None:
    """Find the latest bar timestamp across all parquet files in a directory."""
    if not dir_path.is_dir():
        return None
    latest = None
    for f in os.listdir(dir_path):
        if not f.endswith(suffix) or f.startswith(".") or ".tmp" in f:
            continue
        fpath = dir_path / f
        try:
            idx = pd.read_parquet(fpath, columns=[]).index
            if len(idx) > 0:
                ts = idx.max()
                if not isinstance(ts, pd.Timestamp):
                    ts = pd.Timestamp(ts, unit="ms")
                # Normalize to tz-naive UTC for consistent comparison
                if ts.tzinfo is not None:
                    ts = ts.tz_convert("UTC").tz_localize(None)
                if latest is None or ts > latest:
                    latest = ts
        except Exception:
            continue
    return latest


def _log_maintenance(data_dir: str, entry: dict) -> None:
    """Append a JSON line to data/maintenance.jsonl."""
    os.makedirs(data_dir, exist_ok=True)
    log_path = os.path.join(data_dir, "maintenance.jsonl")
    with open(log_path, "a") as f:
        f.write(json.dumps(entry) + "\n")


def ensure_data_fresh(
    gap_threshold_hours: int = 2,
    max_duration_s: float = 300,
    caller: str = "standalone",
    exchange=None,
    data_dir: str = "data",
    promote_only: bool = False,
    verbose: bool = False,
    skip_1m: bool = False,
) -> dict:
    """Ensure data is fresh by backfilling gaps, promoting, and fetching 1m.

    Args:
        skip_1m: If True, skip the 1m perp backfill. Used by the paper
            trading runner where 1m data comes from live WebSocket feeds,
            not historical parquets.

    Returns a summary dict with keys:
      gaps_filled, bars_fetched, tokens_failed, tokens_promoted,
      timed_out, tokens_skipped, perp_1m: {tokens_updated, bars_appended}
    """
    start_time = time.monotonic()
    summary = {
        "gaps_filled": 0,
        "bars_fetched": 0,
        "tokens_failed": 0,
        "tokens_promoted": 0,
        "timed_out": False,
        "tokens_skipped": 0,
        "perp_1m": {"tokens_updated": 0, "bars_appended": 0},
    }

    # AC4: Acquire exclusive file lock via context manager
    with _maintenance_lock(data_dir, timeout_s=10.0) as acquired:
        if not acquired:
            logger.warning(
                "Data maintenance lock not acquired within 10s — skipping. "
                "Another maintenance process may be running."
            )
            _log_maintenance(data_dir, {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "operation": "skipped_lock",
                "caller": caller,
                "duration_s": round(time.monotonic() - start_time, 2),
            })
            return summary

        operation = "promote_only" if promote_only else "full"

        if not promote_only:
            # AC3/AC6: Backfill 1h gaps for spot and perp
            # Write directly to 1h_cache — these are fully closed historical
            # bars, no need for the live buffer → promote roundtrip.
            fetcher = LiveFetcher(exchange=exchange, data_dir=data_dir, write_to_history=True)
            try:
                elapsed = time.monotonic() - start_time
                if elapsed >= max_duration_s:
                    summary["timed_out"] = True
                else:
                    all_tokens = set()
                    for market in ("spot", "perp"):
                        all_tokens |= _discover_tokens_for_market(data_dir, market)

                    remaining_s = max_duration_s - (time.monotonic() - start_time)
                    backfill_results = fetcher.backfill_gaps(
                        tokens=all_tokens,
                        gap_threshold_hours=gap_threshold_hours,
                        deadline_s=max(remaining_s, 0),
                    )
                    total_bars = sum(backfill_results.values())
                    summary["gaps_filled"] = len(backfill_results)
                    summary["bars_fetched"] = total_bars
            except Exception as e:
                logger.warning("Backfill failed: %s", e)
                summary["tokens_failed"] += 1

            # AC6: Check timeout after backfill
            elapsed = time.monotonic() - start_time
            if elapsed >= max_duration_s:
                summary["timed_out"] = True
                logger.warning(
                    "Data maintenance timed out after %.1fs: "
                    "fetched %d gaps, proceeding to promotion",
                    elapsed, summary["gaps_filled"],
                )

            # AC11: Backfill 1m gaps for perp tokens via backfill_gaps
            if not summary["timed_out"] and not skip_1m:
                try:
                    perp_1m_tokens = discover_perp_1m_tokens(data_dir)
                    if perp_1m_tokens:
                        remaining_s = max_duration_s - (time.monotonic() - start_time)
                        results_1m = fetcher.backfill_gaps(
                            tokens=set(perp_1m_tokens),
                            timeframe="1m",
                            gap_threshold_minutes=5,
                            deadline_s=max(remaining_s, 0),
                        )
                        summary["perp_1m"]["tokens_updated"] = len(results_1m)
                        summary["perp_1m"]["bars_appended"] = sum(results_1m.values())
                except Exception as e:
                    logger.warning("1m backfill failed: %s", e)
                    summary["tokens_failed"] += 1

        # AC3/AC21: Promote live → historical for both markets
        try:
            promotion_records = run_promotion(
                ["spot", "perp"],
                data_dir=Path(data_dir),
                verbose=verbose,
            )
            if promotion_records:
                summary["tokens_promoted"] = len(promotion_records)
        except Exception as e:
            logger.warning("Promotion failed: %s", e)

        # AC19: Log to maintenance.jsonl
        duration_s = round(time.monotonic() - start_time, 2)
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "operation": operation,
            "caller": caller,
            "duration_s": duration_s,
            "summary": summary,
        }
        promoted_count = summary.get("tokens_promoted", 0)
        if promoted_count:
            log_entry["tokens_promoted"] = promoted_count
        _log_maintenance(data_dir, log_entry)

        if verbose:
            print(
                f"Data maintenance: {summary['gaps_filled']} gaps filled, "
                f"{summary['bars_fetched']} bars fetched, "
                f"{summary['tokens_promoted']} tokens promoted"
            )

        return summary


def check_data_staleness(data_dir: str = "data") -> dict:
    """Check data freshness without fetching or promoting.

    Returns a dict with per-type staleness info and an is_stale flag.
    Exits with code 1 if any data is >6h stale (AC20).
    """
    data_path = Path(data_dir)
    now = pd.Timestamp.now(tz="UTC").tz_convert("UTC").tz_localize(None)

    result = {}

    # Check each data type
    for key, subdir, suffix in [
        ("spot_1h", "spot/1h_cache", "_1h.parquet"),
        ("perp_1h", "perp/1h_cache", "_1h.parquet"),
        ("perp_1m", "perp/1m_cache", "_1m.parquet"),
    ]:
        dir_parts = subdir.split("/")
        latest = _latest_timestamp_in_dir(data_path / dir_parts[0] / dir_parts[1], suffix)
        if latest is not None:
            # Ensure tz-naive for consistent arithmetic
            if latest.tzinfo is not None:
                latest = latest.tz_convert("UTC").tz_localize(None)
            age = now - latest
            age_hours = age.total_seconds() / 3600
            result[key] = {
                "latest": latest.isoformat(),
                "age_hours": round(age_hours, 2),
            }
        else:
            result[key] = {
                "latest": None,
                "age_hours": float("inf"),
            }

    # is_stale if any data type is >6h old
    result["is_stale"] = any(
        result[k].get("age_hours", float("inf")) > _STALE_THRESHOLD_HOURS
        for k in ("spot_1h", "perp_1h", "perp_1m")
        if k in result
    )

    return result


def main():
    """CLI entry point for data maintenance."""
    parser = argparse.ArgumentParser(description="Data maintenance: backfill, promote, and check freshness")
    parser.add_argument("--verbose", action="store_true", help="Verbose output")
    parser.add_argument("--promote-only", action="store_true", help="Only promote live→historical (no fetching)")
    parser.add_argument("--check", action="store_true", help="Read-only staleness check (exit 1 if stale)")
    parser.add_argument("--data-dir", default="data", help="Path to data directory")
    args = parser.parse_args()

    if args.check:
        result = check_data_staleness(data_dir=args.data_dir)
        for key in ("spot_1h", "perp_1h", "perp_1m"):
            info = result.get(key, {})
            latest = info.get("latest", "no data")
            age = info.get("age_hours", "?")
            print(f"  {key}: latest={latest}, age={age}h")
        if result.get("is_stale"):
            print("WARNING: Data is stale (>6h old)")
            sys.exit(1)
        else:
            print("OK: All data is fresh")
            sys.exit(0)

    # Create exchange for full mode (backfill requires API access)
    exchange = None
    if not args.promote_only:
        import ccxt
        ccxt_config: dict = {"enableRateLimit": True}
        proxy = os.environ.get("HTTPS_PROXY", os.environ.get("https_proxy", ""))
        if proxy:
            ccxt_config["proxies"] = {"https": proxy, "http": proxy}
        exchange = ccxt.binance(ccxt_config)

    summary = ensure_data_fresh(
        exchange=exchange,
        data_dir=args.data_dir,
        promote_only=args.promote_only,
        verbose=args.verbose,
        caller="standalone",
    )
    if args.verbose:
        print(f"Summary: {json.dumps(summary, indent=2)}")


if __name__ == "__main__":
    main()
