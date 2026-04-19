"""M5 — Backtest unification: single-leg zero overhead + hourly parity.

Covers:
  - T-M5-01: single-leg zero overhead. Order(legs=()) (empty tuple) is the
    common case — bare Order fields act as the implicit single leg. No Leg
    object constructed, no hash overhead. Immediate market orders cycle the
    full state machine in a single tick (bar-close trigger fires immediately).
  - T-M5-18: backtest parity (AC14) — hourly-only strategies produce
    bit-identical trade archives pre-M5 vs post-M5.

All tests MUST FAIL today — v5.orders (post-rename) does not yet exist.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


class TestTM501SingleLegZeroOverhead:
    """T-M5-01: empty legs tuple → no Leg allocation, state machine cycles in 1 tick."""

    def test_empty_legs_tuple_is_default(self):
        """T-M5-01: Order constructed without `legs` kwarg defaults to ()."""
        from v5.orders import Order, TriggerType
        order = Order.arm(
            strategy_id="s1", token="BTC", direction=1,
            trigger=TriggerType.BAR_CLOSE, trigger_price=0.0,
            working_price_source="last",
            armed_at=_dt("2026-04-01T00:00:00"),
            expires_at=None, sizing_ctx={},
        )
        assert order.legs == ()

    def test_immediate_market_order_full_cycle_single_tick(self):
        """T-M5-01: BAR_CLOSE trigger → ARMED → TRIGGERED → RELEASED → FILLED
        in a single _process_orders pass (no extra serialized state events)."""
        from v5.orders import Order, OrderStatus, TriggerType
        order = Order.arm(
            strategy_id="s1", token="BTC", direction=1,
            trigger=TriggerType.BAR_CLOSE, trigger_price=0.0,
            working_price_source="last",
            armed_at=_dt("2026-04-01T00:00:00"),
            expires_at=None, sizing_ctx={},
        )
        assert order.state == OrderStatus.ARMED
        order = order.trigger_immediately()
        assert order.state == OrderStatus.TRIGGERED
        order = order.release(capital_ok=True)
        assert order.state == OrderStatus.RELEASED
        order = order.on_fill(filled_qty=1.0, leaves_qty=0.0)
        assert order.state == OrderStatus.FILLED

    def test_immediate_market_order_emits_only_filled_event(self):
        """T-M5-01: for immediate market orders the state-machine cycle is
        pure control flow — NO intermediate armed/triggered/released events
        in orders_log.jsonl, only the 'filled' event."""
        from v5.orders import Order, TriggerType
        order = Order.arm(
            strategy_id="s1", token="BTC", direction=1,
            trigger=TriggerType.BAR_CLOSE, trigger_price=0.0,
            working_price_source="last",
            armed_at=_dt("2026-04-01T00:00:00"),
            expires_at=None, sizing_ctx={},
        )
        order = order.trigger_immediately()
        order = order.release(capital_ok=True)
        order = order.on_fill(filled_qty=1.0, leaves_qty=0.0)
        events = list(order.emitted_events())
        names = [e.get("event") for e in events]
        assert names == ["filled"], (
            f"Immediate market order must emit only 'filled' event; got {names}"
        )


class TestTM518HourlyBacktestParity:
    """T-M5-18: AC14 — hourly-only trade archive bit-identical pre-M5 vs post-M5."""

    def test_hourly_parity_fixture_bit_identical(self):
        """T-M5-18: hourly-only engine output is byte-stable against a
        committed regression fixture.

        Dispute resolution note (M5 Round 2 re-review, 2026-04-18):
        the original framing of this test — "pre-M5 vs post-M5 engine
        byte equivalence" — was tautological. The facade at
        ``v5.backtest.run_backtest_mtf`` was a self-contained synthetic
        generator, and the fixture was produced by the same function;
        ``pre == post`` therefore only asserted that the generator was
        deterministic. There is no separate pre-M5 engine in this
        codebase to compare against retroactively — M4 parity is
        already locked at ``test_m4_parity_hourly.py`` against the
        ``parity_hourly_pre_m4.bin`` fixture.

        The honest regression this test now binds:
          * ``v5.backtest.run_backtest_mtf`` delegates to the REAL
            :func:`v5.simulator.run_backtest_mtf` with ``strategies=[]``,
            ``output_path=<tmp>``, ``seed=0xC0FFEE``.
          * The engine's ``_write_parity_archive`` writes an M4HP blob
            derived from ``_seeded_price_walk(seed, n_bars)``, the bar
            grid from (start_ts_ns, end_ts_ns, base_resolution.period_ns),
            and the tokens list.
          * The committed fixture is the snapshot of that blob at the
            point the M5 review was accepted. Any future change that
            alters the seeded walk, the bar-grid arithmetic, the M4HP
            header, or the archive serialization will flip the bytes
            and fail this test — which is the useful property.

        Fixture-conditional: regeneration via
        ``v5/tests/fixtures/generate_m5_hourly_parity.py`` on a
        legitimate upstream change; never silently re-snapshot.
        """
        from v5.backtest import run_backtest_mtf  # noqa: F401

        fixture_path = (
            _project_root / "v5" / "tests" / "fixtures"
            / "m5_hourly_parity_pre.bin"
        )
        if not fixture_path.is_file():
            pytest.fail(
                f"fixture missing: {fixture_path}. Run "
                f"v5/tests/fixtures/generate_m5_hourly_parity.py to "
                f"capture the M5 baseline from the real engine."
            )
        pre_bytes = fixture_path.read_bytes()
        # Post-M5 run: drives v5.simulator.run_backtest_mtf via the facade.
        post_bytes = run_backtest_mtf(
            strategy_id="s524", resolution="1h",
            start="2026-01-01", end="2026-01-07", seed=0xC0FFEE,
            serialize_to_bytes=True,
        )
        assert pre_bytes == post_bytes, (
            f"Hourly engine output drifted from committed fixture: "
            f"pre={len(pre_bytes)} bytes, post={len(post_bytes)} bytes. "
            f"If the change is intentional, regenerate via "
            f"v5/tests/fixtures/generate_m5_hourly_parity.py."
        )

    def test_hourly_shadow_replay_passes(self):
        """T-M5-18: shadow replay harness reports PASS for hourly-only fleet.

        Fixture-conditional: loads pre-M5 archive from
        ``v5/tests/fixtures/pre_m5_archive.json`` (M4 baseline capture per
        AC14). Runs post-M5 backtest with the same seed and feeds both into
        run_shadow_replay. If the fixture is missing, fails loudly.
        """
        import json
        from v5.tests.shadow_replay import run_shadow_replay
        from v5.backtest import run_backtest_mtf

        fixture_path = (
            _project_root / "v5" / "tests" / "fixtures"
            / "pre_m5_archive.json"
        )
        if not fixture_path.is_file():
            pytest.fail(
                f"requires M4 baseline capture per AC14: {fixture_path} "
                f"missing. Generate via Phase 4 Task 21 harness."
            )
        pre_archive = json.loads(fixture_path.read_text())
        post_archive = run_backtest_mtf(
            strategy_id="s524", resolution="1h",
            start="2026-01-01", end="2026-01-07", seed=0xC0FFEE,
        )
        report = run_shadow_replay(
            pre_trades=pre_archive, post_trades=post_archive,
        )
        assert report.status == "PASS", (
            f"Shadow replay FAIL: {report.failing_trades} failing trades "
            f"out of {len(pre_archive)}"
        )
        # M5 post-rename must still expose Order via v5.orders.
        from v5.orders import Order, OrderStatus, TriggerType  # noqa: F401

    def test_legs_tuple_default_preserves_m4_behavior(self):
        """T-M5-01 + T-M5-18: Order(legs=()) behaves identically to the
        M4 PendingEntry state machine (state transitions match)."""
        from v5.orders import Order, OrderStatus, TriggerType
        order = Order.arm(
            strategy_id="s1", token="BTC", direction=1,
            trigger=TriggerType.PRICE_ABOVE, trigger_price=100.0,
            working_price_source="last",
            armed_at=_dt("2026-04-01T00:00:00"),
            expires_at=None, sizing_ctx={},
        )
        # ARMED -> TRIGGERED -> RELEASED -> FILLED (M4 baseline)
        order = order.on_price(price=101.0)
        assert order.state == OrderStatus.TRIGGERED
        order = order.release(capital_ok=True)
        assert order.state == OrderStatus.RELEASED
        order = order.on_fill(filled_qty=1.0, leaves_qty=0.0)
        assert order.state == OrderStatus.FILLED
