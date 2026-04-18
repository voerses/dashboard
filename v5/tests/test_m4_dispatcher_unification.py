"""M4 — Dispatcher unification (AC1/AC4/AC5/AC6).

Single BarProcessor entry-point across backtest and paper paths. All tests
MUST FAIL RED today — v5.bar_processor does not exist.
"""
from __future__ import annotations

import io
import re
import sys
import tokenize
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

V5_DIR = _project_root / "v5"
SIMULATOR_PATH = V5_DIR / "simulator.py"
PAPER_ENGINE_PATH = V5_DIR / "paper_engine.py"


def _strip_comments_and_docstrings(src: str) -> str:
    """Strip comments; blank string-literal content (keeps AC1 grep honest)."""
    out: list[tokenize.TokenInfo] = []
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.COMMENT:
            continue
        if tok.type == tokenize.STRING:
            out.append(tokenize.TokenInfo(tok.type, '""', tok.start, tok.end, tok.line))
            continue
        out.append(tok)
    return tokenize.untokenize(out)


def _non_delegation_hits(path: Path, pattern: re.Pattern[str]) -> list[str]:
    """Pattern hits NOT on a `bar_processor.process_bar(...)` delegation line."""
    cleaned = _strip_comments_and_docstrings(path.read_text(encoding="utf-8"))
    hits: list[str] = []
    for lineno, line in enumerate(cleaned.splitlines(), start=1):
        if not pattern.search(line):
            continue
        if "bar_processor.process_bar" in line:
            continue
        hits.append(f"{path.name}:{lineno}: {line.strip()}")
    return hits


# ---------------------------------------------------------------------------
# AC1 — grep verification
# ---------------------------------------------------------------------------


class TestAC1GrepVerification:
    """AC1: Stage 1/2/3 names absent from simulator/paper_engine outside delegation."""

    @pytest.mark.parametrize("path,token", [
        (SIMULATOR_PATH, r"\bcheck_exit\("),
        (SIMULATOR_PATH, r"\bcheck_scale\("),
        (SIMULATOR_PATH, r"\bStopLossHandler\("),
        (PAPER_ENGINE_PATH, r"\bcheck_exit\("),
        (PAPER_ENGINE_PATH, r"\bcheck_scale\("),
        (PAPER_ENGINE_PATH, r"\bStopLossHandler\("),
    ])
    def test_no_direct_call(self, path, token):
        """AC1: `{token}` must not occur in {path.name} outside delegation."""
        from v5.bar_processor import BarProcessor  # noqa: F401 — RED gate
        hits = _non_delegation_hits(path, re.compile(token))
        assert hits == [], f"AC1 violation:\n" + "\n".join(hits)

    def test_simulator_references_bar_processor(self):
        """AC1: simulator.py imports/references BarProcessor (delegation target)."""
        from v5.bar_processor import BarProcessor  # noqa: F401 — RED gate
        src = SIMULATOR_PATH.read_text(encoding="utf-8")
        assert "bar_processor" in src or "BarProcessor" in src

    def test_paper_engine_references_bar_processor(self):
        """AC4: paper_engine.py imports/references BarProcessor."""
        from v5.bar_processor import BarProcessor  # noqa: F401 — RED gate
        src = PAPER_ENGINE_PATH.read_text(encoding="utf-8")
        assert "bar_processor" in src or "BarProcessor" in src


# ---------------------------------------------------------------------------
# AC4 — Paper engine zero-duplication
# ---------------------------------------------------------------------------


class TestAC4PaperZeroDuplication:
    """AC4: paper tick path builds BarContext and calls process_bar."""

    def test_paper_tick_calls_process_bar_with_bar_context(self):
        """AC4: simulated tick routes through BarProcessor.process_bar(BarContext)."""
        from v5.bar_processor import BarContext, BarProcessor
        from v5.bar_spec import BarSpec
        from v5.paper_engine import PaperEngine

        captured: list = []

        class _Recorder(BarProcessor):
            def process_bar(self, pos=None, bar_ctx=None, global_bar=0,
                            positions=None, pending_entries=None, **kw):
                captured.append(bar_ctx)
                return None

        engine = PaperEngine(bar_processor=_Recorder())
        engine.on_tick({"token": "BTC", "price": 100.0,
                        "ts_ns": 1_700_000_000_000_000_000, "volume": 1.0})

        assert len(captured) >= 1, "AC4: tick did not reach BarProcessor.process_bar"
        for ctx in captured:
            assert isinstance(ctx, BarContext)
            assert isinstance(ctx.bar_spec, BarSpec)

    def test_paper_engine_does_not_build_exit_handler_list_inline(self):
        """AC4: paper_engine.py does not assemble its own handler chain."""
        from v5.bar_processor import EXIT_HANDLER_REGISTRY  # noqa: F401 — RED gate
        cleaned = _strip_comments_and_docstrings(
            PAPER_ENGINE_PATH.read_text(encoding="utf-8"),
        )
        forbidden = re.compile(r"\[\s*StopLossHandler|\(\s*StopLossHandler")
        assert not forbidden.search(cleaned)


