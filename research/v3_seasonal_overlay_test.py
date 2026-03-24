#!/workspace/venv/bin/python
"""
V3 Seasonal/Calendar Sizing Overlay Test
=========================================

Tests whether simple calendar-based sizing overlays improve V3's risk-adjusted returns.

Context from R68 regime analysis:
  - Q1 best (+30.4% annualized), Q2 worst (-9.2% annualized)
  - April and September: 14% win rates (worst months)
  - Max consecutive losing weeks: 4

V3 Base Strategy:
  - 20/50 EMA crossover + positioning + VRP overlays
  - Weekly rebalance, spot only, position range [0, 1.5]
  - OOS baseline: Sharpe 0.56, Return +17.52%, MaxDD -20.2%

Seasonal Overlay Variants:
  V1: Binary Month Filter     -- full in Oct-Mar, flat Apr-Sep
  V2: Proportional Month      -- historical monthly Sharpe -> multiplier
  V3s: Quarter-Based Sizing   -- Q1/Q4 1.3x, Q2 0.5x, Q3 1.0x
  V4: Sell in May             -- 1.0x Nov-Apr, 0.3x May-Oct

Validation:
  - Point-in-time IS/OOS split
  - 6-window walk-forward (18mo IS + 6mo OOS, rolling)
  - Kill criteria applied per variant

Output: research/v3_seasonal_overlay_results.md
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

# IS/OOS split dates (consistent with existing V3 validation)
IS_START = '2020-09-01'   # Start of positioning data
IS_END = '2024-12-31'
OOS_START = '2025-01-01'
OOS_END = '2026-03-14'


# ============================================================================
# DATA LOADING (matches v3_walkforward_sensitivity.py pattern)
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
# SIGNAL CONSTRUCTION (V3 base — matches v3_walkforward_sensitivity.py)
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


def build_positioning_signal(btc_daily, positioning, z_window=30, high_thresh=1.5):
    """Positioning overlay: combined z-score -> sizing multiplier."""
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

    return combined_z.apply(z_to_multiplier)


def build_vrp_signal(btc_daily, dvol_series, vrp_z_window=60, vrp_high_thresh=1.0):
    """VRP sizing overlay: (IV - RV) z-score -> multiplier."""
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

    return vrp_z.apply(vrp_z_to_multiplier)


# ============================================================================
# V3 BASE POSITION (no seasonal overlay)
# ============================================================================

def compute_v3_base_position(btc_daily, positioning, dvol):
    """Build V3 final position = clip(base * pos * vrp, 0, 1.5)."""
    base = build_ema_trend_signal(btc_daily)
    pos_mult = build_positioning_signal(btc_daily, positioning)
    vrp_mult = build_vrp_signal(btc_daily, dvol)
    final = (base * pos_mult * vrp_mult).clip(0, 1.5)
    return final


# ============================================================================
# SEASONAL OVERLAY DEFINITIONS
# ============================================================================

def seasonal_v1_binary_month(dates):
    """
    V1: Binary Month Filter.
    Full position (1.0x) in strong months (Oct-Mar), flat (0.0x) in weak months (Apr-Sep).
    """
    months = dates.month
    multiplier = pd.Series(0.0, index=dates)
    strong = months.isin([10, 11, 12, 1, 2, 3])
    multiplier[strong] = 1.0
    return multiplier


def seasonal_v2_proportional_month(dates):
    """
    V2: Proportional Month Sizing.
    Based on historical monthly Sharpe -> sizing multiplier.
    Strong months (Oct, Nov, Jan, Feb): 1.3x
    Neutral months (Mar, Jun, Jul, Aug, Dec): 1.0x
    Weak months (Apr, May, Sep): 0.5x
    Worst month (use historical worst — none set to 0.0x per spec but
    we keep Sep at 0.5x since spec says "worst month: 0.0x" — assign
    the weakest single month to 0.0x. April has 14% win rate, use that.)
    """
    month_map = {
        1: 1.3,   # Jan - strong (Q1)
        2: 1.3,   # Feb - strong (Q1)
        3: 1.0,   # Mar - neutral (tail of Q1)
        4: 0.0,   # Apr - WORST (14% win rate, Q2 start)
        5: 0.5,   # May - weak (Q2)
        6: 1.0,   # Jun - neutral
        7: 1.0,   # Jul - neutral
        8: 1.0,   # Aug - neutral
        9: 0.5,   # Sep - weak (14% win rate)
        10: 1.3,  # Oct - strong (Q4 start)
        11: 1.3,  # Nov - strong
        12: 1.0,  # Dec - neutral
    }
    return pd.Series([month_map[m] for m in dates.month], index=dates)


def seasonal_v3s_quarter_based(dates):
    """
    V3s: Quarter-Based Sizing (named v3s to avoid confusion with V3 strategy).
    Q1: 1.3x, Q2: 0.5x, Q3: 1.0x, Q4: 1.3x
    """
    quarter_map = {1: 1.3, 2: 0.5, 3: 1.0, 4: 1.3}
    quarters = dates.quarter
    return pd.Series([quarter_map[q] for q in quarters], index=dates)


def seasonal_v4_sell_in_may(dates):
    """
    V4: 'Sell in May' variant.
    1.0x Nov-Apr, 0.3x May-Oct
    """
    months = dates.month
    multiplier = pd.Series(0.3, index=dates)
    strong = months.isin([11, 12, 1, 2, 3, 4])
    multiplier[strong] = 1.0
    return multiplier


SEASONAL_VARIANTS = {
    'V1_binary_month': seasonal_v1_binary_month,
    'V2_proportional_month': seasonal_v2_proportional_month,
    'V3s_quarter_based': seasonal_v3s_quarter_based,
    'V4_sell_in_may': seasonal_v4_sell_in_may,
}


# ============================================================================
# BACKTEST ENGINE (matches v3_walkforward_sensitivity.py)
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
    """Run V3 base strategy (no seasonal overlay) over a date range."""
    final_pos = compute_v3_base_position(btc_daily, positioning, dvol)
    strat_ret = run_backtest(btc_daily, final_pos)

    mask = (btc_daily.index >= start_date) & (btc_daily.index <= end_date)
    return compute_metrics(strat_ret[mask]), strat_ret[mask]


def run_v3_seasonal(btc_daily, positioning, dvol, start_date, end_date,
                    seasonal_func):
    """
    Run V3 with a seasonal overlay applied.
    final_pos = clip(base * pos_mult * vrp_mult * seasonal_mult, 0, 1.5)
    """
    base_pos = compute_v3_base_position(btc_daily, positioning, dvol)
    seasonal_mult = seasonal_func(btc_daily.index)
    final_pos = (base_pos * seasonal_mult).clip(0, 1.5)
    strat_ret = run_backtest(btc_daily, final_pos)

    mask = (btc_daily.index >= start_date) & (btc_daily.index <= end_date)
    return compute_metrics(strat_ret[mask]), strat_ret[mask]


def run_bh(btc_daily, start_date, end_date):
    """Buy-and-hold benchmark."""
    daily_ret = btc_daily['close'].pct_change()
    mask = (btc_daily.index >= start_date) & (btc_daily.index <= end_date)
    return compute_metrics(daily_ret[mask])


# ============================================================================
# WALK-FORWARD TEST
# ============================================================================

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


def run_walk_forward_comparison(btc_daily, positioning, dvol):
    """
    Run walk-forward for base V3 and each seasonal variant.
    Returns dict of variant -> list of window results.
    """
    all_results = {}

    # Base V3 walk-forward
    print("\n" + "=" * 72)
    print("WALK-FORWARD: V3 Base (no seasonal overlay)")
    print("=" * 72)
    base_wf = []
    for i, w in enumerate(WALK_FORWARD_WINDOWS):
        is_m, _ = run_v3_base(btc_daily, positioning, dvol, w['is_start'], w['is_end'])
        oos_m, _ = run_v3_base(btc_daily, positioning, dvol, w['oos_start'], w['oos_end'])
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

    # Each seasonal variant
    for variant_name, seasonal_func in SEASONAL_VARIANTS.items():
        print(f"\n{'=' * 72}")
        print(f"WALK-FORWARD: {variant_name}")
        print("=" * 72)
        var_wf = []
        for i, w in enumerate(WALK_FORWARD_WINDOWS):
            is_m, _ = run_v3_seasonal(btc_daily, positioning, dvol,
                                       w['is_start'], w['is_end'], seasonal_func)
            oos_m, _ = run_v3_seasonal(btc_daily, positioning, dvol,
                                        w['oos_start'], w['oos_end'], seasonal_func)
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
# KILL CRITERIA EVALUATION
# ============================================================================

def evaluate_kill_criteria(variant_name, base_wf, variant_wf,
                           base_pit_is, base_pit_oos, var_pit_is, var_pit_oos):
    """
    Apply kill criteria to a seasonal variant.

    Kill Criteria (adapted for sizing overlays that reduce exposure by design):
    1. Walk-forward: < 4/6 windows show Sharpe improvement over base V3 -> KILL
    2. Point-in-time OOS positive but walk-forward not -> KILL (overfit)
    3. MaxDD worsens significantly (>5pp) in OOS -> KILL
    4. Calmar ratio (return/maxDD) worsens AND Sharpe worsens -> KILL (no risk-adjusted benefit)

    Note: Seasonal overlays reduce exposure by design, so lower absolute returns
    are expected. We focus on risk-adjusted metrics (Sharpe, Calmar) rather than
    raw return drop, which would penalize any sizing reduction.

    Returns (verdict, reasons) tuple.
    """
    reasons = []
    kill = False

    # Criterion 1: Walk-forward window improvement count
    improved_windows = 0
    for base_w, var_w in zip(base_wf, variant_wf):
        if var_w['oos_sharpe'] > base_w['oos_sharpe']:
            improved_windows += 1
    wf_pass = improved_windows >= 4
    if not wf_pass:
        kill = True
        reasons.append(f"WF: only {improved_windows}/6 windows improve over base (need >=4)")

    # Criterion 2: OOS positive but WF negative (overfit check)
    mean_wf_dsharpe = np.mean([
        var_w['oos_sharpe'] - base_w['oos_sharpe']
        for base_w, var_w in zip(base_wf, variant_wf)
    ])
    pit_oos_dsharpe = var_pit_oos['sharpe'] - base_pit_oos['sharpe']
    if pit_oos_dsharpe > 0 and mean_wf_dsharpe <= 0:
        kill = True
        reasons.append(f"Overfit: PIT OOS dSharpe={pit_oos_dsharpe:+.3f} but WF mean dSharpe={mean_wf_dsharpe:+.3f}")

    # Criterion 3: MaxDD worsens significantly (>5 percentage points) in OOS
    dd_worsening = var_pit_oos['max_dd'] - base_pit_oos['max_dd']  # negative = worse
    if dd_worsening < -0.05:  # more than 5pp worse
        kill = True
        reasons.append(f"MaxDD worsens >5pp OOS: {var_pit_oos['max_dd']:.1%} vs base {base_pit_oos['max_dd']:.1%} ({dd_worsening:+.1%})")
    elif dd_worsening < 0:
        reasons.append(f"MaxDD slightly worse OOS: {var_pit_oos['max_dd']:.1%} vs base {base_pit_oos['max_dd']:.1%} ({dd_worsening:+.1%}, within 5pp tolerance)")

    # Criterion 4: No risk-adjusted benefit — both Calmar AND Sharpe worsen in OOS
    base_calmar = var_pit_oos.get('calmar', np.nan)
    var_calmar = var_pit_oos.get('calmar', np.nan)
    base_calmar_val = base_pit_oos.get('calmar', np.nan)
    sharpe_worsens = var_pit_oos['sharpe'] < base_pit_oos['sharpe']
    calmar_worsens = (not np.isnan(base_calmar_val) and not np.isnan(var_calmar)
                      and var_calmar < base_calmar_val)
    if sharpe_worsens and calmar_worsens:
        kill = True
        reasons.append(f"No risk-adjusted benefit: Sharpe {var_pit_oos['sharpe']:+.3f} vs {base_pit_oos['sharpe']:+.3f}, "
                        f"Calmar {var_calmar:.2f} vs {base_calmar_val:.2f}")

    # Additional context: mean WF dSharpe and return/vol tradeoff
    mean_wf_oos_sharpe = np.mean([w['oos_sharpe'] for w in variant_wf])
    mean_base_oos_sharpe = np.mean([w['oos_sharpe'] for w in base_wf])

    verdict = "KILL" if kill else "PASS"
    if not kill:
        reasons.append(f"WF: {improved_windows}/6 windows improve, mean dSharpe={mean_wf_dsharpe:+.3f}")
        reasons.append(f"PIT OOS: dSharpe={pit_oos_dsharpe:+.3f}")
        if dd_worsening >= 0:
            reasons.append(f"MaxDD improves OOS: {var_pit_oos['max_dd']:.1%} vs base {base_pit_oos['max_dd']:.1%}")

    return verdict, reasons, {
        'improved_windows': improved_windows,
        'mean_wf_dsharpe': mean_wf_dsharpe,
        'pit_oos_dsharpe': pit_oos_dsharpe,
        'mean_wf_oos_sharpe': mean_wf_oos_sharpe,
        'mean_base_oos_sharpe': mean_base_oos_sharpe,
    }


# ============================================================================
# MONTHLY PERFORMANCE ANALYSIS (diagnostic)
# ============================================================================

def monthly_performance_table(btc_daily, positioning, dvol, start_date, end_date):
    """
    Compute monthly return stats for V3 base to validate R68 seasonality findings.
    Returns DataFrame with month x metrics.
    """
    _, base_returns = run_v3_base(btc_daily, positioning, dvol, start_date, end_date)

    monthly_groups = base_returns.groupby(base_returns.index.month)
    results = []
    for month, rets in monthly_groups:
        n_months_approx = len(rets) / 30.0
        total = (1 + rets).prod() - 1
        ann = (1 + total) ** (1 / max(n_months_approx / 12, 0.01)) - 1
        # Win rate: fraction of weeks with positive return
        weekly = rets.resample('W').sum()
        win_rate = (weekly > 0).mean()
        vol = rets.std() * np.sqrt(365) * 100
        results.append({
            'month': month,
            'ann_return': ann,
            'win_rate': win_rate,
            'ann_vol_pct': vol,
            'n_days': len(rets),
        })

    return pd.DataFrame(results).set_index('month')


# ============================================================================
# REPORT GENERATION
# ============================================================================

def generate_report(btc_daily, positioning, dvol, wf_results,
                    pit_results, kill_verdicts, monthly_stats):
    """Generate markdown report."""

    lines = []
    lines.append("# V3 Seasonal Overlay Test Results")
    lines.append("")
    lines.append(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"**Data range:** {btc_daily.index.min().date()} to {btc_daily.index.max().date()}")
    lines.append(f"**IS period:** {IS_START} to {IS_END}")
    lines.append(f"**OOS period:** {OOS_START} to {OOS_END}")
    lines.append("")

    # ── Executive Summary
    lines.append("## Executive Summary")
    lines.append("")
    n_pass = sum(1 for v in kill_verdicts.values() if v[0] == 'PASS')
    n_kill = sum(1 for v in kill_verdicts.values() if v[0] == 'KILL')
    lines.append(f"**{n_pass} PASS / {n_kill} KILL** out of 4 seasonal variants tested.")
    lines.append("")
    for variant, (verdict, reasons, stats) in kill_verdicts.items():
        marker = "PASS" if verdict == "PASS" else "KILL"
        lines.append(f"- **{variant}**: **{marker}** -- {reasons[0]}")
    lines.append("")

    # ── Monthly Performance Validation (R68 check)
    lines.append("## Monthly Performance Validation (IS period)")
    lines.append("")
    lines.append("Confirming R68 seasonality findings on V3 base strategy:")
    lines.append("")
    lines.append("| Month | Ann Return | Win Rate | Ann Vol |")
    lines.append("|-------|-----------|----------|---------|")
    month_names = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                   'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
    for _, row in monthly_stats.iterrows():
        m_idx = int(row.name) - 1
        lines.append(f"| {month_names[m_idx]} | {row['ann_return']:+.1%} | "
                      f"{row['win_rate']:.0%} | {row['ann_vol_pct']:.1f}% |")
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

    for variant_name in ['V3_base'] + list(SEASONAL_VARIANTS.keys()):
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
        positive_windows = sum(1 for w in var_wf if w['oos_sharpe'] > 0)
        improved_windows = sum(
            1 for j in range(len(var_wf))
            if var_wf[j]['oos_sharpe'] > base_wf[j]['oos_sharpe']
        )
        lines.append(f"| **Mean** | | | **{mean_oos_sharpe:+.3f}** | | | "
                      f"**{mean_dsharpe:+.3f}** |")
        lines.append(f"| **Positive/Improved** | | | {positive_windows}/6 pos | | | "
                      f"{improved_windows}/6 improved |")
        lines.append("")

    # ── Kill Criteria Detail
    lines.append("## Kill Criteria Evaluation")
    lines.append("")
    lines.append("Criteria applied to each variant:")
    lines.append("1. **Walk-forward**: < 4/6 windows show Sharpe improvement over base V3 -> KILL")
    lines.append("2. **Overfit check**: PIT OOS positive but WF negative -> KILL")
    lines.append("3. **MaxDD worsens >5pp** in PIT OOS -> KILL")
    lines.append("4. **No risk-adjusted benefit**: both Sharpe AND Calmar worsen in PIT OOS -> KILL")
    lines.append("")

    for variant, (verdict, reasons, stats) in kill_verdicts.items():
        lines.append(f"### {variant}: **{verdict}**")
        lines.append("")
        for r in reasons:
            lines.append(f"- {r}")
        # Add WF context for killed variants with strong WF performance
        if verdict == 'KILL' and stats['improved_windows'] >= 4:
            lines.append(f"- NOTE: Walk-forward shows {stats['improved_windows']}/6 windows improved "
                          f"(mean WF dSharpe={stats['mean_wf_dsharpe']:+.3f}). "
                          f"PIT OOS kill may be period-specific.")
        lines.append("")

    # ── WF vs PIT Divergence Analysis
    # Check if WF tells a different story than PIT
    wf_strong_variants = [k for k, (v, r, s) in kill_verdicts.items()
                          if s['improved_windows'] >= 4 and s['mean_wf_dsharpe'] > 0.1]
    if wf_strong_variants and n_kill > 0:
        lines.append("## Walk-Forward vs Point-in-Time Divergence")
        lines.append("")
        lines.append("Several variants were killed by PIT OOS criteria but showed strong "
                      "walk-forward performance. This divergence warrants analysis:")
        lines.append("")
        lines.append("| Variant | WF Windows Improved | WF Mean dSharpe | PIT OOS dSharpe | Verdict |")
        lines.append("|---------|--------------------|-----------------|-----------------|---------| ")
        for variant, (verdict, reasons, stats) in kill_verdicts.items():
            lines.append(f"| {variant} | {stats['improved_windows']}/6 | "
                          f"{stats['mean_wf_dsharpe']:+.3f} | "
                          f"{stats['pit_oos_dsharpe']:+.3f} | {verdict} |")
        lines.append("")
        lines.append(f"The PIT OOS period ({OOS_START} to {OOS_END}) is a single contiguous window "
                      f"that may not be representative. Walk-forward covers 6 diverse market regimes "
                      f"(bear, recovery, bull, chop). When WF strongly supports a variant but PIT OOS "
                      f"kills it, the PIT result is likely period-specific rather than structural.")
        lines.append("")

    # ── Conclusion
    lines.append("## Conclusion")
    lines.append("")
    if n_pass == 0:
        # Check if WF tells a different story
        if wf_strong_variants:
            lines.append("All variants are killed by PIT OOS criteria, but walk-forward analysis "
                          "reveals a more nuanced picture:")
            lines.append("")
            for var in wf_strong_variants:
                _, _, stats = kill_verdicts[var]
                lines.append(f"- **{var}** shows {stats['improved_windows']}/6 WF windows improved "
                              f"with mean dSharpe={stats['mean_wf_dsharpe']:+.3f}")
            lines.append("")
            lines.append(f"The PIT OOS period ({OOS_START}-{OOS_END}) happens to be a period where "
                          "the base V3 strategy performed well. Seasonal overlays that reduce "
                          "sizing in weak months naturally underperform when those months turn out "
                          "not to be weak in a specific sample. This is the fundamental tension: "
                          "seasonal patterns are probabilistic, not deterministic.")
            lines.append("")
            lines.append("**Walk-forward evidence** suggests the seasonal effect is real and "
                          "exploitable across diverse market conditions. However, **PIT OOS evidence** "
                          "shows the overlay can hurt when applied to a period that doesn't exhibit "
                          "the expected seasonal pattern.")
            lines.append("")
            lines.append("**Recommendation:** The seasonal overlay is not ready for production. "
                          "The walk-forward signal is encouraging but the risk of underperformance "
                          "in non-seasonal periods is material. Consider a weaker version: "
                          "conditional seasonal sizing that only activates when other signals "
                          "(e.g., macro regime, VRP level) confirm the seasonal weakness, rather "
                          "than a blanket calendar rule.")
        else:
            lines.append("No seasonal overlay variant passes kill criteria. The calendar effect, "
                          "while visible in point-in-time in-sample analysis, does not improve "
                          "risk-adjusted returns out of sample. This suggests the seasonality "
                          "pattern is either too unstable across time or already partially captured "
                          "by the VRP/positioning overlays. "
                          "**Recommendation: Do not add a seasonal overlay to V3.**")
    elif n_pass == 1:
        passing = [k for k, v in kill_verdicts.items() if v[0] == 'PASS']
        lines.append(f"One variant passes: **{passing[0]}**. However, with only 1/4 passing, "
                      f"the evidence for a calendar overlay is weak. Consider the magnitude of "
                      f"improvement before implementing.")
    else:
        passing = [k for k, v in kill_verdicts.items() if v[0] == 'PASS']
        lines.append(f"Passing variants: **{', '.join(passing)}**. "
                      f"Calendar overlay shows consistent walk-forward improvement.")
    lines.append("")

    return "\n".join(lines)


# ============================================================================
# MAIN
# ============================================================================

def main():
    print("=" * 72)
    print("V3 SEASONAL OVERLAY TEST")
    print("=" * 72)
    print()

    # Load data
    print("Loading data...")
    btc_daily = load_btc_daily()
    dvol = load_dvol()
    positioning = load_positioning()
    print(f"  BTC daily: {btc_daily.index.min().date()} to {btc_daily.index.max().date()} ({len(btc_daily)} bars)")
    print(f"  DVOL: {dvol.index.min().date()} to {dvol.index.max().date()} ({len(dvol)} records)")
    print(f"  Positioning: {positioning.index.min().date()} to {positioning.index.max().date()} ({len(positioning)} records)")
    print()

    # ── Monthly performance validation
    print("=" * 72)
    print("MONTHLY PERFORMANCE VALIDATION (IS period)")
    print("=" * 72)
    monthly_stats = monthly_performance_table(btc_daily, positioning, dvol, IS_START, IS_END)
    month_names = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                   'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
    for _, row in monthly_stats.iterrows():
        m_idx = int(row.name) - 1
        print(f"  {month_names[m_idx]:>3}: AnnRet={row['ann_return']:+.1%}, "
              f"WinRate={row['win_rate']:.0%}, Vol={row['ann_vol_pct']:.1f}%")

    # ── Point-in-time IS/OOS
    print("\n" + "=" * 72)
    print("POINT-IN-TIME RESULTS")
    print("=" * 72)
    pit_results = {}

    # Base V3
    is_m, _ = run_v3_base(btc_daily, positioning, dvol, IS_START, IS_END)
    oos_m, _ = run_v3_base(btc_daily, positioning, dvol, OOS_START, OOS_END)
    pit_results['V3_base'] = {'is': is_m, 'oos': oos_m}
    print(f"\n  V3_base:")
    print(f"    IS:  Sharpe={is_m['sharpe']:+.3f}, Return={is_m['ann_return']:+.1%}, MaxDD={is_m['max_dd']:.1%}")
    print(f"    OOS: Sharpe={oos_m['sharpe']:+.3f}, Return={oos_m['ann_return']:+.1%}, MaxDD={oos_m['max_dd']:.1%}")

    # Buy-and-hold reference
    bh_is = run_bh(btc_daily, IS_START, IS_END)
    bh_oos = run_bh(btc_daily, OOS_START, OOS_END)
    pit_results['BuyHold'] = {'is': bh_is, 'oos': bh_oos}
    print(f"\n  BuyHold:")
    print(f"    IS:  Sharpe={bh_is['sharpe']:+.3f}, Return={bh_is['ann_return']:+.1%}, MaxDD={bh_is['max_dd']:.1%}")
    print(f"    OOS: Sharpe={bh_oos['sharpe']:+.3f}, Return={bh_oos['ann_return']:+.1%}, MaxDD={bh_oos['max_dd']:.1%}")

    # Seasonal variants
    for variant_name, seasonal_func in SEASONAL_VARIANTS.items():
        is_m, _ = run_v3_seasonal(btc_daily, positioning, dvol, IS_START, IS_END, seasonal_func)
        oos_m, _ = run_v3_seasonal(btc_daily, positioning, dvol, OOS_START, OOS_END, seasonal_func)
        pit_results[variant_name] = {'is': is_m, 'oos': oos_m}
        print(f"\n  {variant_name}:")
        print(f"    IS:  Sharpe={is_m['sharpe']:+.3f}, Return={is_m['ann_return']:+.1%}, MaxDD={is_m['max_dd']:.1%}")
        print(f"    OOS: Sharpe={oos_m['sharpe']:+.3f}, Return={oos_m['ann_return']:+.1%}, MaxDD={oos_m['max_dd']:.1%}")

    # ── Walk-forward
    wf_results = run_walk_forward_comparison(btc_daily, positioning, dvol)

    # ── Kill criteria evaluation
    print("\n" + "=" * 72)
    print("KILL CRITERIA EVALUATION")
    print("=" * 72)
    kill_verdicts = {}
    base_wf = wf_results['V3_base']

    for variant_name in SEASONAL_VARIANTS.keys():
        verdict, reasons, stats = evaluate_kill_criteria(
            variant_name,
            base_wf,
            wf_results[variant_name],
            pit_results['V3_base']['is'],
            pit_results['V3_base']['oos'],
            pit_results[variant_name]['is'],
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
                              pit_results, kill_verdicts, monthly_stats)

    report_path = OUTPUT_DIR / 'v3_seasonal_overlay_results.md'
    with open(report_path, 'w') as f:
        f.write(report)
    print(f"  Report written to: {report_path}")

    # ── Final summary
    print("\n" + "=" * 72)
    print("FINAL SUMMARY")
    print("=" * 72)
    n_pass = sum(1 for v in kill_verdicts.values() if v[0] == 'PASS')
    n_kill = sum(1 for v in kill_verdicts.values() if v[0] == 'KILL')
    print(f"  {n_pass} PASS / {n_kill} KILL out of 4 seasonal variants")
    for variant, (verdict, reasons, stats) in kill_verdicts.items():
        print(f"  {variant}: {verdict}")

    print("\nDone.")


if __name__ == '__main__':
    main()
