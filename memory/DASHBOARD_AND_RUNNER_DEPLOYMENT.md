# Dashboard & Runner Deployment Guide

## Post-Mortem: 2026-03-24 Dashboard Incident

### What happened
During a session fixing a minor filter UI bug, the live dashboard at `/srv/dashboard/current/index.html` was overwritten with output from `generate_dashboard_v2.py` — a deprecated tool. This replaced the working live dashboard (which polls `state.json`) with an old snapshot-based dashboard with embedded stale data.

### Root cause
1. **Wrong mental model**: Assumed `generate_dashboard_v2.py` was the source of truth for the dashboard. It is NOT — it's deprecated.
2. **Wrote directly to a symlinked release directory**: `/srv/dashboard/current` is a symlink to `releases/1774167682/`. Writing to `current/index.html` overwrites the release in-place with no backup.
3. **No verification**: Did not verify the restored dashboard was correct before moving on.

### Lesson learned
- **NEVER run `generate_dashboard_v2.py`** — it produces an outdated embedded-data dashboard
- **NEVER write directly to `/srv/dashboard/current/`** — use the atomic deploy pattern
- **Always verify by checking the dashboard in a browser or checking for `state.json` polling code**

---

## Architecture

```
Paper Trading Runner (v4/run_paper_multi.py)
    │
    ├─ writes /srv/data/state.json every ~1 second (atomic: .tmp + mv)
    │   └─ Contains: portfolios, trades, equity history, live prices
    │
    └─ does NOT generate dashboard HTML

Dashboard (client-side only)
    │
    ├─ Served by Caddy container from /srv/dashboard/current/
    ├─ Polls /data/state.json every 1 second
    ├─ Renders live data in browser (no server-side runtime)
    └─ Uses Plotly for equity charts
```

## Dashboard Source

| Location | Purpose | Status |
|----------|---------|--------|
| `docs/index.html` | **THE dashboard source** (git-tracked) | ACTIVE |
| `tools/generate_dashboard_v2.py` | Old snapshot generator (embedded data, no polling) | **DEPRECATED — DO NOT USE** |
| `dashboard/gh-pages` remote | Old GitHub Pages deployment (stale snapshot from Mar 9) | **DEPRECATED — DO NOT USE** |
| `v3/dashboard_paper.py` | Legacy v3 dashboard | **DEPRECATED** |

## How to Deploy a Dashboard Update

### 1. Edit the source
```bash
# Edit docs/index.html (the ONLY dashboard source file)
vim docs/index.html
```

### 2. Deploy to live
```bash
# Copy to the existing release (quick update, same release)
cp docs/index.html /srv/dashboard/releases/1774167682/index.html

# OR: Create a new release (preferred for significant changes)
RELEASE_DIR="/srv/dashboard/releases/$(date +%s)"
mkdir -p "$RELEASE_DIR"
cp docs/index.html "$RELEASE_DIR/"
cp /srv/dashboard/releases/1774167682/plotly-2.27.0.min.js "$RELEASE_DIR/"
cp /srv/dashboard/releases/1774167682/debug.html "$RELEASE_DIR/"
ln -sfn "$RELEASE_DIR" /srv/dashboard/current.new
mv -T /srv/dashboard/current.new /srv/dashboard/current
```

### 3. Verify
- Check the dashboard loads in a browser
- Confirm connection status shows "LIVE" (not offline)
- Confirm portfolio data is current (not stale placeholder)

## How to Deploy/Restart the Runner

### Check current runner
```bash
cat state/v4_paper_multi/paper.pid
ps aux | grep run_paper_multi
```

### Stop the runner
```bash
kill $(cat state/v4_paper_multi/paper.pid)
# Wait for graceful shutdown
sleep 3
```

### Start the runner
```bash
cd /workspace/crypto_backtest
nohup /workspace/venv/bin/python -m v4.run_paper_multi \
    --config configs/multi_v4_paper.json \
    >> /tmp/paper_multi.log 2>&1 &
```

### Verify runner is healthy
```bash
# Check PID is alive
ps -p $(cat state/v4_paper_multi/paper.pid)

# Check state.json is being written
ls -la /srv/data/state.json
python3 -c "import json; d=json.load(open('/srv/data/state.json')); print('Status:', d.get('runner_status'), '| Portfolios:', len(d.get('portfolios',[])))"

# Check recent logs
tail -20 /tmp/paper_multi.log
```

### Single tick test (without disrupting live runner)
```bash
/workspace/venv/bin/python -m v4.run_paper_multi \
    --config configs/multi_v4_paper.json --once --status
```

## What NOT to Do

1. **DO NOT run `generate_dashboard_v2.py`** — produces deprecated embedded-data HTML
2. **DO NOT push to `dashboard/gh-pages` remote** — that's a stale snapshot, not the live dashboard
3. **DO NOT write directly to `/srv/dashboard/current/index.html`** without understanding it's a symlink
4. **DO NOT use `git checkout` or `git reset` to restore dashboard** — uncommitted Python changes would be lost. Only `git show <commit>:docs/index.html` is safe.

## File Inventory

| Path | What | Updated by |
|------|------|------------|
| `/srv/data/state.json` | Live portfolio data (JSON) | Runner every ~1s |
| `/srv/dashboard/current/` | Symlink to active release | Manual deploy |
| `/srv/dashboard/releases/*/` | Release directories | Manual deploy |
| `docs/index.html` | Dashboard source (git) | Developer |
| `configs/multi_v4_paper.json` | Runner config (portfolios) | Developer |
| `state/v4_paper_multi/paper.pid` | Runner PID file | Runner on start |
| `/tmp/paper_multi.log` | Runner log output | Runner |
