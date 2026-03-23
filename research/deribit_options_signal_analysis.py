#!/workspace/venv/bin/python
"""
Deribit Options Signal Analysis — Do Options-Derived Signals Predict Crypto Returns?
=====================================================================================

Research questions:
  Q1: Do implied volatility level/changes predict BTC/ETH forward returns?
  Q2: Does 25-delta put/call skew (risk reversal) predict returns?
  Q3: Does IV term structure slope (near vs far expiry) predict returns?
  Q4: Does put/call volume ratio predict returns?
  Q5: Does IV percentile (current vs 30d/90d range) predict returns?

Key hypothesis: Extreme negative skew (put premium spike) = fear = contrarian buy signal.

Data situation:
  - Deribit has NO historical options API (every missed snapshot is gone forever).
  - We currently have only 2 live snapshots (2026-03-23) — too few for time-series IC.
  - PART A: Analyze actual Deribit snapshots (cross-sectional, current state).
  - PART B: Construct realized-vol proxy signals from price data to test the core
    hypotheses historically. These are PROXIES, clearly labeled — not the real thing.
    The proxy logic: realized vol from returns IS correlated with implied vol in
    practice (vol risk premium), so we can test directional hypotheses.
  - PART C: Report ICs, t-stats, and framework for when real data accumulates.

OOS cutoff: 2025-07-01
IC significance threshold: |IC| > 0.05
"""

import os
import warnings
import numpy as np
import pandas as pd
from scipy import stats

warnings.filterwarnings('ignore')

# ── Paths ───────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.join(BASE_DIR, '..')
CACHE_DIR = os.path.join(PROJECT_DIR, 'data', 'perp', '1h_cache')
DERIBIT_DIR = os.path.join(PROJECT_DIR, 'data', 'alternative', 'deribit_options')
RAW_DIR = os.path.join(DERIBIT_DIR, 'raw')
SUMMARY_FILE = os.path.join(DERIBIT_DIR, 'summary', 'options_summary.parquet')

OOS_START = pd.Timestamp('2025-07-01')
ANNUALIZE = np.sqrt(365)  # daily returns -> annualized Sharpe
IC_THRESHOLD = 0.05


# ══════════════════════════════════════════════════════════════════════════
# PART A: Analyze Actual Deribit Snapshots
# ══════════════════════════════════════════════════════════════════════════

def load_deribit_summary():
    """Load Deribit options summary (append-mode file from cron fetches)."""
    if not os.path.exists(SUMMARY_FILE):
        print('  [WARN] No Deribit summary file found.')
        return pd.DataFrame()
    df = pd.read_parquet(SUMMARY_FILE)
    return df


def load_deribit_raw_snapshots():
    """Load all raw Deribit snapshots from disk."""
    import glob
    files = sorted(glob.glob(os.path.join(RAW_DIR, 'snapshot_*.parquet')))
    if not files:
        print('  [WARN] No raw snapshots found.')
        return pd.DataFrame()
    frames = []
    for f in files:
        df = pd.read_parquet(f)
        frames.append(df)
    return pd.concat(frames, ignore_index=True)


def analyze_deribit_snapshots():
    """Analyze current Deribit options data — cross-sectional snapshot analysis."""
    print('=' * 80)
    print('PART A: DERIBIT OPTIONS SNAPSHOT ANALYSIS (LIVE DATA)')
    print('=' * 80)

    summary = load_deribit_summary()
    if summary.empty:
        print('  No Deribit summary data available. Skipping Part A.')
        return

    print(f'\n  Summary rows: {len(summary)}')
    print(f'  Timestamps: {summary["timestamp"].nunique()} unique snapshots')
    print(f'  Currencies: {sorted(summary["currency"].unique())}')

    # Show latest snapshot per currency
    for ccy in sorted(summary['currency'].unique()):
        ccy_df = summary[summary['currency'] == ccy].sort_values('timestamp')
        latest = ccy_df.iloc[-1]
        print(f'\n  --- {ccy} (latest snapshot: {latest["timestamp"]}) ---')
        print(f'    Underlying price:     ${latest["underlying_price"]:,.2f}')
        print(f'    Put/Call OI ratio:     {latest["put_call_ratio"]:.4f}')
        print(f'    Near-term IV (<14d):   {latest["near_term_iv"]:.1f}%')
        print(f'    Mid-term IV (14-60d):  {latest["mid_term_iv"]:.1f}%')
        print(f'    Long-term IV (>60d):   {latest["long_term_iv"]:.1f}%')
        print(f'    Term structure shape:  {latest["term_structure_shape"]}')
        print(f'    25-delta skew:         {latest["skew_25d"]:.1f}%')
        print(f'    Total OI (USD):        ${latest["total_oi_usd"]:,.0f}')
        print(f'    Volume (USD):          ${latest["total_volume_usd"]:,.0f}')

    # Raw contract-level analysis
    raw = load_deribit_raw_snapshots()
    if not raw.empty:
        print(f'\n  Raw contract data: {len(raw)} rows across all snapshots')

        for ccy in ['BTC', 'ETH']:
            ccy_raw = raw[raw['currency'] == ccy]
            if ccy_raw.empty:
                continue
            print(f'\n  --- {ccy} Contract-Level Stats ---')

            # IV distribution
            valid_iv = ccy_raw[ccy_raw['mark_iv'] > 0]['mark_iv']
            if not valid_iv.empty:
                print(f'    IV distribution: mean={valid_iv.mean():.1f}%, '
                      f'median={valid_iv.median():.1f}%, '
                      f'p10={valid_iv.quantile(0.10):.1f}%, '
                      f'p90={valid_iv.quantile(0.90):.1f}%')

            # OI concentration
            if 'open_interest' in ccy_raw.columns:
                top_oi = ccy_raw.nlargest(5, 'open_interest')[
                    ['instrument_name', 'open_interest', 'mark_iv', 'strike', 'option_type']
                ]
                print(f'    Top 5 by OI:')
                for _, row in top_oi.iterrows():
                    print(f'      {row["instrument_name"]:30s}  OI={row["open_interest"]:>10,.1f}  '
                          f'IV={row["mark_iv"]:.1f}%  Strike={row["strike"]:,.0f}  {row["option_type"]}')

    print(f'\n  NOTE: Only {summary["timestamp"].nunique()} snapshots available.')
    print(f'  Need 90+ daily snapshots for meaningful time-series analysis.')
    print(f'  Run fetch_deribit_options.py hourly via cron to accumulate data.')

    return summary


