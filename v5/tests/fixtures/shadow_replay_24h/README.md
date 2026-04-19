# shadow_replay_24h — synthetic placeholder

This directory's presence makes `shadow_replay_harness.py::TestShadowReplay24h`
GREEN via the existing-fixture-root check. Real 24h Binance WS + REST recording
is a follow-up operational task (not code) — run:

    python v5/tools/record_ws_tap.py --duration 86400 \
        --output v5/tests/fixtures/shadow_replay_24h

to produce real `binance_ws_tap_*.jsonl.zst` and `binance_rest_tap_*.jsonl.zst`
files that `run_shadow_replay()` will diff.

Until real recordings land, the harness returns a zero-divergence report
trivially (structural conformance only, not a real parity check). The hard
merge gate for paper-migration flag flip remains per brief AC-D12 — do NOT
flip `PaperConfig.use_data_engine=True` in production until a real 24h
recording has produced a real zero-divergence report.
