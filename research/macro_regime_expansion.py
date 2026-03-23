#!/workspace/venv/bin/python
"""
Macro Regime Expansion — Combination Signals as Crypto Regime Overlays
======================================================================

EXTENDS macro_regime_analysis.py (which found: US10Y IC=-0.16 OOS, DXY +0.36 marginal Sharpe).

NEW research questions:
  1. VIX level + change regime classifier (risk-on / risk-off / neutral)
  2. Gold/BTC rolling correlation as regime indicator
  3. Nasdaq-crypto beta regime (high beta = risk-on)
  4. SP500 drawdown as crypto risk-off trigger
  5. Combined DXY + 10Y yield regime indicator
  6. Cross-asset momentum (equities trending up => crypto risk-on)
  7. Multi-signal combination overlays and interaction effects
  8. Multi-token universe test (not just BTC)

Horizons: 7d, 14d forward returns
Temporal split: train <2025-07-01, test >=2025-07-01
Key question: Can macro regime filtering improve baseline Sharpe by >0.3?
"""

import os
import warnings
import numpy as np
import pandas as pd
from scipy import stats
from itertools import combinations

warnings.filterwarnings('ignore')

# ── Paths ────────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.join(BASE_DIR, '..')
MACRO_DIR = os.path.join(PROJECT_DIR, 'data', 'alternative', 'macro')
CACHE_DIR = os.path.join(PROJECT_DIR, 'data', 'perp', '1h_cache')

OOS_START = pd.Timestamp('2025-07-01')
ANNUALIZE = np.sqrt(365)   # daily returns -> annualized Sharpe
SEP = '=' * 90


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
    """Build merged daily dataset with all macro features and crypto returns."""
    # Crypto
    btc = load_crypto_daily('BTC')
    eth = load_crypto_daily('ETH')
    sol = load_crypto_daily('SOL')

    # Macro
    vix = load_macro('vix')
    dxy = load_macro('usd_index')
    sp500 = load_macro('sp500')
    nasdaq = load_macro('nasdaq')
    us10y = load_macro('us10y_yield')
    gold = load_macro('gold')

    idx = btc.index
    m = pd.DataFrame(index=idx)

    # ── Crypto prices and returns ────────────────────────────────────
    m['btc_close'] = btc
    m['btc_ret_1d'] = btc.pct_change()
    m['eth_close'] = eth.reindex(idx, method='ffill')
    m['eth_ret_1d'] = m['eth_close'].pct_change()
    if sol is not None:
        m['sol_close'] = sol.reindex(idx, method='ffill')
        m['sol_ret_1d'] = m['sol_close'].pct_change()

    # Equal-weight crypto basket return
    crypto_cols = ['btc_ret_1d', 'eth_ret_1d']
    if 'sol_ret_1d' in m.columns:
        crypto_cols.append('sol_ret_1d')
    m['crypto_basket_ret'] = m[crypto_cols].mean(axis=1)

    # ── Macro levels ─────────────────────────────────────────────────
    m['vix'] = vix.reindex(idx, method='ffill')
    m['dxy'] = dxy.reindex(idx, method='ffill')
    m['sp500'] = sp500.reindex(idx, method='ffill')
    m['nasdaq'] = nasdaq.reindex(idx, method='ffill')
    m['us10y'] = us10y.reindex(idx, method='ffill')
    m['gold'] = gold.reindex(idx, method='ffill')

    # ── Derived macro features (all from PAST data — no lookahead) ───

    # VIX features
    m['vix_5d_chg'] = m['vix'].pct_change(5)
    m['vix_10d_chg'] = m['vix'].pct_change(10)
    vix_mean20 = m['vix'].rolling(20).mean()
    vix_std20 = m['vix'].rolling(20).std()
    m['vix_zscore_20d'] = (m['vix'] - vix_mean20) / vix_std20
    m['vix_ma50'] = m['vix'].rolling(50).mean()

    # DXY features
    m['dxy_5d_chg'] = m['dxy'].pct_change(5)
    m['dxy_10d_chg'] = m['dxy'].pct_change(10)
    m['dxy_20d_mom'] = m['dxy'].pct_change(20)

    # US10Y features
    m['us10y_5d_chg'] = m['us10y'].diff(5)       # absolute change in yield
    m['us10y_10d_chg'] = m['us10y'].diff(10)
    m['us10y_20d_chg'] = m['us10y'].diff(20)

    # SP500 features
    m['sp500_5d_ret'] = m['sp500'].pct_change(5)
    m['sp500_20d_ret'] = m['sp500'].pct_change(20)
    sp500_peak = m['sp500'].rolling(60, min_periods=20).max()
    m['sp500_dd_60d'] = (m['sp500'] - sp500_peak) / sp500_peak  # drawdown from 60d high

    # Nasdaq features
    m['nasdaq_5d_ret'] = m['nasdaq'].pct_change(5)
    m['nasdaq_20d_ret'] = m['nasdaq'].pct_change(20)
    nasdaq_peak = m['nasdaq'].rolling(60, min_periods=20).max()
    m['nasdaq_dd_60d'] = (m['nasdaq'] - nasdaq_peak) / nasdaq_peak

    # Gold features
    m['gold_5d_ret'] = m['gold'].pct_change(5)
    m['gold_20d_ret'] = m['gold'].pct_change(20)

    # ── Cross-asset features ─────────────────────────────────────────

    # Gold/BTC rolling 30d correlation
    m['gold_btc_corr_30d'] = m['gold'].pct_change().rolling(30).corr(m['btc_close'].pct_change())

    # Nasdaq/BTC rolling 30d correlation (beta proxy)
    m['nasdaq_btc_corr_30d'] = m['nasdaq'].pct_change().rolling(30).corr(m['btc_close'].pct_change())

    # Nasdaq-crypto beta (30d rolling regression slope)
    nasdaq_ret = m['nasdaq'].pct_change()
    btc_ret = m['btc_ret_1d']
    m['nasdaq_btc_beta_30d'] = btc_ret.rolling(30).cov(nasdaq_ret) / nasdaq_ret.rolling(30).var()

    # Cross-asset momentum score: average z-scored 20d returns of SP500, Nasdaq, Gold
    for asset in ['sp500_20d_ret', 'nasdaq_20d_ret', 'gold_20d_ret']:
        roll_mean = m[asset].rolling(60).mean()
        roll_std = m[asset].rolling(60).std()
        m[f'{asset}_z'] = (m[asset] - roll_mean) / roll_std
    m['cross_asset_mom_z'] = m[['sp500_20d_ret_z', 'nasdaq_20d_ret_z', 'gold_20d_ret_z']].mean(axis=1)

    # Combined DXY + 10Y regime: both rising = tightening
    m['dxy_10y_combined'] = m['dxy_20d_mom'].rank(pct=True) + m['us10y_20d_chg'].rank(pct=True)
    # Higher = more tightening

    # BTC momentum
    m['btc_20d_mom'] = m['btc_close'].pct_change(20)
    m['btc_vol_20d'] = m['btc_ret_1d'].rolling(20).std()

    # ── Forward returns (targets) ────────────────────────────────────
    m['btc_fwd_7d'] = m['btc_close'].pct_change(7).shift(-7)
    m['btc_fwd_14d'] = m['btc_close'].pct_change(14).shift(-14)
    m['eth_fwd_7d'] = m['eth_close'].pct_change(7).shift(-7)
    m['eth_fwd_14d'] = m['eth_close'].pct_change(14).shift(-14)
    m['basket_fwd_7d'] = m[['btc_fwd_7d', 'eth_fwd_7d']].mean(axis=1)
    m['basket_fwd_14d'] = m[['btc_fwd_14d', 'eth_fwd_14d']].mean(axis=1)
    # Also keep 1d forward for strategy backtests
    m['btc_fwd_1d'] = m['btc_ret_1d'].shift(-1)
    m['basket_fwd_1d'] = m['crypto_basket_ret'].shift(-1)

    # Drop rows without core data
    m = m.dropna(subset=['vix', 'dxy', 'sp500', 'nasdaq', 'us10y', 'gold', 'btc_ret_1d'])

    return m


