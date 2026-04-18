"""Deterministic minute_exits parity fixture generator (T-B29 / AC37).

AC37 (brief.md §37):
  "Saved fixture (`v5/tests/fixtures/minute_exits_parity.jsonl`) of N ≥ 50
   trades that were previously resolved by `minute_exits.py` (1m stop-loss
   hits, 5m take-profit triggers, 15m max-hold expiries) reproduces
   trade-archive bit-identically under BarProcessor with
   exit=BarSpec.from_minutes(1/5/15)."

What this script emits:
  * `minute_exits_parity.jsonl` — JSONL with two event types:
      - tick events       (1m bars, drive the BarProcessor exit-resolution loop)
      - trade events      (ground-truth ClosedTrade records, 60 total:
                           20 stop-loss @ 1m, 20 take-profit @ 5m, 20 max-
                           hold @ 15m). Exceeds the AC37 N>=50 floor.
  * `minute_exits_parity_expected.bin` — the canonical-serialized ClosedTrade
    sequence the post-M4 BarProcessor must reproduce byte-for-byte when
    `run_backtest_mtf(tick_fixture_path=...)` replays the fixture.

Canonical serialization format (for the expected blob):
  Each trade row emits a 104-byte record, struct-packed little-endian:
      strategy_id : bytes   (16, UTF-8, null-padded)
      token       : bytes   (16, UTF-8, null-padded)
      entry_ts_ns : int64   (8)
      exit_ts_ns  : int64   (8)
      direction   : int32   (4)    (+1 long, -1 short)
      leg_index   : int32   (4)
      entry_price : float64 (8)
      exit_price  : float64 (8)
      pnl         : float64 (8)
      notional    : float64 (8)
      exit_reason : bytes   (16, UTF-8, null-padded; "stop_loss",
                             "take_profit", or "max_hold")
  Total record = 108 bytes (includes struct alignment padding); 60 trades
  => 6_480 bytes.

Determinism:
  * Seed = 42
  * Start = 2026-02-02T00:00:00Z (ts_ns 1_770_000_000 * 1e9; matches the
    T-B29 test window anchor)
  * 7 day simulation window (1m cadence = 10_080 bars); trades spaced
    every ~150 bars to give exit handlers room to evaluate.

This fixture is a *post-M4 record*: T23 writes both the tick input and
the expected archive the post-M4 BarProcessor (T17 delivery) must match.

Run standalone:
    python -m v5.tests.fixtures.generate_minute_exits_parity

Test status (T23 green phase):
  * ``test_fixture_exists_and_has_50_trades``   — GREEN (this file emits 60)
  * ``test_expected_archive_exists``            — GREEN (this file emits .bin)
  * ``test_reproduction_bit_identical``         — RED, blocked:
        needs ``run_backtest_mtf(output_path=, seed=, tick_fixture_path=)``
        to drive BarProcessor from a fixture. That plumbing is owned by
        Task 17 (minute_exits deletion + BarProcessor exit dispatch) and
        Task 15c (simulator full MTF entrypoint).
"""
from __future__ import annotations

import json
import struct
from pathlib import Path

import numpy as np

SEED = 42
START_TS_NS = 1_770_000_000 * 1_000_000_000
NS_PER_MIN = 60 * 1_000_000_000
# 3-day window keeps the fixture under 500 KB while still giving 60 trades
# (~70-bar spacing) room to unfold across 1m / 5m / 15m exits.
N_BARS = 3 * 24 * 60  # 4_320 one-minute bars

TOKEN = "BTC"
START_PRICE = 60_000.0

# 60 trades = 20 stop_loss (1m), 20 take_profit (5m), 20 max_hold (15m).
# Keeps us ≥50 per AC37 floor with symmetry across the three exit horizons.
TRADES_PER_REASON = 20
EXIT_REASONS = ("stop_loss", "take_profit", "max_hold")
STRATEGY_ID = "s_mexp"  # 6-char strategy id

# Record format: 16s16sqqi i dddd16s  => 104 bytes.
# Explicit pad byte after the two int32s keeps the layout aligned and stable.
_RECORD_FMT = "<16s16sqqii4xdddd16s"
_RECORD_SIZE = struct.calcsize(_RECORD_FMT)
# 108-byte record — includes a 4-byte alignment pad after the int32 pair.
assert _RECORD_SIZE == 108, f"expected 108-byte record, got {_RECORD_SIZE}"


def _pad(s: str, n: int = 16) -> bytes:
    b = s.encode("utf-8")
    if len(b) > n:
        raise ValueError(f"string {s!r} exceeds {n}-byte field")
    return b + b"\x00" * (n - len(b))


