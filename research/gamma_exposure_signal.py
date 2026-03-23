#!/workspace/venv/bin/python
"""
Gamma Exposure (GEX) Regime Detection Signal — Crypto Trading Research
======================================================================

Signal hypothesis:
  Dealer gamma exposure from options markets determines volatility regimes:
  - Positive gamma: dealers hedge by selling rallies/buying dips -> dampens volatility
    -> mean reversion works
  - Negative gamma: dealers hedge by buying rallies/selling dips -> amplifies volatility
    -> trend following works
  - GEX crossing zero is the key regime transition signal
  - Dec 2025 evidence: dealer gamma was 13x stronger than ETF flows ($507M vs $38M daily)

Data reality:
  - Historical options chain snapshots (OI + greeks by strike) are NOT available from
    Deribit free API. Each snapshot is ephemeral — you must collect it yourself.
  - We have only 2 live snapshots from 2026-03-23.
  - What IS available: 5 years of daily Deribit DVOL index (implied vol), plus full
    BTC/ETH price histories for realized vol computation.

Approach — GEX REGIME PROXY:
  The core insight is that GEX sign determines whether vol amplifies or dampens. We can
  detect the *regime* without the exact GEX level by using DVOL-based signals:

  1. DVOL Level Regime: High DVOL = likely negative gamma (fear/hedging demand pumps up
     put OI -> dealers short puts -> negative gamma). Low DVOL = positive gamma regime.
  2. DVOL Change Momentum: Rapidly rising DVOL = transition to negative gamma (vol
     amplification). Falling DVOL = transition to positive gamma (vol suppression).
  3. Volatility Risk Premium (VRP): DVOL - RealizedVol. When VRP is high, options are
     expensive (heavy put buying for hedging -> negative gamma). When VRP is low or
     negative, options are cheap (complacency -> positive gamma).
  4. DVOL Z-Score: Standardized DVOL relative to rolling window -> regime detection.
  5. Composite GEX Proxy: Combines all four sub-signals into a single regime indicator.

  PLUS: Direct GEX computation from live snapshot (PART A) to validate the methodology
  and establish the current regime.

OOS cutoff: 2025-07-01
IC significance threshold: |IC| > 0.05
"""

import os
import sys
import json
import warnings
import numpy as np
import pandas as pd
from scipy import stats
from scipy.stats import norm
from datetime import datetime, timezone

warnings.filterwarnings('ignore')

# ── Paths ───────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.join(BASE_DIR, '..')
CACHE_DIR = os.path.join(PROJECT_DIR, 'data', 'perp', '1h_cache')
DERIBIT_DIR = os.path.join(PROJECT_DIR, 'data', 'alternative', 'deribit_options')
DVOL_DIR = os.path.join(DERIBIT_DIR, 'dvol')
RAW_DIR = os.path.join(DERIBIT_DIR, 'raw')
RESULTS_DIR = os.path.join(BASE_DIR)

OOS_START = pd.Timestamp('2025-07-01')
IC_THRESHOLD = 0.05
ANNUALIZE = np.sqrt(365)

# Forward return horizons to test
FWD_HORIZONS = [1, 3, 7, 14]


# ══════════════════════════════════════════════════════════════════════════
# DATA LOADING
# ══════════════════════════════════════════════════════════════════════════

def load_dvol(currency='BTC'):
    """Load DVOL daily data from saved JSON."""
    fpath = os.path.join(DVOL_DIR, f'{currency.lower()}_dvol_daily.json')
    if not os.path.exists(fpath):
        print(f'  [ERROR] DVOL file not found: {fpath}')
        return pd.DataFrame()
    with open(fpath) as f:
        raw = json.load(f)
    df = pd.DataFrame(raw, columns=['timestamp', 'open', 'high', 'low', 'close'])
    df['date'] = pd.to_datetime(df['timestamp'], unit='ms').dt.tz_localize(None)
    df = df.drop_duplicates(subset='date').sort_values('date').reset_index(drop=True)
    df = df.rename(columns={'close': 'dvol_close', 'open': 'dvol_open',
                            'high': 'dvol_high', 'low': 'dvol_low'})
    return df.set_index('date')[['dvol_open', 'dvol_high', 'dvol_low', 'dvol_close']]


def load_price(symbol='BTC'):
    """Load 1h price data and resample to daily."""
    fpath = os.path.join(CACHE_DIR, f'{symbol}_1h.parquet')
    if not os.path.exists(fpath):
        print(f'  [ERROR] Price file not found: {fpath}')
        return pd.DataFrame()
    df = pd.read_parquet(fpath)
    df.index = df.index.tz_localize(None) if df.index.tz is not None else df.index
    daily = df.resample('1D').agg({
        'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last',
        'volume': 'sum'
    }).dropna(subset=['close'])
    if 'funding_rate' in df.columns:
        daily['funding_rate'] = df['funding_rate'].resample('1D').mean()
    return daily