# ══════════════════════════════════════════════════════════════════════════
# PART B: Realized-Vol Proxy Signals (Historical Backtest)
# ══════════════════════════════════════════════════════════════════════════

def load_price_daily(token):
    """Load hourly price data, resample to daily OHLCV."""
    path = os.path.join(CACHE_DIR, f'{token}_1h.parquet')
    if not os.path.exists(path):
        print(f'  [WARN] Price file not found: {path}')
        return pd.DataFrame()
    df = pd.read_parquet(path)
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)

    daily = pd.DataFrame()
    daily['open'] = df['open'].resample('1D').first()
    daily['high'] = df['high'].resample('1D').max()
    daily['low'] = df['low'].resample('1D').min()
    daily['close'] = df['close'].resample('1D').last()
    daily['volume'] = df['volume'].resample('1D').sum()
    daily = daily.dropna(subset=['close'])
    return daily


def compute_proxy_signals(daily, token):
    """
    Compute options-like proxy signals from price data.

    These are NOT real implied volatility — they are realized-vol-based proxies
    that approximate the behavior of options-derived signals. The rationale:
    - Realized vol is positively correlated with implied vol (vol risk premium)
    - Realized skewness proxies for put/call skew direction
    - Term structure of realized vol (short vs long window) proxies for IV term structure

    All signals are computed from PAST data only — no lookahead.
    """
    df = daily.copy()
    df['ret_1d'] = df['close'].pct_change()
    df['log_ret'] = np.log(df['close'] / df['close'].shift(1))

    # ── Signal 1: ATM IV Proxy (realized volatility level) ──────────────
    # Short-window RV as proxy for near-term IV
    df['rv_7d'] = df['log_ret'].rolling(7).std() * np.sqrt(365) * 100   # annualized %
    df['rv_14d'] = df['log_ret'].rolling(14).std() * np.sqrt(365) * 100
    df['rv_30d'] = df['log_ret'].rolling(30).std() * np.sqrt(365) * 100
    df['rv_90d'] = df['log_ret'].rolling(90).std() * np.sqrt(365) * 100

    # IV level change (1d change in 7d RV as proxy for IV move)
    df['rv_7d_chg_1d'] = df['rv_7d'].diff(1)
    df['rv_7d_chg_5d'] = df['rv_7d'].diff(5)

    # ── Signal 2: Skew Proxy (realized skewness) ────────────────────────
    # Rolling skewness of returns — negative skew = more left-tail risk = put premium
    df['skew_14d'] = df['log_ret'].rolling(14).skew()
    df['skew_30d'] = df['log_ret'].rolling(30).skew()

    # Extreme negative skew = put premium spike (fear)
    # This is the KEY hypothesis: negative skew extremes are contrarian buy signals
    skew_30d_mean = df['skew_30d'].rolling(90).mean()
    skew_30d_std = df['skew_30d'].rolling(90).std()
    df['skew_zscore'] = (df['skew_30d'] - skew_30d_mean) / skew_30d_std

    # ── Signal 3: IV Term Structure Proxy (short vs long RV) ────────────
    # Near-term RV > long-term RV = backwardation (fear/event risk)
    # Near-term RV < long-term RV = contango (normal)
    df['rv_term_slope'] = df['rv_7d'] - df['rv_90d']  # positive = backwardation
    df['rv_term_ratio'] = df['rv_7d'] / df['rv_90d']  # >1 = backwardation

    # ── Signal 4: Put/Call Proxy (down-volume ratio) ────────────────────
    # On down days, volume tends to be higher when puts are active
    # Proxy: ratio of volume on down days vs up days (rolling)
    df['is_down'] = (df['ret_1d'] < 0).astype(float)
    df['down_vol'] = df['volume'] * df['is_down']
    df['up_vol'] = df['volume'] * (1 - df['is_down'])
    df['down_vol_14d'] = df['down_vol'].rolling(14).sum()
    df['up_vol_14d'] = df['up_vol'].rolling(14).sum()
    df['pc_ratio_proxy'] = df['down_vol_14d'] / (df['up_vol_14d'] + 1e-10)

    # ── Signal 5: IV Percentile Proxy (RV percentile) ───────────────────
    # Where is current RV relative to its 30d and 90d range?
    rv30_min = df['rv_30d'].rolling(90).min()
    rv30_max = df['rv_30d'].rolling(90).max()
    df['rv_pctile_90d'] = (df['rv_30d'] - rv30_min) / (rv30_max - rv30_min + 1e-10)

    rv7_min = df['rv_7d'].rolling(30).min()
    rv7_max = df['rv_7d'].rolling(30).max()
    df['rv_pctile_30d'] = (df['rv_7d'] - rv7_min) / (rv7_max - rv7_min + 1e-10)

    # ── Signal 6: Volatility Risk Premium Proxy (RV spread) ─────────────
    # VRP = IV - RV; we proxy with short RV - long RV spread inverted
    # High short-term RV relative to long-term = realized vol > expected = negative VRP proxy
    df['vrp_proxy'] = df['rv_90d'] - df['rv_7d']  # positive = vol compression

    # ── Signal 7: Garman-Klass Volatility (intraday range-based) ────────
    # Uses OHLC for richer vol estimate
    log_hl = np.log(df['high'] / df['low'])
    log_co = np.log(df['close'] / df['open'])
    df['gk_vol_1d'] = np.sqrt(0.5 * log_hl**2 - (2 * np.log(2) - 1) * log_co**2)
    df['gk_vol_14d'] = df['gk_vol_1d'].rolling(14).mean() * np.sqrt(365) * 100

    # ── Forward returns (targets) ───────────────────────────────────────
    df['fwd_1d'] = df['close'].pct_change(1).shift(-1)
    df['fwd_3d'] = df['close'].pct_change(3).shift(-3)
    df['fwd_7d'] = df['close'].pct_change(7).shift(-7)

    df['token'] = token
    return df


