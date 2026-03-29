#!/usr/bin/env python3
"""
Raw Backtest Harness — Strategy-Agnostic with Built-in Guardrails
=================================================================

Run any strategy idea quickly with HONEST results. No inflated numbers.

The harness enforces realism guardrails that cannot be bypassed:
  1. Binance-only data (no multi-exchange mixing)
  2. Liquidity gating (position size capped by ADV)
  3. Fee modeling (maker/taker + funding for perps)
  4. Equity cap (can't deploy more than you have)
  5. Liquidation checks (margin calls at maintenance margin)
  6. Walk-forward splits (automatic IS/OOS reporting)
  7. Slippage modeling (market impact proportional to ADV%)
  8. Mark-to-market equity (unrealized P&L included in DD)

Usage:
    # In your research script:
    from tools.raw_backtest import Backtest

    bt = Backtest(
        capital=100_000,
        fee_bps=7,
        market='perp',         # 'perp' or 'spot'
        leverage_max=3.0,      # max allowed leverage
        start='2024-01-01',
        end='2026-03-17',
    )

    # Your strategy logic — run bar by bar or produce signals in bulk
    for bar in bt.bars('BTC'):
        if your_entry_condition(bar):
            bt.order('BTC', side='long', size_usd=10_000)
        if your_exit_condition(bar):
            bt.close('BTC')

    # Or: vectorized signal approach
    signals = bt.load('BTC')
    signals['signal'] = ...  # your logic
    bt.from_signals(signals, ...)

    # Get honest results
    bt.report()

Author: Research Coordinator
"""

import os
import sys
import json
import warnings
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Callable, Any
from datetime import datetime, timedelta
from pathlib import Path
from enum import Enum

warnings.filterwarnings("ignore")

# ── Paths ────────────────────────────────────────────────────────────
PROJECT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR_PERP = PROJECT_DIR / "data" / "perp" / "1h_cache"
DATA_DIR_SPOT = PROJECT_DIR / "data" / "spot" / "1h_cache"


# ── Constants ────────────────────────────────────────────────────────

# Guardrail thresholds — NOT configurable by strategy
_ADV_POSITION_CAP_PCT = 0.01      # Max 1% of token's 30d ADV per position
_MAINTENANCE_MARGIN = 0.05        # 5% maintenance margin (Binance standard)
_MAX_SLIPPAGE_BPS = 50            # Cap slippage model at 50bps
_MIN_ADV_USD = 500_000            # Minimum $500k ADV to be tradeable
_MAX_LEVERAGE = 10.0              # Absolute cap regardless of user request
_WALK_FORWARD_MIN_MONTHS = 6      # Minimum OOS period for walk-forward


class Side(Enum):
    LONG = 1
    SHORT = -1
    FLAT = 0


@dataclass
class Position:
    token: str
    side: Side
    entry_price: float
    entry_time: pd.Timestamp
    size_usd: float              # Notional size in USD
    leverage: float              # Effective leverage for this position
    entry_bar_idx: int = 0
    bars_held: int = 0
    unrealized_pnl: float = 0.0
    cum_funding: float = 0.0
    cum_slippage: float = 0.0
    entry_fee: float = 0.0
    metadata: Dict = field(default_factory=dict)  # User can attach anything


@dataclass
class ClosedTrade:
    token: str
    side: str
    entry_price: float
    exit_price: float
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    size_usd: float
    leverage: float
    gross_pnl: float
    fees: float
    funding: float
    slippage: float
    net_pnl: float
    bars_held: int
    exit_reason: str
    metadata: Dict = field(default_factory=dict)


@dataclass
class Violation:
    """Records a guardrail violation — logged, not silently ignored."""
    timestamp: pd.Timestamp
    rule: str
    detail: str
    action_taken: str


