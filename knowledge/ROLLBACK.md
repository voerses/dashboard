# v5 → v4 Rollback Procedure (M10)

Emergency rollback from v5 paper trading back to v4. Referenced by
`test_m10_rollback_drill.py` (AC #20) which rehearses this procedure in
a sandbox state dir.

## When to roll back

- v5 paper trader is crashing or emitting corrupted state
- Trade-rate deviation >50% vs v4 baseline sustained > 4 hours
- Dashboard `/v5` view unresponsive for > 5 minutes
- Any manual override directive from operator

## Rollback sequence (≤ 5 min wall-clock)

### Step 1 — Stop v5 runner

```bash
# Send SIGTERM
kill -TERM $(cat /tmp/paper_runner_v5.pid)

# Wait 30s for drain
sleep 30

# Verify PID is gone
if [ -f /tmp/paper_runner_v5.pid ]; then
  kill -9 $(cat /tmp/paper_runner_v5.pid) || true
  rm -f /tmp/paper_runner_v5.pid
fi
```

### Step 2 — Restore v4 state from `.v1.bak`

```bash
# For each pool
for pool in state/v4_paper_*/; do
  bak="${pool}state.json.v1.bak"
  if [ -f "$bak" ]; then
    cp "$bak" "${pool}state.json"
    echo "Restored $pool"
  fi
done
```

### Step 3 — Start v4 runner

```bash
bash tools/start_all_services.sh
```

### Step 4 — Verify equity continuity

```bash
/workspace/venv/bin/python -c "
import json
cur = json.load(open('state/v4_paper_s513/state.json'))
bak = json.load(open('state/v4_paper_s513/state.json.v1.bak'))
equity_cur = sum(p.get('margin_usd', 0.0) for p in cur.get('open_positions', []))
equity_bak = sum(p.get('margin_usd', 0.0) for p in bak.get('open_positions', []))
assert abs(equity_cur - equity_bak) < 0.01, f'drift: {equity_cur} vs {equity_bak}'
print(f'OK equity preserved: \${equity_cur:.2f}')
"
```

Accepted drift: **$0.01** (allowing for JSON round-trip float
precision). Anything larger is a migration bug — investigate before
resuming v4.

### Step 5 — Confirm dashboard

- `/` renders v4 state normally.
- `/v5` returns HTTP 503 (v5 runner is stopped — this is expected).

## After rollback

- File a Linear ticket with `/v5` state reproduction steps.
- Attach `v5/logs/paper_runner_v5.log` and `v5/logs/arbitration.jsonl`
  from the incident window.
- Do NOT re-run the v5 migration until the root cause is identified.

## Drill cadence

Per AC #20 the rollback is rehearsed via
`test_m10_rollback_drill.py` — runs on every PR touching `paper_state`
or the migrator. The drill uses a sandbox dir `state/v5_paper_multi_rehearsal/`
and must complete in < 60s wall-clock.

## What `.v1.bak` files contain

The migrator preserves `.v1.bak` files for 7 days minimum (configurable
via `--commit-migration` flag in MIGRATION.md Step 8). The `.v1.bak`
file is the EXACT byte-for-byte v4-format state at the moment migration
ran — no transformation, no field stripping, no schema coercion.

Format: `state.json.v1.bak` next to the corresponding v4 `state.json`
location. The migrator refuses to overwrite an existing `.v1.bak` —
operator must delete an old backup before re-migrating the same pool.
