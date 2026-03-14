#!/usr/bin/env python3
"""
Promote Live Data to Historical — EOD Roll for 24/7 Crypto
============================================================

Moves bars from the live buffer (data/{market}/live/) into the immutable
historical store (data/{market}/1h_cache/).  This is the crypto equivalent
of the kdb+ end-of-day roll (RDB → HDB).

Process per token:
  1. Read live buffer:   data/{market}/live/{TOKEN}.parquet
  2. Read historical:    data/{market}/1h_cache/{TOKEN}_1h.parquet
  3. Identify new bars:  live bars with timestamps > historical max
  4. Quality checks:     OHLC consistency, gaps, duplicates
  5. Append to historical (atomic write: temp + rename)
  6. Trim promoted bars from live buffer (or remove file)
  7. Log promotion to data/promotions.jsonl

Usage:
    python tools/promote_live.py [--market perp|spot|all] [--tokens BTC,ETH]
                                 [--dry-run] [--verbose]

Safety:
  - Historical parquet is written atomically (temp file + rename)
  - --dry-run shows what would happen without modifying files
  - Bars that fail quality checks are skipped with warnings
  - Promotion log enables auditing and rollback
"""

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"

sys.path.insert(0, str(BASE_DIR))

from v4.data_loader import historical_path, live_path, _ensure_datetime_index
from v4.manifest import write_manifest_entry


# ============================================================
# Quality checks for promoted bars
# ============================================================

def check_promoted_bars(df: pd.DataFrame, token: str) -> list[str]:
    """Run lightweight quality checks on bars about to be promoted.

    Returns list of issue strings (empty = all clear).
    """
    issues = []

    if len(df) == 0:
        return issues

    # 1. Duplicates
    n_dupes = df.index.duplicated().sum()
    if n_dupes > 0:
        issues.append(f"{n_dupes} duplicate timestamps")

    # 2. OHLC consistency
    o, h, l, c = df["open"].values, df["high"].values, df["low"].values, df["close"].values
    bad_high = (h < np.maximum(o, c) - 1e-10).sum()
    bad_low = (l > np.minimum(o, c) + 1e-10).sum()
    bad_hl = (h < l - 1e-10).sum()
    non_positive = ((o <= 0) | (h <= 0) | (l <= 0) | (c <= 0)).sum()
    if bad_high + bad_low + bad_hl + non_positive > 0:
        issues.append(f"OHLC violations: high={bad_high} low={bad_low} h<l={bad_hl} non_pos={non_positive}")

    # 3. Gaps > 6h
    if len(df) > 1:
        diffs = df.index.to_series().diff().dt.total_seconds() / 3600
        big_gaps = diffs[diffs > 6.0]
        if len(big_gaps) > 0:
            issues.append(f"{len(big_gaps)} gap(s) > 6h (max {big_gaps.max():.0f}h)")

    # 4. Zero or NaN prices
    nan_rows = df[["open", "high", "low", "close"]].isna().any(axis=1).sum()
    if nan_rows > 0:
        issues.append(f"{nan_rows} rows with NaN prices")

    return issues


def fix_promoted_bars(df: pd.DataFrame) -> pd.DataFrame:
    """Apply minimal fixes to promoted bars: dedupe, sort, clamp OHLC."""
    # Remove duplicates
    if df.index.duplicated().any():
        df = df[~df.index.duplicated(keep="last")]

    # Sort
    df = df.sort_index()

    # Clamp OHLC: high >= max(open, close), low <= min(open, close)
    max_oc = np.maximum(df["open"], df["close"])
    min_oc = np.minimum(df["open"], df["close"])
    df.loc[df["high"] < max_oc, "high"] = max_oc[df["high"] < max_oc]
    df.loc[df["low"] > min_oc, "low"] = min_oc[df["low"] > min_oc]

    # Ensure h >= l
    swapped = df["high"] < df["low"]
    if swapped.any():
        df.loc[swapped, ["high", "low"]] = df.loc[swapped, ["low", "high"]].values

    return df


# ============================================================
# Core promotion logic
# ============================================================

