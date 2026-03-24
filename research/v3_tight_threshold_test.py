#!/workspace/venv/bin/python
"""
R72: V3 Positioning Threshold Tightening — Walk-Forward Validation
====================================================================

Context:
  R67 parameter sensitivity showed pos_high_thresh=1.2 improves OOS Sharpe
  from 0.556 to 0.708 on the 2025 OOS window. But that was a single-window
  test. This script validates rigorously via walk-forward.

Variants:
  V3-default:  pos_high_thresh=1.5, mid_thresh=0.50  (current production)
  V3-mid:      pos_high_thresh=1.35, mid_thresh=0.45  (intermediate)
  V3-tight:    pos_high_thresh=1.2, mid_thresh=0.40   (proposed change)

  All other parameters held at defaults:
    fast_ema=20, slow_ema=50, pos_z_window=30, vrp_z_window=60, vrp_high_thresh=1.0

Tests:
  1. IS (2020-09 to 2024-12) and OOS (2025-01 to latest) full-period comparison
  2. Walk-forward: 6 windows (18mo IS + 6mo OOS, rolling from 2021 to 2025)
  3. Per-window Sharpe, Return, MaxDD for all 3 variants
  4. Window-level improvement counts
  5. Smoothness check (is intermediate between default and tight?)

KILL criteria:
  - V3-tight walk-forward mean Sharpe < V3-default mean Sharpe -> KEEP defaults
  - V3-tight hurts >2/6 windows -> not robust
"""

import pandas as pd
import numpy as np
import json
import warnings
from pathlib import Path
from datetime import datetime

warnings.filterwarnings('ignore')

BASE_DIR = Path('/workspace/crypto_backtest')
DATA_DIR = BASE_DIR / 'data'
OUTPUT_DIR = BASE_DIR / 'research'

COST_BPS = 10  # round-trip cost in basis points

# Variant configurations: only pos_high_thresh differs
VARIANTS = {
    'V3-default': {'pos_high_thresh': 1.5},   # mid_thresh = 0.50
    'V3-mid':     {'pos_high_thresh': 1.35},   # mid_thresh = 0.45
    'V3-tight':   {'pos_high_thresh': 1.2},    # mid_thresh = 0.40
}

# Shared defaults for all other parameters
SHARED_DEFAULTS = {
    'fast_ema': 20,
    'slow_ema': 50,
    'pos_z_window': 30,
    'vrp_z_window': 60,
    'vrp_high_thresh': 1.0,
}

# Walk-forward windows (same as R67)
WF_WINDOWS = [
    {'is_start': '2021-01-01', 'is_end': '2022-06-30', 'oos_start': '2022-07-01', 'oos_end': '2022-12-31'},
    {'is_start': '2021-07-01', 'is_end': '2022-12-31', 'oos_start': '2023-01-01', 'oos_end': '2023-06-30'},
    {'is_start': '2022-01-01', 'is_end': '2023-06-30', 'oos_start': '2023-07-01', 'oos_end': '2023-12-31'},
    {'is_start': '2022-07-01', 'is_end': '2023-12-31', 'oos_start': '2024-01-01', 'oos_end': '2024-06-30'},
    {'is_start': '2023-01-01', 'is_end': '2024-06-30', 'oos_start': '2024-07-01', 'oos_end': '2024-12-31'},
    {'is_start': '2023-07-01', 'is_end': '2024-12-31', 'oos_start': '2025-01-01', 'oos_end': '2025-06-30'},
]


# ============================================================================
# DATA LOADING (same as R67)
# ============================================================================

def load_btc_daily():
    """Load BTC spot 1h data and resample to daily."""
    btc = pd.read_parquet(DATA_DIR / 'spot/1h_cache/BTC_1h.parquet')
    btc.index = pd.to_datetime(btc.index)
    btc.index.name = 'date'
    daily = btc['close'].resample('D').last().dropna().to_frame('close')
    daily['open'] = btc['open'].resample('D').first()
    daily['high'] = btc['high'].resample('D').max()
    daily['low'] = btc['low'].resample('D').min()
    daily['volume'] = btc['volume'].resample('D').sum()
    return daily


def load_dvol():
    """Load BTC DVOL from Deribit JSON (OHLC daily candles)."""
    dvol_path = DATA_DIR / 'alternative/deribit_options/dvol/btc_dvol_daily.json'
    with open(dvol_path) as f:
        data = json.load(f)
    records = []
    for row in data:
        ts = pd.Timestamp(row[0], unit='ms')
        records.append({'date': ts, 'dvol_close': row[4]})
    dvol = pd.DataFrame(records).set_index('date').sort_index()
    dvol = dvol[~dvol.index.duplicated(keep='last')]
    return dvol['dvol_close']


def load_positioning():
    """Load Binance positioning data for BTCUSDT."""
    pos = pd.read_parquet(DATA_DIR / 'alternative/binance_metrics/all_symbols_daily_ls.parquet')
    pos = pos[pos['symbol'] == 'BTCUSDT'].copy()
    pos['date'] = pd.to_datetime(pos['date'])
    pos = pos.set_index('date').sort_index()
    pos = pos[['sum_toptrader_ls_ratio', 'count_toptrader_ls_ratio', 'count_ls_ratio']].copy()
    pos = pos[~pos.index.duplicated(keep='last')]
    return pos


