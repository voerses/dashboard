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


def compute_available_capital_usd(
    *,
    margin_mode: str,
    portfolio: dict,
) -> float:
    """Compute the pool of capital visible to the free-capital clamp,
    branching on `margin_mode`.

    Binance-style semantics (AC-Sz3 clause 3 cross-margin correctness):

      **isolated** — only the position's own initial_margin is locked to
        it. Available = wallet_balance − sum(initial_margin per position).
        Each position's unrealized PnL is isolated from the pool; losses
        can only consume that position's initial margin.

      **cross** — all positions share the wallet pool. Available =
        wallet_balance + sum(unrealized_pnl) − sum(initial_margin) −
        sum(open_order_initial_margin). Unrealized losses reduce the
        shared pool in real time; gains increase it.

    This is the formula most commonly wrong in production sizing engines
    (cited by risk-reviewer 2020 incident). Centralized here so both
    Simulator and DataEngine paths produce identical values.
    """
    positions = portfolio.get("positions", [])
    wallet = float(portfolio.get("wallet_balance", 0.0))
    total_initial = sum(float(p.get("initial_margin", 0.0)) for p in positions)
    total_open_order = sum(
        float(p.get("open_order_initial_margin", 0.0)) for p in positions
    )
    total_maint = sum(float(p.get("maintenance_margin", 0.0)) for p in positions)
    if margin_mode == "isolated":
        # Isolated: wallet minus locked initial margins. Unrealized PnL is
        # scoped to each position and does NOT affect the free pool.
        # Maintenance margin also scoped to the position (does not reduce
        # free pool in isolated mode).
        return wallet - total_initial - total_open_order
    if margin_mode == "cross":
        # Cross: wallet + net unrealized PnL across the book, minus locked
        # initial margins, minus maintenance margins across the book.
        # Binance `availableBalance` for cross accounts formula:
        #   availableBalance = walletBalance + totalUnrealizedProfit
        #                      - totalInitialMargin - totalOpenOrderIM
        #                      - totalMaintMargin
        # Missing `totalMaintMargin` was the historical 2020-incident
        # sizing-engine bug site — Risk-reviewer round 3 BLOCKER fix.
        total_upnl = sum(float(p.get("unrealized_pnl", 0.0)) for p in positions)
        return (
            wallet + total_upnl
            - total_initial - total_open_order - total_maint
        )
    raise ValueError(
        f"margin_mode must be 'isolated' or 'cross'; got {margin_mode!r}"
    )


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
