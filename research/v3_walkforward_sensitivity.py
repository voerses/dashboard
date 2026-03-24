#!/workspace/venv/bin/python
"""
V3 Momentum Strategy: Walk-Forward & Parameter Sensitivity Analysis
====================================================================

Strategy Definition (V3):
  - Base: Long when 20d EMA > 50d EMA, flat otherwise. NO stop losses.
  - Positioning overlay: Binance Top Trader L/S + L/S Divergence combined
    z-score (30d rolling) -> sizing multiplier
    (z>1.5->0.3x, z>0.5->0.5x, neutral->1.0x, z<-0.5->1.3x, z<-1.5->1.5x)
  - VRP overlay: (IV - RV) z-score over 60d -> sizing multiplier
    (z>1->1.3x, z>-0.5->1.0x, z>-1.5->0.5x, z<-1.5->0.3x)
  - Rebalancing: Weekly (Monday). Cost: 10 bps round-trip. Position range: 0 to 1.5x.

Part 1: Walk-Forward (6 rolling windows, 18m IS + 6m OOS)
Part 2: Parameter Sensitivity (+/-20% on 6 parameters)
Part 3: Combinatorial Stability (4 corner cases)
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
    """
    V3 base trend: Long when fast EMA > slow EMA, flat otherwise. NO stop losses.
    """
    ema_fast = btc_daily['close'].ewm(span=fast_period, adjust=False).mean()
    ema_slow = btc_daily['close'].ewm(span=slow_period, adjust=False).mean()
    # Simple crossover: long when fast > slow
    position = (ema_fast > ema_slow).astype(float)
    # Need warm-up: set to 0 for first slow_period days
    position.iloc[:slow_period] = 0.0
    return position


def build_positioning_signal(btc_daily, positioning, z_window=30, high_thresh=1.5):
    """
    Positioning overlay with parameterized z-window and high threshold.
    Thresholds are symmetric: high_thresh, high_thresh/3, -high_thresh/3, -high_thresh
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

    # Derive thresholds from high_thresh
    mid_thresh = high_thresh / 3.0  # default: 0.5

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
    """
    VRP sizing overlay with parameterized z-window and high threshold.
    """
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

    # Derive thresholds from vrp_high_thresh
    mid_low = -vrp_high_thresh / 2.0   # default: -0.5
    extreme_low = -vrp_high_thresh * 1.5  # default: -1.5

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
# STRATEGY RUNNER (parameterized)
# ============================================================================

def run_strategy(btc_daily, positioning, dvol, start_date, end_date,
                 fast_ema=20, slow_ema=50, pos_z_window=30,
                 vrp_z_window=60, pos_high_thresh=1.5, vrp_high_thresh=1.0):
    """
    Run V3 strategy with given parameters over a date range.
    Returns metrics dict.
    """
    # Build signals on full data (to allow warm-up)
    base_pos = build_ema_trend_signal(btc_daily, fast_ema, slow_ema)
    pos_mult = build_positioning_signal(btc_daily, positioning, pos_z_window, pos_high_thresh)
    vrp_mult = build_vrp_signal(btc_daily, dvol, vrp_z_window, vrp_high_thresh)

    final_pos = compute_final_position(base_pos, pos_mult, vrp_mult)
    strat_ret = run_backtest(btc_daily, final_pos)

    # Slice to date range
    mask = (btc_daily.index >= start_date) & (btc_daily.index <= end_date)
    period_ret = strat_ret[mask]
    metrics = compute_metrics(period_ret)

    # Also compute buy-and-hold for reference
    bh_ret = btc_daily['close'].pct_change()[mask]
    bh_metrics = compute_metrics(bh_ret)
    metrics['bh_sharpe'] = bh_metrics['sharpe']
    metrics['bh_return'] = bh_metrics['ann_return']

    return metrics


# ============================================================================
# PART 1: WALK-FORWARD TEST
# ============================================================================