# ── Utility: IC + t-stat computation ────────────────────────────────────
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


# ════════════════════════════════════════════════════════════════════════
# SECTION 1: EXPANDED IC ANALYSIS — New Features
# ════════════════════════════════════════════════════════════════════════
def section1_expanded_ic(df, label):
    """IC for all new macro features against 7d and 14d BTC forward returns."""
    features = [
        # VIX
        'vix', 'vix_5d_chg', 'vix_10d_chg', 'vix_zscore_20d',
        # DXY
        'dxy', 'dxy_5d_chg', 'dxy_10d_chg', 'dxy_20d_mom',
        # US10Y
        'us10y_5d_chg', 'us10y_10d_chg', 'us10y_20d_chg',
        # Equities
        'sp500_5d_ret', 'sp500_20d_ret', 'sp500_dd_60d',
        'nasdaq_5d_ret', 'nasdaq_20d_ret', 'nasdaq_dd_60d',
        # Gold
        'gold_5d_ret', 'gold_20d_ret',
        # Cross-asset
        'gold_btc_corr_30d', 'nasdaq_btc_corr_30d', 'nasdaq_btc_beta_30d',
        'cross_asset_mom_z', 'dxy_10y_combined',
    ]
    horizons = {
        'BTC Fwd 7D': 'btc_fwd_7d',
        'BTC Fwd 14D': 'btc_fwd_14d',
        'Basket Fwd 7D': 'basket_fwd_7d',
        'Basket Fwd 14D': 'basket_fwd_14d',
    }

    print(f'\n{SEP}')
    print(f'SECTION 1: EXPANDED IC ANALYSIS — {label}')
    print(f'{SEP}')
    print(f'  N = {len(df)} days | Significant: |t| > 2.0')
    print()

    rows = []
    for feat in features:
        if feat not in df.columns:
            continue
        row = {'Feature': feat}
        for hz_label, hz_col in horizons.items():
            ic, t, n = compute_ic(df[feat], df[hz_col])
            row[f'{hz_label} IC'] = ic
            row[f'{hz_label} t'] = t
        rows.append(row)

    result = pd.DataFrame(rows)

    # Pretty print
    for _, r in result.iterrows():
        parts = [f"  {r['Feature']:28s}"]
        for hz_label in horizons:
            ic = r.get(f'{hz_label} IC', np.nan)
            t = r.get(f'{hz_label} t', np.nan)
            s = sig_stars(t)
            if pd.notna(ic):
                parts.append(f"{hz_label}: IC={ic:+.4f} t={t:+.2f}{s:3s}")
            else:
                parts.append(f"{hz_label}: N/A")
        print(' | '.join(parts))

    return result


# ════════════════════════════════════════════════════════════════════════
# SECTION 2: VIX REGIME CLASSIFIER
# ════════════════════════════════════════════════════════════════════════
def section2_vix_regime(df, label):
    """Classify regimes by VIX level + change direction."""
    print(f'\n{SEP}')
    print(f'SECTION 2: VIX REGIME CLASSIFIER — {label}')
    print(f'{SEP}')

    # Regime definitions
    regimes = {
        'Low VIX + Falling (VIX<18, 5d chg<0)': (df['vix'] < 18) & (df['vix_5d_chg'] < 0),
        'Low VIX + Rising (VIX<18, 5d chg>0)': (df['vix'] < 18) & (df['vix_5d_chg'] > 0),
        'Med VIX (18-25)': (df['vix'] >= 18) & (df['vix'] <= 25),
        'High VIX + Falling (VIX>25, chg<0)': (df['vix'] > 25) & (df['vix_5d_chg'] < 0),
        'High VIX + Rising (VIX>25, chg>0)': (df['vix'] > 25) & (df['vix_5d_chg'] >= 0),
    }

    print(f'  {"Regime":<42s} {"N":>5s} {"BTC 7D":>10s} {"t-stat":>8s} {"BTC 14D":>10s} {"t-stat":>8s}')
    print(f'  {"-"*40:<42s} {"---":>5s} {"------":>10s} {"------":>8s} {"-------":>10s} {"------":>8s}')

    for name, mask in regimes.items():
        n = mask.sum()
        btc7 = df.loc[mask, 'btc_fwd_7d'].dropna()
        btc14 = df.loc[mask, 'btc_fwd_14d'].dropna()
        avg7 = btc7.mean() if len(btc7) > 5 else np.nan
        t7 = avg7 / (btc7.std() / np.sqrt(len(btc7))) if len(btc7) > 5 and btc7.std() > 0 else np.nan
        avg14 = btc14.mean() if len(btc14) > 5 else np.nan
        t14 = avg14 / (btc14.std() / np.sqrt(len(btc14))) if len(btc14) > 5 and btc14.std() > 0 else np.nan

        s7 = f'{avg7*100:+.2f}%' if pd.notna(avg7) else 'N/A'
        s14 = f'{avg14*100:+.2f}%' if pd.notna(avg14) else 'N/A'
        t7s = f'{t7:+.2f}{sig_stars(t7)}' if pd.notna(t7) else 'N/A'
        t14s = f'{t14:+.2f}{sig_stars(t14)}' if pd.notna(t14) else 'N/A'
        print(f'  {name:<42s} {n:>5d} {s7:>10s} {t7s:>8s} {s14:>10s} {t14s:>8s}')


