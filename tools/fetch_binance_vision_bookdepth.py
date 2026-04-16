#!/workspace/venv/bin/python
"""
Fetch daily bookDepth snapshots from Binance Vision for perp futures.

Downloads daily ZIP archives of pre-aggregated depth curves (12 levels per
snapshot at +/-0.2%, 1%, 2%, 3%, 4%, 5%, ~33s snapshot interval) and stores
them as parquet files under data/perp/binance/{symbol}/bookdepth/{date}.parquet.

URL pattern (confirmed):
    https://data.binance.vision/data/futures/um/daily/bookDepth/{PAIR}/
        {PAIR}-bookDepth-{YYYY-MM-DD}.zip

Raw CSV schema: timestamp, percentage, depth, notional
We store AS-IS (no resampling) with a parsed datetime column.

Usage:
    python tools/fetch_binance_vision_bookdepth.py \
        --symbols BTCUSDT,ETHUSDT,SOLUSDT \
        --start 2025-04-07 --end 2026-04-07
    python tools/fetch_binance_vision_bookdepth.py --force       # overwrite existing
    python tools/fetch_binance_vision_bookdepth.py --workers 12
"""

import argparse
import io
import os
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_TOOLS_DIR = Path(__file__).resolve().parent
_PROJECT_DIR = _TOOLS_DIR.parent
BASE_OUT = _PROJECT_DIR / "data" / "perp" / "binance"

URL_TMPL = (
    "https://data.binance.vision/data/futures/um/daily/bookDepth/"
    "{pair}/{pair}-bookDepth-{date}.zip"
)

REQUEST_TIMEOUT = 120
MAX_RETRIES = 4
BASE_BACKOFF_S = 1.5

# Thread-local session so each worker has its own connection pool
import threading
_tls = threading.local()


def get_session():
    sess = getattr(_tls, "session", None)
    if sess is None:
        sess = requests.Session()
        sess.headers.update({"User-Agent": "crypto-backtest/1.0"})
        proxy = (
            os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
            or os.environ.get("https_proxy") or os.environ.get("http_proxy")
        )
        if proxy:
            sess.proxies = {"https": proxy, "http": proxy}
        _tls.session = sess
    return sess


def out_path(symbol: str, date_str: str) -> Path:
    return BASE_OUT / symbol / "bookdepth" / f"{date_str}.parquet"