def run_walk_forward(btc_daily, positioning, dvol):
    """Run 6 rolling walk-forward windows."""
    print("=" * 72)
    print("PART 1: WALK-FORWARD TEST (6 windows, 18m IS + 6m OOS)")
    print("=" * 72)

    windows = [
        {'is_start': '2021-01-01', 'is_end': '2022-06-30', 'oos_start': '2022-07-01', 'oos_end': '2022-12-31'},
        {'is_start': '2021-07-01', 'is_end': '2022-12-31', 'oos_start': '2023-01-01', 'oos_end': '2023-06-30'},
        {'is_start': '2022-01-01', 'is_end': '2023-06-30', 'oos_start': '2023-07-01', 'oos_end': '2023-12-31'},
        {'is_start': '2022-07-01', 'is_end': '2023-12-31', 'oos_start': '2024-01-01', 'oos_end': '2024-06-30'},
        {'is_start': '2023-01-01', 'is_end': '2024-06-30', 'oos_start': '2024-07-01', 'oos_end': '2024-12-31'},
        {'is_start': '2023-07-01', 'is_end': '2024-12-31', 'oos_start': '2025-01-01', 'oos_end': '2025-06-30'},
    ]

    results = []
    for i, w in enumerate(windows):
        print(f"\n  Window {i+1}: IS {w['is_start']} to {w['is_end']}, OOS {w['oos_start']} to {w['oos_end']}")

        is_metrics = run_strategy(btc_daily, positioning, dvol, w['is_start'], w['is_end'])
        oos_metrics = run_strategy(btc_daily, positioning, dvol, w['oos_start'], w['oos_end'])

        print(f"    IS:  Sharpe={is_metrics['sharpe']:+.3f}, Return={is_metrics['ann_return']:+.1%}, MaxDD={is_metrics['max_dd']:.1%}")
        print(f"    OOS: Sharpe={oos_metrics['sharpe']:+.3f}, Return={oos_metrics['ann_return']:+.1%}, MaxDD={oos_metrics['max_dd']:.1%}")

        results.append({
            'window': i + 1,
            **{f'is_{k}': v for k, v in w.items() if 'is' in k},
            **{f'oos_{k}': v for k, v in w.items() if 'oos' in k},
            'is_sharpe': is_metrics['sharpe'],
            'is_return': is_metrics['ann_return'],
            'is_maxdd': is_metrics['max_dd'],
            'oos_sharpe': oos_metrics['sharpe'],
            'oos_return': oos_metrics['ann_return'],
            'oos_maxdd': oos_metrics['max_dd'],
            'oos_bh_sharpe': oos_metrics['bh_sharpe'],
            'oos_bh_return': oos_metrics['bh_return'],
            'oos_days': oos_metrics['n_days'],
        })

    # Summary statistics
    oos_sharpes = [r['oos_sharpe'] for r in results if not np.isnan(r['oos_sharpe'])]
    n_positive = sum(1 for s in oos_sharpes if s > 0)
    n_above_03 = sum(1 for s in oos_sharpes if s > 0.3)

    print(f"\n  SUMMARY:")
    print(f"    Windows with positive OOS Sharpe: {n_positive}/{len(oos_sharpes)}")
    print(f"    Windows with OOS Sharpe > 0.3: {n_above_03}/{len(oos_sharpes)}")
    print(f"    OOS Sharpe range: {min(oos_sharpes):.3f} to {max(oos_sharpes):.3f}")
    print(f"    OOS Sharpe mean: {np.mean(oos_sharpes):.3f}")
    print(f"    OOS Sharpe median: {np.median(oos_sharpes):.3f}")

    # Trend analysis
    if len(oos_sharpes) >= 3:
        first_half = np.mean(oos_sharpes[:3])
        second_half = np.mean(oos_sharpes[3:])
        if second_half > first_half + 0.1:
            trend = "IMPROVING"
        elif second_half < first_half - 0.1:
            trend = "DEGRADING"
        else:
            trend = "STABLE"
        print(f"    Trend: {trend} (first 3 avg={first_half:.3f}, last 3 avg={second_half:.3f})")
    else:
        trend = "INSUFFICIENT DATA"

    return results, {
        'n_positive': n_positive,
        'n_above_03': n_above_03,
        'n_total': len(oos_sharpes),
        'mean_oos_sharpe': np.mean(oos_sharpes),
        'median_oos_sharpe': np.median(oos_sharpes),
        'min_oos_sharpe': min(oos_sharpes),
        'max_oos_sharpe': max(oos_sharpes),
        'trend': trend,
    }


