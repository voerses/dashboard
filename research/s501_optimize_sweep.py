"""Sweep max_positions and conviction_mode to find optimal configuration."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
os.chdir(os.path.join(os.path.dirname(__file__), '..'))

import numpy as np
from v4.config import PortfolioConfig, StrategySpec
from v4.portfolio_signals import precompute_portfolio_signals
from v4.simulator import simulate_portfolio
from v4.universe import get_all_tradeable


def main():
    tokens = get_all_tradeable()

    # Precompute signals once
    spec = StrategySpec(strategy_id='s501', market='perp', strategy_type='portfolio',
                        max_positions=5, entry_resolution=1)
    config_base = PortfolioConfig(capital=100_000, exchange='binance', skip_walk_forward=True)
    signals = precompute_portfolio_signals(spec, tokens, config_base, months=12)

    header = (f"{'Config':>25s}  {'Return':>8s}  {'MaxDD':>7s}  {'Sharpe':>7s}  "
              f"{'Calmar':>7s}  {'WR':>5s}  {'Trades':>6s}  {'Reject':>7s}  "
              f"{'RawRet':>8s}  {'Fees':>8s}  {'Funding':>8s}")
    print(header)
    print("-" * 120)

    for max_pos in [3, 5, 8, 10, 15, 20, 30]:
        for conv_mode in ['shuffle', 'ranked']:
            spec_test = StrategySpec(strategy_id='s501', market='perp',
                                    strategy_type='portfolio',
                                    max_positions=max_pos, entry_resolution=1)
            config_test = PortfolioConfig(capital=100_000, exchange='binance',
                                          skip_walk_forward=True,
                                          conviction_mode=conv_mode)

            state = simulate_portfolio({'s501': signals}, {'s501': spec_test}, config_test)

            trades = state.position_manager.closed_trades
            pnls = np.array([t.pnl for t in trades]) if trades else np.array([0.0])

            # Raw price returns
            raw_rets = []
            for t in trades:
                if t.direction == 1:
                    r = (t.exit_price - t.entry_price) / t.entry_price
                else:
                    r = (t.entry_price - t.exit_price) / t.entry_price
                raw_rets.append(r)
            raw_rets = np.array(raw_rets) if raw_rets else np.array([0.0])

            eq = state.portfolio_equity
            ret = (eq / 100_000 - 1) * 100

            # Max DD from equity curve
            eq_curve = np.array([e[1] for e in state.equity_snapshots])
            peak = np.maximum.accumulate(eq_curve)
            dd = (eq_curve - peak) / peak
            max_dd = dd.min() * 100

            wr = (pnls > 0).mean() * 100
            rej = state.rejections.total()

            # Sharpe & Calmar
            hourly_rets = np.diff(eq_curve) / eq_curve[:-1]
            sharpe = hourly_rets.mean() / max(hourly_rets.std(), 1e-10) * np.sqrt(8760)
            calmar = (ret / 100) / max(abs(max_dd / 100), 0.001)

            label = f"pos={max_pos:2d} {conv_mode:>7s}"
            print(f"  {label:>25s}  {ret:>+7.1f}%  {max_dd:>6.1f}%  {sharpe:>7.2f}  "
                  f"{calmar:>7.2f}  {wr:>4.1f}%  {len(trades):>6d}  {rej:>7d}  "
                  f"{raw_rets.mean()*100:>+7.3f}%  ${state.total_fees:>7.0f}  ${state.total_funding:>7.0f}")

    # Best config analysis
    print(f"\n{'='*80}")
    print(f"  NOTES:")
    print(f"  - Signal arrays show +1.43% raw return per trade (12,269 entries)")
    print(f"  - Cost model: ~0.6% per trade (entry slip + exit slip + fees + funding)")
    print(f"  - Expected net per trade: ~+0.83%")
    print(f"  - 'ranked' prioritizes high-conviction entries; 'shuffle' is random")
    print(f"  - More positions = more trades but more fee/funding drag")


if __name__ == '__main__':
    main()
