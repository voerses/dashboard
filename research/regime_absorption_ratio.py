"""
Absorption Ratio (PCA-based systemic risk indicator) for crypto markets.

AR = sum of variance explained by top N eigenvectors / total variance
Higher AR = more coupled markets = systemic risk = bear regime
"""

import os
import warnings
import numpy as np
import pandas as pd
from sklearn.decomposition import PCA

warnings.filterwarnings("ignore")

DATA_DIR = "/workspace/crypto_backtest/data/perp/binance/1h_ohlcv"

# ── 1. Load data, pick top 30 by data length (proxy for liquidity) ──────────

print("=" * 80)
print("ABSORPTION RATIO — PCA-BASED SYSTEMIC RISK INDICATOR")
print("=" * 80)

files = [f for f in os.listdir(DATA_DIR) if f.endswith("_perp_1h.csv")]
token_lengths = {}
for f in files:
    token = f.replace("_perp_1h.csv", "")
    try:
        df = pd.read_csv(os.path.join(DATA_DIR, f), usecols=["datetime", "close", "volume"])
        token_lengths[token] = (len(df), df["volume"].sum())
    except Exception:
        pass

# Sort by data length (longer = listed earlier = more established)
sorted_tokens = sorted(token_lengths.items(), key=lambda x: (-x[1][0], -x[1][1]))
top30 = [t[0] for t in sorted_tokens[:30]]
print(f"\nTop 30 tokens (by data length): {top30}")

# ── 2. Load 1h data, resample to daily close, compute log returns ───────────

daily_closes = {}
for token in top30:
    fp = os.path.join(DATA_DIR, f"{token}_perp_1h.csv")
    df = pd.read_csv(fp, parse_dates=["datetime"])
    df = df.set_index("datetime")
    daily = df["close"].resample("1D").last().dropna()
    daily_closes[token] = daily

close_df = pd.DataFrame(daily_closes)
close_df = close_df.dropna(axis=1, thresh=int(len(close_df) * 0.5))  # need 50%+ data
close_df = close_df.ffill().dropna()

print(f"Daily close matrix: {close_df.shape[0]} days x {close_df.shape[1]} tokens")
print(f"Date range: {close_df.index[0].date()} to {close_df.index[-1].date()}")

log_ret = np.log(close_df / close_df.shift(1)).dropna()
print(f"Log returns matrix: {log_ret.shape[0]} days x {log_ret.shape[1]} tokens")

# ── 3. Compute rolling Absorption Ratio ─────────────────────────────────────

def compute_rolling_ar(returns_df, window=90, n_components_frac=0.2):
    """Compute rolling absorption ratio using PCA."""
    n_assets = returns_df.shape[1]
    n_components = max(1, int(n_assets * n_components_frac))

    ar_series = pd.Series(index=returns_df.index, dtype=float)

    for i in range(window, len(returns_df)):
        chunk = returns_df.iloc[i - window:i].dropna(axis=1)
        if chunk.shape[1] < 5:
            continue
        nc = max(1, int(chunk.shape[1] * n_components_frac))
        try:
            pca = PCA(n_components=min(nc, chunk.shape[1], chunk.shape[0]))
            pca.fit(chunk.values)
            ar_series.iloc[i] = pca.explained_variance_ratio_.sum()
        except Exception:
            continue

    return ar_series.dropna()


def compute_ar_for_window(returns_df, window, label):
    """Compute AR + delta_AR for a given window."""
    ar = compute_rolling_ar(returns_df, window=window)
    ar_mean = ar.rolling(365, min_periods=180).mean()
    ar_std = ar.rolling(365, min_periods=180).std()
    delta_ar = (ar - ar_mean) / ar_std
    delta_ar = delta_ar.replace([np.inf, -np.inf], np.nan).dropna()
    return ar, delta_ar


print("\n── Computing Absorption Ratio (90-day window) ──")
ar_90, delta_ar_90 = compute_ar_for_window(log_ret, 90, "90d")
print(f"AR computed: {len(ar_90)} observations")

# ── 4. BTC daily returns for comparison ─────────────────────────────────────

btc_close = close_df["BTC"] if "BTC" in close_df.columns else close_df.iloc[:, 0]
btc_daily_ret = np.log(btc_close / btc_close.shift(1)).dropna()

# Forward 30-day BTC return
btc_fwd_30d = np.log(btc_close.shift(-30) / btc_close).dropna()

