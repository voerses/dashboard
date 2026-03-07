"""AC6: Combined strategies (s30 basis_carry) — manages both spot+perp legs,
separate signals, funding accrual.

Tests verify:
- Combined engine manages spot and perp legs simultaneously
- Spot and perp produce separate signal streams
- Funding accrual is tracked on the perp leg only
- Both legs open/close together for basis trades
- Strategy produces entry when basis spread is favorable
"""

import pytest

from v3.paper_engine import PaperEngine, CombinedPaperEngine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_combined_frozen_bars(n=300, seed=54321):
    """Generate frozen OHLCV bars for both spot and perp markets.

    Returns (spot_bars, perp_bars) with a small basis spread.
    """
    import random
    random.seed(seed)
    base_ts = 1700000000
    spot_bars = []
    perp_bars = []
    spot_price = 40000.0
    for i in range(n):
        change = random.gauss(0, 0.005)
        spot_price *= (1 + change)
        # Perp trades at a small premium/discount (basis)
        basis_bps = random.gauss(5, 10)  # ~5bps premium on average
        perp_price = spot_price * (1 + basis_bps / 10000)

        spot_bars.append({
            "timestamp": base_ts + i * 3600,
            "open": round(spot_price * (1 + random.gauss(0, 0.001)), 2),
            "high": round(spot_price * (1 + abs(random.gauss(0, 0.005))), 2),
            "low": round(spot_price * (1 - abs(random.gauss(0, 0.005))), 2),
            "close": round(spot_price, 2),
            "volume": round(random.uniform(500, 5000), 2),
        })
        perp_bars.append({
            "timestamp": base_ts + i * 3600,
            "open": round(perp_price * (1 + random.gauss(0, 0.001)), 2),
            "high": round(perp_price * (1 + abs(random.gauss(0, 0.005))), 2),
            "low": round(perp_price * (1 - abs(random.gauss(0, 0.005))), 2),
            "close": round(perp_price, 2),
            "volume": round(random.uniform(400, 4000), 2),
        })
    return spot_bars, perp_bars


# ---------------------------------------------------------------------------
# AC6: Combined spot+perp strategy
# ---------------------------------------------------------------------------


class TestCombinedEngineInit:
    """CombinedPaperEngine handles both spot and perp legs."""

    def test_combined_engine_accepts_config(self, sample_combined_engine_config):
        engine = CombinedPaperEngine(config=sample_combined_engine_config)
        assert engine is not None

    def test_combined_engine_has_both_markets(self, sample_combined_engine_config):
        engine = CombinedPaperEngine(config=sample_combined_engine_config)
        assert "spot" in engine.markets
        assert "perp" in engine.markets

    def test_combined_engine_strategy_type(self, sample_combined_engine_config):
        engine = CombinedPaperEngine(config=sample_combined_engine_config)
        assert engine.strategy_type == "basis_carry"


class TestSeparateSignalStreams:
    """Spot and perp produce separate signal streams."""

    def test_compute_signals_returns_spot_and_perp(self, sample_combined_engine_config):
        """Combined engine returns signals keyed by market."""
        spot_bars, perp_bars = make_combined_frozen_bars(300)
        engine = CombinedPaperEngine(config=sample_combined_engine_config)
        signals = engine.compute_signals(spot_bars=spot_bars, perp_bars=perp_bars)
        assert "spot" in signals
        assert "perp" in signals

    def test_spot_signals_are_list(self, sample_combined_engine_config):
        spot_bars, perp_bars = make_combined_frozen_bars(300)
        engine = CombinedPaperEngine(config=sample_combined_engine_config)
        signals = engine.compute_signals(spot_bars=spot_bars, perp_bars=perp_bars)
        assert isinstance(signals["spot"], list)

    def test_perp_signals_are_list(self, sample_combined_engine_config):
        spot_bars, perp_bars = make_combined_frozen_bars(300)
        engine = CombinedPaperEngine(config=sample_combined_engine_config)
        signals = engine.compute_signals(spot_bars=spot_bars, perp_bars=perp_bars)
        assert isinstance(signals["perp"], list)

    def test_spot_and_perp_signals_may_differ(self, sample_combined_engine_config):
        """Spot and perp can produce different signal patterns."""
        spot_bars, perp_bars = make_combined_frozen_bars(300)
        engine = CombinedPaperEngine(config=sample_combined_engine_config)
        signals = engine.compute_signals(spot_bars=spot_bars, perp_bars=perp_bars)
        # They CAN differ — just verify both are present and structured
        for market in ("spot", "perp"):
            for s in signals[market]:
                assert "type" in s
                assert "bar_index" in s


