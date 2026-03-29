"""
s501 1m Entry Edge Analysis (v2 — correct timestamp alignment)
===============================================================

Uses ctx.idx_1h (DatetimeIndex) for direct timestamp mapping.
No parquet bar-offset heuristics.
"""

import sys
import os
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'v4'))

from v4.config import PortfolioConfig, StrategySpec
from v4.universe import get_all_tradeable
from v4.portfolio_signals import _load_all_contexts

# Cost model
FEE_RATE = 0.00045
SLIPPAGE_BPS = 3.0
TOTAL_COST = 2 * (FEE_RATE + SLIPPAGE_BPS / 10000)

HORIZONS_MIN = [15, 60, 240, 480, 1440]
HORIZON_LABELS = ['15m', '1h', '4h', '8h', '24h']


def main():
    print("=" * 70)
    print("  s501 1m Entry Edge Analysis (v2)")
    print("=" * 70)

    all_tokens = get_all_tradeable()
    print(f"\nUniverse: {len(all_tokens)} tokens")

    from strategies.s501_r172_v4_portfolio import strategy

    config = PortfolioConfig(capital=100_000, exchange='binance', skip_walk_forward=True)
    spec = StrategySpec(strategy_id='s501', market='perp', strategy_type='portfolio', max_positions=5)
    contexts, cutoff, anchor = _load_all_contexts(all_tokens, spec, config, months=12)
    print(f"Built contexts: {len(contexts)} tokens")

    results = strategy(contexts)
    print(f"Strategy results: {len(results)} tokens with signals")

    all_trades = []
    stats = {'no_1m': 0, 'no_cross': 0, 'processed': 0, 'outside_1m': 0}

    for token_idx, (token, sr) in enumerate(sorted(results.items())):
        ctx = contexts[token]
        if isinstance(ctx, tuple):
            ctx = ctx[1] if ctx[1] is not None else ctx[0]

        close_1h = ctx.ind_1h['close']
        n_1h = len(close_1h)
        n_4h = len(ctx.ind_4h['close'])
        ts_1h = ctx.idx_1h  # DatetimeIndex — direct timestamps!

        # Reconstruct prior BB levels
        bb_upper_4h = ctx.ind_4h['bb_upper']
        bb_lower_4h = ctx.ind_4h['bb_lower']
        prior_bb_upper_4h = np.empty(n_4h, dtype=np.float64)
        prior_bb_upper_4h[0] = np.nan
        prior_bb_upper_4h[1:] = bb_upper_4h[:-1]
        prior_bb_lower_4h = np.empty(n_4h, dtype=np.float64)
        prior_bb_lower_4h[0] = np.nan
        prior_bb_lower_4h[1:] = bb_lower_4h[:-1]
        prior_bb_upper_1h = ctx.align_4h_to_1h(prior_bb_upper_4h)
        prior_bb_lower_1h = ctx.align_4h_to_1h(prior_bb_lower_4h)

        # Get breakout entry bars
        entry_bars = np.where(sr.entry_mask)[0]
        brk_bars = []
        for bar in entry_bars:
            sm = float(sr.size_multiplier[bar]) if sr.size_multiplier is not None else 1.0
            if sm < 0.01:
                continue
            d = int(sr.direction[bar])
            if d == 1:
                bb = float(prior_bb_upper_1h[bar])
            else:
                bb = float(prior_bb_lower_1h[bar])
            if np.isnan(bb):
                continue
            brk_bars.append((bar, d, bb))

        if not brk_bars:
            continue

        # Load 1m data
        path_1m = f'data/perp/1m_cache/{token}_1m.parquet'
        if not os.path.exists(path_1m):
            stats['no_1m'] += len(brk_bars)
            continue
        df_1m = pd.read_parquet(path_1m)
        ts_1m_start = df_1m.index[0]
        ts_1m_end = df_1m.index[-1]

        for bar, direction, bb_level in brk_bars:
            bar_ts = ts_1h[bar]

            # Check if bar is in 1m data range
            if bar_ts < ts_1m_start or bar_ts > ts_1m_end:
                stats['outside_1m'] += 1
                continue

            bar_end = bar_ts + pd.Timedelta(hours=1)
            close_val = float(close_1h[bar])

            # Find 1m bars in this hour
            mask = (df_1m.index >= bar_ts) & (df_1m.index < bar_end)
            window_1m = df_1m.loc[mask]

            if len(window_1m) == 0:
                stats['no_cross'] += 1
                continue

            # Find first 1m cross
            if direction == 1:
                cross_mask = window_1m['close'].values > bb_level
            else:
                cross_mask = window_1m['close'].values < bb_level

            cross_indices = np.where(cross_mask)[0]
            if len(cross_indices) == 0:
                stats['no_cross'] += 1
                continue

            first = cross_indices[0]
            cross_price = float(window_1m['close'].iloc[first])
            cross_time = window_1m.index[first]
            minutes_offset = (cross_time - bar_ts).total_seconds() / 60.0

            # Price improvement: 1m cross vs 1H close
            if direction == 1:
                improvement = (close_val - cross_price) / close_val  # positive = buying lower
            else:
                improvement = (cross_price - close_val) / close_val  # positive = selling higher

            trade = {
                'token': token,
                'direction': direction,
                'bb_level': bb_level,
                'close_1h': close_val,
                'cross_price_1m': cross_price,
                'minutes_offset': minutes_offset,
                'improvement': improvement,
            }

            # Forward returns
            for horizon, label in zip(HORIZONS_MIN, HORIZON_LABELS):
                # From 1m cross
                target = cross_time + pd.Timedelta(minutes=horizon)
                idx = df_1m.index.searchsorted(target)
                if idx < len(df_1m):
                    fwd = float(df_1m['close'].iloc[idx])
                    if direction == 1:
                        trade[f'ret_1m_{label}'] = (fwd - cross_price) / cross_price
                    else:
                        trade[f'ret_1m_{label}'] = (cross_price - fwd) / cross_price

                # From 1H close (bar_end = start of next hour)
                target_1h = bar_end + pd.Timedelta(minutes=horizon)
                idx_1h = df_1m.index.searchsorted(target_1h)
                if idx_1h < len(df_1m):
                    fwd = float(df_1m['close'].iloc[idx_1h])
                    if direction == 1:
                        trade[f'ret_1h_{label}'] = (fwd - close_val) / close_val
                    else:
                        trade[f'ret_1h_{label}'] = (close_val - fwd) / close_val

            # Max excursion (4h from 1m entry)
            exc_end = cross_time + pd.Timedelta(hours=4)
            exc_mask = (df_1m.index > cross_time) & (df_1m.index <= exc_end)
            exc = df_1m.loc[exc_mask]
            if len(exc) > 0:
                if direction == 1:
                    trade['mfe_4h'] = (exc['high'].max() - cross_price) / cross_price
                    trade['mae_4h'] = (cross_price - exc['low'].min()) / cross_price
                else:
                    trade['mfe_4h'] = (cross_price - exc['low'].min()) / cross_price
                    trade['mae_4h'] = (exc['high'].max() - cross_price) / cross_price

            all_trades.append(trade)
            stats['processed'] += 1

        del df_1m

        if (token_idx + 1) % 20 == 0:
            print(f"  {token_idx+1}/{len(results)} tokens, {stats['processed']} trades...")

    print(f"\n  Total: {stats['processed']} trades")
    print(f"  Skipped: {stats['no_1m']} no 1m file, {stats['outside_1m']} outside 1m range, {stats['no_cross']} no cross")

    if stats['processed'] == 0:
        return

    df = pd.DataFrame(all_trades)

    # ── RESULTS ──
    print("\n" + "=" * 70)
    print("  RESULTS")
    print("=" * 70)

    n_long = (df['direction'] == 1).sum()
    n_short = (df['direction'] == -1).sum()
    print(f"\nTrades: {len(df)} ({n_long} long, {n_short} short)")

    print(f"\n--- Entry Timing ---")
    print(f"  Median minutes: {df['minutes_offset'].median():.1f}")
    print(f"  Mean minutes:   {df['minutes_offset'].mean():.1f}")
    for m in [0, 1, 5, 15, 30]:
        n = (df['minutes_offset'] <= m).sum()
        print(f"  <= {m:2d}min: {n:4d} ({100*n/len(df):.1f}%)")

    print(f"\n--- Price Improvement (1m entry vs 1H close) ---")
    print(f"  Mean:   {df['improvement'].mean()*100:+.3f}%")
    print(f"  Median: {df['improvement'].median()*100:+.3f}%")
    print(f"  > 0%:   {(df['improvement'] > 0).sum()} ({100*(df['improvement'] > 0).mean():.1f}%)")
    print(f"  > 0.5%: {(df['improvement'] > 0.005).sum()} ({100*(df['improvement'] > 0.005).mean():.1f}%)")
    print(f"  > 1%:   {(df['improvement'] > 0.01).sum()} ({100*(df['improvement'] > 0.01).mean():.1f}%)")

    # Forward returns
    print(f"\n--- Forward Returns: 1m Entry vs 1H Close ---")
    print(f"  (Round-trip cost: {TOTAL_COST*100:.3f}%)")

    for label in HORIZON_LABELS:
        col_1m = f'ret_1m_{label}'
        col_1h = f'ret_1h_{label}'
        if col_1m not in df.columns:
            continue
        valid = df[[col_1m, col_1h]].dropna()
        if len(valid) == 0:
            continue

        mean_1m = valid[col_1m].mean()
        mean_1h = valid[col_1h].mean()
        ev_1m = mean_1m - TOTAL_COST
        ev_1h = mean_1h - TOTAL_COST
        wr_1m = (valid[col_1m] > TOTAL_COST).mean()
        wr_1h = (valid[col_1h] > TOTAL_COST).mean()
        pf_1m = valid[col_1m][valid[col_1m] > 0].sum() / max(abs(valid[col_1m][valid[col_1m] < 0].sum()), 1e-10)
        pf_1h = valid[col_1h][valid[col_1h] > 0].sum() / max(abs(valid[col_1h][valid[col_1h] < 0].sum()), 1e-10)

        print(f"\n  {label} (N={len(valid)}):")
        print(f"    1m entry: EV={ev_1m*100:+.3f}%  WR={wr_1m*100:.1f}%  PF={pf_1m:.2f}  raw={mean_1m*100:+.3f}%")
        print(f"    1H close: EV={ev_1h*100:+.3f}%  WR={wr_1h*100:.1f}%  PF={pf_1h:.2f}  raw={mean_1h*100:+.3f}%")
        print(f"    Delta:    {(ev_1m - ev_1h)*100:+.3f}% EV from earlier entry")

    # Max excursion
    if 'mfe_4h' in df.columns:
        mfe = df['mfe_4h'].dropna()
        mae = df['mae_4h'].dropna()
        print(f"\n--- Max Excursion (4h from 1m entry) ---")
        print(f"  MFE: {mfe.mean()*100:.2f}% mean, {mfe.median()*100:.2f}% median")
        print(f"  MAE: {mae.mean()*100:.2f}% mean, {mae.median()*100:.2f}% median")
        print(f"  MFE/MAE: {mfe.mean()/max(mae.mean(),1e-10):.2f}")

    # By direction
    for dv, dn in [(1, 'LONG'), (-1, 'SHORT')]:
        sub = df[df['direction'] == dv]
        if len(sub) == 0:
            continue
        print(f"\n--- {dn} ({len(sub)} trades) ---")
        print(f"  Improvement: {sub['improvement'].mean()*100:+.3f}% mean")
        print(f"  Minutes:     {sub['minutes_offset'].median():.1f} median")
        for label in HORIZON_LABELS:
            col = f'ret_1m_{label}'
            if col in sub.columns:
                v = sub[col].dropna()
                if len(v) > 0:
                    ev = v.mean() - TOTAL_COST
                    wr = (v > TOTAL_COST).mean()
                    pf = v[v > 0].sum() / max(abs(v[v < 0].sum()), 1e-10)
                    print(f"  {label}: EV={ev*100:+.3f}%  WR={wr*100:.1f}%  PF={pf:.2f}")

    # Aggregate PnL
    print(f"\n--- Aggregate PnL ($1000/trade, 2.5x leverage) ---")
    for label in HORIZON_LABELS:
        col_1m = f'ret_1m_{label}'
        col_1h = f'ret_1h_{label}'
        if col_1m not in df.columns:
            continue
        valid = df[[col_1m, col_1h]].dropna()
        if len(valid) == 0:
            continue
        lev = 2.5
        pnl_1m = (valid[col_1m] * 1000 * lev).sum() - TOTAL_COST * 1000 * lev * len(valid)
        pnl_1h = (valid[col_1h] * 1000 * lev).sum() - TOTAL_COST * 1000 * lev * len(valid)
        print(f"  {label}: 1m=${pnl_1m:+,.0f}  1H=${pnl_1h:+,.0f}  Delta=${pnl_1m-pnl_1h:+,.0f}")

    # Early vs late crosses
    print(f"\n--- Early crosses (<=5 min) ---")
    early = df[df['minutes_offset'] <= 5]
    if len(early) > 0:
        print(f"  Count: {len(early)} ({100*len(early)/len(df):.1f}%)")
        print(f"  Improvement: {early['improvement'].mean()*100:+.3f}%")
        for label in ['1h', '4h', '24h']:
            col = f'ret_1m_{label}'
            if col in early.columns:
                v = early[col].dropna()
                if len(v) > 0:
                    ev = v.mean() - TOTAL_COST
                    wr = (v > TOTAL_COST).mean()
                    pf = v[v > 0].sum() / max(abs(v[v < 0].sum()), 1e-10)
                    print(f"    {label}: EV={ev*100:+.3f}%  WR={wr*100:.1f}%  PF={pf:.2f}  N={len(v)}")

    print(f"\n--- Late crosses (>15 min) ---")
    late = df[df['minutes_offset'] > 15]
    if len(late) > 0:
        print(f"  Count: {len(late)} ({100*len(late)/len(df):.1f}%)")
        print(f"  Improvement: {late['improvement'].mean()*100:+.3f}%")
        for label in ['1h', '4h', '24h']:
            col = f'ret_1m_{label}'
            if col in late.columns:
                v = late[col].dropna()
                if len(v) > 0:
                    ev = v.mean() - TOTAL_COST
                    wr = (v > TOTAL_COST).mean()
                    pf = v[v > 0].sum() / max(abs(v[v < 0].sum()), 1e-10)
                    print(f"    {label}: EV={ev*100:+.3f}%  WR={wr*100:.1f}%  PF={pf:.2f}  N={len(v)}")

    # VERDICT
    print(f"\n{'='*70}")
    print("  VERDICT")
    print(f"{'='*70}")

    col_4h = 'ret_1m_4h'
    if col_4h in df.columns:
        valid = df[col_4h].dropna()
        ev = valid.mean() - TOTAL_COST
        wr = (valid > TOTAL_COST).mean()
        pf = valid[valid > 0].sum() / max(abs(valid[valid < 0].sum()), 1e-10)

        col_1h_4h = 'ret_1h_4h'
        valid_1h = df[col_1h_4h].dropna() if col_1h_4h in df.columns else pd.Series()
        ev_1h = valid_1h.mean() - TOTAL_COST if len(valid_1h) > 0 else 0

        print(f"  4h from 1m entry: EV={ev*100:+.3f}%  WR={wr*100:.1f}%  PF={pf:.2f}  N={len(valid)}")
        print(f"  4h from 1H close: EV={ev_1h*100:+.3f}%")
        print(f"  Delta: {(ev - ev_1h)*100:+.3f}%")

        if ev > 0 and pf > 1.2:
            print(f"\n  ==> POSITIVE EDGE with 1m entry!")
        elif ev > 0:
            print(f"\n  ==> MARGINAL: positive but thin")
        else:
            print(f"\n  ==> NO EDGE at 4h horizon")

    return df


if __name__ == '__main__':
    df = main()
