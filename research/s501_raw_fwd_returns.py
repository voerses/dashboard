"""Compare raw forward returns from signal arrays vs research numbers.

Bypasses the simulator entirely. For each entry bar with entry_limit_price,
computes the raw price return at bar+4 and bar+8, same as the research.
"""
import sys, os
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from v4.config import PortfolioConfig, StrategySpec
from v4.portfolio_signals import precompute_portfolio_signals, _load_all_contexts
from v4.universe import get_all_tradeable
from v4.engine import _load_strategy_fn


def main():
    tokens = get_all_tradeable()
    print(f"Universe: {len(tokens)} tokens")

    config = PortfolioConfig(capital=100_000, exchange='binance', skip_walk_forward=True)
    spec = StrategySpec(strategy_id='s501', market='perp', strategy_type='portfolio',
                        max_positions=5, entry_resolution=1)

    # Load contexts and run strategy
    strategy = _load_strategy_fn('s501')
    contexts, cutoff, anchor = _load_all_contexts(tokens, spec, config, months=12)
    print(f"Built contexts: {len(contexts)} tokens")

    results = strategy(contexts)
    print(f"Strategy results: {len(results)} tokens with signals")

    # Get 1m-resolved prices
    from v4.portfolio_signals import _resolve_minute_entries
    _resolve_minute_entries(results, contexts, spec)

    # Compute raw forward returns directly from arrays
    COST = 2 * (0.00045 + 3.0 / 10000)  # round-trip fee + slippage estimate
    all_rets_4 = []
    all_rets_8 = []
    all_rets_1 = []
    entry_prices_1m = []
    close_at_entry = []
    directions_list = []

    for token, sr in sorted(results.items()):
        if sr.entry_limit_price is None:
            continue

        ctx = contexts[token]
        if isinstance(ctx, tuple):
            ctx = ctx[1] if ctx[1] is not None else ctx[0]

        close = ctx.ind_1h['close']
        n = len(close)
        entry_bars = np.where(sr.entry_mask)[0]

        for bar in entry_bars:
            lp = sr.entry_limit_price[bar]
            if np.isnan(lp):
                continue

            d = int(sr.direction[bar])
            entry_p = lp  # 1m cross price (already resolved)

            entry_prices_1m.append(entry_p)
            close_at_entry.append(float(close[bar]))
            directions_list.append(d)

            # Forward return at bar+1
            if bar + 1 < n:
                exit_p = float(close[bar + 1])
                if d == 1:
                    r = (exit_p - entry_p) / entry_p
                else:
                    r = (entry_p - exit_p) / entry_p
                all_rets_1.append(r)

            # Forward return at bar+4
            if bar + 4 < n:
                exit_p = float(close[bar + 4])
                if d == 1:
                    r = (exit_p - entry_p) / entry_p
                else:
                    r = (entry_p - exit_p) / entry_p
                all_rets_4.append(r)

            # Forward return at bar+8
            if bar + 8 < n:
                exit_p = float(close[bar + 8])
                if d == 1:
                    r = (exit_p - entry_p) / entry_p
                else:
                    r = (entry_p - exit_p) / entry_p
                all_rets_8.append(r)

    print(f"\nTotal entries: {len(entry_prices_1m)}")
    dirs = np.array(directions_list)
    print(f"  Long: {(dirs == 1).sum()}, Short: {(dirs == -1).sum()}")

    # Entry price comparison
    ep = np.array(entry_prices_1m)
    ce = np.array(close_at_entry)
    improvement = np.where(dirs == 1, (ce - ep) / ce, (ep - ce) / ce)
    print(f"\n--- Entry Price Improvement (1m vs 1H close) ---")
    print(f"  Mean: {improvement.mean()*100:+.3f}%")
    print(f"  Median: {np.median(improvement)*100:+.3f}%")

    # Raw forward returns (no costs)
    for label, rets in [("bar+1 (1h)", all_rets_1), ("bar+4 (4h)", all_rets_4), ("bar+8 (8h)", all_rets_8)]:
        r = np.array(rets)
        wr = (r > 0).mean()
        ev = r.mean()
        ev_net = ev - COST
        pf = r[r > 0].sum() / max(abs(r[r < 0].sum()), 1e-10)
        print(f"\n--- Raw Return at {label} (N={len(r)}) ---")
        print(f"  Mean (raw): {ev*100:+.3f}%")
        print(f"  Mean (net): {ev_net*100:+.3f}%")
        print(f"  WR: {wr*100:.1f}%")
        print(f"  PF: {pf:.2f}")
        print(f"  Median: {np.median(r)*100:+.3f}%")

    # Compare to research
    print(f"\n{'='*70}")
    print(f"  RESEARCH COMPARISON")
    print(f"{'='*70}")
    print(f"  Research 4h:   EV=+1.149%  WR=63.2%  PF=3.50  (from 1m cross, exit at cross+4h)")
    print(f"  Research 8h:   EV=+1.295%  WR=59.9%  PF=2.75  (from 1m cross, exit at cross+8h)")
    print(f"  Signal bar+4:  EV={np.array(all_rets_4).mean()*100:+.3f}%  WR={100*(np.array(all_rets_4)>0).mean():.1f}%  (from 1m cross, exit at bar+4 close)")
    print(f"  Signal bar+8:  EV={np.array(all_rets_8).mean()*100:+.3f}%  WR={100*(np.array(all_rets_8)>0).mean():.1f}%  (from 1m cross, exit at bar+8 close)")
    print(f"")
    print(f"  NOTE: Research exits at cross_time+Nh (exact minute)")
    print(f"        Signal exits at bar_start+N bars (end of hour)")
    print(f"        ~1h timing offset for 50% of trades (cross at minute 0)")


if __name__ == '__main__':
    main()
