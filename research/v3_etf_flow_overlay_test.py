#!/workspace/venv/bin/python
"""
V3 ETF Flow Sizing Overlay Test
=================================

Tests ETF flow data as an ADDITIONAL sizing overlay on the validated V3 BTC
momentum strategy (20/50 EMA + Positioning + VRP overlays).

Context from prior research:
  - R40: BTC ETF flow 20d z-score IC=+0.191 at 14d (t=2.99)
  - R40: ETF inflow during TIGHTENING regime -> +6.89% 14d (t=7.26, 70% WR)
  - V3 OOS: Sharpe 0.56, Return +17.52%, MaxDD -20.2%
  - Positioning and VRP overlays are INDEPENDENT (Spearman rho=0.031)

IMPORTANT CONSTRAINTS:
  - ETF data only starts Jan 2024 (~26 months total)
  - Too short for standard walk-forward (18mo IS windows)
  - Using expanding-window validation instead (6mo IS, 3mo OOS, expand)
  - Statistical significance will be weak. All conclusions flagged honestly.

Variants tested:
  V1: Simple Flow Z-Score Sizing (20d z-score thresholds)
  V2: Flow Momentum (5-day sum direction)
  V3: Flow Acceleration (z-score delta)

Kill criteria:
  - dSharpe < 0 on OOS -> KILL
  - Correlation with existing overlays > 0.3 -> KILL (redundant)
  - < 20 sizing changes -> KILL (signal too sparse)
  - Data too short -> FLAG with honest confidence interval
"""

import os
import json
import warnings
import numpy as np
import pandas as pd
from datetime import datetime
from pathlib import Path
from scipy import stats

warnings.filterwarnings('ignore')

# ── Paths ─────────────────────────────────────────────────────────────────
BASE_DIR = Path('/workspace/crypto_backtest')
DATA_DIR = BASE_DIR / 'data'
OUTPUT_DIR = BASE_DIR / 'research'

ETF_FLOW_PATH = DATA_DIR / 'alternative' / 'etf_flows' / 'btc_etf_daily.parquet'
BTC_SPOT_PATH = DATA_DIR / 'spot' / '1h_cache' / 'BTC_1h.parquet'
POS_PATH = DATA_DIR / 'alternative' / 'binance_metrics' / 'all_symbols_daily_ls.parquet'
DVOL_PATH = DATA_DIR / 'alternative' / 'deribit_options' / 'dvol' / 'btc_dvol_daily.json'

RESULTS_PATH = OUTPUT_DIR / 'v3_etf_flow_overlay_results.md'
SCRIPT_PATH = OUTPUT_DIR / 'v3_etf_flow_overlay_test.py'

COST_BPS = 10   # round-trip cost in basis points
ANNUALIZE = np.sqrt(365)
SEP = '=' * 80
THIN = '-' * 80


# ══════════════════════════════════════════════════════════════════════════════
# 1. DATA LOADING
# ══════════════════════════════════════════════════════════════════════════════

def load_btc_daily():
    """Load BTC spot 1H data and resample to daily."""
    print("[1/4] Loading BTC spot data...")
    btc = pd.read_parquet(BTC_SPOT_PATH)
    btc.index = pd.to_datetime(btc.index)
    btc.index.name = 'date'
    daily = btc['close'].resample('D').last().dropna().to_frame('close')
    daily['log_ret'] = np.log(daily['close'] / daily['close'].shift(1))
    print(f"  BTC daily: {daily.index.min().date()} to {daily.index.max().date()}, {len(daily)} rows")
    return daily


def load_etf_flows():
    """Load BTC ETF daily net flow data."""
    print("[2/4] Loading ETF flow data...")
    df = pd.read_parquet(ETF_FLOW_PATH)
    df['date'] = pd.to_datetime(df['date']).dt.normalize()
    df = df.sort_values('date').drop_duplicates(subset='date', keep='last')
    df = df.set_index('date')
    print(f"  ETF flows: {df.index.min().date()} to {df.index.max().date()}, {len(df)} rows")
    print(f"  Mean daily flow: ${df['total_inflow_mm'].mean():.1f}M, Std: ${df['total_inflow_mm'].std():.1f}M")
    return df


def load_positioning():
    """Load Binance positioning data for BTCUSDT."""
    print("[3/4] Loading positioning data...")
    pos = pd.read_parquet(POS_PATH)
    pos = pos[pos['symbol'] == 'BTCUSDT'].copy()
    pos['date'] = pd.to_datetime(pos['date'])
    pos = pos.set_index('date').sort_index()
    pos = pos[['sum_toptrader_ls_ratio', 'count_toptrader_ls_ratio', 'count_ls_ratio']].copy()
    pos = pos[~pos.index.duplicated(keep='last')]
    print(f"  Positioning: {pos.index.min().date()} to {pos.index.max().date()}, {len(pos)} rows")
    return pos


def load_dvol():
    """Load BTC DVOL from Deribit JSON."""
    print("[4/4] Loading BTC DVOL...")
    if not DVOL_PATH.exists():
        print("  WARNING: DVOL not found, using RV proxy.")
        return pd.Series(dtype=float)
    with open(DVOL_PATH) as f:
        data = json.load(f)
    records = [{'date': pd.Timestamp(row[0], unit='ms'), 'dvol_close': row[4]} for row in data]
    dvol = pd.DataFrame(records).set_index('date').sort_index()
    dvol = dvol[~dvol.index.duplicated(keep='last')]
    print(f"  DVOL: {dvol.index.min().date()} to {dvol.index.max().date()}, {len(dvol)} rows")
    return dvol['dvol_close']


# ══════════════════════════════════════════════════════════════════════════════
# 2. V3 BASE STRATEGY CONSTRUCTION
# ══════════════════════════════════════════════════════════════════════════════

def build_ema_base(btc_daily, fast=20, slow=50):
    """Build EMA crossover base signal: 1.0 when EMA_fast > EMA_slow, else 0.0."""
    ema_fast = btc_daily['close'].ewm(span=fast, adjust=False).mean()
    ema_slow = btc_daily['close'].ewm(span=slow, adjust=False).mean()
    base = (ema_fast > ema_slow).astype(float)
    print(f"  EMA base: {base.sum():.0f}/{len(base)} days long ({100*base.mean():.1f}%)")
    return base, ema_fast, ema_slow


