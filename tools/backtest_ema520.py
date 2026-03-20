#!/usr/bin/env python3
"""
Standalone EMA 5/20 signal-exit backtester (minute-resolution).

This is the definitive backtester for the s99 strategy. V4 cannot
model signal-based exit, so this standalone simulation is authoritative.

Usage:
    python tools/backtest_ema520.py                    # Default: 4x cap=2.0, last 12mo
    python tools/backtest_ema520.py --lev 6 --cm 1.5   # Custom leverage/cap
    python tools/backtest_ema520.py --years 3           # 3-year backtest
    python tools/backtest_ema520.py --sweep             # Full parameter sweep
"""
import numpy as np
import pandas as pd
import argparse
import time

FUND_RATE_8H = 0.00005  # 0.005% per 8h net funding


def load_data():
    df_1m = pd.read_parquet('data/perp/1m_cache/ETH_1m.parquet')
    df_daily = df_1m.resample('1D').agg({
        'open': 'first', 'high': 'max', 'low': 'min',
        'close': 'last', 'volume': 'sum'
    }).dropna()
    df_1h = df_1m.resample('1h').agg({
        'open': 'first', 'high': 'max', 'low': 'min',
        'close': 'last', 'volume': 'sum'
    }).dropna()
    return df_1m, df_daily, df_1h


def build_daily_signal(df_daily, fast=5, slow=20):
    """Build daily-checked EMA crossover signal."""
    fast_ema = df_daily['close'].ewm(span=fast, adjust=False).mean().values
    slow_ema = df_daily['close'].ewm(span=slow, adjust=False).mean().values
    return {
        dt.date(): (1 if fast_ema[i] > slow_ema[i] else -1)
        for i, dt in enumerate(df_daily.index)
    }


def simulate(dsm, df_1m, df_1h, start_dt, end_dt, lev, cm,
             fee=0.0005, capital=200_000.0):
    """Run signal-exit simulation with minute-level liquidation checks."""
    d1h = df_1h.loc[start_dt:end_dt]
    if len(d1h) == 0:
        return None
    hm = {ts: i for i, ts in enumerate(d1h.index)}
    ds = df_1m.loc[start_dt:end_dt]
    if len(ds) == 0:
        return None

    mc = ds['close'].values
    md = ds.index
    ms = np.array([dsm.get(dt.date(), 0) for dt in md])
    mh = np.array([hm.get(dt.floor('h'), -1) for dt in md])

    equity = capital
    peak = capital
    max_dd = 0.0
    trades = wins = liqs = 0
    total_fees = 0.0
    MMR = 0.004
    LF = 0.005
    cap_pct = min(0.12 * cm, 0.30)

    in_pos = False
    entry_p = 0.0
    direction = 0
    margin = 0.0
    notional = 0.0
    fund_bars = 0

    for i in range(len(mc)):
        price = mc[i]
        sig = ms[i]
        hidx = mh[i]
        if sig == 0 or hidx < 0:
            continue

        if in_pos:
            fund_bars += 1
            if fund_bars % 480 == 0:
                fc = notional * FUND_RATE_8H
                equity -= fc
                total_fees += fc

            unrealized = direction * (price - entry_p) / entry_p * notional

            # Liquidation check (minute CLOSE)
            if (margin + unrealized) < (notional * MMR):
                equity -= margin + notional * (LF + fee)
                total_fees += notional * (LF + fee)
                trades += 1
                liqs += 1
                in_pos = False
            # Signal change exit
            elif sig != direction and md[i].minute == 0:
                exit_fee = notional * fee
                equity += unrealized - exit_fee
                total_fees += exit_fee
                trades += 1
                if unrealized > 0:
                    wins += 1
                in_pos = False

        if not in_pos and md[i].minute == 0:
            direction = sig
            margin = min(equity * cap_pct, equity * 0.30)
            if margin <= 0 or equity <= 0:
                continue
            notional = margin * lev
            entry_p = price
            fund_bars = 0
            entry_fee = notional * fee
            equity -= entry_fee
            total_fees += entry_fee
            in_pos = True

        # DD tracking
        if in_pos:
            curr_eq = equity + direction * (price - entry_p) / entry_p * notional
        else:
            curr_eq = equity
        peak = max(peak, curr_eq)
        dd = (curr_eq - peak) / max(peak, 1)
        max_dd = min(max_dd, dd)

    # Close final position
    if in_pos:
        final_pnl = direction * (mc[-1] - entry_p) / entry_p * notional
        equity += final_pnl - notional * fee
        trades += 1
        if final_pnl > 0:
            wins += 1

    ret = (equity - capital) / capital * 100
    wr = wins / max(trades, 1) * 100
    cal = ret / abs(max_dd * 100) if max_dd < 0 else float('inf')
    bh = (mc[-1] / mc[0] - 1) * 100

    return dict(
        ret=ret, dd=max_dd * 100, cal=cal,
        tr=trades, lq=liqs, wr=wr,
        fees=total_fees, bh=bh,
        final_equity=equity,
        start=md[0], end=md[-1],
    )


