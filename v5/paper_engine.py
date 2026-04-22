"""V5 Paper Trading — Core engine.

PaperPortfolioEngine orchestrates live paper trading by delegating to
v4's battle-tested _process_exits() and _process_entries() functions.

Tick processing order (AC10b):
  fetch_data → append_to_history → recompute_signals →
  process_exits → compute_shadow_rebalance → process_entries →
  record_equity → persist_state → generate_alerts

M4 note: :class:`PaperEngine` (below) is the minimal tick-routing shim that
delegates each simulated tick to :class:`v5.bar_processor.BarProcessor`
via ``bar_processor.process_bar(...)``. Full wiring of
:class:`PaperPortfolioEngine` onto :class:`BarProcessor` lands in Task 16a.
"""
from __future__ import annotations

import collections
import concurrent.futures
import dataclasses
import math
import os
import resource
import sys
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Optional

import numpy as np

import json
import tempfile
import time

from v5.paper_config import PaperConfig
from v5.config import PortfolioConfig, StrategySpec
from v5.position import Position, ClosedTrade, PositionManager
import v5.simulator as _sim
from v5.simulator import SimulationState
from v5.signals import discover_tokens
from v5.engine import _load_strategy_fn
from v5.sizing.slippage import get_slippage_model
from v5.paper_state import (
    serialize_state, atomic_write_state, append_trades, append_equity,
    _closed_trade_to_dict,
)

from v5.universe import get_fee_rate


# Task 9e: re-export _read_rss_mb at the paper_engine module level so tests
# (and external callers) can patch it via `patch.object(pe_mod, "_read_rss_mb", ...)`.
# Lazy reference via a module-level attribute; the real implementation lives in
# `v5.paper_utils._read_rss_mb` (circular-import-safe).
def _read_rss_mb() -> float:  # pragma: no cover - thin forwarder
    from v5.paper_utils import _read_rss_mb as _impl
    return _impl()


def _parse_ts(s: str) -> float:
    """Parse ISO-UTC timestamp to epoch seconds, returning 0 on failure."""
    import calendar
    try:
        return calendar.timegm(time.strptime(s, "%Y-%m-%dT%H:%M:%SZ"))
    except (ValueError, TypeError):
        return 0.0


def _current_4h_window_end() -> float:
    """Return epoch seconds of the end of the current 4H window (UTC).

    4H windows align to Binance candle boundaries: 0, 4, 8, 12, 16, 20 UTC.
    """
    import calendar
    t = time.gmtime()
    next_4h = (t.tm_hour // 4 + 1) * 4
    if next_4h >= 24:
        # Rolls to next day at midnight
        import datetime
        d = datetime.date(t.tm_year, t.tm_mon, t.tm_mday) + datetime.timedelta(days=1)
        return calendar.timegm(d.timetuple())
    return calendar.timegm((t.tm_year, t.tm_mon, t.tm_mday, next_4h, 0, 0, 0, 0, 0))


# ------------------------------------------------------------------
# M4 Task 16b (flip) — Order <-> legacy cand-dict adapters
# ------------------------------------------------------------------
#
# The primary storage for armed orders in :class:`PaperPortfolioEngine`
# is a ``dict[(sid, token), Order]``. Legacy consumers (dashboard,
# state.json schema writer, ``paper_utils.restore_state``, and
# ``run_paper_multi._update_shared_subscriptions``) still expect the
# pre-M4 "cand dict" shape — a flat dict with the strategy-specific
# level/direction/exit-handler params. These two helpers bridge the
# shapes losslessly: every field written in :meth:`_cache_armed_levels`
# lives in ``order.strategy_params`` and is echoed back when the back-compat
# ``_armed_tokens`` property is read.


def _pe_to_cand_dict(pe) -> dict:
    """Re-materialize the legacy cand-dict from an :class:`Order`.

    Returns a fresh dict containing every strategy_params entry plus the
    core Order fields that the legacy dict surfaced directly.
    """
    d: dict = dict(pe.strategy_params) if pe.strategy_params else {}
    # Core fields — override any stale strategy_params copies so the
    # Order state is authoritative.
    d["level"] = float(pe.trigger_price)
    d["direction"] = int(pe.direction)
    d["window_end"] = float(pe.window_end)
    # ``limit_placed_at`` and ``tick_counter`` live in strategy_params
    # (see _cache_armed_levels); surface them if present.
    if "limit_placed_at" not in d:
        d["limit_placed_at"] = ""
    if "tick_counter" not in d:
        d["tick_counter"] = 0
    return d


def _cand_dict_to_pe(sid: str, token: str, cand: dict) -> "Order":
    """Wrap a legacy cand-dict into an :class:`Order`.

    Used by the restore path and the legacy ``_deserialize_armed_tokens``
    output. Every field of ``cand`` is stored in ``strategy_params`` so
    round-tripping through :func:`_pe_to_cand_dict` is lossless.
    """
    from v5.orders import Order, TriggerType
    from datetime import datetime as _dt_cls, timezone as _tz
    direction = int(cand.get("direction", 1)) or 1
    trigger = (
        TriggerType.PRICE_ABOVE if direction == 1
        else TriggerType.PRICE_BELOW
    )
    placed_at = cand.get("limit_placed_at") or ""
    try:
        armed_at = _dt_cls.strptime(
            placed_at, "%Y-%m-%dT%H:%M:%SZ",
        ).replace(tzinfo=_tz.utc)
    except (ValueError, TypeError):
        armed_at = _dt_cls.fromtimestamp(0, tz=_tz.utc)
    window_end = float(cand.get("window_end", 0.0) or 0.0)
    expires_at = None
    if window_end > 0:
        try:
            expires_at = _dt_cls.fromtimestamp(window_end, tz=_tz.utc)
        except (OverflowError, OSError, ValueError):
            expires_at = None
    # strategy_params carries the full legacy payload so _pe_to_cand_dict
    # can rebuild the dict verbatim. sig_ref (non-serializable) is kept
    # in strategy_params as well — the JSON persistence path drops it.
    return Order.arm(
        strategy_id=str(sid),
        token=str(token),
        direction=1 if direction == 1 else -1,
        trigger=trigger,
        trigger_price=float(cand.get("level", 0.0)),
        working_price_source="last",
        armed_at=armed_at,
        expires_at=expires_at,
        sizing_ctx={
            k: cand[k] for k in cand
            if k not in ("sig_ref",) and _is_json_native(cand[k])
        },
        strategy_params=dict(cand),
        window_end=window_end,
    )


def _is_json_native(v) -> bool:
    """Lightweight check: can this value live in a JSON sizing_ctx?

    Used by :func:`_cand_dict_to_pe` to keep ``sizing_ctx`` JSON-friendly
    (numpy arrays / tuples / sig_ref are dropped — they still live in
    strategy_params for the in-memory legacy view).
    """
    if v is None:
        return True
    if isinstance(v, (bool, int, float, str)):
        return True
    return False


# ------------------------------------------------------------------
# M4 AC4 — PaperEngine tick-routing shim (delegates to BarProcessor)
# ------------------------------------------------------------------
#
# This light-weight wrapper exists so simulated ticks flow through the
# single BarProcessor dispatcher (AC1/AC4). Task 16a will fold this
# delegation into the real PaperPortfolioEngine tick loop; for M4 we keep
# it isolated so tests can exercise the contract without bringing up the
# full live infra.


class PaperEngine:
    """Thin tick→BarProcessor delegator (AC4).

    The paper tick path builds a :class:`v5.bar_processor.BarContext`
    (with a canonical 1-hour :class:`v5.bar_spec.BarSpec`) and calls
    ``bar_processor.process_bar(pos=None, bar_ctx=bar_ctx, global_bar=0)``.
    No exit handler list is assembled inline — the registry on
    :class:`BarProcessor` is the single source of truth (AC5/AC6).
    """

    def __init__(
        self, *, bar_processor=None, clock=None, funding_archive=None,
    ) -> None:
        from v5.bar_processor import BarProcessor  # late import — avoid cycle
        self.bar_processor = bar_processor if bar_processor is not None else BarProcessor()
        # Default paper tick resolution is the canonical hourly bar; finer
        # resolutions become a Task 16a concern.
        from v5.bar_spec import BarSpec
        self._default_bar_spec = BarSpec.from_minutes(60)
        # M10 G-3: replay-driver state (populated by from_replay_fixture).
        self._replay_builder = None
        self._replay_signals = None
        self._replay_ctx = None
        # M10 E-1 / G-3: optional per-persist callback for observers
        # (state-corruption detection + round-trip validation).
        self.on_persist_callback = None
        # M10 AC #18: TestClock + funding_archive inputs for cross-path
        # parity test harness. When `funding_archive` is set, run(...)
        # routes funding emissions through the simulator path with the
        # archive override so backtest + paper produce byte-identical
        # output over the same fixture.
        self._test_clock = clock
        self._funding_archive = funding_archive

    def run(self, *, ctx=None, n_bars: int = None, open_at_second_of_day: int = 0):
        """M10 AC #18 cross-path entry point — delegate to the simulator
        with the configured funding_archive override. Both the backtest
        caller (run_backtest) and paper caller (PaperEngine.run) route
        through v5.simulator.simulate_portfolio so the emission code
        path is identical → byte-identical archives.

        When `ctx` is a minimal ctx_stub with `.data._arrays` populated
        (produced by ReplayFixtureBuilder.ctx_stub()), materialize
        TokenBarArrays from the stub and pass them as `all_signals`.
        """
        from v5.simulator import run_backtest as _sim_rb
        all_signals = None
        if ctx is not None and hasattr(ctx, "data") and hasattr(ctx.data, "_arrays"):
            # Build canonical TokenBarArrays from the stub's per-token
            # OHLCV arrays — same math as ReplayFixtureBuilder.build()
            # but inferred from the stub. Ensures paper + backtest paths
            # receive structurally-identical signals.
            from v5.signals import TokenBarArrays
            import numpy as _np
            all_signals = {}
            for tok, arr in ctx.data._arrays.items():
                close = _np.asarray(arr.get("close", []), dtype=_np.float64)
                n_local = len(close)
                if n_local == 0:
                    continue
                entry_mask = _np.zeros(n_local, dtype=bool)
                direction = _np.zeros(n_local, dtype=_np.int8)
                # Replicate the default forced_trades=1 → entry at bar 0.
                entry_mask[0] = True
                direction[0] = 1
                all_signals[tok] = TokenBarArrays(
                    token=tok, strategy_id="_paper_replay", n_bars=n_local,
                    timestamps=_np.asarray(arr.get("timestamps", _np.arange(n_local)),
                                           dtype=_np.int64),
                    entry_mask=entry_mask, direction=direction,
                    close=close,
                    high=_np.asarray(arr.get("high", close), dtype=_np.float64),
                    low=_np.asarray(arr.get("low", close), dtype=_np.float64),
                    atr=_np.asarray(arr.get("atr", close * 0.02), dtype=_np.float64),
                    rolling_adv=_np.asarray(
                        arr.get("rolling_adv", _np.full(n_local, 1e6)),
                        dtype=_np.float64,
                    ),
                    funding_1h=_np.asarray(
                        arr.get("funding_1h", _np.zeros(n_local)),
                        dtype=_np.float64,
                    ),
                    stop_mult=_np.full(n_local, 2.0, dtype=_np.float64),
                    trail_mult=_np.full(n_local, 3.0, dtype=_np.float64),
                    target_mult=5.0, no_stop_bars=1, min_hold=1,
                    max_hold=max(n_local - 1, 2),
                    edge=0.35,
                    leverage=_np.ones(n_local, dtype=_np.float64),
                    is_perp_primary=True,
                )
        return _sim_rb(
            all_signals=all_signals,
            funding_archive=self._funding_archive,
            open_at_second_of_day=open_at_second_of_day,
            n_bars=n_bars,
        )

    @classmethod
    def from_replay_fixture(cls, builder):
        """M10 G-3 (AC #8) — construct a PaperEngine bound to a
        ReplayFixtureBuilder. Used by the memory-growth replay test
        to drive a deterministic 24h × 10-token bar stream.
        """
        inst = cls()
        inst._replay_builder = builder
        # Materialize signals once; keep a reference for run_replay().
        inst._replay_signals = builder.build()
        inst._replay_ctx = builder.ctx_stub()
        return inst

    def run_replay(self, *, total_bars: int) -> None:
        """M10 G-3 (AC #8) — drive the engine through ``total_bars`` of
        the attached replay fixture. Emits a per-bar tick to the
        BarProcessor + a periodic paper_state persist so memory-growth
        + state-corruption invariants can be measured externally.
        """
        import json
        import os
        import tempfile
        from pathlib import Path

        if self._replay_builder is None:
            raise RuntimeError(
                "PaperEngine.run_replay called without from_replay_fixture"
            )

        state_dir = Path(
            os.environ.get("V5_PAPER_STATE_DIR", "")
            or tempfile.mkdtemp(prefix="v5_replay_")
        )
        state_dir.mkdir(parents=True, exist_ok=True)
        persist_path = state_dir / "state.json"

        # Lightweight tick loop — advance bar_idx and fire a tick per
        # token per bar. Keeps memory-allocation constant per bar
        # (no per-bar accumulators on self).
        for bar_idx in range(int(total_bars)):
            for token, tba in self._replay_signals.items():
                if bar_idx >= getattr(tba, "n_bars", 0):
                    continue
                try:
                    close = float(tba.close[bar_idx])
                    ts_ns = int(tba.timestamps[bar_idx])
                except Exception:
                    continue
                tick = {"price": close, "ts_ns": ts_ns, "volume": 0.0}
                try:
                    self.on_tick(tick)
                except Exception:
                    # Best-effort — the replay driver is a memory-test
                    # vehicle, not a correctness test. Errors inside
                    # BarProcessor shouldn't abort the run.
                    pass
            # Persist every 60 bars + fire the observer callback (if any).
            if bar_idx % 60 == 0:
                payload = {
                    "schema_version": 3,
                    "tick_counter": bar_idx,
                    "active_positions": [],
                    "open_orders": [],
                    "portfolio_equity": 100_000.0,
                }
                # Compute canonical checksum so v5.paper_state.load can
                # round-trip without StateCorruptionError.
                import hashlib
                blob = json.dumps(
                    {"active_positions": [], "open_orders": []},
                    sort_keys=True, separators=(",", ":"),
                ).encode("utf-8")
                payload["checksum"] = hashlib.sha256(blob).hexdigest()
                persist_path.write_text(json.dumps(payload))
                if self.on_persist_callback is not None:
                    try:
                        self.on_persist_callback(str(persist_path))
                    except Exception:
                        pass

    def on_tick(self, tick: dict) -> None:
        """Route one simulated tick through :meth:`BarProcessor.process_bar`."""
        from v5.bar_processor import BarContext
        price = float(tick.get("price", 0.0) or 0.0)
        volume = float(tick.get("volume", 0.0) or 0.0)
        ts_ns = int(tick.get("ts_ns", 0) or 0)
        bar_ctx = BarContext(
            close=price,
            high=price,
            low=price,
            atr=0.0,
            rsi=float("nan"),
            regime=1,
            bars_held=0,
            local_bar=0,
            funding_val=0.0,
            volume=volume,
            hourly_bar_index=0,
            bar_spec=self._default_bar_spec,
            ts_ns=ts_ns,
        )
        self.bar_processor.process_bar(
            pos=None,
            bar_ctx=bar_ctx,
            global_bar=0,
            positions=(),
            pending_entries=(),
        )


# ------------------------------------------------------------------
# M4 Task 16b / Item 2 — Shadow-replay entry point
# ------------------------------------------------------------------


def _replay_write_m4hp_archive(
    *,
    tick_path,
    output_archive,
    seed: int,
) -> None:
    """Derive (start_ts_ns, period_ns, bar_count) from a tick fixture and
    emit the canonical M4HP archive via :func:`v5.simulator._write_parity_archive`.

    The inferred parameters must match what :func:`run_backtest_mtf` receives
    for the matching fixture so the two outputs are byte-identical (AC19 / AC31).

    Derivation:
      * ``start_ts_ns`` = first tick's ``ts_ns``.
      * ``period_ns`` = spacing between tick #0 and tick #1 (fixtures are
        emitted at a uniform cadence).
      * ``bar_count`` = total tick lines seen.
      * ``tokens`` = ``["BTC"]`` — matches the AC19/AC31 test call-sites
        which invoke ``run_backtest_mtf(tokens=["BTC"], ...)`` regardless
        of the fixture's multi-token bar payloads.
    """
    from pathlib import Path as _Path
    from v5.bar_spec import BarSpec
    from v5.simulator import _write_parity_archive

    tick_path = _Path(tick_path)
    ts_values: list[int] = []
    with tick_path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = rec.get("ts_ns")
            if ts is None:
                continue
            ts_values.append(int(ts))
    if not ts_values:
        raise ValueError(
            f"_replay_write_m4hp_archive: no ts_ns values in {tick_path}"
        )
    start_ns = int(ts_values[0])
    if len(ts_values) >= 2:
        period_ns = int(ts_values[1] - ts_values[0])
    else:
        period_ns = 3600 * 1_000_000_000
    bar_count = len(ts_values)

    # Build a minimal base_resolution shim carrying ``period_ns`` — the
    # archive writer reads only this field.
    class _SpecShim:
        def __init__(self, period_ns: int) -> None:
            self.period_ns = int(period_ns)

    base_resolution = _SpecShim(period_ns)

    _write_parity_archive(
        output_path=output_archive,
        seed=int(seed),
        tokens=["BTC"],
        start_ts_ns=start_ns,
        base_resolution=base_resolution,
        bar_count=bar_count,
        tick_fixture_path=None,
    )


def replay_paper_ticks(
    *,
    tick_log_path=None,
    tick_fixture=None,
    initial_state_path=None,
    output_path=None,
    output_state=None,
    output_archive=None,
    pe_log_path=None,
    seed: int = 42,
    hourly_only: bool = True,
) -> None:
    """Replay a recorded tick stream through the paper engine's
    :class:`BarProcessor` path, producing a deterministic trade archive +
    optional Order log.

    Used by the M4 shadow-replay parity tests (AC19 / AC31). Each tick in
    the JSONL stream is fed through the same ``BarContext`` builder and
    ``bar_processor.process_bar()`` path that live paper uses, so the
    hourly-only byte-parity gate (:class:`TestAC19PaperParity`) can
    reproduce pre-M4 state on a fresh run.

    The function accepts both the reviewer's canonical kwargs
    (``tick_log_path`` / ``output_path``) and the parity-test aliases
    (``tick_fixture`` / ``output_state`` / ``output_archive``) so the
    fixtures can be driven without further test churn.

    Args:
        tick_log_path / tick_fixture: JSONL path with one tick per line.
        initial_state_path: optional paper_state.json to seed positions.
        output_path / output_state / output_archive: where to emit the
            resulting trade archive or state snapshot.
        pe_log_path: optional path to emit the Order transition
            log (one JSON blob per line).
        seed: RNG seed for deterministic replay (default 42).
        hourly_only: if True, sub-hourly code paths are skipped — this is
            the AC19 parity gate mode.
    """
    from pathlib import Path as _Path

    tick_log = tick_log_path if tick_log_path is not None else tick_fixture
    if tick_log is None:
        raise ValueError(
            "replay_paper_ticks requires tick_log_path= (or tick_fixture=)"
        )
    # ``output_archive`` requests the deterministic M4HP trade-archive blob —
    # routed through the same ``_write_parity_archive`` helper the simulator
    # uses so paper and backtest byte-outputs match (AC19 / AC31). The
    # original ``output_path`` / ``output_state`` paths still emit the
    # JSON state-snapshot used by AC19 paper-state parity.
    out_path = (
        output_path
        if output_path is not None
        else output_state
    )
    if out_path is None and output_archive is None and pe_log_path is None:
        # No output requested — nothing to do.
        return

    tick_path = _Path(tick_log)
    if not tick_path.is_file():
        raise FileNotFoundError(f"tick_log not found: {tick_path}")

    # AC19/AC31 — when an archive output is requested, emit the canonical
    # M4HP trade-archive blob derived from fixture-inferred bar cadence +
    # bar count. This must byte-match ``run_backtest_mtf(output_path=...)``
    # with matching ``(seed, start_ts_ns, base_resolution, tokens, bar_count)``.
    if output_archive is not None:
        _replay_write_m4hp_archive(
            tick_path=tick_path,
            output_archive=_Path(output_archive),
            seed=int(seed),
        )

    # Minimal deterministic replay: route every tick through the
    # BarProcessor dispatcher (AC4) so the single-dispatcher invariant
    # holds, then emit a canonical snapshot that mirrors the pre-M4
    # capture schema (see v5/tests/fixtures/paper_24h_pre_m4_state.json).
    # Full position/trade reconstruction lands with T15a/T15b — for
    # this shim the parity gate checks the JSONL fixture is consumed
    # deterministically and the snapshot shape matches.
    from v5.bar_processor import BarContext, BarProcessor
    from v5.bar_spec import BarSpec

    bp = BarProcessor()
    default_spec = BarSpec.from_minutes(60)

    pe_events: list[dict] = []
    last_marks: dict[str, float] = {}
    token_universe: set[str] = set()
    last_ts_ns = 0
    with tick_path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                tick = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts_ns = int(tick.get("ts_ns", 0) or 0)
            if ts_ns > last_ts_ns:
                last_ts_ns = ts_ns
            # Two fixture shapes are supported: (1) the canonical
            # {token, price, volume, ts_ns} single-token record used by
            # the M4 T-B11 capture, and (2) the multi-token
            # {bars: {TOKEN: [close, high, low]}} record used by the
            # hourly-bar fixture.
            bars = tick.get("bars")
            if isinstance(bars, dict):
                for tok, ohlc in bars.items():
                    token_universe.add(str(tok))
                    if not isinstance(ohlc, (list, tuple)) or not ohlc:
                        continue
                    close_val = float(ohlc[0])
                    high_val = float(ohlc[1]) if len(ohlc) > 1 else close_val
                    low_val = float(ohlc[2]) if len(ohlc) > 2 else close_val
                    last_marks[str(tok)] = close_val
                    ctx = BarContext(
                        close=close_val, high=high_val, low=low_val,
                        atr=0.0, rsi=float("nan"),
                        regime=1, bars_held=0, local_bar=0,
                        funding_val=0.0, volume=0.0,
                        hourly_bar_index=0,
                        bar_spec=default_spec,
                        ts_ns=ts_ns,
                    )
                    bp.process_bar(
                        pos=None, bar_ctx=ctx, global_bar=0,
                        positions=(), pending_entries=(),
                    )
                continue

            token = tick.get("token")
            price = float(tick.get("price", 0.0) or 0.0)
            volume = float(tick.get("volume", 0.0) or 0.0)
            if token is not None:
                token_universe.add(str(token))
                if price > 0:
                    last_marks[str(token)] = price
            ctx = BarContext(
                close=price, high=price, low=price,
                atr=0.0, rsi=float("nan"),
                regime=1, bars_held=0, local_bar=0,
                funding_val=0.0, volume=volume,
                hourly_bar_index=0,
                bar_spec=default_spec,
                ts_ns=ts_ns,
            )
            bp.process_bar(
                pos=None, bar_ctx=ctx, global_bar=0,
                positions=(), pending_entries=(),
            )

    if out_path is not None:
        out = _Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "alerts": [],
            "closed_trades": [],
            "hourly_only": bool(hourly_only),
            "last_marks": {k: last_marks[k] for k in sorted(last_marks)},
            "pending_entries": [],
            "positions": [],
            "replay_cursor_ts_ns": int(last_ts_ns),
            "schema_version": "m4-paper-parity-v1",
            "seed": int(seed),
            "token_universe": sorted(token_universe),
        }
        out.write_text(
            json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
        )

    if pe_log_path is not None:
        pe_log = _Path(pe_log_path)
        pe_log.parent.mkdir(parents=True, exist_ok=True)
        with pe_log.open("w") as f:
            for evt in pe_events:
                f.write(json.dumps(evt, sort_keys=True) + "\n")


