#!/usr/bin/env python3
"""Before/after comparison: s72 12mo with realistic sizing constraints."""
import sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "v4"))

from v4.config import PortfolioConfig, StrategySpec
from v4.signals import precompute_strategy_signals, discover_tokens, infer_data_end_date
from v4.simulator import simulate_portfolio
from v4.report import compute_portfolio_metrics

CAPITAL = 200_000


def run_s72(label, config, spec, months=12):
    data_end = infer_data_end_date("perp")
    tokens = discover_tokens("perp")
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")
    print(f"  Precomputing signals ({months}mo, {len(tokens)} tokens)...")
    t0 = time.time()
    signals = precompute_strategy_signals(spec, tokens, config, months, end_date=data_end)
    print(f"  Signals ready ({time.time()-t0:.1f}s). Running simulation...")
    t1 = time.time()
    state = simulate_portfolio({"s72": signals}, {"s72": spec}, config)
    m, extra, _ = compute_portfolio_metrics(state, CAPITAL)
    print(f"  Simulation done ({time.time()-t1:.1f}s).")
    return m, extra


def main():
    # BEFORE: all new features disabled (backward-compatible defaults)
    config_before = PortfolioConfig(
        capital=CAPITAL,
        max_portfolio_positions=15,
        concentration_limit=0.10,
        adv_cap_pct=0.05,
        seed=42,
        # Defaults: max_sizing_equity=None, stress_adv_multiplier=1.0, impact_coeff=0.03
    )
    spec_before = StrategySpec(
        strategy_id="s72", weight=1.0, max_positions=15,
        market="perp", strategy_type="per_token",
        # Defaults: adv_sizing_enabled=False
    )

    # AFTER: all deployment values enabled
    config_after = PortfolioConfig(
        capital=CAPITAL,
        max_portfolio_positions=15,
        concentration_limit=0.10,
        adv_cap_pct=0.05,
        seed=42,
        max_sizing_equity=2_000_000,
        stress_adv_multiplier=0.5,
        impact_coeff=0.01,
    )
    spec_after = StrategySpec(
        strategy_id="s72", weight=1.0, max_positions=15,
        market="perp", strategy_type="per_token",
        adv_sizing_enabled=True,
        adv_sizing_base=75_000_000,
    )

    m_before, ex_before = run_s72("BEFORE (defaults — no sizing constraints)", config_before, spec_before)
    m_after, ex_after = run_s72("AFTER (realistic sizing constraints)", config_after, spec_after)

    # Compare
    print(f"\n{'='*70}")
    print(f"  S72 12-MONTH COMPARISON: BEFORE vs AFTER")
    print(f"{'='*70}")
    print(f"  {'Metric':<30} {'BEFORE':>12} {'AFTER':>12} {'DELTA':>12}")
    print(f"  {'-'*66}")

    rows = [
        ("Total Return %",      f"{m_before.total_return_pct:+.1f}%",    f"{m_after.total_return_pct:+.1f}%",     f"{m_after.total_return_pct - m_before.total_return_pct:+.1f}%"),
        ("Annualized Return %", f"{m_before.annualized_return_pct:+.1f}%", f"{m_after.annualized_return_pct:+.1f}%", f"{m_after.annualized_return_pct - m_before.annualized_return_pct:+.1f}%"),
        ("Final Equity",        f"${ex_before['final_equity']:,.0f}",    f"${ex_after['final_equity']:,.0f}",     f"${ex_after['final_equity'] - ex_before['final_equity']:+,.0f}"),
        ("Sharpe Ratio",        f"{m_before.sharpe_ratio:.2f}",          f"{m_after.sharpe_ratio:.2f}",           f"{m_after.sharpe_ratio - m_before.sharpe_ratio:+.2f}"),
        ("Sortino Ratio",       f"{m_before.sortino_ratio:.2f}",         f"{m_after.sortino_ratio:.2f}",          f"{m_after.sortino_ratio - m_before.sortino_ratio:+.2f}"),
        ("Calmar Ratio",        f"{m_before.calmar_ratio:.2f}",          f"{m_after.calmar_ratio:.2f}",           f"{m_after.calmar_ratio - m_before.calmar_ratio:+.2f}"),
        ("Max Drawdown %",      f"{m_before.max_drawdown_pct:.2f}%",     f"{m_after.max_drawdown_pct:.2f}%",      f"{m_after.max_drawdown_pct - m_before.max_drawdown_pct:+.2f}%"),
        ("Total Trades",        f"{m_before.total_trades}",              f"{m_after.total_trades}",               f"{m_after.total_trades - m_before.total_trades:+d}"),
        ("Win Rate %",          f"{m_before.win_rate_pct:.1f}%",         f"{m_after.win_rate_pct:.1f}%",          f"{m_after.win_rate_pct - m_before.win_rate_pct:+.1f}%"),
        ("Profit Factor",       f"{m_before.profit_factor}",             f"{m_after.profit_factor}",              ""),
        ("Avg Trade PnL",       f"${m_before.avg_trade_pnl:,.0f}",      f"${m_after.avg_trade_pnl:,.0f}",        f"${m_after.avg_trade_pnl - m_before.avg_trade_pnl:+,.0f}"),
        ("Avg Hold Hours",      f"{m_before.avg_hold_hours:.1f}",        f"{m_after.avg_hold_hours:.1f}",         f"{m_after.avg_hold_hours - m_before.avg_hold_hours:+.1f}"),
        ("Total Funding",       f"${ex_before['total_funding']:+,.0f}",  f"${ex_after['total_funding']:+,.0f}",   f"${ex_after['total_funding'] - ex_before['total_funding']:+,.0f}"),
        ("Total Fees",          f"${ex_before['total_fees']:,.0f}",      f"${ex_after['total_fees']:,.0f}",       f"${ex_after['total_fees'] - ex_before['total_fees']:+,.0f}"),
    ]

    for label, before, after, delta in rows:
        print(f"  {label:<30} {before:>12} {after:>12} {delta:>12}")

    print(f"  {'-'*66}")

    # Rejections comparison
    rej_before = ex_before["rejections"]
    rej_after = ex_after["rejections"]
    print(f"\n  Rejections:")
    for key in sorted(set(list(rej_before.keys()) + list(rej_after.keys()))):
        b = rej_before.get(key, 0)
        a = rej_after.get(key, 0)
        if b > 0 or a > 0:
            print(f"    {key:<26} {b:>8} {a:>8} {a-b:>+8}")

    print(f"\n  Note: 'BEFORE' uses default config (hourly slippage is baked in).")
    print(f"  The AFTER adds: equity cap $2M, ADV sizing ($75M base),")
    print(f"  stress_adv_multiplier=0.5, impact_coeff=0.01")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