def load_raw_snapshot():
    """Load raw Deribit options snapshots from parquet files."""
    import glob
    files = sorted(glob.glob(os.path.join(RAW_DIR, 'snapshot_*.parquet')))
    if not files:
        return pd.DataFrame()
    frames = [pd.read_parquet(f) for f in files]
    return pd.concat(frames, ignore_index=True)


# ══════════════════════════════════════════════════════════════════════════
# PART A: DIRECT GEX COMPUTATION FROM LIVE SNAPSHOT
# ══════════════════════════════════════════════════════════════════════════

def bs_gamma(S, K, T, sigma, r=0.0):
    """Black-Scholes gamma for a European option."""
    if T <= 0 or sigma <= 0 or S <= 0:
        return 0.0
    d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
    return norm.pdf(d1) / (S * sigma * np.sqrt(T))


def bs_delta(S, K, T, sigma, option_type='C', r=0.0):
    """Black-Scholes delta for a European option."""
    if T <= 0 or sigma <= 0 or S <= 0:
        return 0.0
    d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
    if option_type == 'C':
        return norm.cdf(d1)
    else:
        return norm.cdf(d1) - 1.0


def compute_gex_from_snapshot(df, currency='BTC'):
    """
    Compute Gamma Exposure (GEX) from an options chain snapshot.

    Dealer gamma convention:
      - Market participants BUY options (calls for upside, puts for protection)
      - Dealers/MMs are NET SHORT options -> they delta-hedge
      - Dealer gamma from calls: SHORT calls -> when spot rises, call delta increases ->
        dealers must BUY more underlying -> STABILIZING -> POSITIVE GEX
      - Dealer gamma from puts: SHORT puts -> when spot falls, put delta goes more
        negative -> dealers must SELL more underlying -> DESTABILIZING -> but...
        puts bought for hedging means dealers are short puts, which gives POSITIVE gamma
        (put gamma is always positive, dealers being short = negative position but
        hedging by buying on dips)

    Standard convention:
      GEX = sum(call_OI * call_gamma * S^2 * 0.01) - sum(put_OI * put_gamma * S^2 * 0.01)

    When GEX > 0: net positive gamma -> dealers dampen volatility (mean reversion regime)
    When GEX < 0: net negative gamma -> dealers amplify volatility (trend regime)
    """
    opts = df[df['currency'] == currency].copy()
    if opts.empty:
        return {}

    S = opts['underlying_price'].iloc[0]

    # Compute greeks for each option
    gammas = []
    deltas = []
    for _, row in opts.iterrows():
        T = max(row['days_to_expiry'] / 365, 1e-6)
        sigma = row['mark_iv'] / 100  # convert from percentage
        K = row['strike']
        ot = row['option_type']
        gammas.append(bs_gamma(S, K, T, sigma))
        deltas.append(bs_delta(S, K, T, sigma, ot))

    opts['gamma'] = gammas
    opts['delta'] = deltas
    opts['gex_contribution'] = opts['open_interest'] * opts['gamma'] * S * S * 0.01

    # Split by type
    calls = opts[opts['option_type'] == 'C']
    puts = opts[opts['option_type'] == 'P']

    call_gex = calls['gex_contribution'].sum()
    put_gex = puts['gex_contribution'].sum()
    net_gex = call_gex - put_gex

    # GEX profile by strike
    gex_by_strike = opts.groupby(['strike', 'option_type'])['gex_contribution'].sum().unstack(fill_value=0)
    if 'C' in gex_by_strike.columns and 'P' in gex_by_strike.columns:
        gex_by_strike['net'] = gex_by_strike['C'] - gex_by_strike['P']
    elif 'C' in gex_by_strike.columns:
        gex_by_strike['net'] = gex_by_strike['C']
    else:
        gex_by_strike['net'] = -gex_by_strike.get('P', 0)

    # Key levels
    max_gamma_strike = gex_by_strike['net'].abs().idxmax()
    zero_gamma_level = None
    # Find strike closest to GEX flip (where net GEX changes sign)
    sorted_strikes = gex_by_strike.sort_index()
    sign_changes = sorted_strikes['net'].values[:-1] * sorted_strikes['net'].values[1:]
    flip_idx = np.where(sign_changes < 0)[0]
    if len(flip_idx) > 0:
        # Find the flip closest to spot
        flip_strikes = sorted_strikes.index.values[flip_idx]
        closest = flip_strikes[np.argmin(np.abs(flip_strikes - S))]
        zero_gamma_level = closest

    # OI statistics
    total_call_oi = calls['open_interest'].sum()
    total_put_oi = puts['open_interest'].sum()
    pc_ratio = total_put_oi / total_call_oi if total_call_oi > 0 else np.nan

    # Weighted average delta (net dealer delta exposure)
    dealer_delta = -(calls['open_interest'] * calls['delta']).sum() - \
                    (puts['open_interest'] * puts['delta']).sum()

    return {
        'currency': currency,
        'spot': S,
        'call_gex': call_gex,
        'put_gex': put_gex,
        'net_gex': net_gex,
        'gex_sign': 'POSITIVE' if net_gex > 0 else 'NEGATIVE',
        'max_gamma_strike': max_gamma_strike,
        'zero_gamma_level': zero_gamma_level,
        'total_call_oi': total_call_oi,
        'total_put_oi': total_put_oi,
        'put_call_ratio': pc_ratio,
        'dealer_delta': dealer_delta,
        'gex_by_strike': gex_by_strike,
        'n_options': len(opts),
    }


