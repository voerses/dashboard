"""M10 AC #19 — ``use_data_engine`` default flipped to ``True`` (closes M7 deferral).

Three regression guards:

1. ``PortfolioConfig().use_data_engine`` is ``True`` by default. The
   old ``_engine_precompute_fallback`` path is no longer the default
   data source for backtests.

2. The legacy ``use_data_engine=False`` branches have been removed
   from ``v5/paper_engine.py``. A grep of the source file returns zero
   hits for ``use_data_engine=False``. M7 shipped 8 such branches;
   M10 deletes them all.

3. Constructing ``PortfolioConfig(use_data_engine=False)`` either
   raises (field removed → TypeError) OR produces a config that triggers
   ``DeprecatedPathError`` the first time the backtest actually consumes
   data. The test accepts EITHER failure mode — Phase 4 picks.

All tests MUST FAIL today — ``PortfolioConfig`` does not currently
expose ``use_data_engine`` as a public field at all, ``paper_engine.py``
still contains ≥8 legacy branches, and no ``DeprecatedPathError``
exists in ``v5``.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


class TestAC19DefaultIsTrue:
    """AC #19.1 — default PortfolioConfig().use_data_engine is True."""

    def test_portfolio_config_default_use_data_engine_is_true(self):
        from v5.config import PortfolioConfig
        cfg = PortfolioConfig()
        assert hasattr(cfg, "use_data_engine"), (
            "PortfolioConfig must expose `use_data_engine` as a public field"
        )
        assert cfg.use_data_engine is True, (
            f"PortfolioConfig.use_data_engine default must be True; "
            f"got {cfg.use_data_engine!r}"
        )


class TestAC19NoLegacyFalseBranches:
    """AC #19.2 — `use_data_engine=False` branches fully removed.

    Brief spec: ``grep v5/paper_engine.py source for use_data_engine=False
    returns zero hits``. Interpreted strictly (per M7 heritage + M10 scope
    audit): ALL ``use_data_engine``-gated branches have been deleted. M7
    shipped ≥8 such branches (verified by the existing
    ``_count_use_data_engine_branches`` helper in ``test_m7_paper_8site.py``).
    M10 deletes them all — the count drops to 0.
    """

    def _count_use_data_engine_branches(self, path: Path) -> int:
        import ast as _ast
        tree = _ast.parse(path.read_text())
        count = 0
        for node in _ast.walk(tree):
            if isinstance(node, _ast.If):
                s = _ast.unparse(node.test) if hasattr(_ast, "unparse") else ""
                if "use_data_engine" in s:
                    count += 1
        return count

    def test_paper_engine_has_no_use_data_engine_false_branches(self):
        """Source-level grep — no legacy `if use_data_engine:` branches,
        no `use_data_engine=False` keyword arguments. All 8 M7 branches
        must be deleted by M10."""
        paper_engine = _project_root / "v5" / "paper_engine.py"
        assert paper_engine.exists(), f"Missing {paper_engine}"
        src = paper_engine.read_text()
        # (a) Literal grep per brief wording: `use_data_engine=False` →
        # zero hits (already the case, enforces that no one re-introduces).
        hits = re.findall(r"use_data_engine\s*=\s*False", src)
        assert len(hits) == 0, (
            f"paper_engine.py still contains {len(hits)} literal "
            f"`use_data_engine=False` sites; M10 must delete all"
        )
        # (b) Substantive deletion: AST-level branch count must drop to 0.
        n = self._count_use_data_engine_branches(paper_engine)
        assert n == 0, (
            f"paper_engine.py still contains {n} `use_data_engine`-gated "
            f"if-branches; M7 shipped ≥8, M10 must delete all"
        )

    def test_run_paper_multi_has_no_use_data_engine_false_branches(self):
        """The legacy 2-branch site in run_paper_multi.py also goes."""
        run_paper = _project_root / "v5" / "run_paper_multi.py"
        assert run_paper.exists(), f"Missing {run_paper}"
        src = run_paper.read_text()
        hits = re.findall(r"use_data_engine\s*=\s*False", src)
        assert len(hits) == 0, (
            f"run_paper_multi.py still contains {len(hits)} literal "
            f"`use_data_engine=False` sites; M10 must delete all"
        )
        n = self._count_use_data_engine_branches(run_paper)
        assert n == 0, (
            f"run_paper_multi.py still contains {n} `use_data_engine`-gated "
            f"if-branches; M10 must delete all"
        )


class TestAC19FalseEitherRaisesOrDeprecated:
    """AC #19.3 — constructing with ``use_data_engine=False`` dies one way or another.

    Per AC #19.1 the field exists with default ``True``. Per M10 brief AC
    #19, using it with ``=False`` must either (a) raise TypeError (field
    removed entirely — the fallback path is deleted outright) OR (b) raise
    :class:`v5.simulator.DeprecatedPathError` when the config is consumed.
    Silent acceptance is the ONE forbidden outcome.
    """

    def test_false_raises_or_triggers_deprecated_path_error(self):
        from v5.config import PortfolioConfig

        # Pre-test state check: after AC #19.1 passes the field exists
        # with default True. Constructing with =False must be explicitly
        # handled — today the field is entirely absent, so neither
        # outcome can be verified without the Phase 4 change landing.
        default_cfg = PortfolioConfig()
        assert hasattr(default_cfg, "use_data_engine"), (
            "AC #19.3 cannot be verified until AC #19.1 lands — "
            "PortfolioConfig does not yet expose `use_data_engine`."
        )

        # Option A: field deleted OR accepts only True — TypeError/ValueError.
        try:
            cfg = PortfolioConfig(use_data_engine=False)
        except (TypeError, ValueError):
            return  # Option A satisfied.

        # Option B: field retained for debug only — using it must raise
        # DeprecatedPathError when the config is consumed by the engine.
        try:
            from v5.simulator import DeprecatedPathError
        except ImportError:
            pytest.fail(
                "PortfolioConfig(use_data_engine=False) did not raise, "
                "and v5.simulator.DeprecatedPathError does not exist. "
                "M10 must either delete the field or gate it behind "
                "DeprecatedPathError."
            )

        # Config constructed; exercising the engine with it must raise.
        from v5.simulator import simulate_portfolio
        with pytest.raises(DeprecatedPathError):
            simulate_portfolio({}, strategy_specs={}, config=cfg)
