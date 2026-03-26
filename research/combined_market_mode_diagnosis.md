# Combined Market Mode 0-Trade Bug — Diagnosis

**Date:** 2026-03-26
**References:** R139, R144
**Severity:** Silent data loss (0 trades, no error)

## Root Cause

There is an AND gate in `v4/simulator.py:557-560` that requires **both** primary and secondary entry masks to fire on the same bar for a combined-mode trade to open:

```python
# simulator.py:557-560
if sig.is_combined:
    if sig.secondary_entry_mask is None or not sig.secondary_entry_mask[local_bar]:
        continue  # <-- silently drops the entry
```

When a single-context strategy (one that takes only `ctx_spot` or `ctx_perp`) is run with `--market combined`, the following chain causes all entries to be dropped:

1. `signals.py:227` — `is_combined = True` because `strategy_spec.market == "combined"`
2. `signals.py:337-341` — strategy is called with a single context (detected via `inspect.signature`), returns a `StrategyResult` with `secondary_entry_mask = None` (the dataclass default in `engine.py:471`)
3. `signals.py:409` — the fallback creates an **all-zeros boolean array**: `np.zeros(n_safe, dtype=bool)`
4. `signals.py:628` — `TokenSignals.is_combined = True` with `secondary_entry_mask` all False
5. `simulator.py:557-560` — the AND gate checks `secondary_entry_mask[local_bar]`, which is always False
6. Every entry candidate is silently skipped. **0 trades.**

There is no warning, error, or diagnostic log when this happens. The signal funnel shows entries in the primary mask but they never reach the position-opening logic.

## Code Path Summary

```
portfolio_backtest.py  --market combined (default)
  → StrategySpec(market="combined")
  → signals.py: is_combined=True
  → signals.py: strategy_fn(ctx_spot)  [single-context, no perp context passed]
  → StrategyResult.secondary_entry_mask = None  [strategy doesn't set it]
  → signals.py:409: sec_entry = np.zeros(n_safe, dtype=bool)  [ALL FALSE]
  → TokenSignals(is_combined=True, secondary_entry_mask=[all False])
  → simulator.py:557-560: AND gate kills every entry
  → 0 trades
```

## Which Strategies Are Affected

**All single-context (Class A) strategies** when run with `--market combined`. These are strategies whose function signature takes exactly 1 argument (`StrategyContext`). Examples:

- `s320` (V3 momentum overlays) — BTC-only spot
- `s320a` (binary gates) — BTC-only spot
- `s320b` (floored mult) — BTC-only spot
- Any other single-argument strategy that doesn't populate `secondary_entry_mask`

**NOT affected:**

- Dual-context strategies that explicitly set `secondary_entry_mask` (e.g., `s30` basis carry, `s31` funding hedged, `s32` regime spot-perp)
- Strategies that return per-bar `market_type` arrays (adaptive venue routing) — these get `ts_is_combined = False` at `signals.py:434`, bypassing the AND gate
- Strategies explicitly run with `--market spot` or `--market perp`

## Additional Combined-Mode Blockers

Even if the AND gate were fixed, combined strategies in **raw mode** (`--raw`) are explicitly skipped with a separate rejection at `simulator.py:636-643`:

```python
if config.raw_mode:
    if sig.is_combined:
        state.rejections.raw_combined_skip += 1
        continue  # "Combined strategies need atomic two-leg handling"
```

This is documented behavior, not a bug.

## Is This a Bug or Expected Behavior?

**It is a bug in the default behavior, not in the logic itself.**

The AND gate is correct for true combined strategies (e.g., basis carry needs both spot long AND perp short to fire simultaneously). The bug is that:

1. `--market combined` is the **default** (`portfolio_backtest.py:164`: `market = args.market or "combined"`)
2. There is no validation that a single-context strategy is incompatible with combined mode
3. The failure is **silent** — no warning, no error, just 0 trades
4. The `run_backtest()` function also defaults to `"combined"` (line 120) when no market override exists

The correct behavior would be one of:
- Auto-detect: if strategy is single-context, force `market=spot` (or `perp`) instead of `combined`
- Validate: reject with a clear error if a single-context strategy is paired with `--market combined`
- Warn: emit a loud warning when `secondary_entry_mask` falls back to all-zeros

## Does This Block Trend+Carry Architecture?

**No.** The trend+carry architecture uses dual-context strategies (like `s30`, `s31`, `s32`) that explicitly set `secondary_entry_mask`. These work correctly in combined mode. The bug only affects running single-market strategies (trend, momentum) under the combined default.

**Workaround:** Always pass `--market spot` for spot-only strategies and `--market perp` for perp-only strategies. Do not rely on the combined default for single-context strategies.

## Recommended Fix (Not Implemented Here)

Add strategy-market compatibility validation in `signals.py:precompute_strategy_signals()` after line 226:

```python
if is_combined and is_single_ctx:
    print(f"  WARNING: {strategy_spec.strategy_id} is single-context but market=combined. "
          f"Forcing market=spot (single-context strategies cannot produce secondary_entry_mask).")
    is_combined = False
    # OR: raise ValueError("Single-context strategy incompatible with combined mode")
```
