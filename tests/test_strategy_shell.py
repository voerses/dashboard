"""AC2 + AC6: Strategy shell dispatch and position sizing.

Tests verify:
- strategy_shell.py dispatches all 6 strategies: s11, s09, s13, s21, s17, s18
- Each strategy's entry logic produces boolean signals given indicator data
- All share exit framework: 3x ATR trail, 24h no-stop protection, regime exit
- Tier-based max position: T1=15%, T2=8%, T3=3% of capital
- Regime scaling: 50% in downtrend, 30% in high-vol chaos
- Drawdown throttle: linear reduction >10% DD, stop new trades at 25% DD
"""

import pytest

from freqtrade_bridge.strategy_shell import StrategyShell


STRATEGIES = ["s11", "s09", "s13", "s21", "s17", "s18"]


class TestStrategyDispatch:
    """strategy_shell dispatches all 6 Tier A strategies."""

    @pytest.mark.parametrize("strategy_id", STRATEGIES)
    def test_dispatch_known_strategy(self, strategy_id, sample_indicator_data):
        shell = StrategyShell(strategy_id=strategy_id)
        signal = shell.entry_signal(sample_indicator_data)
        assert isinstance(signal, bool)

    def test_dispatch_unknown_strategy_raises(self, sample_indicator_data):
        with pytest.raises(ValueError):
            shell = StrategyShell(strategy_id="s99")
            shell.entry_signal(sample_indicator_data)

    @pytest.mark.parametrize("strategy_id", STRATEGIES)
    def test_each_strategy_returns_boolean(self, strategy_id, sample_indicator_data):
        shell = StrategyShell(strategy_id=strategy_id)
        result = shell.entry_signal(sample_indicator_data)
        assert result is True or result is False


class TestExitFramework:
    """All strategies share exit framework: 3x ATR trail, 24h no-stop, regime exit."""

    def test_trailing_stop_uses_3x_atr(self, sample_indicator_data):
        shell = StrategyShell(strategy_id="s11")
        trail_distance = shell.compute_trailing_stop(
            entry_price=100.0,
            current_price=110.0,
            atr=sample_indicator_data["atr_14"],
        )
        expected = 3.0 * sample_indicator_data["atr_14"]
        assert trail_distance == pytest.approx(expected)

    def test_trailing_stop_price_below_current(self, sample_indicator_data):
        shell = StrategyShell(strategy_id="s11")
        stop_price = shell.trailing_stop_price(
            current_price=110.0,
            atr=sample_indicator_data["atr_14"],
        )
        assert stop_price < 110.0

    def test_no_stop_protection_within_24h(self):
        """Positions within 24h of entry should not be stopped out."""
        shell = StrategyShell(strategy_id="s11")
        # entry_age_hours < 24 means no-stop protection active
        should_stop = shell.should_exit(
            entry_age_hours=12,
            current_price=90.0,
            entry_price=100.0,
            stop_price=95.0,
            atr=3.5,
            regime="uptrend",
        )
        assert should_stop is False

    def test_stop_allowed_after_24h(self):
        """Positions older than 24h can be stopped out."""
        shell = StrategyShell(strategy_id="s11")
        should_stop = shell.should_exit(
            entry_age_hours=30,
            current_price=90.0,
            entry_price=100.0,
            stop_price=95.0,
            atr=3.5,
            regime="uptrend",
        )
        # Price 90 < stop 95, and entry is >24h old -- should exit
        assert should_stop is True

    def test_regime_exit_triggers_on_regime_change(self):
        """Regime change to adverse regime triggers exit."""
        shell = StrategyShell(strategy_id="s11")
        should_exit = shell.should_exit(
            entry_age_hours=48,
            current_price=105.0,
            entry_price=100.0,
            stop_price=95.0,
            atr=3.5,
            regime="downtrend",
        )
        assert should_exit is True

    @pytest.mark.parametrize("strategy_id", STRATEGIES)
    def test_exit_framework_shared_across_strategies(self, strategy_id):
        """Every strategy uses the same exit logic."""
        shell = StrategyShell(strategy_id=strategy_id)
        # Verify the exit method exists and is callable
        assert callable(shell.should_exit)
        assert callable(shell.compute_trailing_stop)


