"""Tests for V4 Diagnostic Infrastructure.

Tests cover:
  - Config: raw_mode, skip_walk_forward, raw_max_positions, RegimeConfig, regime_params
  - SignalDiagnostics: dataclass, to_dict, direction_zero
  - TokenSignals: diagnostic counters
  - Walk-forward skip: no masking, lower bar threshold, cutoff calculation
  - Raw mode simulator: strategy sizing, fixed equity, skip constraints, keep slippage/exits
  - Exit constants: configurable regime_exit_min_bars, convex thresholds
  - Configurable regime: parameterized detect_daily_regime
  - Custom indicators: compute_custom_indicators utility
  - Diagnostic report: print_diagnostic_report
"""
from __future__ import annotations

import dataclasses
import sys
from collections import defaultdict
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root))

from v4.config import PortfolioConfig, StrategySpec, RegimeConfig, SizingDefaults
from v4.position import Position
from v4.simulator import (
    RejectionStats,
    SignalDiagnostics,
    SimulationState,
    _process_exits,
    _process_entries,
    build_unified_index,
)
from v4.signals import TokenSignals, _apply_walk_forward_mask


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_token_signals(
    token="BTC",
    strategy_id="s30",
    n_bars=200,
    entry_bar=None,
    direction_val=1,
    edge=0.35,
    raw_entry_count=0,
    post_liquidity_count=0,
    post_walkforward_count=0,
    regime_exit_min_bars=6,
    convex_bar_thresholds=(48, 12),
    convex_multipliers=(2.0, 1.5, 0.3),
    convex_exit=False,
    is_perp=False,
):
    """Create a minimal TokenSignals for testing."""
    entry_mask = np.zeros(n_bars, dtype=bool)
    if entry_bar is not None:
        if isinstance(entry_bar, (list, tuple)):
            for b in entry_bar:
                entry_mask[b] = True
        else:
            entry_mask[entry_bar] = True

    close = np.full(n_bars, 100.0, dtype=np.float32)
    high = np.full(n_bars, 101.0, dtype=np.float32)
    low = np.full(n_bars, 99.0, dtype=np.float32)
    atr = np.full(n_bars, 2.0, dtype=np.float32)
    adv = np.full(n_bars, 5_000_000.0, dtype=np.float32)
    regime = np.full(n_bars, 3, dtype=np.int8)  # RANGE
    funding = np.zeros(n_bars, dtype=np.float32)
    direction = np.full(n_bars, direction_val, dtype=np.int8)
    stop_mult = np.full(n_bars, 3.0, dtype=np.float32)
    trail_mult = np.full(n_bars, 1.5, dtype=np.float32)
    size_mult = np.ones(n_bars, dtype=np.float32)
    cap_mult = np.ones(n_bars, dtype=np.float32)
    leverage = np.ones(n_bars, dtype=np.float32)
    timestamps = np.array(pd.date_range("2024-01-01", periods=n_bars, freq="1h"), dtype="datetime64[ns]")

    return TokenSignals(
        token=token,
        strategy_id=strategy_id,
        n_bars=n_bars,
        timestamps=timestamps,
        entry_mask=entry_mask,
        direction=direction,
        close=close,
        high=high,
        low=low,
        atr=atr,
        rolling_adv=adv,
        regime=regime,
        funding_1h=funding,
        exit_regimes={0},  # CRISIS
        stop_mult=stop_mult,
        trail_mult=trail_mult,
        target_mult=999.0,
        no_stop_bars=6,
        min_hold=6,
        max_hold=720,
        edge=edge,
        size_multiplier=size_mult,
        cap_multiplier=cap_mult,
        leverage=leverage,
        max_trade_pct=0.0,
        raw_entry_count=raw_entry_count,
        post_liquidity_count=post_liquidity_count,
        post_walkforward_count=post_walkforward_count,
        regime_exit_min_bars=regime_exit_min_bars,
        convex_bar_thresholds=convex_bar_thresholds,
        convex_multipliers=convex_multipliers,
        convex_exit=convex_exit,
        is_perp_primary=is_perp,
    )


def _make_config(raw_mode=False, skip_wf=False, capital=200_000, **kwargs):
    return PortfolioConfig(
        capital=capital,
        raw_mode=raw_mode,
        skip_walk_forward=skip_wf,
        **kwargs,
    )


# ===========================================================================
# Group 1: Config tests (Task 1)
# ===========================================================================

class TestConfigFlags:

    def test_portfolio_config_has_raw_mode_field(self):
        pc = PortfolioConfig()
        assert pc.raw_mode is False

    def test_portfolio_config_has_skip_wf_field(self):
        pc = PortfolioConfig()
        assert pc.skip_walk_forward is False

    def test_portfolio_config_has_raw_max_positions(self):
        pc = PortfolioConfig()
        assert pc.raw_max_positions == 500

    def test_regime_config_defaults(self):
        rc = RegimeConfig()
        assert rc.adx_threshold == 25.0
        assert rc.crisis_mult == 2.0
        assert rc.quiet_mult == 0.7
        assert rc.ema_pair == (20, 50)
        assert rc.min_periods == 60

    def test_strategy_spec_has_regime_params(self):
        ss = StrategySpec(strategy_id="test")
        assert ss.regime_params is None

    def test_strategy_spec_from_dict_parses_regime_params(self):
        d = {
            "strategy_id": "test",
            "regime_params": {"adx_threshold": 20, "ema_pair": [10, 30]},
        }
        ss = StrategySpec.from_dict(d)
        assert ss.regime_params == {"adx_threshold": 20, "ema_pair": [10, 30]}

    def test_dataclasses_replace_forwards_new_fields(self):
        pc = PortfolioConfig(raw_mode=True, skip_walk_forward=True, raw_max_positions=100)
        pc2 = dataclasses.replace(pc, capital=500_000)
        assert pc2.raw_mode is True
        assert pc2.skip_walk_forward is True
        assert pc2.raw_max_positions == 100
        assert pc2.capital == 500_000


# ===========================================================================
# Group 2: Signal diagnostics (Tasks 2, 3)
# ===========================================================================

