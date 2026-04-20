"""Comprehensive acceptance tests for the V5 Portfolio Backtest Engine.

Tests cover:
  - Unit tests: config defaults, position management, sizing,
    unified index building, SimulationState equity/capital, RejectionStats
  - Integration tests: single-token simulation, portfolio constraints, concentration
    limits, free capital exhaustion, combined positions, funding accrual, ADV cap,
    determinism, capital model
  - Edge cases: empty signals, single bar, NaN ATR

All tests use synthetic data -- no real market data required.
"""
from __future__ import annotations

import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root))

import numpy as np
import pandas as pd
import pytest

from v5.config import PortfolioConfig, StrategySpec
from v5.position import Position, ClosedTrade, PositionManager
# M8 legacy shim (test-only pre-M8 regression gate preserved until M9).
from v5.sizing.slippage import compute_slippage_bps
import v5.sizing_legacy as _legacy_sizing_tv5
compute_position_size = _legacy_sizing_tv5._legacy_compute_position_size
from v5.signals import TokenBarArrays
from v5.simulator import (
    SimulationState,
    RejectionStats,
    build_unified_index,
    simulate_portfolio,
    _process_exits,
    _process_entries,
)


# ---------------------------------------------------------------------------
# Helpers: build synthetic TokenBarArrays
# ---------------------------------------------------------------------------

def _make_timestamps(n: int, start: str = "2024-01-01") -> np.ndarray:
    """Create n hourly timestamps starting from `start`."""
    return pd.date_range(start, periods=n, freq="1h").values


def _make_token_signals(
    token: str = "BTC",
    strategy_id: str = "s30",
    n_bars: int = 500,
    entry_bar: int | None = None,
    direction: int = 1,
    close_price: float = 100.0,
    atr_val: float = 2.0,
    adv: float = 5_000_000.0,
    edge: float = 0.35,
    stop_mult: float = 2.0,
    trail_mult: float = 3.0,
    target_mult: float = 5.0,
    no_stop_bars: int = 6,
    min_hold: int = 6,
    max_hold: int = 720,
    leverage: float = 1.0,
    is_perp_primary: bool = False,
    funding_rate: float = 0.0,
    is_combined: bool = False,
    secondary_entry_bar: int | None = None,
    secondary_direction: int = -1,
    secondary_leverage: float = 1.0,
    capital_split: float = 0.5,
    start_ts: str = "2024-01-01",
    close_array: np.ndarray | None = None,
    high_array: np.ndarray | None = None,
    low_array: np.ndarray | None = None,
    atr_array: np.ndarray | None = None,
    regime_array: np.ndarray | None = None,
    rsi_array: np.ndarray | None = None,
    rsi_exit_level: float = 999.0,
    convex_exit: bool = False,
    mean_target_vals: np.ndarray | None = None,
    entry_mask_array: np.ndarray | None = None,
    perp_close: np.ndarray | None = None,
    perp_high: np.ndarray | None = None,
    perp_low: np.ndarray | None = None,
    perp_atr: np.ndarray | None = None,
    perp_rolling_adv: np.ndarray | None = None,
    perp_funding_1h: np.ndarray | None = None,
    sec_stop_mult: float | None = None,
    sec_trail_mult: float | None = None,
    sec_target_mult: float | None = None,
    sec_no_stop_bars: int | None = None,
    sec_min_hold: int | None = None,
    sec_max_hold: int | None = None,
) -> TokenBarArrays:
    """Build a synthetic TokenBarArrays for testing."""
    timestamps = _make_timestamps(n_bars, start_ts)

    if close_array is None:
        close_array = np.full(n_bars, close_price, dtype=np.float64)
    if high_array is None:
        high_array = close_array + atr_val * 0.5
    if low_array is None:
        low_array = close_array - atr_val * 0.5
    if atr_array is None:
        atr_array = np.full(n_bars, atr_val, dtype=np.float64)
    if regime_array is None:
        regime_array = np.zeros(n_bars, dtype=np.int8)

    if entry_mask_array is None:
        entry_mask_array = np.zeros(n_bars, dtype=bool)
        if entry_bar is not None:
            entry_mask_array[entry_bar] = True

    dir_array = np.full(n_bars, direction, dtype=np.int8)
    adv_array = np.full(n_bars, adv, dtype=np.float64)
    funding_array = np.full(n_bars, funding_rate, dtype=np.float64)

    lev_array = np.full(n_bars, leverage, dtype=np.float64)
    stop_arr = np.full(n_bars, stop_mult, dtype=np.float64)
    trail_arr = np.full(n_bars, trail_mult, dtype=np.float64)

    # Secondary entry mask for combined
    sec_entry = None
    sec_dir = None
    if is_combined:
        sec_entry = np.zeros(n_bars, dtype=bool)
        if secondary_entry_bar is not None:
            sec_entry[secondary_entry_bar] = True
        elif entry_bar is not None:
            sec_entry[entry_bar] = True  # default: same bar
        sec_dir = np.full(n_bars, secondary_direction, dtype=np.int8)

    return TokenBarArrays(
        token=token,
        strategy_id=strategy_id,
        n_bars=n_bars,
        timestamps=timestamps,
        entry_mask=entry_mask_array,
        direction=dir_array,
        close=close_array,
        high=high_array,
        low=low_array,
        atr=atr_array,
        rolling_adv=adv_array,
        regime=regime_array,
        funding_1h=funding_array,
        stop_mult=stop_arr,
        trail_mult=trail_arr,
        target_mult=target_mult,
        no_stop_bars=no_stop_bars,
        min_hold=min_hold,
        max_hold=max_hold,
        edge=edge,
        leverage=lev_array,
        convex_exit=convex_exit,
        rsi=rsi_array,
        rsi_exit_level=rsi_exit_level,
        mean_target_vals=mean_target_vals,
        is_combined=is_combined,
        secondary_entry_mask=sec_entry,
        secondary_direction=sec_dir,
        secondary_leverage=secondary_leverage,
        capital_split=capital_split,
        is_perp_primary=is_perp_primary,
        is_perp_secondary=is_combined and not is_perp_primary,
        perp_close=perp_close,
        perp_high=perp_high,
        perp_low=perp_low,
        perp_atr=perp_atr,
        perp_rolling_adv=perp_rolling_adv,
        perp_funding_1h=perp_funding_1h,
        sec_stop_mult=sec_stop_mult,
        sec_trail_mult=sec_trail_mult,
        sec_target_mult=sec_target_mult,
        sec_no_stop_bars=sec_no_stop_bars,
        sec_min_hold=sec_min_hold,
        sec_max_hold=sec_max_hold,
    )


