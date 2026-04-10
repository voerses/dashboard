"""
Research: 3-Distance Regime Classifier for BTC
================================================
Distances (all causal):
  1. SMA350 distance:  (price - SMA350) / SMA350   (350-day ~ 50-week)
  2. ATH drawdown:     (price - running_max) / running_max
  3. Rally from 365d low: (price - rolling_365d_min) / rolling_365d_min
"""

import itertools
import numpy as np
import pandas as pd

# ── Load & resample ──────────────────────────────────────────────────────────
DATA_PATH = "/workspace/crypto_backtest/data/perp/binance/1h_ohlcv/BTC_perp_1h.csv"

df_raw = pd.read_csv(DATA_PATH, parse_dates=["datetime"])
df_raw = df_raw.set_index("datetime").sort_index()

# Resample to daily close
daily = df_raw["close"].resample("1D").last().dropna()
daily = daily.loc["2019-01-01":"2026-03-29"]  # extra runway for SMA350

# ── Compute 3 distances ─────────────────────────────────────────────────────
sma350 = daily.rolling(350).mean()
sma350_dist = (daily - sma350) / sma350

running_max = daily.expanding().max()
ath_dd = (daily - running_max) / running_max

rolling_365_min = daily.rolling(365).min()
rally_365 = (daily - rolling_365_min) / rolling_365_min

# Trim to analysis window
mask = daily.index >= "2020-01-01"
daily = daily[mask]
sma350_dist = sma350_dist[mask]
ath_dd = ath_dd[mask]
rally_365 = rally_365[mask]

# Build dataframe
data = pd.DataFrame({
    "close": daily,
    "sma350_dist": sma350_dist,
    "ath_dd": ath_dd,
    "rally_365": rally_365,
})
data = data.dropna()

print(f"Data range: {data.index[0].date()} to {data.index[-1].date()} ({len(data)} days)")
print()

# ── Regime classifier function ───────────────────────────────────────────────
def classify_regime(df, sma_bear=-0.15, ath_bear=-0.25, sma_bull=0.15, ath_bull_floor=-0.15):
    """Classify each day into BEAR / BULL / TRANSITION."""
    bear = (
        (df["sma350_dist"] < sma_bear) |
        ((df["ath_dd"] < ath_bear) & (df["sma350_dist"] < 0))
    )
    bull = (
        (df["sma350_dist"] > sma_bull) &
        (df["ath_dd"] > ath_bull_floor)
    )
    regime = pd.Series("TRANSITION", index=df.index)
    regime[bear] = "BEAR"
    regime[bull] = "BULL"
    return regime


# ── Task 1 & 2: Monthly summary ─────────────────────────────────────────────
data["regime"] = classify_regime(data)

monthly = data.resample("ME").last()

print("=" * 90)
print("MONTHLY SUMMARY (default thresholds: SMA_bear=-0.15, ATH_bear=-0.25, SMA_bull=0.15, ATH_bull=-0.15)")
print("=" * 90)
print(f"{'Month':<10} {'BTC Close':>10} {'SMA350_dist':>12} {'ATH_dd':>10} {'Rally_365d':>12} {'Regime':<12}")
print("-" * 90)
for dt, row in monthly.iterrows():
    print(f"{dt.strftime('%Y-%m'):<10} {row['close']:>10,.0f} {row['sma350_dist']:>12.3f} "
          f"{row['ath_dd']:>10.3f} {row['rally_365']:>12.3f} {row['regime']:<12}")
print()

# ── Task 4 & 5: Threshold sensitivity sweep ─────────────────────────────────
sma_bear_vals = [-0.10, -0.12, -0.15, -0.18, -0.20]
ath_bear_vals = [-0.20, -0.25, -0.30, -0.35]
sma_bull_vals = [0.10, 0.12, 0.15, 0.18, 0.20]
ath_bull_vals = [-0.10, -0.12, -0.15, -0.18, -0.20]


