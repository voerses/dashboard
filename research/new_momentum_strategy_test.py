#!/usr/bin/env python3
"""
New Momentum Strategy: EMA Crossover + Overlays (No Hard Stops)
================================================================

Context:
  Previous momentum strategies (s56, s44) are DEAD after a backtest bug fix.
  The bug: stops were executing at close price instead of current/actual price.
  Fix means: stop losses are hit at WORSE prices (mid-candle vs end-of-candle).
  s37 (momentum trail) barely survives with PF 1.30.

  This strategy removes hard stop losses entirely and relies on:
  1. EMA crossover (20/50) for entry/exit — faster than 50/200 SMA
  2. Positioning overlay from Binance metrics for sizing
  3. VRP overlay from Deribit DVOL for volatility-adjusted sizing
  4. ADX filter for trend quality confirmation

Design Principles:
  - NO hard stop losses — exit on signal reversal (EMA cross) only
  - Wider, slower exits that aren't affected by the stop-price bug fix
  - Position sizing from validated overlays
  - Weekly rebalancing to reduce turnover

Variants:
  1. EMA crossover only (no overlays, no stops)
  2. EMA + positioning overlay
  3. EMA + positioning + VRP overlay
  4. EMA + ADX filter + positioning + VRP

Temporal split:
  - IS: 2020-09-01 to 2024-12-31
  - OOS: 2025-01-01 to latest

Backtest rules:
  - Weekly rebalancing (Monday)
  - 10 bps round-trip cost on position changes
  - Position range: 0 to 1.5x (long only, never short)
  - NO hard stop losses
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

# ── Temporal split ──────────────────────────────────────────────────────────
IS_START = '2020-09-01'
IS_END = '2024-12-31'
OOS_START = '2025-01-01'
COST_BPS = 10  # round-trip cost in basis points


# ── 1. Data Loading ─────────────────────────────────────────────────────────

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


def load_positioning():
    """Load Binance positioning data for BTCUSDT."""
    print("[2/5] Loading positioning data...")
    pos = pd.read_parquet(DATA_DIR / 'alternative/binance_metrics/all_symbols_daily_ls.parquet')
    pos = pos[pos['symbol'] == 'BTCUSDT'].copy()
    pos['date'] = pd.to_datetime(pos['date'])
    pos = pos.set_index('date').sort_index()
    pos = pos[['sum_toptrader_ls_ratio', 'count_toptrader_ls_ratio', 'count_ls_ratio']].copy()
    pos = pos[~pos.index.duplicated(keep='last')]
    print(f"  Positioning: {pos.index.min().date()} to {pos.index.max().date()}, {len(pos)} rows")
    return pos


def load_dvol():
    """Load BTC DVOL from Deribit JSON (OHLC daily candles)."""
    print("[3/5] Loading BTC DVOL (Deribit implied volatility index)...")
    dvol_path = DATA_DIR / 'alternative/deribit_options/dvol/btc_dvol_daily.json'
    if not dvol_path.exists():
        print("  WARNING: DVOL file not found. VRP overlay will use proxy.")
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
    return dvol['dvol_close']


# ── 2. Signal Construction ──────────────────────────────────────────────────

def build_ema_crossover_signal(btc_daily, fast_period=20, slow_period=50):
    """
    EMA crossover signal: long when fast EMA > slow EMA, flat otherwise.
    Faster than 50/200 SMA for quicker trend detection.
    NO hysteresis — pure crossover for simplicity.
    """
    print(f"[4/5] Building EMA crossover signal ({fast_period}/{slow_period})...")
    ema_fast = btc_daily['close'].ewm(span=fast_period, adjust=False).mean()
    ema_slow = btc_daily['close'].ewm(span=slow_period, adjust=False).mean()

    # Long when fast EMA > slow EMA, flat otherwise
    position = (ema_fast > ema_slow).astype(float)

    # Need enough warmup for EMA to be meaningful
    warmup = slow_period * 2
    position.iloc[:warmup] = 0.0

    n_long = position.sum()
    pct_long = 100 * position.mean()
    print(f"  EMA signal: {n_long:.0f} days long out of {len(position)} ({pct_long:.1f}%)")

    # Count trades (signal changes)
    trades = (position.diff().abs() > 0).sum()
    print(f"  Signal changes: {trades} (avg holding ~{len(position)/max(trades,1):.0f} days)")

    return position, ema_fast, ema_slow


def build_adx_filter(btc_daily, period=14, threshold=20):
    """
    ADX (Average Directional Index) filter for trend quality.
    Only allows entries when ADX > threshold (strong trend).

    ADX measures trend strength regardless of direction.
    High ADX = strong trend (good for momentum), Low ADX = choppy (avoid).
    """
    print(f"  Building ADX filter (period={period}, threshold={threshold})...")

    high = btc_daily['high']
    low = btc_daily['low']
    close = btc_daily['close']

    # True Range
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    # Directional Movement
    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm[plus_dm < 0] = 0
    minus_dm[minus_dm < 0] = 0
    # When both are positive, keep only the larger one
    mask = plus_dm > minus_dm
    minus_dm[mask & (plus_dm > 0)] = 0
    plus_dm[~mask & (minus_dm > 0)] = 0

    # Smoothed (Wilder's smoothing = EMA with alpha=1/period)
    atr = tr.ewm(alpha=1.0/period, adjust=False).mean()
    plus_di = 100 * (plus_dm.ewm(alpha=1.0/period, adjust=False).mean() / atr)
    minus_di = 100 * (minus_dm.ewm(alpha=1.0/period, adjust=False).mean() / atr)

    # ADX
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = dx.ewm(alpha=1.0/period, adjust=False).mean()

    # Filter: 1.0 when ADX > threshold, 0.0 otherwise
    adx_filter = (adx > threshold).astype(float)

    # Warmup
    adx_filter.iloc[:period * 3] = 1.0  # Don't filter during warmup

    n_active = adx_filter.sum()
    pct_active = 100 * adx_filter.mean()
    print(f"  ADX filter: active {n_active:.0f} days ({pct_active:.1f}%), avg ADX={adx.dropna().mean():.1f}")

    return adx_filter, adx


def build_positioning_signal(btc_daily, positioning):
    """
    Positioning overlay (contrarian sizing based on crowd positioning).
    - Top Trader L/S: sum_toptrader_ls_ratio, 30d rolling z-score
    - L/S Divergence: (count_toptrader_ls_ratio - count_ls_ratio), 30d rolling z-score
    - Combined z -> sizing multiplier
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
        iv_proxy = log_ret.rolling(90, min_periods=60).std() * np.sqrt(365) * 100
        iv_proxy = iv_proxy * 1.2
        iv = iv_proxy
        vrp_source = "proxy (90d RV x 1.2)"
    else:
        iv = dvol_series.reindex(btc_daily.index).ffill()
        vrp_source = "Deribit DVOL"

    # VRP = IV - RV (high VRP = vol overpriced = calm expected)
    vrp = iv - rv_20d

    # 60d rolling z-score of VRP
    vrp_mu = vrp.rolling(60, min_periods=30).mean()
    vrp_sigma = vrp.rolling(60, min_periods=30).std()
    vrp_z = (vrp - vrp_mu) / vrp_sigma.replace(0, np.nan)

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
    print(f"  VRP multiplier distribution:")
    for val in [0.3, 0.5, 1.0, 1.3]:
        pct = (vrp_multiplier == val).mean() * 100
        print(f"    {val}x: {pct:.1f}%")

    return vrp_multiplier, vrp_z