def main():
    parser = argparse.ArgumentParser(description='EMA 5/20 Signal-Exit Backtester')
    parser.add_argument('--lev', type=float, default=4.0, help='Leverage (default: 4)')
    parser.add_argument('--cm', type=float, default=2.0, help='Cap multiplier (default: 2.0)')
    parser.add_argument('--fee', type=float, default=0.0005, help='Fee per side (default: 0.05%%)')
    parser.add_argument('--capital', type=float, default=200_000, help='Starting capital')
    parser.add_argument('--years', type=float, default=1.0, help='Lookback in years')
    parser.add_argument('--fast', type=int, default=5, help='Fast EMA period')
    parser.add_argument('--slow', type=int, default=20, help='Slow EMA period')
    parser.add_argument('--sweep', action='store_true', help='Run full parameter sweep')
    args = parser.parse_args()

    print("Loading data...")
    df_1m, df_daily, df_1h = load_data()
    dsm = build_daily_signal(df_daily, args.fast, args.slow)

    end_dt = df_1m.index[-1]
    start_dt = end_dt - pd.Timedelta(days=int(args.years * 365))

    if args.sweep:
        print(f"\nEMA {args.fast}/{args.slow} Signal-Exit Sweep")
        print(f"Period: {start_dt.date()} to {end_dt.date()}")
        print(f"{'Config':<20} {'Ret%':>8} {'DD%':>7} {'Cal':>7} {'#T':>4} {'Lq':>3} {'WR%':>5}")
        print("-" * 60)
        for lev in [3, 4, 5, 6, 7, 8]:
            for cm in [0.5, 1.0, 1.5, 2.0, 2.5]:
                r = simulate(dsm, df_1m, df_1h, start_dt, end_dt,
                             lev, cm, args.fee, args.capital)
                if r:
                    mark = ""
                    if r['ret'] >= 300 and r['dd'] >= -20:
                        mark = " ***"
                    elif r['ret'] >= 300:
                        mark = " **"
                    elif r['dd'] >= -20:
                        mark = " *"
                    print(f"{lev}x cap={cm:<4} "
                          f"{r['ret']:>+8.1f} {r['dd']:>7.1f} {r['cal']:>7.1f} "
                          f"{r['tr']:>4} {r['lq']:>3} {r['wr']:>5.1f}{mark}")
    else:
        r = simulate(dsm, df_1m, df_1h, start_dt, end_dt,
                     args.lev, args.cm, args.fee, args.capital)
        if r:
            print(f"\nEMA {args.fast}/{args.slow} | {args.lev}x leverage | cap_mult={args.cm}")
            print(f"Period: {r['start'].date()} to {r['end'].date()}")
            print(f"Capital: ${args.capital:,.0f}")
            print(f"{'─' * 40}")
            print(f"Return:     {r['ret']:+.1f}%")
            print(f"Final Eq:   ${r['final_equity']:,.0f}")
            print(f"Max DD:     {r['dd']:.1f}%")
            print(f"Calmar:     {r['cal']:.1f}")
            print(f"Trades:     {r['tr']} ({r['lq']} liquidations)")
            print(f"Win Rate:   {r['wr']:.1f}%")
            print(f"Total Fees: ${r['fees']:,.0f}")
            print(f"B&H ETH:    {r['bh']:+.1f}%")


if __name__ == '__main__':
    t0 = time.time()
    main()
    print(f"\nElapsed: {time.time() - t0:.0f}s")
