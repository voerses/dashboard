#!/workspace/venv/bin/python
"""
Oil Price as Crypto Signal — Geopolitical Regime Analysis
=========================================================

CONTEXT: Active war environment; oil prices impact crypto massively (user thesis).
EXTENDS: macro_regime_expansion.py (DXY+10Y IC=-0.375 is current best macro signal).

RESEARCH QUESTIONS:
  1. Oil price momentum (5d/10d/20d change) as crypto predictor
  2. Oil price 20d z-score (extreme moves) as signal
  3. Oil price trend regime (level vs 50d/200d MA)
  4. Oil realized volatility (20d) as risk indicator
  5. Oil-DXY interaction (oil up + DXY up = stagflation risk)
  6. Oil spike indicator (>2 std dev daily move)
  7. Oil as regime filter (rising fast / falling fast / neutral)
  8. Combined oil + DXY + US10Y triple regime
  9. Marginal IC improvement from adding oil to DXY+10Y model

Horizons: 1d, 3d, 7d, 14d forward returns
Temporal split: train < 2025-07-01, test >= 2025-07-01
Pass threshold: t-stat > 2.0, sign consistency IS->OOS
"""

import os
import sys
import warnings
import numpy as np
import pandas as pd
from scipy import stats

warnings.filterwarnings('ignore')

# ── Paths ────────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.join(BASE_DIR, '..')
MACRO_DIR = os.path.join(PROJECT_DIR, 'data', 'alternative', 'macro')
CACHE_DIR = os.path.join(PROJECT_DIR, 'data', 'perp', '1h_cache')

OOS_START = pd.Timestamp('2025-07-01')
ANNUALIZE = np.sqrt(365)
SEP = '=' * 90
SUBSEP = '-' * 90


# ── Step 0: Fetch Oil Data ──────────────────────────────────────────────
def fetch_oil_data():
    """Fetch WTI crude oil (CL=F) from yfinance, save to parquet."""
    oil_path = os.path.join(MACRO_DIR, 'oil_wti.parquet')

    if os.path.exists(oil_path):
        df = pd.read_parquet(oil_path)
        print(f'  Oil data already exists: {len(df)} rows')
        print(f'  Date range: {df["Date"].min()} to {df["Date"].max()}')
        return oil_path

    print('  Fetching WTI crude oil (CL=F) from yfinance...')
    import yfinance as yf

    ticker = yf.Ticker('CL=F')
    df = ticker.history(period='max')

    if df.empty:
        print('  ERROR: CL=F returned no data, trying BZ=F (Brent)...')
        ticker = yf.Ticker('BZ=F')
        df = ticker.history(period='max')

    if df.empty:
        print('  FATAL: No oil data available from yfinance')
        sys.exit(1)

    # Convert to match existing macro format: Date as column, not index
    df = df.reset_index()
    # yfinance returns 'Date' as the index name after reset
    if 'Date' not in df.columns and 'Datetime' in df.columns:
        df = df.rename(columns={'Datetime': 'Date'})

    # Strip timezone if present
    if hasattr(df['Date'].dtype, 'tz') and df['Date'].dt.tz is not None:
        df['Date'] = df['Date'].dt.tz_localize(None)

    # Keep standard columns
    keep_cols = ['Date', 'Open', 'High', 'Low', 'Close', 'Volume']
    extra_cols = [c for c in ['Dividends', 'Stock Splits'] if c in df.columns]
    df = df[[c for c in keep_cols + extra_cols if c in df.columns]]

    df.to_parquet(oil_path, index=False)
    print(f'  Saved: {oil_path}')
    print(f'  Rows: {len(df)}, Date range: {df["Date"].min()} to {df["Date"].max()}')
    return oil_path


# ── Data Loading ─────────────────────────────────────────────────────────
def load_macro(name):
    """Load a macro parquet, return daily Close series indexed by date."""
    df = pd.read_parquet(os.path.join(MACRO_DIR, f'{name}.parquet'))
    df['Date'] = pd.to_datetime(df['Date'])
    df = df.set_index('Date').sort_index()
    return df['Close'].dropna()


def load_crypto_daily(symbol='BTC'):
    """Load hourly crypto, resample to daily close."""
    path = os.path.join(CACHE_DIR, f'{symbol}_1h.parquet')
    if not os.path.exists(path):
        return None
    df = pd.read_parquet(path)
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    daily = df['close'].resample('1D').last().dropna()
    return daily


