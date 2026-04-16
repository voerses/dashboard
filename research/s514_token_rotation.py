"""
s514 Token Rotation Research — Monthly Dynamic Token Selection
===============================================================

Tests whether dynamically rotating tokens each month based on predictive
signals (last month's per-token P&L) improves returns vs the fixed 28-token
allowlist.

s514 currently uses a fixed 28-token allowlist and achieved +678% OOS monthly.
This script runs the full 229-token universe month-by-month, extracts per-token
P&L each month, then tests whether LAST month's per-token metrics predict
NEXT month's performance.

Rotation strategies tested:
  - TOP_10/15/20_momentum: Best performing tokens from prior month
  - ALL_positive_momentum: All tokens with positive P&L last month
  - BOTTOM_10_antimo: Worst performing tokens (contrarian)
  - ALL_positive_2mo: Positive in both prior 2 months
  - STATIC_28: Current fixed allowlist (baseline)
  - FULL_universe: All tokens (no filter)

Usage:
    /workspace/venv/bin/python research/s514_token_rotation.py
"""

import sys
import os
import time
import dataclasses

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
os.chdir(os.path.join(os.path.dirname(__file__), '..'))

import pandas as pd
import numpy as np
from collections import defaultdict

from v4.config import PortfolioConfig, StrategySpec
from v4.signals import precompute_strategy_signals, discover_tokens
from v4.simulator import simulate_portfolio, build_unified_index
from v4.report import compute_portfolio_metrics
from v4.engine import _STRATEGY_MODULE_CACHE


STRATEGY_ID = 's514'
MARKET = 'perp'
CAPITAL = 100_000

# Current fixed allowlist for baseline comparison
STATIC_28 = {
    'EIGEN', 'PENGU', 'INJ', 'LTC', 'ARB', 'SAND', 'AAVE', 'GUN',
    'RAYSOL', 'LINK', 'HBAR', 'RENDER', 'ETH', 'PIXEL', 'DOGE',
    'WLD', 'VANRY', 'ADA', 'AIOT', 'UNI', 'BTC', 'IP', 'NEAR',
    'ETC', 'MYX', 'PIPPIN', 'ZEN', 'RESOLV',
}

# Months to backtest: (label, lookback_months, end_date)
# 6-month lookback ending at month boundary gives signal history + last month's trades
MONTHS = [
    ('2025-04', 6, '2025-05-01'),
    ('2025-05', 6, '2025-06-01'),
    ('2025-06', 6, '2025-07-01'),
    ('2025-07', 6, '2025-08-01'),
    ('2025-08', 6, '2025-09-01'),
    ('2025-09', 6, '2025-10-01'),
    ('2025-10', 6, '2025-11-01'),
    ('2025-11', 6, '2025-12-01'),
    ('2025-12', 6, '2026-01-01'),
    ('2026-01', 6, '2026-02-01'),
    ('2026-02', 6, '2026-03-01'),
    ('2026-03', 6, '2026-04-01'),
]


def make_spec_and_config(max_positions=200):
    """Create strategy spec and portfolio config for s514 full-universe run."""
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
        concentration_limit=0.15,
    )
    return spec, config


def bypass_token_filter():
    """Set ALLOWED_TOKENS to include all tokens from L/S parquet data."""
    mod = _STRATEGY_MODULE_CACHE.get('s514')
    if mod is None:
        raise RuntimeError("s514 module not loaded yet — run precompute first")

    # Get all token names from the perp L/S data
    ls_path = os.path.join('data', 'alternative', 'binance_metrics',
                           'all_symbols_daily_ls.parquet')
    df = pd.read_parquet(ls_path, columns=['symbol'])
    all_tokens = set(s.replace('USDT', '') for s in df['symbol'].unique())
    mod.ALLOWED_TOKENS = all_tokens
    # Remove TP so trail captures full move
    mod.TARGET_MULT = 999.0
    print(f"  Bypassed token filter: {len(all_tokens)} tokens allowed")
    return all_tokens


def clear_strategy_caches():
    """Clear per-run caches in the s514 module to avoid stale data."""
    mod = _STRATEGY_MODULE_CACHE.get('s514')
    if mod is None:
        return
    if hasattr(mod, '_aligned_cache'):
        mod._aligned_cache.clear()
    if hasattr(mod, '_ls_loaded'):
        mod._ls_loaded = False
    if hasattr(mod, '_ls_cache'):
        mod._ls_cache.clear()


