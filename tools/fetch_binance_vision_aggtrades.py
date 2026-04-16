#!/workspace/venv/bin/python
"""
Fetch daily aggTrades from Binance Vision perp futures, resample to 1s bars.

Writes data/perp/binance/{symbol}/trades_1s/{date}.parquet. Raw ticks are
discarded; only OHLC / taker-buy / taker-sell / VWAP / num_trades are kept.
Taker side: is_buyer_maker=False -> aggressor is buyer -> buy_volume.

Usage:
    python tools/fetch_binance_vision_aggtrades.py \
        --symbols BTCUSDT,ETHUSDT,SOLUSDT --start 2025-04-07 --end 2026-04-07
"""

import argparse
import io
import os
import sys
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_TOOLS_DIR = Path(__file__).resolve().parent
_PROJECT_DIR = _TOOLS_DIR.parent
BASE_OUT = _PROJECT_DIR / "data" / "perp" / "binance"

URL_TMPL = (
    "https://data.binance.vision/data/futures/um/daily/aggTrades/"
    "{pair}/{pair}-aggTrades-{date}.zip"
)

REQUEST_TIMEOUT = 300
MAX_RETRIES = 4
BASE_BACKOFF_S = 1.5

AGG_COLS = [
    "agg_trade_id", "price", "quantity", "first_trade_id", "last_trade_id",
    "transact_time", "is_buyer_maker",
]

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
    return BASE_OUT / symbol / "trades_1s" / f"{date_str}.parquet"


