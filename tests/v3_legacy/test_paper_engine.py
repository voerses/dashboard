"""AC5: Signal identity — paper engine produces same entry/exit signals
as backtest engine on frozen data.

Tests verify:
- On identical frozen OHLCV data, paper engine and backtest engine produce
  the same entry signals (matching entry_bar indices)
- Same exit signals
- 200-bar burn-in period is excluded from comparison
- Deterministic: same seed produces same results
"""

import pytest

from v3.paper_engine import PaperEngine
from v3.engine import BacktestEngine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_frozen_bars(n=300, seed=12345):
    """Generate deterministic frozen OHLCV bars for signal-identity testing.

    300 bars: 200 burn-in + 100 test bars.
    """
    import random
    random.seed(seed)
    base_ts = 1700000000
    bars = []
    price = 40000.0
    for i in range(n):
        change = random.gauss(0, 0.005)
        price *= (1 + change)
        bars.append({
            "timestamp": base_ts + i * 3600,
            "open": round(price * (1 + random.gauss(0, 0.001)), 2),
            "high": round(price * (1 + abs(random.gauss(0, 0.005))), 2),
            "low": round(price * (1 - abs(random.gauss(0, 0.005))), 2),
            "close": round(price, 2),
            "volume": round(random.uniform(500, 5000), 2),
        })
    return bars


# ---------------------------------------------------------------------------
# AC5: Signal identity on frozen data
# ---------------------------------------------------------------------------


class TestSignalIdentity:
    """Paper engine produces same signals as backtest engine on frozen data."""

    def test_entry_signals_match_backtest(self, tmp_path, sample_paper_engine_config):
        """Entry signals from paper engine match backtest on same frozen data."""
        frozen_bars = make_frozen_bars(300, seed=12345)

        backtest = BacktestEngine(strategy_id="s11")
        backtest_signals = backtest.compute_signals(frozen_bars)

        paper = PaperEngine(config=sample_paper_engine_config)
        paper_signals = paper.compute_signals(frozen_bars)

        # Compare only after 200-bar burn-in
        bt_entries = [
            s["bar_index"] for s in backtest_signals
            if s["type"] == "entry" and s["bar_index"] >= 200
        ]
        pe_entries = [
            s["bar_index"] for s in paper_signals
            if s["type"] == "entry" and s["bar_index"] >= 200
        ]
        assert bt_entries == pe_entries, (
            f"Entry signal mismatch: backtest={bt_entries}, paper={pe_entries}"
        )

    def test_exit_signals_match_backtest(self, tmp_path, sample_paper_engine_config):
        """Exit signals from paper engine match backtest on same frozen data."""
        frozen_bars = make_frozen_bars(300, seed=12345)

        backtest = BacktestEngine(strategy_id="s11")
        backtest_signals = backtest.compute_signals(frozen_bars)

        paper = PaperEngine(config=sample_paper_engine_config)
        paper_signals = paper.compute_signals(frozen_bars)

        bt_exits = [
            s["bar_index"] for s in backtest_signals
            if s["type"] == "exit" and s["bar_index"] >= 200
        ]
        pe_exits = [
            s["bar_index"] for s in paper_signals
            if s["type"] == "exit" and s["bar_index"] >= 200
        ]
        assert bt_exits == pe_exits, (
            f"Exit signal mismatch: backtest={bt_exits}, paper={pe_exits}"
        )

    def test_burn_in_excluded_from_comparison(self, tmp_path, sample_paper_engine_config):
        """Signals within the first 200 bars are ignored for identity check."""
        frozen_bars = make_frozen_bars(300, seed=12345)

        paper = PaperEngine(config=sample_paper_engine_config)
        paper_signals = paper.compute_signals(frozen_bars)

        # Separate signals into burn-in and post-burn-in
        compared = [s for s in paper_signals if s["bar_index"] >= 200]
        burnin = [s for s in paper_signals if s["bar_index"] < 200]
        # The engine should produce some signals overall (300 bars is enough)
        assert len(paper_signals) > 0, "No signals produced from 300 bars"
        # Post-burn-in signals must exist in the comparison window
        assert len(compared) > 0, (
            "No signals after 200-bar burn-in — engine may not be generating "
            "signals in the test window"
        )

    def test_deterministic_with_same_seed(self, tmp_path, sample_paper_engine_config):
        """Same frozen data produces same signals on repeated runs."""
        frozen_bars = make_frozen_bars(300, seed=12345)

        paper1 = PaperEngine(config=sample_paper_engine_config)
        signals1 = paper1.compute_signals(frozen_bars)

        paper2 = PaperEngine(config=sample_paper_engine_config)
        signals2 = paper2.compute_signals(frozen_bars)

        assert signals1 == signals2


