"""M9 C-10 — scale_check_fn function hook removal + canonical Strategy.check_scale.

Covers AC #15:
- StrategySpec.scale_check_fn function hook is REMOVED (not just deprecated)
- Canonical Strategy.check_scale(pos, bar_ctx) — 2-arg signature, ctx via bar_ctx.ctx
- Simultaneous registration of both hooks raises ValueError at config-construction time

All tests MUST FAIL today — scale_check_fn field removal not yet landed;
dual-hook ValueError guard not yet implemented.
"""
from __future__ import annotations

import dataclasses
import inspect
import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


class TestScaleCheckFnRemoved:
    """AC #15 — StrategySpec.scale_check_fn removed from dataclass fields."""

    def test_strategy_spec_has_no_scale_check_fn_field(self):
        from v5.config import StrategySpec

        field_names = {f.name for f in dataclasses.fields(StrategySpec)}
        assert "scale_check_fn" not in field_names, (
            "StrategySpec.scale_check_fn must be REMOVED in M9 "
            "(canonical hook is Strategy.check_scale Protocol method)"
        )

    def test_passing_scale_check_fn_raises(self):
        """Constructing StrategySpec with scale_check_fn= kw raises TypeError."""
        from v5.config import StrategySpec

        def _some_fn(pos, bar_ctx): return None

        with pytest.raises(TypeError):
            StrategySpec(strategy_id="s1", scale_check_fn=_some_fn)  # type: ignore[call-arg]


class TestCheckScaleProtocolMethodCanonical:
    """AC #15 — Strategy.check_scale(pos, bar_ctx) is the canonical hook."""

    def test_strategy_protocol_check_scale_is_two_arg(self):
        from v5.strategy_api import Strategy

        sig = inspect.signature(Strategy.check_scale)
        params = list(sig.parameters.values())
        # self + pos + bar_ctx = 3 params
        non_self_params = [p for p in params if p.name != "self"]
        assert len(non_self_params) == 2, (
            f"Strategy.check_scale must be (self, pos, bar_ctx); got params "
            f"{[p.name for p in non_self_params]}"
        )
        assert non_self_params[0].name == "pos"
        assert non_self_params[1].name == "bar_ctx"

    def test_ctx_accessed_via_bar_ctx_ctx_not_third_arg(self):
        """bar_ctx.ctx: UniverseContext present — no third arg required."""
        from v5.strategy_api import BarContext

        field_names = {f.name for f in dataclasses.fields(BarContext)}
        assert "ctx" in field_names, (
            "BarContext.ctx: UniverseContext must be present (C-1 enrichment)"
        )


class TestBothHooksSimultaneouslyRaises:
    """AC #15 — configuring both scale_check_fn AND Protocol method raises ValueError.

    (During the M9 deprecation window where fn-hook scaffolding may still exist
    in fixtures — dual-wiring is an error, not silent precedence.)
    """

    def test_both_hooks_registered_raises_value_error(self):
        from v5.config import PortfolioConfig, StrategySpec
        from v5.strategy_api import Strategy

        class _StratWithCheckScale(Strategy):
            def on_start(self, ctx): pass
            def on_stop(self): pass
            def generate(self, ctx, bar_idx): return []
            def check_exit(self, pos, bar_ctx): return None
            def check_scale(self, pos, bar_ctx): return None

        # User error: registers Strategy with check_scale AND tries to wire a fn hook
        with pytest.raises(ValueError, match="both|simultaneous|duplicate"):
            PortfolioConfig(
                strategies=[
                    StrategySpec(
                        strategy_id="s1",
                        strategy=_StratWithCheckScale(),
                        legacy_scale_check_fn=lambda pos, bar_ctx: None,  # type: ignore[call-arg]
                    )
                ],
                capital=10_000.0,
            )
