"""M10 B10 — `WalkForwardRunner` dispatch-table lock (Q4, AC #13).

Locks the 4-row canonical dispatch contract BEFORE the `__new__`
kwarg-routing shim at `v5/validation.py:1285-1298` is deleted in Phase 4:

    Row 1 (M8 shape):  strategy + data_bundle + seed  → `_WalkForwardResult`
    Row 2 (M9 shape):  config=ValidationConfig(...)   → M9 result
    Row 3 (ambiguous): config + strategy              → TypeError (mutex)
    Row 4 (invalid):   no args                        → TypeError

MUST FAIL TODAY — Row 3 today raises TypeError only as accident
(`unexpected kwarg 'config'`); Row 4 returns a runner today.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


def _stub_strategy():
    class _S:
        def on_start(self, portfolio_config=None): pass
        def on_stop(self, reason=""): pass
        def generate(self, ctx, bar_idx): return []
    return _S()


def _simple_returns():
    return np.random.default_rng(123).normal(0.0005, 0.01, 3000)


def _assert_is_m8_result(result) -> None:
    assert hasattr(result, "metrics"), (
        f"M8 result must have `.metrics`; got {type(result).__name__}"
    )
    assert isinstance(result.metrics, dict)
    for key in ("total_return", "sharpe", "sortino", "calmar", "max_drawdown"):
        assert key in result.metrics, f"M8 result.metrics missing {key!r}"


def _assert_is_m9_result(result) -> None:
    for attr in ("per_fold_metrics", "per_path_metrics"):
        assert hasattr(result, attr), (
            f"M9 result must have `.{attr}`; got {type(result).__name__}"
        )
    assert isinstance(result.per_fold_metrics, list)
    assert isinstance(result.per_path_metrics, list)


class TestWalkForwardRunnerDispatchTable:
    """B10 — 4-row dispatch lock. LOCKS semantics at top of Phase 4."""

    def test_row1_m8_shape_returns_m8_result(self):
        """Row 1: `(strategy=, data_bundle=, seed=)` → M8 result."""
        from v5.validation import WalkForwardRunner
        runner = WalkForwardRunner(
            strategy=_stub_strategy(), data_bundle={}, seed=42,
        )
        assert runner is not None
        _assert_is_m8_result(runner.run())

    def test_row2_m9_shape_returns_m9_result(self):
        """Row 2: `(config=ValidationConfig(...))` → M9 result."""
        from v5.validation import ValidationConfig, WalkForwardRunner
        cfg = ValidationConfig(train_bars=500, recal_bars=100, cpcv=None)
        runner = WalkForwardRunner(config=cfg)
        assert runner is not None
        _assert_is_m9_result(runner.run(returns=_simple_returns()))

    def test_row3_ambiguous_config_plus_strategy_raises_typeerror(self):
        """Row 3: both `config=` AND `strategy=` → TypeError w/ mutex msg.

        After `__new__` is deleted, the canonical `__init__` must
        explicitly reject the combination.

        Audit-tightened 2026-04-20: require mutual-exclusion keywords
        unconditionally. Today's accidental "unexpected kwarg 'config'"
        message contains the word 'config' and could be satisfied by a
        disjunction — that would mask the wrong failure mode. The
        Phase-4 message MUST include one of {"mutual", "exclus",
        "ambig"} explicitly.
        """
        from v5.validation import ValidationConfig, WalkForwardRunner
        cfg = ValidationConfig(train_bars=500, recal_bars=100)
        with pytest.raises(TypeError) as excinfo:
            WalkForwardRunner(config=cfg, strategy=_stub_strategy())
        msg = str(excinfo.value).lower()
        mutex_words = any(kw in msg for kw in ("mutual", "exclus", "ambig"))
        assert mutex_words, (
            f"Row 3 TypeError must flag mutual exclusion explicitly "
            f"(mention 'mutual'/'exclus'/'ambig'). Got: {excinfo.value!r}"
        )

    def test_row4_no_args_raises_typeerror(self):
        """Row 4: `WalkForwardRunner()` with no kwargs → TypeError."""
        from v5.validation import WalkForwardRunner
        with pytest.raises(TypeError):
            WalkForwardRunner()
