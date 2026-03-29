#!/usr/bin/env python3
"""
R160 BB Volatility Breakout — Gate 3: Multi-Token Standalone Evaluation
========================================================================
Gate 1 PASSED on BTC with Sharpe 4.08, Calmar 22.26.
Gate 3 tests:
  1. Per-token raw backtest on 10 tokens
  2. Multi-token portfolio aggregation (L12M/L6M/L3M)
  3. Parameter sensitivity on BTC (BB period, std dev, volume multiplier)
"""
import sys
sys.path.insert(0, '/workspace/crypto_backtest')

import numpy as np
import pandas as pd
from tools.raw_backtest import Backtest

# ── R160 Parameters (from Gate 1) ──────────────────────────────────
BB_PERIOD = 20
BB_STD = 2.0
VOL_LOOKBACK = 20
VOL_THRESHOLD = 1.3
EMA_PERIOD = 20
COOLDOWN_BARS = 8     # 32 hours cooldown between trades
SIZE_USD = 50_000

# ── Token universe ──────────────────────────────────────────────────
TOKENS = ['BTC', 'ETH', 'SOL', 'DOGE', 'XRP', 'AVAX', 'LINK', 'NEAR', 'SUI', 'PEPE']

# ── Backtest parameters ────────────────────────────────────────────
BT_PARAMS = dict(
    capital=100_000,
    fee_bps=7,
    market='perp',
    leverage_max=1.0,
    start='2024-01-01',
    end='2026-03-17',
)


def generate_r160_signal(df_4h, bb_period=BB_PERIOD, bb_std=BB_STD,
                          vol_lookback=VOL_LOOKBACK, vol_threshold=VOL_THRESHOLD,
                          ema_period=EMA_PERIOD, cooldown_bars=COOLDOWN_BARS):
    """Generate R160 BB breakout signal on 4H data. Returns pd.Series of 0/1."""
    sma = df_4h['close'].rolling(bb_period).mean()
    std_dev = df_4h['close'].rolling(bb_period).std()
    upper_bb = sma + bb_std * std_dev
    middle_bb = sma

    vol_avg = df_4h['volume'].rolling(vol_lookback).mean()
    ema = df_4h['close'].ewm(span=ema_period, adjust=False).mean()

    # Entry conditions
    entry_cond = (
        (df_4h['close'] > upper_bb) &
        (df_4h['volume'] > vol_threshold * vol_avg) &
        (df_4h['close'] > ema)
    )

    # Signal generation with cooldown
    signal = pd.Series(0, index=df_4h.index, dtype=int)
    in_position = False
    bars_since_exit = cooldown_bars + 1  # Allow entry on first bar

    for i in range(bb_period, len(df_4h)):
        close = df_4h['close'].iloc[i]

        if in_position:
            if close < middle_bb.iloc[i]:
                signal.iloc[i] = 0
                in_position = False
                bars_since_exit = 0
            else:
                signal.iloc[i] = 1
        else:
            bars_since_exit += 1
            if entry_cond.iloc[i] and bars_since_exit > cooldown_bars:
                signal.iloc[i] = 1
                in_position = True
            else:
                signal.iloc[i] = 0

    return signal


def resample_to_4h(df_1h):
    """Resample 1H data to 4H."""
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    if "funding_1h" in df_1h.columns:
        agg["funding_1h"] = "sum"
    return df_1h.resample('4h').agg(agg).dropna(subset=['close'])


# ═══════════════════════════════════════════════════════════════════════
# PART 1: Per-Token Raw Backtest
# ═══════════════════════════════════════════════════════════════════════
print("=" * 80)
print(" R160 BB BREAKOUT — GATE 3: MULTI-TOKEN EVALUATION")
print("=" * 80)
print()
print("=" * 80)
print(" PART 1: PER-TOKEN RAW BACKTEST")
print("=" * 80)
print()

per_token_results = {}

