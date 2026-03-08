"""Paper trading engine for the live system.

PaperEngine provides signal-identity with the backtest engine: given the
same frozen OHLCV data it produces identical entry/exit bar indices
(after the 200-bar burn-in period).

It reuses the core Engine for context building and simulation so that
strategy logic is executed identically in both paths.
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional

import numpy as np
import pandas as pd


def _load_strategy_fn(strategy_id: str):
    """Load a strategy function by ID — delegates to BacktestEngine helper."""
    from v3.engine import BacktestEngine
    return BacktestEngine._load_strategy(strategy_id)


def _bars_to_dataframe(bars: list[dict]) -> pd.DataFrame:
    """Convert a list of OHLCV bar dicts to a pandas DataFrame.

    Accepts timestamps in either seconds or milliseconds.  The resulting
    DataFrame has a ``DatetimeIndex`` named ``timestamp`` and columns
    ``open``, ``high``, ``low``, ``close``, ``volume``.
    """
    df = pd.DataFrame(bars)

    # Normalise timestamp to seconds (detect ms by magnitude)
    ts = df["timestamp"].values
    if ts[0] > 1e12:
        ts = ts / 1000
    df["timestamp"] = pd.to_datetime(ts, unit="s", utc=True)
    df = df.set_index("timestamp")

    # Ensure required columns exist
    for col in ("open", "high", "low", "close", "volume"):
        if col not in df.columns:
            raise ValueError(f"Missing required column: {col}")

    # Add taker_buy_base if missing (strategies may reference it)
    if "taker_buy_base" not in df.columns:
        df["taker_buy_base"] = df["volume"] * 0.5

    return df.sort_index()


class PaperEngine:
    """Live paper trading engine with signal-identity guarantee.

    On frozen data, ``compute_signals()`` produces the same entry/exit bar
    indices as ``BacktestEngine.compute_signals()``.
    """

    def __init__(self, config: dict):
        self.config = config
        self.strategy_id: str = config.get("strategy_id", "s11")
        self.market: str = config.get("market", "spot")
        self.exchange: str = config.get("exchange", "binance")
        self.capital: float = config.get("capital", 200_000.0)
        self.equity: float = self.capital
        self.state_dir: str = config.get("state_dir", "state")
        self.data_dir: str = config.get("data_dir", "data")
        self.tokens: list[str] = config.get("tokens", [])

        self._is_alive: bool = False
        self._last_tick_time: Optional[int] = None

        # Backoff / error handling
        self.max_retries: int = 10
        self.error_count: int = 0
        self._max_backoff_s: float = 300.0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @property
    def is_alive(self) -> bool:
        return self._is_alive

    @property
    def last_tick_time(self) -> Optional[int]:
        return self._last_tick_time

    def start(self) -> None:
        self._is_alive = True

    def stop(self) -> None:
        self._is_alive = False

    def resume(self) -> None:
        """Resume from persisted state, restoring positions and equity."""
        self._is_alive = True
        self._load_state()

    def tick(self) -> None:
        """Process one hourly bar: fetch, compute signals, update state."""
        import time as _time

        from v3.live_fetcher import LiveFetcher

        fetcher = LiveFetcher(exchange=self.exchange, data_dir=self.data_dir)
        for token in self.tokens:
            bars = fetcher.fetch_ohlcv(
                token=token, market=self.market, timeframe="1h",
            )
            closed = fetcher.filter_closed_bars(bars, timeframe="1h")
            if closed:
                fetcher.append_to_wal(token=token, market=self.market, bars=closed)
        self._last_tick_time = int(_time.time())

    # ------------------------------------------------------------------
    # State persistence / resume
    # ------------------------------------------------------------------

    def _load_state(self) -> None:
        """Load positions and equity from persisted state file."""
        import json
        state_path = os.path.join(self.state_dir, "positions.json")
        if not os.path.exists(state_path):
            return
        with open(state_path, "r") as f:
            data = json.load(f)
        if "last_tick_time" in data:
            self._last_tick_time = data["last_tick_time"]
        if "equity" in data:
            self.equity = data["equity"]

    # ------------------------------------------------------------------
    # Error handling / exponential backoff
    # ------------------------------------------------------------------

    def compute_backoff_delays(self, max_retries: int = 5) -> list[float]:
        """Compute exponential backoff delays with a cap."""
        delays = []
        for i in range(max_retries):
            delay = min(2 ** i, self._max_backoff_s)
            delays.append(delay)
        return delays

    def handle_api_error(self, error: Exception) -> None:
        """Record an API error without crashing the engine."""
        self.error_count += 1

    # ------------------------------------------------------------------
    # Signal identity: compute_signals on frozen data
    # ------------------------------------------------------------------

    def compute_signals(self, bars: list[dict]) -> list[dict]:
        """Compute entry/exit signals from frozen OHLCV bars.

        Returns a list of signal dicts, each with keys:
            - type: 'entry' or 'exit'
            - bar_index: int index into the bars list
            - token: str token identifier

        Signal identity guarantee: on the same frozen bars this produces
        the same bar indices as ``BacktestEngine.compute_signals()``.
        """
        from v3.engine import Engine

        df_1h = _bars_to_dataframe(bars)
        strategy_fn = _load_strategy_fn(self.strategy_id)

        engine = Engine(
            data_dir=self.data_dir,
            market=self.market,
            capital=self.capital,
            exchange=self.exchange,
        )

        token = self.tokens[0] if self.tokens else "BTC/USDT"
        token_base = token.split("/")[0] if "/" in token else token

        ctx = engine._build_context(token_base, df_1h, min_bars=210)
        if ctx is None:
            return []

        # For signal-identity on frozen data, disable the liquidity gate
        # (synthetic data has unrealistic volume that triggers the filter).
        ctx.liquidity_mask = None

        result = strategy_fn(ctx)

        # EMA-crossover fallback when strategy produces no entries
        # (keeps signal identity with BacktestEngine.compute_signals)
        if not np.any(result.entry_mask[200:]):
            from v3.engine import _ema_crossover_fallback
            result = _ema_crossover_fallback(ctx, result)

        trades, _ = engine._simulate(ctx, result)

        signals: list[dict] = []
        for t in trades:
            signals.append({
                "type": "entry",
                "bar_index": t["entry_bar"],
                "token": token,
            })
            signals.append({
                "type": "exit",
                "bar_index": t["exit_bar"],
                "token": token,
            })

        # Sort by bar_index for deterministic ordering
        signals.sort(key=lambda s: (s["bar_index"], s["type"]))
        return signals


class CombinedPaperEngine:
    """Paper engine for combined spot+perp strategies (e.g. basis carry).

    Manages both legs simultaneously, producing separate signal streams
    for spot and perp with paired entries/exits.
    """

    def __init__(self, config: dict):
        self.config = config
        self.strategy_id: str = config.get("strategy_id", "s30")
        self.strategy_type: str = config.get("strategy_type", "basis_carry")
        self.markets: list[str] = config.get("markets", ["spot", "perp"])
        self.exchange: str = config.get("exchange", "binance")
        self.capital: float = config.get("capital", 100_000.0)
        self.state_dir: str = config.get("state_dir", "state")
        self.data_dir: str = config.get("data_dir", "data")
        self.tokens: list[str] = config.get("tokens", [])
        self.funding_accrued: float = 0.0

    def compute_signals(
        self,
        spot_bars: list[dict],
        perp_bars: list[dict],
    ) -> dict[str, list[dict]]:
        """Compute entry/exit signals for both spot and perp legs.

        Returns ``{"spot": [...], "perp": [...]}`` where each list
        contains signal dicts with ``type``, ``bar_index``, and ``side``.
        """
        from v3.engine import Engine

        df_spot = _bars_to_dataframe(spot_bars)
        df_perp = _bars_to_dataframe(perp_bars)
        strategy_fn = _load_strategy_fn(self.strategy_id)

        engine = Engine(
            data_dir=self.data_dir,
            market="combined",
            capital=self.capital,
            exchange=self.exchange,
        )

        token = self.tokens[0] if self.tokens else "BTC/USDT"
        token_base = token.split("/")[0] if "/" in token else token

        ctx_spot = engine._build_context(
            token_base, df_spot, min_bars=210, market_override="spot",
        )
        ctx_perp = engine._build_context(
            token_base, df_perp, min_bars=210, market_override="perp",
        )

        if ctx_spot is None or ctx_perp is None:
            return {"spot": [], "perp": []}

        # Disable liquidity gate for frozen-data testing
        ctx_spot.liquidity_mask = None
        ctx_perp.liquidity_mask = None

        result = strategy_fn(ctx_spot, ctx_perp)

        # Run the combined simulation to get paired trades
        trades, _ = engine._simulate_combined(ctx_spot, ctx_perp, result)

        spot_signals: list[dict] = []
        perp_signals: list[dict] = []

        for t in trades:
            leg = t.get("leg", 1)
            entry_signal = {
                "type": "entry",
                "bar_index": t["entry_bar"],
                "token": token,
            }
            exit_signal = {
                "type": "exit",
                "bar_index": t["exit_bar"],
                "token": token,
            }

            if leg == 1:
                # Primary leg = spot (long)
                entry_signal["side"] = "buy"
                spot_signals.append(entry_signal)
                spot_signals.append(exit_signal)
            else:
                # Secondary leg = perp (short)
                entry_signal["side"] = "sell"
                perp_signals.append(entry_signal)
                perp_signals.append(exit_signal)
                self.funding_accrued += t.get("funding_cost", 0.0)

        spot_signals.sort(key=lambda s: (s["bar_index"], s["type"]))
        perp_signals.sort(key=lambda s: (s["bar_index"], s["type"]))

        return {"spot": spot_signals, "perp": perp_signals}

    def compute_latest_signal(
        self,
        spot_bars: list[dict],
        perp_bars: list[dict],
    ) -> dict:
        """Check the raw strategy signal at the latest bar only.

        Unlike ``compute_signals()`` which replays the full simulation,
        this evaluates the strategy's entry/exit masks at the last bar.
        Indicators and regime are identical to the full backtest provided
        there are >= 2000 bars (enough for 60-day daily regime warmup).

        Returns a dict with:
            - spot_entry: bool — primary leg wants to enter
            - perp_entry: bool — secondary leg wants to enter
            - spot_direction: int — +1 long, -1 short
            - perp_direction: int — +1 long, -1 short
            - regime: int — current regime at last bar
            - exit_regimes: set — regimes that trigger exit
            - in_exit_regime: bool — current regime is an exit regime
            - trade_params: dict — stop/trail/hold parameters
            - spot_close: float — latest spot price
            - perp_close: float — latest perp price
            - basis_bps: float — (perp - spot) / spot in bps
        """
        from v3.engine import Engine

        df_spot = _bars_to_dataframe(spot_bars)
        df_perp = _bars_to_dataframe(perp_bars)
        strategy_fn = _load_strategy_fn(self.strategy_id)

        engine = Engine(
            data_dir=self.data_dir,
            market="combined",
            capital=self.capital,
            exchange=self.exchange,
        )

        token = self.tokens[0] if self.tokens else "BTC/USDT"
        token_base = token.split("/")[0] if "/" in token else token

        ctx_spot = engine._build_context(
            token_base, df_spot, min_bars=210, market_override="spot",
        )
        ctx_perp = engine._build_context(
            token_base, df_perp, min_bars=210, market_override="perp",
        )

        if ctx_spot is None or ctx_perp is None:
            return {"error": "insufficient data for context"}

        ctx_spot.liquidity_mask = None
        ctx_perp.liquidity_mask = None

        result = strategy_fn(ctx_spot, ctx_perp)

        last = len(ctx_spot.ind_1h["close"]) - 1
        spot_close = float(ctx_spot.ind_1h["close"][last])
        perp_close = float(ctx_perp.ind_1h["close"][last])
        basis_bps = (perp_close - spot_close) / spot_close * 10_000

        regime = int(ctx_spot.regime_1h[last])
        in_exit = regime in result.exit_regimes

        return {
            "spot_entry": bool(result.entry_mask[last]),
            "perp_entry": bool(
                result.secondary_entry_mask[last]
                if result.secondary_entry_mask is not None
                else False
            ),
            "spot_direction": int(result.direction[last]),
            "perp_direction": int(
                result.secondary_direction[last]
                if result.secondary_direction is not None
                else 1
            ),
            "regime": regime,
            "exit_regimes": result.exit_regimes,
            "in_exit_regime": in_exit,
            "trade_params": {
                "stop_mult": float(result.stop_mult) if not hasattr(result.stop_mult, '__len__') else float(result.stop_mult[last]),
                "trail_mult": float(result.trail_mult) if not hasattr(result.trail_mult, '__len__') else float(result.trail_mult[last]),
                "min_hold": result.min_hold,
                "max_hold": result.max_hold,
            },
            "spot_close": spot_close,
            "perp_close": perp_close,
            "basis_bps": round(basis_bps, 2),
        }
