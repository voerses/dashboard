"""M7 — Unified signals.py; portfolio_signals.py deleted; no strategy_type branch (AC-S6).

All tests MUST FAIL today — portfolio_signals.py still exists.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


class TestPortfolioSignalsDeleted:
    """AC-S6 — portfolio_signals.py must be removed from the tree."""

    def test_portfolio_signals_module_deleted_on_disk(self):
        ps_path = _project_root / "v5" / "portfolio_signals.py"
        assert not ps_path.exists(), (
            f"AC-S6: v5/portfolio_signals.py must be DELETED; still at {ps_path}"
        )

    def test_portfolio_signals_import_raises_module_not_found(self):
        with pytest.raises(ModuleNotFoundError):
            import v5.portfolio_signals  # noqa: F401


class TestUnifiedTokenSignal:
    """AC-S6 — single unified TokenSignal dataclass."""

    def test_token_signal_importable_from_v5_signals(self):
        from v5.signals import TokenSignal  # noqa: F401

    def test_universe_signals_importable(self):
        from v5.signals import UniverseSignals  # noqa: F401

    def test_old_token_signals_plural_removed(self):
        import v5.signals as sig
        assert not hasattr(sig, "TokenSignals"), (
            "AC-S6: legacy TokenSignals (plural) must be removed"
        )

    def test_old_portfolio_signals_class_removed_from_signals(self):
        import v5.signals as sig
        assert not hasattr(sig, "PortfolioSignals")

    def test_token_signal_has_priority_field(self):
        from v5.signals import TokenSignal
        ann = getattr(TokenSignal, "__annotations__", {}) or {}
        assert "priority" in ann, (
            "AC-S6: TokenSignal must declare 'priority' field (conviction→priority split)"
        )

    def test_token_signal_has_sizing_field(self):
        from v5.signals import TokenSignal
        ann = getattr(TokenSignal, "__annotations__", {}) or {}
        assert "sizing" in ann


class TestNoStrategyTypeBranch:
    """AC-S6 — engine/signals dispatch must not branch on strategy_type."""

    def _offenders(self, rel_path: str) -> list[str]:
        p = _project_root / rel_path
        if not p.exists():
            pytest.fail(f"expected source module at {p}")
        tree = ast.parse(p.read_text())
        offenders = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Compare):
                left_str = ast.unparse(node.left) if hasattr(ast, "unparse") else ""
                if "strategy_type" in left_str:
                    offenders.append(ast.unparse(node))
        return offenders

    def test_signals_source_has_no_strategy_type_equality(self):
        assert self._offenders("v5/signals.py") == []

    def test_engine_source_has_no_strategy_type_equality(self):
        assert self._offenders("v5/engine.py") == []


class TestSizingRequestStub:
    """AC-S6 / SizingRequest stub — intent / fraction_of_equity / leverage."""

    def test_sizing_request_importable(self):
        from v5.signals import SizingRequest  # noqa: F401

    @pytest.mark.parametrize("field", ["intent", "fraction_of_equity", "leverage"])
    def test_sizing_request_has_field(self, field):
        from v5.signals import SizingRequest
        ann = getattr(SizingRequest, "__annotations__", {}) or {}
        assert field in ann, f"SizingRequest must declare {field!r}"

    def test_sizing_request_leverage_defaults_to_1(self):
        from v5.signals import SizingRequest
        req = SizingRequest(intent="FIXED_FRACTION", fraction_of_equity=0.02)
        assert req.leverage == 1.0


class TestUnifiedDispatch:
    """AC-S6 — engine dispatches all strategies through the same signal path."""

    def test_generate_returns_universe_signals(self):
        from v5.signals import UniverseSignals
        from v5.strategy_api import BaseStrategy

        class _PerTokenStyle(BaseStrategy):
            def generate(self, ctx, bar_idx):
                return UniverseSignals(signals={})

        class _PortfolioStyle(BaseStrategy):
            def generate(self, ctx, bar_idx):
                return UniverseSignals(signals={})

        assert isinstance(_PerTokenStyle().generate(None, 0), UniverseSignals)
        assert isinstance(_PortfolioStyle().generate(None, 0), UniverseSignals)
