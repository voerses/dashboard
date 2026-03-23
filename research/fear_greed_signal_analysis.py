"""
Fear & Greed Index Signal Analysis for Crypto Return Prediction
================================================================
Tests whether the Alternative.me Fear & Greed Index has predictive power
for BTC and ETH forward returns across multiple signal constructions.

Methodology:
- Strict temporal split: IS (before 2025-07-01) / OOS (after 2025-07-01)
- Parameters calibrated on IS only, evaluated on OOS
- Threshold: |IC| > 0.05 and |t| > 2.0 with matching IS/OOS sign to PASS

Signals tested:
1. Raw F&G value (contrarian: extreme fear = buy, greed = sell)
2. F&G z-score (rolling 30d, 90d)
3. F&G regime changes (crossing 25/75 thresholds)
4. F&G momentum (change over 7d, 14d)
5. Extreme readings (<10, >90) as event signals
"""

import pandas as pd
import numpy as np
from scipy import stats
import warnings
warnings.filterwarnings('ignore')

# ==============================================================================
# Configuration
# ==============================================================================
SPLIT_DATE = pd.Timestamp('2025-07-01')
FG_PATH = '/workspace/crypto_backtest/data/alternative/fear_greed/fear_greed_index.parquet'
PRICE_DIR = '/workspace/crypto_backtest/data/perp/1h_cache'
TOKENS = ['BTC', 'ETH']
FWD_HORIZONS = {'1d': 24, '3d': 72, '7d': 168, '14d': 336}  # in hours
IC_THRESHOLD = 0.05
T_THRESHOLD = 2.0


def load_fear_greed():
    """Load and clean Fear & Greed index data."""
    fg = pd.read_parquet(FG_PATH)
    fg['date'] = pd.to_datetime(fg['timestamp']).dt.tz_localize(None)
    fg = fg.set_index('date').sort_index()
    fg = fg[~fg.index.duplicated(keep='first')]
    fg = fg[['value', 'value_classification']]
    fg['value'] = fg['value'].astype(float)
    print(f"Fear & Greed data: {fg.index.min().date()} to {fg.index.max().date()}, {len(fg)} days")
    print(f"  Value range: {fg['value'].min():.0f} - {fg['value'].max():.0f}, mean: {fg['value'].mean():.1f}")
    return fg


def load_daily_close(token):
    """Load hourly data and resample to daily close at 00:00 UTC."""
    path = f'{PRICE_DIR}/{token}_1h.parquet'
    df = pd.read_parquet(path)
    # Resample to daily: use the close price at end of each day
    daily = df['close'].resample('1D').last().dropna()
    daily.index = daily.index.normalize()
    print(f"  {token} daily close: {daily.index.min().date()} to {daily.index.max().date()}, {len(daily)} days")
    return daily


def compute_forward_returns(prices, horizons_hours):
    """Compute forward returns at multiple horizons (using daily data, so horizon in days)."""
    fwd = pd.DataFrame(index=prices.index)
    for label, hours in horizons_hours.items():
        days = hours // 24
        fwd[f'fwd_{label}'] = prices.pct_change(days).shift(-days)
    return fwd


def compute_ic_and_tstat(signal, returns, min_obs=30):
    """Compute Spearman rank IC and t-statistic."""
    aligned = pd.concat([signal, returns], axis=1).dropna()
    if len(aligned) < min_obs:
        return np.nan, np.nan, 0
    ic, pval = stats.spearmanr(aligned.iloc[:, 0], aligned.iloc[:, 1])
    n = len(aligned)
    # t-stat for correlation
    if abs(ic) >= 1.0:
        t_stat = np.inf * np.sign(ic)
    else:
        t_stat = ic * np.sqrt((n - 2) / (1 - ic**2))
    return ic, t_stat, n


