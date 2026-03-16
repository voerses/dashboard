# Dev Session 2026-03-16: Circuit Breaker & Pump-and-Dump Protection

## What We Did

1. **PIPPIN crash analysis** — Meme coin pumped then dumped -45% (0.367→0.202) in 10 hours.
   18 open positions across multiple pools totaling -$63K unrealized loss. s63 (mean-reversion)
   entered LONG fading the vol spike — structurally wrong for pump-and-dump.

2. **Circuit breaker implementation** — Added 4R emergency exit in `v4/simulator.py` that fires
   during the `no_stop_bars` protection window. Tested R=2,3,4,5 via A/B backtests. R=4.0
   selected as optimal: s60 +13%, s65 88%, s63 71% retention.

3. **Dashboard timestamp fix** — `paper_engine.py` used `time.gmtime()` (wall-clock) for equity
   CSV timestamps. Fixed to derive from `sig.timestamps[-1]` (actual bar close time). Added
   defensive sort in dashboard loader.

4. **Pump-and-dump research** — Entry-side detection preferred over exit-side circuit breaker.
   Three recommended layers: Volume-Vol Anomaly Score, ADV Pump Risk Discount, Funding Rate
   Extremes entry blocker.

## Fuckups & Learnings

### 1. Wrong state.json keys
**What happened:** Used `state.get('positions', [])` — returned empty list. Actual key is
`state.get('open_positions', [])`.
**Learning:** Always `print(state.keys())` or read the actual file before assuming key names.

### 2. Wrong equity.csv column names
**What happened:** Parsed with `row.get('equity', 0)` and `row.get('unrealized_pnl', 0)` —
both returned 0. Actual columns: `portfolio_equity`, `mark_to_market_equity`.
**Learning:** Read actual CSV headers before writing parsing code.

### 3. Module caching in A/B tests
**What happened:** Used `importlib.reload(v4.simulator)` to test different R values. All R
values (2,3,4,5) gave identical results because `run_backtest()` had already bound the old
module references. Wasted 20 minutes debugging "why all R values give same results."
**Learning:** In-process reimport doesn't propagate to already-imported function references.
For A/B tests with different module constants, use **separate Python processes** (subprocess
or `python -c` with `sed` to patch the source file).

### 4. R=2.0 was far too aggressive
**What happened:** Initially deployed R=2.0 (from Van Tharp recommendation). Backtests showed
27-43% retention — the circuit breaker was killing 57-73% of returns. Most "legitimate"
drawdowns exceed 2x initial risk before recovering.
**Learning:** Academic risk management recommendations (2-3R hard stops) don't translate directly
to crypto mean-reversion strategies where initial volatility is inherently high. Always
backtest R-values before deploying. R=4.0 is the sweet spot for this system.

### 5. PID lock file blocking restart
**What happened:** Old paper trader held `paper.pid` lock. New process couldn't start with
"Another instance is already running."
**Learning:** `kill -9 <pid> && rm -f state/v4_paper_multi/paper.pid` before restart. The lock
file cleanup is not atomic with process death.

### 6. Wall-clock vs bar-close timestamps
**What happened:** Equity CSV timestamps jumped backwards because `time.gmtime()` records when
processing happens (variable), not when the candle closed (fixed). After replay + live ticks,
the last row had an earlier timestamp than replay rows.
**Learning:** Always derive timestamps from the data itself (candle close time), never from
wall-clock. Wall-clock introduces non-determinism.

### 7. Circuit breaker only protects during no_stop_bars window
**Key insight:** After `bars_held >= no_stop_bars`, the trailing stop takes over and provides
continuous protection. The circuit breaker is specifically for the "naked" period (12-48 bars)
where ALL stops are disabled. It's a safety net, not a replacement for the trailing stop system.

## New Mission: Pump-and-Dump Entry Detection

See PROJECT_STATUS.md for mission details. Three layers to implement:
1. Composite Volume-Volatility Anomaly Score (entry filter, blocks pump_risk > 0.6)
2. ADV-Tiered Sizing with aggressive low-ADV penalty (static sizing)
3. Funding Rate Extremes as LONG entry blocker (z-score > 3.0)

Estimated backtest cost: 2-5% vs 12-29% for exit-side circuit breaker.
