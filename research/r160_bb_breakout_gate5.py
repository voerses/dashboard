#!/usr/bin/env python3
"""
R160 BB Volatility Breakout — Gate 5: Full Universe Validation
===============================================================
Gate 5 tests the strategy across the FULL liquid token universe with:
  1. Full universe backtest (all tokens meeting ADV filter)
  2. L12M / L6M / L3M window verdicts
  3. Parameter sensitivity sweep (27 combos)

Strategy rules (4H bars):
  - Long entry: close > BB_upper(20,2) AND volume > 1.3x avg AND 7d_ret > 0
  - Short entry: close < BB_lower(20,2) AND volume > 1.3x avg AND 7d_ret < 0
  - Exit long: close < SMA(15) trailing stop OR 30d max hold
  - Exit short: close > SMA(15) trailing stop OR 30d max hold
  - 8-bar cooldown between trades per token
  - Signals mapped from 4H back to 1H (forward-fill within each 4H bar)

Kill criteria: Sharpe > 2, Calmar > 3, MaxDD > -25% in ALL windows (L12M, L6M, L3M).
"""
import sys
sys.path.insert(0, '/workspace/crypto_backtest')

import numpy as np
import pandas as pd
from tools.raw_backtest import Backtest

# ── R160 Parameters ───────────────────────────────────────────────
BB_PERIOD = 20
BB_STD = 2.0
VOL_LOOKBACK = 20
VOL_THRESHOLD = 1.3
MOM_BARS_4H = 42          # 7 days * 6 four-hour bars per day
TRAIL_SMA = 15             # SMA period for trailing stop (4H bars)
COOLDOWN_BARS = 8          # 8 four-hour bars cooldown between trades
MAX_HOLD_BARS = 180        # 30 days * 6 four-hour bars per day
SIZE_USD = 50_000

# ── Backtest parameters ──────────────────────────────────────────
BT_PARAMS = dict(
    capital=100_000,
    fee_bps=7,
    market='perp',
    leverage_max=1.0,
    start='2024-01-01',
    end='2026-03-17',
)


def resample_to_4h(df_1h):
    """Resample 1H data to 4H OHLCV bars."""
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    if "funding_1h" in df_1h.columns:
        agg["funding_1h"] = "sum"
    return df_1h.resample('4h').agg(agg).dropna(subset=['close'])


def generate_r160_signal_4h(df_4h, bb_period=BB_PERIOD, bb_std=BB_STD,
                             vol_lookback=VOL_LOOKBACK, vol_threshold=VOL_THRESHOLD,
                             trail_sma=TRAIL_SMA, cooldown_bars=COOLDOWN_BARS,
                             max_hold_bars=MAX_HOLD_BARS):
    """
    Generate R160 BB breakout signal on 4H data.

    Returns pd.Series indexed by 4H timestamps with values:
      +1 = long, -1 = short, 0 = flat
    """
    close = df_4h['close']
    volume = df_4h['volume']

    # Bollinger Bands
    sma = close.rolling(bb_period).mean()
    std_dev = close.rolling(bb_period).std()
    upper_bb = sma + bb_std * std_dev
    lower_bb = sma - bb_std * std_dev

    # Average volume
    vol_avg = volume.rolling(vol_lookback).mean()

    # 7-day momentum (42 four-hour bars)
    mom_7d = close / close.shift(MOM_BARS_4H) - 1.0

    # SMA trailing stop
    sma_trail = close.rolling(trail_sma).mean()

    # Vectorized entry conditions
    long_entry_cond = (
        (close > upper_bb) &
        (volume > vol_threshold * vol_avg) &
        (mom_7d > 0)
    )
    short_entry_cond = (
        (close < lower_bb) &
        (volume > vol_threshold * vol_avg) &
        (mom_7d < 0)
    )

    # State machine with cooldown and max hold
    signal = pd.Series(0, index=df_4h.index, dtype=int)
    position = 0       # 0=flat, 1=long, -1=short
    bars_since_exit = cooldown_bars + 1  # Allow entry on first bar
    bars_in_trade = 0
    warmup = max(bb_period, MOM_BARS_4H, trail_sma)

    for i in range(warmup, len(df_4h)):
        c = close.iloc[i]
        trail_val = sma_trail.iloc[i]

        if position == 1:  # In long
            bars_in_trade += 1
            # Exit long: close < SMA trail OR max hold exceeded
            if c < trail_val or bars_in_trade >= max_hold_bars:
                signal.iloc[i] = 0
                position = 0
                bars_since_exit = 0
                bars_in_trade = 0
            else:
                signal.iloc[i] = 1
        elif position == -1:  # In short
            bars_in_trade += 1
            # Exit short: close > SMA trail OR max hold exceeded
            if c > trail_val or bars_in_trade >= max_hold_bars:
                signal.iloc[i] = 0
                position = 0
                bars_since_exit = 0
                bars_in_trade = 0
            else:
                signal.iloc[i] = -1
        else:  # Flat
            bars_since_exit += 1
            if bars_since_exit > cooldown_bars:
                if long_entry_cond.iloc[i]:
                    signal.iloc[i] = 1
                    position = 1
                    bars_in_trade = 1
                elif short_entry_cond.iloc[i]:
                    signal.iloc[i] = -1
                    position = -1
                    bars_in_trade = 1
                else:
                    signal.iloc[i] = 0
            else:
                signal.iloc[i] = 0

    return signal


