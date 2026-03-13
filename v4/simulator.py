"""V4 Portfolio Backtest — Bar-by-bar portfolio simulation loop.

Processes exits then entries each bar, with portfolio-level constraints.
Faithfully ports ALL v3 JIT exit logic paths.
"""
from __future__ import annotations

import sys
import os
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from .config import PortfolioConfig, StrategySpec
from .position import Position, ClosedTrade, PositionManager
from .signals import TokenSignals
from .sizing import compute_position_size, compute_slippage_bps

# Import fee/MMR lookups from v3
_v3_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "v3")
if _v3_dir not in sys.path:
    sys.path.insert(0, _v3_dir)
from universe import get_fee_rate, get_maint_margin_rate, get_liquidation_fee_rate


@dataclass
class RejectionStats:
    """Track why entries were rejected."""
    portfolio_limit: int = 0
    strategy_limit: int = 0
    min_size: int = 0
    adv_cap: int = 0
    concentration: int = 0
    capital: int = 0
    conviction: int = 0  # entries below min_conviction_threshold

    def total(self) -> int:
        return (self.portfolio_limit + self.strategy_limit + self.min_size +
                self.adv_cap + self.concentration + self.capital + self.conviction)

    def to_dict(self) -> dict:
        return {
            "portfolio_limit": self.portfolio_limit,
            "strategy_limit": self.strategy_limit,
            "min_size": self.min_size,
            "adv_cap": self.adv_cap,
            "concentration": self.concentration,
            "capital": self.capital,
            "conviction": self.conviction,
            "total": self.total(),
        }


@dataclass
class SimulationState:
    """Mutable state for the simulation loop."""
    initial_capital: float
    realized_pnl: float = 0.0
    total_fees: float = 0.0
    total_funding: float = 0.0
    position_manager: PositionManager = field(default_factory=PositionManager)
    rejections: RejectionStats = field(default_factory=RejectionStats)
    partial_fills: int = 0
    equity_snapshots: list = field(default_factory=list)
    _entry_fees_by_pos: dict = field(default_factory=dict)  # position_id -> entry fee

    @property
    def portfolio_equity(self) -> float:
        """Sizing equity = initial + realized - fees - funding. Unrealized excluded."""
        return self.initial_capital + self.realized_pnl - self.total_fees - self.total_funding

    @property
    def free_capital(self) -> float:
        """Capital available for new positions = equity - locked margin."""
        return self.portfolio_equity - self.position_manager.total_locked_margin()


def build_unified_index(
    all_signals: dict[str, dict[str, TokenSignals]],
) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """Build unified hourly DatetimeIndex and bar maps.

    Returns:
        unified_ts: sorted unique timestamps across all tokens
        bar_maps: {token: array mapping global_bar -> local_bar or -1}
    """
    all_ts = set()
    # Collect (token, timestamps) pairs — same token may appear under multiple strategies
    token_timestamps: dict[str, np.ndarray] = {}

    for strategy_id, token_signals in all_signals.items():
        for token, sig in token_signals.items():
            ts = sig.timestamps
            if not isinstance(ts, np.ndarray):
                ts = np.asarray(ts)
            if token not in token_timestamps:
                token_timestamps[token] = ts
            else:
                # Use the longer series if same token appears under multiple strategies
                if len(ts) > len(token_timestamps[token]):
                    token_timestamps[token] = ts
            all_ts.update(ts.tolist())

    unified_ts = np.array(sorted(all_ts), dtype='datetime64[ns]')

    bar_maps: dict[str, np.ndarray] = {}
    for token, token_ts in token_timestamps.items():
        token_ts_ns = np.asarray(token_ts, dtype='datetime64[ns]')
        positions = np.searchsorted(token_ts_ns, unified_ts)
        valid = (positions < len(token_ts_ns)) & (
            token_ts_ns[np.minimum(positions, len(token_ts_ns) - 1)] == unified_ts
        )
        bar_maps[token] = np.where(valid, positions, -1)

    return unified_ts, bar_maps


def _close_position(
    state: SimulationState,
    pos: Position,
    exit_bar: int,
    exit_price: float,
    exit_reason: str,
    exit_adv: float,
    config: PortfolioConfig,
) -> ClosedTrade:
    """Close a position with exit slippage and fee."""
    notional = abs(pos.quantity * exit_price)

    # Exit slippage using point-in-time ADV
    if exit_reason != "liquidation":
        # Apply stress ADV multiplier for stop exits (liquidity dries up during cascades)
        effective_adv = exit_adv
        if exit_reason == "stop":
            effective_adv = exit_adv * config.stress_adv_multiplier
        slip_bps = compute_slippage_bps(notional, effective_adv, config.base_spread_bps, config.impact_coeff, config.max_slip_bps)
        slip = exit_price * slip_bps / 10000.0
        if pos.direction == 1:
            exit_price -= slip
        else:
            exit_price += slip

    # Compute raw PnL
    # Accounting invariant: portfolio_equity = initial + realized_pnl - total_fees - total_funding
    # Entry fees already in total_fees. Exit fee tracked in total_fees only (not realized_pnl)
    # to avoid double-counting.
    if exit_reason == "liquidation":
        mmr = get_maint_margin_rate(config.exchange)
        entry_notional = pos.margin_usd * pos.leverage
        max_loss = pos.margin_usd - entry_notional * mmr
        liq_fee_rate = get_liquidation_fee_rate(config.exchange)
        exit_fee = abs(pos.quantity * exit_price) * liq_fee_rate
        # Funding already deducted bar-by-bar via total_funding; add back to realized_pnl
        # so net effect is: equity -= (max_loss + exit_fee)
        state.realized_pnl += -max_loss + pos.cumulative_funding
        state.total_fees += exit_fee
        # Net trade PnL for reporting: total loss including funding
        net_pnl = -(max_loss + exit_fee) - pos.cumulative_funding
    else:
        if pos.direction == 1:
            pnl = pos.quantity * (exit_price - pos.entry_price)
        else:
            pnl = abs(pos.quantity) * (pos.entry_price - exit_price)
        exit_fee = abs(pos.quantity * exit_price) * pos.fee_rate
        net_pnl = pnl - exit_fee - pos.cumulative_funding
        # realized_pnl gets raw pnl (no exit_fee — that goes to total_fees)
        state.realized_pnl += pnl
        state.total_fees += exit_fee

    entry_fee = state._entry_fees_by_pos.pop(pos.position_id, 0.0)

    return state.position_manager.close_position(
        pos,
        exit_bar=exit_bar,
        exit_price=exit_price,
        pnl=net_pnl,
        funding_cost=pos.cumulative_funding,
        entry_fee=entry_fee,
        exit_fee=exit_fee,
        exit_reason=exit_reason,
    )


