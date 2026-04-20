"""M8 — CapitalAllocationPolicy Protocol + SharedPoolPolicy default.

Design §8 hook: ships in M8 to avoid M9 retrofit cost across 5+ freshly-
stabilized clamp files. M9 adds pluggable `PortfolioConfig.capital_
allocation_policy` + (in M10) FixedBudgetPolicy implementation.

Named `CapitalAllocationPolicy` (NOT `AllocationPolicy`) to avoid name
collision with M9 C-1's existing `AllocationPolicy` which handles
signal-order ranking.

`sampling_cadence` load-bearing for AC-Sz9 parity — future dynamic
policies (Sharpe-weighted, vol-target) drift between tick-cadence paper
and bar-cadence backtest unless cadence is explicit. 2023 crypto-prop
incident: 80bps drift on volatile day after clean 1-week shadow replay.
"""
from __future__ import annotations

from typing import Any, Dict, Literal, Protocol, TypedDict, runtime_checkable


class AllocationState(TypedDict):
    """Narrow read surface for `CapitalAllocationPolicy.available_capital`.

    Intentionally minimal — policies should NOT need to peek at individual
    position data. Forward-compat: preserves ability to restrict further
    in M10 multi-tenant work.

    M9 C-9: `market_snapshot` extension — populated by engine at bar_close
    with canonical market_indices keys (BTC_CLOSE_1D, TOTAL2, REGIME_FLAG_1D
    etc.). Enables user-written dynamic allocators (e.g. DynamicRegimeAllocator)
    to read cross-sectional state WITHOUT direct UniverseContext access.
    """

    available_margin: float
    per_strategy_equity: Dict[str, float]
    rolling_pnl_24h: Dict[str, float]
    current_positions_notional: Dict[str, float]
    market_snapshot: Dict[str, float]


@runtime_checkable
class CapitalAllocationPolicy(Protocol):
    """Returns capital available to a strategy at release time.

    `sampling_cadence` declares when `state` was snapshotted:
      - "release": pure function of current state (default for stateless)
      - "bar_close": reads state snapshot at bar close (required for
                     rolling-equity-reading policies to preserve AC-Sz9
                     paper-vs-backtest parity)
      - "tick": paper-only tick-cadence reads (explicitly non-parity;
                documented drift in AC-Sz9 sampling-cadence tests).

    Note on `sampling_cadence` as class attribute: `@runtime_checkable`
    Protocols only declare *methods* — attribute declarations don't
    surface via `hasattr` on the Protocol class. We explicitly define
    `sampling_cadence` with a default here so `hasattr(Protocol, attr)`
    succeeds and concrete implementations can override the value.
    """

    sampling_cadence: Literal["bar_close", "tick", "release"] = "release"

    def available_capital(
        self,
        strategy_id: str,
        state: AllocationState,
        clock_now_ns: int,
    ) -> float: ...


class SharedPoolPolicy:
    """Default — every strategy sees the same `state["available_margin"]`.

    Identity function over the shared pool. Matches current v5 behavior
    exactly (backward-compat guarantee). `release` cadence = pure
    function of current state, so AC-Sz9 parity holds trivially.
    """

    sampling_cadence: Literal["bar_close", "tick", "release"] = "release"

    def available_capital(
        self,
        strategy_id: str,
        state: AllocationState,
        clock_now_ns: int,
    ) -> float:
        return state["available_margin"]

    def to_config(self) -> Dict[str, Any]:
        """Serialize for paper-state JSON. Policies must be config-roundtrippable
        because paper crash-restart re-instantiates from the serialized state;
        serializing Protocol instances directly is pain."""
        return {"policy_name": "SharedPoolPolicy"}

    @classmethod
    def from_config(cls, config: Dict[str, Any]) -> "SharedPoolPolicy":
        """Restore from paper-state JSON. No policy-specific config today."""
        return cls()
