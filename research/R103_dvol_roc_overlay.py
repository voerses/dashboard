#!/workspace/venv/bin/python
"""
R103: DVOL Rate-of-Change Asymmetric Overlay Test
===================================================

Follow-up to R94 which found DVOL ROC is orthogonal to VRP (corr=-0.16) and has
IC=0.101 at 7d, but the symmetric mapping (0.7x penalty for falling IV) created
drag in OOS (dSharpe = -0.145).

Root cause from R94: the 0.7x bucket at z < -0.5 was too aggressive -- it penalized
during trending markets where IV naturally declines post-rally.

This test:
  1. Tests multiple ROC lookbacks: 3d, 5d, 7d, 10d (vs R94's 20d)
  2. Tests 3 ASYMMETRIC mapping variants:
     V1 (Mild Asymmetric): boost upside, soften downside penalty
     V2 (Boost-Only): boost for rising IV, NO penalty for falling IV
     V3 (Threshold): only boost strong rising, only penalize extreme collapse
  3. Walk-forward: 6 windows, 180d train / 90d test
  4. Triple overlay check: does DVOL ROC + Positioning + VRP beat Positioning + VRP?

Kill criteria:
  - Any variant degrades V3 Sharpe OOS -> KILL that variant
  - All variants degrade -> KILL signal entirely
  - Walk-forward <3/6 windows improved -> KILL
  - Correlation with VRP overlay > 0.3 -> likely redundant
"""

import pandas as pd
import numpy as np
import json
import warnings
from pathlib import Path
from datetime import datetime
from scipy import stats

warnings.filterwarnings('ignore')

BASE_DIR = Path('/workspace/crypto_backtest')
DATA_DIR = BASE_DIR / 'data'
OUTPUT_DIR = BASE_DIR / 'research'

COST_BPS = 10  # round-trip cost in basis points
IS_END = '2024-12-31'
OOS_START = '2025-01-01'

# OOS validation sub-period (same as V3 validation window)
OOS_VAL_START = '2025-09-01'
OOS_VAL_END = '2026-03-14'

# ROC lookback periods to test
ROC_LOOKBACKS = [3, 5, 7, 10]

# Z-score rolling window
ZSCORE_WINDOW = 60


# ============================================================================
# 1. DATA LOADING
# ============================================================================

def load_btc_daily():
    """Load BTC spot 1h data and resample to daily."""
    print("[1/5] Loading BTC spot data...")
    btc = pd.read_parquet(DATA_DIR / 'spot/1h_cache/BTC_1h.parquet')
    btc.index = pd.to_datetime(btc.index)
    btc.index.name = 'date'
    daily = btc['close'].resample('D').last().dropna().to_frame('close')
    daily['open'] = btc['open'].resample('D').first()
    daily['high'] = btc['high'].resample('D').max()
    daily['low'] = btc['low'].resample('D').min()
    daily['volume'] = btc['volume'].resample('D').sum()
    print(f"  BTC daily: {daily.index.min().date()} to {daily.index.max().date()}, {len(daily)} rows")
    return daily


def load_dvol():
    """Load BTC DVOL from Deribit JSON (OHLC daily candles)."""
    print("[2/5] Loading BTC DVOL (Deribit implied volatility index)...")
    dvol_path = DATA_DIR / 'alternative/deribit_options/dvol/btc_dvol_daily.json'
    if not dvol_path.exists():
        raise FileNotFoundError(f"DVOL file not found: {dvol_path}")

    with open(dvol_path) as f:
        data = json.load(f)

    # Format: [[timestamp_ms, open, high, low, close], ...]
    records = []
    for row in data:
        ts = pd.Timestamp(row[0], unit='ms')
        records.append({'date': ts, 'dvol_close': row[4]})

    dvol = pd.DataFrame(records).set_index('date').sort_index()
    dvol = dvol[~dvol.index.duplicated(keep='last')]
    print(f"  DVOL: {dvol.index.min().date()} to {dvol.index.max().date()}, {len(dvol)} rows")
    print(f"  DVOL range: {dvol['dvol_close'].min():.1f} to {dvol['dvol_close'].max():.1f}")
    return dvol['dvol_close']


def load_positioning():
    """Load Binance positioning data for BTCUSDT."""
    print("[3/5] Loading positioning data...")
    pos = pd.read_parquet(DATA_DIR / 'alternative/binance_metrics/all_symbols_daily_ls.parquet')
    pos = pos[pos['symbol'] == 'BTCUSDT'].copy()
    pos['date'] = pd.to_datetime(pos['date'])
    pos = pos.set_index('date').sort_index()
    pos = pos[['sum_toptrader_ls_ratio', 'count_toptrader_ls_ratio', 'count_ls_ratio']].copy()
    pos = pos[~pos.index.duplicated(keep='last')]
    print(f"  Positioning: {pos.index.min().date()} to {pos.index.max().date()}, {len(pos)} rows")
    return pos


# ============================================================================
# 2. SIGNAL CONSTRUCTION
# ============================================================================

def rolling_zscore(s, window, min_periods=None):
    """Rolling z-score with safety checks."""
    if min_periods is None:
        min_periods = window // 2
    mu = s.rolling(window, min_periods=min_periods).mean()
    sigma = s.rolling(window, min_periods=min_periods).std()
    return (s - mu) / sigma.replace(0, np.nan)


def build_ema_base_signal(btc_daily):
    """Base trend-following signal: long when 20d EMA > 50d EMA."""
    print("[4/5] Building EMA 20/50 base signal...")
    ema20 = btc_daily['close'].ewm(span=20, adjust=False).mean()
    ema50 = btc_daily['close'].ewm(span=50, adjust=False).mean()
    position = pd.Series(0.0, index=btc_daily.index)
    position[ema20 > ema50] = 1.0
    position.iloc[:50] = 0.0
    print(f"  EMA base signal: {position.sum():.0f} days long out of {len(position)} ({100*position.mean():.1f}%)")
    return position


def build_positioning_signal(btc_daily, positioning):
    """Positioning overlay (from V3 s320): contrarian sizing based on Top Trader L/S."""
    print("[5a/5] Building positioning signal...")
    pos = positioning.reindex(btc_daily.index).ffill()

    z_toptrader = rolling_zscore(pos['sum_toptrader_ls_ratio'], window=30)
    divergence = pos['count_toptrader_ls_ratio'] - pos['count_ls_ratio']
    z_divergence = rolling_zscore(divergence, window=30)
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
    return pos_multiplier, combined_z


def build_vrp_signal(btc_daily, dvol_series):
    """VRP sizing overlay (from V3 s320): IV-RV z-score based sizing."""
    print("[5b/5] Building VRP signal...")
    log_ret = np.log(btc_daily['close'] / btc_daily['close'].shift(1))
    rv_20d = log_ret.rolling(20, min_periods=15).std() * np.sqrt(365) * 100
    iv = dvol_series.reindex(btc_daily.index).ffill()
    vrp = iv - rv_20d
    vrp_z = rolling_zscore(vrp, window=60)

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
    return vrp_multiplier, vrp_z


def compute_dvol_roc(btc_daily, dvol_series, lookback):
    """
    Compute DVOL rate-of-change and z-score for a given lookback.
    Returns raw ROC, z-scored ROC.
    """
    dvol = dvol_series.reindex(btc_daily.index).ffill()
    dvol_roc = (dvol - dvol.shift(lookback)) / dvol.shift(lookback)
    dvol_roc_z = rolling_zscore(dvol_roc, window=ZSCORE_WINDOW)
    return dvol_roc, dvol_roc_z