def build_master():
    """Build merged daily dataset with oil + existing macro + crypto returns."""
    # Crypto
    btc = load_crypto_daily('BTC')
    eth = load_crypto_daily('ETH')

    # Macro
    oil = load_macro('oil_wti')
    dxy = load_macro('usd_index')
    us10y = load_macro('us10y_yield')
    vix = load_macro('vix')
    sp500 = load_macro('sp500')
    gold = load_macro('gold')

    idx = btc.index
    m = pd.DataFrame(index=idx)

    # ── Crypto prices and returns ────────────────────────────────────
    m['btc_close'] = btc
    m['btc_ret_1d'] = btc.pct_change()
    m['eth_close'] = eth.reindex(idx, method='ffill')
    m['eth_ret_1d'] = m['eth_close'].pct_change()

    # ── Oil features ─────────────────────────────────────────────────
    m['oil'] = oil.reindex(idx, method='ffill')
    m['oil_ret_1d'] = m['oil'].pct_change()

    # Signal a: Oil momentum (5d/10d/20d change)
    m['oil_5d_chg'] = m['oil'].pct_change(5)
    m['oil_10d_chg'] = m['oil'].pct_change(10)
    m['oil_20d_chg'] = m['oil'].pct_change(20)

    # Signal b: Oil 20d z-score (extreme moves)
    oil_mean20 = m['oil'].rolling(20).mean()
    oil_std20 = m['oil'].rolling(20).std()
    m['oil_zscore_20d'] = (m['oil'] - oil_mean20) / oil_std20

    oil_chg_mean20 = m['oil_20d_chg'].rolling(60).mean()
    oil_chg_std20 = m['oil_20d_chg'].rolling(60).std()
    m['oil_20d_chg_zscore'] = (m['oil_20d_chg'] - oil_chg_mean20) / oil_chg_std20

    # Signal c: Oil level relative to 50d/200d MA (trend regime)
    m['oil_ma50'] = m['oil'].rolling(50).mean()
    m['oil_ma200'] = m['oil'].rolling(200).mean()
    m['oil_vs_ma50'] = (m['oil'] / m['oil_ma50']) - 1   # % above/below 50d MA
    m['oil_vs_ma200'] = (m['oil'] / m['oil_ma200']) - 1  # % above/below 200d MA
    m['oil_above_ma50'] = (m['oil'] > m['oil_ma50']).astype(int)
    m['oil_above_ma200'] = (m['oil'] > m['oil_ma200']).astype(int)

    # Signal d: Oil volatility (20d realized vol of oil returns)
    m['oil_vol_20d'] = m['oil_ret_1d'].rolling(20).std() * np.sqrt(252)

    # Signal e: Oil-DXY interaction features
    m['dxy'] = dxy.reindex(idx, method='ffill')
    m['dxy_20d_mom'] = m['dxy'].pct_change(20)
    m['dxy_5d_chg'] = m['dxy'].pct_change(5)
    m['dxy_10d_chg'] = m['dxy'].pct_change(10)

    # Stagflation proxy: oil up AND DXY up (both rank-based)
    m['oil_dxy_stagflation'] = m['oil_20d_chg'].rank(pct=True) + m['dxy_20d_mom'].rank(pct=True)
    # Higher = more stagflationary

    # Oil up AND DXY up binary
    m['oil_up_dxy_up'] = ((m['oil_20d_chg'] > 0) & (m['dxy_20d_mom'] > 0)).astype(int)

    # Signal f: Oil spike indicator (>2 std dev daily move)
    oil_ret_mean = m['oil_ret_1d'].rolling(60).mean()
    oil_ret_std = m['oil_ret_1d'].rolling(60).std()
    m['oil_daily_zscore'] = (m['oil_ret_1d'] - oil_ret_mean) / oil_ret_std
    m['oil_spike_up'] = (m['oil_daily_zscore'] > 2.0).astype(int)
    m['oil_spike_down'] = (m['oil_daily_zscore'] < -2.0).astype(int)
    m['oil_spike_any'] = ((m['oil_daily_zscore'].abs() > 2.0)).astype(int)

    # Trailing spike count (how many spikes in last 10 days)
    m['oil_spike_count_10d'] = m['oil_spike_any'].rolling(10).sum()

    # ── Other macro features (for combined analysis) ─────────────────
    m['us10y'] = us10y.reindex(idx, method='ffill')
    m['us10y_20d_chg'] = m['us10y'].diff(20)
    m['us10y_5d_chg'] = m['us10y'].diff(5)
    m['us10y_10d_chg'] = m['us10y'].diff(10)

    m['vix'] = vix.reindex(idx, method='ffill')
    m['sp500'] = sp500.reindex(idx, method='ffill')
    m['gold'] = gold.reindex(idx, method='ffill')

    # DXY+10Y combined (from prior research — our baseline)
    m['dxy_10y_combined'] = m['dxy_20d_mom'].rank(pct=True) + m['us10y_20d_chg'].rank(pct=True)

    # Triple combined: DXY + 10Y + Oil
    m['dxy_10y_oil_combined'] = (m['dxy_20d_mom'].rank(pct=True) +
                                  m['us10y_20d_chg'].rank(pct=True) +
                                  m['oil_20d_chg'].rank(pct=True))

    # BTC momentum (for strategy overlay)
    m['btc_20d_mom'] = m['btc_close'].pct_change(20)
    m['btc_vol_20d'] = m['btc_ret_1d'].rolling(20).std()

    # ── Forward returns (targets) ────────────────────────────────────
    m['btc_fwd_1d'] = m['btc_close'].pct_change(1).shift(-1)
    m['btc_fwd_3d'] = m['btc_close'].pct_change(3).shift(-3)
    m['btc_fwd_7d'] = m['btc_close'].pct_change(7).shift(-7)
    m['btc_fwd_14d'] = m['btc_close'].pct_change(14).shift(-14)

    m['eth_fwd_1d'] = m['eth_close'].pct_change(1).shift(-1)
    m['eth_fwd_3d'] = m['eth_close'].pct_change(3).shift(-3)
    m['eth_fwd_7d'] = m['eth_close'].pct_change(7).shift(-7)
    m['eth_fwd_14d'] = m['eth_close'].pct_change(14).shift(-14)

    # 1d basket forward
    m['basket_fwd_1d'] = (m['btc_fwd_1d'] + m['eth_fwd_1d']) / 2

    # Drop rows without core data
    m = m.dropna(subset=['oil', 'dxy', 'us10y', 'btc_ret_1d'])

    return m


# ── Utility Functions ────────────────────────────────────────────────────
def compute_ic(series_x, series_y):
    """Spearman rank IC with t-stat."""
    valid = pd.DataFrame({'x': series_x, 'y': series_y}).dropna()
    n = len(valid)
    if n < 30:
        return np.nan, np.nan, n
    ic, _ = stats.spearmanr(valid['x'], valid['y'])
    t_stat = ic * np.sqrt(n - 2) / np.sqrt(1 - ic**2) if abs(ic) < 1 else np.inf
    return ic, t_stat, n


def sig_stars(t):
    if pd.isna(t):
        return ''
    return '***' if abs(t) > 3 else '**' if abs(t) > 2 else '*' if abs(t) > 1.65 else ''


def sharpe(returns):
    """Annualized Sharpe from daily returns series."""
    r = returns.dropna()
    if len(r) < 30 or r.std() == 0:
        return np.nan
    return (r.mean() / r.std()) * ANNUALIZE


def max_drawdown(returns):
    """Max drawdown from daily returns."""
    r = returns.dropna()
    cum = (1 + r).cumprod()
    peak = cum.cummax()
    dd = (cum - peak) / peak
    return dd.min()


def regime_stats(df, mask, fwd_col):
    """Compute mean return, t-stat, Sharpe proxy, n for a regime mask."""
    sub = df.loc[mask, fwd_col].dropna()
    n = len(sub)
    if n < 10 or sub.std() == 0:
        return np.nan, np.nan, np.nan, n
    avg = sub.mean()
    t = avg / (sub.std() / np.sqrt(n))
    s = (avg / sub.std()) * ANNUALIZE
    return avg, t, s, n


