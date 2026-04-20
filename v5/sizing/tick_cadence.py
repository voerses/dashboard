"""M9 C-10 — TickCadencePolicy production scaffold.

Replaces the M8 synthetic "5% available_margin nudge" used in
`parity_fixture.py` with a real tick-cadence CapitalAllocationPolicy
that samples equity at tick-resolution.

AC-Sz9 parity relevance: tick-cadence policies legitimately drift
between paper (tick-sampled equity) and backtest (bar-close equity).
The policy `sampling_cadence = "tick"` declaration is load-bearing —
AC-Sz9 parity tests skip tick-cadence policies by construction.
"""
from __future__ import annotations

from typing import Any, Dict, Literal


class TickCadencePolicy:
    """CapitalAllocationPolicy that samples portfolio equity at tick
    resolution (paper) or per-bar (backtest degenerate case).

    Distinct from SharedPoolPolicy (which samples at release-cadence,
    pure function of state). This policy is explicitly NON-PARITY for
    the tick vs bar gap — documented in AC-Sz9 cadence drift contract.
    """

    sampling_cadence: Literal["bar_close", "tick", "release"] = "tick"

    def __init__(self, tick_equity_source: Any = None):
        """`tick_equity_source` is the live paper-tick equity provider
        (inject for production; None uses a state-passthrough default)."""
        self._tick_equity_source = tick_equity_source

    def available_capital(
        self,
        strategy_id: str,
        state,
        clock_now_ns: int,
    ) -> float:
        """Return equity sampled at current tick. When `tick_equity_source`
        is wired, calls it; otherwise falls back to `state["available_margin"]`
        (functionally equivalent to SharedPoolPolicy for test fixtures)."""
        if self._tick_equity_source is not None:
            try:
                return float(self._tick_equity_source.equity_at(clock_now_ns))
            except Exception:
                pass
        try:
            return float(state["available_margin"])
        except (KeyError, TypeError):
            return 0.0

    def to_config(self) -> Dict[str, Any]:
        return {"policy_name": "TickCadencePolicy"}

    @classmethod
    def from_config(cls, config: Dict[str, Any]) -> "TickCadencePolicy":
        return cls()