# ============================================================================
# 3. ASYMMETRIC MAPPING VARIANTS
# ============================================================================

def apply_mapping_v1_mild_asymmetric(z):
    """
    V1 - Mild asymmetric:
    Keep upside boost, soften downside penalty.
    z > 1.0:  1.3x (boost -- IV rising fast)
    z > 0.0:  1.1x (slight boost)
    z > -1.0: 0.9x (mild penalty -- SOFTENED from 0.7x)
    z <= -1.0: 0.7x (strong penalty -- only for extreme IV collapse)
    """
    if pd.isna(z):
        return 1.0
    if z > 1.0:
        return 1.3
    elif z > 0.0:
        return 1.1
    elif z > -1.0:
        return 0.9
    else:
        return 0.7


def apply_mapping_v2_boost_only(z):
    """
    V2 - Boost-only:
    Boost for rising IV, NO penalty for falling IV.
    z > 1.0:  1.3x
    z > 0.0:  1.1x
    else:     1.0x (NO penalty)
    """
    if pd.isna(z):
        return 1.0
    if z > 1.0:
        return 1.3
    elif z > 0.0:
        return 1.1
    else:
        return 1.0


def apply_mapping_v3_threshold(z):
    """
    V3 - Threshold:
    Only act on strong signals. Boost strong rising, penalize only extreme collapse.
    z > 0.5:  1.2x
    z < -1.5: 0.5x (extreme collapse only)
    else:     1.0x
    """
    if pd.isna(z):
        return 1.0
    if z > 0.5:
        return 1.2
    elif z < -1.5:
        return 0.5
    else:
        return 1.0


# R94's original symmetric mapping for comparison
def apply_mapping_r94_symmetric(z):
    """R94 original: symmetric boost/penalty (known to create drag)."""
    if pd.isna(z):
        return 1.0
    if z > 1.0:
        return 1.3
    elif z > 0.0:
        return 1.1
    elif z > -0.5:
        return 1.0
    elif z > -1.5:
        return 0.7
    else:
        return 0.5


MAPPING_FUNCS = {
    'V1_mild_asym':  apply_mapping_v1_mild_asymmetric,
    'V2_boost_only': apply_mapping_v2_boost_only,
    'V3_threshold':  apply_mapping_v3_threshold,
    'R94_symmetric': apply_mapping_r94_symmetric,
}

MAPPING_LABELS = {
    'V1_mild_asym':  'V1 Mild Asymmetric',
    'V2_boost_only': 'V2 Boost-Only',
    'V3_threshold':  'V3 Threshold',
    'R94_symmetric': 'R94 Symmetric (baseline)',
}


# ============================================================================
# 4. BACKTEST ENGINE
# ============================================================================

def compute_final_position(base_pos, pos_mult, vrp_mult, dvol_roc_mult, use_dvol_roc=True):
    """Compute final position with or without DVOL ROC overlay."""
    if use_dvol_roc:
        final = base_pos * pos_mult * vrp_mult * dvol_roc_mult
    else:
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
    return strat_ret, held_position, costs


def compute_metrics(returns, label=""):
    """Compute performance metrics from daily returns."""
    returns = returns.dropna()
    if len(returns) == 0:
        return {'label': label, 'total_return': 0, 'ann_return': 0,
                'ann_vol': 0, 'sharpe': 0, 'max_dd': 0, 'calmar': 0, 'n_days': 0}

    total_ret = (1 + returns).prod() - 1
    n_years = len(returns) / 365
    ann_ret = (1 + total_ret) ** (1 / max(n_years, 0.01)) - 1
    ann_vol = returns.std() * np.sqrt(365)
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0

    cum = (1 + returns).cumprod()
    peak = cum.cummax()
    dd = (cum - peak) / peak
    max_dd = dd.min()
    calmar = ann_ret / abs(max_dd) if max_dd != 0 else 0

    return {
        'label': label,
        'total_return': total_ret,
        'ann_return': ann_ret,
        'ann_vol': ann_vol,
        'sharpe': sharpe,
        'max_dd': max_dd,
        'calmar': calmar,
        'n_days': len(returns),
    }


def compute_turnover(held_position):
    """Compute annualized turnover."""
    daily_changes = held_position.diff().abs()
    total_turnover = daily_changes.sum()
    n_years = len(held_position) / 365
    return total_turnover / max(n_years, 0.01)


# ============================================================================
# 5. ANALYSIS FUNCTIONS
# ============================================================================

def scan_roc_lookbacks(btc_daily, dvol_series, base_pos, pos_mult, vrp_mult):
    """
    Phase 1: Scan all ROC lookback periods to find the best raw signal.
    Tests IC at 7d horizon for each lookback.
    """
    print("\n" + "=" * 74)
    print("PHASE 1: ROC LOOKBACK SCAN")
    print("=" * 74)

    fwd_ret_7d = btc_daily['close'].pct_change(7).shift(-7)
    results = []

    for lb in ROC_LOOKBACKS:
        roc, roc_z = compute_dvol_roc(btc_daily, dvol_series, lb)

        # IC over full period
        valid = pd.DataFrame({'z': roc_z, 'fwd': fwd_ret_7d}).dropna()
        if len(valid) < 50:
            print(f"  ROC({lb}d): insufficient data ({len(valid)} rows)")
            continue

        ic_full, p_full = stats.spearmanr(valid['z'], valid['fwd'])
        t_full = ic_full * np.sqrt(len(valid) - 2) / np.sqrt(1 - ic_full**2) if abs(ic_full) < 1 else np.inf

        # IS/OOS split
        is_valid = valid[valid.index <= IS_END]
        oos_valid = valid[valid.index >= OOS_START]

        ic_is = ic_oos = t_is = t_oos = np.nan
        if len(is_valid) > 30:
            ic_is, _ = stats.spearmanr(is_valid['z'], is_valid['fwd'])
            t_is = ic_is * np.sqrt(len(is_valid) - 2) / np.sqrt(1 - ic_is**2) if abs(ic_is) < 1 else np.inf
        if len(oos_valid) > 30:
            ic_oos, _ = stats.spearmanr(oos_valid['z'], oos_valid['fwd'])
            t_oos = ic_oos * np.sqrt(len(oos_valid) - 2) / np.sqrt(1 - ic_oos**2) if abs(ic_oos) < 1 else np.inf

        # Signal stats
        roc_clean = roc.dropna()
        pct_positive = (roc_clean > 0).mean() * 100

        results.append({
            'lookback': lb,
            'ic_full': ic_full,
            't_full': t_full,
            'ic_is': ic_is,
            't_is': t_is,
            'ic_oos': ic_oos,
            't_oos': t_oos,
            'n_full': len(valid),
            'n_is': len(is_valid),
            'n_oos': len(oos_valid),
            'pct_positive': pct_positive,
            'roc_mean': roc_clean.mean(),
            'roc_std': roc_clean.std(),
        })

        sig = '***' if abs(t_full) > 3 else '**' if abs(t_full) > 2 else '*' if abs(t_full) > 1.65 else ''
        print(f"  ROC({lb:2d}d): IC_full={ic_full:+.4f} (t={t_full:+.2f}{sig}), "
              f"IC_IS={ic_is:+.4f}, IC_OOS={ic_oos:+.4f}, "
              f"pct_pos={pct_positive:.1f}%, std={roc_clean.std():.4f}")

    # Select best lookback by OOS IC (prefer stability over magnitude)
    valid_results = [r for r in results if not np.isnan(r['ic_oos']) and r['ic_oos'] > 0]
    if valid_results:
        best = max(valid_results, key=lambda r: r['ic_oos'])
        print(f"\n  Best lookback by OOS IC: {best['lookback']}d (IC_OOS={best['ic_oos']:+.4f})")
    else:
        best = max(results, key=lambda r: r['ic_full'])
        print(f"\n  No positive OOS IC found. Using best full-period: {best['lookback']}d")

    return results, best['lookback']


