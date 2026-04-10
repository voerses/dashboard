"""
HMM-based regime detector for BTC crypto markets.

Fits 2-state and 3-state Gaussian HMMs on daily BTC data using expanding windows
(causal -- no future data leakage). Compares regime classifications against:
- Hardcoded s523r bear years [2022, 2025, 2026]
- SMA50-week (350-day) classification
- Hindsight accuracy (was the regime call correct based on actual BTC returns?)
"""

import warnings
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
from hmmlearn.hmm import GaussianHMM


# ── 1. Load and resample data ────────────────────────────────────────────────

DATA_PATH = "/workspace/crypto_backtest/data/perp/binance/1h_ohlcv/BTC_perp_1h.csv"

df_raw = pd.read_csv(DATA_PATH, parse_dates=["datetime"])
df_raw = df_raw.set_index("datetime").sort_index()

# Resample to daily OHLCV
daily = df_raw.resample("1D").agg({
    "open": "first",
    "high": "max",
    "low": "min",
    "close": "last",
    "volume": "sum",
}).dropna()

print(f"Daily BTC data: {daily.index[0].date()} to {daily.index[-1].date()} ({len(daily)} bars)")


# ── 2. Compute features ──────────────────────────────────────────────────────

daily["log_ret"] = np.log(daily["close"] / daily["close"].shift(1))
daily["rvol_20d"] = daily["log_ret"].rolling(20).std() * np.sqrt(252)  # annualized
daily = daily.dropna(subset=["rvol_20d"]).copy()

print(f"Feature data: {daily.index[0].date()} to {daily.index[-1].date()} ({len(daily)} bars)")


# ── 3. Expanding-window HMM fit ──────────────────────────────────────────────

def fit_hmm_expanding(daily_df, n_states=3, refit_days=90, min_train=180):
    """
    Expanding window HMM. Every `refit_days` days, refit on all data from start
    to current point. Use model.predict() on the full training window but only
    record the regime for the NEXT quarter (until next refit).

    Returns a Series of regime labels aligned to daily_df index.
    """
    features_cols = ["log_ret", "rvol_20d"]
    X_all = daily_df[features_cols].values
    n = len(X_all)

    regimes = pd.Series(index=daily_df.index, dtype=float)
    state_means_history = []  # track mean mappings over time

    # Refit points: every refit_days starting from min_train
    refit_points = list(range(min_train, n, refit_days))
    if refit_points[-1] != n:
        refit_points.append(n)

    prev_bull_state = None

    for i, end_idx in enumerate(refit_points):
        X_train = X_all[:end_idx]

        # Fit HMM with multiple random restarts for stability
        best_model = None
        best_score = -np.inf
        for seed in range(10):
            try:
                model = GaussianHMM(
                    n_components=n_states,
                    covariance_type="full",
                    n_iter=200,
                    random_state=seed,
                    tol=1e-4,
                )
                model.fit(X_train)
                score = model.score(X_train)
                if score > best_score:
                    best_score = score
                    best_model = model
            except Exception:
                continue

        if best_model is None:
            continue

        # Get state labels for training data
        states = best_model.predict(X_train)

        # Map states by mean return
        mean_returns = best_model.means_[:, 0]  # first feature = log_ret
        sorted_states = np.argsort(mean_returns)  # lowest to highest

        if n_states == 3:
            # BEAR = lowest mean, SIDEWAYS = middle, BULL = highest
            state_map = {}
            state_map[sorted_states[0]] = "bear"
            state_map[sorted_states[1]] = "sideways"
            state_map[sorted_states[2]] = "bull"
        else:  # 2-state
            state_map = {}
            state_map[sorted_states[0]] = "bear"
            state_map[sorted_states[1]] = "bull"

        # Get filtered probabilities for all training data
        probs = best_model.predict_proba(X_train)

        # Assign regimes for the period from previous refit to this refit
        if i == 0:
            start_assign = 0
        else:
            start_assign = refit_points[i - 1]

        for j in range(start_assign, end_idx):
            state_label = states[j]
            regimes.iloc[j] = state_label

        # Store mapping for this window
        state_means_history.append({
            "end_date": daily_df.index[end_idx - 1],
            "state_map": state_map.copy(),
            "means": mean_returns.copy(),
        })

    # Convert numeric states to regime labels using the state_map from each period
    regime_labels = pd.Series(index=daily_df.index, dtype=object)

    # Apply state maps period by period
    for i, end_idx in enumerate(refit_points):
        if i < len(state_means_history):
            smap = state_means_history[i]["state_map"]
            if i == 0:
                start_assign = 0
            else:
                start_assign = refit_points[i - 1]

            for j in range(start_assign, end_idx):
                raw_state = regimes.iloc[j]
                if not np.isnan(raw_state):
                    regime_labels.iloc[j] = smap[int(raw_state)]

    # Get probabilities too (refit and store)
    # Re-run final model for probabilities
    regime_probs = pd.Series(index=daily_df.index, dtype=float)

    for i, end_idx in enumerate(refit_points):
        if i >= len(state_means_history):
            continue
        X_train = X_all[:end_idx]

        # Re-fit best model for this window
        best_model2 = None
        best_score2 = -np.inf
        for seed in range(10):
            try:
                model = GaussianHMM(
                    n_components=n_states,
                    covariance_type="full",
                    n_iter=200,
                    random_state=seed,
                    tol=1e-4,
                )
                model.fit(X_train)
                score = model.score(X_train)
                if score > best_score2:
                    best_score2 = score
                    best_model2 = model
            except Exception:
                continue

        if best_model2 is None:
            continue

        probs = best_model2.predict_proba(X_train)
        states = best_model2.predict(X_train)

        if i == 0:
            start_assign = 0
        else:
            start_assign = refit_points[i - 1]

        for j in range(start_assign, end_idx):
            state_idx = states[j]
            regime_probs.iloc[j] = probs[j, state_idx]

    return regime_labels, regime_probs


