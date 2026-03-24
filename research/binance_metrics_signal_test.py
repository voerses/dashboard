#!/usr/bin/env python3
"""
Binance Futures Daily Metrics as Crypto Return Predictors
==========================================================
Hypothesis: Binance Futures positioning data (OI, top trader L/S, taker ratios)
            contains signal for future crypto returns.

Data source:
  - Binance daily metrics: 40,767 rows, 29 symbols, Dec 2021 -- Mar 2026
  - Columns: count_toptrader_ls_ratio, sum_toptrader_ls_ratio, count_ls_ratio,
    taker_buy_sell_ratio, sum_open_interest, sum_open_interest_value, etc.
  - Price: Hyperliquid perp 1h OHLCV resampled to daily

Signals tested:
  1. OI rate of change (5d, 10d, 20d) -- does rising OI predict returns?
  2. OI z-score (20d rolling) -- extreme OI as contrarian signal
  3. Top trader L/S ratio (raw, z-score, roc)
  4. Top trader vs general L/S divergence -- when smart money disagrees with retail
  5. OI + price divergence -- leverage buildup without price movement = cascade risk
  6. Taker buy/sell ratio extremes -- z-score of taker ratio
  7. Cross-token OI dispersion -- crowding indicator across tokens

Forward return horizons: 1d, 3d, 7d, 14d
Evaluation: Spearman IC, t-stat, hit rate, temporal IS/OOS split
OOS split: train < 2025-01-01, test >= 2025-01-01

PASS CRITERIA: |IC| > 0.05, |t-stat| > 2.0, sign consistent IS -> OOS
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

METRICS_PATH = "/workspace/crypto_backtest/data/alternative/binance_metrics/all_symbols_daily_ls.parquet"
PRICE_DIR = "/workspace/crypto_backtest/data/perp/1h_cache"
OUTPUT_DIR = "/workspace/crypto_backtest/research"

FWD_HORIZONS = {"1d": 1, "3d": 3, "7d": 7, "14d": 14}

# IS/OOS temporal split
OOS_START = pd.Timestamp("2025-01-01")

# Rolling windows
ZSCORE_WINDOW = 20
ROC_WINDOWS = [5, 10, 20]

# Symbols to exclude (no matching price data)
EXCLUDE_SYMBOLS = {"MATICUSDT", "MKRUSDT"}

# Key tokens for individual analysis
KEY_TOKENS = ["BTC", "ETH"]


# ============================================================================
# DATA LOADING
# ============================================================================

def load_daily_price(token: str) -> pd.DataFrame:
    """Load hourly price data and resample to daily OHLCV."""
    path = os.path.join(PRICE_DIR, f"{token}_1h.parquet")
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

    # Forward returns
    for label, days in FWD_HORIZONS.items():
        daily[f"fwd_ret_{label}"] = daily["close"].pct_change(days).shift(-days)

    # Price rate of change for OI-price divergence
    for w in ROC_WINDOWS:
        daily[f"price_roc_{w}d"] = daily["close"].pct_change(w)

    return daily


def load_metrics() -> pd.DataFrame:
    """Load Binance daily metrics panel."""
    df = pd.read_parquet(METRICS_PATH)
    df["date"] = pd.to_datetime(df["date"])
    df = df[~df["symbol"].isin(EXCLUDE_SYMBOLS)].copy()
    df["token"] = df["symbol"].str.replace("USDT", "", regex=False)
    df = df.sort_values(["symbol", "date"]).reset_index(drop=True)
    return df


def build_panel() -> pd.DataFrame:
    """
    Build panel: merge metrics with daily price returns for each token.
    Returns a long-format DataFrame with signals and forward returns.
    """
    metrics = load_metrics()
    all_frames = []

    for symbol in metrics["symbol"].unique():
        token = symbol.replace("USDT", "")
        price_path = os.path.join(PRICE_DIR, f"{token}_1h.parquet")
        if not os.path.exists(price_path):
            continue

        # Get metrics for this symbol
        sym_metrics = metrics[metrics["symbol"] == symbol].copy()
        sym_metrics = sym_metrics.set_index("date").sort_index()

        # Load price
        try:
            price = load_daily_price(token)
        except Exception:
            continue

        # Join on date
        merged = sym_metrics.join(
            price[[c for c in price.columns if c.startswith("fwd_ret_") or c.startswith("price_roc_")]],
            how="inner",
        )

        if len(merged) < 60:
            continue

        merged["token"] = token
        merged["symbol"] = symbol
        all_frames.append(merged)

    panel = pd.concat(all_frames, axis=0)
    panel.index.name = "date"
    return panel


# ============================================================================
# SIGNAL CONSTRUCTION
# ============================================================================

def compute_signals_per_token(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute all signals for a single token's time series.
    Input df must be sorted by date with metrics columns.
    """
    out = df.copy()

    oi = df["sum_open_interest_value"]

    # ------------------------------------------------------------------
    # 1. OI rate of change (5d, 10d, 20d)
    # ------------------------------------------------------------------
    for w in ROC_WINDOWS:
        out[f"oi_roc_{w}d"] = oi.pct_change(w)

    # ------------------------------------------------------------------
    # 2. OI z-score (20d rolling)
    # ------------------------------------------------------------------
    oi_mean = oi.rolling(ZSCORE_WINDOW, min_periods=ZSCORE_WINDOW).mean()
    oi_std = oi.rolling(ZSCORE_WINDOW, min_periods=ZSCORE_WINDOW).std()
    out["oi_zscore_20d"] = (oi - oi_mean) / oi_std

    # ------------------------------------------------------------------
    # 3. Top trader L/S ratio signals
    # ------------------------------------------------------------------
    top_ls = df["sum_toptrader_ls_ratio"]

    # Raw
    out["toptrader_ls_raw"] = top_ls

    # Z-score
    top_mean = top_ls.rolling(ZSCORE_WINDOW, min_periods=ZSCORE_WINDOW).mean()
    top_std = top_ls.rolling(ZSCORE_WINDOW, min_periods=ZSCORE_WINDOW).std()
    out["toptrader_ls_zscore"] = (top_ls - top_mean) / top_std

    # Rate of change
    out["toptrader_ls_roc_5d"] = top_ls.pct_change(5)

    # ------------------------------------------------------------------
    # 4. Top trader vs general L/S divergence
    # ------------------------------------------------------------------
    general_ls = df["count_ls_ratio"]
    top_count_ls = df["count_toptrader_ls_ratio"]

    # Difference (top trader - general) -- when top traders are more long
    out["ls_divergence_sum"] = top_ls - general_ls
    out["ls_divergence_count"] = top_count_ls - general_ls

    # Ratio divergence (top/general)
    out["ls_divergence_ratio"] = np.where(
        general_ls > 0, top_count_ls / general_ls, np.nan
    )

    # Z-scored divergence
    div = out["ls_divergence_count"]
    div_mean = div.rolling(ZSCORE_WINDOW, min_periods=ZSCORE_WINDOW).mean()
    div_std = div.rolling(ZSCORE_WINDOW, min_periods=ZSCORE_WINDOW).std()
    out["ls_divergence_zscore"] = (div - div_mean) / div_std

    # ------------------------------------------------------------------
    # 5. OI + price divergence (leverage buildup)
    # ------------------------------------------------------------------
    for w in ROC_WINDOWS:
        oi_roc = out.get(f"oi_roc_{w}d")
        price_roc = df.get(f"price_roc_{w}d")
        if oi_roc is not None and price_roc is not None:
            # OI rising but price flat = leverage buildup = cascade risk
            # Positive = OI outpacing price movement
            out[f"oi_price_div_{w}d"] = oi_roc - price_roc.abs()

    # ------------------------------------------------------------------
    # 6. Taker buy/sell ratio signals
    # ------------------------------------------------------------------
    taker = df["taker_buy_sell_ratio"]
    taker_mean = taker.rolling(ZSCORE_WINDOW, min_periods=ZSCORE_WINDOW).mean()
    taker_std = taker.rolling(ZSCORE_WINDOW, min_periods=ZSCORE_WINDOW).std()
    out["taker_ratio_zscore"] = (taker - taker_mean) / taker_std

    # Raw taker ratio centered around 1.0
    out["taker_ratio_raw"] = taker

    # Taker ratio intraday range (max - min as a measure of volatility)
    out["taker_ratio_range"] = df["taker_ratio_max"] - df["taker_ratio_min"]

    # LS ratio intraday range
    out["ls_ratio_range"] = df["count_ls_ratio_max"] - df["count_ls_ratio_min"]

    return out


