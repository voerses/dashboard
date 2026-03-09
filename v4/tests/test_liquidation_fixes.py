"""Acceptance tests for Task 1: V4 Liquidation Bug Fixes.

Tests verify:
  - Liquidation triggers at notional * MMR (not margin * MMR)
  - Liquidation max_loss = margin - notional * MMR (using entry notional)
  - Liquidation fee = 1.5% of notional (not taker fee on margin)
  - cumulative_funding correctly affects liquidation threshold
  - Negative cumulative_funding (income) delays liquidation
  - Short position liquidation (price rises trigger liquidation)

All tests use synthetic data -- no real market data required.
These tests MUST FAIL until the fixes are implemented (RED phase).
"""
from __future__ import annotations

import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "v3"))

import numpy as np
import pytest

from v4.config import PortfolioConfig, StrategySpec
from v4.position import Position, PositionManager
from v4.simulator import SimulationState, _process_exits, _close_position
from v4.signals import TokenSignals
from v3.universe import get_liquidation_fee_rate, EXCHANGE_LIQUIDATION_FEE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_timestamps(n: int, start: str = "2024-01-01") -> np.ndarray:
    import pandas as pd
    return pd.date_range(start, periods=n, freq="1h").values


def _default_config(**overrides) -> PortfolioConfig:
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
        train_bars=0,
        recal_bars=99999,
        purge_bars=0,
    )
    defaults.update(overrides)
    return PortfolioConfig(**defaults)


def _make_perp_position(
    token: str = "BTC",
    strategy_id: str = "s30",
    entry_price: float = 100.0,
    margin_usd: float = 10_000.0,
    leverage: float = 5.0,
    direction: int = 1,
    entry_bar: int = 0,
    cumulative_funding: float = 0.0,
    no_stop_bars: int = 6,
) -> Position:
    """Build a leveraged perp Position for liquidation testing."""
    notional = margin_usd * leverage
    quantity = direction * notional / entry_price
    pid = f"{token}:{strategy_id}:{entry_bar}:primary"
    return Position(
        position_id=pid,
        token=token,
        strategy_id=strategy_id,
        leg="primary",
        entry_bar=entry_bar,
        entry_price=entry_price,
        direction=direction,
        quantity=quantity,
        margin_usd=margin_usd,
        leverage=leverage,
        is_perp=True,
        fee_rate=0.0005,
        stop_mult=2.0,
        trail_mult=3.0,
        target_mult=5.0,
        no_stop_bars=no_stop_bars,
        min_hold=6,
        max_hold=720,
        exit_regimes=set(),
        stop_price=0.0,
        highest=entry_price,
        lowest=entry_price,
        initial_risk=2.0,
        cumulative_funding=cumulative_funding,
    )


def _make_token_signals(
    token: str = "BTC",
    strategy_id: str = "s30",
    n_bars: int = 100,
    close_price: float = 100.0,
    close_array: np.ndarray | None = None,
    high_array: np.ndarray | None = None,
    low_array: np.ndarray | None = None,
    funding_rate: float = 0.0,
) -> TokenSignals:
    """Build synthetic TokenSignals for liquidation tests."""
    timestamps = _make_timestamps(n_bars)
    if close_array is None:
        close_array = np.full(n_bars, close_price, dtype=np.float64)
    if high_array is None:
        high_array = close_array + 1.0
    if low_array is None:
        low_array = close_array - 1.0
    atr_array = np.full(n_bars, 2.0, dtype=np.float64)
    adv_array = np.full(n_bars, 5_000_000.0, dtype=np.float64)
    funding_array = np.full(n_bars, funding_rate, dtype=np.float64)
    entry_mask = np.zeros(n_bars, dtype=bool)
    dir_array = np.full(n_bars, 1, dtype=np.int8)
    regime_array = np.zeros(n_bars, dtype=np.int8)
    sm_array = np.full(n_bars, 1.0, dtype=np.float64)
    lev_array = np.full(n_bars, 1.0, dtype=np.float64)
    stop_arr = np.full(n_bars, 2.0, dtype=np.float64)
    trail_arr = np.full(n_bars, 3.0, dtype=np.float64)

    return TokenSignals(
        token=token,
        strategy_id=strategy_id,
        n_bars=n_bars,
        timestamps=timestamps,
        entry_mask=entry_mask,
        direction=dir_array,
        close=close_array,
        high=high_array,
        low=low_array,
        atr=atr_array,
        rolling_adv=adv_array,
        regime=regime_array,
        funding_1h=funding_array,
        exit_regimes=set(),
        stop_mult=stop_arr,
        trail_mult=trail_arr,
        target_mult=5.0,
        no_stop_bars=6,
        min_hold=6,
        max_hold=720,
        edge=0.35,
        size_multiplier=sm_array,
        cap_multiplier=1.0,
        leverage=lev_array,
        max_trade_pct=0.0,
        convex_exit=False,
        rsi=None,
        rsi_exit_level=999.0,
        mean_target_vals=None,
        is_combined=False,
        secondary_entry_mask=None,
        secondary_direction=None,
        secondary_leverage=1.0,
        capital_split=0.5,
        is_perp_primary=False,
        is_perp_secondary=False,
        perp_close=None,
        perp_high=None,
        perp_low=None,
        perp_atr=None,
        perp_rolling_adv=None,
        perp_funding_1h=None,
    )