def run_month(month_label, lookback, end_date_str):
    """Run full-universe backtest for one month and extract per-token P&L.

    Returns dict {token: pnl} for trades that OPENED in the target month.
    """
    clear_strategy_caches()
    end_date = pd.Timestamp(end_date_str)
    spec, config = make_spec_and_config()

    tokens = discover_tokens(MARKET)
    signals = precompute_strategy_signals(spec, tokens, config, lookback,
                                         end_date=end_date)

    if not signals:
        print(f"  [{month_label}] No signals produced!")
        return {}, {}

    strategy_specs = {STRATEGY_ID: spec}
    config_run = dataclasses.replace(config, strategies=[spec])

    # Build unified index to map entry_bar -> timestamp
    unified_ts, _ = build_unified_index({STRATEGY_ID: signals})

    state = simulate_portfolio({STRATEGY_ID: signals}, strategy_specs, config_run)

    # Extract per-token P&L for trades opening in the target month
    token_pnl = defaultdict(float)
    token_trades = defaultdict(int)

    for trade in state.position_manager.closed_trades:
        # Map entry_bar to timestamp
        if trade.entry_bar < len(unified_ts):
            entry_ts = pd.Timestamp(unified_ts[trade.entry_bar])
            entry_month = entry_ts.strftime('%Y-%m')
        else:
            continue

        if entry_month == month_label:
            token_pnl[trade.token] += trade.pnl
            token_trades[trade.token] += 1

    return dict(token_pnl), dict(token_trades)


def step1_collect_monthly_pnl():
    """Step 1: Run each month on full universe, collect per-token P&L."""
    print("=" * 70)
    print("  STEP 1: Monthly Per-Token P&L Collection (Full Universe)")
    print("=" * 70)

    # First, force-load the strategy module
    spec, config = make_spec_and_config()
    tokens = discover_tokens(MARKET)
    precompute_strategy_signals(spec, tokens, config, 12,
                                end_date=pd.Timestamp('2026-04-01'))

    # Bypass the token filter for all subsequent runs
    all_tokens = bypass_token_filter()

    monthly_token_pnl = {}
    monthly_token_trades = {}

    for month_label, lookback, end_date_str in MONTHS:
        t0 = time.time()
        pnl, trades = run_month(month_label, lookback, end_date_str)
        elapsed = time.time() - t0

        total = sum(pnl.values())
        n_tokens = len(pnl)
        monthly_token_pnl[month_label] = pnl
        monthly_token_trades[month_label] = trades
        print(f"  {month_label}: {n_tokens} tokens traded, "
              f"${total:+,.0f} P&L ({elapsed:.1f}s)")

    return monthly_token_pnl, monthly_token_trades


def step2_test_rotation_strategies(monthly_token_pnl, monthly_token_trades):
    """Step 2: Test rotation strategies using lookahead-free monthly selection."""
    print("\n" + "=" * 70)
    print("  STEP 2: Rotation Strategy Comparison")
    print("=" * 70)

    month_labels = [m[0] for m in MONTHS]

    # Define rotation strategies
    rotation_strategies = [
        ("TOP_10_momentum",
         lambda prev, prev2: set(t for t, p in sorted(
             prev.items(), key=lambda x: x[1], reverse=True)[:10])),
        ("TOP_15_momentum",
         lambda prev, prev2: set(t for t, p in sorted(
             prev.items(), key=lambda x: x[1], reverse=True)[:15])),
        ("TOP_20_momentum",
         lambda prev, prev2: set(t for t, p in sorted(
             prev.items(), key=lambda x: x[1], reverse=True)[:20])),
        ("TOP_28_momentum",
         lambda prev, prev2: set(t for t, p in sorted(
             prev.items(), key=lambda x: x[1], reverse=True)[:28])),
        ("ALL_positive_momentum",
         lambda prev, prev2: set(t for t, p in prev.items() if p > 0)),
        ("BOTTOM_10_antimo",
         lambda prev, prev2: set(t for t, p in sorted(
             prev.items(), key=lambda x: x[1])[:10])),
        ("ALL_positive_2mo",
         lambda prev, prev2: (
             set(t for t in prev if prev.get(t, 0) > 0)
             & set(t for t in prev2 if prev2.get(t, 0) > 0)
         )),
        ("STATIC_28",
         lambda prev, prev2: STATIC_28),
        ("FULL_universe",
         lambda prev, prev2: None),  # None = all tokens
    ]

    results = {}
    for strategy_name, select_fn in rotation_strategies:
        monthly_results = []
        total_rotated_pnl = 0
        months_tested = 0

        # Start from month index 2 to have 2 months of history
        for i in range(2, len(month_labels)):
            current_month = month_labels[i]
            prev_month = month_labels[i - 1]
            prev2_month = month_labels[i - 2]

            prev = monthly_token_pnl.get(prev_month, {})
            prev2 = monthly_token_pnl.get(prev2_month, {})
            current = monthly_token_pnl.get(current_month, {})

            selected = select_fn(prev, prev2)

            if selected is None:
                # Full universe — all tokens
                rotated_pnl = sum(current.values())
                n_selected = len(current)
            else:
                rotated_pnl = sum(current.get(t, 0) for t in selected)
                n_selected = len(selected)

            all_pnl = sum(current.values())
            total_rotated_pnl += rotated_pnl
            months_tested += 1
            monthly_results.append({
                'month': current_month,
                'selected_pnl': rotated_pnl,
                'all_pnl': all_pnl,
                'n_selected': n_selected,
                'n_all': len(current),
            })

        avg_monthly = total_rotated_pnl / months_tested if months_tested else 0
        results[strategy_name] = {
            'total_pnl': total_rotated_pnl,
            'months_tested': months_tested,
            'avg_monthly': avg_monthly,
            'monthly_results': monthly_results,
        }
        print(f"  {strategy_name:<30} Total P&L: ${total_rotated_pnl:+,.0f} "
              f"({months_tested} months, avg ${avg_monthly:+,.0f}/mo)")

    return results


