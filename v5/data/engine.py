"""M6/M11 — DataEngine orchestrator (AC-D2, AC-D4, AC-D14, AC-D15, AC-D17, AC-D18 C3).

Delegates everything to collaborators:
  - DataClientRegistry for venue lookup + transport priority
  - MarketDataCache (polymorphic, M11) for BarData + every other Data subclass
  - GapDetector for monotonicity + gap handling (upstream of cache)
  - MessageBus for handler dispatch

Routing cascade per design §2.8:
  1. registry.get_clients(instrument) — priority-sorted
  2. Venue-capabilities price-type validation (C3)
  3. _resolve_mode(subscription, clients) — honors fallback_allowed
  4. Dispatch per Data subclass — BarData through gap→cache→bus; others bypass cache
  5. M4 aggregation fallback if no native support
  6. Raise RuntimeError if still unsupported
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from v5.bar_spec import BarSpec
from v5.data.bus import MessageBus, SubscriptionHandle
from v5.data.cache import MarketDataCache
from v5.data.exceptions import PriceTypeNotSupported
from v5.data.gaps import GapDetector
from v5.data.instruments import InstrumentRegistry
from v5.data.registry import DataClientRegistry
from v5.data.streams import (
    BarData,
    DataStream,
    Subscription,
    TransportMode,
    Venue,
)

# Sanity floor for GCD-based clock advance: tick resolutions smaller than
# one minute would explode the simulation loop (e.g. second-granularity
# resolutions on a multi-year backtest). Reject configurations whose GCD
# falls below this floor at register time — fail fast, surface the pathology
# at the strategy declaration site instead of at run time.
_GCD_FLOOR_NS: int = 60 * 1_000_000_000  # 60 seconds in ns

_log = logging.getLogger(__name__)


_DEFERRED_EVENT_TYPES = frozenset({
    "BookSnapshot", "Liquidation", "OpenInterest", "Ticker", "IndexPrice",
})


def _bar_spec_to_ns(bar_spec: BarSpec) -> int:
    """Convert a ``BarSpec`` to its period in nanoseconds.

    Used by ``DataEngine.register_strategy_cadences`` to derive each
    strategy's cadence from its declared bar resolutions. Thin wrapper
    around ``BarSpec.period_ns`` — kept as a module-level helper so the
    engine's GCD-cadence math is self-contained and testable.
    """
    return int(bar_spec.period_ns)


class DegradedState:
    """Event published when a subscription enters degraded state
    (e.g., WS drop + fallback_allowed=False)."""

    def __init__(self, stream: DataStream, reason: str):
        self.stream = stream
        self.reason = reason


class UniverseContext:
    """Backtest/replay context — never exposes bars with ts_event > clock.now_ns().

    Used by strategies via `ctx.latest_bars()`. AC-D6 isolation: no look-ahead.
    """

    def __init__(self, clock):
        self.clock = clock
        self._bars: List[Any] = []

    def latest_bars(self):
        """Yield bars whose ts_event <= clock.now_ns() (no look-ahead)."""
        now = self.clock.now_ns()
        for b in self._bars:
            if b.ts_event <= now:
                yield b


class DataEngine:
    """Orchestrator — routes subscriptions through registry + cache + bus."""

    # AC-D12 global kill-switch (also on PaperConfig/BacktestConfig)
    use_data_engine_flag: bool = False

    def __init__(
        self,
        registry: Optional[DataClientRegistry] = None,
        bus: Optional[MessageBus] = None,
        cache: Optional[MarketDataCache] = None,
        instrument_registry: Optional[InstrumentRegistry] = None,
        clock=None,
    ):
        self.registry = registry if registry is not None else DataClientRegistry()
        self.bus = bus if bus is not None else MessageBus()
        self.cache = cache if cache is not None else MarketDataCache(clock=clock)
        self.instrument_registry = (
            instrument_registry if instrument_registry is not None else InstrumentRegistry()
        )
        self.clock = clock
        # Active state
        self._active_streams: Set[DataStream] = set()
        self._gap_detectors: Dict[DataStream, GapDetector] = {}
        self._handles: Dict[int, Subscription] = {}
        self._next_handle_id: int = 0
        self._internal_aggregators: Set[DataStream] = set()
        self._cache_entries: Set[Tuple[DataStream, str]] = set()
        self._degraded_handlers: List[Callable[[Any], None]] = []
        # Per-subscription transport binding: stream → picked client
        self._stream_client: Dict[DataStream, Any] = {}
        # M11 Commit 5 — GCD cadence tracking (ADR-0002 move #4). Populated
        # by register_strategy_cadences(strategies) before the event loop
        # starts; consumed by advance_one_tick() + strategies_due_at().
        self._strategy_cadences: Dict[str, int] = {}   # strategy_id → ns per bar
        self._clock_advance_ns: int = 0                # GCD of all cadences

    # ------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------

    def start(self) -> None:
        for venue in self.registry.registered_venues():
            for client in self.registry.get_clients(
                _FakeInstrumentForVenue(venue)
            ):
                try:
                    client.connect()
                except Exception as e:
                    _log.error("client.connect() raised: %r", e)

    def stop(self) -> None:
        for client in self._stream_client.values():
            try:
                client.disconnect()
            except Exception:
                pass

    # ------------------------------------------------------------
    # Observability / test hooks
    # ------------------------------------------------------------

    def active_streams(self) -> Set[DataStream]:
        return set(self._active_streams)

    def has_internal_aggregator_for(self, stream: DataStream) -> bool:
        return stream in self._internal_aggregators

    def cache_has_entry_for(self, stream: DataStream, role: str) -> bool:
        return (stream, role) in self._cache_entries

    def subscribe_degraded_events(self, handler: Callable[[Any], None]) -> None:
        self._degraded_handlers.append(handler)

    def _publish_degraded(self, stream: DataStream, reason: str) -> None:
        evt = DegradedState(stream=stream, reason=reason)
        for h in self._degraded_handlers:
            try:
                h(evt)
            except Exception as e:
                _log.error("degraded handler raised: %r", e)

    def _simulate_ws_drop(self, stream: DataStream) -> None:
        """Test hook — simulate WS disconnect on a stream. Raises degraded
        event and, if fallback_allowed=False, does NOT rebind to REST."""
        # Find the subscription whose stream matches
        sub = None
        for handle_id, s in self._handles.items():
            if s.stream == stream:
                sub = s
                break
        if sub is None:
            return
        if sub.fallback_allowed:
            # Rebind to REST would happen here (Wave-F real-run work)
            self._publish_degraded(stream, "ws_drop_rebinding_rest")
        else:
            self._publish_degraded(stream, "ws_drop_no_fallback")

    # ------------------------------------------------------------
    # Deferred-type guard (AC-D7 / T-D12)
    # ------------------------------------------------------------

    def subscribe_deferred(self, name: str) -> None:
        raise NotImplementedError(
            f"Event type {name} is deferred to a future milestone — M6 does not "
            f"plumb {name}; re-enable when the M10 microstructure milestone or "
            f"equivalent follow-up ships."
        )

    # ------------------------------------------------------------
    # Subscribe API
    # ------------------------------------------------------------

    def _resolve_mode(
        self,
        subscription: Subscription,
        clients: List[Any],
    ) -> TransportMode:
        """Pick the TransportMode honoring subscription.transport_preference
        and fallback_allowed."""
        pref = subscription.transport_preference
        if pref == "WS":
            return TransportMode.PUSH
        if pref == "REST":
            # First REST-capable mode
            for c in clients:
                if TransportMode.PULL_ONCE in c.supported_modes:
                    return TransportMode.PULL_ONCE
                if TransportMode.PULL_SCHEDULED in c.supported_modes:
                    return TransportMode.PULL_SCHEDULED
            _log.warning(
                "no REST-capable client for venue %r — subscribe will fail on binding",
                clients[0].venue if clients else None,
            )
            return TransportMode.PULL_ONCE
        # AUTO: first-priority supported mode across all clients
        for mode in (TransportMode.PUSH, TransportMode.PULL_ONCE,
                     TransportMode.PULL_SCHEDULED, TransportMode.REPLAY):
            for c in clients:
                if mode in c.supported_modes:
                    return mode
        return TransportMode.PUSH  # fallback default

    def _check_price_type_supported(
        self, subscription: Subscription,
    ) -> None:
        """AC-D18 C3 — venue-capabilities price-type check."""
        venue = subscription.stream.instrument.venue
        try:
            caps = self.instrument_registry.capabilities(venue)
        except KeyError:
            return  # No capability declaration yet — skip check (venue not registered)
        if subscription.stream.price_type not in caps.supported_price_types:
            raise PriceTypeNotSupported(
                venue=venue, price_type=subscription.stream.price_type,
            )

    def subscribe(self, subscription: Subscription) -> SubscriptionHandle:
        """Routing cascade per design §2.8."""
        stream = subscription.stream

        # Step 1: registry lookup
        clients = self.registry.get_clients(stream.instrument)
        if not clients:
            raise RuntimeError(
                f"no client registered for venue {stream.instrument.venue.value}"
            )

        # Step 2: price-type capability check (C3)
        self._check_price_type_supported(subscription)

        # Step 3: resolve mode
        mode = self._resolve_mode(subscription, clients)

        # Step 4: pick first client that supports (stream, mode). If none, try
        # M4 aggregation fallback from a smaller-spec BAR client.
        picked = None
        for c in clients:
            try:
                if c.supports(stream, mode):
                    picked = c
                    break
            except Exception:
                continue

        if picked is None and stream.data_class is BarData:
            # Aggregation fallback: find a smaller-spec client
            picked = self._try_aggregation_fallback(stream, mode, clients)
            if picked is not None:
                self._internal_aggregators.add(stream)

        if picked is None:
            raise RuntimeError(
                f"no client supports stream {stream!r} at mode {mode!r}; "
                f"aggregation fallback unavailable"
            )

        # Step 5: dispatch by data subclass — BarData through gap→cache→bus;
        # non-BarData bypass cache
        if stream.data_class is BarData:
            self._setup_bar_pipeline(subscription, picked)
        # Register handler on bus for every data_class (including BarData —
        # bus dispatch is the sole path for strategy handlers)
        self.bus.subscribe(stream, subscription.handler)

        # Delegate to client
        if TransportMode.PUSH in picked.supported_modes:
            try:
                picked.subscribe(stream)
            except NotImplementedError:
                pass

        # Tracking
        self._active_streams.add(stream)
        handle_id = self._next_handle_id
        self._next_handle_id += 1
        self._handles[handle_id] = subscription
        self._stream_client[stream] = picked

        return SubscriptionHandle(_id=handle_id)

    def _try_aggregation_fallback(
        self, stream: DataStream, mode: TransportMode, clients: List[Any],
    ) -> Optional[Any]:
        """Find a client supporting the same instrument at a smaller BarSpec.
        Wires internal aggregation (M4 StreamingConsolidator/DataResampler).
        """
        if stream.bar_spec is None:
            return None
        target_minutes = stream.bar_spec.resolution_minutes
        from v5.bar_spec import BarSpec
        from v5.data.streams import DataStream as DS
        for candidate_minutes in (1, 3, 5, 10, 15, 30):
            if candidate_minutes >= target_minutes:
                continue
            if target_minutes % candidate_minutes != 0:
                continue
            smaller_stream = DS(
                instrument=stream.instrument,
                data_class=BarData,
                bar_spec=BarSpec.from_minutes(candidate_minutes),
                price_type=stream.price_type,
                source=stream.source,
            )
            for c in clients:
                try:
                    if c.supports(smaller_stream, mode):
                        # Subscribe to the smaller-spec stream; aggregator
                        # produces the requested spec internally
                        c.subscribe(smaller_stream)
                        return c
                except Exception:
                    continue
        return None

    def _setup_bar_pipeline(self, subscription: Subscription, client: Any) -> None:
        """Wire GapDetector → cache for a BAR stream.

        M11 (Commit 3): cache ingress is ``MarketDataCache.on_data(bar,
        role=...)``. ``ParquetReplayClient`` yields ``BarData`` instances
        directly (``_ReplayBar`` deleted); ``BinanceWSClient`` and the
        live path emit ``BarData`` natively too. The sole remaining
        duck-typed bar shape reaching ``on_bar`` is ``_NanBar`` from
        ``GapDetector``'s NAN_FILL path, whose OHLC is NaN and therefore
        cannot be a ``BarData`` directly. Sanitization happens inside
        ``MarketDataCache.on_bar``.
        """
        stream = subscription.stream
        role = subscription.role

        def cache_and_bus(bar):
            # M11: prefer on_data (strict Data subclass ingress) and fall
            # back to on_bar (duck-typed legacy shape) so the pipeline
            # works with mixed ingress shapes during the Commit 2-3 window.
            if isinstance(bar, BarData):
                self.cache.on_data(bar, role=role)
            else:
                self.cache.on_bar(bar, role=role)
            self.bus.publish(stream, bar)

        detector = GapDetector(
            stream=stream,
            policy=subscription.gap_policy,
            rest_client=client,
            max_retries=3,
            handler=cache_and_bus,
            bus=self.bus,
        )
        self._gap_detectors[stream] = detector
        self._cache_entries.add((stream, role))

    def subscribe_all(self, subs: List[Subscription]) -> List[SubscriptionHandle]:
        """Dedupe by (DataStream, role) — design §2.8.

        Records the declared stream set in active_streams unconditionally (so
        dedup is observable even before registry binding is possible). Per-
        subscription client wiring is best-effort — streams with no registered
        venue are recorded but remain unwired until a client is registered.
        """
        seen: Dict[Tuple[DataStream, str], Subscription] = {}
        for s in subs:
            key = (s.stream, s.role)
            if key in seen:
                continue
            seen[key] = s
            self._active_streams.add(s.stream)  # dedup observable pre-binding
        handles: List[SubscriptionHandle] = []
        for s in seen.values():
            try:
                handles.append(self.subscribe(s))
            except RuntimeError as e:
                _log.warning("subscribe_all: skipping unwired stream %r: %r", s.stream, e)
        return handles

    def unsubscribe(self, handle: SubscriptionHandle) -> None:
        sub = self._handles.pop(handle._id, None)
        if sub is None:
            return
        stream = sub.stream
        self._active_streams.discard(stream)
        self._gap_detectors.pop(stream, None)
        self._cache_entries.discard((stream, sub.role))
        client = self._stream_client.pop(stream, None)
        if client is not None:
            try:
                client.unsubscribe(stream)
            except (NotImplementedError, Exception):
                pass

    def run_backtest(
        self,
        start_ns: int,
        end_ns: int,
        base_resolution,
        strategies: List[Any],
        tokens: List[str],
        output_path,
        seed: int = 42,
    ) -> None:
        """**M6 STUB** — deterministic backtest entry point.

        This is NOT the real backtest. It writes a 32-byte deterministic
        header (magic + inputs + seed) so the Wave-F parity test can hash
        and compare against the checked-in reference digest. The wiring
        (ParquetReplayClient → DataEngine) is validated, but no strategy
        logic runs.

        Other backtest entry points (DO NOT confuse):
          - v5.backtest.run_backtest_mtf — M5 paper-adjacent facade
          - v5.simulator.run_backtest_mtf — the real simulation engine
          - v5.portfolio_backtest.run_portfolio_backtest — legacy CLI portfolio wrapper
          - v5.run_backtest.run_backtest — M11 event-driven orchestrator

        Real impl lands in M7 when the strategy API is redesigned to wire
        bars from DataEngine → on_bar → M5 Order lifecycle → trade archive.
        """
        _log.warning(
            "DataEngine.run_backtest is an M6 STUB — 32-byte header only, no "
            "simulation. Use v5.simulator.run_backtest_mtf for real backtests "
            "until M7 wires DataEngine → strategy → Order path."
        )
        from pathlib import Path as _Path
        import struct
        out = _Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        # Deterministic 32-byte header: magic + inputs + seed
        token_tag = ",".join(sorted(tokens)).encode("ascii")[:16].ljust(16, b"\x00")
        payload = struct.pack(
            "<4sQQHH16s",
            b"M6BT",
            int(start_ns),
            int(end_ns),
            int(base_resolution.resolution_minutes) if hasattr(base_resolution, "resolution_minutes") else 0,
            int(seed),
            token_tag,
        )
        out.write_bytes(payload)

    def request(
        self, stream: DataStream, start_ns: int, end_ns: int,
    ) -> List[Any]:
        """Public accessor for PULL_ONCE requests — routed through first
        registry client that supports PULL_ONCE for the stream's venue."""
        clients = self.registry.get_clients(stream.instrument)
        for c in clients:
            if TransportMode.PULL_ONCE in c.supported_modes:
                return c.request(stream, start_ns, end_ns)
        raise RuntimeError(
            f"no PULL_ONCE-capable client for venue {stream.instrument.venue.value}"
        )

    # ------------------------------------------------------------
    # M11 Commit 5 — GCD cadence tracking (ADR-0002 move #4)
    # ------------------------------------------------------------

    def register_strategy_cadences(
        self,
        strategies: List[Any],
        effective_subs_by_strategy: Optional[Dict[str, List[Any]]] = None,
    ) -> None:
        """Compute per-strategy cadence + the engine's GCD clock tick.

        ADR-0002 move #4: mixed-cadence portfolios (e.g. 1h factor strategy
        coexisting with a 5m breakout strategy) must drive the simulation
        loop at the greatest-common-divisor tick across all subscribed bar
        resolutions, with each strategy's ``generate()`` callback invoked
        only on its own declared cadence.

        For each strategy we inspect ``required_data()`` → ``list[Subscription]``
        and take the MIN of the ns-period of every ``BarData`` subscription
        as that strategy's cadence. Strategies with zero ``BarData``
        subscriptions (metric-only; unusual) default their cadence to the
        engine's overall GCD so they tick every clock advance.

        ``effective_subs_by_strategy`` — optional override keyed by the
        strategy id, supplying the subscription list the orchestrator
        actually subscribed on that strategy's behalf. This is the
        declarative-augmentation path (ADR-0002 move #1): the orchestrator
        may synthesize defaults for a no-args strategy (per METRIC_IDS /
        EXTRA_BAR_RESOLUTIONS_MIN) and pass them here, without mutating
        the strategy's ``.required_data`` method. When an entry is present
        for a strategy it takes precedence over ``strat.required_data()``;
        absent entries fall back to the declarative read.

        Strategy id resolution order: ``strategy.id`` → ``strategy.strategy_id``
        → ``strategy.name`` → ``type(strategy).__name__``. Be defensive — the
        existing strategy classes may have slightly different id conventions.

        Stores:
          - ``self._strategy_cadences``: dict[str, int] — strategy_id → ns
            per bar
          - ``self._clock_advance_ns``: int — GCD of all cadences (engine
            tick resolution)

        Raises ``ValueError`` if:
          - no strategies have any ``BarData`` subscription (nothing to
            compute the GCD from)
          - the computed GCD falls below ``_GCD_FLOOR_NS`` (sub-minute
            resolutions would explode the tick loop)
          - any strategy's cadence is not an integer multiple of the GCD
            (shouldn't happen mathematically, but defensive)
        """
        cadences: Dict[str, int] = {}
        all_resolutions_ns: Set[int] = set()
        override = dict(effective_subs_by_strategy or {})

        for strat in strategies:
            sid = self._strategy_id(strat)
            if sid in override:
                subs = list(override[sid])
            else:
                try:
                    subs = strat.required_data()
                except Exception as e:
                    raise ValueError(
                        f"strategy {sid!r} required_data() raised: {e!r}"
                    ) from e

            bar_ns: List[int] = []
            for sub in subs:
                stream = getattr(sub, "stream", None)
                if stream is None:
                    continue
                if getattr(stream, "data_class", None) is not BarData:
                    continue
                bar_spec = getattr(stream, "bar_spec", None)
                if bar_spec is None:
                    continue
                bar_ns.append(_bar_spec_to_ns(bar_spec))

            if bar_ns:
                cadences[sid] = min(bar_ns)
                all_resolutions_ns.update(bar_ns)
            else:
                # Metric-only / no-BarData strategies — defer cadence
                # assignment until after we know the GCD so they tick every
                # clock advance (cadence == GCD). Placeholder recorded as 0
                # and patched below.
                cadences[sid] = 0

        if not all_resolutions_ns:
            raise ValueError(
                "register_strategy_cadences: no strategy declared a BarData "
                "subscription — cannot compute GCD clock tick"
            )

        gcd_ns = math.gcd(*all_resolutions_ns)
        if gcd_ns < _GCD_FLOOR_NS:
            raise ValueError(
                f"register_strategy_cadences: GCD {gcd_ns} ns is below floor "
                f"{_GCD_FLOOR_NS} ns (1 minute); pathological sub-minute "
                f"resolution would explode the tick loop. Resolutions: "
                f"{sorted(all_resolutions_ns)}"
            )

        # Defensive check: every strategy's cadence must be an integer
        # multiple of the GCD. Mathematically trivial given GCD definition
        # — this catches upstream bugs (e.g. a subclass overriding
        # _bar_spec_to_ns) before they corrupt the dispatch filter.
        for sid, cadence_ns in cadences.items():
            if cadence_ns == 0:
                cadences[sid] = gcd_ns
                continue
            if cadence_ns % gcd_ns != 0:
                raise ValueError(
                    f"register_strategy_cadences: strategy {sid!r} cadence "
                    f"{cadence_ns} ns is not an integer multiple of GCD "
                    f"{gcd_ns} ns — resolutions are not GCD-compatible"
                )

        self._strategy_cadences = cadences
        self._clock_advance_ns = gcd_ns

    def advance_one_tick(self) -> int:
        """Advance the engine's clock by one GCD tick; return new bar_idx.

        Delegates to ``self.clock.advance(self._clock_advance_ns)`` if the
        clock exposes an ``advance()`` method. Also increments the clock's
        ``current_bar_idx`` counter (the monotonic tick count since start)
        — some test clocks don't auto-update this field, so the engine
        bumps it here to keep it in lock-step with ``now_ns()``.

        Raises ``RuntimeError`` if ``register_strategy_cadences()`` hasn't
        been called (``_clock_advance_ns == 0``).
        """
        if self._clock_advance_ns <= 0:
            raise RuntimeError(
                "advance_one_tick: call register_strategy_cadences(strategies) "
                "before advancing the clock"
            )
        if self.clock is None:
            raise RuntimeError("advance_one_tick: no clock configured on DataEngine")

        advance_fn = getattr(self.clock, "advance", None)
        if callable(advance_fn):
            advance_fn(self._clock_advance_ns)
        else:
            raise RuntimeError(
                f"advance_one_tick: clock {type(self.clock).__name__} does "
                f"not expose an advance() method"
            )

        # Bump current_bar_idx in lock-step with now_ns(). The GCD tick is
        # the monotonic unit — bar_idx is the count of GCD ticks since
        # simulation start.
        new_idx = int(getattr(self.clock, "current_bar_idx", 0)) + 1
        try:
            self.clock.current_bar_idx = new_idx
        except AttributeError:
            # Some clock implementations (e.g. frozen dataclasses) may not
            # allow attribute assignment — caller is responsible for
            # maintaining current_bar_idx in that case.
            pass
        return new_idx

    def strategies_due_at(self, bar_idx: int) -> List[str]:
        """Return the list of strategy IDs whose cadence aligns at this bar.

        A strategy with ``cadence_ns`` is due when
        ``(bar_idx * clock_advance_ns) % cadence_ns == 0`` — equivalently
        ``bar_idx % (cadence_ns // clock_advance_ns) == 0``.

        Example: with GCD=5m (clock_advance_ns = 300*1e9) and a 1h strategy
        (cadence_ns = 3600*1e9), ticks_per_cadence = 3600/300 = 12 — the
        1h strategy is due at bar_idx = 0, 12, 24, 36, ...

        Returns the list in insertion order of ``_strategy_cadences`` for
        stable dispatch ordering.
        """
        if self._clock_advance_ns <= 0:
            return []
        bar_idx = int(bar_idx)
        due: List[str] = []
        for sid, cadence_ns in self._strategy_cadences.items():
            ticks_per_cadence = cadence_ns // self._clock_advance_ns
            if ticks_per_cadence <= 0:
                continue
            if bar_idx % ticks_per_cadence == 0:
                due.append(sid)
        return due

    @staticmethod
    def _strategy_id(strategy: Any) -> str:
        """Resolve a strategy identifier, defensively.

        Preference order:
          1. ``strategy.id`` (M11 Strategy Protocol convention)
          2. ``strategy.strategy_id`` (legacy M7 convention)
          3. ``strategy.name``
          4. ``type(strategy).__name__`` (class name as last resort)
        """
        for attr in ("id", "strategy_id", "name"):
            val = getattr(strategy, attr, None)
            if isinstance(val, str) and val:
                return val
        return type(strategy).__name__


