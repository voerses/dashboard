#!/usr/bin/env python3
"""
MULTI-STATE Combinatoric Regime Detector for Crypto Markets
============================================================
Classifies each day into one of 6 regime states based on BTC + alt breadth signals.
Produces monthly summaries, transition analysis, forward return validation,
threshold sensitivity sweeps, strategy parameter mapping, and a simplified 3-state version.
"""

import numpy as np
import pandas as pd
from collections import Counter
from itertools import product

# ── Load data ────────────────────────────────────────────────────────────────
DATA_PATH = "/workspace/crypto_backtest/data/alternative/regime_signals.parquet"
df = pd.read_parquet(DATA_PATH)
df.index = pd.to_datetime(df.index)

# Ensure we have btc_above_sma50 as bool
df["btc_above_sma50"] = df["btc_above_sma50"].astype(bool)
df["btc_below_sma50"] = ~df["btc_above_sma50"]

print("=" * 90)
print("MULTI-STATE REGIME DETECTOR — 6-State Combinatoric Classifier")
print("=" * 90)
print(f"Data: {len(df)} days from {df.index.min().date()} to {df.index.max().date()}")
print(f"Columns: {list(df.columns)}")
print()


# ── 1. SIX-STATE CLASSIFIER ─────────────────────────────────────────────────
REGIME_NAMES = ["BEAR_CRASH", "ALT_BLEED", "RECOVERY", "ALT_SEASON", "BULL_BTC", "RANGING"]
REGIME_ABBREV = {
    "ALT_SEASON": "A",
    "BULL_BTC": "B",
    "RANGING": "R",
    "ALT_BLEED": "L",
    "BEAR_CRASH": "C",
    "RECOVERY": "V",
}


def classify_regime(row, thresholds=None):
    """Classify a single day into one of 6 regimes using priority cascade."""
    if thresholds is None:
        thresholds = {}

    # Thresholds with defaults
    t = {
        "bear_crash_btc_ret": thresholds.get("bear_crash_btc_ret", -0.10),
        "bear_crash_breadth": thresholds.get("bear_crash_breadth", 0.25),
        "alt_bleed_breadth": thresholds.get("alt_bleed_breadth", 0.35),
        "alt_bleed_spread": thresholds.get("alt_bleed_spread", -0.05),
        "alt_season_breadth": thresholds.get("alt_season_breadth", 0.60),
        "alt_season_spread": thresholds.get("alt_season_spread", 0.05),
        "bull_btc_spread": thresholds.get("bull_btc_spread", -0.03),
        "ranging_btc_ret_abs": thresholds.get("ranging_btc_ret_abs", 0.05),
        "ranging_breadth_lo": thresholds.get("ranging_breadth_lo", 0.35),
        "ranging_breadth_hi": thresholds.get("ranging_breadth_hi", 0.60),
    }

    btc_ret_30d = row["btc_ret_30d"]
    btc_above = row["btc_above_sma50"]
    btc_below = not btc_above
    breadth_50d = row["alt_breadth_50d"]
    breadth_20d = row["alt_breadth_20d"]
    spread_30d = row["alt_btc_spread_30d"]

    # Handle NaN — default to RANGING if missing data
    if pd.isna(btc_ret_30d) or pd.isna(breadth_50d) or pd.isna(spread_30d):
        return "RANGING"

    # Priority cascade
    # 1. BEAR_CRASH
    if btc_below and btc_ret_30d < t["bear_crash_btc_ret"] and breadth_50d < t["bear_crash_breadth"]:
        return "BEAR_CRASH"

    # 2. ALT_BLEED
    if breadth_50d < t["alt_bleed_breadth"] and spread_30d < t["alt_bleed_spread"]:
        return "ALT_BLEED"

    # 3. RECOVERY
    if btc_below and btc_ret_30d > 0 and not pd.isna(breadth_20d) and breadth_20d > breadth_50d:
        return "RECOVERY"

    # 4. ALT_SEASON
    if breadth_50d > t["alt_season_breadth"] and spread_30d > t["alt_season_spread"] and btc_ret_30d > 0:
        return "ALT_SEASON"

    # 5. BULL_BTC
    if btc_above and btc_ret_30d > 0 and spread_30d < t["bull_btc_spread"]:
        return "BULL_BTC"

    # 6. RANGING (default)
    return "RANGING"


