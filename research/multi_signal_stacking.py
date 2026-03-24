#!/workspace/venv/bin/python
"""
Multi-Signal Stacking Analysis — Do Proven Signals ADD or CANCEL?
==================================================================

Tests whether 9 individually-proven signals are complementary (additive IC)
or redundant (correlated, cancelling) when combined as composite overlays.

Signals tested (all with OOS validation):
  1. US10Y 20d change       — IC=-0.375, 14D
  2. DXY 20d momentum       — part of DXY+10Y regime
  3. Oil 20d momentum       — IC=-0.480 ETH, part of triple regime
  4. Skew_30d (realized)    — IC=+0.224, 7D
  5. Taker dispersion       — IC=-0.138 (cross-token)
  6. ETF flow z-score       — IC=+0.191, 14D
  7. OI divergence          — IC=-0.059, 3D
  8. VRP z-score            — IC=0.268, BTC 7D vol predictor
  9. Net taker volume       — trend overlay Sharpe +180%

Methodology:
  - All signals computed as daily rank or z-score
  - Pairwise correlation matrix
  - Combined IC = rank(avg(rank(sig_A), rank(sig_B))) vs forward returns
  - OOS split: train < 2025-07-01, test >= 2025-07-01
  - Top-3 and Top-5 equal-weight rank composites

Output: research/multi_signal_stacking_results.md
"""

import os
import json
import warnings
import numpy as np
import pandas as pd
from scipy import stats
from itertools import combinations
from pathlib import Path

warnings.filterwarnings("ignore")

# ============================================================================
# CONFIGURATION
# ============================================================================

BASE_DIR = Path("/workspace/crypto_backtest")
PRICE_DIR = BASE_DIR / "data" / "perp" / "1h_cache"
MACRO_DIR = BASE_DIR / "data" / "alternative" / "macro"
TAKER_DIR = BASE_DIR / "data" / "alternative" / "binance_positioning" / "extended"
TAKER_MAIN = BASE_DIR / "data" / "alternative" / "binance_positioning"
ETF_PATH = BASE_DIR / "data" / "alternative" / "etf_flows" / "btc_etf_daily.parquet"
OI_PATH = BASE_DIR / "research" / "oi_divergence_panel.parquet"
DVOL_PATH = BASE_DIR / "data" / "alternative" / "deribit_options" / "dvol" / "btc_dvol_daily.json"
OUTPUT_PATH = BASE_DIR / "research" / "multi_signal_stacking_results.md"

OOS_START = pd.Timestamp("2025-07-01")
FWD_HORIZONS = {"7d": 7, "14d": 14}

SIGNAL_NAMES = [
    "us10y_20d",
    "dxy_20d",
    "oil_20d",
    "skew_30d",
    "taker_dispersion",
    "etf_flow_zscore",
    "oi_divergence",
    "vrp_zscore",
    "net_taker_vol",
]

SIGNAL_LABELS = {
    "us10y_20d": "US10Y 20d Chg",
    "dxy_20d": "DXY 20d Mom",
    "oil_20d": "Oil 20d Mom",
    "skew_30d": "Skew 30d",
    "taker_dispersion": "Taker Dispersion",
    "etf_flow_zscore": "ETF Flow Z",
    "oi_divergence": "OI Divergence",
    "vrp_zscore": "VRP Z-Score",
    "net_taker_vol": "Net Taker Vol",
}

# Expected IC signs (for sign-alignment in composites)
# Positive = signal up -> returns up; Negative = signal up -> returns down
EXPECTED_SIGNS = {
    "us10y_20d": -1,      # rising yields = bearish crypto
    "dxy_20d": -1,         # strong dollar = bearish crypto
    "oil_20d": -1,         # rising oil = bearish crypto (risk-off/inflation)
    "skew_30d": +1,        # positive realized skew = bullish
    "taker_dispersion": -1,  # high dispersion = subsequent underperformance
    "etf_flow_zscore": +1,   # ETF inflows = bullish
    "oi_divergence": -1,     # OI diverging from price = bearish
    "vrp_zscore": +1,        # high VRP = vol overpriced = bullish for returns
    "net_taker_vol": +1,     # net buying = bullish
}


# ============================================================================
# DATA LOADING
# ============================================================================

def load_btc_daily():
    """Load BTC hourly -> resample to daily close."""
    df = pd.read_parquet(PRICE_DIR / "BTC_1h.parquet")
    df.index = pd.to_datetime(df.index)
    daily = df["close"].resample("D").last().dropna()
    daily.index = daily.index.normalize()
    daily.name = "btc_close"
    return daily


def load_eth_daily():
    """Load ETH hourly -> resample to daily close."""
    path = PRICE_DIR / "ETH_1h.parquet"
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    df.index = pd.to_datetime(df.index)
    daily = df["close"].resample("D").last().dropna()
    daily.index = daily.index.normalize()
    daily.name = "eth_close"
    return daily


def compute_forward_returns(prices, horizon_days):
    """Compute forward returns over horizon_days."""
    return prices.pct_change(horizon_days).shift(-horizon_days)


def load_macro_series(name):
    """Load a macro series, set Date as index, return Close."""
    df = pd.read_parquet(MACRO_DIR / f"{name}.parquet")
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.set_index("Date").sort_index()
    df.index = df.index.normalize()
    return df["Close"]


# ============================================================================
# SIGNAL CONSTRUCTION
# ============================================================================

def build_signal_us10y_20d():
    """US 10Y yield 20-day change."""
    y = load_macro_series("us10y_yield")
    sig = y.diff(20)
    sig.name = "us10y_20d"
    return sig