for token in TOKENS:
    print(f"\n{'─' * 70}")
    print(f" Testing R160 on {token}")
    print(f"{'─' * 70}")

    bt = Backtest(**BT_PARAMS)

    try:
        df_1h = bt.load(token)
        print(f"  Loaded {token} 1H data: {len(df_1h)} bars")
    except FileNotFoundError:
        print(f"  SKIP: No data for {token}")
        per_token_results[token] = {'verdict': 'NO_DATA'}
        continue

    if len(df_1h) < 200:
        print(f"  SKIP: Insufficient data ({len(df_1h)} bars)")
        per_token_results[token] = {'verdict': 'INSUFFICIENT_DATA'}
        continue

    df_4h = resample_to_4h(df_1h)
    print(f"  Resampled to 4H: {len(df_4h)} bars")

    signal = generate_r160_signal(df_4h)
    n_entries = ((signal == 1) & (signal.shift(1) == 0)).sum()
    n_bars_long = (signal == 1).sum()
    print(f"  Signal: {n_entries} entries, {n_bars_long} bars long ({n_bars_long/len(df_4h)*100:.1f}%)")

    bt.from_signals(token=token, signals=signal, size_usd=SIZE_USD, leverage=1.0, timeframe='4h')

    result = bt.report(f'R160 BB Breakout {token} — Gate 3')
    per_token_results[token] = result
    print(f"\n  >>> {token} VERDICT: {result['verdict']}")
    if result['kill_reasons']:
        for r in result['kill_reasons']:
            print(f"      - {r}")

# ── Summary table ──
print("\n\n")
print("=" * 80)
print(" PART 1 SUMMARY: PER-TOKEN VERDICTS")
print("=" * 80)
print(f"\n  {'Token':>6s}  {'Verdict':>8s}  {'L12M Sharpe':>12s}  {'L12M Calmar':>12s}  {'L12M MaxDD':>12s}  {'Trades':>8s}")
print(f"  {'─'*6}  {'─'*8}  {'─'*12}  {'─'*12}  {'─'*12}  {'─'*8}")

pass_count = 0
for token in TOKENS:
    r = per_token_results[token]
    if r['verdict'] in ('NO_DATA', 'INSUFFICIENT_DATA'):
        print(f"  {token:>6s}  {r['verdict']:>8s}  {'—':>12s}  {'—':>12s}  {'—':>12s}  {'—':>8s}")
        continue
    w = r['windows']['L12M']
    trades = w.get('trades', 0)
    v = r['verdict']
    if v == 'PASS':
        pass_count += 1
    print(f"  {token:>6s}  {v:>8s}  {w['sharpe']:>12.2f}  {w['calmar']:>12.2f}  {w['maxdd']:>11.1%}  {trades:>8d}")

total_with_data = sum(1 for r in per_token_results.values() if r['verdict'] not in ('NO_DATA', 'INSUFFICIENT_DATA'))
print(f"\n  PASSED: {pass_count}/{total_with_data} tokens")
print()


# ═══════════════════════════════════════════════════════════════════════
# PART 2: Multi-Token Portfolio
# ═══════════════════════════════════════════════════════════════════════
print("\n\n")
print("=" * 80)
print(" PART 2: MULTI-TOKEN PORTFOLIO BACKTEST")
print("=" * 80)
print()
print("  Running R160 signal independently on each token, aggregated into")
print("  a single portfolio backtest with shared capital.")
print()

bt_portfolio = Backtest(**BT_PARAMS)

# Generate signals for all tokens and feed them through the same backtest instance
# We need to interleave the bar-by-bar processing across tokens
# The simplest correct approach: use the bars() iterator for each token sequentially
# But that won't properly track shared equity across tokens.
#
# Better approach: build a unified timeline, iterate bar by bar across all tokens.

# Step 1: Load all token data and generate 4H signals
token_signals = {}
token_4h_data = {}

for token in TOKENS:
    try:
        df_1h = bt_portfolio.load(token)
        if len(df_1h) < 200:
            print(f"  SKIP {token}: insufficient data")
            continue
        df_4h = resample_to_4h(df_1h)
        signal = generate_r160_signal(df_4h)
        token_signals[token] = signal
        token_4h_data[token] = df_4h
        n_entries = ((signal == 1) & (signal.shift(1) == 0)).sum()
        print(f"  {token}: {n_entries} entries generated")
    except FileNotFoundError:
        print(f"  SKIP {token}: no data")

# Step 2: Build unified timeline of 4H bars across all tokens
# We use from_signals for each token sequentially — each gets its own position slot
# The Backtest harness handles multi-token positions internally via the positions dict

# Re-create backtest for clean state
bt_portfolio = Backtest(**BT_PARAMS)