def compute_criteria(df, regime):
    """Compute 6 criteria percentages."""
    r = regime.copy()

    # 1. % of 2022 classified BEAR (target >80%)
    m22 = r.loc["2022"]
    pct_2022_bear = (m22 == "BEAR").mean() * 100

    # 2. % of 2023 classified BULL (target >60%)
    m23 = r.loc["2023"]
    pct_2023_bull = (m23 == "BULL").mean() * 100

    # 3. % of 2024 classified BULL (target >80%)
    m24 = r.loc["2024"]
    pct_2024_bull = (m24 == "BULL").mean() * 100

    # 4. % of 2025 Jan-Apr NOT classified BULL (target: catch distribution)
    m25_ja = r.loc["2025-01":"2025-04"]
    pct_2025_ja_notbull = (m25_ja != "BULL").mean() * 100 if len(m25_ja) > 0 else 0

    # 5. % of 2025 May-Dec classified BEAR (target >50%)
    # Note: data may only go to Mar 2026, so 2025 May-Dec should exist
    m25_md = r.loc["2025-05":"2025-12"]
    pct_2025_md_bear = (m25_md == "BEAR").mean() * 100 if len(m25_md) > 0 else 0

    # 6. % of 2026 Q1 classified BEAR (target >80%)
    m26q1 = r.loc["2026-01":"2026-03"]
    pct_2026q1_bear = (m26q1 == "BEAR").mean() * 100 if len(m26q1) > 0 else 0

    return {
        "2022_bear": pct_2022_bear,
        "2023_bull": pct_2023_bull,
        "2024_bull": pct_2024_bull,
        "2025_ja_notbull": pct_2025_ja_notbull,
        "2025_md_bear": pct_2025_md_bear,
        "2026q1_bear": pct_2026q1_bear,
    }


print("=" * 90)
print("THRESHOLD SENSITIVITY SWEEP")
print("=" * 90)

results = []
for sb, ab, sbl, abf in itertools.product(sma_bear_vals, ath_bear_vals, sma_bull_vals, ath_bull_vals):
    regime = classify_regime(data, sma_bear=sb, ath_bear=ab, sma_bull=sbl, ath_bull_floor=abf)
    crit = compute_criteria(data, regime)
    composite = sum(crit.values())
    results.append({
        "sma_bear": sb, "ath_bear": ab, "sma_bull": sbl, "ath_bull_floor": abf,
        **crit, "composite": composite,
    })

results_df = pd.DataFrame(results).sort_values("composite", ascending=False).reset_index(drop=True)

# ── Task 6: Top 10 combinations ─────────────────────────────────────────────
print("\nTOP 10 THRESHOLD COMBINATIONS (by composite score = sum of 6 criteria %)")
print("-" * 130)
print(f"{'Rank':<5} {'SMA_bear':>9} {'ATH_bear':>9} {'SMA_bull':>9} {'ATH_bull':>9} | "
      f"{'2022_B%':>8} {'2023_U%':>8} {'2024_U%':>8} {'25JA_!U%':>9} {'25MD_B%':>8} {'26Q1_B%':>8} | {'Score':>7}")
print("-" * 130)
for i, row in results_df.head(10).iterrows():
    print(f"{i+1:<5} {row['sma_bear']:>9.2f} {row['ath_bear']:>9.2f} {row['sma_bull']:>9.2f} "
          f"{row['ath_bull_floor']:>9.2f} | "
          f"{row['2022_bear']:>8.1f} {row['2023_bull']:>8.1f} {row['2024_bull']:>8.1f} "
          f"{row['2025_ja_notbull']:>9.1f} {row['2025_md_bear']:>8.1f} {row['2026q1_bear']:>8.1f} | "
          f"{row['composite']:>7.1f}")
print()

