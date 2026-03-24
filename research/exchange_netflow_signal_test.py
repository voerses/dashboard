#!/usr/bin/env python3
"""
Exchange Netflow as a Crypto Return Predictor — Quantitative Research
=====================================================================
Hypothesis: Large net inflows to exchanges = selling pressure = bearish for price.
            Net outflows = accumulation = bullish.

Data sources:
  - CoinMetrics BTC/ETH exchange flow (568 days, 2024-09-01 to 2026-03-22)
  - Santiment BTC/ETH exchange flow (266 days, 2025-06-01 to 2026-02-21)
  - Blockchain.com BTC onchain (725 days, 2024-03-23 to 2026-03-17)
  - Price: Hyperliquid perp 1h OHLCV resampled to daily

Signals tested:
  1. netflow_raw          — daily net flow in native units
  2. netflow_usd          — daily net flow in USD (CoinMetrics only)
  3. netflow_zscore_20d   — z-score of netflow over 20d rolling window
  4. netflow_5d_sum       — cumulative netflow over 5 days (trend in flows)
  5. netflow_direction    — binary flag (1 = net inflow, -1 = net outflow)

Forward return horizons: 1d, 3d, 7d, 14d
Evaluation: Spearman IC, t-stat, hit rate, temporal IS/OOS split

PASS CRITERIA: IC > 0.05, t-stat > 2.0, sign consistent IS -> OOS
"""

import os
import warnings
import numpy as np
import pandas as pd
from scipy import stats
from datetime import datetime

warnings.filterwarnings("ignore")

# ============================================================================
# CONFIGURATION
# ============================================================================

NETFLOW_DIR = "/workspace/crypto_backtest/data/alternative/exchange_netflow"
PRICE_DIR = "/workspace/crypto_backtest/data/perp/1h_cache"
OUTPUT_DIR = "/workspace/crypto_backtest/research"

FWD_HORIZONS = {"1d": 1, "3d": 3, "7d": 7, "14d": 14}  # in trading days

# Rolling window for z-score
ZSCORE_WINDOW = 20  # days
# Cumulative netflow window
CUM_WINDOW = 5  # days

# IS/OOS split: first 70% IS, last 30% OOS
IS_FRACTION = 0.70

# Minimum warm-up for expanding walk-forward
MIN_WARMUP = 90  # days


# ============================================================================
# DATA LOADING
# ============================================================================

def load_daily_price(asset: str) -> pd.DataFrame:
    """Load hourly price data and resample to daily OHLCV."""
    path = os.path.join(PRICE_DIR, f"{asset}_1h.parquet")
    df = pd.read_parquet(path)
    df.index = pd.to_datetime(df.index)
    df = df.sort_index()

    daily = df.resample("1D").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).dropna(subset=["close"])

    # Compute forward returns
    for label, days in FWD_HORIZONS.items():
        daily[f"fwd_ret_{label}"] = daily["close"].pct_change(days).shift(-days)

    return daily


def load_coinmetrics(asset: str) -> pd.DataFrame:
    """Load CoinMetrics exchange flow data."""
    fname = f"coinmetrics_{asset.lower()}_exchange_flow.csv"
    path = os.path.join(NETFLOW_DIR, fname)
    df = pd.read_csv(path)
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()

    out = pd.DataFrame(index=df.index)
    out["netflow_ntv"] = df["NetFlowNtv"].astype(float)
    out["netflow_usd"] = df["NetFlowUSD"].astype(float)
    out["inflow_ntv"] = df["FlowInExNtv"].astype(float)
    out["outflow_ntv"] = df["FlowOutExNtv"].astype(float)
    out["source"] = "coinmetrics"
    return out


