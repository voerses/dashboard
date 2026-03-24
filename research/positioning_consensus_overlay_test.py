#!/workspace/venv/bin/python
"""
Cross-Token Positioning Consensus Overlay Test
===============================================

Tests whether cross-token positioning consensus (mean top trader L/S across
all 29 tokens) adds residual information beyond BTC-specific positioning
when used as a sizing overlay on the V3 (s320) trend-following strategy.

Key question: Does the market-wide positioning consensus provide signal
that is NOT already captured by BTC-specific positioning?

Variants:
  1. V3 Base Only (50/200 SMA trend following)
  2. V3 + BTC positioning + VRP (current s320 baseline)
  3. V3 + BTC positioning + VRP + cross-token consensus (candidate ADD)
  4. V3 + cross-token consensus only (candidate REPLACE)

Tests:
  - Residual value: correlation between consensus z and BTC-specific z
  - Walk-forward: 6 rolling windows, majority must improve
  - MaxDD check: must not worsen any window's MaxDD
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

COST_BPS = 10
IS_END = '2024-12-31'
OOS_START = '2025-01-01'

# ============================================================================
# 1. DATA LOADING
# ============================================================================

def load_btc_daily():
    """Load BTC spot 1H data, resample to daily."""
    print("[1/6] Loading BTC spot data...")
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


def load_btc_positioning():
    """Load BTC-specific positioning data."""
    print("[2/6] Loading BTC positioning data...")
    pos = pd.read_parquet(DATA_DIR / 'alternative/binance_metrics/all_symbols_daily_ls.parquet')
    pos = pos[pos['symbol'] == 'BTCUSDT'].copy()
    pos['date'] = pd.to_datetime(pos['date'])
    pos = pos.set_index('date').sort_index()
    pos = pos[['sum_toptrader_ls_ratio', 'count_toptrader_ls_ratio', 'count_ls_ratio']].copy()
    pos = pos[~pos.index.duplicated(keep='last')]
    print(f"  BTC positioning: {pos.index.min().date()} to {pos.index.max().date()}, {len(pos)} rows")
    return pos


def load_cross_token_panel():
    """Load all-symbols panel and compute cross-token consensus."""
    print("[3/6] Loading cross-token panel (29 symbols)...")
    panel = pd.read_parquet(DATA_DIR / 'alternative/binance_metrics/all_symbols_daily_ls.parquet')
    panel['date'] = pd.to_datetime(panel['date'])
    n_symbols = panel['symbol'].nunique()
    print(f"  Panel: {len(panel)} rows, {n_symbols} symbols")
    print(f"  Date range: {panel['date'].min().date()} to {panel['date'].max().date()}")

    # Pivot to wide format and compute daily cross-token mean
    pivot = panel.pivot_table(index='date', columns='symbol', values='sum_toptrader_ls_ratio')
    pivot = pivot.sort_index()

    # Cross-token consensus: mean across all tokens each day
    consensus = pivot.mean(axis=1)
    consensus.name = 'cross_token_consensus'

    # Count how many tokens contribute each day
    n_tokens = pivot.notna().sum(axis=1)
    print(f"  Tokens per day: min={n_tokens.min()}, median={n_tokens.median():.0f}, max={n_tokens.max()}")
    print(f"  Consensus stats: mean={consensus.mean():.4f}, std={consensus.std():.4f}")

    return consensus, pivot


def load_dvol():
    """Load BTC DVOL from Deribit JSON."""
    print("[4/6] Loading BTC DVOL...")
    dvol_path = DATA_DIR / 'alternative/deribit_options/dvol/btc_dvol_daily.json'
    if not dvol_path.exists():
        print("  WARNING: DVOL file not found. Will use RV proxy.")
        return pd.Series(dtype=float)
    with open(dvol_path) as f:
        data = json.load(f)
    records = [{'date': pd.Timestamp(row[0], unit='ms'), 'dvol_close': row[4]} for row in data]
    dvol = pd.DataFrame(records).set_index('date').sort_index()
    dvol = dvol[~dvol.index.duplicated(keep='last')]
    print(f"  DVOL: {dvol.index.min().date()} to {dvol.index.max().date()}, {len(dvol)} rows")
    return dvol['dvol_close']


# ============================================================================
# 2. SIGNAL CONSTRUCTION
# ============================================================================

def build_trend_signal(btc_daily):
    """Base trend signal: long when close > 50d SMA AND > 200d SMA. Hysteresis exit on 50 SMA."""
    print("[5/6] Building trend signal (50/200 SMA)...")
    sma50 = btc_daily['close'].rolling(50, min_periods=50).mean()
    sma200 = btc_daily['close'].rolling(200, min_periods=200).mean()
    position = pd.Series(0.0, index=btc_daily.index)
    in_position = False
    for i in range(len(btc_daily)):
        close = btc_daily['close'].iloc[i]
        s50 = sma50.iloc[i]
        s200 = sma200.iloc[i]
        if pd.isna(s50) or pd.isna(s200):
            continue
        if not in_position:
            if close > s50 and close > s200:
                in_position = True
                position.iloc[i] = 1.0
        else:
            if close < s50:
                in_position = False
            else:
                position.iloc[i] = 1.0
    print(f"  Trend signal: {position.sum():.0f} days long out of {len(position)} ({100*position.mean():.1f}%)")
    return position


def rolling_zscore(s, window=30, min_periods=15):
    """Rolling z-score."""
    mu = s.rolling(window, min_periods=min_periods).mean()
    sigma = s.rolling(window, min_periods=min_periods).std()
    return (s - mu) / sigma.replace(0, np.nan)


def build_btc_positioning_signal(btc_daily, positioning):
    """BTC-specific positioning overlay (from existing s320/R60)."""
    print("[6a/6] Building BTC positioning signal...")
    pos = positioning.reindex(btc_daily.index).ffill()

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

    pos_mult = combined_z.apply(z_to_multiplier)
    print(f"  BTC positioning multiplier dist:")
    for val in [0.3, 0.5, 1.0, 1.3, 1.5]:
        pct = (pos_mult == val).mean() * 100
        print(f"    {val}x: {pct:.1f}%")
    return pos_mult, combined_z


def build_vrp_signal(btc_daily, dvol_series):
    """VRP sizing overlay (from R61/VRP test)."""
    print("[6b/6] Building VRP signal...")
    log_ret = np.log(btc_daily['close'] / btc_daily['close'].shift(1))
    rv_20d = log_ret.rolling(20, min_periods=15).std() * np.sqrt(365) * 100

    if dvol_series.empty or len(dvol_series) < 30:
        print("  WARNING: DVOL insufficient, using RV proxy")
        iv = log_ret.rolling(90, min_periods=60).std() * np.sqrt(365) * 100 * 1.2
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
    print(f"  VRP multiplier dist:")
    for val in [0.3, 0.5, 1.0, 1.3]:
        pct = (vrp_mult == val).mean() * 100
        print(f"    {val}x: {pct:.1f}%")
    return vrp_mult, vrp_z


def build_consensus_signal(btc_daily, consensus_raw):
    """Cross-token positioning consensus overlay (NEW SIGNAL UNDER TEST)."""
    print("[6c/6] Building cross-token consensus signal...")
    consensus = consensus_raw.reindex(btc_daily.index).ffill()

    # 30d rolling z-score of the cross-token consensus
    consensus_z = rolling_zscore(consensus, window=30, min_periods=15)

    # Contrarian multiplier: high consensus = danger, low = opportunity
    def consensus_z_to_mult(z):
        if pd.isna(z):
            return 1.0
        if z > 1.5:
            return 0.3   # everyone long = danger
        elif z > 0.5:
            return 0.5
        elif z > -0.5:
            return 1.0   # neutral
        elif z > -1.5:
            return 1.3   # low consensus = opportunity
        else:
            return 1.5   # extreme pessimism = contrarian buy

    consensus_mult = consensus_z.apply(consensus_z_to_mult)
    print(f"  Consensus z-score stats: mean={consensus_z.dropna().mean():.3f}, std={consensus_z.dropna().std():.3f}")
    print(f"  Consensus multiplier dist:")
    for val in [0.3, 0.5, 1.0, 1.3, 1.5]:
        pct = (consensus_mult == val).mean() * 100
        print(f"    {val}x: {pct:.1f}%")
    return consensus_mult, consensus_z


# ============================================================================
# 3. BACKTEST ENGINE
# ============================================================================

def compute_final_position(base_pos, pos_mult, vrp_mult, consensus_mult, variant):
    """Compute final position for each variant."""
    if variant == 'base_only':
        final = base_pos.copy()
    elif variant == 'base_pos_vrp':
        final = base_pos * pos_mult * vrp_mult
    elif variant == 'base_pos_vrp_consensus':
        final = base_pos * pos_mult * vrp_mult * consensus_mult
    elif variant == 'base_consensus_only':
        final = base_pos * consensus_mult
    else:
        raise ValueError(f"Unknown variant: {variant}")
    return final.clip(0, 2.0)


def run_backtest(btc_daily, final_position, cost_bps=COST_BPS):
    """Run backtest with weekly rebalancing and transaction costs."""
    daily_ret = btc_daily['close'].pct_change()

    # Weekly rebalance dates
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
    """Compute performance metrics."""
    returns = returns.dropna()
    if len(returns) < 5:
        return {'label': label, 'total_return': np.nan, 'ann_return': np.nan,
                'ann_vol': np.nan, 'sharpe': np.nan, 'max_dd': np.nan, 'calmar': np.nan, 'n_days': 0}

    total_ret = (1 + returns).prod() - 1
    n_years = len(returns) / 252
    ann_ret = (1 + total_ret) ** (1 / max(n_years, 0.01)) - 1
    ann_vol = returns.std() * np.sqrt(252)
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0

    cum = (1 + returns).cumprod()
    peak = cum.cummax()
    dd = (cum - peak) / peak
    max_dd = dd.min()
    calmar = ann_ret / abs(max_dd) if max_dd != 0 else 0

    return {
        'label': label, 'total_return': total_ret, 'ann_return': ann_ret,
        'ann_vol': ann_vol, 'sharpe': sharpe, 'max_dd': max_dd,
        'calmar': calmar, 'n_days': len(returns),
    }


# ============================================================================
# 4. WALK-FORWARD ENGINE
# ============================================================================

def walk_forward_test(btc_daily, base_pos, pos_mult, vrp_mult, consensus_mult, n_windows=6):
    """Rolling walk-forward: split data into n_windows, train on expanding, test on next window."""
    print("\n" + "=" * 70)
    print(f"WALK-FORWARD TEST ({n_windows} windows)")
    print("=" * 70)

    # Use data from 2021-01-01 onwards (enough for 200d warmup)
    valid_start = '2021-01-01'
    valid_idx = btc_daily.index[btc_daily.index >= valid_start]
    n_days = len(valid_idx)
    window_size = n_days // n_windows

    results = []
    for w in range(n_windows):
        w_start = valid_idx[w * window_size]
        w_end = valid_idx[min((w + 1) * window_size - 1, n_days - 1)]

        # Mask for this window
        mask = (btc_daily.index >= w_start) & (btc_daily.index <= w_end)

        window_results = {}
        for variant, label in [
            ('base_only', 'Base'),
            ('base_pos_vrp', 'Base+Pos+VRP'),
            ('base_pos_vrp_consensus', 'Base+Pos+VRP+Cons'),
            ('base_consensus_only', 'Base+ConsOnly'),
        ]:
            final_pos = compute_final_position(base_pos, pos_mult, vrp_mult, consensus_mult, variant)
            strat_ret, _, _ = run_backtest(btc_daily, final_pos, cost_bps=COST_BPS)
            window_ret = strat_ret[mask]
            m = compute_metrics(window_ret, f"{label} W{w+1}")
            window_results[variant] = m

        results.append({
            'window': w + 1,
            'start': w_start.strftime('%Y-%m-%d'),
            'end': w_end.strftime('%Y-%m-%d'),
            'n_days': mask.sum(),
            'metrics': window_results,
        })

        # Print summary
        base_s = window_results['base_pos_vrp']['sharpe']
        cand_s = window_results['base_pos_vrp_consensus']['sharpe']
        delta = cand_s - base_s if not np.isnan(cand_s) and not np.isnan(base_s) else np.nan
        print(f"  W{w+1} [{w_start.strftime('%Y-%m-%d')} to {w_end.strftime('%Y-%m-%d')}]: "
              f"s320 Sharpe={base_s:.2f}, +Consensus Sharpe={cand_s:.2f}, delta={delta:+.3f}")

    return results


# ============================================================================
# 5. MAIN EXECUTION
# ============================================================================

def main():
    print("=" * 70)
    print("CROSS-TOKEN POSITIONING CONSENSUS OVERLAY TEST")
    print("Does consensus add beyond BTC-specific positioning in s320?")
    print("=" * 70)
    print()

    # ── Load data ──
    btc_daily = load_btc_daily()
    btc_positioning = load_btc_positioning()
    consensus_raw, panel_pivot = load_cross_token_panel()
    dvol = load_dvol()
    print()

    # ── Build signals ──
    base_position = build_trend_signal(btc_daily)
    btc_pos_mult, btc_pos_z = build_btc_positioning_signal(btc_daily, btc_positioning)
    vrp_mult, vrp_z = build_vrp_signal(btc_daily, dvol)
    consensus_mult, consensus_z = build_consensus_signal(btc_daily, consensus_raw)
    print()

    # =========================================================================
    # TEST 1: RESIDUAL VALUE — Correlation analysis
    # =========================================================================
    print("=" * 70)
    print("TEST 1: RESIDUAL VALUE — Correlation between signals")
    print("=" * 70)

    # Align all z-scores
    aligned = pd.DataFrame({
        'btc_pos_z': btc_pos_z,
        'consensus_z': consensus_z,
        'vrp_z': vrp_z,
    }).dropna()

    corr_btc_cons = aligned['btc_pos_z'].corr(aligned['consensus_z'])
    corr_btc_vrp = aligned['btc_pos_z'].corr(aligned['vrp_z'])
    corr_cons_vrp = aligned['consensus_z'].corr(aligned['vrp_z'])

    # Spearman rank correlations
    spearman_btc_cons = aligned['btc_pos_z'].corr(aligned['consensus_z'], method='spearman')
    spearman_btc_vrp = aligned['btc_pos_z'].corr(aligned['vrp_z'], method='spearman')
    spearman_cons_vrp = aligned['consensus_z'].corr(aligned['vrp_z'], method='spearman')

    print(f"\n  Pearson Correlations (N={len(aligned)}):")
    print(f"    BTC positioning z vs Consensus z:    r = {corr_btc_cons:.3f}")
    print(f"    BTC positioning z vs VRP z:          r = {corr_btc_vrp:.3f}")
    print(f"    Consensus z vs VRP z:                r = {corr_cons_vrp:.3f}")
    print(f"\n  Spearman Correlations:")
    print(f"    BTC positioning z vs Consensus z:    rho = {spearman_btc_cons:.3f}")
    print(f"    BTC positioning z vs VRP z:          rho = {spearman_btc_vrp:.3f}")
    print(f"    Consensus z vs VRP z:                rho = {spearman_cons_vrp:.3f}")

    # Rolling correlation (90d) to check stability
    roll_corr = aligned['btc_pos_z'].rolling(90).corr(aligned['consensus_z'])
    print(f"\n  Rolling 90d correlation (BTC pos z vs Consensus z):")
    print(f"    Mean: {roll_corr.mean():.3f}, Std: {roll_corr.std():.3f}")
    print(f"    Min: {roll_corr.min():.3f}, Max: {roll_corr.max():.3f}")

    redundancy_verdict = "REDUNDANT" if abs(corr_btc_cons) > 0.7 else "INDEPENDENT" if abs(corr_btc_cons) < 0.3 else "PARTIALLY INDEPENDENT"
    print(f"\n  VERDICT: {redundancy_verdict} (r={corr_btc_cons:.3f}, threshold: >0.7 = redundant)")

    # IS/OOS correlation stability
    is_mask = aligned.index <= IS_END
    oos_mask = aligned.index >= OOS_START
    corr_is = aligned.loc[is_mask, 'btc_pos_z'].corr(aligned.loc[is_mask, 'consensus_z'])
    corr_oos = aligned.loc[oos_mask, 'btc_pos_z'].corr(aligned.loc[oos_mask, 'consensus_z'])
    print(f"  IS correlation: {corr_is:.3f}, OOS correlation: {corr_oos:.3f}")

    # =========================================================================
    # TEST 2: BACKTEST COMPARISON
    # =========================================================================
    print("\n" + "=" * 70)
    print("TEST 2: BACKTEST COMPARISON (IS/OOS)")
    print("=" * 70)

    variants = {
        'base_only': 'V3 Base Only',
        'base_pos_vrp': 'V3 + BTC Pos + VRP (s320)',
        'base_pos_vrp_consensus': 'V3 + BTC Pos + VRP + Consensus',
        'base_consensus_only': 'V3 + Consensus Only',
    }

    results = {}
    for vk, vname in variants.items():
        final_pos = compute_final_position(base_position, btc_pos_mult, vrp_mult, consensus_mult, vk)
        strat_ret, held_pos, costs = run_backtest(btc_daily, final_pos)

        is_mask = (btc_daily.index >= '2021-06-01') & (btc_daily.index <= IS_END)
        oos_mask = btc_daily.index >= OOS_START

        is_ret = strat_ret[is_mask]
        oos_ret = strat_ret[oos_mask]

        is_metrics = compute_metrics(is_ret, f"{vname} (IS)")
        oos_metrics = compute_metrics(oos_ret, f"{vname} (OOS)")

        results[vk] = {
            'name': vname,
            'is': is_metrics,
            'oos': oos_metrics,
            'strat_ret': strat_ret,
            'held_pos': held_pos,
        }

        print(f"  {vname}:")
        print(f"    IS:  Sharpe={is_metrics['sharpe']:.3f}, Return={is_metrics['ann_return']:.1%}, MaxDD={is_metrics['max_dd']:.1%}")
        print(f"    OOS: Sharpe={oos_metrics['sharpe']:.3f}, Return={oos_metrics['ann_return']:.1%}, MaxDD={oos_metrics['max_dd']:.1%}")

    # =========================================================================
    # TEST 3: WALK-FORWARD
    # =========================================================================
    wf_results = walk_forward_test(btc_daily, base_position, btc_pos_mult, vrp_mult, consensus_mult)

    # Count wins: windows where +Consensus beats s320
    n_wins = 0
    n_valid = 0
    n_dd_worse = 0
    for w in wf_results:
        s320_sharpe = w['metrics']['base_pos_vrp']['sharpe']
        cand_sharpe = w['metrics']['base_pos_vrp_consensus']['sharpe']
        s320_dd = w['metrics']['base_pos_vrp']['max_dd']
        cand_dd = w['metrics']['base_pos_vrp_consensus']['max_dd']

        if not np.isnan(s320_sharpe) and not np.isnan(cand_sharpe):
            n_valid += 1
            if cand_sharpe > s320_sharpe:
                n_wins += 1
            if not np.isnan(cand_dd) and not np.isnan(s320_dd):
                if cand_dd < s320_dd - 0.01:  # worse DD by >1%
                    n_dd_worse += 1

    wf_win_rate = n_wins / max(n_valid, 1)
    print(f"\n  Walk-forward summary: {n_wins}/{n_valid} windows improve ({100*wf_win_rate:.0f}%)")
    print(f"  MaxDD worsened in {n_dd_worse}/{n_valid} windows")

    # =========================================================================
    # TEST 4: INCREMENTAL IC ANALYSIS
    # =========================================================================
    print("\n" + "=" * 70)
    print("TEST 4: INCREMENTAL IC — Does consensus predict after controlling for BTC pos?")
    print("=" * 70)

    # Forward returns
    btc_fwd_7d = btc_daily['close'].pct_change(7).shift(-7)
    btc_fwd_14d = btc_daily['close'].pct_change(14).shift(-14)

    signal_df = pd.DataFrame({
        'btc_pos_z': btc_pos_z,
        'consensus_z': consensus_z,
        'fwd_7d': btc_fwd_7d,
        'fwd_14d': btc_fwd_14d,
    }).dropna()

    for horizon, col in [(7, 'fwd_7d'), (14, 'fwd_14d')]:
        sub = signal_df[['btc_pos_z', 'consensus_z', col]].dropna()

        # Univariate ICs
        ic_btc = sub['btc_pos_z'].corr(sub[col], method='spearman')
        ic_cons = sub['consensus_z'].corr(sub[col], method='spearman')

        # Residual IC: regress consensus_z on btc_pos_z, test residual vs returns
        from numpy.linalg import lstsq
        X = sub['btc_pos_z'].values.reshape(-1, 1)
        X_aug = np.column_stack([X, np.ones(len(X))])
        y = sub['consensus_z'].values
        beta, _, _, _ = lstsq(X_aug, y, rcond=None)
        residual = y - X_aug @ beta
        residual_series = pd.Series(residual, index=sub.index)
        ic_residual = residual_series.corr(sub[col], method='spearman')

        print(f"\n  {horizon}d forward returns (N={len(sub)}):")
        print(f"    BTC positioning z IC:  {ic_btc:.4f}")
        print(f"    Consensus z IC:        {ic_cons:.4f}")
        print(f"    Consensus residual IC: {ic_residual:.4f}  (after partialing out BTC pos)")

        # IS/OOS split
        for period, pmask in [('IS', sub.index <= IS_END), ('OOS', sub.index >= OOS_START)]:
            ps = sub[pmask]
            if len(ps) < 30:
                continue
            ic_btc_p = ps['btc_pos_z'].corr(ps[col], method='spearman')
            ic_cons_p = ps['consensus_z'].corr(ps[col], method='spearman')
            X_p = ps['btc_pos_z'].values.reshape(-1, 1)
            X_p_aug = np.column_stack([X_p, np.ones(len(X_p))])
            y_p = ps['consensus_z'].values
            beta_p, _, _, _ = lstsq(X_p_aug, y_p, rcond=None)
            resid_p = y_p - X_p_aug @ beta_p
            ic_resid_p = pd.Series(resid_p, index=ps.index).corr(ps[col], method='spearman')
            print(f"    {period}: BTC IC={ic_btc_p:.4f}, Cons IC={ic_cons_p:.4f}, Resid IC={ic_resid_p:.4f}")

    # =========================================================================
    # TEST 5: MULTIPLIER AGREEMENT/DISAGREEMENT ANALYSIS
    # =========================================================================
    print("\n" + "=" * 70)
    print("TEST 5: MULTIPLIER AGREEMENT/DISAGREEMENT ANALYSIS")
    print("=" * 70)

    mult_df = pd.DataFrame({
        'btc_pos_mult': btc_pos_mult,
        'consensus_mult': consensus_mult,
    }).dropna()

    # How often do they agree vs disagree?
    agree = (mult_df['btc_pos_mult'] == mult_df['consensus_mult']).mean()
    both_reduce = ((mult_df['btc_pos_mult'] < 1.0) & (mult_df['consensus_mult'] < 1.0)).mean()
    both_boost = ((mult_df['btc_pos_mult'] > 1.0) & (mult_df['consensus_mult'] > 1.0)).mean()
    disagree = ((mult_df['btc_pos_mult'] < 1.0) & (mult_df['consensus_mult'] > 1.0) |
                (mult_df['btc_pos_mult'] > 1.0) & (mult_df['consensus_mult'] < 1.0)).mean()

    print(f"  Exact agreement:    {100*agree:.1f}%")
    print(f"  Both reduce (<1x):  {100*both_reduce:.1f}%")
    print(f"  Both boost (>1x):   {100*both_boost:.1f}%")
    print(f"  Disagree:           {100*disagree:.1f}%")

    # When they disagree, who is right?
    btc_fwd = btc_daily['close'].pct_change(14).shift(-14)
    mult_df['fwd_14d'] = btc_fwd
    mult_df = mult_df.dropna()

    # When BTC pos says reduce but consensus says boost
    btc_bearish_cons_bullish = (mult_df['btc_pos_mult'] < 1.0) & (mult_df['consensus_mult'] > 1.0)
    btc_bullish_cons_bearish = (mult_df['btc_pos_mult'] > 1.0) & (mult_df['consensus_mult'] < 1.0)

    if btc_bearish_cons_bullish.sum() > 10:
        avg_ret = mult_df.loc[btc_bearish_cons_bullish, 'fwd_14d'].mean()
        print(f"\n  When BTC pos REDUCE but consensus BOOST ({btc_bearish_cons_bullish.sum()} days):")
        print(f"    Avg 14d fwd return: {100*avg_ret:.2f}%")
    if btc_bullish_cons_bearish.sum() > 10:
        avg_ret = mult_df.loc[btc_bullish_cons_bearish, 'fwd_14d'].mean()
        print(f"  When BTC pos BOOST but consensus REDUCE ({btc_bullish_cons_bearish.sum()} days):")
        print(f"    Avg 14d fwd return: {100*avg_ret:.2f}%")

    # =========================================================================
    # GENERATE REPORT
    # =========================================================================
    print("\n" + "=" * 70)
    print("GENERATING REPORT")
    print("=" * 70)

    md = []
    md.append("# Cross-Token Positioning Consensus Overlay Results")
    md.append("")
    md.append(f"**Run date:** {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    md.append(f"**IS period:** 2021-06-01 to {IS_END}")
    md.append(f"**OOS period:** {OOS_START} to latest")
    md.append(f"**Transaction cost:** {COST_BPS} bps round-trip")
    md.append(f"**Rebalancing:** Weekly (Monday)")
    md.append("")
    md.append("## Question")
    md.append("")
    md.append("Does cross-token positioning consensus (mean top trader L/S across 29 tokens)")
    md.append("add information beyond BTC-specific positioning as an overlay on V3 (s320)?")
    md.append("")

    # ── 1. Signal Correlation ──
    md.append("## 1. Signal Correlation (Residual Value Test)")
    md.append("")
    md.append("If corr > 0.7, consensus is redundant with BTC-specific positioning.")
    md.append("")
    md.append("| Pair | Pearson r | Spearman rho |")
    md.append("|------|-----------|--------------|")
    md.append(f"| BTC pos z vs Consensus z | {corr_btc_cons:.3f} | {spearman_btc_cons:.3f} |")
    md.append(f"| BTC pos z vs VRP z | {corr_btc_vrp:.3f} | {spearman_btc_vrp:.3f} |")
    md.append(f"| Consensus z vs VRP z | {corr_cons_vrp:.3f} | {spearman_cons_vrp:.3f} |")
    md.append("")
    md.append(f"**Rolling 90d corr (BTC pos vs Consensus):** mean={roll_corr.mean():.3f}, std={roll_corr.std():.3f}, range=[{roll_corr.min():.3f}, {roll_corr.max():.3f}]")
    md.append(f"**IS corr:** {corr_is:.3f}, **OOS corr:** {corr_oos:.3f}")
    md.append("")
    md.append(f"**Verdict:** {redundancy_verdict} (r={corr_btc_cons:.3f})")
    md.append("")

    # ── 2. Backtest Comparison ──
    md.append("## 2. Backtest Comparison")
    md.append("")
    md.append("| Variant | IS Sharpe | OOS Sharpe | IS Return | OOS Return | IS MaxDD | OOS MaxDD | IS Calmar | OOS Calmar |")
    md.append("|---------|-----------|------------|-----------|------------|----------|-----------|-----------|------------|")
    for vk in ['base_only', 'base_pos_vrp', 'base_pos_vrp_consensus', 'base_consensus_only']:
        r = results[vk]
        i = r['is']
        o = r['oos']
        md.append(f"| {r['name']} | {i['sharpe']:.3f} | {o['sharpe']:.3f} | {i['ann_return']:.1%} | {o['ann_return']:.1%} | {i['max_dd']:.1%} | {o['max_dd']:.1%} | {i['calmar']:.2f} | {o['calmar']:.2f} |")
    md.append("")

    # Delta table
    md.append("### Marginal Improvement vs s320 Baseline")
    md.append("")
    md.append("| Metric | +Consensus (ADD) | Consensus Only (REPLACE) |")
    md.append("|--------|------------------|--------------------------|")
    s320 = results['base_pos_vrp']
    add = results['base_pos_vrp_consensus']
    replace = results['base_consensus_only']

    for period, pk in [('IS', 'is'), ('OOS', 'oos')]:
        d_sharpe_add = add[pk]['sharpe'] - s320[pk]['sharpe']
        d_sharpe_rep = replace[pk]['sharpe'] - s320[pk]['sharpe']
        md.append(f"| {period} dSharpe | {d_sharpe_add:+.3f} | {d_sharpe_rep:+.3f} |")

        d_ret_add = add[pk]['ann_return'] - s320[pk]['ann_return']
        d_ret_rep = replace[pk]['ann_return'] - s320[pk]['ann_return']
        md.append(f"| {period} dReturn | {d_ret_add:+.1%} | {d_ret_rep:+.1%} |")

        d_dd_add = add[pk]['max_dd'] - s320[pk]['max_dd']
        d_dd_rep = replace[pk]['max_dd'] - s320[pk]['max_dd']
        md.append(f"| {period} dMaxDD | {d_dd_add:+.1%} | {d_dd_rep:+.1%} |")
    md.append("")

    # ── 3. Walk-Forward Results ──
    md.append("## 3. Walk-Forward Results")
    md.append("")
    md.append(f"**Windows:** {len(wf_results)}")
    md.append(f"**Win rate (consensus improves Sharpe over s320):** {n_wins}/{n_valid} ({100*wf_win_rate:.0f}%)")
    md.append(f"**MaxDD worsened:** {n_dd_worse}/{n_valid} windows")
    md.append("")
    md.append("| Window | Period | s320 Sharpe | +Consensus Sharpe | Delta | s320 MaxDD | +Cons MaxDD | DD Delta |")
    md.append("|--------|--------|-------------|-------------------|-------|------------|-------------|----------|")

    for w in wf_results:
        s = w['metrics']['base_pos_vrp']
        c = w['metrics']['base_pos_vrp_consensus']
        d_sharpe = c['sharpe'] - s['sharpe'] if not np.isnan(c['sharpe']) else np.nan
        d_dd = c['max_dd'] - s['max_dd'] if not np.isnan(c['max_dd']) else np.nan
        md.append(f"| W{w['window']} | {w['start']} to {w['end']} | {s['sharpe']:.3f} | {c['sharpe']:.3f} | {d_sharpe:+.3f} | {s['max_dd']:.1%} | {c['max_dd']:.1%} | {d_dd:+.1%} |")

    md.append("")

    # ── 4. Incremental IC ──
    md.append("## 4. Incremental IC Analysis")
    md.append("")
    md.append("Residual IC = IC of consensus z-score after partialing out BTC positioning z-score.")
    md.append("If residual IC is near zero, consensus adds no information beyond BTC-specific positioning.")
    md.append("")

    # Recompute for table
    md.append("| Horizon | BTC Pos IC | Consensus IC | Residual IC | IS Resid IC | OOS Resid IC |")
    md.append("|---------|-----------|-------------|-------------|-------------|--------------|")

    for horizon, col in [(7, 'fwd_7d'), (14, 'fwd_14d')]:
        sub = signal_df[['btc_pos_z', 'consensus_z', col]].dropna()
        ic_btc = sub['btc_pos_z'].corr(sub[col], method='spearman')
        ic_cons = sub['consensus_z'].corr(sub[col], method='spearman')

        X = sub['btc_pos_z'].values.reshape(-1, 1)
        X_aug = np.column_stack([X, np.ones(len(X))])
        y = sub['consensus_z'].values
        beta, _, _, _ = lstsq(X_aug, y, rcond=None)
        residual = y - X_aug @ beta
        ic_residual = pd.Series(residual, index=sub.index).corr(sub[col], method='spearman')

        # IS/OOS
        ic_resid_is = np.nan
        ic_resid_oos = np.nan
        for period, pmask in [('IS', sub.index <= IS_END), ('OOS', sub.index >= OOS_START)]:
            ps = sub[pmask]
            if len(ps) < 30:
                continue
            X_p = ps['btc_pos_z'].values.reshape(-1, 1)
            X_p_aug = np.column_stack([X_p, np.ones(len(X_p))])
            y_p = ps['consensus_z'].values
            beta_p, _, _, _ = lstsq(X_p_aug, y_p, rcond=None)
            resid_p = y_p - X_p_aug @ beta_p
            ic_r = pd.Series(resid_p, index=ps.index).corr(ps[col], method='spearman')
            if period == 'IS':
                ic_resid_is = ic_r
            else:
                ic_resid_oos = ic_r

        md.append(f"| {horizon}d | {ic_btc:.4f} | {ic_cons:.4f} | {ic_residual:.4f} | {ic_resid_is:.4f} | {ic_resid_oos:.4f} |")
    md.append("")

    # ── 5. Multiplier Agreement ──
    md.append("## 5. Multiplier Agreement/Disagreement")
    md.append("")
    md.append(f"- Exact agreement: {100*agree:.1f}%")
    md.append(f"- Both reduce (<1x): {100*both_reduce:.1f}%")
    md.append(f"- Both boost (>1x): {100*both_boost:.1f}%")
    md.append(f"- Disagree: {100*disagree:.1f}%")
    md.append("")

    if btc_bearish_cons_bullish.sum() > 10:
        avg_ret = mult_df.loc[btc_bearish_cons_bullish, 'fwd_14d'].mean()
        md.append(f"When BTC pos REDUCES but consensus BOOSTS ({btc_bearish_cons_bullish.sum()} days): avg 14d return = {100*avg_ret:.2f}%")
    if btc_bullish_cons_bearish.sum() > 10:
        avg_ret = mult_df.loc[btc_bullish_cons_bearish, 'fwd_14d'].mean()
        md.append(f"When BTC pos BOOSTS but consensus REDUCES ({btc_bullish_cons_bearish.sum()} days): avg 14d return = {100*avg_ret:.2f}%")
    md.append("")

    # ── 6. FINAL VERDICT ──
    md.append("## 6. Final Verdict")
    md.append("")

    # Decision criteria:
    # 1. Correlation < 0.7 (not redundant)
    # 2. OOS Sharpe improvement > 0
    # 3. Walk-forward majority wins (>= 4/6)
    # 4. MaxDD not worsened in any window
    # 5. Residual IC meaningful (|resid IC| > 0.02)

    oos_sharpe_delta = results['base_pos_vrp_consensus']['oos']['sharpe'] - results['base_pos_vrp']['oos']['sharpe']
    is_sharpe_delta = results['base_pos_vrp_consensus']['is']['sharpe'] - results['base_pos_vrp']['is']['sharpe']

    criteria = []
    criteria.append(('Correlation < 0.7 (not redundant)', abs(corr_btc_cons) < 0.7))
    criteria.append(('OOS Sharpe improves', oos_sharpe_delta > 0))
    criteria.append(('Walk-forward majority wins (>=4/6)', n_wins >= 4))
    criteria.append(('MaxDD not worsened in any window', n_dd_worse == 0))
    criteria.append(('IS/OOS directional consistency', np.sign(is_sharpe_delta) == np.sign(oos_sharpe_delta) if not np.isnan(is_sharpe_delta) else False))

    n_pass = sum(1 for _, v in criteria if v)

    md.append("### Decision Criteria")
    md.append("")
    md.append("| Criterion | Result |")
    md.append("|-----------|--------|")
    for name, passed in criteria:
        md.append(f"| {name} | {'PASS' if passed else 'FAIL'} |")
    md.append(f"| **Total** | **{n_pass}/{len(criteria)}** |")
    md.append("")

    # Determine verdict
    if n_pass >= 4 and oos_sharpe_delta > 0.05:
        verdict = "ADD"
        reasoning = (f"Cross-token consensus adds meaningful residual information (corr={corr_btc_cons:.3f}). "
                    f"OOS Sharpe improvement of {oos_sharpe_delta:+.3f} with {n_wins}/{n_valid} walk-forward wins.")
    elif n_pass >= 3 and oos_sharpe_delta > 0:
        verdict = "ADD (MARGINAL)"
        reasoning = (f"Consensus signal shows promise but improvements are modest. "
                    f"OOS Sharpe delta: {oos_sharpe_delta:+.3f}. Walk-forward: {n_wins}/{n_valid} wins. "
                    f"Consider for deployment with close monitoring.")
    elif abs(corr_btc_cons) > 0.7:
        verdict = "KILL"
        reasoning = f"Consensus is redundant with BTC-specific positioning (r={corr_btc_cons:.3f} > 0.7). No incremental value."
    else:
        # Check if REPLACE is better
        replace_oos = results['base_consensus_only']['oos']['sharpe']
        s320_oos = results['base_pos_vrp']['oos']['sharpe']
        if replace_oos > s320_oos + 0.05:
            verdict = "REPLACE"
            reasoning = (f"Consensus alone (Sharpe={replace_oos:.3f}) outperforms full s320 stack (Sharpe={s320_oos:.3f}). "
                        f"Simpler is better.")
        else:
            verdict = "KILL"
            reasoning = (f"Consensus does not improve s320 OOS (delta={oos_sharpe_delta:+.3f}). "
                        f"Walk-forward: {n_wins}/{n_valid} wins. Not enough evidence to add complexity.")

    md.append(f"### Verdict: **{verdict}**")
    md.append("")
    md.append(f"**Reasoning:** {reasoning}")
    md.append("")

    # Key numbers summary
    md.append("### Key Numbers")
    md.append("")
    md.append(f"- BTC pos z / Consensus z correlation: {corr_btc_cons:.3f}")
    md.append(f"- s320 OOS Sharpe: {results['base_pos_vrp']['oos']['sharpe']:.3f}")
    md.append(f"- +Consensus OOS Sharpe: {results['base_pos_vrp_consensus']['oos']['sharpe']:.3f}")
    md.append(f"- Consensus-only OOS Sharpe: {results['base_consensus_only']['oos']['sharpe']:.3f}")
    md.append(f"- Walk-forward win rate: {n_wins}/{n_valid} ({100*wf_win_rate:.0f}%)")
    md.append(f"- MaxDD worsened: {n_dd_worse}/{n_valid} windows")
    md.append("")

    # Write report
    output_path = OUTPUT_DIR / 'positioning_consensus_overlay_results.md'
    with open(output_path, 'w') as f:
        f.write('\n'.join(md))
    print(f"\nReport saved to: {output_path}")

    # Print final summary
    print("\n" + "=" * 70)
    print("FINAL VERDICT")
    print("=" * 70)
    print(f"\n  Verdict: {verdict}")
    print(f"  Reasoning: {reasoning}")
    print(f"\n  BTC pos z / Consensus z correlation: {corr_btc_cons:.3f}")
    print(f"  s320 OOS Sharpe:      {results['base_pos_vrp']['oos']['sharpe']:.3f}")
    print(f"  +Consensus OOS Sharpe: {results['base_pos_vrp_consensus']['oos']['sharpe']:.3f}")
    print(f"  Consensus-only Sharpe: {results['base_consensus_only']['oos']['sharpe']:.3f}")
    print(f"  Walk-forward: {n_wins}/{n_valid} windows improve")


if __name__ == '__main__':
    main()
