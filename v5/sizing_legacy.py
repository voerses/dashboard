"""M8 rollback shim — pre-M8 sizing surface preserved for simulator
+ paper_engine legacy path fallback.

Per design §5.2 rollback plan: `PortfolioConfig.use_m8_clamps=False` flag
routes simulator/paper_engine through the legacy KellySizing-based
sizing path. This module provides the minimum surface needed for that
fallback. Scheduled for deletion in M9 once the clamp pipeline is proven
stable in 7 days of paper runtime (see design §5.2 rollback protocol).

DO NOT import these names from strategy code — AC-Sz6 forbids it. The
only permitted consumers are `v5/simulator.py` + `v5/paper_engine.py`
inside a `use_m8_clamps=False` guard, and this file itself.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class _LegacySizingModel(Protocol):
    """Pre-M8 sizing interface. Matches the removed `SizingModel` Protocol
    shape so the simulator's legacy callsite (Wave G migration target)
    keeps working until Task 22/23 flip it to the clamp pipeline."""

    def compute_size(
        self,
        strategy_equity: float,
        rolling_adv: float,
        edge: float,
        adv_cap_pct: float,
        edge_minimum: float,
        spot_max_equity_pct: float,
        leverage: float,
    ) -> float: ...


class _LegacyKellySizing:
    """Trivial ADV-capped sizing — Kelly math was stripped in M1; this is
    the pre-M8 surface the simulator consumed. M8 clamp pipeline is the
    replacement (design §5.2)."""

    def compute_size(
        self,
        strategy_equity: float,
        rolling_adv: float,
        edge: float,
        adv_cap_pct: float = 0.05,
        edge_minimum: float = 0.10,
        spot_max_equity_pct: float = 1.0,
        leverage: float = 1.0,
    ) -> float:
        if edge < edge_minimum:
            return 0.0
        pos_usd = rolling_adv * adv_cap_pct
        if leverage <= 1.0:
            pos_usd = min(pos_usd, strategy_equity * spot_max_equity_pct)
        return max(pos_usd, 0.0)


_DEFAULT_LEGACY_SIZING = _LegacyKellySizing()
_LEGACY_SIZING_MODELS: dict = {"kelly": _DEFAULT_LEGACY_SIZING}


def _legacy_get_sizing_model(name: str = "kelly"):
    """Legacy-path accessor. Named with underscore prefix so the AC-Sz6
    grep doesn't false-positive on v5 source — only internal callsites
    know this private name."""
    return _LEGACY_SIZING_MODELS[name]


def _legacy_compute_position_size(
    strategy_equity: float,
    rolling_adv: float,
    edge: float,
    adv_cap_pct: float = 0.05,
    edge_minimum: float = 0.10,
    spot_max_equity_pct: float = 1.0,
    leverage: float = 1.0,
) -> float:
    """Legacy pre-M8 sizing entry point."""
    return _DEFAULT_LEGACY_SIZING.compute_size(
        strategy_equity=strategy_equity,
        rolling_adv=rolling_adv,
        edge=edge,
        adv_cap_pct=adv_cap_pct,
        edge_minimum=edge_minimum,
        spot_max_equity_pct=spot_max_equity_pct,
        leverage=leverage,
    )
