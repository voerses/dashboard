"""V5 Portfolio Backtest — Position sizing and market impact model.

Kelly curve stripped in M1. The SizingModel protocol and default KellySizing
class remain as extension points; the current implementation is a trivial
ADV-capped sizing that will be replaced in M8 (sizing redesign).
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

import numpy as np


# ---------------------------------------------------------------------------
# Sizing model protocol + default
# ---------------------------------------------------------------------------

@runtime_checkable
class SizingModel(Protocol):
    """Interface for position sizing models."""

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


class KellySizing:
    """Trivial ADV-capped sizing (Kelly math removed in M1; redesign in M8)."""

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
        """Compute position size in USD."""
        if edge < edge_minimum:
            return 0.0
        pos_usd = rolling_adv * adv_cap_pct
        # Spot equity cap: on spot (leverage <= 1.0), position cannot exceed equity allocation
        if leverage <= 1.0:
            pos_usd = min(pos_usd, strategy_equity * spot_max_equity_pct)
        return max(pos_usd, 0.0)


_DEFAULT_KELLY = KellySizing()

_SIZING_MODELS: dict[str, SizingModel] = {
    "kelly": _DEFAULT_KELLY,
}


def get_sizing_model(name: str = "kelly") -> SizingModel:
    """Look up a sizing model by name. Raises KeyError for unknown models."""
    return _SIZING_MODELS[name]


def compute_position_size(
    strategy_equity: float,
    rolling_adv: float,
    edge: float,
    adv_cap_pct: float = 0.05,
    edge_minimum: float = 0.10,
    spot_max_equity_pct: float = 1.0,
    leverage: float = 1.0,
) -> float:
    """Backward-compatible wrapper that delegates to KellySizing.compute_size()."""
    return _DEFAULT_KELLY.compute_size(
        strategy_equity=strategy_equity,
        rolling_adv=rolling_adv,
        edge=edge,
        adv_cap_pct=adv_cap_pct,
        edge_minimum=edge_minimum,
        spot_max_equity_pct=spot_max_equity_pct,
        leverage=leverage,
    )


# ---------------------------------------------------------------------------
# Slippage model protocol + registry (unchanged from v4)
# ---------------------------------------------------------------------------

@runtime_checkable
class SlippageModel(Protocol):
    """Interface for market impact / slippage models."""

    def compute_slippage(
        self,
        pos_usd: float,
        adv: float,
        base_spread_bps: float,
        impact_coeff: float,
        max_slip_bps: float,
    ) -> float: ...


class SqrtImpactSlippage:
    """Square-root market impact model (matches v3 JIT engine.py:534-538).

    slip_bps = base_spread + impact_coeff * sqrt(participation) * 10000
    where participation = pos_usd / (adv / 24)
    """

    def compute_slippage(
        self,
        pos_usd: float,
        adv: float,
        base_spread_bps: float = 3.0,
        impact_coeff: float = 0.03,
        max_slip_bps: float = 300.0,
    ) -> float:
        participation = pos_usd / max(adv / 24.0, 1.0)
        slip_bps = base_spread_bps + impact_coeff * np.sqrt(participation) * 10000.0
        return min(slip_bps, max_slip_bps)


_DEFAULT_SLIPPAGE = SqrtImpactSlippage()

_SLIPPAGE_MODELS: dict[str, SlippageModel] = {
    "sqrt": _DEFAULT_SLIPPAGE,
}


def get_slippage_model(name: str = "sqrt") -> SlippageModel:
    """Look up a slippage model by name. Raises KeyError for unknown models."""
    return _SLIPPAGE_MODELS[name]


def compute_slippage_bps(
    pos_usd: float,
    adv: float,
    base_spread_bps: float = 3.0,
    impact_coeff: float = 0.03,
    max_slip_bps: float = 300.0,
) -> float:
    """Square-root market impact model — backward-compatible wrapper."""
    return _DEFAULT_SLIPPAGE.compute_slippage(
        pos_usd, adv, base_spread_bps, impact_coeff, max_slip_bps,
    )
