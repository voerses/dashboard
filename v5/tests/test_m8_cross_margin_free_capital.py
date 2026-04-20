"""M8 — Free-capital clamp under isolated vs cross margin modes.

Under margin_mode="isolated": free-capital = per-position margin (sum independent)
Under margin_mode="cross": free-capital = portfolio-level (positions net against each other)

The two modes MUST produce different available_capital_usd for the same
gross position book.

Canonical API: run_clamp_pipeline(order, *, available_capital_usd, market_state,
policy, config).

All tests MUST FAIL today — margin-mode aware free-capital clamp not wired.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


@pytest.fixture
def ctx():
    from v5.universe_context import UniverseContext
    return UniverseContext.build_test(tokens=["BTC"], bars=100, seed=0,
                                      equity=100_000.0)


class _PortfolioMarketState:
    """MarketState adapter stub that carries portfolio state (positions,
    unrealized_pnl, maintenance_margin) for cross-mode free-capital testing.
    """

    def __init__(self, *, positions, wallet_balance, mark_price=50_000.0,
                 adv=1_000_000_000.0, liquidation_distance_bps=10_000.0):
        self._positions = list(positions)
        self._wallet = wallet_balance
        self._mark = mark_price
        self._adv = adv
        self._liq = liquidation_distance_bps

    def adv(self, token):
        return self._adv

    def rolling_adv(self, token, window_hours=24):
        return self._adv

    def mark_price(self, token):
        return self._mark

    def equity(self, strategy_id):
        return self._wallet

    def liquidation_distance(self, position, leverage):
        return self._liq

    # The free-capital clamp calls market_state.free_margin(strategy_id, policy)
    # and must branch by SizingRequest.margin_mode. We expose portfolio state
    # so the clamp can compute both branches.
    def portfolio_snapshot(self):
        return {
            "positions": self._positions,
            "wallet_balance": self._wallet,
        }

    def free_margin(self, strategy_id, policy):
        from v5.sizing.allocation import AllocationState
        state = AllocationState(
            available_margin=self._wallet,
            per_strategy_equity={strategy_id: self._wallet},
            rolling_pnl_24h={},
            current_positions_notional={},
        )
        return policy.available_capital(strategy_id, state, 0)


class TestMarginModeBranching:
    """AC-Sz3 clause 3 — cross-margin formula differs from isolated.

    Binance cross-margin 'available' is:
      walletBalance + unrealizedProfit - initialMargin - maintenanceMargin
      - openOrderInitialMargin

    Parametrized scenarios:
    (a) net positive unrealized PnL (cross > isolated)
    (b) net negative unrealized PnL (cross < isolated)
    (c) deep drawdown (cross would force-liquidate, isolated contains loss)
    """

    @pytest.mark.parametrize(
        "scenario,positions,wallet,expected_cross_gt_iso",
        [
            (
                "net_positive_pnl",
                [
                    {"symbol": "BTC", "initial_margin": 10_000.0,
                     "maintenance_margin": 400.0, "unrealized_pnl": +5_000.0,
                     "open_order_initial_margin": 0.0},
                    {"symbol": "ETH", "initial_margin": 8_000.0,
                     "maintenance_margin": 320.0, "unrealized_pnl": +2_000.0,
                     "open_order_initial_margin": 0.0},
                ],
                100_000.0,
                True,   # cross nets positive PnL → > isolated
            ),
            (
                "net_negative_pnl",
                [
                    {"symbol": "BTC", "initial_margin": 10_000.0,
                     "maintenance_margin": 400.0, "unrealized_pnl": -3_000.0,
                     "open_order_initial_margin": 0.0},
                    {"symbol": "ETH", "initial_margin": 8_000.0,
                     "maintenance_margin": 320.0, "unrealized_pnl": -2_000.0,
                     "open_order_initial_margin": 0.0},
                ],
                100_000.0,
                False,  # cross nets negative PnL → < isolated
            ),
        ],
    )
    def test_cross_vs_isolated_differs_by_unrealized_pnl(
        self, scenario, positions, wallet, expected_cross_gt_iso,
    ):
        from v5.sizing.market_state import compute_available_capital_usd
        iso = compute_available_capital_usd(
            margin_mode="isolated",
            portfolio={"positions": positions, "wallet_balance": wallet},
        )
        cross = compute_available_capital_usd(
            margin_mode="cross",
            portfolio={"positions": positions, "wallet_balance": wallet},
        )
        assert iso != pytest.approx(cross), (
            f"{scenario}: iso vs cross must differ; iso={iso} cross={cross}"
        )
        if expected_cross_gt_iso:
            assert cross > iso
        else:
            assert cross < iso

    def test_cross_deep_drawdown_would_force_liquidation(self):
        """(c) A position deep enough in drawdown — cross mode available_capital
        falls below 0 (would force liquidation); isolated stays positive.
        """
        from v5.sizing.market_state import compute_available_capital_usd
        positions = [
            {"symbol": "BTC", "initial_margin": 10_000.0,
             "maintenance_margin": 400.0, "unrealized_pnl": -95_000.0,
             "open_order_initial_margin": 0.0},
        ]
        wallet = 100_000.0
        iso = compute_available_capital_usd(
            margin_mode="isolated",
            portfolio={"positions": positions, "wallet_balance": wallet},
        )
        cross = compute_available_capital_usd(
            margin_mode="cross",
            portfolio={"positions": positions, "wallet_balance": wallet},
        )
        # Cross subtracts entire unrealized loss from pool → near zero or neg
        assert cross <= 10_000.0
        # Isolated only loses the position's initial_margin (10k); rest of
        # wallet is untouched.
        assert iso > cross, (
            f"isolated must preserve more capital during drawdown; "
            f"iso={iso} cross={cross}"
        )


class TestClampBranchingByMarginMode:
    """The free-capital clamp reads the right pool given SizingRequest.margin_mode."""

    def _order_with(self, ctx, *, margin_mode):
        from v5.orders import TriggerType
        from v5.sizing.intents import SizingIntent, SizingRequest
        order = ctx.orders.arm(
            symbol="BTC", direction="LONG", size=1.0,
            trigger=TriggerType.PRICE_ABOVE, trigger_price=50_000.0,
            sizing=SizingRequest(
                intent=SizingIntent.FIXED_NOTIONAL,
                notional_usd=30_000.0,
                margin_mode=margin_mode,
            ),
        )
        return order.trigger_immediately()

    def test_cross_mode_allows_larger_position_when_pnl_positive(self, ctx):
        from v5.sizing.allocation import SharedPoolPolicy
        from v5.sizing.clamps import ClampsConfig, run_clamp_pipeline
        positions = [
            {"symbol": "BTC", "initial_margin": 10_000.0,
             "maintenance_margin": 400.0, "unrealized_pnl": +5_000.0,
             "open_order_initial_margin": 0.0},
        ]
        mkt_iso = _PortfolioMarketState(positions=positions,
                                        wallet_balance=50_000.0)
        mkt_cross = _PortfolioMarketState(positions=positions,
                                          wallet_balance=50_000.0)

        cfg = ClampsConfig(
            adv_cap_pct=1.0, concentration_limit=1.0,
            min_position_usd=10.0, min_liquidation_distance_bps=100.0,
        )
        policy = SharedPoolPolicy()

        order_iso = self._order_with(ctx, margin_mode="isolated")
        order_cross = self._order_with(ctx, margin_mode="cross")

        _, log_iso = run_clamp_pipeline(
            order_iso, available_capital_usd=40_000.0,
            market_state=mkt_iso, policy=policy, config=cfg,
        )
        _, log_cross = run_clamp_pipeline(
            order_cross, available_capital_usd=45_000.0,
            market_state=mkt_cross, policy=policy, config=cfg,
        )
        assert log_cross["filled_notional"] >= log_iso["filled_notional"]
