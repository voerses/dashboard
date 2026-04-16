#!/usr/bin/env python3
"""
s98 Filter Ablation Diagnosis
=============================

Diagnoses why s98_sr_breakout_swing has very few trades by:

1. Running baseline s98 across all tokens for L12M via v4 portfolio engine
2. Systematically disabling each filter (liquidity, squeeze, ADX) and re-running
3. Counting per-bar filter TRUE/FALSE rates on BTC to quantify restriction

This identifies which filter is the binding constraint on trade frequency.

Usage:
    /workspace/venv/bin/python research/s98_filter_diagnosis.py
"""

import sys
import os
import time
import copy
import dataclasses
import numpy as np
import pandas as pd

sys.path.insert(0, '/workspace/crypto_backtest')
sys.path.insert(0, '/workspace/crypto_backtest/v4')
os.chdir('/workspace/crypto_backtest')

from v4.config import PortfolioConfig, StrategySpec
from v4.signals import precompute_strategy_signals, discover_tokens, infer_data_end_date
from v4.simulator import simulate_portfolio
from v4.report import compute_portfolio_metrics
from v4.data_loader import load_token_data_cached
from v4.engine import Engine, rolling_mean, _load_strategy_fn, _STRATEGY_MODULE_CACHE

# Constants from engine for regime labels
from engine import UPTREND, DOWNTREND, CRISIS, RANGE, QUIET

# Force-load the strategy module into cache so we can patch it
_load_strategy_fn('s98')
s98_mod = _STRATEGY_MODULE_CACHE['s98']

# ── Configuration ───────────────────────────────────────────────────
CAPITAL = 100_000
MONTHS = 12
MARKET = 'perp'
DATA_DIR = '/workspace/crypto_backtest/data'

# Save original constants
ORIG = {
    'MIN_ADV_USD': s98_mod.MIN_ADV_USD,
    'SQUEEZE_MULT': s98_mod.SQUEEZE_MULT,
    'ADX_THRESH': s98_mod.ADX_THRESH,
}


def run_s98(label: str, overrides: dict = None) -> dict:
    """Run s98 with optional constant overrides, return metrics + trade count."""
    # Apply overrides
    if overrides:
        for k, v in overrides.items():
            setattr(s98_mod, k, v)

    config = PortfolioConfig(
        capital=CAPITAL,
        exchange='binance',
        skip_walk_forward=True,
        concentration_limit=0.20,
        max_portfolio_positions=40,
    )

    spec = StrategySpec(
        strategy_id='s98',
        market=MARKET,
        strategy_type='per_token',
        max_positions=40,
    )

    end_date = infer_data_end_date(MARKET)
    tokens = discover_tokens(MARKET)

    print(f"\n{'='*70}")
    print(f"  {label}")
    if overrides:
        for k, v in overrides.items():
            print(f"    {k}: {ORIG[k]} -> {v}")
    print(f"  Tokens: {len(tokens)}, Months: {MONTHS}, Capital: ${CAPITAL:,}")
    print(f"{'='*70}")

    t0 = time.perf_counter()
    signals = precompute_strategy_signals(spec, tokens, config, MONTHS, end_date=end_date)
    t_sig = time.perf_counter() - t0

    # Count raw entries across all tokens before simulation
    total_entries = 0
    entries_by_token = {}
    for tok, tsig in signals.items():
        n_entries = int(np.sum(tsig.entry_mask))
        total_entries += n_entries
        if n_entries > 0:
            entries_by_token[tok] = n_entries

    print(f"  Signal precompute: {t_sig:.1f}s, Raw entries across all tokens: {total_entries}")
    if entries_by_token:
        top = sorted(entries_by_token.items(), key=lambda x: -x[1])[:10]
        print(f"  Top tokens by entries: {', '.join(f'{t}({n})' for t, n in top)}")

    strategy_specs = {'s98': spec}
    config_run = dataclasses.replace(config, strategies=[spec], capital=CAPITAL)

    t0 = time.perf_counter()
    state = simulate_portfolio({'s98': signals}, strategy_specs, config_run)
    t_sim = time.perf_counter() - t0

    trades = state.position_manager.closed_trades
    n_trades = len(trades)
    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]
    win_rate = len(wins) / n_trades * 100 if n_trades else 0

    metrics, extra_info, eq_daily = compute_portfolio_metrics(state, CAPITAL)

    sharpe = metrics.sharpe_ratio
    calmar = metrics.calmar_ratio
    max_dd = metrics.max_drawdown_pct
    total_ret = metrics.total_return_pct
    sortino = metrics.sortino_ratio

    print(f"  Simulation: {t_sim:.1f}s")
    print(f"  Trades: {n_trades}  |  WinRate: {win_rate:.1f}%")
    print(f"  Return: {total_ret:.1f}%  |  Sharpe: {sharpe:.2f}  |  Calmar: {calmar:.2f}")
    print(f"  MaxDD: {max_dd:.1f}%  |  Sortino: {sortino:.2f}")
    print(f"  Raw entries (pre-sim): {total_entries}")

    # Restore originals
    for k in ORIG:
        setattr(s98_mod, k, ORIG[k])

    return {
        'label': label,
        'trades': n_trades,
        'raw_entries': total_entries,
        'win_rate': win_rate,
        'total_return': total_ret,
        'sharpe': sharpe,
        'calmar': calmar,
        'max_dd': max_dd,
        'sortino': sortino,
    }


