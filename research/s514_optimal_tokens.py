"""
s514 Optimal Token Selection — Multi-Window Consistency Analysis
=================================================================

Runs s514 across 3 NON-OVERLAPPING time windows to find tokens that
are consistently profitable, avoiding hindsight bias from single-window
optimization.

Windows:
  A: 2024-04 to 2025-04 (12 months, older regime)
  B: 2025-04 to 2025-10 (6 months, mid)
  C: 2025-10 to 2026-04 (6 months, recent)

Token tiers:
  Tier 1: Profitable in ALL 3 windows (most robust)
  Tier 2: Profitable in 2/3 windows
  Tier 3: Profitable in only 1 window (unreliable)

Candidate lists:
  MAX_PNL:      All tokens with positive L12M P&L (hindsight, flagged as overfit)
  ROBUST:       Tokens profitable in 2+ of 3 windows (validated)
  ULTRA_ROBUST: Tokens profitable in all 3 windows (highest conviction)

Usage:
    /workspace/venv/bin/python research/s514_optimal_tokens.py
"""

import sys
import os
import time
import dataclasses

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pandas as pd
import numpy as np
from collections import defaultdict

from v4.config import PortfolioConfig, StrategySpec
from v4.signals import precompute_strategy_signals, discover_tokens
from v4.simulator import simulate_portfolio
from v4.report import compute_portfolio_metrics, print_report

STRATEGY_ID = 's514'
MARKET = 'perp'
CAPITAL = 100_000

# Original 29-token curated list for comparison
ORIGINAL_29 = {
    'AAVE', 'ADA', 'APT', 'ARB', 'ATOM', 'AVAX', 'BNB', 'BTC', 'DOGE', 'DOT',
    'ETH', 'FIL', 'IMX', 'INJ', 'LINK', 'LTC', 'MATIC', 'MKR', 'NEAR', 'ONDO',
    'OP', 'SEI', 'SOL', 'SUI', 'TIA', 'TRX', 'UNI', 'WIF', 'XRP',
}


def make_spec_and_config(max_positions=200):
    """Create strategy spec and portfolio config for s514."""
    spec = StrategySpec(
        strategy_id=STRATEGY_ID,
        weight=1.0,
        max_positions=max_positions,
        market=MARKET,
        strategy_type='per_token',
        max_concurrent_per_token=1,
        dd_scaling=[],
    )
    config = PortfolioConfig(
        capital=CAPITAL,
        exchange='binance',
        skip_walk_forward=True,
        conviction_mode='ranked',
        max_portfolio_positions=max_positions,
        strategies=[spec],
    )
    return spec, config


def run_backtest(months, end_date_str, max_positions=200):
    """Run a full portfolio backtest and return state + metrics."""
    end_date = pd.Timestamp(end_date_str)
    spec, config = make_spec_and_config(max_positions)

    tokens = discover_tokens(MARKET)

    t0 = time.time()
    signals = precompute_strategy_signals(spec, tokens, config, months, end_date=end_date)
    t1 = time.time()

    tokens_with_signals = sum(1 for t, sigs in signals.items() if sigs.entry_mask.any())
    print(f"  Signals: {len(signals)} tokens total, {tokens_with_signals} with entries ({t1 - t0:.1f}s)")

    if not signals:
        print("  No signals produced!")
        return None, None, None

    strategy_specs = {STRATEGY_ID: spec}
    config_run = dataclasses.replace(config, strategies=[spec])
    state = simulate_portfolio({STRATEGY_ID: signals}, strategy_specs, config_run)
    t2 = time.time()
    print(f"  Simulation: {len(state.position_manager.closed_trades)} trades ({t2 - t1:.1f}s)")

    metrics, extra_info, eq_daily = compute_portfolio_metrics(state, CAPITAL)
    return state, metrics, extra_info


def get_per_token_pnl(state):
    """Extract per-token P&L and trade count from closed trades."""
    token_pnl = defaultdict(float)
    token_trades = defaultdict(int)
    token_wins = defaultdict(int)
    for trade in state.position_manager.closed_trades:
        token_pnl[trade.token] += trade.pnl
        token_trades[trade.token] += 1
        if trade.pnl > 0:
            token_wins[trade.token] += 1
    return dict(token_pnl), dict(token_trades), dict(token_wins)


