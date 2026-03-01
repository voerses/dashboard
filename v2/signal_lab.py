"""
Signal Lab — Fast signal evaluation framework for crypto backtesting.

Instead of running full backtests on 149 tokens (hours), compute the Information
Coefficient (IC) of individual signals and combine the best ones (minutes).

IC = Spearman rank correlation between signal value today and forward return.
|IC| > 0.03 is useful. IC > 0.05 is strong. IC > 0.10 is exceptional.

Usage: python signal_lab.py
"""

import sys
import os
import time
import warnings

import numpy as np
import pandas as pd
from scipy import stats as scipy_stats

warnings.filterwarnings("ignore", category=RuntimeWarning)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.makedirs("outputs_v2", exist_ok=True)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DATA_DIR = os.path.join(os.path.dirname(__file__), "real_data")
ETF_DATE = pd.Timestamp("2024-01-10")
MIN_HISTORY_DAYS = 730
FORWARD_WINDOWS = [1, 5, 20]
PRIMARY_FORWARD = 5  # main IC computed against 5d forward return


# ===========================================================================
# Data Loading
# ===========================================================================
def load_all_tokens(data_dir: str, min_days: int = MIN_HISTORY_DAYS) -> pd.DataFrame:
    """Load all CSVs into a single long-format DataFrame with a 'token' column."""
    frames = []
    files = sorted(f for f in os.listdir(data_dir) if f.endswith("_daily.csv"))
    for fname in files:
        token = fname.replace("_daily.csv", "")
        path = os.path.join(data_dir, fname)
        df = pd.read_csv(path, parse_dates=["date"])
        if len(df) < min_days:
            continue
        df["token"] = token
        frames.append(df)
    combined = pd.concat(frames, ignore_index=True)
    combined.sort_values(["token", "date"], inplace=True)
    combined.reset_index(drop=True, inplace=True)
    print(f"Loaded {combined['token'].nunique()} tokens with {min_days}+ days of data")
    print(f"Total rows: {len(combined):,}")
    return combined


def load_btc(data_dir: str) -> pd.DataFrame:
    """Load BTC data separately for cross-asset signals."""
    path = os.path.join(data_dir, "BTC_daily.csv")
    btc = pd.read_csv(path, parse_dates=["date"])
    btc.sort_values("date", inplace=True)
    btc.reset_index(drop=True, inplace=True)
    return btc


# ===========================================================================
# Forward Returns (vectorized per-group)
# ===========================================================================
def compute_forward_returns(df: pd.DataFrame, windows: list[int]) -> pd.DataFrame:
    """Add fwd_Nd columns = (close[t+N] / close[t]) - 1, computed per token."""
    for w in windows:
        df[f"fwd_{w}d"] = df.groupby("token")["close"].transform(
            lambda s: s.shift(-w) / s - 1
        )
    return df


# ===========================================================================
# Signal Definitions
# ===========================================================================
# Each signal function takes (group: DataFrame for one token, btc: DataFrame)
# and returns a Series aligned to group.index.


def _ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False).mean()


def _sma(s: pd.Series, window: int) -> pd.Series:
    return s.rolling(window, min_periods=window).mean()


def _std(s: pd.Series, window: int) -> pd.Series:
    return s.rolling(window, min_periods=window).std()


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr.rolling(period, min_periods=period).mean()


def _adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 20) -> pd.Series:
    """Average Directional Index."""
    plus_dm = high.diff().clip(lower=0)
    minus_dm = (-low.diff()).clip(lower=0)
    # zero out where the other is larger
    plus_dm[plus_dm < minus_dm] = 0
    minus_dm[minus_dm < plus_dm] = 0
    atr = _atr(high, low, close, period)
    plus_di = 100 * _ema(plus_dm, period) / atr.replace(0, np.nan)
    minus_di = 100 * _ema(minus_dm, period) / atr.replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return _ema(dx, period)


# --- Signal registry ---
SIGNAL_FUNCTIONS: dict[str, callable] = {}


