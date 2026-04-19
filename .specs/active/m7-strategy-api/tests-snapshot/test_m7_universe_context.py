"""M7 — UniverseContext namespace split + read-only views (AC-S2, AC-S4).

All tests MUST FAIL today — v5.universe_context does not exist.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


class TestUniverseContextNamespaceSplit:
    """AC-S2 — UniverseContext exposes split facades."""

    def test_universe_context_importable(self):
        from v5.universe_context import UniverseContext  # noqa: F401

    def test_data_view_importable(self):
        from v5.universe_context import DataView  # noqa: F401

    def test_portfolio_view_importable(self):
        from v5.universe_context import PortfolioView  # noqa: F401

    def test_order_factory_view_importable(self):
        from v5.universe_context import OrderFactoryView  # noqa: F401

    def test_token_view_importable(self):
        from v5.universe_context import TokenView  # noqa: F401

    @pytest.mark.parametrize("facade", ["data", "portfolio", "clock", "orders"])
    def test_ctx_declares_facade_attribute(self, facade):
        from v5.universe_context import UniverseContext
        ann = getattr(UniverseContext, "__annotations__", {}) or {}
        assert facade in ann or hasattr(UniverseContext, facade), (
            f"UniverseContext must declare facade attribute {facade!r}"
        )

    @pytest.mark.parametrize("attr", ["fold_id", "fold_window"])
    def test_ctx_declares_fold_attrs(self, attr):
        from v5.universe_context import UniverseContext
        ann = getattr(UniverseContext, "__annotations__", {}) or {}
        assert attr in ann or hasattr(UniverseContext, attr)


class TestDataViewReadOnly:
    """AC-S2 — ctx.data.per_token(t).close is a READ-ONLY view."""

    def test_per_token_close_is_read_only(self):
        from v5.universe_context import UniverseContext
        ctx = UniverseContext.build_test(tokens=["BTC"], bars=100, seed=42)
        tv = ctx.data.per_token("BTC")
        with pytest.raises(ValueError):
            tv.close[50] = 999.0

    def test_per_token_arrays_flags_writeable_false(self):
        from v5.universe_context import UniverseContext
        ctx = UniverseContext.build_test(tokens=["BTC"], bars=50, seed=0)
        tv = ctx.data.per_token("BTC")
        assert isinstance(tv.close, np.ndarray)
        assert tv.close.flags.writeable is False

    def test_per_token_open_high_low_volume_read_only(self):
        from v5.universe_context import UniverseContext
        ctx = UniverseContext.build_test(tokens=["BTC"], bars=50, seed=0)
        tv = ctx.data.per_token("BTC")
        for attr in ("open", "high", "low", "volume"):
            assert getattr(tv, attr).flags.writeable is False

    def test_mutable_copy_yields_writable_array(self):
        from v5.universe_context import UniverseContext
        ctx = UniverseContext.build_test(tokens=["BTC"], bars=50, seed=0)
        tv = ctx.data.per_token("BTC")
        mut = ctx.mutable_copy(tv.close)
        assert mut.flags.writeable is True
        orig = tv.close.copy()
        mut[0] = -1.0
        assert np.array_equal(tv.close, orig)


class TestDataViewVenueArgument:
    """AC-S2 — per_token accepts optional venue= argument."""

    def test_per_token_default_venue(self):
        from v5.universe_context import UniverseContext
        ctx = UniverseContext.build_test(tokens=["BTC"], bars=50, seed=0)
        assert ctx.data.per_token("BTC") is not None

    def test_per_token_explicit_venue(self):
        from v5.data.streams import Venue
        from v5.universe_context import UniverseContext
        ctx = UniverseContext.build_test(tokens=["BTC"], bars=50, seed=0)
        assert ctx.data.per_token("BTC", venue=Venue.BINANCE) is not None


class TestTokensBarRelative:
    """AC-S4 — ctx.data.tokens queries InstrumentRegistry at bar_idx."""

    def test_tokens_at_bar_idx_respects_listing(self):
        from v5.universe_context import UniverseContext
        ctx = UniverseContext.build_test(
            tokens=["BTC", "NEW"], bars=100, seed=0,
            listings={"NEW": 50},
        )
        assert "NEW" not in ctx.data.tokens(bar_idx=30)
        assert "NEW" in ctx.data.tokens(bar_idx=60)

    def test_tokens_excludes_delisted_after_delist_bar(self):
        from v5.universe_context import UniverseContext
        ctx = UniverseContext.build_test(
            tokens=["BTC", "OLD"], bars=100, seed=0,
            delistings={"OLD": 70},
        )
        assert "OLD" in ctx.data.tokens(bar_idx=50)
        assert "OLD" not in ctx.data.tokens(bar_idx=80)

    def test_tokens_requires_cache_availability(self):
        from v5.universe_context import UniverseContext
        ctx = UniverseContext.build_test(
            tokens=["BTC"], bars=100, seed=0,
            registry_extra=["GHOST"],
        )
        assert "GHOST" not in ctx.data.tokens(bar_idx=50), (
            "AC-S4: tradable requires listed AND cached"
        )

    def test_tokens_attribute_shortcut_aliases_current_bar(self):
        from v5.universe_context import UniverseContext
        ctx = UniverseContext.build_test(tokens=["BTC", "ETH"], bars=100, seed=0)
        ctx.seek_bar(idx=50)
        tokens = ctx.data.tokens if not callable(ctx.data.tokens) else ctx.data.tokens(bar_idx=50)
        assert set(tokens) == {"BTC", "ETH"}


class TestFacadeContract:
    """AC-S2 — portfolio / orders facades expose documented surface."""

    def test_portfolio_view_exposes_equity_and_open_positions(self):
        from v5.universe_context import UniverseContext
        ctx = UniverseContext.build_test(tokens=["BTC"], bars=10, seed=0, equity=150_000.0)
        assert ctx.portfolio.equity == pytest.approx(150_000.0)
        assert hasattr(ctx.portfolio, "open_positions")

    def test_orders_view_exposes_arm_and_arm_bracket(self):
        from v5.universe_context import UniverseContext
        ctx = UniverseContext.build_test(tokens=["BTC"], bars=10, seed=0)
        assert callable(getattr(ctx.orders, "arm", None))
        assert callable(getattr(ctx.orders, "arm_bracket", None))

    def test_clock_is_a_clock_protocol_instance(self):
        from v5.clock import Clock
        from v5.universe_context import UniverseContext
        ctx = UniverseContext.build_test(tokens=["BTC"], bars=10, seed=0)
        assert isinstance(ctx.clock, Clock)
