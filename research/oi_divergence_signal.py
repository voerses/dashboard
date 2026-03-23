"""
OI Rate-of-Change Divergence Signal for Crypto Perpetual Futures
================================================================

Signal Hypothesis:
- OI increases rapidly + price moves in sustained direction = leverage building (trend confirmation)
- OI spikes + price stalls = leverage buildup that will resolve in cascade (mean reversion signal)
- Key: OI acceleration diverging from price momentum

Data Sources:
- Price: Binance perp 1h OHLCV
- OI: Bybit 1h open interest (2024-01-01 to 2026-03-23)
- Funding: Binance perp funding rates

Methodology:
- Build daily OI/price signals from hourly data
- Test IC (Spearman rank correlation) with forward returns at 1d, 3d, 7d, 14d
- Temporal holdout: IS < 2025-07-01, OOS >= 2025-07-01
- Report: IC, t-stat, hit rate for each signal variant, IS vs OOS
"""

import os
import sys
import warnings
import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from scipy import stats

warnings.filterwarnings("ignore")

# ============================================================================
# Configuration
# ============================================================================

TOKENS = ["BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "AVAX", "LINK", "SUI"]
# BNB excluded: no Bybit OI history available

OI_DIR = "/workspace/crypto_backtest/data/perp/bybit_oi"
PRICE_DIR = "/workspace/crypto_backtest/data/perp/binance/1h_ohlcv"
FUNDING_DIR = "/workspace/crypto_backtest/data/perp/binance/funding"
OUTPUT_DIR = "/workspace/crypto_backtest/research"

# Signal windows (in days)
SIGNAL_WINDOWS = [1, 3, 7]

# Forward return horizons (in days)
FWD_HORIZONS = [1, 3, 7, 14]

# Temporal holdout split
HOLDOUT_DATE = pd.Timestamp("2025-07-01", tz="UTC")

# ============================================================================
# Data Loading
# ============================================================================


def load_oi_data(token: str) -> pd.DataFrame:
    """Load hourly OI data from Bybit."""
    path = os.path.join(OI_DIR, f"{token}_oi_1h.csv")
    df = pd.read_csv(path)
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
    df = df.rename(columns={"open_interest": "oi"})
    df = df[["datetime", "oi"]].sort_values("datetime").reset_index(drop=True)
    return df


def load_price_data(token: str) -> pd.DataFrame:
    """Load hourly OHLCV data from Binance perps."""
    path = os.path.join(PRICE_DIR, f"{token}_perp_1h.csv")
    df = pd.read_csv(path)
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
    df = df[["datetime", "open", "high", "low", "close", "volume"]].sort_values(
        "datetime"
    ).reset_index(drop=True)
    return df


def load_funding_data(token: str) -> pd.DataFrame:
    """Load funding rate data from Binance perps."""
    path = os.path.join(FUNDING_DIR, f"{token}_funding.csv")
    df = pd.read_csv(path)
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
    df = df.rename(columns={"funding_rate": "funding"})
    df = df[["datetime", "funding"]].sort_values("datetime").reset_index(drop=True)
    return df


def resample_to_daily(oi_hourly: pd.DataFrame, price_hourly: pd.DataFrame,
                      funding_df: pd.DataFrame) -> pd.DataFrame:
    """
    Resample hourly data to daily.
    OI: end-of-day snapshot (last value).
    Price: OHLCV aggregation.
    Funding: sum of daily funding payments.
    """
    oi_hourly = oi_hourly.set_index("datetime")
    price_hourly = price_hourly.set_index("datetime")
    funding_df = funding_df.set_index("datetime")

    # Daily OI: end-of-day snapshot
    oi_daily = oi_hourly["oi"].resample("1D").last().to_frame()

    # Daily price: standard OHLCV
    price_daily = price_hourly.resample("1D").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    })

    # Daily funding: sum of 8h payments within each day
    funding_daily = funding_df["funding"].resample("1D").sum().to_frame()

    # Merge
    merged = oi_daily.join(price_daily, how="inner").join(funding_daily, how="left")
    merged["funding"] = merged["funding"].fillna(0)

    # Drop rows with NaN OI or close
    merged = merged.dropna(subset=["oi", "close"])

    return merged