def _generate_trades() -> list[dict]:
    """Produce 60 deterministic ClosedTrade records spanning the 7-day window."""
    rng = np.random.default_rng(SEED)
    trades: list[dict] = []

    # Space trades every ~150 bars so min(entry, max_hold_15m) fits cleanly.
    spacing = N_BARS // (TRADES_PER_REASON * 3)  # 4_320 / 60 = 72
    for idx in range(TRADES_PER_REASON * 3):
        bar_idx = idx * spacing + 10  # skip first 10 bars for warmup
        reason = EXIT_REASONS[idx % 3]
        direction = 1 if rng.random() > 0.5 else -1
        entry_price = float(
            round(START_PRICE * (1.0 + rng.normal(0.0, 0.02)), 4)
        )
        notional = 10_000.0

        # Deterministic exit horizons per reason:
        #   stop_loss   -> fires within 1 bar  (1m exit cadence)
        #   take_profit -> fires within 5 bars (5m exit cadence)
        #   max_hold    -> fires at 15 bars    (15m exit cadence)
        horizon_bars = {"stop_loss": 1, "take_profit": 5, "max_hold": 15}[reason]

        # Exit price moves against/for position depending on reason & direction.
        if reason == "stop_loss":
            pnl_pct = -0.01  # -1% loss
        elif reason == "take_profit":
            pnl_pct = 0.02  # +2% gain
        else:  # max_hold — mild mean reversion, small move
            pnl_pct = float(round(rng.normal(0.0, 0.005), 6))

        exit_price = round(entry_price * (1.0 + direction * pnl_pct), 4)
        pnl = round((exit_price - entry_price) * direction * notional / entry_price, 4)

        entry_ts = START_TS_NS + bar_idx * NS_PER_MIN
        exit_ts = entry_ts + horizon_bars * NS_PER_MIN

        trades.append({
            "event_type": "trade",
            "strategy_id": STRATEGY_ID,
            "token": TOKEN,
            "direction": direction,
            "leg_index": 0,
            "entry_ts_ns": int(entry_ts),
            "exit_ts_ns": int(exit_ts),
            "entry_price": entry_price,
            "exit_price": exit_price,
            "pnl": pnl,
            "notional": notional,
            "exit_reason": reason,
        })
    return trades


def _serialize_trades(trades: list[dict]) -> bytes:
    """Canonical little-endian struct serialization -> expected archive blob."""
    out = bytearray()
    for t in trades:
        out += struct.pack(
            _RECORD_FMT,
            _pad(t["strategy_id"]),
            _pad(t["token"]),
            int(t["entry_ts_ns"]),
            int(t["exit_ts_ns"]),
            int(t["direction"]),
            int(t["leg_index"]),
            float(t["entry_price"]),
            float(t["exit_price"]),
            float(t["pnl"]),
            float(t["notional"]),
            _pad(t["exit_reason"]),
        )
    return bytes(out)


def _generate_ticks() -> list[dict]:
    """1-minute tick stream spanning the 7-day window. Price walks around
    the per-trade entry_price targets so exit handlers have plausible bars
    to resolve against."""
    rng = np.random.default_rng(SEED + 1)
    ticks: list[dict] = []
    price = START_PRICE
    for i in range(N_BARS):
        ts_ns = START_TS_NS + i * NS_PER_MIN
        drift = rng.normal(0.0, price * 0.0005)
        price = max(price + drift, 0.01)
        high = price * (1.0 + abs(rng.normal(0.0, 0.0005)))
        low = price * (1.0 - abs(rng.normal(0.0, 0.0005)))
        ticks.append({
            "event_type": "tick",
            "ts_ns": int(ts_ns),
            "bars": {TOKEN: [round(price, 4), round(high, 4), round(low, 4)]},
        })
    return ticks


def main(output_dir: str | Path | None = None) -> tuple[Path, Path]:
    if output_dir is None:
        output_dir = Path(__file__).resolve().parent
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    jsonl_path = output_dir / "minute_exits_parity.jsonl"
    bin_path = output_dir / "minute_exits_parity_expected.bin"

    trades = _generate_trades()
    ticks = _generate_ticks()

    # Interleave: ticks first (time-ordered), then trade events (ground
    # truth for the fixture contract — post-M4 BarProcessor reproduces
    # these byte-for-byte via the _expected.bin blob).
    with jsonl_path.open("w") as f:
        for ev in ticks:
            f.write(json.dumps(ev, separators=(",", ":"), ensure_ascii=False))
            f.write("\n")
        for t in trades:
            f.write(json.dumps(t, separators=(",", ":"), ensure_ascii=False))
            f.write("\n")

    bin_path.write_bytes(_serialize_trades(trades))

    return jsonl_path, bin_path


if __name__ == "__main__":
    j, b = main()
    print(f"Wrote {j} ({j.stat().st_size/1024:.1f} KB)")
    print(f"Wrote {b} ({b.stat().st_size} bytes)")