def step3_autocorrelation(monthly_token_pnl):
    """Step 3: Test per-token P&L autocorrelation (month M vs M+1)."""
    print("\n" + "=" * 70)
    print("  STEP 3: Per-Token P&L Autocorrelation")
    print("=" * 70)

    month_labels = [m[0] for m in MONTHS]

    # Build token-level month-over-month pairs
    all_pairs = []  # (pnl_this_month, pnl_next_month)
    for i in range(len(month_labels) - 1):
        this_month = month_labels[i]
        next_month = month_labels[i + 1]
        pnl_this = monthly_token_pnl.get(this_month, {})
        pnl_next = monthly_token_pnl.get(next_month, {})

        # Only include tokens present in both months
        common_tokens = set(pnl_this.keys()) & set(pnl_next.keys())
        for token in common_tokens:
            all_pairs.append((pnl_this[token], pnl_next[token]))

    if len(all_pairs) < 10:
        print("  Not enough data for autocorrelation analysis")
        return None

    pairs = np.array(all_pairs)
    corr = np.corrcoef(pairs[:, 0], pairs[:, 1])[0, 1]
    print(f"  Per-token monthly P&L autocorrelation: {corr:.4f} (N={len(pairs)})")

    # Rank correlation (Spearman) — more robust to outliers
    from scipy.stats import spearmanr
    rho, pval = spearmanr(pairs[:, 0], pairs[:, 1])
    print(f"  Spearman rank correlation: {rho:.4f} (p={pval:.4f})")

    # Also test: does winning last month predict winning next month?
    prev_positive = pairs[:, 0] > 0
    next_positive = pairs[:, 1] > 0
    hit_rate = np.mean(next_positive[prev_positive]) if prev_positive.sum() > 0 else 0
    base_rate = np.mean(next_positive)
    print(f"  P(profit M+1 | profit M) = {hit_rate:.1%} vs base rate {base_rate:.1%}")

    prev_negative = pairs[:, 0] <= 0
    if prev_negative.sum() > 0:
        loser_hit = np.mean(next_positive[prev_negative])
        print(f"  P(profit M+1 | loss M)   = {loser_hit:.1%}")

    return corr, rho, pval