def build_signals(fg):
    """Build all signal variants from Fear & Greed data."""
    signals = {}
    fg_val = fg['value'].copy()

    # Signal 1: Raw F&G value (CONTRARIAN: negate so high fear = positive signal)
    signals['raw_contrarian'] = -fg_val

    # Signal 2a: Z-score rolling 30d
    roll_30_mean = fg_val.rolling(30, min_periods=20).mean()
    roll_30_std = fg_val.rolling(30, min_periods=20).std()
    zscore_30 = -(fg_val - roll_30_mean) / roll_30_std.replace(0, np.nan)
    signals['zscore_30d'] = zscore_30

    # Signal 2b: Z-score rolling 90d
    roll_90_mean = fg_val.rolling(90, min_periods=60).mean()
    roll_90_std = fg_val.rolling(90, min_periods=60).std()
    zscore_90 = -(fg_val - roll_90_mean) / roll_90_std.replace(0, np.nan)
    signals['zscore_90d'] = zscore_90

    # Signal 3: Regime change signals
    # Crossing below 25 (entering extreme fear) = buy signal (+1)
    # Crossing above 75 (entering extreme greed) = sell signal (-1)
    regime = pd.Series(0.0, index=fg_val.index)
    prev = fg_val.shift(1)
    # Entered fear zone
    regime[(fg_val < 25) & (prev >= 25)] = 1.0
    # Entered greed zone
    regime[(fg_val > 75) & (prev <= 75)] = -1.0
    signals['regime_cross'] = regime

    # Signal 3b: Current regime state (in fear zone = +1, neutral = 0, greed = -1)
    regime_state = pd.Series(0.0, index=fg_val.index)
    regime_state[fg_val < 25] = 1.0
    regime_state[fg_val > 75] = -1.0
    signals['regime_state'] = regime_state

    # Signal 4a: F&G momentum 7d (contrarian: declining F&G = buy)
    mom_7 = -fg_val.diff(7)
    signals['momentum_7d'] = mom_7

    # Signal 4b: F&G momentum 14d
    mom_14 = -fg_val.diff(14)
    signals['momentum_14d'] = mom_14

    # Signal 5a: Extreme fear event (<10) -- binary buy signal
    extreme_fear = pd.Series(0.0, index=fg_val.index)
    extreme_fear[fg_val < 10] = 1.0
    signals['extreme_fear_lt10'] = extreme_fear

    # Signal 5b: Extreme greed event (>90) -- binary sell signal
    extreme_greed = pd.Series(0.0, index=fg_val.index)
    extreme_greed[fg_val > 90] = -1.0
    signals['extreme_greed_gt90'] = extreme_greed

    # Signal 5c: Combined extreme signals
    extreme_combined = extreme_fear + extreme_greed
    signals['extreme_combined'] = extreme_combined

    return signals


def evaluate_signals(signals, fwd_returns, split_date):
    """Evaluate all signals against forward returns with IS/OOS split."""
    results = []

    for sig_name, sig in signals.items():
        for ret_col in fwd_returns.columns:
            horizon = ret_col.replace('fwd_', '')

            # Align signal and returns
            combined = pd.concat([sig.rename('signal'), fwd_returns[ret_col]], axis=1).dropna()

            # IS/OOS split
            is_data = combined[combined.index < split_date]
            oos_data = combined[combined.index >= split_date]

            # Compute IC and t-stat for IS
            ic_is, t_is, n_is = compute_ic_and_tstat(
                is_data['signal'], is_data[ret_col]
            )

            # Compute IC and t-stat for OOS
            ic_oos, t_oos, n_oos = compute_ic_and_tstat(
                oos_data['signal'], oos_data[ret_col]
            )

            # Determine if signs match
            if np.isnan(ic_is) or np.isnan(ic_oos):
                sign_match = False
            else:
                sign_match = np.sign(ic_is) == np.sign(ic_oos)

            # Pass/fail determination
            passes = (
                abs(ic_is) > IC_THRESHOLD and
                abs(t_is) > T_THRESHOLD and
                abs(ic_oos) > IC_THRESHOLD and
                abs(t_oos) > T_THRESHOLD and
                sign_match
            )

            results.append({
                'signal': sig_name,
                'horizon': horizon,
                'IC_IS': ic_is,
                't_IS': t_is,
                'n_IS': n_is,
                'IC_OOS': ic_oos,
                't_OOS': t_oos,
                'n_OOS': n_oos,
                'sign_match': sign_match,
                'PASS': passes,
            })

    return pd.DataFrame(results)


