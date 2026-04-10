"""
Regime Reversal Detector — State Machine based on Fibonacci Level Reversals

Core idea: Maintain a BULL/BEAR state, only transition on confirmed reversals
near Fibonacci extension/retracement levels. Gives stable, bold regime calls.
"""

import pandas as pd
import numpy as np
from pathlib import Path

pd.set_option('display.max_columns', 30)
pd.set_option('display.width', 200)
pd.set_option('display.float_format', '{:.2f}'.format)

# ── Data Loading ──────────────────────────────────────────────────────────────

DATA_DIR = Path('/workspace/crypto_backtest/data')

print("=" * 80)
print("REGIME REVERSAL DETECTOR — Fibonacci State Machine")
print("=" * 80)

# BTC 1h -> daily
btc_1h = pd.read_csv(DATA_DIR / 'perp/binance/1h_ohlcv/BTC_perp_1h.csv', parse_dates=['datetime'])
btc_1h = btc_1h.set_index('datetime')
btc_daily = btc_1h.resample('1D').agg({
    'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'
}).dropna()
btc_daily.index = btc_daily.index.tz_localize('UTC') if btc_daily.index.tz is None else btc_daily.index

# Regime signals
regime_sig = pd.read_parquet(DATA_DIR / 'alternative/regime_signals.parquet')
# Align indexes
regime_sig.index = regime_sig.index.tz_convert('UTC') if regime_sig.index.tz is not None else regime_sig.index.tz_localize('UTC')

# Merge
df = btc_daily[['close', 'high', 'low']].join(regime_sig[['alt_breadth_50d']], how='left')
df['alt_breadth_50d'] = df['alt_breadth_50d'].ffill()
prices = df['close'].values
dates = df.index

print(f"\nData range: {dates[0].date()} to {dates[-1].date()} ({len(df)} days)")
print(f"Alt breadth coverage: {df['alt_breadth_50d'].notna().sum()} / {len(df)} days")

# ── STEP 1: Identify Major Cycle Pivots ──────────────────────────────────────

print("\n" + "=" * 80)
print("STEP 1: MAJOR CYCLE PIVOTS (20% drop confirms peak, 30% rise confirms trough)")
print("=" * 80)

state = 'seeking_peak'
pivots = []
running_high = prices[0]
running_high_idx = 0
running_low = prices[0]
running_low_idx = 0

for i in range(len(prices)):
    price = prices[i]
    if state == 'seeking_peak':
        if price > running_high:
            running_high = price
            running_high_idx = i
        if price < running_high * 0.80:  # 20% drop = peak confirmed
            pivots.append(('PEAK', running_high_idx, running_high))
            state = 'seeking_trough'
            running_low = price
            running_low_idx = i
    elif state == 'seeking_trough':
        if price < running_low:
            running_low = price
            running_low_idx = i
        if price > running_low * 1.30:  # 30% rise = trough confirmed
            pivots.append(('TROUGH', running_low_idx, running_low))
            state = 'seeking_peak'
            running_high = price
            running_high_idx = i

# If still seeking, add the unconfirmed running extreme
if state == 'seeking_trough':
    pivots.append(('TROUGH (unconfirmed)', running_low_idx, running_low))
elif state == 'seeking_peak':
    pivots.append(('PEAK (unconfirmed)', running_high_idx, running_high))

print(f"\nDetected {len(pivots)} pivots:\n")
print(f"{'Type':<25} {'Date':<15} {'Price':>12} {'Confirmed At':>15}")
print("-" * 70)
for ptype, idx, price in pivots:
    # Confirmation date: when the threshold was crossed (next pivot or end)
    print(f"{ptype:<25} {str(dates[idx].date()):<15} ${price:>11,.0f}")

# ── STEP 2: Compute Fibonacci Levels ─────────────────────────────────────────

print("\n" + "=" * 80)
print("STEP 2: FIBONACCI LEVELS FROM PIVOTS")
print("=" * 80)

fib_levels = []  # list of dicts with level info