def _default_config(**overrides) -> PortfolioConfig:
    """Build a PortfolioConfig with test-friendly defaults."""
    defaults = dict(
        capital=200_000.0,
        max_portfolio_positions=40,
        concentration_limit=0.10,
        adv_cap_pct=0.10,
        min_position_usd=200.0,
        exchange="binance",
        base_spread_bps=3.0,
        impact_coeff=0.03,
        seed=42,
    )
    defaults.update(overrides)
    return PortfolioConfig(**defaults)


# ===================================================================
# 1. config.py: PortfolioConfig and StrategySpec defaults
# ===================================================================

class TestConfigDefaults:
    """Test 1: PortfolioConfig and StrategySpec defaults."""

    def test_portfolio_config_capital_default(self):
        cfg = PortfolioConfig()
        assert cfg.capital == 200_000

    def test_portfolio_config_max_positions_default(self):
        cfg = PortfolioConfig()
        assert cfg.max_portfolio_positions == 40

    def test_portfolio_config_concentration_limit_default(self):
        cfg = PortfolioConfig()
        assert cfg.concentration_limit == pytest.approx(0.10)

    def test_portfolio_config_adv_cap_default(self):
        cfg = PortfolioConfig()
        assert cfg.adv_cap_pct == pytest.approx(0.05)

    def test_portfolio_config_min_position_usd_default(self):
        cfg = PortfolioConfig()
        assert cfg.min_position_usd == pytest.approx(200.0)

    def test_portfolio_config_exchange_default(self):
        cfg = PortfolioConfig()
        assert cfg.exchange == "binance"

    def test_portfolio_config_seed_default(self):
        cfg = PortfolioConfig()
        assert cfg.seed == 42

    def test_portfolio_config_spread_bps_default(self):
        cfg = PortfolioConfig()
        assert cfg.base_spread_bps == pytest.approx(3.0)

    def test_portfolio_config_impact_coeff_default(self):
        cfg = PortfolioConfig()
        assert cfg.impact_coeff == pytest.approx(0.03)

    def test_strategy_spec_defaults(self):
        spec = StrategySpec(strategy_id="s30")
        assert spec.strategy_id == "s30"
        assert spec.weight == pytest.approx(1.0)
        assert spec.max_positions == 15
        assert spec.market == "combined"

    def test_strategy_spec_custom_values(self):
        spec = StrategySpec(strategy_id="s32", weight=0.5, max_positions=10, market="perp")
        assert spec.weight == pytest.approx(0.5)
        assert spec.max_positions == 10
        assert spec.market == "perp"


# ===================================================================
# 2. position.py: PositionManager operations
# ===================================================================

def _make_position(
    token: str = "BTC",
    strategy_id: str = "s30",
    margin: float = 1000.0,
    direction: int = 1,
    entry_bar: int = 0,
    position_id: str | None = None,
    linked_position_id: str | None = None,
) -> Position:
    """Build a minimal Position for unit tests."""
    pid = position_id or f"{token}:{strategy_id}:{entry_bar}:primary"
    return Position(
        position_id=pid,
        token=token,
        strategy_id=strategy_id,
        leg="primary",
        entry_bar=entry_bar,
        entry_price=100.0,
        direction=direction,
        quantity=direction * margin / 100.0,
        margin_usd=margin,
        leverage=1.0,
        is_perp=False,
        fee_rate=0.001,
        stop_mult=2.0,
        trail_mult=3.0,
        target_mult=5.0,
        no_stop_bars=6,
        min_hold=6,
        max_hold=720,
        stop_price=90.0 if direction == 1 else 110.0,
        highest=100.0,
        lowest=100.0,
        initial_risk=4.0,
        linked_position_id=linked_position_id,
    )