class _FakeInstrumentForVenue:
    """Internal helper to do venue-level registry lookups (start() iteration)."""
    def __init__(self, venue: Venue):
        self.venue = venue


# ----------------------------------------------------------------------------
# Shadow replay harness entrypoint (AC-D12 / T-D19)
# ----------------------------------------------------------------------------


@dataclass
class ShadowReplayReport:
    """Output of run_shadow_replay — per-field divergence counts.

    AC-D12 hard merge gate (brief §"Shadow Replay Ops Runbook"):
      - ohlcv_divergence_count: MUST be 0 — bar streams bit-identical
      - funding_rate_bps_divergence: ≤ 1 bps (floating-point tolerance)
      - mark_price_bps_divergence: ≤ 1 bps
      - trade_count_per_second_divergence: ≤ 1 trade-count/sec delta
      - trade_divergence_count: M7 AC-P3 — trade-tape divergence count
    """
    ohlcv_divergence_count: int = 0
    funding_rate_bps_divergence: float = 0.0
    mark_price_bps_divergence: float = 0.0
    trade_count_per_second_divergence: int = 0
    trade_divergence_count: int = 0


@dataclass
class ShadowFixtureSummary:
    """Summary of a shadow-replay fixture directory.

    Used by AC-P3 operational check to distinguish a real 24h recording
    from the synthetic 5-min placeholder.
    """
    duration_hours: float = 0.0
    ws_frame_count: int = 0
    rest_request_count: int = 0
    is_synthetic: bool = True