for i in range(len(pivots) - 1):
    p1_type, p1_idx, p1_price = pivots[i]
    p2_type, p2_idx, p2_price = pivots[i + 1]

    if 'TROUGH' in p1_type and 'PEAK' in p2_type:
        # Trough -> Peak: compute extensions (resistance for bull->bear)
        rng = p2_price - p1_price
        print(f"\n--- TROUGH ({dates[p1_idx].date()}, ${p1_price:,.0f}) -> PEAK ({dates[p2_idx].date()}, ${p2_price:,.0f}) ---")
        print(f"    Range: ${rng:,.0f}")
        print(f"    Extensions from trough (RESISTANCE levels):")
        for ext in [1.0, 1.272, 1.618, 2.0, 2.618, 3.618]:
            level = p1_price + ext * rng
            print(f"      {ext:.3f} extension: ${level:,.0f}")
            fib_levels.append({
                'type': 'extension', 'from_date': dates[p1_idx], 'to_date': dates[p2_idx],
                'ratio': ext, 'level': level, 'trough': p1_price, 'peak': p2_price
            })

    elif 'PEAK' in p1_type and 'TROUGH' in p2_type:
        # Peak -> Trough: compute retracements (support for bear->bull)
        rng = p1_price - p2_price
        print(f"\n--- PEAK ({dates[p1_idx].date()}, ${p1_price:,.0f}) -> TROUGH ({dates[p2_idx].date()}, ${p2_price:,.0f}) ---")
        print(f"    Range: ${rng:,.0f}")
        print(f"    Retracements from peak (SUPPORT levels):")
        for ret in [0.382, 0.500, 0.618, 0.786, 1.0]:
            level = p1_price - ret * rng
            print(f"      {ret:.3f} retracement: ${level:,.0f}")
            fib_levels.append({
                'type': 'retracement', 'from_date': dates[p1_idx], 'to_date': dates[p2_idx],
                'ratio': ret, 'level': level, 'peak': p1_price, 'trough': p2_price
            })

# ── Special check: 1.618 extension prediction for 2025 top ──

print("\n" + "-" * 60)
print("SPECIAL CHECK: Does 1.618 extension predict the 2025 top?")
print("-" * 60)

# Find the most relevant trough->peak for this
for i in range(len(pivots) - 1):
    p1_type, p1_idx, p1_price = pivots[i]
    p2_type, p2_idx, p2_price = pivots[i + 1]
    if 'TROUGH' in p1_type and dates[p1_idx].year >= 2022:
        rng = p2_price - p1_price if 'PEAK' in p2_type else prices.max() - p1_price
        ext_1618 = p1_price + 1.618 * rng
        # Also compute from the trough with ATH as peak proxy
        ath_idx = np.argmax(prices)
        ath_price = prices[ath_idx]
        print(f"  Trough: {dates[p1_idx].date()} @ ${p1_price:,.0f}")
        print(f"  ATH: {dates[ath_idx].date()} @ ${ath_price:,.0f}")
        # Compute extension from the PREVIOUS major cycle
        break

# Find 2022 low and previous ATH for the classic calculation
for pt, pi, pp in pivots:
    if 'TROUGH' in pt and dates[pi].year >= 2022:
        trough_2022_price = pp
        trough_2022_idx = pi
        break

# Find the peak before 2022 trough
for pt, pi, pp in reversed(pivots):
    if 'PEAK' in pt and dates[pi] < dates[trough_2022_idx]:
        peak_pre_2022_price = pp
        peak_pre_2022_idx = pi
        break

rng_classic = peak_pre_2022_price - trough_2022_price
ext_1618_classic = trough_2022_price + 1.618 * rng_classic
actual_peak_2025 = df.loc[df.index >= '2025-01-01', 'close'].max() if df.index[-1].year >= 2025 else prices.max()
actual_peak_all = prices.max()
actual_peak_date = dates[np.argmax(prices)]

print(f"\n  Classic Fibonacci calculation:")
print(f"    Previous peak: {dates[peak_pre_2022_idx].date()} @ ${peak_pre_2022_price:,.0f}")
print(f"    2022 trough:   {dates[trough_2022_idx].date()} @ ${trough_2022_price:,.0f}")
print(f"    Range:         ${rng_classic:,.0f}")
print(f"    1.618 extension from trough: ${ext_1618_classic:,.0f}")
print(f"    Actual ATH:    {actual_peak_date.date()} @ ${actual_peak_all:,.0f}")
print(f"    Deviation:     {(actual_peak_all / ext_1618_classic - 1) * 100:+.1f}%")

# ── STEP 3: Detect Reversals ─────────────────────────────────────────────────

print("\n" + "=" * 80)
print("STEP 3: DETECT REVERSALS (CAUSAL)")
print("=" * 80)


