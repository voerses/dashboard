#!/usr/bin/env python3
"""
Regime-Split Signal Testing
============================
Tests whether our top 5 signals work across all market regimes or only in specific ones.

Regimes (defined by BTC price dynamics):
  UPTREND:   BTC 50d return > 10%
  DOWNTREND: BTC 50d return < -10%
  RANGE:     BTC 50d return between -10% and +10%
  CRISIS:    BTC 20d realized vol > 80th pctile AND BTC 20d return < -15%

Signals tested:
  1. US10Y+DXY regime score
  2. Top Trader L/S raw
  3. L/S Divergence
  4. Skew_30d
  5. Oil 20d momentum

OOS split: train <2025-01-01, test >=2025-01-01
"""

import warnings
warnings.filterwarnings("ignore")

import pandas as pd
import numpy as np
from scipy import stats
from pathlib import Path
from datetime import datetime

# =============================================================================
# DATA LOADING
# =============================================================================

def load_btc_daily():
    """Load BTC 1h data and resample to daily."""
    btc = pd.read_parquet("/workspace/crypto_backtest/data/perp/1h_cache/BTC_1h.parquet")
    daily = btc.resample("1D").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).dropna(subset=["close"])
    return daily


def load_us10y():
    """Load US 10Y yield data."""
    df = pd.read_parquet("/workspace/crypto_backtest/data/alternative/macro/us10y_yield.parquet")
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.set_index("Date").sort_index()
    df = df[["Close"]].rename(columns={"Close": "us10y"})
    return df


def load_dxy():
    """Load USD index data."""
    df = pd.read_parquet("/workspace/crypto_backtest/data/alternative/macro/usd_index.parquet")
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.set_index("Date").sort_index()
    df = df[["Close"]].rename(columns={"Close": "dxy"})
    return df


def load_oil():
    """Load WTI oil data."""
    df = pd.read_parquet("/workspace/crypto_backtest/data/alternative/macro/oil_wti.parquet")
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.set_index("Date").sort_index()
    df = df[["Close"]].rename(columns={"Close": "oil"})
    return df


def load_ls_data():
    """Load Binance L/S ratio data for BTCUSDT."""
    df = pd.read_parquet("/workspace/crypto_backtest/data/alternative/binance_metrics/all_symbols_daily_ls.parquet")
    btc_ls = df[df["symbol"] == "BTCUSDT"].copy()
    btc_ls["date"] = pd.to_datetime(btc_ls["date"])
    btc_ls = btc_ls.set_index("date").sort_index()
    btc_ls = btc_ls[["sum_toptrader_ls_ratio", "count_toptrader_ls_ratio", "count_ls_ratio"]]
    return btc_ls


# =============================================================================
# REGIME CLASSIFICATION
# =============================================================================

def classify_regimes(btc_daily):
    """
    Classify each day into a regime based on BTC price dynamics.
    Returns a Series with regime labels.

    UPTREND:   50d return > 10%
    DOWNTREND: 50d return < -10%
    RANGE:     50d return between -10% and +10%
    CRISIS:    20d realized vol > 80th pctile AND 20d return < -15%
    """
    df = btc_daily.copy()

    # 50d return
    df["ret_50d"] = df["close"].pct_change(50)

    # 20d return
    df["ret_20d"] = df["close"].pct_change(20)

    # 20d realized volatility (annualized from daily returns)
    df["daily_ret"] = df["close"].pct_change()
    df["vol_20d"] = df["daily_ret"].rolling(20).std() * np.sqrt(365)

    # 80th percentile of 20d vol (expanding to avoid lookahead)
    df["vol_80pct"] = df["vol_20d"].expanding().quantile(0.80)

    # Classify
    regime = pd.Series("RANGE", index=df.index)
    regime[df["ret_50d"] > 0.10] = "UPTREND"
    regime[df["ret_50d"] < -0.10] = "DOWNTREND"

    # CRISIS overrides: high vol + significant drawdown
    crisis_mask = (df["vol_20d"] > df["vol_80pct"]) & (df["ret_20d"] < -0.15)
    regime[crisis_mask] = "CRISIS"

    regime.name = "regime"
    return regime, df


