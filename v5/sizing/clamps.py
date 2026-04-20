"""M8 AC-Sz3 — 6-clamp sizing pipeline + dispatch.

Fixed clamp order (matters for binding-log determinism):
  1. ADV cap          — notional ≤ adv_cap_pct × rolling_adv
  2. Concentration    — notional ≤ concentration_limit × equity
  3. Free capital     — notional ≤ policy.available_capital(...)
                          (funding_buffer_pct subtracted BEFORE policy call)
  4. Min size         — reject if notional < min_position_usd
  5. Liquidation dist — reject if notional × leverage pushes liq inside
                          config.min_liquidation_distance_bps (Binance
                          tiered MMR schedule)
  6. Slippage         — SqrtImpact(notional, adv); adjusts fill_price,
                          NEVER a reject.

Multi-leg aggregation (Task 24) respects `ContingencyType` rules from
`compute_reserved_capital` — OTOCO = entry + max(siblings), OCO = max,
OTO/OUO/NONE = sum.

Clamp error containment (AC-Sz7, Task 17): exceptions inside a clamp
raise `ClampError(name)`; caller (`Order.release_atomic`) catches and
sets `state=REJECTED, reject_reason=f"clamp_error_{name}"` + emits
binding-log entry with `error` field populated.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Optional, Tuple

from v5.sizing.intents import SizingIntent
from v5.sizing.slippage import SqrtImpactSlippage


# Default Binance BTCUSDT MMR schedule (tier-1 through tier-5).
# Each entry: (upper_notional_usd, mmr_fraction). Tier lookup: first
# tier where notional <= upper_bound. Matches Binance published
# perpetual-futures leverage/margin schedule.
DEFAULT_MMR_SCHEDULE: List[Tuple[float, float]] = [
    (50_000.0, 0.0050),
    (250_000.0, 0.0065),
    (1_000_000.0, 0.0100),
    (5_000_000.0, 0.0200),
    (20_000_000.0, 0.0500),
]


@dataclass(frozen=True)
class ClampsConfig:
    """Clamp-pipeline configuration. Replaces v4's 9-parameter sizing
    pipeline with 6 explicit clamp parameters.
    """

    adv_cap_pct: float = 0.05
    concentration_limit: float = 0.10
    min_position_usd: float = 200.0
    min_liquidation_distance_bps: float = 100.0
    funding_buffer_pct: float = 0.0
    max_sizing_equity: Optional[float] = None
    mmr_schedule: List[Tuple[float, float]] = field(
        default_factory=lambda: list(DEFAULT_MMR_SCHEDULE)
    )
    # Slippage model params (applied in clamp #6)
    base_spread_bps: float = 3.0
    impact_coeff: float = 0.03
    max_slip_bps: float = 300.0
    # Binding-log JSONL sink — Wave D Task 15 wires the default to
    # `v5/logs/sizing_fills.jsonl`. Tests may override via tmp_path.
    # None = no logging (used for clamp-only unit tests that don't need
    # persistence).
    log_path: Optional[Any] = None


# Fixed clamp dispatch order (AC-Sz3 determinism invariant). Exposed for
# tests that want to assert the order at the module level without
# parsing source.
CLAMP_ORDER: Tuple[str, ...] = (
    "adv_cap",
    "concentration",
    "free_capital",
    "min_size",
    "liquidation_distance",
    "slippage",
)


class ClampError(Exception):
    """Raised by a clamp on an unrecoverable internal error. Caught by
    `Order.release_atomic` → sets `state=REJECTED, reject_reason=
    f"clamp_error_{name}"`. BarProcessor never crashed (AC-Sz7).
    """

    def __init__(self, clamp_name: str, msg: str = ""):
        self.clamp_name = clamp_name
        super().__init__(f"{clamp_name}: {msg}" if msg else clamp_name)


# ─── Helpers ───────────────────────────────────────────────────────────


def _requested_notional(order, market_state) -> float:
    """Resolve `SizingRequest` → notional USD at release time."""
    sizing = order.sizing_ctx.get("sizing") if isinstance(order.sizing_ctx, dict) else None
    if sizing is None:
        # Fallback: legacy path baked `target_size` into sizing_ctx
        target = order.sizing_ctx.get("target_size", 0.0) if isinstance(order.sizing_ctx, dict) else 0.0
        return float(target) * float(market_state.mark_price(order.token))
    # FIXED_NOTIONAL: trivial
    if sizing.intent == SizingIntent.FIXED_NOTIONAL:
        return float(sizing.notional_usd)
    # FIXED_FRACTION: equity × fraction × leverage
    equity = float(market_state.equity(order.strategy_id))
    return float(sizing.fraction_of_equity) * equity * float(sizing.leverage)


def _lookup_mmr(notional: float, schedule: List[Tuple[float, float]]) -> float:
    """Binance-tiered MMR lookup. First tier whose upper bound covers
    `notional`; last tier rate for over-schedule positions."""
    for upper, mmr in schedule:
        if notional <= upper:
            return mmr
    return schedule[-1][1]


# ─── Individual clamps ────────────────────────────────────────────────


def _adv_cap_clamp(notional: float, order, market_state, config: ClampsConfig,
                   ) -> Tuple[float, float]:
    """Clamp #1 — returns (clamped_notional, clamp_ceiling)."""
    try:
        adv = float(market_state.rolling_adv(order.token))
    except Exception as e:
        raise ClampError("adv_cap", str(e)) from e
    ceiling = adv * float(config.adv_cap_pct)
    return min(notional, ceiling), ceiling


