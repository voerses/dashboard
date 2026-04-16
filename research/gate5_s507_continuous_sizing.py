#!/usr/bin/env python3
"""
Gate 5 — s507 Continuous Sizing Test
=====================================
Address THRESHOLD fragility: s507 uses binary z-score threshold (2.5) for entry.
At +/-20%, Calmar degrades >30%.

Hypothesis: graduated sizing smooths the cliff by lowering entry threshold to 1.5
but deploying most capital only at higher z-scores:

  z-score 1.5-2.0: size_multiplier = 0.3 (low conviction)
  z-score 2.0-2.5: size_multiplier = 0.6 (medium conviction)
  z-score 2.5-3.0: size_multiplier = 1.0 (high conviction)
  z-score >3.0:    size_multiplier = 1.0 (max)

Test plan:
  1. Baseline s507 (binary THRESHOLD=2.5)
  2. Continuous variant (graduated sizing, LOW_THRESHOLD=1.5)
  3. Sensitivity: LOW_THRESHOLD at 1.2 (-20%) and 1.8 (+20%)
  4. Compare Sharpe, Calmar, MaxDD, trades
"""

import sys
import os
import time
import warnings
import copy

warnings.filterwarnings("ignore")

sys.path.insert(0, '/workspace/crypto_backtest')
sys.path.insert(0, '/workspace/crypto_backtest/v4')
os.chdir('/workspace/crypto_backtest')

import numpy as np
import pandas as pd

from v4.config import PortfolioConfig, StrategySpec
from v4.signals import precompute_strategy_signals, infer_data_end_date
from v4.simulator import simulate_portfolio
from v4.universe import get_all_tradeable
from v4.engine import _load_strategy_fn, _STRATEGY_MODULE_CACHE, _STRATEGY_MODULE_LOCK

import strategies.s506_ls_divergence as s506_mod
import strategies.s507_ls_div_fixed_tp as s507_mod


# ======================================================================
#  Metrics computation (same as gate5_s507_param_sensitivity.py)
# ======================================================================

def compute_metrics(equity_snapshots, capital=100_000):
    eq = np.array([s[1] for s in equity_snapshots], dtype=np.float64)
    ts = np.array([s[0] for s in equity_snapshots])
    n = len(eq)
    if n < 100:
        return None

    total_ret = (eq[-1] / eq[0] - 1)
    peak = np.maximum.accumulate(eq)
    dd = (eq - peak) / peak
    max_dd = dd.min()

    rets = np.diff(eq) / np.maximum(eq[:-1], 1e-10)
    mean_ret = np.mean(rets)
    std_ret = np.std(rets)
    sharpe = mean_ret / max(std_ret, 1e-10) * np.sqrt(8760)

    neg_rets = rets[rets < 0]
    downside = np.sqrt(np.mean(neg_rets**2)) if len(neg_rets) > 0 else 1e-10
    sortino = mean_ret / downside * np.sqrt(8760)

    hours = n
    years = hours / 8760.0
    ann_ret = (1 + total_ret) ** (1.0 / max(years, 0.01)) - 1 if total_ret > -1 else total_ret
    calmar = ann_ret / max(abs(max_dd), 1e-10)

    return {
        'total_return': total_ret * 100,
        'ann_return': ann_ret * 100,
        'max_dd': max_dd * 100,
        'sharpe': sharpe,
        'sortino': sortino,
        'calmar': calmar,
        'n_bars': n,
        'final_equity': float(eq[-1]),
    }


# ======================================================================
#  Cached module references
# ======================================================================
_cached_s506 = None
_cached_s507 = None


def _ensure_cached_modules():
    global _cached_s506, _cached_s507
    with _STRATEGY_MODULE_LOCK:
        if 's507' in _STRATEGY_MODULE_CACHE:
            _cached_s507 = _STRATEGY_MODULE_CACHE['s507']
        if 's506' in _STRATEGY_MODULE_CACHE:
            _cached_s506 = _STRATEGY_MODULE_CACHE['s506']


def _patch_s506(param_name, value):
    """Patch a parameter on ALL copies of s506."""
    setattr(s506_mod, param_name, value)
    if _cached_s506 is not None and _cached_s506 is not s506_mod:
        setattr(_cached_s506, param_name, value)
    sm = sys.modules.get('strategies.s506_ls_divergence')
    if sm is not None and sm is not s506_mod:
        setattr(sm, param_name, value)


