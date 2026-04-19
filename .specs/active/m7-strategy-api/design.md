# M7 Design — Unified Strategy API + Carryover Closeout

**Phase**: 2 (Design). Read-only exploration + interface crystallisation.

**Inputs**: `brief.md` (23 ACs, 8 resolved gates G1-G8, 100-140h budget). Phase-2 audits of v4/engine.py strategy loader, v5/signals.py + v4/portfolio_signals.py, v5/paper_engine.py (4557 LOC) + v5/run_paper_multi.py + v4/price_monitor.py (831 LOC) + v4/live_fetcher.py (606 LOC), v5/validation.py (WF outer loop), reference strategies (s513/s523c/s524m).

**Scope anchor**: the brief's 23 ACs are the contract. This design specifies module layout, concrete APIs, the 8-site migration plan, walk-forward extraction mechanics, strategy-loader rewrite, 13 pre-existing failures root-cause map, implementation wave ordering.

---

## 1. Module Layout

```
v5/
├── strategy_api.py                 # Strategy Protocol + base class + @runtime_checkable
├── universe_context.py             # UniverseContext + ctx.data / ctx.portfolio / ctx.clock / ctx.orders namespaces
├── indicators.py                   # Pull-based memoized indicator cache + typed param contract
├── regimes.py                      # Optional regime utility, memoized per (bar_idx, ctx_id)
├── validation.py                   # MODIFIED — WF outer loop, fresh Strategy per fold
├── signals.py                      # REPLACES v4/signals.py + v4/portfolio_signals.py (unified)
├── strategy_loader.py              # REPLACES v4/engine.py::_load_strategy_fn (adds AST scan)
├── fill.py                         # Fill dataclass with FIX triple (ClOrdID + OrderID + ExecID)
├── paper_engine.py                 # MODIFIED — 8-site flag dispatch (Task 17)
├── run_paper_multi.py              # MODIFIED — runner lock path migration (state/v4→v5)
├── orders.py                       # M5-shipped — extended with FIX wire serializers (AC-O2)
└── data/
    ├── engine.py                   # M6-shipped — extended with strategy API hooks (arm/arm_bracket)
    └── instruments.py              # M6-shipped — VenueCapabilities gains supported_transport_modes

v5/tests/
├── test_m7_strategy_protocol.py    # AC-S1 — Protocol + @runtime_checkable conformance
├── test_m7_universe_context.py     # AC-S2 — namespace split + read-only isolation
├── test_m7_indicators.py           # AC-S3 — pull-based memoization + cache_key()
├── test_m7_signals_unified.py      # AC-S6 — unified signals.py, portfolio_signals.py deleted
├── test_m7_validation_wf.py        # AC-V1 — WF extraction, fresh Strategy per fold, no masks in signals
├── test_m7_ast_scan.py             # AC-V2 — strategy loader rejects banned clock calls
├── test_m7_arm_bracket.py          # AC-O1 — arm + arm_bracket factories
├── test_m7_fix_wire.py             # AC-O2 — OrdStatus/TriggerType/LegStatus serializers
├── test_m7_reject_unwind.py        # AC-O3 — post-fill _order_reject_event unwind
├── test_m7_venue_order_id.py       # AC-O4 — venue_order_id populated on ack
├── test_m7_fill_triple.py          # AC-O5 — Fill dataclass with ClOrdID/OrderID/ExecID
├── test_m7_paper_8site.py          # AC-P1 — flag=OFF byte-identity + flag=ON wiring
├── test_m7_runner_swap.py          # AC-P2 — lock file migration v4→v5
├── test_m7_lifecycle_hooks.py      # All 15 callbacks fire at correct phase
├── test_m7_s524m_parity.py         # AC-S10 — 0.5% parity with v4/s524m
├── test_m7_pre_existing_13.py      # AC-H1 — 13 cleared failures
└── shadow_replay_24h.py            # AC-P3 — runs against real 24h recording
```

**File counts**: ~14 new test files, ~4 new source files, ~6 modified files. Sizing expectations per file target ≤400 LOC; strategy_loader ~500 LOC (AST scan is substantial); paper_engine edits span 8 sites but each is ≤20 LOC delta.

---

## 2. Key Interfaces

### 2.1 `v5/strategy_api.py`

```python
from typing import Protocol, runtime_checkable, Optional, Literal

@runtime_checkable
class Strategy(Protocol):
    """Unified Strategy contract — G2 decision: 15-callback FIX-aligned surface.

    Protocol declared @runtime_checkable; AC-S1 test asserts
    isinstance(strategy, Strategy) AND mypy --strict passes.
    """

    # Lifecycle
    def on_start(self, portfolio_config) -> None: ...
    def on_stop(self, reason: str) -> None: ...
    def on_reset(self) -> None: ...  # WF fold boundary

    # Data declaration (forward-compat with M6)
    def required_data(self) -> list["Subscription"]: ...

    # Signal generation
    def generate(self, ctx: "UniverseContext", bar_idx: int) -> "UniverseSignals": ...

    # Per-position per-bar checks
    def check_scale(self, pos, bar_ctx) -> Optional["ScaleAction"]: ...
    def check_exit(self, pos, bar_ctx) -> Optional["ExitCheck"]: ...
    def filter_entry(self, candidate, bar_ctx) -> bool: ...

    # FIX-aligned execution event callbacks (M5 OrdStatus states)
    def on_order_accepted(self, order) -> None: ...
    def on_order_rejected(self, order, reason: str) -> None: ...
    def on_order_cancelled(self, order, reason: str) -> None: ...
    def on_order_triggered(self, order) -> None: ...           # ARMED → TRIGGERED
    def on_order_partial_fill(self, order, fill: "Fill") -> None: ...
    def on_order_filled(self, order, fill: "Fill") -> None: ...  # final fill
    def on_order_expired(self, order) -> None: ...

    # Position lifecycle
    def on_position_opened(self, position) -> None: ...
    def on_position_changed(self, position, delta) -> None: ...
    def on_position_closed(self, closed_trade) -> None: ...

    # Introspection
    def view_state(self) -> dict: ...


class BaseStrategy:
    """Default no-op implementations for all 15 callbacks.

    Strategies inherit this to override only what they need. Default impls:
    - lifecycle/event hooks: no-op
    - check_scale/check_exit: return None
    - filter_entry: return True
    - view_state: return {}
    - required_data: return [] (strategy declares nothing; engine uses defaults)
    - generate: MUST be overridden (raise NotImplementedError)
    """
    # ... (concrete no-op defaults)
```

### 2.2 `v5/universe_context.py`

G7 decision: namespace split (not a god-object). Nautilus-style separation.