def register_signal(name: str):
    """Decorator to register a signal function."""
    def decorator(fn):
        SIGNAL_FUNCTIONS[name] = fn
        return fn
    return decorator


# ---- Momentum / Trend ----

@register_signal("ret_5d")
def sig_ret_5d(g, btc):
    return g["close"].pct_change(5)

@register_signal("ret_10d")
def sig_ret_10d(g, btc):
    return g["close"].pct_change(10)

@register_signal("ret_20d")
def sig_ret_20d(g, btc):
    return g["close"].pct_change(20)

@register_signal("ret_60d")
def sig_ret_60d(g, btc):
    return g["close"].pct_change(60)

@register_signal("ema_cross")
def sig_ema_cross(g, btc):
    return _ema(g["close"], 12) / _ema(g["close"], 26) - 1

@register_signal("macd_signal")
def sig_macd_signal(g, btc):
    macd_line = _ema(g["close"], 12) - _ema(g["close"], 26)
    signal_line = _ema(macd_line, 9)
    return macd_line - signal_line

@register_signal("adx_20")
def sig_adx_20(g, btc):
    return _adx(g["high"], g["low"], g["close"], 20)

@register_signal("donchian_pos")
def sig_donchian_pos(g, btc):
    high_20 = g["high"].rolling(20, min_periods=20).max()
    low_20 = g["low"].rolling(20, min_periods=20).min()
    rng = (high_20 - low_20).replace(0, np.nan)
    return (g["close"] - low_20) / rng


# ---- Mean Reversion ----

@register_signal("zscore_20")
def sig_zscore_20(g, btc):
    sma = _sma(g["close"], 20)
    std = _std(g["close"], 20)
    return (g["close"] - sma) / std.replace(0, np.nan)

@register_signal("zscore_50")
def sig_zscore_50(g, btc):
    sma = _sma(g["close"], 50)
    std = _std(g["close"], 50)
    return (g["close"] - sma) / std.replace(0, np.nan)

@register_signal("rsi_14")
def sig_rsi_14(g, btc):
    return _rsi(g["close"], 14) - 50

@register_signal("bb_pct")
def sig_bb_pct(g, btc):
    sma = _sma(g["close"], 20)
    std = _std(g["close"], 20)
    upper = sma + 2 * std
    lower = sma - 2 * std
    rng = (upper - lower).replace(0, np.nan)
    return (g["close"] - lower) / rng


# ---- Volatility ----

@register_signal("rvol_ratio")
def sig_rvol_ratio(g, btc):
    log_ret = np.log(g["close"] / g["close"].shift(1))
    vol_10 = log_ret.rolling(10, min_periods=10).std()
    vol_60 = log_ret.rolling(60, min_periods=60).std()
    return vol_10 / vol_60.replace(0, np.nan)

@register_signal("vol_of_vol")
def sig_vol_of_vol(g, btc):
    log_ret = np.log(g["close"] / g["close"].shift(1))
    vol_10 = log_ret.rolling(10, min_periods=10).std()
    return vol_10.rolling(20, min_periods=20).std()

@register_signal("garman_klass")
def sig_garman_klass(g, btc):
    log_hl = np.log(g["high"] / g["low"])
    log_co = np.log(g["close"] / g["open"])
    gk_daily = 0.5 * log_hl ** 2 - (2 * np.log(2) - 1) * log_co ** 2
    return gk_daily.rolling(20, min_periods=20).mean()

@register_signal("parkinson")
def sig_parkinson(g, btc):
    log_hl = np.log(g["high"] / g["low"])
    pk_daily = log_hl ** 2 / (4 * np.log(2))
    return pk_daily.rolling(20, min_periods=20).mean()

@register_signal("range_compression")
def sig_range_compression(g, btc):
    atr5 = _atr(g["high"], g["low"], g["close"], 5)
    atr20 = _atr(g["high"], g["low"], g["close"], 20)
    return atr5 / atr20.replace(0, np.nan)


