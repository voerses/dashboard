#!/usr/bin/env python3
"""
s98 Triple-Trigger Sensitivity Analysis
=========================================

Part 1: Leverage sweep (1x-10x) on L12M with triple triggers.
Part 2: Key parameter sensitivity (+/-20%) at optimal leverage.

Baseline: s98 triple (MACD + RSI pullback + Donchian breakout)
L12M period: 2025-04-01 to 2026-04-01

Usage:
    /workspace/venv/bin/python research/s98_triple_sensitivity.py
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
START = '2025-04-01'
END = '2026-04-01'
MONTHS = 12

# RSI thresholds (module-level so we can monkey-patch)
RSI_LONG_THRESH = 40
RSI_SHORT_THRESH = 60


# ════════════════════════════════════════════════════════════════════
# TRIPLE-TRIGGER STRATEGY
# ════════════════════════════════════════════════════════════════════

def strategy_triple(ctx: StrategyContext) -> StrategyResult:
    """s98 with MACD + RSI + Donchian triggers."""
    global RSI_LONG_THRESH, RSI_SHORT_THRESH

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

    # RSI pullback trigger (use module-level thresholds)
    rsi_prev = np.roll(rsi, 1); rsi_prev[0] = 50.0
    rsi_pullback_long = (rsi > RSI_LONG_THRESH) & (rsi_prev <= RSI_LONG_THRESH)
    rsi_pullback_short = (rsi < RSI_SHORT_THRESH) & (rsi_prev >= RSI_SHORT_THRESH)

    # Donchian breakout trigger (20-day = 480h)
    donchian_high = pd.Series(high).rolling(480, min_periods=240).max().values
    donchian_low = pd.Series(low).rolling(480, min_periods=240).min().values
    donchian_high_prev = np.roll(donchian_high, 1)
    donchian_low_prev = np.roll(donchian_low, 1)
    donchian_break_long = close > donchian_high_prev
    donchian_break_short = close < donchian_low_prev

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


# Install triple strategy
s98_mod.strategy = strategy_triple


# ════════════════════════════════════════════════════════════════════
# RUNNER
# ════════════════════════════════════════════════════════════════════

def run_once(label: str) -> dict:
    """Run s98 triple through v4 simulator, return metrics dict."""
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

    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")

    t0 = time.perf_counter()
    signals = precompute_strategy_signals(spec, tokens, config, MONTHS, end_date=end_date)
    t_sig = time.perf_counter() - t0

    total_entries = sum(int(np.sum(tsig.entry_mask)) for tsig in signals.values())
    print(f"  Signals: {t_sig:.1f}s, Raw entries: {total_entries}")

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

    print(f"  Sim: {t_sim:.1f}s  |  Trades: {n_trades}  |  WR: {win_rate:.1f}%")
    print(f"  Return: {total_ret:+.1f}%  |  Sharpe: {sharpe:.2f}  |  Calmar: {calmar:.2f}")
    print(f"  MaxDD: {max_dd:.1f}%  |  Sortino: {sortino:.2f}")

    return {
        'label': label,
        'trades': n_trades,
        'win_rate': win_rate,
        'total_return': total_ret,
        'sharpe': sharpe,
        'calmar': calmar,
        'max_dd': max_dd,
        'sortino': sortino,
    }


# ════════════════════════════════════════════════════════════════════
# PART 1: LEVERAGE SWEEP
# ════════════════════════════════════════════════════════════════════

def part1_leverage_sweep():
    """Sweep leverage 1x-10x, return results list and optimal leverage."""
    print("\n" + "=" * 70)
    print("  PART 1: LEVERAGE SWEEP (L12M, triple triggers)")
    print("=" * 70)

    orig_leverage = s98_mod.LEVERAGE
    results = []

    for lev in [1, 2, 3, 4, 5, 6, 7, 8, 10]:
        s98_mod.LEVERAGE = float(lev)
        r = run_once(f"Leverage = {lev}x")
        r['leverage'] = lev
        results.append(r)

    # Restore
    s98_mod.LEVERAGE = orig_leverage

    # Summary table
    print(f"\n\n{'='*90}")
    print("  LEVERAGE SWEEP SUMMARY")
    print(f"{'='*90}")
    print(f"  {'Lev':>4} {'Trades':>7} {'WR%':>6} {'Return%':>9} "
          f"{'Sharpe':>7} {'Calmar':>7} {'MaxDD%':>7} {'Sortino':>8}")
    print(f"  {'-'*88}")
    for r in results:
        print(f"  {r['leverage']:>3}x {r['trades']:>7} {r['win_rate']:>5.1f}% "
              f"{r['total_return']:>+8.1f}% {r['sharpe']:>7.2f} "
              f"{r['calmar']:>7.2f} {r['max_dd']:>6.1f}% {r['sortino']:>8.2f}")

    # Find Calmar-optimal leverage
    best = max(results, key=lambda r: r['calmar'])
    print(f"\n  CALMAR-OPTIMAL LEVERAGE: {best['leverage']}x "
          f"(Calmar={best['calmar']:.2f}, Sharpe={best['sharpe']:.2f}, "
          f"MaxDD={best['max_dd']:.1f}%)")

    return results, best['leverage']


# ════════════════════════════════════════════════════════════════════
# PART 2: PARAMETER SENSITIVITY
# ════════════════════════════════════════════════════════════════════

def part2_param_sensitivity(optimal_leverage: int):
    """Test each parameter at +/-20% from baseline at optimal leverage."""
    global RSI_LONG_THRESH, RSI_SHORT_THRESH

    print(f"\n\n{'='*70}")
    print(f"  PART 2: PARAMETER SENSITIVITY (+/-20%, {optimal_leverage}x leverage)")
    print(f"{'='*70}")

    # Set optimal leverage
    s98_mod.LEVERAGE = float(optimal_leverage)

    # Run baseline first
    baseline = run_once(f"BASELINE (all defaults, {optimal_leverage}x)")
    baseline_calmar = baseline['calmar']

    # Parameter sweep definitions: (base_val, low_val, high_val)
    s98_params = {
        'ADX_THRESH':   (s98_mod.ADX_THRESH,   16,   24),
        'SQUEEZE_MULT': (s98_mod.SQUEEZE_MULT, 0.64, 0.96),
        'BB_LOOKBACK':  (s98_mod.BB_LOOKBACK,  192,  288),
        'TRAIL_MULT':   (s98_mod.TRAIL_MULT,   2.0,  3.0),
        'NO_STOP_BARS': (s98_mod.NO_STOP_BARS, 58,   86),
    }

    sensitivity_results = []

    for param_name, (base_val, low_val, high_val) in s98_params.items():
        for label_suffix, new_val in [('-20%', low_val), ('+20%', high_val)]:
            setattr(s98_mod, param_name, new_val)
            r = run_once(f"{param_name} {label_suffix} ({new_val})")
            r['param'] = param_name
            r['direction'] = label_suffix
            r['value'] = new_val
            sensitivity_results.append(r)
            # Restore to base
            setattr(s98_mod, param_name, base_val)

    # RSI thresholds (module-level globals in THIS script)
    for label_suffix, long_t, short_t in [('-20% (35/65)', 35, 65), ('+20% (45/55)', 45, 55)]:
        RSI_LONG_THRESH = long_t
        RSI_SHORT_THRESH = short_t
        r = run_once(f"RSI thresholds {label_suffix}")
        r['param'] = 'RSI_THRESH'
        r['direction'] = label_suffix.split(' ')[0]  # '-20%' or '+20%'
        r['value'] = f"{long_t}/{short_t}"
        sensitivity_results.append(r)

    # Restore RSI defaults
    RSI_LONG_THRESH = 40
    RSI_SHORT_THRESH = 60

    # Summary table
    print(f"\n\n{'='*100}")
    print(f"  PARAMETER SENSITIVITY SUMMARY (baseline Calmar={baseline_calmar:.2f})")
    print(f"{'='*100}")
    print(f"  {'Parameter':<16} {'Dir':>5} {'Value':>8} {'Trades':>7} {'Return%':>9} "
          f"{'Sharpe':>7} {'Calmar':>7} {'MaxDD%':>7} {'Calmar_Chg':>11} {'Flag':>8}")
    print(f"  {'-'*98}")

    fragile_params = []
    for r in sensitivity_results:
        calmar_chg = ((r['calmar'] - baseline_calmar) / baseline_calmar * 100
                      if baseline_calmar != 0 else 0)
        flag = "FRAGILE" if calmar_chg < -30 else ""
        if flag:
            fragile_params.append(f"{r['param']} {r['direction']}")
        print(f"  {r['param']:<16} {r['direction']:>5} {str(r['value']):>8} "
              f"{r['trades']:>7} {r['total_return']:>+8.1f}% {r['sharpe']:>7.2f} "
              f"{r['calmar']:>7.2f} {r['max_dd']:>6.1f}% {calmar_chg:>+10.1f}% "
              f"{flag:>8}")

    # Fragile parameter summary
    print(f"\n  FRAGILE PARAMETERS (>30% Calmar degradation):")
    if fragile_params:
        for fp in fragile_params:
            print(f"    - {fp}")
    else:
        print(f"    None -- all parameters within 30% Calmar tolerance")

    return baseline, sensitivity_results


# ════════════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════════════

def main():
    t_start = time.perf_counter()

    lev_results, optimal_lev = part1_leverage_sweep()
    baseline, sens_results = part2_param_sensitivity(optimal_lev)

    t_total = time.perf_counter() - t_start

    # Restore original strategy
    s98_mod.strategy = _original_strategy

    print(f"\n\n{'='*70}")
    print(f"  COMPLETE — Total runtime: {t_total/60:.1f} minutes")
    print(f"{'='*70}")


if __name__ == '__main__':
    main()
