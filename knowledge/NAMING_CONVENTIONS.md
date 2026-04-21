# v5 Naming Conventions — Source of Truth (AC #2)

Canonical terms for v5 public APIs. Enforced by `tools/audit_naming.sh`.
Any PR touching v5/ must keep these invariants zero-hit.

## Core terms

| Canonical | Use for | NOT these |
|---|---|---|
| **`symbol`** | String identifier (e.g. `"BTCUSDT"`, `"ETH"`) | `token`, `ticker` |
| **`instrument`** | Metadata object (tick size, lot size, fee schedule) | `pair`, `market` (overloaded) |
| **`bar`** | Single OHLCV observation at the chosen cadence | `candle`, `kline` |
| **`position`** | Open live position | `trade` (for an open row) |
| **`closed_trade`** | Fully-closed position row | `filled_order`, `completed_trade` |
| **`order`** | Unfilled or partially-filled order (may carry multiple Legs) | `pending`, `armed` (both overloaded in v4) |
| **`leg`** | Sub-component of a multi-leg Order (FIX LegRefID 654) | `side`, `order_leg` |
| **`fill`** | The execution event (price + size at a moment) | `execution` (too broad) |
| **`strategy_id`** | Opaque string (e.g. `"s524m"`) | `strategy_name`, `sid` in public APIs |

## Why these choices

- **`symbol` vs `token`**: v5 public APIs standardize on `symbol`. `token`
  is retained internally for legacy compatibility in some strategy code
  that directly iterates per-token arrays; NEVER in public Protocols.
  An exception file at `tools/audit_naming_exceptions.txt` tracks
  legitimate `token:` type annotations and external-API tokens.
- **`bar` vs `candle`**: FIX uses `bar`; Binance APIs use `kline` or
  `candle`. We normalize to `bar` throughout v5/. Internal Binance client
  code may reference `kline` in WebSocket URL strings (that's wire-level
  Binance, not our vocabulary).
- **`instrument` vs `market`**: `market` is overloaded (`"spot"/"perp"`
  enum AND generic exchange-market concept). `instrument` is the
  metadata carrier; `market` is reserved for the enum literal on
  `Leg.settlement_type`.
- **`closed_trade`**: singular noun, no ambiguity. `filled_order` would
  imply the order shape survives, which is false (an Order may close
  multiple positions across legs).

## Enforcement

- `tools/audit_naming.sh` runs `rg -n '\btoken\b|\bcandle\b' v5/ --glob '*.py' --glob '!v5/tests/**'`.
- Exit non-zero on any hit not in `tools/audit_naming_exceptions.txt`.
- `tools/audit_naming_exceptions.txt` is an operator-maintained
  allowlist for legitimate exceptions (Python type `token: str`,
  Binance API `_token` fields, etc.).
- CI runs this script as part of the zero-warnings gate (AC #26).

## History

- Pre-M10: inconsistent — `token` widely used for both `symbol` strings
  and opaque identifiers.
- M10 (2026-04-21): consolidated naming per this document. Existing v4/
  references remain frozen; v5/ enforces the canonical terms.