```python
@dataclass(frozen=True, slots=True)
class UniverseContext:
    """Read-only namespace container passed to Strategy.generate().

    AC-S2: arrays returned via `ctx.data.per_token(...)` are read-only views
    (arr.setflags(write=False)). Strategy opts into mutation via
    ctx.mutable_copy(arr) when intent is explicit.
    """
    data: "DataView"           # ctx.data.per_token(), ctx.data.tokens
    portfolio: "PortfolioView" # ctx.portfolio.equity, .open_positions
    clock: "Clock"             # ctx.clock.now_ns()
    orders: "OrderFactoryView" # ctx.orders.arm(), .arm_bracket()
    fold_id: Optional[int] = None        # AC-V1 — for ensemble strategies
    fold_window: Optional[tuple[int, int]] = None

    def mutable_copy(self, arr):
        """Explicit opt-in mutation — returns np.copy()."""
        return arr.copy()


class DataView:
    def per_token(self, symbol: str, venue: Optional[Venue] = None) -> "TokenView":
        """Returns TokenView for (symbol, venue). If venue=None, uses default
        venue from portfolio config. Cross-venue strategies pass explicit venue."""

    @property
    def tokens(self) -> list[InstrumentId]:
        """Bar-relative — queries InstrumentRegistry at current bar_idx.
        Delisted tokens absent; new listings present."""


class TokenView:
    """Per-token indicator + bar access. Arrays returned are read-only views."""
    close: np.ndarray   # read-only, writeable=False
    high: np.ndarray
    low: np.ndarray
    open: np.ndarray
    volume: np.ndarray

    def ema(self, n: int, col: str = "close") -> float: ...    # scalar at bar_idx
    def rsi(self, n: int = 14) -> float: ...
    def atr(self, n: int = 14) -> float: ...
    # ... stdlib indicators return SCALAR at bar_idx (not array)

    def custom(self, fn, **params) -> Any:
        """Custom indicator. `fn` must be a named function (not lambda).
        Non-scalar params require fn.cache_key() method."""


class PortfolioView:
    equity: float
    open_positions: list["Position"]
    closed_trades_today: list["ClosedTrade"]


class OrderFactoryView:
    """Strategy-facing order construction — G3 decision: both factories."""

    def arm(
        self,
        symbol: str,
        direction: int,
        size: float,
        trigger: "TriggerType",
        trigger_price: float,
        **kwargs,
    ) -> "Order":
        """Single-leg order (AC-O1). Publishes to M5 bus per pre-fill atomic path."""

    def arm_bracket(
        self,
        entry_spec: dict,
        sl_spec: dict,
        tp_spec: dict,
    ) -> "Order":
        """Three-leg bracket (AC-O1).

        Constructs Order(
            legs=[entry_leg, sl_leg, tp_leg],
            fill_policy=LegFillPolicy.OTO_BRACKET,
            contingency=ContingencyType.OTO,
        ).

        Validation (raises ValueError at factory time):
        - SL price on wrong side of entry (below for long, above for short) —
          ONLY enforced when entry has a fixed price (LIMIT/STOP). MARKET
          entries defer SL/TP side validation to fill time: `_order_reject_event`
          re-validates against `fill.last_px` and unwinds if mis-sided
          (FIX reviewer M1 — defer-to-fill semantics).
        - TP price on wrong side of entry (same deferral for MARKET entries)
        - Size mismatch across legs (entry.size != sl.size != tp.size)
        - Invalid trigger type for SL/TP (must be STOP_LOSS / TAKE_PROFIT variants)
        """
```

### 2.3 `v5/indicators.py`

G5 decision: `.cache_key()` method for custom indicators with non-scalar params; stdlib indicators restricted to scalar params only.

```python
class IndicatorCache:
    """Pull-based memoized cache — AC-S3.

    Cache key: (token, fn.__qualname__, frozen_params_tuple, bar_idx)

    Stdlib indicator params: scalar (int/float/str) only. Non-scalar rejected
    at call time with clear TypeError.

    Custom indicators: MUST implement .cache_key() -> tuple returning hashable
    params snapshot. Lambdas/closures rejected at registration:
        ValueError("indicator must be a named function — lambda/closure has
                   unstable __qualname__")
    """

    def compute(
        self,
        token: str,
        fn: Callable,
        bar_idx: int,
        **params,
    ) -> Any:
        self._validate_fn(fn)       # rejects lambda/closure
        cache_key_params = self._freeze_params(fn, params)
        key = (token, fn.__qualname__, cache_key_params, bar_idx)
        if key in self._cache:
            return self._cache[key]
        result = fn(token, bar_idx, **params)
        self._cache[key] = result
        return result

    def clear(self) -> None:
        """Called on Strategy.on_reset() for WF fold boundary."""
        self._cache.clear()

    def _validate_fn(self, fn) -> None:
        qname = fn.__qualname__
        if "<locals>" in qname or "<lambda>" in qname:
            raise ValueError(
                f"indicator {qname} must be a named function; "
                f"lambda/closure has unstable __qualname__ → cache collisions"
            )

    def _freeze_params(self, fn, params: dict) -> tuple:
        """Convert params dict to hashable tuple.

        For stdlib indicators: all params must be scalar (int/float/str/bool).
        For custom indicators with .cache_key() method: call fn.cache_key(**params)
        to let the function freeze its own params (handles numpy arrays,
        dataclass params, etc.).

        Quant reviewer H2 fix — defensive hash: both paths run hash(result) to
        catch cache_key() returning non-hashable (e.g., dict or list instead of
        tuple). Raises at call site with clear error, not deep inside dict insert.
        """
        if hasattr(fn, "cache_key"):
            result = fn.cache_key(**params)
            try:
                hash(result)
            except TypeError:
                raise TypeError(
                    f"indicator {fn.__qualname__}.cache_key() must return hashable; "
                    f"got {type(result).__name__} — use tuple of scalars/frozensets"
                )
            return result
        # Stdlib path — scalar-only
        frozen = []
        for k, v in sorted(params.items()):
            if not isinstance(v, (int, float, str, bool)):
                raise TypeError(
                    f"indicator {fn.__qualname__} param {k}={v!r} is non-scalar; "
                    f"implement fn.cache_key(**params) for custom handling"
                )
            frozen.append((k, v))
        return tuple(frozen)
```

### 2.4 `v5/fill.py` + ExecType

AC-O5 — Fill carries FIX triple + ExecType. Per FIX-reviewer H1: ExecType(150) is independent of OrdStatus (current state vs what-this-report-is-about). Without it, strategies can't distinguish `TRADE_CANCEL(H)` reversal from a fresh partial.