# ---- Volume / Microstructure ----

@register_signal("volume_momentum")
def sig_volume_momentum(g, btc):
    sma_vol = _sma(g["volume"], 20)
    return g["volume"] / sma_vol.replace(0, np.nan)

@register_signal("obv_slope")
def sig_obv_slope(g, btc):
    sign = np.sign(g["close"].diff())
    obv = (sign * g["volume"]).cumsum()
    # slope over 10 days via simple diff
    return obv.diff(10) / (obv.rolling(10, min_periods=10).std().replace(0, np.nan))

@register_signal("amihud")
def sig_amihud(g, btc):
    abs_ret = g["close"].pct_change().abs()
    dollar_vol = g["close"] * g["volume"]
    daily_illiq = abs_ret / dollar_vol.replace(0, np.nan)
    return daily_illiq.rolling(10, min_periods=10).mean()

@register_signal("close_location")
def sig_close_location(g, btc):
    rng = (g["high"] - g["low"]).replace(0, np.nan)
    return (g["close"] - g["low"]) / rng

@register_signal("vpin_approx")
def sig_vpin_approx(g, btc):
    """Approximate VPIN: |buy_vol - sell_vol| / total_vol over 20 bars.
    Buy/sell classified by close vs midpoint of high-low."""
    mid = (g["high"] + g["low"]) / 2
    buy_frac = (g["close"] - mid) / (g["high"] - g["low"]).replace(0, np.nan)
    buy_frac = buy_frac.clip(-1, 1)
    buy_vol = (0.5 + 0.5 * buy_frac) * g["volume"]
    sell_vol = g["volume"] - buy_vol
    imbalance = (buy_vol - sell_vol).abs()
    total = g["volume"].rolling(20, min_periods=20).sum()
    return imbalance.rolling(20, min_periods=20).sum() / total.replace(0, np.nan)


# ---- Cross-Asset (BTC) ----

@register_signal("btc_beta_20")
def sig_btc_beta_20(g, btc):
    token_ret = g["close"].pct_change()
    btc_merged = g[["date"]].merge(btc[["date", "btc_ret"]], on="date", how="left")
    btc_ret = btc_merged["btc_ret"].values
    btc_s = pd.Series(btc_ret, index=g.index)
    cov = token_ret.rolling(20, min_periods=20).cov(btc_s)
    var_btc = btc_s.rolling(20, min_periods=20).var()
    return cov / var_btc.replace(0, np.nan)

@register_signal("btc_corr_20")
def sig_btc_corr_20(g, btc):
    token_ret = g["close"].pct_change()
    btc_merged = g[["date"]].merge(btc[["date", "btc_ret"]], on="date", how="left")
    btc_ret = btc_merged["btc_ret"].values
    btc_s = pd.Series(btc_ret, index=g.index)
    return token_ret.rolling(20, min_periods=20).corr(btc_s)

@register_signal("btc_lead_5d")
def sig_btc_lead_5d(g, btc):
    btc_5d = btc[["date"]].copy()
    btc_5d["btc_ret_5d"] = btc["close"].pct_change(5)
    merged = g[["date"]].merge(btc_5d, on="date", how="left")
    return pd.Series(merged["btc_ret_5d"].values, index=g.index)

@register_signal("relative_strength")
def sig_relative_strength(g, btc):
    token_ret_20 = g["close"].pct_change(20)
    btc_20 = btc[["date"]].copy()
    btc_20["btc_ret_20d"] = btc["close"].pct_change(20)
    merged = g[["date"]].merge(btc_20, on="date", how="left")
    btc_r = pd.Series(merged["btc_ret_20d"].values, index=g.index)
    return token_ret_20 - btc_r


# ---- Contrarian / Crowding ----

@register_signal("mean_reversion_5d")
def sig_mean_reversion_5d(g, btc):
    return -1 * g["close"].pct_change(5)

