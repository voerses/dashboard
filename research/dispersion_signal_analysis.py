"""
Cross-Token DISPERSION Signal Analysis
=======================================
Hypothesis: When cross-sectional dispersion (spread of returns across tokens) is high,
there is more opportunity for momentum/selection strategies. When dispersion is low,
everything moves together and alpha is harder to find.

Signals computed:
  1. Cross-sectional dispersion: std(24h returns across all tokens)
  2. Cross-sectional mean correlation: mean pairwise 14d rolling correlation
  3. Dispersion z-score (rolling 60d window)
  4. Herfindahl concentration of returns

Targets:
  - Equal-weight basket forward returns (1d, 7d, 14d)
  - Top quintile momentum returns
  - Momentum spread (top vs bottom quintile)

Temporal split: train < 2025-07-01, test >= 2025-07-01
"""

import os
import warnings
import numpy as np
import pandas as pd
from scipy import stats as sp_stats
from pathlib import Path

warnings.filterwarnings("ignore")

# ============================================================
# 1. DATA LOADING
# ============================================================
DATA_DIR = Path("/workspace/crypto_backtest/data/perp/1h_cache")
TRAIN_CUTOFF = pd.Timestamp("2025-07-01")
MIN_HISTORY_DAYS = 90  # tokens must have at least 90 days to be included

print("=" * 80)
print("CROSS-TOKEN DISPERSION SIGNAL ANALYSIS")
print("=" * 80)

print("\n[1] Loading hourly close prices for all tokens...")

close_frames = {}
files = sorted(DATA_DIR.glob("*_1h.parquet"))
for f in files:
    token = f.stem.replace("_1h", "")
    try:
        df = pd.read_parquet(f, columns=["close"])
        df.index = pd.to_datetime(df.index)
        if len(df) < 24 * MIN_HISTORY_DAYS:
            continue
        close_frames[token] = df["close"]
    except Exception as e:
        pass

print(f"   Loaded {len(close_frames)} tokens with >= {MIN_HISTORY_DAYS} days of data")

# Build hourly close price panel
hourly_close = pd.DataFrame(close_frames)
hourly_close = hourly_close.sort_index()

# Resample to daily close (last hourly bar of each day)
daily_close = hourly_close.resample("1D").last()
daily_close = daily_close.dropna(how="all")

# Require at least 20 tokens per day
min_tokens_mask = daily_close.notna().sum(axis=1) >= 20
daily_close = daily_close.loc[min_tokens_mask]

print(f"   Daily close panel: {daily_close.shape[0]} days x {daily_close.shape[1]} tokens")
print(f"   Date range: {daily_close.index.min().date()} to {daily_close.index.max().date()}")

# Daily returns
daily_returns = daily_close.pct_change()
# Drop first row (NaN)
daily_returns = daily_returns.iloc[1:]

# Count tokens per day
tokens_per_day = daily_returns.notna().sum(axis=1)
print(f"   Median tokens per day: {tokens_per_day.median():.0f}")

# ============================================================
# 2. SIGNAL CONSTRUCTION
# ============================================================
print("\n[2] Computing dispersion signals...")

signals = pd.DataFrame(index=daily_returns.index)

# --- 2a. Cross-sectional dispersion: std of daily returns across tokens ---
signals["dispersion"] = daily_returns.std(axis=1)

# --- 2b. Cross-sectional mean absolute return (level of returns) ---
signals["mean_abs_return"] = daily_returns.abs().mean(axis=1)

# --- 2c. Cross-sectional dispersion z-score (rolling 60d) ---
roll_mean = signals["dispersion"].rolling(60, min_periods=30).mean()
roll_std = signals["dispersion"].rolling(60, min_periods=30).std()
signals["dispersion_zscore"] = (signals["dispersion"] - roll_mean) / roll_std

# --- 2d. Herfindahl concentration of squared returns ---
# Measures if a few tokens dominate returns
def herfindahl_daily(row):
    r = row.dropna()
    if len(r) < 10:
        return np.nan
    sq = r ** 2
    total = sq.sum()
    if total == 0:
        return np.nan
    shares = sq / total
    return (shares ** 2).sum()