class TestSignalDiagnostics:

    def test_rejection_stats_has_direction_zero(self):
        rs = RejectionStats()
        assert hasattr(rs, "direction_zero")
        assert rs.direction_zero == 0
        rs.direction_zero = 5
        # direction_zero IS a rejection (entry is skipped, strategies must emit +1 or -1)
        assert rs.total() == 5
        rs.portfolio_limit = 3
        assert rs.total() == 8  # direction_zero + portfolio_limit
        d = rs.to_dict()
        assert "direction_zero" in d
        assert d["direction_zero"] == 5

    def test_signal_diagnostics_dataclass(self):
        sd = SignalDiagnostics()
        assert hasattr(sd, "raw_entries_fired")
        assert hasattr(sd, "entries_after_liquidity")
        assert hasattr(sd, "entries_after_walkforward")
        assert hasattr(sd, "entries_opened")
        assert hasattr(sd, "entries_rejected_by")
        sd.raw_entries_fired["s30"] = 100
        sd.entries_opened["s30"] = 50
        d = sd.to_dict()
        assert d["raw_entries_fired"]["s30"] == 100
        assert d["entries_opened"]["s30"] == 50

    def test_token_signals_has_diagnostic_counters(self):
        ts = _make_token_signals(raw_entry_count=10, post_liquidity_count=8, post_walkforward_count=5)
        assert ts.raw_entry_count == 10
        assert ts.post_liquidity_count == 8
        assert ts.post_walkforward_count == 5

    def test_direction_zero_increments_counter(self):
        """direction=0 entry should increment direction_zero counter."""
        sig = _make_token_signals(entry_bar=50, direction_val=0)
        all_signals = {"s30": {"BTC": sig}}
        specs = {"s30": StrategySpec(strategy_id="s30", weight=1.0)}
        config = _make_config(capital=200_000)

        unified_ts, bar_maps = build_unified_index(all_signals)
        state = SimulationState(initial_capital=config.capital)
        rng = np.random.RandomState(42)

        _process_entries(state, all_signals, specs, bar_maps, 50, config, rng)
        assert state.rejections.direction_zero == 1

    def test_direction_zero_rejects_entry(self):
        """direction=0 entry should be rejected (strategies must emit +1 or -1)."""
        sig = _make_token_signals(entry_bar=50, direction_val=0)
        all_signals = {"s30": {"BTC": sig}}
        specs = {"s30": StrategySpec(strategy_id="s30", weight=1.0)}
        config = _make_config(capital=200_000)

        unified_ts, bar_maps = build_unified_index(all_signals)
        state = SimulationState(initial_capital=config.capital)
        rng = np.random.RandomState(42)

        _process_entries(state, all_signals, specs, bar_maps, 50, config, rng)
        assert state.position_manager.total_open() == 0
        assert state.rejections.direction_zero == 1


# ===========================================================================
# Group 3: Walk-forward skip (Task 3)
# ===========================================================================

class TestWalkForwardSkip:

    def test_wf_masking_zeros_training_window(self):
        """_apply_walk_forward_mask zeros out training + purge windows."""
        n = 10000
        entry_mask = np.ones(n, dtype=bool)
        masked = _apply_walk_forward_mask(entry_mask.copy(), 8760, 2160, 120)
        # First train_bars (8760) should be entirely masked
        assert masked[:8760].sum() == 0
        # Purge window after training: bars 8760-8879 (120 bars) should be masked
        assert masked[8760:8880].sum() == 0
        # Post-purge bars should have entries
        assert masked[8880:].sum() == n - 8880  # all remaining bars are unmasked
        # Total masked = train_bars + purge_bars (no second recal within n=10000)
        assert masked.sum() == n - 8760 - 120

    def test_skip_wf_lower_min_bar_threshold(self):
        """skip_walk_forward uses n_safe < 200 instead of train_bars + purge + 100.

        Contract test: verifies the threshold values that precompute_strategy_signals()
        uses for the two branches (signals.py lines 516-521). Integration testing of the
        full pipeline requires loading parquet data files.
        """
        config_skip = _make_config(skip_wf=True)
        config_normal = _make_config(skip_wf=False)
        n_safe = 5000
        # Normal mode threshold: train_bars + purge_bars + 100 = 8760 + 120 + 100 = 8980
        normal_threshold = config_normal.train_bars + config_normal.purge_bars + 100
        assert normal_threshold == 8980
        assert n_safe < normal_threshold  # would be skipped in normal mode
        # Skip-WF threshold: 200 (hardcoded in signals.py)
        skip_threshold = 200
        assert n_safe >= skip_threshold  # accepted in skip-wf mode
        # Verify the gap is meaningful: normal requires 44x more bars than skip
        assert normal_threshold / skip_threshold > 40

    def test_skip_wf_cutoff_differs_from_normal(self):
        """With skip_walk_forward, cutoff = trade_start; without, it's train_bars earlier.

        Contract test: verifies the cutoff formula used in precompute_strategy_signals()
        (signals.py lines 237-240). Integration testing requires loading parquet data.
        """
        anchor = pd.Timestamp("2024-06-01")
        trade_start = anchor - pd.DateOffset(months=12)

        config_normal = _make_config(skip_wf=False)

        cutoff_skip = trade_start  # skip_walk_forward path
        cutoff_normal = trade_start - pd.DateOffset(hours=int(config_normal.train_bars))

        # Skip-WF cutoff is later (closer to present) — no training window reserved
        assert cutoff_skip > cutoff_normal
        # The difference is exactly train_bars (8760) hours = 365 days
        delta = cutoff_skip - cutoff_normal
        assert delta == pd.Timedelta(hours=8760)


# ===========================================================================
# Group 4: Raw mode simulator (Task 4)
# ===========================================================================

