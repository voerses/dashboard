#!/usr/bin/env python3
"""s514 Regime Conditioning Sweep — Does filtering by market regime improve returns?

Hypothesis: s514 is a contrarian MR signal (L/S divergence), short-dominant
(66% of P&L from shorts). Shorting against a strong uptrend is dangerous,
going long in a downtrend is dangerous. Regime conditioning should help.

Approach:
  1. Load s514 strategy via the engine's module cache
  2. For each regime filter config, monkey-patch the strategy function to
     zero out entries in unfavorable regimes
  3. Run full portfolio backtest via precompute_strategy_signals + simulate_portfolio
  4. Report comparative results

Regime constants (from v4/engine.py):
  CRISIS=0, QUIET=1, UPTREND=2, RANGE=3, DOWNTREND=4

Usage:
    /workspace/venv/bin/python research/s514_regime_sweep.py
"""

import sys
import os
import time
import dataclasses
import copy

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import numpy as np
import pandas as pd

from v4.config import PortfolioConfig, StrategySpec
from v4.signals import precompute_strategy_signals, discover_tokens, infer_data_end_date
from v4.simulator import simulate_portfolio
from v4.report import compute_portfolio_metrics, print_report
from v4.engine import (
    _load_strategy_fn, _STRATEGY_MODULE_CACHE, _STRATEGY_MODULE_LOCK,
    StrategyResult, CRISIS, QUIET, UPTREND, RANGE, DOWNTREND,
)

# ======================================================================
#  Configuration
# ======================================================================

STRATEGY_ID = 's514'
MARKET = 'perp'
CAPITAL = 100_000
MONTHS = 12
END_DATE_STR = '2026-04-01'

# Use s514's own token list
TOKENS = {
    'EIGEN', 'PENGU', 'INJ', 'LTC', 'ARB', 'SAND', 'AAVE', 'GUN',
    'RAYSOL', 'LINK', 'HBAR', 'RENDER', 'ETH', 'PIXEL', 'DOGE',
    'WLD', 'VANRY', 'ADA', 'AIOT', 'UNI', 'BTC', 'IP', 'NEAR',
    'ETC', 'MYX', 'PIPPIN', 'ZEN', 'RESOLV',
}

# ======================================================================
#  Regime filter configurations
# ======================================================================
# Each config is: (label, skip_short_regimes, skip_long_regimes, only_regimes, trail_override)
# - skip_short_regimes: set of regimes where shorts are zeroed
# - skip_long_regimes: set of regimes where longs are zeroed
# - only_regimes: if non-empty, only trade in these regimes (both sides)
# - trail_override: dict {regime: trail_mult} for regime-conditional trailing

FILTER_CONFIGS = [
    # 0. Baseline (no filter)
    (
        "BASELINE (no regime filter)",
        set(), set(), set(), {},
    ),
    # 1. Skip shorts in UPTREND
    (
        "Skip shorts in UPTREND",
        {UPTREND}, set(), set(), {},
    ),
    # 2. Skip longs in DOWNTREND
    (
        "Skip longs in DOWNTREND",
        set(), {DOWNTREND}, set(), {},
    ),
    # 3. Skip shorts in UPTREND + longs in DOWNTREND (counter-trend filter)
    (
        "Skip counter-trend (shorts in UP, longs in DOWN)",
        {UPTREND}, {DOWNTREND}, set(), {},
    ),
    # 4. Only trade in RANGE (both sides)
    (
        "Only trade in RANGE",
        set(), set(), {RANGE}, {},
    ),
    # 5. Only trade in RANGE + QUIET
    (
        "Only trade in RANGE + QUIET",
        set(), set(), {RANGE, QUIET}, {},
    ),
    # 6. Skip all in CRISIS
    (
        "Skip all in CRISIS",
        {CRISIS}, {CRISIS}, set(), {},
    ),
    # 7. Skip shorts in UPTREND + skip all in CRISIS
    (
        "Skip shorts in UPTREND + skip all in CRISIS",
        {UPTREND, CRISIS}, {CRISIS}, set(), {},
    ),
    # 8. Full counter-trend + crisis filter
    (
        "Full filter (counter-trend + crisis)",
        {UPTREND, CRISIS}, {DOWNTREND, CRISIS}, set(), {},
    ),
    # 9. Only trade in RANGE + QUIET + CRISIS (skip trending)
    (
        "Skip trending (only RANGE + QUIET + CRISIS)",
        set(), set(), {RANGE, QUIET, CRISIS}, {},
    ),
    # 10. Tighter trail in RANGE (1.5 ATR), wider elsewhere (2.0 ATR)
    (
        "Tighter trail in RANGE (1.5 vs 2.0 ATR)",
        set(), set(), set(), {RANGE: 1.5},
    ),
    # 11. Counter-trend filter + tighter trail in RANGE
    (
        "Counter-trend filter + tighter trail in RANGE",
        {UPTREND}, {DOWNTREND}, set(), {RANGE: 1.5},
    ),
]


