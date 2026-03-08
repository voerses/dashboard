# Dev Session: Dashboard Overhaul — 2026-03-05

## What Was Done

### 1. Terminology Rename
- `wallet` → `portfolio` → `simulation` (throughout codebase)
- `wallets.json` → `portfolios.json` → `simulations.json`
- A simulation run = capital + strategies + tokens (strategies share capital pool)

### 2. Dashboard Complete Rewrite (`tools/generate_dashboard.py`)

**Signal Enrichment** — Each trade now has full indicator snapshot at entry:
- Calls `eng._build_context(token, df)` + `strat_fn(ctx)` after backtest
- Looks up indicator values at `entry_bar`: ADX, RSI, vol_ratio, ATR, regime, ret_24h, leverage, stops
- Generates human-readable `signal_short` text per strategy (e.g., "Burst +3.5% | ADX 24 | Vol 2.8x")

**New Dashboard Features:**
- **Open Positions panel** — Green-bordered section at top showing all open positions with signals, regime, leverage, P&L
- **Simulation tabs** — Switch between sim runs (sim1: s11 only, sim2: s33 only, sim3: both sharing $200k)
- **KPI row** — Capital, Value, P&L, Max Drawdown, Trades, Win Rate, Best/Worst Day, Total Fees
- **Time-based equity curve** — Calendar date x-axis (was trade-indexed before). Only shows "Total" line when >1 strategy.
- **Daily P&L** — Stacked bars per strategy + cumulative line on secondary y-axis
- **Expandable trade rows** — Click to see full indicator grid (rendered on-demand, not upfront)
- **Per-token summary** — With best/worst trade columns
- **Regime chips** — Color-coded (DOWNTREND=red, UPTREND=green, etc.) in trade table

**Performance Optimizations:**
- Stripped duplicate `strategies[].trades` arrays (was 2x the data)
- Removed unused fields from trade JSON (entry_bar, exit_bar, simulation, market_type)
- Trade detail panels rendered on click, not pre-rendered (saved ~9000 DOM nodes)
- Initial trade table shows 50 rows with "Show more" button
- Charts deferred via `setTimeout` so tables paint first
- Added `no-cache` meta headers

**Removed Charts (were too slow / not useful):**
- Drawdown chart (max DD shown as number in KPI row instead)
- Hourly activity heatmap

### 3. Simulations Config (`simulations.json`)
```json
[
  {"id": "sim_1", "name": "Simulation Run 1", "capital": 100000, "strategies": ["s11_momentum_burst"], ...},
  {"id": "sim_2", "name": "Simulation Run 2", "capital": 100000, "strategies": ["s33_leveraged_conviction_perp"], ...},
  {"id": "sim_3", "name": "Simulation Run 3", "capital": 200000, "strategies": ["s11_momentum_burst", "s33_leveraged_conviction_perp"], ...}
]
```

### 4. GitHub Pages Deployment
- Push function: clones gh-pages branch, copies index.html, commits, pushes
- Added `.nojekyll` to bypass Jekyll processing
- Added `data.json` as backwards-compat fallback for cached old HTML version

## Known Issues (To Fix Next Session)

### P0: GitHub Pages serving stale version
- **Symptom:** `voerses.github.io/dashboard/` serves old 25KB fetch-based HTML with `drawHeatmap` that crashes Plotly
- **Root cause:** Gitea→GitHub mirror not propagating gh-pages branch updates (or slow CDN)
- **Workaround applied:** Added `data.json` so old cached HTML can still load data
- **Fix needed:** Either push gh-pages directly to GitHub (bypass Gitea proxy), or configure Gitea mirror to sync gh-pages branch
- **Files:** `tools/generate_dashboard.py` (push_to_ghpages function)

### P1: s33 strategy losing money
- -15.1% in 1 month, 27% win rate, $15,161 exchange fees on $100k capital
- 369 trades — way too many, most exit via `max_hold`
- Entry conviction threshold (0.20 long, 0.25 short) may be too low
- Fee model: $15k fees on 369 leveraged perp trades seems realistic but brutal
- Needs parameter tuning or fundamental rethink before paper trading

### P2: Dashboard improvements deferred
- Drawdown chart (removed for perf, could add back as lightweight CSS sparkline)
- Hourly activity heatmap (removed, could be pure HTML table instead of Plotly)
- Auto-refresh during paper trading (poll for new data)
- Strategy comparison view (overlay equity curves from different sims)

## Files Modified

| File | Change |
|------|--------|
| `tools/generate_dashboard.py` | Complete rewrite — signal enrichment, new layout, perf optimizations |
| `simulations.json` | New file — 3 simulation configs |
| `memory/PROJECT_STATUS.md` | Updated with dashboard, s33, open items |
| `v3/engine.py` | Array support for stop_mult/trail_mult (done in prior session) |
| `strategies/s33_leveraged_conviction_perp.py` | New strategy (done in prior session) |

## Technical Notes for Next Session

- `ctx.regime_1h` is the correct attribute (not `ctx.regime`)
- `StrategyResult.leverage` and `.stop_mult` can be numpy arrays (per-bar) or float scalars
- Dashboard uses `eng._build_context()` which is a private API — works but brittle
- Plotly full bundle is 3.5MB — not a perf issue on fast connections, the real bottleneck was DOM node count
- GitHub Pages CDN at `voerses.github.io` has aggressive caching, `no-cache` meta tags help for browser but not CDN edge