class TestPositionManager:
    """Test 2: PositionManager open/close/query operations."""

    def test_open_position_adds_to_list(self):
        pm = PositionManager()
        pos = _make_position()
        pm.open_position(pos)
        assert pm.total_open() == 1

    def test_close_position_removes_from_open(self):
        pm = PositionManager()
        pos = _make_position()
        pm.open_position(pos)
        pm.close_position(pos, exit_bar=10, exit_price=105.0,
                          pnl=50.0, funding_cost=0.0, entry_fee=1.0,
                          exit_fee=1.0, exit_reason="target")
        assert pm.total_open() == 0
        assert len(pm.closed_trades) == 1

    def test_closed_trade_fields(self):
        pm = PositionManager()
        pos = _make_position(entry_bar=5)
        pm.open_position(pos)
        trade = pm.close_position(pos, exit_bar=15, exit_price=110.0,
                                  pnl=100.0, funding_cost=2.0, entry_fee=1.5,
                                  exit_fee=1.5, exit_reason="stop")
        assert isinstance(trade, ClosedTrade)
        assert trade.hold_bars == 10
        assert trade.exit_reason == "stop"
        assert trade.pnl == pytest.approx(100.0)
        assert trade.funding_cost == pytest.approx(2.0)

    def test_total_margin_for_token(self):
        pm = PositionManager()
        pm.open_position(_make_position(token="BTC", margin=1000.0,
                                        position_id="BTC:s30:0:primary"))
        pm.open_position(_make_position(token="BTC", margin=2000.0,
                                        position_id="BTC:s32:0:primary",
                                        strategy_id="s32"))
        pm.open_position(_make_position(token="ETH", margin=500.0,
                                        position_id="ETH:s30:0:primary"))
        assert pm.total_margin_for_token("BTC") == pytest.approx(3000.0)
        assert pm.total_margin_for_token("ETH") == pytest.approx(500.0)
        assert pm.total_margin_for_token("SOL") == pytest.approx(0.0)

    def test_count_for_strategy(self):
        pm = PositionManager()
        pm.open_position(_make_position(strategy_id="s30",
                                        position_id="BTC:s30:0:primary"))
        pm.open_position(_make_position(strategy_id="s30", token="ETH",
                                        position_id="ETH:s30:0:primary"))
        pm.open_position(_make_position(strategy_id="s32",
                                        position_id="BTC:s32:0:primary"))
        assert pm.count_for_strategy("s30") == 2
        assert pm.count_for_strategy("s32") == 1
        assert pm.count_for_strategy("s99") == 0

    def test_total_locked_margin(self):
        pm = PositionManager()
        pm.open_position(_make_position(margin=1000.0,
                                        position_id="BTC:s30:0:primary"))
        pm.open_position(_make_position(margin=2000.0, token="ETH",
                                        position_id="ETH:s30:0:primary"))
        assert pm.total_locked_margin() == pytest.approx(3000.0)

    def test_get_linked_found(self):
        pm = PositionManager()
        pos = _make_position(position_id="BTC:s30:100:secondary")
        pm.open_position(pos)
        found = pm.get_linked("BTC:s30:100:secondary")
        assert found is pos

    def test_get_linked_not_found(self):
        pm = PositionManager()
        assert pm.get_linked("nonexistent") is None

    def test_find_open_for_token_strategy(self):
        pm = PositionManager()
        p1 = _make_position(token="BTC", strategy_id="s30",
                            position_id="BTC:s30:0:primary")
        p2 = _make_position(token="BTC", strategy_id="s32",
                            position_id="BTC:s32:0:primary")
        pm.open_position(p1)
        pm.open_position(p2)
        found = pm.find_open_for_token_strategy("BTC", "s30")
        assert len(found) == 1
        assert found[0] is p1
        assert len(pm.find_open_for_token_strategy("ETH", "s30")) == 0


# ===================================================================
# 3. sizing.py: compute_position_size and compute_slippage_bps
# ===================================================================

class TestComputePositionSize:
    """Test 3: Position sizing with ADV cap, edge minimum, zero edge."""

    def test_basic_sizing_positive(self):
        result = compute_position_size(
            strategy_equity=200_000.0,
            rolling_adv=5_000_000.0,
            edge=0.35,
            adv_cap_pct=0.10,
        )
        assert result > 0.0

    def test_adv_cap_binding(self):
        """When ADV is tiny, the ADV cap should bind and limit position size."""
        tiny_adv = 1_000.0
        result = compute_position_size(
            strategy_equity=200_000.0,
            rolling_adv=tiny_adv,
            edge=0.35,
            adv_cap_pct=0.10,
        )
        assert result <= tiny_adv * 0.10 + 0.01

    def test_zero_edge_returns_zero(self):
        result = compute_position_size(
            strategy_equity=200_000.0,
            rolling_adv=5_000_000.0,
            edge=0.0,
            adv_cap_pct=0.10,
        )
        assert result == pytest.approx(0.0)

    def test_negative_edge_returns_zero(self):
        result = compute_position_size(
            strategy_equity=200_000.0,
            rolling_adv=5_000_000.0,
            edge=-0.05,
            adv_cap_pct=0.10,
        )
        assert result == pytest.approx(0.0)


class TestComputeSlippageBps:
    """Test 3 (continued): slippage model."""

    def test_basic_slippage_positive(self):
        slip = compute_slippage_bps(
            pos_usd=10_000.0,
            adv=5_000_000.0,
            base_spread_bps=3.0,
            impact_coeff=0.03,
        )
        assert slip > 3.0  # must exceed base spread

    def test_slippage_increases_with_position_size(self):
        small = compute_slippage_bps(1_000.0, 5_000_000.0)
        large = compute_slippage_bps(100_000.0, 5_000_000.0)
        assert large > small

    def test_slippage_capped_at_100bps(self):
        huge = compute_slippage_bps(
            pos_usd=1_000_000.0,
            adv=100.0,  # extremely low ADV -> huge participation
            base_spread_bps=3.0,
            impact_coeff=0.03,
        )
        assert huge == pytest.approx(300.0)

    def test_slippage_minimum_is_base_spread(self):
        tiny = compute_slippage_bps(
            pos_usd=0.01,
            adv=1_000_000_000.0,
            base_spread_bps=3.0,
            impact_coeff=0.03,
        )
        assert tiny >= 3.0 - 0.001


# ===================================================================
# 5. Unified index building
# ===================================================================