def download_zip(url: str):
    """Return (status_code, content_bytes or None). Retries transient errors."""
    session = get_session()
    for attempt in range(MAX_RETRIES):
        try:
            resp = session.get(url, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 200:
                return 200, resp.content
            if resp.status_code == 404:
                return 404, None
            # 5xx or other transient
            time.sleep(BASE_BACKOFF_S * (2 ** attempt))
        except (requests.ConnectionError, requests.Timeout) as e:
            if attempt == MAX_RETRIES - 1:
                return -1, None
            time.sleep(BASE_BACKOFF_S * (2 ** attempt))
    return -1, None


def parse_bookdepth_zip(zip_bytes: bytes) -> pd.DataFrame:
    """Parse a bookDepth daily zip into a typed DataFrame. Raises on bad data."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        csv_names = [n for n in zf.namelist() if n.endswith(".csv")]
        if not csv_names:
            raise ValueError("no CSV inside zip")
        with zf.open(csv_names[0]) as f:
            df = pd.read_csv(f)

    required = {"timestamp", "percentage", "depth", "notional"}
    if not required.issubset(df.columns):
        raise ValueError(f"unexpected columns: {list(df.columns)}")

    # timestamp can be either a datetime string ("2025-06-01 00:00:09")
    # or an epoch in ms. Detect and parse accordingly.
    sample = df["timestamp"].iloc[0]
    if isinstance(sample, str):
        ts = pd.to_datetime(df["timestamp"], utc=True).dt.tz_localize(None)
    else:
        val = float(sample)
        unit = "us" if val > 1e15 else ("ms" if val > 1e12 else "s")
        ts = pd.to_datetime(df["timestamp"], unit=unit, utc=True).dt.tz_localize(None)

    out = pd.DataFrame({
        "ts": ts,
        "percentage": pd.to_numeric(df["percentage"], errors="coerce").astype("int16"),
        "depth": pd.to_numeric(df["depth"], errors="coerce").astype("float64"),
        "notional": pd.to_numeric(df["notional"], errors="coerce").astype("float64"),
    })
    out = out.dropna().reset_index(drop=True)
    if out.empty:
        raise ValueError("empty after parsing")
    return out


def fetch_one(symbol: str, date_str: str, force: bool):
    """Download + save one (symbol, date) parquet. Returns a status dict."""
    path = out_path(symbol, date_str)
    if path.exists() and not force:
        return {"symbol": symbol, "date": date_str, "status": "skip", "rows": 0}

    url = URL_TMPL.format(pair=symbol, date=date_str)
    code, content = download_zip(url)
    if code == 404:
        return {"symbol": symbol, "date": date_str, "status": "404", "rows": 0}
    if code != 200 or content is None:
        return {"symbol": symbol, "date": date_str, "status": f"err{code}", "rows": 0}

    try:
        df = parse_bookdepth_zip(content)
    except (zipfile.BadZipFile, ValueError, Exception) as e:
        return {"symbol": symbol, "date": date_str, "status": "parse_err",
                "rows": 0, "msg": str(e)[:100]}

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(path, engine="pyarrow", compression="zstd", index=False)
    except Exception as e:
        return {"symbol": symbol, "date": date_str, "status": "write_err",
                "rows": 0, "msg": str(e)[:100]}

    return {"symbol": symbol, "date": date_str, "status": "ok", "rows": len(df)}


def daterange(start: str, end: str):
    d0 = datetime.strptime(start, "%Y-%m-%d").date()
    d1 = datetime.strptime(end, "%Y-%m-%d").date()
    if d1 < d0:
        raise ValueError(f"end {end} before start {start}")
    cur = d0
    out = []
    while cur <= d1:
        out.append(cur.strftime("%Y-%m-%d"))
        cur += timedelta(days=1)
    return out


def parse_args():
    p = argparse.ArgumentParser(
        description="Fetch Binance Vision perp bookDepth daily archives."
    )
    p.add_argument("--symbols", type=str, default="BTCUSDT,ETHUSDT,SOLUSDT",
                   help="Comma-separated perp symbols (e.g. BTCUSDT,ETHUSDT)")
    p.add_argument("--start", type=str, required=True, help="YYYY-MM-DD inclusive")
    p.add_argument("--end", type=str, required=True, help="YYYY-MM-DD inclusive")
    p.add_argument("--force", action="store_true", help="Re-download + overwrite")
    p.add_argument("--workers", type=int, default=8, help="Concurrent downloads")
    return p.parse_args()


def main():
    args = parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if not symbols:
        raise ValueError("no symbols provided")

    dates = daterange(args.start, args.end)
    tasks = [(s, d) for s in symbols for d in dates]

    print("=" * 70, flush=True)
    print(f"  Binance Vision — bookDepth backfill", flush=True)
    print("=" * 70, flush=True)
    print(f"  Symbols: {symbols}", flush=True)
    print(f"  Date range: {dates[0]} .. {dates[-1]} ({len(dates)} days)", flush=True)
    print(f"  Workers: {args.workers}   Force: {args.force}", flush=True)
    print(f"  Output: {BASE_OUT}/<SYMBOL>/bookdepth/<date>.parquet", flush=True)
    print(flush=True)

    t0 = time.time()
    counts = {"ok": 0, "skip": 0, "404": 0, "err": 0}
    errs = []
    total = len(tasks)
    done = 0

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(fetch_one, sym, date, args.force): (sym, date)
            for sym, date in tasks
        }
        for fut in as_completed(futures):
            sym, date = futures[fut]
            done += 1
            try:
                res = fut.result()
            except Exception as e:
                counts["err"] += 1
                errs.append((sym, date, f"exc:{e}"))
                continue
            status = res["status"]
            if status == "ok":
                counts["ok"] += 1
            elif status == "skip":
                counts["skip"] += 1
            elif status == "404":
                counts["404"] += 1
                errs.append((sym, date, "404"))
            else:
                counts["err"] += 1
                errs.append((sym, date, status))

            if done % 25 == 0 or done == total:
                pct = 100.0 * done / total
                elapsed = time.time() - t0
                rate = done / max(elapsed, 0.001)
                eta = (total - done) / max(rate, 0.001)
                print(
                    f"  [{done:4d}/{total}] {pct:5.1f}%  "
                    f"ok={counts['ok']} skip={counts['skip']} "
                    f"404={counts['404']} err={counts['err']}  "
                    f"{rate:4.1f}/s  ETA {eta:5.0f}s",
                    flush=True,
                )

    elapsed = time.time() - t0
    print(flush=True)
    print("=" * 70, flush=True)
    print(f"  Done in {elapsed:.0f}s   "
          f"ok={counts['ok']} skip={counts['skip']} "
          f"404={counts['404']} err={counts['err']}", flush=True)
    if errs:
        print(f"  First 10 errors/404s:", flush=True)
        for sym, date, st in errs[:10]:
            print(f"    {sym} {date} -> {st}", flush=True)
    print("=" * 70, flush=True)

    return 0 if counts["err"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