def map_4h_signal_to_1h(signal_4h, df_1h):
    """
    Map 4H signal series back to 1H index by forward-filling.
    Each 4H signal value is applied to the 4 hourly bars within that 4H window.
    """
    # Reindex to 1H, forward-fill (each 4H signal persists for 4 hourly bars)
    signal_1h = signal_4h.reindex(df_1h.index, method='ffill')
    # Fill any leading NaNs with 0
    signal_1h = signal_1h.fillna(0).astype(int)
    return signal_1h


# ═══════════════════════════════════════════════════════════════════════
# PART 1: FULL UNIVERSE BACKTEST
# ═══════════════════════════════════════════════════════════════════════
print("=" * 80)
print(" R160 BB BREAKOUT — GATE 5: FULL UNIVERSE VALIDATION")
print("=" * 80)
print()

bt = Backtest(**BT_PARAMS)

# Load all liquid tokens
print("Loading full token universe (min_adv=$2M)...")
all_token_data = bt.load_tokens(min_adv=2_000_000)
print(f"  Loaded {len(all_token_data)} tokens meeting ADV filter")
print(f"  Tokens: {', '.join(sorted(all_token_data.keys()))}")
print()

# Re-create backtest for clean state (load_tokens consumed bars)
bt = Backtest(**BT_PARAMS)

# Generate signals for each token and feed through the backtest
token_signal_stats = {}
tokens_processed = 0
tokens_skipped = 0

for token, df_1h in sorted(all_token_data.items()):
    if len(df_1h) < 200:
        print(f"  SKIP {token}: insufficient data ({len(df_1h)} bars)")
        tokens_skipped += 1
        continue

    df_4h = resample_to_4h(df_1h)
    if len(df_4h) < max(BB_PERIOD, MOM_BARS_4H, TRAIL_SMA) + 10:
        print(f"  SKIP {token}: insufficient 4H bars ({len(df_4h)})")
        tokens_skipped += 1
        continue

    # Generate 4H signal
    signal_4h = generate_r160_signal_4h(df_4h)

    # Map to 1H for from_signals
    signal_1h = map_4h_signal_to_1h(signal_4h, df_1h)

    # Count entries
    n_long_entries = ((signal_1h == 1) & (signal_1h.shift(1) != 1)).sum()
    n_short_entries = ((signal_1h == -1) & (signal_1h.shift(1) != -1)).sum()
    n_total = n_long_entries + n_short_entries

    token_signal_stats[token] = {
        'long_entries': n_long_entries,
        'short_entries': n_short_entries,
        'total_entries': n_total,
        'bars_1h': len(df_1h),
    }

    if n_total > 0:
        print(f"  {token:>8s}: {n_long_entries:>3d}L + {n_short_entries:>3d}S = {n_total:>3d} entries  "
              f"({len(df_1h)} 1H bars)")
    else:
        print(f"  {token:>8s}: no entries (data OK, no signals)")

    # Feed to backtest
    bt.from_signals(
        token=token,
        signals=signal_1h,
        size_usd=SIZE_USD,
        leverage=1.0,
        timeframe='1h',
    )
    tokens_processed += 1