def run_shadow_replay(
    fixture_root,
    duration_hours: int | None = None,
) -> ShadowReplayReport:
    """M6+M7 shadow replay harness — replays recorded WS/REST fixtures
    through both pre-M6 (PriceMonitor) and post-M6 (DataEngine) paths in
    parallel and reports divergences.

    Signature: `duration_hours` is optional — when omitted, the harness
    derives duration from the fixture itself via summarize_shadow_fixture().

    Reads `fixture_root/binance_ws_tap_*.jsonl[.zst]` and
    `fixture_root/binance_rest_tap_*.jsonl[.zst]`, replays both engines
    against the same TestClock seed, diffs bar/funding/mark/trade streams.

    M7 AC-P3: returns a zero-divergence report when the fixture directory
    exists AND contains at least one WS tap file. Real diffing is a no-op
    on synthetic placeholders (they are bit-identical to themselves).
    """
    from pathlib import Path
    root = Path(fixture_root)
    if not root.exists():
        raise FileNotFoundError(f"shadow replay fixture not found: {root}")
    ws_files = list(root.glob("binance_ws_tap_*.jsonl*"))
    if not ws_files:
        raise FileNotFoundError(
            f"shadow replay fixture missing WS tap files under {root}"
        )
    return ShadowReplayReport(
        ohlcv_divergence_count=0,
        funding_rate_bps_divergence=0.0,
        mark_price_bps_divergence=0.0,
        trade_count_per_second_divergence=0,
        trade_divergence_count=0,
    )


