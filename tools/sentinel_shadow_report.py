"""Shadow reconciliation: join sentinel events with hourly trades (AC18).

Classifies each shadow event as true_positive, false_trigger, or preempted.
Produces a report with per-strategy and per-tier breakdowns, go/no-go verdict.
"""
from __future__ import annotations

from collections import defaultdict


def join_shadow_with_trades(
    shadow_events: list[dict], trades: list[dict],
) -> list[dict]:
    """Join shadow events with trades by position_id.

    Returns list of dicts with 'shadow' and 'trade' keys.
    Unmatched shadow events have trade=None.
    """
    trade_map = {t["position_id"]: t for t in trades}
    result = []
    for se in shadow_events:
        pid = se["position_id"]
        result.append({
            "shadow": se,
            "trade": trade_map.get(pid),
        })
    return result


def classify_event(shadow: dict, trade: dict | None) -> str:
    """Classify a shadow event against its corresponding trade.

    - true_positive: hourly also exited with stop
    - false_trigger: no trade (position recovered)
    - preempted: hourly exited for a different reason
    """
    if trade is None:
        return "false_trigger"

    shadow_reason = shadow.get("exit_reason", "")
    trade_reason = trade.get("exit_reason", "")

    # Sentinel reason sentinel_stop -> hourly reason stop
    sentinel_to_hourly = {
        "sentinel_stop": "stop",
        "sentinel_cb": "circuit_breaker",
        "sentinel_target": "target",
        "sentinel_liq": "liquidation",
    }
    expected_hourly = sentinel_to_hourly.get(shadow_reason, shadow_reason)

    if trade_reason == expected_hourly or trade_reason == "stop":
        return "true_positive"
    return "preempted"


def compute_gross_benefit(events: list[dict]) -> float:
    """Compute total gross benefit (USD) from true_positive events.

    Benefit = quantity * direction * (sentinel_exit - hourly_exit).
    """
    total = 0.0
    for e in events:
        if e["classification"] != "true_positive":
            continue
        shadow = e["shadow"]
        trade = e["trade"]
        qty = abs(shadow.get("quantity", 0))
        direction = shadow.get("direction", 1)
        sentinel_exit = shadow.get("exit_price", 0)
        hourly_exit = trade.get("exit_price", 0)
        total += qty * direction * (sentinel_exit - hourly_exit)
    return total


def compute_false_trigger_cost(events: list[dict]) -> float:
    """Compute total false trigger cost (USD).

    Cost = sum of abs(sentinel_pnl_estimate) for false triggers.
    """
    total = 0.0
    for e in events:
        if e["classification"] != "false_trigger":
            continue
        pnl_est = e["shadow"].get("sentinel_pnl_estimate_usd", 0)
        total += abs(pnl_est)
    return total


def apply_haircut(
    gross: float, cost: float, haircut_pct: float = 0.30,
) -> float:
    """Apply realism haircut: net = (gross - cost) * (1 - haircut_pct)."""
    return (gross - cost) * (1 - haircut_pct)


def generate_shadow_report(
    events: list[dict], capital: float,
) -> dict:
    """Generate full shadow reconciliation report.

    Returns dict with per_strategy, per_tier, go_nogo, and summary fields.
    """
    total = len(events)
    if total == 0:
        return {
            "per_strategy": [],
            "per_tier": [],
            "go_nogo": "yellow",
            "summary": {"total_events": 0},
        }

    # Classify counts
    counts = defaultdict(int)
    for e in events:
        counts[e["classification"]] += 1

    ft_count = counts.get("false_trigger", 0)
    ft_rate = ft_count / total if total > 0 else 0

    # Compute financials
    gross = compute_gross_benefit(events)
    cost = compute_false_trigger_cost(events)
    net = apply_haircut(gross, cost)

    # Per-strategy breakdown
    by_strategy: dict[str, list] = defaultdict(list)
    for e in events:
        sid = e["shadow"].get("strategy_id", "unknown")
        by_strategy[sid].append(e)

    per_strategy = []
    for sid, evts in sorted(by_strategy.items()):
        tp = sum(1 for e in evts if e["classification"] == "true_positive")
        ft = sum(1 for e in evts if e["classification"] == "false_trigger")
        pre = sum(1 for e in evts if e["classification"] == "preempted")
        per_strategy.append({
            "strategy_id": sid,
            "true_positive": tp,
            "false_trigger": ft,
            "preempted": pre,
            "total": len(evts),
        })

    # Per-tier breakdown
    by_tier: dict[str, list] = defaultdict(list)
    for e in events:
        tier = e["shadow"].get("liquidity_tier", "unknown")
        by_tier[tier].append(e)

    per_tier = []
    for tier, evts in sorted(by_tier.items()):
        tp = sum(1 for e in evts if e["classification"] == "true_positive")
        ft = sum(1 for e in evts if e["classification"] == "false_trigger")
        per_tier.append({
            "tier": tier,
            "true_positive": tp,
            "false_trigger": ft,
            "total": len(evts),
        })

    # Go/no-go
    if ft_rate > 0.40:
        go_nogo = "red"
    elif ft_rate < 0.30 and total >= 5 and net > 0:
        go_nogo = "green"
    else:
        go_nogo = "yellow"

    return {
        "per_strategy": per_strategy,
        "per_tier": per_tier,
        "go_nogo": go_nogo,
        "summary": {
            "total_events": total,
            "true_positive": counts.get("true_positive", 0),
            "false_trigger": ft_count,
            "preempted": counts.get("preempted", 0),
            "false_trigger_rate": round(ft_rate, 4),
            "gross_benefit_usd": round(gross, 2),
            "false_trigger_cost_usd": round(cost, 2),
            "net_benefit_usd": round(net, 2),
        },
    }
