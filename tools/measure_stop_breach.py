"""Phase 0 measurement: intra-bar stop breach analysis (AC0a-AC0e, AC21).

Analyses 1-minute klines to measure how much slippage occurs between
the actual stop breach time and the hourly bar close where the exit
is currently executed.  Produces a go/no-go report for sentinel deployment.
"""
from __future__ import annotations


def detect_intra_bar_breach(
    klines: list[dict],
    stop_price: float,
    direction: int,
) -> dict | None:
    """Find the first 1m bar where stop is breached intra-bar.

    Long: breach when low < stop_price.
    Short: breach when high > stop_price.

    Returns dict with breach_bar_index, breach_price, open_time; or None.
    """
    for i, bar in enumerate(klines):
        if direction == 1:
            if bar["low"] < stop_price:
                return {
                    "breach_bar_index": i,
                    "breach_price": bar["low"],
                    "open_time": bar["open_time"],
                }
        else:
            if bar["high"] > stop_price:
                return {
                    "breach_bar_index": i,
                    "breach_price": bar["high"],
                    "open_time": bar["open_time"],
                }
    return None


def compute_counterfactual_pnl(trade: dict, breach_price: float) -> dict:
    """Compute counterfactual PnL if exit happened at breach_price.

    Returns dict with actual_pnl, counterfactual_pnl, improvement_usd,
    slippage_bps.
    """
    qty = abs(trade["quantity"])
    direction = trade["direction"]
    entry = trade["entry_price"]
    actual_exit = trade["exit_price"]

    actual_pnl = qty * direction * (actual_exit - entry)
    counterfactual_pnl = qty * direction * (breach_price - entry)
    improvement = counterfactual_pnl - actual_pnl

    stop_price = trade.get("stop_price", entry)
    slippage_bps = abs(actual_exit - breach_price) / stop_price * 10_000

    return {
        "actual_pnl": actual_pnl,
        "counterfactual_pnl": counterfactual_pnl,
        "improvement_usd": improvement,
        "slippage_bps": slippage_bps,
    }


def identify_false_triggers(
    klines: list[dict],
    stop_price: float,
    direction: int,
    bar_close_price: float,
) -> dict:
    """Identify false triggers: intra-bar breach followed by recovery.

    A false trigger occurs when:
    - Stop was breached intra-bar
    - But bar closed on the safe side (position not exited hourly)

    Returns dict with is_false_trigger, breach_price, recovery_price.
    """
    breach = detect_intra_bar_breach(klines, stop_price, direction)
    if breach is None:
        return {"is_false_trigger": False, "breach_price": None, "recovery_price": None}

    # Check if bar closed on safe side (no exit at hourly level)
    if direction == 1:
        recovered = bar_close_price >= stop_price
    else:
        recovered = bar_close_price <= stop_price

    return {
        "is_false_trigger": recovered,
        "breach_price": breach["breach_price"],
        "recovery_price": bar_close_price if recovered else None,
    }


def apply_realism_haircut(gross_benefit: float, false_trigger_cost: float) -> float:
    """Apply 30% realism haircut to net benefit.

    net = (gross - cost) * 0.7
    """
    return (gross_benefit - false_trigger_cost) * 0.7


def generate_go_nogo_report(portfolio_data: list[dict]) -> list[dict]:
    """Generate go/no-go report for each portfolio.

    Thresholds:
    - net_benefit >= 1.0%: proceed
    - net_benefit < 0.5%: kill
    - 0.5% <= net_benefit < 1.0%: gray_zone
    """
    report = []
    for p in portfolio_data:
        gross = p["gross_benefit_annualized_pct"]
        cost = p["false_trigger_cost_annualized_pct"]
        net = apply_realism_haircut(gross, cost)

        if net >= 1.0:
            rec = "proceed"
        elif net < 0.5:
            rec = "kill"
        else:
            rec = "gray_zone"

        report.append({
            "portfolio_name": p["portfolio_name"],
            "num_stop_exits": p["num_stop_exits"],
            "avg_slippage_bps": p["avg_slippage_bps"],
            "worst_slippage_bps": p["worst_slippage_bps"],
            "gross_benefit_pct": gross,
            "false_trigger_cost_pct": cost,
            "net_benefit_pct": round(net, 4),
            "recommendation": rec,
        })
    return report