def build_positioning_overlay(btc_daily, positioning):
    """
    Positioning overlay (from V3 spec):
    Top Trader L/S z-score (30d) + L/S Divergence z-score (30d) -> combined -> multiplier.
    """
    pos = positioning.reindex(btc_daily.index).ffill()

    def rolling_zscore(s, window=30):
        mu = s.rolling(window, min_periods=15).mean()
        sigma = s.rolling(window, min_periods=15).std()
        return (s - mu) / sigma.replace(0, np.nan)

    z_toptrader = rolling_zscore(pos['sum_toptrader_ls_ratio'])
    divergence = pos['count_toptrader_ls_ratio'] - pos['count_ls_ratio']
    z_divergence = rolling_zscore(divergence)
    combined_z = (z_toptrader + z_divergence) / 2.0

    def z_to_mult(z):
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

    pos_mult = combined_z.apply(z_to_mult)
    return pos_mult, combined_z


def build_vrp_overlay(btc_daily, dvol_series):
    """
    VRP overlay (from V3 spec):
    VRP = IV - RV(20d). Z-score over 60d -> multiplier.
    """
    rv_20d = btc_daily['log_ret'].rolling(20, min_periods=15).std() * np.sqrt(365) * 100

    if dvol_series.empty or len(dvol_series) < 30:
        iv = btc_daily['log_ret'].rolling(90, min_periods=60).std() * np.sqrt(365) * 100 * 1.2
    else:
        iv = dvol_series.reindex(btc_daily.index).ffill()

    vrp = iv - rv_20d
    vrp_mu = vrp.rolling(60, min_periods=30).mean()
    vrp_sigma = vrp.rolling(60, min_periods=30).std()
    vrp_z = (vrp - vrp_mu) / vrp_sigma.replace(0, np.nan)

    def vrp_z_to_mult(z):
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

    vrp_mult = vrp_z.apply(vrp_z_to_mult)
    return vrp_mult, vrp_z


# ══════════════════════════════════════════════════════════════════════════════
# 3. ETF FLOW OVERLAY VARIANTS
# ══════════════════════════════════════════════════════════════════════════════

def compute_etf_flow_signals(etf_flows):
    """
    Compute all ETF flow signals from daily net flows.
    All signals use T-1 data (lagged by 1 day) to avoid look-ahead bias.
    ETF flow data is published end-of-day, so we can only use it next day.
    """
    flow = etf_flows['total_inflow_mm'].copy()

    # Continuous daily index (fill non-trading days with 0)
    full_idx = pd.date_range(flow.index.min(), flow.index.max(), freq='D')
    flow = flow.reindex(full_idx).fillna(0)

    # Lag by 1 day (T-1)
    flow_lagged = flow.shift(1)

    signals = pd.DataFrame(index=full_idx)

    # Rolling 20d z-score of daily net flow (for V1)
    flow_20d_mean = flow_lagged.rolling(20, min_periods=10).mean()
    flow_20d_std = flow_lagged.rolling(20, min_periods=10).std()
    signals['flow_z_20d'] = (flow_lagged - flow_20d_mean) / flow_20d_std.clip(lower=1e-6)

    # 5-day sum (for V2)
    signals['flow_5d_sum'] = flow_lagged.rolling(5, min_periods=3).sum()

    # Z-score delta: today vs 5 days ago (for V3)
    signals['flow_z_delta_5d'] = signals['flow_z_20d'] - signals['flow_z_20d'].shift(5)

    # Raw lagged flow for analysis
    signals['flow_daily_lagged'] = flow_lagged

    return signals


def apply_etf_overlay_v1(flow_z):
    """V1: Simple Flow Z-Score Sizing."""
    def z_to_mult(z):
        if pd.isna(z):
            return 1.0
        if z > 1.0:
            return 1.3    # strong inflows
        elif z > -0.5:
            return 1.0    # neutral
        elif z > -1.5:
            return 0.5    # outflows
        else:
            return 0.3    # extreme outflows
    return flow_z.apply(z_to_mult)


def apply_etf_overlay_v2(flow_5d_sum):
    """V2: Flow Momentum (Direction)."""
    def sum_to_mult(s):
        if pd.isna(s):
            return 1.0
        if s > 0:
            return 1.2    # recent net inflows
        else:
            return 0.7    # recent net outflows
    return flow_5d_sum.apply(sum_to_mult)


def apply_etf_overlay_v3(flow_z_delta):
    """V3: Flow Acceleration."""
    def delta_to_mult(d):
        if pd.isna(d):
            return 1.0
        if d > 0.5:
            return 1.3    # accelerating inflows
        elif d < -0.5:
            return 0.5    # decelerating
        else:
            return 1.0    # stable
    return flow_z_delta.apply(delta_to_mult)


# ══════════════════════════════════════════════════════════════════════════════
# 4. BACKTEST ENGINE
# ══════════════════════════════════════════════════════════════════════════════

def compute_v3_final_position(base, pos_mult, vrp_mult, etf_mult=None):
    """Compute final V3 position with optional ETF overlay."""
    final = base * pos_mult * vrp_mult
    if etf_mult is not None:
        final = final * etf_mult
    return final.clip(0, 1.5)


def run_backtest(btc_daily, final_position, cost_bps=COST_BPS):
    """Run backtest with weekly rebalancing and transaction costs."""
    daily_ret = btc_daily['close'].pct_change()

    # Weekly rebalance (every 7 days, using Mondays)
    rebalance_dates = btc_daily.index.to_series().groupby(
        btc_daily.index.to_period('W')
    ).first()
    rebalance_set = set(rebalance_dates.values)

    held_position = pd.Series(0.0, index=btc_daily.index)
    current_pos = 0.0
    costs = pd.Series(0.0, index=btc_daily.index)
    n_rebalances = 0

    for dt in btc_daily.index:
        if dt in rebalance_set:
            new_pos = final_position.loc[dt] if dt in final_position.index else np.nan
            if not pd.isna(new_pos):
                pos_change = abs(new_pos - current_pos)
                costs.loc[dt] = pos_change * cost_bps / 10000.0
                if pos_change > 0.001:
                    n_rebalances += 1
                current_pos = new_pos
        held_position.loc[dt] = current_pos

    strat_ret = held_position.shift(1) * daily_ret - costs
    return strat_ret, held_position, costs, n_rebalances


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