signals["herfindahl"] = daily_returns.apply(herfindahl_daily, axis=1)

# --- 2e. Cross-sectional mean pairwise correlation (14d rolling) ---
# This is expensive, so we use a sampling approach:
# For each day, compute rolling 14d returns correlation using top-50 most liquid tokens
print("   Computing rolling 14d cross-sectional correlation (sampled)...")

# Select top 50 tokens by available data coverage
token_coverage = daily_returns.notna().sum()
top_tokens = token_coverage.nlargest(50).index.tolist()
returns_top = daily_returns[top_tokens]

# Rolling 14-day correlation: compute mean pairwise correlation
window = 14
corr_series = []
dates = returns_top.index

for i in range(window, len(dates)):
    window_rets = returns_top.iloc[i - window:i]
    # Require at least 10 observations
    valid_cols = window_rets.dropna(axis=1, thresh=10).columns
    if len(valid_cols) < 10:
        corr_series.append(np.nan)
        continue
    corr_mat = window_rets[valid_cols].corr()
    # Mean of upper triangle (excluding diagonal)
    n = len(valid_cols)
    upper_tri = corr_mat.values[np.triu_indices(n, k=1)]
    corr_series.append(np.nanmean(upper_tri))

corr_index = dates[window:]
signals["mean_corr_14d"] = pd.Series(corr_series, index=corr_index)

# --- 2f. Dispersion / Correlation ratio (high disp + low corr = idiosyncratic moves) ---
signals["disp_corr_ratio"] = signals["dispersion_zscore"] / (signals["mean_corr_14d"] + 0.01)

# --- 2g. Lagged correlation change (correlation regime change signal) ---
signals["corr_change_7d"] = signals["mean_corr_14d"].diff(7)

print(f"   Signals computed. Date range: {signals.index.min().date()} to {signals.index.max().date()}")
print(f"   Signal columns: {list(signals.columns)}")

# ============================================================
# 3. TARGET CONSTRUCTION
# ============================================================
print("\n[3] Computing forward return targets...")

targets = pd.DataFrame(index=daily_returns.index)

# --- 3a. Equal-weight basket forward returns ---
ew_daily_return = daily_returns.mean(axis=1)
targets["fwd_1d"] = ew_daily_return.shift(-1)
targets["fwd_7d"] = ew_daily_return.rolling(7).sum().shift(-7)
targets["fwd_14d"] = ew_daily_return.rolling(14).sum().shift(-14)

# --- 3b. Momentum quintile returns ---
# For each day, rank tokens by trailing 7d return, then compute forward 7d return
# of top quintile and bottom quintile
print("   Computing momentum quintile returns...")

trailing_7d = daily_returns.rolling(7).sum()

fwd_1d_returns = daily_returns.shift(-1)
fwd_7d_returns = daily_returns.rolling(7).sum().shift(-7)

top_q_1d = []
bot_q_1d = []
top_q_7d = []
bot_q_7d = []
mom_spread_1d = []
mom_spread_7d = []

for dt in daily_returns.index:
    trail = trailing_7d.loc[dt].dropna()
    if len(trail) < 20:
        top_q_1d.append(np.nan)
        bot_q_1d.append(np.nan)
        top_q_7d.append(np.nan)
        bot_q_7d.append(np.nan)
        mom_spread_1d.append(np.nan)
        mom_spread_7d.append(np.nan)
        continue

    q80 = trail.quantile(0.8)
    q20 = trail.quantile(0.2)
    top_tokens_day = trail[trail >= q80].index
    bot_tokens_day = trail[trail <= q20].index

    if dt in fwd_1d_returns.index:
        top_1d = fwd_1d_returns.loc[dt, top_tokens_day].mean()
        bot_1d = fwd_1d_returns.loc[dt, bot_tokens_day].mean()
    else:
        top_1d = np.nan
        bot_1d = np.nan

    if dt in fwd_7d_returns.index:
        top_7d = fwd_7d_returns.loc[dt, top_tokens_day].mean()
        bot_7d = fwd_7d_returns.loc[dt, bot_tokens_day].mean()
    else:
        top_7d = np.nan
        bot_7d = np.nan

    top_q_1d.append(top_1d)
    bot_q_1d.append(bot_1d)
    top_q_7d.append(top_7d)
    bot_q_7d.append(bot_7d)
    mom_spread_1d.append(top_1d - bot_1d if not (np.isnan(top_1d) or np.isnan(bot_1d)) else np.nan)
    mom_spread_7d.append(top_7d - bot_7d if not (np.isnan(top_7d) or np.isnan(bot_7d)) else np.nan)

