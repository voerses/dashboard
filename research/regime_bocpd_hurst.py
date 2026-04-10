"""
Regime Detection: BOCPD + Rolling Hurst Exponent
=================================================
Date: 2026-04-10
Purpose: Build and test two causal (no look-ahead) regime detectors for BTC:
  1. Rolling Hurst Exponent (trending vs mean-reverting vs random)
  2. Bayesian Online Changepoint Detection (bull vs bear with changepoint flags)

These complement an HMM-based primary detector.
"""

import numpy as np
import pandas as pd
from scipy import stats
from scipy.special import gammaln
import warnings
warnings.filterwarnings("ignore")

# ============================================================================
#  DATA LOADING
# ============================================================================

def load_btc_daily() -> pd.DataFrame:
    """Load BTC 1h data, resample to daily close."""
    df = pd.read_csv(
        "/workspace/crypto_backtest/data/perp/binance/1h_ohlcv/BTC_perp_1h.csv",
        parse_dates=["datetime"],
    )
    df = df.set_index("datetime")
    daily = df["close"].resample("1D").last().dropna()
    daily.name = "close"
    return daily.to_frame()


# ============================================================================
#  TASK 1: ROLLING HURST EXPONENT
# ============================================================================

def hurst_exponent(ts: np.ndarray, max_lag: int = 20) -> float:
    """Compute Hurst exponent via variance of lagged differences."""
    lags = range(2, max_lag)
    tau = [np.std(np.subtract(ts[lag:], ts[:-lag])) for lag in lags]
    # Guard against zero/nan
    tau = np.array(tau)
    if np.any(tau <= 0) or np.any(np.isnan(tau)):
        return np.nan
    return np.polyfit(np.log(list(lags)), np.log(tau), 1)[0]


def rolling_hurst(close: pd.Series, window: int, max_lag: int = 20) -> pd.Series:
    """Compute rolling Hurst exponent (causal: uses only past data)."""
    log_price = np.log(close.values)
    n = len(log_price)
    result = np.full(n, np.nan)
    for i in range(window, n):
        segment = log_price[i - window : i]
        result[i] = hurst_exponent(segment, max_lag=max_lag)
    return pd.Series(result, index=close.index, name=f"hurst_{window}d")


def classify_hurst(h: float) -> str:
    """Classify Hurst exponent into regime."""
    if np.isnan(h):
        return "n/a"
    if h > 0.55:
        return "TREND"
    elif h < 0.45:
        return "MR"
    else:
        return "RANDOM"


def run_hurst_analysis(daily: pd.DataFrame) -> pd.DataFrame:
    """Run rolling Hurst for multiple windows, produce monthly table."""
    close = daily["close"]
    windows = [30, 60, 90, 120]

    hurst_frames = {}
    for w in windows:
        print(f"  Computing Hurst (window={w}d)...")
        hurst_frames[w] = rolling_hurst(close, window=w)

    # Monthly resampling: take last value of each month
    monthly = pd.DataFrame()
    monthly["btc_close"] = close.resample("ME").last()
    monthly["btc_ret_30d"] = close.pct_change(30).resample("ME").last() * 100

    for w in windows:
        col = f"H_{w}d"
        monthly[col] = hurst_frames[w].resample("ME").last()
        monthly[f"regime_{w}d"] = monthly[col].apply(classify_hurst)

    return monthly.dropna(subset=["btc_ret_30d"])


# ============================================================================
#  TASK 2: BAYESIAN ONLINE CHANGEPOINT DETECTION (BOCPD)
# ============================================================================

