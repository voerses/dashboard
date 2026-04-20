"""M8 AC-Sz1 + AC-Sz2 — SizingIntent enum + SizingRequest schema.

Replaces M7's `Literal[...]` intent stub with a proper `(str, Enum)` to
match M5/M7 FIX vocabulary convention (ExecType, OrderStatus,
ContingencyType, TriggerType, LegStatus, LegFillPolicy are all
`(str, Enum)` for wire alignment + type hygiene). `.value` preserves
the M7 string form so paper-state JSON roundtrip stays byte-identical.

Scalar-only invariant (§3.1): SizingRequest fields are pure scalars;
strategies pre-index per-bar arrays at `generate()` time and bake
scalars into the request. Engine never reads bar-arrays.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal, Optional


class SizingIntent(str, Enum):
    """M8 AC-Sz1 — two engine-supported sizing intents.

    Matches M5/M7 `(str, Enum)` convention. `RISK_PER_TRADE` is NOT an
    engine intent — risk-budget sizing is a strategy-side transformation
    via `v5.sizing.helpers.risk_budget_fraction` that composes into a
    `SizingRequest(FIXED_FRACTION, fraction=...)`.
    """

    FIXED_FRACTION = "FIXED_FRACTION"
    FIXED_NOTIONAL = "FIXED_NOTIONAL"
    # M9: alias for FIXED_FRACTION — quant-literature name "fraction of equity"
    # preserved for strategy authors who prefer it. Same enum member value.
    FRACTION_OF_EQUITY = "FIXED_FRACTION"


_VALID_MARGIN_MODES = frozenset({"isolated", "cross"})


def _reject_array_scalar(name: str, value):
    """AC-Sz2 scalar-only invariant: raises TypeError when a numpy array
    or sequence is passed where a scalar is expected.

    Catches the class of bug where a strategy forgets to pre-index a
    per-bar array at `generate()` time and accidentally bakes a full
    bar-length array into the SizingRequest. The engine then sees a
    non-scalar and downstream clamp math returns nonsense. Fail-fast
    at SizingRequest construction.
    """
    if value is None:
        return
    # Reject numpy arrays (most common bug path for strategies)
    if hasattr(value, "shape") and hasattr(value, "dtype"):
        raise TypeError(
            f"SizingRequest.{name} must be a scalar float, got "
            f"{type(value).__name__}(shape={getattr(value, 'shape', '?')}). "
            f"Strategies must pre-index per-bar arrays at generate() time."
        )
    # Reject list/tuple (same class of bug, different source)
    if isinstance(value, (list, tuple)):
        raise TypeError(
            f"SizingRequest.{name} must be a scalar float, got {type(value).__name__}. "
            f"Strategies must pre-index per-bar sequences at generate() time."
        )


@dataclass
class SizingRequest:
    """M8 AC-Sz2 — scalar per-fill sizing intent baked into TokenSignal.

    Invariants enforced at construction:
      - `intent` must be a `SizingIntent` member (str values accepted and
        coerced; invalid strings raise ValueError)
      - `fraction_of_equity` XOR `notional_usd` — mutually exclusive
      - Required field matches intent: FIXED_FRACTION needs
        `fraction_of_equity`; FIXED_NOTIONAL needs `notional_usd`
      - `margin_mode` in {"isolated", "cross"}
      - Fields are scalars — numpy arrays / lists / tuples rejected
    """

    intent: SizingIntent
    fraction_of_equity: Optional[float] = None
    notional_usd: Optional[float] = None
    leverage: float = 1.0
    reduce_only: bool = False
    margin_mode: Literal["isolated", "cross"] = "isolated"
    # AC-Sz9 escape hatch for aggregate `fraction × leverage > 1.0` gate
    # (pre-clamp portfolio bound in Task 32). Default False → reject the
    # marginal signal; strategies explicitly opt in when they want to
    # exceed 1.0x aggregate leverage across concurrent positions.
    allow_over_leveraged: bool = False

    def __post_init__(self):
        # Coerce string intent to enum; raise if invalid.
        if not isinstance(self.intent, SizingIntent):
            if isinstance(self.intent, str):
                try:
                    self.intent = SizingIntent(self.intent)
                except ValueError:
                    valid = [m.value for m in SizingIntent]
                    raise ValueError(
                        f"SizingRequest.intent must be a SizingIntent member "
                        f"(one of {valid}); got {self.intent!r}"
                    )
            else:
                raise ValueError(
                    f"SizingRequest.intent must be a SizingIntent member or "
                    f"string; got {type(self.intent).__name__}"
                )

        # Scalar-only invariant — reject arrays/sequences up front.
        _reject_array_scalar("fraction_of_equity", self.fraction_of_equity)
        _reject_array_scalar("notional_usd", self.notional_usd)
        _reject_array_scalar("leverage", self.leverage)

        # Mutex: exactly one of fraction_of_equity / notional_usd must be set.
        has_fraction = self.fraction_of_equity is not None
        has_notional = self.notional_usd is not None
        if has_fraction and has_notional:
            raise ValueError(
                "SizingRequest: fraction_of_equity and notional_usd are "
                "mutually exclusive — set exactly one."
            )
        if not (has_fraction or has_notional):
            raise ValueError(
                "SizingRequest: must set either fraction_of_equity or "
                "notional_usd (neither provided)."
            )

        # Intent ↔ field pairing: FIXED_FRACTION requires fraction_of_equity,
        # FIXED_NOTIONAL requires notional_usd. Catches the easy typo where
        # a strategy sets intent=FIXED_FRACTION but passes notional_usd.
        if self.intent == SizingIntent.FIXED_FRACTION and not has_fraction:
            raise ValueError(
                "SizingRequest: intent=FIXED_FRACTION requires "
                "fraction_of_equity to be set."
            )
        if self.intent == SizingIntent.FIXED_NOTIONAL and not has_notional:
            raise ValueError(
                "SizingRequest: intent=FIXED_NOTIONAL requires "
                "notional_usd to be set."
            )

        # margin_mode validation (Literal annotation is not enforced at runtime).
        if self.margin_mode not in _VALID_MARGIN_MODES:
            raise ValueError(
                f"SizingRequest.margin_mode must be one of "
                f"{sorted(_VALID_MARGIN_MODES)}; got {self.margin_mode!r}"
            )