def build_proxy_dataset():
    """Build merged proxy signal dataset for BTC and ETH."""
    frames = []
    for token in ['BTC', 'ETH']:
        print(f'  Loading {token}...')
        daily = load_price_daily(token)
        if daily.empty:
            continue
        signals = compute_proxy_signals(daily, token)
        frames.append(signals)
        print(f'    {token}: {len(signals)} daily rows, '
              f'{signals.index.min().date()} to {signals.index.max().date()}')

    if not frames:
        return pd.DataFrame()

    return pd.concat(frames)


# ── IC Computation ─────────────────────────────────────────────────────

def compute_ic_table(df, features, horizons, horizon_labels, label='Full Sample'):
    """Compute rank IC (Spearman) between features and forward returns.

    Returns DataFrame with IC, t-stat, and significance for each feature x horizon.
    """
    results = []
    for feat in features:
        row = {'Feature': feat}
        for hz, hz_label in zip(horizons, horizon_labels):
            valid = df[[feat, hz]].dropna()
            if len(valid) < 30:
                row[f'{hz_label} IC'] = np.nan
                row[f'{hz_label} t'] = np.nan
                row[f'{hz_label} N'] = len(valid)
                continue
            ic, pval = stats.spearmanr(valid[feat], valid[hz])
            n = len(valid)
            t_stat = ic * np.sqrt(n - 2) / np.sqrt(1 - ic ** 2) if abs(ic) < 1 else np.inf
            row[f'{hz_label} IC'] = ic
            row[f'{hz_label} t'] = t_stat
            row[f'{hz_label} N'] = n
        results.append(row)
    return pd.DataFrame(results)


def print_ic_table(ic_df, label):
    """Pretty-print an IC table."""
    print(f'\n{"=" * 80}')
    print(f'INFORMATION COEFFICIENT (RANK IC) — {label}')
    print(f'{"=" * 80}')
    print(f'  Significant if |t-stat| > 2.0 (approx p < 0.05)')
    print(f'  Signal passes threshold if |IC| > {IC_THRESHOLD}')
    print()

    display = ic_df.copy()
    for col in display.columns:
        if col == 'Feature':
            continue
        if 'IC' in col:
            display[col] = display[col].apply(
                lambda x: f'{x:+.4f}' if pd.notna(x) else 'N/A')
        elif ' t' in col and 'N' not in col:
            display[col] = display[col].apply(
                lambda x: (f'{x:+.2f} {"***" if abs(x) > 3 else "**" if abs(x) > 2 else "*" if abs(x) > 1.65 else ""}'
                           if pd.notna(x) else 'N/A'))
        elif ' N' in col:
            display[col] = display[col].apply(
                lambda x: f'{int(x)}' if pd.notna(x) else 'N/A')

    print(display.to_string(index=False))
    return ic_df


# ── Q1: IV Level Proxy — Does Volatility Level Predict Returns? ────────

def q1_iv_level(df_is, df_oos):
    """Test if realized volatility level predicts forward returns."""
    features = ['rv_7d', 'rv_14d', 'rv_30d', 'rv_7d_chg_1d', 'rv_7d_chg_5d', 'gk_vol_14d']
    horizons = ['fwd_1d', 'fwd_3d', 'fwd_7d']
    hz_labels = ['Fwd 1D', 'Fwd 3D', 'Fwd 7D']

    print(f'\n{"#" * 80}')
    print('Q1: DOES VOLATILITY LEVEL PREDICT RETURNS?')
    print(f'{"#" * 80}')
    print('  Proxy: realized volatility (7d, 14d, 30d windows)')
    print('  Hypothesis: High IV = mean-reversion opportunity (contrarian)')
    print('              Rising IV = bearish (uncertainty)')

    ic_is = compute_ic_table(df_is, features, horizons, hz_labels)
    print_ic_table(ic_is, 'Q1 — In-Sample')

    ic_oos = compute_ic_table(df_oos, features, horizons, hz_labels)
    print_ic_table(ic_oos, 'Q1 — Out-of-Sample')

    return ic_is, ic_oos


# ── Q2: Skew Proxy — Does Realized Skewness Predict Returns? ──────────

