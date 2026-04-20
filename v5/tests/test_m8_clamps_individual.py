"""M8 AC-Sz3 — Individual clamp semantics (one clamp binding per test).

6 clamps: ADV cap → concentration → free capital → min size → liq distance → slippage.
Each test constructs a scenario where ONLY that clamp is binding.

Canonical API:
  run_clamp_pipeline(order, *, available_capital_usd, market_state, policy, config)
  → returns (new_order, binding_log_dict). On REJECT, new_order.state == REJECTED.

All tests MUST FAIL today — v5.sizing clamps do not exist.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


# ---------------------------------------------------------------------------
# Binance perp USDT-M tiered MMR schedule (AC-Sz3 clause 5 source-of-truth).
# Reference: https://www.binance.com/en/futures/trading-rules/perpetual/leverage-margin
# Values below are the bracket floors for BTCUSDT perp (2026 schedule snapshot).
# Structure: [(notional_ceiling_usd, maintenance_margin_ratio), ...]
# Tier 1: 0–50k USDT at 0.50% MMR (below the first bracket cap)
# Tier 2: 50k–250k USDT at 0.65% MMR (above 50k, below 250k)
# ---------------------------------------------------------------------------
BINANCE_BTCUSDT_MMR_SCHEDULE = [
    (50_000.0, 0.0050),        # tier 1
    (250_000.0, 0.0065),       # tier 2
    (1_000_000.0, 0.0100),     # tier 3
    (5_000_000.0, 0.0200),     # tier 4
    (20_000_000.0, 0.0500),    # tier 5
]


def _make_order(ctx, *, notional_usd, leverage=1.0, reduce_only=False,
                margin_mode="isolated", direction="LONG"):
    """Minimal Order helper for clamp tests.

    Constructs a single-leg Order via ctx.orders.arm(...) + a SizingRequest.
    """
    from v5.orders import TriggerType
    from v5.sizing.intents import SizingIntent, SizingRequest
    order = ctx.orders.arm(
        symbol="BTC", direction=direction, size=1.0,
        trigger=TriggerType.PRICE_ABOVE, trigger_price=50_000.0,
        sizing=SizingRequest(
            intent=SizingIntent.FIXED_NOTIONAL,
            notional_usd=notional_usd,
            leverage=leverage,
            reduce_only=reduce_only,
            margin_mode=margin_mode,
        ),
    )
    return order.trigger_immediately()


@pytest.fixture
def ctx():
    from v5.universe_context import UniverseContext
    return UniverseContext.build_test(tokens=["BTC"], bars=100, seed=0,
                                      equity=10_000_000.0)


class _SyntheticMarketState:
    """Minimal MarketState implementing the canonical Protocol."""

    def __init__(self, **state):
        self._state = dict(state)

    def adv(self, token):
        return self._state["adv"]

    def rolling_adv(self, token, window_hours=24):
        return self._state["adv"]

    def mark_price(self, token):
        return self._state["mark_price"]

    def free_margin(self, strategy_id, policy):
        # Delegates to policy — policy.available_capital reads the
        # allocation state dict.
        from v5.sizing.allocation import AllocationState
        state = AllocationState(
            available_margin=self._state["available_margin"],
            per_strategy_equity={strategy_id: self._state["equity"]},
            rolling_pnl_24h={},
            current_positions_notional={},
        )
        return policy.available_capital(strategy_id, state, 0)

    def liquidation_distance(self, position, leverage):
        return self._state["liquidation_distance_bps"]

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


class TestAdvCapClamp:
    """AC-Sz3 #1 — ADV cap: position notional <= adv_cap_pct × ADV."""

    def test_adv_cap_binds_when_request_exceeds_adv(self, ctx):
        from v5.sizing.clamps import run_clamp_pipeline
        order = _make_order(ctx, notional_usd=1_000_000.0)
        new_order, log = run_clamp_pipeline(
            order,
            available_capital_usd=10_000_000.0,
            market_state=_market_state(adv=1_000_000.0),
            policy=_policy(),
            config=_config(adv_cap_pct=0.05),  # 5% ADV → $50k cap
        )
        assert log["binding_constraint"] == "adv_cap"
        assert log["filled_notional"] == pytest.approx(50_000.0)