def _patch_s507(param_name, value):
    """Patch a parameter on ALL copies of s507."""
    setattr(s507_mod, param_name, value)
    if _cached_s507 is not None and _cached_s507 is not s507_mod:
        setattr(_cached_s507, param_name, value)
    sm = sys.modules.get('strategies.s507_ls_div_fixed_tp')
    if sm is not None and sm is not s507_mod:
        setattr(sm, param_name, value)


# ======================================================================
#  Graduated sizing: monkey-patch the s507 strategy function
# ======================================================================

# Save originals to restore later
_original_s506_strategy = s506_mod.strategy
_original_s507_strategy = s507_mod.strategy

# Graduated sizing thresholds (configurable for sensitivity sweep)
_LOW_THRESHOLD = 1.5
_MID_THRESHOLD = 2.0
_HIGH_THRESHOLD = 2.5
_MAX_THRESHOLD = 3.0

_SIZE_LOW = 0.3
_SIZE_MID = 0.6
_SIZE_HIGH = 1.0


def _graduated_s506_strategy(ctx):
    """Modified s506 strategy with graduated sizing based on z-score magnitude.

    Instead of binary entry at z=2.5, enter at z=1.5 with graduated size:
      1.5-2.0: 0.3x  |  2.0-2.5: 0.6x  |  2.5+: 1.0x
    """
    from engine import StrategyContext, StrategyResult, MarketType, rolling_zscore

    s506_mod._load_ls_data()

    close = ctx.ind_1h['close']
    n = len(close)
    symbol = ctx.ticker + "USDT"

    if symbol not in s506_mod._ls_cache:
        return StrategyResult(
            entry_mask=np.zeros(n, dtype=bool),
            direction=np.zeros(n, dtype=np.int8),
            market_type=s506_mod.MARKET,
            leverage=s506_mod.LEVERAGE,
            stop_mult=s506_mod.STOP_MULT,
            trail_mult=s506_mod.TRAIL_MULT,
            target_mult=999,
            no_stop_bars=s506_mod.NO_STOP_BARS,
            min_hold=s506_mod.MIN_HOLD,
            max_hold=s506_mod.MAX_HOLD,
            edge=s506_mod.EDGE,
            name='s506_ls_divergence_continuous',
            breakeven_atr=0.5,
        )

    divergence = s506_mod._get_divergence_aligned(symbol, ctx.idx_1h)
    div_zscore = rolling_zscore(divergence, s506_mod.ZSCORE_WINDOW)
    div_zscore = np.nan_to_num(div_zscore, nan=0.0)

    # Day boundary detection (same as original)
    dates = ctx.idx_1h.normalize()
    day_change = np.zeros(n, dtype=bool)
    day_change[0] = True
    day_change[1:] = dates[1:] != dates[:-1]

    abs_z = np.abs(div_zscore)

    # Entry at LOW_THRESHOLD instead of THRESHOLD
    short_level = div_zscore > _LOW_THRESHOLD
    short_prev = np.roll(short_level, 1)
    short_prev[0] = False
    short_signal = short_level & (~short_prev | day_change)

    long_level = div_zscore < -_LOW_THRESHOLD
    long_prev = np.roll(long_level, 1)
    long_prev[0] = False
    long_signal = long_level & (~long_prev | day_change)

    long_signal = long_signal & day_change
    short_signal = short_signal & day_change

    # Compose entry and direction (both directions)
    entry = long_signal | short_signal
    direction = np.where(long_signal, 1,
                         np.where(short_signal, -1, 0)).astype(np.int8)

    # Graduated size_multiplier based on z-score magnitude
    size_mult = np.zeros(n, dtype=np.float64)
    size_mult[(abs_z >= _LOW_THRESHOLD) & (abs_z < _MID_THRESHOLD)] = _SIZE_LOW
    size_mult[(abs_z >= _MID_THRESHOLD) & (abs_z < _HIGH_THRESHOLD)] = _SIZE_MID
    size_mult[abs_z >= _HIGH_THRESHOLD] = _SIZE_HIGH

    # Warmup guard
    entry[:s506_mod.WARMUP] = False

    return StrategyResult(
        entry_mask=entry,
        direction=direction,
        market_type=s506_mod.MARKET,
        leverage=s506_mod.LEVERAGE,
        stop_mult=s506_mod.STOP_MULT,
        trail_mult=s506_mod.TRAIL_MULT,
        target_mult=999,
        no_stop_bars=s506_mod.NO_STOP_BARS,
        min_hold=s506_mod.MIN_HOLD,
        max_hold=s506_mod.MAX_HOLD,
        edge=s506_mod.EDGE,
        name='s506_ls_divergence_continuous',
        breakeven_atr=0.5,
        size_multiplier=size_mult,
    )


