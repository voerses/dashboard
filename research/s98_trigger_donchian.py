#!/usr/bin/env python3
"""
s98 Trigger Test: Donchian Breakout
====================================

Tests replacing MACD zero-cross entry trigger with Donchian channel breakout,
keeping all other s98 filters (regime, ADX, DI, liquidity, BB squeeze).

Variants:
  1. BASELINE: Original s98 (MACD zero-cross + regime change)
  2. DONCHIAN: Replace MACD zero-cross with Donchian breakout (keep regime change)
  3. COMBINED: MACD OR Donchian (union of both triggers)

Donchian breakout logic:
  - LONG: close > 20-period (480h) rolling high (prev bar), in UPTREND
  - SHORT: close < 20-period (480h) rolling low (prev bar), in DOWNTREND
  - Classic turtle trading — new highs/lows confirm trend

Usage:
    /workspace/venv/bin/python research/s98_trigger_donchian.py
"""

import sys
import os
import time
import copy
import types
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
from v4.engine import Engine, rolling_mean, rolling_max, rolling_min, _load_strategy_fn, _STRATEGY_MODULE_CACHE

from engine import (StrategyContext, StrategyResult, MarketType,
                    CRISIS, DOWNTREND, UPTREND, RANGE, QUIET)

# ── Configuration ───────────────────────────────────────────────────
CAPITAL = 100_000
MONTHS = 12
MARKET = 'perp'

# Date range: 2025-04-01 to 2026-04-01
END_DATE = pd.Timestamp('2026-04-01')

# Donchian params
DONCHIAN_PERIOD = 480  # 20 days * 24h
DONCHIAN_MIN_PERIODS = 240  # half period


# ── Strategy variants ──────────────────────────────────────────────

def strategy_baseline(ctx: StrategyContext) -> StrategyResult:
    """Original s98: MACD zero-cross + regime change."""
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

    LEVERAGE = 7.0
    WARMUP = 200
    MIN_ADV_USD = 1_000_000_000
    ADX_THRESH = 20
    BB_LOOKBACK = 240
    SQUEEZE_MULT = 0.8

    dollar_vol = close * volume
    rolling_adv = rolling_mean(dollar_vol, 24) * 24
    liquid = rolling_adv > MIN_ADV_USD

    bb_avg = rolling_mean(bb_width, BB_LOOKBACK)
    squeeze = bb_width < bb_avg * SQUEEZE_MULT

    uptrend = regime == UPTREND
    downtrend = regime == DOWNTREND
    adx_ok = adx > ADX_THRESH
    long_di = plus_di > minus_di
    short_di = minus_di > plus_di

    regime_prev = np.roll(regime, 1)
    regime_prev[0] = regime[0]
    regime_change_up = (regime == UPTREND) & (regime_prev != UPTREND)
    regime_change_down = (regime == DOWNTREND) & (regime_prev != DOWNTREND)

    ema_bull = (ema10 > ema20) & (ema20 > ema50)
    ema_bear = (ema10 < ema20) & (ema20 < ema50)

    macd_bull = macd > 0
    macd_bear = macd < 0
    macd_prev = np.roll(macd, 1)
    macd_prev[0] = np.nan
    macd_cross_bull = (macd > 0) & (macd_prev <= 0)
    macd_cross_bear = (macd < 0) & (macd_prev >= 0)

    entry_long = (
        ((regime_change_up & macd_bull & ema_bull) | (macd_cross_bull & uptrend))
        & adx_ok & long_di & liquid & squeeze
    )
    entry_short = (
        ((regime_change_down & macd_bear & ema_bear) | (macd_cross_bear & downtrend))
        & adx_ok & short_di & liquid & squeeze
    )

    entry_mask = entry_long | entry_short
    direction = np.where(entry_long, 1, np.where(entry_short, -1, 0)).astype(np.int8)
    entry_mask[:WARMUP] = False

    if ctx.liquidity_mask is not None:
        entry_mask = entry_mask & ctx.liquidity_mask

    return StrategyResult(
        entry_mask=entry_mask,
        direction=direction,
        market_type=MarketType.PERP,
        leverage=LEVERAGE,
        stop_mult=99.0,
        trail_mult=2.5,
        target_mult=999.0,
        no_stop_bars=72,
        min_hold=24,
        max_hold=720,
        edge=0.40,
        exit_regimes={CRISIS},
        breakeven_atr=0.0,
        exchange='binance',
        name='s98_baseline',
    )