class TestPositionSizing:
    """Tier-based max position sizes."""

    def test_tier1_max_position_15_percent(self):
        shell = StrategyShell(strategy_id="s11")
        max_pos = shell.max_position_size(capital=100_000, token_tier=1)
        assert max_pos == pytest.approx(15_000)

    def test_tier2_max_position_8_percent(self):
        shell = StrategyShell(strategy_id="s11")
        max_pos = shell.max_position_size(capital=100_000, token_tier=2)
        assert max_pos == pytest.approx(8_000)

    def test_tier3_max_position_3_percent(self):
        shell = StrategyShell(strategy_id="s11")
        max_pos = shell.max_position_size(capital=100_000, token_tier=3)
        assert max_pos == pytest.approx(3_000)

    def test_unknown_tier_raises(self):
        shell = StrategyShell(strategy_id="s11")
        with pytest.raises(ValueError):
            shell.max_position_size(capital=100_000, token_tier=5)


class TestRegimeScaling:
    """Regime scaling: 50% in downtrend, 30% in high-vol chaos."""

    def test_downtrend_scales_to_50_percent(self):
        shell = StrategyShell(strategy_id="s11")
        scale = shell.regime_scale_factor(regime="downtrend")
        assert scale == pytest.approx(0.50)

    def test_high_vol_chaos_scales_to_30_percent(self):
        shell = StrategyShell(strategy_id="s11")
        scale = shell.regime_scale_factor(regime="high_vol_chaos")
        assert scale == pytest.approx(0.30)

    def test_uptrend_no_scaling(self):
        shell = StrategyShell(strategy_id="s11")
        scale = shell.regime_scale_factor(regime="uptrend")
        assert scale == pytest.approx(1.0)

    def test_normal_regime_no_scaling(self):
        shell = StrategyShell(strategy_id="s11")
        scale = shell.regime_scale_factor(regime="normal")
        assert scale == pytest.approx(1.0)

    def test_scaled_position_in_downtrend(self):
        shell = StrategyShell(strategy_id="s11")
        base = shell.max_position_size(capital=100_000, token_tier=1)
        scale = shell.regime_scale_factor(regime="downtrend")
        assert base * scale == pytest.approx(7_500)


class TestDrawdownThrottle:
    """Drawdown throttle: linear reduction >10% DD, stop new trades at 25% DD."""

    def test_no_throttle_below_10_percent_dd(self):
        shell = StrategyShell(strategy_id="s11")
        factor = shell.drawdown_throttle(current_drawdown=0.05)
        assert factor == pytest.approx(1.0)

    def test_linear_reduction_at_15_percent_dd(self):
        shell = StrategyShell(strategy_id="s11")
        factor = shell.drawdown_throttle(current_drawdown=0.15)
        # Linear from 10% (factor=1.0) to 25% (factor=0.0)
        # At 15%: (25 - 15) / (25 - 10) = 10/15 = 0.6667
        assert factor == pytest.approx(2 / 3, rel=0.01)

    def test_linear_reduction_at_20_percent_dd(self):
        shell = StrategyShell(strategy_id="s11")
        factor = shell.drawdown_throttle(current_drawdown=0.20)
        # At 20%: (25 - 20) / (25 - 10) = 5/15 = 0.3333
        assert factor == pytest.approx(1 / 3, rel=0.01)

    def test_stop_trading_at_25_percent_dd(self):
        shell = StrategyShell(strategy_id="s11")
        factor = shell.drawdown_throttle(current_drawdown=0.25)
        assert factor == pytest.approx(0.0)

    def test_stop_trading_above_25_percent_dd(self):
        shell = StrategyShell(strategy_id="s11")
        factor = shell.drawdown_throttle(current_drawdown=0.30)
        assert factor == pytest.approx(0.0)

    def test_at_10_percent_boundary_no_throttle(self):
        shell = StrategyShell(strategy_id="s11")
        factor = shell.drawdown_throttle(current_drawdown=0.10)
        assert factor == pytest.approx(1.0)


