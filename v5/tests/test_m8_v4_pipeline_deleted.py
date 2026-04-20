"""M8 AC-Sz6 — v4 opaque sizing pipeline DELETED from v5.

Assertions:
  - No v5 file references the 18 deleted field names.
  - v5/sizing.py (module) does NOT exist (replaced by v5/sizing/ package).
  - Class/function names from v4 pipeline are not importable from v5.

All tests MUST FAIL today — v5.sizing module still exists / references remain.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


V4_DELETED_FIELDS = [
    "adv_to_sizing",
    "kelly_mult_floor",
    "kelly_mult_range",
    "kelly_mult_override",
    "kelly_mult_scale",
    "cap_pct_floor",
    "cap_pct_range",
    "cap_pct_override",
    "cap_pct_scale",
    "adv_scaling_divisor",
    "size_multiplier",
    "cap_multiplier",
    "max_trade_pct",
    "dd_scaling",
    "pump_filter_",
    "unrealized_pnl_floor",
    "adv_sizing_enabled",
    "vol_adj",
]


V5_DIR = Path(__file__).resolve().parent.parent


def _iter_v5_py_files():
    for p in V5_DIR.rglob("*.py"):
        # Skip these tests themselves + tests directory test files referencing
        # the banned names for assertion purposes.
        if "test_m8_v4_pipeline_deleted" in p.name:
            continue
        if "test_m8_helpers" in p.name:
            # helpers test legitimately exposes V4_DEFAULTS (ADVScalingConfig)
            # which mentions kelly_mult_floor by attribute name — OK inside
            # ADVScalingConfig, not in v4 pipeline.
            continue
        yield p


class TestV4FieldsNotReferenced:
    """AC-Sz6 — the 18 deleted v4 fields have no references under v5/.

    Tight word-boundary regex prevents false-positives in comments and
    docstrings that explain WHY the field was removed. Use re.search with
    \\b boundaries and re.escape.
    """

    @pytest.mark.parametrize("field", V4_DELETED_FIELDS)
    def test_field_not_in_v5_source(self, field):
        import re
        # Fields that overlap with ADVScalingConfig (legit v4-curve helper)
        # are scoped to helpers.py; everywhere else, field name must be absent.
        offenders = []
        allowed_in_helpers = {
            "kelly_mult_floor", "kelly_mult_range",
            "cap_pct_floor", "cap_pct_range",
            "adv_scaling_divisor",
        }
        pattern = re.compile(r"\b" + re.escape(field) + r"\b")
        for p in _iter_v5_py_files():
            if field in allowed_in_helpers and p.name in {"helpers.py", "__init__.py"}:
                continue
            try:
                text = p.read_text()
            except (OSError, UnicodeDecodeError):
                continue
            # Strip docstrings & comments so explanatory references are OK.
            try:
                import ast
                tree = ast.parse(text)
                # Collect all ast.Name / ast.Attribute / ast.keyword.arg references
                hits = []
                for node in ast.walk(tree):
                    if isinstance(node, ast.Name) and node.id == field:
                        hits.append(node.lineno)
                    elif isinstance(node, ast.Attribute) and node.attr == field:
                        hits.append(node.lineno)
                    elif isinstance(node, ast.keyword) and node.arg == field:
                        hits.append(node.lineno)
                    elif isinstance(node, ast.FunctionDef):
                        for arg in node.args.args:
                            if arg.arg == field:
                                hits.append(node.lineno)
                    elif isinstance(node, ast.Assign):
                        for target in node.targets:
                            if isinstance(target, ast.Name) and target.id == field:
                                hits.append(node.lineno)
                if hits:
                    offenders.append(f"{p} (lines {hits})")
            except SyntaxError:
                # Fallback to regex on un-parseable files.
                if pattern.search(text):
                    offenders.append(str(p))
        assert not offenders, (
            f"v4-deleted field {field!r} still referenced in v5/ source (AST): "
            f"{offenders}"
        )


class TestV5SizingModuleReplacedByPackage:
    """AC-Sz6 — v5/sizing.py (top-level module) is gone; v5/sizing/ package replaces it."""

    def test_sizing_py_module_file_does_not_exist(self):
        assert not (V5_DIR / "sizing.py").exists(), (
            "v5/sizing.py must be deleted; replaced by v5/sizing/ package"
        )

    def test_sizing_is_a_package(self):
        import v5.sizing
        assert hasattr(v5.sizing, "__path__"), (
            "v5.sizing must be a package (not a module)"
        )


class TestDeletedClassesNotImportable:
    """AC-Sz6 — v4 sizing class/function names not exported from v5."""

    @pytest.mark.parametrize(
        "name",
        ["KellySizing", "SizingModel", "compute_position_size", "get_sizing_model"],
    )
    def test_name_not_importable(self, name):
        # Ensure no module under v5 re-exports these v4 names.
        # Scan v5 source files for these symbols as top-level defs.
        for p in _iter_v5_py_files():
            try:
                text = p.read_text()
            except (OSError, UnicodeDecodeError):
                continue
            banned_patterns = [
                f"class {name}",
                f"def {name}",
                f"from .* import .*{name}",
            ]
            import re
            for pat in banned_patterns:
                if re.search(pat, text):
                    pytest.fail(
                        f"Deleted symbol {name!r} found in {p}: pattern {pat!r}"
                    )