class TestBuildUnifiedIndex:
    """Test 5: build_unified_index with overlapping and non-overlapping timestamps."""

    def test_identical_timestamps(self):
        sig_a = _make_token_signals(token="A", n_bars=100, start_ts="2024-01-01")
        sig_b = _make_token_signals(token="B", n_bars=100, start_ts="2024-01-01")
        all_signals = {"s30": {"A": sig_a, "B": sig_b}}
        unified, bar_maps = build_unified_index(all_signals)
        assert len(unified) == 100
        np.testing.assert_array_equal(bar_maps["A"], np.arange(100))
        np.testing.assert_array_equal(bar_maps["B"], np.arange(100))

    def test_non_overlapping_timestamps(self):
        sig_a = _make_token_signals(token="A", n_bars=50, start_ts="2024-01-01")
        sig_b = _make_token_signals(token="B", n_bars=50, start_ts="2024-01-03 02:00")
        all_signals = {"s30": {"A": sig_a, "B": sig_b}}
        unified, bar_maps = build_unified_index(all_signals)
        assert len(unified) == 100  # 50 + 50, no overlap
        assert (bar_maps["A"][:50] >= 0).all()
        assert (bar_maps["A"][50:] == -1).all()
        assert (bar_maps["B"][:50] == -1).all()
        assert (bar_maps["B"][50:] >= 0).all()

    def test_partial_overlap(self):
        sig_a = _make_token_signals(token="A", n_bars=100, start_ts="2024-01-01")
        sig_b = _make_token_signals(token="B", n_bars=100, start_ts="2024-01-03")
        all_signals = {"s30": {"A": sig_a, "B": sig_b}}
        unified, bar_maps = build_unified_index(all_signals)
        assert len(unified) == 148
        assert (bar_maps["A"][:100] >= 0).all()
        assert (bar_maps["A"][100:] == -1).all()

    def test_same_token_multiple_strategies(self):
        """Same token under two strategies uses the longer series."""
        sig_a1 = _make_token_signals(token="A", strategy_id="s30",
                                     n_bars=100, start_ts="2024-01-01")
        sig_a2 = _make_token_signals(token="A", strategy_id="s32",
                                     n_bars=80, start_ts="2024-01-01")
        all_signals = {"s30": {"A": sig_a1}, "s32": {"A": sig_a2}}
        unified, bar_maps = build_unified_index(all_signals)
        assert len(unified) == 100
        assert "A" in bar_maps


# ===================================================================
# 6. SimulationState equity and free_capital
# ===================================================================

class TestSimulationState:
    """Test 6: SimulationState equity and free capital calculations."""

    def test_initial_equity(self):
        state = SimulationState(initial_capital=200_000.0)
        assert state.portfolio_equity == pytest.approx(200_000.0)

    def test_equity_after_realized_pnl(self):
        state = SimulationState(initial_capital=200_000.0)
        state.realized_pnl = 5_000.0
        assert state.portfolio_equity == pytest.approx(205_000.0)

    def test_equity_after_fees(self):
        state = SimulationState(initial_capital=200_000.0)
        state.total_fees = 1_000.0
        assert state.portfolio_equity == pytest.approx(199_000.0)

    def test_equity_after_funding(self):
        state = SimulationState(initial_capital=200_000.0)
        state.total_funding = 500.0
        assert state.portfolio_equity == pytest.approx(199_500.0)

    def test_equity_formula(self):
        """Equity = initial + realized - fees - funding."""
        state = SimulationState(initial_capital=200_000.0)
        state.realized_pnl = 10_000.0
        state.total_fees = 2_000.0
        state.total_funding = 500.0
        expected = 200_000.0 + 10_000.0 - 2_000.0 - 500.0
        assert state.portfolio_equity == pytest.approx(expected)

    def test_free_capital_no_positions(self):
        state = SimulationState(initial_capital=200_000.0)
        assert state.free_capital == pytest.approx(200_000.0)

    def test_free_capital_with_positions(self):
        state = SimulationState(initial_capital=200_000.0)
        pos = _make_position(margin=50_000.0)
        state.position_manager.open_position(pos)
        assert state.free_capital == pytest.approx(150_000.0)


# ===================================================================
# 7. RejectionStats
# ===================================================================

class TestRejectionStats:
    """Test 7: RejectionStats counting and to_dict."""

    def test_initial_counts_zero(self):
        rs = RejectionStats()
        assert rs.total() == 0

    def test_to_dict_contains_total(self):
        rs = RejectionStats()
        rs.portfolio_limit = 1
        rs.capital = 2
        d = rs.to_dict()
        assert "portfolio_limit" in d
        assert "total" in d


# ===================================================================
# 8. Single-token single-strategy simulation: entry, exit by
#    stop / target / max_hold
# ===================================================================