def analyze_live_gex():
    """PART A: Analyze live GEX from snapshot data."""
    print('=' * 80)
    print('PART A: DIRECT GEX COMPUTATION FROM LIVE DERIBIT SNAPSHOT')
    print('=' * 80)

    snapshot = load_raw_snapshot()
    if snapshot.empty:
        print('  No raw snapshot data available. Skipping Part A.')
        return {}

    results = {}
    for ccy in ['BTC', 'ETH']:
        gex = compute_gex_from_snapshot(snapshot, ccy)
        if not gex:
            continue
        results[ccy] = gex

        print(f'\n  --- {ccy} GEX Analysis ---')
        print(f'    Spot price:              ${gex["spot"]:>12,.2f}')
        print(f'    Call GEX:                ${gex["call_gex"]:>12,.0f}')
        print(f'    Put GEX:                 ${gex["put_gex"]:>12,.0f}')
        print(f'    Net Dealer GEX:          ${gex["net_gex"]:>12,.0f}')
        print(f'    GEX Sign:                {gex["gex_sign"]}')
        print(f'    Max Gamma Strike:        ${gex["max_gamma_strike"]:>12,.0f}')
        if gex['zero_gamma_level']:
            print(f'    Zero-Gamma Level:        ${gex["zero_gamma_level"]:>12,.0f}')
        else:
            print(f'    Zero-Gamma Level:        N/A (all same sign)')
        print(f'    Put/Call OI Ratio:       {gex["put_call_ratio"]:>12.4f}')
        print(f'    Net Dealer Delta:        {gex["dealer_delta"]:>12,.2f} {ccy}')
        print(f'    Total Options:           {gex["n_options"]:>12,d}')

        # Regime interpretation
        if gex['net_gex'] > 0:
            regime = 'POSITIVE GAMMA (vol-suppression / mean reversion regime)'
        else:
            regime = 'NEGATIVE GAMMA (vol-amplification / trend regime)'
        print(f'    Current Regime:          {regime}')

        # GEX per strike — top 10
        gex_strikes = gex['gex_by_strike'].copy()
        gex_strikes['abs_net'] = gex_strikes['net'].abs()
        top = gex_strikes.nlargest(10, 'abs_net')
        print(f'\n    Top 10 GEX strikes:')
        print(f'    {"Strike":>10s}  {"Call GEX":>14s}  {"Put GEX":>14s}  {"Net GEX":>14s}')
        for strike, row in top.iterrows():
            cg = row.get('C', 0)
            pg = row.get('P', 0)
            print(f'    ${strike:>9,.0f}  ${cg:>13,.0f}  ${pg:>13,.0f}  ${row["net"]:>13,.0f}')

    return results


# ══════════════════════════════════════════════════════════════════════════
# PART B: GEX REGIME PROXY SIGNALS FROM DVOL
# ══════════════════════════════════════════════════════════════════════════