def compute_cross_token_signals(panel: pd.DataFrame) -> pd.DataFrame:
    """
    Compute cross-sectional signals across all tokens for each date.
    Signal 7: Cross-token OI dispersion (std of OI z-scores).
    """
    # Compute OI z-score dispersion per date
    daily_stats = panel.groupby(panel.index).agg(
        oi_zscore_mean=("oi_zscore_20d", "mean"),
        oi_zscore_std=("oi_zscore_20d", "std"),
        oi_zscore_count=("oi_zscore_20d", "count"),
        toptrader_ls_zscore_std=("toptrader_ls_zscore", "std"),
        taker_zscore_std=("taker_ratio_zscore", "std"),
    )

    # Cross-token OI dispersion
    panel = panel.join(daily_stats[["oi_zscore_std", "toptrader_ls_zscore_std", "taker_zscore_std"]],
                       rsuffix="_cross")

    panel.rename(columns={
        "oi_zscore_std": "oi_dispersion",
        "toptrader_ls_zscore_std": "toptrader_ls_dispersion",
        "taker_zscore_std": "taker_dispersion",
    }, inplace=True)

    return panel


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
    tstat = rho * np.sqrt((n - 2) / (1 - rho ** 2 + 1e-12))
    return {"ic": rho, "pval": pval, "tstat": tstat, "n": n}


