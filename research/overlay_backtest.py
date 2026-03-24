#!/usr/bin/env python3
"""
Overlay Backtest: Positioning & Crisis signals as OVERLAYS on trend-following base.

Architecture:
  Base: Trend-following (50/200 SMA) — always long or flat, never short
  Layer 1: Positioning signals adjust sizing (0.3x to 1.5x)
  Layer 2: Crisis hedge (oil + BTC drawdown) exits positions
  Layer 3: Macro agreement boost (US10Y + DXY)

Rebalancing: Weekly (Monday) with 10 bps round-trip cost on changes.

Variants:
  1. Base Only (trend following)
  2. Base + Positioning
  3. Base + Positioning + Crisis
  4. Base + All Layers
  5. Base + Crisis Only
"""

import pandas as pd
import numpy as np
import warnings
from pathlib import Path
from datetime import datetime

warnings.filterwarnings('ignore')

BASE_DIR = Path('/workspace/crypto_backtest')
DATA_DIR = BASE_DIR / 'data'
OUTPUT_DIR = BASE_DIR / 'research'

# ── Temporal split ──────────────────────────────────────────────────────────
IS_START = '2020-09-01'
IS_END = '2024-12-31'
OOS_START = '2025-01-01'
COST_BPS = 10  # round-trip cost in basis points

# ── 1. Load Data ────────────────────────────────────────────────────────────

def load_btc_daily():
    """Load BTC spot 1h data and resample to daily close."""
    print("[1/7] Loading BTC spot data...")
    btc = pd.read_parquet(DATA_DIR / 'spot/1h_cache/BTC_1h.parquet')
    btc.index = pd.to_datetime(btc.index)
    btc.index.name = 'date'
    # Resample to daily using last close of each day
    daily = btc['close'].resample('D').last().dropna()
    daily = daily.to_frame('close')
    # Also get daily OHLCV for volume etc
    daily['open'] = btc['open'].resample('D').first()
    daily['high'] = btc['high'].resample('D').max()
    daily['low'] = btc['low'].resample('D').min()
    daily['volume'] = btc['volume'].resample('D').sum()
    print(f"  BTC daily: {daily.index.min().date()} to {daily.index.max().date()}, {len(daily)} rows")
    return daily


def load_positioning():
    """Load Binance positioning data for BTCUSDT."""
    print("[2/7] Loading positioning data...")
    pos = pd.read_parquet(DATA_DIR / 'alternative/binance_metrics/all_symbols_daily_ls.parquet')
    pos = pos[pos['symbol'] == 'BTCUSDT'].copy()
    pos['date'] = pd.to_datetime(pos['date'])
    pos = pos.set_index('date').sort_index()
    # Keep only the columns we need
    pos = pos[['sum_toptrader_ls_ratio', 'count_toptrader_ls_ratio', 'count_ls_ratio']].copy()
    pos = pos[~pos.index.duplicated(keep='last')]
    print(f"  Positioning: {pos.index.min().date()} to {pos.index.max().date()}, {len(pos)} rows")
    return pos


def load_macro(name, filename):
    """Load a macro series."""
    df = pd.read_parquet(DATA_DIR / f'alternative/macro/{filename}')
    df['Date'] = pd.to_datetime(df['Date'])
    df = df.set_index('Date').sort_index()
    df = df[~df.index.duplicated(keep='last')]
    return df['Close'].rename(name)


def load_all_macro():
    """Load all macro data."""
    print("[3/7] Loading macro data...")
    oil = load_macro('oil', 'oil_wti.parquet')
    us10y = load_macro('us10y', 'us10y_yield.parquet')
    dxy = load_macro('dxy', 'usd_index.parquet')
    print(f"  Oil: {oil.index.min().date()} to {oil.index.max().date()}")
    print(f"  US10Y: {us10y.index.min().date()} to {us10y.index.max().date()}")
    print(f"  DXY: {dxy.index.min().date()} to {dxy.index.max().date()}")
    return oil, us10y, dxy


# ── 2. Build Signals ────────────────────────────────────────────────────────

def build_trend_signal(btc_daily):
    """Base trend-following signal: long when close > 50d SMA AND close > 200d SMA."""
    print("[4/7] Building trend signal (50/200 SMA)...")
    sma50 = btc_daily['close'].rolling(50, min_periods=50).mean()
    sma200 = btc_daily['close'].rolling(200, min_periods=200).mean()
    # Long when above both SMAs
    trend_long = ((btc_daily['close'] > sma50) & (btc_daily['close'] > sma200)).astype(float)
    # Exit when below 50 SMA (regardless of 200 SMA)
    # The entry requires both, exit triggers on 50 SMA cross below
    # We need state tracking for hysteresis: enter on both, exit on 50 SMA cross
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
            # Entry: close > 50 SMA AND close > 200 SMA
            if close > s50 and close > s200:
                in_position = True
                position.iloc[i] = 1.0
            else:
                position.iloc[i] = 0.0
        else:
            # Exit: close < 50 SMA
            if close < s50:
                in_position = False
                position.iloc[i] = 0.0
            else:
                position.iloc[i] = 1.0
    print(f"  Trend signal: {position.sum():.0f} days long out of {len(position)} total ({100*position.mean():.1f}%)")
    return position, sma50, sma200


