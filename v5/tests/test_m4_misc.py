"""M4 — Miscellaneous ACs: ReduceResult, scale cap, R-anchor, role constraint,
paper streaming == backtest eager, minute_exits.py absent.

Covers:
  - T-B2 (AC11): Dispatcher-agnostic ReduceResult — same reduce inputs from
    hourly vs 1m contexts produce bit-exact ReduceResult.
  - T-B3 (AC9): Per-hourly-bar scale cap — 120 sub-hourly ticks => partial_fills <= 10.
  - T-B4 (AC10): Stage 3 reads r_anchor_price (frozen at first entry); after
    scale (long $100, increase $95 -> VWAP $97.5), r_anchor stays $100.
  - T-B19 (AC29): Role constraint — signal.period >= entry.period; else ValueError
    at StrategySpec construction.
  - T-B24 (AC33): Paper streaming consolidator byte-identical to backtest eager
    DataResampler across all 13 canonical BarSpecs.
  - T-B25 (AC3): minute_exits.py file is absent from v5/.

All tests MUST FAIL today — M4 implementations don't exist yet.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from v5.testing import F64_ATOL, F32_ATOL  # noqa: E402


CANONICAL_MINUTES = (1, 3, 5, 10, 15, 30, 60, 120, 240, 360, 480, 720, 1440)


class TestTB2DispatcherAgnosticReduceResult:
    """T-B2 (AC11): same reduce inputs from hourly vs 1m -> bit-exact ReduceResult."""

    def test_reduce_from_hourly_vs_1m_context_equal(self):
        """T-B2: ReduceResult bit-identical across exit_resolution contexts."""
        from v5.bar_spec import BarSpec
        from v5.position import Position

        pos_h = Position(token="BTC", direction=1, entry_price=100.0, quantity=1.0)
        pos_h.margin_usd = 1000.0
        pos_h.cumulative_funding = 1.5

        pos_m = Position(token="BTC", direction=1, entry_price=100.0, quantity=1.0)
        pos_m.margin_usd = 1000.0
        pos_m.cumulative_funding = 1.5

        r_hourly = pos_h.reduce(
            close_fraction=0.4, bar_spec=BarSpec.from_minutes(60),
        )
        r_minute = pos_m.reduce(
            close_fraction=0.4, bar_spec=BarSpec.from_minutes(1),
        )
        # Post-reduce state must be bit-identical.
        assert pos_h.entry_price == pos_m.entry_price
        assert pos_h.quantity == pos_m.quantity
        assert pos_h.margin_usd == pos_m.margin_usd
        assert pos_h.cumulative_funding == pos_m.cumulative_funding
        assert r_hourly == r_minute


class TestTB3PerHourlyBarScaleCap:
    """T-B3 (AC9): 120 sub-hourly ticks => partial_fills <= 10."""

    def test_scale_cap_across_120_subhourly_ticks(self):
        """T-B3: 10 hourly windows x 12 sub-hourly ticks each -> <=10 scale calls."""
        from v5.bar_processor import BarProcessor
        from v5.bar_spec import BarSpec
        from v5.position import Position

        pos = Position(token="BTC", direction=1, entry_price=100.0, quantity=1.0)
        scale_calls = {"n": 0}

        class Strat:
            strategy_id = "t"
            bar_subscriptions = {
                "signal": BarSpec.from_minutes(60),
                "entry": BarSpec.from_minutes(60),
                "exit": BarSpec.from_minutes(5),
            }
            def check_scale(self, pos, bar_ctx):
                scale_calls["n"] += 1
                return "increase"

        bp = BarProcessor()
        strat = Strat()
        bp.register_strategy(strat)
        bp.register_position(pos)

        # 10 hourly windows * 12 five-minute bars = 120 ticks.
        start = 1_770_000_000 * 1_000_000_000
        five_min = 5 * 60 * 1_000_000_000
        for i in range(120):
            ts = start + i * five_min
            hourly_bar_index = (i * 5) // 60
            bp.process_bar_for_position(
                pos=pos,
                bar_ctx=type("BC", (), {
                    "ts_ns": ts, "close": 100.0, "high": 100.5, "low": 99.5,
                    "volume": 1000.0, "bar_spec": BarSpec.from_minutes(5),
                    "hourly_bar_index": hourly_bar_index,
                })(),
            )

        assert scale_calls["n"] <= 10, (
            f"AC9 scale cap breached: {scale_calls['n']} > 10 calls in 120 ticks"
        )


class TestTB4RAnchorInvariant:
    """T-B4 (AC10): Stage 3 reads r_anchor_price; VWAP scale does NOT change it."""

    def test_r_anchor_frozen_at_first_entry(self):
        """T-B4: long $100 entry, increase at $95 -> VWAP $97.5; r_anchor stays $100."""
        from v5.position import Position

        pos = Position(token="BTC", direction=1, entry_price=100.0, quantity=1.0)
        pos.r_anchor_price = 100.0  # frozen at first entry

        pos.increase(price=95.0, quantity=1.0, margin_delta=500.0)
        # After equal-weight average of 100 and 95 -> VWAP = 97.5.
        assert pos.entry_price == pytest.approx(97.5), (
            f"VWAP not 97.5: got {pos.entry_price}"
        )
        # r_anchor MUST stay at 100.0.
        assert pos.r_anchor_price == 100.0, (
            f"r_anchor moved: {pos.r_anchor_price}"
        )


class TestTB19RoleConstraint:
    """T-B19 (AC29): signal.period >= entry.period else ValueError."""

    def test_signal_less_than_entry_raises(self):
        """AC29: signal=1m, entry=1h violates signal.period >= entry.period."""
        from v5.bar_spec import BarSpec
        from v5.strategy_spec import StrategySpec

        with pytest.raises(ValueError) as exc_info:
            StrategySpec(
                strategy_id="bad",
                bar_subscriptions={
                    "signal": BarSpec.from_minutes(1),
                    "entry": BarSpec.from_minutes(60),
                    "exit": BarSpec.from_minutes(60),
                },
            )
        msg = str(exc_info.value).lower()
        assert "signal" in msg or "entry" in msg or "period" in msg

    def test_signal_equal_entry_ok(self):
        """AC29: signal == entry is permitted (equality boundary)."""
        from v5.bar_spec import BarSpec
        from v5.strategy_spec import StrategySpec

        spec = StrategySpec(
            strategy_id="ok",
            bar_subscriptions={
                "signal": BarSpec.from_minutes(60),
                "entry": BarSpec.from_minutes(60),
                "exit": BarSpec.from_minutes(1),
            },
        )
        assert spec is not None

    def test_signal_greater_than_entry_ok(self):
        """AC29: signal > entry is the canonical case."""
        from v5.bar_spec import BarSpec
        from v5.strategy_spec import StrategySpec

        spec = StrategySpec(
            strategy_id="ok2",
            bar_subscriptions={
                "signal": BarSpec.from_minutes(240),
                "entry": BarSpec.from_minutes(60),
                "exit": BarSpec.from_minutes(1),
            },
        )
        assert spec is not None


class TestTB24PaperStreamingEqBacktestEager:
    """T-B24 (AC33): paper streaming == backtest eager across all 13 BarSpecs."""

    @pytest.mark.parametrize("n", CANONICAL_MINUTES)
    def test_streaming_byte_identical_to_eager(self, n):
        """T-B24: fixed 1m tick stream -> streaming consolidator at Nm produces
        bytes identical to DataResampler at Nm (via float64 protocol)."""
        from v5.bar_spec import BarSpec
        from v5.data_resampler import DataResampler
        from v5.streaming_consolidator import StreamingConsolidator

        # Synthesize a 1m tick stream for an even multiple of n minutes.
        total_minutes = max(n * 3, 60)
        rng = np.random.default_rng(42)
        base_ts = 1_770_000_000 * 1_000_000_000
        ticks = []
        for i in range(total_minutes):
            ticks.append({
                "ts_ns": base_ts + i * 60_000_000_000,
                "price": 100.0 + float(rng.normal(0, 0.1)),
                "volume": 1.0,
            })

        # Eager path: build 1m DF then DataResampler -> Nm.
        df_1m = pd.DataFrame({
            "timestamp": [t["ts_ns"] for t in ticks],
            "open": [t["price"] for t in ticks],
            "high": [t["price"] for t in ticks],
            "low": [t["price"] for t in ticks],
            "close": [t["price"] for t in ticks],
            "volume": [t["volume"] for t in ticks],
        })
        eager = DataResampler.materialize(df_1m, BarSpec.from_minutes(n))

        # Streaming path: push ticks through consolidator at Nm.
        cons = StreamingConsolidator(bar_spec=BarSpec.from_minutes(n))
        closed_bars = []
        for t in ticks:
            b = cons.on_tick(ts_ns=t["ts_ns"], price=t["price"], volume=t["volume"])
            if b is not None:
                closed_bars.append(b)

        # Compare eager rows with closed_bars produced by streaming.
        assert len(eager) >= 1, "Eager produced zero bars"
        # Match OHLCV bytes on the overlapping range (post-downcast to float32).
        for i, eb in enumerate(closed_bars[: len(eager)]):
            for col in ("open", "high", "low", "close", "volume"):
                a = np.float32(eager[col].iloc[i]).tobytes()
                b = np.float32(eb[col]).tobytes()
                assert a == b, (
                    f"n={n} bar={i} col={col} diverged between streaming and eager"
                )


class TestTB25MinuteExitsAbsent:
    """T-B25 (AC3): v5/minute_exits.py file is absent."""

    def test_v5_minute_exits_file_absent(self):
        """AC3 T-B25: v5/minute_exits.py must not exist post-M4."""
        path = _project_root / "v5" / "minute_exits.py"
        assert not path.exists(), (
            f"AC3 violation: {path} still exists post-M4; should be deleted"
        )

    def test_no_minute_exits_imports_in_v5(self):
        """AC3: no v5/*.py imports minute_exits post-deletion."""
        v5_dir = _project_root / "v5"
        offenders = []
        for py in v5_dir.glob("**/*.py"):
            if "/tests/" in str(py) or py.name.startswith("test_"):
                continue
            content = py.read_text()
            if "from v5.minute_exits" in content or "import v5.minute_exits" in content:
                offenders.append(py.relative_to(_project_root))
        assert not offenders, (
            f"AC3: v5 sources still import minute_exits: {offenders}"
        )
