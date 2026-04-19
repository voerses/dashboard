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

import os

import pytest

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