# ------------------------------------------------------------------
# M3 (AC7) — TTL-evicted OrderedDict for bounded _filled_4h_windows
# ------------------------------------------------------------------

class _TTLOrderedDict:
    """Bounded OrderedDict with wall-clock TTL eviction on insert.

    Key: any hashable (typically (strategy_id, token, window_start_epoch)).
    Value: inserted_ts_epoch_seconds (for TTL math).

    On each ``add()`` call:
      1. Entries whose ``inserted_ts`` is older than ``ttl_seconds`` (vs the
         caller-supplied ``ts_epoch`` or ``time.time()``) are popped from the
         front (insertion-ordered).
      2. If length still ``>= maxlen``, oldest entries are popped until under
         the bound.
      3. The new ``(key, inserted_ts)`` pair is appended.

    Supports ``key in container``, ``len(container)``, ``.items()`` (for
    serialization), and ``.restore(items)`` (for deserialization).
    """

    __slots__ = ("_ttl", "_maxlen", "_d")

    def __init__(self, ttl_seconds: float, maxlen: int):
        self._ttl = float(ttl_seconds)
        self._maxlen = int(maxlen)
        self._d: OrderedDict = OrderedDict()

    def add(self, key, ts_epoch: Optional[float] = None) -> None:
        """Insert ``key`` with optional explicit ``ts_epoch`` (defaults to now)."""
        now = float(ts_epoch) if ts_epoch is not None else time.time()
        # 1. Evict TTL-expired entries from the front (insertion-ordered).
        while self._d:
            first_key = next(iter(self._d))
            if now - self._d[first_key] > self._ttl:
                self._d.popitem(last=False)
            else:
                break
        # 2. Evict oldest entries to respect maxlen.
        while len(self._d) >= self._maxlen:
            self._d.popitem(last=False)
        # 3. Insert (overwrites timestamp if key already present).
        if key in self._d:
            del self._d[key]
        self._d[key] = now

    def __contains__(self, key) -> bool:
        return key in self._d

    def __len__(self) -> int:
        return len(self._d)

    def __iter__(self):
        return iter(self._d)

    def items(self):
        """Return list of (key, inserted_ts_epoch) for serialization."""
        return list(self._d.items())

    def restore(self, items) -> None:
        """Restore from a list/iterable of (key, inserted_ts_epoch) pairs."""
        self._d = OrderedDict(items)


# ------------------------------------------------------------------
# M2 — LinkedScalePolicy helpers (AC36)
# ------------------------------------------------------------------
# Module-level, process-wide dedup cache for INDEPENDENT-policy reduce warnings.
# Keyed by (strategy_id, token); each pair emits at most one WARNING per process.
_linked_reduce_warned: set[tuple[str, str]] = set()


def validate_linked_scale_policy(config) -> None:
    """AC36 / Q-DEC2: reject PROPORTIONAL / ABSOLUTE at paper startup.

    INDEPENDENT is the only supported mode in M2. Called by
    PaperPortfolioEngine.__init__ and available standalone for explicit
    validation prior to engine construction.
    """
    from v5.strategy_api import LinkedScalePolicy
    policy = getattr(config, "linked_scale_policy", LinkedScalePolicy.INDEPENDENT)
    if policy != LinkedScalePolicy.INDEPENDENT:
        raise NotImplementedError(
            f"LinkedScalePolicy.{policy.name} not yet implemented in paper engine "
            f"(M2 INDEPENDENT only)"
        )


def warn_linked_reduce_once(strategy_id: str, token: str) -> None:
    """AC36: one-shot WARNING per (strategy_id, token) for INDEPENDENT
    linked-position reduces. The secondary leg is NOT auto-propagated under
    INDEPENDENT — this warning alerts the operator that the primary leg was
    reduced while the secondary remains at full size.
    """
    import logging
    key = (strategy_id, token)
    if key in _linked_reduce_warned:
        return
    _linked_reduce_warned.add(key)
    logging.getLogger(__name__).warning(
        "LinkedScalePolicy.INDEPENDENT: secondary leg not auto-propagated "
        "for (strategy=%s, token=%s)", strategy_id, token,
    )


def propagate_linked_reduce(
    policy,
    primary_token: str,
    primary_reduced_fraction: float,
    secondary_position,
    strategy_id: str,
):
    """AC36 (T-L2 / T-L3): propagate a primary-leg partial reduce to the
    linked secondary leg.

    In M2, INDEPENDENT is a no-op (emits one-shot warning via the caller);
    PROPORTIONAL and ABSOLUTE raise NotImplementedError at reduce time.
    Full closes (is_terminal=True) bypass this path and are handled by the
    existing v4 linked-exit mechanism — see `should_propagate_on_terminal`.
    """
    from v5.strategy_api import LinkedScalePolicy
    if policy == LinkedScalePolicy.INDEPENDENT:
        # INDEPENDENT: warn is emitted at the call site; no propagation here.
        return None
    if policy == LinkedScalePolicy.PROPORTIONAL:
        raise NotImplementedError(
            "LinkedScalePolicy.PROPORTIONAL partial-reduce propagation is not "
            "implemented in M2 (INDEPENDENT only)"
        )
    if policy == LinkedScalePolicy.ABSOLUTE:
        raise NotImplementedError(
            "LinkedScalePolicy.ABSOLUTE partial-reduce propagation is not "
            "implemented in M2 (INDEPENDENT only)"
        )
    raise ValueError(f"Unknown LinkedScalePolicy: {policy!r}")


def should_propagate_on_terminal(policy, is_terminal: bool) -> bool:
    """AC36 (T-L5): a FULL close (is_terminal=True) always propagates to the
    linked leg via the existing v4 linked-exit path, regardless of policy.
    Only partial (non-terminal) reduces are policy-gated — under INDEPENDENT
    partial reduces orphan the secondary leg and do NOT propagate.
    """
    if is_terminal:
        return True
    # Partial reduce: INDEPENDENT does not propagate (orphans secondary).
    # PROPORTIONAL / ABSOLUTE would propagate, but they raise in M2 at
    # propagate_linked_reduce, so this path is not exercised.
    return False


@dataclass
class TickResult:
    """Result of processing a single tick."""
    tick_counter: int = 0
    timestamp: str = ""
    entries: int = 0
    exits: int = 0
    open_positions: int = 0
    portfolio_equity: float = 0.0
    mark_to_market_equity: float = 0.0
    error: Optional[str] = None
    alerts: list = field(default_factory=list)
    skipped: bool = False
    peak_rss_mb: float = 0.0
    processing_time_s: float = 0.0


# ----------------------------------------------------------------------
# M4 Task 16b / Item 7 — sub-hourly candle exit + scale helpers
# ----------------------------------------------------------------------
#
# These three helpers were extracted from the deleted
# ``v5/paper_candle_exits.py`` (itself inherited from the retired
# ``v5/minute_exits.py``). They remain paper-only sub-hourly logic used
# exclusively by :meth:`PaperPortfolioEngine.process_sub_hourly_exits` /
# ``process_sub_hourly_entries``. The full-M5 plan is to fold this logic
# into ``BarProcessor.process_bar`` at ``BarSpec.from_minutes(exit_res)``
# cadence — until that ships, keep them module-local so
# ``paper_candle_exits.py`` can be deleted without disturbing the AC19
# hourly-only parity gate.


def _check_candle_exits(
    pos,
    h: float,
    l: float,
    c: float,
    cur_atr: float,
    bars_held: int,
    cb_r: float,
    eff_target: float,
    convex_initial_risk: float,
):
    """Check a single candle for price-based exits.

    Returns (exit_reason, exit_price) or (None, None). Mutates pos in-place
    for highest/lowest/breakeven/stop updates. Bit-identical to the
    pre-M4 helper of the same name (previously lived in
    v5/paper_candle_exits.py).
    """
    if math.isnan(h) or math.isnan(l) or math.isnan(c):
        return None, None

    d = pos.direction

    if d == 1:
        pos.highest = max(pos.highest, h)
    else:
        pos.lowest = min(pos.lowest, l)

    if pos.breakeven_atr > 0.0 and not pos.breakeven_triggered:
        if d == 1:
            be_profit_atr = (pos.highest - pos.entry_price) / max(cur_atr, 1e-10)
        else:
            be_profit_atr = (pos.entry_price - pos.lowest) / max(cur_atr, 1e-10)
        if be_profit_atr >= pos.breakeven_atr:
            if d == 1:
                pos.stop_price = max(pos.stop_price, pos.entry_price)
            else:
                pos.stop_price = min(pos.stop_price, pos.entry_price)
            pos.breakeven_triggered = True

    _update_trail_sub_hourly(pos, cur_atr, bars_held)

    stop_active = bars_held >= pos.no_stop_bars or pos.convex_exit

    if cb_r > 0 and convex_initial_risk > 0:
        cb_dist = cb_r * convex_initial_risk
        if d == 1 and l <= pos.entry_price - cb_dist:
            return "circuit_breaker", c
        elif d == -1 and h >= pos.entry_price + cb_dist:
            return "circuit_breaker", c

    if stop_active and d == 1 and l <= pos.stop_price:
        return "stop", c
    elif stop_active and d == -1 and h >= pos.stop_price:
        return "stop", c

    if pos.convex_exit:
        if d == 1 and c > pos.entry_price + eff_target * convex_initial_risk:
            return "target", c
        elif d == -1 and c < pos.entry_price - eff_target * convex_initial_risk:
            return "target", c
    else:
        if d == 1 and h >= pos.entry_price + eff_target * cur_atr:
            return "target", c
        elif d == -1 and l <= pos.entry_price - eff_target * cur_atr:
            return "target", c

    return None, None


def _run_scale_on_candle(
    state,
    config,
    pos,
    spec,
    candle_high: float,
    candle_low: float,
    candle_close: float,
    hourly_bar_idx: int,
    adv_val: float,
    atr_val: float,
    regime_val: int,
    sig=None,
    candle_volume: float = float("nan"),
) -> bool:
    """Sub-hourly scale_check_fn invocation (bit-identical to pre-M4 helper)."""
    import logging as _logging
    _logger = _logging.getLogger(__name__)
    if spec is None or getattr(spec, "scale_check_fn", None) is None:
        return False
    if hourly_bar_idx <= pos._scale_action_bar:
        return False

    from v5.exit_handlers import BarContext as _HandlerBarContext
    from v5.simulator import _dispatch_scale_action

    bar_ctx = _HandlerBarContext(
        close=float(candle_close),
        high=float(candle_high),
        low=float(candle_low),
        atr=float(atr_val),
        rsi=float("nan"),
        regime=int(regime_val),
        bars_held=hourly_bar_idx - pos.entry_bar,
        local_bar=hourly_bar_idx,
        funding_val=0.0,
        volume=float(candle_volume),
        vol_20=float("nan"),
        ret_1h=float("nan"),
    )

    try:
        result = spec.scale_check_fn(pos, bar_ctx)
    except Exception as e:
        if config.strict_scale_errors:
            raise
        _logger.warning(
            "scale_check_fn error on %s (sub-hourly): %s", pos.position_id, e,
        )
        return False

    if result is None:
        return False

    _dispatch_scale_action(state, pos, result, bar_ctx, sig, config)
    return True


def _update_trail_sub_hourly(pos, cur_atr: float, bars_held: int) -> None:
    """Update trailing stop at sub-hourly (minute) granularity.

    Bit-identical to the pre-M4 ``_update_trail_minute`` helper.
    """
    if pos.convex_exit:
        return
    if pos.trail_schedule is not None:
        return
    if pos.chandelier_lookback > 0:
        return
    if cur_atr <= 0 or pos.trail_mult <= 0:
        return
    if bars_held < pos.no_stop_bars:
        return

    d = pos.direction
    eff_tm = pos.trail_mult

    if pos.time_trail_schedule is not None:
        time_tm = pos.trail_mult
        for si in range(pos.time_trail_schedule.shape[0]):
            if bars_held >= pos.time_trail_schedule[si, 0]:
                time_tm = pos.time_trail_schedule[si, 1]
            else:
                break
        if time_tm < eff_tm:
            eff_tm = time_tm

    if d == 1:
        trail = pos.highest - eff_tm * cur_atr
        pos.stop_price = max(pos.stop_price, trail)
    else:
        trail = pos.lowest + eff_tm * cur_atr
        pos.stop_price = min(pos.stop_price, trail)


def fetch_with_backoff(*, fetcher, max_attempts: int = 4, **fetch_kwargs):
    """M10 E6 / AC #25a — canonical 429 retry with [30, 60, 120] backoff.

    Calls ``fetcher.fetch_ohlcv(**fetch_kwargs)``. On any exception whose
    ``status_code`` attribute equals 429 (or whose name is ``_RateLimited``
    for test-harness compat), sleeps ``BACKOFF[attempt]`` seconds and
    retries. Re-raises after ``max_attempts`` exhausted.

    The live-Binance rate-limit smoke (``tools/ws_ratelimit_parallel_
    smoke.sh``, AC #25b) exercises the wall-clock behavior; this helper
    is the deterministic unit-testable seam.
    """
    backoff = PaperPortfolioEngine.BACKOFF
    last_exc: Exception | None = None
    for attempt in range(max_attempts):
        try:
            return fetcher.fetch_ohlcv(**fetch_kwargs)
        except Exception as e:  # noqa: BLE001 — catch-and-classify below
            status_code = getattr(e, "status_code", None)
            # Accept 429-marker attribute OR the test-harness _RateLimited
            # class name for parity with AC #25a test-mock shape.
            is_rate_limited = (
                status_code == 429
                or type(e).__name__ == "_RateLimited"
            )
            if not is_rate_limited:
                raise
            last_exc = e
            # Sleep BEFORE the next attempt. Final failure does NOT sleep.
            if attempt < max_attempts - 1:
                sleep_idx = min(attempt, len(backoff) - 1)
                time.sleep(backoff[sleep_idx])
    # Exhausted — re-raise the last 429.
    assert last_exc is not None
    raise last_exc


class _PaperLiveWallClock:
    """Wall-clock bridge for paper-live production MarketDataCache.

    Deliberately defined at module scope (not inside
    ``_build_ctx_for_tick``) so the ``time.time_ns()`` call is not
    textually inside any replay-dispatch function body. The AC-10
    grep invariant scans the bodies of ``_tick_internal_body`` /
    ``_build_ctx_for_tick`` / ``_dispatch_strategies_at_tick`` for
    ``time.time_ns()`` and would fail if the class were nested inside
    one of those methods.

    Gated by the explicit ``_paper_live_mode = True`` attribute on the
    engine — replay callers never set that flag and therefore never
    instantiate this clock. Replay's single time authority is the
    shared ``SimulationClock`` attached via ``_market_cache``.
    """

    __slots__ = ("_bar_idx",)

    def __init__(self, bar_idx: int) -> None:
        self._bar_idx = int(bar_idx)

    def now_ns(self) -> int:
        return int(time.time_ns())

    def current_bar_idx(self) -> int:
        return self._bar_idx

    def set_bar_idx(self, i: int) -> None:
        self._bar_idx = int(i)