def compute_rsi(series, period=30):
    """Compute RSI."""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = (-delta.clip(upper=0))
    avg_gain = gain.rolling(period, min_periods=period).mean()
    avg_loss = loss.rolling(period, min_periods=period).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi


df['rsi_30'] = compute_rsi(df['close'], 30)
df['ath'] = df['close'].expanding().max()
df['ath_dd'] = df['close'] / df['ath'] - 1

# Track distance from last confirmed trough/peak
# We'll use the pivot list to compute these features causally


def detect_reversals(df, pivots, dates, prices,
                     ath_dd_trigger=-0.15,
                     rally_threshold=1.0,
                     drop_threshold=0.30,
                     alt_breadth_top_trigger=0.30,
                     alt_breadth_bottom_trigger=0.50,
                     verbose=True):
    """
    Detect bull->bear and bear->bull reversals.
    Returns regime array and list of transition events.
    """
    n = len(df)
    regime = np.full(n, 'BULL', dtype='U4')
    current_regime = 'BULL'
    transitions = []

    # Build causal pivot knowledge: at each day, what's the last confirmed pivot?
    last_trough_price = np.nan
    last_trough_idx = 0
    last_peak_price = np.nan
    last_peak_idx = 0

    # Pre-compute: for each day, the last confirmed peak and trough
    # A pivot is confirmed when the threshold is crossed, not at the pivot date itself
    # So we need to find WHEN each pivot was confirmed
    confirmed_pivots = []
    for j, (ptype, pidx, pprice) in enumerate(pivots):
        if 'unconfirmed' in ptype:
            continue
        # Find confirmation date: first day after pidx where the threshold was crossed
        if 'PEAK' in ptype:
            # Peak confirmed when price drops 20% from it
            for k in range(pidx, n):
                if prices[k] < pprice * 0.80:
                    confirmed_pivots.append(('PEAK', pidx, pprice, k))
                    break
        elif 'TROUGH' in ptype:
            # Trough confirmed when price rises 30% from it
            for k in range(pidx, n):
                if prices[k] > pprice * 1.30:
                    confirmed_pivots.append(('TROUGH', pidx, pprice, k))
                    break

    if verbose:
        print(f"\nConfirmed pivots (with confirmation dates):")
        print(f"{'Type':<10} {'Pivot Date':<15} {'Price':>12} {'Confirmed':>15}")
        print("-" * 55)
        for ptype, pidx, pprice, cidx in confirmed_pivots:
            print(f"{ptype:<10} {str(dates[pidx].date()):<15} ${pprice:>11,.0f} {str(dates[cidx].date()):>15}")

    # Build per-day knowledge of last confirmed peak/trough
    last_conf_peak_price = np.full(n, np.nan)
    last_conf_peak_idx = np.full(n, -1, dtype=int)
    last_conf_trough_price = np.full(n, np.nan)
    last_conf_trough_idx = np.full(n, -1, dtype=int)

    cur_peak_p, cur_peak_i = np.nan, -1
    cur_trough_p, cur_trough_i = np.nan, -1

    # Sort confirmed pivots by confirmation date
    conf_sorted = sorted(confirmed_pivots, key=lambda x: x[3])
    conf_ptr = 0

    for i in range(n):
        while conf_ptr < len(conf_sorted) and conf_sorted[conf_ptr][3] <= i:
            cp = conf_sorted[conf_ptr]
            if cp[0] == 'PEAK':
                cur_peak_p = cp[2]
                cur_peak_i = cp[1]
            else:
                cur_trough_p = cp[2]
                cur_trough_i = cp[1]
            conf_ptr += 1
        last_conf_peak_price[i] = cur_peak_p
        last_conf_peak_idx[i] = cur_peak_i
        last_conf_trough_price[i] = cur_trough_p
        last_conf_trough_idx[i] = cur_trough_i

    # Compute Fibonacci retracement levels dynamically
    # For bull->bear: 0.382 retracement of rally from last trough
    # For bear->bull: 0.618 retracement from last peak

    alt_breadth = df['alt_breadth_50d'].values
    rsi = df['rsi_30'].values
    ath_dd = df['ath_dd'].values
    close = prices

    for i in range(1, n):
        if current_regime == 'BULL':
            # Bull -> Bear detection
            trough_p = last_conf_trough_price[i]
            if np.isnan(trough_p):
                regime[i] = current_regime
                continue

            rally_from_trough = close[i] / trough_p - 1 if trough_p > 0 else 0
            max_since_trough_idx = last_conf_trough_idx[i]
            if max_since_trough_idx >= 0:
                max_since_trough = np.max(close[max_since_trough_idx:i+1])
                retracement_382 = max_since_trough - 0.382 * (max_since_trough - trough_p)
            else:
                retracement_382 = 0

            # Check conditions
            extended_move = rally_from_trough > rally_threshold  # rallied >100% from trough
            ath_pullback = ath_dd[i] < ath_dd_trigger
            below_382 = close[i] < retracement_382
            alt_weak = (not np.isnan(alt_breadth[i])) and alt_breadth[i] < alt_breadth_top_trigger

            if extended_move and ath_pullback and alt_weak:
                current_regime = 'BEAR'
                transitions.append({
                    'date': dates[i], 'from': 'BULL', 'to': 'BEAR',
                    'trigger': f'ATH_DD={ath_dd[i]:.1%}, rally={rally_from_trough:.0%}, alt_b={alt_breadth[i]:.2f}',
                    'close': close[i]
                })
            elif extended_move and below_382 and alt_weak:
                current_regime = 'BEAR'
                transitions.append({
                    'date': dates[i], 'from': 'BULL', 'to': 'BEAR',
                    'trigger': f'Below 0.382 retrace=${retracement_382:,.0f}, alt_b={alt_breadth[i]:.2f}',
                    'close': close[i]
                })

        elif current_regime == 'BEAR':
            # Bear -> Bull detection
            peak_p = last_conf_peak_price[i]
            trough_p = last_conf_trough_price[i]
            if np.isnan(peak_p):
                regime[i] = current_regime
                continue

            drop_from_peak = 1 - close[i] / peak_p if peak_p > 0 else 0

            # 0.618 retracement level
            if not np.isnan(trough_p) and peak_p > trough_p:
                retrace_618 = peak_p - 0.618 * (peak_p - trough_p)
            else:
                retrace_618 = peak_p * 0.382  # fallback

            # Check conditions
            significant_drop = drop_from_peak > drop_threshold
            near_618 = close[i] <= retrace_618 * 1.05  # within 5% of 0.618

            rsi_cross_above_50 = (not np.isnan(rsi[i])) and (not np.isnan(rsi[i-1])) and rsi[i] > 50 and rsi[i-1] <= 50
            alt_recovering = (not np.isnan(alt_breadth[i])) and (not np.isnan(alt_breadth[i-1])) and \
                             alt_breadth[i] > alt_breadth_bottom_trigger and alt_breadth[i-1] <= alt_breadth_bottom_trigger

            momentum_turn = rsi_cross_above_50 or alt_recovering

            if significant_drop and momentum_turn:
                current_regime = 'BULL'
                trig_parts = []
                if rsi_cross_above_50:
                    trig_parts.append(f'RSI30={rsi[i]:.1f}')
                if alt_recovering:
                    trig_parts.append(f'alt_b={alt_breadth[i]:.2f}')
                transitions.append({
                    'date': dates[i], 'from': 'BEAR', 'to': 'BULL',
                    'trigger': f'drop={drop_from_peak:.0%}, {", ".join(trig_parts)}',
                    'close': close[i]
                })

        regime[i] = current_regime

    return regime, transitions