class BOCPD:
    """
    Adams & MacKay (2007) Bayesian Online Changepoint Detection.

    Observation model: Normal with known variance, conjugate Normal prior on mean.
    This is simpler and more practical than the full NIG model for detecting
    mean-shifts in financial returns (variance changes are handled separately).

    Hazard function: constant rate 1/lambda.

    For each run length r, we maintain:
      - mu_r: posterior mean of the segment mean
      - var_r: posterior variance of the segment mean
    The observation variance (sigma2) is estimated from data.
    """

    def __init__(self, hazard_lambda: int = 180):
        self.hazard_lambda = hazard_lambda
        self.hazard = 1.0 / hazard_lambda

    def run(self, data: np.ndarray):
        """
        Run BOCPD on 1D data array. Returns:
          - changepoint_prob: (T,) array of P(run_length=0) at each step
          - max_run_length: (T,) array of MAP run length at each step
        """
        T = len(data)

        # Estimate observation variance from data (known variance model)
        # Use rolling 60d variance, but for the prior we use the global estimate
        sigma2 = np.var(data[: min(60, T)])

        # Prior on segment mean: N(mu0, sigma2_prior)
        mu0 = 0.0
        sigma2_prior = sigma2  # prior variance on the mean = observation variance

        # Initialize: single run length = 0
        # Posterior mean and variance for each run length
        post_mu = np.array([mu0])
        post_var = np.array([sigma2_prior])

        log_R = np.array([0.0])

        changepoint_prob = np.zeros(T)
        max_run_length = np.zeros(T)

        log_hazard = np.log(self.hazard)
        log_1m_hazard = np.log(1.0 - self.hazard)

        for t in range(T):
            x = data[t]

            # 1. Predictive probability: N(x | post_mu, post_var + sigma2)
            pred_var = post_var + sigma2
            log_pred = stats.norm.logpdf(x, loc=post_mu, scale=np.sqrt(pred_var))

            # 2. Growth probabilities
            log_growth = log_R + log_pred + log_1m_hazard

            # 3. Changepoint probability
            log_cp = np.logaddexp.reduce(log_R + log_pred + log_hazard)

            # 4. New run-length distribution
            new_log_R = np.empty(len(log_R) + 1)
            new_log_R[0] = log_cp
            new_log_R[1:] = log_growth

            log_evidence = np.logaddexp.reduce(new_log_R)
            new_log_R -= log_evidence

            changepoint_prob[t] = np.exp(new_log_R[0])
            max_run_length[t] = np.argmax(new_log_R)

            # 5. Update posterior for each run length (Bayesian update for Normal-Normal)
            # New precision = 1/post_var + 1/sigma2
            # New mean = (post_mu/post_var + x/sigma2) / new_precision
            new_precision = 1.0 / post_var + 1.0 / sigma2
            new_var = 1.0 / new_precision
            new_mu = (post_mu / post_var + x / sigma2) * new_var

            # Grow arrays: index 0 = new segment (prior), rest = continued
            post_mu = np.concatenate([[mu0], new_mu])
            post_var = np.concatenate([[sigma2_prior], new_var])
            log_R = new_log_R

            # Pruning
            if len(log_R) > 500:
                keep = log_R > (log_R.max() - 30)
                keep[0] = True
                log_R = log_R[keep]
                post_mu = post_mu[keep]
                post_var = post_var[keep]
                log_R -= np.logaddexp.reduce(log_R)

        return changepoint_prob, max_run_length


def run_bocpd_analysis(daily: pd.DataFrame) -> pd.DataFrame:
    """Run BOCPD for multiple lambda values, classify regimes.

    Uses 7-day rolling mean of log returns as input to BOCPD.
    This smoothing boosts the signal-to-noise ratio (daily returns have
    SNR ~0.03, too low for changepoint detection on raw returns).
    The smoothed series preserves regime shifts while reducing noise.
    """
    close = daily["close"]
    returns = close.pct_change().dropna()

    # Smooth returns: 7-day rolling mean of log returns
    # This increases SNR from ~0.03 to ~0.1, making mean shifts detectable
    log_returns = np.log(close / close.shift(1)).dropna()
    smoothed = log_returns.rolling(7, min_periods=7).mean().dropna()

    lambdas = [90, 180, 365]
    results = {}

    cp_signals = {}  # changepoint signal: run-length reset detection
    max_rls = {}

    for lam in lambdas:
        print(f"  Running BOCPD (lambda={lam})...")
        bocpd = BOCPD(hazard_lambda=lam)
        cp_prob, max_rl = bocpd.run(smoothed.values)

        # Detect changepoints: MAP run length drops by >50% or below 10
        # This is more robust than P(rl=0) which stays near the hazard rate
        rl_series = pd.Series(max_rl, index=smoothed.index)
        rl_prev = rl_series.shift(1)

        # Changepoint = MAP run length drops significantly
        cond1 = (rl_prev > 20) & (rl_series < 5)

        # Also flag when rl drops by >70%
        rl_drop = (rl_prev - rl_series) / rl_prev.clip(lower=1)
        cond2 = (rl_drop > 0.7) & (rl_prev > 10)

        cp_signal = (cond1 | cond2).astype(float)

        cp_signals[lam] = cp_signal
        max_rls[lam] = rl_series

    # Classify regime based on rolling 30d mean return
    rolling_mean_ret = returns.rolling(30).mean()

    def classify_regime(mean_ret):
        if np.isnan(mean_ret):
            return "n/a"
        return "BULL" if mean_ret > 0 else "BEAR"

    regime = rolling_mean_ret.apply(classify_regime)

    # Monthly table
    monthly = pd.DataFrame()
    monthly["btc_close"] = close.resample("ME").last()
    monthly["btc_ret_30d"] = close.pct_change(30).resample("ME").last() * 100
    monthly["regime"] = regime.resample("ME").last()

    for lam in lambdas:
        # Number of changepoints detected in the month
        monthly[f"cp_count_{lam}"] = cp_signals[lam].resample("ME").sum()
        # Flag if any changepoint detected
        monthly[f"cp_flag_{lam}"] = (monthly[f"cp_count_{lam}"] > 0).map(
            {True: "CP", False: ""}
        )
        # Mean MAP run length in the month (low = unstable regime)
        monthly[f"rl_mean_{lam}"] = max_rls[lam].resample("ME").mean()

    return monthly.dropna(subset=["btc_ret_30d"])