print(f"\n  Processed: {tokens_processed} tokens  |  Skipped: {tokens_skipped}")
total_entries = sum(s['total_entries'] for s in token_signal_stats.values())
total_longs = sum(s['long_entries'] for s in token_signal_stats.values())
total_shorts = sum(s['short_entries'] for s in token_signal_stats.values())
print(f"  Total signal entries: {total_entries} ({total_longs}L + {total_shorts}S)")
print()

# ── Run report ──
print("=" * 80)
print(" FULL UNIVERSE REPORT")
print("=" * 80)
result = bt.report('R160 BB Breakout - Gate 5 Full Universe')

# ═══════════════════════════════════════════════════════════════════════
# PART 2: DETAILED METRICS TABLE
# ═══════════════════════════════════════════════════════════════════════
print("\n\n")
print("=" * 80)
print(" GATE 5 VERDICT ANALYSIS")
print("=" * 80)
print()

verdict = result['verdict']
kill_reasons = result['kill_reasons']
windows = result['windows']

print(f"  VERDICT: {verdict}")
print()

if kill_reasons:
    print("  Kill Reasons:")
    for r in kill_reasons:
        print(f"    - {r}")
    print()

print(f"  {'Metric':>12s}  {'L12M':>10s}  {'L6M':>10s}  {'L3M':>10s}  {'Threshold':>12s}  {'Status':>8s}")
print(f"  {'─'*12}  {'─'*10}  {'─'*10}  {'─'*10}  {'─'*12}  {'─'*8}")

metrics_to_check = [
    ("Sharpe",    "sharpe",  2.0,  "gt"),
    ("Calmar",    "calmar",  3.0,  "gt"),
    ("MaxDD",     "maxdd",  -0.25, "gt"),
    ("Ann. Ret",  "ann_ret", 0.0,  "gt"),
    ("Win Rate",  "win_rate", None, None),
    ("Trades",    "trades",  None, None),
    ("PF",        "profit_factor", None, None),
]

for label, key, threshold, direction in metrics_to_check:
    vals = []
    statuses = []
    for wname in ["L12M", "L6M", "L3M"]:
        v = windows[wname][key]
        if key == "maxdd":
            vals.append(f"{v:>+9.1%}")
        elif key in ("ann_ret", "win_rate"):
            vals.append(f"{v:>+9.1%}")
        elif key == "trades":
            vals.append(f"{v:>10d}")
        elif key == "profit_factor":
            vals.append(f"{v:>10.2f}" if v != float("inf") else f"{'inf':>10s}")
        else:
            vals.append(f"{v:>10.2f}")

        if threshold is not None:
            if direction == "gt":
                statuses.append("OK" if v > threshold else "FAIL")
            else:
                statuses.append("OK" if v < threshold else "FAIL")
        else:
            statuses.append("")

    thresh_str = ""
    if threshold is not None:
        if key == "maxdd":
            thresh_str = f"> {threshold:.0%}"
        elif key in ("ann_ret",):
            thresh_str = f"> {threshold:.0%}"
        else:
            thresh_str = f"> {threshold:.1f}"

    worst_status = "FAIL" if "FAIL" in statuses else ("OK" if any(s == "OK" for s in statuses) else "")
    print(f"  {label:>12s}  {vals[0]}  {vals[1]}  {vals[2]}  {thresh_str:>12s}  {worst_status:>8s}")


# ═══════════════════════════════════════════════════════════════════════
# PART 3: PARAMETER SENSITIVITY SWEEP (27 combos)
# ═══════════════════════════════════════════════════════════════════════
print("\n\n")
print("=" * 80)
print(" PARAMETER SENSITIVITY SWEEP (27 combos)")
print("=" * 80)
print()
print("  Grid: BB_STD x [1.5, 2.0, 2.5]  |  VOL_THRESHOLD x [1.1, 1.3, 1.5]  |  TRAIL_SMA x [10, 15, 20]")
print("  Using full universe (4H timeframe for speed). Checking how many combos pass verdict thresholds.")
print()

bb_stds_sweep = [1.5, 2.0, 2.5]
vol_thresholds_sweep = [1.1, 1.3, 1.5]
trail_smas_sweep = [10, 15, 20]

sensitivity_results = []
combo_count = 0
pass_count_sens = 0

