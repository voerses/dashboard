#!/usr/bin/env python3
"""Replay paper trading from exit ablation deployment (2026-03-15 20:29 UTC).

Reconstructs state at the ablation tick, patches trail_mult=1.5 on all positions,
then replays tick-by-tick using historical parquet data — NO forward bias.

Usage:
    python tools/replay_from_ablation.py [--dry-run] [--pool POOL_NAME]
"""
from __future__ import annotations

import argparse
import csv
import json
import glob
import os
import shutil
import sys
import time

from datetime import datetime, timedelta

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from v4.config import PortfolioConfig, StrategySpec
from v4.position import Position, PositionManager
from v4.simulator import SimulationState
from v4.paper_state import (
    _serialize_position, _deserialize_position,
    _closed_trade_to_dict, serialize_state,
)
import v4.simulator as _sim
from v4.signals import precompute_strategy_signals, discover_tokens


ABLATION_TIME = "2026-03-15T20"  # Hour of ablation deployment
NEW_TRAIL_MULT = 1.5


def load_multi_config(config_path: str) -> list[dict]:
    """Load the multi paper trading config."""
    with open(config_path) as f:
        return json.load(f)["portfolios"]


def find_ablation_tick(equity_file: str) -> tuple[int, str] | None:
    """Find the tick_counter and timestamp at the ablation time."""
    if not os.path.exists(equity_file):
        return None
    with open(equity_file) as f:
        reader = csv.DictReader(f)
        for row in reader:
            ts = row.get("timestamp", "")
            if ABLATION_TIME in ts:
                return int(row["tick"]), ts
    return None


def load_backup_state(state_dir: str) -> dict | None:
    """Load the backup state.json (pre-trail-patch)."""
    backups = sorted(glob.glob(os.path.join(state_dir, "state.json.backup_*")))
    if backups:
        with open(backups[0]) as f:
            return json.load(f)
    # Fall back to current state
    state_path = os.path.join(state_dir, "state.json")
    if os.path.exists(state_path):
        with open(state_path) as f:
            return json.load(f)
    return None


def load_trades(state_dir: str) -> list[dict]:
    """Load all trades from trades.jsonl."""
    trades_path = os.path.join(state_dir, "trades.jsonl")
    trades = []
    if os.path.exists(trades_path):
        with open(trades_path) as f:
            for line in f:
                line = line.strip()
                if line:
                    trades.append(json.loads(line))
    return trades


def get_strategy_params(strategy_id: str, existing_positions: list[dict]) -> dict:
    """Get strategy-specific position parameters from an existing position."""
    for p in existing_positions:
        if p["strategy_id"] == strategy_id:
            return {
                "stop_mult": p.get("stop_mult", 2.0),
                "trail_mult": NEW_TRAIL_MULT,  # Always use new value for replay
                "old_trail_mult": p.get("trail_mult", 3.5),  # Original for pre-ablation bars
                "target_mult": p.get("target_mult", 999.0),
                "no_stop_bars": p.get("no_stop_bars", 24),
                "min_hold": p.get("min_hold", 12),
                "max_hold": p.get("max_hold", 336),
                "exit_regimes": p.get("exit_regimes", [4]),
                "convex_exit": p.get("convex_exit", False),
                "rsi_exit_level": p.get("rsi_exit_level", 999.0),
                "trail_schedule": None,
                "time_trail_schedule": p.get("time_trail_schedule"),
                "funding_exit_threshold": p.get("funding_exit_threshold", 0.0),
                "partial_tp_atr": p.get("partial_tp_atr", 0.0),
                "partial_tp_pct": p.get("partial_tp_pct", 0.5),
                "partial_tp_trail": p.get("partial_tp_trail", 1.5),
                "breakeven_atr": p.get("breakeven_atr", 0.5),
                "chandelier_lookback": p.get("chandelier_lookback", 0),
            }
    # Fallback defaults
    return {
        "stop_mult": 2.0, "trail_mult": NEW_TRAIL_MULT, "old_trail_mult": 3.5,
        "target_mult": 999.0, "no_stop_bars": 24, "min_hold": 12, "max_hold": 336,
        "exit_regimes": [4], "convex_exit": False, "rsi_exit_level": 999.0,
        "trail_schedule": None, "time_trail_schedule": None,
        "funding_exit_threshold": 0.0, "partial_tp_atr": 0.0,
        "partial_tp_pct": 0.5, "partial_tp_trail": 1.5,
        "breakeven_atr": 0.5, "chandelier_lookback": 0,
    }