def _concentration_clamp(notional: float, order, market_state,
                         config: ClampsConfig) -> Tuple[float, float]:
    """Clamp #2 — per-symbol concentration cap against strategy equity."""
    try:
        equity = float(market_state.equity(order.strategy_id))
    except Exception as e:
        raise ClampError("concentration", str(e)) from e
    ceiling = equity * float(config.concentration_limit)
    return min(notional, ceiling), ceiling


def _free_capital_clamp(notional: float, order, market_state, policy,
                        available_capital_usd: float,
                        config: ClampsConfig) -> Tuple[float, float]:
    """Clamp #3 — policy-driven free capital. Funding buffer subtracted
    BEFORE policy call (design §7.1); policy gets post-buffer value.

    For FIXED_FRACTION orders, `notional` already uses the request's
    leverage so the ceiling is the available margin × leverage. For
    FIXED_NOTIONAL, margin = notional / leverage and margin must fit.
    """
    try:
        sizing = order.sizing_ctx.get("sizing") if isinstance(order.sizing_ctx, dict) else None
        leverage = float(sizing.leverage) if sizing is not None else 1.0
        equity = float(market_state.equity(order.strategy_id))
        # Funding buffer subtracted BEFORE policy call — design §7.1.
        buffer = equity * float(config.funding_buffer_pct)
        post_buffer_available = max(0.0, float(available_capital_usd) - buffer)

        # Build the narrow AllocationState view and invoke the policy via
        # the MarketState adapter (delegation keeps the cross-margin formula
        # centralized in the adapter rather than duplicated in the clamp).
        # MarketState.free_margin is responsible for calling the policy
        # with the AllocationState dict; here we inject post_buffer_available
        # as the seed margin.
        from v5.sizing.allocation import AllocationState

        state = AllocationState(
            available_margin=post_buffer_available,
            per_strategy_equity={order.strategy_id: equity},
            rolling_pnl_24h={},
            current_positions_notional={},
        )
        policy_margin = float(policy.available_capital(
            order.strategy_id, state, 0
        ))
    except Exception as e:
        raise ClampError("free_capital", str(e)) from e
    ceiling = policy_margin * leverage  # margin × leverage = max notional
    return min(notional, ceiling), ceiling


def _min_size_clamp(notional: float, config: ClampsConfig) -> Tuple[float, bool]:
    """Clamp #4 — floor check. Returns (notional, rejected_flag).
    If rejected_flag=True, caller sets state=REJECTED, reason="min_size".
    """
    if notional < float(config.min_position_usd):
        return notional, True
    return notional, False


def _liquidation_distance_clamp(notional: float, order, market_state,
                                config: ClampsConfig) -> Tuple[float, bool]:
    """Clamp #5 — Binance tiered MMR liquidation distance check.

    Returns (liq_distance_bps, rejected_flag).

    Formula: at leverage L the position has initial margin ratio 1/L.
    Liquidation fires when unrealized loss consumes initial margin minus
    maintenance margin: liq_distance = (1/L - mmr) as a fraction. Scale
    to bps. Reject if `liq_distance_bps < config.min_liquidation_distance_bps`.
    """
    try:
        sizing = order.sizing_ctx.get("sizing") if isinstance(order.sizing_ctx, dict) else None
        leverage = float(sizing.leverage) if sizing is not None else 1.0
        if leverage <= 0:
            raise ClampError("liquidation_distance", "leverage must be > 0")
        mmr = _lookup_mmr(notional, config.mmr_schedule)
        # Initial-margin fraction = 1/leverage; liq at (1/L - mmr) adverse move.
        liq_frac = (1.0 / leverage) - mmr
        liq_bps = max(0.0, liq_frac * 10_000.0)
    except ClampError:
        raise
    except Exception as e:
        raise ClampError("liquidation_distance", str(e)) from e
    rejected = liq_bps < float(config.min_liquidation_distance_bps)
    return liq_bps, rejected


