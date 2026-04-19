"""M6 — Generate shadow_replay_1h_proxy + shadow_replay_24h fixtures from real
on-disk Binance data (data/perp/1m_cache/).

We already have 620k+ rows of real 1m BTC/ETH/etc. data locally — no need to
hit the Binance REST endpoint. This script reads the shipped parquet files,
extracts the most recent 60 minutes (1h proxy) and 1440 minutes (24h) across
the 5-token perp core, and writes JSONL fixtures in the shape that
`shadow_replay_harness.py` + `test_m6_paper_migration.py` expect.

Run:
    python v5/tests/fixtures/generate_m6_shadow_replay_fixtures.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Iterable

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent.parent
SYMBOLS = ("BTC", "ETH", "SOL", "XRP", "ADA")


def _read_1m(symbol: str) -> pd.DataFrame:
    path = ROOT / "data" / "perp" / "1m_cache" / f"{symbol}_1m.parquet"
    if not path.exists():
        raise FileNotFoundError(f"missing 1m parquet: {path}")
    df = pd.read_parquet(path)
    # timestamp is in the index (name=None) — reset to a column
    if df.index.name is None:
        df = df.reset_index().rename(columns={"index": "timestamp"})
    return df


def _jsonl_records_for_last_n(symbol: str, n_minutes: int) -> Iterable[dict]:
    df = _read_1m(symbol)
    if "timestamp" in df.columns:
        ts_col = pd.to_datetime(df["timestamp"], utc=True)
    else:
        ts_col = pd.to_datetime(df.index, utc=True)
    df = df.iloc[-n_minutes:].reset_index(drop=True)
    ts_col = ts_col.iloc[-n_minutes:].reset_index(drop=True)
    stream = f"{symbol}USDT@kline_1m"
    for i, row in df.iterrows():
        ts_ns = int(ts_col.iloc[i].value)  # pd.Timestamp.value = ns
        yield {
            "stream": stream,
            "ts_event": ts_ns,
            "ts_init": ts_ns + 1_000_000,
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": float(row["volume"]),
        }


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        for r in records:
            fh.write(json.dumps(r, sort_keys=True) + "\n")


def main() -> int:
    out_1h = ROOT / "v5" / "tests" / "fixtures" / "shadow_replay_1h_proxy"
    out_24h = ROOT / "v5" / "tests" / "fixtures" / "shadow_replay_24h"

    # 1h proxy: 60 minutes × 5 tokens = 300 bars
    records_1h: list[dict] = []
    for sym in SYMBOLS:
        records_1h.extend(_jsonl_records_for_last_n(sym, 60))
    records_1h.sort(key=lambda r: (r["ts_event"], r["stream"]))
    _write_jsonl(out_1h / "1h_proxy.jsonl", records_1h)
    _write_jsonl(out_1h / "binance_rest_tap_synthetic.jsonl", records_1h)
    print(f"wrote {len(records_1h)} records to {out_1h}")

    # 24h: 1440 minutes × 5 tokens = 7200 bars
    records_24h: list[dict] = []
    for sym in SYMBOLS:
        records_24h.extend(_jsonl_records_for_last_n(sym, 1440))
    records_24h.sort(key=lambda r: (r["ts_event"], r["stream"]))
    # Both ws_tap and rest_tap get the same records intentionally — the
    # shadow-replay harness is a structural stub today; real impl will diff
    # WS frames against REST backfill when real paired recordings land.
    # Do NOT assume ws_tap != rest_tap in future code that reads these.
    _write_jsonl(out_24h / "binance_ws_tap_synthetic.jsonl", records_24h)
    _write_jsonl(out_24h / "binance_rest_tap_synthetic.jsonl", records_24h)
    print(f"wrote {len(records_24h)} records to {out_24h}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