def print_results_table(df, token, title=""):
    """Print formatted results table."""
    print(f"\n{'='*100}")
    print(f"  {title} — {token}")
    print(f"{'='*100}")
    print(f"{'Signal':<22} {'Horizon':<8} {'IC_IS':>8} {'t_IS':>8} {'n_IS':>6} "
          f"{'IC_OOS':>8} {'t_OOS':>8} {'n_OOS':>6} {'Sign?':>6} {'Result':>8}")
    print(f"{'-'*22} {'-'*8} {'-'*8} {'-'*8} {'-'*6} {'-'*8} {'-'*8} {'-'*6} {'-'*6} {'-'*8}")

    for _, row in df.iterrows():
        ic_is = f"{row['IC_IS']:.4f}" if not np.isnan(row['IC_IS']) else "N/A"
        t_is = f"{row['t_IS']:.2f}" if not np.isnan(row['t_IS']) else "N/A"
        ic_oos = f"{row['IC_OOS']:.4f}" if not np.isnan(row['IC_OOS']) else "N/A"
        t_oos = f"{row['t_OOS']:.2f}" if not np.isnan(row['t_OOS']) else "N/A"
        sign = "YES" if row['sign_match'] else "NO"
        result = "PASS" if row['PASS'] else "FAIL"

        print(f"{row['signal']:<22} {row['horizon']:<8} {ic_is:>8} {t_is:>8} {row['n_IS']:>6.0f} "
              f"{ic_oos:>8} {t_oos:>8} {row['n_OOS']:>6.0f} {sign:>6} {result:>8}")


def analyze_extreme_events(fg, daily_close, token, split_date):
    """Detailed analysis of extreme F&G readings as event signals."""
    print(f"\n{'='*100}")
    print(f"  EXTREME EVENT ANALYSIS — {token}")
    print(f"{'='*100}")

    fg_val = fg['value']
    fwd = compute_forward_returns(daily_close, FWD_HORIZONS)
    combined = pd.concat([fg_val, fwd], axis=1).dropna()

    for threshold, label, condition in [
        (10, 'Extreme Fear (<10)', combined['value'] < 10),
        (20, 'Fear (<20)', combined['value'] < 20),
        (25, 'Fear (<25)', combined['value'] < 25),
        (75, 'Greed (>75)', combined['value'] > 75),
        (80, 'Greed (>80)', combined['value'] > 80),
        (90, 'Extreme Greed (>90)', combined['value'] > 90),
    ]:
        events = combined[condition]
        non_events = combined[~condition]

        if len(events) < 5:
            print(f"\n  {label}: Only {len(events)} events -- insufficient data")
            continue

        print(f"\n  {label}: {len(events)} events")

        # Split IS/OOS
        events_is = events[events.index < split_date]
        events_oos = events[events.index >= split_date]
        non_events_is = non_events[non_events.index < split_date]

        for h_label in FWD_HORIZONS:
            col = f'fwd_{h_label}'
            if col not in events.columns:
                continue

            ev_mean_is = events_is[col].mean() * 100 if len(events_is) > 0 else np.nan
            ev_mean_oos = events_oos[col].mean() * 100 if len(events_oos) > 0 else np.nan
            non_ev_mean_is = non_events_is[col].mean() * 100 if len(non_events_is) > 0 else np.nan

            # t-test: event returns vs non-event returns (IS only for calibration)
            if len(events_is) >= 5 and len(non_events_is) >= 5:
                t_val, p_val = stats.ttest_ind(
                    events_is[col].dropna(),
                    non_events_is[col].dropna(),
                    equal_var=False
                )
            else:
                t_val, p_val = np.nan, np.nan

            ev_is_str = f"{ev_mean_is:+.2f}%" if not np.isnan(ev_mean_is) else "N/A"
            ev_oos_str = f"{ev_mean_oos:+.2f}%" if not np.isnan(ev_mean_oos) else "N/A"
            non_ev_str = f"{non_ev_mean_is:+.2f}%" if not np.isnan(non_ev_mean_is) else "N/A"
            t_str = f"{t_val:.2f}" if not np.isnan(t_val) else "N/A"
            n_is_str = f"{len(events_is)}"
            n_oos_str = f"{len(events_oos)}"

            print(f"    {h_label}: Event_IS={ev_is_str} (n={n_is_str}), "
                  f"NonEvent_IS={non_ev_str}, t={t_str}, "
                  f"Event_OOS={ev_oos_str} (n={n_oos_str})")