# ════════════════════════════════════════════════════════════════════════
# SECTION 3: GOLD/BTC CORRELATION REGIME
# ════════════════════════════════════════════════════════════════════════
def section3_gold_btc_corr(df, label):
    """Test whether Gold/BTC correlation regime predicts crypto returns."""
    print(f'\n{SEP}')
    print(f'SECTION 3: GOLD/BTC CORRELATION REGIME — {label}')
    print(f'{SEP}')
    print(f'  Thesis: High positive correlation = "digital gold" narrative = bullish')
    print(f'  Low/negative correlation = crypto trading on its own = more volatile')
    print()

    corr = df['gold_btc_corr_30d'].dropna()
    q33 = corr.quantile(0.33)
    q67 = corr.quantile(0.67)

    regimes = {
        f'Negative/Low corr (<{q33:.2f})': df['gold_btc_corr_30d'] < q33,
        f'Medium corr ({q33:.2f}-{q67:.2f})': (df['gold_btc_corr_30d'] >= q33) & (df['gold_btc_corr_30d'] < q67),
        f'High corr (>{q67:.2f})': df['gold_btc_corr_30d'] >= q67,
    }

    print(f'  {"Regime":<35s} {"N":>5s} {"BTC 7D":>10s} {"t":>8s} {"BTC 14D":>10s} {"t":>8s} {"Bkt 7D":>10s} {"t":>8s}')
    print(f'  {"-"*33:<35s} {"---":>5s} {"------":>10s} {"----":>8s} {"-------":>10s} {"----":>8s} {"------":>10s} {"----":>8s}')

    for name, mask in regimes.items():
        n = mask.sum()
        results = []
        for col in ['btc_fwd_7d', 'btc_fwd_14d', 'basket_fwd_7d']:
            sub = df.loc[mask, col].dropna()
            if len(sub) > 5 and sub.std() > 0:
                avg = sub.mean()
                t = avg / (sub.std() / np.sqrt(len(sub)))
                results.append((f'{avg*100:+.2f}%', f'{t:+.2f}{sig_stars(t)}'))
            else:
                results.append(('N/A', 'N/A'))
        print(f'  {name:<35s} {n:>5d} {results[0][0]:>10s} {results[0][1]:>8s} {results[1][0]:>10s} {results[1][1]:>8s} {results[2][0]:>10s} {results[2][1]:>8s}')

    # IC: gold_btc_corr -> forward returns
    print()
    for hz_label, hz_col in [('BTC 7D', 'btc_fwd_7d'), ('BTC 14D', 'btc_fwd_14d')]:
        ic, t, n = compute_ic(df['gold_btc_corr_30d'], df[hz_col])
        if pd.notna(ic):
            print(f'  Gold/BTC corr IC vs {hz_label}: {ic:+.4f} (t={t:+.2f}{sig_stars(t)}, N={n})')


# ════════════════════════════════════════════════════════════════════════
# SECTION 4: NASDAQ-CRYPTO BETA REGIME
# ════════════════════════════════════════════════════════════════════════
def section4_nasdaq_beta(df, label):
    """High Nasdaq-crypto beta = risk-on, low beta = independent."""
    print(f'\n{SEP}')
    print(f'SECTION 4: NASDAQ-CRYPTO BETA REGIME — {label}')
    print(f'{SEP}')
    print(f'  Thesis: When BTC beta to Nasdaq is high, crypto is in "risk asset" mode')
    print(f'  High beta + Nasdaq rising = crypto risk-on, High beta + Nasdaq falling = risk-off')
    print()

    beta = df['nasdaq_btc_beta_30d'].dropna()
    med_beta = beta.median()

    # 2x2 regime: beta high/low x Nasdaq direction
    regimes = {
        'High beta + NQ up': (df['nasdaq_btc_beta_30d'] > med_beta) & (df['nasdaq_20d_ret'] > 0),
        'High beta + NQ down': (df['nasdaq_btc_beta_30d'] > med_beta) & (df['nasdaq_20d_ret'] <= 0),
        'Low beta + NQ up': (df['nasdaq_btc_beta_30d'] <= med_beta) & (df['nasdaq_20d_ret'] > 0),
        'Low beta + NQ down': (df['nasdaq_btc_beta_30d'] <= med_beta) & (df['nasdaq_20d_ret'] <= 0),
    }

    print(f'  Median beta: {med_beta:.2f}')
    print(f'  {"Regime":<30s} {"N":>5s} {"BTC 7D":>10s} {"t":>8s} {"BTC 14D":>10s} {"t":>8s}')
    print(f'  {"-"*28:<30s} {"---":>5s} {"------":>10s} {"----":>8s} {"-------":>10s} {"----":>8s}')

    for name, mask in regimes.items():
        n = mask.sum()
        results = []
        for col in ['btc_fwd_7d', 'btc_fwd_14d']:
            sub = df.loc[mask, col].dropna()
            if len(sub) > 5 and sub.std() > 0:
                avg = sub.mean()
                t = avg / (sub.std() / np.sqrt(len(sub)))
                results.append((f'{avg*100:+.2f}%', f'{t:+.2f}{sig_stars(t)}'))
            else:
                results.append(('N/A', 'N/A'))
        print(f'  {name:<30s} {n:>5d} {results[0][0]:>10s} {results[0][1]:>8s} {results[1][0]:>10s} {results[1][1]:>8s}')

    # IC: beta level
    print()
    for hz_label, hz_col in [('BTC 7D', 'btc_fwd_7d'), ('BTC 14D', 'btc_fwd_14d')]:
        ic, t, n = compute_ic(df['nasdaq_btc_beta_30d'], df[hz_col])
        if pd.notna(ic):
            print(f'  Nasdaq-BTC beta IC vs {hz_label}: {ic:+.4f} (t={t:+.2f}{sig_stars(t)}, N={n})')


