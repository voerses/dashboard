"""V5 Paper Trading — State persistence.

Handles:
  - Serialize/deserialize SimulationState (including Position fields with numpy arrays, sets)
  - Atomic write (write to temp, fsync, rename)
  - Append-only writers: trades.jsonl, equity.csv
  - Position ID generation with tick_counter
  - M3 Task 11a: engine-level (engine, state_dir) save/load entry point with
    RollingCache sidecar .npz (numpy.savez_compressed) + crash-consistent
    save order: sidecar committed first, state.json pointer committed second.
"""
from __future__ import annotations

import collections
import csv
import datetime as _dt
import hashlib
import json
import logging
import os
import tempfile
from dataclasses import asdict
from typing import Optional

import numpy as np

from v5.position import Position, ClosedTrade, PositionManager, ScalingEvent
from v5.simulator import SimulationState

logger = logging.getLogger(__name__)


STATE_VERSION = 1

# M4/M5 schema version for the `write_paper_state` / `read_paper_state` API.
# `STATE_VERSION` is preserved at 1 so M3's version-fetching code keeps
# raising on mismatched legacy payloads (the deserialize_state path).
#
# M4 (v2): added the `pending_entries` payload shape so the reader could
#   apply the legacy `_armed_tokens` migration shim on older files (AC32).
# M5 (v3): renamed `pending_entries` → `open_orders` (design F4), added
#   M5 multi-leg Order fields (legs, fill_policy, contingency,
#   linked_order_id, time_in_force, venue_order_id) and Position identity
#   back-links (order_id, leg_ref_id). See `_migrate_v2_to_v3`.
STATE_SCHEMA_VERSION = 3


def make_position_id(token: str, strategy_id: str, tick_counter: int, leg: str) -> str:
    """Generate position ID: '{token}:{strategy_id}:{tick_counter}:{leg}'."""
    return f"{token}:{strategy_id}:{tick_counter}:{leg}"


# ---------------------------------------------------------------------------
# Position serialization helpers
# ---------------------------------------------------------------------------

def _json_safe(obj):
    """Recursively coerce non-JSON-native types (set, frozenset, numpy scalars)
    so that json.dumps() without default=str does not TypeError.

    Sets/frozensets → sorted lists (stable output). Numpy scalars → python.
    Dicts / lists / tuples recursed. Everything else passed through.
    """
    import numpy as _np
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (set, frozenset)):
        try:
            return sorted(_json_safe(v) for v in obj)
        except TypeError:
            # Unsortable mixed types — fall back to list
            return [_json_safe(v) for v in obj]
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, _np.generic):
        return obj.item()
    return obj


def _serialize_position(pos: Position) -> dict:
    """Serialize a Position to a JSON-compatible dict.

    Explicitly converts numpy scalar types to Python native types
    to avoid json.dumps TypeError on numpy float32/int32/etc.
    """
    d = {
        "position_id": pos.position_id,
        "token": pos.token,
        "strategy_id": pos.strategy_id,
        "leg": pos.leg,
        "entry_bar": int(pos.entry_bar),
        "entry_price": float(pos.entry_price),
        "direction": int(pos.direction),
        "quantity": float(pos.quantity),
        "margin_usd": float(pos.margin_usd),
        "leverage": float(pos.leverage),
        "is_perp": bool(pos.is_perp),
        "fee_rate": float(pos.fee_rate),
        "stop_mult": float(pos.stop_mult),
        "trail_mult": float(pos.trail_mult),
        "target_mult": float(pos.target_mult),
        "no_stop_bars": int(pos.no_stop_bars),
        "min_hold": int(pos.min_hold),
        "max_hold": int(pos.max_hold),
        "convex_exit": bool(pos.convex_exit),
        "rsi_exit_level": float(pos.rsi_exit_level),
        "trail_schedule": pos.trail_schedule.tolist() if pos.trail_schedule is not None else None,
        "time_trail_schedule": pos.time_trail_schedule.tolist() if pos.time_trail_schedule is not None else None,
        "max_trail_mult_arr": pos.max_trail_mult_arr.tolist() if pos.max_trail_mult_arr is not None else None,
        "funding_exit_threshold": float(pos.funding_exit_threshold),
        "breakeven_atr": float(pos.breakeven_atr),
        "breakeven_triggered": bool(pos.breakeven_triggered),
        "chandelier_lookback": int(pos.chandelier_lookback),
        "stop_price": float(pos.stop_price),
        "highest": float(pos.highest),
        "lowest": float(pos.lowest),
        "initial_risk": float(pos.initial_risk),
        "cumulative_funding": float(pos.cumulative_funding),
        "linked_position_id": pos.linked_position_id,
        "entry_timestamp": pos.entry_timestamp,
        "limit_price": float(pos.limit_price),
        "limit_placed_at": pos.limit_placed_at,
        "stop_limit_price": float(pos.stop_limit_price),
        "fill_source": pos.fill_source,
        # M2 scaling fields (Task 12, brief T13b)
        "scale_count": int(pos.scale_count),
        "scaling_events": [asdict(ev) for ev in pos.scaling_events],
        "r_anchor_price": float(pos.r_anchor_price),
        "_scale_action_bar": int(pos._scale_action_bar),
        # _helper_state: serialized as a fast-path cache (M2 Q3 revised 2026-04-18
        # per helper-state-dispute review). Helpers MUST still tolerate empty/
        # missing state on v4-compat load and rebuild from pos.scaling_events
        # (see v5/helpers.py::_rebuild_fired_from_events). Serialization is an
        # optimization, not a correctness requirement.
        #
        # Defensive coercion: helpers may store Python `set()` in the cache
        # (e.g. "tp_ladder_atr__fired_indices"). JSON can't encode sets, and
        # atomic_write_state's top-level json.dumps would TypeError on the
        # first rung fire (quant reviewer flagged). Coerce sets → sorted lists
        # here at the serialize boundary.
        "_helper_state": _json_safe(pos._helper_state),
        # M5 F9 — Position identity back-links (schema v3). Both default None
        # so a v2 Position (no back-links) roundtrips as `None`.
        "order_id": pos.order_id,
        "leg_ref_id": pos.leg_ref_id,
    }
    # Sub-hourly entry window_end for re-entry prevention across restarts
    window_end = getattr(pos, '_window_end', 0.0)
    if window_end > 0:
        d["_window_end"] = float(window_end)
    return d


