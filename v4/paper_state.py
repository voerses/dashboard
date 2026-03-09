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
    """Serialize a Position to a JSON-compatible dict."""
    d = {
        "position_id": pos.position_id,
        "token": pos.token,
        "strategy_id": pos.strategy_id,
        "leg": pos.leg,
        "entry_bar": pos.entry_bar,
        "entry_price": pos.entry_price,
        "direction": pos.direction,
        "quantity": pos.quantity,
        "margin_usd": pos.margin_usd,
        "leverage": pos.leverage,
        "is_perp": pos.is_perp,
        "fee_rate": pos.fee_rate,
        "stop_mult": pos.stop_mult,
        "trail_mult": pos.trail_mult,
        "target_mult": pos.target_mult,
        "no_stop_bars": pos.no_stop_bars,
        "min_hold": pos.min_hold,
        "max_hold": pos.max_hold,
        "exit_regimes": sorted(pos.exit_regimes),
        "convex_exit": pos.convex_exit,
        "rsi_exit_level": pos.rsi_exit_level,
        "trail_schedule": pos.trail_schedule.tolist() if pos.trail_schedule is not None else None,
        "max_trail_mult_arr": pos.max_trail_mult_arr.tolist() if pos.max_trail_mult_arr is not None else None,
        "stop_price": pos.stop_price,
        "highest": pos.highest,
        "lowest": pos.lowest,
        "initial_risk": pos.initial_risk,
        "cumulative_funding": pos.cumulative_funding,
        "linked_position_id": pos.linked_position_id,
    }
    return d


def _deserialize_position(d: dict) -> Position:
    """Deserialize a dict to a Position."""
    trail_schedule = None
    if d.get("trail_schedule") is not None:
        trail_schedule = np.array(d["trail_schedule"], dtype=np.float64)

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
        max_trail_mult_arr=max_trail_mult_arr,
        stop_price=d.get("stop_price", 0.0),
        highest=d.get("highest", 0.0),
        lowest=d.get("lowest", 999999.0),
        initial_risk=d.get("initial_risk", 0.0),
        cumulative_funding=d.get("cumulative_funding", 0.0),
        linked_position_id=d.get("linked_position_id"),
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
) -> dict:
    """Serialize SimulationState to a JSON-compatible dict.

    Closed trades are NOT included — they go to trades.jsonl.
    """
    positions = [_serialize_position(p) for p in state.position_manager.open_positions]

    data = {
        "version": STATE_VERSION,
        "tick_counter": tick_counter,
        "last_timestamp": last_timestamp,
        "initial_capital": state.initial_capital,
        "realized_pnl": state.realized_pnl,
        "total_fees": state.total_fees,
        "total_funding": state.total_funding,
        "open_positions": positions,
        "entry_fees_by_pos": dict(state._entry_fees_by_pos),
    }

    if shadow_pools is not None:
        data["shadow_pools"] = shadow_pools

    if last_known_prices is not None:
        data["last_known_prices"] = last_known_prices

    return data


def deserialize_state(data: dict, return_shadow: bool = False):
    """Deserialize a state dict back to SimulationState + tick_counter.

    Returns (state, tick_counter) or (state, tick_counter, shadow_pools) if return_shadow=True.
    last_timestamp is stored as state.last_timestamp for callers that need it.
    Closed trades list is always empty (they live in trades.jsonl).
    """
    state = SimulationState(initial_capital=data["initial_capital"])
    state.realized_pnl = data["realized_pnl"]
    state.total_fees = data["total_fees"]
    state.total_funding = data["total_funding"]
    state.last_timestamp = data.get("last_timestamp", None)

    for pos_data in data.get("open_positions", []):
        pos = _deserialize_position(pos_data)
        state.position_manager.open_position(pos)

    state._entry_fees_by_pos = dict(data.get("entry_fees_by_pos", {}))
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
) -> None:
    """Write state to target_path atomically (write to temp, fsync, rename).

    If rename fails, the original file is preserved.
    """
    data = serialize_state(state, tick_counter, last_timestamp, shadow_pools, last_known_prices)
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

def _closed_trade_to_dict(trade: ClosedTrade) -> dict:
    """Convert a ClosedTrade to a JSON-serializable dict."""
    return {
        "position_id": trade.position_id,
        "token": trade.token,
        "strategy_id": trade.strategy_id,
        "leg": trade.leg,
        "entry_bar": trade.entry_bar,
        "exit_bar": trade.exit_bar,
        "entry_price": trade.entry_price,
        "exit_price": trade.exit_price,
        "direction": trade.direction,
        "margin_usd": trade.margin_usd,
        "pnl": trade.pnl,
        "funding_cost": trade.funding_cost,
        "entry_fee": trade.entry_fee,
        "exit_fee": trade.exit_fee,
        "hold_bars": trade.hold_bars,
        "exit_reason": trade.exit_reason,
        "is_perp": trade.is_perp,
    }


def append_trades(trades: list[ClosedTrade], path: str) -> None:
    """Append closed trades as JSON lines to trades.jsonl."""
    with open(path, "a") as f:
        for trade in trades:
            f.write(json.dumps(_closed_trade_to_dict(trade)) + "\n")


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
