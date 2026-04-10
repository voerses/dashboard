"""
Regime Reversal V2 — Research Script
======================================

State machine detecting cycle tops/bottoms using:
  BEAR LOW -> BULL: score >= 4 (rally, RSI, alt breadth, momentum)
    Requires above SMA200 or rally > 80% to filter false bear-market bounces.
  BULL HIGH -> BEAR: ATH_DD < -25% REQUIRED + near_ath_120d REQUIRED + score >= 5

Key finding: The dynamic regime is useful for conviction adjustment but NOT for
hard short gating. The halving cycle prior (post-halving years = shorts ungated)
is the dominant signal. The dynamic regime adds value as a 1.2x long conviction
boost during BULL periods.

Strategy s523z_reversal uses:
  1. Halving cycle for short gating (post-halving years ungated)
  2. Dynamic regime for conviction boost (longs boosted 1.2x in BULL)
"""

import numpy as np
import pandas as pd
import os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

btc_1h = pd.read_csv(
    os.path.join(BASE, "data", "perp", "binance", "1h_ohlcv", "BTC_perp_1h.csv"),
    parse_dates=["datetime"],
).set_index("datetime").sort_index()
if btc_1h.index.tz is not None:
    btc_1h.index = btc_1h.index.tz_convert(None)

btc_daily = btc_1h["close"].resample("1D").last().dropna()

regime_df = pd.read_parquet(os.path.join(BASE, "data", "alternative", "regime_signals.parquet"))
if regime_df.index.tz is not None:
    regime_df.index = regime_df.index.tz_localize(None)

alt_breadth_50d = regime_df["alt_breadth_50d"].reindex(btc_daily.index, method="ffill")
alt_breadth_20d = regime_df["alt_breadth_20d"].reindex(btc_daily.index, method="ffill")

close = btc_daily.values
idx = btc_daily.index
n = len(close)

# === Signals ===
low_365d = pd.Series(close, index=idx).rolling(365, min_periods=90).min().values
rally_from_low = (close - low_365d) / (low_365d + 1e-10)

ret_1d = np.diff(close, prepend=close[0]) / (np.roll(close, 1) + 1e-10)
ret_1d[0] = 0
gain = np.where(ret_1d > 0, ret_1d, 0.0)
loss = np.where(ret_1d < 0, -ret_1d, 0.0)
avg_gain = pd.Series(gain).ewm(span=30, adjust=False).mean().values
avg_loss = pd.Series(loss).ewm(span=30, adjust=False).mean().values
rsi_30d = 100.0 - 100.0 / (1.0 + avg_gain / (avg_loss + 1e-10))

ab50 = alt_breadth_50d.values
ab20 = alt_breadth_20d.values

ret_30d = np.full(n, np.nan)
ret_30d[30:] = close[30:] / close[:-30] - 1

ath = np.maximum.accumulate(close)
ath_dd = (close - ath) / (ath + 1e-10)

within_5pct = (close >= ath * 0.95)
near_ath_120d = pd.Series(within_5pct.astype(float), index=idx).rolling(120, min_periods=1).max().values > 0

ab50_shift30 = np.full(n, np.nan)
ab50_shift30[30:] = ab50[:-30]

sma350 = pd.Series(close, index=idx).rolling(350, min_periods=100).mean().values
sma200 = pd.Series(close, index=idx).rolling(200, min_periods=100).mean().values

# === State Machine ===
BEAR_TO_BULL_HOLD = 60
BULL_TO_BEAR_HOLD = 120

state = "BEAR" if np.isnan(sma350[0]) or close[0] <= sma350[0] else "BULL"
regime = np.empty(n, dtype='U4')
regime[:] = state
last_flip_idx = 0
reversals = []

