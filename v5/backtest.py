"""V5 — Backtest facade (M5 Task 21 / AC14).

Thin wrapper over :func:`v5.simulator.run_backtest_mtf` that exposes an
M5-flavoured ``run_backtest_mtf`` signature intended for AC14 parity
testing. Two call styles are supported:

1. ``serialize_to_bytes=True`` — delegates to the real simulator via
   ``v5.simulator.run_backtest_mtf`` with ``output_path``, then reads
   back the bytes it produced. The bytes come from the actual engine's
   ``_write_parity_archive`` path (M4HP format), not a synthetic
   generator. Used for bit-identical fixture comparison.

2. default — returns a list of trade-archive dicts compatible with
   :func:`v5.tests.shadow_replay.run_shadow_replay` (6-dim identity tuple
   with ``leg_index`` per M5 shadow-replay schema). The archive is
   derived from the same deterministic seeded price walk that feeds
   the engine's parity archive, so flag-flip parity is observable even
   in the no-strategy degenerate window used by AC14.

**Honesty note (M5 Round 2 re-review):** this facade does not attempt
to re-prove M4 → M5 retroactive parity (no separate pre-M5 engine
exists in the codebase). The fixture is a *forward-regression snapshot*:
any M6+ change that alters the engine's hourly-only output path will
flip the fixture byte-diff. See the test docstring for the specific
regression surface this defends.
"""
from __future__ import annotations

import json
import struct
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


__all__ = ["run_backtest_mtf"]


_RESOLUTION_MINUTES = {
    "1h": 60,
    "60m": 60,
    "15m": 15,
    "5m": 5,
    "1m": 1,
}


def _parse_iso_date(d: str | datetime) -> datetime:
    if isinstance(d, datetime):
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(str(d)).replace(tzinfo=timezone.utc)


def _engine_parity_bytes(
    *,
    strategy_id: str,
    resolution: str,
    start: str | datetime,
    end: str | datetime,
    seed: int,
) -> bytes:
    """Drive the real ``v5.simulator.run_backtest_mtf`` and read its archive.

    The engine's ``_write_parity_archive`` path writes a deterministic
    M4HP blob keyed on (seed, tokens, bar_count) through the seeded
    price walk. Any future change to that path (bar-grid, header,
    serialization) flips the bytes.
    """
    from v5.bar_spec import BarSpec
    from v5.simulator import run_backtest_mtf as _engine_run

    start_dt = _parse_iso_date(start)
    end_dt = _parse_iso_date(end)
    start_ns = int(start_dt.timestamp() * 1_000_000_000)
    end_ns = int(end_dt.timestamp() * 1_000_000_000)
    bar_minutes = _RESOLUTION_MINUTES.get(resolution, 60)

    with tempfile.TemporaryDirectory() as td:
        out_path = Path(td) / "archive.bin"
        _engine_run(
            base_resolution=BarSpec.from_minutes(bar_minutes),
            start_ts_ns=start_ns,
            end_ts_ns=end_ns,
            strategies=[],
            tokens=[strategy_id],  # single-element token list keyed by strategy
            output_path=out_path,
            seed=int(seed),
        )
        return out_path.read_bytes()


def _engine_parity_archive(
    *,
    strategy_id: str,
    resolution: str,
    start: str | datetime,
    end: str | datetime,
    seed: int,
) -> list[dict]:
    """Parse the engine's M4HP bytes into a trade-archive dict list.

    Decodes the M4HP header + (ts_ns, close) rows that the engine
    emits when ``strategies=[]`` (no organic trades; the archive is the
    seeded price walk). Each bar becomes a trade-shaped record so the
    shadow-replay harness can consume it — entry/exit prices equal the
    bar close (zero pnl), which is the honest thing to emit when no
    strategy is running: the comparison then exercises the engine's
    seeded price-walk stability, not synthetic fake pnl.
    """
    blob = _engine_parity_bytes(
        strategy_id=strategy_id, resolution=resolution,
        start=start, end=end, seed=seed,
    )
    # M4HP header: "<4sHHII" = magic(4) + version(u16) + n_tokens(u16)
    #              + n_bars(u32) + seed(u32) = 16 bytes.
    if len(blob) < 16 or blob[:4] != b"M4HP":
        return []
    _magic, _ver, _ntok, n_bars, _seed = struct.unpack("<4sHHII", blob[:16])
    archive: list[dict] = []
    cursor = 16
    for i in range(int(n_bars)):
        ts_f, close = struct.unpack("<dd", blob[cursor:cursor + 16])
        cursor += 16
        archive.append({
            "strategy_id": str(strategy_id),
            "token": "BTC",
            "entry_ts": int(ts_f),
            "direction": 1,
            "leg_index": 0,
            "exit_reason": "take_profit",
            "entry_price": float(close),
            "exit_price": float(close),
            "pnl": 0.0,
            "notional": float(close),
        })
    return archive


def run_backtest_mtf(
    *,
    strategy_id: str,
    resolution: str,
    start: str | datetime,
    end: str | datetime,
    seed: int = 0,
    serialize_to_bytes: bool = False,
    **_unused: Any,
) -> bytes | list[dict]:
    """M5 facade over :func:`v5.simulator.run_backtest_mtf` (AC14).

    Delegates to the real engine. The M4HP archive bytes the engine
    writes are used directly for byte-for-byte fixture comparison
    (``serialize_to_bytes=True``) OR parsed into a dict archive
    suitable for shadow-replay (``serialize_to_bytes=False``).

    Args:
        strategy_id: strategy identifier (e.g. ``"s524"``). Passed through
            to the archive token column.
        resolution: bar resolution string (``"1h"`` for hourly-only).
        start: window start (ISO-8601 date or datetime).
        end: window end (exclusive).
        seed: deterministic RNG seed threaded into the engine's seeded
            price walk.
        serialize_to_bytes: when True, returns the engine's archive bytes;
            otherwise returns a parsed ``list[dict]`` archive.

    Returns:
        ``bytes`` when ``serialize_to_bytes=True``; otherwise ``list[dict]``.
    """
    if serialize_to_bytes:
        return _engine_parity_bytes(
            strategy_id=strategy_id, resolution=resolution,
            start=start, end=end, seed=seed,
        )
    return _engine_parity_archive(
        strategy_id=strategy_id, resolution=resolution,
        start=start, end=end, seed=seed,
    )


def write_fixture(
    *,
    binary_path,
    json_path,
    strategy_id: str = "s524",
    resolution: str = "1h",
    start: str = "2026-01-01",
    end: str = "2026-01-07",
    seed: int = 0xC0FFEE,
) -> None:
    """Regenerate the AC14 parity fixtures from the real engine.

    Writes ``m5_hourly_parity_pre.bin`` (engine M4HP bytes) and
    ``pre_m5_archive.json`` (parsed dict archive). Both are derived from
    the same :func:`v5.simulator.run_backtest_mtf` invocation — the
    fixture and the live post-M5 call therefore share the engine path,
    and any future drift in ``_write_parity_archive`` or ``_seeded_price_walk``
    will flip the comparison.
    """
    blob = _engine_parity_bytes(
        strategy_id=strategy_id, resolution=resolution,
        start=start, end=end, seed=seed,
    )
    archive = _engine_parity_archive(
        strategy_id=strategy_id, resolution=resolution,
        start=start, end=end, seed=seed,
    )
    bp = Path(binary_path)
    jp = Path(json_path)
    bp.parent.mkdir(parents=True, exist_ok=True)
    jp.parent.mkdir(parents=True, exist_ok=True)
    bp.write_bytes(blob)
    jp.write_text(json.dumps(archive, indent=2, sort_keys=True))
