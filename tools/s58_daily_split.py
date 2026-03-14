#!/usr/bin/env python3
"""Analyze daily spot/perp capital split and rebalancing needs for s58."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd
from collections import defaultdict
from datetime import datetime, timedelta

from strategies.s58_multi_strategy_portfolio import (
    _get_momentum_trades, _get_carry_trades, _load_v3
)
from v4.universe import get_fee_rate, get_all_tradeable


def run_and_analyze(capital=200_000, year=2025, exchange='binance'):
    portfolio_mod = _load_v3('portfolio')
    universe_mod = _load_v3('universe')
    simulate_portfolio = portfolio_mod.simulate_portfolio

    perp_tokens = universe_mod.get_all_tradeable('perp')
    print(f"\nS58 Daily Spot/Perp Split Analysis — {year}")
    print(f"  Capital: ${capital:,.0f}")

    import time
    t0 = time.time()
    print(f"  Extracting momentum trades (s56)...", end='', flush=True)
    mom_trades = _get_momentum_trades(perp_tokens, 'data', capital, exchange)
    print(f" {sum(len(t) for t in mom_trades.values())} trades ({time.time()-t0:.0f}s)")

    t1 = time.time()
    print(f"  Extracting carry trades (s57)...", end='', flush=True)
    carry_trades = _get_carry_trades(perp_tokens, 'data', capital, exchange)
    print(f" {sum(len(t) for t in carry_trades.values())} trades ({time.time()-t1:.0f}s)")

    # Pool + filter to target year
    all_token_trades = {}
    for key, trades in mom_trades.items():
        token = key.replace('_mom', '')
        year_trades = [t for t in trades if t['entry_time'].year == year]
        if year_trades:
            all_token_trades.setdefault(token, []).extend(year_trades)
    for key, trades in carry_trades.items():
        token = key.replace('_carry', '')
        year_trades = [t for t in trades if t['entry_time'].year == year]
        if year_trades:
            all_token_trades.setdefault(token, []).extend(year_trades)

    total = sum(len(t) for t in all_token_trades.values())
    print(f"  Filtered: {total} trades in {year}")

    # Run portfolio simulation
    fee_rate = get_fee_rate(exchange, 'perp', 'taker')
    portfolio_eq, accepted, skipped, sim_info = simulate_portfolio(
        all_token_trades, capital, max_token_pct=0.15,
        min_position_usd=1000, fee_rate=fee_rate)
    print(f"  Accepted: {len(accepted)}, Skipped: {len(skipped)}")

    # --- Build daily snapshot of spot vs perp capital ---
    # Each accepted trade has:
    #   - strategy: 'momentum' (perp-only) or 'carry' (spot+perp combined)
    #   - entry_time, exit_time
    #   - portfolio_position_usd (or position_usd)
    #   - leg: 1 (spot/primary) or 2 (perp/secondary) for combined trades
    #     But s58 pools trades, so we need to determine from strategy type

    # For carry trades (s57 combined): capital is split ~50/50 spot + perp
    # For momentum trades (s56 perp-only): 100% perp
    # The leg info may be in the trade dict

    events = []  # (datetime, spot_delta, perp_delta)
    for t in accepted:
        entry = t.get('entry_time')
        exit_t = t.get('exit_time')
        pos_usd = t.get('portfolio_position_usd', t.get('position_usd', 0))
        strategy = t.get('strategy', '')
        leg = t.get('leg', 0)

        if not entry or not exit_t or pos_usd <= 0:
            continue

        if strategy == 'momentum':
            # Perp-only
            events.append((entry, 0, pos_usd))      # entry: add perp
            events.append((exit_t, 0, -pos_usd))    # exit: remove perp
        elif strategy == 'carry':
            # Combined: check leg
            if leg == 1:  # spot leg
                events.append((entry, pos_usd, 0))
                events.append((exit_t, -pos_usd, 0))
            elif leg == 2:  # perp leg
                events.append((entry, 0, pos_usd))
                events.append((exit_t, 0, -pos_usd))
            else:
                # No leg info — assume 50/50 split (carry default)
                spot_half = pos_usd * 0.5
                perp_half = pos_usd * 0.5
                events.append((entry, spot_half, perp_half))
                events.append((exit_t, -spot_half, -perp_half))

    events.sort(key=lambda x: x[0])

    # Build daily snapshots
    daily = {}
    spot_locked = 0
    perp_locked = 0

    for dt, s_delta, p_delta in events:
        spot_locked += s_delta
        perp_locked += p_delta
        day = dt.normalize() if hasattr(dt, 'normalize') else pd.Timestamp(dt).normalize()
        daily[day] = (max(spot_locked, 0), max(perp_locked, 0))

    if not daily:
        print("  No daily data to analyze.")
        return

    # Fill gaps (days with no events)
    days_sorted = sorted(daily.keys())
    all_days = pd.date_range(days_sorted[0], days_sorted[-1], freq='D')
    daily_filled = {}
    last_spot, last_perp = 0, 0
    for d in all_days:
        if d in daily:
            last_spot, last_perp = daily[d]
        daily_filled[d] = (last_spot, last_perp)

    # Compute splits and rebalancing needs
    days = sorted(daily_filled.keys())
    spots = [daily_filled[d][0] for d in days]
    perps = [daily_filled[d][1] for d in days]
    totals = [s + p for s, p in zip(spots, perps)]
    spot_pcts = [s / t * 100 if t > 0 else 0 for s, t in zip(spots, totals)]
    perp_pcts = [p / t * 100 if t > 0 else 0 for p, t in zip(perps, totals)]

    # Rebalancing: change in spot% from previous day
    rebal_pcts = [0]
    for i in range(1, len(spot_pcts)):
        rebal_pcts.append(abs(spot_pcts[i] - spot_pcts[i-1]))

    # --- Monthly summary ---
    monthly = defaultdict(lambda: {
        'spot_avg': [], 'perp_avg': [], 'total_avg': [],
        'spot_pct_avg': [], 'rebal_avg': [], 'rebal_max': 0,
        'rebal_gt_5': 0, 'rebal_gt_10': 0, 'days': 0,
    })

    for i, d in enumerate(days):
        m = d.month
        mo = monthly[m]
        mo['spot_avg'].append(spots[i])
        mo['perp_avg'].append(perps[i])
        mo['total_avg'].append(totals[i])
        mo['spot_pct_avg'].append(spot_pcts[i])
        mo['rebal_avg'].append(rebal_pcts[i])
        mo['days'] += 1
        if rebal_pcts[i] > mo['rebal_max']:
            mo['rebal_max'] = rebal_pcts[i]
        if rebal_pcts[i] > 5:
            mo['rebal_gt_5'] += 1
        if rebal_pcts[i] > 10:
            mo['rebal_gt_10'] += 1

    month_names = ['', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                   'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

    print(f"\n{'='*95}")
    print(f"DAILY SPOT/PERP CAPITAL SPLIT — {year}")
    print(f"{'='*95}")

    print(f"\n  Monthly Averages:")
    print(f"  {'Month':<6} {'Avg Spot':>12} {'Avg Perp':>12} {'Avg Total':>12} "
          f"{'Spot%':>7} {'Perp%':>7} {'Avg Rebal':>10} {'Max Rebal':>10} "
          f"{'Days>5%':>8} {'Days>10%':>9}")
    print(f"  {'─'*91}")

    for m in sorted(monthly.keys()):
        mo = monthly[m]
        avg_s = np.mean(mo['spot_avg'])
        avg_p = np.mean(mo['perp_avg'])
        avg_t = np.mean(mo['total_avg'])
        avg_sp = np.mean(mo['spot_pct_avg'])
        avg_r = np.mean(mo['rebal_avg'])
        print(f"  {month_names[m]:<6} ${avg_s:>11,.0f} ${avg_p:>11,.0f} ${avg_t:>11,.0f} "
              f"{avg_sp:>6.1f}% {100-avg_sp:>6.1f}% {avg_r:>9.1f}% {mo['rebal_max']:>9.1f}% "
              f"{mo['rebal_gt_5']:>8} {mo['rebal_gt_10']:>9}")

    # Overall stats
    avg_spot_pct = np.mean(spot_pcts)
    avg_rebal = np.mean(rebal_pcts)
    max_rebal = max(rebal_pcts)
    days_gt_5 = sum(1 for r in rebal_pcts if r > 5)
    days_gt_10 = sum(1 for r in rebal_pcts if r > 10)
    days_gt_20 = sum(1 for r in rebal_pcts if r > 20)

    print(f"  {'─'*91}")
    print(f"\n  Overall Split: Spot {avg_spot_pct:.1f}% / Perp {100-avg_spot_pct:.1f}%")
    print(f"  Average Daily Rebalancing: {avg_rebal:.1f}%")
    print(f"  Max Single-Day Rebalancing: {max_rebal:.1f}%")
    print(f"  Days Needing >5% Rebalance: {days_gt_5} ({days_gt_5/len(days)*100:.0f}% of days)")
    print(f"  Days Needing >10% Rebalance: {days_gt_10} ({days_gt_10/len(days)*100:.0f}% of days)")
    print(f"  Days Needing >20% Rebalance: {days_gt_20} ({days_gt_20/len(days)*100:.0f}% of days)")

    # --- Weekly detail for first 2 months ---
    print(f"\n  Weekly Detail (first 8 weeks):")
    print(f"  {'Week':<14} {'Spot ($)':>12} {'Perp ($)':>12} {'Total':>12} "
          f"{'Spot%':>7} {'Avg Rebal':>10}")
    print(f"  {'─'*69}")

    week_start = days[0]
    week_num = 0
    while week_start <= days[-1] and week_num < 8:
        week_end = week_start + timedelta(days=7)
        week_days = [i for i, d in enumerate(days) if week_start <= d < week_end]
        if week_days:
            ws = np.mean([spots[i] for i in week_days])
            wp = np.mean([perps[i] for i in week_days])
            wt = ws + wp
            wsp = ws / wt * 100 if wt > 0 else 0
            wr = np.mean([rebal_pcts[i] for i in week_days])
            label = f"{week_start.strftime('%b %d')}-{(week_end - timedelta(1)).strftime('%d')}"
            print(f"  {label:<14} ${ws:>11,.0f} ${wp:>11,.0f} ${wt:>11,.0f} "
                  f"{wsp:>6.1f}% {wr:>9.1f}%")
        week_start = week_end
        week_num += 1

    # --- Strategy composition (how many positions of each type per day) ---
    print(f"\n  Average Concurrent Positions by Type:")
    pos_events = []
    for t in accepted:
        entry = t.get('entry_time')
        exit_t = t.get('exit_time')
        strategy = t.get('strategy', '')
        if entry and exit_t:
            pos_events.append((entry, 1, strategy))
            pos_events.append((exit_t, -1, strategy))
    pos_events.sort(key=lambda x: x[0])

    daily_counts = {}
    mom_count = 0
    carry_count = 0
    for dt, delta, strat in pos_events:
        day = dt.normalize() if hasattr(dt, 'normalize') else pd.Timestamp(dt).normalize()
        if strat == 'momentum':
            mom_count += delta
        else:
            carry_count += delta
        daily_counts[day] = (max(mom_count, 0), max(carry_count, 0))

    # Fill and average by month
    last_mc, last_cc = 0, 0
    monthly_pos = defaultdict(lambda: {'mom': [], 'carry': []})
    for d in all_days:
        if d in daily_counts:
            last_mc, last_cc = daily_counts[d]
        monthly_pos[d.month]['mom'].append(last_mc)
        monthly_pos[d.month]['carry'].append(last_cc)

    print(f"  {'Month':<6} {'Momentum':>10} {'Carry':>10} {'Total':>10}")
    print(f"  {'─'*38}")
    for m in sorted(monthly_pos.keys()):
        avg_m = np.mean(monthly_pos[m]['mom'])
        avg_c = np.mean(monthly_pos[m]['carry'])
        print(f"  {month_names[m]:<6} {avg_m:>10.1f} {avg_c:>10.1f} {avg_m+avg_c:>10.1f}")

    print(f"\n{'='*95}")


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--year', type=int, default=2025)
    parser.add_argument('--capital', type=float, default=200_000)
    args = parser.parse_args()

    run_and_analyze(capital=args.capital, year=args.year)