def analyze_quintile_returns(fg, daily_close, token, split_date):
    """Quintile analysis: bucket F&G into quintiles and compute average forward returns."""
    print(f"\n{'='*100}")
    print(f"  QUINTILE ANALYSIS — {token}")
    print(f"{'='*100}")

    fg_val = fg['value']
    fwd = compute_forward_returns(daily_close, FWD_HORIZONS)
    combined = pd.concat([fg_val, fwd], axis=1).dropna()

    # IS only for quintile boundaries
    is_data = combined[combined.index < split_date]
    oos_data = combined[combined.index >= split_date]

    # Compute quintile boundaries from IS data
    quintile_boundaries = is_data['value'].quantile([0.2, 0.4, 0.6, 0.8]).values
    labels = ['Q1 (Fear)', 'Q2', 'Q3', 'Q4', 'Q5 (Greed)']

    print(f"\n  Quintile boundaries (from IS): {quintile_boundaries}")
    print(f"  IS period: {is_data.index.min().date()} to {is_data.index.max().date()} ({len(is_data)} days)")
    print(f"  OOS period: {oos_data.index.min().date()} to {oos_data.index.max().date()} ({len(oos_data)} days)")

    # Assign quintiles using IS boundaries for BOTH sets
    is_data = is_data.copy()
    oos_data = oos_data.copy()
    is_data['quintile'] = pd.cut(is_data['value'], bins=[-np.inf] + list(quintile_boundaries) + [np.inf], labels=labels)
    oos_data['quintile'] = pd.cut(oos_data['value'], bins=[-np.inf] + list(quintile_boundaries) + [np.inf], labels=labels)

    for h_label in FWD_HORIZONS:
        col = f'fwd_{h_label}'
        print(f"\n  Forward {h_label} returns by F&G quintile:")
        print(f"  {'Quintile':<16} {'IS Mean':>10} {'IS Median':>10} {'IS N':>6} "
              f"{'OOS Mean':>10} {'OOS Median':>12} {'OOS N':>6}")

        for q_label in labels:
            is_q = is_data[is_data['quintile'] == q_label][col].dropna()
            oos_q = oos_data[oos_data['quintile'] == q_label][col].dropna()

            is_mean = f"{is_q.mean()*100:+.2f}%" if len(is_q) > 0 else "N/A"
            is_med = f"{is_q.median()*100:+.2f}%" if len(is_q) > 0 else "N/A"
            oos_mean = f"{oos_q.mean()*100:+.2f}%" if len(oos_q) > 0 else "N/A"
            oos_med = f"{oos_q.median()*100:+.2f}%" if len(oos_q) > 0 else "N/A"

            print(f"  {q_label:<16} {is_mean:>10} {is_med:>10} {len(is_q):>6} "
                  f"{oos_mean:>10} {oos_med:>12} {len(oos_q):>6}")

        # Monotonicity check: is Q1 > Q5 (contrarian)?
        is_q1 = is_data[is_data['quintile'] == labels[0]][col].mean()
        is_q5 = is_data[is_data['quintile'] == labels[-1]][col].mean()
        oos_q1 = oos_data[oos_data['quintile'] == labels[0]][col].mean() if len(oos_data[oos_data['quintile'] == labels[0]]) > 0 else np.nan
        oos_q5 = oos_data[oos_data['quintile'] == labels[-1]][col].mean() if len(oos_data[oos_data['quintile'] == labels[-1]]) > 0 else np.nan

        q1_q5_is = (is_q1 - is_q5) * 100 if not (np.isnan(is_q1) or np.isnan(is_q5)) else np.nan
        q1_q5_oos = (oos_q1 - oos_q5) * 100 if not (np.isnan(oos_q1) or np.isnan(oos_q5)) else np.nan
        q1q5_is_str = f"{q1_q5_is:+.2f}%" if not np.isnan(q1_q5_is) else "N/A"
        q1q5_oos_str = f"{q1_q5_oos:+.2f}%" if not np.isnan(q1_q5_oos) else "N/A"
        print(f"  Q1-Q5 spread: IS={q1q5_is_str}, OOS={q1q5_oos_str}")


