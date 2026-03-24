#!/workspace/venv/bin/python
"""
R69: V3 Range Filter Test — Can we reduce RANGE regime losses?

V3 Strategy Definition:
  Base (V1): Long when 20d EMA > 50d EMA, flat otherwise. NO stop losses.
  Positioning overlay: Binance Top Trader L/S + L/S Divergence combined z-score (30d rolling)
    -> sizing multiplier (z>1.5->0.3x, z>0.5->0.5x, neutral->1.0x, z<-0.5->1.3x, z<-1.5->1.5x)
  VRP overlay: (IV - RV) z-score over 60d
    -> sizing multiplier (z>1->1.3x, z>-0.5->1.0x, z>-1.5->0.5x, z<-1.5->0.3x)
  Rebalancing: Weekly (Monday). Cost: 10 bps round-trip. Position range: 0 to 1.5x.

Known weakness: RANGE regime Sharpe = -1.01 (IS), -1.06 (OOS).
Goal: Add a range-market filter that reduces RANGE losses without hurting UPTREND.

Filters tested:
  A: EMA Spread Threshold (|fast - slow| / slow > threshold)
  B: ADX > 15 (looser than R65's killed ADX > 20)
  C: Recent Whipsaw Counter (>2 EMA crosses in 60d -> reduce)
  D: Bollinger Band Width (narrow bands -> reduce position)
  E: Realized Vol Filter (low vol -> reduce position)
  F: EMA Slope Confirmation (slow EMA slope must be positive for longs)

KILL criteria:
  - Filter reduces UPTREND Sharpe by >20%: KILL
  - Filter improves OOS Sharpe by <0.05: KILL (not worth complexity)
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

IS_START = '2020-09-01'
IS_END = '2024-12-31'
OOS_START = '2025-01-01'
COST_BPS = 10


# ============================================================================
# DATA LOADING (reused from v3_regime_analysis.py)
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
# V3 SIGNAL CONSTRUCTION (canonical)
# ============================================================================

def build_v1_signal(btc_daily):
    """V1 base: Long when 20d EMA > 50d EMA, flat otherwise."""
    ema20 = btc_daily['close'].ewm(span=20, adjust=False).mean()
    ema50 = btc_daily['close'].ewm(span=50, adjust=False).mean()
    position = (ema20 > ema50).astype(float)
    position.iloc[:50] = 0.0
    return position, ema20, ema50


def build_positioning_multiplier(btc_daily, positioning):
    """Positioning overlay: combined z-score -> sizing multiplier."""
    pos = positioning.reindex(btc_daily.index).ffill()

    def rolling_zscore(s, window=30):
        mu = s.rolling(window, min_periods=15).mean()
        sigma = s.rolling(window, min_periods=15).std()
        return (s - mu) / sigma.replace(0, np.nan)

    z_toptrader = rolling_zscore(pos['sum_toptrader_ls_ratio'])
    divergence = pos['count_toptrader_ls_ratio'] - pos['count_ls_ratio']
    z_divergence = rolling_zscore(divergence)
    combined_z = (z_toptrader + z_divergence) / 2.0

    def z_to_multiplier(z):
        if pd.isna(z):
            return 1.0
        if z > 1.5:
            return 0.3
        elif z > 0.5:
            return 0.5
        elif z > -0.5:
            return 1.0
        elif z > -1.5:
            return 1.3
        else:
            return 1.5

    pos_multiplier = combined_z.apply(z_to_multiplier)
    return pos_multiplier


def build_vrp_multiplier(btc_daily, dvol_series):
    """VRP overlay: (IV - RV) z-score over 60d -> sizing multiplier."""
    log_ret = np.log(btc_daily['close'] / btc_daily['close'].shift(1))
    rv_20d = log_ret.rolling(20, min_periods=15).std() * np.sqrt(365) * 100

    iv = dvol_series.reindex(btc_daily.index).ffill()
    vrp = iv - rv_20d

    vrp_mu = vrp.rolling(60, min_periods=30).mean()
    vrp_sigma = vrp.rolling(60, min_periods=30).std()
    vrp_z = (vrp - vrp_mu) / vrp_sigma.replace(0, np.nan)

    def vrp_z_to_multiplier(z):
        if pd.isna(z):
            return 1.0
        if z > 1.0:
            return 1.3
        elif z > -0.5:
            return 1.0
        elif z > -1.5:
            return 0.5
        else:
            return 0.3

    vrp_multiplier = vrp_z.apply(vrp_z_to_multiplier)
    return vrp_multiplier


# ============================================================================
# REGIME CLASSIFICATION (canonical from R68)
# ============================================================================

def classify_regimes(btc_daily):
    """
    CRISIS:    50d return < -20%
    UPTREND:   50d SMA > 200d SMA AND close > 50d SMA
    DOWNTREND: 50d SMA < 200d SMA AND close < 50d SMA
    RANGE:     Everything else
    """
    close = btc_daily['close']
    sma50 = close.rolling(50, min_periods=50).mean()
    sma200 = close.rolling(200, min_periods=200).mean()
    ret_50d = close.pct_change(50)

    regime = pd.Series('RANGE', index=btc_daily.index)

    # UPTREND
    uptrend = (sma50 > sma200) & (close > sma50)
    regime[uptrend] = 'UPTREND'

    # DOWNTREND
    downtrend = (sma50 < sma200) & (close < sma50)
    regime[downtrend] = 'DOWNTREND'

    # CRISIS overrides
    crisis = ret_50d < -0.20
    regime[crisis] = 'CRISIS'

    # Set early data (no SMA200) to NaN
    regime[sma200.isna()] = np.nan

    return regime


# ============================================================================
# BACKTEST ENGINE (canonical)
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
# RANGE FILTER IMPLEMENTATIONS
# ============================================================================

def filter_a_ema_spread(btc_daily, ema_fast, ema_slow, threshold):
    """
    Filter A: Only enter when |EMA_fast - EMA_slow| / EMA_slow > threshold.
    Returns a multiplier series: 1.0 when spread > threshold, 0.0 otherwise.
    """
    spread = (ema_fast - ema_slow).abs() / ema_slow
    return (spread > threshold).astype(float)


def filter_b_adx(btc_daily, period=14, threshold=15):
    """
    Filter B: ADX > threshold. Returns multiplier 1.0 when ADX > threshold, 0.0 otherwise.
    """
    high = btc_daily['high']
    low = btc_daily['low']
    close = btc_daily['close']

    # True Range
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    # Directional movement
    up_move = high - high.shift(1)
    down_move = low.shift(1) - low

    plus_dm = pd.Series(0.0, index=btc_daily.index)
    minus_dm = pd.Series(0.0, index=btc_daily.index)

    plus_dm[(up_move > down_move) & (up_move > 0)] = up_move[(up_move > down_move) & (up_move > 0)]
    minus_dm[(down_move > up_move) & (down_move > 0)] = down_move[(down_move > up_move) & (down_move > 0)]

    # Smoothed averages (Wilder's smoothing)
    atr = tr.ewm(span=period, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(span=period, adjust=False).mean() / atr
    minus_di = 100 * minus_dm.ewm(span=period, adjust=False).mean() / atr

    # ADX
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = dx.ewm(span=period, adjust=False).mean()

    return (adx > threshold).astype(float), adx


def filter_c_whipsaw_counter(btc_daily, ema_fast, ema_slow, lookback=60, max_crosses=2, reduce_to=0.5):
    """
    Filter C: Count EMA crossovers in last `lookback` days.
    If crosses > max_crosses: reduce position to `reduce_to`, else 1.0.
    """
    # Detect crossovers: when sign of (fast - slow) changes
    diff = ema_fast - ema_slow
    sign_change = (diff > 0).astype(int).diff().abs()  # 1 on crossover days
    sign_change.iloc[0] = 0

    # Rolling count of crossovers
    cross_count = sign_change.rolling(lookback, min_periods=1).sum()

    # Multiplier
    multiplier = pd.Series(1.0, index=btc_daily.index)
    multiplier[cross_count > max_crosses] = reduce_to
    return multiplier, cross_count


def filter_d_bb_width(btc_daily, period=20, num_std=2, percentile=25):
    """
    Filter D: Bollinger Band Width. When width < percentile, reduce to 0.5x.
    """
    close = btc_daily['close']
    sma = close.rolling(period, min_periods=period).mean()
    std = close.rolling(period, min_periods=period).std()

    upper = sma + num_std * std
    lower = sma - num_std * std
    bb_width = (upper - lower) / sma  # Normalized width

    # Rolling percentile threshold
    width_threshold = bb_width.expanding(min_periods=60).quantile(percentile / 100.0)

    multiplier = pd.Series(1.0, index=btc_daily.index)
    multiplier[bb_width < width_threshold] = 0.5
    return multiplier, bb_width


def filter_e_realized_vol(btc_daily, vol_threshold_ann=0.30, reduce_to=0.5):
    """
    Filter E: When 20d realized vol < threshold (annualized), reduce position.
    """
    log_ret = np.log(btc_daily['close'] / btc_daily['close'].shift(1))
    rv_20d = log_ret.rolling(20, min_periods=15).std() * np.sqrt(365)

    multiplier = pd.Series(1.0, index=btc_daily.index)
    multiplier[rv_20d < vol_threshold_ann] = reduce_to
    return multiplier, rv_20d


def filter_f_ema_slope(btc_daily, ema_slow, slope_lookback=10):
    """
    Filter F: Only enter long when slope of slow EMA is positive.
    Slope = (EMA_today - EMA_N_days_ago) / EMA_N_days_ago
    """
    slope = (ema_slow - ema_slow.shift(slope_lookback)) / ema_slow.shift(slope_lookback)
    multiplier = (slope > 0).astype(float)
    return multiplier, slope


# ============================================================================
# V3 STRATEGY WITH FILTER
# ============================================================================

def run_v3_with_filter(btc_daily, positioning, dvol_series, filter_multiplier):
    """
    Run V3 strategy with an additional filter multiplier applied.
    filter_multiplier is a pd.Series of floats (0.0 to 1.0) that scales the position.
    """
    v1_pos, ema20, ema50 = build_v1_signal(btc_daily)
    pos_mult = build_positioning_multiplier(btc_daily, positioning)
    vrp_mult = build_vrp_multiplier(btc_daily, dvol_series)

    # Apply V3 overlays
    v3_pos = (v1_pos * pos_mult * vrp_mult).clip(0, 1.5)

    # Apply range filter
    filtered_pos = (v3_pos * filter_multiplier).clip(0, 1.5)

    return run_backtest(btc_daily, filtered_pos)


def run_v3_baseline(btc_daily, positioning, dvol_series):
    """Run plain V3 without any range filter."""
    v1_pos, ema20, ema50 = build_v1_signal(btc_daily)
    pos_mult = build_positioning_multiplier(btc_daily, positioning)
    vrp_mult = build_vrp_multiplier(btc_daily, dvol_series)
    v3_pos = (v1_pos * pos_mult * vrp_mult).clip(0, 1.5)
    return run_backtest(btc_daily, v3_pos)


# ============================================================================
# ANALYSIS ENGINE
# ============================================================================

def analyze_filter(name, strat_ret, baseline_ret, regime, btc_daily,
                   is_start, is_end, oos_start):
    """
    Analyze a filter variant vs baseline across regimes and periods.
    Returns a dict with all metrics.
    """
    results = {}

    # Period masks
    is_mask = (btc_daily.index >= is_start) & (btc_daily.index <= is_end)
    oos_mask = btc_daily.index >= oos_start
    latest = btc_daily.index.max().strftime('%Y-%m-%d')

    # Overall metrics
    for period_name, mask in [('IS', is_mask), ('OOS', oos_mask)]:
        results[f'{period_name}_metrics'] = compute_metrics(strat_ret[mask])
        results[f'{period_name}_baseline'] = compute_metrics(baseline_ret[mask])

    # Regime-specific metrics (OOS)
    for r in ['UPTREND', 'DOWNTREND', 'RANGE', 'CRISIS']:
        r_mask = (regime == r) & oos_mask
        r_ret = strat_ret[r_mask]
        r_base = baseline_ret[r_mask]
        if len(r_ret.dropna()) > 10:
            results[f'OOS_{r}'] = compute_metrics(r_ret)
            results[f'OOS_{r}_baseline'] = compute_metrics(r_base)
        else:
            results[f'OOS_{r}'] = {'sharpe': np.nan, 'ann_return': np.nan, 'max_dd': np.nan, 'n_days': 0}
            results[f'OOS_{r}_baseline'] = {'sharpe': np.nan, 'ann_return': np.nan, 'max_dd': np.nan, 'n_days': 0}

    # IS regime metrics too
    for r in ['UPTREND', 'DOWNTREND', 'RANGE', 'CRISIS']:
        r_mask = (regime == r) & is_mask
        r_ret = strat_ret[r_mask]
        r_base = baseline_ret[r_mask]
        if len(r_ret.dropna()) > 10:
            results[f'IS_{r}'] = compute_metrics(r_ret)
            results[f'IS_{r}_baseline'] = compute_metrics(r_base)
        else:
            results[f'IS_{r}'] = {'sharpe': np.nan, 'ann_return': np.nan, 'max_dd': np.nan, 'n_days': 0}
            results[f'IS_{r}_baseline'] = {'sharpe': np.nan, 'ann_return': np.nan, 'max_dd': np.nan, 'n_days': 0}

    return results


def check_kill(results, filter_name):
    """
    KILL criteria:
    1. Filter reduces OOS UPTREND Sharpe by >20%: KILL (cure worse than disease)
    2. Filter improves OOS Sharpe by <0.05: KILL (not worth complexity)
    """
    kills = []

    # Check UPTREND damage
    up_base = results.get('OOS_UPTREND_baseline', {}).get('sharpe', np.nan)
    up_filt = results.get('OOS_UPTREND', {}).get('sharpe', np.nan)
    if not np.isnan(up_base) and not np.isnan(up_filt) and up_base > 0:
        degradation = (up_base - up_filt) / abs(up_base)
        if degradation > 0.20:
            kills.append(f"UPTREND Sharpe degraded by {degradation:.1%} (>{20}%)")

    # Check overall OOS improvement
    oos_base = results.get('OOS_baseline', {}).get('sharpe', np.nan)
    oos_filt = results.get('OOS_metrics', {}).get('sharpe', np.nan)
    if not np.isnan(oos_base) and not np.isnan(oos_filt):
        improvement = oos_filt - oos_base
        if improvement < 0.05:
            kills.append(f"OOS Sharpe improvement only {improvement:+.3f} (<0.05)")

    return kills


# ============================================================================
# MAIN
# ============================================================================

def main():
    print("=" * 80)
    print("R69: V3 RANGE FILTER TEST")
    print(f"Run date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 80)
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

    # Build regime classification
    regime = classify_regimes(btc_daily)

    # Build V1 signals (needed for filter construction)
    v1_pos, ema20, ema50 = build_v1_signal(btc_daily)

    # Compute V3 baseline
    print("Computing V3 baseline...")
    baseline_ret = run_v3_baseline(btc_daily, positioning, dvol)

    is_mask = (btc_daily.index >= IS_START) & (btc_daily.index <= IS_END)
    oos_mask = btc_daily.index >= OOS_START

    base_is = compute_metrics(baseline_ret[is_mask])
    base_oos = compute_metrics(baseline_ret[oos_mask])
    print(f"  V3 Baseline IS:  Sharpe={base_is['sharpe']:+.3f}, Return={base_is['ann_return']:+.1%}, MaxDD={base_is['max_dd']:.1%}")
    print(f"  V3 Baseline OOS: Sharpe={base_oos['sharpe']:+.3f}, Return={base_oos['ann_return']:+.1%}, MaxDD={base_oos['max_dd']:.1%}")

    # Regime-specific baseline
    for r in ['UPTREND', 'RANGE', 'DOWNTREND', 'CRISIS']:
        r_oos_mask = (regime == r) & oos_mask
        r_ret = baseline_ret[r_oos_mask]
        if len(r_ret.dropna()) > 10:
            m = compute_metrics(r_ret)
            print(f"  V3 Baseline OOS {r}: Sharpe={m['sharpe']:+.3f}, Return={m['ann_return']:+.1%}, Days={m['n_days']}")
    print()

    # ── Store all results ──
    all_results = {}

    # ════════════════════════════════════════════════════════════════════════
    # FILTER A: EMA SPREAD THRESHOLD
    # ════════════════════════════════════════════════════════════════════════
    print("=" * 80)
    print("FILTER A: EMA SPREAD THRESHOLD")
    print("=" * 80)

    for thresh in [0.005, 0.01, 0.02, 0.03]:
        name = f"A_spread_{thresh:.3f}"
        print(f"\n  Testing {name}...")
        filt = filter_a_ema_spread(btc_daily, ema20, ema50, thresh)
        strat_ret = run_v3_with_filter(btc_daily, positioning, dvol, filt)
        results = analyze_filter(name, strat_ret, baseline_ret, regime, btc_daily,
                                 IS_START, IS_END, OOS_START)
        kills = check_kill(results, name)

        oos_m = results['OOS_metrics']
        oos_b = results['OOS_baseline']
        print(f"    OOS Sharpe: {oos_b['sharpe']:+.3f} -> {oos_m['sharpe']:+.3f} (delta={oos_m['sharpe']-oos_b['sharpe']:+.3f})")
        print(f"    OOS Return: {oos_b['ann_return']:+.1%} -> {oos_m['ann_return']:+.1%}")
        print(f"    OOS MaxDD: {oos_b['max_dd']:.1%} -> {oos_m['max_dd']:.1%}")

        # Days filter is active (position reduced)
        pct_filtered = (filt == 0.0).mean()
        print(f"    % days filtered out: {pct_filtered:.1%}")

        # OOS RANGE performance
        r_oos = results.get('OOS_RANGE', {})
        r_base = results.get('OOS_RANGE_baseline', {})
        if not np.isnan(r_oos.get('sharpe', np.nan)):
            print(f"    OOS RANGE Sharpe: {r_base['sharpe']:+.3f} -> {r_oos['sharpe']:+.3f}")

        # OOS UPTREND performance
        u_oos = results.get('OOS_UPTREND', {})
        u_base = results.get('OOS_UPTREND_baseline', {})
        if not np.isnan(u_oos.get('sharpe', np.nan)):
            print(f"    OOS UPTREND Sharpe: {u_base['sharpe']:+.3f} -> {u_oos['sharpe']:+.3f}")

        if kills:
            print(f"    *** KILL: {'; '.join(kills)}")
        else:
            print(f"    PASS")

        all_results[name] = {'results': results, 'kills': kills, 'filter_type': 'A',
                             'params': {'threshold': thresh}, 'pct_filtered': pct_filtered}

    # ════════════════════════════════════════════════════════════════════════
    # FILTER B: ADX THRESHOLD
    # ════════════════════════════════════════════════════════════════════════
    print("\n" + "=" * 80)
    print("FILTER B: ADX THRESHOLD")
    print("=" * 80)

    for adx_thresh in [10, 15, 20, 25]:
        name = f"B_adx_{adx_thresh}"
        print(f"\n  Testing {name}...")
        filt, adx_series = filter_b_adx(btc_daily, period=14, threshold=adx_thresh)
        strat_ret = run_v3_with_filter(btc_daily, positioning, dvol, filt)
        results = analyze_filter(name, strat_ret, baseline_ret, regime, btc_daily,
                                 IS_START, IS_END, OOS_START)
        kills = check_kill(results, name)

        oos_m = results['OOS_metrics']
        oos_b = results['OOS_baseline']
        print(f"    OOS Sharpe: {oos_b['sharpe']:+.3f} -> {oos_m['sharpe']:+.3f} (delta={oos_m['sharpe']-oos_b['sharpe']:+.3f})")
        print(f"    OOS Return: {oos_b['ann_return']:+.1%} -> {oos_m['ann_return']:+.1%}")
        print(f"    OOS MaxDD: {oos_b['max_dd']:.1%} -> {oos_m['max_dd']:.1%}")

        pct_filtered = (filt == 0.0).mean()
        print(f"    % days filtered out: {pct_filtered:.1%}")

        r_oos = results.get('OOS_RANGE', {})
        r_base = results.get('OOS_RANGE_baseline', {})
        if not np.isnan(r_oos.get('sharpe', np.nan)):
            print(f"    OOS RANGE Sharpe: {r_base['sharpe']:+.3f} -> {r_oos['sharpe']:+.3f}")

        u_oos = results.get('OOS_UPTREND', {})
        u_base = results.get('OOS_UPTREND_baseline', {})
        if not np.isnan(u_oos.get('sharpe', np.nan)):
            print(f"    OOS UPTREND Sharpe: {u_base['sharpe']:+.3f} -> {u_oos['sharpe']:+.3f}")

        if kills:
            print(f"    *** KILL: {'; '.join(kills)}")
        else:
            print(f"    PASS")

        all_results[name] = {'results': results, 'kills': kills, 'filter_type': 'B',
                             'params': {'adx_threshold': adx_thresh}, 'pct_filtered': pct_filtered}

    # ════════════════════════════════════════════════════════════════════════
    # FILTER C: WHIPSAW COUNTER
    # ════════════════════════════════════════════════════════════════════════
    print("\n" + "=" * 80)
    print("FILTER C: WHIPSAW COUNTER")
    print("=" * 80)

    for max_crosses, reduce_to in [(2, 0.5), (2, 0.0), (3, 0.5), (1, 0.5)]:
        name = f"C_whipsaw_{max_crosses}x_r{reduce_to}"
        print(f"\n  Testing {name} (max_crosses={max_crosses}, reduce_to={reduce_to})...")
        filt, cross_count = filter_c_whipsaw_counter(btc_daily, ema20, ema50,
                                                      lookback=60, max_crosses=max_crosses,
                                                      reduce_to=reduce_to)
        strat_ret = run_v3_with_filter(btc_daily, positioning, dvol, filt)
        results = analyze_filter(name, strat_ret, baseline_ret, regime, btc_daily,
                                 IS_START, IS_END, OOS_START)
        kills = check_kill(results, name)

        oos_m = results['OOS_metrics']
        oos_b = results['OOS_baseline']
        print(f"    OOS Sharpe: {oos_b['sharpe']:+.3f} -> {oos_m['sharpe']:+.3f} (delta={oos_m['sharpe']-oos_b['sharpe']:+.3f})")
        print(f"    OOS Return: {oos_b['ann_return']:+.1%} -> {oos_m['ann_return']:+.1%}")
        print(f"    OOS MaxDD: {oos_b['max_dd']:.1%} -> {oos_m['max_dd']:.1%}")

        pct_reduced = (filt < 1.0).mean()
        print(f"    % days position reduced: {pct_reduced:.1%}")
        print(f"    Avg cross count (OOS): {cross_count[oos_mask].mean():.1f}")

        r_oos = results.get('OOS_RANGE', {})
        r_base = results.get('OOS_RANGE_baseline', {})
        if not np.isnan(r_oos.get('sharpe', np.nan)):
            print(f"    OOS RANGE Sharpe: {r_base['sharpe']:+.3f} -> {r_oos['sharpe']:+.3f}")

        u_oos = results.get('OOS_UPTREND', {})
        u_base = results.get('OOS_UPTREND_baseline', {})
        if not np.isnan(u_oos.get('sharpe', np.nan)):
            print(f"    OOS UPTREND Sharpe: {u_base['sharpe']:+.3f} -> {u_oos['sharpe']:+.3f}")

        if kills:
            print(f"    *** KILL: {'; '.join(kills)}")
        else:
            print(f"    PASS")

        all_results[name] = {'results': results, 'kills': kills, 'filter_type': 'C',
                             'params': {'max_crosses': max_crosses, 'reduce_to': reduce_to},
                             'pct_filtered': pct_reduced}

    # ════════════════════════════════════════════════════════════════════════
    # FILTER D: BOLLINGER BAND WIDTH
    # ════════════════════════════════════════════════════════════════════════
    print("\n" + "=" * 80)
    print("FILTER D: BOLLINGER BAND WIDTH")
    print("=" * 80)

    for pctile in [15, 25, 35]:
        name = f"D_bbwidth_p{pctile}"
        print(f"\n  Testing {name} (reduce when width < {pctile}th percentile)...")
        filt, bb_width = filter_d_bb_width(btc_daily, period=20, num_std=2, percentile=pctile)
        strat_ret = run_v3_with_filter(btc_daily, positioning, dvol, filt)
        results = analyze_filter(name, strat_ret, baseline_ret, regime, btc_daily,
                                 IS_START, IS_END, OOS_START)
        kills = check_kill(results, name)

        oos_m = results['OOS_metrics']
        oos_b = results['OOS_baseline']
        print(f"    OOS Sharpe: {oos_b['sharpe']:+.3f} -> {oos_m['sharpe']:+.3f} (delta={oos_m['sharpe']-oos_b['sharpe']:+.3f})")
        print(f"    OOS Return: {oos_b['ann_return']:+.1%} -> {oos_m['ann_return']:+.1%}")
        print(f"    OOS MaxDD: {oos_b['max_dd']:.1%} -> {oos_m['max_dd']:.1%}")

        pct_reduced = (filt < 1.0).mean()
        print(f"    % days position reduced: {pct_reduced:.1%}")

        r_oos = results.get('OOS_RANGE', {})
        r_base = results.get('OOS_RANGE_baseline', {})
        if not np.isnan(r_oos.get('sharpe', np.nan)):
            print(f"    OOS RANGE Sharpe: {r_base['sharpe']:+.3f} -> {r_oos['sharpe']:+.3f}")

        u_oos = results.get('OOS_UPTREND', {})
        u_base = results.get('OOS_UPTREND_baseline', {})
        if not np.isnan(u_oos.get('sharpe', np.nan)):
            print(f"    OOS UPTREND Sharpe: {u_base['sharpe']:+.3f} -> {u_oos['sharpe']:+.3f}")

        if kills:
            print(f"    *** KILL: {'; '.join(kills)}")
        else:
            print(f"    PASS")

        all_results[name] = {'results': results, 'kills': kills, 'filter_type': 'D',
                             'params': {'percentile': pctile}, 'pct_filtered': pct_reduced}

    # ════════════════════════════════════════════════════════════════════════
    # FILTER E: REALIZED VOL
    # ════════════════════════════════════════════════════════════════════════
    print("\n" + "=" * 80)
    print("FILTER E: REALIZED VOL FILTER")
    print("=" * 80)

    for vol_thresh in [0.25, 0.30, 0.40, 0.50]:
        for reduce_to in [0.5, 0.0]:
            name = f"E_rvol_{int(vol_thresh*100)}pct_r{reduce_to}"
            print(f"\n  Testing {name} (vol < {vol_thresh:.0%} ann -> {reduce_to}x)...")
            filt, rv = filter_e_realized_vol(btc_daily, vol_threshold_ann=vol_thresh, reduce_to=reduce_to)
            strat_ret = run_v3_with_filter(btc_daily, positioning, dvol, filt)
            results = analyze_filter(name, strat_ret, baseline_ret, regime, btc_daily,
                                     IS_START, IS_END, OOS_START)
            kills = check_kill(results, name)

            oos_m = results['OOS_metrics']
            oos_b = results['OOS_baseline']
            print(f"    OOS Sharpe: {oos_b['sharpe']:+.3f} -> {oos_m['sharpe']:+.3f} (delta={oos_m['sharpe']-oos_b['sharpe']:+.3f})")
            print(f"    OOS Return: {oos_b['ann_return']:+.1%} -> {oos_m['ann_return']:+.1%}")
            print(f"    OOS MaxDD: {oos_b['max_dd']:.1%} -> {oos_m['max_dd']:.1%}")

            pct_reduced = (filt < 1.0).mean()
            print(f"    % days position reduced: {pct_reduced:.1%}")
            print(f"    Mean 20d RV (OOS): {rv[oos_mask].mean():.1%}")

            r_oos = results.get('OOS_RANGE', {})
            r_base = results.get('OOS_RANGE_baseline', {})
            if not np.isnan(r_oos.get('sharpe', np.nan)):
                print(f"    OOS RANGE Sharpe: {r_base['sharpe']:+.3f} -> {r_oos['sharpe']:+.3f}")

            u_oos = results.get('OOS_UPTREND', {})
            u_base = results.get('OOS_UPTREND_baseline', {})
            if not np.isnan(u_oos.get('sharpe', np.nan)):
                print(f"    OOS UPTREND Sharpe: {u_base['sharpe']:+.3f} -> {u_oos['sharpe']:+.3f}")

            if kills:
                print(f"    *** KILL: {'; '.join(kills)}")
            else:
                print(f"    PASS")

            all_results[name] = {'results': results, 'kills': kills, 'filter_type': 'E',
                                 'params': {'vol_threshold': vol_thresh, 'reduce_to': reduce_to},
                                 'pct_filtered': pct_reduced}

    # ════════════════════════════════════════════════════════════════════════
    # FILTER F: EMA SLOPE CONFIRMATION
    # ════════════════════════════════════════════════════════════════════════
    print("\n" + "=" * 80)
    print("FILTER F: EMA SLOPE CONFIRMATION")
    print("=" * 80)

    for lookback in [5, 10, 20]:
        name = f"F_slope_{lookback}d"
        print(f"\n  Testing {name} (50d EMA slope positive over {lookback}d)...")
        filt, slope = filter_f_ema_slope(btc_daily, ema50, slope_lookback=lookback)
        strat_ret = run_v3_with_filter(btc_daily, positioning, dvol, filt)
        results = analyze_filter(name, strat_ret, baseline_ret, regime, btc_daily,
                                 IS_START, IS_END, OOS_START)
        kills = check_kill(results, name)

        oos_m = results['OOS_metrics']
        oos_b = results['OOS_baseline']
        print(f"    OOS Sharpe: {oos_b['sharpe']:+.3f} -> {oos_m['sharpe']:+.3f} (delta={oos_m['sharpe']-oos_b['sharpe']:+.3f})")
        print(f"    OOS Return: {oos_b['ann_return']:+.1%} -> {oos_m['ann_return']:+.1%}")
        print(f"    OOS MaxDD: {oos_b['max_dd']:.1%} -> {oos_m['max_dd']:.1%}")

        pct_filtered = (filt == 0.0).mean()
        print(f"    % days filtered out: {pct_filtered:.1%}")

        r_oos = results.get('OOS_RANGE', {})
        r_base = results.get('OOS_RANGE_baseline', {})
        if not np.isnan(r_oos.get('sharpe', np.nan)):
            print(f"    OOS RANGE Sharpe: {r_base['sharpe']:+.3f} -> {r_oos['sharpe']:+.3f}")

        u_oos = results.get('OOS_UPTREND', {})
        u_base = results.get('OOS_UPTREND_baseline', {})
        if not np.isnan(u_oos.get('sharpe', np.nan)):
            print(f"    OOS UPTREND Sharpe: {u_base['sharpe']:+.3f} -> {u_oos['sharpe']:+.3f}")

        if kills:
            print(f"    *** KILL: {'; '.join(kills)}")
        else:
            print(f"    PASS")

        all_results[name] = {'results': results, 'kills': kills, 'filter_type': 'F',
                             'params': {'lookback': lookback}, 'pct_filtered': pct_filtered}

    # ════════════════════════════════════════════════════════════════════════
    # SUMMARY & REPORT
    # ════════════════════════════════════════════════════════════════════════
    print("\n" + "=" * 80)
    print("GENERATING REPORT")
    print("=" * 80)

    generate_report(all_results, base_is, base_oos, baseline_ret, regime, btc_daily)

    # Final summary
    print("\n" + "=" * 80)
    print("FINAL SUMMARY")
    print("=" * 80)

    # Sort by OOS Sharpe improvement
    ranked = []
    for name, data in all_results.items():
        oos_base_sharpe = data['results']['OOS_baseline']['sharpe']
        oos_filt_sharpe = data['results']['OOS_metrics']['sharpe']
        delta = oos_filt_sharpe - oos_base_sharpe
        killed = len(data['kills']) > 0
        ranked.append((name, delta, oos_filt_sharpe, killed, data['kills']))

    ranked.sort(key=lambda x: x[1], reverse=True)

    print(f"\n{'Filter':<35} {'OOS dSharpe':>12} {'OOS Sharpe':>12} {'Verdict':>10}")
    print("-" * 75)
    for name, delta, sharpe, killed, kills in ranked:
        verdict = "KILL" if killed else "PASS"
        print(f"  {name:<33} {delta:>+10.3f}   {sharpe:>+10.3f}   {verdict:>8}")

    # Best non-killed
    passing = [(n, d, s) for n, d, s, k, _ in ranked if not k]
    if passing:
        best = passing[0]
        print(f"\n  BEST PASSING FILTER: {best[0]} (dSharpe={best[1]:+.3f}, OOS Sharpe={best[2]:+.3f})")
    else:
        print(f"\n  NO FILTERS PASS. All range filters either hurt UPTREND or don't improve enough.")


def generate_report(all_results, base_is, base_oos, baseline_ret, regime, btc_daily):
    """Generate markdown report."""
    lines = []
    lines.append("# R69: V3 Range Filter Test Results")
    lines.append("")
    lines.append(f"**Run date**: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"**IS period**: {IS_START} to {IS_END}")
    lines.append(f"**OOS period**: {OOS_START} to {btc_daily.index.max().strftime('%Y-%m-%d')}")
    lines.append("")
    lines.append("## Goal")
    lines.append("")
    lines.append("V3 (20/50 EMA + Positioning + VRP overlays) has a known weakness in RANGE regimes")
    lines.append("(IS Sharpe -1.01, OOS Sharpe -1.06). Test range-market filters to reduce RANGE losses")
    lines.append("without hurting UPTREND performance.")
    lines.append("")
    lines.append("## KILL Criteria")
    lines.append("")
    lines.append("- Filter reduces OOS UPTREND Sharpe by >20%: **KILL** (cure worse than disease)")
    lines.append("- Filter improves overall OOS Sharpe by <0.05: **KILL** (not worth complexity)")
    lines.append("")

    # V3 Baseline
    lines.append("## V3 Baseline (No Filter)")
    lines.append("")
    lines.append(f"| Period | Sharpe | Return | MaxDD | Days |")
    lines.append(f"|--------|--------|--------|-------|------|")
    lines.append(f"| IS | {base_is['sharpe']:+.3f} | {base_is['ann_return']:+.1%} | {base_is['max_dd']:.1%} | {base_is['n_days']} |")
    lines.append(f"| OOS | {base_oos['sharpe']:+.3f} | {base_oos['ann_return']:+.1%} | {base_oos['max_dd']:.1%} | {base_oos['n_days']} |")
    lines.append("")

    # Baseline regime performance
    is_mask = (btc_daily.index >= IS_START) & (btc_daily.index <= IS_END)
    oos_mask = btc_daily.index >= OOS_START

    lines.append("### Baseline Regime Performance (OOS)")
    lines.append("")
    lines.append("| Regime | Sharpe | Return | MaxDD | Days |")
    lines.append("|--------|--------|--------|-------|------|")
    for r in ['UPTREND', 'RANGE', 'DOWNTREND', 'CRISIS']:
        r_mask = (regime == r) & oos_mask
        r_ret = baseline_ret[r_mask]
        if len(r_ret.dropna()) > 10:
            m = compute_metrics(r_ret)
            lines.append(f"| {r} | {m['sharpe']:+.3f} | {m['ann_return']:+.1%} | {m['max_dd']:.1%} | {m['n_days']} |")
        else:
            lines.append(f"| {r} | N/A | N/A | N/A | {len(r_ret)} |")
    lines.append("")

    # ── Per-filter results ──
    filter_types = {
        'A': ('Filter A: EMA Spread Threshold', 'Only enter when |EMA20 - EMA50| / EMA50 > threshold. Small gaps = noise, large gaps = real trend.'),
        'B': ('Filter B: ADX Threshold', 'Only enter when ADX > threshold. Low ADX = no trend.'),
        'C': ('Filter C: Whipsaw Counter', 'Count EMA crossovers in last 60 days. Many crosses = range market.'),
        'D': ('Filter D: Bollinger Band Width', 'When BB width < Nth percentile, reduce position to 0.5x. Narrow BBs = range.'),
        'E': ('Filter E: Realized Vol', 'When 20d realized vol < threshold, reduce position. Low vol = range.'),
        'F': ('Filter F: EMA Slope Confirmation', 'Only enter long when 50d EMA slope is positive over N days.'),
    }

    for ft, (title, desc) in filter_types.items():
        lines.append(f"## {title}")
        lines.append("")
        lines.append(f"*{desc}*")
        lines.append("")

        # Gather all results for this filter type
        ft_results = {k: v for k, v in all_results.items() if v['filter_type'] == ft}

        if not ft_results:
            lines.append("No variants tested.")
            lines.append("")
            continue

        lines.append("| Variant | Params | OOS Sharpe | OOS dSharpe | OOS Return | OOS MaxDD | OOS RANGE Sharpe | OOS UPTREND Sharpe | % Filtered | Verdict |")
        lines.append("|---------|--------|------------|-------------|------------|-----------|------------------|--------------------|-----------:|---------|")

        for name, data in sorted(ft_results.items()):
            r = data['results']
            oos_m = r['OOS_metrics']
            oos_b = r['OOS_baseline']
            delta_sharpe = oos_m['sharpe'] - oos_b['sharpe']

            range_sharpe = r.get('OOS_RANGE', {}).get('sharpe', np.nan)
            up_sharpe = r.get('OOS_UPTREND', {}).get('sharpe', np.nan)

            range_str = f"{range_sharpe:+.3f}" if not np.isnan(range_sharpe) else "N/A"
            up_str = f"{up_sharpe:+.3f}" if not np.isnan(up_sharpe) else "N/A"

            verdict = "KILL" if data['kills'] else "PASS"
            params_str = str(data['params'])

            lines.append(
                f"| {name} | {params_str} "
                f"| {oos_m['sharpe']:+.3f} | {delta_sharpe:+.3f} "
                f"| {oos_m['ann_return']:+.1%} | {oos_m['max_dd']:.1%} "
                f"| {range_str} | {up_str} "
                f"| {data['pct_filtered']:.1%} | **{verdict}** |"
            )

        lines.append("")

        # Kill reasons
        killed = {k: v for k, v in ft_results.items() if v['kills']}
        if killed:
            lines.append("### Kill Reasons")
            lines.append("")
            for name, data in killed.items():
                for reason in data['kills']:
                    lines.append(f"- **{name}**: {reason}")
            lines.append("")

    # ── GRAND SUMMARY ──
    lines.append("## Grand Summary")
    lines.append("")

    # Rank all variants by OOS dSharpe
    ranked = []
    for name, data in all_results.items():
        oos_base_sharpe = data['results']['OOS_baseline']['sharpe']
        oos_filt_sharpe = data['results']['OOS_metrics']['sharpe']
        delta = oos_filt_sharpe - oos_base_sharpe
        killed = len(data['kills']) > 0

        oos_range_sharpe = data['results'].get('OOS_RANGE', {}).get('sharpe', np.nan)
        oos_range_base = data['results'].get('OOS_RANGE_baseline', {}).get('sharpe', np.nan)
        range_improvement = oos_range_sharpe - oos_range_base if not np.isnan(oos_range_sharpe) and not np.isnan(oos_range_base) else np.nan

        oos_up_sharpe = data['results'].get('OOS_UPTREND', {}).get('sharpe', np.nan)
        oos_up_base = data['results'].get('OOS_UPTREND_baseline', {}).get('sharpe', np.nan)
        up_damage = oos_up_sharpe - oos_up_base if not np.isnan(oos_up_sharpe) and not np.isnan(oos_up_base) else np.nan

        ranked.append({
            'name': name,
            'delta_sharpe': delta,
            'oos_sharpe': oos_filt_sharpe,
            'killed': killed,
            'kills': data['kills'],
            'range_improvement': range_improvement,
            'uptrend_damage': up_damage,
            'filter_type': data['filter_type'],
        })

    ranked.sort(key=lambda x: x['delta_sharpe'], reverse=True)

    lines.append("### All Filters Ranked by OOS Sharpe Improvement")
    lines.append("")
    lines.append("| Rank | Filter | OOS dSharpe | OOS Sharpe | RANGE dSharpe | UPTREND dSharpe | Verdict |")
    lines.append("|------|--------|-------------|------------|---------------|-----------------|---------|")

    for i, r in enumerate(ranked):
        range_str = f"{r['range_improvement']:+.3f}" if not np.isnan(r['range_improvement']) else "N/A"
        up_str = f"{r['uptrend_damage']:+.3f}" if not np.isnan(r['uptrend_damage']) else "N/A"
        verdict = "KILL" if r['killed'] else "PASS"
        lines.append(
            f"| {i+1} | {r['name']} | {r['delta_sharpe']:+.3f} | {r['oos_sharpe']:+.3f} "
            f"| {range_str} | {up_str} | **{verdict}** |"
        )

    lines.append("")

    # ── IS analysis for surviving filters (overfit check) ──
    passing = [r for r in ranked if not r['killed']]

    if passing:
        lines.append("### Surviving Filters: IS vs OOS Consistency Check")
        lines.append("")
        lines.append("| Filter | IS Sharpe | IS dSharpe | OOS Sharpe | OOS dSharpe | IS-OOS Gap | Overfit Risk |")
        lines.append("|--------|----------|------------|------------|-------------|------------|--------------|")

        for r in passing:
            data = all_results[r['name']]
            is_m = data['results']['IS_metrics']
            is_b = data['results']['IS_baseline']
            is_delta = is_m['sharpe'] - is_b['sharpe']
            oos_delta = r['delta_sharpe']
            gap = is_delta - oos_delta

            if abs(is_delta) > 0 and abs(oos_delta) > 0:
                if gap > 0.2:
                    overfit = "HIGH"
                elif gap > 0.1:
                    overfit = "MODERATE"
                else:
                    overfit = "LOW"
            else:
                overfit = "N/A"

            lines.append(
                f"| {r['name']} | {is_m['sharpe']:+.3f} | {is_delta:+.3f} "
                f"| {r['oos_sharpe']:+.3f} | {oos_delta:+.3f} "
                f"| {gap:+.3f} | {overfit} |"
            )

        lines.append("")

    # ── VERDICT ──
    lines.append("## Verdict")
    lines.append("")

    passing_filters = [r for r in ranked if not r['killed']]
    if not passing_filters:
        lines.append("### NO FILTERS WORTH ADDING")
        lines.append("")
        lines.append("All tested range filters either:")
        lines.append("- Hurt UPTREND performance by >20% (cure worse than disease)")
        lines.append("- Failed to improve overall OOS Sharpe by at least 0.05 (not worth the complexity)")
        lines.append("")
        lines.append("**Recommendation**: Keep V3 as-is. The RANGE regime weakness is a known, accepted")
        lines.append("cost of the trend-following approach. The overlays (positioning + VRP) already provide")
        lines.append("some RANGE mitigation (Sharpe improvement of +0.23 vs V1 in RANGE).")
        lines.append("")
    else:
        best = passing_filters[0]
        lines.append(f"### BEST FILTER: {best['name']}")
        lines.append("")
        lines.append(f"- OOS Sharpe improvement: {best['delta_sharpe']:+.3f}")
        lines.append(f"- OOS Sharpe: {best['oos_sharpe']:+.3f}")
        if not np.isnan(best['range_improvement']):
            lines.append(f"- RANGE Sharpe improvement: {best['range_improvement']:+.3f}")
        if not np.isnan(best['uptrend_damage']):
            lines.append(f"- UPTREND Sharpe change: {best['uptrend_damage']:+.3f}")
        lines.append("")

        # Check IS/OOS consistency for overfit risk
        data = all_results[best['name']]
        is_delta = data['results']['IS_metrics']['sharpe'] - data['results']['IS_baseline']['sharpe']
        oos_delta = best['delta_sharpe']
        gap = is_delta - oos_delta

        if gap > 0.2:
            lines.append(f"**WARNING**: IS improvement ({is_delta:+.3f}) >> OOS improvement ({oos_delta:+.3f}). High overfit risk.")
            lines.append("Recommend additional walk-forward validation before production deployment.")
        elif gap > 0.1:
            lines.append(f"**CAUTION**: IS improvement ({is_delta:+.3f}) > OOS improvement ({oos_delta:+.3f}). Moderate overfit risk.")
            lines.append("Consider walk-forward validation.")
        else:
            lines.append(f"IS/OOS consistency looks good (IS delta={is_delta:+.3f}, OOS delta={oos_delta:+.3f}).")

        lines.append("")

        if len(passing_filters) > 1:
            lines.append("### Other Passing Filters")
            lines.append("")
            for r in passing_filters[1:]:
                lines.append(f"- **{r['name']}**: OOS dSharpe={r['delta_sharpe']:+.3f}")
            lines.append("")

    # ── Key insights ──
    lines.append("## Key Insights")
    lines.append("")

    # Count by filter type
    type_results = {}
    for r in ranked:
        ft = r['filter_type']
        if ft not in type_results:
            type_results[ft] = {'pass': 0, 'kill': 0, 'best_delta': -999}
        if r['killed']:
            type_results[ft]['kill'] += 1
        else:
            type_results[ft]['pass'] += 1
        type_results[ft]['best_delta'] = max(type_results[ft]['best_delta'], r['delta_sharpe'])

    filter_names = {
        'A': 'EMA Spread', 'B': 'ADX', 'C': 'Whipsaw Counter',
        'D': 'BB Width', 'E': 'Realized Vol', 'F': 'EMA Slope'
    }

    for ft, stats in type_results.items():
        lines.append(f"- **{filter_names[ft]}**: {stats['pass']} pass, {stats['kill']} kill. Best OOS dSharpe: {stats['best_delta']:+.3f}")

    lines.append("")

    report_path = OUTPUT_DIR / 'v3_range_filter_results.md'
    with open(report_path, 'w') as f:
        f.write('\n'.join(lines))
    print(f"\nReport saved to: {report_path}")


if __name__ == '__main__':
    main()