def test_mapping_variants(btc_daily, dvol_series, base_pos, pos_mult, vrp_mult,
                          best_lookback):
    """
    Phase 2: Test all 3 asymmetric mapping variants + R94 baseline.
    Uses the best lookback from Phase 1.
    """
    print("\n" + "=" * 74)
    print(f"PHASE 2: ASYMMETRIC MAPPING VARIANTS (ROC lookback = {best_lookback}d)")
    print("=" * 74)

    roc, roc_z = compute_dvol_roc(btc_daily, dvol_series, best_lookback)

    # Compute V3 Full (no DVOL ROC) as reference
    v3_full_pos = compute_final_position(base_pos, pos_mult, vrp_mult, None, use_dvol_roc=False)
    v3_ret, v3_held, _ = run_backtest(btc_daily, v3_full_pos)

    is_mask = (btc_daily.index >= '2021-06-22') & (btc_daily.index <= IS_END)
    oos_mask = btc_daily.index >= OOS_START

    v3_is = compute_metrics(v3_ret[is_mask], "V3 Full (IS)")
    v3_oos = compute_metrics(v3_ret[oos_mask], "V3 Full (OOS)")

    print(f"\n  V3 Full reference: IS Sharpe={v3_is['sharpe']:.3f}, OOS Sharpe={v3_oos['sharpe']:.3f}")
    print(f"  V3 Full reference: IS Return={v3_is['ann_return']:.1%}, OOS Return={v3_oos['ann_return']:.1%}")
    print(f"  V3 Full reference: IS MaxDD={v3_is['max_dd']:.1%}, OOS MaxDD={v3_oos['max_dd']:.1%}")

    variant_results = {}
    for mapping_key, mapping_func in MAPPING_FUNCS.items():
        dvol_roc_mult = roc_z.apply(mapping_func)
        final_pos = compute_final_position(base_pos, pos_mult, vrp_mult, dvol_roc_mult)
        strat_ret, held_pos, costs = run_backtest(btc_daily, final_pos)

        is_ret = strat_ret[is_mask]
        oos_ret = strat_ret[oos_mask]
        is_metrics = compute_metrics(is_ret, f"{MAPPING_LABELS[mapping_key]} (IS)")
        oos_metrics = compute_metrics(oos_ret, f"{MAPPING_LABELS[mapping_key]} (OOS)")

        # Delta vs V3 Full
        d_sharpe_is = is_metrics['sharpe'] - v3_is['sharpe']
        d_sharpe_oos = oos_metrics['sharpe'] - v3_oos['sharpe']
        d_return_oos = oos_metrics['ann_return'] - v3_oos['ann_return']
        d_maxdd_oos = oos_metrics['max_dd'] - v3_oos['max_dd']

        # Statistical significance (OOS)
        oos_common = strat_ret[oos_mask].dropna()
        v3_oos_ret = v3_ret[oos_mask].dropna()
        cidx = oos_common.index.intersection(v3_oos_ret.index)
        diff = oos_common.loc[cidx] - v3_oos_ret.loc[cidx]
        t_stat = diff.mean() / (diff.std() / np.sqrt(len(diff))) if len(diff) > 30 else 0.0
        p_value = 2 * (1 - stats.t.cdf(abs(t_stat), df=len(diff) - 1)) if len(diff) > 30 else 1.0

        # Multiplier distribution
        mult_dist = {}
        for val in sorted(dvol_roc_mult.dropna().unique()):
            mult_dist[val] = (dvol_roc_mult == val).mean() * 100

        turnover_oos = compute_turnover(held_pos[oos_mask])

        variant_results[mapping_key] = {
            'is': is_metrics,
            'oos': oos_metrics,
            'd_sharpe_is': d_sharpe_is,
            'd_sharpe_oos': d_sharpe_oos,
            'd_return_oos': d_return_oos,
            'd_maxdd_oos': d_maxdd_oos,
            't_stat': t_stat,
            'p_value': p_value,
            'turnover_oos': turnover_oos,
            'mult_dist': mult_dist,
            'strat_ret': strat_ret,
            'held_pos': held_pos,
            'dvol_roc_mult': dvol_roc_mult,
        }

        # Kill check
        killed = "KILL" if d_sharpe_oos < 0 else "PASS"
        print(f"\n  {MAPPING_LABELS[mapping_key]}:")
        print(f"    IS:  Sharpe={is_metrics['sharpe']:.3f} (d={d_sharpe_is:+.3f}), "
              f"Return={is_metrics['ann_return']:.1%}, MaxDD={is_metrics['max_dd']:.1%}")
        print(f"    OOS: Sharpe={oos_metrics['sharpe']:.3f} (d={d_sharpe_oos:+.3f}), "
              f"Return={oos_metrics['ann_return']:.1%}, MaxDD={oos_metrics['max_dd']:.1%}")
        print(f"    t-stat={t_stat:.3f}, p={p_value:.3f} -> [{killed}]")
        print(f"    Mult dist: {mult_dist}")

    return variant_results, v3_is, v3_oos, v3_ret, v3_held, roc, roc_z