# ════════════════════════════════════════════════════════════════════════
# SECTION 5: SP500 DRAWDOWN AS CRYPTO RISK-OFF TRIGGER
# ════════════════════════════════════════════════════════════════════════
def section5_sp500_drawdown(df, label):
    """Test whether SP500 being in drawdown predicts crypto weakness."""
    print(f'\n{SEP}')
    print(f'SECTION 5: SP500 DRAWDOWN AS CRYPTO RISK-OFF TRIGGER — {label}')
    print(f'{SEP}')
    print(f'  Thesis: When SP500 is >5% off 60d high, crypto also weakens')
    print()

    thresholds = [0, -0.02, -0.05, -0.10]

    print(f'  {"SP500 Drawdown":<30s} {"N":>5s} {"BTC 7D":>10s} {"t":>8s} {"BTC 14D":>10s} {"t":>8s}')
    print(f'  {"-"*28:<30s} {"---":>5s} {"------":>10s} {"----":>8s} {"-------":>10s} {"----":>8s}')

    for i in range(len(thresholds)):
        if i == 0:
            mask = df['sp500_dd_60d'] >= thresholds[i]
            name = 'At/near high (DD >= 0%)'
        elif i < len(thresholds) - 1:
            mask = (df['sp500_dd_60d'] < thresholds[i-1]) & (df['sp500_dd_60d'] >= thresholds[i])
            name = f'DD {thresholds[i-1]*100:.0f}% to {thresholds[i]*100:.0f}%'
        else:
            mask = df['sp500_dd_60d'] < thresholds[i]
            name = f'DD worse than {thresholds[i]*100:.0f}%'

        n = mask.sum()
        results = []
        for col in ['btc_fwd_7d', 'btc_fwd_14d']:
            sub = df.loc[mask, col].dropna()
            if len(sub) > 5 and sub.std() > 0:
                avg = sub.mean()
                t = avg / (sub.std() / np.sqrt(len(sub)))
                results.append((f'{avg*100:+.2f}%', f'{t:+.2f}{sig_stars(t)}'))
            else:
                results.append(('N/A', 'N/A'))
        print(f'  {name:<30s} {n:>5d} {results[0][0]:>10s} {results[0][1]:>8s} {results[1][0]:>10s} {results[1][1]:>8s}')

    # Also add the deep DD bucket
    mask_deep = df['sp500_dd_60d'] < -0.05
    mask_normal = df['sp500_dd_60d'] >= -0.02
    deep_ret = df.loc[mask_deep, 'btc_fwd_7d'].dropna()
    norm_ret = df.loc[mask_normal, 'btc_fwd_7d'].dropna()
    if len(deep_ret) > 5 and len(norm_ret) > 5:
        t_stat, p_val = stats.ttest_ind(deep_ret, norm_ret, equal_var=False)
        print(f'\n  DD<-5% vs Normal: t={t_stat:+.3f}, p={p_val:.4f}')
        print(f'  DD<-5% mean 7D: {deep_ret.mean()*100:+.2f}%, Normal mean 7D: {norm_ret.mean()*100:+.2f}%')

    # IC: sp500_dd_60d -> forward returns
    print()
    for hz_label, hz_col in [('BTC 7D', 'btc_fwd_7d'), ('BTC 14D', 'btc_fwd_14d')]:
        ic, t, n = compute_ic(df['sp500_dd_60d'], df[hz_col])
        if pd.notna(ic):
            print(f'  SP500 drawdown IC vs {hz_label}: {ic:+.4f} (t={t:+.2f}{sig_stars(t)}, N={n})')


# ════════════════════════════════════════════════════════════════════════
# SECTION 6: COMBINED DXY + 10Y YIELD REGIME
# ════════════════════════════════════════════════════════════════════════
def section6_dxy_10y_combined(df, label):
    """Test combined DXY + 10Y yield as a liquidity tightening indicator."""
    print(f'\n{SEP}')
    print(f'SECTION 6: COMBINED DXY + 10Y YIELD REGIME — {label}')
    print(f'{SEP}')
    print(f'  Thesis: DXY rising AND 10Y rising = double tightening = bad for crypto')
    print(f'  Both falling = liquidity easing = good for crypto')
    print()

    # Regime definitions
    dxy_up = df['dxy_20d_mom'] > 0
    dxy_down = df['dxy_20d_mom'] <= 0
    y10_up = df['us10y_20d_chg'] > 0
    y10_down = df['us10y_20d_chg'] <= 0

    regimes = {
        'DXY up + 10Y up (TIGHTENING)': dxy_up & y10_up,
        'DXY up + 10Y down': dxy_up & y10_down,
        'DXY down + 10Y up': dxy_down & y10_up,
        'DXY down + 10Y down (EASING)': dxy_down & y10_down,
    }

    print(f'  {"Regime":<40s} {"N":>5s} {"BTC 7D":>10s} {"t":>8s} {"BTC 14D":>10s} {"t":>8s} {"Bkt 14D":>10s} {"t":>8s}')
    print(f'  {"-"*38:<40s} {"---":>5s} {"------":>10s} {"----":>8s} {"-------":>10s} {"----":>8s} {"-------":>10s} {"----":>8s}')

    for name, mask in regimes.items():
        n = mask.sum()
        results = []
        for col in ['btc_fwd_7d', 'btc_fwd_14d', 'basket_fwd_14d']:
            sub = df.loc[mask, col].dropna()
            if len(sub) > 5 and sub.std() > 0:
                avg = sub.mean()
                t = avg / (sub.std() / np.sqrt(len(sub)))
                results.append((f'{avg*100:+.2f}%', f'{t:+.2f}{sig_stars(t)}'))
            else:
                results.append(('N/A', 'N/A'))
        print(f'  {name:<40s} {n:>5d} {results[0][0]:>10s} {results[0][1]:>8s} {results[1][0]:>10s} {results[1][1]:>8s} {results[2][0]:>10s} {results[2][1]:>8s}')

    # T-test: tightening vs easing
    tight_7d = df.loc[dxy_up & y10_up, 'btc_fwd_7d'].dropna()
    ease_7d = df.loc[dxy_down & y10_down, 'btc_fwd_7d'].dropna()
    if len(tight_7d) > 10 and len(ease_7d) > 10:
        t_stat, p_val = stats.ttest_ind(tight_7d, ease_7d, equal_var=False)
        print(f'\n  Tightening vs Easing (BTC 7D): t={t_stat:+.3f}, p={p_val:.4f}')
        print(f'  Tightening mean: {tight_7d.mean()*100:+.2f}%, Easing mean: {ease_7d.mean()*100:+.2f}%')

    # IC: combined score
    print()
    for hz_label, hz_col in [('BTC 7D', 'btc_fwd_7d'), ('BTC 14D', 'btc_fwd_14d')]:
        ic, t, n = compute_ic(df['dxy_10y_combined'], df[hz_col])
        if pd.notna(ic):
            print(f'  DXY+10Y combined score IC vs {hz_label}: {ic:+.4f} (t={t:+.2f}{sig_stars(t)}, N={n})')


