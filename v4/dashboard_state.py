"""Live dashboard state writer.

Produces /srv/data/state.json every ~1 second from running engines.
Uses engine.to_dashboard_sim() for the heavy lifting, overlays live
WebSocket prices for real-time unrealized P&L, and writes atomically.
"""
from __future__ import annotations

import csv
import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import calendar

if TYPE_CHECKING:
    from v4.paper_config import PaperConfig
    from v4.paper_engine import PaperPortfolioEngine

EXPIRED_WINDOW_S = 86400  # 24 hours


def consolidate_partial_trades(trades: list[dict]) -> list[dict]:
    """Merge partial_tp trades with their remainder into single logical trades.

    Partial TP creates two ClosedTrade records:
      - position_id="X:partial" with exit_reason="partial_tp"
      - position_id="X" with the final exit reason

    This function merges them into one trade with combined margin, PnL, and fees,
    using the remainder's exit price/reason/timestamp. Trades without a partial
    counterpart pass through unchanged.
    """
    # Index partials by their base position_id
    partials: dict[str, dict] = {}
    remainders: dict[str, dict] = {}
    standalone: list[dict] = []

    for t in trades:
        pid = t.get("position_id", "")
        if pid.endswith(":partial"):
            base_id = pid[:-len(":partial")]
            partials[base_id] = t
        else:
            remainders[pid] = t

    result = []
    merged_bases: set[str] = set()

    for base_id, rem in remainders.items():
        partial = partials.get(base_id)
        if partial is not None:
            # Merge: combine margin, pnl, fees; use remainder's exit info
            merged = dict(rem)
            merged["margin_usd"] = float(partial.get("margin_usd", 0)) + float(rem.get("margin_usd", 0))
            merged["pnl"] = float(partial.get("pnl", 0)) + float(rem.get("pnl", 0))
            merged["entry_fee"] = float(partial.get("entry_fee", 0)) + float(rem.get("entry_fee", 0))
            merged["exit_fee"] = float(partial.get("exit_fee", 0)) + float(rem.get("exit_fee", 0))
            merged["funding_cost"] = float(partial.get("funding_cost", 0)) + float(rem.get("funding_cost", 0))
            # Show partial TP in exit reason so it's visible at a glance
            final_reason = rem.get("exit_reason", "")
            merged["exit_reason"] = f"partial_tp + {final_reason}"
            # Detailed legs for click-to-expand view
            merged["had_partial_tp"] = True
            merged["partial_legs"] = [
                _leg_summary(partial),
                _leg_summary(rem),
            ]
            result.append(merged)
            merged_bases.add(base_id)
        else:
            result.append(rem)

    # Add orphan partials (no matching remainder yet — position may still be open)
    for base_id, partial in partials.items():
        if base_id not in merged_bases:
            result.append(partial)

    return result


def _leg_summary(t: dict) -> dict:
    """Extract a compact summary of one leg for partial_legs display."""
    return {
        "exit_reason": t.get("exit_reason", ""),
        "margin_usd": float(t.get("margin_usd", 0)),
        "pnl": float(t.get("pnl", 0)),
        "entry_price": float(t.get("entry_price", 0)),
        "exit_price": float(t.get("exit_price", 0)),
        "hold_bars": t.get("hold_bars", 0),
        "entry_fee": float(t.get("entry_fee", 0)),
        "exit_fee": float(t.get("exit_fee", 0)),
        "funding_cost": float(t.get("funding_cost", 0)),
        "exit_timestamp": t.get("exit_timestamp", ""),
    }


def _parse_ts(s: str) -> float:
    """Parse ISO-UTC timestamp to epoch seconds, returning 0 on failure."""
    try:
        return calendar.timegm(time.strptime(s, "%Y-%m-%dT%H:%M:%SZ"))
    except (ValueError, TypeError):
        return 0.0


