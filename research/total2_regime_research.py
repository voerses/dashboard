"""
TOTAL2/TOTAL3 regime signal research for s524b integration.
Analyzes predictive power of altcoin market cap signals.
"""
import os
import sys
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# ---- Load data ----
t2t3 = pd.read_parquet(os.path.join(ROOT, "data/alternative/total2_total3.parquet"))
if t2t3.index.tz is not None:
    t2t3.index = t2t3.index.tz_localize(None)

total2 = t2t3["total2_close"].astype(np.float64)
total3 = t2t3["total3_close"].astype(np.float64)

# BTC daily
btc_df = pd.read_csv(os.path.join(ROOT, "data/perp/binance/1h_ohlcv/BTC_perp_1h.csv"),
                      parse_dates=["datetime"]).set_index("datetime").sort_index()
if btc_df.index.tz is not None:
    btc_df.index = btc_df.index.tz_convert(None)
btc_daily = btc_df["close"].resample("1D").last().dropna()

# Align all to common daily index
idx = total2.index.intersection(total3.index).intersection(btc_daily.index)
total2 = total2.reindex(idx)
total3 = total3.reindex(idx)
btc = btc_daily.reindex(idx)

print(f"Data range: {idx[0]} to {idx[-1]} ({len(idx)} days)")

# ---- Compute signals ----
for w in [30, 60, 90]:
    total2_ret = total2.pct_change(w)
    total3_ret = total3.pct_change(w)
    btc_ret = btc.pct_change(w)
    t2t3[f"t2_ret_{w}d"] = total2_ret
    t2t3[f"t3_ret_{w}d"] = total3_ret
    t2t3[f"btc_ret_{w}d"] = btc_ret

# SMA200
t2_sma200 = total2.rolling(200, min_periods=100).mean()
t2_above_sma200 = (total2 > t2_sma200)

t3_sma200 = total3.rolling(200, min_periods=100).mean()
t3_above_sma200 = (total3 > t3_sma200)

# BTC dominance proxy
btc_dom = btc.pct_change(30) - total2.pct_change(30)

# TOTAL3/TOTAL2 ratio
t3_t2_ratio = total3 / total2

# TOTAL2 ATH drawdown
t2_ath = total2.expanding().max()
t2_ath_dd = (total2 - t2_ath) / t2_ath

# ---- Monthly table ----
print("\n" + "="*100)
print("MONTHLY TABLE: TOTAL2/TOTAL3/BTC Returns + Regime Signals (2022-2026)")
print("="*100)

df_monthly = pd.DataFrame({
    "T2_30d_ret": total2.pct_change(30),
    "T3_30d_ret": total3.pct_change(30),
    "BTC_30d_ret": btc.pct_change(30),
    "BTC_dom": btc_dom,
    "T2>SMA200": t2_above_sma200.astype(int),
    "T2_ATH_DD": t2_ath_dd,
    "T3/T2": t3_t2_ratio,
}).resample("MS").last()

df_monthly = df_monthly["2022":"2026"]
pd.set_option("display.float_format", "{:.3f}".format)
pd.set_option("display.max_rows", 100)
print(df_monthly.to_string())

# ---- Key question: TOTAL2 < SMA200 predicts negative forward returns? ----
print("\n" + "="*100)
print("TOTAL2 < SMA200 → Forward 30d TOTAL2 Return (Predictive Power)")
print("="*100)

fwd_30d = total2.pct_change(30).shift(-30)
below_sma200 = ~t2_above_sma200

# Only use days where we have the fwd return
mask = below_sma200 & fwd_30d.notna()
below_fwd = fwd_30d[mask]
above_fwd = fwd_30d[t2_above_sma200 & fwd_30d.notna()]

print(f"\nWhen TOTAL2 < SMA200 ({mask.sum()} days):")
print(f"  Mean fwd 30d return: {below_fwd.mean():.4f} ({below_fwd.mean()*100:.1f}%)")
print(f"  Median fwd 30d return: {below_fwd.median():.4f}")
print(f"  % negative: {(below_fwd < 0).mean()*100:.1f}%")
print(f"  % < -5%: {(below_fwd < -0.05).mean()*100:.1f}%")

print(f"\nWhen TOTAL2 > SMA200 ({(t2_above_sma200 & fwd_30d.notna()).sum()} days):")
print(f"  Mean fwd 30d return: {above_fwd.mean():.4f} ({above_fwd.mean()*100:.1f}%)")
print(f"  Median fwd 30d return: {above_fwd.median():.4f}")
print(f"  % negative: {(above_fwd < 0).mean()*100:.1f}%")
print(f"  % < -5%: {(above_fwd < -0.05).mean()*100:.1f}%")

# ---- Yearly accuracy ----
print("\n--- Yearly Breakdown ---")
for year in [2022, 2023, 2024, 2025, 2026]:
    yr_mask = fwd_30d.index.year == year
    yr_below = below_sma200 & yr_mask & fwd_30d.notna()
    yr_above = t2_above_sma200 & yr_mask & fwd_30d.notna()
    if yr_below.sum() > 0:
        yr_fwd = fwd_30d[yr_below]
        print(f"{year} BELOW SMA200: {yr_below.sum()} days, mean fwd={yr_fwd.mean()*100:.1f}%, neg={((yr_fwd<0).mean()*100):.0f}%")
    else:
        print(f"{year} BELOW SMA200: 0 days")
    if yr_above.sum() > 0:
        yr_fwd = fwd_30d[yr_above]
        print(f"{year} ABOVE SMA200: {yr_above.sum()} days, mean fwd={yr_fwd.mean()*100:.1f}%, neg={((yr_fwd<0).mean()*100):.0f}%")