print(f"  {'#':>3s}  {'BB_Std':>6s}  {'Vol_Th':>6s}  {'Trail':>5s}  {'Trades':>7s}  "
      f"{'L12M_Sh':>8s}  {'L6M_Sh':>8s}  {'L3M_Sh':>8s}  "
      f"{'L12M_Cal':>9s}  {'L12M_DD':>9s}  {'Verdict':>8s}")
print(f"  {'─'*3}  {'─'*6}  {'─'*6}  {'─'*5}  {'─'*7}  "
      f"{'─'*8}  {'─'*8}  {'─'*8}  "
      f"{'─'*9}  {'─'*9}  {'─'*8}")

for bb_s in bb_stds_sweep:
    for vol_t in vol_thresholds_sweep:
        for trail_s in trail_smas_sweep:
            combo_count += 1

            # Create fresh backtest
            bt_sens = Backtest(**BT_PARAMS)

            # Process each token (use 4H timeframe for speed — signals are 4H native)
            for token, df_1h in sorted(all_token_data.items()):
                if len(df_1h) < 200:
                    continue
                df_4h = resample_to_4h(df_1h)
                if len(df_4h) < max(BB_PERIOD, MOM_BARS_4H, trail_s) + 10:
                    continue

                signal_4h = generate_r160_signal_4h(
                    df_4h,
                    bb_std=bb_s,
                    vol_threshold=vol_t,
                    trail_sma=trail_s,
                )

                bt_sens.from_signals(
                    token=token,
                    signals=signal_4h,
                    size_usd=SIZE_USD,
                    leverage=1.0,
                    timeframe='4h',
                )

            # Compute metrics without full report
            eq = pd.Series(bt_sens.mtm_equity_curve)
            if len(eq) < 10:
                row = {
                    'bb_std': bb_s, 'vol_threshold': vol_t, 'trail_sma': trail_s,
                    'trades': 0, 'verdict': 'NO_DATA',
                    'l12m_sharpe': 0, 'l6m_sharpe': 0, 'l3m_sharpe': 0,
                    'l12m_calmar': 0, 'l12m_maxdd': 0,
                    'kill_reasons': ['No equity curve data'],
                }
                sensitivity_results.append(row)
                print(f"  {combo_count:>3d}  {bb_s:>6.1f}  {vol_t:>6.1f}  {trail_s:>5d}  "
                      f"{'N/A':>7s}  {'N/A':>8s}  {'N/A':>8s}  {'N/A':>8s}  "
                      f"{'N/A':>9s}  {'N/A':>9s}  {'NO_DATA':>8s}")
                continue

            daily_eq = eq.resample("1D").last().dropna().ffill()
            daily_returns = daily_eq.pct_change().dropna()

            # Compute windowed metrics
            end_ts = pd.Timestamp(BT_PARAMS['end'])
            start_ts = pd.Timestamp(BT_PARAMS['start'])
            combo_kill = []
            w_metrics = {}

            for wname, wdays in [("L12M", 365), ("L6M", 182), ("L3M", 91)]:
                wstart = end_ts - pd.Timedelta(days=wdays)
                if wstart < start_ts:
                    wstart = start_ts
                dr = daily_returns.loc[wstart:end_ts]
                m = bt_sens._compute_metrics(dr)
                w_metrics[wname] = m

                if m['sharpe'] < 2.0:
                    combo_kill.append(f"{wname} Sharpe {m['sharpe']:.2f} < 2.0")
                if m['maxdd'] < -0.25:
                    combo_kill.append(f"{wname} MaxDD {m['maxdd']:.1%} < -25%")
                if m['calmar'] < 3.0:
                    combo_kill.append(f"{wname} Calmar {m['calmar']:.2f} < 3.0")
                if m['ann_ret'] < 0:
                    combo_kill.append(f"{wname} negative return")

            combo_verdict = "KILL" if combo_kill else "PASS"
            if combo_verdict == "PASS":
                pass_count_sens += 1

            n_trades = len(bt_sens.trades)
            is_baseline = (bb_s == 2.0 and vol_t == 1.3 and trail_s == 15)
            marker = " <-- BASE" if is_baseline else ""

            row = {
                'bb_std': bb_s, 'vol_threshold': vol_t, 'trail_sma': trail_s,
                'trades': n_trades, 'verdict': combo_verdict,
                'l12m_sharpe': w_metrics.get('L12M', {}).get('sharpe', 0),
                'l6m_sharpe': w_metrics.get('L6M', {}).get('sharpe', 0),
                'l3m_sharpe': w_metrics.get('L3M', {}).get('sharpe', 0),
                'l12m_calmar': w_metrics.get('L12M', {}).get('calmar', 0),
                'l12m_maxdd': w_metrics.get('L12M', {}).get('maxdd', 0),
                'kill_reasons': combo_kill,
            }
            sensitivity_results.append(row)

            print(f"  {combo_count:>3d}  {bb_s:>6.1f}  {vol_t:>6.1f}  {trail_s:>5d}  "
                  f"{n_trades:>7d}  "
                  f"{row['l12m_sharpe']:>8.2f}  {row['l6m_sharpe']:>8.2f}  {row['l3m_sharpe']:>8.2f}  "
                  f"{row['l12m_calmar']:>9.2f}  {row['l12m_maxdd']:>+8.1%}  "
                  f"{combo_verdict:>8s}{marker}")


