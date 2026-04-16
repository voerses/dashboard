#!/usr/bin/env python3
"""
Gate 0 IC Test: Taker Buy Ratio as Per-Token Predictive Signal
==============================================================
Hypothesis: The taker buy ratio (fraction of volume from aggressive buyers)
captures order flow information. Persistent high (>60%) signals momentum
continuation; persistent low (<40%) signals selloff continuation.

Data: Binance taker buy/sell volume (hourly), merged with 1h OHLCV prices.
Tokens: Top 10 by volume (BTC, ETH, SOL, BNB, DOGE, XRP, ADA, AVAX, LINK, DOT).
IS/OOS split: 2025-01-01 (but taker data starts 2025-07-01, so we use
2025-11-01 as the midpoint split).

Signal variants:
  taker_raw          — raw taker buy ratio per bar
  taker_zscore_24h   — rolling 24h z-score of taker buy ratio
  taker_zscore_72h   — rolling 72h z-score of taker buy ratio
  taker_persistence  — rolling 12h mean of (taker > 0.5)

Forward return horizons: 4h, 8h, 24h, 48h (non-overlapping).
IC: Spearman rank correlation per non-overlapping window.

Kill criteria:
  KILL if best |IC| < 0.03 OR best |t-stat| < 2.0 OR IS/OOS sign flip
  PASS if |IC| > 0.03 AND |t-stat| > 2.0 AND consistent IS->OOS
"""

import os
import warnings
import numpy as np
import pandas as pd
from scipy import stats

warnings.filterwarnings("ignore")

# ============================================================================
# CONFIGURATION
# ============================================================================

TAKER_DIR = "/workspace/crypto_backtest/data/alternative/binance_positioning/extended"
PRICE_DIR = "/workspace/crypto_backtest/data/perp/1h_cache"

TOKENS = [
    ("BTC", "BTCUSDT"),
    ("ETH", "ETHUSDT"),
    ("SOL", "SOLUSDT"),
    ("BNB", "BNBUSDT"),
    ("DOGE", "DOGEUSDT"),
    ("XRP", "XRPUSDT"),
    ("ADA", "ADAUSDT"),
    ("AVAX", "AVAXUSDT"),
    ("LINK", "LINKUSDT"),
    ("DOT", "DOTUSDT"),
]

# Forward return horizons in hours
FWD_HORIZONS = {"4h": 4, "8h": 8, "24h": 24, "48h": 48}

# IS/OOS split — taker data runs 2025-07-01 to 2026-02-28 (~8 months)
# Midpoint split at 2025-11-01
SPLIT_DATE = pd.Timestamp("2025-11-01")


# ============================================================================
# DATA LOADING
# ============================================================================

def load_token(ticker: str, symbol: str) -> pd.DataFrame:
    """Load and merge taker data with price data for a single token."""
    # Load taker data
    taker_file = os.path.join(TAKER_DIR, f"{symbol}_taker_buysell.parquet")
    taker = pd.read_parquet(taker_file)
    taker["timestamp"] = pd.to_datetime(taker["timestamp"]).dt.tz_localize(None)
    taker = taker.set_index("timestamp").sort_index()

    # Load price data
    price_file = os.path.join(PRICE_DIR, f"{ticker}_1h.parquet")
    price = pd.read_parquet(price_file)
    price.index = pd.to_datetime(price.index)
    price = price.sort_index()

    # Merge on datetime index
    merged = taker.join(price[["close"]], how="inner")
    merged["ticker"] = ticker

    # Compute taker buy ratio = buy_vol / total_vol
    buy_vol = merged["taker_buy_base_vol"]
    total_vol = merged["volume"]  # total volume from taker data
    merged["taker_buy_ratio"] = buy_vol / total_vol.replace(0, np.nan)

    return merged


def load_all() -> dict:
    """Load data for all target tokens."""
    data = {}
    for ticker, symbol in TOKENS:
        try:
            df = load_token(ticker, symbol)
            if len(df) > 100:
                data[ticker] = df
                print(f"  {ticker:>5s}: {len(df):>6,} rows  "
                      f"{df.index.min().date()} to {df.index.max().date()}  "
                      f"taker_buy_ratio median={df['taker_buy_ratio'].median():.4f}")
        except Exception as e:
            print(f"  {ticker:>5s}: FAILED - {e}")
    return data