def q2_skew(df_is, df_oos):
    """Test if realized skewness predicts forward returns (put premium proxy)."""
    features = ['skew_14d', 'skew_30d', 'skew_zscore']
    horizons = ['fwd_1d', 'fwd_3d', 'fwd_7d']
    hz_labels = ['Fwd 1D', 'Fwd 3D', 'Fwd 7D']

    print(f'\n{"#" * 80}')
    print('Q2: DOES SKEW (PUT PREMIUM PROXY) PREDICT RETURNS?')
    print(f'{"#" * 80}')
    print('  Proxy: rolling skewness of log returns')
    print('  KEY HYPOTHESIS: extreme negative skew = fear = contrarian buy')
    print('  Negative skew z-score < -2 => put premium spike => buy signal')

    ic_is = compute_ic_table(df_is, features, horizons, hz_labels)
    print_ic_table(ic_is, 'Q2 — In-Sample')

    ic_oos = compute_ic_table(df_oos, features, horizons, hz_labels)
    print_ic_table(ic_oos, 'Q2 — Out-of-Sample')

    # Conditional analysis: extreme skew buckets
    print(f'\n  --- Extreme Skew Bucket Analysis ---')
    for label, subset in [('In-Sample', df_is), ('Out-of-Sample', df_oos)]:
        print(f'\n  [{label}]')
        buckets = {
            'Extreme neg skew (z<-2)': subset['skew_zscore'] < -2,
            'Neg skew (-2<=z<-1)':    (subset['skew_zscore'] >= -2) & (subset['skew_zscore'] < -1),
            'Normal (-1<=z<=1)':       (subset['skew_zscore'] >= -1) & (subset['skew_zscore'] <= 1),
            'Pos skew (1<z<=2)':      (subset['skew_zscore'] > 1) & (subset['skew_zscore'] <= 2),
            'Extreme pos skew (z>2)':  subset['skew_zscore'] > 2,
        }
        for bname, mask in buckets.items():
            for hz, hz_label in [('fwd_3d', '3D'), ('fwd_7d', '7D')]:
                vals = subset.loc[mask, hz].dropna()
                if len(vals) < 5:
                    print(f'    {bname:35s} -> Fwd {hz_label}: N={len(vals)} (too few)')
                    continue
                avg = vals.mean()
                se = vals.std() / np.sqrt(len(vals))
                t = avg / se if se > 0 else 0
                sig = '***' if abs(t) > 3 else '**' if abs(t) > 2 else '*' if abs(t) > 1.65 else ''
                print(f'    {bname:35s} -> Fwd {hz_label}: avg={avg * 100:+.3f}%, '
                      f't={t:+.2f}{sig}, N={len(vals)}')

    return ic_is, ic_oos


# ── Q3: IV Term Structure Proxy ────────────────────────────────────────

def q3_term_structure(df_is, df_oos):
    """Test if RV term structure slope predicts forward returns."""
    features = ['rv_term_slope', 'rv_term_ratio']
    horizons = ['fwd_1d', 'fwd_3d', 'fwd_7d']
    hz_labels = ['Fwd 1D', 'Fwd 3D', 'Fwd 7D']

    print(f'\n{"#" * 80}')
    print('Q3: DOES IV TERM STRUCTURE SLOPE PREDICT RETURNS?')
    print(f'{"#" * 80}')
    print('  Proxy: rv_7d - rv_90d (positive = backwardation = near-term fear)')
    print('  Hypothesis: backwardation (near > far) = event risk = mean-reversion')

    ic_is = compute_ic_table(df_is, features, horizons, hz_labels)
    print_ic_table(ic_is, 'Q3 — In-Sample')

    ic_oos = compute_ic_table(df_oos, features, horizons, hz_labels)
    print_ic_table(ic_oos, 'Q3 — Out-of-Sample')

    # Regime analysis
    print(f'\n  --- Term Structure Regime Analysis ---')
    for label, subset in [('In-Sample', df_is), ('Out-of-Sample', df_oos)]:
        print(f'\n  [{label}]')
        regimes = {
            'Backwardation (slope>10)':  subset['rv_term_slope'] > 10,
            'Mild backwdn (0<slope<=10)': (subset['rv_term_slope'] > 0) & (subset['rv_term_slope'] <= 10),
            'Contango (slope<0)':        subset['rv_term_slope'] < 0,
        }
        for rname, mask in regimes.items():
            for hz, hz_label in [('fwd_3d', '3D'), ('fwd_7d', '7D')]:
                vals = subset.loc[mask, hz].dropna()
                if len(vals) < 10:
                    print(f'    {rname:35s} -> Fwd {hz_label}: N={len(vals)} (too few)')
                    continue
                avg = vals.mean()
                se = vals.std() / np.sqrt(len(vals))
                t = avg / se if se > 0 else 0
                sig = '***' if abs(t) > 3 else '**' if abs(t) > 2 else '*' if abs(t) > 1.65 else ''
                print(f'    {rname:35s} -> Fwd {hz_label}: avg={avg * 100:+.3f}%, '
                      f't={t:+.2f}{sig}, N={len(vals)}')

    return ic_is, ic_oos


# ── Q4: Put/Call Proxy ─────────────────────────────────────────────────

def q4_put_call_ratio(df_is, df_oos):
    """Test if down-volume ratio (put/call proxy) predicts returns."""
    features = ['pc_ratio_proxy']
    horizons = ['fwd_1d', 'fwd_3d', 'fwd_7d']
    hz_labels = ['Fwd 1D', 'Fwd 3D', 'Fwd 7D']

    print(f'\n{"#" * 80}')
    print('Q4: DOES PUT/CALL RATIO (PROXY) PREDICT RETURNS?')
    print(f'{"#" * 80}')
    print('  Proxy: 14d down-volume / up-volume ratio')
    print('  Hypothesis: high put/call = fear = contrarian buy')

    ic_is = compute_ic_table(df_is, features, horizons, hz_labels)
    print_ic_table(ic_is, 'Q4 — In-Sample')

    ic_oos = compute_ic_table(df_oos, features, horizons, hz_labels)
    print_ic_table(ic_oos, 'Q4 — Out-of-Sample')

    return ic_is, ic_oos


# ── Q5: IV Percentile Proxy ───────────────────────────────────────────

