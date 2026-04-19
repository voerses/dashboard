"""s523c RSI-Timed Composite Positioning — v5 port (reference migration).

Ported from `strategies/s523c_growth.py` to the M7 unified Strategy Protocol.

v4→v5 changes applied (per M7 design §4 "conviction→priority split"):
  - v4 `conviction_score` array (sizing input) → `TokenSignal.priority`
    (ranking only) + `TokenSignal.sizing.fraction_of_equity` (capital
    allocation, derived from conviction via explicit formula).
  - Module-level state purged:
      _composite_cache, _daily_loaded, _last_load_date,
      _aligned_cache, _paper_mode, _token_configs
    → all live on `self`. AST scan in v5/strategy_loader.py now passes.
  - `_check_new_day()` (used datetime.now()) removed — v5's DataEngine
    layer (M6) drives data refresh; strategies receive bars post-ingest.

Reference port only — AC-S10 parity is not required for s523c.
"""
from __future__ import annotations

import json
import os
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from v5.strategy_api import (
    BaseStrategy,
    ExitCheck,
    SizingRequest,
    TokenSignal,
    UniverseSignals,
)


CRISIS = 0  # engine regime constant


# Token blacklist — 50 value-destroying tokens from L12M optimization sweep.
# Class-level frozenset is a constant (not mutable state), so AST scan passes.
_TOKEN_BLACKLIST: frozenset = frozenset({
    "EIGEN", "BAN", "CETUS", "ONT", "BANANA", "DOT",
    "STRK", "SOL", "PIPPIN", "DEGO", "SUI", "NEIRO",
    "ZEN", "MINA", "STEEM", "ANKR", "AVAX", "BCH",
    "BTC", "CRV", "DASH", "DUSK", "ENA", "ETC",
    "FET", "FIL", "G", "GRASS", "INJ", "JUP",
    "KAS", "KAVA", "NEO", "OGN", "POLYX", "RENDER",
    "RVN", "SAND", "SIREN", "TAO", "UNI", "WLD",
    "LTC", "LINK", "AAVE", "XRP", "ADA", "TRX",
    "DOGE", "SHIB",
})


