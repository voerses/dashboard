"""V5 — StrategySpec scaffold (M4).

Minimal forward-reference scaffold introduced by Task 4 so that the AC35
memory-budget tests can construct StrategySpec(strategy_id=..., bar_subscriptions={...}).
Full StrategySpec (bar_subscriptions validation, chandelier ATR source rules,
scale_check_fn wiring, etc.) is delivered by later M4 tasks.

This scaffold accepts arbitrary kwargs so subsequent tasks can extend it
without breaking callers.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any


logger = logging.getLogger(__name__)


@dataclass
class StrategySpec:
    """Minimal StrategySpec — scaffold only.

    Required fields for AC35 memory-budget projection:
      - ``strategy_id``: per-strategy key used in log + telemetry.
      - ``bar_subscriptions``: dict mapping role ("signal"/"entry"/"exit") to
        a :class:`v5.bar_spec.BarSpec`. Consumed by
        :func:`v5.rolling_cache.compute_projected_memory_mb` to size caches.

    AC40 fields (T13a) — chandelier ATR source cascade:
      - ``chandelier_atr_source``: ``"fine"`` or ``"hourly"``. Declares which
        ATR series the chandelier trail should read off ``bar_ctx``. The
        handler has NO default — the strategy must declare explicitly.
      - ``declared_fine_atr``: ``True`` when the strategy subscribes to a
        fine-bar ATR indicator. Validation at construction time rejects the
        ``("fine", declared_fine_atr=False)`` combination with a ``ValueError``.
      - ``chandelier_lookback``: rolling window length used by the
        :class:`v5.exit_handlers.ChandelierStopHandler`.

    Additional keyword arguments are accepted and stored on the instance
    (see ``__post_init__``) so that later M4 tasks can extend the contract
    without modifying this file's signature.
    """

    strategy_id: str
    bar_subscriptions: dict = field(default_factory=dict)
    # T28 / AC30 — warm-up gate: number of signal_resolution bars that must
    # be observed before this strategy's ``on_signal`` callback is allowed
    # to fire, and before any PendingEntry it owns can transition
    # ARMED -> TRIGGERED. Stage 1 (exits) is NOT gated by warmup.
    warmup_bars: int = 0

    def __init__(
        self,
        strategy_id: str,
        bar_subscriptions: dict | None = None,
        *,
        warmup_bars: int = 0,
        **extra: Any,
    ):
        self.strategy_id = strategy_id
        self.bar_subscriptions = dict(bar_subscriptions or {})
        self.warmup_bars = int(warmup_bars)

        # T14 / AC29 — role constraint: signal.period_ns >= entry.period_ns.
        # A strategy that requests a finer signal resolution than its entry
        # resolution inverts the Stage-2/Stage-3 coarsening contract. Raise
        # ValueError at construction time so the misconfiguration surfaces
        # before the backtest loop starts.
        sig_spec = self.bar_subscriptions.get("signal")
        entry_spec = self.bar_subscriptions.get("entry")
        if sig_spec is not None and entry_spec is not None:
            sig_period = getattr(sig_spec, "period_ns", None)
            entry_period = getattr(entry_spec, "period_ns", None)
            if sig_period is not None and entry_period is not None:
                if sig_period < entry_period:
                    raise ValueError(
                        f"StrategySpec {strategy_id!r}: signal.period_ns "
                        f"({sig_period}) must be >= entry.period_ns "
                        f"({entry_period}). A strategy cannot request a "
                        f"finer signal resolution than its entry resolution."
                    )
                # AC29 warning — ratio > 1000 suggests a likely misconfig.
                if entry_period > 0 and sig_period / entry_period > 1000:
                    logger.warning(
                        "StrategySpec %r: signal/entry period ratio %.1f "
                        "exceeds 1000 — likely misconfiguration.",
                        strategy_id,
                        sig_period / entry_period,
                    )

        # AC40 T13a: chandelier ATR source cascade validation.
        # Pop known AC40 fields so we can apply default + validation rules
        # before storing forward-compat kwargs.
        chandelier_atr_source = extra.pop("chandelier_atr_source", None)
        declared_fine_atr = extra.pop("declared_fine_atr", False)
        chandelier_lookback = extra.pop("chandelier_lookback", 0)

        if chandelier_atr_source is not None:
            if chandelier_atr_source not in ("fine", "hourly"):
                raise ValueError(
                    f"chandelier_atr_source must be 'fine' or 'hourly'; "
                    f"got {chandelier_atr_source!r}"
                )
            if chandelier_atr_source == "fine" and not declared_fine_atr:
                raise ValueError(
                    "chandelier_atr_source='fine' requires declared_fine_atr=True "
                    "(no fine-bar ATR subscription was declared). "
                    "Either declare a fine-bar ATR indicator or use "
                    "chandelier_atr_source='hourly'."
                )

        self.chandelier_atr_source = chandelier_atr_source
        self.declared_fine_atr = bool(declared_fine_atr)
        self.chandelier_lookback = int(chandelier_lookback)

        # Preserve forward-compat kwargs so later tasks can layer on behavior.
        for k, v in extra.items():
            setattr(self, k, v)