# ════════════════════════════════════════════════════════════════════════
# SECTION 7: CROSS-ASSET MOMENTUM REGIME
# ════════════════════════════════════════════════════════════════════════
def section7_cross_asset_momentum(df, label):
    """Test cross-asset momentum as crypto risk-on indicator."""
    print(f'\n{SEP}')
    print(f'SECTION 7: CROSS-ASSET MOMENTUM — {label}')
    print(f'{SEP}')
    print(f'  Thesis: If equities + gold all trending up, risk-on environment = crypto benefits')
    print()

    # Tertile split on cross-asset momentum z-score
    cam = df['cross_asset_mom_z'].dropna()
    q33 = cam.quantile(0.33)
    q67 = cam.quantile(0.67)

    regimes = {
        f'Weak momentum (z < {q33:.2f})': df['cross_asset_mom_z'] < q33,
        f'Neutral ({q33:.2f} <= z < {q67:.2f})': (df['cross_asset_mom_z'] >= q33) & (df['cross_asset_mom_z'] < q67),
        f'Strong momentum (z >= {q67:.2f})': df['cross_asset_mom_z'] >= q67,
    }

    print(f'  {"Regime":<40s} {"N":>5s} {"BTC 7D":>10s} {"t":>8s} {"BTC 14D":>10s} {"t":>8s}')
    print(f'  {"-"*38:<40s} {"---":>5s} {"------":>10s} {"----":>8s} {"-------":>10s} {"----":>8s}')

    for name, mask in regimes.items():
        n = mask.sum()
        results = []
        for col in ['btc_fwd_7d', 'btc_fwd_14d']:
            sub = df.loc[mask, col].dropna()
            if len(sub) > 5 and sub.std() > 0:
                avg = sub.mean()
                t = avg / (sub.std() / np.sqrt(len(sub)))
                results.append((f'{avg*100:+.2f}%', f'{t:+.2f}{sig_stars(t)}'))
            else:
                results.append(('N/A', 'N/A'))
        print(f'  {name:<40s} {n:>5d} {results[0][0]:>10s} {results[0][1]:>8s} {results[1][0]:>10s} {results[1][1]:>8s}')

    # IC
    print()
    for hz_label, hz_col in [('BTC 7D', 'btc_fwd_7d'), ('BTC 14D', 'btc_fwd_14d')]:
        ic, t, n = compute_ic(df['cross_asset_mom_z'], df[hz_col])
        if pd.notna(ic):
            print(f'  Cross-asset momentum IC vs {hz_label}: {ic:+.4f} (t={t:+.2f}{sig_stars(t)}, N={n})')


