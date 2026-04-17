"""V5 Portfolio Backtest — Bar-by-bar portfolio simulation loop.

Processes exits then entries each bar, with portfolio-level constraints.
Faithfully ports ALL v3 JIT exit logic paths.
"""
from __future__ import annotations

import sys
import os
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from .config import PortfolioConfig, StrategySpec, resolve_sizing
from .position import Position, ClosedTrade, PositionManager
from .signals import TokenSignals
from .sizing import compute_slippage_bps, get_sizing_model, get_slippage_model
from .exit_handlers import BarContext, build_exit_chain, run_exit_handlers

# Import fee/MMR lookups from v4
from v5.universe import get_fee_rate, get_maint_margin_rate, get_liquidation_fee_rate


@dataclass
class RejectionStats:
    """Track why entries were rejected."""
    portfolio_limit: int = 0
    strategy_limit: int = 0
    min_size: int = 0
    adv_cap: int = 0
    concentration: int = 0
    capital: int = 0
    direction_zero: int = 0  # entries with direction=0 (defaulted to long)

    def total(self) -> int:
        return (self.portfolio_limit + self.strategy_limit + self.min_size +
                self.direction_zero +
                self.adv_cap + self.concentration + self.capital)

    def to_dict(self) -> dict:
        return {
            "portfolio_limit": self.portfolio_limit,
            "strategy_limit": self.strategy_limit,
            "min_size": self.min_size,
            "adv_cap": self.adv_cap,
            "concentration": self.concentration,
            "capital": self.capital,
            "direction_zero": self.direction_zero,
            "total": self.total(),
        }


@dataclass
class SignalDiagnostics:
    """Track signal funnel from raw entries through to opened positions."""
    raw_entries_fired: dict = field(default_factory=dict)         # per-strategy
    entries_after_liquidity: dict = field(default_factory=dict)
    entries_after_walkforward: dict = field(default_factory=dict)
    entries_opened: dict = field(default_factory=dict)
    entries_rejected_by: dict = field(default_factory=dict)       # {strategy_id: {reason: count}}

    def to_dict(self) -> dict:
        return {
            "raw_entries_fired": dict(self.raw_entries_fired),
            "entries_after_liquidity": dict(self.entries_after_liquidity),
            "entries_after_walkforward": dict(self.entries_after_walkforward),
            "entries_opened": dict(self.entries_opened),
            "entries_rejected_by": {k: dict(v) for k, v in self.entries_rejected_by.items()},
        }


@dataclass
class PendingEntry:
    """A signal that has been armed but not yet executed.

    Reserves a slot in max_positions during the delay period.
    Converts to a real position when the trigger condition is met.

    Trigger can be any combination of:
      - Time delay: entry_bar > 0 means enter at that global bar
      - Price level: checked via armed_levels on TokenSignals
      - Custom function: trigger_fn(close, high, low, atr, bars_held) -> bool
    """
    strategy_id: str
    token: str
    signal_bar: int       # global bar when signal fired
    entry_bar: int        # global bar when to enter (0 = no time trigger, use other conditions)
    direction: int        # 1=long, -1=short
    conviction: float     # frozen from signal bar
    trigger_fn: object = None  # Optional[Callable[[float, float, float, float, int], bool]]
                               # Args: (close, high, low, atr, bars_since_signal) -> should_enter


@dataclass
class SimulationState:
    """Mutable state for the simulation loop."""
    initial_capital: float
    realized_pnl: float = 0.0
    total_fees: float = 0.0
    total_funding: float = 0.0
    position_manager: PositionManager = field(default_factory=PositionManager)
    rejections: RejectionStats = field(default_factory=RejectionStats)
    diagnostics: SignalDiagnostics = field(default_factory=SignalDiagnostics)
    partial_fills: int = 0
    margin_calls: int = 0
    equity_snapshots: list = field(default_factory=list)
    _entry_fees_by_pos: dict = field(default_factory=dict)  # position_id -> entry fee
    last_known_atrs: dict = field(default_factory=dict)    # token -> last ATR value
    _slippage_models: dict = field(default_factory=dict)   # strategy_id -> SlippageModel
    max_equity_watermark: float = 0.0                      # peak MTM equity for DD scaling
    pending_entries: list = field(default_factory=list)     # list[PendingEntry] — armed, slot-reserving

    @property
    def n_pending(self) -> int:
        """Slots reserved by pending entries."""
        return len(self.pending_entries)

    @property
    def effective_open(self) -> int:
        """Open positions + pending entries = total slots consumed."""
        return self.position_manager.total_open() + self.n_pending

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
        if exit_reason in ("stop", "margin_call"):
            effective_adv = exit_adv * config.stress_adv_multiplier
        _slip_model = state._slippage_models.get(pos.strategy_id)
        if _slip_model is not None:
            slip_bps = _slip_model.compute_slippage(notional, effective_adv, config.base_spread_bps, config.impact_coeff, config.max_slip_bps)
        else:
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


