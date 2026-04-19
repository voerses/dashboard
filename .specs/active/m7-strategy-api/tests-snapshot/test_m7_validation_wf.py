"""M7 — Walk-forward extracted; fresh Strategy per fold; no masks (AC-V1).

All tests MUST FAIL today — the new WalkForwardRunner shape doesn't exist.
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


class TestWalkForwardRunnerImport:
    """AC-V1 — v5.validation.WalkForwardRunner is the new entry point."""

    def test_walk_forward_runner_importable(self):
        from v5.validation import WalkForwardRunner  # noqa: F401


class TestFreshStrategyPerFold:
    """AC-V1 — each fold receives a FRESH Strategy instance by default."""

    def test_fresh_instance_per_fold(self):
        from v5.validation import WalkForwardRunner

        instances: list[int] = []

        class _CountingStrategy:
            def __init__(self):
                instances.append(id(self))

            def on_start(self, portfolio_config): pass
            def on_stop(self, reason): pass
            def on_reset(self): pass
            def required_data(self): return []
            def generate(self, ctx, bar_idx): return None

        runner = WalkForwardRunner(
            strategy_factory=_CountingStrategy,
            n_folds=3, train_bars=100, oos_bars=20,
        )
        runner.run(tokens=["BTC"], seed=42)
        assert len(instances) == 3
        assert len(set(instances)) == 3


class TestOnResetCalledBetweenFoldsWhenReused:
    """AC-V1 — reused instance receives on_reset() between folds."""

    def test_on_reset_called_between_folds(self):
        from v5.validation import WalkForwardRunner

        resets: list[int] = []

        class _ReusedStrategy:
            def __init__(self):
                self._fold_idx = 0

            def on_start(self, portfolio_config): pass
            def on_stop(self, reason): pass
            def on_reset(self):
                resets.append(self._fold_idx)
                self._fold_idx += 1

            def required_data(self): return []
            def generate(self, ctx, bar_idx): return None

        instance = _ReusedStrategy()
        runner = WalkForwardRunner(
            strategy_factory=lambda: instance,
            n_folds=3, train_bars=100, oos_bars=20, reuse_instance=True,
        )
        runner.run(tokens=["BTC"], seed=42)
        # 2 resets for 3 folds (between folds only)
        assert len(resets) == 2


class TestStrategyNeverSeesMasks:
    """AC-V1 — Strategy.generate's ctx never exposes WF mask arrays."""

    def test_ctx_has_no_mask_attribute(self):
        from v5.validation import WalkForwardRunner

        leaked_attrs: list[str] = []

        class _Peek:
            def on_start(self, portfolio_config): pass
            def on_stop(self, reason): pass
            def on_reset(self): pass
            def required_data(self): return []

            def generate(self, ctx, bar_idx):
                for n in ("wf_mask", "train_mask", "oos_mask", "fold_mask"):
                    if hasattr(ctx, n) or hasattr(getattr(ctx, "data", object()), n):
                        leaked_attrs.append(n)
                return None

        runner = WalkForwardRunner(
            strategy_factory=_Peek, n_folds=1, train_bars=50, oos_bars=20,
        )
        runner.run(tokens=["BTC"], seed=0)
        assert leaked_attrs == []


class TestModuleLevelMutableStateBan:
    """AC-V1 — AST scan rejects `global` + top-level mutable containers."""

    def _write_fake_strategy(self, tmp_path: Path, body: str) -> Path:
        p = tmp_path / "s_bad.py"
        p.write_text(textwrap.dedent(body))
        return p

    def test_global_keyword_in_strategy_rejected(self, tmp_path):
        from v5.strategy_loader import StrategyLoader, StrategyLoadError
        p = self._write_fake_strategy(tmp_path, """
            _COUNTER = 0
            def generate(ctx, bar_idx):
                global _COUNTER
                _COUNTER += 1
                return None
        """)
        with pytest.raises(StrategyLoadError, match="global"):
            StrategyLoader().load(p)

    def test_top_level_dict_assignment_rejected(self, tmp_path):
        from v5.strategy_loader import StrategyLoader, StrategyLoadError
        p = self._write_fake_strategy(tmp_path, """
            _STATE = {}
            def generate(ctx, bar_idx):
                return None
        """)
        with pytest.raises(StrategyLoadError, match="(dict|mutable|top-level)"):
            StrategyLoader().load(p)

    def test_top_level_list_assignment_rejected(self, tmp_path):
        from v5.strategy_loader import StrategyLoader, StrategyLoadError
        p = self._write_fake_strategy(tmp_path, """
            _HISTORY = []
            def generate(ctx, bar_idx):
                return None
        """)
        with pytest.raises(StrategyLoadError, match="(list|mutable|top-level)"):
            StrategyLoader().load(p)

    def test_top_level_set_assignment_rejected(self, tmp_path):
        from v5.strategy_loader import StrategyLoader, StrategyLoadError
        p = self._write_fake_strategy(tmp_path, """
            _SEEN = set()
            def generate(ctx, bar_idx):
                return None
        """)
        with pytest.raises(StrategyLoadError, match="(set|mutable|top-level)"):
            StrategyLoader().load(p)

    def test_scalar_constants_at_top_level_allowed(self, tmp_path):
        from v5.strategy_loader import StrategyLoader
        p = self._write_fake_strategy(tmp_path, """
            _LOOKBACK = 20
            _NAME = "s_test"
            _TUPLE = (1, 2, 3)
            def generate(ctx, bar_idx):
                return None
        """)
        StrategyLoader().load(p)
