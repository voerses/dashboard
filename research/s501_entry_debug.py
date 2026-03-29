"""Debug: check 1m entry resolution timestamp alignment for problematic trades.

For tokens where entry_limit_price >> 1H close, inspect the actual 1m bars
to understand why the first cross price is so far from the BB level.
"""
import sys, os
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from v4.config import PortfolioConfig, StrategySpec
from v4.portfolio_signals import _load_all_contexts
from v4.engine import _load_strategy_fn
from v4.minute_exits import MinuteExitCache


def main():
    tokens = ['FIL', 'FET', 'ADA', 'AAVE', 'BTC']
    config = PortfolioConfig(capital=100_000, exchange='binance', skip_walk_forward=True)
    spec = StrategySpec(strategy_id='s501', market='perp', strategy_type='portfolio',
                        max_positions=5, entry_resolution=1)

    strategy = _load_strategy_fn('s501')
    contexts, _, _ = _load_all_contexts(tokens, spec, config, months=12)
    results = strategy(contexts)

    cache = MinuteExitCache(resolution=1, max_tokens=20)

    # Examine first few entries per token
    for token in tokens:
        if token not in results:
            continue
        sr = results[token]
        if sr.entry_limit_price is None:
            continue

        ctx = contexts[token]
        if isinstance(ctx, tuple):
            ctx = ctx[1] if ctx[1] is not None else ctx[0]

        close_1h = ctx.ind_1h['close']
        ts_1h = ctx.idx_1h
        entry_bars = np.where(sr.entry_mask)[0]

        print(f"\n{'='*80}")
        print(f"  {token}: {len(entry_bars)} entries")
        print(f"{'='*80}")

        count = 0
        for bar in entry_bars[:10]:
            lp = sr.entry_limit_price[bar]  # Original BB level (before resolution)
            if np.isnan(lp):
                continue

            direction = int(sr.direction[bar])
            d_str = 'L' if direction == 1 else 'S'
            close_val = float(close_1h[bar])
            hour_ts = np.datetime64(ts_1h[bar], 'ms')

            # Get 1m bars
            minute_data = cache.get_minute_bars(token, hour_ts)

            print(f"\n  Bar {bar} ({d_str}) @ {ts_1h[bar]}")
            print(f"    BB level (limit):  ${lp:.4f}")
            print(f"    1H close:          ${close_val:.4f}")
            print(f"    Ratio limit/close: {lp/close_val:.3f}")

            if bar > 0:
                prev_close = float(close_1h[bar - 1])
                print(f"    Prev 1H close:     ${prev_close:.4f}")

            if minute_data is None:
                print(f"    1m data: NOT AVAILABLE")
                continue

            highs_1m, lows_1m, closes_1m = minute_data
            print(f"    1m bars: {len(closes_1m)} minutes")
            print(f"    1m range: ${closes_1m.min():.4f} - ${closes_1m.max():.4f}")
            print(f"    1m open(close[0]): ${closes_1m[0]:.4f}")
            print(f"    1m close(close[-1]): ${closes_1m[-1]:.4f}")
            print(f"    1m low: ${lows_1m.min():.4f}, high: ${highs_1m.max():.4f}")

            # Find first cross
            if direction == 1:
                cross_mask = closes_1m > lp
            else:
                cross_mask = closes_1m < lp

            cross_idx = np.where(cross_mask)[0]
            if len(cross_idx) > 0:
                ci = cross_idx[0]
                print(f"    First cross at minute {ci}: ${closes_1m[ci]:.4f}")
                # Show a few minutes around the cross
                start = max(0, ci - 2)
                end = min(len(closes_1m), ci + 5)
                for m in range(start, end):
                    marker = " <-- CROSS" if m == ci else ""
                    print(f"      min {m:2d}: close=${closes_1m[m]:.4f}  "
                          f"low=${lows_1m[m]:.4f}  high=${highs_1m[m]:.4f}{marker}")
            else:
                print(f"    NO CROSS found in this hour!")

            count += 1
            if count >= 3:
                break


if __name__ == '__main__':
    main()
