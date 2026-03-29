"""s501 Trade Gap Analysis — Quantify per-trade edge loss from signal to simulator.

Research shows +1.43% raw return per trade at bar+8, WR=62.1%.
Backtest shows PF=1.23, WR=39%. This script maps each simulator trade back
to the signal arrays and breaks down exactly where the edge is lost.

Gap components:
  1. Entry slippage: sim entry_price vs signal entry_limit_price
  2. Exit slippage: sim exit_price vs signal close[bar+8]
  3. Fees: entry_fee + exit_fee per trade
  4. Funding: cumulative funding cost per trade
  5. Selection bias: does the simulator pick worse-than-average signals?
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
os.chdir('/workspace/crypto_backtest')

import numpy as np
import pandas as pd
from collections import Counter

from v4.config import PortfolioConfig, StrategySpec
from v4.portfolio_signals import precompute_portfolio_signals
from v4.simulator import simulate_portfolio, build_unified_index
from v4.universe import get_all_tradeable


def main():
    # ── Configuration ─────────────────────────────────────────────────
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
        max_positions=5,
        entry_resolution=1,
    )

    tokens = get_all_tradeable('perp')
    print(f"Universe: {len(tokens)} tokens")

    # ── Step 1: Run full pipeline ─────────────────────────────────────
    print("\n=== Step 1: Precompute signals ===")
    signals = precompute_portfolio_signals(spec, tokens, config, months=12)
    print(f"Tokens with signals: {len(signals)}")

    # Total entries available in signal arrays
    total_signal_entries = sum(int(sig.entry_mask.sum()) for sig in signals.values())
    print(f"Total signal entries across all tokens: {total_signal_entries}")

    print("\n=== Step 2: Simulate portfolio ===")
    all_signals = {'s501': signals}
    state = simulate_portfolio(all_signals, {'s501': spec}, config)

    trades = state.position_manager.closed_trades
    n_trades = len(trades)
    print(f"Closed trades: {n_trades}")
    print(f"Final equity: ${state.portfolio_equity:,.0f}")
    print(f"Total fees: ${state.total_fees:,.0f}")
    print(f"Total funding: ${state.total_funding:,.0f}")

    if n_trades == 0:
        print("No trades to analyze.")
        return

    # ── Step 3: Build bar maps for signal-to-trade mapping ────────────
    print("\n=== Step 3: Map simulator trades back to signal arrays ===")
    unified_ts, bar_maps = build_unified_index(all_signals)

    # Collect per-trade gap analysis
    mapped_trades = []
    unmapped = 0

    for trade in trades:
        token = trade.token
        sig = signals.get(token)
        if sig is None:
            unmapped += 1
            continue

        bm = bar_maps.get(token)
        if bm is None:
            unmapped += 1
            continue

        # Map global entry_bar to local bar
        entry_global = trade.entry_bar
        if entry_global < 0 or entry_global >= len(bm):
            unmapped += 1
            continue

        local_bar = int(bm[entry_global])
        if local_bar < 0 or local_bar >= sig.n_bars:
            unmapped += 1
            continue

        # Signal entry limit price
        sig_limit = np.nan
        if sig.entry_limit_price is not None and local_bar < len(sig.entry_limit_price):
            sig_limit = float(sig.entry_limit_price[local_bar])

        # Signal close at entry bar
        sig_close_entry = float(sig.close[local_bar])

        # Signal close at bar+8 (the "research" exit)
        hold_bars = sig.max_hold  # should be 8 for s501
        exit_local = local_bar + hold_bars
        if exit_local >= sig.n_bars:
            # Cannot compute research return, skip
            unmapped += 1
            continue

        sig_close_exit = float(sig.close[exit_local])

        direction = trade.direction

        # Research raw return: from sig_limit to close[bar+8]
        if not np.isnan(sig_limit) and sig_limit > 0:
            if direction == 1:
                research_ret = (sig_close_exit - sig_limit) / sig_limit
            else:
                research_ret = (sig_limit - sig_close_exit) / sig_limit
        else:
            # Fallback: use close at entry
            if direction == 1:
                research_ret = (sig_close_exit - sig_close_entry) / sig_close_entry
            else:
                research_ret = (sig_close_entry - sig_close_exit) / sig_close_entry

        # Simulator raw price return (before fees/funding, from actual entry/exit prices)
        if direction == 1:
            sim_price_ret = (trade.exit_price - trade.entry_price) / trade.entry_price
        else:
            sim_price_ret = (trade.entry_price - trade.exit_price) / trade.entry_price

        # Simulator net return (includes fees, funding, slippage)
        sim_net_ret = trade.pnl / max(trade.margin_usd, 1.0)

        # Gap decomposition
        # Entry slippage: how much worse is the sim entry vs the signal limit?
        entry_slip = 0.0
        if not np.isnan(sig_limit) and sig_limit > 0:
            entry_slip = (trade.entry_price - sig_limit) / sig_limit * direction
            # positive means sim entered at a worse price

        # Exit difference: sim exit vs research exit (close[bar+8])
        if direction == 1:
            exit_diff = (trade.exit_price - sig_close_exit) / sig_close_exit
        else:
            exit_diff = (sig_close_exit - trade.exit_price) / sig_close_exit
        # negative means sim exited at a worse price (for the direction)

        # Fee impact (as fraction of entry notional)
        notional = abs(trade.margin_usd)
        fee_impact = (trade.entry_fee + trade.exit_fee) / max(notional, 1.0)

        # Funding impact
        funding_impact = trade.funding_cost / max(notional, 1.0)

        # Research WR check
        research_win = research_ret > 0

        # Sim WR check (using PnL)
        sim_win = trade.pnl > 0

        mapped_trades.append({
            'token': token,
            'direction': direction,
            'hold_bars': trade.hold_bars,
            'exit_reason': trade.exit_reason,
            'sig_limit': sig_limit,
            'sig_close_entry': sig_close_entry,
            'sig_close_exit': sig_close_exit,
            'sim_entry': trade.entry_price,
            'sim_exit': trade.exit_price,
            'research_ret': research_ret,
            'sim_price_ret': sim_price_ret,
            'sim_net_ret': sim_net_ret,
            'entry_slip': entry_slip,
            'exit_diff': exit_diff,
            'fee_impact': fee_impact,
            'funding_impact': funding_impact,
            'entry_fee': trade.entry_fee,
            'exit_fee': trade.exit_fee,
            'funding_cost': trade.funding_cost,
            'margin_usd': trade.margin_usd,
            'pnl': trade.pnl,
            'research_win': research_win,
            'sim_win': sim_win,
            'local_bar': local_bar,
        })

    print(f"Mapped trades: {len(mapped_trades)}, Unmapped: {unmapped}")

    if not mapped_trades:
        print("No trades could be mapped. Exiting.")
        return

    df = pd.DataFrame(mapped_trades)

    # ── Step 4: Gap distribution ──────────────────────────────────────
    print("\n" + "=" * 70)
    print("=== TRADE GAP ANALYSIS RESULTS ===")
    print("=" * 70)

    print(f"\n--- Per-Trade Return Comparison ---")
    print(f"  Research return (sig_limit -> close[bar+8]):")
    print(f"    Mean:   {df['research_ret'].mean()*100:+.4f}%")
    print(f"    Median: {df['research_ret'].median()*100:+.4f}%")
    print(f"    Std:    {df['research_ret'].std()*100:.4f}%")
    print(f"  Simulator price return (sim_entry -> sim_exit):")
    print(f"    Mean:   {df['sim_price_ret'].mean()*100:+.4f}%")
    print(f"    Median: {df['sim_price_ret'].median()*100:+.4f}%")
    print(f"    Std:    {df['sim_price_ret'].std()*100:.4f}%")
    print(f"  Simulator net return (incl fees/funding):")
    print(f"    Mean:   {df['sim_net_ret'].mean()*100:+.4f}%")
    print(f"    Median: {df['sim_net_ret'].median()*100:+.4f}%")

    gap = df['research_ret'] - df['sim_price_ret']
    net_gap = df['research_ret'] - df['sim_net_ret']
    print(f"\n--- Gap: Research - Sim Price Return ---")
    print(f"    Mean gap:   {gap.mean()*100:+.4f}%")
    print(f"    Median gap: {gap.median()*100:+.4f}%")
    print(f"    P10:  {gap.quantile(0.10)*100:+.4f}%")
    print(f"    P25:  {gap.quantile(0.25)*100:+.4f}%")
    print(f"    P75:  {gap.quantile(0.75)*100:+.4f}%")
    print(f"    P90:  {gap.quantile(0.90)*100:+.4f}%")

    print(f"\n--- Gap: Research - Sim Net Return ---")
    print(f"    Mean gap:   {net_gap.mean()*100:+.4f}%")
    print(f"    Median gap: {net_gap.median()*100:+.4f}%")

    # ── Step 5: Gap decomposition ─────────────────────────────────────
    print(f"\n--- Gap Decomposition (mean per trade) ---")
    print(f"  Entry slippage (sim entered worse):  {df['entry_slip'].mean()*100:+.4f}%")
    print(f"  Exit difference (sim vs bar+8 close): {df['exit_diff'].mean()*100:+.4f}%")
    print(f"  Fees (entry+exit / margin):           {df['fee_impact'].mean()*100:+.4f}%")
    print(f"  Funding (/ margin):                   {df['funding_impact'].mean()*100:+.4f}%")
    print(f"  ---")
    total_explained = (df['entry_slip'].mean() - df['exit_diff'].mean()
                       + df['fee_impact'].mean() + df['funding_impact'].mean())
    print(f"  Sum of components:                    {total_explained*100:+.4f}%")
    print(f"  Actual mean gap (research - sim_net): {net_gap.mean()*100:+.4f}%")

    # Breakdown in absolute dollars
    print(f"\n--- Gap Decomposition (total dollars) ---")
    print(f"  Total research PnL (hypothetical): ${(df['research_ret'] * df['margin_usd']).sum():+,.0f}")
    print(f"  Total sim net PnL:                 ${df['pnl'].sum():+,.0f}")
    print(f"  Total entry fees:                  ${df['entry_fee'].sum():,.0f}")
    print(f"  Total exit fees:                   ${df['exit_fee'].sum():,.0f}")
    print(f"  Total funding cost:                ${df['funding_cost'].sum():+,.0f}")

    # ── Step 6: Win Rate Comparison ───────────────────────────────────
    print(f"\n--- Win Rate Comparison (same set of {len(df)} trades) ---")
    research_wr = df['research_win'].mean()
    sim_wr = df['sim_win'].mean()
    print(f"  Research WR (sig_limit -> close[bar+8] > 0):  {research_wr*100:.1f}%")
    print(f"  Simulator WR (pnl > 0):                       {sim_wr*100:.1f}%")
    print(f"  Sim price-only WR (price_ret > 0):            {(df['sim_price_ret'] > 0).mean()*100:.1f}%")
    print(f"  WR gap:                                        {(research_wr - sim_wr)*100:+.1f}pp")

    # ── Step 7: Selection Bias ────────────────────────────────────────
    print(f"\n--- Selection Bias: Are TAKEN trades worse than average? ---")

    # Compute research return for ALL available entries (not just taken ones)
    all_research_rets = []
    all_research_wins = []

    for token, sig in signals.items():
        if sig.entry_limit_price is None:
            continue
        bm = bar_maps.get(token)
        if bm is None:
            continue

        entry_bars_local = np.where(sig.entry_mask)[0]
        hold = sig.max_hold

        for lb in entry_bars_local:
            exit_lb = lb + hold
            if exit_lb >= sig.n_bars:
                continue

            lp = float(sig.entry_limit_price[lb]) if lb < len(sig.entry_limit_price) else np.nan
            close_exit = float(sig.close[exit_lb])
            direction = int(sig.direction[lb])

            if not np.isnan(lp) and lp > 0:
                if direction == 1:
                    r = (close_exit - lp) / lp
                else:
                    r = (lp - close_exit) / lp
            else:
                close_entry = float(sig.close[lb])
                if direction == 1:
                    r = (close_exit - close_entry) / close_entry
                else:
                    r = (close_entry - close_exit) / close_entry

            all_research_rets.append(r)
            all_research_wins.append(r > 0)

    all_rets = np.array(all_research_rets)
    all_wins = np.array(all_research_wins)

    print(f"  ALL available signal entries: {len(all_rets)}")
    print(f"    Mean return: {all_rets.mean()*100:+.4f}%")
    print(f"    WR:          {all_wins.mean()*100:.1f}%")
    print(f"    Median:      {np.median(all_rets)*100:+.4f}%")

    taken_rets = df['research_ret'].values
    print(f"  TAKEN trades (simulator selected): {len(taken_rets)}")
    print(f"    Mean return: {taken_rets.mean()*100:+.4f}%")
    print(f"    WR:          {df['research_win'].mean()*100:.1f}%")
    print(f"    Median:      {np.median(taken_rets)*100:+.4f}%")

    selection_gap = taken_rets.mean() - all_rets.mean()
    print(f"  Selection bias (taken - all): {selection_gap*100:+.4f}%")
    if selection_gap < -0.001:
        print(f"  >> Simulator is selecting WORSE-than-average trades!")
    elif selection_gap > 0.001:
        print(f"  >> Simulator is selecting BETTER-than-average trades.")
    else:
        print(f"  >> No significant selection bias.")

    # ── Step 8: Breakdown by exit reason ──────────────────────────────
    print(f"\n--- Breakdown by Exit Reason ---")
    for reason, grp in df.groupby('exit_reason'):
        n = len(grp)
        pct = n / len(df) * 100
        avg_ret_research = grp['research_ret'].mean() * 100
        avg_ret_sim = grp['sim_price_ret'].mean() * 100
        wr_r = grp['research_win'].mean() * 100
        wr_s = grp['sim_win'].mean() * 100
        avg_hold = grp['hold_bars'].mean()
        print(f"  {reason:15s}: n={n:4d} ({pct:5.1f}%) | "
              f"research={avg_ret_research:+.3f}% WR={wr_r:.0f}% | "
              f"sim_price={avg_ret_sim:+.3f}% WR={wr_s:.0f}% | "
              f"avg_hold={avg_hold:.1f}")

    # ── Step 9: Breakdown by direction ────────────────────────────────
    print(f"\n--- Breakdown by Direction ---")
    for d, label in [(1, "LONG"), (-1, "SHORT")]:
        grp = df[df['direction'] == d]
        if len(grp) == 0:
            continue
        print(f"  {label:5s}: n={len(grp):4d} | "
              f"research={grp['research_ret'].mean()*100:+.3f}% WR={grp['research_win'].mean()*100:.0f}% | "
              f"sim_price={grp['sim_price_ret'].mean()*100:+.3f}% WR={(grp['sim_price_ret']>0).mean()*100:.0f}% | "
              f"sim_net={grp['sim_net_ret'].mean()*100:+.3f}% WR={grp['sim_win'].mean()*100:.0f}%")

    # ── Step 10: Rejection analysis ───────────────────────────────────
    print(f"\n--- Rejection Statistics ---")
    r = state.rejections
    print(f"  Portfolio limit: {r.portfolio_limit}")
    print(f"  Strategy limit:  {r.strategy_limit}")
    print(f"  Min size:        {r.min_size}")
    print(f"  ADV cap:         {r.adv_cap}")
    print(f"  Concentration:   {r.concentration}")
    print(f"  Capital:         {r.capital}")
    print(f"  Conviction:      {r.conviction}")
    print(f"  DD scaling:      {r.dd_scaling}")
    print(f"  Total rejected:  {r.total()}")
    print(f"  Entries opened:  {n_trades + r.total()} signals -> {n_trades} trades ({n_trades/(n_trades+r.total())*100:.0f}% fill rate)")

    # ── Step 11: Hold bar distribution ────────────────────────────────
    print(f"\n--- Hold Bar Distribution ---")
    holds = df['hold_bars']
    for h in sorted(holds.unique()):
        sub = df[df['hold_bars'] == h]
        print(f"  hold={h:2d}: n={len(sub):4d} | "
              f"research={sub['research_ret'].mean()*100:+.3f}% | "
              f"sim_price={sub['sim_price_ret'].mean()*100:+.3f}% | "
              f"sim_net={sub['pnl'].mean():+.1f}")

    # ── Summary ───────────────────────────────────────────────────────
    print(f"\n{'='*70}")
    print(f"SUMMARY: Where the edge is lost")
    print(f"{'='*70}")
    print(f"  Research per-trade return:    {all_rets.mean()*100:+.4f}% (WR={all_wins.mean()*100:.1f}%, n={len(all_rets)})")
    print(f"  Taken-trade research return:  {taken_rets.mean()*100:+.4f}% (WR={df['research_win'].mean()*100:.1f}%, n={len(df)})")
    print(f"  Sim price return:             {df['sim_price_ret'].mean()*100:+.4f}% (WR={(df['sim_price_ret']>0).mean()*100:.1f}%)")
    print(f"  Sim net return:               {df['sim_net_ret'].mean()*100:+.4f}% (WR={sim_wr*100:.1f}%)")
    print(f"")
    print(f"  Edge lost to selection bias:  {selection_gap*100:+.4f}%")
    print(f"  Edge lost to entry slippage:  {df['entry_slip'].mean()*100:+.4f}%")
    print(f"  Edge lost to exit timing:     {(-df['exit_diff'].mean())*100:+.4f}%")
    print(f"  Edge lost to fees:            {df['fee_impact'].mean()*100:+.4f}%")
    print(f"  Edge lost to funding:         {df['funding_impact'].mean()*100:+.4f}%")
    print(f"  Signals available: {total_signal_entries} | Trades taken: {n_trades} | Rejected: {r.total()}")


if __name__ == '__main__':
    main()