# ======================================================================
#  Monkey-patching machinery
# ======================================================================

def make_regime_filtered_strategy(original_fn, skip_short_regimes, skip_long_regimes,
                                  only_regimes, trail_override):
    """Create a wrapper that filters entries by regime.

    The wrapper calls the original strategy, then zeros out entries based
    on the regime_1h array from the context.
    """
    def regime_filtered_strategy(ctx):
        result = original_fn(ctx)
        regime = ctx.regime_1h
        n = len(result.entry_mask)

        entry = result.entry_mask.copy()
        direction = result.direction.copy()

        # Only-regimes filter (both sides)
        if only_regimes:
            allowed = np.zeros(n, dtype=bool)
            for r in only_regimes:
                allowed |= (regime[:n] == r)
            entry &= allowed

        # Skip shorts in specific regimes
        for r in skip_short_regimes:
            mask = (regime[:n] == r) & (direction == -1)
            entry[mask] = False

        # Skip longs in specific regimes
        for r in skip_long_regimes:
            mask = (regime[:n] == r) & (direction == 1)
            entry[mask] = False

        # Trail override: create per-bar trail array if regime-conditional
        trail_mult = result.trail_mult
        if trail_override:
            if isinstance(trail_mult, (int, float)):
                base_trail = float(trail_mult)
                trail_arr = np.full(n, base_trail, dtype=np.float64)
            else:
                trail_arr = np.array(trail_mult[:n], dtype=np.float64)
            for r, t in trail_override.items():
                trail_arr[regime[:n] == r] = t
            trail_mult = trail_arr

        # Rebuild StrategyResult with filtered entry + potentially modified trail
        # Use dataclasses.fields to copy all fields generically
        field_vals = {}
        for f in dataclasses.fields(result):
            field_vals[f.name] = getattr(result, f.name)
        field_vals['entry_mask'] = entry
        field_vals['direction'] = direction
        field_vals['trail_mult'] = trail_mult
        return StrategyResult(**field_vals)

    return regime_filtered_strategy


def patch_strategy(original_fn, skip_short_regimes, skip_long_regimes,
                   only_regimes, trail_override):
    """Monkey-patch the cached s514 module's strategy function."""
    filtered_fn = make_regime_filtered_strategy(
        original_fn, skip_short_regimes, skip_long_regimes,
        only_regimes, trail_override,
    )
    with _STRATEGY_MODULE_LOCK:
        mod = _STRATEGY_MODULE_CACHE.get(STRATEGY_ID)
        if mod is not None:
            mod.strategy = filtered_fn


def restore_strategy(original_fn):
    """Restore the original strategy function."""
    with _STRATEGY_MODULE_LOCK:
        mod = _STRATEGY_MODULE_CACHE.get(STRATEGY_ID)
        if mod is not None:
            mod.strategy = original_fn


# ======================================================================
#  Backtest runner
# ======================================================================

def run_backtest(label):
    """Run a single portfolio backtest, return metrics + extra info."""
    end_date = pd.Timestamp(END_DATE_STR)
    spec = StrategySpec(
        strategy_id=STRATEGY_ID,
        weight=1.0,
        max_positions=min(len(TOKENS), 50),
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
        max_portfolio_positions=min(len(TOKENS), 50),
        strategies=[spec],
    )

    tokens = discover_tokens(MARKET)
    t0 = time.time()
    signals = precompute_strategy_signals(spec, tokens, config, MONTHS, end_date=end_date)
    t1 = time.time()

    # Filter to allowed tokens
    filtered_signals = {t: s for t, s in signals.items() if t in TOKENS}

    print(f"\n{'='*70}")
    print(f"  {label}")
    print(f"  Tokens with signals: {len(filtered_signals)}/{len(TOKENS)}")
    print(f"  Signal compute: {t1 - t0:.1f}s")
    print(f"{'='*70}")

    if not filtered_signals:
        print("  No signals!")
        return None, None

    strategy_specs = {STRATEGY_ID: spec}
    state = simulate_portfolio({STRATEGY_ID: filtered_signals}, strategy_specs, config)
    t2 = time.time()

    n_trades = len(state.position_manager.closed_trades)
    print(f"  Trades: {n_trades} ({t2 - t1:.1f}s simulation)")

    if n_trades == 0:
        print("  No trades produced!")
        return None, None

    metrics, extra_info, eq_daily = compute_portfolio_metrics(state, CAPITAL)
    print_report(metrics, extra_info, CAPITAL, [STRATEGY_ID])

    return metrics, extra_info


# ======================================================================
#  Main sweep
# ======================================================================