def build_signal_dxy_20d():
    """DXY 20-day momentum (pct change)."""
    dxy = load_macro_series("usd_index")
    sig = dxy.pct_change(20)
    sig.name = "dxy_20d"
    return sig


def build_signal_oil_20d():
    """Oil WTI 20-day momentum (pct change)."""
    oil = load_macro_series("oil_wti")
    sig = oil.pct_change(20)
    sig.name = "oil_20d"
    return sig


def build_signal_skew_30d(prices):
    """Realized return skew over 30d: (mean - median) / std of daily returns."""
    daily_ret = prices.pct_change()

    def skew_func(window):
        if window.isna().sum() > 5:
            return np.nan
        m = window.mean()
        med = window.median()
        s = window.std()
        if s < 1e-10:
            return 0.0
        return (m - med) / s

    sig = daily_ret.rolling(30, min_periods=20).apply(skew_func, raw=False)
    sig.name = "skew_30d"
    return sig


def build_signal_taker_dispersion():
    """
    Cross-token taker buy/sell ratio dispersion.
    For each day, compute std of buySellRatio across tokens -> higher = more divergent positioning.
    """
    # Use extended taker data (wider coverage)
    all_tokens = sorted(TAKER_DIR.glob("*_taker_buysell.parquet"))
    if not all_tokens:
        print("  [WARN] No extended taker data found, using main taker data")
        taker = pd.read_parquet(TAKER_MAIN / "taker_buy_sell_vol.parquet")
        taker["timestamp"] = pd.to_datetime(taker["timestamp"]).dt.tz_localize(None)
        taker_daily = taker.set_index("timestamp").groupby("symbol")["buySellRatio"].resample("D").mean().unstack(level=0)
        dispersion = taker_daily.std(axis=1)
        dispersion.index = dispersion.index.normalize()
        dispersion.name = "taker_dispersion"
        return dispersion

    frames = []
    for f in all_tokens:
        token = f.stem.replace("_taker_buysell", "")
        df = pd.read_parquet(f)
        df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.tz_localize(None)
        df = df.set_index("timestamp")
        daily_bsr = df["buySellRatio"].resample("D").mean()
        daily_bsr.name = token
        frames.append(daily_bsr)

    if not frames:
        return pd.Series(dtype=float, name="taker_dispersion")

    panel = pd.concat(frames, axis=1)
    panel.index = panel.index.normalize()
    # Cross-sectional std of buy/sell ratios
    dispersion = panel.std(axis=1)
    dispersion.name = "taker_dispersion"
    return dispersion


def build_signal_etf_flow_zscore():
    """ETF flow 20d rolling sum, z-scored over 60d."""
    df = pd.read_parquet(ETF_PATH)
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    df = df.sort_values("date").drop_duplicates(subset="date", keep="last")
    df = df.set_index("date")

    flow_20d = df["total_inflow_mm"].rolling(20, min_periods=10).sum()
    roll_mean = flow_20d.rolling(60, min_periods=30).mean()
    roll_std = flow_20d.rolling(60, min_periods=30).std()
    zscore = (flow_20d - roll_mean) / roll_std.replace(0, np.nan)
    zscore.name = "etf_flow_zscore"
    return zscore


def build_signal_oi_divergence():
    """OI divergence (signed 3d) for BTC."""
    df = pd.read_parquet(OI_PATH)
    btc = df[df["token"] == "BTC"].copy()
    btc["datetime"] = pd.to_datetime(btc["datetime"]).dt.tz_localize(None)
    btc = btc.set_index("datetime").sort_index()
    btc.index = btc.index.normalize()
    btc = btc[~btc.index.duplicated(keep="last")]
    sig = btc["oi_div_signed_3d"]
    sig.name = "oi_divergence"
    return sig


def build_signal_vrp_zscore():
    """
    Volatility Risk Premium proxy z-score.
    VRP = implied vol (DVOL) - realized vol.
    If DVOL > realized, options are overpriced -> mean revert -> bullish for spot.
    Z-score over 60d rolling window.
    """
    # Load DVOL
    with open(DVOL_PATH) as f:
        dvol_raw = json.load(f)
    dvol = pd.DataFrame(dvol_raw, columns=["timestamp", "open", "high", "low", "close"])
    dvol["timestamp"] = pd.to_datetime(dvol["timestamp"], unit="ms")
    dvol = dvol.set_index("timestamp").sort_index()
    dvol.index = dvol.index.normalize()
    dvol = dvol[~dvol.index.duplicated(keep="last")]
    iv = dvol["close"]  # DVOL = annualized implied vol (percentage)

    # Compute realized vol from BTC price
    btc = load_btc_daily()
    daily_ret = btc.pct_change()
    rv_30d = daily_ret.rolling(30, min_periods=20).std() * np.sqrt(365) * 100  # annualized, percent

    # Align
    aligned = pd.DataFrame({"iv": iv, "rv": rv_30d}).dropna()
    vrp = aligned["iv"] - aligned["rv"]  # positive = IV > RV = overpriced vol

    # Z-score
    roll_mean = vrp.rolling(60, min_periods=30).mean()
    roll_std = vrp.rolling(60, min_periods=30).std()
    zscore = (vrp - roll_mean) / roll_std.replace(0, np.nan)
    zscore.name = "vrp_zscore"
    return zscore