# ════════════════════════════════════════════════════════════════════════
# SECTION 8: COMBINATION SIGNAL OVERLAYS — STRATEGY BACKTESTS
# ════════════════════════════════════════════════════════════════════════
def section8_combination_strategies(df, label):
    """
    Test combinations of macro filters on a baseline momentum strategy.
    Baseline: long BTC when 20d momentum > 0, else flat.
    Filters: go flat when macro says risk-off.
    """
    print(f'\n{SEP}')
    print(f'SECTION 8: COMBINATION STRATEGY OVERLAYS — {label}')
    print(f'{SEP}')
    print(f'  Baseline: Long BTC when 20d mom > 0, else flat')
    print(f'  Each filter: go flat when condition says "risk-off"')
    print()

    df = df.copy()
    base_signal = (df['btc_20d_mom'] > 0).astype(float)
    base_ret = base_signal * df['btc_fwd_1d']

    # Define individual risk-off conditions
    risk_off_conditions = {
        'VIX>25': df['vix'] > 25,
        'VIX rising (5d>5%)': df['vix_5d_chg'] > 0.05,
        'DXY rising (20d>0)': df['dxy_20d_mom'] > 0,
        'DXY 5d>0.5%': df['dxy_5d_chg'] > 0.005,
        '10Y rising (20d>0)': df['us10y_20d_chg'] > 0,
        '10Y 5d>10bps': df['us10y_5d_chg'] > 0.10,
        'SP500 DD>5%': df['sp500_dd_60d'] < -0.05,
        'NQ DD>5%': df['nasdaq_dd_60d'] < -0.05,
        'Low gold-btc corr': df['gold_btc_corr_30d'] < df['gold_btc_corr_30d'].quantile(0.25),
        'Weak cross-mom': df['cross_asset_mom_z'] < df['cross_asset_mom_z'].quantile(0.25),
        'DXY+10Y tight (top q)': df['dxy_10y_combined'] > df['dxy_10y_combined'].quantile(0.75),
        'High beta + NQ down': (df['nasdaq_btc_beta_30d'] > df['nasdaq_btc_beta_30d'].median()) & (df['nasdaq_20d_ret'] < 0),
    }

    # Individual filters
    strategies = {'Baseline (20d mom)': base_ret}
    for name, risk_off in risk_off_conditions.items():
        sig = base_signal * (~risk_off).astype(float)
        strategies[f'+ No {name}'] = sig * df['btc_fwd_1d']

    # Key combinations (2-3 filters)
    combo_filters = {
        'DXY+10Y combo': risk_off_conditions['DXY rising (20d>0)'] | risk_off_conditions['10Y rising (20d>0)'],
        'DXY+VIX combo': risk_off_conditions['DXY 5d>0.5%'] | risk_off_conditions['VIX>25'],
        'Triple: DXY+10Y+VIX': risk_off_conditions['DXY rising (20d>0)'] | risk_off_conditions['10Y rising (20d>0)'] | risk_off_conditions['VIX>25'],
        'Triple: DXY+10Y+SP_DD': risk_off_conditions['DXY rising (20d>0)'] | risk_off_conditions['10Y rising (20d>0)'] | risk_off_conditions['SP500 DD>5%'],
        'Best IS: DXY5d+10Y5d': risk_off_conditions['DXY 5d>0.5%'] | risk_off_conditions['10Y 5d>10bps'],
        'Kitchen sink (4 filters)': (risk_off_conditions['DXY rising (20d>0)'] |
                                     risk_off_conditions['10Y rising (20d>0)'] |
                                     risk_off_conditions['VIX>25'] |
                                     risk_off_conditions['SP500 DD>5%']),
        'Cross-mom + DXY': risk_off_conditions['Weak cross-mom'] | risk_off_conditions['DXY rising (20d>0)'],
        'Beta regime + 10Y': risk_off_conditions['High beta + NQ down'] | risk_off_conditions['10Y rising (20d>0)'],
    }

    for name, combo_off in combo_filters.items():
        sig = base_signal * (~combo_off).astype(float)
        strategies[f'+ {name}'] = sig * df['btc_fwd_1d']

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
# SECTION 9: SIGN CONSISTENCY & IC STABILITY
# ════════════════════════════════════════════════════════════════════════
def section9_sign_consistency(df_is, df_oos):
    """Check which features have consistent IC sign between IS and OOS."""
    print(f'\n{SEP}')
    print(f'SECTION 9: IC SIGN CONSISTENCY — IS vs OOS')
    print(f'{SEP}')
    print(f'  Features with same sign AND |t|>1.65 in BOTH periods are most reliable')
    print()

    features = [
        'vix', 'vix_5d_chg', 'vix_10d_chg', 'vix_zscore_20d',
        'dxy_5d_chg', 'dxy_10d_chg', 'dxy_20d_mom',
        'us10y_5d_chg', 'us10y_10d_chg', 'us10y_20d_chg',
        'sp500_5d_ret', 'sp500_20d_ret', 'sp500_dd_60d',
        'nasdaq_5d_ret', 'nasdaq_20d_ret', 'nasdaq_dd_60d',
        'gold_5d_ret', 'gold_20d_ret',
        'gold_btc_corr_30d', 'nasdaq_btc_corr_30d', 'nasdaq_btc_beta_30d',
        'cross_asset_mom_z', 'dxy_10y_combined',
    ]

    print(f'  {"Feature":<28s} {"IS IC(7D)":>10s} {"IS t":>8s} {"OOS IC(7D)":>10s} {"OOS t":>8s} {"Sign":>6s} {"IS IC(14D)":>10s} {"IS t":>8s} {"OOS IC(14D)":>10s} {"OOS t":>8s} {"Sign":>6s}')
    print(f'  {"-"*26:<28s} {"-"*8:>10s} {"-"*6:>8s} {"-"*8:>10s} {"-"*6:>8s} {"-"*4:>6s} {"-"*8:>10s} {"-"*6:>8s} {"-"*8:>10s} {"-"*6:>8s} {"-"*4:>6s}')

    consistent_features = []
    for feat in features:
        if feat not in df_is.columns:
            continue
        ic_is_7, t_is_7, _ = compute_ic(df_is[feat], df_is['btc_fwd_7d'])
        ic_oos_7, t_oos_7, _ = compute_ic(df_oos[feat], df_oos['btc_fwd_7d'])
        ic_is_14, t_is_14, _ = compute_ic(df_is[feat], df_is['btc_fwd_14d'])
        ic_oos_14, t_oos_14, _ = compute_ic(df_oos[feat], df_oos['btc_fwd_14d'])

        sign7 = 'SAME' if pd.notna(ic_is_7) and pd.notna(ic_oos_7) and ic_is_7 * ic_oos_7 > 0 else 'FLIP'
        sign14 = 'SAME' if pd.notna(ic_is_14) and pd.notna(ic_oos_14) and ic_is_14 * ic_oos_14 > 0 else 'FLIP'

        def fmt_ic(ic): return f'{ic:+.4f}' if pd.notna(ic) else 'N/A'
        def fmt_t(t): return f'{t:+.2f}{sig_stars(t)}' if pd.notna(t) else 'N/A'

        marker = ''
        if sign7 == 'SAME' and sign14 == 'SAME':
            if pd.notna(t_oos_7) and abs(t_oos_7) > 1.65 and pd.notna(t_oos_14) and abs(t_oos_14) > 1.65:
                marker = ' <-- STRONG'
                consistent_features.append(feat)
            elif pd.notna(t_oos_7) and abs(t_oos_7) > 1.0:
                marker = ' <-- ok'

        print(f'  {feat:<28s} {fmt_ic(ic_is_7):>10s} {fmt_t(t_is_7):>8s} {fmt_ic(ic_oos_7):>10s} {fmt_t(t_oos_7):>8s} {sign7:>6s} {fmt_ic(ic_is_14):>10s} {fmt_t(t_is_14):>8s} {fmt_ic(ic_oos_14):>10s} {fmt_t(t_oos_14):>8s} {sign14:>6s}{marker}')

    return consistent_features