# ── 5. Monthly output ───────────────────────────────────────────────────────

print("\n" + "=" * 80)
print("MONTHLY ABSORPTION RATIO vs BTC RETURNS")
print("=" * 80)

# Resample AR and delta_AR to monthly
ar_monthly = ar_90.resample("ME").last().dropna()
delta_monthly = delta_ar_90.resample("ME").last().dropna()
btc_monthly_ret = btc_daily_ret.resample("ME").sum().dropna()

# Align
common_idx = ar_monthly.index.intersection(delta_monthly.index).intersection(btc_monthly_ret.index)
ar_m = ar_monthly.loc[common_idx]
delta_m = delta_monthly.loc[common_idx]
btc_m = btc_monthly_ret.loc[common_idx]

bear_years = [2022, 2025, 2026]

def classify_regime(d):
    if d > 1.0:
        return "STRESS"
    elif d < -1.0:
        return "CALM"
    else:
        return "normal"

print(f"\n{'Month':<12} {'AR':>6} {'dAR':>7} {'Regime':<8} {'BTC_ret':>8} {'Bear_yr':>8}")
print("-" * 60)
for dt in common_idx:
    regime = classify_regime(delta_m.loc[dt])
    bear_flag = "YES" if dt.year in bear_years else ""
    print(f"{dt.strftime('%Y-%m'):<12} {ar_m.loc[dt]:>6.3f} {delta_m.loc[dt]:>7.2f} {regime:<8} {btc_m.loc[dt]:>8.1%} {bear_flag:>8}")

# ── 6. Regime analysis: does high AR predict bear? ──────────────────────────

print("\n" + "=" * 80)
print("REGIME ANALYSIS")
print("=" * 80)

stress_months = delta_m[delta_m > 1.0]
calm_months = delta_m[delta_m < -1.0]
normal_months = delta_m[(delta_m >= -1.0) & (delta_m <= 1.0)]

for label, subset in [("STRESS (dAR > 1)", stress_months),
                       ("NORMAL (-1 < dAR < 1)", normal_months),
                       ("CALM (dAR < -1)", calm_months)]:
    idx = subset.index.intersection(btc_m.index)
    if len(idx) == 0:
        print(f"\n{label}: no months")
        continue
    ret = btc_m.loc[idx]
    bear_count = sum(1 for dt in idx if dt.year in bear_years)
    print(f"\n{label}:")
    print(f"  Months: {len(idx)}")
    print(f"  Avg BTC return: {ret.mean():.2%}")
    print(f"  Median BTC return: {ret.median():.2%}")
    print(f"  % negative months: {(ret < 0).mean():.1%}")
    print(f"  Bear year months: {bear_count}/{len(idx)} ({bear_count/len(idx):.1%})")

# ── 7. Correlation: AR vs forward 30-day BTC return ─────────────────────────

print("\n" + "=" * 80)
print("PREDICTIVE POWER: AR vs FORWARD 30-DAY BTC RETURN")
print("=" * 80)

# Daily correlation
common_daily = ar_90.index.intersection(btc_fwd_30d.index)
ar_daily_aligned = ar_90.loc[common_daily]
btc_fwd_aligned = btc_fwd_30d.loc[common_daily]
delta_daily_aligned = delta_ar_90.reindex(common_daily).dropna()

corr_ar = ar_daily_aligned.corr(btc_fwd_aligned)
print(f"\nCorrelation(AR, fwd_30d_BTC_ret): {corr_ar:.4f}")

common2 = delta_daily_aligned.index.intersection(btc_fwd_30d.index)
if len(common2) > 30:
    corr_delta = delta_daily_aligned.loc[common2].corr(btc_fwd_30d.loc[common2])
    print(f"Correlation(delta_AR, fwd_30d_BTC_ret): {corr_delta:.4f}")

# Quintile analysis
print("\nQuintile analysis (delta_AR quintiles -> avg fwd 30d BTC return):")
combined = pd.DataFrame({
    "delta_ar": delta_daily_aligned.loc[common2],
    "fwd_30d": btc_fwd_30d.loc[common2]
}).dropna()

