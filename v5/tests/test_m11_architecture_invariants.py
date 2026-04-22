"""M11 AC-10 — Architecture invariants (structural, not behavioral).

These tests exist to prevent the failure mode where an implementer makes the
behavioral acceptance tests (AC-1 through AC-9) green via kludges that
silently violate ADR-0001 / ADR-0002 — e.g., re-creating the pre-baked-array
pattern under a new name, aliasing `DataKind` to maintain compatibility,
leaving the bridge wrapper renamed but behaviorally identical.

Each test is a structural assertion: grep-based, import-based, or
inspect-based. None of them test behavior — they test SHAPE.

All tests must FAIL today (current codebase still has `DataKind`,
`MultiInstrumentCache`, bridge helpers, etc.) and PASS only after M11 is
implemented per the design.

See: knowledge/adr/ADR-0001-unified-event-driven-execution.md
     knowledge/adr/ADR-0002-extensible-data-model-and-native-signal-pipeline.md
     .specs/active/m11-unified-event-loop/brief.md (AC-10)
     .specs/active/m11-unified-event-loop/tasks.md ("Implementer/reviewer discipline")
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest


# ─── Helpers ─────────────────────────────────────────────────────────

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_V5_DIR = _PROJECT_ROOT / "v5"


def _read(path_from_project_root: str) -> str:
    """Read a file relative to the project root."""
    return (_PROJECT_ROOT / path_from_project_root).read_text(encoding="utf-8")


def _strip_docstrings_and_comments(source: str) -> str:
    """Return ``source`` with triple-quoted strings and ``#`` comments
    removed — so a grep over the result only hits executable code.

    This deliberately keeps single/double-quoted string literals intact;
    the forbidden names this helper supports are Python identifiers that
    don't legitimately appear inside short string literals in the files
    we scan. Triple-quoted strings (docstrings) are the only construct
    where the forbidden identifier names *do* legitimately appear —
    the VectorizedStrategy Protocol docstring describes
    ``to_token_bar_arrays`` as the opt-in contract name.
    """
    # Strip triple-single and triple-double docstrings (non-greedy).
    src = re.sub(r'"""[\s\S]*?"""', '', source)
    src = re.sub(r"'''[\s\S]*?'''", '', src)
    # Strip full-line and trailing ``#`` comments.
    src = re.sub(r'(?m)#.*$', '', src)
    return src


def _extract_function_body(source: str, function_name: str) -> str:
    """Return the source of `def <function_name>(...):` including its body.

    Uses a simple indent-based scan; matches the first function definition
    by that name. Returns empty string if not found.
    """
    pattern = re.compile(
        rf"^(?P<indent>\s*)def\s+{re.escape(function_name)}\b[^\n]*:",
        re.MULTILINE,
    )
    match = pattern.search(source)
    if match is None:
        return ""
    start = match.start()
    lines = source[start:].splitlines(keepends=True)
    if not lines:
        return ""
    header = lines[0]
    base_indent = len(header) - len(header.lstrip())
    body_lines = [header]
    for line in lines[1:]:
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            body_lines.append(line)
            continue
        line_indent = len(line) - len(stripped)
        if line_indent <= base_indent:
            break
        body_lines.append(line)
    return "".join(body_lines)


# ─── AC-10 Structural invariants ─────────────────────────────────────


def test_datakind_enum_genuinely_deleted_not_aliased():
    """`DataKind` name must not resolve from `v5.data.streams` post-M11.

    Rationale (ADR-0002 move #1): the polymorphic `Data` class hierarchy
    REPLACES `DataKind`. If someone adds `DataKind = BarData` or similar
    alias to preserve import compatibility, they've silently reintroduced
    the enum-plus-discriminator model. This test catches that kludge.
    """
    with pytest.raises(ImportError):
        from v5.data.streams import DataKind  # noqa: F401


def test_multi_instrument_cache_genuinely_deleted():
    """`MultiInstrumentCache` must not exist post-M11.

    Rationale (ADR-0002 move #3): unified `MarketDataCache` replaces it.
    If someone keeps `MultiInstrumentCache` alongside `MarketDataCache`
    for "backward compatibility," they've silently reintroduced the dual
    cache pattern.
    """
    with pytest.raises(ImportError):
        from v5.data.cache import MultiInstrumentCache  # noqa: F401


def test_simulate_portfolio_has_no_bridge_kwargs():
    """`simulate_portfolio` must not accept `strategies=` or `ctx=` kwargs.

    Rationale (AC-3): the bridge was the impedance-matcher that conflated
    data-loading with simulation. ADR-0001 requires event-driven dispatch
    via the new orchestrator. If the bridge kwargs remain — even unused —
    the impedance match is still the declared API surface.
    """
    from v5.simulator import simulate_portfolio

    sig = inspect.signature(simulate_portfolio)
    params = sig.parameters
    assert "strategies" not in params, (
        "simulate_portfolio still accepts strategies= kwarg; ADR-0001 "
        "requires removal"
    )
    assert "ctx" not in params, (
        "simulate_portfolio still accepts ctx= kwarg; ADR-0001 requires removal"
    )


def test_bridge_helpers_absent_from_simulator():
    """The bridge helper functions must not exist in `v5/simulator.py`.

    Covers: `_engine_precompute_fallback`, `_build_multi_token_ctx_from_bundle`,
    `_wrap_ctx_if_raw_bundle`, `_MutationGuard`, `_build_token_bar_arrays_from_generate`.

    Rationale: deleting only the kwargs but leaving the helper functions
    in place is incomplete — they'd be dead code today, cargo-cult surface
    for the next session tomorrow.
    """
    import v5.simulator as sim

    forbidden = [
        "_engine_precompute_fallback",
        "_build_multi_token_ctx_from_bundle",
        "_wrap_ctx_if_raw_bundle",
        "_MutationGuard",
        "_build_token_bar_arrays_from_generate",
    ]
    present = [name for name in forbidden if hasattr(sim, name)]
    assert not present, (
        f"Bridge helpers still present in v5/simulator.py: {present}. "
        f"ADR-0001 requires deletion, not just bypassing."
    )


def test_bridge_helpers_absent_from_strategy_api_and_strategies():
    """Bridge helper names must not appear in ``v5/strategy_api.py`` or
    any ``v5/strategies/s*.py`` file (executable code; docstrings OK).

    Rationale (RF-2 rework, M11 Stage 1): the original AC-10 grep scanned
    only ``v5/simulator.py``. The final reviewer (reviews/final.json
    caveat #5) found runtime-reachable dead code at one-hop remove:
    ``v5/strategy_api.py::_build_token_bar_arrays_from_generate`` was a
    delayed-import shim pointing at the deleted simulator symbol, and
    ``v5/strategies/s524m_v5.py::to_token_bar_arrays`` +
    ``v5/strategies/s523c_v5.py::to_token_bar_arrays`` both imported
    through that shim. Calling any of these raised ImportError in
    production. This invariant extends the grep to ``v5/strategy_api.py``
    and every ``v5/strategies/s*.py`` to catch cargo-culting-under-new-name
    one module boundary away from the simulator.

    Two name lists:

    * ``forbidden_everywhere`` — names with no legitimate occurrence
      anywhere in these files (bridge helpers, mutation guards, precompute
      fallback, bundle ctx wrappers, the deleted cross-module shim).
    * ``forbidden_in_strategies`` — concrete strategy files must not
      implement ``to_token_bar_arrays``. ``v5/strategy_api.py`` is
      exempted because the ``VectorizedStrategy`` Protocol definition
      itself contains the method stub as its structural contract; that
      Protocol is passive (no caller invokes it) and its presence does
      not reintroduce executable precompute surface.
    """
    forbidden_everywhere = [
        "_engine_precompute_fallback",
        "_build_multi_token_ctx_from_bundle",
        "_wrap_ctx_if_raw_bundle",
        "_MutationGuard",
        "_build_token_bar_arrays_from_generate",
    ]
    forbidden_in_strategies = ["to_token_bar_arrays"]

    offenses: list[tuple[str, str]] = []

    # v5/strategy_api.py — the delayed-import shim lived here.
    strategy_api_src = _read("v5/strategy_api.py")
    api_code = _strip_docstrings_and_comments(strategy_api_src)
    for name in forbidden_everywhere:
        if name in api_code:
            offenses.append(("v5/strategy_api.py", name))

    # v5/strategies/s*.py — every concrete strategy file must be clean
    # on BOTH lists (bridge helpers AND the to_token_bar_arrays method).
    strategies_dir = _V5_DIR / "strategies"
    for strat_path in sorted(strategies_dir.glob("s*.py")):
        src = strat_path.read_text(encoding="utf-8")
        code = _strip_docstrings_and_comments(src)
        rel = str(strat_path.relative_to(_PROJECT_ROOT))
        for name in forbidden_everywhere + forbidden_in_strategies:
            if name in code:
                offenses.append((rel, name))

    assert not offenses, (
        "Bridge/precompute helper names appear in executable code of "
        "v5/strategy_api.py or v5/strategies/s*.py (one-hop dead code "
        "surface per RF-2):\n  "
        + "\n  ".join(f"{path}: {name}" for path, name in offenses)
        + "\nADR-0001/ADR-0002 require deletion; these are not cargo-cult "
        "surfaces the next session can re-export."
    )


def test_indicators_mixin_stub_absent_from_simulator():
    """`_IndicatorsMixin` (the fake ctx facade) must not exist in simulator.

    Rationale: this was the bridge's hand-populated stub replacing real
    DataEngine delivery. Its presence signals the bridge path is still live.
    """
    src = _read("v5/simulator.py")
    # Allow the name to appear only in comments/docstrings that reference
    # historical context; must not appear as a class definition.
    assert "class _IndicatorsMixin" not in src, (
        "`class _IndicatorsMixin` still defined in v5/simulator.py; ADR-0001 "
        "requires deletion in favor of DataEngine-backed ctx"
    )


def test_process_orders_native_has_no_local_bar_array_indexing():
    """`_process_orders_native` body must not use the `[local_bar]` pattern.

    Rationale (ADR-0002 move #2): native signal consumption means
    reading `TokenSignal` fields directly, not walking pre-baked
    `TokenBarArrays.field[local_bar]`. If `[local_bar]` appears anywhere
    in the native function, the implementer cargo-culted the old pattern.
    """
    src = _read("v5/simulator.py")
    body = _extract_function_body(src, "_process_orders_native")
    assert body, (
        "`_process_orders_native` not defined in v5/simulator.py; "
        "AC-5 requires this function to exist (commit 6)"
    )
    assert "[local_bar]" not in body, (
        "_process_orders_native body contains `[local_bar]` indexing — "
        "ADR-0002 move #2 requires reading TokenSignal directly, not "
        "walking pre-baked arrays at local_bar."
    )


def test_process_orders_native_has_no_tokenbararrays_wrapper():
    """`_process_orders_native` body must not reference `TokenBarArrays`.

    Rationale (ADR-0002 move #2): the single-bar `TokenBarArrays`
    wrapper that was considered for the initial pragmatic design is
    explicitly rejected in option B. If `TokenBarArrays` appears in the
    native function, the wrapper has been smuggled back in.
    """
    src = _read("v5/simulator.py")
    body = _extract_function_body(src, "_process_orders_native")
    assert body, (
        "`_process_orders_native` not defined in v5/simulator.py"
    )
    assert "TokenBarArrays" not in body, (
        "_process_orders_native references TokenBarArrays — ADR-0002 "
        "move #2 requires native UniverseSignals consumption; no wrapper."
    )


def test_process_exits_native_has_no_local_bar_indexing():
    """Same grep invariant for `_process_exits_native`."""
    src = _read("v5/simulator.py")
    body = _extract_function_body(src, "_process_exits_native")
    assert body, (
        "`_process_exits_native` not defined in v5/simulator.py; "
        "AC-5 requires this function to exist (commit 6)"
    )
    assert "[local_bar]" not in body, (
        "_process_exits_native body contains `[local_bar]` indexing — "
        "ADR-0002 move #2 requires reading BarContext from cache, not "
        "walking pre-baked arrays at local_bar."
    )


def test_paper_tick_handler_invokes_strategy_generate():
    """`v5/paper_engine.py` must contain a direct call to `strategy.generate`
    or `strat.generate` inside the tick path.

    Rationale (ADR-0001): backtest and paper share the event-driven loop;
    paper tick handler calls `strategy.generate(ctx, bar_idx)` inline.
    If the file contains no such call, paper is still on the pre-baked
    walk-arrays dispatch.
    """
    src = _read("v5/paper_engine.py")
    # Match `strategy.generate(`, `strat.generate(`, `s.generate(` (common names)
    pattern = re.compile(
        r"\b(strategy|strat|s)\.generate\s*\(",
    )
    match = pattern.search(src)
    assert match is not None, (
        "v5/paper_engine.py contains no direct call to strategy.generate() — "
        "ADR-0001 requires per-tick inline dispatch in paper, not pre-baked "
        "array walks. Commit 7 must introduce such a call."
    )


def test_cache_armed_levels_deleted_from_paper_engine():
    """`_cache_armed_levels` must be deleted from paper_engine.

    Rationale (ADR-0002 move #4): armed-entry intra-bar trigger firing is
    engine order-manager state (pending `Order` + `trigger_price` + TIF,
    already in v5/orders.py from M5). The `_cache_armed_levels`
    walk-arrays pattern is redundant and must be removed.
    """
    import v5.paper_engine as pe

    # Check both module-level and class-level
    module_has = hasattr(pe, "_cache_armed_levels")
    class_has = False
    for name in dir(pe):
        obj = getattr(pe, name, None)
        if inspect.isclass(obj) and hasattr(obj, "_cache_armed_levels"):
            class_has = True
            break
    assert not module_has and not class_has, (
        "`_cache_armed_levels` still present in v5/paper_engine.py — "
        "ADR-0002 move #4 requires deletion; armed-entry firing is "
        "delegated to the Order/ArmedEntry manager (M5 in v5/orders.py)."
    )


def test_precompute_strategy_signals_not_called_in_paper_tick_path():
    """`precompute_strategy_signals` must not be invoked from paper's
    tick handler (`_tick_internal`, `_tick_internal_body`, etc.).

    Rationale (ADR-0001): paper pre-computing full TokenBarArrays each
    tick is the dual-dispatch pattern ADR-0001 eliminates. Per-tick
    inline `strategy.generate()` replaces it.
    """
    src = _read("v5/paper_engine.py")
    # Extract the bodies of the known tick entry points
    bodies = []
    for fn in ("_tick_internal", "_tick_internal_async", "_tick_internal_body"):
        body = _extract_function_body(src, fn)
        if body:
            bodies.append((fn, body))
    assert bodies, (
        "None of the expected tick entry points (_tick_internal, "
        "_tick_internal_async, _tick_internal_body) found in v5/paper_engine.py"
    )
    offending = [
        fn for fn, body in bodies if "precompute_strategy_signals" in body
    ]
    assert not offending, (
        f"`precompute_strategy_signals` still called from paper tick path "
        f"in: {offending}. ADR-0001 forbids pre-computing the full "
        f"TokenBarArrays at each tick; use inline strategy.generate() instead."
    )


def test_build_bar_maps_not_referenced_in_paper_tick_path():
    """`_build_bar_maps` must not be referenced in paper's tick path.

    Rationale: `bar_maps` is the tick-counter-to-local_bar translator
    for walking pre-baked arrays. Deleting it is a structural signal
    that the walk-arrays dispatch is gone.
    """
    src = _read("v5/paper_engine.py")
    bodies = []
    for fn in ("_tick_internal", "_tick_internal_async", "_tick_internal_body"):
        body = _extract_function_body(src, fn)
        if body:
            bodies.append((fn, body))
    offending = [
        fn for fn, body in bodies if "_build_bar_maps" in body or "bar_maps" in body
    ]
    assert not offending, (
        f"`bar_maps` / `_build_bar_maps` still referenced in paper tick path "
        f"in: {offending}. ADR-0001 requires deletion — the local_bar "
        f"translation is obsolete under per-tick strategy.generate() dispatch."
    )


def test_run_backtest_orchestrator_module_exists():
    """`v5.run_backtest` module must exist as the canonical backtest entry.

    Rationale (AC-8): the single top-level orchestrator replaces ad-hoc
    backtest setup paths. Its existence at this module path is the API
    contract future callers depend on.
    """
    import importlib

    try:
        module = importlib.import_module("v5.run_backtest")
    except ImportError as e:
        pytest.fail(
            f"v5.run_backtest module missing — AC-8 requires this orchestrator "
            f"to be the canonical backtest entry. (ImportError: {e})"
        )
    assert hasattr(module, "run_backtest"), (
        "v5.run_backtest exists but has no `run_backtest` function — "
        "AC-8 requires that name as the top-level entry."
    )


def test_run_backtest_signature_uses_declared_shape():
    """`v5.run_backtest.run_backtest` must accept
    `(strategies, instruments, start, end, manifest, config)` shape.

    Rationale (AC-8): the orchestrator's signature defines what callers
    provide. Design §7 pins this shape; drift from it suggests the
    orchestrator was built ad-hoc rather than per design.
    """
    try:
        from v5.run_backtest import run_backtest as fn
    except ImportError:
        pytest.fail("v5.run_backtest.run_backtest not importable")
    sig = inspect.signature(fn)
    params = set(sig.parameters.keys())
    expected = {"strategies", "instruments", "start", "end"}
    # manifest and config are allowed to have defaults; only check presence
    missing = expected - params
    assert not missing, (
        f"v5.run_backtest.run_backtest missing required parameters: "
        f"{missing}. Actual: {sorted(params)}"
    )


def test_last_known_regimes_genuinely_deleted():
    """`last_known_regimes` / `_last_known_regimes` must be absent from
    executable code in `v5/paper_engine.py` and `v5/paper_state.py`.

    Rationale (M11 Commit-8 rework Stage-1b, ADR-0002 §4): the
    `_last_known_regimes` dict was never populated and the kwarg was never
    part of the persistence signatures. Regime is a strategy-owned
    `MetricData` concern; flows through `required_data()` (pending strategy
    migration). No live readers remain after Stage-1b.
    """
    offenses: list[tuple[str, int]] = []
    for rel_path in ("v5/paper_engine.py", "v5/paper_state.py"):
        src = _read(rel_path)
        code = _strip_docstrings_and_comments(src)
        hits = code.count("last_known_regimes")
        if hits:
            offenses.append((rel_path, hits))
    assert not offenses, (
        "`last_known_regimes` / `_last_known_regimes` still appears as "
        "executable code:\n  "
        + "\n  ".join(f"{path}: × {hits}" for path, hits in offenses)
        + "\nADR-0002 §4: regime is a strategy-owned MetricData concern, "
        "not engine persistence. Delete or migrate to required_data()."
    )


def test_legacy_persistence_kwargs_and_json_keys_removed():
    """The legacy persistence kwargs (`armed_tokens=`, `filled_4h_windows=`,
    `last_known_prices=`, `last_known_regimes=`) and corresponding JSON
    dict keys (`"armed_tokens"`, `"filled_4h_windows"`,
    `"last_known_prices"`, `"last_known_regimes"`) must be absent from
    `v5/paper_state.py`, `v5/paper_engine.py`, and `v5/paper_utils.py`
    executable code.

    This covers the exact failure mode the Stage-1b rework is fixing:
    `atomic_write_state` / `serialize_state` / `serialize_engine_state`
    had these as kwargs and persisted them into state.json. Per ADR-0002:

    - ``armed_tokens`` → ``Order.status=ARMED`` + ``trigger_price`` +
      ``TimeInForce`` in ``SimulationState.pending_orders`` (M5,
      schema-v3 ``open_orders`` read by ``read_paper_state``).
    - ``filled_4h_windows`` → ``DataEngine.register_strategy_cadences`` +
      ``strategies_due_at`` (M11). In-memory `_filled_4h_windows` TTL
      container remains for intra-session re-entry gating.
    - ``last_known_prices`` → ``MarketDataCache.bars(...).close[-1]``
      (M6). In-memory `_last_known_prices` dict remains as fast-path cache.
    - ``last_known_regimes`` → strategy ``required_data()`` per ADR-0002
      §4 (pending strategy port).

    The in-memory backing state (`_armed_tokens_lock`, `_pending_entries`,
    `_filled_4h_windows`, `_last_known_prices`) is permitted — only the
    persistence surface is forbidden.
    """
    forbidden_kwargs = [
        "armed_tokens=",
        "filled_4h_windows=",
        "last_known_prices=",
        "last_known_regimes=",
    ]
    forbidden_json_keys = [
        '"armed_tokens"',
        '"filled_4h_windows"',
        '"last_known_prices"',
        '"last_known_regimes"',
        "'armed_tokens'",
        "'filled_4h_windows'",
        "'last_known_prices'",
        "'last_known_regimes'",
    ]

    offenses: list[tuple[str, str, int]] = []
    for rel_path in (
        "v5/paper_state.py",
        "v5/paper_engine.py",
        "v5/paper_utils.py",
    ):
        src = _read(rel_path)
        code = _strip_docstrings_and_comments(src)
        for kw in forbidden_kwargs:
            hits = code.count(kw)
            if hits:
                offenses.append((rel_path, kw, hits))
        for key in forbidden_json_keys:
            hits = code.count(key)
            if hits:
                offenses.append((rel_path, key, hits))

    assert not offenses, (
        "Legacy persistence scaffolding still present in executable "
        "code:\n  "
        + "\n  ".join(
            f"{path}: `{pattern}` × {hits}"
            for path, pattern, hits in offenses
        )
        + "\nM11 Commit-8 rework Stage-1b (ADR-0002) requires these kwargs "
        "and JSON keys to be deleted. The unified architecture replaces "
        "each (see test docstring)."
    )


def test_legacy_persistence_helpers_deleted_from_paper_engine():
    """`_serialize_armed_tokens`, `_serialize_filled_4h_windows`,
    `_deserialize_armed_tokens`, `_deserialize_filled_4h_windows` must be
    deleted from `v5/paper_engine.py`.

    Rationale: the helpers materialized the legacy armed-tokens /
    filled-windows snapshot for state.json. With persistence removed
    (Stage-1b, ADR-0002), they are dead methods and their presence would
    be cargo-cult surface for the next session.
    """
    import v5.paper_engine as pe

    forbidden_methods = [
        "_serialize_armed_tokens",
        "_serialize_filled_4h_windows",
        "_deserialize_armed_tokens",
        "_deserialize_filled_4h_windows",
    ]
    offenses: list[str] = []
    module_has = [name for name in forbidden_methods if hasattr(pe, name)]
    offenses.extend(f"v5.paper_engine.{name}" for name in module_has)
    for cls_name in dir(pe):
        obj = getattr(pe, cls_name, None)
        if inspect.isclass(obj):
            for name in forbidden_methods:
                if name in obj.__dict__:
                    offenses.append(
                        f"v5.paper_engine.{cls_name}.{name}"
                    )

    assert not offenses, (
        "Legacy persistence helpers still present:\n  "
        + "\n  ".join(offenses)
        + "\nADR-0002: armed entries live in Order.status=ARMED records "
        "(M5), not engine-side helper serializers."
    )


# ─── M11 Stage-3 (task 8.9) — single clock authority invariants ──────


def test_no_wall_clock_reads_in_paper_tick_body():
    """ADR-0001: replay mode must use the shared SimulationClock, never time.time_ns().

    Rationale (M11 Stage-3 P4): the paper tick body historically
    included a nested ``_PaperTickClock`` whose ``now_ns()`` returned
    ``time.time_ns()`` (wall clock). When reached on the replay path
    (no ``_market_cache`` attached), this silently fabricated a
    wall-clock time for simulation PIT slicing — a correctness bug
    indistinguishable from clean code at the call site.

    This invariant is structural: the three dispatch functions that
    constitute the replay-reachable tick path —
    ``_tick_internal_body``, ``_build_ctx_for_tick``,
    ``_dispatch_strategies_at_tick`` — MUST NOT textually contain
    ``time.time_ns()`` in their executable code. Wall-clock reads for
    paper-live production belong on a module-scope helper
    (``_PaperLiveWallClock``), gated by an explicit
    ``_paper_live_mode = True`` engine attribute.
    """
    src = _read("v5/paper_engine.py")
    # Strip comments/docstrings so forbidden-identifier grep only sees
    # executable code. The module-scope ``_PaperLiveWallClock.now_ns``
    # intentionally retains the call for the paper-live opt-in path.
    stripped = _strip_docstrings_and_comments(src)
    # Extract the body of each dispatch function. The existing
    # ``_extract_function_body`` helper requires the ``def ... :``
    # header to fit on a single line; ``_dispatch_strategies_at_tick``
    # has a multi-line signature, so scan with a dedicated multi-line
    # regex first and fall back to the helper.
    forbidden_functions = (
        "_tick_internal_body",
        "_build_ctx_for_tick",
        "_dispatch_strategies_at_tick",
    )
    multiline_def_re = {
        fn: re.compile(
            rf"^(?P<indent>[ \t]*)def\s+{re.escape(fn)}\b[\s\S]*?:\s*$",
            re.MULTILINE,
        )
        for fn in forbidden_functions
    }
    offenses: list[str] = []
    for fn_name in forbidden_functions:
        body = _extract_function_body(stripped, fn_name)
        if not body:
            # Fall back to multi-line-aware extraction.
            header_match = multiline_def_re[fn_name].search(stripped)
            if header_match:
                start = header_match.start()
                base_indent = len(header_match.group("indent"))
                remainder = stripped[start:].splitlines(keepends=True)
                # Skip the header lines until we close on ``:``
                # (the regex already closed there; start the body after)
                body_lines = [header_match.group(0) + "\n"]
                # Find the first line after the header that has indent
                # greater than base_indent — that begins the body.
                after_header = stripped[header_match.end():].splitlines(
                    keepends=True,
                )
                for line in after_header:
                    stripped_line = line.lstrip()
                    if not stripped_line:
                        body_lines.append(line)
                        continue
                    line_indent = len(line) - len(stripped_line)
                    if line_indent <= base_indent:
                        break
                    body_lines.append(line)
                body = "".join(body_lines)

        if not body:
            # The function is expected to exist; its absence is itself
            # a regression signal.
            offenses.append(f"{fn_name}: MISSING (expected to exist)")
            continue
        if "time.time_ns()" in body:
            offenses.append(f"{fn_name}: contains time.time_ns()")

    assert not offenses, (
        "Wall-clock read found in a replay-dispatch function body — "
        "replay dispatch must use the shared SimulationClock, never "
        "time.time_ns(). Offenses:\n  "
        + "\n  ".join(offenses)
        + "\nFix: move any wall-clock bridge to a module-scope helper "
        "(see ``_PaperLiveWallClock``) and gate it with an explicit "
        "``_paper_live_mode = True`` engine attribute so replay callers "
        "cannot fall through to it."
    )


def test_paper_bar_idx_matches_shared_clock_in_replay():
    """Under ``drive_paper_in_replay``, every ``generate()`` call observes
    ``arg_bar_idx == ctx.cache._clock.current_bar_idx`` AND
    ``arg_bar_idx == (clock.now_ns() - start_ns) // cadence_ns``.

    Rationale (M11 Stage-3 P1 — single clock authority): the paper
    engine historically read ``self.tick_counter`` as the dispatch
    ``bar_idx``. ``tick_counter`` is a monotonic persistence counter
    incremented at the END of each tick (so state.json records the
    next expected tick on crash-recovery), while the shared
    ``SimulationClock`` has already advanced at the START of the tick.
    The two disagree by exactly one bar — every time-anchored strategy
    decision (day-boundary gating, funding application, session filters)
    fires 1 bar later on paper than on backtest.

    This test drives a minimal synthetic fixture through
    ``drive_paper_in_replay`` with a tiny BaseStrategy that captures
    ``(arg_bar_idx, ctx.cache._clock.current_bar_idx,
    ctx.cache._clock.now_ns())`` on every ``generate()`` call, then
    asserts all three are mutually consistent. The assertion is tight
    on the clock/bar_idx/now_ns relationship — it does not test
    strategy output.
    """
    # Imports deferred until the test runs so collection stays cheap
    # and doesn't depend on a fully-wired M11 stack at import time.
    import tempfile
    from pathlib import Path as _Path

    import numpy as np
    import pandas as pd

    from v5.bar_spec import BarSpec
    from v5.data.streams import BarData, InstrumentId, Venue
    from v5.paper_config import PaperConfig
    from v5.paper_engine import PaperPortfolioEngine
    from v5.run_backtest import drive_paper_in_replay
    from v5.strategy_api import BaseStrategy, UniverseSignals

    # --- Minimal 2-day BTC 1h fixture ------------------------------
    n_bars = 48
    start_ns = pd.Timestamp("2026-02-01T00:00:00Z").value
    cadence_ns = 3_600 * 1_000_000_000  # 1h

    with tempfile.TemporaryDirectory() as tmp:
        root = _Path(tmp) / "fixture"
        (root / "perp" / "1h_cache").mkdir(parents=True, exist_ok=True)
        rng = np.random.default_rng(7)
        for token, base in [("BTCUSDT", 50_000.0)]:
            rows = []
            price = base
            for i in range(n_bars):
                ts_ns = start_ns + i * cadence_ns
                delta = rng.normal(0, base * 0.005)
                new_price = max(1.0, price + delta)
                rows.append({
                    "timestamp": pd.Timestamp(ts_ns),
                    "open": price,
                    "high": max(price, new_price) + abs(delta) * 0.5,
                    "low": min(price, new_price) - abs(delta) * 0.5,
                    "close": new_price,
                    "volume": 1e6,
                })
                price = new_price
            pd.DataFrame(rows).set_index("timestamp").to_parquet(
                root / "perp" / "1h_cache" / f"{token}_1h.parquet"
            )

        # --- Tiny strategy that records (arg_bar_idx, clock state) --
        class _ClockProbeStrategy(BaseStrategy):
            id = "clock_probe"
            strategy_id = "clock_probe"

            def __init__(self) -> None:
                self.observations: list[tuple[int, int, int]] = []

            def required_data(self):
                inst = InstrumentId(
                    symbol="BTCUSDT", venue=Venue.BINANCE, asset_class="perp",
                )
                from v5.data.streams import DataStream
                from v5.strategy_api import Subscription
                stream = DataStream(
                    instrument=inst,
                    data_class=BarData,
                    bar_spec=BarSpec.from_minutes(60),
                )
                return [Subscription(stream=stream)]

            def generate(self, ctx, bar_idx):
                shared_clock = getattr(
                    getattr(ctx, "cache", None), "_clock", None,
                )
                if shared_clock is not None:
                    self.observations.append((
                        int(bar_idx),
                        int(shared_clock.current_bar_idx),
                        int(shared_clock.now_ns()),
                    ))
                return UniverseSignals(bar_idx=bar_idx, signals={})

        strat = _ClockProbeStrategy()
        config = PaperConfig(
            strategies=[], capital=150_000.0, exchange="binance",
        )
        engine = PaperPortfolioEngine(config=config)
        drive_paper_in_replay(
            engine=engine,
            strategies=[strat],
            instruments=[InstrumentId(
                symbol="BTCUSDT", venue=Venue.BINANCE, asset_class="perp",
            )],
            start_ns=int(start_ns),
            end_ns=int(start_ns + n_bars * cadence_ns),
            fixture_root=root,
        )

        assert strat.observations, (
            "Probe strategy was never invoked in replay — drive_paper_in_replay "
            "did not reach strategy.generate. Clock-alignment invariant cannot "
            "be verified."
        )

        # Three-way consistency on every observation:
        #   arg_bar_idx == clock.current_bar_idx
        #   arg_bar_idx == (now_ns - start_ns) // cadence_ns
        mismatches: list[str] = []
        for i, (arg_idx, clk_idx, now_ns) in enumerate(strat.observations):
            derived_idx = (now_ns - int(start_ns)) // cadence_ns
            if arg_idx != clk_idx:
                mismatches.append(
                    f"obs#{i}: arg_bar_idx={arg_idx} but "
                    f"clock.current_bar_idx={clk_idx}"
                )
            if arg_idx != derived_idx:
                mismatches.append(
                    f"obs#{i}: arg_bar_idx={arg_idx} but "
                    f"(now_ns-start_ns)//cadence_ns={derived_idx} "
                    f"(now_ns={now_ns})"
                )

        assert not mismatches, (
            "Paper replay dispatch bar_idx is not aligned with the shared "
            "SimulationClock — single-authority invariant violated:\n  "
            + "\n  ".join(mismatches)
        )


# ─── M11 Stage-4 (task 8.9 cleanup) — drive-via-absence / monkey-patch /
#     wall-clock-in-replay invariants ──────────────────────────────────


def test_no_wall_clock_in_paper_replay_paths():
    """ADR-0001: replay-reachable paper dispatch functions must not read
    the wall clock (``time.time()`` / ``time.time_ns()`` /
    ``time.strftime(...)``) unconditionally.

    Rationale (M11 Stage-4 Change 1): ``_purge_expired_armed_orders``
    historically read ``time.time()`` as ``now_epoch``. Under
    ``drive_paper_in_replay`` that wall-clock value returns today's
    date (e.g. 2026-04-22) while the ``_filled_4h_windows`` container
    holds fixture timestamps from the test's simulated year (e.g.
    2024) — every window is flagged expired on the very first tick.
    The fix gates the ``time.time()`` read behind
    ``if self._clock is None:`` so replay derives ``now_epoch`` from
    the shared ``SimulationClock`` while paper-live (no clock attached)
    preserves wall-clock behavior.

    Stage-5 extension: ``time.strftime(...)`` falls in the same
    wall-clock-equivalent family. ``_tick_internal_body`` previously had
    ``timestamp = bar_timestamp or time.strftime(..., time.gmtime())``
    as its persistence/logging timestamp; in replay,
    ``drive_paper_in_replay`` passes no ``bar_timestamp`` so the
    fallback leaked wall-clock date into every tick record. Replay-
    dispatch functions must derive their timestamp from
    ``self._clock.now_ns()``; the unconditional ``time.strftime`` read
    is banned on the replay-dispatch path alongside ``time.time``.

    Four functions are checked:

    * ``_tick_internal_body``, ``_build_ctx_for_tick``,
      ``_dispatch_strategies_at_tick`` — replay-dispatch functions; they
      must contain NO wall-clock reads at all (``time.time``,
      ``time.time_ns``, or ``time.strftime``) — all time derives from
      ``self._clock`` / ``ctx.cache._clock``, with a
      ``self._clock is None`` gate preserving paper-live fallbacks.
    * ``_purge_expired_armed_orders`` — runs in both paper-live and
      replay; any ``time.time()`` / ``time.time_ns()`` call MUST lie
      inside an ``if ..._clock is None:`` branch. Unconditional reads
      are forbidden. ``time.strftime`` is permitted here — it formats
      the already-computed ``now_epoch`` (or fixture-anchored
      ``expired_at`` log fields) for human-readable logging, and the
      time authority has already been selected by the preceding
      ``_clock is None`` branch.

    Enforcement walks the stripped (docstrings + ``#`` comments removed)
    function body; regex-extracts the executable code only.
    """
    src = _read("v5/paper_engine.py")
    stripped = _strip_docstrings_and_comments(src)

    replay_only_functions = (
        "_tick_internal_body",
        "_build_ctx_for_tick",
        "_dispatch_strategies_at_tick",
    )
    multiline_def_re = {
        fn: re.compile(
            rf"^(?P<indent>[ \t]*)def\s+{re.escape(fn)}\b[\s\S]*?:\s*$",
            re.MULTILINE,
        )
        for fn in replay_only_functions + ("_purge_expired_armed_orders",)
    }

    def _body_of(fn_name: str) -> str:
        # Use the multi-line-aware regex directly — ``_extract_function_body``
        # under-counts the base indent when the regex's ``\s*`` greedily
        # swallows the preceding newline, making the helper walk past the
        # next sibling method. The multi-line regex pins indent to
        # ``[ \t]*`` so the header indent is measured correctly.
        header_match = multiline_def_re[fn_name].search(stripped)
        if not header_match:
            return ""
        base_indent = len(header_match.group("indent"))
        after_header = stripped[header_match.end():].splitlines(
            keepends=True,
        )
        body_lines = [header_match.group(0) + "\n"]
        for line in after_header:
            stripped_line = line.lstrip()
            if not stripped_line:
                body_lines.append(line)
                continue
            line_indent = len(line) - len(stripped_line)
            if line_indent <= base_indent:
                break
            body_lines.append(line)
        return "".join(body_lines)

    offenses: list[str] = []
    # The replay-dispatch bodies must not textually contain any of these
    # wall-clock reads as unconditional fallbacks. ``time.strftime(...)``
    # is included per the Stage-5 rationale in the docstring: the
    # ``_tick_internal_body`` persistence timestamp fallback was a
    # wall-clock leak class indistinguishable from ``time.time()``.
    # If a replay-dispatch function legitimately formats a timestamp
    # derived from ``self._clock``, it routes through
    # ``pd.Timestamp(..., unit="ns", tz="UTC").strftime(...)`` (a
    # method call on a clock-derived ``Timestamp``), not the
    # module-level ``time.strftime`` function — the grep is narrowed
    # to ``time.strftime`` so the clock-derived path is unaffected.
    #
    # ``time.strftime`` is allowed INSIDE an ``if self._clock is None:``
    # branch (paper-live fallback) using the same gate-aware scan as
    # ``_purge_expired_armed_orders`` below. ``time.time`` and
    # ``time.time_ns`` remain unconditionally banned in these bodies —
    # the replay-dispatch path must never derive ``now`` from the wall
    # clock, even when the sim clock is absent.
    gate_re = re.compile(
        r"\bif\s+[^\n]*_clock[^\n]*\bis\s+None\b",
    )
    unconditional_forbidden = ("time.time()", "time.time_ns()")
    gated_forbidden = ("time.strftime",)
    for fn_name in replay_only_functions:
        body = _body_of(fn_name)
        if not body:
            offenses.append(f"{fn_name}: MISSING (expected to exist)")
            continue
        for pattern in unconditional_forbidden:
            if pattern in body:
                offenses.append(
                    f"{fn_name}: contains {pattern} "
                    f"(unconditional wall-clock read)"
                )
        # Gate-aware scan for ``time.strftime`` — allowed only inside an
        # ``if ... _clock is None:`` branch (paper-live fallback).
        in_gate = False
        gate_indent = -1
        for line in body.splitlines():
            stripped_line = line.lstrip()
            if not stripped_line:
                continue
            line_indent = len(line) - len(stripped_line)
            if in_gate and line_indent <= gate_indent:
                in_gate = False
                gate_indent = -1
            if gate_re.search(stripped_line):
                in_gate = True
                gate_indent = line_indent
                continue
            for pattern in gated_forbidden:
                if pattern in stripped_line and not in_gate:
                    offenses.append(
                        f"{fn_name}: {pattern} read outside "
                        f"``if ..._clock is None:`` branch — "
                        f"line fragment: {stripped_line!r}"
                    )

    # _purge_expired_armed_orders: any wall-clock read must be gated on
    # ``self._clock is None``. Scan for unconditional (top-of-body) reads.
    purge_body = _body_of("_purge_expired_armed_orders")
    if not purge_body:
        offenses.append(
            "_purge_expired_armed_orders: MISSING (expected to exist)"
        )
    else:
        # Tokenize body line-by-line; track whether we're inside an
        # ``if ... _clock is None:`` (or equivalent) branch by indent.
        gate_re = re.compile(
            r"\bif\s+[^\n]*_clock[^\n]*\bis\s+None\b",
        )
        wall_re = re.compile(r"\btime\.time(?:_ns)?\s*\(")
        in_gate = False
        gate_indent = -1
        for line in purge_body.splitlines():
            stripped_line = line.lstrip()
            if not stripped_line:
                continue
            line_indent = len(line) - len(stripped_line)
            if in_gate and line_indent <= gate_indent:
                in_gate = False
                gate_indent = -1
            if gate_re.search(stripped_line):
                in_gate = True
                gate_indent = line_indent
                continue
            if wall_re.search(stripped_line) and not in_gate:
                offenses.append(
                    f"_purge_expired_armed_orders: wall-clock read "
                    f"outside ``if ..._clock is None:`` branch — "
                    f"line fragment: {stripped_line!r}"
                )
                break

    assert not offenses, (
        "Wall-clock reads found on a replay-reachable paper path — "
        "ADR-0001 requires replay dispatch to derive time from the "
        "shared SimulationClock. Offenses:\n  "
        + "\n  ".join(offenses)
    )


def test_orchestrator_does_not_mutate_strategy_required_data():
    """ADR-0002 §2: the strategy protocol is read-only from the
    orchestrator.

    Rationale (M11 Stage-4 Change 4, reviewer RF-4): earlier stages
    had ``v5/run_backtest.py::_subscribe_strategies_with_default``
    monkey-patch ``strat.required_data = lambda _subs=subs: list(_subs)``
    so ``register_strategy_cadences`` would see the orchestrator-
    synthesized subs. That pattern mutates the strategy's method table
    — any future caller (tests, paper runtime) that reads
    ``strat.required_data()`` on the same instance sees subs the
    strategy never declared. The correct path is the explicit
    ``effective_subs_by_strategy`` override on
    :meth:`DataEngine.register_strategy_cadences`, which leaves the
    strategy's public contract untouched.

    This invariant greps the stripped ``run_backtest.py`` source for any
    assignment to ``strat.required_data`` or ``strategy.required_data``
    — those are the specific mutations the reviewer flagged.
    """
    src = _read("v5/run_backtest.py")
    stripped = _strip_docstrings_and_comments(src)
    forbidden_assignments = (
        "strat.required_data =",
        "strategy.required_data =",
    )
    offenses = [pat for pat in forbidden_assignments if pat in stripped]
    assert not offenses, (
        "Orchestrator mutates strategy.required_data — ADR-0002 §2 "
        "requires declarative-only data-dependency contracts. "
        "Offending assignments: " + ", ".join(repr(p) for p in offenses)
        + ".\nFix: pass the effective subs through the "
        "``effective_subs_by_strategy`` override on "
        "``DataEngine.register_strategy_cadences``."
    )


def test_tick_internal_body_timestamp_honors_sim_clock():
    """ADR-0001 Stage-5 — when a shared ``SimulationClock`` is attached
    on ``self._clock``, ``_tick_internal_body`` must derive its
    persistence/logging ``timestamp`` from it (not from wall-clock).

    Rationale: ``test_no_wall_clock_in_paper_replay_paths`` is a
    negative grep (ban ``time.strftime`` outside the
    ``_clock is None`` gate). A future refactor could satisfy that
    grep by deleting the wall-clock fallback entirely AND leaving no
    clock-derived path, silently reintroducing the replay wall-clock
    leak via a new code shape. This positive invariant asserts the
    complementary contract: the function body textually contains both
    (a) a ``_clock.now_ns()`` read, and (b) a ``pd.Timestamp(..., unit=
    "ns", tz="UTC").strftime(...)`` formatting of that clock-derived
    value. Together with the negative grep in
    ``test_no_wall_clock_in_paper_replay_paths``, this pins the
    sim-clock-derived path as the canonical timestamp source for
    replay ticks.
    """
    src = _read("v5/paper_engine.py")
    stripped = _strip_docstrings_and_comments(src)
    multiline_def_re = re.compile(
        r"^(?P<indent>[ \t]*)def\s+_tick_internal_body\b[\s\S]*?:\s*$",
        re.MULTILINE,
    )
    header_match = multiline_def_re.search(stripped)
    assert header_match, (
        "_tick_internal_body not found in v5/paper_engine.py — "
        "expected to exist (paper replay dispatch entry)"
    )
    base_indent = len(header_match.group("indent"))
    after_header = stripped[header_match.end():].splitlines(keepends=True)
    body_lines: list[str] = [header_match.group(0) + "\n"]
    for line in after_header:
        stripped_line = line.lstrip()
        if not stripped_line:
            body_lines.append(line)
            continue
        line_indent = len(line) - len(stripped_line)
        if line_indent <= base_indent:
            break
        body_lines.append(line)
    body = "".join(body_lines)

    # Positive: clock read appears in the body.
    assert "_clock.now_ns()" in body or "shared_clock.now_ns()" in body, (
        "_tick_internal_body body contains no ``_clock.now_ns()`` / "
        "``shared_clock.now_ns()`` read — ADR-0001 Stage-5 requires the "
        "persistence/logging timestamp to derive from the shared "
        "SimulationClock in replay. Any refactor must preserve that "
        "clock-derived path."
    )
    # Positive: clock-derived value is formatted as an ISO timestamp
    # via a ``pd.Timestamp(..., unit="ns", tz="UTC").strftime(...)``
    # call (the clock-derived formatter).
    assert re.search(
        r"pd\.Timestamp\([^)]*unit\s*=\s*[\"']ns[\"'][^)]*\)\.strftime\(",
        body,
    ), (
        "_tick_internal_body body contains no "
        "``pd.Timestamp(..., unit='ns', tz='UTC').strftime(...)`` call — "
        "ADR-0001 Stage-5 requires the clock-derived now_ns value to be "
        "formatted into the persistence/logging timestamp. The grep-based "
        "negative invariant cannot alone catch a refactor that removes the "
        "clock-derived formatter while keeping the ``_clock is None`` gate."
    )


def test_handle_disappeared_tokens_deleted():
    """ADR-0002 §3: data staleness is a cache concern (gap detector),
    not a signal-set sweep.

    Rationale (M11 Stage-4 Change 2): the legacy
    ``_handle_disappeared_tokens`` method (plus its native wrapper
    ``_handle_disappeared_tokens_native``) walked each tick's
    ``signals_by_sid.keys()`` and force-closed any position whose token
    had dropped out — a "drive-via-absence" antipattern (Lopez de Prado,
    *Advances in Financial Machine Learning* §5.3): a strategy
    quietly emitting no signal this bar is observationally identical to
    a delisting, and the sweep cannot tell them apart. The correct home
    for staleness detection is the ``MarketDataCache`` gap detector,
    which signals missing bars as first-class events; positions then
    close through the normal exit path. The sweep is deleted outright
    rather than gated behind a replay guard.

    This invariant greps stripped ``v5/paper_engine.py`` source for any
    executable reference to ``_handle_disappeared_tokens`` — both the
    method name and its ``_native`` wrapper share the common prefix.
    """
    src = _read("v5/paper_engine.py")
    stripped = _strip_docstrings_and_comments(src)
    assert "_handle_disappeared_tokens" not in stripped, (
        "`_handle_disappeared_tokens` still appears in executable code "
        "of v5/paper_engine.py — ADR-0002 §3 requires deletion. "
        "Data-staleness force-closes belong to the cache gap detector, "
        "not a signal-set sweep."
    )
