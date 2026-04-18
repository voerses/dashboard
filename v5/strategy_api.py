"""V5 Portfolio Backtest — Strategy-facing API (ScaleAction, LinkedScalePolicy).

Re-exports :class:`v5.strategy_spec.StrategySpec` so downstream callers can
import the strategy-facing surface from a single module.
"""

from dataclasses import dataclass
from enum import Enum

from v5.strategy_spec import StrategySpec  # noqa: F401 — re-export


@dataclass
class ScaleAction:
    """Scale action returned by strategy scale callbacks.

    Attributes:
        qty_delta: Signed quantity delta. Positive = increase position,
            negative = reduce position.
        reason: Free-form diagnostic string (e.g. "tp_rung_1", "stop_trail").
        stop_override: Optional explicit stop price to set on an increase
            (AC2). ``None`` means no stop change.
        is_stop_like: When ``True`` on a reduce action, dispatch applies a
            stressed ADV participation cap (AC28b).
    """

    qty_delta: float
    reason: str
    stop_override: float | None = None
    is_stop_like: bool = False


class LinkedScalePolicy(Enum):
    """Policy for propagating scale actions from a primary leg to its linked
    secondary leg in a linked (pair) position (AC36)."""

    INDEPENDENT = "independent"
    PROPORTIONAL = "proportional"
    ABSOLUTE = "absolute"