targets["top_q_fwd_1d"] = pd.Series(top_q_1d, index=daily_returns.index)
targets["bot_q_fwd_1d"] = pd.Series(bot_q_1d, index=daily_returns.index)
targets["top_q_fwd_7d"] = pd.Series(top_q_7d, index=daily_returns.index)
targets["bot_q_fwd_7d"] = pd.Series(bot_q_7d, index=daily_returns.index)
targets["mom_spread_1d"] = pd.Series(mom_spread_1d, index=daily_returns.index)
targets["mom_spread_7d"] = pd.Series(mom_spread_7d, index=daily_returns.index)

print(f"   Targets computed: {list(targets.columns)}")

# ============================================================
# 4. MERGE & SPLIT
# ============================================================
print("\n[4] Merging signals and targets, applying temporal split...")

combined = signals.join(targets, how="inner")
combined = combined.dropna(subset=["dispersion", "fwd_1d"])

train = combined.loc[combined.index < TRAIN_CUTOFF]
test = combined.loc[combined.index >= TRAIN_CUTOFF]

print(f"   Combined: {len(combined)} days")
print(f"   Train: {len(train)} days ({train.index.min().date()} to {train.index.max().date()})")
print(f"   Test:  {len(test)} days ({test.index.min().date()} to {test.index.max().date()})")

# ============================================================
# 5. INFORMATION COEFFICIENT ANALYSIS
# ============================================================
print("\n" + "=" * 80)
print("INFORMATION COEFFICIENT (IC) ANALYSIS")
print("=" * 80)

signal_cols = ["dispersion", "dispersion_zscore", "mean_corr_14d",
               "herfindahl", "disp_corr_ratio", "corr_change_7d", "mean_abs_return"]
target_cols = ["fwd_1d", "fwd_7d", "fwd_14d",
               "top_q_fwd_1d", "top_q_fwd_7d",
               "mom_spread_1d", "mom_spread_7d"]


def compute_ic_stats(data, signal_col, target_col):
    """Compute IC (rank correlation), t-stat, p-value, sign consistency."""
    valid = data[[signal_col, target_col]].dropna()
    if len(valid) < 30:
        return {"IC": np.nan, "t_stat": np.nan, "p_value": np.nan,
                "sign_consistency": np.nan, "n": len(valid)}

    ic, p = sp_stats.spearmanr(valid[signal_col], valid[target_col])

    # t-stat for IC significance
    n = len(valid)
    t_stat = ic * np.sqrt((n - 2) / (1 - ic ** 2 + 1e-10))

    # Sign consistency: fraction of days where sign(signal) == sign(target)
    s = np.sign(valid[signal_col])
    t = np.sign(valid[target_col])
    sign_cons = (s == t).mean()

    return {"IC": ic, "t_stat": t_stat, "p_value": p,
            "sign_consistency": sign_cons, "n": n}


