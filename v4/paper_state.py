"""V4 Paper Trading — State persistence.

Handles:
  - Serialize/deserialize SimulationState (including Position fields with numpy arrays, sets)
  - Atomic write (write to temp, fsync, rename)
  - Append-only writers: trades.jsonl, equity.csv
  - Position ID generation with tick_counter
"""
from __future__ import annotations

import csv
import json
import os
import tempfile
from dataclasses import asdict
from typing import Optional

import numpy as np

from v4.position import Position, ClosedTrade, PositionManager
from v4.simulator import SimulationState


STATE_VERSION = 1


def make_position_id(token: str, strategy_id: str, tick_counter: int, leg: str) -> str:
    """Generate position ID: '{token}:{strategy_id}:{tick_counter}:{leg}'."""
    return f"{token}:{strategy_id}:{tick_counter}:{leg}"


# ---------------------------------------------------------------------------
# Position serialization helpers
# ---------------------------------------------------------------------------

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
        "exit_regimes": sorted(pos.exit_regimes),
        "convex_exit": bool(pos.convex_exit),
        "rsi_exit_level": float(pos.rsi_exit_level),
        "trail_schedule": pos.trail_schedule.tolist() if pos.trail_schedule is not None else None,
        "time_trail_schedule": pos.time_trail_schedule.tolist() if pos.time_trail_schedule is not None else None,
        "max_trail_mult_arr": pos.max_trail_mult_arr.tolist() if pos.max_trail_mult_arr is not None else None,
        "funding_exit_threshold": float(pos.funding_exit_threshold),
        "partial_tp_atr": float(pos.partial_tp_atr),
        "partial_tp_pct": float(pos.partial_tp_pct),
        "partial_tp_trail": float(pos.partial_tp_trail),
        "breakeven_atr": float(pos.breakeven_atr),
        "breakeven_triggered": bool(pos.breakeven_triggered),
        "chandelier_lookback": int(pos.chandelier_lookback),
        "partial_closed": bool(pos.partial_closed),
        "stop_price": float(pos.stop_price),
        "highest": float(pos.highest),
        "lowest": float(pos.lowest),
        "initial_risk": float(pos.initial_risk),
        "cumulative_funding": float(pos.cumulative_funding),
        "linked_position_id": pos.linked_position_id,
        "entry_timestamp": pos.entry_timestamp,
    }
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

    return Position(
        position_id=d["position_id"],
        token=d["token"],
        strategy_id=d["strategy_id"],
        leg=d["leg"],
        entry_bar=d["entry_bar"],
        entry_price=d["entry_price"],
        direction=d["direction"],
        quantity=d["quantity"],
        margin_usd=d["margin_usd"],
        leverage=d["leverage"],
        is_perp=d["is_perp"],
        fee_rate=d["fee_rate"],
        stop_mult=d["stop_mult"],
        trail_mult=d["trail_mult"],
        target_mult=d["target_mult"],
        no_stop_bars=d["no_stop_bars"],
        min_hold=d["min_hold"],
        max_hold=d["max_hold"],
        exit_regimes=set(d["exit_regimes"]),
        convex_exit=d.get("convex_exit", False),
        rsi_exit_level=d.get("rsi_exit_level", 999.0),
        trail_schedule=trail_schedule,
        time_trail_schedule=time_trail_schedule,
        max_trail_mult_arr=max_trail_mult_arr,
        funding_exit_threshold=d.get("funding_exit_threshold", 0.0),
        partial_tp_atr=d.get("partial_tp_atr", 0.0),
        partial_tp_pct=d.get("partial_tp_pct", 0.5),
        partial_tp_trail=d.get("partial_tp_trail", 1.5),
        breakeven_atr=d.get("breakeven_atr", 0.5),
        breakeven_triggered=d.get("breakeven_triggered", False),
        chandelier_lookback=d.get("chandelier_lookback", 0),
        partial_closed=d.get("partial_closed", False),
        stop_price=d.get("stop_price", 0.0),
        highest=d.get("highest", 0.0),
        lowest=d.get("lowest", 999999.0),
        initial_risk=d.get("initial_risk", 0.0),
        cumulative_funding=d.get("cumulative_funding", 0.0),
        linked_position_id=d.get("linked_position_id"),
        entry_timestamp=d.get("entry_timestamp", ""),
    )


# ---------------------------------------------------------------------------
# State serialization
# ---------------------------------------------------------------------------

def serialize_state(
    state: SimulationState,
    tick_counter: int,
    last_timestamp: str,
    shadow_pools: Optional[dict] = None,
    last_known_prices: Optional[dict] = None,
    last_known_regimes: Optional[dict] = None,
) -> dict:
    """Serialize SimulationState to a JSON-compatible dict.

    Closed trades are NOT included — they go to trades.jsonl.
    """
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

    if last_known_regimes is not None:
        data["last_known_regimes"] = {k: int(v) for k, v in last_known_regimes.items()}

    return data


def deserialize_state(data: dict, return_shadow: bool = False):
    """Deserialize a state dict back to SimulationState + tick_counter.

    Returns (state, tick_counter) or (state, tick_counter, shadow_pools) if return_shadow=True.
    last_timestamp is stored as state.last_timestamp for callers that need it.
    Closed trades list is always empty (they live in trades.jsonl).
    """
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
    last_known_regimes: Optional[dict] = None,
) -> None:
    """Write state to target_path atomically (write to temp, fsync, rename).

    If rename fails, the original file is preserved.
    """
    data = serialize_state(state, tick_counter, last_timestamp, shadow_pools, last_known_prices, last_known_regimes)
    json_str = json.dumps(data, indent=2)

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
    }
    if tick is not None:
        d["tick"] = tick
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
    last_known_regimes: Optional[dict] = None,
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

    if last_known_regimes is not None:
        data["last_known_regimes"] = {k: int(v) for k, v in last_known_regimes.items()}

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