# ---- TOTAL2 below SMA200 periods ----
print("\n" + "="*100)
print("TOTAL2 < SMA200 Periods (continuous stretches)")
print("="*100)

below_series = below_sma200.astype(int)
transitions = below_series.diff().fillna(0)
starts = transitions[transitions == 1].index
ends = transitions[transitions == -1].index

# Handle edge cases
if below_series.iloc[0] == 1:
    starts = starts.insert(0, below_series.index[0])
if below_series.iloc[-1] == 1:
    ends = ends.append(pd.DatetimeIndex([below_series.index[-1]]))

for s, e in zip(starts, ends):
    dur = (e - s).days
    t2_ret_period = (total2.loc[e] / total2.loc[s] - 1) * 100
    print(f"  {s.strftime('%Y-%m-%d')} to {e.strftime('%Y-%m-%d')} ({dur} days): T2 return {t2_ret_period:+.1f}%")

# ---- Best regime signals ----
print("\n" + "="*100)
print("SIGNAL COMPARISON: Which best predicts alt bear/bull?")
print("="*100)

# Forward 30d return for scoring
fwd = total2.pct_change(30).shift(-30)
valid = fwd.notna()

signals = {
    "T2 < SMA200": below_sma200,
    "T2 30d ret < -15%": total2.pct_change(30) < -0.15,
    "T2 30d ret < -10%": total2.pct_change(30) < -0.10,
    "T2 ATH DD < -40%": t2_ath_dd < -0.40,
    "T2 ATH DD < -30%": t2_ath_dd < -0.30,
    "T3/T2 declining 30d": t3_t2_ratio.pct_change(30) < 0,
    "BTC dom > +5%": btc_dom > 0.05,
    "BTC dom > +10%": btc_dom > 0.10,
    "T2 < SMA200 AND T2 ret < -10%": below_sma200 & (total2.pct_change(30) < -0.10),
}

print(f"\n{'Signal':<40} {'Days':>6} {'Mean Fwd30d':>12} {'% Neg':>7} {'Quality':>8}")
print("-" * 80)
for name, sig in signals.items():
    m = sig & valid
    if m.sum() < 10:
        continue
    f = fwd[m]
    neg_pct = (f < 0).mean() * 100
    mean_ret = f.mean() * 100
    # Quality = negative mean * high neg% (for bear signals)
    quality = -mean_ret * neg_pct / 100
    print(f"{name:<40} {m.sum():>6} {mean_ret:>+11.1f}% {neg_pct:>6.0f}% {quality:>7.1f}")

# Bull signals
print(f"\n{'Bull Signal':<40} {'Days':>6} {'Mean Fwd30d':>12} {'% Pos':>7}")
print("-" * 70)
bull_signals = {
    "T2 > SMA200": t2_above_sma200,
    "T2 30d ret > +10%": total2.pct_change(30) > 0.10,
    "T2 30d ret > +15%": total2.pct_change(30) > 0.15,
    "T3/T2 rising 30d": t3_t2_ratio.pct_change(30) > 0,
    "T2 > SMA200 AND T2 ret > 0": t2_above_sma200 & (total2.pct_change(30) > 0),
}
for name, sig in bull_signals.items():
    m = sig & valid
    if m.sum() < 10:
        continue
    f = fwd[m]
    pos_pct = (f > 0).mean() * 100
    mean_ret = f.mean() * 100
    print(f"{name:<40} {m.sum():>6} {mean_ret:>+11.1f}% {pos_pct:>6.0f}%")

# ---- 2024 deep dive ----
print("\n" + "="*100)
print("2024 DEEP DIVE: When was TOTAL2 below SMA200?")
print("="*100)

mask_2024 = (total2.index >= "2024-01-01") & (total2.index < "2025-01-01")
t2_2024_below = below_sma200[mask_2024]
print(f"Days TOTAL2 < SMA200 in 2024: {t2_2024_below.sum()} / {mask_2024.sum()}")
# Monthly breakdown
for m in range(1, 13):
    mm = mask_2024 & (total2.index.month == m)
    if mm.sum() == 0:
        continue
    b = below_sma200[mm].sum()
    t = mm.sum()
    t2_ret = (total2[mm].iloc[-1] / total2[mm].iloc[0] - 1) * 100 if mm.sum() > 1 else 0
    print(f"  2024-{m:02d}: below={b}/{t} days, T2 monthly return={t2_ret:+.1f}%")

# SMA windows comparison
print("\n" + "="*100)
print("SMA WINDOW COMPARISON for short-gating signal")
print("="*100)
for window in [100, 150, 200, 250, 300]:
    sma = total2.rolling(window, min_periods=window//2).mean()
    below = total2 < sma
    m = below & valid
    if m.sum() < 10:
        continue
    f = fwd[m]
    neg_pct = (f < 0).mean() * 100
    mean_ret = f.mean() * 100
    # Count days below in 2024
    d2024 = below[(total2.index >= "2024-01-01") & (total2.index < "2025-01-01")].sum()
    print(f"  SMA{window}: {m.sum()} days below, mean fwd30d={mean_ret:+.1f}%, neg={neg_pct:.0f}%, 2024 below={d2024} days")

print("\n[DONE] Research complete.")