def main():
    print("=" * 70)
    print("  s514 Optimal Token Selection — Multi-Window Consistency")
    print("  3 non-overlapping windows to avoid hindsight bias")
    print("=" * 70)

    # ── Step 1: Run 3 non-overlapping windows ──
    windows = {
        'A (2024-04 to 2025-04)': (12, '2025-04-01'),
        'B (2025-04 to 2025-10)': (6, '2025-10-01'),
        'C (2025-10 to 2026-04)': (6, '2026-04-01'),
    }

    window_pnls = {}
    window_trades = {}
    window_metrics = {}

    for name, (months, end) in windows.items():
        print(f"\n{'='*70}")
        print(f"  Window {name}: {months}M ending {end}")
        print(f"{'='*70}")
        state, metrics, extra_info = run_backtest(months, end)
        if state is None:
            print(f"  FAILED — skipping window")
            continue
        pnl, trades, wins = get_per_token_pnl(state)
        window_pnls[name] = pnl
        window_trades[name] = trades
        window_metrics[name] = metrics
        print(f"  Tokens traded: {len(pnl)}, Total P&L: ${sum(pnl.values()):,.0f}")
        print(f"  Sharpe: {metrics.sharpe_ratio:.2f}, Return: {metrics.total_return_pct:.1f}%")
        print(f"  MaxDD: {metrics.max_drawdown_pct:.1f}%")

    if len(window_pnls) < 3:
        print("\nERROR: Not all 3 windows completed. Cannot proceed.")
        return

    # ── Step 2: Classify tokens by consistency ──
    all_tokens = set()
    for pnl in window_pnls.values():
        all_tokens |= set(pnl.keys())

    token_scores = {}
    for token in all_tokens:
        profitable_windows = []
        for wname in windows:
            p = window_pnls[wname].get(token, 0)
            if p > 0:
                profitable_windows.append(wname)
        total_pnl = sum(window_pnls[w].get(token, 0) for w in windows)
        total_trades = sum(window_trades[w].get(token, 0) for w in windows)
        token_scores[token] = {
            'wins': len(profitable_windows),
            'total_pnl': total_pnl,
            'total_trades': total_trades,
            'profitable_in': profitable_windows,
        }

    tier1 = sorted([t for t, s in token_scores.items() if s['wins'] == 3],
                   key=lambda t: token_scores[t]['total_pnl'], reverse=True)
    tier2 = sorted([t for t, s in token_scores.items() if s['wins'] == 2],
                   key=lambda t: token_scores[t]['total_pnl'], reverse=True)
    tier3 = sorted([t for t, s in token_scores.items() if s['wins'] == 1],
                   key=lambda t: token_scores[t]['total_pnl'], reverse=True)
    tier0 = sorted([t for t, s in token_scores.items() if s['wins'] == 0],
                   key=lambda t: token_scores[t]['total_pnl'], reverse=True)

    window_names = list(windows.keys())

    print(f"\n{'='*70}")
    print("  TOKEN CONSISTENCY TIERS")
    print(f"{'='*70}")

    print(f"\n  Tier 1 — Profitable in ALL 3 windows: {len(tier1)} tokens")
    print(f"  {'Token':<12} {'Total P&L':>12} {'Trades':>8}  {'Window A':>12} {'Window B':>12} {'Window C':>12}  {'Orig29':>6}")
    print(f"  {'-'*76}")
    for t in tier1:
        pnls = [f"${window_pnls[w].get(t, 0):>+9,.0f}" for w in window_names]
        in_orig = 'Y' if t in ORIGINAL_29 else 'N'
        print(f"  {t:<12} ${token_scores[t]['total_pnl']:>+11,.0f} {token_scores[t]['total_trades']:>8}  "
              f"{'  '.join(pnls)}  {in_orig:>6}")

    print(f"\n  Tier 2 — Profitable in 2/3 windows: {len(tier2)} tokens")
    print(f"  {'Token':<12} {'Total P&L':>12} {'Trades':>8}  {'Window A':>12} {'Window B':>12} {'Window C':>12}  {'Orig29':>6}")
    print(f"  {'-'*76}")
    for t in tier2:
        pnls = [f"${window_pnls[w].get(t, 0):>+9,.0f}" for w in window_names]
        in_orig = 'Y' if t in ORIGINAL_29 else 'N'
        print(f"  {t:<12} ${token_scores[t]['total_pnl']:>+11,.0f} {token_scores[t]['total_trades']:>8}  "
              f"{'  '.join(pnls)}  {in_orig:>6}")

    print(f"\n  Tier 3 — Profitable in 1/3 windows: {len(tier3)} tokens")
    print(f"  {'Token':<12} {'Total P&L':>12} {'Trades':>8}  {'Window A':>12} {'Window B':>12} {'Window C':>12}")
    print(f"  {'-'*60}")
    for t in tier3:
        pnls = [f"${window_pnls[w].get(t, 0):>+9,.0f}" for w in window_names]
        print(f"  {t:<12} ${token_scores[t]['total_pnl']:>+11,.0f} {token_scores[t]['total_trades']:>8}  "
              f"{'  '.join(pnls)}")

    print(f"\n  Tier 0 — Never profitable: {len(tier0)} tokens")
    for t in tier0:
        pnls = [f"${window_pnls[w].get(t, 0):>+9,.0f}" for w in window_names]
        print(f"  {t:<12} ${token_scores[t]['total_pnl']:>+11,.0f} {token_scores[t]['total_trades']:>8}  "
              f"{'  '.join(pnls)}")

    # ── Step 3: Run fresh L12M for hindsight comparison ──
    print(f"\n{'='*70}")
    print("  Running fresh L12M backtest (hindsight reference)...")
    print(f"{'='*70}")
    state_l12m, metrics_l12m, extra_l12m = run_backtest(12, '2026-04-01')
    if state_l12m is None:
        print("  FAILED — cannot compute hindsight list")
        return
    l12m_pnl, l12m_trades, l12m_wins = get_per_token_pnl(state_l12m)

    print(f"  L12M portfolio: Sharpe {metrics_l12m.sharpe_ratio:.2f}, "
          f"Return {metrics_l12m.total_return_pct:.1f}%, "
          f"MaxDD {metrics_l12m.max_drawdown_pct:.1f}%")

    # ── Step 4: Build candidate lists ──
    max_pnl_tokens = sorted([t for t, p in l12m_pnl.items() if p > 0],
                            key=lambda t: l12m_pnl[t], reverse=True)
    robust_tokens = tier1 + tier2   # 2+ windows profitable
    ultra_robust_tokens = tier1     # all 3 windows profitable

    print(f"\n{'='*70}")
    print("  CANDIDATE TOKEN LISTS")
    print(f"{'='*70}")
    print(f"\n  MAX_PNL (L12M hindsight, OVERFIT WARNING): {len(max_pnl_tokens)} tokens")
    print(f"    {max_pnl_tokens}")
    print(f"\n  ROBUST (profitable 2+/3 windows): {len(robust_tokens)} tokens")
    print(f"    {robust_tokens}")
    print(f"\n  ULTRA_ROBUST (profitable 3/3 windows): {len(ultra_robust_tokens)} tokens")
    print(f"    {ultra_robust_tokens}")

    # ── Step 5: Estimate L12M P&L for each candidate list ──
    # Sum per-token P&L from the full L12M run for each token list
    print(f"\n{'='*70}")
    print("  ESTIMATED L12M P&L BY TOKEN LIST")
    print("  (summing per-token P&L from full 229-token L12M simulation)")
    print(f"{'='*70}")

    closed = state_l12m.position_manager.closed_trades

    for name, token_list in [
        ('ALL 229', list(l12m_pnl.keys())),
        ('ORIGINAL 29', [t for t in ORIGINAL_29 if t in l12m_pnl]),
        ('MAX_PNL (hindsight)', max_pnl_tokens),
        ('ROBUST (2+/3)', robust_tokens),
        ('ULTRA_ROBUST (3/3)', ultra_robust_tokens),
    ]:
        token_set = set(token_list)
        pnl_sum = sum(l12m_pnl.get(t, 0) for t in token_set)
        n_trades = sum(1 for tr in closed if tr.token in token_set)
        n_winners = sum(1 for tr in closed if tr.token in token_set and tr.pnl > 0)
        trade_wr = n_winners / n_trades * 100 if n_trades > 0 else 0
        n_profitable = sum(1 for t in token_set if l12m_pnl.get(t, 0) > 0)
        n_losing = len(token_set) - n_profitable

        print(f"\n  {name}:")
        print(f"    Tokens: {len(token_set)} ({n_profitable} profitable, {n_losing} losing)")
        print(f"    Est L12M P&L: ${pnl_sum:>+12,.0f}")
        print(f"    Trades: {n_trades}, Trade WR: {trade_wr:.1f}%")
        print(f"    Avg P&L/token: ${pnl_sum/len(token_set):>+9,.0f}" if token_set else "    N/A")

    # ── Step 6: New discoveries ──
    print(f"\n{'='*70}")
    print("  NEW DISCOVERIES — Tokens NOT in original 29 that are ROBUST")
    print(f"{'='*70}")
    new_robust = [t for t in robust_tokens if t not in ORIGINAL_29]
    new_ultra = [t for t in ultra_robust_tokens if t not in ORIGINAL_29]

    print(f"\n  New ULTRA_ROBUST (3/3, not in original 29): {len(new_ultra)} tokens")
    for t in new_ultra:
        pnls = [f"${window_pnls[w].get(t, 0):>+9,.0f}" for w in window_names]
        l12m_p = l12m_pnl.get(t, 0)
        print(f"    {t:<12} L12M: ${l12m_p:>+9,.0f}  windows: {'  '.join(pnls)}")

    print(f"\n  New ROBUST (2/3, not in original 29): {len(new_robust)} tokens")
    for t in new_robust:
        pnls = [f"${window_pnls[w].get(t, 0):>+9,.0f}" for w in window_names]
        l12m_p = l12m_pnl.get(t, 0)
        print(f"    {t:<12} L12M: ${l12m_p:>+9,.0f}  windows: {'  '.join(pnls)}")

    # ── Step 7: Original 29 that are NOT robust ──
    print(f"\n{'='*70}")
    print("  ORIGINAL 29 TOKENS — ROBUSTNESS CHECK")
    print(f"{'='*70}")
    orig_robust = [t for t in ORIGINAL_29 if t in set(robust_tokens)]
    orig_not_robust = [t for t in ORIGINAL_29 if t not in set(robust_tokens) and t in all_tokens]
    orig_no_data = [t for t in ORIGINAL_29 if t not in all_tokens]

    print(f"\n  Robust (2+/3 windows): {len(orig_robust)}")
    for t in sorted(orig_robust, key=lambda t: token_scores.get(t, {}).get('total_pnl', 0), reverse=True):
        tier = "T1" if t in tier1 else "T2"
        print(f"    {t:<10} [{tier}]  total: ${token_scores[t]['total_pnl']:>+9,.0f}")

    print(f"\n  NOT robust (<2 windows): {len(orig_not_robust)}")
    for t in sorted(orig_not_robust, key=lambda t: token_scores.get(t, {}).get('total_pnl', 0), reverse=True):
        tier = "T3" if t in tier3 else "T0"
        pnls = [f"${window_pnls[w].get(t, 0):>+9,.0f}" for w in window_names]
        print(f"    {t:<10} [{tier}]  total: ${token_scores[t]['total_pnl']:>+9,.0f}  windows: {'  '.join(pnls)}")

    if orig_no_data:
        print(f"\n  No trade data: {len(orig_no_data)} — {sorted(orig_no_data)}")

    # ── Step 8: Final recommendation ──
    print(f"\n{'='*70}")
    print("  FINAL RECOMMENDATION")
    print(f"{'='*70}")
    print(f"\n  ULTRA_ROBUST list (3/3 windows, highest conviction):")
    print(f"  TOKENS = {ultra_robust_tokens}")
    print(f"\n  ROBUST list (2+/3 windows, good balance):")
    print(f"  TOKENS = {robust_tokens}")
    print(f"\n  Use ROBUST for production (maximizes P&L without hindsight).")
    print(f"  Use ULTRA_ROBUST if you want maximum conviction / minimum regret.")
    print(f"\n  WARNING: MAX_PNL list is hindsight-biased. Do NOT use in production")
    print(f"  without forward validation.")

    print(f"\nDone.")


if __name__ == '__main__':
    main()