# Per-token size scaled to portfolio: $50k per signal per token
# With 10 tokens, max exposure = $500k (but leverage=1.0 and capital=$100k
# means equity cap will constrain). Use $50k per signal as specified.

for token in token_signals:
    signal = token_signals[token]
    bt_portfolio.from_signals(
        token=token,
        signals=signal,
        size_usd=SIZE_USD,
        leverage=1.0,
        timeframe='4h',
    )

# Report
result_portfolio = bt_portfolio.report('R160 BB Breakout PORTFOLIO (10 tokens) — Gate 3')
print(f"\n  >>> PORTFOLIO VERDICT: {result_portfolio['verdict']}")
if result_portfolio['kill_reasons']:
    for r in result_portfolio['kill_reasons']:
        print(f"      - {r}")


# ═══════════════════════════════════════════════════════════════════════
# PART 3: Parameter Sensitivity on BTC
# ═══════════════════════════════════════════════════════════════════════
print("\n\n")
print("=" * 80)
print(" PART 3: PARAMETER SENSITIVITY (BTC)")
print("=" * 80)
print()
print("  Testing parameter robustness: BB period, BB std dev, volume multiplier.")
print("  Baseline: BB(20, 2.0), vol_mult=1.3")
print("  Checking that Calmar doesn't degrade >30% with +/-20% param changes.")
print()

# Load BTC data once
bt_base = Backtest(**BT_PARAMS)
df_1h_btc = bt_base.load('BTC')
df_4h_btc = resample_to_4h(df_1h_btc)

# Parameter grid
bb_periods = [16, 20, 24]
bb_stds = [1.6, 2.0, 2.4]
vol_mults = [1.0, 1.3, 1.6]

sensitivity_results = []

# Run baseline first to get reference Calmar
print("  Running parameter grid...")
print()

for bp in bb_periods:
    for bs in bb_stds:
        for vm in vol_mults:
            bt_sens = Backtest(**BT_PARAMS)
            df_1h_sens = bt_sens.load('BTC')
            df_4h_sens = resample_to_4h(df_1h_sens)

            signal = generate_r160_signal(
                df_4h_sens,
                bb_period=bp,
                bb_std=bs,
                vol_threshold=vm,
            )

            n_entries = ((signal == 1) & (signal.shift(1) == 0)).sum()

            bt_sens.from_signals(
                token='BTC',
                signals=signal,
                size_usd=SIZE_USD,
                leverage=1.0,
                timeframe='4h',
            )

            # Compute metrics without printing full report
            eq = pd.Series(bt_sens.mtm_equity_curve)
            if len(eq) < 10:
                sensitivity_results.append({
                    'bb_period': bp, 'bb_std': bs, 'vol_mult': vm,
                    'entries': n_entries,
                    'sharpe': 0, 'calmar': 0, 'maxdd': 0, 'ann_ret': 0,
                    'net_pnl': 0,
                })
                continue

            daily_eq = eq.resample("1D").last().dropna().ffill()
            daily_returns = daily_eq.pct_change().dropna()
            metrics = bt_sens._compute_metrics(daily_returns)

            total_net = sum(t.net_pnl for t in bt_sens.trades)

            sensitivity_results.append({
                'bb_period': bp,
                'bb_std': bs,
                'vol_mult': vm,
                'entries': n_entries,
                'sharpe': metrics['sharpe'],
                'calmar': metrics['calmar'],
                'maxdd': metrics['maxdd'],
                'ann_ret': metrics['ann_ret'],
                'net_pnl': total_net,
            })

# Print sensitivity table
print(f"  {'BB_Per':>6s}  {'BB_Std':>6s}  {'Vol_M':>6s}  {'Entries':>8s}  {'Sharpe':>8s}  {'Calmar':>8s}  {'MaxDD':>8s}  {'Ann_Ret':>8s}  {'Net_PnL':>10s}")
print(f"  {'─'*6}  {'─'*6}  {'─'*6}  {'─'*8}  {'─'*8}  {'─'*8}  {'─'*8}  {'─'*8}  {'─'*10}")

baseline_calmar = None
degraded_params = []

