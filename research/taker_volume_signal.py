#!/usr/bin/env python3
"""
Taker Buy/Sell Volume as a Trading Signal — Quantitative Research
=================================================================
Tests 6 signal variants derived from Binance taker buy/sell volume data
against forward returns at multiple horizons, with temporal holdout validation.

Data: 19 tokens, hourly, 2025-07-01 to 2026-02-28
Price: Hyperliquid/Binance perp 1h OHLCV from 1h_cache
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

TAKER_DIR = "/workspace/crypto_backtest/data/alternative/binance_positioning/extended"
PRICE_DIR = "/workspace/crypto_backtest/data/perp/1h_cache"
OUTPUT_DIR = "/workspace/crypto_backtest/research"

TOKENS = [
    "AAVEUSDT", "ADAUSDT", "ARBUSDT", "AVAXUSDT", "BNBUSDT",
    "BTCUSDT", "DOGEUSDT", "DOTUSDT", "ETHUSDT", "INJUSDT",
    "LINKUSDT", "LTCUSDT", "NEARUSDT", "OPUSDT", "SOLUSDT",
    "SUIUSDT", "UNIUSDT", "WIFUSDT", "XRPUSDT",
]

# Forward return horizons in hours (1d=24h, 3d=72h, 7d=168h, 14d=336h)
FWD_HORIZONS = {"1d": 24, "3d": 72, "7d": 168, "14d": 336}

# Rolling window sizes for z-score (in hours)
ZSCORE_WINDOWS = {"10d": 240, "20d": 480}

# Momentum lookbacks (in hours)
MOMENTUM_LOOKBACKS = {"1d": 24, "3d": 72, "7d": 168}

# Resample to daily for cleaner signal analysis (reduce autocorrelation)
RESAMPLE_FREQ = "24h"


# ============================================================================
# DATA LOADING
# ============================================================================

def load_token_data(token: str) -> pd.DataFrame:
    """Load and merge taker data with price data for a single token."""
    # Load taker data
    taker_file = os.path.join(TAKER_DIR, f"{token}_taker_buysell.parquet")
    taker = pd.read_parquet(taker_file)
    taker["timestamp"] = pd.to_datetime(taker["timestamp"]).dt.tz_localize(None)
    taker = taker.set_index("timestamp").sort_index()

    # Load price data
    base = token.replace("USDT", "")
    price_file = os.path.join(PRICE_DIR, f"{base}_1h.parquet")
    price = pd.read_parquet(price_file)
    price.index = pd.to_datetime(price.index)
    price = price.sort_index()

    # Merge on datetime index
    merged = taker.join(price[["close", "volume"]], how="inner", rsuffix="_price")
    merged = merged.rename(columns={"volume_price": "price_volume"})

    # Compute taker buy volume (taker_buy_base_vol is already buy vol)
    # taker_sell_vol is sell vol, volume is total
    # buySellRatio = buy / sell (confirmed from data)
    merged["taker_buy_vol"] = merged["taker_buy_base_vol"]
    merged["taker_sell_vol_raw"] = merged["taker_sell_vol"]
    merged["taker_total_vol"] = merged["taker_buy_vol"] + merged["taker_sell_vol_raw"]

    merged["token"] = token
    return merged


def load_all_tokens() -> dict:
    """Load data for all tokens."""
    data = {}
    for token in TOKENS:
        try:
            df = load_token_data(token)
            if len(df) > 100:
                data[token] = df
                print(f"  {token}: {len(df)} rows, {df.index.min()} to {df.index.max()}")
        except Exception as e:
            print(f"  {token}: FAILED - {e}")
    return data


# ============================================================================
# SIGNAL CONSTRUCTION
# ============================================================================

def compute_signals_hourly(df: pd.DataFrame) -> pd.DataFrame:
    """Compute all signal variants at hourly frequency."""
    signals = pd.DataFrame(index=df.index)

    # --- Signal 1: Taker Buy/Sell Ratio ---
    signals["buy_sell_ratio"] = df["taker_buy_vol"] / df["taker_sell_vol_raw"].replace(0, np.nan)

    # --- Signal 2: Net Taker Volume (normalized imbalance) ---
    signals["net_taker_vol"] = (
        (df["taker_buy_vol"] - df["taker_sell_vol_raw"])
        / df["taker_total_vol"].replace(0, np.nan)
    )

    # --- Signal 3: Z-Score of net taker volume ---
    for label, window in ZSCORE_WINDOWS.items():
        net = signals["net_taker_vol"]
        roll_mean = net.rolling(window, min_periods=window // 2).mean()
        roll_std = net.rolling(window, min_periods=window // 2).std()
        signals[f"net_taker_zscore_{label}"] = (net - roll_mean) / roll_std.replace(0, np.nan)

    # --- Signal 4: Taker Volume Momentum (change in buy/sell ratio) ---
    for label, lookback in MOMENTUM_LOOKBACKS.items():
        signals[f"ratio_momentum_{label}"] = (
            signals["buy_sell_ratio"] - signals["buy_sell_ratio"].shift(lookback)
        )

    # --- Signal 5: Volume-Weighted Taker Pressure ---
    # Net taker vol * total volume (dollar-scaled pressure)
    signals["vol_weighted_pressure"] = (
        (df["taker_buy_vol"] - df["taker_sell_vol_raw"]) * df["taker_total_vol"]
    )
    # Normalize by rolling std to make cross-token comparable
    roll_std = signals["vol_weighted_pressure"].rolling(240, min_periods=120).std()
    signals["vol_weighted_pressure_norm"] = (
        signals["vol_weighted_pressure"] / roll_std.replace(0, np.nan)
    )

    return signals


def resample_to_daily(df: pd.DataFrame, signals: pd.DataFrame) -> tuple:
    """Resample hourly data to daily, using end-of-day values for signals and close for price."""
    # Price: use last close of the day
    daily_price = df["close"].resample(RESAMPLE_FREQ).last()

    # Signals: for ratios use mean, for z-scores use last
    daily_signals = pd.DataFrame(index=daily_price.index)

    # Buy/sell ratio: daily mean
    daily_signals["buy_sell_ratio"] = signals["buy_sell_ratio"].resample(RESAMPLE_FREQ).mean()

    # Net taker vol: daily mean of normalized imbalance
    daily_signals["net_taker_vol"] = signals["net_taker_vol"].resample(RESAMPLE_FREQ).mean()

    # Z-scores: last value of the day
    for label in ZSCORE_WINDOWS:
        col = f"net_taker_zscore_{label}"
        daily_signals[col] = signals[col].resample(RESAMPLE_FREQ).last()

    # Momentum: last value of day
    for label in MOMENTUM_LOOKBACKS:
        col = f"ratio_momentum_{label}"
        daily_signals[col] = signals[col].resample(RESAMPLE_FREQ).last()

    # Volume-weighted pressure: sum over day (total pressure)
    daily_signals["vol_weighted_pressure_norm"] = (
        signals["vol_weighted_pressure_norm"].resample(RESAMPLE_FREQ).mean()
    )

    return daily_price, daily_signals


# ============================================================================
# FORWARD RETURNS
# ============================================================================

def compute_forward_returns(daily_price: pd.Series) -> pd.DataFrame:
    """Compute forward log returns at multiple horizons (in days)."""
    fwd_rets = pd.DataFrame(index=daily_price.index)
    for label, hours in FWD_HORIZONS.items():
        days = hours // 24
        fwd_rets[f"fwd_{label}"] = np.log(daily_price.shift(-days) / daily_price)
    return fwd_rets


# ============================================================================
# INFORMATION COEFFICIENT CALCULATION
# ============================================================================

def compute_ic(signal: pd.Series, fwd_ret: pd.Series) -> dict:
    """Compute IC metrics between a signal and forward return series."""
    # Align and drop NaN
    aligned = pd.concat([signal, fwd_ret], axis=1).dropna()
    if len(aligned) < 20:
        return {"ic_mean": np.nan, "ic_tstat": np.nan, "hit_rate": np.nan, "n_obs": 0}

    sig = aligned.iloc[:, 0]
    ret = aligned.iloc[:, 1]

    # Spearman rank correlation (single IC)
    ic, pval = stats.spearmanr(sig, ret)

    # Rolling IC for t-stat (use 20-day windows)
    rolling_ics = []
    window = 20
    for i in range(0, len(aligned) - window + 1, window):
        chunk_sig = sig.iloc[i : i + window]
        chunk_ret = ret.iloc[i : i + window]
        if chunk_sig.std() > 0 and chunk_ret.std() > 0:
            r, _ = stats.spearmanr(chunk_sig, chunk_ret)
            rolling_ics.append(r)

    rolling_ics = np.array(rolling_ics)
    if len(rolling_ics) > 2:
        ic_mean = np.nanmean(rolling_ics)
        ic_std = np.nanstd(rolling_ics, ddof=1)
        ic_tstat = ic_mean / (ic_std / np.sqrt(len(rolling_ics))) if ic_std > 0 else 0
    else:
        ic_mean = ic
        ic_tstat = 0

    # Hit rate: fraction of times signal direction matches return direction
    hit_rate = np.mean(np.sign(sig) == np.sign(ret))

    return {
        "ic_full": ic,
        "ic_mean": ic_mean,
        "ic_tstat": ic_tstat,
        "ic_pval": pval,
        "hit_rate": hit_rate,
        "n_obs": len(aligned),
        "n_windows": len(rolling_ics),
    }


def compute_panel_ic(panel_data: list, signal_col: str, horizon: str) -> dict:
    """Compute cross-sectional IC: at each date, rank signals across tokens vs forward returns."""
    # Build cross-sectional panel
    frames = []
    for token, daily_signals, fwd_rets in panel_data:
        if signal_col in daily_signals.columns and horizon in fwd_rets.columns:
            tmp = pd.DataFrame({
                "signal": daily_signals[signal_col],
                "fwd_ret": fwd_rets[horizon],
                "token": token,
            })
            frames.append(tmp)

    if not frames:
        return {"ic_mean": np.nan, "ic_tstat": np.nan, "hit_rate": np.nan, "n_obs": 0}

    panel = pd.concat(frames)
    panel = panel.dropna()

    # Time-series IC: pool all obs
    if len(panel) < 20:
        return {"ic_mean": np.nan, "ic_tstat": np.nan, "hit_rate": np.nan, "n_obs": 0}

    # Cross-sectional IC per date
    cs_ics = []
    for dt, group in panel.groupby(level=0):
        if len(group) >= 5:  # need enough tokens for meaningful rank corr
            r, _ = stats.spearmanr(group["signal"], group["fwd_ret"])
            cs_ics.append(r)

    cs_ics = np.array(cs_ics)
    if len(cs_ics) > 2:
        ic_mean = np.nanmean(cs_ics)
        ic_std = np.nanstd(cs_ics, ddof=1)
        ic_tstat = ic_mean / (ic_std / np.sqrt(len(cs_ics))) if ic_std > 0 else 0
    else:
        ic_mean = np.nan
        ic_tstat = 0

    # Hit rate across pooled panel
    hit_rate = np.mean(np.sign(panel["signal"]) == np.sign(panel["fwd_ret"]))

    return {
        "ic_mean": ic_mean,
        "ic_tstat": ic_tstat,
        "hit_rate": hit_rate,
        "n_dates": len(cs_ics),
        "n_obs": len(panel),
    }


# ============================================================================
# CROSS-TOKEN DISPERSION SIGNAL (Signal 6)
# ============================================================================

def compute_dispersion_signal(all_daily_signals: dict) -> pd.Series:
    """Compute cross-token dispersion of buy/sell ratio (crowding signal)."""
    ratios = pd.DataFrame({
        token: sigs["buy_sell_ratio"]
        for token, sigs in all_daily_signals.items()
        if "buy_sell_ratio" in sigs.columns
    })
    # Std dev across tokens at each timestamp
    dispersion = ratios.std(axis=1)
    dispersion.name = "cross_token_dispersion"
    return dispersion


# ============================================================================
# TREND OVERLAY TEST
# ============================================================================

def test_trend_overlay(panel_data: list, signal_col: str = "net_taker_vol") -> dict:
    """
    Test if taker signal improves a simple trend strategy.
    Trend = 7d return > 0 (long) or < 0 (short).
    Overlay = only take trend signal when taker volume confirms direction.
    """
    results = {"trend_only": [], "trend_plus_taker": [], "taker_confirms_pct": []}

    for token, daily_signals, fwd_rets in panel_data:
        if signal_col not in daily_signals.columns or "fwd_1d" not in fwd_rets.columns:
            continue

        df = pd.DataFrame({
            "signal": daily_signals[signal_col],
            "fwd_1d": fwd_rets["fwd_1d"],
        })

        # Trend signal: sign of trailing 7d return
        # We need trailing return — compute from fwd_rets index
        # Actually use the price data. Let's compute from close prices.
        # Since we have fwd_rets, trailing 7d ret = -fwd_7d shifted
        # Simpler: use the signal df index to compute trailing returns
        df = df.dropna()
        if len(df) < 30:
            continue

        # We need daily close to compute trailing return
        # Let's pass it through panel_data
        # For simplicity, use a rolling mean of net_taker_vol as trend proxy
        # Actually, let's compute trailing momentum directly

    # Recompute with close prices
    all_trend = []
    all_overlay = []

    for token, daily_signals, fwd_rets in panel_data:
        if signal_col not in daily_signals.columns or "fwd_1d" not in fwd_rets.columns:
            continue

        # Get close prices from the token data
        base = token.replace("USDT", "")
        price_file = os.path.join(PRICE_DIR, f"{base}_1h.parquet")
        price = pd.read_parquet(price_file)
        price.index = pd.to_datetime(price.index)
        daily_close = price["close"].resample("24h").last()

        # Trailing 7d return
        trailing_7d = np.log(daily_close / daily_close.shift(7))

        df = pd.DataFrame({
            "taker_signal": daily_signals[signal_col],
            "fwd_1d": fwd_rets["fwd_1d"],
            "trend_7d": trailing_7d,
        }).dropna()

        if len(df) < 30:
            continue

        # Trend-only strategy: long if trend_7d > 0, short if < 0
        trend_pos = np.sign(df["trend_7d"])
        trend_ret = trend_pos * df["fwd_1d"]

        # Overlay: only trade when taker confirms trend direction
        taker_sign = np.sign(df["taker_signal"])
        confirmed = trend_pos == taker_sign
        overlay_pos = trend_pos.copy()
        overlay_pos[~confirmed] = 0  # flat when taker disagrees

        overlay_ret = overlay_pos * df["fwd_1d"]

        all_trend.extend(trend_ret.values)
        all_overlay.extend(overlay_ret.values)
        results["taker_confirms_pct"].append(confirmed.mean())

    all_trend = np.array(all_trend)
    all_overlay = np.array(all_overlay)

    if len(all_trend) > 0:
        return {
            "trend_sharpe": np.mean(all_trend) / np.std(all_trend) * np.sqrt(365) if np.std(all_trend) > 0 else 0,
            "overlay_sharpe": np.mean(all_overlay) / np.std(all_overlay) * np.sqrt(365) if np.std(all_overlay) > 0 else 0,
            "trend_mean_daily": np.mean(all_trend),
            "overlay_mean_daily": np.mean(all_overlay),
            "trend_hit_rate": np.mean(all_trend > 0),
            "overlay_hit_rate": np.mean(all_overlay[all_overlay != 0] > 0) if np.any(all_overlay != 0) else 0,
            "confirmation_rate": np.mean(results["taker_confirms_pct"]) if results["taker_confirms_pct"] else 0,
            "n_trades_trend": len(all_trend),
            "n_trades_overlay": np.sum(all_overlay != 0),
        }
    return {}


# ============================================================================
# MAIN ANALYSIS
# ============================================================================

def main():
    print("=" * 80)
    print("TAKER BUY/SELL VOLUME — SIGNAL RESEARCH")
    print("=" * 80)

    # --- Step 1: Load data ---
    print("\n[1] Loading data...")
    token_data = load_all_tokens()
    print(f"  Loaded {len(token_data)} tokens")

    # --- Step 2: Compute signals and forward returns ---
    print("\n[2] Computing signals and forward returns...")
    panel_data_is = []  # In-sample
    panel_data_oos = []  # Out-of-sample
    all_daily_signals = {}  # For dispersion

    # Determine IS/OOS split
    # Data runs 2025-07-01 to 2026-02-28 (~8 months)
    # Split at midpoint: ~2025-11-01
    SPLIT_DATE = pd.Timestamp("2025-11-01")
    print(f"  IS/OOS split at: {SPLIT_DATE}")

    for token, df in token_data.items():
        hourly_signals = compute_signals_hourly(df)
        daily_price, daily_signals = resample_to_daily(df, hourly_signals)
        fwd_rets = compute_forward_returns(daily_price)

        all_daily_signals[token] = daily_signals

        # Split IS / OOS
        is_mask = daily_signals.index < SPLIT_DATE
        oos_mask = daily_signals.index >= SPLIT_DATE

        panel_data_is.append((token, daily_signals[is_mask], fwd_rets[is_mask]))
        panel_data_oos.append((token, daily_signals[oos_mask], fwd_rets[oos_mask]))

    # Add dispersion signal to panel
    dispersion = compute_dispersion_signal(all_daily_signals)
    for i, (token, daily_signals, fwd_rets) in enumerate(panel_data_is):
        daily_signals = daily_signals.copy()
        daily_signals["cross_token_dispersion"] = dispersion
        panel_data_is[i] = (token, daily_signals, fwd_rets)

    for i, (token, daily_signals, fwd_rets) in enumerate(panel_data_oos):
        daily_signals = daily_signals.copy()
        daily_signals["cross_token_dispersion"] = dispersion
        panel_data_oos[i] = (token, daily_signals, fwd_rets)

    # --- Step 3: Compute IC for each signal variant ---
    print("\n[3] Computing Information Coefficients...")

    signal_names = [
        "buy_sell_ratio",
        "net_taker_vol",
        "net_taker_zscore_10d",
        "net_taker_zscore_20d",
        "ratio_momentum_1d",
        "ratio_momentum_3d",
        "ratio_momentum_7d",
        "vol_weighted_pressure_norm",
        "cross_token_dispersion",
    ]

    horizon_labels = [f"fwd_{h}" for h in FWD_HORIZONS.keys()]

    # A) Time-series pooled IC (pool all tokens, compute IC per signal/horizon)
    print("\n  --- A) Pooled Time-Series IC ---")
    results_ts_is = []
    results_ts_oos = []

    for sig_name in signal_names:
        for hz in horizon_labels:
            # IS
            all_sig_is, all_ret_is = [], []
            all_sig_oos, all_ret_oos = [], []

            for token, daily_signals, fwd_rets in panel_data_is:
                if sig_name in daily_signals.columns and hz in fwd_rets.columns:
                    tmp = pd.concat([daily_signals[sig_name], fwd_rets[hz]], axis=1).dropna()
                    all_sig_is.extend(tmp.iloc[:, 0].values)
                    all_ret_is.extend(tmp.iloc[:, 1].values)

            for token, daily_signals, fwd_rets in panel_data_oos:
                if sig_name in daily_signals.columns and hz in fwd_rets.columns:
                    tmp = pd.concat([daily_signals[sig_name], fwd_rets[hz]], axis=1).dropna()
                    all_sig_oos.extend(tmp.iloc[:, 0].values)
                    all_ret_oos.extend(tmp.iloc[:, 1].values)

            is_ic = compute_ic(pd.Series(all_sig_is), pd.Series(all_ret_is))
            oos_ic = compute_ic(pd.Series(all_sig_oos), pd.Series(all_ret_oos))

            results_ts_is.append({
                "signal": sig_name, "horizon": hz, "sample": "IS", **is_ic
            })
            results_ts_oos.append({
                "signal": sig_name, "horizon": hz, "sample": "OOS", **oos_ic
            })

    df_ts_is = pd.DataFrame(results_ts_is)
    df_ts_oos = pd.DataFrame(results_ts_oos)
    df_ts = pd.concat([df_ts_is, df_ts_oos], ignore_index=True)

    # B) Cross-sectional IC
    print("  --- B) Cross-Sectional IC ---")
    results_cs_is = []
    results_cs_oos = []

    for sig_name in signal_names:
        for hz in horizon_labels:
            is_cs = compute_panel_ic(panel_data_is, sig_name, hz)
            oos_cs = compute_panel_ic(panel_data_oos, sig_name, hz)

            results_cs_is.append({"signal": sig_name, "horizon": hz, "sample": "IS", **is_cs})
            results_cs_oos.append({"signal": sig_name, "horizon": hz, "sample": "OOS", **oos_cs})

    df_cs_is = pd.DataFrame(results_cs_is)
    df_cs_oos = pd.DataFrame(results_cs_oos)
    df_cs = pd.concat([df_cs_is, df_cs_oos], ignore_index=True)

    # --- Step 4: Print Results ---
    print("\n" + "=" * 100)
    print("POOLED TIME-SERIES IC RESULTS")
    print("=" * 100)

    # Pivot for cleaner display
    for sample in ["IS", "OOS"]:
        print(f"\n--- {sample} ---")
        subset = df_ts[df_ts["sample"] == sample]
        pivot_ic = subset.pivot(index="signal", columns="horizon", values="ic_mean")
        pivot_t = subset.pivot(index="signal", columns="horizon", values="ic_tstat")
        pivot_hit = subset.pivot(index="signal", columns="horizon", values="hit_rate")

        # Reorder columns
        col_order = [f"fwd_{h}" for h in FWD_HORIZONS.keys() if f"fwd_{h}" in pivot_ic.columns]
        pivot_ic = pivot_ic[col_order]
        pivot_t = pivot_t[col_order]
        pivot_hit = pivot_hit[col_order]

        print("\nIC Mean:")
        print(pivot_ic.round(4).to_string())
        print("\nIC t-stat:")
        print(pivot_t.round(2).to_string())
        print("\nHit Rate:")
        print(pivot_hit.round(4).to_string())

    # Sign consistency
    print("\n" + "=" * 100)
    print("SIGN CONSISTENCY (IS vs OOS IC Mean — same direction?)")
    print("=" * 100)

    consistency = []
    for sig_name in signal_names:
        for hz in horizon_labels:
            is_row = df_ts[(df_ts["signal"] == sig_name) & (df_ts["horizon"] == hz) & (df_ts["sample"] == "IS")]
            oos_row = df_ts[(df_ts["signal"] == sig_name) & (df_ts["horizon"] == hz) & (df_ts["sample"] == "OOS")]
            if len(is_row) > 0 and len(oos_row) > 0:
                is_ic = is_row.iloc[0]["ic_mean"]
                oos_ic = oos_row.iloc[0]["ic_mean"]
                same_sign = np.sign(is_ic) == np.sign(oos_ic) if not (np.isnan(is_ic) or np.isnan(oos_ic)) else False
                consistency.append({
                    "signal": sig_name, "horizon": hz,
                    "IS_IC": is_ic, "OOS_IC": oos_ic,
                    "consistent": "YES" if same_sign else "NO"
                })

    df_consistency = pd.DataFrame(consistency)
    pivot_cons = df_consistency.pivot(index="signal", columns="horizon", values="consistent")
    col_order = [f"fwd_{h}" for h in FWD_HORIZONS.keys() if f"fwd_{h}" in pivot_cons.columns]
    pivot_cons = pivot_cons[col_order]
    print(pivot_cons.to_string())

    # Cross-sectional IC summary
    print("\n" + "=" * 100)
    print("CROSS-SECTIONAL IC RESULTS")
    print("=" * 100)

    for sample in ["IS", "OOS"]:
        print(f"\n--- {sample} ---")
        subset = df_cs[df_cs["sample"] == sample]
        pivot_ic = subset.pivot(index="signal", columns="horizon", values="ic_mean")
        pivot_t = subset.pivot(index="signal", columns="horizon", values="ic_tstat")

        col_order = [f"fwd_{h}" for h in FWD_HORIZONS.keys() if f"fwd_{h}" in pivot_ic.columns]
        pivot_ic = pivot_ic[col_order]
        pivot_t = pivot_t[col_order]

        print("\nCS IC Mean:")
        print(pivot_ic.round(4).to_string())
        print("\nCS IC t-stat:")
        print(pivot_t.round(2).to_string())

    # --- Step 5: Trend Overlay ---
    print("\n" + "=" * 100)
    print("TREND OVERLAY ANALYSIS")
    print("=" * 100)

    # Full sample panel data for overlay
    panel_data_full = []
    for token, df in token_data.items():
        hourly_signals = compute_signals_hourly(df)
        daily_price, daily_signals = resample_to_daily(df, hourly_signals)
        fwd_rets = compute_forward_returns(daily_price)
        panel_data_full.append((token, daily_signals, fwd_rets))

    for sig_name in ["net_taker_vol", "net_taker_zscore_10d", "vol_weighted_pressure_norm"]:
        print(f"\n  Overlay signal: {sig_name}")

        # IS
        overlay_is = test_trend_overlay(panel_data_is, sig_name)
        if overlay_is:
            print(f"    IS:  Trend Sharpe={overlay_is['trend_sharpe']:.3f}  "
                  f"Overlay Sharpe={overlay_is['overlay_sharpe']:.3f}  "
                  f"Confirm%={overlay_is['confirmation_rate']:.1%}  "
                  f"Trend HR={overlay_is['trend_hit_rate']:.1%}  "
                  f"Overlay HR={overlay_is['overlay_hit_rate']:.1%}")

        # OOS
        overlay_oos = test_trend_overlay(panel_data_oos, sig_name)
        if overlay_oos:
            print(f"    OOS: Trend Sharpe={overlay_oos['trend_sharpe']:.3f}  "
                  f"Overlay Sharpe={overlay_oos['overlay_sharpe']:.3f}  "
                  f"Confirm%={overlay_oos['confirmation_rate']:.1%}  "
                  f"Trend HR={overlay_oos['trend_hit_rate']:.1%}  "
                  f"Overlay HR={overlay_oos['overlay_hit_rate']:.1%}")

    # --- Step 6: Per-token breakdown for strongest signals ---
    print("\n" + "=" * 100)
    print("PER-TOKEN IC BREAKDOWN (Best Signal: net_taker_vol, fwd_3d)")
    print("=" * 100)

    best_signal = "net_taker_vol"
    best_horizon = "fwd_3d"

    token_ics = []
    for token, daily_signals, fwd_rets in panel_data_full:
        if best_signal in daily_signals.columns and best_horizon in fwd_rets.columns:
            ic_res = compute_ic(daily_signals[best_signal], fwd_rets[best_horizon])
            token_ics.append({"token": token, **ic_res})

    df_token_ics = pd.DataFrame(token_ics).sort_values("ic_full", ascending=False)
    print(df_token_ics[["token", "ic_full", "ic_mean", "ic_tstat", "hit_rate", "n_obs"]].to_string(index=False))

    # --- Step 7: Summary statistics ---
    print("\n" + "=" * 100)
    print("SUMMARY")
    print("=" * 100)

    # Find best signals (highest abs IC in OOS with t-stat > 1.5)
    oos_results = df_ts[df_ts["sample"] == "OOS"].copy()
    oos_results["abs_ic"] = oos_results["ic_mean"].abs()
    oos_results = oos_results.sort_values("abs_ic", ascending=False)

    print("\nTop 10 signal/horizon combos by |OOS IC|:")
    top10 = oos_results.head(10)
    print(top10[["signal", "horizon", "ic_mean", "ic_tstat", "hit_rate", "n_obs"]].to_string(index=False))

    # Signals with |t-stat| > 1.5 in both IS and OOS
    print("\nSignals with |t-stat| > 1.5 in BOTH IS and OOS:")
    for _, row in oos_results.iterrows():
        sig, hz = row["signal"], row["horizon"]
        is_row = df_ts[(df_ts["signal"] == sig) & (df_ts["horizon"] == hz) & (df_ts["sample"] == "IS")]
        if len(is_row) > 0:
            is_t = is_row.iloc[0]["ic_tstat"]
            oos_t = row["ic_tstat"]
            if abs(is_t) > 1.5 and abs(oos_t) > 1.5:
                print(f"  {sig:35s} {hz:8s}  IS t={is_t:6.2f}  OOS t={oos_t:6.2f}  "
                      f"IS IC={is_row.iloc[0]['ic_mean']:.4f}  OOS IC={row['ic_mean']:.4f}")

    # --- Save results to markdown ---
    save_results_md(df_ts, df_cs, df_consistency, df_token_ics, oos_results, panel_data_is, panel_data_oos)

    return df_ts, df_cs


def save_results_md(df_ts, df_cs, df_consistency, df_token_ics, oos_results,
                    panel_data_is, panel_data_oos):
    """Save comprehensive results to markdown."""
    lines = []
    lines.append("# Taker Buy/Sell Volume Signal Research Results")
    lines.append("")
    lines.append(f"**Generated**: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"**Data**: 19 tokens, hourly, 2025-07-01 to 2026-02-28")
    lines.append(f"**IS/OOS Split**: 2025-11-01 (first 4 months IS, last 4 months OOS)")
    lines.append(f"**Methodology**: Spearman rank IC, daily resampled, rolling 20-day IC windows for t-stat")
    lines.append("")

    # Signal descriptions
    lines.append("## Signal Variants")
    lines.append("")
    lines.append("| # | Signal | Description |")
    lines.append("|---|--------|-------------|")
    lines.append("| 1 | `buy_sell_ratio` | Taker buy volume / sell volume |")
    lines.append("| 2 | `net_taker_vol` | (buy - sell) / (buy + sell), normalized imbalance |")
    lines.append("| 3a | `net_taker_zscore_10d` | 10-day rolling z-score of net taker volume |")
    lines.append("| 3b | `net_taker_zscore_20d` | 20-day rolling z-score of net taker volume |")
    lines.append("| 4a | `ratio_momentum_1d` | 1-day change in buy/sell ratio |")
    lines.append("| 4b | `ratio_momentum_3d` | 3-day change in buy/sell ratio |")
    lines.append("| 4c | `ratio_momentum_7d` | 7-day change in buy/sell ratio |")
    lines.append("| 5 | `vol_weighted_pressure_norm` | Net taker vol * total volume, normalized |")
    lines.append("| 6 | `cross_token_dispersion` | Std dev of buy/sell ratios across tokens |")
    lines.append("")

    # Pooled TS IC tables
    lines.append("## Pooled Time-Series IC")
    lines.append("")

    for sample in ["IS", "OOS"]:
        lines.append(f"### {sample}")
        lines.append("")
        subset = df_ts[df_ts["sample"] == sample]

        # IC Mean table
        pivot_ic = subset.pivot(index="signal", columns="horizon", values="ic_mean")
        pivot_t = subset.pivot(index="signal", columns="horizon", values="ic_tstat")
        pivot_hit = subset.pivot(index="signal", columns="horizon", values="hit_rate")

        col_order = [f"fwd_{h}" for h in FWD_HORIZONS.keys() if f"fwd_{h}" in pivot_ic.columns]
        pivot_ic = pivot_ic[col_order]
        pivot_t = pivot_t[col_order]
        pivot_hit = pivot_hit[col_order]

        lines.append("**IC Mean:**")
        lines.append("")
        lines.append("| Signal | " + " | ".join(col_order) + " |")
        lines.append("|--------|" + "|".join(["--------"] * len(col_order)) + "|")
        for idx in pivot_ic.index:
            vals = [f"{pivot_ic.loc[idx, c]:.4f}" if not pd.isna(pivot_ic.loc[idx, c]) else "N/A" for c in col_order]
            lines.append(f"| `{idx}` | " + " | ".join(vals) + " |")
        lines.append("")

        lines.append("**IC t-stat:**")
        lines.append("")
        lines.append("| Signal | " + " | ".join(col_order) + " |")
        lines.append("|--------|" + "|".join(["--------"] * len(col_order)) + "|")
        for idx in pivot_t.index:
            vals = [f"{pivot_t.loc[idx, c]:.2f}" if not pd.isna(pivot_t.loc[idx, c]) else "N/A" for c in col_order]
            lines.append(f"| `{idx}` | " + " | ".join(vals) + " |")
        lines.append("")

        lines.append("**Hit Rate:**")
        lines.append("")
        lines.append("| Signal | " + " | ".join(col_order) + " |")
        lines.append("|--------|" + "|".join(["--------"] * len(col_order)) + "|")
        for idx in pivot_hit.index:
            vals = [f"{pivot_hit.loc[idx, c]:.1%}" if not pd.isna(pivot_hit.loc[idx, c]) else "N/A" for c in col_order]
            lines.append(f"| `{idx}` | " + " | ".join(vals) + " |")
        lines.append("")

    # Sign consistency
    lines.append("## Sign Consistency (IS vs OOS)")
    lines.append("")
    pivot_cons = df_consistency.pivot(index="signal", columns="horizon", values="consistent")
    col_order = [f"fwd_{h}" for h in FWD_HORIZONS.keys() if f"fwd_{h}" in pivot_cons.columns]
    pivot_cons = pivot_cons[col_order]

    lines.append("| Signal | " + " | ".join(col_order) + " |")
    lines.append("|--------|" + "|".join(["--------"] * len(col_order)) + "|")
    for idx in pivot_cons.index:
        vals = [str(pivot_cons.loc[idx, c]) for c in col_order]
        lines.append(f"| `{idx}` | " + " | ".join(vals) + " |")
    lines.append("")

    # Cross-sectional IC
    lines.append("## Cross-Sectional IC")
    lines.append("")
    for sample in ["IS", "OOS"]:
        lines.append(f"### {sample}")
        lines.append("")
        subset = df_cs[df_cs["sample"] == sample]
        pivot_ic = subset.pivot(index="signal", columns="horizon", values="ic_mean")
        pivot_t = subset.pivot(index="signal", columns="horizon", values="ic_tstat")
        col_order = [f"fwd_{h}" for h in FWD_HORIZONS.keys() if f"fwd_{h}" in pivot_ic.columns]
        pivot_ic = pivot_ic[col_order]
        pivot_t = pivot_t[col_order]

        lines.append("**CS IC Mean:**")
        lines.append("")
        lines.append("| Signal | " + " | ".join(col_order) + " |")
        lines.append("|--------|" + "|".join(["--------"] * len(col_order)) + "|")
        for idx in pivot_ic.index:
            vals = [f"{pivot_ic.loc[idx, c]:.4f}" if not pd.isna(pivot_ic.loc[idx, c]) else "N/A" for c in col_order]
            lines.append(f"| `{idx}` | " + " | ".join(vals) + " |")
        lines.append("")

        lines.append("**CS IC t-stat:**")
        lines.append("")
        lines.append("| Signal | " + " | ".join(col_order) + " |")
        lines.append("|--------|" + "|".join(["--------"] * len(col_order)) + "|")
        for idx in pivot_t.index:
            vals = [f"{pivot_t.loc[idx, c]:.2f}" if not pd.isna(pivot_t.loc[idx, c]) else "N/A" for c in col_order]
            lines.append(f"| `{idx}` | " + " | ".join(vals) + " |")
        lines.append("")

    # Per-token breakdown
    lines.append("## Per-Token IC Breakdown (net_taker_vol, fwd_3d)")
    lines.append("")
    lines.append("| Token | IC (full) | IC (mean) | t-stat | Hit Rate | N obs |")
    lines.append("|-------|-----------|-----------|--------|----------|-------|")
    for _, row in df_token_ics.iterrows():
        lines.append(
            f"| {row['token']} | {row['ic_full']:.4f} | {row['ic_mean']:.4f} | "
            f"{row['ic_tstat']:.2f} | {row['hit_rate']:.1%} | {int(row['n_obs'])} |"
        )
    lines.append("")

    # Trend overlay
    lines.append("## Trend Overlay Analysis")
    lines.append("")
    lines.append("Strategy: 7-day trailing momentum (long/short). Overlay: only trade when taker signal confirms direction.")
    lines.append("")
    lines.append("| Signal | Sample | Trend Sharpe | Overlay Sharpe | Confirm % | Trend HR | Overlay HR |")
    lines.append("|--------|--------|-------------|----------------|-----------|----------|------------|")

    for sig_name in ["net_taker_vol", "net_taker_zscore_10d", "vol_weighted_pressure_norm"]:
        for label, panel in [("IS", panel_data_is), ("OOS", panel_data_oos)]:
            res = test_trend_overlay(panel, sig_name)
            if res:
                lines.append(
                    f"| `{sig_name}` | {label} | {res['trend_sharpe']:.3f} | {res['overlay_sharpe']:.3f} | "
                    f"{res['confirmation_rate']:.1%} | {res['trend_hit_rate']:.1%} | {res['overlay_hit_rate']:.1%} |"
                )
    lines.append("")

    # Top signals summary
    lines.append("## Top OOS Signals (by |IC|)")
    lines.append("")
    lines.append("| Signal | Horizon | IC Mean | t-stat | Hit Rate |")
    lines.append("|--------|---------|---------|--------|----------|")
    top10 = oos_results.head(10)
    for _, row in top10.iterrows():
        lines.append(
            f"| `{row['signal']}` | {row['horizon']} | {row['ic_mean']:.4f} | "
            f"{row['ic_tstat']:.2f} | {row['hit_rate']:.1%} |"
        )
    lines.append("")

    # Signals passing dual t-stat threshold
    lines.append("## Robust Signals (|t-stat| > 1.5 in both IS and OOS)")
    lines.append("")
    robust_found = False
    for _, row in oos_results.iterrows():
        sig, hz = row["signal"], row["horizon"]
        is_row = df_ts[(df_ts["signal"] == sig) & (df_ts["horizon"] == hz) & (df_ts["sample"] == "IS")]
        if len(is_row) > 0:
            is_t = is_row.iloc[0]["ic_tstat"]
            oos_t = row["ic_tstat"]
            if abs(is_t) > 1.5 and abs(oos_t) > 1.5:
                if not robust_found:
                    lines.append("| Signal | Horizon | IS IC | IS t | OOS IC | OOS t |")
                    lines.append("|--------|---------|-------|------|--------|-------|")
                    robust_found = True
                lines.append(
                    f"| `{sig}` | {hz} | {is_row.iloc[0]['ic_mean']:.4f} | {is_t:.2f} | "
                    f"{row['ic_mean']:.4f} | {oos_t:.2f} |"
                )

    if not robust_found:
        lines.append("No signals passed the dual t-stat > 1.5 threshold in both IS and OOS.")
    lines.append("")

    # Interpretation
    lines.append("## Interpretation Notes")
    lines.append("")
    lines.append("- **Positive IC**: Higher taker buy pressure predicts higher future returns (momentum/continuation)")
    lines.append("- **Negative IC**: Higher taker buy pressure predicts lower future returns (contrarian/reversal)")
    lines.append("- **Sign consistency**: If IS and OOS have the same IC sign, the signal direction is stable")
    lines.append("- **t-stat > 2**: Statistically significant at ~95% confidence")
    lines.append("- **Hit rate**: Fraction of observations where signal direction matches return direction (50% = random)")
    lines.append("- **Cross-sectional IC**: Measures whether ranking tokens by signal predicts relative performance")
    lines.append("- **Trend overlay**: Tests whether filtering trend signals by taker confirmation improves Sharpe ratio")
    lines.append("")

    output_path = os.path.join(OUTPUT_DIR, "taker_volume_results.md")
    with open(output_path, "w") as f:
        f.write("\n".join(lines))
    print(f"\n  Results saved to {output_path}")


if __name__ == "__main__":
    df_ts, df_cs = main()