def build_signal_net_taker_vol():
    """
    Net taker volume for BTC: (buy_vol - sell_vol) / total_vol, z-scored.
    Uses extended data for broader range.
    """
    ext_path = TAKER_DIR / "BTCUSDT_taker_buysell.parquet"
    if ext_path.exists():
        df = pd.read_parquet(ext_path)
        df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.tz_localize(None)
        df = df.set_index("timestamp").sort_index()
        # Net taker = (buy - sell) / total
        net = (df["taker_buy_base_vol"] - df["taker_sell_vol"]) / df["volume"]
        daily_net = net.resample("D").mean()
    else:
        # Fallback to main taker data
        taker = pd.read_parquet(TAKER_MAIN / "taker_buy_sell_vol.parquet")
        btc = taker[taker["symbol"] == "BTCUSDT"].copy()
        btc["timestamp"] = pd.to_datetime(btc["timestamp"]).dt.tz_localize(None)
        btc = btc.set_index("timestamp").sort_index()
        net = (btc["buyVol"] - btc["sellVol"]) / (btc["buyVol"] + btc["sellVol"])
        daily_net = net.resample("D").mean()

    daily_net.index = daily_net.index.normalize()
    # Z-score over 20d
    roll_mean = daily_net.rolling(20, min_periods=10).mean()
    roll_std = daily_net.rolling(20, min_periods=10).std()
    zscore = (daily_net - roll_mean) / roll_std.replace(0, np.nan)
    zscore.name = "net_taker_vol"
    return zscore


# ============================================================================
# IC COMPUTATION
# ============================================================================

def compute_ic(signal, fwd_returns, method="spearman"):
    """Compute rank IC between signal and forward returns."""
    aligned = pd.DataFrame({"signal": signal, "fwd_ret": fwd_returns}).dropna()
    if len(aligned) < 20:
        return np.nan, np.nan, 0
    if method == "spearman":
        ic, pval = stats.spearmanr(aligned["signal"], aligned["fwd_ret"])
    else:
        ic, pval = stats.pearsonr(aligned["signal"], aligned["fwd_ret"])
    return ic, pval, len(aligned)


def compute_ic_with_split(signal, fwd_returns, split_date):
    """Compute IC for IS and OOS periods."""
    aligned = pd.DataFrame({"signal": signal, "fwd_ret": fwd_returns}).dropna()
    is_mask = aligned.index < split_date
    oos_mask = aligned.index >= split_date

    is_data = aligned[is_mask]
    oos_data = aligned[oos_mask]

    is_ic, is_pval, is_n = np.nan, np.nan, 0
    oos_ic, oos_pval, oos_n = np.nan, np.nan, 0

    if len(is_data) >= 20:
        is_ic, is_pval = stats.spearmanr(is_data["signal"], is_data["fwd_ret"])
        is_n = len(is_data)
    if len(oos_data) >= 20:
        oos_ic, oos_pval = stats.spearmanr(oos_data["signal"], oos_data["fwd_ret"])
        oos_n = len(oos_data)

    return {
        "is_ic": is_ic, "is_pval": is_pval, "is_n": is_n,
        "oos_ic": oos_ic, "oos_pval": oos_pval, "oos_n": oos_n,
    }


def sign_align_signal(signal, name):
    """Flip signal so that higher signal -> higher expected returns."""
    if EXPECTED_SIGNS.get(name, 1) < 0:
        return -signal
    return signal


def rank_normalize(series):
    """Rank normalize to [0, 1]."""
    return series.rank(pct=True)


# ============================================================================
# MAIN ANALYSIS
# ============================================================================