def strategy_donchian(ctx: StrategyContext) -> StrategyResult:
    """s98 with Donchian breakout replacing MACD zero-cross (keep regime change)."""
    close = ctx.ind_1h['close']
    high = ctx.ind_1h['high']
    low = ctx.ind_1h['low']
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

    LEVERAGE = 7.0
    WARMUP = 480  # need full Donchian lookback
    MIN_ADV_USD = 1_000_000_000
    ADX_THRESH = 20
    BB_LOOKBACK = 240
    SQUEEZE_MULT = 0.8

    dollar_vol = close * volume
    rolling_adv = rolling_mean(dollar_vol, 24) * 24
    liquid = rolling_adv > MIN_ADV_USD

    bb_avg = rolling_mean(bb_width, BB_LOOKBACK)
    squeeze = bb_width < bb_avg * SQUEEZE_MULT

    uptrend = regime == UPTREND
    downtrend = regime == DOWNTREND
    adx_ok = adx > ADX_THRESH
    long_di = plus_di > minus_di
    short_di = minus_di > plus_di

    # Regime change (still used as secondary trigger)
    regime_prev = np.roll(regime, 1)
    regime_prev[0] = regime[0]
    regime_change_up = (regime == UPTREND) & (regime_prev != UPTREND)
    regime_change_down = (regime == DOWNTREND) & (regime_prev != DOWNTREND)

    ema_bull = (ema10 > ema20) & (ema20 > ema50)
    ema_bear = (ema10 < ema20) & (ema20 < ema50)

    macd_bull = macd > 0
    macd_bear = macd < 0

    # Donchian channels
    donchian_high = pd.Series(high).rolling(DONCHIAN_PERIOD, min_periods=DONCHIAN_MIN_PERIODS).max().values
    donchian_low = pd.Series(low).rolling(DONCHIAN_PERIOD, min_periods=DONCHIAN_MIN_PERIODS).min().values

    # Breakout: close exceeds previous bar's channel
    donchian_high_prev = np.roll(donchian_high, 1)
    donchian_low_prev = np.roll(donchian_low, 1)
    donchian_high_prev[0] = np.nan
    donchian_low_prev[0] = np.nan

    donchian_break_long = close > donchian_high_prev
    donchian_break_short = close < donchian_low_prev

    # Entry: Donchian breakout replaces MACD zero-cross, keep regime change trigger
    entry_long = (
        ((regime_change_up & macd_bull & ema_bull) | (donchian_break_long & uptrend))
        & adx_ok & long_di & liquid & squeeze
    )
    entry_short = (
        ((regime_change_down & macd_bear & ema_bear) | (donchian_break_short & downtrend))
        & adx_ok & short_di & liquid & squeeze
    )

    entry_mask = entry_long | entry_short
    direction = np.where(entry_long, 1, np.where(entry_short, -1, 0)).astype(np.int8)
    entry_mask[:WARMUP] = False

    if ctx.liquidity_mask is not None:
        entry_mask = entry_mask & ctx.liquidity_mask

    return StrategyResult(
        entry_mask=entry_mask,
        direction=direction,
        market_type=MarketType.PERP,
        leverage=LEVERAGE,
        stop_mult=99.0,
        trail_mult=2.5,
        target_mult=999.0,
        no_stop_bars=72,
        min_hold=24,
        max_hold=720,
        edge=0.40,
        exit_regimes={CRISIS},
        breakeven_atr=0.0,
        exchange='binance',
        name='s98_donchian',
    )