for r in sensitivity_results:
    is_baseline = (r['bb_period'] == 20 and r['bb_std'] == 2.0 and r['vol_mult'] == 1.3)
    marker = " <-- BASELINE" if is_baseline else ""

    if is_baseline:
        baseline_calmar = r['calmar']

    print(f"  {r['bb_period']:>6d}  {r['bb_std']:>6.1f}  {r['vol_mult']:>6.1f}  "
          f"{r['entries']:>8d}  {r['sharpe']:>8.2f}  {r['calmar']:>8.2f}  "
          f"{r['maxdd']:>7.1%}  {r['ann_ret']:>7.1%}  ${r['net_pnl']:>+9,.0f}{marker}")

# Check degradation
print()
if baseline_calmar and baseline_calmar > 0:
    threshold = baseline_calmar * 0.7  # 30% degradation
    print(f"  Baseline Calmar: {baseline_calmar:.2f}")
    print(f"  30% degradation threshold: {threshold:.2f}")
    print()

    for r in sensitivity_results:
        is_baseline = (r['bb_period'] == 20 and r['bb_std'] == 2.0 and r['vol_mult'] == 1.3)
        if is_baseline:
            continue
        if r['calmar'] < threshold:
            degraded_params.append(r)
            print(f"  WARNING: BB({r['bb_period']}, {r['bb_std']:.1f}) vol_mult={r['vol_mult']:.1f} "
                  f"Calmar={r['calmar']:.2f} DEGRADED >30% from baseline")

    if not degraded_params:
        print("  PASS: No parameter combination degrades Calmar >30%")
    else:
        print(f"\n  FAIL: {len(degraded_params)} parameter combinations degrade Calmar >30%")
else:
    print("  WARNING: Baseline Calmar is 0 or negative — sensitivity check inconclusive")


# ═══════════════════════════════════════════════════════════════════════
# FINAL GATE 3 SUMMARY
# ═══════════════════════════════════════════════════════════════════════
print("\n\n")
print("=" * 80)
print(" GATE 3 FINAL SUMMARY — R160 BB VOLATILITY BREAKOUT")
print("=" * 80)
print()

# 1. Per-token results
print("  1. PER-TOKEN RESULTS:")
for token in TOKENS:
    r = per_token_results[token]
    if r['verdict'] in ('NO_DATA', 'INSUFFICIENT_DATA'):
        print(f"     {token:>6s}: {r['verdict']}")
    else:
        w = r['windows']['L12M']
        print(f"     {token:>6s}: {r['verdict']:>6s}  Sharpe={w['sharpe']:.2f}  "
              f"Calmar={w['calmar']:.2f}  MaxDD={w['maxdd']:.1%}  Trades={w.get('trades',0)}")

print(f"\n     Tokens PASSED: {pass_count}/{total_with_data}")

# 2. Portfolio
print(f"\n  2. PORTFOLIO (all tokens combined):")
print(f"     Verdict: {result_portfolio['verdict']}")
if result_portfolio['windows']:
    for wname in ['L12M', 'L6M', 'L3M']:
        w = result_portfolio['windows'][wname]
        print(f"     {wname}: Sharpe={w['sharpe']:.2f}  Calmar={w['calmar']:.2f}  "
              f"MaxDD={w['maxdd']:.1%}  Trades={w.get('trades',0)}")

# 3. Sensitivity
print(f"\n  3. PARAMETER SENSITIVITY (BTC):")
if baseline_calmar and baseline_calmar > 0:
    if not degraded_params:
        print(f"     PASS: All 27 parameter combos within 30% of baseline Calmar ({baseline_calmar:.2f})")
    else:
        print(f"     FAIL: {len(degraded_params)}/27 combos degrade >30%")
        for r in degraded_params:
            print(f"       BB({r['bb_period']},{r['bb_std']:.1f}) vol={r['vol_mult']:.1f}: "
                  f"Calmar={r['calmar']:.2f} vs baseline {baseline_calmar:.2f}")
else:
    print(f"     INCONCLUSIVE: baseline Calmar={baseline_calmar}")

# Overall gate verdict
print()
overall_gate = "PASS" if (pass_count >= total_with_data * 0.5 and
                          result_portfolio['verdict'] == 'PASS' and
                          not degraded_params) else "CONDITIONAL" if pass_count >= 3 else "FAIL"
print(f"  OVERALL GATE 3 VERDICT: {overall_gate}")
print("=" * 80)