combined["quintile"] = pd.qcut(combined["delta_ar"], 5, labels=False, duplicates="drop")
quintile_stats = combined.groupby("quintile")["fwd_30d"].agg(["mean", "median", "count"])
for q, row in quintile_stats.iterrows():
    label = "LOW AR" if q == 0 else ("HIGH AR" if q == 4 else f"Q{q+1}")
    print(f"  {label} (n={int(row['count'])}): mean={row['mean']:.3%}, median={row['median']:.3%}")

# ── 8. Test different windows ───────────────────────────────────────────────

print("\n" + "=" * 80)
print("WINDOW COMPARISON: WHICH IS MOST PREDICTIVE?")
print("=" * 80)

results = {}
for window in [60, 90, 120, 180]:
    print(f"\n--- Window: {window} days ---")
    ar_w, delta_w = compute_ar_for_window(log_ret, window, f"{window}d")

    # Correlation with fwd 30d return
    common_w = ar_w.index.intersection(btc_fwd_30d.index)
    if len(common_w) < 100:
        print(f"  Insufficient data ({len(common_w)} obs)")
        continue

    corr_ar_w = ar_w.loc[common_w].corr(btc_fwd_30d.loc[common_w])

    common_d = delta_w.index.intersection(btc_fwd_30d.index)
    corr_delta_w = delta_w.loc[common_d].corr(btc_fwd_30d.loc[common_d]) if len(common_d) > 30 else np.nan

    # Regime accuracy: high delta_AR months that are actually bear
    delta_w_monthly = delta_w.resample("ME").last().dropna()
    btc_m_w = btc_daily_ret.resample("ME").sum()
    common_m = delta_w_monthly.index.intersection(btc_m_w.index)
    stress_m = delta_w_monthly.loc[common_m][delta_w_monthly.loc[common_m] > 1.0]
    if len(stress_m) > 0:
        stress_ret = btc_m_w.loc[stress_m.index.intersection(btc_m_w.index)]
        pct_neg = (stress_ret < 0).mean()
    else:
        pct_neg = np.nan

    print(f"  Corr(AR, fwd_30d_BTC): {corr_ar_w:.4f}")
    print(f"  Corr(delta_AR, fwd_30d_BTC): {corr_delta_w:.4f}")
    print(f"  STRESS months: {len(stress_m)}, % negative BTC: {pct_neg:.1%}" if not np.isnan(pct_neg) else f"  STRESS months: {len(stress_m)}")

    results[window] = {
        "corr_ar": corr_ar_w,
        "corr_delta": corr_delta_w,
        "stress_months": len(stress_m),
        "stress_pct_neg": pct_neg,
    }

# ── 9. Summary ──────────────────────────────────────────────────────────────

print("\n" + "=" * 80)
print("SUMMARY TABLE")
print("=" * 80)
print(f"\n{'Window':<10} {'Corr(AR)':<12} {'Corr(dAR)':<12} {'Stress_N':<10} {'Stress_%neg':<12}")
print("-" * 56)
for w, r in sorted(results.items()):
    neg_str = f"{r['stress_pct_neg']:.1%}" if not np.isnan(r['stress_pct_neg']) else "N/A"
    print(f"{w:<10} {r['corr_ar']:<12.4f} {r['corr_delta']:<12.4f} {r['stress_months']:<10} {neg_str:<12}")

# Best window
if results:
    best = min(results.items(), key=lambda x: x[1]["corr_delta"] if not np.isnan(x[1]["corr_delta"]) else 0)
    print(f"\nMost predictive window (most negative corr with fwd returns): {best[0]} days")
    print(f"  Corr(delta_AR, fwd_30d_BTC) = {best[1]['corr_delta']:.4f}")

# ── 10. Year-by-year AR level ──────────────────────────────────────────────

print("\n" + "=" * 80)
print("YEAR-BY-YEAR AVERAGE AR AND BTC RETURN")
print("=" * 80)

ar_yearly = ar_90.resample("YE").mean().dropna()
btc_yearly = btc_daily_ret.resample("YE").sum().dropna()
common_yr = ar_yearly.index.intersection(btc_yearly.index)

print(f"\n{'Year':<8} {'Avg AR':>8} {'BTC ret':>10} {'Bear?':>6}")
print("-" * 35)
for dt in common_yr:
    bear = "YES" if dt.year in bear_years else ""
    print(f"{dt.year:<8} {ar_yearly.loc[dt]:>8.3f} {btc_yearly.loc[dt]:>10.1%} {bear:>6}")

print("\n" + "=" * 80)
print("DONE")
print("=" * 80)
