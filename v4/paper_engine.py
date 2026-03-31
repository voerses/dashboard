"""V4 Paper Trading — Core engine.

PaperPortfolioEngine orchestrates live paper trading by delegating to
v4's battle-tested _process_exits() and _process_entries() functions.

Tick processing order (AC10b):
  fetch_data → append_to_history → recompute_signals →
  process_exits → compute_shadow_rebalance → process_entries →
  record_equity → persist_state → generate_alerts
"""
from __future__ import annotations

import dataclasses
import os
import resource
import sys
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

import json
import tempfile
import time

from v4.paper_config import PaperConfig
from v4.config import PortfolioConfig, StrategySpec
from v4.position import Position, ClosedTrade, PositionManager
import v4.simulator as _sim
from v4.simulator import SimulationState
from v4.signals import precompute_strategy_signals, discover_tokens
from v4.sizing import get_slippage_model
from v4.paper_state import (
    serialize_state, atomic_write_state, append_trades, append_equity,
    _closed_trade_to_dict,
)

from v4.universe import get_fee_rate


@dataclass
class TickResult:
    """Result of processing a single tick."""
    tick_counter: int = 0
    timestamp: str = ""
    entries: int = 0
    exits: int = 0
    open_positions: int = 0
    portfolio_equity: float = 0.0
    mark_to_market_equity: float = 0.0
    error: Optional[str] = None
    alerts: list = field(default_factory=list)
    skipped: bool = False
    peak_rss_mb: float = 0.0
    processing_time_s: float = 0.0