def _get_bar_data(sig: TokenSignals, local_bar: int, is_primary: bool = True):
    """Get price/indicator data for a bar, choosing primary or secondary arrays."""
    if not is_primary and sig.perp_close is not None:
        return (
            sig.perp_close[local_bar],
            sig.perp_high[local_bar],
            sig.perp_low[local_bar],
            sig.perp_atr[local_bar],
            sig.perp_rolling_adv[local_bar],
            sig.perp_funding_1h[local_bar] if sig.perp_funding_1h is not None else 0.0,
        )
    return (
        sig.close[local_bar],
        sig.high[local_bar],
        sig.low[local_bar],
        sig.atr[local_bar],
        sig.rolling_adv[local_bar],
        sig.funding_1h[local_bar],
    )


def _partial_close_position(
    state: SimulationState,
    pos: Position,
    global_bar: int,
    exit_price: float,
    exit_adv: float,
    config: PortfolioConfig,
) -> ClosedTrade:
    """Close a fraction of a position (partial profit-taking).

    Books a trade for the closed portion, reduces the remaining position's
    quantity/margin, and tightens the trail. Does NOT remove the position
    from open_positions — the remainder stays open.
    """
    close_pct = pos.partial_tp_pct
    notional_closed = abs(pos.quantity * exit_price) * close_pct

    # Exit slippage on the closed portion
    slip_bps = compute_slippage_bps(
        notional_closed, exit_adv, config.base_spread_bps,
        config.impact_coeff, config.max_slip_bps,
    )
    slip = exit_price * slip_bps / 10000.0
    if pos.direction == 1:
        adj_exit_price = exit_price - slip
    else:
        adj_exit_price = exit_price + slip

    # Compute PnL for closed portion
    closed_qty = pos.quantity * close_pct
    if pos.direction == 1:
        pnl = closed_qty * (adj_exit_price - pos.entry_price)
    else:
        pnl = abs(closed_qty) * (pos.entry_price - adj_exit_price)

    exit_fee = abs(closed_qty * adj_exit_price) * pos.fee_rate
    closed_margin = pos.margin_usd * close_pct
    closed_funding = pos.cumulative_funding * close_pct
    net_pnl = pnl - exit_fee - closed_funding

    # Book accounting
    state.realized_pnl += pnl
    state.total_fees += exit_fee

    # Pro-rate entry fee for the trade record
    full_entry_fee = state._entry_fees_by_pos.get(pos.position_id, 0.0)
    partial_entry_fee = full_entry_fee * close_pct
    state._entry_fees_by_pos[pos.position_id] = full_entry_fee - partial_entry_fee

    # Create trade record for closed portion
    trade = ClosedTrade(
        position_id=pos.position_id + ":partial",
        token=pos.token,
        strategy_id=pos.strategy_id,
        leg=pos.leg,
        entry_bar=pos.entry_bar,
        exit_bar=global_bar,
        entry_price=pos.entry_price,
        exit_price=adj_exit_price,
        direction=pos.direction,
        margin_usd=closed_margin,
        pnl=net_pnl,
        funding_cost=closed_funding,
        entry_fee=partial_entry_fee,
        exit_fee=exit_fee,
        hold_bars=global_bar - pos.entry_bar,
        exit_reason="partial_tp",
        is_perp=pos.is_perp,
        entry_timestamp=pos.entry_timestamp,
    )
    state.position_manager.closed_trades.append(trade)

    # Reduce the remaining position
    pos.quantity = pos.quantity * (1.0 - close_pct)
    pos.margin_usd = pos.margin_usd * (1.0 - close_pct)
    pos.cumulative_funding = pos.cumulative_funding * (1.0 - close_pct)

    # Tighten trail on remainder
    pos.trail_mult = pos.partial_tp_trail
    # Clear trail schedule — remainder uses the fixed tighter trail
    pos.trail_schedule = None
    pos.time_trail_schedule = None

    # Mark as partially closed (one-time only)
    pos.partial_closed = True

    return trade


