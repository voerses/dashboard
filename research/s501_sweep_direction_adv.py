"""Sweep direction filter (long-only, short-only, both) and ADV thresholds."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
os.chdir(os.path.join(os.path.dirname(__file__), '..'))

import numpy as np
from v4.config import PortfolioConfig, StrategySpec
from v4.portfolio_signals import precompute_portfolio_signals
from v4.simulator import simulate_portfolio
from v4.universe import get_all_tradeable


def run_filtered(tokens, max_pos=15, direction_filter=None, min_conviction=0.0):
    """Run backtest with optional direction and conviction filters."""
    spec = StrategySpec(strategy_id='s501', market='perp', strategy_type='portfolio',
                        max_positions=max_pos, entry_resolution=1)
    config = PortfolioConfig(capital=100_000, exchange='binance', skip_walk_forward=True,
                             conviction_mode='ranked')

    signals = precompute_portfolio_signals(spec, tokens, config, months=12)

    # Apply direction filter
    if direction_filter is not None:
        for token, sig in signals.items():
            mask = sig.entry_mask.copy()
            for i in range(sig.n_bars):
                if mask[i] and sig.direction[i] != direction_filter:
                    mask[i] = False
            sig.entry_mask = mask

    # Apply conviction filter
    if min_conviction > 0:
        for token, sig in signals.items():
            if sig.conviction_score is None:
                continue
            mask = sig.entry_mask.copy()
            for i in range(sig.n_bars):
                if mask[i] and sig.conviction_score[i] < min_conviction:
                    mask[i] = False
            sig.entry_mask = mask

    state = simulate_portfolio({'s501': signals}, {'s501': spec}, config)
    return state


def report(label, state):
    trades = state.position_manager.closed_trades
    if not trades:
        print(f"  {label:>35s}  -- no trades --")
        return
    pnls = np.array([t.pnl for t in trades])

    raw_rets = []
    for t in trades:
        if t.direction == 1:
            r = (t.exit_price - t.entry_price) / t.entry_price
        else:
            r = (t.entry_price - t.exit_price) / t.entry_price
        raw_rets.append(r)
    raw_rets = np.array(raw_rets)

    eq = state.portfolio_equity
    ret = (eq / 100_000 - 1) * 100

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

    n_long = sum(1 for t in trades if t.direction == 1)
    n_short = sum(1 for t in trades if t.direction == -1)

    print(f"  {label:>35s}  {ret:>+7.1f}%  {max_dd:>6.1f}%  {sharpe:>7.2f}  "
          f"{calmar:>7.2f}  {wr:>4.1f}%  {pf:>4.2f}  {len(trades):>5d}  "
          f"L={n_long:>4d} S={n_short:>4d}  {raw_rets.mean()*100:>+7.3f}%")


def main():
    tokens = get_all_tradeable()
    print(f"Universe: {len(tokens)} tokens\n")

    header = (f"{'Config':>35s}  {'Return':>8s}  {'MaxDD':>7s}  {'Sharpe':>7s}  "
              f"{'Calmar':>7s}  {'WR':>5s}  {'PF':>5s}  {'Trades':>5s}  "
              f"{'L/S':>12s}  {'RawRet':>8s}")
    print(header)
    print("-" * 130)

    # Direction filters
    print("\n--- DIRECTION FILTER (max_pos=15) ---")
    state = run_filtered(tokens, max_pos=15, direction_filter=None)
    report("Both L+S", state)
    state = run_filtered(tokens, max_pos=15, direction_filter=1)
    report("Long only", state)
    state = run_filtered(tokens, max_pos=15, direction_filter=-1)
    report("Short only", state)

    # Conviction filters
    print("\n--- CONVICTION FILTER (max_pos=15, both directions) ---")
    for min_conv in [0.0, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]:
        state = run_filtered(tokens, max_pos=15, min_conviction=min_conv)
        report(f"min_conviction={min_conv:.1f}", state)

    # Max positions sweep with long-only
    print("\n--- LONG ONLY + max_pos sweep ---")
    for mp in [5, 10, 15, 20]:
        state = run_filtered(tokens, max_pos=mp, direction_filter=1)
        report(f"Long pos={mp}", state)

    print("\n--- SHORT ONLY + max_pos sweep ---")
    for mp in [5, 10, 15, 20]:
        state = run_filtered(tokens, max_pos=mp, direction_filter=-1)
        report(f"Short pos={mp}", state)


if __name__ == '__main__':
    main()