def build_positioning_signal(btc_daily, positioning):
    """
    Layer 1: Positioning size adjustment.
    - Top Trader L/S: sum_toptrader_ls_ratio, 30d rolling z-score
    - L/S Divergence: (count_toptrader_ls_ratio - count_ls_ratio), 30d rolling z-score
    - Combined: average of both z-scores
    """
    print("[5/7] Building positioning signals...")
    # Merge positioning onto daily index
    pos = positioning.reindex(btc_daily.index).ffill()

    # Z-scores (30d rolling)
    def rolling_zscore(s, window=30):
        mu = s.rolling(window, min_periods=15).mean()
        sigma = s.rolling(window, min_periods=15).std()
        return (s - mu) / sigma.replace(0, np.nan)

    z_toptrader = rolling_zscore(pos['sum_toptrader_ls_ratio'])
    divergence = pos['count_toptrader_ls_ratio'] - pos['count_ls_ratio']
    z_divergence = rolling_zscore(divergence)

    # Combined z-score
    combined_z = (z_toptrader + z_divergence) / 2.0

    # Size multiplier mapping
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


def build_crisis_signal(btc_daily, oil):
    """
    Layer 2: Crisis hedge.
    - Oil 20d momentum: 20-day return of oil close
    - BTC 50d return
    - Crisis exit when BTC in downtrend + oil stress, or deep crisis
    """
    print("[6/7] Building crisis signal...")
    oil_daily = oil.reindex(btc_daily.index).ffill()
    oil_20d_ret = oil_daily.pct_change(20)
    btc_50d_ret = btc_daily['close'].pct_change(50)

    crisis_multiplier = pd.Series(1.0, index=btc_daily.index)

    # Deep crisis: BTC 50d return < -20% => always exit
    deep_crisis = btc_50d_ret < -0.20
    crisis_multiplier[deep_crisis] = 0.0

    # Moderate crisis: BTC 50d return < -10% AND oil 20d return > 5%
    moderate_crisis = (btc_50d_ret < -0.10) & (oil_20d_ret > 0.05)
    crisis_multiplier[moderate_crisis] = 0.0

    n_crisis_days = (crisis_multiplier == 0.0).sum()
    print(f"  Crisis days: {n_crisis_days} ({100*n_crisis_days/len(btc_daily):.1f}%)")
    return crisis_multiplier, btc_50d_ret, oil_20d_ret


def build_macro_signal(btc_daily, us10y, dxy, combined_pos_z):
    """
    Layer 3: Macro agreement boost.
    - US10Y 20d change expanding rank
    - DXY 20d momentum expanding rank
    - Composite < 33rd percentile + positioning bullish => 1.3x boost
    """
    print("[7/7] Building macro agreement signal...")
    us10y_daily = us10y.reindex(btc_daily.index).ffill()
    dxy_daily = dxy.reindex(btc_daily.index).ffill()

    us10y_20d_change = us10y_daily.diff(20)
    dxy_20d_mom = dxy_daily.pct_change(20)

    # Expanding rank (percentile rank over all history up to that point)
    def expanding_rank(s):
        return s.expanding(min_periods=50).rank(pct=True)

    us10y_rank = expanding_rank(us10y_20d_change)
    dxy_rank = expanding_rank(dxy_20d_mom)

    # Composite: sum of ranks (lower = more bullish for risk assets)
    macro_composite = us10y_rank + dxy_rank

    # Macro bullish: composite < 33rd percentile of its own expanding distribution
    macro_threshold = macro_composite.expanding(min_periods=50).quantile(0.33)
    macro_bullish = macro_composite < macro_threshold

    # Positioning bullish: combined z < -0.5
    pos_bullish = combined_pos_z < -0.5

    # Agreement: both bullish
    agreement = macro_bullish & pos_bullish

    macro_multiplier = pd.Series(1.0, index=btc_daily.index)
    macro_multiplier[agreement] = 1.3

    n_boost = agreement.sum()
    print(f"  Macro agreement boost days: {n_boost} ({100*n_boost/len(btc_daily):.1f}%)")
    return macro_multiplier


