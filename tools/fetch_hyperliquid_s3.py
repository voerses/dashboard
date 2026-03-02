#!/workspace/venv/bin/python
"""
Fetch historical data from Hyperliquid's S3 archive buckets.

Buckets:
  - hyperliquid-archive    : market data (L2 book, asset contexts)
      asset_ctxs/{YYYYMMDD}.csv.lz4   -- daily OI, mark price, funding, volumes
      market_data/{date}/{hour}/l2Book/{coin}.lz4
  - hl-mainnet-node-data   : trade fills
      node_fills_by_block/             -- current format (fills by block)
      node_fills/                      -- older format fills
      node_trades/                     -- older format trades

Both buckets are requester-pays.  This script tries authenticated access
first (requires AWS credentials with billing enabled).  If that fails it
logs a clear error explaining how to set up credentials.

Primary focus: asset_ctxs (daily snapshots with OI, mark price, funding,
volume per coin) -- the most valuable dataset since the API doesn't
provide historical OI.

Usage examples:
  # Download all asset_ctxs from launch to today
  python fetch_hyperliquid_s3.py

  # Download fills for specific tokens for a date range
  python fetch_hyperliquid_s3.py --data-type fills --tokens BTC ETH SOL \\
      --start-date 2024-01-01 --end-date 2024-06-30

  # Re-download everything, overwriting existing files
  python fetch_hyperliquid_s3.py --data-type all --force
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from typing import Optional

import boto3
import lz4.frame
from botocore import UNSIGNED
from botocore.config import Config
from botocore.exceptions import ClientError, NoCredentialsError

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ARCHIVE_BUCKET = "hyperliquid-archive"
NODE_DATA_BUCKET = "hl-mainnet-node-data"

# Hyperliquid mainnet launched around 2023-03-01; earliest S3 data ~2023-04-15
DEFAULT_START = "2023-03-01"

BASE_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..",
    "data",
    "perp",
    "hyperliquid",
    "s3_raw",
)

ASSET_CTXS_DIR = os.path.join(BASE_DIR, "asset_ctxs")
FILLS_DIR = os.path.join(BASE_DIR, "fills")

MAX_WORKERS = 8

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("fetch_hl_s3")


# ---------------------------------------------------------------------------
# S3 client helpers
# ---------------------------------------------------------------------------


def _make_s3_client_authenticated() -> Optional[boto3.client]:
    """Try to create an authenticated S3 client (for requester-pays)."""
    try:
        session = boto3.Session()
        creds = session.get_credentials()
        if creds is None:
            return None
        frozen = creds.get_frozen_credentials()
        if frozen.access_key is None:
            return None
        client = session.client("s3")
        return client
    except (NoCredentialsError, Exception):
        return None


def _make_s3_client_unsigned() -> boto3.client:
    """Create an unsigned (anonymous) S3 client."""
    return boto3.client("s3", config=Config(signature_version=UNSIGNED))


def _get_object(s3_client, bucket: str, key: str, authenticated: bool) -> bytes:
    """Download an S3 object, using requester-pays if authenticated."""
    kwargs = {"Bucket": bucket, "Key": key}
    if authenticated:
        kwargs["RequestPayer"] = "requester"
    resp = s3_client.get_object(**kwargs)
    return resp["Body"].read()


def _list_objects(
    s3_client, bucket: str, prefix: str, authenticated: bool
) -> list[dict]:
    """List objects under a prefix. Returns list of {Key, Size} dicts."""
    kwargs = {"Bucket": bucket, "Prefix": prefix, "MaxKeys": 1000}
    if authenticated:
        kwargs["RequestPayer"] = "requester"

    all_objects = []
    while True:
        resp = s3_client.list_objects_v2(**kwargs)
        contents = resp.get("Contents", [])
        all_objects.extend(contents)
        if resp.get("IsTruncated"):
            kwargs["ContinuationToken"] = resp["NextContinuationToken"]
        else:
            break
    return all_objects


def _list_common_prefixes(
    s3_client, bucket: str, prefix: str, delimiter: str, authenticated: bool
) -> list[str]:
    """List common prefixes (subdirectories) under a prefix."""
    kwargs = {
        "Bucket": bucket,
        "Prefix": prefix,
        "Delimiter": delimiter,
        "MaxKeys": 1000,
    }
    if authenticated:
        kwargs["RequestPayer"] = "requester"

    prefixes = []
    while True:
        resp = s3_client.list_objects_v2(**kwargs)
        for p in resp.get("CommonPrefixes", []):
            prefixes.append(p["Prefix"])
        if resp.get("IsTruncated"):
            kwargs["ContinuationToken"] = resp["NextContinuationToken"]
        else:
            break
    return prefixes


class S3Downloader:
    """Manages S3 access with automatic fallback from authenticated to unsigned."""

    def __init__(self):
        self.authenticated = False
        self.client = None
        self._setup()

    def _setup(self):
        # Try authenticated first (required for requester-pays)
        client = _make_s3_client_authenticated()
        if client is not None:
            try:
                # Quick smoke test -- try listing a small prefix
                _list_objects(client, ARCHIVE_BUCKET, "asset_ctxs/2024", True)
                self.client = client
                self.authenticated = True
                log.info("Using authenticated S3 access (requester-pays)")
                return
            except ClientError as e:
                code = e.response["Error"]["Code"]
                if code in ("AccessDenied", "403"):
                    log.warning(
                        "Authenticated client exists but access denied "
                        "(requester-pays billing may not be enabled): %s",
                        e,
                    )
                else:
                    log.warning("Authenticated S3 test failed: %s", e)
            except Exception as e:
                log.warning("Authenticated S3 test failed: %s", e)

        # Fall back to unsigned
        unsigned_client = _make_s3_client_unsigned()
        try:
            _list_objects(unsigned_client, ARCHIVE_BUCKET, "asset_ctxs/2024", False)
            self.client = unsigned_client
            self.authenticated = False
            log.info("Using unsigned (anonymous) S3 access")
            return
        except ClientError as e:
            code = e.response["Error"]["Code"]
            if code in ("AccessDenied", "403"):
                log.error(
                    "\n"
                    "============================================================\n"
                    "  ACCESS DENIED -- Hyperliquid S3 buckets are requester-pays\n"
                    "============================================================\n"
                    "\n"
                    "  Anonymous (unsigned) access is blocked because these\n"
                    "  buckets require the caller to pay for data transfer.\n"
                    "\n"
                    "  To fix this, configure AWS credentials:\n"
                    "\n"
                    "    1. Create an AWS account (if you don't have one)\n"
                    "    2. Create an IAM user with s3:GetObject permission\n"
                    "    3. Run:  aws configure\n"
                    "       - Set your Access Key ID and Secret Access Key\n"
                    "       - Set region to us-east-1\n"
                    "    4. Re-run this script\n"
                    "\n"
                    "  Cost: ~$0.09/GB for data transfer out of S3.\n"
                    "  The asset_ctxs dataset is small (~1-5 MB/day compressed).\n"
                    "============================================================\n"
                )
                # Still set client so the script can attempt downloads
                # and report per-file errors rather than crashing outright
                self.client = unsigned_client
                self.authenticated = False
            else:
                raise
        except Exception:
            # Last resort: set unsigned client, errors will surface per-download
            self.client = unsigned_client
            self.authenticated = False

    def get_object(self, bucket: str, key: str) -> bytes:
        return _get_object(self.client, bucket, key, self.authenticated)

    def list_objects(self, bucket: str, prefix: str) -> list[dict]:
        return _list_objects(self.client, bucket, prefix, self.authenticated)

    def list_prefixes(
        self, bucket: str, prefix: str, delimiter: str = "/"
    ) -> list[str]:
        return _list_common_prefixes(
            self.client, bucket, prefix, delimiter, self.authenticated
        )


# ---------------------------------------------------------------------------
# LZ4 decompression
# ---------------------------------------------------------------------------


def decompress_lz4(data: bytes) -> bytes:
    """Decompress LZ4-frame-compressed bytes."""
    return lz4.frame.decompress(data)


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------


def date_range(start: date, end: date) -> list[date]:
    """Return inclusive list of dates from start to end."""
    days = []
    current = start
    while current <= end:
        days.append(current)
        current += timedelta(days=1)
    return days


def date_to_s3_fmt(d: date) -> str:
    """Format date as YYYYMMDD for S3 keys."""
    return d.strftime("%Y%m%d")


# ---------------------------------------------------------------------------
# Download: asset_ctxs
# ---------------------------------------------------------------------------


def _download_asset_ctxs_one(
    downloader: S3Downloader, d: date, force: bool
) -> dict:
    """Download a single day's asset_ctxs file. Returns a result dict."""
    date_str = date_to_s3_fmt(d)
    s3_key = f"asset_ctxs/{date_str}.csv.lz4"
    out_path = os.path.join(ASSET_CTXS_DIR, f"{date_str}.csv")

    result = {
        "date": d.isoformat(),
        "key": s3_key,
        "status": "unknown",
        "size": 0,
        "error": None,
    }

    if os.path.exists(out_path) and not force:
        result["status"] = "skipped"
        result["size"] = os.path.getsize(out_path)
        return result

    try:
        compressed = downloader.get_object(ARCHIVE_BUCKET, s3_key)
        decompressed = decompress_lz4(compressed)
        os.makedirs(ASSET_CTXS_DIR, exist_ok=True)
        with open(out_path, "wb") as f:
            f.write(decompressed)
        result["status"] = "downloaded"
        result["size"] = len(decompressed)
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code in ("NoSuchKey", "404"):
            result["status"] = "missing"
            result["error"] = f"No data for {d.isoformat()} (key not found)"
        elif code in ("AccessDenied", "403"):
            result["status"] = "access_denied"
            result["error"] = (
                "Access denied (requester-pays). Set up AWS credentials."
            )
        else:
            result["status"] = "error"
            result["error"] = str(e)
    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)

    return result