# ============================================================================
# SIGNAL CONSTRUCTION
# ============================================================================

def compute_signals(df: pd.DataFrame) -> pd.DataFrame:
    """Compute all 4 signal variants from taker buy ratio."""
    tbr = df["taker_buy_ratio"].copy()
    signals = pd.DataFrame(index=df.index)

    # Signal 1: Raw taker buy ratio
    signals["taker_raw"] = tbr

    # Signal 2: Rolling 24h z-score
    roll_24 = tbr.rolling(24, min_periods=12)
    signals["taker_zscore_24h"] = (tbr - roll_24.mean()) / roll_24.std().replace(0, np.nan)

    # Signal 3: Rolling 72h z-score
    roll_72 = tbr.rolling(72, min_periods=36)
    signals["taker_zscore_72h"] = (tbr - roll_72.mean()) / roll_72.std().replace(0, np.nan)

    # Signal 4: Persistence — rolling 12h mean of (taker > 0.5)
    # Measures sustained buying pressure
    signals["taker_persistence"] = (tbr > 0.5).astype(float).rolling(12, min_periods=6).mean()

    return signals


# ============================================================================
# FORWARD RETURNS (NON-OVERLAPPING)
# ============================================================================

def compute_forward_returns(close: pd.Series) -> pd.DataFrame:
    """Compute forward log returns at multiple horizons."""
    fwd = pd.DataFrame(index=close.index)
    for label, hours in FWD_HORIZONS.items():
        fwd[label] = np.log(close.shift(-hours) / close)
    return fwd


# ============================================================================
# IC CALCULATION (PER-TOKEN TIME-SERIES)
# ============================================================================

def compute_ic_nonoverlapping(signal: pd.Series, fwd_ret: pd.Series,
                               horizon_hours: int) -> dict:
    """
    Compute IC using non-overlapping return windows for statistical validity.

    For a given horizon H, we sample every H-th bar to avoid overlapping
    forward returns, then compute Spearman rank IC on each non-overlapping
    chunk of 20 observations.
    """
    # Align and drop NaN
    aligned = pd.concat([signal.rename("sig"), fwd_ret.rename("ret")], axis=1).dropna()
    if len(aligned) < 30:
        return {"ic": np.nan, "t_stat": np.nan, "n_obs": 0, "n_windows": 0}

    # Subsample every H bars for non-overlapping returns
    subsampled = aligned.iloc[::horizon_hours]

    if len(subsampled) < 20:
        # Fall back to full sample Spearman
        ic, _ = stats.spearmanr(aligned["sig"], aligned["ret"])
        return {"ic": ic, "t_stat": np.nan, "n_obs": len(aligned), "n_windows": 0}

    # Compute rolling IC on non-overlapping chunks of 20 observations
    window = 20
    ics = []
    for i in range(0, len(subsampled) - window + 1, window):
        chunk = subsampled.iloc[i:i + window]
        if chunk["sig"].std() > 0 and chunk["ret"].std() > 0:
            r, _ = stats.spearmanr(chunk["sig"], chunk["ret"])
            if not np.isnan(r):
                ics.append(r)

    ics = np.array(ics)
    if len(ics) < 3:
        # Not enough windows for a t-stat; compute single IC
        ic, _ = stats.spearmanr(subsampled["sig"], subsampled["ret"])
        return {"ic": ic, "t_stat": np.nan, "n_obs": len(subsampled), "n_windows": len(ics)}

    ic_mean = np.mean(ics)
    ic_std = np.std(ics, ddof=1)
    t_stat = ic_mean / (ic_std / np.sqrt(len(ics))) if ic_std > 0 else 0.0

    return {
        "ic": ic_mean,
        "t_stat": t_stat,
        "n_obs": len(subsampled),
        "n_windows": len(ics),
    }


# ============================================================================
# MAIN
# ============================================================================