def load_santiment(asset: str) -> pd.DataFrame:
    """Load Santiment exchange flow data."""
    fname = f"santiment_{asset.lower()}_exchange_flow.csv"
    path = os.path.join(NETFLOW_DIR, fname)
    df = pd.read_csv(path)
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()

    out = pd.DataFrame(index=df.index)
    out["netflow_ntv"] = df["exchange_netflow"].astype(float)
    out["netflow_usd"] = np.nan  # Santiment data is in native units only
    out["inflow_ntv"] = df["exchange_inflow"].astype(float) if "exchange_inflow" in df.columns else np.nan
    out["outflow_ntv"] = df["exchange_outflow"].astype(float) if "exchange_outflow" in df.columns else np.nan
    out["source"] = "santiment"
    return out


# ============================================================================
# SIGNAL CONSTRUCTION
# ============================================================================

def build_signals(flow_df: pd.DataFrame, use_usd: bool = True) -> pd.DataFrame:
    """
    Compute 5 signal variants from exchange netflow data.

    Hypothesis: positive netflow (inflow > outflow) is bearish.
    We NEGATE signals so that positive signal = bullish expectation.
    This way a positive IC means the signal works as expected.
    """
    signals = pd.DataFrame(index=flow_df.index)

    # 1. Raw netflow (negated: outflow = bullish)
    signals["netflow_raw"] = -flow_df["netflow_ntv"]

    # 2. USD netflow (negated)
    if use_usd and "netflow_usd" in flow_df.columns and flow_df["netflow_usd"].notna().sum() > 10:
        signals["netflow_usd"] = -flow_df["netflow_usd"]
    else:
        signals["netflow_usd"] = np.nan

    # 3. Z-score of netflow over 20d rolling window (negated)
    roll_mean = flow_df["netflow_ntv"].rolling(ZSCORE_WINDOW, min_periods=ZSCORE_WINDOW).mean()
    roll_std = flow_df["netflow_ntv"].rolling(ZSCORE_WINDOW, min_periods=ZSCORE_WINDOW).std()
    raw_zscore = (flow_df["netflow_ntv"] - roll_mean) / roll_std
    signals["netflow_zscore_20d"] = -raw_zscore

    # 4. Cumulative netflow over 5 days (negated)
    signals["netflow_5d_sum"] = -flow_df["netflow_ntv"].rolling(CUM_WINDOW, min_periods=CUM_WINDOW).sum()

    # 5. Direction: binary flag (negated: outflow day = +1, inflow day = -1)
    signals["netflow_direction"] = -np.sign(flow_df["netflow_ntv"])

    return signals


# ============================================================================
# IC COMPUTATION
# ============================================================================

def compute_spearman_ic(signal: pd.Series, returns: pd.Series) -> dict:
    """Compute Spearman rank correlation (IC) between signal and forward returns."""
    aligned = pd.DataFrame({"signal": signal, "returns": returns}).dropna()
    n = len(aligned)
    if n < 30:
        return {"ic": np.nan, "pval": np.nan, "tstat": np.nan, "n": n}

    rho, pval = stats.spearmanr(aligned["signal"], aligned["returns"])
    # t-stat: t = rho * sqrt((n-2) / (1 - rho^2))
    tstat = rho * np.sqrt((n - 2) / (1 - rho**2 + 1e-12))
    return {"ic": rho, "pval": pval, "tstat": tstat, "n": n}


def compute_rolling_ic(signal: pd.Series, returns: pd.Series, window: int = 60) -> pd.Series:
    """Compute rolling Spearman IC with given window."""
    aligned = pd.DataFrame({"signal": signal, "returns": returns}).dropna()
    ic_series = aligned["signal"].rolling(window, min_periods=window).corr(aligned["returns"], method="spearman")
    return ic_series


def compute_hit_rate(signal: pd.Series, returns: pd.Series) -> float:
    """Fraction of days where signal direction matches return direction."""
    aligned = pd.DataFrame({"signal": signal, "returns": returns}).dropna()
    if len(aligned) < 10:
        return np.nan
    hits = (np.sign(aligned["signal"]) == np.sign(aligned["returns"])).sum()
    return hits / len(aligned)


