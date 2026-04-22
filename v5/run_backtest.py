"""M11 — ``run_backtest`` orchestrator module (Commit 8).

This module is the canonical top-level backtest entry point per ADR-0001
(event-driven dispatch) + ADR-0002 (extensible data model). It wires up
the unified event loop the M11 milestone establishes:

  1. Instantiate a ``SimulationClock`` pinned to ``start_ns``.
  2. Construct a shared ``MarketDataCache`` keyed off the clock.
  3. Register parquet-backed replay clients for ``BarData`` +
     ``FundingRateData`` (via :class:`ParquetReplayClient`) and
     ``MetricData`` (via :class:`ParquetMetricsReplayClient`) — one
     polymorphic ``DataEngine`` serves both.
  4. Subscribe each strategy's declared ``required_data()`` streams.
  5. Register per-strategy cadences (ADR-0002 move #4 — GCD clock
     advance, strategies invoked only on their own cadence).
  6. Drive the event loop: per GCD tick, hydrate the cache from the
     REPLAY clients up to ``clock.now_ns()``, then invoke each due
     strategy's ``generate(ctx, bar_idx)`` callback inline. Exits +
     orders flow through the signal-native pipeline
     (``_process_exits_native`` + ``_process_orders_native``) — the
     same pipeline paper uses.

Arbitration + risk-components phase 3.0 run at the orchestrator level
on emitted UniverseSignals BEFORE ``_process_orders_native`` — so
the default ``RandomShuffle`` arbitration policy is applied to the
same candidate set the legacy simulator arbitrates (Commit 6 handoff
note #1). Without this, paper (which already does arbitrate) and
backtest would diverge on AC-2 parity.

``drive_paper_in_replay`` is the parity-test twin: the same strategy
instance is driven through ``PaperPortfolioEngine`` in REPLAY mode so
the parity test (``test_m11_paper_vs_backtest_parity``) can
byte-compare closed_trades.

All helpers in this module are strategy-agnostic. No per-strategy
branching is allowed; adding a new strategy shape is a new
``BaseStrategy`` subclass, no orchestrator edit.

Naming note: the positional parameters are ``start`` / ``end`` per
``test_run_backtest_signature_uses_declared_shape``; the values are
nanoseconds-since-epoch. The kwargs ``start_ns`` / ``end_ns`` are
accepted as aliases for caller ergonomics.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from v5.config import PortfolioConfig
from v5.data.cache import MarketDataCache
from v5.data.engine import DataEngine
from v5.data.metrics import BUILT_IN_MANIFEST, MetricsManifest
from v5.data.registry import DataClientRegistry
from v5.data.streams import (
    BarData,
    FundingRateData,
    InstrumentId,
    MetricData,
    TransportMode,
    Venue,
)

_log = logging.getLogger(__name__)


__all__ = [
    "run_backtest",
    "drive_paper_in_replay",
    "_build_default_registry",
    "_pick_mode_for_registry",
    # Preserved Commit-6-rework re-export — keeps the parity fixture
    # importable from ``v5.run_backtest`` for the frozen Phase-3 test.
    "_build_parity_fixture",
    "build_parity_fixture",
]


# ---------------------------------------------------------------------------
# Parity fixture re-export (Commit-6 carryover)
# ---------------------------------------------------------------------------


from v5.tests.fixtures._m11_parity import (  # noqa: E402
    _build_parity_fixture,
    build_parity_fixture,
)


# ---------------------------------------------------------------------------
# Simulation clock (tick-driven, advance()/now_ns()/current_bar_idx)
# ---------------------------------------------------------------------------


class SimulationClock:
    """Deterministic clock driven by ``DataEngine.advance_one_tick``.

    Exposes ``now_ns()`` + ``advance(ns)`` + ``current_bar_idx`` so it
    satisfies both the :class:`MarketDataCache` PIT contract (reads
    ``current_bar_idx``) and the :class:`DataEngine` cadence loop
    (calls ``advance(ns)``).

    ``current_bar_idx`` is the monotonic count of GCD ticks since
    ``start_ns`` — the same semantic :class:`DataEngine.advance_one_tick`
    establishes.
    """

    __slots__ = ("_start_ns", "_now_ns", "current_bar_idx")

    def __init__(self, start_ns: int):
        self._start_ns = int(start_ns)
        self._now_ns = int(start_ns)
        self.current_bar_idx = 0

    def now_ns(self) -> int:
        return self._now_ns

    def advance(self, ns: int) -> int:
        self._now_ns += int(ns)
        return self._now_ns


# ---------------------------------------------------------------------------
# Registry construction + transport-mode picker
# ---------------------------------------------------------------------------


def _build_default_registry(
    manifest: MetricsManifest = BUILT_IN_MANIFEST,
    fixture_root: Optional[Path] = None,
) -> DataClientRegistry:
    """Register the canonical parquet replay clients on a fresh registry.

    Shared by the bar/funding path (``ParquetReplayClient``) and the
    metric path (``ParquetMetricsReplayClient``). The same bar client
    instance serves both ``BarData`` and ``FundingRateData`` — the
    registry dedupes factories by object identity so one physical
    client is stored under two ``(Venue, Data subclass)`` keys.

    Extensibility: to register clients for new venues or data classes,
    compose on top of the returned registry before constructing
    ``DataEngine``. The orchestrator never branches on venue identity.
    """
    from v5.data.clients.parquet_metrics import ParquetMetricsReplayClient
    from v5.data.clients.parquet_replay import ParquetReplayClient

    registry = DataClientRegistry()
    bar_client = ParquetReplayClient(fixture_root=fixture_root)
    registry.register(Venue.BINANCE, BarData, lambda _cfg, _c=bar_client: _c)
    registry.register(Venue.BINANCE, FundingRateData, lambda _cfg, _c=bar_client: _c)

    metrics_client = ParquetMetricsReplayClient(
        manifest=manifest, fixture_root=fixture_root,
    )
    registry.register(Venue.BINANCE, MetricData, lambda _cfg, _c=metrics_client: _c)
    return registry


def _pick_mode_for_registry(registry: DataClientRegistry) -> TransportMode:
    """Determine the transport mode the engine should operate under.

    Canonical contract: if every registered client supports
    ``TransportMode.REPLAY``, we are in REPLAY mode. Any registered
    client missing REPLAY support would imply a live transport; we
    surface that as PUSH so the engine's tick loop is not driven
    programmatically.

    This module only registers replay clients, so the expected answer
    is ``TransportMode.REPLAY``. The helper exists so
    ``test_uses_data_engine_replay_mode`` can assert the orchestrator
    is configured deterministically.
    """
    seen_modes: set = set()
    for venue, _data_class in list(registry._clients_by_key.keys()):  # type: ignore[attr-defined]
        for client in registry._clients_by_key.get((venue, _data_class), []):  # type: ignore[attr-defined]
            modes = getattr(client, "supported_modes", frozenset()) or frozenset()
            seen_modes.update(modes)
    if TransportMode.REPLAY in seen_modes and TransportMode.PUSH not in seen_modes:
        return TransportMode.REPLAY
    if TransportMode.PUSH in seen_modes:
        return TransportMode.PUSH
    return TransportMode.REPLAY


# ---------------------------------------------------------------------------
# Strategy-id helpers
# ---------------------------------------------------------------------------


def _strategy_id(strategy: Any) -> str:
    """Resolve a strategy identifier via ``.id → .strategy_id → .name
    → type.__name__`` fallback chain — same order :class:`DataEngine`
    uses internally so the per-strategy cadence map aligns with the
    dispatch bookkeeping.
    """
    for attr in ("id", "strategy_id", "name"):
        val = getattr(strategy, attr, None)
        if isinstance(val, str) and val:
            return val
    return type(strategy).__name__


def _assert_strategy_ids_unique(strategies: Iterable[Any]) -> None:
    """Fail fast if two strategies resolve to the same id.

    Carryover from commit-7 SESSION_RESUME note C5-M2: the id fallback
    chain can silently collide when two strategies share a class name
    or both fall back to the class name. Under silent collision, the
    cadence map overwrites and the dispatch filter invokes only one.
    A one-liner assertion at orchestrator startup surfaces this at
    configuration time rather than after the fact.
    """
    seen: dict = {}
    for strat in strategies:
        sid = _strategy_id(strat)
        if sid in seen:
            raise ValueError(
                f"Duplicate strategy id {sid!r}: "
                f"{type(seen[sid]).__name__} and {type(strat).__name__}. "
                "Set a unique `.id` / `.strategy_id` on one of them."
            )
        seen[sid] = strat


def _align_start_to_cadence(start_ns: int, clock_advance_ns: int) -> int:
    """Normalize ``start_ns`` to the nearest GCD-cadence boundary at or
    below it — carryover from commit-7 SESSION_RESUME note C5-M1.

    ``bar_idx`` only equals ``(now_ns - start_ns) // clock_advance_ns``
    when ``start_ns`` is on a cadence boundary. Callers that pass a
    UTC-aligned ``start_ns`` (every second-of-day zero) already land
    on a boundary for sub-day cadences; the snap-down protects
    non-aligned inputs.
    """
    if clock_advance_ns <= 0:
        return int(start_ns)
    start_ns = int(start_ns)
    remainder = start_ns % clock_advance_ns
    return start_ns - remainder


# ---------------------------------------------------------------------------
# Replay driver (cache hydration up to clock.now_ns())
# ---------------------------------------------------------------------------


def _hydrate_cache_up_to_now(
    engine: DataEngine,
    instruments: List[InstrumentId],
    strategies: List[Any],
    *,
    replay_state: dict,
    end_ns: int,
    effective_subs_by_sid: Optional[Dict[str, List[Any]]] = None,
) -> None:
    """Stream replay events into the cache up to ``clock.now_ns()``.

    REPLAY clients are iterator-based: each subscribed stream is
    replayed once end-to-end. We pre-materialize the iterators in
    ``replay_state`` (keyed by ``DataStream``) and advance them in
    lockstep with the simulation clock.

    Implementation detail: per-tick we drain every iterator until its
    next event's ``ts_event > clock.now_ns()``. Drained events flow
    through the cache (native ``on_data``) bypassing the bus handler
    cascade — the cache is the canonical store, and strategy handlers
    are opt-in observers (strategies read via ``ctx`` / ``TokenView``
    accessors, not the handler callback).
    """
    clock = engine.clock
    now_ns = int(clock.now_ns()) if clock is not None else end_ns
    # Lazy-init iterators on first call
    if not replay_state:
        streams_by_sub = _collect_subscribed_streams(
            strategies, effective_subs_by_sid=effective_subs_by_sid,
        )
        for stream, sub in streams_by_sub.items():
            clients = engine.registry.get_clients(
                stream.instrument, stream.data_class,
            )
            # Pick the first REPLAY-capable client for this stream.
            picked = None
            for c in clients:
                if TransportMode.REPLAY in getattr(c, "supported_modes", frozenset()):
                    picked = c
                    break
            if picked is None:
                continue
            # Pre-fetched window is the full sim window — iterators
            # are lazy so this is cheap.
            it = picked.replay(stream, int(clock._start_ns), int(end_ns))  # type: ignore[attr-defined]
            replay_state[stream] = {
                "iter": it,
                "role": sub.role,
                "next": None,
                "exhausted": False,
            }

    # Drain every iterator until its head event's ts > now_ns.
    for stream, st in replay_state.items():
        if st["exhausted"]:
            continue
        while True:
            head = st["next"]
            if head is None:
                try:
                    head = next(st["iter"])
                except StopIteration:
                    st["exhausted"] = True
                    break
                st["next"] = head
            try:
                head_ts = int(getattr(head, "ts_event", 0))
            except Exception:
                head_ts = 0
            if head_ts > now_ns:
                break
            # Feed into the cache
            try:
                engine.cache.on_data(head, role=st["role"])
            except Exception as e:  # pragma: no cover — keep loop alive
                _log.warning(
                    "cache.on_data rejected %s for stream %r: %r",
                    type(head).__name__, stream, e,
                )
            st["next"] = None


def _subscribe_strategies_with_default(
    engine: DataEngine,
    strategies: List[Any],
    instruments: List[InstrumentId],
) -> Dict[str, List[Any]]:
    """Subscribe each strategy's ``required_data()`` streams — falling
    back to a synthesized 1h-bar-per-instrument subscription when the
    strategy declares none.

    Several shipped v5 strategies (s513, s524m) return an empty
    ``required_data()`` in the M7 Protocol port because their original
    v4 incarnation leaned on engine-populated indicators rather than
    explicit subscriptions. The cadence loop needs at least one
    ``BarData`` stream per strategy to compute a GCD tick; synthesizing
    defaults here keeps the orchestrator compatible with legacy
    strategies while still honoring any explicit subscriptions a
    modern strategy declares.

    The METRIC_IDS / EXTRA_BAR_RESOLUTIONS_MIN class-attribute lookups
    are declarative-augmentation (ADR-0002 move #1): the strategy class
    declares its metric-id / extra-resolution needs, and the orchestrator
    expands them into concrete subscriptions per instrument. No strategy
    method is mutated; the effective subscription list is returned so
    the caller can pass it to
    :meth:`DataEngine.register_strategy_cadences` as an explicit
    override alongside ``strategies`` — keeping the strategy protocol
    read-only from the orchestrator.

    Returns ``{strategy_id: [Subscription, ...]}`` — the effective per-
    strategy subscription list. Strategies with their own non-empty
    ``required_data()`` appear verbatim; strategies defaulted here
    appear with the synthesized list.
    """
    from v5.bar_spec import BarSpec
    from v5.data.streams import DataStream, MetricData, Subscription

    effective_by_sid: Dict[str, List[Any]] = {}
    for strat in strategies:
        sid = _strategy_id(strat)
        try:
            subs = list(strat.required_data() or [])
        except Exception as exc:
            _log.warning(
                "required_data() failed for strategy %r: %r",
                sid, exc,
            )
            subs = []
        if not subs:
            # Synthesize default BarData(1h) subscriptions per
            # instrument — the minimum needed for any strategy's
            # cadence to be computed.
            synth = [
                Subscription(
                    stream=DataStream(
                        instrument=inst, data_class=BarData,
                        bar_spec=BarSpec.from_minutes(60),
                    ),
                    handler=lambda _e: None,
                )
                for inst in instruments
            ]
            # M11 Stage-2a: strategies (notably s524m) declare their
            # metric-id needs via a class-level ``METRIC_IDS`` tuple.
            # When the strategy zero-args construction leaves
            # ``required_data()`` empty, the orchestrator adds
            # MetricData subs for each instrument × metric_id here.
            # This preserves ADR-0002 move #1 (declarative data
            # needs) while keeping the no-args frozen-test call
            # shape working.
            metric_ids = getattr(type(strat), "METRIC_IDS", None)
            if metric_ids:
                for inst in instruments:
                    for metric_id in metric_ids:
                        synth.append(
                            Subscription(
                                stream=DataStream(
                                    instrument=inst,
                                    data_class=MetricData,
                                    discriminator=metric_id,
                                ),
                                handler=lambda _e: None,
                            )
                        )
            # Optional 4h BarData subscription — strategies that need
            # an MTF overlay (e.g., s524m's RSI4h) declare it via a
            # class-level ``EXTRA_BAR_RESOLUTIONS_MIN`` tuple. The
            # default-synthesizer adds them alongside the 1h bar.
            extra_res = getattr(
                type(strat), "EXTRA_BAR_RESOLUTIONS_MIN", None,
            )
            if extra_res:
                for inst in instruments:
                    for res_min in extra_res:
                        synth.append(
                            Subscription(
                                stream=DataStream(
                                    instrument=inst, data_class=BarData,
                                    bar_spec=BarSpec.from_minutes(res_min),
                                ),
                                handler=lambda _e: None,
                            )
                        )
            subs = synth
            # ADR-0002 §2: the strategy protocol is read-only from the
            # orchestrator. Previously the synthesized subs were smuggled
            # in by monkey-patching ``strat.required_data = lambda ...``;
            # that violated the declarative contract (orchestrator
            # "wishes" on the strategy rather than accepting what it
            # declares). Today the subs flow through the explicit
            # ``effective_subs_by_strategy`` override on
            # ``register_strategy_cadences`` — no method-table mutation.
        effective_by_sid[sid] = list(subs)
        engine.subscribe_all(list(subs))
    return effective_by_sid


def _collect_subscribed_streams(
    strategies: List[Any],
    effective_subs_by_sid: Optional[Dict[str, List[Any]]] = None,
) -> dict:
    """Return a dict mapping each ``DataStream`` a strategy subscribes
    to the ``Subscription`` carrying it. Deduplicates by ``DataStream``
    identity (same stream subscribed by two strategies is hydrated
    once).

    When ``effective_subs_by_sid`` is provided, it is the authoritative
    per-strategy subscription list — ADR-0002 §2 plus the orchestrator's
    ``_subscribe_strategies_with_default`` synthesis (e.g. from class-
    level ``METRIC_IDS`` / ``EXTRA_BAR_RESOLUTIONS_MIN`` declarations).
    This replaces the prior monkey-patch of ``strat.required_data``
    that allowed the orchestrator to augment per-strategy declarations
    without mutating the strategy's method table. When absent, falls
    back to reading ``strat.required_data()`` directly (paper-live
    path / tests that construct clients manually).
    """
    out: dict = {}
    for strat in strategies:
        sid = _strategy_id(strat)
        subs: List[Any]
        if effective_subs_by_sid is not None and sid in effective_subs_by_sid:
            subs = list(effective_subs_by_sid[sid])
        else:
            try:
                subs = list(strat.required_data() or [])
            except Exception as exc:
                _log.warning(
                    "strategy %r required_data() raised: %r",
                    sid, exc,
                )
                continue
        for sub in subs:
            stream = getattr(sub, "stream", None)
            if stream is None:
                continue
            out.setdefault(stream, sub)
    return out


# ---------------------------------------------------------------------------
# Orchestrator: run_backtest
# ---------------------------------------------------------------------------


def run_backtest(
    strategies: List[Any],
    instruments: List[InstrumentId],
    start: Optional[int] = None,
    end: Optional[int] = None,
    manifest: MetricsManifest = BUILT_IN_MANIFEST,
    config: Optional[PortfolioConfig] = None,
    fixture_root: Optional[Path] = None,
    *,
    start_ns: Optional[int] = None,
    end_ns: Optional[int] = None,
):
    """Top-level event-driven backtest orchestrator.

    Per ADR-0001 this is the sole production entrypoint for an
    event-driven backtest. No per-strategy special-casing; works for
    any strategy implementing the :class:`Strategy` Protocol.

    Args:
      strategies: list of Protocol-conformant strategies. Each must
        expose ``required_data() → list[Subscription]`` and
        ``generate(ctx, bar_idx) → UniverseSignals``.
      instruments: active instrument universe (used for
        :class:`UniverseContext` construction).
      start / end: sim window in nanoseconds-since-epoch. Callers that
        prefer the ``start_ns`` / ``end_ns`` kwargs get the same
        semantics — the positional names match the frozen AC-10
        signature test.
      manifest: :class:`MetricsManifest` consulted by the metrics
        replay client. Defaults to ``BUILT_IN_MANIFEST``.
      config: :class:`PortfolioConfig`. Provides the seed, capital,
        arbitration policy, and sizing clamps.
      fixture_root: optional override for the parquet root. Defaults
        to the project-level ``data/`` tree (same convention as
        :class:`ParquetReplayClient`).

    Returns:
      :class:`SimulationState` with accumulated closed trades, equity
      snapshots, and rejection counters.
    """
    # -- Normalize start/end kwargs ---------------------------------
    if start is None:
        start = start_ns
    if end is None:
        end = end_ns
    if start is None or end is None:
        raise ValueError(
            "run_backtest requires `start` + `end` (or `start_ns` + "
            "`end_ns`) nanoseconds-since-epoch"
        )
    start = int(start)
    end = int(end)
    if end <= start:
        raise ValueError(
            f"run_backtest requires end ({end}) > start ({start})"
        )

    config = config or PortfolioConfig()

    # -- Strategy-id uniqueness (C5-M2 carryover) -------------------
    _assert_strategy_ids_unique(strategies)

    # -- Clock, cache, engine ---------------------------------------
    # First, build engine with a placeholder clock so we can compute
    # cadences; then snap ``start`` down to a cadence boundary.
    clock = SimulationClock(start_ns=start)
    cache = MarketDataCache(clock=clock)
    registry = _build_default_registry(
        manifest=manifest, fixture_root=fixture_root,
    )
    engine = DataEngine(registry=registry, cache=cache, clock=clock)

    # -- Subscribe per-strategy required_data (ADR-0002 move #1) ----
    effective_subs_by_sid = _subscribe_strategies_with_default(
        engine, strategies, instruments,
    )

    # -- Cadence registration (ADR-0002 move #4) --------------------
    engine.register_strategy_cadences(
        list(strategies), effective_subs_by_sid,
    )
    gcd_ns = engine._clock_advance_ns
    # Snap start to the nearest cadence boundary (C5-M1 carryover).
    aligned_start = _align_start_to_cadence(start, gcd_ns)
    if aligned_start != start:
        _log.info(
            "run_backtest: start_ns %d snapped to cadence boundary %d "
            "(gcd=%d ns)", start, aligned_start, gcd_ns,
        )
        # Rebuild clock — cache was constructed against the original
        # clock but MarketDataCache only reads ``current_bar_idx`` /
        # ``now_ns`` so updating the clock object's internal state is
        # safe. Easier: keep the same clock object, just roll back.
        clock._start_ns = aligned_start  # type: ignore[attr-defined]
        clock._now_ns = aligned_start    # type: ignore[attr-defined]
        start = aligned_start

    # -- Per-strategy spec map --------------------------------------
    strategy_specs = _collect_strategy_specs(strategies)

    # -- Simulation state --------------------------------------------
    from v5.config import StrategySpec
    from v5.simulator import SimulationState
    sim = SimulationState(initial_capital=float(config.capital))
    sim.max_equity_watermark = float(config.capital)
    # Attach specs so _process_orders_native can look them up.
    try:
        sim.strategy_specs = strategy_specs  # type: ignore[attr-defined]
    except Exception:
        pass

    # Install slippage model lookups (keep parity with legacy path)
    try:
        from v5.sizing.slippage import get_slippage_model
        for sid, spec in strategy_specs.items():
            try:
                sim._slippage_models[sid] = get_slippage_model(
                    getattr(spec, "slippage_model", None)
                )
            except Exception:
                pass
    except ImportError:
        pass

    # -- Context (DataView-wrapped cache) ---------------------------
    ctx = _build_orchestrator_context(
        instruments=instruments, cache=cache, clock=clock, config=config,
    )

    # -- Replay driver state ---------------------------------------
    replay_state: dict = {}

    # -- Event loop -------------------------------------------------
    from v5.simulator import (
        _fire_armed_orders_native,
        _process_exits_native,
        _process_orders_native,
    )

    strategies_by_id: dict = {
        _strategy_id(s): s for s in strategies
    }

    while clock.now_ns() < end:
        engine.advance_one_tick()
        bar_idx = int(clock.current_bar_idx)

        # Hydrate cache from replay clients up to clock.now_ns()
        _hydrate_cache_up_to_now(
            engine, instruments, list(strategies),
            replay_state=replay_state, end_ns=end,
            effective_subs_by_sid=effective_subs_by_sid,
        )

        due = engine.strategies_due_at(bar_idx)
        if not due:
            continue

        # Collect per-strategy UniverseSignals
        signals_by_sid: dict = {}
        strategies_by_sid_due: dict = {}
        for sid in due:
            strat = strategies_by_id.get(sid)
            if strat is None:
                continue
            try:
                signals = strat.generate(ctx, bar_idx)
            except Exception as exc:  # pragma: no cover - AC-S5
                _log.warning(
                    "strategy.generate(%s) raised %r — emitting empty "
                    "UniverseSignals", sid, exc,
                )
                try:
                    strat.exception_counter = (
                        getattr(strat, "exception_counter", 0) + 1
                    )
                except AttributeError:
                    pass
                from v5.strategy_api import UniverseSignals
                signals = UniverseSignals(bar_idx=bar_idx, signals={})
            signals_by_sid[sid] = signals
            strategies_by_sid_due[sid] = strat

        # Arbitration + risk-components phase 3.0 (Commit 6 handoff
        # note #1): the orchestrator calls these BEFORE handing
        # candidates to _process_orders_native. Otherwise paper
        # (which DOES arbitrate) and backtest diverge under the
        # default RandomShuffle policy.
        if signals_by_sid:
            signals_by_sid = _apply_arbitration_orchestrator(
                signals_by_sid, bar_idx=bar_idx, config=config, sim=sim,
            )

        # Exits + orders via signal-native pipeline.
        _process_exits_native(sim, ctx, bar_idx, strategies_by_sid_due)
        _process_orders_native(
            sim, signals_by_sid, ctx, bar_idx, strategy_specs,
        )
        # Armed-order intra-bar trigger sweep (ADR-0002 move #4 —
        # order-manager state, not strategy state). Fires at bar
        # close; same-bar entries open immediately when the trigger
        # price is bracketed by the current bar's OHLC.
        _fire_armed_orders_native(sim, ctx, bar_idx)

        # Equity snapshot for metrics computation
        try:
            sim.equity_snapshots.append((int(clock.now_ns()), sim.portfolio_equity))
        except Exception:
            pass

    return sim


# ---------------------------------------------------------------------------
# Arbitration at orchestrator level
# ---------------------------------------------------------------------------


def _apply_arbitration_orchestrator(
    signals_by_sid: dict,
    *,
    bar_idx: int,
    config: PortfolioConfig,
    sim: Any,
) -> dict:
    """Apply risk components + arbitration policy to native signals.

    Mirrors :meth:`PaperPortfolioEngine._apply_arbitration_native`
    structurally — the logic is the same so backtest and paper produce
    the same candidate order under the configured policy.
    """
    import numpy as np

    from v5.simulator import (
        _apply_risk_components_phase_30,
        _run_arbitration_dispatch,
    )
    from v5.strategy_api import UniverseSignals

    if not signals_by_sid:
        return signals_by_sid

    class _PriorityShim:
        __slots__ = ("priority", "_token_signal")

        def __init__(self, token_signal):
            prio = float(getattr(token_signal, "priority", 0.0) or 0.0)
            self.priority = np.array([prio], dtype=np.float64)
            self._token_signal = token_signal

    candidates: list = []
    shim_by_key: dict = {}
    for sid, univ in signals_by_sid.items():
        inner = getattr(univ, "signals", None)
        if not inner:
            continue
        for token, token_signal in inner.items():
            shim = _PriorityShim(token_signal)
            shim_by_key[(sid, token)] = token_signal
            candidates.append((sid, token, shim))

    if not candidates:
        return signals_by_sid

    translator: dict = {}
    zero_map = np.zeros(max(bar_idx + 1, 1), dtype=np.int64)
    for sid, token, _ in candidates:
        translator[token] = zero_map

    kept = _apply_risk_components_phase_30(
        candidates, translator, bar_idx, config, sim,
    )
    if not kept:
        return {
            sid: UniverseSignals(bar_idx=bar_idx, signals={})
            for sid in signals_by_sid
        }

    ordered_indices = _run_arbitration_dispatch(
        kept, translator, bar_idx, config, sim,
    )

    rebuilt: dict = {sid: {} for sid in signals_by_sid}
    for idx in ordered_indices:
        sid, token, _shim = kept[idx]
        rebuilt[sid][token] = shim_by_key[(sid, token)]

    return {
        sid: UniverseSignals(bar_idx=bar_idx, signals=signals)
        for sid, signals in rebuilt.items()
    }


# ---------------------------------------------------------------------------
# Orchestrator ctx (minimal DataView that delegates to MarketDataCache)
# ---------------------------------------------------------------------------


def _build_orchestrator_context(
    *,
    instruments: List[InstrumentId],
    cache: MarketDataCache,
    clock: Any,
    config: PortfolioConfig,
):
    """Build a context object suitable for ``strategy.generate(ctx, bar_idx)``.

    Today we bind to the existing :class:`v5.universe_context.UniverseContext`
    test-harness constructor because every shipped v5 strategy
    (s513, s523c, s524m) reads via ``ctx.per_token(...)`` /
    ``ctx.market_indices`` / ``ctx.positions`` — the production path
    uses the same surface. The cache is attached as ``ctx.cache`` so
    ``_resolve_market_cache`` finds it on the native simulator path.
    """
    from v5.universe_context import UniverseContext, DataView, PortfolioView, OrderFactoryView

    tokens = [inst.symbol.replace("USDT", "") for inst in instruments]
    data = DataView(
        tokens_seed=tokens or ["BTC"], bars=1, seed=int(config.seed),
    )
    # Use the real orchestrator cache — TokenView delegations reach it.
    try:
        data._market_cache = cache  # type: ignore[attr-defined]
    except Exception:
        pass

    portfolio = PortfolioView(equity=float(config.capital))
    orders = OrderFactoryView(runner_instance_id="run_backtest", clock=clock)

    ctx = UniverseContext(
        data=data, portfolio=portfolio, clock=clock, orders=orders,
    )
    try:
        object.__setattr__(ctx, "_lifecycle_config", {"bars": 1})
    except Exception:
        pass
    # Expose cache directly on ctx too — _resolve_market_cache prefers
    # ``ctx.cache`` when present.
    try:
        object.__setattr__(ctx, "cache", cache)
    except Exception:
        pass
    return ctx


# ---------------------------------------------------------------------------
# Strategy spec collection
# ---------------------------------------------------------------------------


def _collect_strategy_specs(strategies: List[Any]) -> dict:
    """Build ``{strategy_id: StrategySpec}`` from each strategy's
    ``.spec`` / ``.strategy_spec`` attribute.

    Falls back to a minimal-default :class:`StrategySpec` for strategies
    that don't expose one — keeps the orchestrator robust for test
    fixtures where a bare ``_TinyStrategy`` carries no spec.
    """
    from v5.config import StrategySpec

    out: dict = {}
    for strat in strategies:
        sid = _strategy_id(strat)
        spec = (
            getattr(strat, "spec", None)
            or getattr(strat, "strategy_spec", None)
        )
        if spec is None:
            spec = StrategySpec(strategy_id=sid, market="perp")
        out[sid] = spec
    return out


# ---------------------------------------------------------------------------
# drive_paper_in_replay — AC-2 parity driver
# ---------------------------------------------------------------------------


def drive_paper_in_replay(
    engine: Any,
    strategies: List[Any],
    instruments: List[InstrumentId],
    *,
    start_ns: int,
    end_ns: int,
    fixture_root: Optional[Path] = None,
):
    """Drive :class:`PaperPortfolioEngine` in REPLAY mode for AC-2 parity.

    Conceptually mirrors :func:`run_backtest`: a polymorphic replay
    source feeds bar events through the DataEngine into the shared
    cache; the paper engine's tick handler (which now inline-calls
    ``strategy.generate`` per ADR-0001) processes each tick. Closed
    trades + equity snapshots accumulate on the engine's
    :class:`SimulationState`.

    This helper is deliberately minimal — it composes the same
    primitives :func:`run_backtest` uses, then hands the hydrated cache
    + tick clock to the paper engine so the production dispatch path
    runs against the same data.
    """
    # Attach strategies to the engine's config so the paper tick path
    # dispatches them (the paper engine reads strategies from
    # ``config.strategies`` via :class:`StrategySpec` records).
    from v5.config import StrategySpec as _PortfolioStrategySpec

    clock = SimulationClock(start_ns=int(start_ns))
    cache = MarketDataCache(clock=clock)
    registry = _build_default_registry(fixture_root=fixture_root)
    data_engine = DataEngine(registry=registry, cache=cache, clock=clock)

    effective_subs_by_sid = _subscribe_strategies_with_default(
        data_engine, strategies, instruments,
    )

    data_engine.register_strategy_cadences(
        list(strategies), effective_subs_by_sid,
    )
    gcd_ns = data_engine._clock_advance_ns
    aligned = _align_start_to_cadence(int(start_ns), gcd_ns)
    if aligned != int(start_ns):
        clock._start_ns = aligned  # type: ignore[attr-defined]
        clock._now_ns = aligned    # type: ignore[attr-defined]

    _assert_strategy_ids_unique(strategies)

    # Replay driver state — shared between tick iterations so iterators
    # advance monotonically across the loop.
    replay_state: dict = {}

    # Surface the cache + clock on the paper engine so its tick body
    # sees the same hydrated cache the orchestrator populates.
    try:
        engine._market_cache = cache
    except Exception:
        pass
    try:
        engine._clock = clock
    except Exception:
        pass

    # Strategies must be registered into engine config as specs so the
    # tick path's _resolve_protocol_strategy finds them. We attach the
    # native strategies directly; specs carry a ``.build()``-returning
    # stub via the ``strategy_instance`` attribute paper engine
    # checks.
    specs: list = []
    for strat in strategies:
        sid = _strategy_id(strat)
        spec = _PortfolioStrategySpec(strategy_id=sid, market="perp")
        try:
            object.__setattr__(spec, "strategy_instance", strat)
        except Exception:
            pass
        specs.append(spec)
    try:
        engine.config.strategies = specs  # type: ignore[attr-defined]
    except Exception:
        pass

    while clock.now_ns() < int(end_ns):
        data_engine.advance_one_tick()
        bar_idx = int(clock.current_bar_idx)
        _hydrate_cache_up_to_now(
            data_engine, instruments, list(strategies),
            replay_state=replay_state, end_ns=int(end_ns),
            effective_subs_by_sid=effective_subs_by_sid,
        )
        # Advance the paper engine's internal tick counter so its
        # per-tick bookkeeping (tick_counter, _last_tick_ts) aligns.
        # ADR-0001 ship gate: exceptions propagate verbatim. The fail-fast
        # validator (``_validate_all_strategies_are_protocol``) in the paper
        # tick body raises ValueError when a strategy is not BaseStrategy —
        # that signal MUST reach the caller, not be swallowed. Swallowing
        # here makes the AC-2 parity test vacuous (paper never runs
        # strategy.generate, both sides emit zero trades, parity trivially
        # passes). RF-1 rework.
        tick_fn = getattr(engine, "_tick_internal_body", None)
        if callable(tick_fn):
            tick_fn()

    return engine.state if hasattr(engine, "state") else engine