def bar_level_filter_analysis():
    """Count how many bars each filter is TRUE vs FALSE for BTC.

    This shows which filter is the tightest constraint.
    """
    print(f"\n{'='*70}")
    print("  BAR-LEVEL FILTER ANALYSIS (BTC)")
    print(f"{'='*70}")

    end_date = infer_data_end_date(MARKET)
    eng = Engine(data_dir=DATA_DIR, market='perp', capital=CAPITAL, exchange='binance')

    # Load BTC data
    df_perp = load_token_data_cached('BTC', 'perp', data_dir=DATA_DIR)
    if df_perp is None:
        print("  ERROR: No BTC perp data found")
        return

    # Trim to ~12mo + warmup
    WARMUP_DAYS = 180
    trade_start = end_date - pd.DateOffset(months=MONTHS)
    load_from = trade_start - pd.DateOffset(days=WARMUP_DAYS)
    df_perp = df_perp[(df_perp.index >= load_from) & (df_perp.index <= end_date)]

    print(f"  Data range: {df_perp.index[0]} to {df_perp.index[-1]} ({len(df_perp)} bars)")

    ctx = eng._build_context('BTC', df_perp, min_bars=210)
    if ctx is None:
        print("  ERROR: Could not build context for BTC")
        return

    close = ctx.ind_1h['close']
    volume = ctx.ind_1h['volume']
    n = len(close)
    macd = ctx.ind_1h['macd']
    adx = ctx.ind_1h['adx']
    plus_di = ctx.ind_1h['plus_di']
    minus_di = ctx.ind_1h['minus_di']
    ema10 = ctx.ind_1h['ema_10']
    ema20 = ctx.ind_1h['ema_20']
    ema50 = ctx.ind_1h['ema_50']
    bb_width = ctx.ind_1h['bb_width']
    regime = ctx.regime_1h

    WARMUP = 200

    # ── Compute each filter independently ──
    # Liquidity
    dollar_vol = close * volume
    rolling_adv = rolling_mean(dollar_vol, 24) * 24
    liquid = rolling_adv > ORIG['MIN_ADV_USD']

    # BB squeeze
    bb_avg = rolling_mean(bb_width, 240)
    squeeze = bb_width < bb_avg * ORIG['SQUEEZE_MULT']

    # ADX
    adx_ok = adx > ORIG['ADX_THRESH']

    # Regime
    uptrend = regime == UPTREND
    downtrend = regime == DOWNTREND
    trending = uptrend | downtrend

    # DI confirmation
    long_di = plus_di > minus_di
    short_di = minus_di > plus_di

    # MACD signals
    macd_bull = macd > 0
    macd_bear = macd < 0
    macd_prev = np.roll(macd, 1)
    macd_prev[0] = np.nan
    macd_cross_bull = (macd > 0) & (macd_prev <= 0)
    macd_cross_bear = (macd < 0) & (macd_prev >= 0)

    # Regime change
    regime_prev = np.roll(regime, 1)
    regime_prev[0] = regime[0]
    regime_change_up = (regime == UPTREND) & (regime_prev != UPTREND)
    regime_change_down = (regime == DOWNTREND) & (regime_prev != DOWNTREND)

    # EMA alignment
    ema_bull = (ema10 > ema20) & (ema20 > ema50)
    ema_bear = (ema10 < ema20) & (ema20 < ema50)

    # Core signal (MACD cross OR regime change with confirmation)
    core_long = (macd_cross_bull & uptrend) | (regime_change_up & macd_bull & ema_bull)
    core_short = (macd_cross_bear & downtrend) | (regime_change_down & macd_bear & ema_bear)
    core_signal = core_long | core_short

    # Valid bars (post-warmup)
    valid = np.ones(n, dtype=bool)
    valid[:WARMUP] = False
    n_valid = int(np.sum(valid))

    # ── Individual filter pass rates ──
    filters = {
        'Liquid (ADV > $1B)': liquid,
        'Squeeze (BB < 80% avg)': squeeze,
        'ADX > 20': adx_ok,
        'Trending (UP or DOWN)': trending,
        'DI confirmation (either)': long_di | short_di,
        'Core signal (MACD/regime)': core_signal,
    }

    print(f"\n  Total bars: {n}  |  Valid (post-warmup): {n_valid}")
    print(f"\n  {'Filter':<35} {'TRUE':>8} {'FALSE':>8} {'TRUE%':>8}")
    print(f"  {'-'*62}")

    for name, mask in filters.items():
        mask_valid = mask[WARMUP:]
        n_true = int(np.sum(mask_valid))
        n_false = len(mask_valid) - n_true
        pct = n_true / len(mask_valid) * 100
        print(f"  {name:<35} {n_true:>8,} {n_false:>8,} {pct:>7.1f}%")

    # ── Progressive filter stack ──
    print(f"\n  {'Progressive Filter Stack':<45} {'TRUE':>8} {'TRUE%':>8}")
    print(f"  {'-'*64}")

    cumulative = valid.copy()
    steps = [
        ('Core signal', core_signal),
        ('+ ADX > 20', adx_ok),
        ('+ DI confirmation', long_di | short_di),
        ('+ Liquidity (ADV > $1B)', liquid),
        ('+ BB Squeeze', squeeze),
    ]

    for name, mask in steps:
        cumulative = cumulative & mask
        n_true = int(np.sum(cumulative))
        pct = n_true / n_valid * 100 if n_valid else 0
        print(f"  {name:<45} {n_true:>8,} {pct:>7.2f}%")

    # Full entry signal (as computed by strategy)
    entry_long = (
        ((regime_change_up & macd_bull & ema_bull) | (macd_cross_bull & uptrend))
        & adx_ok & long_di & liquid & squeeze
    )
    entry_short = (
        ((regime_change_down & macd_bear & ema_bear) | (macd_cross_bear & downtrend))
        & adx_ok & short_di & liquid & squeeze
    )
    full_entry = (entry_long | entry_short) & valid
    print(f"\n  Full entry signal (matching strategy): {int(np.sum(full_entry)):,} bars")

    # ── Filter removal impact on BTC ──
    print(f"\n  {'Filter Removed (BTC only)':<35} {'Entries':>8} {'vs Base':>10}")
    print(f"  {'-'*55}")
    baseline_entries = int(np.sum(full_entry))
    print(f"  {'BASELINE (all filters)':35} {baseline_entries:>8} {'---':>10}")

    # No liquidity
    el = ((regime_change_up & macd_bull & ema_bull) | (macd_cross_bull & uptrend)) & adx_ok & long_di & squeeze
    es = ((regime_change_down & macd_bear & ema_bear) | (macd_cross_bear & downtrend)) & adx_ok & short_di & squeeze
    no_liq = int(np.sum((el | es) & valid))
    print(f"  {'Remove Liquidity':35} {no_liq:>8} {f'+{no_liq - baseline_entries}':>10}")

    # No squeeze
    el = ((regime_change_up & macd_bull & ema_bull) | (macd_cross_bull & uptrend)) & adx_ok & long_di & liquid
    es = ((regime_change_down & macd_bear & ema_bear) | (macd_cross_bear & downtrend)) & adx_ok & short_di & liquid
    no_sqz = int(np.sum((el | es) & valid))
    print(f"  {'Remove Squeeze':35} {no_sqz:>8} {f'+{no_sqz - baseline_entries}':>10}")

    # No ADX
    el = ((regime_change_up & macd_bull & ema_bull) | (macd_cross_bull & uptrend)) & long_di & liquid & squeeze
    es = ((regime_change_down & macd_bear & ema_bear) | (macd_cross_bear & downtrend)) & short_di & liquid & squeeze
    no_adx = int(np.sum((el | es) & valid))
    print(f"  {'Remove ADX':35} {no_adx:>8} {f'+{no_adx - baseline_entries}':>10}")

    # No ADX + No DI
    el = ((regime_change_up & macd_bull & ema_bull) | (macd_cross_bull & uptrend)) & liquid & squeeze
    es = ((regime_change_down & macd_bear & ema_bear) | (macd_cross_bear & downtrend)) & liquid & squeeze
    no_adx_di = int(np.sum((el | es) & valid))
    print(f"  {'Remove ADX + DI':35} {no_adx_di:>8} {f'+{no_adx_di - baseline_entries}':>10}")

    # All filters removed (just core signal)
    no_all = int(np.sum(core_signal & valid))
    print(f"  {'ALL filters removed':35} {no_all:>8} {f'+{no_all - baseline_entries}':>10}")

    # Regime distribution
    print(f"\n  Regime Distribution (post-warmup):")
    regime_valid = regime[WARMUP:]
    for label, val in [('UPTREND', UPTREND), ('DOWNTREND', DOWNTREND),
                       ('RANGE', RANGE), ('QUIET', QUIET), ('CRISIS', CRISIS)]:
        n_r = int(np.sum(regime_valid == val))
        pct = n_r / len(regime_valid) * 100
        print(f"    {label:<12} {n_r:>8,} ({pct:>5.1f}%)")


