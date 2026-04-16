#!/usr/bin/env python3
"""
s98 EMA Pullback Trigger Study
===============================

Tests adding an EMA pullback entry trigger to s98's regime framework.

EMA Pullback trigger:
  LONG:  price dips to touch/cross below EMA20, then closes back above EMA20,
         while in UPTREND regime (trend continuation after pullback)
  SHORT: price spikes to touch/cross above EMA20, then closes back below EMA20,
         while in DOWNTREND regime

Runs 3 variants through v4 portfolio engine L12M:
  1. Baseline s98 (MACD zero-cross only)
  2. EMA pullback only (replaces MACD trigger)
  3. Combined (MACD OR EMA pullback)

Usage:
    /workspace/venv/bin/python research/s98_trigger_ema_pullback.py
"""

import sys
import os
import time
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
from v4.engine import Engine, rolling_mean, _load_strategy_fn, _STRATEGY_MODULE_CACHE

from engine import (StrategyContext, StrategyResult, MarketType,
                    CRISIS, DOWNTREND, UPTREND, RANGE, QUIET)

# Force-load s98 into cache
_load_strategy_fn('s98')
s98_mod = _STRATEGY_MODULE_CACHE['s98']

# Save original strategy function
_original_strategy = s98_mod.strategy

# ── Configuration ───────────────────────────────────────────────────
CAPITAL = 100_000
MONTHS = 12
MARKET = 'perp'
DATA_DIR = '/workspace/crypto_backtest/data'
START = '2025-04-01'
END = '2026-04-01'


# ── Strategy Variants ──────────────────────────────────────────────

def strategy_ema_pullback_only(ctx: StrategyContext) -> StrategyResult:
    """s98 with EMA pullback trigger REPLACING MACD zero-cross.
    All other filters (ADX, DI, liquidity, squeeze, regime) kept intact."""
    close = ctx.ind_1h['close']
    volume = ctx.ind_1h['volume']
    n = len(close)
    adx = ctx.ind_1h['adx']
    plus_di = ctx.ind_1h['plus_di']
    minus_di = ctx.ind_1h['minus_di']
    ema20 = ctx.ind_1h['ema_20']
    bb_width = ctx.ind_1h['bb_width']
    regime = ctx.regime_1h

    # ── Liquidity filter ──
    dollar_vol = close * volume
    rolling_adv = rolling_mean(dollar_vol, 24) * 24
    liquid = rolling_adv > s98_mod.MIN_ADV_USD

    # ── BB squeeze filter ──
    bb_avg = rolling_mean(bb_width, s98_mod.BB_LOOKBACK)
    squeeze = bb_width < bb_avg * s98_mod.SQUEEZE_MULT

    # ── Regime filter ──
    uptrend = regime == UPTREND
    downtrend = regime == DOWNTREND
    adx_ok = adx > s98_mod.ADX_THRESH
    long_di = plus_di > minus_di
    short_di = minus_di > plus_di

    # ── EMA pullback trigger ──
    close_prev = np.roll(close, 1)
    close_prev[0] = close[0]
    ema20_prev = np.roll(ema20, 1)
    ema20_prev[0] = ema20[0]

    # LONG: price was at/below ema20 last bar, now above (bounce off support)
    ema20_touch_long = (close > ema20) & (close_prev <= ema20_prev)
    # SHORT: price was at/above ema20 last bar, now below (rejection from resistance)
    ema20_touch_short = (close < ema20) & (close_prev >= ema20_prev)

    # ── Entry signals ──
    entry_long = ema20_touch_long & uptrend & adx_ok & long_di & liquid & squeeze
    entry_short = ema20_touch_short & downtrend & adx_ok & short_di & liquid & squeeze

    entry_mask = entry_long | entry_short
    direction = np.where(entry_long, 1, np.where(entry_short, -1, 0)).astype(np.int8)
    entry_mask[:s98_mod.WARMUP] = False

    if ctx.liquidity_mask is not None:
        entry_mask = entry_mask & ctx.liquidity_mask

    return StrategyResult(
        entry_mask=entry_mask,
        direction=direction,
        market_type=MarketType.PERP,
        leverage=s98_mod.LEVERAGE,
        stop_mult=s98_mod.STOP_MULT,
        trail_mult=s98_mod.TRAIL_MULT,
        target_mult=s98_mod.TARGET_MULT,
        no_stop_bars=s98_mod.NO_STOP_BARS,
        min_hold=s98_mod.MIN_HOLD,
        max_hold=s98_mod.MAX_HOLD,
        edge=s98_mod.EDGE,
        exit_regimes={CRISIS},
        breakeven_atr=s98_mod.BREAKEVEN_ATR,
        exchange='binance',
        name='s98_ema_pullback_only',
    )