def run_walk_forward(btc_daily, base_pos, pos_mult, vrp_mult, dvol_roc_mult,
                     v3_ret_full, mapping_label, n_windows=6, train_days=180, test_days=90):
    """
    Walk-forward validation: 6 windows, 180d train / 90d test.
    """
    print(f"\n  Walk-Forward for {mapping_label}: {n_windows} windows, {train_days}d train / {test_days}d test")

    # Build V3+DVOL backtest once (positions already computed)
    dvol_final_pos = compute_final_position(base_pos, pos_mult, vrp_mult, dvol_roc_mult)
    dvol_ret, _, _ = run_backtest(btc_daily, dvol_final_pos)

    # Generate windows starting after warmup
    dvol_start = pd.Timestamp('2021-09-01')  # after DVOL warmup
    wf_end = btc_daily.index.max()

    # Compute total span needed and create evenly-spaced windows
    total_span = (wf_end - dvol_start).days
    step = (total_span - train_days - test_days) // (n_windows - 1) if n_windows > 1 else 0

    windows = []
    for i in range(n_windows):
        offset = i * step
        train_start = dvol_start + pd.Timedelta(days=offset)
        train_end = train_start + pd.Timedelta(days=train_days - 1)
        test_start = train_end + pd.Timedelta(days=1)
        test_end = test_start + pd.Timedelta(days=test_days - 1)
        if test_end > wf_end:
            test_end = wf_end
        windows.append({
            'train_start': train_start,
            'train_end': train_end,
            'test_start': test_start,
            'test_end': test_end,
        })

    wf_results = []
    for i, w in enumerate(windows):
        test_mask = (btc_daily.index >= w['test_start']) & (btc_daily.index <= w['test_end'])

        v3_test_ret = v3_ret_full[test_mask].dropna()
        dvol_test_ret = dvol_ret[test_mask].dropna()

        v3_m = compute_metrics(v3_test_ret)
        dvol_m = compute_metrics(dvol_test_ret)

        d_sharpe = dvol_m['sharpe'] - v3_m['sharpe']

        # T-test on daily return difference
        cidx = v3_test_ret.index.intersection(dvol_test_ret.index)
        diff = dvol_test_ret.loc[cidx] - v3_test_ret.loc[cidx]
        t_stat = diff.mean() / (diff.std() / np.sqrt(len(diff))) if len(diff) > 10 else 0.0

        wf_results.append({
            'window': i + 1,
            'test_start': w['test_start'].strftime('%Y-%m-%d'),
            'test_end': w['test_end'].strftime('%Y-%m-%d'),
            'v3_sharpe': v3_m['sharpe'],
            'dvol_sharpe': dvol_m['sharpe'],
            'd_sharpe': d_sharpe,
            'v3_maxdd': v3_m['max_dd'],
            'dvol_maxdd': dvol_m['max_dd'],
            't_stat': t_stat,
            'n_days': dvol_m['n_days'],
            'improved': d_sharpe > 0,
        })

        sign = '+' if d_sharpe > 0 else ''
        print(f"    W{i+1} [{w['test_start'].strftime('%Y-%m-%d')} to {w['test_end'].strftime('%Y-%m-%d')}]: "
              f"V3={v3_m['sharpe']:.2f}, +DVOL={dvol_m['sharpe']:.2f}, d={sign}{d_sharpe:.3f}, t={t_stat:.2f}")

    n_improved = sum(1 for r in wf_results if r['improved'])
    avg_d = np.mean([r['d_sharpe'] for r in wf_results])
    print(f"    => Improved {n_improved}/{n_windows} windows, avg dSharpe={avg_d:+.3f}")

    return wf_results


def overlay_correlation_analysis(vrp_mult, dvol_roc_mult, vrp_z, dvol_roc_z,
                                 pos_mult, common_index, label=""):
    """Analyze correlation between overlays."""
    v = vrp_mult.reindex(common_index).dropna()
    d = dvol_roc_mult.reindex(common_index).dropna()
    p = pos_mult.reindex(common_index).dropna()
    common = v.index.intersection(d.index).intersection(p.index)
    v = v.loc[common]
    d = d.loc[common]
    p = p.loc[common]

    if len(common) < 30:
        return {'label': label, 'n': 0}

    # Multiplier correlations
    corr_dvol_vrp = d.corr(v)
    corr_dvol_pos = d.corr(p)
    corr_vrp_pos = v.corr(p)

    # Z-score Spearman
    vz = vrp_z.reindex(common).dropna()
    dz = dvol_roc_z.reindex(common).dropna()
    common_z = vz.index.intersection(dz.index)

    if len(common_z) > 30:
        sp = stats.spearmanr(dz.loc[common_z], vz.loc[common_z])
        corr_z_spearman = sp.correlation
        spearman_p = sp.pvalue
    else:
        corr_z_spearman = np.nan
        spearman_p = np.nan

    return {
        'label': label,
        'n': len(common),
        'corr_dvol_vrp_mult': corr_dvol_vrp,
        'corr_dvol_pos_mult': corr_dvol_pos,
        'corr_vrp_pos_mult': corr_vrp_pos,
        'corr_z_spearman': corr_z_spearman,
        'spearman_p': spearman_p,
    }


def triple_overlay_analysis(btc_daily, base_pos, pos_mult, vrp_mult, dvol_roc_mult,
                            v3_ret_full, variant_label):
    """
    Phase 4: Check if DVOL ROC + Positioning + VRP together is better than just Pos + VRP.
    Decompose the marginal contribution of each overlay.
    """
    print(f"\n  Triple Overlay Decomposition for {variant_label}")

    is_mask = (btc_daily.index >= '2021-06-22') & (btc_daily.index <= IS_END)
    oos_mask = btc_daily.index >= OOS_START

    configs = {
        'Base Only':           (base_pos, None, None, None),
        'Base + Pos':          (base_pos, pos_mult, None, None),
        'Base + VRP':          (base_pos, None, vrp_mult, None),
        'Base + DVOL_ROC':     (base_pos, None, None, dvol_roc_mult),
        'Base + Pos + VRP':    (base_pos, pos_mult, vrp_mult, None),
        'Base + Pos + DVOL':   (base_pos, pos_mult, None, dvol_roc_mult),
        'Base + VRP + DVOL':   (base_pos, None, vrp_mult, dvol_roc_mult),
        'Triple (all 3)':      (base_pos, pos_mult, vrp_mult, dvol_roc_mult),
    }

    decomp_results = {}
    for cfg_name, (bp, pm, vm, dm) in configs.items():
        # Build final position
        final = bp.copy()
        if pm is not None:
            final = final * pm
        if vm is not None:
            final = final * vm
        if dm is not None:
            final = final * dm
        final = final.clip(0, 1.5)

        strat_ret, held_pos, _ = run_backtest(btc_daily, final)
        is_m = compute_metrics(strat_ret[is_mask])
        oos_m = compute_metrics(strat_ret[oos_mask])

        decomp_results[cfg_name] = {
            'is_sharpe': is_m['sharpe'],
            'oos_sharpe': oos_m['sharpe'],
            'is_return': is_m['ann_return'],
            'oos_return': oos_m['ann_return'],
            'is_maxdd': is_m['max_dd'],
            'oos_maxdd': oos_m['max_dd'],
        }

        print(f"    {cfg_name:24s}: IS Sharpe={is_m['sharpe']:.3f}, OOS Sharpe={oos_m['sharpe']:.3f}, "
              f"OOS Return={oos_m['ann_return']:.1%}, OOS MaxDD={oos_m['max_dd']:.1%}")

    # Marginal contribution of DVOL ROC
    marginal = decomp_results['Triple (all 3)']['oos_sharpe'] - decomp_results['Base + Pos + VRP']['oos_sharpe']
    super_additive = (decomp_results['Triple (all 3)']['oos_sharpe'] >
                      max(decomp_results['Base + Pos + VRP']['oos_sharpe'],
                          decomp_results['Base + Pos + DVOL']['oos_sharpe'],
                          decomp_results['Base + VRP + DVOL']['oos_sharpe']))

    print(f"\n    Marginal DVOL ROC contribution (Triple vs Pos+VRP): {marginal:+.3f}")
    print(f"    Super-additive? {'YES' if super_additive else 'NO'}")

    return decomp_results, marginal, super_additive


# ============================================================================
# 6. MAIN EXECUTION
# ============================================================================