def _deserialize_position(d: dict) -> Position:
    """Deserialize a dict to a Position."""
    trail_schedule = None
    if d.get("trail_schedule") is not None:
        trail_schedule = np.array(d["trail_schedule"], dtype=np.float64)

    time_trail_schedule = None
    if d.get("time_trail_schedule") is not None:
        time_trail_schedule = np.array(d["time_trail_schedule"], dtype=np.float64)

    max_trail_mult_arr = None
    if d.get("max_trail_mult_arr") is not None:
        max_trail_mult_arr = np.array(d["max_trail_mult_arr"], dtype=np.float64)

    pos = Position(
        position_id=d.get("position_id", ""),
        token=d.get("token", ""),
        strategy_id=d.get("strategy_id", ""),
        leg=d.get("leg", "primary"),
        entry_bar=d.get("entry_bar", 0),
        entry_price=d.get("entry_price", 0.0),
        direction=d.get("direction", 1),
        quantity=d.get("quantity", 0.0),
        margin_usd=d.get("margin_usd", 0.0),
        leverage=d.get("leverage", 1.0),
        is_perp=d.get("is_perp", False),
        fee_rate=d.get("fee_rate", 0.0),
        stop_mult=d.get("stop_mult", 0.0),
        trail_mult=d.get("trail_mult", 0.0),
        target_mult=d.get("target_mult", 0.0),
        no_stop_bars=d.get("no_stop_bars", 0),
        min_hold=d.get("min_hold", 0),
        max_hold=d.get("max_hold", 10 ** 9),
        convex_exit=d.get("convex_exit", False),
        rsi_exit_level=d.get("rsi_exit_level", 999.0),
        trail_schedule=trail_schedule,
        time_trail_schedule=time_trail_schedule,
        max_trail_mult_arr=max_trail_mult_arr,
        funding_exit_threshold=d.get("funding_exit_threshold", 0.0),
        breakeven_atr=d.get("breakeven_atr", 0.0),
        breakeven_triggered=d.get("breakeven_triggered", False),
        chandelier_lookback=d.get("chandelier_lookback", 0),
        stop_price=d.get("stop_price", 0.0),
        highest=d.get("highest", 0.0),
        lowest=d.get("lowest", 999999.0),
        initial_risk=d.get("initial_risk", 0.0),
        cumulative_funding=d.get("cumulative_funding", 0.0),
        linked_position_id=d.get("linked_position_id"),
        entry_timestamp=d.get("entry_timestamp", ""),
        limit_price=d.get("limit_price", 0.0),
        limit_placed_at=d.get("limit_placed_at", ""),
        stop_limit_price=d.get("stop_limit_price", 0.0),
        fill_source=d.get("fill_source", ""),
    )
    # Restore sub-hourly window_end if present
    wend = d.get("_window_end", 0.0)
    if wend > 0:
        pos._window_end = wend

    # M2 scaling fields (Task 12) — backward compatible defaults
    pos.scale_count = int(d.get("scale_count", 0))
    # r_anchor_price: if 0.0 or missing, Position.__post_init__ already set it to
    # entry_price. For explicit-zero legacy states, fall back to entry_price.
    r_anchor = d.get("r_anchor_price", 0.0)
    if r_anchor and r_anchor > 0.0:
        pos.r_anchor_price = float(r_anchor)
    # else: leave as entry_price from __post_init__
    pos._scale_action_bar = int(d.get("_scale_action_bar", -1))

    scaling_events_data = d.get("scaling_events", []) or []
    pos.scaling_events = [ScalingEvent(**ev) for ev in scaling_events_data]
    helper_state = d.get("_helper_state", None)
    if isinstance(helper_state, dict):
        pos._helper_state = dict(helper_state)
    # else: Position.__init__ already set _helper_state = {} (default factory)
    # M5 F9 — restore identity back-links (schema v3). Defaults to None for
    # v2 payloads that never carried these fields.
    pos.order_id = d.get("order_id", None)
    pos.leg_ref_id = d.get("leg_ref_id", None)
    return pos


# ---------------------------------------------------------------------------
# State serialization
# ---------------------------------------------------------------------------

def _write_rolling_cache_sidecar(registry, state_dir: str) -> tuple[str, str, dict]:
    """Write a RollingCache sidecar `.npz` file via tmp + fsync + rename.

    Builds an arrays dict keyed `{token}__{bar_type.value}__{field}` plus
    `{token}__{bar_type.value}__timestamps` and dumps all caches into a
    single compressed npz. Returns `(relative_path, sha256_hex, metadata)`.

    `metadata` is `{key_prefix: {head, size, maxlen, seeded_through_ts_ns}}`
    and is embedded in the state.json by the caller.

    Task 11a: HAPPY PATH ONLY. Caller writes sidecar BEFORE state.json so
    that a crash between steps leaves an orphan .npz (harmless — reclaimed
    on next save). Task 11b handles sha256-mismatch / missing-sidecar
    recovery.
    """
    arrays: dict[str, np.ndarray] = {}
    metadata: dict[str, dict] = {}
    for cache in registry.all():
        # AC20 M4: sidecar key uses BarSpec.label (e.g. "1h") — same on-disk
        # string as the pre-M4 BarType(Enum).value, so sidecar format is
        # bit-identical after the migration-alias swap.
        bar_label = cache.bar_spec.label
        key_prefix = f"{cache.token}__{bar_label}"
        # Each field becomes its own array in the .npz
        for fname, buf in cache._buf.items():
            arrays[f"{key_prefix}__{fname}"] = buf
        arrays[f"{key_prefix}__timestamps"] = cache._timestamps
        metadata[key_prefix] = {
            "token": cache.token,
            "bar_type": bar_label,
            "head": int(cache._head),
            "size": int(cache._size),
            "maxlen": int(cache.maxlen),
            "seeded_through_ts_ns": int(cache.seeded_through_ts_ns),
        }

    # numpy.savez_compressed appends `.npz` automatically if the target
    # path doesn't end in `.npz` — use a `.tmp` extension that it WILL
    # preserve verbatim to control the on-disk filename precisely.
    tmp_path = os.path.join(state_dir, "rolling_cache.npz.tmp")
    final_path = os.path.join(state_dir, "rolling_cache.npz")
    # 1. np.savez_compressed writes the archive to the tmp path. Pass a
    #    file handle to skip numpy's auto-.npz suffix logic.
    with open(tmp_path, "wb") as f:
        np.savez_compressed(f, **arrays)
        f.flush()
        os.fsync(f.fileno())
    # 2. Read back for sha256 + size
    with open(tmp_path, "rb") as f:
        data = f.read()
    sha = hashlib.sha256(data).hexdigest()
    # 3. Atomic rename — sidecar is now committed to `rolling_cache.npz`
    os.rename(tmp_path, final_path)
    return ("rolling_cache.npz", sha, metadata)


def _serialize_filled_windows_generic(obj) -> list:
    """Serialize a `_TTLOrderedDict`-like container (or any mapping-like
    object supporting `.items()`) to a JSON-compatible list.

    Each item is `[list(key), inserted_ts_epoch]`. Tuples become lists so
    they survive JSON roundtrip; deserialize reconstructs them as tuples.
    """
    items = []
    if obj is None:
        return items
    if hasattr(obj, "items"):
        for k, v in obj.items():
            if isinstance(k, tuple):
                key_payload = list(k)
            else:
                key_payload = k
            items.append([key_payload, float(v) if v is not None else 0.0])
    else:
        for k in obj:
            if isinstance(k, tuple):
                items.append([list(k), 0.0])
            else:
                items.append([k, 0.0])
    return items


def _deserialize_filled_windows_generic(data: list, obj):
    """Restore into an existing `_TTLOrderedDict`-like container.

    Reconstructs tuple keys (list→tuple) and calls `.add(key, ts)` if the
    container supports it, else falls back to dict-style `__setitem__`.
    """
    if data is None or obj is None:
        return
    for entry in data:
        if not entry:
            continue
        raw_key = entry[0]
        ts = entry[1] if len(entry) > 1 else 0.0
        if isinstance(raw_key, list):
            key = tuple(raw_key)
        else:
            key = raw_key
        if hasattr(obj, "add"):
            try:
                obj.add(key, ts)
            except Exception:  # pragma: no cover - defensive
                try:
                    obj[key] = None
                except Exception:
                    continue
        else:
            try:
                obj[key] = None
            except Exception:  # pragma: no cover - defensive
                continue