def strategy_combined(ctx: StrategyContext) -> StrategyResult:
    """s98 with BOTH triggers: MACD zero-cross OR EMA pullback.
    All other filters (ADX, DI, liquidity, squeeze, regime) kept intact."""
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

    # ── Liquidity filter ──
    dollar_vol = close * volume
    rolling_adv = rolling_mean(dollar_vol, 24) * 24
    liquid = rolling_adv > s98_mod.MIN_ADV_USD

    # ── BB squeeze filter ──
    bb_avg = rolling_mean(bb_width, s98_mod.BB_LOOKBACK)
    squeeze = bb_width < bb_avg * s98_mod.SQUEEZE_MULT

    # ── Regime filter ──
    uptrend = regime == UPTREND
    downtrend = regime == DOWNTREND
    adx_ok = adx > s98_mod.ADX_THRESH
    long_di = plus_di > minus_di
    short_di = minus_di > plus_di

    # ── Regime change detection ──
    regime_prev = np.roll(regime, 1)
    regime_prev[0] = regime[0]
    regime_change_up = (regime == UPTREND) & (regime_prev != UPTREND)
    regime_change_down = (regime == DOWNTREND) & (regime_prev != DOWNTREND)

    # ── EMA alignment ──
    ema_bull = (ema10 > ema20) & (ema20 > ema50)
    ema_bear = (ema10 < ema20) & (ema20 < ema50)

    # ── MACD signals (original trigger) ──
    macd_bull = macd > 0
    macd_bear = macd < 0
    macd_prev = np.roll(macd, 1)
    macd_prev[0] = np.nan
    macd_cross_bull = (macd > 0) & (macd_prev <= 0)
    macd_cross_bear = (macd < 0) & (macd_prev >= 0)

    # ── EMA pullback trigger (new) ──
    close_prev = np.roll(close, 1)
    close_prev[0] = close[0]
    ema20_prev = np.roll(ema20, 1)
    ema20_prev[0] = ema20[0]

    ema20_touch_long = (close > ema20) & (close_prev <= ema20_prev)
    ema20_touch_short = (close < ema20) & (close_prev >= ema20_prev)

    # ── Entry signals: MACD OR regime change OR EMA pullback ──
    trigger_long = (
        (macd_cross_bull & uptrend)
        | (regime_change_up & macd_bull & ema_bull)
        | (ema20_touch_long & uptrend)
    )
    trigger_short = (
        (macd_cross_bear & downtrend)
        | (regime_change_down & macd_bear & ema_bear)
        | (ema20_touch_short & downtrend)
    )

    entry_long = trigger_long & adx_ok & long_di & liquid & squeeze
    entry_short = trigger_short & adx_ok & short_di & liquid & squeeze

    entry_mask = entry_long | entry_short
    direction = np.where(entry_long, 1, np.where(entry_short, -1, 0)).astype(np.int8)
    entry_mask[:s98_mod.WARMUP] = False

    if ctx.liquidity_mask is not None:
        entry_mask = entry_mask & ctx.liquidity_mask

    return StrategyResult(
        entry_mask=entry_mask,
        direction=direction,
        market_type=MarketType.PERP,
        leverage=s98_mod.LEVERAGE,
        stop_mult=s98_mod.STOP_MULT,
        trail_mult=s98_mod.TRAIL_MULT,
        target_mult=s98_mod.TARGET_MULT,
        no_stop_bars=s98_mod.NO_STOP_BARS,
        min_hold=s98_mod.MIN_HOLD,
        max_hold=s98_mod.MAX_HOLD,
        edge=s98_mod.EDGE,
        exit_regimes={CRISIS},
        breakeven_atr=s98_mod.BREAKEVEN_ATR,
        exchange='binance',
        name='s98_combined',
    )