def classify_all(df, thresholds=None):
    """Classify all days."""
    return df.apply(lambda row: classify_regime(row, thresholds), axis=1)


df["regime_6"] = classify_all(df)

# Simplified 3-state
def map_3state(regime):
    if regime in ("ALT_SEASON", "BULL_BTC"):
        return "RISK_ON"
    elif regime in ("RANGING", "RECOVERY"):
        return "NEUTRAL"
    else:  # ALT_BLEED, BEAR_CRASH
        return "RISK_OFF"

df["regime_3"] = df["regime_6"].map(map_3state)

# Summary counts
print("─" * 90)
print("TASK 1: Regime Classification Summary")
print("─" * 90)
counts = df["regime_6"].value_counts()
for regime in REGIME_NAMES:
    c = counts.get(regime, 0)
    pct = 100.0 * c / len(df)
    print(f"  {regime:<14s}: {c:>5d} days ({pct:>5.1f}%)")
print()


# ── 2. MONTHLY SUMMARY TABLE ────────────────────────────────────────────────
print("─" * 90)
print("TASK 2: Monthly Summary Table (2022-01 to 2026-03)")
print("─" * 90)

df["year_month"] = df.index.to_period("M")
monthly = df.loc["2022-01":"2026-03"].copy()

# Group by month
groups = monthly.groupby("year_month")

header = f"{'Month':<10s} {'Dominant':<14s} {'BTC Ret':>8s} {'Breadth':>8s} | "
for r in REGIME_NAMES:
    header += f"{REGIME_ABBREV[r]:>3s} "
print(header)
print("-" * len(header))

for period, g in groups:
    mode = g["regime_6"].mode().iloc[0]
    btc_ret = g["btc_ret_30d"].mean()
    breadth = g["alt_breadth_50d"].mean()
    line = f"{str(period):<10s} {mode:<14s} {btc_ret:>+7.1%} {breadth:>7.2f} | "
    total = len(g)
    for r in REGIME_NAMES:
        pct = 100.0 * (g["regime_6"] == r).sum() / total
        line += f"{pct:>3.0f} "
    print(line)
print()


# ── 3. REGIME TRANSITION ANALYSIS ───────────────────────────────────────────
print("─" * 90)
print("TASK 3: Regime Transition Analysis")
print("─" * 90)

# Transitions
regimes_series = df["regime_6"]
transitions = []
for i in range(1, len(regimes_series)):
    if regimes_series.iloc[i] != regimes_series.iloc[i - 1]:
        transitions.append((df.index[i].year, regimes_series.iloc[i - 1], regimes_series.iloc[i]))

# Transitions per year
print("\n  Transitions per year:")
year_counts = Counter(t[0] for t in transitions)
for y in sorted(year_counts):
    print(f"    {y}: {year_counts[y]} transitions")

# Average duration of each regime
print("\n  Average regime duration (consecutive days):")
durations = {r: [] for r in REGIME_NAMES}
current_regime = regimes_series.iloc[0]
current_start = 0
for i in range(1, len(regimes_series)):
    if regimes_series.iloc[i] != current_regime:
        durations[current_regime].append(i - current_start)
        current_regime = regimes_series.iloc[i]
        current_start = i
durations[current_regime].append(len(regimes_series) - current_start)

for r in REGIME_NAMES:
    if durations[r]:
        avg = np.mean(durations[r])
        med = np.median(durations[r])
        mx = np.max(durations[r])
        n = len(durations[r])
        print(f"    {r:<14s}: avg={avg:>6.1f}d  median={med:>5.0f}d  max={mx:>4d}d  ({n} episodes)")
    else:
        print(f"    {r:<14s}: no episodes")

