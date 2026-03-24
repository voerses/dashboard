#!/workspace/venv/bin/python
"""
VRP (Volatility Risk Premium) Sizing Overlay Test
===================================================

Tests VRP z-score as a POSITION SIZING OVERLAY on a trend-following base for BTC.

Context from prior research:
  - VRP z-score IC=0.268 for BTC 7d volatility prediction (p<0.001)
  - It predicts VOLATILITY, not direction -- use for sizing, not entry/exit
  - A positioning overlay (Top Trader L/S) adds +0.31 OOS Sharpe on trend base
  - This test checks whether VRP sizing adds INDEPENDENTLY of positioning

VRP Signal Construction:
  1. Realized vol: 20d rolling std of BTC daily log returns, annualized (x sqrt(365))
  2. Implied vol: BTC DVOL from Deribit (daily close)
  3. VRP = Implied vol - Realized vol
  4. VRP z-score: 60d rolling z-score of VRP
  5. Sizing rule:
     - High VRP z > 1    -> 1.3x (vol overpriced, market complacent)
     - Normal -0.5 < z < 1 -> 1.0x
     - Low VRP z < -0.5   -> 0.5x (vol cheap, expect turbulence)
     - Very low z < -1.5  -> 0.3x (extreme turbulence expected)

Variants:
  1. Base Only (trend following, 50/200 SMA)
  2. Base + Positioning only (R60 baseline)
  3. Base + VRP only (isolate VRP contribution)
  4. Base + Positioning + VRP (full stack)

Temporal split:
  - IS: start of DVOL data to 2024-12-31
  - OOS: 2025-01-01 to latest
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


# ── 1. Data Loading ──────────────────────────────────────────────────────────

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
        print("  WARNING: DVOL file not found. Will use RV proxy.")
        return pd.Series(dtype=float)

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


# ── 2. Signal Construction ───────────────────────────────────────────────────

def build_trend_signal(btc_daily):
    """Base trend-following signal: long when close > 50d SMA AND close > 200d SMA.
    Exit when close < 50 SMA (hysteresis)."""
    print("[4/5] Building trend signal (50/200 SMA)...")
    sma50 = btc_daily['close'].rolling(50, min_periods=50).mean()
    sma200 = btc_daily['close'].rolling(200, min_periods=200).mean()

    position = pd.Series(0.0, index=btc_daily.index)
    in_position = False
    for i in range(len(btc_daily)):
        close = btc_daily['close'].iloc[i]
        s50 = sma50.iloc[i]
        s200 = sma200.iloc[i]
        if pd.isna(s50) or pd.isna(s200):
            position.iloc[i] = 0.0
            continue
        if not in_position:
            if close > s50 and close > s200:
                in_position = True
                position.iloc[i] = 1.0
            else:
                position.iloc[i] = 0.0
        else:
            if close < s50:
                in_position = False
                position.iloc[i] = 0.0
            else:
                position.iloc[i] = 1.0
    print(f"  Trend signal: {position.sum():.0f} days long out of {len(position)} ({100*position.mean():.1f}%)")
    return position


def build_positioning_signal(btc_daily, positioning):
    """
    Positioning overlay (from R60):
    - Top Trader L/S: sum_toptrader_ls_ratio, 30d rolling z-score
    - L/S Divergence: (count_toptrader_ls_ratio - count_ls_ratio), 30d rolling z-score
    - Combined z-score -> sizing multiplier
    """
    print("[5a/5] Building positioning signal...")
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
    print(f"  Positioning multiplier distribution:")
    for val in [0.3, 0.5, 1.0, 1.3, 1.5]:
        pct = (pos_multiplier == val).mean() * 100
        print(f"    {val}x: {pct:.1f}%")
    return pos_multiplier, combined_z


def build_vrp_signal(btc_daily, dvol_series):
    """
    VRP sizing overlay:
    1. Realized vol: 20d rolling std of daily log returns, annualized
    2. Implied vol: DVOL close (already annualized %)
    3. VRP = IV - RV
    4. VRP z-score: 60d rolling z-score
    5. Sizing rule based on z-score thresholds
    """
    print("[5b/5] Building VRP signal...")

    log_ret = np.log(btc_daily['close'] / btc_daily['close'].shift(1))

    # Realized vol: 20d rolling, annualized to % points
    rv_20d = log_ret.rolling(20, min_periods=15).std() * np.sqrt(365) * 100

    if dvol_series.empty or len(dvol_series) < 30:
        print("  WARNING: DVOL data insufficient. Using RV-based proxy for IV.")
        print("  Proxy: 90d RV as slow-moving IV proxy (captures long-run vol expectations)")
        iv_proxy = log_ret.rolling(90, min_periods=60).std() * np.sqrt(365) * 100
        # Scale up by typical VRP ratio (IV is usually ~20% higher than RV)
        iv_proxy = iv_proxy * 1.2
        iv = iv_proxy
        vrp_source = "proxy (90d RV x 1.2)"
    else:
        # Use actual DVOL, align to daily index
        iv = dvol_series.reindex(btc_daily.index).ffill()
        vrp_source = "Deribit DVOL"

    # VRP = IV - RV (high VRP = vol overpriced = calm expected)
    vrp = iv - rv_20d

    # 60d rolling z-score of VRP
    vrp_mu = vrp.rolling(60, min_periods=30).mean()
    vrp_sigma = vrp.rolling(60, min_periods=30).std()
    vrp_z = (vrp - vrp_mu) / vrp_sigma.replace(0, np.nan)

    # Sizing multiplier
    def vrp_z_to_multiplier(z):
        if pd.isna(z):
            return 1.0
        if z > 1.0:
            return 1.3   # Vol overpriced, market complacent -> size up
        elif z > -0.5:
            return 1.0   # Normal
        elif z > -1.5:
            return 0.5   # Vol cheap, turbulence expected -> reduce
        else:
            return 0.3   # Extreme stress expected -> minimum

    vrp_multiplier = vrp_z.apply(vrp_z_to_multiplier)

    print(f"  VRP source: {vrp_source}")
    print(f"  VRP stats: mean={vrp.dropna().mean():.1f}, std={vrp.dropna().std():.1f}")
    print(f"  VRP z-score stats: mean={vrp_z.dropna().mean():.2f}, std={vrp_z.dropna().std():.2f}")
    print(f"  VRP multiplier distribution:")
    for val in [0.3, 0.5, 1.0, 1.3]:
        pct = (vrp_multiplier == val).mean() * 100
        print(f"    {val}x: {pct:.1f}%")

    return vrp_multiplier, vrp_z, vrp, rv_20d, iv


# ── 3. Backtest Engine ───────────────────────────────────────────────────────

def compute_final_position(base_pos, pos_mult, vrp_mult, variant):
    """Compute final position based on variant."""
    if variant == 'base_only':
        final = base_pos.copy()
    elif variant == 'base_positioning':
        final = base_pos * pos_mult
    elif variant == 'base_vrp':
        final = base_pos * vrp_mult
    elif variant == 'base_pos_vrp':
        final = base_pos * pos_mult * vrp_mult
    else:
        raise ValueError(f"Unknown variant: {variant}")
    # Clip to [0, 2.0] -- never short, max 2.0x (1.5 * 1.3 = 1.95)
    final = final.clip(0, 2.0)
    return final


def run_backtest(btc_daily, final_position, cost_bps=COST_BPS):
    """Run backtest with weekly rebalancing and transaction costs."""
    daily_ret = btc_daily['close'].pct_change()

    # Identify rebalance days (every Monday)
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


# ── 4. Analysis Helpers ──────────────────────────────────────────────────────

def overlay_correlation_analysis(pos_mult, vrp_mult, pos_z, vrp_z, common_index):
    """Analyze correlation between positioning and VRP overlays."""
    print("\n--- Overlay Correlation Analysis ---")

    # Align to common index
    p = pos_mult.reindex(common_index).dropna()
    v = vrp_mult.reindex(common_index).dropna()
    common = p.index.intersection(v.index)
    p = p.loc[common]
    v = v.loc[common]

    if len(common) < 30:
        print("  Insufficient overlapping data for correlation analysis")
        return {}

    # Multiplier correlation
    corr_mult = p.corr(v)
    print(f"  Multiplier correlation (Pearson): {corr_mult:.3f}")

    # Z-score correlation
    pz = pos_z.reindex(common).dropna()
    vz = vrp_z.reindex(common).dropna()
    common_z = pz.index.intersection(vz.index)
    if len(common_z) > 30:
        corr_z = pz.loc[common_z].corr(vz.loc[common_z])
        spearman_z, spearman_p = stats.spearmanr(pz.loc[common_z], vz.loc[common_z])
        print(f"  Z-score correlation (Pearson): {corr_z:.3f}")
        print(f"  Z-score correlation (Spearman): {spearman_z:.3f} (p={spearman_p:.4f})")
    else:
        corr_z = np.nan
        spearman_z = np.nan
        spearman_p = np.nan

    # Agreement frequency
    both_reduce = ((p < 1.0) & (v < 1.0)).sum()
    both_boost = ((p > 1.0) & (v > 1.0)).sum()
    disagree = ((p > 1.0) & (v < 1.0)).sum() + ((p < 1.0) & (v > 1.0)).sum()
    both_neutral = ((p == 1.0) & (v == 1.0)).sum()
    total = len(common)

    print(f"  Agreement analysis ({total} days):")
    print(f"    Both reduce:   {both_reduce} ({100*both_reduce/total:.1f}%)")
    print(f"    Both boost:    {both_boost} ({100*both_boost/total:.1f}%)")
    print(f"    Disagree:      {disagree} ({100*disagree/total:.1f}%)")
    print(f"    Both neutral:  {both_neutral} ({100*both_neutral/total:.1f}%)")

    return {
        'corr_multiplier': corr_mult,
        'corr_z_pearson': corr_z,
        'corr_z_spearman': spearman_z,
        'spearman_p': spearman_p,
        'pct_both_reduce': 100*both_reduce/total,
        'pct_both_boost': 100*both_boost/total,
        'pct_disagree': 100*disagree/total,
        'pct_both_neutral': 100*both_neutral/total,
    }


def vrp_ic_analysis(vrp_z, btc_daily, common_index):
    """Test VRP z-score IC for forward volatility and returns."""
    print("\n--- VRP Information Coefficient Analysis ---")

    log_ret = np.log(btc_daily['close'] / btc_daily['close'].shift(1))

    # Forward realized vol (7d)
    fwd_rv_7d = log_ret.rolling(7).std().shift(-7) * np.sqrt(365) * 100
    # Forward returns
    fwd_ret_7d = btc_daily['close'].pct_change(7).shift(-7)
    fwd_ret_1d = btc_daily['close'].pct_change(1).shift(-1)

    # Align
    df = pd.DataFrame({
        'vrp_z': vrp_z,
        'fwd_rv_7d': fwd_rv_7d,
        'fwd_ret_7d': fwd_ret_7d,
        'fwd_ret_1d': fwd_ret_1d,
    }).reindex(common_index).dropna()

    results = {}
    for target_name, target_col in [('Fwd 7D RV', 'fwd_rv_7d'), ('Fwd 7D Return', 'fwd_ret_7d'), ('Fwd 1D Return', 'fwd_ret_1d')]:
        valid = df[['vrp_z', target_col]].dropna()
        if len(valid) < 30:
            print(f"  VRP z -> {target_name}: N={len(valid)} (too few)")
            continue
        ic, pval = stats.spearmanr(valid['vrp_z'], valid[target_col])
        n = len(valid)
        t_stat = ic * np.sqrt(n - 2) / np.sqrt(1 - ic**2) if abs(ic) < 1 else np.inf
        sig = '***' if abs(t_stat) > 3 else '**' if abs(t_stat) > 2 else '*' if abs(t_stat) > 1.65 else ''
        print(f"  VRP z -> {target_name}: IC={ic:+.4f}, t={t_stat:+.2f}{sig}, N={n}")
        results[target_name] = {'ic': ic, 't_stat': t_stat, 'n': n}

    return results


# ── 5. Main Execution ────────────────────────────────────────────────────────

def main():
    print("=" * 72)
    print("VRP SIZING OVERLAY TEST")
    print("Testing VRP z-score as position sizing overlay on trend-following base")
    print("=" * 72)
    print()

    # Load data
    btc_daily = load_btc_daily()
    dvol = load_dvol()
    positioning = load_positioning()
    print()

    # Determine IS start based on DVOL data availability
    if not dvol.empty:
        is_start = dvol.index.min().strftime('%Y-%m-%d')
    else:
        is_start = '2021-03-24'  # DVOL start date from data inspection
    print(f"IS period: {is_start} to {IS_END}")
    print(f"OOS period: {OOS_START} to latest")
    print()

    # Build signals
    base_position = build_trend_signal(btc_daily)
    pos_multiplier, pos_combined_z = build_positioning_signal(btc_daily, positioning)
    vrp_multiplier, vrp_z, vrp_raw, rv_20d, iv = build_vrp_signal(btc_daily, dvol)
    print()

    # Define variants
    variants = {
        'base_only':        'Base Only (Trend)',
        'base_positioning': 'Base + Positioning',
        'base_vrp':         'Base + VRP',
        'base_pos_vrp':     'Base + Positioning + VRP',
    }

    # Run backtests
    print("Running backtests...")
    results = {}
    for variant_key, variant_name in variants.items():
        final_pos = compute_final_position(
            base_position, pos_multiplier, vrp_multiplier, variant_key
        )
        strat_ret, held_pos, costs = run_backtest(btc_daily, final_pos)

        is_mask = (btc_daily.index >= is_start) & (btc_daily.index <= IS_END)
        oos_mask = btc_daily.index >= OOS_START

        is_ret = strat_ret[is_mask]
        oos_ret = strat_ret[oos_mask]

        is_metrics = compute_metrics(is_ret, f"{variant_name} (IS)")
        oos_metrics = compute_metrics(oos_ret, f"{variant_name} (OOS)")

        is_turnover = compute_turnover(held_pos[is_mask])
        oos_turnover = compute_turnover(held_pos[oos_mask])

        results[variant_key] = {
            'name': variant_name,
            'is': is_metrics,
            'oos': oos_metrics,
            'is_turnover': is_turnover,
            'oos_turnover': oos_turnover,
            'strat_ret': strat_ret,
            'held_pos': held_pos,
            'costs': costs,
            'final_pos': final_pos,
        }
        print(f"  {variant_name}: IS Sharpe={is_metrics['sharpe']:.2f}, OOS Sharpe={oos_metrics['sharpe']:.2f}")
    print()

    # ── Correlation Analysis ──
    common_idx = btc_daily.index[(btc_daily.index >= is_start)]
    corr_results = overlay_correlation_analysis(
        pos_multiplier, vrp_multiplier, pos_combined_z, vrp_z, common_idx
    )

    # IS/OOS split for correlation
    is_idx = btc_daily.index[(btc_daily.index >= is_start) & (btc_daily.index <= IS_END)]
    oos_idx = btc_daily.index[btc_daily.index >= OOS_START]

    print("\n  IS period correlation:")
    corr_is = overlay_correlation_analysis(pos_multiplier, vrp_multiplier, pos_combined_z, vrp_z, is_idx)

    print("\n  OOS period correlation:")
    corr_oos = overlay_correlation_analysis(pos_multiplier, vrp_multiplier, pos_combined_z, vrp_z, oos_idx)

    # ── VRP IC Analysis ──
    print("\n  Full period:")
    ic_full = vrp_ic_analysis(vrp_z, btc_daily, common_idx)
    print("\n  IS period:")
    ic_is = vrp_ic_analysis(vrp_z, btc_daily, is_idx)
    print("\n  OOS period:")
    ic_oos = vrp_ic_analysis(vrp_z, btc_daily, oos_idx)

    # ── Monthly OOS Returns ──
    # Find best OOS Sharpe variant
    best_key = max(results.keys(), key=lambda k: results[k]['oos']['sharpe'])
    best_name = results[best_key]['name']
    best_oos_ret = results[best_key]['strat_ret'][oos_mask]
    monthly_oos = (1 + best_oos_ret).resample('ME').prod() - 1

    # Also compute monthly for all variants
    monthly_all = {}
    for vk in variants:
        vr = results[vk]['strat_ret'][oos_mask]
        monthly_all[vk] = (1 + vr).resample('ME').prod() - 1

    # ── Statistical Significance Tests ──
    print("\n--- Statistical Significance ---")
    for vk in ['base_positioning', 'base_vrp', 'base_pos_vrp']:
        overlay_ret = results[vk]['strat_ret'][oos_mask].dropna()
        base_ret = results['base_only']['strat_ret'][oos_mask].dropna()
        common = overlay_ret.index.intersection(base_ret.index)
        diff = overlay_ret.loc[common] - base_ret.loc[common]
        if len(diff) > 30:
            t_stat = diff.mean() / (diff.std() / np.sqrt(len(diff)))
            print(f"  {results[vk]['name']} vs Base: t-stat={t_stat:.3f}, N={len(diff)}")
        else:
            print(f"  {results[vk]['name']} vs Base: insufficient data")

    # Check VRP marginal contribution vs positioning
    print("\n  VRP marginal (on top of positioning):")
    full_ret = results['base_pos_vrp']['strat_ret'][oos_mask].dropna()
    pos_ret = results['base_positioning']['strat_ret'][oos_mask].dropna()
    common = full_ret.index.intersection(pos_ret.index)
    diff_marginal = full_ret.loc[common] - pos_ret.loc[common]
    if len(diff_marginal) > 30:
        t_marginal = diff_marginal.mean() / (diff_marginal.std() / np.sqrt(len(diff_marginal)))
        print(f"  (Pos+VRP) vs (Pos only): t-stat={t_marginal:.3f}, N={len(diff_marginal)}")
    else:
        t_marginal = 0
        print(f"  Insufficient data for marginal test")

    # ── VRP Regime Analysis ──
    print("\n--- VRP Regime Performance (OOS) ---")
    vrp_z_oos = vrp_z.reindex(oos_idx)
    base_ret_oos = results['base_only']['strat_ret'].reindex(oos_idx)
    vrp_ret_oos = results['base_vrp']['strat_ret'].reindex(oos_idx)

    regimes = {
        'z > 1 (complacent)': vrp_z_oos > 1,
        '-0.5 < z <= 1 (normal)': (vrp_z_oos > -0.5) & (vrp_z_oos <= 1),
        '-1.5 < z <= -0.5 (caution)': (vrp_z_oos > -1.5) & (vrp_z_oos <= -0.5),
        'z <= -1.5 (extreme)': vrp_z_oos <= -1.5,
    }

    regime_perf = {}
    for rname, mask in regimes.items():
        mask_valid = mask.fillna(False)
        n_days = mask_valid.sum()
        if n_days < 5:
            print(f"  {rname}: {n_days} days (too few)")
            regime_perf[rname] = {'n_days': n_days, 'base_avg': np.nan, 'vrp_avg': np.nan}
            continue
        base_avg = base_ret_oos[mask_valid].mean() * 365 * 100
        vrp_avg = vrp_ret_oos[mask_valid].mean() * 365 * 100
        base_vol = base_ret_oos[mask_valid].std() * np.sqrt(365) * 100
        vrp_vol = vrp_ret_oos[mask_valid].std() * np.sqrt(365) * 100
        print(f"  {rname}: {n_days} days, Base ann={base_avg:+.1f}% (vol={base_vol:.1f}%), VRP ann={vrp_avg:+.1f}% (vol={vrp_vol:.1f}%)")
        regime_perf[rname] = {'n_days': n_days, 'base_avg': base_avg, 'vrp_avg': vrp_avg, 'base_vol': base_vol, 'vrp_vol': vrp_vol}

    # ── Generate Report ──
    print()
    print("=" * 72)
    print("GENERATING REPORT")
    print("=" * 72)

    lines = []
    lines.append("# VRP Sizing Overlay Results")
    lines.append("")
    lines.append(f"**Run date**: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"**IS period**: {is_start} to {IS_END}")
    lines.append(f"**OOS period**: {OOS_START} to latest")
    lines.append(f"**DVOL source**: {'Deribit BTC DVOL' if not dvol.empty else 'PROXY (90d RV x 1.2)'}")
    lines.append(f"**Rebalancing**: Weekly (Monday)")
    lines.append(f"**Transaction cost**: {COST_BPS} bps round-trip")
    lines.append("")

    lines.append("## Architecture")
    lines.append("")
    lines.append("- **Base**: Trend following (50/200 SMA crossover with hysteresis)")
    lines.append("- **Positioning overlay**: Top Trader L/S + L/S Divergence z-score -> 0.3x to 1.5x")
    lines.append("- **VRP overlay**: VRP z-score -> 0.3x to 1.3x")
    lines.append("  - VRP = Implied Vol (DVOL) - Realized Vol (20d)")
    lines.append("  - High VRP z > 1: vol overpriced, market complacent -> 1.3x")
    lines.append("  - Normal -0.5 < z < 1 -> 1.0x")
    lines.append("  - Low VRP z < -0.5: vol cheap, turbulence expected -> 0.5x")
    lines.append("  - Very low z < -1.5: extreme -> 0.3x")
    lines.append("")

    # ── 1. Variant Performance Table ──
    lines.append("## 1. Variant Performance Table")
    lines.append("")
    lines.append("| Variant | IS Return | OOS Return | IS Sharpe | OOS Sharpe | IS MaxDD | OOS MaxDD | IS Calmar | OOS Calmar |")
    lines.append("|---------|-----------|------------|-----------|------------|----------|-----------|-----------|------------|")

    for vk in ['base_only', 'base_positioning', 'base_vrp', 'base_pos_vrp']:
        r = results[vk]
        is_m = r['is']
        oos_m = r['oos']
        line = f"| {r['name']} | {is_m['ann_return']:.1%} | {oos_m['ann_return']:.1%} | {is_m['sharpe']:.2f} | {oos_m['sharpe']:.2f} | {is_m['max_dd']:.1%} | {oos_m['max_dd']:.1%} | {is_m['calmar']:.2f} | {oos_m['calmar']:.2f} |"
        lines.append(line)

    lines.append("")

    # ── 2. VRP Contribution vs Positioning ──
    lines.append("## 2. VRP Contribution vs Positioning (Marginal Analysis)")
    lines.append("")

    base_is = results['base_only']['is']
    base_oos = results['base_only']['oos']

    contributions = [
        ('+ Positioning', 'base_positioning'),
        ('+ VRP', 'base_vrp'),
        ('+ Positioning + VRP', 'base_pos_vrp'),
    ]

    lines.append("### vs Base Only")
    lines.append("")
    lines.append("| Overlay | IS dSharpe | OOS dSharpe | IS dReturn | OOS dReturn | IS dMaxDD | OOS dMaxDD |")
    lines.append("|---------|------------|-------------|------------|-------------|-----------|------------|")

    for label, vk in contributions:
        curr_is = results[vk]['is']
        curr_oos = results[vk]['oos']
        d_s_is = curr_is['sharpe'] - base_is['sharpe']
        d_s_oos = curr_oos['sharpe'] - base_oos['sharpe']
        d_r_is = curr_is['ann_return'] - base_is['ann_return']
        d_r_oos = curr_oos['ann_return'] - base_oos['ann_return']
        d_dd_is = curr_is['max_dd'] - base_is['max_dd']
        d_dd_oos = curr_oos['max_dd'] - base_oos['max_dd']
        line = f"| {label} | {d_s_is:+.3f} | {d_s_oos:+.3f} | {d_r_is:+.1%} | {d_r_oos:+.1%} | {d_dd_is:+.1%} | {d_dd_oos:+.1%} |"
        lines.append(line)

    lines.append("")

    # Marginal VRP on top of positioning
    pos_is = results['base_positioning']['is']
    pos_oos = results['base_positioning']['oos']
    full_is = results['base_pos_vrp']['is']
    full_oos = results['base_pos_vrp']['oos']

    lines.append("### Marginal VRP contribution (on top of positioning)")
    lines.append("")
    lines.append(f"- IS dSharpe: {full_is['sharpe'] - pos_is['sharpe']:+.3f}")
    lines.append(f"- OOS dSharpe: {full_oos['sharpe'] - pos_oos['sharpe']:+.3f}")
    lines.append(f"- IS dReturn: {full_is['ann_return'] - pos_is['ann_return']:+.1%}")
    lines.append(f"- OOS dReturn: {full_oos['ann_return'] - pos_oos['ann_return']:+.1%}")
    lines.append(f"- IS dMaxDD: {full_is['max_dd'] - pos_is['max_dd']:+.1%}")
    lines.append(f"- OOS dMaxDD: {full_oos['max_dd'] - pos_oos['max_dd']:+.1%}")
    lines.append("")

    # ── 3. Correlation Between Overlays ──
    lines.append("## 3. Correlation Between VRP and Positioning Overlays")
    lines.append("")
    lines.append("Low correlation = independent signals = diversification benefit.")
    lines.append("")

    lines.append("### Full period")
    lines.append(f"- Multiplier correlation: {corr_results.get('corr_multiplier', np.nan):.3f}")
    lines.append(f"- Z-score Spearman correlation: {corr_results.get('corr_z_spearman', np.nan):.3f} (p={corr_results.get('spearman_p', np.nan):.4f})")
    lines.append(f"- Both reduce: {corr_results.get('pct_both_reduce', 0):.1f}%")
    lines.append(f"- Both boost: {corr_results.get('pct_both_boost', 0):.1f}%")
    lines.append(f"- Disagree: {corr_results.get('pct_disagree', 0):.1f}%")
    lines.append(f"- Both neutral: {corr_results.get('pct_both_neutral', 0):.1f}%")
    lines.append("")

    lines.append("### IS period")
    lines.append(f"- Multiplier correlation: {corr_is.get('corr_multiplier', np.nan):.3f}")
    lines.append(f"- Z-score Spearman correlation: {corr_is.get('corr_z_spearman', np.nan):.3f}")
    lines.append("")

    lines.append("### OOS period")
    lines.append(f"- Multiplier correlation: {corr_oos.get('corr_multiplier', np.nan):.3f}")
    lines.append(f"- Z-score Spearman correlation: {corr_oos.get('corr_z_spearman', np.nan):.3f}")
    lines.append("")

    independence_verdict = "INDEPENDENT" if abs(corr_results.get('corr_z_spearman', 1)) < 0.3 else \
        "WEAKLY CORRELATED" if abs(corr_results.get('corr_z_spearman', 1)) < 0.5 else "CORRELATED"
    lines.append(f"**Independence verdict**: {independence_verdict}")
    lines.append("")

    # ── VRP IC Results ──
    lines.append("## VRP Information Coefficient")
    lines.append("")
    lines.append("Does VRP z-score predict forward volatility (its intended use) and/or returns?")
    lines.append("")
    lines.append("| Target | IS IC | IS t-stat | OOS IC | OOS t-stat |")
    lines.append("|--------|-------|-----------|--------|------------|")

    for target_name in ['Fwd 7D RV', 'Fwd 7D Return', 'Fwd 1D Return']:
        is_data = ic_is.get(target_name, {})
        oos_data = ic_oos.get(target_name, {})
        is_ic_val = is_data.get('ic', np.nan)
        is_t_val = is_data.get('t_stat', np.nan)
        oos_ic_val = oos_data.get('ic', np.nan)
        oos_t_val = oos_data.get('t_stat', np.nan)
        lines.append(f"| {target_name} | {is_ic_val:+.4f} | {is_t_val:+.2f} | {oos_ic_val:+.4f} | {oos_t_val:+.2f} |")

    lines.append("")

    # ── VRP Regime Performance ──
    lines.append("## VRP Regime Performance (OOS)")
    lines.append("")
    lines.append("| VRP Regime | Days | Base Ann Return | VRP Ann Return | Base Vol | VRP Vol |")
    lines.append("|------------|------|-----------------|----------------|----------|---------|")

    for rname, rp in regime_perf.items():
        if pd.isna(rp.get('base_avg', np.nan)):
            lines.append(f"| {rname} | {rp['n_days']} | N/A | N/A | N/A | N/A |")
        else:
            lines.append(f"| {rname} | {rp['n_days']} | {rp['base_avg']:+.1f}% | {rp['vrp_avg']:+.1f}% | {rp.get('base_vol', 0):.1f}% | {rp.get('vrp_vol', 0):.1f}% |")

    lines.append("")

    # ── 4. Monthly Returns ──
    lines.append(f"## 4. Monthly Returns (OOS) -- All Variants")
    lines.append("")
    lines.append("| Month | Base Only | Base+Pos | Base+VRP | Base+Pos+VRP |")
    lines.append("|-------|-----------|----------|----------|--------------|")

    all_months = sorted(set(monthly_all['base_only'].index))
    for dt in all_months:
        parts = [f"| {dt.strftime('%Y-%m')}"]
        for vk in ['base_only', 'base_positioning', 'base_vrp', 'base_pos_vrp']:
            if dt in monthly_all[vk].index:
                parts.append(f"{monthly_all[vk].loc[dt]:.2%}")
            else:
                parts.append("N/A")
        lines.append(" | ".join(parts) + " |")

    # Cumulative
    parts = ["| **Cumulative**"]
    for vk in ['base_only', 'base_positioning', 'base_vrp', 'base_pos_vrp']:
        cum = (1 + results[vk]['strat_ret'][oos_mask].dropna()).cumprod().iloc[-1] - 1
        parts.append(f"**{cum:.2%}**")
    lines.append(" | ".join(parts) + " |")
    lines.append("")

    # ── 5. Verdict ──
    lines.append("## 5. Verdict: Does VRP Add On Top of Positioning?")
    lines.append("")

    # Compute key metrics
    vrp_standalone_oos_dsharpe = results['base_vrp']['oos']['sharpe'] - results['base_only']['oos']['sharpe']
    vrp_marginal_oos_dsharpe = results['base_pos_vrp']['oos']['sharpe'] - results['base_positioning']['oos']['sharpe']
    pos_standalone_oos_dsharpe = results['base_positioning']['oos']['sharpe'] - results['base_only']['oos']['sharpe']

    lines.append("### Key Numbers")
    lines.append("")
    lines.append(f"- Positioning standalone OOS dSharpe: {pos_standalone_oos_dsharpe:+.3f}")
    lines.append(f"- VRP standalone OOS dSharpe: {vrp_standalone_oos_dsharpe:+.3f}")
    lines.append(f"- VRP marginal OOS dSharpe (on top of positioning): {vrp_marginal_oos_dsharpe:+.3f}")
    lines.append(f"- Overlay correlation (Spearman z-scores): {corr_results.get('corr_z_spearman', np.nan):.3f}")
    lines.append(f"- VRP marginal t-stat: {t_marginal:.3f}")
    lines.append("")

    # Decision
    if vrp_marginal_oos_dsharpe > 0.05 and abs(corr_results.get('corr_z_spearman', 1)) < 0.5:
        verdict = "YES -- VRP adds independent value"
        explanation = (
            f"VRP provides a marginal OOS Sharpe improvement of {vrp_marginal_oos_dsharpe:+.3f} "
            f"on top of positioning. The overlays are {independence_verdict.lower()} "
            f"(Spearman rho={corr_results.get('corr_z_spearman', np.nan):.3f}), confirming "
            f"VRP captures different information (volatility regime vs. crowd positioning). "
            f"The full stack (Base + Positioning + VRP) achieves OOS Sharpe of "
            f"{results['base_pos_vrp']['oos']['sharpe']:.2f} vs {results['base_only']['oos']['sharpe']:.2f} for base only."
        )
    elif vrp_standalone_oos_dsharpe > 0.05:
        verdict = "PARTIAL -- VRP adds standalone but not marginal value"
        explanation = (
            f"VRP standalone OOS dSharpe is {vrp_standalone_oos_dsharpe:+.3f}, showing value "
            f"as a sizing overlay. However, marginal VRP contribution on top of positioning "
            f"is only {vrp_marginal_oos_dsharpe:+.3f}, suggesting the two overlays capture "
            f"overlapping information. Use VRP as a REPLACEMENT for positioning if the IC "
            f"for forward volatility is stronger."
        )
    elif vrp_standalone_oos_dsharpe > -0.02 and abs(corr_results.get('corr_z_spearman', 1)) < 0.3:
        verdict = "MARGINAL -- VRP is independent but weak"
        explanation = (
            f"VRP standalone OOS dSharpe is {vrp_standalone_oos_dsharpe:+.3f} (weak). "
            f"The overlays ARE independent (rho={corr_results.get('corr_z_spearman', np.nan):.3f}), "
            f"so VRP captures different information, but the signal-to-noise is too low "
            f"for reliable sizing. Consider: (a) different z-score thresholds, "
            f"(b) longer lookback, (c) wait for more DVOL history."
        )
    else:
        verdict = "NO -- VRP does not add value"
        explanation = (
            f"VRP standalone OOS dSharpe is {vrp_standalone_oos_dsharpe:+.3f}. "
            f"Marginal contribution on top of positioning is {vrp_marginal_oos_dsharpe:+.3f}. "
            f"VRP sizing does not improve risk-adjusted returns out-of-sample."
        )

    lines.append(f"### **{verdict}**")
    lines.append("")
    lines.append(explanation)
    lines.append("")

    # Statistical significance caveat
    lines.append("### Statistical Significance")
    lines.append("")
    oos_days = results['base_only']['oos']['n_days']
    lines.append(f"- OOS period: {oos_days} days (~{oos_days/30:.0f} months)")

    for vk_name, vk in [('Positioning vs Base', 'base_positioning'), ('VRP vs Base', 'base_vrp'), ('Full vs Base', 'base_pos_vrp')]:
        overlay_ret_sig = results[vk]['strat_ret'][oos_mask].dropna()
        base_ret_sig = results['base_only']['strat_ret'][oos_mask].dropna()
        common_sig = overlay_ret_sig.index.intersection(base_ret_sig.index)
        diff_sig = overlay_ret_sig.loc[common_sig] - base_ret_sig.loc[common_sig]
        if len(diff_sig) > 30:
            t = diff_sig.mean() / (diff_sig.std() / np.sqrt(len(diff_sig)))
            sig = 'significant (p<0.05)' if abs(t) > 1.96 else 'marginally significant (p<0.10)' if abs(t) > 1.65 else 'not significant'
            lines.append(f"- {vk_name}: t={t:.3f} ({sig})")
        else:
            lines.append(f"- {vk_name}: insufficient data")

    lines.append("")
    lines.append(f"- VRP marginal (Pos+VRP vs Pos): t={t_marginal:.3f}")
    lines.append("")

    # Recommendations
    lines.append("### Recommendations")
    lines.append("")
    if vrp_marginal_oos_dsharpe > 0.05:
        lines.append("1. **Adopt full stack** (Base + Positioning + VRP) as production configuration")
        lines.append("2. VRP and Positioning capture different information -- both add value")
        lines.append("3. Continue accumulating DVOL data for more robust VRP estimation")
    elif vrp_standalone_oos_dsharpe > 0.02:
        lines.append("1. **Keep positioning overlay as primary** -- it contributes more OOS Sharpe")
        lines.append("2. VRP is a candidate for inclusion but needs parameter tuning")
        lines.append("3. Consider: VRP may add more during regime transitions (not captured in short OOS)")
        lines.append("4. Monitor VRP IC for forward volatility as more DVOL data accumulates")
    else:
        lines.append("1. **Positioning overlay only** -- VRP does not improve risk-adjusted returns")
        lines.append("2. VRP z-score may have value as a risk MONITOR (not sizing signal)")
        lines.append("3. Re-test when 2+ years of DVOL data is available")

    lines.append("")

    # ── Appendix: VRP Time Series ──
    lines.append("## Appendix: VRP Summary Statistics")
    lines.append("")

    vrp_common = vrp_raw.dropna()
    lines.append(f"- VRP mean: {vrp_common.mean():.1f}")
    lines.append(f"- VRP median: {vrp_common.median():.1f}")
    lines.append(f"- VRP std: {vrp_common.std():.1f}")
    lines.append(f"- VRP min: {vrp_common.min():.1f}")
    lines.append(f"- VRP max: {vrp_common.max():.1f}")
    lines.append(f"- VRP % positive: {(vrp_common > 0).mean()*100:.1f}%")
    lines.append(f"- RV 20d mean: {rv_20d.dropna().mean():.1f}%")
    lines.append(f"- IV mean: {iv.dropna().mean():.1f}%")
    lines.append("")

    # Quarterly equity snapshot
    lines.append("## Appendix: Quarterly Equity Curve (normalized to 1.0)")
    lines.append("")
    lines.append("| Date | Base Only | Base+Pos | Base+VRP | Base+Pos+VRP |")
    lines.append("|------|-----------|----------|----------|--------------|")

    for vk in results:
        cum = (1 + results[vk]['strat_ret'].fillna(0)).cumprod()
        results[vk]['_cum'] = cum

    all_dates = btc_daily.index[(btc_daily.index >= is_start)]
    quarterly = all_dates.to_series().resample('QE').last().dropna()
    for dt in quarterly:
        parts = [f"| {dt.strftime('%Y-%m-%d')}"]
        for vk in ['base_only', 'base_positioning', 'base_vrp', 'base_pos_vrp']:
            c = results[vk]['_cum']
            if dt in c.index:
                parts.append(f"{c.loc[dt]:.2f}")
            else:
                idx = c.index.get_indexer([dt], method='ffill')[0]
                if idx >= 0:
                    parts.append(f"{c.iloc[idx]:.2f}")
                else:
                    parts.append("N/A")
        lines.append(" | ".join(parts) + " |")

    lines.append("")

    # Write report
    report_path = OUTPUT_DIR / 'vrp_sizing_overlay_results.md'
    with open(report_path, 'w') as f:
        f.write('\n'.join(lines))
    print(f"\nReport saved to: {report_path}")

    # Console summary
    print("\n" + "=" * 72)
    print("SUMMARY")
    print("=" * 72)
    print(f"\nBase OOS Sharpe:             {results['base_only']['oos']['sharpe']:.2f}")
    print(f"Base+Positioning OOS Sharpe: {results['base_positioning']['oos']['sharpe']:.2f}")
    print(f"Base+VRP OOS Sharpe:         {results['base_vrp']['oos']['sharpe']:.2f}")
    print(f"Base+Pos+VRP OOS Sharpe:     {results['base_pos_vrp']['oos']['sharpe']:.2f}")
    print(f"\nPositioning standalone dSharpe: {pos_standalone_oos_dsharpe:+.3f}")
    print(f"VRP standalone dSharpe:         {vrp_standalone_oos_dsharpe:+.3f}")
    print(f"VRP marginal dSharpe:           {vrp_marginal_oos_dsharpe:+.3f}")
    print(f"Overlay correlation (Spearman): {corr_results.get('corr_z_spearman', np.nan):.3f}")
    print(f"\nVerdict: {verdict}")


if __name__ == '__main__':
    main()