def _graduated_s507_strategy(ctx):
    """s507 overlay wrapping the graduated s506 — adds fixed TP."""
    result = _graduated_s506_strategy(ctx)
    from engine import StrategyResult
    return StrategyResult(
        entry_mask=result.entry_mask,
        direction=result.direction,
        market_type=result.market_type,
        leverage=result.leverage,
        stop_mult=result.stop_mult,
        trail_mult=result.trail_mult,
        target_mult=s507_mod.TARGET_MULT,
        no_stop_bars=result.no_stop_bars,
        min_hold=result.min_hold,
        max_hold=result.max_hold,
        edge=result.edge,
        name='s507_continuous_sizing',
        breakeven_atr=0.5,
        size_multiplier=result.size_multiplier,
    )


def _install_graduated_strategy():
    """Monkey-patch s507 (and its s506 base) to use graduated sizing."""
    s506_mod.strategy = _graduated_s506_strategy
    s507_mod.strategy = _graduated_s507_strategy
    if _cached_s506 is not None:
        _cached_s506.strategy = _graduated_s506_strategy
    if _cached_s507 is not None:
        _cached_s507.strategy = _graduated_s507_strategy
    sm506 = sys.modules.get('strategies.s506_ls_divergence')
    if sm506 is not None and sm506 is not s506_mod:
        sm506.strategy = _graduated_s506_strategy
    sm507 = sys.modules.get('strategies.s507_ls_div_fixed_tp')
    if sm507 is not None and sm507 is not s507_mod:
        sm507.strategy = _graduated_s507_strategy


def _restore_original_strategy():
    """Restore original binary-threshold strategies."""
    s506_mod.strategy = _original_s506_strategy
    s507_mod.strategy = _original_s507_strategy
    if _cached_s506 is not None:
        _cached_s506.strategy = _original_s506_strategy
    if _cached_s507 is not None:
        _cached_s507.strategy = _original_s507_strategy
    sm506 = sys.modules.get('strategies.s506_ls_divergence')
    if sm506 is not None and sm506 is not s506_mod:
        sm506.strategy = _original_s506_strategy
    sm507 = sys.modules.get('strategies.s507_ls_div_fixed_tp')
    if sm507 is not None and sm507 is not s507_mod:
        sm507.strategy = _original_s507_strategy


# ======================================================================
#  Run one backtest
# ======================================================================

def run_one(label="baseline", first_run=False):
    """Run s507 L12M backtest, return metrics dict."""
    config = PortfolioConfig(
        capital=100_000,
        exchange='binance',
        skip_walk_forward=True,
        conviction_mode='ranked',
    )

    spec = StrategySpec(
        strategy_id='s507',
        market='perp',
        strategy_type='per_token',
        max_positions=50,
    )

    tokens = get_all_tradeable()
    end_date = pd.Timestamp('2026-04-01')

    if first_run:
        with _STRATEGY_MODULE_LOCK:
            _STRATEGY_MODULE_CACHE.pop('s507', None)
            _STRATEGY_MODULE_CACHE.pop('s506', None)

    # Clear alignment cache (data alignment depends on signal params)
    s506_mod._aligned_cache.clear()
    if _cached_s506 is not None and hasattr(_cached_s506, '_aligned_cache'):
        _cached_s506._aligned_cache.clear()

    t0 = time.perf_counter()
    signals = precompute_strategy_signals(spec, tokens, config, months=12, end_date=end_date)
    t1 = time.perf_counter()

    if not signals:
        print(f"  [{label}] No signals produced!")
        return None

    state = simulate_portfolio(
        {'s507': signals},
        {'s507': spec},
        config,
    )
    t2 = time.perf_counter()

    metrics = compute_metrics(state.equity_snapshots, 100_000)
    if metrics is None:
        print(f"  [{label}] Insufficient equity data")
        return None

    metrics['label'] = label
    metrics['n_trades'] = len(state.position_manager.closed_trades)
    metrics['signal_time'] = t1 - t0
    metrics['sim_time'] = t2 - t1
    return metrics