# ── 3. Backtest Engine ──────────────────────────────────────────────────────

def compute_final_position(base_pos, pos_mult, vrp_mult, adx_filt, variant):
    """Compute final position based on variant."""
    if variant == 'ema_only':
        final = base_pos.copy()
    elif variant == 'ema_positioning':
        final = base_pos * pos_mult
    elif variant == 'ema_pos_vrp':
        final = base_pos * pos_mult * vrp_mult
    elif variant == 'ema_adx_pos_vrp':
        final = base_pos * adx_filt * pos_mult * vrp_mult
    else:
        raise ValueError(f"Unknown variant: {variant}")
    # Clip to [0, 1.5] -- never short, max 1.5x
    final = final.clip(0, 1.5)
    return final


def run_backtest(btc_daily, final_position, cost_bps=COST_BPS):
    """
    Run backtest with weekly rebalancing and transaction costs.
    Returns daily returns series, held positions, and costs.
    """
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

    # Strategy return: position * daily return - costs
    strat_ret = held_position.shift(1) * daily_ret - costs

    return strat_ret, held_position, costs


def compute_metrics(returns, label=""):
    """Compute performance metrics from a daily returns series."""
    returns = returns.dropna()
    if len(returns) == 0:
        return {'label': label, 'total_return': 0, 'ann_return': 0,
                'ann_vol': 0, 'sharpe': 0, 'max_dd': 0, 'calmar': 0,
                'n_days': 0, 'pf': 0, 'n_trades': 0}

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
    pf = gains / losses if losses > 0 else float('inf')

    return {
        'label': label,
        'total_return': total_ret,
        'ann_return': ann_ret,
        'ann_vol': ann_vol,
        'sharpe': sharpe,
        'max_dd': max_dd,
        'calmar': calmar,
        'n_days': len(returns),
        'pf': pf,
    }


def compute_turnover(held_position):
    """Compute annualized turnover."""
    daily_changes = held_position.diff().abs()
    total_turnover = daily_changes.sum()
    n_years = len(held_position) / 365
    return total_turnover / max(n_years, 0.01)


def count_trades(held_position):
    """Count number of position changes (trades)."""
    changes = held_position.diff().abs()
    return (changes > 0.01).sum()


# ── 4. Comparison with Existing Strategies ──────────────────────────────────

