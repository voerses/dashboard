# shadow_replay_5min_live — REAL Binance WS recording (5 minutes)

Recorded 2026-04-19 UTC via `v5/tools/record_ws_tap.py --mode ws --duration 300`.

- **binance_ws_tap_20260419_164654.jsonl** — 8,494 WS frames (raw payloads
  with `ts_init_ns` local ingest time + `raw` Binance frame string). Streams:
  BTCUSDT/ETHUSDT/SOLUSDT `@kline_1m` + `@aggTrade` on the perp venue.
- **binance_rest_tap_20260419_164654.jsonl** — 18 REST klines (3 symbols × 6
  minutes) covering the same window, parsed into the M6 `{stream, ts_event,
  ts_init, open, high, low, close, volume}` shape.

This is a REAL live recording, not synthetic. Good for structural validation
of the shadow-replay harness. Full AC-D12 hard-merge-gate for production
`use_data_engine=True` flip still requires a genuine 24h recording per
brief §"Shadow Replay Ops Runbook" — extend the duration to 86400 and rerun.

## Regenerating

    python v5/tools/record_ws_tap.py --mode ws --duration 300 \
        --output v5/tests/fixtures/shadow_replay_5min_live \
        --symbols BTCUSDT,ETHUSDT,SOLUSDT

Takes 5 minutes of wall-clock. WS frame rate varies (~1500 frames/min on a
3-symbol subscription, dominated by aggTrade activity).