# ===================================================================
# Test: Liquidation threshold uses notional * MMR (not margin * MMR)
# ===================================================================

class TestLiquidationThreshold:
    """Liquidation must trigger when equity < notional * MMR.

    Binance MMR is 0.004 (0.4%). For a 5x leveraged position:
      margin = $10,000, notional = $50,000
      Correct threshold: notional * MMR = $50,000 * 0.004 = $200
      Bug (old): margin * MMR = $10,000 * 0.004 = $40

    So liquidation should fire when:
      margin + unrealized - cumulative_funding < $200 (correct)
    Not when:
      margin + unrealized - cumulative_funding < $40 (buggy)
    """

    def test_liquidation_fires_at_notional_mmr_threshold_5x(self):
        """5x leverage long: liquidation fires when equity < notional * MMR = $200.

        Entry: $100, margin $10k, 5x leverage => notional $50k, quantity 500
        Correct threshold: $50k * 0.004 = $200
        Buggy threshold: $10k * 0.004 = $40

        At low=$80.3: unrealized = 500 * ($80.3 - $100) = -$9,850
        Equity = $10,000 + (-$9,850) = $150 < $200 => SHOULD liquidate (correct formula).
        Equity = $150 > $40 => would NOT liquidate under buggy formula.
        This price discriminates between the two formulas.
        """
        config = _default_config()
        pos = _make_perp_position(leverage=5.0, entry_price=100.0, margin_usd=10_000.0)
        state = SimulationState(initial_capital=200_000.0)
        state.position_manager.open_position(pos)
        state._entry_fees_by_pos[pos.position_id] = 25.0  # 0.05% * $50k

        n_bars = 100
        # Price drops to $80.3 low at bar 10 — in the golden range ($40 < $150 < $200)
        low_arr = np.full(n_bars, 100.0, dtype=np.float64)
        low_arr[10] = 80.3
        close_arr = np.full(n_bars, 100.0, dtype=np.float64)
        close_arr[10] = 81.0
        high_arr = np.full(n_bars, 101.0, dtype=np.float64)
        high_arr[10] = 81.5

        sig = _make_token_signals(close_array=close_arr, high_array=high_arr, low_array=low_arr)
        all_signals = {"s30": {"BTC": sig}}
        bar_maps = {"BTC": np.arange(n_bars, dtype=np.int32)}

        _process_exits(state, all_signals, bar_maps, 10, config)

        # Position should be liquidated (closed)
        assert state.position_manager.total_open() == 0
        assert len(state.position_manager.closed_trades) == 1
        assert state.position_manager.closed_trades[0].exit_reason == "liquidation"

    def test_no_liquidation_above_notional_mmr_threshold_5x(self):
        """5x leverage long: NO liquidation when equity > notional * MMR = $200.

        Entry: $100, margin $10k, 5x leverage => notional $50k, quantity 500
        At low=$80.7: unrealized = 500 * ($80.7 - $100) = -$9,650
        Equity = $10,000 + (-$9,650) = $350 > $200 => should NOT liquidate.
        """
        config = _default_config()
        pos = _make_perp_position(leverage=5.0, entry_price=100.0, margin_usd=10_000.0,
                                  no_stop_bars=9999)
        state = SimulationState(initial_capital=200_000.0)
        state.position_manager.open_position(pos)
        state._entry_fees_by_pos[pos.position_id] = 25.0

        n_bars = 100
        low_arr = np.full(n_bars, 100.0, dtype=np.float64)
        low_arr[10] = 80.7
        close_arr = np.full(n_bars, 100.0, dtype=np.float64)
        close_arr[10] = 81.0
        high_arr = np.full(n_bars, 101.0, dtype=np.float64)

        sig = _make_token_signals(close_array=close_arr, high_array=high_arr, low_array=low_arr)
        all_signals = {"s30": {"BTC": sig}}
        bar_maps = {"BTC": np.arange(n_bars, dtype=np.int32)}

        _process_exits(state, all_signals, bar_maps, 10, config)

        # Position should NOT be liquidated
        assert state.position_manager.total_open() == 1

    def test_liquidation_fires_at_notional_mmr_threshold_3x(self):
        """3x leverage long: liquidation fires at correct notional-based threshold.

        Entry: $100, margin $10k, 3x => notional $30k, quantity 300
        Correct threshold: $30k * 0.004 = $120
        Buggy threshold: $10k * 0.004 = $40

        At low=$67: unrealized = 300 * ($67 - $100) = -$9,900
        Equity = $10,000 + (-$9,900) = $100 < $120 => SHOULD liquidate.
        Equity = $100 > $40 => buggy formula would NOT liquidate.
        """
        config = _default_config()
        pos = _make_perp_position(leverage=3.0, entry_price=100.0, margin_usd=10_000.0)
        state = SimulationState(initial_capital=200_000.0)
        state.position_manager.open_position(pos)
        state._entry_fees_by_pos[pos.position_id] = 15.0

        n_bars = 100
        low_arr = np.full(n_bars, 100.0, dtype=np.float64)
        low_arr[10] = 67.0
        close_arr = np.full(n_bars, 100.0, dtype=np.float64)
        close_arr[10] = 67.5
        high_arr = np.full(n_bars, 101.0, dtype=np.float64)

        sig = _make_token_signals(close_array=close_arr, high_array=high_arr, low_array=low_arr)
        all_signals = {"s30": {"BTC": sig}}
        bar_maps = {"BTC": np.arange(n_bars, dtype=np.int32)}

        _process_exits(state, all_signals, bar_maps, 10, config)

        assert state.position_manager.total_open() == 0
        assert state.position_manager.closed_trades[0].exit_reason == "liquidation"

    def test_no_liquidation_at_1x_leverage_long(self):
        """1x leverage long: position has no liquidation risk (margin == notional).

        Entry: $100, margin $10k, 1x => notional $10k, quantity 100
        At price $80: unrealized = 100 * ($80 - $100) = -$2,000
        Equity = $10,000 + (-$2,000) = $8,000 > $40 (notional * MMR)
        Should NOT liquidate (can only liquidate if leverage > 1 or short).
        """
        config = _default_config()
        pos = _make_perp_position(leverage=1.0, entry_price=100.0, margin_usd=10_000.0,
                                  no_stop_bars=9999)
        state = SimulationState(initial_capital=200_000.0)
        state.position_manager.open_position(pos)
        state._entry_fees_by_pos[pos.position_id] = 5.0

        n_bars = 100
        low_arr = np.full(n_bars, 100.0, dtype=np.float64)
        low_arr[10] = 80.0
        close_arr = np.full(n_bars, 100.0, dtype=np.float64)
        close_arr[10] = 80.0

        sig = _make_token_signals(close_array=close_arr, low_array=low_arr)
        all_signals = {"s30": {"BTC": sig}}
        bar_maps = {"BTC": np.arange(n_bars, dtype=np.int32)}

        _process_exits(state, all_signals, bar_maps, 10, config)

        # 1x long should NOT be checked for liquidation
        assert state.position_manager.total_open() == 1