def build_gex_proxy_signals(currency='BTC'):
    """
    Build GEX regime proxy signals from DVOL + price data.

    Sub-signals (all designed so HIGHER = more likely NEGATIVE gamma / trending regime):
      1. dvol_level:  DVOL z-score (high DVOL = fear = hedging demand = negative gamma)
      2. dvol_change: 5d DVOL change (rising vol = transitioning to negative gamma)
      3. vrp:         Vol Risk Premium = DVOL - RVol (high VRP = put overpricing = negative gamma)
      4. dvol_accel:  DVOL acceleration (2nd derivative, captures regime transitions)
      5. vrp_zscore:  VRP normalized to z-score for regime detection

    Composite GEX Proxy = mean of z-scored sub-signals
      - HIGH composite = NEGATIVE gamma regime (trend following)
      - LOW composite = POSITIVE gamma regime (mean reversion)
    """
    dvol = load_dvol(currency)
    price = load_price(currency)

    if dvol.empty or price.empty:
        print(f'  [SKIP] Missing data for {currency}')
        return pd.DataFrame()

    # Merge on date
    merged = price.join(dvol, how='inner')
    if len(merged) < 60:
        print(f'  [SKIP] Insufficient merged data: {len(merged)} days')
        return pd.DataFrame()

    print(f'\n  {currency}: {len(merged)} trading days ({merged.index[0].date()} to {merged.index[-1].date()})')

    # ── Sub-signal 1: DVOL Level (z-scored) ──
    # Higher DVOL = more hedging demand = more likely negative gamma
    merged['dvol_ma60'] = merged['dvol_close'].rolling(60, min_periods=30).mean()
    merged['dvol_std60'] = merged['dvol_close'].rolling(60, min_periods=30).std()
    merged['dvol_zscore'] = (merged['dvol_close'] - merged['dvol_ma60']) / merged['dvol_std60']

    # ── Sub-signal 2: DVOL Momentum (5d change) ──
    # Rising DVOL = transitioning toward negative gamma
    merged['dvol_change_5d'] = merged['dvol_close'].pct_change(5)
    merged['dvol_change_10d'] = merged['dvol_close'].pct_change(10)

    # ── Sub-signal 3: Realized Volatility ──
    merged['ret'] = merged['close'].pct_change()
    merged['abs_ret'] = merged['ret'].abs()
    for window in [10, 20, 30, 60]:
        merged[f'rvol_{window}d'] = merged['ret'].rolling(window, min_periods=max(5, window // 2)).std() * np.sqrt(365) * 100

    # ── Sub-signal 4: Volatility Risk Premium ──
    merged['vrp_20d'] = merged['dvol_close'] - merged['rvol_20d']
    merged['vrp_60d'] = merged['dvol_close'] - merged['rvol_60d']

    # VRP z-scored
    merged['vrp_ma60'] = merged['vrp_20d'].rolling(60, min_periods=30).mean()
    merged['vrp_std60'] = merged['vrp_20d'].rolling(60, min_periods=30).std()
    merged['vrp_zscore'] = (merged['vrp_20d'] - merged['vrp_ma60']) / merged['vrp_std60']

    # ── Sub-signal 5: DVOL Acceleration (2nd derivative) ──
    merged['dvol_accel'] = merged['dvol_change_5d'] - merged['dvol_change_5d'].shift(5)

    # ── Sub-signal 6: DVOL Intraday Range (realized IV movement) ──
    merged['dvol_range'] = (merged['dvol_high'] - merged['dvol_low']) / merged['dvol_close']

    # ── Sub-signal 7: Vol of Vol (VVOL) ──
    merged['dvol_ret'] = merged['dvol_close'].pct_change()
    merged['vvol_20d'] = merged['dvol_ret'].rolling(20, min_periods=10).std() * np.sqrt(365)

    # ── Composite GEX Proxy ──
    # Z-score each sub-signal using expanding window (avoid look-ahead)
    sub_signals = ['dvol_zscore', 'dvol_change_5d', 'vrp_zscore', 'dvol_accel', 'vvol_20d']
    for sig in sub_signals:
        expanding_mean = merged[sig].expanding(min_periods=30).mean()
        expanding_std = merged[sig].expanding(min_periods=30).std()
        merged[f'{sig}_z'] = (merged[sig] - expanding_mean) / expanding_std

    # Composite = mean of z-scored sub-signals
    z_cols = [f'{s}_z' for s in sub_signals]
    merged['gex_proxy_composite'] = merged[z_cols].mean(axis=1)

    # ── Regime Classification ──
    # Positive composite = NEGATIVE gamma regime (high vol, rising vol, high VRP)
    # Negative composite = POSITIVE gamma regime (low vol, falling vol, low VRP)
    merged['gex_regime'] = np.where(merged['gex_proxy_composite'] > 0, 'NEG_GAMMA', 'POS_GAMMA')

    # Regime strength buckets
    merged['regime_strength'] = pd.cut(merged['gex_proxy_composite'],
                                        bins=[-np.inf, -1, -0.3, 0.3, 1, np.inf],
                                        labels=['STRONG_POS_GAMMA', 'MODERATE_POS_GAMMA',
                                                'NEUTRAL', 'MODERATE_NEG_GAMMA',
                                                'STRONG_NEG_GAMMA'])

    # ── Forward Returns ──
    for h in FWD_HORIZONS:
        merged[f'fwd_ret_{h}d'] = merged['close'].pct_change(h).shift(-h)

    # ── Regime-Conditional Return Statistics ──
    # Also compute: abs(fwd_ret) to test vol prediction, and sign(fwd_ret) for direction
    for h in FWD_HORIZONS:
        merged[f'fwd_abs_ret_{h}d'] = merged[f'fwd_ret_{h}d'].abs()
        merged[f'fwd_sign_{h}d'] = np.sign(merged[f'fwd_ret_{h}d'])

    return merged


def compute_ic(signals_df, signal_col, target_col, min_obs=30):
    """Compute rank IC (Spearman) between signal and target."""
    valid = signals_df[[signal_col, target_col]].dropna()
    if len(valid) < min_obs:
        return np.nan, np.nan, len(valid)
    ic, pval = stats.spearmanr(valid[signal_col], valid[target_col])
    return ic, pval, len(valid)


def compute_ic_timeseries(signals_df, signal_col, target_col, window=60):
    """Compute rolling IC over time for stability analysis."""
    valid = signals_df[[signal_col, target_col]].dropna()
    ic_series = []
    dates = []
    for i in range(window, len(valid)):
        chunk = valid.iloc[i - window:i]
        ic, _, _ = compute_ic(chunk, signal_col, target_col, min_obs=20)
        ic_series.append(ic)
        dates.append(valid.index[i])
    return pd.Series(ic_series, index=dates)


def analyze_gex_proxy_signals(currency='BTC'):
    """PART B: Compute and test GEX proxy signals."""
    print(f'\n{"=" * 80}')
    print(f'PART B: GEX REGIME PROXY SIGNALS — {currency}')
    print(f'{"=" * 80}')

    df = build_gex_proxy_signals(currency)
    if df.empty:
        return pd.DataFrame(), {}

    # Split IS/OOS
    is_df = df[df.index < OOS_START]
    oos_df = df[df.index >= OOS_START]
    print(f'  In-sample:  {len(is_df)} days ({is_df.index[0].date()} to {is_df.index[-1].date()})')
    print(f'  Out-of-sample: {len(oos_df)} days ({oos_df.index[0].date()} to {oos_df.index[-1].date()})')

    # ── Signal List ──
    all_signals = [
        ('dvol_zscore', 'DVOL Z-Score (60d)'),
        ('dvol_change_5d', 'DVOL 5d Change'),
        ('dvol_change_10d', 'DVOL 10d Change'),
        ('vrp_20d', 'VRP (DVOL - RVol20d)'),
        ('vrp_zscore', 'VRP Z-Score'),
        ('dvol_accel', 'DVOL Acceleration'),
        ('vvol_20d', 'Vol-of-Vol (20d)'),
        ('dvol_range', 'DVOL Intraday Range'),
        ('gex_proxy_composite', 'GEX Proxy Composite'),
    ]

    # ── IC Analysis ──
    results = {}
    print(f'\n  {"Signal":<30s} | {"Horizon":>7s} | {"IS IC":>7s} | {"IS p":>7s} | {"OOS IC":>7s} | {"OOS p":>7s} | {"IS t":>6s} | {"N_IS":>5s} | {"N_OOS":>5s}')
    print(f'  {"-" * 120}')

    for sig_col, sig_name in all_signals:
        for h in FWD_HORIZONS:
            target = f'fwd_ret_{h}d'

            # IS
            is_ic, is_p, is_n = compute_ic(is_df, sig_col, target)
            # OOS
            oos_ic, oos_p, oos_n = compute_ic(oos_df, sig_col, target)

            # T-stat approximation: t = IC * sqrt(N-2) / sqrt(1 - IC^2)
            is_t = np.nan
            if not np.isnan(is_ic) and abs(is_ic) < 1:
                is_t = is_ic * np.sqrt(is_n - 2) / np.sqrt(1 - is_ic ** 2)

            key = f'{sig_col}__{h}d'
            results[key] = {
                'signal': sig_name, 'horizon': h,
                'is_ic': is_ic, 'is_p': is_p, 'is_n': is_n, 'is_t': is_t,
                'oos_ic': oos_ic, 'oos_p': oos_p, 'oos_n': oos_n,
            }

            # Highlight significant results
            flag = ''
            if not np.isnan(oos_ic) and abs(oos_ic) > IC_THRESHOLD:
                flag = ' ***' if oos_p < 0.01 else ' **' if oos_p < 0.05 else ' *'

            print(f'  {sig_name:<30s} | {h:>5d}d | {is_ic:>7.4f} | {is_p:>7.4f} | {oos_ic:>7.4f} | {oos_p:>7.4f} | {is_t:>6.2f} | {is_n:>5d} | {oos_n:>5d}{flag}')

    # ── Regime-Conditional Analysis ──
    print(f'\n  {"=" * 80}')
    print(f'  REGIME-CONDITIONAL RETURN ANALYSIS')
    print(f'  {"=" * 80}')

    for split_name, split_df in [('In-Sample', is_df), ('Out-of-Sample', oos_df)]:
        print(f'\n  --- {split_name} ---')
        for h in FWD_HORIZONS:
            ret_col = f'fwd_ret_{h}d'
            abs_ret_col = f'fwd_abs_ret_{h}d'

            regime_stats = split_df.groupby('gex_regime').agg({
                ret_col: ['mean', 'std', 'count'],
                abs_ret_col: ['mean'],
            })

            print(f'\n    Forward {h}d returns by regime:')
            print(f'    {"Regime":<20s} | {"Mean Ret":>10s} | {"Std":>10s} | {"Abs Ret":>10s} | {"Sharpe":>8s} | {"N":>5s}')
            for regime in ['POS_GAMMA', 'NEG_GAMMA']:
                if regime not in regime_stats.index:
                    continue
                mean_r = regime_stats.loc[regime, (ret_col, 'mean')]
                std_r = regime_stats.loc[regime, (ret_col, 'std')]
                abs_r = regime_stats.loc[regime, (abs_ret_col, 'mean')]
                n = int(regime_stats.loc[regime, (ret_col, 'count')])
                sharpe = (mean_r / std_r * np.sqrt(365 / h)) if std_r > 0 else 0
                print(f'    {regime:<20s} | {mean_r:>9.4f}% | {std_r:>9.4f}% | {abs_r:>9.4f}% | {sharpe:>7.2f} | {n:>5d}')

    # ── Regime Transition Analysis ──
    print(f'\n  {"=" * 80}')
    print(f'  REGIME TRANSITION ANALYSIS')
    print(f'  {"=" * 80}')

    df['regime_prev'] = df['gex_regime'].shift(1)
    df['regime_change'] = df['gex_regime'] != df['regime_prev']

    for split_name, split_df in [('In-Sample', df[df.index < OOS_START]), ('Out-of-Sample', df[df.index >= OOS_START])]:
        transitions = split_df[split_df['regime_change']].copy()
        print(f'\n  --- {split_name}: {len(transitions)} regime transitions ---')

        if len(transitions) < 5:
            print('    Too few transitions for analysis.')
            continue

        # Returns after transitions
        for h in FWD_HORIZONS:
            ret_col = f'fwd_ret_{h}d'
            for direction in ['POS_GAMMA', 'NEG_GAMMA']:
                trans = transitions[transitions['gex_regime'] == direction]
                if len(trans) < 3:
                    continue
                mean_r = trans[ret_col].mean()
                median_r = trans[ret_col].median()
                print(f'    Transition TO {direction}: {h}d fwd mean={mean_r:.4f} median={median_r:.4f} (n={len(trans)})')

    # ── Rolling IC Stability ──
    print(f'\n  {"=" * 80}')
    print(f'  ROLLING IC STABILITY (60d window)')
    print(f'  {"=" * 80}')

    for sig_col, sig_name in [('gex_proxy_composite', 'GEX Proxy Composite'),
                               ('dvol_zscore', 'DVOL Z-Score'),
                               ('vrp_zscore', 'VRP Z-Score')]:
        for h in [1, 7]:
            target = f'fwd_ret_{h}d'
            ic_ts = compute_ic_timeseries(df, sig_col, target, window=60)
            if len(ic_ts) == 0:
                continue
            mean_ic = ic_ts.mean()
            std_ic = ic_ts.std()
            pct_positive = (ic_ts > 0).mean()
            pct_significant = (ic_ts.abs() > IC_THRESHOLD).mean()
            print(f'  {sig_name:<30s} {h}d: mean_IC={mean_ic:.4f}, std={std_ic:.4f}, '
                  f'%positive={pct_positive:.1%}, %significant={pct_significant:.1%}')

    # ── Volatility Prediction (does the regime predict abs returns?) ──
    print(f'\n  {"=" * 80}')
    print(f'  VOLATILITY PREDICTION (IC with absolute returns)')
    print(f'  {"=" * 80}')

    for sig_col, sig_name in all_signals:
        for h in [1, 7, 14]:
            target = f'fwd_abs_ret_{h}d'
            is_ic, is_p, _ = compute_ic(is_df, sig_col, target)
            oos_ic, oos_p, _ = compute_ic(oos_df, sig_col, target)
            flag = ''
            if not np.isnan(oos_ic) and abs(oos_ic) > IC_THRESHOLD:
                flag = ' ***' if oos_p < 0.01 else ' **' if oos_p < 0.05 else ' *'
            print(f'  {sig_name:<30s} {h:>2d}d: IS={is_ic:>7.4f} (p={is_p:.3f}), OOS={oos_ic:>7.4f} (p={oos_p:.3f}){flag}')

    return df, results


# ══════════════════════════════════════════════════════════════════════════
# PART C: STRATEGY SIMULATION — Does GEX regime switching work?
# ══════════════════════════════════════════════════════════════════════════

def simulate_regime_strategy(df, currency='BTC'):
    """
    Simulate a simple regime-switching strategy:
      - POSITIVE gamma regime: run mean-reversion (fade daily moves)
      - NEGATIVE gamma regime: run momentum (follow daily direction)
      - NEUTRAL: stay flat or reduced position

    This is a directional test — not a full backtest. The question is whether
    the regime signal adds value over buy-and-hold.
    """
    print(f'\n  {"=" * 80}')
    print(f'  PART C: REGIME-SWITCHING STRATEGY SIMULATION — {currency}')
    print(f'  {"=" * 80}')

    sim = df[['close', 'ret', 'gex_proxy_composite', 'gex_regime']].dropna().copy()
    if len(sim) < 100:
        print('  Insufficient data for simulation.')
        return

    # Strategy: use YESTERDAY's regime to trade TODAY
    sim['regime_signal'] = sim['gex_regime'].shift(1)
    sim['composite_signal'] = sim['gex_proxy_composite'].shift(1)

    # Mean reversion signal: -sign(yesterday's return)
    sim['mr_signal'] = -np.sign(sim['ret'].shift(1))

    # Momentum signal: +sign(5d return)
    sim['mom_signal'] = np.sign(sim['close'].pct_change(5).shift(1))

    # Regime-switched position
    sim['regime_position'] = np.where(
        sim['regime_signal'] == 'POS_GAMMA',
        sim['mr_signal'],     # mean reversion in positive gamma
        sim['mom_signal']     # momentum in negative gamma
    )

    # Strategy returns
    sim['strat_mr'] = sim['mr_signal'] * sim['ret']
    sim['strat_mom'] = sim['mom_signal'] * sim['ret']
    sim['strat_regime'] = sim['regime_position'] * sim['ret']
    sim['strat_bh'] = sim['ret']  # buy-and-hold benchmark

    # Split IS/OOS
    for split_name, split_df in [('In-Sample', sim[sim.index < OOS_START]),
                                  ('Out-of-Sample', sim[sim.index >= OOS_START]),
                                  ('Full Sample', sim)]:
        if len(split_df) < 30:
            continue
        print(f'\n    --- {split_name} ({len(split_df)} days) ---')
        print(f'    {"Strategy":<25s} | {"Ann.Ret":>9s} | {"Ann.Vol":>9s} | {"Sharpe":>8s} | {"MaxDD":>8s} | {"HitRate":>8s}')

        for strat_name, col in [('Buy & Hold', 'strat_bh'),
                                 ('Always MR', 'strat_mr'),
                                 ('Always Momentum', 'strat_mom'),
                                 ('GEX Regime Switch', 'strat_regime')]:
            rets = split_df[col].dropna()
            if len(rets) < 10:
                continue
            ann_ret = rets.mean() * 365
            ann_vol = rets.std() * np.sqrt(365)
            sharpe = ann_ret / ann_vol if ann_vol > 0 else 0
            cum_ret = (1 + rets).cumprod()
            max_dd = (cum_ret / cum_ret.cummax() - 1).min()
            hit_rate = (rets > 0).mean()

            print(f'    {strat_name:<25s} | {ann_ret:>8.1%} | {ann_vol:>8.1%} | {sharpe:>7.2f} | {max_dd:>7.1%} | {hit_rate:>7.1%}')

    # ── Regime frequency ──
    print(f'\n    Regime frequency (full sample):')
    regime_counts = sim['regime_signal'].value_counts()
    for regime, count in regime_counts.items():
        print(f'      {regime}: {count} days ({count / len(sim):.1%})')


# ══════════════════════════════════════════════════════════════════════════
# PART D: CROSS-ASSET ANALYSIS — BTC vs ETH GEX regime correlation
# ══════════════════════════════════════════════════════════════════════════

def cross_asset_analysis():
    """Compare GEX proxy regimes across BTC and ETH."""
    print(f'\n{"=" * 80}')
    print(f'PART D: CROSS-ASSET GEX REGIME ANALYSIS')
    print(f'{"=" * 80}')

    btc_df = build_gex_proxy_signals('BTC')
    eth_df = build_gex_proxy_signals('ETH')

    if btc_df.empty or eth_df.empty:
        print('  Missing data for cross-asset analysis.')
        return

    # Align dates
    common_idx = btc_df.index.intersection(eth_df.index)
    btc = btc_df.loc[common_idx].copy()
    eth = eth_df.loc[common_idx].copy()
    print(f'  Common days: {len(common_idx)}')

    # Correlation of GEX proxy composites
    corr = btc['gex_proxy_composite'].corr(eth['gex_proxy_composite'])
    print(f'  GEX proxy composite correlation (BTC vs ETH): {corr:.3f}')

    # Regime agreement
    btc_regime = btc['gex_regime']
    eth_regime = eth['gex_regime']
    agreement = (btc_regime == eth_regime).mean()
    print(f'  Regime agreement rate: {agreement:.1%}')

    # When regimes DISAGREE — is one leading?
    disagree = btc[btc_regime != eth_regime].copy()
    print(f'  Regime disagreement days: {len(disagree)} ({len(disagree)/len(common_idx):.1%})')

    # DVOL correlation
    dvol_corr = btc['dvol_close'].corr(eth['dvol_close'])
    print(f'  DVOL correlation: {dvol_corr:.3f}')

    # VRP correlation
    vrp_corr = btc['vrp_20d'].corr(eth['vrp_20d'])
    print(f'  VRP correlation: {vrp_corr:.3f}')


# ══════════════════════════════════════════════════════════════════════════
# PART E: DATA COLLECTION RECOMMENDATIONS
# ══════════════════════════════════════════════════════════════════════════

def data_collection_recommendations():
    """Document what data is available and what would be needed for true GEX."""
    print(f'\n{"=" * 80}')
    print(f'PART E: DATA AVAILABILITY & COLLECTION RECOMMENDATIONS')
    print(f'{"=" * 80}')

    print("""
  AVAILABLE DATA (what we used):
  ─────────────────────────────
  1. Deribit DVOL Index (daily OHLC): 2021-03-24 to present (~1826 days)
     - Source: Deribit public API /get_volatility_index_data
     - Quality: No gaps, official index
     - Limitation: Aggregate vol level, not GEX directly

  2. BTC/ETH 1h OHLCV + funding rates: 2020-01-01 to present
     - Source: Binance perpetual futures
     - Allows: Realized vol, returns, funding-based signals

  3. Live Deribit options snapshots: 2 snapshots (2026-03-23)
     - Full chain: strike, OI, mark_iv, delta, gamma per option
     - Allows: Direct GEX computation (single point-in-time)

  NOT AVAILABLE (what would improve the signal):
  ──────────────────────────────────────────────
  1. Historical options OI snapshots (daily)
     - CRITICAL: This is the actual data needed for historical GEX
     - Deribit does NOT provide historical OI via API
     - Must be collected via daily cron job going forward
     - Sources: Tardis.dev ($199/mo), Kaiko ($$$), or self-collect

  2. Historical GEX computations (pre-computed)
     - Laevitas, Greeks.live — both require paid subscriptions
     - Amberdata — enterprise pricing
     - No free source found

  3. Historical put/call OI ratio time series
     - Deribit doesn't expose this historically
     - Some of the information is embedded in DVOL (indirectly)

  RECOMMENDED COLLECTION PLAN:
  ────────────────────────────
  1. IMMEDIATE: Set up daily Deribit snapshot collection via cron
     - Fetch /get_book_summary_by_currency every 8 hours (3x/day)
     - Save to parquet with timestamp
     - Script exists: research/deribit_options_signal_analysis.py (Part A loader)

  2. SHORT-TERM (1-3 months): Accumulate enough snapshots for:
     - Daily GEX time series
     - GEX sign tracking
     - Put/call OI ratio time series
     - Strike-level OI heatmaps

  3. MEDIUM-TERM (3-6 months): Build proper GEX signal with:
     - Rolling 90-day IC analysis
     - Regime transition backtesting
     - Intraday GEX changes (4h frequency)

  4. ALTERNATIVE (if budget allows):
     - Tardis.dev historical Deribit data ($199/mo) — goes back to 2019
     - Would immediately enable full historical GEX backtest
""")


# ══════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════

def main():
    print('╔══════════════════════════════════════════════════════════════════════════════╗')
    print('║  GAMMA EXPOSURE (GEX) REGIME DETECTION SIGNAL — RESEARCH ANALYSIS          ║')
    print('╠══════════════════════════════════════════════════════════════════════════════╣')
    print(f'║  Date: {datetime.now().strftime("%Y-%m-%d %H:%M")}                                                  ║')
    print(f'║  OOS Cutoff: {OOS_START.date()}                                                 ║')
    print('╚══════════════════════════════════════════════════════════════════════════════╝')

    # Part A: Live GEX from snapshot
    gex_results = analyze_live_gex()

    # Part B: GEX proxy signals
    all_results = {}
    all_dfs = {}
    for currency in ['BTC', 'ETH']:
        df, results = analyze_gex_proxy_signals(currency)
        all_dfs[currency] = df
        all_results[currency] = results

    # Part C: Strategy simulation
    for currency in ['BTC', 'ETH']:
        if currency in all_dfs and not all_dfs[currency].empty:
            simulate_regime_strategy(all_dfs[currency], currency)

    # Part D: Cross-asset analysis
    cross_asset_analysis()

    # Part E: Data recommendations
    data_collection_recommendations()

    # ── Summary Table ──
    print(f'\n{"=" * 80}')
    print(f'SUMMARY: STRONGEST SIGNALS (OOS IC > {IC_THRESHOLD})')
    print(f'{"=" * 80}')

    strong_signals = []
    for currency, results in all_results.items():
        for key, r in results.items():
            if not np.isnan(r['oos_ic']) and abs(r['oos_ic']) > IC_THRESHOLD:
                strong_signals.append({
                    'currency': currency,
                    'signal': r['signal'],
                    'horizon': r['horizon'],
                    'is_ic': r['is_ic'],
                    'oos_ic': r['oos_ic'],
                    'oos_p': r['oos_p'],
                })

    if strong_signals:
        strong_df = pd.DataFrame(strong_signals).sort_values('oos_ic', key=abs, ascending=False)
        print(f'\n  {"Currency":<8s} | {"Signal":<30s} | {"Horizon":>7s} | {"IS IC":>7s} | {"OOS IC":>7s} | {"OOS p":>7s}')
        print(f'  {"-" * 90}')
        for _, row in strong_df.iterrows():
            print(f'  {row["currency"]:<8s} | {row["signal"]:<30s} | {row["horizon"]:>5d}d | {row["is_ic"]:>7.4f} | {row["oos_ic"]:>7.4f} | {row["oos_p"]:>7.4f}')
    else:
        print('\n  No signals with |OOS IC| > 0.05 found.')
        print('  This is expected with DVOL-based proxies. True GEX from OI snapshots would be stronger.')

    print(f'\n  Total signals tested: {sum(len(r) for r in all_results.values())}')
    print(f'  Significant OOS: {len(strong_signals)}')

    print('\n' + '=' * 80)
    print('RESEARCH COMPLETE')
    print('=' * 80)


if __name__ == '__main__':
    main()