@register_signal("vol_adjusted_momentum")
def sig_vol_adjusted_momentum(g, btc):
    ret_20 = g["close"].pct_change(20)
    log_ret = np.log(g["close"] / g["close"].shift(1))
    vol_20 = log_ret.rolling(20, min_periods=20).std()
    return ret_20 / vol_20.replace(0, np.nan)

@register_signal("liquidity_adjusted_ret")
def sig_liquidity_adjusted_ret(g, btc):
    ret_20 = g["close"].pct_change(20)
    dollar_vol = g["close"] * g["volume"]
    avg_dv = dollar_vol.rolling(20, min_periods=20).mean()
    return ret_20 * np.sqrt(avg_dv.clip(lower=0))


# ===========================================================================
# Signal Computation Engine
# ===========================================================================
def compute_all_signals(df: pd.DataFrame, btc: pd.DataFrame) -> pd.DataFrame:
    """Compute all registered signals for every token-day. Returns df with signal columns."""
    # Pre-compute BTC returns for cross-asset signals
    btc = btc.copy()
    btc["btc_ret"] = btc["close"].pct_change()

    signal_names = list(SIGNAL_FUNCTIONS.keys())
    print(f"\nComputing {len(signal_names)} signals...")

    # Initialize signal columns
    for name in signal_names:
        df[name] = np.nan

    # Compute per token group
    tokens = df["token"].unique()
    t0 = time.time()
    for i, token in enumerate(tokens):
        mask = df["token"] == token
        g = df.loc[mask].copy()
        for name, fn in SIGNAL_FUNCTIONS.items():
            try:
                vals = fn(g, btc)
                df.loc[mask, name] = vals.values if hasattr(vals, "values") else vals
            except Exception:
                pass  # leave as NaN
        if (i + 1) % 25 == 0:
            elapsed = time.time() - t0
            print(f"  {i + 1}/{len(tokens)} tokens ({elapsed:.1f}s)")

    elapsed = time.time() - t0
    print(f"  Done: {len(tokens)} tokens in {elapsed:.1f}s")
    return df


# ===========================================================================
# IC Computation
# ===========================================================================
def spearman_ic(signal: pd.Series, forward_ret: pd.Series) -> float:
    """Spearman rank correlation (IC) between signal and forward return."""
    valid = signal.notna() & forward_ret.notna() & np.isfinite(signal) & np.isfinite(forward_ret)
    s = signal[valid]
    r = forward_ret[valid]
    if len(s) < 30:
        return np.nan
    corr, _ = scipy_stats.spearmanr(s, r)
    return corr


def compute_monthly_ic(
    df: pd.DataFrame, signal_name: str, fwd_col: str, mask: pd.Series
) -> pd.Series:
    """Compute IC per month for a signal (cross-sectional: rank tokens each day, then monthly avg)."""
    sub = df.loc[mask, ["date", signal_name, fwd_col]].dropna()
    if len(sub) < 60:
        return pd.Series(dtype=float)
    sub["month"] = sub["date"].dt.to_period("M")
    monthly_ics = []
    for _, mdf in sub.groupby("month"):
        if len(mdf) < 10:
            continue
        ic = scipy_stats.spearmanr(mdf[signal_name], mdf[fwd_col])[0]
        monthly_ics.append(ic)
    return pd.Series(monthly_ics)


def compute_turnover(df: pd.DataFrame, signal_name: str) -> float:
    """Average daily rank change of a signal across tokens."""
    sub = df[["date", "token", signal_name]].dropna()
    if len(sub) < 100:
        return np.nan
    # Rank cross-sectionally each day
    sub["rank"] = sub.groupby("date")[signal_name].rank(pct=True)
    # Average absolute daily rank change per token
    sub["rank_change"] = sub.groupby("token")["rank"].diff().abs()
    return sub["rank_change"].mean()