class TestConcentrationClamp:
    """AC-Sz3 #2 — Concentration: position notional <= concentration_limit × equity."""

    def test_concentration_binds_when_request_exceeds_limit(self, ctx):
        from v5.sizing.clamps import run_clamp_pipeline
        order = _make_order(ctx, notional_usd=80_000.0)  # exceeds 10% cap on $100k equity
        new_order, log = run_clamp_pipeline(
            order,
            available_capital_usd=10_000_000.0,
            market_state=_market_state(equity=100_000.0),
            policy=_policy(),
            config=_config(concentration_limit=0.10),  # 10% → $10k
        )
        assert log["binding_constraint"] == "concentration"
        assert log["filled_notional"] == pytest.approx(10_000.0)


class TestFreeCapitalClamp:
    """AC-Sz3 #3 — Free capital: position notional <= available margin."""

    def test_free_capital_binds_when_request_exceeds_margin(self, ctx):
        from v5.sizing.clamps import run_clamp_pipeline
        order = _make_order(ctx, notional_usd=50_000.0, leverage=1.0)
        new_order, log = run_clamp_pipeline(
            order,
            available_capital_usd=5_000.0,  # only $5K free
            market_state=_market_state(available_margin=5_000.0),
            policy=_policy(),
            config=_config(),
        )
        assert log["binding_constraint"] == "free_capital"


class TestMinSizeClamp:
    """AC-Sz3 #4 — Min size: request < min_position_usd → REJECTED."""

    def test_min_size_rejects_below_threshold(self, ctx):
        from v5.orders import OrderStatus
        from v5.sizing.clamps import run_clamp_pipeline
        order = _make_order(ctx, notional_usd=5.0)  # below $10 minimum
        new_order, log = run_clamp_pipeline(
            order,
            available_capital_usd=10_000_000.0,
            market_state=_market_state(),
            policy=_policy(),
            config=_config(min_position_usd=10.0),
        )
        assert new_order.state == OrderStatus.REJECTED
        assert new_order.reject_reason == "min_size"


class TestLiquidationDistanceClamp:
    """AC-Sz3 #5 — Liquidation distance: reject if notional × leverage pushes liq inside stop.

    Two tier-spanning tests using Binance tiered MMR schedule. Under the same
    leverage the liquidation distance MUST differ between tiers (tier 2 MMR is
    higher, so liquidation price is closer to mark → distance is SMALLER).
    """

    def test_high_leverage_pushes_liq_inside_stop(self, ctx):
        from v5.orders import OrderStatus
        from v5.sizing.clamps import run_clamp_pipeline
        order = _make_order(ctx, notional_usd=50_000.0, leverage=50.0)
        new_order, log = run_clamp_pipeline(
            order,
            available_capital_usd=10_000_000.0,
            market_state=_market_state(
                equity=100_000.0,
                liquidation_distance_bps=200.0,
            ),
            policy=_policy(),
            config=_config(min_liquidation_distance_bps=500.0),
        )
        assert new_order.state == OrderStatus.REJECTED
        assert new_order.reject_reason == "liquidation_distance"

    def test_tier1_30k_notional_at_10x_leverage_uses_tier1_mmr(self, ctx):
        """Position notional $30k at 10x → falls in Binance tier 1 (MMR 0.50%).

        Expected: liquidation_distance_bps computed using tier 1 MMR.
        """
        from v5.sizing.clamps import run_clamp_pipeline
        order = _make_order(ctx, notional_usd=30_000.0, leverage=10.0)
        new_order, log = run_clamp_pipeline(
            order,
            available_capital_usd=10_000_000.0,
            market_state=_market_state(equity=100_000.0),
            policy=_policy(),
            config=_config(
                min_liquidation_distance_bps=50.0,
                mmr_schedule=BINANCE_BTCUSDT_MMR_SCHEDULE,
            ),
        )
        # Tier 1 MMR is 0.50%; at 10x, liq distance ≈ (1/10 - 0.005) = 9.50% = 950 bps
        tier1_liq_bps = log["clamp_values"]["liq_distance"]
        assert tier1_liq_bps == pytest.approx(950.0, rel=1e-4)

    def test_tier2_80k_notional_at_10x_leverage_uses_tier2_mmr(self, ctx):
        """Position notional $80k at 10x → falls in Binance tier 2 (MMR 0.65%).

        Expected: SAME leverage but DIFFERENT liquidation distance because
        tier 2 MMR (0.65%) > tier 1 MMR (0.50%).
        """
        from v5.sizing.clamps import run_clamp_pipeline
        order = _make_order(ctx, notional_usd=80_000.0, leverage=10.0)
        new_order, log = run_clamp_pipeline(
            order,
            available_capital_usd=10_000_000.0,
            market_state=_market_state(equity=1_000_000.0),
            policy=_policy(),
            config=_config(
                min_liquidation_distance_bps=50.0,
                mmr_schedule=BINANCE_BTCUSDT_MMR_SCHEDULE,
            ),
        )
        # Tier 2 MMR is 0.65%; at 10x, liq distance ≈ (1/10 - 0.0065) = 9.35% = 935 bps
        tier2_liq_bps = log["clamp_values"]["liq_distance"]
        assert tier2_liq_bps == pytest.approx(935.0, rel=1e-4)

    def test_tier_spanning_liq_distance_differs_at_same_leverage(self, ctx):
        """Same leverage, different tiers → different liq distances (MMR drift)."""
        from v5.sizing.clamps import run_clamp_pipeline
        order_t1 = _make_order(ctx, notional_usd=30_000.0, leverage=10.0)
        order_t2 = _make_order(ctx, notional_usd=80_000.0, leverage=10.0)
        _, log_t1 = run_clamp_pipeline(
            order_t1,
            available_capital_usd=10_000_000.0,
            market_state=_market_state(equity=1_000_000.0),
            policy=_policy(),
            config=_config(
                min_liquidation_distance_bps=50.0,
                mmr_schedule=BINANCE_BTCUSDT_MMR_SCHEDULE,
            ),
        )
        _, log_t2 = run_clamp_pipeline(
            order_t2,
            available_capital_usd=10_000_000.0,
            market_state=_market_state(equity=1_000_000.0),
            policy=_policy(),
            config=_config(
                min_liquidation_distance_bps=50.0,
                mmr_schedule=BINANCE_BTCUSDT_MMR_SCHEDULE,
            ),
        )
        t1_dist = log_t1["clamp_values"]["liq_distance"]
        t2_dist = log_t2["clamp_values"]["liq_distance"]
        assert t1_dist != pytest.approx(t2_dist), (
            f"Tier 1 and tier 2 must produce DIFFERENT liquidation distances "
            f"at same leverage (MMR differs); got t1={t1_dist} t2={t2_dist}"
        )
        # Tier 2 must have SMALLER distance (MMR is higher → liq closer)
        assert t2_dist < t1_dist


