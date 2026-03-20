# Feature: Signal-Based Exit for V4 Engine

**Status:** Specify
**Created:** 2026-03-20
**Slug:** `signal-exit`

## Problem Statement

The V4 backtesting engine uses `max_hold` to control position duration — positions are forcibly closed after N bars regardless of market conditions. This works for fixed-horizon strategies but **destroys performance for trend-following strategies** that should hold until the signal flips.

### Evidence: s99 EMA 5/20

Strategy s99 uses a daily EMA 5/20 crossover on ETH. Positions should be held until the crossover direction changes (signal-based exit). Under V4's `max_hold` mechanism, s99 returns **-3.8%** because positions are closed while the trend is still active, then immediately re-entered (paying fees) or worse, stay open in the wrong direction after the signal flips.

The standalone minute-level backtester (`tools/backtest_ema520.py`) properly models signal-based exit and shows dramatically different results:

| Config | Period | Return | Max DD | Calmar | Trades | Liqs | Win Rate |
|--------|--------|--------|--------|--------|--------|------|----------|
| 4x cap=2.0 | 12mo | +530% | -16.5% | 32.1 | 15 | 0 | 93.3% |
| 6x cap=1.5 | 12mo | +664% | -18.5% | 35.9 | 15 | 0 | 93.3% |
| 4x cap=2.0 | 2yr | +1,993% | -20.7% | 96.1 | 34 | 0 | — |
| 4x cap=2.0 | 3yr | +6,219% | -20.7% | 300.1 | 50 | 0 | — |

Robustness: ALL 12 rolling 12-month windows (2021-2026) profitable at 4x cap=2.0 (min +134%, max +548%, DD -16.5% to -27.1%, Calmar always > 5.0).

### Root Cause

`v4/simulator.py` position lifecycle is hardcoded around `max_hold`:
- Positions auto-close at `max_hold` bars regardless of signal state
- No mechanism to query the originating strategy's current signal for exit decisions
- Re-entry after forced close creates fee drag and timing gaps

### Why Not Just Use the Standalone Backtester?

The standalone backtester proves the strategy works, but it cannot:
- Run in paper trading (V4 paper infrastructure handles live data, state persistence, exchange connectivity)
- Participate in multi-strategy portfolios
- Use V4's risk management (concentration limits, regime filters, pump filters)
- Produce standardized metrics compatible with the dashboard

Signal-based exit must be a V4 engine capability for s99 (and future signal-exit strategies) to go live.

## Objective

Add an optional signal-based exit mode to the V4 simulator so that strategies can control position exit timing based on their own signal state, rather than relying solely on `max_hold`.

### Requirements

1. **Signal exit mode**: Strategies can declare `exit_mode="signal"` (vs current implicit `exit_mode="max_hold"`)
2. **Exit callback**: When a position is in signal-exit mode, the simulator re-evaluates the strategy's signal each bar and closes the position when the signal direction changes (or goes flat)
3. **Safety max_hold**: Signal-exit positions still have a `max_hold` as a safety cap (e.g., 720 bars = 30 days) to prevent infinite holds if the strategy has a bug
4. **No impact on existing strategies**: All current strategies use `exit_mode="max_hold"` by default — zero behavior change
5. **Backward-compatible StrategyResult**: New field `exit_mode` with default `"max_hold"` — existing strategy files unchanged
6. **Paper trading support**: Signal-exit positions work in the paper trading loop (`v4/paper_runner.py`)
7. **Metrics compatibility**: Trades closed by signal exit are recorded the same way as max_hold exits — dashboard and metrics unchanged

## Non-Goals

- Multi-token signal correlation (exit ETH based on BTC signal)
- Partial exit / scaling out based on signal strength
- Signal-exit for spot positions (perp-only initially)
- Modifying any existing strategy to use signal exit (only s99 uses it initially)

## Success Criteria

1. s99 in V4 produces results within 10% of standalone backtester (matching on return, DD, trade count)
2. All existing strategies produce identical results with and without the change (regression)
3. s99 can run in paper trading with signal-based exit
4. No performance regression in V4 backtest runtime (< 5% slower)

## Key Files

| File | Role |
|------|------|
| `strategies/s99_ema520_signal_exit.py` | Strategy that needs signal-exit (already written, V4-approximation only) |
| `tools/backtest_ema520.py` | Standalone backtester with definitive results (validation reference) |
| `v4/simulator.py` | Core engine — position lifecycle, entry/exit logic |
| `v4/paper_runner.py` | Paper trading loop — needs signal-exit support |
| `engine.py` | `StrategyResult` dataclass — needs `exit_mode` field |

## Open Questions

1. Should `exit_mode="signal"` re-run the full `strategy()` function each bar, or should the strategy provide a lightweight `check_exit(ctx, position)` callback?
2. How to handle the case where a strategy's signal goes flat (neither long nor short) — close immediately or wait for opposing signal?
3. Should signal-exit positions be exempt from regime-based forced exits (`exit_regimes`), or should regime exits take priority?