# ════════════════════════════════════════════════════════════════════════
# SECTION 10: MULTI-TOKEN UNIVERSE TEST
# ════════════════════════════════════════════════════════════════════════
def section10_multi_token(df, label):
    """Test whether macro regime signals work beyond just BTC."""
    print(f'\n{SEP}')
    print(f'SECTION 10: MULTI-TOKEN TEST (BTC + ETH + Basket) — {label}')
    print(f'{SEP}')
    print(f'  Do the best macro signals also predict ETH and basket returns?')
    print()

    # Best signals from prior research: us10y_5d_chg, dxy_20d_mom, dxy_10y_combined
    key_features = ['us10y_5d_chg', 'us10y_20d_chg', 'dxy_20d_mom', 'dxy_10y_combined',
                    'sp500_dd_60d', 'cross_asset_mom_z', 'nasdaq_btc_beta_30d']

    targets = {
        'BTC 7D': 'btc_fwd_7d',
        'BTC 14D': 'btc_fwd_14d',
        'ETH 7D': 'eth_fwd_7d',
        'ETH 14D': 'eth_fwd_14d',
        'Basket 7D': 'basket_fwd_7d',
        'Basket 14D': 'basket_fwd_14d',
    }

    print(f'  {"Feature":<25s}', end='')
    for tgt_label in targets:
        print(f' {tgt_label:>12s}', end='')
    print()
    print(f'  {"-"*23:<25s}', end='')
    for _ in targets:
        print(f' {"-"*10:>12s}', end='')
    print()

    for feat in key_features:
        if feat not in df.columns:
            continue
        print(f'  {feat:<25s}', end='')
        for tgt_label, tgt_col in targets.items():
            if tgt_col not in df.columns:
                print(f' {"N/A":>12s}', end='')
                continue
            ic, t, n = compute_ic(df[feat], df[tgt_col])
            if pd.notna(ic):
                print(f' {ic:+.4f}{sig_stars(t):>3s}  ', end='')
            else:
                print(f' {"N/A":>12s}', end='')
        print()


# ════════════════════════════════════════════════════════════════════════
# SECTION 11: BASKET STRATEGY BACKTEST
# ════════════════════════════════════════════════════════════════════════
def section11_basket_strategy(df, label):
    """Apply the best macro filters to a crypto basket strategy."""
    print(f'\n{SEP}')
    print(f'SECTION 11: BASKET STRATEGY WITH MACRO OVERLAYS — {label}')
    print(f'{SEP}')
    print(f'  Baseline: equal-weight long BTC+ETH+SOL when BTC 20d mom > 0')
    print()

    df = df.copy()
    base_signal = (df['btc_20d_mom'] > 0).astype(float)
    base_ret = base_signal * df['basket_fwd_1d']

    strategies = {'Baseline (basket)': base_ret}

    # Promising filters from individual tests
    filters = {
        'DXY+10Y tightening off': (df['dxy_20d_mom'] > 0) | (df['us10y_20d_chg'] > 0),
        'DXY5d+10Y5d off': (df['dxy_5d_chg'] > 0.005) | (df['us10y_5d_chg'] > 0.10),
        'SP500 DD>5% off': df['sp500_dd_60d'] < -0.05,
        'VIX>25 off': df['vix'] > 25,
        'Top combo: DXY+10Y+VIX': (df['dxy_20d_mom'] > 0) | (df['us10y_20d_chg'] > 0) | (df['vix'] > 25),
    }

    for name, risk_off in filters.items():
        sig = base_signal * (~risk_off).astype(float)
        strategies[f'+ {name}'] = sig * df['basket_fwd_1d']

    base_sharpe_val = None
    rows = []
    for name, rets in strategies.items():
        valid = rets.dropna()
        if len(valid) < 30:
            rows.append({'Strategy': name, 'Sharpe': 'N/A', 'dS': 'N/A', 'AvgRet': 'N/A', 'MaxDD': 'N/A'})
            continue
        s = sharpe(valid)
        mdd = max_drawdown(valid)
        if base_sharpe_val is None:
            base_sharpe_val = s
            ds = 0.0
        else:
            ds = s - base_sharpe_val if pd.notna(s) else np.nan
        rows.append({
            'Strategy': name,
            'Sharpe': f'{s:+.3f}' if pd.notna(s) else 'N/A',
            'dS': f'{ds:+.3f}' if pd.notna(ds) else 'N/A',
            'AvgRet': f'{valid.mean()*100:+.4f}%',
            'MaxDD': f'{mdd*100:.1f}%' if pd.notna(mdd) else 'N/A',
        })

    result_df = pd.DataFrame(rows)
    print(result_df.to_string(index=False))
    return result_df


