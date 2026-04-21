# v5 Sizing System (M8 clamps + M9 Protocols)

Companion to the archived `V4_SIZING_SYSTEM.md`. Documents the
v5-canonical sizing pipeline shipped in M8 and extended in M9. M10
freezes this contract.

## Overview

v4's sizing was a 9-parameter opaque pipeline with overlapping
constraints. v5 replaces it with:

1. **2 transparent intents** (M8):
   - `FIXED_FRACTION`: size = equity × fraction (per-strategy)
   - `FIXED_NOTIONAL`: size = notional (externally supplied)

2. **6 explicit engine clamps** (M8), applied in order:
   - `adv_cap` — token-level ADV ceiling (default 0.005 = 0.5%)
   - `concentration` — per-strategy concentration limit
   - `free_capital` — portfolio-level remaining capital
   - `min_size` — minimum notional (exchange lot size)
   - `liq_distance` — distance-to-liquidation safety margin
   - `slippage` — slippage-adjusted fill price bound

3. **Pluggable Protocols** (M9):
   - `SignalArbitrationPolicy` — ranks & deduplicates entry candidates
     across strategies. Default: `RandomShuffle` (seed-deterministic).
     Alternatives: `PriorityDesc`, `TieredPriority`, `RoundRobin`.
   - `CapitalAllocationPolicy` — splits capital across strategies per
     bar. Default: `SharedPoolPolicy`. Alternatives: strategy-specific
     budgets (written by user per README in `v5/sizing/`).
   - `RiskComponent` — pre-trade gating. 8 default components:
     `DrawdownThrottle`, `MaxGrossExposure`, `MaxNetExposure`,
     `MaxConcurrentOrders`, `DailyLossLimit`, `FundingSafetyCheck`,
     `MaxCorrelatedExposure`, `PerSymbolStrategyLimit`.
   - `TickCadencePolicy` — `bar_close` | `tick` | `release` (M9 C-10).

## Phase ordering (engine dispatch)

```
Strategy.generate() → TokenSignal(direction, priority, sizing_request)
                             │
                             ▼
[3.0 Risk gating]  check_entry_allowed() via RiskComponent list
                             │  HALT → flip TradingState; REJECT → drop
                             ▼
[3.05 Dedup]       collapse duplicate (strategy, token) signals
                             │
                             ▼
[3.10 Arbitration] config.arbitration_policy.rank(candidates)
                             │
                             ▼
[3.20 Allocation]  config.capital_allocation_policy.allocate()
                             │
                             ▼
[3.30 Clamps]      run_clamp_pipeline(order, market_state, policy, config)
                             │  binding clamp logged to sizing_fills.jsonl
                             ▼
                   Order execution
```

## Intent selection

| Scenario | Intent |
|---|---|
| Strategy emits `fraction_of_equity=0.05` | `FIXED_FRACTION` |
| Strategy emits explicit `notional_usd=50000` | `FIXED_NOTIONAL` |
| Legacy Kelly sizing migration | Initial output → `FIXED_FRACTION` equivalent via `compute_fixed_fraction_notional` helper (M10 B1) |

Default: `FIXED_FRACTION` with `fraction = spot_max_equity_pct`. The
legacy `v5.sizing_legacy.compute_size()` path is being migrated to
`FIXED_FRACTION` throughout in M10 Cluster-B (B1 equivalence test,
B2 file delete).

## Clamp pipeline details

Each clamp receives `(order, current_size_usd, market_state, policy,
config)` and returns `(new_size_usd, binding_reason)`. A clamp "binds"
if it reduces the size further than upstream clamps.

- **binding_reason** is logged to `v5/logs/sizing_fills.jsonl` (M8
  AC-Sz6 — one JSONL entry per order, even if non-binding).
- **Order of clamps matters**: ADV cap is cheapest; slippage is most
  expensive. Running in this order minimizes wasted computation.

## M9 test-dispute: `AllocationState` fields

M8's `test_allocation_state_fields` asserted exactly 4 fields. M9
added `market_snapshot` (for tick-cadence-aware policies). Resolution
per `.specs/telemetry.jsonl`: relax test to "at least these 4 core
fields" — retain invariance on the original 4, allow future additions.

## M10 deliverables

- **B1** — `test_m10_sizing_fixed_fraction_equivalence.py` — proves
  clamp pipeline `FIXED_FRACTION` = `_LegacyKellySizing.compute_size()`
  byte-identical over 1000 seeded tuples.
- **B2** — `sizing_legacy.py` DELETED once B1 green.
- **D-19** — `use_data_engine=True` becomes default in
  `PortfolioConfig`. Phase-4 deletes the `=False` fallback branch after
  parity-verification per AC #19.

## Funding cost (M10 AC #16 + #18)

- Per-snap formula: `sign(direction) × rate × notional` where
  `notional = quantity × entry_price`. Margin-based formula is a
  category error (margin is collateral, not exposure).
- Snap cadence: 00/08/16 UTC exact (no drift).
- Cross-path parity (AC #18): backtest + paper emit byte-identical
  `funding_accruals.jsonl` archives under `TestClock`. Order-preserving
  compare (audit 2026-04-20: sort-before-compare would mask FIX-layer
  ordering bugs).

## References

- `v5/sizing/intents.py` — `SizingIntent`, `SizingRequest`.
- `v5/sizing/clamps.py` — `run_clamp_pipeline`.
- `v5/sizing/allocation.py` — `CapitalAllocationPolicy`.
- `v5/sizing/tick_cadence.py` — `TickCadencePolicy`.
- `v5/arbitration.py` — `SignalArbitrationPolicy` + 4 impls.
- `v5/risk.py` — `RiskComponent` + 8 default impls.
- `.specs/done/m8-sizing-redesign/brief.md` — full M8 contract.
- `.specs/active/m9-cleanups/brief.md` — M9 Protocol specs.
