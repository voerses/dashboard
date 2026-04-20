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

__all__ = [
    "SizingIntent",
    "SizingRequest",
    "SqrtImpactSlippage",
    "SlippageModel",
    "compute_slippage_bps",
    "get_slippage_model",
]