def _audit_rolling_cache_fallback(state_dir: str, reason: str) -> None:
    """Task 11b: append a dispute-style audit event for a cold-start fallback.

    Writes to `.specs/telemetry.jsonl` (project audit log). Best-effort —
    any I/O failure is swallowed silently so that state recovery itself is
    never blocked by audit-logging problems.
    """
    try:
        ts_iso = _dt.datetime.now(_dt.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        event = {
            "event": "rolling_cache_fallback",
            "state_dir": str(state_dir),
            "reason": reason,
            "timestamp": ts_iso,
        }
        # Walk upward from state_dir to find a project root with .specs/.
        # Fall back to writing next to state_dir if no .specs is found.
        candidate = os.path.abspath(state_dir)
        telemetry_path: Optional[str] = None
        for _ in range(10):
            specs_dir = os.path.join(candidate, ".specs")
            if os.path.isdir(specs_dir):
                telemetry_path = os.path.join(specs_dir, "telemetry.jsonl")
                break
            parent = os.path.dirname(candidate)
            if parent == candidate:
                break
            candidate = parent
        if telemetry_path is None:
            telemetry_path = os.path.join(state_dir, "audit.jsonl")
            try:
                os.makedirs(state_dir, exist_ok=True)
            except OSError:
                return
        with open(telemetry_path, "a") as f:
            f.write(json.dumps(event) + "\n")
    except Exception:  # pragma: no cover - defensive
        pass


def _cold_start_rolling_cache(engine, *, reason: str, state_dir: str,
                              emit_warning: bool) -> None:
    """Task 11b: invoke the same cold-start seed path that fresh engine
    init uses. Mirrors the engine's `_seed_rolling_caches(config)` contract.

    If the engine exposes `_seed_rolling_caches`, delegate to it so we share
    exactly one code path with first-boot init (most important for parity).
    Otherwise fall back to a direct `registry.seed_all(...)` call.
    """
    if emit_warning:
        logger.warning(
            "rolling_cache.npz sha256 mismatch OR missing; falling back to "
            "cold-start seed (state_dir=%s, reason=%s)", state_dir, reason,
        )

    registry = getattr(engine, "_rolling_cache_registry", None)
    if registry is None:
        return

    # Preferred path: re-run the engine's own cold-start so new-engine and
    # recovered-engine behave identically.
    seed_fn = getattr(engine, "_seed_rolling_caches", None)
    config = getattr(engine, "config", None)
    if callable(seed_fn) and config is not None:
        try:
            seed_fn(config)
            return
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "cold-start _seed_rolling_caches failed: %s "
                "(state_dir=%s); registry left empty", exc, state_dir,
            )
            return

    # Fallback path: directly invoke seed_all without a load_fn (subscribe
    # the needed caches; signal compute will populate them lazily).
    try:
        registry.seed_all(
            strategy_specs=getattr(config, "strategies", []) if config else [],
            data_dir=getattr(config, "data_dir", "") if config else "",
            max_lookback_bars=int(
                getattr(engine, "_max_lookback_bars", 4800),
            ),
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(
            "cold-start registry.seed_all failed: %s (state_dir=%s); "
            "registry left empty", exc, state_dir,
        )


def _restore_filled_4h_windows(engine, payload) -> None:
    """Task 11c: restore `engine._filled_4h_windows`.

    Accepts two formats:
      1. Structured dict: `{"maxlen": N, "ttl_seconds": T, "items": [[k, ts], ...]}`
         — rebuild a `_TTLOrderedDict` with the saved bounds and original
         insertion timestamps (TTL eviction continues from the right offset).
      2. Legacy flat list: `[[k, ts], ...]` — fall back to adding into the
         existing container at current wall-clock time.

    If `_TTLOrderedDict` is importable, format (1) yields a freshly
    constructed container that replaces `engine._filled_4h_windows`.
    Otherwise, we fall through to the generic `.add`/`__setitem__` restore.
    """
    if payload is None:
        return

    # Format (1): structured dict
    if isinstance(payload, dict) and "items" in payload:
        try:
            from v5.paper_engine import _TTLOrderedDict as _TTLOrderedDictCls
        except Exception:
            _TTLOrderedDictCls = None
        items_raw = payload.get("items", []) or []
        restored: list = []
        for entry in items_raw:
            if not entry:
                continue
            raw_key = entry[0]
            ts = entry[1] if len(entry) > 1 else 0.0
            if isinstance(raw_key, list):
                key = tuple(raw_key)
            else:
                key = raw_key
            try:
                ts_f = float(ts) if ts is not None else 0.0
            except (TypeError, ValueError):
                ts_f = 0.0
            restored.append((key, ts_f))
        if _TTLOrderedDictCls is not None:
            try:
                ttl = _TTLOrderedDictCls(
                    ttl_seconds=float(payload.get("ttl_seconds", 7 * 86400.0)),
                    maxlen=int(payload.get("maxlen", 500)),
                )
                ttl.restore(restored)
                engine._filled_4h_windows = ttl
                return
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning(
                    "restore _TTLOrderedDict failed: %s; falling back to "
                    "generic restore", exc,
                )
        # Generic fallback: mutate existing container
        _deserialize_filled_windows_generic(
            [[list(k) if isinstance(k, tuple) else k, ts] for k, ts in restored],
            engine._filled_4h_windows,
        )
        return

    # Format (2): legacy flat list
    _deserialize_filled_windows_generic(payload, engine._filled_4h_windows)


def _serialize_engine_state_to_dir(engine, state_dir: str) -> None:
    """M3 Task 11a — engine-level state save with RollingCache sidecar.

    Save order (crash-consistent):
      1. If engine has `_rolling_cache_registry` with caches, write
         `rolling_cache.npz` via tmp + fsync + atomic rename.
      2. Build state JSON (includes `rolling_cache_path`,
         `rolling_cache_sha256`, `rolling_cache_metadata` when a sidecar
         was written, plus bounded structures: `_alerts`, `_pending_alerts`,
         `_filled_4h_windows`).
      3. Write `state.json.tmp` → fsync → atomic rename to `state.json`.

    A crash between (1) and (3) leaves an orphan `.npz` (harmless, reclaimed
    on next save). A crash mid-(3) cannot tear the state.json thanks to the
    atomic rename.
    """
    os.makedirs(state_dir, exist_ok=True)

    data: dict = {
        "version": STATE_VERSION,
        "tick_counter": int(getattr(engine, "tick_counter", 0)),
    }

    # --- 1. Sidecar first -------------------------------------------------
    registry = getattr(engine, "_rolling_cache_registry", None)
    if registry is not None:
        try:
            has_caches = len(registry.all()) > 0
        except Exception:
            has_caches = False
        if has_caches:
            rel_path, sha, metadata = _write_rolling_cache_sidecar(
                registry, state_dir
            )
            data["rolling_cache_path"] = rel_path
            data["rolling_cache_sha256"] = sha
            data["rolling_cache_metadata"] = metadata

    # --- 2. Bounded structures (AC14) ------------------------------------
    alerts_obj = getattr(engine, "_alerts", None)
    if alerts_obj is not None:
        try:
            data["_alerts"] = list(alerts_obj)
        except Exception:
            data["_alerts"] = []
        maxlen = getattr(alerts_obj, "maxlen", None)
        if maxlen is not None:
            data["_alerts_maxlen"] = int(maxlen)

    pending_obj = getattr(engine, "_pending_alerts", None)
    if pending_obj is not None:
        try:
            data["_pending_alerts"] = list(pending_obj)
        except Exception:
            data["_pending_alerts"] = []
        maxlen = getattr(pending_obj, "maxlen", None)
        if maxlen is not None:
            data["_pending_alerts_maxlen"] = int(maxlen)

    filled = getattr(engine, "_filled_4h_windows", None)
    if filled is not None:
        # Task 11c: prefer structured format for `_TTLOrderedDict` so
        # maxlen/ttl_seconds survive the roundtrip. Items are `[key, ts]`
        # where key-tuples are cast to lists for JSON and restored on load.
        try:
            from v5.paper_engine import _TTLOrderedDict as _TTLOrderedDictCls
        except Exception:
            _TTLOrderedDictCls = None
        if _TTLOrderedDictCls is not None and isinstance(
            filled, _TTLOrderedDictCls
        ):
            data["_filled_4h_windows"] = {
                "maxlen": int(filled._maxlen),
                "ttl_seconds": float(filled._ttl),
                "items": [
                    [list(k) if isinstance(k, tuple) else k,
                     float(ts) if ts is not None else 0.0]
                    for k, ts in filled.items()
                ],
            }
        else:
            data["_filled_4h_windows"] = _serialize_filled_windows_generic(
                filled,
            )

    # --- 3. Atomic state.json write ---------------------------------------
    final_path = os.path.join(state_dir, "state.json")
    tmp_path = os.path.join(state_dir, "state.json.tmp")
    payload = json.dumps(data, default=str)
    with open(tmp_path, "w") as f:
        f.write(payload)
        f.flush()
        os.fsync(f.fileno())
    os.rename(tmp_path, final_path)


def _deserialize_engine_state_from_dir(engine, state_dir: str) -> None:
    """M3 Task 11a — engine-level state load with RollingCache sidecar.

    HAPPY PATH ONLY (Task 11a). Task 11b adds sha256-mismatch / missing
    sidecar → cold-start fallback.

    - If `rolling_cache_path` is in the JSON AND the file exists:
        1. Load `.npz` via `np.load`.
        2. Verify sha256 matches — raises `ValueError` on mismatch (Task
           11b will downgrade this to a WARNING + cold-start fallback).
        3. Reconstruct each RollingCache from the arrays + metadata and
           register it into `engine._rolling_cache_registry`.
    - If `rolling_cache_path` NOT in JSON (pre-M3 format), leave registry
      alone — caller falls back to cold-start seed (Task 9b / 11b).
    """
    state_path = os.path.join(state_dir, "state.json")
    if not os.path.exists(state_path):
        return
    with open(state_path) as f:
        data = json.load(f)

    # --- Bounded structures ----------------------------------------------
    if "_alerts" in data and hasattr(engine, "_alerts"):
        maxlen = data.get("_alerts_maxlen",
                          getattr(engine._alerts, "maxlen", None))
        engine._alerts = collections.deque(data["_alerts"], maxlen=maxlen)

    if "_pending_alerts" in data and hasattr(engine, "_pending_alerts"):
        maxlen = data.get("_pending_alerts_maxlen",
                          getattr(engine._pending_alerts, "maxlen", None))
        engine._pending_alerts = collections.deque(
            data["_pending_alerts"], maxlen=maxlen,
        )

    if "_filled_4h_windows" in data and hasattr(engine, "_filled_4h_windows"):
        _restore_filled_4h_windows(engine, data["_filled_4h_windows"])

    engine.tick_counter = int(data.get("tick_counter", 0))

    # --- Sidecar with Task 11b cold-start fallback ------------------------
    rel_path = data.get("rolling_cache_path")
    if not rel_path:
        # Old-format state: no `rolling_cache_path` key at all. Legitimate
        # (pre-Task 11a) — silently cold-start without WARNING.
        _cold_start_rolling_cache(engine, reason="old_format_no_sidecar_key",
                                  state_dir=state_dir, emit_warning=False)
        return

    sidecar_path = os.path.join(state_dir, rel_path)
    if not os.path.exists(sidecar_path):
        logger.warning(
            "rolling_cache.npz sha256 mismatch OR missing; falling back to "
            "cold-start seed (state_dir=%s)", state_dir,
        )
        _audit_rolling_cache_fallback(state_dir, reason="missing")
        _cold_start_rolling_cache(engine, reason="missing",
                                  state_dir=state_dir, emit_warning=False)
        return

    expected_sha = data.get("rolling_cache_sha256", "")
    with open(sidecar_path, "rb") as f:
        actual_sha = hashlib.sha256(f.read()).hexdigest()
    if expected_sha and actual_sha != expected_sha:
        logger.warning(
            "rolling_cache.npz sha256 mismatch OR missing; falling back to "
            "cold-start seed (state_dir=%s)", state_dir,
        )
        _audit_rolling_cache_fallback(state_dir, reason="sha256_mismatch")
        _cold_start_rolling_cache(engine, reason="sha256_mismatch",
                                  state_dir=state_dir, emit_warning=False)
        return

    metadata = data.get("rolling_cache_metadata", {}) or {}
    registry = getattr(engine, "_rolling_cache_registry", None)
    if registry is None:
        return

    from v5.rolling_cache import BarType  # noqa: F401 — compat alias namespace
    from v5.bar_spec import BarSpec

    # Mapping from the legacy "1m"/"1h"/"1d" on-disk label back to BarSpec.
    _LABEL_TO_BARSPEC = {
        "1m": BarSpec.from_minutes(1),
        "1h": BarSpec.from_minutes(60),
        "1d": BarSpec.from_minutes(1440),
    }

    try:
        npz = np.load(sidecar_path)
    except Exception as exc:
        logger.warning(
            "rolling_cache.npz load failed (%s); falling back to cold-start "
            "seed (state_dir=%s)", exc, state_dir,
        )
        _audit_rolling_cache_fallback(state_dir, reason="load_error")
        _cold_start_rolling_cache(engine, reason="load_error",
                                  state_dir=state_dir, emit_warning=False)
        return
    try:
        for key_prefix, meta in metadata.items():
            token = meta.get("token") or key_prefix.split("__")[0]
            bt_val = meta.get("bar_type") or key_prefix.split("__")[1]
            bar_type = _LABEL_TO_BARSPEC[bt_val]
            cache = registry.subscribe(token, bar_type)

            # Buffers — each field had a dedicated array
            for fname in cache._buf.keys():
                arr_key = f"{key_prefix}__{fname}"
                if arr_key in npz.files:
                    buf = npz[arr_key]
                    if buf.shape == cache._buf[fname].shape:
                        cache._buf[fname][:] = buf
            ts_key = f"{key_prefix}__timestamps"
            if ts_key in npz.files:
                ts_buf = npz[ts_key]
                if ts_buf.shape == cache._timestamps.shape:
                    cache._timestamps[:] = ts_buf

            cache._head = int(meta.get("head", 0))
            cache._size = int(meta.get("size", 0))
            cache.seeded_through_ts_ns = int(
                meta.get("seeded_through_ts_ns", 0)
            )
    finally:
        npz.close()


def serialize_state(
    state=None,
    tick_counter=None,
    last_timestamp: Optional[str] = None,
    shadow_pools: Optional[dict] = None,
    last_known_prices: Optional[dict] = None,
    armed_tokens: Optional[list] = None,
    filled_4h_windows: Optional[list] = None,
    timestamp: Optional[str] = None,
):
    """Serialize SimulationState to a JSON-compatible dict.

    Closed trades are NOT included — they go to trades.jsonl.
    `timestamp=` is accepted as an alias for `last_timestamp` (M2 tests use this
    shorter name; legacy positional callers still pass `last_timestamp`).

    M3 Task 11a overload: when called as `serialize_state(engine, state_dir)`
    where `engine` is a PaperPortfolioEngine (has `_rolling_cache_registry`
    or is not a SimulationState) and `state_dir` is a string path, this
    delegates to the engine-level save with sidecar .npz. Returns `None` in
    that mode (side-effectful disk write, not a dict).
    """
    # M3 Task 11a overload detection — first arg is a PaperPortfolioEngine
    # (exposes `_rolling_cache_registry` or lacks `position_manager`) and
    # second arg is a string path.
    if isinstance(tick_counter, str) and not isinstance(state, SimulationState):
        _serialize_engine_state_to_dir(state, tick_counter)
        return None

    if last_timestamp is None:
        last_timestamp = timestamp if timestamp is not None else ""
    positions = [_serialize_position(p) for p in state.position_manager.open_positions]

    data = {
        "version": STATE_VERSION,
        "tick_counter": int(tick_counter),
        "last_timestamp": last_timestamp,
        "initial_capital": float(state.initial_capital),
        "realized_pnl": float(state.realized_pnl),
        "total_fees": float(state.total_fees),
        "total_funding": float(state.total_funding),
        "open_positions": positions,
        "entry_fees_by_pos": {k: float(v) for k, v in state._entry_fees_by_pos.items()},
        "last_known_atrs": {k: float(v) for k, v in state.last_known_atrs.items()},
    }

    if shadow_pools is not None:
        data["shadow_pools"] = {k: float(v) for k, v in shadow_pools.items()}

    if last_known_prices is not None:
        data["last_known_prices"] = {k: float(v) for k, v in last_known_prices.items()}


    if armed_tokens is not None:
        data["armed_tokens"] = armed_tokens

    if filled_4h_windows is not None:
        data["filled_4h_windows"] = filled_4h_windows

    return data


def deserialize_state(data=None, return_shadow: bool = False, *,
                      state_dir: Optional[str] = None):
    """Deserialize a state dict back to SimulationState + tick_counter.

    Returns (state, tick_counter) or (state, tick_counter, shadow_pools) if return_shadow=True.
    last_timestamp is stored as state.last_timestamp for callers that need it.
    Closed trades list is always empty (they live in trades.jsonl).

    M3 Task 11a overload: when called as `deserialize_state(engine, state_dir)`
    where `engine` is a PaperPortfolioEngine and `return_shadow` is a string
    path (positional), this delegates to the engine-level load with sidecar
    .npz. Returns `None` in that mode (side-effectful in-place engine state
    restore).
    """
    # M3 Task 11a overload detection — (engine, state_dir) form.
    if isinstance(return_shadow, str):
        _deserialize_engine_state_from_dir(data, return_shadow)
        return None
    if state_dir is not None and not isinstance(data, dict):
        _deserialize_engine_state_from_dir(data, state_dir)
        return None

    # R3-I3 fix: validate state version
    version = data.get("version", 1)
    if version != STATE_VERSION:
        raise ValueError(
            f"State version mismatch: file has v{version}, "
            f"engine expects v{STATE_VERSION}. Manual migration required."
        )
    state = SimulationState(initial_capital=data["initial_capital"])
    state.realized_pnl = data["realized_pnl"]
    state.total_fees = data["total_fees"]
    state.total_funding = data["total_funding"]
    state.last_timestamp = data.get("last_timestamp", None)

    for pos_data in data.get("open_positions", []):
        pos = _deserialize_position(pos_data)
        state.position_manager.open_position(pos)

    state._entry_fees_by_pos = dict(data.get("entry_fees_by_pos", {}))
    state.last_known_atrs = dict(data.get("last_known_atrs", {}))
    state.last_known_prices = data.get("last_known_prices", {})

    tick_counter = data["tick_counter"]

    if return_shadow:
        shadow_pools = data.get("shadow_pools", {
            "spot_funds": 0.0,
            "perp_funds": 0.0,
            "spot_deployed": 0.0,
            "perp_deployed": 0.0,
        })
        return state, tick_counter, shadow_pools

    return state, tick_counter


# ---------------------------------------------------------------------------
# Atomic file write
# ---------------------------------------------------------------------------

def atomic_write_state(
    state: SimulationState,
    tick_counter: int,
    last_timestamp: str,
    target_path: str,
    shadow_pools: Optional[dict] = None,
    last_known_prices: Optional[dict] = None,
    armed_tokens: Optional[list] = None,
    filled_4h_windows: Optional[list] = None,
) -> None:
    """Write state to target_path atomically (write to temp, fsync, rename).

    If rename fails, the original file is preserved.
    """
    data = serialize_state(state, tick_counter, last_timestamp, shadow_pools, last_known_prices, armed_tokens=armed_tokens, filled_4h_windows=filled_4h_windows)
    # default=str as a safety net — _json_safe() handles most known cases but a
    # stray numpy/set value slipping through would otherwise crash the save.
    json_str = json.dumps(data, indent=2, default=str)

    target_dir = os.path.dirname(target_path) or "."

    # Write to temp file in same directory (same filesystem for atomic rename)
    fd, tmp_path = tempfile.mkstemp(dir=target_dir, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(json_str)
            f.flush()
            os.fsync(f.fileno())
        os.rename(tmp_path, target_path)
    except Exception:
        # Clean up temp file if rename failed
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Append-only writers
# ---------------------------------------------------------------------------

def _closed_trade_to_dict(trade: ClosedTrade, tick: Optional[int] = None) -> dict:
    """Convert a ClosedTrade to a JSON-serializable dict.

    Args:
        trade: The closed trade to convert.
        tick: Optional tick counter value for recovery truncation (AC16).
    """
    d = {
        "position_id": trade.position_id,
        "token": trade.token,
        "strategy_id": trade.strategy_id,
        "leg": trade.leg,
        "entry_bar": int(trade.entry_bar),
        "exit_bar": int(trade.exit_bar),
        "entry_price": float(trade.entry_price),
        "exit_price": float(trade.exit_price),
        "direction": int(trade.direction),
        "margin_usd": float(trade.margin_usd),
        "pnl": float(trade.pnl),
        "funding_cost": float(trade.funding_cost),
        "entry_fee": float(trade.entry_fee),
        "exit_fee": float(trade.exit_fee),
        "hold_bars": int(trade.hold_bars),
        "exit_reason": trade.exit_reason,
        "is_perp": bool(trade.is_perp),
        "entry_timestamp": trade.entry_timestamp,
        "exit_timestamp": trade.exit_timestamp,
        "limit_price": float(trade.limit_price),
        "limit_placed_at": trade.limit_placed_at,
        "stop_limit_price": float(trade.stop_limit_price),
        "fill_source": trade.fill_source,
    }
    if tick is not None:
        d["tick"] = tick
    # Include window_end for 4H re-entry prevention on crash recovery
    window_end = getattr(trade, '_window_end', 0.0)
    if window_end > 0:
        d["window_end"] = float(window_end)
    return d


def append_trades(trades: list[ClosedTrade], path: str, tick: Optional[int] = None) -> None:
    """Append closed trades as JSON lines to trades.jsonl."""
    with open(path, "a") as f:
        for trade in trades:
            f.write(json.dumps(_closed_trade_to_dict(trade, tick=tick)) + "\n")


_EQUITY_COLUMNS = [
    "timestamp", "tick", "portfolio_equity", "mark_to_market_equity",
    "free_capital", "open_positions",
    "spot_shadow_free", "perp_shadow_free",
    "spot_deployed", "perp_deployed", "imbalance_pct",
]


def append_equity(
    path: str,
    timestamp: str,
    tick: int,
    portfolio_equity: float,
    mark_to_market_equity: float,
    free_capital: float,
    open_positions: int,
    spot_shadow_free: float = 0.0,
    perp_shadow_free: float = 0.0,
    spot_deployed: float = 0.0,
    perp_deployed: float = 0.0,
    imbalance_pct: float = 0.0,
) -> None:
    """Append one equity snapshot row to equity.csv.

    Creates the file with header if it doesn't exist.
    Includes shadow pool columns per AC25.
    """
    write_header = not os.path.exists(path)

    with open(path, "a", newline="") as f:
        writer = csv.writer(f)
        if write_header:
            writer.writerow(_EQUITY_COLUMNS)
        writer.writerow([
            timestamp, tick,
            f"{portfolio_equity:.2f}",
            f"{mark_to_market_equity:.2f}",
            f"{free_capital:.2f}",
            open_positions,
            f"{spot_shadow_free:.2f}",
            f"{perp_shadow_free:.2f}",
            f"{spot_deployed:.2f}",
            f"{perp_deployed:.2f}",
            f"{imbalance_pct:.2f}",
        ])
        # R6-I3 fix: fsync equity.csv for power-failure safety
        f.flush()
        os.fsync(f.fileno())


# ---------------------------------------------------------------------------
# Rebalance log (AC10)
# ---------------------------------------------------------------------------

def append_rebalances(records: list[dict], path: str) -> None:
    """Append rebalance records as JSON lines to a JSONL file."""
    with open(path, "a") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")


# ---------------------------------------------------------------------------
# JSONL reader with malformed-line tolerance
# ---------------------------------------------------------------------------

def read_trades_jsonl(path: str) -> list[dict]:
    """Read trades from a JSONL file, skipping malformed lines.

    Returns list of parsed dicts. Lines that fail JSON parsing are silently
    skipped (crash recovery safety — partial writes leave truncated lines).
    """
    trades = []
    if not os.path.exists(path):
        return trades
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                trades.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return trades


# ---------------------------------------------------------------------------
# Recovery truncation (AC16)
# ---------------------------------------------------------------------------

def truncate_after_tick(path: str, max_tick: int, format: str = "jsonl") -> None:
    """Remove entries with tick > max_tick from a file.

    Args:
        path: Path to the file (trades.jsonl or equity.csv).
        max_tick: Keep only entries with tick <= max_tick.
        format: "jsonl" for trades.jsonl, "csv" for equity.csv.
    """
    if not os.path.exists(path):
        return

    if format == "jsonl":
        kept = []
        with open(path) as f:
            for line in f:
                line_s = line.strip()
                if not line_s:
                    continue
                try:
                    rec = json.loads(line_s)
                except json.JSONDecodeError:
                    continue
                if rec.get("tick", 0) <= max_tick:
                    kept.append(line_s)
        with open(path, "w") as f:
            for line_s in kept:
                f.write(line_s + "\n")

    elif format == "csv":
        import pandas as pd
        try:
            df = pd.read_csv(path)
        except (pd.errors.ParserError, pd.errors.EmptyDataError):
            # R6-I2 fix: handle malformed CSV from crash mid-write.
            # Fall back to line-by-line reading, skipping bad lines.
            import io
            good_lines = []
            with open(path) as f:
                for i, line in enumerate(f):
                    if i == 0:
                        good_lines.append(line)  # header
                        continue
                    # Validate line has expected number of fields
                    if line.strip() and line.count(",") >= 2:
                        good_lines.append(line)
            df = pd.read_csv(io.StringIO("".join(good_lines)))
        df = df[df["tick"] <= max_tick]
        df.to_csv(path, index=False)


# ---------------------------------------------------------------------------
# Independent mode engine state (AC12)
# ---------------------------------------------------------------------------

def serialize_engine_state(
    strategy_states: dict[str, SimulationState],
    tick_counter: int,
    last_timestamp: str,
    mode: str = "independent",
    shadow_pools: Optional[dict] = None,
    last_known_prices: Optional[dict] = None,
    armed_tokens: Optional[list] = None,
    filled_4h_windows: Optional[list] = None,
) -> dict:
    """Serialize multiple strategy states for independent mode.

    tick_counter is stored at the top level (not per-strategy).
    Each strategy state is serialized without tick_counter.
    """
    data = {
        "version": STATE_VERSION,
        "mode": mode,
        "tick_counter": int(tick_counter),
        "last_timestamp": last_timestamp,
        "strategy_states": {},
    }

    for sid, state in strategy_states.items():
        positions = [_serialize_position(p) for p in state.position_manager.open_positions]
        sdata = {
            "initial_capital": float(state.initial_capital),
            "realized_pnl": float(state.realized_pnl),
            "total_fees": float(state.total_fees),
            "total_funding": float(state.total_funding),
            "open_positions": positions,
            "entry_fees_by_pos": {k: float(v) for k, v in state._entry_fees_by_pos.items()},
            "last_known_atrs": {k: float(v) for k, v in state.last_known_atrs.items()},
        }
        data["strategy_states"][sid] = sdata

    if shadow_pools is not None:
        data["shadow_pools"] = {k: float(v) for k, v in shadow_pools.items()}

    if last_known_prices is not None:
        data["last_known_prices"] = {k: float(v) for k, v in last_known_prices.items()}


    if armed_tokens is not None:
        data["armed_tokens"] = armed_tokens

    if filled_4h_windows is not None:
        data["filled_4h_windows"] = filled_4h_windows

    return data


def deserialize_engine_state(data: dict) -> tuple:
    """Deserialize independent mode engine state.

    Returns (strategy_states_dict, tick_counter, last_timestamp)
    where strategy_states_dict maps strategy_id -> SimulationState.
    """
    # R3-I3 fix: validate state version
    version = data.get("version", 1)
    if version != STATE_VERSION:
        raise ValueError(
            f"State version mismatch: file has v{version}, "
            f"engine expects v{STATE_VERSION}. Manual migration required."
        )
    tick_counter = data["tick_counter"]
    last_timestamp = data.get("last_timestamp", None)

    strategy_states = {}
    for sid, sdata in data.get("strategy_states", {}).items():
        state = SimulationState(initial_capital=sdata["initial_capital"])
        state.realized_pnl = sdata["realized_pnl"]
        state.total_fees = sdata["total_fees"]
        state.total_funding = sdata["total_funding"]

        for pos_data in sdata.get("open_positions", []):
            pos = _deserialize_position(pos_data)
            state.position_manager.open_position(pos)

        state._entry_fees_by_pos = dict(sdata.get("entry_fees_by_pos", {}))
        state.last_known_atrs = dict(sdata.get("last_known_atrs", {}))
        strategy_states[sid] = state

    return strategy_states, tick_counter, last_timestamp


# ---------------------------------------------------------------------------
# Legacy-log parsers (v4 back-compat for /dashboard analysis JSON)
# (AC31a, AC33, AC34 — Tasks 12 + 14)
# M10 B13: renamed banner from "v4 compatibility loaders" to clarify these
# parsers read v4 JSON analysis logs for back-compat dashboard display —
# they are legitimate load paths, not deletable compat shims.
# ---------------------------------------------------------------------------

def load_v4_compat(state_path: str) -> tuple:
    """AC31a: load a v4-format paper_state JSON and synthesize v5 defaults.

    Returns (state, tick_counter). v4 positions are deserialized via the standard
    path — new M2 fields (`scale_count`, `scaling_events`, `r_anchor_price`,
    `_scale_action_bar`) are absent in v4 so they pick up defaults:
      - scale_count = 0
      - scaling_events = []
      - r_anchor_price = entry_price (set by Position.__post_init__)
      - _scale_action_bar = -1
    """
    with open(state_path) as f:
        data = json.load(f)
    return deserialize_state(data)


def load_trade_log(path: str) -> list[ClosedTrade]:
    """AC33/AC34: load v4 analysis/*.json trade logs; synthesize M2 identity fields.

    v4 uses a ':partial' suffix on position_id for partial-TP events. Strip it
    to derive parent_position_id. Synthesize identity fields per record:
      - parent_position_id: position_id with trailing ':partial' stripped
      - exec_seq: 1 if ':partial' suffix present else 0
      - exec_type: 'reduce' if ':partial' else 'exit'
      - is_terminal: False if ':partial' else True
      - has_scaling: True if ANY record in the log has ':partial' suffix
      - scaling_events: [] (v4 logs do not carry these)
      - triggered_by: ''

    Raises ValueError if the log contains v5-format ':scale_N' or ':scale_N_final'
    suffixes WITHOUT corresponding identity fields (AC33 fails loud on mixed-format
    logs to prevent silent misinterpretation).
    """
    with open(path) as f:
        records = json.load(f)

    if not isinstance(records, list):
        raise ValueError(
            f"Trade log {path} must be a JSON list; got {type(records).__name__}"
        )

    # First pass: detect v5-format suffixes + compute has_scaling at log level
    any_partial = False
    for rec in records:
        pid = rec.get("position_id", "")
        if not isinstance(pid, str):
            continue
        if ":scale_" in pid:
            # v5-format suffix present. Identity fields must be explicit.
            if "exec_seq" not in rec or "parent_position_id" not in rec:
                raise ValueError(
                    f"Mixed-format trade log {path}: record {pid!r} uses v5 "
                    f"':scale_' suffix without required identity fields "
                    f"(parent_position_id, exec_seq). Refusing to load."
                )
        if pid.endswith(":partial"):
            any_partial = True

    # Second pass: build ClosedTrade objects with synthesized identity fields
    trades: list[ClosedTrade] = []
    for rec in records:
        pid = rec.get("position_id", "")
        is_partial = isinstance(pid, str) and pid.endswith(":partial")

        if is_partial:
            parent_id = pid[: -len(":partial")]
            exec_seq = 1
            exec_type = "reduce"
            is_terminal = False
        else:
            parent_id = pid
            exec_seq = 0
            exec_type = "exit"
            is_terminal = True

        # v4 logs sometimes use 'hold_hours' instead of 'hold_bars' — prefer
        # hold_bars if present, else fall back to hold_hours, else 0.
        hold_bars = rec.get("hold_bars")
        if hold_bars is None:
            hold_bars = rec.get("hold_hours", 0)

        trade = ClosedTrade(
            position_id=pid,  # AC34: retain original legacy position_id verbatim
            token=rec.get("token", ""),
            strategy_id=rec.get("strategy_id", ""),
            leg=rec.get("leg", ""),
            entry_bar=int(rec.get("entry_bar", 0)),
            exit_bar=int(rec.get("exit_bar", 0)),
            entry_price=float(rec.get("entry_price", 0.0)),
            exit_price=float(rec.get("exit_price", 0.0)),
            direction=int(rec.get("direction", 0)),
            margin_usd=float(rec.get("margin_usd", 0.0)),
            pnl=float(rec.get("pnl", 0.0)),
            funding_cost=float(rec.get("funding_cost", 0.0)),
            entry_fee=float(rec.get("entry_fee", 0.0)),
            exit_fee=float(rec.get("exit_fee", 0.0)),
            hold_bars=int(hold_bars),
            exit_reason=rec.get("exit_reason", ""),
            is_perp=bool(rec.get("is_perp", False)),
            entry_timestamp=rec.get("entry_timestamp", ""),
            exit_timestamp=rec.get("exit_timestamp", ""),
            limit_price=float(rec.get("limit_price", 0.0)),
            limit_placed_at=rec.get("limit_placed_at", ""),
            stop_limit_price=float(rec.get("stop_limit_price", 0.0)),
            fill_source=rec.get("fill_source", ""),
            parent_position_id=parent_id,
            exec_seq=exec_seq,
            exec_type=exec_type,
            is_terminal=is_terminal,
            triggered_by="",
            has_scaling=any_partial,
            scaling_events=[],
        )
        trades.append(trade)

    return trades


# ---------------------------------------------------------------------------
# M4 Task 10 — Order persistence (AC32) + schema bump
# ---------------------------------------------------------------------------


class _PaperState:
    """Lightweight view over a paper_state.json payload.

    Returned by :func:`read_paper_state`. Only the fields exercised by the
    M4 AC32/AC26 acceptance tests are surfaced as attributes; the raw
    payload is always available via ``.raw`` for callers that need the full
    dict.

    M5 addition: dict-like access (``[]``, ``.get()``) delegates to
    ``self.raw`` so callers that want to inspect the migrated payload
    directly (e.g. the v3 migration tests assert ``migrated.get(
    "schema_version") == 3``) can treat the return value as a mapping.
    """

    __slots__ = ("pending_entries", "schema_version", "raw", "positions")

    def __init__(self, pending_entries=None, schema_version=STATE_SCHEMA_VERSION,
                 raw=None, positions=None):
        self.pending_entries = list(pending_entries or [])
        self.schema_version = schema_version
        self.raw = raw if raw is not None else {}
        self.positions = list(positions or [])

    # ------------------------------------------------------------------ #
    # M5 — dict-like facade for v3 migration assertions.                  #
    # ------------------------------------------------------------------ #

    def __getitem__(self, key):
        return self.raw[key]

    def __contains__(self, key) -> bool:
        return key in self.raw

    def get(self, key, default=None):
        return self.raw.get(key, default)

    # ------------------------------------------------------------------ #
    # M4 Task 26 (AC26 T-B16) — class-style save/load convenience API    #
    # ------------------------------------------------------------------ #

    def save(self, path) -> None:
        """Persist this paper state to ``path`` atomically.

        Thin wrapper over :func:`write_paper_state` that forwards the
        currently-attached ``positions`` / ``pending_entries`` / ``raw``
        payload. Accepts the same ``str`` or ``PathLike`` argument as
        ``write_paper_state``.
        """
        extra = {}
        # Pass-through any pre-existing raw keys (tick_counter, timestamps,
        # etc.) so the round-trip preserves custom fields — but skip the
        # slots we already emit explicitly.
        if isinstance(self.raw, dict):
            for k, v in self.raw.items():
                if k in ("schema_version", "pending_entries", "positions"):
                    continue
                extra[k] = v
        write_paper_state(
            path,
            positions=self.positions,
            pending_entries=self.pending_entries,
            extra=extra or None,
        )

    @classmethod
    def load(cls, path) -> "_PaperState":
        """Load a paper state from ``path`` via :func:`read_paper_state`."""
        return read_paper_state(path)


# Public alias — M4 Task 26 (AC26 T-B16) exposes the class-style API that
# the scale-cap crash-restart acceptance tests import as ``PaperState``.
PaperState = _PaperState


def _migrate_v2_to_v3(raw: dict) -> dict:
    """Migrate a paper_state payload from schema v2 to v3 in place (M5 F9).

    Idempotent: a v3+ payload is returned unchanged. The migration:

      * Bumps ``schema_version`` to 3.
      * Renames ``pending_entries`` → ``open_orders`` (design §F4).
      * Adds M5 defaults to each Order dict:
          - ``legs = []``                         (single-leg fallback)
          - ``fill_policy = "unwind_on_reject"``  (LegFillPolicy.value)
          - ``contingency = 0``                   (ContingencyType.NONE)
          - ``linked_order_id = None``
          - ``time_in_force = "1"``               (TimeInForce.GTC.value)
          - ``venue_order_id = None``
      * Adds Position identity defaults (``order_id = None``,
        ``leg_ref_id = None``) to each Position dict.

    Callers pass the mutated dict through ``_PaperState`` so tests that
    treat the return as a mapping can inspect the migrated fields
    directly (T-M5-10).
    """
    if int(raw.get("schema_version", 1)) >= 3:
        return raw

    # Positions: populate the F9 back-link defaults.
    for pos in raw.get("positions", []) or []:
        if not isinstance(pos, dict):
            continue
        pos.setdefault("order_id", None)
        pos.setdefault("leg_ref_id", None)

    # Rename pending_entries → open_orders (design §F4). If both keys are
    # present (defensive), preserve open_orders (already v3-shape) and drop
    # the legacy key.
    if "pending_entries" in raw and "open_orders" not in raw:
        raw["open_orders"] = raw.pop("pending_entries")
    elif "pending_entries" in raw and "open_orders" in raw:
        raw.pop("pending_entries", None)

    # Add M5 defaults to each Order dict.
    for order in raw.get("open_orders", []) or []:
        if not isinstance(order, dict):
            continue
        order.setdefault("legs", [])
        order.setdefault("fill_policy", "unwind_on_reject")
        order.setdefault("contingency", 0)
        order.setdefault("linked_order_id", None)
        order.setdefault("time_in_force", "1")
        order.setdefault("venue_order_id", None)

    raw["schema_version"] = 3
    return raw


def _migrate_legacy_armed_tokens(raw: dict) -> list:
    """AC32 legacy shim: convert pre-M4 ``_armed_tokens`` dict to Order
    records in the ARMED state. Expected legacy shape::

        {"_armed_tokens": {"<token>": {<arm-kwargs>}}}

    Missing/unknown fields use neutral defaults so the migration never
    crashes on best-effort input. Returns an empty list if no legacy data
    is present or the migration fails cleanly.
    """
    from v5.orders import Order, TriggerType

    legacy = raw.get("_armed_tokens")
    if not legacy or not isinstance(legacy, dict):
        return []

    migrated: list = []
    for token, payload in legacy.items():
        if not isinstance(payload, dict):
            continue
        trigger_raw = payload.get("trigger", TriggerType.PRICE_ABOVE.name)
        try:
            if isinstance(trigger_raw, str):
                trigger = TriggerType[trigger_raw]
            else:
                trigger = TriggerType(int(trigger_raw))
        except (KeyError, ValueError):
            trigger = TriggerType.PRICE_ABOVE
        armed_at_raw = payload.get("armed_at")
        armed_at = (
            _dt.datetime.fromisoformat(armed_at_raw)
            if isinstance(armed_at_raw, str) and armed_at_raw
            else _dt.datetime.now(_dt.timezone.utc)
        )
        if armed_at.tzinfo is None:
            armed_at = armed_at.replace(tzinfo=_dt.timezone.utc)
        expires_at_raw = payload.get("expires_at")
        if isinstance(expires_at_raw, str) and expires_at_raw:
            expires_at = _dt.datetime.fromisoformat(expires_at_raw)
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=_dt.timezone.utc)
        else:
            expires_at = None
        try:
            pe = Order.arm(
                strategy_id=payload.get("strategy_id", ""),
                token=str(token),
                direction=int(payload.get("direction", 1)),
                trigger=trigger,
                trigger_price=float(payload.get("trigger_price", 0.0)),
                working_price_source=payload.get(
                    "working_price_source", "last"
                ),
                armed_at=armed_at,
                expires_at=expires_at,
                sizing_ctx=payload.get("sizing_ctx") or {},
                strategy_params=payload.get("strategy_params") or {},
                window_end=float(payload.get("window_end", 0.0)),
            )
            migrated.append(pe)
        except Exception:  # pragma: no cover - defensive
            continue
    return migrated


def write_paper_state(path, *, positions=None, pending_entries=None,
                      open_orders=None, extra=None) -> None:
    """Write an M4/M5-schema paper_state.json atomically (AC32, AC26, AC9).

    Args:
        path: target path (str or PathLike).
        positions: iterable of :class:`v5.position.Position` — persisted as a
            top-level ``positions`` list via :func:`_serialize_position`.
            Added for M4 Task 26 (AC26 T-B16: scale-cap crash-restart) so
            ``pos._scale_action_bar`` survives a mid-hour restart.
        pending_entries: legacy alias for ``open_orders`` kept for M4
            backward compatibility. Schema v3 renames this key to
            ``open_orders`` (design §F4). If both are provided ``open_orders``
            wins.
        open_orders: iterable of :class:`Order` — persisted as a top-level
            ``open_orders`` list via :meth:`Order.to_json` (schema v3).
        extra: optional dict merged into the payload (e.g. tick_counter,
            last_timestamp) so callers can piggyback on the same file.
    """
    orders_iter = open_orders if open_orders is not None else pending_entries
    payload: dict = {
        "schema_version": STATE_SCHEMA_VERSION,
        "open_orders": [
            pe.to_json() for pe in (orders_iter or [])
        ],
        "positions": [
            _serialize_position(p) for p in (positions or [])
        ],
    }
    if extra:
        for k, v in extra.items():
            if k in ("schema_version", "open_orders",
                     "pending_entries", "positions"):
                continue
            payload[k] = v

    target_path = str(path)
    target_dir = os.path.dirname(target_path) or "."
    os.makedirs(target_dir, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=target_dir, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(payload, indent=2, default=str))
            f.flush()
            os.fsync(f.fileno())
        os.rename(tmp_path, target_path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def read_paper_state(path) -> _PaperState:
    """Read an M4/M5-schema paper_state.json (AC32/AC26/AC9).

    Migration order:
      1. Legacy pre-M4 (``_armed_tokens`` key) → synthesize ``pending_entries``
         via :func:`_migrate_legacy_armed_tokens` (kept as Order objects).
      2. v2 → v3 (``pending_entries`` key, no M5 fields) →
         :func:`_migrate_v2_to_v3` renames to ``open_orders`` and populates
         M5 defaults in place on the raw dict.
      3. v3 native (``open_orders`` key) — no migration needed.

    Returned :class:`_PaperState` is dict-like over the migrated raw
    payload (``state["positions"]``, ``state.get("schema_version")``) so
    v3 migration assertions can inspect the payload directly.
    """
    from v5.orders import Order

    target_path = str(path)
    with open(target_path) as f:
        raw = json.load(f)

    # Pre-M4 legacy shim: synthesize Order objects from _armed_tokens.
    if "pending_entries" not in raw and "open_orders" not in raw:
        legacy_pes = _migrate_legacy_armed_tokens(raw)
    else:
        legacy_pes = None

    # v2 → v3 migration (idempotent).
    _migrate_v2_to_v3(raw)

    # Deserialize Order objects from the post-migration `open_orders`
    # (or fall back to the legacy-synthesized list).
    if legacy_pes is not None:
        pes = legacy_pes
    else:
        pes = [Order.from_json(b) for b in raw.get("open_orders", []) or []]

    # M4 Task 26 (AC26 T-B16) — rehydrate Position records. Legacy payloads
    # without the ``positions`` key deserialize to an empty list (tolerates
    # v4 / pre-T10 files the same way the pending_entries key does).
    positions = [_deserialize_position(d) for d in raw.get("positions", []) or []]

    schema_version = int(raw.get("schema_version", 1))
    return _PaperState(
        pending_entries=pes,
        schema_version=schema_version,
        raw=raw,
        positions=positions,
    )