# ============================================================================
# Signal Construction
# ============================================================================


def compute_signals(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute OI divergence signals.

    Signals:
    1. OI_pct_change_{w}d: OI rate of change over w days
    2. Price_pct_change_{w}d: Price rate of change over w days
    3. OI_price_divergence_{w}d: OI_pct_change - abs(Price_pct_change)
       Positive = OI growing faster than price is moving (leverage buildup)
    4. OI_acceleration_{w}d: Change in OI_pct_change (second derivative)
    5. OI_div_signed_{w}d: OI_pct_change - Price_pct_change (preserves direction)
    6. Funding_extreme: abs(cumulative funding) z-score over 30d rolling window
    7. Combined signals: divergence + funding extreme
    """
    out = df.copy()

    for w in SIGNAL_WINDOWS:
        # Raw percent changes
        out[f"oi_pct_{w}d"] = out["oi"].pct_change(w)
        out[f"price_pct_{w}d"] = out["close"].pct_change(w)

        # OI-Price divergence (unsigned): OI growth vs magnitude of price move
        # Positive = OI growing more than price is moving in any direction
        out[f"oi_div_unsigned_{w}d"] = out[f"oi_pct_{w}d"] - out[f"price_pct_{w}d"].abs()

        # OI-Price divergence (signed): OI growth minus price change
        # When OI rises and price falls, this is very positive = bearish leverage buildup
        out[f"oi_div_signed_{w}d"] = out[f"oi_pct_{w}d"] - out[f"price_pct_{w}d"]

        # OI acceleration: change in OI rate of change
        out[f"oi_accel_{w}d"] = out[f"oi_pct_{w}d"].diff(w)

        # OI divergence normalized by volatility
        # Use rolling std of price returns as denominator
        price_vol = out["close"].pct_change(1).rolling(w * 5).std()
        out[f"oi_div_vol_adj_{w}d"] = out[f"oi_pct_{w}d"] / price_vol.clip(lower=1e-6)

    # Funding rate signals
    # Cumulative funding over 3d
    out["funding_cum_3d"] = out["funding"].rolling(3 * 3).sum()  # ~3 days of 8h payments
    # Funding z-score (30d rolling)
    funding_30d_mean = out["funding"].rolling(30).mean()
    funding_30d_std = out["funding"].rolling(30).std()
    out["funding_zscore"] = (out["funding"] - funding_30d_mean) / funding_30d_std.clip(lower=1e-8)

    # Combined signals: OI divergence + funding extreme (interaction)
    for w in SIGNAL_WINDOWS:
        out[f"oi_div_funding_{w}d"] = out[f"oi_div_unsigned_{w}d"] * out["funding_zscore"].abs()
        out[f"oi_div_signed_funding_{w}d"] = out[f"oi_div_signed_{w}d"] * out["funding_zscore"]

    # Volume-OI divergence: volume declining while OI rises (hollow leverage)
    for w in SIGNAL_WINDOWS:
        vol_pct = out["volume"].pct_change(w)
        out[f"oi_vol_div_{w}d"] = out[f"oi_pct_{w}d"] - vol_pct

    return out


def compute_forward_returns(df: pd.DataFrame) -> pd.DataFrame:
    """Compute forward returns at various horizons."""
    out = df.copy()
    for h in FWD_HORIZONS:
        out[f"fwd_ret_{h}d"] = out["close"].pct_change(h).shift(-h)
    return out


# ============================================================================
# Signal Evaluation
# ============================================================================


def evaluate_signal(signal_vals: pd.Series, fwd_ret_vals: pd.Series,
                    min_obs: int = 50) -> dict:
    """
    Evaluate a signal's predictive power.

    Returns:
    - ic: Spearman rank correlation (Information Coefficient)
    - ic_pval: p-value of the IC
    - t_stat: t-statistic of the IC
    - hit_rate: fraction of times sign(signal) == sign(fwd_return)
    - n_obs: number of valid observations
    """
    # Align and drop NaN
    mask = signal_vals.notna() & fwd_ret_vals.notna()
    # Also drop infinite values
    mask = mask & np.isfinite(signal_vals) & np.isfinite(fwd_ret_vals)
    sig = signal_vals[mask]
    ret = fwd_ret_vals[mask]

    n = len(sig)
    if n < min_obs:
        return {
            "ic": np.nan, "ic_pval": np.nan, "t_stat": np.nan,
            "hit_rate": np.nan, "n_obs": n,
        }

    # Spearman rank correlation
    ic, ic_pval = stats.spearmanr(sig, ret)

    # t-statistic: t = IC * sqrt(n-2) / sqrt(1-IC^2)
    if abs(ic) < 1.0:
        t_stat = ic * np.sqrt(n - 2) / np.sqrt(1 - ic**2)
    else:
        t_stat = np.inf * np.sign(ic)

    # Hit rate: how often does signal direction match return direction
    # For divergence signals, positive signal (OI > price) should predict mean reversion (negative return)
    # So we check if sign(signal) matches sign(-fwd_return) for mean reversion
    # But we report raw hit rate of sign agreement for now; interpretation depends on signal
    hit_rate = np.mean(np.sign(sig.values) == np.sign(ret.values))

    return {
        "ic": ic,
        "ic_pval": ic_pval,
        "t_stat": t_stat,
        "hit_rate": hit_rate,
        "n_obs": n,
    }


def compute_rolling_ic(signal_vals: pd.Series, fwd_ret_vals: pd.Series,
                       window: int = 60) -> pd.Series:
    """Compute rolling IC for stability analysis."""
    mask = signal_vals.notna() & fwd_ret_vals.notna() & np.isfinite(signal_vals) & np.isfinite(fwd_ret_vals)
    sig = signal_vals.copy()
    ret = fwd_ret_vals.copy()
    sig[~mask] = np.nan
    ret[~mask] = np.nan

    rolling_ic = pd.Series(index=sig.index, dtype=float)
    for i in range(window, len(sig)):
        s_win = sig.iloc[i - window:i]
        r_win = ret.iloc[i - window:i]
        valid = s_win.notna() & r_win.notna()
        if valid.sum() >= 30:
            ic, _ = stats.spearmanr(s_win[valid], r_win[valid])
            rolling_ic.iloc[i] = ic

    return rolling_ic


# ============================================================================
# Main Pipeline
# ============================================================================


def build_panel() -> pd.DataFrame:
    """Load all data, merge, and compute signals for all tokens."""
    all_frames = []

    for token in TOKENS:
        print(f"  Loading {token}...")
        try:
            oi = load_oi_data(token)
            price = load_price_data(token)
            funding = load_funding_data(token)
        except FileNotFoundError as e:
            print(f"    SKIP {token}: {e}")
            continue

        # Resample to daily
        daily = resample_to_daily(oi, price, funding)

        # Compute signals
        daily = compute_signals(daily)

        # Compute forward returns
        daily = compute_forward_returns(daily)

        daily["token"] = token
        all_frames.append(daily)

    panel = pd.concat(all_frames, axis=0)
    panel = panel.sort_index()
    return panel


def get_signal_columns() -> list:
    """Return list of all signal column names."""
    signals = []
    for w in SIGNAL_WINDOWS:
        signals.extend([
            f"oi_pct_{w}d",
            f"price_pct_{w}d",
            f"oi_div_unsigned_{w}d",
            f"oi_div_signed_{w}d",
            f"oi_accel_{w}d",
            f"oi_div_vol_adj_{w}d",
            f"oi_div_funding_{w}d",
            f"oi_div_signed_funding_{w}d",
            f"oi_vol_div_{w}d",
        ])
    signals.extend(["funding_cum_3d", "funding_zscore"])
    return signals


def run_analysis(panel: pd.DataFrame) -> pd.DataFrame:
    """
    Run IC analysis for all signals x forward horizons x IS/OOS splits.
    Returns a DataFrame of results.
    """
    signal_cols = get_signal_columns()
    results = []

    # Split IS/OOS
    is_mask = panel.index < HOLDOUT_DATE
    oos_mask = panel.index >= HOLDOUT_DATE

    for sig_name in signal_cols:
        for horizon in FWD_HORIZONS:
            fwd_col = f"fwd_ret_{horizon}d"

            # In-Sample
            is_data = panel[is_mask]
            is_eval = evaluate_signal(is_data[sig_name], is_data[fwd_col])
            is_eval.update({
                "signal": sig_name,
                "horizon": f"{horizon}d",
                "sample": "IS",
            })
            results.append(is_eval)

            # Out-of-Sample
            oos_data = panel[oos_mask]
            oos_eval = evaluate_signal(oos_data[sig_name], oos_data[fwd_col])
            oos_eval.update({
                "signal": sig_name,
                "horizon": f"{horizon}d",
                "sample": "OOS",
            })
            results.append(oos_eval)

            # Per-token analysis for top signals
            for token in TOKENS:
                token_data = panel[panel["token"] == token]
                # IS
                token_is = token_data[token_data.index < HOLDOUT_DATE]
                token_is_eval = evaluate_signal(token_is[sig_name], token_is[fwd_col], min_obs=30)
                token_is_eval.update({
                    "signal": sig_name,
                    "horizon": f"{horizon}d",
                    "sample": f"IS_{token}",
                })
                results.append(token_is_eval)
                # OOS
                token_oos = token_data[token_data.index >= HOLDOUT_DATE]
                token_oos_eval = evaluate_signal(token_oos[sig_name], token_oos[fwd_col], min_obs=30)
                token_oos_eval.update({
                    "signal": sig_name,
                    "horizon": f"{horizon}d",
                    "sample": f"OOS_{token}",
                })
                results.append(token_oos_eval)

    return pd.DataFrame(results)


def format_results_table(results_df: pd.DataFrame) -> str:
    """Format the aggregate results as a readable markdown table."""
    # Filter to aggregate IS/OOS only
    agg = results_df[results_df["sample"].isin(["IS", "OOS"])].copy()
    agg = agg.sort_values(["signal", "horizon", "sample"])

    # Pivot to show IS and OOS side by side
    lines = []
    lines.append("=" * 130)
    lines.append(f"{'Signal':<35} {'Horizon':<8} {'Sample':<5} {'IC':>8} {'t-stat':>8} {'Hit%':>7} {'p-val':>10} {'N':>6}")
    lines.append("=" * 130)

    for _, row in agg.iterrows():
        ic_str = f"{row['ic']:.4f}" if not np.isnan(row['ic']) else "   N/A"
        t_str = f"{row['t_stat']:.2f}" if not np.isnan(row['t_stat']) else "  N/A"
        hit_str = f"{row['hit_rate'] * 100:.1f}%" if not np.isnan(row['hit_rate']) else " N/A"
        p_str = f"{row['ic_pval']:.2e}" if not np.isnan(row['ic_pval']) else "      N/A"
        n_str = f"{int(row['n_obs'])}"
        lines.append(
            f"{row['signal']:<35} {row['horizon']:<8} {row['sample']:<5} "
            f"{ic_str:>8} {t_str:>8} {hit_str:>7} {p_str:>10} {n_str:>6}"
        )

    lines.append("=" * 130)
    return "\n".join(lines)


def find_top_signals(results_df: pd.DataFrame, n: int = 10) -> pd.DataFrame:
    """Find top signals by absolute IC that are consistent IS to OOS."""
    agg = results_df[results_df["sample"].isin(["IS", "OOS"])].copy()

    # Pivot IS/OOS side-by-side
    is_df = agg[agg["sample"] == "IS"][["signal", "horizon", "ic", "t_stat", "hit_rate", "n_obs"]].copy()
    is_df.columns = ["signal", "horizon", "ic_is", "t_is", "hit_is", "n_is"]

    oos_df = agg[agg["sample"] == "OOS"][["signal", "horizon", "ic", "t_stat", "hit_rate", "n_obs"]].copy()
    oos_df.columns = ["signal", "horizon", "ic_oos", "t_oos", "hit_oos", "n_oos"]

    merged = is_df.merge(oos_df, on=["signal", "horizon"])

    # Score: rank by |IC_OOS| where IC signs are consistent
    merged["sign_consistent"] = np.sign(merged["ic_is"]) == np.sign(merged["ic_oos"])
    merged["abs_ic_oos"] = merged["ic_oos"].abs()
    merged["abs_ic_is"] = merged["ic_is"].abs()

    # Sort by OOS IC magnitude, preferring sign-consistent signals
    merged = merged.sort_values(
        ["sign_consistent", "abs_ic_oos"],
        ascending=[False, False]
    )

    return merged.head(n)


def per_token_summary(results_df: pd.DataFrame, signal_name: str, horizon: str) -> pd.DataFrame:
    """Get per-token breakdown for a specific signal and horizon."""
    token_rows = []
    for token in TOKENS:
        is_key = f"IS_{token}"
        oos_key = f"OOS_{token}"
        is_row = results_df[
            (results_df["signal"] == signal_name) &
            (results_df["horizon"] == horizon) &
            (results_df["sample"] == is_key)
        ]
        oos_row = results_df[
            (results_df["signal"] == signal_name) &
            (results_df["horizon"] == horizon) &
            (results_df["sample"] == oos_key)
        ]
        if len(is_row) > 0 and len(oos_row) > 0:
            token_rows.append({
                "token": token,
                "ic_is": is_row.iloc[0]["ic"],
                "ic_oos": oos_row.iloc[0]["ic"],
                "t_is": is_row.iloc[0]["t_stat"],
                "t_oos": oos_row.iloc[0]["t_stat"],
                "hit_is": is_row.iloc[0]["hit_rate"],
                "hit_oos": oos_row.iloc[0]["hit_rate"],
            })
    return pd.DataFrame(token_rows)


def generate_results_markdown(results_df: pd.DataFrame, panel: pd.DataFrame) -> str:
    """Generate the full results markdown report."""
    lines = []
    lines.append("# OI Rate-of-Change Divergence Signal -- Research Results")
    lines.append("")
    lines.append(f"**Generated**: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append(f"**Tokens**: {', '.join(TOKENS)}")
    lines.append(f"**IS period**: 2024-01-01 to 2025-06-30")
    lines.append(f"**OOS period**: 2025-07-01 to 2026-03-23")
    lines.append(f"**Total daily observations**: {len(panel):,}")
    lines.append("")

    # Data summary
    lines.append("## Data Summary")
    lines.append("")
    lines.append("| Token | Daily Obs | IS Obs | OOS Obs | OI Min Date | OI Max Date |")
    lines.append("|-------|-----------|--------|---------|-------------|-------------|")
    for token in TOKENS:
        td = panel[panel["token"] == token]
        is_count = len(td[td.index < HOLDOUT_DATE])
        oos_count = len(td[td.index >= HOLDOUT_DATE])
        lines.append(
            f"| {token} | {len(td)} | {is_count} | {oos_count} "
            f"| {td.index.min().strftime('%Y-%m-%d')} | {td.index.max().strftime('%Y-%m-%d')} |"
        )
    lines.append("")

    # Top signals
    lines.append("## Top 10 Signals (Ranked by OOS |IC|, sign-consistent preferred)")
    lines.append("")
    top = find_top_signals(results_df, n=15)
    lines.append("| Rank | Signal | Horizon | IC (IS) | IC (OOS) | t (IS) | t (OOS) | Hit% IS | Hit% OOS | Consistent |")
    lines.append("|------|--------|---------|---------|----------|--------|---------|---------|----------|------------|")
    for i, (_, row) in enumerate(top.iterrows(), 1):
        consistent = "Yes" if row["sign_consistent"] else "NO"
        lines.append(
            f"| {i} | {row['signal']} | {row['horizon']} "
            f"| {row['ic_is']:.4f} | {row['ic_oos']:.4f} "
            f"| {row['t_is']:.2f} | {row['t_oos']:.2f} "
            f"| {row['hit_is'] * 100:.1f}% | {row['hit_oos'] * 100:.1f}% "
            f"| {consistent} |"
        )
    lines.append("")

    # Detailed results for top 3 signals: per-token breakdown
    lines.append("## Per-Token Breakdown (Top 3 Signals)")
    lines.append("")
    for i, (_, row) in enumerate(top.head(3).iterrows(), 1):
        sig = row["signal"]
        hor = row["horizon"]
        lines.append(f"### #{i}: {sig} @ {hor}")
        lines.append("")
        token_df = per_token_summary(results_df, sig, hor)
        if len(token_df) > 0:
            lines.append("| Token | IC (IS) | IC (OOS) | t (IS) | t (OOS) | Hit% IS | Hit% OOS |")
            lines.append("|-------|---------|----------|--------|---------|---------|----------|")
            for _, tr in token_df.iterrows():
                ic_is = f"{tr['ic_is']:.4f}" if not np.isnan(tr['ic_is']) else "N/A"
                ic_oos = f"{tr['ic_oos']:.4f}" if not np.isnan(tr['ic_oos']) else "N/A"
                t_is = f"{tr['t_is']:.2f}" if not np.isnan(tr['t_is']) else "N/A"
                t_oos = f"{tr['t_oos']:.2f}" if not np.isnan(tr['t_oos']) else "N/A"
                hit_is = f"{tr['hit_is'] * 100:.1f}%" if not np.isnan(tr['hit_is']) else "N/A"
                hit_oos = f"{tr['hit_oos'] * 100:.1f}%" if not np.isnan(tr['hit_oos']) else "N/A"
                lines.append(
                    f"| {tr['token']} | {ic_is} | {ic_oos} | {t_is} | {t_oos} | {hit_is} | {hit_oos} |"
                )
        lines.append("")

    # Full aggregate results table
    lines.append("## Full Aggregate Results (IS vs OOS)")
    lines.append("")
    lines.append("```")
    lines.append(format_results_table(results_df))
    lines.append("```")
    lines.append("")

    # Signal interpretation guide
    lines.append("## Signal Interpretation")
    lines.append("")
    lines.append("| Signal | Interpretation |")
    lines.append("|--------|---------------|")
    lines.append("| `oi_pct_{w}d` | Raw OI rate of change. Positive = growing open interest. |")
    lines.append("| `oi_div_unsigned_{w}d` | OI growth minus abs(price change). Positive = OI growing faster than price moves. |")
    lines.append("| `oi_div_signed_{w}d` | OI growth minus price change. High positive = OI up + price down (bearish leverage). |")
    lines.append("| `oi_accel_{w}d` | Second derivative of OI. Positive = OI growth accelerating. |")
    lines.append("| `oi_div_vol_adj_{w}d` | OI change divided by price volatility. Leverage intensity. |")
    lines.append("| `oi_div_funding_{w}d` | OI divergence * |funding z-score|. Divergence amplified by funding extreme. |")
    lines.append("| `oi_div_signed_funding_{w}d` | Signed divergence * funding z-score. Directional leverage + funding signal. |")
    lines.append("| `oi_vol_div_{w}d` | OI growth minus volume growth. OI up + volume down = hollow leverage. |")
    lines.append("| `funding_zscore` | Current funding rate z-score vs 30d rolling. |")
    lines.append("| `funding_cum_3d` | Cumulative funding over ~3 days. |")
    lines.append("")

    # IC interpretation guide
    lines.append("## IC Interpretation Guide")
    lines.append("")
    lines.append("- |IC| > 0.05: Potentially meaningful signal")
    lines.append("- |IC| > 0.10: Strong signal (rare in crypto cross-sectional)")
    lines.append("- |t-stat| > 2.0: Statistically significant at 5% level")
    lines.append("- Hit rate > 52%: Slight directional edge")
    lines.append("- **Sign consistency IS->OOS is more important than IC magnitude**")
    lines.append("- Negative IC on divergence signals = mean reversion (OI buildup predicts reversal)")
    lines.append("- Positive IC on divergence signals = trend confirmation (OI buildup predicts continuation)")
    lines.append("")

    return "\n".join(lines)


# ============================================================================
# Entry Point
# ============================================================================


def main():
    print("=" * 80)
    print("OI Rate-of-Change Divergence Signal Analysis")
    print("=" * 80)
    print()

    # Step 1: Build panel
    print("[1/4] Building daily panel from hourly data...")
    panel = build_panel()
    print(f"  Panel shape: {panel.shape}")
    print(f"  Date range: {panel.index.min()} to {panel.index.max()}")
    print(f"  Tokens: {panel['token'].nunique()}")
    print(f"  IS observations: {len(panel[panel.index < HOLDOUT_DATE]):,}")
    print(f"  OOS observations: {len(panel[panel.index >= HOLDOUT_DATE]):,}")
    print()

    # Save intermediate panel
    panel_path = os.path.join(OUTPUT_DIR, "oi_divergence_panel.parquet")
    panel.reset_index().to_parquet(panel_path)
    print(f"  Saved panel to {panel_path}")
    print()

    # Step 2: Run signal evaluation
    print("[2/4] Evaluating signals...")
    signal_cols = get_signal_columns()
    print(f"  {len(signal_cols)} signals x {len(FWD_HORIZONS)} horizons x 2 samples x {len(TOKENS)} tokens")
    results = run_analysis(panel)
    print(f"  Total evaluations: {len(results):,}")
    print()

    # Save results
    results_path = os.path.join(OUTPUT_DIR, "oi_divergence_raw_results.parquet")
    results.to_parquet(results_path)
    print(f"  Saved raw results to {results_path}")
    print()

    # Step 3: Display results
    print("[3/4] Results Summary")
    print()

    # Aggregate table
    print("--- AGGREGATE RESULTS (IS vs OOS) ---")
    print(format_results_table(results))
    print()

    # Top signals
    print("--- TOP 15 SIGNALS (by OOS |IC|, sign-consistent preferred) ---")
    print()
    top = find_top_signals(results, n=15)
    print(top.to_string(index=False, float_format="%.4f"))
    print()

    # Per-token breakdown for top 3
    print("--- PER-TOKEN BREAKDOWN (Top 3 signals) ---")
    for i, (_, row) in enumerate(top.head(3).iterrows(), 1):
        sig = row["signal"]
        hor = row["horizon"]
        print(f"\n#{i}: {sig} @ {hor}")
        token_df = per_token_summary(results, sig, hor)
        if len(token_df) > 0:
            print(token_df.to_string(index=False, float_format="%.4f"))

    print()

    # Step 4: Generate markdown report
    print("[4/4] Generating results report...")
    md = generate_results_markdown(results, panel)
    md_path = os.path.join(OUTPUT_DIR, "oi_divergence_results.md")
    with open(md_path, "w") as f:
        f.write(md)
    print(f"  Saved report to {md_path}")
    print()

    # Rolling IC stability for best signal
    if len(top) > 0:
        best_sig = top.iloc[0]["signal"]
        best_hor = top.iloc[0]["horizon"]
        fwd_col = f"fwd_ret_{best_hor}"
        print(f"--- ROLLING IC STABILITY for best signal: {best_sig} @ {best_hor} ---")
        ric = compute_rolling_ic(panel[best_sig], panel[fwd_col], window=60)
        valid_ric = ric.dropna()
        if len(valid_ric) > 0:
            print(f"  Rolling IC (60d window): mean={valid_ric.mean():.4f}, std={valid_ric.std():.4f}")
            print(f"  % positive IC windows: {(valid_ric > 0).mean() * 100:.1f}%")
            print(f"  % |IC| > 0.05 windows: {(valid_ric.abs() > 0.05).mean() * 100:.1f}%")
            # IS vs OOS rolling IC
            is_ric = valid_ric[valid_ric.index < HOLDOUT_DATE]
            oos_ric = valid_ric[valid_ric.index >= HOLDOUT_DATE]
            if len(is_ric) > 0:
                print(f"  IS rolling IC: mean={is_ric.mean():.4f}, std={is_ric.std():.4f}")
            if len(oos_ric) > 0:
                print(f"  OOS rolling IC: mean={oos_ric.mean():.4f}, std={oos_ric.std():.4f}")
        print()

    print("=" * 80)
    print("DONE. All results saved.")
    print("=" * 80)


if __name__ == "__main__":
    main()