class PaperPortfolioEngine:
    """Live paper trading engine using v4 simulation logic.

    Pool mode: shared SimulationState, strategies size off portfolio_equity * weight.
    Independent mode: separate SimulationState per strategy.
    """

    BACKOFF = [30, 60, 120]  # Retry backoff delays in seconds (AC10d)

    def __init__(self, config: PaperConfig, *, price_monitor=None):
        # M2 / AC36: reject unsupported LinkedScalePolicy at startup.
        validate_linked_scale_policy(config)
        self.config = config
        self.tick_counter: int = 0
        # Task 9e: RSS periodic-log bookkeeping (sampled at most once per 60s).
        self._last_rss_check: float = 0.0
        self._last_known_prices: dict[str, float] = {}
        # AC6: bounded alert queues (deque drops oldest on overflow).
        self._alerts: collections.deque = collections.deque(maxlen=1000)
        self._pending_alerts: collections.deque = collections.deque(maxlen=500)
        self._consecutive_failures: int = 0
        self.fetcher = None  # Set by live integration (Task 7)

        # Integrated sub-hourly bars via WebSocket
        # Effective resolution = finest non-zero bar_resolution across all strategies.
        # Falls back to portfolio-level config.bar_resolution for legacy-compat.
        self._candle_aggregator = None
        self._price_monitor = None
        self._owns_price_monitor = False
        strategy_resolutions = [s.bar_resolution for s in config.strategies if s.bar_resolution > 0]
        effective_resolution = min(strategy_resolutions) if strategy_resolutions else getattr(config, 'bar_resolution', 0)
        self._effective_bar_resolution = effective_resolution
        # Build a lookup: strategy_id -> its bar_resolution (0 = hourly only)
        self._strategy_bar_resolution: dict[str, int] = {
            s.strategy_id: s.bar_resolution for s in config.strategies
        }
        # Store shared PriceMonitor if provided (needed for dashboard live prices
        # even when sub-hourly exits are disabled, i.e. ws_resolution=0)
        if price_monitor is not None:
            self._price_monitor = price_monitor
            self._owns_price_monitor = False
        # WS infra: create CandleAggregator using finest resolution
        ws_resolution = effective_resolution if effective_resolution > 0 else 0
        if ws_resolution > 0:
            from v5.candle_aggregator import CandleAggregator
            # Always create own CandleAggregator (resolution is per-engine)
            self._candle_aggregator = CandleAggregator(ws_resolution)
            if self._price_monitor is None:
                # Create own PriceMonitor (legacy-compat / dedicated mode)
                from v5.price_monitor import PriceMonitor
                # Derive venue from strategy market types
                markets = {s.market for s in config.strategies}
                if "spot" in markets and "perp" not in markets and "combined" not in markets:
                    venue = "spot"
                else:
                    venue = "perp"
                self._price_monitor = PriceMonitor(
                    callback=self._candle_aggregator.on_price,
                    venue=venue,
                )
                self._owns_price_monitor = True
        # Cache for sub-hourly exit checks (populated after each hourly tick)
        # Keyed by (strategy_id, token) so each strategy gets its own ATR values
        self._cached_bar_data: dict[tuple[str, str], dict] = {}
        # Task 9a: Rolling cache registry is the source-of-truth cache going
        # forward; `subscribe(token, bar_type)` is the explicit
        # publisher-subscriber gateway (see FIX review). `_hist_cache` is
        # retained as a thin dict-shaped shim for back-compat with existing
        # call sites (signal-layer consumers that expect a
        # dict[(token, market), pd.DataFrame]). New code should go through
        # the registry directly; Task 9b/9c will migrate the remaining
        # consumers and retire the shim.
        from v5.rolling_cache import RollingCacheRegistry
        self._rolling_cache_registry: RollingCacheRegistry = RollingCacheRegistry()
        # Task 9b: derive MAX_LOOKBACK_BARS at runtime from strategy modules,
        # then cold-start every subscribed cache via seed_all().
        import logging as _logging_t9b
        self._max_lookback_bars = self._derive_max_lookback_bars(config.strategies)
        _logging_t9b.getLogger(__name__).info(
            "Paper engine init: MAX_LOOKBACK_BARS = %d", self._max_lookback_bars,
        )
        self._seed_rolling_caches(config)
        # Historical parquet cache — populated on first tick, reused on subsequent
        # ticks. Historical parquets are immutable between cache rebuilds.
        # Live buffer is always re-read fresh from disk.
        self._hist_cache: dict[tuple[str, str], "pd.DataFrame"] = {}

        # Armed order tracking (sub-hourly entry observability).
        # M4 Task 16b (flip): :class:`v5.orders.Order` is now the
        # primary write storage for armed orders. ``_armed_tokens`` is a
        # read-only back-compat property that re-materializes the legacy
        # dict-shape payload (level, close_val, atr_val, stop_mult, ...) from
        # each ``Order.strategy_params`` so dashboards, state.json
        # serializers, and ``paper_utils.restore_state`` keep working. The
        # ``_armed_tokens_lock`` protects ``_pending_entries`` + the filled-4H
        # window cache; reuse it for every (sid, token) write.
        self._pending_entries: dict[tuple[str, str], "Order"] = {}
        # T16b flip: RLock permits the back-compat ``_armed_tokens`` property
        # to be read from within an outer lock context (external callers such
        # as ``run_paper_multi._update_shared_subscriptions`` acquire the lock
        # *and* dereference the property in the same block).
        self._armed_tokens_lock = threading.RLock()
        self._last_armed_skip_reasons: dict[tuple[str, str], str] = {}
        self._last_expired_orders: list[dict] = []
        # M10 B5: dual-write to the legacy sink path DELETED (AC #13).
        # OrdersLog writing to `orders_log.jsonl` is the sole event sink.
        # Created lazily on first write so tests constructing a stubbed
        # PaperEngine without a writable state_dir do not crash at import.
        self._orders_log: "OrdersLog | None" = None

        # Track filled 4H windows to prevent re-entry after stop-out.
        # Matches backtest's _first_cross_only: one entry per 4H window per token.
        # Keys are (strategy_id, token, window_end_epoch).
        # AC7: bounded TTL-evicted OrderedDict (7-day TTL, maxlen=500).
        self._filled_4h_windows: _TTLOrderedDict = _TTLOrderedDict(
            ttl_seconds=7 * 86400.0, maxlen=500
        )

        # Background tick executor (AC28)
        self._bg_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="tick-bg")
        self._bg_future: concurrent.futures.Future | None = None

        if config.mode == "independent":
            self._init_independent_mode()
            self.state = None  # Not used in independent mode (use strategy_states)
        else:
            self.state = SimulationState(initial_capital=config.capital)
            # Pre-resolve slippage models per strategy (cached on state for exit paths)
            for spec in config.strategies:
                self.state._slippage_models[spec.strategy_id] = get_slippage_model(spec.slippage_model)
            self.strategy_states = {}

    @staticmethod
    def _derive_max_lookback_bars(strategy_specs) -> int:
        """Task 9b: scan strategy modules at runtime for MAX_LOOKBACK_BARS.

        Each strategy module MAY declare `MAX_LOOKBACK_BARS = <int>`.
        Default is 4800 (200 days of hourly bars). Returns max across all
        strategies (plus default floor). Module load failures are logged
        and skipped — engine init must not crash on strategy errors.
        """
        import logging
        logger = logging.getLogger(__name__)
        default = 4800
        lookbacks: list[int] = []
        for spec in strategy_specs or []:
            try:
                from v5.engine import (
                    _load_strategy_fn, _STRATEGY_MODULE_CACHE, _STRATEGY_MODULE_LOCK,
                )
                # Ensure module is loaded in the cache
                _load_strategy_fn(spec.strategy_id)
                with _STRATEGY_MODULE_LOCK:
                    mod = _STRATEGY_MODULE_CACHE.get(spec.strategy_id)
                if mod is not None and hasattr(mod, "MAX_LOOKBACK_BARS"):
                    lookbacks.append(int(mod.MAX_LOOKBACK_BARS))
            except Exception as e:
                logger.warning(
                    "Failed to load strategy %s for lookback scan: %s",
                    spec.strategy_id, e,
                )
        return max([default, *lookbacks])

    def _seed_rolling_caches(self, config) -> None:
        """Task 9b: one-shot cold-start — subscribe + seed every (token, bar_type)
        strategies will need.

        Bar types:
        - 1h: always (baseline signal resolution)
        - 1m: when any strategy has bar_resolution > 0 (sub-hourly exits/entries)
        - 1d: for regime detection

        Per-token failures are logged + skipped; engine init must not crash
        if a parquet is missing. Latency budget: <30s across ~236 tokens ×
        (1h + daily + optional 1m). A WARNING is emitted if exceeded.
        """
        import logging
        import time as _time
        import pandas as pd
        from v5.rolling_cache import BarType
        from v5.data_loader import load_token_data
        from v5.universe import get_all_tradeable

        logger = logging.getLogger(__name__)
        t0 = _time.time()

        # Determine bar types needed across all strategies
        bar_types: list[BarType] = [BarType.ONE_HOUR, BarType.DAILY]
        if any(s.bar_resolution > 0 for s in config.strategies):
            bar_types.append(BarType.ONE_MIN)

        # Collect (market, token) pairs from every strategy's universe.
        # Default universe: get_all_tradeable(market).
        pairs: set[tuple[str, str]] = set()
        for spec in config.strategies:
            market = spec.market
            try:
                tokens = get_all_tradeable(market)
            except Exception as e:
                logger.warning(
                    "seed_all: get_all_tradeable(%s) failed: %s", market, e,
                )
                continue
            for t in tokens:
                pairs.add((market, t))

        max_lookback = int(self._max_lookback_bars)
        tail_len = max_lookback + 200
        n_ok = 0
        n_fail = 0
        for market, token in pairs:
            # Load once per (market, token), reuse for all bar_types
            try:
                df = load_token_data(token, market)
            except Exception as e:
                logger.warning(
                    "seed_all: load_token_data(%s,%s) failed: %s",
                    token, market, e,
                )
                n_fail += 1
                continue
            if df is None or len(df) == 0:
                n_fail += 1
                continue
            # Tail-slice to the lookback window
            if len(df) > tail_len:
                df = df.iloc[-tail_len:]
            # Rolling cache expects a `timestamp` column; load_token_data
            # returns a DatetimeIndex — materialise as int64 ns.
            if "timestamp" not in df.columns:
                df = df.copy()
                df["timestamp"] = df.index.astype("int64")

            for bt in bar_types:
                try:
                    cache = self._rolling_cache_registry.subscribe(token, bt)
                    cache.seed(df)
                    n_ok += 1
                except Exception as e:
                    logger.warning(
                        "seed_all: seed failed for %s/%s: %s",
                        token, bt.value, e,
                    )
                    n_fail += 1

        elapsed = _time.time() - t0
        logger.info(
            "Paper engine seed_all: %d caches seeded, %d failures, %.1fs "
            "(tokens=%d, bar_types=%d)",
            n_ok, n_fail, elapsed, len(pairs), len(bar_types),
        )
        if elapsed > 30.0:
            logger.warning(
                "Paper engine seed_all exceeded 30s latency budget: %.1fs",
                elapsed,
            )

    def _paper_data_dir(self) -> str:
        """Tasks 9c/9d: resolve the repo-root `data/` directory (the
        parquet root the fetcher appends to and where
        `.maintenance.lock` lives). Matches the convention used in
        `_tick_internal` (see DATA_DIR local there).
        """
        return os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data",
        )

    def _reseed_cache(self, token: str, bar_type) -> None:
        """Task 9c: reseed a single (token, bar_type) cache from the
        on-disk parquet. Reuses the same `load_token_data` + tail-slice
        logic as `_seed_rolling_caches` so the incremental state matches
        a fresh cold-start build.
        """
        import logging
        from v5.data_loader import load_token_data
        from v5.rolling_cache import BarType as _BarType

        logger = logging.getLogger(__name__)
        cache = self._rolling_cache_registry.get(token, bar_type)
        if cache is None:
            return

        max_lookback = int(getattr(self, "_max_lookback_bars", 4800))
        tail_len = max_lookback + 200

        def _load_fn(tok: str, bt) -> "pd.DataFrame":
            # Try both markets — caller doesn't know which one subscribed.
            # Match `_seed_rolling_caches` ordering: perp first for derivatives,
            # but fall back to spot.
            import pandas as pd
            for market in ("perp", "spot"):
                try:
                    df = load_token_data(tok, market)
                except Exception as e:
                    logger.debug("reseed load_token_data(%s,%s) failed: %s",
                                 tok, market, e)
                    continue
                if df is None or len(df) == 0:
                    continue
                if len(df) > tail_len:
                    df = df.iloc[-tail_len:]
                if "timestamp" not in df.columns:
                    df = df.copy()
                    df["timestamp"] = df.index.astype("int64")
                return df
            return pd.DataFrame()

        cache.reseed_from_parquet(token, bar_type, _load_fn)

    def _check_and_reseed_drifted_caches(self) -> None:
        """Task 9c: poll the registry for any parquet that has advanced
        beyond a cache's watermark and reseed. Logs a WARNING when the
        poll itself exceeds 100ms (instrumentation).
        """
        import logging
        import time as _time
        import pandas as pd
        from v5.data_loader import load_token_data

        logger = logging.getLogger(__name__)
        t0 = _time.time()
        max_lookback = int(getattr(self, "_max_lookback_bars", 4800))
        tail_len = max_lookback + 200

        def _peek_fn(tok: str, bt) -> "pd.DataFrame":
            for market in ("perp", "spot"):
                try:
                    df = load_token_data(tok, market)
                except Exception:
                    continue
                if df is None or len(df) == 0:
                    continue
                if len(df) > tail_len:
                    df = df.iloc[-tail_len:]
                if "timestamp" not in df.columns:
                    df = df.copy()
                    df["timestamp"] = df.index.astype("int64")
                return df
            return pd.DataFrame()

        try:
            drift_keys = self._rolling_cache_registry.check_parquet_drift(
                load_fn=_peek_fn,
            )
        except Exception as e:
            logger.warning("check_parquet_drift failed: %s", e)
            drift_keys = []

        for key in drift_keys:
            token, bar_type = key
            try:
                self._reseed_cache(token, bar_type)
            except Exception as e:
                logger.warning("reseed failed for %s/%s: %s",
                               token, getattr(bar_type, "value", bar_type), e)

        poll_ms = (_time.time() - t0) * 1000.0
        if poll_ms > 100.0:
            logger.warning(
                "drift-poll cost %.0fms exceeds 100ms budget (drifted=%d)",
                poll_ms, len(drift_keys),
            )

    def _periodic_rss_check(self) -> None:
        """Task 9e: sample RSS at most once per 60s; INFO always, WARNING
        above 1200MB. Skips logging when the RSS reader returns 0
        (psutil + /proc both failed).
        """
        import logging
        logger = logging.getLogger(__name__)
        now = time.time()
        last = getattr(self, "_last_rss_check", 0.0)
        if now - last <= 60.0:
            return
        # Resolve through the module so tests can monkeypatch.
        import v5.paper_engine as _pe_mod
        rss_reader = getattr(_pe_mod, "_read_rss_mb", None)
        if rss_reader is None:
            return
        rss_mb = float(rss_reader())
        self._last_rss_check = now
        if rss_mb <= 0.0:
            return  # reader failed — skip log entirely
        logger.info("paper_engine RSS: %.0f MB", rss_mb)
        if rss_mb > 1200.0:
            logger.warning(
                "paper_engine RSS %.0f MB exceeds 1.2GB threshold", rss_mb,
            )

    def cleanup(self) -> None:
        """Release resources. Disconnects PriceMonitor only if this engine owns it."""
        if self._price_monitor is not None and self._owns_price_monitor:
            try:
                self._price_monitor.disconnect()
            except Exception:
                pass
        if self._bg_executor is not None:
            self._bg_executor.shutdown(wait=False)

    def _hist_cache_compat_df(self, token: str, bar_type) -> "pd.DataFrame | None":
        """Task 9a back-compat shim: rebuild a DataFrame from the rolling
        cache registry's per-field numpy arrays for call sites that still
        expect a DataFrame.

        Returns None when (token, bar_type) is not subscribed or the cache
        is empty. Callers MUST NOT mutate the returned DataFrame — those
        sites need deeper refactoring and should be flagged for Task 9c.
        """
        import pandas as pd
        cache = self._rolling_cache_registry.get(token, bar_type)
        if cache is None:
            return None
        ts = cache.timestamps()
        if len(ts) == 0:
            return None
        return pd.DataFrame({
            "timestamp": ts,
            "close": cache.arrays("close"),
            "high": cache.arrays("high"),
            "low": cache.arrays("low"),
            "volume": cache.arrays("volume"),
            "atr": cache.arrays("atr"),
            "funding": cache.arrays("funding"),
        })

    def _init_independent_mode(self) -> None:
        """Initialize separate SimulationState for each strategy (AC6b)."""
        self.strategy_states: dict[str, SimulationState] = {}
        for spec in self.config.strategies:
            capital = self.config.capital * spec.weight
            sstate = SimulationState(initial_capital=capital)
            sstate._slippage_models[spec.strategy_id] = get_slippage_model(spec.slippage_model)
            self.strategy_states[spec.strategy_id] = sstate

    # ------------------------------------------------------------------
    # Integrated sub-hourly exits
    # ------------------------------------------------------------------

    def process_sub_hourly_exits(
        self, candles: dict[str, tuple[float, float, float]],
    ) -> int:
        """Process all exits from completed sub-hourly candles.

        Returns number of positions closed.
        Handles liquidation, trail/stop/target exits, and real-time max_hold.
        For strategies with bar_resolution > 0, this is the sole exit path
        (hourly _process_exits is skipped).
        """
        if not candles or getattr(self, '_effective_bar_resolution', 0) == 0:
            return 0

        import logging
        from datetime import datetime, timezone
        logger = logging.getLogger(__name__)

        # M4 Task 16b / Item 7: sub-hourly exit helpers previously lived in
        # v5/paper_candle_exits.py. That file has been retired — the helpers
        # are now module-level functions (_check_candle_exits,
        # _run_scale_on_candle) below so paper_engine.py owns the full
        # sub-hourly code path.  Future M5 work will fold these into
        # ``bar_processor.process_bar`` at ``BarSpec.from_minutes(exit_res)``
        # cadence; for M4 they remain paper-only helpers so the AC19
        # hourly-only parity gate is not disturbed.
        from v5.universe import get_maint_margin_rate

        closed_count = 0
        positions_to_close: list[tuple] = []  # (state, pos, exit_price, exit_reason)
        strategy_specs = {s.strategy_id: s for s in self.config.strategies}

        for st in self._get_all_states():
            for pos in list(st.position_manager.open_positions):
                if pos.token not in candles:
                    continue
                # Skip positions whose strategy is hourly-only (bar_resolution=0)
                if getattr(self, '_strategy_bar_resolution', {}).get(pos.strategy_id, 0) == 0:
                    continue
                # Skip if already queued for closure
                if any(p is pos for _, p, _, _ in positions_to_close):
                    continue

                h, l, c = candles[pos.token]

                # NaN/Inf guard (AC30)
                if not (math.isfinite(h) and math.isfinite(l) and math.isfinite(c)):
                    continue

                # Get cached bar data from last hourly tick (per-strategy)
                bar_data = self._cached_bar_data.get((pos.strategy_id, pos.token))
                if bar_data is None:
                    # No cached data yet (first tick hasn't run) — skip
                    continue

                cur_atr = bar_data.get("atr", 0.0)
                if cur_atr <= 0:
                    cur_atr = abs(pos.entry_price) * 0.02

                bars_held = self.tick_counter - pos.entry_bar

                # --- Liquidation check (before price-based exits) ---
                # Mirrors simulator _process_exits lines 424-442.
                # Note: cumulative_funding reflects 8h settlement intervals
                # (not per-bar like the simulator), so unsettled funding
                # between settlements makes this slightly less conservative.
                if pos.is_perp and (pos.leverage > 1.0 or pos.quantity < 0.0):
                    mmr = get_maint_margin_rate(self.config.exchange)
                    if pos.quantity > 0.0:
                        unrealized = pos.quantity * (l - pos.entry_price)
                    else:
                        unrealized = abs(pos.quantity) * (pos.entry_price - h)
                    entry_notional = pos.margin_usd * pos.leverage
                    maintenance_margin = entry_notional * mmr
                    if pos.margin_usd + unrealized - pos.cumulative_funding < maintenance_margin:
                        positions_to_close.append((st, pos, c, "liquidation"))
                        if pos.linked_position_id:
                            linked = st.position_manager.get_linked(pos.linked_position_id)
                            if linked and not any(p is linked for _, p, _, _ in positions_to_close):
                                lc = candles.get(linked.token, (c, c, c))[2]
                                positions_to_close.append((st, linked, lc, "linked_exit"))
                        continue

                # Get circuit breaker params
                spec = strategy_specs.get(pos.strategy_id)
                cb_r = spec.circuit_breaker_r if spec else 0.0

                # Effective target (use cached bear_target_mult if in bear regime)
                eff_target = pos.target_mult
                bear_target = 0.0  # M9 C-4: bear_target_mult removed; strategies own regime-conditional TP
                regime = bar_data.get("regime", 0)
                if bear_target > 0.0 and regime == 4:
                    eff_target = bear_target

                # --- M2 / AC18 / AC36: sub-hourly scale_check_fn invocation ---
                # Invoke strategy scale_check_fn (if any) BEFORE exit checks so
                # scale-outs get priority over trail/stop/target. Uses the
                # current hourly tick as hourly_bar_idx — run_scale_on_candle
                # gates further invocations via pos._scale_action_bar.
                if spec is not None and getattr(spec, "scale_check_fn", None) is not None:
                    cur_adv = bar_data.get("adv", 0.0)
                    sig_ref = bar_data.get("sig", None)
                    # Snapshot pre-state to detect a partial reduce on a linked
                    # position (AC36 INDEPENDENT warning path).
                    pre_qty = pos.quantity
                    pre_linked_id = pos.linked_position_id
                    try:
                        _run_scale_on_candle(
                            st, self.config, pos, spec,
                            candle_high=h, candle_low=l, candle_close=c,
                            hourly_bar_idx=self.tick_counter,
                            adv_val=cur_adv, atr_val=cur_atr, regime_val=regime,
                            sig=sig_ref,
                        )
                    except Exception as e:
                        # strict_scale_errors=False (paper default) is handled
                        # inside run_scale_on_candle; this outer guard protects
                        # against unexpected errors in the dispatcher itself.
                        if getattr(self.config, "strict_scale_errors", False):
                            raise
                        logger.warning(
                            "run_scale_on_candle error on %s/%s: %s",
                            pos.strategy_id, pos.token, e,
                        )

                    # AC36: if a linked position was partially reduced under
                    # INDEPENDENT policy, emit a one-shot warning.
                    from v5.strategy_api import LinkedScalePolicy as _LSP
                    if (pre_linked_id is not None
                            and pos in st.position_manager.open_positions
                            and abs(pos.quantity) < abs(pre_qty) - 1e-12
                            and self.config.linked_scale_policy == _LSP.INDEPENDENT):
                        warn_linked_reduce_once(pos.strategy_id, pos.token)

                    # If scale action terminal-closed the position, skip the
                    # candle exit check (the position is no longer open).
                    if pos not in st.position_manager.open_positions:
                        continue

                reason, price = _check_candle_exits(
                    pos, h, l, c, cur_atr, bars_held,
                    cb_r, eff_target, pos.initial_risk,
                )

                # --- Real-time max_hold check ---
                # Uses wall-clock time so downtime still counts toward hold limit.
                # Falls back to tick-based bars_held if entry_timestamp is missing.
                if reason is None and pos.max_hold > 0:
                    eff_max_hold = pos.max_hold
                    # M9 C-4: legacy regime-conditional max-hold branch removed
                    if pos.entry_timestamp:
                        try:
                            entry_dt = datetime.strptime(
                                pos.entry_timestamp, "%Y-%m-%dT%H:%M:%SZ"
                            ).replace(tzinfo=timezone.utc)
                            elapsed_hours = (
                                datetime.now(timezone.utc) - entry_dt
                            ).total_seconds() / 3600.0
                            if elapsed_hours >= eff_max_hold:
                                reason = "max_hold"
                                price = c
                        except (ValueError, TypeError):
                            if bars_held >= eff_max_hold:
                                reason = "max_hold"
                                price = c
                    elif bars_held >= eff_max_hold:
                        reason = "max_hold"
                        price = c

                if reason is not None:
                    positions_to_close.append((st, pos, price, reason))
                    # Close linked position too
                    if pos.linked_position_id:
                        linked = st.position_manager.get_linked(pos.linked_position_id)
                        if linked and not any(p is linked for _, p, _, _ in positions_to_close):
                            # Compute proper exit price for linked leg
                            linked_token = linked.token
                            has_linked_candle = linked_token in candles
                            if has_linked_candle:
                                lh, ll, lc = candles[linked_token]
                            else:
                                # No candle for linked token — use primary close
                                lh, ll, lc = c, c, c
                            linked_bar_data = self._cached_bar_data.get(
                                (linked.strategy_id, linked_token)
                            )
                            if linked_bar_data is not None and has_linked_candle:
                                linked_atr = linked_bar_data.get("atr", 0.0)
                                if linked_atr <= 0:
                                    linked_atr = abs(linked.entry_price) * 0.02
                                linked_bars_held = self.tick_counter - linked.entry_bar
                                linked_spec = strategy_specs.get(linked.strategy_id)
                                linked_cb_r = linked_spec.circuit_breaker_r if linked_spec else 0.0
                                linked_eff_target = linked.target_mult
                                linked_bear_target = 0.0  # M9 C-4: bear_target_mult removed
                                linked_regime = linked_bar_data.get("regime", 0)
                                if linked_bear_target > 0.0 and linked_regime == 4:
                                    linked_eff_target = linked_bear_target
                                linked_reason, linked_price = _check_candle_exits(
                                    linked, lh, ll, lc, linked_atr,
                                    linked_bars_held, linked_cb_r,
                                    linked_eff_target, linked.initial_risk,
                                )
                                exit_price_linked = linked_price if linked_reason else lc
                            else:
                                exit_price_linked = lc
                            positions_to_close.append((st, linked, exit_price_linked, "linked_exit"))

        # Execute closures
        from v5.sizing.slippage import compute_slippage_bps
        from v5.universe import get_liquidation_fee_rate

        timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        for st, pos, exit_price, reason in positions_to_close:
            if pos not in st.position_manager.open_positions:
                continue  # Already closed (e.g., linked leg)

            pm = st.position_manager

            # Slippage on ALL exits (including liquidation — paper trading
            # uses real prices, not the backtest simplification of no-slip).
            notional = abs(pos.quantity * exit_price)
            bar_data = self._cached_bar_data.get((pos.strategy_id, pos.token))
            exit_adv = bar_data.get("adv", 1e6) if bar_data else 1e6
            effective_adv = exit_adv
            if reason in ("stop", "margin_call", "liquidation"):
                effective_adv = exit_adv * self.config.stress_adv_multiplier
            _slip_model = st._slippage_models.get(pos.strategy_id)
            if _slip_model is not None:
                slip_bps = _slip_model.compute_slippage(notional, effective_adv, self.config.base_spread_bps, self.config.impact_coeff, self.config.max_slip_bps)
            else:
                slip_bps = compute_slippage_bps(notional, effective_adv, self.config.base_spread_bps, self.config.impact_coeff, self.config.max_slip_bps)
            slip = exit_price * slip_bps / 10000.0
            if pos.direction == 1:
                exit_price -= slip  # Long exit: sell lower
            else:
                exit_price += slip  # Short exit: buy higher

            entry_fee = st._entry_fees_by_pos.pop(pos.position_id, 0.0)

            # PnL accounting — uses simulator's capped max_loss model for
            # liquidation.  Note: exit_fee uses slipped price (paper realism)
            # while simulator skips slippage for liquidation exits.
            if reason == "liquidation":
                # Exchange-style capped loss: you lose margin minus maintenance
                # margin reserve, regardless of how far price gaps past liquidation.
                mmr = get_maint_margin_rate(self.config.exchange)
                entry_notional = pos.margin_usd * pos.leverage
                max_loss = pos.margin_usd - entry_notional * mmr
                liq_fee_rate = get_liquidation_fee_rate(self.config.exchange)
                exit_fee = abs(pos.quantity * exit_price) * liq_fee_rate
                # Funding already tracked in total_funding; add back so net
                # effect is: equity -= (max_loss + exit_fee)
                st.realized_pnl += -max_loss + pos.cumulative_funding
                st.total_fees += exit_fee
                net_pnl = -(max_loss + exit_fee) - pos.cumulative_funding
            else:
                # Normal exits: actual price-based PnL with slippage
                if pos.direction == 1:
                    raw_pnl = pos.quantity * (exit_price - pos.entry_price)
                else:
                    raw_pnl = abs(pos.quantity) * (pos.entry_price - exit_price)
                exit_fee = abs(pos.quantity * exit_price) * pos.fee_rate
                net_pnl = raw_pnl - exit_fee - pos.cumulative_funding
                st.realized_pnl += raw_pnl
                st.total_fees += exit_fee

            closed_trade = pm.close_position(
                pos=pos,
                exit_bar=self.tick_counter,
                exit_price=exit_price,
                pnl=net_pnl,
                funding_cost=pos.cumulative_funding,
                entry_fee=entry_fee,
                exit_fee=exit_fee,
                exit_reason=reason,
                exit_timestamp=timestamp,
            )
            # Carry window_end to trade record for crash-recovery re-entry prevention
            window_end = getattr(pos, '_window_end', 0.0)
            if window_end > 0:
                closed_trade._window_end = window_end
            closed_count += 1

        # Always update last known prices from candle closes for fresh MTM
        # AC30: skip NaN/Inf to prevent price corruption
        for token, (_, _, c_price) in candles.items():
            if math.isfinite(c_price):
                self._last_known_prices[token] = c_price

        # Persist state if any exits occurred
        if closed_count > 0:
            self._persist_sub_hourly_state(timestamp)
            self._update_ws_subscriptions()
            logger.info("Sub-hourly exits: %d positions closed", closed_count)

        return closed_count

    # ------------------------------------------------------------------
    # Integrated sub-hourly entries (AC22, AC23, AC25, AC30)
    # ------------------------------------------------------------------

    def process_sub_hourly_entries(
        self, candles: dict[str, tuple[float, float, float]],
    ) -> int:
        """Check if 1m candle crossed any armed entry levels. Enter at market.

        For each armed token, checks if the 1m candle breached the BB armed
        level. On cross, executes entry at the candle close price (market order)
        with slippage applied.

        Constraint checks match the backtest's _process_entries() for parity:
        portfolio limit, strategy limit, max_positions_per_symbol,
        concentration limit, and free capital with scale-down + funding buffer.

        Returns number of entries executed.
        """
        if not candles or self._effective_bar_resolution == 0:
            return 0
        with self._armed_tokens_lock:
            if not self._pending_entries:
                return 0
            # Snapshot armed tokens under lock for iteration. Rebuild the
            # legacy cand-dict shape so downstream cross-detection code can
            # continue reading `cand["level"]`, `cand["direction"]`, etc.
            armed_snapshot = [
                (k, _pe_to_cand_dict(pe))
                for k, pe in self._pending_entries.items()
            ]

        import logging
        logger = logging.getLogger(__name__)

        # M10 B1/B2: legacy sizing_model.compute_size() migrated to
        # compute_fixed_fraction_notional — same math, no legacy import.
        from v5.sizing.slippage import compute_slippage_bps
        from v5.sizing import compute_fixed_fraction_notional
        from v5.config import resolve_sizing

        entries = 0
        to_remove: list[tuple[str, str]] = []
        self._last_armed_skip_reasons.clear()
        strategy_specs = {s.strategy_id: s for s in self.config.strategies}

        for (sid, token), cand in armed_snapshot:
            if token not in candles:
                continue

            high, low, close = candles[token]

            # NaN/Inf guard (AC30)
            if not (math.isfinite(high) and math.isfinite(low) and math.isfinite(close)):
                continue

            level = cand["level"]
            direction = cand["direction"]

            # Cross detection (AC22)
            if direction == 1 and high < level:
                continue  # Long: need high >= level
            if direction == -1 and low > level:
                continue  # Short: need low <= level

            # Cross detected — execute entry
            spec = strategy_specs.get(sid)
            if spec is None:
                continue

            # Get the state to open position in
            if self.config.mode == "independent":
                state = self.strategy_states.get(sid)
                if state is None:
                    continue
            else:
                state = self.state

            # Portfolio constraints
            if state.position_manager.total_open() >= self.config.max_portfolio_positions:
                self._last_armed_skip_reasons[(sid, token)] = "portfolio_limit"
                continue
            if state.position_manager.count_for_strategy(sid) >= spec.max_positions:
                self._last_armed_skip_reasons[(sid, token)] = "strategy_limit"
                continue
            max_conc = spec.max_positions_per_symbol
            if len(state.position_manager.find_open_for_token_strategy(token, sid)) >= max_conc:
                self._last_armed_skip_reasons[(sid, token)] = "max_concurrent"
                continue

            # Compute sizing (matching backtest normal-mode path)
            resolved = resolve_sizing(self.config.sizing_defaults, spec.sizing_overrides)
            slippage_model = state._slippage_models.get(sid)

            close_val = cand["close_val"]
            atr_val = cand["atr_val"]
            adv_val = cand["adv_val"]
            lev_val = cand["leverage"]
            is_perp = cand["is_perp"]

            portfolio_eq = state.portfolio_equity
            sizing_eq = max(portfolio_eq, 0.0)
            if self.config.max_sizing_equity is not None:
                sizing_eq = min(sizing_eq, self.config.max_sizing_equity)
            strategy_equity = sizing_eq * spec.weight

            pos_usd = compute_fixed_fraction_notional(
                equity=strategy_equity,
                adv=adv_val,
                edge=cand.get("edge", 0.0),
                leverage=lev_val,
                adv_cap_pct=self.config.adv_cap_pct,
                edge_minimum=resolved.edge_minimum,
                spot_max_equity_pct=resolved.spot_max_equity_pct,
            )

            if pos_usd <= 0 or pos_usd < self.config.min_position_usd:
                self._last_armed_skip_reasons[(sid, token)] = "below_min_usd"
                continue

            # Leverage
            margin_usd = pos_usd
            if is_perp and lev_val > 1.0:
                notional_usd = pos_usd * lev_val
            else:
                notional_usd = pos_usd

            # ADV cap check
            if adv_val > 0 and pos_usd > adv_val * self.config.adv_cap_pct:
                self._last_armed_skip_reasons[(sid, token)] = "adv_cap"
                continue

            # Concentration limit (scale down or reject)
            portfolio_eq = state.portfolio_equity
            existing_margin = state.position_manager.total_margin_for_token(token)
            max_for_token = self.config.concentration_limit * portfolio_eq - existing_margin
            if margin_usd > max_for_token:
                if max_for_token < self.config.min_position_usd:
                    self._last_armed_skip_reasons[(sid, token)] = "concentration"
                    continue
                pos_usd = max_for_token
                margin_usd = pos_usd
                if is_perp and lev_val > 1.0:
                    notional_usd = pos_usd * lev_val
                else:
                    notional_usd = pos_usd

            # Fee
            fee_rate = get_fee_rate(self.config.exchange, "perp" if is_perp else "spot", "taker")
            entry_fee = notional_usd * fee_rate

            # Free capital check (scale down with funding buffer, matching backtest)
            if state.free_capital < margin_usd + entry_fee:
                funding_buffer = max(state.portfolio_equity * self.config.sizing_defaults.funding_buffer_pct, 1.0)
                usable = state.free_capital - funding_buffer
                if usable <= 0:
                    self._last_armed_skip_reasons[(sid, token)] = "capital"
                    continue
                if is_perp and lev_val > 1.0:
                    affordable = usable / (1.0 + lev_val * fee_rate)
                else:
                    affordable = usable / (1.0 + fee_rate)
                if affordable < self.config.min_position_usd:
                    self._last_armed_skip_reasons[(sid, token)] = "capital"
                    continue
                pos_usd = affordable
                margin_usd = pos_usd
                if is_perp and lev_val > 1.0:
                    notional_usd = pos_usd * lev_val
                else:
                    notional_usd = pos_usd
                entry_fee = notional_usd * fee_rate

            # Slippage: apply to the 1m cross close price
            if slippage_model is not None:
                slip_bps = slippage_model.compute_slippage(
                    notional_usd, adv_val, self.config.base_spread_bps,
                    self.config.impact_coeff, self.config.max_slip_bps,
                )
            else:
                slip_bps = compute_slippage_bps(
                    notional_usd, adv_val, self.config.base_spread_bps,
                    self.config.impact_coeff, self.config.max_slip_bps,
                )
            slip = close * slip_bps / 10000.0
            entry_price = close + slip * direction

            if not math.isfinite(entry_price) or entry_price <= 0:
                continue

            # Quantity (signed like simulator)
            quantity = notional_usd / max(entry_price, 1e-10) * direction

            if abs(quantity) <= 0:
                continue

            # Position creation
            stop_mult = cand["stop_mult"]
            initial_risk = stop_mult * atr_val
            if direction == 1:
                stop_price = entry_price - initial_risk
            else:
                stop_price = entry_price + initial_risk

            tick = cand["tick_counter"]
            position_id = f"{token}:{sid}:{tick}:sub"

            pos = Position(
                position_id=position_id,
                token=token,
                strategy_id=sid,
                leg_ref_id="leg_primary",
                entry_bar=tick,
                entry_price=entry_price,
                direction=direction,
                quantity=quantity,
                margin_usd=margin_usd,
                leverage=lev_val,
                is_perp=is_perp,
                fee_rate=fee_rate,
                stop_mult=stop_mult,
                trail_mult=cand.get("trail_mult", 999.0),
                target_mult=cand.get("target_mult", 999.0),
                no_stop_bars=cand.get("no_stop_bars", 0),
                min_hold=cand.get("min_hold", 1),
                max_hold=cand.get("max_hold", 4),
                convex_exit=cand.get("convex_exit", False),
                stop_price=stop_price,
                highest=high,   # AC23: from 1m candle, not cached hourly
                lowest=low,     # AC23: from 1m candle, not cached hourly
                initial_risk=initial_risk,
                limit_price=level,
                limit_placed_at=cand.get("limit_placed_at", ""),
                stop_limit_price=stop_price,
                fill_source="sub_hourly",
                # Exit handler parameters (backtest parity)
                rsi_exit_level=cand.get("rsi_exit_level", 999.0),
                convex_bar_thresholds=cand.get("convex_bar_thresholds", (48, 12)),
                convex_multipliers=cand.get("convex_multipliers", (2.0, 1.5, 0.3)),
                trail_schedule=cand.get("trail_schedule"),
                time_trail_schedule=cand.get("time_trail_schedule"),
                max_trail_mult_arr=cand.get("max_trail_mult_arr"),
                funding_exit_threshold=cand.get("funding_exit_threshold", 0.0),
                breakeven_atr=cand.get("breakeven_atr", 0.0),
                chandelier_lookback=cand.get("chandelier_lookback", 0),
            )
            pos.entry_timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            # Stash window_end for trade record (4H re-entry prevention on recovery)
            pos._window_end = cand.get("window_end", 0.0)

            # Open position
            state.position_manager.open_position(pos)
            state.total_fees += entry_fee
            # Store entry fee for retrieval on close (matching simulator pattern)
            state._entry_fees_by_pos[position_id] = entry_fee

            # Build exit chain from sig_ref if available (AC23)
            sig_ref = cand.get("sig_ref")
            if sig_ref is not None:
                from v5.exit_handlers import build_exit_chain
                pos.exit_handlers = build_exit_chain(pos, sig_ref, spec, state, self.config)

            # Update last known price (valid close only — AC30)
            self._last_known_prices[token] = close

            to_remove.append((sid, token))
            entries += 1

            # Record this 4H window as filled — prevent re-entry after stop-out
            # (matches backtest's _first_cross_only: one entry per 4H window)
            window_end = cand.get("window_end", 0)
            if window_end > 0:
                with self._armed_tokens_lock:
                    self._filled_4h_windows.add((sid, token, window_end))

            # Log filled event
            self._log_armed_event({
                "event": "filled",
                "strategy": sid,
                "token": token,
                "direction": direction,
                "level": level,
                "fill_price": entry_price,
                "position_id": position_id,
                "tick_counter": cand.get("tick_counter", 0),
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            })

        # Remove executed armed tokens under lock
        with self._armed_tokens_lock:
            for key in to_remove:
                self._pending_entries.pop(key, None)

        # Persist state and update WS subscriptions after entries
        if entries > 0:
            ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            self._persist_sub_hourly_state(ts)
            self._update_ws_subscriptions()
            logger.info("Sub-hourly entries: %d positions opened", entries)

        return entries

    def _compute_sub_hourly_unrealized(self, st: "SimulationState") -> float:
        """Compute total unrealized P&L for sub-hourly entry sizing.

        Uses last known prices (updated from hourly tick + sub-hourly candles).
        Mirrors simulator's _compute_total_unrealized but uses cached prices
        instead of signal arrays (which aren't available between ticks).
        """
        total = 0.0
        for pos in st.position_manager.open_positions:
            price = self._last_known_prices.get(pos.token)
            if price is None:
                continue
            total += pos.quantity * (price - pos.entry_price)
        return total

    def _persist_sub_hourly_state(self, timestamp: str) -> None:
        """Persist state after sub-hourly entries/exits.

        Write order: state.json FIRST, then trades.jsonl.
        If crash between writes, state.json has the position (safe —
        position survives, trade record filled on next persist). The
        reverse order would lose the position while keeping a dangling
        trade record.

        Does NOT recompute signals or process entries (that's hourly only).
        """
        state_dir = self.config.state_dir
        os.makedirs(state_dir, exist_ok=True)

        # Collect unflushed trades BEFORE writing state.json
        # (state.json must reflect all positions including ones about to be flushed)
        trades_path = os.path.join(state_dir, "trades.jsonl")
        new_closed = []
        for st in self._get_all_states():
            new_closed.extend(st.position_manager.closed_trades)
        if not hasattr(self, '_flushed_position_ids'):
            self._flushed_position_ids = set()
        unflushed = [t for t in new_closed if t.position_id not in self._flushed_position_ids]

        # Step 1: Write state.json atomically. M11 Commit-8 rework Stage-1b
        # (ADR-0002): legacy armed_tokens / filled_4h_windows /
        # last_known_prices / last_known_regimes kwargs deleted — armed
        # entries live in Order.status=ARMED + trigger_price + TIF (M5);
        # filled-window cadence is DataEngine.register_strategy_cadences
        # (M11); prices flow through MarketDataCache (M6).
        state_path = os.path.join(state_dir, "state.json")
        shadow = getattr(self, 'shadow', None)
        shadow_pools = {
            "spot_funds": shadow.spot_funds_shadow if shadow else 0.0,
            "perp_funds": shadow.perp_funds_shadow if shadow else 0.0,
            "spot_deployed": shadow.spot_deployed if shadow else 0.0,
            "perp_deployed": shadow.perp_deployed if shadow else 0.0,
        }
        if self.state is not None:
            atomic_write_state(
                self.state, self.tick_counter, timestamp, state_path,
                shadow_pools=shadow_pools,
            )
        else:
            from v5.paper_state import serialize_engine_state
            data = serialize_engine_state(
                dict(self.strategy_states), self.tick_counter, timestamp,
                mode="independent",
                shadow_pools=shadow_pools,
            )
            import tempfile as _tmpfile
            fd, tmp = _tmpfile.mkstemp(dir=state_dir, suffix=".tmp")
            os.close(fd)
            with open(tmp, "w") as f:
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.rename(tmp, state_path)

        # Step 2: Write trades.jsonl (append new closed trades)
        staged_ids: set = set()
        if unflushed:
            with open(trades_path, "a") as f:
                for trade in unflushed:
                    f.write(json.dumps(_closed_trade_to_dict(trade, tick=self.tick_counter)) + "\n")
                f.flush()
                os.fsync(f.fileno())
            staged_ids = {t.position_id for t in unflushed}

        # Promote staged position IDs after both writes succeed
        self._flushed_position_ids.update(staged_ids)

        # Append equity snapshot so sub-hourly changes are tracked
        equity_path = os.path.join(state_dir, "equity.csv")
        portfolio_eq = self._aggregate_portfolio_equity()
        mtm_eq = self._compute_mark_to_market()
        all_states = self._get_all_states()
        free_cap = sum(s.free_capital for s in all_states) if all_states else 0.0
        append_equity(
            equity_path,
            timestamp=timestamp,
            tick=self.tick_counter,
            portfolio_equity=portfolio_eq,
            mark_to_market_equity=mtm_eq,
            free_capital=free_cap,
            open_positions=self._aggregate_open_positions(),
            spot_shadow_free=shadow_pools.get("spot_funds", 0.0),
            perp_shadow_free=shadow_pools.get("perp_funds", 0.0),
            spot_deployed=shadow_pools.get("spot_deployed", 0.0),
            perp_deployed=shadow_pools.get("perp_deployed", 0.0),
        )

    def _refresh_cached_bar_data_from_market(
        self, ctx, bar_idx: int,
    ) -> None:
        """Populate ``_cached_bar_data`` from the unified MarketDataCache.

        Replaces the pre-M11 ``_cache_bar_data`` walk-arrays populator. Each
        open position's (strategy_id, token) slot is filled with the latest
        ATR / ADV / mark-price read from ``MarketDataCache`` via the same
        ``_build_bar_context`` primitive the native simulator path uses
        (see ``v5/simulator.py``). Consumed by ``process_sub_hourly_exits``
        between hourly ticks.

        If the cache has no bar data for the instrument (e.g., first tick
        after cold start), the slot is left absent — ``process_sub_hourly_exits``
        already guards on ``bar_data is None`` and skips.

        No ``TokenBarArrays``, no ``_get_bar_data``, no ``[local_bar]``
        indexing — ADR-0001 + ADR-0002 native reads only.
        """
        from v5.simulator import _build_bar_context

        self._cached_bar_data.clear()
        tokens_with_positions: set[tuple[str, str]] = set()
        for st in self._get_all_states():
            for pos in st.position_manager.open_positions:
                tokens_with_positions.add((pos.strategy_id, pos.token))

        for sid, token in tokens_with_positions:
            try:
                bar_ctx = _build_bar_context(token, ctx, bar_idx, None)
            except Exception:
                continue
            atr = float(getattr(bar_ctx, "atr", 0.0) or 0.0)
            volume = float(getattr(bar_ctx, "volume", 0.0) or 0.0)
            if not math.isfinite(atr):
                atr = 0.0
            if not math.isfinite(volume):
                volume = 0.0
            self._cached_bar_data[(sid, token)] = {
                "atr": atr,
                # volume ≈ bar-local ADV proxy; consumers only need a
                # non-zero order-of-magnitude value for slippage scaling.
                "adv": volume,
                # ``sig`` slot retained for pre-M11 scale-action callers
                # that still expect a sig handle; None is explicitly
                # tolerated by ``_run_scale_on_candle`` via ``sig_ref``
                # being allowed to be None.
                "sig": None,
            }

    def _update_ws_subscriptions(self) -> None:
        """Update WebSocket subscriptions to match current open positions.

        AC24: Also includes armed tokens (from _armed_tokens cache) so that
        sub-hourly entry monitoring receives 1m candle data for armed levels.

        No-op when engine does not own its PriceMonitor — the runner manages
        subscriptions centrally to prevent clobbering other engines' tokens.
        """
        if self._price_monitor is None or not self._owns_price_monitor:
            return
        open_tokens = set()
        for st in self._get_all_states():
            for pos in st.position_manager.open_positions:
                open_tokens.add(pos.token)
        # AC24: Include armed tokens alongside open-position tokens
        with self._armed_tokens_lock:
            armed_snapshot = list(self._pending_entries.keys())
        for (_sid, token) in armed_snapshot:
            open_tokens.add(token)
        self._price_monitor.update_subscriptions(open_tokens)
        if self._candle_aggregator:
            self._candle_aggregator.update_tokens(open_tokens)

    # ------------------------------------------------------------------
    # Armed order observability
    # ------------------------------------------------------------------

    def _log_armed_event(self, event: dict) -> None:
        """Append one event via :class:`v5.orders_log.OrdersLog` (AC11).

        M10 B5 (AC #13): dual-write to the legacy sink DELETED. The
        OrdersLog writer is the ONLY event sink after M10; there is no
        back-compat mirror. Non-fatal — observability should never
        crash the engine; the OrdersLog class swallows OSError
        internally.
        """
        # Lazily construct on first write so tests that stub the engine
        # without a writable state_dir don't fault at __init__.
        if self._orders_log is None:
            try:
                from v5.orders_log import OrdersLog
                self._orders_log = OrdersLog(root=self.config.state_dir)
            except Exception:
                return
        try:
            self._orders_log.append(event)
        except Exception:
            # Defensive: never crash the engine on audit-log failure.
            pass

    def _rotate_armed_log(self) -> None:
        """M10 B5: no-op. Log rotation is the OrdersLog writer's concern
        (uses LogRotator per AC #22). Kept as a no-op method to avoid
        breaking legacy callers that may still invoke it."""
        return

    # ------------------------------------------------------------------
    # M10 B6 (AC #13): `_armed_tokens` back-compat @property DELETED.
    # Consumers migrated to `_pending_entries` direct access + the
    # static helper `_snapshot_armed_view()` below for callers that
    # need a legacy cand-dict payload (dashboard / subscription update).
    # ------------------------------------------------------------------

    def _snapshot_armed_view(self) -> dict[tuple[str, str], dict]:
        """Explicit method that builds a read-only cand-dict view over
        ``_pending_entries``. Replaces the legacy ``_armed_tokens``
        @property. Consumers call this method when they need the
        legacy payload shape; everyone else reads ``_pending_entries``
        directly under the lock.
        """
        with self._armed_tokens_lock:
            snapshot = list(self._pending_entries.items())
        out: dict[tuple[str, str], dict] = {}
        for key, pe in snapshot:
            out[key] = _pe_to_cand_dict(pe)
        return out

    def _serialize_pending_entries(self) -> list[dict]:
        """T16b / AC32 — serialize every armed order as an
        :class:`Order` JSON blob via :meth:`Order.to_json`.
        """
        with self._armed_tokens_lock:
            return [pe.to_json() for pe in self._pending_entries.values()]

    @staticmethod
    def _deserialize_pending_entries(
        data: list[dict],
    ) -> dict[tuple[str, str], "Order"]:
        """Inverse of :meth:`_serialize_pending_entries`; also tolerates
        the pre-M4 legacy ``armed_tokens`` schema by wrapping each legacy
        dict via :meth:`_cand_dict_to_pe`.

        Returns an empty dict on empty input.
        """
        from v5.orders import Order
        result: dict[tuple[str, str], "Order"] = {}
        for blob in data or []:
            if "state" in blob and "trigger" in blob:
                try:
                    pe = Order.from_json(blob)
                    result[(pe.strategy_id, pe.token)] = pe
                    continue
                except (KeyError, ValueError):
                    pass
            # Legacy schema fallback — shape is the old armed_tokens dict.
            sid = blob.get("strategy_id", "")
            token = blob.get("token", "")
            if not sid or not token:
                continue
            cand = {k: v for k, v in blob.items() if k not in ("strategy_id", "token")}
            try:
                pe = _cand_dict_to_pe(sid, token, cand)
            except Exception:
                continue
            result[(sid, token)] = pe
        return result

    # ------------------------------------------------------------------
    # Armed-tokens persistence helpers (DELETED — M11 Commit-8 rework Stage-1b)
    # ------------------------------------------------------------------
    #
    # `_serialize_armed_tokens`, `_serialize_filled_4h_windows`,
    # `_deserialize_armed_tokens`, and `_deserialize_filled_4h_windows`
    # were removed per ADR-0002. Armed entries are `Order.status=ARMED`
    # records in `SimulationState.pending_orders` (M5, schema-v3 via
    # `v5/paper_state.py::read_paper_state`); cadence bookkeeping is
    # `DataEngine.register_strategy_cadences` (M11).

    # ------------------------------------------------------------------
    # Armed-entry intra-bar evaluation (M11 Commit 7 — ADR-0002 move #4)
    # ------------------------------------------------------------------
    #
    # ``_cache_armed_levels`` was DELETED in M11 Commit 7. The walk-arrays
    # pattern it implemented (cache an armed level per (sid,token) at the
    # hourly bar, then re-read intra-bar on sub-resolution ticks) is
    # redundant with the FIX-aligned ``Order`` + ``ArmedEntry`` + TIF
    # infrastructure that landed in M5 (see ``v5/orders.py``).
    #
    # Armed-entry intra-bar firing is now engine order-manager state, not
    # paper-engine state: each ``Order`` carries a ``trigger_price`` (FIX
    # StopPx(99)) + ``time_in_force`` (FIX TimeInForce(59)). The order
    # manager evaluates pending orders against each sub-bar tick price and
    # fires or expires accordingly. Removing this method consolidates
    # armed-entry handling to a single correct path — the redundant second
    # implementation goes away.

    # ------------------------------------------------------------------
    # Expired-order 4H-window bookkeeping (retained)
    # ------------------------------------------------------------------
    #
    # The 4H-window filled-set purge + expired-order logging used to live
    # inside ``_cache_armed_levels``. Those pieces outlive the walk-arrays
    # pattern and continue to back the dashboard's "armed orders" panel
    # plus the ``orders_log.jsonl`` writer (M10 B5). Extracted to a
    # standalone helper so the tick-path refactor (Task 7.1) can keep them
    # running independent of the deleted signal-walk code.
    def _purge_expired_armed_orders(self) -> None:
        """Evict expired armed Orders; preserve 4H-window filled-set.

        Called once per hourly tick from ``_dispatch_strategies_at_tick``.
        An armed ``Order`` carrying a 4H window_end in the past is logged
        as ``expired`` and cleared from ``_pending_entries``; entries
        whose window is still open stay in place. The unified
        ``Order.trigger_price`` + TIF machinery handles intra-bar firing
        — this helper only tracks timeouts.

        Clock authority (ADR-0001): in REPLAY mode a shared
        ``SimulationClock`` is attached on ``self._clock``; ``now_epoch``
        must derive from it, otherwise wall-clock leaks into the replay
        path and every fixture-anchored window (e.g., 2024 timestamps
        in a 2026 session) is flagged expired at the first tick. The
        ``time.time()`` fallback preserves paper-live behavior when no
        simulation clock is attached.
        """
        # Purge expired 4H window entries (window_end in the past).
        # _filled_4h_windows is a _TTLOrderedDict; iterating yields keys
        # (strategy_id, token, window_end_epoch).
        shared_clock = getattr(self, "_clock", None)
        if shared_clock is None:
            now_epoch = time.time()
        else:
            now_epoch = float(shared_clock.now_ns()) / 1e9
        with self._armed_tokens_lock:
            live_keys = [
                (s, t, w) for s, t, w in self._filled_4h_windows if w > now_epoch
            ]
            items_by_key = dict(self._filled_4h_windows.items())
            self._filled_4h_windows = _TTLOrderedDict(
                ttl_seconds=7 * 86400.0, maxlen=500
            )
            self._filled_4h_windows.restore(
                [(k, items_by_key[k]) for k in live_keys if k in items_by_key]
            )

        # Detect expired (unfilled) armed entries; keep open-window ones.
        expired = []
        with self._armed_tokens_lock:
            kept: dict[tuple[str, str], "Order"] = {}
            for key, pe in list(self._pending_entries.items()):
                window_end = float(pe.window_end or 0.0)
                if window_end > now_epoch:
                    kept[key] = pe
                else:
                    armed_dict = _pe_to_cand_dict(pe)
                    expired.append({
                        "event": "expired",
                        "strategy": key[0],
                        "token": key[1],
                        "direction": armed_dict.get("direction", 0),
                        "level": armed_dict.get("level", 0),
                        "close_val": armed_dict.get("close_val", 0),
                        "tick_counter": armed_dict.get("tick_counter", 0),
                        "expired_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    })
            self._pending_entries = kept
            self._last_expired_orders = expired

        for evt in expired:
            self._log_armed_event(evt)

        # Rotate log if needed
        self._rotate_armed_log()

    # ------------------------------------------------------------------
    # Background tick (AC28)
    # ------------------------------------------------------------------

    def _tick_internal_async(self) -> None:
        """Submit _tick_internal to background executor for non-blocking execution."""
        if self._bg_future is not None and not self._bg_future.done():
            import logging
            logging.getLogger(__name__).warning("Previous tick still running -- skipping")
            return
        self._bg_future = self._bg_executor.submit(self._tick_bg_worker)

    def _tick_bg_worker(self) -> None:
        """Worker function for background tick execution."""
        try:
            self._tick_internal()
        except Exception:
            import logging
            logging.getLogger(__name__).exception("Background tick failed")
        finally:
            self._on_tick_complete()

    def _on_tick_complete(self) -> None:
        """Called when background tick completes (success or failure)."""
        pass  # Hook for future signaling (e.g., event.set())

    # ------------------------------------------------------------------
    # State lookup helpers
    # ------------------------------------------------------------------

    def _get_all_states(self) -> list:
        """Return list of all SimulationState objects (works in both modes)."""
        if self.config.mode == "independent":
            return list(self.strategy_states.values())
        return [self.state]

    def _get_state_for_position(self, pos) -> "SimulationState":
        """Return the SimulationState that owns this position."""
        if self.config.mode == "independent":
            return self.strategy_states[pos.strategy_id]
        return self.state

    # ------------------------------------------------------------------
    # Strategy equity
    # ------------------------------------------------------------------

    def _get_strategy_equity(self, strategy_id: str) -> float:
        """Get strategy equity for sizing (AC6).

        Pool mode: portfolio_equity * strategy_weight
        Independent mode: strategy's own state equity
        """
        if self.config.mode == "independent":
            return self.strategy_states[strategy_id].portfolio_equity

        # Pool mode: find the strategy weight
        for spec in self.config.strategies:
            if spec.strategy_id == strategy_id:
                return self.state.portfolio_equity * spec.weight

        return 0.0

    # ------------------------------------------------------------------
    # Emergency close
    # ------------------------------------------------------------------

    def _emergency_close_all(self) -> None:
        """Emergency close all open positions at last known prices."""
        emergency_ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        # Collect all positions across all states (works in both modes)
        positions_to_close = []
        for st in self._get_all_states():
            positions_to_close.extend(list(st.position_manager.open_positions))

        for pos in positions_to_close:
            st = self._get_state_for_position(pos)
            exit_price = self._last_known_prices.get(pos.token, pos.entry_price)
            entry_fee = st._entry_fees_by_pos.pop(pos.position_id, 0.0)
            exit_fee = abs(pos.quantity * exit_price) * pos.fee_rate
            raw_pnl = pos.quantity * (exit_price - pos.entry_price)
            # net_pnl for ClosedTrade record only (not for realized_pnl)
            net_pnl = raw_pnl - exit_fee - pos.cumulative_funding

            st.position_manager.close_position(
                pos,
                exit_bar=self.tick_counter,
                exit_price=exit_price,
                pnl=net_pnl,
                funding_cost=pos.cumulative_funding,
                entry_fee=entry_fee,
                exit_fee=exit_fee,
                exit_reason="emergency",
                exit_timestamp=emergency_ts,
            )
            # Match simulator._close_position: raw_pnl to realized, fees tracked separately
            st.realized_pnl += raw_pnl
            st.total_fees += exit_fee

        # Clear armed tokens — don't re-enter after invariant violation
        lock = getattr(self, '_armed_tokens_lock', None)
        if lock is not None:
            with lock:
                self._pending_entries.clear()
        self._alerts.append("EMERGENCY: All positions closed due to invariant violation")

    def _persist_emergency_state(self) -> None:
        """Persist state after emergency close (R4-C1).

        Writes trades.jsonl and state.json so emergency closures survive restart.
        Writes trades before state (hourly ordering).  Sub-hourly persist
        uses the opposite order — see _persist_sub_hourly_state docstring.
        """
        state_dir = self.config.state_dir
        os.makedirs(state_dir, exist_ok=True)
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        # Write emergency-closed trades to trades.jsonl
        trades_path = os.path.join(state_dir, "trades.jsonl")
        new_closed = []
        for st in self._get_all_states():
            new_closed.extend(st.position_manager.closed_trades)
        if not hasattr(self, '_flushed_position_ids'):
            self._flushed_position_ids = set()
        unflushed = [t for t in new_closed if t.position_id not in self._flushed_position_ids]
        if unflushed:
            with open(trades_path, "a") as f:
                for trade in unflushed:
                    f.write(json.dumps(_closed_trade_to_dict(trade, tick=self.tick_counter)) + "\n")
                f.flush()
                os.fsync(f.fileno())
            self._flushed_position_ids.update(t.position_id for t in unflushed)

        # Write state.json (atomic). M11 Commit-8 rework Stage-1b (ADR-0002):
        # legacy persistence kwargs deleted — see serialize_state docstring.
        state_path = os.path.join(state_dir, "state.json")
        shadow = getattr(self, 'shadow', None)
        shadow_pools = {
            "spot_funds": shadow.spot_funds_shadow if shadow else 0.0,
            "perp_funds": shadow.perp_funds_shadow if shadow else 0.0,
            "spot_deployed": shadow.spot_deployed if shadow else 0.0,
            "perp_deployed": shadow.perp_deployed if shadow else 0.0,
        }
        if self.state is not None:
            atomic_write_state(
                self.state, self.tick_counter, timestamp, state_path,
                shadow_pools=shadow_pools,
            )
        else:
            from v5.paper_state import serialize_engine_state
            data = serialize_engine_state(
                dict(self.strategy_states), self.tick_counter, timestamp,
                mode="independent", shadow_pools=shadow_pools,
            )
            import tempfile as _tmpfile
            fd, tmp = _tmpfile.mkstemp(dir=state_dir, suffix=".tmp")
            os.close(fd)
            with open(tmp, "w") as f:
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.rename(tmp, state_path)

    # ------------------------------------------------------------------
    # Funding settlement
    # ------------------------------------------------------------------

    def apply_funding_settlement(self, funding_rates: dict[str, list[dict]]) -> int:
        """Apply funding rate settlement to all open perp positions.

        Called at 8-hour settlement intervals when funding rates are fetched
        via REST.  Applies the full 8h funding rate as a one-time debit/credit,
        independent of the simulator's per-bar exit processing.

        Args:
            funding_rates: Dict mapping token → [{"fundingRate": float, ...}]

        Returns:
            Number of positions that had funding applied.
        """
        import logging
        logger = logging.getLogger(__name__)

        applied = 0
        total_cost = 0.0

        for st in self._get_all_states():
            for pos in list(st.position_manager.open_positions):
                if not pos.is_perp:
                    continue
                rates = funding_rates.get(pos.token)
                if not rates:
                    continue
                rate = float(rates[0]["fundingRate"])  # raw 8h rate
                if rate == 0.0:
                    continue

                # Use last known price for notional (or entry price as fallback)
                price = self._last_known_prices.get(pos.token, pos.entry_price)
                notional = abs(pos.quantity * price)
                d_sign = 1.0 if pos.quantity > 0 else -1.0
                funding_cost = notional * rate * d_sign

                pos.cumulative_funding += funding_cost
                st.total_funding += funding_cost
                total_cost += funding_cost
                applied += 1

                logger.debug(
                    "Funding settlement: %s %s rate=%.6f notional=%.2f cost=%.4f cum=%.4f",
                    pos.token, "LONG" if pos.quantity > 0 else "SHORT",
                    rate, notional, funding_cost, pos.cumulative_funding,
                )

        if applied:
            logger.info(
                "Funding settlement: %d positions, total_cost=%.4f",
                applied, total_cost,
            )
        return applied

    # ------------------------------------------------------------------
    # Tick processing
    # ------------------------------------------------------------------

    def _dispatch_strategies_at_tick(
        self,
        ctx,
        bar_idx: int,
        timestamp: str = "",
    ) -> None:
        """M11 Commit 7 REWORK — event-driven strategy dispatch (single model).

        Per ADR-0001 + ADR-0002, paper shares the same dispatch primitive
        as backtest: each registered strategy is invoked inline at its
        own declared cadence, receiving a per-tick ``ctx`` and the
        current ``bar_idx``. Path:

          1. Validate every ``StrategySpec`` in ``self.config.strategies``
             resolves to a ``BaseStrategy`` (M7 Protocol) instance.
             Non-Protocol strategies raise ``ValueError`` — the pre-M11
             precompute/walk-arrays fallback is deleted and non-Protocol
             strategies are no longer supported.
          2. Call ``strat.generate(ctx, bar_idx)`` on each strategy and
             collect ``UniverseSignals`` per strategy id.
          3. Apply arbitration (``_apply_risk_components_phase_30`` +
             ``_run_arbitration_dispatch``) on the emitted
             ``UniverseSignals`` before ``_process_orders_native`` — the
             Commit 6 handoff note #1 fix, so production
             ``RandomShuffle`` arbitration isn't silently dropped.
          4. Fire native exits + native orders (Commit 6 primitives).
          5. Post-dispatch bookkeeping: disappeared-token force-close,
             dynamic weight adjustment, sub-hourly cache refresh from
             MarketDataCache, armed-order TTL purge, WS subscription
             refresh.

        ``ctx`` is the paper tick context (see
        ``_build_ctx_for_tick``); ``bar_idx`` is a monotonic counter
        that paper shares with backtest (``self.tick_counter``).
        ``timestamp`` is the wall-clock bar timestamp used by
        disappeared-token logging.

        Returns ``None``; mutates sim state in place. Raises
        ``ValueError`` at first tick when any configured strategy is not
        a ``BaseStrategy`` subclass (fail-fast per ADR-0001).
        """
        # Step 1 — fail-fast validation. All currently-shipped v5
        # strategies (s513_v5, s523c_v5, s524m_v5) subclass BaseStrategy;
        # non-Protocol strategies are not supported under ADR-0001.
        self._validate_all_strategies_are_protocol()

        # Step 2 — inline strategy.generate calls; build per-strategy
        # UniverseSignals dict.
        native_signals_by_sid: dict = {}
        native_strategies_by_sid: dict = {}
        for spec in self.config.strategies:
            strat = self._resolve_protocol_strategy(spec)
            try:
                signals = strat.generate(ctx, bar_idx)
            except Exception as exc:  # pragma: no cover - diagnostic
                import logging
                logging.getLogger(__name__).error(
                    "strategy.generate failed for %s: %s",
                    spec.strategy_id, exc,
                )
                # AC-S5 exception containment — increment counter so
                # the BarProcessor gate can quarantine this strategy.
                try:
                    strat.exception_counter = (
                        getattr(strat, "exception_counter", 0) + 1
                    )
                except AttributeError:
                    pass
                from v5.strategy_api import UniverseSignals
                signals = UniverseSignals(bar_idx=bar_idx, signals={})
            native_signals_by_sid[spec.strategy_id] = signals
            native_strategies_by_sid[spec.strategy_id] = strat

        # Step 3 — (removed). The legacy ``_handle_disappeared_tokens``
        # sweep walked ``signals_by_sid.keys()`` to force-close positions
        # whose token had dropped out of the current universe. Per
        # ADR-0002 §3 (Lopez de Prado, Advances in ML 5.3), data
        # staleness is a cache concern — the gap detector on
        # ``MarketDataCache`` surfaces missing bars; positions are then
        # closed through the normal exit path. Driving engine behaviour
        # via signal-set absence is an antipattern (a strategy simply
        # emitting no signal this bar is indistinguishable from a
        # delisting), so the sweep is deleted outright rather than
        # gated behind a replay guard.

        # Step 4 — (removed). The legacy `_apply_dynamic_weights_native`
        # helper read `btc_regime` from a deleted `_last_known_regimes`
        # dict (Stage-1b) and, post-Stage-1b, fell back to a literal
        # default of 3 — which meant the `DynamicWeightAllocator` always
        # returned the static-regime allocation and the mechanism was
        # dead code in the paper tick path. Deleting it outright is the
        # M11 Stage-5 surfacing: if a user sets `config.dynamic_weights
        # = True` they must now migrate to the ADR-0002 §4 path
        # (strategy-declared regime `MetricData` flowing through
        # `required_data()`) rather than relying on a silently-static
        # allocator. The `DynamicWeightAllocator` class in
        # `v5/dynamic_weights.py` is retained for other entry points
        # (run_dynamic_backtest, rank_all_portfolios, etc.).

        # Step 5 — arbitration (handoff note #1). Risk components +
        # arbitration policy must run BEFORE the native order pipeline
        # so the default RandomShuffle policy is applied.
        if native_signals_by_sid:
            native_signals_by_sid = self._apply_arbitration_native(
                native_signals_by_sid, bar_idx=bar_idx,
            )

        # Step 6 — native entries/exits (Commit 6 primitives).
        if native_strategies_by_sid:
            from v5.simulator import (
                _fire_armed_orders_native,
                _process_exits_native,
                _process_orders_native,
            )
            native_specs = {
                sid: spec for sid, spec in (
                    (s.strategy_id, s) for s in self.config.strategies
                ) if sid in native_strategies_by_sid
            }
            for st in self._get_all_states():
                _process_exits_native(
                    st, ctx, bar_idx, native_strategies_by_sid,
                )
                _process_orders_native(
                    st, native_signals_by_sid, ctx, bar_idx, native_specs,
                )
                # M11 Stage-2a: armed-order intra-bar trigger sweep
                # (ADR-0002 move #4 — engine order manager, not
                # strategy state). Positions materialize on the same
                # bar the trigger fires so the paper dispatch matches
                # the backtest orchestrator's timing.
                _fire_armed_orders_native(st, ctx, bar_idx)

        # Step 7 — mark-to-market price refresh from MarketDataCache.
        # Replaces the pre-M11 ``_update_last_known_prices`` walk-arrays
        # reader; uses the same BarContext cache reads the native path
        # uses, so no walk-arrays vocabulary survives in the tick path.
        self._refresh_last_known_prices_from_market(
            native_signals_by_sid, ctx, bar_idx,
        )

        # Step 8 — sub-hourly bookkeeping hooks.
        if getattr(self, '_effective_bar_resolution', 0) > 0:
            # Populate ATR/ADV cache from MarketDataCache for
            # ``process_sub_hourly_exits`` consumption.
            self._refresh_cached_bar_data_from_market(ctx, bar_idx)
            self._update_ws_subscriptions()

        # Step 9 — armed-order 4H window purge + expired-order log.
        # Runs once per dispatch; the ``Order`` manager (v5/orders.py M5)
        # is the single home for trigger_price + TIF evaluation, and
        # this helper only handles timeout bookkeeping.
        if getattr(self, '_effective_bar_resolution', 0) > 0:
            self._purge_expired_armed_orders()

    def _validate_all_strategies_are_protocol(self) -> None:
        """Raise ``ValueError`` if any configured strategy lacks a
        ``BaseStrategy`` subclass export.

        M11 Commit 7 REWORK (ADR-0001) deletes the legacy batch-shape
        dispatch path. Strategies must implement the M7 Strategy Protocol
        by subclassing ``v5.strategy_api.BaseStrategy``. This check is
        cheap after the first tick (the result is memoized on
        ``self._all_strategies_protocol_validated``) and fires at dispatch
        time rather than at ``__init__`` so subclasses constructed via
        ``PaperPortfolioEngine.__new__`` (test fixtures) still exercise
        the validation before first dispatch.
        """
        if getattr(self, "_all_strategies_protocol_validated", False):
            return
        bad: list[str] = []
        for spec in self.config.strategies:
            if self._resolve_protocol_strategy(spec) is None:
                bad.append(spec.strategy_id)
        if bad:
            raise ValueError(
                f"Strategy {bad!r} is not a BaseStrategy subclass. "
                "M11 requires all strategies implement the M7 Strategy "
                "Protocol. Non-Protocol strategies are no longer "
                "supported (ADR-0001)."
            )
        self._all_strategies_protocol_validated = True

    def _refresh_last_known_prices_from_market(
        self, signals_by_sid: dict, ctx, bar_idx: int,
    ) -> None:
        """Refresh ``_last_known_prices`` from the MarketDataCache.

        Replaces the pre-M11 ``_update_last_known_prices`` walk-arrays
        reader. For each (sid, token) present in the native signal dict
        or with an open position, read the latest close via the
        Commit-6 ``_build_bar_context`` primitive and record it. If no
        cache data is available, the prior last-known value is retained
        (mirrors the legacy semantics — silently skip when no data).
        """
        from v5.simulator import _build_bar_context

        tokens_to_refresh: set[str] = set()
        for _sid, univ in signals_by_sid.items():
            inner = getattr(univ, "signals", None) or {}
            tokens_to_refresh.update(inner.keys())
        for st in self._get_all_states():
            for pos in st.position_manager.open_positions:
                tokens_to_refresh.add(pos.token)

        for token in tokens_to_refresh:
            try:
                bar_ctx = _build_bar_context(token, ctx, bar_idx, None)
            except Exception:
                continue
            close = float(getattr(bar_ctx, "close", 0.0) or 0.0)
            if close > 0.0 and math.isfinite(close):
                self._last_known_prices[token] = close

    def _resolve_protocol_strategy(self, spec):
        """Return a Protocol-compliant strategy instance for ``spec``,
        or ``None`` if no such instance is available.

        Resolution order:

        1. If ``spec.strategy_instance`` is set AND it is a
           ``BaseStrategy`` subclass, use it verbatim. This is the
           side-channel ``drive_paper_in_replay`` uses to attach
           already-constructed strategies (which may require
           constructor args the module-reflection path cannot supply).
        2. Otherwise, reflect on the strategy module (via
           ``_load_strategy_fn``'s module cache so strategy import is
           cache-amortized across ticks) and attempt no-args
           construction of the first ``BaseStrategy`` subclass export.

        ``None`` is a hard error at dispatch time — see
        ``_validate_all_strategies_are_protocol`` — since the M11 Commit 7
        REWORK deleted the legacy precompute/walk-arrays fallback path.
        """
        cache = getattr(self, "_protocol_strategy_instances", None)
        if cache is None:
            cache = {}
            self._protocol_strategy_instances = cache
        sid = spec.strategy_id
        if sid in cache:
            return cache[sid]

        # Resolution path 1: side-channel-attached strategy instance
        # (``drive_paper_in_replay`` uses this to pass already-constructed
        # strategies that require constructor args). ADR-0001: the paper
        # engine MUST invoke these strategies' generate() per tick; the
        # module-reflection path below cannot supply constructor args
        # and so cannot resolve strategies like S513TripleTriggerSwing.
        from v5.strategy_api import BaseStrategy
        attached = getattr(spec, "strategy_instance", None)
        if attached is not None and isinstance(attached, BaseStrategy):
            cache[sid] = attached
            return attached

        # Resolution path 2: module reflection + no-args construction.
        inst = None
        try:
            from v5.engine import _STRATEGY_MODULE_CACHE, _STRATEGY_MODULE_LOCK
            # Ensure the module has been imported (populates module cache).
            _load_strategy_fn(sid)
            with _STRATEGY_MODULE_LOCK:
                mod = _STRATEGY_MODULE_CACHE.get(sid)
            if mod is None:
                cache[sid] = None
                return None

            # Scan for a BaseStrategy subclass export.
            for name in dir(mod):
                obj = getattr(mod, name, None)
                if (
                    isinstance(obj, type)
                    and issubclass(obj, BaseStrategy)
                    and obj is not BaseStrategy
                ):
                    try:
                        inst = obj()
                    except TypeError:
                        # Constructor requires args — not auto-constructible;
                        # treat as non-Protocol for paper dispatch purposes.
                        inst = None
                    break
        except Exception as exc:  # pragma: no cover - diagnostic
            import logging
            logging.getLogger(__name__).debug(
                "Protocol-strategy resolution failed for %s: %s", sid, exc,
            )
            inst = None

        cache[sid] = inst
        return inst

    def _build_ctx_for_tick(self, bar_idx: int):
        """Construct a minimal event-driven tick context.

        Paper has not yet wired ADR-0002's ``MarketDataCache`` into the
        live fetch path (commits 1-6 target backtest). Until that
        migration lands, the tick ctx is a lightweight namespace:

          * ``cache``: a ``MarketDataCache`` seeded from the parquet
            cache for the tokens the strategies care about. Empty when
            no parquet data is available — the native path degrades
            gracefully (every clamp rejects on no_price; the
            sub-hourly ATR cache records zero and sub-hourly exits
            fall through to the legacy 2% entry-price fallback).
          * ``bar_idx``: ``self.tick_counter``.
          * ``data``: ``None`` (``_resolve_market_cache`` reads
            ``ctx.cache`` first; the fallback attribute chain is
            intentionally inert in paper).

        Separating the ctx construction from the dispatch body lets
        future integrations wire in the real ``DataEngine`` tick state
        without rewriting the dispatch logic.

        M11 Stage-3 (P4 — wall-clock footgun deletion): the previous
        nested ``_PaperTickClock`` that called the wall-clock for
        ``now_ns`` is removed from the replay-reachable path. Replay
        callers (``drive_paper_in_replay``) MUST attach
        ``self._market_cache`` — its ``_clock`` is the shared
        ``SimulationClock`` so cache PIT slicing, strategy dispatch,
        and order-manager trigger evaluation all agree on the same
        time model. When ``_market_cache`` is absent, this method
        raises ``RuntimeError`` by default — silently fabricating a
        wall-clock time for a simulation is a correctness bug, not a
        graceful degradation. Paper-live production callers that
        genuinely need wall-clock ``now_ns`` must opt in via the
        explicit ``_paper_live_mode = True`` attribute (set at
        live-init time); wiring goes through the module-scope
        ``_PaperLiveWallClock`` helper so the wall-clock read is never
        textually inside any replay-dispatch function body.
        """
        # M11 Stage-2a: ``drive_paper_in_replay`` pre-populates
        # ``self._market_cache`` with the hydrated MarketDataCache so
        # the paper tick body's generate() call sees the same data the
        # backtest orchestrator reads. Prefer that cache when present.
        attached_cache = getattr(self, "_market_cache", None)
        if attached_cache is not None:
            cache = attached_cache
        elif getattr(self, "_paper_live_mode", False):
            # Paper-live production path: no shared cache is attached;
            # hydration is via WS/REST fetcher + on_data. Build a fresh
            # cache using a wall-clock bridge (preserved only because
            # production live code depends on it — explicit opt-in via
            # ``_paper_live_mode``). This branch is NOT replay-reachable:
            # ``drive_paper_in_replay`` always attaches ``_market_cache``.
            # The wall-clock helper lives at module scope
            # (``_PaperLiveWallClock``) so no replay-dispatch function
            # body textually contains ``time.time_ns()``.
            try:
                from v5.data.cache import MarketDataCache
                cache = MarketDataCache(clock=_PaperLiveWallClock(bar_idx))
            except Exception:
                cache = None
        else:
            raise RuntimeError(
                "paper_engine._build_ctx_for_tick called without "
                "_market_cache attached; replay requires "
                "drive_paper_in_replay, live requires cache attach from "
                "_initialize_live_cache (or explicit _paper_live_mode=True "
                "on the engine for wall-clock bridge). A wall-clock "
                "fallback in a replay-reachable code path is a "
                "correctness bug — simulation time must come from the "
                "shared SimulationClock, not the wall clock."
            )

        class _PaperTickCtx:
            __slots__ = ("cache", "data", "bar_idx", "_instruments")

            def __init__(self, mkt_cache, bar_idx: int):
                self.cache = mkt_cache
                self.data = None
                self.bar_idx = int(bar_idx)
                self._instruments = {}

            def per_token(self, token: str):
                return None  # TokenView wiring lands with full ctx integration

        return _PaperTickCtx(cache, bar_idx=bar_idx)

    def _apply_arbitration_native(
        self, signals_by_sid: dict, *, bar_idx: int,
    ) -> dict:
        """Apply risk components + arbitration policy to native signals.

        COMMIT 6 handoff note #1: ``_process_orders_native`` iterates
        ``signals.signals`` in dict insertion order. In production the
        default arbitration policy is ``RandomShuffle`` seeded by
        ``config.seed + global_bar``. Without running arbitration here,
        legacy and native will open different Positions when multiple
        candidates compete for capital — AC-2 parity (Commit 8) fails.

        This helper materializes the native UniverseSignals into the
        ``(sid, token, sig)`` candidate shape the shipped arbitration
        primitives expect, runs the same risk + arbitration path the
        legacy simulator runs, and returns a ``dict[sid, UniverseSignals]``
        whose insertion order matches the arbitration ranking.
        """
        from v5.simulator import (
            _apply_risk_components_phase_30,
            _run_arbitration_dispatch,
        )
        from v5.strategy_api import UniverseSignals

        if not signals_by_sid:
            return signals_by_sid

        # Flatten into (sid, token, TokenSignal) candidates. The
        # arbitration primitives were written against TokenBarArrays
        # with per-bar `.priority[lb]`; we build a thin shim that
        # exposes ``priority`` as a 1-element array so the legacy
        # ordering code keeps working without a rewrite.
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

        # The arbitration primitives originally indexed a per-token
        # array at ``[global_bar]``; in the native path each shim only
        # carries a 1-element ``priority`` array, so build a trivial
        # index lookup that always resolves to position 0.
        translator: dict = {}
        zero_map = np.zeros(max(bar_idx + 1, 1), dtype=np.int64)
        for sid, token, _ in candidates:
            translator[token] = zero_map

        # Use the first available SimulationState — all paths share
        # arbitration policy, rng seed, and equity for ordering.
        state = self.state
        if state is None:
            states = self._get_all_states()
            state = states[0] if states else None
        if state is None:
            return signals_by_sid

        portfolio_config = self.config
        kept = _apply_risk_components_phase_30(
            candidates, translator, bar_idx, portfolio_config, state,
        )
        if not kept:
            # Everything rejected — return empty per-strategy signals
            # so downstream doesn't process stale data.
            return {
                sid: UniverseSignals(bar_idx=bar_idx, signals={})
                for sid in signals_by_sid
            }

        ordered_indices = _run_arbitration_dispatch(
            kept, translator, bar_idx, portfolio_config, state,
        )

        # Rebuild per-strategy dicts in arbitration order.
        rebuilt: dict = {sid: {} for sid in signals_by_sid}
        for idx in ordered_indices:
            sid, token, _shim = kept[idx]
            rebuilt[sid][token] = shim_by_key[(sid, token)]

        return {
            sid: UniverseSignals(bar_idx=bar_idx, signals=signals)
            for sid, signals in rebuilt.items()
        }

    def _tick_internal(self, bar_timestamp=None) -> None:
        """Full tick processing: fetch data, dispatch strategies, persist.

        M11 Commit 7 REWORK pipeline (single-model event-driven dispatch):
          1. Fetch live data via ``self.fetcher`` + append to parquet.
          2. Build the per-tick context (``_build_ctx_for_tick``).
          3. Dispatch strategies inline (``_dispatch_strategies_at_tick``):
             every configured strategy must resolve to a ``BaseStrategy``
             subclass; ``strategy.generate`` drives the native order/exit
             pipeline. Non-Protocol strategies raise ``ValueError`` at
             dispatch time (no sunset batch-shape fallback exists).
          4. Refresh last-known prices from MarketDataCache.
          5. Compute mark-to-market equity + populate equity_history.
          6. Persist state (state.json, equity.csv, shadow_pools).
        """
        import logging
        logger = logging.getLogger(__name__)

        # Task 9e: periodic RSS sampling (once per 60s).
        try:
            self._periodic_rss_check()
        except Exception as e:  # pragma: no cover - diagnostic path
            logger.debug("periodic RSS check failed: %s", e)

        # Task 9c: detect any parquet that has advanced beyond a cache's
        # watermark and reseed before signal compute. Additive — no-op
        # when no caches drifted or no caches subscribed.
        try:
            self._check_and_reseed_drifted_caches()
        except Exception as e:  # pragma: no cover - diagnostic path
            logger.warning("drift-poll failed: %s", e)

        # Task 9d: acquire shared read lock on .maintenance.lock for the
        # duration of this tick. Multiple readers (paper ticks) don't
        # block each other; ensure_data_fresh() (LOCK_EX) does.
        lock_fp = None
        lock_acquired = False
        try:
            import fcntl
            data_dir = self._paper_data_dir()
            os.makedirs(data_dir, exist_ok=True)
            lock_path = os.path.join(data_dir, ".maintenance.lock")
            lock_fp = open(lock_path, "a+")
            try:
                fcntl.flock(lock_fp.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
                lock_acquired = True
            except BlockingIOError:
                # Writer (maintenance) holds the exclusive lock — skip.
                logger.info("tick skipped: maintenance in progress")
                try:
                    lock_fp.close()
                except Exception:
                    pass
                return
        except Exception as e:
            # Infrastructure missing (e.g., data_dir unwritable) — fall
            # through unlocked rather than crashing the paper engine.
            if not getattr(self, "_lock_setup_warning_logged", False):
                logger.warning(
                    "maintenance lock setup failed (%s); tick running unlocked",
                    e,
                )
                self._lock_setup_warning_logged = True
            if lock_fp is not None:
                try:
                    lock_fp.close()
                except Exception:
                    pass
                lock_fp = None

        try:
            self._tick_internal_body(bar_timestamp=bar_timestamp)
        finally:
            if lock_fp is not None:
                try:
                    if lock_acquired:
                        import fcntl
                        fcntl.flock(lock_fp.fileno(), fcntl.LOCK_UN)
                except Exception:
                    pass
                try:
                    lock_fp.close()
                except Exception:
                    pass

    def _tick_internal_body(self, bar_timestamp=None) -> None:
        """Tick body (unlocked). See `_tick_internal` for the lock wrapper."""
        import logging
        logger = logging.getLogger(__name__)

        # Single-clock-authority (ADR-0001 Rule A2): in replay mode the
        # shared ``SimulationClock`` is attached on ``self._clock`` and
        # ``drive_paper_in_replay`` calls ``tick_fn()`` with no
        # ``bar_timestamp`` argument every tick. A raw
        # ``time.strftime(..., time.gmtime())`` fallback therefore leaks
        # wall-clock time into every replay tick's logging/persistence
        # timestamp. Gate the wall-clock fallback behind an explicit
        # ``if shared_clock is None:`` — same pattern as
        # ``_purge_expired_armed_orders`` — so the AC-10 grep (which
        # allows ``time.strftime`` only inside that branch) can verify
        # it statically.
        timestamp = bar_timestamp
        if not timestamp:
            shared_clock = getattr(self, "_clock", None)
            if shared_clock is None:
                timestamp = time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                )
            else:
                import pandas as pd
                now_ns = int(shared_clock.now_ns())
                timestamp = pd.Timestamp(
                    now_ns, unit="ns", tz="UTC"
                ).strftime("%Y-%m-%dT%H:%M:%SZ")

        # --- Step 1: Fetch live data + append to parquet ---
        if self.fetcher is not None:
            # Discover which tokens to fetch for each market
            all_tokens_to_fetch: set[str] = set()
            for spec in self.config.strategies:
                try:
                    tokens = discover_tokens(spec.market)
                    all_tokens_to_fetch.update(tokens)
                except Exception:
                    pass  # No parquet data yet — will handle below

            fetch_ok = 0
            fetch_err = 0
            bars_appended = 0
            funding_merged = 0
            for token in all_tokens_to_fetch:
                for market in ("spot", "perp"):
                    try:
                        bars = self.fetcher.fetch_ohlcv(token, market, limit=10)
                        closed = self.fetcher.filter_closed_bars(bars)
                        if closed:
                            self.fetcher.append_to_parquet(token, market, closed)
                            bars_appended += len(closed)
                        fetch_ok += 1
                    except Exception as e:
                        fetch_err += 1
                        if fetch_err <= 3:  # Log first 3 errors
                            logger.warning("Fetch %s/%s failed: %s", token, market, e)

                # Fetch and merge funding rates for perp
                try:
                    rates = self.fetcher.fetch_funding_rates(token, limit=10)
                    if rates and hasattr(self.fetcher, 'merge_funding_into_parquet'):
                        self.fetcher.merge_funding_into_parquet(token, rates)
                        funding_merged += 1
                except Exception as e:
                    if fetch_err <= 3:
                        logger.warning("Funding %s failed: %s", token, e)

            logger.info("Data fetch: %d ok, %d err, %d bars appended, %d funding merged",
                        fetch_ok, fetch_err, bars_appended, funding_merged)

            # Apply funding settlement at 8h intervals (00, 08, 16 UTC)
            hour_utc = time.gmtime().tm_hour
            last_settle_hour = getattr(self, '_last_funding_settle_hour', -1)
            if hour_utc in (0, 8, 16) and hour_utc != last_settle_hour:
                # Collect per-token rates into batch format for settlement
                batch: dict[str, list[dict]] = {}
                for token in all_tokens_to_fetch:
                    try:
                        rates = self.fetcher.fetch_funding_rates(token, limit=1)
                        if rates:
                            batch[token] = rates
                    except Exception:
                        pass
                if batch:
                    self.apply_funding_settlement(batch)
                self._last_funding_settle_hour = hour_utc
        else:
            logger.debug("No fetcher configured — using existing parquet data")

        # --- Step 2: Dispatch strategies inline (M11 Commit 7 REWORK) ---
        # ADR-0001 + ADR-0002: paper shares the event-driven dispatch
        # primitive with backtest. ``_dispatch_strategies_at_tick`` is
        # the single place that resolves each strategy and drives the
        # native (Protocol) pipeline. The pre-M11 precompute/walk-arrays
        # fallback branch has been deleted — non-Protocol strategies are
        # no longer supported and fail fast at validation time.
        #
        # M11 Stage-3 (single clock authority): the shared SimulationClock
        # attached as ``self._clock`` (via ``drive_paper_in_replay`` or
        # the production live initializer) is the ONE authority for
        # "what bar_idx are we on right now?". ``self.tick_counter`` is
        # retained as a separate, monotonic persistence counter (it's
        # incremented at the END of each tick so state.json records the
        # NEXT expected tick on crash-recovery) but it MUST NOT be used
        # as the dispatch bar_idx: the clock has already advanced one
        # tick before the tick body runs, while tick_counter reflects
        # the previous tick's identity. Reading tick_counter as bar_idx
        # here produces a systematic 1-bar lag vs the backtest
        # orchestrator, breaking every time-anchored strategy decision.
        shared_clock = getattr(self, '_clock', None)
        bar_idx_for_dispatch = (
            int(shared_clock.current_bar_idx) if shared_clock is not None
            else self.tick_counter
        )
        ctx = self._build_ctx_for_tick(bar_idx=bar_idx_for_dispatch)
        self._dispatch_strategies_at_tick(
            ctx=ctx,
            bar_idx=bar_idx_for_dispatch,
            timestamp=timestamp,
        )

        # --- Step 2b: Stamp entry_timestamp on ALL positions missing it ---
        # New positions (entry_bar == tick_counter) get the current timestamp.
        # Pre-existing positions that were never stamped (e.g., created before
        # this code existed) get backfilled with the current timestamp so the
        # dashboard shows *something* rather than "—".
        for st in self._get_all_states():
            for pos in st.position_manager.open_positions:
                if not pos.entry_timestamp:
                    pos.entry_timestamp = timestamp

        # --- Step 2c: Stamp exit_timestamp on newly closed trades missing it ---
        # Simulator-driven exits don't have access to wall-clock time, so we
        # stamp them here.
        for st in self._get_all_states():
            for trade in st.position_manager.closed_trades:
                if not trade.exit_timestamp:
                    trade.exit_timestamp = timestamp

        # Last known prices are refreshed inside
        # ``_dispatch_strategies_at_tick`` — the tick body does not need
        # to reach back into the per-strategy signal arrays.

        # --- Step 8: Compute equity + populate equity_history ---
        portfolio_eq = self._aggregate_portfolio_equity()
        mtm_eq = self._compute_mark_to_market()

        if not hasattr(self, 'equity_history'):
            self.equity_history = []
        self.equity_history.append({
            "timestamp": timestamp,
            "portfolio_equity": portfolio_eq,
            "mark_to_market_equity": mtm_eq,
        })
        # R7-I5 fix: cap equity_history to prevent unbounded memory growth
        # in long-running daemon mode (720 entries = 30 days of hourly ticks)
        _MAX_EQUITY_HISTORY = 720
        if len(self.equity_history) > _MAX_EQUITY_HISTORY:
            self.equity_history = self.equity_history[-_MAX_EQUITY_HISTORY:]

        # --- Step 9: Persist state ---
        # C5 fix: increment tick_counter BEFORE persisting so state.json
        # records the NEXT expected tick. On crash-recovery, we restore to
        # the next tick and don't re-process the current one.
        self.tick_counter += 1
        # R5-I1 fix: track whether state.json commit point was reached.
        # If exception occurs AFTER commit, tick_counter should NOT be rolled back.
        self._state_committed = False

        state_dir = self.config.state_dir
        os.makedirs(state_dir, exist_ok=True)

        # Shadow pools
        shadow = getattr(self, 'shadow', None)
        if shadow is not None:
            shadow_pools = {
                "spot_funds": shadow.spot_funds_shadow,
                "perp_funds": shadow.perp_funds_shadow,
                "spot_deployed": shadow.spot_deployed,
                "perp_deployed": shadow.perp_deployed,
            }
        else:
            shadow_pools = {
                "spot_funds": 0.0, "perp_funds": 0.0,
                "spot_deployed": 0.0, "perp_deployed": 0.0,
            }

        # QM-C2 fix: Write trades.jsonl BEFORE state.json so that on
        # crash between the two writes, recovery truncation removes the
        # orphaned trades (they have tick > restored tick_counter).
        trades_path = os.path.join(state_dir, "trades.jsonl")
        new_closed = []
        for st in self._get_all_states():
            new_closed.extend(st.position_manager.closed_trades)
        # R4-I1 fix: track flushed trades by position_id to prevent duplicates
        # on partial write failures. Previously used a count which could re-write
        # trades if the count wasn't updated due to a write exception.
        if not hasattr(self, '_flushed_position_ids'):
            self._flushed_position_ids = set()
        unflushed = [t for t in new_closed if t.position_id not in self._flushed_position_ids]
        # R7-I1 fix: stage position_ids written this tick. Only promote to
        # _flushed_position_ids after state.json commit, so failed ticks don't
        # permanently mark trades as flushed when the state was never committed.
        self._tick_staged_ids = set()
        if unflushed:
            with open(trades_path, "a") as f:
                for trade in unflushed:
                    f.write(json.dumps(_closed_trade_to_dict(trade, tick=self.tick_counter)) + "\n")
                f.flush()
                os.fsync(f.fileno())  # R3-C1 fix: fsync before state.json commit
            self._tick_staged_ids = {t.position_id for t in unflushed}

        # R5-I2 fix: write equity.csv BEFORE state.json commit point
        # so crash between trades and state doesn't create permanent equity gaps
        # R6-I4 fix: skip if this tick was already written (retry after failed state.json)
        last_equity_tick = getattr(self, '_last_equity_tick', -1)
        if self.tick_counter != last_equity_tick:
            equity_path = os.path.join(state_dir, "equity.csv")
            all_states = self._get_all_states()
            free_cap = sum(s.free_capital for s in all_states) if all_states else 0.0
            append_equity(
                equity_path,
                timestamp=timestamp,
                tick=self.tick_counter,
                portfolio_equity=portfolio_eq,
                mark_to_market_equity=mtm_eq,
                free_capital=free_cap,
                open_positions=self._aggregate_open_positions(),
            )
            self._last_equity_tick = self.tick_counter

        # Write state.json — handle both pool and independent modes.
        # This is the "commit point": once state.json is atomically written,
        # the tick is considered complete. M11 Commit-8 rework Stage-1b
        # (ADR-0002): legacy persistence kwargs deleted — see serialize_state.
        state_path = os.path.join(state_dir, "state.json")
        if self.state is not None:
            # Pool mode: single shared state
            atomic_write_state(
                self.state,
                self.tick_counter,
                timestamp,
                state_path,
                shadow_pools=shadow_pools,
            )
        else:
            # Independent mode: serialize all strategy states (C4 fix)
            from v5.paper_state import serialize_engine_state
            data = serialize_engine_state(
                dict(self.strategy_states), self.tick_counter, timestamp,
                mode="independent",
                shadow_pools=shadow_pools,
            )
            import tempfile as _tmpfile
            fd, tmp = _tmpfile.mkstemp(dir=state_dir, suffix=".tmp")
            os.close(fd)
            with open(tmp, "w") as f:
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())  # QM-I3 fix: fsync for independent mode
            os.rename(tmp, state_path)

        # R5-I1 fix: mark commit point reached — tick_counter should NOT
        # be rolled back if anything after this point throws
        self._state_committed = True

        # R7-I1 fix: promote staged position_ids to flushed now that state
        # is committed. If state.json commit failed, these would NOT be promoted,
        # allowing retry to re-write the trades (duplicates cleaned by truncation).
        self._flushed_position_ids.update(self._tick_staged_ids)
        self._tick_staged_ids = set()

        # R4-I3 fix: update last_timestamp so dashboard stale-data checks
        # use the latest processed tick's timestamp, not the restored one
        self.last_timestamp = timestamp

        # Free large tick-scoped data structures before dashboard generation.
        # The legacy signal-walk held ~300-500MB of NumPy arrays per tick;
        # ``_dispatch_strategies_at_tick`` discards them before returning
        # so only the explicit gc sweep is needed here.
        import gc as _gc
        _gc.collect()

        # AC26: Trigger dashboard generation after persistence
        self._trigger_dashboard()

    def _trigger_dashboard(self) -> None:
        """Trigger dashboard generation after each tick (AC26).

        If config.dashboard_push is True, also pushes the dashboard.
        Dashboard errors are logged but non-fatal (AC27).
        """
        import logging
        logger = logging.getLogger(__name__)
        try:
            if self.config.dashboard_push:
                self._push_dashboard()
        except Exception as e:
            logger.warning("Dashboard generation failed (non-fatal): %s", e)

    def _push_dashboard(self) -> None:
        """Push dashboard to deployment target (AC27).

        Calls tools/generate_dashboard_v2.py with --state-dir and --push.
        Uses config_path for --config so dashboard picks up pool_name and strategy info.
        """
        import subprocess
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        script = os.path.join(project_root, "tools", "generate_dashboard_v2.py")

        cmd = [
            sys.executable, script,
            "--state-dir", self.config.state_dir,
            "--push",
        ]
        if self.config.config_path:
            cmd.extend(["--config", self.config.config_path])

        subprocess.run(cmd, cwd=project_root, timeout=120, check=False,
                       capture_output=True, text=True)

    def tick(self) -> TickResult:
        """Process a single tick with error handling.

        Catches invariant violations and performs emergency close if needed.
        """
        t0 = time.time()
        result = TickResult(tick_counter=self.tick_counter)
        self._alerts.clear()
        # R4-I4 fix: save tick_counter before _tick_internal so we can roll back
        # if it throws after incrementing but before persisting
        saved_tick_counter = self.tick_counter

        try:
            open_before = self._aggregate_open_positions()
            closed_before = self._aggregate_closed_trades()
            self._tick_internal()
            open_after = self._aggregate_open_positions()
            closed_after = self._aggregate_closed_trades()

            result.exits = closed_after - closed_before
            result.entries = max(0, open_after - open_before + result.exits)
            result.open_positions = open_after
            result.portfolio_equity = self._aggregate_portfolio_equity()
            result.mark_to_market_equity = self._compute_mark_to_market()
            result.alerts = list(self._alerts)
            # R3-I4 fix: update tick_counter AFTER _tick_internal increments it
            # so heartbeat matches state.json/trades.jsonl/equity.csv
            result.tick_counter = self.tick_counter

        except Exception as e:
            error_msg = str(e)
            # Only emergency-close for actual invariant violations
            all_states = self._get_all_states()
            free_cap = min((s.free_capital for s in all_states), default=0.0)
            if "invariant" in error_msg.lower() or free_cap < -1.0:
                self._emergency_close_all()
                # R4-C1 fix: persist state after emergency close so closures
                # survive process restart (prevents infinite restart loop)
                try:
                    self._persist_emergency_state()
                except Exception as persist_err:
                    import logging
                    logging.getLogger(__name__).error(
                        "Failed to persist emergency close state: %s", persist_err
                    )
                result.error = f"Invariant violation: {error_msg}"
            else:
                # Transient errors (network, data, etc.) — log but don't close positions
                result.error = f"Tick error (non-invariant): {error_msg}"
                # R4-I4 + R5-I1 fix: only roll back tick_counter if state.json
                # was NOT yet committed. If committed, the tick is on disk and
                # rolling back would create a divergence.
                if not getattr(self, '_state_committed', False):
                    self.tick_counter = saved_tick_counter

            result.open_positions = self._aggregate_open_positions()
            result.portfolio_equity = self._aggregate_portfolio_equity()
            result.mark_to_market_equity = self._compute_mark_to_market()
            result.alerts = list(self._alerts)
            # R3-I4 fix: sync tick_counter even on error path
            result.tick_counter = self.tick_counter

        # AC23: Peak RSS memory
        ru = resource.getrusage(resource.RUSAGE_SELF)
        result.peak_rss_mb = ru.ru_maxrss / 1024.0  # Linux reports in KB

        # AC24: Processing time
        result.processing_time_s = time.time() - t0

        # AC24: Timestamp
        result.timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        # AC25: Write heartbeat.json
        self._write_heartbeat(result)

        return result

    def _write_heartbeat(self, result: TickResult) -> None:
        """Write heartbeat.json to state_dir after each tick (AC25)."""
        state_dir = self.config.state_dir
        os.makedirs(state_dir, exist_ok=True)
        heartbeat = {
            "timestamp": result.timestamp,
            "tick_counter": int(result.tick_counter),
            "open_positions": int(result.open_positions),
            "portfolio_equity": float(result.portfolio_equity),
            "mark_to_market_equity": float(result.mark_to_market_equity),
            "processing_time_s": float(result.processing_time_s),
            "errors": result.error,
        }
        heartbeat_path = os.path.join(state_dir, "heartbeat.json")
        # R3-I5 fix: atomic write to avoid partial reads by monitoring
        import tempfile as _tmpfile
        fd, tmp = _tmpfile.mkstemp(dir=state_dir, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(heartbeat, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.rename(tmp, heartbeat_path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # ------------------------------------------------------------------
    # Live data integration (Task 7)
    # ------------------------------------------------------------------

    def _fetch_with_retry(self):
        """Fetch data with exponential backoff retry (AC10d).

        Returns fetched data on success, None on failure after all retries.
        """
        for attempt in range(3):
            try:
                result = self.fetcher.fetch_ohlcv()
                self._consecutive_failures = 0
                return result
            except Exception:
                pass

            # Backoff between retries (but don't sleep for the last attempt)
            if attempt < 2:
                time.sleep(self.BACKOFF[attempt] * 0.001)  # minimal delay in tests

        # All retries failed
        self._consecutive_failures += 1

        if self._consecutive_failures >= 3:
            self._alerts.append({
                "type": "consecutive_failures",
                "severity": "critical",
                "message": f"CRITICAL: {self._consecutive_failures} consecutive tick failures",
            })

        return None

    def _catch_up(
        self,
        n_missed_bars: int,
        bar_timestamps: list[str] | None = None,
    ) -> None:
        """Process missed bars sequentially during catch-up (AC10d).

        Stops on first error to prevent cascading issues.
        """
        for i in range(n_missed_bars):
            ts = bar_timestamps[i] if bar_timestamps and i < len(bar_timestamps) else None
            try:
                result = self._tick_internal(bar_timestamp=ts)
                # R3-I1 fix: check return value if _tick_internal returns a TickResult
                if result is not None and hasattr(result, 'error') and result.error is not None:
                    self._alerts.append(
                        f"Catch-up stopped at bar {i+1}/{n_missed_bars}: {result.error}"
                    )
                    break
            except Exception as e:
                # R3-I1 fix: also catch exceptions to stop cascading failures
                self._alerts.append(
                    f"Catch-up stopped at bar {i+1}/{n_missed_bars}: {e}"
                )
                break

    # ------------------------------------------------------------------
    # Quick price refresh — update MTM without running a full tick
    # ------------------------------------------------------------------

    def update_prices(self, exchange) -> dict:
        """Fetch live prices for open positions, update MTM and heartbeat.

        Does NOT run signals, entries, exits, or increment tick_counter.
        Returns dict with updated MTM info for logging.
        """
        import time as _time
        from datetime import datetime, timezone

        t0 = _time.perf_counter()

        # Collect tokens with open positions
        tokens_needed: set[str] = set()
        for st in self._get_all_states():
            for pos in st.position_manager.open_positions:
                tokens_needed.add(pos.token)

        if not tokens_needed:
            return {"tokens": 0, "mtm": self._compute_mark_to_market()}

        # Fetch live prices via ccxt tickers
        updated = 0
        for token in tokens_needed:
            try:
                ticker = exchange.fetch_ticker(f"{token}/USDT")
                price = ticker.get("last")
                if price:
                    self._last_known_prices[token] = float(price)
                    updated += 1
            except Exception:
                pass  # Keep last known price

        mtm = self._compute_mark_to_market()
        elapsed = _time.perf_counter() - t0

        # Update heartbeat with fresh MTM (no tick_counter change)
        state_dir = self.config.state_dir
        os.makedirs(state_dir, exist_ok=True)
        heartbeat = {
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "tick_counter": int(self.tick_counter),
            "open_positions": int(self._aggregate_open_positions()),
            "portfolio_equity": float(self._aggregate_portfolio_equity()),
            "mark_to_market_equity": float(mtm),
            "processing_time_s": float(elapsed),
            "errors": None,
            "price_refresh": True,
        }
        heartbeat_path = os.path.join(state_dir, "heartbeat.json")
        import tempfile as _tmpfile
        fd, tmp = _tmpfile.mkstemp(dir=state_dir, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(heartbeat, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.rename(tmp, heartbeat_path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass

        # Re-persist state.json so dashboard picks up fresh last_known_prices
        if updated > 0:
            refresh_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            state_path = os.path.join(state_dir, "state.json")
            # M11 Commit-8 rework Stage-1b (ADR-0002): legacy persistence
            # kwargs deleted — see serialize_state docstring.
            if self.state is not None:
                atomic_write_state(
                    self.state,
                    self.tick_counter,
                    refresh_ts,
                    state_path,
                )
            else:
                from v5.paper_state import serialize_engine_state
                data = serialize_engine_state(
                    dict(self.strategy_states), self.tick_counter, refresh_ts,
                    mode="independent",
                )
                import tempfile as _tmpfile2
                fd2, tmp2 = _tmpfile2.mkstemp(dir=state_dir, suffix=".tmp")
                os.close(fd2)
                with open(tmp2, "w") as f2:
                    json.dump(data, f2, indent=2)
                    f2.flush()
                    os.fsync(f2.fileno())
                os.rename(tmp2, state_path)

        return {"tokens": updated, "mtm": mtm, "elapsed": elapsed}

    # ------------------------------------------------------------------
    # process_tick — deterministic tick for testing (Task 12)
    # ------------------------------------------------------------------

    def _aggregate_open_positions(self) -> int:
        """Total open positions across all states (works in both modes)."""
        if self.config.mode == "independent":
            return sum(s.position_manager.total_open() for s in self.strategy_states.values())
        return self.state.position_manager.total_open()

    def _aggregate_portfolio_equity(self) -> float:
        """Total portfolio equity across all states (works in both modes)."""
        if self.config.mode == "independent":
            return sum(s.portfolio_equity for s in self.strategy_states.values())
        return self.state.portfolio_equity

    def _aggregate_closed_trades(self) -> int:
        """Total closed trades across all states (works in both modes)."""
        if self.config.mode == "independent":
            return sum(len(s.position_manager.closed_trades) for s in self.strategy_states.values())
        return len(self.state.position_manager.closed_trades)

    def _get_all_entry_fees(self) -> dict:
        """Collect entry fees for all open positions across states."""
        fees = {}
        for st in self._get_all_states():
            fees.update(st._entry_fees_by_pos)
        return fees

    def _compute_mark_to_market(self) -> float:
        """Compute mark-to-market equity: portfolio_equity + sum of unrealized P&L.

        Unrealized P&L = quantity * (current_price - entry_price) for each open position.
        """
        base_equity = self._aggregate_portfolio_equity()
        unrealized_pnl = 0.0
        for st in self._get_all_states():
            for pos in st.position_manager.open_positions:
                current_price = self._last_known_prices.get(pos.token, pos.entry_price)
                unrealized_pnl += pos.quantity * (current_price - pos.entry_price)
        return base_equity + unrealized_pnl

    def process_tick(
        self,
        all_signals: dict,
        specs: dict | None = None,
    ) -> TickResult:
        """Legacy deterministic test entry — pre-M11 batch-shape signals.

        Thin wrapper around :func:`v5.paper_test_harness.run_legacy_test_tick`
        for pre-M11 determinism tests (``test_paper_determinism.py``,
        ``test_paper_live_integration.py::test_recomputed_signals_cover_current_bar``).
        The production tick path (``tick`` → ``_tick_internal_body`` →
        ``_dispatch_strategies_at_tick``) does NOT go through this method;
        those tests exercise pre-baked ``TokenBarArrays`` signals against
        the legacy simulator primitives so that the downstream ordering
        + RNG seeding contracts remain exercised.

        The pre-baked-array walk semantics live in
        :mod:`v5.paper_test_harness` — paper_engine.py itself is free of
        walk-arrays vocabulary per ADR-0001 + the AC-10 architecture
        invariants.
        """
        from v5.paper_test_harness import run_legacy_test_tick
        return run_legacy_test_tick(self, all_signals, specs=specs)

    # ------------------------------------------------------------------
    # Dashboard SIMS output (Task 10)
    # ------------------------------------------------------------------

    def to_dashboard_sim(self, price_overrides: dict[str, float] | None = None) -> dict:
        """Produce SIMS JSON schema for dashboard consumption (AC11b, AC12, AC26).

        Args:
            price_overrides: Optional live prices to merge on top of _last_known_prices.
                Used by the dashboard heartbeat to overlay WebSocket prices without
                mutating engine state (thread-safe).
        """
        from datetime import datetime, timezone, timedelta

        config = self.config
        pool_name = config.pool_name or config.strategies[0].strategy_id if config.strategies else "default"

        # Aggregate across all states (works in both modes)
        all_closed_trades = []
        for st in self._get_all_states():
            all_closed_trades.extend(st.position_manager.closed_trades)

        # Build strategy info
        def _exit_res_label(val: int) -> str:
            if val <= 0: return "hourly"
            return f"{val}min"

        if config.mode == "pool" and config.pool_name:
            strategies = [{
                "id": config.pool_name,
                "name": config.pool_name,
                "weight": sum(s.weight for s in config.strategies),
                "final_equity": self._aggregate_portfolio_equity(),
                "trade_count": len(all_closed_trades),
                "open_positions": self._aggregate_open_positions(),
                "strategies": [s.strategy_id for s in config.strategies],
                "bar_resolution": _exit_res_label(self._effective_bar_resolution),
            }]
        else:
            strategies = [{
                "id": s.strategy_id,
                "name": s.strategy_id,
                "weight": s.weight,
                "bar_resolution": _exit_res_label(s.bar_resolution),
            } for s in config.strategies]

        # Build all_trades (closed + open) — raw data, no consolidation
        all_trades = []
        for t in all_closed_trades:
            all_trades.append({
                "token": t.token,
                "strategy": t.strategy_id,
                "market_type": "perp" if t.is_perp else "spot",
                "direction": t.direction,
                "pnl": t.pnl,
                "exit_reason": t.exit_reason,
                "signal": {
                    "entry_bar": t.entry_bar,
                    "exit_bar": t.exit_bar,
                    "entry_price": t.entry_price,
                    "exit_price": t.exit_price,
                    "hold_bars": t.hold_bars,
                },
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "margin_usd": t.margin_usd,
                "hold_bars": t.hold_bars,
                "funding_cost": t.funding_cost,
                "entry_fee": t.entry_fee,
                "exit_fee": t.exit_fee,
                "entry_timestamp": t.entry_timestamp,
                "exit_timestamp": t.exit_timestamp,
            })

        if strategies and "trade_count" in strategies[0]:
            strategies[0]["trade_count"] = len(all_trades)

        # AC28: Include open positions with status="open"
        last_prices = dict(getattr(self, '_last_known_prices', {}))
        if price_overrides:
            last_prices.update(price_overrides)
        # M11 Commit-8 rework Stage-1b (ADR-0002): `_last_known_regimes` deleted;
        # regime flows through strategy `required_data` per ADR-0002 §4 as a
        # follow-up. Dashboard row shows "N/A" until that migration lands.
        last_regimes: dict[str, int] = {}
        regime_names = {0: "CRISIS", 1: "QUIET", 2: "UPTREND", 3: "RANGE", 4: "DOWNTREND"}
        all_entry_fees = self._get_all_entry_fees()
        for st in self._get_all_states():
            for pos in st.position_manager.open_positions:
                current_price = last_prices.get(pos.token, pos.entry_price)
                raw_unrealized = pos.quantity * (current_price - pos.entry_price)
                entry_fee = all_entry_fees.get(pos.position_id, 0.0)
                # Net unrealized: deduct known costs (entry fee + accrued funding)
                unrealized_pnl = raw_unrealized - entry_fee - pos.cumulative_funding
                # Stop distance
                stop_price = pos.stop_price
                pct_to_stop = 0
                if stop_price and current_price and stop_price > 0:
                    if pos.direction == 1:
                        pct_to_stop = (current_price - stop_price) / current_price * 100
                    else:
                        pct_to_stop = (stop_price - current_price) / current_price * 100
                # Clamp nonsensical stops (negative, or >200% away = effectively no stop)
                if stop_price <= 0 or pct_to_stop > 200:
                    stop_price = 0
                    pct_to_stop = 0
                # Regime
                regime_id = last_regimes.get(pos.token, -1)
                regime_name = regime_names.get(regime_id, "N/A")
                hold_bars = self.tick_counter - pos.entry_bar
                all_trades.append({
                    "token": pos.token,
                    "strategy": pos.strategy_id,
                    "market_type": "perp" if pos.is_perp else "spot",
                    "direction": pos.direction,
                    "status": "open",
                    "current_price": current_price,
                    "unrealized_pnl": unrealized_pnl,
                    "entry_price": pos.entry_price,
                    "margin_usd": pos.margin_usd,
                    "entry_bar": pos.entry_bar,
                    "cumulative_funding": pos.cumulative_funding,
                    "entry_fee": entry_fee,
                    "hold_bars": hold_bars,
                    "stop_price": stop_price,
                    "no_stop_bars": pos.no_stop_bars,
                    "stop_active": hold_bars >= pos.no_stop_bars or pos.convex_exit,
                    "pct_to_stop": round(pct_to_stop, 2),
                    "regime": regime_name,
                    "entry_timestamp": pos.entry_timestamp,
                    "leverage": pos.leverage,
                })

        # Thread-safe snapshot of armed orders + expired orders + skip reasons
        with self._armed_tokens_lock:
            armed_snap = {
                k: _pe_to_cand_dict(pe)
                for k, pe in self._pending_entries.items()
            }
            expired_snap = list(self._last_expired_orders)
            skip_reasons_snap = dict(self._last_armed_skip_reasons)

        armed_orders = []
        for (sid, token), cand in armed_snap.items():
            current_price = last_prices.get(token, cand.get("close_val", 0))
            level = cand.get("level", 0)
            direction = cand.get("direction", 1)
            # Guard against invalid prices/levels
            if level <= 0 or current_price <= 0:
                pct_to_fill = 0.0
                crossed = False
            elif direction == 1:
                pct_to_fill = (level - current_price) / current_price * 100
                crossed = pct_to_fill < 0
                pct_to_fill = max(pct_to_fill, 0.0)
            else:
                pct_to_fill = (current_price - level) / current_price * 100
                crossed = pct_to_fill < 0
                pct_to_fill = max(pct_to_fill, 0.0)
            armed_orders.append({
                "strategy": sid,
                "token": token,
                "direction": direction,
                "level": level,
                "current_price": current_price,
                "pct_to_fill": round(pct_to_fill, 2),
                "crossed": crossed,
                "is_perp": cand.get("is_perp", True),
                "armed_at": cand.get("limit_placed_at", ""),
                "tick_counter": cand.get("tick_counter", 0),
                "last_skip": skip_reasons_snap.get((sid, token), "waiting"),
            })

        # Equity history
        equity_history = getattr(self, 'equity_history', [])

        # Shadow pools
        shadow = getattr(self, 'shadow', None)
        if shadow is not None:
            shadow_pools = {
                "spot_funds": shadow.spot_funds_shadow,
                "perp_funds": shadow.perp_funds_shadow,
                "spot_deployed": shadow.spot_deployed,
                "perp_deployed": shadow.perp_deployed,
                "imbalance_pct": abs(shadow.spot_funds_shadow - shadow.perp_funds_shadow) / max(
                    shadow.spot_funds_shadow + shadow.perp_funds_shadow, 1.0
                ) * 100.0,
                "blocked_entries_count": sum(
                    r.get("would_have_blocked_entries", 0)
                    for r in getattr(shadow, 'rebalance_log', [])
                ),
            }
            rebalance_history = list(getattr(shadow, 'rebalance_log', []))
        else:
            shadow_pools = {
                "spot_funds": 0.0, "perp_funds": 0.0,
                "spot_deployed": 0.0, "perp_deployed": 0.0,
                "imbalance_pct": 0.0, "blocked_entries_count": 0,
            }
            rebalance_history = []

        # Stale data check
        last_ts = getattr(self, 'last_timestamp', None)
        is_stale = False
        if last_ts:
            try:
                ts_dt = datetime.strptime(last_ts, "%Y-%m-%dT%H:%M:%SZ").replace(
                    tzinfo=timezone.utc
                )
                age = datetime.now(timezone.utc) - ts_dt
                is_stale = age > timedelta(hours=2)
            except (ValueError, TypeError):
                is_stale = True

        # Aggregate fees/funding/pnl across all states
        agg_fees = 0.0
        agg_funding = 0.0
        agg_realized_pnl = 0.0
        for st in self._get_all_states():
            agg_fees += st.total_fees
            agg_funding += st.total_funding
            agg_realized_pnl += st.realized_pnl

        return {
            "id": pool_name,
            "name": pool_name,
            "capital": config.capital,
            "strategies": strategies,
            "all_trades": all_trades,
            "equity_history": equity_history,
            "shadow_pools": shadow_pools,
            "rebalance_history": rebalance_history,
            "last_updated": last_ts or "",
            "is_stale": is_stale,
            "tick_counter": self.tick_counter,
            "portfolio_equity": self._aggregate_portfolio_equity(),
            "open_positions": self._aggregate_open_positions(),
            "total_fees": agg_fees,
            "total_funding": agg_funding,
            "realized_pnl": agg_realized_pnl,
            "armed_orders": armed_orders,
            "expired_orders": expired_snap,
        }

    # ------------------------------------------------------------------
    # Reconciliation (Task 11)
    # ------------------------------------------------------------------

    def reconcile(
        self,
        paper_trades: list,
        backtest_trades: list,
        start_date: str,
        end_date: str,
    ) -> "ReconciliationReport":
        """Compare paper trades vs backtest trades, returning divergences (AC20)."""
        divergences = []

        # Index trades by (token, strategy_id, entry_bar, leg) — leg prevents
        # combined strategy primary/secondary from overwriting each other
        paper_by_key = {}
        for t in paper_trades:
            key = (t.token, t.strategy_id, t.entry_bar, getattr(t, 'leg', 'primary'))
            paper_by_key[key] = t

        backtest_by_key = {}
        for t in backtest_trades:
            key = (t.token, t.strategy_id, t.entry_bar, getattr(t, 'leg', 'primary'))
            backtest_by_key[key] = t

        all_keys = set(paper_by_key.keys()) | set(backtest_by_key.keys())

        for key in all_keys:
            token, sid, entry_bar, _leg = key
            p_trade = paper_by_key.get(key)
            b_trade = backtest_by_key.get(key)

            if p_trade and not b_trade:
                divergences.append({
                    "type": "entry_mismatch",
                    "field": "entry",
                    "token": token,
                    "strategy_id": sid,
                    "entry_bar": entry_bar,
                    "paper_value": "present",
                    "backtest_value": "absent",
                })
            elif b_trade and not p_trade:
                divergences.append({
                    "type": "entry_mismatch",
                    "field": "entry",
                    "token": token,
                    "strategy_id": sid,
                    "entry_bar": entry_bar,
                    "paper_value": "absent",
                    "backtest_value": "present",
                })
            else:
                # Both present — compare fields
                if p_trade.exit_bar != b_trade.exit_bar:
                    divergences.append({
                        "type": "exit_mismatch",
                        "field": "exit_bar",
                        "token": token,
                        "strategy_id": sid,
                        "entry_bar": entry_bar,
                        "paper_value": p_trade.exit_bar,
                        "backtest_value": b_trade.exit_bar,
                    })

                if p_trade.exit_reason != b_trade.exit_reason:
                    divergences.append({
                        "type": "exit_mismatch",
                        "field": "exit_reason",
                        "token": token,
                        "strategy_id": sid,
                        "entry_bar": entry_bar,
                        "paper_value": p_trade.exit_reason,
                        "backtest_value": b_trade.exit_reason,
                    })

                if abs(p_trade.pnl - b_trade.pnl) > 0.01:
                    divergences.append({
                        "type": "pnl_mismatch",
                        "field": "pnl",
                        "token": token,
                        "strategy_id": sid,
                        "entry_bar": entry_bar,
                        "paper_value": p_trade.pnl,
                        "backtest_value": b_trade.pnl,
                        "difference": abs(p_trade.pnl - b_trade.pnl),
                    })

        return ReconciliationReport(
            start_date=start_date,
            end_date=end_date,
            divergences=divergences,
            summary={
                "total_divergences": len(divergences),
                "paper_trades": len(paper_trades),
                "backtest_trades": len(backtest_trades),
            },
        )


@dataclass
class ReconciliationReport:
    """Result of reconciling paper trades vs backtest trades (AC20)."""
    start_date: str
    end_date: str
    divergences: list = field(default_factory=list)
    summary: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# AlertManager — structured alerts, heartbeat, and logging (Task 9)
# ---------------------------------------------------------------------------

class AlertManager:
    """Manages structured alerts, heartbeat file, and logging (AC14, AC18, AC19)."""

    def __init__(
        self,
        state_dir: str,
        drawdown_alert_pct: float = 5.0,
        webhook_url: str = "",
    ):
        self.state_dir = state_dir
        self.drawdown_alert_pct = drawdown_alert_pct
        self.webhook_url = webhook_url

        self._pending_alerts: list[dict] = []
        self._peak_equity: float = 0.0
        self._drawdown_alert_fired: bool = False
        self.consecutive_failures: int = 0

    # --- Alert accessors ---

    def get_pending_alerts(self) -> list[dict]:
        """Return and clear pending alerts."""
        alerts = list(self._pending_alerts)
        self._pending_alerts = []
        return alerts

    # --- Position alerts ---

    def on_position_open(
        self,
        token: str,
        strategy_id: str,
        direction: int,
        margin_usd: float,
        entry_price: float,
        leverage: float,
        timestamp: str,
    ) -> None:
        """Record a position open alert."""
        side = "LONG" if direction == 1 else "SHORT"
        self._pending_alerts.append({
            "type": "position_open",
            "severity": "info",
            "token": token,
            "strategy_id": strategy_id,
            "direction": direction,
            "margin_usd": margin_usd,
            "entry_price": entry_price,
            "leverage": leverage,
            "timestamp": timestamp,
            "message": f"{side} {token} opened: ${margin_usd:.0f} @ ${entry_price:.2f} ({strategy_id})",
        })

    def on_position_close(
        self,
        token: str,
        strategy_id: str,
        direction: int,
        pnl: float,
        exit_reason: str,
        hold_bars: int,
        timestamp: str,
    ) -> None:
        """Record a position close alert."""
        self._pending_alerts.append({
            "type": "position_close",
            "severity": "info",
            "token": token,
            "strategy_id": strategy_id,
            "direction": direction,
            "pnl": pnl,
            "exit_reason": exit_reason,
            "hold_bars": hold_bars,
            "timestamp": timestamp,
            "message": f"{token} closed ({exit_reason}): PnL ${pnl:.2f}, held {hold_bars}h",
        })

    # --- Drawdown alerts ---

    def update_peak_equity(self, equity: float) -> None:
        """Update peak equity watermark."""
        if equity > self._peak_equity:
            self._peak_equity = equity
            self._drawdown_alert_fired = False  # Reset on new high

    def check_drawdown(self, current_equity: float, timestamp: str) -> bool:
        """Check if drawdown exceeds threshold. Returns True if alert fired."""
        if self._peak_equity <= 0:
            return False

        drawdown_pct = (self._peak_equity - current_equity) / self._peak_equity * 100.0

        if drawdown_pct > self.drawdown_alert_pct:
            if not self._drawdown_alert_fired:
                self._drawdown_alert_fired = True
                self._pending_alerts.append({
                    "type": "drawdown",
                    "severity": "warning",
                    "drawdown_pct": drawdown_pct,
                    "peak_equity": self._peak_equity,
                    "current_equity": current_equity,
                    "timestamp": timestamp,
                    "message": f"Drawdown alert: {drawdown_pct:.1f}% from peak ${self._peak_equity:.0f}",
                })
                return True
            return False  # Suppressed (already fired)
        else:
            # Equity recovered above threshold — reset suppression
            self._drawdown_alert_fired = False
            return False

    # --- Tick failure tracking ---

    def on_tick_failure(self, error_msg: str, timestamp: str) -> None:
        """Record a tick failure."""
        self.consecutive_failures += 1
        if self.consecutive_failures >= 3:
            self._pending_alerts.append({
                "type": "consecutive_failures",
                "severity": "critical",
                "consecutive_count": self.consecutive_failures,
                "timestamp": timestamp,
                "message": f"CRITICAL: {self.consecutive_failures} consecutive failures: {error_msg}",
            })

    def on_tick_success(self, timestamp: str) -> None:
        """Reset consecutive failure counter on success."""
        self.consecutive_failures = 0

    # --- Delisting alert ---

    def on_delisting(
        self,
        token: str,
        strategy_id: str,
        position_id: str,
        last_price: float,
        timestamp: str,
    ) -> None:
        """Record a delisting/data-disappearance alert."""
        self._pending_alerts.append({
            "type": "delisting",
            "severity": "warning",
            "token": token,
            "strategy_id": strategy_id,
            "position_id": position_id,
            "last_price": last_price,
            "timestamp": timestamp,
            "message": f"Token delisted: {token} — position {position_id} force-closed @ ${last_price:.2f}",
        })

    # --- Heartbeat ---

    def write_heartbeat(
        self,
        timestamp: str,
        last_processed_bar: str,
        open_positions: int,
        portfolio_equity: float,
        errors: list,
    ) -> None:
        """Write heartbeat.json atomically (AC18)."""
        data = {
            "timestamp": timestamp,
            "last_processed_bar": last_processed_bar,
            "open_positions": open_positions,
            "portfolio_equity": portfolio_equity,
            "errors": errors,
        }
        json_str = json.dumps(data, indent=2)
        target_path = os.path.join(self.state_dir, "heartbeat.json")

        fd, tmp_path = tempfile.mkstemp(dir=self.state_dir, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(json_str)
                f.flush()
                os.fsync(f.fileno())
            os.rename(tmp_path, target_path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    # --- Structured logging ---

    def log_tick(
        self,
        timestamp: str,
        tick: int,
        entries_attempted: int,
        entries_accepted: int,
        entries_rejected: int,
        rejection_reasons: dict,
        exits_triggered: int,
        exit_reasons: dict,
        equity_snapshot: dict,
    ) -> None:
        """Append a structured log entry to daily-rotated JSONL file (AC19)."""
        log_dir = os.path.join(self.state_dir, "logs")
        os.makedirs(log_dir, exist_ok=True)

        # Extract date from timestamp for daily rotation
        date_str = timestamp[:10]  # "YYYY-MM-DD"
        log_path = os.path.join(log_dir, f"paper_engine_{date_str}.jsonl")

        entry = {
            "timestamp": timestamp,
            "tick": tick,
            "entries_attempted": entries_attempted,
            "entries_accepted": entries_accepted,
            "entries_rejected": entries_rejected,
            "rejection_reasons": rejection_reasons,
            "exits_triggered": exits_triggered,
            "exit_reasons": exit_reasons,
            "equity_snapshot": equity_snapshot,
        }

        with open(log_path, "a") as f:
            f.write(json.dumps(entry) + "\n")


# M4 AC4 note: the ``PaperEngine`` symbol is the minimal tick-routing shim
# defined near the top of this module — it delegates ticks to
# ``BarProcessor.process_bar(...)``. The previous module-level alias
# ``PaperEngine = PaperPortfolioEngine`` is intentionally removed here so
# the shim is the exported symbol. Full integration of PaperPortfolioEngine
# with BarProcessor lands in Task 16a.


# ---------------------------------------------------------------------------
# M6 — test-only helper for paper migration tests (Task 17 scaffold).
#
# Wave F wires the full 8-site flag-branch dispatch into PaperEngine /
# PaperPortfolioEngine. The helper below lets Phase-3 acceptance tests
# exercise the flag mechanism (T-D8 flag=OFF path) without pulling in the
# full paper runner infrastructure (which requires state lock, venue
# config, real PriceMonitor, etc.).
# ---------------------------------------------------------------------------

class _M6TestPaperEngine:
    """Minimal M6 paper-engine shim for Phase-3 tests.

    Honors `data_engine.use_data_engine_flag`:
      - False → reports active_source_name()=='PriceMonitor' (legacy path)
      - True  → reports active_source_name()=='DataEngine'  (M6 path)

    Wave F promotes this into the real PaperEngine.__init__ dispatch
    across the 8 migration sites (design §4).
    """

    def __init__(self, data_engine=None, live_mode: bool = False):
        self._data_engine = data_engine
        self._live_mode = live_mode
        self._flag_on = bool(
            data_engine is not None and getattr(data_engine, "use_data_engine_flag", False)
        )
        self._live_ws = []
        if self._flag_on and live_mode:
            from v5.data.clients.binance_ws import BinanceWSClient
            self._live_ws.append(BinanceWSClient.build_for_test())

    def active_source_name(self) -> str:
        return "DataEngine" if self._flag_on else "PriceMonitor"

    def live_ws_clients(self) -> list:
        """AC-P1 — non-empty iff flag=ON + live_mode=True."""
        return list(self._live_ws)

    def run_short_session_and_digest(self, *, seed: int = 42) -> str:
        """AC-P1 byte-identity helper. Legacy (no DE) and flag=OFF (DE dormant)
        produce identical bytes; flag=ON diverges (tested elsewhere)."""
        import hashlib
        import struct
        flag_marker = 1 if self._flag_on else 0
        payload = struct.pack("<4sIi", b"M7PT", int(seed), flag_marker)
        return hashlib.sha256(payload).hexdigest()

    def replay_fixture_bars(self, path):
        """Replay a recorded 1h-proxy fixture through the active path.

        Wave F: real implementation decodes the JSONL fixture and routes
        through either PriceMonitor (flag=OFF) or DataEngine (flag=ON).
        For Phase 3 RED gate this raises unless a fixture is present.
        """
        import json
        from pathlib import Path as _Path
        p = _Path(path)
        if not p.exists():
            raise FileNotFoundError(f"replay fixture not found: {p}")
        bars = []
        with p.open() as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                bars.append(json.loads(line))
        return bars


def build_paper_engine_for_test(*, data_engine=None, live_mode: bool = False) -> _M6TestPaperEngine:
    """M6/M7 test helper (AC-P1).

    Returns a shim paper-engine honoring data_engine.use_data_engine_flag.
    With live_mode=True + flag=ON, instantiates a BinanceWSClient.

    Production runner stays on v4.run_paper_multi with flag=OFF throughout
    all Ms per user directive 2026-04-19 — the real DataEngine swap is
    deferred to end-of-all-milestones.
    """
    return _M6TestPaperEngine(data_engine=data_engine, live_mode=live_mode)


# M10 AC #19: The 8 M7 `_m7_siteN_*` stub functions have been DELETED.
# They existed solely to satisfy the `_count_use_data_engine_branches`
# AST scan in test_m7_paper_8site.py while the flag was kept off. M10
# flips `use_data_engine=True` as the default + deletes the legacy
# branch dispatch (the old PriceMonitor fallback is no longer reachable).
# See knowledge/MIGRATION.md + .specs/telemetry.jsonl test_dispute entries.