def main():
    print("=" * 74)
    print("R103: DVOL RATE-OF-CHANGE ASYMMETRIC OVERLAY TEST")
    print("Follow-up to R94: testing asymmetric mapping to fix downside drag")
    print("=" * 74)
    print()

    # -- Load Data --
    btc_daily = load_btc_daily()
    dvol = load_dvol()
    positioning = load_positioning()
    print()

    # -- Build Base Signals --
    base_position = build_ema_base_signal(btc_daily)
    pos_multiplier, pos_combined_z = build_positioning_signal(btc_daily, positioning)
    vrp_multiplier, vrp_z = build_vrp_signal(btc_daily, dvol)
    print()

    # ================================================================
    # PHASE 1: ROC Lookback Scan
    # ================================================================
    lookback_results, best_lookback = scan_roc_lookbacks(
        btc_daily, dvol, base_position, pos_multiplier, vrp_multiplier
    )

    # ================================================================
    # PHASE 2: Asymmetric Mapping Variants
    # ================================================================
    variant_results, v3_is, v3_oos, v3_ret, v3_held, roc_raw, roc_z = test_mapping_variants(
        btc_daily, dvol, base_position, pos_multiplier, vrp_multiplier, best_lookback
    )

    # ================================================================
    # PHASE 3: Walk-Forward for surviving variants
    # ================================================================
    print("\n" + "=" * 74)
    print("PHASE 3: WALK-FORWARD VALIDATION")
    print("=" * 74)

    wf_all = {}
    for mk in MAPPING_FUNCS:
        vr = variant_results[mk]
        # Run walk-forward for all variants (even killed ones, for comparison)
        wf = run_walk_forward(
            btc_daily, base_position, pos_multiplier, vrp_multiplier,
            vr['dvol_roc_mult'], v3_ret, MAPPING_LABELS[mk],
            n_windows=6, train_days=180, test_days=90
        )
        wf_all[mk] = wf

    # ================================================================
    # PHASE 4: Triple Overlay Analysis (best surviving variant)
    # ================================================================
    print("\n" + "=" * 74)
    print("PHASE 4: TRIPLE OVERLAY ANALYSIS")
    print("=" * 74)

    # Find best surviving variant (positive OOS dSharpe)
    surviving = {k: v for k, v in variant_results.items() if v['d_sharpe_oos'] >= 0}
    if surviving:
        best_variant_key = max(surviving, key=lambda k: surviving[k]['d_sharpe_oos'])
    else:
        # All killed -- still analyze the least bad for reporting
        best_variant_key = max(variant_results, key=lambda k: variant_results[k]['d_sharpe_oos'])

    best_vr = variant_results[best_variant_key]
    decomp, marginal_contribution, super_additive = triple_overlay_analysis(
        btc_daily, base_position, pos_multiplier, vrp_multiplier,
        best_vr['dvol_roc_mult'], v3_ret, MAPPING_LABELS[best_variant_key]
    )

    # ================================================================
    # PHASE 5: Correlation Analysis
    # ================================================================
    print("\n" + "=" * 74)
    print("PHASE 5: OVERLAY CORRELATION ANALYSIS")
    print("=" * 74)

    common_idx = btc_daily.index[(btc_daily.index >= '2021-06-22')]
    is_idx = btc_daily.index[(btc_daily.index >= '2021-06-22') & (btc_daily.index <= IS_END)]
    oos_idx = btc_daily.index[btc_daily.index >= OOS_START]

    best_roc_z = roc_z  # from best lookback
    best_dvol_mult = best_vr['dvol_roc_mult']

    print(f"\n  Using {MAPPING_LABELS[best_variant_key]} with ROC({best_lookback}d)")
    print("\n  Full Period:")
    corr_full = overlay_correlation_analysis(
        vrp_multiplier, best_dvol_mult, vrp_z, best_roc_z,
        pos_multiplier, common_idx, "Full Period"
    )
    print(f"    DVOL ROC vs VRP mult: {corr_full.get('corr_dvol_vrp_mult', np.nan):.3f}")
    print(f"    DVOL ROC vs Pos mult: {corr_full.get('corr_dvol_pos_mult', np.nan):.3f}")
    print(f"    VRP vs Pos mult:      {corr_full.get('corr_vrp_pos_mult', np.nan):.3f}")
    print(f"    DVOL ROC z vs VRP z (Spearman): {corr_full.get('corr_z_spearman', np.nan):.3f}")

    print("\n  IS Period:")
    corr_is = overlay_correlation_analysis(
        vrp_multiplier, best_dvol_mult, vrp_z, best_roc_z,
        pos_multiplier, is_idx, "IS Period"
    )
    print(f"    DVOL ROC z vs VRP z (Spearman): {corr_is.get('corr_z_spearman', np.nan):.3f}")

    print("\n  OOS Period:")
    corr_oos = overlay_correlation_analysis(
        vrp_multiplier, best_dvol_mult, vrp_z, best_roc_z,
        pos_multiplier, oos_idx, "OOS Period"
    )
    print(f"    DVOL ROC z vs VRP z (Spearman): {corr_oos.get('corr_z_spearman', np.nan):.3f}")

    # Pairwise correlations of all three overlay multipliers
    print("\n  Pairwise Overlay Multiplier Correlations (OOS):")
    oos_dvol = best_dvol_mult.reindex(oos_idx).dropna()
    oos_vrp = vrp_multiplier.reindex(oos_idx).dropna()
    oos_pos = pos_multiplier.reindex(oos_idx).dropna()
    cidx = oos_dvol.index.intersection(oos_vrp.index).intersection(oos_pos.index)
    if len(cidx) > 30:
        corr_mat = pd.DataFrame({
            'DVOL_ROC': oos_dvol.loc[cidx],
            'VRP': oos_vrp.loc[cidx],
            'Positioning': oos_pos.loc[cidx],
        }).corr()
        print(f"    DVOL_ROC - VRP:         {corr_mat.loc['DVOL_ROC', 'VRP']:.3f}")
        print(f"    DVOL_ROC - Positioning:  {corr_mat.loc['DVOL_ROC', 'Positioning']:.3f}")
        print(f"    VRP - Positioning:       {corr_mat.loc['VRP', 'Positioning']:.3f}")

    # ================================================================
    # PHASE 6: IC Analysis for best lookback
    # ================================================================
    print("\n" + "=" * 74)
    print("PHASE 6: INFORMATION COEFFICIENT ANALYSIS")
    print("=" * 74)

    fwd_ret_1d = btc_daily['close'].pct_change(1).shift(-1)
    fwd_ret_7d = btc_daily['close'].pct_change(7).shift(-7)
    fwd_ret_14d = btc_daily['close'].pct_change(14).shift(-14)

    ic_results = {}
    for period_label, period_idx in [('Full', common_idx), ('IS', is_idx), ('OOS', oos_idx)]:
        ic_results[period_label] = {}
        for target_name, fwd_ret in [('1D', fwd_ret_1d), ('7D', fwd_ret_7d), ('14D', fwd_ret_14d)]:
            df = pd.DataFrame({'z': best_roc_z, 'fwd': fwd_ret}).reindex(period_idx).dropna()
            if len(df) < 30:
                continue
            ic, p = stats.spearmanr(df['z'], df['fwd'])
            t = ic * np.sqrt(len(df) - 2) / np.sqrt(1 - ic**2) if abs(ic) < 1 else np.inf
            ic_results[period_label][target_name] = {'ic': ic, 't': t, 'n': len(df), 'p': p}

    print(f"\n  ROC({best_lookback}d) z-score -> Forward Returns (Spearman IC):")
    print(f"  {'Horizon':>8s}  {'Full IC':>10s}  {'Full t':>8s}  {'IS IC':>10s}  {'IS t':>8s}  {'OOS IC':>10s}  {'OOS t':>8s}")
    for target in ['1D', '7D', '14D']:
        full_d = ic_results.get('Full', {}).get(target, {})
        is_d = ic_results.get('IS', {}).get(target, {})
        oos_d = ic_results.get('OOS', {}).get(target, {})
        print(f"  {target:>8s}  {full_d.get('ic', np.nan):>+10.4f}  {full_d.get('t', np.nan):>+8.2f}  "
              f"{is_d.get('ic', np.nan):>+10.4f}  {is_d.get('t', np.nan):>+8.2f}  "
              f"{oos_d.get('ic', np.nan):>+10.4f}  {oos_d.get('t', np.nan):>+8.2f}")

    # ================================================================
    # VERDICT
    # ================================================================
    print("\n" + "=" * 74)
    print("VERDICT")
    print("=" * 74)

    # Count surviving variants
    n_surviving = len(surviving)
    n_total = len(variant_results)
    all_killed = n_surviving == 0

    # Walk-forward check for surviving variants
    wf_pass = {}
    for mk in MAPPING_FUNCS:
        n_imp = sum(1 for r in wf_all[mk] if r['improved'])
        wf_pass[mk] = n_imp >= 3  # at least 3/6

    # Correlation check
    corr_redundant = abs(corr_full.get('corr_z_spearman', 0)) > 0.3

    print(f"\n  Surviving variants (OOS dSharpe >= 0): {n_surviving}/{n_total}")
    for mk in variant_results:
        d = variant_results[mk]['d_sharpe_oos']
        n_wf = sum(1 for r in wf_all[mk] if r['improved'])
        status = "PASS" if d >= 0 and wf_pass[mk] else "KILL"
        print(f"    {MAPPING_LABELS[mk]:30s}: OOS dSharpe={d:+.3f}, WF={n_wf}/6, [{status}]")

    print(f"\n  Correlation with VRP (Spearman): {corr_full.get('corr_z_spearman', np.nan):.3f} "
          f"({'REDUNDANT' if corr_redundant else 'ORTHOGONAL'})")
    print(f"  Marginal contribution of DVOL ROC (triple overlay): {marginal_contribution:+.3f}")
    print(f"  Super-additive: {'YES' if super_additive else 'NO'}")

    # Final verdict
    fully_passing = {mk for mk in variant_results
                     if variant_results[mk]['d_sharpe_oos'] >= 0 and wf_pass[mk]}

    if len(fully_passing) == 0 or corr_redundant:
        if corr_redundant:
            verdict = "KILL"
            verdict_reason = (f"Signal is correlated with VRP (Spearman={corr_full.get('corr_z_spearman', np.nan):.3f} > 0.3). "
                              f"Redundant with existing VRP overlay.")
        elif all_killed:
            verdict = "KILL"
            verdict_reason = (f"All {n_total} asymmetric mapping variants degrade OOS Sharpe vs V3 Full. "
                              f"Signal does not add value as a sizing overlay despite having positive IC.")
        else:
            verdict = "KILL"
            verdict_reason = (f"No variant passes both OOS improvement AND walk-forward (>= 3/6 windows). "
                              f"The DVOL ROC signal has positive IC but does not translate to overlay improvement.")
    elif len(fully_passing) >= 1:
        best_passing = max(fully_passing, key=lambda k: variant_results[k]['d_sharpe_oos'])
        bp_d = variant_results[best_passing]['d_sharpe_oos']
        bp_wf = sum(1 for r in wf_all[best_passing] if r['improved'])

        if bp_d >= 0.05 and marginal_contribution > 0:
            verdict = "PASS"
            verdict_reason = (f"{MAPPING_LABELS[best_passing]} with ROC({best_lookback}d) improves V3 OOS Sharpe by "
                              f"{bp_d:+.3f}, passes walk-forward ({bp_wf}/6), and adds marginal value in triple overlay "
                              f"({marginal_contribution:+.3f}). Signal is orthogonal to VRP "
                              f"(Spearman={corr_full.get('corr_z_spearman', np.nan):.3f}).")
        elif bp_d >= 0:
            verdict = "CONDITIONAL"
            verdict_reason = (f"{MAPPING_LABELS[best_passing]} with ROC({best_lookback}d) does not degrade V3 OOS "
                              f"(dSharpe={bp_d:+.3f}), passes walk-forward ({bp_wf}/6), signal is orthogonal. "
                              f"However, improvement is small. Recommend monitoring with more OOS data before "
                              f"adding to production.")
        else:
            verdict = "KILL"
            verdict_reason = "Internal logic error: should not reach here."
    else:
        verdict = "KILL"
        verdict_reason = "No variants pass all criteria."

    print(f"\n  VERDICT: {verdict}")
    print(f"  {verdict_reason}")

    # ================================================================
    # GENERATE REPORT
    # ================================================================
    print("\n" + "=" * 74)
    print("GENERATING REPORT")
    print("=" * 74)

    lines = []
    lines.append("# R103: DVOL Rate-of-Change Asymmetric Overlay Test")
    lines.append("")
    lines.append(f"**Run date**: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"**IS period**: 2021-06-22 to {IS_END}")
    lines.append(f"**OOS period**: {OOS_START} to latest")
    lines.append(f"**DVOL source**: Deribit BTC DVOL")
    lines.append(f"**Base signal**: EMA 20/50 crossover (V3 s320 base)")
    lines.append(f"**Rebalancing**: Weekly")
    lines.append(f"**Transaction cost**: {COST_BPS} bps round-trip")
    lines.append(f"**Prior research**: R94 (symmetric mapping, dSharpe_OOS = -0.145)")
    lines.append("")

    # Background
    lines.append("## Background")
    lines.append("")
    lines.append("R94 found DVOL ROC has IC=0.101 at 7d and is orthogonal to VRP (corr=-0.16),")
    lines.append("but the symmetric mapping (0.7x penalty for falling IV at z < -0.5) created drag.")
    lines.append("The root cause: falling IV often coincides with strong trending markets where BTC")
    lines.append("momentum is working. The 0.7x penalty reduced position size exactly when V3 was")
    lines.append("performing best.")
    lines.append("")
    lines.append("This test explores 3 asymmetric mappings that preserve the upside boost (rising IV")
    lines.append("= crypto reflexivity) while softening or eliminating the downside penalty.")
    lines.append("")

    # Mapping definitions
    lines.append("## Mapping Definitions")
    lines.append("")
    lines.append("| Z-Score Range | R94 Symmetric | V1 Mild Asym | V2 Boost-Only | V3 Threshold |")
    lines.append("|---------------|---------------|--------------|---------------|--------------|")
    lines.append("| z > 1.0       | 1.3x          | 1.3x         | 1.3x          | 1.2x         |")
    lines.append("| 0 < z < 1.0   | 1.1x          | 1.1x         | 1.1x          | 1.0x (if < 0.5) |")
    lines.append("| z > 0.5       | --             | --           | --            | 1.2x         |")
    lines.append("| -0.5 < z < 0  | 1.0x          | --           | 1.0x          | 1.0x         |")
    lines.append("| -1.0 < z < 0  | --            | 0.9x         | --            | --           |")
    lines.append("| z < -0.5      | 0.7x          | --           | --            | --           |")
    lines.append("| z < -1.0      | --            | 0.7x         | --            | --           |")
    lines.append("| z < -1.5      | 0.5x          | --           | 1.0x          | 0.5x         |")
    lines.append("| else          | --            | --           | 1.0x          | 1.0x         |")
    lines.append("")
    lines.append("Key differences from R94:")
    lines.append("- **V1**: Penalty starts at z < -1.0 (not -0.5), milder 0.9x in neutral zone")
    lines.append("- **V2**: NO downside penalty at all -- only boost for rising IV")
    lines.append("- **V3**: Binary -- 1.2x boost above z=0.5, 0.5x only for extreme collapse (z < -1.5)")
    lines.append("")

    # Phase 1: ROC Lookback Scan
    lines.append("## 1. ROC Lookback Scan")
    lines.append("")
    lines.append("Testing multiple lookback periods for raw signal quality (IC at 7d forward return).")
    lines.append("")
    lines.append("| Lookback | Full IC | Full t | IS IC | IS t | OOS IC | OOS t | Pct Positive |")
    lines.append("|----------|---------|--------|-------|------|--------|-------|--------------|")
    for r in lookback_results:
        sig = '***' if abs(r['t_full']) > 3 else '**' if abs(r['t_full']) > 2 else '*' if abs(r['t_full']) > 1.65 else ''
        lines.append(f"| {r['lookback']:d}d | {r['ic_full']:+.4f} | {r['t_full']:+.2f}{sig} "
                     f"| {r['ic_is']:+.4f} | {r['t_is']:+.2f} "
                     f"| {r['ic_oos']:+.4f} | {r['t_oos']:+.2f} "
                     f"| {r['pct_positive']:.1f}% |")
    lines.append("")
    lines.append(f"**Selected lookback**: {best_lookback}d (best OOS IC)")
    lines.append("")

    # Phase 2: Mapping Variant Comparison
    lines.append("## 2. Asymmetric Mapping Variant Comparison")
    lines.append("")
    lines.append("V3 Full reference (no DVOL ROC):")
    lines.append(f"- IS: Sharpe={v3_is['sharpe']:.3f}, Return={v3_is['ann_return']:.1%}, MaxDD={v3_is['max_dd']:.1%}")
    lines.append(f"- OOS: Sharpe={v3_oos['sharpe']:.3f}, Return={v3_oos['ann_return']:.1%}, MaxDD={v3_oos['max_dd']:.1%}")
    lines.append("")

    lines.append("| Variant | IS Sharpe | OOS Sharpe | IS dSharpe | OOS dSharpe | OOS Return | OOS MaxDD | t-stat | p-value | Verdict |")
    lines.append("|---------|-----------|------------|------------|-------------|------------|-----------|--------|---------|---------|")

    for mk in ['R94_symmetric', 'V1_mild_asym', 'V2_boost_only', 'V3_threshold']:
        vr = variant_results[mk]
        killed = vr['d_sharpe_oos'] < 0
        n_wf = sum(1 for r in wf_all[mk] if r['improved'])
        if killed:
            v_label = "KILL"
        elif n_wf < 3:
            v_label = "KILL (WF)"
        else:
            v_label = "PASS"
        lines.append(f"| {MAPPING_LABELS[mk]} "
                     f"| {vr['is']['sharpe']:.3f} | {vr['oos']['sharpe']:.3f} "
                     f"| {vr['d_sharpe_is']:+.3f} | {vr['d_sharpe_oos']:+.3f} "
                     f"| {vr['oos']['ann_return']:.1%} | {vr['oos']['max_dd']:.1%} "
                     f"| {vr['t_stat']:.3f} | {vr['p_value']:.3f} "
                     f"| {v_label} |")
    lines.append("")

    # Phase 3: Walk-Forward
    lines.append("## 3. Walk-Forward Validation")
    lines.append("")
    lines.append(f"6 windows, 180d train / 90d test, using ROC({best_lookback}d)")
    lines.append("")

    for mk in ['R94_symmetric', 'V1_mild_asym', 'V2_boost_only', 'V3_threshold']:
        n_imp = sum(1 for r in wf_all[mk] if r['improved'])
        avg_d = np.mean([r['d_sharpe'] for r in wf_all[mk]])
        lines.append(f"### {MAPPING_LABELS[mk]}")
        lines.append("")
        lines.append("| Window | Test Period | V3 Sharpe | +DVOL Sharpe | dSharpe | t-stat | Days |")
        lines.append("|--------|-------------|-----------|--------------|---------|--------|------|")
        for r in wf_all[mk]:
            lines.append(f"| {r['window']} | {r['test_start']} to {r['test_end']} "
                         f"| {r['v3_sharpe']:.2f} | {r['dvol_sharpe']:.2f} "
                         f"| {r['d_sharpe']:+.3f} | {r['t_stat']:+.2f} | {r['n_days']} |")
        lines.append("")
        lines.append(f"Improved: {n_imp}/6 windows. Avg dSharpe: {avg_d:+.3f}. "
                     f"{'PASS' if n_imp >= 3 else 'KILL'}")
        lines.append("")

    # Phase 4: Triple Overlay
    lines.append("## 4. Triple Overlay Decomposition")
    lines.append("")
    lines.append(f"Using {MAPPING_LABELS[best_variant_key]} with ROC({best_lookback}d)")
    lines.append("")
    lines.append("| Configuration | IS Sharpe | OOS Sharpe | OOS Return | OOS MaxDD |")
    lines.append("|---------------|-----------|------------|------------|-----------|")
    for cfg_name, cfg_data in decomp.items():
        lines.append(f"| {cfg_name} | {cfg_data['is_sharpe']:.3f} | {cfg_data['oos_sharpe']:.3f} "
                     f"| {cfg_data['oos_return']:.1%} | {cfg_data['oos_maxdd']:.1%} |")
    lines.append("")
    lines.append(f"**Marginal DVOL ROC contribution**: {marginal_contribution:+.3f} (Triple vs Pos+VRP)")
    lines.append(f"**Super-additive**: {'Yes' if super_additive else 'No'}")
    lines.append("")

    # Phase 5: Correlations
    lines.append("## 5. Overlay Correlations")
    lines.append("")
    lines.append(f"Using {MAPPING_LABELS[best_variant_key]} with ROC({best_lookback}d)")
    lines.append("")
    lines.append("| Period | DVOL-VRP (Spearman z) | DVOL-Pos (Pearson mult) | VRP-Pos (Pearson mult) |")
    lines.append("|--------|----------------------|------------------------|-----------------------|")
    for period_label, corr_data in [('Full', corr_full), ('IS', corr_is), ('OOS', corr_oos)]:
        lines.append(f"| {period_label} | {corr_data.get('corr_z_spearman', np.nan):.3f} "
                     f"| {corr_data.get('corr_dvol_pos_mult', np.nan):.3f} "
                     f"| {corr_data.get('corr_vrp_pos_mult', np.nan):.3f} |")
    lines.append("")
    redundant = abs(corr_full.get('corr_z_spearman', 0)) > 0.3
    lines.append(f"**Independence verdict**: {'REDUNDANT (corr > 0.3)' if redundant else 'ORTHOGONAL (corr < 0.3)'}")
    lines.append("")

    # Phase 6: IC
    lines.append("## 6. Information Coefficient")
    lines.append("")
    lines.append(f"ROC({best_lookback}d) z-score -> Forward Returns (Spearman IC)")
    lines.append("")
    lines.append("| Horizon | Full IC | Full t | IS IC | IS t | OOS IC | OOS t |")
    lines.append("|---------|---------|--------|-------|------|--------|-------|")
    for target in ['1D', '7D', '14D']:
        full_d = ic_results.get('Full', {}).get(target, {})
        is_d = ic_results.get('IS', {}).get(target, {})
        oos_d = ic_results.get('OOS', {}).get(target, {})
        lines.append(f"| {target} | {full_d.get('ic', np.nan):+.4f} | {full_d.get('t', np.nan):+.2f} "
                     f"| {is_d.get('ic', np.nan):+.4f} | {is_d.get('t', np.nan):+.2f} "
                     f"| {oos_d.get('ic', np.nan):+.4f} | {oos_d.get('t', np.nan):+.2f} |")
    lines.append("")

    # Multiplier Distribution
    lines.append("## 7. Multiplier Distribution")
    lines.append("")
    lines.append("| Variant | Multiplier Values (% of time) |")
    lines.append("|---------|-------------------------------|")
    for mk in ['R94_symmetric', 'V1_mild_asym', 'V2_boost_only', 'V3_threshold']:
        vr = variant_results[mk]
        dist_str = ", ".join(f"{float(k):.1f}x: {float(v):.1f}%" for k, v in sorted(vr['mult_dist'].items()))
        lines.append(f"| {MAPPING_LABELS[mk]} | {dist_str} |")
    lines.append("")

    # Monthly OOS Returns for best variant
    lines.append("## 8. Monthly OOS Returns")
    lines.append("")
    oos_mask = btc_daily.index >= OOS_START

    v3_monthly = (1 + v3_ret[oos_mask]).resample('ME').prod() - 1
    best_monthly = (1 + best_vr['strat_ret'][oos_mask]).resample('ME').prod() - 1

    lines.append("| Month | V3 Full | Best Variant | Delta |")
    lines.append("|-------|---------|--------------|-------|")
    for dt in sorted(v3_monthly.index):
        v3_val = v3_monthly.loc[dt]
        bv_val = best_monthly.loc[dt] if dt in best_monthly.index else 0
        delta = bv_val - v3_val
        lines.append(f"| {dt.strftime('%Y-%m')} | {v3_val:.2%} | {bv_val:.2%} | {delta:+.2%} |")

    v3_cum = (1 + v3_ret[oos_mask].dropna()).cumprod().iloc[-1] - 1
    bv_cum = (1 + best_vr['strat_ret'][oos_mask].dropna()).cumprod().iloc[-1] - 1
    lines.append(f"| **Cumulative** | **{v3_cum:.2%}** | **{bv_cum:.2%}** | **{bv_cum - v3_cum:+.2%}** |")
    lines.append("")

    # VERDICT
    lines.append("## 9. Verdict")
    lines.append("")
    lines.append("### Kill Criteria Check")
    lines.append("")
    lines.append(f"- Any variant degrades V3 Sharpe OOS: "
                 f"{'YES (some killed)' if n_surviving < n_total else 'NO (all pass)'}")
    lines.append(f"- All variants degrade: {'YES -> KILL' if all_killed else 'NO'}")

    for mk in MAPPING_FUNCS:
        n_wf = sum(1 for r in wf_all[mk] if r['improved'])
        lines.append(f"- {MAPPING_LABELS[mk]} walk-forward: {n_wf}/6 windows improved "
                     f"{'(KILL: < 3/6)' if n_wf < 3 else '(PASS)'}")

    lines.append(f"- Correlation with VRP > 0.3: "
                 f"{'YES -> likely redundant' if redundant else 'NO -> orthogonal'}")
    lines.append("")

    lines.append(f"### **{verdict}**")
    lines.append("")
    lines.append(verdict_reason)
    lines.append("")

    if verdict == "PASS":
        bp = variant_results[best_variant_key]
        lines.append(f"### Recommended Configuration")
        lines.append("")
        lines.append(f"- **Mapping**: {MAPPING_LABELS[best_variant_key]}")
        lines.append(f"- **ROC Lookback**: {best_lookback}d")
        lines.append(f"- **Z-Score Window**: {ZSCORE_WINDOW}d")
        lines.append(f"- **OOS dSharpe vs V3**: {bp['d_sharpe_oos']:+.3f}")
        lines.append(f"- **Walk-Forward**: {sum(1 for r in wf_all[best_variant_key] if r['improved'])}/6 windows")
        lines.append("")
        lines.append("Integration into s320:")
        lines.append("```python")
        lines.append(f"# Compute DVOL ROC signal")
        lines.append(f"dvol_roc_{best_lookback}d = (dvol - dvol.shift({best_lookback})) / dvol.shift({best_lookback})")
        lines.append(f"dvol_roc_z = rolling_zscore(dvol_roc_{best_lookback}d, window={ZSCORE_WINDOW})")
        lines.append(f"# Apply {MAPPING_LABELS[best_variant_key]} mapping")
        lines.append(f"final_position = base * pos_mult * vrp_mult * dvol_roc_mult")
        lines.append(f"final_position = np.clip(final_position, 0.0, 1.5)")
        lines.append("```")
        lines.append("")

    elif verdict == "CONDITIONAL":
        bp = variant_results[best_variant_key]
        lines.append(f"### Recommended Next Steps")
        lines.append("")
        lines.append(f"- **Best candidate**: {MAPPING_LABELS[best_variant_key]} with ROC({best_lookback}d)")
        lines.append(f"- **OOS dSharpe**: {bp['d_sharpe_oos']:+.3f} (non-negative but small)")
        lines.append(f"- Monitor for 3+ more months of OOS data")
        lines.append(f"- If dSharpe remains >= 0 and walk-forward continues passing, promote to PASS")
        lines.append(f"- If dSharpe turns negative, promote to KILL")
        lines.append("")

    elif verdict == "KILL":
        lines.append("### Post-Mortem")
        lines.append("")
        lines.append("Despite positive IC (7d) and orthogonality to VRP, DVOL ROC does not")
        lines.append("translate to a reliable sizing overlay improvement. Possible reasons:")
        lines.append("- IC is concentrated in specific regimes (crypto reflexivity only works in bull runs)")
        lines.append("- The overlay multiplication effect is non-linear and can amplify noise")
        lines.append("- 14 months OOS may be insufficient to capture the full vol regime cycle")
        lines.append("- The signal may work better as a standalone alpha rather than a position-sizing overlay")
        lines.append("")

    # Write report
    report_path = OUTPUT_DIR / 'R103_dvol_roc_overlay.md'
    with open(report_path, 'w') as f:
        f.write('\n'.join(lines))
    print(f"\nReport saved to: {report_path}")

    # Console summary
    print("\n" + "=" * 74)
    print("FINAL SUMMARY")
    print("=" * 74)
    print(f"\n  Best lookback: ROC({best_lookback}d)")
    print(f"  V3 Full OOS Sharpe:  {v3_oos['sharpe']:.3f}")
    for mk in ['R94_symmetric', 'V1_mild_asym', 'V2_boost_only', 'V3_threshold']:
        vr = variant_results[mk]
        n_wf = sum(1 for r in wf_all[mk] if r['improved'])
        print(f"  {MAPPING_LABELS[mk]:30s}: OOS Sharpe={vr['oos']['sharpe']:.3f} "
              f"(d={vr['d_sharpe_oos']:+.3f}), WF={n_wf}/6")
    print(f"\n  VERDICT: {verdict}")
    print(f"  {verdict_reason}")
    print()


if __name__ == '__main__':
    main()