def main():
    print("=" * 90)
    print("MULTI-SIGNAL STACKING ANALYSIS")
    print("Do 9 proven signals ADD or CANCEL when combined?")
    print("=" * 90)

    # ── Load BTC price ──
    print("\n[1] Loading price data...")
    btc_daily = load_btc_daily()
    eth_daily = load_eth_daily()
    print(f"   BTC daily: {len(btc_daily)} days, {btc_daily.index.min().date()} to {btc_daily.index.max().date()}")
    if eth_daily is not None:
        print(f"   ETH daily: {len(eth_daily)} days")

    # Forward returns
    fwd_rets = {}
    for label, days in FWD_HORIZONS.items():
        fwd_rets[f"btc_{label}"] = compute_forward_returns(btc_daily, days)
        if eth_daily is not None:
            fwd_rets[f"eth_{label}"] = compute_forward_returns(eth_daily, days)

    # ── Build all signals ──
    print("\n[2] Building signals...")
    signal_builders = {
        "us10y_20d": build_signal_us10y_20d,
        "dxy_20d": build_signal_dxy_20d,
        "oil_20d": build_signal_oil_20d,
        "skew_30d": lambda: build_signal_skew_30d(btc_daily),
        "taker_dispersion": build_signal_taker_dispersion,
        "etf_flow_zscore": build_signal_etf_flow_zscore,
        "oi_divergence": build_signal_oi_divergence,
        "vrp_zscore": build_signal_vrp_zscore,
        "net_taker_vol": build_signal_net_taker_vol,
    }

    raw_signals = {}
    for name, builder in signal_builders.items():
        try:
            sig = builder()
            n_valid = sig.dropna().shape[0]
            date_range = f"{sig.dropna().index.min().date()} to {sig.dropna().index.max().date()}" if n_valid > 0 else "NO DATA"
            print(f"   {SIGNAL_LABELS[name]:22s}: {n_valid:5d} obs  |  {date_range}")
            raw_signals[name] = sig
        except Exception as e:
            print(f"   {SIGNAL_LABELS[name]:22s}: FAILED — {e}")
            raw_signals[name] = pd.Series(dtype=float, name=name)

    # ── Align all signals to common dates ──
    print("\n[3] Aligning signals to common date index...")
    signal_df = pd.DataFrame(raw_signals)
    # Use BTC daily index as base
    signal_df = signal_df.reindex(btc_daily.index)

    # Report coverage
    coverage = signal_df.notna().sum()
    total_days = len(signal_df)
    print(f"   Total date range: {signal_df.index.min().date()} to {signal_df.index.max().date()} ({total_days} days)")
    for name in SIGNAL_NAMES:
        if name in signal_df.columns:
            n = coverage[name]
            pct = n / total_days * 100
            print(f"   {SIGNAL_LABELS[name]:22s}: {n:5d}/{total_days} days ({pct:.0f}%)")

    # Sign-align all signals (so positive = bullish crypto)
    aligned_signals = pd.DataFrame()
    for name in SIGNAL_NAMES:
        if name in signal_df.columns:
            aligned_signals[name] = sign_align_signal(signal_df[name], name)

    # ── SECTION A: Individual signal ICs ──
    print("\n" + "=" * 90)
    print("[4] INDIVIDUAL SIGNAL ICs (Spearman rank correlation)")
    print("=" * 90)

    individual_ics = {}
    for name in SIGNAL_NAMES:
        if name not in aligned_signals.columns:
            continue
        sig = aligned_signals[name]
        ics = {}
        for ret_label, fwd_ret in fwd_rets.items():
            if "btc" not in ret_label:
                continue
            result = compute_ic_with_split(sig, fwd_ret, OOS_START)
            ics[ret_label] = result
        individual_ics[name] = ics

    # Print table
    print(f"\n{'Signal':22s} | {'IS IC (7d)':>10s} {'n':>5s} | {'OOS IC (7d)':>11s} {'n':>5s} | {'IS IC (14d)':>11s} {'n':>5s} | {'OOS IC (14d)':>12s} {'n':>5s}")
    print("-" * 115)
    for name in SIGNAL_NAMES:
        if name not in individual_ics:
            continue
        ics = individual_ics[name]
        row = f"{SIGNAL_LABELS[name]:22s}"
        for ret_label in ["btc_7d", "btc_14d"]:
            if ret_label in ics:
                r = ics[ret_label]
                is_str = f"{r['is_ic']:+.3f}" if not np.isnan(r["is_ic"]) else "   N/A"
                oos_str = f"{r['oos_ic']:+.3f}" if not np.isnan(r["oos_ic"]) else "   N/A"
                row += f" | {is_str:>10s} {r['is_n']:5d} | {oos_str:>11s} {r['oos_n']:5d}"
            else:
                row += f" | {'N/A':>10s} {'':>5s} | {'N/A':>11s} {'':>5s}"
        print(row)

    # ── SECTION B: Pairwise correlation matrix ──
    print("\n" + "=" * 90)
    print("[5] PAIRWISE SIGNAL CORRELATION MATRIX (rank correlations)")
    print("=" * 90)

    # Rank all signals for correlation
    ranked_signals = aligned_signals.rank(pct=True)

    # Compute pairwise rank correlations
    available = [n for n in SIGNAL_NAMES if n in ranked_signals.columns]
    corr_matrix = pd.DataFrame(index=available, columns=available, dtype=float)
    corr_n_matrix = pd.DataFrame(index=available, columns=available, dtype=int)

    for i, sig_a in enumerate(available):
        for j, sig_b in enumerate(available):
            pair = pd.DataFrame({
                "a": ranked_signals[sig_a],
                "b": ranked_signals[sig_b],
            }).dropna()
            n = len(pair)
            if n >= 20:
                rho, _ = stats.spearmanr(pair["a"], pair["b"])
            else:
                rho = np.nan
            corr_matrix.loc[sig_a, sig_b] = rho
            corr_n_matrix.loc[sig_a, sig_b] = n

    # Print correlation matrix
    print(f"\n{'':22s}", end="")
    for name in available:
        print(f" {SIGNAL_LABELS[name][:8]:>8s}", end="")
    print()
    print("-" * (22 + 9 * len(available)))
    for i, name_i in enumerate(available):
        print(f"{SIGNAL_LABELS[name_i]:22s}", end="")
        for j, name_j in enumerate(available):
            val = corr_matrix.loc[name_i, name_j]
            if np.isnan(val):
                print(f" {'N/A':>8s}", end="")
            elif i == j:
                print(f" {'1.000':>8s}", end="")
            else:
                print(f" {val:>+8.3f}", end="")
        print()

    # Identify highly correlated pairs (|rho| > 0.3)
    print("\n   Highly correlated pairs (|rho| > 0.30):")
    high_corr_pairs = []
    for i, sig_a in enumerate(available):
        for j, sig_b in enumerate(available):
            if j <= i:
                continue
            val = corr_matrix.loc[sig_a, sig_b]
            if not np.isnan(val) and abs(val) > 0.30:
                high_corr_pairs.append((sig_a, sig_b, val))
                print(f"   {SIGNAL_LABELS[sig_a]:22s} x {SIGNAL_LABELS[sig_b]:22s}: rho = {val:+.3f}")

    if not high_corr_pairs:
        print("   None found — signals are relatively orthogonal!")

    # ── SECTION C: Pairwise combined ICs ──
    print("\n" + "=" * 90)
    print("[6] PAIRWISE COMBINED SIGNAL ICs — Do pairs ADD or CANCEL?")
    print("=" * 90)

    pair_results = []
    for i, sig_a in enumerate(available):
        for j, sig_b in enumerate(available):
            if j <= i:
                continue

            # Combine: average of ranks
            rank_a = ranked_signals[sig_a]
            rank_b = ranked_signals[sig_b]
            combined = (rank_a + rank_b) / 2.0
            combined_rank = combined.rank(pct=True)

            for ret_label in ["btc_7d", "btc_14d"]:
                fwd_ret = fwd_rets[ret_label]

                # Individual ICs (OOS only)
                ic_a_result = compute_ic_with_split(aligned_signals[sig_a], fwd_ret, OOS_START)
                ic_b_result = compute_ic_with_split(aligned_signals[sig_b], fwd_ret, OOS_START)
                ic_combo_result = compute_ic_with_split(combined_rank, fwd_ret, OOS_START)

                ic_a = ic_a_result["oos_ic"]
                ic_b = ic_b_result["oos_ic"]
                ic_combo = ic_combo_result["oos_ic"]
                n_combo = ic_combo_result["oos_n"]

                # Additive = combined IC > max(individual ICs)
                max_individual = max(abs(ic_a) if not np.isnan(ic_a) else 0,
                                     abs(ic_b) if not np.isnan(ic_b) else 0)
                avg_individual = np.nanmean([abs(ic_a), abs(ic_b)])

                if not np.isnan(ic_combo) and max_individual > 0:
                    improvement = (abs(ic_combo) - avg_individual) / avg_individual * 100
                else:
                    improvement = np.nan

                pair_results.append({
                    "sig_a": sig_a, "sig_b": sig_b,
                    "horizon": ret_label,
                    "ic_a": ic_a, "ic_b": ic_b,
                    "ic_combo": ic_combo, "n": n_combo,
                    "improvement_pct": improvement,
                    "corr": corr_matrix.loc[sig_a, sig_b],
                    "additive": not np.isnan(ic_combo) and abs(ic_combo) > max_individual,
                })

    pair_df = pd.DataFrame(pair_results)

    # Print top additive pairs
    for horizon in ["btc_7d", "btc_14d"]:
        h_df = pair_df[pair_df["horizon"] == horizon].copy()
        h_df["abs_combo_ic"] = h_df["ic_combo"].abs()
        h_df = h_df.sort_values("abs_combo_ic", ascending=False)

        print(f"\n   --- {horizon.upper()} HORIZON (OOS) ---")
        print(f"   {'Signal A':22s} {'Signal B':22s} {'IC(A)':>7s} {'IC(B)':>7s} {'IC(AB)':>8s} {'Improv%':>8s} {'Corr':>6s} {'Verdict':>10s}")
        print("   " + "-" * 95)
        for _, row in h_df.head(15).iterrows():
            ic_a_str = f"{row['ic_a']:+.3f}" if not np.isnan(row["ic_a"]) else "  N/A"
            ic_b_str = f"{row['ic_b']:+.3f}" if not np.isnan(row["ic_b"]) else "  N/A"
            ic_combo_str = f"{row['ic_combo']:+.3f}" if not np.isnan(row["ic_combo"]) else "   N/A"
            improv_str = f"{row['improvement_pct']:+.0f}%" if not np.isnan(row["improvement_pct"]) else "  N/A"
            corr_str = f"{row['corr']:+.2f}" if not np.isnan(row["corr"]) else " N/A"
            verdict = "ADDITIVE" if row["additive"] else "OVERLAP"
            print(f"   {SIGNAL_LABELS[row['sig_a']]:22s} {SIGNAL_LABELS[row['sig_b']]:22s} {ic_a_str:>7s} {ic_b_str:>7s} {ic_combo_str:>8s} {improv_str:>8s} {corr_str:>6s} {verdict:>10s}")

    # ── SECTION D: Top-3 and Top-5 composites ──
    print("\n" + "=" * 90)
    print("[7] TOP-N COMPOSITE SIGNALS (equal-weight rank combination)")
    print("=" * 90)

    # Rank individual OOS ICs for BTC 14d to select best signals
    oos_ic_ranking = {}
    for name in available:
        result = compute_ic_with_split(aligned_signals[name], fwd_rets["btc_14d"], OOS_START)
        oos_ic_ranking[name] = abs(result["oos_ic"]) if not np.isnan(result["oos_ic"]) else 0

    sorted_signals = sorted(oos_ic_ranking.items(), key=lambda x: x[1], reverse=True)
    print("\n   Signal ranking by |OOS IC| (BTC 14d):")
    for rank, (name, ic) in enumerate(sorted_signals, 1):
        print(f"   {rank}. {SIGNAL_LABELS[name]:22s}: |IC| = {ic:.3f}")

    # Build composites
    composites = {}
    for n_signals in [3, 5, 7, len(available)]:
        if n_signals > len(available):
            continue
        top_n = [name for name, _ in sorted_signals[:n_signals]]
        label = f"top_{n_signals}"

        # Equal-weight average of ranks
        rank_sum = ranked_signals[top_n].mean(axis=1)
        composites[label] = rank_sum.rank(pct=True)
        composites[f"{label}_signals"] = top_n

    # Evaluate composites
    print(f"\n   {'Composite':22s} | {'IS IC (7d)':>10s} | {'OOS IC (7d)':>11s} | {'IS IC (14d)':>11s} | {'OOS IC (14d)':>12s} | {'OOS n':>5s} | Signals")
    print("   " + "-" * 120)

    composite_results = {}
    for label in ["top_3", "top_5", "top_7", f"top_{len(available)}"]:
        if label not in composites:
            continue
        signals_used = composites[f"{label}_signals"]

        row = f"   {label.upper():22s}"
        results = {}
        for ret_label in ["btc_7d", "btc_14d"]:
            fwd_ret = fwd_rets[ret_label]
            result = compute_ic_with_split(composites[label], fwd_ret, OOS_START)
            results[ret_label] = result
            is_str = f"{result['is_ic']:+.3f}" if not np.isnan(result["is_ic"]) else "   N/A"
            oos_str = f"{result['oos_ic']:+.3f}" if not np.isnan(result["oos_ic"]) else "   N/A"
            row += f" | {is_str:>10s} | {oos_str:>11s}"
        row += f" | {result['oos_n']:5d} | {', '.join([SIGNAL_LABELS[s][:10] for s in signals_used])}"
        composite_results[label] = results
        print(row)

    # ── Compare composites to best individual ──
    print("\n   Composite IC improvement over best individual signal:")
    best_individual_7d = max(
        (abs(individual_ics[n].get("btc_7d", {}).get("oos_ic", 0) or 0), n)
        for n in available
    )
    best_individual_14d = max(
        (abs(individual_ics[n].get("btc_14d", {}).get("oos_ic", 0) or 0), n)
        for n in available
    )

    print(f"   Best individual (7d OOS):  {SIGNAL_LABELS[best_individual_7d[1]]:22s} |IC| = {best_individual_7d[0]:.3f}")
    print(f"   Best individual (14d OOS): {SIGNAL_LABELS[best_individual_14d[1]]:22s} |IC| = {best_individual_14d[0]:.3f}")

    for label in ["top_3", "top_5", "top_7", f"top_{len(available)}"]:
        if label not in composite_results:
            continue
        for horizon_key, best_ind in [("btc_7d", best_individual_7d), ("btc_14d", best_individual_14d)]:
            combo_ic = abs(composite_results[label].get(horizon_key, {}).get("oos_ic", 0) or 0)
            delta = combo_ic - best_ind[0]
            pct_change = (delta / best_ind[0] * 100) if best_ind[0] > 0 else np.nan
            verdict = "ADDITIVE" if delta > 0 else "SUBTRACTIVE"
            print(f"   {label.upper():12s} {horizon_key}: |IC| = {combo_ic:.3f} vs {best_ind[0]:.3f} -> {delta:+.3f} ({pct_change:+.0f}%) [{verdict}]")

    # ── SECTION E: ETH results (if available) ──
    if eth_daily is not None:
        print("\n" + "=" * 90)
        print("[8] ETH COMPOSITE RESULTS")
        print("=" * 90)

        for label in ["top_3", "top_5"]:
            if label not in composites:
                continue
            print(f"\n   {label.upper()}:")
            for ret_label in ["eth_7d", "eth_14d"]:
                if ret_label in fwd_rets:
                    result = compute_ic_with_split(composites[label], fwd_rets[ret_label], OOS_START)
                    is_str = f"{result['is_ic']:+.3f}" if not np.isnan(result["is_ic"]) else "N/A"
                    oos_str = f"{result['oos_ic']:+.3f}" if not np.isnan(result["oos_ic"]) else "N/A"
                    print(f"   {ret_label}: IS IC = {is_str}, OOS IC = {oos_str} (n={result['oos_n']})")

    # ── SECTION F: Redundancy analysis ──
    print("\n" + "=" * 90)
    print("[9] REDUNDANCY vs COMPLEMENTARITY SUMMARY")
    print("=" * 90)

    # Cluster signals by correlation
    print("\n   Signal groups by correlation structure:")
    # Find clusters: if |corr| > 0.4, they're in the same cluster
    assigned = set()
    clusters = []
    for sig in available:
        if sig in assigned:
            continue
        cluster = [sig]
        assigned.add(sig)
        for other in available:
            if other in assigned:
                continue
            val = corr_matrix.loc[sig, other]
            if not np.isnan(val) and abs(val) > 0.40:
                cluster.append(other)
                assigned.add(other)
        clusters.append(cluster)

    for i, cluster in enumerate(clusters, 1):
        labels = [SIGNAL_LABELS[s] for s in cluster]
        if len(cluster) > 1:
            print(f"   Cluster {i} (REDUNDANT): {', '.join(labels)}")
        else:
            print(f"   Cluster {i} (INDEPENDENT): {labels[0]}")

    # Marginal contribution analysis
    print("\n   Marginal contribution (drop-one from Top-5):")
    if "top_5" in composites:
        top5_signals = composites["top_5_signals"]
        full_ic = compute_ic_with_split(composites["top_5"], fwd_rets["btc_14d"], OOS_START)["oos_ic"]
        for drop_sig in top5_signals:
            remaining = [s for s in top5_signals if s != drop_sig]
            if len(remaining) >= 2:
                sub_combo = ranked_signals[remaining].mean(axis=1).rank(pct=True)
                sub_ic = compute_ic_with_split(sub_combo, fwd_rets["btc_14d"], OOS_START)["oos_ic"]
                delta = abs(full_ic or 0) - abs(sub_ic or 0)
                direction = "HELPS" if delta > 0 else "HURTS"
                print(f"   Drop {SIGNAL_LABELS[drop_sig]:22s}: IC {abs(sub_ic or 0):.3f} vs {abs(full_ic or 0):.3f} (delta={delta:+.3f}) [{direction}]")

    # ── Write results to markdown ──
    print("\n" + "=" * 90)
    print("[10] Writing results to markdown...")
    write_results_md(
        individual_ics, corr_matrix, corr_n_matrix, pair_df,
        composite_results, composites, sorted_signals, available,
        clusters, fwd_rets, ranked_signals, aligned_signals,
        best_individual_7d, best_individual_14d,
    )
    print(f"   Saved to: {OUTPUT_PATH}")
    print("=" * 90)
    print("DONE")