# =============================================================================
# SIGNAL COMPUTATION
# =============================================================================

def compute_signals(btc_daily, us10y, dxy, oil, ls_data):
    """Compute all 5 signals aligned to BTC daily dates."""

    df = btc_daily[["close"]].copy()
    df["daily_ret"] = df["close"].pct_change()

    # --- Signal 1: US10Y + DXY regime score ---
    # rank(us10y_20d_chg) + rank(dxy_20d_pct_chg)
    us10y_aligned = us10y.reindex(df.index, method="ffill")
    dxy_aligned = dxy.reindex(df.index, method="ffill")

    us10y_20d_chg = us10y_aligned["us10y"].diff(20)
    dxy_20d_pct_chg = dxy_aligned["dxy"].pct_change(20)

    # Expanding rank (percentile) to avoid lookahead
    us10y_rank = us10y_20d_chg.expanding(min_periods=60).rank(pct=True)
    dxy_rank = dxy_20d_pct_chg.expanding(min_periods=60).rank(pct=True)

    df["sig_us10y_dxy"] = us10y_rank + dxy_rank

    # --- Signal 2: Top Trader L/S raw ---
    ls_aligned = ls_data.reindex(df.index, method="ffill")
    df["sig_toptrader_ls"] = ls_aligned["sum_toptrader_ls_ratio"]

    # --- Signal 3: L/S Divergence ---
    # count_toptrader_ls_ratio - count_ls_ratio
    df["sig_ls_divergence"] = (
        ls_aligned["count_toptrader_ls_ratio"] - ls_aligned["count_ls_ratio"]
    )

    # --- Signal 4: Skew_30d ---
    # (mean - median) / std of 30d rolling daily returns
    rolling_mean = df["daily_ret"].rolling(30).mean()
    rolling_median = df["daily_ret"].rolling(30).median()
    rolling_std = df["daily_ret"].rolling(30).std()
    df["sig_skew_30d"] = (rolling_mean - rolling_median) / rolling_std

    # --- Signal 5: Oil 20d momentum ---
    oil_aligned = oil.reindex(df.index, method="ffill")
    df["sig_oil_20d_mom"] = oil_aligned["oil"].pct_change(20)

    # --- Forward returns ---
    df["fwd_7d"] = df["close"].pct_change(7).shift(-7)
    df["fwd_14d"] = df["close"].pct_change(14).shift(-14)

    return df


# =============================================================================
# IC ANALYSIS
# =============================================================================

SIGNAL_COLS = [
    "sig_us10y_dxy",
    "sig_toptrader_ls",
    "sig_ls_divergence",
    "sig_skew_30d",
    "sig_oil_20d_mom",
]

SIGNAL_NAMES = {
    "sig_us10y_dxy": "US10Y+DXY Regime",
    "sig_toptrader_ls": "Top Trader L/S Raw",
    "sig_ls_divergence": "L/S Divergence",
    "sig_skew_30d": "Skew 30d",
    "sig_oil_20d_mom": "Oil 20d Momentum",
}

REGIME_ORDER = ["UPTREND", "DOWNTREND", "RANGE", "CRISIS"]


def compute_ic(signal, forward_ret):
    """Compute Spearman IC, t-stat, and N for a signal vs forward return."""
    mask = signal.notna() & forward_ret.notna()
    s = signal[mask]
    f = forward_ret[mask]
    n = len(s)
    if n < 20:
        return np.nan, np.nan, n

    ic, pval = stats.spearmanr(s, f)
    # t-stat approximation for Spearman
    t_stat = ic * np.sqrt((n - 2) / (1 - ic**2 + 1e-10))
    return ic, t_stat, n