def compute_position_state_at_bar(
    token: str,
    entry_bar_local: int,
    ablation_bar_local: int,
    entry_price: float,
    direction: int,
    stop_mult: float,
    no_stop_bars: int,
    breakeven_atr: float,
    sig,
    old_trail_mult: float = 3.5,
    partial_tp_bar_local: int = -1,
    partial_tp_trail: float = 1.5,
) -> dict:
    """Compute highest, lowest, stop_price, breakeven_triggered at ablation bar.

    Walks bar-by-bar from entry to ablation, updating stops exactly like the simulator.
    Uses the ORIGINAL trail_mult for pre-ablation bars to avoid forward bias.
    If a partial TP occurred at partial_tp_bar_local, switches trail to partial_tp_trail
    from that bar onward (matching simulator behavior: pos.trail_mult = pos.partial_tp_trail).
    """
    # Initialize highest/lowest to entry bar's high/low — matching simulator (lines 816-817).
    # Using entry_price would miss the entry bar's range, understating trailing stops.
    if entry_bar_local < sig.n_bars and sig.high is not None:
        highest = float(sig.high[entry_bar_local])
        lowest = float(sig.low[entry_bar_local])
    else:
        highest = entry_price
        lowest = entry_price

    # Initial risk from ATR at entry
    entry_atr = float(sig.atr[entry_bar_local]) if entry_bar_local < len(sig.atr) else entry_price * 0.02
    if np.isnan(entry_atr):
        entry_atr = entry_price * 0.02
    initial_risk = stop_mult * entry_atr

    # Initial stop
    if direction == 1:
        stop_price = entry_price - initial_risk
    else:
        stop_price = entry_price + initial_risk

    breakeven_triggered = False

    # Walk through bars from entry to ablation
    for bar in range(entry_bar_local + 1, min(ablation_bar_local + 1, sig.n_bars)):
        # Get bar data
        h = float(sig.high[bar]) if sig.high is not None else entry_price
        l = float(sig.low[bar]) if sig.low is not None else entry_price
        atr = float(sig.atr[bar]) if bar < len(sig.atr) else entry_atr
        if np.isnan(atr):
            atr = entry_price * 0.02

        # Update highest/lowest
        if direction == 1:
            highest = max(highest, h)
        else:
            lowest = min(lowest, l)

        bars_held = bar - entry_bar_local

        # Breakeven ratchet
        if breakeven_atr > 0 and not breakeven_triggered:
            if direction == 1:
                be_profit = (highest - entry_price) / max(atr, 1e-10)
            else:
                be_profit = (entry_price - lowest) / max(atr, 1e-10)
            if be_profit >= breakeven_atr:
                if direction == 1:
                    stop_price = max(stop_price, entry_price)
                else:
                    stop_price = min(stop_price, entry_price)
                breakeven_triggered = True

        # Trailing stop — use OLD trail_mult for pre-ablation bars (no forward bias).
        # NOTE: trail_schedule and time_trail_schedule are intentionally omitted here.
        # All active V4 strategies have trail_schedule=None and time_trail_schedule=None
        # after the exit ablation, so the pre-ablation walk needs only the flat trail_mult.
        # After a partial TP, trail tightens to partial_tp_trail (matches simulator line 288).
        if bars_held >= no_stop_bars:
            effective_trail = old_trail_mult
            if partial_tp_bar_local >= 0 and bar >= partial_tp_bar_local:
                effective_trail = partial_tp_trail
            if direction == 1:
                trail = highest - effective_trail * atr
                stop_price = max(stop_price, trail)
            else:
                trail = lowest + effective_trail * atr
                stop_price = min(stop_price, trail)

    return {
        "highest": highest,
        "lowest": lowest,
        "stop_price": stop_price,
        "initial_risk": initial_risk,
        "breakeven_triggered": breakeven_triggered,
    }