class TestBasisTradeLegs:
    """Both legs open/close together for basis trades."""

    def test_entry_opens_both_legs(self, sample_combined_engine_config):
        """A basis entry should create positions on both spot and perp."""
        spot_bars, perp_bars = make_combined_frozen_bars(300)
        engine = CombinedPaperEngine(config=sample_combined_engine_config)
        signals = engine.compute_signals(spot_bars=spot_bars, perp_bars=perp_bars)

        # Find paired entries: same bar_index on both legs
        spot_entry_bars = {
            s["bar_index"] for s in signals["spot"] if s["type"] == "entry"
        }
        perp_entry_bars = {
            s["bar_index"] for s in signals["perp"] if s["type"] == "entry"
        }
        # Basis trades: entries should appear on both legs at the same bar
        paired = spot_entry_bars & perp_entry_bars
        # Basis trades require paired entries on both legs at the same bar
        assert len(paired) > 0, (
            "Combined engine produced no paired entries — basis trade logic is broken. "
            f"Spot entries: {spot_entry_bars}, Perp entries: {perp_entry_bars}"
        )

    def test_exit_closes_both_legs(self, sample_combined_engine_config):
        """A basis exit should close positions on both spot and perp."""
        spot_bars, perp_bars = make_combined_frozen_bars(300)
        engine = CombinedPaperEngine(config=sample_combined_engine_config)
        signals = engine.compute_signals(spot_bars=spot_bars, perp_bars=perp_bars)

        spot_exit_bars = {
            s["bar_index"] for s in signals["spot"] if s["type"] == "exit"
        }
        perp_exit_bars = {
            s["bar_index"] for s in signals["perp"] if s["type"] == "exit"
        }
        paired_exits = spot_exit_bars & perp_exit_bars
        assert len(paired_exits) > 0, (
            "Combined engine produced no paired exits — basis trade exit logic is broken. "
            f"Spot exits: {spot_exit_bars}, Perp exits: {perp_exit_bars}"
        )

    def test_spot_leg_is_long_perp_leg_is_short(self, sample_combined_engine_config):
        """For cash-and-carry: buy spot (long), sell perp (short)."""
        spot_bars, perp_bars = make_combined_frozen_bars(300)
        engine = CombinedPaperEngine(config=sample_combined_engine_config)
        signals = engine.compute_signals(spot_bars=spot_bars, perp_bars=perp_bars)

        for s in signals["spot"]:
            if s["type"] == "entry":
                assert "side" in s, "Spot entry signal missing 'side' field"
                assert s["side"] == "buy"
        for s in signals["perp"]:
            if s["type"] == "entry":
                assert "side" in s, "Perp entry signal missing 'side' field"
                assert s["side"] == "sell"