# ============================================================================
# PART 2: PARAMETER SENSITIVITY
# ============================================================================

def run_parameter_sensitivity(btc_daily, positioning, dvol):
    """Test +/-20% on each of 6 parameters, one at a time."""
    print("\n" + "=" * 72)
    print("PART 2: PARAMETER SENSITIVITY (+-20% on each parameter)")
    print("=" * 72)

    # Use 2025-01-01 to latest available for OOS
    oos_start = '2025-01-01'
    oos_end = btc_daily.index.max().strftime('%Y-%m-%d')
    print(f"  OOS period: {oos_start} to {oos_end}")

    # Default parameters
    defaults = {
        'fast_ema': 20,
        'slow_ema': 50,
        'pos_z_window': 30,
        'vrp_z_window': 60,
        'pos_high_thresh': 1.5,
        'vrp_high_thresh': 1.0,
    }

    # Parameter test grid: name -> [low, default, high]
    param_tests = {
        'fast_ema': [16, 20, 24],
        'slow_ema': [40, 50, 60],
        'pos_z_window': [24, 30, 36],
        'vrp_z_window': [48, 60, 72],
        'pos_high_thresh': [1.2, 1.5, 1.8],
        'vrp_high_thresh': [0.8, 1.0, 1.2],
    }

    results = {}
    kill_flags = []

    for param_name, values in param_tests.items():
        print(f"\n  Testing {param_name}: {values}")
        param_results = []

        for val in values:
            # Override only this parameter
            params = defaults.copy()
            params[param_name] = val
            metrics = run_strategy(
                btc_daily, positioning, dvol,
                oos_start, oos_end,
                **params
            )
            param_results.append({
                'value': val,
                'sharpe': metrics['sharpe'],
                'ann_return': metrics['ann_return'],
                'max_dd': metrics['max_dd'],
            })
            print(f"    {param_name}={val}: Sharpe={metrics['sharpe']:+.3f}, Return={metrics['ann_return']:+.1%}, MaxDD={metrics['max_dd']:.1%}")

        results[param_name] = param_results

        # Check KILL condition: any +-20% change degrades Sharpe by >30%
        default_sharpe = param_results[1]['sharpe']  # middle value is default
        for pr in [param_results[0], param_results[2]]:
            if default_sharpe > 0 and not np.isnan(pr['sharpe']) and not np.isnan(default_sharpe):
                degradation = (default_sharpe - pr['sharpe']) / abs(default_sharpe)
                if degradation > 0.30:
                    kill_flags.append({
                        'param': param_name,
                        'value': pr['value'],
                        'default_sharpe': default_sharpe,
                        'test_sharpe': pr['sharpe'],
                        'degradation_pct': degradation * 100,
                    })
                    print(f"    *** KILL FLAG: {param_name}={pr['value']} degrades Sharpe by {degradation:.1%}")

    if kill_flags:
        print(f"\n  KILL FLAGS: {len(kill_flags)} parameter perturbations degrade Sharpe >30%")
        for kf in kill_flags:
            print(f"    {kf['param']}={kf['value']}: {kf['default_sharpe']:.3f} -> {kf['test_sharpe']:.3f} ({kf['degradation_pct']:.1f}% degradation)")
    else:
        print(f"\n  No KILL flags. All +-20% perturbations maintain Sharpe within 30% of default.")

    return results, kill_flags


# ============================================================================
# PART 3: COMBINATORIAL STABILITY (4 corner cases)
# ============================================================================

