"""M9 C-9 — CapitalAllocationPolicy pluggable + AllocationState extension.

Covers AC #13:
- PortfolioConfig.capital_allocation_policy field defaults to SharedPoolPolicy
- Hot-swap protection raises ConfigError when position book non-empty
- AllocationState carries market_snapshot at bar_close
- FIX 1098 + 1099 dual-stamp in sizing_fills.jsonl

All tests MUST FAIL today — pluggable field, ConfigError hot-swap guard,
market_snapshot field, and fix_1098/fix_1099 schema do not exist yet.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


class TestCapitalAllocationPolicyDefault:
    """AC #13 — PortfolioConfig.capital_allocation_policy defaults to SharedPoolPolicy."""

    def test_default_is_shared_pool_policy(self):
        from v5.config import PortfolioConfig
        from v5.sizing.allocation import SharedPoolPolicy

        cfg = PortfolioConfig(strategies=[], capital=10_000.0)
        assert isinstance(cfg.capital_allocation_policy, SharedPoolPolicy), (
            "PortfolioConfig.capital_allocation_policy must default to SharedPoolPolicy"
        )


class TestHotSwapProtection:
    """AC #13 — changing capital_allocation_policy with non-empty position book raises."""

    def test_hot_swap_with_open_positions_raises_config_error(self):
        from v5.config import ConfigError, PortfolioConfig
        from v5.sizing.allocation import SharedPoolPolicy

        cfg = PortfolioConfig(strategies=[], capital=10_000.0)

        # Simulate non-empty position book via engine-attached state
        class _FakePositionBook:
            def __len__(self): return 3
            def is_empty(self): return False

        # Attach the book (mechanism is config-implementation-defined; test behavior)
        cfg._attach_position_book(_FakePositionBook())  # type: ignore[attr-defined]

        new_policy = SharedPoolPolicy()
        with pytest.raises(ConfigError, match="hot-swap|position book|non-empty"):
            cfg.capital_allocation_policy = new_policy


class TestAllocationStateMarketSnapshot:
    """AC #13 — AllocationState TypedDict carries market_snapshot populated at bar_close."""

    def test_allocation_state_has_market_snapshot_field(self):
        from typing import get_type_hints

        from v5.sizing.allocation import AllocationState

        # market_snapshot must be declared as an explicit TypedDict key,
        # not merely accepted by total=False kwargs. Verify via __annotations__.
        hints = get_type_hints(AllocationState)
        assert "market_snapshot" in hints, (
            "AllocationState must declare 'market_snapshot' as a typed field; "
            f"current keys: {sorted(hints.keys())}"
        )
        # And the field must be populated correctly at construction
        state: AllocationState = AllocationState(
            available_margin=1_000.0,
            per_strategy_equity={"s1": 500.0},
            rolling_pnl_24h={},
            current_positions_notional={},
            market_snapshot={"BTC": 50_000.0, "regime_flag": 1.0},
        )
        assert state["market_snapshot"]["BTC"] == 50_000.0
        assert state["market_snapshot"]["regime_flag"] == 1.0

    def test_market_snapshot_populated_at_bar_close_sampling(self):
        """Engine fills AllocationState.market_snapshot when invoking policy at bar_close."""
        from v5.config import PortfolioConfig
        from v5.sizing.allocation import CapitalAllocationPolicy
        from v5.simulator import simulate_portfolio
        from v5.universe_context import UniverseContext

        captured = {}

        class _CapturingPolicy(CapitalAllocationPolicy):
            sampling_cadence = "bar_close"
            def available_capital(self, strategy_id, state, clock_now_ns):
                captured["state"] = dict(state)
                return 1_000.0

        cfg = PortfolioConfig(
            strategies=[], capital=10_000.0,
            capital_allocation_policy=_CapturingPolicy(),
        )
        ctx = UniverseContext.build_test(
            tokens=["BTC"], bars=10, seed=0, equity=10_000.0
        )
        simulate_portfolio(strategies={}, config=cfg, ctx=ctx)

        assert "state" in captured
        assert "market_snapshot" in captured["state"]
        assert isinstance(captured["state"]["market_snapshot"], dict)


class TestFIX1098And1099DualStamp:
    """AC #13 — sizing_fills.jsonl records fix_1098 + fix_1099 keys alongside strategy_id."""

    def test_sizing_fills_jsonl_contains_fix_1098_and_1099(self):
        from v5.sizing.binding_log import BindingLogWriter

        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "sizing_fills.jsonl"
            writer = BindingLogWriter(log_path=log_path)
            writer.write({
                "order_id": "ord-42",
                "strategy_id": "s524m",
                "binding_constraint": "concentration",
                "requested_notional_usd": 1_000.0,
                "final_notional_usd": 500.0,
            })
            writer.close()

            with log_path.open() as fh:
                record = json.loads(fh.readline())

            assert record["strategy_id"] == "s524m"
            assert record["fix_1098"] == "s524m", (
                "FIX StrategyID(1098) must mirror strategy_id"
            )
            assert "fix_1099" in record, (
                "FIX 1099 StrategyParameters placeholder key must exist (None OK)"
            )
