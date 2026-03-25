"""V4 Portfolio Backtest — Position sizing and market impact model.

Matches v3 JIT (engine.py:502-546) with two additions:
  - ADV hard cap (v4 portfolio constraint, not in v3 JIT)
  - max_trade_pct passthrough

Sizing is pluggable via the SizingModel protocol. KellySizing is the default.
New models can be registered in _SIZING_MODELS and selected via StrategySpec.sizing_model.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

import numpy as np

from v4.universe import adv_to_sizing


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class SizingModel(Protocol):
    """Interface for position sizing models."""

    def compute_size(
        self,
        strategy_equity: float,
        rolling_adv: float,
        volatility: float,
        edge: float,
        size_multiplier: float,
        cap_multiplier: float,
        max_trade_pct: float,
        adv_cap_pct: float,
        adv_sizing_enabled: bool,
        adv_sizing_base: float,
        adv_sizing_floor: float,
        edge_minimum: float,
        target_vol: float,
        vol_floor: float,
        spot_max_equity_pct: float,
        leverage: float,
        kelly_mult_override: float,
        kelly_mult_scale: float,
        cap_pct_override: float,
        cap_pct_scale: float,
        kelly_mult_floor: float,
        kelly_mult_range: float,
        cap_pct_floor: float,
        cap_pct_range: float,
        adv_scaling_divisor: float,
    ) -> float: ...


# ---------------------------------------------------------------------------
# KellySizing — extracted from compute_position_size()
# ---------------------------------------------------------------------------

class KellySizing:
    """Kelly criterion position sizing (the original and default model)."""

    def compute_size(
        self,
        strategy_equity: float,
        rolling_adv: float,
        volatility: float,
        edge: float,
        size_multiplier: float,
        cap_multiplier: float,
        max_trade_pct: float,
        adv_cap_pct: float = 0.10,
        adv_sizing_enabled: bool = False,
        adv_sizing_base: float = 100_000_000,
        adv_sizing_floor: float = 0.20,
        edge_minimum: float = 0.10,
        target_vol: float = 0.02,
        vol_floor: float = 0.005,
        spot_max_equity_pct: float = 1.0,
        leverage: float = 1.0,
        kelly_mult_override: float = 0.0,
        kelly_mult_scale: float = 1.0,
        cap_pct_override: float = 0.0,
        cap_pct_scale: float = 1.0,
        kelly_mult_floor: float = 0.15,
        kelly_mult_range: float = 0.35,
        cap_pct_floor: float = 0.02,
        cap_pct_range: float = 0.10,
        adv_scaling_divisor: float = 5.0,
    ) -> float:
        """Compute position size in USD using Kelly criterion."""
        if edge < edge_minimum:
            return 0.0

        # Resolve kelly_mult and cap_pct
        if kelly_mult_override > 0:
            kelly_mult = kelly_mult_override
            if cap_pct_override > 0:
                cap_pct = cap_pct_override
            else:
                _, cap_pct = adv_to_sizing(
                    rolling_adv, kelly_mult_floor, kelly_mult_range,
                    cap_pct_floor, cap_pct_range, adv_scaling_divisor,
                )
                cap_pct *= cap_pct_scale
        else:
            kelly_mult, cap_pct = adv_to_sizing(
                rolling_adv, kelly_mult_floor, kelly_mult_range,
                cap_pct_floor, cap_pct_range, adv_scaling_divisor,
            )
            kelly_mult *= kelly_mult_scale
            if cap_pct_override > 0:
                cap_pct = cap_pct_override
            else:
                cap_pct *= cap_pct_scale

        kelly_frac = kelly_mult * edge * size_multiplier
        vol_adj = target_vol / max(volatility, vol_floor) if volatility > 0.0 else 1.0
        raw = strategy_equity * kelly_frac * vol_adj
        cap = strategy_equity * cap_pct * cap_multiplier
        adv_cap = rolling_adv * adv_cap_pct

        pos_usd = min(raw, cap, adv_cap)
        if max_trade_pct > 0:
            pos_usd = min(pos_usd, strategy_equity * max_trade_pct)
        if adv_sizing_enabled:
            adv_mult = min(1.0, max(adv_sizing_floor, np.sqrt(rolling_adv / max(adv_sizing_base, 1.0))))
            pos_usd *= adv_mult

        # Spot equity cap: on spot (leverage <= 1.0), position cannot exceed equity allocation
        if leverage <= 1.0:
            pos_usd = min(pos_usd, strategy_equity * spot_max_equity_pct)

        return max(pos_usd, 0.0)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_DEFAULT_KELLY = KellySizing()

_SIZING_MODELS: dict[str, SizingModel] = {
    "kelly": _DEFAULT_KELLY,
}


def get_sizing_model(name: str = "kelly") -> SizingModel:
    """Look up a sizing model by name. Raises KeyError for unknown models."""
    return _SIZING_MODELS[name]


# ---------------------------------------------------------------------------
# Backward-compatible function API
# ---------------------------------------------------------------------------

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
    # --- From resolved SizingDefaults ---
    edge_minimum: float = 0.10,
    target_vol: float = 0.02,
    vol_floor: float = 0.005,
    spot_max_equity_pct: float = 1.0,
    leverage: float = 1.0,
    # Kelly/cap overrides (0 = use ADV curve)
    kelly_mult_override: float = 0.0,
    kelly_mult_scale: float = 1.0,
    cap_pct_override: float = 0.0,
    cap_pct_scale: float = 1.0,
    # ADV curve params (passed through when no override)
    kelly_mult_floor: float = 0.15,
    kelly_mult_range: float = 0.35,
    cap_pct_floor: float = 0.02,
    cap_pct_range: float = 0.10,
    adv_scaling_divisor: float = 5.0,
) -> float:
    """Compute position size in USD, matching v3 JIT with ADV cap addition.

    Backward-compatible wrapper that delegates to KellySizing.compute_size().
    """
    return _DEFAULT_KELLY.compute_size(
        strategy_equity=strategy_equity,
        rolling_adv=rolling_adv,
        volatility=volatility,
        edge=edge,
        size_multiplier=size_multiplier,
        cap_multiplier=cap_multiplier,
        max_trade_pct=max_trade_pct,
        adv_cap_pct=adv_cap_pct,
        adv_sizing_enabled=adv_sizing_enabled,
        adv_sizing_base=adv_sizing_base,
        adv_sizing_floor=adv_sizing_floor,
        edge_minimum=edge_minimum,
        target_vol=target_vol,
        vol_floor=vol_floor,
        spot_max_equity_pct=spot_max_equity_pct,
        leverage=leverage,
        kelly_mult_override=kelly_mult_override,
        kelly_mult_scale=kelly_mult_scale,
        cap_pct_override=cap_pct_override,
        cap_pct_scale=cap_pct_scale,
        kelly_mult_floor=kelly_mult_floor,
        kelly_mult_range=kelly_mult_range,
        cap_pct_floor=cap_pct_floor,
        cap_pct_range=cap_pct_range,
        adv_scaling_divisor=adv_scaling_divisor,
    )


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
