"""Tests for margin-call mechanism in the V4 portfolio simulator.

Tests verify:
  - Margin calls trigger when free_capital drops below -5% of equity
  - The worst-funding-burden position is closed first
  - Linked positions (carry pairs) are closed together
  - Skip-set handles positions with no data at current bar
  - Stress slippage is applied to margin-call exits
  - margin_calls counter increments correctly
  - No margin calls fire when free_capital is healthy

All tests use synthetic data -- no real market data required.
"""
from __future__ import annotations

import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "v4"))

import numpy as np
import pytest

from v5.config import PortfolioConfig, StrategySpec
from v5.position import Position, PositionManager
from v5.simulator import SimulationState, _process_margin_calls, _process_exits
from v5.signals import TokenBarArrays


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
    )
    defaults.update(overrides)
    return PortfolioConfig(**defaults)


def _make_perp_position(
    token: str = "BTC",
    strategy_id: str = "s30",
    entry_price: float = 100.0,
    margin_usd: float = 10_000.0,
    leverage: float = 1.0,
    direction: int = 1,
    entry_bar: int = 0,
    cumulative_funding: float = 0.0,
    leg: str = "primary",
    linked_position_id: str | None = None,
) -> Position:
    """Build a perp Position for margin-call testing."""
    notional = margin_usd * leverage
    quantity = direction * notional / entry_price
    pid = f"{token}:{strategy_id}:{entry_bar}:{leg}"
    return Position(
        position_id=pid,
        token=token,
        strategy_id=strategy_id,
        leg=leg,
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
        no_stop_bars=999,
        min_hold=6,
        max_hold=720,
        stop_price=0.0,
        highest=entry_price,
        lowest=entry_price,
        initial_risk=2.0,
        cumulative_funding=cumulative_funding,
        linked_position_id=linked_position_id,
    )


