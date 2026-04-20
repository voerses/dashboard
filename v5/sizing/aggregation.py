"""M8 Task 24 — Multi-leg reserved-capital aggregation under ContingencyType.

Thin wrapper over `v5.orders.compute_reserved_capital` that exposes the
aggregation in a sizing-layer-friendly API. The underlying M5/M7 rules
are preserved:
  - NONE / OTO / OUO → sum(leg.sizing_ctx["margin_usd"])
  - OCO              → max(leg.sizing_ctx["margin_usd"])
  - OTOCO            → leg[0].margin + max(leg[1:].margin)  (entry +
                          max siblings, round-6 FIX fix)
  - Single-leg       → order.sizing_ctx["margin_usd"] (bare fallback)
"""
from __future__ import annotations

from typing import Any

from v5.orders import compute_reserved_capital


def reserved_capital_for_clamp(order: Any) -> float:
    """Return the aggregate margin to reserve at the free-capital clamp
    under the Order's `contingency` rule. Delegates to
    `v5.orders.compute_reserved_capital` (single source of truth); the
    sizing-layer wrapper exists so Task 24 tests can import from a
    sizing module instead of reaching into orders.py internals.
    """
    return float(compute_reserved_capital(order))