def step4_compounding_backtest(monthly_token_pnl, best_strategy_name, select_fn):
    """Step 4: If rotation works, run proper compounding backtest."""
    print("\n" + "=" * 70)
    print(f"  STEP 4: Compounding Backtest — {best_strategy_name}")
    print("=" * 70)

    month_labels = [m[0] for m in MONTHS]

    capital = CAPITAL
    equity_curve = [('start', capital)]

    for i in range(len(month_labels)):
        current_month = month_labels[i]
        current = monthly_token_pnl.get(current_month, {})

        if i < 2:
            # Not enough history for rotation — use full universe
            month_pnl = sum(current.values())
        else:
            prev = monthly_token_pnl.get(month_labels[i - 1], {})
            prev2 = monthly_token_pnl.get(month_labels[i - 2], {})
            selected = select_fn(prev, prev2)

            if selected is None:
                month_pnl = sum(current.values())
            else:
                month_pnl = sum(current.get(t, 0) for t in selected)

        # Scale P&L to current capital level (original P&L is on $100k base)
        scaled_pnl = month_pnl * (capital / CAPITAL)
        capital += scaled_pnl
        equity_curve.append((current_month, capital))

        pct = (scaled_pnl / (capital - scaled_pnl)) * 100
        print(f"  {current_month}: P&L ${scaled_pnl:+,.0f} "
              f"({pct:+.1f}%), equity ${capital:,.0f}")

    total_return = (capital / CAPITAL - 1) * 100
    print(f"\n  Total compounded return: {total_return:+.1f}%")
    print(f"  Final equity: ${capital:,.0f}")

    # Compare with static 28 compounding
    capital_static = CAPITAL
    for i in range(len(month_labels)):
        current = monthly_token_pnl.get(month_labels[i], {})
        month_pnl = sum(current.get(t, 0) for t in STATIC_28)
        scaled_pnl = month_pnl * (capital_static / CAPITAL)
        capital_static += scaled_pnl

    static_return = (capital_static / CAPITAL - 1) * 100
    print(f"\n  Static 28 compounded return: {static_return:+.1f}%")
    print(f"  Static 28 final equity: ${capital_static:,.0f}")

    # Full universe compounding
    capital_full = CAPITAL
    for i in range(len(month_labels)):
        current = monthly_token_pnl.get(month_labels[i], {})
        month_pnl = sum(current.values())
        scaled_pnl = month_pnl * (capital_full / CAPITAL)
        capital_full += scaled_pnl

    full_return = (capital_full / CAPITAL - 1) * 100
    print(f"  Full universe compounded return: {full_return:+.1f}%")
    print(f"  Full universe final equity: ${capital_full:,.0f}")

    return equity_curve


def print_pnl_matrix(monthly_token_pnl):
    """Print per-month per-token P&L matrix."""
    print("\n" + "=" * 70)
    print("  PER-TOKEN MONTHLY P&L MATRIX")
    print("=" * 70)

    # Collect all tokens
    all_tokens = set()
    for pnl in monthly_token_pnl.values():
        all_tokens.update(pnl.keys())
    all_tokens = sorted(all_tokens)

    month_labels = [m[0] for m in MONTHS]

    # Build DataFrame
    rows = []
    for token in all_tokens:
        row = {'token': token}
        total = 0
        months_active = 0
        months_positive = 0
        for month in month_labels:
            pnl = monthly_token_pnl.get(month, {}).get(token, 0)
            row[month] = pnl
            total += pnl
            if pnl != 0:
                months_active += 1
            if pnl > 0:
                months_positive += 1
        row['total'] = total
        row['months_active'] = months_active
        row['months_positive'] = months_positive
        rows.append(row)

    df = pd.DataFrame(rows).set_index('token')
    df = df.sort_values('total', ascending=False)

    # Print top 30 tokens
    print(f"\n  Top 30 tokens by total P&L:")
    print(f"  {'Token':<10}", end='')
    for m in month_labels:
        print(f" {m[5:]:>7}", end='')
    print(f" {'Total':>10} {'Active':>7} {'Pos':>5}")
    print("  " + "-" * (10 + 8 * len(month_labels) + 10 + 7 + 5))

    for idx, row in df.head(30).iterrows():
        print(f"  {idx:<10}", end='')
        for m in month_labels:
            val = row[m]
            if val == 0:
                print(f"      -", end='')
            elif val > 0:
                print(f" {val:>+6.0f}", end='')
            else:
                print(f" {val:>+6.0f}", end='')
        print(f" {row['total']:>+10,.0f} {row['months_active']:>7.0f} "
              f"{row['months_positive']:>5.0f}")

    # Print bottom 10 tokens
    print(f"\n  Bottom 10 tokens by total P&L:")
    for idx, row in df.tail(10).iterrows():
        print(f"  {idx:<10}", end='')
        for m in month_labels:
            val = row[m]
            if val == 0:
                print(f"      -", end='')
            else:
                print(f" {val:>+6.0f}", end='')
        print(f" {row['total']:>+10,.0f} {row['months_active']:>7.0f} "
              f"{row['months_positive']:>5.0f}")

    # Summary stats
    total_all = df['total'].sum()
    n_profitable = (df['total'] > 0).sum()
    n_unprofitable = (df['total'] <= 0).sum()
    print(f"\n  Summary: {n_profitable} profitable tokens, "
          f"{n_unprofitable} unprofitable, "
          f"total P&L: ${total_all:+,.0f}")

    return df