def download_asset_ctxs(
    downloader: S3Downloader,
    dates: list[date],
    force: bool,
) -> list[dict]:
    """Download asset_ctxs for a list of dates, in parallel."""
    results = []
    total = len(dates)

    log.info("Downloading asset_ctxs for %d dates ...", total)

    downloaded = 0
    skipped = 0
    failed = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {
            pool.submit(_download_asset_ctxs_one, downloader, d, force): d
            for d in dates
        }
        for i, future in enumerate(as_completed(futures), 1):
            result = future.result()
            results.append(result)

            if result["status"] == "downloaded":
                downloaded += 1
            elif result["status"] == "skipped":
                skipped += 1
            elif result["status"] == "missing":
                # Missing dates are expected near the start and for recent dates
                pass
            else:
                failed += 1

            done = downloaded + skipped
            if i % 50 == 0 or i == total:
                log.info(
                    "  asset_ctxs progress: %d/%d processed "
                    "(%d downloaded, %d skipped, %d missing, %d failed)",
                    i,
                    total,
                    downloaded,
                    skipped,
                    i - downloaded - skipped - failed,
                    failed,
                )

            # If we get access denied on the very first file, abort early
            if i == 1 and result["status"] == "access_denied":
                log.error(
                    "First download attempt returned AccessDenied. "
                    "Aborting -- please configure AWS credentials."
                )
                # Cancel remaining futures
                for f in futures:
                    f.cancel()
                break

    return results