# ── Task 7: Monthly regime timeline for best combination ────────────────────
best = results_df.iloc[0]
print("=" * 90)
print(f"BEST THRESHOLDS: SMA_bear={best['sma_bear']:.2f}, ATH_bear={best['ath_bear']:.2f}, "
      f"SMA_bull={best['sma_bull']:.2f}, ATH_bull_floor={best['ath_bull_floor']:.2f}")
print(f"Composite score: {best['composite']:.1f}")
print("=" * 90)

best_regime = classify_regime(
    data,
    sma_bear=best["sma_bear"],
    ath_bear=best["ath_bear"],
    sma_bull=best["sma_bull"],
    ath_bull_floor=best["ath_bull_floor"],
)

# Monthly regime timeline
data["best_regime"] = best_regime
monthly_regime = data["best_regime"].resample("ME").apply(
    lambda x: x.value_counts().idxmax() if len(x) > 0 else "?"
)

regime_map = {"BEAR": "B", "TRANSITION": "T", "BULL": "U"}

print("\nMONTHLY REGIME TIMELINE (B=Bear, T=Transition, U=Bull):")
print("-" * 60)
for year in range(2020, 2027):
    months_in_year = monthly_regime.loc[str(year)]
    if len(months_in_year) == 0:
        continue
    letters = "".join(regime_map.get(r, "?") for r in months_in_year)
    print(f"  {year}: {letters}")
print()

# ── Task 8: Validate against strategy ground truth ──────────────────────────
print("=" * 90)
print("VALIDATION AGAINST STRATEGY GROUND TRUTH")
print("=" * 90)
print()
print("s523v annual returns: 2022:+76%, 2023:+194%, 2024:+48%, 2025:+292%, 2026Q1:+42%")
print()
print("Regime logic check:")
print("  - 2022: Ungated shorts helped -> should be BEAR")
print("  - 2023: Gated shorts helped   -> should be BULL")
print("  - 2024: Gated shorts helped   -> should be BULL")
print("  - 2025: Ungated shorts helped  -> should be BEAR (at least in distribution/bear phase)")
print("  - 2026 Q1: Ungated shorts helped -> should be BEAR")
print()

# Compute dominant regime per year
for period_label, period_slice in [
    ("2022", "2022"),
    ("2023", "2023"),
    ("2024", "2024"),
    ("2025 Jan-Apr", slice("2025-01", "2025-04")),
    ("2025 May-Dec", slice("2025-05", "2025-12")),
    ("2026 Q1", slice("2026-01", "2026-03")),
]:
    subset = best_regime.loc[period_slice] if isinstance(period_slice, slice) else best_regime.loc[period_slice]
    if len(subset) == 0:
        print(f"  {period_label}: NO DATA")
        continue
    counts = subset.value_counts()
    dominant = counts.idxmax()
    pcts = {r: f"{(subset == r).mean()*100:.0f}%" for r in ["BEAR", "TRANSITION", "BULL"]}
    print(f"  {period_label:<15}: Dominant={dominant:<12}  B={pcts.get('BEAR','0%'):>4}  T={pcts.get('TRANSITION','0%'):>4}  U={pcts.get('BULL','0%'):>4}")

print()

# ── Detailed monthly view for best thresholds ────────────────────────────────
print("=" * 90)
print("DETAILED MONTHLY VIEW (best thresholds)")
print("=" * 90)
print(f"{'Month':<10} {'BTC Close':>10} {'SMA350_dist':>12} {'ATH_dd':>10} {'Rally_365d':>12} {'Regime':<12}")
print("-" * 90)
monthly_best = data[["close", "sma350_dist", "ath_dd", "rally_365", "best_regime"]].resample("ME").last()
for dt, row in monthly_best.iterrows():
    print(f"{dt.strftime('%Y-%m'):<10} {row['close']:>10,.0f} {row['sma350_dist']:>12.3f} "
          f"{row['ath_dd']:>10.3f} {row['rally_365']:>12.3f} {row['best_regime']:<12}")

print()
print("DONE.")
