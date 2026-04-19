"""M7 — Strategy loader with AST scan (AC-V2).

Replaces v4/engine.py::_load_strategy_fn. Before importing a strategy, scans
the source for banned clock calls and rejects at load time — NOT at first
tick. Strategies must use `ctx.clock.now_ns()` for deterministic time.

Banned forms detected:
  - Attribute access: `time.time()`, `datetime.now()`, `pd.Timestamp.now()`,
    `np.datetime64('now')`, etc.
  - Bare imports:      `from time import time; time()`
  - Aliased imports:   `from time import time as t; t()`

Docstrings/comments containing banned names as text are OK — only Call nodes
that resolve to banned targets trigger the error.
"""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path
from typing import Iterable, Optional


class StrategyLoadError(Exception):
    """Raised when a strategy's source contains a banned call or fails to load."""


# ----------------------------------------------------------------
# Banned-call registry (per brief AC-V2 + reviewer H2/M2 extensions)
# ----------------------------------------------------------------

# Attribute-access banned patterns: (module_alias, attr_chain)
# Matches `module.attr()` calls. Module alias tracked via the import table.
_BANNED_ATTR: frozenset[tuple[str, ...]] = frozenset({
    # time module
    ("time", "time"),
    ("time", "time_ns"),
    ("time", "monotonic"),
    ("time", "perf_counter"),
    ("time", "gmtime"),
    ("time", "localtime"),
    ("time", "strftime"),
    ("time", "asctime"),
    ("time", "ctime"),
    ("time", "mktime"),
    # datetime module (typically `from datetime import datetime`)
    ("datetime", "now"),
    ("datetime", "utcnow"),
    ("datetime", "today"),
    # pandas
    ("pd", "Timestamp", "now"),
    ("pandas", "Timestamp", "now"),
    # numpy — np.datetime64('now') — special-cased (call with literal 'now')
})

# Bare-import banned names (when imported via `from time import X`)
_BANNED_BARE_IMPORT_NAMES: frozenset[str] = frozenset({
    "time", "time_ns", "monotonic", "perf_counter",
    "gmtime", "localtime", "strftime", "asctime", "ctime", "mktime",
    "now", "utcnow", "today",  # datetime.now() imported bare
})


# ----------------------------------------------------------------
# Scanner
# ----------------------------------------------------------------


def _walk_attribute_chain(node: ast.AST) -> tuple[str, ...]:
    """Extract ('pd', 'Timestamp', 'now') from an ast.Attribute chain."""
    parts: list[str] = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
    return tuple(reversed(parts))


def _collect_banned_bare_aliases(tree: ast.AST) -> dict[str, str]:
    """Return {local_name: banned_real_name} for any `from X import Y` that
    imports a banned name. E.g., `from time import time as t` → {'t': 'time'}.
    """
    banned_local: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            src_module = node.module or ""
            for alias in node.names:
                if alias.name in _BANNED_BARE_IMPORT_NAMES:
                    local = alias.asname or alias.name
                    banned_local[local] = f"{src_module}.{alias.name}"
    return banned_local


def _scan_module_level_mutable_state(path: Path, tree: ast.AST) -> None:
    """AC-V1 — reject module-level mutable state that contaminates folds.

    Strategies must keep all mutable state on `self`, not at module level.
    The WF runner instantiates a fresh Strategy per fold, but module-level
    caches (e.g. `_composite_cache = {}`) persist across folds via module
    identity → fold N+1 sees fold N's data.
    """
    for stmt in tree.body:
        # `global X` declarations at module level (inside functions) — flag any
        # function that declares `global` so we catch `global _COUNTER` patterns
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for sub in ast.walk(stmt):
                if isinstance(sub, ast.Global):
                    raise StrategyLoadError(
                        f"{path}:{sub.lineno}: `global {', '.join(sub.names)}` "
                        f"is a module-level mutable state pattern. Strategies "
                        f"must keep mutable state on `self` (AC-V1 — fresh "
                        f"Strategy per WF fold)."
                    )

        # Top-level assignments to mutable containers (dict/list/set literals or
        # constructor calls): `_STATE = {}`, `_HISTORY = []`, `_SEEN = set()`
        if isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if not isinstance(target, ast.Name):
                    continue
                value = stmt.value
                # Dict/list/set literal
                if isinstance(value, ast.Dict):
                    raise StrategyLoadError(
                        f"{path}:{stmt.lineno}: top-level mutable dict "
                        f"`{target.id} = {{...}}` rejected. Use `self.{target.id}` "
                        f"inside Strategy subclass (AC-V1)."
                    )
                if isinstance(value, ast.List):
                    raise StrategyLoadError(
                        f"{path}:{stmt.lineno}: top-level mutable list "
                        f"`{target.id} = [...]` rejected. Use `self.{target.id}` "
                        f"(AC-V1)."
                    )
                if isinstance(value, ast.Set):
                    raise StrategyLoadError(
                        f"{path}:{stmt.lineno}: top-level mutable set "
                        f"`{target.id} = {{...}}` rejected (AC-V1)."
                    )
                # Constructor calls: dict(), list(), set()
                if isinstance(value, ast.Call) and isinstance(value.func, ast.Name):
                    if value.func.id in ("dict", "list", "set"):
                        raise StrategyLoadError(
                            f"{path}:{stmt.lineno}: top-level mutable "
                            f"`{target.id} = {value.func.id}(...)` rejected. "
                            f"Module-level mutable state contaminates WF folds "
                            f"(AC-V1)."
                        )


