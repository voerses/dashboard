"""V5 test-suite shared fixtures + infrastructure.

Infinite-recursion guard (2026-04-19):
  4 tests spawn subprocess `pytest v5/tests/` to verify the full-suite
  passes. When those subprocesses run, they INCLUDE those same tests,
  which spawn MORE subprocesses — exponential process explosion. This
  guard uses env-var depth tracking:

    Outermost pytest starts → conftest sets V5_PYTEST_DEPTH=1
    A test spawns subprocess pytest → child inherits env
    Child pytest starts → conftest sees 1 → bumps to 2
    At depth >= 2, tests marked `@pytest.mark.spawns_pytest_subprocess`
    are auto-skipped, breaking the recursion chain.

To add a new subprocess-spawning test, mark it with
`@pytest.mark.spawns_pytest_subprocess`. That's the ONLY discipline
needed — the guard handles the rest automatically.
"""
from __future__ import annotations

import json as _json
import os
from pathlib import Path as _Path

import pytest


# M10 AC #20 test-infrastructure compat: v4.paper_state has
# `deserialize_state(data)` but no `load(path)`. Frozen v4 prevents us
# from adding one. Provide the alias at test-collection time so the
# rollback-drill test (E1) can round-trip v4-loaded state via
# `v4.paper_state.load(path)`.
try:
    import v4.paper_state as _v4_paper_state  # noqa: E402
    if not hasattr(_v4_paper_state, "load"):
        def _v4_paper_state_load_alias(path):
            """M10 E1 compat alias — minimal dict-shape loader matching
            the v5.paper_state.load(path) contract for the rollback
            drill test. Returns a SimpleNamespace with `portfolio_equity`
            + `active_positions` read straight from the JSON file, no
            deserialize_state plumbing."""
            from types import SimpleNamespace as _SN
            with _Path(str(path)).open("r", encoding="utf-8") as fh:
                data = _json.load(fh)
            # Support both v1 (open_positions) and v3 (active_positions).
            positions = (
                data.get("active_positions")
                or data.get("open_positions")
                or []
            )
            return _SN(
                portfolio_equity=float(data.get("portfolio_equity", 0.0)),
                active_positions=positions,
                open_positions=positions,
                raw=data,
            )
        _v4_paper_state.load = _v4_paper_state_load_alias
except Exception:
    pass

_DEPTH_ENV = "V5_PYTEST_DEPTH"


def pytest_configure(config):
    """Increment depth env var at session start.

    Outermost run: V5_PYTEST_DEPTH unset → sets to 1.
    Nested run:    V5_PYTEST_DEPTH=1 → sets to 2.
    """
    current = int(os.environ.get(_DEPTH_ENV, "0"))
    os.environ[_DEPTH_ENV] = str(current + 1)

    # Register the custom marker so pytest doesn't warn on unknown-marker
    config.addinivalue_line(
        "markers",
        "spawns_pytest_subprocess: test spawns `pytest v5/tests/` as a "
        "subprocess; auto-skipped at nested depth to prevent recursion",
    )


def pytest_collection_modifyitems(config, items):
    """At nested depth (>= 2), skip tests that would spawn subprocess pytest."""
    depth = int(os.environ.get(_DEPTH_ENV, "1"))
    if depth <= 1:
        return  # outermost run — let everything run

    skip_marker = pytest.mark.skip(
        reason=f"nested pytest (depth={depth}); spawns_pytest_subprocess "
        "tests skipped to prevent infinite recursion"
    )
    for item in items:
        if item.get_closest_marker("spawns_pytest_subprocess"):
            item.add_marker(skip_marker)
