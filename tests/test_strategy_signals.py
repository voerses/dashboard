"""Test that CpcvSwingStrategy actually produces entry/exit signals.

Feeds synthetic candle data through the strategy and verifies:
1. Indicators are computed (not NaN)
2. S11 entry signal fires when conditions are met
3. S11 entry does NOT fire when conditions aren't met
4. Exit signal fires when conditions are met
5. Validated token filter works (unvalidated tokens get no signals)
"""

import json
import os

import numpy as np
import pytest

try:
    import pandas as pd
    import talib as ta
    from freqtrade.strategy import IStrategy
    HAS_DEPS = True
except ImportError:
    HAS_DEPS = False

pytestmark = pytest.mark.skipif(not HAS_DEPS, reason="freqtrade/talib not installed")


def make_candles(n=100, base_price=1.0, seed=42):
    """Generate synthetic OHLCV candle data as a DataFrame."""
    rng = np.random.RandomState(seed)
    dates = pd.date_range("2026-01-01", periods=n, freq="1h")
    close = base_price * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    high = close * (1 + rng.uniform(0, 0.02, n))
    low = close * (1 - rng.uniform(0, 0.02, n))
    open_ = close * (1 + rng.normal(0, 0.005, n))
    volume = rng.uniform(1e6, 1e7, n)

    return pd.DataFrame({
        "date": dates,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    })


def make_burst_candles(n=100, base_price=1.0, seed=42):
    """Generate candles with a 3%+ burst at the end (to trigger S11 entry)."""
    df = make_candles(n - 1, base_price, seed)

    # Make the last candle a 5% burst with high volume and strong trend
    last_close = df["close"].iloc[-1] * 1.05
    burst_row = pd.DataFrame({
        "date": [df["date"].iloc[-1] + pd.Timedelta(hours=1)],
        "open": [df["close"].iloc[-1]],
        "high": [last_close * 1.01],
        "low": [df["close"].iloc[-1] * 0.99],
        "close": [last_close],
        "volume": [df["volume"].mean() * 3],  # 3x avg volume
    })
    return pd.concat([df, burst_row], ignore_index=True)


@pytest.fixture
def strategy_config(tmp_path):
    """Create a minimal Freqtrade config + cpcv_params.json."""
    strat_dir = tmp_path / "strategies"
    strat_dir.mkdir()

    params = {
        "validated_tokens": ["SUI", "PENGU", "OM", "TRX", "AVAX",
                             "BONK", "FIL", "FLOKI", "ZRO"],
        "strategy_params": {
            "stop_mult": 3.0,
            "trail_mult": 3.0,
            "no_stop_bars": 24,
            "min_hold": 18,
            "max_hold": 720,
            "edge": 0.40,
        },
        "exchanges": {
            "binance": {
                "tokens": ["SUI", "PENGU", "OM", "TRX", "AVAX",
                           "BONK", "FIL", "FLOKI", "ZRO"],
                "missing_tokens": [],
            }
        },
    }
    with open(strat_dir / "cpcv_params.json", "w") as f:
        json.dump(params, f)

    return {
        "exchange": {"name": "binance"},
        "user_data_dir": str(tmp_path),
        "stake_currency": "USDT",
        "trading_mode": "spot",
    }


@pytest.fixture
def strategy(strategy_config):
    """Load the CpcvSwingStrategy with test config."""
    import sys
    strat_path = os.path.join(os.path.dirname(__file__),
                              "..", "user_data", "strategies")
    sys.path.insert(0, strat_path)
    from CpcvSwingStrategy import CpcvSwingStrategy
    return CpcvSwingStrategy(strategy_config)


class TestIndicatorsComputed:
    """Verify all indicators are computed and not NaN."""

    def test_indicators_not_nan(self, strategy):
        df = make_candles(100)
        result = strategy.populate_indicators(df, {"pair": "SUI/USDT"})

        # Check last row (after warmup period) has valid indicators
        last = result.iloc[-1]
        for col in ["ema_10", "ema_20", "ema_50", "rsi", "macd",
                     "adx", "bb_upper", "bb_lower", "atr", "vol_ratio", "ret_1"]:
            assert col in result.columns, f"Missing indicator: {col}"
            assert not np.isnan(last[col]), f"{col} is NaN on last row"

    def test_rsi_in_range(self, strategy):
        df = make_candles(100)
        result = strategy.populate_indicators(df, {"pair": "SUI/USDT"})
        rsi_vals = result["rsi"].dropna()
        assert (rsi_vals >= 0).all() and (rsi_vals <= 100).all()

    def test_adx_positive(self, strategy):
        df = make_candles(100)
        result = strategy.populate_indicators(df, {"pair": "SUI/USDT"})
        adx_vals = result["adx"].dropna()
        assert (adx_vals >= 0).all()


