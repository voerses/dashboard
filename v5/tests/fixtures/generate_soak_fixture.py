"""Deterministic soak-fixture generator (AC25).

Produces `v5/tests/fixtures/soak_ticks.jsonl` — a recorded tick stream the
M3 soak test (AC11) replays.

Per AC25 the fixture MUST include >=1 instance of each event type:
  - tick        (hourly bar update; majority)
  - backfill    (gap > 1 bar_period => caller should reseed)
  - promote     (live->historical promotion with OHLC rewrite)
  - reconnect   (WS disconnect+catchup)

Determinism:
  - Seed = 42
  - Epoch = 2026-03-01T00:00:00Z (matches TestClock default)
  - 1440 hourly ticks (60 days); events injected at ticks 600, 720, 900, 1100
  - Token universe = 10 majors (BTC ETH SOL BNB XRP ADA MATIC LINK AVAX UNI)

Run standalone:
    python -m v5.tests.fixtures.generate_soak_fixture

Or invoke `main(output_path=...)` from tests to lazy-regenerate.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import numpy as np

SEED = 42
EPOCH_ISO = "2026-03-01T00:00:00Z"
TOKENS = ["BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "MATIC", "LINK", "AVAX", "UNI"]
N_TICKS = 1440
NS_PER_HOUR = 3_600 * 1_000_000_000

# Injection positions (0-based over the emitted tick stream).
BACKFILL_AT_600_TOKEN = "BTC"
BACKFILL_AT_600_GAP_HOURS = 24
PROMOTE_AT_720_TOKEN = "ETH"
PROMOTE_AT_720_REWRITE_LAST_N = 12
RECONNECT_AT_900_DURATION_SEC = 120
BACKFILL_AT_1100_TOKEN = "SOL"
BACKFILL_AT_1100_GAP_HOURS = 6

# Starting reference prices (deterministic; not a real market).
_START_PRICES = {
    "BTC": 60_000.0, "ETH": 3_000.0, "SOL": 150.0, "BNB": 500.0,
    "XRP": 0.55, "ADA": 0.45, "MATIC": 0.80, "LINK": 15.0,
    "AVAX": 30.0, "UNI": 8.0,
}


def _epoch_ns() -> int:
    dt = datetime.fromisoformat(EPOCH_ISO.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1_000_000_000)


def _synthetic_bar(close: float, rng: np.random.Generator) -> list:
    """Sparse synthetic bar — array form `[c, h, l]` to stay under 500 KB
    across 1440 ticks x 10 tokens. The soak runner fabricates volume/atr/
    funding at ingest from fixed per-token constants (not needed for the
    event-type tests; the full-soak replay re-derives ATR locally)."""
    high = close * (1.0 + abs(rng.normal(0.0, 0.002)))
    low = close * (1.0 - abs(rng.normal(0.0, 0.002)))
    return [
        round(close, 3),
        round(high, 3),
        round(low, 3),
    ]


BAR_FIELDS = ("close", "high", "low")
BACKFILL_BAR_FIELDS = ("ts_ns",) + BAR_FIELDS


def bar_array_to_dict(arr):
    """Runner helper: convert the sparse list-form [c, h, l] back to the
    canonical dict used by the tick path. Volume/atr/funding default to 0
    (runner may fill with sensible constants during replay)."""
    d = dict(zip(BAR_FIELDS, arr))
    d.setdefault("volume", 1000.0)
    d.setdefault("atr", d["close"] * 0.01)
    d.setdefault("funding", 0.0)
    return d


def backfill_bar_array_to_dict(arr):
    """Runner helper: convert the sparse backfill bar [ts_ns, c, h, l] back
    to a dict with explicit ts_ns + defaults for the remaining fields."""
    d = dict(zip(BACKFILL_BAR_FIELDS, arr))
    d.setdefault("volume", 1000.0)
    d.setdefault("atr", d["close"] * 0.01)
    d.setdefault("funding", 0.0)
    return d


def _emit_event(event: dict) -> str:
    # Compact JSON (no spaces) to keep fixture < 500 KB.
    return json.dumps(event, separators=(",", ":"), ensure_ascii=False)


def _generate_events() -> Iterable[dict]:
    rng = np.random.default_rng(SEED)
    epoch_ns = _epoch_ns()
    # Per-token price walk state.
    prices = {t: _START_PRICES[t] for t in TOKENS}

    for i in range(N_TICKS):
        ts_ns = epoch_ns + i * NS_PER_HOUR

        # Inject event BEFORE tick emission where the AC specifies an index.
        if i == 600:
            # BACKFILL: 24h-gap worth of bars for BTC appended as a batch.
            gap_h = BACKFILL_AT_600_GAP_HOURS
            new_bars = []
            p = prices[BACKFILL_AT_600_TOKEN]
            for h in range(gap_h):
                # Bars for hours [i - gap_h, i)
                b_ts = epoch_ns + (i - gap_h + h) * NS_PER_HOUR
                drift = rng.normal(0.0, p * 0.002)
                p = max(p + drift, 0.0001)
                bar = _synthetic_bar(p, rng)
                new_bars.append([b_ts] + bar)
            prices[BACKFILL_AT_600_TOKEN] = p
            yield {
                "type": "backfill",
                "ts_ns": ts_ns,
                "token": BACKFILL_AT_600_TOKEN,
                "bar_type": "1h",
                "new_bars": new_bars,
            }

        if i == 720:
            # PROMOTE: rewrite last N bars for ETH with corrected OHLC.
            rewritten = []
            p = prices[PROMOTE_AT_720_TOKEN]
            for h in range(PROMOTE_AT_720_REWRITE_LAST_N):
                b_ts = epoch_ns + (i - PROMOTE_AT_720_REWRITE_LAST_N + h) * NS_PER_HOUR
                # Slightly different OHLC than the live bars (simulates
                # fix_promoted_bars correcting mid-bar noise).
                drift = rng.normal(0.0, p * 0.001)
                p = max(p + drift, 0.0001)
                bar = _synthetic_bar(p, rng)
                rewritten.append([b_ts] + bar)
            yield {
                "type": "promote",
                "ts_ns": ts_ns,
                "token": PROMOTE_AT_720_TOKEN,
                "market": "perp",
                "rewritten_bars": rewritten,
            }

        if i == 900:
            yield {
                "type": "reconnect",
                "ts_ns": ts_ns,
                "duration_sec": RECONNECT_AT_900_DURATION_SEC,
            }

        if i == 1100:
            gap_h = BACKFILL_AT_1100_GAP_HOURS
            new_bars = []
            p = prices[BACKFILL_AT_1100_TOKEN]
            for h in range(gap_h):
                b_ts = epoch_ns + (i - gap_h + h) * NS_PER_HOUR
                drift = rng.normal(0.0, p * 0.002)
                p = max(p + drift, 0.0001)
                bar = _synthetic_bar(p, rng)
                new_bars.append([b_ts] + bar)
            prices[BACKFILL_AT_1100_TOKEN] = p
            yield {
                "type": "backfill",
                "ts_ns": ts_ns,
                "token": BACKFILL_AT_1100_TOKEN,
                "bar_type": "1h",
                "new_bars": new_bars,
            }

        # Normal tick (random walk per token).
        bars = {}
        for t in TOKENS:
            drift = rng.normal(0.0, prices[t] * 0.002)
            prices[t] = max(prices[t] + drift, 0.0001)
            bars[t] = _synthetic_bar(prices[t], rng)
        yield {
            "type": "tick",
            "ts_ns": ts_ns,
            "bars": bars,
        }


def main(output_path: str | Path | None = None) -> Path:
    """Write the fixture deterministically. Returns the output path."""
    if output_path is None:
        output_path = Path(__file__).resolve().parent / "soak_ticks.jsonl"
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w") as f:
        for ev in _generate_events():
            f.write(_emit_event(ev))
            f.write("\n")
    return output_path


if __name__ == "__main__":
    path = main()
    size = path.stat().st_size
    print(f"Wrote {path} ({size/1024:.1f} KB)")
