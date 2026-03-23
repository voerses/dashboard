#!/usr/bin/env python3
"""
Stablecoin Supply Signal Analysis
==================================
Tests whether changes in stablecoin supply (USDT, USDC, DAI, BUSD) have
predictive power for BTC and ETH returns at various forward horizons.

Methodology:
- Compute signal features from stablecoin supply data (7d/30d change rates,
  supply acceleration, USDT/USDC divergence, supply vs 90d MA)
- Align with daily BTC/ETH close prices
- Compute Information Coefficient (IC) = rank correlation between signal
  and forward returns
- Strict temporal split: signals calibrated on pre-2025 data, IC computed
  only on post-2025-01-01 data
- Flag signals with |IC| > 0.05 and t-stat > 2.0

Author: Quant research pipeline
Date: 2026-03-23
"""

import warnings
warnings.filterwarnings("ignore")

import pandas as pd
import numpy as np
from scipy import stats
from pathlib import Path

# ==============================================================================
# Configuration
# ==============================================================================

DATA_DIR = Path("/workspace/crypto_backtest/data")
STABLECOIN_DIR = DATA_DIR / "alternative" / "stablecoin_supply"
PRICE_DIR = DATA_DIR / "perp" / "1h_cache"

STABLECOINS = ["usdt", "usdc", "dai", "busd"]
ASSETS = ["BTC", "ETH"]
FORWARD_HORIZONS = [1, 3, 7, 14, 30]  # days
OOS_START = pd.Timestamp("2025-01-01", tz="UTC")
IC_THRESHOLD = 0.05
TSTAT_THRESHOLD = 2.0


# ==============================================================================
# 1. Load and aggregate stablecoin supply data
# ==============================================================================

def load_stablecoin_supply():
    """Load stablecoin supply parquets, aggregate across chains to daily totals."""
    supply_by_coin = {}

    for coin in STABLECOINS:
        path = STABLECOIN_DIR / f"{coin}_supply.parquet"
        df = pd.read_parquet(path)
        # Aggregate circulating supply across all chains per date
        daily = df.groupby("date")["circulating_usd"].sum()
        daily.index = pd.to_datetime(daily.index, utc=True)
        daily = daily.sort_index()
        daily.name = coin
        supply_by_coin[coin] = daily
        print(f"  {coin.upper()}: {daily.index.min().date()} to {daily.index.max().date()}, "
              f"{len(daily)} days, latest={daily.iloc[-1]:,.0f}")

    # Combine into a single DataFrame
    supply_df = pd.DataFrame(supply_by_coin)
    supply_df = supply_df.sort_index()

    # Forward-fill missing values (some coins start later)
    supply_df = supply_df.ffill()

    # Total supply across all stablecoins
    supply_df["total"] = supply_df[STABLECOINS].sum(axis=1)

    print(f"\n  Combined supply range: {supply_df.index.min().date()} to "
          f"{supply_df.index.max().date()}, {len(supply_df)} days")
    print(f"  Latest total supply: ${supply_df['total'].iloc[-1]:,.0f}")

    return supply_df


# ==============================================================================
# 2. Compute signal features
# ==============================================================================

