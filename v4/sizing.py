"""V4 Portfolio Backtest — Position sizing and market impact model.

Matches v3 JIT (engine.py:502-546) with two additions:
  - ADV hard cap (v4 portfolio constraint, not in v3 JIT)
  - max_trade_pct passthrough
"""
from __future__ import annotations

import numpy as np

from v4.universe import adv_to_sizing


def compute_position_size(
    strategy_equity: float,       # portfolio_equity * strategy_weight
    rolling_adv: float,
    volatility: float,            # atr / close
    edge: float,
    size_multiplier: float,
    cap_multiplier: float,
    max_trade_pct: float,         # from StrategyResult (0 = disabled)
    adv_cap_pct: float = 0.10,   # v4 portfolio constraint
    # ADV sizing parameters
    adv_sizing_enabled: bool = False,
    adv_sizing_base: float = 100_000_000,
    adv_sizing_floor: float = 0.20,
) -> float:
    """Compute position size in USD, matching v3 JIT with ADV cap addition."""
    if edge < 0.10:
        return 0.0
    kelly_mult, cap_pct = adv_to_sizing(rolling_adv)
    kelly_frac = kelly_mult * edge * size_multiplier
    vol_adj = 0.02 / max(volatility, 0.005) if volatility > 0.0 else 1.0
    raw = strategy_equity * kelly_frac * vol_adj
    cap = strategy_equity * cap_pct * cap_multiplier
    adv_cap = rolling_adv * adv_cap_pct

    pos_usd = min(raw, cap, adv_cap)
    if max_trade_pct > 0:
        pos_usd = min(pos_usd, strategy_equity * max_trade_pct)
    if adv_sizing_enabled:
        adv_mult = min(1.0, max(adv_sizing_floor, np.sqrt(rolling_adv / max(adv_sizing_base, 1.0))))
        pos_usd *= adv_mult
    return max(pos_usd, 0.0)


def compute_slippage_bps(
    pos_usd: float,
    adv: float,
    base_spread_bps: float = 3.0,
    impact_coeff: float = 0.03,
    max_slip_bps: float = 300.0,
) -> float:
    """Square-root market impact model (matches v3 JIT engine.py:534-538)."""
    participation = pos_usd / max(adv / 24.0, 1.0)
    slip_bps = base_spread_bps + impact_coeff * np.sqrt(participation) * 10000.0
    return min(slip_bps, max_slip_bps)