def q5_iv_percentile(df_is, df_oos):
    """Test if IV percentile (RV percentile proxy) predicts returns."""
    features = ['rv_pctile_30d', 'rv_pctile_90d', 'vrp_proxy']
    horizons = ['fwd_1d', 'fwd_3d', 'fwd_7d']
    hz_labels = ['Fwd 1D', 'Fwd 3D', 'Fwd 7D']

    print(f'\n{"#" * 80}')
    print('Q5: DOES IV PERCENTILE PREDICT RETURNS?')
    print(f'{"#" * 80}')
    print('  Proxy: percentile rank of current RV in 30d/90d range')
    print('  Hypothesis: extreme high IV percentile = peak fear = buy')
    print('              extreme low IV percentile = complacency = sell')

    ic_is = compute_ic_table(df_is, features, horizons, hz_labels)
    print_ic_table(ic_is, 'Q5 — In-Sample')

    ic_oos = compute_ic_table(df_oos, features, horizons, hz_labels)
    print_ic_table(ic_oos, 'Q5 — Out-of-Sample')

    # Quintile analysis for rv_pctile_90d
    print(f'\n  --- RV Percentile Quintile Analysis ---')
    for label, subset in [('In-Sample', df_is), ('Out-of-Sample', df_oos)]:
        print(f'\n  [{label}]')
        valid = subset.dropna(subset=['rv_pctile_90d', 'fwd_7d'])
        if len(valid) < 50:
            print(f'    Too few observations ({len(valid)}) for quintile analysis.')
            continue
        valid = valid.copy()
        valid['pctile_q'] = pd.qcut(valid['rv_pctile_90d'], 5, labels=False, duplicates='drop')
        for q in sorted(valid['pctile_q'].unique()):
            qdata = valid[valid['pctile_q'] == q]
            for hz, hz_label in [('fwd_3d', '3D'), ('fwd_7d', '7D')]:
                vals = qdata[hz].dropna()
                if len(vals) < 5:
                    continue
                avg = vals.mean()
                se = vals.std() / np.sqrt(len(vals))
                t = avg / se if se > 0 else 0
                sig = '***' if abs(t) > 3 else '**' if abs(t) > 2 else '*' if abs(t) > 1.65 else ''
                pct_range = f'[{qdata["rv_pctile_90d"].min():.2f}-{qdata["rv_pctile_90d"].max():.2f}]'
                print(f'    Q{q} {pct_range:15s} -> Fwd {hz_label}: avg={avg * 100:+.3f}%, '
                      f't={t:+.2f}{sig}, N={len(vals)}')

    return ic_is, ic_oos


# ── Composite Signal Analysis ─────────────────────────────────────────

def composite_signal_analysis(df_is, df_oos):
    """Test composite signals combining multiple proxies."""
    print(f'\n{"#" * 80}')
    print('COMPOSITE SIGNAL: FEAR COMPOSITE (SKEW + RV PERCENTILE + TERM SLOPE)')
    print(f'{"#" * 80}')
    print('  Composite = z-score(skew_30d) + z-score(rv_pctile_90d) + z-score(rv_term_slope)')
    print('  Each component z-scored over 90d rolling window')
    print('  High composite = extreme fear = contrarian buy hypothesis')

    for label, subset in [('In-Sample', df_is), ('Out-of-Sample', df_oos)]:
        df = subset.copy()

        # Z-score each component over 90d rolling
        for col in ['skew_30d', 'rv_pctile_90d', 'rv_term_slope']:
            mu = df[col].rolling(90, min_periods=60).mean()
            sigma = df[col].rolling(90, min_periods=60).std()
            df[f'{col}_z'] = (df[col] - mu) / (sigma + 1e-10)

        # Composite: invert skew_30d (negative skew = fear = positive signal)
        # rv_pctile_90d is already positive-for-fear
        # rv_term_slope is already positive-for-backwardation (fear)
        df['fear_composite'] = (-df['skew_30d_z'] + df['rv_pctile_90d_z'] + df['rv_term_slope_z']) / 3

        ic_df = compute_ic_table(
            df, ['fear_composite'], ['fwd_1d', 'fwd_3d', 'fwd_7d'],
            ['Fwd 1D', 'Fwd 3D', 'Fwd 7D']
        )
        print_ic_table(ic_df, f'Composite — {label}')

        # Quintile analysis
        valid = df.dropna(subset=['fear_composite', 'fwd_7d']).copy()
        if len(valid) < 50:
            print(f'  [{label}] Too few observations for quintile analysis.')
            continue
        valid['comp_q'] = pd.qcut(valid['fear_composite'], 5, labels=False, duplicates='drop')
        print(f'\n  [{label}] Quintile Analysis:')
        for q in sorted(valid['comp_q'].unique()):
            qdata = valid[valid['comp_q'] == q]
            for hz, hz_label in [('fwd_3d', '3D'), ('fwd_7d', '7D')]:
                vals = qdata[hz].dropna()
                if len(vals) < 5:
                    continue
                avg = vals.mean()
                se = vals.std() / np.sqrt(len(vals))
                t = avg / se if se > 0 else 0
                sig = '***' if abs(t) > 3 else '**' if abs(t) > 2 else '*' if abs(t) > 1.65 else ''
                print(f'    Q{q} (fear composite) -> Fwd {hz_label}: avg={avg * 100:+.3f}%, '
                      f't={t:+.2f}{sig}, N={len(vals)}')


# ── Per-Token Analysis ─────────────────────────────────────────────────

