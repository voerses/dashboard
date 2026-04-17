"""Position management commands — file-based command queue for live paper runner.

CLI side (run_paper_multi.py --close / --positions):
  - write_command(): append a command to the pool's commands.jsonl
  - list_positions_for_state_dir(): read state.json and print open positions

Runner side (in main loop):
  - read_pending_commands(): read commands.jsonl, return list, move to processed.jsonl
  - apply_command_to_engine(): dispatch one command to a paper engine

Commands are JSONL (one per line) for atomic append safety. After processing,
each command is moved to processed.jsonl with an outcome field.

This module is INTENTIONALLY isolated from the rest of v4 to keep blast radius
zero. It only imports stdlib + pathlib. The dispatch function takes a paper
engine instance and operates on its public state.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

COMMANDS_FILENAME = "commands.jsonl"
PROCESSED_FILENAME = "commands_processed.jsonl"


# ---------------------------------------------------------------------------
# CLI side — writing commands and listing positions
# ---------------------------------------------------------------------------

def write_command(state_dir: Path, command: dict) -> str:
    """Append a command to the pool's commands.jsonl. Returns command id."""
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    cmd = dict(command)
    if "id" not in cmd:
        cmd["id"] = uuid.uuid4().hex[:12]
    if "issued_at" not in cmd:
        cmd["issued_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    path = state_dir / COMMANDS_FILENAME
    with open(path, "a") as f:
        f.write(json.dumps(cmd) + "\n")
    return cmd["id"]


def list_positions_for_state_dir(state_dir: Path) -> list[dict]:
    """Read state.json and return open positions with computed MTM info.

    Returns a list of dicts with keys: pool, token, direction, entry_price,
    current_price, current_ret_pct, current_ret_lev_pct, margin_usd,
    entry_timestamp, hours_held, stop_price.
    """
    state_dir = Path(state_dir)
    state_path = state_dir / "state.json"
    if not state_path.exists():
        return []
    with open(state_path) as f:
        state = json.load(f)
    open_positions = state.get("open_positions", [])
    last_prices = state.get("last_known_prices", {})
    last_ts = state.get("last_timestamp")

    out: list[dict] = []
    for p in open_positions:
        token = p.get("token")
        cur_price = last_prices.get(token)
        if cur_price is None:
            cur_price = p.get("entry_price", 0)
        entry_price = p.get("entry_price", 0)
        direction = p.get("direction", 0)
        leverage = p.get("leverage", 1.0)
        if entry_price == 0:
            cur_ret = 0.0
        else:
            cur_ret = (cur_price - entry_price) / entry_price * direction

        # Hours held
        entry_ts = p.get("entry_timestamp", "")
        hours_held = 0.0
        if entry_ts and last_ts:
            try:
                from datetime import datetime
                e = datetime.fromisoformat(entry_ts.replace("Z", "+00:00"))
                n = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
                hours_held = (n - e).total_seconds() / 3600
            except Exception:
                pass

        out.append({
            "pool": state_dir.name,
            "token": token,
            "strategy_id": p.get("strategy_id", ""),
            "direction": direction,
            "entry_price": entry_price,
            "current_price": cur_price,
            "current_ret_pct": cur_ret * 100,
            "current_ret_lev_pct": cur_ret * leverage * 100,
            "margin_usd": p.get("margin_usd", 0),
            "entry_timestamp": entry_ts,
            "hours_held": hours_held,
            "stop_price": p.get("stop_price", 0),
            "position_id": p.get("position_id", ""),
        })
    return out


# ---------------------------------------------------------------------------
# Runner side — reading and processing commands
# ---------------------------------------------------------------------------

def read_pending_commands(state_dir: Path) -> list[dict]:
    """Read commands.jsonl and clear it. Returns list of pending commands.

    Atomic-ish: rename to a temp file then read it, so concurrent appends
    from the CLI go to a fresh file. If the file doesn't exist, returns [].
    """
    state_dir = Path(state_dir)
    path = state_dir / COMMANDS_FILENAME
    if not path.exists():
        return []
    # Rename to a tmp file to "claim" the pending commands
    tmp_path = path.with_suffix(".jsonl.processing")
    try:
        path.rename(tmp_path)
    except FileNotFoundError:
        return []
    commands: list[dict] = []
    try:
        with open(tmp_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    commands.append(json.loads(line))
                except Exception as e:
                    logger.warning("Failed to parse command line: %s — %s", line[:200], e)
    finally:
        try:
            tmp_path.unlink()
        except Exception:
            pass
    return commands


def append_processed(state_dir: Path, command: dict, outcome: dict) -> None:
    """Append a processed command + outcome to processed.jsonl."""
    state_dir = Path(state_dir)
    record = {**command, "outcome": outcome, "processed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    with open(state_dir / PROCESSED_FILENAME, "a") as f:
        f.write(json.dumps(record, default=str) + "\n")


def apply_command_to_engine(engine: Any, command: dict) -> dict:
    """Dispatch one command to a paper engine. Returns outcome dict.

    Supported commands:
      - {"type": "close", "token": "FORM", "force": false}
        Closes ALL open positions for the given token in this engine.
        If force is omitted or false, in-grace positions are skipped and
        reported in the outcome's `grace_blocked` list.
        Returns {"status": "ok|not_found|grace_blocked|error", "n_closed": N, ...}.
    """
    cmd_type = command.get("type")
    if cmd_type == "close":
        token = command.get("token")
        if not token:
            return {"status": "error", "reason": "missing token"}
        force = bool(command.get("force", False))
        reason = command.get("reason", "manual_close")
        min_hours_held = float(command.get("min_hours_held", 0))
        return _close_token_in_engine(engine, token, reason=reason, force=force, min_hours_held=min_hours_held)

    return {"status": "error", "reason": f"unknown command type: {cmd_type}"}


def _close_token_in_engine(
    engine: Any,
    token: str,
    reason: str = "manual_close",
    force: bool = False,
    min_hours_held: float = 0.0,
) -> dict:
    """Find positions matching the token in this engine and close them.

    Models the close on `_emergency_close_all` in paper_engine.py — uses
    last_known_prices for the exit price and accounts via simulator-style
    realized_pnl + total_fees + funding bookkeeping.

    Two independent age filters:

      no_stop_bars grace (unless force=True):
        Positions still within their strategy-defined no_stop_bars grace
        window are skipped. Protects manual closes from nuking positions
        that the strategy hasn't yet had time to evaluate via stops.
        The Mission P breadth cull uses force=True because its signal is
        portfolio-level regime, orthogonal to price stops.

      min_hours_held (always enforced, independent of force):
        Positions younger than min_hours_held are skipped as
        "age_protected". Even with force=True, positions below this floor
        are not closed. This is Mission P's own 3-day grace — we never
        want to cut freshly-opened positions that have no signal yet,
        even in execute mode. Defense-in-depth against token-scoped
        commands accidentally closing a new entry that shares a token
        with an older position being culled.
    """
    closed: list[dict] = []
    errors: list[dict] = []
    grace_blocked: list[dict] = []
    age_protected: list[dict] = []

    # Find all positions matching the token. Use the engine's existing
    # _get_all_states() which handles pool vs independent mode correctly.
    targets: list[Any] = []
    for st in engine._get_all_states():
        for pos in list(st.position_manager.open_positions):
            if pos.token == token:
                targets.append(pos)

    if not targets:
        return {"status": "not_found", "n_closed": 0, "details": []}

    # min_hours_held filter — ALWAYS enforced (even with force=True).
    # Computes hours from WALL-CLOCK entry_timestamp vs now, NOT from tick_counter.
    # tick_counter can drift from wall clock due to restarts, funding settlements,
    # and sub-hourly processing, so bars_held is unreliable for age checks.
    if min_hours_held > 0:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        filtered: list[Any] = []
        for pos in targets:
            entry_ts_str = getattr(pos, "entry_timestamp", "") or ""
            if not entry_ts_str:
                # No timestamp → cannot verify age → PROTECT conservatively
                age_protected.append({
                    "position_id": pos.position_id,
                    "token": pos.token,
                    "hours_held": 0.0,
                    "min_hours_required": min_hours_held,
                    "reason": "no_entry_timestamp",
                })
                logger.warning("AGE PROTECT (no entry_timestamp): refusing to close %s", pos.position_id)
                continue
            try:
                entry_dt = datetime.fromisoformat(entry_ts_str.replace("Z", "+00:00"))
                hours_held = (now - entry_dt).total_seconds() / 3600
            except Exception as e:
                age_protected.append({
                    "position_id": pos.position_id,
                    "token": pos.token,
                    "hours_held": 0.0,
                    "min_hours_required": min_hours_held,
                    "reason": f"entry_timestamp_parse_error: {e}",
                })
                logger.warning("AGE PROTECT (parse error): refusing to close %s", pos.position_id)
                continue
            if hours_held < min_hours_held:
                age_protected.append({
                    "position_id": pos.position_id,
                    "token": pos.token,
                    "hours_held": round(hours_held, 2),
                    "min_hours_required": min_hours_held,
                    "hours_remaining": round(min_hours_held - hours_held, 2),
                })
                logger.warning(
                    "AGE PROTECT: refusing to close %s pos=%s hours_held=%.1f < min_hours_held=%.1f",
                    pos.token, pos.position_id, hours_held, min_hours_held,
                )
            else:
                filtered.append(pos)
        targets = filtered

    # no_stop_bars grace filter (unless force=True)
    if not force:
        filtered2: list[Any] = []
        for pos in targets:
            bars_held = engine.tick_counter - pos.entry_bar
            no_stop_bars = getattr(pos, "no_stop_bars", 0) or 0
            if bars_held < no_stop_bars:
                grace_blocked.append({
                    "position_id": pos.position_id,
                    "token": pos.token,
                    "bars_held": int(bars_held),
                    "no_stop_bars": int(no_stop_bars),
                    "bars_remaining": int(no_stop_bars - bars_held),
                })
                logger.warning(
                    "GRACE BLOCK: refusing to close %s pos=%s bars_held=%d < no_stop_bars=%d. "
                    "Use force=True to override.",
                    pos.token, pos.position_id, bars_held, no_stop_bars,
                )
            else:
                filtered2.append(pos)
        targets = filtered2

    if not targets:
        status = "age_protected" if age_protected and not grace_blocked else (
            "grace_blocked" if grace_blocked else "no_targets"
        )
        return {
            "status": status,
            "n_closed": 0,
            "details": [],
            "grace_blocked": grace_blocked,
            "age_protected": age_protected,
        }

    timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    for pos in targets:
        try:
            exit_price = engine._last_known_prices.get(pos.token, pos.entry_price)
            st = engine._get_state_for_position(pos)
            entry_fee = st._entry_fees_by_pos.pop(pos.position_id, 0.0)
            exit_fee = abs(pos.quantity * exit_price) * pos.fee_rate
            if pos.direction == 1:
                raw_pnl = pos.quantity * (exit_price - pos.entry_price)
            else:
                raw_pnl = abs(pos.quantity) * (pos.entry_price - exit_price)
            net_pnl = raw_pnl - exit_fee - pos.cumulative_funding

            st.position_manager.close_position(
                pos=pos,
                exit_bar=engine.tick_counter,
                exit_price=exit_price,
                pnl=net_pnl,
                funding_cost=pos.cumulative_funding,
                entry_fee=entry_fee,
                exit_fee=exit_fee,
                exit_reason=reason,
                exit_timestamp=timestamp,
            )
            st.realized_pnl += raw_pnl
            st.total_fees += exit_fee

            closed.append({
                "position_id": pos.position_id,
                "token": pos.token,
                "direction": pos.direction,
                "exit_price": exit_price,
                "net_pnl": net_pnl,
            })
            logger.info("Manual close: %s pos=%s exit=%.6f net_pnl=%.2f", pos.token, pos.position_id, exit_price, net_pnl)
        except Exception as e:
            logger.exception("Failed to close position %s", pos.position_id)
            errors.append({"position_id": pos.position_id, "error": str(e)})

    out = {
        "status": "ok" if not errors else "partial",
        "n_closed": len(closed),
        "details": closed,
        "errors": errors,
    }
    if grace_blocked:
        out["grace_blocked"] = grace_blocked
    if age_protected:
        out["age_protected"] = age_protected
    if not closed:
        if age_protected and not grace_blocked:
            out["status"] = "age_protected"
        elif grace_blocked:
            out["status"] = "grace_blocked"
    return out


def process_pending_commands(engines: list[Any], state_dirs: list[Path]) -> int:
    """Top-level helper called from the runner main loop.

    For each (engine, state_dir) pair, reads pending commands and applies
    them. Returns total number of commands processed.
    """
    total = 0
    for engine, state_dir in zip(engines, state_dirs):
        commands = read_pending_commands(state_dir)
        if not commands:
            continue
        for cmd in commands:
            try:
                outcome = apply_command_to_engine(engine, cmd)
            except Exception as e:
                logger.exception("Command dispatch failed: %s", cmd)
                outcome = {"status": "error", "reason": str(e)}
            try:
                append_processed(state_dir, cmd, outcome)
            except Exception:
                logger.exception("Failed to write processed log")
            total += 1
            logger.info("Processed command: %s → %s", cmd, outcome.get("status"))
    return total