def _process_exits(
    state: SimulationState,
    all_signals: dict[str, dict[str, TokenSignals]],
    bar_maps: dict[str, np.ndarray],
    global_bar: int,
    config: PortfolioConfig,
):
    """Process all exit conditions for open positions.

    Faithfully ports ALL v3 JIT exit logic paths (engine.py:344-467).
    """
    positions_to_close: list[tuple[Position, float, str, float]] = []  # (pos, exit_price, reason, exit_adv)

    for pos in list(state.position_manager.open_positions):
        # Skip if already marked for closure via linked position
        if any(p is pos for p, _, _, _ in positions_to_close):
            continue

        sig = all_signals[pos.strategy_id].get(pos.token)
        if sig is None:
            continue

        bm = bar_maps.get(pos.token)
        if bm is None:
            continue

        local_bar = int(bm[global_bar])

        # Step 1: Token data ended (or out of bounds for this strategy's signals) -> force close
        if local_bar == -1 or local_bar >= sig.n_bars:
            # Find last valid bar for exit price
            last_valid = -1
            for b in range(global_bar - 1, -1, -1):
                lb = int(bm[b])
                if lb != -1 and lb < sig.n_bars:
                    last_valid = lb
                    break
            if last_valid >= 0:
                is_secondary = (pos.leg == "secondary")
                close_p, _, _, _, adv_p, _ = _get_bar_data(sig, last_valid, not is_secondary)
            else:
                close_p = pos.entry_price
                adv_p = 1_000_000.0
            positions_to_close.append((pos, close_p, "data_end", adv_p))
            # Also close linked position
            if pos.linked_position_id:
                linked = state.position_manager.get_linked(pos.linked_position_id)
                if linked and not any(p is linked for p, _, _, _ in positions_to_close):
                    is_linked_secondary = (linked.leg == "secondary")
                    if last_valid >= 0:
                        lc, _, _, _, la, _ = _get_bar_data(sig, last_valid, not is_linked_secondary)
                    else:
                        lc, la = linked.entry_price, 1_000_000.0
                    positions_to_close.append((linked, lc, "data_end", la))
            continue

        is_secondary = (pos.leg == "secondary")
        close_val, high_val, low_val, atr_val, adv_val, funding_val = _get_bar_data(sig, local_bar, not is_secondary)

        bars_held = global_bar - pos.entry_bar
        d = pos.direction

        # Step 2: Accrue funding (perp only)
        if pos.is_perp:
            notional = abs(pos.quantity * close_val)
            d_sign = 1.0 if pos.quantity > 0.0 else -1.0
            funding_cost = notional * funding_val * d_sign
            state.total_funding += funding_cost
            pos.cumulative_funding += funding_cost

        # Step 3: Liquidation check
        if pos.is_perp and (pos.leverage > 1.0 or pos.quantity < 0.0):
            mmr = get_maint_margin_rate(config.exchange)
            if pos.quantity > 0.0:
                unrealized = pos.quantity * (low_val - pos.entry_price)
            else:
                unrealized = abs(pos.quantity) * (pos.entry_price - high_val)
            entry_notional = pos.margin_usd * pos.leverage
            maintenance_margin = entry_notional * mmr
            if pos.margin_usd + unrealized - pos.cumulative_funding < maintenance_margin:
                positions_to_close.append((pos, close_val, "liquidation", adv_val))
                if pos.linked_position_id:
                    linked = state.position_manager.get_linked(pos.linked_position_id)
                    if linked and not any(p is linked for p, _, _, _ in positions_to_close):
                        is_lk_sec = (linked.leg == "secondary")
                        lc, _, _, _, la, _ = _get_bar_data(sig, local_bar, not is_lk_sec)
                        positions_to_close.append((linked, lc, "linked_exit", la))
                continue

        # Step 4: Update highest/lowest
        if d == 1:
            pos.highest = max(pos.highest, high_val)
        else:
            pos.lowest = min(pos.lowest, low_val)

        # Handle NaN ATR
        cur_atr = atr_val
        if np.isnan(cur_atr):
            cur_atr = abs(pos.entry_price) * 0.02

        # Step 4.5: Breakeven ratchet
        if pos.breakeven_atr > 0.0 and not pos.breakeven_triggered:
            if d == 1:
                be_profit_atr = (pos.highest - pos.entry_price) / max(cur_atr, 1e-10)
            else:
                be_profit_atr = (pos.entry_price - pos.lowest) / max(cur_atr, 1e-10)
            if be_profit_atr >= pos.breakeven_atr:
                # Move stop to entry price (breakeven)
                if d == 1:
                    pos.stop_price = max(pos.stop_price, pos.entry_price)
                else:
                    pos.stop_price = min(pos.stop_price, pos.entry_price)
                pos.breakeven_triggered = True

        # Step 5: Update trailing stop
        if pos.convex_exit:
            if bars_held >= 48 and d == 1:
                trail = pos.highest - 2.0 * cur_atr
                pos.stop_price = max(pos.stop_price, trail)
            elif bars_held >= 12 and d == 1:
                if pos.highest > pos.entry_price + 1.5 * pos.initial_risk:
                    be_trail = pos.entry_price + 0.3 * pos.initial_risk
                    pos.stop_price = max(pos.stop_price, be_trail)
        else:
            if bars_held >= pos.no_stop_bars:
                # Determine effective trail multiplier
                if pos.trail_schedule is not None:
                    profit_atr = abs(close_val - pos.entry_price) / max(cur_atr, 1e-10)
                    eff_tm = pos.trail_mult  # fallback
                    for si in range(pos.trail_schedule.shape[0]):
                        if profit_atr >= pos.trail_schedule[si, 0]:
                            eff_tm = pos.trail_schedule[si, 1]
                        else:
                            break
                else:
                    eff_tm = pos.trail_mult

                # Time-based trail tightening (tighten with elapsed time)
                if pos.time_trail_schedule is not None:
                    time_tm = pos.trail_mult  # fallback
                    for si in range(pos.time_trail_schedule.shape[0]):
                        if bars_held >= pos.time_trail_schedule[si, 0]:
                            time_tm = pos.time_trail_schedule[si, 1]
                        else:
                            break
                    if time_tm < eff_tm:
                        eff_tm = time_tm

                # Per-bar ceiling (defensive stop overlay)
                if pos.max_trail_mult_arr is not None and local_bar < len(pos.max_trail_mult_arr):
                    if pos.max_trail_mult_arr[local_bar] < eff_tm:
                        eff_tm = pos.max_trail_mult_arr[local_bar]

                if d == 1:
                    trail = pos.highest - eff_tm * cur_atr
                    pos.stop_price = max(pos.stop_price, trail)
                else:
                    trail = pos.lowest + eff_tm * cur_atr
                    pos.stop_price = min(pos.stop_price, trail)

        # Step 5.5: Partial profit-taking (before full exit checks)
        if (pos.partial_tp_atr > 0.0
                and not pos.partial_closed
                and bars_held >= pos.no_stop_bars):
            if d == 1:
                profit_atr = (close_val - pos.entry_price) / max(cur_atr, 1e-10)
            else:
                profit_atr = (pos.entry_price - close_val) / max(cur_atr, 1e-10)
            if profit_atr >= pos.partial_tp_atr:
                _partial_close_position(state, pos, global_bar, close_val, adv_val, config)

        # Step 6: Check exit conditions (order matters, matches engine.py:438-467)
        exit_signal = False
        exit_reason = ""
        exit_price = close_val

        stop_active = bars_held >= pos.no_stop_bars or pos.convex_exit

        # 1. Stop-loss (intra-bar)
        if stop_active and d == 1 and low_val <= pos.stop_price:
            exit_signal = True
            exit_reason = "stop"
            exit_price = pos.stop_price
        elif stop_active and d == -1 and high_val >= pos.stop_price:
            exit_signal = True
            exit_reason = "stop"
            exit_price = pos.stop_price

        # 2. Take-profit (regime-conditional: use tighter target in bear)
        if not exit_signal:
            eff_target = pos.target_mult
            if sig.bear_target_mult > 0.0:
                regime_val_tp = int(sig.regime[local_bar])
                if regime_val_tp == 4:  # DOWNTREND
                    eff_target = sig.bear_target_mult
            if pos.convex_exit and d == 1 and close_val > pos.entry_price + eff_target * pos.initial_risk:
                exit_signal = True
                exit_reason = "target"
                exit_price = close_val
            elif not pos.convex_exit and d == 1 and high_val >= pos.entry_price + eff_target * cur_atr:
                exit_signal = True
                exit_reason = "target"
                exit_price = pos.entry_price + eff_target * cur_atr

        # 3. Regime exit
        if not exit_signal:
            regime_val = int(sig.regime[local_bar])
            if regime_val in pos.exit_regimes and bars_held > 6:
                exit_signal = True
                exit_reason = "regime"

        # 4. RSI exit (symmetric: longs exit on high RSI, shorts on low RSI)
        if not exit_signal and pos.rsi_exit_level < 999.0 and sig.rsi is not None:
            rsi_val = sig.rsi[local_bar]
            if bars_held >= pos.min_hold:
                if d == 1 and rsi_val > pos.rsi_exit_level:
                    exit_signal = True
                    exit_reason = "rsi"
                elif d == -1 and rsi_val < (100.0 - pos.rsi_exit_level):
                    exit_signal = True
                    exit_reason = "rsi"

        # 5. Mean-target exit (convex only)
        if not exit_signal and pos.convex_exit and sig.mean_target_vals is not None:
            mt = sig.mean_target_vals[local_bar]
            if not np.isnan(mt) and d == 1 and close_val >= mt and bars_held >= pos.min_hold:
                if close_val < pos.entry_price + 2.0 * pos.initial_risk:
                    exit_signal = True
                    exit_reason = "mean_target"

        # 6. Max hold
        if not exit_signal and bars_held >= pos.max_hold:
            exit_signal = True
            exit_reason = "max_hold"

        # 7. Funding ceiling (perp only — exit if cumulative funding drag exceeds threshold)
        if not exit_signal and pos.funding_exit_threshold > 0.0 and pos.is_perp:
            if pos.margin_usd > 0.0 and pos.cumulative_funding / pos.margin_usd > pos.funding_exit_threshold:
                exit_signal = True
                exit_reason = "funding"

        if exit_signal:
            positions_to_close.append((pos, exit_price, exit_reason, adv_val))
            # Close linked position too
            if pos.linked_position_id:
                linked = state.position_manager.get_linked(pos.linked_position_id)
                if linked and not any(p is linked for p, _, _, _ in positions_to_close):
                    is_lk_sec = (linked.leg == "secondary")
                    lc, _, _, _, la, _ = _get_bar_data(sig, local_bar, not is_lk_sec)
                    positions_to_close.append((linked, lc, "linked_exit", la))

    # Execute all closures
    for pos, exit_price, reason, exit_adv in positions_to_close:
        if pos in state.position_manager.open_positions:
            _close_position(state, pos, global_bar, exit_price, reason, exit_adv, config)