def run_rolling_ic_analysis(signals, fwd_returns, token):
    """Compute rolling 90-day IC to check stability."""
    print(f"\n{'='*100}")
    print(f"  ROLLING IC STABILITY — {token}")
    print(f"{'='*100}")

    key_signals = ['raw_contrarian', 'zscore_30d', 'zscore_90d', 'momentum_7d']
    key_horizons = ['fwd_1d', 'fwd_7d']

    for sig_name in key_signals:
        if sig_name not in signals:
            continue
        sig = signals[sig_name]
        for ret_col in key_horizons:
            combined = pd.concat([sig.rename('signal'), fwd_returns[ret_col]], axis=1).dropna()
            if len(combined) < 90:
                continue

            # Compute rolling 90-day IC
            rolling_ic = combined.rolling(90).apply(
                lambda x: stats.spearmanr(x[:45], x[45:])[0] if len(x) == 90 else np.nan,
                raw=False
            )

            # Actually, let's compute it properly with explicit rolling
            ics = []
            dates = []
            for i in range(90, len(combined)):
                window = combined.iloc[i-90:i]
                ic_val, _ = stats.spearmanr(window['signal'], window[ret_col])
                ics.append(ic_val)
                dates.append(combined.index[i])

            ic_series = pd.Series(ics, index=dates)
            pct_positive = (ic_series > 0).mean() * 100
            pct_sig = (ic_series.abs() > 0.05).mean() * 100

            print(f"\n  {sig_name} vs {ret_col}:")
            print(f"    Mean rolling IC: {ic_series.mean():.4f}")
            print(f"    Std rolling IC:  {ic_series.std():.4f}")
            print(f"    % positive:      {pct_positive:.1f}%")
            print(f"    % |IC|>0.05:     {pct_sig:.1f}%")
            print(f"    Min/Max:         {ic_series.min():.4f} / {ic_series.max():.4f}")


