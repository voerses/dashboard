#!/workspace/venv/bin/python
"""
DVOL Rate-of-Change Overlay Test
==================================

Tests DVOL ROC(20d) z-score as a THIRD overlay on the V3 momentum strategy (s320).

Context:
  - V3 (s320) uses: 20/50 EMA trend base + Positioning overlay + VRP overlay, BTC spot only
  - VRP overlay uses IV-RV z-score (LEVEL of vol premium) for sizing
  - NEW SIGNAL: DVOL ROC(20d) has IC=0.101 at 7d with near-perfect IS/OOS stability
  - DVOL ROC is the RATE OF CHANGE of implied vol -- orthogonal to VRP level
  - Positive IC: rising IV -> rising BTC (crypto reflexive volatility)

Variants:
  1. Base Only (EMA 20/50 crossover, BTC spot, weekly rebalance)
  2. V3 Full (Base + Positioning + VRP) -- current s320
  3. V3 + DVOL ROC (Base + Positioning + VRP + DVOL ROC overlay)

Additional analyses:
  - Walk-forward validation (6 rolling windows, 6m train / 6m test)
  - Correlation between DVOL ROC multiplier and VRP multiplier
  - Delta-Sharpe with t-test for significance
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


# ── 2. Signal Construction ───────────────────────────────────────────────────

def build_ema_base_signal(btc_daily):
    """
    Base trend-following signal: long when 20d EMA > 50d EMA.
    This is the actual s320 base signal (NOT 50/200 SMA from older research).
    """
    print("[4/5] Building EMA 20/50 base signal...")
    ema20 = btc_daily['close'].ewm(span=20, adjust=False).mean()
    ema50 = btc_daily['close'].ewm(span=50, adjust=False).mean()

    # Long when fast > slow, flat otherwise (simple crossover, no hysteresis)
    position = pd.Series(0.0, index=btc_daily.index)
    position[ema20 > ema50] = 1.0
    # Warmup guard: no signal during first 50 bars
    position.iloc[:50] = 0.0

    print(f"  EMA base signal: {position.sum():.0f} days long out of {len(position)} ({100*position.mean():.1f}%)")
    return position


def build_positioning_signal(btc_daily, positioning):
    """
    Positioning overlay (from V3 s320):
    - Top Trader L/S: sum_toptrader_ls_ratio, 30d rolling z-score
    - L/S Divergence: (count_toptrader_ls_ratio - count_ls_ratio), 30d rolling z-score
    - Combined z-score -> sizing multiplier (contrarian)
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
    VRP sizing overlay (from V3 s320):
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

    # Use actual DVOL, align to daily index
    iv = dvol_series.reindex(btc_daily.index).ffill()

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
    print(f"  VRP multiplier distribution:")
    for val in [0.3, 0.5, 1.0, 1.3]:
        pct = (vrp_multiplier == val).mean() * 100
        print(f"    {val}x: {pct:.1f}%")
    return vrp_multiplier, vrp_z


def build_dvol_roc_signal(btc_daily, dvol_series):
    """
    DVOL Rate-of-Change overlay (NEW candidate):
    1. Load DVOL data
    2. Compute 20-day rate of change: dvol_roc_20d = dvol / dvol.shift(20) - 1
    3. Z-score over 60d rolling window
    4. Multiplier mapping:
       - z > 1.0  -> 1.3x (IV rising fast = bullish reflexive vol)
       - 0.0 < z < 1.0 -> 1.1x
       - -0.5 < z < 0.0 -> 1.0x (neutral)
       - z < -0.5 -> 0.7x (IV falling = less conviction)
       - z < -1.5 -> 0.5x (IV collapsing = risk-off)

    Rationale: DVOL ROC captures the RATE OF CHANGE of implied vol, orthogonal
    to VRP which captures the LEVEL of vol premium (IV - RV). Positive IC at 7d
    means rising IV -> rising BTC (crypto reflexive volatility: vol goes up WITH
    price in crypto, unlike equities).
    """
    print("[5c/5] Building DVOL ROC signal...")
    dvol = dvol_series.reindex(btc_daily.index).ffill()

    # 20-day rate of change
    dvol_roc_20d = dvol / dvol.shift(20) - 1

    # 60d rolling z-score
    roc_mu = dvol_roc_20d.rolling(60, min_periods=30).mean()
    roc_sigma = dvol_roc_20d.rolling(60, min_periods=30).std()
    dvol_roc_z = (dvol_roc_20d - roc_mu) / roc_sigma.replace(0, np.nan)

    # Multiplier mapping
    def roc_z_to_multiplier(z):
        if pd.isna(z):
            return 1.0
        if z > 1.0:
            return 1.3   # IV rising fast = bullish reflexive vol
        elif z > 0.0:
            return 1.1   # IV rising moderately
        elif z > -0.5:
            return 1.0   # Neutral
        elif z > -1.5:
            return 0.7   # IV falling = less conviction
        else:
            return 0.5   # IV collapsing = risk-off

    dvol_roc_multiplier = dvol_roc_z.apply(roc_z_to_multiplier)

    print(f"  DVOL ROC(20d) stats: mean={dvol_roc_20d.dropna().mean():.4f}, std={dvol_roc_20d.dropna().std():.4f}")
    print(f"  DVOL ROC z-score stats: mean={dvol_roc_z.dropna().mean():.2f}, std={dvol_roc_z.dropna().std():.2f}")
    print(f"  DVOL ROC multiplier distribution:")
    for val in [0.5, 0.7, 1.0, 1.1, 1.3]:
        pct = (dvol_roc_multiplier == val).mean() * 100
        print(f"    {val}x: {pct:.1f}%")

    return dvol_roc_multiplier, dvol_roc_z, dvol_roc_20d


# ── 3. Backtest Engine ───────────────────────────────────────────────────────

