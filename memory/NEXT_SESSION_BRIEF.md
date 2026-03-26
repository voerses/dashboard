# Next Session Brief — Shared WebSocket PriceMonitor

## What to do

Consolidate 4 duplicate WebSocket connections into 1 shared connection with fan-out.

**Full plan:** `/home/claude/.claude/plans/mellow-wondering-dream.md`

## Why

Paper runner creates 1 PriceMonitor per engine (4 sub-hourly pools = 4 WebSocket
connections to Binance). All subscribe to the same tokens on the same venue ("perp").
Wasteful, risks rate limits.

## Target

1 shared PriceMonitor per unique venue → fan-out to N CandleAggregators.

## Files to modify

1. `v4/run_paper_multi.py` — create shared PriceMonitor, fan-out callback, manage lifecycle
2. `v4/paper_engine.py` — accept external PriceMonitor, `_owns_price_monitor` flag

## Key design decisions (already made)

- Fan-out wrapper in runner (not multi-callback in PriceMonitor) — simpler
- One connection per venue (perp/spot), not per engine
- Backward compatible: engine without shared monitor creates its own
- Use `update_subscriptions()` instead of disconnect/reconnect per tick

## Context from today's session

- Fixed 8x funding overcharge, combined mode 0-trade bug, --market required
- 2 adversarial reviews, all clean
- 657 tests passing, commit `9623249` pushed
- Runner PID 26219 is live with 8 pools
