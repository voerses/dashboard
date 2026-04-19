"""M6 — paper_engine.py migration feature flag + proxy parity (T-D8 / AC-D12).

Covers:
  - AC-D12 T-D8 flag=OFF: DataEngine.use_data_engine_flag=False routes paper
    engine through legacy PriceMonitor; behavior unchanged vs pre-M6.
  - AC-D12 T-D8 flag=ON proxy: over a 1-hour recorded-WS proxy (Phase 3),
    DataEngine delivers bit-identical bars to handlers vs the legacy path.
    Full 24h shadow replay harness lives in shadow_replay_harness.py.

All tests MUST FAIL today — v5.data.engine does not exist.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


FIXTURE_ROOT = _project_root / "v5" / "tests" / "fixtures" / "shadow_replay_1h_proxy"


class TestFeatureFlagDefault:
    """T-D8 / AC-D12 — default flag is False (legacy path active)."""

    def test_use_data_engine_flag_default_is_false(self):
        from v5.data.engine import DataEngine
        engine = DataEngine()
        assert engine.use_data_engine_flag is False

    def test_flag_flip_is_single_attribute(self):
        from v5.data.engine import DataEngine
        engine = DataEngine()
        engine.use_data_engine_flag = True
        assert engine.use_data_engine_flag is True


class TestFlagOffLegacyParity:
    """T-D8 flag=OFF — paper_engine uses legacy PriceMonitor path."""

    def test_flag_off_legacy_path_still_active(self):
        """paper_engine with flag=False imports-but-does-not-activate DataEngine.
        # NOTE: brief ambiguous at AC-D12; preferred strict interpretation —
        # when flag=False, paper_engine does NOT publish bars through the
        # M6 bus; legacy PriceMonitor handler chain is exclusive.
        """
        from v5.data.engine import DataEngine
        from v5.paper_engine import build_paper_engine_for_test

        m6_engine = DataEngine()
        m6_engine.use_data_engine_flag = False
        paper = build_paper_engine_for_test(data_engine=m6_engine)

        # Legacy path signal: paper engine's active data source is PriceMonitor.
        assert paper.active_source_name() == "PriceMonitor"


class TestFlagOnProxyParity:
    """T-D8 flag=ON proxy — 1h recorded-WS replay parity vs legacy."""

    def _require_fixture(self):
        if not FIXTURE_ROOT.exists():
            pytest.fail(
                f"Expected fixture at {FIXTURE_ROOT} (Wave-F work). "
                "Test is RED today via fixture-missing; when fixture lands "
                "the parity assertion exercises the real M6 path."
            )

    def test_flag_wiring_returns_fixture_bars(self):
        """Flag mechanism wires cleanly: both flag=OFF and flag=ON paths return
        the same parsed fixture bars via the helper's JSONL decode path.

        NOTE: this is a WIRING test, not a real bit-identity test. Real
        bit-identity (v4 PriceMonitor vs M6 DataEngine over the same recorded
        WS tape) requires Task 17's 8-site PaperEngine dispatch + a real 24h
        live WS recording — see brief §"Shadow Replay Ops Runbook" for the
        production hard-merge-gate. That work is deferred past M6 scope.
        """
        self._require_fixture()
        from v5.data.engine import DataEngine
        from v5.paper_engine import build_paper_engine_for_test

        # Run the 1-hour proxy twice — once with flag OFF, once ON.
        def _run(flag_on: bool):
            eng = DataEngine()
            eng.use_data_engine_flag = flag_on
            paper = build_paper_engine_for_test(data_engine=eng)
            return paper.replay_fixture_bars(FIXTURE_ROOT / "1h_proxy.jsonl")

        bars_off = _run(False)
        bars_on = _run(True)

        assert len(bars_off) == len(bars_on), (
            f"bar count diverged off={len(bars_off)} on={len(bars_on)} — AC-D12 zero tolerance"
        )
        for i, (a, b) in enumerate(zip(bars_off, bars_on)):
            assert a == b, f"bar {i} diverged off={a!r} on={b!r}"