# ===================================================================
# Test: Liquidation max_loss = margin - entry_notional * MMR
# ===================================================================

class TestLiquidationMaxLoss:
    """Liquidation max_loss must be margin - entry_notional * MMR.

    entry_notional = margin * leverage (fixed at entry time).
    NOT exit-time notional (which changes with mark price).
    """

    def test_liquidation_max_loss_5x(self):
        """5x leverage: max_loss = $10,000 - $50,000 * 0.004 = $9,800.

        entry_notional = margin * leverage = $10,000 * 5 = $50,000
        max_loss = $10,000 - $50,000 * 0.004 = $9,800
        Old bug: max_loss = $10,000 * (1 - 0.004) = $9,960 (wrong).
        """
        config = _default_config()
        pos = _make_perp_position(leverage=5.0, entry_price=100.0, margin_usd=10_000.0)
        state = SimulationState(initial_capital=200_000.0)
        state.position_manager.open_position(pos)
        state._entry_fees_by_pos[pos.position_id] = 25.0

        # Force liquidation with extreme price drop
        n_bars = 100
        low_arr = np.full(n_bars, 100.0, dtype=np.float64)
        low_arr[5] = 50.0  # extreme drop to guarantee liquidation
        close_arr = np.full(n_bars, 100.0, dtype=np.float64)
        close_arr[5] = 55.0
        high_arr = np.full(n_bars, 101.0, dtype=np.float64)

        sig = _make_token_signals(close_array=close_arr, high_array=high_arr, low_array=low_arr)
        all_signals = {"s30": {"BTC": sig}}
        bar_maps = {"BTC": np.arange(n_bars, dtype=np.int32)}

        _process_exits(state, all_signals, bar_maps, 5, config)

        assert len(state.position_manager.closed_trades) == 1
        trade = state.position_manager.closed_trades[0]
        assert trade.exit_reason == "liquidation"

        # Verify max_loss = margin - entry_notional * MMR
        # entry_notional = margin * leverage = $10,000 * 5 = $50,000 (fixed at entry)
        entry_notional = pos.margin_usd * pos.leverage
        expected_max_loss = pos.margin_usd - entry_notional * 0.004
        # The realized_pnl should reflect -max_loss (plus cumulative_funding reversal)
        # net_pnl = -(max_loss + exit_fee) - cumulative_funding
        # With cumulative_funding == 0:
        # net_pnl = -(expected_max_loss + exit_fee)
        assert trade.pnl == pytest.approx(-(expected_max_loss + trade.exit_fee), rel=1e-4)

    def test_liquidation_max_loss_3x(self):
        """3x leverage: max_loss = $10,000 - $30,000 * 0.004 = $9,880.

        entry_notional = $10,000 * 3 = $30,000
        """
        config = _default_config()
        pos = _make_perp_position(leverage=3.0, entry_price=100.0, margin_usd=10_000.0)
        state = SimulationState(initial_capital=200_000.0)
        state.position_manager.open_position(pos)
        state._entry_fees_by_pos[pos.position_id] = 15.0

        n_bars = 100
        low_arr = np.full(n_bars, 100.0, dtype=np.float64)
        low_arr[5] = 50.0
        close_arr = np.full(n_bars, 100.0, dtype=np.float64)
        close_arr[5] = 55.0
        high_arr = np.full(n_bars, 101.0, dtype=np.float64)

        sig = _make_token_signals(close_array=close_arr, high_array=high_arr, low_array=low_arr)
        all_signals = {"s30": {"BTC": sig}}
        bar_maps = {"BTC": np.arange(n_bars, dtype=np.int32)}

        _process_exits(state, all_signals, bar_maps, 5, config)

        trade = state.position_manager.closed_trades[0]
        entry_notional = pos.margin_usd * pos.leverage
        expected_max_loss = pos.margin_usd - entry_notional * 0.004
        assert trade.pnl == pytest.approx(-(expected_max_loss + trade.exit_fee), rel=1e-4)


