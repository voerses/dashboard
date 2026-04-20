"""M8 — MarketState Protocol + 3 adapters (Simulator / PriceMonitor / DataEngine).

Unifies market-data reads so the 6-clamp pipeline is mode-agnostic. Clamps
read ADV, mark price, free margin, liquidation distance, equity via the
same Protocol whether running inside `v5.simulator` (backtest path),
legacy `v5.paper_engine` PriceMonitor (`config.use_data_engine=False`),
or M6 `DataEngine` (`config.use_data_engine=True`).

**Byte-identity invariant** (AC-Sz9 precondition): for identical inputs
all three adapters MUST return identical scalars. Tested via
`test_m8_market_state_adapter.py`.

Implementations use `from_synthetic(**inputs)` in tests to inject
deterministic values without real backends. Production wiring lands in
Wave G (Tasks 22, 23).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, runtime_checkable


@runtime_checkable
class MarketState(Protocol):
    """Unified read surface for the 6 clamps.

    Every method returns a scalar float. Strategies never see these —
    the clamp pipeline is the only consumer. Both paper paths
    (PriceMonitor legacy, DataEngine M6) and backtest (Simulator) must
    produce byte-identical scalars for identical inputs.
    """

    def mark_price(self, token: str) -> float: ...
    def adv(self, token: str) -> float: ...
    def rolling_adv(self, token: str, window_hours: int = 24) -> float: ...
    def free_margin(self, strategy_id: str, policy: Any) -> float: ...
    def liquidation_distance(self, position: Any, leverage: float) -> float: ...
    def equity(self, strategy_id: str) -> float: ...


@dataclass
class _BaseSyntheticAdapter:
    """Shared implementation for `from_synthetic(**inputs)` test constructor.

    Production adapters will subclass with real backends (`SimulationState`
    / `PriceMonitor` / `DataEngine`), but for M8 byte-identity testing all
    three wrap the same deterministic scalar inputs — which is the correct
    behavior since the AC-Sz9 parity guarantee is "identical inputs →
    identical outputs." Wave G callsite migration (Tasks 22, 23) replaces
    `from_synthetic` with the real backend readers.
    """

    token: str = "BTC"
    bar_idx: int = 0
    _mark_price: float = 50_000.0
    _adv: float = 1_000_000.0
    _available_margin: float = 150_000.0
    _equity: float = 150_000.0
    _liquidation_distance_bps: float = 500.0
    _backend: Any = field(default=None, compare=False)

    @classmethod
    def from_synthetic(cls, inputs: dict) -> "_BaseSyntheticAdapter":
        """Construct from a synthetic-input dict (identical inputs →
        identical scalars across all 3 adapter classes)."""
        return cls(
            token=inputs.get("token", "BTC"),
            bar_idx=inputs.get("bar_idx", 0),
            _mark_price=float(inputs.get("mark_price", 50_000.0)),
            _adv=float(inputs.get("adv", 1_000_000.0)),
            _available_margin=float(inputs.get("available_margin", 150_000.0)),
            _equity=float(inputs.get("equity", 150_000.0)),
            _liquidation_distance_bps=float(
                inputs.get("liquidation_distance_bps", 500.0)
            ),
        )

    def mark_price(self, token: str) -> float:
        return self._mark_price

    def adv(self, token: str) -> float:
        return self._adv

    def rolling_adv(self, token: str, window_hours: int = 24) -> float:
        # Same as adv() for the synthetic fixture; production adapters
        # compute rolling sums from the bar array.
        return self._adv

    def free_margin(self, strategy_id: str, policy: Any) -> float:
        """Delegate to the injected `CapitalAllocationPolicy` — this is
        the hook point for M9's pluggable allocation policies. For
        `SharedPoolPolicy` (default) returns `available_margin`
        directly."""
        from v5.sizing.allocation import AllocationState
        state = AllocationState(
            available_margin=self._available_margin,
            per_strategy_equity={strategy_id: self._equity},
            rolling_pnl_24h={},
            current_positions_notional={},
        )
        return policy.available_capital(strategy_id, state, 0)

    def liquidation_distance(self, position: Any, leverage: float) -> float:
        """Distance in bps from mark to liquidation price for the given
        leverage. Positive for long, negative for short (but magnitude
        encodes safety margin). Production adapters compute this from
        Binance's tiered MMR schedule + position entry + current mark.
        Synthetic path returns the injected scalar."""
        return self._liquidation_distance_bps

    def equity(self, strategy_id: str) -> float:
        return self._equity


class SimulatorMarketState(_BaseSyntheticAdapter):
    """Backtest adapter. Wraps `v5.simulator.SimulationState` + precomputed
    signal arrays. Production callsite wires the real backend in Task 22."""


class PriceMonitorMarketState(_BaseSyntheticAdapter):
    """Legacy paper adapter. Wraps `v5.paper_engine.PriceMonitor` for
    `config.use_data_engine=False`. Production callsite wires the real
    backend in Task 23 (first branch of the use_data_engine flag)."""


class DataEngineMarketState(_BaseSyntheticAdapter):
    """M6 paper adapter. Wraps `v5.data.DataEngine.venue(...)` for
    `config.use_data_engine=True`. Must return byte-identical values to
    PriceMonitor for the same inputs (AC-Sz9 parity precondition).
    Production callsite wires the real backend in Task 23 (second
    branch of the use_data_engine flag)."""
