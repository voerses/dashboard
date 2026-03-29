"""Sweep position sizing parameters: edge, cap_multiplier, and capital."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
os.chdir(os.path.join(os.path.dirname(__file__), '..'))

import numpy as np
from v4.config import PortfolioConfig, StrategySpec
from v4.portfolio_signals import precompute_portfolio_signals
from v4.simulator import simulate_portfolio
from v4.universe import get_all_tradeable


def run_with_sizing(tokens, edge, cap_mult, max_pos=15, leverage=1.0, capital=100_000):
    """Run backtest with modified sizing parameters."""
    spec = StrategySpec(strategy_id='s501', market='perp', strategy_type='portfolio',
                        max_positions=max_pos, entry_resolution=1)
    config = PortfolioConfig(capital=capital, exchange='binance', skip_walk_forward=True,
                             conviction_mode='ranked')

    signals = precompute_portfolio_signals(spec, tokens, config, months=12)

    # Override edge and cap_multiplier in each TokenSignals
    for token, sig in signals.items():
        sig.edge = edge
        sig.cap_multiplier = np.full(sig.n_bars, cap_mult, dtype=np.float32)
        sig.leverage = np.full(sig.n_bars, leverage, dtype=np.float32)

    state = simulate_portfolio({'s501': signals}, {'s501': spec}, config)
    return state


def report(label, state, capital=100_000):
    trades = state.position_manager.closed_trades
    if not trades:
        print(f"  {label:>40s}  -- no trades --")
        return
    pnls = np.array([t.pnl for t in trades])
    margins = np.array([t.margin_usd for t in trades])

    eq = state.portfolio_equity
    ret = (eq / capital - 1) * 100

    eq_curve = np.array([e[1] for e in state.equity_snapshots])
    peak = np.maximum.accumulate(eq_curve)
    dd = (eq_curve - peak) / peak
    max_dd = dd.min() * 100

    wr = (pnls > 0).mean() * 100
    hourly_rets = np.diff(eq_curve) / eq_curve[:-1]
    sharpe = hourly_rets.mean() / max(hourly_rets.std(), 1e-10) * np.sqrt(8760)
    calmar = (ret / 100) / max(abs(max_dd / 100), 0.001)

    wins = pnls[pnls > 0].sum()
    losses = abs(pnls[pnls < 0].sum())
    pf = wins / max(losses, 1)

    avg_margin = margins.mean()
    total_margin = margins.sum()
    utilization = total_margin / (capital * len(eq_curve)) * 100  # avg capital used

    print(f"  {label:>40s}  {ret:>+8.1f}%  {max_dd:>6.1f}%  {sharpe:>7.2f}  "
          f"{calmar:>7.2f}  {wr:>4.1f}%  {pf:>4.2f}  {len(trades):>5d}  "
          f"${avg_margin:>6.0f}  ${state.total_fees:>7.0f}  {state.rejections.total():>5d}")


def main():
    tokens = get_all_tradeable()
    print(f"Universe: {len(tokens)} tokens\n")

    header = (f"{'Config':>40s}  {'Return':>8s}  {'MaxDD':>7s}  {'Sharpe':>7s}  "
              f"{'Calmar':>7s}  {'WR':>5s}  {'PF':>5s}  {'Trades':>5s}  "
              f"{'AvgMgn':>7s}  {'Fees':>8s}  {'Rej':>5s}")
    print(header)
    print("-" * 145)

    # Edge sweep (controls Kelly fraction)
    print("\n--- EDGE SWEEP (cap_mult=2.5, lev=1.0, pos=15) ---")
    for edge in [0.20, 0.25, 0.30, 0.35, 0.40, 0.50, 0.60, 0.80, 1.00]:
        state = run_with_sizing(tokens, edge=edge, cap_mult=2.5, max_pos=15)
        report(f"edge={edge:.2f}", state)

    # Cap multiplier sweep
    print("\n--- CAP_MULT SWEEP (edge=0.35, lev=1.0, pos=15) ---")
    for cm in [1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 8.0]:
        state = run_with_sizing(tokens, edge=0.35, cap_mult=cm, max_pos=15)
        report(f"cap_mult={cm:.1f}", state)

    # Combined: higher edge + higher cap_mult + leverage
    print("\n--- AGGRESSIVE CONFIGS (pos=15) ---")
    configs = [
        ("baseline", 0.35, 2.5, 1.0),
        ("edge=0.5 lev=1.5", 0.50, 2.5, 1.5),
        ("edge=0.6 lev=1.5", 0.60, 3.0, 1.5),
        ("edge=0.8 lev=2.0", 0.80, 3.0, 2.0),
        ("edge=1.0 cap=4 lev=2", 1.00, 4.0, 2.0),
        ("edge=1.0 cap=5 lev=2.5", 1.00, 5.0, 2.5),
        ("edge=0.5 cap=5 lev=1.0", 0.50, 5.0, 1.0),
        ("edge=0.5 cap=8 lev=1.0", 0.50, 8.0, 1.0),
    ]
    for label, edge, cm, lev in configs:
        state = run_with_sizing(tokens, edge=edge, cap_mult=cm, max_pos=15, leverage=lev)
        report(label, state)

    # Best config at different max_positions
    print("\n--- BEST SIZING @ DIFFERENT POS COUNTS (edge=0.50, cap=5.0, lev=1.0) ---")
    for mp in [5, 10, 15, 20, 30]:
        state = run_with_sizing(tokens, edge=0.50, cap_mult=5.0, max_pos=mp, leverage=1.0)
        report(f"pos={mp}", state)


if __name__ == '__main__':
    main()