def regime_ic_analysis(df, regime, split_date="2025-01-01"):
    """
    Compute IC for each signal x regime x horizon, split by train/test.
    Returns a list of result dicts.
    """
    results = []

    for period_name, period_mask in [
        ("TRAIN", df.index < split_date),
        ("TEST", df.index >= split_date),
        ("ALL", pd.Series(True, index=df.index)),
    ]:
        for regime_name in REGIME_ORDER + ["ALL"]:
            if regime_name == "ALL":
                r_mask = pd.Series(True, index=df.index)
            else:
                r_mask = (regime == regime_name)

            combined_mask = period_mask & r_mask
            sub = df[combined_mask]

            for sig_col in SIGNAL_COLS:
                for horizon, fwd_col in [("7d", "fwd_7d"), ("14d", "fwd_14d")]:
                    ic, t_stat, n = compute_ic(sub[sig_col], sub[fwd_col])

                    direction = "—"
                    if not np.isnan(ic):
                        if ic > 0.02:
                            direction = "LONG"
                        elif ic < -0.02:
                            direction = "SHORT"
                        else:
                            direction = "FLAT"

                    results.append({
                        "period": period_name,
                        "regime": regime_name,
                        "signal": SIGNAL_NAMES[sig_col],
                        "signal_col": sig_col,
                        "horizon": horizon,
                        "IC": ic,
                        "t_stat": t_stat,
                        "N": n,
                        "direction": direction,
                    })

    return pd.DataFrame(results)


# =============================================================================
# SIGNAL CLASSIFICATION
# =============================================================================

def classify_signal_type(results_df, period="TRAIN", horizon="7d"):
    """
    Classify each signal based on regime behavior:
    - ALWAYS-ON: IC significant (|t|>1.5) in 3+ regimes, same sign
    - REGIME-SWITCHED: significant in 1-2 regimes, flat/reversed in others
    - BULL-ONLY: only works in UPTREND
    - CRISIS-ALPHA: works in CRISIS/DOWNTREND
    """
    classifications = {}

    sub = results_df[
        (results_df["period"] == period) &
        (results_df["horizon"] == horizon) &
        (results_df["regime"].isin(REGIME_ORDER))
    ]

    for sig_name in SIGNAL_NAMES.values():
        sig_data = sub[sub["signal"] == sig_name].set_index("regime")

        sig_regimes = {}
        for regime in REGIME_ORDER:
            if regime in sig_data.index:
                row = sig_data.loc[regime]
                ic = row["IC"]
                t = row["t_stat"]
                n = row["N"]
                significant = abs(t) > 1.5 if not np.isnan(t) else False
                sig_regimes[regime] = {
                    "IC": ic, "t_stat": t, "N": n,
                    "significant": significant,
                    "sign": np.sign(ic) if not np.isnan(ic) else 0,
                }
            else:
                sig_regimes[regime] = {"IC": np.nan, "t_stat": np.nan, "N": 0, "significant": False, "sign": 0}

        # Count significant regimes and their signs
        sig_count = sum(1 for r in sig_regimes.values() if r["significant"])
        sig_signs = [r["sign"] for r in sig_regimes.values() if r["significant"]]
        all_same_sign = len(set(sig_signs)) <= 1 if sig_signs else False

        # Classify
        uptrend_sig = sig_regimes.get("UPTREND", {}).get("significant", False)
        downtrend_sig = sig_regimes.get("DOWNTREND", {}).get("significant", False)
        crisis_sig = sig_regimes.get("CRISIS", {}).get("significant", False)
        range_sig = sig_regimes.get("RANGE", {}).get("significant", False)

        if sig_count >= 3 and all_same_sign:
            classification = "ALWAYS-ON"
        elif uptrend_sig and not downtrend_sig and not crisis_sig and not range_sig:
            classification = "BULL-ONLY"
        elif (crisis_sig or downtrend_sig) and not uptrend_sig:
            classification = "CRISIS-ALPHA"
        elif sig_count >= 1:
            classification = "REGIME-SWITCHED"
        else:
            classification = "INSIGNIFICANT"

        # Identify good/bad regimes for regime-switched
        good_regimes = [r for r, v in sig_regimes.items() if v["significant"]]
        bad_regimes = [r for r, v in sig_regimes.items() if not v["significant"]]

        classifications[sig_name] = {
            "type": classification,
            "sig_count": sig_count,
            "good_regimes": good_regimes,
            "bad_regimes": bad_regimes,
            "details": sig_regimes,
        }

    return classifications


# =============================================================================
# SHARPE IMPROVEMENT FROM REGIME SWITCHING
# =============================================================================