class TestSingleTokenSimulation:
    """Test 8: Single-token sim with deterministic price paths."""

    def _run_single_token(self, close_array, high_array=None, low_array=None,
                          entry_bar=10, max_hold=720, target_mult=5.0,
                          stop_mult=2.0, atr_val=2.0, **extra):
        """Helper: run simulate_portfolio with one token, one entry."""
        n = len(close_array)
        if high_array is None:
            high_array = close_array + atr_val * 0.3
        if low_array is None:
            low_array = close_array - atr_val * 0.3

        sig = _make_token_signals(
            n_bars=n, entry_bar=entry_bar,
            close_array=close_array,
            high_array=high_array,
            low_array=low_array,
            atr_val=atr_val,
            stop_mult=stop_mult,
            target_mult=target_mult,
            max_hold=max_hold,
            **extra,
        )
        config = _default_config()
        spec = StrategySpec(strategy_id="s30", weight=1.0, max_positions=15)
        all_signals = {"s30": {"BTC": sig}}
        specs = {"s30": spec}
        state = simulate_portfolio(all_signals, specs, config)
        return state

    def test_entry_creates_trade(self):
        n = 200
        close = np.full(n, 100.0, dtype=np.float64)
        state = self._run_single_token(close, entry_bar=10)
        trades = state.position_manager.closed_trades
        assert len(trades) >= 1

    def test_exit_by_max_hold(self):
        n = 300
        close = np.full(n, 100.0, dtype=np.float64)
        high = np.full(n, 100.01, dtype=np.float64)
        low = np.full(n, 99.99, dtype=np.float64)
        state = self._run_single_token(close, high_array=high, low_array=low,
                                       entry_bar=10, max_hold=50,
                                       target_mult=9999.0, stop_mult=50.0,
                                       atr_val=2.0)
        trades = state.position_manager.closed_trades
        max_hold_trades = [t for t in trades if t.exit_reason == "max_hold"]
        assert len(max_hold_trades) >= 1
        for t in max_hold_trades:
            assert t.hold_bars >= 50

    def test_exit_by_stop(self):
        n = 200
        close = np.full(n, 100.0, dtype=np.float64)
        low = np.full(n, 100.0, dtype=np.float64)
        high = np.full(n, 100.0, dtype=np.float64)
        for i in range(20, n):
            close[i] = 90.0
            low[i] = 85.0
            high[i] = 91.0

        state = self._run_single_token(close, high_array=high, low_array=low,
                                       entry_bar=10, stop_mult=2.0, atr_val=2.0)
        trades = state.position_manager.closed_trades
        stop_trades = [t for t in trades if t.exit_reason == "stop"]
        assert len(stop_trades) >= 1

    def test_exit_by_target(self):
        n = 200
        close = np.full(n, 100.0, dtype=np.float64)
        high = np.full(n, 100.0, dtype=np.float64)
        low = np.full(n, 100.0, dtype=np.float64)
        for i in range(20, n):
            close[i] = 115.0
            high[i] = 116.0
            low[i] = 114.0

        state = self._run_single_token(close, high_array=high, low_array=low,
                                       entry_bar=10, target_mult=5.0, atr_val=2.0)
        trades = state.position_manager.closed_trades
        target_trades = [t for t in trades if t.exit_reason == "target"]
        assert len(target_trades) >= 1


# ===================================================================
# 9. Portfolio constraints: position limits blocking new entries
# ===================================================================

class TestPortfolioPositionLimits:
    """Test 9: Portfolio and per-strategy position limits."""

    def test_portfolio_limit_blocks_entry(self):
        """Once max_portfolio_positions is reached, new entries are rejected."""
        n = 500
        config = _default_config(max_portfolio_positions=2)

        sigs = {}
        for tok in ["A", "B", "C"]:
            sigs[tok] = _make_token_signals(
                token=tok, n_bars=n, entry_bar=10,
                max_hold=400,
                close_price=100.0, edge=0.35,
            )
        all_signals = {"s30": sigs}
        spec = StrategySpec(strategy_id="s30", weight=1.0, max_positions=15)
        state = simulate_portfolio(all_signals, {"s30": spec}, config)
        assert state.rejections.portfolio_limit >= 1

    def test_strategy_limit_blocks_entry(self):
        """Per-strategy limit of 1 blocks second entry."""
        n = 500
        config = _default_config(max_portfolio_positions=40)

        sigs = {}
        for tok in ["A", "B"]:
            sigs[tok] = _make_token_signals(
                token=tok, n_bars=n, entry_bar=10,
                max_hold=400, close_price=100.0, edge=0.35,
            )
        all_signals = {"s30": sigs}
        spec = StrategySpec(strategy_id="s30", weight=1.0, max_positions=1)
        state = simulate_portfolio(all_signals, {"s30": spec}, config)
        assert state.rejections.strategy_limit >= 1


# ===================================================================
# 10. Concentration limit
# ===================================================================

class TestConcentrationLimit:
    """Test 10: Token exposure blocked when above concentration limit."""

    def test_concentration_blocks_second_entry_same_token(self):
        """Two strategies trying to enter same token exceeds concentration."""
        n = 500
        config = _default_config(concentration_limit=0.005)  # very tight: 0.5% of equity

        sig1 = _make_token_signals(token="BTC", strategy_id="s30",
                                   n_bars=n, entry_bar=10, close_price=100.0,
                                   edge=0.35, max_hold=400)
        sig2 = _make_token_signals(token="BTC", strategy_id="s32",
                                   n_bars=n, entry_bar=10, close_price=100.0,
                                   edge=0.35, max_hold=400)
        all_signals = {"s30": {"BTC": sig1}, "s32": {"BTC": sig2}}
        specs = {
            "s30": StrategySpec(strategy_id="s30", weight=0.5, max_positions=15),
            "s32": StrategySpec(strategy_id="s32", weight=0.5, max_positions=15),
        }
        state = simulate_portfolio(all_signals, specs, config)
        assert state.rejections.concentration >= 1


# ===================================================================
# 12. Combined positions: atomic entry, linked exit
# ===================================================================

