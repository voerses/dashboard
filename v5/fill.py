"""M7 — Fill dataclass with FIX triple (AC-O5).

Carries FIX ExecutionReport fields needed for audit-log reconstruction and
strategy-level dispatch:

  cl_ord_id        → FIX ClOrdID(11)     — caller-originated; stable across retries
  venue_order_id   → FIX OrderID(37)     — venue-assigned; None until ack
  exec_id          → FIX ExecID(17)      — unique per exec report
  exec_type        → FIX ExecType(150)   — what this report describes
  transact_time    → FIX TransactTime(60) — epoch nanoseconds
  last_qty         → FIX LastQty(32)     — filled size THIS report
  last_px          → FIX LastPx(31)      — filled price THIS report
  cum_qty          → FIX CumQty(14)      — total filled across all reports
  leaves_qty       → FIX LeavesQty(151)  — remaining open qty
  avg_px           → FIX AvgPx(6)        — VWAP across partials

Invariant (AC-O5): cum_qty + leaves_qty == original order_qty.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from v5.orders import ExecType


@dataclass(frozen=True, slots=True)
class Fill:
    """FIX-aligned exec report payload."""

    cl_ord_id: str
    venue_order_id: Optional[str]
    exec_id: Optional[str]
    exec_type: ExecType
    transact_time: int
    last_qty: float
    last_px: float
    cum_qty: float
    leaves_qty: float
    avg_px: float

    def __post_init__(self):
        if self.leaves_qty < 0:
            raise ValueError(
                f"Fill: leaves_qty must be >= 0; got {self.leaves_qty}"
            )
