"""M4 AC41 — Shadow replay tolerance constants (single source of truth).

Updatable without touching harness logic. Per brief AC41.
"""
from __future__ import annotations

# Per-trade tolerances
PRICE_ULP_TOLERANCE: int = 4              # entry/exit prices within 4 ULP of float32
PER_TRADE_PNL_BPS_TOLERANCE: float = 5.0  # per-trade PnL delta ≤ 5 bps (0.05% of notional)

# Fleet-wide tolerances
AGGREGATE_PNL_BPS_TOLERANCE: float = 10.0  # total PnL delta ≤ 10 bps of total notional