# ---------------------------------------------------------------------------
# AC5 — Registry-based dispatch
# ---------------------------------------------------------------------------


class TestAC5RegistryDispatch:
    """AC5: handlers dispatched via a registry sequence, not if/elif."""

    def test_registry_is_sequence_of_nine(self):
        """AC5: exit_handlers registry is iterable with exactly 9 entries."""
        from v5.bar_processor import EXIT_HANDLER_REGISTRY
        assert hasattr(EXIT_HANDLER_REGISTRY, "__iter__")
        assert hasattr(EXIT_HANDLER_REGISTRY, "__len__")
        assert len(EXIT_HANDLER_REGISTRY) == 9

    def test_registry_entries_are_classes_or_factories(self):
        """AC5: every registry entry is callable."""
        from v5.bar_processor import EXIT_HANDLER_REGISTRY
        for entry in EXIT_HANDLER_REGISTRY:
            assert callable(entry)

    def test_process_bar_source_has_no_ifelif_handler_chain(self):
        """AC5: bar_processor source has no hard-coded if/elif handler dispatch."""
        from v5 import bar_processor as bp_mod
        cleaned = _strip_comments_and_docstrings(
            Path(bp_mod.__file__).read_text(encoding="utf-8"),
        )
        bad = re.compile(
            r"if\s+isinstance\([^)]*StopLossHandler\b.*\n\s*elif\s+isinstance\(",
            re.MULTILINE,
        )
        assert not bad.search(cleaned)


# ---------------------------------------------------------------------------
# AC6 — Chain order (first-match-wins)
# ---------------------------------------------------------------------------


EXPECTED_REGISTRY_ORDER = (
    "CustomExitHandler",
    "CircuitBreakerHandler",
    "StopLossHandler",
    "TakeProfitHandler",
    "RSIExitHandler",
    "MeanTargetHandler",
    "SMATrailExitHandler",
    "MaxHoldHandler",
    "FundingCeilingHandler",
)


class TestAC6ChainOrder:
    """AC6: registry order is exactly as documented; earlier wins on conflict."""

    def test_registry_class_names_match_expected_order(self):
        """AC6: handler class names match the documented 9-element ordering."""
        from v5.bar_processor import EXIT_HANDLER_REGISTRY
        names = tuple(
            cls.__name__ if isinstance(cls, type) else type(cls).__name__
            for cls in EXIT_HANDLER_REGISTRY
        )
        assert names == EXPECTED_REGISTRY_ORDER

    def test_first_match_wins_behavioral(self):
        """AC6: when two handlers match, the earlier-registered one wins."""
        from v5.bar_processor import BarProcessor

        early = MagicMock(name="EarlyHandler")
        early.return_value.check.return_value = MagicMock(
            should_close=True, exit_reason="early_win", exit_price=100.0,
        )
        late = MagicMock(name="LateHandler")
        late.return_value.check.return_value = MagicMock(
            should_close=True, exit_reason="late_loss", exit_price=100.0,
        )

        bp = BarProcessor(exit_handler_registry=[early, late])
        pos = MagicMock(token="BTC", direction=1, stop_price=0.0)
        pos.exit_handlers = [early.return_value, late.return_value]
        bar_ctx = MagicMock(
            ts_ns=1_700_000_000_000_000_000, hourly_bar_index=0,
            close=100.0, high=101.0, low=99.0,
        )
        result = bp.process_bar(pos=pos, bar_ctx=bar_ctx, global_bar=0)
        assert getattr(result, "exit_reason", None) == "early_win"