for split_name, data in [("TRAIN", train), ("TEST", test)]:
    print(f"\n--- {split_name} SET (n={len(data)}) ---")
    print(f"{'Signal':<22} {'Target':<18} {'IC':>8} {'t-stat':>8} {'p-value':>10} {'SignCons':>9} {'N':>6}")
    print("-" * 85)

    for sig in signal_cols:
        for tgt in target_cols:
            result = compute_ic_stats(data, sig, tgt)
            ic = result["IC"]
            ts = result["t_stat"]
            pv = result["p_value"]
            sc = result["sign_consistency"]
            n = result["n"]
            # Highlight significant results
            marker = ""
            if not np.isnan(pv):
                if pv < 0.01:
                    marker = " ***"
                elif pv < 0.05:
                    marker = " **"
                elif pv < 0.10:
                    marker = " *"
            print(f"{sig:<22} {tgt:<18} {ic:>8.4f} {ts:>8.2f} {pv:>10.4f} {sc:>8.1%} {n:>6}{marker}")
        print()

# ============================================================
# 6. QUINTILE ANALYSIS: DISPERSION SIGNAL
# ============================================================
print("\n" + "=" * 80)
print("QUINTILE ANALYSIS: DISPERSION Z-SCORE -> TARGETS")
print("=" * 80)


def quintile_analysis(data, signal_col, target_col, n_quantiles=5):
    """Split signal into quintiles, compute mean target in each."""
    valid = data[[signal_col, target_col]].dropna()
    if len(valid) < 50:
        return None
    valid["quintile"] = pd.qcut(valid[signal_col], n_quantiles, labels=False, duplicates="drop")
    result = valid.groupby("quintile")[target_col].agg(["mean", "std", "count"])
    result["sharpe"] = result["mean"] / result["std"] * np.sqrt(252)
    return result


for split_name, data in [("TRAIN", train), ("TEST", test)]:
    print(f"\n=== {split_name} SET ===")
    for sig in ["dispersion_zscore", "mean_corr_14d", "herfindahl"]:
        for tgt in ["fwd_7d", "mom_spread_7d", "top_q_fwd_7d"]:
            qa = quintile_analysis(data, sig, tgt)
            if qa is not None:
                print(f"\n  Signal: {sig} -> Target: {tgt}")
                print(f"  {'Quintile':<10} {'Mean':>10} {'Std':>10} {'Sharpe':>10} {'Count':>8}")
                print(f"  {'-'*50}")
                for q_idx, row in qa.iterrows():
                    print(f"  Q{q_idx:<9} {row['mean']:>10.5f} {row['std']:>10.5f} {row['sharpe']:>10.2f} {row['count']:>8.0f}")
                # Spread Q4-Q0
                if 0 in qa.index and (n_quantiles := len(qa)) > 1:
                    top_q = qa.index.max()
                    spread = qa.loc[top_q, "mean"] - qa.loc[0, "mean"]
                    print(f"  Q{top_q}-Q0 spread: {spread:>10.5f}")

# ============================================================
# 7. CONDITIONAL MOMENTUM ANALYSIS
# ============================================================
print("\n" + "=" * 80)
print("CONDITIONAL MOMENTUM ANALYSIS")
print("Does high dispersion predict stronger momentum profits?")
print("=" * 80)


def conditional_momentum(data, condition_col, threshold_type="median"):
    """Split into high/low condition regimes, compare momentum spread."""
    valid = data[[condition_col, "mom_spread_7d", "top_q_fwd_7d", "bot_q_fwd_7d"]].dropna()
    if len(valid) < 50:
        return None

    if threshold_type == "median":
        threshold = valid[condition_col].median()
    elif threshold_type == "tercile":
        threshold_lo = valid[condition_col].quantile(0.33)
        threshold_hi = valid[condition_col].quantile(0.67)

    if threshold_type == "median":
        high = valid[valid[condition_col] >= threshold]
        low = valid[valid[condition_col] < threshold]
    else:
        high = valid[valid[condition_col] >= threshold_hi]
        low = valid[valid[condition_col] <= threshold_lo]

    results = {}
    for regime_name, regime_data in [("HIGH", high), ("LOW", low)]:
        results[regime_name] = {
            "mom_spread_mean": regime_data["mom_spread_7d"].mean(),
            "mom_spread_std": regime_data["mom_spread_7d"].std(),
            "mom_spread_sharpe": regime_data["mom_spread_7d"].mean() / regime_data["mom_spread_7d"].std() * np.sqrt(52) if regime_data["mom_spread_7d"].std() > 0 else 0,
            "top_q_mean": regime_data["top_q_fwd_7d"].mean(),
            "bot_q_mean": regime_data["bot_q_fwd_7d"].mean(),
            "n_days": len(regime_data),
            "hit_rate": (regime_data["mom_spread_7d"] > 0).mean()
        }

    # Two-sample t-test for momentum spread difference
    t_stat, p_val = sp_stats.ttest_ind(
        high["mom_spread_7d"].dropna(),
        low["mom_spread_7d"].dropna(),
        equal_var=False
    )
    results["diff_t_stat"] = t_stat
    results["diff_p_val"] = p_val

    return results