def _process_entries(
    state: SimulationState,
    all_signals: dict[str, dict[str, TokenSignals]],
    strategy_specs: dict[str, StrategySpec],
    bar_maps: dict[str, np.ndarray],
    global_bar: int,
    config: PortfolioConfig,
    rng: np.random.RandomState,
):
    """Process entries with portfolio-level constraints and shuffled order."""
    # Build candidate list
    candidates: list[tuple[str, str, TokenSignals]] = []  # (strategy_id, token, signals)

    for strategy_id, token_signals in all_signals.items():
        for token, sig in token_signals.items():
            bm = bar_maps.get(token)
            if bm is None:
                continue
            local_bar = int(bm[global_bar])
            if local_bar == -1 or local_bar >= sig.n_bars:
                continue

            # Check entry signal
            if not sig.entry_mask[local_bar]:
                continue

            # For combined: BOTH legs must signal
            if sig.is_combined:
                if sig.secondary_entry_mask is None or not sig.secondary_entry_mask[local_bar]:
                    continue

            # No re-entry while position open for same token+strategy
            if state.position_manager.find_open_for_token_strategy(token, strategy_id):
                continue

            # No duplicate token across strategies (cross-strategy dedup)
            if state.position_manager.total_margin_for_token(token) > 0:
                continue

            candidates.append((strategy_id, token, sig))

    if not candidates:
        return

    # Order candidates based on conviction mode
    if config.conviction_mode == "ranked":
        # Sort by conviction score descending — highest conviction gets capital first
        def _get_conviction(c):
            sid, tok, sig = c
            bm = bar_maps[tok]
            lb = int(bm[global_bar])
            if sig.conviction_score is not None and 0 <= lb < len(sig.conviction_score):
                return float(sig.conviction_score[lb])
            return 1.0  # no conviction data → neutral priority
        indices = sorted(range(len(candidates)), key=lambda i: _get_conviction(candidates[i]), reverse=True)
    elif config.conviction_mode == "hybrid":
        # Tier into conviction buckets, shuffle within each tier
        def _get_conviction(c):
            sid, tok, sig = c
            bm = bar_maps[tok]
            lb = int(bm[global_bar])
            if sig.conviction_score is not None and 0 <= lb < len(sig.conviction_score):
                return float(sig.conviction_score[lb])
            return 1.0
        # 3 tiers: high (>0.66), medium (0.33-0.66), low (<0.33)
        tiers = [[], [], []]
        for i, c in enumerate(candidates):
            conv = _get_conviction(c)
            if conv >= 0.66:
                tiers[0].append(i)
            elif conv >= 0.33:
                tiers[1].append(i)
            else:
                tiers[2].append(i)
        indices = []
        for tier in tiers:
            rng.shuffle(tier)
            indices.extend(tier)
    else:
        # Default: random shuffle (original behavior)
        indices = list(range(len(candidates)))
        rng.shuffle(indices)

    for idx in indices:
        strategy_id, token, sig = candidates[idx]
        spec = strategy_specs[strategy_id]

        bm = bar_maps[token]
        local_bar = int(bm[global_bar])

        # Constraint 0: minimum conviction threshold
        if config.min_conviction_threshold > 0:
            conv = 1.0
            if sig.conviction_score is not None and 0 <= local_bar < len(sig.conviction_score):
                conv = float(sig.conviction_score[local_bar])
            if conv < config.min_conviction_threshold:
                state.rejections.conviction += 1
                continue

        # Constraint 1: portfolio position limit
        if state.position_manager.total_open() >= config.max_portfolio_positions:
            state.rejections.portfolio_limit += 1
            continue

        # Constraint 2: per-strategy position limit
        if state.position_manager.count_for_strategy(strategy_id) >= spec.max_positions:
            state.rejections.strategy_limit += 1
            continue

        # Compute sizing
        portfolio_eq = state.portfolio_equity
        strategy_equity = portfolio_eq * spec.weight

        close_val = sig.close[local_bar]
        atr_val = sig.atr[local_bar]
        if np.isnan(atr_val):
            atr_val = close_val * 0.02
        volatility = atr_val / max(close_val, 1e-10)

        adv_val = sig.rolling_adv[local_bar]
        sm_val = float(sig.size_multiplier[local_bar])
        lev_val = float(sig.leverage[local_bar])

        pos_usd = compute_position_size(
            strategy_equity=strategy_equity,
            rolling_adv=adv_val,
            volatility=volatility,
            edge=sig.edge,
            size_multiplier=sm_val,
            cap_multiplier=sig.cap_multiplier,
            max_trade_pct=sig.max_trade_pct,
            adv_cap_pct=config.adv_cap_pct,
        )

        direction = int(sig.direction[local_bar])
        if direction == 0:
            direction = 1

        if sig.is_combined:
            # Split capital between legs
            primary_usd = pos_usd * sig.capital_split
            secondary_usd = pos_usd * (1.0 - sig.capital_split)

            # Constraint 4: each leg >= min_position_usd
            if primary_usd < config.min_position_usd or secondary_usd < config.min_position_usd:
                state.rejections.min_size += 1
                continue

            # Constraint 6: ADV cap per leg
            perp_adv = sig.perp_rolling_adv[local_bar] if sig.perp_rolling_adv is not None else adv_val
            if primary_usd > adv_val * config.adv_cap_pct or secondary_usd > perp_adv * config.adv_cap_pct:
                state.rejections.adv_cap += 1
                continue

            total_margin = primary_usd + secondary_usd

            # Constraint 7: concentration limit (scale down if needed)
            existing_margin = state.position_manager.total_margin_for_token(token)
            max_for_token = config.concentration_limit * portfolio_eq - existing_margin
            if total_margin > max_for_token:
                # Both legs must remain >= min_position_usd after scaling
                min_split = min(sig.capital_split, 1.0 - sig.capital_split)
                if max_for_token * min_split < config.min_position_usd:
                    state.rejections.concentration += 1
                    continue
                # Scale down proportionally
                pos_usd = max_for_token
                primary_usd = pos_usd * sig.capital_split
                secondary_usd = pos_usd * (1.0 - sig.capital_split)
                total_margin = primary_usd + secondary_usd
                state.partial_fills += 1

            # Constraint 8: free capital
            spot_fee_rate = get_fee_rate(config.exchange, "spot", "taker")
            perp_fee_rate = get_fee_rate(config.exchange, "perp", "taker")

            # Primary leg fee
            if sig.is_perp_primary:
                primary_fee_rate = perp_fee_rate
            else:
                primary_fee_rate = spot_fee_rate
            # Secondary leg fee
            if sig.is_perp_secondary:
                secondary_fee_rate = perp_fee_rate
            else:
                secondary_fee_rate = spot_fee_rate

            # Apply leverage to notional for fee calculation
            primary_notional = primary_usd * lev_val if sig.is_perp_primary and lev_val > 1.0 else primary_usd
            sec_lev = sig.secondary_leverage
            secondary_notional = secondary_usd * sec_lev if sig.is_perp_secondary and sec_lev > 1.0 else secondary_usd

            primary_entry_fee = primary_notional * primary_fee_rate
            secondary_entry_fee = secondary_notional * secondary_fee_rate
            total_entry_fee = primary_entry_fee + secondary_entry_fee

            if state.free_capital < total_margin + total_entry_fee:
                # Reserve buffer for per-bar funding costs on open positions
                funding_buffer = max(state.portfolio_equity * 0.01, 1.0)
                usable = state.free_capital - funding_buffer
                if usable <= 0:
                    state.rejections.capital += 1
                    continue
                # Compute fee ratio (linear in pos_usd) for scale-down
                fee_pct = total_entry_fee / max(total_margin, 1e-10)
                affordable = usable / (1.0 + fee_pct)
                min_split = min(sig.capital_split, 1.0 - sig.capital_split)
                if affordable * min_split < config.min_position_usd:
                    state.rejections.capital += 1
                    continue
                # Scale down to affordable size
                pos_usd = affordable
                primary_usd = pos_usd * sig.capital_split
                secondary_usd = pos_usd * (1.0 - sig.capital_split)
                total_margin = primary_usd + secondary_usd
                # Recompute notionals and fees at new size
                primary_notional = primary_usd * lev_val if sig.is_perp_primary and lev_val > 1.0 else primary_usd
                secondary_notional = secondary_usd * sec_lev if sig.is_perp_secondary and sec_lev > 1.0 else secondary_usd
                primary_entry_fee = primary_notional * primary_fee_rate
                secondary_entry_fee = secondary_notional * secondary_fee_rate
                total_entry_fee = primary_entry_fee + secondary_entry_fee
                state.partial_fills += 1

            # Open both legs atomically
            pos_id_base = f"{token}:{strategy_id}:{global_bar}"

            # Primary leg
            p_close = sig.close[local_bar]
            p_high = sig.high[local_bar]
            p_low = sig.low[local_bar]
            p_atr = atr_val

            p_slip_bps = compute_slippage_bps(primary_notional, adv_val, config.base_spread_bps, config.impact_coeff, config.max_slip_bps)
            p_slip = p_close * p_slip_bps / 10000.0
            p_entry_price = p_close + p_slip * direction

            p_quantity = primary_notional / max(p_entry_price, 1e-10) * direction

            p_initial_risk = float(sig.stop_mult[local_bar]) * p_atr
            if direction == 1:
                p_stop = p_entry_price - p_initial_risk
            else:
                p_stop = p_entry_price + p_initial_risk

            primary_pos = Position(
                position_id=f"{pos_id_base}:primary",
                token=token,
                strategy_id=strategy_id,
                leg="primary",
                entry_bar=global_bar,
                entry_price=p_entry_price,
                direction=direction,
                quantity=p_quantity,
                margin_usd=primary_usd,
                leverage=lev_val,
                is_perp=sig.is_perp_primary,
                fee_rate=primary_fee_rate,
                stop_mult=float(sig.stop_mult[local_bar]),
                trail_mult=float(sig.trail_mult[local_bar]),
                target_mult=sig.target_mult,
                no_stop_bars=sig.no_stop_bars,
                min_hold=sig.min_hold,
                max_hold=sig.max_hold,
                exit_regimes=sig.exit_regimes,
                convex_exit=sig.convex_exit,
                rsi_exit_level=sig.rsi_exit_level,
                trail_schedule=sig.trail_schedule,
                time_trail_schedule=sig.time_trail_schedule,
                max_trail_mult_arr=sig.max_trail_mult,
                funding_exit_threshold=sig.funding_exit_threshold,
                partial_tp_atr=sig.partial_tp_atr,
                partial_tp_pct=sig.partial_tp_pct,
                partial_tp_trail=sig.partial_tp_trail,
                breakeven_atr=sig.breakeven_atr,
                stop_price=p_stop,
                highest=p_high,
                lowest=p_low,
                initial_risk=p_initial_risk,
                linked_position_id=f"{pos_id_base}:secondary",
            )

            # Secondary leg
            sec_dir = int(sig.secondary_direction[local_bar]) if sig.secondary_direction is not None else direction
            if sec_dir == 0:
                sec_dir = -direction  # default: opposite of primary for carry

            if sig.perp_close is not None:
                s_close = sig.perp_close[local_bar]
                s_high = sig.perp_high[local_bar]
                s_low = sig.perp_low[local_bar]
                s_atr = sig.perp_atr[local_bar] if sig.perp_atr is not None else atr_val
                s_adv = sig.perp_rolling_adv[local_bar] if sig.perp_rolling_adv is not None else adv_val
            else:
                s_close = sig.close[local_bar]
                s_high = sig.high[local_bar]
                s_low = sig.low[local_bar]
                s_atr = atr_val
                s_adv = adv_val

            s_slip_bps = compute_slippage_bps(secondary_notional, s_adv, config.base_spread_bps, config.impact_coeff, config.max_slip_bps)
            s_slip = s_close * s_slip_bps / 10000.0
            s_entry_price = s_close + s_slip * sec_dir

            s_quantity = secondary_notional / max(s_entry_price, 1e-10) * sec_dir

            # Secondary trade params (fall back to primary if not specified)
            s_stop_mult = sig.sec_stop_mult if sig.sec_stop_mult is not None else float(sig.stop_mult[local_bar])
            s_trail_mult = sig.sec_trail_mult if sig.sec_trail_mult is not None else float(sig.trail_mult[local_bar])
            s_target_mult = sig.sec_target_mult if sig.sec_target_mult is not None else sig.target_mult
            s_no_stop_bars = sig.sec_no_stop_bars if sig.sec_no_stop_bars is not None else sig.no_stop_bars
            s_min_hold = sig.sec_min_hold if sig.sec_min_hold is not None else sig.min_hold
            s_max_hold = sig.sec_max_hold if sig.sec_max_hold is not None else sig.max_hold

            if np.isnan(s_atr):
                s_atr = abs(s_entry_price) * 0.02
            s_initial_risk = s_stop_mult * s_atr
            if sec_dir == 1:
                s_stop = s_entry_price - s_initial_risk
            else:
                s_stop = s_entry_price + s_initial_risk

            secondary_pos = Position(
                position_id=f"{pos_id_base}:secondary",
                token=token,
                strategy_id=strategy_id,
                leg="secondary",
                entry_bar=global_bar,
                entry_price=s_entry_price,
                direction=sec_dir,
                quantity=s_quantity,
                margin_usd=secondary_usd,
                leverage=sec_lev,
                is_perp=sig.is_perp_secondary,
                fee_rate=secondary_fee_rate,
                stop_mult=s_stop_mult,
                trail_mult=s_trail_mult,
                target_mult=s_target_mult,
                no_stop_bars=s_no_stop_bars,
                min_hold=s_min_hold,
                max_hold=s_max_hold,
                exit_regimes=sig.exit_regimes,
                convex_exit=sig.convex_exit,
                rsi_exit_level=sig.rsi_exit_level,
                trail_schedule=sig.trail_schedule,
                time_trail_schedule=sig.time_trail_schedule,
                max_trail_mult_arr=sig.max_trail_mult,
                funding_exit_threshold=sig.funding_exit_threshold,
                partial_tp_atr=sig.partial_tp_atr,
                partial_tp_pct=sig.partial_tp_pct,
                partial_tp_trail=sig.partial_tp_trail,
                breakeven_atr=sig.breakeven_atr,
                stop_price=s_stop,
                highest=s_high,
                lowest=s_low,
                initial_risk=s_initial_risk,
                linked_position_id=f"{pos_id_base}:primary",
            )

            # Deduct fees, open positions
            state.total_fees += total_entry_fee
            state._entry_fees_by_pos[primary_pos.position_id] = primary_entry_fee
            state._entry_fees_by_pos[secondary_pos.position_id] = secondary_entry_fee
            state.position_manager.open_position(primary_pos)
            state.position_manager.open_position(secondary_pos)

        else:
            # Single-leg entry
            # Constraint 5: min position size
            if pos_usd < config.min_position_usd:
                state.rejections.min_size += 1
                continue

            # Constraint 6: ADV cap
            if pos_usd > adv_val * config.adv_cap_pct:
                state.rejections.adv_cap += 1
                continue

            # Leverage: amplify notional, margin stays same
            margin_usd = pos_usd
            if sig.is_perp_primary and lev_val > 1.0:
                notional_usd = pos_usd * lev_val
            else:
                notional_usd = pos_usd

            # Constraint 7: concentration limit (scale down if needed)
            existing_margin = state.position_manager.total_margin_for_token(token)
            max_for_token = config.concentration_limit * portfolio_eq - existing_margin
            if margin_usd > max_for_token:
                if max_for_token < config.min_position_usd:
                    state.rejections.concentration += 1
                    continue
                # Scale down to fit concentration limit
                pos_usd = max_for_token
                margin_usd = pos_usd
                if sig.is_perp_primary and lev_val > 1.0:
                    notional_usd = pos_usd * lev_val
                else:
                    notional_usd = pos_usd
                state.partial_fills += 1

            # Fee
            if sig.is_perp_primary:
                fee_rate = get_fee_rate(config.exchange, "perp", "taker")
            else:
                fee_rate = get_fee_rate(config.exchange, "spot", "taker")

            entry_fee = notional_usd * fee_rate

            # Constraint 8: free capital (scale down if needed)
            if state.free_capital < margin_usd + entry_fee:
                # Reserve buffer for per-bar funding costs on open positions
                funding_buffer = max(state.portfolio_equity * 0.01, 1.0)
                usable = state.free_capital - funding_buffer
                if usable <= 0:
                    state.rejections.capital += 1
                    continue
                # Compute affordable margin given fee structure
                if sig.is_perp_primary and lev_val > 1.0:
                    affordable = usable / (1.0 + lev_val * fee_rate)
                else:
                    affordable = usable / (1.0 + fee_rate)
                if affordable < config.min_position_usd:
                    state.rejections.capital += 1
                    continue
                # Scale down to affordable size
                pos_usd = affordable
                margin_usd = pos_usd
                if sig.is_perp_primary and lev_val > 1.0:
                    notional_usd = pos_usd * lev_val
                else:
                    notional_usd = pos_usd
                entry_fee = notional_usd * fee_rate
                state.partial_fills += 1

            # Entry slippage
            slip_bps = compute_slippage_bps(notional_usd, adv_val, config.base_spread_bps, config.impact_coeff, config.max_slip_bps)
            slip = close_val * slip_bps / 10000.0
            entry_price = close_val + slip * direction

            quantity = notional_usd / max(entry_price, 1e-10) * direction

            initial_risk = float(sig.stop_mult[local_bar]) * atr_val
            if direction == 1:
                stop_price = entry_price - initial_risk
            else:
                stop_price = entry_price + initial_risk

            pos = Position(
                position_id=f"{token}:{strategy_id}:{global_bar}:primary",
                token=token,
                strategy_id=strategy_id,
                leg="primary",
                entry_bar=global_bar,
                entry_price=entry_price,
                direction=direction,
                quantity=quantity,
                margin_usd=margin_usd,
                leverage=lev_val,
                is_perp=sig.is_perp_primary,
                fee_rate=fee_rate,
                stop_mult=float(sig.stop_mult[local_bar]),
                trail_mult=float(sig.trail_mult[local_bar]),
                target_mult=sig.target_mult,
                no_stop_bars=sig.no_stop_bars,
                min_hold=sig.min_hold,
                max_hold=sig.max_hold,
                exit_regimes=sig.exit_regimes,
                convex_exit=sig.convex_exit,
                rsi_exit_level=sig.rsi_exit_level,
                trail_schedule=sig.trail_schedule,
                time_trail_schedule=sig.time_trail_schedule,
                max_trail_mult_arr=sig.max_trail_mult,
                funding_exit_threshold=sig.funding_exit_threshold,
                partial_tp_atr=sig.partial_tp_atr,
                partial_tp_pct=sig.partial_tp_pct,
                partial_tp_trail=sig.partial_tp_trail,
                breakeven_atr=sig.breakeven_atr,
                stop_price=stop_price,
                highest=sig.high[local_bar],
                lowest=sig.low[local_bar],
                initial_risk=initial_risk,
            )

            state.total_fees += entry_fee
            state._entry_fees_by_pos[pos.position_id] = entry_fee
            state.position_manager.open_position(pos)