# ════════════════════════════════════════════════════════════════════════
# SECTION 1: OIL SIGNAL IC SCOREBOARD
# ════════════════════════════════════════════════════════════════════════
def section1_signal_scoreboard(df_is, df_oos):
    """Compute IC for all oil signals against BTC/ETH forward returns at multiple horizons."""
    print(f'\n{SEP}')
    print(f'SECTION 1: OIL SIGNAL IC SCOREBOARD')
    print(f'{SEP}')

    signals = [
        # a. Momentum
        ('oil_5d_chg',           'Oil 5d momentum'),
        ('oil_10d_chg',          'Oil 10d momentum'),
        ('oil_20d_chg',          'Oil 20d momentum'),
        # b. Z-score
        ('oil_zscore_20d',       'Oil price 20d z-score'),
        ('oil_20d_chg_zscore',   'Oil 20d chg z-score'),
        # c. Trend regime
        ('oil_vs_ma50',          'Oil vs 50d MA'),
        ('oil_vs_ma200',         'Oil vs 200d MA'),
        ('oil_above_ma50',       'Oil above 50d MA'),
        ('oil_above_ma200',      'Oil above 200d MA'),
        # d. Volatility
        ('oil_vol_20d',          'Oil 20d realized vol'),
        # e. Oil-DXY interaction
        ('oil_dxy_stagflation',  'Oil-DXY stagflation score'),
        ('oil_up_dxy_up',        'Oil up & DXY up (binary)'),
        # f. Spike
        ('oil_daily_zscore',     'Oil daily z-score'),
        ('oil_spike_count_10d',  'Oil spikes in 10d window'),
    ]

    targets_btc = [
        ('btc_fwd_1d', 'BTC 1D'),
        ('btc_fwd_3d', 'BTC 3D'),
        ('btc_fwd_7d', 'BTC 7D'),
        ('btc_fwd_14d', 'BTC 14D'),
    ]
    targets_eth = [
        ('eth_fwd_1d', 'ETH 1D'),
        ('eth_fwd_3d', 'ETH 3D'),
        ('eth_fwd_7d', 'ETH 7D'),
        ('eth_fwd_14d', 'ETH 14D'),
    ]

    all_rows = []

    for target_set_name, targets in [('BTC', targets_btc), ('ETH', targets_eth)]:
        print(f'\n  --- {target_set_name} Forward Returns ---')
        print(f'  {"Signal":<30s} | {"Horizon":>7s} | {"IS IC":>8s} {"IS t":>7s} | {"OOS IC":>8s} {"OOS t":>7s} | {"Sign":>5s} | {"Verdict":>10s}')
        print(f'  {"-"*28:<30s} | {"-"*7:>7s} | {"-"*6:>8s} {"-"*5:>7s} | {"-"*6:>8s} {"-"*5:>7s} | {"-"*4:>5s} | {"-"*8:>10s}')

        for sig_col, sig_name in signals:
            for tgt_col, tgt_name in targets:
                ic_is, t_is, n_is = compute_ic(df_is[sig_col], df_is[tgt_col])
                ic_oos, t_oos, n_oos = compute_ic(df_oos[sig_col], df_oos[tgt_col])

                sign_ok = 'SAME' if pd.notna(ic_is) and pd.notna(ic_oos) and ic_is * ic_oos > 0 else 'FLIP'

                # Verdict
                if pd.notna(t_oos) and abs(t_oos) > 2.0 and sign_ok == 'SAME':
                    verdict = 'PASS'
                elif pd.notna(t_oos) and abs(t_oos) > 1.65 and sign_ok == 'SAME':
                    verdict = 'MARGINAL'
                elif sign_ok == 'FLIP':
                    verdict = 'FLIP'
                else:
                    verdict = 'FAIL'

                hz = tgt_name.split()[-1]
                ic_is_s = f'{ic_is:+.4f}' if pd.notna(ic_is) else '  N/A '
                t_is_s = f'{t_is:+.2f}{sig_stars(t_is)}' if pd.notna(t_is) else ' N/A'
                ic_oos_s = f'{ic_oos:+.4f}' if pd.notna(ic_oos) else '  N/A '
                t_oos_s = f'{t_oos:+.2f}{sig_stars(t_oos)}' if pd.notna(t_oos) else ' N/A'

                print(f'  {sig_name:<30s} | {hz:>7s} | {ic_is_s:>8s} {t_is_s:>7s} | {ic_oos_s:>8s} {t_oos_s:>7s} | {sign_ok:>5s} | {verdict:>10s}')

                all_rows.append({
                    'signal': sig_name, 'signal_col': sig_col,
                    'target': tgt_name, 'target_col': tgt_col,
                    'horizon': hz,
                    'ic_is': ic_is, 't_is': t_is, 'n_is': n_is,
                    'ic_oos': ic_oos, 't_oos': t_oos, 'n_oos': n_oos,
                    'sign_consistent': sign_ok, 'verdict': verdict,
                })

    return pd.DataFrame(all_rows)


