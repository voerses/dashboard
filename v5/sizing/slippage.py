"""M8 AC-Sz3 clause 6 — SqrtImpact slippage (moved from legacy v5/sizing.py).

Industry-standard square-root market-impact model:
  slip_bps = base_spread + impact_coeff × sqrt(participation) × 10000
  where participation = pos_usd / (adv / 24)

Clamp #6 uses this to adjust `fill_price`; it is NEVER a reject.
Preserved verbatim from M1's shipped implementation so byte-identity
holds between pre-M8 and post-M8 backtest output (AC-Sz6 behavior
preservation for slippage).
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

import numpy as np


@runtime_checkable
class SlippageModel(Protocol):
    """Market-impact slippage interface. Single-method Protocol; both
    signature and default values preserved from pre-M8 `v5/sizing.py`.
    """

    def compute_slippage(
        self, pos_usd: float, adv: float,
        base_spread_bps: float = 3.0,
        impact_coeff: float = 0.03,
        max_slip_bps: float = 300.0,
    ) -> float: ...


class SqrtImpactSlippage:
    """Square-root market impact — matches v3 JIT engine.py:534-538.

    participation = pos_usd / (adv_hourly)
    slip_bps      = base_spread + impact_coeff × sqrt(participation) × 10000
    """

    def compute_slippage(
        self, pos_usd: float, adv: float,
        base_spread_bps: float = 3.0,
        impact_coeff: float = 0.03,
        max_slip_bps: float = 300.0,
    ) -> float:
        participation = pos_usd / max(adv / 24.0, 1.0)
        slip_bps = base_spread_bps + impact_coeff * np.sqrt(participation) * 10000.0
        return min(float(slip_bps), max_slip_bps)


# Default singleton — cheap to share; stateless.
_DEFAULT_SLIPPAGE = SqrtImpactSlippage()


# Slippage model registry — preserved from pre-M8 v5/sizing.py for Wave G
# callsite migration (simulator.py + paper_engine.py still look up
# models by name). M9+ may narrow this when clamp #6 is the sole consumer.
_SLIPPAGE_MODELS: dict = {
    "sqrt": _DEFAULT_SLIPPAGE,
}


def get_slippage_model(name: str = "sqrt") -> SlippageModel:
    """Look up a slippage model by name. Raises KeyError for unknowns."""
    return _SLIPPAGE_MODELS[name]


def compute_slippage_bps(
    pos_usd: float, adv: float,
    base_spread_bps: float = 3.0,
    impact_coeff: float = 0.03,
    max_slip_bps: float = 300.0,
) -> float:
    """Backward-compat wrapper matching legacy `v5/sizing.py` surface."""
    return _DEFAULT_SLIPPAGE.compute_slippage(
        pos_usd, adv, base_spread_bps, impact_coeff, max_slip_bps,
    )