def compute_signal_features(supply_df):
    """
    Compute predictive signal features from stablecoin supply data.

    Features:
    1. supply_chg_7d: 7-day total supply change rate (pct)
    2. supply_chg_30d: 30-day total supply change rate (pct)
    3. supply_accel: supply acceleration (change of 7d change rate over 7d)
    4. usdt_usdc_div: USDT vs USDC divergence (rotation signal)
       = 7d change rate of (USDT share of USDT+USDC)
    5. supply_vs_ma90: total supply vs 90-day moving average (z-score-like)
    """
    signals = pd.DataFrame(index=supply_df.index)

    total = supply_df["total"]

    # 1. 7-day supply change rate
    signals["supply_chg_7d"] = total.pct_change(7)

    # 2. 30-day supply change rate
    signals["supply_chg_30d"] = total.pct_change(30)

    # 3. Supply acceleration: change of 7d change over 7 days
    chg_7d = total.pct_change(7)
    signals["supply_accel"] = chg_7d - chg_7d.shift(7)

    # 4. USDT vs USDC divergence (rotation signal)
    #    Compute USDT's share of (USDT+USDC), then take 7d change
    usdt_share = supply_df["usdt"] / (supply_df["usdt"] + supply_df["usdc"])
    signals["usdt_usdc_div"] = usdt_share - usdt_share.shift(7)

    # 5. Supply level vs 90-day moving average (deviation)
    ma90 = total.rolling(90).mean()
    signals["supply_vs_ma90"] = (total - ma90) / ma90

    print(f"\n  Signal features computed: {list(signals.columns)}")
    print(f"  Non-null counts:\n{signals.count()}")

    return signals


# ==============================================================================
# 3. Load price data and compute daily returns
# ==============================================================================

def load_daily_prices(asset):
    """Load hourly price data and resample to daily close."""
    path = PRICE_DIR / f"{asset}_1h.parquet"
    df = pd.read_parquet(path)

    # Index is already DatetimeIndex
    # Resample to daily using last close of each day
    daily_close = df["close"].resample("1D").last().dropna()

    # Make timezone-aware to match stablecoin data
    if daily_close.index.tz is None:
        daily_close.index = daily_close.index.tz_localize("UTC")

    print(f"  {asset}: {daily_close.index.min().date()} to {daily_close.index.max().date()}, "
          f"{len(daily_close)} days")

    return daily_close


def compute_forward_returns(daily_close, horizons):
    """Compute forward returns at multiple horizons."""
    fwd_rets = pd.DataFrame(index=daily_close.index)
    for h in horizons:
        fwd_rets[f"fwd_{h}d"] = daily_close.shift(-h) / daily_close - 1
    return fwd_rets


# ==============================================================================
# 4. Compute Information Coefficient (IC)
# ==============================================================================

def compute_ic(signal_series, return_series):
    """
    Compute rank IC (Spearman correlation) between signal and forward returns.
    Returns IC value and t-statistic.
    """
    # Align and drop NaN
    combined = pd.concat([signal_series, return_series], axis=1).dropna()
    if len(combined) < 30:
        return np.nan, np.nan, len(combined)

    ic, pval = stats.spearmanr(combined.iloc[:, 0], combined.iloc[:, 1])

    # t-stat for rank correlation
    n = len(combined)
    if abs(ic) < 1.0:
        t_stat = ic * np.sqrt((n - 2) / (1 - ic**2))
    else:
        t_stat = np.inf * np.sign(ic)

    return ic, t_stat, n


def compute_rolling_ic(signal_series, return_series, window=63):
    """Compute rolling IC over a window (default ~3 months)."""
    combined = pd.concat([signal_series.rename("signal"),
                          return_series.rename("ret")], axis=1).dropna()
    if len(combined) < window:
        return pd.Series(dtype=float)

    rolling_ic = []
    dates = []
    for i in range(window, len(combined)):
        chunk = combined.iloc[i - window:i]
        ic, _ = stats.spearmanr(chunk["signal"], chunk["ret"])
        rolling_ic.append(ic)
        dates.append(combined.index[i])

    return pd.Series(rolling_ic, index=dates, name="rolling_ic")


# ==============================================================================
# 5. Out-of-sample analysis with strict temporal split
# ==============================================================================