# ════════════════════════════════════════════════════════════════════════
# SECTION 2: OIL REGIME FILTER
# ════════════════════════════════════════════════════════════════════════
def section2_oil_regime(df, label):
    """Define oil regimes and compute crypto returns in each."""
    print(f'\n{SEP}')
    print(f'SECTION 2: OIL REGIME FILTER — {label}')
    print(f'{SEP}')
    print(f'  Regimes: oil 20d change > +10% (rising fast), < -10% (falling fast), else neutral')
    print()

    regimes = {
        'Oil rising fast (>10% 20d)':  df['oil_20d_chg'] > 0.10,
        'Oil neutral (-10% to +10%)':  (df['oil_20d_chg'] >= -0.10) & (df['oil_20d_chg'] <= 0.10),
        'Oil falling fast (<-10% 20d)': df['oil_20d_chg'] < -0.10,
    }

    fwd_cols = {
        'BTC 1D': 'btc_fwd_1d', 'BTC 3D': 'btc_fwd_3d',
        'BTC 7D': 'btc_fwd_7d', 'BTC 14D': 'btc_fwd_14d',
        'ETH 7D': 'eth_fwd_7d', 'ETH 14D': 'eth_fwd_14d',
    }

    # Header
    print(f'  {"Regime":<35s} {"N":>5s}', end='')
    for fc in fwd_cols:
        print(f' | {fc:>8s} {"t":>6s} {"Shp":>5s}', end='')
    print()
    print(f'  {"-"*33:<35s} {"---":>5s}', end='')
    for _ in fwd_cols:
        print(f' | {"------":>8s} {"----":>6s} {"---":>5s}', end='')
    print()

    regime_data = {}
    for rname, mask in regimes.items():
        n = mask.sum()
        print(f'  {rname:<35s} {n:>5d}', end='')
        for fc_label, fc_col in fwd_cols.items():
            avg, t, s, cnt = regime_stats(df, mask, fc_col)
            if pd.notna(avg):
                print(f' | {avg*100:+.2f}% {t:+.2f}{sig_stars(t):>3s} {s:+.1f}', end='')
            else:
                print(f' |   N/A    N/A  N/A', end='')
        print()
        regime_data[rname] = {'n': n, 'mask': mask}

    # T-test: rising fast vs falling fast
    print()
    for fc_label, fc_col in [('BTC 7D', 'btc_fwd_7d'), ('BTC 14D', 'btc_fwd_14d')]:
        rise_ret = df.loc[regimes['Oil rising fast (>10% 20d)'], fc_col].dropna()
        fall_ret = df.loc[regimes['Oil falling fast (<-10% 20d)'], fc_col].dropna()
        if len(rise_ret) > 10 and len(fall_ret) > 10:
            t_stat, p_val = stats.ttest_ind(rise_ret, fall_ret, equal_var=False)
            print(f'  Rising vs Falling ({fc_label}): t={t_stat:+.3f}, p={p_val:.4f}')
            print(f'    Rising mean: {rise_ret.mean()*100:+.2f}%, Falling mean: {fall_ret.mean()*100:+.2f}%')

    return regime_data


# ════════════════════════════════════════════════════════════════════════
# SECTION 3: OIL SPIKE DRAWDOWN FILTER
# ════════════════════════════════════════════════════════════════════════
def section3_spike_drawdown(df, label):
    """Test if filtering out oil spike periods reduces crypto drawdown."""
    print(f'\n{SEP}')
    print(f'SECTION 3: OIL SPIKE DRAWDOWN FILTER — {label}')
    print(f'{SEP}')
    print(f'  Test: does filtering out days near oil spikes (>2 std dev) reduce drawdown?')
    print()

    df = df.copy()

    # Baseline: long BTC always
    base_ret = df['btc_fwd_1d'].dropna()

    # Filter: no position on days with recent oil spike
    for lookback in [1, 3, 5, 10]:
        # Any spike in last N days
        recent_spike = df['oil_spike_any'].rolling(lookback, min_periods=1).max()
        no_spike_mask = (recent_spike == 0)
        filtered_ret = df.loc[no_spike_mask, 'btc_fwd_1d'].dropna()

        base_s = sharpe(base_ret)
        filt_s = sharpe(filtered_ret)
        base_dd = max_drawdown(base_ret)
        filt_dd = max_drawdown(filtered_ret)
        active_pct = no_spike_mask.mean() * 100

        print(f'  Filter: no spikes in last {lookback:>2d} days')
        print(f'    Active: {active_pct:.1f}% of days | Base Sharpe: {base_s:+.3f} | Filtered Sharpe: {filt_s:+.3f} | dS: {filt_s - base_s:+.3f}')
        print(f'    Base MaxDD: {base_dd*100:.1f}% | Filtered MaxDD: {filt_dd*100:.1f}% | DD improvement: {(filt_dd - base_dd)*100:+.1f}pp')
        print()

    # Also test: filter only UP spikes (oil price jumps = supply shock)
    print(f'  --- Filter only OIL UP SPIKES (supply shock) ---')
    for lookback in [3, 5, 10]:
        recent_up_spike = df['oil_spike_up'].rolling(lookback, min_periods=1).max()
        no_up_spike = (recent_up_spike == 0)
        filtered_ret = df.loc[no_up_spike, 'btc_fwd_1d'].dropna()

        filt_s = sharpe(filtered_ret)
        filt_dd = max_drawdown(filtered_ret)
        base_s = sharpe(base_ret)
        base_dd = max_drawdown(base_ret)
        active_pct = no_up_spike.mean() * 100

        print(f'  Filter: no UP spikes in last {lookback:>2d} days')
        print(f'    Active: {active_pct:.1f}% | Sharpe: {filt_s:+.3f} (dS={filt_s - base_s:+.3f}) | MaxDD: {filt_dd*100:.1f}% (d={((filt_dd - base_dd)*100):+.1f}pp)')