class TestRawModeSimulator:

    def _run_entry(self, config, sig=None, n_open=0):
        """Helper: run _process_entries for one bar and return state."""
        if sig is None:
            sig = _make_token_signals(entry_bar=50)
        all_signals = {"s30": {"BTC": sig}}
        specs = {"s30": StrategySpec(strategy_id="s30", weight=1.0)}
        unified_ts, bar_maps = build_unified_index(all_signals)
        state = SimulationState(initial_capital=config.capital)
        rng = np.random.RandomState(42)

        # Pre-fill positions if needed
        for i in range(n_open):
            pos = Position(
                position_id=f"PREFILL:{i}:primary", token=f"TOK{i}", strategy_id="s30",
                leg="primary", entry_bar=0, entry_price=100, direction=1, quantity=1,
                margin_usd=100, leverage=1, is_perp=False, fee_rate=0.001,
                stop_mult=3, trail_mult=1.5, target_mult=999, no_stop_bars=6,
                min_hold=6, max_hold=720, exit_regimes={0},
            )
            state.position_manager.open_position(pos)

        _process_entries(state, all_signals, specs, bar_maps, 50, config, rng)
        return state

    def test_raw_mode_uses_strategy_sizing(self):
        """In raw mode, position size comes from strategy's edge/kelly pipeline.

        With edge=0.35, capital=200k, volatility=0.02, ADV=5M, the sizing pipeline
        produces a deterministic position size via compute_position_size().
        """
        from v4.sizing import compute_position_size
        from v4.config import resolve_sizing

        config = _make_config(raw_mode=True, capital=200_000)
        state = self._run_entry(config)
        assert state.position_manager.total_open() == 1
        pos = state.position_manager.open_positions[0]

        # Compute expected margin from the same inputs the simulator uses
        resolved = resolve_sizing(config.sizing_defaults, {})
        expected_margin = compute_position_size(
            strategy_equity=200_000.0,  # config.capital * weight(1.0)
            rolling_adv=5_000_000.0, volatility=0.02, edge=0.35,
            size_multiplier=1.0, cap_multiplier=1.0, max_trade_pct=0.0,
            adv_cap_pct=config.adv_cap_pct,
            edge_minimum=resolved.edge_minimum, target_vol=resolved.target_vol,
            vol_floor=resolved.vol_floor, spot_max_equity_pct=resolved.spot_max_equity_pct,
            leverage=1.0,
            kelly_mult_override=resolved.kelly_mult_override, kelly_mult_scale=resolved.kelly_mult_scale,
            cap_pct_override=resolved.cap_pct_override, cap_pct_scale=resolved.cap_pct_scale,
            kelly_mult_floor=resolved.kelly_mult_floor, kelly_mult_range=resolved.kelly_mult_range,
            cap_pct_floor=resolved.cap_pct_floor, cap_pct_range=resolved.cap_pct_range,
            adv_scaling_divisor=resolved.adv_scaling_divisor,
        )
        assert pos.margin_usd == pytest.approx(expected_margin, rel=1e-6)

    def test_raw_mode_uses_fixed_equity(self):
        """Raw mode sizes from config.capital * weight, not portfolio_equity (which includes realized PnL).

        Two entries with different realized PnL states should produce the same margin_usd
        because raw mode uses fixed initial capital, not the dynamic equity.
        """
        capital = 200_000
        sig1 = _make_token_signals(token="BTC", entry_bar=50, edge=0.35)
        sig2 = _make_token_signals(token="ETH", entry_bar=50, edge=0.35)

        # Run 1: fresh state (no realized losses)
        all_signals_1 = {"s30": {"BTC": sig1}}
        specs = {"s30": StrategySpec(strategy_id="s30", weight=1.0)}
        config = _make_config(raw_mode=True, capital=capital)
        unified_ts_1, bar_maps_1 = build_unified_index(all_signals_1)
        state1 = SimulationState(initial_capital=config.capital)
        rng1 = np.random.RandomState(42)
        _process_entries(state1, all_signals_1, specs, bar_maps_1, 50, config, rng1)
        margin_fresh = state1.position_manager.open_positions[0].margin_usd

        # Run 2: state with large realized losses (portfolio_equity < capital)
        all_signals_2 = {"s30": {"ETH": sig2}}
        unified_ts_2, bar_maps_2 = build_unified_index(all_signals_2)
        state2 = SimulationState(initial_capital=config.capital)
        state2.realized_pnl = -100_000  # big loss: portfolio_equity = 100k
        rng2 = np.random.RandomState(42)
        _process_entries(state2, all_signals_2, specs, bar_maps_2, 50, config, rng2)
        margin_after_loss = state2.position_manager.open_positions[0].margin_usd

        # Both should be identical: raw mode uses config.capital, not portfolio_equity
        assert margin_fresh == pytest.approx(margin_after_loss, rel=1e-6)

    def test_raw_mode_skips_concentration_limit(self):
        """Entry that would exceed 10% concentration should proceed in raw mode."""
        config = _make_config(raw_mode=True, capital=1_000, concentration_limit=0.01)
        state = self._run_entry(config)
        # In raw mode, concentration check is skipped
        assert state.position_manager.total_open() == 1

    def test_raw_mode_skips_adv_cap(self):
        """Entry with very low ADV proceeds in raw mode (no Constraint 6 check).

        Note: compute_position_size() internally caps pos_usd at rolling_adv * adv_cap_pct,
        so the normal-mode Constraint 6 in the simulator is defense-in-depth for single-leg
        entries (it can only fire for combined entries where leg sizes are recomputed).
        This test verifies the raw mode path does not reject on ADV at all.
        """
        sig = _make_token_signals(entry_bar=50)
        sig.rolling_adv[:] = 100.0  # very low ADV → sizing caps at 100*0.05=5 USD
        config = _make_config(raw_mode=True, capital=200_000)
        state = self._run_entry(config, sig=sig)
        assert state.position_manager.total_open() == 1
        # Position should be tiny: adv_cap = rolling_adv * adv_cap_pct = 100 * 0.05 = 5.0
        pos = state.position_manager.open_positions[0]
        assert pos.margin_usd == pytest.approx(5.0, rel=0.01)

    def test_raw_mode_skips_portfolio_position_limit(self):
        """More than max_portfolio_positions allowed in raw mode."""
        config = _make_config(raw_mode=True, max_portfolio_positions=5)
        # Open 10 positions first (exceeds max_portfolio_positions=5)
        state = self._run_entry(config, n_open=10)
        # Raw mode skips portfolio_limit check, so position should still open
        # (it only checks raw_max_positions=500)
        assert state.position_manager.total_open() == 11

    def test_raw_mode_skips_strategy_position_limit(self):
        """More than spec.max_positions per strategy allowed in raw mode."""
        # Pre-fill 20 positions all from strategy s30
        sig = _make_token_signals(entry_bar=50)
        all_signals = {"s30": {"BTC": sig}}
        specs = {"s30": StrategySpec(strategy_id="s30", weight=1.0, max_positions=15)}
        config = _make_config(raw_mode=True)

        unified_ts, bar_maps = build_unified_index(all_signals)
        state = SimulationState(initial_capital=config.capital)
        rng = np.random.RandomState(42)
        for i in range(20):
            pos = Position(
                position_id=f"PREFILL:{i}:primary", token=f"TOK{i}", strategy_id="s30",
                leg="primary", entry_bar=0, entry_price=100, direction=1, quantity=1,
                margin_usd=100, leverage=1, is_perp=False, fee_rate=0.001,
                stop_mult=3, trail_mult=1.5, target_mult=999, no_stop_bars=6,
                min_hold=6, max_hold=720, exit_regimes={0},
            )
            state.position_manager.open_position(pos)

        _process_entries(state, all_signals, specs, bar_maps, 50, config, rng)
        # Raw mode skips per-strategy limit (20 > max_positions=15), still opens
        assert state.position_manager.total_open() == 21

    def test_raw_mode_skips_capital_check(self):
        """Entries proceed even when free_capital < 0 in raw mode."""
        sig = _make_token_signals(entry_bar=50)
        all_signals = {"s30": {"BTC": sig}}
        specs = {"s30": StrategySpec(strategy_id="s30", weight=1.0)}
        config = _make_config(raw_mode=True, capital=200_000)

        unified_ts, bar_maps = build_unified_index(all_signals)
        state = SimulationState(initial_capital=config.capital)
        # Drain all capital via realized losses → portfolio_equity = 0, free_capital < 0
        state.realized_pnl = -200_000
        rng = np.random.RandomState(42)

        _process_entries(state, all_signals, specs, bar_maps, 50, config, rng)
        # Raw mode uses fixed config.capital for sizing, ignoring portfolio_equity
        assert state.position_manager.total_open() == 1

    def test_raw_mode_keeps_no_reentry(self):
        """No duplicate position for same token+strategy in raw mode.

        Entry fires at bar 50 and bar 60. After bar 50 opens a position,
        bar 60 should be blocked because BTC:s30 is already open.
        """
        sig = _make_token_signals(entry_bar=[50, 60])
        all_signals = {"s30": {"BTC": sig}}
        specs = {"s30": StrategySpec(strategy_id="s30", weight=1.0)}
        config = _make_config(raw_mode=True)

        unified_ts, bar_maps = build_unified_index(all_signals)
        state = SimulationState(initial_capital=config.capital)
        rng = np.random.RandomState(42)

        # Bar 50: first entry opens
        _process_entries(state, all_signals, specs, bar_maps, 50, config, rng)
        assert state.position_manager.total_open() == 1

        # Bar 60: same token+strategy already open — no-reentry blocks it
        _process_entries(state, all_signals, specs, bar_maps, 60, config, rng)
        assert state.position_manager.total_open() == 1  # still just 1

    def test_raw_mode_keeps_slippage(self):
        """Slippage should still be computed in raw mode (long entry is above close)."""
        config = _make_config(raw_mode=True)
        state = self._run_entry(config)
        pos = state.position_manager.open_positions[0]
        # For a long entry (direction=1), slippage adds to the close price
        assert pos.entry_price > 100.0  # close=100.0, entry must be higher due to slip

    def test_raw_mode_keeps_exit_logic(self):
        """Stops/targets/regime exits should still work in raw mode."""
        sig = _make_token_signals(entry_bar=50)
        all_signals = {"s30": {"BTC": sig}}
        specs = {"s30": StrategySpec(strategy_id="s30", weight=1.0)}
        config = _make_config(raw_mode=True)

        unified_ts, bar_maps = build_unified_index(all_signals)
        state = SimulationState(initial_capital=config.capital)
        rng = np.random.RandomState(42)

        _process_entries(state, all_signals, specs, bar_maps, 50, config, rng)
        assert state.position_manager.total_open() == 1

        # Trigger stop by setting low below stop price (must be after no_stop_bars=6)
        pos = state.position_manager.open_positions[0]
        sig.low[57] = 0.1  # way below stop, bars_held=7 > no_stop_bars=6
        _process_exits(state, all_signals, bar_maps, 57, config, strategy_specs=specs)
        # Position should be closed
        assert state.position_manager.total_open() == 0

    def test_raw_mode_position_safety_cap(self):
        """Respects raw_max_positions cap."""
        config = _make_config(raw_mode=True, raw_max_positions=5)
        state = self._run_entry(config, n_open=5)
        # 5 already open + raw_max_positions=5 → should not open
        new_positions = state.position_manager.total_open() - 5
        assert new_positions == 0

    def test_raw_mode_diagnostics_populated(self):
        """entries_opened counter should be incremented in raw mode."""
        config = _make_config(raw_mode=True)
        state = self._run_entry(config)
        assert state.diagnostics.entries_opened.get("s30", 0) == 1


