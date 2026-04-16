#!/usr/bin/env python3
"""
s98 Multi-Trigger Validation
==============================

Task 1: Test TRIPLE combo (MACD + RSI + Donchian) vs MACD+RSI (winner from prior research).
Task 2: Run the best variant through L12M/L6M/L3M windows.

Variants compared:
  1. BASELINE: Original s98 (MACD zero-cross + regime change)
  2. MACD+RSI: MACD OR RSI pullback (prior winner: 186 trades, +245.4%, Sharpe 1.71)
  3. TRIPLE: MACD OR RSI pullback OR Donchian breakout

Usage:
    /workspace/venv/bin/python research/s98_multi_trigger_validate.py
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
from v4.engine import _load_strategy_fn, _STRATEGY_MODULE_CACHE, rolling_mean

from engine import (StrategyContext, StrategyResult, MarketType,
                    CRISIS, DOWNTREND, UPTREND, RANGE, QUIET)

# Force-load the strategy module into cache so we can patch it
_load_strategy_fn('s98')
s98_mod = _STRATEGY_MODULE_CACHE['s98']

# Save original strategy function
_original_strategy = s98_mod.strategy

# ── Configuration ───────────────────────────────────────────────────
CAPITAL = 100_000
MARKET = 'perp'


# ════════════════════════════════════════════════════════════════════
# STRATEGY VARIANTS
# ════════════════════════════════════════════════════════════════════

def strategy_macd_rsi(ctx: StrategyContext) -> StrategyResult:
    """s98 with MACD + RSI pullback triggers."""
    close = ctx.ind_1h['close']
    volume = ctx.ind_1h['volume']
    macd = ctx.ind_1h['macd']
    rsi = ctx.ind_1h['rsi']
    adx = ctx.ind_1h['adx']
    plus_di = ctx.ind_1h['plus_di']
    minus_di = ctx.ind_1h['minus_di']
    ema10 = ctx.ind_1h['ema_10']
    ema20 = ctx.ind_1h['ema_20']
    ema50 = ctx.ind_1h['ema_50']
    bb_width = ctx.ind_1h['bb_width']
    regime = ctx.regime_1h

    # Filters (same as s98)
    dollar_vol = close * volume
    rolling_adv = rolling_mean(dollar_vol, 24) * 24
    liquid = rolling_adv > s98_mod.MIN_ADV_USD
    bb_avg = rolling_mean(bb_width, s98_mod.BB_LOOKBACK)
    squeeze = bb_width < bb_avg * s98_mod.SQUEEZE_MULT
    uptrend = regime == UPTREND
    downtrend = regime == DOWNTREND
    adx_ok = adx > s98_mod.ADX_THRESH
    long_di = plus_di > minus_di
    short_di = minus_di > plus_di

    common_long = uptrend & adx_ok & long_di & liquid & squeeze
    common_short = downtrend & adx_ok & short_di & liquid & squeeze

    # Trigger 1: MACD zero-cross (original)
    regime_prev = np.roll(regime, 1); regime_prev[0] = regime[0]
    regime_change_up = (regime == UPTREND) & (regime_prev != UPTREND)
    regime_change_down = (regime == DOWNTREND) & (regime_prev != DOWNTREND)
    ema_bull = (ema10 > ema20) & (ema20 > ema50)
    ema_bear = (ema10 < ema20) & (ema20 < ema50)
    macd_bull = macd > 0; macd_bear = macd < 0
    macd_prev = np.roll(macd, 1); macd_prev[0] = np.nan
    macd_cross_bull = (macd > 0) & (macd_prev <= 0)
    macd_cross_bear = (macd < 0) & (macd_prev >= 0)
    macd_long = (regime_change_up & macd_bull & ema_bull) | (macd_cross_bull & uptrend)
    macd_short = (regime_change_down & macd_bear & ema_bear) | (macd_cross_bear & downtrend)

    # Trigger 2: RSI pullback recovery
    rsi_prev = np.roll(rsi, 1); rsi_prev[0] = 50.0
    rsi_pullback_long = (rsi > 40) & (rsi_prev <= 40)
    rsi_pullback_short = (rsi < 60) & (rsi_prev >= 60)

    # Combined: MACD OR RSI
    entry_long = (macd_long | rsi_pullback_long) & common_long
    entry_short = (macd_short | rsi_pullback_short) & common_short

    entry_mask = entry_long | entry_short
    direction = np.where(entry_long, 1, np.where(entry_short, -1, 0)).astype(np.int8)
    entry_mask[:s98_mod.WARMUP] = False
    if ctx.liquidity_mask is not None:
        entry_mask = entry_mask & ctx.liquidity_mask

    return StrategyResult(
        entry_mask=entry_mask, direction=direction,
        market_type=MarketType.PERP, leverage=s98_mod.LEVERAGE,
        stop_mult=s98_mod.STOP_MULT, trail_mult=s98_mod.TRAIL_MULT,
        target_mult=s98_mod.TARGET_MULT, no_stop_bars=s98_mod.NO_STOP_BARS,
        min_hold=s98_mod.MIN_HOLD, max_hold=s98_mod.MAX_HOLD,
        edge=s98_mod.EDGE, exit_regimes={CRISIS},
        breakeven_atr=s98_mod.BREAKEVEN_ATR, exchange='binance',
        name='s98_macd_rsi',
    )


def strategy_triple(ctx: StrategyContext) -> StrategyResult:
    """s98 with MACD + RSI + Donchian triggers."""
    close = ctx.ind_1h['close']
    high = ctx.ind_1h['high']
    low = ctx.ind_1h['low']
    volume = ctx.ind_1h['volume']
    macd = ctx.ind_1h['macd']
    rsi = ctx.ind_1h['rsi']
    adx = ctx.ind_1h['adx']
    plus_di = ctx.ind_1h['plus_di']
    minus_di = ctx.ind_1h['minus_di']
    ema10 = ctx.ind_1h['ema_10']
    ema20 = ctx.ind_1h['ema_20']
    ema50 = ctx.ind_1h['ema_50']
    bb_width = ctx.ind_1h['bb_width']
    regime = ctx.regime_1h

    dollar_vol = close * volume
    rolling_adv = rolling_mean(dollar_vol, 24) * 24
    liquid = rolling_adv > s98_mod.MIN_ADV_USD
    bb_avg = rolling_mean(bb_width, s98_mod.BB_LOOKBACK)
    squeeze = bb_width < bb_avg * s98_mod.SQUEEZE_MULT
    uptrend = regime == UPTREND
    downtrend = regime == DOWNTREND
    adx_ok = adx > s98_mod.ADX_THRESH
    long_di = plus_di > minus_di
    short_di = minus_di > plus_di
    common_long = uptrend & adx_ok & long_di & liquid & squeeze
    common_short = downtrend & adx_ok & short_di & liquid & squeeze

    # MACD trigger
    regime_prev = np.roll(regime, 1); regime_prev[0] = regime[0]
    regime_change_up = (regime == UPTREND) & (regime_prev != UPTREND)
    regime_change_down = (regime == DOWNTREND) & (regime_prev != DOWNTREND)
    ema_bull = (ema10 > ema20) & (ema20 > ema50)
    ema_bear = (ema10 < ema20) & (ema20 < ema50)
    macd_bull = macd > 0; macd_bear = macd < 0
    macd_prev = np.roll(macd, 1); macd_prev[0] = np.nan
    macd_cross_bull = (macd > 0) & (macd_prev <= 0)
    macd_cross_bear = (macd < 0) & (macd_prev >= 0)
    macd_long = (regime_change_up & macd_bull & ema_bull) | (macd_cross_bull & uptrend)
    macd_short = (regime_change_down & macd_bear & ema_bear) | (macd_cross_bear & downtrend)

    # RSI pullback trigger
    rsi_prev = np.roll(rsi, 1); rsi_prev[0] = 50.0
    rsi_pullback_long = (rsi > 40) & (rsi_prev <= 40)
    rsi_pullback_short = (rsi < 60) & (rsi_prev >= 60)

    # Donchian breakout trigger (20-day / 480h channel)
    donchian_high = pd.Series(high).rolling(480, min_periods=240).max().values
    donchian_low = pd.Series(low).rolling(480, min_periods=240).min().values
    donchian_high_prev = np.roll(donchian_high, 1)
    donchian_low_prev = np.roll(donchian_low, 1)
    donchian_break_long = close > donchian_high_prev
    donchian_break_short = close < donchian_low_prev

    # Triple combo: MACD OR RSI OR Donchian
    entry_long = (macd_long | rsi_pullback_long | donchian_break_long) & common_long
    entry_short = (macd_short | rsi_pullback_short | donchian_break_short) & common_short

    entry_mask = entry_long | entry_short
    direction = np.where(entry_long, 1, np.where(entry_short, -1, 0)).astype(np.int8)
    entry_mask[:s98_mod.WARMUP] = False
    if ctx.liquidity_mask is not None:
        entry_mask = entry_mask & ctx.liquidity_mask

    return StrategyResult(
        entry_mask=entry_mask, direction=direction,
        market_type=MarketType.PERP, leverage=s98_mod.LEVERAGE,
        stop_mult=s98_mod.STOP_MULT, trail_mult=s98_mod.TRAIL_MULT,
        target_mult=s98_mod.TARGET_MULT, no_stop_bars=s98_mod.NO_STOP_BARS,
        min_hold=s98_mod.MIN_HOLD, max_hold=s98_mod.MAX_HOLD,
        edge=s98_mod.EDGE, exit_regimes={CRISIS},
        breakeven_atr=s98_mod.BREAKEVEN_ATR, exchange='binance',
        name='s98_triple',
    )


# ════════════════════════════════════════════════════════════════════
# RUNNER
# ════════════════════════════════════════════════════════════════════

def run_variant(label: str, strategy_fn, start: str, end: str, months: int) -> dict:
    """Run s98 with a patched strategy function, return metrics."""
    s98_mod.strategy = strategy_fn

    end_date = pd.Timestamp(end)

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

    tokens = discover_tokens(MARKET)

    print(f"\n{'='*70}")
    print(f"  {label}")
    print(f"  Tokens: {len(tokens)}, Months: {months}, Capital: ${CAPITAL:,}")
    print(f"  Period: {start} to {end}")
    print(f"{'='*70}")

    t0 = time.perf_counter()
    signals = precompute_strategy_signals(spec, tokens, config, months, end_date=end_date)
    t_sig = time.perf_counter() - t0

    # Count raw entries
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


# ════════════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════════════

def main():
    # ── Task 1: L12M comparison — Baseline vs MACD+RSI vs Triple ──
    print("=" * 70)
    print("  TASK 1: MACD+RSI vs TRIPLE (MACD+RSI+Donchian)")
    print("  Does Donchian add value on top of MACD+RSI?")
    print("  Period: L12M (2025-04-01 to 2026-04-01)")
    print("=" * 70)

    task1_results = []

    r = run_variant("BASELINE: Original s98 (MACD only)",
                    _original_strategy, '2025-04-01', '2026-04-01', 12)
    task1_results.append(r)

    r = run_variant("MACD+RSI: MACD OR RSI pullback",
                    strategy_macd_rsi, '2025-04-01', '2026-04-01', 12)
    task1_results.append(r)

    r = run_variant("TRIPLE: MACD OR RSI OR Donchian",
                    strategy_triple, '2025-04-01', '2026-04-01', 12)
    task1_results.append(r)

    # Task 1 summary
    print(f"\n\n{'='*100}")
    print("  TASK 1 SUMMARY: L12M COMPARISON")
    print(f"{'='*100}")
    print(f"  {'Variant':<40} {'Trades':>7} {'RawEnt':>7} {'WR%':>6} "
          f"{'Ret%':>8} {'Sharpe':>7} {'Calmar':>7} {'MaxDD%':>7} {'Sortino':>8}")
    print(f"  {'-'*98}")
    for r in task1_results:
        print(f"  {r['label']:<40} {r['trades']:>7} {r['raw_entries']:>7} "
              f"{r['win_rate']:>5.1f}% {r['total_return']:>+7.1f}% "
              f"{r['sharpe']:>7.2f} {r['calmar']:>7.2f} {r['max_dd']:>6.1f}% "
              f"{r['sortino']:>8.2f}")

    # Determine winner
    macd_rsi = task1_results[1]
    triple = task1_results[2]

    donchian_adds_value = (triple['sharpe'] > macd_rsi['sharpe'] and
                           triple['calmar'] >= macd_rsi['calmar'] * 0.9)

    print(f"\n  DONCHIAN ADDS VALUE? ", end="")
    if donchian_adds_value:
        print("YES — Triple outperforms MACD+RSI on Sharpe with acceptable Calmar.")
        best_name = "TRIPLE"
        best_fn = strategy_triple
    else:
        print("NO — MACD+RSI remains the better variant.")
        best_name = "MACD+RSI"
        best_fn = strategy_macd_rsi

    sharpe_delta = triple['sharpe'] - macd_rsi['sharpe']
    calmar_delta = triple['calmar'] - macd_rsi['calmar']
    trade_delta = triple['trades'] - macd_rsi['trades']
    print(f"  Triple vs MACD+RSI: Sharpe {sharpe_delta:+.2f}, "
          f"Calmar {calmar_delta:+.2f}, Trades {trade_delta:+d}")

    # ── Task 2: Multi-window validation of best variant ──
    print(f"\n\n{'='*70}")
    print(f"  TASK 2: MULTI-WINDOW VALIDATION — {best_name}")
    print(f"  Windows: L12M, L6M, L3M")
    print(f"{'='*70}")

    windows = {
        'L12M': {'start': '2025-04-01', 'end': '2026-04-01', 'months': 12},
        'L6M':  {'start': '2025-10-01', 'end': '2026-04-01', 'months': 6},
        'L3M':  {'start': '2026-01-01', 'end': '2026-04-01', 'months': 3},
    }

    # Run best variant across all windows
    best_results = []
    for wname, w in windows.items():
        r = run_variant(f"{best_name} — {wname}",
                        best_fn, w['start'], w['end'], w['months'])
        best_results.append(r)

    # Also run MACD+RSI across windows if Triple won, for comparison
    if best_name == "TRIPLE":
        alt_name = "MACD+RSI"
        alt_fn = strategy_macd_rsi
    else:
        alt_name = "TRIPLE"
        alt_fn = strategy_triple

    alt_results = []
    for wname, w in windows.items():
        r = run_variant(f"{alt_name} — {wname}",
                        alt_fn, w['start'], w['end'], w['months'])
        alt_results.append(r)

    # Task 2 summary
    print(f"\n\n{'='*100}")
    print(f"  TASK 2 SUMMARY: MULTI-WINDOW COMPARISON")
    print(f"{'='*100}")
    print(f"  {'Variant':<40} {'Trades':>7} {'RawEnt':>7} {'WR%':>6} "
          f"{'Ret%':>8} {'Sharpe':>7} {'Calmar':>7} {'MaxDD%':>7} {'Sortino':>8}")
    print(f"  {'-'*98}")

    print(f"\n  {best_name}:")
    for r in best_results:
        print(f"  {r['label']:<40} {r['trades']:>7} {r['raw_entries']:>7} "
              f"{r['win_rate']:>5.1f}% {r['total_return']:>+7.1f}% "
              f"{r['sharpe']:>7.2f} {r['calmar']:>7.2f} {r['max_dd']:>6.1f}% "
              f"{r['sortino']:>8.2f}")

    print(f"\n  {alt_name}:")
    for r in alt_results:
        print(f"  {r['label']:<40} {r['trades']:>7} {r['raw_entries']:>7} "
              f"{r['win_rate']:>5.1f}% {r['total_return']:>+7.1f}% "
              f"{r['sharpe']:>7.2f} {r['calmar']:>7.2f} {r['max_dd']:>6.1f}% "
              f"{r['sortino']:>8.2f}")

    # Final verdict
    print(f"\n\n{'='*70}")
    print(f"  FINAL VERDICT")
    print(f"{'='*70}")

    # Check consistency across windows for best variant
    best_sharpes = [r['sharpe'] for r in best_results]
    best_all_positive = all(s > 0 for s in best_sharpes)
    best_min_sharpe = min(best_sharpes)

    alt_sharpes = [r['sharpe'] for r in alt_results]
    alt_all_positive = all(s > 0 for s in alt_sharpes)
    alt_min_sharpe = min(alt_sharpes)

    print(f"  {best_name}: Sharpes = {[f'{s:.2f}' for s in best_sharpes]}, "
          f"All positive: {best_all_positive}, Min: {best_min_sharpe:.2f}")
    print(f"  {alt_name}: Sharpes = {[f'{s:.2f}' for s in alt_sharpes]}, "
          f"All positive: {alt_all_positive}, Min: {alt_min_sharpe:.2f}")

    # Count windows won by each
    best_wins = sum(1 for b, a in zip(best_sharpes, alt_sharpes) if b >= a)
    alt_wins = 3 - best_wins

    print(f"\n  Windows won: {best_name}={best_wins}, {alt_name}={alt_wins}")

    if best_wins >= 2 and best_all_positive:
        print(f"\n  WINNER: {best_name}")
        print(f"  Consistent positive Sharpe across all windows, wins majority.")
    elif alt_wins >= 2 and alt_all_positive:
        print(f"\n  WINNER: {alt_name}")
        print(f"  Wins more windows with consistent positive performance.")
    else:
        print(f"\n  INCONCLUSIVE: Neither variant dominates across all windows.")
        print(f"  Recommend sticking with MACD+RSI as the simpler model.")

    # Restore original strategy
    s98_mod.strategy = _original_strategy

    print(f"\n{'='*70}")
    print("  END OF s98 MULTI-TRIGGER VALIDATION")
    print(f"{'='*70}")


if __name__ == '__main__':
    main()