# ── 3. Backtest Engine ──────────────────────────────────────────────────────

def compute_final_position(base_pos, pos_mult, crisis_mult, macro_mult, variant):
    """Compute final position based on variant."""
    if variant == 'base_only':
        final = base_pos.copy()
    elif variant == 'base_positioning':
        final = base_pos * pos_mult
    elif variant == 'base_positioning_crisis':
        final = base_pos * pos_mult * crisis_mult
    elif variant == 'base_all':
        final = base_pos * pos_mult * crisis_mult * macro_mult
    elif variant == 'base_crisis_only':
        final = base_pos * crisis_mult
    else:
        raise ValueError(f"Unknown variant: {variant}")

    # Clip to [0, 1.5] — never short, max 1.5x
    final = final.clip(0, 1.5)
    return final


def run_backtest(btc_daily, final_position, cost_bps=COST_BPS, rebalance_freq='W-MON'):
    """
    Run backtest with weekly rebalancing and transaction costs.
    Returns daily returns series and metadata.
    """
    daily_ret = btc_daily['close'].pct_change()

    # Identify rebalance days (every Monday)
    rebalance_dates = btc_daily.index.to_series().groupby(
        btc_daily.index.to_period(rebalance_freq.replace('W-MON', 'W'))
    ).first()
    rebalance_set = set(rebalance_dates.values)

    # Build actual held position (only changes on rebalance days)
    held_position = pd.Series(0.0, index=btc_daily.index)
    current_pos = 0.0
    costs = pd.Series(0.0, index=btc_daily.index)

    for dt in btc_daily.index:
        if dt in rebalance_set:
            new_pos = final_position.loc[dt]
            if not pd.isna(new_pos):
                # Cost on position change
                pos_change = abs(new_pos - current_pos)
                costs.loc[dt] = pos_change * cost_bps / 10000.0
                current_pos = new_pos
        held_position.loc[dt] = current_pos

    # Strategy return: position * daily return - costs
    strat_ret = held_position.shift(1) * daily_ret - costs

    return strat_ret, held_position, costs


def compute_metrics(returns, label=""):
    """Compute performance metrics from a daily returns series."""
    returns = returns.dropna()
    if len(returns) == 0:
        return {}

    total_ret = (1 + returns).prod() - 1
    n_years = len(returns) / 252
    ann_ret = (1 + total_ret) ** (1 / max(n_years, 0.01)) - 1
    ann_vol = returns.std() * np.sqrt(252)
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0

    # Max drawdown
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
    n_years = len(held_position) / 252
    return total_turnover / max(n_years, 0.01)


def compute_cost_drag(costs, n_days):
    """Compute annualized cost drag."""
    total_cost = costs.sum()
    n_years = n_days / 252
    return total_cost / max(n_years, 0.01)


# ── 4. Regime Classification ────────────────────────────────────────────────

def classify_regimes(btc_daily):
    """Classify each day into UPTREND/RANGE/DOWNTREND/CRISIS."""
    ret_50d = btc_daily['close'].pct_change(50)
    ret_20d = btc_daily['close'].pct_change(20)

    regime = pd.Series('RANGE', index=btc_daily.index)
    regime[ret_50d > 0.15] = 'UPTREND'
    regime[(ret_50d < -0.10) & (ret_50d >= -0.20)] = 'DOWNTREND'
    regime[ret_50d < -0.20] = 'CRISIS'

    return regime


# ── 5. Main Execution ───────────────────────────────────────────────────────