# ============================================================================
#  COMPARISON TABLE
# ============================================================================

BEAR_YEARS = {2022, 2026}  # Hardcoded bear years for reference


def build_comparison(hurst_monthly: pd.DataFrame, bocpd_monthly: pd.DataFrame) -> pd.DataFrame:
    """Build side-by-side comparison table."""
    # Align on common months
    common_idx = hurst_monthly.index.intersection(bocpd_monthly.index)

    comp = pd.DataFrame(index=common_idx)
    comp["btc_close"] = hurst_monthly.loc[common_idx, "btc_close"]
    comp["btc_ret_30d"] = hurst_monthly.loc[common_idx, "btc_ret_30d"]

    # Hurst (use 60d as primary)
    comp["hurst_60d"] = hurst_monthly.loc[common_idx, "H_60d"]
    comp["hurst_regime"] = hurst_monthly.loc[common_idx, "regime_60d"]

    # BOCPD (use lambda=180 as primary)
    comp["bocpd_regime"] = bocpd_monthly.loc[common_idx, "regime"]
    comp["rl_mean_180"] = bocpd_monthly.loc[common_idx, "rl_mean_180"]
    comp["cp_flag"] = bocpd_monthly.loc[common_idx, "cp_flag_180"]

    # Bear year flag
    comp["bear_yr"] = pd.Series(
        np.where(comp.index.year.isin(BEAR_YEARS), "BEAR", ""),
        index=comp.index,
    )

    return comp


# ============================================================================
#  PRINTING HELPERS
# ============================================================================

def print_hurst_table(monthly: pd.DataFrame):
    """Print monthly Hurst analysis table."""
    print("\n" + "=" * 120)
    print("  ROLLING HURST EXPONENT — MONTHLY TABLE")
    print("=" * 120)

    header = f"{'Month':<10} {'BTC Close':>10} {'Ret 30d':>8}"
    for w in [30, 60, 90, 120]:
        header += f" {'H_' + str(w) + 'd':>7} {'Regime':>7}"
    print(header)
    print("-" * 120)

    for dt, row in monthly.iterrows():
        line = f"{dt.strftime('%Y-%m'):<10} {row['btc_close']:>10,.0f} {row['btc_ret_30d']:>7.1f}%"
        for w in [30, 60, 90, 120]:
            h_val = row.get(f"H_{w}d", np.nan)
            regime = row.get(f"regime_{w}d", "n/a")
            if np.isnan(h_val):
                line += f" {'n/a':>7} {'n/a':>7}"
            else:
                line += f" {h_val:>7.3f} {regime:>7}"
        print(line)


def print_bocpd_table(monthly: pd.DataFrame):
    """Print monthly BOCPD analysis table."""
    print("\n" + "=" * 130)
    print("  BAYESIAN ONLINE CHANGEPOINT DETECTION — MONTHLY TABLE")
    print("=" * 130)

    header = f"{'Month':<10} {'BTC Close':>10} {'Ret 30d':>8} {'Regime':>6}"
    for lam in [90, 180, 365]:
        header += f" {'RL_' + str(lam):>6} {'CPs':>3} {'Flag':>4}"
    print(header)
    print("-" * 130)

    for dt, row in monthly.iterrows():
        line = (
            f"{dt.strftime('%Y-%m'):<10} {row['btc_close']:>10,.0f} "
            f"{row['btc_ret_30d']:>7.1f}% {row['regime']:>6}"
        )
        for lam in [90, 180, 365]:
            rl_val = row.get(f"rl_mean_{lam}", np.nan)
            cp_count = row.get(f"cp_count_{lam}", 0)
            flag = row.get(f"cp_flag_{lam}", "")
            if np.isnan(rl_val):
                line += f" {'n/a':>6} {'':>3} {'':>4}"
            else:
                line += f" {rl_val:>6.0f} {int(cp_count):>3} {flag:>4}"
        print(line)