# ══════════════════════════════════════════════════════════════════════════════
# 5. EXPANDING-WINDOW VALIDATION
# ══════════════════════════════════════════════════════════════════════════════

def expanding_window_validation(btc_daily, v3_final, etf_mult_series, variant_name,
                                 initial_is_months=6, oos_months=3):
    """
    Expanding-window validation:
    Start with initial_is_months IS, test next oos_months OOS.
    Expand IS by oos_months each round.

    ETF data starts ~Jan 2024. We need ~20d warmup for z-scores.
    First usable date: ~Feb 2024.
    """
    # Determine date range from ETF data availability
    etf_valid = etf_mult_series.dropna()
    if len(etf_valid) == 0:
        return []

    data_start = etf_valid.index.min()
    data_end = etf_valid.index.max()

    # Compute V3+ETF final position
    v3_with_etf = (v3_final * etf_mult_series).clip(0, 1.5)

    # Run full backtest once
    strat_v3, _, _, _ = run_backtest(btc_daily, v3_final)
    strat_etf, _, _, _ = run_backtest(btc_daily, v3_with_etf)

    windows = []
    is_end_date = data_start + pd.DateOffset(months=initial_is_months)

    window_num = 0
    while is_end_date < data_end:
        oos_end_date = min(is_end_date + pd.DateOffset(months=oos_months), data_end)

        # OOS returns for this window
        oos_mask = (btc_daily.index > is_end_date) & (btc_daily.index <= oos_end_date)

        v3_oos_ret = strat_v3[oos_mask].dropna()
        etf_oos_ret = strat_etf[oos_mask].dropna()

        if len(v3_oos_ret) < 30:
            is_end_date = oos_end_date
            window_num += 1
            continue

        v3_metrics = compute_metrics(v3_oos_ret, f"V3 Window {window_num}")
        etf_metrics = compute_metrics(etf_oos_ret, f"{variant_name} Window {window_num}")

        d_sharpe = etf_metrics['sharpe'] - v3_metrics['sharpe']
        d_return = etf_metrics['total_return'] - v3_metrics['total_return']

        windows.append({
            'window': window_num,
            'is_start': data_start.date(),
            'is_end': is_end_date.date(),
            'oos_start': is_end_date.date(),
            'oos_end': oos_end_date.date(),
            'oos_days': len(v3_oos_ret),
            'v3_return': v3_metrics['total_return'],
            'v3_sharpe': v3_metrics['sharpe'],
            'etf_return': etf_metrics['total_return'],
            'etf_sharpe': etf_metrics['sharpe'],
            'd_sharpe': d_sharpe,
            'd_return': d_return,
        })

        # Expand IS window
        is_end_date = oos_end_date
        window_num += 1

    return windows


# ══════════════════════════════════════════════════════════════════════════════
# 6. SIGNAL CORRELATION ANALYSIS
# ══════════════════════════════════════════════════════════════════════════════

def correlation_analysis(etf_mult, pos_mult, vrp_mult, pos_z, vrp_z, flow_z, common_idx):
    """Analyze correlation between ETF flow overlay and existing overlays."""
    results = {}

    # Align all to common index
    df = pd.DataFrame({
        'etf_mult': etf_mult,
        'pos_mult': pos_mult,
        'vrp_mult': vrp_mult,
        'pos_z': pos_z,
        'vrp_z': vrp_z,
        'flow_z': flow_z,
    }).reindex(common_idx).dropna()

    if len(df) < 30:
        print("  Insufficient overlapping data for correlation analysis")
        return results

    # Multiplier correlations
    corr_etf_pos = df['etf_mult'].corr(df['pos_mult'])
    corr_etf_vrp = df['etf_mult'].corr(df['vrp_mult'])

    # Z-score Spearman correlations (more robust)
    sp_etf_pos, sp_etf_pos_p = stats.spearmanr(df['flow_z'], df['pos_z'])
    sp_etf_vrp, sp_etf_vrp_p = stats.spearmanr(df['flow_z'], df['vrp_z'])

    results = {
        'corr_etf_pos_mult': corr_etf_pos,
        'corr_etf_vrp_mult': corr_etf_vrp,
        'spearman_etf_pos_z': sp_etf_pos,
        'spearman_etf_pos_p': sp_etf_pos_p,
        'spearman_etf_vrp_z': sp_etf_vrp,
        'spearman_etf_vrp_p': sp_etf_vrp_p,
        'n_obs': len(df),
    }

    return results


# ══════════════════════════════════════════════════════════════════════════════
# 7. MAIN EXECUTION
# ══════════════════════════════════════════════════════════════════════════════