def reconstruct_position_from_trade(
    trade: dict,
    strategy_params: dict,
    sig,
    entry_bar_local: int,
    ablation_bar_local: int,
    partial_tp_bar_local: int = -1,
    margin_inflate_factor: float = 1.0,
    estimated_pre_abl_funding: float = 0.0,
) -> dict | None:
    """Reconstruct a Position dict for a trade that was closed post-ablation.

    Uses historical price data to compute the correct state at ablation time
    with the new trail_mult.  If partial_tp_bar_local >= 0, the position had
    a pre-ablation partial TP at that local bar — trail tightens from that bar
    onward and partial_closed is set True.

    margin_inflate_factor: if the close trade had reduced margin from a
    post-ablation partial TP, inflate back by 1/(1-partial_tp_pct) so the
    replay position starts at the original full size.

    estimated_pre_abl_funding: estimated cumulative_funding from entry to
    ablation, so the position's funding exit threshold works correctly.
    """
    if sig is None or entry_bar_local < 0 or ablation_bar_local < 0:
        return None

    entry_price = trade["entry_price"]
    direction = trade["direction"]
    stop_mult = strategy_params["stop_mult"]

    # Compute intermediate state at ablation bar using OLD trail_mult (no forward bias)
    old_trail = strategy_params.get("old_trail_mult", strategy_params.get("trail_mult", 3.5))
    state_at_ablation = compute_position_state_at_bar(
        trade["token"], entry_bar_local, ablation_bar_local,
        entry_price, direction, stop_mult,
        strategy_params["no_stop_bars"],
        strategy_params["breakeven_atr"],
        sig,
        old_trail_mult=old_trail,
        partial_tp_bar_local=partial_tp_bar_local,
        partial_tp_trail=strategy_params["partial_tp_trail"],
    )

    fee_rate = 0.0005  # default
    had_pre_abl_partial_tp = partial_tp_bar_local >= 0

    # W6 fix: Get leverage from signal data at entry bar (matches simulator line 842).
    # Hardcoding 1.0 would understate quantity for leveraged strategies (e.g. s56 @ 5x).
    leverage = 1.0
    if hasattr(sig, 'leverage') and sig.leverage is not None and entry_bar_local < len(sig.leverage):
        lev = float(sig.leverage[entry_bar_local])
        if lev > 0:
            leverage = lev

    # Inflate margin/quantity if post-ablation partial TP reduced them (CRITICAL-1 fix)
    margin_usd = trade["margin_usd"] * margin_inflate_factor
    # Quantity = margin * leverage / price * direction (matches simulator line 824)
    raw_qty = trade.get("quantity", margin_usd * leverage / max(entry_price, 1e-10) * direction)
    quantity = raw_qty * margin_inflate_factor

    return {
        "position_id": trade["position_id"],
        "token": trade["token"],
        "strategy_id": trade["strategy_id"],
        "leg": trade.get("leg", "primary"),
        "entry_bar": trade["entry_bar"],
        "entry_price": entry_price,
        "direction": direction,
        "quantity": quantity,
        "margin_usd": margin_usd,
        "leverage": leverage,
        "is_perp": trade.get("is_perp", True),
        "fee_rate": fee_rate,
        "stop_mult": stop_mult,
        "trail_mult": NEW_TRAIL_MULT,
        "target_mult": strategy_params["target_mult"],
        "no_stop_bars": strategy_params["no_stop_bars"],
        "min_hold": strategy_params["min_hold"],
        "max_hold": strategy_params["max_hold"],
        "exit_regimes": strategy_params["exit_regimes"],
        "convex_exit": strategy_params["convex_exit"],
        "rsi_exit_level": strategy_params["rsi_exit_level"],
        "trail_schedule": None,
        "time_trail_schedule": strategy_params["time_trail_schedule"],
        "max_trail_mult_arr": None,
        "funding_exit_threshold": strategy_params["funding_exit_threshold"],
        "partial_tp_atr": strategy_params["partial_tp_atr"],
        "partial_tp_pct": strategy_params["partial_tp_pct"],
        "partial_tp_trail": strategy_params["partial_tp_trail"],
        "breakeven_atr": strategy_params["breakeven_atr"],
        "breakeven_triggered": state_at_ablation["breakeven_triggered"],
        "chandelier_lookback": strategy_params["chandelier_lookback"],
        "partial_closed": had_pre_abl_partial_tp,
        "stop_price": state_at_ablation["stop_price"],
        "highest": state_at_ablation["highest"],
        "lowest": state_at_ablation["lowest"],
        "initial_risk": state_at_ablation["initial_risk"],
        "cumulative_funding": estimated_pre_abl_funding,  # W2 fix: pre-ablation funding estimate
        "linked_position_id": None,
        "entry_timestamp": trade.get("entry_timestamp", ""),
    }


def build_replay_bar_maps(
    all_signals: dict,
    tick_counter: int,
    replay_offset: int,
    ablation_tick: int = 0,
) -> dict[str, np.ndarray]:
    """Build bar_maps for a replay tick.

    Maps tick_counter to the correct historical bar index:
    local_bar = n_bars - 1 - replay_offset

    Fills all slots from ablation_tick to tick_counter so the simulator's
    data_end fallback search can find valid bars (not just 2 slots).
    """
    bar_maps = {}
    for sid, token_sigs in all_signals.items():
        for token, sig in token_sigs.items():
            if token in bar_maps:
                continue
            n_bars = sig.n_bars
            local_bar = n_bars - 1 - replay_offset
            if local_bar < 0:
                continue

            size = tick_counter + 1
            bm = np.full(size, -1, dtype=np.int32)

            # Fill from ablation_tick to tick_counter — covers any backward
            # search the simulator may do for data_end fallback (line 331).
            start_tick = max(0, ablation_tick)
            for t in range(start_tick, tick_counter + 1):
                offset_from_current = tick_counter - t
                lb = local_bar - offset_from_current
                if 0 <= lb < n_bars:
                    bm[t] = lb

            bar_maps[token] = bm

    return bar_maps


