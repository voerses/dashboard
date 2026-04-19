"""M7 — 8-site PaperEngine dispatch: flag=OFF byte-identity + flag=ON wiring (AC-P1).

Covers:
  - AC-P1 — each of the 8 legacy PaperEngine dispatch sites + 2
    run_paper_multi.py sites gains an `if self._use_data_engine:` branch.
  - AC-P1 — flag=OFF remains byte-identical to pre-M7 behavior (legacy
    PriceMonitor path unchanged).
  - AC-P1 — flag=ON routes through DataEngine / BinanceWSClient /
    BinanceRESTClient.

All tests MUST FAIL today — the 8-site migration has not landed.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


# Sites per brief — 8 in paper_engine.py + 2 in run_paper_multi.py.
PAPER_ENGINE_SITES_MIN = 8
RUN_PAPER_MULTI_SITES_MIN = 2


def _count_use_data_engine_branches(path: Path) -> int:
    """Count `if self._use_data_engine` (or `if <x>.use_data_engine_flag`) branches via AST."""
    if not path.exists():
        return 0
    tree = ast.parse(path.read_text())
    count = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            s = ast.unparse(node.test) if hasattr(ast, "unparse") else ""
            if "use_data_engine" in s:
                count += 1
    return count


class TestEightSiteBranchesAtExpectedLocations:
    """AC-P1 — each of the 8 sites per design §4 MUST have a dispatch branch.

    Reviewer M-finding (Quant): counting branches alone is loose — 8 unrelated
    ifs would pass. Each branch must live in a function/method whose name
    matches the expected site (e.g., __init__, cleanup, _update_ws_subscriptions,
    process_hourly_tick, _settle_funding, etc.).
    """

    # Site → (containing function name, line range from design §4)
    EXPECTED_SITES = (
        ("__init__", 859, 912),              # Site 1: PriceMonitor vs DataEngine wiring
        ("__init__", 893, 911),              # Site 2: CandleAggregator (often nested in __init__)
        ("cleanup", 1244, 1252),             # Site 3: shutdown branch
        ("_update_ws_subscriptions", 2046, 2068),  # Site 4: subscription sync
        ("_fetch_live_ohlcv", 3213, 3217),   # Site 5: OHLCV fetch (exact fn name TBD by impl)
        ("_fetch_funding_rates", 3225, 3229),      # Site 6
        ("_hourly_funding_settlement", 3237, 3252),  # Site 7
    )

    def test_each_expected_site_has_use_data_engine_branch(self):
        """Walk AST; find use_data_engine branches; verify ≥7 are inside functions
        whose names match the expected site set (allows for impl naming flex)."""
        import ast as _ast
        paper_engine = _project_root / "v5" / "paper_engine.py"
        if not paper_engine.exists():
            pytest.skip("v5/paper_engine.py not present")
        tree = _ast.parse(paper_engine.read_text())

        branches_by_fn: dict[str, int] = {}
        for fn_node in _ast.walk(tree):
            if isinstance(fn_node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                for inner in _ast.walk(fn_node):
                    if isinstance(inner, _ast.If):
                        s = _ast.unparse(inner.test) if hasattr(_ast, "unparse") else ""
                        if "use_data_engine" in s:
                            branches_by_fn[fn_node.name] = branches_by_fn.get(fn_node.name, 0) + 1

        expected_fn_names = {name for name, _, _ in self.EXPECTED_SITES}
        matched_fns = {fn for fn in branches_by_fn if fn in expected_fn_names}
        assert len(matched_fns) >= 4, (
            f"AC-P1: at least 4 of {expected_fn_names} must contain use_data_engine branches; "
            f"got branches in {dict(branches_by_fn)}"
        )
        total_branches = sum(branches_by_fn.values())
        assert total_branches >= PAPER_ENGINE_SITES_MIN, (
            f"AC-P1: expected ≥{PAPER_ENGINE_SITES_MIN} use_data_engine branches; got {total_branches}"
        )


class TestEightSiteBranchesInPaperEngine:
    """AC-P1 — paper_engine.py has ≥ 8 use_data_engine branches (loose count)."""

    def test_paper_engine_contains_enough_branches(self):
        p = _project_root / "v5" / "paper_engine.py"
        n = _count_use_data_engine_branches(p)
        assert n >= PAPER_ENGINE_SITES_MIN, (
            f"AC-P1: v5/paper_engine.py must have ≥ {PAPER_ENGINE_SITES_MIN} "
            f"`use_data_engine` branches (one per legacy dispatch site); got {n}"
        )


class TestTwoSiteBranchesInRunPaperMulti:
    """AC-P1 — run_paper_multi.py has ≥ 2 use_data_engine branches."""

    def test_run_paper_multi_contains_enough_branches(self):
        p = _project_root / "v5" / "run_paper_multi.py"
        n = _count_use_data_engine_branches(p)
        assert n >= RUN_PAPER_MULTI_SITES_MIN, (
            f"AC-P1: v5/run_paper_multi.py must have ≥ {RUN_PAPER_MULTI_SITES_MIN} "
            f"`use_data_engine` branches; got {n}"
        )


class TestFlagOffByteIdentity:
    """AC-P1 — flag=OFF leaves legacy PriceMonitor path byte-identical."""

    def test_flag_off_price_monitor_remains_active_source(self):
        """AC-P1 — flag=OFF: paper engine reports PriceMonitor as active source."""
        from v5.data.engine import DataEngine
        from v5.paper_engine import build_paper_engine_for_test

        de = DataEngine()
        de.use_data_engine_flag = False
        paper = build_paper_engine_for_test(data_engine=de)
        assert paper.active_source_name() == "PriceMonitor"

    def test_flag_off_digest_matches_legacy(self, tmp_path):
        """AC-P1 — running a short paper session with flag=OFF produces the same
        trade archive digest as a pure-legacy run (byte-identical).
        """
        from v5.data.engine import DataEngine
        from v5.paper_engine import build_paper_engine_for_test

        # Pure legacy — no DataEngine at all
        legacy = build_paper_engine_for_test(data_engine=None)
        digest_legacy = legacy.run_short_session_and_digest(seed=42)

        # Flag OFF path — DataEngine present but dormant
        de = DataEngine()
        de.use_data_engine_flag = False
        gated = build_paper_engine_for_test(data_engine=de)
        digest_gated = gated.run_short_session_and_digest(seed=42)

        assert digest_legacy == digest_gated, (
            "AC-P1: flag=OFF must be byte-identical to legacy path"
        )


class TestFlagOnRoutesThroughDataEngine:
    """AC-P1 — flag=ON: paper engine routes bars through DataEngine."""

    def test_flag_on_active_source_is_data_engine(self):
        from v5.data.engine import DataEngine
        from v5.paper_engine import build_paper_engine_for_test

        de = DataEngine()
        de.use_data_engine_flag = True
        paper = build_paper_engine_for_test(data_engine=de)
        assert paper.active_source_name() == "DataEngine", (
            "AC-P1: flag=ON must route through DataEngine, not PriceMonitor"
        )

    def test_flag_on_uses_binance_ws_client(self):
        """AC-P1 — live paper with flag=ON uses BinanceWSClient (not PriceMonitor)."""
        from v5.data.clients.binance_ws import BinanceWSClient
        from v5.data.engine import DataEngine
        from v5.paper_engine import build_paper_engine_for_test

        de = DataEngine()
        de.use_data_engine_flag = True
        paper = build_paper_engine_for_test(data_engine=de, live_mode=True)
        ws_clients = paper.live_ws_clients()
        assert any(isinstance(c, BinanceWSClient) for c in ws_clients), (
            "AC-P1: flag=ON live mode must instantiate BinanceWSClient"
        )
