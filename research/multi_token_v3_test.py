#!/workspace/venv/bin/python
"""
Multi-Token V3 Strategy Generalizability Test
===============================================

Tests the V3 momentum strategy across multiple tokens to check whether
the strategy generalizes beyond BTC.

V3 Strategy Definition:
  - Base (V1): Long when 20d EMA > 50d EMA, flat otherwise. NO stop losses.
  - Positioning overlay: Binance Top Trader L/S + L/S Divergence combined z-score
    (30d rolling) -> sizing multiplier:
      z > 1.5  -> 0.3x (crowd extremely long, reduce)
      z > 0.5  -> 0.5x (crowd moderately long, reduce)
      -0.5 < z < 0.5 -> 1.0x (neutral)
      z < -0.5 -> 1.3x (crowd short, contrarian increase)
      z < -1.5 -> 1.5x (crowd extremely short, max increase)
  - VRP overlay: (IV - RV) z-score over 60d -> sizing multiplier:
      z > 1    -> 1.3x (vol overpriced, complacent, size up)
      z > -0.5 -> 1.0x (normal)
      z > -1.5 -> 0.5x (vol cheap, turbulence expected)
      z < -1.5 -> 0.3x (extreme stress)
  - Rebalancing: Weekly (Monday). Cost: 10 bps round-trip. Position range: 0 to 1.5x.
  - IV source: Deribit DVOL for BTC/ETH, otherwise proxy (90d RV x 1.2)

Tokens tested:
  Major: ETH, SOL, BNB, XRP, DOGE
  Mid-cap: LINK, AVAX, DOT, OP, ARB

Temporal split:
  IS: 2021-12-01 (positioning data start) to 2024-12-31
  OOS: 2025-01-01 to latest
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

# Tokens to test (must have both spot and positioning data)
TARGET_TOKENS = ['ETH', 'SOL', 'BNB', 'XRP', 'DOGE', 'LINK', 'AVAX', 'DOT', 'OP', 'ARB']

# Tokens with real DVOL data
DVOL_TOKENS = {'BTC', 'ETH'}


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


# ── 2. Signal Construction ───────────────────────────────────────────────────

def build_ema_signal(daily):
    """V1 base: Long when 20d EMA > 50d EMA, flat otherwise. No stops."""
    ema20 = daily['close'].ewm(span=20, adjust=False).mean()
    ema50 = daily['close'].ewm(span=50, adjust=False).mean()
    position = (ema20 > ema50).astype(float)
    return position


def build_positioning_signal(daily, positioning):
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
    return pos_multiplier, combined_z


def build_vrp_signal(daily, dvol_series):
    """
    VRP sizing overlay:
    1. Realized vol: 20d rolling std of daily log returns, annualized
    2. Implied vol: DVOL close or RV proxy (90d RV x 1.2)
    3. VRP = IV - RV
    4. VRP z-score: 60d rolling z-score
    5. Sizing rule based on z-score thresholds
    """
    log_ret = np.log(daily['close'] / daily['close'].shift(1))
    rv_20d = log_ret.rolling(20, min_periods=15).std() * np.sqrt(365) * 100

    if dvol_series.empty or len(dvol_series) < 30:
        # Proxy: 90d RV x 1.2
        iv = log_ret.rolling(90, min_periods=60).std() * np.sqrt(365) * 100 * 1.2
        vrp_source = "proxy"
    else:
        iv = dvol_series.reindex(daily.index).ffill()
        vrp_source = "DVOL"

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
    return vrp_multiplier, vrp_z, vrp_source


# ── 3. Backtest Engine ───────────────────────────────────────────────────────

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

    # Profit factor
    gains = returns[returns > 0].sum()
    losses = abs(returns[returns < 0].sum())
    profit_factor = gains / losses if losses > 0 else np.inf

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
    }


# ── 4. Per-Token Test ────────────────────────────────────────────────────────

def test_token(token, all_positioning, verbose=True):
    """
    Test V1 and V3 on a single token.
    Returns dict with IS and OOS metrics for both variants, or None if data insufficient.
    """
    symbol = f'{token}USDT'
    if verbose:
        print(f"\n{'='*60}")
        print(f"  Testing {token}")
        print(f"{'='*60}")

    # Load spot data
    daily = load_spot_daily(token)
    if daily is None:
        if verbose:
            print(f"  SKIP: No spot data for {token}")
        return None

    # Check positioning data
    if symbol not in all_positioning:
        if verbose:
            print(f"  SKIP: No positioning data for {symbol}")
        return None

    positioning = all_positioning[symbol]
    pos_days = len(positioning)
    if pos_days < 200:
        if verbose:
            print(f"  SKIP: Insufficient positioning data ({pos_days} days < 200)")
        return None

    # Determine IS start: use positioning data start (need ~30d warmup)
    pos_start = positioning.index.min()
    is_start = max(pos_start + pd.Timedelta(days=60), pd.Timestamp('2021-12-01'))
    is_start_str = is_start.strftime('%Y-%m-%d')

    if verbose:
        print(f"  Spot data: {daily.index.min().date()} to {daily.index.max().date()}")
        print(f"  Positioning: {positioning.index.min().date()} to {positioning.index.max().date()} ({pos_days} days)")
        print(f"  IS period: {is_start_str} to {IS_END}")
        print(f"  OOS period: {OOS_START} to latest")

    # Load DVOL (only for BTC/ETH)
    dvol = load_dvol(token) if token in DVOL_TOKENS else pd.Series(dtype=float)
    vrp_source = "DVOL" if not dvol.empty else "proxy (90d RV x 1.2)"
    if verbose:
        print(f"  VRP source: {vrp_source}")

    # Build signals
    base_position = build_ema_signal(daily)
    pos_multiplier, pos_z = build_positioning_signal(daily, positioning)
    vrp_multiplier, vrp_z, _ = build_vrp_signal(daily, dvol)

    # V1: Base only (EMA crossover)
    v1_position = base_position.copy()

    # V3: Base + Positioning + VRP
    v3_position = (base_position * pos_multiplier * vrp_multiplier).clip(0, 1.5)

    # Run backtests
    v1_ret, v1_held, v1_costs = run_backtest(daily, v1_position)
    v3_ret, v3_held, v3_costs = run_backtest(daily, v3_position)

    # Split IS/OOS
    is_mask = (daily.index >= is_start_str) & (daily.index <= IS_END)
    oos_mask = daily.index >= OOS_START

    # Buy-and-hold benchmark
    bh_ret = daily['close'].pct_change()

    results = {
        'token': token,
        'symbol': symbol,
        'pos_days': pos_days,
        'vrp_source': vrp_source,
        'is_start': is_start_str,
        'spot_start': daily.index.min().strftime('%Y-%m-%d'),
        'spot_end': daily.index.max().strftime('%Y-%m-%d'),
    }

    for variant_name, strat_ret in [('v1', v1_ret), ('v3', v3_ret), ('bh', bh_ret)]:
        is_ret = strat_ret[is_mask]
        oos_ret = strat_ret[oos_mask]
        is_m = compute_metrics(is_ret, f"{token} {variant_name} IS")
        oos_m = compute_metrics(oos_ret, f"{token} {variant_name} OOS")
        results[f'{variant_name}_is'] = is_m
        results[f'{variant_name}_oos'] = oos_m

    if verbose:
        v1_is = results['v1_is']
        v1_oos = results['v1_oos']
        v3_is = results['v3_is']
        v3_oos = results['v3_oos']
        print(f"  V1 IS:  Ret={v1_is['ann_return']:.1%}, Sharpe={v1_is['sharpe']:.2f}, MaxDD={v1_is['max_dd']:.1%}")
        print(f"  V1 OOS: Ret={v1_oos['ann_return']:.1%}, Sharpe={v1_oos['sharpe']:.2f}, MaxDD={v1_oos['max_dd']:.1%}")
        print(f"  V3 IS:  Ret={v3_is['ann_return']:.1%}, Sharpe={v3_is['sharpe']:.2f}, MaxDD={v3_is['max_dd']:.1%}")
        print(f"  V3 OOS: Ret={v3_oos['ann_return']:.1%}, Sharpe={v3_oos['sharpe']:.2f}, MaxDD={v3_oos['max_dd']:.1%}")

        # Overlay contribution
        d_sharpe_is = v3_is['sharpe'] - v1_is['sharpe'] if not np.isnan(v1_is['sharpe']) else np.nan
        d_sharpe_oos = v3_oos['sharpe'] - v1_oos['sharpe'] if not np.isnan(v1_oos['sharpe']) else np.nan
        if not np.isnan(d_sharpe_is):
            print(f"  Overlay dSharpe: IS={d_sharpe_is:+.3f}, OOS={d_sharpe_oos:+.3f}")

    return results


# ── 5. Main Execution ────────────────────────────────────────────────────────

def main():
    print("=" * 72)
    print("MULTI-TOKEN V3 STRATEGY GENERALIZABILITY TEST")
    print("=" * 72)
    print()
    print(f"V3: 20/50 EMA crossover + Positioning overlay + VRP overlay, NO stops")
    print(f"V1: 20/50 EMA crossover only (base)")
    print(f"Rebalancing: Weekly (Monday). Cost: {COST_BPS} bps round-trip.")
    print(f"IS: to {IS_END}. OOS: {OOS_START} to latest.")
    print()

    # Load all positioning data
    print("Loading positioning data for all symbols...")
    all_positioning = load_all_positioning()
    available_symbols = sorted(all_positioning.keys())
    print(f"  Available symbols: {available_symbols}")
    print()

    # Check which tokens we can test
    testable = []
    skipped = []
    for token in TARGET_TOKENS:
        symbol = f'{token}USDT'
        if symbol in all_positioning:
            pos_days = len(all_positioning[symbol])
            spot_path = DATA_DIR / f'spot/1h_cache/{token}_1h.parquet'
            if spot_path.exists() and pos_days >= 200:
                testable.append(token)
            else:
                skipped.append((token, f"pos_days={pos_days}" if pos_days < 200 else "no spot"))
        else:
            skipped.append((token, "no positioning data"))

    print(f"Testable tokens ({len(testable)}): {testable}")
    if skipped:
        print(f"Skipped tokens ({len(skipped)}): {skipped}")
    print()

    # Run tests
    all_results = {}
    for token in testable:
        result = test_token(token, all_positioning, verbose=True)
        if result is not None:
            all_results[token] = result

    # ── Summary Analysis ──
    print("\n" + "=" * 72)
    print("CROSS-TOKEN SUMMARY")
    print("=" * 72)

    # Collect metrics
    summary_rows = []
    for token, r in all_results.items():
        v1_is = r['v1_is']
        v1_oos = r['v1_oos']
        v3_is = r['v3_is']
        v3_oos = r['v3_oos']
        bh_is = r['bh_is']
        bh_oos = r['bh_oos']

        d_sharpe_is = v3_is['sharpe'] - v1_is['sharpe'] if not (np.isnan(v3_is['sharpe']) or np.isnan(v1_is['sharpe'])) else np.nan
        d_sharpe_oos = v3_oos['sharpe'] - v1_oos['sharpe'] if not (np.isnan(v3_oos['sharpe']) or np.isnan(v1_oos['sharpe'])) else np.nan

        summary_rows.append({
            'token': token,
            'pos_days': r['pos_days'],
            'vrp_source': r['vrp_source'],
            'v1_is_sharpe': v1_is['sharpe'],
            'v1_oos_sharpe': v1_oos['sharpe'],
            'v3_is_sharpe': v3_is['sharpe'],
            'v3_oos_sharpe': v3_oos['sharpe'],
            'v1_is_ret': v1_is['ann_return'],
            'v1_oos_ret': v1_oos['ann_return'],
            'v3_is_ret': v3_is['ann_return'],
            'v3_oos_ret': v3_oos['ann_return'],
            'v1_is_maxdd': v1_is['max_dd'],
            'v1_oos_maxdd': v1_oos['max_dd'],
            'v3_is_maxdd': v3_is['max_dd'],
            'v3_oos_maxdd': v3_oos['max_dd'],
            'v1_is_calmar': v1_is['calmar'],
            'v1_oos_calmar': v1_oos['calmar'],
            'v3_is_calmar': v3_is['calmar'],
            'v3_oos_calmar': v3_oos['calmar'],
            'v1_is_pf': v1_is['profit_factor'],
            'v1_oos_pf': v1_oos['profit_factor'],
            'v3_is_pf': v3_is['profit_factor'],
            'v3_oos_pf': v3_oos['profit_factor'],
            'bh_is_sharpe': bh_is['sharpe'],
            'bh_oos_sharpe': bh_oos['sharpe'],
            'bh_is_ret': bh_is['ann_return'],
            'bh_oos_ret': bh_oos['ann_return'],
            'd_sharpe_is': d_sharpe_is,
            'd_sharpe_oos': d_sharpe_oos,
        })

    summary_df = pd.DataFrame(summary_rows)

    # Print console summary
    print(f"\n{'Token':<8} {'V1 IS Sh':>10} {'V1 OOS Sh':>10} {'V3 IS Sh':>10} {'V3 OOS Sh':>10} {'dSh OOS':>10} {'VRP Src':>8}")
    print("-" * 70)
    for _, row in summary_df.iterrows():
        print(f"{row['token']:<8} {row['v1_is_sharpe']:>10.2f} {row['v1_oos_sharpe']:>10.2f} {row['v3_is_sharpe']:>10.2f} {row['v3_oos_sharpe']:>10.2f} {row['d_sharpe_oos']:>+10.3f} {row['vrp_source']:>8}")

    # Aggregate stats
    valid_oos = summary_df.dropna(subset=['v3_oos_sharpe'])
    n_tokens = len(valid_oos)
    n_positive_v3 = (valid_oos['v3_oos_sharpe'] > 0).sum()
    n_positive_v1 = (valid_oos['v1_oos_sharpe'] > 0).sum()
    n_v3_better = (valid_oos['d_sharpe_oos'] > 0).sum()

    mean_v1_oos = valid_oos['v1_oos_sharpe'].mean()
    median_v1_oos = valid_oos['v1_oos_sharpe'].median()
    mean_v3_oos = valid_oos['v3_oos_sharpe'].mean()
    median_v3_oos = valid_oos['v3_oos_sharpe'].median()
    mean_d_sharpe = valid_oos['d_sharpe_oos'].mean()
    median_d_sharpe = valid_oos['d_sharpe_oos'].median()

    print(f"\n--- Aggregate OOS Statistics ({n_tokens} tokens) ---")
    print(f"V1 OOS Sharpe: mean={mean_v1_oos:.3f}, median={median_v1_oos:.3f}")
    print(f"V3 OOS Sharpe: mean={mean_v3_oos:.3f}, median={median_v3_oos:.3f}")
    print(f"Overlay dSharpe: mean={mean_d_sharpe:+.3f}, median={median_d_sharpe:+.3f}")
    print(f"Positive V1 OOS Sharpe: {n_positive_v1}/{n_tokens} ({100*n_positive_v1/n_tokens:.0f}%)")
    print(f"Positive V3 OOS Sharpe: {n_positive_v3}/{n_tokens} ({100*n_positive_v3/n_tokens:.0f}%)")
    print(f"V3 > V1 OOS: {n_v3_better}/{n_tokens} ({100*n_v3_better/n_tokens:.0f}%)")

    # ── Generate Report ──
    print("\n" + "=" * 72)
    print("GENERATING REPORT")
    print("=" * 72)

    lines = []
    lines.append("# Multi-Token V3 Strategy Generalizability Results")
    lines.append("")
    lines.append(f"**Run date**: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"**Strategy**: V3 = 20/50 EMA crossover + Positioning overlay + VRP overlay (no stops)")
    lines.append(f"**Baseline**: V1 = 20/50 EMA crossover only")
    lines.append(f"**Rebalancing**: Weekly (Monday)")
    lines.append(f"**Transaction cost**: {COST_BPS} bps round-trip")
    lines.append(f"**Position range**: 0 to 1.5x")
    lines.append(f"**IS period**: ~2022-02 to {IS_END} (positioning data starts 2021-12, +60d warmup)")
    lines.append(f"**OOS period**: {OOS_START} to latest")
    lines.append(f"**Tokens tested**: {len(all_results)}/{len(TARGET_TOKENS)}")
    if skipped:
        lines.append(f"**Skipped**: {', '.join([f'{t} ({r})' for t, r in skipped])}")
    lines.append("")

    # ── 1. Per-Token IS/OOS Metrics ──
    lines.append("## 1. Per-Token Performance: V3 (Full Strategy)")
    lines.append("")
    lines.append("| Token | IS Return | OOS Return | IS Sharpe | OOS Sharpe | IS MaxDD | OOS MaxDD | IS Calmar | OOS Calmar | IS PF | OOS PF |")
    lines.append("|-------|-----------|------------|-----------|------------|----------|-----------|-----------|------------|-------|--------|")

    for _, row in summary_df.iterrows():
        def fmt(v, pct=False):
            if pd.isna(v) or v is None:
                return "N/A"
            if pct:
                return f"{v:.1%}"
            return f"{v:.2f}"

        lines.append(
            f"| {row['token']} "
            f"| {fmt(row['v3_is_ret'], True)} "
            f"| {fmt(row['v3_oos_ret'], True)} "
            f"| {fmt(row['v3_is_sharpe'])} "
            f"| {fmt(row['v3_oos_sharpe'])} "
            f"| {fmt(row['v3_is_maxdd'], True)} "
            f"| {fmt(row['v3_oos_maxdd'], True)} "
            f"| {fmt(row['v3_is_calmar'])} "
            f"| {fmt(row['v3_oos_calmar'])} "
            f"| {fmt(row['v3_is_pf'])} "
            f"| {fmt(row['v3_oos_pf'])} |"
        )

    lines.append("")

    # ── 2. Per-Token V1 vs V3 Comparison ──
    lines.append("## 2. V1 vs V3 Comparison (Overlay Contribution)")
    lines.append("")
    lines.append("| Token | V1 IS Sharpe | V1 OOS Sharpe | V3 IS Sharpe | V3 OOS Sharpe | IS dSharpe | OOS dSharpe | VRP Source | Pos Days |")
    lines.append("|-------|-------------|---------------|-------------|---------------|------------|-------------|------------|----------|")

    for _, row in summary_df.iterrows():
        def fmt(v):
            if pd.isna(v) or v is None:
                return "N/A"
            return f"{v:.2f}"

        def fmt_d(v):
            if pd.isna(v) or v is None:
                return "N/A"
            return f"{v:+.3f}"

        lines.append(
            f"| {row['token']} "
            f"| {fmt(row['v1_is_sharpe'])} "
            f"| {fmt(row['v1_oos_sharpe'])} "
            f"| {fmt(row['v3_is_sharpe'])} "
            f"| {fmt(row['v3_oos_sharpe'])} "
            f"| {fmt_d(row['d_sharpe_is'])} "
            f"| {fmt_d(row['d_sharpe_oos'])} "
            f"| {row['vrp_source']} "
            f"| {row['pos_days']} |"
        )

    lines.append("")

    # ── 3. V1 vs V3 vs Buy-and-Hold ──
    lines.append("## 3. V1 vs V3 vs Buy-and-Hold (OOS Only)")
    lines.append("")
    lines.append("| Token | B&H OOS Return | B&H OOS Sharpe | V1 OOS Return | V1 OOS Sharpe | V3 OOS Return | V3 OOS Sharpe |")
    lines.append("|-------|----------------|----------------|---------------|---------------|---------------|---------------|")

    for _, row in summary_df.iterrows():
        def fmt_r(v):
            if pd.isna(v) or v is None:
                return "N/A"
            return f"{v:.1%}"
        def fmt_s(v):
            if pd.isna(v) or v is None:
                return "N/A"
            return f"{v:.2f}"

        lines.append(
            f"| {row['token']} "
            f"| {fmt_r(row['bh_oos_ret'])} "
            f"| {fmt_s(row['bh_oos_sharpe'])} "
            f"| {fmt_r(row['v1_oos_ret'])} "
            f"| {fmt_s(row['v1_oos_sharpe'])} "
            f"| {fmt_r(row['v3_oos_ret'])} "
            f"| {fmt_s(row['v3_oos_sharpe'])} |"
        )

    lines.append("")

    # ── 4. Aggregate Statistics ──
    lines.append("## 4. Aggregate Cross-Token OOS Statistics")
    lines.append("")
    lines.append(f"| Metric | V1 | V3 | Delta |")
    lines.append(f"|--------|----|----|-------|")
    lines.append(f"| Mean OOS Sharpe | {mean_v1_oos:.3f} | {mean_v3_oos:.3f} | {mean_d_sharpe:+.3f} |")
    lines.append(f"| Median OOS Sharpe | {median_v1_oos:.3f} | {median_v3_oos:.3f} | {median_d_sharpe:+.3f} |")
    lines.append(f"| Mean OOS Return | {valid_oos['v1_oos_ret'].mean():.1%} | {valid_oos['v3_oos_ret'].mean():.1%} | {(valid_oos['v3_oos_ret'].mean() - valid_oos['v1_oos_ret'].mean()):+.1%} |")
    lines.append(f"| Mean OOS MaxDD | {valid_oos['v1_oos_maxdd'].mean():.1%} | {valid_oos['v3_oos_maxdd'].mean():.1%} | {(valid_oos['v3_oos_maxdd'].mean() - valid_oos['v1_oos_maxdd'].mean()):+.1%} |")
    lines.append(f"| Positive OOS Sharpe | {n_positive_v1}/{n_tokens} ({100*n_positive_v1/n_tokens:.0f}%) | {n_positive_v3}/{n_tokens} ({100*n_positive_v3/n_tokens:.0f}%) | |")
    lines.append(f"| V3 beats V1 OOS | | {n_v3_better}/{n_tokens} ({100*n_v3_better/n_tokens:.0f}%) | |")
    lines.append("")

    # ── 5. IS vs OOS Stability ──
    lines.append("## 5. IS/OOS Stability Analysis")
    lines.append("")
    lines.append("Sharpe degradation from IS to OOS (lower = more stable):")
    lines.append("")
    lines.append("| Token | V1 IS Sharpe | V1 OOS Sharpe | V1 Degradation | V3 IS Sharpe | V3 OOS Sharpe | V3 Degradation |")
    lines.append("|-------|-------------|---------------|----------------|-------------|---------------|----------------|")

    for _, row in summary_df.iterrows():
        v1_deg = row['v1_is_sharpe'] - row['v1_oos_sharpe'] if not (np.isnan(row['v1_is_sharpe']) or np.isnan(row['v1_oos_sharpe'])) else np.nan
        v3_deg = row['v3_is_sharpe'] - row['v3_oos_sharpe'] if not (np.isnan(row['v3_is_sharpe']) or np.isnan(row['v3_oos_sharpe'])) else np.nan

        def fmt(v):
            if pd.isna(v):
                return "N/A"
            return f"{v:.2f}"

        def fmt_d(v):
            if pd.isna(v):
                return "N/A"
            return f"{v:+.2f}"

        lines.append(
            f"| {row['token']} "
            f"| {fmt(row['v1_is_sharpe'])} "
            f"| {fmt(row['v1_oos_sharpe'])} "
            f"| {fmt_d(v1_deg)} "
            f"| {fmt(row['v3_is_sharpe'])} "
            f"| {fmt(row['v3_oos_sharpe'])} "
            f"| {fmt_d(v3_deg)} |"
        )

    lines.append("")

    # ── 6. Token-Level Overlay Analysis ──
    lines.append("## 6. Which Tokens Benefit Most/Least from Overlays?")
    lines.append("")

    # Sort by OOS dSharpe
    sorted_df = summary_df.dropna(subset=['d_sharpe_oos']).sort_values('d_sharpe_oos', ascending=False)

    if len(sorted_df) > 0:
        lines.append("### Ranked by OOS Overlay dSharpe (V3 - V1)")
        lines.append("")
        lines.append("| Rank | Token | OOS dSharpe | V1 OOS Sharpe | V3 OOS Sharpe | VRP Source |")
        lines.append("|------|-------|-------------|---------------|---------------|------------|")

        for rank, (_, row) in enumerate(sorted_df.iterrows(), 1):
            marker = ""
            if row['d_sharpe_oos'] > 0.1:
                marker = " (strong benefit)"
            elif row['d_sharpe_oos'] > 0:
                marker = " (mild benefit)"
            elif row['d_sharpe_oos'] > -0.1:
                marker = " (neutral)"
            else:
                marker = " (hurt by overlays)"

            lines.append(
                f"| {rank} | {row['token']} "
                f"| {row['d_sharpe_oos']:+.3f}{marker} "
                f"| {row['v1_oos_sharpe']:.2f} "
                f"| {row['v3_oos_sharpe']:.2f} "
                f"| {row['vrp_source']} |"
            )

        lines.append("")

        # Best and worst
        best_token = sorted_df.iloc[0]['token']
        best_d = sorted_df.iloc[0]['d_sharpe_oos']
        worst_token = sorted_df.iloc[-1]['token']
        worst_d = sorted_df.iloc[-1]['d_sharpe_oos']

        lines.append(f"- **Most improved by overlays**: {best_token} (dSharpe = {best_d:+.3f})")
        lines.append(f"- **Least improved by overlays**: {worst_token} (dSharpe = {worst_d:+.3f})")
        lines.append("")

    # ── 7. Detailed OOS MaxDD Comparison ──
    lines.append("## 7. Drawdown Comparison (OOS)")
    lines.append("")
    lines.append("| Token | V1 OOS MaxDD | V3 OOS MaxDD | Delta (positive = less DD) | B&H OOS MaxDD |")
    lines.append("|-------|-------------|-------------|--------------------------|---------------|")

    for _, row in summary_df.iterrows():
        v1_dd = row['v1_oos_maxdd']
        v3_dd = row['v3_oos_maxdd']
        bh_dd = all_results[row['token']]['bh_oos']['max_dd']
        delta = v3_dd - v1_dd  # less negative = better

        def fmt_dd(v):
            if pd.isna(v):
                return "N/A"
            return f"{v:.1%}"

        def fmt_delta(v):
            if pd.isna(v):
                return "N/A"
            return f"{v:+.1%}"

        lines.append(
            f"| {row['token']} "
            f"| {fmt_dd(v1_dd)} "
            f"| {fmt_dd(v3_dd)} "
            f"| {fmt_delta(delta)} "
            f"| {fmt_dd(bh_dd)} |"
        )

    lines.append("")

    # ── 8. Pass Rate and Verdict ──
    lines.append("## 8. Pass Rate Analysis")
    lines.append("")
    lines.append(f"### OOS Sharpe > 0 (profitable)")
    lines.append(f"- V1: {n_positive_v1}/{n_tokens} ({100*n_positive_v1/n_tokens:.0f}%)")
    lines.append(f"- V3: {n_positive_v3}/{n_tokens} ({100*n_positive_v3/n_tokens:.0f}%)")
    lines.append("")

    # Sharpe > 0.5 (acceptable)
    n_v1_good = (valid_oos['v1_oos_sharpe'] > 0.5).sum()
    n_v3_good = (valid_oos['v3_oos_sharpe'] > 0.5).sum()
    lines.append(f"### OOS Sharpe > 0.5 (acceptable risk-adjusted returns)")
    lines.append(f"- V1: {n_v1_good}/{n_tokens} ({100*n_v1_good/n_tokens:.0f}%)")
    lines.append(f"- V3: {n_v3_good}/{n_tokens} ({100*n_v3_good/n_tokens:.0f}%)")
    lines.append("")

    # Sharpe > 1.0 (strong)
    n_v1_strong = (valid_oos['v1_oos_sharpe'] > 1.0).sum()
    n_v3_strong = (valid_oos['v3_oos_sharpe'] > 1.0).sum()
    lines.append(f"### OOS Sharpe > 1.0 (strong)")
    lines.append(f"- V1: {n_v1_strong}/{n_tokens} ({100*n_v1_strong/n_tokens:.0f}%)")
    lines.append(f"- V3: {n_v3_strong}/{n_tokens} ({100*n_v3_strong/n_tokens:.0f}%)")
    lines.append("")

    # V3 beats B&H
    n_v3_beats_bh = (valid_oos['v3_oos_sharpe'] > valid_oos['bh_oos_sharpe']).sum()
    lines.append(f"### V3 beats Buy-and-Hold (OOS Sharpe)")
    lines.append(f"- {n_v3_beats_bh}/{n_tokens} ({100*n_v3_beats_bh/n_tokens:.0f}%)")
    lines.append("")

    # ── 9. Final Verdict ──
    lines.append("## 9. Final Verdict: Does V3 Generalize Beyond BTC?")
    lines.append("")

    # Compute various criteria
    v3_pass_rate = n_positive_v3 / n_tokens if n_tokens > 0 else 0
    v3_mean_oos = mean_v3_oos
    overlay_helps_rate = n_v3_better / n_tokens if n_tokens > 0 else 0
    overlay_mean_d = mean_d_sharpe

    lines.append("### Key Numbers")
    lines.append("")
    lines.append(f"- **V3 positive OOS Sharpe rate**: {100*v3_pass_rate:.0f}% ({n_positive_v3}/{n_tokens})")
    lines.append(f"- **V3 mean OOS Sharpe**: {v3_mean_oos:.3f}")
    lines.append(f"- **V3 median OOS Sharpe**: {median_v3_oos:.3f}")
    lines.append(f"- **Overlay improvement rate (V3 > V1)**: {100*overlay_helps_rate:.0f}% ({n_v3_better}/{n_tokens})")
    lines.append(f"- **Mean overlay OOS dSharpe**: {overlay_mean_d:+.3f}")
    lines.append(f"- **Median overlay OOS dSharpe**: {median_d_sharpe:+.3f}")
    lines.append("")

    # Verdict logic
    if v3_pass_rate >= 0.7 and v3_mean_oos > 0.3:
        if overlay_helps_rate >= 0.6 and overlay_mean_d > 0.05:
            verdict = "STRONG GENERALIZATION"
            explanation = (
                f"V3 is profitable on {100*v3_pass_rate:.0f}% of tokens OOS with mean Sharpe {v3_mean_oos:.2f}. "
                f"The positioning + VRP overlays improve {100*overlay_helps_rate:.0f}% of tokens with mean dSharpe {overlay_mean_d:+.3f}. "
                f"Both the base strategy and overlays generalize well across the crypto universe."
            )
        else:
            verdict = "BASE GENERALIZES, OVERLAYS MIXED"
            explanation = (
                f"The EMA crossover base is profitable on {100*v3_pass_rate:.0f}% of tokens (mean Sharpe {v3_mean_oos:.2f}). "
                f"However, overlays only improve {100*overlay_helps_rate:.0f}% of tokens (mean dSharpe {overlay_mean_d:+.3f}). "
                f"The base strategy generalizes but overlay benefits are token-specific."
            )
    elif v3_pass_rate >= 0.5:
        if overlay_helps_rate >= 0.5:
            verdict = "MODERATE GENERALIZATION"
            explanation = (
                f"V3 is profitable on {100*v3_pass_rate:.0f}% of tokens OOS with mean Sharpe {v3_mean_oos:.2f}. "
                f"Overlays help more often than they hurt ({100*overlay_helps_rate:.0f}%). "
                f"Strategy works on most tokens but not universally."
            )
        else:
            verdict = "BASE OK, OVERLAYS HURT"
            explanation = (
                f"V3 is profitable on {100*v3_pass_rate:.0f}% of tokens but overlays make things worse "
                f"for {100*(1-overlay_helps_rate):.0f}% of tokens (mean dSharpe {overlay_mean_d:+.3f}). "
                f"Consider using V1 (base only) for non-BTC tokens."
            )
    else:
        verdict = "DOES NOT GENERALIZE"
        explanation = (
            f"V3 is profitable on only {100*v3_pass_rate:.0f}% of tokens OOS with mean Sharpe {v3_mean_oos:.2f}. "
            f"The strategy is too dependent on BTC-specific dynamics. "
            f"Momentum crossover + overlays do not transfer well to altcoins."
        )

    lines.append(f"### **{verdict}**")
    lines.append("")
    lines.append(explanation)
    lines.append("")

    # Recommendations
    lines.append("### Recommendations")
    lines.append("")

    if "STRONG" in verdict:
        lines.append("1. V3 can be deployed across the tested token universe")
        lines.append("2. Consider equal-weight or inverse-vol-weighted multi-token portfolio")
        lines.append("3. Monitor per-token performance; remove tokens that degrade significantly")
        lines.append("4. The overlay (positioning + VRP) adds value cross-sectionally")
    elif "BASE GENERALIZES" in verdict:
        lines.append("1. Deploy V1 (EMA-only) across the token universe as the base strategy")
        lines.append("2. Apply positioning + VRP overlays SELECTIVELY to tokens where they improve OOS Sharpe")
        lines.append("3. Use per-token overlay toggles rather than uniform overlays")
    elif "MODERATE" in verdict:
        lines.append("1. V3 can be used on the subset of tokens with positive OOS Sharpe")
        lines.append("2. For underperforming tokens, consider different overlay parameters or V1-only")
        lines.append("3. The strategy is not universal -- requires per-token qualification")
    elif "BASE OK" in verdict:
        lines.append("1. Use V1 (EMA-only) as the multi-token strategy")
        lines.append("2. Overlays hurt more than they help outside BTC -- do NOT apply uniformly")
        lines.append("3. Investigate why overlays fail: different crowd behavior per token?")
    else:
        lines.append("1. V3 does NOT generalize beyond BTC")
        lines.append("2. EMA crossover underperforms on many altcoins (too choppy?)")
        lines.append("3. Consider different base strategies for altcoins (e.g., relative momentum)")
        lines.append("4. The BTC result may be specific to BTC's unique market structure")

    lines.append("")

    # Write report
    report_path = OUTPUT_DIR / 'multi_token_v3_results.md'
    with open(report_path, 'w') as f:
        f.write('\n'.join(lines))
    print(f"\nReport saved to: {report_path}")

    # Final console summary
    print(f"\n{'='*72}")
    print(f"VERDICT: {verdict}")
    print(f"{'='*72}")
    print(explanation)


if __name__ == '__main__':
    main()