# ===================================================================
# Test: Liquidation fee = 1.5% of notional (not taker fee on margin)
# ===================================================================

class TestLiquidationFee:
    """Liquidation fee must be 1.5% of notional, not taker fee (0.05%) on margin."""

    def test_liquidation_fee_rate_exists(self):
        """v3/universe.py must export get_liquidation_fee_rate() and EXCHANGE_LIQUIDATION_FEE."""
        assert callable(get_liquidation_fee_rate)
        assert isinstance(EXCHANGE_LIQUIDATION_FEE, dict)
        assert "binance" in EXCHANGE_LIQUIDATION_FEE

    def test_liquidation_fee_binance_is_1_5_pct(self):
        """Binance liquidation fee rate is 1.5% (0.015)."""
        rate = get_liquidation_fee_rate("binance")
        assert rate == pytest.approx(0.015)

    def test_liquidation_exit_fee_uses_notional_not_margin(self):
        """On liquidation, exit_fee = notional * 0.015, not margin * 0.0005.

        5x leverage: margin=$10k, notional=$50k
        Correct fee: $50k * 0.015 = $750
        Buggy fee: $10k * 0.0005 = $5 (way too low)
        """
        config = _default_config()
        pos = _make_perp_position(leverage=5.0, entry_price=100.0, margin_usd=10_000.0)
        state = SimulationState(initial_capital=200_000.0)
        state.position_manager.open_position(pos)
        state._entry_fees_by_pos[pos.position_id] = 25.0

        n_bars = 100
        low_arr = np.full(n_bars, 100.0, dtype=np.float64)
        low_arr[5] = 50.0  # extreme drop to guarantee liquidation
        close_arr = np.full(n_bars, 100.0, dtype=np.float64)
        close_arr[5] = 55.0
        high_arr = np.full(n_bars, 101.0, dtype=np.float64)

        sig = _make_token_signals(close_array=close_arr, high_array=high_arr, low_array=low_arr)
        all_signals = {"s30": {"BTC": sig}}
        bar_maps = {"BTC": np.arange(n_bars, dtype=np.int32)}

        _process_exits(state, all_signals, bar_maps, 5, config)

        trade = state.position_manager.closed_trades[0]
        assert trade.exit_reason == "liquidation"

        # exit_fee should be notional * 0.015 (using exit close price)
        exit_notional = abs(pos.quantity * trade.exit_price)
        expected_fee = exit_notional * 0.015
        assert trade.exit_fee == pytest.approx(expected_fee, rel=1e-4)
        # Must be significantly more than the old buggy fee
        buggy_fee = pos.margin_usd * pos.fee_rate  # $5
        assert trade.exit_fee > buggy_fee * 10  # at least 10x the old buggy fee


