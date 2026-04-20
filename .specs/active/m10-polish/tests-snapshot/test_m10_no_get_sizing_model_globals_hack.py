"""M10 B11 — `globals()["get_sizing_model"]` obfuscation DELETED (AC #13).

Test enforces:
    1. `v5/simulator.py` source contains NO `globals()["get_sizing_model"]`
       or the string-split obfuscation `"get_" + "sizing_model"`.
    2. `from v5.sizing import get_sizing_model` succeeds (real
       module-level import, not a dynamic-attribute hack).

Today the simulator at lines 35-41 does:

    import v5.sizing_legacy as _legacy_sizing
    _get_legacy_sizing = _legacy_sizing._legacy_get_sizing_model
    globals()["get_" + "sizing_model"] = _get_legacy_sizing

to dodge AC-Sz6 grep bans while still exposing `get_sizing_model` for
M7 conviction-test monkeypatching. M10 deletes the hack: `get_sizing_model`
must become a REAL module-level import from `v5.sizing`.

MUST FAIL TODAY —
  * grep hits both obfuscations in `v5/simulator.py`.
  * `from v5.sizing import get_sizing_model` is NOT exported from
    `v5/sizing/__init__.py` today.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


_SIMULATOR_SRC = (
    Path(__file__).resolve().parent.parent / "simulator.py"
)


class TestGetSizingModelGlobalsHackDeleted:
    """B11 — the simulator's obfuscated resolver must be a real import."""

    def test_simulator_source_has_no_globals_getitem_hack(self):
        """`globals()["get_sizing_model"]` absent from simulator.py."""
        src = _SIMULATOR_SRC.read_text()
        assert 'globals()["get_sizing_model"]' not in src, (
            "v5/simulator.py still uses globals()[\"get_sizing_model\"] "
            "obfuscation — delete per AC #13."
        )

    def test_simulator_source_has_no_string_split_obfuscation(self):
        """The `"get_" + "sizing_model"` concatenation trick is absent."""
        src = _SIMULATOR_SRC.read_text()
        assert '"get_" + "sizing_model"' not in src, (
            "v5/simulator.py still uses `\"get_\" + \"sizing_model\"` "
            "string-split obfuscation — delete per AC #13. The "
            "resolver must use a plain module-level import."
        )
        # Also reject the single-quoted variant.
        assert "'get_' + 'sizing_model'" not in src, (
            "v5/simulator.py still uses `'get_' + 'sizing_model'` "
            "single-quoted obfuscation — delete per AC #13."
        )

    def test_simulator_source_has_no_globals_get_fallback(self):
        """`globals().get("get_sizing_model", ...)` resolver absent."""
        src = _SIMULATOR_SRC.read_text()
        assert 'globals().get("get_sizing_model"' not in src, (
            "v5/simulator.py still falls back to globals().get() for "
            "get_sizing_model — delete per AC #13."
        )
        assert 'globals().get("get_" + "sizing_model"' not in src, (
            "v5/simulator.py still falls back to globals().get() with "
            "the string-split obfuscation — delete per AC #13."
        )

    def test_real_import_from_v5_sizing_succeeds(self):
        """`from v5.sizing import get_sizing_model` must work after M10."""
        # Clear any cached import so the check is real.
        for mod_name in list(sys.modules):
            if mod_name == "v5.sizing" or mod_name.startswith("v5.sizing."):
                # Keep — re-importing will use the cache; that's fine. We
                # just need the symbol lookup to succeed.
                pass
        try:
            mod = importlib.import_module("v5.sizing")
        except Exception as e:  # pragma: no cover
            pytest.fail(f"Import v5.sizing raised: {e!r}")
        assert hasattr(mod, "get_sizing_model"), (
            "v5.sizing must export `get_sizing_model` as a real "
            "module-level name (AC #13). Currently absent from "
            "v5/sizing/__init__.py."
        )

    def test_simulator_imports_get_sizing_model_directly(self):
        """Source check: simulator imports `get_sizing_model` via a
        normal `from ... import` statement (no dynamic-attribute dodge)."""
        src = _SIMULATOR_SRC.read_text()
        # Either from-import style must be present AFTER Phase 4.
        patterns = (
            "from v5.sizing import get_sizing_model",
            "from .sizing import get_sizing_model",
            "from v5.sizing import",  # allow multi-import line
        )
        has_real_import = any(
            p in src and ("get_sizing_model" in src.split(p, 1)[1].split("\n", 1)[0]
                          if p.endswith("import") else True)
            for p in patterns[:2]
        ) or ("from v5.sizing import" in src and "get_sizing_model" in src)
        # Narrower: require the literal string `get_sizing_model` to
        # appear on the same line as an import statement.
        import re
        m = re.search(
            r"^\s*from\s+(?:v5\.sizing|\.sizing)\s+import\s+[^\n]*\bget_sizing_model\b",
            src, re.MULTILINE,
        )
        assert m is not None, (
            "v5/simulator.py must `from v5.sizing import get_sizing_model` "
            "directly (AC #13). No import statement matching that pattern "
            "was found."
        )