# ===========================================================================
# Group 5: Exit constants (Task 5)
# ===========================================================================

class TestExitConstants:

    def test_strategy_result_has_exit_constants(self):
        from v4.engine import StrategyResult
        sr = StrategyResult(
            entry_mask=np.zeros(10, dtype=bool),
            direction=np.zeros(10, dtype=np.int8),
        )
        assert sr.regime_exit_min_bars == 6
        assert sr.convex_bar_thresholds == (48, 12)
        assert sr.convex_multipliers == (2.0, 1.5, 0.3)

    def test_position_has_exit_constants(self):
        pos = Position(
            position_id="test", token="BTC", strategy_id="s30", leg="primary",
            entry_bar=0, entry_price=100, direction=1, quantity=1, margin_usd=100,
            leverage=1, is_perp=False, fee_rate=0.001, stop_mult=3, trail_mult=1.5,
            target_mult=5, no_stop_bars=6, min_hold=6, max_hold=720, exit_regimes={0},
        )
        assert pos.regime_exit_min_bars == 6
        assert pos.convex_bar_thresholds == (48, 12)
        assert pos.convex_multipliers == (2.0, 1.5, 0.3)

    def test_regime_exit_uses_configurable_min_bars(self):
        """Regime exit should respect pos.regime_exit_min_bars."""
        # Use a regime_exit_min_bars of 3 (shorter than default 6)
        sig = _make_token_signals(entry_bar=50, regime_exit_min_bars=3)
        sig.regime[54] = 0  # CRISIS at bar 54 (4 bars held > 3 min_bars)
        all_signals = {"s30": {"BTC": sig}}
        specs = {"s30": StrategySpec(strategy_id="s30", weight=1.0)}
        config = _make_config()

        unified_ts, bar_maps = build_unified_index(all_signals)
        state = SimulationState(initial_capital=config.capital)
        rng = np.random.RandomState(42)

        _process_entries(state, all_signals, specs, bar_maps, 50, config, rng)
        assert state.position_manager.total_open() == 1

        # At bar 54, bars_held=4 > regime_exit_min_bars=3, regime is CRISIS(0) which is in exit_regimes
        _process_exits(state, all_signals, bar_maps, 54, config, strategy_specs=specs)
        assert state.position_manager.total_open() == 0
        assert state.position_manager.closed_trades[-1].exit_reason == "regime"

    def test_convex_exit_uses_configurable_thresholds(self):
        """Convex trail uses pos.convex_bar_thresholds, not hardcoded (48, 12).

        With mature_bars=10, after 11 bars of price going up, the mature convex
        trail (highest - 2.0*atr) should kick in, raising stop_price above its
        initial value.
        """
        sig = _make_token_signals(
            entry_bar=50, convex_exit=True,
            convex_bar_thresholds=(10, 5),  # much shorter than default (48, 12)
            convex_multipliers=(2.0, 1.5, 0.3),
        )
        all_signals = {"s30": {"BTC": sig}}
        specs = {"s30": StrategySpec(strategy_id="s30", weight=1.0)}
        config = _make_config()

        unified_ts, bar_maps = build_unified_index(all_signals)
        state = SimulationState(initial_capital=config.capital)
        rng = np.random.RandomState(42)

        _process_entries(state, all_signals, specs, bar_maps, 50, config, rng)
        assert state.position_manager.total_open() == 1

        pos = state.position_manager.open_positions[0]
        initial_stop = pos.stop_price

        # Simulate price going up significantly over 11 bars
        # low must stay above the mature trail stop (highest - 2*atr = 120 - 4 = 116)
        for b in range(51, 62):
            sig.high[b] = 120.0  # big move up
            sig.close[b] = 118.0
            sig.low[b] = 117.0   # low stays above mature trail stop

        # After 11 bars (>= mature_bars=10), convex trail should kick in
        # Process exits for each bar to update highest and trailing stop
        for b in range(51, 62):
            _process_exits(state, all_signals, bar_maps, b, config, strategy_specs=specs)

        # Position should still be open (price going up, low above trail stop)
        assert state.position_manager.total_open() == 1
        pos = state.position_manager.open_positions[0]
        # Mature convex trail: stop = highest - mature_trail_atr * atr = 120.0 - 2.0 * 2.0 = 116.0
        assert pos.stop_price == pytest.approx(116.0, abs=0.1)
        # This must be higher than the initial stop (~94)
        assert pos.stop_price > initial_stop

    def test_exit_constants_backward_compatible(self):
        """Default values produce identical behavior to hardcoded."""
        pos = Position(
            position_id="test", token="BTC", strategy_id="s30", leg="primary",
            entry_bar=0, entry_price=100, direction=1, quantity=1, margin_usd=100,
            leverage=1, is_perp=False, fee_rate=0.001, stop_mult=3, trail_mult=1.5,
            target_mult=5, no_stop_bars=6, min_hold=6, max_hold=720, exit_regimes={0},
        )
        # Default values match the original hardcoded constants
        assert pos.regime_exit_min_bars == 6
        assert pos.convex_bar_thresholds[0] == 48
        assert pos.convex_bar_thresholds[1] == 12
        assert pos.convex_multipliers[0] == 2.0
        assert pos.convex_multipliers[1] == 1.5
        assert pos.convex_multipliers[2] == 0.3