for split_name, data in [("TRAIN", train), ("TEST", test)]:
    print(f"\n=== {split_name} SET ===")
    for cond in ["dispersion_zscore", "mean_corr_14d", "herfindahl"]:
        result = conditional_momentum(data, cond)
        if result is None:
            print(f"\n  {cond}: insufficient data")
            continue
        print(f"\n  Conditioning on: {cond}")
        print(f"  {'Regime':<8} {'MomSpread':>12} {'MomStd':>10} {'MomSharpe':>11} {'TopQ':>10} {'BotQ':>10} {'HitRate':>9} {'N':>6}")
        print(f"  {'-'*70}")
        for regime in ["HIGH", "LOW"]:
            r = result[regime]
            print(f"  {regime:<8} {r['mom_spread_mean']:>12.5f} {r['mom_spread_std']:>10.5f} "
                  f"{r['mom_spread_sharpe']:>11.2f} {r['top_q_mean']:>10.5f} {r['bot_q_mean']:>10.5f} "
                  f"{r['hit_rate']:>8.1%} {r['n_days']:>6}")
        print(f"  Diff t-stat: {result['diff_t_stat']:.3f}, p-value: {result['diff_p_val']:.4f}"
              + (" ***" if result['diff_p_val'] < 0.01 else " **" if result['diff_p_val'] < 0.05 else " *" if result['diff_p_val'] < 0.10 else ""))

# ============================================================
# 8. POSITION SIZING SIGNAL
# ============================================================
print("\n" + "=" * 80)
print("POSITION SIZING ANALYSIS")
print("Can dispersion z-score be used to scale position sizes?")
print("=" * 80)


def position_sizing_backtest(data, signal_col="dispersion_zscore", target_col="mom_spread_7d"):
    """
    Compare:
    1. Static: always full size momentum
    2. Scaled: size proportional to dispersion z-score (capped at 0-2x)
    """
    valid = data[[signal_col, target_col]].dropna()
    if len(valid) < 50:
        return None

    # Static: equal weight every day
    static_returns = valid[target_col]

    # Scaled: clip z-score to [0, 2], normalize to mean ~1
    raw_scale = valid[signal_col].clip(lower=-1, upper=2)
    # Shift so minimum scale is 0.25 and maximum is 2
    scale = (raw_scale + 1) / 2  # maps [-1,2] to [0, 1.5]
    scale = scale.clip(lower=0.1, upper=2.0)
    # Normalize so mean scale = 1
    scale = scale / scale.mean()
    scaled_returns = valid[target_col] * scale

    results = {
        "static_mean": static_returns.mean(),
        "static_std": static_returns.std(),
        "static_sharpe": static_returns.mean() / static_returns.std() * np.sqrt(52),
        "scaled_mean": scaled_returns.mean(),
        "scaled_std": scaled_returns.std(),
        "scaled_sharpe": scaled_returns.mean() / scaled_returns.std() * np.sqrt(52),
        "correlation_signal_target": valid[signal_col].corr(valid[target_col]),
        "n": len(valid)
    }
    return results