def compute_final_position(base_pos, pos_mult, vrp_mult, dvol_roc_mult, variant):
    """Compute final position based on variant."""
    if variant == 'base_only':
        final = base_pos.copy()
    elif variant == 'v3_full':
        final = base_pos * pos_mult * vrp_mult
    elif variant == 'v3_dvol_roc':
        final = base_pos * pos_mult * vrp_mult * dvol_roc_mult
    else:
        raise ValueError(f"Unknown variant: {variant}")
    # Clip to [0, 1.5] -- never short, max 1.5x
    final = final.clip(0, 1.5)
    return final


def run_backtest(btc_daily, final_position, cost_bps=COST_BPS):
    """Run backtest with weekly rebalancing (every 7 days) and transaction costs."""
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


# ── 4. Walk-Forward Validation ───────────────────────────────────────────────

def run_walk_forward(btc_daily, base_pos, pos_mult, vrp_mult, dvol_roc_mult):
    """
    Run walk-forward validation with 6 rolling windows (6-month train, 6-month test).
    Tests whether DVOL ROC overlay improves in majority of windows.
    """
    print("\n--- Walk-Forward Validation ---")
    print("6 rolling windows, 6-month train / 6-month test")
    print()

    # DVOL data starts 2021-03-24; need warmup for z-scores (~80 days)
    # Start walk-forward from 2021-07-01 to allow full signal construction
    wf_start = pd.Timestamp('2021-07-01')
    wf_end = btc_daily.index.max()

    # Generate windows: 6-month train + 6-month test, rolling every 6 months
    windows = []
    current = wf_start
    while current + pd.DateOffset(months=12) <= wf_end:
        train_start = current
        train_end = current + pd.DateOffset(months=6) - pd.Timedelta(days=1)
        test_start = current + pd.DateOffset(months=6)
        test_end = current + pd.DateOffset(months=12) - pd.Timedelta(days=1)
        if test_end > wf_end:
            test_end = wf_end
        windows.append({
            'train_start': train_start,
            'train_end': train_end,
            'test_start': test_start,
            'test_end': test_end,
        })
        current += pd.DateOffset(months=6)

    # Limit to 6 windows
    windows = windows[-6:] if len(windows) > 6 else windows

    wf_results = []
    for i, w in enumerate(windows):
        print(f"  Window {i+1}: Train {w['train_start'].date()} to {w['train_end'].date()}, "
              f"Test {w['test_start'].date()} to {w['test_end'].date()}")

        test_mask = (btc_daily.index >= w['test_start']) & (btc_daily.index <= w['test_end'])

        # Run V3 full (no DVOL ROC)
        final_v3 = compute_final_position(base_pos, pos_mult, vrp_mult, dvol_roc_mult, 'v3_full')
        ret_v3, _, _ = run_backtest(btc_daily, final_v3)
        v3_metrics = compute_metrics(ret_v3[test_mask], f"V3 Full (Win {i+1})")

        # Run V3 + DVOL ROC
        final_dvol = compute_final_position(base_pos, pos_mult, vrp_mult, dvol_roc_mult, 'v3_dvol_roc')
        ret_dvol, _, _ = run_backtest(btc_daily, final_dvol)
        dvol_metrics = compute_metrics(ret_dvol[test_mask], f"V3+DVOL_ROC (Win {i+1})")

        # Delta
        d_sharpe = dvol_metrics['sharpe'] - v3_metrics['sharpe']
        d_ret = dvol_metrics['ann_return'] - v3_metrics['ann_return']
        d_dd = dvol_metrics['max_dd'] - v3_metrics['max_dd']

        # T-test on daily return difference
        common_ret = ret_dvol[test_mask].dropna()
        v3_common = ret_v3[test_mask].dropna()
        common_idx = common_ret.index.intersection(v3_common.index)
        diff = common_ret.loc[common_idx] - v3_common.loc[common_idx]
        t_stat = diff.mean() / (diff.std() / np.sqrt(len(diff))) if len(diff) > 10 else 0.0

        wf_results.append({
            'window': i + 1,
            'test_start': w['test_start'].date(),
            'test_end': w['test_end'].date(),
            'v3_sharpe': v3_metrics['sharpe'],
            'dvol_roc_sharpe': dvol_metrics['sharpe'],
            'd_sharpe': d_sharpe,
            'v3_return': v3_metrics['ann_return'],
            'dvol_roc_return': dvol_metrics['ann_return'],
            'd_return': d_ret,
            'v3_maxdd': v3_metrics['max_dd'],
            'dvol_roc_maxdd': dvol_metrics['max_dd'],
            'd_maxdd': d_dd,
            't_stat': t_stat,
            'n_days': v3_metrics['n_days'],
            'improved_sharpe': d_sharpe > 0,
            'worsened_dd': dvol_metrics['max_dd'] < v3_metrics['max_dd'],  # more negative = worse
        })

        sign = '+' if d_sharpe > 0 else ''
        print(f"    V3 Sharpe={v3_metrics['sharpe']:.2f}, +DVOL_ROC Sharpe={dvol_metrics['sharpe']:.2f}, "
              f"delta={sign}{d_sharpe:.3f}, t={t_stat:.2f}")

    print()
    n_improved = sum(1 for r in wf_results if r['improved_sharpe'])
    n_worsened_dd = sum(1 for r in wf_results if r['worsened_dd'])
    print(f"  Improved Sharpe in {n_improved}/{len(wf_results)} windows")
    print(f"  Worsened MaxDD in {n_worsened_dd}/{len(wf_results)} windows")

    return wf_results


# ── 5. Correlation Analysis ──────────────────────────────────────────────────