def _slippage_clamp(notional: float, order, market_state, config: ClampsConfig,
                    ) -> Tuple[float, float]:
    """Clamp #6 — SqrtImpact slippage. Adjusts fill_price; NEVER rejects.

    Returns (fill_price, slippage_bps).
    """
    try:
        mark = float(market_state.mark_price(order.token))
        adv = float(market_state.rolling_adv(order.token))
        slippage = SqrtImpactSlippage()
        slip_bps = float(slippage.compute_slippage(
            notional, adv,
            base_spread_bps=config.base_spread_bps,
            impact_coeff=config.impact_coeff,
            max_slip_bps=config.max_slip_bps,
        ))
    except Exception as e:
        raise ClampError("slippage", str(e)) from e
    # Slippage shifts fill_price adversely. For long orders: fill higher than mark.
    direction = float(order.direction) if hasattr(order, "direction") else 1.0
    fill_price = mark * (1.0 + direction * slip_bps / 10_000.0)
    return fill_price, slip_bps


# ─── Dispatch pipeline ─────────────────────────────────────────────────


def _emit_binding_log(config: ClampsConfig, entry: Dict[str, Any]) -> None:
    """Write a binding-log entry to JSONL if config.log_path is set.

    Wave D Task 15 is co-implemented here (single file; lightweight JSONL
    append semantics don't warrant a separate module). Delegates to
    `v5.sizing.binding_log.write_sizing_fill_entry` if present for forward
    compatibility.
    """
    if config.log_path is None:
        return
    import json
    import os
    path = str(config.log_path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def run_clamp_pipeline(
    order,
    *,
    available_capital_usd: float,
    market_state,
    policy,
    config: ClampsConfig,
) -> Tuple[Any, Dict[str, Any]]:
    """Execute the fixed 6-clamp order and return (new_order, binding_log).

    `binding_log` is a dict matching the AC-Sz5 schema. On reject the
    returned `new_order` has `state=REJECTED` with a `reject_reason`
    naming the failed clamp ("min_size" / "liquidation_distance" /
    "clamp_error_<name>").

    Multi-leg aggregation: the current single-leg path reads
    `order.sizing_ctx["sizing"]` baked by OrderFactoryView. Task 24
    extends for OTOCO/OCO/OTO per `compute_reserved_capital` rules.
    """
    from v5.orders import OrderStatus

    sizing = order.sizing_ctx.get("sizing") if isinstance(order.sizing_ctx, dict) else None
    requested_notional = _requested_notional(order, market_state)

    # Determine requested fraction for log schema (if FIXED_FRACTION)
    requested_fraction = (
        float(sizing.fraction_of_equity)
        if sizing is not None and sizing.intent == SizingIntent.FIXED_FRACTION
        else None
    )
    requested_notional_logged = (
        float(sizing.notional_usd)
        if sizing is not None and sizing.intent == SizingIntent.FIXED_NOTIONAL
        else requested_notional
    )

    current = float(requested_notional)
    clamp_values: Dict[str, float] = {}
    binding: str = "none"

    def _record_binding(name: str, new_value: float):
        """Set binding_constraint on the FIRST clamp that reduces below
        the currently requested value."""
        nonlocal binding
        if binding == "none" and new_value < current - 1e-9:
            binding = name

    def _error_return(clamp_name: str, exc: Exception):
        """AC-Sz7: transition Order to REJECTED + emit error-path log entry."""
        new_order = order._transit(
            OrderStatus.REJECTED, reject_reason=f"clamp_error_{clamp_name}",
        )
        log = _build_log(
            order, sizing, requested_fraction, requested_notional_logged,
            clamp_values, binding=f"clamp_error_{clamp_name}",
            filled_notional=0.0, fill_price=0.0, slippage_bps=0.0,
            error=str(exc),
        )
        _emit_binding_log(config, log)
        return new_order, log

    # Clamp 1 — ADV cap
    try:
        new_current, adv_ceil = _adv_cap_clamp(current, order, market_state, config)
    except Exception as e:
        return _error_return("adv_cap", e)
    clamp_values["adv_cap"] = adv_ceil
    _record_binding("adv_cap", new_current)
    current = new_current

    # Clamp 2 — Concentration
    try:
        new_current, conc_ceil = _concentration_clamp(current, order, market_state, config)
    except Exception as e:
        return _error_return("concentration", e)
    clamp_values["concentration"] = conc_ceil
    _record_binding("concentration", new_current)
    current = new_current

    # Clamp 3 — Free capital (via policy)
    try:
        new_current, fc_ceil = _free_capital_clamp(
            current, order, market_state, policy,
            available_capital_usd=available_capital_usd, config=config,
        )
    except Exception as e:
        return _error_return("free_capital", e)
    clamp_values["free_capital"] = fc_ceil
    _record_binding("free_capital", new_current)
    current = new_current

    # Clamp 4 — Min size (reject, not clamp)
    try:
        _, below_min = _min_size_clamp(current, config)
    except Exception as e:
        return _error_return("min_size", e)
    clamp_values["min_size"] = float(config.min_position_usd)
    if below_min:
        new_order = order._transit(OrderStatus.REJECTED, reject_reason="min_size")
        # Binding-log keeps the FIRST reducer's name (free_capital, adv_cap, …)
        # if any earlier clamp already cut below requested. Pure min-size
        # rejects (no prior reducer) stamp binding="min_size". This matches
        # the design §3d clause: "binding_constraint records the FIRST
        # clamp to reduce below requested (not last)."
        log_binding = binding if binding != "none" else "min_size"
        log = _build_log(
            order, sizing, requested_fraction, requested_notional_logged,
            clamp_values, binding=log_binding, filled_notional=0.0,
            fill_price=0.0, slippage_bps=0.0,
        )
        _emit_binding_log(config, log)
        return new_order, log

    # Clamp 5 — Liquidation distance (reject if tight)
    try:
        liq_bps, liq_rejected = _liquidation_distance_clamp(
            current, order, market_state, config,
        )
    except Exception as e:
        return _error_return("liquidation_distance", e)
    clamp_values["liquidation_distance"] = liq_bps
    if liq_rejected:
        new_order = order._transit(
            OrderStatus.REJECTED, reject_reason="liquidation_distance",
        )
        log = _build_log(
            order, sizing, requested_fraction, requested_notional_logged,
            clamp_values, binding="liquidation_distance", filled_notional=0.0,
            fill_price=0.0, slippage_bps=0.0,
        )
        _emit_binding_log(config, log)
        return new_order, log

    # Clamp 6 — Slippage (fill_price adjust; never rejects)
    try:
        fill_price, slippage_bps = _slippage_clamp(
            current, order, market_state, config,
        )
    except Exception as e:
        return _error_return("slippage", e)
    clamp_values["slippage"] = slippage_bps

    # Build final Order with the clamped margin baked into sizing_ctx.
    new_ctx = dict(order.sizing_ctx) if isinstance(order.sizing_ctx, dict) else {}
    sizing_leverage = float(sizing.leverage) if sizing is not None else 1.0
    filled_margin = current / sizing_leverage if sizing_leverage > 0 else current
    new_ctx["margin_usd"] = filled_margin
    new_ctx["target_size"] = current / float(market_state.mark_price(order.token)) \
        if market_state.mark_price(order.token) > 0 else 0.0
    new_order = replace(order, sizing_ctx=new_ctx)

    log = _build_log(
        order, sizing, requested_fraction, requested_notional_logged,
        clamp_values, binding=binding, filled_notional=current,
        fill_price=fill_price, slippage_bps=slippage_bps,
        filled_margin=filled_margin,
    )
    _emit_binding_log(config, log)
    return new_order, log


def _build_log(
    order, sizing, requested_fraction, requested_notional,
    clamp_values: Dict[str, float], *,
    binding: str,
    filled_notional: float,
    fill_price: float,
    slippage_bps: float,
    filled_margin: float = 0.0,
    error: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a binding-log dict per AC-Sz5 schema. Actual JSONL write
    happens in Task 15 via `write_sizing_fill_entry`.
    """
    intent_name = sizing.intent.value if sizing is not None else "FIXED_FRACTION"
    leverage = float(sizing.leverage) if sizing is not None else 1.0
    margin_mode = sizing.margin_mode if sizing is not None else "isolated"
    reduce_only = bool(sizing.reduce_only) if sizing is not None else False
    return {
        "order_id": getattr(order, "order_id", ""),
        "symbol": order.token,
        "strategy_id": order.strategy_id,
        "intent": intent_name,
        "requested_fraction": requested_fraction,
        "requested_notional": requested_notional,
        "leverage": leverage,
        "reduce_only": reduce_only,
        "margin_mode": margin_mode,
        "clamp_values": clamp_values,
        "binding_constraint": binding,
        "filled_margin": filled_margin,
        "filled_notional": filled_notional,
        "fill_price": fill_price,
        "slippage_bps": slippage_bps,
        "error": error,
    }