for split_name, data in [("TRAIN", train), ("TEST", test)]:
    print(f"\n=== {split_name} SET ===")
    for tgt in ["mom_spread_7d", "fwd_7d", "top_q_fwd_7d"]:
        result = position_sizing_backtest(data, target_col=tgt)
        if result is None:
            print(f"\n  Target {tgt}: insufficient data")
            continue
        print(f"\n  Target: {tgt} (n={result['n']})")
        print(f"  {'Strategy':<12} {'Mean':>10} {'Std':>10} {'Sharpe':>10}")
        print(f"  {'-'*44}")
        print(f"  {'Static':<12} {result['static_mean']:>10.5f} {result['static_std']:>10.5f} {result['static_sharpe']:>10.3f}")
        print(f"  {'Disp-Scaled':<12} {result['scaled_mean']:>10.5f} {result['scaled_std']:>10.5f} {result['scaled_sharpe']:>10.3f}")
        improvement = (result['scaled_sharpe'] - result['static_sharpe']) / abs(result['static_sharpe'] + 1e-10) * 100
        print(f"  Sharpe improvement: {improvement:+.1f}%")

# ============================================================
# 9. REGIME ANALYSIS: CORRELATION REGIMES
# ============================================================
print("\n" + "=" * 80)
print("CORRELATION REGIME ANALYSIS")
print("Does low correlation predict regime change?")
print("=" * 80)


def correlation_regime_analysis(data):
    """Analyze what happens after correlation drops."""
    valid = data[["mean_corr_14d", "corr_change_7d", "fwd_7d", "fwd_14d",
                   "dispersion", "mom_spread_7d"]].dropna()
    if len(valid) < 50:
        return None

    # Define regimes based on correlation level
    q33 = valid["mean_corr_14d"].quantile(0.33)
    q67 = valid["mean_corr_14d"].quantile(0.67)

    low_corr = valid[valid["mean_corr_14d"] <= q33]
    mid_corr = valid[(valid["mean_corr_14d"] > q33) & (valid["mean_corr_14d"] <= q67)]
    high_corr = valid[valid["mean_corr_14d"] > q67]

    results = {}
    for name, regime in [("LOW_CORR", low_corr), ("MID_CORR", mid_corr), ("HIGH_CORR", high_corr)]:
        results[name] = {
            "fwd_7d_mean": regime["fwd_7d"].mean(),
            "fwd_14d_mean": regime["fwd_14d"].mean(),
            "dispersion_mean": regime["dispersion"].mean(),
            "mom_spread_mean": regime["mom_spread_7d"].mean(),
            "mom_spread_hit": (regime["mom_spread_7d"] > 0).mean(),
            "n": len(regime)
        }

    # Also: correlation dropping (7d change < -0.1) as a signal
    corr_dropping = valid[valid["corr_change_7d"] < -0.10]
    corr_rising = valid[valid["corr_change_7d"] > 0.10]
    corr_stable = valid[valid["corr_change_7d"].abs() <= 0.10]

    results["CORR_DROPPING"] = {
        "fwd_7d_mean": corr_dropping["fwd_7d"].mean() if len(corr_dropping) > 5 else np.nan,
        "fwd_14d_mean": corr_dropping["fwd_14d"].mean() if len(corr_dropping) > 5 else np.nan,
        "mom_spread_mean": corr_dropping["mom_spread_7d"].mean() if len(corr_dropping) > 5 else np.nan,
        "n": len(corr_dropping)
    }
    results["CORR_RISING"] = {
        "fwd_7d_mean": corr_rising["fwd_7d"].mean() if len(corr_rising) > 5 else np.nan,
        "fwd_14d_mean": corr_rising["fwd_14d"].mean() if len(corr_rising) > 5 else np.nan,
        "mom_spread_mean": corr_rising["mom_spread_7d"].mean() if len(corr_rising) > 5 else np.nan,
        "n": len(corr_rising)
    }
    results["CORR_STABLE"] = {
        "fwd_7d_mean": corr_stable["fwd_7d"].mean() if len(corr_stable) > 5 else np.nan,
        "fwd_14d_mean": corr_stable["fwd_14d"].mean() if len(corr_stable) > 5 else np.nan,
        "mom_spread_mean": corr_stable["mom_spread_7d"].mean() if len(corr_stable) > 5 else np.nan,
        "n": len(corr_stable)
    }

    return results


