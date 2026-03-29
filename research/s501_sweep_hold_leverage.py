"""Sweep hold period and leverage to find optimal combination."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
os.chdir(os.path.join(os.path.dirname(__file__), '..'))

import numpy as np
from v4.config import PortfolioConfig, StrategySpec
from v4.portfolio_signals import precompute_portfolio_signals, _load_all_contexts
from v4.simulator import simulate_portfolio
from v4.universe import get_all_tradeable
from v4.engine import _load_strategy_fn


def run_with_hold_leverage(tokens, hold, leverage, max_pos=15):
    """Run backtest with modified max_hold and leverage."""
    # We need to modify the strategy's StrategyResult before signal conversion.
    # The cleanest way: precompute signals, then modify the TokenSignals max_hold/leverage.
    spec = StrategySpec(strategy_id='s501', market='perp', strategy_type='portfolio',
                        max_positions=max_pos, entry_resolution=1)
    config = PortfolioConfig(capital=100_000, exchange='binance', skip_walk_forward=True,
                             conviction_mode='ranked')

    signals = precompute_portfolio_signals(spec, tokens, config, months=12)

    # Override max_hold and leverage in each TokenSignals
    for token, sig in signals.items():
        sig.max_hold = hold
        sig.leverage = np.full(sig.n_bars, leverage, dtype=np.float32)

    state = simulate_portfolio({'s501': signals}, {'s501': spec}, config)
    return state


def main():
    tokens = get_all_tradeable()
    print(f"Universe: {len(tokens)} tokens\n")

    header = (f"{'Hold':>5s}  {'Lev':>4s}  {'Return':>8s}  {'MaxDD':>7s}  {'Sharpe':>7s}  "
              f"{'Calmar':>7s}  {'Sortino':>7s}  {'WR':>5s}  {'Trades':>6s}  "
              f"{'RawRet':>8s}  {'PF':>5s}  {'Fees':>8s}")
    print(header)
    print("-" * 115)

    for hold in [4, 6, 8, 10, 12, 16, 24]:
        for leverage in [1.0, 1.5, 2.0, 2.5]:
            state = run_with_hold_leverage(tokens, hold, leverage, max_pos=15)

            trades = state.position_manager.closed_trades
            if not trades:
                continue
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
            neg_rets = hourly_rets[hourly_rets < 0]
            sortino = hourly_rets.mean() / max(np.sqrt(np.mean(neg_rets**2)), 1e-10) * np.sqrt(8760)
            calmar = (ret / 100) / max(abs(max_dd / 100), 0.001)

            wins = pnls[pnls > 0].sum()
            losses = abs(pnls[pnls < 0].sum())
            pf = wins / max(losses, 1)

            print(f"  {hold:>4d}  {leverage:>4.1f}  {ret:>+7.1f}%  {max_dd:>6.1f}%  {sharpe:>7.2f}  "
                  f"{calmar:>7.2f}  {sortino:>7.2f}  {wr:>4.1f}%  {len(trades):>6d}  "
                  f"{raw_rets.mean()*100:>+7.3f}%  {pf:>4.2f}  ${state.total_fees:>7.0f}")

    print(f"\n  All runs with max_pos=15, conviction_mode=ranked, MIN_ADV=50M")


if __name__ == '__main__':
    main()
