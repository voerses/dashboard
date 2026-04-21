# v5 Dashboard — /v5 Routing, state_v5.json Schema, Cutover (M10)

Reference doc for the v5 paper-trading dashboard (AC #12).

## URL routing

| Path | Serves | Backed by |
|---|---|---|
| `/` | v4 legacy view (unchanged during parallel ops) | `/srv/data/state.json` |
| `/v5` | v5 view with M10 engine wiring | `/srv/data/state_v5.json` |

Path-aware routing implemented at `/srv/dashboard/current/index.html`
(JS-side detection) — from M9 C-8. Dashboard headers render "V4 Live"
on `/` and "V5 Live" on `/v5` distinctly so the operator can visually
compare the two side-by-side.

## state_v5.json schema (v3)

```jsonc
{
  "schema_version": 3,
  "tick_counter": 142857,
  "last_update_ts_ns": 1767225600000000000,
  "portfolio_equity": 100345.67,

  // Active positions (FIX-aligned identity fields)
  "active_positions": [
    {
      "position_id": "BTC:s524m:42:primary",
      "parent_position_id": "BTC:s524m:42:primary",
      "exec_seq": 0,
      "exec_type": "open",         // reserved — set on entry
      "is_terminal": false,
      "triggered_by": "",
      "token": "BTC",
      "strategy_id": "s524m",
      "leg_ref_id": "leg_primary",
      "entry_bar": 42,
      "entry_price": 68000.0,
      "direction": 1,
      "quantity": 0.5,
      "margin_usd": 6800.0,
      "leverage": 5.0,
      "is_perp": true,
      "cumulative_funding": -12.34
    }
  ],

  // Open orders (multi-leg aware)
  "open_orders": [
    {
      "order_id": "ORD-0042",
      "strategy_id": "s524m",
      "legs": [
        {
          "leg_ref_id": "leg_primary",
          "settlement_type": "perp",    // FIX LegSettlType(587)
          "status": "ARMED",
          "target_qty": 0.5,
          "cum_qty": 0.0,
          "trigger_price": 68500.0,
          "limit_price": null,
          "order_type": "stop"
        }
      ]
    }
  ],

  // FIX-aligned counters
  "partial_fills": 23,
  "increase_fills": 0,                 // RESERVED — M11+
  "contingent_fills": 11,
  "entry_scale_downs": 4,

  // M8 sizing binding (join via position_id)
  "binding_constraints": {
    "BTC:s524m:42:primary": {
      "clamp": "adv_cap",
      "bound_size_usd": 6800.0
    }
  },

  // Integrity check (M10 AC #24)
  "checksum": "sha256:a1b2c3d4..."
}
```

## Cutover procedure

**Pre-conditions** (all must be GREEN before flipping default):
- AC #9 replay-parity tests PASS
- AC #10 AC-S10 per-year parity PASS within per-metric tolerances
- AC #8 replay-stability test PASS (24h fixture)
- AC #12 short parallel-ops smoke PASS (30-60 min with WS rate-limit check)

**Flip** (single atomic commit):
1. Modify `tools/start_all_services.sh` to launch `v5.run_paper_multi`
   instead of `v4.run_paper_multi`.
2. Or publish a new canonical launcher at `tools/start_v5_services.sh`
   and archive the v4 variant to `tools/start_v4_legacy.sh`.
3. Update `/srv/data/state.json` symlink to point at
   `/srv/data/state_v5.json`, OR update dashboard default URL to `/v5`.
4. Set `V5_PAPER_ENABLED=1` as the default in the v5 runner script.

**Kill-switch** (if post-cutover issues arise): roll back per
`ROLLBACK.md`. Rollback is instant until `--commit-migration` runs.

## Parallel-ops smoke (AC #12)

**Not a multi-day soak.** Short wiring-verification smoke:

- Both v4 + v5 runners started simultaneously (separate PID files,
  separate state dirs, separate log files).
- 30-60 min operator window: watch `/` and `/v5` in two browser tabs.
- WS rate-limit collision guard: `tools/ws_ratelimit_parallel_smoke.sh`
  monitors `/srv/data/ws_errors.jsonl` for 429 count.
- Operator tears down when satisfied; multi-day equity drift is NOT
  the smoke's responsibility (AC #10 handles parity via replay).

Per quant-expert audit 2026-04-20: parallel-run is a dashboard-wiring
smoke, not a parity gate. Parity lives in AC #9 (replay) + AC #10
(backtest-to-backtest) + AC #8 (replay stability).