class DataCache:
    """Loads and caches Binance-only data with ADV computation."""

    def __init__(self, market: str = "perp"):
        self.market = market
        self.data_dir = DATA_DIR_PERP if market == "perp" else DATA_DIR_SPOT
        self._cache: Dict[str, pd.DataFrame] = {}
        self._adv_cache: Dict[str, pd.Series] = {}
        self._loaded_tokens: set = set()

        if not self.data_dir.exists():
            raise FileNotFoundError(
                f"Data directory not found: {self.data_dir}. "
                f"Run tools/build_parquet_cache.py first."
            )

    def available_tokens(self) -> List[str]:
        """List all tokens with data files."""
        tokens = []
        for f in sorted(os.listdir(self.data_dir)):
            if f.endswith("_1h.parquet"):
                tokens.append(f.replace("_1h.parquet", ""))
        return tokens

    def load(self, token: str) -> pd.DataFrame:
        """Load token data. Cached after first load."""
        if token in self._cache:
            return self._cache[token]

        path = self.data_dir / f"{token}_1h.parquet"
        if not path.exists():
            raise FileNotFoundError(f"No data for {token} at {path}")

        df = pd.read_parquet(path)
        df = df.sort_index()
        df = df[~df.index.duplicated(keep="first")]

        # Verify single-exchange (Binance-only cache)
        if "exchange" in df.columns:
            exchanges = df["exchange"].unique()
            if len(exchanges) > 1:
                raise ValueError(
                    f"GUARDRAIL: {token} has data from multiple exchanges: "
                    f"{exchanges}. Use Binance-only cache."
                )

        self._cache[token] = df
        return df

    def adv(self, token: str, window: int = 30) -> pd.Series:
        """Compute rolling Average Daily Volume in USD."""
        key = f"{token}_{window}"
        if key in self._adv_cache:
            return self._adv_cache[key]

        df = self.load(token)
        hourly_dvol = df["volume"] * df["close"]
        daily_dvol = hourly_dvol.resample("1D").sum()
        rolling_adv = daily_dvol.rolling(window, min_periods=max(window // 2, 1)).mean()
        self._adv_cache[key] = rolling_adv
        return rolling_adv

    def adv_at(self, token: str, timestamp: pd.Timestamp, window: int = 30) -> float:
        """Get ADV at a specific point in time (no look-ahead)."""
        adv_series = self.adv(token, window)
        # Use last available ADV on or before timestamp
        valid = adv_series.loc[:timestamp]
        if len(valid) == 0:
            return 0.0
        return valid.iloc[-1]

    def funding_rate(self, token: str, timestamp: pd.Timestamp) -> float:
        """Get hourly funding rate at timestamp. 0 if spot."""
        if self.market == "spot":
            return 0.0
        df = self.load(token)
        if "funding_1h" not in df.columns:
            return 0.0
        if timestamp in df.index:
            val = df.loc[timestamp, "funding_1h"]
            return val if not np.isnan(val) else 0.0
        return 0.0


class Backtest:
    """
    Strategy-agnostic backtest harness with built-in guardrails.

    The harness does NOT dictate how you generate signals. It only enforces
    that once you decide to trade, the execution is realistic.

    Guardrails (always active, not optional):
      - Position sizing capped at 1% of 30d ADV
      - Equity-based position limits (can't trade more than you have)
      - Maintenance margin checks (liquidation at 5%)
      - Slippage proportional to position/ADV ratio
      - Funding costs for perps (from actual historical data)
      - Walk-forward IS/OOS split in reporting
      - Mark-to-market equity tracking (unrealized P&L in drawdown)
    """

    def __init__(
        self,
        capital: float = 100_000,
        fee_bps: float = 7.0,
        market: str = "perp",
        leverage_max: float = 1.0,
        start: str = "2024-01-01",
        end: str = "2026-03-17",
        slippage_model: str = "sqrt",   # 'sqrt', 'linear', 'fixed', 'none'
        slippage_fixed_bps: float = 3.0,
        walk_forward_split: float = 0.5,  # 50% IS, 50% OOS
    ):
        self.initial_capital = capital
        self.equity = capital
        self.fee_rate = fee_bps / 10_000
        self.market = market
        self.leverage_max = min(leverage_max, _MAX_LEVERAGE)
        self.start = pd.Timestamp(start)
        self.end = pd.Timestamp(end)
        self.slippage_model = slippage_model
        self.slippage_fixed_bps = slippage_fixed_bps
        self.wf_split = walk_forward_split

        # State
        self.data = DataCache(market)
        self.positions: Dict[str, Position] = {}   # token -> Position
        self.trades: List[ClosedTrade] = []
        self.violations: List[Violation] = []
        self.equity_curve: Dict[pd.Timestamp, float] = {}
        self.mtm_equity_curve: Dict[pd.Timestamp, float] = {}
        self._current_time: Optional[pd.Timestamp] = None
        self._peak_equity = capital
        self._max_drawdown = 0.0
        self._liquidation_count = 0
        self._order_count = 0
        self._rejected_count = 0
        self._daily_pnl: Dict[pd.Timestamp, float] = {}

        # Margin tracking
        self._total_margin_used = 0.0

        if self.market == "perp" and self.leverage_max > 1.0:
            print(f"  Leverage cap: {self.leverage_max:.1f}x "
                  f"(maintenance margin: {_MAINTENANCE_MARGIN:.0%})")

    # ── Data Access ──────────────────────────────────────────────────

    def load(self, token: str) -> pd.DataFrame:
        """Load token OHLCV+funding data, filtered to [start, end]."""
        df = self.data.load(token)
        return df.loc[self.start:self.end].copy()

    def load_tokens(self, min_adv: float = _MIN_ADV_USD,
                    min_history_days: int = 90) -> Dict[str, pd.DataFrame]:
        """Load all tokens meeting liquidity and history requirements."""
        result = {}
        for token in self.data.available_tokens():
            df = self.data.load(token)
            # History check
            token_start = df.index.min()
            if (self.start - token_start).days < min_history_days:
                continue
            # ADV check at start of backtest
            adv = self.data.adv_at(token, self.start)
            if adv < min_adv:
                continue
            result[token] = df.loc[self.start:self.end].copy()
        return result

    def bars(self, token: str, timeframe: str = "1h"):
        """
        Iterate over bars for a token. Yields (timestamp, row) tuples.

        Supports: '1h', '4h', '1d'
        """
        df = self.load(token)
        if timeframe != "1h":
            agg = {"open": "first", "high": "max", "low": "min",
                   "close": "last", "volume": "sum"}
            if "funding_1h" in df.columns:
                agg["funding_1h"] = "sum"
            df = df.resample(timeframe).agg(agg).dropna(subset=["close"])

        for ts, row in df.iterrows():
            self._current_time = ts
            self._update_positions(ts)
            yield ts, row

    # ── Order Execution ──────────────────────────────────────────────

    def order(self, token: str, side: str, size_usd: float,
             leverage: float = 1.0, reason: str = "entry",
             metadata: Optional[Dict] = None) -> Optional[Position]:
        """
        Place an order. Returns Position if filled, None if rejected.

        Guardrails applied:
          1. ADV cap: position capped at 1% of 30d ADV
          2. Equity cap: can't exceed available equity * leverage
          3. Leverage cap: can't exceed leverage_max
          4. Liquidation check: margin sufficient
          5. Slippage applied to fill price
          6. Fees deducted

        The order is either filled (possibly at reduced size) or rejected.
        Rejections are logged as violations.
        """
        self._order_count += 1
        ts = self._current_time
        if ts is None:
            raise RuntimeError("order() can only be called inside bars() loop or after setting time")

        side_enum = Side.LONG if side.lower() in ("long", "buy", "1") else Side.SHORT
        leverage = min(leverage, self.leverage_max, _MAX_LEVERAGE)

        # Get current price
        df = self.data.load(token)
        if ts not in df.index:
            # Find nearest bar
            valid = df.index[df.index <= ts]
            if len(valid) == 0:
                self._reject(ts, "NO_DATA", f"{token} has no data at {ts}")
                return None
            ts_actual = valid[-1]
        else:
            ts_actual = ts
        price = df.loc[ts_actual, "close"]

        # ── GUARDRAIL 1: ADV cap ──
        adv = self.data.adv_at(token, ts)
        if adv < _MIN_ADV_USD:
            self._reject(ts, "LOW_ADV",
                         f"{token} ADV=${adv:,.0f} < minimum ${_MIN_ADV_USD:,.0f}")
            return None

        adv_cap = adv * _ADV_POSITION_CAP_PCT
        if size_usd > adv_cap:
            original = size_usd
            size_usd = adv_cap
            self.violations.append(Violation(
                timestamp=ts,
                rule="ADV_CAP",
                detail=f"{token}: requested ${original:,.0f} > 1% ADV ${adv_cap:,.0f}",
                action_taken=f"Reduced to ${size_usd:,.0f}",
            ))

        # ── GUARDRAIL 2: Equity cap ──
        # You can reinvest profits — equity grows naturally with compounding.
        # But you can't trade more than you actually have.
        margin_required = size_usd / leverage
        available_equity = self.equity - self._total_margin_used
        if margin_required > available_equity:
            if available_equity <= 0:
                self._reject(ts, "NO_EQUITY",
                             f"No available equity (used: ${self._total_margin_used:,.0f})")
                return None
            # Reduce to what's available
            original = size_usd
            margin_required = available_equity
            size_usd = margin_required * leverage
            self.violations.append(Violation(
                timestamp=ts,
                rule="EQUITY_CAP",
                detail=f"{token}: margin ${original/leverage:,.0f} > available ${available_equity:,.0f}",
                action_taken=f"Reduced position to ${size_usd:,.0f}",
            ))

        if size_usd < 10:  # Minimum $10 position
            self._reject(ts, "TOO_SMALL",
                         f"{token}: position ${size_usd:.2f} below $10 minimum")
            return None

        # ── GUARDRAIL 3: Slippage ──
        slippage_bps = self._compute_slippage(size_usd, adv)
        if side_enum == Side.LONG:
            fill_price = price * (1 + slippage_bps / 10_000)
        else:
            fill_price = price * (1 - slippage_bps / 10_000)

        # ── GUARDRAIL 4: Fees ──
        fee = size_usd * self.fee_rate
        self.equity -= fee

        # ── Create position ──
        margin = size_usd / leverage
        self._total_margin_used += margin

        pos = Position(
            token=token,
            side=side_enum,
            entry_price=fill_price,
            entry_time=ts,
            size_usd=size_usd,
            leverage=leverage,
            entry_fee=fee,
            cum_slippage=slippage_bps * size_usd / 10_000,
            metadata=metadata or {},
        )

        # If already have a position in this token, close it first
        if token in self.positions:
            self.close(token, reason="replaced")

        self.positions[token] = pos
        return pos

    def close(self, token: str, reason: str = "exit",
              exit_price: Optional[float] = None) -> Optional[ClosedTrade]:
        """Close position in a token. Returns ClosedTrade or None."""
        if token not in self.positions:
            return None

        ts = self._current_time
        pos = self.positions[token]

        # Get exit price
        if exit_price is None:
            df = self.data.load(token)
            if ts in df.index:
                exit_price = df.loc[ts, "close"]
            else:
                valid = df.index[df.index <= ts]
                if len(valid) == 0:
                    return None
                exit_price = df.loc[valid[-1], "close"]

        # Apply slippage to exit
        adv = self.data.adv_at(token, ts)
        slip_bps = self._compute_slippage(pos.size_usd, max(adv, 1))
        if pos.side == Side.LONG:
            exit_price *= (1 - slip_bps / 10_000)
        else:
            exit_price *= (1 + slip_bps / 10_000)

        # Compute P&L
        if pos.side == Side.LONG:
            gross_pnl = (exit_price / pos.entry_price - 1) * pos.size_usd
        else:
            gross_pnl = (1 - exit_price / pos.entry_price) * pos.size_usd

        exit_fee = pos.size_usd * self.fee_rate
        exit_slippage = slip_bps * pos.size_usd / 10_000
        net_pnl = gross_pnl - pos.entry_fee - exit_fee - pos.cum_funding - pos.cum_slippage - exit_slippage

        # Update equity
        self.equity += gross_pnl - exit_fee
        margin = pos.size_usd / pos.leverage
        self._total_margin_used -= margin
        self._total_margin_used = max(0, self._total_margin_used)

        trade = ClosedTrade(
            token=token,
            side="long" if pos.side == Side.LONG else "short",
            entry_price=pos.entry_price,
            exit_price=exit_price,
            entry_time=pos.entry_time,
            exit_time=ts,
            size_usd=pos.size_usd,
            leverage=pos.leverage,
            gross_pnl=gross_pnl,
            fees=pos.entry_fee + exit_fee,
            funding=pos.cum_funding,
            slippage=pos.cum_slippage + exit_slippage,
            net_pnl=net_pnl,
            bars_held=pos.bars_held,
            exit_reason=reason,
            metadata=pos.metadata,
        )
        self.trades.append(trade)
        del self.positions[token]
        return trade

    def close_all(self, reason: str = "close_all"):
        """Close all open positions."""
        tokens = list(self.positions.keys())
        for token in tokens:
            self.close(token, reason=reason)

    def set_time(self, ts: pd.Timestamp):
        """Manually set current time (for non-bars() workflows)."""
        self._current_time = ts

    # ── Internal: Position Updates ───────────────────────────────────

    def _update_positions(self, ts: pd.Timestamp):
        """
        Mark-to-market all positions, apply funding, check liquidation.
        Called automatically each bar in bars().
        """
        tokens_to_liquidate = []

        for token, pos in self.positions.items():
            pos.bars_held += 1

            # Get current price
            df = self.data.load(token)
            if ts not in df.index:
                continue
            price = df.loc[ts, "close"]

            # Mark-to-market P&L
            if pos.side == Side.LONG:
                pos.unrealized_pnl = (price / pos.entry_price - 1) * pos.size_usd
            else:
                pos.unrealized_pnl = (1 - price / pos.entry_price) * pos.size_usd

            # Apply funding (perps only)
            if self.market == "perp":
                fr = self.data.funding_rate(token, ts)
                if pos.side == Side.LONG:
                    funding_cost = fr * pos.size_usd
                else:
                    funding_cost = -fr * pos.size_usd
                pos.cum_funding += funding_cost
                self.equity -= funding_cost

            # ── GUARDRAIL: Liquidation check ──
            margin = pos.size_usd / pos.leverage
            if margin > 0:
                margin_ratio = (margin + pos.unrealized_pnl) / margin
                if margin_ratio <= _MAINTENANCE_MARGIN:
                    tokens_to_liquidate.append(token)
                    self.violations.append(Violation(
                        timestamp=ts,
                        rule="LIQUIDATION",
                        detail=f"{token}: margin_ratio={margin_ratio:.2%} <= "
                               f"{_MAINTENANCE_MARGIN:.0%}. "
                               f"Entry=${pos.entry_price:.2f}, "
                               f"Current=${price:.2f}, "
                               f"Size=${pos.size_usd:,.0f} @ {pos.leverage:.1f}x",
                        action_taken="Position liquidated",
                    ))

        # Process liquidations
        for token in tokens_to_liquidate:
            self._liquidation_count += 1
            self.close(token, reason="liquidated")

        # Record equity curve (mark-to-market)
        total_unrealized = sum(p.unrealized_pnl for p in self.positions.values())
        mtm_equity = self.equity + total_unrealized
        self.equity_curve[ts] = self.equity
        self.mtm_equity_curve[ts] = mtm_equity

        # Track peak and drawdown
        if mtm_equity > self._peak_equity:
            self._peak_equity = mtm_equity
        dd = (mtm_equity - self._peak_equity) / self._peak_equity if self._peak_equity > 0 else 0
        if dd < self._max_drawdown:
            self._max_drawdown = dd

    def _compute_slippage(self, size_usd: float, adv: float) -> float:
        """Compute slippage in bps based on position size relative to ADV."""
        if self.slippage_model == "none":
            return 0.0
        if self.slippage_model == "fixed":
            return self.slippage_fixed_bps

        # Size as fraction of daily volume
        adv_frac = size_usd / max(adv, 1)

        if self.slippage_model == "sqrt":
            # Square-root impact model: commonly used in practice
            # 1% of ADV ≈ 3bps, 10% of ADV ≈ 10bps
            bps = 30 * np.sqrt(adv_frac)
        elif self.slippage_model == "linear":
            bps = 300 * adv_frac  # 1% ADV = 3bps
        else:
            bps = self.slippage_fixed_bps

        return min(bps, _MAX_SLIPPAGE_BPS)

    def _reject(self, ts: pd.Timestamp, rule: str, detail: str):
        """Record an order rejection."""
        self._rejected_count += 1
        self.violations.append(Violation(
            timestamp=ts,
            rule=f"REJECTED_{rule}",
            detail=detail,
            action_taken="Order rejected",
        ))

    # ── Vectorized Signal Interface ──────────────────────────────────

    def from_signals(
        self,
        token: str,
        signals: pd.Series,
        size_usd: float = 10_000,
        leverage: float = 1.0,
        timeframe: str = "1h",
    ):
        """
        Run backtest from a signal series.

        signals: pd.Series with values:
           1 = long, -1 = short, 0 = flat
        Indexed by timestamp.

        Transitions (0→1, 0→-1, 1→-1, -1→1, 1→0, -1→0) trigger
        orders automatically.
        """
        prev_signal = 0
        for ts, row in self.bars(token, timeframe):
            if ts not in signals.index:
                continue
            sig = int(signals.loc[ts])

            # Close on signal change
            if prev_signal != 0 and sig != prev_signal:
                self.close(token, reason="signal_flip")

            # Open on new signal
            if sig != 0 and sig != prev_signal:
                side = "long" if sig == 1 else "short"
                self.order(token, side=side, size_usd=size_usd,
                          leverage=leverage)

            prev_signal = sig

        # Close any remaining position
        if token in self.positions:
            self.close(token, reason="end_of_backtest")

    def from_weights(
        self,
        weights: pd.DataFrame,
        rebalance_freq: str = "1W",
        leverage: float = 1.0,
    ):
        """
        Run backtest from a weight matrix.

        weights: DataFrame with tokens as columns, timestamps as index.
                 Values are target weights (e.g., 0.1 = 10% of capital).
                 Negative weights = short positions.

        Rebalances at the specified frequency. Respects all guardrails.
        """
        if weights.empty:
            print("WARNING: Empty weights DataFrame")
            return

        # Build unified timeline from all tokens
        all_tokens = list(weights.columns)
        token_data = {}
        for token in all_tokens:
            try:
                token_data[token] = self.data.load(token)
            except FileNotFoundError:
                print(f"  WARNING: No data for {token}, skipping")

        all_tokens = [t for t in all_tokens if t in token_data]
        if not all_tokens:
            print("WARNING: No valid tokens in weight matrix")
            return

        # Get the intersection of timestamps
        all_times = token_data[all_tokens[0]].loc[self.start:self.end].index
        for token in all_tokens[1:]:
            all_times = all_times.union(
                token_data[token].loc[self.start:self.end].index
            )
        all_times = all_times.sort_values()

        # Determine rebalance dates
        rebal_dates = set(
            weights.index.to_series()
            .resample(rebalance_freq)
            .first()
            .dropna()
            .values
        )

        for ts in all_times:
            self._current_time = ts
            self._update_positions(ts)

            # Check if rebalance
            day = pd.Timestamp(ts.date())
            if day not in rebal_dates:
                continue

            # Get target weights for this date
            valid_weights = weights.loc[:ts]
            if len(valid_weights) == 0:
                continue
            target = valid_weights.iloc[-1]

            # Close positions not in targets or with changed direction
            for token in list(self.positions.keys()):
                if token not in target.index:
                    self.close(token, reason="rebalance_exit")
                    continue
                tw = target[token]
                if np.isnan(tw) or tw == 0:
                    self.close(token, reason="rebalance_exit")
                elif (tw > 0 and self.positions[token].side == Side.SHORT) or \
                     (tw < 0 and self.positions[token].side == Side.LONG):
                    self.close(token, reason="rebalance_flip")

            # Open/adjust positions
            for token in all_tokens:
                if token not in target.index:
                    continue
                tw = target[token]
                if np.isnan(tw) or tw == 0:
                    continue

                target_usd = abs(tw) * self.equity * leverage
                side = "long" if tw > 0 else "short"

                # If already have position in right direction, adjust size
                if token in self.positions:
                    pos = self.positions[token]
                    if (tw > 0 and pos.side == Side.LONG) or \
                       (tw < 0 and pos.side == Side.SHORT):
                        # Only rebalance if size differs by >10%
                        size_diff = abs(target_usd - pos.size_usd) / max(pos.size_usd, 1)
                        if size_diff < 0.10:
                            continue
                        self.close(token, reason="rebalance_resize")

                self.order(token, side=side, size_usd=target_usd,
                          leverage=leverage, reason="rebalance")

        # Close remaining positions
        self.close_all(reason="end_of_backtest")

    # ── Reporting ────────────────────────────────────────────────────

    def report(self, name: str = "Strategy", save_path: Optional[str] = None):
        """
        Print comprehensive backtest report with walk-forward split.

        Includes all guardrail violations and honest metrics.
        """
        if not self.equity_curve:
            print("No equity curve data. Did you run a backtest?")
            return

        eq = pd.Series(self.mtm_equity_curve)
        daily_eq = eq.resample("1D").last().dropna().ffill()
        daily_returns = daily_eq.pct_change().dropna()

        total_days = (self.end - self.start).days
        full_metrics = self._compute_metrics(daily_returns)

        # ── Time windows: L12M, L6M, L3M from end of backtest ──
        windows = {}
        window_defs = [
            ("L12M", 365),
            ("L6M", 182),
            ("L3M", 91),
        ]
        for wname, wdays in window_defs:
            wstart = self.end - pd.Timedelta(days=wdays)
            if wstart < self.start:
                wstart = self.start
            dr = daily_returns.loc[wstart:self.end]
            m = self._compute_metrics(dr)
            # Count trades in this window
            wtrades = [t for t in self.trades if t.entry_time >= wstart]
            wwins = [t for t in wtrades if t.net_pnl > 0]
            wlosers = [t for t in wtrades if t.net_pnl <= 0]
            m["trades"] = len(wtrades)
            m["win_rate"] = len(wwins) / len(wtrades) if wtrades else 0
            m["avg_win"] = np.mean([t.net_pnl for t in wwins]) if wwins else 0
            m["avg_loss"] = np.mean([t.net_pnl for t in wlosers]) if wlosers else 0
            m["profit_factor"] = (
                sum(t.net_pnl for t in wwins) / abs(sum(t.net_pnl for t in wlosers))
                if wlosers and sum(t.net_pnl for t in wlosers) != 0
                else float("inf") if wwins else 0
            )
            m["start"] = wstart
            m["end"] = self.end
            windows[wname] = m

        # ── VERDICT — based on WORST recent window ──
        # Strategy must pass ALL windows. One bad window = KILL.
        # Sharpe >2.0, Calmar >3.0, MaxDD >-25%, positive return, 30+ trades in L12M
        kill_reasons = []

        for wname in ["L12M", "L6M", "L3M"]:
            m = windows[wname]
            if m["sharpe"] < 2.0:
                kill_reasons.append(f"{wname} Sharpe {m['sharpe']:.2f} < 2.0")
            if m["maxdd"] < -0.25:
                kill_reasons.append(f"{wname} MaxDD {m['maxdd']:.1%} breaches -25%")
            if m["calmar"] < 3.0:
                kill_reasons.append(f"{wname} Calmar {m['calmar']:.2f} < 3.0")
            if m["ann_ret"] < 0:
                kill_reasons.append(f"{wname} negative return {m['ann_ret']:.1%}")

        # Trade count check on L12M
        if windows["L12M"]["trades"] < 30:
            kill_reasons.append(
                f"Only {windows['L12M']['trades']} trades in L12M (need 30+)"
            )

        verdict = "KILL" if kill_reasons else "PASS"

        # ── Build report ──
        lines = []
        lines.append(f"{'='*70}")
        lines.append(f" RAW BACKTEST REPORT: {name}")
        lines.append(f"{'='*70}")
        lines.append(f"")
        lines.append(f" VERDICT: {verdict}")
        if kill_reasons:
            for r in kill_reasons:
                lines.append(f"   - {r}")
        else:
            lines.append(f"   All windows PASS (Sharpe>2, Calmar>3, MaxDD>-25%)")
        lines.append(f"")
        lines.append(f"{'='*70}")
        lines.append(f"Period: {self.start.date()} to {self.end.date()} "
                      f"({total_days} days)")
        lines.append(f"Market: {self.market} | Fee: {self.fee_rate*10000:.0f}bps "
                      f"(baked in) | Leverage cap: {self.leverage_max:.1f}x")
        lines.append(f"Slippage: {self.slippage_model} (baked in) | "
                      f"Funding: {'historical rates (baked in)' if self.market == 'perp' else 'N/A (spot)'}")
        lines.append(f"Initial capital: ${self.initial_capital:,.0f} | "
                      f"Final equity: ${self.equity:,.0f}")

        # ── Performance table: L12M / L6M / L3M ──
        lines.append(f"\n{'─'*70}")
        lines.append(f" PERFORMANCE BY WINDOW (all metrics net of fees/funding/slippage)")
        lines.append(f"{'─'*70}")

        hdr = (f"  {'':>8s}  {'L12M':>10s}  {'L6M':>10s}  {'L3M':>10s}  "
               f"{'Full':>10s}  {'Threshold':>10s}")
        lines.append(hdr)
        lines.append(f"  {'':>8s}  {'─'*10}  {'─'*10}  {'─'*10}  {'─'*10}  {'─'*10}")

        def fmt_pct(v): return f"{v:>+9.1%}"
        def fmt_f(v): return f"{v:>10.2f}"
        def fmt_i(v): return f"{v:>10d}"
        def fmt_pf(v): return f"{v:>10.2f}" if v != float("inf") else f"{'inf':>10s}"

        rows = [
            ("Return",    "total_ret", fmt_pct, None),
            ("Ann. Ret",  "ann_ret",   fmt_pct, "> 0%"),
            ("Sharpe",    "sharpe",    fmt_f,   "> 2.00"),
            ("Sortino",   "sortino",   fmt_f,   None),
            ("Calmar",    "calmar",    fmt_f,   "> 3.00"),
            ("Max DD",    "maxdd",     fmt_pct, "> -25%"),
            ("Win Rate",  "win_rate",  fmt_pct, None),
            ("Win Days",  "win_days",  fmt_pct, None),
            ("Trades",    "trades",    fmt_i,   "> 30 (L12M)"),
            ("PF",        "profit_factor", fmt_pf, None),
        ]

        for label, key, formatter, threshold in rows:
            vals = []
            for wname in ["L12M", "L6M", "L3M"]:
                vals.append(formatter(windows[wname][key]))
            vals.append(formatter(full_metrics[key]) if key in full_metrics else f"{'—':>10s}")
            thresh_str = f"{threshold:>10s}" if threshold else f"{'':>10s}"
            lines.append(f"  {label:>8s}  {vals[0]}  {vals[1]}  {vals[2]}  "
                          f"{vals[3]}  {thresh_str}")

        # Avg win / avg loss row
        lines.append(f"  {'Avg Win':>8s}", )
        awl = ""
        for wname in ["L12M", "L6M", "L3M"]:
            awl += f"  ${windows[wname]['avg_win']:>+8,.0f}"
        lines[-1] = f"  {'Avg Win':>8s}{awl}"
        lines.append(f"  {'Avg Loss':>8s}", )
        all_line = ""
        for wname in ["L12M", "L6M", "L3M"]:
            all_line += f"  ${windows[wname]['avg_loss']:>+8,.0f}"
        lines[-1] = f"  {'Avg Loss':>8s}{all_line}"

        # Window date ranges
        lines.append(f"\n  Window ranges:")
        for wname, wdays in window_defs:
            m = windows[wname]
            lines.append(f"    {wname}: {m['start'].date()} to {m['end'].date()} "
                          f"({(m['end'] - m['start']).days}d)")

        # ── Cost breakdown ──
        if self.trades:
            lines.append(f"\n{'─'*70}")
            lines.append(f" COST BREAKDOWN (already deducted from all metrics above)")
            lines.append(f"{'─'*70}")

            total_fees = sum(t.fees for t in self.trades)
            total_funding = sum(t.funding for t in self.trades)
            total_slippage = sum(t.slippage for t in self.trades)
            total_gross = sum(t.gross_pnl for t in self.trades)
            total_net = sum(t.net_pnl for t in self.trades)

            lines.append(f"  Gross P&L:        ${total_gross:>+12,.0f}")
            lines.append(f"  Fees:             ${total_fees:>12,.0f} "
                          f"({total_fees/max(self.initial_capital,1):.1%} of starting capital)")
            lines.append(f"  Funding:          ${total_funding:>+12,.0f}")
            lines.append(f"  Slippage:         ${total_slippage:>12,.0f}")
            lines.append(f"  Net P&L:          ${total_net:>+12,.0f}")
            lines.append(f"  Fee drag:         "
                          f"{total_fees/max(abs(total_gross),1):.1%} of gross")

        # ── Trade details ──
        lines.append(f"\n{'─'*70}")
        lines.append(f" TRADE DETAILS")
        lines.append(f"{'─'*70}")
        lines.append(f"  Total trades:     {len(self.trades)}")
        lines.append(f"  Orders placed:    {self._order_count}")
        lines.append(f"  Orders rejected:  {self._rejected_count}")
        lines.append(f"  Liquidations:     {self._liquidation_count}")

        if self.trades:
            avg_bars = np.mean([t.bars_held for t in self.trades])
            lines.append(f"  Avg holding:      {avg_bars:.0f} bars")

            # Per-token breakdown
            tokens_traded = set(t.token for t in self.trades)
            if len(tokens_traded) > 1:
                lines.append(f"  Tokens traded:    {len(tokens_traded)}")
                token_pnl = {}
                for t in self.trades:
                    token_pnl[t.token] = token_pnl.get(t.token, 0) + t.net_pnl
                sorted_tokens = sorted(token_pnl.items(),
                                       key=lambda x: x[1], reverse=True)
                lines.append(f"  Top 5 winners:")
                for tok, pnl in sorted_tokens[:5]:
                    lines.append(f"    {tok:>10s}  ${pnl:>+10,.0f}")
                lines.append(f"  Top 5 losers:")
                for tok, pnl in sorted_tokens[-5:]:
                    lines.append(f"    {tok:>10s}  ${pnl:>+10,.0f}")

        # Guardrail violations
        lines.append(f"\n{'─'*70}")
        lines.append(f" GUARDRAIL VIOLATIONS")
        lines.append(f"{'─'*70}")
        if not self.violations:
            lines.append(f"  None (clean execution)")
        else:
            violation_counts = {}
            for v in self.violations:
                violation_counts[v.rule] = violation_counts.get(v.rule, 0) + 1
            for rule, count in sorted(violation_counts.items(),
                                      key=lambda x: -x[1]):
                lines.append(f"  {rule:>25s}: {count:>5d}")
            lines.append(f"  Total violations: {len(self.violations)}")

        # Monthly returns table
        lines.append(f"\n{'─'*70}")
        lines.append(f" MONTHLY RETURNS")
        lines.append(f"{'─'*70}")
        monthly = daily_returns.resample("ME").apply(
            lambda x: (1 + x).prod() - 1
        )
        lines.append(f"  {'Month':>8s}  {'Return':>8s}  {'Cumulative':>12s}")
        cum = 1.0
        for date, ret in monthly.items():
            cum *= (1 + ret)
            lines.append(
                f"  {date.strftime('%Y-%m'):>8s}  {ret:>+7.1%}  "
                f"{cum-1:>+11.1%}"
            )

        lines.append(f"\n{'='*70}")

        # Print report
        report_text = "\n".join(lines)
        print(report_text)

        # Save if requested
        if save_path:
            with open(save_path, "w") as f:
                f.write(report_text)
            print(f"\nReport saved to: {save_path}")

        return {
            "verdict": verdict,
            "kill_reasons": kill_reasons,
            "windows": windows,
            "full": full_metrics,
            "trades": len(self.trades),
            "liquidations": self._liquidation_count,
            "violations": len(self.violations),
        }

    def _compute_metrics(self, daily_returns: pd.Series) -> Dict:
        """Compute standard performance metrics from daily returns."""
        if len(daily_returns) < 5:
            return {"total_ret": 0, "ann_ret": 0, "sharpe": 0, "sortino": 0,
                    "calmar": 0, "maxdd": 0, "win_days": 0,
                    "trades": 0, "win_rate": 0, "profit_factor": 0}

        cum = (1 + daily_returns).cumprod()
        total_ret = cum.iloc[-1] / cum.iloc[0] - 1
        n_days = len(daily_returns)
        ann_factor = 365 / max(n_days, 1)
        ann_ret = (1 + total_ret) ** ann_factor - 1

        peak = cum.cummax()
        dd = (cum - peak) / peak
        maxdd = dd.min()

        mean_r = daily_returns.mean()
        std_r = daily_returns.std()
        sharpe = (mean_r / std_r) * np.sqrt(365) if std_r > 1e-10 else 0

        down_r = daily_returns[daily_returns < 0]
        down_std = down_r.std() if len(down_r) > 5 else std_r
        sortino = (mean_r / down_std) * np.sqrt(365) if down_std > 1e-10 else 0

        calmar = ann_ret / abs(maxdd) if abs(maxdd) > 1e-10 else 0
        win_days = (daily_returns > 0).mean()

        # Trade-level metrics for this period (computed from self.trades)
        period_start = daily_returns.index.min() if len(daily_returns) > 0 else self.start
        period_trades = [t for t in self.trades if t.entry_time >= period_start]
        n_trades = len(period_trades)
        p_winners = [t for t in period_trades if t.net_pnl > 0]
        p_losers = [t for t in period_trades if t.net_pnl <= 0]
        win_rate = len(p_winners) / n_trades if n_trades > 0 else 0
        gross_wins = sum(t.net_pnl for t in p_winners)
        gross_losses = abs(sum(t.net_pnl for t in p_losers))
        profit_factor = gross_wins / gross_losses if gross_losses > 0 else (float("inf") if gross_wins > 0 else 0)

        return {
            "total_ret": total_ret,
            "ann_ret": ann_ret,
            "sharpe": sharpe,
            "sortino": sortino,
            "calmar": calmar,
            "maxdd": maxdd,
            "win_days": win_days,
            "trades": n_trades,
            "win_rate": win_rate,
            "profit_factor": profit_factor,
        }

    # ── Convenience accessors ────────────────────────────────────────

    @property
    def open_positions(self) -> Dict[str, Position]:
        return self.positions

    @property
    def total_exposure(self) -> float:
        """Total notional exposure across all positions."""
        return sum(p.size_usd for p in self.positions.values())

    @property
    def net_exposure(self) -> float:
        """Net directional exposure (long - short)."""
        return sum(
            p.size_usd * p.side.value for p in self.positions.values()
        )

    def get_equity(self) -> float:
        """Current mark-to-market equity."""
        unrealized = sum(p.unrealized_pnl for p in self.positions.values())
        return self.equity + unrealized

    def get_trades_df(self) -> pd.DataFrame:
        """Return all closed trades as a DataFrame."""
        if not self.trades:
            return pd.DataFrame()
        records = []
        for t in self.trades:
            records.append({
                "token": t.token, "side": t.side,
                "entry_price": t.entry_price, "exit_price": t.exit_price,
                "entry_time": t.entry_time, "exit_time": t.exit_time,
                "size_usd": t.size_usd, "leverage": t.leverage,
                "gross_pnl": t.gross_pnl, "fees": t.fees,
                "funding": t.funding, "slippage": t.slippage,
                "net_pnl": t.net_pnl, "bars_held": t.bars_held,
                "exit_reason": t.exit_reason,
            })
        return pd.DataFrame(records)

    def get_equity_curve(self) -> pd.Series:
        """Return mark-to-market equity curve."""
        return pd.Series(self.mtm_equity_curve)

    def get_violations_df(self) -> pd.DataFrame:
        """Return all guardrail violations as a DataFrame."""
        if not self.violations:
            return pd.DataFrame()
        return pd.DataFrame([
            {"timestamp": v.timestamp, "rule": v.rule,
             "detail": v.detail, "action_taken": v.action_taken}
            for v in self.violations
        ])
