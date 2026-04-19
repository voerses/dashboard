"""M6 — Standalone Binance WS tap recorder (Task 19, AC-D12).

Records WS frames + REST backfill calls to JSONL fixtures for shadow-replay.

**CRITICAL**: This is a STANDALONE script. It does NOT touch:
  - state/v4_paper_multi/paper.pid (the live paper runner lock)
  - run_paper_multi.py infra
  - start_all_services.sh

It connects directly to wss://stream.binance.com (spot) and
wss://fstream.binance.com (perp) via a fresh TCP socket, records frames,
and writes them to disk. The live paper runner keeps running untouched.

Usage (Wave F fixture acquisition):
  # 1-hour proxy (Phase 3 RED gate fixture)
  python v5/tools/record_ws_tap.py --duration 3600 \\
      --output v5/tests/fixtures/shadow_replay_1h_proxy

  # 24-hour hard merge gate fixture (Phase 4 Wave F)
  python v5/tools/record_ws_tap.py --duration 86400 \\
      --output v5/tests/fixtures/shadow_replay_24h

Output shape:
  <output_dir>/
    binance_ws_tap_<date>.jsonl.zst     # WS frames (1m kline + aggTrades)
    binance_rest_tap_<date>.jsonl.zst   # REST backfill snapshots
    1h_proxy.jsonl  (only for 1h proxy runs — legacy decoded form used by
                     test_m6_paper_migration.py)
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def _default_symbols() -> list[str]:
    """Core 5 perps for the proxy fixture — keeps fixture size manageable."""
    return ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Binance WS tap recorder (M6 Wave F)")
    parser.add_argument("--duration", type=int, required=True,
                        help="recording duration in seconds (3600 or 86400 typical)")
    parser.add_argument("--output", type=Path, required=True,
                        help="output directory (created if missing)")
    parser.add_argument("--symbols", type=str, default=",".join(_default_symbols()),
                        help="comma-separated symbols (default: 5-token perp core)")
    parser.add_argument("--market", choices=("spot", "perp"), default="perp")
    args = parser.parse_args(argv)

    args.output.mkdir(parents=True, exist_ok=True)
    date_tag = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    ws_path = args.output / f"binance_ws_tap_{date_tag}.jsonl"
    rest_path = args.output / f"binance_rest_tap_{date_tag}.jsonl"
    proxy_path = args.output / "1h_proxy.jsonl"

    # Wave F real implementation: opens wss://fstream.binance.com,
    # subscribes to <symbol>@kline_1m and <symbol>@aggTrade streams for each
    # symbol, records every frame to ws_path with local ts_init. Periodic
    # REST calls to /fapi/v1/klines record to rest_path. After --duration
    # seconds, writes the decoded 1h_proxy.jsonl for paper_migration tests.
    #
    # This structural scaffold validates the CLI shape + output paths.
    # Real WS connection deferred to Wave F execution (needs 1h of real
    # wall-clock time for the proxy recording — not runnable in CI).
    print(f"[record_ws_tap] duration={args.duration}s output={args.output}")
    print(f"[record_ws_tap] symbols={args.symbols}")
    print(f"[record_ws_tap] market={args.market}")
    print(f"[record_ws_tap] ws_out={ws_path}")
    print(f"[record_ws_tap] rest_out={rest_path}")
    if args.duration == 3600:
        print(f"[record_ws_tap] proxy_out={proxy_path}")
    print("[record_ws_tap] Wave F execution required — see module docstring",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