regime, transitions = detect_reversals(
    df, pivots, dates, prices,
    ath_dd_trigger=-0.15, rally_threshold=1.0,
    drop_threshold=0.30, alt_breadth_top_trigger=0.30,
    alt_breadth_bottom_trigger=0.50
)

print(f"\n\nDetected {len(transitions)} regime transitions:")
print(f"{'Date':<15} {'From':<6} {'To':<6} {'Price':>12} {'Trigger'}")
print("-" * 80)
for t in transitions:
    print(f"{str(t['date'].date()):<15} {t['from']:<6} {t['to']:<6} ${t['close']:>11,.0f} {t['trigger']}")

# ── STEP 4: State Machine Output ─────────────────────────────────────────────

df['regime'] = regime

# ── STEP 5: Validation ───────────────────────────────────────────────────────

print("\n" + "=" * 80)
print("STEP 5: VALIDATION")
print("=" * 80)

# Monthly regime timeline
print("\n--- Monthly Regime Timeline ---")
print("(U=BULL, B=BEAR)\n")

df['year'] = df.index.year
df['month'] = df.index.month

for year in sorted(df['year'].unique()):
    year_data = df[df['year'] == year]
    months = []
    for m in range(1, 13):
        month_data = year_data[year_data['month'] == m]
        if len(month_data) == 0:
            months.append(' ')
        else:
            # Majority regime for the month
            bull_days = (month_data['regime'] == 'BULL').sum()
            bear_days = (month_data['regime'] == 'BEAR').sum()
            months.append('U' if bull_days > bear_days else 'B')
    print(f"  {year}: {''.join(months)}  (J F M A M J J A S O N D)")