def overlay_correlation_analysis(vrp_mult, dvol_roc_mult, vrp_z, dvol_roc_z, pos_mult, common_index):
    """Analyze correlation between DVOL ROC overlay and existing VRP/positioning overlays."""
    print("\n--- Overlay Correlation Analysis ---")

    # Align to common index
    v = vrp_mult.reindex(common_index).dropna()
    d = dvol_roc_mult.reindex(common_index).dropna()
    p = pos_mult.reindex(common_index).dropna()
    common = v.index.intersection(d.index).intersection(p.index)
    v = v.loc[common]
    d = d.loc[common]
    p = p.loc[common]

    if len(common) < 30:
        print("  Insufficient overlapping data for correlation analysis")
        return {}

    # Multiplier correlation: DVOL ROC vs VRP
    corr_dvol_vrp_mult = d.corr(v)
    print(f"  DVOL ROC vs VRP multiplier correlation (Pearson): {corr_dvol_vrp_mult:.3f}")

    # Multiplier correlation: DVOL ROC vs Positioning
    corr_dvol_pos_mult = d.corr(p)
    print(f"  DVOL ROC vs Positioning multiplier correlation (Pearson): {corr_dvol_pos_mult:.3f}")

    # Z-score correlations
    vz = vrp_z.reindex(common).dropna()
    dz = dvol_roc_z.reindex(common).dropna()
    common_z = vz.index.intersection(dz.index)

    corr_z_pearson = np.nan
    corr_z_spearman = np.nan
    spearman_p = np.nan
    if len(common_z) > 30:
        corr_z_pearson = dz.loc[common_z].corr(vz.loc[common_z])
        spearman_result = stats.spearmanr(dz.loc[common_z], vz.loc[common_z])
        corr_z_spearman = spearman_result.correlation
        spearman_p = spearman_result.pvalue
        print(f"  DVOL ROC z vs VRP z (Pearson): {corr_z_pearson:.3f}")
        print(f"  DVOL ROC z vs VRP z (Spearman): {corr_z_spearman:.3f} (p={spearman_p:.4f})")

    # Agreement frequency: DVOL ROC vs VRP
    both_reduce = ((d < 1.0) & (v < 1.0)).sum()
    both_boost = ((d > 1.0) & (v > 1.0)).sum()
    disagree = ((d > 1.0) & (v < 1.0)).sum() + ((d < 1.0) & (v > 1.0)).sum()
    both_neutral = ((d == 1.0) & (v == 1.0)).sum()
    total = len(common)

    print(f"  DVOL ROC vs VRP agreement ({total} days):")
    print(f"    Both reduce:   {both_reduce} ({100*both_reduce/total:.1f}%)")
    print(f"    Both boost:    {both_boost} ({100*both_boost/total:.1f}%)")
    print(f"    Disagree:      {disagree} ({100*disagree/total:.1f}%)")
    print(f"    Both neutral:  {both_neutral} ({100*both_neutral/total:.1f}%)")

    return {
        'corr_dvol_vrp_mult': corr_dvol_vrp_mult,
        'corr_dvol_pos_mult': corr_dvol_pos_mult,
        'corr_z_pearson': corr_z_pearson,
        'corr_z_spearman': corr_z_spearman,
        'spearman_p': spearman_p,
        'pct_both_reduce': 100*both_reduce/total,
        'pct_both_boost': 100*both_boost/total,
        'pct_disagree': 100*disagree/total,
        'pct_both_neutral': 100*both_neutral/total,
        'total_days': total,
    }


# ── 6. DVOL ROC IC Analysis ─────────────────────────────────────────────────

def dvol_roc_ic_analysis(dvol_roc_z, btc_daily, common_index):
    """Test DVOL ROC z-score IC for forward returns at multiple horizons."""
    print("\n--- DVOL ROC Information Coefficient Analysis ---")

    # Forward returns at multiple horizons
    fwd_ret_1d = btc_daily['close'].pct_change(1).shift(-1)
    fwd_ret_7d = btc_daily['close'].pct_change(7).shift(-7)
    fwd_ret_14d = btc_daily['close'].pct_change(14).shift(-14)

    df = pd.DataFrame({
        'dvol_roc_z': dvol_roc_z,
        'fwd_ret_1d': fwd_ret_1d,
        'fwd_ret_7d': fwd_ret_7d,
        'fwd_ret_14d': fwd_ret_14d,
    }).reindex(common_index).dropna()

    results = {}
    for target_name, target_col in [('Fwd 1D Return', 'fwd_ret_1d'),
                                     ('Fwd 7D Return', 'fwd_ret_7d'),
                                     ('Fwd 14D Return', 'fwd_ret_14d')]:
        valid = df[['dvol_roc_z', target_col]].dropna()
        if len(valid) < 30:
            print(f"  DVOL ROC z -> {target_name}: N={len(valid)} (too few)")
            continue
        ic, pval = stats.spearmanr(valid['dvol_roc_z'], valid[target_col])
        n = len(valid)
        t_stat = ic * np.sqrt(n - 2) / np.sqrt(1 - ic**2) if abs(ic) < 1 else np.inf
        sig = '***' if abs(t_stat) > 3 else '**' if abs(t_stat) > 2 else '*' if abs(t_stat) > 1.65 else ''
        print(f"  DVOL ROC z -> {target_name}: IC={ic:+.4f}, t={t_stat:+.2f}{sig}, N={n}")
        results[target_name] = {'ic': ic, 't_stat': t_stat, 'n': n, 'p_value': pval}

    return results


# ── 7. Main Execution ────────────────────────────────────────────────────────