# ---------------------------------------------------------------------------
# Download: fills
# ---------------------------------------------------------------------------


def _discover_fills_structure(
    downloader: S3Downloader, d: date
) -> tuple[str, str, list[dict]]:
    """
    Discover which fills path format exists for a given date.

    Returns (bucket, prefix_used, objects_list).
    Tries node_fills_by_block first (newer), then node_fills (older).
    """
    date_str = date_to_s3_fmt(d)

    # Try node_fills_by_block/{date}/
    prefix_block = f"node_fills_by_block/{date_str}/"
    try:
        objects = downloader.list_objects(NODE_DATA_BUCKET, prefix_block)
        if objects:
            return NODE_DATA_BUCKET, prefix_block, objects
    except ClientError:
        pass

    # Try node_fills/{date}/
    prefix_old = f"node_fills/{date_str}/"
    try:
        objects = downloader.list_objects(NODE_DATA_BUCKET, prefix_old)
        if objects:
            return NODE_DATA_BUCKET, prefix_old, objects
    except ClientError:
        pass

    # Try node_trades/{date}/ as last resort
    prefix_trades = f"node_trades/{date_str}/"
    try:
        objects = downloader.list_objects(NODE_DATA_BUCKET, prefix_trades)
        if objects:
            return NODE_DATA_BUCKET, prefix_trades, objects
    except ClientError:
        pass

    return NODE_DATA_BUCKET, "", []