# Transitions per year
print("\n--- Transitions Per Year ---")
for year in sorted(df['year'].unique()):
    year_trans = [t for t in transitions if t['date'].year == year]
    print(f"  {year}: {len(year_trans)} transitions")
    for t in year_trans:
        print(f"    {t['date'].date()}: {t['from']} -> {t['to']}")

# Ideal classification comparison
print("\n--- Comparison Against Ideal ---")
ideal = {
    2022: 'BEAR',
    2023: 'BULL from ~Q2',
    2024: 'BULL',
    2025: 'BEAR by Q1-Q2',
    2026: 'BEAR'
}

for year, expected in ideal.items():
    year_data = df[df['year'] == year]
    if len(year_data) == 0:
        print(f"  {year}: No data")
        continue
    bull_pct = (year_data['regime'] == 'BULL').mean() * 100
    bear_pct = 100 - bull_pct
    year_trans = [t for t in transitions if t['date'].year == year]
    trans_str = ", ".join([f"{t['date'].date()} {t['from']}->{t['to']}" for t in year_trans]) if year_trans else "none"
    print(f"  {year}: BULL {bull_pct:.0f}% / BEAR {bear_pct:.0f}% | Expected: {expected} | Transitions: {trans_str}")

# Fibonacci level accuracy
print("\n--- Fibonacci Level Accuracy at Reversals ---")
for t in transitions:
    t_date = t['date']
    t_price = t['close']
    print(f"\n  {t_date.date()} {t['from']}->{t['to']} @ ${t_price:,.0f}")

    # Find relevant fib levels
    relevant = [f for f in fib_levels if f.get('from_date') is not None and pd.Timestamp(f['from_date']) < t_date]
    if relevant:
        closest = min(relevant, key=lambda f: abs(f['level'] - t_price))
        pct_off = (t_price / closest['level'] - 1) * 100
        print(f"    Closest Fibonacci level: ${closest['level']:,.0f} ({closest['type']} {closest['ratio']:.3f})")
        print(f"    Distance: {pct_off:+.1f}%")

        # Show all nearby levels
        nearby = sorted(relevant, key=lambda f: abs(f['level'] - t_price))[:5]
        for f in nearby:
            dist = (t_price / f['level'] - 1) * 100
            print(f"      {f['type']} {f['ratio']:.3f}: ${f['level']:,.0f} ({dist:+.1f}%)")

# ── STEP 6: Sensitivity Analysis ─────────────────────────────────────────────

print("\n" + "=" * 80)
print("STEP 6: SENSITIVITY ANALYSIS")
print("=" * 80)

# Ideal monthly classification for scoring
ideal_monthly = {}
# 2022: all BEAR
for m in range(1, 13):
    ideal_monthly[(2022, m)] = 'BEAR'
# 2023: BEAR Jan-Apr, BULL May-Dec
for m in range(1, 5):
    ideal_monthly[(2023, m)] = 'BEAR'
for m in range(5, 13):
    ideal_monthly[(2023, m)] = 'BULL'
# 2024: all BULL
for m in range(1, 13):
    ideal_monthly[(2024, m)] = 'BULL'
# 2025: BULL Jan, BEAR Feb-Dec
for m in range(1, 2):
    ideal_monthly[(2025, m)] = 'BULL'
for m in range(2, 13):
    ideal_monthly[(2025, m)] = 'BEAR'
# 2026: all BEAR (what we have)
for m in range(1, 13):
    ideal_monthly[(2026, m)] = 'BEAR'

ath_dd_values = [-0.10, -0.15, -0.20, -0.25]
rally_values = [0.20, 0.30, 0.40]  # bear->bull minimum drop for reversal is separate
alt_top_values = [0.20, 0.30, 0.40]

print(f"\nTesting {len(ath_dd_values) * len(rally_values) * len(alt_top_values)} parameter combinations...\n")

