"""Trace exact entry/exit prices: simulator vs signal arrays for the same trades."""
import sys, os
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from v4.config import PortfolioConfig, StrategySpec
from v4.portfolio_signals import precompute_portfolio_signals
from v4.simulator import simulate_portfolio, build_unified_index
from v4.universe import get_all_tradeable


def main():
    tokens = get_all_tradeable()
    capital = 100_000
    config = PortfolioConfig(capital=capital, exchange='binance', skip_walk_forward=True)
    spec = StrategySpec(strategy_id='s501', market='perp', strategy_type='portfolio',
                        max_positions=5, entry_resolution=1)

    signals = precompute_portfolio_signals(spec, tokens, config, months=12)
    all_signals = {'s501': signals}

    # Build bar maps so we can map global_bar -> local_bar
    unified_ts, bar_maps = build_unified_index(all_signals)

    state = simulate_portfolio(all_signals, {'s501': spec}, config)
    trades = state.position_manager.closed_trades

    # For each max_hold trade, compare simulator vs signal array
    print(f"{'Token':>10s}  {'Dir':>4s}  {'GBar':>5s}  {'LBar':>5s}  "
          f"{'SimEntry':>10s}  {'SigLimit':>10s}  {'SigClose':>10s}  "
          f"{'SimExit':>10s}  {'Sig+8Close':>10s}  "
          f"{'SimRet':>8s}  {'SigRet':>8s}  {'Gap':>8s}")
    print("-" * 130)

    count = 0
    sim_rets = []
    sig_rets = []

    for t in trades:
        if t.exit_reason != 'max_hold':
            continue

        token = t.token
        if token not in signals:
            continue
        sig = signals[token]
        if token not in bar_maps:
            continue
        bm = bar_maps[token]

        entry_gbar = t.entry_bar
        exit_gbar = entry_gbar + t.hold_bars

        # Map global bar to local bar
        if entry_gbar >= len(bm):
            continue
        local_entry = int(bm[entry_gbar])
        if local_entry < 0:
            continue

        local_exit = local_entry + t.hold_bars
        if local_exit >= len(sig.close):
            continue

        d = t.direction

        # Simulator prices (slippage baked in)
        sim_entry = t.entry_price
        sim_exit = t.exit_price

        # Signal array prices (raw)
        sig_limit = float(sig.entry_limit_price[local_entry]) if sig.entry_limit_price is not None else np.nan
        sig_close_entry = float(sig.close[local_entry])
        sig_close_exit = float(sig.close[local_exit])

        # Returns
        if d == 1:
            sim_ret = (sim_exit - sim_entry) / sim_entry
            sig_ret = (sig_close_exit - sig_limit) / sig_limit if not np.isnan(sig_limit) else np.nan
        else:
            sim_ret = (sim_entry - sim_exit) / sim_entry
            sig_ret = (sig_limit - sig_close_exit) / sig_limit if not np.isnan(sig_limit) else np.nan

        if np.isnan(sig_ret):
            continue

        sim_rets.append(sim_ret)
        sig_rets.append(sig_ret)

        if count < 30:
            d_str = 'L' if d == 1 else 'S'
            print(f"  {token:>10s}  {d_str:>4s}  {entry_gbar:>5d}  {local_entry:>5d}  "
                  f"${sim_entry:>9.4f}  ${sig_limit:>9.4f}  ${sig_close_entry:>9.4f}  "
                  f"${sim_exit:>9.4f}  ${sig_close_exit:>9.4f}  "
                  f"{sim_ret*100:>+7.3f}%  {sig_ret*100:>+7.3f}%  {(sig_ret-sim_ret)*100:>+7.3f}%")
        count += 1

    print(f"\n--- Summary ({count} max_hold trades matched) ---")
    sim_rets = np.array(sim_rets)
    sig_rets = np.array(sig_rets)
    gap = sig_rets - sim_rets

    print(f"  Simulator raw return:  mean={sim_rets.mean()*100:+.3f}%  median={np.median(sim_rets)*100:+.3f}%  WR={100*(sim_rets>0).mean():.1f}%")
    print(f"  Signal array return:   mean={sig_rets.mean()*100:+.3f}%  median={np.median(sig_rets)*100:+.3f}%  WR={100*(sig_rets>0).mean():.1f}%")
    print(f"  Gap (signal - sim):    mean={gap.mean()*100:+.3f}%  median={np.median(gap)*100:+.3f}%")
    print(f"  Signal>0 & Sim<0:      {((sig_rets > 0) & (sim_rets < 0)).sum()} trades ({100*((sig_rets > 0) & (sim_rets < 0)).mean():.1f}%)")

    # Check if entry_limit_price == 1m cross close (not BB level)
    # The BB level would be noticeably different from the close
    print(f"\n--- Entry Price Check ---")
    entry_diffs = []
    for t in trades[:500]:
        if t.exit_reason != 'max_hold':
            continue
        token = t.token
        if token not in signals or token not in bar_maps:
            continue
        sig = signals[token]
        bm = bar_maps[token]
        if t.entry_bar >= len(bm):
            continue
        lb = int(bm[t.entry_bar])
        if lb < 0 or sig.entry_limit_price is None:
            continue
        lp = float(sig.entry_limit_price[lb])
        if np.isnan(lp):
            continue
        close_val = float(sig.close[lb])
        # For long: entry limit should be < close (buying below close)
        # Diff = (close - limit) / close
        if t.direction == 1:
            diff = (close_val - lp) / close_val
        else:
            diff = (lp - close_val) / close_val
        entry_diffs.append(diff)
        # Also check: simulator entry vs limit
        sim_vs_limit = (t.entry_price - lp) / lp * t.direction
        if len(entry_diffs) <= 10:
            print(f"  {token:>10s}  limit=${lp:.4f}  close=${close_val:.4f}  "
                  f"sim_entry=${t.entry_price:.4f}  improvement={(diff)*100:+.3f}%  "
                  f"sim_slip={(sim_vs_limit)*100:+.3f}%")

    entry_diffs = np.array(entry_diffs)
    print(f"\n  Entry improvement (close vs limit): mean={entry_diffs.mean()*100:+.3f}%  "
          f"median={np.median(entry_diffs)*100:+.3f}%")


if __name__ == '__main__':
    main()