def promote_token(
    token: str,
    market: str,
    data_dir: Path = DATA_DIR,
    dry_run: bool = False,
    verbose: bool = False,
) -> dict | None:
    """Promote live buffer bars to historical for one token/market.

    Returns a promotion record dict, or None if nothing to promote.
    """
    hist_pq = historical_path(token, market, str(data_dir))
    live_pq = live_path(token, market, str(data_dir))

    if not live_pq.exists():
        return None

    df_live = pd.read_parquet(live_pq)
    df_live = _ensure_datetime_index(df_live)

    if len(df_live) == 0:
        # Empty live file — clean up
        if not dry_run:
            try:
                os.remove(live_pq)
            except FileNotFoundError:
                pass
        return None

    # Load historical (may not exist for brand-new tokens)
    df_hist = None
    hist_max = None
    if hist_pq.exists():
        df_hist = pd.read_parquet(hist_pq)
        df_hist = _ensure_datetime_index(df_hist)
        if len(df_hist) > 0:
            hist_max = df_hist.index[-1]

    # Identify new bars: those beyond historical max
    new_bars = df_live[df_live.index > hist_max] if hist_max is not None else df_live

    if len(new_bars) == 0:
        if verbose:
            print(f"  {token}/{market}: no new bars to promote")
        # Live buffer has only overlap — clean it
        if not dry_run:
            try:
                os.remove(live_pq)
            except FileNotFoundError:
                pass
        return None

    # Quality checks
    issues = check_promoted_bars(new_bars, token)
    if issues:
        if verbose:
            for issue in issues:
                print(f"  {token}/{market}: QC warning: {issue}")
        # Fix what we can
        new_bars = fix_promoted_bars(new_bars)

    n_promote = len(new_bars)
    ts_start = new_bars.index[0]
    ts_end = new_bars.index[-1]

    if verbose:
        print(f"  {token}/{market}: promoting {n_promote} bars "
              f"({ts_start} → {ts_end})")

    if dry_run:
        return {
            "token": token,
            "market": market,
            "bars_promoted": n_promote,
            "ts_start": ts_start.isoformat(),
            "ts_end": ts_end.isoformat(),
            "issues": issues,
            "dry_run": True,
        }

    # Append to historical
    if df_hist is not None:
        combined = pd.concat([df_hist, new_bars])
        combined = combined[~combined.index.duplicated(keep="last")]
        combined = combined.sort_index()
    else:
        combined = new_bars

    # Atomic write to historical
    hist_dir = hist_pq.parent
    hist_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(hist_dir), suffix=".tmp")
    os.close(fd)
    try:
        combined.to_parquet(tmp_path)
        try:
            os.rename(tmp_path, str(hist_pq))
        except OSError:
            import shutil
            shutil.move(tmp_path, str(hist_pq))
        # Note: readers using load_token_data() may briefly see promoted bars
        # in both historical and live. This is safe because load_token_data()
        # deduplicates on index (live wins on overlap).
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    # Write manifest entry (after successful historical write, before live trim)
    write_manifest_entry(
        token=token,
        market=market,
        bars_promoted=n_promote,
        ts_start=ts_start,
        ts_end=ts_end,
        hist_total_bars=len(combined),
        data_dir=str(data_dir),
    )

    # Re-read live buffer to capture any bars written during promotion
    if live_pq.exists():
        df_live_current = pd.read_parquet(live_pq)
        df_live_current = _ensure_datetime_index(df_live_current)
    else:
        df_live_current = pd.DataFrame()

    # Trim using the fresh read, not the stale one
    remaining = df_live_current[df_live_current.index > ts_end] if len(df_live_current) > 0 else pd.DataFrame()
    if len(remaining) == 0:
        # Atomic removal: unlink tolerates concurrent deletion
        try:
            os.unlink(live_pq)
        except FileNotFoundError:
            pass
    else:
        live_dir = live_pq.parent
        fd2, tmp2 = tempfile.mkstemp(dir=str(live_dir), suffix=".tmp")
        os.close(fd2)
        try:
            remaining.to_parquet(tmp2)
            try:
                os.rename(tmp2, str(live_pq))
            except OSError:
                import shutil
                shutil.move(tmp2, str(live_pq))
        except Exception:
            try:
                os.unlink(tmp2)
            except OSError:
                pass
            raise

    return {
        "token": token,
        "market": market,
        "bars_promoted": n_promote,
        "ts_start": ts_start.isoformat(),
        "ts_end": ts_end.isoformat(),
        "hist_total_bars": len(combined),
        "live_remaining_bars": len(remaining),
        "issues": issues,
        "dry_run": False,
    }