def main():
    print("=" * 70)
    print("  s514 Regime Conditioning Sweep")
    print("  Hypothesis: skip counter-trend entries to improve risk-adjusted returns")
    print(f"  Period: {MONTHS}mo ending {END_DATE_STR}, Capital: ${CAPITAL:,.0f}")
    print("=" * 70)
    print(f"\n  Regime constants: CRISIS={CRISIS}, QUIET={QUIET}, "
          f"UPTREND={UPTREND}, RANGE={RANGE}, DOWNTREND={DOWNTREND}")

    # Force-load the strategy module into cache
    _load_strategy_fn(STRATEGY_ID)
    with _STRATEGY_MODULE_LOCK:
        mod = _STRATEGY_MODULE_CACHE.get(STRATEGY_ID)
    original_fn = mod.strategy

    results = {}

    for i, (label, skip_short, skip_long, only, trail_ovr) in enumerate(FILTER_CONFIGS):
        print(f"\n\n{'#'*70}")
        print(f"  CONFIG {i}: {label}")
        print(f"  skip_short_regimes={skip_short}, skip_long_regimes={skip_long}")
        print(f"  only_regimes={only}, trail_override={trail_ovr}")
        print(f"{'#'*70}")

        # Patch
        if i == 0:
            # Baseline — no patching needed, but restore to be safe
            restore_strategy(original_fn)
        else:
            patch_strategy(original_fn, skip_short, skip_long, only, trail_ovr)

        try:
            m, ei = run_backtest(label)
            if m is not None:
                results[label] = (m, ei)
        finally:
            restore_strategy(original_fn)

    # ======================================================================
    #  Summary table
    # ======================================================================
    print(f"\n\n{'='*120}")
    print("  REGIME CONDITIONING SWEEP — SUMMARY")
    print(f"{'='*120}")
    header = (f"  {'#':>2}  {'Config':<52} {'Sharpe':>7} {'Calmar':>7} "
              f"{'MaxDD':>8} {'Return':>10} {'Trades':>7} {'WinRate':>8}")
    print(header)
    print("  " + "-" * 116)

    baseline_sharpe = None
    baseline_calmar = None
    baseline_return = None

    for i, (label, *_) in enumerate(FILTER_CONFIGS):
        if label not in results:
            print(f"  {i:>2}  {label:<52} {'N/A':>7} {'N/A':>7} "
                  f"{'N/A':>8} {'N/A':>10} {'N/A':>7} {'N/A':>8}")
            continue

        m, ei = results[label]
        sharpe = m.sharpe_ratio
        calmar = m.calmar_ratio
        maxdd = m.max_drawdown_pct
        ret = m.total_return_pct
        trades = m.total_trades
        wr = m.win_rate_pct if hasattr(m, 'win_rate_pct') else 0.0

        if i == 0:
            baseline_sharpe = sharpe
            baseline_calmar = calmar
            baseline_return = ret

        # Delta markers vs baseline
        s_delta = ""
        c_delta = ""
        r_delta = ""
        if baseline_sharpe is not None and i > 0:
            s_diff = sharpe - baseline_sharpe
            c_diff = calmar - baseline_calmar
            r_diff = ret - baseline_return
            s_delta = f" ({s_diff:+.2f})"
            c_delta = f" ({c_diff:+.2f})"
            r_delta = f" ({r_diff:+.1f})"

        print(f"  {i:>2}  {label:<52} {sharpe:>7.2f}{s_delta} {calmar:>7.2f}{c_delta} "
              f"{maxdd:>7.1f}% {ret:>+9.1f}%{r_delta} {trades:>7} {wr:>7.1f}%")

    # Best by Sharpe
    if results:
        best_sharpe_label = max(results.keys(), key=lambda k: results[k][0].sharpe_ratio)
        best_calmar_label = max(results.keys(), key=lambda k: results[k][0].calmar_ratio)
        print(f"\n  BEST BY SHARPE: {best_sharpe_label} "
              f"(Sharpe={results[best_sharpe_label][0].sharpe_ratio:.2f})")
        print(f"  BEST BY CALMAR: {best_calmar_label} "
              f"(Calmar={results[best_calmar_label][0].calmar_ratio:.2f})")

    # Analysis
    print(f"\n{'='*70}")
    print("  ANALYSIS")
    print(f"{'='*70}")
    print("  Key questions:")
    print("  1. Does skipping shorts in UPTREND improve Sharpe/Calmar?")
    print("  2. Does skipping longs in DOWNTREND help?")
    print("  3. Is RANGE-only trading viable (enough trades)?")
    print("  4. Does tighter trailing in RANGE capture MR profits better?")
    print("  5. Which config offers the best risk-adjusted improvement?")

    print("\nDone.")


if __name__ == '__main__':
    main()
