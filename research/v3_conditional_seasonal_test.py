#!/workspace/venv/bin/python
"""
V3 Conditional Seasonal Overlay Test
======================================

Tests CONDITIONAL seasonal sizing overlays on the V3 BTC momentum strategy.

Context from R80 (blanket seasonal overlay test):
  - All 4 blanket calendar rules KILLED by PIT OOS criteria
  - BUT: all passed 5/6 walk-forward windows (mean WF dSharpe +0.43 to +1.47)
  - Insight: seasonality is REAL but needs CONDITIONING -- reduce in weak months
    ONLY when other signals also suggest caution

V3 Base Strategy:
  - 20/50 EMA crossover + positioning + VRP overlays
  - Weekly rebalance, spot only, position range [0, 1.5]
  - OOS baseline: Sharpe 0.56, Return +17.52%, MaxDD -20.2%

Conditional Seasonal Variants:
  V1: Weak-Month + Crowded Positioning (pos_z conditioning)
  V2: Weak-Month + Low VRP (vrp_z conditioning)
  V3c: Weak-Month + BOTH Signals Cautious (double conditioning)
  V4: Gradient (softer month_strength * positioning conditioning)

Validation:
  - Full-period IS and OOS separately
  - 6-window walk-forward (18mo IS + 6mo OOS rolling)
  - Per-window metrics table
  - Kill criteria applied per variant

Kill Criteria:
  - WF: if < 4/6 windows improved, KILL
  - If MaxDD worsens in > 2/6 windows, KILL
  - If mean WF dSharpe < +0.05, KILL
  - Must pass BOTH PIT OOS AND WF

Output:
  - research/v3_conditional_seasonal_results.md
"""

import pandas as pd
import numpy as np
import json
import warnings
from pathlib import Path
from datetime import datetime
from scipy import stats as sp_stats

warnings.filterwarnings('ignore')

BASE_DIR = Path('/workspace/crypto_backtest')
DATA_DIR = BASE_DIR / 'data'
OUTPUT_DIR = BASE_DIR / 'research'

COST_BPS = 10  # round-trip cost in basis points

# IS/OOS split dates (consistent with existing V3 validation)
IS_START = '2020-09-01'   # Start of positioning data
IS_END = '2024-12-31'
OOS_START = '2025-01-01'
OOS_END = '2026-03-14'

WEAK_MONTHS = [4, 5, 6, 7, 8, 9]  # Apr through Sep
STRONG_MONTHS = [1, 2, 3, 10, 11, 12]  # Oct through Mar

WALK_FORWARD_WINDOWS = [
    {'is_start': '2021-01-01', 'is_end': '2022-06-30',
     'oos_start': '2022-07-01', 'oos_end': '2022-12-31'},
    {'is_start': '2021-07-01', 'is_end': '2022-12-31',
     'oos_start': '2023-01-01', 'oos_end': '2023-06-30'},
    {'is_start': '2022-01-01', 'is_end': '2023-06-30',
     'oos_start': '2023-07-01', 'oos_end': '2023-12-31'},
    {'is_start': '2022-07-01', 'is_end': '2023-12-31',
     'oos_start': '2024-01-01', 'oos_end': '2024-06-30'},
    {'is_start': '2023-01-01', 'is_end': '2024-06-30',
     'oos_start': '2024-07-01', 'oos_end': '2024-12-31'},
    {'is_start': '2023-07-01', 'is_end': '2024-12-31',
     'oos_start': '2025-01-01', 'oos_end': '2025-06-30'},
]


# ============================================================================
# DATA LOADING (matches v3_seasonal_overlay_test.py pattern)
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
    """Load BTC DVOL from Deribit JSON."""
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
# SIGNAL CONSTRUCTION (V3 base -- matches existing codebase)
# ============================================================================

