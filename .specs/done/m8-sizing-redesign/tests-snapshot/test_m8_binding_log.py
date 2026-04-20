"""M8 AC-Sz5 — Per-fill binding-constraint JSONL log.

Every fill emits one JSONL entry to v5/logs/sizing_fills.jsonl via
write_sizing_fill_entry() in v5/sizing/binding_log.py.

Schema fields (strategy_id = FIX StrategyID(1098) vocabulary, NOT
Party(448)/party_id prime-broker give-up vocabulary).

All tests MUST FAIL today — v5.sizing.binding_log does not exist.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


REQUIRED_LOG_FIELDS = {
    "timestamp", "order_id", "symbol", "strategy_id", "intent",
    "requested_fraction", "requested_notional", "leverage",
    "clamp_values", "binding_constraint",
    "filled_margin", "filled_notional", "fill_price", "slippage_bps",
    "error",
}

REQUIRED_CLAMP_KEYS = {
    "adv_cap", "concentration", "free_capital",
    "min_size", "liquidation_distance", "slippage",
}


class _SyntheticMarketState:
    def __init__(self, **state):
        self._state = dict(state)

    def adv(self, token):
        return self._state["adv"]

    def rolling_adv(self, token, window_hours=24):
        return self._state["adv"]

    def mark_price(self, token):
        return self._state["mark_price"]

    def free_margin(self, strategy_id, policy):
        from v5.sizing.allocation import AllocationState
        state = AllocationState(
            available_margin=self._state["available_margin"],
            per_strategy_equity={strategy_id: self._state["equity"]},
            rolling_pnl_24h={},
            current_positions_notional={},
        )
        return policy.available_capital(strategy_id, state, 0)

    def liquidation_distance(self, position, leverage):
        return self._state.get("liquidation_distance_bps", 10_000.0)

    def equity(self, strategy_id):
        return self._state["equity"]


def _market_state(**kw):
    base = dict(
        adv=1_000_000_000.0,
        mark_price=50_000.0,
        available_margin=10_000_000.0,
        equity=10_000_000.0,
        liquidation_distance_bps=10_000.0,
    )
    base.update(kw)
    return _SyntheticMarketState(**base)


def _policy():
    from v5.sizing.allocation import SharedPoolPolicy
    return SharedPoolPolicy()


def _config(**kw):
    from v5.sizing.clamps import ClampsConfig
    base = dict(
        adv_cap_pct=1.0,
        concentration_limit=1.0,
        min_position_usd=10.0,
        min_liquidation_distance_bps=100.0,
    )
    base.update(kw)
    return ClampsConfig(**base)


@pytest.fixture
def ctx():
    from v5.universe_context import UniverseContext
    return UniverseContext.build_test(tokens=["BTC"], bars=100, seed=0,
                                      equity=10_000_000.0)


def _make_order(ctx, *, fraction=0.02, notional=None):
    from v5.orders import TriggerType
    from v5.sizing.intents import SizingIntent, SizingRequest
    if notional is not None:
        sizing = SizingRequest(intent=SizingIntent.FIXED_NOTIONAL,
                               notional_usd=notional)
    else:
        sizing = SizingRequest(intent=SizingIntent.FIXED_FRACTION,
                               fraction_of_equity=fraction)
    order = ctx.orders.arm(
        symbol="BTC", direction="LONG", size=1.0,
        trigger=TriggerType.PRICE_ABOVE, trigger_price=50_000.0,
        sizing=sizing,
    )
    return order.trigger_immediately()


class TestBindingLogSchema:
    """AC-Sz5 — each entry conforms to the documented schema."""

    def test_log_entry_emitted_on_fill(self, ctx, tmp_path):
        from v5.sizing.binding_log import write_sizing_fill_entry
        from v5.sizing.clamps import run_clamp_pipeline
        log_path = tmp_path / "sizing_fills.jsonl"
        order = _make_order(ctx, fraction=0.02)
        _, binding = run_clamp_pipeline(
            order,
            available_capital_usd=10_000_000.0,
            market_state=_market_state(equity=100_000.0),
            policy=_policy(),
            config=_config(),
        )
        write_sizing_fill_entry(order, binding, binding="none", path=log_path)
        lines = log_path.read_text().splitlines()
        assert len(lines) == 1

    def test_log_entry_has_all_required_fields(self, ctx, tmp_path):
        from v5.sizing.binding_log import write_sizing_fill_entry
        from v5.sizing.clamps import run_clamp_pipeline
        log_path = tmp_path / "sizing_fills.jsonl"
        order = _make_order(ctx, fraction=0.02)
        _, binding = run_clamp_pipeline(
            order,
            available_capital_usd=10_000_000.0,
            market_state=_market_state(equity=100_000.0),
            policy=_policy(),
            config=_config(),
        )
        write_sizing_fill_entry(order, binding, binding="none", path=log_path)
        entry = json.loads(log_path.read_text().splitlines()[0])
        missing = REQUIRED_LOG_FIELDS - set(entry.keys())
        assert not missing, f"Log entry missing fields: {missing}"

    def test_clamp_values_has_six_keys(self, ctx, tmp_path):
        from v5.sizing.binding_log import write_sizing_fill_entry
        from v5.sizing.clamps import run_clamp_pipeline
        log_path = tmp_path / "sizing_fills.jsonl"
        order = _make_order(ctx, fraction=0.02)
        _, binding = run_clamp_pipeline(
            order,
            available_capital_usd=10_000_000.0,
            market_state=_market_state(equity=100_000.0),
            policy=_policy(),
            config=_config(),
        )
        write_sizing_fill_entry(order, binding, binding="none", path=log_path)
        entry = json.loads(log_path.read_text().splitlines()[0])
        assert set(entry["clamp_values"].keys()) == REQUIRED_CLAMP_KEYS

    def test_log_schema_uses_fix_1098_strategy_id_not_party_448(self, ctx, tmp_path):
        """FIX StrategyID(1098) vocabulary only — no Party(448)/party_id drift."""
        from v5.sizing.binding_log import write_sizing_fill_entry
        from v5.sizing.clamps import run_clamp_pipeline
        log_path = tmp_path / "sizing_fills.jsonl"
        order = _make_order(ctx, fraction=0.02)
        _, binding = run_clamp_pipeline(
            order,
            available_capital_usd=10_000_000.0,
            market_state=_market_state(equity=100_000.0),
            policy=_policy(),
            config=_config(),
        )
        write_sizing_fill_entry(order, binding, binding="none", path=log_path)
        entry = json.loads(log_path.read_text().splitlines()[0])
        # Positive: strategy_id field exists (FIX 1098 vocabulary)
        assert "strategy_id" in entry
        # Negative: no prime-broker give-up vocabulary has leaked in.
        assert "party_id" not in entry, (
            "Binding log must use FIX StrategyID(1098), NOT Party(448). "
            "Found party_id — this is a give-up/allocation vocabulary regression."
        )
        assert "fix_448" not in entry
        assert "party_role" not in entry
        assert "party_role_53" not in entry


class TestBindingLogConcentration:
    """AC-Sz5 — concentration-binding scenario produces correct binding_constraint."""

    def test_concentration_clamp_records_in_log(self, ctx, tmp_path):
        from v5.sizing.binding_log import write_sizing_fill_entry
        from v5.sizing.clamps import run_clamp_pipeline
        log_path = tmp_path / "sizing_fills.jsonl"
        order = _make_order(ctx, fraction=0.80)
        _, binding = run_clamp_pipeline(
            order,
            available_capital_usd=10_000_000.0,
            market_state=_market_state(equity=100_000.0),
            policy=_policy(),
            config=_config(concentration_limit=0.10),
        )
        write_sizing_fill_entry(order, binding,
                                binding=binding.get("binding_constraint", "none"),
                                path=log_path)
        entry = json.loads(log_path.read_text().splitlines()[0])
        assert entry["binding_constraint"] == "concentration"
        assert entry["error"] is None


class TestBindingLogErrorPath:
    """AC-Sz5 — clamp exception produces entry with error populated."""

    def test_error_path_log_entry(self, ctx, tmp_path, monkeypatch):
        from v5.sizing import clamps as clamps_mod
        from v5.sizing.binding_log import write_sizing_fill_entry

        def boom(*a, **kw):
            raise ValueError("simulated clamp failure")

        monkeypatch.setattr(clamps_mod, "_adv_cap_clamp", boom, raising=False)

        log_path = tmp_path / "sizing_fills.jsonl"
        order = _make_order(ctx, fraction=0.02)
        new_order, binding = clamps_mod.run_clamp_pipeline(
            order,
            available_capital_usd=10_000_000.0,
            market_state=_market_state(equity=100_000.0),
            policy=_policy(),
            config=_config(),
        )
        write_sizing_fill_entry(
            new_order, binding,
            binding=f"clamp_error_adv_cap",
            error="simulated clamp failure",
            path=log_path,
        )
        entry = json.loads(log_path.read_text().splitlines()[0])
        assert entry["error"] is not None
        assert "simulated clamp failure" in str(entry["error"])