def strategy_combined(ctx: StrategyContext) -> StrategyResult:
    """s98 with MACD OR Donchian (union of both entry triggers)."""
    close = ctx.ind_1h['close']
    high = ctx.ind_1h['high']
    low = ctx.ind_1h['low']
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

    LEVERAGE = 7.0
    WARMUP = 480
    MIN_ADV_USD = 1_000_000_000
    ADX_THRESH = 20
    BB_LOOKBACK = 240
    SQUEEZE_MULT = 0.8

    dollar_vol = close * volume
    rolling_adv = rolling_mean(dollar_vol, 24) * 24
    liquid = rolling_adv > MIN_ADV_USD

    bb_avg = rolling_mean(bb_width, BB_LOOKBACK)
    squeeze = bb_width < bb_avg * SQUEEZE_MULT

    uptrend = regime == UPTREND
    downtrend = regime == DOWNTREND
    adx_ok = adx > ADX_THRESH
    long_di = plus_di > minus_di
    short_di = minus_di > plus_di

    regime_prev = np.roll(regime, 1)
    regime_prev[0] = regime[0]
    regime_change_up = (regime == UPTREND) & (regime_prev != UPTREND)
    regime_change_down = (regime == DOWNTREND) & (regime_prev != DOWNTREND)

    ema_bull = (ema10 > ema20) & (ema20 > ema50)
    ema_bear = (ema10 < ema20) & (ema20 < ema50)

    macd_bull = macd > 0
    macd_bear = macd < 0
    macd_prev = np.roll(macd, 1)
    macd_prev[0] = np.nan
    macd_cross_bull = (macd > 0) & (macd_prev <= 0)
    macd_cross_bear = (macd < 0) & (macd_prev >= 0)

    # Donchian channels
    donchian_high = pd.Series(high).rolling(DONCHIAN_PERIOD, min_periods=DONCHIAN_MIN_PERIODS).max().values
    donchian_low = pd.Series(low).rolling(DONCHIAN_PERIOD, min_periods=DONCHIAN_MIN_PERIODS).min().values

    donchian_high_prev = np.roll(donchian_high, 1)
    donchian_low_prev = np.roll(donchian_low, 1)
    donchian_high_prev[0] = np.nan
    donchian_low_prev[0] = np.nan

    donchian_break_long = close > donchian_high_prev
    donchian_break_short = close < donchian_low_prev

    # Combined: MACD zero-cross OR Donchian breakout OR regime change
    entry_long = (
        (
            (regime_change_up & macd_bull & ema_bull)
            | (macd_cross_bull & uptrend)
            | (donchian_break_long & uptrend)
        )
        & adx_ok & long_di & liquid & squeeze
    )
    entry_short = (
        (
            (regime_change_down & macd_bear & ema_bear)
            | (macd_cross_bear & downtrend)
            | (donchian_break_short & downtrend)
        )
        & adx_ok & short_di & liquid & squeeze
    )

    entry_mask = entry_long | entry_short
    direction = np.where(entry_long, 1, np.where(entry_short, -1, 0)).astype(np.int8)
    entry_mask[:WARMUP] = False

    if ctx.liquidity_mask is not None:
        entry_mask = entry_mask & ctx.liquidity_mask

    return StrategyResult(
        entry_mask=entry_mask,
        direction=direction,
        market_type=MarketType.PERP,
        leverage=LEVERAGE,
        stop_mult=99.0,
        trail_mult=2.5,
        target_mult=999.0,
        no_stop_bars=72,
        min_hold=24,
        max_hold=720,
        edge=0.40,
        exit_regimes={CRISIS},
        breakeven_atr=0.0,
        exchange='binance',
        name='s98_combined',
    )


# ── Runner ─────────────────────────────────────────────────────────

def run_variant(label: str, strategy_fn) -> dict:
    """Run a strategy variant through the v4 portfolio engine."""
    # Monkey-patch the strategy function into the cached module
    _load_strategy_fn('s98')
    mod = _STRATEGY_MODULE_CACHE['s98']
    original_fn = mod.strategy
    mod.strategy = strategy_fn

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
    print(f"  Tokens: {len(tokens)}, Months: {MONTHS}, Capital: ${CAPITAL:,}")
    print(f"  End date: {END_DATE}")
    print(f"{'='*70}")

    t0 = time.perf_counter()
    signals = precompute_strategy_signals(spec, tokens, config, MONTHS, end_date=END_DATE)
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

    # Restore original
    mod.strategy = original_fn

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


# ── Main ───────────────────────────────────────────────────────────

if __name__ == '__main__':
    print("=" * 70)
    print(" s98 TRIGGER TEST: Donchian Breakout")
    print(" Period: L12M (2025-04-01 to 2026-04-01)")
    print("=" * 70)

    results = []

    # 1. Baseline
    r = run_variant("BASELINE: s98 Original (MACD zero-cross)", strategy_baseline)
    results.append(r)

    # 2. Donchian only
    r = run_variant("DONCHIAN: Replace MACD with Donchian breakout", strategy_donchian)
    results.append(r)

    # 3. Combined
    r = run_variant("COMBINED: MACD OR Donchian", strategy_combined)
    results.append(r)

    # ── Summary ────────────────────────────────────────────────────
    print(f"\n\n{'='*70}")
    print(" COMPARISON SUMMARY")
    print(f"{'='*70}")
    print()
    print(f"{'Variant':<45s} {'Trades':>7s} {'Return':>8s} {'Sharpe':>7s} {'Calmar':>7s} {'MaxDD':>7s} {'WR':>6s}")
    print("-" * 88)
    for r in results:
        print(f"{r['label']:<45s} {r['trades']:>7d} {r['total_return']:>+7.1f}% {r['sharpe']:>7.2f} {r['calmar']:>7.2f} {r['max_dd']:>6.1f}% {r['win_rate']:>5.1f}%")

    print()
    # Trade count delta
    base_trades = results[0]['trades']
    for r in results[1:]:
        delta = r['trades'] - base_trades
        pct = (delta / base_trades * 100) if base_trades > 0 else 0
        print(f"  {r['label'][:40]:40s} trade delta: {delta:+d} ({pct:+.0f}%)")

    print(f"\n{'='*70}")
    print(" END OF DONCHIAN TRIGGER TEST")
    print(f"{'='*70}")