def write_results_md(
    individual_ics, corr_matrix, corr_n_matrix, pair_df,
    composite_results, composites, sorted_signals, available,
    clusters, fwd_rets, ranked_signals, aligned_signals,
    best_individual_7d, best_individual_14d,
):
    """Write comprehensive results to markdown."""
    lines = []
    lines.append("# Multi-Signal Stacking Analysis Results")
    lines.append("")
    lines.append("**Research question:** Do 9 individually-proven signals ADD or CANCEL when stacked?")
    lines.append("")
    lines.append(f"**OOS split:** train < 2025-07-01, test >= 2025-07-01")
    lines.append(f"**Generated:** {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("")

    # ── Section 1: Individual ICs ──
    lines.append("## 1. Individual Signal ICs (OOS, sign-aligned)")
    lines.append("")
    lines.append("All signals sign-aligned so positive IC = bullish prediction correct.")
    lines.append("")
    lines.append("| Signal | IS IC (7d) | OOS IC (7d) | IS IC (14d) | OOS IC (14d) | OOS n |")
    lines.append("|--------|-----------|------------|------------|-------------|-------|")
    for name in SIGNAL_NAMES:
        if name not in individual_ics:
            continue
        ics = individual_ics[name]
        row_parts = [f"| {SIGNAL_LABELS[name]}"]
        for ret_label in ["btc_7d", "btc_14d"]:
            if ret_label in ics:
                r = ics[ret_label]
                is_str = f"{r['is_ic']:+.3f}" if not np.isnan(r["is_ic"]) else "N/A"
                oos_str = f"{r['oos_ic']:+.3f}" if not np.isnan(r["oos_ic"]) else "N/A"
                row_parts.append(f" {is_str} | {oos_str}")
            else:
                row_parts.append(" N/A | N/A")
        n = ics.get("btc_14d", {}).get("oos_n", 0)
        row_parts.append(f" {n} |")
        lines.append(" |".join(row_parts))
    lines.append("")

    # ── Section 2: Correlation matrix ──
    lines.append("## 2. Pairwise Signal Correlation Matrix")
    lines.append("")
    lines.append("Spearman rank correlations on overlapping date ranges.")
    lines.append("")

    # Abbreviated labels for compact table
    abbrev = {n: SIGNAL_LABELS[n][:10] for n in available}
    header = "| Signal | " + " | ".join(abbrev[n] for n in available) + " |"
    sep = "|--------|" + "|".join(["--------"] * len(available)) + "|"
    lines.append(header)
    lines.append(sep)
    for name_i in available:
        row = f"| {abbrev[name_i]:10s}"
        for name_j in available:
            val = corr_matrix.loc[name_i, name_j]
            if name_i == name_j:
                row += " | 1.000"
            elif np.isnan(val):
                row += " | N/A"
            else:
                row += f" | {val:+.3f}"
        row += " |"
        lines.append(row)
    lines.append("")

    # Highlight high correlations
    lines.append("**Highly correlated pairs (|rho| > 0.30):**")
    found_high = False
    for i, sig_a in enumerate(available):
        for j, sig_b in enumerate(available):
            if j <= i:
                continue
            val = corr_matrix.loc[sig_a, sig_b]
            if not np.isnan(val) and abs(val) > 0.30:
                lines.append(f"- {SIGNAL_LABELS[sig_a]} x {SIGNAL_LABELS[sig_b]}: rho = {val:+.3f}")
                found_high = True
    if not found_high:
        lines.append("- None found -- signals are relatively orthogonal!")
    lines.append("")

    # ── Section 3: Pairwise combined ICs ──
    lines.append("## 3. Best Pairwise Combinations (OOS)")
    lines.append("")
    for horizon in ["btc_7d", "btc_14d"]:
        h_df = pair_df[pair_df["horizon"] == horizon].copy()
        h_df["abs_combo_ic"] = h_df["ic_combo"].abs()
        h_df = h_df.sort_values("abs_combo_ic", ascending=False)

        lines.append(f"### {horizon.replace('btc_', 'BTC ').upper()}")
        lines.append("")
        lines.append("| Signal A | Signal B | IC(A) | IC(B) | IC(A+B) | Improvement | Corr | Verdict |")
        lines.append("|----------|----------|-------|-------|---------|------------|------|---------|")
        for _, row in h_df.head(10).iterrows():
            ic_a_str = f"{row['ic_a']:+.3f}" if not np.isnan(row["ic_a"]) else "N/A"
            ic_b_str = f"{row['ic_b']:+.3f}" if not np.isnan(row["ic_b"]) else "N/A"
            ic_combo_str = f"{row['ic_combo']:+.3f}" if not np.isnan(row["ic_combo"]) else "N/A"
            improv_str = f"{row['improvement_pct']:+.0f}%" if not np.isnan(row["improvement_pct"]) else "N/A"
            corr_str = f"{row['corr']:+.2f}" if not np.isnan(row["corr"]) else "N/A"
            verdict = "ADDITIVE" if row["additive"] else "OVERLAP"
            lines.append(f"| {SIGNAL_LABELS[row['sig_a']]} | {SIGNAL_LABELS[row['sig_b']]} | {ic_a_str} | {ic_b_str} | {ic_combo_str} | {improv_str} | {corr_str} | {verdict} |")
        lines.append("")

    # ── Section 4: Top-N composites ──
    lines.append("## 4. Top-N Composite Signals")
    lines.append("")

    lines.append("**Signal ranking by |OOS IC| (BTC 14d):**")
    for rank, (name, ic) in enumerate(sorted_signals, 1):
        lines.append(f"{rank}. {SIGNAL_LABELS[name]}: |IC| = {ic:.3f}")
    lines.append("")

    lines.append("| Composite | IS IC (7d) | OOS IC (7d) | IS IC (14d) | OOS IC (14d) | Signals |")
    lines.append("|-----------|-----------|------------|------------|-------------|---------|")
    for label in ["top_3", "top_5", "top_7", f"top_{len(available)}"]:
        if label not in composite_results:
            continue
        signals_used = composites[f"{label}_signals"]
        sig_str = ", ".join([SIGNAL_LABELS[s][:12] for s in signals_used])
        row = f"| {label.upper()}"
        for ret_label in ["btc_7d", "btc_14d"]:
            r = composite_results[label].get(ret_label, {})
            is_str = f"{r.get('is_ic', float('nan')):+.3f}" if not np.isnan(r.get("is_ic", float("nan"))) else "N/A"
            oos_str = f"{r.get('oos_ic', float('nan')):+.3f}" if not np.isnan(r.get("oos_ic", float("nan"))) else "N/A"
            row += f" | {is_str} | {oos_str}"
        row += f" | {sig_str} |"
        lines.append(row)
    lines.append("")

    # Improvement analysis
    lines.append("**Composite vs best individual:**")
    lines.append("")
    lines.append(f"- Best individual (7d OOS): {SIGNAL_LABELS[best_individual_7d[1]]} |IC| = {best_individual_7d[0]:.3f}")
    lines.append(f"- Best individual (14d OOS): {SIGNAL_LABELS[best_individual_14d[1]]} |IC| = {best_individual_14d[0]:.3f}")
    lines.append("")

    for label in ["top_3", "top_5", "top_7", f"top_{len(available)}"]:
        if label not in composite_results:
            continue
        for horizon_key, best_ind in [("btc_7d", best_individual_7d), ("btc_14d", best_individual_14d)]:
            combo_ic = abs(composite_results[label].get(horizon_key, {}).get("oos_ic", 0) or 0)
            delta = combo_ic - best_ind[0]
            pct_change = (delta / best_ind[0] * 100) if best_ind[0] > 0 else float("nan")
            verdict = "ADDITIVE" if delta > 0 else "SUBTRACTIVE"
            lines.append(f"- {label.upper()} {horizon_key}: |IC| = {combo_ic:.3f} vs {best_ind[0]:.3f} ({delta:+.3f}, {pct_change:+.0f}%) [{verdict}]")
    lines.append("")

    # ── Section 5: Redundancy analysis ──
    lines.append("## 5. Redundancy vs Complementarity")
    lines.append("")
    lines.append("### Signal clusters (|corr| > 0.40 threshold)")
    for i, cluster in enumerate(clusters, 1):
        labels = [SIGNAL_LABELS[s] for s in cluster]
        if len(cluster) > 1:
            lines.append(f"- **Cluster {i} (REDUNDANT):** {', '.join(labels)}")
        else:
            lines.append(f"- **Cluster {i} (INDEPENDENT):** {labels[0]}")
    lines.append("")

    # Marginal contribution
    lines.append("### Marginal contribution (drop-one from Top-5)")
    if "top_5" in composites:
        top5_signals = composites["top_5_signals"]
        full_ic = compute_ic_with_split(composites["top_5"], fwd_rets["btc_14d"], OOS_START)["oos_ic"]
        for drop_sig in top5_signals:
            remaining = [s for s in top5_signals if s != drop_sig]
            if len(remaining) >= 2:
                sub_combo = ranked_signals[remaining].mean(axis=1).rank(pct=True)
                sub_ic = compute_ic_with_split(sub_combo, fwd_rets["btc_14d"], OOS_START)["oos_ic"]
                delta = abs(full_ic or 0) - abs(sub_ic or 0)
                direction = "HELPS" if delta > 0 else "HURTS"
                lines.append(f"- Drop {SIGNAL_LABELS[drop_sig]}: IC {abs(sub_ic or 0):.3f} vs {abs(full_ic or 0):.3f} (delta={delta:+.3f}) [{direction}]")
    lines.append("")

    # ── Section 6: Key conclusions ──
    lines.append("## 6. Key Conclusions")
    lines.append("")

    # Auto-generate conclusions based on results
    # Count additive vs overlap pairs
    oos_14d = pair_df[pair_df["horizon"] == "btc_14d"]
    n_additive = oos_14d["additive"].sum()
    n_total = len(oos_14d)
    pct_additive = n_additive / n_total * 100 if n_total > 0 else 0

    lines.append(f"1. **Pairwise additivity:** {n_additive}/{n_total} pairs ({pct_additive:.0f}%) show additive IC at 14d horizon")
    lines.append("")

    # Best composite
    best_comp = None
    best_comp_ic = 0
    for label in ["top_3", "top_5", "top_7"]:
        if label in composite_results:
            ic = abs(composite_results[label].get("btc_14d", {}).get("oos_ic", 0) or 0)
            if ic > best_comp_ic:
                best_comp = label
                best_comp_ic = ic

    if best_comp:
        lines.append(f"2. **Best composite:** {best_comp.upper()} with OOS |IC| = {best_comp_ic:.3f} at 14d")
        delta = best_comp_ic - best_individual_14d[0]
        if delta > 0:
            lines.append(f"   - This is {delta:+.3f} ({delta/best_individual_14d[0]*100:+.0f}%) better than the best individual signal")
        else:
            lines.append(f"   - This is {delta:+.3f} ({delta/best_individual_14d[0]*100:+.0f}%) vs the best individual signal — diminishing returns from stacking")
    lines.append("")

    # Redundant groups
    redundant = [c for c in clusters if len(c) > 1]
    if redundant:
        lines.append(f"3. **Redundant signal groups:** {len(redundant)} cluster(s) with |corr| > 0.40")
        for c in redundant:
            labels = [SIGNAL_LABELS[s] for s in c]
            lines.append(f"   - {', '.join(labels)} — pick the one with highest individual IC")
    else:
        lines.append("3. **No redundant groups:** All signals have |pairwise corr| < 0.40 — good orthogonality")
    lines.append("")

    lines.append("4. **Recommendation:** Use the composite with the best OOS IC improvement over individual signals. "
                 "Drop signals that hurt marginal contribution (negative delta in drop-one analysis).")
    lines.append("")

    with open(OUTPUT_PATH, "w") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    main()
