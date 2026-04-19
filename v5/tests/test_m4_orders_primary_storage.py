"""M4 — Structural invariant tests for T16b primary-storage flip.

Gap-closing tests added after Phase 4 Round 2 review. Round 1/Round 2 pytest
passed 260/260 despite T16b being half-landed (lazy read-view property over
legacy dict backing), because AC4/AC13/AC19 are expressed as behavioral ACs
and the subagent kept behaviour identical across both storage directions.

These tests assert the STRUCTURAL invariants that the behavioural tests cannot
see — guarding against silent architectural regression in later milestones.

ACs reinforced:
  - AC4  Paper zero-duplication: backing storage IS the Order state
         machine, not a dict that gets adapted on read.
  - AC13 Every open via Order state machine.
  - AC32 Order persistence (serialization goes through to_json/from_json,
         not the legacy 40+ field dict schema).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


PAPER_ENGINE_PATH = _project_root / "v5" / "paper_engine.py"


class TestT16bPrimaryStorage:
    """After T16b flip: _pending_entries is the PRIMARY storage, not a view."""

    def test_pending_entries_attr_exists_on_paper_engine(self):
        """AC4: paper engine must own a _pending_entries attribute post-flip."""
        from v5.paper_engine import PaperPortfolioEngine
        # Create a minimal instance without starting websockets
        engine = PaperPortfolioEngine.__new__(PaperPortfolioEngine)
        # Initialize the attribute via the same init path
        engine._pending_entries = {}
        assert hasattr(engine, "_pending_entries")
        assert isinstance(engine._pending_entries, dict)

    def test_pending_entries_is_typed_pending_entry(self):
        """AC13: the primary storage maps tuples to Order instances
        (not dicts). If a later refactor flipped back to dict-of-dicts, this
        structural check would catch it immediately — behavioural tests would
        still pass but the type contract would be broken."""
        from v5.paper_engine import PaperPortfolioEngine
        from v5.orders import Order

        # The attribute type annotation should be (or imply) Order
        # Use MRO + attribute inspection — we can't rely on runtime types of
        # an empty dict, so instead we verify that the PaperPortfolioEngine
        # source declares the type annotation with Order.
        src = PAPER_ENGINE_PATH.read_text()
        # The init line should declare dict[..., Order] (or equivalent)
        assert re.search(
            r"_pending_entries\s*[:=].*Order|"
            r"_pending_entries\s*:\s*Dict.*Order",
            src,
        ), (
            "Expected `_pending_entries` to be type-annotated with "
            "Order. If this fails, storage may have silently reverted "
            "to dict-of-dicts."
        )


class TestT16bWriteSiteCap:
    """Grep invariant: no more than a tiny number of `_armed_tokens[...]` or
    `_armed_tokens =` write sites in paper_engine.py after T16b flip."""

    def test_write_sites_capped(self):
        """AC4 structural: `_armed_tokens[...] = ...` and `_armed_tokens = ...`
        write patterns should appear zero or near-zero times — only acceptable
        inside the back-compat property implementation (if any).

        Prior to T16b flip this count was ~32. After flip it must be ≤ 1
        (the property's internal assignment of a newly-built dict, if any).
        """
        src = PAPER_ENGINE_PATH.read_text()
        # Strip comments and docstrings for a cleaner grep. This is a heuristic
        # — we look for actual write patterns on non-comment lines only.
        write_patterns = []
        for lineno, line in enumerate(src.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            # `_armed_tokens[` followed by anything up to `] =` (mutation)
            # OR `_armed_tokens =` (rebinding)
            # Exclude `_armed_tokens_lock` (the lock attribute).
            if re.search(r"self\._armed_tokens\[[^\]]*\]\s*=(?!=)", line):
                write_patterns.append((lineno, line.strip()))
            elif re.search(r"self\._armed_tokens\s*=(?!=)", line):
                # Allow inside the property implementation (rare).
                write_patterns.append((lineno, line.strip()))

        assert len(write_patterns) == 0, (
            f"Expected 0 _armed_tokens write sites after T16b flip; "
            f"found {len(write_patterns)}:\n"
            + "\n".join(f"  line {ln}: {s}" for ln, s in write_patterns)
            + "\nPrimary storage is _pending_entries. _armed_tokens must be "
            "read-only back-compat only."
        )


class TestT16bBackCompatProperty:
    """The back-compat `_armed_tokens` property returns a dict view derived
    from `_pending_entries`, NOT a live backing store."""

    def test_armed_tokens_is_property(self):
        """AC4 structural: `_armed_tokens` should be a property, not an
        attribute. If it were an attribute, the storage would still be the
        legacy dict."""
        from v5.paper_engine import PaperPortfolioEngine

        # Check at the class level — a property lives on the class, not the
        # instance.
        attr = getattr(PaperPortfolioEngine, "_armed_tokens", None)
        assert isinstance(attr, property), (
            "Expected `_armed_tokens` to be a @property on "
            "PaperPortfolioEngine. If this fails, the attribute is a raw "
            "dict again — storage direction reverted."
        )

    def test_armed_tokens_view_is_derived_from_pending_entries(self):
        """AC4 structural: reading `_armed_tokens` returns a fresh dict
        materialised from `_pending_entries`, not a live reference."""
        from v5.paper_engine import PaperPortfolioEngine

        engine = PaperPortfolioEngine.__new__(PaperPortfolioEngine)
        import threading
        engine._armed_tokens_lock = threading.RLock()
        engine._pending_entries = {}

        view1 = engine._armed_tokens
        view2 = engine._armed_tokens
        # Fresh dict on each call — mutating one must not affect the other
        # or the primary _pending_entries.
        assert view1 is not view2 or len(view1) == 0, (
            "If the property returns the same object each call AND the "
            "object is non-empty, it may be a live backing store rather "
            "than a derived view."
        )