# Transition matrix
print("\n  Transition matrix P(next_state | current_state):")
trans_counts = pd.DataFrame(0, index=REGIME_NAMES, columns=REGIME_NAMES)
for _, from_r, to_r in transitions:
    trans_counts.loc[from_r, to_r] += 1

trans_probs = trans_counts.div(trans_counts.sum(axis=1).replace(0, 1), axis=0)
from_to_label = "From \\ To"
print(f"  {from_to_label:<14s}", end="")
for r in REGIME_NAMES:
    print(f" {REGIME_ABBREV[r]:>5s}", end="")
print()
for from_r in REGIME_NAMES:
    print(f"  {REGIME_ABBREV[from_r]:<14s}", end="")
    for to_r in REGIME_NAMES:
        print(f" {trans_probs.loc[from_r, to_r]:>5.2f}", end="")
    print()
print()


# ── 4. PERFORMANCE VALIDATION ───────────────────────────────────────────────
print("─" * 90)
print("TASK 4: Forward Return Validation by Regime")
print("─" * 90)

# Compute forward 30d returns
df["fwd_btc_30d"] = df["btc_close"].pct_change(30).shift(-30)
df["fwd_alt_30d"] = df["alt_index_ret_30d"].shift(-30)  # alt_index_ret_30d is already 30d return
# For forward alt-btc spread, shift the spread column
df["fwd_spread_30d"] = df["alt_btc_spread_30d"].shift(-30)

print(f"\n  {'Regime':<14s} {'Fwd BTC 30d':>12s} {'Fwd Alt 30d':>12s} {'Fwd Spread':>12s} {'N days':>8s}")
print("  " + "-" * 62)
for r in REGIME_NAMES:
    mask = df["regime_6"] == r
    fwd_btc = df.loc[mask, "fwd_btc_30d"].mean()
    fwd_alt = df.loc[mask, "fwd_alt_30d"].mean()
    fwd_spr = df.loc[mask, "fwd_spread_30d"].mean()
    n = mask.sum()
    btc_str = f"{fwd_btc:>+11.2%}" if not pd.isna(fwd_btc) else f"{'N/A':>12s}"
    alt_str = f"{fwd_alt:>+11.2%}" if not pd.isna(fwd_alt) else f"{'N/A':>12s}"
    spr_str = f"{fwd_spr:>+11.2%}" if not pd.isna(fwd_spr) else f"{'N/A':>12s}"
    print(f"  {r:<14s} {btc_str} {alt_str} {spr_str} {n:>8d}")

print("\n  Interpretation:")
print("  - BEAR_CRASH should show positive fwd returns (bottom = buy signal)")
print("  - ALT_SEASON should show lower fwd returns (tops = mean reversion)")
print("  - ALT_BLEED fwd returns tell us if shorts profit after detection")
print()


# ── 5. THRESHOLD SENSITIVITY SWEEP ──────────────────────────────────────────
print("─" * 90)
print("TASK 5: Threshold Sensitivity Sweep (+/- 20%)")
print("─" * 90)

base_thresholds = {
    "bear_crash_btc_ret": -0.10,
    "bear_crash_breadth": 0.25,
    "alt_bleed_breadth": 0.35,
    "alt_bleed_spread": -0.05,
    "alt_season_breadth": 0.60,
    "alt_season_spread": 0.05,
    "bull_btc_spread": -0.03,
}

# Baseline classification
baseline = df["regime_6"].copy()
baseline_counts = baseline.value_counts()

print(f"\n  {'Threshold':<24s} {'Base Val':>9s} {'Swept Val':>10s} {'Changed Days':>13s} {'% Changed':>10s} {'Impact':>8s}")
print("  " + "-" * 78)

sensitivity_results = []

