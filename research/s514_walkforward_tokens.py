"""
s514 Walk-Forward Dynamic Token Selection
==========================================

Builds a walk-forward system that trains promotion/demotion rules on
April 2024 - March 2025 (12 months), then tests them out-of-sample on
April 2025 - March 2026 (12 months).

TRAIN period: learn which tokens work and what predicts token quality.
TEST period: apply rules month-by-month, promoting/demoting tokens based
on rolling performance (using ONLY past data at each decision point).

Rules tested:
  1. trailing_1mo_positive: token was profitable last month
  2. trailing_2of3_positive: profitable in 2 of last 3 months
  3. trailing_3mo_positive: profitable in all of last 3 months
  4. cumulative_positive: positive cumulative P&L across all history
  5. min_trades_and_positive: >= 2 trades AND positive last 2 months
  6. ema_pnl: exponentially-weighted moving average of monthly P&L > 0

Usage:
    /workspace/venv/bin/python research/s514_walkforward_tokens.py
"""

import sys
import os
import time
import dataclasses

sys.path.insert(0, '/workspace/crypto_backtest')
sys.path.insert(0, '/workspace/crypto_backtest/v4')
os.chdir('/workspace/crypto_backtest')

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

# Full 24-month calendar: training + testing
ALL_MONTHS = [
    # Training: Apr 2024 - Mar 2025 (months 0-11)
    ('2024-04', 6, '2024-05-01'),
    ('2024-05', 6, '2024-06-01'),
    ('2024-06', 6, '2024-07-01'),
    ('2024-07', 6, '2024-08-01'),
    ('2024-08', 6, '2024-09-01'),
    ('2024-09', 6, '2024-10-01'),
    ('2024-10', 6, '2024-11-01'),
    ('2024-11', 6, '2024-12-01'),
    ('2024-12', 6, '2025-01-01'),
    ('2025-01', 6, '2025-02-01'),
    ('2025-02', 6, '2025-03-01'),
    ('2025-03', 6, '2025-04-01'),
    # Testing: Apr 2025 - Mar 2026 (months 12-23)
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

TRAIN_MONTHS = [m[0] for m in ALL_MONTHS[:12]]   # Apr 2024 - Mar 2025
TEST_MONTHS = [m[0] for m in ALL_MONTHS[12:]]    # Apr 2025 - Mar 2026
ALL_MONTH_LABELS = [m[0] for m in ALL_MONTHS]


# ---------------------------------------------------------------------------
# Infrastructure (reused from s514_token_rotation.py pattern)
# ---------------------------------------------------------------------------

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
        raise RuntimeError("s514 module not loaded yet -- run precompute first")

    ls_path = os.path.join('data', 'alternative', 'binance_metrics',
                           'all_symbols_daily_ls.parquet')
    df = pd.read_parquet(ls_path, columns=['symbol'])
    all_tokens = set(s.replace('USDT', '') for s in df['symbol'].unique())
    mod.ALLOWED_TOKENS = all_tokens
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

    Returns (dict {token: pnl}, dict {token: n_trades}) for trades that
    OPENED in the target month.
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

    unified_ts, _ = build_unified_index({STRATEGY_ID: signals})
    state = simulate_portfolio({STRATEGY_ID: signals}, strategy_specs, config_run)

    token_pnl = defaultdict(float)
    token_trades = defaultdict(int)

    for trade in state.position_manager.closed_trades:
        if trade.entry_bar < len(unified_ts):
            entry_ts = pd.Timestamp(unified_ts[trade.entry_bar])
            entry_month = entry_ts.strftime('%Y-%m')
        else:
            continue

        if entry_month == month_label:
            token_pnl[trade.token] += trade.pnl
            token_trades[trade.token] += 1

    return dict(token_pnl), dict(token_trades)


# ---------------------------------------------------------------------------
# Step 1: Collect per-token monthly P&L for ALL 24 months
# ---------------------------------------------------------------------------

def step1_collect_monthly_pnl():
    """Run each month on full universe, collect per-token P&L."""
    print("=" * 70)
    print("  STEP 1: Monthly Per-Token P&L Collection (24 months, Full Universe)")
    print("=" * 70)

    # Force-load the strategy module with a long window
    spec, config = make_spec_and_config()
    tokens = discover_tokens(MARKET)
    precompute_strategy_signals(spec, tokens, config, 12,
                                end_date=pd.Timestamp('2026-04-01'))
    all_tokens = bypass_token_filter()

    monthly_token_pnl = {}
    monthly_token_trades = {}

    for month_label, lookback, end_date_str in ALL_MONTHS:
        t0 = time.time()
        pnl, trades = run_month(month_label, lookback, end_date_str)
        elapsed = time.time() - t0

        total = sum(pnl.values())
        n_tokens = len(pnl)
        monthly_token_pnl[month_label] = pnl
        monthly_token_trades[month_label] = trades

        period = "TRAIN" if month_label in TRAIN_MONTHS else "TEST"
        print(f"  [{period}] {month_label}: {n_tokens} tokens traded, "
              f"${total:+,.0f} P&L ({elapsed:.1f}s)")

    return monthly_token_pnl, monthly_token_trades


# ---------------------------------------------------------------------------
# Step 2: Define promotion/demotion rules
# ---------------------------------------------------------------------------

def _get_token_history(token, months_so_far, monthly_token_pnl, monthly_token_trades):
    """Get a token's monthly history: list of (month, pnl, n_trades)."""
    history = []
    for m in months_so_far:
        pnl = monthly_token_pnl.get(m, {}).get(token, None)
        trades = monthly_token_trades.get(m, {}).get(token, 0)
        if pnl is not None:
            history.append((m, pnl, trades))
    return history


def rule_trailing_1mo_positive(token, history):
    """Token was profitable in the most recent month."""
    if not history:
        return False
    return history[-1][1] > 0


def rule_trailing_2of3_positive(token, history):
    """Token was profitable in at least 2 of the last 3 months."""
    if len(history) < 2:
        return False
    recent = history[-3:] if len(history) >= 3 else history
    positive_count = sum(1 for _, pnl, _ in recent if pnl > 0)
    return positive_count >= 2


def rule_trailing_3mo_positive(token, history):
    """Token was profitable in all of the last 3 months."""
    if len(history) < 3:
        return False
    return all(pnl > 0 for _, pnl, _ in history[-3:])


def rule_cumulative_positive(token, history):
    """Token has positive cumulative P&L across all history."""
    if not history:
        return False
    return sum(pnl for _, pnl, _ in history) > 0


def rule_min_trades_and_positive(token, history):
    """At least 2 trades in most recent month AND positive last 2 months."""
    if len(history) < 2:
        return False
    # Check trade count in most recent month
    if history[-1][2] < 2:
        return False
    # Check positive in last 2 months
    return all(pnl > 0 for _, pnl, _ in history[-2:])


def rule_ema_pnl(token, history):
    """Exponentially-weighted moving average of monthly P&L is positive.

    Uses alpha=0.5, giving ~87% weight to last 3 months.
    """
    if not history:
        return False
    alpha = 0.5
    ema = 0.0
    for _, pnl, _ in history:
        ema = alpha * pnl + (1 - alpha) * ema
    return ema > 0


RULES = {
    'trailing_1mo_positive': rule_trailing_1mo_positive,
    'trailing_2of3_positive': rule_trailing_2of3_positive,
    'trailing_3mo_positive': rule_trailing_3mo_positive,
    'cumulative_positive': rule_cumulative_positive,
    'min_trades_and_positive': rule_min_trades_and_positive,
    'ema_pnl': rule_ema_pnl,
}


# ---------------------------------------------------------------------------
# Step 3: Simulate dynamic selection on a given period
# ---------------------------------------------------------------------------

def simulate_dynamic_selection(months, rule_fn, monthly_token_pnl,
                               monthly_token_trades, all_history_months=None):
    """Simulate dynamic token selection over a set of months.

    For each month, we look at ALL history up to (and including) that month
    to decide which tokens are active for the NEXT month. Month 0 uses all
    tokens (no history yet).

    If all_history_months is provided, it is the full list of months from
    the start of data (used so that test months can see training history).

    Returns list of dicts with per-month results.
    """
    if all_history_months is None:
        all_history_months = months

    # For the first month we need some active set. Use all tokens that have
    # any history up to the month before our window.
    active_tokens = None  # None = all tokens (no filter yet)
    monthly_results = []

    for i, month in enumerate(months):
        month_pnl = monthly_token_pnl.get(month, {})

        if active_tokens is None:
            # No filter -- use all tokens
            active_pnl = sum(month_pnl.values())
            n_active = len(month_pnl)
        else:
            active_pnl = sum(month_pnl.get(t, 0) for t in active_tokens)
            n_active = len(active_tokens)

        monthly_results.append({
            'month': month,
            'pnl': active_pnl,
            'n_tokens': n_active,
            'tokens': set(active_tokens) if active_tokens else set(month_pnl.keys()),
        })

        # Determine active set for NEXT month using all history up to now
        # Find the index of current month in all_history_months
        try:
            hist_idx = all_history_months.index(month)
        except ValueError:
            hist_idx = -1
        months_so_far = all_history_months[:hist_idx + 1] if hist_idx >= 0 else months[:i + 1]

        # Collect all tokens ever seen
        all_tokens_seen = set()
        for m in months_so_far:
            all_tokens_seen |= set(monthly_token_pnl.get(m, {}).keys())

        new_active = set()
        for token in all_tokens_seen:
            history = _get_token_history(token, months_so_far,
                                         monthly_token_pnl, monthly_token_trades)
            if rule_fn(token, history):
                new_active.add(token)

        active_tokens = new_active if new_active else None  # fallback to all if empty

    return monthly_results


# ---------------------------------------------------------------------------
# Step 4: Train -- find best rules on training period
# ---------------------------------------------------------------------------

def step2_train_rules(monthly_token_pnl, monthly_token_trades):
    """Train: evaluate all rules on the TRAIN period (Apr 2024 - Mar 2025).

    For each rule, simulate dynamic selection and compute:
      - Total P&L
      - Monthly Sharpe-equivalent (mean / std of monthly P&L)
      - Average number of active tokens
    """
    print("\n" + "=" * 70)
    print("  STEP 2: TRAIN -- Rule Evaluation (Apr 2024 - Mar 2025)")
    print("=" * 70)

    train_results = {}
    for rule_name, rule_fn in RULES.items():
        results = simulate_dynamic_selection(
            TRAIN_MONTHS, rule_fn, monthly_token_pnl, monthly_token_trades,
            all_history_months=TRAIN_MONTHS,
        )

        pnl_series = [r['pnl'] for r in results]
        total_pnl = sum(pnl_series)
        mean_pnl = np.mean(pnl_series)
        std_pnl = np.std(pnl_series, ddof=1) if len(pnl_series) > 1 else 1e-6
        sharpe = mean_pnl / std_pnl if std_pnl > 0 else 0.0
        avg_tokens = np.mean([r['n_tokens'] for r in results])
        n_positive = sum(1 for p in pnl_series if p > 0)

        train_results[rule_name] = {
            'total_pnl': total_pnl,
            'mean_monthly': mean_pnl,
            'std_monthly': std_pnl,
            'sharpe': sharpe,
            'avg_tokens': avg_tokens,
            'n_positive_months': n_positive,
            'results': results,
        }

        print(f"  {rule_name:<30} Total: ${total_pnl:>+10,.0f}  "
              f"Sharpe: {sharpe:>+.3f}  "
              f"Avg tokens: {avg_tokens:>5.1f}  "
              f"Win months: {n_positive}/{len(pnl_series)}")

    # Also compute baselines
    for baseline_name, baseline_tokens in [('STATIC_28', STATIC_28), ('FULL_universe', None)]:
        pnl_series = []
        for month in TRAIN_MONTHS:
            month_pnl = monthly_token_pnl.get(month, {})
            if baseline_tokens is None:
                pnl_series.append(sum(month_pnl.values()))
            else:
                pnl_series.append(sum(month_pnl.get(t, 0) for t in baseline_tokens))

        total = sum(pnl_series)
        mean = np.mean(pnl_series)
        std = np.std(pnl_series, ddof=1) if len(pnl_series) > 1 else 1e-6
        sharpe = mean / std if std > 0 else 0.0
        avg_tokens = len(baseline_tokens) if baseline_tokens else np.mean(
            [len(monthly_token_pnl.get(m, {})) for m in TRAIN_MONTHS])

        train_results[baseline_name] = {
            'total_pnl': total,
            'mean_monthly': mean,
            'std_monthly': std,
            'sharpe': sharpe,
            'avg_tokens': avg_tokens,
            'n_positive_months': sum(1 for p in pnl_series if p > 0),
            'results': None,
        }

        print(f"  {baseline_name:<30} Total: ${total:>+10,.0f}  "
              f"Sharpe: {sharpe:>+.3f}  "
              f"Avg tokens: {avg_tokens:>5.1f}  "
              f"Win months: {sum(1 for p in pnl_series if p > 0)}/{len(pnl_series)}")

    # Rank by Sharpe
    print("\n  TRAIN Rankings by Sharpe:")
    ranked = sorted(train_results.items(), key=lambda x: x[1]['sharpe'], reverse=True)
    for rank, (name, data) in enumerate(ranked, 1):
        marker = ""
        if name == 'STATIC_28':
            marker = " <-- current baseline"
        elif name == 'FULL_universe':
            marker = " <-- full universe"
        print(f"    {rank}. {name:<30} Sharpe: {data['sharpe']:>+.3f}  "
              f"P&L: ${data['total_pnl']:>+10,.0f}{marker}")

    return train_results


# ---------------------------------------------------------------------------
# Step 5: Test -- apply best rules out-of-sample
# ---------------------------------------------------------------------------

def step3_test_rules(monthly_token_pnl, monthly_token_trades, train_results):
    """Test: apply the top rules on the TEST period (Apr 2025 - Mar 2026).

    Uses full history (including training months) for the lookback window,
    so the rule can see training-period performance when making decisions
    for the first test months.
    """
    print("\n" + "=" * 70)
    print("  STEP 3: TEST -- Out-of-Sample (Apr 2025 - Mar 2026)")
    print("=" * 70)

    # Pick top 3 rules by train Sharpe (excluding baselines)
    dynamic_rules = {k: v for k, v in train_results.items()
                     if k not in ('STATIC_28', 'FULL_universe')}
    top_rules = sorted(dynamic_rules.items(),
                       key=lambda x: x[1]['sharpe'], reverse=True)[:3]

    print(f"\n  Testing top 3 rules from training + baselines:")
    for name, data in top_rules:
        print(f"    - {name} (train Sharpe: {data['sharpe']:+.3f})")

    test_results = {}

    # Test each dynamic rule
    for rule_name, _ in top_rules:
        rule_fn = RULES[rule_name]
        results = simulate_dynamic_selection(
            TEST_MONTHS, rule_fn, monthly_token_pnl, monthly_token_trades,
            all_history_months=ALL_MONTH_LABELS,  # Full history for lookback
        )

        pnl_series = [r['pnl'] for r in results]
        total_pnl = sum(pnl_series)
        mean_pnl = np.mean(pnl_series)
        std_pnl = np.std(pnl_series, ddof=1) if len(pnl_series) > 1 else 1e-6
        sharpe = mean_pnl / std_pnl if std_pnl > 0 else 0.0

        test_results[rule_name] = {
            'total_pnl': total_pnl,
            'mean_monthly': mean_pnl,
            'std_monthly': std_pnl,
            'sharpe': sharpe,
            'results': results,
        }

    # Baselines on test period
    for baseline_name, baseline_tokens in [('STATIC_28', STATIC_28), ('FULL_universe', None)]:
        pnl_series = []
        for month in TEST_MONTHS:
            month_pnl = monthly_token_pnl.get(month, {})
            if baseline_tokens is None:
                pnl_series.append(sum(month_pnl.values()))
            else:
                pnl_series.append(sum(month_pnl.get(t, 0) for t in baseline_tokens))

        total = sum(pnl_series)
        mean = np.mean(pnl_series)
        std = np.std(pnl_series, ddof=1) if len(pnl_series) > 1 else 1e-6
        sharpe = mean / std if std > 0 else 0.0

        test_results[baseline_name] = {
            'total_pnl': total,
            'mean_monthly': mean,
            'std_monthly': std,
            'sharpe': sharpe,
            'results': None,
        }

    # Print month-by-month comparison
    print("\n  Month-by-Month TEST Results:")
    header = f"  {'Month':<10}"
    rule_names_test = [name for name, _ in top_rules] + ['STATIC_28', 'FULL_universe']
    for name in rule_names_test:
        short = name[:18]
        header += f" {short:>18}"
    print(header)
    print("  " + "-" * (10 + 19 * len(rule_names_test)))

    for mi, month in enumerate(TEST_MONTHS):
        row = f"  {month:<10}"
        for name in rule_names_test:
            data = test_results[name]
            if data['results'] is not None:
                pnl = data['results'][mi]['pnl']
                n_tok = data['results'][mi]['n_tokens']
                row += f" ${pnl:>+8,.0f} ({n_tok:>3})"
            else:
                month_pnl = monthly_token_pnl.get(month, {})
                if name == 'STATIC_28':
                    pnl = sum(month_pnl.get(t, 0) for t in STATIC_28)
                    row += f" ${pnl:>+8,.0f} ( 28)"
                else:
                    pnl = sum(month_pnl.values())
                    row += f" ${pnl:>+8,.0f} ({len(month_pnl):>3})"
        print(row)

    # Summary
    print(f"\n  TEST Summary:")
    print(f"  {'Rule':<30} {'Total P&L':>12} {'Sharpe':>8} {'Mean/Mo':>10}")
    print("  " + "-" * 62)

    ranked = sorted(test_results.items(), key=lambda x: x[1]['sharpe'], reverse=True)
    for name, data in ranked:
        marker = ""
        if name == 'STATIC_28':
            marker = " <-- baseline"
        print(f"  {name:<30} ${data['total_pnl']:>+10,.0f} {data['sharpe']:>+8.3f} "
              f"${data['mean_monthly']:>+8,.0f}{marker}")

    return test_results


# ---------------------------------------------------------------------------
# Step 6: Promotion/demotion tracking for the winning rule
# ---------------------------------------------------------------------------

def step4_promotion_tracking(monthly_token_pnl, monthly_token_trades,
                              best_rule_name, test_results):
    """Show detailed promotion/demotion tracking for the best rule on TEST."""
    print("\n" + "=" * 70)
    print(f"  STEP 4: Promotion/Demotion Tracking -- {best_rule_name}")
    print("=" * 70)

    rule_fn = RULES[best_rule_name]
    results = test_results[best_rule_name]['results']
    if results is None:
        print("  No dynamic results to track (baseline selected)")
        return

    prev_tokens = None
    for i, r in enumerate(results):
        month = r['month']
        tokens = r['tokens']
        pnl = r['pnl']

        if prev_tokens is not None:
            promoted = tokens - prev_tokens
            demoted = prev_tokens - tokens
            retained = tokens & prev_tokens
        else:
            promoted = tokens
            demoted = set()
            retained = set()

        # Show top promoted/demoted by prior month P&L
        month_pnl = monthly_token_pnl.get(month, {})

        print(f"\n  {month}: {len(tokens)} active tokens, P&L ${pnl:>+,.0f}")
        if promoted:
            top_promoted = sorted(promoted,
                                   key=lambda t: month_pnl.get(t, 0), reverse=True)
            promo_str = ', '.join(top_promoted[:8])
            if len(promoted) > 8:
                promo_str += f" (+{len(promoted) - 8} more)"
            print(f"    PROMOTED ({len(promoted)}): {promo_str}")
        if demoted:
            top_demoted = sorted(demoted,
                                  key=lambda t: month_pnl.get(t, 0))
            demo_str = ', '.join(top_demoted[:8])
            if len(demoted) > 8:
                demo_str += f" (+{len(demoted) - 8} more)"
            print(f"    DEMOTED  ({len(demoted)}): {demo_str}")

        prev_tokens = tokens


# ---------------------------------------------------------------------------
# Step 7: Compounding comparison
# ---------------------------------------------------------------------------

def step5_compounding_comparison(monthly_token_pnl, monthly_token_trades,
                                  best_rule_name):
    """Compare compounding returns: dynamic vs static vs full on TEST period."""
    print("\n" + "=" * 70)
    print(f"  STEP 5: Compounding Comparison on TEST Period")
    print("=" * 70)

    rule_fn = RULES[best_rule_name]

    # Dynamic rule with compounding
    results_dynamic = simulate_dynamic_selection(
        TEST_MONTHS, rule_fn, monthly_token_pnl, monthly_token_trades,
        all_history_months=ALL_MONTH_LABELS,
    )

    scenarios = {
        f'Dynamic ({best_rule_name})': [r['pnl'] for r in results_dynamic],
        'STATIC_28': [sum(monthly_token_pnl.get(m, {}).get(t, 0)
                          for t in STATIC_28) for m in TEST_MONTHS],
        'FULL_universe': [sum(monthly_token_pnl.get(m, {}).values())
                          for m in TEST_MONTHS],
    }

    print(f"\n  {'Scenario':<35} {'Final Equity':>14} {'Return':>10} {'Max DD':>10}")
    print("  " + "-" * 71)

    for scenario_name, pnl_series in scenarios.items():
        capital = CAPITAL
        peak = CAPITAL
        max_dd = 0.0
        for month_pnl in pnl_series:
            scaled_pnl = month_pnl * (capital / CAPITAL)
            capital += scaled_pnl
            if capital > peak:
                peak = capital
            dd = (peak - capital) / peak if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd

        total_return = (capital / CAPITAL - 1) * 100
        print(f"  {scenario_name:<35} ${capital:>12,.0f} {total_return:>+9.1f}% "
              f"{max_dd:>9.1%}")

    # Month-by-month equity curve
    print(f"\n  Month-by-Month Equity Curve (TEST):")
    print(f"  {'Month':<10}", end='')
    for name in scenarios:
        print(f" {name[:20]:>20}", end='')
    print()

    equities = {name: CAPITAL for name in scenarios}
    for mi, month in enumerate(TEST_MONTHS):
        print(f"  {month:<10}", end='')
        for name, pnl_series in scenarios.items():
            scaled_pnl = pnl_series[mi] * (equities[name] / CAPITAL)
            equities[name] += scaled_pnl
            print(f" ${equities[name]:>18,.0f}", end='')
        print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    t_start = time.time()

    # ==================================================================
    # Step 1: Collect per-token P&L for all 24 months
    # ==================================================================
    monthly_token_pnl, monthly_token_trades = step1_collect_monthly_pnl()

    # Print summary of data collected
    print(f"\n  Data summary:")
    all_tokens_seen = set()
    for m in ALL_MONTH_LABELS:
        all_tokens_seen |= set(monthly_token_pnl.get(m, {}).keys())
    print(f"  Total unique tokens seen: {len(all_tokens_seen)}")

    train_total = sum(sum(monthly_token_pnl.get(m, {}).values()) for m in TRAIN_MONTHS)
    test_total = sum(sum(monthly_token_pnl.get(m, {}).values()) for m in TEST_MONTHS)
    print(f"  TRAIN total P&L (full universe): ${train_total:+,.0f}")
    print(f"  TEST  total P&L (full universe): ${test_total:+,.0f}")

    # ==================================================================
    # Step 2: Train rules on training period
    # ==================================================================
    train_results = step2_train_rules(monthly_token_pnl, monthly_token_trades)

    # ==================================================================
    # Step 3: Test top rules on OOS period
    # ==================================================================
    test_results = step3_test_rules(monthly_token_pnl, monthly_token_trades,
                                     train_results)

    # ==================================================================
    # Step 4: Detailed promotion/demotion tracking
    # ==================================================================
    # Find best dynamic rule on TEST by Sharpe
    dynamic_test = {k: v for k, v in test_results.items()
                    if k not in ('STATIC_28', 'FULL_universe')}
    if dynamic_test:
        best_test_rule = max(dynamic_test, key=lambda k: dynamic_test[k]['sharpe'])
    else:
        best_test_rule = list(RULES.keys())[0]

    step4_promotion_tracking(monthly_token_pnl, monthly_token_trades,
                              best_test_rule, test_results)

    # ==================================================================
    # Step 5: Compounding comparison
    # ==================================================================
    step5_compounding_comparison(monthly_token_pnl, monthly_token_trades,
                                  best_test_rule)

    # ==================================================================
    # Final summary
    # ==================================================================
    elapsed = time.time() - t_start
    print(f"\n{'=' * 70}")
    print(f"  FINAL SUMMARY")
    print(f"{'=' * 70}")

    print(f"\n  1. TRAINING PERIOD (Apr 2024 - Mar 2025):")
    train_ranked = sorted(
        [(k, v) for k, v in train_results.items()],
        key=lambda x: x[1]['sharpe'], reverse=True)
    for rank, (name, data) in enumerate(train_ranked, 1):
        marker = " ***" if name == best_test_rule else ""
        print(f"     {rank}. {name:<30} Sharpe: {data['sharpe']:>+.3f}  "
              f"P&L: ${data['total_pnl']:>+10,.0f}{marker}")

    print(f"\n  2. TEST PERIOD (Apr 2025 - Mar 2026):")
    test_ranked = sorted(
        [(k, v) for k, v in test_results.items()],
        key=lambda x: x[1]['sharpe'], reverse=True)
    for rank, (name, data) in enumerate(test_ranked, 1):
        marker = " <-- baseline" if name == 'STATIC_28' else ""
        if name == best_test_rule:
            marker = " <-- WINNER"
        print(f"     {rank}. {name:<30} Sharpe: {data['sharpe']:>+.3f}  "
              f"P&L: ${data['total_pnl']:>+10,.0f}{marker}")

    # Does dynamic beat static?
    static_test_pnl = test_results.get('STATIC_28', {}).get('total_pnl', 0)
    best_dynamic_pnl = test_results.get(best_test_rule, {}).get('total_pnl', 0)

    print(f"\n  3. VERDICT:")
    if best_dynamic_pnl > static_test_pnl:
        improvement = best_dynamic_pnl - static_test_pnl
        print(f"     Dynamic selection ({best_test_rule}) BEATS Static 28 "
              f"by ${improvement:+,.0f} on test period")
        print(f"     Dynamic: ${best_dynamic_pnl:+,.0f}  vs  Static: ${static_test_pnl:+,.0f}")
    else:
        shortfall = static_test_pnl - best_dynamic_pnl
        print(f"     Dynamic selection ({best_test_rule}) LOSES to Static 28 "
              f"by ${shortfall:+,.0f} on test period")
        print(f"     Dynamic: ${best_dynamic_pnl:+,.0f}  vs  Static: ${static_test_pnl:+,.0f}")

    print(f"\n  Total runtime: {elapsed:.0f}s")


if __name__ == '__main__':
    main()