def compute_sharpe_improvement(df, regime, classifications, horizon="7d"):
    """
    For regime-switched signals: compute Sharpe improvement from switching OFF
    in bad regimes.

    Simple model: go LONG when signal > median (in good regimes), FLAT in bad regimes.
    Compare to always-on baseline (LONG when signal > median, all regimes).
    """
    fwd_col = f"fwd_{horizon}"
    train_mask = df.index < "2025-01-01"
    test_mask = df.index >= "2025-01-01"

    improvements = {}

    for sig_name, cls in classifications.items():
        sig_col = [k for k, v in SIGNAL_NAMES.items() if v == sig_name][0]

        good_regimes = cls["good_regimes"]
        if not good_regimes or cls["type"] == "INSIGNIFICANT":
            improvements[sig_name] = {"train": None, "test": None}
            continue

        # Determine signal direction from good regimes
        avg_ic = np.nanmean([cls["details"][r]["IC"] for r in good_regimes])
        go_long = avg_ic > 0  # if positive IC, signal > median = bullish

        for period_name, period_mask in [("train", train_mask), ("test", test_mask)]:
            sub = df[period_mask].copy()
            sub_regime = regime[period_mask]

            valid = sub[sig_col].notna() & sub[fwd_col].notna()
            sub = sub[valid]
            sub_regime = sub_regime[valid]

            if len(sub) < 40:
                improvements.setdefault(sig_name, {})[period_name] = None
                continue

            signal_median = sub[sig_col].expanding(min_periods=30).median()

            if go_long:
                always_on_pos = (sub[sig_col] > signal_median).astype(float)
            else:
                always_on_pos = (sub[sig_col] < signal_median).astype(float)

            # Regime-switched: only trade in good regimes
            regime_mask_good = sub_regime.isin(good_regimes)
            switched_pos = always_on_pos * regime_mask_good.astype(float)

            # Returns
            always_on_ret = always_on_pos * sub[fwd_col]
            switched_ret = switched_pos * sub[fwd_col]

            def sharpe(rets):
                if rets.std() == 0 or len(rets) < 10:
                    return 0
                return rets.mean() / rets.std() * np.sqrt(365 / 7)  # annualized

            sharpe_always = sharpe(always_on_ret.dropna())
            sharpe_switched = sharpe(switched_ret.dropna())

            improvements.setdefault(sig_name, {})[period_name] = {
                "sharpe_always_on": round(sharpe_always, 3),
                "sharpe_regime_switched": round(sharpe_switched, 3),
                "improvement": round(sharpe_switched - sharpe_always, 3),
                "pct_improvement": round(
                    (sharpe_switched - sharpe_always) / (abs(sharpe_always) + 1e-6) * 100, 1
                ),
                "fraction_traded": round(regime_mask_good.mean(), 3),
            }

    return improvements


# =============================================================================
# REPORT GENERATION
# =============================================================================