def main():
    print("=" * 80)
    print("GATE 0 IC TEST: Taker Buy Ratio as Per-Token Predictive Signal")
    print("=" * 80)

    # ── Load data ─────────────────────────────────────────────────────────
    print("\n[1] Loading data...")
    token_data = load_all()
    print(f"\n  Loaded {len(token_data)} tokens")

    if len(token_data) == 0:
        print("  ERROR: No tokens loaded. Aborting.")
        return

    # ── Compute signals & forward returns ─────────────────────────────────
    print(f"\n[2] Computing signals and forward returns...")
    print(f"    IS period: before {SPLIT_DATE.date()}")
    print(f"    OOS period: {SPLIT_DATE.date()} onward")

    signal_names = ["taker_raw", "taker_zscore_24h", "taker_zscore_72h", "taker_persistence"]
    horizon_labels = list(FWD_HORIZONS.keys())

    # Storage for results
    results_rows = []
    per_token_data = {}  # for per-token breakdown

    for ticker, df in token_data.items():
        signals = compute_signals(df)
        fwd_rets = compute_forward_returns(df["close"])
        per_token_data[ticker] = (signals, fwd_rets)

        # Split IS / OOS
        is_mask = signals.index < SPLIT_DATE
        oos_mask = signals.index >= SPLIT_DATE

        for sig_name in signal_names:
            for hz_label, hz_hours in FWD_HORIZONS.items():
                # IS
                is_ic = compute_ic_nonoverlapping(
                    signals.loc[is_mask, sig_name],
                    fwd_rets.loc[is_mask, hz_label],
                    hz_hours,
                )
                # OOS
                oos_ic = compute_ic_nonoverlapping(
                    signals.loc[oos_mask, sig_name],
                    fwd_rets.loc[oos_mask, hz_label],
                    hz_hours,
                )

                results_rows.append({
                    "token": ticker,
                    "signal": sig_name,
                    "horizon": hz_label,
                    "is_ic": is_ic["ic"],
                    "is_tstat": is_ic["t_stat"],
                    "is_nobs": is_ic["n_obs"],
                    "is_nwin": is_ic["n_windows"],
                    "oos_ic": oos_ic["ic"],
                    "oos_tstat": oos_ic["t_stat"],
                    "oos_nobs": oos_ic["n_obs"],
                    "oos_nwin": oos_ic["n_windows"],
                })

    df_results = pd.DataFrame(results_rows)

    # ── Aggregate: pool across tokens ─────────────────────────────────────
    print("\n[3] Aggregating pooled IC across tokens...")

    agg_rows = []
    for sig_name in signal_names:
        for hz_label, hz_hours in FWD_HORIZONS.items():
            subset = df_results[
                (df_results["signal"] == sig_name) & (df_results["horizon"] == hz_label)
            ]
            # Pool all per-token signal/fwd data, compute a single IC
            all_sig_is, all_ret_is = [], []
            all_sig_oos, all_ret_oos = [], []

            for ticker in token_data:
                signals, fwd_rets = per_token_data[ticker]
                is_mask = signals.index < SPLIT_DATE
                oos_mask = signals.index >= SPLIT_DATE

                # Subsample for non-overlapping
                is_df = pd.concat([
                    signals.loc[is_mask, sig_name],
                    fwd_rets.loc[is_mask, hz_label],
                ], axis=1).dropna().iloc[::hz_hours]

                oos_df = pd.concat([
                    signals.loc[oos_mask, sig_name],
                    fwd_rets.loc[oos_mask, hz_label],
                ], axis=1).dropna().iloc[::hz_hours]

                all_sig_is.extend(is_df.iloc[:, 0].values)
                all_ret_is.extend(is_df.iloc[:, 1].values)
                all_sig_oos.extend(oos_df.iloc[:, 0].values)
                all_ret_oos.extend(oos_df.iloc[:, 1].values)

            # Pooled IC
            is_ic, is_pval = (np.nan, np.nan)
            oos_ic, oos_pval = (np.nan, np.nan)

            if len(all_sig_is) > 30:
                is_ic, is_pval = stats.spearmanr(all_sig_is, all_ret_is)
            if len(all_sig_oos) > 30:
                oos_ic, oos_pval = stats.spearmanr(all_sig_oos, all_ret_oos)

            # Mean of per-token ICs and t-stat
            valid_is = subset["is_ic"].dropna()
            valid_oos = subset["oos_ic"].dropna()

            mean_is_ic = valid_is.mean() if len(valid_is) > 0 else np.nan
            mean_oos_ic = valid_oos.mean() if len(valid_oos) > 0 else np.nan

            # t-stat from distribution of per-token ICs (more conservative)
            if len(valid_is) > 2:
                is_tstat = mean_is_ic / (valid_is.std(ddof=1) / np.sqrt(len(valid_is))) \
                    if valid_is.std() > 0 else 0.0
            else:
                is_tstat = np.nan

            if len(valid_oos) > 2:
                oos_tstat = mean_oos_ic / (valid_oos.std(ddof=1) / np.sqrt(len(valid_oos))) \
                    if valid_oos.std() > 0 else 0.0
            else:
                oos_tstat = np.nan

            # Sign consistency
            if not np.isnan(mean_is_ic) and not np.isnan(mean_oos_ic):
                consistent = np.sign(mean_is_ic) == np.sign(mean_oos_ic)
            else:
                consistent = False

            agg_rows.append({
                "signal": sig_name,
                "horizon": hz_label,
                "is_ic_pooled": is_ic,
                "oos_ic_pooled": oos_ic,
                "is_ic_mean": mean_is_ic,
                "oos_ic_mean": mean_oos_ic,
                "is_tstat": is_tstat,
                "oos_tstat": oos_tstat,
                "consistent": consistent,
                "n_tokens": len(valid_oos),
                "is_nobs": len(all_sig_is),
                "oos_nobs": len(all_sig_oos),
            })

    df_agg = pd.DataFrame(agg_rows)

    # ── Print summary table ───────────────────────────────────────────────
    print("\n" + "=" * 110)
    print("SUMMARY TABLE: Pooled IC across tokens (mean of per-token ICs, t-stat from cross-token distribution)")
    print("=" * 110)
    print(f"{'Signal':<22s} | {'Horizon':>7s} | {'IS IC':>8s} | {'OOS IC':>8s} | "
          f"{'IS t-stat':>9s} | {'OOS t-stat':>10s} | {'Consistent':>10s} | Verdict")
    print("-" * 110)

    best_abs_ic = 0
    best_abs_tstat = 0
    any_pass = False
    best_signal_row = None

    for _, row in df_agg.iterrows():
        verdict = ""
        is_ic = row["is_ic_mean"]
        oos_ic = row["oos_ic_mean"]
        is_t = row["is_tstat"]
        oos_t = row["oos_tstat"]
        cons = row["consistent"]

        # Track best
        abs_oos_ic = abs(oos_ic) if not np.isnan(oos_ic) else 0
        abs_oos_t = abs(oos_t) if not np.isnan(oos_t) else 0

        if abs_oos_ic > best_abs_ic:
            best_abs_ic = abs_oos_ic
            best_signal_row = row

        if abs_oos_t > best_abs_tstat:
            best_abs_tstat = abs_oos_t

        # Verdict
        if abs_oos_ic >= 0.03 and abs_oos_t >= 2.0 and cons:
            verdict = "PASS"
            any_pass = True
        elif abs_oos_ic < 0.03:
            verdict = "KILL (low IC)"
        elif abs_oos_t < 2.0:
            verdict = "KILL (low t-stat)"
        elif not cons:
            verdict = "KILL (sign flip)"
        else:
            verdict = "KILL"

        # Format
        is_ic_s = f"{is_ic:+.4f}" if not np.isnan(is_ic) else "   N/A"
        oos_ic_s = f"{oos_ic:+.4f}" if not np.isnan(oos_ic) else "   N/A"
        is_t_s = f"{is_t:+.2f}" if not np.isnan(is_t) else "   N/A"
        oos_t_s = f"{oos_t:+.2f}" if not np.isnan(oos_t) else "    N/A"
        cons_s = "YES" if cons else "NO"

        print(f"{row['signal']:<22s} | {row['horizon']:>7s} | {is_ic_s:>8s} | {oos_ic_s:>8s} | "
              f"{is_t_s:>9s} | {oos_t_s:>10s} | {cons_s:>10s} | {verdict}")

    # ── Per-token breakdown for best signal ───────────────────────────────
    print("\n" + "=" * 110)
    if best_signal_row is not None:
        best_sig = best_signal_row["signal"]
        best_hz = best_signal_row["horizon"]
        print(f"PER-TOKEN IC BREAKDOWN: {best_sig} @ {best_hz}")
    else:
        best_sig = "taker_raw"
        best_hz = "24h"
        print(f"PER-TOKEN IC BREAKDOWN: {best_sig} @ {best_hz} (default)")
    print("=" * 110)

    token_subset = df_results[
        (df_results["signal"] == best_sig) & (df_results["horizon"] == best_hz)
    ].sort_values("oos_ic", ascending=False)

    print(f"{'Token':>6s} | {'IS IC':>8s} | {'IS t-stat':>9s} | {'OOS IC':>8s} | {'OOS t-stat':>10s} | "
          f"{'IS nobs':>7s} | {'OOS nobs':>8s}")
    print("-" * 80)

    for _, row in token_subset.iterrows():
        is_ic_s = f"{row['is_ic']:+.4f}" if not np.isnan(row['is_ic']) else "   N/A"
        oos_ic_s = f"{row['oos_ic']:+.4f}" if not np.isnan(row['oos_ic']) else "   N/A"
        is_t_s = f"{row['is_tstat']:+.2f}" if not np.isnan(row['is_tstat']) else "   N/A"
        oos_t_s = f"{row['oos_tstat']:+.2f}" if not np.isnan(row['oos_tstat']) else "    N/A"
        print(f"{row['token']:>6s} | {is_ic_s:>8s} | {is_t_s:>9s} | {oos_ic_s:>8s} | {oos_t_s:>10s} | "
              f"{int(row['is_nobs']):>7d} | {int(row['oos_nobs']):>8d}")

    # ── Also show all 4 signal variants per-token IC (OOS only) ──────────
    print("\n" + "=" * 110)
    print("OOS IC BY TOKEN AND SIGNAL (best horizon per signal)")
    print("=" * 110)

    # For each signal, find the best horizon, then show per-token
    for sig_name in signal_names:
        sig_agg = df_agg[df_agg["signal"] == sig_name]
        best_hz_row = sig_agg.loc[sig_agg["oos_ic_mean"].abs().idxmax()]
        hz = best_hz_row["horizon"]

        print(f"\n  {sig_name} @ {hz}  (pooled OOS IC={best_hz_row['oos_ic_mean']:+.4f}, "
              f"t={best_hz_row['oos_tstat']:+.2f})")

        subset = df_results[
            (df_results["signal"] == sig_name) & (df_results["horizon"] == hz)
        ].sort_values("oos_ic", ascending=False)

        for _, row in subset.iterrows():
            oos_ic_s = f"{row['oos_ic']:+.4f}" if not np.isnan(row['oos_ic']) else "   N/A"
            print(f"    {row['token']:>5s}  OOS IC={oos_ic_s}")

    # ── Final gate verdict ────────────────────────────────────────────────
    print("\n" + "=" * 110)
    print("GATE 0 VERDICT")
    print("=" * 110)
    print(f"  Best |OOS IC|:     {best_abs_ic:.4f}  (threshold: > 0.03)")
    print(f"  Best |OOS t-stat|: {best_abs_tstat:.2f}  (threshold: > 2.0)")

    if any_pass:
        print("\n  >>> PASS: At least one signal/horizon passes all criteria.")
        print("  >>> Advance to Gate 1 for deeper analysis.")
    else:
        # Check individual reasons
        reasons = []
        if best_abs_ic < 0.03:
            reasons.append(f"best |IC| = {best_abs_ic:.4f} < 0.03")
        if best_abs_tstat < 2.0:
            reasons.append(f"best |t-stat| = {best_abs_tstat:.2f} < 2.0")

        # Check sign flips for best signal
        if best_signal_row is not None and not best_signal_row["consistent"]:
            reasons.append("IS/OOS sign flip for best signal")

        print(f"\n  >>> KILL: {'; '.join(reasons) if reasons else 'no signal passes all three criteria'}")
        print("  >>> Taker buy ratio does NOT have reliable per-token predictive power at these horizons.")


if __name__ == "__main__":
    main()