def _scan_source(path: Path, source: str) -> None:
    """Raise StrategyLoadError if banned clock calls or module-level mutable
    state are present."""
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as e:
        raise StrategyLoadError(
            f"{path}: cannot parse strategy source: {e}"
        ) from e

    _scan_module_level_mutable_state(path, tree)

    bare_banned = _collect_banned_bare_aliases(tree)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        # Form 1: attribute access — module.attr() or module.sub.attr()
        if isinstance(node.func, ast.Attribute):
            chain = _walk_attribute_chain(node.func)
            if chain in _BANNED_ATTR:
                raise StrategyLoadError(
                    f"{path}:{node.lineno}: banned clock call "
                    f"{'.'.join(chain)}() detected. Strategies MUST use "
                    f"`ctx.clock.now_ns()` — AC-V2 determinism invariant."
                )
            # Also check suffix match for 3-part chains (pd.Timestamp.now via `ts_mod.now()`)
            for banned in _BANNED_ATTR:
                if len(banned) >= 2 and len(chain) >= 2 and chain[-len(banned):] == banned:
                    raise StrategyLoadError(
                        f"{path}:{node.lineno}: banned clock call "
                        f"{'.'.join(chain)}() matches banned pattern "
                        f"{'.'.join(banned)}. Strategies MUST use ctx.clock."
                    )

        # Form 2: bare-name or aliased-bare-name call
        if isinstance(node.func, ast.Name):
            local = node.func.id
            if local in bare_banned:
                raise StrategyLoadError(
                    f"{path}:{node.lineno}: banned clock call via "
                    f"`{local}()` (alias for {bare_banned[local]}). "
                    f"Strategies MUST use `ctx.clock.now_ns()`."
                )

        # Form 3: np.datetime64('now')
        if isinstance(node.func, ast.Attribute):
            chain = _walk_attribute_chain(node.func)
            if chain in (("np", "datetime64"), ("numpy", "datetime64")):
                if node.args and isinstance(node.args[0], ast.Constant):
                    if node.args[0].value == "now":
                        raise StrategyLoadError(
                            f"{path}:{node.lineno}: banned wall-clock "
                            f"{'.'.join(chain)}('now') — use ctx.clock."
                        )


# ----------------------------------------------------------------
# Loader
# ----------------------------------------------------------------


class StrategyLoader:
    """AST-scanning strategy loader.

    Call `loader.load(path)` to (1) scan source for banned calls, (2) exec
    module, (3) return the module object. Exceptions from step 1 are
    StrategyLoadError; from step 2 are whatever the strategy raises during
    module init (wrapped in StrategyLoadError for clarity).
    """

    def load(self, path):
        """Load a strategy from `path` (str or Path). Scans + imports."""
        path = Path(path)
        if not path.is_file():
            raise StrategyLoadError(f"strategy file not found: {path}")
        source = path.read_text()
        _scan_source(path, source)
        # Safe to exec — scan passed
        module_name = f"_v5_strategy_{path.stem}"
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise StrategyLoadError(f"failed to build import spec for {path}")
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception as e:
            raise StrategyLoadError(
                f"{path}: strategy module __init__ raised {type(e).__name__}: {e}"
            ) from e
        return module
