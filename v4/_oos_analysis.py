"""Temporary OOS analysis script — run from within v4/ package"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from v4.portfolio_backtest import run_backtest
from v4.config import PortfolioConfig
from v4.report import compute_portfolio_metrics
from v4.simulator import simulate_portfolio

def run_with_equity(strategy_ids, market_overrides, label):
    config = PortfolioConfig()
    config._market_overrides = market_overrides
    
    from v4.portfolio_backtest import build_strategy_specs, precompute_all_signals
    strategy_specs = build_strategy_specs(strategy_ids, 74, config)
    for sid, spec in strategy_specs.items():
        if sid in market_overrides:
            spec['market'] = market_overrides[sid]
    
    precomputed = precompute_all_signals(strategy_specs, config)
    state = simulate_portfolio(precomputed, strategy_specs, config)
    metrics, extra_info, eq_daily = compute_portfolio_metrics(state, 200000)
    
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")
    print(f"  Sharpe: {metrics.sharpe_ratio:.2f}  Calmar: {metrics.calmar_ratio:.2f}  MaxDD: {metrics.max_drawdown_pct:.2f}%")
    print(f"  Trades: {metrics.total_trades}  Return: {metrics.total_return_pct:,.1f}%")
    
    oos_total = 0
    mar_daily = 0
    if eq_daily:
        dates = sorted(eq_daily.keys())
        print(f"  Equity: {dates[0]} to {dates[-1]} ({len(dates)}d)")
        
        for ml, prefix in [('Jan', '2026-01'), ('Feb', '2026-02'), ('Mar', '2026-03')]:
            md = [d for d in dates if d.startswith(prefix)]
            if not md: continue
            fi = dates.index(md[0])
            s = eq_daily[dates[fi-1]] if fi > 0 else eq_daily[md[0]]
            e = eq_daily[md[-1]]
            pnl = e - s
            print(f"    {ml}: ${pnl:+,.0f} ({len(md)}d, ${pnl/len(md):,.0f}/day)")
            if prefix == '2026-03':
                mar_daily = pnl / len(md)
        
        oos = [d for d in dates if d >= '2026-01-01']
        if oos:
            pi = dates.index(oos[0]) - 1
            pre = eq_daily[dates[pi]] if pi >= 0 else eq_daily[oos[0]]
            oos_total = eq_daily[oos[-1]] - pre
            print(f"    TOTAL: ${oos_total:+,.0f} ({len(oos)}d, ${oos_total/len(oos):,.0f}/day)")
    
    return metrics, oos_total, mar_daily

print("Running baseline (s58)...")
b_m, b_oos, b_mar = run_with_equity(['s56', 's57'], {'s56': 'perp', 's57': 'combined'}, 'BASELINE s58')

print("\nRunning Mode A (s58+s62)...")
a_m, a_oos, a_mar = run_with_equity(['s56', 's57', 's62'], {'s56': 'perp', 's57': 'combined', 's62': 'perp'}, 'MODE A s58+s62')

print(f"\n{'='*60}")
print(f"  FULL MISSION CRITERIA CHECK — Mode A (s58+s62)")
print(f"{'='*60}")
checks = [
    ("Sharpe >= 7.0", a_m.sharpe_ratio, 7.0, a_m.sharpe_ratio >= 7.0),
    ("MaxDD <= 3%", abs(a_m.max_drawdown_pct), 3.0, abs(a_m.max_drawdown_pct) <= 3.0),
    ("OOS PnL >= $131,878", a_oos, 131878, a_oos >= 131878),
    ("March daily >= $833", a_mar, 833, a_mar >= 833),
    ("Calmar improves", a_m.calmar_ratio, b_m.calmar_ratio, a_m.calmar_ratio > b_m.calmar_ratio),
]

kills = 0
for desc, val, thresh, passed in checks:
    status = "PASS" if passed else "KILL"
    if not passed: kills += 1
    if isinstance(val, float) and val > 1000:
        print(f"  {status}  {desc}: ${val:,.0f} vs ${thresh:,.0f}")
    else:
        print(f"  {status}  {desc}: {val:.2f} vs {thresh:.2f}")

print(f"\n  FINAL VERDICT: {'KILL' if kills else 'ALL PASS'} ({5-kills}/5 pass, {kills}/5 kill)")