```python
# v5/orders.py extension (existing M5 module)
class ExecType(str, Enum):
    """FIX ExecType(150) — what this exec report describes.

    FIX-standard values (reviewer H1 fix): `TRADE = "F"` is the single FIX
    value for fills. Partial vs final is disambiguated via OrdStatus(39):
    OrdStatus=1 (Partial) vs OrdStatus=2 (Filled). The Fill dataclass carries
    both exec_type AND can be cross-referenced against Order.status — strategies
    use on_order_partial_fill vs on_order_filled callback dispatch to
    distinguish at the Strategy layer.
    """
    NEW = "0"               # order accepted by venue
    TRADE = "F"             # fill (partial or final — use OrdStatus to distinguish)
    CANCELED = "4"
    REJECTED = "8"
    TRIGGERED = "L"
    EXPIRED = "C"
    TRADE_CANCEL = "H"      # cancel a prior fill (venue bust)
    TRADE_CORRECT = "G"     # correct a prior fill (venue amendment)
    ORDER_STATUS = "I"      # status response (no trade)


# v5/fill.py
@dataclass(frozen=True, slots=True)
class Fill:
    """FIX-aligned exec report payload.

    Carries the 3 FIX IDs + ExecType + TransactTime + qty/px details. Passed
    to Strategy.on_order_filled/on_order_partial_fill and surfaced via
    on_order_accepted/rejected/cancelled/expired on non-fill exec types.
    """
    cl_ord_id: str              # FIX ClOrdID(11) — caller-originated; stable
    venue_order_id: str | None  # FIX OrderID(37) — venue-assigned
    exec_id: str | None         # FIX ExecID(17) — unique per exec report
    exec_type: ExecType         # FIX ExecType(150) — report kind
    transact_time: int          # FIX TransactTime(60) — epoch ns
    last_qty: float             # FIX LastQty(32) — filled size this report
    last_px: float              # FIX LastPx(31) — filled price this report
    cum_qty: float               # FIX CumQty(14) — total filled so far
    leaves_qty: float           # FIX LeavesQty(151) — remaining open
    avg_px: float               # FIX AvgPx(6) — VWAP across partials (FIX reviewer M1 fix)
```

### 2.4a `v5/orders.py` — FIX wire serializer locations (Quant reviewer H3 fix)

All FIX wire serializers live as methods on the M5-shipped enums in `v5/orders.py` — NOT split across files. Serializer signatures pinned here:

```python
class OrderStatus(Enum):  # M5-shipped, add serializer
    def to_fix_ordstatus(self) -> str:
        """Return FIX OrdStatus(39) string.
        ARMED/TRIGGERED/RELEASED → 'A' + custom tag 9001/9002/9003 (G8 decision).
        """

class TriggerType(Enum):  # M5-shipped, add serializers
    def to_fix_trigger_type(self) -> str:
        """Return FIX TriggerType(1100) string."""
    def to_fix_trigger_price_direction(self) -> str:
        """Return FIX TriggerPriceDirection(1109) string."""

class LegStatus(Enum):  # M5-shipped, add serializer
    def to_fix_ordstatus(self) -> str:
        """Return FIX OrdStatus(39) string for this leg's state."""
```

### 2.5 `v5/strategy_loader.py`

Replaces v4/engine.py::_load_strategy_fn (lines 1033-1084). Adds AST scan (AC-V2).

```python
class StrategyLoader:
    """AST-scanning strategy loader (AC-V2).

    Replaces v4/engine.py::_load_strategy_fn. Cache: dict[str, Strategy class].
    Thread-safe via lock.
    """

    _BANNED_ATTRIBUTES: frozenset[tuple[str, str]] = frozenset({
        ("time", "time"),
        ("time", "time_ns"),
        ("time", "monotonic"),
        ("time", "perf_counter"),
        ("datetime", "now"),
        ("datetime", "utcnow"),
        ("datetime", "today"),
        ("pd", "Timestamp.now"),
        ("np", "datetime64"),   # for np.datetime64('now') — detected via literal 'now' arg
    })

    _BANNED_BARE_NAMES: frozenset[str] = frozenset({
        # Names that after `from time import X` would be directly callable
        "time", "time_ns", "monotonic", "perf_counter",
    })

    # FIX reviewer M3: threading.Event.wait(timeout=) and
    # concurrent.futures.wait(timeout=) read wall-clock for timeout accounting.
    # In a backtest's TestClock world, a wait(timeout=5.0) blocks real seconds
    # even if TestClock has advanced hours. Same class of determinism bug.
    # WARN-only (not block) — some legitimate uses exist.
    _WARN_ATTRIBUTES: frozenset[tuple[str, str]] = frozenset({
        ("Event", "wait"),          # threading.Event
        ("wait", ""),               # concurrent.futures.wait (bare name via import)
    })

    def load(self, strategy_id: str) -> type[Strategy]:
        """Load strategy module by id. AST-scan for banned clock calls
        BEFORE importlib.exec_module — if scan fails, strategy rejected
        at __init__ time (never instantiated).
        """
        path = self._resolve_strategy_path(strategy_id)
        source = path.read_text()
        self._ast_scan(path, source)
        module = self._exec_module(path, source)
        return self._extract_strategy_class(module)

    def _ast_scan(self, path: Path, source: str) -> None:
        tree = ast.parse(source)
        for node in ast.walk(tree):
            self._check_attribute_access(path, node)
            self._check_bare_name_call(path, node, tree)
            self._check_numpy_datetime_now(path, node)

    def _check_attribute_access(self, path, node) -> None:
        """Detect `time.time()` / `datetime.now()` / `pd.Timestamp.now()`."""
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            attr_chain = self._walk_attribute(node.func)
            # e.g. attr_chain = ("time", "time") or ("pd", "Timestamp", "now")
            for banned in self._BANNED_ATTRIBUTES:
                if attr_chain[-len(banned):] == banned:
                    raise StrategyLoadError(
                        f"{path}: banned clock call {'.'.join(attr_chain)}() "
                        f"at line {node.lineno}. Use ctx.clock.now_ns() instead."
                    )

    def _check_bare_name_call(self, path, node, tree) -> None:
        """Detect `from time import time; time()` — bare-name usage."""
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in self._BANNED_BARE_NAMES:
                if self._has_banned_import(tree, node.func.id):
                    raise StrategyLoadError(
                        f"{path}: banned bare-name clock call "
                        f"{node.func.id}() at line {node.lineno}. "
                        f"Use ctx.clock.now_ns()."
                    )

    # Additional: check for module-level mutable state (AC-V1 MED)
    def _check_module_mutable_state(self, path, tree) -> None:
        """Flags top-level list/dict/set literal assignments as potential
        fold-contamination sources. Strategies MUST keep mutable state on
        self, not at module level. See Phase-2 audit: s524m_nofilter.py has
        HIGH-RISK _composite_cache + _aligned_cache at module level."""
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        # Warn (not fail) — some strategies have acceptable
                        # read-only module state (e.g., s523c_growth.TOKEN_BLACKLIST).
                        # AST scan logs WARN; reviewer decides.
                        if isinstance(node.value, (ast.Dict, ast.List, ast.Set)):
                            _log.warning(
                                "%s: module-level mutable %s at line %d — "
                                "potential fold contamination",
                                path, type(node.value).__name__, node.lineno,
                            )
```

