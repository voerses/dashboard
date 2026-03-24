#!/workspace/venv/bin/python
"""
R71: V3.1 Walk-Forward Validation — Whipsaw Filter Robustness Test
====================================================================

V3.1 = V3 + Whipsaw Filter (best filter from R69).

Strategy Definition:
  Base: Long when 20d EMA > 50d EMA, flat otherwise. NO stop losses.
  **Whipsaw filter**: Count EMA crossovers (20/50) in the last 60 days.
    If crosses > 2: go flat (position = 0).
  Positioning overlay: Binance Top Trader L/S + L/S Divergence combined
    z-score (30d rolling) -> sizing multiplier
    (z>1.5->0.3x, z>0.5->0.5x, neutral->1.0x, z<-0.5->1.3x, z<-1.5->1.5x)
  VRP overlay: (IV - RV) z-score over 60d -> sizing multiplier
    (z>1->1.3x, z>-0.5->1.0x, z>-1.5->0.5x, z<-1.5->0.3x)
  Rebalancing: Weekly (Monday). Cost: 10 bps round-trip. Position range: 0 to 1.5x.

Walk-Forward Windows (same as R67):
  6 rolling windows, each 18-month IS + 6-month OOS.
  1. IS: 2021-01 to 2022-06, OOS: 2022-07 to 2022-12
  2. IS: 2021-07 to 2022-12, OOS: 2023-01 to 2023-06
  3. IS: 2022-01 to 2023-06, OOS: 2023-07 to 2023-12
  4. IS: 2022-07 to 2023-12, OOS: 2024-01 to 2024-06
  5. IS: 2023-01 to 2024-06, OOS: 2024-07 to 2024-12
  6. IS: 2023-07 to 2024-12, OOS: 2025-01 to 2025-06

KILL criteria:
  - V3.1 fails to improve V3 in >= 4/6 windows -> NOT ROBUST
  - Any window dSharpe < -0.5 -> filter is dangerous
  - Whipsaw filter parameter sensitivity > 30% Sharpe degradation from +-33% changes -> FRAGILE
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


# ============================================================================
# DATA LOADING
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
# SIGNAL CONSTRUCTION (parameterized)
# ============================================================================

def build_ema_trend_signal(btc_daily, fast_period=20, slow_period=50):
    """V3 base trend: Long when fast EMA > slow EMA, flat otherwise."""
    ema_fast = btc_daily['close'].ewm(span=fast_period, adjust=False).mean()
    ema_slow = btc_daily['close'].ewm(span=slow_period, adjust=False).mean()
    position = (ema_fast > ema_slow).astype(float)
    position.iloc[:slow_period] = 0.0
    return position, ema_fast, ema_slow


def count_ema_crossovers(ema_fast, ema_slow, lookback_window=60):
    """
    Count EMA crossovers in a rolling window.
    A crossover occurs when the fast/slow EMA relative position changes.
    Returns a Series with the count of crossovers in the last `lookback_window` days.
    """
    # Cross signal: True when fast > slow
    cross_signal = (ema_fast > ema_slow).astype(int)
    # Change in cross signal (1 = bullish cross, -1 = bearish cross)
    cross_change = cross_signal.diff().abs()
    # Rolling sum of crossovers in the lookback window
    cross_count = cross_change.rolling(lookback_window, min_periods=1).sum()
    return cross_count


def apply_whipsaw_filter(base_position, cross_count, max_crosses=2,
                         reduce_to=0.0):
    """
    Apply whipsaw filter: when cross_count > max_crosses, reduce position.

    Args:
        base_position: Series of base positions (0.0 or 1.0)
        cross_count: Series of crossover counts in lookback window
        max_crosses: Threshold above which we reduce
        reduce_to: Position multiplier when whipsaw detected (0.0 = flat, 0.5 = half)

    Returns:
        Filtered position Series
    """
    whipsaw_active = cross_count > max_crosses
    filtered = base_position.copy()
    filtered[whipsaw_active] = base_position[whipsaw_active] * reduce_to
    return filtered


def build_positioning_signal(btc_daily, positioning, z_window=30, high_thresh=1.5):
    """Positioning overlay: combined z-score -> sizing multiplier."""
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
    """VRP sizing overlay: (IV - RV) z-score -> sizing multiplier."""
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
# BACKTEST ENGINE
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
# STRATEGY RUNNER (V3 and V3.1)
# ============================================================================

def run_v3_strategy(btc_daily, positioning, dvol, start_date, end_date):
    """Run V3 (no whipsaw filter) over a date range."""
    base_pos, _, _ = build_ema_trend_signal(btc_daily)
    pos_mult = build_positioning_signal(btc_daily, positioning)
    vrp_mult = build_vrp_signal(btc_daily, dvol)
    final_pos = compute_final_position(base_pos, pos_mult, vrp_mult)
    strat_ret = run_backtest(btc_daily, final_pos)

    mask = (btc_daily.index >= start_date) & (btc_daily.index <= end_date)
    return compute_metrics(strat_ret[mask])


def run_v31_strategy(btc_daily, positioning, dvol, start_date, end_date,
                     whipsaw_lookback=60, whipsaw_max_crosses=2,
                     whipsaw_reduce_to=0.0):
    """Run V3.1 (with whipsaw filter) over a date range."""
    base_pos, ema_fast, ema_slow = build_ema_trend_signal(btc_daily)

    # Apply whipsaw filter
    cross_count = count_ema_crossovers(ema_fast, ema_slow, whipsaw_lookback)
    filtered_pos = apply_whipsaw_filter(base_pos, cross_count,
                                        whipsaw_max_crosses, whipsaw_reduce_to)

    pos_mult = build_positioning_signal(btc_daily, positioning)
    vrp_mult = build_vrp_signal(btc_daily, dvol)
    final_pos = compute_final_position(filtered_pos, pos_mult, vrp_mult)
    strat_ret = run_backtest(btc_daily, final_pos)

    mask = (btc_daily.index >= start_date) & (btc_daily.index <= end_date)
    return compute_metrics(strat_ret[mask])


# ============================================================================
# PART 1: WALK-FORWARD TEST (V3 vs V3.1)
# ============================================================================

def run_walk_forward(btc_daily, positioning, dvol):
    """Run 6 rolling walk-forward windows, comparing V3 and V3.1."""
    print("=" * 72)
    print("PART 1: WALK-FORWARD TEST (V3 vs V3.1, 6 windows)")
    print("=" * 72)

    windows = [
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

    results = []
    for i, w in enumerate(windows):
        print(f"\n  Window {i+1}: IS {w['is_start']}..{w['is_end']}, "
              f"OOS {w['oos_start']}..{w['oos_end']}")

        # V3 (no whipsaw)
        v3_is = run_v3_strategy(btc_daily, positioning, dvol,
                                w['is_start'], w['is_end'])
        v3_oos = run_v3_strategy(btc_daily, positioning, dvol,
                                 w['oos_start'], w['oos_end'])

        # V3.1 (with whipsaw)
        v31_is = run_v31_strategy(btc_daily, positioning, dvol,
                                  w['is_start'], w['is_end'])
        v31_oos = run_v31_strategy(btc_daily, positioning, dvol,
                                   w['oos_start'], w['oos_end'])

        d_sharpe_is = v31_is['sharpe'] - v3_is['sharpe']
        d_sharpe_oos = v31_oos['sharpe'] - v3_oos['sharpe']
        d_return_oos = v31_oos['ann_return'] - v3_oos['ann_return']
        d_maxdd_oos = v31_oos['max_dd'] - v3_oos['max_dd']

        print(f"    V3  IS:  Sharpe={v3_is['sharpe']:+.3f}, "
              f"Return={v3_is['ann_return']:+.1%}")
        print(f"    V3  OOS: Sharpe={v3_oos['sharpe']:+.3f}, "
              f"Return={v3_oos['ann_return']:+.1%}, "
              f"MaxDD={v3_oos['max_dd']:.1%}")
        print(f"    V3.1 IS: Sharpe={v31_is['sharpe']:+.3f}, "
              f"Return={v31_is['ann_return']:+.1%}")
        print(f"    V3.1 OOS: Sharpe={v31_oos['sharpe']:+.3f}, "
              f"Return={v31_oos['ann_return']:+.1%}, "
              f"MaxDD={v31_oos['max_dd']:.1%}")
        print(f"    Delta OOS: dSharpe={d_sharpe_oos:+.3f}, "
              f"dReturn={d_return_oos:+.1%}, "
              f"dMaxDD={d_maxdd_oos:+.1%}")

        results.append({
            'window': i + 1,
            'is_start': w['is_start'],
            'is_end': w['is_end'],
            'oos_start': w['oos_start'],
            'oos_end': w['oos_end'],
            'v3_is_sharpe': v3_is['sharpe'],
            'v3_is_return': v3_is['ann_return'],
            'v3_oos_sharpe': v3_oos['sharpe'],
            'v3_oos_return': v3_oos['ann_return'],
            'v3_oos_maxdd': v3_oos['max_dd'],
            'v31_is_sharpe': v31_is['sharpe'],
            'v31_is_return': v31_is['ann_return'],
            'v31_oos_sharpe': v31_oos['sharpe'],
            'v31_oos_return': v31_oos['ann_return'],
            'v31_oos_maxdd': v31_oos['max_dd'],
            'd_sharpe_is': d_sharpe_is,
            'd_sharpe_oos': d_sharpe_oos,
            'd_return_oos': d_return_oos,
            'd_maxdd_oos': d_maxdd_oos,
            'oos_days': v3_oos['n_days'],
        })

    # Summary
    d_sharpes = [r['d_sharpe_oos'] for r in results
                 if not np.isnan(r['d_sharpe_oos'])]
    n_improved = sum(1 for d in d_sharpes if d > 0)
    n_hurt = sum(1 for d in d_sharpes if d < -0.5)
    min_d = min(d_sharpes) if d_sharpes else np.nan
    max_d = max(d_sharpes) if d_sharpes else np.nan

    v3_oos_sharpes = [r['v3_oos_sharpe'] for r in results
                      if not np.isnan(r['v3_oos_sharpe'])]
    v31_oos_sharpes = [r['v31_oos_sharpe'] for r in results
                       if not np.isnan(r['v31_oos_sharpe'])]

    print(f"\n  WALK-FORWARD SUMMARY:")
    print(f"    V3.1 improves V3 in: {n_improved}/{len(d_sharpes)} windows")
    print(f"    V3.1 hurts V3 by >0.5: {n_hurt}/{len(d_sharpes)} windows")
    print(f"    dSharpe range: {min_d:+.3f} to {max_d:+.3f}")
    print(f"    Mean dSharpe: {np.mean(d_sharpes):+.3f}")
    print(f"    V3 mean OOS Sharpe: {np.mean(v3_oos_sharpes):.3f}")
    print(f"    V3.1 mean OOS Sharpe: {np.mean(v31_oos_sharpes):.3f}")

    summary = {
        'n_improved': n_improved,
        'n_hurt': n_hurt,
        'n_total': len(d_sharpes),
        'mean_d_sharpe': np.mean(d_sharpes),
        'median_d_sharpe': np.median(d_sharpes),
        'min_d_sharpe': min_d,
        'max_d_sharpe': max_d,
        'v3_mean_oos_sharpe': np.mean(v3_oos_sharpes),
        'v31_mean_oos_sharpe': np.mean(v31_oos_sharpes),
        'v3_median_oos_sharpe': np.median(v3_oos_sharpes),
        'v31_median_oos_sharpe': np.median(v31_oos_sharpes),
    }

    return results, summary


# ============================================================================
# DIAGNOSTIC: When does the whipsaw filter actually override base signal?
# ============================================================================

def run_filter_diagnostic(btc_daily):
    """Analyze when the whipsaw filter actually changes the position."""
    print("\n" + "=" * 72)
    print("DIAGNOSTIC: WHIPSAW FILTER ACTIVITY ANALYSIS")
    print("=" * 72)

    ema_fast = btc_daily['close'].ewm(span=20, adjust=False).mean()
    ema_slow = btc_daily['close'].ewm(span=50, adjust=False).mean()

    base_pos = (ema_fast > ema_slow).astype(float)
    base_pos.iloc[:50] = 0.0

    cross_signal = (ema_fast > ema_slow).astype(int)
    cross_change = cross_signal.diff().abs()
    cross_count_60 = cross_change.rolling(60, min_periods=1).sum()

    whipsaw_active = cross_count_60 > 2
    filtered_pos = base_pos.copy()
    filtered_pos[whipsaw_active] = 0.0

    # Days where filter actually overrides a LONG signal
    override_days = (base_pos > 0) & (filtered_pos == 0)
    n_override = override_days.sum()

    print(f"  Total days in dataset: {len(btc_daily)}")
    print(f"  Days with whipsaw active (crosses>2 in 60d): {whipsaw_active.sum()}")
    print(f"  Days where filter OVERRIDES a long signal: {n_override}")

    # Year-by-year
    print(f"\n  Whipsaw active days by year:")
    for year in range(2020, 2027):
        mask = btc_daily.index.year == year
        n_total = mask.sum()
        n_active = (whipsaw_active & mask).sum()
        n_override_yr = (override_days & mask).sum()
        if n_total > 0:
            print(f"    {year}: {n_active:3d}/{n_total} active "
                  f"({100*n_active/n_total:.1f}%), "
                  f"{n_override_yr} overrides")

    # Override periods
    override_dates = btc_daily.index[override_days]
    override_periods = []
    if len(override_dates) > 0:
        print(f"\n  Override periods (base=long, filter=flat):")
        gaps = (override_dates.to_series().diff() > pd.Timedelta(days=5)).cumsum()
        for _, group in override_dates.to_series().groupby(gaps):
            start = group.min()
            end = group.max()
            duration = (end - start).days + 1
            # Price move during this period
            price_start = btc_daily.loc[start, 'close']
            price_end = btc_daily.loc[end, 'close']
            price_move = (price_end / price_start - 1) * 100
            print(f"    {start.date()} to {end.date()} ({duration} days), "
                  f"BTC {price_start:.0f} -> {price_end:.0f} ({price_move:+.1f}%)")
            override_periods.append({
                'start': start.date().isoformat(),
                'end': end.date().isoformat(),
                'duration': duration,
                'price_move_pct': price_move,
            })

    # Check OOS windows
    print(f"\n  Override days per walk-forward OOS window:")
    oos_windows = [
        ('2022-07-01', '2022-12-31', 'W1'),
        ('2023-01-01', '2023-06-30', 'W2'),
        ('2023-07-01', '2023-12-31', 'W3'),
        ('2024-01-01', '2024-06-30', 'W4'),
        ('2024-07-01', '2024-12-31', 'W5'),
        ('2025-01-01', '2025-06-30', 'W6'),
    ]
    for start, end, label in oos_windows:
        mask = (btc_daily.index >= start) & (btc_daily.index <= end)
        n_ov = (override_days & mask).sum()
        n_act = (whipsaw_active & mask).sum()
        print(f"    {label} ({start}..{end}): {n_ov} overrides, {n_act} active")

    # Key insight
    print(f"\n  KEY INSIGHT: The whipsaw filter only overrides long signals on")
    print(f"  {n_override} days total, ALL in 2025 Q4 (Oct 2025).")
    print(f"  This is AFTER all 6 walk-forward OOS windows end.")
    print(f"  The R69 OOS improvement (+0.426 dSharpe) was driven by a SINGLE episode.")

    return {
        'n_override': n_override,
        'n_whipsaw_active': whipsaw_active.sum(),
        'override_periods': override_periods,
    }


# ============================================================================
# PART 2: PARAMETER SENSITIVITY (cross_threshold x lookback_window grid)
# ============================================================================

def run_parameter_sensitivity(btc_daily, positioning, dvol):
    """Test whipsaw filter parameter grid on OOS period."""
    print("\n" + "=" * 72)
    print("PART 2: WHIPSAW FILTER PARAMETER SENSITIVITY")
    print("=" * 72)

    oos_start = '2025-01-01'
    oos_end = btc_daily.index.max().strftime('%Y-%m-%d')
    print(f"  OOS period: {oos_start} to {oos_end}")

    # V3 baseline (no filter)
    v3_metrics = run_v3_strategy(btc_daily, positioning, dvol, oos_start, oos_end)
    print(f"  V3 baseline OOS Sharpe: {v3_metrics['sharpe']:+.3f}")

    # Parameter grid
    cross_thresholds = [1, 2, 3, 4]
    lookback_windows = [30, 60, 90, 120]

    # Grid at reduction = 0.0 (go flat)
    print(f"\n  Grid: cross_threshold x lookback_window (reduction=0.0)")
    print(f"  {'':>15s}", end="")
    for lb in lookback_windows:
        print(f"  LB={lb:>3d}", end="")
    print()

    grid_flat = {}
    for ct in cross_thresholds:
        print(f"  crosses>{ct:>2d}   ", end="")
        for lb in lookback_windows:
            metrics = run_v31_strategy(
                btc_daily, positioning, dvol, oos_start, oos_end,
                whipsaw_lookback=lb, whipsaw_max_crosses=ct,
                whipsaw_reduce_to=0.0
            )
            grid_flat[(ct, lb)] = metrics
            print(f"  {metrics['sharpe']:+.3f}", end="")
        print()

    # Grid at reduction = 0.5 (go half)
    print(f"\n  Grid: cross_threshold x lookback_window (reduction=0.5)")
    print(f"  {'':>15s}", end="")
    for lb in lookback_windows:
        print(f"  LB={lb:>3d}", end="")
    print()

    grid_half = {}
    for ct in cross_thresholds:
        print(f"  crosses>{ct:>2d}   ", end="")
        for lb in lookback_windows:
            metrics = run_v31_strategy(
                btc_daily, positioning, dvol, oos_start, oos_end,
                whipsaw_lookback=lb, whipsaw_max_crosses=ct,
                whipsaw_reduce_to=0.5
            )
            grid_half[(ct, lb)] = metrics
            print(f"  {metrics['sharpe']:+.3f}", end="")
        print()

    # Check sensitivity around default (ct=2, lb=60)
    default_sharpe = grid_flat[(2, 60)]['sharpe']
    print(f"\n  Default (ct=2, lb=60, r=0.0) Sharpe: {default_sharpe:+.3f}")

    # Check +-33% parameter changes
    sensitivity_kills = []
    neighbors = [
        (1, 60, "ct=1 (cross threshold -50%)"),
        (3, 60, "ct=3 (cross threshold +50%)"),
        (2, 40, "lb=40 (lookback -33%)"),
        (2, 80, "lb=80 (lookback +33%)"),
    ]

    # We need ct=2,lb=40 and ct=2,lb=80 which are not in the grid
    # Run them specifically
    extra_params = [
        (2, 40, 0.0),
        (2, 80, 0.0),
    ]
    for ct, lb, rt in extra_params:
        if (ct, lb) not in grid_flat:
            metrics = run_v31_strategy(
                btc_daily, positioning, dvol, oos_start, oos_end,
                whipsaw_lookback=lb, whipsaw_max_crosses=ct,
                whipsaw_reduce_to=rt
            )
            grid_flat[(ct, lb)] = metrics

    print(f"\n  Sensitivity check (+-33% parameter changes from default):")
    for ct, lb, label in neighbors:
        if (ct, lb) in grid_flat:
            neighbor_sharpe = grid_flat[(ct, lb)]['sharpe']
        else:
            m = run_v31_strategy(
                btc_daily, positioning, dvol, oos_start, oos_end,
                whipsaw_lookback=lb, whipsaw_max_crosses=ct,
                whipsaw_reduce_to=0.0
            )
            grid_flat[(ct, lb)] = m
            neighbor_sharpe = m['sharpe']

        if default_sharpe > 0 and not np.isnan(neighbor_sharpe):
            degradation = (default_sharpe - neighbor_sharpe) / abs(default_sharpe)
        else:
            degradation = 0.0

        is_kill = degradation > 0.30
        flag = " *** KILL" if is_kill else ""
        print(f"    {label}: Sharpe={neighbor_sharpe:+.3f}, "
              f"degradation={degradation:+.1%}{flag}")

        if is_kill:
            sensitivity_kills.append({
                'ct': ct, 'lb': lb, 'label': label,
                'default_sharpe': default_sharpe,
                'neighbor_sharpe': neighbor_sharpe,
                'degradation': degradation,
            })

    return v3_metrics, grid_flat, grid_half, sensitivity_kills


# ============================================================================
# REPORT GENERATION
# ============================================================================

def generate_report(wf_results, wf_summary, v3_baseline, grid_flat, grid_half,
                    sensitivity_kills, diag=None):
    """Generate markdown report."""
    lines = []
    lines.append("# R71: V3.1 Walk-Forward Validation Results")
    lines.append("")
    lines.append(f"**Run date**: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("")

    # Strategy definition
    lines.append("## Strategy Definitions")
    lines.append("")
    lines.append("### V3 (Baseline)")
    lines.append("- Base: Long when 20d EMA > 50d EMA, flat otherwise. No stop losses.")
    lines.append("- Positioning overlay: Binance Top Trader L/S + L/S Divergence "
                 "combined z-score (30d) -> 0.3x to 1.5x")
    lines.append("- VRP overlay: (IV - RV) z-score (60d) -> 0.3x to 1.3x")
    lines.append("- Rebalancing: Weekly (Monday). Cost: 10 bps. Position range: [0, 1.5x].")
    lines.append("")
    lines.append("### V3.1 (V3 + Whipsaw Filter)")
    lines.append("- **Same as V3**, plus:")
    lines.append("- **Whipsaw filter**: Count EMA 20/50 crossovers in last 60 days. "
                 "If crosses > 2: go flat (position = 0).")
    lines.append("")

    # KILL criteria
    lines.append("## KILL Criteria")
    lines.append("")
    lines.append("1. V3.1 fails to improve V3 in >= 4/6 windows -> NOT ROBUST")
    lines.append("2. Any window dSharpe < -0.5 -> filter is dangerous")
    lines.append("3. Parameter sensitivity > 30% Sharpe degradation from "
                 "+-33% changes -> FRAGILE")
    lines.append("")

    # ── PART 1: Walk-Forward ──
    lines.append("## Part 1: Walk-Forward Test (V3 vs V3.1)")
    lines.append("")
    lines.append("6 rolling windows, each 18-month IS + 6-month OOS.")
    lines.append("")

    # Table
    lines.append("| Win | OOS Period | V3 OOS Sharpe | V3.1 OOS Sharpe | "
                 "dSharpe | V3 OOS Return | V3.1 OOS Return | "
                 "V3 OOS MaxDD | V3.1 OOS MaxDD |")
    lines.append("|-----|------------|---------------|-----------------|"
                 "---------|---------------|------------------|"
                 "--------------|----------------|")

    for r in wf_results:
        lines.append(
            f"| {r['window']} "
            f"| {r['oos_start']}..{r['oos_end']} "
            f"| {r['v3_oos_sharpe']:+.3f} "
            f"| {r['v31_oos_sharpe']:+.3f} "
            f"| {r['d_sharpe_oos']:+.3f} "
            f"| {r['v3_oos_return']:+.1%} "
            f"| {r['v31_oos_return']:+.1%} "
            f"| {r['v3_oos_maxdd']:.1%} "
            f"| {r['v31_oos_maxdd']:.1%} |"
        )

    lines.append("")

    # Summary
    lines.append("### Walk-Forward Summary")
    lines.append("")
    lines.append(f"- V3.1 improves V3 in: **{wf_summary['n_improved']}/"
                 f"{wf_summary['n_total']}** windows "
                 f"(target: >= 4/6)")
    lines.append(f"- V3.1 hurts V3 by >0.5 Sharpe: **{wf_summary['n_hurt']}/"
                 f"{wf_summary['n_total']}** windows "
                 f"(target: 0)")
    lines.append(f"- dSharpe range: {wf_summary['min_d_sharpe']:+.3f} to "
                 f"{wf_summary['max_d_sharpe']:+.3f}")
    lines.append(f"- Mean dSharpe: {wf_summary['mean_d_sharpe']:+.3f}")
    lines.append(f"- Median dSharpe: {wf_summary['median_d_sharpe']:+.3f}")
    lines.append(f"- V3 mean OOS Sharpe: {wf_summary['v3_mean_oos_sharpe']:.3f}")
    lines.append(f"- V3.1 mean OOS Sharpe: {wf_summary['v31_mean_oos_sharpe']:.3f}")
    lines.append(f"- V3 median OOS Sharpe: {wf_summary['v3_median_oos_sharpe']:.3f}")
    lines.append(f"- V3.1 median OOS Sharpe: {wf_summary['v31_median_oos_sharpe']:.3f}")
    lines.append("")

    # Distribution analysis
    d_sharpes = [r['d_sharpe_oos'] for r in wf_results
                 if not np.isnan(r['d_sharpe_oos'])]
    positive_windows = [r['window'] for r in wf_results
                        if r['d_sharpe_oos'] > 0]
    negative_windows = [r['window'] for r in wf_results
                        if r['d_sharpe_oos'] < 0]
    zero_windows = [r['window'] for r in wf_results
                    if r['d_sharpe_oos'] == 0]

    lines.append("### Distribution of Improvement")
    lines.append("")
    lines.append(f"- Windows where V3.1 > V3: {positive_windows if positive_windows else 'none'}")
    lines.append(f"- Windows where V3.1 < V3: {negative_windows if negative_windows else 'none'}")
    lines.append(f"- Windows where V3.1 = V3: {zero_windows if zero_windows else 'none'}")
    lines.append("")

    # Check if benefit is concentrated
    if d_sharpes:
        abs_d = [abs(d) for d in d_sharpes]
        max_abs_contribution = max(abs_d) / sum(abs_d) if sum(abs_d) > 0 else 0
        if max_abs_contribution > 0.5:
            concentration = "CONCENTRATED (single window dominates)"
        else:
            concentration = "BROADLY DISTRIBUTED"
        lines.append(f"- Benefit concentration: **{concentration}** "
                     f"(max single window = {max(abs_d):.3f} of total {sum(abs_d):.3f})")
    lines.append("")

    # IS comparison table
    lines.append("### IS Comparison (for overfit detection)")
    lines.append("")
    lines.append("| Win | V3 IS Sharpe | V3.1 IS Sharpe | IS dSharpe | OOS dSharpe |")
    lines.append("|-----|--------------|----------------|------------|-------------|")

    for r in wf_results:
        lines.append(
            f"| {r['window']} "
            f"| {r['v3_is_sharpe']:+.3f} "
            f"| {r['v31_is_sharpe']:+.3f} "
            f"| {r['d_sharpe_is']:+.3f} "
            f"| {r['d_sharpe_oos']:+.3f} |"
        )
    lines.append("")

    # ── DIAGNOSTIC: Why zero delta? ──
    if diag:
        lines.append("## Diagnostic: Why V3.1 = V3 Across All Walk-Forward Windows")
        lines.append("")
        lines.append("The whipsaw filter (crosses > 2 in 60d -> go flat) only overrides ")
        lines.append("the base long signal when TWO conditions are met simultaneously:")
        lines.append("1. The EMA crossover count exceeds the threshold (>2 crosses)")
        lines.append("2. The base signal is LONG (fast EMA > slow EMA)")
        lines.append("")
        lines.append(f"Analysis of {diag['n_whipsaw_active']} days where the filter was "
                     f"active (crosses > 2): in {diag['n_whipsaw_active'] - diag['n_override']} "
                     f"of those days, the base signal was already flat (EMA fast < slow). "
                     f"The filter only OVERRIDES a long signal on **{diag['n_override']} days** "
                     f"total.")
        lines.append("")

        if diag['override_periods']:
            lines.append("### Override Periods (base=long, filter=flat)")
            lines.append("")
            lines.append("| Period | Duration | BTC Price Move |")
            lines.append("|--------|----------|----------------|")
            for p in diag['override_periods']:
                lines.append(f"| {p['start']} to {p['end']} | {p['duration']} days | "
                             f"{p['price_move_pct']:+.1f}% |")
            lines.append("")

        lines.append("### Critical Finding")
        lines.append("")
        lines.append("All override days fall in **October 2025**, which is AFTER every ")
        lines.append("walk-forward OOS window ends (latest: 2025-06-30). This means:")
        lines.append("")
        lines.append("1. The R69 OOS improvement (+0.426 dSharpe) was driven by a **single "
                     "16-day episode** in Oct 2025")
        lines.append("2. The whipsaw filter has **zero historical evidence** of value across "
                     "the 2022-2025 walk-forward periods")
        lines.append("3. The filter's apparent benefit is concentrated in a single event, not "
                     "a persistent pattern")
        lines.append("")
        lines.append("This is a textbook case of a filter that looks great on a recent test "
                     "period but has no historical support. The 20/50 EMA whipsaw pattern "
                     "(multiple crosses followed by a brief recovery that the filter catches) "
                     "only occurred once in 5+ years of data.")
        lines.append("")

    # ── PART 2: Parameter Sensitivity ──
    lines.append("## Part 2: Whipsaw Filter Parameter Sensitivity")
    lines.append("")
    lines.append(f"V3 baseline OOS Sharpe (no filter): {v3_baseline['sharpe']:+.3f}")
    lines.append("")

    # Grid at reduction = 0.0
    cross_thresholds = [1, 2, 3, 4]
    lookback_windows = [30, 60, 90, 120]

    lines.append("### Grid: cross_threshold x lookback_window (reduction=0.0, go flat)")
    lines.append("")
    header = "| crosses \\ lookback |"
    for lb in lookback_windows:
        header += f" LB={lb} |"
    lines.append(header)
    divider = "|" + "---|" * (len(lookback_windows) + 1)
    lines.append(divider)

    for ct in cross_thresholds:
        row = f"| >{ct} |"
        for lb in lookback_windows:
            if (ct, lb) in grid_flat:
                s = grid_flat[(ct, lb)]['sharpe']
                row += f" {s:+.3f} |"
            else:
                row += " N/A |"
        lines.append(row)
    lines.append("")

    # Delta vs V3 grid
    lines.append("### Delta Sharpe vs V3 (reduction=0.0)")
    lines.append("")
    header = "| crosses \\ lookback |"
    for lb in lookback_windows:
        header += f" LB={lb} |"
    lines.append(header)
    lines.append(divider)

    for ct in cross_thresholds:
        row = f"| >{ct} |"
        for lb in lookback_windows:
            if (ct, lb) in grid_flat:
                d = grid_flat[(ct, lb)]['sharpe'] - v3_baseline['sharpe']
                row += f" {d:+.3f} |"
            else:
                row += " N/A |"
        lines.append(row)
    lines.append("")

    # Grid at reduction = 0.5
    lines.append("### Grid: cross_threshold x lookback_window (reduction=0.5, go half)")
    lines.append("")
    header = "| crosses \\ lookback |"
    for lb in lookback_windows:
        header += f" LB={lb} |"
    lines.append(header)
    lines.append(divider)

    for ct in cross_thresholds:
        row = f"| >{ct} |"
        for lb in lookback_windows:
            if (ct, lb) in grid_half:
                s = grid_half[(ct, lb)]['sharpe']
                row += f" {s:+.3f} |"
            else:
                row += " N/A |"
        lines.append(row)
    lines.append("")

    # Sensitivity analysis
    lines.append("### Sensitivity Check (+-33% from default ct=2, lb=60)")
    lines.append("")

    default_sharpe = grid_flat.get((2, 60), {}).get('sharpe', np.nan)

    neighbors = [
        (1, 60, "ct=1 (cross threshold -50%)"),
        (3, 60, "ct=3 (cross threshold +50%)"),
        (2, 40, "lb=40 (lookback -33%)"),
        (2, 80, "lb=80 (lookback +33%)"),
    ]

    lines.append("| Parameter Change | Sharpe | Degradation | Verdict |")
    lines.append("|------------------|--------|-------------|---------|")
    lines.append(f"| Default (ct=2, lb=60) | {default_sharpe:+.3f} | -- | BASELINE |")

    for ct, lb, label in neighbors:
        if (ct, lb) in grid_flat:
            neighbor_sharpe = grid_flat[(ct, lb)]['sharpe']
        else:
            neighbor_sharpe = np.nan

        if default_sharpe > 0 and not np.isnan(neighbor_sharpe):
            degradation = (default_sharpe - neighbor_sharpe) / abs(default_sharpe)
        else:
            degradation = 0.0

        verdict = "KILL (>30%)" if degradation > 0.30 else "OK"
        lines.append(f"| {label} | {neighbor_sharpe:+.3f} | "
                     f"{degradation:+.1%} | {verdict} |")

    lines.append("")

    if sensitivity_kills:
        lines.append(f"**Sensitivity KILL flags**: {len(sensitivity_kills)}")
        for sk in sensitivity_kills:
            lines.append(f"- {sk['label']}: {sk['default_sharpe']:.3f} -> "
                         f"{sk['neighbor_sharpe']:.3f} "
                         f"({sk['degradation']:.1%} degradation)")
        lines.append("")
    else:
        lines.append("**No sensitivity KILL flags.** All +-33% perturbations "
                     "within 30% tolerance.")
        lines.append("")

    # ── VERDICT ──
    lines.append("## Verdict")
    lines.append("")

    # Decision logic
    wf_pass = wf_summary['n_improved'] >= 4
    wf_safe = wf_summary['n_hurt'] == 0
    param_pass = len(sensitivity_kills) == 0

    # Check concentration
    if d_sharpes:
        abs_d = [abs(d) for d in d_sharpes]
        max_abs_contribution = max(abs_d) / sum(abs_d) if sum(abs_d) > 0 else 0
        broadly_distributed = max_abs_contribution <= 0.5
    else:
        broadly_distributed = False

    if wf_pass and wf_safe and param_pass:
        verdict = "ROBUST"
        explanation = (
            f"V3.1 improves V3 in {wf_summary['n_improved']}/{wf_summary['n_total']} "
            f"windows (mean dSharpe {wf_summary['mean_d_sharpe']:+.3f}). "
            f"No window shows dangerous degradation (min dSharpe {wf_summary['min_d_sharpe']:+.3f}, "
            f"well above -0.5 threshold). "
            f"Parameter sensitivity is within tolerance (no KILL flags). "
            f"V3.1 mean OOS Sharpe ({wf_summary['v31_mean_oos_sharpe']:.3f}) vs "
            f"V3 mean OOS Sharpe ({wf_summary['v3_mean_oos_sharpe']:.3f}). "
            f"The whipsaw filter improvement is {'broadly distributed across periods' if broadly_distributed else 'somewhat concentrated but still consistent'}."
        )
    elif not wf_safe:
        verdict = "KILL"
        explanation = (
            f"V3.1 causes dangerous degradation in at least one window "
            f"(min dSharpe {wf_summary['min_d_sharpe']:+.3f}, threshold -0.5). "
            f"The whipsaw filter is unsafe for production deployment."
        )
    elif not wf_pass:
        verdict = "KILL"
        explanation = (
            f"V3.1 only improves V3 in {wf_summary['n_improved']}/{wf_summary['n_total']} "
            f"windows (target >= 4/6). The whipsaw filter is not robust across "
            f"different market periods."
        )
    elif not param_pass:
        verdict = "FRAGILE"
        explanation = (
            f"V3.1 passes walk-forward ({wf_summary['n_improved']}/{wf_summary['n_total']} "
            f"improved) but fails parameter sensitivity ({len(sensitivity_kills)} KILL flags). "
            f"The filter's performance is too dependent on exact parameter choices."
        )
    else:
        verdict = "FRAGILE"
        explanation = "Mixed signals -- some criteria pass, some fail."

    lines.append(f"### **{verdict}**")
    lines.append("")
    lines.append(explanation)
    lines.append("")

    # Scoring breakdown
    lines.append("### KILL Criteria Check")
    lines.append("")
    lines.append("| Criterion | Result | Threshold | Verdict |")
    lines.append("|-----------|--------|-----------|---------|")
    lines.append(f"| Walk-forward improvement | "
                 f"{wf_summary['n_improved']}/{wf_summary['n_total']} windows | "
                 f">= 4/6 | {'PASS' if wf_pass else 'FAIL'} |")
    lines.append(f"| No dangerous degradation | "
                 f"min dSharpe = {wf_summary['min_d_sharpe']:+.3f} | "
                 f"> -0.5 | {'PASS' if wf_safe else 'FAIL'} |")
    lines.append(f"| Parameter sensitivity | "
                 f"{len(sensitivity_kills)} KILL flags | "
                 f"0 flags | {'PASS' if param_pass else 'FAIL'} |")
    lines.append("")

    # Recommendation
    lines.append("### Recommendation")
    lines.append("")
    if verdict == "ROBUST":
        lines.append("The whipsaw filter (crosses > 2 in 60d -> go flat) is validated "
                     "for production inclusion in V3.1. Key properties:")
        lines.append("")
        lines.append(f"1. Consistent improvement across market periods "
                     f"({wf_summary['n_improved']}/{wf_summary['n_total']} windows)")
        lines.append(f"2. Mean OOS Sharpe improvement: {wf_summary['mean_d_sharpe']:+.3f}")
        lines.append("3. Parameter-stable (no sensitivity KILL flags)")
        lines.append("4. Simple implementation (crossover counting)")
        lines.append("")
        lines.append("**Proceed to production deployment.**")
    elif verdict == "FRAGILE":
        lines.append("The whipsaw filter shows promise but is not robust enough for "
                     "unconditional deployment. Consider:")
        lines.append("")
        lines.append("1. Testing with different EMA periods")
        lines.append("2. Using reduction=0.5 instead of 0.0 for a softer filter")
        lines.append("3. Combining with EMA spread filter for confirmation")
    else:
        lines.append("The whipsaw filter does not pass robustness validation. "
                     "Do not deploy to production.")
        lines.append("")
        lines.append("**Root cause**: The whipsaw filter (crosses > 2 in 60d) "
                     "is mechanically redundant with the base EMA crossover signal. "
                     "When multiple EMA crosses occur, the base signal naturally "
                     "oscillates between long and flat. The filter only adds value when "
                     "it catches a brief EMA re-cross (bullish) following multiple "
                     "prior crosses -- a pattern that occurred exactly once in 5+ years "
                     "of data (Oct 2025).")
        lines.append("")
        lines.append("**Why R69 was misleading**: R69 tested on a single OOS period "
                     "(2025-01 to latest) that happened to contain the one episode "
                     "where the filter made a difference. Walk-forward validation "
                     "exposes this as a one-event artifact, not a systematic improvement.")
        lines.append("")
        lines.append("Next steps:")
        lines.append("1. **Do NOT add whipsaw filter to V3** -- it adds complexity with "
                     "zero historical benefit")
        lines.append("2. **EMA spread filter** (A_spread_0.010 from R69, dSharpe=+0.410) "
                     "should be walk-forward tested instead as the next candidate")
        lines.append("3. **V3 RANGE regime weakness** remains an open problem -- the filter "
                     "that works best in a point-in-time test is not necessarily robust")
        lines.append("4. Consider whether RANGE regime losses are an acceptable cost of the "
                     "strategy's UPTREND capture (Sharpe +6.0 in UPTREND)")

    lines.append("")
    return '\n'.join(lines), verdict


# ============================================================================
# MAIN
# ============================================================================

def main():
    print("=" * 72)
    print("R71: V3.1 WALK-FORWARD VALIDATION (WHIPSAW FILTER)")
    print(f"Run date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 72)
    print()

    # Load data
    print("Loading data...")
    btc_daily = load_btc_daily()
    dvol = load_dvol()
    positioning = load_positioning()
    print(f"  BTC daily: {btc_daily.index.min().date()} to "
          f"{btc_daily.index.max().date()}")
    print(f"  DVOL: {dvol.index.min().date()} to {dvol.index.max().date()}")
    print(f"  Positioning: {positioning.index.min().date()} to "
          f"{positioning.index.max().date()}")
    print()

    # Part 1: Walk-Forward
    wf_results, wf_summary = run_walk_forward(btc_daily, positioning, dvol)

    # Diagnostic: analyze when the whipsaw filter actually overrides
    diag = run_filter_diagnostic(btc_daily)

    # Part 2: Parameter Sensitivity
    v3_baseline, grid_flat, grid_half, sensitivity_kills = \
        run_parameter_sensitivity(btc_daily, positioning, dvol)

    # Generate report
    print("\n" + "=" * 72)
    print("GENERATING REPORT")
    print("=" * 72)

    report, verdict = generate_report(
        wf_results, wf_summary,
        v3_baseline, grid_flat, grid_half, sensitivity_kills,
        diag
    )

    report_path = OUTPUT_DIR / 'v3_1_walkforward_results.md'
    with open(report_path, 'w') as f:
        f.write(report)
    print(f"\nReport saved to: {report_path}")

    print(f"\n{'=' * 72}")
    print(f"FINAL VERDICT: {verdict}")
    print(f"{'=' * 72}")


if __name__ == '__main__':
    main()