class S523CGrowth(BaseStrategy):
    """RSI-Timed Composite Positioning (v4 s523c → v5 Protocol)."""

    name = "s523c_growth"

    # ── Parameters (class attributes — constants) ──
    ZSCORE_WINDOW_DAYS = 30
    THRESHOLD = 1.0
    DIRECTION = "both"
    RSI_PERIOD = 14
    RSI_LONG_LEVEL = 40
    RSI_SHORT_LEVEL = 60
    RSI_WINDOW_1H = 72
    RSI_RESAMPLE = 4
    LEVERAGE = 2.6
    STOP_MULT = 5.0
    TRAIL_MULT = 999.0
    MIN_HOLD = 48
    NO_STOP_BARS = 72
    BREAKEVEN_ATR = 1.0
    FUNDING_BOOST = 0.10
    WARMUP = 400
    CONVICTION_NORM = 3.0  # divides |composite| to normalize priority to [0, 1]
    MAX_POSITIONS_HINT = 50

    def __init__(self, tokens: Optional[List[str]] = None,
                 token_config_path: Optional[str] = None):
        self.tokens: List[str] = list(tokens) if tokens else []
        self._config_path = token_config_path or self._default_config_path()
        self._token_configs: Dict[str, dict] = {}
        # Per-symbol daily composite series (shift-by-1 applied, indexed by UTC date).
        # Populated on first generate() call per symbol; cleared on on_reset().
        self._composite_cache: Dict[str, pd.Series] = {}
        # Per-(symbol, index_signature) 1H-aligned composite arrays.
        self._aligned_cache: Dict[tuple, np.ndarray] = {}

    # ── Helpers ───────────────────────────────────────────────────────
    @staticmethod
    def _default_config_path() -> str:
        return os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "data", "alternative", "s521_token_config.json",
        )

    def _ensure_configs_loaded(self) -> None:
        """Load per-token IC weights/signs exactly once."""
        if self._token_configs:
            return
        if os.path.exists(self._config_path):
            with open(self._config_path) as f:
                self._token_configs = json.load(f)

    @staticmethod
    def _compute_rsi(close: np.ndarray, period: int = 14) -> np.ndarray:
        delta = np.diff(close, prepend=close[0])
        gain = np.where(delta > 0, delta, 0.0)
        loss = np.where(delta < 0, -delta, 0.0)
        avg_gain = pd.Series(gain).ewm(span=period, adjust=False).mean().values
        avg_loss = pd.Series(loss).ewm(span=period, adjust=False).mean().values
        rs = avg_gain / (avg_loss + 1e-10)
        return 100.0 - (100.0 / (1.0 + rs))

    @staticmethod
    def _daily_zscore(arr: np.ndarray, window: int) -> np.ndarray:
        s = pd.Series(arr, dtype=np.float64)
        mu = s.rolling(window, min_periods=window // 2).mean()
        sd = s.rolling(window, min_periods=window // 2).std(ddof=1)
        z = (s - mu) / sd.replace(0, np.nan)
        return z.values

    # ── Protocol methods ──────────────────────────────────────────────
    def on_start(self, portfolio_config) -> None:
        self._ensure_configs_loaded()
        self._composite_cache.clear()
        self._aligned_cache.clear()

    def on_reset(self) -> None:
        self._composite_cache.clear()
        self._aligned_cache.clear()

    def required_data(self) -> list:
        # Reference port — real subscriptions built by the engine bootstrap
        # when it binds this strategy into a live universe.
        return []

    def generate(self, ctx, bar_idx: int) -> UniverseSignals:
        signals: Dict[str, TokenSignal] = {}
        if bar_idx < self.WARMUP:
            return UniverseSignals(bar_idx=bar_idx, signals=signals)

        self._ensure_configs_loaded()
        token_iter = getattr(ctx, "tokens", None) or self.tokens
        for token in token_iter:
            if token in _TOKEN_BLACKLIST or token not in self._token_configs:
                continue
            sig = self._evaluate_token(ctx, bar_idx, token)
            if sig is not None:
                signals[token] = sig
        return UniverseSignals(bar_idx=bar_idx, signals=signals)

    def _evaluate_token(self, ctx, bar_idx: int, token: str) -> Optional[TokenSignal]:
        """Per-bar RSI-timed composite evaluation — returns None when no entry."""
        try:
            ind = ctx.data.indicators(token, "1h")
        except Exception:
            return None

        composite = ind.get("composite_zscore")
        rsi_long_recent = ind.get("rsi4h_crossup40_within_72h")
        rsi_short_recent = ind.get("rsi4h_crossdown60_within_72h")
        day_change = ind.get("day_boundary")
        funding_1h = ind.get("funding_rate_1h", 0.0)

        # Indicators supplied by M7 DataEngine indicator cache. Missing values
        # mean "not yet computed" — treat as no-entry rather than crashing.
        if composite is None or day_change is None:
            return None
        if not bool(day_change):
            return None

        composite_v = float(composite)
        if np.isnan(composite_v):
            return None

        # Funding-aware conviction adjustment (v4 parity)
        funding_v = float(funding_1h) if funding_1h is not None else 0.0
        if abs(funding_v) > 1e-8:
            composite_sign = np.sign(composite_v)
            funding_alignment = -composite_sign * np.sign(funding_v)
            if funding_alignment > 0:
                composite_v *= 1.0 + self.FUNDING_BOOST
            elif funding_alignment < 0:
                composite_v *= 1.0 - self.FUNDING_BOOST

        long_entry = composite_v > self.THRESHOLD and (
            rsi_long_recent is None or bool(rsi_long_recent)
        )
        short_entry = composite_v < -self.THRESHOLD and (
            rsi_short_recent is None or bool(rsi_short_recent)
        )

        if self.DIRECTION == "long" and not long_entry:
            return None
        if self.DIRECTION == "short" and not short_entry:
            return None
        if self.DIRECTION == "both" and not (long_entry or short_entry):
            return None

        direction = 1 if long_entry else -1
        cfg = self._token_configs.get(token, {})
        token_max_hold = int(cfg.get("max_hold_hours", 720))
        token_no_stop = max(self.NO_STOP_BARS, token_max_hold // 2)

        # Conviction→priority split (design §4):
        #   priority = ranked score in [0, 1] (NOT sizing)
        #   fraction_of_equity = portfolio caps; scaled by priority on engine side
        abs_composite = abs(composite_v)
        priority = min(1.0, abs_composite / self.CONVICTION_NORM)

        return TokenSignal(
            token=token,
            direction=direction,
            priority=priority,
            sizing=SizingRequest(
                intent="FIXED_FRACTION",
                fraction_of_equity=1.0 / self.MAX_POSITIONS_HINT,
                leverage=self.LEVERAGE,
            ),
            stop_mult=self.STOP_MULT,
            trail_mult=self.TRAIL_MULT,
            target_mult=999.0,
            min_hold=self.MIN_HOLD,
            max_hold=token_max_hold,
        )

    def check_exit(self, pos, bar_ctx):
        """Strategy-level CRISIS exit — mirrors v4 _crisis_exit."""
        bars_held = getattr(bar_ctx, "bars_held", 0) if bar_ctx is not None else 0
        regime = getattr(bar_ctx, "regime", None) if bar_ctx is not None else None
        if bars_held > 6 and regime == CRISIS:
            return ExitCheck(reason="crisis")
        return None

    def view_state(self) -> dict:
        return {
            "tokens": list(self.tokens),
            "configs_loaded": len(self._token_configs),
            "composite_cache_size": len(self._composite_cache),
        }