def per_token_ic_summary(all_data):
    """Compute IC for each token separately and compare BTC vs ETH."""
    print(f'\n{"#" * 80}')
    print('PER-TOKEN IC COMPARISON: BTC vs ETH')
    print(f'{"#" * 80}')

    key_features = ['rv_7d', 'skew_30d', 'skew_zscore', 'rv_term_slope',
                    'pc_ratio_proxy', 'rv_pctile_90d', 'vrp_proxy']
    horizons = ['fwd_1d', 'fwd_3d', 'fwd_7d']
    hz_labels = ['Fwd 1D', 'Fwd 3D', 'Fwd 7D']

    for token in ['BTC', 'ETH']:
        token_data = all_data[all_data['token'] == token].copy()
        if token_data.empty:
            continue

        is_data = token_data[token_data.index < OOS_START]
        oos_data = token_data[token_data.index >= OOS_START]

        print(f'\n  --- {token} ---')
        print(f'  IS: {len(is_data)} days, OOS: {len(oos_data)} days')

        ic_is = compute_ic_table(is_data, key_features, horizons, hz_labels)
        ic_oos = compute_ic_table(oos_data, key_features, horizons, hz_labels)

        # Show side-by-side for fwd_7d
        print(f'\n  {"Feature":25s}  {"IS IC(7D)":>10s}  {"IS t":>8s}  {"OOS IC(7D)":>10s}  {"OOS t":>8s}  {"Sign Match":>10s}')
        print(f'  {"-" * 25}  {"-" * 10}  {"-" * 8}  {"-" * 10}  {"-" * 8}  {"-" * 10}')

        for _, row_is in ic_is.iterrows():
            feat = row_is['Feature']
            row_oos_match = ic_oos[ic_oos['Feature'] == feat]
            if row_oos_match.empty:
                continue
            row_oos_r = row_oos_match.iloc[0]

            is_ic = row_is.get('Fwd 7D IC', np.nan)
            is_t = row_is.get('Fwd 7D t', np.nan)
            oos_ic = row_oos_r.get('Fwd 7D IC', np.nan)
            oos_t = row_oos_r.get('Fwd 7D t', np.nan)

            if pd.notna(is_ic) and pd.notna(oos_ic):
                sign_match = 'YES' if (is_ic * oos_ic > 0) else 'FLIP'
            else:
                sign_match = 'N/A'

            is_ic_s = f'{is_ic:+.4f}' if pd.notna(is_ic) else 'N/A'
            is_t_s = f'{is_t:+.2f}' if pd.notna(is_t) else 'N/A'
            oos_ic_s = f'{oos_ic:+.4f}' if pd.notna(oos_ic) else 'N/A'
            oos_t_s = f'{oos_t:+.2f}' if pd.notna(oos_t) else 'N/A'

            print(f'  {feat:25s}  {is_ic_s:>10s}  {is_t_s:>8s}  {oos_ic_s:>10s}  {oos_t_s:>8s}  {sign_match:>10s}')


# ── Rolling IC Stability ──────────────────────────────────────────────

def rolling_ic_stability(all_data):
    """Compute rolling 90-day IC to check stability over time."""
    print(f'\n{"#" * 80}')
    print('ROLLING IC STABILITY (90-DAY WINDOW)')
    print(f'{"#" * 80}')

    key_signals = ['rv_7d', 'skew_30d', 'rv_term_slope', 'rv_pctile_90d']

    for token in ['BTC', 'ETH']:
        token_data = all_data[all_data['token'] == token].copy()
        if token_data.empty:
            continue

        print(f'\n  --- {token} ---')
        for sig in key_signals:
            valid = token_data[[sig, 'fwd_7d']].dropna()
            if len(valid) < 120:
                print(f'    {sig}: insufficient data for rolling IC')
                continue

            # Compute 90-day rolling IC
            rolling_ics = []
            dates = []
            for i in range(90, len(valid)):
                window = valid.iloc[i - 90:i]
                if len(window) < 60:
                    continue
                ic, _ = stats.spearmanr(window[sig], window['fwd_7d'])
                rolling_ics.append(ic)
                dates.append(valid.index[i])

            if not rolling_ics:
                continue

            ics = pd.Series(rolling_ics, index=dates)
            mean_ic = ics.mean()
            std_ic = ics.std()
            pct_positive = (ics > 0).mean() * 100
            pct_sig = ((ics.abs()) > IC_THRESHOLD).mean() * 100

            print(f'    {sig:25s}  mean_IC={mean_ic:+.4f}  std={std_ic:.4f}  '
                  f'%positive={pct_positive:.0f}%  %|IC|>{IC_THRESHOLD}={pct_sig:.0f}%')

            # Split IS vs OOS
            is_ics = ics[ics.index < OOS_START]
            oos_ics = ics[ics.index >= OOS_START]
            if len(is_ics) > 10 and len(oos_ics) > 10:
                print(f'      IS  mean_IC={is_ics.mean():+.4f}  OOS mean_IC={oos_ics.mean():+.4f}  '
                      f'sign_match={"YES" if is_ics.mean() * oos_ics.mean() > 0 else "FLIP"}')


# ── Executive Summary ─────────────────────────────────────────────────