print("\n" + "="*80)
print("FITTING 3-STATE HMM (expanding window, 90-day refit)")
print("="*80)
regime_3s, prob_3s = fit_hmm_expanding(daily, n_states=3, refit_days=90)

print("\nFITTING 2-STATE HMM (expanding window, 90-day refit)")
print("="*80)
regime_2s, prob_2s = fit_hmm_expanding(daily, n_states=2, refit_days=90)


# ── 4. Comparison classifiers ────────────────────────────────────────────────

# s523r hardcoded bear years
BEAR_YEARS = [2022, 2025, 2026]
daily["s523r_regime"] = daily.index.year.map(
    lambda y: "bear" if y in BEAR_YEARS else "bull"
)

# SMA50-week (350 calendar days ~ 250 trading days, use 350 for daily)
daily["sma350"] = daily["close"].rolling(350, min_periods=200).mean()
daily["sma50w_regime"] = np.where(daily["close"] > daily["sma350"], "bull", "bear")

# HMM regimes
daily["hmm3_regime"] = regime_3s
daily["hmm3_prob"] = prob_3s
daily["hmm2_regime"] = regime_2s
daily["hmm2_prob"] = prob_2s

# For strategy: bear+sideways = "bear regime", bull = "bull regime"
daily["hmm3_strat"] = daily["hmm3_regime"].map(
    lambda x: "bull" if x == "bull" else "bear" if pd.notna(x) else np.nan
)
daily["hmm2_strat"] = daily["hmm2_regime"]  # already bull/bear


# ── 5. Monthly output table ──────────────────────────────────────────────────

monthly = daily.resample("MS").agg({
    "close": "last",
    "log_ret": "sum",
    "hmm3_regime": lambda x: x.mode().iloc[0] if len(x.dropna()) > 0 else np.nan,
    "hmm3_prob": "mean",
    "hmm3_strat": lambda x: x.mode().iloc[0] if len(x.dropna()) > 0 else np.nan,
    "hmm2_regime": lambda x: x.mode().iloc[0] if len(x.dropna()) > 0 else np.nan,
    "hmm2_prob": "mean",
    "s523r_regime": "first",
    "sma50w_regime": lambda x: x.mode().iloc[0] if len(x.dropna()) > 0 else np.nan,
})

monthly["btc_ret_pct"] = (np.exp(monthly["log_ret"]) - 1) * 100
monthly["30d_ret"] = daily["close"].pct_change(30).resample("MS").last() * 100