class TestCombinedPositions:
    """Test 12: Combined (spot+perp) positions enter/exit atomically."""

    def test_combined_entry_creates_two_positions(self):
        n = 200
        perp_close = np.full(n, 100.5, dtype=np.float64)
        perp_high = np.full(n, 101.0, dtype=np.float64)
        perp_low = np.full(n, 100.0, dtype=np.float64)
        perp_atr = np.full(n, 2.0, dtype=np.float64)
        perp_adv = np.full(n, 5_000_000.0, dtype=np.float64)
        perp_funding = np.full(n, 0.0001, dtype=np.float64)

        sig = _make_token_signals(
            n_bars=n, entry_bar=10,
            is_combined=True,
            secondary_entry_bar=10,
            secondary_direction=-1,
            secondary_leverage=1.0,
            capital_split=0.5,
            is_perp_primary=False,
            perp_close=perp_close,
            perp_high=perp_high,
            perp_low=perp_low,
            perp_atr=perp_atr,
            perp_rolling_adv=perp_adv,
            perp_funding_1h=perp_funding,
            max_hold=100,
        )
        config = _default_config()
        spec = StrategySpec(strategy_id="s30", weight=1.0, max_positions=15, market="combined")
        all_signals = {"s30": {"BTC": sig}}
        state = simulate_portfolio(all_signals, {"s30": spec}, config)
        trades = state.position_manager.closed_trades

        assert len(trades) == 2
        legs = {t.leg for t in trades}
        assert "primary" in legs
        assert "secondary" in legs

    def test_combined_linked_exit(self):
        """When one leg exits, the linked leg exits too."""
        n = 200
        close = np.full(n, 100.0, dtype=np.float64)
        high = np.full(n, 101.0, dtype=np.float64)
        low = np.full(n, 99.0, dtype=np.float64)
        perp_close = np.full(n, 100.5, dtype=np.float64)
        perp_high = np.full(n, 101.5, dtype=np.float64)
        perp_low = np.full(n, 99.5, dtype=np.float64)
        perp_atr = np.full(n, 2.0, dtype=np.float64)
        perp_adv = np.full(n, 5_000_000.0, dtype=np.float64)
        perp_funding = np.zeros(n, dtype=np.float64)

        sig = _make_token_signals(
            n_bars=n, entry_bar=10,
            close_array=close, high_array=high, low_array=low,
            is_combined=True,
            secondary_entry_bar=10,
            secondary_direction=-1,
            capital_split=0.5,
            is_perp_primary=False,
            perp_close=perp_close, perp_high=perp_high,
            perp_low=perp_low, perp_atr=perp_atr,
            perp_rolling_adv=perp_adv, perp_funding_1h=perp_funding,
            max_hold=30,
        )
        config = _default_config()
        spec = StrategySpec(strategy_id="s30", weight=1.0, max_positions=15, market="combined")
        state = simulate_portfolio({"s30": {"BTC": sig}}, {"s30": spec}, config)
        trades = state.position_manager.closed_trades
        assert len(trades) == 2
        exit_bars = [t.exit_bar for t in trades]
        assert exit_bars[0] == exit_bars[1]


# ===================================================================
# 13. Funding accrual for perp positions
# ===================================================================

class TestFundingAccrual:
    """Test 13: Perp positions accrue funding correctly."""

    def test_perp_funding_accrues(self):
        n = 200
        funding_rate = 0.0001
        sig = _make_token_signals(
            n_bars=n, entry_bar=10,
            is_perp_primary=True,
            funding_rate=funding_rate,
            max_hold=50,
            close_price=100.0,
            leverage=1.0,
        )
        config = _default_config()
        spec = StrategySpec(strategy_id="s30", weight=1.0, max_positions=15)
        state = simulate_portfolio({"s30": {"BTC": sig}}, {"s30": spec}, config)
        assert state.total_funding > 0.0

    def test_spot_no_funding(self):
        n = 200
        sig = _make_token_signals(
            n_bars=n, entry_bar=10,
            is_perp_primary=False,
            funding_rate=0.0,
            max_hold=50,
        )
        config = _default_config()
        spec = StrategySpec(strategy_id="s30", weight=1.0, max_positions=15)
        state = simulate_portfolio({"s30": {"BTC": sig}}, {"s30": spec}, config)
        assert state.total_funding == pytest.approx(0.0)


# ===================================================================
# 14. ADV cap: large positions rejected when ADV is small
# ===================================================================

class TestADVCap:
    """Test 14: ADV cap rejects oversized entries."""

    def test_tiny_adv_rejects_entry(self):
        n = 200
        sig = _make_token_signals(
            n_bars=n, entry_bar=10,
            adv=500.0,
            edge=0.10,
            close_price=100.0,
        )
        config = _default_config(adv_cap_pct=0.10, min_position_usd=200.0)
        spec = StrategySpec(strategy_id="s30", weight=1.0, max_positions=15)
        state = simulate_portfolio({"s30": {"BTC": sig}}, {"s30": spec}, config)
        assert (state.rejections.adv_cap + state.rejections.min_size) >= 1


# ===================================================================
# 15. Determinism: same seed produces same results
# ===================================================================

class TestDeterminism:
    """Test 15: Same seed and inputs produce identical results."""

    def _build_multi_token_signals(self, n: int = 300):
        sigs = {}
        for tok in ["A", "B", "C", "D", "E"]:
            sigs[tok] = _make_token_signals(
                token=tok, n_bars=n, entry_bar=10,
                close_price=100.0, edge=0.35, max_hold=100,
            )
        return sigs

    def test_same_seed_same_results(self):
        config = _default_config(seed=42)
        spec = StrategySpec(strategy_id="s30", weight=1.0, max_positions=15)

        sigs1 = self._build_multi_token_signals()
        state1 = simulate_portfolio({"s30": sigs1}, {"s30": spec}, config)

        sigs2 = self._build_multi_token_signals()
        state2 = simulate_portfolio({"s30": sigs2}, {"s30": spec}, config)

        trades1 = state1.position_manager.closed_trades
        trades2 = state2.position_manager.closed_trades

        assert len(trades1) == len(trades2)
        for t1, t2 in zip(trades1, trades2):
            assert t1.position_id == t2.position_id
            assert t1.pnl == pytest.approx(t2.pnl)
            assert t1.entry_bar == t2.entry_bar
            assert t1.exit_bar == t2.exit_bar


# ===================================================================
# 16. Capital model: equity = initial + realized - fees - funding
# ===================================================================

class TestCapitalModel:
    """Test 16: Equity bookkeeping excludes unrealized PnL."""

    def test_equity_accounts_for_all_costs(self):
        """Final equity = initial + realized_pnl - total_fees - total_funding."""
        n = 200
        close = np.full(n, 100.0, dtype=np.float64)

        sig = _make_token_signals(
            n_bars=n, entry_bar=10,
            close_array=close,
            max_hold=50,
            is_perp_primary=True,
            funding_rate=0.0001,
        )
        config = _default_config()
        spec = StrategySpec(strategy_id="s30", weight=1.0, max_positions=15)
        state = simulate_portfolio({"s30": {"BTC": sig}}, {"s30": spec}, config)

        expected = state.initial_capital + state.realized_pnl - state.total_fees - state.total_funding
        assert state.portfolio_equity == pytest.approx(expected, rel=1e-6)