def _make_token_signals(
    token: str = "BTC",
    strategy_id: str = "s30",
    n_bars: int = 20,
    close_price: float = 100.0,
    funding_rate: float = 0.0,
) -> TokenBarArrays:
    """Build synthetic TokenBarArrays for margin-call tests."""
    timestamps = _make_timestamps(n_bars)
    close_array = np.full(n_bars, close_price, dtype=np.float64)
    high_array = close_array + 1.0
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

    return TokenBarArrays(
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
        funding_1h=funding_array,
        stop_mult=stop_arr,
        trail_mult=trail_arr,
        target_mult=5.0,
        no_stop_bars=999,
        min_hold=6,
        max_hold=720,
        edge=0.35,
        leverage=lev_array,
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


def _build_bar_maps(all_signals, n_bars):
    """Build trivial bar_maps where global_bar == local_bar for all tokens."""
    bar_maps = {}
    for sid, token_sigs in all_signals.items():
        for token, sig in token_sigs.items():
            if token not in bar_maps:
                bar_maps[token] = np.arange(n_bars, dtype=np.int64)
    return bar_maps


# ===================================================================
# Test: Margin call triggers when free_capital < -5% of equity
# ===================================================================

class TestMarginCallTrigger:
    """Margin calls should fire when funding erosion pushes free_capital below threshold."""

    def test_no_margin_call_when_healthy(self):
        """No margin calls should fire when free_capital is positive."""
        config = _default_config()
        state = SimulationState(initial_capital=200_000.0)
        pos = _make_perp_position(margin_usd=10_000.0, cumulative_funding=0.0)
        state.position_manager.open_position(pos)
        state._entry_fees_by_pos[pos.position_id] = 5.0

        sig = _make_token_signals()
        all_signals = {"s30": {"BTC": sig}}
        bar_maps = _build_bar_maps(all_signals, 20)

        _process_margin_calls(state, all_signals, bar_maps, 10, config)

        assert state.margin_calls == 0
        assert len(state.position_manager.open_positions) == 1

    def test_margin_call_fires_when_threshold_breached(self):
        """Margin call should close positions when free_capital < -5% of equity."""
        config = _default_config(capital=100_000.0)
        state = SimulationState(initial_capital=100_000.0)

        # Create a state where free_capital is deeply negative:
        # Lock up 100K in margin, then erode equity with 6K in funding
        # equity = 100K - 6K(funding) = 94K, locked = 100K
        # free_capital = 94K - 100K = -6K, threshold = -94K*0.05 = -4.7K
        # -6K < -4.7K => margin call fires
        pos = _make_perp_position(margin_usd=100_000.0, cumulative_funding=0.0)
        state.position_manager.open_position(pos)
        state._entry_fees_by_pos[pos.position_id] = 0.0
        state.total_funding = 6_000.0  # simulate funding already accrued

        sig = _make_token_signals()
        all_signals = {"s30": {"BTC": sig}}
        bar_maps = _build_bar_maps(all_signals, 20)

        _process_margin_calls(state, all_signals, bar_maps, 10, config)

        assert state.margin_calls >= 1
        assert len(state.position_manager.open_positions) == 0

    def test_margin_call_does_not_fire_within_tolerance(self):
        """free_capital at -3% of equity should NOT trigger margin call (threshold is -5%)."""
        config = _default_config(capital=100_000.0)
        state = SimulationState(initial_capital=100_000.0)

        # equity = 100K - 3K = 97K, locked = 100K
        # free_capital = -3K, threshold = -97K*0.05 = -4.85K
        # -3K > -4.85K => no margin call
        pos = _make_perp_position(margin_usd=100_000.0, cumulative_funding=0.0)
        state.position_manager.open_position(pos)
        state._entry_fees_by_pos[pos.position_id] = 0.0
        state.total_funding = 3_000.0

        sig = _make_token_signals()
        all_signals = {"s30": {"BTC": sig}}
        bar_maps = _build_bar_maps(all_signals, 20)

        _process_margin_calls(state, all_signals, bar_maps, 10, config)

        assert state.margin_calls == 0
        assert len(state.position_manager.open_positions) == 1


# ===================================================================
# Test: Worst-funding-burden position is closed first
# ===================================================================

class TestMarginCallSelection:
    """Margin call should close the position with highest funding/margin ratio."""

    def test_worst_funding_ratio_closed_first(self):
        """Position with higher funding/margin ratio should be closed first."""
        config = _default_config(capital=100_000.0)
        state = SimulationState(initial_capital=100_000.0)

        # Two positions, same margin, different funding burden
        pos_low = _make_perp_position(
            token="ETH", margin_usd=50_000.0, cumulative_funding=100.0,
            entry_price=100.0, strategy_id="s30",
        )
        pos_high = _make_perp_position(
            token="BTC", margin_usd=50_000.0, cumulative_funding=500.0,
            entry_price=100.0, strategy_id="s30",
        )
        state.position_manager.open_position(pos_low)
        state.position_manager.open_position(pos_high)
        state._entry_fees_by_pos[pos_low.position_id] = 0.0
        state._entry_fees_by_pos[pos_high.position_id] = 0.0
        state.total_funding = 8_000.0  # push free_capital negative

        sig_btc = _make_token_signals(token="BTC")
        sig_eth = _make_token_signals(token="ETH")
        all_signals = {"s30": {"BTC": sig_btc, "ETH": sig_eth}}
        bar_maps = _build_bar_maps(all_signals, 20)

        _process_margin_calls(state, all_signals, bar_maps, 10, config)

        # BTC (worst funding ratio) should be closed first; closing its 50K margin
        # restores free_capital well above threshold, so only 1 margin call needed.
        # equity=92K, locked after BTC close=50K, free=~42K >> threshold=-4.6K
        assert state.margin_calls == 1
        remaining_tokens = [p.token for p in state.position_manager.open_positions]
        assert len(remaining_tokens) == 1
        assert remaining_tokens[0] == "ETH"


# ===================================================================
# Test: Linked positions (carry pairs) close together
# ===================================================================

class TestMarginCallLinkedPositions:
    """When a carry pair position is margin-called, both legs must close."""

    def test_linked_pair_both_closed(self):
        """Closing one leg of a carry pair should also close the linked leg."""
        config = _default_config(capital=100_000.0)
        state = SimulationState(initial_capital=100_000.0)

        primary = _make_perp_position(
            token="BTC", margin_usd=50_000.0, cumulative_funding=500.0,
            leg_ref_id="leg_primary", linked_position_id="BTC:s30:0:secondary",
        )
        secondary = _make_perp_position(
            token="BTC", margin_usd=50_000.0, cumulative_funding=100.0,
            direction=-1, leg_ref_id="leg_secondary", linked_position_id="BTC:s30:0:primary",
        )
        state.position_manager.open_position(primary)
        state.position_manager.open_position(secondary)
        state._entry_fees_by_pos[primary.position_id] = 5.0
        state._entry_fees_by_pos[secondary.position_id] = 5.0
        state.total_funding = 8_000.0

        sig = _make_token_signals(token="BTC")
        all_signals = {"s30": {"BTC": sig}}
        bar_maps = _build_bar_maps(all_signals, 20)

        _process_margin_calls(state, all_signals, bar_maps, 10, config)

        # Both legs should be closed
        assert len(state.position_manager.open_positions) == 0
        assert state.margin_calls == 2  # one for each leg


# ===================================================================
# Test: Skip-set handles missing data
# ===================================================================

class TestMarginCallSkipSet:
    """If a position's token has no data at current bar, skip and try next."""

    def test_skips_no_data_and_closes_next(self):
        """Position with no data should be skipped; next worst should be closed."""
        config = _default_config(capital=100_000.0)
        state = SimulationState(initial_capital=100_000.0)

        # BTC position: highest funding but no data at bar 15
        pos_btc = _make_perp_position(
            token="BTC", margin_usd=50_000.0, cumulative_funding=800.0,
        )
        # ETH position: lower funding but has data
        pos_eth = _make_perp_position(
            token="ETH", margin_usd=50_000.0, cumulative_funding=200.0,
            entry_price=100.0,
        )
        state.position_manager.open_position(pos_btc)
        state.position_manager.open_position(pos_eth)
        state._entry_fees_by_pos[pos_btc.position_id] = 0.0
        state._entry_fees_by_pos[pos_eth.position_id] = 0.0
        state.total_funding = 8_000.0

        # BTC signals only have 10 bars (no data at bar 15)
        sig_btc = _make_token_signals(token="BTC", n_bars=10)
        sig_eth = _make_token_signals(token="ETH", n_bars=20)
        all_signals = {"s30": {"BTC": sig_btc, "ETH": sig_eth}}

        # bar_maps: BTC only maps up to bar 9, -1 for bars 10+
        btc_bm = np.full(20, -1, dtype=np.int64)
        btc_bm[:10] = np.arange(10)
        eth_bm = np.arange(20, dtype=np.int64)
        bar_maps = {"BTC": btc_bm, "ETH": eth_bm}

        _process_margin_calls(state, all_signals, bar_maps, 15, config)

        # BTC was skipped (no data at bar 15), ETH was closed
        remaining = [p.token for p in state.position_manager.open_positions]
        assert "BTC" in remaining
        assert "ETH" not in remaining
        assert state.margin_calls >= 1


# ===================================================================
# Test: Stress slippage applied to margin-call exits
# ===================================================================

class TestMarginCallStressSlippage:
    """Margin-call exits should use stress_adv_multiplier like stop exits."""

    def test_stress_multiplier_applied(self):
        """With stress_adv_multiplier=0.5, margin-call exit should have higher slippage."""
        config_normal = _default_config(capital=100_000.0, stress_adv_multiplier=1.0)
        config_stress = _default_config(capital=100_000.0, stress_adv_multiplier=0.5)

        def _run_margin_call(config):
            state = SimulationState(initial_capital=100_000.0)
            pos = _make_perp_position(margin_usd=100_000.0, cumulative_funding=0.0)
            state.position_manager.open_position(pos)
            state._entry_fees_by_pos[pos.position_id] = 0.0
            state.total_funding = 8_000.0

            sig = _make_token_signals()
            all_signals = {"s30": {"BTC": sig}}
            bar_maps = _build_bar_maps(all_signals, 20)

            _process_margin_calls(state, all_signals, bar_maps, 10, config)
            # Return the realized PnL (affected by slippage)
            return state.realized_pnl

        pnl_normal = _run_margin_call(config_normal)
        pnl_stress = _run_margin_call(config_stress)

        # With stress multiplier (lower effective ADV), slippage is higher,
        # so the exit price is worse, resulting in lower (more negative) PnL
        assert pnl_stress < pnl_normal


# ===================================================================
# Test: margin_calls counter in metrics
# ===================================================================

class TestMarginCallMetrics:
    """The margin_calls counter should be reported in extra_info."""

    def test_margin_calls_in_report(self):
        """compute_portfolio_metrics should include margin_calls in extra_info."""
        from v5.report import compute_portfolio_metrics

        config = _default_config(capital=100_000.0)
        state = SimulationState(initial_capital=100_000.0)
        state.margin_calls = 5  # simulate 5 margin calls happened

        # Need at least one equity snapshot for metrics
        import pandas as pd
        ts = pd.Timestamp("2024-06-01")
        state.equity_snapshots = [(ts, 100_000.0)]

        metrics, extra_info, _ = compute_portfolio_metrics(state, 100_000.0)

        assert "margin_calls" in extra_info
        assert extra_info["margin_calls"] == 5


# ===================================================================
# Test: exit_reason = "margin_call" in closed trades
# ===================================================================

class TestMarginCallExitReason:
    """Trades closed by margin call should have exit_reason='margin_call'."""

    def test_exit_reason_set_correctly(self):
        """Closed trade from margin call should have exit_reason='margin_call'."""
        config = _default_config(capital=100_000.0)
        state = SimulationState(initial_capital=100_000.0)

        pos = _make_perp_position(margin_usd=100_000.0)
        state.position_manager.open_position(pos)
        state._entry_fees_by_pos[pos.position_id] = 5.0
        state.total_funding = 8_000.0

        sig = _make_token_signals()
        all_signals = {"s30": {"BTC": sig}}
        bar_maps = _build_bar_maps(all_signals, 20)

        _process_margin_calls(state, all_signals, bar_maps, 10, config)

        trades = state.position_manager.closed_trades
        assert len(trades) >= 1
        assert all(t.exit_reason == "margin_call" for t in trades)


# ===================================================================
# Test: zero / negative equity edge case
# ===================================================================

class TestMarginCallZeroEquity:
    """Margin calls should terminate cleanly when equity is zero or negative."""

    def test_negative_equity_closes_all_positions(self):
        """When realized losses wipe out capital, all positions get margin-called."""
        # Start with 100K, then set realized_pnl = -100K so equity ≈ 0
        config = _default_config(capital=100_000.0)
        state = SimulationState(initial_capital=100_000.0)
        state.realized_pnl = -100_000.0  # equity = 0

        # Open 3 positions with margin, so free_capital is deeply negative
        for i, token in enumerate(["BTC", "ETH", "SOL"]):
            pos = _make_perp_position(
                token=token, margin_usd=10_000.0,
                cumulative_funding=500.0, entry_bar=i,
            )
            state.position_manager.open_position(pos)
            state._entry_fees_by_pos[pos.position_id] = 5.0

        # equity=0, locked=30K => free_capital=-30K, threshold=-max(0*0.05,1)=-1
        assert state.portfolio_equity == 0.0
        assert state.free_capital < -1.0

        # Build signals for all 3 tokens
        all_signals = {"s30": {}}
        for token in ["BTC", "ETH", "SOL"]:
            all_signals["s30"][token] = _make_token_signals(token=token)
        bar_maps = _build_bar_maps(all_signals, 20)

        _process_margin_calls(state, all_signals, bar_maps, 10, config)

        # All 3 should be closed (free_capital = -30K, threshold = -1)
        assert state.position_manager.total_open() == 0
        assert state.margin_calls == 3
        assert len(state.position_manager.closed_trades) == 3

    def test_deeply_negative_equity_terminates(self):
        """Even with deeply negative equity, the loop terminates without errors."""
        config = _default_config(capital=100_000.0)
        state = SimulationState(initial_capital=100_000.0)
        state.realized_pnl = -150_000.0  # equity = -50K

        pos = _make_perp_position(margin_usd=20_000.0, cumulative_funding=1000.0)
        state.position_manager.open_position(pos)
        state._entry_fees_by_pos[pos.position_id] = 5.0

        assert state.portfolio_equity < 0

        sig = _make_token_signals()
        all_signals = {"s30": {"BTC": sig}}
        bar_maps = _build_bar_maps(all_signals, 20)

        # Should not raise, should close the position
        _process_margin_calls(state, all_signals, bar_maps, 10, config)

        assert state.margin_calls == 1
        assert state.position_manager.total_open() == 0


# ===================================================================
# Test: exact threshold boundary (free_capital == threshold)
# ===================================================================

class TestMarginCallExactBoundary:
    """free_capital exactly at threshold should NOT trigger a margin call."""

    def test_exact_threshold_does_not_trigger(self):
        """When free_capital == -5% of equity exactly, no margin call fires.

        The check is `free_capital >= threshold` → break, so equality means safe.
        """
        # We need: free_capital = -max(equity * 0.05, 1.0)
        # equity = initial + realized - fees - funding
        # free_capital = equity - locked_margin
        # Target: equity = 100K, locked = 105K → free = -5K, threshold = -5K
        config = _default_config(capital=100_000.0)
        state = SimulationState(initial_capital=100_000.0)

        pos = _make_perp_position(margin_usd=105_000.0)
        state.position_manager.open_position(pos)
        state._entry_fees_by_pos[pos.position_id] = 0.0

        # equity = 100K, locked = 105K, free = -5K
        # threshold = -max(100K * 0.05, 1) = -5K
        # free_capital (-5K) >= threshold (-5K) → no margin call
        assert state.portfolio_equity == 100_000.0
        assert state.free_capital == -5_000.0
        threshold = -max(state.portfolio_equity * 0.05, 1.0)
        assert state.free_capital == threshold  # exact boundary

        sig = _make_token_signals()
        all_signals = {"s30": {"BTC": sig}}
        bar_maps = _build_bar_maps(all_signals, 20)

        _process_margin_calls(state, all_signals, bar_maps, 10, config)

        assert state.margin_calls == 0
        assert state.position_manager.total_open() == 1


# ===================================================================
# Test: all positions lack data at current bar
# ===================================================================

class TestMarginCallAllPositionsNoData:
    """Loop terminates when every position is skipped due to missing data."""

    def test_all_positions_no_data_terminates(self):
        """If no position has data at the current bar, loop exits via worst_pos=None."""
        config = _default_config(capital=100_000.0)
        state = SimulationState(initial_capital=100_000.0)

        # Two positions, both with data that ends before bar 15
        for token in ["BTC", "ETH"]:
            pos = _make_perp_position(
                token=token, margin_usd=60_000.0,
                cumulative_funding=500.0, entry_bar=0,
            )
            state.position_manager.open_position(pos)
            state._entry_fees_by_pos[pos.position_id] = 0.0

        state.total_funding = 25_000.0
        # equity = 75K, locked = 120K, free = -45K → deeply below threshold
        assert state.free_capital < -max(state.portfolio_equity * 0.05, 1.0)

        # Build signals with only 10 bars — bar 15 will have lb=-1
        sig_btc = _make_token_signals(token="BTC", n_bars=10)
        sig_eth = _make_token_signals(token="ETH", n_bars=10)
        all_signals = {"s30": {"BTC": sig_btc, "ETH": sig_eth}}

        # Custom bar_maps: bars 0-9 map to local 0-9, bars 10+ map to -1
        bar_maps = {}
        for token in ["BTC", "ETH"]:
            bm = np.full(20, -1, dtype=np.int64)
            bm[:10] = np.arange(10)
            bar_maps[token] = bm

        # Call at bar 15 — both positions have no data, both go to skip_ids
        _process_margin_calls(state, all_signals, bar_maps, 15, config)

        # No positions closed (all skipped), but loop terminates cleanly
        assert state.margin_calls == 0
        assert state.position_manager.total_open() == 2


# ===================================================================
# Test: zero-margin position is skipped
# ===================================================================

class TestMarginCallZeroMarginSkip:
    """Positions with margin_usd=0 should be skipped in worst-ratio selection."""

    def test_zero_margin_position_skipped(self):
        """A position with zero margin should not be selected as worst-ratio candidate."""
        config = _default_config(capital=100_000.0)
        state = SimulationState(initial_capital=100_000.0)

        # Position A: zero margin (degenerate, should be skipped)
        pos_zero = _make_perp_position(
            token="BTC", margin_usd=0.0,
            cumulative_funding=9999.0, entry_bar=0,
        )
        pos_zero.quantity = 0.0  # consistent with zero margin

        # Position B: normal margin, high funding
        pos_normal = _make_perp_position(
            token="ETH", margin_usd=80_000.0,
            cumulative_funding=500.0, entry_bar=1,
        )

        state.position_manager.open_position(pos_zero)
        state.position_manager.open_position(pos_normal)
        state._entry_fees_by_pos[pos_zero.position_id] = 0.0
        state._entry_fees_by_pos[pos_normal.position_id] = 0.0
        state.total_funding = 15_000.0
        # equity = 85K, locked = 80K (zero-margin doesn't count), free = 5K
        # But we need free < threshold to trigger. Bump funding more.
        state.total_funding = 20_000.0
        # equity = 80K, locked = 80K, free = 0 → threshold = -4K, 0 >= -4K, no trigger
        # Need locked > equity:
        state.total_funding = 25_000.0
        # equity = 75K, locked = 80K, free = -5K, threshold = -3.75K → triggers

        assert state.free_capital < -max(state.portfolio_equity * 0.05, 1.0)

        sig_btc = _make_token_signals(token="BTC")
        sig_eth = _make_token_signals(token="ETH")
        all_signals = {"s30": {"BTC": sig_btc, "ETH": sig_eth}}
        bar_maps = _build_bar_maps(all_signals, 20)

        _process_margin_calls(state, all_signals, bar_maps, 10, config)

        # ETH (normal margin) should be closed, BTC (zero margin) should remain
        assert state.margin_calls == 1
        remaining = [p.token for p in state.position_manager.open_positions]
        assert "BTC" in remaining
        assert "ETH" not in remaining