def _load_expired_from_log(state_dir: str) -> list[dict]:
    """Read expired events from armed_log.jsonl within the 24h window."""
    log_path = os.path.join(state_dir, "armed_log.jsonl")
    cutoff = time.time() - EXPIRED_WINDOW_S
    expired = []
    seen: set[tuple] = set()
    try:
        with open(log_path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    evt = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if evt.get("event") != "expired":
                    continue
                ts_field = evt.get("expired_at") or evt.get("timestamp", "")
                if _parse_ts(ts_field) <= cutoff:
                    continue
                # Deduplicate by (strategy, token, expired_at)
                key = (evt.get("strategy"), evt.get("token"), ts_field)
                if key in seen:
                    continue
                seen.add(key)
                evt["expired_at"] = ts_field
                expired.append(evt)
    except (FileNotFoundError, OSError):
        pass
    return expired

# Default output path (external HTTP server reads from here)
DEFAULT_STATE_PATH = "/srv/data/state.json"

# Rolling window caps to keep state.json small
EQUITY_HISTORY_CAP = 168


def write_dashboard_state(
    engines: list[PaperPortfolioEngine],
    configs: list[PaperConfig],
    path: str = DEFAULT_STATE_PATH,
    include_sentinel: bool = False,
    runner_status: str = "idle",
    runner_health: dict | None = None,
    include_armed: bool = False,
) -> None:
    """Write a combined dashboard state file for all portfolios.

    Args:
        engines: Running PaperPortfolioEngine instances.
        configs: Corresponding PaperConfig objects.
        path: Output path (default: /srv/data/state.json).
        include_sentinel: If True, include sentinel data from state_dir files.
        runner_status: One of "idle", "fetching", "ticking".
        runner_health: Deprecated, ignored. Kept for API compat.
        include_armed: If True, include armed_orders/expired_orders in portfolio data.
    """
    # Collect live WebSocket prices from any engine that has a PriceMonitor
    # (typically only the first portfolio has one; share prices across all)
    shared_live_prices: dict[str, float] = {}
    for engine in engines:
        if engine._price_monitor is not None:
            try:
                live = engine._price_monitor.get_latest_prices()
                if live:
                    shared_live_prices.update(live)
            except Exception:
                pass

    portfolios = []
    for engine, config in zip(engines, configs):
        sim = _build_portfolio(engine, config, include_sentinel, shared_live_prices,
                               include_armed=include_armed)
        portfolios.append(sim)

    envelope = {
        "version": 1,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "runner_pid": os.getpid(),
        "runner_status": runner_status,
        "portfolios": portfolios,
    }

    _atomic_write_json(envelope, path)


def _build_portfolio(
    engine: PaperPortfolioEngine,
    config: PaperConfig,
    include_sentinel: bool,
    shared_live_prices: dict[str, float] | None = None,
    include_armed: bool = False,
) -> dict:
    """Build a single portfolio dict with live price overlay."""
    # Pass live prices as overrides instead of mutating engine state (thread-safe)
    sim = engine.to_dashboard_sim(price_overrides=shared_live_prices)

    if include_armed:
        # Load expired orders from armed_log.jsonl (same pattern as trades.jsonl)
        sim["expired_orders"] = _load_expired_from_log(config.state_dir)
    else:
        sim.pop("armed_orders", None)
        sim.pop("expired_orders", None)

    # Load full equity history from equity.csv (engine only keeps in-memory
    # entries from current session; equity.csv has the full run history)
    equity_csv = Path(config.state_dir) / "equity.csv"
    if equity_csv.exists():
        csv_history = _load_equity_csv(equity_csv)
        if len(csv_history) > len(sim.get("equity_history", [])):
            sim["equity_history"] = csv_history

    # Apply rolling window caps
    if len(sim.get("equity_history", [])) > EQUITY_HISTORY_CAP:
        sim["equity_history"] = sim["equity_history"][-EQUITY_HISTORY_CAP:]

    all_trades = sim.get("all_trades", [])
    open_trades = [t for t in all_trades if t.get("status") == "open"]
    closed_trades = [t for t in all_trades if t.get("status") != "open"]

    # Load historical closed trades from trades.jsonl (engine only keeps
    # current-session closed trades in memory; trades.jsonl has full history)
    trades_jsonl = Path(config.state_dir) / "trades.jsonl"
    if trades_jsonl.exists():
        jsonl_trades = _load_trades_jsonl(trades_jsonl)
        if len(jsonl_trades) > len(closed_trades):
            # trades.jsonl has more history — use it instead, mapping to
            # the same format that to_dashboard_sim() produces.
            closed_trades = [_jsonl_to_dashboard_trade(t) for t in jsonl_trades]

    sim["all_trades"] = closed_trades + open_trades
    sim["recent_trades"] = closed_trades
    sim["open_positions_list"] = open_trades

    # Sentinel data (only after hourly ticks / SIGUSR1 — heavier reads)
    if include_sentinel:
        _attach_sentinel(sim, config)

    return sim


_equity_csv_cache: dict[str, tuple[float, list[dict]]] = {}  # path -> (mtime, data)
_trades_jsonl_cache: dict[str, tuple[float, list[dict]]] = {}  # path -> (mtime, data)


def _load_trades_jsonl(path: Path) -> list[dict]:
    """Load closed trades from trades.jsonl (cached by mtime)."""
    key = str(path)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return []
    cached = _trades_jsonl_cache.get(key)
    if cached and cached[0] == mtime:
        return cached[1]
    result = []
    try:
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if line:
                    result.append(json.loads(line))
    except (OSError, json.JSONDecodeError):
        pass
    _trades_jsonl_cache[key] = (mtime, result)
    return result


def _jsonl_to_dashboard_trade(t: dict) -> dict:
    """Convert a trades.jsonl entry to the dashboard trade format."""
    d = {
        "token": t.get("token", ""),
        "strategy": t.get("strategy_id", t.get("strategy", "")),
        "market_type": "perp" if t.get("is_perp") else "spot",
        "direction": t.get("direction", 1),
        "pnl": float(t.get("pnl", 0)),
        "exit_reason": t.get("exit_reason", ""),
        "signal": {
            "entry_bar": t.get("entry_bar", 0),
            "exit_bar": t.get("exit_bar", 0),
            "entry_price": float(t.get("entry_price", 0)),
            "exit_price": float(t.get("exit_price", 0)),
            "hold_bars": t.get("hold_bars", 0),
        },
        "entry_price": float(t.get("entry_price", 0)),
        "exit_price": float(t.get("exit_price", 0)),
        "margin_usd": float(t.get("margin_usd", 0)),
        "hold_bars": t.get("hold_bars", 0),
        "funding_cost": float(t.get("funding_cost", 0)),
        "entry_fee": float(t.get("entry_fee", 0)),
        "exit_fee": float(t.get("exit_fee", 0)),
        "entry_timestamp": t.get("entry_timestamp", ""),
        "exit_timestamp": t.get("exit_timestamp", ""),
    }
    # Pass through partial TP metadata from consolidation
    if t.get("had_partial_tp"):
        d["had_partial_tp"] = True
        d["partial_legs"] = t.get("partial_legs", [])
    return d

def _load_equity_csv(path: Path) -> list[dict]:
    """Load equity history from equity.csv (cached by mtime to avoid re-reading every second)."""
    key = str(path)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return []
    cached = _equity_csv_cache.get(key)
    if cached and cached[0] == mtime:
        return cached[1]
    result = []
    try:
        with open(path, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                result.append({
                    "timestamp": row.get("timestamp", ""),
                    "portfolio_equity": float(row.get("portfolio_equity", 0)),
                    "mark_to_market_equity": float(row.get("mark_to_market_equity", 0)),
                })
    except (OSError, ValueError, KeyError):
        pass
    _equity_csv_cache[key] = (mtime, result)
    return result


def _attach_sentinel(sim: dict, config: PaperConfig) -> None:
    """Attach sentinel data from state_dir files."""
    try:
        from tools.generate_dashboard_v2 import (
            parse_sentinel_recent,
            resolve_event_status,
            compute_24h_summary,
            build_strategy_breakdown,
            load_sentinel_shadow_deduped,
            compute_sentinel_reconciliation,
            load_trades_jsonl,
            load_equity_csv,
        )
    except ImportError:
        return

    state_dir = Path(config.state_dir)

    sentinel_events = parse_sentinel_recent(state_dir / "sentinel_recent.json")

    # Reconcile with trades
    trades = load_trades_jsonl(str(state_dir / "trades.jsonl"))
    trades_by_pid = {t.get("position_id", ""): t for t in trades if t.get("position_id")}
    bar_complete = bool(trades)
    for evt in sentinel_events:
        pid = evt.get("position_id", "")
        evt["status"] = resolve_event_status(evt, trades_by_pid.get(pid), bar_complete=bar_complete)

    sim["sentinel_events"] = sentinel_events
    sim["sentinel_summary"] = compute_24h_summary(sentinel_events)
    sim["sentinel_strategy_breakdown"] = build_strategy_breakdown(sentinel_events)

    # Heartbeat + metrics
    for fname, key in [("sentinel_heartbeat.json", "sentinel_heartbeat"),
                       ("sentinel_metrics.json", "sentinel_metrics")]:
        fpath = state_dir / fname
        if fpath.exists():
            try:
                sim[key] = json.loads(fpath.read_text())
            except (json.JSONDecodeError, OSError):
                pass

    # Reconciliation
    shadow_deduped = load_sentinel_shadow_deduped(state_dir / "sentinel_shadow.jsonl")
    equity = load_equity_csv(str(state_dir / "equity.csv"))
    tick_to_ts = {e["tick"]: e["timestamp"] for e in equity}
    open_positions = sim.get("open_positions_list", [])
    # Build open_positions list in state.json format for reconciliation
    state_open = []
    for t in open_positions:
        state_open.append({"position_id": f"{t.get('token','')}:{t.get('strategy','')}:{t.get('entry_bar',0)}:primary"})
    sim["sentinel_reconciliation"] = compute_sentinel_reconciliation(
        shadow_deduped, trades, open_positions=state_open, tick_to_ts=tick_to_ts,
    )


def _json_default(obj):
    """JSON serializer: convert numpy scalars to Python native types."""
    try:
        import numpy as np
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
    except ImportError:
        pass
    return str(obj)


def _atomic_write_json(data: dict, path: str) -> None:
    """Atomic write: tmpfile + fsync + os.rename."""
    target_dir = os.path.dirname(path) or "."
    os.makedirs(target_dir, exist_ok=True)

    json_str = json.dumps(data, separators=(",", ":"), default=_json_default)

    fd, tmp_path = tempfile.mkstemp(dir=target_dir, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(json_str)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp_path, 0o644)
        os.rename(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