def main():
    t_start = time.time()

    # ====================================================================
    # Step 1: Collect per-token P&L for each month
    # ====================================================================
    monthly_token_pnl, monthly_token_trades = step1_collect_monthly_pnl()

    # ====================================================================
    # Print P&L matrix
    # ====================================================================
    pnl_df = print_pnl_matrix(monthly_token_pnl)

    # ====================================================================
    # Step 2: Test rotation strategies
    # ====================================================================
    results = step2_test_rotation_strategies(monthly_token_pnl,
                                            monthly_token_trades)

    # ====================================================================
    # Step 3: Autocorrelation analysis
    # ====================================================================
    autocorr = step3_autocorrelation(monthly_token_pnl)

    # ====================================================================
    # Step 4: Compounding backtest with best rotation strategy
    # ====================================================================
    # Find best rotation strategy (excluding FULL_universe and STATIC_28)
    rotation_only = {k: v for k, v in results.items()
                     if k not in ('FULL_universe', 'STATIC_28')}
    if rotation_only:
        best_name = max(rotation_only, key=lambda k: rotation_only[k]['total_pnl'])
        best_pnl = rotation_only[best_name]['total_pnl']
        static_pnl = results.get('STATIC_28', {}).get('total_pnl', 0)

        print(f"\n  Best rotation strategy: {best_name} "
              f"(${best_pnl:+,.0f} vs static ${static_pnl:+,.0f})")

        # Define the best strategy's selection function for compounding
        rotation_fns = {
            "TOP_10_momentum": lambda prev, prev2: set(t for t, p in sorted(
                prev.items(), key=lambda x: x[1], reverse=True)[:10]),
            "TOP_15_momentum": lambda prev, prev2: set(t for t, p in sorted(
                prev.items(), key=lambda x: x[1], reverse=True)[:15]),
            "TOP_20_momentum": lambda prev, prev2: set(t for t, p in sorted(
                prev.items(), key=lambda x: x[1], reverse=True)[:20]),
            "TOP_28_momentum": lambda prev, prev2: set(t for t, p in sorted(
                prev.items(), key=lambda x: x[1], reverse=True)[:28]),
            "ALL_positive_momentum": lambda prev, prev2: set(
                t for t, p in prev.items() if p > 0),
            "BOTTOM_10_antimo": lambda prev, prev2: set(t for t, p in sorted(
                prev.items(), key=lambda x: x[1])[:10]),
            "ALL_positive_2mo": lambda prev, prev2: (
                set(t for t in prev if prev.get(t, 0) > 0)
                & set(t for t in prev2 if prev2.get(t, 0) > 0)
            ),
        }

        if best_name in rotation_fns:
            step4_compounding_backtest(monthly_token_pnl, best_name,
                                      rotation_fns[best_name])

    # ====================================================================
    # Final summary
    # ====================================================================
    elapsed = time.time() - t_start
    print(f"\n{'=' * 70}")
    print(f"  FINAL SUMMARY")
    print(f"{'=' * 70}")

    print(f"\n  Rotation Strategy Rankings (by total P&L over "
          f"{list(results.values())[0]['months_tested'] if results else 0} months):")
    sorted_results = sorted(results.items(),
                            key=lambda x: x[1]['total_pnl'], reverse=True)
    for rank, (name, data) in enumerate(sorted_results, 1):
        marker = " <-- current" if name == "STATIC_28" else ""
        print(f"    {rank}. {name:<30} ${data['total_pnl']:>+12,.0f} "
              f"(avg ${data['avg_monthly']:>+8,.0f}/mo){marker}")

    if autocorr is not None:
        corr, rho, pval = autocorr
        signal = "SIGNIFICANT" if pval < 0.05 else "NOT significant"
        print(f"\n  Autocorrelation: Pearson={corr:.4f}, "
              f"Spearman={rho:.4f} (p={pval:.4f}, {signal})")
        if rho > 0.1 and pval < 0.05:
            print("  -> MOMENTUM EFFECT DETECTED: "
                  "last month's winners tend to be next month's winners")
        elif rho < -0.1 and pval < 0.05:
            print("  -> MEAN REVERSION DETECTED: "
                  "last month's winners tend to be next month's losers")
        else:
            print("  -> NO PREDICTIVE SIGNAL: "
                  "per-token monthly P&L is essentially random walk")

    print(f"\n  Total runtime: {elapsed:.0f}s")


if __name__ == '__main__':
    main()