def main():
    print(SEP)
    print("V3 ETF FLOW SIZING OVERLAY TEST")
    print("Testing ETF flow data as additional sizing overlay on V3 momentum strategy")
    print(SEP)
    print()

    # ── Load data ──────────────────────────────────────────────────────────
    btc_daily = load_btc_daily()
    etf_flows = load_etf_flows()
    positioning = load_positioning()
    dvol = load_dvol()
    print()

    # ── Build V3 base (no ETF) ─────────────────────────────────────────────
    print(THIN)
    print("BUILDING V3 BASE STRATEGY (EMA 20/50 + Positioning + VRP)")
    print(THIN)

    base, ema_fast, ema_slow = build_ema_base(btc_daily)
    pos_mult, pos_z = build_positioning_overlay(btc_daily, positioning)
    vrp_mult, vrp_z = build_vrp_overlay(btc_daily, dvol)

    v3_final = compute_v3_final_position(base, pos_mult, vrp_mult)
    print(f"  V3 final position: mean={v3_final.mean():.3f}, non-zero={100*(v3_final>0).mean():.1f}%")
    print()

    # ── Compute ETF flow signals ───────────────────────────────────────────
    print(THIN)
    print("COMPUTING ETF FLOW SIGNALS")
    print(THIN)

    flow_signals = compute_etf_flow_signals(etf_flows)

    # Align to btc_daily index
    flow_signals = flow_signals.reindex(btc_daily.index).ffill()

    print(f"  flow_z_20d: {flow_signals['flow_z_20d'].dropna().shape[0]} non-NaN values")
    print(f"  flow_5d_sum: {flow_signals['flow_5d_sum'].dropna().shape[0]} non-NaN values")
    print(f"  flow_z_delta_5d: {flow_signals['flow_z_delta_5d'].dropna().shape[0]} non-NaN values")
    print()

    # ── Apply ETF overlay variants ─────────────────────────────────────────
    print(THIN)
    print("APPLYING ETF OVERLAY VARIANTS")
    print(THIN)

    etf_v1_mult = apply_etf_overlay_v1(flow_signals['flow_z_20d'])
    etf_v2_mult = apply_etf_overlay_v2(flow_signals['flow_5d_sum'])
    etf_v3_mult = apply_etf_overlay_v3(flow_signals['flow_z_delta_5d'])

    variants = {
        'v3_base': {
            'name': 'V3 Base (no ETF)',
            'etf_mult': None,
            'mult_series': None,
        },
        'v1_flow_z': {
            'name': 'V1: Flow Z-Score Sizing',
            'etf_mult': etf_v1_mult,
            'mult_series': etf_v1_mult,
        },
        'v2_flow_momentum': {
            'name': 'V2: Flow Momentum',
            'etf_mult': etf_v2_mult,
            'mult_series': etf_v2_mult,
        },
        'v3_flow_accel': {
            'name': 'V3: Flow Acceleration',
            'etf_mult': etf_v3_mult,
            'mult_series': etf_v3_mult,
        },
    }

    for vk, vinfo in variants.items():
        if vinfo['etf_mult'] is not None:
            # Count sizing adjustments (non-1.0 multiplier)
            non_neutral = (vinfo['etf_mult'] != 1.0).sum()
            unique_vals = vinfo['etf_mult'].value_counts()
            print(f"  {vinfo['name']}:")
            print(f"    Non-neutral days: {non_neutral} ({100*non_neutral/len(vinfo['etf_mult']):.1f}%)")
            for val, cnt in unique_vals.items():
                print(f"    {val}x: {cnt} days ({100*cnt/len(vinfo['etf_mult']):.1f}%)")

    print()

    # ── Determine test period (ETF data coverage) ──────────────────────────
    etf_data_start = etf_flows.index.min()
    etf_data_end = etf_flows.index.max()
    # Allow 20 days for z-score warmup
    test_start = etf_data_start + pd.Timedelta(days=25)
    test_end = btc_daily.index.max()

    test_mask = (btc_daily.index >= test_start) & (btc_daily.index <= test_end)
    n_test_days = test_mask.sum()
    n_test_months = n_test_days / 30.4

    print(THIN)
    print(f"TEST PERIOD: {test_start.date()} to {test_end.date()} ({n_test_days} days, ~{n_test_months:.0f} months)")
    print(THIN)
    print()

    # ── Run full-period backtests ──────────────────────────────────────────
    print(THIN)
    print("FULL-PERIOD BACKTEST RESULTS (ETF data coverage only)")
    print(THIN)

    full_results = {}
    for vk, vinfo in variants.items():
        final_pos = compute_v3_final_position(base, pos_mult, vrp_mult, vinfo['etf_mult'])
        strat_ret, held_pos, costs, n_rebal = run_backtest(btc_daily, final_pos)

        test_ret = strat_ret[test_mask]
        metrics = compute_metrics(test_ret, vinfo['name'])

        full_results[vk] = {
            'name': vinfo['name'],
            'metrics': metrics,
            'strat_ret': strat_ret,
            'held_pos': held_pos,
            'final_pos': final_pos,
            'n_rebalances': n_rebal,
        }

        print(f"  {vinfo['name']:35s}: Return={metrics['total_return']:+7.2%}  "
              f"Sharpe={metrics['sharpe']:+6.2f}  MaxDD={metrics['max_dd']:+7.2%}  "
              f"Calmar={metrics['calmar']:+5.2f}")

    print()

    # ── Compute dSharpe vs V3 base ─────────────────────────────────────────
    print(THIN)
    print("dSHARPE vs V3 BASE")
    print(THIN)

    base_sharpe = full_results['v3_base']['metrics']['sharpe']
    base_return = full_results['v3_base']['metrics']['total_return']
    base_dd = full_results['v3_base']['metrics']['max_dd']

    for vk in ['v1_flow_z', 'v2_flow_momentum', 'v3_flow_accel']:
        vr = full_results[vk]
        d_s = vr['metrics']['sharpe'] - base_sharpe
        d_r = vr['metrics']['total_return'] - base_return
        d_dd = vr['metrics']['max_dd'] - base_dd
        print(f"  {vr['name']:35s}: dSharpe={d_s:+.3f}  dReturn={d_r:+.2%}  dMaxDD={d_dd:+.2%}")

        # Statistical significance of return difference
        base_ret_test = full_results['v3_base']['strat_ret'][test_mask].dropna()
        overlay_ret_test = vr['strat_ret'][test_mask].dropna()
        common = base_ret_test.index.intersection(overlay_ret_test.index)
        diff = overlay_ret_test.loc[common] - base_ret_test.loc[common]
        if len(diff) > 30:
            t_stat = diff.mean() / (diff.std() / np.sqrt(len(diff)))
            p_val = 2 * (1 - stats.t.cdf(abs(t_stat), len(diff) - 1))
            print(f"    t-stat={t_stat:+.3f}, p={p_val:.4f}, N={len(diff)}")
        else:
            print(f"    Insufficient data for significance test")

    print()

    # ── Sizing change count (Kill criterion: < 20 = KILL) ─────────────────
    print(THIN)
    print("SIZING CHANGE COUNT (Kill if < 20)")
    print(THIN)

    for vk in ['v1_flow_z', 'v2_flow_momentum', 'v3_flow_accel']:
        vinfo = variants[vk]
        mult = vinfo['etf_mult']

        # Count actual sizing changes (when multiplier changes value)
        changes = mult.diff().abs()
        n_changes = (changes > 0.001).sum()

        # Count non-neutral days within test period
        mult_test = mult[test_mask]
        n_non_neutral = (mult_test != 1.0).sum()

        kill = "KILL" if n_changes < 20 else "PASS"
        print(f"  {vinfo['name']:35s}: {n_changes:4d} changes, {n_non_neutral:4d} non-neutral days [{kill}]")

    print()

    # ── Correlation with existing overlays ─────────────────────────────────
    print(THIN)
    print("CORRELATION WITH EXISTING OVERLAYS (Kill if > 0.3)")
    print(THIN)

    common_idx = btc_daily.index[test_mask]

    for vk, flow_z_col in [('v1_flow_z', 'flow_z_20d'),
                             ('v2_flow_momentum', 'flow_5d_sum'),
                             ('v3_flow_accel', 'flow_z_delta_5d')]:
        vname = variants[vk]['name']
        etf_m = variants[vk]['etf_mult']

        corr = correlation_analysis(
            etf_m, pos_mult, vrp_mult, pos_z, vrp_z,
            flow_signals[flow_z_col], common_idx
        )

        if corr:
            sp_pos = corr['spearman_etf_pos_z']
            sp_vrp = corr['spearman_etf_vrp_z']
            kill_pos = "KILL" if abs(sp_pos) > 0.3 else "PASS"
            kill_vrp = "KILL" if abs(sp_vrp) > 0.3 else "PASS"
            print(f"  {vname}:")
            print(f"    vs Positioning z: Spearman={sp_pos:+.3f} (p={corr['spearman_etf_pos_p']:.4f}) [{kill_pos}]")
            print(f"    vs VRP z:         Spearman={sp_vrp:+.3f} (p={corr['spearman_etf_vrp_p']:.4f}) [{kill_vrp}]")
            print(f"    Multiplier corr with pos_mult: {corr['corr_etf_pos_mult']:.3f}")
            print(f"    Multiplier corr with vrp_mult: {corr['corr_etf_vrp_mult']:.3f}")
            print(f"    N={corr['n_obs']}")

            # Store for report
            full_results[vk]['correlation'] = corr

    print()

    # ── Expanding-Window Validation ────────────────────────────────────────
    print(SEP)
    print("EXPANDING-WINDOW VALIDATION (6mo IS start, 3mo OOS steps)")
    print(SEP)
    print()

    for vk in ['v1_flow_z', 'v2_flow_momentum', 'v3_flow_accel']:
        vinfo = variants[vk]
        vname = vinfo['name']

        windows = expanding_window_validation(
            btc_daily, v3_final, vinfo['etf_mult'], vname,
            initial_is_months=6, oos_months=3
        )

        full_results[vk]['windows'] = windows

        if not windows:
            print(f"  {vname}: No valid windows (insufficient data)")
            continue

        print(f"  {vname}:")
        print(f"  {'Window':>6s} {'IS End':>12s} {'OOS End':>12s} {'OOS Days':>9s} "
              f"{'V3 Ret':>8s} {'ETF Ret':>8s} {'V3 Sharpe':>10s} {'ETF Sharpe':>11s} {'dSharpe':>8s}")

        total_d_sharpe = 0
        n_positive = 0
        for w in windows:
            d = w['d_sharpe']
            total_d_sharpe += d
            if d > 0:
                n_positive += 1
            print(f"  {w['window']:>6d} {str(w['is_end']):>12s} {str(w['oos_end']):>12s} "
                  f"{w['oos_days']:>9d} {w['v3_return']:>+8.2%} {w['etf_return']:>+8.2%} "
                  f"{w['v3_sharpe']:>+10.2f} {w['etf_sharpe']:>+11.2f} {w['d_sharpe']:>+8.3f}")

        n_windows = len(windows)
        avg_d_sharpe = total_d_sharpe / n_windows if n_windows > 0 else 0
        print(f"\n  Summary: {n_positive}/{n_windows} windows positive dSharpe, "
              f"avg dSharpe={avg_d_sharpe:+.3f}")
        print()

    # ── Monthly Returns Comparison ─────────────────────────────────────────
    print(THIN)
    print("MONTHLY RETURNS (test period)")
    print(THIN)

    monthly_all = {}
    for vk in full_results:
        vr = full_results[vk]['strat_ret'][test_mask].dropna()
        monthly_all[vk] = (1 + vr).resample('ME').prod() - 1

    print(f"{'Month':>8s} {'V3 Base':>10s} {'V1 FlowZ':>10s} {'V2 FlowMom':>11s} {'V3 FlowAcc':>11s}")
    all_months = sorted(set().union(*[set(m.index) for m in monthly_all.values()]))
    for dt in all_months:
        parts = [f"{dt.strftime('%Y-%m'):>8s}"]
        for vk in ['v3_base', 'v1_flow_z', 'v2_flow_momentum', 'v3_flow_accel']:
            if dt in monthly_all[vk].index:
                parts.append(f"{monthly_all[vk].loc[dt]:>+10.2%}")
            else:
                parts.append(f"{'N/A':>10s}")
        print("  ".join(parts))

    print()

    # ══════════════════════════════════════════════════════════════════════════
    # KILL CRITERIA EVALUATION
    # ══════════════════════════════════════════════════════════════════════════

    print(SEP)
    print("KILL CRITERIA EVALUATION")
    print(SEP)
    print()

    kill_results = {}
    for vk in ['v1_flow_z', 'v2_flow_momentum', 'v3_flow_accel']:
        vname = variants[vk]['name']
        kills = []
        flags = []

        # 1. dSharpe < 0 -> KILL
        d_s = full_results[vk]['metrics']['sharpe'] - base_sharpe
        if d_s < 0:
            kills.append(f"dSharpe={d_s:+.3f} < 0")
        else:
            flags.append(f"dSharpe={d_s:+.3f} >= 0 (PASS)")

        # 2. Correlation > 0.3 -> KILL
        corr = full_results[vk].get('correlation', {})
        sp_pos = abs(corr.get('spearman_etf_pos_z', 0))
        sp_vrp = abs(corr.get('spearman_etf_vrp_z', 0))
        if sp_pos > 0.3:
            kills.append(f"|corr_pos|={sp_pos:.3f} > 0.3 (redundant with positioning)")
        if sp_vrp > 0.3:
            kills.append(f"|corr_vrp|={sp_vrp:.3f} > 0.3 (redundant with VRP)")
        if sp_pos <= 0.3 and sp_vrp <= 0.3:
            flags.append(f"|corr_pos|={sp_pos:.3f}, |corr_vrp|={sp_vrp:.3f} (INDEPENDENT)")

        # 3. < 20 sizing changes -> KILL
        mult = variants[vk]['etf_mult']
        n_changes = (mult.diff().abs() > 0.001).sum()
        if n_changes < 20:
            kills.append(f"Only {n_changes} sizing changes < 20")
        else:
            flags.append(f"{n_changes} sizing changes (sufficient)")

        # 4. Data too short -> FLAG
        flags.append(f"WARNING: Only ~{n_test_months:.0f} months of test data. "
                     f"Statistical power is LOW. Confidence intervals are wide.")

        # Expanding-window check
        windows = full_results[vk].get('windows', [])
        if windows:
            n_win = len(windows)
            n_pos = sum(1 for w in windows if w['d_sharpe'] > 0)
            if n_pos < n_win / 2:
                kills.append(f"OOS expanding-window: only {n_pos}/{n_win} positive dSharpe")
            else:
                flags.append(f"OOS expanding-window: {n_pos}/{n_win} positive dSharpe")

        verdict = "KILL" if kills else "PASS (with caveats)"
        kill_results[vk] = {'kills': kills, 'flags': flags, 'verdict': verdict}

        print(f"  {vname}: [{verdict}]")
        for k in kills:
            print(f"    KILL: {k}")
        for f in flags:
            print(f"    FLAG: {f}")
        print()

    # ══════════════════════════════════════════════════════════════════════════
    # GENERATE RESULTS REPORT
    # ══════════════════════════════════════════════════════════════════════════

    print(SEP)
    print("GENERATING REPORT")
    print(SEP)

    lines = []
    lines.append("# V3 ETF Flow Sizing Overlay Test Results")
    lines.append("")
    lines.append(f"**Run date**: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"**Test period**: {test_start.date()} to {test_end.date()} (~{n_test_months:.0f} months)")
    lines.append(f"**ETF data**: {etf_data_start.date()} to {etf_data_end.date()} ({len(etf_flows)} trading days)")
    lines.append(f"**V3 base**: 20/50 EMA + Positioning + VRP overlays")
    lines.append(f"**Rebalancing**: Weekly")
    lines.append(f"**Transaction cost**: {COST_BPS} bps round-trip")
    lines.append("")

    # Critical warning
    lines.append("## CRITICAL: Statistical Power Warning")
    lines.append("")
    lines.append(f"This analysis covers only ~{n_test_months:.0f} months of data. "
                 f"BTC ETF trading began January 2024. With only ~{n_test_months:.0f} months:")
    lines.append(f"- Standard walk-forward validation (18mo IS windows) is NOT feasible")
    lines.append(f"- Expanding-window validation has very few windows")
    lines.append(f"- Any positive result should be treated with LOW confidence")
    lines.append(f"- A full market cycle (3+ years) is needed to draw reliable conclusions")
    lines.append(f"- Results may be dominated by a single regime (2024 bull + 2025 correction)")
    lines.append("")

    # Variant descriptions
    lines.append("## Overlay Variants Tested")
    lines.append("")
    lines.append("| Variant | Signal | Thresholds | Multiplier Range |")
    lines.append("|---------|--------|------------|------------------|")
    lines.append("| V1: Flow Z-Score | 20d rolling z-score of daily net ETF flow | z>1: 1.3x, -0.5<z<1: 1.0x, z<-0.5: 0.5x, z<-1.5: 0.3x | 0.3x - 1.3x |")
    lines.append("| V2: Flow Momentum | 5-day sum of daily net flow | sum>0: 1.2x, sum<0: 0.7x | 0.7x - 1.2x |")
    lines.append("| V3: Flow Acceleration | z-score delta (today vs 5d ago) | delta>0.5: 1.3x, delta<-0.5: 0.5x, else 1.0x | 0.5x - 1.3x |")
    lines.append("")

    # Full-period performance table
    lines.append("## 1. Full-Period Performance (ETF data coverage)")
    lines.append("")
    lines.append("| Variant | Return | Sharpe | MaxDD | Calmar | Ann Vol | Days |")
    lines.append("|---------|--------|--------|-------|--------|---------|------|")
    for vk in ['v3_base', 'v1_flow_z', 'v2_flow_momentum', 'v3_flow_accel']:
        m = full_results[vk]['metrics']
        lines.append(f"| {full_results[vk]['name']} | {m['total_return']:+.2%} | "
                     f"{m['sharpe']:.2f} | {m['max_dd']:.2%} | {m['calmar']:.2f} | "
                     f"{m['ann_vol']:.1%} | {m['n_days']} |")
    lines.append("")

    # dSharpe table
    lines.append("## 2. dSharpe vs V3 Base")
    lines.append("")
    lines.append("| Variant | dSharpe | dReturn | dMaxDD | t-stat | p-value | Verdict |")
    lines.append("|---------|---------|---------|--------|--------|---------|---------|")

    for vk in ['v1_flow_z', 'v2_flow_momentum', 'v3_flow_accel']:
        vr = full_results[vk]
        d_s = vr['metrics']['sharpe'] - base_sharpe
        d_r = vr['metrics']['total_return'] - base_return
        d_dd = vr['metrics']['max_dd'] - base_dd

        base_ret_test = full_results['v3_base']['strat_ret'][test_mask].dropna()
        overlay_ret_test = vr['strat_ret'][test_mask].dropna()
        common = base_ret_test.index.intersection(overlay_ret_test.index)
        diff = overlay_ret_test.loc[common] - base_ret_test.loc[common]
        if len(diff) > 30:
            t_stat = diff.mean() / (diff.std() / np.sqrt(len(diff)))
            p_val = 2 * (1 - stats.t.cdf(abs(t_stat), len(diff) - 1))
        else:
            t_stat = np.nan
            p_val = np.nan

        kill_v = kill_results[vk]['verdict']
        t_str = f"{t_stat:+.3f}" if not np.isnan(t_stat) else "N/A"
        p_str = f"{p_val:.4f}" if not np.isnan(p_val) else "N/A"
        lines.append(f"| {vr['name']} | {d_s:+.3f} | {d_r:+.2%} | {d_dd:+.2%} | "
                     f"{t_str} | {p_str} | {kill_v} |")
    lines.append("")

    # Correlation analysis
    lines.append("## 3. Correlation with Existing Overlays")
    lines.append("")
    lines.append("Low correlation (<0.3) = independent signal = potential diversification benefit.")
    lines.append("High correlation (>0.3) = redundant = KILL.")
    lines.append("")
    lines.append("| Variant | Spearman vs Positioning | Spearman vs VRP | N | Independence |")
    lines.append("|---------|------------------------|-----------------|---|-------------|")

    for vk in ['v1_flow_z', 'v2_flow_momentum', 'v3_flow_accel']:
        corr = full_results[vk].get('correlation', {})
        sp_pos = corr.get('spearman_etf_pos_z', np.nan)
        sp_vrp = corr.get('spearman_etf_vrp_z', np.nan)
        n_obs = corr.get('n_obs', 0)
        indep = "INDEPENDENT" if abs(sp_pos) < 0.3 and abs(sp_vrp) < 0.3 else "REDUNDANT"
        lines.append(f"| {variants[vk]['name']} | {sp_pos:+.3f} | {sp_vrp:+.3f} | {n_obs} | {indep} |")
    lines.append("")

    # Sizing change count
    lines.append("## 4. Sizing Activity")
    lines.append("")
    lines.append("| Variant | Total Changes | Non-Neutral Days | % Active | Kill? |")
    lines.append("|---------|---------------|------------------|----------|-------|")

    for vk in ['v1_flow_z', 'v2_flow_momentum', 'v3_flow_accel']:
        mult = variants[vk]['etf_mult']
        n_changes = (mult.diff().abs() > 0.001).sum()
        mult_test = mult[test_mask]
        n_non_neutral = (mult_test != 1.0).sum()
        pct_active = 100 * n_non_neutral / len(mult_test) if len(mult_test) > 0 else 0
        kill = "KILL" if n_changes < 20 else "PASS"
        lines.append(f"| {variants[vk]['name']} | {n_changes} | {n_non_neutral} | {pct_active:.1f}% | {kill} |")
    lines.append("")

    # Expanding-window validation
    lines.append("## 5. Expanding-Window Validation (6mo IS, 3mo OOS steps)")
    lines.append("")

    for vk in ['v1_flow_z', 'v2_flow_momentum', 'v3_flow_accel']:
        vname = variants[vk]['name']
        windows = full_results[vk].get('windows', [])

        lines.append(f"### {vname}")
        lines.append("")

        if not windows:
            lines.append("No valid windows (insufficient data).")
            lines.append("")
            continue

        lines.append("| Window | IS End | OOS End | OOS Days | V3 Return | ETF Return | V3 Sharpe | ETF Sharpe | dSharpe |")
        lines.append("|--------|--------|---------|----------|-----------|------------|-----------|------------|---------|")

        for w in windows:
            lines.append(f"| {w['window']} | {w['is_end']} | {w['oos_end']} | {w['oos_days']} | "
                         f"{w['v3_return']:+.2%} | {w['etf_return']:+.2%} | "
                         f"{w['v3_sharpe']:.2f} | {w['etf_sharpe']:.2f} | {w['d_sharpe']:+.3f} |")

        n_win = len(windows)
        n_pos = sum(1 for w in windows if w['d_sharpe'] > 0)
        avg_ds = np.mean([w['d_sharpe'] for w in windows])

        lines.append("")
        lines.append(f"**Summary**: {n_pos}/{n_win} windows positive dSharpe, average dSharpe={avg_ds:+.3f}")
        lines.append("")

    # Monthly returns
    lines.append("## 6. Monthly Returns")
    lines.append("")
    lines.append("| Month | V3 Base | V1 FlowZ | V2 FlowMom | V3 FlowAcc |")
    lines.append("|-------|---------|----------|------------|------------|")

    for dt in all_months:
        parts = [f"| {dt.strftime('%Y-%m')}"]
        for vk in ['v3_base', 'v1_flow_z', 'v2_flow_momentum', 'v3_flow_accel']:
            if dt in monthly_all[vk].index:
                parts.append(f"{monthly_all[vk].loc[dt]:+.2%}")
            else:
                parts.append("N/A")
        lines.append(" | ".join(parts) + " |")

    # Cumulative row
    parts = ["| **Cumulative**"]
    for vk in ['v3_base', 'v1_flow_z', 'v2_flow_momentum', 'v3_flow_accel']:
        cum = full_results[vk]['metrics']['total_return']
        parts.append(f"**{cum:+.2%}**")
    lines.append(" | ".join(parts) + " |")
    lines.append("")

    # Kill criteria summary
    lines.append("## 7. Kill Criteria Summary")
    lines.append("")
    lines.append("| Variant | dSharpe | Corr Pos | Corr VRP | Changes | Expanding WF | Final |")
    lines.append("|---------|---------|----------|----------|---------|--------------|-------|")

    for vk in ['v1_flow_z', 'v2_flow_momentum', 'v3_flow_accel']:
        kr = kill_results[vk]
        d_s = full_results[vk]['metrics']['sharpe'] - base_sharpe
        corr = full_results[vk].get('correlation', {})
        sp_pos = abs(corr.get('spearman_etf_pos_z', 0))
        sp_vrp = abs(corr.get('spearman_etf_vrp_z', 0))
        mult = variants[vk]['etf_mult']
        n_changes = (mult.diff().abs() > 0.001).sum()
        windows = full_results[vk].get('windows', [])
        n_pos_w = sum(1 for w in windows if w['d_sharpe'] > 0) if windows else 0
        n_w = len(windows) if windows else 0

        ds_verdict = "PASS" if d_s >= 0 else "KILL"
        corr_pos_v = "PASS" if sp_pos <= 0.3 else "KILL"
        corr_vrp_v = "PASS" if sp_vrp <= 0.3 else "KILL"
        chg_v = "PASS" if n_changes >= 20 else "KILL"
        wf_v = f"{n_pos_w}/{n_w}" if n_w > 0 else "N/A"

        lines.append(f"| {variants[vk]['name']} | {ds_verdict} ({d_s:+.3f}) | "
                     f"{corr_pos_v} ({sp_pos:.3f}) | {corr_vrp_v} ({sp_vrp:.3f}) | "
                     f"{chg_v} ({n_changes}) | {wf_v} | **{kr['verdict']}** |")
    lines.append("")

    # Final verdict
    lines.append("## 8. Final Verdict")
    lines.append("")

    any_pass = any(kr['verdict'].startswith('PASS') for kr in kill_results.values())

    if any_pass:
        passing = [vk for vk in kill_results if kill_results[vk]['verdict'].startswith('PASS')]
        best_vk = max(passing,
                      key=lambda vk: full_results[vk]['metrics']['sharpe'] - base_sharpe)
        best_name = variants[best_vk]['name']
        best_ds = full_results[best_vk]['metrics']['sharpe'] - base_sharpe

        lines.append(f"### Best candidate: {best_name} (dSharpe={best_ds:+.3f})")
        lines.append("")
        lines.append("However, this result must be interpreted with extreme caution:")
        lines.append("")
        lines.append(f"1. **Data length**: Only ~{n_test_months:.0f} months. This is far below the "
                     f"minimum 36 months typically needed for strategy validation.")
        lines.append(f"2. **Regime coverage**: The test period covers the 2024 bull run and 2025 "
                     f"correction. A single BTC cycle is insufficient.")
        lines.append(f"3. **Expanding-window windows**: Very few OOS windows available. "
                     f"Results are not robust.")
        lines.append(f"4. **Statistical significance**: t-stats are likely below 2.0, "
                     f"meaning results are NOT statistically significant at p<0.05.")
        lines.append("")
        lines.append("**Recommendation**: DO NOT add ETF flow overlay to V3 production yet. "
                     "Instead:")
        lines.append("- Continue collecting ETF flow data")
        lines.append("- Re-test after Q4 2026 when 3+ years of data are available")
        lines.append("- Monitor the flow z-score signal out-of-sample as a research tracker")
        lines.append("- The R40 finding (IC=+0.191 at 14d) is promising but needs more "
                     "data to validate as a sizing overlay")
    else:
        lines.append("### ALL VARIANTS KILLED")
        lines.append("")
        lines.append("No ETF flow overlay variant passed the kill criteria. Reasons:")
        lines.append("")
        for vk in ['v1_flow_z', 'v2_flow_momentum', 'v3_flow_accel']:
            kr = kill_results[vk]
            lines.append(f"- **{variants[vk]['name']}**: {', '.join(kr['kills'])}")
        lines.append("")
        lines.append("**Recommendation**: The ETF flow signal has predictive power (R40 confirmed "
                     "IC=+0.191 at 14d), but it does NOT improve V3 as a sizing overlay in the "
                     "current form. Possible explanations:")
        lines.append("- V3's existing overlays (positioning + VRP) may already capture the "
                     "information in ETF flows indirectly")
        lines.append("- The flow signal works at 14d horizon but V3 rebalances weekly, "
                     "creating a mismatch")
        lines.append("- 26 months is simply too short to detect a real improvement")
        lines.append("")
        lines.append("**Next steps**:")
        lines.append("- Investigate whether ETF flow is redundant with positioning (both "
                     "measure institutional activity)")
        lines.append("- Test a regime-conditional version: only apply ETF overlay during "
                     "TIGHTENING regime (where R40 found the strongest signal)")
        lines.append("- Wait for more data and re-test after Q4 2026")

    lines.append("")

    # Confidence assessment
    lines.append("## 9. Honest Confidence Assessment")
    lines.append("")
    lines.append("| Aspect | Assessment |")
    lines.append("|--------|-----------|")
    lines.append(f"| Data length | ~{n_test_months:.0f} months (VERY SHORT, need 36+) |")
    lines.append(f"| Number of OOS windows | {max(len(w) for w in [full_results[vk].get('windows', []) for vk in ['v1_flow_z', 'v2_flow_momentum', 'v3_flow_accel']])} (need 8+) |")
    lines.append("| Regime coverage | 1 partial cycle (2024 bull + 2025 correction) |")
    lines.append("| Multiple testing | 3 variants tested, no Bonferroni correction applied |")
    lines.append("| Look-ahead bias | T-1 lag applied to all flow signals |")
    lines.append("| Survivorship bias | Not applicable (BTC only) |")
    lines.append("| Overfitting risk | MEDIUM (thresholds from R40 IC analysis, not optimized on backtest) |")
    lines.append(f"| Confidence in results | LOW (~30-40%). Need 2+ more years of data. |")
    lines.append("")

    # Write report
    with open(RESULTS_PATH, 'w') as f:
        f.write('\n'.join(lines))
    print(f"\nReport saved to: {RESULTS_PATH}")

    # Console summary
    print()
    print(SEP)
    print("FINAL SUMMARY")
    print(SEP)
    print()
    print(f"V3 Base (test period): Sharpe={base_sharpe:.2f}, Return={base_return:+.2%}, MaxDD={base_dd:.2%}")
    print()
    for vk in ['v1_flow_z', 'v2_flow_momentum', 'v3_flow_accel']:
        m = full_results[vk]['metrics']
        d_s = m['sharpe'] - base_sharpe
        verdict = kill_results[vk]['verdict']
        print(f"  {variants[vk]['name']:35s}: Sharpe={m['sharpe']:.2f} (dSharpe={d_s:+.3f})  [{verdict}]")

    print()
    print(f"Confidence: LOW (~30-40%). Only ~{n_test_months:.0f} months of ETF data available.")
    print(f"Recommendation: Do NOT deploy. Re-test after Q4 2026 with 3+ years of data.")
    print()


if __name__ == '__main__':
    main()