# ── Runner ─────────────────────────────────────────────────────────

def run_variant(label: str, strategy_fn) -> dict:
    """Run a strategy variant through the v4 portfolio engine."""
    # Monkey-patch the strategy function
    s98_mod.strategy = strategy_fn

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

    end_date = pd.Timestamp(END)
    tokens = discover_tokens(MARKET)

    print(f"\n{'='*70}")
    print(f"  {label}")
    print(f"  Tokens: {len(tokens)}, Period: {START} to {END}, Capital: ${CAPITAL:,}")
    print(f"{'='*70}")

    t0 = time.perf_counter()
    signals = precompute_strategy_signals(spec, tokens, config, MONTHS, end_date=end_date)
    t_sig = time.perf_counter() - t0

    # Count raw entries across all tokens
    total_entries = 0
    entries_by_token = {}
    for tok, tsig in signals.items():
        n_entries = int(np.sum(tsig.entry_mask))
        total_entries += n_entries
        if n_entries > 0:
            entries_by_token[tok] = n_entries

    print(f"  Signal precompute: {t_sig:.1f}s, Raw entries: {total_entries}")
    if entries_by_token:
        top = sorted(entries_by_token.items(), key=lambda x: -x[1])[:10]
        print(f"  Top tokens: {', '.join(f'{t}({n})' for t, n in top)}")

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

    # Profit factor
    gross_wins = sum(t.pnl for t in wins) if wins else 0
    gross_losses = abs(sum(t.pnl for t in losses)) if losses else 0
    pf = gross_wins / gross_losses if gross_losses > 0 else (float('inf') if gross_wins > 0 else 0)

    print(f"  Simulation: {t_sim:.1f}s")
    print(f"  Trades: {n_trades}  |  WinRate: {win_rate:.1f}%  |  PF: {pf:.2f}")
    print(f"  Return: {total_ret:.1f}%  |  Sharpe: {sharpe:.2f}  |  Calmar: {calmar:.2f}")
    print(f"  MaxDD: {max_dd:.1f}%  |  Sortino: {sortino:.2f}")

    # Collect trade entry timestamps for overlap analysis
    trade_entries = set()
    for t in trades:
        trade_entries.add((t.token, t.entry_bar))

    # Restore original
    s98_mod.strategy = _original_strategy

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
        'profit_factor': pf,
        'trade_entries': trade_entries,
        'closed_trades': trades,
    }


def overlap_analysis(baseline_result, pullback_result, combined_result):
    """Analyze trade overlap between baseline and pullback variants."""
    print(f"\n{'='*70}")
    print("  TRADE OVERLAP ANALYSIS")
    print(f"{'='*70}")

    base_entries = baseline_result['trade_entries']
    pull_entries = pullback_result['trade_entries']
    comb_entries = combined_result['trade_entries']

    overlap = base_entries & pull_entries
    base_only = base_entries - pull_entries
    pull_only = pull_entries - base_entries

    print(f"  Baseline trades:       {len(base_entries)}")
    print(f"  EMA pullback trades:   {len(pull_entries)}")
    print(f"  Combined trades:       {len(comb_entries)}")
    print(f"  Overlapping entries:   {len(overlap)}")
    print(f"  Baseline-only entries: {len(base_only)}")
    print(f"  Pullback-only entries: {len(pull_only)}")

    if len(base_entries) > 0:
        pct_overlap = len(overlap) / len(base_entries) * 100
        print(f"  Overlap (% of baseline): {pct_overlap:.1f}%")

    # New trades added by combined vs baseline
    new_in_combined = comb_entries - base_entries
    print(f"  New trades in combined (vs baseline): {len(new_in_combined)}")

    # Analyze PnL of pullback-only trades
    pull_trades = pullback_result['closed_trades']
    if pull_trades:
        pnl_list = [t.pnl for t in pull_trades]
        avg_pnl = np.mean(pnl_list)
        median_pnl = np.median(pnl_list)
        print(f"\n  EMA Pullback trade quality:")
        print(f"    Avg PnL:    ${avg_pnl:+,.0f}")
        print(f"    Median PnL: ${median_pnl:+,.0f}")
        print(f"    Best:       ${max(pnl_list):+,.0f}")
        print(f"    Worst:      ${min(pnl_list):+,.0f}")


