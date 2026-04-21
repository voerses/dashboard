"""M8 — v5 sizing package.

Replaces v4's 9-parameter opaque sizing pipeline with a transparent
2-intent system (FIXED_FRACTION + FIXED_NOTIONAL) plus 6 explicit
engine clamps (ADV cap → concentration → free capital → min size →
liquidation distance → slippage).

Public API:
  - `SizingIntent`                    — enum (FIXED_FRACTION, FIXED_NOTIONAL)
  - `SizingRequest`                   — scalar-only dataclass baked into TokenSignal
  - `run_clamp_pipeline`              — 6-clamp dispatch (Wave C Task 14+)
  - `write_sizing_fill_entry`         — binding-log JSONL writer (Wave D Task 15)
  - `CapitalAllocationPolicy`         — M9-forward-compat Protocol (Wave B Task 7)
  - `SharedPoolPolicy`                — default identity policy (current behavior)
  - `MarketState` + adapters          — data-source abstraction (Wave B)
  - helpers (`vol_target_fraction`, `kelly_fraction`,
             `risk_budget_fraction`, `composite_scaled_fraction`)

Out of scope (deferred):
  - M9: `FixedBudgetPolicy`, `SharpeWeightedPolicy`, per-strategy budget
         enforcement (Protocol hook ships here; non-default policies M9+)
  - M9: funding 8h cadence parity between backtest and paper
"""
from v5.sizing.intents import SizingIntent, SizingRequest
# Slippage back-compat re-exports (v5.sizing was flat before M8 delete;
# legacy tests like test_slippage_fixes still import from the package).
from v5.sizing.slippage import (
    SqrtImpactSlippage,
    SlippageModel,
    compute_slippage_bps,
    get_slippage_model,
)


# M10 B11: real module-level `get_sizing_model` (replaces a dynamic
# globals resolver obfuscation deleted from `v5/simulator.py:41`).
# After B1/B2 the only "sizing model" is the free function
# `compute_fixed_fraction_notional` below — this resolver returns a
# thin adapter that exposes a legacy `.compute_size()` method bound
# to the new helper. M7 conviction tests that monkeypatched
# `sim.get_sizing_model` continue to work.
class _FixedFractionSizingAdapter:
    """Adapter exposing the pre-M8 `compute_size()` shape over
    `compute_fixed_fraction_notional`. Keeps legacy tests alive
    after `v5/sizing_legacy.py` was deleted in M10 B2."""

    def compute_size(
        self, strategy_equity, rolling_adv, edge,
        adv_cap_pct=0.05, edge_minimum=0.10,
        spot_max_equity_pct=1.0, leverage=1.0,
    ):
        return compute_fixed_fraction_notional(
            equity=strategy_equity, adv=rolling_adv, edge=edge,
            leverage=leverage, adv_cap_pct=adv_cap_pct,
            edge_minimum=edge_minimum,
            spot_max_equity_pct=spot_max_equity_pct,
        )


_SIZING_MODELS: dict = {"kelly": _FixedFractionSizingAdapter()}


def get_sizing_model(name: str = "kelly"):
    """Resolve a sizing-model implementation by name.

    Returns a `_FixedFractionSizingAdapter` for back-compat `.compute_size()`
    callers. New code should call `compute_fixed_fraction_notional`
    directly.
    """
    if name not in _SIZING_MODELS:
        raise KeyError(f"Unknown sizing model {name!r}")
    return _SIZING_MODELS[name]


def compute_fixed_fraction_notional(
    *,
    equity: float,
    adv: float,
    edge: float,
    leverage: float,
    adv_cap_pct: float = 0.05,
    edge_minimum: float = 0.10,
    spot_max_equity_pct: float = 1.0,
) -> float:
    """M10 B1 — FIXED_FRACTION notional computation.

    Mathematically equivalent to ``_LegacyKellySizing.compute_size()``:
      * edge < edge_minimum → 0.0 (the strategy isn't confident enough)
      * else → ``min(adv × adv_cap_pct, equity × spot_max_equity_pct)``
        when leverage ≤ 1.0; otherwise just the ADV cap.
    Always non-negative.

    Quant-expert review (2026-04-20, Q3 resolution): the clamp pipeline
    is self-sufficient — this helper makes that self-sufficiency
    explicit + testable via `test_m10_sizing_fixed_fraction_
    equivalence.py` + enables `v5/sizing_legacy.py` to be deleted (B2).
    """
    if edge < edge_minimum:
        return 0.0
    pos_usd = adv * adv_cap_pct
    if leverage <= 1.0:
        pos_usd = min(pos_usd, equity * spot_max_equity_pct)
    return max(pos_usd, 0.0)


__all__ = [
    "SizingIntent",
    "SizingRequest",
    "SqrtImpactSlippage",
    "SlippageModel",
    "compute_slippage_bps",
    "get_slippage_model",
    "get_sizing_model",
    "compute_fixed_fraction_notional",
]