def run_combinatorial_stability(btc_daily, positioning, dvol):
    """Test 4 corner case parameter combos."""
    print("\n" + "=" * 72)
    print("PART 3: COMBINATORIAL STABILITY (4 corner cases)")
    print("=" * 72)

    oos_start = '2025-01-01'
    oos_end = btc_daily.index.max().strftime('%Y-%m-%d')

    corners = {
        'Fast base + tight overlays': {
            'fast_ema': 16, 'slow_ema': 40,
            'pos_z_window': 30, 'vrp_z_window': 60,
            'pos_high_thresh': 1.2, 'vrp_high_thresh': 1.0,
        },
        'Slow base + wide overlays': {
            'fast_ema': 24, 'slow_ema': 60,
            'pos_z_window': 30, 'vrp_z_window': 60,
            'pos_high_thresh': 1.8, 'vrp_high_thresh': 1.0,
        },
        'Fast base + wide overlays': {
            'fast_ema': 16, 'slow_ema': 40,
            'pos_z_window': 30, 'vrp_z_window': 60,
            'pos_high_thresh': 1.8, 'vrp_high_thresh': 1.0,
        },
        'Slow base + tight overlays': {
            'fast_ema': 24, 'slow_ema': 60,
            'pos_z_window': 30, 'vrp_z_window': 60,
            'pos_high_thresh': 1.2, 'vrp_high_thresh': 1.0,
        },
    }

    # Also run default for comparison
    default_metrics = run_strategy(
        btc_daily, positioning, dvol, oos_start, oos_end,
        fast_ema=20, slow_ema=50, pos_z_window=30,
        vrp_z_window=60, pos_high_thresh=1.5, vrp_high_thresh=1.0,
    )
    print(f"\n  Default: Sharpe={default_metrics['sharpe']:+.3f}, Return={default_metrics['ann_return']:+.1%}, MaxDD={default_metrics['max_dd']:.1%}")

    results = {}
    for name, params in corners.items():
        metrics = run_strategy(btc_daily, positioning, dvol, oos_start, oos_end, **params)
        results[name] = metrics
        print(f"  {name}: Sharpe={metrics['sharpe']:+.3f}, Return={metrics['ann_return']:+.1%}, MaxDD={metrics['max_dd']:.1%}")

    # Check stability
    all_sharpes = [default_metrics['sharpe']] + [m['sharpe'] for m in results.values()]
    valid_sharpes = [s for s in all_sharpes if not np.isnan(s)]
    sharpe_range = max(valid_sharpes) - min(valid_sharpes) if valid_sharpes else np.nan
    sharpe_std = np.std(valid_sharpes) if valid_sharpes else np.nan

    print(f"\n  Sharpe range across corners: {sharpe_range:.3f}")
    print(f"  Sharpe std across corners: {sharpe_std:.3f}")

    return results, default_metrics, {
        'sharpe_range': sharpe_range,
        'sharpe_std': sharpe_std,
        'n_positive': sum(1 for s in valid_sharpes if s > 0),
        'n_total': len(valid_sharpes),
    }


# ============================================================================
# REPORT GENERATION
# ============================================================================

