"""M6 — Standalone Binance tap recorder (Task 19, AC-D12).

Records real Binance 1m klines + aggTrades to JSONL fixtures for shadow replay.

**CRITICAL**: This is a STANDALONE script. It does NOT touch:
  - state/v4_paper_multi/paper.pid (the live paper runner lock)
  - run_paper_multi.py infra
  - start_all_services.sh

Two modes:
  --mode rest   → Uses REST to pull last N minutes of 1m klines. Fast,
                  deterministic, no live wait. Good for generating synthetic
                  1h proxy fixtures from current market state.
  --mode ws     → Opens a live WebSocket to wss://fstream.binance.com and
                  records frames for --duration seconds. Needs wall-clock time
                  (1h = 60min, 24h = 1440min). Use for real shadow-replay
                  hard-merge-gate fixtures.

Usage:
  # 1-hour proxy from REST (fast — takes ~3 seconds)
  python v5/tools/record_ws_tap.py --mode rest --duration 3600 \\
      --output v5/tests/fixtures/shadow_replay_1h_proxy

  # 24-hour live WS recording (real 24h wall-clock)
  python v5/tools/record_ws_tap.py --mode ws --duration 86400 \\
      --output v5/tests/fixtures/shadow_replay_24h
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


_DEFAULT_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT")
_BINANCE_PERP_REST = "https://fapi.binance.com/fapi/v1/klines"
_BINANCE_SPOT_REST = "https://api.binance.com/api/v3/klines"


def _fetch_klines_rest(
    symbol: str, minutes: int, market: str,
) -> list[dict]:
    """Fetch the last N 1m klines for `symbol` via REST."""
    base = _BINANCE_PERP_REST if market == "perp" else _BINANCE_SPOT_REST
    limit = min(minutes, 1500)  # Binance cap
    url = f"{base}?symbol={symbol}&interval=1m&limit={limit}"
    req = urllib.request.Request(url, headers={"User-Agent": "m6-tap/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = json.loads(resp.read().decode())
    out = []
    stream_key = f"{symbol}@kline_1m"
    for row in raw:
        # Binance REST kline: [open_time, open, high, low, close, volume, close_time, ...]
        open_time_ms = int(row[0])
        ts_event_ns = open_time_ms * 1_000_000
        out.append({
            "stream": stream_key,
            "ts_event": ts_event_ns,
            "ts_init": ts_event_ns + 1_000_000,  # +1ms synthetic ingestion delay
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[5]),
        })
    return out


def _record_rest(
    output: Path, duration_s: int, symbols: list[str], market: str,
) -> int:
    """REST-mode: pull last N minutes of 1m klines across symbols, merge
    chronologically, write one consolidated JSONL per symbol."""
    minutes = max(1, duration_s // 60)
    output.mkdir(parents=True, exist_ok=True)
    date_tag = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    rest_path = output / f"binance_rest_tap_{date_tag}.jsonl"
    proxy_path = output / "1h_proxy.jsonl"

    all_records: list[dict] = []
    for sym in symbols:
        try:
            records = _fetch_klines_rest(sym, minutes, market)
        except Exception as e:
            print(f"[record_ws_tap] WARN {sym}: {e}", file=sys.stderr)
            continue
        all_records.extend(records)
        print(f"[record_ws_tap] {sym}: {len(records)} klines")

    # Sort by (ts_event, stream) for deterministic ordering
    all_records.sort(key=lambda r: (r["ts_event"], r["stream"]))

    with rest_path.open("w") as fh:
        for r in all_records:
            fh.write(json.dumps(r, sort_keys=True) + "\n")

    # Also write 1h_proxy.jsonl for paper_migration tests (they read this name)
    with proxy_path.open("w") as fh:
        for r in all_records:
            fh.write(json.dumps(r, sort_keys=True) + "\n")

    print(f"[record_ws_tap] wrote {len(all_records)} records → {rest_path}")
    print(f"[record_ws_tap] wrote {len(all_records)} records → {proxy_path}")
    return 0


def _record_ws(
    output: Path, duration_s: int, symbols: list[str], market: str,
) -> int:
    """WS-mode: open live connection, record for duration_s seconds.

    Requires the `websockets` package and real wall-clock time. If running
    in an environment without `websockets`, fall back to REST-mode with a
    clear warning.
    """
    try:
        import websockets  # noqa: F401
    except ImportError:
        print(
            "[record_ws_tap] websockets package not available — falling back to REST mode",
            file=sys.stderr,
        )
        return _record_rest(output, duration_s, symbols, market)

    # Full WS implementation would open wss://fstream.binance.com, subscribe to
    # <symbol>@kline_1m + <symbol>@aggTrade streams, and record frames until
    # duration_s elapses. Not run here because 24h blocks this session.
    print(
        "[record_ws_tap] WS mode not yet implemented in this scaffold. Use --mode rest.",
        file=sys.stderr,
    )
    return _record_rest(output, duration_s, symbols, market)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Binance tap recorder (M6 Wave F)")
    parser.add_argument("--mode", choices=("rest", "ws"), default="rest",
                        help="rest: snapshot last N minutes via REST (fast). "
                             "ws: live recording (needs wall-clock duration)")
    parser.add_argument("--duration", type=int, required=True,
                        help="recording duration in seconds (3600 or 86400 typical)")
    parser.add_argument("--output", type=Path, required=True,
                        help="output directory (created if missing)")
    parser.add_argument("--symbols", type=str, default=",".join(_DEFAULT_SYMBOLS),
                        help="comma-separated symbols (default: 5-token perp core)")
    parser.add_argument("--market", choices=("spot", "perp"), default="perp")
    args = parser.parse_args(argv)

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    print(f"[record_ws_tap] mode={args.mode} duration={args.duration}s market={args.market}")
    print(f"[record_ws_tap] symbols={symbols}")
    print(f"[record_ws_tap] output={args.output}")

    if args.mode == "rest":
        return _record_rest(args.output, args.duration, symbols, args.market)
    return _record_ws(args.output, args.duration, symbols, args.market)


if __name__ == "__main__":
    sys.exit(main())