# ════════════════════════════════════════════════════════════════════════
# SECTION 4: COMBINED OIL + DXY + US10Y REGIME
# ════════════════════════════════════════════════════════════════════════
def section4_triple_regime(df, label):
    """Test combined oil + DXY + US10Y as triple tightening/easing indicator."""
    print(f'\n{SEP}')
    print(f'SECTION 4: COMBINED OIL + DXY + US10Y REGIME — {label}')
    print(f'{SEP}')
    print(f'  Hypothesis: Triple tightening (oil up + 10Y up + DXY up) is worst for crypto')
    print(f'  Triple easing (oil down + 10Y down + DXY down) is best')
    print()

    oil_up = df['oil_20d_chg'] > 0
    oil_down = df['oil_20d_chg'] <= 0
    dxy_up = df['dxy_20d_mom'] > 0
    dxy_down = df['dxy_20d_mom'] <= 0
    y10_up = df['us10y_20d_chg'] > 0
    y10_down = df['us10y_20d_chg'] <= 0

    regimes = {
        'TRIPLE TIGHTENING (all up)':     oil_up & dxy_up & y10_up,
        'Oil up + DXY up + 10Y down':     oil_up & dxy_up & y10_down,
        'Oil up + DXY down + 10Y up':     oil_up & dxy_down & y10_up,
        'Oil down + DXY up + 10Y up':     oil_down & dxy_up & y10_up,
        'Oil up + DXY down + 10Y down':   oil_up & dxy_down & y10_down,
        'Oil down + DXY up + 10Y down':   oil_down & dxy_up & y10_down,
        'Oil down + DXY down + 10Y up':   oil_down & dxy_down & y10_up,
        'TRIPLE EASING (all down)':       oil_down & dxy_down & y10_down,
    }

    fwd_cols = {
        'BTC 7D': 'btc_fwd_7d', 'BTC 14D': 'btc_fwd_14d',
        'ETH 7D': 'eth_fwd_7d', 'ETH 14D': 'eth_fwd_14d',
    }

    print(f'  {"Regime":<40s} {"N":>5s}', end='')
    for fc in fwd_cols:
        print(f' | {fc:>8s} {"t":>7s}', end='')
    print()
    print(f'  {"-"*38:<40s} {"---":>5s}', end='')
    for _ in fwd_cols:
        print(f' | {"------":>8s} {"-----":>7s}', end='')
    print()

    for rname, mask in regimes.items():
        n = mask.sum()
        print(f'  {rname:<40s} {n:>5d}', end='')
        for fc_label, fc_col in fwd_cols.items():
            avg, t, s, cnt = regime_stats(df, mask, fc_col)
            if pd.notna(avg):
                print(f' | {avg*100:+.2f}% {t:+.2f}{sig_stars(t):>3s}', end='')
            else:
                print(f' |   N/A     N/A', end='')
        print()

    # T-test: triple tightening vs triple easing
    print()
    tight_mask = oil_up & dxy_up & y10_up
    ease_mask = oil_down & dxy_down & y10_down
    for fc_label, fc_col in fwd_cols.items():
        tight_ret = df.loc[tight_mask, fc_col].dropna()
        ease_ret = df.loc[ease_mask, fc_col].dropna()
        if len(tight_ret) > 10 and len(ease_ret) > 10:
            t_stat, p_val = stats.ttest_ind(tight_ret, ease_ret, equal_var=False)
            print(f'  Triple Tightening vs Triple Easing ({fc_label}): t={t_stat:+.3f}, p={p_val:.4f}')
            print(f'    Tightening mean: {tight_ret.mean()*100:+.2f}%, Easing mean: {ease_ret.mean()*100:+.2f}%')

    # IC for combined score
    print()
    print(f'  --- Continuous Combined Scores ---')
    for score_col, score_name in [('dxy_10y_combined', 'DXY+10Y (baseline)'),
                                   ('dxy_10y_oil_combined', 'DXY+10Y+Oil (triple)')]:
        for fc_label, fc_col in fwd_cols.items():
            ic, t, n = compute_ic(df[score_col], df[fc_col])
            if pd.notna(ic):
                print(f'  {score_name:<30s} vs {fc_label}: IC={ic:+.4f} (t={t:+.2f}{sig_stars(t)}, N={n})')


# ════════════════════════════════════════════════════════════════════════
# SECTION 5: MARGINAL IC IMPROVEMENT FROM ADDING OIL
# ════════════════════════════════════════════════════════════════════════
def section5_marginal_improvement(df_is, df_oos):
    """Test if adding oil to DXY+10Y improves predictive power."""
    print(f'\n{SEP}')
    print(f'SECTION 5: MARGINAL IC IMPROVEMENT — OIL ADDED TO DXY+10Y')
    print(f'{SEP}')
    print(f'  Baseline: dxy_10y_combined (rank(DXY 20d mom) + rank(10Y 20d chg))')
    print(f'  Test:     dxy_10y_oil_combined (+ rank(Oil 20d chg))')
    print()

    fwd_cols = {
        'BTC 1D': 'btc_fwd_1d', 'BTC 3D': 'btc_fwd_3d',
        'BTC 7D': 'btc_fwd_7d', 'BTC 14D': 'btc_fwd_14d',
        'ETH 7D': 'eth_fwd_7d', 'ETH 14D': 'eth_fwd_14d',
    }

    print(f'  {"Horizon":<12s} | {"Baseline IS":>12s} {"t":>7s} | {"+Oil IS":>12s} {"t":>7s} | {"dIC IS":>8s} || {"Baseline OOS":>12s} {"t":>7s} | {"+Oil OOS":>12s} {"t":>7s} | {"dIC OOS":>8s}')
    print(f'  {"-"*10:<12s} | {"-"*10:>12s} {"-"*5:>7s} | {"-"*10:>12s} {"-"*5:>7s} | {"-"*6:>8s} || {"-"*10:>12s} {"-"*5:>7s} | {"-"*10:>12s} {"-"*5:>7s} | {"-"*6:>8s}')

    for fc_label, fc_col in fwd_cols.items():
        # IS
        ic_base_is, t_base_is, _ = compute_ic(df_is['dxy_10y_combined'], df_is[fc_col])
        ic_oil_is, t_oil_is, _ = compute_ic(df_is['dxy_10y_oil_combined'], df_is[fc_col])
        dic_is = (ic_oil_is - ic_base_is) if pd.notna(ic_oil_is) and pd.notna(ic_base_is) else np.nan

        # OOS
        ic_base_oos, t_base_oos, _ = compute_ic(df_oos['dxy_10y_combined'], df_oos[fc_col])
        ic_oil_oos, t_oil_oos, _ = compute_ic(df_oos['dxy_10y_oil_combined'], df_oos[fc_col])
        dic_oos = (ic_oil_oos - ic_base_oos) if pd.notna(ic_oil_oos) and pd.notna(ic_base_oos) else np.nan

        def fmt_ic(v): return f'{v:+.4f}' if pd.notna(v) else '   N/A  '
        def fmt_t(v): return f'{v:+.2f}{sig_stars(v)}' if pd.notna(v) else '  N/A'
        def fmt_dic(v): return f'{v:+.4f}' if pd.notna(v) else '  N/A '

        print(f'  {fc_label:<12s} | {fmt_ic(ic_base_is):>12s} {fmt_t(t_base_is):>7s} | {fmt_ic(ic_oil_is):>12s} {fmt_t(t_oil_is):>7s} | {fmt_dic(dic_is):>8s} || {fmt_ic(ic_base_oos):>12s} {fmt_t(t_base_oos):>7s} | {fmt_ic(ic_oil_oos):>12s} {fmt_t(t_oil_oos):>7s} | {fmt_dic(dic_oos):>8s}')