class TestCombinedFundingAccrual:
    """Funding accrual is tracked on the perp leg only."""

    def test_perp_positions_accrue_funding(self, sample_combined_engine_config):
        """Perp leg positions should have funding_accrued tracking."""
        spot_bars, perp_bars = make_combined_frozen_bars(300)
        engine = CombinedPaperEngine(config=sample_combined_engine_config)
        signals = engine.compute_signals(spot_bars=spot_bars, perp_bars=perp_bars)
        # Perp entry signals should carry funding_rate or engine tracks funding
        perp_entries = [s for s in signals["perp"] if s["type"] == "entry"]
        assert len(perp_entries) > 0, "No perp entries to verify funding on"
        # Engine must expose funding tracking for the perp leg
        assert hasattr(engine, "funding_accrued") or any(
            "funding_rate" in s for s in perp_entries
        ), "Perp leg has no funding accrual mechanism"

    def test_spot_positions_no_funding(self, sample_combined_engine_config):
        """Spot leg positions should NOT accrue funding."""
        spot_bars, perp_bars = make_combined_frozen_bars(300)
        engine = CombinedPaperEngine(config=sample_combined_engine_config)
        signals = engine.compute_signals(spot_bars=spot_bars, perp_bars=perp_bars)
        # Spot entry signals must not carry funding_rate
        spot_entries = [s for s in signals["spot"] if s["type"] == "entry"]
        for s in spot_entries:
            assert "funding_rate" not in s, (
                f"Spot entry at bar {s['bar_index']} has funding_rate — "
                "spot leg should NOT accrue funding"
            )


class TestBasisSpreadSignal:
    """Strategy produces entry when basis spread is favorable."""

    def test_signals_sensitive_to_basis_spread(self, sample_combined_engine_config):
        """Different basis spreads should produce different entry patterns."""
        import random
        random.seed(11111)
        base_ts = 1700000000

        # Wide basis: many entries expected
        spot_bars_wide = []
        perp_bars_wide = []
        price = 40000.0
        for i in range(300):
            change = random.gauss(0, 0.005)
            price *= (1 + change)
            spot_bars_wide.append({
                "timestamp": base_ts + i * 3600,
                "open": round(price, 2), "high": round(price * 1.005, 2),
                "low": round(price * 0.995, 2), "close": round(price, 2),
                "volume": 1000.0,
            })
            # Large premium on perp
            perp_bars_wide.append({
                "timestamp": base_ts + i * 3600,
                "open": round(price * 1.005, 2), "high": round(price * 1.01, 2),
                "low": round(price, 2), "close": round(price * 1.005, 2),
                "volume": 1000.0,
            })

        engine_wide = CombinedPaperEngine(config=sample_combined_engine_config)
        signals_wide = engine_wide.compute_signals(
            spot_bars=spot_bars_wide, perp_bars=perp_bars_wide,
        )

        # Narrow basis: few/no entries expected
        random.seed(22222)
        spot_bars_narrow = []
        perp_bars_narrow = []
        price2 = 40000.0
        for i in range(300):
            change = random.gauss(0, 0.005)
            price2 *= (1 + change)
            spot_bars_narrow.append({
                "timestamp": base_ts + i * 3600,
                "open": round(price2, 2), "high": round(price2 * 1.005, 2),
                "low": round(price2 * 0.995, 2), "close": round(price2, 2),
                "volume": 1000.0,
            })
            # Negligible premium on perp
            perp_bars_narrow.append({
                "timestamp": base_ts + i * 3600,
                "open": round(price2 * 1.0001, 2), "high": round(price2 * 1.005, 2),
                "low": round(price2 * 0.995, 2), "close": round(price2 * 1.0001, 2),
                "volume": 1000.0,
            })

        engine_narrow = CombinedPaperEngine(config=sample_combined_engine_config)
        signals_narrow = engine_narrow.compute_signals(
            spot_bars=spot_bars_narrow, perp_bars=perp_bars_narrow,
        )

        wide_entries = len([
            s for s in signals_wide["perp"] if s["type"] == "entry"
        ])
        narrow_entries = len([
            s for s in signals_narrow["perp"] if s["type"] == "entry"
        ])
        assert wide_entries > narrow_entries, (
            f"Wide basis ({wide_entries} entries) should produce more entries "
            f"than narrow basis ({narrow_entries} entries)"
        )
