#!/usr/bin/env python3
"""
s98 RSI Pullback Trigger Research
==================================

Tests RSI pullback recovery as an alternative/additional entry trigger for s98.

RSI Pullback Recovery logic:
  LONG:  RSI drops below 40 then crosses back above 40, in UPTREND regime
  SHORT: RSI rises above 60 then crosses back below 60, in DOWNTREND regime

Three variants compared against L12M (2025-04-01 to 2026-04-01):
  1. BASELINE: Original s98 (MACD zero-cross + regime change)
  2. RSI_ONLY: Replace MACD triggers with RSI pullback triggers
  3. COMBINED: MACD OR RSI pullback (union of both triggers)

All other filters (ADX, DI, liquidity, BB squeeze) are kept identical.

Usage:
    /workspace/venv/bin/python research/s98_trigger_rsi_pullback.py
"""

import sys
import os
import time
import types
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
MONTHS = 12
MARKET = 'perp'
START = '2025-04-01'
END = '2026-04-01'


# ════════════════════════════════════════════════════════════════════
# STRATEGY VARIANTS
# ════════════════════════════════════════════════════════════════════

def strategy_rsi_only(ctx: StrategyContext) -> StrategyResult:
    """s98 with MACD triggers replaced by RSI pullback recovery."""
    close = ctx.ind_1h['close']
    volume = ctx.ind_1h['volume']
    n = len(close)
    rsi = ctx.ind_1h['rsi']
    adx = ctx.ind_1h['adx']
    plus_di = ctx.ind_1h['plus_di']
    minus_di = ctx.ind_1h['minus_di']
    bb_width = ctx.ind_1h['bb_width']
    regime = ctx.regime_1h

    LEVERAGE = s98_mod.LEVERAGE
    WARMUP = s98_mod.WARMUP
    MIN_ADV_USD = s98_mod.MIN_ADV_USD
    ADX_THRESH = s98_mod.ADX_THRESH
    BB_LOOKBACK = s98_mod.BB_LOOKBACK
    SQUEEZE_MULT = s98_mod.SQUEEZE_MULT

    # ── Liquidity filter ──
    dollar_vol = close * volume
    rolling_adv = rolling_mean(dollar_vol, 24) * 24
    liquid = rolling_adv > MIN_ADV_USD

    # ── BB squeeze filter ──
    bb_avg = rolling_mean(bb_width, BB_LOOKBACK)
    squeeze = bb_width < bb_avg * SQUEEZE_MULT

    # ── Regime filter ──
    uptrend = regime == UPTREND
    downtrend = regime == DOWNTREND
    adx_ok = adx > ADX_THRESH
    long_di = plus_di > minus_di
    short_di = minus_di > plus_di

    # ── RSI pullback recovery trigger ──
    rsi_prev = np.roll(rsi, 1)
    rsi_prev[0] = 50.0

    # RSI crosses above 40 from below (recovery from oversold in uptrend)
    rsi_pullback_long = (rsi > 40) & (rsi_prev <= 40)
    entry_long = rsi_pullback_long & uptrend & adx_ok & long_di & liquid & squeeze

    # RSI crosses below 60 from above (overbought fade in downtrend)
    rsi_pullback_short = (rsi < 60) & (rsi_prev >= 60)
    entry_short = rsi_pullback_short & downtrend & adx_ok & short_di & liquid & squeeze

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
        name='s98_rsi_pullback',
    )


def strategy_combined(ctx: StrategyContext) -> StrategyResult:
    """s98 with MACD OR RSI pullback triggers (union)."""
    close = ctx.ind_1h['close']
    volume = ctx.ind_1h['volume']
    n = len(close)
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

    LEVERAGE = s98_mod.LEVERAGE
    WARMUP = s98_mod.WARMUP
    MIN_ADV_USD = s98_mod.MIN_ADV_USD
    ADX_THRESH = s98_mod.ADX_THRESH
    BB_LOOKBACK = s98_mod.BB_LOOKBACK
    SQUEEZE_MULT = s98_mod.SQUEEZE_MULT

    # ── Liquidity filter ──
    dollar_vol = close * volume
    rolling_adv = rolling_mean(dollar_vol, 24) * 24
    liquid = rolling_adv > MIN_ADV_USD

    # ── BB squeeze filter ──
    bb_avg = rolling_mean(bb_width, BB_LOOKBACK)
    squeeze = bb_width < bb_avg * SQUEEZE_MULT

    # ── Regime filter ──
    uptrend = regime == UPTREND
    downtrend = regime == DOWNTREND
    adx_ok = adx > ADX_THRESH
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

    # ── MACD signals (original) ──
    macd_bull = macd > 0
    macd_bear = macd < 0
    macd_prev = np.roll(macd, 1)
    macd_prev[0] = np.nan
    macd_cross_bull = (macd > 0) & (macd_prev <= 0)
    macd_cross_bear = (macd < 0) & (macd_prev >= 0)

    # ── RSI pullback recovery trigger ──
    rsi_prev = np.roll(rsi, 1)
    rsi_prev[0] = 50.0
    rsi_pullback_long = (rsi > 40) & (rsi_prev <= 40)
    rsi_pullback_short = (rsi < 60) & (rsi_prev >= 60)

    # ── Combined entry signals (MACD OR RSI pullback) ──
    # Original MACD triggers
    macd_long = (
        (regime_change_up & macd_bull & ema_bull) | (macd_cross_bull & uptrend)
    )
    macd_short = (
        (regime_change_down & macd_bear & ema_bear) | (macd_cross_bear & downtrend)
    )

    # RSI pullback triggers
    rsi_long = rsi_pullback_long & uptrend
    rsi_short = rsi_pullback_short & downtrend

    # Union of both trigger types, same filters
    entry_long = (macd_long | rsi_long) & adx_ok & long_di & liquid & squeeze
    entry_short = (macd_short | rsi_short) & adx_ok & short_di & liquid & squeeze

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