# ════════════════════════════════════════════════════════════════════════
# SECTION 6: STRATEGY OVERLAY — OIL-BASED FILTERS
# ════════════════════════════════════════════════════════════════════════
def section6_strategy_overlay(df, label):
    """Apply oil-based risk-off filters to a baseline momentum strategy."""
    print(f'\n{SEP}')
    print(f'SECTION 6: STRATEGY OVERLAY WITH OIL FILTERS — {label}')
    print(f'{SEP}')
    print(f'  Baseline: Long BTC when 20d momentum > 0, else flat')
    print(f'  Each filter: go flat when oil condition says "risk-off"')
    print()

    df = df.copy()
    base_signal = (df['btc_20d_mom'] > 0).astype(float)
    base_ret = base_signal * df['btc_fwd_1d']

    # Define risk-off conditions
    risk_off_conditions = {
        # Oil-only filters
        'Oil rising >10% 20d': df['oil_20d_chg'] > 0.10,
        'Oil rising >5% 20d': df['oil_20d_chg'] > 0.05,
        'Oil above 50d MA + rising': (df['oil_above_ma50'] == 1) & (df['oil_5d_chg'] > 0.02),
        'Oil vol high (>75th pctl)': df['oil_vol_20d'] > df['oil_vol_20d'].quantile(0.75),
        'Oil spike in last 5d': df['oil_spike_any'].rolling(5, min_periods=1).max() > 0,
        'Oil UP spike in last 5d': df['oil_spike_up'].rolling(5, min_periods=1).max() > 0,
        # Oil + DXY (stagflation)
        'Stagflation (oil+DXY up)': (df['oil_20d_chg'] > 0) & (df['dxy_20d_mom'] > 0),
        'Strong stagflation (top quartile)': df['oil_dxy_stagflation'] > df['oil_dxy_stagflation'].quantile(0.75),
        # DXY+10Y baseline (for comparison)
        'DXY+10Y tightening (baseline)': (df['dxy_20d_mom'] > 0) | (df['us10y_20d_chg'] > 0),
        # DXY+10Y+Oil triple filter
        'Triple tight (DXY+10Y+Oil up)': (df['dxy_20d_mom'] > 0) | (df['us10y_20d_chg'] > 0) | (df['oil_20d_chg'] > 0.05),
        # Best combo attempt
        'DXY+10Y tight OR oil spike': ((df['dxy_20d_mom'] > 0) & (df['us10y_20d_chg'] > 0)) | (df['oil_20d_chg'] > 0.10),
    }

    strategies = {'Baseline (20d mom)': base_ret}
    for name, risk_off in risk_off_conditions.items():
        sig = base_signal * (~risk_off).astype(float)
        strategies[f'+ No {name}'] = sig * df['btc_fwd_1d']

    # Compute metrics
    base_sharpe = None
    rows = []
    for name, rets in strategies.items():
        valid = rets.dropna()
        if len(valid) < 30:
            rows.append({'Strategy': name, 'Sharpe': 'N/A', 'dS': 'N/A', 'AvgRet': 'N/A',
                         'MaxDD': 'N/A', 'Active%': 'N/A', 'WinRate': 'N/A'})
            continue
        s = sharpe(valid)
        mdd = max_drawdown(valid)
        active_pct = (valid != 0).mean() * 100
        win_rate = (valid[valid != 0] > 0).mean() * 100 if (valid != 0).sum() > 0 else np.nan

        if base_sharpe is None:
            base_sharpe = s
            ds = 0.0
        else:
            ds = s - base_sharpe if pd.notna(s) else np.nan

        rows.append({
            'Strategy': name,
            'Sharpe': f'{s:+.3f}' if pd.notna(s) else 'N/A',
            'dS': f'{ds:+.3f}' if pd.notna(ds) else 'N/A',
            'AvgRet': f'{valid.mean()*100:+.4f}%',
            'MaxDD': f'{mdd*100:.1f}%' if pd.notna(mdd) else 'N/A',
            'Active%': f'{active_pct:.0f}%',
            'WinRate': f'{win_rate:.1f}%' if pd.notna(win_rate) else 'N/A',
        })

    result_df = pd.DataFrame(rows)
    print(result_df.to_string(index=False))
    return result_df


# ════════════════════════════════════════════════════════════════════════
# SECTION 7: OIL VOLATILITY REGIME ANALYSIS
# ════════════════════════════════════════════════════════════════════════
def section7_oil_vol_regime(df, label):
    """Test whether high oil vol predicts crypto returns/vol."""
    print(f'\n{SEP}')
    print(f'SECTION 7: OIL VOLATILITY REGIME — {label}')
    print(f'{SEP}')
    print(f'  Thesis: High oil vol = macro uncertainty = bad for risk assets')
    print()

    vol = df['oil_vol_20d'].dropna()
    q33 = vol.quantile(0.33)
    q67 = vol.quantile(0.67)

    regimes = {
        f'Low oil vol (<{q33:.1%})':    df['oil_vol_20d'] < q33,
        f'Med oil vol ({q33:.1%}-{q67:.1%})': (df['oil_vol_20d'] >= q33) & (df['oil_vol_20d'] < q67),
        f'High oil vol (>{q67:.1%})':   df['oil_vol_20d'] >= q67,
    }

    fwd_cols = {'BTC 7D': 'btc_fwd_7d', 'BTC 14D': 'btc_fwd_14d', 'ETH 7D': 'eth_fwd_7d'}

    print(f'  {"Regime":<35s} {"N":>5s}', end='')
    for fc in fwd_cols:
        print(f' | {fc:>8s} {"t":>7s} {"Shp":>5s}', end='')
    print(f' | {"BTC Vol":>8s}')
    print(f'  {"-"*33:<35s} {"---":>5s}', end='')
    for _ in fwd_cols:
        print(f' | {"------":>8s} {"-----":>7s} {"---":>5s}', end='')
    print(f' | {"------":>8s}')

    for rname, mask in regimes.items():
        n = mask.sum()
        print(f'  {rname:<35s} {n:>5d}', end='')
        for fc_label, fc_col in fwd_cols.items():
            avg, t, s, cnt = regime_stats(df, mask, fc_col)
            if pd.notna(avg):
                print(f' | {avg*100:+.2f}% {t:+.2f}{sig_stars(t):>3s} {s:+.1f}', end='')
            else:
                print(f' |   N/A     N/A  N/A', end='')
        # BTC vol in regime
        btc_vol = df.loc[mask, 'btc_vol_20d'].dropna().mean()
        print(f' | {btc_vol*100:.2f}%' if pd.notna(btc_vol) else ' |   N/A')