def _get_bar_data(sig: TokenSignals, local_bar: int, is_primary: bool = True, use_perp: bool | None = None):
    """Get price/indicator data for a bar, choosing primary or secondary arrays.

    Args:
        use_perp: Explicit override for adaptive strategies. When set, selects
            perp arrays (True) or spot arrays (False) regardless of is_primary.
    """
    want_perp = use_perp if use_perp is not None else (not is_primary)
    if want_perp and sig.perp_close is not None:
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


def _process_exits(
    state: SimulationState,
    all_signals: dict[str, dict[str, TokenSignals]],
    bar_maps: dict[str, np.ndarray],
    global_bar: int,
    config: PortfolioConfig,
    strategy_specs: dict[str, StrategySpec] | None = None,
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
                _up = pos.is_perp if sig.per_bar_is_perp is not None else None
                close_p, _, _, _, adv_p, _ = _get_bar_data(sig, last_valid, not is_secondary, use_perp=_up)
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
                        _lup = linked.is_perp if sig.per_bar_is_perp is not None else None
                        lc, _, _, _, la, _ = _get_bar_data(sig, last_valid, not is_linked_secondary, use_perp=_lup)
                    else:
                        lc, la = linked.entry_price, 1_000_000.0
                    positions_to_close.append((linked, lc, "data_end", la))
            continue

        is_secondary = (pos.leg == "secondary")
        # For adaptive strategies (per_bar_is_perp), use pos.is_perp for price routing
        _use_perp = pos.is_perp if sig.per_bar_is_perp is not None else None
        close_val, high_val, low_val, atr_val, adv_val, funding_val = _get_bar_data(sig, local_bar, not is_secondary, use_perp=_use_perp)

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
                        _lkup = linked.is_perp if sig.per_bar_is_perp is not None else None
                        lc, _, _, _, la, _ = _get_bar_data(sig, local_bar, not is_lk_sec, use_perp=_lkup)
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

        # Build exit handler chain on first bar (lazy init for backward compat)
        if not pos.exit_handlers:
            spec_for_chain = strategy_specs.get(pos.strategy_id) if strategy_specs else None
            pos.exit_handlers = build_exit_chain(pos, sig, spec_for_chain, state=state, config=config)

        # Build BarContext for this bar
        rsi_val = float(sig.rsi[local_bar]) if sig.rsi is not None and local_bar < len(sig.rsi) else float('nan')
        volume_val = float(sig.volume[local_bar]) if sig.volume is not None and local_bar < len(sig.volume) else float('nan')
        vol_20_val = float(sig.vol_20[local_bar]) if sig.vol_20 is not None and local_bar < len(sig.vol_20) else float('nan')
        ret_1h_val = float(sig.ret_1h[local_bar]) if sig.ret_1h is not None and local_bar < len(sig.ret_1h) else float('nan')
        bar_ctx = BarContext(
            close=close_val,
            high=high_val,
            low=low_val,
            atr=cur_atr,
            rsi=rsi_val,
            regime=int(sig.regime[local_bar]),
            bars_held=bars_held,
            local_bar=local_bar,
            funding_val=funding_val,
            volume=volume_val,
            vol_20=vol_20_val,
            ret_1h=ret_1h_val,
        )

        # Run handler chain: update_state -> partial TP -> check_exit
        exit_result = run_exit_handlers(pos, bar_ctx, global_bar, adv_val)

        exit_signal = exit_result.should_exit
        exit_reason = exit_result.reason
        exit_price = exit_result.exit_price_override if exit_result.exit_price_override is not None else close_val

        if exit_signal:
            positions_to_close.append((pos, exit_price, exit_reason, adv_val))
            # Close linked position too
            if pos.linked_position_id:
                linked = state.position_manager.get_linked(pos.linked_position_id)
                if linked and not any(p is linked for p, _, _, _ in positions_to_close):
                    is_lk_sec = (linked.leg == "secondary")
                    _lkup = linked.is_perp if sig.per_bar_is_perp is not None else None
                    lc, _, _, _, la, _ = _get_bar_data(sig, local_bar, not is_lk_sec, use_perp=_lkup)
                    positions_to_close.append((linked, lc, "linked_exit", la))

    # Execute all closures
    for pos, exit_price, reason, exit_adv in positions_to_close:
        if pos in state.position_manager.open_positions:
            _close_position(state, pos, global_bar, exit_price, reason, exit_adv, config)


def _compute_total_unrealized(
    state: SimulationState,
    all_signals: dict,
    bar_maps: dict,
    global_bar: int,
) -> float:
    """Compute total unrealized P&L across open positions."""
    total = 0.0
    for pos in state.position_manager.open_positions:
        sig = all_signals.get(pos.strategy_id, {}).get(pos.token)
        if sig is None:
            continue
        bm = bar_maps.get(pos.token)
        if bm is None:
            continue
        lb = int(bm[global_bar])
        if lb == -1 or lb >= sig.n_bars:
            continue
        is_sec = (pos.leg == "secondary")
        _up = pos.is_perp if sig.per_bar_is_perp is not None else None
        close_val = _get_bar_data(sig, lb, not is_sec, use_perp=_up)[0]
        if np.isnan(close_val):
            continue
        total += pos.quantity * (close_val - pos.entry_price)
    return total


def _process_pending_entries(
    state: SimulationState,
    all_signals: dict[str, dict[str, TokenSignals]],
    bar_maps: dict[str, np.ndarray],
    global_bar: int,
    config: PortfolioConfig,
):
    """Convert mature pending entries into real entry signals at current bar's price.

    When a pending entry's delay expires (global_bar >= entry_bar), inject it as
    an entry signal on this bar by setting entry_mask/direction/conviction on the
    TokenSignals. The normal _process_entries() will then pick it up and open
    the position at today's price with the original conviction.

    Pending entries convert when ANY trigger condition is met:
      1. Time delay expired: global_bar >= entry_bar
      2. Price level hit: armed_levels crossed (if set by strategy)
    Pending entries that exceed max_pending_bars are expired and removed.
    """
    if not state.pending_entries:
        return

    still_pending = []
    for pe in state.pending_entries:
        # Expired?
        if global_bar > pe.signal_bar + config.max_pending_bars:
            continue  # drop expired pending

        sig = all_signals.get(pe.strategy_id, {}).get(pe.token)
        if sig is None:
            still_pending.append(pe)
            continue

        bm = bar_maps.get(pe.token)
        if bm is None:
            still_pending.append(pe)
            continue

        local_bar = int(bm[global_bar])
        if local_bar == -1 or local_bar >= sig.n_bars:
            still_pending.append(pe)
            continue

        # Check trigger conditions (any one sufficient)
        triggered = False

        # Trigger 1: time delay expired
        if pe.entry_bar > 0 and global_bar >= pe.entry_bar:
            triggered = True

        # Trigger 2: custom trigger function
        if not triggered and pe.trigger_fn is not None:
            close_val = float(sig.close[local_bar])
            high_val = float(sig.high[local_bar]) if sig.high is not None else close_val
            low_val = float(sig.low[local_bar]) if sig.low is not None else close_val
            atr_val = float(sig.atr[local_bar]) if sig.atr is not None else close_val * 0.02
            bars_since = global_bar - pe.signal_bar
            try:
                triggered = bool(pe.trigger_fn(close_val, high_val, low_val, atr_val, bars_since))
            except Exception:
                pass  # trigger failed, keep pending

        # Trigger 3: armed price level crossed
        if not triggered and sig.armed_levels is not None and local_bar < len(sig.armed_levels):
            level = float(sig.armed_levels[local_bar])
            if not np.isnan(level) and level > 0:
                close_val = float(sig.close[local_bar])
                high_val = float(sig.high[local_bar]) if sig.high is not None else close_val
                low_val = float(sig.low[local_bar]) if sig.low is not None else close_val
                if pe.direction == 1 and low_val <= level:  # long: price dipped to target
                    triggered = True
                elif pe.direction == -1 and high_val >= level:  # short: price rose to target
                    triggered = True

        if not triggered:
            still_pending.append(pe)
            continue

        # Triggered — inject entry signal
        sig.entry_mask[local_bar] = True
        sig.direction[local_bar] = pe.direction
        if sig.conviction_score is not None and local_bar < len(sig.conviction_score):
            sig.conviction_score[local_bar] = pe.conviction
        # Set entry_limit_price to armed level for precise fill at target price
        if sig.armed_levels is not None and local_bar < len(sig.armed_levels):
            armed_price = float(sig.armed_levels[local_bar])
            if not np.isnan(armed_price) and armed_price > 0:
                if sig.entry_limit_price is None:
                    sig.entry_limit_price = np.full(sig.n_bars, np.nan)
                sig.entry_limit_price[local_bar] = armed_price
        # Prevent re-arming: clear delay on THIS bar so _process_entries enters immediately.
        # Preserve config.entry_delay_bars for future organic signals on this token.
        if sig.entry_delay is None:
            sig.entry_delay = np.full(sig.n_bars, config.entry_delay_bars, dtype=int)
        if local_bar < len(sig.entry_delay):
            sig.entry_delay[local_bar] = 0
        if sig.armed_levels is not None and local_bar < len(sig.armed_levels):
            sig.armed_levels[local_bar] = np.nan

    state.pending_entries = still_pending


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
    # Compute unrealized P&L once per bar (doesn't change during entries)
    total_unrealized = _compute_total_unrealized(state, all_signals, bar_maps, global_bar)

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

            # Limit concurrent positions per token+strategy (default 1 = no re-entry)
            # Note: for combined strategies, each entry creates 2 legs (primary+secondary),
            # so the count reflects individual Position objects, not logical entries.
            spec_for_check = strategy_specs.get(strategy_id)
            max_conc = spec_for_check.max_positions_per_symbol if spec_for_check else 1
            if len(state.position_manager.find_open_for_token_strategy(token, strategy_id)) >= max_conc:
                continue

            candidates.append((strategy_id, token, sig))

    if not candidates:
        return

    # Order candidates by conviction score descending — highest conviction gets capital first.
    def _get_conviction(c):
        sid, tok, sig = c
        bm = bar_maps[tok]
        lb = int(bm[global_bar])
        if sig.conviction_score is not None and 0 <= lb < len(sig.conviction_score):
            return float(sig.conviction_score[lb])
        return 1.0  # no conviction data → neutral priority
    indices = sorted(range(len(candidates)), key=lambda i: _get_conviction(candidates[i]), reverse=True)

    # Cache resolved sizing per strategy (resolve once, not per candidate)
    _resolved_sizing_cache: dict = {}
    _sizing_model_cache: dict = {}

    # Slots masked by entry_filter_fn returning < 0 ("skip and don't replace")
    _masked_slots = 0

    for idx in indices:
        strategy_id, token, sig = candidates[idx]
        spec = strategy_specs.get(strategy_id)
        if spec is None:
            continue

        # Resolve sizing overrides and sizing model for this strategy (cached)
        if strategy_id not in _resolved_sizing_cache:
            _resolved_sizing_cache[strategy_id] = resolve_sizing(
                config.sizing_defaults, spec.sizing_overrides
            )
            _sizing_model_cache[strategy_id] = get_sizing_model(spec.sizing_model)
        resolved = _resolved_sizing_cache[strategy_id]
        sizing_model = _sizing_model_cache[strategy_id]
        slippage_model = state._slippage_models.get(strategy_id)

        bm = bar_maps[token]
        local_bar = int(bm[global_bar])


        # Strategy-defined entry filter: conviction adjustment based on trade history
        # Return values: >0 = allow (multiply conviction), 0 = block, <0 = mask without replace
        if spec.entry_filter_fn is not None:
            _token_trades = [t for t in state.position_manager.closed_trades
                             if t.token == token]
            _direction = int(sig.direction[local_bar])
            try:
                _mult = float(spec.entry_filter_fn(token, _direction, _token_trades, global_bar))
                if _mult < 0.0:
                    _masked_slots += 1  # consume a slot so next candidate can't fill it
                    continue
                if _mult == 0.0:
                    continue  # strategy says block (slot available for next candidate)
                if _mult < 1.0 and sig.conviction_score is not None and local_bar < len(sig.conviction_score):
                    sig.conviction_score[local_bar] *= _mult
            except Exception:
                pass  # filter error, allow entry

        # Constraint 1: portfolio position limit (includes pending/armed entries + masked slots)
        if state.effective_open + _masked_slots >= config.max_portfolio_positions:
            state.rejections.portfolio_limit += 1
            continue

        # Constraint 2: per-strategy position limit (includes pending for this strategy)
        n_pending_for_strat = sum(1 for pe in state.pending_entries if pe.strategy_id == strategy_id)
        if state.position_manager.count_for_strategy(strategy_id) + n_pending_for_strat >= spec.max_positions:
            state.rejections.strategy_limit += 1
            continue

        # Check if already pending for this token+strategy (prevent double-arming)
        _already_pending = any(pe.strategy_id == strategy_id and pe.token == token
                               for pe in state.pending_entries)

        # Armed/delayed entry: per-bar delay from strategy, or global config fallback
        # Also check armed_levels for price-triggered pending entries
        _delay = 0
        if sig.entry_delay is not None and 0 <= local_bar < len(sig.entry_delay):
            _delay = int(sig.entry_delay[local_bar])
        elif config.entry_delay_bars > 0:
            _delay = config.entry_delay_bars

        _has_armed_level = (sig.armed_levels is not None and 0 <= local_bar < len(sig.armed_levels)
                            and not np.isnan(float(sig.armed_levels[local_bar]))
                            and float(sig.armed_levels[local_bar]) > 0)

        if (_delay > 0 or _has_armed_level) and not _already_pending:
            conv = 1.0
            if sig.conviction_score is not None and 0 <= local_bar < len(sig.conviction_score):
                conv = float(sig.conviction_score[local_bar])
            state.pending_entries.append(PendingEntry(
                strategy_id=strategy_id,
                token=token,
                signal_bar=global_bar,
                entry_bar=global_bar + _delay,
                direction=int(sig.direction[local_bar]),
                conviction=conv,
            ))
            continue  # slot reserved, skip immediate entry
        # (debug: arming happened above if _delay > 0)

        # Compute sizing
        portfolio_eq = state.portfolio_equity
        # Apply unrealized P&L constraint (constrain-only: never inflates above realized)
        sizing_eq = min(portfolio_eq + total_unrealized, portfolio_eq)
        sizing_eq = max(sizing_eq, 0.0)  # floor at 0 when NLV is negative
        if config.max_sizing_equity is not None:
            sizing_eq = min(sizing_eq, config.max_sizing_equity)
        strategy_equity = sizing_eq * spec.weight

        close_val = sig.close[local_bar]
        atr_val = sig.atr[local_bar]
        if np.isnan(atr_val):
            atr_val = close_val * 0.02
        volatility = atr_val / max(close_val, 1e-10)

        adv_val = sig.rolling_adv[local_bar]
        lev_val = float(sig.leverage[local_bar])

        pos_usd = sizing_model.compute_size(
            strategy_equity=strategy_equity,
            rolling_adv=adv_val,
            edge=sig.edge,
            adv_cap_pct=config.adv_cap_pct,
            edge_minimum=resolved.edge_minimum,
            spot_max_equity_pct=resolved.spot_max_equity_pct,
            leverage=lev_val,
        )

        direction = int(sig.direction[local_bar])
        if direction == 0:
            state.rejections.direction_zero += 1
            continue  # reject: strategies must emit +1 or -1

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
                funding_buffer = max(state.portfolio_equity * config.sizing_defaults.funding_buffer_pct, 1.0)
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

            if slippage_model is not None:
                p_slip_bps = slippage_model.compute_slippage(primary_notional, adv_val, config.base_spread_bps, config.impact_coeff, config.max_slip_bps)
            else:
                p_slip_bps = compute_slippage_bps(primary_notional, adv_val, config.base_spread_bps, config.impact_coeff, config.max_slip_bps)
            p_slip = p_close * p_slip_bps / 10000.0
            p_entry_price = p_close + p_slip * direction

            p_quantity = primary_notional / max(p_entry_price, 1e-10) * direction

            _psm = float(sig.stop_mult[local_bar])
            if _psm >= 999:
                p_initial_risk = 0.0
                p_stop = 0.0
            elif direction == 1:
                p_initial_risk = _psm * p_atr
                p_stop = p_entry_price - p_initial_risk
            else:
                p_initial_risk = _psm * p_atr
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
                convex_exit=sig.convex_exit,
                rsi_exit_level=sig.rsi_exit_level,
                convex_bar_thresholds=sig.convex_bar_thresholds,
                convex_multipliers=sig.convex_multipliers,
                trail_schedule=sig.trail_schedule,
                time_trail_schedule=sig.time_trail_schedule,
                max_trail_mult_arr=sig.max_trail_mult,
                funding_exit_threshold=sig.funding_exit_threshold,
                breakeven_atr=sig.breakeven_atr,
                chandelier_lookback=sig.chandelier_lookback,
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

            if slippage_model is not None:
                s_slip_bps = slippage_model.compute_slippage(secondary_notional, s_adv, config.base_spread_bps, config.impact_coeff, config.max_slip_bps)
            else:
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
                convex_exit=sig.convex_exit,
                rsi_exit_level=sig.rsi_exit_level,
                convex_bar_thresholds=sig.convex_bar_thresholds,
                convex_multipliers=sig.convex_multipliers,
                trail_schedule=sig.trail_schedule,
                time_trail_schedule=sig.time_trail_schedule,
                max_trail_mult_arr=sig.max_trail_mult,
                funding_exit_threshold=sig.funding_exit_threshold,
                breakeven_atr=sig.breakeven_atr,
                chandelier_lookback=sig.chandelier_lookback,
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
            primary_pos.exit_handlers = build_exit_chain(primary_pos, sig, spec, state=state, config=config)
            secondary_pos.exit_handlers = build_exit_chain(secondary_pos, sig, spec, state=state, config=config)
            state.position_manager.open_position(primary_pos)
            state.position_manager.open_position(secondary_pos)
            if strategy_id not in state.diagnostics.entries_opened:
                state.diagnostics.entries_opened[strategy_id] = 0
            state.diagnostics.entries_opened[strategy_id] += 1

        else:
            # Single-leg entry

            # Per-bar venue routing for adaptive strategies (spot longs, perp shorts)
            is_perp_this_bar = sig.is_perp_primary
            if sig.per_bar_is_perp is not None:
                is_perp_this_bar = bool(sig.per_bar_is_perp[local_bar])
                # Override price/ADV with correct venue data
                if is_perp_this_bar and sig.perp_close is not None:
                    close_val = float(sig.perp_close[local_bar])
                    atr_val = float(sig.perp_atr[local_bar])
                    if np.isnan(atr_val):
                        atr_val = close_val * 0.02
                    adv_val = float(sig.perp_rolling_adv[local_bar])
                    volatility = atr_val / max(close_val, 1e-10)
                    # Recompute position size with perp venue data
                    pos_usd = sizing_model.compute_size(
                        strategy_equity=strategy_equity,
                        rolling_adv=adv_val,
                        edge=sig.edge,
                        adv_cap_pct=config.adv_cap_pct,
                        edge_minimum=resolved.edge_minimum,
                        spot_max_equity_pct=resolved.spot_max_equity_pct,
                        leverage=lev_val,
                    )

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
            if is_perp_this_bar and lev_val > 1.0:
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
                if is_perp_this_bar and lev_val > 1.0:
                    notional_usd = pos_usd * lev_val
                else:
                    notional_usd = pos_usd
                state.partial_fills += 1

            # Fee
            if is_perp_this_bar:
                fee_rate = get_fee_rate(config.exchange, "perp", "taker")
            else:
                fee_rate = get_fee_rate(config.exchange, "spot", "taker")

            entry_fee = notional_usd * fee_rate

            # Constraint 8: free capital (scale down if needed)
            if state.free_capital < margin_usd + entry_fee:
                # Reserve buffer for per-bar funding costs on open positions
                funding_buffer = max(state.portfolio_equity * config.sizing_defaults.funding_buffer_pct, 1.0)
                usable = state.free_capital - funding_buffer
                if usable <= 0:
                    state.rejections.capital += 1
                    continue
                # Compute affordable margin given fee structure
                if is_perp_this_bar and lev_val > 1.0:
                    affordable = usable / (1.0 + lev_val * fee_rate)
                else:
                    affordable = usable / (1.0 + fee_rate)
                if affordable < config.min_position_usd:
                    state.rejections.capital += 1
                    continue
                # Scale down to affordable size
                pos_usd = affordable
                margin_usd = pos_usd
                if is_perp_this_bar and lev_val > 1.0:
                    notional_usd = pos_usd * lev_val
                else:
                    notional_usd = pos_usd
                entry_fee = notional_usd * fee_rate
                state.partial_fills += 1

            # Entry slippage
            if slippage_model is not None:
                slip_bps = slippage_model.compute_slippage(notional_usd, adv_val, config.base_spread_bps, config.impact_coeff, config.max_slip_bps)
            else:
                slip_bps = compute_slippage_bps(notional_usd, adv_val, config.base_spread_bps, config.impact_coeff, config.max_slip_bps)
            slip = close_val * slip_bps / 10000.0

            # Limit entry price: check if limit fills on this bar
            base_price = close_val
            low_val_entry = sig.low[local_bar]
            high_val_entry = sig.high[local_bar]
            if sig.entry_limit_price is not None and 0 <= local_bar < len(sig.entry_limit_price):
                lp = float(sig.entry_limit_price[local_bar])
                if not np.isnan(lp):
                    if direction == 1 and low_val_entry <= lp:
                        base_price = lp      # Long limit filled
                    elif direction == -1 and high_val_entry >= lp:
                        base_price = lp      # Short limit filled
            entry_price = base_price + slip * direction

            quantity = notional_usd / max(entry_price, 1e-10) * direction

            _sm2 = float(sig.stop_mult[local_bar])
            if _sm2 >= 999:
                initial_risk = 0.0
                stop_price = 0.0
            else:
                initial_risk = _sm2 * atr_val
                stop_price = entry_price - initial_risk if direction == 1 else entry_price + initial_risk

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
                is_perp=is_perp_this_bar,
                fee_rate=fee_rate,
                stop_mult=float(sig.stop_mult[local_bar]),
                trail_mult=float(sig.trail_mult[local_bar]),
                target_mult=sig.target_mult,
                no_stop_bars=sig.no_stop_bars,
                min_hold=sig.min_hold,
                max_hold=sig.max_hold,
                convex_exit=sig.convex_exit,
                rsi_exit_level=sig.rsi_exit_level,
                convex_bar_thresholds=sig.convex_bar_thresholds,
                convex_multipliers=sig.convex_multipliers,
                trail_schedule=sig.trail_schedule,
                time_trail_schedule=sig.time_trail_schedule,
                max_trail_mult_arr=sig.max_trail_mult,
                funding_exit_threshold=sig.funding_exit_threshold,
                breakeven_atr=sig.breakeven_atr,
                chandelier_lookback=sig.chandelier_lookback,
                stop_price=stop_price,
                highest=sig.high[local_bar],
                lowest=sig.low[local_bar],
                initial_risk=initial_risk,
                limit_price=base_price if base_price != close_val else 0.0,
                stop_limit_price=stop_price,
                fill_source="hourly",
            )

            state.total_fees += entry_fee
            state._entry_fees_by_pos[pos.position_id] = entry_fee
            pos.exit_handlers = build_exit_chain(pos, sig, spec, state=state, config=config)
            state.position_manager.open_position(pos)
            if strategy_id not in state.diagnostics.entries_opened:
                state.diagnostics.entries_opened[strategy_id] = 0
            state.diagnostics.entries_opened[strategy_id] += 1


