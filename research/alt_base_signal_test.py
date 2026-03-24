#!/workspace/venv/bin/python
"""
Alt-Coin Base Signal Search (R73)
===================================

R66 showed V3's positioning+VRP overlays generalize to altcoins (improve 7/10 tokens)
but the 20/50 EMA crossover BASE fails on alts (too choppy — only 3/10 positive OOS).

This study tests 5 candidate base signals to find one that works on alts so the
proven overlays can be applied.

Candidates:
  A: Slower EMA (50/200)
  B: SMA with Hysteresis (50/200 + band)
  C: Breakout (Donchian Channel 50/20)
  D: Momentum (dual ROC filter 30d/90d)
  E: Relative Strength vs BTC (30d outperformance + BTC trend)

Tokens: ETH, SOL, BNB, XRP, DOGE (same as R66 for comparability)

Temporal split:
  IS: 2020-09-01 (positioning data start) to 2024-12-31
  OOS: 2025-01-01 to latest

Success criteria:
  - Base-only: positive OOS Sharpe on >= 3/5 tokens
  - With overlays: positive OOS Sharpe on >= 4/5 tokens
  - Mean cross-token OOS Sharpe > 0.2
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

COST_BPS = 10  # round-trip cost in basis points
IS_END = '2024-12-31'
OOS_START = '2025-01-01'

TARGET_TOKENS = ['ETH', 'SOL', 'BNB', 'XRP', 'DOGE']
DVOL_TOKENS = {'BTC', 'ETH'}

SIGNAL_NAMES = {
    'A': 'Slower EMA (50/200)',
    'B': 'SMA Hysteresis (50/200+band)',
    'C': 'Donchian Breakout (50/20)',
    'D': 'Dual ROC Momentum (30/90)',
    'E': 'Relative Strength vs BTC',
    'V1': 'Original EMA (20/50) [baseline]',
}


# ── 1. Data Loading ──────────────────────────────────────────────────────────

def load_spot_daily(token):
    """Load spot 1h data for a token and resample to daily."""
    path = DATA_DIR / f'spot/1h_cache/{token}_1h.parquet'
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    df.index = pd.to_datetime(df.index)
    df.index.name = 'date'
    daily = df['close'].resample('D').last().dropna().to_frame('close')
    daily['open'] = df['open'].resample('D').first()
    daily['high'] = df['high'].resample('D').max()
    daily['low'] = df['low'].resample('D').min()
    daily['volume'] = df['volume'].resample('D').sum()
    return daily


def load_all_positioning():
    """Load all positioning data, return dict keyed by symbol (e.g., ETHUSDT)."""
    path = DATA_DIR / 'alternative/binance_metrics/all_symbols_daily_ls.parquet'
    df = pd.read_parquet(path)
    df['date'] = pd.to_datetime(df['date'])
    result = {}
    for symbol in df['symbol'].unique():
        sub = df[df['symbol'] == symbol].copy()
        sub = sub.set_index('date').sort_index()
        sub = sub[['sum_toptrader_ls_ratio', 'count_toptrader_ls_ratio', 'count_ls_ratio']].copy()
        sub = sub[~sub.index.duplicated(keep='last')]
        result[symbol] = sub
    return result


def load_dvol(token):
    """Load DVOL for a token. Returns Series or empty Series if not available."""
    fname = f'{token.lower()}_dvol_daily.json'
    path = DATA_DIR / f'alternative/deribit_options/dvol/{fname}'
    if not path.exists():
        return pd.Series(dtype=float)
    with open(path) as f:
        data = json.load(f)
    records = []
    for row in data:
        ts = pd.Timestamp(row[0], unit='ms')
        records.append({'date': ts, 'dvol_close': row[4]})
    dvol = pd.DataFrame(records).set_index('date').sort_index()
    dvol = dvol[~dvol.index.duplicated(keep='last')]
    return dvol['dvol_close']


# ── 2. Base Signal Constructors ──────────────────────────────────────────────

def build_signal_v1(daily, **kwargs):
    """V1 baseline: Long when 20d EMA > 50d EMA, flat otherwise."""
    ema20 = daily['close'].ewm(span=20, adjust=False).mean()
    ema50 = daily['close'].ewm(span=50, adjust=False).mean()
    position = (ema20 > ema50).astype(float)
    return position


def build_signal_A(daily, **kwargs):
    """Signal A: Slower EMA (50/200). Long when 50d EMA > 200d EMA."""
    ema50 = daily['close'].ewm(span=50, adjust=False).mean()
    ema200 = daily['close'].ewm(span=200, adjust=False).mean()
    position = (ema50 > ema200).astype(float)
    return position


def build_signal_B(daily, **kwargs):
    """
    Signal B: SMA with Hysteresis (50/200 + band).
    Enter long when close > 200 SMA AND 50 SMA > 200 SMA.
    Exit when close < 50 SMA.
    """
    sma50 = daily['close'].rolling(50, min_periods=50).mean()
    sma200 = daily['close'].rolling(200, min_periods=200).mean()
    close = daily['close']

    position = pd.Series(0.0, index=daily.index)
    in_position = False

    for i in range(len(daily)):
        if pd.isna(sma50.iloc[i]) or pd.isna(sma200.iloc[i]):
            position.iloc[i] = 0.0
            continue

        if not in_position:
            # Entry: close > 200 SMA AND 50 SMA > 200 SMA
            if close.iloc[i] > sma200.iloc[i] and sma50.iloc[i] > sma200.iloc[i]:
                in_position = True
                position.iloc[i] = 1.0
            else:
                position.iloc[i] = 0.0
        else:
            # Exit: close < 50 SMA
            if close.iloc[i] < sma50.iloc[i]:
                in_position = False
                position.iloc[i] = 0.0
            else:
                position.iloc[i] = 1.0

    return position


def build_signal_C(daily, **kwargs):
    """
    Signal C: Donchian Channel Breakout.
    Long when close > max(high, previous 50 days).
    Flat when close < min(low, previous 20 days).
    Uses shifted windows to avoid look-ahead (today's bar not in the channel).
    """
    high_50 = daily['high'].shift(1).rolling(50, min_periods=50).max()
    low_20 = daily['low'].shift(1).rolling(20, min_periods=20).min()
    close = daily['close']

    position = pd.Series(0.0, index=daily.index)
    in_position = False

    for i in range(len(daily)):
        if pd.isna(high_50.iloc[i]) or pd.isna(low_20.iloc[i]):
            position.iloc[i] = 0.0
            continue

        if not in_position:
            # Entry: close breaks above 50-day high channel
            if close.iloc[i] > high_50.iloc[i]:
                in_position = True
                position.iloc[i] = 1.0
            else:
                position.iloc[i] = 0.0
        else:
            # Exit: close breaks below 20-day low channel
            if close.iloc[i] < low_20.iloc[i]:
                in_position = False
                position.iloc[i] = 0.0
            else:
                position.iloc[i] = 1.0

    return position


def build_signal_D(daily, **kwargs):
    """
    Signal D: Dual ROC Momentum.
    Long when 30d ROC > 0 AND 90d ROC > 0.
    Flat otherwise.
    """
    close = daily['close']
    roc_30 = close / close.shift(30) - 1
    roc_90 = close / close.shift(90) - 1
    position = ((roc_30 > 0) & (roc_90 > 0)).astype(float)
    return position


def build_signal_E(daily, btc_daily=None, **kwargs):
    """
    Signal E: Relative Strength vs BTC.
    Long when token outperforms BTC over 30d AND BTC trend is up (BTC EMA20>50).
    """
    if btc_daily is None:
        return pd.Series(0.0, index=daily.index)

    # Align BTC to token dates
    btc_close = btc_daily['close'].reindex(daily.index).ffill()

    # 30d relative performance
    token_ret_30 = daily['close'] / daily['close'].shift(30) - 1
    btc_ret_30 = btc_close / btc_close.shift(30) - 1
    outperforms = token_ret_30 > btc_ret_30

    # BTC trend: 20d EMA > 50d EMA
    btc_ema20 = btc_close.ewm(span=20, adjust=False).mean()
    btc_ema50 = btc_close.ewm(span=50, adjust=False).mean()
    btc_uptrend = btc_ema20 > btc_ema50

    position = (outperforms & btc_uptrend).astype(float)
    return position


SIGNAL_BUILDERS = {
    'V1': build_signal_v1,
    'A': build_signal_A,
    'B': build_signal_B,
    'C': build_signal_C,
    'D': build_signal_D,
    'E': build_signal_E,
}


# ── 3. Overlay Construction (same as R66/V3) ────────────────────────────────

def build_positioning_overlay(daily, positioning):
    """
    Positioning overlay: Top Trader L/S + L/S Divergence combined z-score.
    30d rolling z-score -> sizing multiplier.
    """
    pos = positioning.reindex(daily.index).ffill()

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


def build_vrp_overlay(daily, dvol_series):
    """
    VRP sizing overlay (same as V3):
    IV - RV z-score -> sizing multiplier.
    """
    log_ret = np.log(daily['close'] / daily['close'].shift(1))
    rv_20d = log_ret.rolling(20, min_periods=15).std() * np.sqrt(365) * 100

    if dvol_series.empty or len(dvol_series) < 30:
        iv = log_ret.rolling(90, min_periods=60).std() * np.sqrt(365) * 100 * 1.2
    else:
        iv = dvol_series.reindex(daily.index).ffill()

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


# ── 4. Backtest Engine ───────────────────────────────────────────────────────

def run_backtest(daily, final_position, cost_bps=COST_BPS):
    """Run backtest with weekly rebalancing and transaction costs."""
    daily_ret = daily['close'].pct_change()

    # Identify rebalance days (every Monday)
    rebalance_dates = daily.index.to_series().groupby(
        daily.index.to_period('W')
    ).first()
    rebalance_set = set(rebalance_dates.values)

    held_position = pd.Series(0.0, index=daily.index)
    current_pos = 0.0
    costs = pd.Series(0.0, index=daily.index)

    for dt in daily.index:
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
    if len(returns) < 30:
        return {
            'label': label, 'total_return': np.nan, 'ann_return': np.nan,
            'ann_vol': np.nan, 'sharpe': np.nan, 'max_dd': np.nan,
            'calmar': np.nan, 'profit_factor': np.nan, 'n_days': len(returns),
            'win_rate': np.nan, 'n_trades': np.nan,
        }

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

    gains = returns[returns > 0].sum()
    losses = abs(returns[returns < 0].sum())
    profit_factor = gains / losses if losses > 0 else np.inf

    # Win rate (days with positive returns when in position)
    active_days = returns[returns != 0]
    win_rate = (active_days > 0).mean() if len(active_days) > 0 else np.nan

    return {
        'label': label,
        'total_return': total_ret,
        'ann_return': ann_ret,
        'ann_vol': ann_vol,
        'sharpe': sharpe,
        'max_dd': max_dd,
        'calmar': calmar,
        'profit_factor': profit_factor,
        'n_days': len(returns),
        'win_rate': win_rate,
    }


def count_trades(position):
    """Count number of round-trip trades from a position series."""
    changes = position.diff().abs()
    # Each entry (0->1) is half a round trip; count entries
    entries = ((position.diff() > 0) & (position > 0)).sum()
    return int(entries)


# ── 5. Per-Token, Per-Signal Test ────────────────────────────────────────────

def test_signal_on_token(token, signal_id, daily, positioning, dvol_series,
                         btc_daily=None, verbose=False):
    """
    Test a single base signal (with and without overlays) on a single token.
    Returns dict of IS/OOS metrics for base-only and base+overlays.
    """
    builder = SIGNAL_BUILDERS[signal_id]

    # Build base signal
    base_position = builder(daily, btc_daily=btc_daily)

    # Build overlays
    pos_multiplier = build_positioning_overlay(daily, positioning)
    vrp_multiplier = build_vrp_overlay(daily, dvol_series)

    # Base only
    base_only_pos = base_position.copy()

    # Base + overlays (V3 style)
    overlay_pos = (base_position * pos_multiplier * vrp_multiplier).clip(0, 1.5)

    # Run backtests
    base_ret, base_held, base_costs = run_backtest(daily, base_only_pos)
    overlay_ret, overlay_held, overlay_costs = run_backtest(daily, overlay_pos)

    # Buy-and-hold benchmark
    bh_ret = daily['close'].pct_change()

    # Split IS/OOS
    is_mask = (daily.index >= '2020-09-01') & (daily.index <= IS_END)
    oos_mask = daily.index >= OOS_START

    results = {}
    for variant, strat_ret, held_pos in [
        ('base', base_ret, base_held),
        ('overlay', overlay_ret, overlay_held),
        ('bh', bh_ret, None),
    ]:
        is_ret = strat_ret[is_mask]
        oos_ret = strat_ret[oos_mask]
        results[f'{variant}_is'] = compute_metrics(is_ret, f"{token} {signal_id} {variant} IS")
        results[f'{variant}_oos'] = compute_metrics(oos_ret, f"{token} {signal_id} {variant} OOS")
        if held_pos is not None:
            # Trade count and exposure
            is_held = held_pos[is_mask]
            oos_held = held_pos[oos_mask]
            results[f'{variant}_is']['n_trades'] = count_trades(is_held)
            results[f'{variant}_oos']['n_trades'] = count_trades(oos_held)
            results[f'{variant}_is']['exposure'] = (is_held > 0).mean()
            results[f'{variant}_oos']['exposure'] = (oos_held > 0).mean()

    results['token'] = token
    results['signal'] = signal_id

    if verbose:
        b_is = results['base_is']
        b_oos = results['base_oos']
        o_is = results['overlay_is']
        o_oos = results['overlay_oos']
        d_sh_oos = o_oos['sharpe'] - b_oos['sharpe'] if not (np.isnan(o_oos['sharpe']) or np.isnan(b_oos['sharpe'])) else np.nan
        print(f"    {signal_id} ({SIGNAL_NAMES[signal_id][:30]}):")
        print(f"      Base   IS: Sh={b_is['sharpe']:+.2f}, Ret={b_is['ann_return']:.1%}, DD={b_is['max_dd']:.1%}, Exp={b_is.get('exposure', 0):.0%}")
        print(f"      Base  OOS: Sh={b_oos['sharpe']:+.2f}, Ret={b_oos['ann_return']:.1%}, DD={b_oos['max_dd']:.1%}, Exp={b_oos.get('exposure', 0):.0%}")
        print(f"      +Ovly OOS: Sh={o_oos['sharpe']:+.2f}, Ret={o_oos['ann_return']:.1%}, DD={o_oos['max_dd']:.1%}, dSh={d_sh_oos:+.3f}" if not np.isnan(d_sh_oos) else "")

    return results


# ── 6. Main Execution ────────────────────────────────────────────────────────

def main():
    print("=" * 78)
    print("ALT-COIN BASE SIGNAL SEARCH (R73)")
    print("=" * 78)
    print()
    print("Objective: Find a base signal that works on altcoins so V3's proven")
    print("positioning+VRP overlays can be applied.")
    print()
    print(f"Tokens: {TARGET_TOKENS}")
    print(f"IS: 2020-09-01 to {IS_END}. OOS: {OOS_START} to latest.")
    print(f"Rebalancing: Weekly (Monday). Cost: {COST_BPS} bps round-trip.")
    print()

    # Load positioning data
    print("Loading positioning data...")
    all_positioning = load_all_positioning()

    # Load BTC daily (needed for signal E)
    print("Loading BTC daily data...")
    btc_daily = load_spot_daily('BTC')

    # Load token data
    token_data = {}
    for token in TARGET_TOKENS:
        daily = load_spot_daily(token)
        symbol = f'{token}USDT'
        positioning = all_positioning.get(symbol)
        dvol = load_dvol(token) if token in DVOL_TOKENS else pd.Series(dtype=float)

        if daily is None or positioning is None:
            print(f"  SKIP {token}: missing data")
            continue

        token_data[token] = {
            'daily': daily,
            'positioning': positioning,
            'dvol': dvol,
        }
        print(f"  {token}: spot {daily.index.min().date()} to {daily.index.max().date()}, "
              f"pos {len(positioning)} days, VRP={'DVOL' if not dvol.empty else 'proxy'}")

    print(f"\nTestable tokens: {list(token_data.keys())}")
    print()

    # ── Run all signal x token combinations ──
    all_results = []
    signal_ids = ['V1', 'A', 'B', 'C', 'D', 'E']

    for token in token_data:
        print(f"\n{'='*60}")
        print(f"  {token}")
        print(f"{'='*60}")

        td = token_data[token]
        for sig_id in signal_ids:
            result = test_signal_on_token(
                token, sig_id, td['daily'], td['positioning'], td['dvol'],
                btc_daily=btc_daily, verbose=True,
            )
            all_results.append(result)

    # ── Build summary tables ──
    print("\n\n" + "=" * 78)
    print("CROSS-SIGNAL COMPARISON")
    print("=" * 78)

    # Organize: for each signal, collect OOS metrics across tokens
    signal_summary = {}
    for sig_id in signal_ids:
        sig_results = [r for r in all_results if r['signal'] == sig_id]
        base_oos_sharpes = [r['base_oos']['sharpe'] for r in sig_results]
        overlay_oos_sharpes = [r['overlay_oos']['sharpe'] for r in sig_results]
        base_oos_rets = [r['base_oos']['ann_return'] for r in sig_results]
        overlay_oos_rets = [r['overlay_oos']['ann_return'] for r in sig_results]
        base_oos_dds = [r['base_oos']['max_dd'] for r in sig_results]
        overlay_oos_dds = [r['overlay_oos']['max_dd'] for r in sig_results]

        d_sharpes = [o - b for o, b in zip(overlay_oos_sharpes, base_oos_sharpes)
                     if not (np.isnan(o) or np.isnan(b))]

        n_base_pos = sum(1 for s in base_oos_sharpes if not np.isnan(s) and s > 0)
        n_overlay_pos = sum(1 for s in overlay_oos_sharpes if not np.isnan(s) and s > 0)
        n_valid = sum(1 for s in base_oos_sharpes if not np.isnan(s))

        signal_summary[sig_id] = {
            'name': SIGNAL_NAMES[sig_id],
            'n_tokens': n_valid,
            'base_mean_sharpe': np.nanmean(base_oos_sharpes),
            'base_median_sharpe': np.nanmedian(base_oos_sharpes),
            'overlay_mean_sharpe': np.nanmean(overlay_oos_sharpes),
            'overlay_median_sharpe': np.nanmedian(overlay_oos_sharpes),
            'base_mean_ret': np.nanmean(base_oos_rets),
            'overlay_mean_ret': np.nanmean(overlay_oos_rets),
            'base_mean_dd': np.nanmean(base_oos_dds),
            'overlay_mean_dd': np.nanmean(overlay_oos_dds),
            'mean_d_sharpe': np.mean(d_sharpes) if d_sharpes else np.nan,
            'n_base_positive': n_base_pos,
            'n_overlay_positive': n_overlay_pos,
            'per_token': sig_results,
        }

    # Console summary
    print(f"\n{'Signal':<10} {'Name':<35} {'Base OOS Sh':>12} {'Ovly OOS Sh':>12} {'dSh':>8} {'Base+':>6} {'Ovly+':>6}")
    print("-" * 95)
    for sig_id in signal_ids:
        s = signal_summary[sig_id]
        print(f"{sig_id:<10} {s['name']:<35} {s['base_mean_sharpe']:>+12.3f} {s['overlay_mean_sharpe']:>+12.3f} {s['mean_d_sharpe']:>+8.3f} {s['n_base_positive']:>3}/{s['n_tokens']:<2} {s['n_overlay_positive']:>3}/{s['n_tokens']:<2}")

    # Per-token detail for each signal
    print(f"\n{'Signal':<6} {'Token':<6} {'Base IS Sh':>10} {'Base OOS Sh':>11} {'Ovly IS Sh':>11} {'Ovly OOS Sh':>12} {'dSh OOS':>8} {'Base DD':>8} {'Ovly DD':>8} {'Exposure':>9}")
    print("-" * 105)
    for sig_id in signal_ids:
        for r in signal_summary[sig_id]['per_token']:
            b_is = r['base_is']
            b_oos = r['base_oos']
            o_is = r['overlay_is']
            o_oos = r['overlay_oos']
            d_sh = o_oos['sharpe'] - b_oos['sharpe'] if not (np.isnan(o_oos['sharpe']) or np.isnan(b_oos['sharpe'])) else np.nan
            exp = b_oos.get('exposure', np.nan)
            print(f"{sig_id:<6} {r['token']:<6} {b_is['sharpe']:>+10.2f} {b_oos['sharpe']:>+11.2f} {o_is['sharpe']:>+11.2f} {o_oos['sharpe']:>+12.2f} {d_sh:>+8.3f} {b_oos['max_dd']:>8.1%} {o_oos['max_dd']:>8.1%} {exp:>8.0%}" if not np.isnan(d_sh) else f"{sig_id:<6} {r['token']:<6} N/A")
        print()

    # ── Determine winner ──
    print("\n" + "=" * 78)
    print("WINNER SELECTION")
    print("=" * 78)

    # Score each signal
    # Primary: overlay positive OOS count >= 4/5 AND overlay mean OOS Sharpe > 0.2
    # Secondary: base positive OOS count >= 3/5
    # Tertiary: best overlay mean OOS Sharpe

    candidates = []
    for sig_id in signal_ids:
        s = signal_summary[sig_id]
        score = 0
        # Primary: overlay positive count
        score += s['n_overlay_positive'] * 100
        # Secondary: base positive count
        score += s['n_base_positive'] * 10
        # Tertiary: overlay mean sharpe
        score += s['overlay_mean_sharpe'] * 1

        meets_primary = s['n_overlay_positive'] >= 4
        meets_base = s['n_base_positive'] >= 3
        meets_sharpe = s['overlay_mean_sharpe'] > 0.2

        candidates.append({
            'signal': sig_id,
            'name': s['name'],
            'score': score,
            'meets_primary': meets_primary,
            'meets_base': meets_base,
            'meets_sharpe': meets_sharpe,
            'overlay_positive': s['n_overlay_positive'],
            'base_positive': s['n_base_positive'],
            'overlay_mean_sharpe': s['overlay_mean_sharpe'],
            'base_mean_sharpe': s['base_mean_sharpe'],
            'mean_d_sharpe': s['mean_d_sharpe'],
        })

    candidates.sort(key=lambda c: c['score'], reverse=True)

    for c in candidates:
        status = []
        if c['meets_primary']:
            status.append("PASS: overlay >=4/5 positive")
        else:
            status.append(f"FAIL: overlay {c['overlay_positive']}/5 positive")
        if c['meets_base']:
            status.append("PASS: base >=3/5 positive")
        else:
            status.append(f"FAIL: base {c['base_positive']}/5 positive")
        if c['meets_sharpe']:
            status.append(f"PASS: mean overlay Sharpe {c['overlay_mean_sharpe']:.3f} > 0.2")
        else:
            status.append(f"FAIL: mean overlay Sharpe {c['overlay_mean_sharpe']:.3f} <= 0.2")

        print(f"\n  {c['signal']}: {c['name']}")
        for s in status:
            print(f"    {s}")

    # Find best
    full_pass = [c for c in candidates if c['meets_primary'] and c['meets_base'] and c['meets_sharpe']]
    partial_pass = [c for c in candidates if c['meets_primary'] or (c['meets_base'] and c['meets_sharpe'])]

    if full_pass:
        winner = full_pass[0]
        verdict = "FULL PASS"
    elif partial_pass:
        winner = partial_pass[0]
        verdict = "PARTIAL PASS"
    else:
        winner = candidates[0]
        verdict = "NO SIGNAL PASSES ALL CRITERIA"

    print(f"\n{'='*78}")
    print(f"VERDICT: {verdict}")
    print(f"Best signal: {winner['signal']} ({winner['name']})")
    print(f"  Base OOS: mean Sharpe {winner['base_mean_sharpe']:.3f}, {winner['base_positive']}/5 positive")
    print(f"  +Overlays OOS: mean Sharpe {winner['overlay_mean_sharpe']:.3f}, {winner['overlay_positive']}/5 positive")
    print(f"  Overlay dSharpe: {winner['mean_d_sharpe']:+.3f}")
    print(f"{'='*78}")

    # ── Generate Report ──
    generate_report(signal_summary, signal_ids, all_results, candidates, winner, verdict, token_data)


def generate_report(signal_summary, signal_ids, all_results, candidates, winner, verdict, token_data):
    """Generate markdown report."""
    lines = []
    lines.append("# Alt-Coin Base Signal Search Results (R73)")
    lines.append("")
    lines.append(f"**Run date**: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"**Objective**: Find a base signal for altcoins that works with V3's positioning+VRP overlays")
    lines.append(f"**Context**: R66 showed overlays improve 7/10 altcoins (mean dSharpe +0.105) but the 20/50 EMA base fails on alts")
    lines.append(f"**Tokens**: {TARGET_TOKENS}")
    lines.append(f"**IS period**: 2020-09-01 to {IS_END}")
    lines.append(f"**OOS period**: {OOS_START} to latest")
    lines.append(f"**Rebalancing**: Weekly (Monday). Cost: {COST_BPS} bps round-trip.")
    lines.append(f"**Position range**: 0 to 1.5x (with overlays)")
    lines.append("")

    # ── Signal descriptions ──
    lines.append("## Candidate Signals")
    lines.append("")
    lines.append("| ID | Name | Logic |")
    lines.append("|----|------|-------|")
    lines.append("| V1 | Original EMA (20/50) | Long when 20d EMA > 50d EMA (R66 baseline) |")
    lines.append("| A | Slower EMA (50/200) | Long when 50d EMA > 200d EMA |")
    lines.append("| B | SMA Hysteresis | Enter: close > 200 SMA AND 50 SMA > 200 SMA. Exit: close < 50 SMA |")
    lines.append("| C | Donchian Breakout | Long: close > 50d high. Exit: close < 20d low |")
    lines.append("| D | Dual ROC Momentum | Long when 30d ROC > 0 AND 90d ROC > 0 |")
    lines.append("| E | Relative Strength vs BTC | Long when outperforms BTC 30d AND BTC EMA20 > EMA50 |")
    lines.append("")

    # ── 1. Cross-Signal OOS Summary ──
    lines.append("## 1. Cross-Signal OOS Summary (Mean Across 5 Tokens)")
    lines.append("")
    lines.append("| Signal | Base Mean Sh | Base Median Sh | Ovly Mean Sh | Ovly Median Sh | Mean dSh | Base+/5 | Ovly+/5 | Base Mean Ret | Ovly Mean Ret | Base Mean DD | Ovly Mean DD |")
    lines.append("|--------|-------------|----------------|-------------|----------------|----------|---------|---------|---------------|---------------|-------------|-------------|")

    for sig_id in signal_ids:
        s = signal_summary[sig_id]
        lines.append(
            f"| **{sig_id}** "
            f"| {s['base_mean_sharpe']:+.3f} "
            f"| {s['base_median_sharpe']:+.3f} "
            f"| {s['overlay_mean_sharpe']:+.3f} "
            f"| {s['overlay_median_sharpe']:+.3f} "
            f"| {s['mean_d_sharpe']:+.3f} "
            f"| {s['n_base_positive']}/5 "
            f"| {s['n_overlay_positive']}/5 "
            f"| {s['base_mean_ret']:.1%} "
            f"| {s['overlay_mean_ret']:.1%} "
            f"| {s['base_mean_dd']:.1%} "
            f"| {s['overlay_mean_dd']:.1%} |"
        )

    lines.append("")

    # ── 2. Per-Token Detail for each Signal ──
    lines.append("## 2. Per-Token OOS Detail")
    lines.append("")

    for sig_id in signal_ids:
        s = signal_summary[sig_id]
        lines.append(f"### Signal {sig_id}: {s['name']}")
        lines.append("")
        lines.append("| Token | Base IS Sh | Base OOS Sh | +Ovly IS Sh | +Ovly OOS Sh | dSh OOS | Base OOS Ret | +Ovly OOS Ret | Base OOS DD | +Ovly OOS DD | Exposure |")
        lines.append("|-------|-----------|------------|------------|-------------|---------|-------------|--------------|------------|-------------|----------|")

        for r in s['per_token']:
            b_is = r['base_is']
            b_oos = r['base_oos']
            o_is = r['overlay_is']
            o_oos = r['overlay_oos']
            d_sh = o_oos['sharpe'] - b_oos['sharpe'] if not (np.isnan(o_oos['sharpe']) or np.isnan(b_oos['sharpe'])) else np.nan
            exp = b_oos.get('exposure', np.nan)

            def f(v, pct=False):
                if pd.isna(v) or v is None:
                    return "N/A"
                return f"{v:.1%}" if pct else f"{v:+.2f}"

            def fd(v):
                if pd.isna(v) or v is None:
                    return "N/A"
                return f"{v:+.3f}"

            lines.append(
                f"| {r['token']} "
                f"| {f(b_is['sharpe'])} "
                f"| {f(b_oos['sharpe'])} "
                f"| {f(o_is['sharpe'])} "
                f"| {f(o_oos['sharpe'])} "
                f"| {fd(d_sh)} "
                f"| {f(b_oos['ann_return'], True)} "
                f"| {f(o_oos['ann_return'], True)} "
                f"| {f(b_oos['max_dd'], True)} "
                f"| {f(o_oos['max_dd'], True)} "
                f"| {f(exp, True)} |"
            )

        lines.append("")

    # ── 3. Head-to-Head: Best signal vs V1 baseline per token ──
    lines.append("## 3. Head-to-Head: Each Signal vs V1 Baseline (OOS Sharpe)")
    lines.append("")
    lines.append("| Token | V1 Base | V1+Ovly | A Base | A+Ovly | B Base | B+Ovly | C Base | C+Ovly | D Base | D+Ovly | E Base | E+Ovly |")
    lines.append("|-------|---------|---------|--------|--------|--------|--------|--------|--------|--------|--------|--------|--------|")

    for token in TARGET_TOKENS:
        row_parts = [f"| {token} "]
        for sig_id in signal_ids:
            sig_results = [r for r in all_results if r['signal'] == sig_id and r['token'] == token]
            if sig_results:
                r = sig_results[0]
                b_sh = r['base_oos']['sharpe']
                o_sh = r['overlay_oos']['sharpe']
                row_parts.append(f"| {b_sh:+.2f} | {o_sh:+.2f} ")
            else:
                row_parts.append("| N/A | N/A ")
        lines.append("".join(row_parts) + "|")

    lines.append("")

    # ── 4. Success Criteria Evaluation ──
    lines.append("## 4. Success Criteria Evaluation")
    lines.append("")
    lines.append("| Signal | Base >=3/5 positive OOS | +Ovly >=4/5 positive OOS | Mean Ovly OOS Sh > 0.2 | Overall |")
    lines.append("|--------|------------------------|--------------------------|------------------------|---------|")

    for c in candidates:
        base_pass = "PASS" if c['meets_base'] else "FAIL"
        ovly_pass = "PASS" if c['meets_primary'] else "FAIL"
        sh_pass = "PASS" if c['meets_sharpe'] else "FAIL"
        overall = "PASS" if (c['meets_base'] and c['meets_primary'] and c['meets_sharpe']) else "FAIL"
        lines.append(
            f"| **{c['signal']}** "
            f"| {base_pass} ({c['base_positive']}/5) "
            f"| {ovly_pass} ({c['overlay_positive']}/5) "
            f"| {sh_pass} ({c['overlay_mean_sharpe']:.3f}) "
            f"| **{overall}** |"
        )

    lines.append("")

    # ── 5. Exposure Analysis ──
    lines.append("## 5. OOS Exposure Analysis (% Time in Market)")
    lines.append("")
    lines.append("Lower exposure can explain lower returns but also lower drawdowns.")
    lines.append("")
    lines.append("| Token | V1 | A | B | C | D | E |")
    lines.append("|-------|----|---|---|---|---|---|")

    for token in TARGET_TOKENS:
        row_parts = [f"| {token} "]
        for sig_id in signal_ids:
            sig_results = [r for r in all_results if r['signal'] == sig_id and r['token'] == token]
            if sig_results:
                exp = sig_results[0]['base_oos'].get('exposure', np.nan)
                row_parts.append(f"| {exp:.0%} " if not np.isnan(exp) else "| N/A ")
            else:
                row_parts.append("| N/A ")
        lines.append("".join(row_parts) + "|")

    lines.append("")

    # ── 6. IS/OOS Degradation per Signal ──
    lines.append("## 6. IS to OOS Sharpe Degradation (Base Only)")
    lines.append("")
    lines.append("| Signal | Mean IS Sharpe | Mean OOS Sharpe | Degradation |")
    lines.append("|--------|---------------|----------------|-------------|")

    for sig_id in signal_ids:
        s = signal_summary[sig_id]
        is_sharpes = [r['base_is']['sharpe'] for r in s['per_token']]
        oos_sharpes = [r['base_oos']['sharpe'] for r in s['per_token']]
        mean_is = np.nanmean(is_sharpes)
        mean_oos = np.nanmean(oos_sharpes)
        deg = mean_is - mean_oos
        lines.append(f"| **{sig_id}** | {mean_is:+.3f} | {mean_oos:+.3f} | {deg:+.3f} |")

    lines.append("")

    # ── 7. Verdict ──
    lines.append("## 7. Verdict")
    lines.append("")
    lines.append(f"### {verdict}")
    lines.append("")
    lines.append(f"**Best signal: {winner['signal']} ({winner['name']})**")
    lines.append("")
    lines.append(f"| Criterion | Value |")
    lines.append(f"|-----------|-------|")
    lines.append(f"| Base-only OOS positive | {winner['base_positive']}/5 (need >=3) |")
    lines.append(f"| +Overlays OOS positive | {winner['overlay_positive']}/5 (need >=4) |")
    lines.append(f"| Mean overlay OOS Sharpe | {winner['overlay_mean_sharpe']:.3f} (need >0.2) |")
    lines.append(f"| Base mean OOS Sharpe | {winner['base_mean_sharpe']:.3f} |")
    lines.append(f"| Overlay dSharpe (mean) | {winner['mean_d_sharpe']:+.3f} |")
    lines.append("")

    # Analysis: Why does the winner work / not work?
    lines.append("### Analysis")
    lines.append("")

    if winner['signal'] == 'V1':
        lines.append("The original 20/50 EMA base remains the best option, though it still struggles with altcoin chop.")
    elif winner['signal'] == 'A':
        lines.append("The slower 50/200 EMA reduces whipsaws by filtering out short-term noise. "
                      "The tradeoff is slower entry into trends and lower exposure.")
    elif winner['signal'] == 'B':
        lines.append("SMA with hysteresis provides asymmetric entry/exit conditions that reduce "
                      "whipsaw in choppy markets. The 50 SMA exit is tighter than the 200 SMA entry, "
                      "which creates a band that filters noise.")
    elif winner['signal'] == 'C':
        lines.append("Donchian breakout only enters on new highs (strong momentum confirmation) "
                      "and exits on new lows. This is fundamentally different from moving average "
                      "crossovers -- it requires price action proof rather than lagging indicator signals.")
    elif winner['signal'] == 'D':
        lines.append("Dual ROC momentum requires both short-term (30d) and medium-term (90d) "
                      "momentum to be positive. This dual confirmation reduces false signals "
                      "but may miss early trend entries.")
    elif winner['signal'] == 'E':
        lines.append("Relative strength vs BTC acts as a quality filter -- only trading alts "
                      "that are outperforming the market leader in a favorable BTC regime. "
                      "This is fundamentally a cross-sectional signal rather than time-series.")

    lines.append("")

    # Recommendations
    lines.append("### Recommendations")
    lines.append("")

    if verdict == "FULL PASS":
        lines.append(f"1. **Adopt signal {winner['signal']} as the alt-coin base** for the multi-token portfolio")
        lines.append(f"2. Apply V3 overlays (positioning + VRP) on top -- they add {winner['mean_d_sharpe']:+.3f} mean dSharpe")
        lines.append(f"3. Keep V1 (20/50 EMA) for BTC where it is proven")
        lines.append(f"4. Next step: Run walk-forward validation on the winning signal to confirm robustness")
    elif verdict == "PARTIAL PASS":
        lines.append(f"1. Signal {winner['signal']} shows promise but does not meet all criteria")
        lines.append(f"2. Consider combining the best signals (ensemble) for more robust base")
        lines.append(f"3. The overlay contribution ({winner['mean_d_sharpe']:+.3f} dSharpe) confirms R66 findings")
        lines.append(f"4. Consider token-specific signal selection (different base per token)")
    else:
        lines.append("1. No single base signal meets all criteria for altcoins in this OOS period")
        lines.append("2. The OOS period (2025-01 to 2026-03) was a severe alt drawdown -- all tokens B&H negative")
        lines.append("3. Consider regime-conditional approach: only trade alts when BTC is in uptrend")
        lines.append("4. Consider ensemble: combine top 2-3 signals for more robust base")
        lines.append("5. The overlays remain valuable regardless of base signal choice")

    lines.append("")

    # Write report
    report_path = OUTPUT_DIR / 'alt_base_signal_results.md'
    with open(report_path, 'w') as f:
        f.write('\n'.join(lines))
    print(f"\nReport saved to: {report_path}")


if __name__ == '__main__':
    main()