# ===================================================================
# 17. Edge case: empty signals (no tokens pass filters)
# ===================================================================

class TestEmptySignals:
    """Test 17: Empty signals produce no trades and no errors."""

    def test_no_tokens(self):
        config = _default_config()
        spec = StrategySpec(strategy_id="s30", weight=1.0, max_positions=15)
        state = simulate_portfolio({"s30": {}}, {"s30": spec}, config)
        assert len(state.position_manager.closed_trades) == 0
        assert state.portfolio_equity == pytest.approx(config.capital)

    def test_no_entry_signals(self):
        n = 200
        sig = _make_token_signals(n_bars=n, entry_bar=None)
        config = _default_config()
        spec = StrategySpec(strategy_id="s30", weight=1.0, max_positions=15)
        state = simulate_portfolio({"s30": {"BTC": sig}}, {"s30": spec}, config)
        assert len(state.position_manager.closed_trades) == 0


# ===================================================================
# 18. Edge case: single bar of data
# ===================================================================

class TestSingleBar:
    """Test 18: Single bar of data does not crash."""

    def test_single_bar_no_entry(self):
        sig = _make_token_signals(n_bars=1, entry_bar=None)
        config = _default_config()
        spec = StrategySpec(strategy_id="s30", weight=1.0, max_positions=15)
        state = simulate_portfolio({"s30": {"BTC": sig}}, {"s30": spec}, config)
        assert len(state.position_manager.closed_trades) == 0

    def test_single_bar_with_entry(self):
        sig = _make_token_signals(n_bars=1, entry_bar=0, close_price=100.0)
        config = _default_config()
        spec = StrategySpec(strategy_id="s30", weight=1.0, max_positions=15)
        state = simulate_portfolio({"s30": {"BTC": sig}}, {"s30": spec}, config)
        assert state.portfolio_equity > 0


# ===================================================================
# 20. Edge case: position with NaN ATR
# ===================================================================

class TestNaNATR:
    """Test 20: NaN ATR at entry and during position doesn't crash."""

    def test_nan_atr_at_entry(self):
        n = 200
        atr = np.full(n, np.nan, dtype=np.float64)
        close = np.full(n, 100.0, dtype=np.float64)

        sig = _make_token_signals(
            n_bars=n, entry_bar=10,
            close_array=close,
            atr_array=atr,
            max_hold=50,
        )
        config = _default_config()
        spec = StrategySpec(strategy_id="s30", weight=1.0, max_positions=15)
        state = simulate_portfolio({"s30": {"BTC": sig}}, {"s30": spec}, config)
        assert state.portfolio_equity > 0

    def test_nan_atr_during_position(self):
        n = 200
        atr = np.full(n, 2.0, dtype=np.float64)
        atr[20:30] = np.nan
        close = np.full(n, 100.0, dtype=np.float64)

        sig = _make_token_signals(
            n_bars=n, entry_bar=10,
            close_array=close,
            atr_array=atr,
            max_hold=50,
        )
        config = _default_config()
        spec = StrategySpec(strategy_id="s30", weight=1.0, max_positions=15)
        state = simulate_portfolio({"s30": {"BTC": sig}}, {"s30": spec}, config)
        assert state.portfolio_equity > 0


# ===================================================================
# Additional integration tests
# ===================================================================

class TestMinPositionSizeRejection:
    """Verify min_position_usd rejection when sizing is too small."""

    def test_min_size_rejects_tiny_position(self):
        n = 200
        sig = _make_token_signals(
            n_bars=n, entry_bar=10,
            edge=0.0001,
            adv=1_000.0,
            close_price=100.0,
        )
        config = _default_config(min_position_usd=10_000.0)
        spec = StrategySpec(strategy_id="s30", weight=1.0, max_positions=15)
        state = simulate_portfolio({"s30": {"BTC": sig}}, {"s30": spec}, config)
        assert (state.rejections.min_size + state.rejections.adv_cap) >= 1


class TestMultipleStrategiesSameUniverse:
    """Two strategies trading different tokens simultaneously."""

    def test_two_strategies_independent_tokens(self):
        n = 300
        sig_a = _make_token_signals(token="A", strategy_id="s30", n_bars=n,
                                    entry_bar=10, max_hold=100)
        sig_b = _make_token_signals(token="B", strategy_id="s32", n_bars=n,
                                    entry_bar=10, max_hold=100)
        all_signals = {"s30": {"A": sig_a}, "s32": {"B": sig_b}}
        specs = {
            "s30": StrategySpec(strategy_id="s30", weight=0.5, max_positions=15),
            "s32": StrategySpec(strategy_id="s32", weight=0.5, max_positions=15),
        }
        config = _default_config()
        state = simulate_portfolio(all_signals, specs, config)
        trades = state.position_manager.closed_trades
        strategy_ids = {t.strategy_id for t in trades}
        assert "s30" in strategy_ids
        assert "s32" in strategy_ids


class TestNoReentryWhileOpen:
    """Prevents re-entry for same token+strategy while position is open."""

    def test_no_reentry(self):
        n = 300
        mask = np.zeros(n, dtype=bool)
        mask[10] = True
        mask[20] = True
        sig = _make_token_signals(
            n_bars=n, entry_mask_array=mask,
            max_hold=200, close_price=100.0, edge=0.35,
        )
        config = _default_config()
        spec = StrategySpec(strategy_id="s30", weight=1.0, max_positions=15)
        state = simulate_portfolio({"s30": {"BTC": sig}}, {"s30": spec}, config)
        trades = state.position_manager.closed_trades
        btc_trades = [t for t in trades if t.token == "BTC" and t.strategy_id == "s30"]
        assert len(btc_trades) == 1