def compare_with_existing(oos_ret, btc_daily):
    """
    Compare new strategy OOS returns with known surviving strategies.
    s29: +5.6% OOS return, s37: +3.87% OOS return (both post-bug-fix).
    Also compare with buy-and-hold BTC.
    """
    oos_mask = btc_daily.index >= OOS_START
    btc_oos = btc_daily['close'][oos_mask]
    bnh_ret = (btc_oos.iloc[-1] / btc_oos.iloc[0]) - 1

    return {
        'btc_bnh_oos': bnh_ret,
        's29_oos': 0.056,  # Known surviving strategy
        's37_oos': 0.0387,  # Known surviving strategy (PF 1.30)
    }


# ── 5. Main Execution ──────────────────────────────────────────────────────

def main():
    print("=" * 74)
    print("NEW MOMENTUM STRATEGY: EMA Crossover + Overlays (No Hard Stops)")
    print("Post-bug-fix design: avoids stop-price execution issues entirely")
    print("=" * 74)
    print()

    # Load data
    btc_daily = load_btc_daily()
    positioning = load_positioning()
    dvol = load_dvol()
    print()

    # Build signals
    base_position, ema_fast, ema_slow = build_ema_crossover_signal(btc_daily)
    adx_filter, adx_series = build_adx_filter(btc_daily)
    pos_multiplier, pos_combined_z = build_positioning_signal(btc_daily, positioning)
    vrp_multiplier, vrp_z = build_vrp_signal(btc_daily, dvol)
    print()

    # Define variants
    variants = {
        'ema_only':        'V1: EMA Only (no overlays)',
        'ema_positioning': 'V2: EMA + Positioning',
        'ema_pos_vrp':     'V3: EMA + Pos + VRP',
        'ema_adx_pos_vrp': 'V4: EMA + ADX + Pos + VRP',
    }

    # Run backtests
    print("Running backtests...")
    results = {}
    for variant_key, variant_name in variants.items():
        final_pos = compute_final_position(
            base_position, pos_multiplier, vrp_multiplier, adx_filter, variant_key
        )
        strat_ret, held_pos, costs = run_backtest(btc_daily, final_pos)

        is_mask = (btc_daily.index >= IS_START) & (btc_daily.index <= IS_END)
        oos_mask = btc_daily.index >= OOS_START

        is_ret = strat_ret[is_mask]
        oos_ret = strat_ret[oos_mask]

        is_metrics = compute_metrics(is_ret, f"{variant_name} (IS)")
        oos_metrics = compute_metrics(oos_ret, f"{variant_name} (OOS)")

        is_turnover = compute_turnover(held_pos[is_mask])
        oos_turnover = compute_turnover(held_pos[oos_mask])
        n_trades_is = count_trades(held_pos[is_mask])
        n_trades_oos = count_trades(held_pos[oos_mask])

        results[variant_key] = {
            'name': variant_name,
            'is': is_metrics,
            'oos': oos_metrics,
            'is_turnover': is_turnover,
            'oos_turnover': oos_turnover,
            'n_trades_is': n_trades_is,
            'n_trades_oos': n_trades_oos,
            'strat_ret': strat_ret,
            'held_pos': held_pos,
            'costs': costs,
            'final_pos': final_pos,
        }
        print(f"  {variant_name}: IS Sharpe={is_metrics['sharpe']:.2f}, OOS Sharpe={oos_metrics['sharpe']:.2f}, "
              f"IS Ret={is_metrics['ann_return']:.1%}, OOS Ret={oos_metrics['ann_return']:.1%}")
    print()

    # ── Also run the old 50/200 SMA base for comparison ──
    print("Running 50/200 SMA baseline for comparison...")
    sma50 = btc_daily['close'].rolling(50, min_periods=50).mean()
    sma200 = btc_daily['close'].rolling(200, min_periods=200).mean()
    sma_position = pd.Series(0.0, index=btc_daily.index)
    in_position = False
    for i in range(len(btc_daily)):
        close = btc_daily['close'].iloc[i]
        s50 = sma50.iloc[i]
        s200 = sma200.iloc[i]
        if pd.isna(s50) or pd.isna(s200):
            sma_position.iloc[i] = 0.0
            continue
        if not in_position:
            if close > s50 and close > s200:
                in_position = True
                sma_position.iloc[i] = 1.0
            else:
                sma_position.iloc[i] = 0.0
        else:
            if close < s50:
                in_position = False
                sma_position.iloc[i] = 0.0
            else:
                sma_position.iloc[i] = 1.0

    sma_ret, sma_held, sma_costs = run_backtest(btc_daily, sma_position)
    is_mask = (btc_daily.index >= IS_START) & (btc_daily.index <= IS_END)
    oos_mask = btc_daily.index >= OOS_START
    sma_is_metrics = compute_metrics(sma_ret[is_mask], "50/200 SMA (IS)")
    sma_oos_metrics = compute_metrics(sma_ret[oos_mask], "50/200 SMA (OOS)")
    print(f"  50/200 SMA: IS Sharpe={sma_is_metrics['sharpe']:.2f}, OOS Sharpe={sma_oos_metrics['sharpe']:.2f}")
    print()

    # ── Buy and hold comparison ──
    btc_ret = btc_daily['close'].pct_change()
    bnh_is_metrics = compute_metrics(btc_ret[is_mask], "BTC Buy-Hold (IS)")
    bnh_oos_metrics = compute_metrics(btc_ret[oos_mask], "BTC Buy-Hold (OOS)")
    print(f"  BTC Buy-Hold: IS Sharpe={bnh_is_metrics['sharpe']:.2f}, OOS Sharpe={bnh_oos_metrics['sharpe']:.2f}")

    # ── Statistical Significance ──
    print("\n--- Statistical Significance (OOS) ---")
    base_oos_ret = results['ema_only']['strat_ret'][oos_mask].dropna()
    for vk in ['ema_positioning', 'ema_pos_vrp', 'ema_adx_pos_vrp']:
        overlay_ret = results[vk]['strat_ret'][oos_mask].dropna()
        common = overlay_ret.index.intersection(base_oos_ret.index)
        diff = overlay_ret.loc[common] - base_oos_ret.loc[common]
        if len(diff) > 30:
            t_stat = diff.mean() / (diff.std() / np.sqrt(len(diff)))
            sig = 'p<0.05' if abs(t_stat) > 1.96 else 'p<0.10' if abs(t_stat) > 1.65 else 'n.s.'
            print(f"  {results[vk]['name']} vs EMA Only: t={t_stat:.3f} ({sig})")

    # ── Monthly OOS Returns for all variants ──
    monthly_all = {}
    for vk in variants:
        vr = results[vk]['strat_ret'][oos_mask]
        monthly_all[vk] = (1 + vr).resample('ME').prod() - 1
    monthly_all['sma_base'] = (1 + sma_ret[oos_mask]).resample('ME').prod() - 1
    monthly_all['bnh'] = (1 + btc_ret[oos_mask]).resample('ME').prod() - 1

    # ── Recent OOS performance (last 6 months: 2025-09 to 2026-03) ──
    recent_start = '2025-09-01'
    recent_mask = btc_daily.index >= recent_start
    print(f"\n--- Recent Performance ({recent_start} to latest) ---")
    for vk in variants:
        recent_ret = results[vk]['strat_ret'][recent_mask]
        recent_m = compute_metrics(recent_ret, f"{results[vk]['name']} (Recent)")
        print(f"  {results[vk]['name']}: Return={recent_m['total_return']:.2%}, "
              f"Sharpe={recent_m['sharpe']:.2f}, MaxDD={recent_m['max_dd']:.2%}")
    recent_bnh = compute_metrics(btc_ret[recent_mask], "BTC BnH (Recent)")
    print(f"  BTC Buy-Hold: Return={recent_bnh['total_return']:.2%}, "
          f"Sharpe={recent_bnh['sharpe']:.2f}, MaxDD={recent_bnh['max_dd']:.2%}")

    # ── Drawdown Analysis ──
    print("\n--- Drawdown Analysis (OOS) ---")
    for vk in variants:
        oos_ret_series = results[vk]['strat_ret'][oos_mask]
        cum = (1 + oos_ret_series.fillna(0)).cumprod()
        peak = cum.cummax()
        dd = (cum - peak) / peak
        max_dd_date = dd.idxmin()
        # Find drawdown start (last peak before max DD)
        dd_at_max = dd.loc[max_dd_date]
        peak_before = peak.loc[:max_dd_date]
        if len(peak_before) > 0:
            dd_start = cum.loc[:max_dd_date][cum.loc[:max_dd_date] == peak.loc[:max_dd_date]].index[-1]
            dd_duration = (max_dd_date - dd_start).days
            print(f"  {results[vk]['name']}: MaxDD={dd_at_max:.2%} "
                  f"({dd_start.strftime('%Y-%m-%d')} to {max_dd_date.strftime('%Y-%m-%d')}, {dd_duration}d)")

    # ── Generate Report ──
    print()
    print("=" * 74)
    print("GENERATING REPORT")
    print("=" * 74)

    lines = []
    lines.append("# New Momentum Strategy Results: EMA Crossover + Overlays (No Hard Stops)")
    lines.append("")
    lines.append(f"**Run date**: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"**IS period**: {IS_START} to {IS_END}")
    lines.append(f"**OOS period**: {OOS_START} to latest")
    lines.append(f"**Rebalancing**: Weekly (Monday)")
    lines.append(f"**Transaction cost**: {COST_BPS} bps round-trip")
    lines.append(f"**Position range**: 0 to 1.5x (long only)")
    lines.append(f"**Stop losses**: NONE (exit on signal reversal only)")
    lines.append("")

    lines.append("## Context")
    lines.append("")
    lines.append("Previous momentum strategies (s56, s44) are DEAD after a backtest bug fix.")
    lines.append("The bug: stops were executing at close price instead of current/actual price.")
    lines.append("This means stop losses are hit at worse prices (mid-candle vs end-of-candle),")
    lines.append("increasing realized losses. s37 (momentum trail) barely survives with PF 1.30.")
    lines.append("")
    lines.append("**Design response**: Remove hard stops entirely. Use EMA crossover for exits.")
    lines.append("Wider, slower exits that aren't affected by the stop-price bug fix at all.")
    lines.append("")

    lines.append("## Architecture")
    lines.append("")
    lines.append("- **Base signal**: 20/50 EMA crossover (faster than 50/200 SMA)")
    lines.append("  - Long when 20d EMA > 50d EMA, flat otherwise")
    lines.append("  - NO hard stop losses -- exit on EMA cross-under only")
    lines.append("- **Positioning overlay**: Top Trader L/S + L/S Divergence z-scores -> 0.3x to 1.5x")
    lines.append("- **VRP overlay**: VRP z-score -> 0.3x to 1.3x")
    lines.append("- **ADX filter**: ADX > 20 required for entry (trend quality gate)")
    lines.append("")

    # ── 1. Variant Performance Table ──
    lines.append("## 1. Variant Performance")
    lines.append("")
    lines.append("| Variant | IS Return | OOS Return | IS Sharpe | OOS Sharpe | IS MaxDD | OOS MaxDD | IS PF | OOS PF | Trades/yr |")
    lines.append("|---------|-----------|------------|-----------|------------|----------|-----------|-------|--------|-----------|")

    for vk in ['ema_only', 'ema_positioning', 'ema_pos_vrp', 'ema_adx_pos_vrp']:
        r = results[vk]
        is_m = r['is']
        oos_m = r['oos']
        is_years = max(is_m['n_days'] / 365, 0.01)
        oos_years = max(oos_m['n_days'] / 365, 0.01)
        trades_yr = r['n_trades_oos'] / oos_years
        line = (f"| {r['name']} | {is_m['ann_return']:.1%} | {oos_m['ann_return']:.1%} | "
                f"{is_m['sharpe']:.2f} | {oos_m['sharpe']:.2f} | "
                f"{is_m['max_dd']:.1%} | {oos_m['max_dd']:.1%} | "
                f"{is_m['pf']:.2f} | {oos_m['pf']:.2f} | {trades_yr:.0f} |")
        lines.append(line)

    # Add SMA baseline and BnH
    lines.append(f"| 50/200 SMA (baseline) | {sma_is_metrics['ann_return']:.1%} | {sma_oos_metrics['ann_return']:.1%} | "
                 f"{sma_is_metrics['sharpe']:.2f} | {sma_oos_metrics['sharpe']:.2f} | "
                 f"{sma_is_metrics['max_dd']:.1%} | {sma_oos_metrics['max_dd']:.1%} | "
                 f"{sma_is_metrics['pf']:.2f} | {sma_oos_metrics['pf']:.2f} | - |")
    lines.append(f"| BTC Buy-Hold | {bnh_is_metrics['ann_return']:.1%} | {bnh_oos_metrics['ann_return']:.1%} | "
                 f"{bnh_is_metrics['sharpe']:.2f} | {bnh_oos_metrics['sharpe']:.2f} | "
                 f"{bnh_is_metrics['max_dd']:.1%} | {bnh_oos_metrics['max_dd']:.1%} | "
                 f"{bnh_is_metrics['pf']:.2f} | {bnh_oos_metrics['pf']:.2f} | - |")
    lines.append("")

    # ── 2. Comparison with Surviving Strategies ──
    lines.append("## 2. Comparison with Surviving Strategies")
    lines.append("")
    lines.append("Known post-bug-fix OOS performance:")
    lines.append("- s29: +5.6% OOS return")
    lines.append("- s37: +3.87% OOS return (PF 1.30)")
    lines.append("")
    lines.append("| Strategy | OOS Return | OOS Sharpe | OOS MaxDD | Status |")
    lines.append("|----------|------------|------------|-----------|--------|")
    lines.append(f"| s29 (reference) | +5.6% | - | - | Surviving |")
    lines.append(f"| s37 (reference) | +3.87% | - | - | Barely surviving |")
    for vk in ['ema_only', 'ema_positioning', 'ema_pos_vrp', 'ema_adx_pos_vrp']:
        r = results[vk]
        oos_m = r['oos']
        oos_ret_pct = f"{oos_m['total_return']:+.2%}"
        status = "BETTER" if oos_m['total_return'] > 0.056 else ("COMPETITIVE" if oos_m['total_return'] > 0.035 else "WORSE")
        lines.append(f"| {r['name']} | {oos_ret_pct} | {oos_m['sharpe']:.2f} | {oos_m['max_dd']:.1%} | {status} |")
    lines.append("")

    # ── 3. Does Removing Hard Stops Improve Performance? ──
    lines.append("## 3. Does Removing Hard Stops Improve Performance?")
    lines.append("")
    lines.append("**Key question**: The bug fix makes hard stops execute at worse prices.")
    lines.append("Does removing them and relying on EMA crossover exits produce better results?")
    lines.append("")

    # Compare EMA-only with SMA baseline (both are no-stop trend followers)
    ema_vs_sma_is = results['ema_only']['is']['sharpe'] - sma_is_metrics['sharpe']
    ema_vs_sma_oos = results['ema_only']['oos']['sharpe'] - sma_oos_metrics['sharpe']
    lines.append(f"### EMA (20/50) vs SMA (50/200) -- both without stops")
    lines.append(f"- IS Sharpe delta: {ema_vs_sma_is:+.3f}")
    lines.append(f"- OOS Sharpe delta: {ema_vs_sma_oos:+.3f}")
    lines.append(f"- IS Return delta: {results['ema_only']['is']['ann_return'] - sma_is_metrics['ann_return']:+.1%}")
    lines.append(f"- OOS Return delta: {results['ema_only']['oos']['ann_return'] - sma_oos_metrics['ann_return']:+.1%}")
    lines.append("")

    if ema_vs_sma_oos > 0:
        lines.append("The faster EMA crossover outperforms the slower SMA in the OOS period.")
    else:
        lines.append("The slower SMA crossover is more robust than the faster EMA out-of-sample.")
    lines.append("")

    lines.append("**Answer**: The no-stop approach avoids the bug fix issue entirely.")
    lines.append("Performance depends on the trend-following signal quality, not stop execution.")
    lines.append("This is by design -- EMA crossover exits are computed at close price (not affected).")
    lines.append("")

    # ── 4. Monthly OOS Returns ──
    lines.append("## 4. Monthly Returns (OOS)")
    lines.append("")
    lines.append("| Month | V1 EMA | V2 EMA+Pos | V3 EMA+Pos+VRP | V4 Full | SMA Base | BTC BnH |")
    lines.append("|-------|--------|------------|----------------|---------|----------|---------|")

    all_months = sorted(set(monthly_all['ema_only'].index))
    for dt in all_months:
        parts = [f"| {dt.strftime('%Y-%m')}"]
        for vk in ['ema_only', 'ema_positioning', 'ema_pos_vrp', 'ema_adx_pos_vrp', 'sma_base', 'bnh']:
            if dt in monthly_all[vk].index:
                parts.append(f"{monthly_all[vk].loc[dt]:.2%}")
            else:
                parts.append("N/A")
        lines.append(" | ".join(parts) + " |")

    # Cumulative row
    parts = ["| **Cumulative**"]
    for vk in ['ema_only', 'ema_positioning', 'ema_pos_vrp', 'ema_adx_pos_vrp']:
        cum = (1 + results[vk]['strat_ret'][oos_mask].dropna()).cumprod().iloc[-1] - 1
        parts.append(f"**{cum:.2%}**")
    cum_sma = (1 + sma_ret[oos_mask].dropna()).cumprod().iloc[-1] - 1
    cum_bnh = (1 + btc_ret[oos_mask].dropna()).cumprod().iloc[-1] - 1
    parts.append(f"**{cum_sma:.2%}**")
    parts.append(f"**{cum_bnh:.2%}**")
    lines.append(" | ".join(parts) + " |")
    lines.append("")

    # ── 5. Overlay Contribution Analysis ──
    lines.append("## 5. Overlay Contribution Analysis")
    lines.append("")
    lines.append("Marginal improvement of each overlay vs EMA-only base:")
    lines.append("")
    lines.append("| Overlay Added | IS dSharpe | OOS dSharpe | IS dReturn | OOS dReturn | IS dMaxDD | OOS dMaxDD |")
    lines.append("|---------------|------------|-------------|------------|-------------|-----------|------------|")

    base_is_m = results['ema_only']['is']
    base_oos_m = results['ema_only']['oos']

    overlays = [
        ('+ Positioning', 'ema_positioning'),
        ('+ Positioning + VRP', 'ema_pos_vrp'),
        ('+ ADX + Pos + VRP (full)', 'ema_adx_pos_vrp'),
    ]

    for label, vk in overlays:
        curr_is = results[vk]['is']
        curr_oos = results[vk]['oos']
        d_s_is = curr_is['sharpe'] - base_is_m['sharpe']
        d_s_oos = curr_oos['sharpe'] - base_oos_m['sharpe']
        d_r_is = curr_is['ann_return'] - base_is_m['ann_return']
        d_r_oos = curr_oos['ann_return'] - base_oos_m['ann_return']
        d_dd_is = curr_is['max_dd'] - base_is_m['max_dd']
        d_dd_oos = curr_oos['max_dd'] - base_oos_m['max_dd']
        line = (f"| {label} | {d_s_is:+.3f} | {d_s_oos:+.3f} | "
                f"{d_r_is:+.1%} | {d_r_oos:+.1%} | {d_dd_is:+.1%} | {d_dd_oos:+.1%} |")
        lines.append(line)

    lines.append("")
    lines.append("Positive dMaxDD = less drawdown (improvement).")
    lines.append("")

    # Sequential marginal
    lines.append("### Sequential marginal contribution:")
    lines.append("")
    lines.append("| Step | IS dSharpe | OOS dSharpe |")
    lines.append("|------|------------|-------------|")

    seq = [('ema_only', 'EMA Only'), ('ema_positioning', '+ Positioning'),
           ('ema_pos_vrp', '+ VRP'), ('ema_adx_pos_vrp', '+ ADX')]
    for i in range(1, len(seq)):
        prev_k, prev_n = seq[i-1]
        curr_k, curr_n = seq[i]
        d_is = results[curr_k]['is']['sharpe'] - results[prev_k]['is']['sharpe']
        d_oos = results[curr_k]['oos']['sharpe'] - results[prev_k]['oos']['sharpe']
        lines.append(f"| {curr_n} | {d_is:+.3f} | {d_oos:+.3f} |")

    lines.append("")

    # ── 6. Recent Performance (Last 6 Months) ──
    lines.append("## 6. Recent Performance (2025-09 to 2026-03)")
    lines.append("")
    lines.append("Focus on the most recent period to check for regime sensitivity.")
    lines.append("")
    lines.append("| Variant | Return | Sharpe | MaxDD |")
    lines.append("|---------|--------|--------|-------|")

    for vk in ['ema_only', 'ema_positioning', 'ema_pos_vrp', 'ema_adx_pos_vrp']:
        recent_ret = results[vk]['strat_ret'][recent_mask]
        recent_m = compute_metrics(recent_ret)
        lines.append(f"| {results[vk]['name']} | {recent_m['total_return']:.2%} | "
                     f"{recent_m['sharpe']:.2f} | {recent_m['max_dd']:.2%} |")

    recent_bnh_ret = btc_ret[recent_mask]
    recent_bnh_m = compute_metrics(recent_bnh_ret)
    lines.append(f"| BTC Buy-Hold | {recent_bnh_m['total_return']:.2%} | "
                 f"{recent_bnh_m['sharpe']:.2f} | {recent_bnh_m['max_dd']:.2%} |")
    lines.append("")

    # ── 7. Position Distribution ──
    lines.append("## 7. Position Distribution (% of time at each level)")
    lines.append("")
    pos_bins = [-0.01, 0.01, 0.35, 0.55, 0.85, 1.15, 1.40, 2.0]
    pos_labels = ['0.0', '~0.3', '~0.5', '~0.65', '~1.0', '~1.3', '~1.5']
    lines.append("| Variant | 0.0 (Flat) | ~0.3 | ~0.5 | ~0.65 | ~1.0 | ~1.3 | ~1.5 |")
    lines.append("|---------|------------|------|------|-------|------|------|------|")

    for vk in variants:
        hp = results[vk]['held_pos'][(btc_daily.index >= IS_START)]
        hp_binned = pd.cut(hp, bins=pos_bins, labels=pos_labels, include_lowest=True)
        dist = hp_binned.value_counts(normalize=True).reindex(pos_labels, fill_value=0)
        parts = [f"| {results[vk]['name']}"]
        for lbl in pos_labels:
            parts.append(f"{dist[lbl]:.1%}")
        lines.append(" | ".join(parts) + " |")

    lines.append("")

    # ── 8. Verdict ──
    lines.append("## 8. Verdict")
    lines.append("")

    # Find best variant
    best_oos_key = max(variants.keys(), key=lambda k: results[k]['oos']['sharpe'])
    best_oos_m = results[best_oos_key]['oos']
    best_name = results[best_oos_key]['name']

    # Compare with surviving strategies
    beats_s29 = best_oos_m['total_return'] > 0.056
    beats_s37 = best_oos_m['total_return'] > 0.0387
    beats_bnh = best_oos_m['sharpe'] > bnh_oos_metrics['sharpe']
    positive_oos = best_oos_m['total_return'] > 0

    lines.append(f"### Best variant: {best_name}")
    lines.append(f"- OOS Return: {best_oos_m['total_return']:.2%}")
    lines.append(f"- OOS Sharpe: {best_oos_m['sharpe']:.2f}")
    lines.append(f"- OOS MaxDD: {best_oos_m['max_dd']:.1%}")
    lines.append(f"- OOS Profit Factor: {best_oos_m['pf']:.2f}")
    lines.append("")

    lines.append("### Comparison checklist:")
    lines.append(f"- [{'x' if beats_s29 else ' '}] Beats s29 OOS return (+5.6%)")
    lines.append(f"- [{'x' if beats_s37 else ' '}] Beats s37 OOS return (+3.87%)")
    lines.append(f"- [{'x' if beats_bnh else ' '}] Better risk-adjusted than BTC buy-hold")
    lines.append(f"- [{'x' if positive_oos else ' '}] Positive OOS return")
    lines.append(f"- [{'x' if best_oos_m['sharpe'] > 0.5 else ' '}] OOS Sharpe > 0.5")
    lines.append(f"- [{'x' if best_oos_m['pf'] > 1.3 else ' '}] OOS Profit Factor > 1.3")
    lines.append("")

    # Decision
    score = sum([beats_s29, beats_s37, beats_bnh, positive_oos,
                 best_oos_m['sharpe'] > 0.5, best_oos_m['pf'] > 1.3])

    if score >= 5:
        recommendation = "WORTH PURSUING"
        reasoning = (
            f"Strategy passes {score}/6 criteria. {best_name} shows strong OOS performance "
            f"with no dependence on stop-loss execution. The EMA crossover approach is immune "
            f"to the stop-price bug fix."
        )
    elif score >= 3:
        recommendation = "WORTH PURSUING (with caveats)"
        reasoning = (
            f"Strategy passes {score}/6 criteria. Shows promise but needs refinement. "
            f"The no-stop design is sound but overlay contribution may be marginal."
        )
    elif score >= 2 and positive_oos:
        recommendation = "MARGINAL -- needs more development"
        reasoning = (
            f"Strategy passes {score}/6 criteria. Positive OOS but not yet competitive "
            f"with surviving strategies. Consider parameter optimization or additional filters."
        )
    else:
        recommendation = "KILL"
        reasoning = (
            f"Strategy passes only {score}/6 criteria. Not competitive with surviving strategies "
            f"despite the no-stop design advantage."
        )

    # Check IS/OOS degradation
    best_is_sharpe = results[best_oos_key]['is']['sharpe']
    if best_is_sharpe > best_oos_m['sharpe'] * 2:
        reasoning += (f" WARNING: IS Sharpe ({best_is_sharpe:.2f}) is much higher than "
                      f"OOS ({best_oos_m['sharpe']:.2f}) -- possible overfitting concern.")

    lines.append(f"### Recommendation: **{recommendation}**")
    lines.append("")
    lines.append(f"{reasoning}")
    lines.append("")

    lines.append("### Key insights:")
    lines.append("")
    lines.append("1. **No hard stops**: EMA crossover exits are computed on daily close -- they are")
    lines.append("   immune to the stop-price execution bug that killed s56 and s44.")
    lines.append("2. **EMA vs SMA**: The 20/50 EMA is faster than 50/200 SMA, catching trend")
    lines.append("   reversals sooner but potentially generating more whipsaws.")

    # Check which overlay helps most
    pos_contrib_oos = results['ema_positioning']['oos']['sharpe'] - results['ema_only']['oos']['sharpe']
    vrp_marginal_oos = results['ema_pos_vrp']['oos']['sharpe'] - results['ema_positioning']['oos']['sharpe']
    adx_marginal_oos = results['ema_adx_pos_vrp']['oos']['sharpe'] - results['ema_pos_vrp']['oos']['sharpe']

    lines.append(f"3. **Positioning overlay** OOS dSharpe: {pos_contrib_oos:+.3f}")
    lines.append(f"4. **VRP overlay** marginal OOS dSharpe: {vrp_marginal_oos:+.3f}")
    lines.append(f"5. **ADX filter** marginal OOS dSharpe: {adx_marginal_oos:+.3f}")
    lines.append("")

    # ── Appendix: Equity Curve ──
    lines.append("## Appendix: Quarterly Equity Curve (normalized to 1.0)")
    lines.append("")
    lines.append("| Date | V1 EMA | V2 EMA+Pos | V3 EMA+Pos+VRP | V4 Full | 50/200 SMA | BTC BnH |")
    lines.append("|------|--------|------------|----------------|---------|------------|---------|")

    cum_dict = {}
    for vk in variants:
        cum_dict[vk] = (1 + results[vk]['strat_ret'].fillna(0)).cumprod()
    cum_dict['sma'] = (1 + sma_ret.fillna(0)).cumprod()
    cum_dict['bnh'] = (1 + btc_ret.fillna(0)).cumprod()

    all_dates = btc_daily.index[(btc_daily.index >= IS_START)]
    quarterly = all_dates.to_series().resample('QE').last().dropna()
    for dt in quarterly:
        parts = [f"| {dt.strftime('%Y-%m-%d')}"]
        for vk_key in ['ema_only', 'ema_positioning', 'ema_pos_vrp', 'ema_adx_pos_vrp', 'sma', 'bnh']:
            c = cum_dict[vk_key]
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
    report_path = OUTPUT_DIR / 'new_momentum_strategy_results.md'
    with open(report_path, 'w') as f:
        f.write('\n'.join(lines))
    print(f"\nReport saved to: {report_path}")

    # Console summary
    print("\n" + "=" * 74)
    print("SUMMARY")
    print("=" * 74)
    print(f"\nBest OOS variant: {best_name}")
    print(f"  OOS Return:       {best_oos_m['total_return']:.2%}")
    print(f"  OOS Sharpe:       {best_oos_m['sharpe']:.2f}")
    print(f"  OOS MaxDD:        {best_oos_m['max_dd']:.1%}")
    print(f"  OOS Profit Factor: {best_oos_m['pf']:.2f}")
    print(f"\nComparison:")
    print(f"  s29 OOS return:   +5.6%")
    print(f"  s37 OOS return:   +3.87%")
    print(f"  BTC BnH OOS:      {bnh_oos_metrics['total_return']:.2%} (Sharpe={bnh_oos_metrics['sharpe']:.2f})")
    print(f"\nRecommendation: {recommendation}")
    print(f"Reasoning: {reasoning}")


if __name__ == '__main__':
    main()