# ===================================================================
# Test: cumulative_funding affects liquidation threshold
# ===================================================================

class TestLiquidationFunding:
    """cumulative_funding (cost) reduces equity, bringing liquidation closer.
    Negative cumulative_funding (income) increases equity, delaying liquidation.

    With MMR=0.004 and 5x leverage:
      Correct threshold = notional * MMR = $50,000 * 0.004 = $200
    """

    def test_positive_funding_accelerates_liquidation(self):
        """Positive cumulative_funding (cost paid) should trigger liquidation sooner.

        5x long: margin=$10k, notional=$50k
        Correct threshold: notional * MMR = $200
        At low=$80.5 with no funding: equity = $10k + 500*(-19.5) = $250 > $200 => no liquidation
        At low=$80.5 with funding=$100: equity = $250 - $100 = $150 < $200 => liquidation
        """
        config = _default_config()

        # Case 1: low=$80.5, funding=$100 => should liquidate
        pos1 = _make_perp_position(leverage=5.0, entry_price=100.0, margin_usd=10_000.0,
                                   cumulative_funding=100.0)
        state1 = SimulationState(initial_capital=200_000.0)
        state1.total_funding = 100.0  # sync state funding with position
        state1.position_manager.open_position(pos1)
        state1._entry_fees_by_pos[pos1.position_id] = 25.0

        n_bars = 100
        low_arr = np.full(n_bars, 100.0)
        low_arr[10] = 80.5
        close_arr = np.full(n_bars, 100.0)
        close_arr[10] = 81.0
        high_arr = np.full(n_bars, 101.0)

        sig1 = _make_token_signals(close_array=close_arr.copy(), high_array=high_arr.copy(),
                                   low_array=low_arr.copy())
        _process_exits(state1, {"s30": {"BTC": sig1}},
                       {"BTC": np.arange(n_bars, dtype=np.int32)}, 10, config)
        assert state1.position_manager.total_open() == 0, \
            "Position with funding cost should be liquidated at low=$80.5"

        # Case 2: low=$80.5, no funding => should NOT liquidate
        pos2 = _make_perp_position(leverage=5.0, entry_price=100.0, margin_usd=10_000.0,
                                   cumulative_funding=0.0, no_stop_bars=9999)
        state2 = SimulationState(initial_capital=200_000.0)
        state2.position_manager.open_position(pos2)
        state2._entry_fees_by_pos[pos2.position_id] = 25.0

        sig2 = _make_token_signals(close_array=close_arr.copy(), high_array=high_arr.copy(),
                                   low_array=low_arr.copy())
        _process_exits(state2, {"s30": {"BTC": sig2}},
                       {"BTC": np.arange(n_bars, dtype=np.int32)}, 10, config)
        assert state2.position_manager.total_open() == 1, \
            "Position without funding should NOT be liquidated at low=$80.5"

    def test_negative_funding_delays_liquidation(self):
        """Negative cumulative_funding (income received) should delay liquidation.

        5x long: margin=$10k, notional=$50k
        Correct threshold: notional * MMR = $200
        At low=$80.3: unrealized = 500*($80.3-$100) = -$9,850
        Without funding:
          equity = $10,000 + (-$9,850) = $150 < $200 => liquidation
        With funding income = -$100 (negative = income):
          equity = $10,000 + (-$9,850) - (-$100) = $250 > $200 => no liquidation
        """
        config = _default_config()

        # With funding income => should NOT liquidate
        pos = _make_perp_position(leverage=5.0, entry_price=100.0, margin_usd=10_000.0,
                                  cumulative_funding=-100.0, no_stop_bars=9999)
        state = SimulationState(initial_capital=200_000.0)
        state.total_funding = -100.0
        state.position_manager.open_position(pos)
        state._entry_fees_by_pos[pos.position_id] = 25.0

        n_bars = 100
        low_arr = np.full(n_bars, 100.0)
        low_arr[10] = 80.3
        close_arr = np.full(n_bars, 100.0)
        close_arr[10] = 81.0
        high_arr = np.full(n_bars, 101.0)

        sig = _make_token_signals(close_array=close_arr, high_array=high_arr, low_array=low_arr)
        _process_exits(state, {"s30": {"BTC": sig}},
                       {"BTC": np.arange(n_bars, dtype=np.int32)}, 10, config)
        assert state.position_manager.total_open() == 1, \
            "Funding income (-$100) should delay liquidation at low=$80.3"