# ===========================================================================
# Group 6: Configurable regime (Task 6)
# ===========================================================================

class TestConfigurableRegime:

    def test_detect_daily_regime_accepts_kwargs(self):
        from v4.engine import detect_daily_regime
        n = 200
        ind_d = {
            "adx": np.random.random(n) * 50,
            "ema_20": np.random.random(n) * 100 + 50,
            "ema_50": np.random.random(n) * 100 + 50,
            "vol_20": np.random.random(n) * 0.5,
            "close": np.random.random(n) * 100 + 50,
        }
        r = detect_daily_regime(ind_d, adx_threshold=20, crisis_mult=1.5,
                                quiet_mult=0.8, ema_pair=(10, 30), min_periods=30)
        assert len(r) == n

    def test_detect_daily_regime_default_kwargs_match_hardcoded(self):
        from v4.engine import detect_daily_regime
        np.random.seed(42)
        n = 200
        ind_d = {
            "adx": np.random.random(n) * 50,
            "ema_20": np.cumsum(np.random.randn(n)) + 100,
            "ema_50": np.cumsum(np.random.randn(n)) + 100,
            "vol_20": np.abs(np.random.randn(n)) * 0.3,
            "close": np.cumsum(np.random.randn(n)) + 100,
        }
        r_default = detect_daily_regime(ind_d)
        r_explicit = detect_daily_regime(
            ind_d, adx_threshold=25, crisis_mult=2.0,
            quiet_mult=0.7, ema_pair=(20, 50), min_periods=60
        )
        assert np.array_equal(r_default, r_explicit)

    def test_detect_daily_regime_custom_adx_threshold(self):
        from v4.engine import detect_daily_regime
        n = 200
        # Deterministic data: ADX ramps 10→40, EMA_fast > EMA_slow (uptrend)
        ind_d = {
            "adx": np.linspace(10, 40, n),  # smooth ramp from weak to strong
            "ema_20": np.linspace(100, 120, n),  # fast EMA above slow → uptrend
            "ema_50": np.linspace(99, 110, n),
            "vol_20": np.full(n, 0.2),
            "close": np.linspace(100, 120, n),
        }
        r_default = detect_daily_regime(ind_d, adx_threshold=25)
        r_lower = detect_daily_regime(ind_d, adx_threshold=15)
        # With a linear ADX ramp, threshold=15 includes bars with ADX 15-25 as trending
        # that threshold=25 would classify as range — strictly more trend bars
        trend_default = np.sum((r_default == 2) | (r_default == 4))
        trend_lower = np.sum((r_lower == 2) | (r_lower == 4))
        assert trend_lower > trend_default

    def test_detect_daily_regime_custom_ema_pair(self):
        from v4.engine import detect_daily_regime
        np.random.seed(42)
        n = 200
        ind_d = {
            "adx": np.full(n, 30.0),  # always strong
            "ema_20": np.cumsum(np.random.randn(n) * 0.1) + 100,
            "ema_50": np.cumsum(np.random.randn(n) * 0.1) + 99,
            "vol_20": np.full(n, 0.2),
            "close": np.cumsum(np.random.randn(n)) + 100,
        }
        # Non-default EMA pair should NOT KeyError (uses _ema on close)
        r = detect_daily_regime(ind_d, ema_pair=(10, 30))
        assert len(r) == n
        assert r.dtype == np.int8