# ==============================================================================
# Main
# ==============================================================================
def main():
    print("=" * 100)
    print("  FEAR & GREED INDEX — CRYPTO RETURN PREDICTION ANALYSIS")
    print("=" * 100)
    print(f"\n  Split date: {SPLIT_DATE.date()}")
    print(f"  IC threshold: |IC| > {IC_THRESHOLD}")
    print(f"  t-stat threshold: |t| > {T_THRESHOLD}")
    print(f"  Forward horizons: {list(FWD_HORIZONS.keys())}")
    print()

    # Load data
    print("Loading data...")
    fg = load_fear_greed()

    # Build signals
    print("\nBuilding signals...")
    signals = build_signals(fg)
    print(f"  Built {len(signals)} signal variants:")
    for name, sig in signals.items():
        non_null = sig.dropna().shape[0]
        non_zero = (sig.dropna() != 0).sum()
        print(f"    {name}: {non_null} non-null values, {non_zero} non-zero")

    # Per-token analysis
    all_results = {}

    for token in TOKENS:
        print(f"\n{'#'*100}")
        print(f"  ANALYZING: {token}")
        print(f"{'#'*100}")

        daily_close = load_daily_close(token)
        fwd_returns = compute_forward_returns(daily_close, FWD_HORIZONS)

        # Check data overlap
        overlap_start = max(fg.index.min(), fwd_returns.index.min())
        overlap_end = min(fg.index.max(), fwd_returns.index.max())
        print(f"  Data overlap: {overlap_start.date()} to {overlap_end.date()}")

        is_count = fwd_returns[(fwd_returns.index >= overlap_start) & (fwd_returns.index < SPLIT_DATE)].shape[0]
        oos_count = fwd_returns[(fwd_returns.index >= SPLIT_DATE) & (fwd_returns.index <= overlap_end)].shape[0]
        print(f"  IS observations: ~{is_count}, OOS observations: ~{oos_count}")

        # Evaluate all signals
        results = evaluate_signals(signals, fwd_returns, SPLIT_DATE)
        all_results[token] = results

        # Print results
        print_results_table(results, token, "SIGNAL IC & T-STAT RESULTS")

        # Print passing signals
        passing = results[results['PASS']]
        if len(passing) > 0:
            print(f"\n  *** {len(passing)} PASSING signal(s) for {token}:")
            for _, row in passing.iterrows():
                print(f"      {row['signal']} @ {row['horizon']}: "
                      f"IC_IS={row['IC_IS']:.4f} (t={row['t_IS']:.2f}), "
                      f"IC_OOS={row['IC_OOS']:.4f} (t={row['t_OOS']:.2f})")
        else:
            print(f"\n  *** NO signals pass the threshold for {token}")

        # Quintile analysis
        analyze_quintile_returns(fg, daily_close, token, SPLIT_DATE)

        # Extreme event analysis
        analyze_extreme_events(fg, daily_close, token, SPLIT_DATE)

        # Rolling IC stability
        run_rolling_ic_analysis(signals, fwd_returns, token)

    # ==============================================================================
    # Summary
    # ==============================================================================
    print("\n" + "=" * 100)
    print("  FINAL SUMMARY")
    print("=" * 100)

    for token in TOKENS:
        results = all_results[token]
        passing = results[results['PASS']]
        total = len(results)

        print(f"\n  {token}: {len(passing)}/{total} signal-horizon combinations PASS")

        if len(passing) > 0:
            print(f"  Passing signals:")
            for _, row in passing.iterrows():
                print(f"    - {row['signal']} @ {row['horizon']}: "
                      f"IC_IS={row['IC_IS']:.4f}, IC_OOS={row['IC_OOS']:.4f}")
        else:
            # Show the closest to passing
            results_valid = results.dropna(subset=['IC_IS', 'IC_OOS'])
            if len(results_valid) > 0:
                # Score: average of abs IC values, penalize sign mismatch
                results_valid = results_valid.copy()
                results_valid['score'] = (
                    results_valid['IC_IS'].abs() + results_valid['IC_OOS'].abs()
                ) / 2
                results_valid.loc[~results_valid['sign_match'], 'score'] *= -1

                top = results_valid.nlargest(5, 'score')
                print(f"  Closest to passing (top 5 by avg |IC|):")
                for _, row in top.iterrows():
                    sign_str = "MATCH" if row['sign_match'] else "FLIP"
                    print(f"    - {row['signal']} @ {row['horizon']}: "
                          f"IC_IS={row['IC_IS']:.4f} (t={row['t_IS']:.2f}), "
                          f"IC_OOS={row['IC_OOS']:.4f} (t={row['t_OOS']:.2f}), "
                          f"sign={sign_str}")

    # Overall verdict
    total_passing = sum(len(all_results[t][all_results[t]['PASS']]) for t in TOKENS)
    total_tests = sum(len(all_results[t]) for t in TOKENS)

    print(f"\n{'='*100}")
    print(f"  VERDICT: {total_passing}/{total_tests} signal-horizon-token combinations PASS")
    if total_passing == 0:
        print("  CONCLUSION: The Fear & Greed Index does NOT provide reliable predictive")
        print("  signal for BTC or ETH forward returns under strict IS/OOS validation.")
        print("  None of the tested signal constructions meet the |IC|>0.05 AND |t|>2.0")
        print("  threshold with consistent sign across IS and OOS periods.")
    elif total_passing < 5:
        print("  CONCLUSION: Weak and sparse evidence of predictive power. Only a small")
        print("  number of signal-horizon combinations pass. Exercise extreme caution.")
    else:
        print("  CONCLUSION: Evidence of predictive power exists for some signal constructions.")
        print("  Further robustness checks recommended before live deployment.")
    print(f"{'='*100}")


if __name__ == '__main__':
    main()