### 2.6 Callback Dispatch Order (FIX reviewer H2)

When a single engine event produces cascading callbacks, dispatch is strict-ordered:

**Bracket entry fill** (e.g., entry fills, SL/TP arm on fill):
```
on_order_partial_fill(entry, partial_fill)  × N  # one per partial
on_order_filled(entry, final_fill)
on_position_opened(new_position)
on_order_accepted(sl_order)       # SL leg becomes ARMED
on_order_accepted(tp_order)       # TP leg becomes ARMED
on_order_triggered(sl_order)      # if SL armed-on-entry (rare)
on_order_triggered(tp_order)      # if TP armed-on-entry (rare)
```

**SL hit on open position**:
```
on_order_triggered(sl_order)      # ARMED → TRIGGERED
on_order_partial_fill(sl_order, ...) × N
on_order_filled(sl_order, final_fill)
on_order_cancelled(tp_order, "oco_sibling_filled")   # OCO cascade
on_position_closed(closed_trade)
```

**Venue reject post-fill (AC-O3 unwind)**:
```
on_order_rejected(leg_order, "post_fill_unwind:<venue_reason>")
# engine-published reversal order:
on_order_accepted(reversal_order)
on_order_filled(reversal_order, reversal_fill)
on_position_changed(position, delta)  # if partial unwind; on_position_closed if full
```

Error in one callback MUST NOT skip subsequent callbacks in the cascade (AC-S5 containment). Dispatch order is stable across runs — tested via AC-Lifecycle.

### 2.7 AC-S5 Error Containment — Operationalization (Quant reviewer H/M1)

"Treated as no-op for that call" operationalized at engine level:

| Method | On exception |
|---|---|
| `on_start` / `on_reset` | **FAIL-FAST**: engine aborts run; exception propagates |
| `on_stop` | logged; shutdown continues |
| `generate(ctx, bar_idx)` | logged; engine substitutes `EMPTY_SIGNALS = UniverseSignals(tokens=[], signals={})`; strategy.exception_counter += 1 |
| `check_scale(pos, ctx)` | logged; treated as `return None`; strategy.exception_counter += 1 |
| `check_exit(pos, ctx)` | logged; treated as `return None`; strategy.exception_counter += 1 |
| `filter_entry(candidate, ctx)` | logged; treated as `return True` (fail-open for entries — same as no filter); strategy.exception_counter += 1 |
| `on_order_*` / `on_position_*` | logged; dispatch continues to siblings; strategy.exception_counter += 1 |
| `view_state` | logged; dashboard shows `{"error": "view_state raised"}` |

**Observability**: `strategy.exception_counter` is exposed on `Strategy.view_state()` by default (dashboards can display); engine logs include strategy_id + method + exception. If `exception_counter > threshold` in a single run, engine publishes `StrategyQuarantined` event on MessageBus.

### 2.8 Implementation Decisions — Phase-4 Pinned (final-review sweep)

Three hazards were flagged in the final review as "implementer discretion". Per the design-over-code meta-rule (M5-M10), these decisions belong in the design, not in Phase-4 head-scratching. Pinned here:

#### ID1 — `Order.venue_order_id` mutation on frozen M5 Order

**Hazard**: M5 shipped `Order` as `@dataclass(frozen=True, slots=True)` with 101 passing tests on the frozen invariant. Task 13 (AC-O4) writes `venue_order_id` post-arm on ack.

**Decision**: use `object.__setattr__(order, "venue_order_id", venue_id)` at the single ack-time write site. Do NOT break `frozen=True`. Do NOT use a side-table (breaks encapsulation — callers should be able to ask the Order for its venue id directly).

**Rationale**: Nautilus uses the same pattern for post-construction mutation on otherwise-frozen events. Preserves M5's structural invariant for all other fields; creates exactly one documented escape hatch at `BinanceWSClient.on_venue_ack` / `BinanceRESTClient.on_venue_ack`. The test `test_m7_venue_order_id.py::test_order_has_venue_order_id_field` only checks annotations so the mechanism is free.

**Implementation**:
```python
# v5/data/clients/binance_ws.py
class BinanceWSClient:
    def on_venue_ack(self, event: dict, order: "Order") -> None:
        venue_id = event.get("order_id") or event.get("i")  # FIX OrderID(37)
        if venue_id is None:
            raise ValueError(f"ack missing OrderID(37): {event}")
        object.__setattr__(order, "venue_order_id", str(venue_id))
```

#### ID2 — `UniverseContext` frozen vs test-harness mutation

**Hazard**: Design §2.2 declares `UniverseContext` as `@dataclass(frozen=True, slots=True)` but tests call `ctx.run_full_lifecycle()`, `ctx.orders.arm(...)`, `ctx.orders.simulate_fill(...)` — methods that mutate state.

**Decision**: **Keep `frozen=True` on UniverseContext**. The container is structurally immutable (strategies cannot re-bind `ctx.data` to a different DataView mid-run). The inner view objects (`DataView`, `PortfolioView`, `OrderFactoryView`) are NOT frozen — they hold mutable state (bar cursor, order book, recorded events). Calling `ctx.orders.arm(...)` invokes a method on the inner mutable `OrderFactoryView`; `ctx.orders` itself never re-binds.

**Rationale**: `frozen=True` on the outer container is load-bearing for `id(ctx)`-based memoization (AC-Reg2). If the container were re-bindable, `id(ctx_a) == id(ctx_b)` could change across calls. Inner-mutable + outer-frozen is Nautilus's pattern.

**Implementation**:
```python
@dataclass(frozen=True, slots=True)
class UniverseContext:
    data: DataView              # DataView is NOT frozen; holds bar cursor
    portfolio: PortfolioView    # NOT frozen; holds equity/positions
    clock: Clock
    orders: OrderFactoryView    # NOT frozen; holds order book
    fold_id: int | None = None
    fold_window: tuple[int, int] | None = None

class DataView:
    """NOT frozen — holds bar cursor, token universe, indicator cache."""
    _bar_idx: int
    _cache: IndicatorCache
    # ... mutable state ...
```

**AC-S2 read-only arrays**: come from `DataView.per_token(symbol).close` returning a numpy array with `arr.setflags(write=False)`. Enforcement is at the ARRAY level, not the container level. Strategies that need mutation use `ctx.mutable_copy(arr)` which returns `np.copy(arr)` (writable).

#### ID3 — Read-only numpy arrays + indicator math