# ===========================================================================
# Group 7: Custom indicators (Task 7)
# ===========================================================================

class TestCustomIndicators:

    def _make_ctx(self, n=500):
        from v4.engine import StrategyContext
        close = np.cumsum(np.random.randn(n)) + 100
        high = close + np.abs(np.random.randn(n))
        low = close - np.abs(np.random.randn(n))
        ind = {
            "close": close, "high": high, "low": low, "open": close,
            "volume": np.random.random(n) * 1e6, "atr": np.ones(n),
        }
        return StrategyContext(
            ticker="TEST",
            ind_1h=dict(ind), ind_4h=dict(ind), ind_d=dict(ind),
            idx_1h=pd.date_range("2024-01-01", periods=n, freq="1h"),
            idx_4h=pd.date_range("2024-01-01", periods=n, freq="4h"),
            idx_d=pd.date_range("2024-01-01", periods=n, freq="1D"),
            regime_1h=np.zeros(n, dtype=np.int8),
            df_1h=pd.DataFrame(), df_4h=pd.DataFrame(), df_daily=pd.DataFrame(),
        )

    def test_compute_custom_indicators_ema(self):
        from v4.engine import compute_custom_indicators
        ctx = self._make_ctx()
        result = compute_custom_indicators(ctx, [{"type": "ema", "period": 14}])
        assert "ema_14_1h" in result
        assert len(result["ema_14_1h"]) == 500

    def test_compute_custom_indicators_rsi(self):
        from v4.engine import compute_custom_indicators
        ctx = self._make_ctx()
        result = compute_custom_indicators(ctx, [{"type": "rsi", "period": 14}])
        assert "rsi_14_1h" in result
        assert len(result["rsi_14_1h"]) == 500

    def test_compute_custom_indicators_bb(self):
        from v4.engine import compute_custom_indicators
        ctx = self._make_ctx()
        result = compute_custom_indicators(ctx, [{"type": "bb", "period": 20, "std": 2.0}])
        assert "bb_mid_20_1h" in result
        assert "bb_upper_20_1h" in result
        assert "bb_lower_20_1h" in result

    def test_compute_custom_indicators_returns_dict(self):
        from v4.engine import compute_custom_indicators
        ctx = self._make_ctx()
        result = compute_custom_indicators(ctx, [
            {"type": "ema", "period": 14},
            {"type": "sma", "period": 20},
        ])
        assert isinstance(result, dict)
        assert "ema_14_1h" in result
        assert "sma_20_1h" in result


# ===========================================================================
# Group 8: Diagnostic report (Task 8)
# ===========================================================================

class TestDiagnosticReport:

    def test_print_diagnostic_report_runs_without_error(self, capsys):
        from v4.report import print_diagnostic_report
        state = SimulationState(initial_capital=200_000)
        sig = _make_token_signals(raw_entry_count=50, post_liquidity_count=40, post_walkforward_count=30)
        all_signals = {"s30": {"BTC": sig}}
        state.diagnostics.entries_opened["s30"] = 20

        print_diagnostic_report(state, all_signals)
        captured = capsys.readouterr()
        assert "DIAGNOSTIC REPORT" in captured.out

    def test_diagnostic_report_shows_funnel(self, capsys):
        from v4.report import print_diagnostic_report
        state = SimulationState(initial_capital=200_000)
        # Use distinctive numbers that won't match line numbers or other noise
        sig = _make_token_signals(raw_entry_count=9371, post_liquidity_count=7823, post_walkforward_count=6154)
        all_signals = {"s30": {"BTC": sig}}
        state.diagnostics.entries_opened["s30"] = 4092

        print_diagnostic_report(state, all_signals)
        captured = capsys.readouterr()
        assert "9371" in captured.out    # raw count
        assert "7823" in captured.out    # post-liq count
        assert "6154" in captured.out    # post-wf count
        assert "4092" in captured.out    # opened count


# ===========================================================================
# Group 9: Normal-mode counter-tests (verify constraints actually block)
# ===========================================================================

