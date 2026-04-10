"""
Debug s523u regime detection vs s523r hardcoded year-split.

Compares halving-cycle-based regime classification (s523u) against
hardcoded bear year list (s523r) to identify where they diverge and
how that affects short gating and long suppression.
"""

import numpy as np
import pandas as pd

# ======================================================================
# CONFIG (matching s523u parameters exactly)
# ======================================================================

HALVING_DATES = [
    pd.Timestamp('2012-11-28'),
    pd.Timestamp('2016-07-09'),
    pd.Timestamp('2020-05-11'),
    pd.Timestamp('2024-04-20'),
    pd.Timestamp('2028-03-20'),
]

BULL_PHASE_END = 365       # days after halving: 0-365 = bull
BEAR_PHASE_END = 1095      # days after halving: 365-1095 = bear
PRICE_OVERRIDE_THRESH = -0.25

# s523r hardcoded bear years
S523R_BEAR_YEARS = {2021, 2022, 2025, 2026, 2029, 2030}

# Deep bear thresholds (s523u)
BEAR_DEEP_BEAR_THRESH = -0.10
BULL_DEEP_BEAR_THRESH = -0.05

# ======================================================================
# LOAD BTC DATA
# ======================================================================

BTC_PATH = "/workspace/crypto_backtest/data/perp/binance/1h_ohlcv/BTC_perp_1h.csv"
print(f"Loading BTC 1h data from {BTC_PATH}...")
df = pd.read_csv(BTC_PATH, parse_dates=["datetime"]).set_index("datetime").sort_index()
if df.index.tz is not None:
    df.index = df.index.tz_convert(None)

close = df["close"].astype(np.float64)
idx = df.index
n = len(idx)
print(f"Loaded {n:,} bars from {idx[0]} to {idx[-1]}")

# ======================================================================
# HALVING CYCLE REGIME (s523u logic, copied exactly)
# ======================================================================

# BUG DETECTION: check if asi8 and Timestamp.value use same units
_test_ts = pd.Timestamp("2022-01-01")
_test_idx = pd.DatetimeIndex([_test_ts])
_unit_ratio = _test_ts.value / _test_idx.asi8[0]
print(f"\n*** UNIT CHECK: Timestamp.value / idx.asi8 = {_unit_ratio:.0f}x ***")
if _unit_ratio != 1.0:
    print(f"*** BUG FOUND IN s523u! idx.asi8 returns {idx.dtype} units, "
          f"but Timestamp.value returns nanoseconds. ***")
    print(f"*** The _compute_regime() function at line 286 has this exact bug. ***")
    print(f"*** Result: halving_ns values are {_unit_ratio:.0f}x larger than ts_ns, "
          f"so 'after_halving' is ALWAYS False, and regime is NEVER bear. ***\n")

