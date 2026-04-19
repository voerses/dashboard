"""M7 — Strategy ports to v5 Strategy Protocol.

Reference ports for s513, s523c (no parity required) and s524m (AC-S10 parity
within 0.5% of v4 over Q-DEC4). All inherit from `v5.strategy_api.BaseStrategy`
and publish `TokenSignal`s via `generate()`.

Ports strictly avoid module-level mutable state (AC-H4 / strategy_loader
AST scan): caches, counters, and per-symbol data live on `self`, not in
module-level dicts.

The original `strategies/s*.py` files in v4's top-level package remain
FROZEN (per CLAUDE.md meta-rule #6 and user directive 2026-04-19:
"dont touch the originals in v4 obviously").
"""