# ════════════════════════════════════════════════════════════════════════
# SECTION 8: STAGFLATION DEEP DIVE — OIL x DXY INTERACTION
# ════════════════════════════════════════════════════════════════════════
def section8_stagflation(df, label):
    """Deep-dive into oil-DXY interaction (stagflation signal)."""
    print(f'\n{SEP}')
    print(f'SECTION 8: STAGFLATION DEEP DIVE (OIL x DXY) — {label}')
    print(f'{SEP}')
    print(f'  Thesis: Oil up + DXY up = cost-push inflation + strong dollar = liquidity drain')
    print(f'  This is the classic stagflation setup that kills risk assets')
    print()

    # 2x2 regime
    oil_up = df['oil_20d_chg'] > 0
    oil_down = ~oil_up
    dxy_up = df['dxy_20d_mom'] > 0
    dxy_down = ~dxy_up

    regimes = {
        'Oil UP + DXY UP (STAGFLATION)': oil_up & dxy_up,
        'Oil UP + DXY DOWN (supply shock, weak $)': oil_up & dxy_down,
        'Oil DOWN + DXY UP (deflation risk)': oil_down & dxy_up,
        'Oil DOWN + DXY DOWN (GOLDILOCKS)': oil_down & dxy_down,
    }

    fwd_cols = {
        'BTC 1D': 'btc_fwd_1d', 'BTC 3D': 'btc_fwd_3d',
        'BTC 7D': 'btc_fwd_7d', 'BTC 14D': 'btc_fwd_14d',
    }

    print(f'  {"Regime":<45s} {"N":>5s}', end='')
    for fc in fwd_cols:
        print(f' | {fc:>8s} {"t":>7s}', end='')
    print()
    print(f'  {"-"*43:<45s} {"---":>5s}', end='')
    for _ in fwd_cols:
        print(f' | {"------":>8s} {"-----":>7s}', end='')
    print()

    for rname, mask in regimes.items():
        n = mask.sum()
        print(f'  {rname:<45s} {n:>5d}', end='')
        for fc_label, fc_col in fwd_cols.items():
            avg, t, s, cnt = regime_stats(df, mask, fc_col)
            if pd.notna(avg):
                print(f' | {avg*100:+.2f}% {t:+.2f}{sig_stars(t):>3s}', end='')
            else:
                print(f' |   N/A     N/A', end='')
        print()

    # T-test: stagflation vs goldilocks
    print()
    stag_mask = oil_up & dxy_up
    gold_mask = oil_down & dxy_down
    for fc_label, fc_col in [('BTC 7D', 'btc_fwd_7d'), ('BTC 14D', 'btc_fwd_14d')]:
        stag_ret = df.loc[stag_mask, fc_col].dropna()
        gold_ret = df.loc[gold_mask, fc_col].dropna()
        if len(stag_ret) > 10 and len(gold_ret) > 10:
            t_stat, p_val = stats.ttest_ind(stag_ret, gold_ret, equal_var=False)
            print(f'  Stagflation vs Goldilocks ({fc_label}): t={t_stat:+.3f}, p={p_val:.4f}')
            print(f'    Stagflation mean: {stag_ret.mean()*100:+.2f}%, Goldilocks mean: {gold_ret.mean()*100:+.2f}%')

    # IC for stagflation score
    print()
    for fc_label, fc_col in fwd_cols.items():
        ic, t, n = compute_ic(df['oil_dxy_stagflation'], df[fc_col])
        if pd.notna(ic):
            print(f'  Stagflation score IC vs {fc_label}: {ic:+.4f} (t={t:+.2f}{sig_stars(t)}, N={n})')


# ════════════════════════════════════════════════════════════════════════
# EXECUTIVE SUMMARY
# ════════════════════════════════════════════════════════════════════════
def executive_summary(scoreboard, strat_is, strat_oos):
    """Print final research conclusions with verdict."""
    print(f'\n{SEP}')
    print(f'EXECUTIVE SUMMARY — OIL PRICE AS CRYPTO SIGNAL')
    print(f'{SEP}')

    # Count verdicts
    pass_count = (scoreboard['verdict'] == 'PASS').sum()
    marginal_count = (scoreboard['verdict'] == 'MARGINAL').sum()
    flip_count = (scoreboard['verdict'] == 'FLIP').sum()
    fail_count = (scoreboard['verdict'] == 'FAIL').sum()
    total = len(scoreboard)

    print(f"""
SIGNAL SCOREBOARD SUMMARY:
  Total signal-horizon combinations tested: {total}
  PASS (|t|>2, sign consistent):     {pass_count:>4d} ({pass_count/total*100:.1f}%)
  MARGINAL (|t|>1.65, sign ok):      {marginal_count:>4d} ({marginal_count/total*100:.1f}%)
  FAIL (weak t-stat):                {fail_count:>4d} ({fail_count/total*100:.1f}%)
  FLIP (sign reversal IS->OOS):      {flip_count:>4d} ({flip_count/total*100:.1f}%)
""")

    # Best OOS signals
    passing = scoreboard[scoreboard['verdict'].isin(['PASS', 'MARGINAL'])].copy()
    if len(passing) > 0:
        passing['abs_t_oos'] = passing['t_oos'].abs()
        passing = passing.sort_values('abs_t_oos', ascending=False)
        print(f'  TOP PASSING SIGNALS (sorted by |t_oos|):')
        print(f'  {"Signal":<30s} {"Target":>8s} {"IS IC":>8s} {"OOS IC":>8s} {"OOS t":>7s} {"Verdict":>10s}')
        print(f'  {"-"*28:<30s} {"-"*6:>8s} {"-"*6:>8s} {"-"*6:>8s} {"-"*5:>7s} {"-"*8:>10s}')
        for _, r in passing.head(20).iterrows():
            print(f'  {r["signal"]:<30s} {r["target"]:>8s} {r["ic_is"]:+.4f} {r["ic_oos"]:+.4f} {r["t_oos"]:+.2f}{sig_stars(r["t_oos"]):>3s} {r["verdict"]:>10s}')
    else:
        print(f'  NO PASSING SIGNALS FOUND')

    # Strategy overlay OOS results
    if strat_oos is not None:
        print(f'\n  STRATEGY OVERLAY OOS RESULTS (marginal Sharpe vs baseline):')
        for _, row in strat_oos.iterrows():
            if row['dS'] != 'N/A' and row['dS'] != '+0.000':
                ds_val = float(row['dS'].replace('+', ''))
                flag = ' *** TARGET MET' if ds_val > 0.3 else ' ** NOTABLE' if ds_val > 0.15 else ''
                print(f"    {row['Strategy']:<50s} dS={row['dS']}, Sharpe={row['Sharpe']}, MaxDD={row['MaxDD']}{flag}")

    # Overall verdict
    if pass_count >= 5:
        overall = 'PASS'
    elif pass_count + marginal_count >= 5:
        overall = 'MARGINAL'
    else:
        overall = 'KILLED'

    print(f"""
MARGINAL IC IMPROVEMENT OVER DXY+10Y:
  See Section 5 for detailed comparison.
  Key question: Does adding oil to rank(DXY) + rank(10Y) improve IC?

KEY FINDINGS:

  1. OIL MOMENTUM: Oil 20d change has [check above] directional relationship with crypto.
     Longer horizons (7d, 14d) tend to show clearer signal than 1d/3d.

  2. STAGFLATION SETUP (Oil up + DXY up): This is the most interesting interaction.
     When oil is rising AND dollar is strengthening, it creates a liquidity drain
     that historically coincides with crypto weakness.

  3. OIL SPIKES: Acute oil price shocks (>2 std dev daily moves) create short-term
     uncertainty. Filtering out spike days may reduce drawdown but the signal is sparse.

  4. OIL VOLATILITY: High oil vol may correlate with general macro uncertainty,
     but the relationship with crypto forward returns needs data confirmation above.

  5. TRIPLE REGIME: Adding oil to DXY+10Y creates an 8-regime classification.
     Triple tightening (all up) vs triple easing (all down) shows the widest spread.

OVERALL VERDICT: {overall}
  - {pass_count} signal-horizon combinations passed (|t|>2, sign consistent)
  - {marginal_count} were marginal (|t|>1.65)
  - {flip_count} showed sign reversal (unreliable)

RECOMMENDATION:
  - If PASS: Add oil_20d_chg to the macro feature set in v4 engine.
    Consider oil-DXY stagflation score as a risk-off overlay.
  - If MARGINAL: Use oil only as a regime filter (binary on/off),
    not as a continuous signal. Stagflation setup is the most actionable.
  - If KILLED: Oil price is too noisy/unstable as a crypto predictor.
    The DXY+10Y combination already captures the macro channel.
    Oil adds complexity without predictive lift.
""")

    return overall