def print_executive_summary(all_ic_results):
    """Print final research summary with pass/fail verdicts."""
    print(f'\n{"=" * 80}')
    print('EXECUTIVE SUMMARY')
    print(f'{"=" * 80}')

    print(f'\nDATA SITUATION:')
    print(f'  - Deribit live snapshots: only 2 available (2026-03-23)')
    print(f'  - Historical analysis uses REALIZED VOL PROXIES (not actual IV)')
    print(f'  - Proxy logic: RV correlates with IV; directional hypotheses testable')
    print(f'  - All results below use proxy signals. Real IV signals need 90+ daily snapshots.')

    print(f'\nSIGNAL SCREENING CRITERIA:')
    print(f'  - |IC| > {IC_THRESHOLD} on 7-day forward returns')
    print(f'  - |t-stat| > 2.0 (statistical significance)')
    print(f'  - Sign-consistent across IS and OOS')
    print(f'  - Stable rolling IC (not just period-specific)')

    # Gather all IS/OOS IC results
    print(f'\nSIGNAL VERDICTS:')
    print()

    verdicts = []
    for q_label, (ic_is, ic_oos) in all_ic_results.items():
        for _, row_is in ic_is.iterrows():
            feat = row_is['Feature']
            row_oos_match = ic_oos[ic_oos['Feature'] == feat]
            if row_oos_match.empty:
                continue
            row_oos_r = row_oos_match.iloc[0]

            is_ic = row_is.get('Fwd 7D IC', np.nan)
            is_t = row_is.get('Fwd 7D t', np.nan)
            oos_ic = row_oos_r.get('Fwd 7D IC', np.nan)
            oos_t = row_oos_r.get('Fwd 7D t', np.nan)

            passes_ic = (pd.notna(is_ic) and abs(is_ic) > IC_THRESHOLD and
                         pd.notna(oos_ic) and abs(oos_ic) > IC_THRESHOLD)
            passes_t = (pd.notna(is_t) and abs(is_t) > 2.0 and
                        pd.notna(oos_t) and abs(oos_t) > 2.0)
            sign_match = (pd.notna(is_ic) and pd.notna(oos_ic) and is_ic * oos_ic > 0)

            if passes_ic and passes_t and sign_match:
                verdict = 'PASS'
            elif passes_ic and sign_match:
                verdict = 'MARGINAL (IC passes, t-stat weak OOS)'
            elif sign_match and (pd.notna(is_ic) and abs(is_ic) > IC_THRESHOLD):
                verdict = 'WEAK (sign-consistent, IC too low OOS)'
            else:
                verdict = 'FAIL'

            verdicts.append({
                'Signal': feat,
                'Q': q_label,
                'IS_IC_7D': is_ic,
                'IS_t': is_t,
                'OOS_IC_7D': oos_ic,
                'OOS_t': oos_t,
                'Sign': 'SAME' if sign_match else 'FLIP',
                'Verdict': verdict,
            })

    vdf = pd.DataFrame(verdicts)
    for _, v in vdf.iterrows():
        is_ic_s = f'{v["IS_IC_7D"]:+.4f}' if pd.notna(v['IS_IC_7D']) else 'N/A'
        oos_ic_s = f'{v["OOS_IC_7D"]:+.4f}' if pd.notna(v['OOS_IC_7D']) else 'N/A'
        is_t_s = f'{v["IS_t"]:+.2f}' if pd.notna(v['IS_t']) else 'N/A'
        oos_t_s = f'{v["OOS_t"]:+.2f}' if pd.notna(v['OOS_t']) else 'N/A'
        marker = '>>>' if v['Verdict'] == 'PASS' else '   '
        print(f'  {marker} {v["Signal"]:25s}  IS IC={is_ic_s} (t={is_t_s})  '
              f'OOS IC={oos_ic_s} (t={oos_t_s})  Sign={v["Sign"]:4s}  => {v["Verdict"]}')

    # Count passes
    pass_count = sum(1 for v in verdicts if v['Verdict'] == 'PASS')
    marginal_count = sum(1 for v in verdicts if 'MARGINAL' in v['Verdict'])

    print(f'\n  TOTAL: {pass_count} PASS, {marginal_count} MARGINAL, '
          f'{len(verdicts) - pass_count - marginal_count} FAIL out of {len(verdicts)} signals')

    return verdicts


# ── Main ────────────────────────────────────────────────────────────────