for split_name, data in [("TRAIN", train), ("TEST", test)]:
    print(f"\n=== {split_name} SET ===")
    result = correlation_regime_analysis(data)
    if result is None:
        print("  Insufficient data")
        continue

    print(f"\n  By Correlation Level:")
    print(f"  {'Regime':<14} {'Fwd7d':>10} {'Fwd14d':>10} {'Dispersion':>12} {'MomSpread':>12} {'MomHit':>9} {'N':>6}")
    print(f"  {'-'*72}")
    for regime in ["LOW_CORR", "MID_CORR", "HIGH_CORR"]:
        r = result[regime]
        print(f"  {regime:<14} {r['fwd_7d_mean']:>10.5f} {r['fwd_14d_mean']:>10.5f} "
              f"{r['dispersion_mean']:>12.5f} {r['mom_spread_mean']:>12.5f} "
              f"{r['mom_spread_hit']:>8.1%} {r['n']:>6}")

    print(f"\n  By Correlation Change (7d delta):")
    print(f"  {'Regime':<16} {'Fwd7d':>10} {'Fwd14d':>10} {'MomSpread':>12} {'N':>6}")
    print(f"  {'-'*58}")
    for regime in ["CORR_DROPPING", "CORR_STABLE", "CORR_RISING"]:
        r = result[regime]
        fwd7 = f"{r['fwd_7d_mean']:>10.5f}" if not np.isnan(r.get('fwd_7d_mean', np.nan)) else f"{'N/A':>10}"
        fwd14 = f"{r['fwd_14d_mean']:>10.5f}" if not np.isnan(r.get('fwd_14d_mean', np.nan)) else f"{'N/A':>10}"
        mom = f"{r['mom_spread_mean']:>12.5f}" if not np.isnan(r.get('mom_spread_mean', np.nan)) else f"{'N/A':>12}"
        print(f"  {regime:<16} {fwd7} {fwd14} {mom} {r['n']:>6}")

# ============================================================
# 10. ROLLING IC STABILITY
# ============================================================
print("\n" + "=" * 80)
print("ROLLING IC STABILITY (60-day rolling windows)")
print("=" * 80)

key_pairs = [
    ("dispersion_zscore", "mom_spread_7d"),
    ("dispersion_zscore", "fwd_7d"),
    ("mean_corr_14d", "mom_spread_7d"),
    ("herfindahl", "mom_spread_7d"),
]

for sig, tgt in key_pairs:
    valid = combined[[sig, tgt]].dropna()
    if len(valid) < 120:
        print(f"\n  {sig} -> {tgt}: insufficient data for rolling IC")
        continue

    rolling_ic = []
    dates_ic = []
    for i in range(60, len(valid)):
        window_data = valid.iloc[i - 60:i]
        ic, _ = sp_stats.spearmanr(window_data[sig], window_data[tgt])
        rolling_ic.append(ic)
        dates_ic.append(valid.index[i])

    rolling_ic = pd.Series(rolling_ic, index=dates_ic)

    # Stats
    mean_ic = rolling_ic.mean()
    std_ic = rolling_ic.std()
    pct_positive = (rolling_ic > 0).mean()
    ic_ir = mean_ic / std_ic if std_ic > 0 else 0

    # Split by train/test
    train_ic = rolling_ic[rolling_ic.index < TRAIN_CUTOFF]
    test_ic = rolling_ic[rolling_ic.index >= TRAIN_CUTOFF]

    print(f"\n  {sig} -> {tgt}:")
    print(f"    Full:  mean IC = {mean_ic:.4f}, std = {std_ic:.4f}, ICIR = {ic_ir:.3f}, "
          f"pct_positive = {pct_positive:.1%}")
    if len(train_ic) > 0:
        print(f"    Train: mean IC = {train_ic.mean():.4f}, std = {train_ic.std():.4f}, "
              f"pct_positive = {(train_ic > 0).mean():.1%}")
    if len(test_ic) > 0:
        print(f"    Test:  mean IC = {test_ic.mean():.4f}, std = {test_ic.std():.4f}, "
              f"pct_positive = {(test_ic > 0).mean():.1%}")