def _process_margin_calls(
    state: SimulationState,
    all_signals: dict[str, dict[str, TokenSignals]],
    bar_maps: dict[str, np.ndarray],
    global_bar: int,
    config: PortfolioConfig,
):
    """Force-close positions when funding erosion pushes free_capital below threshold.

    Closes positions with the worst cumulative_funding/margin_usd ratio first.
    Handles linked carry-pair legs (both legs closed together).
    Skips positions with no market data at the current bar.
    """
    skip_ids: set[str] = set()
    while state.position_manager.open_positions:
        threshold = -max(state.portfolio_equity * 0.05, 1.0)
        if state.free_capital >= threshold:
            break
        # Find the open position with the worst funding ratio
        worst_pos = None
        worst_ratio = -float('inf')
        for pos in state.position_manager.open_positions:
            if pos.position_id in skip_ids:
                continue
            if pos.margin_usd > 0:
                ratio = pos.cumulative_funding / pos.margin_usd
                if ratio > worst_ratio:
                    worst_ratio = ratio
                    worst_pos = pos
        if worst_pos is None:
            break
        # Get market data for this position
        sig = all_signals.get(worst_pos.strategy_id, {}).get(worst_pos.token)
        if sig is None:
            skip_ids.add(worst_pos.position_id)
            continue
        bm = bar_maps.get(worst_pos.token)
        if bm is None:
            skip_ids.add(worst_pos.position_id)
            continue
        lb = int(bm[global_bar])
        if lb == -1 or lb >= sig.n_bars:
            skip_ids.add(worst_pos.position_id)
            continue
        is_sec = (worst_pos.leg == "secondary")
        _up = worst_pos.is_perp if sig.per_bar_is_perp is not None else None
        close_val, _, _, _, adv_val, _ = _get_bar_data(sig, lb, not is_sec, use_perp=_up)
        _close_position(state, worst_pos, global_bar, close_val, "margin_call", adv_val, config)
        state.margin_calls += 1
        # Close linked carry-pair leg if present
        if worst_pos.linked_position_id:
            linked = state.position_manager.get_linked(worst_pos.linked_position_id)
            if linked and linked in state.position_manager.open_positions:
                is_lk_sec = (linked.leg == "secondary")
                _lup = linked.is_perp if sig.per_bar_is_perp is not None else None
                lc, _, _, _, la, _ = _get_bar_data(sig, lb, not is_lk_sec, use_perp=_lup)
                _close_position(state, linked, global_bar, lc, "margin_call", la, config)
                state.margin_calls += 1