def generate_report(results_df, regime_fractions, classifications, improvements,
                    oos_classifications, oos_improvements):
    """Generate markdown report."""
    lines = []
    lines.append("# Regime-Split Signal Analysis")
    lines.append("")
    lines.append(f"**Generated**: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"**OOS split**: Train <2025-01-01 | Test >=2025-01-01")
    lines.append("")

    # --- Regime fractions ---
    lines.append("## Regime Distribution")
    lines.append("")
    lines.append("| Regime | Train Days | Train % | Test Days | Test % | All Days | All % |")
    lines.append("|--------|-----------|---------|-----------|--------|----------|-------|")
    for regime in REGIME_ORDER:
        train = regime_fractions.get(("TRAIN", regime), {"N": 0, "pct": 0})
        test = regime_fractions.get(("TEST", regime), {"N": 0, "pct": 0})
        all_ = regime_fractions.get(("ALL", regime), {"N": 0, "pct": 0})
        lines.append(
            f"| {regime} | {train['N']} | {train['pct']:.1f}% | "
            f"{test['N']} | {test['pct']:.1f}% | {all_['N']} | {all_['pct']:.1f}% |"
        )
    lines.append("")

    # --- Signal Classification Summary ---
    lines.append("## Signal Classification (Train, 7d horizon)")
    lines.append("")
    lines.append("| Signal | Classification | Significant In | Details |")
    lines.append("|--------|---------------|----------------|---------|")
    for sig_name, cls in classifications.items():
        good = ", ".join(cls["good_regimes"]) if cls["good_regimes"] else "None"

        type_emoji = {
            "ALWAYS-ON": "ALWAYS-ON",
            "REGIME-SWITCHED": "REGIME-SWITCHED",
            "BULL-ONLY": "BULL-ONLY (DANGEROUS)",
            "CRISIS-ALPHA": "CRISIS-ALPHA (VALUABLE)",
            "INSIGNIFICANT": "INSIGNIFICANT",
        }.get(cls["type"], cls["type"])

        detail_parts = []
        for r in REGIME_ORDER:
            d = cls["details"].get(r, {})
            ic = d.get("IC", np.nan)
            t = d.get("t_stat", np.nan)
            if not np.isnan(ic):
                star = "*" if abs(t) > 1.5 else ""
                detail_parts.append(f"{r[:3]}:{ic:+.3f}{star}")
        details_str = " | ".join(detail_parts)

        lines.append(f"| {sig_name} | **{type_emoji}** | {good} | {details_str} |")
    lines.append("")

    # --- Detailed IC Tables ---
    for period_name in ["TRAIN", "TEST"]:
        lines.append(f"## Detailed IC: {period_name}")
        lines.append("")

        for horizon in ["7d", "14d"]:
            lines.append(f"### {horizon} Forward Return")
            lines.append("")
            lines.append("| Signal | Regime | IC | t-stat | N | Direction |")
            lines.append("|--------|--------|---:|-------:|--:|-----------|")

            sub = results_df[
                (results_df["period"] == period_name) &
                (results_df["horizon"] == horizon)
            ]

            for sig_name in SIGNAL_NAMES.values():
                sig_rows = sub[sub["signal"] == sig_name]
                for _, row in sig_rows.iterrows():
                    ic_str = f"{row['IC']:.4f}" if not np.isnan(row['IC']) else "—"
                    t_str = f"{row['t_stat']:.2f}" if not np.isnan(row['t_stat']) else "—"
                    sig_marker = " **" if abs(row['t_stat']) > 1.5 and not np.isnan(row['t_stat']) else ""
                    sig_end = "**" if sig_marker else ""
                    lines.append(
                        f"| {row['signal']} | {row['regime']} | "
                        f"{sig_marker}{ic_str}{sig_end} | {sig_marker}{t_str}{sig_end} | "
                        f"{row['N']} | {row['direction']} |"
                    )
            lines.append("")

    # --- OOS Classification Comparison ---
    lines.append("## OOS Validation: Classification Stability")
    lines.append("")
    lines.append("| Signal | Train Class. | Test Class. | Stable? |")
    lines.append("|--------|-------------|------------|---------|")
    for sig_name in SIGNAL_NAMES.values():
        train_cls = classifications.get(sig_name, {}).get("type", "?")
        test_cls = oos_classifications.get(sig_name, {}).get("type", "?")
        stable = "YES" if train_cls == test_cls else "NO - REGIME SHIFT"
        lines.append(f"| {sig_name} | {train_cls} | {test_cls} | {stable} |")
    lines.append("")

    # --- Sharpe Improvement ---
    lines.append("## Sharpe Improvement from Regime Switching")
    lines.append("")
    lines.append("For regime-switched signals: compare always-on vs regime-filtered trading.")
    lines.append("")
    lines.append("| Signal | Period | Sharpe (Always-On) | Sharpe (Regime-Switched) | Improvement | % Time Traded |")
    lines.append("|--------|--------|-------------------:|------------------------:|------------:|--------------:|")

    for sig_name, imp_data in improvements.items():
        for period in ["train", "test"]:
            data = imp_data.get(period)
            if data is None:
                lines.append(f"| {sig_name} | {period.upper()} | — | — | — | — |")
            else:
                lines.append(
                    f"| {sig_name} | {period.upper()} | "
                    f"{data['sharpe_always_on']:.3f} | "
                    f"{data['sharpe_regime_switched']:.3f} | "
                    f"{data['improvement']:+.3f} ({data['pct_improvement']:+.1f}%) | "
                    f"{data['fraction_traded']:.1%} |"
                )
    lines.append("")

    # --- Key Findings ---
    lines.append("## Key Findings & Recommendations")
    lines.append("")

    always_on = [s for s, c in classifications.items() if c["type"] == "ALWAYS-ON"]
    regime_switched = [s for s, c in classifications.items() if c["type"] == "REGIME-SWITCHED"]
    bull_only = [s for s, c in classifications.items() if c["type"] == "BULL-ONLY"]
    crisis_alpha = [s for s, c in classifications.items() if c["type"] == "CRISIS-ALPHA"]
    insignificant = [s for s, c in classifications.items() if c["type"] == "INSIGNIFICANT"]

    if always_on:
        lines.append(f"### ALWAYS-ON Signals (safe to run in all regimes)")
        for s in always_on:
            lines.append(f"- **{s}**: Significant in 3+ regimes with consistent sign. No regime filter needed.")
        lines.append("")

    if regime_switched:
        lines.append(f"### REGIME-SWITCHED Signals (need regime overlay)")
        for s in regime_switched:
            cls = classifications[s]
            good = ", ".join(cls["good_regimes"])
            bad = ", ".join(cls["bad_regimes"])
            imp = improvements.get(s, {}).get("train")
            imp_str = f" Sharpe improvement from switching: {imp['improvement']:+.3f}" if imp else ""
            lines.append(
                f"- **{s}**: ON in [{good}], OFF in [{bad}].{imp_str}"
            )
        lines.append("")

    if bull_only:
        lines.append(f"### BULL-ONLY Signals (DANGEROUS - flag for review)")
        for s in bull_only:
            lines.append(
                f"- **{s}**: Only significant in UPTREND. Will give false signals in "
                f"bear markets. Either add regime gate or remove from signal stack."
            )
        lines.append("")

    if crisis_alpha:
        lines.append(f"### CRISIS-ALPHA Signals (valuable for hedging)")
        for s in crisis_alpha:
            cls = classifications[s]
            lines.append(
                f"- **{s}**: Works in CRISIS/DOWNTREND. Activate during stress periods for tail hedging."
            )
        lines.append("")

    if insignificant:
        lines.append(f"### INSIGNIFICANT Signals (no regime shows significance)")
        for s in insignificant:
            lines.append(f"- **{s}**: Not significant in any regime. Consider dropping.")
        lines.append("")

    # --- Action items ---
    lines.append("## Action Items")
    lines.append("")
    lines.append("1. **Always-on signals**: Deploy without regime filter")
    lines.append("2. **Regime-switched signals**: Implement BTC 50d return regime detector; gate signal ON/OFF by regime")
    lines.append("3. **Bull-only signals**: Add mandatory regime gate or remove from production signal set")
    lines.append("4. **Crisis-alpha signals**: Keep as overlay; activate when crisis detector fires")
    lines.append("5. **Insignificant signals**: Review for data quality issues or drop from stack")
    lines.append("")

    return "\n".join(lines)


# =============================================================================
# MAIN
# =============================================================================

def main():
    print("=" * 70)
    print("REGIME-SPLIT SIGNAL ANALYSIS")
    print("=" * 70)

    # Load data
    print("\n[1/6] Loading data...")
    btc_daily = load_btc_daily()
    us10y = load_us10y()
    dxy = load_dxy()
    oil = load_oil()
    ls_data = load_ls_data()

    print(f"  BTC daily: {btc_daily.index.min().date()} to {btc_daily.index.max().date()} ({len(btc_daily)} days)")
    print(f"  US10Y: {us10y.index.min().date()} to {us10y.index.max().date()}")
    print(f"  DXY: {dxy.index.min().date()} to {dxy.index.max().date()}")
    print(f"  Oil: {oil.index.min().date()} to {oil.index.max().date()}")
    print(f"  L/S data: {ls_data.index.min().date()} to {ls_data.index.max().date()}")

    # Classify regimes
    print("\n[2/6] Classifying regimes...")
    regime, btc_features = classify_regimes(btc_daily)

    # Compute regime fractions
    regime_fractions = {}
    for period_name, mask in [
        ("TRAIN", btc_daily.index < "2025-01-01"),
        ("TEST", btc_daily.index >= "2025-01-01"),
        ("ALL", pd.Series(True, index=btc_daily.index)),
    ]:
        total = mask.sum()
        for r in REGIME_ORDER:
            n = (mask & (regime == r)).sum()
            regime_fractions[(period_name, r)] = {"N": int(n), "pct": n / total * 100}

    print("\n  Regime distribution:")
    for r in REGIME_ORDER:
        n = regime_fractions[("ALL", r)]["N"]
        pct = regime_fractions[("ALL", r)]["pct"]
        print(f"    {r:12s}: {n:5d} days ({pct:.1f}%)")

    # Compute signals
    print("\n[3/6] Computing signals...")
    df = compute_signals(btc_daily, us10y, dxy, oil, ls_data)

    # Verify signal coverage
    for sig in SIGNAL_COLS:
        valid = df[sig].notna().sum()
        print(f"  {SIGNAL_NAMES[sig]:25s}: {valid} valid observations")

    # IC analysis
    print("\n[4/6] Computing regime-conditional IC...")
    results_df = regime_ic_analysis(df, regime)

    # Print summary
    print("\n  --- TRAIN 7d IC Summary ---")
    train_7d = results_df[
        (results_df["period"] == "TRAIN") & (results_df["horizon"] == "7d")
    ]
    for sig_name in SIGNAL_NAMES.values():
        sig_rows = train_7d[train_7d["signal"] == sig_name]
        parts = []
        for _, row in sig_rows.iterrows():
            if not np.isnan(row["IC"]):
                star = "*" if abs(row["t_stat"]) > 1.5 else " "
                parts.append(f"{row['regime']:10s}: IC={row['IC']:+.4f} t={row['t_stat']:+.2f}{star} N={row['N']}")
        print(f"\n  {sig_name}:")
        for p in parts:
            print(f"    {p}")

    # Classify signals
    print("\n[5/6] Classifying signals...")
    classifications = classify_signal_type(results_df, period="TRAIN", horizon="7d")
    oos_classifications = classify_signal_type(results_df, period="TEST", horizon="7d")

    print("\n  Signal Classifications (Train, 7d):")
    for sig_name, cls in classifications.items():
        good = ", ".join(cls["good_regimes"]) if cls["good_regimes"] else "None"
        print(f"    {sig_name:25s}: {cls['type']:20s} | Significant in: {good}")

    print("\n  OOS Classifications (Test, 7d):")
    for sig_name, cls in oos_classifications.items():
        good = ", ".join(cls["good_regimes"]) if cls["good_regimes"] else "None"
        print(f"    {sig_name:25s}: {cls['type']:20s} | Significant in: {good}")

    # Sharpe improvement
    print("\n[6/6] Computing Sharpe improvement from regime switching...")
    improvements = compute_sharpe_improvement(df, regime, classifications, horizon="7d")
    oos_improvements = compute_sharpe_improvement(df, regime, oos_classifications, horizon="7d")

    for sig_name, imp in improvements.items():
        train_imp = imp.get("train")
        test_imp = imp.get("test")
        if train_imp:
            print(
                f"  {sig_name:25s} TRAIN: Always-On={train_imp['sharpe_always_on']:.3f} "
                f"Switched={train_imp['sharpe_regime_switched']:.3f} "
                f"Delta={train_imp['improvement']:+.3f} "
                f"({train_imp['fraction_traded']:.0%} time traded)"
            )
        if test_imp:
            print(
                f"  {'':25s} TEST:  Always-On={test_imp['sharpe_always_on']:.3f} "
                f"Switched={test_imp['sharpe_regime_switched']:.3f} "
                f"Delta={test_imp['improvement']:+.3f} "
                f"({test_imp['fraction_traded']:.0%} time traded)"
            )

    # Generate report
    print("\n" + "=" * 70)
    print("GENERATING REPORT...")
    report = generate_report(
        results_df, regime_fractions, classifications, improvements,
        oos_classifications, oos_improvements,
    )

    output_path = Path("/workspace/crypto_backtest/research/regime_split_signal_results.md")
    output_path.write_text(report)
    print(f"Report saved to: {output_path}")

    print("\n" + "=" * 70)
    print("DONE")
    print("=" * 70)


if __name__ == "__main__":
    main()