def compute_hit_rate(signal: pd.Series, returns: pd.Series) -> float:
    """Fraction of observations where signal sign matches return sign."""
    aligned = pd.DataFrame({"signal": signal, "returns": returns}).dropna()
    if len(aligned) < 10:
        return np.nan
    hits = (np.sign(aligned["signal"]) == np.sign(aligned["returns"])).sum()
    return hits / len(aligned)


# ============================================================================
# SIGNAL LIST
# ============================================================================

SIGNAL_DEFS = {
    # OI signals
    "oi_roc_5d": "OI rate of change (5d)",
    "oi_roc_10d": "OI rate of change (10d)",
    "oi_roc_20d": "OI rate of change (20d)",
    "oi_zscore_20d": "OI z-score (20d rolling)",
    # Top trader L/S signals
    "toptrader_ls_raw": "Top trader L/S ratio (raw)",
    "toptrader_ls_zscore": "Top trader L/S z-score (20d)",
    "toptrader_ls_roc_5d": "Top trader L/S rate of change (5d)",
    # Divergence signals
    "ls_divergence_sum": "Top trader - general L/S (sum-based)",
    "ls_divergence_count": "Top trader - general L/S (count-based)",
    "ls_divergence_ratio": "Top trader / general L/S ratio",
    "ls_divergence_zscore": "L/S divergence z-score (20d)",
    # OI-price divergence
    "oi_price_div_5d": "OI-price divergence (5d)",
    "oi_price_div_10d": "OI-price divergence (10d)",
    "oi_price_div_20d": "OI-price divergence (20d)",
    # Taker signals
    "taker_ratio_zscore": "Taker buy/sell z-score (20d)",
    "taker_ratio_raw": "Taker buy/sell ratio (raw)",
    "taker_ratio_range": "Taker ratio intraday range",
    "ls_ratio_range": "L/S ratio intraday range",
    # Cross-token signals
    "oi_dispersion": "Cross-token OI z-score dispersion",
    "toptrader_ls_dispersion": "Cross-token top trader L/S dispersion",
    "taker_dispersion": "Cross-token taker ratio dispersion",
}


# ============================================================================
# MAIN ANALYSIS
# ============================================================================