def run_oos_analysis(signals, fwd_returns, asset_name):
    """
    Run out-of-sample IC analysis.

    - Signals are computed using lookback windows that only use past data
      (pct_change, rolling MA are inherently causal)
    - IC is measured ONLY on data after OOS_START (2025-01-01)
    - This is a strict temporal split
    """
    # Align signals and forward returns
    common_idx = signals.index.intersection(fwd_returns.index)
    signals_aligned = signals.loc[common_idx]
    fwd_aligned = fwd_returns.loc[common_idx]

    # Filter to OOS period only
    oos_mask = common_idx >= OOS_START
    signals_oos = signals_aligned[oos_mask]
    fwd_oos = fwd_aligned[oos_mask]

    print(f"\n  OOS period: {signals_oos.index.min().date()} to {signals_oos.index.max().date()}, "
          f"{len(signals_oos)} days")

    results = []
    for sig_name in signals_oos.columns:
        for horizon_col in fwd_oos.columns:
            horizon = int(horizon_col.split("_")[1].replace("d", ""))
            ic, t_stat, n = compute_ic(signals_oos[sig_name], fwd_oos[horizon_col])
            is_promising = (abs(ic) > IC_THRESHOLD and abs(t_stat) > TSTAT_THRESHOLD
                            if not np.isnan(ic) else False)
            results.append({
                "asset": asset_name,
                "signal": sig_name,
                "horizon_days": horizon,
                "ic": ic,
                "t_stat": t_stat,
                "n_obs": n,
                "promising": is_promising,
            })

    return pd.DataFrame(results)


# ==============================================================================
# 6. In-sample analysis for comparison (pre-2025)
# ==============================================================================

def run_is_analysis(signals, fwd_returns, asset_name):
    """Run in-sample IC analysis on pre-2025 data for comparison."""
    common_idx = signals.index.intersection(fwd_returns.index)
    signals_aligned = signals.loc[common_idx]
    fwd_aligned = fwd_returns.loc[common_idx]

    is_mask = common_idx < OOS_START
    signals_is = signals_aligned[is_mask]
    fwd_is = fwd_aligned[is_mask]

    print(f"  IS period: {signals_is.index.min().date()} to {signals_is.index.max().date()}, "
          f"{len(signals_is)} days")

    results = []
    for sig_name in signals_is.columns:
        for horizon_col in fwd_is.columns:
            horizon = int(horizon_col.split("_")[1].replace("d", ""))
            ic, t_stat, n = compute_ic(signals_is[sig_name], fwd_is[horizon_col])
            results.append({
                "asset": asset_name,
                "signal": sig_name,
                "horizon_days": horizon,
                "ic": ic,
                "t_stat": t_stat,
                "n_obs": n,
            })

    return pd.DataFrame(results)


# ==============================================================================
# 7. Rolling IC stability check
# ==============================================================================

def check_ic_stability(signals, fwd_returns, asset_name, promising_pairs):
    """For promising signals, compute rolling IC and check stability."""
    common_idx = signals.index.intersection(fwd_returns.index)
    signals_aligned = signals.loc[common_idx]
    fwd_aligned = fwd_returns.loc[common_idx]

    stability_results = []
    for _, row in promising_pairs.iterrows():
        sig_name = row["signal"]
        horizon = row["horizon_days"]
        horizon_col = f"fwd_{horizon}d"

        rolling = compute_rolling_ic(signals_aligned[sig_name],
                                     fwd_aligned[horizon_col], window=63)
        if len(rolling) == 0:
            continue

        # Compute stability metrics
        oos_rolling = rolling[rolling.index >= OOS_START]
        if len(oos_rolling) < 10:
            continue

        pct_positive = (oos_rolling > 0).mean() if row["ic"] > 0 else (oos_rolling < 0).mean()
        stability_results.append({
            "asset": asset_name,
            "signal": sig_name,
            "horizon_days": horizon,
            "oos_ic": row["ic"],
            "rolling_ic_mean": oos_rolling.mean(),
            "rolling_ic_std": oos_rolling.std(),
            "pct_correct_sign": pct_positive,
            "rolling_ic_min": oos_rolling.min(),
            "rolling_ic_max": oos_rolling.max(),
        })

    return pd.DataFrame(stability_results) if stability_results else pd.DataFrame()


# ==============================================================================
# Main
# ==============================================================================