def _download_fills_file(
    downloader: S3Downloader, bucket: str, key: str, out_path: str, force: bool
) -> dict:
    """Download and decompress a single fills file."""
    result = {
        "key": key,
        "out_path": out_path,
        "status": "unknown",
        "size": 0,
        "error": None,
    }

    if os.path.exists(out_path) and not force:
        result["status"] = "skipped"
        result["size"] = os.path.getsize(out_path)
        return result

    try:
        compressed = downloader.get_object(bucket, key)
        # Some files may be LZ4 compressed, others may not
        if key.endswith(".lz4"):
            decompressed = decompress_lz4(compressed)
        else:
            decompressed = compressed

        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "wb") as f:
            f.write(decompressed)
        result["status"] = "downloaded"
        result["size"] = len(decompressed)
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code in ("NoSuchKey", "404"):
            result["status"] = "missing"
            result["error"] = f"Key not found: {key}"
        elif code in ("AccessDenied", "403"):
            result["status"] = "access_denied"
            result["error"] = "Access denied (requester-pays)"
        else:
            result["status"] = "error"
            result["error"] = str(e)
    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)

    return result


def _filter_fills_by_tokens(
    objects: list[dict], tokens: list[str] | None
) -> list[dict]:
    """
    Filter fill objects by token name if tokens are specified.

    Fills files may contain the coin name in the key path. If the structure
    is flat (e.g. just block-number files), we download all of them.
    """
    if not tokens:
        return objects

    tokens_upper = {t.upper() for t in tokens}
    filtered = []
    for obj in objects:
        key = obj["Key"]
        basename = os.path.basename(key).upper()
        # Check if any token appears in the filename
        matches = any(tok in basename for tok in tokens_upper)
        if matches:
            filtered.append(obj)

    # If no files matched the token filter, the structure may be
    # block-based (all fills in one file). Return all in that case.
    if not filtered:
        return objects

    return filtered


def _download_fills_one_date(
    downloader: S3Downloader,
    d: date,
    tokens: list[str] | None,
    force: bool,
) -> list[dict]:
    """Download all fills files for a single date."""
    date_str = date_to_s3_fmt(d)
    bucket, prefix, objects = _discover_fills_structure(downloader, d)

    if not objects:
        return [
            {
                "date": d.isoformat(),
                "key": f"node_fills*/{date_str}/",
                "status": "missing",
                "size": 0,
                "error": f"No fills data found for {d.isoformat()}",
            }
        ]

    objects = _filter_fills_by_tokens(objects, tokens)

    results = []
    for obj in objects:
        key = obj["Key"]
        # Derive output filename: strip the prefix and decompress extension
        relative = key[len(prefix) :] if key.startswith(prefix) else os.path.basename(key)
        if relative.endswith(".lz4"):
            relative = relative[:-4]
        out_path = os.path.join(FILLS_DIR, date_str, relative)
        result = _download_fills_file(downloader, bucket, key, out_path, force)
        result["date"] = d.isoformat()
        results.append(result)

    return results


def download_fills(
    downloader: S3Downloader,
    dates: list[date],
    tokens: list[str] | None,
    force: bool,
) -> list[dict]:
    """Download fills for a list of dates."""
    all_results = []
    total = len(dates)

    log.info("Downloading fills for %d dates ...", total)
    if tokens:
        log.info("  Token filter: %s", ", ".join(tokens))

    downloaded = 0
    skipped = 0
    failed = 0
    missing = 0

    # Fills discovery is heavier (list + multiple downloads per date),
    # so we parallelize at the date level with fewer workers
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, 4)) as pool:
        futures = {
            pool.submit(
                _download_fills_one_date, downloader, d, tokens, force
            ): d
            for d in dates
        }

        first_done = False
        for i, future in enumerate(as_completed(futures), 1):
            date_results = future.result()
            all_results.extend(date_results)

            for r in date_results:
                if r["status"] == "downloaded":
                    downloaded += 1
                elif r["status"] == "skipped":
                    skipped += 1
                elif r["status"] == "missing":
                    missing += 1
                else:
                    failed += 1

            # Abort on access denied for the first date
            if not first_done:
                first_done = True
                if any(r["status"] == "access_denied" for r in date_results):
                    log.error(
                        "First fills download returned AccessDenied. "
                        "Aborting -- please configure AWS credentials."
                    )
                    for f in futures:
                        f.cancel()
                    break

            if i % 20 == 0 or i == total:
                log.info(
                    "  fills progress: %d/%d dates processed "
                    "(%d files downloaded, %d skipped, %d missing dates, %d failed)",
                    i,
                    total,
                    downloaded,
                    skipped,
                    missing,
                    failed,
                )

    return all_results


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