def rolling_zscore(series, window):
    """Rolling z-score with minimum periods."""
    mu = series.rolling(window, min_periods=max(15, window // 2)).mean()
    sigma = series.rolling(window, min_periods=max(15, window // 2)).std()
    return (series - mu) / sigma.replace(0, np.nan)


def build_ema_trend_signal(btc_daily, fast_period=20, slow_period=50):
    """V3 base trend: long when fast EMA > slow EMA, flat otherwise."""
    ema_fast = btc_daily['close'].ewm(span=fast_period, adjust=False).mean()
    ema_slow = btc_daily['close'].ewm(span=slow_period, adjust=False).mean()
    position = (ema_fast > ema_slow).astype(float)
    position.iloc[:slow_period] = 0.0
    return position


def build_positioning_overlay(btc_daily, positioning, z_window=30, high_thresh=1.5):
    """Positioning overlay: combined z-score -> sizing multiplier.
    Also returns the raw combined z-score for conditional seasonal use."""
    pos = positioning.reindex(btc_daily.index).ffill()

    z_toptrader = rolling_zscore(pos['sum_toptrader_ls_ratio'], z_window)
    divergence = pos['count_toptrader_ls_ratio'] - pos['count_ls_ratio']
    z_divergence = rolling_zscore(divergence, z_window)
    combined_z = (z_toptrader + z_divergence) / 2.0

    mid_thresh = high_thresh / 3.0  # 0.5

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

    multiplier = combined_z.apply(z_to_multiplier)
    return multiplier, combined_z


def build_vrp_overlay(btc_daily, dvol_series, vrp_z_window=60, vrp_high_thresh=1.0):
    """VRP sizing overlay: (IV - RV) z-score -> multiplier.
    Also returns the raw VRP z-score for conditional seasonal use."""
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

    mid_low = -vrp_high_thresh / 2.0   # -0.5
    extreme_low = -vrp_high_thresh * 1.5  # -1.5

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

    multiplier = vrp_z.apply(vrp_z_to_multiplier)
    return multiplier, vrp_z


# ============================================================================
# V3 BASE POSITION (no seasonal overlay)
# ============================================================================

def compute_v3_base_position(btc_daily, positioning, dvol):
    """Build V3 final position = clip(base * pos * vrp, 0, 1.5).
    Returns final position, pos_z, vrp_z for conditional overlays."""
    base = build_ema_trend_signal(btc_daily)
    pos_mult, pos_z = build_positioning_overlay(btc_daily, positioning)
    vrp_mult, vrp_z = build_vrp_overlay(btc_daily, dvol)
    final = (base * pos_mult * vrp_mult).clip(0, 1.5)
    return final, pos_z, vrp_z


# ============================================================================
# CONDITIONAL SEASONAL OVERLAY DEFINITIONS
# ============================================================================

def conditional_seasonal_v1_pos(v3_pos, pos_z, dates):
    """
    V1: Weak-Month + Crowded Positioning.
    In weak months (Apr-Sep):
      if pos_z > 0.5 (crowd forming) -> reduce to 0.3x
      if pos_z neutral (-0.5 to 0.5) -> reduce to 0.7x
      if pos_z < -0.5 (contrarian) -> keep full size (1.0x)
    In strong months (Oct-Mar): no seasonal adjustment.
    """
    seasonal_mult = pd.Series(1.0, index=dates)
    is_weak = dates.month.isin(WEAK_MONTHS)

    for i in range(len(dates)):
        if is_weak[i]:
            pz = pos_z.iloc[i]
            if pd.isna(pz):
                seasonal_mult.iloc[i] = 0.7  # default to mild reduction
            elif pz > 0.5:
                seasonal_mult.iloc[i] = 0.3
            elif pz > -0.5:
                seasonal_mult.iloc[i] = 0.7
            else:
                seasonal_mult.iloc[i] = 1.0

    return (v3_pos * seasonal_mult).clip(0, 1.5)


def conditional_seasonal_v2_vrp(v3_pos, vrp_z, dates):
    """
    V2: Weak-Month + Low VRP.
    In weak months:
      if vrp_z < -0.5 (vol cheap, turbulence) -> reduce to 0.3x
      if vrp_z neutral (-0.5 to 0.5) -> reduce to 0.7x
      if vrp_z > 0.5 (vol overpriced, complacent) -> keep full size (1.0x)
    In strong months: no adjustment.
    """
    seasonal_mult = pd.Series(1.0, index=dates)
    is_weak = dates.month.isin(WEAK_MONTHS)

    for i in range(len(dates)):
        if is_weak[i]:
            vz = vrp_z.iloc[i]
            if pd.isna(vz):
                seasonal_mult.iloc[i] = 0.7
            elif vz < -0.5:
                seasonal_mult.iloc[i] = 0.3
            elif vz < 0.5:
                seasonal_mult.iloc[i] = 0.7
            else:
                seasonal_mult.iloc[i] = 1.0

    return (v3_pos * seasonal_mult).clip(0, 1.5)


def conditional_seasonal_v3c_both(v3_pos, pos_z, vrp_z, dates):
    """
    V3c: Weak-Month + BOTH Signals Cautious.
    In weak months:
      if pos_z > 0.5 AND vrp_z < -0.5 -> 0.0x (go flat)
      if EITHER pos_z > 0.5 OR vrp_z < -0.5 -> 0.5x
      if neither cautious -> 0.8x (mild reduction)
    In strong months: no adjustment.
    """
    seasonal_mult = pd.Series(1.0, index=dates)
    is_weak = dates.month.isin(WEAK_MONTHS)

    for i in range(len(dates)):
        if is_weak[i]:
            pz = pos_z.iloc[i]
            vz = vrp_z.iloc[i]
            pz_cautious = (not pd.isna(pz)) and pz > 0.5
            vz_cautious = (not pd.isna(vz)) and vz < -0.5

            if pz_cautious and vz_cautious:
                seasonal_mult.iloc[i] = 0.0
            elif pz_cautious or vz_cautious:
                seasonal_mult.iloc[i] = 0.5
            else:
                seasonal_mult.iloc[i] = 0.8

    return (v3_pos * seasonal_mult).clip(0, 1.5)


def conditional_seasonal_v4_gradient(v3_pos, pos_z, dates):
    """
    V4: Gradient (softer).
    Define month_strength:
      Oct=1.3, Nov=1.3, Dec=1.1, Jan=1.3, Feb=1.1, Mar=1.0,
      Apr=0.8, May=0.7, Jun=0.8, Jul=0.9, Aug=0.8, Sep=0.7
    In weak months (Apr-Sep): if pos_z > 0.5, multiply month_strength by 0.5
    Apply month_strength as additional multiplier on V3 final position.
    """
    month_strength_map = {
        1: 1.3, 2: 1.1, 3: 1.0,
        4: 0.8, 5: 0.7, 6: 0.8,
        7: 0.9, 8: 0.8, 9: 0.7,
        10: 1.3, 11: 1.3, 12: 1.1,
    }

    seasonal_mult = pd.Series(1.0, index=dates)

    for i in range(len(dates)):
        month = dates[i].month
        ms = month_strength_map[month]

        if month in WEAK_MONTHS:
            pz = pos_z.iloc[i]
            if (not pd.isna(pz)) and pz > 0.5:
                ms = ms * 0.5

        seasonal_mult.iloc[i] = ms

    return (v3_pos * seasonal_mult).clip(0, 1.5)


# ============================================================================
# BACKTEST ENGINE (matches v3_seasonal_overlay_test.py)
# ============================================================================

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
        return {'sharpe': np.nan, 'ann_return': np.nan, 'max_dd': np.nan,
                'n_days': len(returns), 'total_return': np.nan, 'ann_vol': np.nan,
                'calmar': np.nan}

    total_ret = (1 + returns).prod() - 1
    n_years = len(returns) / 365
    ann_ret = (1 + total_ret) ** (1 / max(n_years, 0.01)) - 1
    ann_vol = returns.std() * np.sqrt(365)
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0

    cum = (1 + returns).cumprod()
    peak = cum.cummax()
    dd = (cum - peak) / peak
    max_dd = dd.min()

    calmar = ann_ret / abs(max_dd) if max_dd != 0 else np.nan

    return {
        'sharpe': sharpe,
        'ann_return': ann_ret,
        'max_dd': max_dd,
        'total_return': total_ret,
        'ann_vol': ann_vol,
        'calmar': calmar,
        'n_days': len(returns),
    }


# ============================================================================
# STRATEGY RUNNERS
# ============================================================================

def run_v3_base(btc_daily, positioning, dvol, start_date, end_date):
    """Run V3 base strategy over a date range."""
    final_pos, pos_z, vrp_z = compute_v3_base_position(btc_daily, positioning, dvol)
    strat_ret = run_backtest(btc_daily, final_pos)
    mask = (btc_daily.index >= start_date) & (btc_daily.index <= end_date)
    return compute_metrics(strat_ret[mask]), strat_ret[mask], pos_z, vrp_z


def run_conditional_seasonal(btc_daily, positioning, dvol, start_date, end_date,
                              variant_name):
    """Run V3 with a conditional seasonal overlay."""
    v3_pos, pos_z, vrp_z = compute_v3_base_position(btc_daily, positioning, dvol)

    if variant_name == 'V1_pos_conditioning':
        final_pos = conditional_seasonal_v1_pos(v3_pos, pos_z, btc_daily.index)
    elif variant_name == 'V2_vrp_conditioning':
        final_pos = conditional_seasonal_v2_vrp(v3_pos, vrp_z, btc_daily.index)
    elif variant_name == 'V3c_both_conditioning':
        final_pos = conditional_seasonal_v3c_both(v3_pos, pos_z, vrp_z, btc_daily.index)
    elif variant_name == 'V4_gradient':
        final_pos = conditional_seasonal_v4_gradient(v3_pos, pos_z, btc_daily.index)
    else:
        raise ValueError(f"Unknown variant: {variant_name}")

    strat_ret = run_backtest(btc_daily, final_pos)
    mask = (btc_daily.index >= start_date) & (btc_daily.index <= end_date)
    return compute_metrics(strat_ret[mask]), strat_ret[mask]


CONDITIONAL_VARIANTS = [
    'V1_pos_conditioning',
    'V2_vrp_conditioning',
    'V3c_both_conditioning',
    'V4_gradient',
]


# ============================================================================
# WALK-FORWARD TEST
# ============================================================================

def run_walk_forward(btc_daily, positioning, dvol):
    """Run 6-window walk-forward for base and all conditional seasonal variants."""
    all_results = {}

    # Base V3
    print("\n" + "=" * 72)
    print("WALK-FORWARD: V3 Base (no seasonal overlay)")
    print("=" * 72)
    base_wf = []
    for i, w in enumerate(WALK_FORWARD_WINDOWS):
        is_m, _, _, _ = run_v3_base(btc_daily, positioning, dvol, w['is_start'], w['is_end'])
        oos_m, _, _, _ = run_v3_base(btc_daily, positioning, dvol, w['oos_start'], w['oos_end'])
        base_wf.append({
            'window': i + 1,
            'oos_start': w['oos_start'], 'oos_end': w['oos_end'],
            'is_sharpe': is_m['sharpe'], 'is_return': is_m['ann_return'],
            'is_maxdd': is_m['max_dd'],
            'oos_sharpe': oos_m['sharpe'], 'oos_return': oos_m['ann_return'],
            'oos_maxdd': oos_m['max_dd'],
        })
        print(f"  W{i+1} OOS {w['oos_start']}-{w['oos_end']}: "
              f"Sharpe={oos_m['sharpe']:+.3f}, Ret={oos_m['ann_return']:+.1%}, "
              f"MaxDD={oos_m['max_dd']:.1%}")
    all_results['V3_base'] = base_wf

    # Each conditional variant
    for variant_name in CONDITIONAL_VARIANTS:
        print(f"\n{'=' * 72}")
        print(f"WALK-FORWARD: {variant_name}")
        print("=" * 72)
        var_wf = []
        for i, w in enumerate(WALK_FORWARD_WINDOWS):
            is_m, _ = run_conditional_seasonal(btc_daily, positioning, dvol,
                                                w['is_start'], w['is_end'], variant_name)
            oos_m, _ = run_conditional_seasonal(btc_daily, positioning, dvol,
                                                 w['oos_start'], w['oos_end'], variant_name)
            var_wf.append({
                'window': i + 1,
                'oos_start': w['oos_start'], 'oos_end': w['oos_end'],
                'is_sharpe': is_m['sharpe'], 'is_return': is_m['ann_return'],
                'is_maxdd': is_m['max_dd'],
                'oos_sharpe': oos_m['sharpe'], 'oos_return': oos_m['ann_return'],
                'oos_maxdd': oos_m['max_dd'],
            })
            print(f"  W{i+1} OOS {w['oos_start']}-{w['oos_end']}: "
                  f"Sharpe={oos_m['sharpe']:+.3f}, Ret={oos_m['ann_return']:+.1%}, "
                  f"MaxDD={oos_m['max_dd']:.1%}")
        all_results[variant_name] = var_wf

    return all_results


# ============================================================================
# KILL CRITERIA (STRICT -- from task spec)
# ============================================================================

def evaluate_kill_criteria(variant_name, base_wf, variant_wf,
                            base_pit_oos, var_pit_oos):
    """
    Apply STRICT kill criteria to each conditional seasonal variant.

    Kill Criteria (from task spec):
    1. WF: if < 4/6 windows show Sharpe improvement -> KILL
    2. If MaxDD worsens in > 2/6 windows -> KILL
    3. If mean WF dSharpe < +0.05 -> KILL (not worth the complexity)
    4. Must pass BOTH PIT OOS AND WF

    Returns (verdict, reasons, stats) tuple.
    """
    reasons = []
    kill = False

    # Criterion 1: Walk-forward window improvement count (>= 4/6)
    improved_windows = 0
    for base_w, var_w in zip(base_wf, variant_wf):
        if var_w['oos_sharpe'] > base_w['oos_sharpe']:
            improved_windows += 1
    if improved_windows < 4:
        kill = True
        reasons.append(f"WF KILL: only {improved_windows}/6 windows improve (need >=4)")
    else:
        reasons.append(f"WF PASS: {improved_windows}/6 windows improve")

    # Criterion 2: MaxDD worsens in > 2/6 windows
    dd_worsened_windows = 0
    for base_w, var_w in zip(base_wf, variant_wf):
        if var_w['oos_maxdd'] < base_w['oos_maxdd']:  # more negative = worse
            dd_worsened_windows += 1
    if dd_worsened_windows > 2:
        kill = True
        reasons.append(f"MaxDD KILL: worsens in {dd_worsened_windows}/6 windows (max 2)")
    else:
        reasons.append(f"MaxDD PASS: worsens in {dd_worsened_windows}/6 windows")

    # Criterion 3: Mean WF dSharpe >= +0.05
    mean_wf_dsharpe = np.mean([
        var_w['oos_sharpe'] - base_w['oos_sharpe']
        for base_w, var_w in zip(base_wf, variant_wf)
    ])
    if mean_wf_dsharpe < 0.05:
        kill = True
        reasons.append(f"Mean dSharpe KILL: {mean_wf_dsharpe:+.3f} (need >=+0.050)")
    else:
        reasons.append(f"Mean dSharpe PASS: {mean_wf_dsharpe:+.3f}")

    # Criterion 4: PIT OOS must also be positive (Sharpe >= base Sharpe)
    pit_oos_dsharpe = var_pit_oos['sharpe'] - base_pit_oos['sharpe']
    if pit_oos_dsharpe < 0:
        kill = True
        reasons.append(f"PIT OOS KILL: dSharpe={pit_oos_dsharpe:+.3f} (must be >=0)")
    else:
        reasons.append(f"PIT OOS PASS: dSharpe={pit_oos_dsharpe:+.3f}")

    verdict = "KILL" if kill else "PASS"

    stats = {
        'improved_windows': improved_windows,
        'dd_worsened_windows': dd_worsened_windows,
        'mean_wf_dsharpe': mean_wf_dsharpe,
        'pit_oos_dsharpe': pit_oos_dsharpe,
    }

    return verdict, reasons, stats


# ============================================================================
# STATISTICAL SIGNIFICANCE
# ============================================================================

def compute_statistical_significance(base_returns, variant_returns):
    """Paired t-test on daily return differences."""
    common = base_returns.index.intersection(variant_returns.index)
    base_r = base_returns.loc[common].dropna()
    var_r = variant_returns.loc[common].dropna()
    common2 = base_r.index.intersection(var_r.index)
    if len(common2) < 30:
        return {'t_stat': np.nan, 'p_value': np.nan, 'n': len(common2)}

    diff = var_r.loc[common2] - base_r.loc[common2]
    t_stat = diff.mean() / (diff.std() / np.sqrt(len(diff)))
    p_value = 2 * (1 - sp_stats.t.cdf(abs(t_stat), df=len(diff) - 1))
    return {'t_stat': t_stat, 'p_value': p_value, 'n': len(diff)}


# ============================================================================
# DIAGNOSTIC: SEASONAL ACTIVATION STATS
# ============================================================================

def compute_activation_stats(btc_daily, positioning, dvol):
    """How often does each conditional overlay actually activate?"""
    v3_pos, pos_z, vrp_z = compute_v3_base_position(btc_daily, positioning, dvol)
    dates = btc_daily.index
    is_weak = dates.month.isin(WEAK_MONTHS)

    total_days = len(dates)
    weak_days = is_weak.sum()

    stats = {}

    # V1: pos conditioning
    pos_cautious = (pos_z > 0.5) & is_weak
    pos_neutral = (pos_z >= -0.5) & (pos_z <= 0.5) & is_weak
    pos_contrarian = (pos_z < -0.5) & is_weak
    stats['V1_pos_conditioning'] = {
        'weak_days': int(weak_days),
        'cautious_days': int(pos_cautious.sum()),
        'neutral_days': int(pos_neutral.sum()),
        'contrarian_days': int(pos_contrarian.sum()),
        'pct_full_reduction': f"{100*pos_cautious.sum()/max(weak_days,1):.1f}%",
        'pct_mild_reduction': f"{100*pos_neutral.sum()/max(weak_days,1):.1f}%",
        'pct_no_reduction': f"{100*pos_contrarian.sum()/max(weak_days,1):.1f}%",
    }

    # V2: vrp conditioning
    vrp_cautious = (vrp_z < -0.5) & is_weak
    vrp_neutral = (vrp_z >= -0.5) & (vrp_z <= 0.5) & is_weak
    vrp_complacent = (vrp_z > 0.5) & is_weak
    stats['V2_vrp_conditioning'] = {
        'weak_days': int(weak_days),
        'cautious_days': int(vrp_cautious.sum()),
        'neutral_days': int(vrp_neutral.sum()),
        'complacent_days': int(vrp_complacent.sum()),
        'pct_full_reduction': f"{100*vrp_cautious.sum()/max(weak_days,1):.1f}%",
        'pct_mild_reduction': f"{100*vrp_neutral.sum()/max(weak_days,1):.1f}%",
        'pct_no_reduction': f"{100*vrp_complacent.sum()/max(weak_days,1):.1f}%",
    }

    # V3c: both
    both_cautious = (pos_z > 0.5) & (vrp_z < -0.5) & is_weak
    either_cautious = ((pos_z > 0.5) | (vrp_z < -0.5)) & is_weak & ~both_cautious
    neither_cautious = is_weak & ~((pos_z > 0.5) | (vrp_z < -0.5))
    stats['V3c_both_conditioning'] = {
        'weak_days': int(weak_days),
        'both_cautious_days': int(both_cautious.sum()),
        'either_cautious_days': int(either_cautious.sum()),
        'neither_cautious_days': int(neither_cautious.sum()),
        'pct_go_flat': f"{100*both_cautious.sum()/max(weak_days,1):.1f}%",
        'pct_half_reduction': f"{100*either_cautious.sum()/max(weak_days,1):.1f}%",
        'pct_mild_reduction': f"{100*neither_cautious.sum()/max(weak_days,1):.1f}%",
    }

    # V4: gradient -- compute average multiplier in weak vs strong months
    month_strength_map = {
        1: 1.3, 2: 1.1, 3: 1.0,
        4: 0.8, 5: 0.7, 6: 0.8,
        7: 0.9, 8: 0.8, 9: 0.7,
        10: 1.3, 11: 1.3, 12: 1.1,
    }
    ms_series = pd.Series([month_strength_map[d.month] for d in dates], index=dates)
    # Adjust for pos_z > 0.5 in weak months
    pos_crowd_weak = (pos_z > 0.5) & is_weak
    ms_adjusted = ms_series.copy()
    ms_adjusted[pos_crowd_weak] = ms_adjusted[pos_crowd_weak] * 0.5

    stats['V4_gradient'] = {
        'avg_mult_strong': f"{ms_adjusted[~is_weak].mean():.3f}",
        'avg_mult_weak': f"{ms_adjusted[is_weak].mean():.3f}",
        'avg_mult_weak_crowd': f"{ms_adjusted[pos_crowd_weak].mean():.3f}" if pos_crowd_weak.sum() > 0 else "N/A",
        'days_crowd_reduced': int(pos_crowd_weak.sum()),
    }

    return stats


# ============================================================================
# REPORT GENERATION
# ============================================================================

def generate_report(btc_daily, positioning, dvol, wf_results,
                     pit_results, kill_verdicts, activation_stats, sig_results):
    """Generate markdown report."""

    lines = []
    lines.append("# V3 Conditional Seasonal Overlay Test Results")
    lines.append("")
    lines.append(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"**Data range:** {btc_daily.index.min().date()} to {btc_daily.index.max().date()}")
    lines.append(f"**IS period:** {IS_START} to {IS_END}")
    lines.append(f"**OOS period:** {OOS_START} to {OOS_END}")
    lines.append(f"**Transaction cost:** {COST_BPS} bps round-trip")
    lines.append(f"**Rebalance:** Weekly")
    lines.append("")

    # ── Context
    lines.append("## Context")
    lines.append("")
    lines.append("Previous research (R80) found that **blanket calendar rules** (reduce in Apr-Sep)")
    lines.append("fail PIT OOS but pass 5/6 walk-forward windows. The insight: seasonality is REAL")
    lines.append("but needs to be **CONDITIONAL** -- reduce in weak months ONLY when other signals")
    lines.append("also suggest caution.")
    lines.append("")
    lines.append("This test applies conditional seasonal overlays that combine calendar weakness")
    lines.append("with signal confirmation from positioning z-score and/or VRP z-score.")
    lines.append("")

    # ── Executive Summary
    lines.append("## Executive Summary")
    lines.append("")
    n_pass = sum(1 for v in kill_verdicts.values() if v[0] == 'PASS')
    n_kill = sum(1 for v in kill_verdicts.values() if v[0] == 'KILL')
    lines.append(f"**{n_pass} PASS / {n_kill} KILL** out of 4 conditional seasonal variants tested.")
    lines.append("")
    for variant, (verdict, reasons, stats) in kill_verdicts.items():
        marker = "PASS" if verdict == "PASS" else "KILL"
        lines.append(f"- **{variant}**: **{marker}** -- {reasons[0]}")
    lines.append("")

    # ── Variant Descriptions
    lines.append("## Variant Descriptions")
    lines.append("")
    lines.append("All variants apply a seasonal multiplier ONLY during weak months (Apr-Sep).")
    lines.append("Strong months (Oct-Mar) are always 1.0x (no adjustment).")
    lines.append("")
    lines.append("| Variant | Conditioning Signal | Weak-Month Logic |")
    lines.append("|---------|--------------------|--------------------|")
    lines.append("| V1 | Positioning z-score | pos_z>0.5: 0.3x, neutral: 0.7x, pos_z<-0.5: 1.0x |")
    lines.append("| V2 | VRP z-score | vrp_z<-0.5: 0.3x, neutral: 0.7x, vrp_z>0.5: 1.0x |")
    lines.append("| V3c | Both signals | Both cautious: 0.0x, either: 0.5x, neither: 0.8x |")
    lines.append("| V4 | Gradient + Positioning | Month-strength * (0.5 if pos_z>0.5 in weak month) |")
    lines.append("")

    # ── Activation Statistics
    lines.append("## Conditional Activation Statistics")
    lines.append("")
    lines.append("How often does each overlay actually reduce position in weak months?")
    lines.append("")

    for variant, astats in activation_stats.items():
        lines.append(f"### {variant}")
        lines.append("")
        for k, v in astats.items():
            lines.append(f"- {k}: {v}")
        lines.append("")

    # ── Point-in-Time Results
    lines.append("## Point-in-Time Results (IS + OOS)")
    lines.append("")
    lines.append("| Variant | IS Sharpe | IS Return | IS MaxDD | OOS Sharpe | OOS Return | OOS MaxDD | dSharpe OOS |")
    lines.append("|---------|-----------|-----------|----------|------------|------------|-----------|-------------|")
    base_oos = pit_results['V3_base']['oos']
    for variant, periods in pit_results.items():
        is_m = periods['is']
        oos_m = periods['oos']
        dsharpe = oos_m['sharpe'] - base_oos['sharpe'] if variant != 'V3_base' else 0.0
        lines.append(f"| {variant} | {is_m['sharpe']:+.3f} | {is_m['ann_return']:+.1%} | "
                      f"{is_m['max_dd']:.1%} | {oos_m['sharpe']:+.3f} | {oos_m['ann_return']:+.1%} | "
                      f"{oos_m['max_dd']:.1%} | {dsharpe:+.3f} |")
    lines.append("")

    # ── Walk-Forward Tables
    lines.append("## Walk-Forward Results (6 windows, 18mo IS + 6mo OOS)")
    lines.append("")

    base_wf = wf_results['V3_base']

    for variant_name in ['V3_base'] + CONDITIONAL_VARIANTS:
        var_wf = wf_results[variant_name]
        lines.append(f"### {variant_name}")
        lines.append("")
        lines.append("| Window | OOS Period | IS Sharpe | OOS Sharpe | OOS Return | OOS MaxDD | dSharpe vs Base |")
        lines.append("|--------|-----------|-----------|------------|------------|-----------|-----------------|")

        for j, w in enumerate(var_wf):
            base_w = base_wf[j]
            dsharpe = w['oos_sharpe'] - base_w['oos_sharpe']
            marker = "+" if dsharpe > 0 else ""
            lines.append(f"| W{w['window']} | {w['oos_start']}..{w['oos_end']} | "
                          f"{w['is_sharpe']:+.3f} | {w['oos_sharpe']:+.3f} | "
                          f"{w['oos_return']:+.1%} | {w['oos_maxdd']:.1%} | "
                          f"{marker}{dsharpe:.3f} |")

        # Summary row
        mean_oos_sharpe = np.mean([w['oos_sharpe'] for w in var_wf])
        mean_dsharpe = np.mean([
            var_wf[j]['oos_sharpe'] - base_wf[j]['oos_sharpe']
            for j in range(len(var_wf))
        ])
        improved_windows = sum(
            1 for j in range(len(var_wf))
            if var_wf[j]['oos_sharpe'] > base_wf[j]['oos_sharpe']
        )
        dd_worsened = sum(
            1 for j in range(len(var_wf))
            if var_wf[j]['oos_maxdd'] < base_wf[j]['oos_maxdd']
        )
        lines.append(f"| **Mean** | | | **{mean_oos_sharpe:+.3f}** | | | "
                      f"**{mean_dsharpe:+.3f}** |")
        lines.append(f"| **Summary** | | | | | | "
                      f"{improved_windows}/6 improved, {dd_worsened}/6 DD worse |")
        lines.append("")

    # ── Kill Criteria Detail
    lines.append("## Kill Criteria Evaluation")
    lines.append("")
    lines.append("Criteria (all must PASS for variant to survive):")
    lines.append("1. **WF Windows**: >= 4/6 windows show Sharpe improvement over base V3")
    lines.append("2. **MaxDD Windows**: MaxDD worsens in <= 2/6 windows")
    lines.append("3. **Mean WF dSharpe**: >= +0.05 (worth the complexity)")
    lines.append("4. **PIT OOS**: dSharpe >= 0 (seasonal overlay must pass BOTH PIT and WF)")
    lines.append("")

    for variant, (verdict, reasons, stats) in kill_verdicts.items():
        lines.append(f"### {variant}: **{verdict}**")
        lines.append("")
        for r in reasons:
            lines.append(f"- {r}")
        lines.append("")

    # ── Statistical Significance
    lines.append("## Statistical Significance (OOS period)")
    lines.append("")
    lines.append("| Variant | t-stat | p-value | N days | Significant? |")
    lines.append("|---------|--------|---------|--------|-------------|")
    for variant, sig in sig_results.items():
        sig_label = "Yes (p<0.05)" if sig['p_value'] < 0.05 else \
            "Marginal (p<0.10)" if sig['p_value'] < 0.10 else "No"
        lines.append(f"| {variant} | {sig['t_stat']:+.3f} | {sig['p_value']:.4f} | "
                      f"{sig['n']} | {sig_label} |")
    lines.append("")

    # ── Comparison: Conditional vs Blanket
    lines.append("## Comparison: Conditional vs Blanket Seasonal Overlays")
    lines.append("")
    lines.append("From R80 (blanket seasonal), all 4 variants were KILLED by PIT OOS but")
    lines.append("showed 5/6 WF windows improved. How do conditional variants compare?")
    lines.append("")
    lines.append("| Metric | Blanket V1 (binary) | Blanket V4 (sell-in-may) | Conditional V1 (pos) | Conditional V3c (both) |")
    lines.append("|--------|--------------------|-----------------------|---------------------|-----------------------|")

    # Reference blanket results from R80
    blanket_v1_wf_dsharpe = 1.469
    blanket_v4_wf_dsharpe = 0.427
    blanket_v1_pit_dsharpe = -0.951
    blanket_v4_pit_dsharpe = -0.297

    v1_stats = kill_verdicts.get('V1_pos_conditioning', ('', [], {}))[2]
    v3c_stats = kill_verdicts.get('V3c_both_conditioning', ('', [], {}))[2]

    lines.append(f"| WF Mean dSharpe | {blanket_v1_wf_dsharpe:+.3f} | {blanket_v4_wf_dsharpe:+.3f} | "
                  f"{v1_stats.get('mean_wf_dsharpe', 0):+.3f} | {v3c_stats.get('mean_wf_dsharpe', 0):+.3f} |")
    lines.append(f"| PIT OOS dSharpe | {blanket_v1_pit_dsharpe:+.3f} | {blanket_v4_pit_dsharpe:+.3f} | "
                  f"{v1_stats.get('pit_oos_dsharpe', 0):+.3f} | {v3c_stats.get('pit_oos_dsharpe', 0):+.3f} |")
    lines.append(f"| WF Windows Improved | 5/6 | 5/6 | "
                  f"{v1_stats.get('improved_windows', 0)}/6 | {v3c_stats.get('improved_windows', 0)}/6 |")
    lines.append("")

    # ── Conclusion
    lines.append("## Conclusion")
    lines.append("")

    passing = [k for k, v in kill_verdicts.items() if v[0] == 'PASS']
    killed = [k for k, v in kill_verdicts.items() if v[0] == 'KILL']

    if n_pass == 0:
        lines.append("**No conditional seasonal variant passes all kill criteria.**")
        lines.append("")
        lines.append("Despite conditioning on positioning and/or VRP signals, the seasonal")
        lines.append("overlay still fails to improve V3 when required to pass BOTH PIT OOS AND")
        lines.append("walk-forward validation simultaneously.")
        lines.append("")
        # Analyze the pattern
        best_variant = min(kill_verdicts.items(), key=lambda x: -x[1][2]['mean_wf_dsharpe'])
        bv_name, (bv_verdict, bv_reasons, bv_stats) = best_variant
        lines.append(f"Best conditional variant: **{bv_name}** "
                      f"(WF dSharpe={bv_stats['mean_wf_dsharpe']:+.3f}, "
                      f"PIT OOS dSharpe={bv_stats['pit_oos_dsharpe']:+.3f}, "
                      f"{bv_stats['improved_windows']}/6 WF windows improved)")
        lines.append("")
        lines.append("**Recommendation:** Seasonal conditioning does not add sufficient value")
        lines.append("to justify the complexity. The base V3 strategy with positioning + VRP")
        lines.append("overlays remains the production configuration.")

    elif n_pass >= 1:
        lines.append(f"**{n_pass} variant(s) pass all kill criteria: {', '.join(passing)}**")
        lines.append("")
        for pv in passing:
            pv_verdict, pv_reasons, pv_stats = kill_verdicts[pv]
            lines.append(f"### {pv}")
            lines.append(f"- WF: {pv_stats['improved_windows']}/6 windows improved, "
                          f"mean dSharpe={pv_stats['mean_wf_dsharpe']:+.3f}")
            lines.append(f"- PIT OOS: dSharpe={pv_stats['pit_oos_dsharpe']:+.3f}")
            lines.append(f"- MaxDD worsened in {pv_stats['dd_worsened_windows']}/6 windows")
            sig = sig_results.get(pv, {})
            lines.append(f"- Statistical significance: t={sig.get('t_stat', np.nan):+.3f}, "
                          f"p={sig.get('p_value', np.nan):.4f}")
            lines.append("")
        lines.append("**Recommendation:** Consider adopting the passing variant(s) as a")
        lines.append("production overlay on V3. The conditional approach successfully filters")
        lines.append("out periods where blanket seasonal reduction would have hurt.")

    lines.append("")

    return "\n".join(lines)


# ============================================================================
# MAIN
# ============================================================================

def main():
    print("=" * 72)
    print("V3 CONDITIONAL SEASONAL OVERLAY TEST")
    print("Testing conditional seasonal sizing: reduce in weak months")
    print("ONLY when positioning/VRP signals confirm caution")
    print("=" * 72)
    print()

    # Load data
    print("Loading data...")
    btc_daily = load_btc_daily()
    dvol = load_dvol()
    positioning = load_positioning()
    print(f"  BTC daily: {btc_daily.index.min().date()} to {btc_daily.index.max().date()} "
          f"({len(btc_daily)} bars)")
    print(f"  DVOL: {dvol.index.min().date()} to {dvol.index.max().date()} "
          f"({len(dvol)} records)")
    print(f"  Positioning: {positioning.index.min().date()} to {positioning.index.max().date()} "
          f"({len(positioning)} records)")
    print()

    # ── Activation statistics (diagnostic)
    print("=" * 72)
    print("ACTIVATION STATISTICS")
    print("=" * 72)
    activation_stats = compute_activation_stats(btc_daily, positioning, dvol)
    for variant, astats in activation_stats.items():
        print(f"\n  {variant}:")
        for k, v in astats.items():
            print(f"    {k}: {v}")

    # ── Point-in-time IS/OOS
    print("\n" + "=" * 72)
    print("POINT-IN-TIME RESULTS")
    print("=" * 72)
    pit_results = {}

    # Base V3
    is_m, is_ret, _, _ = run_v3_base(btc_daily, positioning, dvol, IS_START, IS_END)
    oos_m, oos_ret, _, _ = run_v3_base(btc_daily, positioning, dvol, OOS_START, OOS_END)
    pit_results['V3_base'] = {'is': is_m, 'oos': oos_m}
    base_oos_ret = oos_ret
    print(f"\n  V3_base:")
    print(f"    IS:  Sharpe={is_m['sharpe']:+.3f}, Return={is_m['ann_return']:+.1%}, MaxDD={is_m['max_dd']:.1%}")
    print(f"    OOS: Sharpe={oos_m['sharpe']:+.3f}, Return={oos_m['ann_return']:+.1%}, MaxDD={oos_m['max_dd']:.1%}")

    # Conditional seasonal variants
    sig_results = {}
    for variant_name in CONDITIONAL_VARIANTS:
        is_m, is_ret_v = run_conditional_seasonal(btc_daily, positioning, dvol,
                                                    IS_START, IS_END, variant_name)
        oos_m, oos_ret_v = run_conditional_seasonal(btc_daily, positioning, dvol,
                                                      OOS_START, OOS_END, variant_name)
        pit_results[variant_name] = {'is': is_m, 'oos': oos_m}
        print(f"\n  {variant_name}:")
        print(f"    IS:  Sharpe={is_m['sharpe']:+.3f}, Return={is_m['ann_return']:+.1%}, MaxDD={is_m['max_dd']:.1%}")
        print(f"    OOS: Sharpe={oos_m['sharpe']:+.3f}, Return={oos_m['ann_return']:+.1%}, MaxDD={oos_m['max_dd']:.1%}")

        # Statistical significance
        sig = compute_statistical_significance(base_oos_ret, oos_ret_v)
        sig_results[variant_name] = sig
        sig_label = f"t={sig['t_stat']:+.3f}, p={sig['p_value']:.4f}" if not np.isnan(sig['t_stat']) else "N/A"
        print(f"    Sig vs base: {sig_label}")

    # ── Walk-forward
    wf_results = run_walk_forward(btc_daily, positioning, dvol)

    # ── Kill criteria evaluation
    print("\n" + "=" * 72)
    print("KILL CRITERIA EVALUATION")
    print("=" * 72)
    kill_verdicts = {}
    base_wf = wf_results['V3_base']

    for variant_name in CONDITIONAL_VARIANTS:
        verdict, reasons, stats = evaluate_kill_criteria(
            variant_name,
            base_wf,
            wf_results[variant_name],
            pit_results['V3_base']['oos'],
            pit_results[variant_name]['oos'],
        )
        kill_verdicts[variant_name] = (verdict, reasons, stats)
        print(f"\n  {variant_name}: ** {verdict} **")
        for r in reasons:
            print(f"    - {r}")

    # ── Generate report
    print("\n" + "=" * 72)
    print("GENERATING REPORT")
    print("=" * 72)
    report = generate_report(btc_daily, positioning, dvol, wf_results,
                              pit_results, kill_verdicts, activation_stats, sig_results)

    report_path = OUTPUT_DIR / 'v3_conditional_seasonal_results.md'
    with open(report_path, 'w') as f:
        f.write(report)
    print(f"  Report written to: {report_path}")

    # ── Final summary
    print("\n" + "=" * 72)
    print("FINAL SUMMARY")
    print("=" * 72)
    n_pass = sum(1 for v in kill_verdicts.values() if v[0] == 'PASS')
    n_kill = sum(1 for v in kill_verdicts.values() if v[0] == 'KILL')
    print(f"  {n_pass} PASS / {n_kill} KILL out of 4 conditional seasonal variants")
    for variant, (verdict, reasons, stats) in kill_verdicts.items():
        print(f"  {variant}: {verdict} "
              f"(WF {stats['improved_windows']}/6, mean dSharpe={stats['mean_wf_dsharpe']:+.3f}, "
              f"PIT dSharpe={stats['pit_oos_dsharpe']:+.3f})")

    print("\nDone.")


if __name__ == '__main__':
    main()