def evaluate_signals(df: pd.DataFrame) -> pd.DataFrame:
    """Compute IC metrics for every signal."""
    fwd_col = f"fwd_{PRIMARY_FORWARD}d"
    signal_names = list(SIGNAL_FUNCTIONS.keys())

    mask_pre = df["date"] < ETF_DATE
    mask_post = df["date"] >= ETF_DATE

    results = []
    print(f"\nEvaluating {len(signal_names)} signals against {fwd_col}...")

    for name in signal_names:
        sig = df[name]
        fwd = df[fwd_col]

        # Overall IC
        valid = sig.notna() & fwd.notna() & np.isfinite(sig) & np.isfinite(fwd)
        ic_all = spearman_ic(sig, fwd)

        # Pre/post ETF IC
        ic_pre = spearman_ic(sig[mask_pre], fwd[mask_pre])
        ic_post = spearman_ic(sig[mask_post], fwd[mask_post])
        ic_change = (ic_post - ic_pre) if (not np.isnan(ic_pre) and not np.isnan(ic_post)) else np.nan

        # IC stability (t-stat of monthly ICs)
        monthly_ics = compute_monthly_ic(df, name, fwd_col, valid)
        if len(monthly_ics) > 2:
            ic_mean = monthly_ics.mean()
            ic_std = monthly_ics.std()
            ic_stability = ic_mean / (ic_std / np.sqrt(len(monthly_ics))) if ic_std > 0 else np.nan
        else:
            ic_stability = np.nan

        # Turnover
        turnover = compute_turnover(df, name)

        # Count observations
        n_obs = valid.sum()

        results.append({
            "signal": name,
            "IC_all": ic_all,
            "IC_pre_etf": ic_pre,
            "IC_post_etf": ic_post,
            "IC_change": ic_change,
            "IC_stability_tstat": ic_stability,
            "turnover": turnover,
            "n_obs": int(n_obs),
        })

    return pd.DataFrame(results)


# ===========================================================================
# Composite Signal (IC-weighted combination)
# ===========================================================================
def compute_composite(df: pd.DataFrame, eval_df: pd.DataFrame, top_n: int = 10) -> pd.DataFrame:
    """Build IC-weighted composite signal from the top_n signals by |IC_post_etf|."""
    ranked = eval_df.dropna(subset=["IC_post_etf"]).copy()
    ranked["abs_ic"] = ranked["IC_post_etf"].abs()
    ranked = ranked.sort_values("abs_ic", ascending=False).head(top_n)

    print(f"\n--- Composite Signal (top {top_n} by |IC_post_etf|) ---")
    signal_names = ranked["signal"].tolist()
    ic_weights = ranked["IC_post_etf"].values

    # Normalize: cross-sectional z-score each signal per day, then IC-weight
    composite = pd.Series(0.0, index=df.index)
    for name, w in zip(signal_names, ic_weights):
        sig = df[name].copy()
        # Cross-sectional z-score per day
        mean = df.groupby("date")[name].transform("mean")
        std = df.groupby("date")[name].transform("std")
        z = (sig - mean) / std.replace(0, np.nan)
        z = z.clip(-3, 3).fillna(0)
        composite += w * z
        print(f"  {name:30s}  IC_weight={w:+.4f}")

    df["composite"] = composite
    fwd_col = f"fwd_{PRIMARY_FORWARD}d"
    mask_post = df["date"] >= ETF_DATE
    ic_composite = spearman_ic(composite[mask_post], df.loc[mask_post, fwd_col])
    print(f"\n  Composite IC (post-ETF): {ic_composite:.4f}")

    # Also compute for other forward windows
    for w in FORWARD_WINDOWS:
        fc = f"fwd_{w}d"
        ic_w = spearman_ic(composite[mask_post], df.loc[mask_post, fc])
        print(f"  Composite IC (post-ETF, {w}d fwd): {ic_w:.4f}")

    return df