def main():
    print("=" * 70)
    print("  s98 FILTER ABLATION DIAGNOSIS")
    print("  Strategy: s98_sr_breakout_swing (Regime-State MACD Squeeze)")
    print("  Period: L12M  |  Engine: v4 portfolio")
    print("=" * 70)

    # ── Step 1: Bar-level filter analysis on BTC ──
    bar_level_filter_analysis()

    # ── Step 2: Full backtest ablation ──
    results = []

    # Baseline
    r = run_s98("BASELINE (all filters active)")
    results.append(r)

    # Single filter removals
    variants = [
        ('No Liquidity (MIN_ADV=0)', {'MIN_ADV_USD': 0}),
        ('No Squeeze (MULT=99)', {'SQUEEZE_MULT': 99.0}),
        ('No ADX (THRESH=0)', {'ADX_THRESH': 0}),
    ]

    for label, overrides in variants:
        r = run_s98(label, overrides)
        results.append(r)

    # Pairwise removals
    pairs = [
        ('No Liq + No Squeeze', {'MIN_ADV_USD': 0, 'SQUEEZE_MULT': 99.0}),
        ('No Liq + No ADX', {'MIN_ADV_USD': 0, 'ADX_THRESH': 0}),
        ('No Squeeze + No ADX', {'SQUEEZE_MULT': 99.0, 'ADX_THRESH': 0}),
    ]

    for label, overrides in pairs:
        r = run_s98(label, overrides)
        results.append(r)

    # All removed
    r = run_s98('ALL filters removed', {'MIN_ADV_USD': 0, 'SQUEEZE_MULT': 99.0, 'ADX_THRESH': 0})
    results.append(r)

    # ── Step 3: Summary table ──
    print(f"\n\n{'='*105}")
    print("  ABLATION SUMMARY TABLE")
    print(f"{'='*105}")
    print(f"  {'Variant':<30} {'Trades':>7} {'RawEnt':>7} {'WR%':>6} {'Ret%':>8} "
          f"{'Sharpe':>7} {'Calmar':>7} {'MaxDD%':>7} {'Sortino':>8}")
    print(f"  {'-'*93}")

    for r in results:
        print(f"  {r['label']:<30} {r['trades']:>7} {r['raw_entries']:>7} "
              f"{r['win_rate']:>5.1f}% {r['total_return']:>+7.1f}% "
              f"{r['sharpe']:>7.2f} {r['calmar']:>7.2f} {r['max_dd']:>6.1f}% "
              f"{r['sortino']:>8.2f}")

    print(f"\n  Key: RawEnt = raw entry signals before portfolio simulation constraints")
    print(f"       Trades = actual trades after position limits, sizing, etc.")

    # Identify bottleneck
    baseline = results[0]
    max_increase = 0
    bottleneck = None
    for r in results[1:4]:  # Single removals only
        increase = r['raw_entries'] - baseline['raw_entries']
        if increase > max_increase:
            max_increase = increase
            bottleneck = r['label']

    if bottleneck:
        print(f"\n  BOTTLENECK: {bottleneck}")
        print(f"  Removing this filter adds {max_increase} raw entry signals "
              f"({max_increase / max(baseline['raw_entries'], 1) * 100:.0f}% increase)")
    elif baseline['raw_entries'] == 0:
        print(f"\n  BOTTLENECK: Core signal itself produces 0 entries — filters are not the issue")
    else:
        print(f"\n  No single filter dominates — constraint is distributed or in core signal logic")


if __name__ == '__main__':
    main()