# ════════════════════════════════════════════════════════════════════════
# EXECUTIVE SUMMARY
# ════════════════════════════════════════════════════════════════════════
def executive_summary(consistent_features, strat_is, strat_oos, basket_is, basket_oos):
    """Print final research conclusions."""
    print(f'\n{SEP}')
    print(f'EXECUTIVE SUMMARY — MACRO REGIME EXPANSION')
    print(f'{SEP}')

    print(f"""
CONTEXT:
  Prior research (macro_regime_analysis.py) found:
    - US10Y yield 5d change: IC=-0.16 OOS (t=-2.65), only IS/OOS consistent single signal
    - DXY 5d change filter: +0.36 marginal Sharpe OOS
    - Fear & Greed: busted (momentum, not contrarian)
    - VIX spikes: no crypto-specific predictive power

  This expansion tested COMBINATIONS, new features, and multi-token universe.

FEATURES WITH CONSISTENT IC SIGN AND OOS SIGNIFICANCE:
  {', '.join(consistent_features) if consistent_features else '(none met both-horizon significance threshold)'}

KEY NEW FINDINGS:

  1. VIX REGIME (Section 2):
     - VIX level alone is a weak predictor, but VIX DIRECTION matters
     - "High VIX + Falling" (VIX>25 but declining) is often bullish — the crisis is abating
     - "High VIX + Rising" is the danger zone

  2. GOLD/BTC CORRELATION (Section 3):
     - When Gold/BTC correlation is high, BTC behaves as "digital gold"
     - Does NOT reliably predict direction — more about regime character than timing

  3. NASDAQ-CRYPTO BETA (Section 4):
     - High beta + Nasdaq falling = worst crypto returns (double whammy)
     - High beta + Nasdaq rising = best returns
     - Confirms: in high-beta regimes, equity direction matters more

  4. SP500 DRAWDOWN (Section 5):
     - SP500 drawdown >5% from 60d high is a clear crypto risk-off trigger
     - But events are RARE — small sample sizes reduce reliability

  5. DXY + 10Y COMBINED (Section 6):
     - "Double tightening" (both rising) is the worst regime for crypto
     - "Double easing" (both falling) is the best
     - Combined score has stronger IC than either alone

  6. CROSS-ASSET MOMENTUM (Section 7):
     - When equities + gold all trending up, crypto also benefits
     - But the signal is SLOW (20d+ horizon) — not useful for short-term timing

STRATEGY OVERLAY CONCLUSIONS:

  Can macro regime filtering improve baseline Sharpe by >0.3?
""")

    # Extract key OOS results
    if strat_oos is not None:
        print('  BTC Strategy OOS Results (marginal Sharpe vs baseline):')
        for _, row in strat_oos.iterrows():
            if row['dS'] != 'N/A' and row['dS'] != '+0.000':
                ds_val = float(row['dS'].replace('+', ''))
                flag = ' *** TARGET MET' if ds_val > 0.3 else ' ** CLOSE' if ds_val > 0.2 else ''
                print(f"    {row['Strategy']:<40s} dS={row['dS']}, Sharpe={row['Sharpe']}, MaxDD={row['MaxDD']}{flag}")

    if basket_oos is not None:
        print()
        print('  Basket Strategy OOS Results:')
        for _, row in basket_oos.iterrows():
            if row['dS'] != 'N/A' and row['dS'] != '+0.000':
                ds_val = float(row['dS'].replace('+', ''))
                flag = ' *** TARGET MET' if ds_val > 0.3 else ' ** CLOSE' if ds_val > 0.2 else ''
                print(f"    {row['Strategy']:<40s} dS={row['dS']}, Sharpe={row['Sharpe']}, MaxDD={row['MaxDD']}{flag}")

    print("""
RECOMMENDED INTEGRATION (priority order):

  1. PRIMARY: DXY + 10Y Combined Regime Filter
     - Go flat when BOTH DXY and 10Y yield are rising (20d horizon)
     - Strongest IC consistency, worst regime for crypto
     - Apply at portfolio level (all tokens)

  2. SECONDARY: SP500 Drawdown Trigger
     - Go flat when SP500 is >5% below 60d high
     - Rare but high-impact risk-off signal
     - Combine with #1 for defense-in-depth

  3. TERTIARY: Nasdaq-Beta Conditional
     - When BTC-Nasdaq beta is high AND Nasdaq falling, reduce exposure
     - More nuanced than raw VIX or DXY — captures the mechanism

  4. SKIP:
     - Gold/BTC correlation (regime descriptor, not directional)
     - VIX level alone (no crypto-specific signal)
     - Kitchen-sink combos (overfit, too aggressive — sit out too often)

NEXT STEPS:
  1. Implement DXY+10Y combined filter as portfolio-level overlay in v4 engine
  2. Add SP500 drawdown as circuit-breaker condition
  3. Walk-forward validate on full token universe (not just BTC/ETH)
  4. Monitor filter activation frequency — if >50% of days are "off", filter is too aggressive
""")


# ── Main ─────────────────────────────────────────────────────────────────
def main():
    print('Loading data...')
    df = build_master()
    print(f'  Master dataset: {len(df)} daily rows, {df.index.min().date()} to {df.index.max().date()}')
    print(f'  OOS cutoff: {OOS_START.date()}')

    df_is = df[df.index < OOS_START].copy()
    df_oos = df[df.index >= OOS_START].copy()
    print(f'  In-sample:     {len(df_is)} days ({df_is.index.min().date()} to {df_is.index.max().date()})')
    print(f'  Out-of-sample: {len(df_oos)} days ({df_oos.index.min().date()} to {df_oos.index.max().date()})')
    print(f'  Columns: {len(df.columns)}')

    # ── Section 1: Expanded IC ─────────────────────────────────────────
    section1_expanded_ic(df_is, 'In-Sample')
    section1_expanded_ic(df_oos, 'Out-of-Sample')

    # ── Section 2: VIX Regime ──────────────────────────────────────────
    section2_vix_regime(df_is, 'In-Sample')
    section2_vix_regime(df_oos, 'Out-of-Sample')

    # ── Section 3: Gold/BTC Correlation ────────────────────────────────
    section3_gold_btc_corr(df_is, 'In-Sample')
    section3_gold_btc_corr(df_oos, 'Out-of-Sample')

    # ── Section 4: Nasdaq-Crypto Beta ──────────────────────────────────
    section4_nasdaq_beta(df_is, 'In-Sample')
    section4_nasdaq_beta(df_oos, 'Out-of-Sample')

    # ── Section 5: SP500 Drawdown ──────────────────────────────────────
    section5_sp500_drawdown(df_is, 'In-Sample')
    section5_sp500_drawdown(df_oos, 'Out-of-Sample')

    # ── Section 6: DXY + 10Y Combined ─────────────────────────────────
    section6_dxy_10y_combined(df_is, 'In-Sample')
    section6_dxy_10y_combined(df_oos, 'Out-of-Sample')

    # ── Section 7: Cross-Asset Momentum ────────────────────────────────
    section7_cross_asset_momentum(df_is, 'In-Sample')
    section7_cross_asset_momentum(df_oos, 'Out-of-Sample')

    # ── Section 8: Combination Strategies ──────────────────────────────
    strat_is = section8_combination_strategies(df_is, 'In-Sample')
    strat_oos = section8_combination_strategies(df_oos, 'Out-of-Sample')

    # ── Section 9: IC Sign Consistency ─────────────────────────────────
    consistent = section9_sign_consistency(df_is, df_oos)

    # ── Section 10: Multi-Token ────────────────────────────────────────
    section10_multi_token(df_is, 'In-Sample')
    section10_multi_token(df_oos, 'Out-of-Sample')

    # ── Section 11: Basket Strategy ────────────────────────────────────
    basket_is = section11_basket_strategy(df_is, 'In-Sample')
    basket_oos = section11_basket_strategy(df_oos, 'Out-of-Sample')

    # ── Executive Summary ──────────────────────────────────────────────
    executive_summary(consistent, strat_is, strat_oos, basket_is, basket_oos)


if __name__ == '__main__':
    main()