**Hazard**: Arrays returned from `DataView.per_token(...)` have `writeable=False`. Stdlib indicators (EMA, RSI, ATR) computing on them must NOT do in-place ops (`arr += x`, `arr *= y`, `np.add(arr, x, out=arr)`) — those raise `ValueError: assignment destination is read-only` at runtime.

**Decision**: Stdlib indicators in `v5/indicators.py` MUST return new arrays; no in-place ops on input arrays. Custom indicators requiring mutation call `ctx.mutable_copy(input_arr)` at their entry point.

**Rationale**: `np.mean`, `np.ema`, `pd.Series.rolling(...).mean()` all return new arrays by default — no in-place is needed for standard TA. This is defensive-by-default; the only way to hit the trap is if an impl author explicitly uses `out=` kwarg or `np.add(..., out=arr)` pattern.

**Enforcement**: code-review-time audit (Phase 4 reviewer subagent flags any `out=` kwarg in `v5/indicators.py`). Test-level enforcement is impossible without running impl; documentation + review is the gate.

**Standard indicator pattern**:
```python
def ema(token_view, n: int, col: str = "close") -> float:
    arr = getattr(token_view, col)   # read-only view
    # ✓ np.mean returns new array, no in-place
    # ✗ arr *= alpha  — would raise at runtime on read-only
    alpha = 2 / (n + 1)
    # Compute via pandas or numpy functional pattern — no out= kwarg
    ema_series = pd.Series(arr).ewm(span=n, adjust=False).mean()
    return float(ema_series.iloc[-1])
```

---

## 3. Walk-Forward Extraction (AC-V1)

### Current state (from Phase-2 audit)

- v5/signals.py:326 — "walk-forward removed in M1" comment — WF is already NOT inside signals
- v5/validation.py — standalone Tier-4 validator only; not invoked from portfolio_backtest
- WF masking is applied POST-TokenSignals at v5/validation.py:309-315
- 3 strategies have module-level mutable state violations (s524m_nofilter, s524s_conditional_exit, s523c_growth)

### M7 target (AC-V1)

```python
# v5/validation.py (modified)

class WalkForwardRunner:
    """Outer-loop WF orchestrator. Fresh Strategy instance per fold."""

    def run_splits(
        self,
        strategy_cls: type[Strategy],   # the CLASS, not an instance
        splits: list[Tuple[slice, slice]],  # (train, oos) per fold
        universe: list[InstrumentId],
        config: "PortfolioConfig",
    ) -> list["FoldResult"]:
        results = []
        for fold_id, (train_slice, oos_slice) in enumerate(splits):
            strategy = strategy_cls()         # FRESH instance per fold
            strategy.on_start(config)
            fold_result = self._run_one_fold(
                strategy, fold_id, train_slice, oos_slice, universe, config,
            )
            strategy.on_stop("fold_complete")
            results.append(fold_result)
        return results

    def _run_one_fold(self, strategy, fold_id, train_slice, oos_slice, universe, config):
        ctx = UniverseContext(
            data=...,
            portfolio=...,
            clock=TestClock(...),
            orders=OrderFactoryView(...),
            fold_id=fold_id,                 # AC-V1 — ensemble-strategy access
            fold_window=(train_slice.start, oos_slice.stop),
        )
        # Engine runs with strategy inside ctx — strategy NEVER sees train/oos masks
        return run_backtest(strategy, ctx, oos_slice)  # only oos window evaluated
```

**Signal generation has no WF coupling**: v5/signals.py::precompute_strategy_signals takes a full TokenSignals without any train_mask/oos_mask params. The existing "walk-forward removed in M1" comment confirms this is already clean at the signals-path level.

**Why AC-V1 is NOT a NOP** (Quant reviewer H4 clarification): signal-path extraction is already done (M1 work). AC-V1's actual remaining scope is:
1. **`v5/validation.py` upgrade from Tier-4 standalone to single WF entry point** — today it's invoked via `python v5/validation.py --strategy s513` outside normal backtest flow. AC-V1 makes it the ONLY path that runs walk-forward validation, replacing per-strategy ad-hoc invocation.
2. **Fresh `Strategy` instance per fold** — currently folds share instance state (implicit via module-level caches in violating strategies).
3. **`fold_id` + `fold_window` exposure on `UniverseContext`** — enables ensemble/regime-adaptive strategies (none today, but the plumbing must be ready).
4. **WalkForwardRunner class** — a concrete API surface that v5/portfolio_backtest + v5/run_paper_multi can invoke uniformly.

Budget check: AC-V1 actual remaining work is ~3h (was 6h). Falls to lower band of M7's 100-140h estimate. Same pattern as AC-H1's 10h → 1h audit correction.

### 3a. BarProcessor Phase 2/3 check_scale / check_exit ordering (AC-S11, FIX reviewer M2)

BarProcessor Phase 3 dispatches exit logic in strict order — memory note `session_2026_04_15` flagged a StopLossHandler regression from deleting this contract. AC-S11 pins the order:

```
Phase 3 (per-position, per-bar):
  1. Strategy.check_exit(pos, bar_ctx)     # FIRST — strategy gets first refusal
     → if returns ExitCheck, that wins; engine skips to step 4
  2. StopLossHandler                       # engine-side, runs only if check_exit returned None
  3. TrailStopHandler / TakeProfitHandler  # engine-side
  4. If any exit fires: on_order_triggered(exit_order) → fill cascade (§2.6)
```

Rationale: Strategy.check_exit sees the open position first so custom exit logic (e.g., regime-aware unwind) can preempt engine handlers. If strategy returns None, engine fallback handlers run in registered order. This matches v4 behavior; the contract preserves the 2026-04-15 StopLoss regression fix.

---

## 4. Paper Engine 8-Site Migration Plan (AC-P1, Task 17)

Confirmed by Phase-2 audit — all 8 sites identified with current behavior:

| # | Site | Current (flag=OFF) | Target (flag=ON) |
|---|------|---------------------|-------------------|
| 1 | `paper_engine.py:859-912` — `__init__` with PriceMonitor | Construct PriceMonitor + CandleAggregator | Branch on `config.use_data_engine`; if True, use shared DataEngine + forward to bus |
| 2 | `paper_engine.py:893-911` — CandleAggregator creation | Aggregate 1m ticks into N-min candles | Delegate to DataEngine's StreamingConsolidator (M4-shipped) |
| 3 | `paper_engine.py:1244-1252` — cleanup | PriceMonitor.disconnect() if owned | Branch: DataEngine.stop() when flag=ON |
| 4 | `paper_engine.py:2046-2068` — `_update_ws_subscriptions` | `price_monitor.update_subscriptions(tokens)` | `data_engine.subscribe_all([...])` with unsubscribe of stale |
| 5 | `paper_engine.py:3213-3217` — OHLCV live fetch | `fetcher.fetch_ohlcv(token, ...)` | `data_engine.request(stream, start_ns, end_ns)` (M6 public accessor) |
| 6 | `paper_engine.py:3225-3229` — funding rate fetch | `fetcher.fetch_funding_rates(token)` | `data_engine.request(funding_stream, ...)` |
| 7 | `paper_engine.py:3237-3252` — hourly funding settlement | Triggered at hour in (0, 8, 16) UTC | Same trigger cadence; routes through DataEngine |
| 8 | `run_paper_multi.py:360-363 + 381-401` — _PriceFanOut multiplexing | Shared PriceMonitor fans out to N engines' CandleAggregators | Shared DataEngine; MessageBus handles fan-out naturally (already FIFO-deterministic per AC-D19) |