class TestSlippageClamp:
    """AC-Sz3 #6 — Slippage: SqrtImpact adjusts fill_price (NOT a reject)."""

    def test_slippage_adjusts_fill_price_no_reject(self, ctx):
        from v5.orders import OrderStatus
        from v5.sizing.clamps import run_clamp_pipeline
        order = _make_order(ctx, notional_usd=100_000.0)
        new_order, log = run_clamp_pipeline(
            order,
            available_capital_usd=10_000_000.0,
            market_state=_market_state(adv=10_000_000.0),
            policy=_policy(),
            config=_config(),
        )
        # Slippage is NOT a reject; fill_price shifts from mark_price
        assert new_order.state != OrderStatus.REJECTED
        assert log["fill_price"] != pytest.approx(50_000.0)
        assert log["slippage_bps"] > 0

    def test_slippage_never_binds_even_at_extreme_notional(self, ctx):
        """Slippage clamp NEVER rejects + binding_constraint is NOT 'slippage'.

        Request an excessive notional (100x ADV) — slippage is a price-adjust,
        not a reducer, so binding_constraint must be some other clamp (or 'none').
        """
        from v5.orders import OrderStatus
        from v5.sizing.clamps import run_clamp_pipeline
        order = _make_order(ctx, notional_usd=100_000_000.0)  # 100x ADV
        new_order, log = run_clamp_pipeline(
            order,
            available_capital_usd=10_000_000_000.0,
            market_state=_market_state(adv=1_000_000.0),
            policy=_policy(),
            config=_config(),
        )
        assert log["binding_constraint"] != "slippage", (
            f"slippage must never be the binding constraint; got {log['binding_constraint']}"
        )
        assert new_order.state != OrderStatus.REJECTED or new_order.reject_reason != "slippage"
        import math
        sl_bps = log.get("slippage_bps", 0.0)
        assert sl_bps is not None and math.isfinite(sl_bps) and sl_bps > 0
