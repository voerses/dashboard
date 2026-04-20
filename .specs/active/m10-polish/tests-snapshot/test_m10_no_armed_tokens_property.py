"""M10 B6 — paper-engine `_armed_tokens` back-compat property DELETED (AC #13).

Test enforces:
    NO class exported by `v5.paper_engine` has an `_armed_tokens`
    attribute (data or property / any descriptor).

M4 Task 16b flipped the primary store to `Order` (via
`_pending_entries`). The `_armed_tokens` property (declared at
`v5/paper_engine.py:2127` on `PaperPortfolioEngine`) is a legacy
read-only view kept for back-compat during the migration. M10 deletes
it.

The task spec nominally says "import `v5.paper_engine.PaperEngine`"
but the actual live shim is on `PaperPortfolioEngine` (same module;
the M6 refactor split the class). Per `code over specs` (CLAUDE.md
meta-rule 1), we target every class the module exposes and check the
shim is absent everywhere.

MUST FAIL TODAY — `_armed_tokens` is declared as a `@property` on
`PaperPortfolioEngine` in `v5/paper_engine.py`.
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


def _paper_engine_classes():
    """Yield every class defined in v5.paper_engine (module-level)."""
    import v5.paper_engine as mod
    for name, cls in inspect.getmembers(mod, inspect.isclass):
        # Only classes ORIGINATING in this module (not re-exports).
        if getattr(cls, "__module__", "") == "v5.paper_engine":
            yield name, cls


class TestArmedTokensPropertyDeleted:
    """B6 — `_armed_tokens` attribute absent from every paper_engine class."""

    def test_paper_engine_has_no_armed_tokens(self):
        """`hasattr(PaperEngine, "_armed_tokens") is False` — spec form."""
        from v5.paper_engine import PaperEngine
        assert hasattr(PaperEngine, "_armed_tokens") is False, (
            "PaperEngine._armed_tokens back-compat property must be "
            "DELETED in M10 (AC #13)."
        )

    def test_no_class_has_armed_tokens_attribute(self):
        """`hasattr(cls, "_armed_tokens") is False` for every class in
        `v5.paper_engine` — catches the real `PaperPortfolioEngine`
        property."""
        offenders: list[str] = []
        for name, cls in _paper_engine_classes():
            if hasattr(cls, "_armed_tokens"):
                offenders.append(name)
        assert not offenders, (
            f"_armed_tokens still attached to v5.paper_engine class(es): "
            f"{offenders}. Delete the back-compat property descriptor "
            f"per AC #13. `_pending_entries` is the sole armed-order "
            f"store after T16b flip."
        )

    def test_no_class_declares_property_descriptor(self):
        """No class in `v5.paper_engine.__dict__` keys contains
        `_armed_tokens` as a descriptor (property / field / anything)."""
        offenders: list[tuple[str, type]] = []
        for name, cls in _paper_engine_classes():
            if "_armed_tokens" in cls.__dict__:
                offenders.append((name, type(cls.__dict__["_armed_tokens"])))
        assert not offenders, (
            f"_armed_tokens present in __dict__ of: {offenders}. Delete "
            f"the back-compat descriptor per AC #13."
        )