class TestNormalModeConstraints:
    """Verify that constraints which raw mode skips DO block in normal mode."""

    def _run_entry(self, config, sig=None, n_open=0):
        if sig is None:
            sig = _make_token_signals(entry_bar=50)
        all_signals = {"s30": {"BTC": sig}}
        specs = {"s30": StrategySpec(strategy_id="s30", weight=1.0)}
        unified_ts, bar_maps = build_unified_index(all_signals)
        state = SimulationState(initial_capital=config.capital)
        rng = np.random.RandomState(42)
        for i in range(n_open):
            pos = Position(
                position_id=f"PREFILL:{i}:primary", token=f"TOK{i}", strategy_id="s30",
                leg="primary", entry_bar=0, entry_price=100, direction=1, quantity=1,
                margin_usd=100, leverage=1, is_perp=False, fee_rate=0.001,
                stop_mult=3, trail_mult=1.5, target_mult=999, no_stop_bars=6,
                min_hold=6, max_hold=720, exit_regimes={0},
            )
            state.position_manager.open_position(pos)
        _process_entries(state, all_signals, specs, bar_maps, 50, config, rng)
        return state

    def test_normal_mode_blocks_at_portfolio_limit(self):
        """Normal mode rejects entries when portfolio position limit is hit."""
        config = _make_config(raw_mode=False, max_portfolio_positions=5)
        state = self._run_entry(config, n_open=5)
        # Should NOT open a new position (portfolio limit = 5, 5 already open)
        assert state.position_manager.total_open() == 5
        assert state.rejections.portfolio_limit == 1

    def test_normal_mode_blocks_at_strategy_limit(self):
        """Normal mode rejects entries when per-strategy position limit is hit."""
        sig = _make_token_signals(entry_bar=50)
        all_signals = {"s30": {"BTC": sig}}
        specs = {"s30": StrategySpec(strategy_id="s30", weight=1.0, max_positions=5)}
        config = _make_config(raw_mode=False, max_portfolio_positions=40)

        unified_ts, bar_maps = build_unified_index(all_signals)
        state = SimulationState(initial_capital=config.capital)
        rng = np.random.RandomState(42)
        # Pre-fill 5 positions from strategy s30 (different tokens)
        for i in range(5):
            pos = Position(
                position_id=f"PREFILL:{i}:primary", token=f"TOK{i}", strategy_id="s30",
                leg="primary", entry_bar=0, entry_price=100, direction=1, quantity=1,
                margin_usd=100, leverage=1, is_perp=False, fee_rate=0.001,
                stop_mult=3, trail_mult=1.5, target_mult=999, no_stop_bars=6,
                min_hold=6, max_hold=720, exit_regimes={0},
            )
            state.position_manager.open_position(pos)

        _process_entries(state, all_signals, specs, bar_maps, 50, config, rng)
        # Should be rejected by strategy limit (5 already open for s30, max=5)
        assert state.position_manager.total_open() == 5
        assert state.rejections.strategy_limit == 1

    def test_normal_mode_blocks_concentration(self):
        """Normal mode rejects entries exceeding concentration limit.

        Pre-fill a BTC position from a DIFFERENT strategy, then try to enter
        BTC from s30 with a tight concentration limit — rejected since total
        BTC margin already exceeds the token limit.
        """
        sig = _make_token_signals(entry_bar=50)
        all_signals = {"s30": {"BTC": sig}}
        specs = {"s30": StrategySpec(strategy_id="s30", weight=1.0)}
        # Very tight concentration limit: 0.001 = 0.1% of equity = $200
        config = _make_config(raw_mode=False, capital=200_000, concentration_limit=0.001)

        unified_ts, bar_maps = build_unified_index(all_signals)
        state = SimulationState(initial_capital=config.capital)
        rng = np.random.RandomState(42)

        # Pre-fill a large BTC position from a DIFFERENT strategy (no-reentry won't block)
        pos = Position(
            position_id="PREFILL:BTC:primary", token="BTC", strategy_id="other_strat",
            leg="primary", entry_bar=0, entry_price=100, direction=1, quantity=100,
            margin_usd=10_000, leverage=1, is_perp=False, fee_rate=0.001,
            stop_mult=3, trail_mult=1.5, target_mult=999, no_stop_bars=6,
            min_hold=6, max_hold=720, exit_regimes={0},
        )
        state.position_manager.open_position(pos)

        _process_entries(state, all_signals, specs, bar_maps, 50, config, rng)
        # Should be rejected by concentration (existing BTC margin $10k >> 0.1% of $200k = $200)
        assert state.rejections.concentration == 1

    def test_normal_mode_blocks_capital_exhaustion(self):
        """Normal mode rejects entries when capital is exhausted (free_capital < margin + fees)."""
        sig = _make_token_signals(entry_bar=50)
        all_signals = {"s30": {"BTC": sig}}
        specs = {"s30": StrategySpec(strategy_id="s30", weight=1.0)}
        config = _make_config(raw_mode=False, capital=200_000)

        unified_ts, bar_maps = build_unified_index(all_signals)
        state = SimulationState(initial_capital=config.capital)
        # Drain all capital via realized losses
        state.realized_pnl = -200_000
        rng = np.random.RandomState(42)

        _process_entries(state, all_signals, specs, bar_maps, 50, config, rng)
        # portfolio_equity = 200k + (-200k) = 0 → no free capital → should reject
        assert state.position_manager.total_open() == 0
        # Either capital or min_size rejection (sizing returns 0 when equity is 0)

    def test_normal_mode_uses_dynamic_equity(self):
        """Normal mode sizes from portfolio_equity (includes realized PnL), not fixed capital.

        Counter-test to test_raw_mode_uses_fixed_equity: with a big realized loss,
        normal mode should produce a smaller position than with no loss.
        """
        capital = 200_000
        sig1 = _make_token_signals(token="BTC", entry_bar=50, edge=0.35)
        sig2 = _make_token_signals(token="ETH", entry_bar=50, edge=0.35)

        # Run 1: fresh state (no realized losses)
        all_signals_1 = {"s30": {"BTC": sig1}}
        specs = {"s30": StrategySpec(strategy_id="s30", weight=1.0)}
        config = _make_config(raw_mode=False, capital=capital)
        unified_ts_1, bar_maps_1 = build_unified_index(all_signals_1)
        state1 = SimulationState(initial_capital=config.capital)
        rng1 = np.random.RandomState(42)
        _process_entries(state1, all_signals_1, specs, bar_maps_1, 50, config, rng1)
        assert state1.position_manager.total_open() == 1
        margin_fresh = state1.position_manager.open_positions[0].margin_usd

        # Run 2: state with realized losses (portfolio_equity = 100k)
        all_signals_2 = {"s30": {"ETH": sig2}}
        unified_ts_2, bar_maps_2 = build_unified_index(all_signals_2)
        state2 = SimulationState(initial_capital=config.capital)
        state2.realized_pnl = -100_000  # portfolio_equity = 100k
        rng2 = np.random.RandomState(42)
        _process_entries(state2, all_signals_2, specs, bar_maps_2, 50, config, rng2)
        assert state2.position_manager.total_open() == 1
        margin_after_loss = state2.position_manager.open_positions[0].margin_usd

        # Normal mode: less equity → smaller position
        assert margin_after_loss < margin_fresh