# ===========================================================================
# Display & Output
# ===========================================================================
def print_results(eval_df: pd.DataFrame):
    """Print formatted evaluation results."""
    pd.set_option("display.float_format", "{:.4f}".format)
    pd.set_option("display.max_rows", 100)
    pd.set_option("display.width", 140)

    sorted_df = eval_df.sort_values("IC_post_etf", key=abs, ascending=False)

    print("\n" + "=" * 120)
    print("SIGNAL EVALUATION RESULTS")
    print(f"Sorted by |IC_post_etf| (rank correlation with {PRIMARY_FORWARD}d forward return)")
    print("=" * 120)
    print(sorted_df.to_string(index=False))

    # Highlight regime changes
    print("\n" + "-" * 80)
    print("REGIME ANALYSIS (Pre-ETF vs Post-ETF)")
    print("-" * 80)

    improved = sorted_df[sorted_df["IC_change"] > 0.01].sort_values("IC_change", ascending=False)
    degraded = sorted_df[sorted_df["IC_change"] < -0.01].sort_values("IC_change")
    flipped = sorted_df[
        (sorted_df["IC_pre_etf"] * sorted_df["IC_post_etf"] < 0)
        & (sorted_df["IC_pre_etf"].abs() > 0.01)
        & (sorted_df["IC_post_etf"].abs() > 0.01)
    ]

    if len(improved) > 0:
        print("\nSIGNALS THAT IMPROVED POST-ETF:")
        for _, row in improved.iterrows():
            print(f"  {row['signal']:30s}  pre={row['IC_pre_etf']:+.4f}  post={row['IC_post_etf']:+.4f}  change={row['IC_change']:+.4f}")

    if len(degraded) > 0:
        print("\nSIGNALS THAT DEGRADED POST-ETF:")
        for _, row in degraded.iterrows():
            print(f"  {row['signal']:30s}  pre={row['IC_pre_etf']:+.4f}  post={row['IC_post_etf']:+.4f}  change={row['IC_change']:+.4f}")

    if len(flipped) > 0:
        print("\nSIGNALS THAT FLIPPED SIGN:")
        for _, row in flipped.iterrows():
            print(f"  {row['signal']:30s}  pre={row['IC_pre_etf']:+.4f}  post={row['IC_post_etf']:+.4f}")

    # Stability ranking
    print("\n" + "-" * 80)
    print("MOST STABLE SIGNALS (by |IC t-stat|)")
    print("-" * 80)
    stable = sorted_df.dropna(subset=["IC_stability_tstat"]).copy()
    stable["abs_tstat"] = stable["IC_stability_tstat"].abs()
    stable = stable.sort_values("abs_tstat", ascending=False).head(10)
    for _, row in stable.iterrows():
        direction = "+" if row["IC_stability_tstat"] > 0 else "-"
        print(f"  {row['signal']:30s}  t-stat={row['IC_stability_tstat']:+.2f}  IC_post={row['IC_post_etf']:+.4f}")


def save_results(eval_df: pd.DataFrame, output_dir: str = "outputs_v2"):
    """Save evaluation results to CSV."""
    path = os.path.join(output_dir, "signal_evaluation.csv")
    eval_df.sort_values("IC_post_etf", key=abs, ascending=False).to_csv(path, index=False)
    print(f"\nResults saved to {path}")


# ===========================================================================
# Main
# ===========================================================================
def main():
    t_start = time.time()

    # Load data
    df = load_all_tokens(DATA_DIR, min_days=MIN_HISTORY_DAYS)
    btc = load_btc(DATA_DIR)

    # Compute forward returns
    df = compute_forward_returns(df, FORWARD_WINDOWS)

    # Compute all signals
    df = compute_all_signals(df, btc)

    # Evaluate signals
    eval_df = evaluate_signals(df)

    # Print results
    print_results(eval_df)

    # Composite signal
    df = compute_composite(df, eval_df, top_n=10)

    # Save
    save_results(eval_df)

    elapsed = time.time() - t_start
    print(f"\nTotal runtime: {elapsed:.1f}s")


if __name__ == "__main__":
    main()
