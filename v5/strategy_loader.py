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


# Names that produce mutable containers when called. Attribute form is
# `collections.<name>` / `np.<name>` / `array.<name>` etc.; bare-name form
# covers `from X import Y as Z; Z()` after resolution via
# `_collect_mutable_ctor_aliases`.
_MUTABLE_CTOR_BARE = frozenset({
    "dict", "list", "set", "bytearray",
})
_MUTABLE_CTOR_ATTR: frozenset[tuple[str, str]] = frozenset({
    # collections
    ("collections", "defaultdict"),
    ("collections", "OrderedDict"),
    ("collections", "deque"),
    ("collections", "Counter"),
    ("collections", "ChainMap"),
    # numpy/pandas/array — zero-initialised arrays are mutable state too
    ("np", "zeros"), ("np", "ones"), ("np", "empty"),
    ("np", "full"), ("np", "array"),
    ("numpy", "zeros"), ("numpy", "ones"), ("numpy", "empty"),
    ("numpy", "full"), ("numpy", "array"),
    ("pd", "DataFrame"), ("pd", "Series"),
    ("pandas", "DataFrame"), ("pandas", "Series"),
    ("array", "array"),
})


def _collect_mutable_ctor_aliases(tree: ast.AST) -> dict[str, str]:
    """Return {local_name: "origin.constructor"} for any import binding
    that resolves to a banned mutable constructor. Handles:

      from collections import deque           → {'deque':    'collections.deque'}
      from collections import deque as dq     → {'dq':       'collections.deque'}
      import collections                      → (no alias; attr form catches it)
      import collections as c                 → {'c.deque':  'collections.deque'} no-op
                                                (attr-form path handles c.<name>)
    """
    banned_local: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            src_mod = node.module or ""
            for alias in node.names:
                key = (src_mod, alias.name)
                if key in _MUTABLE_CTOR_ATTR:
                    local = alias.asname or alias.name
                    banned_local[local] = f"{src_mod}.{alias.name}"
    return banned_local


def _is_mutable_expr(value: ast.AST,
                     mutable_aliases: Optional[dict[str, str]] = None,
                     ) -> Optional[str]:
    """Return a short description if `value` is a mutable container expr.

    Catches dict/list/set literals, comprehensions, and constructor calls
    (builtins, `collections.*`, `np.*`, `pd.*`, `array.*`). When
    `mutable_aliases` is provided, also resolves bare-name / aliased
    imports back to their banned origin (fix for round-4 Quant MAJOR-2).
    """
    # Literals
    if isinstance(value, (ast.Dict, ast.DictComp)):
        return "{...}" if isinstance(value, ast.Dict) else "{k:v for ...}"
    if isinstance(value, (ast.List, ast.ListComp)):
        return "[...]" if isinstance(value, ast.List) else "[x for ...]"
    if isinstance(value, (ast.Set, ast.SetComp)):
        return "{...}" if isinstance(value, ast.Set) else "{x for ...}"
    # Constructor calls
    if isinstance(value, ast.Call):
        # Bare name: builtins + aliased imports
        if isinstance(value.func, ast.Name):
            name = value.func.id
            if name in _MUTABLE_CTOR_BARE:
                return f"{name}(...)"
            if mutable_aliases and name in mutable_aliases:
                return f"{mutable_aliases[name]}(...)"
        # Attribute form: module.constructor
        if isinstance(value.func, ast.Attribute) and isinstance(value.func.value, ast.Name):
            pair = (value.func.value.id, value.func.attr)
            if pair in _MUTABLE_CTOR_ATTR:
                return f"{pair[0]}.{pair[1]}(...)"
    return None


def _scan_class_level_mutable_state(path: Path, cls: ast.ClassDef,
                                     mutable_aliases: dict[str, str]) -> None:
    """Reject class-body assignments to mutable containers.

    Class-level mutables are shared across ALL instances (including across
    WF folds when the strategy class is re-instantiated). Round-3 Quant
    MAJOR — `class S: _cache = {}` bypasses the module-level scan but
    has the same fold-contamination semantics.
    """
    for stmt in cls.body:
        if not isinstance(stmt, ast.Assign):
            continue
        mut_desc = _is_mutable_expr(stmt.value, mutable_aliases)
        if mut_desc is None:
            continue
        for target in stmt.targets:
            if isinstance(target, ast.Name):
                raise StrategyLoadError(
                    f"{path}:{stmt.lineno}: class-level mutable "
                    f"`{cls.name}.{target.id} = {mut_desc}` rejected. "
                    f"Class-body mutables are SHARED across all instances "
                    f"(AC-V1 — fresh Strategy per WF fold). Initialize in "
                    f"`__init__` as `self.{target.id} = ...`."
                )