for i in range(1, n):
    days_in_state = i - last_flip_idx

    if state == "BEAR" and days_in_state >= BEAR_TO_BULL_HOLD:
        above_sma = (not np.isnan(sma200[i])) and close[i] > sma200[i]
        big_rally = (not np.isnan(rally_from_low[i])) and rally_from_low[i] > 0.80
        sma_na = np.isnan(sma200[i])
        if above_sma or big_rally or sma_na:
            sc = 0; sf = []
            if not np.isnan(rally_from_low[i]) and rally_from_low[i] > 0.50: sc += 2; sf.append(f"rally={rally_from_low[i]:.0%}")
            if rsi_30d[i] > 55: sc += 1; sf.append(f"RSI={rsi_30d[i]:.0f}")
            if not np.isnan(ab50[i]) and ab50[i] > 0.50: sc += 2; sf.append(f"ab50={ab50[i]:.2f}")
            if not np.isnan(ret_30d[i]) and ret_30d[i] > 0.15: sc += 1; sf.append(f"ret30d={ret_30d[i]:.0%}")
            if not np.isnan(ab20[i]) and not np.isnan(ab50[i]) and ab20[i] > ab50[i]: sc += 1; sf.append(f"ab20>ab50")
            if sc >= 4:
                state = "BULL"; last_flip_idx = i
                reversals.append({"date": idx[i], "type": "BEAR_LOW -> BULL",
                    "price": close[i], "score": sc, "signals": ", ".join(sf)})

    elif state == "BULL" and days_in_state >= BULL_TO_BEAR_HOLD:
        if (not np.isnan(ath_dd[i]) and ath_dd[i] < -0.25 and near_ath_120d[i]):
            sc = 3; sf = [f"ath_dd={ath_dd[i]:.0%}", "near_ath_120d"]
            if not np.isnan(ab50[i]) and ab50[i] < 0.20: sc += 2; sf.append(f"ab50={ab50[i]:.2f}")
            if not np.isnan(ret_30d[i]) and ret_30d[i] < -0.10: sc += 1; sf.append(f"ret30d={ret_30d[i]:.0%}")
            if not np.isnan(ab50[i]) and not np.isnan(ab50_shift30[i]) and ab50[i] < ab50_shift30[i]: sc += 1; sf.append("ab50_decline")
            if not np.isnan(sma200[i]) and close[i] < sma200[i]: sc += 1; sf.append("below_sma200")
            if sc >= 5:
                state = "BEAR"; last_flip_idx = i
                reversals.append({"date": idx[i], "type": "BULL_HIGH -> BEAR",
                    "price": close[i], "score": sc, "signals": ", ".join(sf)})

    regime[i] = state

# === Output ===
print("=" * 80)
print("REGIME REVERSAL V2 — State Machine Results")
print("=" * 80)

print(f"\nInitial state: {regime[0]} (BTC={close[0]:.0f})")
print(f"BEAR->BULL: hold >= {BEAR_TO_BULL_HOLD}d, (above SMA200 or rally>80%), score >= 4")
print(f"BULL->BEAR: hold >= {BULL_TO_BEAR_HOLD}d, ATH_DD < -25%, near_ath_120d, score >= 5")
print(f"\nDetected {len(reversals)} reversals:\n")

for r in reversals:
    print(f"  {r['date'].strftime('%Y-%m-%d')}  {r['type']:22s}  BTC=${r['price']:>10,.0f}  "
          f"score={r['score']}  [{r['signals']}]")

print("\n\nMonthly Regime Timeline:")
print("-" * 80)
regime_s = pd.Series(regime, index=idx)
monthly = regime_s.resample("MS").last()
for year in range(2020, 2027):
    months = monthly[monthly.index.year == year]
    if len(months) == 0: continue
    labels = ["B" if m == "BEAR" else "U" for m in months]
    mn = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][:len(labels)]
    print(f"  {year}: {' '.join(f'{l:>3s}' for l in labels)}   ({', '.join(mn)})")

print("\n\nYear Summary:")
print(f"  {'Year':>6s}  {'%BEAR':>6s}  {'%BULL':>6s}  {'Ideal':>12s}  {'Match':>5s}")
print(f"  {'-'*6}  {'-'*6}  {'-'*6}  {'-'*12}  {'-'*5}")

ideal = {2020: "MIXED", 2021: "MIXED", 2022: "BEAR", 2023: "BULL", 2024: "BULL", 2025: "BEAR", 2026: "BEAR"}

for year in range(2020, 2027):
    yd = regime_s[regime_s.index.year == year]
    if len(yd) == 0: continue
    pb = (yd == "BEAR").mean() * 100
    pu = (yd == "BULL").mean() * 100
    dom = "BEAR" if pb > 60 else ("BULL" if pu > 60 else "MIXED")
    exp = ideal.get(year, "?")
    match = "YES" if dom == exp else ("~" if exp == "MIXED" else "NO")
    print(f"  {year:>6d}  {pb:>5.1f}%  {pu:>5.1f}%  {exp:>12s}  {match:>5s}")

print("""
STRATEGY INTEGRATION RESULTS:
================================
s523z_reversal uses this regime for 1.2x long conviction boost in BULL,
combined with halving cycle prior for short gating.

| Year  | s523z  | s523v (baseline) | Delta  |
|-------|--------|------------------|--------|
| 2022  | +76%   | +76%             | 0      |
| 2023  | +220%  | +194%            | +26    |
| 2024  | +22%   | +48%             | -26    |
| 2025  | +396%  | +292%            | +104   |
| 2026Q1| +42%   | +42%             | 0      |
| TOTAL | +756%  | +652%            | +104   |

Key finding: Dynamic regime as conviction boost adds +104pp total.
2023 and 2025 benefit most (+26pp, +104pp). 2024 loses -26pp from
bull-regime long boost in a choppy sideways year.
""")