def replay_pool(pool_config: dict, dry_run: bool = False) -> dict:
    """Replay a single pool from the ablation tick."""
    state_dir = pool_config["state_dir"]
    pool_name = pool_config["pool_name"]
    strategies = pool_config["strategies"]
    capital = pool_config.get("initial_capital", 200000.0)

    print(f"\n{'='*60}")
    print(f"REPLAYING: {pool_name} ({state_dir})")
    print(f"{'='*60}")

    # 1. Find ablation tick
    equity_file = os.path.join(state_dir, "equity.csv")
    result = find_ablation_tick(equity_file)
    if result is None:
        print(f"  SKIP: No ablation tick found")
        return {"status": "skipped", "pool": pool_name}
    ablation_tick, ablation_ts = result

    # 2. Load backup state and trades
    backup_state = load_backup_state(state_dir)
    if backup_state is None:
        print(f"  SKIP: No state file found")
        return {"status": "skipped", "pool": pool_name}

    current_tick = backup_state["tick_counter"]
    ticks_to_replay = current_tick - ablation_tick
    print(f"  Ablation tick: {ablation_tick} ({ablation_ts})")
    print(f"  Current tick:  {current_tick}")
    print(f"  Ticks to replay: {ticks_to_replay}")

    trades = load_trades(state_dir)

    # 3. Separate pre/post ablation trades
    pre_ablation_trades = [t for t in trades if t.get("tick", 0) <= ablation_tick]
    post_ablation_trades = [t for t in trades if t.get("tick", 0) > ablation_tick]
    print(f"  Pre-ablation trades: {len(pre_ablation_trades)}")
    print(f"  Post-ablation trades: {len(post_ablation_trades)}")

    # 4. Build PortfolioConfig for signal computation
    strategy_specs = [StrategySpec.from_dict(s) for s in strategies]

    config = PortfolioConfig(
        capital=capital,
        strategies=strategy_specs,
        max_portfolio_positions=pool_config.get("max_portfolio_positions", 40),
    )

    # 5. Compute signals (once — covers full parquet history)
    print(f"  Computing signals...")
    all_signals = {}
    for spec in strategy_specs:
        tokens = discover_tokens(spec.market)
        sigs = precompute_strategy_signals(spec, tokens, config, 6)
        all_signals[spec.strategy_id] = sigs

    # 7. Reconstruct state at ablation tick
    print(f"  Reconstructing state at ablation tick...")

    # Start from backup state
    all_positions = backup_state.get("open_positions", [])

    # Get positions that were open at ablation time (still in backup state)
    positions_at_ablation = [p for p in all_positions if p["entry_bar"] <= ablation_tick]
    positions_after_ablation = [p for p in all_positions if p["entry_bar"] > ablation_tick]

    print(f"  Positions at ablation: {len(positions_at_ablation)} open, "
          f"{len(positions_after_ablation)} entered after")

    # Re-add positions from post-ablation trades that existed at ablation time
    # Filter out :partial trades — they represent partial take-profits, not full positions.
    # Including them would create ghost positions with reduced margin/quantity.
    positions_to_reconstruct = [
        t for t in post_ablation_trades
        if t["entry_bar"] <= ablation_tick  # entered before ablation
        and not t["position_id"].endswith(":partial")  # skip partial TP trades
    ]

    print(f"  Positions to reconstruct from closed trades: {len(positions_to_reconstruct)}")

    # Build pre-ablation partial TP index: base_position_id → tick of partial TP.
    # Used to (a) set partial_closed=True on reconstructed positions, and
    # (b) tell compute_position_state_at_bar where trail tightened (W1+W2+W3).
    pre_abl_partial_tps = {}
    for t in pre_ablation_trades:
        pid = t.get("position_id", "")
        if pid.endswith(":partial"):
            base_pid = pid.rsplit(":partial", 1)[0]
            pre_abl_partial_tps[base_pid] = t.get("tick", t.get("exit_bar", ablation_tick))

    # Build post-ablation partial TP index: base_position_id → partial trade dict.
    # When a position had a post-ablation partial TP then a full close, the full
    # close trade has reduced margin_usd/quantity. We need to inflate back to the
    # original size so the replay can re-trigger the partial TP naturally (CRITICAL-1).
    post_abl_partial_tps = {}
    for t in post_ablation_trades:
        pid = t.get("position_id", "")
        if pid.endswith(":partial"):
            base_pid = pid.rsplit(":partial", 1)[0]
            post_abl_partial_tps[base_pid] = t

    # Reconstruct closed positions
    reconstructed = []
    for trade in positions_to_reconstruct:
        token = trade["token"]
        sid = trade["strategy_id"]

        # Find signal data for this token
        sig = all_signals.get(sid, {}).get(token)
        if sig is None:
            # Try other strategies
            for other_sid, other_sigs in all_signals.items():
                if token in other_sigs:
                    sig = other_sigs[token]
                    break

        if sig is None:
            print(f"    WARN: No signal for {token} ({sid}), skipping reconstruction")
            continue

        # Compute local bar indices
        # The parquet has n_bars total. The latest bar corresponds to current_tick.
        # So ablation_tick corresponds to local bar: n_bars - 1 - (current_tick - ablation_tick)
        n = sig.n_bars
        ablation_bar_local = n - 1 - ticks_to_replay
        entry_bar_local = ablation_bar_local - (ablation_tick - trade["entry_bar"])

        if entry_bar_local < 0:
            print(f"    WARN: {token} entry_bar_local={entry_bar_local} < 0, skipping")
            continue

        params = get_strategy_params(sid, all_positions)

        # Check if this position had a pre-ablation partial TP (W1+W2+W3 fix)
        base_pid = trade["position_id"]
        partial_tp_tick = pre_abl_partial_tps.get(base_pid, -1)
        partial_tp_bar_local_val = -1
        if partial_tp_tick >= 0:
            partial_tp_bar_local_val = ablation_bar_local - (ablation_tick - partial_tp_tick)

        # Check if this position had a post-ablation partial TP (CRITICAL-1 fix).
        # The close trade's margin_usd is reduced by partial_tp_pct. Inflate back.
        inflate = 1.0
        post_partial = post_abl_partial_tps.get(base_pid)
        if post_partial is not None:
            pct = params.get("partial_tp_pct", 0.5)
            inflate = 1.0 / (1.0 - pct) if pct < 1.0 else 1.0

        # Estimate pre-ablation cumulative_funding (W2 fix) using pro-rata from
        # the close trade's funding_cost (which is lifetime funding).
        est_pre_abl_funding = 0.0
        fc = trade.get("funding_cost", 0.0)
        if fc != 0.0:
            t_total = trade.get("exit_bar", current_tick) - trade["entry_bar"]
            t_pre_abl = ablation_tick - trade["entry_bar"]
            if t_total > 0:
                est_pre_abl_funding = fc * (t_pre_abl / t_total)

        pos_dict = reconstruct_position_from_trade(
            trade, params, sig, entry_bar_local, ablation_bar_local,
            partial_tp_bar_local=partial_tp_bar_local_val,
            margin_inflate_factor=inflate,
            estimated_pre_abl_funding=est_pre_abl_funding,
        )

        if pos_dict:
            reconstructed.append(pos_dict)

    print(f"  Successfully reconstructed: {len(reconstructed)}")

    # Patch trail params on existing positions that were open at ablation
    patched_existing = []
    for p in positions_at_ablation:
        token = p["token"]
        sid = p["strategy_id"]
        sig = all_signals.get(sid, {}).get(token)
        if sig is None:
            for other_sid, other_sigs in all_signals.items():
                if token in other_sigs:
                    sig = other_sigs[token]
                    break

        n = sig.n_bars if sig else 0
        ablation_bar_local = n - 1 - ticks_to_replay if sig else -1
        entry_bar_local = ablation_bar_local - (ablation_tick - p["entry_bar"]) if sig else -1

        p_copy = dict(p)
        p_copy["_ablation_tick"] = ablation_tick

        # Recompute state from entry to ablation using OLD trail_mult (no forward bias)
        old_trail = p.get("trail_mult", 3.5)  # The backup has the original trail_mult

        # Check if this position had a pre-ablation partial TP (W3 fix)
        pp_tick = pre_abl_partial_tps.get(p["position_id"], -1)
        pp_bar_local = ablation_bar_local - (ablation_tick - pp_tick) if pp_tick >= 0 else -1

        if sig and entry_bar_local >= 0:
            state_at_abl = compute_position_state_at_bar(
                token, entry_bar_local, ablation_bar_local,
                p["entry_price"], p["direction"],
                p["stop_mult"], p["no_stop_bars"],
                p.get("breakeven_atr", 0.5), sig,
                old_trail_mult=old_trail,
                partial_tp_bar_local=pp_bar_local,
                partial_tp_trail=p.get("partial_tp_trail", 1.5),
            )
            p_copy["highest"] = state_at_abl["highest"]
            p_copy["lowest"] = state_at_abl["lowest"]
            p_copy["stop_price"] = state_at_abl["stop_price"]
            p_copy["initial_risk"] = state_at_abl["initial_risk"]
            p_copy["breakeven_triggered"] = state_at_abl["breakeven_triggered"]

        p_copy["trail_mult"] = NEW_TRAIL_MULT
        p_copy["trail_schedule"] = None

        # Set cumulative_funding to estimated pre-ablation portion.
        # The backup has lifetime funding (entry→backup). We want entry→ablation only.
        cf = p.get("cumulative_funding", 0.0)
        total_bars = current_tick - p["entry_bar"]
        pre_abl_bars = ablation_tick - p["entry_bar"]
        if total_bars > 0 and cf != 0.0:
            p_copy["cumulative_funding"] = cf * (pre_abl_bars / total_bars)
        else:
            p_copy["cumulative_funding"] = 0.0  # entered at ablation, no pre-abl funding

        patched_existing.append(p_copy)

    # Combine all positions at ablation time
    ablation_positions = patched_existing + reconstructed
    print(f"  Total positions at ablation: {len(ablation_positions)}")

    # --- FIX C1/C2/C3: Use backup state accounting, subtract post-ablation contributions ---
    # The backup state has cumulative realized_pnl/total_fees/total_funding
    # at the current (post-ablation) time. We subtract post-ablation trade
    # contributions to get the ablation-time values.

    backup_realized = backup_state.get("realized_pnl", 0.0)
    backup_fees = backup_state.get("total_fees", 0.0)
    backup_funding = backup_state.get("total_funding", 0.0)

    # Post-ablation trade contributions to subtract from realized_pnl.
    # Normal: realized_pnl += raw_pnl = net_pnl + exit_fee + funding_cost
    # Liquidation: realized_pnl += -max_loss + cumulative_funding (W5 fix)
    #   trade.pnl = -(max_loss + exit_fee) - cumulative_funding
    #   so raw_pnl_contribution = t.pnl + t.exit_fee + 2*t.funding_cost
    post_abl_raw_pnl = 0.0
    for t in post_ablation_trades:
        pnl = t.get("pnl", 0)
        ef = t.get("exit_fee", 0)
        fc = t.get("funding_cost", 0)
        if t.get("exit_reason") == "liquidation":
            # Liquidation: realized_pnl += -max_loss + cumulative_funding
            # = (pnl + exit_fee + funding_cost) + funding_cost
            post_abl_raw_pnl += pnl + ef + 2 * fc
        else:
            post_abl_raw_pnl += pnl + ef + fc
    post_abl_exit_fees = sum(t.get("exit_fee", 0) for t in post_ablation_trades)
    # W1 fix: Pro-rate funding for positions that entered pre-ablation but closed
    # post-ablation. Their trade.funding_cost is lifetime funding — only subtract
    # the post-ablation portion. Positions that entered post-ablation have all
    # funding in the post-ablation window (pro-rate = 1.0).
    post_abl_funding_closed = 0.0
    for t in post_ablation_trades:
        fc = t.get("funding_cost", 0)
        if fc == 0:
            continue
        t_entry = t.get("entry_bar", ablation_tick)
        t_exit = t.get("exit_bar", t.get("tick", current_tick))
        total_bars = t_exit - t_entry
        if t_entry < ablation_tick and total_bars > 0:
            # Position entered pre-ablation: only subtract post-ablation portion
            post_abl_bars = t_exit - ablation_tick
            post_abl_funding_closed += fc * (post_abl_bars / total_bars)
        else:
            # Position entered at or after ablation: all funding is post-ablation
            post_abl_funding_closed += fc

    # Funding accrued on positions still open in backup — we need only the POST-ABLATION
    # portion, not the full lifetime funding. The backup's cumulative_funding includes
    # funding from entry→backup. We estimate post-ablation portion as:
    #   post_abl_bars / total_bars * cumulative_funding
    # (assumes roughly uniform funding rate over position lifetime)
    open_pos_post_abl_funding = 0.0
    for p in positions_at_ablation:
        cf = p.get("cumulative_funding", 0.0)
        total_bars = current_tick - p["entry_bar"]
        post_abl_bars = current_tick - ablation_tick
        if total_bars > 0 and cf != 0.0:
            # Pro-rate: post-ablation fraction of total funding
            open_pos_post_abl_funding += cf * (post_abl_bars / total_bars)
        # If total_bars == 0 (entered at ablation tick), all funding is post-ablation
        elif total_bars == 0:
            open_pos_post_abl_funding += cf
    # Entry fees for positions opened after ablation.
    # Two sources: (a) still-open positions in backup (entry_fees_by_pos map), and
    # (b) positions opened AND closed post-ablation (entry_fee popped from map on close,
    #     so only available from the trade record itself). Missing (b) would overstate
    #     abl_fees, making ablation equity too low (CRITICAL-1 R6 fix).
    entry_fees_map = backup_state.get("entry_fees_by_pos", {})
    # (a) Still-open positions entered after ablation
    post_abl_entry_fees = sum(
        entry_fees_map.get(p["position_id"], 0.0) for p in positions_after_ablation
    )
    # (b) Positions opened AND closed post-ablation (entry_fee no longer in map)
    post_abl_entry_fees += sum(
        t.get("entry_fee", 0.0) for t in post_ablation_trades
        if t.get("entry_bar", 0) > ablation_tick
        and not t["position_id"].endswith(":partial")
    )

    # Reconstruct ablation-time values
    abl_realized = backup_realized - post_abl_raw_pnl
    abl_fees = backup_fees - post_abl_exit_fees - post_abl_entry_fees
    abl_funding = backup_funding - post_abl_funding_closed - open_pos_post_abl_funding

    # Build SimulationState at ablation
    sim_state = SimulationState(initial_capital=capital)
    sim_state.realized_pnl = abl_realized
    sim_state.total_fees = abl_fees
    sim_state.total_funding = abl_funding

    # Add positions
    for p_dict in ablation_positions:
        # Remove internal keys
        clean = {k: v for k, v in p_dict.items() if not k.startswith("_")}
        pos = _deserialize_position(clean)
        sim_state.position_manager.open_position(pos)

    # Carry over entry fees for positions that were open at ablation
    ablation_pos_ids = {p["position_id"] for p in ablation_positions}
    sim_state._entry_fees_by_pos = {
        k: v for k, v in entry_fees_map.items() if k in ablation_pos_ids
    }
    # Add entry fees for reconstructed positions from trades.jsonl
    for trade in positions_to_reconstruct:
        pid = trade["position_id"]
        if pid in ablation_pos_ids and pid not in sim_state._entry_fees_by_pos:
            sim_state._entry_fees_by_pos[pid] = trade.get("entry_fee", 0.0)

    # Validate: equity should approximately match equity.csv at ablation tick
    expected_eq = None
    if os.path.exists(equity_file):
        with open(equity_file) as f:
            reader = csv.DictReader(f)
            for row in reader:
                if int(row.get("tick", 0)) == ablation_tick:
                    expected_eq = float(row.get("portfolio_equity", 0))
                    break
    if expected_eq is not None:
        diff_pct = abs(sim_state.portfolio_equity - expected_eq) / max(expected_eq, 1) * 100
        if diff_pct > 5:
            print(f"  WARNING: Equity mismatch at ablation! "
                  f"Reconstructed=${sim_state.portfolio_equity:,.0f} "
                  f"vs CSV=${expected_eq:,.0f} ({diff_pct:.1f}%)")
        else:
            print(f"  Equity validation: ${sim_state.portfolio_equity:,.0f} "
                  f"vs CSV=${expected_eq:,.0f} ({diff_pct:.1f}% diff) OK")

    print(f"  Ablation state: equity=${sim_state.portfolio_equity:,.0f}, "
          f"realized=${sim_state.realized_pnl:,.0f}, "
          f"{len(sim_state.position_manager.open_positions)} open positions")

    if dry_run:
        print(f"  DRY RUN — skipping replay")
        return {
            "status": "dry_run",
            "pool": pool_name,
            "ablation_tick": ablation_tick,
            "positions_at_ablation": len(ablation_positions),
        }

    # 8. Truncate equity.csv and trades.jsonl to ablation tick
    # Backup originals
    for fname in ["equity.csv", "trades.jsonl", "state.json"]:
        fpath = os.path.join(state_dir, fname)
        if os.path.exists(fpath):
            backup = fpath + ".pre_replay"
            if not os.path.exists(backup):
                shutil.copy2(fpath, backup)

    # Truncate equity.csv
    eq_rows_to_keep = []
    if os.path.exists(equity_file):
        with open(equity_file) as f:
            reader = csv.DictReader(f)
            headers = reader.fieldnames
            for row in reader:
                if int(row.get("tick", 0)) <= ablation_tick:
                    eq_rows_to_keep.append(row)
        with open(equity_file, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
            writer.writerows(eq_rows_to_keep)

    # Truncate trades.jsonl
    trades_path = os.path.join(state_dir, "trades.jsonl")
    with open(trades_path, "w") as f:
        for t in pre_ablation_trades:
            f.write(json.dumps(t) + "\n")

    # 9. REPLAY: Run tick-by-tick from ablation to current
    print(f"  Starting replay ({ticks_to_replay} ticks)...")

    strategy_spec_map = {s.strategy_id: s for s in strategy_specs}
    replay_trades = []
    replay_equity = []
    ablation_dt = datetime(2026, 3, 15, 20, 0, 0)

    for tick_offset in range(ticks_to_replay):
        tick = ablation_tick + tick_offset + 1  # +1 because ablation tick was already processed
        bars_from_end = ticks_to_replay - tick_offset - 1

        # Build bar_maps for this historical tick
        bar_maps = build_replay_bar_maps(
            all_signals, tick, bars_from_end,
            ablation_tick=ablation_tick,
        )

        # Process exits
        _sim._process_exits(sim_state, all_signals, bar_maps, tick, config, strategy_specs=strategy_spec_map)

        # Process entries (FIX C4: use config.seed, not hardcoded 42)
        rng = np.random.RandomState(config.seed + tick)
        _sim._process_entries(
            sim_state, all_signals, strategy_spec_map,
            bar_maps, tick, config, rng,
        )

        new_closed = sim_state.position_manager.closed_trades

        # Record new trades
        newly_closed = [t for t in new_closed if t.exit_bar == tick]
        for ct in newly_closed:
            trade_dict = _closed_trade_to_dict(ct, tick=tick)
            replay_trades.append(trade_dict)

        # Stamp entry_timestamp on new positions
        tick_dt = ablation_dt + timedelta(hours=tick_offset + 1)
        for pos in sim_state.position_manager.open_positions:
            if pos.entry_bar == tick and not pos.entry_timestamp:
                pos.entry_timestamp = tick_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        # Record equity with proper mark-to-market and free_capital for dashboard
        portfolio_eq = sim_state.portfolio_equity
        open_count = len(sim_state.position_manager.open_positions)

        # Compute mark-to-market: portfolio_equity + sum(unrealized PnL)
        unrealized_pnl = 0.0
        total_margin = 0.0
        for pos in sim_state.position_manager.open_positions:
            sig = all_signals.get(pos.strategy_id, {}).get(pos.token)
            if sig is None:
                for other_sid, other_sigs in all_signals.items():
                    if pos.token in other_sigs:
                        sig = other_sigs[pos.token]
                        break
            if sig is not None:
                bm = bar_maps.get(pos.token)
                if bm is not None:
                    lb = int(bm[tick])
                    if 0 <= lb < sig.n_bars:
                        cur_price = float(sig.close[lb])
                        unrealized_pnl += pos.quantity * (cur_price - pos.entry_price)
            total_margin += pos.margin_usd
        mtm = portfolio_eq + unrealized_pnl
        free_cap = portfolio_eq - total_margin

        tick_ts = tick_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        replay_equity.append({
            "tick": tick,
            "timestamp": tick_ts,
            "portfolio_equity": portfolio_eq,
            "mark_to_market_equity": mtm,
            "free_capital": free_cap,
            "open_positions": open_count,
        })

        if newly_closed:
            for ct in newly_closed:
                print(f"    tick={tick}: EXIT {ct.token} ({ct.strategy_id}) "
                      f"reason={ct.exit_reason} pnl=${ct.pnl:,.0f}")

    # 10. Append replay data to files
    # Append trades
    with open(trades_path, "a") as f:
        for t in replay_trades:
            f.write(json.dumps(t) + "\n")

    # Append equity rows (matching existing CSV format:
    # timestamp,tick,portfolio_equity,mark_to_market_equity,free_capital,
    # open_positions,spot_shadow_free,perp_shadow_free,spot_deployed,perp_deployed,imbalance_pct)
    with open(equity_file, "a") as f:
        for eq in replay_equity:
            f.write(
                f"{eq['timestamp']},{eq['tick']},{eq['portfolio_equity']:.2f},"
                f"{eq['mark_to_market_equity']:.2f},{eq['free_capital']:.2f},"
                f"{eq['open_positions']},0.0,0.0,0.0,0.0,0.0\n"
            )

    # 11. Save final state
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    state_path = os.path.join(state_dir, "state.json")

    # Get last known prices/regimes from backup (these are current-time values, which is fine)
    last_prices = backup_state.get("last_known_prices", {})
    last_regimes = backup_state.get("last_known_regimes", {})
    shadow_pools = backup_state.get("shadow_pools", {
        "spot_funds": 0.0, "perp_funds": 0.0,
        "spot_deployed": 0.0, "perp_deployed": 0.0,
    })

    data = serialize_state(
        sim_state, current_tick, timestamp,
        shadow_pools=shadow_pools,
        last_known_prices=last_prices,
        last_known_regimes=last_regimes,
    )
    with open(state_path, "w") as f:
        json.dump(data, f, indent=2)

    final_pos = len(sim_state.position_manager.open_positions)
    final_eq = sim_state.portfolio_equity

    print(f"\n  REPLAY COMPLETE:")
    print(f"    Replay trades: {len(replay_trades)} (exits during replay)")
    print(f"    Final positions: {final_pos}")
    print(f"    Final equity: ${final_eq:,.0f}")

    return {
        "status": "replayed",
        "pool": pool_name,
        "ablation_tick": ablation_tick,
        "ticks_replayed": ticks_to_replay,
        "replay_trades": len(replay_trades),
        "final_positions": final_pos,
        "final_equity": final_eq,
    }


def main():
    parser = argparse.ArgumentParser(description="Replay from exit ablation")
    parser.add_argument("--dry-run", action="store_true", help="Don't actually replay")
    parser.add_argument("--pool", type=str, help="Only replay this pool")
    parser.add_argument("--config", default="configs/multi_v4_paper.json")
    args = parser.parse_args()

    portfolios = load_multi_config(args.config)

    if args.pool:
        portfolios = [p for p in portfolios if p["pool_name"] == args.pool]
        if not portfolios:
            print(f"Pool {args.pool} not found")
            sys.exit(1)

    results = []
    for portfolio in portfolios:
        try:
            result = replay_pool(portfolio, dry_run=args.dry_run)
            results.append(result)
        except Exception as e:
            import traceback
            print(f"\n  ERROR replaying {portfolio['pool_name']}: {e}")
            traceback.print_exc()
            results.append({"status": "error", "pool": portfolio["pool_name"], "error": str(e)})

    print(f"\n{'='*60}")
    print("REPLAY SUMMARY")
    print(f"{'='*60}")
    for r in results:
        if r["status"] == "replayed":
            print(f"  {r['pool']:20s}: {r['ticks_replayed']} ticks, "
                  f"{r['replay_trades']} trades, ${r['final_equity']:,.0f}")
        else:
            print(f"  {r['pool']:20s}: {r['status']}")


if __name__ == "__main__":
    main()