def _scan_function_default_mutable(path: Path, fn: ast.AST,
                                     mutable_aliases: dict[str, str]) -> None:
    """Reject `def fn(x={}, y=[])` — Python evaluates defaults ONCE at
    module load, so they behave as hidden module-level mutables.

    Round-3 Quant MAJOR: function-default mutables bypass both the
    module-level and class-level scans while serving the same role.
    """
    if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return
    for default in fn.args.defaults + fn.args.kw_defaults:
        if default is None:
            continue
        mut_desc = _is_mutable_expr(default, mutable_aliases)
        if mut_desc is not None:
            raise StrategyLoadError(
                f"{path}:{default.lineno}: function `{fn.name}` uses "
                f"mutable default `{mut_desc}` — evaluated once at load "
                f"time, behaves as module-level mutable state. Use "
                f"`arg=None` + `if arg is None: arg = {mut_desc}` "
                f"(AC-V1)."
            )


def _scan_module_level_mutable_state(path: Path, tree: ast.AST) -> None:
    """AC-V1 — reject module-level mutable state that contaminates folds.

    Strategies must keep all mutable state on `self`, not at module level.
    The WF runner instantiates a fresh Strategy per fold, but module-level
    caches (e.g. `_composite_cache = {}`) persist across folds via module
    identity → fold N+1 sees fold N's data.

    Extended (round 3 + 4) to catch bypass patterns surfaced by the Quant
    reviewer: function-default mutables, class-level mutables, collections
    constructor family, np/pd array constructors, comprehension literals,
    and bare-import / aliased-import constructor calls.
    """
    # Build alias table so `from collections import deque as dq; dq()`
    # resolves back to collections.deque (round-4 Quant MAJOR).
    mutable_aliases = _collect_mutable_ctor_aliases(tree)

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
            _scan_function_default_mutable(path, stmt, mutable_aliases)

        # Top-level assignments to mutable containers (dict/list/set literals or
        # constructor calls): `_STATE = {}`, `_HISTORY = []`, `_SEEN = set()`
        if isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if not isinstance(target, ast.Name):
                    continue
                mut_desc = _is_mutable_expr(stmt.value, mutable_aliases)
                if mut_desc is not None:
                    raise StrategyLoadError(
                        f"{path}:{stmt.lineno}: top-level mutable "
                        f"`{target.id} = {mut_desc}` rejected. Use "
                        f"`self.{target.id}` inside Strategy subclass (AC-V1)."
                    )

        # Class-level mutables inside a ClassDef body
        if isinstance(stmt, ast.ClassDef):
            _scan_class_level_mutable_state(path, stmt, mutable_aliases)
            # Also scan nested functions/methods for default-mutable bypass
            for inner in stmt.body:
                _scan_function_default_mutable(path, inner, mutable_aliases)


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

        # Form 4: getattr-bypass — `getattr(datetime, 'now')(...)` or
        # `getattr(time, 'time')(...)`. Round-3 Quant MAJOR: strategies
        # would otherwise sidestep the whole banned-attr scan by looking
        # the method up dynamically.
        if isinstance(node.func, ast.Call) and isinstance(node.func.func, ast.Name):
            if node.func.func.id == "getattr" and len(node.func.args) >= 2:
                target_arg = node.func.args[0]
                name_arg = node.func.args[1]
                if (isinstance(target_arg, ast.Name)
                        and isinstance(name_arg, ast.Constant)
                        and isinstance(name_arg.value, str)):
                    probe_chain = (target_arg.id, name_arg.value)
                    if probe_chain in _BANNED_ATTR:
                        raise StrategyLoadError(
                            f"{path}:{node.lineno}: banned clock call via "
                            f"getattr({target_arg.id}, {name_arg.value!r})() "
                            f"bypass. Use ctx.clock.now_ns() — AC-V2."
                        )
                    if name_arg.value in _BANNED_BARE_IMPORT_NAMES:
                        raise StrategyLoadError(
                            f"{path}:{node.lineno}: banned clock call via "
                            f"getattr({target_arg.id}, {name_arg.value!r})() "
                            f"bypass. Use ctx.clock.now_ns() — AC-V2."
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