def main():
    print("=" * 80)
    print("STABLECOIN SUPPLY SIGNAL ANALYSIS")
    print("=" * 80)

    # ---- Step 1: Load stablecoin supply ----
    print("\n--- Step 1: Loading stablecoin supply data ---")
    supply_df = load_stablecoin_supply()

    # ---- Step 2: Compute signals ----
    print("\n--- Step 2: Computing signal features ---")
    signals = compute_signal_features(supply_df)

    # ---- Step 3: Load prices and compute returns ----
    print("\n--- Step 3: Loading price data ---")
    all_oos_results = []
    all_is_results = []
    all_stability = []

    for asset in ASSETS:
        print(f"\n{'='*60}")
        print(f"  Processing {asset}")
        print(f"{'='*60}")

        daily_close = load_daily_prices(asset)
        fwd_returns = compute_forward_returns(daily_close, FORWARD_HORIZONS)

        # ---- Step 4 & 5: OOS IC analysis ----
        print(f"\n--- OOS IC Analysis ({asset}) ---")
        oos_results = run_oos_analysis(signals, fwd_returns, asset)
        all_oos_results.append(oos_results)

        # ---- In-sample for comparison ----
        print(f"\n--- IS IC Analysis ({asset}) ---")
        is_results = run_is_analysis(signals, fwd_returns, asset)
        all_is_results.append(is_results)

        # ---- Step 7: Rolling IC stability for promising signals ----
        promising = oos_results[oos_results["promising"]]
        if len(promising) > 0:
            print(f"\n--- Rolling IC Stability ({asset}) ---")
            stability = check_ic_stability(signals, fwd_returns, asset, promising)
            if len(stability) > 0:
                all_stability.append(stability)

    # ---- Combine results ----
    oos_df = pd.concat(all_oos_results, ignore_index=True)
    is_df = pd.concat(all_is_results, ignore_index=True)
    stability_df = (pd.concat(all_stability, ignore_index=True)
                    if all_stability else pd.DataFrame())

    # ==============================================================================
    # Results Output
    # ==============================================================================

    print("\n" + "=" * 80)
    print("RESULTS: OUT-OF-SAMPLE IC (post 2025-01-01)")
    print("=" * 80)

    # Pivot table: signal x horizon for each asset
    for asset in ASSETS:
        print(f"\n--- {asset} ---")
        asset_oos = oos_df[oos_df["asset"] == asset]
        pivot_ic = asset_oos.pivot_table(
            index="signal", columns="horizon_days", values="ic"
        )
        pivot_tstat = asset_oos.pivot_table(
            index="signal", columns="horizon_days", values="t_stat"
        )

        # Format: IC (t-stat) with significance stars
        formatted = pivot_ic.astype(object).copy()
        for col in formatted.columns:
            for idx in formatted.index:
                ic_val = pivot_ic.loc[idx, col]
                ts_val = pivot_tstat.loc[idx, col]
                if abs(ic_val) > IC_THRESHOLD and abs(ts_val) > TSTAT_THRESHOLD:
                    star = " ***"
                elif abs(ic_val) > IC_THRESHOLD and abs(ts_val) > 1.65:
                    star = " **"
                elif abs(ts_val) > 1.65:
                    star = " *"
                else:
                    star = ""
                formatted.loc[idx, col] = f"{ic_val:+.4f} ({ts_val:+.2f}){star}"

        print(formatted.to_string())

    # ---- In-sample comparison ----
    print("\n" + "=" * 80)
    print("RESULTS: IN-SAMPLE IC (pre 2025-01-01) — for comparison only")
    print("=" * 80)

    for asset in ASSETS:
        print(f"\n--- {asset} ---")
        asset_is = is_df[is_df["asset"] == asset]
        pivot_ic = asset_is.pivot_table(
            index="signal", columns="horizon_days", values="ic"
        )
        pivot_tstat = asset_is.pivot_table(
            index="signal", columns="horizon_days", values="t_stat"
        )

        formatted = pivot_ic.astype(object).copy()
        for col in formatted.columns:
            for idx in formatted.index:
                ic_val = pivot_ic.loc[idx, col]
                ts_val = pivot_tstat.loc[idx, col]
                formatted.loc[idx, col] = f"{ic_val:+.4f} ({ts_val:+.2f})"

        print(formatted.to_string())

    # ---- Promising signals ----
    promising_all = oos_df[oos_df["promising"]].sort_values("ic", key=abs, ascending=False)

    print("\n" + "=" * 80)
    print("PROMISING SIGNALS (|IC| > 0.05, |t-stat| > 2.0)")
    print("=" * 80)

    if len(promising_all) > 0:
        print(promising_all.to_string(index=False))
    else:
        print("  No signals meet the threshold criteria.")

    # ---- Stability analysis ----
    if len(stability_df) > 0:
        print("\n" + "=" * 80)
        print("ROLLING IC STABILITY (63-day window, OOS period)")
        print("=" * 80)
        print(stability_df.to_string(index=False))

    # ---- IS vs OOS comparison for promising signals ----
    if len(promising_all) > 0:
        print("\n" + "=" * 80)
        print("IS vs OOS COMPARISON (promising signals)")
        print("=" * 80)

        for _, row in promising_all.iterrows():
            is_match = is_df[
                (is_df["asset"] == row["asset"]) &
                (is_df["signal"] == row["signal"]) &
                (is_df["horizon_days"] == row["horizon_days"])
            ]
            if len(is_match) > 0:
                is_ic = is_match.iloc[0]["ic"]
                is_ts = is_match.iloc[0]["t_stat"]
                sign_agree = "YES" if (np.sign(is_ic) == np.sign(row["ic"])) else "NO"
                print(f"  {row['asset']:4s} | {row['signal']:20s} | {row['horizon_days']:2d}d | "
                      f"IS IC={is_ic:+.4f} (t={is_ts:+.2f}) | "
                      f"OOS IC={row['ic']:+.4f} (t={row['t_stat']:+.2f}) | "
                      f"Sign agrees: {sign_agree}")

    # ==============================================================================
    # Summary
    # ==============================================================================

    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)

    n_promising = len(promising_all)
    n_total = len(oos_df)
    n_positive_ic = (oos_df["ic"].abs() > 0.03).sum()

    print(f"""
  Total signal-horizon-asset combinations tested: {n_total}
  Combinations with |IC| > 0.03: {n_positive_ic}
  Combinations meeting threshold (|IC|>0.05, |t|>2.0): {n_promising}

  Interpretation guide:
  - IC > 0 means higher stablecoin metric => higher forward returns
  - IC < 0 means higher stablecoin metric => lower forward returns
  - |IC| of 0.03-0.05 is weak but potentially useful in combination
  - |IC| of 0.05-0.10 is moderate and potentially tradeable
  - |IC| > 0.10 is strong (and rare; verify it's not data leakage)

  Key caveats:
  - Stablecoin supply data is daily; intraday signals are not captured
  - BUSD was deprecated in 2023; its contribution to total supply is minimal after that
  - Supply data aggregated across 100+ chains may have reporting lags
  - Short OOS window (~15 months) limits statistical power
  - Multiple testing: {n_total} tests at 5% significance => ~{int(n_total*0.05)} false positives expected
  """)

    # Bonferroni-corrected threshold
    bonferroni_alpha = 0.05 / n_total
    bonferroni_tstat = stats.norm.ppf(1 - bonferroni_alpha / 2)
    survives_bonferroni = oos_df[oos_df["t_stat"].abs() > bonferroni_tstat]

    print(f"  Bonferroni-corrected t-stat threshold: {bonferroni_tstat:.2f}")
    print(f"  Signals surviving Bonferroni correction: {len(survives_bonferroni)}")
    if len(survives_bonferroni) > 0:
        print(survives_bonferroni[["asset", "signal", "horizon_days", "ic", "t_stat"]].to_string(index=False))

    return oos_df, is_df, promising_all, stability_df


if __name__ == "__main__":
    oos_df, is_df, promising_all, stability_df = main()