# ════════════════════════════════════════════════════════════════════
# BACKTEST RUNNER
# ════════════════════════════════════════════════════════════════════

def run_variant(label: str, strategy_fn) -> dict:
    """Run s98 with a patched strategy function, return metrics."""
    # Monkey-patch the strategy function in the cached module
    s98_mod.strategy = strategy_fn

    end_date = pd.Timestamp(END)

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
    print(f"  Period: {START} to {END}")
    print(f"{'='*70}")

    t0 = time.perf_counter()
    signals = precompute_strategy_signals(spec, tokens, config, MONTHS, end_date=end_date)
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
    print("=" * 70)
    print("  s98 RSI PULLBACK TRIGGER RESEARCH")
    print("  Comparing: BASELINE vs RSI_ONLY vs COMBINED (MACD+RSI)")
    print(f"  Period: L12M ({START} to {END})")
    print("=" * 70)

    results = []

    # 1. BASELINE — original s98
    r = run_variant("BASELINE: Original s98 (MACD zero-cross)", _original_strategy)
    results.append(r)

    # 2. RSI_ONLY — replace MACD with RSI pullback
    r = run_variant("RSI_ONLY: RSI pullback recovery (replaces MACD)", strategy_rsi_only)
    results.append(r)

    # 3. COMBINED — MACD OR RSI pullback
    r = run_variant("COMBINED: MACD OR RSI pullback", strategy_combined)
    results.append(r)

    # Restore original strategy
    s98_mod.strategy = _original_strategy

    # ── Summary table ──
    print(f"\n\n{'='*100}")
    print("  COMPARISON SUMMARY")
    print(f"{'='*100}")
    print(f"  {'Variant':<45} {'Trades':>7} {'RawEnt':>7} {'WR%':>6} "
          f"{'Ret%':>8} {'Sharpe':>7} {'Calmar':>7} {'MaxDD%':>7} {'Sortino':>8}")
    print(f"  {'-'*98}")

    for r in results:
        print(f"  {r['label']:<45} {r['trades']:>7} {r['raw_entries']:>7} "
              f"{r['win_rate']:>5.1f}% {r['total_return']:>+7.1f}% "
              f"{r['sharpe']:>7.2f} {r['calmar']:>7.2f} {r['max_dd']:>6.1f}% "
              f"{r['sortino']:>8.2f}")

    # ── Analysis ──
    baseline = results[0]
    rsi_only = results[1]
    combined = results[2]

    print(f"\n  ANALYSIS:")
    print(f"  - Baseline trades: {baseline['trades']}")
    print(f"  - RSI-only trades: {rsi_only['trades']} "
          f"({rsi_only['trades'] - baseline['trades']:+d} vs baseline)")
    print(f"  - Combined trades: {combined['trades']} "
          f"({combined['trades'] - baseline['trades']:+d} vs baseline)")

    if baseline['trades'] > 0:
        trade_increase = (combined['trades'] - baseline['trades']) / baseline['trades'] * 100
        print(f"  - Combined trade increase: {trade_increase:+.0f}%")

    print(f"\n  - Baseline Sharpe: {baseline['sharpe']:.2f}")
    print(f"  - RSI-only Sharpe: {rsi_only['sharpe']:.2f} "
          f"({rsi_only['sharpe'] - baseline['sharpe']:+.2f})")
    print(f"  - Combined Sharpe: {combined['sharpe']:.2f} "
          f"({combined['sharpe'] - baseline['sharpe']:+.2f})")

    # Verdict
    print(f"\n  VERDICT:")
    if combined['sharpe'] >= baseline['sharpe'] * 0.9 and combined['trades'] > baseline['trades']:
        print(f"  RSI pullback trigger ADDS TRADES without major Sharpe degradation.")
        print(f"  Consider promoting to s98 variant or combined strategy.")
    elif rsi_only['sharpe'] > baseline['sharpe']:
        print(f"  RSI-only OUTPERFORMS baseline on Sharpe. Consider as replacement trigger.")
    else:
        print(f"  RSI pullback trigger does not improve the strategy meaningfully.")

    print(f"\n{'='*100}")
    print("  END OF s98 RSI PULLBACK TRIGGER RESEARCH")
    print(f"{'='*100}")


if __name__ == '__main__':
    main()
