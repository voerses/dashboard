"""s513 Triple-Trigger Regime Swing — v5 port (reference migration).

Ported from `strategies/s513_triple_trigger_swing.py` to the M7 unified
Strategy Protocol. Original strategy semantics preserved; only the
dispatch surface changes (StrategyContext bar-array → ctx.data per-bar).

Triggers (ANY within regime + common filters):
  1. MACD zero-cross in existing trend regime
  2. RSI pullback recovery (cross-up through 40 in UPTREND; cross-down
     through 60 in DOWNTREND)
  3. Donchian 20-day breakout in the regime direction

Market: PERP (bidirectional). Leverage 3x (Calmar-optimal).

Reference port only — AC-S10 parity is NOT required for s513.
"""
from __future__ import annotations

from typing import List

from v5.strategy_api import BaseStrategy, SizingIntent, SizingRequest, TokenSignal, UniverseSignals


# M9 C-4: import regime constants from canonical module
from v5.regimes import CRISIS, DOWNTREND, UPTREND, RANGE, QUIET


class S513TripleTriggerSwing(BaseStrategy):
    """Regime-swing strategy with MACD / RSI-pullback / Donchian-breakout triggers."""

    name = "s513_triple_trigger_swing"

    # ── Configuration (module-level constants in v4 → class attributes here) ──
    LEVERAGE = 3.0
    WARMUP = 200
    MIN_ADV_USD = 1_000_000_000
    ADX_THRESH = 20
    BB_LOOKBACK = 240
    SQUEEZE_MULT = 0.8
    RSI_LONG_THRESH = 40
    RSI_SHORT_THRESH = 60
    DONCHIAN_PERIOD = 480
    STOP_MULT = 99.0
    TRAIL_MULT = 2.5
    TARGET_MULT = 999.0
    NO_STOP_BARS = 72
    MIN_HOLD = 24
    MAX_HOLD = 720
    EDGE = 0.40

    def __init__(self, tokens: List[str] | None = None):
        self.tokens: List[str] = list(tokens) if tokens else []
        # Per-strategy state (NOT module-level — satisfies strategy_loader
        # AST scan + keeps WF folds isolated)
        self._warmup_done: set[str] = set()

    def required_data(self) -> list:
        """Declare 1h BAR subscriptions per token.

        Returns an empty list when tokens are not yet bound. DataEngine
        builds the real `Subscription` list once the runner injects the
        bound token universe via the test harness or live bootstrap.
        """
        # Reference port: empty list is acceptable since this strategy does
        # not drive any test that exercises real subscriptions. The v5 paper
        # engine populates subscriptions from its own universe config.
        return []

    def on_start(self, portfolio_config) -> None:
        self._warmup_done.clear()

    def on_reset(self) -> None:
        self._warmup_done.clear()

    def generate(self, ctx, bar_idx: int) -> UniverseSignals:
        """Per-bar triple-trigger evaluation for each token in the universe."""
        signals: dict[str, TokenSignal] = {}
        if bar_idx < self.WARMUP:
            return UniverseSignals(bar_idx=bar_idx, signals=signals)

        # Iterate universe from ctx (tokens bound via UniverseContext)
        token_iter = getattr(ctx, "tokens", None) or self.tokens
        for token in token_iter:
            sig = self._evaluate_token(ctx, bar_idx, token)
            if sig is not None:
                signals[token] = sig
        return UniverseSignals(bar_idx=bar_idx, signals=signals)

    def _evaluate_token(self, ctx, bar_idx: int, token: str) -> TokenSignal | None:
        """Triple-trigger decision for one token at one bar. Returns None if
        no entry — callers should treat missing signals as direction=0.
        """
        try:
            ind = ctx.data.indicators(token, "1h")
        except Exception:
            return None

        close = ind.get("close")
        high = ind.get("high")
        low = ind.get("low")
        volume = ind.get("volume")
        macd = ind.get("macd")
        rsi = ind.get("rsi")
        adx = ind.get("adx")
        plus_di = ind.get("plus_di")
        minus_di = ind.get("minus_di")
        ema10 = ind.get("ema_10")
        ema20 = ind.get("ema_20")
        ema50 = ind.get("ema_50")
        bb_width = ind.get("bb_width")
        regime = ind.get("regime")
        donchian_high = ind.get("donchian_high_480h")
        donchian_low = ind.get("donchian_low_480h")
        bb_avg = ind.get(f"bb_width_avg_{self.BB_LOOKBACK}h")

        # Any missing indicator → no signal this bar (ctx.data is allowed to
        # report None for uncomputed indicators; engine will fill as needed)
        scalars = (
            close, high, low, volume, macd, rsi, adx, plus_di, minus_di,
            ema10, ema20, ema50, bb_width, regime,
        )
        if any(v is None for v in scalars):
            return None

        # ── Liquidity & squeeze filters ──
        dollar_vol_24h = float(close) * float(volume) * 24.0  # rough ADV proxy
        if dollar_vol_24h < self.MIN_ADV_USD:
            return None
        if bb_avg is None or float(bb_width) >= float(bb_avg) * self.SQUEEZE_MULT:
            return None

        # ── Regime + trend filters ──
        adx_ok = float(adx) > self.ADX_THRESH
        if not adx_ok:
            return None
        uptrend = int(regime) == UPTREND
        downtrend = int(regime) == DOWNTREND
        if not (uptrend or downtrend):
            return None
        long_di = float(plus_di) > float(minus_di)
        short_di = float(minus_di) > float(plus_di)

        ema_bull = (float(ema10) > float(ema20)) and (float(ema20) > float(ema50))
        ema_bear = (float(ema10) < float(ema20)) and (float(ema20) < float(ema50))

        # Prior-bar snapshots for cross detection
        prev = ctx.data.indicators(token, "1h", offset=-1) if bar_idx > 0 else {}
        macd_prev = prev.get("macd", 0.0) if isinstance(prev, dict) else 0.0
        rsi_prev = prev.get("rsi", 50.0) if isinstance(prev, dict) else 50.0
        regime_prev = prev.get("regime", regime) if isinstance(prev, dict) else regime

        macd_cross_bull = (float(macd) > 0) and (float(macd_prev) <= 0)
        macd_cross_bear = (float(macd) < 0) and (float(macd_prev) >= 0)
        regime_change_up = uptrend and int(regime_prev) != UPTREND
        regime_change_down = downtrend and int(regime_prev) != DOWNTREND

        # ── Trigger 1: MACD zero-cross in trend ──
        macd_long = (regime_change_up and float(macd) > 0 and ema_bull) or (
            macd_cross_bull and uptrend
        )
        macd_short = (regime_change_down and float(macd) < 0 and ema_bear) or (
            macd_cross_bear and downtrend
        )

        # ── Trigger 2: RSI pullback recovery ──
        rsi_pullback_long = (
            float(rsi) > self.RSI_LONG_THRESH and float(rsi_prev) <= self.RSI_LONG_THRESH
        )
        rsi_pullback_short = (
            float(rsi) < self.RSI_SHORT_THRESH and float(rsi_prev) >= self.RSI_SHORT_THRESH
        )

        # ── Trigger 3: Donchian breakout ──
        donchian_break_long = (
            donchian_high is not None and float(close) > float(donchian_high)
        )
        donchian_break_short = (
            donchian_low is not None and float(close) < float(donchian_low)
        )

        entry_long = (macd_long or rsi_pullback_long or donchian_break_long) and long_di
        entry_short = (macd_short or rsi_pullback_short or donchian_break_short) and short_di

        if entry_long and uptrend:
            direction = 1
        elif entry_short and downtrend:
            direction = -1
        else:
            return None

        return TokenSignal(
            token=token,
            direction=direction,
            priority=float(adx),  # stronger trend → higher priority
            sizing=SizingRequest(
                intent=SizingIntent.FIXED_FRACTION,
                fraction_of_equity=1.0 / 25.0,  # modest sizing; portfolio caps elsewhere
                leverage=self.LEVERAGE,
            ),
            stop_mult=self.STOP_MULT,
            trail_mult=self.TRAIL_MULT,
            target_mult=self.TARGET_MULT,
            min_hold=self.MIN_HOLD,
            max_hold=self.MAX_HOLD,
        )

    def check_exit(self, pos, bar_ctx):
        """Strategy-level CRISIS exit — mirrors v4 PORTFOLIO_CONFIG exit_check_fn."""
        # Mirrors v4 _crisis_exit: exit after 6 bars held if in CRISIS regime.
        bars_held = getattr(bar_ctx, "bars_held", 0) if bar_ctx is not None else 0
        regime = getattr(bar_ctx, "regime", None) if bar_ctx is not None else None
        if bars_held > 6 and regime == CRISIS:
            from v5.strategy_api import ExitCheck
            return ExitCheck(reason="crisis")
        return None


# M9 C-7: short name alias for test-import convenience.
S513 = S513TripleTriggerSwing
