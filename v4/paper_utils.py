"""V4 Paper Trading — shared utilities.

Contains state restoration, PID locking, and sleep computation
used by the multi-portfolio runner.
"""
from __future__ import annotations

import fcntl
import json
import logging
import os

from v4.paper_engine import PaperPortfolioEngine
from v4.paper_state import deserialize_state, deserialize_engine_state, truncate_after_tick

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# State restoration (AC14)
# ---------------------------------------------------------------------------

def restore_state(engine: PaperPortfolioEngine, config) -> None:
    """Restore engine state from state.json if it exists.

    Restores: tick_counter, SimulationState fields, last_timestamp,
    shadow pools, last_known_prices.
    Also triggers catch-up if last_timestamp indicates missed bars.
    """
    state_dir = config.state_dir
    state_path = os.path.join(state_dir, "state.json")

    if not os.path.exists(state_path):
        return

    with open(state_path) as f:
        data = json.load(f)

    # R7-C1 fix: validate mode consistency between state file and config
    state_is_independent = "strategy_states" in data
    config_is_independent = config.mode == "independent"
    if state_is_independent != config_is_independent:
        state_mode = "independent" if state_is_independent else "pool"
        config_mode = config.mode
        raise ValueError(
            f"Mode mismatch: state.json is '{state_mode}' mode but config is "
            f"'{config_mode}' mode. Cannot restore state across mode changes. "
            f"Either revert the config mode, or delete {state_path} to start fresh "
            f"(existing positions will be lost)."
        )

    # Detect mode: independent mode has "strategy_states" key
    if state_is_independent:
        # Independent mode (QM-C1 fix)
        strategy_states, tick_counter, last_timestamp = deserialize_engine_state(data)
        engine.tick_counter = tick_counter
        # Do NOT set engine.state — independent mode uses engine.state = None
        engine.last_timestamp = last_timestamp

        # R3-I2 fix: reconcile strategy IDs against current config
        config_sids = {s.strategy_id for s in config.strategies}
        restored_sids = set(strategy_states.keys())

        # R4-I2 + R5-C1 fix: force-close orphaned strategy positions,
        # persist trade records, and redistribute equity to active strategies.
        from v4.paper_state import _closed_trade_to_dict
        last_prices = data.get("last_known_prices", {})
        orphan_equity_total = 0.0
        for orphan in restored_sids - config_sids:
            orphan_state = strategy_states[orphan]
            n_orphan_pos = orphan_state.position_manager.total_open()
            if n_orphan_pos > 0:
                # Close all positions at last known prices
                for pos in list(orphan_state.position_manager.open_positions):
                    exit_price = last_prices.get(pos.token, pos.entry_price)
                    entry_fee = orphan_state._entry_fees_by_pos.pop(pos.position_id, 0.0)
                    exit_fee = abs(pos.quantity * exit_price) * pos.fee_rate
                    raw_pnl = pos.quantity * (exit_price - pos.entry_price)
                    net_pnl = raw_pnl - exit_fee - pos.cumulative_funding
                    orphan_state.position_manager.close_position(
                        pos, exit_bar=tick_counter, exit_price=exit_price,
                        pnl=net_pnl, funding_cost=pos.cumulative_funding,
                        entry_fee=entry_fee, exit_fee=exit_fee,
                        exit_reason="orphaned_strategy",
                    )
                    orphan_state.realized_pnl += raw_pnl
                    orphan_state.total_fees += exit_fee
                # R5-C1 fix: persist orphan closed trades to trades.jsonl
                trades_path = os.path.join(state_dir, "trades.jsonl")
                with open(trades_path, "a") as f:
                    for trade in orphan_state.position_manager.closed_trades:
                        f.write(json.dumps(_closed_trade_to_dict(trade, tick=tick_counter)) + "\n")
                    f.flush()
                    os.fsync(f.fileno())
                logger.warning(
                    "Strategy %s removed from config — force-closed %d positions "
                    "at last known prices (trades persisted)", orphan, n_orphan_pos,
                )
            else:
                logger.warning(
                    "Strategy %s found in state but not in config — "
                    "removed (no open positions)", orphan,
                )
            # R5-C1 fix: accumulate orphan equity for redistribution
            orphan_equity_total += orphan_state.portfolio_equity
            del strategy_states[orphan]

        # Initialize missing strategies (in config but not in state)
        for spec in config.strategies:
            if spec.strategy_id not in strategy_states:
                from v4.simulator import SimulationState
                capital = config.capital * spec.weight
                strategy_states[spec.strategy_id] = SimulationState(
                    initial_capital=capital,
                )
                logger.info(
                    "Strategy %s added to config — initialized with $%.0f",
                    spec.strategy_id, capital,
                )

        # R5-C1 + R6-C1 fix: redistribute orphan equity (positive OR negative)
        # to active strategies so total capital is preserved across config changes.
        # Dropping negative equity would silently inflate system capital.
        active_sids = [sid for sid in strategy_states if sid in config_sids]
        if orphan_equity_total != 0 and active_sids:
            per_strategy = orphan_equity_total / len(active_sids)
            for sid in active_sids:
                strategy_states[sid].realized_pnl += per_strategy
            action = "Redistributed" if orphan_equity_total > 0 else "Absorbed"
            logger.warning(
                "%s $%.2f orphan equity across %d active strategies "
                "($%.2f each)", action, orphan_equity_total, len(active_sids), per_strategy,
            )

        engine.strategy_states = strategy_states

        # R6-I1 fix: persist updated state.json after orphan close + redistribution
        # so repeated crash-restart cycles don't re-close orphans and duplicate trades
        if restored_sids - config_sids:
            from v4.paper_state import serialize_engine_state
            state_path_out = os.path.join(state_dir, "state.json")
            updated_data = serialize_engine_state(
                dict(strategy_states), tick_counter, last_timestamp,
                mode="independent",
                shadow_pools=data.get("shadow_pools"),
                last_known_prices=data.get("last_known_prices"),
            )
            import tempfile as _tmpfile
            fd, tmp = _tmpfile.mkstemp(dir=state_dir, suffix=".tmp")
            os.close(fd)
            with open(tmp, "w") as f:
                json.dump(updated_data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.rename(tmp, state_path_out)
            logger.info("State.json updated after strategy reconciliation")
    else:
        # Pool mode
        state, tick_counter = deserialize_state(data)
        engine.tick_counter = tick_counter
        engine.state = state
        engine.last_timestamp = data.get("last_timestamp", None)

    engine._last_known_prices = data.get("last_known_prices", {})
    engine._last_known_regimes = data.get("last_known_regimes", {})

    # Restore shadow pools if present
    shadow_pools = data.get("shadow_pools", {})
    if shadow_pools and hasattr(engine, 'shadow') and engine.shadow is not None:
        engine.shadow.spot_funds_shadow = shadow_pools.get("spot_funds", 0.0)
        engine.shadow.perp_funds_shadow = shadow_pools.get("perp_funds", 0.0)
        engine.shadow.spot_deployed = shadow_pools.get("spot_deployed", 0.0)
        engine.shadow.perp_deployed = shadow_pools.get("perp_deployed", 0.0)

    # Recovery truncation: remove entries after restored tick
    trades_path = os.path.join(state_dir, "trades.jsonl")
    equity_path = os.path.join(state_dir, "equity.csv")
    if os.path.exists(trades_path):
        truncate_after_tick(trades_path, max_tick=tick_counter, format="jsonl")
    if os.path.exists(equity_path):
        truncate_after_tick(equity_path, max_tick=tick_counter, format="csv")

    # Seed _flushed_position_ids from existing trades.jsonl to prevent
    # duplicate writes on restart (closed_trades in restored state would
    # otherwise be re-written since the in-memory set starts empty)
    flushed_ids = set()
    if os.path.exists(trades_path):
        with open(trades_path) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        t = json.loads(line)
                        pid = t.get("position_id", "")
                        if pid:
                            flushed_ids.add(pid)
                    except (json.JSONDecodeError, KeyError):
                        pass
    engine._flushed_position_ids = flushed_ids

    # Log gap for missed bars (C3 fix: do NOT increment tick_counter
    # without processing data — let the next real tick naturally pick up
    # the latest data from the parquet cache)
    if engine.last_timestamp:
        import datetime
        try:
            ts_str = engine.last_timestamp.rstrip("Z")
            # Handle both with and without microseconds
            if "." in ts_str:
                last_ts_dt = datetime.datetime.fromisoformat(ts_str)
            else:
                last_ts_dt = datetime.datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%S")
            now = datetime.datetime.utcnow()
            gap_hours = (now - last_ts_dt).total_seconds() / 3600.0
            if 1.5 < gap_hours < 720:  # Between 1.5 hours and 30 days
                n_missed = int(gap_hours)
                logger.info(
                    "Detected %d missed bars since %s — next tick will fetch latest data",
                    n_missed, engine.last_timestamp,
                )
        except (ValueError, TypeError):
            pass


# ---------------------------------------------------------------------------
# PID locking (AC15)
# ---------------------------------------------------------------------------

def acquire_pid_lock(state_dir: str):
    """Acquire an exclusive PID lock file using fcntl.flock.

    Returns the open file handle (caller must keep it open for lock duration).
    Raises SystemExit if another instance holds the lock.
    """
    os.makedirs(state_dir, exist_ok=True)
    pid_path = os.path.join(state_dir, "paper.pid")
    lock_file = open(pid_path, "w")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (IOError, OSError):
        lock_file.close()
        raise SystemExit(
            f"Another instance is already running (lock held on {pid_path})"
        )
    lock_file.write(str(os.getpid()))
    lock_file.flush()
    return lock_file


# ---------------------------------------------------------------------------
# Sleep computation (AC19)
# ---------------------------------------------------------------------------

def compute_sleep_until_next_hour(now: float) -> float:
    """Compute seconds to sleep until next hour boundary + 5s buffer."""
    next_hour = ((int(now) // 3600) + 1) * 3600
    return next_hour - now + 5.0


# ---------------------------------------------------------------------------
# Graceful shutdown (AC20)
# ---------------------------------------------------------------------------

def create_shutdown_handler(shutdown_event):
    """Create a SIGINT/SIGTERM handler that sets the shutdown event."""
    import threading
    def handler(signum, frame):
        shutdown_event.set()
    return handler