def generate_report(wf_results, wf_summary, param_results, kill_flags,
                    corner_results, default_metrics, corner_summary):
    """Generate markdown report with all results."""

    lines = []
    lines.append("# V3 Momentum Strategy: Walk-Forward & Parameter Sensitivity Results")
    lines.append("")
    lines.append(f"**Run date**: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("")
    lines.append("## Strategy Definition (V3)")
    lines.append("")
    lines.append("- **Base**: Long when 20d EMA > 50d EMA, flat otherwise. No stop losses.")
    lines.append("- **Positioning overlay**: Binance Top Trader L/S + L/S Divergence combined z-score (30d) -> 0.3x to 1.5x")
    lines.append("- **VRP overlay**: (IV - RV) z-score (60d) -> 0.3x to 1.3x")
    lines.append("- **Rebalancing**: Weekly (Monday). Cost: 10 bps round-trip. Position range: [0, 1.5x].")
    lines.append("")

    # ── PART 1: Walk-Forward ──
    lines.append("## Part 1: Walk-Forward Test")
    lines.append("")
    lines.append("6 rolling windows, each 18-month IS + 6-month OOS.")
    lines.append("")
    lines.append("| Window | IS Period | OOS Period | IS Sharpe | OOS Sharpe | IS Return | OOS Return | IS MaxDD | OOS MaxDD | OOS BH Sharpe |")
    lines.append("|--------|-----------|------------|-----------|------------|-----------|------------|----------|----------|---------------|")

    for r in wf_results:
        lines.append(
            f"| {r['window']} "
            f"| {r['is_is_start']} to {r['is_is_end']} "
            f"| {r['oos_oos_start']} to {r['oos_oos_end']} "
            f"| {r['is_sharpe']:+.3f} "
            f"| {r['oos_sharpe']:+.3f} "
            f"| {r['is_return']:+.1%} "
            f"| {r['oos_return']:+.1%} "
            f"| {r['is_maxdd']:.1%} "
            f"| {r['oos_maxdd']:.1%} "
            f"| {r['oos_bh_sharpe']:+.3f} |"
        )

    lines.append("")
    lines.append("### Walk-Forward Summary")
    lines.append("")
    lines.append(f"- Windows with positive OOS Sharpe: **{wf_summary['n_positive']}/{wf_summary['n_total']}**")
    lines.append(f"- Windows with OOS Sharpe > 0.3: **{wf_summary['n_above_03']}/{wf_summary['n_total']}**")
    lines.append(f"- OOS Sharpe range: {wf_summary['min_oos_sharpe']:.3f} to {wf_summary['max_oos_sharpe']:.3f}")
    lines.append(f"- OOS Sharpe mean: {wf_summary['mean_oos_sharpe']:.3f}")
    lines.append(f"- OOS Sharpe median: {wf_summary['median_oos_sharpe']:.3f}")
    lines.append(f"- Trend: **{wf_summary['trend']}**")
    lines.append("")

    # Efficiency ratio
    is_sharpes = [r['is_sharpe'] for r in wf_results if not np.isnan(r['is_sharpe'])]
    oos_sharpes = [r['oos_sharpe'] for r in wf_results if not np.isnan(r['oos_sharpe'])]
    if is_sharpes and oos_sharpes:
        efficiency = np.mean(oos_sharpes) / np.mean(is_sharpes) if np.mean(is_sharpes) != 0 else np.nan
        lines.append(f"- IS/OOS efficiency ratio: {efficiency:.2f} (1.0 = no degradation, <0.5 = suspect overfit)")
        lines.append("")

    # ── PART 2: Parameter Sensitivity ──
    lines.append("## Part 2: Parameter Sensitivity")
    lines.append("")
    lines.append("Each parameter tested at +-20% of default, others held constant.")
    lines.append("OOS period: 2025-01-01 to latest.")
    lines.append("")
    lines.append("| Parameter | Low Value | Low Sharpe | Default Value | Default Sharpe | High Value | High Sharpe | Max Degradation |")
    lines.append("|-----------|----------|------------|---------------|----------------|------------|-------------|-----------------|")

    for param_name, param_res in param_results.items():
        low = param_res[0]
        default = param_res[1]
        high = param_res[2]

        # Max degradation from default
        max_deg = 0
        for pr in [low, high]:
            if default['sharpe'] > 0 and not np.isnan(pr['sharpe']):
                deg = (default['sharpe'] - pr['sharpe']) / abs(default['sharpe'])
                max_deg = max(max_deg, deg)

        lines.append(
            f"| {param_name} "
            f"| {low['value']} | {low['sharpe']:+.3f} "
            f"| {default['value']} | {default['sharpe']:+.3f} "
            f"| {high['value']} | {high['sharpe']:+.3f} "
            f"| {max_deg:.1%} |"
        )

    lines.append("")

    if kill_flags:
        lines.append("### KILL Flags (>30% Sharpe degradation from +-20% parameter change)")
        lines.append("")
        for kf in kill_flags:
            lines.append(f"- **{kf['param']}={kf['value']}**: Sharpe {kf['default_sharpe']:.3f} -> {kf['test_sharpe']:.3f} ({kf['degradation_pct']:.1f}% degradation)")
        lines.append("")
    else:
        lines.append("### No KILL flags. All +-20% perturbations maintain Sharpe within 30%.")
        lines.append("")

    # Detailed sensitivity per param (return + maxdd)
    lines.append("### Detailed Sensitivity (Return & MaxDD)")
    lines.append("")
    lines.append("| Parameter | Value | OOS Sharpe | OOS Return | OOS MaxDD |")
    lines.append("|-----------|-------|------------|------------|-----------|")

    for param_name, param_res in param_results.items():
        for pr in param_res:
            marker = " (default)" if pr == param_res[1] else ""
            lines.append(
                f"| {param_name} | {pr['value']}{marker} "
                f"| {pr['sharpe']:+.3f} "
                f"| {pr['ann_return']:+.1%} "
                f"| {pr['max_dd']:.1%} |"
            )

    lines.append("")

    # ── PART 3: Combinatorial Stability ──
    lines.append("## Part 3: Combinatorial Stability")
    lines.append("")
    lines.append("4 extreme corner cases tested on OOS period (2025-01-01 to latest).")
    lines.append("")
    lines.append("| Configuration | Fast EMA | Slow EMA | Pos Z-Thresh | OOS Sharpe | OOS Return | OOS MaxDD |")
    lines.append("|---------------|----------|----------|-------------|------------|------------|-----------|")

    lines.append(
        f"| Default (20/50, thresh=1.5) | 20 | 50 | 1.5 "
        f"| {default_metrics['sharpe']:+.3f} "
        f"| {default_metrics['ann_return']:+.1%} "
        f"| {default_metrics['max_dd']:.1%} |"
    )

    for name, metrics in corner_results.items():
        lines.append(
            f"| {name} "
            f"| - | - | - "
            f"| {metrics['sharpe']:+.3f} "
            f"| {metrics['ann_return']:+.1%} "
            f"| {metrics['max_dd']:.1%} |"
        )

    lines.append("")
    lines.append(f"- Sharpe range across all configurations: {corner_summary['sharpe_range']:.3f}")
    lines.append(f"- Sharpe std across all configurations: {corner_summary['sharpe_std']:.3f}")
    lines.append(f"- Configurations with positive Sharpe: {corner_summary['n_positive']}/{corner_summary['n_total']}")
    lines.append("")

    # ── VERDICT ──
    lines.append("## Verdict")
    lines.append("")

    # Decision logic
    # ROBUST: majority of WF windows positive, no kill flags, corners stable
    # FRAGILE: some WF windows positive but kill flags or high sensitivity
    # KILL: majority negative WF, kill flags, or very unstable corners

    wf_score = 0
    if wf_summary['n_positive'] >= 4:
        wf_score = 2
    elif wf_summary['n_positive'] >= 3:
        wf_score = 1
    else:
        wf_score = 0

    param_score = 2 if not kill_flags else 0

    corner_score = 0
    if corner_summary['n_positive'] >= 4:
        corner_score = 2
    elif corner_summary['n_positive'] >= 3:
        corner_score = 1
    else:
        corner_score = 0

    total_score = wf_score + param_score + corner_score

    if total_score >= 5:
        verdict = "ROBUST"
        explanation = (
            f"Walk-forward: {wf_summary['n_positive']}/{wf_summary['n_total']} windows positive OOS "
            f"(mean Sharpe {wf_summary['mean_oos_sharpe']:.3f}). "
            f"Parameter sensitivity: no KILL flags (all +-20% perturbations within tolerance). "
            f"Corner cases: {corner_summary['n_positive']}/{corner_summary['n_total']} configurations positive."
        )
    elif total_score >= 3:
        verdict = "FRAGILE"
        issues = []
        if wf_score < 2:
            issues.append(f"walk-forward inconsistent ({wf_summary['n_positive']}/{wf_summary['n_total']} positive)")
        if kill_flags:
            issues.append(f"{len(kill_flags)} parameter KILL flag(s)")
        if corner_score < 2:
            issues.append(f"corner case instability ({corner_summary['n_positive']}/{corner_summary['n_total']} positive)")
        explanation = (
            f"Strategy shows some positive signals but has weaknesses: {'; '.join(issues)}. "
            f"Mean OOS Sharpe: {wf_summary['mean_oos_sharpe']:.3f}. "
            f"May work in favorable regimes but is not robust enough for unconditional production deployment."
        )
    else:
        verdict = "KILL"
        issues = []
        if wf_score == 0:
            issues.append(f"walk-forward failure ({wf_summary['n_positive']}/{wf_summary['n_total']} positive)")
        if kill_flags:
            issues.append(f"{len(kill_flags)} parameter KILL flag(s)")
        if corner_score == 0:
            issues.append(f"corner case failure ({corner_summary['n_positive']}/{corner_summary['n_total']} positive)")
        explanation = (
            f"Strategy fails robustness checks: {'; '.join(issues)}. "
            f"Mean OOS Sharpe: {wf_summary['mean_oos_sharpe']:.3f}. "
            f"Not suitable for production deployment."
        )

    lines.append(f"### **{verdict}**")
    lines.append("")
    lines.append(explanation)
    lines.append("")

    # Scoring breakdown
    lines.append("### Scoring Breakdown")
    lines.append("")
    lines.append(f"| Dimension | Score | Max | Assessment |")
    lines.append(f"|-----------|-------|-----|------------|")
    lines.append(f"| Walk-Forward | {wf_score} | 2 | {wf_summary['n_positive']}/{wf_summary['n_total']} positive OOS windows |")
    lines.append(f"| Parameter Sensitivity | {param_score} | 2 | {len(kill_flags)} KILL flags |")
    lines.append(f"| Corner Cases | {corner_score} | 2 | {corner_summary['n_positive']}/{corner_summary['n_total']} positive configs |")
    lines.append(f"| **Total** | **{total_score}** | **6** | {'ROBUST' if total_score >= 5 else 'FRAGILE' if total_score >= 3 else 'KILL'} |")
    lines.append("")

    lines.append("### Interpretation")
    lines.append("")
    lines.append("- Score >= 5: ROBUST -- safe for production")
    lines.append("- Score 3-4: FRAGILE -- needs further tuning or conditional deployment")
    lines.append("- Score < 3: KILL -- do not deploy")
    lines.append("")

    return '\n'.join(lines), verdict


# ============================================================================
# MAIN
# ============================================================================

def main():
    print("=" * 72)
    print("V3 MOMENTUM STRATEGY: WALK-FORWARD & PARAMETER SENSITIVITY")
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

    # Part 1: Walk-Forward
    wf_results, wf_summary = run_walk_forward(btc_daily, positioning, dvol)

    # Part 2: Parameter Sensitivity
    param_results, kill_flags = run_parameter_sensitivity(btc_daily, positioning, dvol)

    # Part 3: Combinatorial Stability
    corner_results, default_metrics, corner_summary = run_combinatorial_stability(btc_daily, positioning, dvol)

    # Generate report
    print("\n" + "=" * 72)
    print("GENERATING REPORT")
    print("=" * 72)

    report, verdict = generate_report(
        wf_results, wf_summary,
        param_results, kill_flags,
        corner_results, default_metrics, corner_summary,
    )

    report_path = OUTPUT_DIR / 'v3_walkforward_sensitivity_results.md'
    with open(report_path, 'w') as f:
        f.write(report)
    print(f"\nReport saved to: {report_path}")

    print(f"\n{'=' * 72}")
    print(f"FINAL VERDICT: {verdict}")
    print(f"{'=' * 72}")


if __name__ == '__main__':
    main()
