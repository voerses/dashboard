#!/usr/bin/env python3
"""Continuous paper trading runner for combined spot+perp strategies.

Runs two independent simulations sharing a single live data feed:
  - s30 basis_carry  ($200k capital)
  - s32 regime_spot_perp ($200k capital)

Live data is fetched once per tick and written to WAL. Both strategies
read from the same parquet+WAL data via DataLoader.

Usage:
    python run_paper_live.py              # continuous loop (hourly ticks)
    python run_paper_live.py --once       # single scan, then exit
    python run_paper_live.py --status     # print last recorded state
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from v3.data_loader import DataLoader
from v3.live_fetcher import LiveFetcher
from v3.paper_engine import CombinedPaperEngine
from v3.universe import get_fee_rate, get_maint_margin_rate, adv_to_sizing

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def _discover_tokens(data_dir: str = "data") -> list[str]:
    """Auto-discover all tokens with both spot and perp parquet data."""
    spot_dir = os.path.join(data_dir, "spot", "1h_cache")
    perp_dir = os.path.join(data_dir, "perp", "1h_cache")
    spot = {f.replace("_1h.parquet", "") for f in os.listdir(spot_dir) if f.endswith(".parquet")} if os.path.isdir(spot_dir) else set()
    perp = {f.replace("_1h.parquet", "") for f in os.listdir(perp_dir) if f.endswith(".parquet")} if os.path.isdir(perp_dir) else set()
    return sorted(spot & perp)


TOKENS = _discover_tokens(os.path.join(str(Path(__file__).resolve().parent), "data"))

S54_ELITE_TOKENS = [
    "AAVE", "ADA", "APT", "AVAX", "AXS", "BCH", "BNB", "BTC", "DOGE", "DOT",
    "ETH", "FIL", "HBAR", "LINK", "LTC", "NEAR", "OP", "SOL", "TRX", "UNI",
    "XRP", "ZEC",
]

RUNS = [
    {"strategy_id": "s30", "capital": 200_000.0, "label": "s30_basis_carry"},
    {"strategy_id": "s32", "capital": 200_000.0, "label": "s32_regime_spot_perp"},
    {"strategy_id": "s54", "capital": 200_000.0, "label": "s54_turbo_carry",
     "tokens": S54_ELITE_TOKENS},
    {"strategy_id": "s58", "capital": 200_000.0, "label": "s58_multi_strategy_portfolio",
     "max_positions": 25,
     "sub_strategies": [
         {"strategy_id": "s56", "market": "perp", "tag": "mom"},
         {"strategy_id": "s57", "market": "combined", "tag": "carry"},
     ]},
]

EXCHANGE = "binance"
_PROJECT_DIR = str(Path(__file__).resolve().parent)
DATA_DIR = os.path.join(_PROJECT_DIR, "data")
STATE_DIR = os.path.join(_PROJECT_DIR, "state", "paper_live")
LOG_DIR = os.path.join(_PROJECT_DIR, "state", "paper_live", "logs")
TICK_INTERVAL_S = 3600  # 1 hour


# ---------------------------------------------------------------------------
# Position tracker — manages open/closed trades with P&L
# ---------------------------------------------------------------------------

class PaperPositionTracker:
    """Tracks paper trading positions and trades for a single strategy.

    Manages position lifecycle:
    - Entry: when strategy fires an entry signal and no position exists
    - Exit: when regime flips to exit regime or entry signal disappears
    - P&L: computed from entry/exit prices, with fee estimation
    """

    def __init__(
        self, strategy_id: str, label: str, capital: float,
        state_dir: str, data_dir: str = "data",
        exchange: str = "binance", max_positions: int = 15,
    ):
        self.strategy_id = strategy_id
        self.label = label
        self.capital = capital
        self.equity = capital
        self.spot_funds = capital / 2.0
        self.perp_funds = capital / 2.0
        self.rebalance_history: list[dict] = []
        self.state_dir = state_dir
        self.data_dir = data_dir
        self.exchange = exchange
        self.max_positions = max_positions
        # Per-market taker fees and maintenance margin from validated exchange config
        self.spot_fee = get_fee_rate(exchange, "spot", "taker")
        self.perp_fee = get_fee_rate(exchange, "perp", "taker")
        self.maint_margin_rate = get_maint_margin_rate(exchange)
        self.open_positions: dict[str, dict] = {}
        self.closed_trades: list[dict] = []
        self._load()

    def _trades_path(self) -> str:
        return os.path.join(self.state_dir, f"{self.label}_trades.json")

    def _load(self):
        path = self._trades_path()
        if not os.path.exists(path):
            return
        with open(path) as f:
            data = json.load(f)
        self.open_positions = data.get("open_positions", {})
        self.closed_trades = data.get("closed_trades", [])
        self.equity = data.get("equity", self.capital)
        self.spot_funds = data.get("spot_funds", self.equity / 2)
        self.perp_funds = data.get("perp_funds", self.equity / 2)
        self.rebalance_history = data.get("rebalance_history", [])

    def save(self):
        os.makedirs(os.path.dirname(self._trades_path()), exist_ok=True)
        data = {
            "strategy_id": self.strategy_id,
            "label": self.label,
            "capital": self.capital,
            "equity": round(self.equity, 2),
            "spot_funds": round(self.spot_funds, 2),
            "perp_funds": round(self.perp_funds, 2),
            "rebalance_history": self.rebalance_history[-50:],
            "open_positions": self.open_positions,
            "closed_trades": self.closed_trades,
        }
        tmp = self._trades_path() + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2, default=str)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self._trades_path())

    def _position_size(self, sig: dict | None = None) -> float:
        """Compute position size matching backtest engine sizing logic.

        Identical formula to engine.py _simulate_core_jit:
            kelly_frac = kelly_mult(ADV) * size_multiplier * edge
            vol_adj = 0.02 / max(vol, 0.005)
            raw = equity * kelly_frac * vol_adj
            cap = equity * cap_pct(ADV) * cap_multiplier
            pos_usd = min(raw, cap)
        """
        fallback = self.equity / self.max_positions
        if sig is None:
            return fallback

        sizing = sig.get("sizing", {})

        # 1. ADV-based kelly_mult and cap_pct (matches engine adv_to_sizing)
        adv = sig.get("spot_adv", 0) or sig.get("perp_adv", 0) or 5_000_000.0
        kelly_mult, cap_pct = adv_to_sizing(adv)

        # 2. Strategy size_multiplier overlay (matches engine kelly_mult *= sm)
        size_mult = sizing.get("size_multiplier", 1.0)
        kelly_mult *= size_mult

        # 3. Edge (matches engine: kelly_frac = kelly_mult * edge)
        edge = sizing.get("edge", 0.3)
        kelly_frac = kelly_mult * edge

        # 4. ATR vol-adjustment (matches engine vol_adj)
        atr = sig.get("spot_atr", 0) or sig.get("perp_atr", 0)
        close = sig.get("spot_close", 0) or sig.get("perp_close", 0)
        if atr > 0 and close > 0:
            vol = atr / close
            vol_adj = 0.02 / max(vol, 0.005)
        else:
            vol_adj = 1.0

        # 5. Raw position = equity * kelly_frac * vol_adj
        raw = self.equity * kelly_frac * vol_adj

        # 6. Cap = equity * cap_pct * cap_multiplier
        cap_mult = sizing.get("cap_multiplier", 1.0)
        cap = self.equity * cap_pct * cap_mult

        # 7. max_trade_pct ceiling (if strategy provides one)
        max_trade_pct = sizing.get("max_trade_pct", 0.0)
        if max_trade_pct > 0:
            pos_usd = min(raw, cap, self.equity * max_trade_pct)
        else:
            pos_usd = min(raw, cap)

        # 8. ADV hard cap — never exceed 30% of daily volume
        # Without this, compounding equity makes positions >> ADV, unrealistic
        max_participation = 0.30
        adv_cap = adv * max_participation
        if adv_cap > 0:
            pos_usd = min(pos_usd, adv_cap)

        # Minimum viable trade size
        if pos_usd < 200.0:
            return 0.0

        return pos_usd

    def process_signals(
        self, all_signals: dict[str, dict], tick_time: str,
    ) -> dict:
        """Process signals: update P&L, close exits, open entries. Returns summary."""
        opened = []
        closed = []
        liquidated = []

        # 1. Update mark-to-market and check trade management exits
        self._update_open_pnl(all_signals)

        # 2. Close positions: trade management exits, regime exits, signal lost
        for token in list(self.open_positions.keys()):
            pos = self.open_positions[token]
            sig = all_signals.get(token, {})

            exit_reason = pos.get("_exit_reason")
            if exit_reason:
                # Trade management exit (stop, trail, target, max_hold, liquidation)
                if exit_reason == "liquidation":
                    liquidated.append(token)
                self._close_position(token, sig, tick_time, exit_reason=exit_reason)
                closed.append(token)
            elif not sig or "error" in sig:
                # Token not in scan results or errored
                self._close_position(token, sig, tick_time, exit_reason="signal_lost")
                closed.append(token)
            elif sig.get("in_exit_regime", False):
                # Regime exit — but only after 6 bars (matching backtest)
                bars_held = pos.get("bars_held", 0)
                if bars_held > 6:
                    self._close_position(token, sig, tick_time, exit_reason="regime")
                    closed.append(token)

        # 2.5 Rebalance funds between spot and perp accounts
        last_rebalance = self._rebalance(tick_time)

        # 3. Check entries for new positions
        for token, sig in all_signals.items():
            if token in self.open_positions:
                continue
            if len(self.open_positions) >= self.max_positions:
                break
            if "error" in sig:
                continue

            # Edge threshold: skip entries with very low edge (matching backtest)
            sizing = sig.get("sizing", {})
            if sizing.get("edge", 0.3) < 0.10:
                continue

            has_entry = sig.get("spot_entry", False) or sig.get("perp_entry", False)
            if has_entry and not sig.get("in_exit_regime", False):
                self._open_position(token, sig, tick_time)
                if token in self.open_positions:  # may not open if size too small
                    opened.append(token)

        self.save()

        return {"opened": opened, "closed": closed, "liquidated": liquidated,
                "rebalance": last_rebalance}

    def _open_position(self, token: str, sig: dict, tick_time: str):
        size = self._position_size(sig)
        if size <= 0:
            return  # below minimum viable trade size

        # Capital utilization check: don't exceed equity with total deployed.
        # self.equity already reflects entry fees paid, funding deducted, and
        # closed P&L (with exit fees). deployed is raw capital locked in positions.
        deployed = sum(
            p.get("spot_size", 0) + p.get("perp_size", 0)
            for p in self.open_positions.values()
        )
        # Reserve for entry fee this new position will incur
        max_fee = max(self.spot_fee, self.perp_fee)
        available = max(self.equity - deployed, 0) / (1.0 + max_fee)
        if size > available:
            if available < 200:
                return  # not enough capital for minimum trade
            size = available  # cap to remaining capital

        sizing = sig.get("sizing", {})
        spot_lev = sizing.get("leverage", 1.0)
        perp_lev = sizing.get("secondary_leverage", sizing.get("leverage", 1.0))

        # Capital split between legs (matches backtest capital_split)
        has_spot = sig.get("spot_entry", False)
        has_perp = sig.get("perp_entry", False)
        capital_split = sig.get("capital_split", 0.5)
        if has_spot and has_perp:
            spot_size = size * capital_split
            perp_size = size * (1.0 - capital_split)
        elif has_spot:
            spot_size = size
            perp_size = 0.0
        else:
            spot_size = 0.0
            perp_size = size

        # Apply slippage to entry prices (worsens entry for the trader)
        spot_close = sig.get("spot_close", 0)
        perp_close = sig.get("perp_close", 0)
        spot_dir = sig.get("spot_dir", 1)
        perp_dir = sig.get("perp_dir", -1)
        spot_adv = sig.get("spot_adv", 5_000_000)
        perp_adv = sig.get("perp_adv", 5_000_000)

        spot_entry_price = self._compute_slippage(
            spot_close, spot_size * spot_lev, spot_adv, spot_dir,
        ) if has_spot else spot_close
        perp_entry_price = self._compute_slippage(
            perp_close, perp_size * perp_lev, perp_adv, perp_dir,
        ) if has_perp else perp_close

        # Entry fees on leveraged notional — tracked per fund
        spot_entry_fee = spot_size * spot_lev * self.spot_fee if has_spot else 0.0
        perp_entry_fee = perp_size * perp_lev * self.perp_fee if has_perp else 0.0
        entry_fee = spot_entry_fee + perp_entry_fee
        self.spot_funds -= spot_entry_fee
        self.perp_funds -= perp_entry_fee
        self.equity = self.spot_funds + self.perp_funds

        # Trade management params (primary for spot leg, secondary for perp leg)
        tp = sig.get("trade_params", {})
        stp = sig.get("sec_trade_params", tp)
        spot_atr = sig.get("spot_atr", 0)
        perp_atr = sig.get("perp_atr", 0)

        self.open_positions[token] = {
            "token": self._real_token(token),
            "strategy": self.label,
            "entry_time": tick_time,
            "spot_entry_price": round(spot_entry_price, 8),
            "perp_entry_price": round(perp_entry_price, 8),
            "spot_entry": has_spot,
            "perp_entry": has_perp,
            "spot_dir": spot_dir,
            "perp_dir": perp_dir,
            "regime_at_entry": sig.get("regime", "?"),
            "basis_bps_at_entry": sig.get("basis_bps", 0),
            "position_usd": round(size, 2),
            "spot_size": round(spot_size, 2),
            "perp_size": round(perp_size, 2),
            "entry_fee": round(entry_fee, 2),
            "accrued_funding": 0.0,
            "last_funding_ts": 0,
            "leverage": spot_lev,
            "secondary_leverage": perp_lev,
            "size_multiplier": sizing.get("size_multiplier", 1.0),
            "cap_multiplier": sizing.get("cap_multiplier", 1.0),
            # Trade management — stored per leg
            "spot_trade_params": tp,
            "perp_trade_params": stp,
            "trail_schedule": sig.get("trail_schedule"),
            "spot_entry_atr": spot_atr,
            "perp_entry_atr": perp_atr,
            # Watermarks for trailing stop
            "spot_highest": spot_entry_price,
            "spot_lowest": spot_entry_price,
            "perp_highest": perp_entry_price,
            "perp_lowest": perp_entry_price,
            "bars_held": 0,
            # ADV for slippage on exit
            "spot_adv": spot_adv,
            "perp_adv": perp_adv,
            "signal": {
                "regime": sig.get("regime", "?"),
                "basis_bps": sig.get("basis_bps", 0),
                "spot_close": spot_close,
                "perp_close": perp_close,
                "leverage": spot_lev,
                "size_multiplier": sizing.get("size_multiplier", 1.0),
            },
        }

    def _close_position(self, token: str, sig: dict, tick_time: str, exit_reason: str | None = None):
        pos = self.open_positions.pop(token)
        real_token = self._real_token(token)

        # Apply slippage to exit prices (worsens exit for the trader)
        spot_close = sig.get("spot_close", pos["spot_entry_price"])
        perp_close = sig.get("perp_close", pos["perp_entry_price"])
        spot_size = pos.get("spot_size", pos["position_usd"] / 2)
        perp_size = pos.get("perp_size", pos["position_usd"] / 2)
        spot_lev = pos.get("leverage", 1.0)
        perp_lev = pos.get("secondary_leverage", pos.get("leverage", 1.0))

        # Exit slippage: direction flipped (selling long = -1, covering short = +1)
        spot_exit = self._compute_slippage(
            spot_close, spot_size * spot_lev,
            pos.get("spot_adv", 5_000_000), -pos.get("spot_dir", 1),
        ) if pos["spot_entry"] else spot_close
        perp_exit = self._compute_slippage(
            perp_close, perp_size * perp_lev,
            pos.get("perp_adv", 5_000_000), -pos.get("perp_dir", -1),
        ) if pos["perp_entry"] else perp_close

        if exit_reason is None:
            exit_reason = "regime" if sig.get("in_exit_regime") else "signal_lost"
        accrued_funding = pos.get("accrued_funding", 0.0)

        # Hold duration
        try:
            entry_dt = datetime.strptime(pos["entry_time"], "%Y-%m-%d %H:%M UTC")
            entry_dt = entry_dt.replace(tzinfo=timezone.utc)
            exit_dt = datetime.strptime(tick_time, "%Y-%m-%d %H:%M UTC")
            exit_dt = exit_dt.replace(tzinfo=timezone.utc)
            hold_hours = round((exit_dt - entry_dt).total_seconds() / 3600, 1)
        except Exception:
            hold_hours = 0

        total_exit_cost = 0.0

        if pos["spot_entry"] and pos["spot_entry_price"] > 0:
            spot_notional = spot_size * spot_lev
            spot_ret = (spot_exit - pos["spot_entry_price"]) / pos["spot_entry_price"]
            spot_ret *= pos["spot_dir"]
            spot_gross = spot_notional * spot_ret
            entry_fee = spot_notional * self.spot_fee
            exit_fee = spot_notional * self.spot_fee
            total_exit_cost += exit_fee
            spot_net = spot_gross - entry_fee - exit_fee
            self.closed_trades.append({
                "token": real_token, "strategy": self.label, "leg": "spot",
                "entry_time": pos["entry_time"], "exit_time": tick_time,
                "entry_price": pos["spot_entry_price"], "exit_price": round(spot_exit, 8),
                "direction": pos["spot_dir"],
                "regime_at_entry": pos["regime_at_entry"],
                "regime_at_exit": sig.get("regime", "?"),
                "basis_bps_at_entry": pos.get("basis_bps_at_entry", 0),
                "position_usd": round(spot_size, 2),
                "notional_usd": round(spot_notional, 2),
                "return_pct": round(spot_ret * 100, 4),
                "pnl": round(spot_net, 2),
                "exchange_fee": round(entry_fee + exit_fee, 2),
                "funding_cost": 0,
                "hold_hours": hold_hours,
                "status": "closed", "exit_reason": exit_reason,
                "leverage": spot_lev,
                "size_multiplier": pos.get("size_multiplier", 1.0),
            })

        if pos["perp_entry"] and pos["perp_entry_price"] > 0:
            perp_notional = perp_size * perp_lev
            perp_ret = (perp_exit - pos["perp_entry_price"]) / pos["perp_entry_price"]
            perp_ret *= pos["perp_dir"]
            perp_gross = perp_notional * perp_ret
            entry_fee = perp_notional * self.perp_fee
            exit_fee = perp_notional * self.perp_fee
            total_exit_cost += exit_fee
            perp_funding = accrued_funding
            if exit_reason == "liquidation":
                max_loss = perp_size * (1.0 - self.maint_margin_rate)
                perp_net = -(max_loss + entry_fee)
            else:
                perp_net = perp_gross - entry_fee - exit_fee - perp_funding
            self.closed_trades.append({
                "token": real_token, "strategy": self.label, "leg": "perp",
                "entry_time": pos["entry_time"], "exit_time": tick_time,
                "entry_price": pos["perp_entry_price"], "exit_price": round(perp_exit, 8),
                "direction": pos["perp_dir"],
                "regime_at_entry": pos["regime_at_entry"],
                "regime_at_exit": sig.get("regime", "?"),
                "basis_bps_at_entry": pos.get("basis_bps_at_entry", 0),
                "position_usd": round(perp_size, 2),
                "notional_usd": round(perp_notional, 2),
                "return_pct": round(perp_ret * 100, 4),
                "pnl": round(perp_net, 2),
                "exchange_fee": round(entry_fee + exit_fee, 2),
                "funding_cost": round(perp_funding, 2),
                "hold_hours": hold_hours,
                "status": "closed", "exit_reason": exit_reason,
                "leverage": perp_lev,
                "size_multiplier": pos.get("size_multiplier", 1.0),
            })

        # Per-fund accounting: split closed P&L by leg
        spot_entry_fee_undo = spot_size * spot_lev * self.spot_fee if pos["spot_entry"] else 0.0
        perp_entry_fee_undo = perp_size * perp_lev * self.perp_fee if pos["perp_entry"] else 0.0
        spot_closed_pnl = sum(
            t["pnl"] for t in self.closed_trades
            if t["token"] == real_token and t["exit_time"] == tick_time and t.get("leg") == "spot"
        )
        perp_closed_pnl = sum(
            t["pnl"] for t in self.closed_trades
            if t["token"] == real_token and t["exit_time"] == tick_time and t.get("leg") == "perp"
        )
        # Spot fund: undo entry fee pre-deduction + spot P&L
        self.spot_funds += spot_entry_fee_undo + spot_closed_pnl
        # Perp fund: undo entry fee pre-deduction + perp P&L + undo funding double-count
        # (funding was already deducted from perp_funds during hold)
        self.perp_funds += perp_entry_fee_undo + perp_closed_pnl + accrued_funding
        self.equity = self.spot_funds + self.perp_funds

    @staticmethod
    def _compute_slippage(price: float, pos_usd: float, adv: float, direction: int) -> float:
        """Position-size-aware slippage matching backtest engine.

        slip_bps = base_spread + impact_coeff * sqrt(pos_usd / adv)
        Returns adjusted price (worse for the trader).
        """
        base_spread_bps = 3.0
        impact_coeff = 0.03
        if adv > 0:
            participation = pos_usd / adv
            slip_bps = base_spread_bps + impact_coeff * (participation ** 0.5) * 10000.0
        else:
            slip_bps = base_spread_bps
        slip_bps = min(slip_bps, 100.0)  # cap at 100bps
        slip = price * slip_bps / 10000.0
        # direction: +1 long (entry worse = higher), -1 short (entry worse = lower)
        return price + slip * direction

    @staticmethod
    def _real_token(key: str) -> str:
        """Strip sub-strategy suffix from compound position key (e.g. 'BTC:mom' -> 'BTC')."""
        return key.split(":")[0] if ":" in key else key

    def _target_spot_pct(self) -> float:
        """Target spot allocation percentage based on strategy type."""
        sid = self.strategy_id
        # Perp-only strategies
        if sid in ("s56",):
            return 0.0
        # Multi-strategy: weighted average based on current open positions,
        # clamped to [0.2, 0.8] because both sub-strategies are always active
        if sid == "s58" and self.open_positions:
            total_spot = sum(p.get("spot_size", 0) for p in self.open_positions.values())
            total_perp = sum(p.get("perp_size", 0) for p in self.open_positions.values())
            total = total_spot + total_perp
            if total > 0:
                return max(0.2, min(0.8, total_spot / total))
        # s30, s32, s54, s57 carry strategies → 50/50
        return 0.5

    def _rebalance(self, tick_time: str) -> dict | None:
        """Compute and execute fund rebalancing between spot and perp accounts.

        Returns transfer record or None if no rebalance needed.
        """
        spot_deployed = sum(p.get("spot_size", 0) for p in self.open_positions.values())
        perp_deployed = sum(p.get("perp_size", 0) for p in self.open_positions.values())
        spot_available = self.spot_funds - spot_deployed
        perp_available = self.perp_funds - perp_deployed
        total_available = spot_available + perp_available

        if total_available <= 0:
            return None

        target_spot_pct = self._target_spot_pct()
        target_spot = total_available * target_spot_pct
        transfer = target_spot - spot_available

        # Cap transfer so neither fund goes below zero
        transfer = max(-self.spot_funds, min(transfer, self.perp_funds))

        if abs(transfer) < 100:
            return None

        # Execute transfer
        self.spot_funds += transfer
        self.perp_funds -= transfer
        # equity unchanged: spot_funds + perp_funds is constant

        direction = "perp → spot" if transfer > 0 else "spot → perp"
        record = {
            "time": tick_time,
            "amount": round(abs(transfer), 2),
            "direction": direction,
            "spot_funds_after": round(self.spot_funds, 2),
            "perp_funds_after": round(self.perp_funds, 2),
        }
        self.rebalance_history.append(record)
        return record

    def _read_latest_funding_rate(self, token: str) -> float:
        """Read the latest funding rate from WAL for a token."""
        real = self._real_token(token)
        path = os.path.join(self.data_dir, "live", "funding", f"{real}_funding.jsonl")
        if not os.path.exists(path):
            return 0.0001  # default 0.01%
        try:
            last_line = ""
            with open(path, "r") as f:
                for line in f:
                    if line.strip():
                        last_line = line.strip()
            if last_line:
                return abs(json.loads(last_line).get("fundingRate", 0.0001))
        except Exception:
            pass
        return 0.0001

    def _check_leg_exit(self, price_now: float, entry_price: float,
                        direction: int, atr_entry: float, leg_size: float,
                        leverage: float, tp: dict, trail_schedule,
                        highest: float, lowest: float, bars_held: int,
                        accrued_funding: float = 0.0) -> str | None:
        """Check trade management exits for a single leg. Returns exit reason or None."""
        if atr_entry <= 0 or entry_price <= 0:
            return None

        no_stop_bars = tp.get("no_stop_bars", 0)
        stop_mult = tp.get("stop_mult", 3.0)
        trail_mult = tp.get("trail_mult", 3.0)
        target_mult = tp.get("target_mult", 999.0)
        min_hold = tp.get("min_hold", 6)
        max_hold = tp.get("max_hold", 720)

        # Progressive trail schedule: [[profit_atr_threshold, trail_mult], ...]
        if trail_schedule and bars_held >= no_stop_bars:
            profit = (price_now - entry_price) / atr_entry * direction
            eff_trail = trail_mult
            for threshold, tm in trail_schedule:
                if profit >= threshold:
                    eff_trail = tm
            trail_mult = eff_trail

        is_long = direction == 1

        # 1. Stop loss (after no_stop_bars)
        if bars_held >= no_stop_bars:
            if is_long:
                stop_price = entry_price - stop_mult * atr_entry
                if price_now <= stop_price:
                    return "stop"
            else:
                stop_price = entry_price + stop_mult * atr_entry
                if price_now >= stop_price:
                    return "stop"

        # 2. Trailing stop (after no_stop_bars)
        if bars_held >= no_stop_bars:
            if is_long:
                trail_price = highest - trail_mult * atr_entry
                if price_now <= trail_price and trail_price > entry_price - stop_mult * atr_entry:
                    return "trail"
            else:
                trail_price = lowest + trail_mult * atr_entry
                if price_now >= trail_price and trail_price < entry_price + stop_mult * atr_entry:
                    return "trail"

        # 3. Target (after min_hold)
        if bars_held >= min_hold and target_mult < 900:
            if is_long:
                target_price = entry_price + target_mult * atr_entry
                if price_now >= target_price:
                    return "target"
            else:
                target_price = entry_price - target_mult * atr_entry
                if price_now <= target_price:
                    return "target"

        # 4. Max hold
        if bars_held >= max_hold:
            return "max_hold"

        # 5. Liquidation (perp with any leverage, or shorts at any leverage)
        if leverage > 1.0 or not is_long:
            notional = leg_size * leverage
            ret = (price_now - entry_price) / entry_price * direction
            unrealized = notional * ret
            remaining_margin = leg_size + unrealized - accrued_funding
            if remaining_margin < leg_size * self.maint_margin_rate:
                return "liquidation"

        return None

    def _update_open_pnl(self, all_signals: dict[str, dict]):
        now_ms = int(time.time() * 1000)
        funding_interval_ms = 8 * 3600 * 1000  # 8 hours

        for token, pos in self.open_positions.items():
            sig = all_signals.get(token, {})
            spot_now = sig.get("spot_close", pos["spot_entry_price"])
            perp_now = sig.get("perp_close", pos["perp_entry_price"])

            # Per-leg sizes (backward compat with old positions)
            spot_size = pos.get("spot_size", pos["position_usd"] / 2)
            perp_size = pos.get("perp_size", pos["position_usd"] / 2)

            spot_lev = pos.get("leverage", 1.0)
            perp_lev = pos.get("secondary_leverage", pos.get("leverage", 1.0))
            spot_notional = spot_size * spot_lev
            perp_notional = perp_size * perp_lev

            # --- Increment bars held ---
            pos["bars_held"] = pos.get("bars_held", 0) + 1
            bars_held = pos["bars_held"]

            # --- Update watermarks ---
            if pos.get("spot_entry"):
                pos["spot_highest"] = max(pos.get("spot_highest", spot_now), spot_now)
                pos["spot_lowest"] = min(pos.get("spot_lowest", spot_now), spot_now)
            if pos.get("perp_entry"):
                pos["perp_highest"] = max(pos.get("perp_highest", perp_now), perp_now)
                pos["perp_lowest"] = min(pos.get("perp_lowest", perp_now), perp_now)

            # --- Accrue funding for perp leg (on leveraged notional) ---
            # Sign convention: longs pay positive rate, shorts receive positive rate
            if pos.get("perp_entry"):
                last_ts = pos.get("last_funding_ts", 0)
                if last_ts == 0:
                    try:
                        entry_dt = datetime.strptime(
                            pos["entry_time"], "%Y-%m-%d %H:%M UTC",
                        ).replace(tzinfo=timezone.utc)
                        last_ts = int(entry_dt.timestamp() * 1000)
                    except Exception:
                        last_ts = now_ms
                    pos["last_funding_ts"] = last_ts

                elapsed_ms = now_ms - last_ts
                periods = int(elapsed_ms // funding_interval_ms)
                if periods > 0:
                    rate = self._read_latest_funding_rate(token)
                    # Funding sign: d_sign = +1 for long, -1 for short
                    # cost = notional * rate * d_sign (positive = cost, negative = income)
                    d_sign = 1.0 if pos["perp_dir"] == 1 else -1.0
                    funding_cost = rate * periods * perp_notional * d_sign
                    pos["accrued_funding"] = round(
                        pos.get("accrued_funding", 0) + funding_cost, 4,
                    )
                    pos["last_funding_ts"] = last_ts + periods * funding_interval_ms
                    self.perp_funds -= funding_cost
                    self.equity = self.spot_funds + self.perp_funds

            # --- Gross P&L per leg on leveraged notional ---
            spot_gross = 0.0
            perp_gross = 0.0
            spot_ret_pct = 0.0
            perp_ret_pct = 0.0
            if pos["spot_entry"] and pos["spot_entry_price"] > 0:
                spot_ret_pct = (spot_now - pos["spot_entry_price"]) / pos["spot_entry_price"] * pos["spot_dir"]
                spot_gross = spot_notional * spot_ret_pct
            if pos["perp_entry"] and pos["perp_entry_price"] > 0:
                perp_ret_pct = (perp_now - pos["perp_entry_price"]) / pos["perp_entry_price"] * pos["perp_dir"]
                perp_gross = perp_notional * perp_ret_pct

            perp_funding = pos.get("accrued_funding", 0)
            spot_entry_fee = spot_notional * self.spot_fee if pos["spot_entry"] else 0
            perp_entry_fee = perp_notional * self.perp_fee if pos["perp_entry"] else 0

            pos["spot_unrealized_pnl"] = round(spot_gross, 2)
            pos["perp_unrealized_pnl"] = round(perp_gross, 2)
            pos["unrealized_pnl"] = round(spot_gross + perp_gross, 2)
            pos["spot_return_pct"] = round(spot_ret_pct * 100, 4) if pos["spot_entry"] else 0
            pos["perp_return_pct"] = round(perp_ret_pct * 100, 4) if pos["perp_entry"] else 0
            pos["current_spot"] = spot_now
            pos["current_perp"] = perp_now
            pos["spot_fees"] = round(spot_entry_fee, 2)
            pos["perp_fees"] = round(perp_entry_fee, 2)
            pos["perp_funding_cost"] = round(perp_funding, 2)

            # --- Trade management exit checks ---
            tp_spot = pos.get("spot_trade_params", {})
            tp_perp = pos.get("perp_trade_params", tp_spot)
            trail_sched = pos.get("trail_schedule")

            exit_reason = None
            if pos.get("spot_entry"):
                exit_reason = self._check_leg_exit(
                    spot_now, pos["spot_entry_price"], pos["spot_dir"],
                    pos.get("spot_entry_atr", 0), spot_size, spot_lev,
                    tp_spot, trail_sched,
                    pos.get("spot_highest", spot_now), pos.get("spot_lowest", spot_now),
                    bars_held,
                )
            if exit_reason is None and pos.get("perp_entry"):
                exit_reason = self._check_leg_exit(
                    perp_now, pos["perp_entry_price"], pos["perp_dir"],
                    pos.get("perp_entry_atr", 0), perp_size, perp_lev,
                    tp_perp, trail_sched,
                    pos.get("perp_highest", perp_now), pos.get("perp_lowest", perp_now),
                    bars_held, accrued_funding=perp_funding,
                )
            if exit_reason:
                pos["_exit_reason"] = exit_reason

    def to_dashboard_sim(self) -> dict:
        """Convert to dashboard-compatible simulation data.

        Each position is split into separate spot and perp leg trades.
        """
        all_trades = []

        # Closed trades — already stored as separate legs
        for t in self.closed_trades:
            leg = t.get("leg", "spot")
            mtype = leg  # "spot" or "perp"
            direction = t.get("direction", 1)
            dir_label = "SHORT" if direction == -1 else "LONG"

            lev = t.get("leverage", 1.0)
            all_trades.append({
                "token": t["token"],
                "strategy": self.label,
                "market_type": mtype,
                "entry_time": t["entry_time"],
                "exit_time": t["exit_time"],
                "status": "closed",
                "direction": direction,
                "entry_price": t.get("entry_price", 0),
                "exit_price": t.get("exit_price", 0),
                "pnl": t.get("pnl", 0),
                "return_pct": t.get("return_pct", 0),
                "position_usd": t.get("position_usd", 0),
                "exchange_fee": t.get("exchange_fee", 0),
                "funding_cost": t.get("funding_cost", 0),
                "hold_hours": t.get("hold_hours", 0),
                "exit_reason": t.get("exit_reason", ""),
                "signal": {
                    "regime": t.get("regime_at_entry", "?"),
                    "basis_bps": t.get("basis_bps_at_entry", 0),
                    "leverage": lev,
                    "size_multiplier": t.get("size_multiplier", 1.0),
                    "signal_short": f"{mtype.upper()} {dir_label} {lev}x | basis={t.get('basis_bps_at_entry', 0):+.1f}bps",
                },
            })

        # Open positions — split into legs
        for pos_key, pos in self.open_positions.items():
            token = self._real_token(pos_key)
            try:
                entry_dt = datetime.strptime(pos["entry_time"], "%Y-%m-%d %H:%M UTC")
                entry_dt = entry_dt.replace(tzinfo=timezone.utc)
                hold_hours = round(
                    (datetime.now(timezone.utc) - entry_dt).total_seconds() / 3600, 1,
                )
            except Exception:
                hold_hours = 0

            spot_size = pos.get("spot_size", pos.get("position_usd", 0) / 2)
            perp_size = pos.get("perp_size", pos.get("position_usd", 0) / 2)

            spot_lev = pos.get("leverage", 1.0)
            perp_lev = pos.get("secondary_leverage", pos.get("leverage", 1.0))
            sm = pos.get("size_multiplier", 1.0)

            if pos.get("spot_entry"):
                dir_val = pos.get("spot_dir", 1)
                dir_label = "SHORT" if dir_val == -1 else "LONG"
                all_trades.append({
                    "token": token, "strategy": self.label,
                    "market_type": "spot",
                    "entry_time": pos["entry_time"], "exit_time": "",
                    "status": "open",
                    "direction": dir_val,
                    "entry_price": pos.get("spot_entry_price", 0),
                    "current_price": pos.get("current_spot", pos.get("spot_entry_price", 0)),
                    "pnl": pos.get("spot_unrealized_pnl", 0),
                    "return_pct": pos.get("spot_return_pct", 0),
                    "position_usd": round(spot_size, 2),
                    "exchange_fee": pos.get("spot_fees", 0),
                    "funding_cost": 0,
                    "hold_hours": hold_hours, "exit_reason": "",
                    "signal": {
                        "regime": pos.get("regime_at_entry", "?"),
                        "basis_bps": pos.get("basis_bps_at_entry", 0),
                        "leverage": spot_lev,
                        "size_multiplier": sm,
                        "signal_short": f"SPOT {dir_label} {spot_lev}x sm={sm:.1f} | basis={pos.get('basis_bps_at_entry', 0):+.1f}bps",
                    },
                })

            if pos.get("perp_entry"):
                dir_val = pos.get("perp_dir", -1)
                dir_label = "SHORT" if dir_val == -1 else "LONG"
                all_trades.append({
                    "token": token, "strategy": self.label,
                    "market_type": "perp",
                    "entry_time": pos["entry_time"], "exit_time": "",
                    "status": "open",
                    "direction": dir_val,
                    "entry_price": pos.get("perp_entry_price", 0),
                    "current_price": pos.get("current_perp", pos.get("perp_entry_price", 0)),
                    "pnl": pos.get("perp_unrealized_pnl", 0),
                    "return_pct": pos.get("perp_return_pct", 0),
                    "position_usd": round(perp_size, 2),
                    "exchange_fee": pos.get("perp_fees", 0),
                    "funding_cost": pos.get("perp_funding_cost", 0),
                    "hold_hours": hold_hours, "exit_reason": "",
                    "signal": {
                        "regime": pos.get("regime_at_entry", "?"),
                        "basis_bps": pos.get("basis_bps_at_entry", 0),
                        "leverage": perp_lev,
                        "size_multiplier": sm,
                        "signal_short": f"PERP {dir_label} {perp_lev}x sm={sm:.1f} | basis={pos.get('basis_bps_at_entry', 0):+.1f}bps",
                    },
                })

        n_trades = len(all_trades)
        closed_trades = [t for t in all_trades if t["status"] == "closed"]
        wins = sum(1 for t in closed_trades if t.get("pnl", 0) > 0)
        n_open = sum(1 for t in all_trades if t["status"] == "open")
        total_fees = sum(t.get("exchange_fee", 0) for t in all_trades)
        total_funding = sum(t.get("funding_cost", 0) for t in all_trades)
        unrealized = sum(
            p.get("unrealized_pnl", 0) for p in self.open_positions.values()
        )
        # final_equity = base equity (with entry fees deducted) + unrealized P&L
        final_equity = self.equity + unrealized
        total_pnl = final_equity - self.capital

        strat_result = {
            "name": self.label,
            "market": "COMBINED",
            "starting_capital": self.capital,
            "final_equity": round(final_equity, 2),
            "pnl": round(total_pnl, 2),
            "return_pct": round(total_pnl / self.capital * 100, 2),
            "n_trades": n_trades,
            "n_open": n_open,
            "wins": wins,
            "win_rate": round(wins / max(len(closed_trades), 1) * 100, 1),
            "exchange_fees": round(total_fees, 2),
            "funding_fees": round(total_funding, 2),
        }

        # Build equity history from tick log
        equity_history = self._load_equity_history()

        return {
            "id": f"paper_{self.label}",
            "name": f"Paper: {self.label}",
            "capital": self.capital,
            "strategies": [strat_result],
            "all_trades": all_trades,
            "final_equity": round(final_equity, 2),
            "pnl": round(total_pnl, 2),
            "return_pct": round(total_pnl / self.capital * 100, 2),
            "n_trades": n_trades,
            "equity_history": equity_history,
            "spot_funds": round(self.spot_funds, 2),
            "perp_funds": round(self.perp_funds, 2),
            "rebalance_history": self.rebalance_history[-20:],
        }

    def _load_equity_history(self) -> list[dict]:
        """Load equity snapshots from tick log for charting."""
        log_path = os.path.join(self.state_dir, "logs", f"{self.label}.jsonl")
        if not os.path.exists(log_path):
            return []
        history = []
        seen = set()
        try:
            with open(log_path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    entry = json.loads(line)
                    tick = entry.get("tick", "")
                    eq = entry.get("equity")
                    if tick and eq is not None and tick not in seen:
                        seen.add(tick)
                        # Convert "2026-03-07 20:00 UTC" to "2026-03-07T20:00:00Z"
                        t_iso = tick.replace(" UTC", "").replace(" ", "T") + ":00Z"
                        history.append({"t": t_iso, "eq": round(eq, 2)})
        except Exception:
            pass
        return history


# ---------------------------------------------------------------------------
# Shared data fetch (called once per tick)
# ---------------------------------------------------------------------------

def fetch_all_live_data(
    fetcher: LiveFetcher, tokens: list[str],
) -> dict[str, dict[str, int | str]]:
    """Fetch live OHLCV for all tokens (spot + perp). Returns fetch summary."""
    summary: dict[str, dict] = {}
    for token in tokens:
        summary[token] = {}
        symbol = f"{token}/USDT"
        for market in ("spot", "perp"):
            try:
                bars = fetcher.fetch_ohlcv(
                    token=symbol, market=market, timeframe="1h", limit=10,
                )
                closed = fetcher.filter_closed_bars(bars, timeframe="1h")
                if closed:
                    fetcher.append_to_wal(
                        token=symbol, market=market, bars=closed,
                    )
                summary[token][market] = len(closed)
            except Exception as e:
                summary[token][market] = f"ERR: {e}"
        # Also fetch funding rates for perp strategies
        try:
            rates = fetcher.fetch_funding_rates(symbol)
            if rates:
                fetcher.append_funding_wal(symbol, rates)
            summary[token]["funding"] = len(rates)
        except Exception:
            summary[token]["funding"] = 0
    return summary


# ---------------------------------------------------------------------------
# Load rolling window for a token (last N bars only)
# ---------------------------------------------------------------------------

# 2000 bars = enough for 200-bar indicator warmup + 60 daily bars for regime.
# Produces identical indicators and regime as full history.
WINDOW_BARS = 2000


def load_bars(
    loader: DataLoader, token: str, market: str,
) -> list[dict]:
    """Load last WINDOW_BARS of history for incremental signal computation."""
    df = loader.load_token(f"{token}/USDT", market=market)
    if len(df) == 0:
        return []
    # Only keep the tail — indicators warm up in ~200 bars
    df = df.tail(WINDOW_BARS)
    return df.to_dict("records")


# ---------------------------------------------------------------------------
# Regime name lookup
# ---------------------------------------------------------------------------

_REGIME_NAMES = {0: "CRISIS", 1: "QUIET", 2: "UPTREND", 3: "RANGE", 4: "DOWNTREND"}


# ---------------------------------------------------------------------------
# Run one strategy scan across all tokens (incremental, last bar only)
# ---------------------------------------------------------------------------

def scan_strategy(
    engine: CombinedPaperEngine,
    loader: DataLoader,
    tokens: list[str],
) -> dict:
    """Evaluate the raw strategy signal at the latest bar for all tokens.

    Uses ``compute_latest_signal()`` — no full-history simulation replay.
    Indicators and regime are identical to the full backtest with 2000+ bars.
    """
    results: dict = {
        "strategy_id": engine.strategy_id,
        "capital": engine.capital,
        "tokens_scanned": 0,
        "tokens_with_entry": 0,
        "signals": {},
    }

    for token in tokens:
        spot_bars = load_bars(loader, token, "spot")
        perp_bars = load_bars(loader, token, "perp")

        if len(spot_bars) < WINDOW_BARS or len(perp_bars) < WINDOW_BARS:
            continue

        results["tokens_scanned"] += 1
        engine.tokens = [f"{token}/USDT"]

        try:
            sig = engine.compute_latest_signal(spot_bars, perp_bars)
            if "error" in sig:
                results["signals"][token] = sig
                continue

            has_entry = sig["spot_entry"] or sig["perp_entry"]
            if has_entry:
                results["tokens_with_entry"] += 1

            results["signals"][token] = {
                "spot_entry": sig["spot_entry"],
                "perp_entry": sig["perp_entry"],
                "spot_dir": sig["spot_direction"],
                "perp_dir": sig["perp_direction"],
                "regime": _REGIME_NAMES.get(sig["regime"], str(sig["regime"])),
                "in_exit_regime": sig["in_exit_regime"],
                "basis_bps": sig["basis_bps"],
                "spot_close": sig["spot_close"],
                "perp_close": sig["perp_close"],
                "spot_atr": sig.get("spot_atr", 0),
                "perp_atr": sig.get("perp_atr", 0),
                "sizing": sig.get("sizing", {}),
                "trade_params": sig.get("trade_params", {}),
                "sec_trade_params": sig.get("sec_trade_params", {}),
                "trail_schedule": sig.get("trail_schedule"),
                "spot_adv": sig.get("spot_adv", 5_000_000),
                "perp_adv": sig.get("perp_adv", 5_000_000),
                "capital_split": sig.get("capital_split", 0.5),
            }
        except Exception as e:
            results["signals"][token] = {"error": str(e)}

    return results


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------

def save_state(run_label: str, state: dict) -> None:
    """Persist run state to JSON."""
    os.makedirs(STATE_DIR, exist_ok=True)
    path = os.path.join(STATE_DIR, f"{run_label}.json")
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2, default=str)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def load_state(run_label: str) -> dict | None:
    """Load persisted run state."""
    path = os.path.join(STATE_DIR, f"{run_label}.json")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def append_log(run_label: str, entry: dict) -> None:
    """Append a tick log entry to JSONL log file."""
    os.makedirs(LOG_DIR, exist_ok=True)
    path = os.path.join(LOG_DIR, f"{run_label}.jsonl")
    with open(path, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


# ---------------------------------------------------------------------------
# Print helpers
# ---------------------------------------------------------------------------

def print_fetch_summary(summary: dict) -> None:
    """Print a compact fetch summary."""
    ok = sum(
        1 for t in summary.values()
        if isinstance(t.get("spot"), int) and isinstance(t.get("perp"), int)
    )
    errs = len(summary) - ok
    total_bars = sum(
        (t.get("spot", 0) if isinstance(t.get("spot"), int) else 0)
        + (t.get("perp", 0) if isinstance(t.get("perp"), int) else 0)
        for t in summary.values()
    )
    print(f"  Fetched: {ok}/{len(summary)} tokens OK, "
          f"{total_bars} new bars, {errs} errors")
    if errs:
        for token, data in summary.items():
            for market in ("spot", "perp"):
                if isinstance(data.get(market), str):
                    print(f"    {token}/{market}: {data[market]}")


def print_scan_results(results: dict) -> None:
    """Print strategy scan results."""
    label = results["strategy_id"]
    n_entry = results.get("tokens_with_entry", 0)
    print(f"\n  [{label}] Scanned {results['tokens_scanned']} tokens, "
          f"{n_entry} with ENTRY signal on latest bar")

    # Print tokens with entry signals first
    for token, data in sorted(results.get("signals", {}).items()):
        if "error" in data:
            print(f"    {token}: ERROR - {data['error']}")
        elif data.get("spot_entry") or data.get("perp_entry"):
            legs = []
            if data["spot_entry"]:
                legs.append(f"SPOT {'LONG' if data['spot_dir'] == 1 else 'SHORT'}")
            if data["perp_entry"]:
                legs.append(f"PERP {'LONG' if data['perp_dir'] == 1 else 'SHORT'}")
            print(f"    >> {token}: ENTRY {' + '.join(legs)} | "
                  f"basis={data['basis_bps']:+.1f}bps | "
                  f"regime={data['regime']}")

    # Summary of regime distribution
    regimes = {}
    for data in results.get("signals", {}).values():
        if isinstance(data, dict) and "regime" in data:
            r = data["regime"]
            regimes[r] = regimes.get(r, 0) + 1
    if regimes:
        regime_str = ", ".join(f"{k}:{v}" for k, v in sorted(regimes.items()))
        print(f"    Regimes: {regime_str}")


def print_status() -> None:
    """Print the last recorded state for each run."""
    for run_cfg in RUNS:
        label = run_cfg["label"]
        state = load_state(label)
        if state is None:
            print(f"[{label}] No state file found.")
            continue
        print(f"\n[{label}]")
        print(f"  Capital: ${run_cfg['capital']:,.0f}")
        print(f"  Equity: ${state.get('equity', run_cfg['capital']):,.0f}")
        spot_f = state.get('spot_funds', state.get('equity', run_cfg['capital']) / 2)
        perp_f = state.get('perp_funds', state.get('equity', run_cfg['capital']) / 2)
        total_f = spot_f + perp_f
        if total_f > 0:
            print(f"  Spot / Perp: ${spot_f:,.0f} ({spot_f/total_f*100:.0f}%) / "
                  f"${perp_f:,.0f} ({perp_f/total_f*100:.0f}%)")
        print(f"  Last tick: {state.get('last_tick', 'never')}")
        print(f"  Ticks completed: {state.get('tick_count', 0)}")
        print(f"  Tokens scanned: {state.get('tokens_scanned', 0)}")
        print(f"  Open positions: {state.get('open_positions', 0)}")
        print(f"  Closed trades: {state.get('closed_trades', 0)}")
        print(f"  Realized P&L: ${state.get('realized_pnl', 0):,.0f}")
        print(f"  Unrealized P&L: ${state.get('unrealized_pnl', 0):,.0f}")

        # Load position details from tracker file
        tracker = PaperPositionTracker(
            strategy_id=run_cfg["strategy_id"],
            label=label,
            capital=run_cfg["capital"],
            state_dir=STATE_DIR,
            data_dir=DATA_DIR,
            exchange=EXCHANGE,
        )
        if tracker.open_positions:
            print(f"  Open positions ({len(tracker.open_positions)}):")
            for pos_key, pos in tracker.open_positions.items():
                legs = []
                if pos.get("spot_entry"):
                    legs.append("SPOT")
                if pos.get("perp_entry"):
                    legs.append("PERP")
                pnl = pos.get("unrealized_pnl", 0)
                print(f"    {pos_key}: {'+'.join(legs)} | "
                      f"entry={pos.get('entry_time', '?')} | "
                      f"P&L=${pnl:+,.0f}")

        entries = state.get("entry_signals", {})
        if entries:
            print(f"  Entry signals ({len(entries)}):")
            for token, data in entries.items():
                print(f"    {token}: basis={data.get('basis_bps', '?')}bps "
                      f"regime={data.get('regime', '?')}")
        else:
            print(f"  No entry signals on last tick")


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run_tick(
    fetcher: LiveFetcher,
    loader: DataLoader,
    engines: dict[str, CombinedPaperEngine],
    trackers: dict[str, PaperPositionTracker],
    tokens: list[str],
) -> None:
    """Execute one tick: fetch data, scan both strategies, track positions."""
    now = datetime.now(timezone.utc)
    tick_str = now.strftime("%Y-%m-%d %H:%M UTC")
    print(f"\n{'='*60}")
    print(f"TICK @ {tick_str}")
    print(f"{'='*60}")

    # 1) Fetch live data (once, shared — union of all strategy tokens)
    all_tokens = set(tokens)
    for run_cfg in RUNS:
        all_tokens.update(run_cfg.get("tokens", []))
    all_tokens = sorted(all_tokens)
    print("\n[1/4] Fetching live data...")
    fetch_summary = fetch_all_live_data(fetcher, all_tokens)
    print_fetch_summary(fetch_summary)

    # 2) Run each strategy
    print("\n[2/4] Running strategy scans...")
    for run_cfg in RUNS:
        label = run_cfg["label"]
        tracker = trackers[label]
        strategy_tokens = run_cfg.get("tokens", tokens)

        if "sub_strategies" in run_cfg:
            # Multi-strategy: scan each sub-strategy, merge signals
            merged_signals = {}
            total_scanned = 0
            total_entries = 0
            for sub in run_cfg["sub_strategies"]:
                sub_label = f"{label}_{sub['tag']}"
                sub_engine = engines[sub_label]
                sub_results = scan_strategy(sub_engine, loader, strategy_tokens)
                for token, sig in sub_results.get("signals", {}).items():
                    merged_signals[f"{token}:{sub['tag']}"] = sig
                total_scanned = max(total_scanned, sub_results["tokens_scanned"])
                total_entries += sub_results.get("tokens_with_entry", 0)
            results = {
                "strategy_id": run_cfg["strategy_id"],
                "tokens_scanned": total_scanned,
                "tokens_with_entry": total_entries,
                "signals": merged_signals,
            }
        else:
            engine = engines[label]
            results = scan_strategy(engine, loader, strategy_tokens)

        print_scan_results(results)

        # 3) Process positions (open/close based on signals)
        pos_summary = tracker.process_signals(results.get("signals", {}), tick_str)

        if pos_summary["opened"]:
            print(f"    OPENED: {', '.join(pos_summary['opened'])}")
        if pos_summary["closed"]:
            print(f"    CLOSED: {', '.join(pos_summary['closed'])}")
        if pos_summary.get("rebalance"):
            rb = pos_summary["rebalance"]
            print(f"    REBALANCE: ${rb['amount']:,.0f} {rb['direction']}")
        n_open = len(tracker.open_positions)
        n_closed = len(tracker.closed_trades)
        unrealized = sum(
            p.get("unrealized_pnl", 0) for p in tracker.open_positions.values()
        )
        realized = sum(t.get("pnl", 0) for t in tracker.closed_trades)
        print(f"    Positions: {n_open} open, {n_closed} closed | "
              f"Equity: ${tracker.equity:,.0f} | "
              f"Realized: ${realized:,.0f} | Unrealized: ${unrealized:,.0f}")

        # 4) Persist state
        prev_state = load_state(label) or {"tick_count": 0}
        new_state = {
            "strategy_id": run_cfg["strategy_id"],
            "capital": run_cfg["capital"],
            "last_tick": tick_str,
            "tick_count": prev_state["tick_count"] + 1,
            "tokens_scanned": results["tokens_scanned"],
            "tokens_with_entry": results.get("tokens_with_entry", 0),
            "equity": round(tracker.equity, 2),
            "open_positions": len(tracker.open_positions),
            "closed_trades": len(tracker.closed_trades),
            "realized_pnl": round(realized, 2),
            "unrealized_pnl": round(unrealized, 2),
            "entry_signals": {
                t: d for t, d in results.get("signals", {}).items()
                if isinstance(d, dict) and (d.get("spot_entry") or d.get("perp_entry"))
            },
        }
        save_state(label, new_state)
        append_log(label, {
            "tick": tick_str,
            "tokens_scanned": results["tokens_scanned"],
            "tokens_with_entry": results.get("tokens_with_entry", 0),
            "opened": pos_summary["opened"],
            "closed": pos_summary["closed"],
            "equity": round(tracker.equity + unrealized, 2),
            "unrealized": round(unrealized, 2),
            "open_positions": n_open,
            "closed_trades": n_closed,
            "spot_funds": round(tracker.spot_funds, 2),
            "perp_funds": round(tracker.perp_funds, 2),
            "rebalance": pos_summary.get("rebalance"),
        })

    print(f"\n[3/4] State saved to {STATE_DIR}/")

    # 4) Regenerate dashboard
    print("\n[4/4] Updating dashboard...")
    try:
        import subprocess
        result = subprocess.run(
            [sys.executable, os.path.join(_PROJECT_DIR, "tools", "generate_dashboard.py"),
             "--paper", "--push"],
            capture_output=True, text=True, timeout=60,
            cwd=_PROJECT_DIR,
        )
        if result.returncode == 0:
            for line in result.stdout.strip().split("\n"):
                print(f"  {line}")
        else:
            print(f"  Dashboard error: {result.stderr[:200]}")
    except Exception as e:
        print(f"  Dashboard update failed: {e}")


def main():
    parser = argparse.ArgumentParser(
        description="Continuous paper trading: s30 + s32 + s54 + s56 + s57 with shared data feed",
    )
    parser.add_argument("--once", action="store_true", help="Run one tick and exit")
    parser.add_argument("--status", action="store_true", help="Print last state")
    parser.add_argument(
        "--tokens", type=str, default=None,
        help="Comma-separated token list (default: top 20 liquid tokens)",
    )
    args = parser.parse_args()

    if args.status:
        print_status()
        return

    tokens = args.tokens.split(",") if args.tokens else TOKENS
    print(f"Paper Trading Runner")
    print(f"  Strategies: {', '.join(r['strategy_id'] for r in RUNS)}")
    print(f"  Capital: ${sum(r['capital'] for r in RUNS):,.0f} total "
          f"(${RUNS[0]['capital']:,.0f} each)")
    print(f"  Tokens: {len(tokens)} ({', '.join(tokens[:5])}...)")
    print(f"  Exchange: {EXCHANGE}")
    print(f"  Mode: {'single scan' if args.once else 'continuous (hourly)'}")

    # Initialize shared fetcher + loader
    fetcher = LiveFetcher(exchange=EXCHANGE, data_dir=DATA_DIR)
    loader = DataLoader(data_dir=DATA_DIR, exchange=EXCHANGE)

    # Initialize engines + position trackers (one per strategy)
    engines: dict[str, CombinedPaperEngine] = {}
    trackers: dict[str, PaperPositionTracker] = {}
    for run_cfg in RUNS:
        label = run_cfg["label"]
        if "sub_strategies" in run_cfg:
            # Multi-strategy: create an engine per sub-strategy
            for sub in run_cfg["sub_strategies"]:
                sub_label = f"{label}_{sub['tag']}"
                engines[sub_label] = CombinedPaperEngine(config={
                    "strategy_id": sub["strategy_id"],
                    "capital": run_cfg["capital"],
                    "exchange": EXCHANGE,
                    "data_dir": DATA_DIR,
                    "state_dir": os.path.join(STATE_DIR, sub_label),
                    "market": sub.get("market", "combined"),
                })
        else:
            engines[label] = CombinedPaperEngine(config={
                "strategy_id": run_cfg["strategy_id"],
                "capital": run_cfg["capital"],
                "exchange": EXCHANGE,
                "data_dir": DATA_DIR,
                "state_dir": os.path.join(STATE_DIR, label),
                "market": run_cfg.get("market", "combined"),
            })
        trackers[label] = PaperPositionTracker(
            strategy_id=run_cfg["strategy_id"],
            label=label,
            capital=run_cfg["capital"],
            state_dir=STATE_DIR,
            data_dir=DATA_DIR,
            exchange=EXCHANGE,
            max_positions=run_cfg.get("max_positions", 15),
        )

    if args.once:
        run_tick(fetcher, loader, engines, trackers, tokens)
        return

    # Continuous loop
    print(f"\nStarting continuous paper trading (Ctrl+C to stop)...")
    while True:
        try:
            run_tick(fetcher, loader, engines, trackers, tokens)
        except KeyboardInterrupt:
            print("\nStopping paper trading.")
            break
        except Exception:
            traceback.print_exc()
            print("Error during tick, will retry next cycle.")

        # Sleep until next hour boundary
        now = time.time()
        next_hour = ((int(now) // 3600) + 1) * 3600
        sleep_s = next_hour - now + 5  # 5s buffer after hour boundary
        print(f"\nNext tick in {sleep_s/60:.1f} minutes "
              f"(at {datetime.fromtimestamp(next_hour, tz=timezone.utc).strftime('%H:%M UTC')})")
        try:
            time.sleep(sleep_s)
        except KeyboardInterrupt:
            print("\nStopping paper trading.")
            break


if __name__ == "__main__":
    main()