# ============================================================================
# TEMPORAL SPLIT ANALYSIS
# ============================================================================

def temporal_split_analysis(signal: pd.Series, returns: pd.Series) -> dict:
    """
    Compute IC on IS (first 70%) and OOS (last 30%).
    If data < 6 months, use expanding walk-forward with 90d warm-up instead.
    """
    aligned = pd.DataFrame({"signal": signal, "returns": returns}).dropna()
    n = len(aligned)

    if n < 60:
        return {
            "method": "insufficient_data",
            "is_ic": np.nan, "is_tstat": np.nan,
            "oos_ic": np.nan, "oos_tstat": np.nan,
            "sign_consistent": None, "n_is": 0, "n_oos": 0,
        }

    if n >= 180:  # >= ~6 months of daily data
        split_idx = int(n * IS_FRACTION)
        is_data = aligned.iloc[:split_idx]
        oos_data = aligned.iloc[split_idx:]

        is_result = compute_spearman_ic(is_data["signal"], is_data["returns"])
        oos_result = compute_spearman_ic(oos_data["signal"], oos_data["returns"])

        sign_consistent = (
            np.sign(is_result["ic"]) == np.sign(oos_result["ic"])
            if not (np.isnan(is_result["ic"]) or np.isnan(oos_result["ic"]))
            else None
        )

        return {
            "method": "temporal_split_70_30",
            "is_ic": is_result["ic"],
            "is_tstat": is_result["tstat"],
            "oos_ic": oos_result["ic"],
            "oos_tstat": oos_result["tstat"],
            "sign_consistent": sign_consistent,
            "n_is": is_result["n"],
            "n_oos": oos_result["n"],
        }
    else:
        # Expanding walk-forward with 90d warm-up
        ics = []
        for i in range(MIN_WARMUP, n):
            window_data = aligned.iloc[:i+1]
            if len(window_data) >= MIN_WARMUP:
                r = compute_spearman_ic(window_data["signal"], window_data["returns"])
                ics.append(r["ic"])

        if len(ics) < 10:
            return {
                "method": "walk_forward_insufficient",
                "is_ic": np.nan, "is_tstat": np.nan,
                "oos_ic": np.nan, "oos_tstat": np.nan,
                "sign_consistent": None, "n_is": 0, "n_oos": 0,
            }

        # First half vs second half of walk-forward ICs
        mid = len(ics) // 2
        early_mean = np.nanmean(ics[:mid])
        late_mean = np.nanmean(ics[mid:])
        sign_consistent = np.sign(early_mean) == np.sign(late_mean) if not (np.isnan(early_mean) or np.isnan(late_mean)) else None

        return {
            "method": "expanding_walk_forward_90d",
            "is_ic": early_mean,
            "is_tstat": np.nan,  # not well-defined for walk-forward mean
            "oos_ic": late_mean,
            "oos_tstat": np.nan,
            "sign_consistent": sign_consistent,
            "n_is": mid,
            "n_oos": len(ics) - mid,
        }


# ============================================================================
# MULTI-SOURCE ENSEMBLE
# ============================================================================

def build_ensemble_signal(coinmetrics_sig: pd.Series, santiment_sig: pd.Series) -> pd.Series:
    """
    Build ensemble signal from two sources by averaging z-scored signals.
    On days where only one source available, use that source alone.
    """
    # z-score each source independently
    cm_z = (coinmetrics_sig - coinmetrics_sig.expanding(20).mean()) / coinmetrics_sig.expanding(20).std()
    san_z = (santiment_sig - santiment_sig.expanding(20).mean()) / santiment_sig.expanding(20).std()

    ensemble = pd.DataFrame({"cm": cm_z, "san": san_z})
    return ensemble.mean(axis=1, skipna=True)


# ============================================================================
# MAIN ANALYSIS
# ============================================================================