def main():
    print("=" * 74)
    print("DVOL RATE-OF-CHANGE OVERLAY TEST")
    print("Testing DVOL ROC(20d) z-score as third overlay on V3 (s320)")
    print("=" * 74)
    print()

    # ── Load Data ──
    btc_daily = load_btc_daily()
    dvol = load_dvol()
    positioning = load_positioning()
    print()

    # Determine IS start based on DVOL data availability + warmup
    dvol_start = dvol.index.min()
    # Need ~80 days warmup for dvol ROC (20d) + z-score (60d)
    is_start = (dvol_start + pd.DateOffset(days=90)).strftime('%Y-%m-%d')
    print(f"IS period: {is_start} to {IS_END}")
    print(f"OOS period: {OOS_START} to latest")
    print()

    # ── Build Signals ──
    base_position = build_ema_base_signal(btc_daily)
    pos_multiplier, pos_combined_z = build_positioning_signal(btc_daily, positioning)
    vrp_multiplier, vrp_z = build_vrp_signal(btc_daily, dvol)
    dvol_roc_multiplier, dvol_roc_z, dvol_roc_raw = build_dvol_roc_signal(btc_daily, dvol)
    print()

    # ── Define Variants ──
    variants = {
        'base_only':   'Base Only (EMA 20/50)',
        'v3_full':     'V3 Full (Pos+VRP)',
        'v3_dvol_roc': 'V3 + DVOL ROC',
    }

    # ── Run Full-Period Backtests ──
    print("Running backtests...")
    results = {}
    for variant_key, variant_name in variants.items():
        final_pos = compute_final_position(
            base_position, pos_multiplier, vrp_multiplier, dvol_roc_multiplier, variant_key
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

    # ── Walk-Forward Validation ──
    wf_results = run_walk_forward(btc_daily, base_position, pos_multiplier, vrp_multiplier, dvol_roc_multiplier)

    # ── Correlation Analysis ──
    common_idx = btc_daily.index[(btc_daily.index >= is_start)]
    corr_results = overlay_correlation_analysis(
        vrp_multiplier, dvol_roc_multiplier, vrp_z, dvol_roc_z, pos_multiplier, common_idx
    )

    # IS/OOS split for correlation
    is_idx = btc_daily.index[(btc_daily.index >= is_start) & (btc_daily.index <= IS_END)]
    oos_idx = btc_daily.index[btc_daily.index >= OOS_START]

    print("\n  IS period correlation:")
    corr_is = overlay_correlation_analysis(vrp_multiplier, dvol_roc_multiplier, vrp_z, dvol_roc_z, pos_multiplier, is_idx)

    print("\n  OOS period correlation:")
    corr_oos = overlay_correlation_analysis(vrp_multiplier, dvol_roc_multiplier, vrp_z, dvol_roc_z, pos_multiplier, oos_idx)

    # ── DVOL ROC IC Analysis ──
    print("\n  Full period:")
    ic_full = dvol_roc_ic_analysis(dvol_roc_z, btc_daily, common_idx)
    print("\n  IS period:")
    ic_is = dvol_roc_ic_analysis(dvol_roc_z, btc_daily, is_idx)
    print("\n  OOS period:")
    ic_oos = dvol_roc_ic_analysis(dvol_roc_z, btc_daily, oos_idx)

    # ── Statistical Significance ──
    oos_mask = btc_daily.index >= OOS_START
    print("\n--- Statistical Significance ---")

    # V3+DVOL_ROC vs V3 Full (the key comparison)
    dvol_ret = results['v3_dvol_roc']['strat_ret'][oos_mask].dropna()
    v3_ret = results['v3_full']['strat_ret'][oos_mask].dropna()
    common = dvol_ret.index.intersection(v3_ret.index)
    diff_marginal = dvol_ret.loc[common] - v3_ret.loc[common]
    if len(diff_marginal) > 30:
        t_marginal = diff_marginal.mean() / (diff_marginal.std() / np.sqrt(len(diff_marginal)))
        print(f"  V3+DVOL_ROC vs V3 Full: t-stat={t_marginal:.3f}, N={len(diff_marginal)}")
    else:
        t_marginal = 0.0
        print(f"  Insufficient data for marginal test")

    # V3+DVOL_ROC vs Base
    dvol_ret_b = results['v3_dvol_roc']['strat_ret'][oos_mask].dropna()
    base_ret = results['base_only']['strat_ret'][oos_mask].dropna()
    common_b = dvol_ret_b.index.intersection(base_ret.index)
    diff_vs_base = dvol_ret_b.loc[common_b] - base_ret.loc[common_b]
    if len(diff_vs_base) > 30:
        t_vs_base = diff_vs_base.mean() / (diff_vs_base.std() / np.sqrt(len(diff_vs_base)))
        print(f"  V3+DVOL_ROC vs Base: t-stat={t_vs_base:.3f}, N={len(diff_vs_base)}")
    else:
        t_vs_base = 0.0

    # V3 Full vs Base
    v3_ret_b = results['v3_full']['strat_ret'][oos_mask].dropna()
    common_v3b = v3_ret_b.index.intersection(base_ret.index)
    diff_v3_base = v3_ret_b.loc[common_v3b] - base_ret.loc[common_v3b]
    if len(diff_v3_base) > 30:
        t_v3_base = diff_v3_base.mean() / (diff_v3_base.std() / np.sqrt(len(diff_v3_base)))
        print(f"  V3 Full vs Base: t-stat={t_v3_base:.3f}, N={len(diff_v3_base)}")
    else:
        t_v3_base = 0.0

    # ── Monthly Returns ──
    monthly_all = {}
    for vk in variants:
        vr = results[vk]['strat_ret'][oos_mask]
        monthly_all[vk] = (1 + vr).resample('ME').prod() - 1

    # ── Generate Report ──
    print()
    print("=" * 74)
    print("GENERATING REPORT")
    print("=" * 74)

    lines = []
    lines.append("# DVOL Rate-of-Change Overlay Results")
    lines.append("")
    lines.append(f"**Run date**: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"**IS period**: {is_start} to {IS_END}")
    lines.append(f"**OOS period**: {OOS_START} to latest")
    lines.append(f"**DVOL source**: Deribit BTC DVOL")
    lines.append(f"**Base signal**: EMA 20/50 crossover (actual s320 base)")
    lines.append(f"**Rebalancing**: Weekly")
    lines.append(f"**Transaction cost**: {COST_BPS} bps round-trip")
    lines.append("")

    # ── Architecture ──
    lines.append("## Architecture")
    lines.append("")
    lines.append("V3 (s320) stack:")
    lines.append("- **Base**: EMA 20/50 crossover (long when fast > slow)")
    lines.append("- **Positioning overlay**: Top Trader L/S + L/S Divergence z-score -> 0.3x to 1.5x")
    lines.append("- **VRP overlay**: (IV - RV) z-score -> 0.3x to 1.3x (vol LEVEL)")
    lines.append("")
    lines.append("NEW candidate overlay:")
    lines.append("- **DVOL ROC overlay**: 20d rate of change of DVOL, z-scored over 60d")
    lines.append("  - z > 1.0 -> 1.3x (IV rising fast = bullish reflexive vol)")
    lines.append("  - 0.0 < z < 1.0 -> 1.1x (IV rising moderately)")
    lines.append("  - -0.5 < z < 0.0 -> 1.0x (neutral)")
    lines.append("  - z < -0.5 -> 0.7x (IV falling = less conviction)")
    lines.append("  - z < -1.5 -> 0.5x (IV collapsing = risk-off)")
    lines.append("")
    lines.append("Rationale: DVOL ROC measures the RATE OF CHANGE of implied vol, which is")
    lines.append("orthogonal to VRP that measures the LEVEL of vol premium (IV - RV). In crypto,")
    lines.append("rising IV correlates with rising price (reflexive volatility), unlike equities.")
    lines.append("")

    # ── 1. Backtest Comparison Table ──
    lines.append("## 1. Backtest Comparison Table")
    lines.append("")
    lines.append("| Variant | IS Return | OOS Return | IS Sharpe | OOS Sharpe | IS MaxDD | OOS MaxDD | IS Calmar | OOS Calmar | Turnover/yr |")
    lines.append("|---------|-----------|------------|-----------|------------|----------|-----------|-----------|------------|-------------|")

    for vk in ['base_only', 'v3_full', 'v3_dvol_roc']:
        r = results[vk]
        is_m = r['is']
        oos_m = r['oos']
        line = (f"| {r['name']} | {is_m['ann_return']:.1%} | {oos_m['ann_return']:.1%} "
                f"| {is_m['sharpe']:.2f} | {oos_m['sharpe']:.2f} "
                f"| {is_m['max_dd']:.1%} | {oos_m['max_dd']:.1%} "
                f"| {is_m['calmar']:.2f} | {oos_m['calmar']:.2f} "
                f"| {r['is_turnover']:.1f} |")
        lines.append(line)

    lines.append("")

    # Delta analysis
    base_is = results['base_only']['is']
    base_oos = results['base_only']['oos']
    v3_is = results['v3_full']['is']
    v3_oos = results['v3_full']['oos']
    dvol_is = results['v3_dvol_roc']['is']
    dvol_oos = results['v3_dvol_roc']['oos']

    lines.append("### Delta Analysis")
    lines.append("")
    lines.append("| Comparison | IS dSharpe | OOS dSharpe | IS dReturn | OOS dReturn | IS dMaxDD | OOS dMaxDD |")
    lines.append("|------------|------------|-------------|------------|-------------|-----------|------------|")

    # V3 vs Base
    lines.append(f"| V3 Full vs Base | {v3_is['sharpe']-base_is['sharpe']:+.3f} | {v3_oos['sharpe']-base_oos['sharpe']:+.3f} "
                 f"| {v3_is['ann_return']-base_is['ann_return']:+.1%} | {v3_oos['ann_return']-base_oos['ann_return']:+.1%} "
                 f"| {v3_is['max_dd']-base_is['max_dd']:+.1%} | {v3_oos['max_dd']-base_oos['max_dd']:+.1%} |")

    # V3+DVOL_ROC vs Base
    lines.append(f"| V3+DVOL_ROC vs Base | {dvol_is['sharpe']-base_is['sharpe']:+.3f} | {dvol_oos['sharpe']-base_oos['sharpe']:+.3f} "
                 f"| {dvol_is['ann_return']-base_is['ann_return']:+.1%} | {dvol_oos['ann_return']-base_oos['ann_return']:+.1%} "
                 f"| {dvol_is['max_dd']-base_is['max_dd']:+.1%} | {dvol_oos['max_dd']-base_oos['max_dd']:+.1%} |")

    # V3+DVOL_ROC vs V3 Full (THE KEY COMPARISON)
    lines.append(f"| **V3+DVOL_ROC vs V3 Full** | **{dvol_is['sharpe']-v3_is['sharpe']:+.3f}** | **{dvol_oos['sharpe']-v3_oos['sharpe']:+.3f}** "
                 f"| **{dvol_is['ann_return']-v3_is['ann_return']:+.1%}** | **{dvol_oos['ann_return']-v3_oos['ann_return']:+.1%}** "
                 f"| **{dvol_is['max_dd']-v3_is['max_dd']:+.1%}** | **{dvol_oos['max_dd']-v3_oos['max_dd']:+.1%}** |")

    lines.append("")

    # ── 2. Walk-Forward Results ──
    lines.append("## 2. Walk-Forward Validation")
    lines.append("")
    lines.append("6 rolling windows (6-month train / 6-month test):")
    lines.append("")
    lines.append("| Window | Test Period | V3 Sharpe | V3+DVOL_ROC Sharpe | dSharpe | V3 MaxDD | V3+DVOL_ROC MaxDD | t-stat | Days |")
    lines.append("|--------|-------------|-----------|--------------------|---------|---------:|------------------:|-------:|-----:|")

    for r in wf_results:
        improved = 'Y' if r['d_sharpe'] > 0 else 'N'
        lines.append(f"| {r['window']} | {r['test_start']} to {r['test_end']} "
                     f"| {r['v3_sharpe']:.2f} | {r['dvol_roc_sharpe']:.2f} "
                     f"| {r['d_sharpe']:+.3f} | {r['v3_maxdd']:.1%} "
                     f"| {r['dvol_roc_maxdd']:.1%} | {r['t_stat']:+.2f} | {r['n_days']} |")

    n_improved = sum(1 for r in wf_results if r['improved_sharpe'])
    n_worsened_dd = sum(1 for r in wf_results if r['worsened_dd'])
    avg_d_sharpe = np.mean([r['d_sharpe'] for r in wf_results])
    lines.append("")
    lines.append(f"**Summary**: Improved Sharpe in {n_improved}/{len(wf_results)} windows. "
                 f"Worsened MaxDD in {n_worsened_dd}/{len(wf_results)} windows. "
                 f"Avg dSharpe: {avg_d_sharpe:+.3f}.")
    lines.append("")

    # ── 3. Correlation with Existing Overlays ──
    lines.append("## 3. Correlation with Existing Overlays")
    lines.append("")
    lines.append("Low correlation (< 0.3) = genuinely orthogonal signal = worth adding.")
    lines.append("High correlation (> 0.5) = redundant with VRP = do not add.")
    lines.append("")

    lines.append("### Full Period")
    lines.append(f"- DVOL ROC vs VRP multiplier correlation: {corr_results.get('corr_dvol_vrp_mult', np.nan):.3f}")
    lines.append(f"- DVOL ROC vs Positioning multiplier correlation: {corr_results.get('corr_dvol_pos_mult', np.nan):.3f}")
    lines.append(f"- DVOL ROC z vs VRP z (Spearman): {corr_results.get('corr_z_spearman', np.nan):.3f} (p={corr_results.get('spearman_p', np.nan):.4f})")
    lines.append(f"- Agreement: both reduce {corr_results.get('pct_both_reduce', 0):.1f}%, "
                 f"both boost {corr_results.get('pct_both_boost', 0):.1f}%, "
                 f"disagree {corr_results.get('pct_disagree', 0):.1f}%, "
                 f"both neutral {corr_results.get('pct_both_neutral', 0):.1f}%")
    lines.append("")

    lines.append("### IS Period")
    lines.append(f"- DVOL ROC vs VRP multiplier correlation: {corr_is.get('corr_dvol_vrp_mult', np.nan):.3f}")
    lines.append(f"- DVOL ROC z vs VRP z (Spearman): {corr_is.get('corr_z_spearman', np.nan):.3f}")
    lines.append("")

    lines.append("### OOS Period")
    lines.append(f"- DVOL ROC vs VRP multiplier correlation: {corr_oos.get('corr_dvol_vrp_mult', np.nan):.3f}")
    lines.append(f"- DVOL ROC z vs VRP z (Spearman): {corr_oos.get('corr_z_spearman', np.nan):.3f}")
    lines.append("")

    # Independence verdict
    corr_val = abs(corr_results.get('corr_z_spearman', 1))
    if corr_val < 0.3:
        independence = "ORTHOGONAL (genuinely independent -- worth adding)"
    elif corr_val < 0.5:
        independence = "WEAKLY CORRELATED (some overlap with VRP, but adds information)"
    else:
        independence = "CORRELATED (redundant with VRP -- likely not worth adding)"
    lines.append(f"**Independence verdict**: {independence}")
    lines.append("")

    # ── DVOL ROC IC Results ──
    lines.append("## 4. DVOL ROC Information Coefficient")
    lines.append("")
    lines.append("Does DVOL ROC z-score predict forward returns?")
    lines.append("")
    lines.append("| Target | Full IC | Full t | IS IC | IS t | OOS IC | OOS t |")
    lines.append("|--------|---------|--------|-------|------|--------|-------|")

    for target_name in ['Fwd 1D Return', 'Fwd 7D Return', 'Fwd 14D Return']:
        full_data = ic_full.get(target_name, {})
        is_data = ic_is.get(target_name, {})
        oos_data = ic_oos.get(target_name, {})
        lines.append(f"| {target_name} "
                     f"| {full_data.get('ic', np.nan):+.4f} | {full_data.get('t_stat', np.nan):+.2f} "
                     f"| {is_data.get('ic', np.nan):+.4f} | {is_data.get('t_stat', np.nan):+.2f} "
                     f"| {oos_data.get('ic', np.nan):+.4f} | {oos_data.get('t_stat', np.nan):+.2f} |")

    lines.append("")

    # ── 5. Statistical Significance ──
    lines.append("## 5. Statistical Significance")
    lines.append("")

    oos_days = results['base_only']['oos']['n_days']
    lines.append(f"OOS period: {oos_days} days (~{oos_days/30:.0f} months)")
    lines.append("")

    def sig_label(t):
        if abs(t) > 2.58:
            return 'significant (p<0.01)'
        elif abs(t) > 1.96:
            return 'significant (p<0.05)'
        elif abs(t) > 1.65:
            return 'marginally significant (p<0.10)'
        else:
            return 'not significant'

    lines.append(f"- V3 Full vs Base: t={t_v3_base:.3f} ({sig_label(t_v3_base)})")
    lines.append(f"- V3+DVOL_ROC vs Base: t={t_vs_base:.3f} ({sig_label(t_vs_base)})")
    lines.append(f"- **V3+DVOL_ROC vs V3 Full (marginal): t={t_marginal:.3f} ({sig_label(t_marginal)})**")
    lines.append("")

    # ── Monthly OOS Returns ──
    lines.append("## 6. Monthly Returns (OOS)")
    lines.append("")
    lines.append("| Month | Base Only | V3 Full | V3+DVOL_ROC | DVOL_ROC Delta |")
    lines.append("|-------|-----------|---------|-------------|----------------|")

    all_months = sorted(set(monthly_all['base_only'].index))
    for dt in all_months:
        parts = [f"| {dt.strftime('%Y-%m')}"]
        for vk in ['base_only', 'v3_full', 'v3_dvol_roc']:
            if dt in monthly_all[vk].index:
                parts.append(f"{monthly_all[vk].loc[dt]:.2%}")
            else:
                parts.append("N/A")
        # Delta column
        if dt in monthly_all['v3_dvol_roc'].index and dt in monthly_all['v3_full'].index:
            delta = monthly_all['v3_dvol_roc'].loc[dt] - monthly_all['v3_full'].loc[dt]
            parts.append(f"{delta:+.2%}")
        else:
            parts.append("N/A")
        lines.append(" | ".join(parts) + " |")

    # Cumulative
    parts = ["| **Cumulative**"]
    for vk in ['base_only', 'v3_full', 'v3_dvol_roc']:
        cum = (1 + results[vk]['strat_ret'][oos_mask].dropna()).cumprod().iloc[-1] - 1
        parts.append(f"**{cum:.2%}**")
    cum_delta = ((1 + results['v3_dvol_roc']['strat_ret'][oos_mask].dropna()).cumprod().iloc[-1] -
                 (1 + results['v3_full']['strat_ret'][oos_mask].dropna()).cumprod().iloc[-1])
    parts.append(f"**{cum_delta:+.2%}**")
    lines.append(" | ".join(parts) + " |")
    lines.append("")

    # ── 7. DVOL ROC Signal Distribution ──
    lines.append("## 7. DVOL ROC Signal Summary Statistics")
    lines.append("")
    roc_common = dvol_roc_raw.dropna()
    lines.append(f"- DVOL ROC(20d) mean: {roc_common.mean():.4f}")
    lines.append(f"- DVOL ROC(20d) median: {roc_common.median():.4f}")
    lines.append(f"- DVOL ROC(20d) std: {roc_common.std():.4f}")
    lines.append(f"- DVOL ROC(20d) min: {roc_common.min():.4f}")
    lines.append(f"- DVOL ROC(20d) max: {roc_common.max():.4f}")
    lines.append(f"- DVOL ROC(20d) % positive: {(roc_common > 0).mean()*100:.1f}%")
    lines.append(f"- DVOL ROC z-score mean: {dvol_roc_z.dropna().mean():.3f}")
    lines.append(f"- DVOL ROC z-score std: {dvol_roc_z.dropna().std():.3f}")
    lines.append("")

    # Position distribution for each variant
    lines.append("### Position Distribution (% of time at each level)")
    lines.append("")
    pos_bins = [-0.01, 0.15, 0.35, 0.55, 0.85, 1.05, 1.20, 1.55]
    pos_labels = ['~0.0', '~0.3', '~0.5', '~0.7', '~1.0', '~1.1-1.3', '~1.5']
    lines.append("| Variant | " + " | ".join(pos_labels) + " |")
    lines.append("|---------|" + "|".join(["------" for _ in pos_labels]) + "|")

    for vk in ['base_only', 'v3_full', 'v3_dvol_roc']:
        hp = results[vk]['held_pos'][(btc_daily.index >= is_start)]
        hp_binned = pd.cut(hp, bins=pos_bins, labels=pos_labels, include_lowest=True)
        dist = hp_binned.value_counts(normalize=True).reindex(pos_labels, fill_value=0)
        parts = [f"| {results[vk]['name']}"]
        for lbl in pos_labels:
            parts.append(f"{dist[lbl]:.1%}")
        lines.append(" | ".join(parts) + " |")

    lines.append("")

    # ── 8. VERDICT ──
    lines.append("## 8. Verdict")
    lines.append("")

    # Key metrics for decision
    marginal_oos_dsharpe = dvol_oos['sharpe'] - v3_oos['sharpe']
    marginal_is_dsharpe = dvol_is['sharpe'] - v3_is['sharpe']
    oos_dd_improvement = dvol_oos['max_dd'] - v3_oos['max_dd']  # positive = less drawdown
    wf_win_rate = n_improved / max(len(wf_results), 1)
    corr_with_vrp = abs(corr_results.get('corr_z_spearman', 1))

    lines.append("### Decision Inputs")
    lines.append("")
    lines.append(f"- Marginal OOS dSharpe (V3+DVOL_ROC vs V3): {marginal_oos_dsharpe:+.3f}")
    lines.append(f"- Marginal IS dSharpe (V3+DVOL_ROC vs V3): {marginal_is_dsharpe:+.3f}")
    lines.append(f"- OOS MaxDD improvement: {oos_dd_improvement:+.1%}")
    lines.append(f"- Walk-forward win rate: {n_improved}/{len(wf_results)} ({100*wf_win_rate:.0f}%)")
    lines.append(f"- Walk-forward worsened MaxDD: {n_worsened_dd}/{len(wf_results)}")
    lines.append(f"- Walk-forward avg dSharpe: {avg_d_sharpe:+.3f}")
    lines.append(f"- Correlation with VRP (Spearman z): {corr_results.get('corr_z_spearman', np.nan):.3f}")
    lines.append(f"- Marginal t-stat: {t_marginal:.3f}")
    lines.append(f"- IS/OOS Sharpe ratio (DVOL ROC): IS={dvol_is['sharpe']:.2f}, OOS={dvol_oos['sharpe']:.2f}")
    lines.append("")

    # Decision logic
    # Strong ADD: marginal OOS dSharpe > 0.1, low correlation, WF win rate > 50%, no DD worsening
    # ADD: marginal OOS dSharpe > 0.05, low/moderate correlation, WF win rate >= 50%
    # NEEDS_MORE_DATA: promising but ambiguous
    # KILL: negative marginal, high correlation, or worsens DD

    if (marginal_oos_dsharpe > 0.1 and corr_with_vrp < 0.3
            and wf_win_rate > 0.5 and n_worsened_dd <= len(wf_results) // 2):
        verdict = "ADD"
        reasoning = (
            f"DVOL ROC provides strong marginal improvement (OOS dSharpe {marginal_oos_dsharpe:+.3f}) "
            f"with low correlation to existing VRP (rho={corr_results.get('corr_z_spearman', np.nan):.3f}). "
            f"Walk-forward confirms improvement in {n_improved}/{len(wf_results)} windows. "
            f"The signal captures different information: rate-of-change of IV vs level of VRP."
        )
    elif (marginal_oos_dsharpe > 0.05 and corr_with_vrp < 0.5
            and wf_win_rate >= 0.5):
        verdict = "ADD"
        reasoning = (
            f"DVOL ROC provides meaningful marginal improvement (OOS dSharpe {marginal_oos_dsharpe:+.3f}) "
            f"with acceptable correlation to VRP (rho={corr_results.get('corr_z_spearman', np.nan):.3f}). "
            f"Walk-forward win rate: {n_improved}/{len(wf_results)}. Signal adds genuine information."
        )
    elif marginal_oos_dsharpe > 0.0 and corr_with_vrp < 0.5:
        verdict = "NEEDS_MORE_DATA"
        reasoning = (
            f"DVOL ROC shows positive but small marginal improvement (OOS dSharpe {marginal_oos_dsharpe:+.3f}). "
            f"Correlation with VRP is acceptable (rho={corr_results.get('corr_z_spearman', np.nan):.3f}). "
            f"Walk-forward win rate: {n_improved}/{len(wf_results)}. "
            f"Need more OOS data to confirm the signal is reliable."
        )
    elif corr_with_vrp >= 0.5:
        verdict = "KILL"
        reasoning = (
            f"DVOL ROC is too correlated with existing VRP overlay "
            f"(rho={corr_results.get('corr_z_spearman', np.nan):.3f}). "
            f"Adding it would be redundant and increase complexity without diversification."
        )
    else:
        verdict = "KILL"
        reasoning = (
            f"DVOL ROC does not improve V3 out-of-sample (marginal OOS dSharpe {marginal_oos_dsharpe:+.3f}). "
            f"Walk-forward win rate: {n_improved}/{len(wf_results)}. Signal does not add value."
        )

    # Check for overfitting signal
    is_oos_gap = marginal_is_dsharpe - marginal_oos_dsharpe
    if is_oos_gap > 0.2 and verdict == "ADD":
        verdict = "NEEDS_MORE_DATA"
        reasoning += (f" CAUTION: IS improvement ({marginal_is_dsharpe:+.3f}) much larger than "
                      f"OOS ({marginal_oos_dsharpe:+.3f}), suggesting possible overfitting.")

    lines.append(f"### **{verdict}**")
    lines.append("")
    lines.append(reasoning)
    lines.append("")

    if verdict == "ADD":
        lines.append("### Recommended Multiplier Thresholds")
        lines.append("")
        lines.append("Based on the backtest results, the following thresholds are recommended:")
        lines.append("")
        lines.append("| DVOL ROC Z-Score | Multiplier | Interpretation |")
        lines.append("|------------------|------------|----------------|")
        lines.append("| z > 1.0 | 1.3x | IV rising fast -- bullish reflexive vol |")
        lines.append("| 0.0 < z < 1.0 | 1.1x | IV rising moderately |")
        lines.append("| -0.5 < z < 0.0 | 1.0x | Neutral |")
        lines.append("| -1.5 < z < -0.5 | 0.7x | IV falling -- less conviction |")
        lines.append("| z < -1.5 | 0.5x | IV collapsing -- risk-off |")
        lines.append("")
        lines.append("Integration into s320:")
        lines.append("```python")
        lines.append("final_position = base * pos_mult * vrp_mult * dvol_roc_mult")
        lines.append("final_position = np.clip(final_position, 0.0, 1.5)")
        lines.append("```")
        lines.append("")

    # ── Appendix: Equity Curve ──
    lines.append("## Appendix: Quarterly Equity Curve (normalized to 1.0)")
    lines.append("")
    lines.append("| Date | Base Only | V3 Full | V3+DVOL_ROC |")
    lines.append("|------|-----------|---------|-------------|")

    for vk in results:
        cum = (1 + results[vk]['strat_ret'].fillna(0)).cumprod()
        results[vk]['_cum'] = cum

    all_dates = btc_daily.index[(btc_daily.index >= is_start)]
    quarterly = all_dates.to_series().resample('QE').last().dropna()
    for dt in quarterly:
        parts = [f"| {dt.strftime('%Y-%m-%d')}"]
        for vk in ['base_only', 'v3_full', 'v3_dvol_roc']:
            c = results[vk]['_cum']
            if dt in c.index:
                parts.append(f"{c.loc[dt]:.3f}")
            else:
                idx = c.index.get_indexer([dt], method='ffill')[0]
                if idx >= 0:
                    parts.append(f"{c.iloc[idx]:.3f}")
                else:
                    parts.append("N/A")
        lines.append(" | ".join(parts) + " |")

    lines.append("")

    # Write report
    report_path = OUTPUT_DIR / 'dvol_roc_overlay_results.md'
    with open(report_path, 'w') as f:
        f.write('\n'.join(lines))
    print(f"\nReport saved to: {report_path}")

    # Console summary
    print("\n" + "=" * 74)
    print("SUMMARY")
    print("=" * 74)
    print(f"\nBase OOS Sharpe:              {results['base_only']['oos']['sharpe']:.2f}")
    print(f"V3 Full OOS Sharpe:           {results['v3_full']['oos']['sharpe']:.2f}")
    print(f"V3+DVOL_ROC OOS Sharpe:       {results['v3_dvol_roc']['oos']['sharpe']:.2f}")
    print(f"\nMarginal DVOL ROC dSharpe:    {marginal_oos_dsharpe:+.3f}")
    print(f"Marginal t-stat:              {t_marginal:.3f}")
    print(f"DVOL ROC vs VRP correlation:  {corr_results.get('corr_z_spearman', np.nan):.3f}")
    print(f"Walk-forward win rate:        {n_improved}/{len(wf_results)}")
    print(f"\nVerdict: {verdict}")
    print(f"Reasoning: {reasoning}")


if __name__ == '__main__':
    main()