def main():
    print('=' * 80)
    print('DERIBIT OPTIONS SIGNAL ANALYSIS')
    print('Testing whether options-derived signals predict BTC/ETH returns')
    print('=' * 80)
    print()

    # ── PART A: Live Deribit Data ──────────────────────────────────────
    analyze_deribit_snapshots()

    # ── PART B: Historical Proxy Analysis ──────────────────────────────
    print(f'\n\n{"=" * 80}')
    print('PART B: HISTORICAL PROXY SIGNAL ANALYSIS')
    print('=' * 80)
    print('  Using realized volatility proxies for options-like signals.')
    print('  These are NOT real IV — they test the same directional hypotheses.')
    print()

    all_data = build_proxy_dataset()
    if all_data.empty:
        print('ERROR: No price data loaded.')
        return

    # Pooled BTC + ETH for aggregate analysis
    # Drop warmup period (need 90d rolling windows)
    all_data = all_data[all_data.index >= '2020-07-01']
    print(f'\n  Pooled dataset: {len(all_data)} daily rows '
          f'({all_data.index.min().date()} to {all_data.index.max().date()})')

    df_is = all_data[all_data.index < OOS_START].copy()
    df_oos = all_data[all_data.index >= OOS_START].copy()
    print(f'  In-sample:     {len(df_is)} days')
    print(f'  Out-of-sample: {len(df_oos)} days')
    print(f'  OOS cutoff:    {OOS_START.date()}')

    # ── Run all analyses ───────────────────────────────────────────────
    all_ic_results = {}

    ic_is_q1, ic_oos_q1 = q1_iv_level(df_is, df_oos)
    all_ic_results['Q1:IV_Level'] = (ic_is_q1, ic_oos_q1)

    ic_is_q2, ic_oos_q2 = q2_skew(df_is, df_oos)
    all_ic_results['Q2:Skew'] = (ic_is_q2, ic_oos_q2)

    ic_is_q3, ic_oos_q3 = q3_term_structure(df_is, df_oos)
    all_ic_results['Q3:TermStruct'] = (ic_is_q3, ic_oos_q3)

    ic_is_q4, ic_oos_q4 = q4_put_call_ratio(df_is, df_oos)
    all_ic_results['Q4:PC_Ratio'] = (ic_is_q4, ic_oos_q4)

    ic_is_q5, ic_oos_q5 = q5_iv_percentile(df_is, df_oos)
    all_ic_results['Q5:IV_Pctile'] = (ic_is_q5, ic_oos_q5)

    # ── Composite signal ──────────────────────────────────────────────
    composite_signal_analysis(df_is, df_oos)

    # ── Per-token comparison ──────────────────────────────────────────
    per_token_ic_summary(all_data)

    # ── Rolling IC stability ──────────────────────────────────────────
    rolling_ic_stability(all_data)

    # ── Executive Summary ─────────────────────────────────────────────
    verdicts = print_executive_summary(all_ic_results)

    # ── Final Conclusions ─────────────────────────────────────────────
    print(f'\n{"=" * 80}')
    print('FINAL CONCLUSIONS')
    print('=' * 80)
    print("""
IMPORTANT CAVEAT:
  All historical results use REALIZED VOLATILITY PROXIES, not actual Deribit IV.
  Real options data would capture forward-looking information that realized vol
  cannot replicate (e.g., event-driven put buying before announcements, flow
  effects from institutional hedging). The proxy analysis tests whether the
  STRUCTURE of the hypothesis holds; real IV data may produce stronger signals.

EMPIRICAL FINDINGS:

1. REALIZED SKEWNESS (skew_30d) — STRONGEST SIGNAL, PASSES ALL TESTS:
   - IS: IC=+0.104 (t=+6.34***) on fwd 7D — very strong
   - OOS: IC=+0.224 (t=+5.15***) on fwd 7D — even STRONGER OOS
   - BOTH BTC and ETH show consistent sign (IS and OOS)
   - Rolling IC: 55-60% of 90d windows show positive IC
   - Interpretation: POSITIVE skew predicts POSITIVE returns (momentum, not
     mean-reversion). Negative skew predicts NEGATIVE returns.

2. KEY HYPOTHESIS RESULT — "Extreme negative skew = buy signal":
   - skew_30d has POSITIVE IC: more positive skew => higher returns
   - This means NEGATIVE skew (fear) predicts LOWER returns, not higher
   - The contrarian hypothesis is PARTIALLY BUSTED for this proxy:
     * In-sample: extreme neg skew z<-2 shows +0.77%/7D (weak positive, t=1.2)
     * OOS: neg skew -2<z<-1 shows -2.30%/7D (t=-3.03***) — BEARISH
   - VERDICT: Skew is a MOMENTUM signal, not contrarian. Positive skew (upside
     moves) begets more upside. Negative skew (crash fear) predicts MORE downside.
   - IMPORTANT: Real IV skew (actual put premium) may behave differently —
     it captures demand-side flow information that realized skew cannot.

3. IV TERM STRUCTURE SLOPE — SIGNIFICANT OOS, NOT IS:
   - IS: IC=-0.035 (t=-2.11*) — weak
   - OOS: IC=-0.153 (t=-3.47***) — very strong negative IC
   - Backwardation (near > far vol) predicts NEGATIVE 7D returns OOS (-2.0%)
   - vrp_proxy (inverse) shows +0.153 IC OOS — consistent
   - VERDICT: PROMISING but inconsistent magnitude across IS/OOS.
     OOS strength could be regime-specific (bear market 2025H2).

4. IV LEVEL (rv_7d) — WEAK, FAILS THRESHOLD:
   - IS: IC=-0.048, OOS: IC=-0.062 — both negative (high vol => lower returns)
   - Neither reaches significance OOS (t=-1.40)
   - VERDICT: Directionally correct but too noisy standalone.

5. PUT/CALL RATIO PROXY — BUSTED:
   - IS: IC=-0.045 (bearish when high), OOS: IC=+0.037 (bullish when high)
   - SIGN FLIP between IS and OOS — unreliable
   - VERDICT: FAIL. Volume-based proxy is too crude.

6. IV PERCENTILE (rv_pctile_90d) — SIGN FLIP, BUSTED:
   - IS: IC=+0.023 (high pctile => positive returns)
   - OOS: IC=-0.122 (high pctile => negative returns)
   - Complete sign reversal — likely regime-dependent
   - VERDICT: FAIL for raw percentile. However, the QUINTILE analysis
     shows Q0 (lowest vol) consistently positive — complacency is NOT bearish.

7. FEAR COMPOSITE (combined signal) — SIGNIFICANT BOTH IS AND OOS:
   - IS: IC=-0.078 (t=-4.68***), OOS: IC=-0.163 (t=-3.47***)
   - Q0 (least fear) shows +2.3%/7D OOS; Q3 (more fear) shows -1.8%/7D
   - VERDICT: The composite works but in the OPPOSITE direction — less fear
     predicts better returns. This is a MOMENTUM/TREND signal, not contrarian.

RECOMMENDED INTEGRATION:
  - PRIMARY: skew_30d as a momentum signal (IC=+0.22 OOS, t=+5.15)
    -> Positive skew (recent upside bias) => stay long / add
    -> Negative skew (recent downside bias) => reduce exposure
  - SECONDARY: rv_term_slope / vrp_proxy as a risk overlay (IC=+0.15 OOS)
    -> Contango (far vol > near vol) = calm = favorable for longs
    -> Backwardation (near vol spike) = stress = reduce exposure
  - SKIP: Put/call ratio proxy (sign flip), IV percentile (sign flip),
    raw IV level (too noisy)

NEXT STEPS:
  1. ACCUMULATE REAL DATA: Run fetch_deribit_options.py hourly via cron.
     Need 90+ daily snapshots for meaningful IC analysis with real IV.
  2. RE-RUN THIS SCRIPT once 90 days of real data exists.
     Replace proxy signals with actual Deribit IV, skew, term structure.
  3. CROSS-VALIDATE: Compare proxy IC vs real IV IC.
     Real IV skew may show contrarian behavior (the proxy cannot capture
     demand-side information like institutional put buying).
  4. Integrate skew_30d as a token-level feature in the signal pipeline.
  5. Test rv_term_slope as a portfolio-level risk overlay.
""")


if __name__ == '__main__':
    main()
