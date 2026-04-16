"""
Cross-Sectional Momentum Information Coefficient (IC) Test
==========================================================
Hypothesis: rank tokens by trailing N-hour return, go long the top K
and short the bottom K, hold for M hours.

Test: Spearman rank IC between trailing return rank and forward return
rank at each bar across the cross-section.

Point-in-time guarantee:
  - Trailing returns use CLOSED bars only (shift by 1 to avoid look-ahead)
  - Forward returns use FUTURE bars only
"""

import os
import time
import numpy as np
import pandas as pd
from scipy import stats

# ── Config ──────────────────────────────────────────────────────────────
PERP_DIR = "/workspace/crypto_backtest/data/perp/1h_cache/"
LOOKBACKS = [4, 8, 12, 24, 48]
HORIZONS = [4, 8, 12, 24]
TOP_N = 20          # select top N tokens by median USD volume
MIN_TOKENS = 10     # minimum tokens with data at a bar to compute IC
MIN_BARS = 2000     # minimum bars to include a token (filters out new listings)


def load_top_tokens(n: int) -> dict[str, pd.DataFrame]:
    """Load all perp 1h parquet files, return top N by median USD volume."""
    files = sorted(f for f in os.listdir(PERP_DIR) if f.endswith(".parquet"))

    all_data = {}
    vol_scores = {}

    for fname in files:
        ticker = fname.replace("_1h.parquet", "")
        path = os.path.join(PERP_DIR, fname)
        try:
            df = pd.read_parquet(path, columns=["close", "volume"])
        except Exception:
            continue

        # Index should already be datetime
        if not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.iloc[:, 0])
            df = df.iloc[:, 1:]

        if len(df) < MIN_BARS:
            continue

        all_data[ticker] = df
        # USD volume = close price * token volume
        vol_scores[ticker] = (df["close"] * df["volume"]).median()

    # Pick top N by median USD volume
    ranked = sorted(vol_scores.items(), key=lambda x: x[1], reverse=True)
    top_tickers = [t for t, _ in ranked[:n]]

    print(f"Loaded {len(all_data)} tokens (>={MIN_BARS} bars), "
          f"selected top {n} by median USD volume:")
    for t, v in ranked[:n]:
        bars = len(all_data[t])
        date_range = f"{all_data[t].index[0].date()} to {all_data[t].index[-1].date()}"
        print(f"  {t:>10s}  USD_vol=${v:>14,.0f}  bars={bars:>6,}  {date_range}")

    return {t: all_data[t] for t in top_tickers}