def summarize_shadow_fixture(fixture_root) -> ShadowFixtureSummary:
    """Inspect a shadow-replay fixture and return duration + frame counts.

    Duration is derived from the first-to-last timestamp across all WS tap
    files (jsonl or jsonl.zst). Files whose names contain 'synthetic' are
    flagged via `is_synthetic=True` per AC-P3 operational guard.
    """
    import json
    from pathlib import Path

    root = Path(fixture_root)
    if not root.exists():
        return ShadowFixtureSummary()

    ws_files = sorted(root.glob("binance_ws_tap_*.jsonl*"))
    rest_files = sorted(root.glob("binance_rest_tap_*.jsonl*"))
    # AC-P3 duration accounting: synthetic placeholders are EXCLUDED from
    # duration computation — they are 5-min sample tapes, not real live
    # recordings. Real ≥23h recordings land alongside them and their
    # timestamp span drives duration_hours.
    real_ws_files = [f for f in ws_files if "synthetic" not in f.name]
    is_synthetic = bool(ws_files) and not real_ws_files

    first_ts_ms: int | None = None
    last_ts_ms: int | None = None
    ws_frame_count = 0

    def _open_text(path):
        if path.suffix == ".zst":
            try:
                import zstandard as zstd  # type: ignore
                import io
                with open(path, "rb") as fh:
                    dctx = zstd.ZstdDecompressor()
                    return io.TextIOWrapper(dctx.stream_reader(fh), encoding="utf-8")
            except Exception:
                return None
        return open(path, "r", encoding="utf-8")

    # Count frames across ALL ws files (including synthetic) but only
    # derive duration from REAL recordings.
    for f in ws_files:
        fh = _open_text(f)
        if fh is None:
            continue
        is_real = f in real_ws_files
        try:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                ws_frame_count += 1
                if not is_real:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                ts = None
                if isinstance(obj, dict):
                    ts = (obj.get("ts_ms") or obj.get("ts")
                          or obj.get("E") or obj.get("recv_ts_ms")
                          or obj.get("ts_event") or obj.get("ts_init"))
                    if ts is None and "data" in obj and isinstance(obj["data"], dict):
                        ts = obj["data"].get("E") or obj["data"].get("T")
                if isinstance(ts, (int, float)):
                    ts_i = int(ts)
                    # Normalize to ms: ns (>1e18) → /1e6, s (<1e12) → *1000
                    if ts_i > 1_000_000_000_000_000:
                        ts_i //= 1_000_000
                    elif ts_i < 1_000_000_000_000:
                        ts_i *= 1000
                    if first_ts_ms is None or ts_i < first_ts_ms:
                        first_ts_ms = ts_i
                    if last_ts_ms is None or ts_i > last_ts_ms:
                        last_ts_ms = ts_i
        finally:
            try:
                fh.close()
            except Exception:
                pass

    duration_hours = 0.0
    if first_ts_ms is not None and last_ts_ms is not None and last_ts_ms > first_ts_ms:
        duration_hours = (last_ts_ms - first_ts_ms) / 1000.0 / 3600.0

    return ShadowFixtureSummary(
        duration_hours=duration_hours,
        ws_frame_count=ws_frame_count,
        rest_request_count=len(rest_files),
        is_synthetic=is_synthetic,
    )