def run_analysis():
    print("=" * 90)
    print("BINANCE FUTURES DAILY METRICS — SIGNAL PREDICTIVE POWER ANALYSIS")
    print(f"Run date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 90)

    # ------------------------------------------------------------------
    # Build panel
    # ------------------------------------------------------------------
    print("\n[1] Building panel data...")
    panel = build_panel()

    # Compute per-token signals
    print("[2] Computing per-token signals...")
    token_frames = []
    for token in panel["token"].unique():
        tok_df = panel[panel["token"] == token].copy()
        tok_df = compute_signals_per_token(tok_df)
        token_frames.append(tok_df)
    panel = pd.concat(token_frames, axis=0)

    # Compute cross-token signals
    print("[3] Computing cross-token signals...")
    panel = compute_cross_token_signals(panel)

    print(f"\nPanel shape: {panel.shape}")
    print(f"Tokens: {sorted(panel['token'].unique())} ({panel['token'].nunique()})")
    print(f"Date range: {panel.index.min().date()} to {panel.index.max().date()}")

    is_panel = panel[panel.index < OOS_START]
    oos_panel = panel[panel.index >= OOS_START]
    print(f"\nIS period: {is_panel.index.min().date()} to {is_panel.index.max().date()} ({len(is_panel)} obs)")
    print(f"OOS period: {oos_panel.index.min().date()} to {oos_panel.index.max().date()} ({len(oos_panel)} obs)")

    # ------------------------------------------------------------------
    # Analysis scopes
    # ------------------------------------------------------------------
    scopes = {}

    # Individual key tokens
    for token in KEY_TOKENS:
        token_data = panel[panel["token"] == token]
        if len(token_data) > 60:
            scopes[token] = token_data

    # Full panel (all tokens pooled)
    scopes["PANEL"] = panel

    all_results = []

    for scope_name, scope_data in scopes.items():
        print(f"\n{'=' * 70}")
        print(f"  SCOPE: {scope_name} ({len(scope_data)} observations)")
        print(f"{'=' * 70}")

        is_data = scope_data[scope_data.index < OOS_START]
        oos_data = scope_data[scope_data.index >= OOS_START]

        for sig_name, sig_desc in SIGNAL_DEFS.items():
            if sig_name not in scope_data.columns:
                continue

            valid_count = scope_data[sig_name].notna().sum()
            if valid_count < 60:
                continue

            for hz_label, hz_days in FWD_HORIZONS.items():
                ret_col = f"fwd_ret_{hz_label}"
                if ret_col not in scope_data.columns:
                    continue

                # Full-sample IC
                full_ic = compute_spearman_ic(scope_data[sig_name], scope_data[ret_col])

                # Hit rate
                hr = compute_hit_rate(scope_data[sig_name], scope_data[ret_col])

                # IS IC
                is_ic = compute_spearman_ic(is_data[sig_name], is_data[ret_col])

                # OOS IC
                oos_ic = compute_spearman_ic(oos_data[sig_name], oos_data[ret_col])

                # Sign consistency
                sign_consistent = None
                if not (np.isnan(is_ic["ic"]) or np.isnan(oos_ic["ic"])):
                    sign_consistent = bool(np.sign(is_ic["ic"]) == np.sign(oos_ic["ic"]))

                row = {
                    "scope": scope_name,
                    "signal": sig_name,
                    "description": sig_desc,
                    "horizon": hz_label,
                    "full_ic": full_ic["ic"],
                    "full_tstat": full_ic["tstat"],
                    "full_pval": full_ic["pval"],
                    "full_n": full_ic["n"],
                    "hit_rate": hr,
                    "is_ic": is_ic["ic"],
                    "is_tstat": is_ic["tstat"],
                    "is_n": is_ic["n"],
                    "oos_ic": oos_ic["ic"],
                    "oos_tstat": oos_ic["tstat"],
                    "oos_n": oos_ic["n"],
                    "sign_consistent": sign_consistent,
                }
                all_results.append(row)

    results_df = pd.DataFrame(all_results)

    # ------------------------------------------------------------------
    # Print full-sample results
    # ------------------------------------------------------------------
    print("\n\n" + "=" * 90)
    print("FULL-SAMPLE SPEARMAN IC RESULTS")
    print("=" * 90)
    print(f"{'Scope':<7} {'Signal':<28} {'Hz':<5} {'IC':>8} {'t-stat':>8} {'p-val':>8} {'N':>6} {'HitRate':>8}")
    print("-" * 90)

    for _, r in results_df.sort_values(["scope", "signal", "horizon"]).iterrows():
        ic_s = f"{r['full_ic']:>8.4f}" if not np.isnan(r['full_ic']) else "     NaN"
        ts_s = f"{r['full_tstat']:>8.2f}" if not np.isnan(r['full_tstat']) else "     NaN"
        pv_s = f"{r['full_pval']:>8.4f}" if not np.isnan(r['full_pval']) else "     NaN"
        hr_s = f"{r['hit_rate']:>7.1%}" if not np.isnan(r['hit_rate']) else "    NaN"
        print(f"{r['scope']:<7} {r['signal']:<28} {r['horizon']:<5} {ic_s} {ts_s} {pv_s} {r['full_n']:>6} {hr_s}")

    # ------------------------------------------------------------------
    # IS / OOS results
    # ------------------------------------------------------------------
    print("\n\n" + "=" * 90)
    print("IS / OOS SPLIT RESULTS (IS < 2025-01-01, OOS >= 2025-01-01)")
    print("=" * 90)
    print(f"{'Scope':<7} {'Signal':<28} {'Hz':<5} {'IS IC':>8} {'IS t':>8} {'OOS IC':>8} {'OOS t':>8} {'Sign?':>6}")
    print("-" * 90)

    for _, r in results_df.sort_values(["scope", "signal", "horizon"]).iterrows():
        is_s = f"{r['is_ic']:>8.4f}" if not np.isnan(r['is_ic']) else "     NaN"
        is_t = f"{r['is_tstat']:>8.2f}" if not np.isnan(r['is_tstat']) else "     NaN"
        oos_s = f"{r['oos_ic']:>8.4f}" if not np.isnan(r['oos_ic']) else "     NaN"
        oos_t = f"{r['oos_tstat']:>8.2f}" if not np.isnan(r['oos_tstat']) else "     NaN"
        sign_s = "  YES" if r['sign_consistent'] is True else ("   NO" if r['sign_consistent'] is False else "    -")
        print(f"{r['scope']:<7} {r['signal']:<28} {r['horizon']:<5} {is_s} {is_t} {oos_s} {oos_t} {sign_s}")

    # ------------------------------------------------------------------
    # PASS/FAIL evaluation
    # ------------------------------------------------------------------
    print("\n\n" + "=" * 90)
    print("PASS/FAIL EVALUATION")
    print("Criteria: |IC| > 0.05, |t-stat| > 2.0, sign consistent IS -> OOS")
    print("=" * 90)

    # Strong pass: full-sample criteria met AND sign consistent
    pass_full = results_df[
        (results_df["full_ic"].abs() > 0.05) &
        (results_df["full_tstat"].abs() > 2.0) &
        (results_df["sign_consistent"] == True)
    ].copy()

    # Also check IS-criteria pass (IS IC significant + sign consistent OOS)
    pass_is = results_df[
        (results_df["is_ic"].abs() > 0.05) &
        (results_df["is_tstat"].abs() > 2.0) &
        (results_df["sign_consistent"] == True)
    ].copy()

    # Merge both criteria sets
    pass_all = pd.concat([pass_full, pass_is]).drop_duplicates()

    if len(pass_all) > 0:
        print(f"\n{len(pass_all)} PASSING signal-scope-horizon combinations:\n")
        print(f"{'Scope':<7} {'Signal':<28} {'Hz':<5} {'FullIC':>8} {'Full t':>8} {'IS IC':>8} {'OOS IC':>8} {'HitRate':>8}")
        print("-" * 95)
        for _, r in pass_all.sort_values("full_ic", key=abs, ascending=False).iterrows():
            hr_s = f"{r['hit_rate']:>7.1%}" if not np.isnan(r['hit_rate']) else "    NaN"
            print(f"{r['scope']:<7} {r['signal']:<28} {r['horizon']:<5} "
                  f"{r['full_ic']:>8.4f} {r['full_tstat']:>8.2f} "
                  f"{r['is_ic']:>8.4f} {r['oos_ic']:>8.4f} {hr_s}")
    else:
        print("\n  NO signals meet all pass criteria.")

    # Near-misses
    near_miss = results_df[
        (results_df["full_ic"].abs() > 0.03) &
        (results_df["full_tstat"].abs() > 1.5) &
        ~results_df.index.isin(pass_all.index)
    ].sort_values("full_ic", key=abs, ascending=False)

    if len(near_miss) > 0:
        print(f"\n\nNEAR-MISS SIGNALS (|IC| > 0.03, |t-stat| > 1.5): {len(near_miss)} combinations\n")
        print(f"{'Scope':<7} {'Signal':<28} {'Hz':<5} {'IC':>8} {'t-stat':>8} {'Sign?':>6}")
        print("-" * 70)
        for _, r in near_miss.head(25).iterrows():
            sign_s = "YES" if r['sign_consistent'] is True else ("NO" if r['sign_consistent'] is False else "-")
            print(f"{r['scope']:<7} {r['signal']:<28} {r['horizon']:<5} "
                  f"{r['full_ic']:>8.4f} {r['full_tstat']:>8.2f} {sign_s:>6}")

    # ------------------------------------------------------------------
    # IC Decay analysis
    # ------------------------------------------------------------------
    print("\n\n" + "=" * 90)
    print("IC DECAY ANALYSIS (how IC varies across horizons)")
    print("=" * 90)

    hz_order = {"1d": 1, "3d": 3, "7d": 7, "14d": 14}

    for scope_name in results_df["scope"].unique():
        scope_subset = results_df[results_df["scope"] == scope_name]
        print(f"\n  {scope_name}:")
        for sig_name in sorted(scope_subset["signal"].unique()):
            sig_data = scope_subset[scope_subset["signal"] == sig_name].sort_values(
                "horizon", key=lambda x: x.map(hz_order)
            )
            ics = [f"{row['horizon']}={row['full_ic']:.4f}" for _, row in sig_data.iterrows()
                   if not np.isnan(row['full_ic'])]
            if ics:
                print(f"    {sig_name:<28}: {' | '.join(ics)}")

    # ------------------------------------------------------------------
    # Summary by signal family
    # ------------------------------------------------------------------
    print("\n\n" + "=" * 90)
    print("SUMMARY BY SIGNAL (across all scopes and horizons)")
    print("=" * 90)

    for sig_name in sorted(SIGNAL_DEFS.keys()):
        subset = results_df[results_df["signal"] == sig_name]
        if len(subset) == 0:
            continue
        mean_ic = subset["full_ic"].mean()
        median_ic = subset["full_ic"].median()
        max_abs_ic = subset.loc[subset["full_ic"].abs().idxmax()]
        pct_sign_consistent = subset["sign_consistent"].mean() if subset["sign_consistent"].notna().sum() > 0 else np.nan
        pct_significant = ((subset["full_ic"].abs() > 0.05) & (subset["full_tstat"].abs() > 2.0)).mean()

        print(f"\n  {sig_name} ({SIGNAL_DEFS[sig_name]}):")
        print(f"    Mean IC: {mean_ic:.4f}  |  Median IC: {median_ic:.4f}  |  "
              f"% significant: {pct_significant:.0%}  |  % sign consistent: "
              f"{pct_sign_consistent:.0%}" if not np.isnan(pct_sign_consistent) else "N/A")
        print(f"    Best: {max_abs_ic['scope']}/{max_abs_ic['horizon']} "
              f"IC={max_abs_ic['full_ic']:.4f} t={max_abs_ic['full_tstat']:.2f}")

    # ------------------------------------------------------------------
    # Cross-token dispersion detail
    # ------------------------------------------------------------------
    print("\n\n" + "=" * 90)
    print("CROSS-TOKEN DISPERSION SIGNALS (crowding indicators)")
    print("=" * 90)

    disp_signals = ["oi_dispersion", "toptrader_ls_dispersion", "taker_dispersion"]
    for sig in disp_signals:
        subset = results_df[(results_df["signal"] == sig) & (results_df["scope"] == "PANEL")]
        if len(subset) == 0:
            continue
        print(f"\n  {sig}:")
        for _, r in subset.sort_values("horizon", key=lambda x: x.map(hz_order)).iterrows():
            sign_s = "YES" if r['sign_consistent'] is True else ("NO" if r['sign_consistent'] is False else "-")
            print(f"    {r['horizon']}: Full IC={r['full_ic']:.4f} (t={r['full_tstat']:.2f}), "
                  f"IS={r['is_ic']:.4f}, OOS={r['oos_ic']:.4f}, Sign={sign_s}")

    # ------------------------------------------------------------------
    # Generate markdown report
    # ------------------------------------------------------------------
    generate_report(results_df, pass_all, near_miss)

    # Save raw results
    raw_dir = os.path.join(OUTPUT_DIR, "raw")
    os.makedirs(raw_dir, exist_ok=True)
    results_df.to_csv(os.path.join(raw_dir, "binance_metrics_ic_results.csv"), index=False)
    print(f"\nRaw results saved to: research/raw/binance_metrics_ic_results.csv")

    return results_df


# ============================================================================
# REPORT GENERATION
# ============================================================================

def generate_report(results_df: pd.DataFrame, pass_all: pd.DataFrame, near_miss: pd.DataFrame):
    """Generate markdown research report."""
    report_path = os.path.join(OUTPUT_DIR, "binance_metrics_signal_results.md")

    lines = []
    lines.append("# Binance Futures Daily Metrics — Signal Analysis Results")
    lines.append(f"\n**Run date:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"**Script:** `research/binance_metrics_signal_test.py`")

    lines.append("\n## Hypothesis")
    lines.append("Binance Futures positioning data (OI, top trader L/S ratios, taker buy/sell ratios)")
    lines.append("contains information that predicts future crypto returns. Specifically:")
    lines.append("- Rising OI may indicate crowded positioning, creating cascade risk (contrarian)")
    lines.append("- Top trader positioning may lead general positioning (smart money signal)")
    lines.append("- Divergence between top trader and general L/S may signal coming reversals")
    lines.append("- Taker buy/sell extremes may indicate short-term mean reversion")
    lines.append("- Cross-token OI dispersion may indicate crowding in specific names")

    lines.append("\n## Data")
    lines.append(f"- **Source:** Binance Futures daily metrics (all_symbols_daily_ls.parquet)")
    lines.append(f"- **Symbols:** {results_df['scope'].nunique()} scopes (BTC, ETH, full panel of {results_df[results_df['scope']=='PANEL']['full_n'].max()} obs)")
    lines.append(f"- **Date range:** Dec 2021 to Mar 2026")
    lines.append(f"- **IS period:** < 2025-01-01 (~3 years)")
    lines.append(f"- **OOS period:** >= 2025-01-01 (~1+ year)")

    lines.append("\n## Signal Definitions")
    lines.append("\n| Signal | Description |")
    lines.append("|--------|-------------|")
    for sig_name, sig_desc in SIGNAL_DEFS.items():
        lines.append(f"| `{sig_name}` | {sig_desc} |")

    lines.append("\n## Pass Criteria")
    lines.append("- |IC| > 0.05 (economically meaningful rank correlation)")
    lines.append("- |t-stat| > 2.0 (statistically significant)")
    lines.append("- Sign consistent IS -> OOS (signal doesn't flip direction)")

    # ------------------------------------------------------------------
    # Full-sample results table
    # ------------------------------------------------------------------
    lines.append("\n## Full-Sample IC Results")
    lines.append("\n| Scope | Signal | Horizon | IC | t-stat | p-value | N | Hit Rate |")
    lines.append("|-------|--------|---------|---:|-------:|--------:|--:|---------:|")

    for _, r in results_df.sort_values(["scope", "signal", "horizon"]).iterrows():
        ic_s = f"{r['full_ic']:.4f}" if not np.isnan(r['full_ic']) else "NaN"
        ts_s = f"{r['full_tstat']:.2f}" if not np.isnan(r['full_tstat']) else "NaN"
        pv_s = f"{r['full_pval']:.4f}" if not np.isnan(r['full_pval']) else "NaN"
        hr_s = f"{r['hit_rate']:.1%}" if not np.isnan(r['hit_rate']) else "NaN"

        bold = "**" if (abs(r['full_ic']) > 0.05 and abs(r['full_tstat']) > 2.0) else ""
        lines.append(f"| {r['scope']} | {r['signal']} | {r['horizon']} | "
                      f"{bold}{ic_s}{bold} | {bold}{ts_s}{bold} | {pv_s} | {int(r['full_n'])} | {hr_s} |")

    # ------------------------------------------------------------------
    # IS/OOS table
    # ------------------------------------------------------------------
    lines.append("\n## IS / OOS Split Results")
    lines.append("\n| Scope | Signal | Horizon | IS IC | IS t-stat | OOS IC | OOS t-stat | Consistent |")
    lines.append("|-------|--------|---------|------:|----------:|-------:|-----------:|:----------:|")

    for _, r in results_df.sort_values(["scope", "signal", "horizon"]).iterrows():
        is_s = f"{r['is_ic']:.4f}" if not np.isnan(r['is_ic']) else "NaN"
        is_t = f"{r['is_tstat']:.2f}" if not np.isnan(r['is_tstat']) else "NaN"
        oos_s = f"{r['oos_ic']:.4f}" if not np.isnan(r['oos_ic']) else "NaN"
        oos_t = f"{r['oos_tstat']:.2f}" if not np.isnan(r['oos_tstat']) else "NaN"
        sign_s = "YES" if r['sign_consistent'] is True else ("NO" if r['sign_consistent'] is False else "-")
        lines.append(f"| {r['scope']} | {r['signal']} | {r['horizon']} | "
                      f"{is_s} | {is_t} | {oos_s} | {oos_t} | {sign_s} |")

    # ------------------------------------------------------------------
    # Verdict
    # ------------------------------------------------------------------
    lines.append("\n## Verdict")

    if len(pass_all) > 0:
        lines.append(f"\n**{len(pass_all)} signal-scope-horizon combination(s) PASS all criteria:**\n")
        lines.append("| Scope | Signal | Horizon | Full IC | Full t-stat | IS IC | OOS IC | Hit Rate |")
        lines.append("|-------|--------|---------|--------:|------------:|------:|-------:|---------:|")
        for _, r in pass_all.sort_values("full_ic", key=abs, ascending=False).iterrows():
            hr_s = f"{r['hit_rate']:.1%}" if not np.isnan(r['hit_rate']) else "NaN"
            lines.append(f"| {r['scope']} | {r['signal']} | {r['horizon']} | "
                          f"{r['full_ic']:.4f} | {r['full_tstat']:.2f} | "
                          f"{r['is_ic']:.4f} | {r['oos_ic']:.4f} | {hr_s} |")
    else:
        lines.append("\n**FAIL: No signals meet all three pass criteria "
                      "(|IC| > 0.05, |t-stat| > 2.0, sign consistent IS -> OOS).**")

    if len(near_miss) > 0:
        lines.append(f"\n### Near-Miss Signals (|IC| > 0.03, |t-stat| > 1.5)")
        lines.append(f"\n{len(near_miss)} near-miss combinations found.\n")
        lines.append("| Scope | Signal | Horizon | IC | t-stat | Sign Consistent |")
        lines.append("|-------|--------|---------|---:|-------:|:---------------:|")
        for _, r in near_miss.head(20).iterrows():
            sign_s = "YES" if r['sign_consistent'] is True else ("NO" if r['sign_consistent'] is False else "-")
            lines.append(f"| {r['scope']} | {r['signal']} | {r['horizon']} | "
                          f"{r['full_ic']:.4f} | {r['full_tstat']:.2f} | {sign_s} |")

    # ------------------------------------------------------------------
    # Summary stats
    # ------------------------------------------------------------------
    lines.append("\n## Summary by Signal Family")

    signal_families = {
        "OI Signals": ["oi_roc_5d", "oi_roc_10d", "oi_roc_20d", "oi_zscore_20d"],
        "Top Trader L/S": ["toptrader_ls_raw", "toptrader_ls_zscore", "toptrader_ls_roc_5d"],
        "L/S Divergence": ["ls_divergence_sum", "ls_divergence_count", "ls_divergence_ratio", "ls_divergence_zscore"],
        "OI-Price Divergence": ["oi_price_div_5d", "oi_price_div_10d", "oi_price_div_20d"],
        "Taker Ratio": ["taker_ratio_zscore", "taker_ratio_raw", "taker_ratio_range"],
        "Cross-Token Dispersion": ["oi_dispersion", "toptrader_ls_dispersion", "taker_dispersion"],
    }

    for family_name, family_sigs in signal_families.items():
        family_data = results_df[results_df["signal"].isin(family_sigs)]
        if len(family_data) == 0:
            continue

        mean_ic = family_data["full_ic"].mean()
        mean_abs_ic = family_data["full_ic"].abs().mean()
        pct_significant = ((family_data["full_ic"].abs() > 0.05) & (family_data["full_tstat"].abs() > 2.0)).mean()
        pct_sign_consistent = family_data["sign_consistent"].mean() if family_data["sign_consistent"].notna().sum() > 0 else np.nan
        n_pass = len(pass_all[pass_all["signal"].isin(family_sigs)]) if len(pass_all) > 0 else 0

        lines.append(f"\n### {family_name}")
        lines.append(f"- Mean IC: {mean_ic:.4f}, Mean |IC|: {mean_abs_ic:.4f}")
        lines.append(f"- % statistically significant: {pct_significant:.0%}")
        sc_str = f"{pct_sign_consistent:.0%}" if not np.isnan(pct_sign_consistent) else "N/A"
        lines.append(f"- % sign consistent IS->OOS: {sc_str}")
        lines.append(f"- Passing combinations: {n_pass}")

    # ------------------------------------------------------------------
    # Conclusions
    # ------------------------------------------------------------------
    lines.append("\n## Conclusions")

    if len(pass_all) > 0:
        # Identify which families pass
        passing_families = set()
        for _, r in pass_all.iterrows():
            for fam, sigs in signal_families.items():
                if r["signal"] in sigs:
                    passing_families.add(fam)

        lines.append(f"\nBinance Futures metrics show predictive signal in {len(passing_families)} signal family/families:")
        for fam in passing_families:
            fam_passes = pass_all[pass_all["signal"].isin(signal_families[fam])]
            lines.append(f"- **{fam}**: {len(fam_passes)} passing combination(s)")

        lines.append("\nRecommended next steps:")
        lines.append("1. Integrate passing signals into the multi-factor model")
        lines.append("2. Test interaction effects between passing signals")
        lines.append("3. Evaluate portfolio-level performance with position sizing")
        lines.append("4. Test robustness across different market regimes (trending vs ranging)")
    else:
        lines.append("\nBinance Futures daily metrics do **not** meet the strict pass criteria for reliable alpha generation.")
        n_near = len(near_miss)
        if n_near > 0:
            lines.append(f"However, {n_near} near-miss combination(s) show marginal predictive power.")
            lines.append("These may be useful as secondary/confirming factors in a multi-signal framework.")

        lines.append("\nPossible explanations for weak results:")
        lines.append("- Binance positioning data is widely followed -- alpha may be arbitraged away")
        lines.append("- Daily frequency may be too coarse -- intraday positioning shifts could matter more")
        lines.append("- The relationship may be regime-dependent (works in trends, not ranges)")
        lines.append("- L/S ratios may need different lookback windows per token")
        lines.append("- OI signals may work better as interaction terms (OI x price divergence) than standalone")

    with open(report_path, "w") as f:
        f.write("\n".join(lines))

    print(f"\nReport saved to: {report_path}")


if __name__ == "__main__":
    results = run_analysis()