# ============================================================================
# SIGNAL CONSTRUCTION (same as R67, parameterized)
# ============================================================================

def build_ema_trend_signal(btc_daily, fast_period=20, slow_period=50):
    """V3 base trend: Long when fast EMA > slow EMA, flat otherwise."""
    ema_fast = btc_daily['close'].ewm(span=fast_period, adjust=False).mean()
    ema_slow = btc_daily['close'].ewm(span=slow_period, adjust=False).mean()
    position = (ema_fast > ema_slow).astype(float)
    position.iloc[:slow_period] = 0.0
    return position


def build_positioning_signal(btc_daily, positioning, z_window=30, high_thresh=1.5):
    """
    Positioning overlay with parameterized z-window and high threshold.
    Thresholds: high_thresh, high_thresh/3, -high_thresh/3, -high_thresh
    """
    pos = positioning.reindex(btc_daily.index).ffill()

    def rolling_zscore(s, window):
        mu = s.rolling(window, min_periods=max(15, window // 2)).mean()
        sigma = s.rolling(window, min_periods=max(15, window // 2)).std()
        return (s - mu) / sigma.replace(0, np.nan)

    z_toptrader = rolling_zscore(pos['sum_toptrader_ls_ratio'], z_window)
    divergence = pos['count_toptrader_ls_ratio'] - pos['count_ls_ratio']
    z_divergence = rolling_zscore(divergence, z_window)
    combined_z = (z_toptrader + z_divergence) / 2.0

    mid_thresh = high_thresh / 3.0

    def z_to_multiplier(z):
        if pd.isna(z):
            return 1.0
        if z > high_thresh:
            return 0.3
        elif z > mid_thresh:
            return 0.5
        elif z > -mid_thresh:
            return 1.0
        elif z > -high_thresh:
            return 1.3
        else:
            return 1.5

    pos_multiplier = combined_z.apply(z_to_multiplier)
    return pos_multiplier


def build_vrp_signal(btc_daily, dvol_series, vrp_z_window=60, vrp_high_thresh=1.0):
    """VRP sizing overlay."""
    log_ret = np.log(btc_daily['close'] / btc_daily['close'].shift(1))
    rv_20d = log_ret.rolling(20, min_periods=15).std() * np.sqrt(365) * 100

    if dvol_series.empty or len(dvol_series) < 30:
        iv_proxy = log_ret.rolling(90, min_periods=60).std() * np.sqrt(365) * 100
        iv = iv_proxy * 1.2
    else:
        iv = dvol_series.reindex(btc_daily.index).ffill()

    vrp = iv - rv_20d
    vrp_mu = vrp.rolling(vrp_z_window, min_periods=max(30, vrp_z_window // 2)).mean()
    vrp_sigma = vrp.rolling(vrp_z_window, min_periods=max(30, vrp_z_window // 2)).std()
    vrp_z = (vrp - vrp_mu) / vrp_sigma.replace(0, np.nan)

    mid_low = -vrp_high_thresh / 2.0
    extreme_low = -vrp_high_thresh * 1.5

    def vrp_z_to_multiplier(z):
        if pd.isna(z):
            return 1.0
        if z > vrp_high_thresh:
            return 1.3
        elif z > mid_low:
            return 1.0
        elif z > extreme_low:
            return 0.5
        else:
            return 0.3

    vrp_multiplier = vrp_z.apply(vrp_z_to_multiplier)
    return vrp_multiplier


# ============================================================================
# BACKTEST ENGINE (same as R67)
# ============================================================================

def compute_final_position(base_pos, pos_mult, vrp_mult):
    """Compute final position: base * positioning * vrp, clipped to [0, 1.5]."""
    final = base_pos * pos_mult * vrp_mult
    return final.clip(0, 1.5)


def run_backtest(btc_daily, final_position, cost_bps=COST_BPS):
    """Run backtest with weekly rebalancing and transaction costs."""
    daily_ret = btc_daily['close'].pct_change()

    rebalance_dates = btc_daily.index.to_series().groupby(
        btc_daily.index.to_period('W')
    ).first()
    rebalance_set = set(rebalance_dates.values)

    held_position = pd.Series(0.0, index=btc_daily.index)
    current_pos = 0.0
    costs = pd.Series(0.0, index=btc_daily.index)

    for dt in btc_daily.index:
        if dt in rebalance_set:
            new_pos = final_position.loc[dt]
            if not pd.isna(new_pos):
                pos_change = abs(new_pos - current_pos)
                costs.loc[dt] = pos_change * cost_bps / 10000.0
                current_pos = new_pos
        held_position.loc[dt] = current_pos

    strat_ret = held_position.shift(1) * daily_ret - costs
    return strat_ret


def compute_metrics(returns):
    """Compute Sharpe, annualized return, max drawdown from daily returns."""
    returns = returns.dropna()
    if len(returns) < 30:
        return {'sharpe': np.nan, 'ann_return': np.nan, 'max_dd': np.nan, 'n_days': len(returns)}

    total_ret = (1 + returns).prod() - 1
    n_years = len(returns) / 365
    ann_ret = (1 + total_ret) ** (1 / max(n_years, 0.01)) - 1
    ann_vol = returns.std() * np.sqrt(365)
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0

    cum = (1 + returns).cumprod()
    peak = cum.cummax()
    dd = (cum - peak) / peak
    max_dd = dd.min()

    return {
        'sharpe': sharpe,
        'ann_return': ann_ret,
        'max_dd': max_dd,
        'n_days': len(returns),
    }


# ============================================================================
# STRATEGY RUNNER (parameterized)
# ============================================================================

def run_strategy(btc_daily, positioning, dvol, start_date, end_date,
                 fast_ema=20, slow_ema=50, pos_z_window=30,
                 vrp_z_window=60, pos_high_thresh=1.5, vrp_high_thresh=1.0):
    """Run V3 strategy with given parameters over a date range."""
    base_pos = build_ema_trend_signal(btc_daily, fast_ema, slow_ema)
    pos_mult = build_positioning_signal(btc_daily, positioning, pos_z_window, pos_high_thresh)
    vrp_mult = build_vrp_signal(btc_daily, dvol, vrp_z_window, vrp_high_thresh)

    final_pos = compute_final_position(base_pos, pos_mult, vrp_mult)
    strat_ret = run_backtest(btc_daily, final_pos)

    mask = (btc_daily.index >= start_date) & (btc_daily.index <= end_date)
    period_ret = strat_ret[mask]
    metrics = compute_metrics(period_ret)

    # Buy-and-hold for reference
    bh_ret = btc_daily['close'].pct_change()[mask]
    bh_metrics = compute_metrics(bh_ret)
    metrics['bh_sharpe'] = bh_metrics['sharpe']
    metrics['bh_return'] = bh_metrics['ann_return']

    return metrics


def run_variant(btc_daily, positioning, dvol, start_date, end_date, variant_name):
    """Run a specific variant over a date range."""
    params = SHARED_DEFAULTS.copy()
    params.update(VARIANTS[variant_name])
    return run_strategy(btc_daily, positioning, dvol, start_date, end_date, **params)


# ============================================================================
# DIAGNOSTIC: Position distribution analysis
# ============================================================================

def analyze_position_distribution(btc_daily, positioning, dvol, start_date, end_date):
    """
    Compare how often each variant activates the positioning overlay.
    Returns a dict of {variant_name: {multiplier_value: fraction_of_days}}.
    """
    results = {}
    for variant_name, variant_params in VARIANTS.items():
        params = SHARED_DEFAULTS.copy()
        params.update(variant_params)

        pos_mult = build_positioning_signal(
            btc_daily, positioning, params['pos_z_window'], params['pos_high_thresh']
        )

        mask = (btc_daily.index >= start_date) & (btc_daily.index <= end_date)
        pos_mult_period = pos_mult[mask].dropna()

        if len(pos_mult_period) == 0:
            results[variant_name] = {}
            continue

        # Count days in each multiplier bucket
        counts = {}
        for mult_val in [0.3, 0.5, 1.0, 1.3, 1.5]:
            n = (pos_mult_period == mult_val).sum()
            counts[mult_val] = n / len(pos_mult_period)

        # Also compute mean multiplier
        counts['mean_mult'] = pos_mult_period.mean()
        counts['n_days'] = len(pos_mult_period)
        results[variant_name] = counts

    return results


# ============================================================================
# PART 1: FULL-PERIOD IS/OOS TEST
# ============================================================================

def run_full_period_test(btc_daily, positioning, dvol):
    """Run all variants on IS (2020-09 to 2024-12) and OOS (2025-01 to latest)."""
    print("=" * 72)
    print("PART 1: FULL-PERIOD IS/OOS TEST")
    print("=" * 72)

    is_start, is_end = '2020-09-01', '2024-12-31'
    oos_start = '2025-01-01'
    oos_end = btc_daily.index.max().strftime('%Y-%m-%d')

    print(f"  IS:  {is_start} to {is_end}")
    print(f"  OOS: {oos_start} to {oos_end}")

    results = {}
    for variant_name in VARIANTS:
        is_metrics = run_variant(btc_daily, positioning, dvol, is_start, is_end, variant_name)
        oos_metrics = run_variant(btc_daily, positioning, dvol, oos_start, oos_end, variant_name)
        results[variant_name] = {'is': is_metrics, 'oos': oos_metrics}

        print(f"\n  {variant_name} (pos_high_thresh={VARIANTS[variant_name]['pos_high_thresh']}):")
        print(f"    IS:  Sharpe={is_metrics['sharpe']:+.3f}, Return={is_metrics['ann_return']:+.1%}, MaxDD={is_metrics['max_dd']:.1%}, Days={is_metrics['n_days']}")
        print(f"    OOS: Sharpe={oos_metrics['sharpe']:+.3f}, Return={oos_metrics['ann_return']:+.1%}, MaxDD={oos_metrics['max_dd']:.1%}, Days={oos_metrics['n_days']}")

    # Position distribution diagnostic
    print(f"\n  Position Distribution (OOS period):")
    pos_dist = analyze_position_distribution(btc_daily, positioning, dvol, oos_start, oos_end)
    for variant_name, dist in pos_dist.items():
        if dist:
            print(f"    {variant_name}: "
                  f"0.3x={dist.get(0.3,0):.1%}, "
                  f"0.5x={dist.get(0.5,0):.1%}, "
                  f"1.0x={dist.get(1.0,0):.1%}, "
                  f"1.3x={dist.get(1.3,0):.1%}, "
                  f"1.5x={dist.get(1.5,0):.1%}, "
                  f"mean={dist.get('mean_mult',0):.3f}")

    return results, pos_dist


# ============================================================================
# PART 2: WALK-FORWARD TEST
# ============================================================================

def run_walk_forward(btc_daily, positioning, dvol):
    """Run all 3 variants across 6 walk-forward windows."""
    print("\n" + "=" * 72)
    print("PART 2: WALK-FORWARD TEST (6 windows, 18mo IS + 6mo OOS)")
    print("=" * 72)

    all_results = {v: [] for v in VARIANTS}

    for i, w in enumerate(WF_WINDOWS):
        print(f"\n  Window {i+1}: IS {w['is_start']}..{w['is_end']}, OOS {w['oos_start']}..{w['oos_end']}")

        for variant_name in VARIANTS:
            is_m = run_variant(btc_daily, positioning, dvol, w['is_start'], w['is_end'], variant_name)
            oos_m = run_variant(btc_daily, positioning, dvol, w['oos_start'], w['oos_end'], variant_name)

            all_results[variant_name].append({
                'window': i + 1,
                'is_start': w['is_start'],
                'is_end': w['is_end'],
                'oos_start': w['oos_start'],
                'oos_end': w['oos_end'],
                'is_sharpe': is_m['sharpe'],
                'is_return': is_m['ann_return'],
                'is_maxdd': is_m['max_dd'],
                'oos_sharpe': oos_m['sharpe'],
                'oos_return': oos_m['ann_return'],
                'oos_maxdd': oos_m['max_dd'],
                'oos_bh_sharpe': oos_m['bh_sharpe'],
                'oos_days': oos_m['n_days'],
            })

        # Print comparison for this window
        for variant_name in VARIANTS:
            r = all_results[variant_name][-1]
            thresh = VARIANTS[variant_name]['pos_high_thresh']
            print(f"    {variant_name} (t={thresh}): "
                  f"IS Sharpe={r['is_sharpe']:+.3f}, "
                  f"OOS Sharpe={r['oos_sharpe']:+.3f}, "
                  f"OOS Return={r['oos_return']:+.1%}, "
                  f"OOS MaxDD={r['oos_maxdd']:.1%}")

    # Summary statistics
    print(f"\n  {'=' * 60}")
    print(f"  WALK-FORWARD SUMMARY")
    print(f"  {'=' * 60}")

    summaries = {}
    for variant_name in VARIANTS:
        oos_sharpes = [r['oos_sharpe'] for r in all_results[variant_name]
                       if not np.isnan(r['oos_sharpe'])]
        oos_returns = [r['oos_return'] for r in all_results[variant_name]
                       if not np.isnan(r['oos_return'])]
        oos_maxdds = [r['oos_maxdd'] for r in all_results[variant_name]
                      if not np.isnan(r['oos_maxdd'])]

        summaries[variant_name] = {
            'mean_sharpe': np.mean(oos_sharpes) if oos_sharpes else np.nan,
            'median_sharpe': np.median(oos_sharpes) if oos_sharpes else np.nan,
            'min_sharpe': min(oos_sharpes) if oos_sharpes else np.nan,
            'max_sharpe': max(oos_sharpes) if oos_sharpes else np.nan,
            'std_sharpe': np.std(oos_sharpes) if oos_sharpes else np.nan,
            'n_positive': sum(1 for s in oos_sharpes if s > 0),
            'n_total': len(oos_sharpes),
            'mean_return': np.mean(oos_returns) if oos_returns else np.nan,
            'mean_maxdd': np.mean(oos_maxdds) if oos_maxdds else np.nan,
            'worst_maxdd': min(oos_maxdds) if oos_maxdds else np.nan,
        }

        s = summaries[variant_name]
        thresh = VARIANTS[variant_name]['pos_high_thresh']
        print(f"\n  {variant_name} (t={thresh}):")
        print(f"    Mean OOS Sharpe:   {s['mean_sharpe']:+.3f}")
        print(f"    Median OOS Sharpe: {s['median_sharpe']:+.3f}")
        print(f"    OOS Sharpe range:  {s['min_sharpe']:+.3f} to {s['max_sharpe']:+.3f}")
        print(f"    OOS Sharpe std:    {s['std_sharpe']:.3f}")
        print(f"    Positive windows:  {s['n_positive']}/{s['n_total']}")
        print(f"    Mean OOS Return:   {s['mean_return']:+.1%}")
        print(f"    Worst MaxDD:       {s['worst_maxdd']:.1%}")

    # Per-window comparison: V3-tight vs V3-default
    print(f"\n  PER-WINDOW COMPARISON (V3-tight vs V3-default)")
    print(f"  {'=' * 60}")

    n_tight_wins = 0
    n_tight_hurts = 0
    dsharpe_list = []

    for i in range(len(WF_WINDOWS)):
        default_s = all_results['V3-default'][i]['oos_sharpe']
        tight_s = all_results['V3-tight'][i]['oos_sharpe']
        ds = tight_s - default_s
        dsharpe_list.append(ds)

        marker = ""
        if ds > 0.01:
            n_tight_wins += 1
            marker = " [TIGHT WINS]"
        elif ds < -0.01:
            n_tight_hurts += 1
            marker = " [TIGHT HURTS]"
        else:
            marker = " [~SAME]"

        print(f"    Window {i+1}: default={default_s:+.3f}, tight={tight_s:+.3f}, dSharpe={ds:+.3f}{marker}")

    print(f"\n    V3-tight wins: {n_tight_wins}/6")
    print(f"    V3-tight hurts: {n_tight_hurts}/6")
    print(f"    Mean dSharpe: {np.mean(dsharpe_list):+.3f}")
    print(f"    Median dSharpe: {np.median(dsharpe_list):+.3f}")

    # Also compare V3-mid vs V3-default
    print(f"\n  PER-WINDOW COMPARISON (V3-mid vs V3-default)")
    n_mid_wins = 0
    n_mid_hurts = 0
    mid_dsharpe_list = []

    for i in range(len(WF_WINDOWS)):
        default_s = all_results['V3-default'][i]['oos_sharpe']
        mid_s = all_results['V3-mid'][i]['oos_sharpe']
        ds = mid_s - default_s
        mid_dsharpe_list.append(ds)

        marker = ""
        if ds > 0.01:
            n_mid_wins += 1
            marker = " [MID WINS]"
        elif ds < -0.01:
            n_mid_hurts += 1
            marker = " [MID HURTS]"
        else:
            marker = " [~SAME]"

        print(f"    Window {i+1}: default={default_s:+.3f}, mid={mid_s:+.3f}, dSharpe={ds:+.3f}{marker}")

    print(f"\n    V3-mid wins: {n_mid_wins}/6")
    print(f"    V3-mid hurts: {n_mid_hurts}/6")
    print(f"    Mean dSharpe: {np.mean(mid_dsharpe_list):+.3f}")

    return all_results, summaries, dsharpe_list, mid_dsharpe_list


# ============================================================================
# PART 3: MONOTONICITY / SMOOTHNESS CHECK
# ============================================================================

def run_smoothness_check(summaries, all_wf_results):
    """
    Check if the parameter response is monotonic/smooth.
    Is V3-mid consistently between V3-default and V3-tight?
    """
    print("\n" + "=" * 72)
    print("PART 3: MONOTONICITY / SMOOTHNESS CHECK")
    print("=" * 72)

    # Check walk-forward means
    thresholds = [1.5, 1.35, 1.2]
    variant_names = ['V3-default', 'V3-mid', 'V3-tight']
    mean_sharpes = [summaries[v]['mean_sharpe'] for v in variant_names]

    print(f"\n  Walk-forward mean OOS Sharpe:")
    for t, v, s in zip(thresholds, variant_names, mean_sharpes):
        print(f"    thresh={t:.2f}: {s:+.3f}")

    # Check monotonicity: is it monotonically increasing as threshold decreases?
    is_monotonic = all(mean_sharpes[i] <= mean_sharpes[i+1] for i in range(len(mean_sharpes)-1))
    # Or at least: is V3-mid between V3-default and V3-tight?
    is_mid_between = (
        min(mean_sharpes[0], mean_sharpes[2]) <= mean_sharpes[1] <= max(mean_sharpes[0], mean_sharpes[2])
    )

    print(f"\n  Monotonic (lower thresh -> higher Sharpe)? {is_monotonic}")
    print(f"  V3-mid between V3-default and V3-tight? {is_mid_between}")

    # Per-window monotonicity
    n_monotonic_windows = 0
    n_mid_between_windows = 0
    for i in range(6):
        s_default = all_wf_results['V3-default'][i]['oos_sharpe']
        s_mid = all_wf_results['V3-mid'][i]['oos_sharpe']
        s_tight = all_wf_results['V3-tight'][i]['oos_sharpe']

        mono = (s_default <= s_mid <= s_tight) or (s_default >= s_mid >= s_tight)
        between = min(s_default, s_tight) <= s_mid <= max(s_default, s_tight)

        if mono:
            n_monotonic_windows += 1
        if between:
            n_mid_between_windows += 1

        print(f"    Window {i+1}: default={s_default:+.3f}, mid={s_mid:+.3f}, tight={s_tight:+.3f} "
              f"{'MONOTONIC' if mono else 'non-monotonic'}")

    print(f"\n  Monotonic windows: {n_monotonic_windows}/6")
    print(f"  V3-mid between in: {n_mid_between_windows}/6")

    return {
        'is_monotonic': is_monotonic,
        'is_mid_between': is_mid_between,
        'n_monotonic_windows': n_monotonic_windows,
        'n_mid_between_windows': n_mid_between_windows,
        'mean_sharpes': dict(zip(variant_names, mean_sharpes)),
    }


# ============================================================================
# REPORT GENERATION
# ============================================================================

def generate_report(full_period, pos_dist, all_wf_results, wf_summaries,
                    dsharpe_list, mid_dsharpe_list, smoothness):
    """Generate comprehensive markdown report."""

    lines = []
    lines.append("# R72: V3 Positioning Threshold Tightening -- Walk-Forward Validation")
    lines.append("")
    lines.append(f"**Run date**: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("")
    lines.append("## Context")
    lines.append("")
    lines.append("R67 parameter sensitivity showed that tightening `pos_high_thresh` from 1.5 to 1.2")
    lines.append("improved OOS Sharpe from 0.556 to 0.708 on the 2025 window. This was a single-window")
    lines.append("observation. This study validates the finding via walk-forward analysis across 6 windows.")
    lines.append("")
    lines.append("## Variants Tested")
    lines.append("")
    lines.append("| Variant | pos_high_thresh | mid_thresh | Thresholds |")
    lines.append("|---------|----------------|------------|------------|")
    lines.append("| V3-default | 1.50 | 0.50 | z > 1.5 -> 0.3x, z > 0.5 -> 0.5x, neutral -> 1.0x, z < -0.5 -> 1.3x, z < -1.5 -> 1.5x |")
    lines.append("| V3-mid | 1.35 | 0.45 | z > 1.35 -> 0.3x, z > 0.45 -> 0.5x, neutral -> 1.0x, z < -0.45 -> 1.3x, z < -1.35 -> 1.5x |")
    lines.append("| V3-tight | 1.20 | 0.40 | z > 1.2 -> 0.3x, z > 0.4 -> 0.5x, neutral -> 1.0x, z < -0.4 -> 1.3x, z < -1.2 -> 1.5x |")
    lines.append("")
    lines.append("All other parameters held constant: fast_ema=20, slow_ema=50, pos_z_window=30, vrp_z_window=60, vrp_high_thresh=1.0.")
    lines.append("")

    # ── PART 1: Full-Period ──
    lines.append("## Part 1: Full-Period IS/OOS Test")
    lines.append("")
    lines.append("| Variant | IS Sharpe | IS Return | IS MaxDD | OOS Sharpe | OOS Return | OOS MaxDD |")
    lines.append("|---------|-----------|-----------|----------|------------|------------|-----------|")

    for variant_name in VARIANTS:
        is_m = full_period[variant_name]['is']
        oos_m = full_period[variant_name]['oos']
        lines.append(
            f"| {variant_name} "
            f"| {is_m['sharpe']:+.3f} | {is_m['ann_return']:+.1%} | {is_m['max_dd']:.1%} "
            f"| {oos_m['sharpe']:+.3f} | {oos_m['ann_return']:+.1%} | {oos_m['max_dd']:.1%} |"
        )

    lines.append("")

    # Position distribution
    lines.append("### Positioning Overlay Activation (OOS Period)")
    lines.append("")
    lines.append("| Variant | 0.3x (extreme crowd long) | 0.5x (crowd long) | 1.0x (neutral) | 1.3x (crowd short) | 1.5x (extreme crowd short) | Mean Mult |")
    lines.append("|---------|---------------------------|-------------------|----------------|--------------------|-----------------------------|-----------|")

    for variant_name in VARIANTS:
        d = pos_dist.get(variant_name, {})
        if d:
            lines.append(
                f"| {variant_name} "
                f"| {d.get(0.3,0):.1%} "
                f"| {d.get(0.5,0):.1%} "
                f"| {d.get(1.0,0):.1%} "
                f"| {d.get(1.3,0):.1%} "
                f"| {d.get(1.5,0):.1%} "
                f"| {d.get('mean_mult',0):.3f} |"
            )

    lines.append("")
    lines.append("Tighter thresholds mean the overlay activates more often (fewer days at neutral 1.0x).")
    lines.append("")

    # ── PART 2: Walk-Forward ──
    lines.append("## Part 2: Walk-Forward Test (6 windows)")
    lines.append("")

    # Full results table
    lines.append("### Per-Window OOS Results")
    lines.append("")
    lines.append("| Window | OOS Period | V3-default Sharpe | V3-mid Sharpe | V3-tight Sharpe | dSharpe (tight-default) |")
    lines.append("|--------|------------|-------------------|---------------|-----------------|-------------------------|")

    for i in range(6):
        d = all_wf_results['V3-default'][i]
        m = all_wf_results['V3-mid'][i]
        t = all_wf_results['V3-tight'][i]
        ds = dsharpe_list[i]
        lines.append(
            f"| {i+1} | {d['oos_start']}..{d['oos_end']} "
            f"| {d['oos_sharpe']:+.3f} "
            f"| {m['oos_sharpe']:+.3f} "
            f"| {t['oos_sharpe']:+.3f} "
            f"| {ds:+.3f} |"
        )

    lines.append("")

    # Returns and MaxDD table
    lines.append("### Per-Window OOS Returns & MaxDD")
    lines.append("")
    lines.append("| Window | V3-default Return | V3-tight Return | V3-default MaxDD | V3-tight MaxDD |")
    lines.append("|--------|-------------------|-----------------|------------------|----------------|")

    for i in range(6):
        d = all_wf_results['V3-default'][i]
        t = all_wf_results['V3-tight'][i]
        lines.append(
            f"| {i+1} "
            f"| {d['oos_return']:+.1%} "
            f"| {t['oos_return']:+.1%} "
            f"| {d['oos_maxdd']:.1%} "
            f"| {t['oos_maxdd']:.1%} |"
        )

    lines.append("")

    # Walk-forward summary
    lines.append("### Walk-Forward Summary")
    lines.append("")
    lines.append("| Metric | V3-default | V3-mid | V3-tight |")
    lines.append("|--------|------------|--------|----------|")

    for metric_name, metric_key, fmt in [
        ('Mean OOS Sharpe', 'mean_sharpe', '+.3f'),
        ('Median OOS Sharpe', 'median_sharpe', '+.3f'),
        ('OOS Sharpe Std', 'std_sharpe', '.3f'),
        ('Min OOS Sharpe', 'min_sharpe', '+.3f'),
        ('Max OOS Sharpe', 'max_sharpe', '+.3f'),
        ('Positive Windows', 'n_positive', 'd'),
        ('Mean OOS Return', 'mean_return', '+.1%'),
        ('Worst MaxDD', 'worst_maxdd', '.1%'),
    ]:
        vals = []
        for v in VARIANTS:
            val = wf_summaries[v][metric_key]
            if metric_key == 'n_positive':
                vals.append(f"{val}/{wf_summaries[v]['n_total']}")
            elif '%' in fmt:
                vals.append(f"{val:{fmt}}")
            else:
                vals.append(f"{val:{fmt}}")
        lines.append(f"| {metric_name} | {vals[0]} | {vals[1]} | {vals[2]} |")

    lines.append("")

    # Delta Sharpe analysis
    n_tight_wins = sum(1 for d in dsharpe_list if d > 0.01)
    n_tight_hurts = sum(1 for d in dsharpe_list if d < -0.01)
    n_same = 6 - n_tight_wins - n_tight_hurts

    lines.append("### V3-tight vs V3-default: Per-Window Delta")
    lines.append("")
    lines.append(f"- V3-tight wins (dSharpe > +0.01): **{n_tight_wins}/6**")
    lines.append(f"- V3-tight hurts (dSharpe < -0.01): **{n_tight_hurts}/6**")
    lines.append(f"- Approximately same: **{n_same}/6**")
    lines.append(f"- Mean dSharpe: **{np.mean(dsharpe_list):+.3f}**")
    lines.append(f"- Median dSharpe: **{np.median(dsharpe_list):+.3f}**")
    lines.append(f"- dSharpe range: {min(dsharpe_list):+.3f} to {max(dsharpe_list):+.3f}")
    lines.append("")

    # ── PART 3: Smoothness ──
    lines.append("## Part 3: Monotonicity / Smoothness Check")
    lines.append("")
    lines.append("Is the performance curve smooth as threshold tightens (1.5 -> 1.35 -> 1.2)?")
    lines.append("")
    lines.append(f"- Mean OOS Sharpe monotonically increasing? **{smoothness['is_monotonic']}**")
    lines.append(f"- V3-mid between V3-default and V3-tight? **{smoothness['is_mid_between']}**")
    lines.append(f"- Per-window monotonic: **{smoothness['n_monotonic_windows']}/6**")
    lines.append(f"- Per-window V3-mid between: **{smoothness['n_mid_between_windows']}/6**")
    lines.append("")

    for v, s in smoothness['mean_sharpes'].items():
        lines.append(f"- {v}: mean OOS Sharpe = {s:+.3f}")
    lines.append("")

    if smoothness['is_monotonic']:
        lines.append("The response is monotonic: tighter thresholds consistently improve walk-forward Sharpe. "
                      "This suggests a real effect, not noise.")
    elif smoothness['is_mid_between']:
        lines.append("V3-mid sits between V3-default and V3-tight, suggesting a smooth response curve "
                      "even if not perfectly monotonic at every window.")
    else:
        lines.append("The response is non-monotonic: V3-mid does not sit between the other two. "
                      "This suggests the improvement may be noise or regime-specific.")
    lines.append("")

    # ── KILL CRITERIA ──
    lines.append("## KILL Criteria Evaluation")
    lines.append("")

    tight_mean = wf_summaries['V3-tight']['mean_sharpe']
    default_mean = wf_summaries['V3-default']['mean_sharpe']
    kill1 = tight_mean < default_mean
    kill2 = n_tight_hurts > 2

    lines.append(f"| Criterion | Result | Threshold | Verdict |")
    lines.append(f"|-----------|--------|-----------|---------|")
    lines.append(f"| V3-tight WF mean Sharpe < V3-default? | tight={tight_mean:+.3f} vs default={default_mean:+.3f} | tight < default | {'**KILL**' if kill1 else 'PASS'} |")
    lines.append(f"| V3-tight hurts >2/6 windows? | {n_tight_hurts}/6 hurt | >2 | {'**KILL**' if kill2 else 'PASS'} |")
    lines.append("")

    # ── VERDICT ──
    lines.append("## Verdict")
    lines.append("")

    if kill1 or kill2:
        verdict = "KEEP"
        reasons = []
        if kill1:
            reasons.append(f"walk-forward mean Sharpe worse ({tight_mean:+.3f} vs {default_mean:+.3f})")
        if kill2:
            reasons.append(f"hurts {n_tight_hurts}/6 windows (threshold: >2)")
        lines.append(f"### **KEEP** (stay at pos_high_thresh=1.5)")
        lines.append("")
        lines.append(f"V3-tight fails KILL criteria: {'; '.join(reasons)}.")
        lines.append("")
        lines.append("The R67 finding (Sharpe 0.556 -> 0.708) was a single-window observation on 2025 OOS data. "
                      "Walk-forward validation shows it does not generalize robustly across market regimes.")
    else:
        # Check strength of improvement
        improvement = tight_mean - default_mean
        pct_improvement = improvement / abs(default_mean) * 100 if default_mean != 0 else 0

        if improvement > 0.1 and n_tight_wins >= 4 and smoothness['is_monotonic']:
            verdict = "ADOPT"
            lines.append(f"### **ADOPT** (change default to pos_high_thresh=1.2)")
            lines.append("")
            lines.append(f"V3-tight passes all KILL criteria and shows strong improvement:")
            lines.append(f"- Walk-forward mean Sharpe: {default_mean:+.3f} -> {tight_mean:+.3f} ({pct_improvement:+.1f}%)")
            lines.append(f"- V3-tight wins {n_tight_wins}/6 windows, hurts {n_tight_hurts}/6")
            lines.append(f"- Response curve is {'monotonic' if smoothness['is_monotonic'] else 'smooth'}")
            lines.append(f"- Improvement is robust across market regimes")
        elif improvement > 0.05 and n_tight_wins >= 3:
            verdict = "ADOPT"
            lines.append(f"### **ADOPT** (change default to pos_high_thresh=1.2)")
            lines.append("")
            lines.append(f"V3-tight passes KILL criteria with moderate improvement:")
            lines.append(f"- Walk-forward mean Sharpe: {default_mean:+.3f} -> {tight_mean:+.3f} ({pct_improvement:+.1f}%)")
            lines.append(f"- V3-tight wins {n_tight_wins}/6 windows, hurts {n_tight_hurts}/6")
            lines.append(f"- Monotonic response: {smoothness['is_monotonic']}")
        elif improvement > 0:
            verdict = "ADOPT"
            lines.append(f"### **ADOPT** (change default to pos_high_thresh=1.2)")
            lines.append("")
            lines.append(f"V3-tight passes KILL criteria with marginal improvement:")
            lines.append(f"- Walk-forward mean Sharpe: {default_mean:+.3f} -> {tight_mean:+.3f} ({pct_improvement:+.1f}%)")
            lines.append(f"- V3-tight wins {n_tight_wins}/6 windows, hurts {n_tight_hurts}/6")
            lines.append(f"- Note: improvement is modest. Consider V3-mid (t=1.35) as a conservative alternative.")
        else:
            verdict = "KEEP"
            lines.append(f"### **KEEP** (stay at pos_high_thresh=1.5)")
            lines.append("")
            lines.append(f"V3-tight passes KILL criteria but does not improve walk-forward mean Sharpe:")
            lines.append(f"- Walk-forward mean Sharpe: {default_mean:+.3f} -> {tight_mean:+.3f}")

    lines.append("")
    lines.append("## Appendix: IS Results (overfit detection)")
    lines.append("")
    lines.append("| Window | V3-default IS Sharpe | V3-tight IS Sharpe | IS dSharpe |")
    lines.append("|--------|---------------------|--------------------|-----------|")

    for i in range(6):
        d_is = all_wf_results['V3-default'][i]['is_sharpe']
        t_is = all_wf_results['V3-tight'][i]['is_sharpe']
        lines.append(f"| {i+1} | {d_is:+.3f} | {t_is:+.3f} | {t_is - d_is:+.3f} |")

    lines.append("")

    # IS vs OOS improvement correlation
    is_ds = [all_wf_results['V3-tight'][i]['is_sharpe'] - all_wf_results['V3-default'][i]['is_sharpe']
             for i in range(6)]
    oos_ds = dsharpe_list

    if len(is_ds) >= 3:
        correlation = np.corrcoef(is_ds, oos_ds)[0, 1]
        lines.append(f"IS vs OOS dSharpe correlation: {correlation:+.3f}")
        if abs(correlation) > 0.7:
            lines.append("(Strong correlation: IS improvements predict OOS improvements -- good sign)")
        elif abs(correlation) > 0.3:
            lines.append("(Moderate correlation: IS improvements somewhat predict OOS)")
        else:
            lines.append("(Weak correlation: IS improvements do not predict OOS -- watch for overfit)")
    lines.append("")

    return '\n'.join(lines), verdict


# ============================================================================
# MAIN
# ============================================================================

def main():
    print("=" * 72)
    print("R72: V3 POSITIONING THRESHOLD TIGHTENING -- WALK-FORWARD VALIDATION")
    print(f"Run date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 72)
    print()

    # Load data
    print("Loading data...")
    btc_daily = load_btc_daily()
    dvol = load_dvol()
    positioning = load_positioning()
    print(f"  BTC daily: {btc_daily.index.min().date()} to {btc_daily.index.max().date()}")
    print(f"  DVOL: {dvol.index.min().date()} to {dvol.index.max().date()}")
    print(f"  Positioning: {positioning.index.min().date()} to {positioning.index.max().date()}")
    print()

    # Part 1: Full-period IS/OOS
    full_period, pos_dist = run_full_period_test(btc_daily, positioning, dvol)

    # Part 2: Walk-forward
    all_wf_results, wf_summaries, dsharpe_list, mid_dsharpe_list = run_walk_forward(
        btc_daily, positioning, dvol
    )

    # Part 3: Smoothness check
    smoothness = run_smoothness_check(wf_summaries, all_wf_results)

    # Generate report
    print("\n" + "=" * 72)
    print("GENERATING REPORT")
    print("=" * 72)

    report, verdict = generate_report(
        full_period, pos_dist, all_wf_results, wf_summaries,
        dsharpe_list, mid_dsharpe_list, smoothness,
    )

    report_path = OUTPUT_DIR / 'v3_tight_threshold_results.md'
    with open(report_path, 'w') as f:
        f.write(report)
    print(f"\nReport saved to: {report_path}")

    print(f"\n{'=' * 72}")
    print(f"FINAL VERDICT: {verdict}")
    print(f"{'=' * 72}")


if __name__ == '__main__':
    main()