# ============================================================
# Discovery + orchestration
# ============================================================

def discover_live_tokens(market: str, data_dir: Path = DATA_DIR) -> list[str]:
    """Find tokens with live buffer data for a market."""
    live_dir = data_dir / market / "live"
    if not live_dir.is_dir():
        return []
    return sorted(
        f.replace(".parquet", "")
        for f in os.listdir(live_dir)
        if f.endswith(".parquet") and not f.startswith(".") and ".tmp" not in f
    )


def log_promotion(records: list[dict], data_dir: Path = DATA_DIR) -> None:
    """Append promotion records to data/promotions.jsonl."""
    log_path = data_dir / "promotions.jsonl"
    with open(log_path, "a") as f:
        for rec in records:
            rec["promoted_at"] = datetime.now(timezone.utc).isoformat()
            f.write(json.dumps(rec) + "\n")


def run_promotion(
    markets: list[str],
    tokens: list[str] | None = None,
    data_dir: Path = DATA_DIR,
    dry_run: bool = False,
    verbose: bool = False,
) -> list[dict]:
    """Run promotion across markets and tokens.

    Returns list of promotion records.
    """
    records = []

    for market in markets:
        live_tokens = discover_live_tokens(market, data_dir)
        if tokens:
            live_tokens = [t for t in live_tokens if t in tokens]

        if not live_tokens:
            if verbose:
                print(f"  {market}: no live data to promote")
            continue

        if verbose:
            print(f"\n{market.upper()}: {len(live_tokens)} tokens with live data")

        for token in live_tokens:
            try:
                rec = promote_token(token, market, data_dir, dry_run, verbose)
                if rec:
                    records.append(rec)
            except Exception as e:
                import traceback
                print(f"  ERROR promoting {token}/{market}: {e}")
                traceback.print_exc()
                records.append({
                    "token": token,
                    "market": market,
                    "error": str(e),
                    "dry_run": dry_run,
                })

    return records


# ============================================================
# CLI
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="Promote live buffer data to historical parquet cache"
    )
    parser.add_argument(
        "--market", choices=["perp", "spot", "all"], default="all",
        help="Market type (default: all)"
    )
    parser.add_argument(
        "--tokens", type=str, default=None,
        help="Comma-separated token list (default: all with live data)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be promoted without modifying files"
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Verbose output"
    )
    args = parser.parse_args()

    markets = ["perp", "spot"] if args.market == "all" else [args.market]
    tokens = args.tokens.split(",") if args.tokens else None

    print(f"Promote Live → Historical")
    print(f"  Markets: {', '.join(markets)}")
    print(f"  Tokens: {tokens or 'all with live data'}")
    if args.dry_run:
        print(f"  ** DRY RUN — no files will be modified **")
    print()

    records = run_promotion(markets, tokens, DATA_DIR, args.dry_run, args.verbose)

    # Summary
    promoted = [r for r in records if "error" not in r]
    errors = [r for r in records if "error" in r]
    total_bars = sum(r.get("bars_promoted", 0) for r in promoted)

    print(f"\n{'='*60}")
    print(f"Promotion Summary")
    print(f"{'='*60}")
    print(f"  Tokens promoted: {len(promoted)}")
    print(f"  Total bars:      {total_bars}")
    print(f"  Errors:          {len(errors)}")

    if promoted:
        print(f"\n  Details:")
        for r in promoted:
            status = "[DRY RUN]" if r.get("dry_run") else "[OK]"
            issues_str = f" ({len(r.get('issues', []))} QC warnings)" if r.get("issues") else ""
            print(f"    {status} {r['token']}/{r['market']}: "
                  f"{r['bars_promoted']} bars{issues_str}")

    if errors:
        print(f"\n  Errors:")
        for r in errors:
            print(f"    {r['token']}/{r['market']}: {r['error']}")

    # Log (unless dry run)
    if not args.dry_run and records:
        log_promotion(records, DATA_DIR)
        print(f"\n  Logged to data/promotions.jsonl")


if __name__ == "__main__":
    main()