def main():
    print("=" * 70)
    print("OVERLAY BACKTEST: Positioning & Crisis on Trend-Following Base")
    print("=" * 70)
    print()

    # Load data
    btc_daily = load_btc_daily()
    positioning = load_positioning()
    oil, us10y, dxy = load_all_macro()
    print()

    # Build signals
    base_position, sma50, sma200 = build_trend_signal(btc_daily)
    pos_multiplier, combined_z = build_positioning_signal(btc_daily, positioning)
    crisis_multiplier, btc_50d_ret, oil_20d_ret = build_crisis_signal(btc_daily, oil)
    macro_multiplier = build_macro_signal(btc_daily, us10y, dxy, combined_z)
    print()

    # Define variants
    variants = {
        'base_only': 'Base Only (Trend)',
        'base_positioning': 'Base + Positioning',
        'base_positioning_crisis': 'Base + Pos + Crisis',
        'base_all': 'Base + All Layers',
        'base_crisis_only': 'Base + Crisis Only',
    }

    # Run backtests
    print("Running backtests...")
    results = {}
    for variant_key, variant_name in variants.items():
        final_pos = compute_final_position(
            base_position, pos_multiplier, crisis_multiplier, macro_multiplier, variant_key
        )
        strat_ret, held_pos, costs = run_backtest(btc_daily, final_pos)

        # Split IS/OOS
        is_mask = (btc_daily.index >= IS_START) & (btc_daily.index <= IS_END)
        oos_mask = btc_daily.index >= OOS_START

        is_ret = strat_ret[is_mask]
        oos_ret = strat_ret[oos_mask]

        is_metrics = compute_metrics(is_ret, f"{variant_name} (IS)")
        oos_metrics = compute_metrics(oos_ret, f"{variant_name} (OOS)")

        is_turnover = compute_turnover(held_pos[is_mask])
        oos_turnover = compute_turnover(held_pos[oos_mask])
        is_cost_drag = compute_cost_drag(costs[is_mask], is_mask.sum())
        oos_cost_drag = compute_cost_drag(costs[oos_mask], oos_mask.sum())

        results[variant_key] = {
            'name': variant_name,
            'is': is_metrics,
            'oos': oos_metrics,
            'is_turnover': is_turnover,
            'oos_turnover': oos_turnover,
            'is_cost_drag': is_cost_drag,
            'oos_cost_drag': oos_cost_drag,
            'strat_ret': strat_ret,
            'held_pos': held_pos,
            'costs': costs,
            'final_pos': final_pos,
        }
        print(f"  {variant_name}: IS Sharpe={is_metrics['sharpe']:.2f}, OOS Sharpe={oos_metrics['sharpe']:.2f}")

    print()

    # ── Regime Analysis ──
    print("Regime-specific analysis...")
    regime = classify_regimes(btc_daily)
    oos_mask = btc_daily.index >= OOS_START
    full_mask = (btc_daily.index >= IS_START)

    regime_results = {}
    for variant_key, data in results.items():
        regime_results[variant_key] = {}
        for r in ['UPTREND', 'RANGE', 'DOWNTREND', 'CRISIS']:
            r_mask = (regime == r) & oos_mask
            r_ret = data['strat_ret'][r_mask]
            if len(r_ret.dropna()) > 5:
                regime_results[variant_key][r] = compute_metrics(r_ret, f"{data['name']} ({r})")
            else:
                regime_results[variant_key][r] = {'ann_return': np.nan, 'sharpe': np.nan, 'max_dd': np.nan}
    print("  Done.")
    print()

    # ── Position Distribution ──
    print("Position distribution analysis...")
    pos_dist = {}
    pos_bins = [-0.01, 0.01, 0.35, 0.55, 0.85, 1.15, 1.40, 2.0]
    pos_labels = ['0.0', '~0.3', '~0.5', '~0.65', '~1.0', '~1.3', '~1.5']
    assert len(pos_bins) - 1 == len(pos_labels), f"bins={len(pos_bins)}, labels={len(pos_labels)}"
    for variant_key, data in results.items():
        hp = data['held_pos'][(btc_daily.index >= IS_START)]
        hp_binned = pd.cut(hp, bins=pos_bins, labels=pos_labels, include_lowest=True)
        pos_dist[variant_key] = hp_binned.value_counts(normalize=True).reindex(pos_labels, fill_value=0)
    print("  Done.")
    print()

    # ── Monthly OOS Returns for best variant ──
    print("Monthly OOS returns...")
    # Find best OOS Sharpe variant
    best_key = max(results.keys(), key=lambda k: results[k]['oos']['sharpe'])
    best_name = results[best_key]['name']
    print(f"  Best OOS variant: {best_name}")

    best_oos_ret = results[best_key]['strat_ret'][oos_mask]
    monthly_oos = (1 + best_oos_ret).resample('ME').prod() - 1

    # ── Generate Report ──
    print()
    print("=" * 70)
    print("GENERATING REPORT")
    print("=" * 70)

    report_lines = []
    report_lines.append("# Overlay Backtest Results: Positioning & Crisis on Trend-Following Base")
    report_lines.append("")
    report_lines.append(f"**Run date**: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    report_lines.append(f"**IS period**: {IS_START} to {IS_END}")
    report_lines.append(f"**OOS period**: {OOS_START} to latest")
    report_lines.append(f"**Rebalancing**: Weekly (Monday)")
    report_lines.append(f"**Transaction cost**: {COST_BPS} bps round-trip")
    report_lines.append("")
    report_lines.append("## Architecture")
    report_lines.append("")
    report_lines.append("- **Base**: Trend following (50/200 SMA crossover with hysteresis)")
    report_lines.append("- **Layer 1**: Positioning sizing (0.3x to 1.5x based on Binance top trader L/S z-scores)")
    report_lines.append("- **Layer 2**: Crisis hedge (exit on BTC drawdown + oil stress)")
    report_lines.append("- **Layer 3**: Macro agreement boost (US10Y + DXY rank composite)")
    report_lines.append("- **Rebalancing**: Weekly to avoid excessive turnover from noisy daily signals")
    report_lines.append("")

    # ── 1. Variant Performance Table ──
    report_lines.append("## 1. Variant Performance Table")
    report_lines.append("")
    header = "| Variant | IS Return | OOS Return | IS Sharpe | OOS Sharpe | IS MaxDD | OOS MaxDD | IS Calmar | OOS Calmar | Turnover/yr | Cost Drag/yr |"
    sep =    "|---------|-----------|------------|-----------|------------|----------|-----------|-----------|------------|-------------|--------------|"
    report_lines.append(header)
    report_lines.append(sep)

    for vk in ['base_only', 'base_positioning', 'base_positioning_crisis', 'base_all', 'base_crisis_only']:
        r = results[vk]
        is_m = r['is']
        oos_m = r['oos']
        line = f"| {r['name']} | {is_m['ann_return']:.1%} | {oos_m['ann_return']:.1%} | {is_m['sharpe']:.2f} | {oos_m['sharpe']:.2f} | {is_m['max_dd']:.1%} | {oos_m['max_dd']:.1%} | {is_m['calmar']:.2f} | {oos_m['calmar']:.2f} | {r['is_turnover']:.1f} | {r['is_cost_drag']:.2%} |"
        report_lines.append(line)

    report_lines.append("")

    # ── 2. Layer Contribution Analysis ──
    report_lines.append("## 2. Layer Contribution Analysis")
    report_lines.append("")
    report_lines.append("Marginal improvement of each layer (IS / OOS):")
    report_lines.append("")

    base_is = results['base_only']['is']
    base_oos = results['base_only']['oos']

    layers = [
        ('+ Positioning', 'base_positioning'),
        ('+ Positioning + Crisis', 'base_positioning_crisis'),
        ('+ All Layers', 'base_all'),
        ('+ Crisis Only (no positioning)', 'base_crisis_only'),
    ]

    report_lines.append("| Layer Added | IS dSharpe | OOS dSharpe | IS dReturn | OOS dReturn | IS dMaxDD | OOS dMaxDD |")
    report_lines.append("|-------------|------------|-------------|------------|-------------|-----------|------------|")

    prev_is = base_is
    prev_oos = base_oos
    for label, vk in layers:
        curr_is = results[vk]['is']
        curr_oos = results[vk]['oos']
        d_sharpe_is = curr_is['sharpe'] - base_is['sharpe']
        d_sharpe_oos = curr_oos['sharpe'] - base_oos['sharpe']
        d_ret_is = curr_is['ann_return'] - base_is['ann_return']
        d_ret_oos = curr_oos['ann_return'] - base_oos['ann_return']
        d_dd_is = curr_is['max_dd'] - base_is['max_dd']
        d_dd_oos = curr_oos['max_dd'] - base_oos['max_dd']
        line = f"| {label} | {d_sharpe_is:+.2f} | {d_sharpe_oos:+.2f} | {d_ret_is:+.1%} | {d_ret_oos:+.1%} | {d_dd_is:+.1%} | {d_dd_oos:+.1%} |"
        report_lines.append(line)

    report_lines.append("")
    report_lines.append("Note: All deltas are vs Base Only. Positive dMaxDD means LESS drawdown (improvement).")
    report_lines.append("")

    # Marginal (sequential) contribution
    report_lines.append("### Sequential marginal contribution (each layer added incrementally):")
    report_lines.append("")
    report_lines.append("| Step | IS dSharpe | OOS dSharpe |")
    report_lines.append("|------|------------|-------------|")

    seq = [('base_only', 'Base Only'), ('base_positioning', '+ Positioning'), ('base_positioning_crisis', '+ Crisis'), ('base_all', '+ Macro')]
    for i in range(1, len(seq)):
        prev_k, prev_n = seq[i-1]
        curr_k, curr_n = seq[i]
        d_is = results[curr_k]['is']['sharpe'] - results[prev_k]['is']['sharpe']
        d_oos = results[curr_k]['oos']['sharpe'] - results[prev_k]['oos']['sharpe']
        report_lines.append(f"| {curr_n} | {d_is:+.3f} | {d_oos:+.3f} |")

    report_lines.append("")

    # ── 3. Regime-Specific OOS Performance ──
    report_lines.append("## 3. Regime-Specific OOS Performance")
    report_lines.append("")

    # Count regime days in OOS
    oos_regime = regime[oos_mask]
    report_lines.append("Regime day counts (OOS):")
    for r in ['UPTREND', 'RANGE', 'DOWNTREND', 'CRISIS']:
        count = (oos_regime == r).sum()
        report_lines.append(f"- {r}: {count} days ({100*count/len(oos_regime):.0f}%)")
    report_lines.append("")

    report_lines.append("| Variant | UPTREND Ann Ret | RANGE Ann Ret | DOWNTREND Ann Ret | CRISIS Ann Ret |")
    report_lines.append("|---------|-----------------|---------------|-------------------|----------------|")

    for vk in ['base_only', 'base_positioning', 'base_positioning_crisis', 'base_all', 'base_crisis_only']:
        rr = regime_results[vk]
        parts = [f"| {results[vk]['name']}"]
        for r in ['UPTREND', 'RANGE', 'DOWNTREND', 'CRISIS']:
            ar = rr[r].get('ann_return', np.nan)
            if pd.isna(ar):
                parts.append("N/A")
            else:
                parts.append(f"{ar:.1%}")
        report_lines.append(" | ".join(parts) + " |")

    report_lines.append("")

    # Also show Sharpe by regime
    report_lines.append("| Variant | UPTREND Sharpe | RANGE Sharpe | DOWNTREND Sharpe | CRISIS Sharpe |")
    report_lines.append("|---------|----------------|--------------|------------------|---------------|")

    for vk in ['base_only', 'base_positioning', 'base_positioning_crisis', 'base_all', 'base_crisis_only']:
        rr = regime_results[vk]
        parts = [f"| {results[vk]['name']}"]
        for r in ['UPTREND', 'RANGE', 'DOWNTREND', 'CRISIS']:
            s = rr[r].get('sharpe', np.nan)
            if pd.isna(s):
                parts.append("N/A")
            else:
                parts.append(f"{s:.2f}")
        report_lines.append(" | ".join(parts) + " |")

    report_lines.append("")

    # ── 4. Monthly OOS Returns ──
    report_lines.append(f"## 4. Monthly Returns (OOS) — {best_name}")
    report_lines.append("")
    report_lines.append("| Month | Return |")
    report_lines.append("|-------|--------|")
    for dt, ret in monthly_oos.items():
        report_lines.append(f"| {dt.strftime('%Y-%m')} | {ret:.2%} |")

    cum_oos = (1 + best_oos_ret.dropna()).cumprod().iloc[-1] - 1
    report_lines.append(f"| **Cumulative** | **{cum_oos:.2%}** |")
    report_lines.append("")

    # ── 5. Base strategy Alpha ──
    report_lines.append("## 5. Overlay Alpha vs Base")
    report_lines.append("")
    report_lines.append("Net alpha = Overlay variant metrics - Base Only metrics")
    report_lines.append("")
    report_lines.append("| Variant | IS Alpha (Return) | OOS Alpha (Return) | IS Alpha (Sharpe) | OOS Alpha (Sharpe) | IS DD Improvement | OOS DD Improvement |")
    report_lines.append("|---------|-------------------|--------------------|-------------------|--------------------|-------------------|--------------------|")

    for vk in ['base_positioning', 'base_positioning_crisis', 'base_all', 'base_crisis_only']:
        r = results[vk]
        alpha_ret_is = r['is']['ann_return'] - base_is['ann_return']
        alpha_ret_oos = r['oos']['ann_return'] - base_oos['ann_return']
        alpha_sharpe_is = r['is']['sharpe'] - base_is['sharpe']
        alpha_sharpe_oos = r['oos']['sharpe'] - base_oos['sharpe']
        dd_imp_is = r['is']['max_dd'] - base_is['max_dd']
        dd_imp_oos = r['oos']['max_dd'] - base_oos['max_dd']
        report_lines.append(f"| {r['name']} | {alpha_ret_is:+.1%} | {alpha_ret_oos:+.1%} | {alpha_sharpe_is:+.2f} | {alpha_sharpe_oos:+.2f} | {dd_imp_is:+.1%} | {dd_imp_oos:+.1%} |")

    report_lines.append("")
    report_lines.append("Positive Return/Sharpe alpha = overlay improves. Positive DD improvement = less drawdown (better).")
    report_lines.append("")

    # ── 6. Position Distribution ──
    report_lines.append("## 6. Position Distribution (% of time at each level)")
    report_lines.append("")
    report_lines.append("| Variant | 0.0 (Flat) | ~0.3 | ~0.5 | ~0.65 | ~1.0 | ~1.3 | ~1.5 |")
    report_lines.append("|---------|------------|------|------|-------|------|------|------|")

    for vk in ['base_only', 'base_positioning', 'base_positioning_crisis', 'base_all', 'base_crisis_only']:
        r = results[vk]
        dist = pos_dist[vk]
        parts = [f"| {r['name']}"]
        for lbl in pos_labels:
            parts.append(f"{dist[lbl]:.1%}")
        report_lines.append(" | ".join(parts) + " |")

    report_lines.append("")

    # ── 7. Key Conclusions ──
    report_lines.append("## 7. Key Conclusions")
    report_lines.append("")

    # Determine best variant
    best_oos_sharpe = max(results.values(), key=lambda x: x['oos']['sharpe'])
    best_is_sharpe = max(results.values(), key=lambda x: x['is']['sharpe'])

    # Check if overlay improves base
    best_overlay_key = max(
        [k for k in results.keys() if k != 'base_only'],
        key=lambda k: results[k]['oos']['sharpe']
    )
    overlay_improvement = results[best_overlay_key]['oos']['sharpe'] - results['base_only']['oos']['sharpe']

    report_lines.append(f"### Does the overlay improve the base strategy?")
    report_lines.append("")
    if overlay_improvement > 0.05:
        report_lines.append(f"**YES** — Best overlay ({results[best_overlay_key]['name']}) improves OOS Sharpe by {overlay_improvement:+.2f}")
    elif overlay_improvement > 0:
        report_lines.append(f"**MARGINAL** — Best overlay ({results[best_overlay_key]['name']}) improves OOS Sharpe by only {overlay_improvement:+.2f}")
    else:
        report_lines.append(f"**NO** — Best overlay ({results[best_overlay_key]['name']}) worsens OOS Sharpe by {overlay_improvement:+.2f}")

    report_lines.append("")
    report_lines.append(f"### Is the improvement statistically significant?")
    report_lines.append("")

    # Bootstrap test: difference in means of daily returns
    best_overlay_ret = results[best_overlay_key]['strat_ret'][oos_mask].dropna()
    base_ret = results['base_only']['strat_ret'][oos_mask].dropna()
    common_idx = best_overlay_ret.index.intersection(base_ret.index)
    diff = best_overlay_ret.loc[common_idx] - base_ret.loc[common_idx]
    if len(diff) > 30:
        t_stat = diff.mean() / (diff.std() / np.sqrt(len(diff)))
        report_lines.append(f"- OOS daily return difference t-stat: {t_stat:.2f}")
        report_lines.append(f"- OOS period: {len(diff)} days")
        if abs(t_stat) > 1.96:
            report_lines.append(f"- **Statistically significant** at 5% level")
        elif abs(t_stat) > 1.65:
            report_lines.append(f"- **Marginally significant** at 10% level")
        else:
            report_lines.append(f"- **Not statistically significant** (need more OOS data or larger effect)")
    else:
        report_lines.append(f"- Insufficient OOS data for significance test ({len(diff)} days)")

    report_lines.append("")
    report_lines.append(f"### Which layer contributes most?")
    report_lines.append("")

    # Compare positioning-only vs crisis-only contribution
    pos_contrib = results['base_positioning']['oos']['sharpe'] - results['base_only']['oos']['sharpe']
    crisis_contrib = results['base_crisis_only']['oos']['sharpe'] - results['base_only']['oos']['sharpe']
    combo_contrib = results['base_positioning_crisis']['oos']['sharpe'] - results['base_only']['oos']['sharpe']
    macro_marginal = results['base_all']['oos']['sharpe'] - results['base_positioning_crisis']['oos']['sharpe']

    report_lines.append(f"- Positioning layer OOS dSharpe: {pos_contrib:+.3f}")
    report_lines.append(f"- Crisis layer OOS dSharpe: {crisis_contrib:+.3f}")
    report_lines.append(f"- Positioning + Crisis combined OOS dSharpe: {combo_contrib:+.3f}")
    report_lines.append(f"- Macro agreement marginal OOS dSharpe: {macro_marginal:+.3f}")

    if abs(crisis_contrib) > abs(pos_contrib):
        report_lines.append(f"- **Crisis hedge** contributes more (drawdown protection)")
    elif abs(pos_contrib) > abs(crisis_contrib):
        report_lines.append(f"- **Positioning** contributes more (sizing adjustment)")
    else:
        report_lines.append(f"- Both layers contribute similarly")

    report_lines.append("")
    report_lines.append(f"### Recommendation")
    report_lines.append("")

    # Decision logic
    oos_sharpe_best = results[best_overlay_key]['oos']['sharpe']
    oos_sharpe_base = results['base_only']['oos']['sharpe']
    oos_dd_best = results[best_overlay_key]['oos']['max_dd']
    oos_dd_base = results['base_only']['oos']['max_dd']
    dd_improvement = oos_dd_best - oos_dd_base  # positive = less DD

    if oos_sharpe_best > oos_sharpe_base and overlay_improvement > 0.1:
        # Check if improvement is statistically significant
        if len(diff) > 30:
            t_stat_val = diff.mean() / (diff.std() / np.sqrt(len(diff)))
        else:
            t_stat_val = 0
        if t_stat_val > 1.96:
            recommendation = "PROCEED"
            reasoning = f"Overlay significantly improves risk-adjusted returns (OOS Sharpe: {oos_sharpe_base:.2f} -> {oos_sharpe_best:.2f}, t-stat: {t_stat_val:.2f})"
        else:
            recommendation = "NEEDS_TUNING"
            reasoning = f"Overlay improves OOS Sharpe ({oos_sharpe_base:.2f} -> {oos_sharpe_best:.2f}) but improvement is not statistically significant (t={t_stat_val:.2f}). Need more OOS data or parameter refinement."
    elif oos_sharpe_best > oos_sharpe_base or dd_improvement > 0.02:
        recommendation = "NEEDS_TUNING"
        reasoning = f"Overlay shows promise but improvement is modest. Consider parameter optimization."
    else:
        recommendation = "KILL"
        reasoning = f"Overlay does not improve base strategy out-of-sample."

    # Check for IS/OOS degradation (overfitting signal)
    is_sharpe_best = results[best_overlay_key]['is']['sharpe']
    is_sharpe_base = results['base_only']['is']['sharpe']
    is_improvement = is_sharpe_best - is_sharpe_base
    if is_improvement > 0.2 and overlay_improvement < 0.05:
        recommendation = "KILL"
        reasoning += " IS improvement much larger than OOS — likely overfit."

    report_lines.append(f"**{recommendation}**")
    report_lines.append("")
    report_lines.append(f"Reasoning: {reasoning}")
    report_lines.append("")

    # Additional context
    report_lines.append("### Context from R58")
    report_lines.append("")
    report_lines.append("- R58 showed positioning signals as STANDALONE directional system lose money")
    report_lines.append("- This backtest tests them as OVERLAYS on trend-following (the correct architecture)")
    report_lines.append("- Weekly rebalancing (vs daily in R58) reduces turnover cost drag significantly")
    report_lines.append(f"- Base trend-following IS Sharpe: {base_is['sharpe']:.2f}, OOS Sharpe: {base_oos['sharpe']:.2f}")
    report_lines.append(f"- Base IS Return: {base_is['ann_return']:.1%}, OOS Return: {base_oos['ann_return']:.1%}")
    report_lines.append("")

    # ── Equity curves (text) ──
    report_lines.append("## Appendix: Equity Curve Data Points")
    report_lines.append("")
    report_lines.append("Quarterly equity values (normalized to 1.0 at start):")
    report_lines.append("")
    report_lines.append("| Date | Base Only | Base+Pos | Base+Pos+Crisis | Base+All | Base+Crisis |")
    report_lines.append("|------|-----------|----------|-----------------|----------|-------------|")

    for vk in results:
        cum = (1 + results[vk]['strat_ret'].fillna(0)).cumprod()
        results[vk]['_cum'] = cum

    # Quarterly snapshots
    all_dates = btc_daily.index[(btc_daily.index >= IS_START)]
    quarterly = all_dates.to_series().resample('QE').last().dropna()
    for dt in quarterly:
        parts = [f"| {dt.strftime('%Y-%m-%d')}"]
        for vk in ['base_only', 'base_positioning', 'base_positioning_crisis', 'base_all', 'base_crisis_only']:
            c = results[vk]['_cum']
            if dt in c.index:
                parts.append(f"{c.loc[dt]:.2f}")
            else:
                # Find nearest
                idx = c.index.get_indexer([dt], method='ffill')[0]
                if idx >= 0:
                    parts.append(f"{c.iloc[idx]:.2f}")
                else:
                    parts.append("N/A")
        report_lines.append(" | ".join(parts) + " |")

    report_lines.append("")

    # Write report
    report_path = OUTPUT_DIR / 'overlay_backtest_results.md'
    with open(report_path, 'w') as f:
        f.write('\n'.join(report_lines))
    print(f"\nReport saved to: {report_path}")

    # Print summary to console
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"\nBest IS variant:  {best_is_sharpe['name']} (Sharpe={best_is_sharpe['is']['sharpe']:.2f})")
    print(f"Best OOS variant: {best_oos_sharpe['name']} (Sharpe={best_oos_sharpe['oos']['sharpe']:.2f})")
    print(f"Base OOS Sharpe:  {base_oos['sharpe']:.2f}")
    print(f"Recommendation:   {recommendation}")
    print(f"Reasoning:        {reasoning}")


if __name__ == '__main__':
    main()