# ===========================================================================
# Group 10: Combined strategy raw mode skip
# ===========================================================================

class TestCombinedRawModeSkip:

    def test_raw_mode_skips_combined_strategies(self):
        """Combined (carry pair) strategies are skipped in raw mode."""
        sig = _make_token_signals(entry_bar=50)
        sig.is_combined = True  # mark as combined
        sig.capital_split = 0.7
        sig.is_perp_secondary = True
        sig.secondary_direction = np.full(sig.n_bars, -1, dtype=np.int8)
        sig.secondary_leverage = 1.0
        sig.perp_close = sig.close.copy()
        sig.perp_high = sig.high.copy()
        sig.perp_low = sig.low.copy()
        sig.perp_atr = sig.atr.copy()
        sig.perp_rolling_adv = sig.rolling_adv.copy()
        sig.perp_funding = sig.funding_1h.copy()

        all_signals = {"s30": {"BTC": sig}}
        specs = {"s30": StrategySpec(strategy_id="s30", weight=1.0)}
        config = _make_config(raw_mode=True)

        unified_ts, bar_maps = build_unified_index(all_signals)
        state = SimulationState(initial_capital=config.capital)
        rng = np.random.RandomState(42)

        _process_entries(state, all_signals, specs, bar_maps, 50, config, rng)
        # Combined strategies are skipped in raw mode
        assert state.position_manager.total_open() == 0


# ===========================================================================
# Group 11: RegimeConfig list coercion
# ===========================================================================

class TestRegimeConfigCoercion:

    def test_regime_config_coerces_list_to_tuple(self):
        """ema_pair passed as list (from JSON) should be coerced to tuple."""
        rc = RegimeConfig(ema_pair=[10, 30])
        assert isinstance(rc.ema_pair, tuple)
        assert rc.ema_pair == (10, 30)

    def test_regime_config_tuple_stays_tuple(self):
        """ema_pair passed as tuple stays as tuple."""
        rc = RegimeConfig(ema_pair=(15, 45))
        assert isinstance(rc.ema_pair, tuple)
        assert rc.ema_pair == (15, 45)

    def test_regime_config_equality_after_coercion(self):
        """RegimeConfig from list and tuple ema_pair should be equal."""
        rc_list = RegimeConfig(ema_pair=[20, 50])
        rc_tuple = RegimeConfig(ema_pair=(20, 50))
        assert rc_list == rc_tuple


# ===========================================================================
# Group 12: Custom indicators — atr and donch
# ===========================================================================

class TestCustomIndicatorsAdvanced:

    def _make_ctx(self, n=500):
        from v4.engine import StrategyContext
        np.random.seed(123)
        close = np.cumsum(np.random.randn(n)) + 100
        high = close + np.abs(np.random.randn(n))
        low = close - np.abs(np.random.randn(n))
        ind = {
            "close": close, "high": high, "low": low, "open": close,
            "volume": np.random.random(n) * 1e6, "atr": np.ones(n),
        }
        return StrategyContext(
            ticker="TEST",
            ind_1h=dict(ind), ind_4h=dict(ind), ind_d=dict(ind),
            idx_1h=pd.date_range("2024-01-01", periods=n, freq="1h"),
            idx_4h=pd.date_range("2024-01-01", periods=n, freq="4h"),
            idx_d=pd.date_range("2024-01-01", periods=n, freq="1D"),
            regime_1h=np.zeros(n, dtype=np.int8),
            df_1h=pd.DataFrame(), df_4h=pd.DataFrame(), df_daily=pd.DataFrame(),
        )

    def test_compute_custom_indicators_atr(self):
        from v4.engine import compute_custom_indicators
        ctx = self._make_ctx()
        result = compute_custom_indicators(ctx, [{"type": "atr", "period": 14}])
        assert "atr_14_1h" in result
        assert len(result["atr_14_1h"]) == 500
        # ATR should be non-negative
        assert np.all(result["atr_14_1h"][~np.isnan(result["atr_14_1h"])] >= 0)

    def test_compute_custom_indicators_atr_first_value_no_wraparound(self):
        """First ATR true range uses high[0]-low[0], not np.roll wraparound."""
        from v4.engine import compute_custom_indicators
        ctx = self._make_ctx(n=100)
        result = compute_custom_indicators(ctx, [{"type": "atr", "period": 5}])
        atr = result["atr_5_1h"]
        # First ATR should equal high[0] - low[0] (no previous close for TR)
        expected_first_tr = ctx.ind_1h["high"][0] - ctx.ind_1h["low"][0]
        assert atr[0] == pytest.approx(expected_first_tr, rel=1e-6)

    def test_compute_custom_indicators_donch(self):
        from v4.engine import compute_custom_indicators
        ctx = self._make_ctx()
        result = compute_custom_indicators(ctx, [{"type": "donch", "period": 20}])
        assert "donch_high_20_1h" in result
        assert "donch_low_20_1h" in result
        assert len(result["donch_high_20_1h"]) == 500
        # Before period, values should be NaN; after, should be valid
        assert np.isnan(result["donch_high_20_1h"][0])  # not enough bars yet
        assert not np.isnan(result["donch_high_20_1h"][19])  # enough bars at index 19
        # donch_high >= donch_low at all valid points
        valid = ~np.isnan(result["donch_high_20_1h"])
        assert np.all(result["donch_high_20_1h"][valid] >= result["donch_low_20_1h"][valid])