def run_analysis():
    print("=" * 80)
    print("EXCHANGE NETFLOW SIGNAL ANALYSIS")
    print(f"Run date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)

    all_results = []

    for asset in ["BTC", "ETH"]:
        print(f"\n{'=' * 60}")
        print(f"  ASSET: {asset}")
        print(f"{'=' * 60}")

        # Load price data
        price = load_daily_price(asset)
        print(f"  Price data: {price.index[0].date()} to {price.index[-1].date()} ({len(price)} days)")

        # Load flow data from multiple sources
        sources = {}

        # CoinMetrics
        try:
            cm = load_coinmetrics(asset)
            sources["coinmetrics"] = cm
            print(f"  CoinMetrics: {cm.index[0].date()} to {cm.index[-1].date()} ({len(cm)} days)")
        except Exception as e:
            print(f"  CoinMetrics: FAILED - {e}")

        # Santiment
        try:
            san = load_santiment(asset)
            sources["santiment"] = san
            print(f"  Santiment:   {san.index[0].date()} to {san.index[-1].date()} ({len(san)} days)")
        except Exception as e:
            print(f"  Santiment:   FAILED - {e}")

        # Process each source
        for src_name, flow_df in sources.items():
            print(f"\n  --- Source: {src_name} ---")
            use_usd = (src_name == "coinmetrics")
            signals = build_signals(flow_df, use_usd=use_usd)

            # Align signals with price returns
            # Ensure indices match
            combined = signals.join(price[[c for c in price.columns if c.startswith("fwd_ret_")]], how="inner")
            print(f"  Aligned observations: {len(combined)}")

            if len(combined) < 30:
                print("  SKIP: insufficient aligned data")
                continue

            # Compute IC for each signal x horizon
            signal_cols = [c for c in signals.columns if not c.startswith("fwd_")]
            signal_cols = [c for c in signal_cols if combined[c].notna().sum() > 30]

            for sig_name in signal_cols:
                for hz_label in FWD_HORIZONS:
                    ret_col = f"fwd_ret_{hz_label}"
                    if ret_col not in combined.columns:
                        continue

                    sig = combined[sig_name]
                    ret = combined[ret_col]

                    # Full-sample IC
                    ic_result = compute_spearman_ic(sig, ret)

                    # Hit rate
                    hr = compute_hit_rate(sig, ret)

                    # Temporal split
                    split = temporal_split_analysis(sig, ret)

                    row = {
                        "asset": asset,
                        "source": src_name,
                        "signal": sig_name,
                        "horizon": hz_label,
                        "ic": ic_result["ic"],
                        "tstat": ic_result["tstat"],
                        "pval": ic_result["pval"],
                        "n": ic_result["n"],
                        "hit_rate": hr,
                        "split_method": split["method"],
                        "is_ic": split["is_ic"],
                        "is_tstat": split["is_tstat"],
                        "oos_ic": split["oos_ic"],
                        "oos_tstat": split["oos_tstat"],
                        "sign_consistent": split["sign_consistent"],
                        "n_is": split["n_is"],
                        "n_oos": split["n_oos"],
                    }
                    all_results.append(row)

        # Ensemble signal (if both sources available)
        if "coinmetrics" in sources and "santiment" in sources:
            print(f"\n  --- Ensemble: coinmetrics + santiment ---")
            cm_sigs = build_signals(sources["coinmetrics"], use_usd=False)
            san_sigs = build_signals(sources["santiment"], use_usd=False)

            for sig_name in ["netflow_raw", "netflow_zscore_20d", "netflow_5d_sum"]:
                if sig_name in cm_sigs.columns and sig_name in san_sigs.columns:
                    ens = build_ensemble_signal(cm_sigs[sig_name], san_sigs[sig_name])
                    ens_name = f"ensemble_{sig_name}"

                    combined_ens = pd.DataFrame({ens_name: ens})
                    combined_ens = combined_ens.join(price[[c for c in price.columns if c.startswith("fwd_ret_")]], how="inner")

                    if len(combined_ens.dropna(subset=[ens_name])) < 30:
                        continue

                    for hz_label in FWD_HORIZONS:
                        ret_col = f"fwd_ret_{hz_label}"
                        if ret_col not in combined_ens.columns:
                            continue

                        sig = combined_ens[ens_name]
                        ret = combined_ens[ret_col]

                        ic_result = compute_spearman_ic(sig, ret)
                        hr = compute_hit_rate(sig, ret)
                        split = temporal_split_analysis(sig, ret)

                        row = {
                            "asset": asset,
                            "source": "ensemble",
                            "signal": ens_name,
                            "horizon": hz_label,
                            "ic": ic_result["ic"],
                            "tstat": ic_result["tstat"],
                            "pval": ic_result["pval"],
                            "n": ic_result["n"],
                            "hit_rate": hr,
                            "split_method": split["method"],
                            "is_ic": split["is_ic"],
                            "is_tstat": split["is_tstat"],
                            "oos_ic": split["oos_ic"],
                            "oos_tstat": split["oos_tstat"],
                            "sign_consistent": split["sign_consistent"],
                            "n_is": split["n_is"],
                            "n_oos": split["n_oos"],
                        }
                        all_results.append(row)

    # ========================================================================
    # RESULTS
    # ========================================================================
    results_df = pd.DataFrame(all_results)

    print("\n\n" + "=" * 80)
    print("FULL-SAMPLE IC RESULTS (Spearman rank correlation)")
    print("=" * 80)
    print(f"{'Asset':<5} {'Source':<12} {'Signal':<25} {'Horizon':<8} {'IC':>8} {'t-stat':>8} {'p-val':>8} {'N':>5} {'HitRate':>8}")
    print("-" * 95)

    for _, r in results_df.sort_values(["asset", "source", "signal", "horizon"]).iterrows():
        ic_str = f"{r['ic']:>8.4f}" if not np.isnan(r['ic']) else "     NaN"
        ts_str = f"{r['tstat']:>8.2f}" if not np.isnan(r['tstat']) else "     NaN"
        pv_str = f"{r['pval']:>8.4f}" if not np.isnan(r['pval']) else "     NaN"
        hr_str = f"{r['hit_rate']:>8.1%}" if not np.isnan(r['hit_rate']) else "     NaN"
        print(f"{r['asset']:<5} {r['source']:<12} {r['signal']:<25} {r['horizon']:<8} {ic_str} {ts_str} {pv_str} {r['n']:>5} {hr_str}")

    print("\n\n" + "=" * 80)
    print("IS / OOS SPLIT RESULTS")
    print("=" * 80)
    print(f"{'Asset':<5} {'Source':<12} {'Signal':<25} {'Horizon':<8} {'IS IC':>8} {'OOS IC':>8} {'Sign?':>6} {'Method':<30}")
    print("-" * 110)

    for _, r in results_df.sort_values(["asset", "source", "signal", "horizon"]).iterrows():
        is_str = f"{r['is_ic']:>8.4f}" if not np.isnan(r['is_ic']) else "     NaN"
        oos_str = f"{r['oos_ic']:>8.4f}" if not np.isnan(r['oos_ic']) else "     NaN"
        sign_str = "  YES" if r['sign_consistent'] == True else ("   NO" if r['sign_consistent'] == False else "    -")
        print(f"{r['asset']:<5} {r['source']:<12} {r['signal']:<25} {r['horizon']:<8} {is_str} {oos_str} {sign_str} {r['split_method']:<30}")

    # ========================================================================
    # PASS/FAIL EVALUATION
    # ========================================================================
    print("\n\n" + "=" * 80)
    print("PASS/FAIL EVALUATION (IC > 0.05, |t-stat| > 2.0, sign consistent IS->OOS)")
    print("=" * 80)

    pass_candidates = results_df[
        (results_df["ic"].abs() > 0.05) &
        (results_df["tstat"].abs() > 2.0) &
        (results_df["sign_consistent"] == True)
    ]

    if len(pass_candidates) > 0:
        print("\nPASSING SIGNALS:")
        print(f"{'Asset':<5} {'Source':<12} {'Signal':<25} {'Horizon':<8} {'IC':>8} {'t-stat':>8} {'IS IC':>8} {'OOS IC':>8}")
        print("-" * 90)
        for _, r in pass_candidates.sort_values("ic", key=abs, ascending=False).iterrows():
            print(f"{r['asset']:<5} {r['source']:<12} {r['signal']:<25} {r['horizon']:<8} {r['ic']:>8.4f} {r['tstat']:>8.2f} {r['is_ic']:>8.4f} {r['oos_ic']:>8.4f}")
    else:
        print("\n  NO signals meet all three pass criteria.")

    # Also report near-misses
    near_miss = results_df[
        (results_df["ic"].abs() > 0.03) &
        (results_df["tstat"].abs() > 1.5) &
        ~results_df.index.isin(pass_candidates.index)
    ].sort_values("ic", key=abs, ascending=False)

    if len(near_miss) > 0:
        print(f"\nNEAR-MISS SIGNALS (|IC| > 0.03, |t-stat| > 1.5):")
        print(f"{'Asset':<5} {'Source':<12} {'Signal':<25} {'Horizon':<8} {'IC':>8} {'t-stat':>8} {'Sign?':>6}")
        print("-" * 85)
        for _, r in near_miss.head(15).iterrows():
            sign_str = "YES" if r['sign_consistent'] == True else ("NO" if r['sign_consistent'] == False else "-")
            print(f"{r['asset']:<5} {r['source']:<12} {r['signal']:<25} {r['horizon']:<8} {r['ic']:>8.4f} {r['tstat']:>8.2f} {sign_str:>6}")

    # ========================================================================
    # SUMMARY STATISTICS
    # ========================================================================
    print("\n\n" + "=" * 80)
    print("SUMMARY STATISTICS BY SIGNAL")
    print("=" * 80)

    for signal_name in results_df["signal"].unique():
        subset = results_df[results_df["signal"] == signal_name]
        mean_ic = subset["ic"].mean()
        median_ic = subset["ic"].median()
        best_ic = subset.loc[subset["ic"].abs().idxmax()]
        pct_positive = (subset["ic"] > 0).mean()
        print(f"\n  {signal_name}:")
        print(f"    Mean IC: {mean_ic:.4f}  |  Median IC: {median_ic:.4f}  |  % positive: {pct_positive:.0%}")
        print(f"    Best: {best_ic['asset']}/{best_ic['source']}/{best_ic['horizon']} IC={best_ic['ic']:.4f} t={best_ic['tstat']:.2f}")

    # ========================================================================
    # DECAY ANALYSIS
    # ========================================================================
    print("\n\n" + "=" * 80)
    print("IC DECAY ANALYSIS (how IC varies across horizons)")
    print("=" * 80)

    for asset in results_df["asset"].unique():
        for source in results_df[results_df["asset"] == asset]["source"].unique():
            subset = results_df[(results_df["asset"] == asset) & (results_df["source"] == source)]
            print(f"\n  {asset} / {source}:")
            for sig in subset["signal"].unique():
                sig_data = subset[subset["signal"] == sig].sort_values("horizon", key=lambda x: x.map({"1d": 1, "3d": 3, "7d": 7, "14d": 14}))
                ics = [f"{row['horizon']}={row['ic']:.4f}" for _, row in sig_data.iterrows() if not np.isnan(row['ic'])]
                if ics:
                    print(f"    {sig:<25}: {' | '.join(ics)}")

    # ========================================================================
    # GENERATE MARKDOWN REPORT
    # ========================================================================
    generate_report(results_df, pass_candidates, near_miss)

    return results_df


def generate_report(results_df: pd.DataFrame, pass_candidates: pd.DataFrame, near_miss: pd.DataFrame):
    """Generate markdown research report."""
    report_path = os.path.join(OUTPUT_DIR, "exchange_netflow_signal_results.md")

    lines = []
    lines.append("# Exchange Netflow Signal Analysis Results")
    lines.append(f"\n**Run date:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"\n**Script:** `research/exchange_netflow_signal_test.py`")

    lines.append("\n## Hypothesis")
    lines.append("Large net inflows to exchanges = selling pressure = bearish for price.")
    lines.append("Net outflows = accumulation = bullish for price.")
    lines.append("\nSignals are **negated** so that positive signal = bullish (outflow-dominated).")
    lines.append("A positive IC confirms the hypothesis.")

    lines.append("\n## Data Sources")
    lines.append("| Source | Assets | Date Range | Obs |")
    lines.append("|--------|--------|------------|-----|")
    for source in results_df["source"].unique():
        subset = results_df[results_df["source"] == source]
        assets = ", ".join(sorted(subset["asset"].unique()))
        n_range = f"{subset['n'].min()}-{subset['n'].max()}"
        lines.append(f"| {source} | {assets} | see below | {n_range} |")

    lines.append("\n## Signal Definitions")
    lines.append("| Signal | Description |")
    lines.append("|--------|-------------|")
    lines.append("| `netflow_raw` | -(inflow - outflow) in native units |")
    lines.append("| `netflow_usd` | -(inflow - outflow) in USD (CoinMetrics only) |")
    lines.append("| `netflow_zscore_20d` | -z-score of netflow over 20d rolling window |")
    lines.append("| `netflow_5d_sum` | -cumulative netflow over 5 days |")
    lines.append("| `netflow_direction` | -sign(netflow): +1 if outflow day, -1 if inflow day |")
    lines.append("| `ensemble_*` | Average z-scored signal across CoinMetrics + Santiment |")

    lines.append("\n## Pass Criteria")
    lines.append("- |IC| > 0.05")
    lines.append("- |t-stat| > 2.0")
    lines.append("- Sign consistent IS -> OOS")

    # Full-sample IC table
    lines.append("\n## Full-Sample IC Results")
    lines.append("\n| Asset | Source | Signal | Horizon | IC | t-stat | p-value | N | Hit Rate |")
    lines.append("|-------|--------|--------|---------|---:|-------:|--------:|--:|---------:|")

    for _, r in results_df.sort_values(["asset", "source", "signal", "horizon"]).iterrows():
        ic_s = f"{r['ic']:.4f}" if not np.isnan(r['ic']) else "NaN"
        ts_s = f"{r['tstat']:.2f}" if not np.isnan(r['tstat']) else "NaN"
        pv_s = f"{r['pval']:.4f}" if not np.isnan(r['pval']) else "NaN"
        hr_s = f"{r['hit_rate']:.1%}" if not np.isnan(r['hit_rate']) else "NaN"

        # Bold significant results
        bold = "**" if (abs(r['ic']) > 0.05 and abs(r['tstat']) > 2.0) else ""
        lines.append(f"| {r['asset']} | {r['source']} | {r['signal']} | {r['horizon']} | {bold}{ic_s}{bold} | {bold}{ts_s}{bold} | {pv_s} | {r['n']} | {hr_s} |")

    # IS/OOS table
    lines.append("\n## IS / OOS Split Results")
    lines.append("\n| Asset | Source | Signal | Horizon | IS IC | OOS IC | Consistent | Method |")
    lines.append("|-------|--------|--------|---------|------:|-------:|:----------:|--------|")

    for _, r in results_df.sort_values(["asset", "source", "signal", "horizon"]).iterrows():
        is_s = f"{r['is_ic']:.4f}" if not np.isnan(r['is_ic']) else "NaN"
        oos_s = f"{r['oos_ic']:.4f}" if not np.isnan(r['oos_ic']) else "NaN"
        sign_s = "YES" if r['sign_consistent'] == True else ("NO" if r['sign_consistent'] == False else "-")
        lines.append(f"| {r['asset']} | {r['source']} | {r['signal']} | {r['horizon']} | {is_s} | {oos_s} | {sign_s} | {r['split_method']} |")

    # Pass/Fail
    lines.append("\n## Verdict")
    if len(pass_candidates) > 0:
        lines.append(f"\n**{len(pass_candidates)} signal(s) PASS all criteria:**\n")
        lines.append("| Asset | Source | Signal | Horizon | IC | t-stat | IS IC | OOS IC |")
        lines.append("|-------|--------|--------|---------|---:|-------:|------:|-------:|")
        for _, r in pass_candidates.sort_values("ic", key=abs, ascending=False).iterrows():
            lines.append(f"| {r['asset']} | {r['source']} | {r['signal']} | {r['horizon']} | {r['ic']:.4f} | {r['tstat']:.2f} | {r['is_ic']:.4f} | {r['oos_ic']:.4f} |")
    else:
        lines.append("\n**FAIL: No signals meet all three pass criteria (|IC| > 0.05, |t-stat| > 2.0, sign consistent IS->OOS).**")

    if len(near_miss) > 0:
        lines.append(f"\n### Near-Miss Signals (|IC| > 0.03, |t-stat| > 1.5)")
        lines.append("\n| Asset | Source | Signal | Horizon | IC | t-stat | Sign Consistent |")
        lines.append("|-------|--------|--------|---------|---:|-------:|:---------------:|")
        for _, r in near_miss.head(10).iterrows():
            sign_s = "YES" if r['sign_consistent'] == True else ("NO" if r['sign_consistent'] == False else "-")
            lines.append(f"| {r['asset']} | {r['source']} | {r['signal']} | {r['horizon']} | {r['ic']:.4f} | {r['tstat']:.2f} | {sign_s} |")

    # Summary stats
    lines.append("\n## Summary by Signal")
    for signal_name in results_df["signal"].unique():
        subset = results_df[results_df["signal"] == signal_name]
        mean_ic = subset["ic"].mean()
        median_ic = subset["ic"].median()
        pct_positive = (subset["ic"] > 0).mean()
        lines.append(f"\n**{signal_name}**: mean IC = {mean_ic:.4f}, median IC = {median_ic:.4f}, % positive IC = {pct_positive:.0%}")

    # Conclusions
    lines.append("\n## Conclusions")
    if len(pass_candidates) > 0:
        lines.append("\nExchange netflow shows statistically significant predictive power for crypto returns.")
        lines.append("The hypothesis that net outflows are bullish (accumulation) and net inflows are bearish (distribution) is supported.")
        lines.append("\nRecommended next steps:")
        lines.append("- Integrate passing signals into the multi-factor model")
        lines.append("- Test signal combination with funding rate and OI signals")
        lines.append("- Evaluate portfolio-level performance with position sizing")
    else:
        n_near = len(near_miss)
        lines.append(f"\nExchange netflow does **not** meet the strict pass criteria for reliable alpha generation.")
        if n_near > 0:
            lines.append(f"However, {n_near} near-miss signal(s) show marginal predictive power that may be useful")
            lines.append("as a secondary/confirming factor in a multi-signal framework.")
        lines.append("\nPossible explanations for weak results:")
        lines.append("- Exchange netflow is a widely followed metric -- alpha may be arbitraged away")
        lines.append("- Daily frequency may be too coarse -- intraday flow spikes could matter more")
        lines.append("- Netflow definitions vary across providers -- measurement noise reduces signal")
        lines.append("- The relationship may be regime-dependent (works in trends, not ranges)")

    with open(report_path, "w") as f:
        f.write("\n".join(lines))

    print(f"\nReport saved to: {report_path}")


if __name__ == "__main__":
    results = run_analysis()
