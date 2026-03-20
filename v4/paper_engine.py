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

    def __init__(self, config: PaperConfig):
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

        if config.mode == "independent":
            self._init_independent_mode()
            self.state = None  # Not used in independent mode (use strategy_states)
        else:
            self.state = SimulationState(initial_capital=config.capital)
            self.strategy_states = {}

    def _init_independent_mode(self) -> None:
        """Initialize separate SimulationState for each strategy (AC6b)."""
        self.strategy_states: dict[str, SimulationState] = {}
        for spec in self.config.strategies:
            capital = self.config.capital * spec.weight
            self.strategy_states[spec.strategy_id] = SimulationState(
                initial_capital=capital
            )

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
            sigs = precompute_strategy_signals(spec, tokens, self.config, self.config.lookback_months)
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
        self._handle_disappeared_tokens(all_signals)

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

    def to_dashboard_sim(self) -> dict:
        """Produce SIMS JSON schema for dashboard consumption (AC11b, AC12, AC26)."""
        from datetime import datetime, timezone, timedelta

        config = self.config
        pool_name = config.pool_name or config.strategies[0].strategy_id if config.strategies else "default"

        # Aggregate across all states (works in both modes)
        all_closed_trades = []
        for st in self._get_all_states():
            all_closed_trades.extend(st.position_manager.closed_trades)

        # Build strategy info
        if config.mode == "pool" and config.pool_name:
            strategies = [{
                "id": config.pool_name,
                "name": config.pool_name,
                "weight": sum(s.weight for s in config.strategies),
                "final_equity": self._aggregate_portfolio_equity(),
                "trade_count": len(all_closed_trades),
                "open_positions": self._aggregate_open_positions(),
                "strategies": [s.strategy_id for s in config.strategies],
            }]
        else:
            strategies = [{
                "id": s.strategy_id,
                "name": s.strategy_id,
                "weight": s.weight,
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
            })

        # AC28: Include open positions with status="open"
        last_prices = getattr(self, '_last_known_prices', {})
        all_entry_fees = self._get_all_entry_fees()
        for st in self._get_all_states():
            for pos in st.position_manager.open_positions:
                current_price = last_prices.get(pos.token, pos.entry_price)
                raw_unrealized = pos.quantity * (current_price - pos.entry_price)
                entry_fee = all_entry_fees.get(pos.position_id, 0.0)
                # Net unrealized: deduct known costs (entry fee + accrued funding)
                unrealized_pnl = raw_unrealized - entry_fee - pos.cumulative_funding
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