def _record_equity_snapshot(
    state: SimulationState,
    all_signals: dict[str, dict[str, TokenSignals]],
    bar_maps: dict[str, np.ndarray],
    global_bar: int,
    timestamp,
):
    """Record mark-to-market equity snapshot and warn on margin deficiency.

    Computes unrealized P&L across all open positions and appends
    (timestamp, equity + unrealized) to state.equity_snapshots.
    Prints a warning to stderr if free_capital is deeply negative after margin calls.
    """
    if state.free_capital < -max(state.portfolio_equity * 0.10, 1.0):
        print(f"WARNING: bar {global_bar}: margin deficient after margin calls "
              f"(free_capital={state.free_capital:.2f}, equity={state.portfolio_equity:.2f})",
              file=sys.stderr)

    mtm_unrealized = _compute_total_unrealized(state, all_signals, bar_maps, global_bar)

    mtm_equity = state.portfolio_equity + mtm_unrealized
    state.max_equity_watermark = max(state.max_equity_watermark, mtm_equity)
    state.equity_snapshots.append((timestamp, mtm_equity))


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
    state.max_equity_watermark = config.capital
    # Pre-resolve slippage models per strategy (cached on state for exit paths)
    for sid, spec in strategy_specs.items():
        state._slippage_models[sid] = get_slippage_model(spec.slippage_model)
    rng = np.random.RandomState(config.seed)

    for global_bar in range(n_bars):
        _process_exits(state, all_signals, bar_maps, global_bar, config, strategy_specs=strategy_specs)
        _process_margin_calls(state, all_signals, bar_maps, global_bar, config)
        if state.pending_entries:  # process pending whether from config or per-bar delay
            _process_pending_entries(state, all_signals, bar_maps, global_bar, config)
        _process_entries(state, all_signals, strategy_specs, bar_maps, global_bar, config, rng)
        _record_equity_snapshot(state, all_signals, bar_maps, global_bar, unified_ts[global_bar])

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
        _use_perp = pos.is_perp if sig.per_bar_is_perp is not None else None
        close_val, _, _, _, adv_val, _ = _get_bar_data(sig, local_bar, not is_secondary, use_perp=_use_perp)

        _close_position(state, pos, last_bar, close_val, "data_end", adv_val, config)