def simulate_portfolio(
    all_signals: dict[str, dict[str, TokenSignals]],
    strategy_specs: dict[str, StrategySpec],
    config: PortfolioConfig,
) -> SimulationState:
    """Run bar-by-bar portfolio simulation.

    Args:
        all_signals: {strategy_id: {token: TokenSignals}}
        strategy_specs: {strategy_id: StrategySpec}
        config: Portfolio configuration

    Returns:
        SimulationState with completed trades and equity snapshots
    """
    unified_ts, bar_maps = build_unified_index(all_signals)
    n_bars = len(unified_ts)

    state = SimulationState(initial_capital=config.capital)
    rng = np.random.RandomState(config.seed)

    for global_bar in range(n_bars):
        _process_exits(state, all_signals, bar_maps, global_bar, config)
        _process_entries(state, all_signals, strategy_specs, bar_maps, global_bar, config, rng)

        # Record equity snapshot
        timestamp = unified_ts[global_bar]
        state.equity_snapshots.append((timestamp, state.portfolio_equity))

        # Invariant check — allow funding-induced violations (funding deducted
        # bar-by-bar can push equity below locked margin before exits free capital;
        # partial fills consume most free capital, amplifying the effect)
        if state.free_capital < -max(state.portfolio_equity * 0.05, 1.0):
            raise RuntimeError(
                f"Free capital invariant violated at bar {global_bar}: "
                f"free_capital={state.free_capital:.2f}, equity={state.portfolio_equity:.2f}"
            )

    # Force-close all remaining positions at end
    _close_all_remaining(state, all_signals, bar_maps, n_bars - 1, config)

    return state


def _close_all_remaining(
    state: SimulationState,
    all_signals: dict[str, dict[str, TokenSignals]],
    bar_maps: dict[str, np.ndarray],
    last_bar: int,
    config: PortfolioConfig,
):
    """Force-close all remaining open positions at the end of the simulation."""
    for pos in list(state.position_manager.open_positions):
        sig = all_signals[pos.strategy_id].get(pos.token)
        if sig is None:
            continue

        bm = bar_maps.get(pos.token)
        if bm is None:
            continue

        local_bar = int(bm[last_bar])
        if local_bar == -1 or local_bar >= sig.n_bars:
            # Find last valid bar within this strategy's signal bounds
            local_bar = -1
            for b in range(last_bar - 1, -1, -1):
                lb = int(bm[b])
                if lb != -1 and lb < sig.n_bars:
                    local_bar = lb
                    break
            if local_bar == -1:
                local_bar = max(sig.n_bars - 1, 0)

        is_secondary = (pos.leg == "secondary")
        close_val, _, _, _, adv_val, _ = _get_bar_data(sig, local_bar, not is_secondary)

        _close_position(state, pos, last_bar, close_val, "data_end", adv_val, config)