class TestKellyFractionSizing:
    """AC6: Kelly criterion position sizing."""

    def test_kelly_fraction_uses_win_rate_and_payoff(self):
        """Given win_rate=0.55, avg_win=100, avg_loss=80, verify Kelly fraction.

        Kelly formula: f = (wr * b - (1 - wr)) / b  where b = avg_win / avg_loss
        b = 100/80 = 1.25
        f = (0.55 * 1.25 - 0.45) / 1.25 = (0.6875 - 0.45) / 1.25 = 0.19
        Quarter-Kelly = 0.19 * 0.25 = 0.0475
        """
        shell = StrategyShell(strategy_id="s11")
        fraction = shell.kelly_fraction(
            win_rate=0.55,
            avg_win=100.0,
            avg_loss=80.0,
        )
        b = 100.0 / 80.0
        full_kelly = (0.55 * b - (1 - 0.55)) / b
        quarter_kelly = full_kelly * 0.25
        assert fraction == pytest.approx(quarter_kelly, rel=0.01)

    def test_kelly_fraction_with_no_trade_history_uses_default(self):
        """With fewer than 10 trades, use default edge (0.35-0.40 range)."""
        shell = StrategyShell(strategy_id="s11")
        fraction = shell.kelly_fraction(
            win_rate=None,
            avg_win=None,
            avg_loss=None,
            num_trades=5,  # < 10 trades
        )
        # Default edge should produce a quarter-Kelly in a reasonable range
        assert isinstance(fraction, float)
        assert 0.0 < fraction < 0.15  # reasonable default quarter-Kelly range

    def test_kelly_fraction_capped_by_tier(self):
        """Kelly output is capped to the tier max position fraction."""
        shell = StrategyShell(strategy_id="s11")
        fraction = shell.kelly_fraction(
            win_rate=0.70,
            avg_win=200.0,
            avg_loss=50.0,
            token_tier=3,  # T3 cap = 3%
        )
        # Even with very favorable Kelly, output should not exceed tier cap
        assert fraction <= 0.03


class TestStrategyEntryConditions:
    """AC2: Per-strategy entry signal conditions."""

    def test_s11_entry_requires_3pct_return(self):
        """s11 requires ret_1 >= 3% for entry. 4% with good conditions -> True."""
        shell = StrategyShell(strategy_id="s11")
        signal_good = shell.entry_signal({
            "ret_1": 0.04,
            "adx_14": 25.0,
            "atr_14": 3.5,
            "ema_20": 107.0,
            "ema_50": 104.0,
            "rsi_14": 58.0,
            "regime": "uptrend",
            "close": 110.0,
        })
        signal_bad = shell.entry_signal({
            "ret_1": 0.01,  # Only 1%, below 3% threshold
            "adx_14": 25.0,
            "atr_14": 3.5,
            "ema_20": 107.0,
            "ema_50": 104.0,
            "rsi_14": 58.0,
            "regime": "uptrend",
            "close": 110.0,
        })
        assert signal_good is True
        assert signal_bad is False

    def test_s11_entry_requires_adx_above_20(self):
        """s11 requires ADX > 20 for trend confirmation."""
        shell = StrategyShell(strategy_id="s11")
        signal_good = shell.entry_signal({
            "ret_1": 0.04,
            "adx_14": 25.0,  # Above 20
            "atr_14": 3.5,
            "ema_20": 107.0,
            "ema_50": 104.0,
            "rsi_14": 58.0,
            "regime": "uptrend",
            "close": 110.0,
        })
        signal_bad = shell.entry_signal({
            "ret_1": 0.04,
            "adx_14": 15.0,  # Below 20
            "atr_14": 3.5,
            "ema_20": 107.0,
            "ema_50": 104.0,
            "rsi_14": 58.0,
            "regime": "uptrend",
            "close": 110.0,
        })
        assert signal_good is True
        assert signal_bad is False

    def test_s09_entry_requires_ema_stack(self):
        """s09 requires close > ema10 > ema20 for bullish EMA stack."""
        shell = StrategyShell(strategy_id="s09")
        signal_good = shell.entry_signal({
            "close": 115.0,
            "ema_10": 112.0,
            "ema_20": 108.0,  # close > ema10 > ema20
            "atr_14": 3.5,
            "rsi_14": 55.0,
            "regime": "uptrend",
        })
        signal_bad = shell.entry_signal({
            "close": 105.0,
            "ema_10": 112.0,  # ema10 > close -- stack broken
            "ema_20": 108.0,
            "atr_14": 3.5,
            "rsi_14": 55.0,
            "regime": "uptrend",
        })
        assert signal_good is True
        assert signal_bad is False