### Dispatch pattern

Each site gains a `if self._use_data_engine:` branch. Example for Site 1:

```python
# Site 1: paper_engine.py:859-912 — __init__
def __init__(self, config: PaperConfig, *, price_monitor=None, data_engine=None):
    self._use_data_engine = config.use_data_engine
    self._effective_bar_resolution = self._compute_effective_bar_resolution(config)

    if self._use_data_engine:
        # M7 path
        assert data_engine is not None, "flag=ON requires data_engine kwarg"
        self._data_engine = data_engine
        self._price_monitor = None
        self._candle_aggregator = None
        # DataEngine's StreamingConsolidator handles sub-hourly aggregation
    else:
        # Legacy path — byte-identical to pre-M6
        self._data_engine = None
        if price_monitor is not None:
            self._price_monitor = price_monitor
            self._owns_price_monitor = False
        else:
            # existing constructor logic
            ...
```

### Stop-trigger discipline (brief §AC-P1)

Design §10 stop-trigger: if the full rewrite exceeds 18h invested, fall back to **bridging shim** — wrap `v4/price_monitor.py` behind `DataClient` Protocol. Preserves 831 LOC of WS battle-hardening at the cost of carrying legacy through M8+. Tradeoff: shim risk (v3-style freeze through M8) vs. completion risk (subtle WS reconnect bug in rewrite). Shim sunset AIPIP required if carried past M8 per M6 design.

---

## 5. Runner Swap v4→v5 (AC-P2)

Phase-2 audit located lock creation at `v5/paper_utils.py:354-372` (`acquire_pid_lock(state_dir)` uses `fcntl.flock`). Lock path is `state_dir/paper.pid` where `state_dir` comes from config.

### Migration plan — copy-not-move semantics (Quant reviewer H3 fix)

1. **Config**: `configs/runner_pool_config.json` gains `state_dir: state/v5_paper_multi` (previously `state/v4_paper_multi`).
2. **Startup shim** in `v5/run_paper_multi.py`:
   - Read old PID from `state/v4_paper_multi/paper.pid` if present
   - SIGTERM old process (if PID alive)
   - Wait up to 30s for clean shutdown; escalate to SIGKILL if still alive
   - **COPY** state files (closed_trades.json, positions.pkl) from `state/v4_paper_multi/` to `state/v5_paper_multi/` — NOT move. v4 dir preserved intact.
   - Start v5 runner against new dir
3. **Revert path**:
   - Flip `config.use_data_engine = False` AND point `state_dir` back at `state/v4_paper_multi` in config
   - Restart runner. v5 runner exits (SIGTERM on its own PID file); v4 state is still intact from the copy-not-move at step 2
   - No symlink shenanigans — two separate dirs with different fcntl.flock locks
   - **Data-loss window**: any trades executed by v5 runner between migration and revert live ONLY in `state/v5_paper_multi/`. Revert loses those trades unless explicitly reconciled. Ops runbook flags this window.
4. **tools/start_all_services.sh** update — change invocation from v4 to v5 path (already at v5 per current audit of line 20).
5. **AC-P2 tests**:
   - (a) Mock state dir, simulate v4 PID file alive → verify v5 runner SIGTERMs old, copies state, starts clean
   - (b) Revert test: after successful migration, flip config back → verify v4 state dir still has complete pre-migration state (copy preserved it)
   - (c) Concurrent-flock test: verify v4 and v5 runners can't both acquire locks simultaneously (different state_dirs = different lock paths, so this test should show NO deadlock — confirms dirs are independent)

**Pre-flight invariant**: AC-P2 MUST NOT fire during an active trading session. Ops runbook gates the swap to weekend maintenance window. Data-loss window documented per #3.

---

## 6. 13 Pre-existing Test Failures Cleanup (AC-H1)

Phase-2 audit revealed **budget overstatement** — actual effort is ~40 min not 10h:

| # | Test | Category | Root cause | Fix |
|---|------|----------|-----------|-----|
| 1-3 | `test_conviction_to_priority.py` TestAC2/AC3 (3) | meta-cascade | ImportError on `_process_entries` from simulator | Export `_process_entries` from `v5/simulator.py` (1-line change) |
| 4 | `test_conviction_to_priority::TestAC6FullSuiteGreen` | meta-cascade | Subprocess pytest fails | Auto-passes when peers fixed |
| 5 | `test_m1_fork_verification::test_ac01_v5_file_count` | stale expectation | Expects 41 .py, actual 56 | Update constant at line 182 |
| 6 | `test_m1_fork_verification::test_ac05_v5_tests_pass` | meta-cascade | Subprocess pytest fails | Auto-passes |
| 7 | `test_m1_fork_verification::test_ac11_signal_field_absent[size_multiplier]` | stale expectation | Matches comment at `signals.py:544` | Remove stale comment |
| 8 | `test_m1_fork_verification::test_ac16_exit_resolution_absent` | stale expectation | Matches comment at `config.py:243` | Remove stale comment |
| 9 | `test_m3_slots::test_full_v5_suite_passes` | meta-cascade | Subprocess pytest fails | Auto-passes |
| 10 | `test_m4_intra_bar_fill::TestAC38MarkTrigger::test_audit_log_entry_emitted_in_backtest` | false positive | Passes in isolation | No fix — investigate flaky parallelism |
| 11-12 | `test_paper_determinism` (2) | real bug | `paper_engine.py:3042` calls undefined `_sim._process_entries()` | Export from simulator (same as #1-3) |
| 13 | `test_paper_state::test_closed_trades_empty_after_deserialize` | spec drift | Test expects `list`, impl uses `deque` | Align test to deque OR revert to list (review at fix time) |

**Revised AC-H1 budget**: **6h floor, 10h ceiling** (Quant reviewer H1 pushback accepted — the 1h audit was too aggressive). Breakdown:
- Rows #1-3, #11-12 "export `_process_entries`" is not actually a 1-line change — the symbol is a `_`-prefixed private simulator internal (blast radius per CLAUDE.md: 31 importers). Exporting creates a public-API commitment. Options: (a) rename to `process_entries` + update callers (1-2h); (b) leave private, fix tests to use a public proxy (2h); (c) split into proper public/private pair (3-4h). Decide at fix time; budget 3h.
- Rows #5, #7, #8 stale expectations: genuinely 10min total.
- Rows #4, #6, #9 meta-cascade: 0h (auto-pass).
- Row #10 flaky false positive: 30min (investigate + skip marker).
- Row #13 `closed_trades` deque-vs-list: flagged as "spec drift" but the test expects `list`, impl uses `deque`. Per blast-radius rules, `closed_trades` flows through serialization + 21 position.py importers. Changing the type requires: check all `.append`/`.popleft`/iteration call sites; decide if `list` has the amortized perf of `deque.append`; update serialization format; migrate existing on-disk state. **2-3h realistic, file AIPIP if downstream impact surfaces**.

Total: ~6h floor, 10h ceiling if row #13 cascades.

---

## 7. Implementation Waves

### Wave A — Primitives + Types (no legacy dependencies)
1. `v5/strategy_api.py` — Strategy Protocol + BaseStrategy + UniverseSignals/TokenSignal dataclasses
2. `v5/fill.py` — Fill dataclass (AC-O5)
3. `v5/universe_context.py` — UniverseContext + views (DataView, PortfolioView, OrderFactoryView)
4. `v5/indicators.py` — IndicatorCache + `.cache_key()` contract (AC-S3)
5. AC-H1 quick wins (1h) — export `_process_entries`, fix 3 stale expectations

### Wave B — Strategy Loader + WF Extraction
6. `v5/strategy_loader.py` — replaces v4/engine.py::_load_strategy_fn with AST scan (AC-V2)
7. `v5/validation.py` modified — WalkForwardRunner with fresh-per-fold strategy + fold_id/window exposure (AC-V1)

### Wave C — Unified Signals + Order Factory
8. `v5/signals.py` replaces v5/signals.py + v4/portfolio_signals.py — unified dispatch, `strategy_type` field deleted
9. OrderFactoryView implementation — `arm` + `arm_bracket` (AC-O1)
10. `v5/orders.py` extension — fill FIX serializer stubs: `to_fix_ordstatus()`, `to_fix_trigger_type()`, `to_fix_trigger_price_direction()`, `LegStatus.to_fix_ordstatus()` (AC-O2)

### Wave D — Execution Event Wiring
11. BarProcessor extensions — dispatch all 15 callbacks at correct lifecycle phase (AC-S5 error containment, AC-Lifecycle)
12. `_order_reject_event` post-fill unwind hook (AC-O3)
13. Order.venue_order_id population in DataEngine live mode (AC-O4)

### Wave E — PaperEngine Migration (Task 17)
14. PaperEngine 8-site dispatch (AC-P1). 14h nominal, 18h stop-trigger.
15. Runner swap migration shim (AC-P2)
16. VenueCapabilities.supported_transport_modes additive field (AC-D15)

### Wave F — Reference Strategy Migration
17. s513 port to v5 (2h)
18. s523c port — conviction→priority split (3h)
19. s524m port — 5h, resolves module-level mutable state issues

### Wave G — Hard Merge Gate
20. **WS-mode pre-flight** (FIX reviewer M4): before the 24h recording, verify `websockets` package is installed AND `record_ws_tap.py --mode ws --duration 60` (short test) produces real WS frames (not REST-fallback). If fallback silently triggers, AC-P3 is invalid. Add `--strict` flag to recorder that errors-out on fallback.
21. Real 24h live WS recording via `record_ws_tap.py --mode ws --duration 86400 --strict` (wall-clock only, not engineering time)
22. AC-P3 shadow replay verification: `ohlcv_divergence_count == 0`
23. Production `use_data_engine=True` flag flip after parity verified

---

## 8. Test Strategy

Same isolated-subagent pattern as M6 Phase 3 (per `.claude/rules/subagent-patterns.md`):
- Subagent receives `brief.md` + file paths + existing `v5/tests/test_m6_*.py` patterns
- Subagent does NOT receive this design doc
- Tests drive the RED-GREEN cycle

### Mocking patterns

| Collaborator | Mock strategy |
|---|---|
| `DataEngine` in strategy tests | Real instance with `FakeDataClient` registered |
| `Clock` in backtest tests | `TestClock` (M4-shipped) |
| `Strategy` in lifecycle-hook tests | `MockStrategy(BaseStrategy)` with hook-call log |
| `IndicatorCache` in cache-key tests | Real instance with fake functions (named, not lambda) |
| `PaperEngine` in 8-site dispatch | `PaperEngine(config, data_engine=...)` with `FakeDataEngine` |

### Fixtures

- Real 24h live WS recording for AC-P3 (run the recorder during Wave G)
- s524m reference archive: re-use the existing Q-DEC4 validation output for parity (AC-S10)

---

## 9. Blast Radius Audit

**Additive-only changes**:
- New package `v5/strategy_api.py`, `v5/universe_context.py`, `v5/indicators.py`, `v5/strategy_loader.py`, `v5/fill.py` — no existing callers
- New methods on `v5/data/engine.py` (arm/arm_bracket via OrderFactoryView, not directly on DataEngine)
- New field `VenueCapabilities.supported_transport_modes` — additive
- New kwarg `PaperEngine.__init__(data_engine=None)` — already shipped in M6

**Medium-risk (gated behind feature flag)**:
- `v5/paper_engine.py` 8-site dispatch — each site's `if self._use_data_engine:` branch; flag=OFF byte-identical
- `v5/run_paper_multi.py` runner swap — gated behind config state_dir

**Breaking (requires strategy migrations, but no current strategies use v5 Protocol yet)**:
- `strategy_type` field removal from StrategySpec — touches v4/config.py:189 (39+ importers). **Handled**: removed only AFTER s524m/s513/s523c migrated; v4-shaped strategies kept working via legacy loader path that sets `strategy_type` internally.
- `v5/signals.py` replaces both `v5/signals.py` + `v4/portfolio_signals.py` — adds unified surface; old surfaces kept as import-redirect shims until post-M10 sunset.

**High-risk (OUT OF SCOPE)**:
- `v5/simulator.py` — 31 importers. M7 only exports `_process_entries` for test visibility (single-line AC-H1 fix); no behavioral changes.
- `v5/config.py` (39 importers) — NOT changed. PaperConfig already gained `use_data_engine` in M6.

**Rollback stance**: `config.use_data_engine = False` + restart — flag=OFF preserves full pre-M7 paper_engine behavior. Runner swap (AC-P2) is config-level — point the config at `state/v4_paper_multi` to revert.

---

## 10. Risks & Stop Triggers

| Risk | Trigger | Response |
|---|---|---|
| AC-P1 BinanceWS extraction blows 18h budget | Time tracking per-task exceeds 18h | Fall back to bridging shim (wrap v4/price_monitor behind DataClient Protocol). File AIPIP for shim sunset by end-M8. |
| 24h shadow replay diverges | `ohlcv_divergence_count > 0` | STOP. Do NOT flip use_data_engine=True in prod. File root-cause investigation. |
| Strategy Protocol too large (15 callbacks overwhelming) | User pushback during reference strategy migration | De-scope: drop the 3 position-lifecycle hooks (opened/changed/closed) from MVP, ship as M8 addendum. |
| Module-level mutable state in s524m_nofilter breaks fold isolation | AC-V1 WF test reveals fold N+1 sees fold N's cache | Refactor s524m port to keep all state on self (already in 5h budget). |
| `.cache_key()` contract adoption friction | Existing custom indicators rely on numpy params | Provide `freeze_numpy_params(**params)` helper that tobytes()-hashes arrays. Document the escape hatch. |
| FIX ARMED/TRIGGERED/RELEASED wire mapping rejected by venue | Future FIX gateway venue rejects custom tag 9001-9003 | Revisit G8 decision; fall back to local-only audit log, no wire representation. Not an M7 blocker. |
| Pre-existing 13 tests reveal deeper issues | AC-H1 fixes uncover real bugs beyond comment cleanup | De-scope deeper fixes to separate milestone; ship AC-H1 with partial cleanup documented. |
| Task 17 + runner swap destabilizes live paper | Live paper fleet shows P&L drift post-migration | Rollback via flag flip + state_dir revert. Already designed. |

---

## 11. Open Questions (explicitly not for this design)

M8+ decisions; documented here so they're not re-surfaced mid-M7:

- Full SizingRequest field set (leverage/margin_mode/notional_usd) — M8 extends the M7 stub
- OKX/Bybit/Deribit client implementations — M11+
- Multi-venue portfolio routing (cross-venue arbitrage strategies) — M11+
- Real FIX wire transmission (venue submission via FIX session) — M11+ or custom per-venue
- Strategy hot-reload (reload strategy code without runner restart) — post-M10

---

## 12. Design Review Self-Check

- [x] Every AC (S1-S6, V1-V2, O1-O5, P1-P3, D15, H1, plus Lifecycle/Regime/view) has a named home
- [x] G1-G8 decisions from brief carried into concrete interfaces (§2)
- [x] Phase-2 audit findings folded: 13 tests root-cause mapped (§6), 8 sites line-numbered (§4), WF state current (§3), loader location pinned (§2.5)
- [x] Strategy Loader AST scan covers both attribute-access AND bare-name-call forms (reviewer FIX-M9 from Phase-1)
- [x] Module-level state scan warns without failing (preserves s523c_growth.TOKEN_BLACKLIST read-only allowance)
- [x] Test strategy pins mocks + fixtures before Phase 3
- [x] Blast radius audit maps each touched file to risk tier
- [x] Stop triggers + rollback plan explicit
- [x] AC-H1 budget corrected 10h → 1h per audit finding — reduces total M7 budget toward lower band (~100h)
- [x] No new ACs introduced (design respects brief's scope)

Ready for Phase-2 review gate.

---

## 13. Round-1 Review Resolutions

Two parallel independent reviewers (quant architect + FIX/industry) returned NEEDS_ATTENTION with 7 HIGH findings total. All folded:

| Sev | Origin | Finding | Resolution |
|---|---|---|---|
| HIGH | Quant | AC-H1 1h budget too aggressive given deque/list spec-drift blast-radius | §6 revised: 6h floor / 10h ceiling; per-row breakdown with AIPIP escape hatch for row #13 |
| HIGH | Quant | `.cache_key()` silent non-hashable acceptance | §2.3 defensive `hash(result)` + clear error at call site |
| HIGH | Quant | Runner swap SIGTERM + revert contradiction | §5 copy-not-move semantics; v4 dir preserved intact; data-loss-window documented |
| HIGH | Quant | AC-V1 "partially NOP" concern | §3 clarified: WF extraction IS doing real work (validation.py upgrade, fresh-per-fold, fold_id exposure, WalkForwardRunner class) |
| HIGH | FIX | Fill missing ExecType(150) | §2.4 ExecType enum added to `v5/orders.py`; Fill gains `exec_type` field |
| HIGH | FIX | Callback dispatch order unspecified | §2.6 new section with 3 cascade patterns (bracket entry, SL hit, reject unwind) |
| HIGH | FIX | FIX wire serializer location ambiguous | §2.4a pins serializers on `v5/orders.py` enums with concrete method signatures |
| MED | Quant | AC-S5 "treated as no-op" undefined at engine level | §2.7 new section operationalizing per-method behavior + `strategy.exception_counter` observability + `StrategyQuarantined` event |
| MED | Quant | Wave E could parallelize with Wave D | Noted; implementation discretion, not a defect |
| MED | Quant | Stop-trigger accountability | §10 already lists triggers; acceptance: measurement happens at user check-in gate (Wave E mid-point) |
| MED | FIX | `arm_bracket` MARKET entry validation gap | §2.2 updated: MARKET entries defer SL/TP side validation to fill time via `_order_reject_event` re-validation |
| MED | FIX | check_exit vs StopLossHandler ordering | §3a new section: Strategy.check_exit runs FIRST; engine handlers fall-through; matches v4 + prevents 2026-04-15 regression |
| MED | FIX | AST scan misses threading.Event.wait(timeout=) | §2.5 `_WARN_ATTRIBUTES` set; WARN-only per FIX reviewer guidance |
| MED | FIX | AC-P3 WS-mode silent fallback | Wave G step 20 new: pre-flight `--strict` flag on `record_ws_tap.py` errors-out on REST fallback |
| LOW | Quant | AC-Reg2 `universe_ctx_id` undefined | Will use `id(ctx)` in v5/regimes.py impl — clarify in Phase 4 |
| LOW | Quant | Dispatch ordering documentation gap | §2.6 resolves (promoted from LOW to part of FIX H2 fix) |
| LOW | FIX | `paper-{order_id:08x}` collision risk | Phase 4 adopts `f"paper-{runner_instance_id}-{order_id:08x}"` if collision detected in s524m migration |
| LOW | FIX | No generic `on_order_event` catch-all | Strategies wanting generic logging can subclass BaseStrategy and override — acceptable; defer to user feedback |
| LOW | FIX | `on_reset` dead code if only fresh-per-fold | §3 scopes `on_reset` as future-reuse path; MVP uses fresh-per-fold only; Phase 4 may strip `on_reset` entirely if no reuse callers |

All 7 HIGH findings resolved; 7 MED findings folded; 5 LOW noted for Phase 4 judgment.

