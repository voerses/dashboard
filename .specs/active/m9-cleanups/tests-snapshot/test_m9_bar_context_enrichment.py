"""M9 AC #14 — BarContext reactive sizing surface.

Tests the enriched BarContext fields that strategies use for reactive
sizing patterns (scale-down on portfolio drawdown, reduce on ATR spike,
etc.). M9 adds:

- BarContext.ctx: UniverseContext (so strategies can call
  ctx.per_token().atr(), v5.regimes.detect_crisis directly)
- BarContext.state_view: StateView (read-only snapshot with
  equity, portfolio_dd_pct, open_positions_count, total_notional_usd,
  per_strategy_equity, per_symbol_exposure, bars_since_last_fill)

Signatures stay 2-arg: check_scale(pos, bar_ctx), check_exit(pos, bar_ctx).
ctx is on bar_ctx only, NOT a third arg.

All tests MUST FAIL today — StateView dataclass doesn't exist yet;
BarContext.ctx and .state_view fields don't exist yet.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


class TestBarContextCtxField:
    """AC #14 — BarContext.ctx reference."""

    def test_bar_context_has_ctx_field(self):
        from v5.exit_handlers import BarContext

        fields = {f.name for f in BarContext.__dataclass_fields__.values()}
        assert "ctx" in fields, (
            f"BarContext.ctx field required for C-1 reactive sizing. "
            f"Current fields: {sorted(fields)}"
        )

    def test_bar_context_ctx_is_universe_context_or_none(self):
        """ctx field type is UniverseContext (or None for backcompat)."""
        import typing
        from v5.exit_handlers import BarContext

        hints = typing.get_type_hints(BarContext)
        ctx_type = hints.get("ctx")
        # Allow Optional[UniverseContext]
        from v5.universe_context import UniverseContext

        type_str = str(ctx_type)
        assert (
            "UniverseContext" in type_str
        ), f"BarContext.ctx must be UniverseContext-typed; got {ctx_type}"


class TestBarContextStateViewField:
    """AC #14 — BarContext.state_view snapshot."""

    def test_bar_context_has_state_view_field(self):
        from v5.exit_handlers import BarContext

        fields = {f.name for f in BarContext.__dataclass_fields__.values()}
        assert "state_view" in fields

    def test_state_view_has_required_fields(self):
        """StateView carries 7 snapshot fields per AC #14."""
        from v5.exit_handlers import StateView

        required = {
            "equity",
            "portfolio_dd_pct",
            "open_positions_count",
            "total_notional_usd",
            "per_strategy_equity",
            "per_symbol_exposure",
            "bars_since_last_fill",
        }
        fields = {f.name for f in StateView.__dataclass_fields__.values()}
        missing = required - fields
        assert not missing, f"StateView missing fields: {missing}"

    def test_state_view_is_frozen(self):
        """StateView is a frozen dataclass (read-only snapshot)."""
        from v5.exit_handlers import StateView

        sv = StateView(
            equity=100_000.0,
            portfolio_dd_pct=0.0,
            open_positions_count=0,
            total_notional_usd=0.0,
            per_strategy_equity={},
            per_symbol_exposure={},
            bars_since_last_fill={},
        )
        with pytest.raises((AttributeError, Exception)):
            sv.equity = 200_000.0  # frozen raises FrozenInstanceError


class TestReactiveSizingPattern:
    """AC #14 — `scale down when portfolio dd > 5%` expressible in ≤5 lines
    using `bar_ctx.state_view.portfolio_dd_pct`."""

    def test_state_view_dd_pct_readable_in_check_scale(self):
        """Smoke: a check_scale implementation can read
        bar_ctx.state_view.portfolio_dd_pct without needing ctx= kwarg."""
        from v5.exit_handlers import BarContext, StateView

        sv = StateView(
            equity=80_000.0,
            portfolio_dd_pct=0.20,  # 20% drawdown
            open_positions_count=5,
            total_notional_usd=400_000.0,
            per_strategy_equity={"s524m_v5": 80_000.0},
            per_symbol_exposure={"BTCUSDT": 120_000.0},
            bars_since_last_fill={"s524m_v5": 3},
        )
        bar_ctx = BarContext(
            bar_idx=100,
            close=45_000.0,
            atr=1_200.0,
            regime=None,  # deleted in C-4
            ctx=None,  # optional
            state_view=sv,
        )

        # Pattern from design.md §10.2 — 5 lines, no ctx third-arg needed
        def check_scale(pos, bar_ctx):
            if bar_ctx.state_view.portfolio_dd_pct > 0.05:
                return ("reduce", -0.5)
            return None

        result = check_scale(pos=None, bar_ctx=bar_ctx)
        assert result == ("reduce", -0.5), (
            f"Reactive pattern must be expressible in ≤5 lines using "
            f"bar_ctx.state_view; got {result}"
        )