class TestPaperEngineLifecycle:
    """PaperEngine start/stop/tick lifecycle."""

    def test_engine_not_alive_before_start(self, sample_paper_engine_config):
        """Engine should not be alive before start() is called."""
        engine = PaperEngine(config=sample_paper_engine_config)
        assert engine.is_alive is False

    def test_engine_alive_after_start(self, sample_paper_engine_config):
        """Engine should be alive after start() is called."""
        engine = PaperEngine(config=sample_paper_engine_config)
        engine.start()
        assert engine.is_alive is True

    def test_engine_not_alive_after_stop(self, sample_paper_engine_config):
        """Engine should not be alive after stop() is called."""
        engine = PaperEngine(config=sample_paper_engine_config)
        engine.start()
        engine.stop()
        assert engine.is_alive is False

    def test_resume_makes_engine_alive(self, sample_paper_engine_config):
        """resume() should make the engine alive."""
        engine = PaperEngine(config=sample_paper_engine_config)
        engine.resume()
        assert engine.is_alive is True

    def test_last_tick_time_none_before_tick(self, sample_paper_engine_config):
        """last_tick_time should be None before any tick."""
        engine = PaperEngine(config=sample_paper_engine_config)
        assert engine.last_tick_time is None

    def test_tick_is_callable(self, sample_paper_engine_config):
        """Engine exposes a callable tick method."""
        engine = PaperEngine(config=sample_paper_engine_config)
        assert callable(engine.tick)


class TestPaperEngineSignalFormat:
    """Signals have the expected structure."""

    def test_signal_has_type_field(self, sample_paper_engine_config):
        """Each signal dict has a 'type' field: 'entry' or 'exit'."""
        frozen_bars = make_frozen_bars(300, seed=12345)
        engine = PaperEngine(config=sample_paper_engine_config)
        signals = engine.compute_signals(frozen_bars)
        for s in signals:
            assert "type" in s
            assert s["type"] in ("entry", "exit")

    def test_signal_has_bar_index(self, sample_paper_engine_config):
        """Each signal has a 'bar_index' indicating which bar triggered it."""
        frozen_bars = make_frozen_bars(300, seed=12345)
        engine = PaperEngine(config=sample_paper_engine_config)
        signals = engine.compute_signals(frozen_bars)
        for s in signals:
            assert "bar_index" in s
            assert isinstance(s["bar_index"], int)

    def test_signal_has_token(self, sample_paper_engine_config):
        """Each signal identifies the token."""
        frozen_bars = make_frozen_bars(300, seed=12345)
        engine = PaperEngine(config=sample_paper_engine_config)
        signals = engine.compute_signals(frozen_bars)
        for s in signals:
            assert "token" in s


@pytest.mark.parametrize("strategy_id", ["s11", "s09"])
class TestSignalIdentityMultiStrategy:
    """Signal identity holds across multiple strategies."""

    def test_signals_identical_for_strategy(
        self, strategy_id, tmp_path, sample_paper_engine_config,
    ):
        frozen_bars = make_frozen_bars(300, seed=99999)
        config = dict(sample_paper_engine_config)
        config["strategy_id"] = strategy_id

        backtest = BacktestEngine(strategy_id=strategy_id)
        bt_signals = backtest.compute_signals(frozen_bars)

        paper = PaperEngine(config=config)
        pe_signals = paper.compute_signals(frozen_bars)

        bt_entries = [
            s["bar_index"] for s in bt_signals
            if s["type"] == "entry" and s["bar_index"] >= 200
        ]
        pe_entries = [
            s["bar_index"] for s in pe_signals
            if s["type"] == "entry" and s["bar_index"] >= 200
        ]
        assert bt_entries == pe_entries