# ============================================================
# 11. AUTOCORRELATION OF SIGNALS (persistence)
# ============================================================
print("\n" + "=" * 80)
print("SIGNAL PERSISTENCE (Autocorrelation)")
print("=" * 80)

for sig in signal_cols:
    valid = combined[sig].dropna()
    if len(valid) < 30:
        continue
    ac1 = valid.autocorr(lag=1)
    ac7 = valid.autocorr(lag=7)
    ac14 = valid.autocorr(lag=14)
    print(f"  {sig:<22}  AC(1)={ac1:.3f}  AC(7)={ac7:.3f}  AC(14)={ac14:.3f}")

# ============================================================
# 12. SUMMARY & CONCLUSIONS
# ============================================================
print("\n" + "=" * 80)
print("SUMMARY STATISTICS")
print("=" * 80)

print("\n  Signal Descriptive Stats (Full Sample):")
print(combined[signal_cols].describe().round(4).to_string())

print("\n\n  Target Descriptive Stats (Full Sample):")
print(combined[target_cols].describe().round(4).to_string())

# Best signal-target pairs by absolute IC in test set
print("\n" + "=" * 80)
print("TOP SIGNAL-TARGET PAIRS BY |IC| (TEST SET)")
print("=" * 80)

results_list = []
for sig in signal_cols:
    for tgt in target_cols:
        result = compute_ic_stats(test, sig, tgt)
        result["signal"] = sig
        result["target"] = tgt
        results_list.append(result)

results_df = pd.DataFrame(results_list)
results_df["abs_IC"] = results_df["IC"].abs()
results_df = results_df.sort_values("abs_IC", ascending=False)

print(f"\n{'Rank':<6} {'Signal':<22} {'Target':<18} {'IC':>8} {'t-stat':>8} {'p-value':>10} {'SignCons':>9}")
print("-" * 85)
for i, (_, row) in enumerate(results_df.head(15).iterrows()):
    marker = ""
    if not np.isnan(row["p_value"]):
        if row["p_value"] < 0.01:
            marker = " ***"
        elif row["p_value"] < 0.05:
            marker = " **"
        elif row["p_value"] < 0.10:
            marker = " *"
    print(f"{i+1:<6} {row['signal']:<22} {row['target']:<18} {row['IC']:>8.4f} "
          f"{row['t_stat']:>8.2f} {row['p_value']:>10.4f} {row['sign_consistency']:>8.1%}{marker}")

# ============================================================
# 13. TRAIN vs TEST IC CONSISTENCY CHECK
# ============================================================
print("\n" + "=" * 80)
print("TRAIN vs TEST IC CONSISTENCY")
print("=" * 80)

print(f"\n{'Signal':<22} {'Target':<18} {'Train IC':>10} {'Test IC':>10} {'Same Sign':>10} {'Decay':>10}")
print("-" * 85)

for sig in signal_cols:
    for tgt in target_cols:
        train_result = compute_ic_stats(train, sig, tgt)
        test_result = compute_ic_stats(test, sig, tgt)
        train_ic = train_result["IC"]
        test_ic = test_result["IC"]
        same_sign = "YES" if (train_ic * test_ic > 0) else "NO"
        if abs(train_ic) > 0.001:
            decay = (test_ic - train_ic) / abs(train_ic) * 100
            decay_str = f"{decay:+.0f}%"
        else:
            decay_str = "N/A"
        # Only print if at least one is significant
        if (train_result["p_value"] < 0.1 or test_result["p_value"] < 0.1):
            print(f"{sig:<22} {tgt:<18} {train_ic:>10.4f} {test_ic:>10.4f} {same_sign:>10} {decay_str:>10}")

print("\n" + "=" * 80)
print("ANALYSIS COMPLETE")
print("=" * 80)
