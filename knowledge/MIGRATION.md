# v4 → v5 Migration Runbook (M10)

Operator-facing runbook for migrating a strategy + live paper-trading
state from v4 to v5. Tested via `test_m10_rollback_drill.py` (AC #20).

## Pre-flight (do NOT skip)

### Step 0 — Verification

Run v5 backtest on reference strategy `s524m` with the baseline flags:

```bash
/workspace/venv/bin/python v4/portfolio_backtest.py \
  --strategy s524m --capital 100000 --market perp \
  --conviction-mode ranked --adv-cap 0.005 \
  --max-portfolio-positions 50 --skip-wf \
  --start-date 2022-01-01 --end-date 2026-03-31 --seed 42
```

Confirm per-year total_return matches the M10 AC #10 baseline table
(+180.56% / +267.65% / +70.17% / +491.0% / +39.4%). Do NOT stop v4
paper until this passes.

Step 0 also includes rewriting `s513`, `s523c`, `s524m` for v5 API.
These are clean rewrites, not mechanical ports. Estimated: s513 ~2h,
s523c ~3h, s524m ~5h.

## 11-Step runbook

### Step 1 — Graceful v4 paper shutdown

**Signal sequence:**
1. `bash tools/stop_all_services.sh` (existing — sends SIGTERM).
2. Wait 30 seconds for drain — v4 paper must close any in-flight bars.
3. If `state/v4_paper_multi/paper.pid` still exists after 30s, escalate
   to `kill -SIGKILL <pid>`.
4. Confirm `state/v4_paper_multi/paper.pid` is removed.
5. Confirm no partial writes: `tail state/v4_paper_multi/trades.csv` —
   the last row must be a complete trade record (no truncated fields).

### Step 2 — Backup v4 state

```bash
cp -r state/v4_paper_* backups/v4-pre-v5/$(date -u +%Y%m%dT%H%M%SZ)/
```

### Step 3 — Data integrity check

```bash
bash tools/check_data_integrity.sh
```

### Step 4 — Dry-run migration

For each pool:

```bash
/workspace/venv/bin/python -m v5.migrate_state_v1_to_v2 \
  --dry-run \
  --input state/v4_paper_s513/state.json \
  --output state/v5_paper_s513/state.json
```

Verify output schema + position counts + equity sum.

### Step 5 — Real migration

Drop `--dry-run`. After each migration:

```bash
/workspace/venv/bin/python -c "
import json
v4 = json.load(open('state/v4_paper_s513/state.json'))
v5 = json.load(open('state/v5_paper_s513/state.json'))
sum_v4 = sum(p['margin_usd'] for p in v4.get('open_positions', []))
sum_v5 = sum(p['margin_usd'] for p in v5.get('active_positions', []))
assert abs(sum_v4 - sum_v5) < 0.01, f'equity mismatch: {sum_v4} vs {sum_v5}'
print('OK equity preserved')
"
```

### Step 5.5 — Connection verification

Start v5 paper in `--dry-run` mode. Verify:

- `BinanceWSClient` connects successfully.
- `DataEngine` populates `RollingCache` with bars for all subscribed tokens.
- No errors in `v5/logs/paper_runner_v5.log`.

Only proceed to live mode after data flow confirmed.

### Step 6 — Start v5 paper

```bash
bash tools/start_v5_paper.sh
```

Verify first-tick equity reconciles with v4 last-tick within $0.01.

### Step 6.5 — Alert + WS endpoint verification (step 6.5)

- Verify alert endpoints (drawdown_alert_pct threshold, any configured
  notification channels) fire correctly. Send a synthetic test alert.
  Confirm receipt.
- Verify WS rate-limit handling: run
  `bash tools/ws_ratelimit_parallel_smoke.sh 30` (30-minute operator
  smoke). Both v4 + v5 must coexist without triggering 429s OR demonstrate
  the backoff path works if 429s occur. Per AC #25b.

### Step 7 — 24h operator monitoring

The operator (NOT a test — this is a human-in-the-loop observation
window) watches:

- RSS growth curve via `v5/logs/rss.jsonl`.
- Trade-rate within ±20% of v4 baseline.
- Alert smoke-tested once mid-window.
- No state-corruption events.

This 24h is an **operator's choice**, not a required CI gate. The
replay-based equivalent of the M10 `test_m10_memory_growth_replay.py`
(AC #8) is what closes the M10 parity gate.

### Step 8 — After 7 days green: commit migration

```bash
/workspace/venv/bin/python -m v5.migrate_state_v1_to_v2 --commit-migration
```

Moves `.v1.bak` files to `backups/v4-archive/{timestamp}/` (retained 90
days). Explicit `--purge-archive` required for permanent deletion.
Until `--commit-migration`, rollback is instant.

### Step 9 — If regression: roll back

See `ROLLBACK.md` for the stop-restore-restart sequence. AC #20
verifies this procedure via a sandbox drill.

## FAQ

- **Q: What if v4 runner fails SIGTERM?** Escalate to SIGKILL per Step 1;
  then replay with `--replay-from-ts` flag to reconstruct any lost
  trades (Phase-4 feature).
- **Q: How long does the migration take?** < 5 minutes per pool
  (deterministic file transform, no network I/O).
- **Q: Can I rollback after 7 days?** Yes, until `--commit-migration`
  runs (Step 8). After `--commit-migration`, rollback requires manual
  restoration from `backups/v4-archive/`.