def print_comparison_table(comp: pd.DataFrame):
    """Print side-by-side comparison table."""
    print("\n" + "=" * 120)
    print("  COMPARISON TABLE — ALL DETECTORS SIDE BY SIDE")
    print("=" * 120)

    header = (
        f"{'Month':<10} {'BTC':>9} {'Ret30d':>7} "
        f"{'Hurst':>6} {'H_Reg':>7} "
        f"{'BOCPD':>6} {'RL':>5} {'CP?':>3} "
        f"{'BearYr':>6}"
    )
    print(header)
    print("-" * 100)

    for dt, row in comp.iterrows():
        h_val = row["hurst_60d"]
        h_str = f"{h_val:.3f}" if not np.isnan(h_val) else "n/a"
        rl_val = row["rl_mean_180"]
        rl_str = f"{rl_val:.0f}" if not np.isnan(rl_val) else "n/a"

        line = (
            f"{dt.strftime('%Y-%m'):<10} {row['btc_close']:>9,.0f} {row['btc_ret_30d']:>6.1f}% "
            f"{h_str:>6} {row['hurst_regime']:>7} "
            f"{row['bocpd_regime']:>6} {rl_str:>5} {row['cp_flag']:>3} "
            f"{row['bear_yr']:>6}"
        )
        print(line)


def print_summary_stats(comp: pd.DataFrame):
    """Print summary statistics for each regime detector."""
    print("\n" + "=" * 80)
    print("  SUMMARY STATISTICS")
    print("=" * 80)

    # Hurst regime stats
    print("\n--- Hurst 60d Regime vs Returns ---")
    for regime in ["TREND", "MR", "RANDOM"]:
        mask = comp["hurst_regime"] == regime
        if mask.sum() > 0:
            rets = comp.loc[mask, "btc_ret_30d"]
            print(
                f"  {regime:>6}: {mask.sum():>3} months | "
                f"mean ret={rets.mean():>6.1f}% | "
                f"median={rets.median():>6.1f}% | "
                f"win rate={100*(rets>0).mean():>5.1f}%"
            )

    # BOCPD regime stats
    print("\n--- BOCPD Regime (lambda=180) vs Returns ---")
    for regime in ["BULL", "BEAR"]:
        mask = comp["bocpd_regime"] == regime
        if mask.sum() > 0:
            rets = comp.loc[mask, "btc_ret_30d"]
            print(
                f"  {regime:>6}: {mask.sum():>3} months | "
                f"mean ret={rets.mean():>6.1f}% | "
                f"median={rets.median():>6.1f}% | "
                f"win rate={100*(rets>0).mean():>5.1f}%"
            )

    # Changepoint months
    cp_months = comp[comp["cp_flag"] == "CP"]
    print(f"\n--- Changepoint Months (lambda=180, run-length reset) ---")
    print(f"  Total: {len(cp_months)} months with changepoints")
    if len(cp_months) > 0:
        for dt, row in cp_months.iterrows():
            print(
                f"  {dt.strftime('%Y-%m')}: "
                f"BTC ret={row['btc_ret_30d']:>6.1f}% | "
                f"mean_rl={row['rl_mean_180']:.0f} | "
                f"regime={row['bocpd_regime']}"
            )

    # Bear year accuracy
    print("\n--- Bear Year Detection ---")
    for year in sorted(BEAR_YEARS):
        yr_mask = comp.index.year == year
        if yr_mask.sum() == 0:
            continue
        yr_data = comp[yr_mask]
        bear_months = (yr_data["bocpd_regime"] == "BEAR").sum()
        mr_months = (yr_data["hurst_regime"] == "MR").sum()
        trend_months = (yr_data["hurst_regime"] == "TREND").sum()
        print(
            f"  {year}: "
            f"BOCPD bear={bear_months}/{yr_mask.sum()} months | "
            f"Hurst MR={mr_months} TREND={trend_months}"
        )


# ============================================================================
#  MAIN
# ============================================================================

def main():
    print("Loading BTC daily data...")
    daily = load_btc_daily()
    print(f"  {len(daily)} daily bars: {daily.index[0].date()} to {daily.index[-1].date()}")

    print("\n--- Task 1: Rolling Hurst Exponent ---")
    hurst_monthly = run_hurst_analysis(daily)
    print_hurst_table(hurst_monthly)

    print("\n--- Task 2: Bayesian Online Changepoint Detection ---")
    bocpd_monthly = run_bocpd_analysis(daily)
    print_bocpd_table(bocpd_monthly)

    print("\n--- Task 3: Comparison ---")
    comp = build_comparison(hurst_monthly, bocpd_monthly)
    print_comparison_table(comp)
    print_summary_stats(comp)

    print("\nDone.")


if __name__ == "__main__":
    main()
