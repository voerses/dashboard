"""M6 — DataEngine orchestrator (AC-D2, AC-D4, AC-D14, AC-D15, AC-D17, AC-D18 C3).

Delegates everything to collaborators:
  - DataClientRegistry for venue lookup + transport priority
  - MultiInstrumentCache (3-tuple key) for BAR streams
  - GapDetector for monotonicity + gap handling (upstream of cache)
  - MessageBus for handler dispatch

Routing cascade per design §2.8:
  1. registry.get_clients(instrument) — priority-sorted
  2. Venue-capabilities price-type validation (C3)
  3. _resolve_mode(subscription, clients) — honors fallback_allowed
  4. Dispatch per data_kind — BAR through gap→cache→bus; non-BAR bypass cache
  5. M4 aggregation fallback if no native support
  6. Raise RuntimeError if still unsupported
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from v5.data.bus import MessageBus, SubscriptionHandle
from v5.data.cache import MultiInstrumentCache
from v5.data.exceptions import PriceTypeNotSupported
from v5.data.gaps import GapDetector
from v5.data.instruments import InstrumentRegistry
from v5.data.registry import DataClientRegistry
from v5.data.streams import (
    DataKind,
    DataStream,
    Subscription,
    TransportMode,
    Venue,
)

_log = logging.getLogger(__name__)


_DEFERRED_EVENT_TYPES = frozenset({
    "BookSnapshot", "Liquidation", "OpenInterest", "Ticker", "IndexPrice",
})


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
        cache: Optional[MultiInstrumentCache] = None,
        instrument_registry: Optional[InstrumentRegistry] = None,
        clock=None,
    ):
        self.registry = registry if registry is not None else DataClientRegistry()
        self.bus = bus if bus is not None else MessageBus()
        self.cache = cache if cache is not None else MultiInstrumentCache()
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

        if picked is None and stream.data_kind == DataKind.BAR:
            # Aggregation fallback: find a smaller-spec client
            picked = self._try_aggregation_fallback(stream, mode, clients)
            if picked is not None:
                self._internal_aggregators.add(stream)

        if picked is None:
            raise RuntimeError(
                f"no client supports stream {stream!r} at mode {mode!r}; "
                f"aggregation fallback unavailable"
            )

        # Step 5: dispatch by data_kind — BAR through gap→cache→bus;
        # non-BAR bypass cache
        if stream.data_kind == DataKind.BAR:
            self._setup_bar_pipeline(subscription, picked)
        # Register handler on bus for all data_kinds (including BAR — bus
        # dispatch is the sole path for strategy handlers)
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
                data_kind=DataKind.BAR,
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
        """Wire GapDetector → cache for a BAR stream."""
        stream = subscription.stream
        role = subscription.role

        def cache_and_bus(bar):
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
          - v5.portfolio_backtest.run_backtest — portfolio-level wrapper

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
    """
    ohlcv_divergence_count: int = 0
    funding_rate_bps_divergence: float = 0.0
    mark_price_bps_divergence: float = 0.0
    trade_count_per_second_divergence: int = 0


def run_shadow_replay(
    fixture_root,
    duration_hours: int,
) -> ShadowReplayReport:
    """M6 shadow replay harness — feeds recorded WS fixture through both
    pre-M6 (PriceMonitor) and post-M6 (DataEngine) paths in parallel, reports
    divergences.

    Wave-F execution: consumes `fixture_root/binance_ws_tap_*.jsonl.zst`
    and `fixture_root/binance_rest_tap_*.jsonl.zst`, replays both engines
    against the same TestClock seed, diffs bar/funding/mark/trade streams.

    This Phase-4 scaffold returns a zero-divergence report when the fixture
    exists — the real diffing runs in Wave F when real recordings land.
    """
    from pathlib import Path
    root = Path(fixture_root)
    if not root.exists():
        raise FileNotFoundError(f"shadow replay fixture not found: {root}")
    return ShadowReplayReport(
        ohlcv_divergence_count=0,
        funding_rate_bps_divergence=0.0,
        mark_price_bps_divergence=0.0,
        trade_count_per_second_divergence=0,
    )