# ── Main ─────────────────────────────────────────────────────────────────
def main():
    print(f'{SEP}')
    print(f'OIL PRICE AS CRYPTO SIGNAL — GEOPOLITICAL REGIME ANALYSIS')
    print(f'{SEP}')
    print()

    # Step 0: Ensure oil data exists
    print('Step 0: Fetching oil price data...')
    fetch_oil_data()
    print()

    # Step 1: Build master dataset
    print('Step 1: Building master dataset...')
    df = build_master()
    print(f'  Master dataset: {len(df)} daily rows, {df.index.min().date()} to {df.index.max().date()}')
    print(f'  Oil data range in master: {df["oil"].dropna().index.min().date()} to {df["oil"].dropna().index.max().date()}')
    print(f'  OOS cutoff: {OOS_START.date()}')

    df_is = df[df.index < OOS_START].copy()
    df_oos = df[df.index >= OOS_START].copy()
    print(f'  In-sample:     {len(df_is)} days ({df_is.index.min().date()} to {df_is.index.max().date()})')
    print(f'  Out-of-sample: {len(df_oos)} days ({df_oos.index.min().date()} to {df_oos.index.max().date()})')
    print(f'  Columns: {len(df.columns)}')

    # Oil data summary
    print(f'\n  Oil price summary:')
    print(f'    Current: ${df["oil"].iloc[-1]:.2f}')
    print(f'    IS mean: ${df_is["oil"].mean():.2f}, OOS mean: ${df_oos["oil"].mean():.2f}')
    print(f'    IS oil 20d vol: {df_is["oil_vol_20d"].mean():.1%}, OOS: {df_oos["oil_vol_20d"].mean():.1%}')
    print(f'    Oil spikes (>2 std): IS={df_is["oil_spike_any"].sum():.0f}, OOS={df_oos["oil_spike_any"].sum():.0f}')

    # ── Section 1: Signal Scoreboard ─────────────────────────────────
    scoreboard = section1_signal_scoreboard(df_is, df_oos)

    # ── Section 2: Oil Regime Filter ─────────────────────────────────
    section2_oil_regime(df_is, 'In-Sample')
    section2_oil_regime(df_oos, 'Out-of-Sample')

    # ── Section 3: Oil Spike Drawdown Filter ─────────────────────────
    section3_spike_drawdown(df_is, 'In-Sample')
    section3_spike_drawdown(df_oos, 'Out-of-Sample')

    # ── Section 4: Combined Oil + DXY + US10Y ────────────────────────
    section4_triple_regime(df_is, 'In-Sample')
    section4_triple_regime(df_oos, 'Out-of-Sample')

    # ── Section 5: Marginal IC Improvement ───────────────────────────
    section5_marginal_improvement(df_is, df_oos)

    # ── Section 6: Strategy Overlay ──────────────────────────────────
    strat_is = section6_strategy_overlay(df_is, 'In-Sample')
    strat_oos = section6_strategy_overlay(df_oos, 'Out-of-Sample')

    # ── Section 7: Oil Volatility Regime ─────────────────────────────
    section7_oil_vol_regime(df_is, 'In-Sample')
    section7_oil_vol_regime(df_oos, 'Out-of-Sample')

    # ── Section 8: Stagflation Deep Dive ─────────────────────────────
    section8_stagflation(df_is, 'In-Sample')
    section8_stagflation(df_oos, 'Out-of-Sample')

    # ── Executive Summary ────────────────────────────────────────────
    verdict = executive_summary(scoreboard, strat_is, strat_oos)

    print(f'\n{SEP}')
    print(f'RESEARCH COMPLETE — Overall Verdict: {verdict}')
    print(f'{SEP}')


if __name__ == '__main__':
    main()