class PaperPortfolioEngine:
    """Live paper trading engine using v4 simulation logic.

    Pool mode: shared SimulationState, strategies size off portfolio_equity * weight.
    Independent mode: separate SimulationState per strategy.
    """

    BACKOFF = [30, 60, 120]  # Retry backoff delays in seconds (AC10d)

    def __init__(self, config: PaperConfig, *, price_monitor=None):
        self.config = config
        self.tick_counter: int = 0
        self._last_known_prices: dict[str, float] = {}
        self._last_known_regimes: dict[str, int] = {}
        self._alerts: list = []
        self._consecutive_failures: int = 0
        self.fetcher = None  # Set by live integration (Task 7)
        self._dynamic_allocator = None  # Initialized lazily on first tick

        # Sentinel stop store (AC2) — only created when sentinel is active
        if config.sentinel_mode != "off":
            from v4.stop_store import StopStore
            self.stop_store = StopStore(state_dir=config.state_dir)

        # Integrated sub-hourly exits via WebSocket (replaces sentinel)
        # Effective resolution = finest non-zero exit_resolution across all strategies.
        # Falls back to portfolio-level config.exit_resolution for backward compat.
        self._candle_aggregator = None
        self._price_monitor = None
        self._owns_price_monitor = False
        strategy_resolutions = [s.exit_resolution for s in config.strategies if s.exit_resolution > 0]
        effective_resolution = min(strategy_resolutions) if strategy_resolutions else getattr(config, 'exit_resolution', 0)
        self._effective_exit_resolution = effective_resolution
        # Build a lookup: strategy_id -> its exit_resolution (0 = hourly only)
        self._strategy_exit_resolution: dict[str, int] = {
            s.strategy_id: s.exit_resolution for s in config.strategies
        }
        # Sub-hourly entry resolution (1m BB cross detection for live trading)
        self._strategy_entry_resolution: dict[str, int] = {
            s.strategy_id: s.entry_resolution for s in config.strategies
        }
        entry_resolutions = [s.entry_resolution for s in config.strategies if s.entry_resolution > 0]
        self._effective_entry_resolution = min(entry_resolutions) if entry_resolutions else 0
        # Pending entry candidates for sub-hourly resolution (keyed by (strategy_id, token))
        self._pending_entry_candidates: dict[tuple[str, str], dict] = {}
        # Unify: create WS infra if EITHER exit or entry needs sub-hourly data
        all_resolutions = [s.exit_resolution for s in config.strategies if s.exit_resolution > 0]
        all_resolutions += [s.entry_resolution for s in config.strategies if s.entry_resolution > 0]
        effective_ws_resolution = min(all_resolutions) if all_resolutions else effective_resolution
        if effective_ws_resolution > 0 and effective_resolution == 0:
            effective_resolution = effective_ws_resolution
        if effective_resolution > 0:
            from v4.candle_aggregator import CandleAggregator
            # Always create own CandleAggregator (resolution is per-engine)
            self._candle_aggregator = CandleAggregator(effective_resolution)
            if price_monitor is not None:
                # Use shared PriceMonitor from runner — don't create our own
                self._price_monitor = price_monitor
                self._owns_price_monitor = False
            else:
                # Create own PriceMonitor (backward compat / dedicated mode)
                from v4.price_monitor import PriceMonitor
                # Derive venue from strategy market types
                markets = {s.market for s in config.strategies}
                if "spot" in markets and "perp" not in markets and "combined" not in markets:
                    venue = "spot"
                else:
                    venue = "perp"
                self._price_monitor = PriceMonitor(
                    callback=self._candle_aggregator.on_price,
                    venue=venue,
                )
                self._owns_price_monitor = True
        # Cache for sub-hourly exit checks (populated after each hourly tick)
        # Keyed by (strategy_id, token) so each strategy gets its own ATR values
        self._cached_bar_data: dict[tuple[str, str], dict] = {}

        if config.mode == "independent":
            self._init_independent_mode()
            self.state = None  # Not used in independent mode (use strategy_states)
        else:
            self.state = SimulationState(initial_capital=config.capital)
            # Pre-resolve slippage models per strategy (cached on state for exit paths)
            for spec in config.strategies:
                self.state._slippage_models[spec.strategy_id] = get_slippage_model(spec.slippage_model)
            self.strategy_states = {}

    def cleanup(self) -> None:
        """Release resources. Disconnects PriceMonitor only if this engine owns it."""
        if self._price_monitor is not None and self._owns_price_monitor:
            try:
                self._price_monitor.disconnect()
            except Exception:
                pass

    def _init_independent_mode(self) -> None:
        """Initialize separate SimulationState for each strategy (AC6b)."""
        self.strategy_states: dict[str, SimulationState] = {}
        for spec in self.config.strategies:
            capital = self.config.capital * spec.weight
            sstate = SimulationState(initial_capital=capital)
            sstate._slippage_models[spec.strategy_id] = get_slippage_model(spec.slippage_model)
            self.strategy_states[spec.strategy_id] = sstate

    # ------------------------------------------------------------------
    # Sentinel stop extraction (AC2, AC17)
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_stop_levels(
        positions: list,
        strategy_specs: dict,
        last_known_atrs: dict,
        carry_strategies: list,
        tick_counter: int,
    ) -> list:
        """Extract StopLevel for each eligible open position.

        Excludes carry strategies and linked positions (combined strategy
        legs cannot be exited independently).
        Computes cb_price, target_price, estimated_liq_price, stop_active.
        """
        from v4.stop_store import StopLevel
        from v4.universe import get_maint_margin_rate

        mmr = get_maint_margin_rate()
        results: list[StopLevel] = []

        for pos in positions:
            # Skip carry strategies
            if pos.strategy_id in carry_strategies:
                continue
            # Skip linked positions (combined legs can't be exited independently)
            if pos.linked_position_id is not None:
                continue

            spec = strategy_specs.get(pos.strategy_id)
            if spec is None:
                continue

            cur_atr = last_known_atrs.get(pos.token, pos.entry_price * 0.02)
            bars_held = tick_counter - pos.entry_bar

            # cb_price: circuit breaker
            cbr = spec.circuit_breaker_r
            if cbr > 0:
                cb_price = pos.entry_price - pos.direction * cbr * pos.initial_risk
            else:
                cb_price = 0.0

            # target_price
            if pos.target_mult < 100:
                target_price = pos.entry_price + pos.direction * pos.target_mult * cur_atr
            else:
                target_price = 0.0

            # estimated_liq_price (AC17) — only for leveraged perps
            if pos.is_perp and pos.leverage > 1.0 and abs(pos.quantity) > 0:
                entry_notional = pos.margin_usd * pos.leverage
                numerator = pos.margin_usd - pos.cumulative_funding - entry_notional * mmr
                estimated_liq_price = pos.entry_price - pos.direction * numerator / abs(pos.quantity)
            else:
                estimated_liq_price = 0.0

            # stop_active
            stop_active = (bars_held >= pos.no_stop_bars) or pos.convex_exit

            # has_trail_schedule
            has_trail_schedule = (pos.trail_schedule is not None or
                                  pos.time_trail_schedule is not None)

            results.append(StopLevel(
                position_id=pos.position_id,
                token=pos.token,
                strategy_id=pos.strategy_id,
                direction=int(pos.direction),
                stop_price=float(pos.stop_price),
                cb_price=float(cb_price),
                target_price=float(target_price),
                estimated_liq_price=float(estimated_liq_price),
                entry_price=float(pos.entry_price),
                margin_usd=float(pos.margin_usd),
                quantity=float(pos.quantity),
                leverage=float(pos.leverage),
                is_perp=bool(pos.is_perp),
                no_stop_bars=int(pos.no_stop_bars),
                bars_held=int(bars_held),
                stop_active=bool(stop_active),
                convex_exit=bool(pos.convex_exit),
                trail_mult=float(pos.trail_mult),
                cur_atr=float(cur_atr),
                highest=float(pos.highest),
                lowest=float(pos.lowest),
                has_trail_schedule=bool(has_trail_schedule),
                chandelier_lookback=int(pos.chandelier_lookback),
                cumulative_funding=float(pos.cumulative_funding),
                fee_rate=float(pos.fee_rate),
            ))

        return results

    def _process_sentinel_exits(self) -> None:
        """Process exit events from the sentinel process.  Only runs in live mode.

        Reads exit_events.jsonl via StopStore (atomic read-and-clear).
        For each event, finds the matching open position and closes it.
        PnL accounting: raw_pnl → realized_pnl, exit_fee → total_fees.
        No ADV slippage — sentinel exit_price is used directly.
        Works in both pool and independent mode (C-1 fix).

        F14 fix: Only process in live mode. In shadow mode, exit_events.jsonl
        is never written (BreachDetector gates on sentinel_mode=="live"), but
        a leftover file from a prior live run could cause unintended exits.
        """
        if self.config.sentinel_mode != "live":
            return

        events = self.stop_store.read_and_clear_exit_events()
        if not events:
            return

        # C-1 fix: Collect all states to search (works in both pool and independent mode)
        # Inline rather than calling _get_all_states() for test compatibility
        mode = getattr(self.config, 'mode', 'pool')
        if mode == "independent" and hasattr(self, 'strategy_states'):
            all_states = list(self.strategy_states.values())
        elif self.state is not None:
            all_states = [self.state]
        else:
            return

        for event in events:
            # Find matching open position across all states
            target_state = None
            target_pos = None
            for st in all_states:
                for p in st.position_manager.open_positions:
                    if p.position_id == event.position_id:
                        target_state = st
                        target_pos = p
                        break
                if target_pos is not None:
                    break

            if target_pos is None or target_state is None:
                # Already closed (idempotent)
                continue

            pm = target_state.position_manager

            # R2-F7 fix: Compute raw PnL with explicit direction branch
            # (matches simulator.py normal exit path for safety with unsigned quantity)
            if target_pos.direction == 1:
                raw_pnl = target_pos.quantity * (event.exit_price - target_pos.entry_price)
            else:
                raw_pnl = abs(target_pos.quantity) * (target_pos.entry_price - event.exit_price)

            # Exit fee from fee_rate (no ADV-based slippage)
            exit_fee = abs(target_pos.quantity) * event.exit_price * target_pos.fee_rate

            # R3-11 fix: Use .pop() to clean up entry fee record (matching normal exit path)
            entry_fees_dict = getattr(target_state, '_entry_fees_by_pos', {})
            entry_fee = entry_fees_dict.pop(target_pos.position_id, 0.0) if isinstance(entry_fees_dict, dict) else 0.0

            # R3-2 fix: Pass net_pnl to close_position (matching normal exit convention)
            net_pnl = raw_pnl - exit_fee - target_pos.cumulative_funding

            # Close position via PositionManager
            pm.close_position(
                pos=target_pos,
                exit_bar=self.tick_counter,
                exit_price=event.exit_price,
                pnl=net_pnl,
                funding_cost=target_pos.cumulative_funding,
                entry_fee=entry_fee,
                exit_fee=exit_fee,
                exit_reason=event.exit_reason,
                exit_timestamp=event.sentinel_timestamp,
            )

            # Update state accounting
            target_state.realized_pnl += raw_pnl
            target_state.total_fees += exit_fee

            # R8-1 fix: Removed H-5 free_capital assignment — free_capital is a derived
            # @property (portfolio_equity - locked_margin), already correct after
            # close_position + realized_pnl/total_fees updates above.

    def _write_stops(self) -> None:
        """Write stops.json for sentinel consumption.  No-op when mode='off'.

        Works in both pool and independent mode (C-1/M-10 fix).
        """
        if self.config.sentinel_mode == "off":
            return

        strategy_specs = self.strategy_specs if hasattr(self, 'strategy_specs') else {
            s.strategy_id: s for s in self.config.strategies
        }
        carry = self.config.carry_strategies

        # Collect positions and ATRs from all states (pool or independent)
        all_positions = []
        merged_atrs: dict[str, float] = {}
        for st in self._get_all_states():
            all_positions.extend(st.position_manager.open_positions)
            merged_atrs.update(getattr(st, 'last_known_atrs', {}))

        stops = self._extract_stop_levels(
            positions=all_positions,
            strategy_specs=strategy_specs,
            last_known_atrs=merged_atrs,
            carry_strategies=carry,
            tick_counter=self.tick_counter,
        )
        self.stop_store.write_stops(stops)

    # ------------------------------------------------------------------
    # Integrated sub-hourly exits
    # ------------------------------------------------------------------

    def process_sub_hourly_exits(
        self, candles: dict[str, tuple[float, float, float]],
    ) -> int:
        """Process price-based exits from completed sub-hourly candles.

        Returns number of positions closed.
        Handles partial profit-taking and trail/stop/target exits.
        """
        if not candles or getattr(self, '_effective_exit_resolution', 0) == 0:
            return 0

        import logging
        logger = logging.getLogger(__name__)

        from v4.minute_exits import check_candle_exits
        from v4.simulator import _partial_close_position

        closed_count = 0
        partial_count = 0
        positions_to_close: list[tuple] = []  # (state, pos, exit_price, exit_reason)
        strategy_specs = {s.strategy_id: s for s in self.config.strategies}

        for st in self._get_all_states():
            for pos in list(st.position_manager.open_positions):
                if pos.token not in candles:
                    continue
                # Skip positions whose strategy is hourly-only (exit_resolution=0)
                if getattr(self, '_strategy_exit_resolution', {}).get(pos.strategy_id, 0) == 0:
                    continue
                # Skip if already queued for closure
                if any(p is pos for _, p, _, _ in positions_to_close):
                    continue

                h, l, c = candles[pos.token]

                # Guard against inf/NaN candle values (corrupted WS feed)
                if not (np.isfinite(h) and np.isfinite(l) and np.isfinite(c)):
                    continue

                # Get cached bar data from last hourly tick (per-strategy)
                bar_data = self._cached_bar_data.get((pos.strategy_id, pos.token))
                if bar_data is None:
                    # No cached data yet (first tick hasn't run) — skip
                    continue

                cur_atr = bar_data.get("atr", 0.0)
                if cur_atr <= 0:
                    cur_atr = abs(pos.entry_price) * 0.02

                bars_held = self.tick_counter - pos.entry_bar

                # Partial profit-taking (mirrors backtest process_minute_exits)
                if (pos.partial_tp_atr > 0.0
                        and not pos.partial_closed
                        and bars_held >= pos.no_stop_bars):
                    d = pos.direction
                    if d == 1:
                        profit_atr = (h - pos.entry_price) / max(cur_atr, 1e-10)
                    else:
                        profit_atr = (pos.entry_price - l) / max(cur_atr, 1e-10)
                    if profit_atr >= pos.partial_tp_atr:
                        adv_val = bar_data.get("adv", 1e6)
                        _partial_close_position(st, pos, self.tick_counter, c, adv_val, self.config)
                        partial_count += 1

                # Get circuit breaker params
                spec = strategy_specs.get(pos.strategy_id)
                cb_r = spec.circuit_breaker_r if spec else 0.0

                # Effective target (use cached bear_target_mult if in bear regime)
                eff_target = pos.target_mult
                bear_target = bar_data.get("bear_target_mult", 0.0)
                regime = bar_data.get("regime", 0)
                if bear_target > 0.0 and regime == 4:
                    eff_target = bear_target

                reason, price = check_candle_exits(
                    pos, h, l, c, cur_atr, bars_held,
                    cb_r, eff_target, pos.initial_risk,
                )

                if reason is not None:
                    positions_to_close.append((st, pos, price, reason))
                    # Close linked position too
                    if pos.linked_position_id:
                        linked = st.position_manager.get_linked(pos.linked_position_id)
                        if linked and not any(p is linked for _, p, _, _ in positions_to_close):
                            # Compute proper exit price for linked leg
                            linked_token = linked.token
                            has_linked_candle = linked_token in candles
                            if has_linked_candle:
                                lh, ll, lc = candles[linked_token]
                            else:
                                # No candle for linked token — use primary close
                                lh, ll, lc = c, c, c
                            linked_bar_data = self._cached_bar_data.get(
                                (linked.strategy_id, linked_token)
                            )
                            if linked_bar_data is not None and has_linked_candle:
                                linked_atr = linked_bar_data.get("atr", 0.0)
                                if linked_atr <= 0:
                                    linked_atr = abs(linked.entry_price) * 0.02
                                linked_bars_held = self.tick_counter - linked.entry_bar
                                linked_spec = strategy_specs.get(linked.strategy_id)
                                linked_cb_r = linked_spec.circuit_breaker_r if linked_spec else 0.0
                                linked_eff_target = linked.target_mult
                                linked_bear_target = linked_bar_data.get("bear_target_mult", 0.0)
                                linked_regime = linked_bar_data.get("regime", 0)
                                if linked_bear_target > 0.0 and linked_regime == 4:
                                    linked_eff_target = linked_bear_target
                                linked_reason, linked_price = check_candle_exits(
                                    linked, lh, ll, lc, linked_atr,
                                    linked_bars_held, linked_cb_r,
                                    linked_eff_target, linked.initial_risk,
                                )
                                exit_price_linked = linked_price if linked_reason else lc
                            else:
                                exit_price_linked = lc
                            positions_to_close.append((st, linked, exit_price_linked, "linked_exit"))

        # Execute closures
        from v4.sizing import compute_slippage_bps

        timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        for st, pos, exit_price, reason in positions_to_close:
            if pos not in st.position_manager.open_positions:
                continue  # Already closed (e.g., linked leg)

            pm = st.position_manager

            # Apply exit slippage (matching backtest _close_position)
            if reason != "liquidation":
                notional = abs(pos.quantity * exit_price)
                bar_data = self._cached_bar_data.get((pos.strategy_id, pos.token))
                exit_adv = bar_data.get("adv", 1e6) if bar_data else 1e6
                effective_adv = exit_adv
                if reason in ("stop", "margin_call"):
                    effective_adv = exit_adv * self.config.stress_adv_multiplier
                _slip_model = st._slippage_models.get(pos.strategy_id)
                if _slip_model is not None:
                    slip_bps = _slip_model.compute_slippage(notional, effective_adv, self.config.base_spread_bps, self.config.impact_coeff, self.config.max_slip_bps)
                else:
                    slip_bps = compute_slippage_bps(notional, effective_adv, self.config.base_spread_bps, self.config.impact_coeff, self.config.max_slip_bps)
                slip = exit_price * slip_bps / 10000.0
                if pos.direction == 1:
                    exit_price -= slip  # Long exit: sell lower
                else:
                    exit_price += slip  # Short exit: buy higher

            entry_fee = getattr(st, '_entry_fees_by_pos', {}).pop(pos.position_id, 0.0) if isinstance(getattr(st, '_entry_fees_by_pos', {}), dict) else 0.0
            exit_fee = abs(pos.quantity) * exit_price * pos.fee_rate

            if pos.direction == 1:
                raw_pnl = pos.quantity * (exit_price - pos.entry_price)
            else:
                raw_pnl = abs(pos.quantity) * (pos.entry_price - exit_price)

            net_pnl = raw_pnl - exit_fee - pos.cumulative_funding

            pm.close_position(
                pos=pos,
                exit_bar=self.tick_counter,
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
            closed_count += 1

        # Always update last known prices from candle closes for fresh MTM
        for token, (_, _, c_price) in candles.items():
            if np.isfinite(c_price):
                self._last_known_prices[token] = c_price

        # Persist state if any exits or partial closes occurred
        if closed_count > 0 or partial_count > 0:
            self._persist_sub_hourly_state(timestamp)
            if closed_count > 0:
                self._update_ws_subscriptions()
            if closed_count > 0:
                logger.info("Sub-hourly exits: %d positions closed", closed_count)
            if partial_count > 0:
                logger.info("Sub-hourly partial TP: %d positions partially closed", partial_count)

        return closed_count

    def process_sub_hourly_entries(
        self, candles: dict[str, tuple[float, float, float]],
    ) -> int:
        """Process pending entry candidates against completed sub-hourly candles.

        Mirrors process_sub_hourly_exits(): for each pending candidate, checks
        if the 1m candle close crosses the limit price. On cross, executes entry
        with slippage applied to the cross close price.

        Constraint checks match the backtest's _process_entries() for parity:
        portfolio limit, strategy limit, max_concurrent, pump filters,
        unrealized PnL sizing adjustment, DD scaling, concentration limit,
        and free capital with scale-down + funding buffer.

        Returns number of entries executed.
        """
        if not candles or self._effective_entry_resolution == 0:
            return 0
        if not self._pending_entry_candidates:
            return 0

        import logging
        logger = logging.getLogger(__name__)

        from v4.sizing import compute_slippage_bps, get_sizing_model
        from v4.config import resolve_sizing
        from v4.universe import get_fee_rate
        from v4.simulator import _dd_size_mult

        entry_count = 0
        strategy_specs = {s.strategy_id: s for s in self.config.strategies}
        executed_keys: list[tuple[str, str]] = []

        for (sid, token), cand in list(self._pending_entry_candidates.items()):
            if token not in candles:
                continue

            h, l, c = candles[token]
            if not np.isfinite(c):
                continue  # NaN/inf candle — skip to avoid corrupting state

            lp = cand["limit_price"]
            direction = cand["direction"]

            # Check if 1m close crosses the limit price
            if direction == 1 and c <= lp:
                continue  # Long: need close > limit
            if direction == -1 and c >= lp:
                continue  # Short: need close < limit

            # Cross detected — execute entry
            spec = strategy_specs.get(sid)
            if spec is None:
                continue

            # Get the state to open position in
            if self.config.mode == "independent":
                st = self.strategy_states.get(sid)
                if st is None:
                    continue
            else:
                st = self.state

            # --- Pump filter Layer 1: range anomaly ---
            if spec.pump_filter_range_threshold > 0:
                pump_range = cand.get("pump_range", 0.0)
                if pump_range > spec.pump_filter_range_threshold:
                    continue

            # --- Pump filter Layer 3: funding z-score (longs only) ---
            if spec.pump_filter_funding_zscore > 0 and direction >= 1:
                f_zscore = cand.get("funding_zscore", float('nan'))
                if not np.isnan(f_zscore) and f_zscore > spec.pump_filter_funding_zscore:
                    continue

            # Portfolio constraints
            if st.position_manager.total_open() >= self.config.max_portfolio_positions:
                continue
            if st.position_manager.count_for_strategy(sid) >= spec.max_positions:
                continue
            max_conc = spec.max_concurrent_per_token
            if len(st.position_manager.find_open_for_token_strategy(token, sid)) >= max_conc:
                continue

            # Compute sizing (matching backtest normal-mode path)
            resolved = resolve_sizing(self.config.sizing_defaults, spec.sizing_overrides)
            sizing_model = get_sizing_model(spec.sizing_model)
            slippage_model = st._slippage_models.get(sid)

            close_val = cand["close_val"]
            atr_val = cand["atr_val"]
            adv_val = cand["adv_val"]
            lev_val = cand["leverage"]
            sm_val = cand["size_multiplier"]
            cap_mult = cand["cap_multiplier"]
            is_perp = cand["is_perp"]

            volatility = atr_val / max(close_val, 1e-10)

            # Unrealized PnL adjustment (constrain-only: losses reduce, gains don't inflate)
            total_unrealized = self._compute_sub_hourly_unrealized(st)

            if self.config.raw_mode:
                strategy_equity = self.config.capital * spec.weight
            else:
                portfolio_eq = st.portfolio_equity
                sizing_eq = max(min(portfolio_eq + total_unrealized, portfolio_eq),
                                portfolio_eq * self.config.sizing_defaults.unrealized_pnl_floor)
                sizing_eq = max(sizing_eq, 0.0)
                if self.config.max_sizing_equity is not None:
                    sizing_eq = min(sizing_eq, self.config.max_sizing_equity)
                strategy_equity = sizing_eq * spec.weight

            # DD scaling overlay
            dd_mult = _dd_size_mult(st, spec.dd_scaling, total_unrealized)
            if dd_mult <= 0:
                continue
            strategy_equity *= dd_mult

            pos_usd = sizing_model.compute_size(
                strategy_equity=strategy_equity,
                rolling_adv=adv_val,
                volatility=volatility,
                edge=cand["edge"],
                size_multiplier=sm_val,
                cap_multiplier=cap_mult,
                max_trade_pct=cand["max_trade_pct"],
                adv_cap_pct=self.config.adv_cap_pct,
                adv_sizing_enabled=spec.adv_sizing_enabled,
                adv_sizing_base=spec.adv_sizing_base,
                adv_sizing_floor=spec.adv_sizing_floor,
                edge_minimum=resolved.edge_minimum,
                target_vol=resolved.target_vol,
                vol_floor=resolved.vol_floor,
                spot_max_equity_pct=resolved.spot_max_equity_pct,
                leverage=lev_val,
                kelly_mult_override=resolved.kelly_mult_override,
                kelly_mult_scale=resolved.kelly_mult_scale,
                cap_pct_override=resolved.cap_pct_override,
                cap_pct_scale=resolved.cap_pct_scale,
                kelly_mult_floor=resolved.kelly_mult_floor,
                kelly_mult_range=resolved.kelly_mult_range,
                cap_pct_floor=resolved.cap_pct_floor,
                cap_pct_range=resolved.cap_pct_range,
                adv_scaling_divisor=resolved.adv_scaling_divisor,
            )

            if pos_usd <= 0 or pos_usd < self.config.min_position_usd:
                continue

            # Leverage
            margin_usd = pos_usd
            if is_perp and lev_val > 1.0:
                notional_usd = pos_usd * lev_val
            else:
                notional_usd = pos_usd

            # ADV cap check
            if pos_usd > adv_val * self.config.adv_cap_pct:
                continue

            # Concentration limit (scale down or reject)
            if not self.config.raw_mode:
                portfolio_eq = st.portfolio_equity
                existing_margin = st.position_manager.total_margin_for_token(token)
                max_for_token = self.config.concentration_limit * portfolio_eq - existing_margin
                if margin_usd > max_for_token:
                    if max_for_token < self.config.min_position_usd:
                        continue
                    pos_usd = max_for_token
                    margin_usd = pos_usd
                    if is_perp and lev_val > 1.0:
                        notional_usd = pos_usd * lev_val
                    else:
                        notional_usd = pos_usd

            # Fee
            if is_perp:
                fee_rate = get_fee_rate(self.config.exchange, "perp", "taker")
            else:
                fee_rate = get_fee_rate(self.config.exchange, "spot", "taker")
            entry_fee = notional_usd * fee_rate

            # Free capital check (scale down with funding buffer, matching backtest)
            if not self.config.raw_mode and st.free_capital < margin_usd + entry_fee:
                funding_buffer = max(st.portfolio_equity * self.config.sizing_defaults.funding_buffer_pct, 1.0)
                usable = st.free_capital - funding_buffer
                if usable <= 0:
                    continue
                if is_perp and lev_val > 1.0:
                    affordable = usable / (1.0 + lev_val * fee_rate)
                else:
                    affordable = usable / (1.0 + fee_rate)
                if affordable < self.config.min_position_usd:
                    continue
                pos_usd = affordable
                margin_usd = pos_usd
                if is_perp and lev_val > 1.0:
                    notional_usd = pos_usd * lev_val
                else:
                    notional_usd = pos_usd
                entry_fee = notional_usd * fee_rate

            # Slippage: apply to the 1m cross close price
            if slippage_model is not None:
                slip_bps = slippage_model.compute_slippage(
                    notional_usd, adv_val, self.config.base_spread_bps,
                    self.config.impact_coeff, self.config.max_slip_bps,
                )
            else:
                slip_bps = compute_slippage_bps(
                    notional_usd, adv_val, self.config.base_spread_bps,
                    self.config.impact_coeff, self.config.max_slip_bps,
                )
            slip = c * slip_bps / 10000.0
            entry_price = c + slip * direction

            quantity = notional_usd / max(entry_price, 1e-10) * direction

            stop_mult = cand["stop_mult"]
            initial_risk = stop_mult * atr_val
            if direction == 1:
                stop_price = entry_price - initial_risk
            else:
                stop_price = entry_price + initial_risk

            timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

            cached_tick = cand["tick_counter"]
            pos = Position(
                position_id=f"{token}:{sid}:{cached_tick}:sub",
                token=token,
                strategy_id=sid,
                leg="primary",
                entry_bar=cached_tick,
                entry_price=entry_price,
                direction=direction,
                quantity=quantity,
                margin_usd=margin_usd,
                leverage=lev_val,
                is_perp=is_perp,
                fee_rate=fee_rate,
                stop_mult=stop_mult,
                trail_mult=cand["trail_mult"],
                target_mult=cand["target_mult"],
                no_stop_bars=cand["no_stop_bars"],
                min_hold=cand["min_hold"],
                max_hold=cand["max_hold"],
                exit_regimes=cand["exit_regimes"],
                convex_exit=cand["convex_exit"],
                rsi_exit_level=cand.get("rsi_exit_level", 999.0),
                regime_exit_min_bars=cand.get("regime_exit_min_bars", 6),
                convex_bar_thresholds=cand.get("convex_bar_thresholds", (48, 12)),
                convex_multipliers=cand.get("convex_multipliers", (2.0, 1.5, 0.3)),
                trail_schedule=cand.get("trail_schedule"),
                time_trail_schedule=cand.get("time_trail_schedule"),
                max_trail_mult_arr=cand.get("max_trail_mult"),
                funding_exit_threshold=cand.get("funding_exit_threshold", 0.0),
                partial_tp_atr=cand.get("partial_tp_atr", 0.0),
                partial_tp_pct=cand.get("partial_tp_pct", 0.5),
                partial_tp_trail=cand.get("partial_tp_trail", 1.5),
                breakeven_atr=cand.get("breakeven_atr", 0.0),
                chandelier_lookback=cand.get("chandelier_lookback", 0),
                stop_price=stop_price,
                highest=cand["high_val"],
                lowest=cand["low_val"],
                initial_risk=initial_risk,
                limit_price=lp,
                limit_placed_at=cand.get("limit_placed_at", ""),
                stop_limit_price=stop_price,
                fill_source="sub_hourly",
            )
            pos.entry_timestamp = timestamp

            # Build exit handler chain immediately (backtest parity: simulator.py:839)
            sig_ref = cand.get("sig_ref")
            if sig_ref is not None:
                from v4.exit_handlers import build_exit_chain
                spec = strategy_specs.get(sid)
                pos.exit_handlers = build_exit_chain(pos, sig_ref, spec, state=st, config=self.config)

            # Update state
            st.total_fees += entry_fee
            st._entry_fees_by_pos[pos.position_id] = entry_fee
            st.position_manager.open_position(pos)
            self._last_known_prices[token] = c  # fresh price for unrealized PnL
            entry_count += 1
            executed_keys.append((sid, token))

        # Remove executed candidates
        for key in executed_keys:
            self._pending_entry_candidates.pop(key, None)

        # Persist state if any entries occurred
        if entry_count > 0:
            ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            self._persist_sub_hourly_state(ts)
            self._update_ws_subscriptions()
            logger.info("Sub-hourly entries: %d positions opened", entry_count)

        return entry_count

    def _compute_sub_hourly_unrealized(self, st: "SimulationState") -> float:
        """Compute total unrealized P&L for sub-hourly entry sizing.

        Uses last known prices (updated from hourly tick + sub-hourly candles).
        Mirrors simulator's _compute_total_unrealized but uses cached prices
        instead of signal arrays (which aren't available between ticks).
        """
        total = 0.0
        for pos in st.position_manager.open_positions:
            price = self._last_known_prices.get(pos.token)
            if price is None:
                continue
            total += pos.quantity * (price - pos.entry_price)
        return total

    def _persist_sub_hourly_state(self, timestamp: str) -> None:
        """Persist state after sub-hourly exits or entries (trades + state.json only).

        Does NOT recompute signals or process entries (that's hourly only).
        """
        state_dir = self.config.state_dir
        os.makedirs(state_dir, exist_ok=True)

        # Write new trades (staged-flush pattern: R7-I1)
        trades_path = os.path.join(state_dir, "trades.jsonl")
        new_closed = []
        for st in self._get_all_states():
            new_closed.extend(st.position_manager.closed_trades)
        if not hasattr(self, '_flushed_position_ids'):
            self._flushed_position_ids = set()
        unflushed = [t for t in new_closed if t.position_id not in self._flushed_position_ids]
        staged_ids: set = set()
        if unflushed:
            with open(trades_path, "a") as f:
                for trade in unflushed:
                    f.write(json.dumps(_closed_trade_to_dict(trade, tick=self.tick_counter)) + "\n")
                f.flush()
                os.fsync(f.fileno())
            staged_ids = {t.position_id for t in unflushed}

        # Write state.json atomically
        state_path = os.path.join(state_dir, "state.json")
        shadow = getattr(self, 'shadow', None)
        shadow_pools = {
            "spot_funds": shadow.spot_funds_shadow if shadow else 0.0,
            "perp_funds": shadow.perp_funds_shadow if shadow else 0.0,
            "spot_deployed": shadow.spot_deployed if shadow else 0.0,
            "perp_deployed": shadow.perp_deployed if shadow else 0.0,
        }
        if self.state is not None:
            atomic_write_state(
                self.state, self.tick_counter, timestamp, state_path,
                shadow_pools=shadow_pools,
                last_known_prices=dict(self._last_known_prices),
                last_known_regimes=dict(self._last_known_regimes),
            )
        else:
            from v4.paper_state import serialize_engine_state
            data = serialize_engine_state(
                dict(self.strategy_states), self.tick_counter, timestamp,
                mode="independent",
                shadow_pools=shadow_pools,
                last_known_prices=dict(self._last_known_prices),
                last_known_regimes=dict(self._last_known_regimes),
            )
            import tempfile as _tmpfile
            fd, tmp = _tmpfile.mkstemp(dir=state_dir, suffix=".tmp")
            os.close(fd)
            with open(tmp, "w") as f:
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.rename(tmp, state_path)

        # Promote staged position IDs after state.json commit (R7-I1 pattern)
        self._flushed_position_ids.update(staged_ids)

        # Append equity snapshot so sub-hourly changes are tracked
        equity_path = os.path.join(state_dir, "equity.csv")
        portfolio_eq = self._aggregate_portfolio_equity()
        mtm_eq = self._compute_mark_to_market()
        all_states = self._get_all_states()
        free_cap = sum(s.free_capital for s in all_states) if all_states else 0.0
        append_equity(
            equity_path,
            timestamp=timestamp,
            tick=self.tick_counter,
            portfolio_equity=portfolio_eq,
            mark_to_market_equity=mtm_eq,
            free_capital=free_cap,
            open_positions=self._aggregate_open_positions(),
            spot_shadow_free=shadow_pools.get("spot_funds", 0.0),
            perp_shadow_free=shadow_pools.get("perp_funds", 0.0),
            spot_deployed=shadow_pools.get("spot_deployed", 0.0),
            perp_deployed=shadow_pools.get("perp_deployed", 0.0),
        )

    def _cache_bar_data(
        self,
        all_signals: dict[str, dict],
        bar_maps: dict[str, np.ndarray],
    ) -> None:
        """Cache ATR/regime/bear_target_mult per (strategy_id, token) for sub-hourly exit checks.

        Called at the end of each hourly tick so sub-hourly exits between ticks
        have access to the latest hourly bar data.  Keyed by (strategy_id, token)
        so each strategy gets its own ATR values.
        """
        from v4.simulator import _get_bar_data
        from v4.signals import TokenSignals

        self._cached_bar_data.clear()
        for sid, token_sigs in all_signals.items():
            for token, sig in token_sigs.items():
                bm = bar_maps.get(token)
                if bm is None:
                    continue
                local_bar = int(bm[self.tick_counter]) if self.tick_counter < len(bm) else -1
                if local_bar == -1 or local_bar >= sig.n_bars:
                    continue
                try:
                    use_perp = None
                    if sig.per_bar_is_perp is not None:
                        use_perp = bool(sig.per_bar_is_perp[local_bar])
                    _, _, _, atr_val, adv_val, _ = _get_bar_data(sig, local_bar, True, use_perp=use_perp)
                except Exception:
                    continue
                self._cached_bar_data[(sid, token)] = {
                    "atr": float(atr_val) if not np.isnan(atr_val) else 0.0,
                    "adv": float(adv_val) if not np.isnan(adv_val) else 0.0,
                    "regime": int(sig.regime[local_bar]) if sig.regime is not None and local_bar < len(sig.regime) else 0,
                    "bear_target_mult": sig.bear_target_mult if hasattr(sig, 'bear_target_mult') else 0.0,
                }

    def _cache_entry_candidates(
        self,
        all_signals: dict[str, dict],
        strategy_specs: dict[str, "StrategySpec"],
        bar_maps: dict[str, np.ndarray],
    ) -> None:
        """Cache unfilled entry candidates for sub-hourly 1m resolution.

        After each hourly tick, extracts pending entry candidates — tokens where
        entry_limit_price is set but the hourly bar didn't fill the limit.
        These are monitored via WebSocket 1m candles between hourly ticks.
        Candidates are discarded at the next hourly tick (fresh cache each tick).
        """
        import logging
        logger = logging.getLogger(__name__)
        self._pending_entry_candidates.clear()
        _entry_signals_count = 0
        _filled_on_hourly = 0

        for sid, token_sigs in all_signals.items():
            if self._strategy_entry_resolution.get(sid, 0) == 0:
                continue
            spec = strategy_specs.get(sid)
            if spec is None:
                continue
            # Skip combined strategies (require atomic two-leg entry)
            if spec.market == "combined":
                continue

            for token, sig in token_sigs.items():
                bm = bar_maps.get(token)
                if bm is None:
                    continue
                local_bar = int(bm[self.tick_counter]) if self.tick_counter < len(bm) else -1
                if local_bar == -1 or local_bar >= sig.n_bars:
                    continue

                # Must have entry signal AND entry_limit_price set (BB threshold)
                if not sig.entry_mask[local_bar]:
                    continue
                if sig.entry_limit_price is None:
                    continue
                if local_bar >= len(sig.entry_limit_price):
                    continue
                lp = float(sig.entry_limit_price[local_bar])
                if np.isnan(lp):
                    continue

                direction = int(sig.direction[local_bar])
                if direction == 0:
                    continue

                _entry_signals_count += 1

                # Check if the hourly bar already filled the limit
                h_val = float(sig.high[local_bar])
                l_val = float(sig.low[local_bar])
                if direction == 1 and l_val <= lp:
                    _filled_on_hourly += 1
                    continue  # Long limit already filled on hourly bar
                if direction == -1 and h_val >= lp:
                    _filled_on_hourly += 1
                    continue  # Short limit already filled on hourly bar

                # Check if position already exists for this token+strategy
                # Also skip if the hourly path already entered this tick (prevent double-entry)
                skip = False
                for st in self._get_all_states():
                    open_for_ts = st.position_manager.find_open_for_token_strategy(token, sid)
                    max_conc = spec.max_concurrent_per_token
                    if len(open_for_ts) >= max_conc:
                        skip = True
                        break
                    # Check if any were just opened this tick (hourly already entered)
                    if any(p.entry_bar == self.tick_counter for p in open_for_ts):
                        skip = True
                        break
                if not skip:
                    # No state had a blocking position — cache candidate
                    # Pre-extract signal data for entry execution
                    close_val = float(sig.close[local_bar])
                    atr_val = float(sig.atr[local_bar])
                    if np.isnan(atr_val):
                        atr_val = close_val * 0.02
                    # Spot ATR for pump filter (backtest runs pump check before venue routing)
                    spot_atr_val = atr_val
                    adv_val = float(sig.rolling_adv[local_bar])
                    lev_val = float(sig.leverage[local_bar])
                    sm_val = float(sig.size_multiplier[local_bar])
                    cap_mult = float(sig.cap_multiplier[local_bar])
                    is_perp = sig.is_perp_primary
                    if sig.per_bar_is_perp is not None:
                        is_perp = bool(sig.per_bar_is_perp[local_bar])
                        if is_perp and sig.perp_close is not None:
                            close_val = float(sig.perp_close[local_bar])
                            atr_val = float(sig.perp_atr[local_bar])
                            if np.isnan(atr_val):
                                atr_val = close_val * 0.02
                            adv_val = float(sig.perp_rolling_adv[local_bar])

                    self._pending_entry_candidates[(sid, token)] = {
                        "limit_price": lp,
                        "direction": direction,
                        "tick_counter": self.tick_counter,
                        "limit_placed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "close_val": close_val,
                        "atr_val": atr_val,
                        "adv_val": adv_val,
                        "leverage": lev_val,
                        "size_multiplier": sm_val,
                        "cap_multiplier": cap_mult,
                        "is_perp": is_perp,
                        "sig_ref": sig,  # For build_exit_chain at sub-hourly entry
                        "stop_mult": float(sig.stop_mult[local_bar]),
                        "trail_mult": float(sig.trail_mult[local_bar]),
                        "target_mult": sig.target_mult,
                        "no_stop_bars": sig.no_stop_bars,
                        "min_hold": sig.min_hold,
                        "max_hold": sig.max_hold,
                        "exit_regimes": sig.exit_regimes,
                        "convex_exit": sig.convex_exit,
                        "edge": sig.edge,
                        "max_trade_pct": sig.max_trade_pct,
                        "high_val": h_val,
                        "low_val": l_val,
                        # Exit handler parameters (backtest parity)
                        "rsi_exit_level": sig.rsi_exit_level,
                        "regime_exit_min_bars": sig.regime_exit_min_bars,
                        "convex_bar_thresholds": sig.convex_bar_thresholds,
                        "convex_multipliers": sig.convex_multipliers,
                        "trail_schedule": sig.trail_schedule,
                        "time_trail_schedule": sig.time_trail_schedule,
                        "max_trail_mult": sig.max_trail_mult,
                        "funding_exit_threshold": sig.funding_exit_threshold,
                        "partial_tp_atr": sig.partial_tp_atr,
                        "partial_tp_pct": sig.partial_tp_pct,
                        "partial_tp_trail": sig.partial_tp_trail,
                        "breakeven_atr": sig.breakeven_atr,
                        "chandelier_lookback": sig.chandelier_lookback,
                        # Pump filter data (cached for constraint checks)
                        # Use spot ATR to match backtest (pump filter runs before venue routing)
                        "pump_range": (h_val - l_val) / max(spot_atr_val, 1e-10),
                        "funding_zscore": (
                            float(sig.funding_zscore[local_bar])
                            if sig.funding_zscore is not None and local_bar < len(sig.funding_zscore)
                            else float('nan')
                        ),
                    }

        n_cached = len(self._pending_entry_candidates)
        if _entry_signals_count > 0 or n_cached > 0:
            logger.info(
                "Entry candidates: %d signals this bar, %d filled on hourly, "
                "%d cached for WS 1m resolution (%s)",
                _entry_signals_count, _filled_on_hourly, n_cached,
                ", ".join(t for _, t in self._pending_entry_candidates) if n_cached else "none",
            )

    def _update_ws_subscriptions(self) -> None:
        """Update WebSocket subscriptions to match current open positions.

        Also includes tokens with pending entry candidates for sub-hourly
        entry resolution.

        No-op when engine does not own its PriceMonitor — the runner manages
        subscriptions centrally to prevent clobbering other engines' tokens.
        """
        if self._price_monitor is None or not self._owns_price_monitor:
            return
        open_tokens = set()
        for st in self._get_all_states():
            for pos in st.position_manager.open_positions:
                open_tokens.add(pos.token)
        # Include tokens with pending entry candidates
        for (sid, token) in self._pending_entry_candidates:
            open_tokens.add(token)
        self._price_monitor.update_subscriptions(open_tokens)
        if self._candle_aggregator:
            self._candle_aggregator.update_tokens(open_tokens)

    # ------------------------------------------------------------------
    # Bar maps
    # ------------------------------------------------------------------

    def _build_bar_maps(
        self,
        all_signals: dict[str, dict],
        tick_counter: int,
    ) -> dict[str, np.ndarray]:
        """Build trivial bar_maps for paper trading.

        For each token, bar_maps[token] maps global_bar → local_bar.
        In paper mode:
          - bar_maps[token][tick_counter] = sig.n_bars - 1 (latest bar)
          - bar_maps[token][tick_counter-1] = sig.n_bars - 2 (prev-tick safety)
        """
        # Collect all tokens across strategies
        token_n_bars: dict[str, int] = {}
        for sid, token_sigs in all_signals.items():
            for token, sig in token_sigs.items():
                n = getattr(sig, 'n_bars', 0)
                if token not in token_n_bars or n > token_n_bars[token]:
                    token_n_bars[token] = n

        bar_maps: dict[str, np.ndarray] = {}
        for token, n_bars in token_n_bars.items():
            # Allocate array covering [0..tick_counter]
            size = tick_counter + 1
            bm = np.full(size, -1, dtype=np.int32)

            # Map current tick to last signal bar
            local_bar = n_bars - 1
            if tick_counter < size:
                bm[tick_counter] = local_bar

            # Map previous tick for safety (exit processing may look back)
            if tick_counter > 0 and local_bar > 0:
                bm[tick_counter - 1] = local_bar - 1

            bar_maps[token] = bm

        return bar_maps

    # ------------------------------------------------------------------
    # Disappeared token handling
    # ------------------------------------------------------------------

    def _get_all_states(self) -> list:
        """Return list of all SimulationState objects (works in both modes)."""
        if self.config.mode == "independent":
            return list(self.strategy_states.values())
        return [self.state]

    def _get_state_for_position(self, pos) -> "SimulationState":
        """Return the SimulationState that owns this position."""
        if self.config.mode == "independent":
            return self.strategy_states[pos.strategy_id]
        return self.state

    def _handle_disappeared_tokens(
        self,
        all_signals: dict[str, dict],
        timestamp: str = "",
    ) -> None:
        """Pre-scan for tokens with open positions that are no longer in signals.

        Force-close at last known price with exit_reason='data_end'.
        """
        # Collect all tokens in current signals
        current_tokens: set[str] = set()
        for sid, token_sigs in all_signals.items():
            current_tokens.update(token_sigs.keys())

        # Find positions for tokens not in current signals (across all states)
        positions_to_close = []
        for st in self._get_all_states():
            positions_to_close.extend(
                pos for pos in list(st.position_manager.open_positions)
                if pos.token not in current_tokens
            )

        closed_ids: set = set()
        for pos in positions_to_close:
            # Skip if already closed (e.g., secondary leg closed as linked to primary)
            if pos.position_id in closed_ids:
                continue

            # Get exit price: last known or fallback to entry price
            exit_price = self._last_known_prices.get(pos.token, pos.entry_price)

            # Close position — match simulator._close_position accounting:
            #   realized_pnl += raw_pnl  (NO fees, NO funding — those are tracked separately)
            #   total_fees += exit_fee
            #   funding already in total_funding from bar-by-bar accrual
            st = self._get_state_for_position(pos)
            entry_fee = st._entry_fees_by_pos.pop(pos.position_id, 0.0)
            exit_fee = abs(pos.quantity * exit_price) * pos.fee_rate
            raw_pnl = pos.quantity * (exit_price - pos.entry_price)
            net_pnl = raw_pnl - exit_fee - pos.cumulative_funding  # for ClosedTrade record

            st.position_manager.close_position(
                pos,
                exit_bar=self.tick_counter,
                exit_price=exit_price,
                pnl=net_pnl,
                funding_cost=pos.cumulative_funding,
                entry_fee=entry_fee,
                exit_fee=exit_fee,
                exit_reason="data_end",
                exit_timestamp=timestamp,
            )

            st.realized_pnl += raw_pnl
            st.total_fees += exit_fee
            closed_ids.add(pos.position_id)

            # Also close linked position if exists
            if pos.linked_position_id:
                linked = st.position_manager.get_linked(pos.linked_position_id)
                if linked and linked.position_id not in closed_ids:
                    linked_st = self._get_state_for_position(linked)
                    linked_exit_price = self._last_known_prices.get(
                        linked.token, linked.entry_price
                    )
                    linked_entry_fee = linked_st._entry_fees_by_pos.pop(
                        linked.position_id, 0.0
                    )
                    linked_exit_fee = abs(linked.quantity * linked_exit_price) * linked.fee_rate
                    linked_raw_pnl = linked.quantity * (linked_exit_price - linked.entry_price)
                    linked_net_pnl = (linked_raw_pnl
                                      - linked_exit_fee - linked.cumulative_funding)

                    linked_st.position_manager.close_position(
                        linked,
                        exit_bar=self.tick_counter,
                        exit_price=linked_exit_price,
                        pnl=linked_net_pnl,
                        funding_cost=linked.cumulative_funding,
                        entry_fee=linked_entry_fee,
                        exit_fee=linked_exit_fee,
                        exit_reason="data_end",
                        exit_timestamp=timestamp,
                    )
                    linked_st.realized_pnl += linked_raw_pnl
                    linked_st.total_fees += linked_exit_fee
                    closed_ids.add(linked.position_id)

            # Fire alert
            self._alerts.append(
                f"Token disappeared: {pos.token} — position {pos.position_id} force-closed "
                f"at ${exit_price:.2f} (data_end)"
            )

    # ------------------------------------------------------------------
    # Strategy equity
    # ------------------------------------------------------------------

    def _get_strategy_equity(self, strategy_id: str) -> float:
        """Get strategy equity for sizing (AC6).

        Pool mode: portfolio_equity * strategy_weight
        Independent mode: strategy's own state equity
        """
        if self.config.mode == "independent":
            return self.strategy_states[strategy_id].portfolio_equity

        # Pool mode: find the strategy weight
        for spec in self.config.strategies:
            if spec.strategy_id == strategy_id:
                return self.state.portfolio_equity * spec.weight

        return 0.0

    # ------------------------------------------------------------------
    # Emergency close
    # ------------------------------------------------------------------

    def _emergency_close_all(self) -> None:
        """Emergency close all open positions at last known prices."""
        emergency_ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        # Collect all positions across all states (works in both modes)
        positions_to_close = []
        for st in self._get_all_states():
            positions_to_close.extend(list(st.position_manager.open_positions))

        for pos in positions_to_close:
            st = self._get_state_for_position(pos)
            exit_price = self._last_known_prices.get(pos.token, pos.entry_price)
            entry_fee = st._entry_fees_by_pos.pop(pos.position_id, 0.0)
            exit_fee = abs(pos.quantity * exit_price) * pos.fee_rate
            raw_pnl = pos.quantity * (exit_price - pos.entry_price)
            # net_pnl for ClosedTrade record only (not for realized_pnl)
            net_pnl = raw_pnl - exit_fee - pos.cumulative_funding

            st.position_manager.close_position(
                pos,
                exit_bar=self.tick_counter,
                exit_price=exit_price,
                pnl=net_pnl,
                funding_cost=pos.cumulative_funding,
                entry_fee=entry_fee,
                exit_fee=exit_fee,
                exit_reason="emergency",
                exit_timestamp=emergency_ts,
            )
            # Match simulator._close_position: raw_pnl to realized, fees tracked separately
            st.realized_pnl += raw_pnl
            st.total_fees += exit_fee

        self._alerts.append("EMERGENCY: All positions closed due to invariant violation")

    def _persist_emergency_state(self) -> None:
        """Persist state after emergency close (R4-C1).

        Writes trades.jsonl and state.json so emergency closures survive restart.
        Uses the same crash-safe ordering as _tick_internal: trades before state.
        """
        state_dir = self.config.state_dir
        os.makedirs(state_dir, exist_ok=True)
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        # Write emergency-closed trades to trades.jsonl
        trades_path = os.path.join(state_dir, "trades.jsonl")
        new_closed = []
        for st in self._get_all_states():
            new_closed.extend(st.position_manager.closed_trades)
        if not hasattr(self, '_flushed_position_ids'):
            self._flushed_position_ids = set()
        unflushed = [t for t in new_closed if t.position_id not in self._flushed_position_ids]
        if unflushed:
            with open(trades_path, "a") as f:
                for trade in unflushed:
                    f.write(json.dumps(_closed_trade_to_dict(trade, tick=self.tick_counter)) + "\n")
                f.flush()
                os.fsync(f.fileno())
            self._flushed_position_ids.update(t.position_id for t in unflushed)

        # Write state.json (atomic)
        state_path = os.path.join(state_dir, "state.json")
        shadow = getattr(self, 'shadow', None)
        shadow_pools = {
            "spot_funds": shadow.spot_funds_shadow if shadow else 0.0,
            "perp_funds": shadow.perp_funds_shadow if shadow else 0.0,
            "spot_deployed": shadow.spot_deployed if shadow else 0.0,
            "perp_deployed": shadow.perp_deployed if shadow else 0.0,
        }
        if self.state is not None:
            atomic_write_state(
                self.state, self.tick_counter, timestamp, state_path,
                shadow_pools=shadow_pools,
                last_known_prices=dict(self._last_known_prices),
                last_known_regimes=dict(self._last_known_regimes),
            )
        else:
            from v4.paper_state import serialize_engine_state
            data = serialize_engine_state(
                dict(self.strategy_states), self.tick_counter, timestamp,
                mode="independent", shadow_pools=shadow_pools,
                last_known_prices=dict(self._last_known_prices),
                last_known_regimes=dict(self._last_known_regimes),
            )
            import tempfile as _tmpfile
            fd, tmp = _tmpfile.mkstemp(dir=state_dir, suffix=".tmp")
            os.close(fd)
            with open(tmp, "w") as f:
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.rename(tmp, state_path)

    # ------------------------------------------------------------------
    # Tick processing
    # ------------------------------------------------------------------

    def _process_exits_for_tick(
        self,
        all_signals: dict,
        bar_maps: dict,
    ) -> None:
        """Delegate exit processing to v4 simulator."""
        # Clear exit_handlers so they rebuild with fresh sig each tick.
        # In the backtest, sig is a single long-lived object (arrays cover all bars).
        # In paper mode, precompute_strategy_signals() creates NEW TokenSignals each
        # tick with +1 bar. Handlers holding old sig refs would IndexError on
        # RSIExitHandler, MeanTargetHandler, or chandelier lookback.
        for st in self._get_all_states():
            for pos in st.position_manager.open_positions:
                pos.exit_handlers = []

        strategy_specs = {s.strategy_id: s for s in self.config.strategies}
        if self.config.mode == "independent":
            for sid, sstate in self.strategy_states.items():
                sid_signals = {sid: all_signals.get(sid, {})}
                _sim._process_exits(sstate, sid_signals, bar_maps, self.tick_counter, self.config, strategy_specs=strategy_specs)
        else:
            _sim._process_exits(self.state, all_signals, bar_maps, self.tick_counter, self.config, strategy_specs=strategy_specs)

    def _process_entries_for_tick(
        self,
        all_signals: dict,
        strategy_specs: dict,
        bar_maps: dict,
    ) -> None:
        """Delegate entry processing to v4 simulator with per-bar RNG seeding (AC9c)."""
        rng = np.random.RandomState(self.config.seed + self.tick_counter)
        if self.config.mode == "independent":
            for sid, sstate in self.strategy_states.items():
                sid_signals = {sid: all_signals.get(sid, {})}
                if sid in strategy_specs:
                    orig_spec = strategy_specs[sid]
                    # Weight=1.0 because capital was already pre-split in _init_independent_mode.
                    # Passing the original weight would double-apply it (portfolio_equity already
                    # reflects weight-scaled initial_capital, and _process_entries multiplies
                    # by spec.weight again).
                    # Use dataclasses.replace to preserve ALL fields (including ADV sizing).
                    spec_independent = dataclasses.replace(orig_spec, weight=1.0)
                    sid_specs = {sid: spec_independent}
                else:
                    sid_specs = {}
                _sim._process_entries(
                    sstate, sid_signals, sid_specs,
                    bar_maps, self.tick_counter, self.config, rng,
                )
        else:
            _sim._process_entries(
                self.state, all_signals, strategy_specs,
                bar_maps, self.tick_counter, self.config, rng,
            )

    def _process_margin_calls_for_tick(self, all_signals, bar_maps):
        """Run margin-call logic for the current tick."""
        if self.config.mode == "independent":
            for sid, sstate in self.strategy_states.items():
                sid_signals = {sid: all_signals.get(sid, {})}
                _sim._process_margin_calls(sstate, sid_signals, bar_maps, self.tick_counter, self.config)
        else:
            _sim._process_margin_calls(self.state, all_signals, bar_maps, self.tick_counter, self.config)

    def _tick_internal_with_signals(
        self,
        all_signals: dict,
        strategy_specs: dict,
        bar_maps: dict,
    ) -> None:
        """Core tick processing with pre-computed signals.

        Order (AC10b): sentinel_exits → exits → margin_calls → shadow_rebalance → entries → equity snapshot
        """
        # Sentinel exits run first (AC11)
        if self.config.sentinel_mode != "off":
            self._process_sentinel_exits()
        self._process_exits_for_tick(all_signals, bar_maps)
        self._process_margin_calls_for_tick(all_signals, bar_maps)
        # Shadow rebalance would run here (between exits and entries)
        if not strategy_specs:
            strategy_specs = {s.strategy_id: s for s in self.config.strategies}
        self._process_entries_for_tick(all_signals, strategy_specs, bar_maps)

        # Cache bar data for sub-hourly exit checks between hourly ticks
        if getattr(self, '_effective_exit_resolution', 0) > 0:
            self._cache_bar_data(all_signals, bar_maps)

        # Cache entry candidates for sub-hourly entry resolution
        if getattr(self, '_effective_entry_resolution', 0) > 0:
            self._cache_entry_candidates(all_signals, strategy_specs, bar_maps)

        # Update WS subscriptions (covers both exit positions and entry candidates)
        if getattr(self, '_effective_exit_resolution', 0) > 0 or getattr(self, '_effective_entry_resolution', 0) > 0:
            self._update_ws_subscriptions()

    def _tick_internal(self, bar_timestamp=None) -> None:
        """Full tick processing: fetch data, recompute signals, process.

        Pipeline (AC10b):
          1. Fetch live data via self.fetcher + append to parquet
          2. Discover tokens from parquet cache
          3. Call precompute_strategy_signals() per strategy
          4. Build bar_maps
          5. Handle disappeared tokens
          6. Call _tick_internal_with_signals()
          7. Update last known prices
          8. Compute mark-to-market equity + populate equity_history
          9. Persist state (state.json, equity.csv, shadow_pools)
        """
        import logging
        logger = logging.getLogger(__name__)

        timestamp = bar_timestamp or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        # --- Step 1: Fetch live data + append to parquet ---
        if self.fetcher is not None:
            # Discover which tokens to fetch for each market
            all_tokens_to_fetch: set[str] = set()
            for spec in self.config.strategies:
                try:
                    tokens = discover_tokens(spec.market)
                    all_tokens_to_fetch.update(tokens)
                except Exception:
                    pass  # No parquet data yet — will handle below

            fetch_ok = 0
            fetch_err = 0
            bars_appended = 0
            funding_merged = 0
            for token in all_tokens_to_fetch:
                for market in ("spot", "perp"):
                    try:
                        bars = self.fetcher.fetch_ohlcv(token, market, limit=10)
                        closed = self.fetcher.filter_closed_bars(bars)
                        if closed:
                            self.fetcher.append_to_parquet(token, market, closed)
                            bars_appended += len(closed)
                        fetch_ok += 1
                    except Exception as e:
                        fetch_err += 1
                        if fetch_err <= 3:  # Log first 3 errors
                            logger.warning("Fetch %s/%s failed: %s", token, market, e)

                # Fetch and merge funding rates for perp
                try:
                    rates = self.fetcher.fetch_funding_rates(token, limit=10)
                    if rates and hasattr(self.fetcher, 'merge_funding_into_parquet'):
                        self.fetcher.merge_funding_into_parquet(token, rates)
                        funding_merged += 1
                except Exception as e:
                    if fetch_err <= 3:
                        logger.warning("Funding %s failed: %s", token, e)

            logger.info("Data fetch: %d ok, %d err, %d bars appended, %d funding merged",
                        fetch_ok, fetch_err, bars_appended, funding_merged)
        else:
            logger.debug("No fetcher configured — using existing parquet data")

        # --- Step 2-3: Discover tokens + precompute signals per strategy ---
        all_signals: dict[str, dict] = {}
        strategy_specs = {s.strategy_id: s for s in self.config.strategies}

        any_tokens_found = False
        for spec in self.config.strategies:
            tokens = discover_tokens(spec.market)
            if tokens:
                any_tokens_found = True
            live_bar = self.tick_counter if self._strategy_entry_resolution.get(spec.strategy_id, 0) > 0 else -1
            sigs = precompute_strategy_signals(spec, tokens, self.config, self.config.lookback_months, live_bar=live_bar)
            all_signals[spec.strategy_id] = sigs

        if not any_tokens_found:
            logger.warning("No parquet cache data found — cold start or missing data directory")

        # Timestamp: use wall-clock time (set on line 446) for precise equity.csv
        # and trade entry timestamps. Previously this was overridden with the
        # hourly bar close time as a temp fix for backwards-tick issues caused by
        # the ablation tick-counter reset — that is no longer needed.

        # --- Step 4: Build bar_maps ---
        bar_maps = self._build_bar_maps(all_signals, self.tick_counter)

        # --- Step 5: Handle disappeared tokens ---
        self._handle_disappeared_tokens(all_signals, timestamp=timestamp)

        # --- Step 5b: Dynamic weight adjustment (if enabled) ---
        self._apply_dynamic_weights(all_signals, bar_maps)

        # --- Step 6: Process exits → entries ---
        # Note: walk-forward mask is already applied by precompute_strategy_signals
        self._tick_internal_with_signals(all_signals, strategy_specs, bar_maps)

        # --- Step 6b: Stamp entry_timestamp on ALL positions missing it ---
        # New positions (entry_bar == tick_counter) get the current timestamp.
        # Pre-existing positions that were never stamped (e.g., created before
        # this code existed) get backfilled with the current timestamp so the
        # dashboard shows *something* rather than "—".
        for st in self._get_all_states():
            for pos in st.position_manager.open_positions:
                if not pos.entry_timestamp:
                    pos.entry_timestamp = timestamp

        # --- Step 6c: Stamp exit_timestamp on newly closed trades missing it ---
        # Simulator-driven exits don't have access to wall-clock time, so we
        # stamp them here. Sentinel exits already have exit_timestamp set from
        # event.sentinel_timestamp and are skipped.
        for st in self._get_all_states():
            for trade in st.position_manager.closed_trades:
                if not trade.exit_timestamp:
                    trade.exit_timestamp = timestamp

        # --- Step 7: Update last known prices ---
        self._update_last_known_prices(all_signals, bar_maps)

        # --- Step 8: Compute equity + populate equity_history ---
        portfolio_eq = self._aggregate_portfolio_equity()
        mtm_eq = self._compute_mark_to_market()

        if not hasattr(self, 'equity_history'):
            self.equity_history = []
        self.equity_history.append({
            "timestamp": timestamp,
            "portfolio_equity": portfolio_eq,
            "mark_to_market_equity": mtm_eq,
        })
        # R7-I5 fix: cap equity_history to prevent unbounded memory growth
        # in long-running daemon mode (720 entries = 30 days of hourly ticks)
        _MAX_EQUITY_HISTORY = 720
        if len(self.equity_history) > _MAX_EQUITY_HISTORY:
            self.equity_history = self.equity_history[-_MAX_EQUITY_HISTORY:]

        # --- Step 9: Persist state ---
        # C5 fix: increment tick_counter BEFORE persisting so state.json
        # records the NEXT expected tick. On crash-recovery, we restore to
        # the next tick and don't re-process the current one.
        self.tick_counter += 1
        # R5-I1 fix: track whether state.json commit point was reached.
        # If exception occurs AFTER commit, tick_counter should NOT be rolled back.
        self._state_committed = False

        state_dir = self.config.state_dir
        os.makedirs(state_dir, exist_ok=True)

        # Shadow pools
        shadow = getattr(self, 'shadow', None)
        if shadow is not None:
            shadow_pools = {
                "spot_funds": shadow.spot_funds_shadow,
                "perp_funds": shadow.perp_funds_shadow,
                "spot_deployed": shadow.spot_deployed,
                "perp_deployed": shadow.perp_deployed,
            }
        else:
            shadow_pools = {
                "spot_funds": 0.0, "perp_funds": 0.0,
                "spot_deployed": 0.0, "perp_deployed": 0.0,
            }

        # QM-C2 fix: Write trades.jsonl BEFORE state.json so that on
        # crash between the two writes, recovery truncation removes the
        # orphaned trades (they have tick > restored tick_counter).
        trades_path = os.path.join(state_dir, "trades.jsonl")
        new_closed = []
        for st in self._get_all_states():
            new_closed.extend(st.position_manager.closed_trades)
        # R4-I1 fix: track flushed trades by position_id to prevent duplicates
        # on partial write failures. Previously used a count which could re-write
        # trades if the count wasn't updated due to a write exception.
        if not hasattr(self, '_flushed_position_ids'):
            self._flushed_position_ids = set()
        unflushed = [t for t in new_closed if t.position_id not in self._flushed_position_ids]
        # R7-I1 fix: stage position_ids written this tick. Only promote to
        # _flushed_position_ids after state.json commit, so failed ticks don't
        # permanently mark trades as flushed when the state was never committed.
        self._tick_staged_ids = set()
        if unflushed:
            with open(trades_path, "a") as f:
                for trade in unflushed:
                    f.write(json.dumps(_closed_trade_to_dict(trade, tick=self.tick_counter)) + "\n")
                f.flush()
                os.fsync(f.fileno())  # R3-C1 fix: fsync before state.json commit
            self._tick_staged_ids = {t.position_id for t in unflushed}

        # R5-I2 fix: write equity.csv BEFORE state.json commit point
        # so crash between trades and state doesn't create permanent equity gaps
        # R6-I4 fix: skip if this tick was already written (retry after failed state.json)
        last_equity_tick = getattr(self, '_last_equity_tick', -1)
        if self.tick_counter != last_equity_tick:
            equity_path = os.path.join(state_dir, "equity.csv")
            all_states = self._get_all_states()
            free_cap = sum(s.free_capital for s in all_states) if all_states else 0.0
            append_equity(
                equity_path,
                timestamp=timestamp,
                tick=self.tick_counter,
                portfolio_equity=portfolio_eq,
                mark_to_market_equity=mtm_eq,
                free_capital=free_cap,
                open_positions=self._aggregate_open_positions(),
            )
            self._last_equity_tick = self.tick_counter

        # Write state.json — handle both pool and independent modes
        # This is the "commit point": once state.json is atomically written,
        # the tick is considered complete.
        state_path = os.path.join(state_dir, "state.json")
        if self.state is not None:
            # Pool mode: single shared state
            atomic_write_state(
                self.state,
                self.tick_counter,
                timestamp,
                state_path,
                shadow_pools=shadow_pools,
                last_known_prices=dict(self._last_known_prices),
                last_known_regimes=dict(self._last_known_regimes),
            )
        else:
            # Independent mode: serialize all strategy states (C4 fix)
            from v4.paper_state import serialize_engine_state
            data = serialize_engine_state(
                dict(self.strategy_states), self.tick_counter, timestamp,
                mode="independent",
                shadow_pools=shadow_pools,
                last_known_prices=dict(self._last_known_prices),
                last_known_regimes=dict(self._last_known_regimes),
            )
            import tempfile as _tmpfile
            fd, tmp = _tmpfile.mkstemp(dir=state_dir, suffix=".tmp")
            os.close(fd)
            with open(tmp, "w") as f:
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())  # QM-I3 fix: fsync for independent mode
            os.rename(tmp, state_path)

        # R5-I1 fix: mark commit point reached — tick_counter should NOT
        # be rolled back if anything after this point throws
        self._state_committed = True

        # Write stops.json for sentinel consumption (after state.json commit)
        if self.config.sentinel_mode != "off":
            self._write_stops()

        # R7-I1 fix: promote staged position_ids to flushed now that state
        # is committed. If state.json commit failed, these would NOT be promoted,
        # allowing retry to re-write the trades (duplicates cleaned by truncation).
        self._flushed_position_ids.update(self._tick_staged_ids)
        self._tick_staged_ids = set()

        # R4-I3 fix: update last_timestamp so dashboard stale-data checks
        # use the latest processed tick's timestamp, not the restored one
        self.last_timestamp = timestamp

        # AC26: Trigger dashboard generation after persistence
        self._trigger_dashboard()

    def _trigger_dashboard(self) -> None:
        """Trigger dashboard generation after each tick (AC26).

        If config.dashboard_push is True, also pushes the dashboard.
        Dashboard errors are logged but non-fatal (AC27).
        """
        import logging
        logger = logging.getLogger(__name__)
        try:
            if self.config.dashboard_push:
                self._push_dashboard()
        except Exception as e:
            logger.warning("Dashboard generation failed (non-fatal): %s", e)

    def _push_dashboard(self) -> None:
        """Push dashboard to deployment target (AC27).

        Calls tools/generate_dashboard_v2.py with --state-dir and --push.
        Uses config_path for --config so dashboard picks up pool_name and strategy info.
        """
        import subprocess
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        script = os.path.join(project_root, "tools", "generate_dashboard_v2.py")

        cmd = [
            sys.executable, script,
            "--state-dir", self.config.state_dir,
            "--push",
        ]
        if self.config.config_path:
            cmd.extend(["--config", self.config.config_path])

        subprocess.run(cmd, cwd=project_root, timeout=120, check=False,
                       capture_output=True, text=True)

    def tick(self) -> TickResult:
        """Process a single tick with error handling.

        Catches invariant violations and performs emergency close if needed.
        """
        t0 = time.time()
        result = TickResult(tick_counter=self.tick_counter)
        self._alerts.clear()
        # R4-I4 fix: save tick_counter before _tick_internal so we can roll back
        # if it throws after incrementing but before persisting
        saved_tick_counter = self.tick_counter

        try:
            open_before = self._aggregate_open_positions()
            closed_before = self._aggregate_closed_trades()
            self._tick_internal()
            open_after = self._aggregate_open_positions()
            closed_after = self._aggregate_closed_trades()

            result.exits = closed_after - closed_before
            result.entries = max(0, open_after - open_before + result.exits)
            result.open_positions = open_after
            result.portfolio_equity = self._aggregate_portfolio_equity()
            result.mark_to_market_equity = self._compute_mark_to_market()
            result.alerts = list(self._alerts)
            # R3-I4 fix: update tick_counter AFTER _tick_internal increments it
            # so heartbeat matches state.json/trades.jsonl/equity.csv
            result.tick_counter = self.tick_counter

        except Exception as e:
            error_msg = str(e)
            # Only emergency-close for actual invariant violations
            all_states = self._get_all_states()
            free_cap = min((s.free_capital for s in all_states), default=0.0)
            if "invariant" in error_msg.lower() or free_cap < -1.0:
                self._emergency_close_all()
                # R4-C1 fix: persist state after emergency close so closures
                # survive process restart (prevents infinite restart loop)
                try:
                    self._persist_emergency_state()
                except Exception as persist_err:
                    import logging
                    logging.getLogger(__name__).error(
                        "Failed to persist emergency close state: %s", persist_err
                    )
                result.error = f"Invariant violation: {error_msg}"
            else:
                # Transient errors (network, data, etc.) — log but don't close positions
                result.error = f"Tick error (non-invariant): {error_msg}"
                # R4-I4 + R5-I1 fix: only roll back tick_counter if state.json
                # was NOT yet committed. If committed, the tick is on disk and
                # rolling back would create a divergence.
                if not getattr(self, '_state_committed', False):
                    self.tick_counter = saved_tick_counter

            result.open_positions = self._aggregate_open_positions()
            result.portfolio_equity = self._aggregate_portfolio_equity()
            result.mark_to_market_equity = self._compute_mark_to_market()
            result.alerts = list(self._alerts)
            # R3-I4 fix: sync tick_counter even on error path
            result.tick_counter = self.tick_counter

        # AC23: Peak RSS memory
        ru = resource.getrusage(resource.RUSAGE_SELF)
        result.peak_rss_mb = ru.ru_maxrss / 1024.0  # Linux reports in KB

        # AC24: Processing time
        result.processing_time_s = time.time() - t0

        # AC24: Timestamp
        result.timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        # AC25: Write heartbeat.json
        self._write_heartbeat(result)

        return result

    def _write_heartbeat(self, result: TickResult) -> None:
        """Write heartbeat.json to state_dir after each tick (AC25)."""
        state_dir = self.config.state_dir
        os.makedirs(state_dir, exist_ok=True)
        heartbeat = {
            "timestamp": result.timestamp,
            "tick_counter": int(result.tick_counter),
            "open_positions": int(result.open_positions),
            "portfolio_equity": float(result.portfolio_equity),
            "mark_to_market_equity": float(result.mark_to_market_equity),
            "processing_time_s": float(result.processing_time_s),
            "errors": result.error,
        }
        heartbeat_path = os.path.join(state_dir, "heartbeat.json")
        # R3-I5 fix: atomic write to avoid partial reads by monitoring
        import tempfile as _tmpfile
        fd, tmp = _tmpfile.mkstemp(dir=state_dir, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(heartbeat, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.rename(tmp, heartbeat_path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # ------------------------------------------------------------------
    # Live data integration (Task 7)
    # ------------------------------------------------------------------

    def _fetch_with_retry(self):
        """Fetch data with exponential backoff retry (AC10d).

        Returns fetched data on success, None on failure after all retries.
        """
        for attempt in range(3):
            try:
                result = self.fetcher.fetch_ohlcv()
                self._consecutive_failures = 0
                return result
            except Exception:
                pass

            # Backoff between retries (but don't sleep for the last attempt)
            if attempt < 2:
                time.sleep(self.BACKOFF[attempt] * 0.001)  # minimal delay in tests

        # All retries failed
        self._consecutive_failures += 1

        if self._consecutive_failures >= 3:
            self._alerts.append({
                "type": "consecutive_failures",
                "severity": "critical",
                "message": f"CRITICAL: {self._consecutive_failures} consecutive tick failures",
            })

        return None

    def _catch_up(
        self,
        n_missed_bars: int,
        bar_timestamps: list[str] | None = None,
    ) -> None:
        """Process missed bars sequentially during catch-up (AC10d).

        Stops on first error to prevent cascading issues.
        """
        for i in range(n_missed_bars):
            ts = bar_timestamps[i] if bar_timestamps and i < len(bar_timestamps) else None
            try:
                result = self._tick_internal(bar_timestamp=ts)
                # R3-I1 fix: check return value if _tick_internal returns a TickResult
                if result is not None and hasattr(result, 'error') and result.error is not None:
                    self._alerts.append(
                        f"Catch-up stopped at bar {i+1}/{n_missed_bars}: {result.error}"
                    )
                    break
            except Exception as e:
                # R3-I1 fix: also catch exceptions to stop cascading failures
                self._alerts.append(
                    f"Catch-up stopped at bar {i+1}/{n_missed_bars}: {e}"
                )
                break

    # ------------------------------------------------------------------
    # Dynamic weight adjustment
    # ------------------------------------------------------------------

    def _apply_dynamic_weights(self, all_signals: dict, bar_maps: dict) -> None:
        """Adjust strategy weights based on current BTC regime (if enabled).

        Reads BTC regime from the latest signal data, then updates each
        strategy's spec.weight using the DynamicWeightAllocator.
        """
        if not getattr(self.config, 'dynamic_weights', False):
            return

        # Lazy-initialize the allocator on first use
        if self._dynamic_allocator is None:
            from v4.dynamic_weights import DynamicWeightAllocator
            allocator = DynamicWeightAllocator.from_config(
                self.config,
                smoothing_alpha=getattr(self.config, 'dynamic_weights_smoothing', 0.3),
            )
            if allocator is None:
                import logging
                logging.getLogger(__name__).warning(
                    "Dynamic weights enabled but allocator could not be created "
                    "(missing heatmap?). Falling back to static weights."
                )
                # Disable to avoid re-trying every tick
                self.config.dynamic_weights = False
                return
            self._dynamic_allocator = allocator

        # Get current BTC regime from signals or last known
        btc_regime = self._last_known_regimes.get('BTC', 3)  # Default: RANGE

        # Also try to read from current tick's signals (more up-to-date)
        for sid, token_sigs in all_signals.items():
            if 'BTC' in token_sigs:
                sig = token_sigs['BTC']
                bm = bar_maps.get('BTC')
                if bm is not None and self.tick_counter < len(bm):
                    local_bar = bm[self.tick_counter]
                    if 0 <= local_bar < sig.n_bars and hasattr(sig, 'regime') and sig.regime is not None:
                        btc_regime = int(sig.regime[local_bar])
                break  # Only need BTC from one strategy

        # Compute dynamic weights
        new_weights = self._dynamic_allocator.get_weights(btc_regime)

        # Apply to strategy specs
        for spec in self.config.strategies:
            if spec.strategy_id in new_weights:
                spec.weight = new_weights[spec.strategy_id]

        # Log (first tick + regime changes)
        self._dynamic_allocator.log_weights(btc_regime, new_weights)

    # ------------------------------------------------------------------
    # Price tracking
    # ------------------------------------------------------------------

    def _update_last_known_prices(
        self,
        all_signals: dict,
        bar_maps: dict,
    ) -> None:
        """Track last known prices and regimes for each token."""
        for sid, token_sigs in all_signals.items():
            for token, sig in token_sigs.items():
                bm = bar_maps.get(token)
                if bm is not None and self.tick_counter < len(bm):
                    local_bar = bm[self.tick_counter]
                    if local_bar >= 0 and local_bar < sig.n_bars:
                        self._last_known_prices[token] = float(sig.close[local_bar])
                        if hasattr(sig, 'regime') and sig.regime is not None:
                            self._last_known_regimes[token] = int(sig.regime[local_bar])

    # ------------------------------------------------------------------
    # Quick price refresh — update MTM without running a full tick
    # ------------------------------------------------------------------

    def update_prices(self, exchange) -> dict:
        """Fetch live prices for open positions, update MTM and heartbeat.

        Does NOT run signals, entries, exits, or increment tick_counter.
        Returns dict with updated MTM info for logging.
        """
        import time as _time
        from datetime import datetime, timezone

        t0 = _time.perf_counter()

        # Collect tokens with open positions
        tokens_needed: set[str] = set()
        for st in self._get_all_states():
            for pos in st.position_manager.open_positions:
                tokens_needed.add(pos.token)

        if not tokens_needed:
            return {"tokens": 0, "mtm": self._compute_mark_to_market()}

        # Fetch live prices via ccxt tickers
        updated = 0
        for token in tokens_needed:
            try:
                ticker = exchange.fetch_ticker(f"{token}/USDT")
                price = ticker.get("last")
                if price:
                    self._last_known_prices[token] = float(price)
                    updated += 1
            except Exception:
                pass  # Keep last known price

        mtm = self._compute_mark_to_market()
        elapsed = _time.perf_counter() - t0

        # Update heartbeat with fresh MTM (no tick_counter change)
        state_dir = self.config.state_dir
        os.makedirs(state_dir, exist_ok=True)
        heartbeat = {
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "tick_counter": int(self.tick_counter),
            "open_positions": int(self._aggregate_open_positions()),
            "portfolio_equity": float(self._aggregate_portfolio_equity()),
            "mark_to_market_equity": float(mtm),
            "processing_time_s": float(elapsed),
            "errors": None,
            "price_refresh": True,
        }
        heartbeat_path = os.path.join(state_dir, "heartbeat.json")
        import tempfile as _tmpfile
        fd, tmp = _tmpfile.mkstemp(dir=state_dir, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(heartbeat, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.rename(tmp, heartbeat_path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass

        # Re-persist state.json so dashboard picks up fresh last_known_prices
        if updated > 0:
            refresh_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            state_path = os.path.join(state_dir, "state.json")
            if self.state is not None:
                atomic_write_state(
                    self.state,
                    self.tick_counter,
                    refresh_ts,
                    state_path,
                    last_known_prices=dict(self._last_known_prices),
                    last_known_regimes=dict(self._last_known_regimes),
                )
            else:
                from v4.paper_state import serialize_engine_state
                data = serialize_engine_state(
                    dict(self.strategy_states), self.tick_counter, refresh_ts,
                    mode="independent",
                    last_known_prices=dict(self._last_known_prices),
                    last_known_regimes=dict(self._last_known_regimes),
                )
                import tempfile as _tmpfile2
                fd2, tmp2 = _tmpfile2.mkstemp(dir=state_dir, suffix=".tmp")
                os.close(fd2)
                with open(tmp2, "w") as f2:
                    json.dump(data, f2, indent=2)
                    f2.flush()
                    os.fsync(f2.fileno())
                os.rename(tmp2, state_path)

        return {"tokens": updated, "mtm": mtm, "elapsed": elapsed}

    # ------------------------------------------------------------------
    # process_tick — deterministic tick for testing (Task 12)
    # ------------------------------------------------------------------

    def _aggregate_open_positions(self) -> int:
        """Total open positions across all states (works in both modes)."""
        if self.config.mode == "independent":
            return sum(s.position_manager.total_open() for s in self.strategy_states.values())
        return self.state.position_manager.total_open()

    def _aggregate_portfolio_equity(self) -> float:
        """Total portfolio equity across all states (works in both modes)."""
        if self.config.mode == "independent":
            return sum(s.portfolio_equity for s in self.strategy_states.values())
        return self.state.portfolio_equity

    def _aggregate_closed_trades(self) -> int:
        """Total closed trades across all states (works in both modes)."""
        if self.config.mode == "independent":
            return sum(len(s.position_manager.closed_trades) for s in self.strategy_states.values())
        return len(self.state.position_manager.closed_trades)

    def _get_all_entry_fees(self) -> dict:
        """Collect entry fees for all open positions across states."""
        fees = {}
        for st in self._get_all_states():
            fees.update(st._entry_fees_by_pos)
        return fees

    def _compute_mark_to_market(self) -> float:
        """Compute mark-to-market equity: portfolio_equity + sum of unrealized P&L.

        Unrealized P&L = quantity * (current_price - entry_price) for each open position.
        """
        base_equity = self._aggregate_portfolio_equity()
        unrealized_pnl = 0.0
        for st in self._get_all_states():
            for pos in st.position_manager.open_positions:
                current_price = self._last_known_prices.get(pos.token, pos.entry_price)
                unrealized_pnl += pos.quantity * (current_price - pos.entry_price)
        return base_equity + unrealized_pnl

    def process_tick(
        self,
        all_signals: dict,
        specs: dict | None = None,
    ) -> TickResult:
        """Process a single tick with provided signals (for testing/determinism).

        Unlike tick() which fetches data internally, this accepts pre-computed signals.
        Uses per-bar RNG seeding: RandomState(seed + tick_counter).
        """
        if specs is None:
            specs = {s.strategy_id: s for s in self.config.strategies}

        bar_maps = self._build_bar_maps(all_signals, self.tick_counter)
        self._tick_internal_with_signals(all_signals, specs, bar_maps)

        result = TickResult(
            tick_counter=self.tick_counter,
            open_positions=self._aggregate_open_positions(),
            portfolio_equity=self._aggregate_portfolio_equity(),
        )
        self.tick_counter += 1
        return result

    # ------------------------------------------------------------------
    # Dashboard SIMS output (Task 10)
    # ------------------------------------------------------------------

    def to_dashboard_sim(self, price_overrides: dict[str, float] | None = None) -> dict:
        """Produce SIMS JSON schema for dashboard consumption (AC11b, AC12, AC26).

        Args:
            price_overrides: Optional live prices to merge on top of _last_known_prices.
                Used by the dashboard heartbeat to overlay WebSocket prices without
                mutating engine state (thread-safe).
        """
        from datetime import datetime, timezone, timedelta

        config = self.config
        pool_name = config.pool_name or config.strategies[0].strategy_id if config.strategies else "default"

        # Aggregate across all states (works in both modes)
        all_closed_trades = []
        for st in self._get_all_states():
            all_closed_trades.extend(st.position_manager.closed_trades)

        # Build strategy info
        def _exit_res_label(val: int) -> str:
            if val <= 0: return "hourly"
            return f"{val}min"

        if config.mode == "pool" and config.pool_name:
            strategies = [{
                "id": config.pool_name,
                "name": config.pool_name,
                "weight": sum(s.weight for s in config.strategies),
                "final_equity": self._aggregate_portfolio_equity(),
                "trade_count": len(all_closed_trades),
                "open_positions": self._aggregate_open_positions(),
                "strategies": [s.strategy_id for s in config.strategies],
                "exit_resolution": _exit_res_label(getattr(self, '_effective_exit_resolution', 0)),
            }]
        else:
            strategies = [{
                "id": s.strategy_id,
                "name": s.strategy_id,
                "weight": s.weight,
                "exit_resolution": _exit_res_label(s.exit_resolution),
            } for s in config.strategies]

        # Build all_trades (closed + open)
        all_trades = []
        for t in all_closed_trades:
            all_trades.append({
                "token": t.token,
                "strategy": t.strategy_id,
                "market_type": "perp" if t.is_perp else "spot",
                "direction": t.direction,
                "pnl": t.pnl,
                "exit_reason": t.exit_reason,
                "signal": {
                    "entry_bar": t.entry_bar,
                    "exit_bar": t.exit_bar,
                    "entry_price": t.entry_price,
                    "exit_price": t.exit_price,
                    "hold_bars": t.hold_bars,
                },
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "margin_usd": t.margin_usd,
                "hold_bars": t.hold_bars,
                "funding_cost": t.funding_cost,
                "entry_fee": t.entry_fee,
                "exit_fee": t.exit_fee,
                "entry_timestamp": t.entry_timestamp,
                "exit_timestamp": t.exit_timestamp,
            })

        # AC28: Include open positions with status="open"
        last_prices = dict(getattr(self, '_last_known_prices', {}))
        if price_overrides:
            last_prices.update(price_overrides)
        last_regimes = dict(getattr(self, '_last_known_regimes', {}))
        regime_names = {0: "CRISIS", 1: "QUIET", 2: "UPTREND", 3: "RANGE", 4: "DOWNTREND"}
        all_entry_fees = self._get_all_entry_fees()
        for st in self._get_all_states():
            for pos in st.position_manager.open_positions:
                current_price = last_prices.get(pos.token, pos.entry_price)
                raw_unrealized = pos.quantity * (current_price - pos.entry_price)
                entry_fee = all_entry_fees.get(pos.position_id, 0.0)
                # Net unrealized: deduct known costs (entry fee + accrued funding)
                unrealized_pnl = raw_unrealized - entry_fee - pos.cumulative_funding
                # Stop distance
                stop_price = pos.stop_price
                if stop_price and current_price:
                    if pos.direction == 1:
                        pct_to_stop = (current_price - stop_price) / current_price * 100
                    else:
                        pct_to_stop = (stop_price - current_price) / current_price * 100
                else:
                    pct_to_stop = 0
                # Regime
                regime_id = last_regimes.get(pos.token, -1)
                regime_name = regime_names.get(regime_id, "N/A")
                exit_regime_names = [regime_names.get(r, str(r)) for r in pos.exit_regimes]
                hold_bars = self.tick_counter - pos.entry_bar
                all_trades.append({
                    "token": pos.token,
                    "strategy": pos.strategy_id,
                    "market_type": "perp" if pos.is_perp else "spot",
                    "direction": pos.direction,
                    "status": "open",
                    "current_price": current_price,
                    "unrealized_pnl": unrealized_pnl,
                    "entry_price": pos.entry_price,
                    "margin_usd": pos.margin_usd,
                    "entry_bar": pos.entry_bar,
                    "cumulative_funding": pos.cumulative_funding,
                    "entry_fee": entry_fee,
                    "hold_bars": hold_bars,
                    "stop_price": stop_price,
                    "no_stop_bars": pos.no_stop_bars,
                    "stop_active": hold_bars >= pos.no_stop_bars or pos.convex_exit,
                    "pct_to_stop": round(pct_to_stop, 2),
                    "regime": regime_name,
                    "exit_regimes": exit_regime_names,
                    "entry_timestamp": pos.entry_timestamp,
                    "leverage": pos.leverage,
                })

        # Equity history
        equity_history = getattr(self, 'equity_history', [])

        # Shadow pools
        shadow = getattr(self, 'shadow', None)
        if shadow is not None:
            shadow_pools = {
                "spot_funds": shadow.spot_funds_shadow,
                "perp_funds": shadow.perp_funds_shadow,
                "spot_deployed": shadow.spot_deployed,
                "perp_deployed": shadow.perp_deployed,
                "imbalance_pct": abs(shadow.spot_funds_shadow - shadow.perp_funds_shadow) / max(
                    shadow.spot_funds_shadow + shadow.perp_funds_shadow, 1.0
                ) * 100.0,
                "blocked_entries_count": sum(
                    r.get("would_have_blocked_entries", 0)
                    for r in getattr(shadow, 'rebalance_log', [])
                ),
            }
            rebalance_history = list(getattr(shadow, 'rebalance_log', []))
        else:
            shadow_pools = {
                "spot_funds": 0.0, "perp_funds": 0.0,
                "spot_deployed": 0.0, "perp_deployed": 0.0,
                "imbalance_pct": 0.0, "blocked_entries_count": 0,
            }
            rebalance_history = []

        # Stale data check
        last_ts = getattr(self, 'last_timestamp', None)
        is_stale = False
        if last_ts:
            try:
                ts_dt = datetime.strptime(last_ts, "%Y-%m-%dT%H:%M:%SZ").replace(
                    tzinfo=timezone.utc
                )
                age = datetime.now(timezone.utc) - ts_dt
                is_stale = age > timedelta(hours=2)
            except (ValueError, TypeError):
                is_stale = True

        # Aggregate fees/funding/pnl across all states
        agg_fees = 0.0
        agg_funding = 0.0
        agg_realized_pnl = 0.0
        for st in self._get_all_states():
            agg_fees += st.total_fees
            agg_funding += st.total_funding
            agg_realized_pnl += st.realized_pnl

        return {
            "id": pool_name,
            "name": pool_name,
            "capital": config.capital,
            "strategies": strategies,
            "all_trades": all_trades,
            "equity_history": equity_history,
            "shadow_pools": shadow_pools,
            "rebalance_history": rebalance_history,
            "last_updated": last_ts or "",
            "is_stale": is_stale,
            "tick_counter": self.tick_counter,
            "portfolio_equity": self._aggregate_portfolio_equity(),
            "open_positions": self._aggregate_open_positions(),
            "total_fees": agg_fees,
            "total_funding": agg_funding,
            "realized_pnl": agg_realized_pnl,
        }

    # ------------------------------------------------------------------
    # Reconciliation (Task 11)
    # ------------------------------------------------------------------

    def reconcile(
        self,
        paper_trades: list,
        backtest_trades: list,
        start_date: str,
        end_date: str,
    ) -> "ReconciliationReport":
        """Compare paper trades vs backtest trades, returning divergences (AC20)."""
        divergences = []

        # Index trades by (token, strategy_id, entry_bar, leg) — leg prevents
        # combined strategy primary/secondary from overwriting each other
        paper_by_key = {}
        for t in paper_trades:
            key = (t.token, t.strategy_id, t.entry_bar, getattr(t, 'leg', 'primary'))
            paper_by_key[key] = t

        backtest_by_key = {}
        for t in backtest_trades:
            key = (t.token, t.strategy_id, t.entry_bar, getattr(t, 'leg', 'primary'))
            backtest_by_key[key] = t

        all_keys = set(paper_by_key.keys()) | set(backtest_by_key.keys())

        for key in all_keys:
            token, sid, entry_bar, _leg = key
            p_trade = paper_by_key.get(key)
            b_trade = backtest_by_key.get(key)

            if p_trade and not b_trade:
                divergences.append({
                    "type": "entry_mismatch",
                    "field": "entry",
                    "token": token,
                    "strategy_id": sid,
                    "entry_bar": entry_bar,
                    "paper_value": "present",
                    "backtest_value": "absent",
                })
            elif b_trade and not p_trade:
                divergences.append({
                    "type": "entry_mismatch",
                    "field": "entry",
                    "token": token,
                    "strategy_id": sid,
                    "entry_bar": entry_bar,
                    "paper_value": "absent",
                    "backtest_value": "present",
                })
            else:
                # Both present — compare fields
                if p_trade.exit_bar != b_trade.exit_bar:
                    divergences.append({
                        "type": "exit_mismatch",
                        "field": "exit_bar",
                        "token": token,
                        "strategy_id": sid,
                        "entry_bar": entry_bar,
                        "paper_value": p_trade.exit_bar,
                        "backtest_value": b_trade.exit_bar,
                    })

                if p_trade.exit_reason != b_trade.exit_reason:
                    divergences.append({
                        "type": "exit_mismatch",
                        "field": "exit_reason",
                        "token": token,
                        "strategy_id": sid,
                        "entry_bar": entry_bar,
                        "paper_value": p_trade.exit_reason,
                        "backtest_value": b_trade.exit_reason,
                    })

                if abs(p_trade.pnl - b_trade.pnl) > 0.01:
                    divergences.append({
                        "type": "pnl_mismatch",
                        "field": "pnl",
                        "token": token,
                        "strategy_id": sid,
                        "entry_bar": entry_bar,
                        "paper_value": p_trade.pnl,
                        "backtest_value": b_trade.pnl,
                        "difference": abs(p_trade.pnl - b_trade.pnl),
                    })

        return ReconciliationReport(
            start_date=start_date,
            end_date=end_date,
            divergences=divergences,
            summary={
                "total_divergences": len(divergences),
                "paper_trades": len(paper_trades),
                "backtest_trades": len(backtest_trades),
            },
        )


@dataclass
class ReconciliationReport:
    """Result of reconciling paper trades vs backtest trades (AC20)."""
    start_date: str
    end_date: str
    divergences: list = field(default_factory=list)
    summary: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# AlertManager — structured alerts, heartbeat, and logging (Task 9)
# ---------------------------------------------------------------------------

class AlertManager:
    """Manages structured alerts, heartbeat file, and logging (AC14, AC18, AC19)."""

    def __init__(
        self,
        state_dir: str,
        drawdown_alert_pct: float = 5.0,
        webhook_url: str = "",
    ):
        self.state_dir = state_dir
        self.drawdown_alert_pct = drawdown_alert_pct
        self.webhook_url = webhook_url

        self._pending_alerts: list[dict] = []
        self._peak_equity: float = 0.0
        self._drawdown_alert_fired: bool = False
        self.consecutive_failures: int = 0

    # --- Alert accessors ---

    def get_pending_alerts(self) -> list[dict]:
        """Return and clear pending alerts."""
        alerts = list(self._pending_alerts)
        self._pending_alerts = []
        return alerts

    # --- Position alerts ---

    def on_position_open(
        self,
        token: str,
        strategy_id: str,
        direction: int,
        margin_usd: float,
        entry_price: float,
        leverage: float,
        timestamp: str,
    ) -> None:
        """Record a position open alert."""
        side = "LONG" if direction == 1 else "SHORT"
        self._pending_alerts.append({
            "type": "position_open",
            "severity": "info",
            "token": token,
            "strategy_id": strategy_id,
            "direction": direction,
            "margin_usd": margin_usd,
            "entry_price": entry_price,
            "leverage": leverage,
            "timestamp": timestamp,
            "message": f"{side} {token} opened: ${margin_usd:.0f} @ ${entry_price:.2f} ({strategy_id})",
        })

    def on_position_close(
        self,
        token: str,
        strategy_id: str,
        direction: int,
        pnl: float,
        exit_reason: str,
        hold_bars: int,
        timestamp: str,
    ) -> None:
        """Record a position close alert."""
        self._pending_alerts.append({
            "type": "position_close",
            "severity": "info",
            "token": token,
            "strategy_id": strategy_id,
            "direction": direction,
            "pnl": pnl,
            "exit_reason": exit_reason,
            "hold_bars": hold_bars,
            "timestamp": timestamp,
            "message": f"{token} closed ({exit_reason}): PnL ${pnl:.2f}, held {hold_bars}h",
        })

    # --- Drawdown alerts ---

    def update_peak_equity(self, equity: float) -> None:
        """Update peak equity watermark."""
        if equity > self._peak_equity:
            self._peak_equity = equity
            self._drawdown_alert_fired = False  # Reset on new high

    def check_drawdown(self, current_equity: float, timestamp: str) -> bool:
        """Check if drawdown exceeds threshold. Returns True if alert fired."""
        if self._peak_equity <= 0:
            return False

        drawdown_pct = (self._peak_equity - current_equity) / self._peak_equity * 100.0

        if drawdown_pct > self.drawdown_alert_pct:
            if not self._drawdown_alert_fired:
                self._drawdown_alert_fired = True
                self._pending_alerts.append({
                    "type": "drawdown",
                    "severity": "warning",
                    "drawdown_pct": drawdown_pct,
                    "peak_equity": self._peak_equity,
                    "current_equity": current_equity,
                    "timestamp": timestamp,
                    "message": f"Drawdown alert: {drawdown_pct:.1f}% from peak ${self._peak_equity:.0f}",
                })
                return True
            return False  # Suppressed (already fired)
        else:
            # Equity recovered above threshold — reset suppression
            self._drawdown_alert_fired = False
            return False

    # --- Tick failure tracking ---

    def on_tick_failure(self, error_msg: str, timestamp: str) -> None:
        """Record a tick failure."""
        self.consecutive_failures += 1
        if self.consecutive_failures >= 3:
            self._pending_alerts.append({
                "type": "consecutive_failures",
                "severity": "critical",
                "consecutive_count": self.consecutive_failures,
                "timestamp": timestamp,
                "message": f"CRITICAL: {self.consecutive_failures} consecutive failures: {error_msg}",
            })

    def on_tick_success(self, timestamp: str) -> None:
        """Reset consecutive failure counter on success."""
        self.consecutive_failures = 0

    # --- Delisting alert ---

    def on_delisting(
        self,
        token: str,
        strategy_id: str,
        position_id: str,
        last_price: float,
        timestamp: str,
    ) -> None:
        """Record a delisting/data-disappearance alert."""
        self._pending_alerts.append({
            "type": "delisting",
            "severity": "warning",
            "token": token,
            "strategy_id": strategy_id,
            "position_id": position_id,
            "last_price": last_price,
            "timestamp": timestamp,
            "message": f"Token delisted: {token} — position {position_id} force-closed @ ${last_price:.2f}",
        })

    # --- Heartbeat ---

    def write_heartbeat(
        self,
        timestamp: str,
        last_processed_bar: str,
        open_positions: int,
        portfolio_equity: float,
        errors: list,
    ) -> None:
        """Write heartbeat.json atomically (AC18)."""
        data = {
            "timestamp": timestamp,
            "last_processed_bar": last_processed_bar,
            "open_positions": open_positions,
            "portfolio_equity": portfolio_equity,
            "errors": errors,
        }
        json_str = json.dumps(data, indent=2)
        target_path = os.path.join(self.state_dir, "heartbeat.json")

        fd, tmp_path = tempfile.mkstemp(dir=self.state_dir, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(json_str)
                f.flush()
                os.fsync(f.fileno())
            os.rename(tmp_path, target_path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    # --- Structured logging ---

    def log_tick(
        self,
        timestamp: str,
        tick: int,
        entries_attempted: int,
        entries_accepted: int,
        entries_rejected: int,
        rejection_reasons: dict,
        exits_triggered: int,
        exit_reasons: dict,
        equity_snapshot: dict,
    ) -> None:
        """Append a structured log entry to daily-rotated JSONL file (AC19)."""
        log_dir = os.path.join(self.state_dir, "logs")
        os.makedirs(log_dir, exist_ok=True)

        # Extract date from timestamp for daily rotation
        date_str = timestamp[:10]  # "YYYY-MM-DD"
        log_path = os.path.join(log_dir, f"paper_engine_{date_str}.jsonl")

        entry = {
            "timestamp": timestamp,
            "tick": tick,
            "entries_attempted": entries_attempted,
            "entries_accepted": entries_accepted,
            "entries_rejected": entries_rejected,
            "rejection_reasons": rejection_reasons,
            "exits_triggered": exits_triggered,
            "exit_reasons": exit_reasons,
            "equity_snapshot": equity_snapshot,
        }

        with open(log_path, "a") as f:
            f.write(json.dumps(entry) + "\n")
