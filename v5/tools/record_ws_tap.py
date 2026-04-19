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
    """WS-mode: open live Binance WS connection, record for duration_s seconds.

    Uses websocket-client (same library v4/price_monitor.py uses). Subscribes
    to <symbol>@kline_1m + <symbol>@aggTrade streams combined, records every
    frame with local ts_init nanoseconds.
    """
    try:
        import websocket
    except ImportError:
        print(
            "[record_ws_tap] websocket-client not available — falling back to REST",
            file=sys.stderr,
        )
        return _record_rest(output, duration_s, symbols, market)

    import threading
    import time

    base_url = (
        "wss://fstream.binance.com/stream"
        if market == "perp"
        else "wss://stream.binance.com:9443/stream"
    )
    # Build combined-stream URL: ?streams=sym1@kline_1m/sym1@aggTrade/...
    stream_names = []
    for s in symbols:
        stream_names.append(f"{s.lower()}@kline_1m")
        stream_names.append(f"{s.lower()}@aggTrade")
    url = f"{base_url}?streams={'/'.join(stream_names)}"

    output.mkdir(parents=True, exist_ok=True)
    date_tag = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    ws_path = output / f"binance_ws_tap_{date_tag}.jsonl"

    frame_count = [0]
    fh = ws_path.open("w")
    stop_flag = threading.Event()

    def on_message(ws, message):
        # Record as-received with local ingest time
        ts_init_ns = time.time_ns()
        record = {"ts_init_ns": ts_init_ns, "raw": message}
        fh.write(json.dumps(record) + "\n")
        frame_count[0] += 1

    def on_error(ws, error):
        print(f"[record_ws_tap] WS error: {error}", file=sys.stderr)

    def on_close(ws, code, msg):
        print(f"[record_ws_tap] WS closed: code={code} msg={msg}")

    def on_open(ws):
        print(f"[record_ws_tap] WS connected to {len(stream_names)} streams")

    ws = websocket.WebSocketApp(
        url,
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close,
    )

    # Run WS in a thread so we can stop it after duration_s
    thread = threading.Thread(
        target=ws.run_forever,
        kwargs={"ping_interval": 20, "ping_timeout": 10},
        daemon=True,
    )
    thread.start()

    try:
        start = time.time()
        while time.time() - start < duration_s:
            time.sleep(1)
            if frame_count[0] > 0 and int(time.time() - start) % 30 == 0:
                print(f"[record_ws_tap] {frame_count[0]} frames at t={int(time.time()-start)}s")
    except KeyboardInterrupt:
        print("[record_ws_tap] interrupted — closing WS")
    finally:
        stop_flag.set()
        ws.close()
        thread.join(timeout=5)
        fh.close()

    # Also fetch REST snapshot for the same window
    print("[record_ws_tap] fetching REST snapshot for paired backfill...")
    rest_path = output / f"binance_rest_tap_{date_tag}.jsonl"
    minutes = max(1, duration_s // 60 + 1)
    all_rest: list[dict] = []
    for sym in symbols:
        try:
            records = _fetch_klines_rest(sym, minutes, market)
            all_rest.extend(records)
        except Exception as e:
            print(f"[record_ws_tap] WARN REST {sym}: {e}", file=sys.stderr)
    all_rest.sort(key=lambda r: (r["ts_event"], r["stream"]))
    with rest_path.open("w") as f:
        for r in all_rest:
            f.write(json.dumps(r, sort_keys=True) + "\n")

    print(f"[record_ws_tap] WS wrote {frame_count[0]} frames → {ws_path}")
    print(f"[record_ws_tap] REST wrote {len(all_rest)} klines → {rest_path}")
    return 0 if frame_count[0] > 0 else 1


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