def print_metrics(m, header=""):
    if m is None:
        print(f"  {header}: FAILED")
        return
    print(f"\n  {header}")
    print(f"  {'=' * 55}")
    print(f"  Return:      {m['total_return']:+.1f}%")
    print(f"  Ann. Return: {m['ann_return']:+.1f}%")
    print(f"  Max DD:      {m['max_dd']:.1f}%")
    print(f"  Sharpe:      {m['sharpe']:.2f}")
    print(f"  Sortino:     {m['sortino']:.2f}")
    print(f"  Calmar:      {m['calmar']:.2f}")
    print(f"  Trades:      {m['n_trades']}")
    print(f"  Time:        {m['signal_time']:.1f}s signals + {m['sim_time']:.1f}s sim")


# ======================================================================
#  Main
# ======================================================================

def main():
    global _LOW_THRESHOLD

    print("=" * 70)
    print("  GATE 5 — s507 CONTINUOUS SIZING vs BINARY THRESHOLD")
    print("  Does graduated sizing reduce THRESHOLD fragility?")
    print("=" * 70)

    results = []

    # ── 1. Baseline: original s507 (binary THRESHOLD=2.5) ──
    print(f"\n{'=' * 70}")
    print(f"  [1/4] BASELINE — s507 binary threshold (THRESHOLD=2.5)")
    print(f"{'=' * 70}")

    baseline = run_one("s507 baseline (THRESH=2.5)", first_run=True)
    print_metrics(baseline, "s507 BASELINE (binary, THRESHOLD=2.5)")
    results.append(baseline)

    _ensure_cached_modules()

    if baseline is None:
        print("\nBASELINE FAILED — cannot proceed.")
        return

    baseline_calmar = baseline['calmar']

    # ── 2. Continuous variant: graduated sizing (LOW_THRESHOLD=1.5) ──
    print(f"\n{'=' * 70}")
    print(f"  [2/4] CONTINUOUS — graduated sizing (LOW_THRESHOLD=1.5)")
    print(f"  z 1.5-2.0: 0.3x | z 2.0-2.5: 0.6x | z 2.5+: 1.0x")
    print(f"{'=' * 70}")

    _LOW_THRESHOLD = 1.5
    _install_graduated_strategy()

    continuous = run_one("continuous (LOW=1.5)")
    print_metrics(continuous, "CONTINUOUS (LOW_THRESHOLD=1.5)")
    results.append(continuous)

    _restore_original_strategy()

    if continuous is None:
        cont_calmar = None
    else:
        cont_calmar = continuous['calmar']

    # ── 3. Sensitivity: LOW_THRESHOLD = 1.2 (-20%) ──
    print(f"\n{'=' * 70}")
    print(f"  [3/4] SENSITIVITY — LOW_THRESHOLD=1.2 (-20%)")
    print(f"{'=' * 70}")

    _LOW_THRESHOLD = 1.2
    _install_graduated_strategy()

    sens_low = run_one("continuous (LOW=1.2, -20%)")
    print_metrics(sens_low, "CONTINUOUS (LOW_THRESHOLD=1.2, -20%)")
    results.append(sens_low)

    _restore_original_strategy()

    # ── 4. Sensitivity: LOW_THRESHOLD = 1.8 (+20%) ──
    print(f"\n{'=' * 70}")
    print(f"  [4/4] SENSITIVITY — LOW_THRESHOLD=1.8 (+20%)")
    print(f"{'=' * 70}")

    _LOW_THRESHOLD = 1.8
    _install_graduated_strategy()

    sens_high = run_one("continuous (LOW=1.8, +20%)")
    print_metrics(sens_high, "CONTINUOUS (LOW_THRESHOLD=1.8, +20%)")
    results.append(sens_high)

    _restore_original_strategy()

    # ── Summary comparison ──
    print(f"\n{'=' * 70}")
    print(f"  COMPARISON: BINARY vs CONTINUOUS SIZING")
    print(f"{'=' * 70}")

    print(f"\n  {'Variant':<35} {'Sharpe':>8} {'Calmar':>8} {'MaxDD':>8} {'Trades':>8}")
    print(f"  {'=' * 70}")

    for m in results:
        if m is None:
            continue
        print(f"  {m['label']:<35} {m['sharpe']:>8.2f} {m['calmar']:>8.2f} {m['max_dd']:>8.1f} {m['n_trades']:>8}")

    # ── Calmar degradation analysis ──
    print(f"\n  {'=' * 70}")
    print(f"  CALMAR DEGRADATION ANALYSIS")
    print(f"  {'=' * 70}")

    print(f"\n  Baseline binary s507 Calmar: {baseline_calmar:.2f}")

    if cont_calmar is not None:
        cont_change = (cont_calmar - baseline_calmar) / max(abs(baseline_calmar), 1e-10) * 100
        print(f"  Continuous (LOW=1.5) Calmar:  {cont_calmar:.2f} ({cont_change:+.1f}% vs baseline)")

        # Sensitivity of the continuous variant
        if sens_low is not None and cont_calmar is not None:
            low_change = (sens_low['calmar'] - cont_calmar) / max(abs(cont_calmar), 1e-10) * 100
            print(f"  Continuous (LOW=1.2) Calmar:  {sens_low['calmar']:.2f} ({low_change:+.1f}% vs continuous base)")
        if sens_high is not None and cont_calmar is not None:
            high_change = (sens_high['calmar'] - cont_calmar) / max(abs(cont_calmar), 1e-10) * 100
            print(f"  Continuous (LOW=1.8) Calmar:  {sens_high['calmar']:.2f} ({high_change:+.1f}% vs continuous base)")

        # Key verdict: is LOW_THRESHOLD sensitivity < 30%?
        if sens_low is not None and sens_high is not None:
            worst_deg = min(
                (sens_low['calmar'] - cont_calmar) / max(abs(cont_calmar), 1e-10) * 100,
                (sens_high['calmar'] - cont_calmar) / max(abs(cont_calmar), 1e-10) * 100,
            )
            print(f"\n  Worst Calmar degradation at +/-20% of LOW_THRESHOLD: {worst_deg:+.1f}%")

            if worst_deg > -30:
                print(f"  VERDICT: ROBUST — continuous sizing passes Gate 5 threshold sensitivity")
            else:
                print(f"  VERDICT: STILL FRAGILE — continuous sizing did not fix threshold sensitivity")

            # Compare to binary threshold sensitivity
            print(f"\n  For reference, binary s507 THRESHOLD sensitivity:")
            print(f"  (from gate5_s507_param_sensitivity.py — THRESHOLD +/-20% = 2.0/3.0)")
    else:
        print(f"  Continuous variant FAILED — cannot assess improvement.")

    # ── Overall verdict ──
    print(f"\n{'=' * 70}")
    print(f"  OVERALL VERDICT")
    print(f"{'=' * 70}")

    if cont_calmar is not None and baseline_calmar != 0:
        if cont_calmar >= baseline_calmar * 0.9:
            print(f"  Continuous sizing preserves Calmar (within 10% of baseline).")
        elif cont_calmar >= baseline_calmar * 0.7:
            print(f"  Continuous sizing moderately reduces Calmar ({cont_change:+.1f}%).")
        else:
            print(f"  Continuous sizing significantly reduces Calmar ({cont_change:+.1f}%).")

        if sens_low is not None and sens_high is not None:
            low_deg = abs((sens_low['calmar'] - cont_calmar) / max(abs(cont_calmar), 1e-10) * 100)
            high_deg = abs((sens_high['calmar'] - cont_calmar) / max(abs(cont_calmar), 1e-10) * 100)
            max_deg = max(low_deg, high_deg)
            print(f"  Max Calmar sensitivity at +/-20%: {max_deg:.1f}%")
            if max_deg < 30:
                print(f"  --> Graduated sizing RESOLVES threshold fragility (max {max_deg:.1f}% < 30%)")
            else:
                print(f"  --> Graduated sizing DOES NOT resolve threshold fragility (max {max_deg:.1f}% >= 30%)")


if __name__ == "__main__":
    main()
