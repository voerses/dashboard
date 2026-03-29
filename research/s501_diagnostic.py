"""Diagnostic: compare research per-trade edge vs backtest reality.

Tests multiple configurations to isolate the source of loss:
  A) Research baseline — raw per-trade returns (no portfolio)
  B) Backtest with current strategy settings
  C) Backtest with stripped settings (flat sizing, no partial TP, no leverage issues)
"""
import sys, os
import numpy as np
import pandas as pd
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from v4.config import PortfolioConfig, StrategySpec
from v4.portfolio_signals import precompute_portfolio_signals
from v4.simulator import simulate_portfolio
from v4.universe import get_all_tradeable


def run_backtest(label, tokens, max_pos, leverage_override=None):
    """Run backtest and return trade-level stats."""
    capital = 100_000
    config = PortfolioConfig(
        capital=capital,
        exchange='binance',
        skip_walk_forward=True,
    )
    spec = StrategySpec(
        strategy_id='s501',
        market='perp',
        strategy_type='portfolio',
        max_positions=max_pos,
        entry_resolution=1,
    )

    signals = precompute_portfolio_signals(spec, tokens, config, months=12)
    state = simulate_portfolio({'s501': signals}, {'s501': spec}, config)

    trades = state.position_manager.closed_trades
    r = state.rejections

    n = len(trades)
    if n == 0:
        print(f"\n  [{label}] No trades executed")
        return

    pnls = np.array([t.pnl for t in trades])
    margins = np.array([t.margin_usd for t in trades])
    holds = np.array([t.hold_bars for t in trades])
    reasons = [t.exit_reason for t in trades]

    wr = (pnls > 0).mean()
    reason_counts = Counter(reasons)
    liq_count = reason_counts.get('liquidation', 0)
    liq_loss = sum(t.pnl for t in trades if t.exit_reason == 'liquidation')
    mh_trades = [t for t in trades if t.exit_reason == 'max_hold']
    mh_wr = np.mean([t.pnl > 0 for t in mh_trades]) if mh_trades else 0

    print(f"\n  [{label}] max_pos={max_pos}")
    print(f"    Trades: {n}, Rejected: {r.total()} (strat_limit={r.strategy_limit}, capital={r.capital})")
    print(f"    Equity: ${state.portfolio_equity:,.0f} ({(state.portfolio_equity/capital - 1)*100:+.1f}%)")
    print(f"    WR: {wr*100:.1f}%  |  max_hold WR: {mh_wr*100:.1f}% ({len(mh_trades)} trades)")
    print(f"    Liquidations: {liq_count} (${liq_loss:+,.0f})")
    print(f"    Fees: ${state.total_fees:,.0f}  Funding: ${state.total_funding:,.0f}")
    print(f"    Avg hold: {holds.mean():.1f}  Avg margin: ${margins.mean():,.0f}")

    # Per-trade raw price return (not leveraged)
    raw_rets = []
    for t in trades:
        if t.direction == 1:
            r_val = (t.exit_price - t.entry_price) / t.entry_price
        else:
            r_val = (t.entry_price - t.exit_price) / t.entry_price
        raw_rets.append(r_val)
    raw_rets = np.array(raw_rets)
    print(f"    Raw price return (no leverage): mean={raw_rets.mean()*100:+.3f}%  median={np.median(raw_rets)*100:+.3f}%")
    print(f"    Raw WR (price return > 0): {(raw_rets > 0).mean()*100:.1f}%")

    # max_hold only
    if mh_trades:
        mh_rets = []
        for t in mh_trades:
            if t.direction == 1:
                r_val = (t.exit_price - t.entry_price) / t.entry_price
            else:
                r_val = (t.entry_price - t.exit_price) / t.entry_price
            mh_rets.append(r_val)
        mh_rets = np.array(mh_rets)
        print(f"    max_hold raw return: mean={mh_rets.mean()*100:+.3f}%  median={np.median(mh_rets)*100:+.3f}%")
        print(f"    max_hold raw WR: {(mh_rets > 0).mean()*100:.1f}%")

    return state


def main():
    tokens = get_all_tradeable()
    print(f"Universe: {len(tokens)} tokens")

    print(f"\n{'='*70}")
    print(f"  CONFIG A: max_positions=5 (current)")
    print(f"{'='*70}")
    run_backtest("5pos", tokens, max_pos=5)

    print(f"\n{'='*70}")
    print(f"  CONFIG B: max_positions=20")
    print(f"{'='*70}")
    run_backtest("20pos", tokens, max_pos=20)

    print(f"\n{'='*70}")
    print(f"  CONFIG C: max_positions=89 (no limit)")
    print(f"{'='*70}")
    run_backtest("89pos", tokens, max_pos=89)

    print(f"\n{'='*70}")
    print(f"  RESEARCH COMPARISON")
    print(f"{'='*70}")
    print(f"  Research 8h: EV=+1.295%  WR=59.9%  N=20,044")
    print(f"  If backtest max_hold raw return << research, the simulator")
    print(f"  is applying costs/slippage or the entry price differs.")


if __name__ == '__main__':
    main()