for param_name, base_val in base_thresholds.items():
    for direction, mult in [("low", 0.80), ("high", 1.20)]:
        swept_val = base_val * mult
        swept_thresholds = {param_name: swept_val}
        swept_regimes = classify_all(df, swept_thresholds)
        changed = (swept_regimes != baseline).sum()
        pct_changed = 100.0 * changed / len(df)
        tag = "LOW" if pct_changed > 10 else ("MED" if pct_changed > 3 else "low")
        print(f"  {param_name:<24s} {base_val:>+9.3f} {swept_val:>+10.3f} {changed:>13d} {pct_changed:>9.1f}% {tag:>8s}")
        sensitivity_results.append((param_name, direction, changed, pct_changed))

# Most sensitive thresholds
print("\n  Most sensitive thresholds (ranked by avg % changed):")
by_param = {}
for name, direction, changed, pct in sensitivity_results:
    by_param.setdefault(name, []).append(pct)
ranked = sorted(by_param.items(), key=lambda x: -np.mean(x[1]))
for i, (name, pcts) in enumerate(ranked, 1):
    avg_pct = np.mean(pcts)
    print(f"    {i}. {name:<24s} avg {avg_pct:.1f}% days affected")
print()


# ── 6. STRATEGY PARAMETER MAPPING TABLE ─────────────────────────────────────
print("─" * 90)
print("TASK 6: Strategy Parameter Mapping")
print("─" * 90)

mapping = [
    ("ALT_SEASON",  "gated",    "-3%",  "1.5x", "high long returns"),
    ("BULL_BTC",    "45d gate", "-5%",  "1.3x", "moderate"),
    ("RANGING",     "45d gate", "-5%",  "1.0x", "low"),
    ("ALT_BLEED",   "ungated",  "-10%", "0.8x", "high short returns"),
    ("BEAR_CRASH",  "ungated",  "-15%", "0.7x", "short-dominated"),
    ("RECOVERY",    "30d gate", "-5%",  "1.0x", "transition"),
]

print(f"\n  {'Regime':<14s} {'Shorts':<10s} {'Deep Bear':<10s} {'Conviction':<12s} {'Expected':<22s}")
print("  " + "-" * 70)
for row in mapping:
    print(f"  {row[0]:<14s} {row[1]:<10s} {row[2]:<10s} {row[3]:<12s} {row[4]:<22s}")
print()


# ── 7. REGIME TIMELINE VISUALIZATION ────────────────────────────────────────
print("─" * 90)
print("TASK 7: Regime Timeline (one char per month, dominant regime)")
print("─" * 90)
print("  Legend: A=ALT_SEASON  B=BULL_BTC  R=RANGING  L=ALT_BLEED  C=BEAR_CRASH  V=RECOVERY\n")

for year in range(2020, 2027):
    year_data = df[df.index.year == year]
    if len(year_data) == 0:
        continue
    chars = ""
    months_present = sorted(year_data.index.month.unique())
    for m in range(1, 13):
        m_data = year_data[year_data.index.month == m]
        if len(m_data) == 0:
            chars += "."
        else:
            mode = m_data["regime_6"].mode().iloc[0]
            chars += REGIME_ABBREV[mode]
    print(f"  {year}: {chars}   (months: J F M A M J J A S O N D)")

print()


# ── 8. SIMPLIFIED 3-STATE VERSION ───────────────────────────────────────────
print("─" * 90)
print("TASK 8: Simplified 3-State Regime (RISK_ON / NEUTRAL / RISK_OFF)")
print("─" * 90)

state3_names = ["RISK_ON", "NEUTRAL", "RISK_OFF"]
state3_map_desc = {
    "RISK_ON": "ALT_SEASON + BULL_BTC → gate shorts",
    "NEUTRAL": "RANGING + RECOVERY → 45d gate",
    "RISK_OFF": "ALT_BLEED + BEAR_CRASH → ungate shorts",
}