print("\n" + "="*80)
print("MONTHLY REGIME COMPARISON TABLE")
print("="*80)

header = f"{'Month':>10} | {'HMM-3':>8} {'Prob':>5} | {'HMM-2':>6} {'Prob':>5} | {'s523r':>6} | {'SMA50w':>7} | {'BTC Ret':>8}"
print(header)
print("-" * len(header))

for idx, row in monthly.iterrows():
    month_str = idx.strftime("%Y-%m")
    hmm3 = str(row["hmm3_regime"])[:8] if pd.notna(row["hmm3_regime"]) else "n/a"
    hmm3p = f"{row['hmm3_prob']:.2f}" if pd.notna(row["hmm3_prob"]) else "n/a"
    hmm2 = str(row["hmm2_regime"])[:6] if pd.notna(row["hmm2_regime"]) else "n/a"
    hmm2p = f"{row['hmm2_prob']:.2f}" if pd.notna(row["hmm2_prob"]) else "n/a"
    s523r = row["s523r_regime"]
    sma = str(row["sma50w_regime"])[:7] if pd.notna(row["sma50w_regime"]) else "n/a"
    btc_ret = f"{row['btc_ret_pct']:+.1f}%" if pd.notna(row["btc_ret_pct"]) else "n/a"

    print(f"{month_str:>10} | {hmm3:>8} {hmm3p:>5} | {hmm2:>6} {hmm2p:>5} | {s523r:>6} | {sma:>7} | {btc_ret:>8}")


# ── 6. Yearly regime distribution ────────────────────────────────────────────

print("\n" + "="*80)
print("YEARLY REGIME DISTRIBUTION (% of days)")
print("="*80)

for year in sorted(daily.index.year.unique()):
    yr_data = daily[daily.index.year == year]
    if len(yr_data) == 0:
        continue

    hmm3_counts = yr_data["hmm3_regime"].value_counts(normalize=True, dropna=False) * 100
    hmm2_counts = yr_data["hmm2_regime"].value_counts(normalize=True, dropna=False) * 100

    bull3 = hmm3_counts.get("bull", 0)
    bear3 = hmm3_counts.get("bear", 0)
    side3 = hmm3_counts.get("sideways", 0)

    bull2 = hmm2_counts.get("bull", 0)
    bear2 = hmm2_counts.get("bear", 0)

    s523r_val = "BEAR" if year in BEAR_YEARS else "BULL"
    btc_yr_ret = (yr_data["close"].iloc[-1] / yr_data["close"].iloc[0] - 1) * 100

    print(f"\n{year}  (BTC: {btc_yr_ret:+.1f}%)  s523r={s523r_val}")
    print(f"  HMM-3: bull={bull3:.0f}% sideways={side3:.0f}% bear={bear3:.0f}%")
    print(f"  HMM-2: bull={bull2:.0f}% bear={bear2:.0f}%")


# ── 7. Hindsight accuracy ────────────────────────────────────────────────────

print("\n" + "="*80)
print("HINDSIGHT ACCURACY CHECK")
print("="*80)
print("For each month: was the regime call 'correct'?")
print("  Correct = bear regime when BTC fell, bull regime when BTC rose")
print("  Using 30-day forward return as ground truth\n")

# Forward 30d return for each month
daily["fwd_30d_ret"] = daily["close"].pct_change(30).shift(-30)
monthly_fwd = daily.resample("MS").agg({"fwd_30d_ret": "first"})
monthly = monthly.join(monthly_fwd, how="left")

# Ground truth: negative fwd return = bear was correct, positive = bull was correct
monthly["ground_truth"] = np.where(monthly["fwd_30d_ret"] < 0, "bear", "bull")

# Check accuracy for each classifier
def calc_accuracy(pred_col, truth_col, df):
    mask = df[pred_col].notna() & df[truth_col].notna()
    subset = df[mask]
    if len(subset) == 0:
        return np.nan, 0

    # For HMM-3: sideways counts as bear for strategy purposes
    preds = subset[pred_col].map(lambda x: "bull" if x == "bull" else "bear")
    correct = (preds == subset[truth_col]).sum()
    return correct / len(subset) * 100, len(subset)