class TestS11EntrySignal:
    """S11 Momentum Burst: ret_1 > 3%, ADX > 20, close > EMA20, vol_ratio > 1."""

    def test_entry_fires_on_burst_candle(self, strategy):
        """A 5% burst with volume should trigger entry on a validated token."""
        df = make_burst_candles(100)
        df = strategy.populate_indicators(df, {"pair": "SUI/USDT"})

        # Force favorable conditions on the last row to guarantee signal
        df.loc[df.index[-1], "ret_1"] = 0.05  # 5% return
        df.loc[df.index[-1], "adx"] = 25.0
        df.loc[df.index[-1], "vol_ratio"] = 2.0
        df.loc[df.index[-1], "ema_20"] = df["close"].iloc[-1] * 0.95

        result = strategy.populate_entry_trend(df, {"pair": "SUI/USDT"})
        assert result["enter_long"].iloc[-1] == 1, \
            "S11 entry should fire: 5% burst, ADX=25, vol_ratio=2"

    def test_no_entry_without_burst(self, strategy):
        """Normal candle (< 3% return) should NOT trigger entry."""
        df = make_candles(100)
        df = strategy.populate_indicators(df, {"pair": "SUI/USDT"})

        # Ensure last candle has small return
        df.loc[df.index[-1], "ret_1"] = 0.005  # 0.5%

        result = strategy.populate_entry_trend(df, {"pair": "SUI/USDT"})
        assert result["enter_long"].iloc[-1] != 1, \
            "S11 entry should NOT fire on 0.5% return"

    def test_no_entry_low_adx(self, strategy):
        """Burst candle with weak trend (ADX < 20) should NOT trigger."""
        df = make_burst_candles(100)
        df = strategy.populate_indicators(df, {"pair": "SUI/USDT"})

        df.loc[df.index[-1], "ret_1"] = 0.04
        df.loc[df.index[-1], "adx"] = 15.0  # below threshold

        result = strategy.populate_entry_trend(df, {"pair": "SUI/USDT"})
        assert result["enter_long"].iloc[-1] != 1, \
            "S11 entry should NOT fire with ADX=15"

    def test_no_entry_low_volume(self, strategy):
        """Burst candle with below-average volume should NOT trigger."""
        df = make_burst_candles(100)
        df = strategy.populate_indicators(df, {"pair": "SUI/USDT"})

        df.loc[df.index[-1], "ret_1"] = 0.04
        df.loc[df.index[-1], "adx"] = 25.0
        df.loc[df.index[-1], "vol_ratio"] = 0.5  # below average

        result = strategy.populate_entry_trend(df, {"pair": "SUI/USDT"})
        assert result["enter_long"].iloc[-1] != 1, \
            "S11 entry should NOT fire with vol_ratio=0.5"


class TestExitSignal:
    """Exit: ADX < 15 + RSI > 70 (weak trend + overbought)."""

    def test_exit_fires_on_weak_overbought(self, strategy):
        df = make_candles(100)
        df = strategy.populate_indicators(df, {"pair": "SUI/USDT"})

        df.loc[df.index[-1], "adx"] = 10.0  # weak trend
        df.loc[df.index[-1], "rsi"] = 75.0  # overbought

        result = strategy.populate_exit_trend(df, {"pair": "SUI/USDT"})
        assert result["exit_long"].iloc[-1] == 1

    def test_no_exit_strong_trend(self, strategy):
        df = make_candles(100)
        df = strategy.populate_indicators(df, {"pair": "SUI/USDT"})

        df.loc[df.index[-1], "adx"] = 30.0  # strong trend
        df.loc[df.index[-1], "rsi"] = 75.0

        result = strategy.populate_exit_trend(df, {"pair": "SUI/USDT"})
        assert result["exit_long"].iloc[-1] != 1


class TestValidatedTokenFilter:
    """Unvalidated tokens should never get entry signals."""

    def test_unvalidated_token_no_entry(self, strategy):
        """DOGE is not in the validated list — no signals."""
        df = make_burst_candles(100)
        df = strategy.populate_indicators(df, {"pair": "DOGE/USDT"})

        df.loc[df.index[-1], "ret_1"] = 0.05
        df.loc[df.index[-1], "adx"] = 25.0
        df.loc[df.index[-1], "vol_ratio"] = 2.0
        df.loc[df.index[-1], "ema_20"] = df["close"].iloc[-1] * 0.95

        result = strategy.populate_entry_trend(df, {"pair": "DOGE/USDT"})
        assert result["enter_long"].iloc[-1] == 0, \
            "Unvalidated token DOGE should never get entry signal"

    def test_validated_token_can_enter(self, strategy):
        """SUI is validated — should get entry signal when conditions met."""
        df = make_burst_candles(100)
        df = strategy.populate_indicators(df, {"pair": "SUI/USDT"})

        df.loc[df.index[-1], "ret_1"] = 0.05
        df.loc[df.index[-1], "adx"] = 25.0
        df.loc[df.index[-1], "vol_ratio"] = 2.0
        df.loc[df.index[-1], "ema_20"] = df["close"].iloc[-1] * 0.95

        result = strategy.populate_entry_trend(df, {"pair": "SUI/USDT"})
        assert result["enter_long"].iloc[-1] == 1