# Use consistent units (convert halving dates to same unit as asi8)
ts_ns = idx.asi8
halving_ns = np.array([hd.value // int(_unit_ratio) for hd in HALVING_DATES])
print(f"(Fixed for diagnostic: dividing halving .value by {_unit_ratio:.0f} to match asi8 units)\n")

after_halving = ts_ns[:, None] >= halving_ns[None, :]
last_halving_idx = np.where(after_halving.any(axis=1),
                             after_halving.sum(axis=1) - 1,
                             -1)
has_halving = last_halving_idx >= 0
days_since = np.zeros(n, dtype=np.float64)
# Convert to days using the correct unit divisor
_ns_per_day = 24 * 3600 * 1e9 / _unit_ratio  # adjust for actual asi8 units
days_since[has_halving] = (
    (ts_ns[has_halving] - halving_ns[last_halving_idx[has_halving]])
    / _ns_per_day
)

# Core halving cycle bear: 365-1095 days after halving
bear_cycle = has_halving & (days_since >= BULL_PHASE_END) & (days_since < BEAR_PHASE_END)

# ======================================================================
# ROLLING RETURNS (for price override, short gate, deep bear)
# ======================================================================

ret_90d = (close / close.shift(90 * 24) - 1).values
ret_90d = np.nan_to_num(ret_90d, nan=0.0)

ret_45d = (close / close.shift(45 * 24) - 1).values
ret_45d = np.nan_to_num(ret_45d, nan=0.0)

ret_30d = (close / close.shift(30 * 24) - 1).values
ret_30d = np.nan_to_num(ret_30d, nan=0.0)

# s523r uses 1mo return for short gate (shift(30*24))
ret_1mo = ret_30d  # same thing

# Price override: force bear if 90d return < -25%
price_override = ret_90d < PRICE_OVERRIDE_THRESH

# Combined s523u bear regime
bear_s523u = bear_cycle | price_override

# s523r bear regime (hardcoded years)
bar_years = np.array([t.year for t in idx])
bear_s523r = np.isin(bar_years, list(S523R_BEAR_YEARS))

# ======================================================================
# SHORT GATING
# ======================================================================

# s523u: bear regime -> shorts ungated (True), bull regime -> gated by 45d return < 0
short_ok_s523u = np.where(bear_s523u, True, ret_45d < 0)

# s523r: bear years -> shorts ungated (True), bull years -> gated by 1mo return < 0
short_ok_s523r = np.where(bear_s523r, True, ret_1mo < 0)

# ======================================================================
# DEEP BEAR LONG SUPPRESSION
# ======================================================================

# s523u: bear regime -> block longs at -10%, bull regime -> block at -5%
deep_bear_thresh_s523u = np.where(bear_s523u, BEAR_DEEP_BEAR_THRESH, BULL_DEEP_BEAR_THRESH)
longs_blocked_s523u = ret_30d < deep_bear_thresh_s523u

# s523r: NO deep bear filter at all (all longs pass)
longs_blocked_s523r = np.zeros(n, dtype=bool)

# ======================================================================
# ANALYSIS BY PERIOD
# ======================================================================

periods = [
    ("2022", (idx >= "2022-01-01") & (idx < "2023-01-01")),
    ("2023", (idx >= "2023-01-01") & (idx < "2024-01-01")),
    ("2024", (idx >= "2024-01-01") & (idx < "2025-01-01")),
    ("2025", (idx >= "2025-01-01") & (idx < "2026-01-01")),
    ("2026Q1", (idx >= "2026-01-01") & (idx < "2026-04-01")),
]

print()
print("=" * 120)
print(f"{'Period':<8} | {'%BEAR(cycle)':<12} | {'%BEAR(s523r)':<12} | {'Agree?':<8} | "
      f"{'%PriceOvrd':<11} | {'%Short_OK(u)':<13} | {'%Short_OK(r)':<13} | "
      f"{'%LongBlk(u)':<12} | {'%LongBlk(r)':<12}")
print("=" * 120)

for label, mask in periods:
    n_bars = mask.sum()
    if n_bars == 0:
        print(f"{label:<8} | NO DATA")
        continue

    pct_bear_cycle = 100.0 * bear_cycle[mask].sum() / n_bars
    pct_bear_s523r = 100.0 * bear_s523r[mask].sum() / n_bars
    pct_price_override = 100.0 * price_override[mask].sum() / n_bars
    pct_bear_s523u = 100.0 * bear_s523u[mask].sum() / n_bars

    agree = "YES" if abs(pct_bear_cycle - pct_bear_s523r) < 1.0 else "NO"

    pct_short_ok_u = 100.0 * short_ok_s523u[mask].sum() / n_bars
    pct_short_ok_r = 100.0 * short_ok_s523r[mask].sum() / n_bars

    pct_long_blk_u = 100.0 * longs_blocked_s523u[mask].sum() / n_bars
    pct_long_blk_r = 100.0 * longs_blocked_s523r[mask].sum() / n_bars

    print(f"{label:<8} | {pct_bear_cycle:>10.1f}% | {pct_bear_s523r:>10.1f}% | {agree:<8} | "
          f"{pct_price_override:>9.1f}% | {pct_short_ok_u:>11.1f}% | {pct_short_ok_r:>11.1f}% | "
          f"{pct_long_blk_u:>10.1f}% | {pct_long_blk_r:>10.1f}%")

print("=" * 120)

# ======================================================================
# DETAILED REGIME TIMELINE
# ======================================================================

print()
print("DETAILED REGIME TIMELINE (monthly)")
print("-" * 100)
print(f"{'Month':<10} | {'DaysSinceHalv':>14} | {'CycleBear':>10} | {'PriceOvrd':>10} | "
      f"{'s523u_Bear':>10} | {'s523r_Bear':>10} | {'90d_ret':>8}")
print("-" * 100)

# Monthly aggregation
months = pd.date_range("2022-01-01", idx[-1], freq="MS")
for m in months:
    m_end = m + pd.offsets.MonthEnd(1) + pd.Timedelta(hours=23)
    m_mask = (idx >= m) & (idx <= m_end)
    n_m = m_mask.sum()
    if n_m == 0:
        continue

    # Representative: midpoint of month
    mid_idx = n_m // 2
    m_indices = np.where(m_mask)[0]
    mid = m_indices[mid_idx]

    avg_days = days_since[m_mask].mean()
    pct_cycle_bear = 100.0 * bear_cycle[m_mask].mean()
    pct_price_ovrd = 100.0 * price_override[m_mask].mean()
    pct_s523u_bear = 100.0 * bear_s523u[m_mask].mean()
    pct_s523r_bear = 100.0 * bear_s523r[m_mask].mean()
    avg_ret90 = ret_90d[m_mask].mean() * 100

    print(f"{m.strftime('%Y-%m'):<10} | {avg_days:>14.0f} | {pct_cycle_bear:>9.0f}% | "
          f"{pct_price_ovrd:>9.0f}% | {pct_s523u_bear:>9.0f}% | {pct_s523r_bear:>9.0f}% | "
          f"{avg_ret90:>+7.1f}%")

print("-" * 100)

# ======================================================================
# KEY DIVERGENCES
# ======================================================================

print()
print("KEY DIVERGENCES (where s523u and s523r disagree on bear/bull)")
print("=" * 80)

# Find months where they disagree significantly
for m in months:
    m_end = m + pd.offsets.MonthEnd(1) + pd.Timedelta(hours=23)
    m_mask = (idx >= m) & (idx <= m_end)
    n_m = m_mask.sum()
    if n_m == 0:
        continue

    pct_s523u = 100.0 * bear_s523u[m_mask].mean()
    pct_s523r = 100.0 * bear_s523r[m_mask].mean()
    diff = pct_s523u - pct_s523r

    if abs(diff) > 5:
        direction = "s523u=BEAR, s523r=BULL" if diff > 0 else "s523u=BULL, s523r=BEAR"
        avg_days = days_since[m_mask].mean()
        avg_ret90 = ret_90d[m_mask].mean() * 100
        print(f"  {m.strftime('%Y-%m')}: {direction}  "
              f"(days_since_halving={avg_days:.0f}, 90d_ret={avg_ret90:+.1f}%)")

# ======================================================================
# IMPACT SUMMARY
# ======================================================================

print()
print("=" * 80)
print("IMPACT SUMMARY")
print("=" * 80)

# Count total bars where deep bear blocks longs in s523u but NOT in s523r
extra_long_blocks = longs_blocked_s523u & ~longs_blocked_s523r
for label, mask in periods:
    n_bars = mask.sum()
    if n_bars == 0:
        continue
    extra = (extra_long_blocks & mask).sum()
    pct = 100.0 * extra / n_bars
    print(f"  {label}: s523u blocks {extra:,} extra long bars ({pct:.1f}%) vs s523r (no deep bear filter)")

print()

# Short gate differences
for label, mask in periods:
    n_bars = mask.sum()
    if n_bars == 0:
        continue
    diff_short = short_ok_s523u[mask].sum() - short_ok_s523r[mask].sum()
    pct = 100.0 * diff_short / n_bars
    sign = "+" if diff_short >= 0 else ""
    print(f"  {label}: s523u has {sign}{diff_short:,} ({sign}{pct:.1f}%) different short-allowed bars vs s523r")

print()

# The critical question: 2024 halving year
print("CRITICAL: 2024 halving year classification")
print("-" * 60)
mask_2024 = (idx >= "2024-01-01") & (idx < "2025-01-01")
if mask_2024.any():
    # When does the halving happen in 2024?
    halving_2024 = pd.Timestamp("2024-04-20")
    pre_halving = (idx >= "2024-01-01") & (idx < "2024-04-20")
    post_halving = (idx >= "2024-04-20") & (idx < "2025-01-01")

    if pre_halving.any():
        pct_pre = 100.0 * bear_cycle[pre_halving].mean()
        d_pre = days_since[pre_halving].mean()
        print(f"  2024 Jan-Apr (pre-halving):  cycle_bear={pct_pre:.0f}%  avg_days_since={d_pre:.0f}")
        print(f"    -> This is late in 2020 cycle: {d_pre:.0f} days after 2020-05-11 halving")
        is_bear = d_pre >= BULL_PHASE_END and d_pre < BEAR_PHASE_END
        print(f"    -> In [{BULL_PHASE_END}, {BEAR_PHASE_END})? {'YES = BEAR' if is_bear else 'NO = BULL'}")

    if post_halving.any():
        pct_post = 100.0 * bear_cycle[post_halving].mean()
        d_post = days_since[post_halving].mean()
        print(f"  2024 Apr-Dec (post-halving): cycle_bear={pct_post:.0f}%  avg_days_since={d_post:.0f}")
        print(f"    -> This is early in 2024 cycle: {d_post:.0f} days after 2024-04-20 halving")
        is_bear = d_post >= BULL_PHASE_END and d_post < BEAR_PHASE_END
        print(f"    -> In [{BULL_PHASE_END}, {BEAR_PHASE_END})? {'YES = BEAR' if is_bear else 'NO = BULL'}")

    print(f"  s523r: 2024 is BULL (not in bear years list)")

print()
print("CRITICAL: 2025 transition")
print("-" * 60)
mask_2025 = (idx >= "2025-01-01") & (idx < "2026-01-01")
if mask_2025.any():
    # When does 365 days after 2024-04-20 occur?
    bear_start = halving_2024 + pd.Timedelta(days=365)
    print(f"  Bear phase starts: {bear_start.strftime('%Y-%m-%d')} (365 days after 2024-04-20)")
    pre_bear = (idx >= "2025-01-01") & (idx < bear_start)
    post_bear = (idx >= bear_start) & (idx < "2026-01-01")

    if pre_bear.any():
        pct = 100.0 * bear_cycle[pre_bear].mean()
        d = days_since[pre_bear].mean()
        print(f"  2025 Jan-{bear_start.strftime('%b %d')}: cycle_bear={pct:.0f}%  avg_days={d:.0f} -> BULL")
    if post_bear.any():
        pct = 100.0 * bear_cycle[post_bear].mean()
        d = days_since[post_bear].mean()
        print(f"  2025 {bear_start.strftime('%b %d')}-Dec: cycle_bear={pct:.0f}%  avg_days={d:.0f} -> BEAR")

    print(f"  s523r: 2025 is BEAR (full year in bear years list)")
    print(f"  DIVERGENCE: s523u classifies Jan-Apr 2025 as BULL, s523r as BEAR")