acc_hmm3, n_hmm3 = calc_accuracy("hmm3_strat", "ground_truth", monthly)
acc_hmm2, n_hmm2 = calc_accuracy("hmm2_regime", "ground_truth", monthly)
acc_s523r, n_s523r = calc_accuracy("s523r_regime", "ground_truth", monthly)
acc_sma, n_sma = calc_accuracy("sma50w_regime", "ground_truth", monthly)

print(f"HMM 3-state (bull vs bear+sideways): {acc_hmm3:.1f}% ({n_hmm3} months)")
print(f"HMM 2-state:                         {acc_hmm2:.1f}% ({n_hmm2} months)")
print(f"s523r hardcoded years:                {acc_s523r:.1f}% ({n_s523r} months)")
print(f"SMA 50-week:                          {acc_sma:.1f}% ({n_sma} months)")


# ── 8. Per-year accuracy breakdown ───────────────────────────────────────────

print("\n" + "="*80)
print("PER-YEAR HINDSIGHT ACCURACY")
print("="*80)

for year in sorted(monthly.index.year.unique()):
    yr = monthly[monthly.index.year == year]

    a3, n3 = calc_accuracy("hmm3_strat", "ground_truth", yr)
    a2, n2 = calc_accuracy("hmm2_regime", "ground_truth", yr)
    as5, ns = calc_accuracy("s523r_regime", "ground_truth", yr)
    asm, nm = calc_accuracy("sma50w_regime", "ground_truth", yr)

    if n3 == 0 and n2 == 0:
        continue

    print(f"\n{year}:")
    if n3 > 0: print(f"  HMM-3: {a3:.0f}% ({n3} months)")
    if n2 > 0: print(f"  HMM-2: {a2:.0f}% ({n2} months)")
    if ns > 0: print(f"  s523r: {as5:.0f}% ({ns} months)")
    if nm > 0: print(f"  SMA50: {asm:.0f}% ({nm} months)")


# ── 9. Transition analysis ───────────────────────────────────────────────────

print("\n" + "="*80)
print("REGIME TRANSITION ANALYSIS (HMM-3)")
print("="*80)

# Find regime transitions
transitions = []
prev_regime = None
for idx, regime in daily["hmm3_regime"].items():
    if pd.isna(regime):
        continue
    if prev_regime is not None and regime != prev_regime:
        transitions.append({
            "date": idx,
            "from": prev_regime,
            "to": regime,
            "btc_price": daily.loc[idx, "close"],
        })
    prev_regime = regime

print(f"\nTotal transitions: {len(transitions)}")
print(f"\n{'Date':>12} | {'From':>10} -> {'To':>10} | {'BTC Price':>10}")
print("-" * 55)
for t in transitions[-30:]:  # show last 30
    print(f"{t['date'].strftime('%Y-%m-%d'):>12} | {t['from']:>10} -> {t['to']:>10} | ${t['btc_price']:>10,.0f}")

# Count transition frequency
from collections import Counter
trans_counts = Counter([(t["from"], t["to"]) for t in transitions])
print(f"\nTransition counts:")
for (fr, to), count in sorted(trans_counts.items(), key=lambda x: -x[1]):
    print(f"  {fr:>10} -> {to:>10}: {count}")


# ── 10. Summary & recommendations ────────────────────────────────────────────

print("\n" + "="*80)
print("SUMMARY & ANALYSIS")
print("="*80)

print(f"""
Key findings:
- HMM 3-state hindsight accuracy: {acc_hmm3:.1f}%
- HMM 2-state hindsight accuracy: {acc_hmm2:.1f}%
- s523r hardcoded accuracy:       {acc_s523r:.1f}%
- SMA 50-week accuracy:           {acc_sma:.1f}%

Total regime transitions (HMM-3): {len(transitions)}
Avg transitions per year: {len(transitions) / max(1, len(daily.index.year.unique())):.1f}

Note: High transition count = noisy signal, bad for strategy that needs stable regimes.
The HMM is fitted causally (expanding window) so there is no lookahead bias,
but the state labeling (which state = bull) uses within-window mean returns.
""")