# ═══════════════════════════════════════════════════════════════════════
# FINAL GATE 5 SUMMARY
# ═══════════════════════════════════════════════════════════════════════
print("\n\n")
print("=" * 80)
print(" GATE 5 FINAL SUMMARY — R160 BB VOLATILITY BREAKOUT")
print("=" * 80)
print()

# 1. Full universe verdict
print("  1. FULL UNIVERSE BACKTEST:")
print(f"     Verdict:    {verdict}")
print(f"     Tokens:     {tokens_processed} processed ({tokens_skipped} skipped)")
print(f"     Trades:     {result['trades']}")
for wname in ['L12M', 'L6M', 'L3M']:
    w = windows[wname]
    print(f"     {wname}:  Sharpe={w['sharpe']:.2f}  Calmar={w['calmar']:.2f}  "
          f"MaxDD={w['maxdd']:.1%}  Trades={w.get('trades',0)}  "
          f"WinRate={w.get('win_rate',0):.1%}  AnnRet={w['ann_ret']:.1%}")

if kill_reasons:
    print(f"\n     KILL reasons:")
    for r in kill_reasons:
        print(f"       - {r}")

# 2. Sensitivity
print(f"\n  2. PARAMETER SENSITIVITY ({combo_count} combos):")
print(f"     PASS: {pass_count_sens}/{combo_count} combos pass all verdict thresholds")
print(f"     (Sharpe>2, Calmar>3, MaxDD>-25% in ALL windows)")

# Show which combos passed
passing_combos = [r for r in sensitivity_results if r['verdict'] == 'PASS']
if passing_combos:
    print(f"\n     Passing combos:")
    for r in passing_combos:
        print(f"       BB_std={r['bb_std']:.1f}  Vol_th={r['vol_threshold']:.1f}  "
              f"Trail={r['trail_sma']}  |  L12M Sharpe={r['l12m_sharpe']:.2f}  "
              f"Calmar={r['l12m_calmar']:.2f}")

# Show top-5 failing combos by closest to passing
failing_combos = [r for r in sensitivity_results if r['verdict'] == 'KILL']
if failing_combos:
    # Sort by L12M Sharpe descending (closest to passing)
    failing_combos.sort(key=lambda x: x['l12m_sharpe'], reverse=True)
    print(f"\n     Top-5 closest-to-passing KILL combos:")
    for r in failing_combos[:5]:
        print(f"       BB_std={r['bb_std']:.1f}  Vol_th={r['vol_threshold']:.1f}  "
              f"Trail={r['trail_sma']}  |  L12M Sharpe={r['l12m_sharpe']:.2f}  "
              f"Calmar={r['l12m_calmar']:.2f}  MaxDD={r['l12m_maxdd']:.1%}")
        if r['kill_reasons']:
            for kr in r['kill_reasons'][:2]:
                print(f"         - {kr}")

# 3. Overall verdict
print()
print("=" * 80)
overall = "PASS" if verdict == "PASS" else "KILL"
print(f"  GATE 5 OVERALL: {overall}")
if overall == "KILL":
    print(f"  Strategy does NOT meet kill criteria in all windows.")
    if kill_reasons:
        print(f"  Primary reasons:")
        for r in kill_reasons:
            print(f"    - {r}")
else:
    print(f"  Strategy PASSES all thresholds in all windows.")
    print(f"  Sensitivity: {pass_count_sens}/{combo_count} param combos also pass.")
print("=" * 80)