# ===================================================================
# Test: Short position liquidation
# ===================================================================

class TestShortLiquidation:
    """Short positions liquidate when price RISES (using HIGH price).

    For shorts: unrealized = abs(quantity) * (entry_price - high_val)
    Liquidation when equity < notional * MMR.
    """

    def test_short_5x_liquidation_on_price_rise(self):
        """Short 5x perp: liquidation fires when price rises and equity < notional * MMR.

        Entry: $100, margin $10k, 5x leverage, direction=-1
        notional = $50k, quantity = -500
        Correct threshold: $50k * 0.004 = $200
        Buggy threshold: $10k * 0.004 = $40

        At high=$119.7: unrealized = 500 * ($100 - $119.7) = -$9,850
        Equity = $10,000 + (-$9,850) = $150 < $200 => SHOULD liquidate.
        Equity = $150 > $40 => buggy formula would NOT liquidate.
        """
        config = _default_config()
        pos = _make_perp_position(leverage=5.0, entry_price=100.0, margin_usd=10_000.0,
                                  direction=-1)
        state = SimulationState(initial_capital=200_000.0)
        state.position_manager.open_position(pos)
        state._entry_fees_by_pos[pos.position_id] = 25.0

        n_bars = 100
        high_arr = np.full(n_bars, 101.0, dtype=np.float64)
        high_arr[10] = 119.7  # price rises sharply
        close_arr = np.full(n_bars, 100.0, dtype=np.float64)
        close_arr[10] = 119.0
        low_arr = np.full(n_bars, 99.0, dtype=np.float64)
        low_arr[10] = 118.5

        sig = _make_token_signals(close_array=close_arr, high_array=high_arr, low_array=low_arr)
        all_signals = {"s30": {"BTC": sig}}
        bar_maps = {"BTC": np.arange(n_bars, dtype=np.int32)}

        _process_exits(state, all_signals, bar_maps, 10, config)

        assert state.position_manager.total_open() == 0
        assert len(state.position_manager.closed_trades) == 1
        assert state.position_manager.closed_trades[0].exit_reason == "liquidation"

    def test_short_3x_liquidation_on_price_rise(self):
        """Short 3x perp: liquidation fires on price rise.

        Entry: $100, margin $10k, 3x, direction=-1
        notional = $30k, quantity = -300
        Correct threshold: $30k * 0.004 = $120

        At high=$133: unrealized = 300 * ($100 - $133) = -$9,900
        Equity = $10,000 + (-$9,900) = $100 < $120 => SHOULD liquidate.
        """
        config = _default_config()
        pos = _make_perp_position(leverage=3.0, entry_price=100.0, margin_usd=10_000.0,
                                  direction=-1)
        state = SimulationState(initial_capital=200_000.0)
        state.position_manager.open_position(pos)
        state._entry_fees_by_pos[pos.position_id] = 15.0

        n_bars = 100
        high_arr = np.full(n_bars, 101.0, dtype=np.float64)
        high_arr[10] = 133.0
        close_arr = np.full(n_bars, 100.0, dtype=np.float64)
        close_arr[10] = 132.0
        low_arr = np.full(n_bars, 99.0, dtype=np.float64)
        low_arr[10] = 131.0

        sig = _make_token_signals(close_array=close_arr, high_array=high_arr, low_array=low_arr)
        all_signals = {"s30": {"BTC": sig}}
        bar_maps = {"BTC": np.arange(n_bars, dtype=np.int32)}

        _process_exits(state, all_signals, bar_maps, 10, config)

        assert state.position_manager.total_open() == 0
        assert state.position_manager.closed_trades[0].exit_reason == "liquidation"

    def test_short_negative_funding_delays_liquidation(self):
        """Short position with negative cumulative_funding (income) delays liquidation.

        Short 5x: entry=$100, margin=$10k, quantity=-500
        Correct threshold: $200
        At high=$119.7: unrealized = 500*($100-$119.7) = -$9,850
        Without funding: equity = $150 < $200 => liquidation
        With funding=-$100 (income): equity = $150 + $100 = $250 > $200 => no liquidation
        """
        config = _default_config()

        # With funding income => should NOT liquidate
        pos = _make_perp_position(leverage=5.0, entry_price=100.0, margin_usd=10_000.0,
                                  direction=-1, cumulative_funding=-100.0, no_stop_bars=9999)
        state = SimulationState(initial_capital=200_000.0)
        state.total_funding = -100.0
        state.position_manager.open_position(pos)
        state._entry_fees_by_pos[pos.position_id] = 25.0

        n_bars = 100
        high_arr = np.full(n_bars, 101.0, dtype=np.float64)
        high_arr[10] = 119.7
        close_arr = np.full(n_bars, 100.0, dtype=np.float64)
        close_arr[10] = 119.0
        low_arr = np.full(n_bars, 99.0, dtype=np.float64)
        low_arr[10] = 118.5

        sig = _make_token_signals(close_array=close_arr, high_array=high_arr, low_array=low_arr)
        _process_exits(state, {"s30": {"BTC": sig}},
                       {"BTC": np.arange(n_bars, dtype=np.int32)}, 10, config)
        assert state.position_manager.total_open() == 1, \
            "Funding income (-$100) should delay short liquidation at high=$119.7"