print("\n  Mapping:")
for s, desc in state3_map_desc.items():
    print(f"    {s:<12s} = {desc}")

print(f"\n  3-State Distribution:")
counts3 = df["regime_3"].value_counts()
for s in state3_names:
    c = counts3.get(s, 0)
    pct = 100.0 * c / len(df)
    print(f"    {s:<12s}: {c:>5d} days ({pct:>5.1f}%)")

# Forward returns by 3-state
print(f"\n  {'3-State':<12s} {'Fwd BTC 30d':>12s} {'Fwd Alt 30d':>12s} {'Fwd Spread':>12s} {'N':>6s}")
print("  " + "-" * 48)
for s in state3_names:
    mask = df["regime_3"] == s
    fwd_btc = df.loc[mask, "fwd_btc_30d"].mean()
    fwd_alt = df.loc[mask, "fwd_alt_30d"].mean()
    fwd_spr = df.loc[mask, "fwd_spread_30d"].mean()
    n = mask.sum()
    print(f"  {s:<12s} {fwd_btc:>+11.2%} {fwd_alt:>+11.2%} {fwd_spr:>+11.2%} {n:>6d}")

# 3-state timeline
print("\n  3-State Timeline (O=RISK_ON, N=NEUTRAL, F=RISK_OFF):")
abbrev3 = {"RISK_ON": "O", "NEUTRAL": "N", "RISK_OFF": "F"}
for year in range(2020, 2027):
    year_data = df[df.index.year == year]
    if len(year_data) == 0:
        continue
    chars = ""
    for m in range(1, 13):
        m_data = year_data[year_data.index.month == m]
        if len(m_data) == 0:
            chars += "."
        else:
            mode = m_data["regime_3"].mode().iloc[0]
            chars += abbrev3[mode]
    print(f"    {year}: {chars}")

print()


# ── YEARLY REGIME BREAKDOWN ─────────────────────────────────────────────────
print("─" * 90)
print("BONUS: Yearly Regime Breakdown (% of days)")
print("─" * 90)
print(f"\n  {'Year':<6s}", end="")
for r in REGIME_NAMES:
    print(f" {REGIME_ABBREV[r]:>6s}", end="")
print(f" {'Total':>7s}")
print("  " + "-" * 52)

for year in range(2020, 2027):
    yd = df[df.index.year == year]
    if len(yd) == 0:
        continue
    print(f"  {year:<6d}", end="")
    for r in REGIME_NAMES:
        pct = 100.0 * (yd["regime_6"] == r).sum() / len(yd)
        print(f" {pct:>5.1f}%", end="")
    print(f" {len(yd):>6d}d")

print()


# ── REGIME-CONDITIONED STRATEGY RECOMMENDATIONS ─────────────────────────────
print("─" * 90)
print("SUMMARY: Regime-Conditioned Strategy Recommendations")
print("─" * 90)

# Current regime
latest = df.iloc[-1]
current_regime = latest["regime_6"]
current_3state = latest["regime_3"]
print(f"\n  Current date:   {df.index[-1].date()}")
print(f"  Current regime: {current_regime} ({current_3state})")
print(f"  BTC ret 30d:    {latest['btc_ret_30d']:+.2%}")
print(f"  Alt breadth 50d: {latest['alt_breadth_50d']:.2f}")
print(f"  Alt-BTC spread:  {latest['alt_btc_spread_30d']:+.2%}")
print(f"  BTC above SMA50: {latest['btc_above_sma50']}")

# Find strategy params for current regime
for row in mapping:
    if row[0] == current_regime:
        print(f"\n  RECOMMENDED PARAMS:")
        print(f"    Shorts:     {row[1]}")
        print(f"    Deep bear:  {row[2]}")
        print(f"    Conviction: {row[3]}")
        print(f"    Expected:   {row[4]}")

print()
print("=" * 90)
print("DONE — All 8 tasks complete.")
print("=" * 90)
