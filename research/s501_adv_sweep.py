"""Sweep ADV tiers: compute per-trade edge and estimated cost by ADV bucket."""
import sys, os
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'v4'))

from v4.config import PortfolioConfig, StrategySpec
from v4.portfolio_signals import precompute_portfolio_signals, _load_all_contexts, _resolve_minute_entries
from v4.universe import get_all_tradeable
from v4.engine import _load_strategy_fn


def main():
    tokens = get_all_tradeable()
    print(f"Universe: {len(tokens)} tokens")

    config = PortfolioConfig(capital=100_000, exchange='binance', skip_walk_forward=True)
    spec = StrategySpec(strategy_id='s501', market='perp', strategy_type='portfolio',
                        max_positions=5, entry_resolution=1)

    strategy = _load_strategy_fn('s501')
    contexts, _, _ = _load_all_contexts(tokens, spec, config, months=12)
    results = strategy(contexts)
    _resolve_minute_entries(results, contexts, spec)

    # Collect per-trade data with ADV at entry
    trades = []
    for token, sr in sorted(results.items()):
        if sr.entry_limit_price is None:
            continue
        ctx = contexts[token]
        if isinstance(ctx, tuple):
            ctx = ctx[1] if ctx[1] is not None else ctx[0]

        close = ctx.ind_1h['close']
        n = len(close)
        adv_arr = ctx.rolling_adv  # per-bar ADV

        for bar in np.where(sr.entry_mask)[0]:
            lp = sr.entry_limit_price[bar]
            if np.isnan(lp):
                continue
            d = int(sr.direction[bar])

            adv_val = float(adv_arr[bar]) if adv_arr is not None and bar < len(adv_arr) else np.nan

            if bar + 8 < n:
                exit_p = float(close[bar + 8])
                raw_ret = (exit_p - lp) / lp * d
                trades.append((token, adv_val, raw_ret, d))

    print(f"\nTotal entries with bar+8 forward return: {len(trades)}")

    # ADV buckets
    advs = np.array([t[1] for t in trades])
    rets = np.array([t[2] for t in trades])
    dirs = np.array([t[3] for t in trades])

    buckets = [
        ("All", 0, np.inf),
        ("ADV < $5M", 0, 5e6),
        ("ADV $5-20M", 5e6, 20e6),
        ("ADV $20-50M", 20e6, 50e6),
        ("ADV $50-100M", 50e6, 100e6),
        ("ADV $100-500M", 100e6, 500e6),
        ("ADV > $500M", 500e6, np.inf),
        ("--- FILTER: ADV > $50M", 50e6, np.inf),
        ("--- FILTER: ADV > $100M", 100e6, np.inf),
    ]

    print(f"\n{'Bucket':>25s}  {'N':>6s}  {'Tokens':>6s}  {'RawRet':>8s}  {'WR':>6s}  "
          f"{'MedRet':>8s}  {'EstCost':>8s}  {'NetRet':>8s}  {'Long':>5s}  {'Short':>5s}")
    print("-" * 110)

    for label, lo, hi in buckets:
        mask = (advs >= lo) & (advs < hi) & ~np.isnan(advs)
        if mask.sum() == 0:
            print(f"  {label:>25s}  — no trades")
            continue

        sub_rets = rets[mask]
        sub_dirs = dirs[mask]
        sub_advs = advs[mask]
        tokens_in = set(trades[i][0] for i in range(len(trades)) if mask[i])

        wr = (sub_rets > 0).mean()
        avg_adv = np.median(sub_advs)

        # Estimate cost at median ADV
        hourly_vol = avg_adv / 24
        notional = 2500  # rough avg
        participation = notional / max(hourly_vol, 1)
        slip_bps = 3.0 + 0.03 * np.sqrt(participation) * 10000
        rt_cost = 2 * (slip_bps / 10000 + 0.00045)
        net = sub_rets.mean() - rt_cost

        n_long = (sub_dirs == 1).sum()
        n_short = (sub_dirs == -1).sum()

        print(f"  {label:>25s}  {mask.sum():>6d}  {len(tokens_in):>6d}  "
              f"{sub_rets.mean()*100:>+7.3f}%  {wr*100:>5.1f}%  "
              f"{np.median(sub_rets)*100:>+7.3f}%  {rt_cost*100:>7.2f}%  "
              f"{net*100:>+7.3f}%  {n_long:>5d}  {n_short:>5d}")

    # Show top tokens by trade count and ADV
    print(f"\n{'='*70}")
    print(f"  TOP TOKENS BY TRADE COUNT (ADV > $50M)")
    print(f"{'='*70}")
    from collections import Counter, defaultdict
    token_stats = defaultdict(lambda: {'rets': [], 'advs': [], 'count': 0})
    for token, adv, ret, d in trades:
        if adv >= 50e6:
            token_stats[token]['rets'].append(ret)
            token_stats[token]['advs'].append(adv)
            token_stats[token]['count'] += 1

    sorted_tokens = sorted(token_stats.items(), key=lambda x: x[1]['count'], reverse=True)
    print(f"  {'Token':>12s}  {'N':>5s}  {'AvgRet':>8s}  {'WR':>6s}  {'MedADV':>10s}")
    for token, stats in sorted_tokens[:20]:
        r = np.array(stats['rets'])
        a = np.median(stats['advs'])
        print(f"  {token:>12s}  {stats['count']:>5d}  {r.mean()*100:>+7.3f}%  "
              f"{(r>0).mean()*100:>5.1f}%  ${a/1e6:>8.1f}M")


if __name__ == '__main__':
    main()