def build_close_panel(token_data: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Build a wide DataFrame of close prices, outer-joined on datetime index."""
    closes = {}
    for ticker, df in token_data.items():
        closes[ticker] = df["close"]

    panel = pd.DataFrame(closes)
    panel.sort_index(inplace=True)

    # Drop rows where fewer than MIN_TOKENS have data
    valid_count = panel.notna().sum(axis=1)
    panel = panel[valid_count >= MIN_TOKENS]

    print(f"\nClose panel: {panel.shape[0]:,} bars x {panel.shape[1]} tokens")
    print(f"Date range: {panel.index[0]} to {panel.index[-1]}")
    return panel


def spearman_rank_ic_vectorized(trailing: pd.DataFrame, forward: pd.DataFrame,
                                 min_tokens: int) -> np.ndarray:
    """
    Compute Spearman rank IC at each bar between trailing and forward returns.
    Vectorized: rank across columns (tokens) at each row, then correlate.
    """
    # Rank across tokens at each bar (axis=1)
    trail_rank = trailing.rank(axis=1, method="average")
    fwd_rank = forward.rank(axis=1, method="average")

    # Count valid tokens at each bar (both must be non-NaN)
    both_valid = trailing.notna() & forward.notna()
    n_valid = both_valid.sum(axis=1)

    # Mask out bars with too few tokens
    valid_bars = n_valid >= min_tokens

    # For valid bars, compute Pearson correlation of ranks (= Spearman)
    # Center the ranks, then correlate
    trail_r = trail_rank.where(both_valid)
    fwd_r = fwd_rank.where(both_valid)

    # Row-wise mean of ranks (only where both are valid)
    trail_mean = trail_r.sum(axis=1) / n_valid
    fwd_mean = fwd_r.sum(axis=1) / n_valid

    # Deviations
    trail_dev = trail_r.sub(trail_mean, axis=0)
    fwd_dev = fwd_r.sub(fwd_mean, axis=0)

    # Covariance and variances (row-wise)
    cov = (trail_dev * fwd_dev).sum(axis=1)
    var_t = (trail_dev ** 2).sum(axis=1)
    var_f = (fwd_dev ** 2).sum(axis=1)

    denom = np.sqrt(var_t * var_f)
    ic = cov / denom.replace(0, np.nan)

    return ic[valid_bars].dropna().values


def compute_ic_matrix(closes: pd.DataFrame) -> pd.DataFrame:
    """
    For each (lookback, horizon), compute time-series of Spearman rank IC
    between trailing return rank and forward return rank, then report
    mean IC, t-stat, and hit rate.
    """
    results = []

    for L in LOOKBACKS:
        # Trailing L-hour log return, shifted by 1 bar to be point-in-time
        # At bar t, we use close[t-1] / close[t-1-L] -- only CLOSED bars
        trailing_ret = np.log(closes.shift(1) / closes.shift(1 + L))

        for H in HORIZONS:
            t0 = time.time()

            # Forward H-hour log return: close[t+H] / close[t]
            forward_ret = np.log(closes.shift(-H) / closes)

            # Vectorized Spearman IC
            ics = spearman_rank_ic_vectorized(trailing_ret, forward_ret, MIN_TOKENS)

            n = len(ics)
            elapsed = time.time() - t0

            if n < 30:
                results.append({
                    "lookback_h": L, "horizon_h": H,
                    "mean_IC": np.nan, "std_IC": np.nan,
                    "t_stat": np.nan, "hit_rate": np.nan, "n_obs": n
                })
                continue

            mean_ic = ics.mean()
            std_ic = ics.std(ddof=1)
            t_stat = mean_ic / std_ic * np.sqrt(n)
            hit_rate = (ics > 0).mean()

            results.append({
                "lookback_h": L, "horizon_h": H,
                "mean_IC": mean_ic, "std_IC": std_ic,
                "t_stat": t_stat, "hit_rate": hit_rate, "n_obs": n
            })

            print(f"  L={L:>2}h H={H:>2}h  IC={mean_ic:+.4f}  "
                  f"t={t_stat:+.2f}  N={n:,}  ({elapsed:.1f}s)")

    return pd.DataFrame(results)


def main():
    print("=" * 72)
    print("CROSS-SECTIONAL MOMENTUM IC TEST")
    print("  Ranking: USD-volume-weighted top 20 perp tokens")
    print("  Point-in-time: trailing returns shifted by 1 bar")
    print("=" * 72)

    # 1. Load data
    token_data = load_top_tokens(TOP_N)

    # 2. Build close panel
    closes = build_close_panel(token_data)

    # 3. Compute IC matrix
    print("\nComputing Spearman rank IC for each (lookback, horizon)...\n")
    ic_df = compute_ic_matrix(closes)

    # 4. Report
    print("\n" + "=" * 72)
    print("IC RESULTS -- Cross-Sectional Momentum")
    print("  Universe: Top 20 perp tokens by median USD volume")
    print("  Minimum tokens per bar: %d" % MIN_TOKENS)
    print("=" * 72)

    # Pivot tables for display
    for metric in ["mean_IC", "t_stat", "hit_rate"]:
        pivot = ic_df.pivot(index="lookback_h", columns="horizon_h", values=metric)
        label = {
            "mean_IC": "Mean Spearman IC",
            "t_stat": "t-statistic (IC/std * sqrt(N))",
            "hit_rate": "Hit Rate (% bars with IC > 0)",
        }[metric]
        print(f"\n{label}:")
        print(pivot.to_string(float_format=lambda x: f"{x:.4f}"))

    # n_obs table
    pivot_n = ic_df.pivot(index="lookback_h", columns="horizon_h", values="n_obs")
    print(f"\nObservation count:")
    print(pivot_n.to_string(float_format=lambda x: f"{x:.0f}"))

    # 5. Gate check
    print("\n" + "=" * 72)
    print("GATE CHECK: |IC| > 0.03 AND |t| > 2.0")
    print("=" * 72)
    passing = ic_df[(ic_df["mean_IC"].abs() > 0.03) & (ic_df["t_stat"].abs() > 2.0)]
    if len(passing) > 0:
        print(f"\n*** {len(passing)} combination(s) PASS the gate: ***\n")
        for _, row in passing.iterrows():
            if row["mean_IC"] > 0:
                sign = "MOMENTUM (winners keep winning)"
            else:
                sign = "REVERSAL (winners mean-revert)"
            print(f"  L={int(row['lookback_h']):>2}h, H={int(row['horizon_h']):>2}h  "
                  f"IC={row['mean_IC']:+.4f}  t={row['t_stat']:+.2f}  "
                  f"hit={row['hit_rate']:.1%}  N={int(row['n_obs']):,}  [{sign}]")

        # Best combo
        best = passing.loc[passing["t_stat"].abs().idxmax()]
        print(f"\n  Best combo: L={int(best['lookback_h'])}h, H={int(best['horizon_h'])}h "
              f"(IC={best['mean_IC']:+.4f}, t={best['t_stat']:+.2f})")
        print("\nVerdict: ADVANCE to Gate 1 for deeper analysis.")
    else:
        print("\nNo (L, H) combination passes the gate.")
        print("Verdict: Cross-sectional momentum has NO short-horizon "
              "predictive power in this universe.")
        print("Consider: mean-reversion, factor-adjusted momentum, "
              "or longer horizons.")

    # 6. Full raw table
    print("\n" + "=" * 72)
    print("FULL RAW TABLE")
    print("=" * 72)
    print(ic_df.to_string(index=False, float_format=lambda x: f"{x:.4f}"))


if __name__ == "__main__":
    main()