def main():
    print("=" * 70)
    print("  s98 EMA PULLBACK TRIGGER STUDY")
    print("  Strategy: s98_sr_breakout_swing (Regime-State MACD Squeeze)")
    print(f"  Period: {START} to {END} (L12M)")
    print(f"  Engine: v4 portfolio  |  Capital: ${CAPITAL:,}")
    print("=" * 70)

    results = []

    # 1. Baseline (original MACD)
    r1 = run_variant("BASELINE: MACD zero-cross (original s98)", _original_strategy)
    results.append(r1)

    # 2. EMA pullback only
    r2 = run_variant("EMA PULLBACK ONLY (replaces MACD)", strategy_ema_pullback_only)
    results.append(r2)

    # 3. Combined (MACD OR EMA pullback)
    r3 = run_variant("COMBINED: MACD OR EMA pullback", strategy_combined)
    results.append(r3)

    # ── Overlap analysis ──
    overlap_analysis(r1, r2, r3)

    # ── Summary table ──
    print(f"\n\n{'='*110}")
    print("  SUMMARY TABLE")
    print(f"{'='*110}")
    print(f"  {'Variant':<45} {'Trades':>7} {'RawEnt':>7} {'WR%':>6} {'Ret%':>8} "
          f"{'Sharpe':>7} {'Calmar':>7} {'MaxDD%':>7} {'PF':>6} {'Sortino':>8}")
    print(f"  {'-'*108}")

    for r in results:
        pf_str = f"{r['profit_factor']:.2f}" if r['profit_factor'] != float('inf') else 'inf'
        print(f"  {r['label']:<45} {r['trades']:>7} {r['raw_entries']:>7} "
              f"{r['win_rate']:>5.1f}% {r['total_return']:>+7.1f}% "
              f"{r['sharpe']:>7.2f} {r['calmar']:>7.2f} {r['max_dd']:>6.1f}% "
              f"{pf_str:>6} {r['sortino']:>8.2f}")

    # ── Verdict ──
    print(f"\n{'='*70}")
    print("  VERDICT")
    print(f"{'='*70}")

    base = results[0]
    pull = results[1]
    comb = results[2]

    # Trade frequency improvement
    if base['trades'] > 0:
        pull_freq_change = (pull['trades'] - base['trades']) / base['trades'] * 100
        comb_freq_change = (comb['trades'] - base['trades']) / base['trades'] * 100
        print(f"  Trade frequency vs baseline:")
        print(f"    EMA pullback only: {pull['trades']} trades ({pull_freq_change:+.0f}%)")
        print(f"    Combined:          {comb['trades']} trades ({comb_freq_change:+.0f}%)")

    # Quality comparison
    print(f"\n  Quality comparison:")
    print(f"    {'Metric':<15} {'Baseline':>10} {'Pullback':>10} {'Combined':>10}")
    print(f"    {'-'*50}")
    for metric, key in [('Sharpe', 'sharpe'), ('Calmar', 'calmar'),
                         ('Max DD%', 'max_dd'), ('Return%', 'total_return'),
                         ('Win Rate%', 'win_rate')]:
        print(f"    {metric:<15} {base[key]:>10.2f} {pull[key]:>10.2f} {comb[key]:>10.2f}")

    # Recommendation
    print(f"\n  Recommendation:")
    if comb['sharpe'] > base['sharpe'] * 0.85 and comb['trades'] > base['trades'] * 1.2:
        print(f"    ADOPT COMBINED — more trades with acceptable quality retention")
    elif pull['sharpe'] > base['sharpe'] * 0.85 and pull['trades'] > base['trades']:
        print(f"    CONSIDER EMA PULLBACK — quality maintained with different timing")
    else:
        print(f"    KEEP BASELINE — new trigger does not add sufficient value")

    print()


if __name__ == '__main__':
    main()