def print_summary(label: str, results: list[dict]) -> None:
    """Print a summary of download results."""
    downloaded = [r for r in results if r["status"] == "downloaded"]
    skipped = [r for r in results if r["status"] == "skipped"]
    missing = [r for r in results if r["status"] == "missing"]
    access_denied = [r for r in results if r["status"] == "access_denied"]
    errors = [r for r in results if r["status"] == "error"]

    total_size = sum(r.get("size", 0) for r in downloaded)
    existing_size = sum(r.get("size", 0) for r in skipped)

    print(f"\n{'='*60}")
    print(f"  {label} Summary")
    print(f"{'='*60}")
    print(f"  Downloaded:     {len(downloaded):>6} files  ({_fmt_bytes(total_size)})")
    print(f"  Already cached: {len(skipped):>6} files  ({_fmt_bytes(existing_size)})")
    print(f"  Missing (no data): {len(missing):>3} dates")
    if access_denied:
        print(f"  Access denied:  {len(access_denied):>6} files")
    if errors:
        print(f"  Errors:         {len(errors):>6} files")
        for r in errors[:10]:
            print(f"    - {r.get('date', r.get('key', '?'))}: {r['error']}")
        if len(errors) > 10:
            print(f"    ... and {len(errors) - 10} more")
    print(f"{'='*60}\n")


def _fmt_bytes(n: int) -> str:
    """Human-readable byte size."""
    if n < 1024:
        return f"{n} B"
    elif n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    elif n < 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB"
    else:
        return f"{n / (1024 * 1024 * 1024):.2f} GB"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download historical data from Hyperliquid S3 buckets.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--data-type",
        choices=["asset_ctxs", "fills", "all"],
        default="asset_ctxs",
        help="Which dataset to download (default: asset_ctxs)",
    )
    parser.add_argument(
        "--start-date",
        default=DEFAULT_START,
        help=f"Start date YYYY-MM-DD (default: {DEFAULT_START})",
    )
    parser.add_argument(
        "--end-date",
        default=date.today().isoformat(),
        help="End date YYYY-MM-DD (default: today)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download files that already exist locally",
    )
    parser.add_argument(
        "--tokens",
        nargs="+",
        default=None,
        help="Filter fills to specific tokens (e.g. --tokens BTC ETH SOL)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=MAX_WORKERS,
        help=f"Number of parallel download workers (default: {MAX_WORKERS})",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    start = datetime.strptime(args.start_date, "%Y-%m-%d").date()
    end = datetime.strptime(args.end_date, "%Y-%m-%d").date()

    if start > end:
        log.error("Start date %s is after end date %s", start, end)
        sys.exit(1)

    dates = date_range(start, end)
    log.info(
        "Date range: %s to %s (%d days)", start.isoformat(), end.isoformat(), len(dates)
    )

    global MAX_WORKERS
    MAX_WORKERS = args.workers

    # Set up S3 access
    log.info("Initializing S3 access ...")
    downloader = S3Downloader()

    t0 = time.time()
    all_results = []

    # Download asset_ctxs
    if args.data_type in ("asset_ctxs", "all"):
        results = download_asset_ctxs(downloader, dates, args.force)
        all_results.extend(results)
        print_summary("asset_ctxs", results)

    # Download fills
    if args.data_type in ("fills", "all"):
        results = download_fills(downloader, dates, args.tokens, args.force)
        all_results.extend(results)
        print_summary("fills", results)

    elapsed = time.time() - t0
    total_downloaded = sum(
        1 for r in all_results if r["status"] == "downloaded"
    )
    total_size = sum(
        r.get("size", 0) for r in all_results if r["status"] == "downloaded"
    )
    total_failed = sum(
        1
        for r in all_results
        if r["status"] in ("error", "access_denied")
    )

    print(f"Completed in {elapsed:.1f}s")
    print(
        f"Total: {total_downloaded} files downloaded ({_fmt_bytes(total_size)}), "
        f"{total_failed} failures"
    )
    print(f"Data saved to: {os.path.abspath(BASE_DIR)}")

    if total_failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