results = []

for ath_dd_trig in ath_dd_values:
    for rally_thr in rally_values:
        for alt_top in alt_top_values:
            r, trans = detect_reversals(
                df, pivots, dates, prices,
                ath_dd_trigger=ath_dd_trig,
                rally_threshold=1.0,  # keep rally from trough threshold at 100%
                drop_threshold=rally_thr,  # this is the drop threshold for bear->bull
                alt_breadth_top_trigger=alt_top,
                alt_breadth_bottom_trigger=0.50,
                verbose=False
            )

            # Score against ideal
            df_tmp = df.copy()
            df_tmp['regime_test'] = r
            misclassified = 0
            total = 0
            for (yr, mo), ideal_r in ideal_monthly.items():
                mask = (df_tmp['year'] == yr) & (df_tmp['month'] == mo)
                month_data = df_tmp[mask]
                if len(month_data) == 0:
                    continue
                total += 1
                bull_days = (month_data['regime_test'] == 'BULL').sum()
                bear_days = (month_data['regime_test'] == 'BEAR').sum()
                actual = 'BULL' if bull_days > bear_days else 'BEAR'
                if actual != ideal_r:
                    misclassified += 1

            n_trans = len(trans)
            results.append({
                'ath_dd': ath_dd_trig, 'drop_thr': rally_thr, 'alt_top': alt_top,
                'misclassified': misclassified, 'total': total, 'n_transitions': n_trans,
                'accuracy': (total - misclassified) / total * 100 if total > 0 else 0
            })

# Sort by accuracy descending
results.sort(key=lambda x: (-x['accuracy'], x['n_transitions']))

# Suppress the repeated "Confirmed pivots" output - just show summary
print(f"{'ATH DD':>8} {'Drop Thr':>10} {'Alt Top':>9} {'Accuracy':>10} {'Misclass':>10} {'Trans':>7}")
print("-" * 60)
for r in results[:20]:
    print(f"{r['ath_dd']:>8.2f} {r['drop_thr']:>10.2f} {r['alt_top']:>9.2f} {r['accuracy']:>9.1f}% {r['misclassified']:>10} {r['n_transitions']:>7}")

# Show best config timeline
print("\n--- Best Configuration Timeline ---")
best = results[0]
print(f"Parameters: ATH DD={best['ath_dd']}, Drop Threshold={best['drop_thr']}, Alt Top={best['alt_top']}")
print(f"Accuracy: {best['accuracy']:.1f}% ({best['total'] - best['misclassified']}/{best['total']} months correct)")
print(f"Transitions: {best['n_transitions']}")

r_best, trans_best = detect_reversals(
    df, pivots, dates, prices,
    ath_dd_trigger=best['ath_dd'],
    rally_threshold=1.0,
    drop_threshold=best['drop_thr'],
    alt_breadth_top_trigger=best['alt_top'],
    alt_breadth_bottom_trigger=0.50
)

df['regime_best'] = r_best

print("\nMonthly timeline (best config):")
for year in sorted(df['year'].unique()):
    year_data = df[df['year'] == year]
    months = []
    for m in range(1, 13):
        month_data = year_data[year_data['month'] == m]
        if len(month_data) == 0:
            months.append(' ')
        else:
            bull_days = (month_data['regime_best'] == 'BULL').sum()
            bear_days = (month_data['regime_best'] == 'BEAR').sum()
            months.append('U' if bull_days > bear_days else 'B')
    print(f"  {year}: {''.join(months)}  (J F M A M J J A S O N D)")

print("\nTransitions (best config):")
for t in trans_best:
    print(f"  {t['date'].date()}: {t['from']} -> {t['to']} @ ${t['close']:,.0f} ({t['trigger']})")

# Show misclassified months
print("\nMisclassified months (best config):")
for (yr, mo), ideal_r in sorted(ideal_monthly.items()):
    mask = (df['year'] == yr) & (df['month'] == mo)
    month_data = df[mask]
    if len(month_data) == 0:
        continue
    bull_days = (month_data['regime_best'] == 'BULL').sum()
    bear_days = (month_data['regime_best'] == 'BEAR').sum()
    actual = 'BULL' if bull_days > bear_days else 'BEAR'
    if actual != ideal_r:
        print(f"  {yr}-{mo:02d}: got {actual}, expected {ideal_r}")

print("\n" + "=" * 80)
print("DONE")
print("=" * 80)