class TestEquitySnapshotsRecorded:
    """Equity snapshots are recorded each bar."""

    def test_snapshots_length_matches_bars(self):
        n = 100
        sig = _make_token_signals(n_bars=n, entry_bar=None)
        config = _default_config()
        spec = StrategySpec(strategy_id="s30", weight=1.0, max_positions=15)
        state = simulate_portfolio({"s30": {"BTC": sig}}, {"s30": spec}, config)
        assert len(state.equity_snapshots) == n

    def test_snapshots_are_timestamp_equity_pairs(self):
        n = 50
        sig = _make_token_signals(n_bars=n, entry_bar=None)
        config = _default_config()
        spec = StrategySpec(strategy_id="s30", weight=1.0, max_positions=15)
        state = simulate_portfolio({"s30": {"BTC": sig}}, {"s30": spec}, config)
        for ts, eq in state.equity_snapshots:
            assert isinstance(eq, float)


class TestDataEndForcesClose:
    """Positions still open at the end of data are force-closed."""

    def test_data_end_closes_position(self):
        n = 100
        sig = _make_token_signals(
            n_bars=n, entry_bar=10,
            max_hold=99999, target_mult=99999.0,
        )
        config = _default_config()
        spec = StrategySpec(strategy_id="s30", weight=1.0, max_positions=15)
        state = simulate_portfolio({"s30": {"BTC": sig}}, {"s30": spec}, config)
        trades = state.position_manager.closed_trades
        data_end_trades = [t for t in trades if t.exit_reason == "data_end"]
        assert len(data_end_trades) >= 1
        assert state.position_manager.total_open() == 0


# ===================================================================
# Concurrent Positions Per Token (max_positions_per_symbol)
# ===================================================================

class TestMaxPositionsPerSymbol:
    """Tests for configurable concurrent positions per token+strategy."""

    def test_max_positions_per_symbol_allows_multiple(self):
        """With max_positions_per_symbol=3, two entries on same token should both open."""
        n = 300
        mask = np.zeros(n, dtype=bool)
        mask[10] = True
        mask[20] = True
        sig = _make_token_signals(
            n_bars=n, entry_mask_array=mask,
            max_hold=200, close_price=100.0, edge=0.35,
        )
        config = _default_config()
        spec = StrategySpec(
            strategy_id="s30", weight=1.0, max_positions=15,
            max_positions_per_symbol=3,
        )
        state = simulate_portfolio({"s30": {"BTC": sig}}, {"s30": spec}, config)
        trades = state.position_manager.closed_trades
        btc_trades = [t for t in trades if t.token == "BTC" and t.strategy_id == "s30"]
        assert len(btc_trades) == 2

    def test_default_still_blocks_reentry(self):
        """Default max_positions_per_symbol=1 still blocks re-entry (backward compat)."""
        n = 300
        mask = np.zeros(n, dtype=bool)
        mask[10] = True
        mask[20] = True
        sig = _make_token_signals(
            n_bars=n, entry_mask_array=mask,
            max_hold=200, close_price=100.0, edge=0.35,
        )
        config = _default_config()
        spec = StrategySpec(strategy_id="s30", weight=1.0, max_positions=15)
        state = simulate_portfolio({"s30": {"BTC": sig}}, {"s30": spec}, config)
        trades = state.position_manager.closed_trades
        btc_trades = [t for t in trades if t.token == "BTC" and t.strategy_id == "s30"]
        assert len(btc_trades) == 1


class TestMaxPositionsPerSymbolLimit:
    """Test that concurrent position limit is actually enforced at the boundary."""

    def test_third_entry_blocked_at_limit_2(self):
        """With max_positions_per_symbol=2, third entry on same token should be blocked."""
        n = 500
        mask = np.zeros(n, dtype=bool)
        mask[10] = True
        mask[20] = True
        mask[30] = True
        sig = _make_token_signals(
            n_bars=n, entry_mask_array=mask,
            max_hold=400, close_price=100.0, edge=0.35,
        )
        config = _default_config()
        spec = StrategySpec(
            strategy_id="s30", weight=1.0, max_positions=15,
            max_positions_per_symbol=2,
        )
        state = simulate_portfolio({"s30": {"BTC": sig}}, {"s30": spec}, config)
        trades = state.position_manager.closed_trades
        btc_trades = [t for t in trades if t.token == "BTC" and t.strategy_id == "s30"]
        assert len(btc_trades) == 2

    def test_zero_max_positions_per_symbol_raises(self):
        """max_positions_per_symbol=0 is invalid and should raise ValueError."""
        with pytest.raises(ValueError, match="max_positions_per_symbol"):
            StrategySpec(strategy_id="s30", weight=1.0, max_positions_per_symbol=0)

    def test_negative_max_positions_per_symbol_raises(self):
        """Negative max_positions_per_symbol should raise ValueError."""
        with pytest.raises(ValueError, match="max_positions_per_symbol"):
            StrategySpec(strategy_id="s30", weight=1.0, max_positions_per_symbol=-1)

    def test_bool_max_positions_per_symbol_raises(self):
        """Bool max_positions_per_symbol should raise ValueError."""
        with pytest.raises(ValueError, match="max_positions_per_symbol"):
            StrategySpec(strategy_id="s30", weight=1.0, max_positions_per_symbol=True)

    def test_float_max_positions_per_symbol_coerced_to_int(self):
        """Float max_positions_per_symbol should be coerced to int."""
        spec = StrategySpec(strategy_id="s30", weight=1.0, max_positions_per_symbol=3.0)
        assert spec.max_positions_per_symbol == 3
        assert isinstance(spec.max_positions_per_symbol, int)