def download_zip(url: str):
    session = get_session()
    for attempt in range(MAX_RETRIES):
        try:
            resp = session.get(url, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 200:
                return 200, resp.content
            if resp.status_code == 404:
                return 404, None
        except (requests.ConnectionError, requests.Timeout):
            pass
        time.sleep(BASE_BACKOFF_S * (2 ** attempt))
    return -1, None


_DTYPES = {"price": "float64", "quantity": "float64",
           "transact_time": "int64", "is_buyer_maker": "object"}
_USECOLS = ["price", "quantity", "transact_time", "is_buyer_maker"]


def _read_aggtrades_csv(zip_bytes: bytes) -> pd.DataFrame:
    """Read aggTrades CSV from zip. Handles headerless and header variants."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = [n for n in zf.namelist() if n.endswith(".csv")]
        if not names:
            raise ValueError("no CSV inside zip")
        with zf.open(names[0]) as f:
            first = f.readline().decode("utf-8", "replace").split(",")[0].strip()
        with zf.open(names[0]) as f:
            if first.lstrip("-").isdigit():
                return pd.read_csv(f, header=None, names=AGG_COLS,
                                   dtype=_DTYPES, usecols=_USECOLS)
            return pd.read_csv(f, dtype=_DTYPES, usecols=_USECOLS)


_F32_COLS = ("open", "high", "low", "close", "vwap",
             "buy_volume", "sell_volume", "buy_notional", "sell_notional")


def resample_to_1s(df: pd.DataFrame) -> pd.DataFrame:
    """Vectorized resample of raw aggTrades to 1-second bars."""
    if df.empty:
        raise ValueError("empty aggTrades frame")

    sample = df["transact_time"].iloc[0]
    unit = "us" if sample > 1e15 else ("ms" if sample > 1e12 else "s")
    ts = pd.to_datetime(df["transact_time"], unit=unit, utc=True).dt.tz_localize(None)

    price = df["price"].to_numpy()
    qty = df["quantity"].to_numpy()
    notional = price * qty

    # Normalize is_buyer_maker to bool (bool/str/0-1 all supported)
    ibm = df["is_buyer_maker"]
    if ibm.dtype == bool:
        ibm_arr = ibm.to_numpy()
    else:
        ibm_arr = ibm.astype(str).str.strip().str.lower().isin(
            ["true", "1", "t", "yes"]).to_numpy()

    # Taker buy = aggressor is buyer = is_buyer_maker False
    taker_buy = ~ibm_arr
    work = pd.DataFrame({
        "price": price, "qty": qty, "notional": notional,
        "buy_volume": np.where(taker_buy, qty, 0.0),
        "sell_volume": np.where(taker_buy, 0.0, qty),
        "buy_notional": np.where(taker_buy, notional, 0.0),
        "sell_notional": np.where(taker_buy, 0.0, notional),
    }, index=ts)
    work.index.name = "ts"

    g = work.resample("1s")
    bars = pd.DataFrame({
        "open": g["price"].first(),
        "high": g["price"].max(),
        "low": g["price"].min(),
        "close": g["price"].last(),
        "buy_volume": g["buy_volume"].sum(min_count=1),
        "sell_volume": g["sell_volume"].sum(min_count=1),
        "buy_notional": g["buy_notional"].sum(min_count=1),
        "sell_notional": g["sell_notional"].sum(min_count=1),
        "num_trades": g["price"].count(),
        "_sum_notional": g["notional"].sum(min_count=1),
        "_sum_qty": g["qty"].sum(min_count=1),
    })
    bars = bars[bars["num_trades"] > 0].copy()
    if bars.empty:
        raise ValueError("no bars after resample")

    with np.errstate(divide="ignore", invalid="ignore"):
        bars["vwap"] = bars["_sum_notional"].to_numpy() / bars["_sum_qty"].to_numpy()
    bars = bars.drop(columns=["_sum_notional", "_sum_qty"])
    bars["num_trades"] = bars["num_trades"].astype("int32")

    # Downcast to float32 (prices: ~1e-7 relative error, well below one tick)
    for col in _F32_COLS:
        bars[col] = bars[col].astype("float32")

    bars = bars.reset_index()
    bars["ts"] = bars["ts"].astype("datetime64[s]")
    return bars


def fetch_one(symbol: str, date_str: str, force: bool):
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
        raw = _read_aggtrades_csv(content)
        bars = resample_to_1s(raw)
        del raw  # release memory before writing
    except (zipfile.BadZipFile, ValueError, Exception) as e:
        return {"symbol": symbol, "date": date_str, "status": "parse_err",
                "rows": 0, "msg": str(e)[:100]}

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        bars.to_parquet(path, engine="pyarrow", compression="zstd", index=False)
    except Exception as e:
        return {"symbol": symbol, "date": date_str, "status": "write_err",
                "rows": 0, "msg": str(e)[:100]}

    return {"symbol": symbol, "date": date_str, "status": "ok", "rows": len(bars)}


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
        description="Fetch Binance Vision perp aggTrades, resampled to 1s bars."
    )
    p.add_argument("--symbols", type=str, default="BTCUSDT,ETHUSDT,SOLUSDT")
    p.add_argument("--start", type=str, required=True)
    p.add_argument("--end", type=str, required=True)
    p.add_argument("--force", action="store_true")
    p.add_argument("--workers", type=int, default=6)
    return p.parse_args()


def main():
    args = parse_args()
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if not symbols:
        raise ValueError("no symbols provided")
    dates = daterange(args.start, args.end)
    tasks = [(s, d) for s in symbols for d in dates]

    print("=" * 70, flush=True)
    print("  Binance Vision — aggTrades 1s bars backfill", flush=True)
    print("=" * 70, flush=True)
    print(f"  Symbols: {symbols}", flush=True)
    print(f"  Date range: {dates[0]} .. {dates[-1]} ({len(dates)} days)", flush=True)
    print(f"  Workers: {args.workers}   Force: {args.force}", flush=True)
    print(f"  Output: {BASE_OUT}/<SYMBOL>/trades_1s/<date>.parquet\n", flush=True)

    t0 = time.time()
    counts = {"ok": 0, "skip": 0, "404": 0, "err": 0}
    errs = []
    total = len(tasks)
    done = 0
    total_rows = 0

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(fetch_one, s, d, args.force): (s, d) for s, d in tasks}
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
                total_rows += res.get("rows", 0)
            elif status == "skip":
                counts["skip"] += 1
            elif status == "404":
                counts["404"] += 1
                errs.append((sym, date, "404"))
            else:
                counts["err"] += 1
                errs.append((sym, date, status))

            if done % 10 == 0 or done == total:
                el = time.time() - t0
                rate = done / max(el, 0.001)
                eta = (total - done) / max(rate, 0.001)
                print(f"  [{done:4d}/{total}] {100*done/total:5.1f}%  "
                      f"ok={counts['ok']} skip={counts['skip']} "
                      f"404={counts['404']} err={counts['err']}  "
                      f"{rate:4.2f}/s  ETA {eta:5.0f}s", flush=True)

    elapsed = time.time() - t0
    print(f"\n" + "=" * 70, flush=True)
    print(f"  Done in {elapsed:.0f}s   ok={counts['ok']} skip={counts['skip']} "
          f"404={counts['404']} err={counts['err']}   "
          f"total_bars={total_rows:,}", flush=True)
    if errs:
        print(f"  First 10 errors/404s:", flush=True)
        for sym, date, st in errs[:10]:
            print(f"    {sym} {date} -> {st}", flush=True)
    print("=" * 70, flush=True)
    return 0 if counts["err"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
